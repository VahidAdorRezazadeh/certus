#!/usr/bin/env python3
"""
invariants.py - the seven-check plan: checks that need NO reference answer.

Where this sits among the checks:

    locking_check.py   pre-solve. Element formulation against load regime.
    verify_sets.py     pre-solve. Did the node set land on the face you meant.
    results_check.py   post-solve. Compare to a closed form answer.
    invariants.py      pre and post-solve. The seven checks below.

Each check passed seed, solve, verdict (test_checks.py): one seeded defect,
CalculiX exits clean with no warning, the check returns FAIL with an owner
and a lever; and a known-good case returns PASS.

    free, reads the existing result
      1 LOAD vs INTENT        units, resultant, load on constrained DOFs,
                              every part of the load face loaded
      2 SMALL STRAIN          geometrically linear run deforms little
      3 PENETRATION           contact does not interpenetrate
    pre-solve, no extra job
      4 ZERO-ENERGY MODES     rank test: BCs remove every rigid mode
    gated extra solve, only when the declaration is at risk
      5 REVERSIBILITY         declared elastic returns to zero on unload
      6 RATE INDEPENDENCE     time x10 changes nothing
      7 INCREMENT CONVERGENCE halving increments changes nothing

Deleted as identities of any converged solve, do not rebuild: reactions
against the deck's own resultant, zero load, load scaling (the former INV1 to
INV3), contact force balance, non-negative plastic dissipation, incremental
equilibrium under a Newton criterion.

Deliberate design choice: this module reads a WRITTEN DECK and a solver
output. It does not import case_agent. A check that trusts the writer's own
account of what it wrote cannot catch the writer being wrong, and it also
cannot be pointed at a deck somebody else produced.

BLIND SPOT, measured: a load on a geometrically plausible but WRONG face
passes. Moving a cantilever load from the tip to the bottom face changed tip
deflection by 62 percent and every reference-free check passed. Input
fidelity belongs to the face catalogue and the confirmation step.

MEASURED SOLVER BEHAVIOUR, CalculiX 2.21, do not replace with recollection:

  M1. *NODE PRINT, NSET=<constrained set>, TOTALS=ONLY, RF does NOT include
      external loads applied to nodes inside that set. On a pressure-loaded
      cantilever the reported sum was short of the applied resultant by exactly
      the consistent nodal load landing on the clamped face: 1950.000 /
      1975.000 / 1966.667 / 1983.333 N against an applied 2000.000 N, all four
      predicted from the shape functions. A naive check reports a 0.8 to 2.5
      percent imbalance on a correct model, and the size depends on the mesh,
      so no fixed tolerance works. Corrected below by adding the load on
      constrained nodes back.

  M2. The .frd format stores displacements as E12.5, about six significant
      digits, so the relative resolution floor is about 1e-5. INV3 ran with
      rtol 1e-6 and reported FAIL on a verified-correct model at a measured
      4.86e-06 deviation. The tolerance is set by the OUTPUT PRECISION and not
      by solver accuracy. Do not tighten it below 1e-5.

  M3. *CLOAD given an NSET NAME applies that force to EVERY node in the set,
      not distributed over it. A request for 100.0 N on a 9-node set produced
      900.0 N of reaction, converged, no warning. This module therefore reads
      per-node *CLOAD lines and sums them, and refuses to evaluate INV1 if the
      deck applies a load by set name.

  M4. *NODE PRINT writes ONE reaction block PER INCREMENT. read_total_force
      returns the LAST block. A deck with *STATIC 0.25, 1.0 wrote four blocks,
      and returning the first reported a 66 percent equilibrium error on a
      model whose actual defect was elsewhere.

  M5. The equilibrium reference scale is the MAGNITUDE OF THE LOAD RESULTANT,
      not the per-component value. Components of the applied load are often
      exactly zero, and dividing a 1e-9 N numerical residual by a
      per-component floor turns rounding noise into a double-digit percentage.
      Measured on a verified-correct model: 6.3 percent reported in x and 16.6
      percent in y, while z was correct to 1.6e-07.

  M6. The .frd DISP block layout is NOT the same across CalculiX builds. A
      fixed-width reader at columns [3:13][13:25][25:37][37:49] read
      0.315876 mm on Linux ccx 2.21 and 9.998880 mm on a Windows build, from
      the same deck, a factor of 31.6, while the .dat reaction read was
      correct in both. Displacements are therefore extracted by pattern, not
      by column. Run frd_probe.py on any new solver build before trusting a
      displacement.

Usage:
    python invariants.py runs/<run>/case_calculix/case.inp
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
import os
import re
import shutil
import subprocess
import sys

CCX = os.environ.get("CCX", "ccx")

ELEMENT_NODES = {
    "C3D4": 4, "C3D10": 10, "C3D6": 6, "C3D15": 15,
    "C3D8": 8, "C3D8I": 8, "C3D8R": 8, "C3D20": 20, "C3D20R": 20,
}
NON_SOLID = {"CPS3", "CPS4", "CPS6", "CPS8", "CPE3", "CPE4", "CPE6", "CPE8",
             "S3", "S4", "S6", "S8", "B31", "B32", "T3D2"}


# ---------------------------------------------------------------------------
# Deck reader
# ---------------------------------------------------------------------------

@dataclass
class Deck:
    path: str
    nodes: Dict[int, Tuple[float, float, float]] = field(default_factory=dict)
    elements: Dict[int, Tuple[str, Tuple[int, ...]]] = field(default_factory=dict)
    elsets: "OrderedDict[str, List[int]]" = field(default_factory=OrderedDict)
    nsets: "OrderedDict[str, List[int]]" = field(default_factory=OrderedDict)
    keywords: List[str] = field(default_factory=list)
    # (node, dof, value) for every explicit per-node *CLOAD line
    cloads: List[Tuple[int, int, float]] = field(default_factory=list)
    # *CLOAD lines that used a set NAME instead of a node id
    cloads_by_set: List[Tuple[str, int, float]] = field(default_factory=list)
    # (set-or-node, dof_first, dof_last) from *BOUNDARY
    boundaries: List[Tuple[str, int, int]] = field(default_factory=list)
    has_dload: bool = False

    @property
    def element_types(self) -> set:
        return {t for t, _ in self.elements.values()}

    def resolve_nodes(self, token: str) -> List[int]:
        t = token.strip().upper()
        if t in self.nsets:
            return sorted(set(self.nsets[t]))
        try:
            return [int(float(token))]
        except ValueError:
            return []


def _kw(line: str) -> Tuple[str, Dict[str, str]]:
    parts = [p.strip() for p in line.split(",")]
    name = parts[0].lstrip("*").strip().upper()
    opts: Dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            opts[k.strip().upper()] = v.strip()
        elif p:
            opts[p.upper()] = ""
    return name, opts


def read_deck(path: str) -> Deck:
    """Parse a deck without trusting whatever produced it."""
    d = Deck(path=path)
    mode, opts = None, {}
    pend_id: Optional[int] = None
    pend_conn: List[int] = []

    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read().replace("\r\n", "\n").splitlines()

    for line in raw:
        s = line.strip()
        if not s or s.startswith("**"):
            continue
        if s.startswith("*"):
            if pend_id is not None:
                d.elements[pend_id] = (opts.get("TYPE", "?").upper(),
                                       tuple(pend_conn))
                pend_id, pend_conn = None, []
            mode, opts = _kw(s)
            d.keywords.append(mode)
            if mode in ("DLOAD", "DSLOAD"):
                d.has_dload = True
            if mode == "ELSET":
                d.elsets.setdefault(opts.get("ELSET", "").upper(), [])
            if mode == "NSET":
                d.nsets.setdefault(opts.get("NSET", "").upper(), [])
            if mode == "ELEMENT" and "ELSET" in opts:
                d.elsets.setdefault(opts["ELSET"].upper(), [])
            if mode == "NODE" and "NSET" in opts:
                d.nsets.setdefault(opts["NSET"].upper(), [])
            continue

        toks = [t.strip() for t in s.split(",")]
        cont = toks[-1] == ""
        toks = [t for t in toks if t != ""]
        if not toks:
            continue

        if mode == "NODE" and len(toks) >= 4:
            n = int(float(toks[0]))
            d.nodes[n] = tuple(float(v) for v in toks[1:4])
            if "NSET" in opts:
                d.nsets[opts["NSET"].upper()].append(n)

        elif mode == "ELEMENT":
            want = ELEMENT_NODES.get(opts.get("TYPE", "?").upper())
            if pend_id is None:
                pend_id = int(toks[0])
                pend_conn = [int(v) for v in toks[1:]]
            else:
                pend_conn += [int(v) for v in toks]
            if (want is not None and len(pend_conn) >= want) or \
               (want is None and not cont):
                d.elements[pend_id] = (opts.get("TYPE", "?").upper(),
                                       tuple(pend_conn))
                if "ELSET" in opts:
                    d.elsets[opts["ELSET"].upper()].append(pend_id)
                pend_id, pend_conn = None, []

        elif mode in ("ELSET", "NSET") and "GENERATE" in opts:
            # first, last[, increment]. Without this, a GENERATE line is read
            # as three member ids and the set silently holds the wrong nodes.
            name = opts.get(mode, "").upper()
            a = [int(float(t)) for t in toks[:3]]
            first, last = a[0], a[1] if len(a) > 1 else a[0]
            inc = a[2] if len(a) > 2 and a[2] != 0 else 1
            target = d.elsets if mode == "ELSET" else d.nsets
            target[name] += list(range(first, last + 1, inc))

        elif mode == "ELSET":
            name = opts.get("ELSET", "").upper()
            for t in toks:
                if t.isdigit():
                    d.elsets[name].append(int(t))
                elif t.upper() in d.elsets:
                    d.elsets[name] += d.elsets[t.upper()]

        elif mode == "NSET":
            name = opts.get("NSET", "").upper()
            for t in toks:
                if t.isdigit():
                    d.nsets[name].append(int(t))
                elif t.upper() in d.nsets:
                    d.nsets[name] += d.nsets[t.upper()]

        elif mode == "CLOAD" and len(toks) >= 3:
            try:
                nid = int(toks[0])
                d.cloads.append((nid, int(toks[1]), float(toks[2])))
            except ValueError:
                d.cloads_by_set.append((toks[0].upper(), int(toks[1]),
                                        float(toks[2])))

        elif mode == "BOUNDARY" and len(toks) >= 2:
            tgt = toks[0]
            rest = [t.upper() for t in toks[1:]]
            if rest[0] in ("ENCASTRE", "PINNED", "FIXED"):
                lo, hi = (1, 3) if rest[0] != "PINNED" else (1, 3)
            else:
                try:
                    lo = int(rest[0])
                    hi = int(rest[1]) if len(rest) > 1 and \
                        rest[1].lstrip("-").isdigit() else lo
                except ValueError:
                    continue
            d.boundaries.append((tgt.upper(), lo, hi))

    if pend_id is not None:
        d.elements[pend_id] = (opts.get("TYPE", "?").upper(), tuple(pend_conn))
    return d


# ---------------------------------------------------------------------------
# Derived quantities
# ---------------------------------------------------------------------------

def applied_resultant(deck: Deck) -> Tuple[float, float, float]:
    r = [0.0, 0.0, 0.0]
    for _, dof, val in deck.cloads:
        if 1 <= dof <= 3:
            r[dof - 1] += val
    return tuple(r)


def constrained_nodes(deck: Deck) -> Dict[int, set]:
    """node id -> set of constrained dofs."""
    out: Dict[int, set] = {}
    for tgt, lo, hi in deck.boundaries:
        for n in deck.resolve_nodes(tgt):
            out.setdefault(n, set()).update(range(lo, hi + 1))
    return out


def load_on_constrained(deck: Deck) -> Tuple[float, float, float]:
    """Applied load landing on constrained nodes. See M1."""
    cn = constrained_nodes(deck)
    r = [0.0, 0.0, 0.0]
    for nid, dof, val in deck.cloads:
        if 1 <= dof <= 3 and dof in cn.get(nid, ()):
            r[dof - 1] += val
    return tuple(r)


# ---------------------------------------------------------------------------
# Deck variants and solving
# ---------------------------------------------------------------------------

_RF_BLOCK = "*NSET, NSET=NCERTUS_BC\n{ids}\n"


def make_variant(deck: Deck, out_path: str, load_scale: float = 1.0,
                 add_rf_print: bool = True) -> Tuple[str, int]:
    """Write a copy of the deck with every *CLOAD value scaled.

    load_scale = 0.0 gives the zero-load model for INV2.
    load_scale = 2.0 gives the doubled model for INV3.
    """
    with open(deck.path, encoding="utf-8", errors="replace") as f:
        text = f.read().replace("\r\n", "\n")
    lines = text.split("\n")
    out: List[str] = []
    mode = None
    n_mod = 0
    for line in lines:
        s = line.strip()
        if s.startswith("*") and not s.startswith("**"):
            mode = _kw(s)[0]
            out.append(line)
            continue
        if mode in ("CLOAD", "DLOAD", "DSLOAD") and s \
                and not s.startswith("**"):
            toks = [t.strip() for t in s.split(",") if t.strip()]
            # *CLOAD  : node, dof, value
            # *DLOAD  : element or elset, face label (e.g. P3), magnitude
            # *DLOAD GRAV: elset, GRAV, g, nx, ny, nz
            # *DLOAD CENTRIF: elset, CENTRIF, omega^2, point, axis
            # Only the magnitude (field 3) scales. Every field after it is
            # kept: dropping the direction turns GRAV into a malformed card.
            if len(toks) >= 3:
                try:
                    mag = float(toks[2]) * load_scale
                    out.append(", ".join(toks[:2] + [f"{mag:.10g}"]
                                         + toks[3:]))
                    n_mod += 1
                    continue
                except ValueError:
                    pass
        out.append(line)

    text = "\n".join(out)

    if add_rf_print:
        cn = sorted(constrained_nodes(deck))
        chunks = [", ".join(str(v) for v in cn[i:i + 8])
                  for i in range(0, len(cn), 8)]
        block = ("*NSET, NSET=NCERTUS_BC\n" + "\n".join(chunks) + "\n")
        # the set must be defined before *STEP
        idx = text.find("*STEP")
        if idx >= 0:
            text = text[:idx] + block + text[idx:]
        pr = "*NODE PRINT, NSET=NCERTUS_BC, TOTALS=ONLY\nRF\n"
        idx = text.find("*END STEP")
        if idx >= 0:
            text = text[:idx] + pr + text[idx:]

    with open(out_path, "w") as f:
        f.write(text)
    return out_path, n_mod


def solve(deck_path: str, timeout: int = 7200) -> Dict[str, object]:
    workdir = os.path.dirname(os.path.abspath(deck_path)) or "."
    job = os.path.splitext(os.path.basename(deck_path))[0]
    r = subprocess.run([CCX, job], cwd=workdir, capture_output=True,
                       text=True, timeout=timeout)
    dat = os.path.join(workdir, job + ".dat")
    frd = os.path.join(workdir, job + ".frd")
    # A non-empty .frd is NOT convergence: a run that stops with "increment
    # size smaller than minimum" still writes partial results. Measured.
    outcome = ccx_outcome(r.stdout + r.stderr, r.returncode,
                          os.path.join(workdir, job), deck_path)
    return {
        "job": job,
        "converged": outcome.converged,
        "outcome": outcome.reason,
        "rf_total": read_total_force(dat, "NCERTUS_BC")
        if os.path.exists(dat) else None,
        "frd": frd if os.path.exists(frd) else None,
        "stdout": r.stdout,
    }


def read_total_force(path: str, set_name: Optional[str] = None
                     ) -> Optional[Tuple[float, float, float]]:
    """Total reaction force from a .dat file, for ONE named node set.

    set_name selects the block by the set named in its header. Without it the
    reader takes whatever total-force block comes last, which is the wrong
    set as soon as a deck prints reactions for more than one set. With it,
    a missing set returns None instead of another set's numbers.

    Returns the LAST block, not the first. A multi-increment run writes one
    block per increment, and the first one is at a fraction of the load.
    Measured: a deck with *STATIC 0.25, 1.0 wrote four blocks, and returning
    the first produced a 75 percent equilibrium error reported as a physics
    finding on a model whose only defect was elsewhere.
    """
    mode, last = None, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            low = ln.lower()
            if "for set" in low and "time" in low:
                mode = None
                if "total force" in low:
                    name = low.split("for set", 1)[1].split("and time")[0]
                    if set_name is None or \
                            name.strip() == set_name.strip().lower():
                        mode = "rf"
                continue
            if mode == "rf" and ln.strip():
                try:
                    v = [float(t) for t in ln.split()]
                except ValueError:
                    mode = None
                    continue
                if len(v) >= 3:
                    last = tuple(v[-3:])
                    mode = None
    return last


from frdread import read_frd_disp  # width-detecting .frd reader, shared so there is one copy
from results_check import ccx_outcome  # one convergence verdict for every caller


# ---------------------------------------------------------------------------
# Stated intent: what the engineer said, independent of the deck
# ---------------------------------------------------------------------------

@dataclass
class Intent:
    """What the engineer stated. Every check compares the deck against this
    or against physics; none trusts the writer's account of the deck."""
    force: Optional[Tuple[float, float, float]] = None   # total, deck force unit
    units: Optional[str] = None            # "N-mm-MPa" | "N-m-Pa"
    E_GPa: Optional[float] = None          # stated Young's modulus
    material_class: Optional[str] = None   # "elastic" | "elastic-plastic" | ...
    rate_dependent: Optional[bool] = None
    nlgeom: Optional[bool] = None
    load_set: str = "LOAD_FACE"
    contact_tol: Optional[float] = None    # allowed penetration, length unit


