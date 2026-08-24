#!/usr/bin/env python3
"""
test_invariants.py - exercise every invariant on a known-good deck and on
seeded known-bad decks.

The rule this file enforces: no check is presented as working until it has
PASSED on good input and FAILED on bad input. That rule earned its place. Four
separate false FAILs on verified-correct models were found during development
of invariants.py, and every one of them was found by the known-good case, not
by the known-bad case:

  1. equilibrium correction added where it should have been subtracted
  2. load-scaling tolerance set tighter than the .frd output precision
  3. equilibrium scaled per component, so a 1e-9 N residual in a direction
     with zero applied load read as a 6 percent imbalance
  4. reaction force read from the first increment of a multi-increment run

It also enforces the reverse rule. Two seeded cases in this file were WRONG,
and the module was right:

  - a second *BOUNDARY entry in a different set is not hidden from the check,
    because constrained_nodes() reads every *BOUNDARY entry in the deck
  - an *EQUATION tying two loaded nodes together is internal, so global
    equilibrium legitimately still closes

Both are recorded rather than deleted, because "the check did not fire" is
only a defect if the seeded case actually breaks the invariant.

The deck is generated here, in a few hundred elements, so the whole file runs
in under a minute. Do not run this against a 40,000 node production deck.

Usage:
    python test_invariants.py
"""

import os
import sys

import invariants as INV

WORK = "invtest"
E, NU = 210000.0, 0.3
L, H, B = 200.0, 20.0, 20.0
TOTAL_LOAD = -2000.0        # N, in -y, on the tip face


# ---------------------------------------------------------------------------
# A small structured C3D20R cantilever, written directly. No Gmsh, no CAD, so
# a meshing bug cannot be mistaken for a check bug.
# ---------------------------------------------------------------------------

C3D20_OFFSETS = [
    (0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0),
    (0, 0, 2), (2, 0, 2), (2, 2, 2), (0, 2, 2),
    (1, 0, 0), (2, 1, 0), (1, 2, 0), (0, 1, 0),
    (1, 0, 2), (2, 1, 2), (1, 2, 2), (0, 1, 2),
    (0, 0, 1), (2, 0, 1), (2, 2, 1), (0, 2, 1),
]


def write_good_deck(path, nx=10, ny=2, nz=2):
    NX, NY, NZ = 2 * nx, 2 * ny, 2 * nz
    ids, nodes, nid = {}, {}, 0
    for i in range(NX + 1):
        for j in range(NY + 1):
            for k in range(NZ + 1):
                if (i % 2) + (j % 2) + (k % 2) > 1:
                    continue
                nid += 1
                ids[(i, j, k)] = nid
                nodes[nid] = (L * i / NX, H * j / NY, B * k / NZ)

    elems = []
    for a in range(nx):
        for b in range(ny):
            for c in range(nz):
                base = (2 * a, 2 * b, 2 * c)
                elems.append(tuple(
                    ids[(base[0] + d[0], base[1] + d[1], base[2] + d[2])]
                    for d in C3D20_OFFSETS))

    tol = 1e-9
    root = sorted(n for n, (x, y, z) in nodes.items() if abs(x) < tol)
    tip = sorted(n for n, (x, y, z) in nodes.items() if abs(x - L) < tol)
    per = TOTAL_LOAD / len(tip)

    out = ["*HEADING", "invariants known-good cantilever", "*NODE, NSET=NALL"]
    for n in sorted(nodes):
        x, y, z = nodes[n]
        out.append(f"{n}, {x:.6f}, {y:.6f}, {z:.6f}")
    out.append("*ELEMENT, TYPE=C3D20R, ELSET=SOLID")
    for i, cn in enumerate(elems, 1):
        out.append(f"{i}, " + ", ".join(map(str, cn[:15])) + ",")
        out.append(", ".join(map(str, cn[15:])))
    out.append("*NSET, NSET=FIX_FACE")
    for i in range(0, len(root), 8):
        out.append(", ".join(map(str, root[i:i + 8])))
    out += ["*MATERIAL, NAME=MAT1", "*ELASTIC", f"{E:.0f}, {NU}",
            "*SOLID SECTION, ELSET=SOLID, MATERIAL=MAT1", ",",
            "*STEP", "*STATIC", "*BOUNDARY", "FIX_FACE, 1, 3", "*CLOAD"]
    for n in tip:
        out.append(f"{n}, 2, {per:.10g}")
    out += ["*NODE FILE", "U", "*EL FILE", "S", "*END STEP"]
    open(path, "w").write("\n".join(out) + "\n")
    return tip


