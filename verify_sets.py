#!/usr/bin/env python3
"""
verify_sets.py - prove a node set landed on the face you meant, from the deck
alone. No CAD, no Gmsh session, no Abaqus.

Why this exists: "the selector picked group 25" is a claim about tags. This
reads the WRITTEN DECK back and asks a geometric question about the actual
node coordinates. If the answer disagrees with what you intended, the selector
was wrong no matter how convincing the catalogue looked.

Usage:
    python verify_sets.py brk.inp
"""

import sys
import math
from collections import OrderedDict


def read_inp(path):
    nodes, nsets = {}, OrderedDict()
    mode, cur = None, None
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("**"):
                continue
            if s.startswith("*"):
                up = s.upper()
                if up.startswith("*NODE"):
                    mode, cur = "node", None
                elif up.startswith("*NSET"):
                    mode = "nset"
                    cur = s.split("NSET=")[1].split(",")[0].strip()
                    nsets[cur] = []
                else:
                    mode, cur = None, None
                continue
            parts = [p.strip() for p in s.split(",") if p.strip()]
            if mode == "node" and len(parts) >= 4:
                nodes[int(parts[0])] = tuple(float(x) for x in parts[1:4])
            elif mode == "nset" and cur:
                nsets[cur].extend(int(p) for p in parts)
    return nodes, nsets


def classify(pts):
    """Planar, cylindrical or neither. Deterministic, no fitting library."""
    n = len(pts)
    c = tuple(sum(p[i] for p in pts) / n for i in range(3))
    ext = [max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3)]

    # planar test: one extent collapses relative to the other two
    span = max(ext)
    flat = [i for i in range(3) if ext[i] < 1e-6 * max(span, 1.0)]
    if flat:
        ax = "XYZ"[flat[0]]
        return (f"PLANAR, normal along {ax}, at {ax}={c[flat[0]]:.3f}", c, ext)

    # cylindrical test. The centre must come from the BOUNDING BOX, not from
    # the mean of the nodes. Node density along a hole is uneven, so the mean
    # sits off-axis and inflates the radius spread. Measured on a dia-8 hole:
    # mean centre gave 5.96% spread and a false IRREGULAR verdict, bbox centre
    # gave 0.000% and the exact radius.
    bc = [(max(p[i] for p in pts) + min(p[i] for p in pts)) / 2
          for i in range(3)]
    best = None
    for ax in range(3):
        o = [i for i in range(3) if i != ax]
        r = [math.hypot(p[o[0]] - bc[o[0]], p[o[1]] - bc[o[1]]) for p in pts]
        rmin, rmax, rmean = min(r), max(r), sum(r) / n
        if rmean > 1e-9 and (rmax - rmin) / rmean < 0.05:
            best = (f"CYLINDRICAL about {'XYZ'[ax]}, radius {rmean:.4f} "
                    f"(dia {2*rmean:.4f}), radius spread "
                    f"{100*(rmax-rmin)/rmean:.3f}%, axis at "
                    f"{'XYZ'[o[0]]}={bc[o[0]]:.3f} "
                    f"{'XYZ'[o[1]]}={bc[o[1]]:.3f}", c, ext)
            break
    return best or ("IRREGULAR, not a single planar or cylindrical face",
                    c, ext)


def main(path):
    nodes, nsets = read_inp(path)
    print(f"{path}: {len(nodes)} nodes, {len(nsets)} node set(s)\n")
    if not nsets:
        print("NO NODE SETS IN THIS DECK. Nothing to attach a BC to.")
        return 1

    gmin = [min(p[i] for p in nodes.values()) for i in range(3)]
    gmax = [max(p[i] for p in nodes.values()) for i in range(3)]
    print("part bbox: "
          + "  ".join(f"{'XYZ'[i]} {gmin[i]:.2f}..{gmax[i]:.2f}"
                      for i in range(3)) + "\n")

    bad = 0
    for name, tags in nsets.items():
        missing = [t for t in tags if t not in nodes]
        if missing:
            print(f"{name}: {len(missing)} node ids NOT in *NODE. "
                  f"The deck is inconsistent.")
            bad += 1
            continue
        pts = [nodes[t] for t in tags]
        kind, c, ext = classify(pts)
        print(f"{name}  ({len(tags)} nodes)")
        print(f"  shape    : {kind}")
        print(f"  centroid : ({c[0]:.2f}, {c[1]:.2f}, {c[2]:.2f})")
        print(f"  extent   : {ext[0]:.2f} x {ext[1]:.2f} x {ext[2]:.2f}")
        frac = 100.0 * len(tags) / len(nodes)
        print(f"  share    : {frac:.1f}% of all nodes"
              + ("   <-- LARGE. Check this is not over-constrained."
                 if frac > 5 else ""))
        print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "brk.inp"))