_E_SCALE = {"N-mm-MPa": 1e3, "N-m-Pa": 1e9}          # GPa -> deck units
_PATH_DEPENDENT = ("PLASTIC", "CYCLIC HARDENING", "CREEP", "DAMAGE")
_RATE_CARDS = ("CREEP", "VISCOELASTIC")
_RANK_BLIND = ("EQUATION", "MPC", "SPRING", "DASHPOT", "COUPLING",
               "RIGID BODY", "CONTACT PAIR", "TIE", "GAP")


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    rule: str
    verdict: str            # PASS | FAIL | NOT EVALUATED | NOT NEEDED
    detail: str
    owner: str = ""
    cure: str = ""          # the lever: what to change

    def render(self) -> str:
        out = [f"{self.rule:26s} {self.verdict}", f"  {self.detail}"]
        if self.verdict == "FAIL" and self.cure:
            out.append(f"  owner: {self.owner}")
            out.append(f"  lever: {self.cure}")
        return "\n".join(out)


def _cards(path: str) -> List[Tuple[str, Dict[str, str], List[str]]]:
    """(keyword, options, data lines) for every card, comments dropped."""
    out: List[Tuple[str, Dict[str, str], List[str]]] = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f.read().replace("\r\n", "\n").splitlines():
            s = ln.strip()
            if not s or s.startswith("**"):
                continue
            if s.startswith("*"):
                k, o = _kw(s)
                out.append((k, o, []))
            elif out:
                out[-1][2].append(s)
    return out


