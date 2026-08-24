#!/usr/bin/env python3
"""
invariants.py - verification check 4: reference-free physics invariants.

Where this sits among the checks:

    locking_check.py   pre-solve. Element formulation against load regime.
    verify_sets.py     pre-solve. Did the node set land on the face you meant.
    results_check.py   post-solve. Compare to a closed form answer.
    invariants.py      post-solve. Checks that need NO reference answer.

Why it exists. results_check.py needs a closed form solution. On a customer's
bracket there is no closed form solution, no reference deck and no expert. But
a family of physics checks needs none of those three. They are computable from
the model and its own results:

    INV1  global equilibrium   reactions balance the applied resultant
    INV2  zero load            no load must produce no response
    INV3  load scaling         doubling a linear load doubles the response

Deliberate design choice: this module reads a WRITTEN DECK and a solver
output. It does not take a provenance object from the writer, and it does not
import case_agent. That is not tidiness. A check that trusts the writer's own
account of what it wrote cannot catch the writer being wrong, and it also
cannot be pointed at a deck somebody else produced.

WHAT THESE CHECKS DO NOT CATCH, measured not assumed. A load applied to a
geometrically plausible but WRONG face still closes global equilibrium, still
produces zero response at zero load, and still scales linearly. Measured on a
cantilever: moving the load from the tip face to the bottom face changed tip
deflection by 62 percent, and INV1, INV2 and INV3 all returned PASS. Input
fidelity belongs to the face catalogue and the confirmation step. Do not sell
this module as covering it.

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
            if len(toks) >= 3:
                try:
                    out.append(f"{toks[0]}, {toks[1]}, "
                               f"{float(toks[2]) * load_scale:.10g}")
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
    return {
        "job": job,
        "converged": os.path.exists(frd) and os.path.getsize(frd) > 0,
        "rf_total": read_total_force(dat) if os.path.exists(dat) else None,
        "frd": frd if os.path.exists(frd) else None,
        "stdout": r.stdout,
    }


def read_total_force(path: str) -> Optional[Tuple[float, float, float]]:
    """Total reaction force from a .dat file.

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
                mode = "rf" if "total force" in low else None
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


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    rule: str
    verdict: str            # PASS | FAIL | NOT EVALUATED
    detail: str
    owner: str = ""
    cure: str = ""

    def render(self) -> str:
        out = [f"{self.rule:22s} {self.verdict}", f"  {self.detail}"]
        if self.verdict == "FAIL" and self.cure:
            out.append(f"  owner: {self.owner}")
            out.append(f"  cure : {self.cure}")
        return "\n".join(out)


def check_equilibrium(deck: Deck, result: Dict[str, object],
                      rtol: float = 1e-4) -> Finding:
    """INV1. Reactions must balance the applied resultant."""
    if deck.cloads_by_set:
        return Finding(
            "INV1_EQUILIBRIUM", "NOT EVALUATED",
            f"deck applies *CLOAD by set name ({deck.cloads_by_set[0][0]}). "
            "CalculiX applies that force to every node in the set, so the "
            "intended resultant cannot be read from the deck. See M3.")
    if deck.has_dload:
        return Finding(
            "INV1_EQUILIBRIUM", "NOT EVALUATED",
            "deck contains *DLOAD. The applied resultant needs element face "
            "areas, which this module does not compute yet.")
    if not deck.cloads:
        return Finding("INV1_EQUILIBRIUM", "NOT EVALUATED",
                       "no *CLOAD lines in the deck")
    rf = result.get("rf_total")
    if rf is None:
        return Finding("INV1_EQUILIBRIUM", "NOT EVALUATED",
                       "no total reaction force in the .dat output")

    applied = applied_resultant(deck)
    on_bc = load_on_constrained(deck)

    # The reference scale is the MAGNITUDE OF THE LOAD RESULTANT, not the
    # per-component value. A component of the applied load is often exactly
    # zero, and dividing a 1e-9 N numerical residual by a per-component floor
    # turns rounding noise into a double-digit percentage. Measured: a
    # verified-correct cantilever reported 6.3 percent imbalance in x and
    # 16.6 percent in y purely from this, while z was correct to 1.6e-07.
    scale = max((sum(v * v for v in applied)) ** 0.5,
                (sum(v * v for v in rf)) ** 0.5, 1e-12)

    lines, worst = [], 0.0
    for i, ax in enumerate("xyz"):
        # a load on a constrained node is reacted directly and is absent from
        # RF, so it is subtracted here. See M1.
        lhs = rf[i] - on_bc[i]
        rhs = -applied[i]
        rel = abs(lhs - rhs) / scale
        worst = max(worst, rel)
        lines.append(f"{ax}: RF {rf[i]:+.6g} - on-BC {on_bc[i]:+.6g} "
                     f"= {lhs:+.6g}  vs applied {rhs:+.6g}  "
                     f"(rel to |R|={scale:.4g}: {rel:.2e})")
    return Finding(
        "INV1_EQUILIBRIUM", "PASS" if worst <= rtol else "FAIL",
        "; ".join(lines),
        owner="load or constraint definition",
        cure="check load direction, a dropped load component, a unit "
             "inconsistency, or a constraint outside the reported set "
             "absorbing part of the load")


