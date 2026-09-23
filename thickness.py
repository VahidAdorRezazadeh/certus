"""
thickness.py - local member thickness, measured on the solid, and the number
of mesh elements actually crossed through it.

Why this exists. R7 (bending resolution) used to read "elements through the
wall" as 2V/A divided by the target element size. The mesh retry then set the
target size to 2V/A / 3, so the next evaluation returned exactly 3.0 by
construction: a check that could not fail. Here both inputs are measured
independently of the size the mesher was asked for:

  thickness  from point-in-solid rays across the section at half the lever
             arm, along the transverse force direction (thickest member cut)
  elements   by walking the same chord through the FINAL mesh and counting
             element changes

No dependency on the other Certus modules, so every module can import it.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple
import math

import gmsh

Vec = Tuple[float, float, float]


@dataclass
class Chord:
    start: Vec            # first point inside the solid
    direction: Vec        # unit vector along the chord
    length: float


def _bbox() -> Tuple[Vec, Vec]:
    b = gmsh.model.getBoundingBox(-1, -1)
    return (b[0], b[1], b[2]), (b[3], b[4], b[5])


def section_chords(cc: Vec, e: Vec, d_dir: Vec, rlen: float,
                   n_rays: int = 11, n_steps: int = 400
                   ) -> Optional[List[Chord]]:
    """Solid intervals along d_dir on the plane normal to e at half the lever
    arm. None when the model cannot answer point-in-solid queries."""
    vols = [t for _, t in gmsh.model.getEntities(3)]
    if not vols:
        return None
    lo, hi = _bbox()
    diag = math.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))
    t = (e[1] * d_dir[2] - e[2] * d_dir[1],
         e[2] * d_dir[0] - e[0] * d_dir[2],
         e[0] * d_dir[1] - e[1] * d_dir[0])
    p0 = tuple(cc[i] + 0.5 * rlen * e[i] for i in range(3))
    ds = 2.0 * diag / n_steps
    out: List[Chord] = []
    try:
        for k in range(n_rays):
            a = -0.5 * diag + diag * k / (n_rays - 1)
            run, first = 0, None
            for j in range(n_steps + 1):
                sj = -diag + j * ds
                p = [p0[i] + a * t[i] + sj * d_dir[i] for i in range(3)]
                inside = any(gmsh.model.isInside(3, v, p) for v in vols)
                if inside:
                    if run == 0:
                        first = tuple(p)
                    run += 1
                elif run:
                    out.append(Chord(first, tuple(d_dir), run * ds))
                    run = 0
            if run:
                out.append(Chord(first, tuple(d_dir), run * ds))
    except Exception:
        return None
    return out or None


def section_depth(cc: Vec, e: Vec, d_dir: Vec, rlen: float
                  ) -> Optional[Tuple[float, List[float]]]:
    ch = section_chords(cc, e, d_dir, rlen)
    if not ch:
        return None
    return max(c.length for c in ch), sorted(set(round(c.length, 2)
                                                 for c in ch))


@dataclass
class ThroughThickness:
    thickness: float          # length of the member chord, mm
    elements: float           # thickness / local element edge length
    local_size: float         # mean corner-edge length of crossed elements
    crossings: List[int]      # raw element changes per chord, diagnostic
    source: str = "measured on the final mesh"


_CORNERS = {4: 4, 11: 4, 5: 8, 12: 8, 17: 8, 6: 6, 13: 6, 18: 6}  # gmsh types
_EDGES = {4: [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)],
          8: [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
              (0, 4), (1, 5), (2, 6), (3, 7)],
          6: [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4),
              (2, 5)]}


def _edge_length(el_tag: int) -> Optional[float]:
    etype, nodes, _dim, _tag = gmsh.model.mesh.getElement(el_tag)
    nc = _CORNERS.get(etype)
    if nc is None:
        return None
    p = [gmsh.model.mesh.getNode(int(n))[0] for n in nodes[:nc]]
    ed = [math.dist(p[i], p[j]) for i, j in _EDGES[nc]]
    return sum(ed) / len(ed)


def elements_through_member(cc: Vec, e: Vec, d_dir: Vec, rlen: float,
                            samples: int = 200
                            ) -> Optional[ThroughThickness]:
    """Elements through the thickest member at the section, from the mesh.

    Effective count = member thickness / mean edge length of the elements the
    thickness chords actually pass through. Both numbers are measured on the
    final mesh, so the count can fall short of what was requested.

    Why not simply count element changes along the chord: a straight line
    through a tet mesh clips many tets over short lengths. Measured on the
    bracket lug (5.26 mm, 2.5 mm tets): 7 to 9 changes for about 2 layers.
    That count would let R7 pass almost any tet mesh. It is kept only as a
    diagnostic.
    """
    ch = section_chords(cc, e, d_dir, rlen)
    if not ch:
        return None
    tmax = max(c.length for c in ch)
    use = [c for c in ch if c.length >= 0.9 * tmax]
    counts: List[int] = []
    crossed = set()
    for c in use:
        eps = 0.02 * c.length
        last, n = None, 0
        for i in range(samples):
            s = eps + (c.length - 2 * eps) * i / (samples - 1)
            p = [c.start[k] + s * c.direction[k] for k in range(3)]
            try:
                el = gmsh.model.mesh.getElementByCoordinates(
                    p[0], p[1], p[2], 3)[0]
            except Exception:
                continue
            crossed.add(el)
            if el != last:
                n += 1
                last = el
        if n:
            counts.append(n)
    sizes = [h for h in (_edge_length(t) for t in crossed) if h]
    if not sizes:
        return None
    h = sum(sizes) / len(sizes)
    return ThroughThickness(tmax, tmax / h, h, counts)
