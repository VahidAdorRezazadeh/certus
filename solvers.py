#!/usr/bin/env python3
"""
solvers.py - what each solver can actually do.

The reason this file exists: the locking check tells you WHICH cure a model
needs. Whether that cure EXISTS is a property of the solver, not of the
physics. Hardcoding CalculiX's limitations into the mesh agent made the mesh
agent lie about what was possible.

Separation of concerns across the three files:

    locking_check.py   pure physics. Solver-agnostic. Never edit this file to
                       accommodate a solver.
    solvers.py         what each solver offers. Edit when adding a solver.
    mesh_agent.py      geometry to mesh. Consumes both of the above.

Adding a solver means adding one SolverProfile here and nothing else.

LICENSE NOTE, deliberately in the code and not only in a document:
Abaqus is registered here as a REFERENCE ORACLE, meaning it is used to produce
known-correct answers for benchmark validation. It is not a deployment target.
Any Abaqus run must use a license the operator is entitled to use for that
purpose. An employer or academic seat is not such a license for commercial
product development.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple, Optional, List

# (family, order) -> set of integration schemes
CapabilityTable = Dict[Tuple[str, int], Set[str]]


@dataclass
class SolverProfile:
    name: str
    capabilities: CapabilityTable
    element_names: Dict[Tuple[str, int, str], str]
    mesh_format: str                 # file extension the mesh agent should write
    deployment_target: bool          # can this ship inside the product
    license_note: str = ""
    notes: List[str] = field(default_factory=list)

    def available_integration(self, family: str, order: int) -> Set[str]:
        return self.capabilities.get((family, order), set())

    def supports(self, family: str, order: int, integration: str) -> bool:
        return integration in self.available_integration(family, order)

    def element_name(self, family: str, order: int,
                     integration: str) -> Optional[str]:
        return self.element_names.get((family, order, integration))


# ---------------------------------------------------------------------------
# CalculiX
# ---------------------------------------------------------------------------
# [verify] Catalogue recalled from the CalculiX manual. Confirm against the
# manual for your version before this drives a customer-facing claim. The
# consequential entries are the tets: no hybrid, no reduced integration.

CALCULIX = SolverProfile(
    name="calculix",
    capabilities={
        ("tet", 1): {"full"},
        ("tet", 2): {"full"},
        ("hex", 1): {"full", "reduced", "incompatible"},
        ("hex", 2): {"full", "reduced"},
        ("wedge", 1): {"full"},
        ("wedge", 2): {"full"},
    },
    element_names={
        ("tet", 1, "full"): "C3D4",
        ("tet", 2, "full"): "C3D10",
        ("hex", 1, "full"): "C3D8",
        ("hex", 1, "reduced"): "C3D8R",
        ("hex", 1, "incompatible"): "C3D8I",
        ("hex", 2, "full"): "C3D20",
        ("hex", 2, "reduced"): "C3D20R",
        ("wedge", 1, "full"): "C3D6",
        ("wedge", 2, "full"): "C3D15",
    },
    mesh_format="inp",
    deployment_target=True,
    license_note="GPL. Free to ship and to run for customers.",
    notes=[
        "No hybrid (mixed u-p) elements at all. Near-incompressible and "
        "fully plastic problems have NO volumetric locking cure on tets.",
        "Hexes do have reduced integration, but automatic hex meshing of "
        "arbitrary imported CAD is unreliable, so this cure is often out of "
        "reach in an automated pipeline.",
    ],
)


# ---------------------------------------------------------------------------
# Abaqus: reference oracle only
# ---------------------------------------------------------------------------
# [verify] Element catalogue recalled from Abaqus documentation. The hybrid
# family (H suffix) is the entry that matters here: it is the cure CalculiX
# lacks, which is exactly why Abaqus is useful as a source of truth.

ABAQUS = SolverProfile(
    name="abaqus",
    capabilities={
        ("tet", 1): {"full", "hybrid"},
        ("tet", 2): {"full", "hybrid"},
        ("hex", 1): {"full", "reduced", "incompatible", "hybrid"},
        ("hex", 2): {"full", "reduced", "hybrid"},
        ("wedge", 1): {"full", "hybrid"},
        ("wedge", 2): {"full", "hybrid"},
    },
    element_names={
        ("tet", 1, "full"): "C3D4",
        ("tet", 1, "hybrid"): "C3D4H",
        ("tet", 2, "full"): "C3D10",
        ("tet", 2, "hybrid"): "C3D10H",
        ("hex", 1, "full"): "C3D8",
        ("hex", 1, "reduced"): "C3D8R",
        ("hex", 1, "incompatible"): "C3D8I",
        ("hex", 1, "hybrid"): "C3D8H",
        ("hex", 2, "full"): "C3D20",
        ("hex", 2, "reduced"): "C3D20R",
        ("hex", 2, "hybrid"): "C3D20H",
        ("wedge", 1, "full"): "C3D6",
        ("wedge", 2, "full"): "C3D15",
    },
    mesh_format="inp",
    deployment_target=False,
    license_note=(
        "COMMERCIAL. Reference oracle only, never a deployment target. "
        "An employer or academic seat does not permit commercial product "
        "development. Confirm entitlement before every use."),
    notes=[
        "C3D10H is the quadratic hybrid tet. It is the volumetric locking "
        "cure that CalculiX does not have, which makes Abaqus useful for "
        "producing the known-correct answer in a benchmark pair.",
        "C3D10M (modified tet) exists as well and is not represented in the "
        "integration enum used here.",
    ],
)


# ---------------------------------------------------------------------------
# FEniCSx
# ---------------------------------------------------------------------------
# FEniCSx has no element catalogue. You write the weak form, so 'hybrid' means
# you build a mixed displacement-pressure function space yourself, and
# 'reduced' means you set the quadrature degree. That is more work per problem
# and more freedom.

FENICSX = SolverProfile(
    name="fenicsx",
    capabilities={
        ("tet", 1): {"full", "reduced", "hybrid"},
        ("tet", 2): {"full", "reduced", "hybrid"},
        ("hex", 1): {"full", "reduced", "hybrid"},
        ("hex", 2): {"full", "reduced", "hybrid"},
    },
    element_names={
        ("tet", 1, "full"): "Lagrange P1",
        ("tet", 2, "full"): "Lagrange P2",
        ("tet", 1, "hybrid"): "mixed P1-P0 (u-p), needs stabilisation",
        ("tet", 2, "hybrid"): "Taylor-Hood P2-P1 (u-p)",
        ("hex", 1, "full"): "Lagrange Q1",
        ("hex", 2, "full"): "Lagrange Q2",
        ("hex", 2, "hybrid"): "Taylor-Hood Q2-Q1 (u-p)",
    },
    mesh_format="msh",
    deployment_target=True,
    license_note="LGPL. Free to ship and to run for customers.",
    notes=[
        "No native Windows build. Windows means Docker or WSL2.",
        "Taylor-Hood P2-P1 is the standard, inf-sup stable cure for "
        "incompressibility, and it is the thing CalculiX cannot do on tets.",
        "'Incompatible modes' has no standard equivalent here.",
        "Every formulation is code you write, so the verification layer has "
        "to check the weak form, not just an element keyword.",
    ],
)


# ---------------------------------------------------------------------------
# Code_Aster
# ---------------------------------------------------------------------------
# [verify] Recalled, not confirmed. Code_Aster's INCO_UPG / INCO_UP families
# provide mixed formulations, and B-bar style options exist. Confirm before
# relying on this entry.

CODE_ASTER = SolverProfile(
    name="code_aster",
    capabilities={
        ("tet", 1): {"full"},
        ("tet", 2): {"full", "hybrid"},
        ("hex", 1): {"full", "reduced", "hybrid"},
        ("hex", 2): {"full", "reduced", "hybrid"},
    },
    element_names={
        ("tet", 2, "full"): "3D TETRA10",
        ("tet", 2, "hybrid"): "3D_INCO_UPG TETRA10",
        ("hex", 1, "full"): "3D HEXA8",
        ("hex", 1, "hybrid"): "3D_INCO_UPG HEXA8",
        ("hex", 2, "full"): "3D HEXA20",
    },
    mesh_format="med",
    deployment_target=True,
    license_note="GPL. Free to ship and to run for customers.",
    notes=[
        "ENTIRE PROFILE IS UNVERIFIED. Confirm against the Code_Aster docs "
        "before this influences a stack decision.",
        "Mesh format is MED, so gmsh output needs conversion via meshio.",
    ],
)


REGISTRY: Dict[str, SolverProfile] = {
    p.name: p for p in (CALCULIX, ABAQUS, FENICSX, CODE_ASTER)
}


def get_solver(name: str) -> SolverProfile:
    key = name.lower()
    if key not in REGISTRY:
        raise KeyError(f"unknown solver '{name}'. Known: {sorted(REGISTRY)}")
    return REGISTRY[key]


def deployment_solvers() -> List[str]:
    return [n for n, p in REGISTRY.items() if p.deployment_target]


def cure_availability(family: str, order: int,
                      needed: str) -> Dict[str, bool]:
    """Which solvers offer a given locking cure for a given element.

    This is the function that turns a locking finding into a stack decision
    instead of a dead end.
    """
    return {n: p.supports(family, order, needed) for n, p in REGISTRY.items()}


# ---------------------------------------------------------------------------
# Abaqus / CalculiX deck retyping
# ---------------------------------------------------------------------------

def retype_inp(src_path: str, dst_path: str, new_type: str) -> str:
    """Rewrite the element type in a Gmsh-written Abaqus deck.

    Gmsh writes '*ELEMENT, type=C3D10'. To run the oracle you need
    'type=C3D10H'. The node and connectivity data are identical: hybrid
    elements in Abaqus have the same nodal topology, the extra pressure
    degrees of freedom are internal. So a keyword rewrite is sufficient and
    does not touch the mesh.
    """
    out_lines = []
    changed = 0
    with open(src_path, "r") as f:
        for line in f:
            stripped = line.strip()
            if stripped.upper().startswith("*ELEMENT") and "type=" in line:
                head, _, tail = line.partition("type=")
                rest = tail.split(",", 1)
                suffix = ("," + rest[1]) if len(rest) > 1 else "\n"
                line = f"{head}type={new_type}{suffix}"
                if not line.endswith("\n"):
                    line += "\n"
                changed += 1
            out_lines.append(line)
    if changed == 0:
        raise ValueError(f"no *ELEMENT type= line found in {src_path}")
    with open(dst_path, "w") as f:
        f.writelines(out_lines)
    return dst_path


if __name__ == "__main__":
    print("Solver registry")
    print("=" * 60)
    for name, p in REGISTRY.items():
        tag = "DEPLOY" if p.deployment_target else "ORACLE ONLY"
        print(f"\n{name.upper():12s} [{tag}]  mesh: .{p.mesh_format}")
        print(f"  license: {p.license_note}")
        for (fam, order), ints in sorted(p.capabilities.items()):
            print(f"    {fam}/order{order}: {sorted(ints)}")
        for n in p.notes:
            print(f"  note: {n}")

    print("\n" + "=" * 60)
    print("Who can cure volumetric locking on quadratic tets (hybrid)?")
    for solver, ok in cure_availability("tet", 2, "hybrid").items():
        print(f"  {solver:12s} {'YES' if ok else 'no'}")
    print("\nDeployment-capable solvers:", deployment_solvers())