def check_zero_load(result: Dict[str, object], atol: float = 1e-9) -> Finding:
    """INV2. No load must produce no response.

    Refuses to evaluate if no load line was actually rescaled. A check whose
    input was not perturbed cannot fail honestly, and a check that cannot fail
    honestly is worse than no check.
    """
    if result.get("n_load_lines_modified") == 0:
        return Finding("INV2_ZERO_LOAD", "NOT EVALUATED",
                       "no load line in this deck could be rescaled, so the "
                       "zero-load variant is identical to the base run")
    if not result.get("frd"):
        return Finding("INV2_ZERO_LOAD", "NOT EVALUATED",
                       "zero-load run produced no .frd")
    u = read_frd_disp(result["frd"])
    if not u:
        return Finding("INV2_ZERO_LOAD", "NOT EVALUATED",
                       "no displacement block in the zero-load .frd")
    worst = max(max(abs(v) for v in val) for val in u.values())
    return Finding("INV2_ZERO_LOAD", "PASS" if worst <= atol else "FAIL",
                   f"largest displacement with every load removed: "
                   f"{worst:.3e} mm over {len(u)} nodes",
                   owner="load definition or initial state",
                   cure="a spurious load, a residual initial condition, or a "
                        "prescribed nonzero displacement")


def check_load_scaling(base: Dict[str, object], scaled: Dict[str, object],
                       factor: float, rtol: float = 2e-5,
                       max_nodes: int = 5000) -> Finding:
    """INV3. Doubling a linear load doubles the response exactly.

    rtol is set by OUTPUT PRECISION, not solver accuracy. See M2. Do not
    tighten below 1e-5: the .frd stores E12.5.
    """
    if scaled.get("n_load_lines_modified") == 0:
        return Finding("INV3_LOAD_SCALING", "NOT EVALUATED",
                       "no load line in this deck could be rescaled, so the "
                       "scaled variant is identical to the base run. A "
                       "reported deviation of exactly 0.5 is this condition, "
                       "not a physics finding.")
    if not (base.get("frd") and scaled.get("frd")):
        return Finding("INV3_LOAD_SCALING", "NOT EVALUATED",
                       "one of the two runs produced no .frd")
    ub, us = read_frd_disp(base["frd"]), read_frd_disp(scaled["frd"])
    if not ub or not us:
        return Finding("INV3_LOAD_SCALING", "NOT EVALUATED",
                       "displacement field missing")
    ref = max(max(abs(v) for v in val) for val in ub.values())
    floor = ref * 1e-3          # ignore near-zero components, they are noise
    worst, where = 0.0, None
    for i, n in enumerate(ub):
        if i >= max_nodes:
            break
        if n not in us:
            continue
        for k in range(3):
            expect = ub[n][k] * factor
            if abs(expect) < floor:
                continue
            rel = abs(us[n][k] - expect) / abs(expect)
            if rel > worst:
                worst, where = rel, (n, "xyz"[k])
    return Finding(
        "INV3_LOAD_SCALING", "PASS" if worst <= rtol else "FAIL",
        f"worst relative deviation {worst:.3e} at node {where} for a load "
        f"factor of {factor} (tolerance {rtol:.0e}, set by .frd E12.5 "
        f"precision)",
        owner="material model or solution procedure",
        cure="a nonlinear material, contact, or a large-displacement setting "
             "is active in a run presented as linear")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_invariants(deck_path: str, workdir: Optional[str] = None,
                   factor: float = 2.0, verbose: bool = True
                   ) -> List[Finding]:
    """Solve the deck, a zero-load copy and a scaled copy, then check.

    Cost: two extra solves. On a workflow already dominated by solver time
    that is a small marginal cost, which is the point.
    """
    deck = read_deck(deck_path)
    workdir = workdir or os.path.join(
        os.path.dirname(os.path.abspath(deck_path)), "invariants")
    os.makedirs(workdir, exist_ok=True)

    if verbose:
        print(f"deck        : {deck_path}")
        print(f"nodes       : {len(deck.nodes)}   elements: {len(deck.elements)}")
        print(f"element type: {sorted(deck.element_types)}")
        print(f"*CLOAD lines: {len(deck.cloads)}   by set: {len(deck.cloads_by_set)}"
              f"   *DLOAD: {deck.has_dload}")
        print(f"applied resultant : {applied_resultant(deck)}")
        print(f"constrained nodes : {len(constrained_nodes(deck))}")
        print(f"load on those     : {load_on_constrained(deck)}")
        print()

    runs = {}
    for tag, scale in (("base", 1.0), ("zero", 0.0), ("scaled", factor)):
        p, n_mod = make_variant(deck, os.path.join(workdir, f"inv_{tag}.inp"),
                                load_scale=scale)
        if verbose:
            print(f"solving {tag} ... ({n_mod} load line(s) rescaled)",
                  flush=True)
        runs[tag] = solve(p)
        runs[tag]["n_load_lines_modified"] = n_mod
        if not runs[tag]["converged"] and verbose:
            print(f"  {tag} did not converge")

    findings = [
        check_equilibrium(deck, runs["base"]),
        check_zero_load(runs["zero"]),
        check_load_scaling(runs["base"], runs["scaled"], factor),
    ]
    if verbose:
        print()
        print("REFERENCE-FREE INVARIANTS")
        print("=" * 60)
        for f in findings:
            print(f.render())
        print()
        print("NOTE: these checks do not detect a load placed on the wrong "
              "face.\n      Measured: a 62 percent error in tip deflection "
              "passed all three.")
    return findings


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python invariants.py <path to solvable case.inp>")
        sys.exit(1)
    fs = run_invariants(sys.argv[1])
    sys.exit(0 if all(f.verdict != "FAIL" for f in fs) else 2)
