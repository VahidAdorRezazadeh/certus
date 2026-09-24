#!/usr/bin/env python3
"""
checker.py - check a CalculiX deck someone else wrote, against a stated intent.

This is the checker without the pipeline: a deck in, findings out. It uses
nothing the deck's author supplied except the deck itself, plus what the
engineer states (Intent). It is what Milestone B's benchmark runs, and it is
the product question 'checker or pipeline' made concrete.

Findings come from:
  deck-level, pre-solve   plane stress/strain vs intent, 2D thickness vs
                          intent, locking rules R1-R7 on the deck's own
                          element type, material and geometry, symmetry
                          planes against the load, rigid-mode rank (check 4)
  post-solve              the seven checks (invariants.py), support
                          reactions (Step 8), elastic result past the stated
                          yield stress

Frozen finding schema (Milestone B, Phase 2 item 9):
  rule, verdict (PASS | FAIL | NOT EVALUATED | NOT NEEDED), severity,
  owner, lever, detail, provenance, propagation (filled by the benchmark),
  consequence_weight (reserved, None until defined)

Usage:
    python checker.py deck.inp            (checks that need no intent)
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
import math
import os
import sys

import invariants as INV
from invariants import Intent, Deck, read_deck
from locking_check import (ElementSpec, MaterialSpec, LoadCase, check_locking,
                           Severity)

Vec = Tuple[float, float, float]


@dataclass
class CFinding:
    rule: str
    verdict: str
    severity: str = ""
    owner: str = ""
    lever: str = ""
    detail: str = ""
    provenance: str = ""
    propagation: Optional[float] = None
    consequence_weight: Optional[float] = None

    def render(self) -> str:
        s = f"{self.rule:28s} {self.verdict:13s} {self.severity}"
        s += f"\n  {self.detail}"
        if self.verdict == "FAIL":
            s += f"\n  owner {self.owner}; lever: {self.lever}"
        return s


def _from_inv(f: "INV.Finding", prov: str) -> CFinding:
    return CFinding(f.rule, f.verdict,
                    "SEVERE" if f.verdict == "FAIL" else "", f.owner, f.cure,
                    f.detail, prov)


@dataclass
class DeckIntent(Intent):
    """Intent plus what only a deck-level checker needs."""
    plane: Optional[str] = None            # "stress" | "strain" | None
    thickness: Optional[float] = None      # out-of-plane thickness, 2D
    yield_MPa: Optional[float] = None
    fix_set: Optional[str] = None          # support node set, for Step 8


# ---------------------------------------------------------------------------
# element and material from the deck
# ---------------------------------------------------------------------------

_ELEMENTS = {
    "C3D4": ("tet", 1, "full"), "C3D10": ("tet", 2, "full"),
    "C3D8": ("hex", 1, "full"), "C3D8R": ("hex", 1, "reduced"),
    "C3D8I": ("hex", 1, "incompatible"), "C3D20": ("hex", 2, "full"),
    "C3D20R": ("hex", 2, "reduced"), "C3D6": ("wedge", 1, "full"),
    "C3D15": ("wedge", 2, "full"),
    "CPS4": ("quad", 1, "full"), "CPE4": ("quad", 1, "full"),
    "CPS4R": ("quad", 1, "reduced"), "CPE4R": ("quad", 1, "reduced"),
    "CPS8": ("quad", 2, "full"), "CPE8": ("quad", 2, "full"),
    "CPS8R": ("quad", 2, "reduced"), "CPE8R": ("quad", 2, "reduced"),
    "CPS3": ("tri", 1, "full"), "CPE3": ("tri", 1, "full"),
    "CPS6": ("tri", 2, "full"), "CPE6": ("tri", 2, "full"),
}


def _material(deck: Deck) -> Tuple[Optional[float], Optional[float], bool]:
    E = nu = None
    plastic = False
    for k, _o, data in INV._cards(deck.path):
        if k == "ELASTIC" and data and E is None:
            t = [x.strip() for x in data[0].split(",")]
            try:
                E, nu = float(t[0]), float(t[1])
            except (ValueError, IndexError):
                pass
        if k == "PLASTIC":
            plastic = True
    return E, nu, plastic


# ---------------------------------------------------------------------------
# dominant mode and member thickness from the deck alone
# ---------------------------------------------------------------------------

def _load_points(deck: Deck) -> Tuple[Vec, List[Tuple[Vec, float]]]:
    F = [0.0, 0.0, 0.0]
    pts: List[Tuple[Vec, float]] = []
    for n, d, v in deck.cloads:
        if 1 <= d <= 3:
            F[d - 1] += v
            pts.append((deck.nodes[n], abs(v)))
    for name, d, v in deck.cloads_by_set:
        for n in deck.resolve_nodes(name):
            if 1 <= d <= 3:
                F[d - 1] += v
                pts.append((deck.nodes[n], abs(v)))
    for p, ids, A in INV.dsload_faces(deck)[0]:
        c = tuple(sum(deck.nodes[i][k] for i in ids) / len(ids)
                  for k in range(3))
        f = [-p * a for a in A]
        for k in range(3):
            F[k] += f[k]
        pts.append((c, math.sqrt(sum(x * x for x in f))))
    return tuple(F), pts


def deck_mode(deck: Deck):
    """(mode, lever arm, depth, members through thickness, notes).
    Same method as case_agent.compute_dominant_mode, measured on the deck:
    section depth is the thickest connected member cut by a slab at half the
    lever arm, and the element count across it uses each element's own
    extent along the depth direction, not a requested size."""
    F, pts = _load_points(deck)
    fm = math.sqrt(sum(v * v for v in F))
    cn = INV.constrained_nodes(deck)
    if fm < 1e-12 or not pts or not cn:
        return "unknown", None, None, None, "no load resultant or no support"
    w = sum(x[1] for x in pts) or 1.0
    lc = [sum(p[k] * wi for p, wi in pts) / w for k in range(3)]
    cc = [sum(deck.nodes[n][k] for n in cn) / len(cn) for k in range(3)]
    r = [lc[k] - cc[k] for k in range(3)]
    rl = math.sqrt(sum(v * v for v in r))
    if rl < 1e-9:
        return "unknown", 0.0, None, None, "load and support share a centroid"
    e = [v / rl for v in r]
    fa = sum(F[k] * e[k] for k in range(3))
    acr = [F[k] - fa * e[k] for k in range(3)]
    ft = math.sqrt(sum(v * v for v in acr))
    if abs(fa) > 3.0 * ft:
        return "axial", rl, None, None, "force along the lever arm"
    d = [v / ft for v in acr]
    # slab of elements at half the lever arm
    mid = [cc[k] + 0.5 * rl * e[k] for k in range(3)]
    # cut every element by the plane through mid normal to e, and keep the
    # cut points: projecting whole elements onto d leaks their length along
    # e into the depth whenever e is tilted (measured: 1.91 layers for 2)
    sl, cuts = [], {}
    for eid, (_t, conn) in deck.elements.items():
        P = [deck.nodes[n] for n in conn]
        s = [sum((p[k] - mid[k]) * e[k] for k in range(3)) for p in P]
        if not (min(s) <= 0.0 <= max(s)):
            continue
        pts = []
        for a in range(len(P)):
            for b in range(a + 1, len(P)):
                if (s[a] <= 0.0 <= s[b]) or (s[b] <= 0.0 <= s[a]):
                    t = s[a] / (s[a] - s[b]) if s[a] != s[b] else 0.0
                    pts.append([P[a][k] + t * (P[b][k] - P[a][k])
                                for k in range(3)])
        if pts:
            sl.append(conn)
            cuts[id(conn)] = pts
    if not sl:
        return "unknown", rl, None, None, "no element at half lever arm"
    parent: Dict[int, int] = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for conn in sl:
        for n in conn[1:]:
            parent[find(n)] = find(conn[0])
    groups: Dict[int, List] = {}
    for conn in sl:
        groups.setdefault(find(conn[0]), []).append(conn)
    best = None
    dot = lambda p: sum(p[k] * d[k] for k in range(3))
    for g in groups.values():
        proj = [dot(p) for conn in g for p in cuts[id(conn)]]
        depth = max(proj) - min(proj)
        ext = [max(dot(p) for p in cuts[id(conn)]) -
               min(dot(p) for p in cuts[id(conn)]) for conn in g]
        h = sum(ext) / len(ext)
        if best is None or depth > best[0]:
            best = (depth, depth / h if h > 0 else None)
    depth, nthru = best
    mode = "bending" if rl / depth >= 2.0 else "shear"
    return mode, rl, depth, nthru, f"lever {rl:.3g}, depth {depth:.3g}"


# ---------------------------------------------------------------------------
# deck-level checks
# ---------------------------------------------------------------------------

def check_plane(deck: Deck, it: DeckIntent) -> Optional[CFinding]:
    types = {t for t, _ in deck.elements.values()}
    two_d = {t for t in types if t[:3] in ("CPS", "CPE", "CAX")}
    if not two_d:
        return None
    R = "PLANE STRESS/STRAIN"
    if it.plane is None:
        return CFinding(R, "NOT EVALUATED", detail="2D elements "
                        f"{sorted(two_d)} but no stated plane assumption",
                        provenance="checker.check_plane")
    want = {"stress": "CPS", "strain": "CPE", "axisym": "CAX"}[it.plane]
    wrong = sorted(t for t in two_d if not t.startswith(want))
    if wrong:
        return CFinding(R, "FAIL", "SEVERE", "model_agent",
                        f"use {want} elements for plane {it.plane}",
                        f"stated plane {it.plane}, deck uses {wrong}",
                        "checker.check_plane")
    return CFinding(R, "PASS", detail=f"plane {it.plane}: {sorted(two_d)}",
                    provenance="checker.check_plane")


def check_thickness(deck: Deck, it: DeckIntent) -> Optional[CFinding]:
    """2D thickness convention. CalculiX takes the *SOLID SECTION line as the
    out-of-plane thickness (1.0 when blank) and *CLOAD as a TOTAL force on
    that thickness. The deck is consistent with the stated part only if
    force per unit thickness matches: F_deck / t_deck = F_stated / t_stated.
    Measured: a unit slice (t = 1) loaded with the full-width force gives
    10x the deflection of the 10 mm beam, with no warning."""
    types = {t for t, _ in deck.elements.values()}
    if not any(t[:3] in ("CPS", "CPE") for t in types):
        return None
    R = "2D THICKNESS"
    if it.thickness is None or it.force is None:
        return CFinding(R, "NOT EVALUATED", detail="needs the stated part "
                        "thickness and the stated total force",
                        provenance="checker.check_thickness")
    td = []
    for k, _o, data in INV._cards(deck.path):
        if k == "SOLID SECTION":
            t = data[0].split(",")[0].strip() if data else ""
            try:
                td.append(float(t) if t else 1.0)
            except ValueError:
                td.append(1.0)
    t_d = td[0] if td else 1.0
    Fd = INV.applied_resultant(deck)
    fd = math.sqrt(sum(v * v for v in Fd))
    fi = math.sqrt(sum(v * v for v in it.force))
    q_d, q_i = fd / t_d, fi / it.thickness
    if abs(q_d / q_i - 1.0) > 0.01:
        return CFinding(R, "FAIL", "SEVERE", "case_agent",
                        "either write the full thickness with the total "
                        "force, or the unit thickness with force per unit "
                        "thickness",
                        f"deck: {fd:.4g} N on thickness {t_d:g} = {q_d:.4g} "
                        f"N/mm; stated: {fi:.4g} N on {it.thickness:g} mm = "
                        f"{q_i:.4g} N/mm (factor {q_d / q_i:.3g})",
                        "checker.check_thickness")
    return CFinding(R, "PASS", detail=f"{q_d:.4g} N per mm of thickness, as "
                    "stated", provenance="checker.check_thickness")


def check_locking_deck(deck: Deck, it: DeckIntent) -> List[CFinding]:
    types = sorted({t for t, _ in deck.elements.values()})
    E, nu, plastic = _material(deck)
    if len(types) != 1 or types[0] not in _ELEMENTS or nu is None:
        return [CFinding("LOCKING R1-R7", "NOT EVALUATED",
                         detail=f"element types {types}, nu {nu}",
                         provenance="checker.check_locking_deck")]
    fam, order, integ = _ELEMENTS[types[0]]
    mode, rl, depth, nthru, note = deck_mode(deck)
    fam3 = {"quad": "hex", "tri": "tet"}.get(fam, fam)
    el = ElementSpec(fam3, order, integ,
                     hourglass_control=(integ == "reduced"),
                     # 1e-6: a slab edge on an element face measures
                     # 1.9999 for 2 layers, which floor() made 1
                     elements_through_thickness=(int(nthru + 1e-6) if nthru
                                                 else None))
    el.thickness_source = "measured" if nthru else "proxy"
    el.measured_thickness = depth
    mat = MaterialSpec(E=E or 1.0, nu=nu, name="deck",
                       plastic_response_expected=(
                           it.material_class == "elastic-plastic" or plastic),
                       yield_stress=it.yield_MPa)
    rep = check_locking(el, mat, LoadCase(mode, source="deck geometry"))
    out = []
    for f in rep.findings:
        sev = f.severity.value
        verdict = ("FAIL" if f.severity in (Severity.MODERATE, Severity.SEVERE,
                                            Severity.INVALID) else
                   "NOT EVALUATED" if f.severity == Severity.BLOCKED
                   else "PASS")
        out.append(CFinding(f"{f.rule_id} {f.mechanism}", verdict, sev,
                            f.owner.value, f.recommended_action, f.reason,
                            "locking_check via checker"))
    # R5 measured: CalculiX C3D8R keeps its built-in hourglass control, and
    # still gave 37x the beam-theory deflection with 1 element through the
    # depth (0.75 measured) and 1.29x with 2. Four or more: 1.06x.
    if integ == "reduced" and order == 1 and mode in ("bending", "mixed") \
            and nthru is not None:
        ok = nthru >= 4.0
        out.append(CFinding(
            "R5 hourglass in bending", "PASS" if ok else "FAIL",
            "" if ok else ("SEVERE" if nthru < 2 else "MODERATE"),
            "meshing agent",
            "use C3D8I or C3D20R, or at least 4 reduced elements through "
            "the depth", f"{nthru:.2f} {types[0]} elements through the "
            f"{depth:.3g} depth in {mode} ({note})",
            "checker.check_locking_deck"))
    if not out:
        out.append(CFinding("LOCKING R1-R7", "PASS",
                            detail=f"{types[0]}, nu {nu}, {mode}; {note}",
                            provenance="locking_check via checker"))
    return out


def check_symmetry(deck: Deck, it: DeckIntent) -> Optional[CFinding]:
    """A plane where only the normal DOF is fixed, on the model boundary, is
    a symmetry plane. A symmetric half model must carry no load component
    across that plane. Grounded supports (a set also fixed in-plane) are
    not symmetry planes."""
    import numpy as np
    cn = INV.constrained_nodes(deck)
    allp = np.array(list(deck.nodes.values()))
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    planes = []
    for axis in range(3):
        for side in (lo[axis], hi[axis]):
            on = [n for n, p in deck.nodes.items()
                  if abs(p[axis] - side) < 1e-6 * max(1.0, hi[axis] - lo[axis])]
            if len(on) < 3:
                continue
            dofs = [cn.get(n, set()) for n in on]
            # every node holds the normal DOF, most hold only that one (an
            # edge shared with a clamp may hold more)
            exact = sum(1 for ds in dofs if ds == {axis + 1})
            if all(axis + 1 in ds for ds in dofs) and exact >= 0.5 * len(on):
                planes.append((axis, side))
    if not planes:
        return None
    R = "SYMMETRY vs LOAD"
    F, _pts = _load_points(deck)
    fm = math.sqrt(sum(v * v for v in F)) or 1.0
    bad = [(a, s) for a, s in planes if abs(F[a]) > 1e-6 * fm]
    if bad:
        a, s = bad[0]
        return CFinding(R, "FAIL", "SEVERE", "case_agent",
                        "model the full part, or split the load into its "
                        "symmetric and antisymmetric parts with matching BCs",
                        f"symmetry plane {'xyz'[a]} = {s:g} but the load has "
                        f"{abs(F[a]) / fm:.0%} of its magnitude across it",
                        "checker.check_symmetry")
    return CFinding(R, "PASS", detail=f"symmetry plane(s) "
                    f"{[('xyz'[a], round(s, 6)) for a, s in planes]}; load "
                    f"has no component across", provenance=
                    "checker.check_symmetry")


def check_yield(deck: Deck, frd: str, it: DeckIntent) -> Optional[CFinding]:
    R = "ELASTIC PAST YIELD"
    _E, _nu, plastic = _material(deck)
    if it.yield_MPa is None:
        return CFinding(R, "NOT EVALUATED", detail="no stated yield stress",
                        provenance="checker.check_yield")
    if plastic or it.material_class == "elastic-plastic":
        return CFinding(R, "NOT NEEDED", detail="plasticity is modelled",
                        provenance="checker.check_yield")
    from frdread import read_frd_stress, von_mises
    try:
        s = max(von_mises(v) for v in read_frd_stress(frd).values())
    except RuntimeError:
        return CFinding(R, "NOT EVALUATED", detail="no STRESS in the .frd",
                        provenance="checker.check_yield")
    if s > it.yield_MPa:
        return CFinding(R, "FAIL", "SEVERE", "model_agent",
                        "add a *PLASTIC curve from a sourced material card "
                        "and rerun nonlinear, or reduce the load",
                        f"max von Mises {s:.4g} MPa exceeds the stated yield "
                        f"{it.yield_MPa:g} MPa in a linear elastic run; the "
                        f"result is invalid past first yield",
                        "checker.check_yield")
    return CFinding(R, "PASS", detail=f"max von Mises {s:.4g} MPa below "
                    f"yield {it.yield_MPa:g}", provenance="checker.check_yield")


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def check_deck(deck_path: str, it: Optional[DeckIntent] = None,
               solve: bool = True, workdir: Optional[str] = None
               ) -> Tuple[List[CFinding], Dict[str, object]]:
    it = it or DeckIntent()
    deck = read_deck(deck_path)
    out: List[CFinding] = []
    for fn in (check_plane, check_thickness, check_symmetry):
        f = fn(deck, it)
        if f:
            out.append(f)
    out += check_locking_deck(deck, it)
    info: Dict[str, object] = {}
    if not solve:
        out.append(_from_inv(INV.check_rigid_modes(deck), "invariants"))
        return out, info
    workdir = workdir or os.path.join(
        os.path.dirname(os.path.abspath(deck_path)), "checker")
    os.makedirs(workdir, exist_ok=True)
    fs = INV.run_checks(deck_path, it, workdir=workdir, verbose=False)
    out += [_from_inv(f, "invariants") for f in fs]
    refused = any(f.rule.startswith("4 ") and f.verdict == "FAIL" for f in fs)
    if not refused:
        frd = os.path.splitext(deck_path)[0] + ".frd"
        if os.path.exists(frd):
            info["frd"] = frd
            f = check_yield(deck, frd, it)
            if f:
                out.append(f)
            if it.fix_set and it.support == "seated":
                out.append(_from_inv(INV.check_support_reactions(
                    deck, frd, it.fix_set), "invariants"))
            elif it.fix_set:
                out.append(CFinding(
                    "8 SUPPORT REACTIONS",
                    "NOT NEEDED" if it.support == "bonded" else
                    "NOT EVALUATED",
                    detail="support stated as bonded: a clamp is the "
                    "physical support" if it.support == "bonded" else
                    "how the real support holds the part is not stated",
                    provenance="checker"))
    return out, info


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    fs, _ = check_deck(sys.argv[1])
    for f in fs:
        print(f.render())
