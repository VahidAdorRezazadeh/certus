"""The twelve Milestone B cases (v2 review, section 7.3).

One fixed part (the steel cantilever in decks.py), one solver (CalculiX
2.21), one reference (Euler-Bernoulli + Timoshenko), one modelling decision
varied per case. Each case builds:

  correct   the right model for the stated intent; its tip displacement is
            what the seed is measured against (propagation)
  seed      one deliberate modelling change; must run in CalculiX
  control   a deck where the same rule must NOT fire
  intent    what the engineer states (told tier); infer() strips the one
            governing condition (inferred tier)
  expect    the rule that must FAIL on the seed, or None for a documented
            blind spot

Assertions are on findings, never on stress values.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional

from benchmark import decks as D
from certus.checker import DeckIntent

F = D.F_TIP


def steel(force, **kw) -> DeckIntent:
    base = dict(force=force, units="N-mm-MPa", E_GPa=210.0,
                material_class="elastic", rate_dependent=False, nlgeom=False,
                load_set="TIP", fix_set="ROOT", support="bonded")
    base.update(kw)
    return DeckIntent(**base)


@dataclass
class Case:
    cid: int
    name: str
    anchor: str
    expect: Optional[str]              # rule prefix that must FAIL
    build: Callable[[str], Dict]       # dir -> {"correct","seed","control"}
    infer: Callable[[DeckIntent], DeckIntent]
    comp: int = 2                      # QoI component; -1 = magnitude
    qset: str = "TIP"
    pipeline: bool = False             # case 12: runs through model_agent
    analytical: Optional[float] = None  # propagation against this, if set


def _p(d, n):
    return os.path.join(d, n + ".inp")


# 1 ---------------------------------------------------------------------------
def b1(d):
    it_strain = steel((0, -F / D.B, 0), plane="strain", thickness=1.0,
                      load_set="TIP")
    D.plane_deck(_p(d, "correct"), "CPE8", thickness=1.0, F=F / D.B)
    D.plane_deck(_p(d, "seed"), "CPS8", thickness=1.0, F=F / D.B)
    D.plane_deck(_p(d, "control"), "CPS8", thickness=1.0, F=F / D.B)
    return {"correct": (_p(d, "correct"), it_strain),
            "seed": (_p(d, "seed"), it_strain),
            "control": (_p(d, "control"), replace(it_strain, plane="stress"))}


# 2 ---------------------------------------------------------------------------
PLAST = "*PLASTIC\n250., 0.\n400., 0.02"
NL = dict(step="*STEP, NLGEOM, INC=200",
          proc="*STATIC\n0.05, 1., 1e-6, 0.05")


def b2(d):
    F8 = 8 * F                         # root stress 400 MPa if elastic
    it = steel((0, 0, -F8), yield_MPa=250.0)
    # correct: quadratic reduced hexes, a hardening curve and NLGEOM
    D.solid_deck(_p(d, "correct"), "C3D20R", F=F8, mat_extra=PLAST, **NL)
    D.solid_deck(_p(d, "seed"), "C3D8I", F=F8)
    D.solid_deck(_p(d, "control"), "C3D8I", F=4 * F)
    return {"correct": (_p(d, "correct"),
                        replace(it, material_class="elastic-plastic",
                                nlgeom=True)),
            "seed": (_p(d, "seed"), it),
            "control": (_p(d, "control"), replace(it, force=(0, 0, -4 * F)))}


# 3 ---------------------------------------------------------------------------
def b3(d):
    it = steel((0, 0, -F))
    D.solid_deck(_p(d, "correct"), "C3D8I")
    D.solid_deck(_p(d, "seed"), "C3D8")
    D.solid_deck(_p(d, "control"), "C3D8I")
    return {k: (_p(d, k), it) for k in ("correct", "seed", "control")}


# 4 ---------------------------------------------------------------------------
def b4(d):
    it = steel((0, 0, -F))
    # no CalculiX element is correct here: measured at nu = 0.4999, C3D8I
    # 82.6%, C3D20 94.7%, C3D20R 95.9% of beam theory. C3D20R is the least
    # bad; propagation is measured against the analytical value instead.
    D.solid_deck(_p(d, "correct"), "C3D20R", nu=0.4999)
    D.solid_deck(_p(d, "seed"), "C3D4", n=(40, 4, 4), nu=0.4999)
    D.solid_deck(_p(d, "control"), "C3D4", n=(40, 4, 4), nu=0.3)
    return {k: (_p(d, k), it) for k in ("correct", "seed", "control")}


# 5 ---------------------------------------------------------------------------
def b5(d):
    it = steel((0, 0, -F))
    D.solid_deck(_p(d, "correct"), "C3D8I")
    D.solid_deck(_p(d, "seed"), "C3D8R", n=(20, 1, 1))
    D.solid_deck(_p(d, "control"), "C3D8R", n=(40, 2, 4))
    return {k: (_p(d, k), it) for k in ("correct", "seed", "control")}


# 6 ---------------------------------------------------------------------------
def b6(d):
    it = steel((0, 0, -F))
    g, s = D.solid_deck(_p(d, "correct"), "C3D8I")
    # the top face, minus the clamped edge: a plausible face for 'a load
    # pressing down on the beam', and the wrong one for a tip load
    top = g.where(lambda x, y, z: z > D.H - 1e-9 and x > 1e-9)
    D.solid_deck(_p(d, "control"), "C3D8I")
    # the stated load set is the TIP; the seed loads the top face, which is
    # a plausible face for 'a load pressing down on the beam'
    it_seed = replace(it, load_set="TOPF")

    def sets(g):
        return {"TOPF": g.where(lambda x, y, z: z > D.H - 1e-9 and x > 1e-9)}
    D.solid_deck(_p(d, "seed"), "C3D8I", extra_sets=sets,
                 loads=[f"{t}, 3, {-F / len(top):.10g}" for t in top])
    return {"correct": (_p(d, "correct"), it),
            "seed": (_p(d, "seed"), it_seed),
            "control": (_p(d, "control"), it)}


# 7 ---------------------------------------------------------------------------
def _ss_sets(g):
    return {"PIN0": g.where(lambda x, y, z: x < 1e-9 and z < 1e-9),
            "ROLL1": g.where(lambda x, y, z: x > D.L - 1e-9 and z < 1e-9),
            "END0": g.where(lambda x, y, z: x < 1e-9),
            "END1": g.where(lambda x, y, z: x > D.L - 1e-9),
            "MID": g.where(lambda x, y, z: abs(x - D.L / 2) < 1e-9
                           and z > D.H - 1e-9)}


def b7(d):
    def mid_loads(g):
        m = [n for n in _ss_sets(g)["MID"]]
        return [f"{t}, 3, {-F / len(m):.10g}" for t in m]
    g = D.Grid(20, 2, 2)
    loads = mid_loads(g)
    it = steel((0, 0, -F), load_set="MID", fix_set="PIN0", support="seated")
    D.solid_deck(_p(d, "correct"), "C3D8I", extra_sets=_ss_sets,
                 bc=("PIN0, 1, 3", "ROLL1, 3, 3"), loads=loads)
    D.solid_deck(_p(d, "seed"), "C3D8I", extra_sets=_ss_sets,
                 bc=("END0, 1, 3", "END1, 1, 3"), loads=loads)
    D.solid_deck(_p(d, "control"), "C3D8I", extra_sets=_ss_sets,
                 bc=("PIN0, 1, 3", "ROLL1, 3, 3"), loads=loads)
    return {"correct": (_p(d, "correct"), it),
            "seed": (_p(d, "seed"), replace(it, fix_set="END0")),
            "control": (_p(d, "control"), it)}


# 8 ---------------------------------------------------------------------------
def b8(d):
    it = steel((0, 0, -F))
    D.solid_deck(_p(d, "correct"), "C3D8I")
    D.solid_deck(_p(d, "seed"), "C3D8I", bc=("ROOT, 1, 2",))
    D.solid_deck(_p(d, "control"), "C3D8I")
    return {k: (_p(d, k), it) for k in ("correct", "seed", "control")}


# 9 ---------------------------------------------------------------------------
def b9(d):
    it = steel((0, -F, 0), plane="stress", thickness=D.B, load_set="TIP")
    D.plane_deck(_p(d, "correct"), "CPS8", thickness=1.0, F=F / D.B)
    D.plane_deck(_p(d, "seed"), "CPS8", thickness=1.0, F=F)
    D.plane_deck(_p(d, "control"), "CPS8", thickness=D.B, F=F)
    return {k: (_p(d, k), it) for k in ("correct", "seed", "control")}


# 10 --------------------------------------------------------------------------
def b10(d):
    it = steel((0, 0, -F))
    D.solid_deck(_p(d, "correct"), "C3D8I")
    D.solid_deck(_p(d, "seed"), "C3D8I", E_=210.0)
    D.solid_deck(_p(d, "control"), "C3D8I")
    return {k: (_p(d, k), it) for k in ("correct", "seed", "control")}


# 11 --------------------------------------------------------------------------
def b11(d):
    half = dict(y0=0.0, width=D.B / 2, n=(20, 1, 2))

    def sym(g):
        return {"SYM": g.where(lambda x, y, z: y < 1e-9)}
    gh = D.Grid(20, 1, 2, 0.0, D.B / 2)
    tip = gh.where(lambda x, y, z: x > D.L - 1e-9)
    lat = [f"{t}, 3, {-F / 2 / len(tip):.10g}" for t in tip] + \
          [f"{t}, 2, {F / 2 / len(tip):.10g}" for t in tip]
    # correct: the FULL model under the same (non-symmetric) load
    gf = D.Grid(20, 2, 2)
    tipf = gf.where(lambda x, y, z: x > D.L - 1e-9)
    D.solid_deck(_p(d, "correct"), "C3D8I",
                 loads=[f"{t}, 3, {-F / len(tipf):.10g}" for t in tipf] +
                 [f"{t}, 2, {F / len(tipf):.10g}" for t in tipf])
    D.solid_deck(_p(d, "seed"), "C3D8I", extra_sets=sym,
                 bc=("ROOT, 1, 3", "SYM, 2, 2"), loads=lat, **half)
    D.solid_deck(_p(d, "control"), "C3D8I", extra_sets=sym,
                 bc=("ROOT, 1, 3", "SYM, 2, 2"), **half, F=F / 2)
    return {"correct": (_p(d, "correct"), steel((0, F, -F))),
            "seed": (_p(d, "seed"), steel((0, F / 2, -F / 2))),
            "control": (_p(d, "control"), steel((0, 0, -F / 2)))}


# 12 (pipeline) ---------------------------------------------------------------
def b12(d):
    return {}


def _strip(field):
    return lambda it: replace(it, **{field: None})


CASES = [
    Case(1, "plane stress where plane strain is required", "ALL-FEM worked "
         "failure", "PLANE STRESS/STRAIN", b1, _strip("plane"), comp=1),
    Case(2, "linear elastic material past yield", "AutoFEA cantilever "
         "0.0975 vs 0.8738", "ELASTIC PAST YIELD", b2, _strip("yield_MPa")),
    Case(3, "linear full-integration hex in bending (shear locking)",
         "FEM-Bench future work", "R3", b3, lambda it: it),
    Case(4, "linear tet, near-incompressible (volumetric locking, no cure "
         "in CalculiX)", "FEM-Bench future work", "R1", b4, lambda it: it,
         analytical=-D.reference_tip(nu=0.4999)),
    Case(5, "reduced integration hourglassing in bending", "none",
         "R5 hourglass in bending", b5, lambda it: it),
    Case(6, "load on a wrong but plausible face", "FeaGPT index order; "
         "VFEAgent BC 0.600", None, b6, lambda it: it),
    Case(7, "full clamp where a pin is physical (overconstraint)",
         "VFEAgent checks under-constraint only", "8 SUPPORT REACTIONS", b7,
         _strip("fix_set"), qset="MID"),
    Case(8, "rigid-body mode not removed", "none", "4 ZERO-ENERGY MODES",
         b8, lambda it: it),
    Case(9, "2D thickness convention in the reported force", "MechAgents "
         "plate with a hole", "2D THICKNESS", b9, _strip("thickness"),
         comp=1),
    Case(10, "unit inconsistency (E in GPa in an N-mm-MPa deck)", "gap, "
         "unoccupied", "1 LOAD vs INTENT", b10, _strip("E_GPa")),
    Case(11, "symmetry BC under a non-symmetric load", "none",
         "SYMMETRY vs LOAD", b11, lambda it: it, comp=-1),
    Case(12, "mesh too coarse at a stress concentration, convergence gate",
         "PDE-Agents refines offline only", "CONVERGENCE", b12,
         lambda it: it, pipeline=True),
]