def _has(cards, names) -> List[str]:
    return sorted({k for k, _, _ in cards if k in names})


def _nlgeom(cards) -> bool:
    return any(k == "STEP" and ("NLGEOM" in o and o["NLGEOM"].upper()
                                not in ("NO",)) for k, o, _ in cards)


def _components(deck: Deck) -> List[set]:
    """Connected bodies: node sets joined by shared elements."""
    parent: Dict[int, int] = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for _t, conn in deck.elements.values():
        if conn:
            r = find(conn[0])
            for n in conn[1:]:
                parent[find(n)] = r
    groups: Dict[int, set] = {}
    for n in parent:
        groups.setdefault(find(n), set()).add(n)
    return list(groups.values())


def dsload_faces(deck: Deck) -> Tuple[List[Tuple[float, List[int], List[float]]], List[str]]:
    """Pressure faces from *DSLOAD / *DLOAD Pn on element faces:
    [(pressure, corner node ids, outward area vector)], plus the load types
    that could not be resolved. Outward means away from the element's own
    centroid, so a wrong face label shows up as a wrong direction."""
    cards = _cards(deck.path)
    surf = {o.get("NAME", "").upper(): d for k, o, d in cards
            if k == "SURFACE" and o.get("TYPE", "ELEMENT").upper() == "ELEMENT"}
    out, skipped = [], []

    def add(el_ids, label, p):
        idx_map = _FACES.get(label.upper())
        for e in el_ids:
            etype, conn = deck.elements[e]
            idx = idx_map.get(len(conn)) if idx_map else None
            if not idx:
                skipped.append(f"{etype} {label}")
                continue
            ids = [conn[i] for i in idx]
            P = [deck.nodes[i] for i in ids]
            fc = [sum(p[k] for p in P) / len(P) for k in range(3)]
            ec = [sum(deck.nodes[i][k] for i in conn) / len(conn)
                  for k in range(3)]
            A = [0.0, 0.0, 0.0]
            for a, b in zip(P, P[1:] + P[:1]):
                A[0] += 0.5 * (a[1] * b[2] - a[2] * b[1])
                A[1] += 0.5 * (a[2] * b[0] - a[0] * b[2])
                A[2] += 0.5 * (a[0] * b[1] - a[1] * b[0])
            if sum((fc[k] - ec[k]) * A[k] for k in range(3)) < 0:
                A = [-v for v in A]
            out.append((p, ids, A))
    for k, _o, data in cards:
        if k not in ("DSLOAD", "DLOAD"):
            continue
        for ln in data:
            t = [x.strip() for x in ln.split(",")]
            if len(t) < 3:
                continue
            lab = t[1].upper()
            try:
                p = float(t[2])
            except ValueError:
                continue
            if k == "DSLOAD" and lab == "P":
                for sl in surf.get(t[0].upper(), []):
                    st = [x.strip() for x in sl.split(",")]
                    els = deck.elsets.get(st[0].upper()) or \
                        ([int(st[0])] if st[0].isdigit() else [])
                    add(els, st[1], p)
            elif k == "DLOAD" and lab.startswith("P") and lab[1:].isdigit():
                els = deck.elsets.get(t[0].upper()) or \
                    ([int(t[0])] if t[0].isdigit() else [])
                add(els, "S" + lab[1:], p)
            else:
                skipped.append(f"{k} {lab}")
    return out, skipped