# ---------------------------------------------------------------------------
# Seeded defects
# ---------------------------------------------------------------------------

def seed(good_text, tip_nodes, kind):
    if kind == "nonlinear":
        # plasticity active in a run presented as linear static. INV3 must
        # fail because the response no longer scales with the load. INV1 also
        # fails, correctly: the solve does not reach the full applied load, so
        # the reactions are short of the applied resultant.
        t = good_text.replace(
            "*SOLID SECTION",
            "*PLASTIC\n50.0, 0.0\n60.0, 0.02\n*SOLID SECTION", 1)
        return t.replace("*STATIC", "*STATIC\n0.25, 1.0", 1)

    if kind == "prescribed":
        # a prescribed nonzero displacement left in the deck. INV2 must fail,
        # because with every load removed the response is not zero.
        return good_text.replace(
            "FIX_FACE, 1, 3",
            f"FIX_FACE, 1, 3\n{tip_nodes[2]}, 3, 3, 0.05", 1)

    if kind == "wrongface":
        # the falsification case. The load is moved to a geometrically
        # plausible but wrong face. Every invariant must PASS, because a load
        # applied anywhere is still reacted, still linear, and still zero at
        # zero load. This case exists to keep the module honest about what it
        # does not cover.
        lines, out, mode = good_text.split("\n"), [], None
        for ln in lines:
            s = ln.strip()
            if s.startswith("*") and not s.startswith("**"):
                mode = s.lstrip("*").split(",")[0].strip().upper()
                out.append(ln)
                continue
            if mode == "CLOAD" and s:
                nid, dof, val = [t.strip() for t in s.split(",")]
                # shift the load to low-numbered nodes near the root
                out.append(f"{int(nid) % 40 + 1}, {dof}, {val}")
                continue
            out.append(ln)
        return "\n".join(out)

    raise ValueError(kind)


CASES = [
    # tag, seed kind, expected verdicts that MUST hold
    ("good", None, {"INV1_EQUILIBRIUM": "PASS",
                    "INV2_ZERO_LOAD": "PASS",
                    "INV3_LOAD_SCALING": "PASS"}),
    ("bad_nonlinear", "nonlinear", {"INV3_LOAD_SCALING": "FAIL"}),
    ("bad_prescribed", "prescribed", {"INV2_ZERO_LOAD": "FAIL"}),
    # documented blind spot, not a defect. See the module docstring.
    ("blindspot_wrongface", "wrongface", {"INV1_EQUILIBRIUM": "PASS",
                                          "INV2_ZERO_LOAD": "PASS",
                                          "INV3_LOAD_SCALING": "PASS"}),
]


def main():
    os.makedirs(WORK, exist_ok=True)
    good = os.path.join(WORK, "good.inp")
    tip = write_good_deck(good)
    good_text = open(good).read()

    print(f"{'case':22s} {'INV1':16s} {'INV2':16s} {'INV3':16s} result")
    print("-" * 90)
    bad = 0
    for tag, kind, expect in CASES:
        path = good
        if kind:
            path = os.path.join(WORK, tag + ".inp")
            open(path, "w").write(seed(good_text, tip, kind))
        fs = INV.run_invariants(path, workdir=os.path.join(WORK, tag + "_run"),
                                verbose=False)
        got = {f.rule: f.verdict for f in fs}
        ok = all(got.get(k) == v for k, v in expect.items())
        bad += 0 if ok else 1
        print(f"{tag:22s} {got.get('INV1_EQUILIBRIUM', ''):16s} "
              f"{got.get('INV2_ZERO_LOAD', ''):16s} "
              f"{got.get('INV3_LOAD_SCALING', ''):16s} "
              f"{'OK' if ok else 'MISMATCH, wanted ' + str(expect)}")

    print()
    if bad == 0:
        print("ALL CHECKS BEHAVE AS SPECIFIED")
        print("Note: blindspot_wrongface passing is the CORRECT result. These "
              "checks\ndo not detect a load placed on the wrong face. That is "
              "the face catalogue's job.")
    else:
        print(f"{bad} case(s) did not behave as specified")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
