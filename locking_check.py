#!/usr/bin/env python3
"""
locking_check.py - deterministic, pre-solve locking risk check.

This is CHECK TYPE 3 of the three-check verification plan:
  1. mesh / order convergence check   (needs a second solve)   [NOT IMPLEMENTED]
  2. energy or analytical reference check (needs a reference)  [NOT IMPLEMENTED]
  3. element formulation rule check  (no solve at all)         <-- THIS FILE

Why this one first: it costs zero compute, it runs before the solver, and it
catches the most common silent-stiffness errors in linear FE work.

Design principle: NO LLM inference happens in this file. Every decision is
deterministic logic on numbers. The LLM's job is upstream (turning free text
into the structured inputs below) and downstream (explaining the findings to
the user). The check itself must be reproducible and auditable, because it is
the thing you will claim as the product moat.

Scope limit, stated honestly: this file reasons about CONTINUUM SOLID elements
(hex, tet, wedge). Shell, beam and membrane locking modes are out of scope and
are reported as UNSUPPORTED rather than silently passed.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, List, Dict, Any


# ----------------------------------------------------------------------------
# Severity and routing
# ----------------------------------------------------------------------------

class Severity(str, Enum):
    PASS = "PASS"           # no known issue for this rule
    INFO = "INFO"           # worth knowing, not an error
    MODERATE = "MODERATE"   # results likely biased, quantify before trusting
    SEVERE = "SEVERE"       # results expected to be wrong, do not report as-is
    BLOCKED = "BLOCKED"     # check could not be evaluated, inputs insufficient
    INVALID = "INVALID"     # inputs are physically impossible

_ORDER = {
    Severity.PASS: 0,
    Severity.INFO: 1,
    Severity.BLOCKED: 2,
    Severity.MODERATE: 3,
    Severity.SEVERE: 4,
    Severity.INVALID: 5,
}


class Owner(str, Enum):
    """Which sub-agent can actually apply the fix.

    This matters: the meshing agent controls element SHAPE and ORDER (Gmsh).
    It does NOT control the integration scheme or the u-p formulation, which
    are element/section properties set in the solver deck. Routing a volumetric
    locking finding to the meshing agent is a dead end.
    """
    MESH = "MESH_AGENT"            # element family, element order (Gmsh)
    SOLVER = "SOLVER_SETUP_AGENT"  # integration scheme, hybrid formulation
    EITHER = "MESH_OR_SOLVER"      # two valid fixes with different owners
    BC = "BC_AGENT"                # load / constraint definition
    HUMAN = "HUMAN"                # needs a decision, not an automatic fix


# ----------------------------------------------------------------------------
# Structured inputs
# ----------------------------------------------------------------------------

VALID_FAMILIES = ("hex", "tet", "wedge")
VALID_INTEGRATION = ("full", "reduced", "incompatible", "hybrid")
VALID_MODES = ("bending", "axial", "shear", "torsion", "mixed", "unknown")


@dataclass
class ElementSpec:
    family: str              # "hex" | "tet" | "wedge"
    order: int               # 1 (linear) | 2 (quadratic)
    integration: str         # "full" | "reduced" | "incompatible" | "hybrid"
    hourglass_control: bool = False   # only meaningful for reduced order-1
    elements_through_thickness: Optional[int] = None  # optional bending check

    def label(self) -> str:
        return f"{self.family}/order{self.order}/{self.integration}"


@dataclass
class MaterialSpec:
    E: float                       # Young's modulus, consistent units
    nu: float                      # Poisson ratio
    yield_stress: Optional[float] = None
    plastic_response_expected: bool = False   # will the part yield in this run
    name: str = "unnamed"


@dataclass
class LoadCase:
    """Coarse description of the dominant deformation mode.

    Populated by the BC sub-agent. If the BC agent is not confident, it must
    say "unknown" rather than guess: a wrong mode here turns a SEVERE finding
    into a silent PASS, which is the worst possible failure of this check.
    """
    dominant_mode: str                 # see VALID_MODES
    source: str = "bc_agent"           # provenance for the audit trail
    confident: bool = True


@dataclass
class Finding:
    rule_id: str
    mechanism: str
    severity: Severity
    reason: str
    consequence: str
    recommended_action: str
    owner: Owner

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["owner"] = self.owner.value
        return d


@dataclass
class LockingReport:
    findings: List[Finding] = field(default_factory=list)
    element: Optional[ElementSpec] = None
    material: Optional[MaterialSpec] = None
    load_case: Optional[LoadCase] = None

    @property
    def worst(self) -> Severity:
        if not self.findings:
            return Severity.PASS
        return max((f.severity for f in self.findings), key=lambda s: _ORDER[s])

    @property
    def safe_to_solve(self) -> bool:
        """False means: do not run the solver yet, or run it knowing the
        result will need correction. SEVERE and INVALID block. BLOCKED also
        blocks, because an unevaluated check is not a passed check."""
        return _ORDER[self.worst] < _ORDER[Severity.BLOCKED]

    def actionable(self) -> List[Finding]:
        return [f for f in self.findings
                if _ORDER[f.severity] >= _ORDER[Severity.MODERATE]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worst_severity": self.worst.value,
            "safe_to_solve": self.safe_to_solve,
            "findings": [f.to_dict() for f in self.findings],
        }

    def render(self) -> str:
        head = f"LOCKING CHECK: {self.worst.value}"
        if self.element:
            head += f"  [{self.element.label()}"
            if self.material:
                head += f", nu={self.material.nu}"
            if self.load_case:
                head += f", mode={self.load_case.dominant_mode}"
            head += "]"
        lines = [head, "=" * len(head)]
        if not self.findings:
            lines.append("No rule triggered.")
        for f in self.findings:
            lines.append("")
            lines.append(f"[{f.severity.value}] {f.rule_id}: {f.mechanism}")
            lines.append(f"  why      : {f.reason}")
            lines.append(f"  effect   : {f.consequence}")
            lines.append(f"  fix      : {f.recommended_action}")
            lines.append(f"  route to : {f.owner.value}")
        return "\n".join(lines)


# ----------------------------------------------------------------------------
# Thresholds (single place to tune, and to defend in a benchmark writeup)
# ----------------------------------------------------------------------------

NU_NEAR_INCOMPRESSIBLE = 0.45   # severe volumetric locking risk above this
NU_ELEVATED = 0.40              # moderate risk band
MIN_ELEMENTS_THROUGH_THICKNESS_BENDING = 3

BENDING_LIKE = ("bending", "mixed")   # modes with significant flexural response


# ----------------------------------------------------------------------------
# Rules
# ----------------------------------------------------------------------------

def _r0_input_sanity(el: ElementSpec, mat: MaterialSpec,
                     lc: LoadCase) -> List[Finding]:
    out = []
    if el.family not in VALID_FAMILIES:
        out.append(Finding(
            "R0a", "invalid input", Severity.INVALID,
            f"element family '{el.family}' is not one of {VALID_FAMILIES}",
            "check cannot run",
            "supply a supported continuum element family",
            Owner.HUMAN))
    if el.order not in (1, 2):
        out.append(Finding(
            "R0b", "invalid input", Severity.INVALID,
            f"element order {el.order} is not 1 or 2",
            "check cannot run",
            "supply order 1 or 2",
            Owner.HUMAN))
    if el.integration not in VALID_INTEGRATION:
        out.append(Finding(
            "R0c", "invalid input", Severity.INVALID,
            f"integration '{el.integration}' is not one of {VALID_INTEGRATION}",
            "check cannot run",
            "supply a supported integration scheme",
            Owner.HUMAN))
    if mat.nu >= 0.5:
        out.append(Finding(
            "R0d", "invalid material", Severity.INVALID,
            f"nu = {mat.nu} is not admissible for an isotropic 3D solid "
            f"(bulk modulus is singular or negative at nu >= 0.5)",
            "the displacement formulation is ill-posed, not merely locked",
            "use nu slightly below 0.5 with a hybrid u-p formulation, or a "
            "dedicated incompressible formulation",
            Owner.HUMAN))
    if mat.nu < 0.0:
        out.append(Finding(
            "R0e", "unusual material", Severity.INFO,
            f"nu = {mat.nu} is negative (auxetic)",
            "physically possible but rare, often a data-entry error",
            "confirm the Poisson ratio is intended",
            Owner.HUMAN))
    if lc.dominant_mode not in VALID_MODES:
        out.append(Finding(
            "R0f", "invalid input", Severity.INVALID,
            f"dominant_mode '{lc.dominant_mode}' is not one of {VALID_MODES}",
            "check cannot run",
            "supply a supported deformation mode",
            Owner.HUMAN))
    return out


def _r1_volumetric_incompressible(el, mat, lc) -> Optional[Finding]:
    """Volumetric locking from near-incompressible ELASTIC response.

    Mechanism: as nu approaches 0.5 the material resists volume change almost
    rigidly. A fully integrated displacement element cannot represent a
    volume-preserving deformation field at every integration point, so the
    incompressibility constraint over-constrains the element and it stiffens
    artificially. Independent of the load being bending: it appears whenever
    deviatoric deformation dominates.
    """
    if mat.nu < NU_ELEVATED:
        return None
    if el.integration in ("hybrid",):
        return None

    near = mat.nu >= NU_NEAR_INCOMPRESSIBLE

    if el.integration == "full":
        sev = Severity.SEVERE if (near or el.order == 1) else Severity.MODERATE
        if el.order == 2 and not near:
            sev = Severity.INFO
    elif el.integration == "reduced":
        # reduced integration relaxes the volumetric constraint substantially
        sev = Severity.INFO if near else Severity.PASS
    elif el.integration == "incompatible":
        sev = Severity.MODERATE if near else Severity.INFO
    else:
        sev = Severity.INFO

    if sev == Severity.PASS:
        return None

    return Finding(
        "R1", "volumetric locking (near-incompressible elastic)", sev,
        f"nu = {mat.nu} with {el.integration} integration, order {el.order}. "
        f"The volumetric constraint count grows relative to the available "
        f"displacement degrees of freedom.",
        "artificially high stiffness; deflections under-predicted and stresses "
        "distorted. The solve converges normally and reports nothing.",
        "use a hybrid (mixed u-p) formulation, or reduced integration with "
        "hourglass control, or a B-bar / F-bar element. Refining the mesh does "
        "NOT remove volumetric locking.",
        Owner.SOLVER)


def _r2_volumetric_plasticity(el, mat, lc) -> Optional[Finding]:
    """Volumetric locking caused by PLASTIC flow, not by elastic nu.

    Mechanism: J2 (von Mises) plastic flow is isochoric. Once a region is
    fully plastic its incremental response is effectively incompressible
    regardless of the elastic Poisson ratio. A part with nu = 0.3 that yields
    can volumetrically lock exactly like a rubber part.

    This rule exists because the elastic-nu rule alone gives a false PASS on
    every elastic-plastic metal job, which is most metal FE work.
    """
    if not mat.plastic_response_expected:
        return None
    if el.integration in ("hybrid", "reduced"):
        return None
    sev = Severity.SEVERE if el.order == 1 else Severity.MODERATE
    return Finding(
        "R2", "volumetric locking (plastic incompressibility)", sev,
        f"plastic response is expected and integration is '{el.integration}'. "
        f"J2 plastic flow is volume preserving, so the effective Poisson ratio "
        f"tends to 0.5 in yielded regions even though elastic nu = {mat.nu}.",
        "over-stiff post-yield response; load capacity over-predicted and "
        "plastic strain localisation suppressed.",
        "use reduced integration with hourglass control, a hybrid formulation, "
        "or F-bar. Do not rely on elastic nu to judge this case.",
        Owner.SOLVER)


def _r3_shear_locking(el, mat, lc) -> Optional[Finding]:
    """Shear locking (parasitic shear) in bending.

    Mechanism: a linear element has straight edges, so it cannot represent the
    curvature of pure bending. It represents bending with a spurious shear
    strain field, absorbing energy that should go into bending. Result: far too
    stiff. Trigger is ORDER + FULL INTEGRATION + BENDING. Poisson ratio is
    irrelevant here. This is the rule most often confused with R1.
    """
    if lc.dominant_mode == "unknown":
        return None   # handled by R6
    if lc.dominant_mode not in BENDING_LIKE:
        return None
    if el.order != 1:
        return None
    if el.integration in ("reduced", "incompatible"):
        return None   # both are standard cures for parasitic shear

    sev = Severity.SEVERE
    if lc.dominant_mode == "mixed":
        sev = Severity.MODERATE

    return Finding(
        "R3", "shear locking (parasitic shear in bending)", sev,
        f"linear {el.family} elements with full integration under a "
        f"{lc.dominant_mode} load. Straight-edged linear elements cannot "
        f"represent bending curvature and generate spurious shear strain. "
        f"This is independent of nu = {mat.nu}.",
        "bending stiffness over-predicted, commonly by a large factor; "
        "deflection under-predicted. The solve converges and reports nothing.",
        "raise the element order to 2 (meshing agent), or switch to reduced "
        "integration with hourglass control / incompatible modes (solver "
        "agent). Mesh refinement at order 1 converges very slowly and is not "
        "the efficient fix.",
        Owner.EITHER)


def _r4_linear_tet(el, mat, lc) -> Optional[Finding]:
    """Linear tetrahedron is a constant-strain element.

    Mechanism: C3D4 has a constant strain field over the element, so it cannot
    represent any strain gradient. It is excessively stiff in bending and it
    volumetrically locks readily. Flag it regardless of load mode, because even
    an 'axial' job usually has stress gradients near holes and fillets.
    """
    if not (el.family == "tet" and el.order == 1):
        return None
    sev = Severity.SEVERE if lc.dominant_mode in BENDING_LIKE else Severity.MODERATE
    return Finding(
        "R4", "constant-strain element (linear tetrahedron)", sev,
        "linear tets carry a constant strain field per element and cannot "
        "represent a strain gradient.",
        "globally over-stiff response and poor stress resolution at holes, "
        "fillets and any stress concentration.",
        "mesh with quadratic tets (order 2) or with hexahedra. Linear tets "
        "should not be used for stress results in production work.",
        Owner.MESH)


def _r5_hourglassing(el, mat, lc) -> Optional[Finding]:
    """The counter-risk of the usual locking cure.

    Reduced integration on a linear element has one integration point, which
    admits zero-energy deformation modes (hourglassing). Recommending reduced
    integration without saying this would be dishonest.
    """
    if not (el.order == 1 and el.integration == "reduced"):
        return None
    if el.hourglass_control:
        sev = Severity.INFO
        reason = ("linear reduced-integration elements have zero-energy modes; "
                  "hourglass control is enabled, which normally suppresses them.")
        action = ("verify artificial (hourglass) energy stays small relative to "
                  "internal energy in the solver output.")
    else:
        sev = Severity.MODERATE
        reason = ("linear reduced-integration elements have zero-energy "
                  "deformation modes and hourglass control is NOT enabled.")
        action = ("enable hourglass control, or use incompatible modes / "
                  "quadratic reduced integration instead.")
    return Finding("R5", "hourglassing (zero-energy modes)", sev, reason,
                   "spurious mesh-level deformation patterns; displacements "
                   "and stresses can be badly wrong in the opposite direction "
                   "from locking (too soft).",
                   action, Owner.SOLVER)


def _r6_unknown_mode(el, mat, lc) -> Optional[Finding]:
    """An unevaluated check is not a passed check."""
    if lc.dominant_mode != "unknown" and lc.confident:
        return None
    if lc.dominant_mode == "unknown":
        reason = "the dominant deformation mode was not determined."
    else:
        reason = (f"the dominant mode was reported as '{lc.dominant_mode}' but "
                  f"the source ({lc.source}) marked it as not confident.")
    return Finding(
        "R6", "shear locking check not evaluable", Severity.BLOCKED,
        reason,
        "the shear-locking rule (R3) could not be applied. Absence of a "
        "finding here does NOT mean absence of locking.",
        "have the BC agent classify the load case, or run the check for the "
        "worst-case assumption 'bending'.",
        Owner.BC)


def _r7_bending_resolution(el, mat, lc) -> Optional[Finding]:
    """Not locking, but the other classic cause of wrong bending stiffness."""
    n = el.elements_through_thickness
    if n is None or lc.dominant_mode not in BENDING_LIKE:
        return None
    if n >= MIN_ELEMENTS_THROUGH_THICKNESS_BENDING:
        return None
    sev = Severity.SEVERE if n <= 1 else Severity.MODERATE
    return Finding(
        "R7", "insufficient through-thickness resolution in bending", sev,
        f"{n} element(s) through the thickness under a {lc.dominant_mode} "
        f"load. The linear through-thickness stress variation of bending "
        f"cannot be resolved.",
        "bending stiffness and surface stress both wrong, separately from any "
        "locking effect.",
        f"use at least {MIN_ELEMENTS_THROUGH_THICKNESS_BENDING} elements "
        f"through the thickness, or use quadratic elements, or use shells if "
        f"the part is genuinely thin.",
        Owner.MESH)


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def check_locking(element: ElementSpec, material: MaterialSpec,
                  load_case: LoadCase) -> LockingReport:
    """Run the deterministic pre-solve locking rule set.

    Returns a LockingReport. Call report.safe_to_solve before handing the model
    to the solver agent.
    """
    report = LockingReport(element=element, material=material,
                           load_case=load_case)

    sanity = _r0_input_sanity(element, material, load_case)
    report.findings.extend(sanity)
    if any(f.severity == Severity.INVALID for f in sanity):
        return report   # do not reason further on invalid inputs

    for rule in (_r1_volumetric_incompressible,
                 _r2_volumetric_plasticity,
                 _r3_shear_locking,
                 _r4_linear_tet,
                 _r5_hourglassing,
                 _r6_unknown_mode,
                 _r7_bending_resolution):
        f = rule(element, material, load_case)
        if f is not None:
            report.findings.append(f)

    return report


# ----------------------------------------------------------------------------
# Optional convenience: map a spec to a CalculiX element name.
# Kept separate from the rules so the rules stay solver-agnostic.
# ----------------------------------------------------------------------------

CCX_NAMES = {
    ("hex", 1, "full"): "C3D8",
    ("hex", 1, "reduced"): "C3D8R",
    ("hex", 1, "incompatible"): "C3D8I",
    ("hex", 2, "full"): "C3D20",
    ("hex", 2, "reduced"): "C3D20R",
    ("tet", 1, "full"): "C3D4",
    ("tet", 2, "full"): "C3D10",
    ("wedge", 1, "full"): "C3D6",
    ("wedge", 2, "full"): "C3D15",
}


def to_calculix(el: ElementSpec) -> Optional[str]:
    """Best-effort name lookup. Returns None if the combination has no direct
    CalculiX equivalent, which is itself useful information for the solver
    agent."""
    return CCX_NAMES.get((el.family, el.order, el.integration))


def suggest_element(material: MaterialSpec, load_case: LoadCase,
                    prefer_family: str = "hex") -> ElementSpec:
    """Deterministic suggestion of a safe default, given material and load.

    This is a starting point for the mesh/solver agents, not a substitute for
    running check_locking on whatever they finally choose.
    """
    incompressible = (material.nu >= NU_NEAR_INCOMPRESSIBLE
                      or material.plastic_response_expected)
    bending = load_case.dominant_mode in BENDING_LIKE or load_case.dominant_mode == "unknown"

    if prefer_family == "hex":
        if incompressible or bending:
            return ElementSpec("hex", 2, "reduced", hourglass_control=True)
        return ElementSpec("hex", 1, "incompatible")
    # tet meshing is far easier to automate on arbitrary CAD, so it will be
    # the realistic default for the agent on imported STEP geometry.
    # Ask for the hybrid formulation when the physics needs it. Whether the
    # target solver HAS a hybrid tet is not this function's problem: the
    # solver registry filters it and reports the gap.
    if incompressible:
        return ElementSpec("tet", 2, "hybrid")
    return ElementSpec("tet", 2, "full")


# ----------------------------------------------------------------------------
# Self-test: the canonical cases this check must get right.
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    steel = MaterialSpec(E=210e3, nu=0.3, name="generic steel")
    rubberish = MaterialSpec(E=10.0, nu=0.499, name="near-incompressible")
    steel_plastic = MaterialSpec(E=210e3, nu=0.3, yield_stress=350.0,
                                 plastic_response_expected=True,
                                 name="steel, yielding")

    cases = [
        ("1. linear hex, full int, steel, BENDING (pure shear locking)",
         ElementSpec("hex", 1, "full"), steel, LoadCase("bending")),

        ("2. linear hex, full int, steel, AXIAL (should be clean)",
         ElementSpec("hex", 1, "full"), steel, LoadCase("axial")),

        ("3. linear hex, full int, nu=0.499, BENDING (both mechanisms)",
         ElementSpec("hex", 1, "full"), rubberish, LoadCase("bending")),

        ("4. quadratic hex, reduced int, nu=0.499, BENDING (the good setup)",
         ElementSpec("hex", 2, "reduced", hourglass_control=True),
         rubberish, LoadCase("bending")),

        ("5. linear tet, steel, BENDING (worst common default)",
         ElementSpec("tet", 1, "full"), steel, LoadCase("bending")),

        ("6. linear hex, full int, steel that YIELDS, axial",
         ElementSpec("hex", 1, "full"), steel_plastic, LoadCase("axial")),

        ("7. linear hex, reduced, no hourglass control, steel, bending",
         ElementSpec("hex", 1, "reduced", hourglass_control=False),
         steel, LoadCase("bending")),

        ("8. mode UNKNOWN (must not silently pass)",
         ElementSpec("hex", 1, "full"), steel, LoadCase("unknown")),

        ("9. quadratic hex full int, steel, bending, 1 elem thickness",
         ElementSpec("hex", 2, "full", elements_through_thickness=1),
         steel, LoadCase("bending")),

        ("10. nu = 0.5 exactly (ill-posed, not merely locked)",
         ElementSpec("hex", 2, "reduced"), MaterialSpec(E=10, nu=0.5),
         LoadCase("bending")),
    ]

    for title, el, mat, lc in cases:
        print("\n" + "#" * 78)
        print("# " + title)
        ccx = to_calculix(el)
        print("# CalculiX element: " + (ccx if ccx else "no direct equivalent"))
        print("#" * 78)
        print(check_locking(el, mat, lc).render())

    print("\n" + "#" * 78)
    print("# suggest_element for near-incompressible bending:",
          suggest_element(rubberish, LoadCase("bending")))
    print("# suggest_element for steel axial:",
          suggest_element(steel, LoadCase("axial")))