def pressure_resultant_deck(deck: Deck) -> Tuple[Tuple[float, float, float], List[str]]:
    faces, skipped = dsload_faces(deck)
    F = [0.0, 0.0, 0.0]
    for p, _ids, A in faces:
        for k in range(3):
            F[k] -= p * A[k]
    return tuple(F), skipped


# ---- 1. load against stated intent (free) ---------------------------------

def check_load_intent(deck: Deck, intent: Intent) -> Finding:
    """The load in the deck, in the deck's units, is the load the engineer
    stated, on the nodes they meant, and not on constrained nodes."""
    R = "1 LOAD vs INTENT"
    if intent.force is None or intent.units not in _E_SCALE:
        return Finding(R, "NOT EVALUATED", "no stated force or unit system")
    problems = []
    cards = _cards(deck.path)
    # units: the modulus must match the stated one in the stated units
    if intent.E_GPa:
        want = intent.E_GPa * _E_SCALE[intent.units]
        for k, _o, data in cards:
            if k == "ELASTIC" and data:
                try:
                    E = float(data[0].split(",")[0])
                except ValueError:
                    continue
                if abs(E / want - 1.0) > 0.05:
                    problems.append(
                        f"*ELASTIC E = {E:g}, but {intent.E_GPa:g} GPa in "
                        f"{intent.units} is {want:g} (factor {want / E:.3g})")
    # resultant; a load by set name is applied to EVERY node (M3)
    per_set = []
    for name, dof, val in deck.cloads_by_set:
        per_set.append((len(deck.resolve_nodes(name)), dof, val))
    applied = list(applied_resultant(deck))
    for n, dof, val in per_set:
        if 1 <= dof <= 3:
            applied[dof - 1] += n * val
    pF, skipped = pressure_resultant_deck(deck)
    if skipped:
        return Finding(R, "NOT EVALUATED", f"cannot integrate "
                       f"{', '.join(sorted(set(skipped)))} yet")
    applied = [applied[k] + pF[k] for k in range(3)]
    fm = max(sum(v * v for v in intent.force) ** 0.5, 1e-30)
    err = sum((applied[i] - intent.force[i]) ** 2 for i in range(3)) ** 0.5
    # a pressure resultant from CAD and from facets differs by the facet
    # approximation of curved faces; 1e-3 of the load is kept for forces
    rtol = 1e-2 if deck.has_dload else 1e-3
    if err > rtol * fm:
        problems.append(f"deck resultant ({applied[0]:.4g}, {applied[1]:.4g},"
                        f" {applied[2]:.4g}) is not the stated "
                        f"{tuple(intent.force)}")
    # loads on constrained dofs are reacted directly and never reach the part
    cn = constrained_nodes(deck)
    shared = [(n, d) for n, d, v in deck.cloads if d in cn.get(n, ()) and v]
    if shared:
        problems.append(f"{len(shared)} load line(s) act on constrained "
                        f"DOFs, e.g. node {shared[0][0]} dof {shared[0][1]}")
    # every loaded node on the declared load set, every part of it loaded
    lset = set(deck.nsets.get(intent.load_set.upper(), []))
    if lset and deck.cloads:
        off = [n for n, _d, _v in deck.cloads if n not in lset]
        if off:
            problems.append(f"{len(off)} loaded node(s) are not on "
                            f"{intent.load_set}")
        sub = [c & lset for c in _face_patches(deck, lset)]
        fn = {n: 0.0 for n in lset}
        for n, d, v in deck.cloads:
            if n in fn:
                fn[n] += v * (intent.force[d - 1] / fm if 1 <= d <= 3 else 0)
        if len(sub) > 1:
            tot = sum(fn.values()) or 1e-30
            nn = sum(len(p) for p in sub)
            for p in sub:
                share = sum(fn[n] for n in p) / tot
                geo = len(p) / nn
                if share < 0.1 * geo:
                    c = [sum(deck.nodes[n][k] for n in p) / len(p)
                         for k in range(3)]
                    problems.append(
                        f"the part of {intent.load_set} near ({c[0]:.1f}, "
                        f"{c[1]:.1f}, {c[2]:.1f}) carries {share:.0%} of the "
                        f"load against {geo:.0%} of the face")
    if problems:
        return Finding(R, "FAIL", "; ".join(problems), owner="case_agent",
                       cure="rewrite the load and material cards from the "
                            "stated intent and the deck's unit system")
    return Finding(R, "PASS", f"resultant {tuple(round(v, 6) for v in applied)}"
                   f" matches the stated load; units consistent; load on "
                   f"{intent.load_set} only and on every part of it")


