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
