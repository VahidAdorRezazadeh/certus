#!/usr/bin/env python3
"""
results_check.py - verification check 2: the analytical reference check.

Reads a solved result and compares it to a closed form answer. This is the
check that decides whether the whole chain is correct, because every layer
before it can be individually plausible and still produce the wrong number.

Deliberate choices:
  - the metric is TIP DEFLECTION, not peak stress. Deflection is a global
    quantity and is insensitive to the local details where the FE model and
    beam theory legitimately differ: Poisson restraint at a fully clamped
    root, and Saint-Venant effects under a distributed tip traction. Peak
    stress at the root is sensitive to exactly those, so a stress comparison
    would fail for correct reasons and teach nothing.
  - the verdict is a tolerance BAND, never equality. Meshing is not
    reproducible across platforms: the same geometry at the same target size
    gave 84,584 elements on Linux and 84,600 on Windows.
  - a result that is too GOOD is also reported. Agreement to five digits on a
    3D FE against Euler-Bernoulli would mean the FE is not doing what we
    think it is.

Currently reads CalculiX .frd. Abaqus .odb needs its own reader and is not
implemented here, so an Abaqus run must be compared by hand until it is.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List
import os
import re
import sys


from frdread import read_frd_disp  # width-detecting .frd reader, shared so there is one copy


@dataclass
class Comparison:
    quantity: str
    computed: float
    reference: float
    tolerance: float
    unit: str = "mm"

    @property
    def error(self) -> float:
        if abs(self.reference) < 1e-15:
            return float("nan")
        return (self.computed - self.reference) / self.reference

    @property
    def verdict(self) -> str:
        e = abs(self.error)
        if e <= 0.002:
            return "SUSPICIOUS"
        if e <= self.tolerance:
            return "PASS"
        if e <= 3 * self.tolerance:
            return "FAIL"
        return "FAIL HARD"

    def render(self) -> str:
        lines = [
            f"{self.quantity}",
            f"  FE result   {self.computed:.6f} {self.unit}",
            f"  reference   {self.reference:.6f} {self.unit}",
            f"  difference  {self.error*100:+.2f}%  "
            f"(tolerance {self.tolerance*100:.1f}%)",
            f"  verdict     {self.verdict}",
        ]
        if self.verdict == "SUSPICIOUS":
            lines.append(
                "  A 3D FE model should NOT match Euler-Bernoulli to better "
                "than about 0.2%. Shear deformation, Poisson restraint at the "
                "root and the distributed tip traction all make a real "
                "difference. Agreement this close suggests the comparison is "
                "not measuring what it claims to.")
        elif self.verdict.startswith("FAIL"):
            lines.append(
                "  Check in this order: is NLGEOM off, is the load resultant "
                "what you intended, is the constraint face the one you meant, "
                "and is the mesh converged.")
        return "\n".join(lines)


def check_cantilever(frd_path: str, tip_deflection_ref: float,
                     tolerance: float = 0.05) -> Comparison:
    disp = read_frd_disp(frd_path)
    uz = min(d[2] for d in disp.values())   # most negative
    return Comparison("TIP DEFLECTION (max |Uz|)", abs(uz),
                      abs(tip_deflection_ref), tolerance)


def convergence(pairs: List[Tuple[float, float]]) -> str:
    """Verification check 1: mesh convergence.

    pairs is [(element_size, quantity)] from coarse to fine. Reports the
    change between successive refinements. A quantity still moving by more
    than a couple of percent on the finest pair is not converged, and any
    comparison against a reference is measuring discretisation error.
    """
    if len(pairs) < 2:
        return "convergence needs at least two mesh sizes"
    out = ["MESH CONVERGENCE", f"{'size (mm)':>12s} {'value':>14s} "
           f"{'change':>10s}"]
    prev = None
    for size, val in pairs:
        ch = "" if prev is None else f"{(val-prev)/prev*100:+.2f}%"
        out.append(f"{size:12.4f} {val:14.6f} {ch:>10s}")
        prev = val
    last = abs((pairs[-1][1] - pairs[-2][1]) / pairs[-2][1])
    out.append("")
    out.append(f"  final change {last*100:.2f}%  ->  "
               + ("CONVERGED" if last < 0.02 else
                  "NOT CONVERGED, refine further before trusting the value"))
    return "\n".join(out)


if __name__ == "__main__":
    frd, ref = sys.argv[1], float(sys.argv[2])
    print(check_cantilever(frd, ref).render())


# ---------------------------------------------------------------------------
# did the solve finish the step it was asked to do
# ---------------------------------------------------------------------------

@dataclass
class CcxOutcome:
    converged: bool
    reason: str


def _step_period(deck_path: str) -> Optional[float]:
    """Time period of the LAST *STATIC step: 2nd field of its data line,
    1.0 when the line is empty or absent (CalculiX default)."""
    if not deck_path or not os.path.exists(deck_path):
        return None
    period, want = None, False
    with open(deck_path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("**"):
                continue
            if s.startswith("*"):
                if want:            # *STATIC with no data line
                    period = 1.0
                want = s.upper().startswith("*STATIC")
                continue
            if want:
                toks = [t.strip() for t in s.split(",")]
                try:
                    period = float(toks[1]) if len(toks) > 1 and toks[1] \
                        else 1.0
                except ValueError:
                    period = 1.0
                want = False
    return period


def _sta_last_step_time(sta_path: str) -> Optional[float]:
    if not os.path.exists(sta_path):
        return None
    last = None
    with open(sta_path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            toks = ln.split()
            if len(toks) >= 7 and toks[0].isdigit():
                try:
                    last = float(toks[5])
                except ValueError:
                    pass
    return last


def ccx_outcome(log: str, returncode: int, job_base: str,
                deck_path: Optional[str] = None,
                rtol: float = 1e-6) -> CcxOutcome:
    """One verdict on whether CalculiX finished what it was asked to do.

    All must hold: exit code 0, "Job finished" printed, no *ERROR line, and,
    when a .sta file exists, the last converged increment reached the step
    period of the last *STATIC step. A non-empty .frd proves nothing: a run
    that aborts with "increment size smaller than minimum" still writes one.
    """
    if returncode != 0:
        return CcxOutcome(False, f"ccx exit code {returncode}")
    errs = [l.strip() for l in log.splitlines() if "*ERROR" in l.upper()]
    if errs:
        return CcxOutcome(False, errs[0])
    if "job finished" not in log.lower():
        return CcxOutcome(False, "ccx did not print 'Job finished'")
    t_end = _sta_last_step_time(job_base + ".sta")
    period = _step_period(deck_path) if deck_path else None
    if t_end is not None and period is not None and \
            t_end < period * (1 - rtol):
        return CcxOutcome(False, f"step stopped at time {t_end:g} of "
                                 f"{period:g}")
    return CcxOutcome(True, "finished, step period reached"
                      if t_end is not None else "finished (no .sta)")


# ---------------------------------------------------------------------------
# Richardson extrapolation and observed order: a convergence check that can
# fail. Procedure after Celik et al., J. Fluids Eng. 130 (2008) 078001
# (three grids, possibly non-constant refinement ratio, fixed-point p).
# ---------------------------------------------------------------------------

@dataclass
class ConvergenceVerdict:
    verdict: str                 # PASS | FAIL | NOT EVALUATED
    detail: str
    p: Optional[float] = None
    extrapolated: Optional[float] = None
    gci_fine: Optional[float] = None


def richardson(pairs: List[Tuple[float, float]], gci_limit: float = 0.02,
               p_min: float = 0.5) -> ConvergenceVerdict:
    """pairs = [(h, f)] for three meshes, any order. FAIL when the three
    results are identical (the meshes are not distinct), when the changes
    oscillate, when the observed order is below p_min (diverging or not in
    the asymptotic range, e.g. a stress singularity), or when the fine-grid
    GCI exceeds gci_limit."""
    import math
    if len(pairs) < 3:
        return ConvergenceVerdict("NOT EVALUATED", "needs three meshes")
    (h3, f3), (h2, f2), (h1, f1) = sorted(pairs)[:3]    # 1 coarse, 3 fine
    if len({round(h, 9) for h in (h1, h2, h3)}) < 3:
        return ConvergenceVerdict("FAIL", "two meshes have the same size: "
                                  "the study cannot show convergence")
    e21, e32 = f2 - f1, f3 - f2
    scale = max(abs(f3), 1e-30)
    if abs(e21) < 1e-12 * scale and abs(e32) < 1e-12 * scale:
        return ConvergenceVerdict("FAIL", "all three results are identical: "
                                  "the meshes were not really refined, so "
                                  "the study proves nothing")
    if abs(e32) < 1e-12 * scale or abs(e21) < 1e-12 * scale:
        return ConvergenceVerdict("NOT EVALUATED", "one change is zero; "
                                  "observed order undefined")
    if e32 / e21 < 0:
        return ConvergenceVerdict("FAIL", f"oscillating: {f1:.6g} -> "
                                  f"{f2:.6g} -> {f3:.6g}")
    if e32 / e21 > 0 and max(abs(e21), abs(e32)) < 1e-3 * scale:
        # Both changes below 0.1 percent: the order is not resolvable above
        # the noise of an unstructured mesh, and the value has settled.
        return ConvergenceVerdict("PASS", f"monotone and settled: changes "
                                  f"{e21 / scale:+.2e}, {e32 / scale:+.2e} of "
                                  f"the value, below 0.1%; observed order "
                                  f"not resolvable at this level")
    r21, r32 = h1 / h2, h2 / h3
    s = 1.0 if e32 / e21 > 0 else -1.0
    p = abs(math.log(abs(e32 / e21))) / math.log(r21)
    for _ in range(100):
        q = math.log((r21 ** p - s) / (r32 ** p - s))
        pn = abs(math.log(abs(e21 / e32)) + q) / math.log(r21)
        if abs(pn - p) < 1e-10:
            p = pn
            break
        p = pn
    if abs(e32) >= abs(e21):
        return ConvergenceVerdict("FAIL", f"not converging: changes "
                                  f"{e21:+.4g} then {e32:+.4g} do not shrink "
                                  f"({f1:.6g} -> {f2:.6g} -> {f3:.6g}). "
                                  f"Typical of a singularity", p=None)
    ext = (r32 ** p * f3 - f2) / (r32 ** p - 1)
    gci = 1.25 * abs(e32 / scale) / (r32 ** p - 1)
    base = (f"observed order p = {p:.2f}, extrapolated {ext:.6g}, "
            f"fine-grid GCI {gci:.2%}")
    if p < p_min:
        return ConvergenceVerdict("FAIL", base + f"; p below {p_min}: not in "
                                  f"the asymptotic range", p, ext, gci)
    if gci > gci_limit:
        return ConvergenceVerdict("FAIL", base + f" above {gci_limit:.0%}",
                                  p, ext, gci)
    return ConvergenceVerdict("PASS", base, p, ext, gci)