def _face_patches(deck: Deck, nset: set) -> List[set]:
    """Separate patches of a surface node set: nodes joined when they share
    an element. Two lug holes are two patches; one hole is one."""
    parent = {n: n for n in nset}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for _t, conn in deck.elements.values():
        on = [n for n in conn if n in parent]
        for n in on[1:]:
            parent[find(n)] = find(on[0])
    g: Dict[int, set] = {}
    for n in nset:
        g.setdefault(find(n), set()).add(n)
    return list(g.values())


# ---- 2. small-strain validity (free) --------------------------------------

def check_small_strain(deck: Deck, disp: Dict[int, Tuple[float, float, float]],
                       intent: Intent, limit: float = 0.01) -> Finding:
    """A run declared geometrically linear must deform little: max |U| below
    1 percent of the model size. Abstains when NLGEOM is on."""
    R = "2 SMALL STRAIN"
    cards = _cards(deck.path)
    if _nlgeom(cards) or intent.nlgeom:
        return Finding(R, "NOT EVALUATED", "NLGEOM is on; nothing to check")
    xs = list(deck.nodes.values())
    L = max(max(p[k] for p in xs) - min(p[k] for p in xs) for k in range(3))
    umax = max(sum(c * c for c in u) ** 0.5 for u in disp.values())
    r = umax / L if L else 0.0
    if r > limit:
        return Finding(R, "FAIL", f"max |U| {umax:.4g} is {r:.1%} of the model "
                       f"size {L:.4g} in a geometrically LINEAR run",
                       owner="model_agent",
                       cure="turn NLGEOM on, or confirm the load magnitude")
    return Finding(R, "PASS", f"max |U| is {r:.2e} of the model size")


# ---- 3. penetration (free) ------------------------------------------------

