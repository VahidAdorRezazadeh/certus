#!/usr/bin/env python3
"""
case_agent.py - turn a mesh deck into a SOLVABLE deck.

The mesh agent writes nodes, elements and named node sets. That deck cannot
run: it has no material, no section, no step, no boundary conditions and no
loads. This file adds them.

What it COMPUTES rather than asks for:

    dominant_mode        from the load resultant against the constraint
                         centroid and the part slenderness. Previously this
                         was a command line flag that the user asserted. Every
                         locking rule depends on it, so a wrong assertion
                         turned a SEVERE finding into a silent PASS.
    overconstraint       fraction of the model held fixed, and whether all
                         degrees of freedom were removed on a large face.
    nodal load split     area weighted from the surface facets, not equal per
                         node. For a 6-node triangle the consistent loads are
                         zero at corners and area/3 at mid-sides.

What it takes on trust from the caller: the load vector, which faces are
loaded, which faces are held, and the material. Those are intent, not physics.

SOLVER TARGETS
    abaqus     current target. Written first because it is what is installed.
    calculix   switch present, syntax written, NOT yet validated end to end.
The two differ only in the output request block and in a few tolerances, so
this is one code path with a profile lookup, not two writers.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
import math

import gmsh

import geometry_features as GF
from locking_check import MaterialSpec, LoadCase
from solvers import get_solver

Vec = Tuple[float, float, float]


# ---------------------------------------------------------------------------
# Case description: what the user meant
# ---------------------------------------------------------------------------

@dataclass
class LoadSpec:
    selection: str                 # NamedSelection name, role must be "load"
    kind: str = "force"            # "force" (total N) or "pressure" (MPa)
    vector: Vec = (0.0, 0.0, -1.0)  # direction and magnitude for "force"
    pressure: float = 0.0          # positive means INTO the surface

    def resultant(self) -> Vec:
        return self.vector if self.kind == "force" else (0.0, 0.0, 0.0)


@dataclass
class ConstraintSpec:
    selection: str                 # role must be "constraint"
    dofs: Tuple[int, ...] = (1, 2, 3)   # translational DOF to fix
    encastre: bool = False         # fix everything, Abaqus keyword

    def label(self) -> str:
        return "ENCASTRE" if self.encastre else "DOF " + \
            ",".join(str(d) for d in self.dofs)


@dataclass
class CaseSpec:
    material: MaterialSpec
    loads: List[LoadSpec] = field(default_factory=list)
    constraints: List[ConstraintSpec] = field(default_factory=list)
    solver: str = "abaqus"
    nlgeom: bool = False           # keep FALSE for analytical validation
    step_name: str = "Step-1"
    material_name: str = "MAT1"
    solid_elset: str = "SOLID"


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

@dataclass
class ModeEvidence:
    mode: str
    lever_arm: float
    section_depth: float
    slenderness: float
    axial_component: float
    transverse_component: float
    load_centroid: Vec
    constraint_centroid: Vec
    reasoning: str

    def render(self) -> str:
        return (
            f"DOMINANT MODE: {self.mode}   [computed, not asserted]\n"
            f"  load centroid       ({self.load_centroid[0]:.2f}, "
            f"{self.load_centroid[1]:.2f}, {self.load_centroid[2]:.2f})\n"
            f"  constraint centroid ({self.constraint_centroid[0]:.2f}, "
            f"{self.constraint_centroid[1]:.2f}, "
            f"{self.constraint_centroid[2]:.2f})\n"
            f"  lever arm           {self.lever_arm:.3f} mm\n"
            f"  section depth       {self.section_depth:.3f} mm\n"
            f"  slenderness L/d     {self.slenderness:.2f}\n"
            f"  force along lever   {self.axial_component:.3f} N\n"
            f"  force across lever  {self.transverse_component:.3f} N\n"
            f"  reasoning           {self.reasoning}")


@dataclass
class CaseReport:
    deck_path: str
    mode: Optional[ModeEvidence]
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def render(self) -> str:
        out = ["CASE AGENT", "=" * 60, f"  deck: {self.deck_path}"]
        if self.mode:
            out.append("")
            out.append(self.mode.render())
        if self.warnings:
            out.append("")
            out.append("WARNINGS")
            for w in self.warnings:
                out.append(f"  ! {w}")
        if self.notes:
            out.append("")
            for n in self.notes:
                out.append(f"  note: {n}")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# Geometry helpers, read from the OPEN Gmsh session
# ---------------------------------------------------------------------------

def _node_coords(tags: Sequence[int]) -> List[Vec]:
    return [tuple(gmsh.model.mesh.getNode(int(t))[0]) for t in tags]


def _centroid(pts: Sequence[Vec]) -> Vec:
    n = len(pts)
    return tuple(sum(p[i] for p in pts) / n for i in range(3))


def _model_bbox() -> Tuple[Vec, Vec]:
    b = gmsh.model.getBoundingBox(-1, -1)
    return (b[0], b[1], b[2]), (b[3], b[4], b[5])


# ---------------------------------------------------------------------------
# COMPUTE: dominant deformation mode
# ---------------------------------------------------------------------------

AXIAL_DOMINANCE = 3.0      # |F_along| > this * |F_across| means axial
BENDING_SLENDERNESS = 2.0  # lever/depth above this means bending, not shear


def compute_dominant_mode(load_pts: Sequence[Vec],
                          force: Vec,
                          constraint_pts: Sequence[Vec]) -> ModeEvidence:
    """Derive the dominant deformation mode from the load path.

    Method, deliberately simple and auditable:
      1. lever arm r = load centroid minus constraint centroid
      2. split the force into a component along r and a component across r
      3. if the along component dominates, the part is loaded axially
      4. otherwise compare the lever arm to the section depth measured
         perpendicular to r. For a rectangular section the ratio of bending
         stress to shear stress is 4L/d, so L/d above about 2 means bending
         governs and below that shear governs.

    Limits, stated rather than hidden:
      - a single force resultant can never produce a torsion moment about its
        own lever arm, since r cross F is perpendicular to r. Torsion needs an
        applied couple or an offset pair, which this function does not see.
        It therefore never returns "torsion" and says so.
      - a zero resultant, for example a self-equilibrated pressure, returns
        "unknown" instead of guessing.
    """
    lc = _centroid(load_pts)
    cc = _centroid(constraint_pts)
    r = tuple(lc[i] - cc[i] for i in range(3))
    rlen = math.sqrt(sum(v * v for v in r))
    fmag = math.sqrt(sum(v * v for v in force))

    lo, hi = _model_bbox()
    diag = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))

    if fmag < 1e-12:
        return ModeEvidence("unknown", rlen, 0.0, 0.0, 0.0, 0.0, lc, cc,
                            "zero force resultant, mode cannot be derived")
    if rlen < 1e-9 * max(diag, 1.0):
        return ModeEvidence("unknown", rlen, 0.0, 0.0, 0.0, 0.0, lc, cc,
                            "load and constraint share a centroid, so there "
                            "is no lever arm to reason about")

    e = tuple(v / rlen for v in r)
    f_along = sum(force[i] * e[i] for i in range(3))
    across_v = tuple(force[i] - f_along * e[i] for i in range(3))
    f_across = math.sqrt(sum(v * v for v in across_v))

    # section depth: extent of the part measured along the force direction,
    # which is the depth that resists bending from this load
    if f_across > 1e-12:
        d_dir = tuple(v / f_across for v in across_v)
    else:
        d_dir = e
    depth = abs(sum((hi[i] - lo[i]) * d_dir[i] for i in range(3)))
    if depth < 1e-9:
        depth = min(hi[i] - lo[i] for i in range(3))
    slender = rlen / depth if depth > 0 else 0.0

    if abs(f_along) > AXIAL_DOMINANCE * f_across:
        return ModeEvidence("axial", rlen, depth, slender, f_along, f_across,
                            lc, cc,
                            f"force is essentially along the lever arm "
                            f"({abs(f_along):.1f} N along vs {f_across:.1f} N "
                            f"across)")
    if slender >= BENDING_SLENDERNESS:
        return ModeEvidence("bending", rlen, depth, slender, f_along, f_across,
                            lc, cc,
                            f"transverse force on a lever arm {slender:.1f} "
                            f"times the section depth, so bending stress "
                            f"dominates shear")
    return ModeEvidence("shear", rlen, depth, slender, f_along, f_across,
                        lc, cc,
                        f"transverse force with a lever arm only "
                        f"{slender:.1f} times the section depth, so shear "
                        f"is not negligible against bending")


# ---------------------------------------------------------------------------
# COMPUTE: overconstraint
# ---------------------------------------------------------------------------

OVERCONSTRAINT_WARN = 0.05    # fraction of all nodes
OVERCONSTRAINT_SEVERE = 0.10


def check_overconstraint(node_sets: Dict[str, List[int]],
                         constraints: Sequence[ConstraintSpec],
                         total_nodes: int) -> List[str]:
    out = []
    for c in constraints:
        tags = node_sets.get(c.selection)
        if not tags:
            continue
        frac = len(tags) / max(total_nodes, 1)
        full = c.encastre or set(c.dofs) >= {1, 2, 3}
        if frac >= OVERCONSTRAINT_SEVERE and full:
            out.append(
                f"'{c.selection}' fixes ALL translations on {frac*100:.1f}% "
                f"of the model ({len(tags)} nodes). This will over-stiffen "
                f"the part and put a false stress concentration at the "
                f"constraint edge. Any comparison to a hand calculation will "
                f"disagree for this reason and not because of the mesh.")
        elif frac >= OVERCONSTRAINT_WARN and full:
            out.append(
                f"'{c.selection}' fixes all translations on {frac*100:.1f}% "
                f"of the model. Check this is the intended support.")
    if not constraints:
        out.append("NO CONSTRAINTS. The model has rigid body motion and will "
                   "not solve.")
    return out


# ---------------------------------------------------------------------------
# WRITE: append the case to the deck
# ---------------------------------------------------------------------------

# Solver syntax differences, recorded explicitly rather than patched over.
# CalculiX 2.21 REJECTS the ENCASTRE keyword with
#   *ERROR reading *BOUNDARY. Card image: FIX_FACE,ENCASTRE
# Abaqus accepts it. For a solid element mesh the two are physically
# identical, because solid nodes have no rotational degrees of freedom, so
# expanding to DOF 1 through 3 is portable and loses nothing. MEASURED, not
# recalled: this is the third syntax disagreement found between the two
# readers today, after trailing commas in *NSET and the *Part nesting.
_SUPPORTS_ENCASTRE = {"abaqus": True, "calculix": False}

_OUTPUT_BLOCK = {
    "abaqus": ("*OUTPUT, FIELD, VARIABLE=PRESELECT\n"
               "*OUTPUT, HISTORY, VARIABLE=PRESELECT\n"),
    "calculix": ("*NODE FILE\nU\n*EL FILE\nS, E\n"),
}


def write_case(deck_path: str,
               spec: CaseSpec,
               node_sets: Dict[str, List[int]],
               selections: Sequence[GF.NamedSelection],
               total_nodes: int) -> CaseReport:
    """Append material, section, step, BCs and loads to a mesh deck.

    Must run inside the GeomSession that produced the mesh, because the
    consistent load weights and the mode computation both read node
    coordinates from the live model.
    """
    prof = get_solver(spec.solver)
    if spec.solver not in _OUTPUT_BLOCK:
        raise KeyError(f"no output block defined for solver '{spec.solver}'")

    report = CaseReport(deck_path=deck_path, mode=None)

    if not prof.deployment_target:
        report.notes.append(
            f"{prof.name} is registered as deployment_target=False in "
            f"solvers.py. This run uses it deliberately. {prof.license_note}")

    by_name = {s.name: s for s in selections}

    # -- roles must agree with the case ---------------------------------
    for l in spec.loads:
        s = by_name.get(l.selection)
        if s is None:
            raise KeyError(f"load references unknown selection "
                           f"'{l.selection}'")
        if s.role != "load":
            report.warnings.append(
                f"selection '{l.selection}' is registered with role "
                f"'{s.role}' but is being loaded")
    for c in spec.constraints:
        s = by_name.get(c.selection)
        if s is None:
            raise KeyError(f"constraint references unknown selection "
                           f"'{c.selection}'")
        if s.role != "constraint":
            report.warnings.append(
                f"selection '{c.selection}' is registered with role "
                f"'{s.role}' but is being constrained")

    # -- COMPUTE dominant mode ------------------------------------------
    force_loads = [l for l in spec.loads if l.kind == "force"]
    if force_loads and spec.constraints:
        total_f = tuple(sum(l.vector[i] for l in force_loads)
                        for i in range(3))
        lpts, cpts = [], []
        for l in force_loads:
            lpts += _node_coords(node_sets[l.selection])
        for c in spec.constraints:
            cpts += _node_coords(node_sets[c.selection])
        report.mode = compute_dominant_mode(lpts, total_f, cpts)
    else:
        report.notes.append(
            "dominant mode not computed: needs at least one force load and "
            "one constraint. Pressure-only cases have no single resultant to "
            "reason from.")

    # -- COMPUTE overconstraint -----------------------------------------
    report.warnings += check_overconstraint(node_sets, spec.constraints,
                                            total_nodes)

    # -- consistent nodal weights for force loads -----------------------
    weights: Dict[str, Dict[int, float]] = {}
    if force_loads:
        sels = [by_name[l.selection] for l in force_loads]
        weights = GF.facet_node_weights(sels)

    # -- write ----------------------------------------------------------
    m = spec.material
    with open(deck_path, "a") as f:
        f.write("**\n** ---- case written by case_agent.py ----\n**\n")
        f.write(f"*MATERIAL, NAME={spec.material_name}\n")
        f.write("*ELASTIC\n")
        f.write(f"{m.E:.6g}, {m.nu:.6g}\n")
        if m.yield_stress and m.plastic_response_expected:
            f.write("*PLASTIC\n")
            f.write(f"{m.yield_stress:.6g}, 0.0\n")
            report.notes.append(
                "perfectly plastic *PLASTIC table written from yield_stress "
                "alone. This is a placeholder, not a real hardening curve.")
        f.write(f"*SOLID SECTION, ELSET={spec.solid_elset}, "
                f"MATERIAL={spec.material_name}\n,\n")

        nl = "YES" if spec.nlgeom else "NO"
        if spec.solver == "abaqus":
            f.write(f"*STEP, NAME={spec.step_name}, NLGEOM={nl}\n")
        else:
            f.write(f"*STEP{', NLGEOM' if spec.nlgeom else ''}\n")
        f.write("*STATIC\n")

        f.write("**\n** boundary conditions\n**\n*BOUNDARY\n")
        for c in spec.constraints:
            if c.encastre and _SUPPORTS_ENCASTRE.get(spec.solver, False):
                f.write(f"{c.selection}, ENCASTRE\n")
            elif c.encastre:
                f.write(f"{c.selection}, 1, 3\n")
                report.notes.append(
                    f"{spec.solver} does not accept the ENCASTRE keyword, so "
                    f"'{c.selection}' was written as DOF 1 through 3. For a "
                    f"solid mesh this is physically identical.")
            else:
                for d in c.dofs:
                    f.write(f"{c.selection}, {d}, {d}\n")

        if force_loads:
            f.write("**\n** loads: area weighted nodal forces. For a 6-node\n"
                    "** triangle the consistent load is ZERO at corner nodes\n"
                    "** and area/3 at mid-side nodes. Corners are absent by\n"
                    "** design, not by omission.\n**\n*CLOAD\n")
            for l in force_loads:
                w = weights[l.selection]
                total_w = sum(w.values())
                for dof, comp in ((1, l.vector[0]), (2, l.vector[1]),
                                  (3, l.vector[2])):
                    if abs(comp) < 1e-12:
                        continue
                    for nid, wi in sorted(w.items()):
                        val = comp * wi / total_w
                        if abs(val) > 1e-14:
                            f.write(f"{nid}, {dof}, {val:.10g}\n")
                report.notes.append(
                    f"load '{l.selection}': {l.vector} N spread over "
                    f"{len(w)} mid-side nodes, total facet area "
                    f"{total_w:.3f} mm2")

        pressures = [l for l in spec.loads if l.kind == "pressure"]
        if pressures:
            f.write("**\n** pressure loads on element faces\n**\n*DSLOAD\n")
            for l in pressures:
                f.write(f"{l.selection}, P, {l.pressure:.10g}\n")
                report.notes.append(
                    f"pressure '{l.selection}': {l.pressure} MPa. Requires a "
                    f"*SURFACE of that name in the deck.")

        f.write(_OUTPUT_BLOCK[spec.solver])
        f.write("*END STEP\n")

    if spec.nlgeom:
        report.warnings.append(
            "NLGEOM is ON. Geometric nonlinearity invalidates any comparison "
            "to small displacement beam theory. Turn it off for validation.")

    return report


# ---------------------------------------------------------------------------
# Cross check: does the computed mode agree with what the caller assumed
# ---------------------------------------------------------------------------

def reconcile_load_case(computed: Optional[ModeEvidence],
                        assumed: Optional[LoadCase]) -> List[str]:
    """If the caller asserted a mode and the geometry says otherwise, say so.

    Silence here would be the worst outcome: the locking rules would run on
    the asserted mode and report PASS for the wrong physics.
    """
    if computed is None or assumed is None:
        return []
    if assumed.dominant_mode == computed.mode:
        return []
    return [f"ASSERTED mode '{assumed.dominant_mode}' disagrees with the "
            f"COMPUTED mode '{computed.mode}'. The locking check will use the "
            f"computed one. If the asserted value was right, the load or the "
            f"constraint selection is wrong."]
