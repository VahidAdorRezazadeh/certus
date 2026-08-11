#!/usr/bin/env python3
"""
cantilever.py - the validation case, and its closed form answer.

Why a beam and not the bracket. The bracket is the presentation artifact. It
is not hand calculable to better than a rough factor, so a 30 percent
disagreement there tells you nothing about whether the pipeline is correct.
A cantilever gives an exact answer, so a 2 percent disagreement means a real
bug. Validate on the beam, present with the bracket.

Geometry: a rectangular prism, length L along X, width b along Y, depth h
along Z. Fixed at X=0, loaded at X=L in negative Z.

Closed form, small displacement Euler-Bernoulli plus Timoshenko shear:

    I     = b h^3 / 12
    delta = F L^3 / (3 E I)        bending
          + F L / (G A_s)          shear, A_s = 5/6 * b h
    sigma = M c / I  at the root,  M = F L,  c = h/2

Known limits of the comparison, stated so they are not discovered as
surprises:
  - a fully clamped 3D root also restrains Poisson contraction, which makes
    the FE model stiffer than beam theory near the support
  - a distributed tip traction is only statically equivalent to a point load,
    so the tip region differs by Saint-Venant
  - both effects are local. Expect agreement of a few percent on deflection,
    and worse on peak stress, which is why deflection is the metric.
"""

from __future__ import annotations
from dataclasses import dataclass
import math
import gmsh


@dataclass
class Cantilever:
    L: float = 100.0
    b: float = 10.0      # width, Y
    h: float = 5.0       # depth, Z
    E: float = 210000.0  # MPa
    nu: float = 0.3

    @property
    def I(self) -> float:
        return self.b * self.h ** 3 / 12.0

    @property
    def A(self) -> float:
        return self.b * self.h

    @property
    def G(self) -> float:
        return self.E / (2.0 * (1.0 + self.nu))

    def force_for_stress(self, target_sigma: float) -> float:
        """Tip force that produces a given root bending stress."""
        return target_sigma * self.I / (self.L * self.h / 2.0)

    def tip_deflection(self, F: float) -> dict:
        bend = F * self.L ** 3 / (3.0 * self.E * self.I)
        shear = F * self.L / (self.G * (5.0 / 6.0) * self.A)
        return {"bending": bend, "shear": shear, "total": bend + shear}

    def root_stress(self, F: float) -> float:
        return F * self.L * (self.h / 2.0) / self.I

    def slenderness(self) -> float:
        return self.L / self.h

    def render(self, F: float) -> str:
        d = self.tip_deflection(F)
        return (
            f"CANTILEVER REFERENCE (closed form)\n"
            f"  geometry     L={self.L} b={self.b} h={self.h} mm\n"
            f"  material     E={self.E} MPa  nu={self.nu}\n"
            f"  I            {self.I:.4f} mm^4\n"
            f"  slenderness  L/h = {self.slenderness():.1f}\n"
            f"  tip force    {F:.4f} N in -Z\n"
            f"  root stress  {self.root_stress(F):.3f} MPa\n"
            f"  deflection   {d['total']:.6f} mm "
            f"(bending {d['bending']:.6f}, shear {d['shear']:.6f})\n"
            f"  shear share  {100*d['shear']/d['total']:.2f}% of total")


def write_step(cl: Cantilever, path: str) -> str:
    """Write the beam as a STEP file using Gmsh's OCC kernel.

    Deliberately not build123d: the validation case must not depend on the
    CAD agent, so that a CAD agent bug cannot be mistaken for a solver bug.
    """
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("cantilever")
        gmsh.model.occ.addBox(0, 0, 0, cl.L, cl.b, cl.h)
        gmsh.model.occ.synchronize()
        gmsh.write(path)
    finally:
        gmsh.finalize()
    return path


if __name__ == "__main__":
    c = Cantilever()
    F = c.force_for_stress(50.0)
    print(c.render(F))