def check_penetration(deck: Deck, disp, intent: Intent) -> Finding:
    """Contacting surfaces do not interpenetrate past the tolerance. Master
    surface approximated locally by a plane through its 4 nearest deformed
    nodes, oriented away from the master body."""
    R = "3 PENETRATION"
    cards = _cards(deck.path)
    pairs = [d for k, _o, d in cards if k == "CONTACT PAIR"]
    if not pairs:
        return Finding(R, "NOT NEEDED", "no contact pair in the deck")
    surf: Dict[str, Tuple[str, List[str]]] = {}
    for k, o, d in cards:
        if k == "SURFACE":
            surf[o.get("NAME", "").upper()] = (o.get("TYPE", "ELEMENT").upper(),
                                               d)

    def surf_nodes(name):
        typ, data = surf.get(name.upper(), ("", []))
        out = set()
        for ln in data:
            t = [x.strip() for x in ln.split(",") if x.strip()]
            if typ == "NODE":
                out |= set(deck.resolve_nodes(t[0]))
            elif len(t) >= 2:
                face = _FACES.get(t[1].upper())
                els = deck.elsets.get(t[0].upper()) or \
                    ([int(t[0])] if t[0].isdigit() else [])
                for e in els:
                    etype, conn = deck.elements[e]
                    idx = face.get(len(conn)) if face else None
                    out |= {conn[i] for i in idx} if idx else set(conn)
        return out

    def pos(n):
        u = disp.get(n, (0.0, 0.0, 0.0))
        return [deck.nodes[n][k] + u[k] for k in range(3)]
    worst, h = 0.0, 1.0
    for data in pairs:
        for ln in data:
            s_name, m_name = [x.strip() for x in ln.split(",")[:2]]
            S, M = surf_nodes(s_name), surf_nodes(m_name)
            if not S or not M:
                return Finding(R, "NOT EVALUATED",
                               f"could not resolve surfaces {s_name}/{m_name}")
            body = next((c for c in _components(deck) if M & c), set())
            bc = [sum(deck.nodes[n][k] for n in body) / len(body)
                  for k in range(3)]
            Mp = {n: pos(n) for n in M}
            ml = list(Mp.values())
            h = _mean_spacing(ml)
            for n in S:
                p = pos(n)
                near = sorted(Mp.values(), key=lambda q: sum(
                    (q[k] - p[k]) ** 2 for k in range(3)))[:4]
                c, nrm = _plane(near)
                if sum((c[k] - bc[k]) * nrm[k] for k in range(3)) < 0:
                    nrm = [-v for v in nrm]
                d = sum((p[k] - c[k]) * nrm[k] for k in range(3))
                worst = max(worst, -d)
    tol = intent.contact_tol if intent.contact_tol else 1e-3 * h
    if worst > tol:
        return Finding(R, "FAIL", f"max penetration {worst:.3g} exceeds the "
                       f"tolerance {tol:.3g} "
                       f"({'stated' if intent.contact_tol else '0.1% of the master node spacing'})",
                       owner="model_agent",
                       cure="raise the penalty stiffness or switch to a "
                            "Lagrange contact formulation")
    return Finding(R, "PASS", f"max penetration {worst:.3g} within {tol:.3g}")


_FACES = {  # CalculiX face label -> corner node indices, by element size
    "S1": {8: (0, 1, 2, 3), 20: (0, 1, 2, 3), 4: (0, 1, 2), 10: (0, 1, 2)},
    "S2": {8: (4, 5, 6, 7), 20: (4, 5, 6, 7), 4: (0, 1, 3), 10: (0, 1, 3)},
    "S3": {8: (0, 1, 5, 4), 20: (0, 1, 5, 4), 4: (1, 2, 3), 10: (1, 2, 3)},
    "S4": {8: (1, 2, 6, 5), 20: (1, 2, 6, 5), 4: (0, 2, 3), 10: (0, 2, 3)},
    "S5": {8: (2, 3, 7, 6), 20: (2, 3, 7, 6)},
    "S6": {8: (3, 0, 4, 7), 20: (3, 0, 4, 7)},
}


def _mean_spacing(pts) -> float:
    if len(pts) < 2:
        return 1.0
    s = 0.0
    for p in pts[:50]:
        s += min(sum((p[k] - q[k]) ** 2 for k in range(3)) ** 0.5
                 for q in pts if q is not p)
    return s / min(len(pts), 50)


def _plane(pts):
    import numpy as np
    a = np.array(pts, dtype=float)
    c = a.mean(axis=0)
    _u, _s, vt = np.linalg.svd(a - c)
    return list(c), list(vt[-1])


# ---- 4. zero-energy modes: rank test on the BCs (pre-solve) ---------------

_MODES = ("Tx", "Ty", "Tz", "Rx", "Ry", "Rz")


def check_rigid_modes(deck: Deck) -> Finding:
    """Every rigid-body mode of every connected body is removed by the BCs.

    Rank of the constrained-DOF rows of the 6 rigid modes, per body.
    CalculiX does not refuse a singular model (post-A F1: a roller solved
    with exit 0 and a plausible answer), so this runs before the solve.
    """
    R = "4 ZERO-ENERGY MODES"
    import numpy as np
    blind = _has(_cards(deck.path), _RANK_BLIND)
    if blind:
        return Finding(R, "NOT EVALUATED", f"deck uses {', '.join(blind)}, "
                       f"which the BC rank test does not model")
    cn = constrained_nodes(deck)
    bodies = _components(deck)
    msgs = []
    for body in bodies:
        pts = [deck.nodes[n] for n in body]
        c = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
        rows = []
        for n in body:
            for d in cn.get(n, ()):
                if not 1 <= d <= 3:
                    continue
                x, y, z = (deck.nodes[n][k] - c[k] for k in range(3))
                # displacement of dof d under the 6 unit rigid modes
                r = {1: [1, 0, 0, 0, z, -y], 2: [0, 1, 0, -z, 0, x],
                     3: [0, 0, 1, y, -x, 0]}[d]
                rows.append(r)
        A = np.array(rows, dtype=float) if rows else np.zeros((1, 6))
        scale = max(1.0, float(np.abs(A).max()))
        _u, s, vt = np.linalg.svd(A / scale, full_matrices=True)
        rank = int((s > 1e-9 * max(1.0, s.max() if s.size else 1)).sum())
        if rank < 6:
            free = []
            for v in vt[rank:]:
                v = v / np.abs(v).max()
                free.append(" + ".join(f"{v[i]:.2g}{_MODES[i]}"
                                       for i in range(6) if abs(v[i]) > 1e-6))
            msgs.append(f"body of {len(body)} nodes keeps {6 - rank} free "
                        f"rigid mode(s): " + "; ".join(free))
    if msgs:
        return Finding(R, "FAIL", " | ".join(msgs), owner="case_agent",
                       cure="constrain the listed modes, e.g. fix the "
                            "in-plane DOFs on the support face")
    return Finding(R, "PASS", f"all 6 rigid modes removed on "
                   f"{len(bodies)} body(ies)")


# ---- 5-7: gated extra solves ---------------------------------------------

