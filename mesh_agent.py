#!/usr/bin/env python3
"""
mesh_agent.py - the meshing sub-agent.

Reads a STEP file, decides element family / order / size, generates the mesh
with Gmsh, measures mesh quality, and runs the deterministic locking check
(locking_check.py) on the combination it produced.

Design position, stated up front because it differs from cad_agent.py:

    The CAD agent needed an LLM because there is no closed-form map from
    "bracket with two ears and a pin hole" to build123d code.
    The mesh agent mostly does NOT need an LLM. Meshing a STEP file with a
    chosen family, order and size is a deterministic API call. Putting an LLM
    in that path adds failure modes and buys nothing.

    So the LLM's role here is narrow and sits at the EDGES:
      - upstream: turn free text into a structured MeshRequest (optional)
      - downstream: explain findings to the user
    The physics decision and the mesh generation in between are deterministic.

Install:
    pip install gmsh
    Linux containers also need OS libraries the wheel links against:
        apt-get install -y libglu1-mesa libxcursor1 libxinerama1 libxft2
    No separate Gmsh application install is required. The wheel ships the
    Gmsh SDK binary itself.

Run the self-test:
    python3 mesh_agent.py
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
import os
import math

import gmsh

from solvers import (
    SolverProfile, get_solver, cure_availability, retype_inp,
    deployment_solvers,
)
from locking_check import (
    ElementSpec, MaterialSpec, LoadCase, LockingReport, Severity, Owner,
    check_locking, suggest_element, to_calculix, BENDING_LIKE,
    NU_NEAR_INCOMPRESSIBLE, MIN_ELEMENTS_THROUGH_THICKNESS_BENDING,
)


# ----------------------------------------------------------------------------
# What the target stack can actually deliver.
# ----------------------------------------------------------------------------
# This table is the reason the mesh agent cannot be designed in isolation.
# Gmsh chooses FAMILY and ORDER. The solver deck chooses INTEGRATION. If the
# solver has no reduced-integration or hybrid variant for the family Gmsh can
# produce, then the locking cure the check recommends does not exist in this
# stack, and the honest answer is to say so rather than to mesh anyway.

# ----------------------------------------------------------------------------
# Request and result
# ----------------------------------------------------------------------------

MAX_ELEMENTS = 400_000       # guard against memory blowup
MIN_ACCEPTABLE_SICN = 0.05   # below this an element is degenerate, not just poor
HEX_SIZE_FACTOR = 2.0        # all-hex subdivision refines, so coarsen first


class MeshTooLargeError(RuntimeError):
    pass


@dataclass
class MeshRequest:
    step_path: str
    material: MaterialSpec
    load_case: LoadCase
    prefer_family: Optional[str] = None       # "tet" | "hex" | None = auto
    prefer_order: Optional[int] = None        # 1 | 2 | None = auto
    target_size: Optional[float] = None       # absolute element size, mm
    elements_across_min_dim: int = 4          # sizing driver when target_size is None
    out_prefix: str = "mesh"
    solver: str = "calculix"          # the DEPLOYMENT solver
    oracle_solver: Optional[str] = None   # optional reference-truth deck


@dataclass
class MeshQuality:
    n_elements: int
    n_nodes: int
    min_sicn: float            # signed inverse condition number, >0 required
    mean_sicn: float
    min_gamma: float           # inscribed/circumscribed radius ratio
    n_inverted: int            # elements with non-positive quality
    bbox: Tuple[float, float, float]
    char_size: float
    est_elements_through_min_dim: int
    est_wall_thickness: float          # 2V/A heuristic, see _measure
    elements_through_wall: float

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class MeshResult:
    msh_path: str
    deck_path: str
    element: ElementSpec
    quality: MeshQuality
    locking: LockingReport
    integration_resolved: bool
    solver: str = "calculix"
    oracle_deck_path: Optional[str] = None
    oracle_element: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def ok_to_solve(self) -> bool:
        return (self.locking.safe_to_solve
                and self.quality.n_inverted == 0
                and self.quality.min_sicn >= MIN_ACCEPTABLE_SICN)

    def render(self) -> str:
        el = self.element
        prof = get_solver(self.solver)
        ccx = (prof.element_name(el.family, el.order, el.integration)
               or f"no direct {self.solver} equivalent")
        q = self.quality
        lines = [
            "MESH AGENT RESULT",
            "=================",
            f"  solver       : {self.solver}",
            f"  files        : {self.msh_path} , {self.deck_path}",
            f"  element      : {el.label()}   -> {ccx}",
            f"  integration  : {'chosen by mesh agent' if self.integration_resolved else 'NOT RESOLVED HERE, solver agent must set it'}",
            f"  elements     : {q.n_elements}   nodes: {q.n_nodes}",
            f"  bbox (mm)    : {q.bbox[0]:.2f} x {q.bbox[1]:.2f} x {q.bbox[2]:.2f}",
            f"  char size    : {q.char_size:.3f} mm",
            f"  quality      : min SICN {q.min_sicn:.4f} , mean {q.mean_sicn:.4f} , min gamma {q.min_gamma:.4f}",
            f"  inverted     : {q.n_inverted}",
            f"  bbox proxy   : ~{q.est_elements_through_min_dim} elements through min bbox dim",
            f"  wall proxy   : est thickness {q.est_wall_thickness:.2f} mm "
            f"-> {q.elements_through_wall:.1f} elements through the wall",
            "",
        ]
        if self.oracle_deck_path:
            lines.append(f"  ORACLE deck  : {self.oracle_deck_path} "
                         f"(element {self.oracle_element})")
            lines.append("  ORACLE use   : reference truth for benchmarking "
                         "only, not a deployment path. Confirm license "
                         "entitlement before running.")
            lines.append("")
        for n in self.notes:
            lines.append(f"  note: {n}")
        lines.append("")
        lines.append(self.locking.render())
        lines.append("")
        lines.append(f"OK TO SOLVE: {self.ok_to_solve}")
        return "\n".join(lines)


# ----------------------------------------------------------------------------
# Planning: deterministic, physics-driven, constrained by the stack
# ----------------------------------------------------------------------------

def plan_element(req: MeshRequest) -> Tuple[ElementSpec, List[str]]:
    """Choose family, order and (proposed) integration from material + load.

    No LLM. This is the rule layer from locking_check applied forward instead
    of as a post-hoc check.
    """
    notes: List[str] = []
    family = req.prefer_family

    if family is None:
        # Honest default. General automatic all-hex meshing of arbitrary CAD is
        # not a solved problem. Gmsh can recombine tets into hexes, but on
        # non-blocky geometry the resulting hex quality is usually poor. For an
        # imported STEP of unknown shape, tets are the reliable choice.
        family = "tet"
        notes.append("family defaulted to tet: automatic hex meshing of "
                     "arbitrary imported CAD is not reliable. Set "
                     "prefer_family='hex' only for blocky, mappable geometry.")

    ideal = suggest_element(req.material, req.load_case, prefer_family=family)
    order = req.prefer_order if req.prefer_order is not None else ideal.order

    # tets must be quadratic in practice: linear tets are constant-strain
    if family == "tet" and order == 1:
        notes.append("linear tets requested. locking_check rule R4 will flag "
                     "this. Consider order 2.")

    profile = get_solver(req.solver)
    integration = ideal.integration
    available = profile.available_integration(family, order) or {"full"}
    if integration not in available:
        who = cure_availability(family, order, integration)
        can = [n for n, ok in who.items() if ok]
        can_deploy = [n for n in can if n in deployment_solvers()]
        notes.append(
            f"the physics-preferred integration '{integration}' is NOT "
            f"available for {family}/order{order} in '{profile.name}'. "
            f"Available there: {sorted(available)}. Falling back to 'full'.")
        notes.append(
            f"solvers that DO offer '{integration}' for {family}/order{order}: "
            f"{can if can else 'none in registry'}. "
            f"Of those, shippable: {can_deploy if can_deploy else 'NONE'}.")
        integration = "full" if "full" in available else sorted(available)[0]

    return ElementSpec(family=family, order=order, integration=integration), notes


# ----------------------------------------------------------------------------
# Meshing
# ----------------------------------------------------------------------------

def _bbox_and_size(req: MeshRequest) -> Tuple[Tuple[float, float, float], float]:
    xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
    bbox = (xmax - xmin, ymax - ymin, zmax - zmin)
    if req.target_size is not None:
        size = req.target_size
    else:
        size = min(bbox) / max(1, req.elements_across_min_dim)
    return bbox, size


def mesh_step(req: MeshRequest, element: ElementSpec,
              session=None) -> Tuple[MeshQuality, str, str]:
    """Generate the mesh. Deterministic Gmsh API calls, no generated code.

    Two modes:
      session is None  legacy. Opens and closes its own Gmsh session. The face
                      tags in that session are private to it, so no face
                      selection can reach the deck. Kept for geometry-only
                      meshing where there are no BCs yet.
      session given    the GeomSession already holds the imported STEP, the
                      catalogue and the SOLID physical group. This function
                      only clears any previous mesh and remeshes IN PLACE, so
                      CAD face tags stay valid across retries.
    """
    owns_session = session is None
    if owns_session and not os.path.exists(req.step_path):
        raise FileNotFoundError(req.step_path)

    if owns_session:
        gmsh.initialize()
    try:
        if owns_session:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("part")
            gmsh.model.occ.importShapes(req.step_path)
            gmsh.model.occ.synchronize()

            vols = [t for d, t in gmsh.model.getEntities(3)]
            if not vols:
                raise RuntimeError("STEP file contains no 3D solid")
            # A 3D physical group makes Gmsh write ONLY the solid elements to
            # the deck. A 2D group would add CPS6 surface elements, which is
            # why face selections become node sets, not physical groups.
            gmsh.model.addPhysicalGroup(3, vols, name="SOLID")
        else:
            # MEASURED: physical groups survive mesh.clear(), and setOrder(2)
            # after a clear does not compound. So a retry is a clear plus a
            # regenerate, not a re-import.
            gmsh.model.mesh.clear()

        bbox, size = _bbox_and_size(req)
        gmsh.option.setNumber("Mesh.MeshSizeMax", size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", size / 4.0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)

        if element.family == "hex":
            gmsh.option.setNumber("Mesh.RecombineAll", 1)
            gmsh.option.setNumber("Mesh.SubdivisionAlgorithm", 2)
            # The all-hex subdivision splits every tet into several hexes, so
            # the effective resolution is finer than the requested size. Coarsen
            # the request to compensate, otherwise element count explodes.
            gmsh.option.setNumber("Mesh.MeshSizeMax", size * HEX_SIZE_FACTOR)
            gmsh.option.setNumber("Mesh.MeshSizeMin", size * HEX_SIZE_FACTOR / 4.0)

        gmsh.model.mesh.generate(3)

        n_pre = sum(len(gmsh.model.mesh.getElementsByType(t)[0])
                    for t in gmsh.model.mesh.getElementTypes(3))
        if n_pre > MAX_ELEMENTS:
            raise MeshTooLargeError(
                f"{n_pre} elements exceeds the guard of {MAX_ELEMENTS}. "
                f"Coarsen elements_across_min_dim or set target_size.")

        if element.order == 2:
            gmsh.model.mesh.setOrder(2)

        gmsh.option.setNumber("Mesh.SaveGroupsOfElements", 1)
        profile = get_solver(req.solver)
        msh_path = f"{req.out_prefix}.msh"
        deck_path = f"{req.out_prefix}.{profile.mesh_format}"
        gmsh.write(msh_path)
        if deck_path != msh_path:
            gmsh.write(deck_path)

        vol = sum(gmsh.model.occ.getMass(3, t) for d, t in
                  gmsh.model.getEntities(3))
        area = sum(gmsh.model.occ.getMass(2, t) for d, t in
                   gmsh.model.getEntities(2))
        quality = _measure(bbox, size, vol, area)
        return quality, msh_path, deck_path
    finally:
        if owns_session:
            gmsh.finalize()


def _measure(bbox, char_size, volume=0.0, area=0.0) -> MeshQuality:
    types = gmsh.model.mesh.getElementTypes(3)
    all_sicn: List[float] = []
    all_gamma: List[float] = []
    n_el = 0
    for t in types:
        tags, _ = gmsh.model.mesh.getElementsByType(t)
        n_el += len(tags)
        if len(tags):
            all_sicn.extend(gmsh.model.mesh.getElementQualities(tags, "minSICN"))
            all_gamma.extend(gmsh.model.mesh.getElementQualities(tags, "gamma"))
    node_tags, _, _ = gmsh.model.mesh.getNodes()

    min_sicn = min(all_sicn) if all_sicn else 0.0
    mean_sicn = sum(all_sicn) / len(all_sicn) if all_sicn else 0.0
    min_gamma = min(all_gamma) if all_gamma else 0.0
    n_inverted = sum(1 for v in all_sicn if v <= 0.0)

    # PROXY, not a thickness measurement. It divides the smallest bounding-box
    # dimension by the characteristic element size. For a plate-like part whose
    # thin direction aligns with the bounding box this is close to right. For a
    # part with an internal thin wall inside a bulky envelope it is WRONG and
    # optimistic. A real check needs a wall-thickness or medial-axis estimate.
    est = max(1, int(math.floor(min(bbox) / char_size)))

    # SECOND, BETTER PROXY. The bounding-box proxy above is blind to a thin
    # feature inside a bulky envelope: a 2.5 mm base plate in a 30 mm tall
    # bracket reports the 30 mm, not the 2.5 mm, and passes when it should
    # fail. 2*V/A approximates the wall thickness of a plate-like body
    # exactly, and UNDERestimates it for a bulky body, which is the safe
    # direction for a check. Still a proxy, not a measurement: a real one
    # needs a medial-axis or ray-cast thickness field.
    est_t = (2.0 * volume / area) if area > 0 else min(bbox)
    thru_wall = est_t / char_size if char_size > 0 else 0.0

    return MeshQuality(
        n_elements=n_el, n_nodes=len(node_tags),
        min_sicn=min_sicn, mean_sicn=mean_sicn, min_gamma=min_gamma,
        n_inverted=n_inverted, bbox=bbox, char_size=char_size,
        est_elements_through_min_dim=est,
        est_wall_thickness=est_t, elements_through_wall=thru_wall)


# ----------------------------------------------------------------------------
# The agent entry point
# ----------------------------------------------------------------------------

def run_mesh_agent(req: MeshRequest, max_retries: int = 2,
                   session=None) -> MeshResult:
    """Plan, mesh, measure, check. Retries on quality failure by refining.

    The retry loop here fixes MESH QUALITY problems (inverted or badly shaped
    elements) and bending RESOLUTION problems, because those are the mesh
    agent's own responsibility. It does NOT try to fix locking findings that
    require an integration-scheme change: those are routed to the solver agent
    and returned unresolved, on purpose.
    """
    element, notes = plan_element(req)
    attempt = 0
    quality = msh = deck = None

    while True:
        attempt += 1
        quality, msh, deck = mesh_step(req, element, session=session)
        element.elements_through_thickness = max(
            1, int(math.floor(quality.elements_through_wall)))

        problems = []
        quality_failure = False
        if quality.n_inverted > 0:
            quality_failure = True
            problems.append(f"{quality.n_inverted} inverted elements")
        if quality.min_sicn < MIN_ACCEPTABLE_SICN:
            problems.append(
                f"min SICN {quality.min_sicn:.4f} below {MIN_ACCEPTABLE_SICN}")
            quality_failure = True
        if (req.load_case.dominant_mode in BENDING_LIKE
                and quality.elements_through_wall
                < MIN_ELEMENTS_THROUGH_THICKNESS_BENDING):
            problems.append("too few elements through the thin direction "
                            "for a bending load")

        if not problems or attempt > max_retries:
            if problems:
                notes.append("unresolved after retries: " + "; ".join(problems))
            break

        # Choose the CORRECT lever, which is the whole point of this project.
        # Element shape failure from hex recombination is TOPOLOGICAL. Refining
        # a bad recombined hex mesh produces more bad hexes and burns memory.
        # The right response is to abandon hexes and fall back to quadratic
        # tets, accepting the integration-scheme consequence.
        if quality_failure and element.family == "hex":
            notes.append(
                f"retry {attempt}: {'; '.join(problems)}. Hex recombination "
                f"quality is topological, not a sizing problem. Falling back "
                f"to quadratic tets. Consequence: no reduced or hybrid "
                f"integration is available for tets in this stack.")
            element = ElementSpec("tet", 2, "full")
        elif quality.elements_through_wall < MIN_ELEMENTS_THROUGH_THICKNESS_BENDING:
            # Correct lever: size from the WALL, not from the bounding box.
            # Halving a bounding-box-derived size converges on a thin feature
            # very slowly and wastes elements everywhere else.
            new_size = quality.est_wall_thickness / \
                MIN_ELEMENTS_THROUGH_THICKNESS_BENDING
            notes.append(
                f"retry {attempt}: {'; '.join(problems)} -> sizing from the "
                f"estimated wall thickness ({quality.est_wall_thickness:.2f} "
                f"mm), target element size {new_size:.3f} mm")
            req.target_size = new_size
        else:
            notes.append(
                f"retry {attempt}: {'; '.join(problems)} -> refining size")
            req.elements_across_min_dim = max(
                MIN_ELEMENTS_THROUGH_THICKNESS_BENDING,
                req.elements_across_min_dim * 2)
            req.target_size = None

    # ---- CAD faces to node sets, AFTER the final mesh -----------------
    # Node tags are mesh entities. Every retry called mesh.clear(), which
    # invalidated them. This is the only correct point to extract them, and it
    # must happen before the oracle deck is copied from the deployment deck.
    if session is not None and getattr(session, "selections", None):
        sets = session.extract_node_sets()
        if session.write_node_sets(deck):
            notes.append(
                f"wrote {len(sets)} node set(s) into {deck}: "
                + ", ".join(f"{k}({len(v)} nodes)" for k, v in sets.items())
                + ". Face tags survived "
                + f"{attempt} mesh generation(s) because the CAD model was "
                  "never re-imported.")
        else:
            notes.append(
                f"selections exist but deck format .{deck.rsplit('.', 1)[-1]} "
                f"has no NSET writer. Node sets extracted in memory only.")
    elif session is None:
        notes.append(
            "no GeomSession passed. The deck has a solid and no named faces, "
            "so no load or constraint can be attached to it yet.")

    report = check_locking(element, req.material, req.load_case)

    # Did the mesh agent settle the integration scheme? It did not, and should
    # not pretend otherwise. Gmsh has no concept of integration scheme.
    integration_resolved = False
    for f in report.findings:
        if f.owner in (Owner.SOLVER, Owner.EITHER) and f.severity in (
                Severity.SEVERE, Severity.MODERATE):
            notes.append(f"HANDOFF: {f.rule_id} needs the solver agent "
                         f"({f.owner.value}), mesh agent cannot fix it.")

    # ---- optional reference-oracle deck -------------------------------
    oracle_deck = oracle_el = None
    if req.oracle_solver:
        oracle = get_solver(req.oracle_solver)
        if oracle.deployment_target:
            notes.append(
                f"'{req.oracle_solver}' is a deployment-capable solver. Using "
                f"it as an oracle is fine, but prefer a genuinely independent "
                f"formulation for the reference answer.")
        # pick the strongest cure the oracle offers for THIS element
        preferred = ["hybrid", "reduced", "incompatible", "full"]
        avail = oracle.available_integration(element.family, element.order)
        choice = next((c for c in preferred if c in avail), None)
        if choice is None:
            notes.append(f"oracle '{oracle.name}' has no element for "
                         f"{element.family}/order{element.order}")
        else:
            oracle_el = oracle.element_name(element.family, element.order, choice)
            if oracle.mesh_format == "inp" and msh and oracle_el:
                src = deck if deck.endswith(".inp") else None
                if src:
                    oracle_deck = f"{req.out_prefix}_oracle_{oracle.name}.inp"
                    retype_inp(src, oracle_deck, oracle_el)
                    notes.append(
                        f"oracle deck written with element {oracle_el} "
                        f"({choice} integration). Same nodes and connectivity "
                        f"as the deployment deck, so the ONLY difference "
                        f"between the two runs is the element formulation. "
                        f"That is what makes the comparison a clean measure "
                        f"of locking.")
                    notes.append(oracle.license_note)
            else:
                notes.append(
                    f"oracle '{oracle.name}' uses .{oracle.mesh_format}; "
                    f"convert {msh} with meshio, no automatic deck written.")

    return MeshResult(msh_path=msh, deck_path=deck, element=element,
                      quality=quality, locking=report,
                      integration_resolved=integration_resolved,
                      solver=req.solver, oracle_deck_path=oracle_deck,
                      oracle_element=oracle_el, notes=notes)


# ----------------------------------------------------------------------------
# Optional LLM edge: free text -> MeshRequest. Not required for the agent.
# ----------------------------------------------------------------------------

INTERPRET_SYSTEM = (
    "You convert an engineering request into JSON for a meshing agent.\n"
    "Return ONLY a JSON object, no prose, no markdown fences, with keys:\n"
    '  "dominant_mode": one of "bending","axial","shear","torsion","mixed","unknown"\n'
    '  "confident": true or false\n'
    '  "nu": number or null\n'
    '  "E": number or null\n'
    '  "plastic_response_expected": true or false\n'
    '  "prefer_family": "tet" or "hex" or null\n'
    '  "elements_across_min_dim": integer or null\n'
    "Rules: if the load description does not let you determine the dominant\n"
    "deformation mode with confidence, you MUST return \"unknown\". Do not\n"
    "guess. A wrong mode disables the shear-locking check silently."
)


def interpret_request(text: str, model: Optional[str] = None) -> Dict[str, Any]:
    """Optional. Requires ANTHROPIC_API_KEY. Kept out of the mesh path so the
    agent runs fully offline and deterministically without it."""
    import json
    from anthropic import Anthropic
    model = model or os.environ.get("MESH_AGENT_MODEL", "claude-sonnet-5")
    client = Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=600, system=INTERPRET_SYSTEM,
        messages=[{"role": "user", "content": text}])
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------

def _make_test_beam(path="beam_test.step", L=100.0, b=10.0, h=10.0):
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("beam")
        gmsh.model.occ.addBox(0, 0, 0, L, b, h)
        gmsh.model.occ.synchronize()
        gmsh.write(path)
    finally:
        gmsh.finalize()
    return path


def _make_test_bracket(path="bracket_test.step",
                       L=63.4, W=50.7, H=30.5,
                       t_base=2.54, t_ear=5.08, d_pin=8.0):
    """Stand-in for the bracket from cad_agent.py, so the self-test runs on a
    shape with real features (thin base, thin ears, a hole) rather than a box.

    Replace with your own STEP by passing step_path to MeshRequest. Nothing in
    the mesh agent depends on this geometry.
    """
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("bracket")
        occ = gmsh.model.occ
        base = occ.addBox(0, 0, 0, L, W, t_base)
        gap = W - 2 * t_ear
        ear1 = occ.addBox(0, 0, t_base, L, t_ear, H - t_base)
        ear2 = occ.addBox(0, W - t_ear, t_base, L, t_ear, H - t_base)
        fused, _ = occ.fuse([(3, base)], [(3, ear1), (3, ear2)])
        pin = occ.addCylinder(L / 2, -1.0, H * 0.7, 0, W + 2.0, 0, d_pin / 2)
        occ.cut(fused, [(3, pin)])
        occ.synchronize()
        gmsh.write(path)
    finally:
        gmsh.finalize()
    return path


if __name__ == "__main__":
    bracket = _make_test_bracket()
    steel = MaterialSpec(E=210e3, nu=0.3, name="generic steel")
    steel_plastic = MaterialSpec(E=210e3, nu=0.3, yield_stress=350.0,
                                 plastic_response_expected=True,
                                 name="steel, yielding")

    cases = [
        ("A. bracket, steel, BENDING, CalculiX, no oracle",
         MeshRequest(bracket, steel, LoadCase("bending"),
                     solver="calculix", out_prefix="brk_A")),

        ("B. bracket, steel, BENDING, CalculiX + ABAQUS ORACLE",
         MeshRequest(bracket, steel, LoadCase("bending"),
                     solver="calculix", oracle_solver="abaqus",
                     out_prefix="brk_B")),

        ("C. bracket, steel that YIELDS, CalculiX (no cure available)",
         MeshRequest(bracket, steel_plastic, LoadCase("bending"),
                     solver="calculix", oracle_solver="abaqus",
                     out_prefix="brk_C")),

        ("D. same case, FEniCSx as deployment solver",
         MeshRequest(bracket, steel_plastic, LoadCase("bending"),
                     solver="fenicsx", out_prefix="brk_D")),
    ]

    for title, req in cases:
        print("\n" + "#" * 78)
        print("# " + title)
        print("#" * 78)
        try:
            print(run_mesh_agent(req).render())
        except Exception as e:
            print("FAILED:", type(e).__name__, e)