def _qoi(deck: Deck, disp) -> float:
    """Load-point displacement sum(F.u)/|F|, or max |U| without loads.
    Pressure faces count with their force shared equally by their corners."""
    F = [0.0, 0.0, 0.0]
    w = 0.0
    for n, d, v in deck.cloads:
        if 1 <= d <= 3 and n in disp:
            F[d - 1] += v
            w += v * disp[n][d - 1]
    if deck.has_dload:
        for p, ids, A in dsload_faces(deck)[0]:
            for k in range(3):
                fk = -p * A[k]
                F[k] += fk
                w += sum(fk / len(ids) * disp.get(i, (0, 0, 0))[k]
                         for i in ids)
    fm = sum(x * x for x in F) ** 0.5
    if fm > 0:
        return w / fm
    return max(sum(c * c for c in u) ** 0.5 for u in disp.values())


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)
    return path


def check_reversibility(deck: Deck, intent: Intent, workdir: str) -> Finding:
    """A run declared elastic returns to zero on unloading. One unload step
    appended; runs only when a path-dependent card is present."""
    R = "5 REVERSIBILITY"
    cards = _cards(deck.path)
    pd = _has(cards, _PATH_DEPENDENT)
    if intent.material_class != "elastic":
        return Finding(R, "NOT EVALUATED",
                       f"material class declared as "
                       f"'{intent.material_class}', not elastic")
    if not pd:
        return Finding(R, "NOT NEEDED", "declared elastic and the deck has "
                       "no path-dependent card, so unloading returns to zero "
                       "by construction")
    text = open(deck.path, encoding="utf-8", errors="replace").read()
    step = next((f"*STEP{', NLGEOM' if _nlgeom(cards) else ''}, INC=1000"
                 for _ in [0]))
    unload = (f"{step}\n*STATIC\n0.1, 1., 1e-5, 0.1\n*CLOAD, OP=NEW\n*DLOAD, OP=NEW\n"
              f"*NODE FILE\nU\n*END STEP\n")
    p = _write(os.path.join(workdir, "chk_unload.inp"),
               text.rstrip() + "\n" + unload)
    r = solve(p)
    if not r["converged"]:
        return Finding(R, "NOT EVALUATED", f"unload run: {r['outcome']}")
    blocks = __import__("frdread").read_frd_disp_blocks(r["frd"])
    s1 = [b for b in blocks if b["step"] == 1]
    loaded = max(sum(c * c for c in u) ** 0.5 for u in s1[-1]["disp"].values())
    resid = max(sum(c * c for c in u) ** 0.5
                for u in blocks[-1]["disp"].values())
    rel = resid / loaded if loaded else 0.0
    if rel > 1e-3:
        return Finding(R, "FAIL", f"after unloading, {rel:.1%} of the peak "
                       f"displacement remains ({resid:.4g}); cards present: "
                       f"{', '.join(pd)}", owner="model_agent",
                       cure="remove the path-dependent card, or declare the "
                            "material elastic-plastic and read the result as "
                            "such")
    return Finding(R, "PASS", f"residual after unloading {rel:.1e} of peak "
                   f"({', '.join(pd)} present but not activated)")


def _scale_time(text: str, f: float) -> str:
    """Multiply every step's time data (*STATIC / *VISCO) by f."""
    out, want = [], False
    for ln in text.split("\n"):
        s = ln.strip()
        if s.startswith("*") and not s.startswith("**"):
            want = _kw(s)[0] in ("STATIC", "VISCO")
            out.append(ln)
            continue
        if want and s:
            t = [x.strip() for x in s.split(",")]
            try:
                t[:2] = [f"{float(v) * f:.6g}" for v in t[:2]]
                if len(t) > 3 and t[3]:
                    t[3] = f"{float(t[3]) * f:.6g}"
                if len(t) > 2 and t[2]:
                    t[2] = f"{float(t[2]) * f:.6g}"
            except ValueError:
                pass
            out.append(", ".join(t))
            want = False
            continue
        out.append(ln)
    return "\n".join(out)


def check_rate(deck: Deck, intent: Intent, workdir: str, base_disp,
               rtol: float = 0.01) -> Finding:
    """Scaling step time by ten changes nothing in a run declared
    rate-independent. Runs only when a rate-dependent card is present."""
    R = "6 RATE INDEPENDENCE"
    cards = _cards(deck.path)
    rc = _has(cards, _RATE_CARDS)
    if intent.rate_dependent:
        return Finding(R, "NOT EVALUATED", "run declared rate-dependent")
    if intent.rate_dependent is None:
        return Finding(R, "NOT EVALUATED", "rate dependence not declared")
    if not rc:
        return Finding(R, "NOT NEEDED", "no rate-dependent card in the deck")
    text = open(deck.path, encoding="utf-8", errors="replace").read()
    p = _write(os.path.join(workdir, "chk_time10.inp"), _scale_time(text, 10.0))
    r = solve(p)
    if not r["converged"]:
        return Finding(R, "NOT EVALUATED", f"time x10 run: {r['outcome']}")
    q0 = _qoi(deck, base_disp)
    q1 = _qoi(deck, read_frd_disp(r["frd"]))
    rel = abs(q1 - q0) / max(abs(q0), 1e-30)
    if rel > rtol:
        return Finding(R, "FAIL", f"load-point displacement {q0:.4g} -> "
                       f"{q1:.4g} ({rel:.1%}) when step time is x10; cards: "
                       f"{', '.join(rc)}", owner="model_agent",
                       cure="remove the rate-dependent card, or declare the "
                            "run rate-dependent and give the real time scale")
    return Finding(R, "PASS", f"time x10 changes the result by {rel:.1e}")


def _halve_increments(text: str) -> str:
    out, want = [], False
    for ln in text.split("\n"):
        s = ln.strip()
        if s.startswith("*") and not s.startswith("**"):
            want = _kw(s)[0] in ("STATIC", "VISCO")
            out.append(ln)
            continue
        if want and s:
            t = [x.strip() for x in s.split(",")]
            try:
                t[0] = f"{float(t[0]) / 2:.6g}"
                if len(t) > 3 and t[3]:
                    t[3] = f"{float(t[3]) / 2:.6g}"
            except ValueError:
                pass
            out.append(", ".join(t))
            want = False
            continue
        out.append(ln)
    return "\n".join(out)


def check_increments(deck: Deck, workdir: str, base_disp,
                     rtol: float = 0.01) -> Finding:
    """Halving the increments of a nonlinear run does not move the quantity
    of interest past tolerance. Abstains on linear steps."""
    R = "7 INCREMENT CONVERGENCE"
    cards = _cards(deck.path)
    nonlin = _nlgeom(cards) or _has(cards, _PATH_DEPENDENT + _RATE_CARDS
                                    + ("CONTACT PAIR", "HYPERELASTIC"))
    if not nonlin:
        return Finding(R, "NOT NEEDED", "linear steps only")
    text = open(deck.path, encoding="utf-8", errors="replace").read()
    p = _write(os.path.join(workdir, "chk_half.inp"), _halve_increments(text))
    r = solve(p)
    if not r["converged"]:
        return Finding(R, "NOT EVALUATED", f"halved run: {r['outcome']}")
    q0 = _qoi(deck, base_disp)
    q1 = _qoi(deck, read_frd_disp(r["frd"]))
    rel = abs(q1 - q0) / max(abs(q0), 1e-30)
    if rel > rtol:
        return Finding(R, "FAIL", f"load-point displacement {q0:.5g} -> "
                       f"{q1:.5g} ({rel:.2%}) with increments halved",
                       owner="solver driver",
                       cure="reduce the increment size until halving it "
                            "changes the result by less than 1 percent")
    return Finding(R, "PASS", f"halving increments changes the result by "
                   f"{rel:.1e}")


# ---- constraint realism (Step 8), post-solve, from reactions --------------

def check_support_reactions(deck: Deck, frd_path: str, fix_set: str,
                            tension_limit: float = 0.05,
                            mu: float = 0.3) -> Finding:
    """Does a fully fixed support act like a surface the part rests on?

    A clamp on a whole face can PULL the part (tensile normal reaction) and
    can hold any sideways load (unlimited friction). A bolted plate on a frame
    does neither between its bolts. Measured from the solved reactions on the
    support face, oriented by the face's own plane:
      tension share  = sum max(0, R.n) / sum |R.n|   (n outward from the part)
      friction ratio = sum |R_t| / sum of compressive |R.n|
    FAIL if tension share > 5% or friction ratio > mu (0.3 by default).
    The support was stated as fixed, so this is reported as a caveat on
    the idealisation, with the numbers, not as a deck error.
    """
    R = "8 SUPPORT REACTIONS"
    import numpy as np
    from frdread import read_frd_field
    nodes = sorted(set(deck.nsets.get(fix_set.upper(), [])))
    if len(nodes) < 3:
        return Finding(R, "NOT EVALUATED", f"no node set {fix_set}")
    try:
        rf = read_frd_field(frd_path, "FORC")
    except RuntimeError:
        return Finding(R, "NOT EVALUATED", "no RF field in the .frd")
    P = np.array([deck.nodes[n] for n in nodes])
    c = P.mean(axis=0)
    _u, s, vt = np.linalg.svd(P - c)
    span = max(s[0], 1e-30)
    if s[-1] / span > 1e-3:
        return Finding(R, "NOT EVALUATED", f"{fix_set} is not planar")
    n = vt[-1]
    allp = np.array(list(deck.nodes.values()))
    if np.dot(allp.mean(axis=0) - c, n) > 0:
        n = -n                      # outward from the part
    Rv = np.array([rf.get(k, (0.0, 0.0, 0.0)) for k in nodes])
    rn = Rv @ n
    rt = np.linalg.norm(Rv - np.outer(rn, n), axis=1)
    tens = rn[rn > 0].sum() / max(np.abs(rn).sum(), 1e-30)
    comp = -rn[rn < 0].sum()
    fric = rt.sum() / max(comp, 1e-30)
    ftxt = (f"{fric:.2f}" if comp > 1e-9 * max(np.abs(rn).sum(), 1e-30)
            else "unbounded (no compressive reaction)")
    detail = (f"on {fix_set}: {tens:.0%} of the normal reaction is the clamp "
              f"PULLING the part; tangential/compressive reaction "
              f"{ftxt} (limit {mu})")
    if tens > tension_limit or fric > mu:
        return Finding(R, "FAIL", detail, owner="case_agent",
                       cure="support only the bolt or washer footprints, or "
                            "model contact with the frame and bolt preload")
    return Finding(R, "PASS", detail)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_checks(deck_path: str, intent: Intent, workdir: Optional[str] = None,
               solved: Optional[Dict[str, object]] = None,
               verbose: bool = True) -> List[Finding]:
    """All seven. Check 4 before any solve; if it fails, nothing is solved.
    Checks 1-3 read the existing result; 5-7 solve only when gated in."""
    deck = read_deck(deck_path)
    workdir = workdir or os.path.join(
        os.path.dirname(os.path.abspath(deck_path)), "checks")
    os.makedirs(workdir, exist_ok=True)
    f4 = check_rigid_modes(deck)
    findings = [check_load_intent(deck, intent), f4]
    if f4.verdict == "FAIL":
        findings += [Finding(r, "NOT EVALUATED", "model not solved: check 4 "
                             "failed") for r in ("2 SMALL STRAIN",
                             "3 PENETRATION", "5 REVERSIBILITY",
                             "6 RATE INDEPENDENCE",
                             "7 INCREMENT CONVERGENCE")]
    else:
        base = solved or solve(deck_path)
        if not base.get("converged"):
            findings.append(Finding("SOLVE", "FAIL", str(base.get("outcome"))))
        else:
            disp = read_frd_disp(base["frd"])
            findings += [check_small_strain(deck, disp, intent),
                         check_penetration(deck, disp, intent),
                         check_reversibility(deck, intent, workdir),
                         check_rate(deck, intent, workdir, disp),
                         check_increments(deck, workdir, disp)]
    findings.sort(key=lambda f: f.rule)
    if verbose:
        print("\nSEVEN-CHECK PLAN")
        print("=" * 60)
        for f in findings:
            print(f.render())
        print("\nBLIND SPOT, by construction: a load on a wrong but plausible "
              "face\npasses all seven. That belongs to the face catalogue.")
    return findings


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python invariants.py deck.inp  (runs the checks that "
              "need no stated intent)")
        sys.exit(2)
    run_checks(sys.argv[1], Intent())
