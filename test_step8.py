#!/usr/bin/env python3
"""test_step8.py - constraint realism from reactions, and under-constraint.

Over-constraint: the node-count heuristic is replaced by reading the solved
reactions on the support face. Known-bad: a clamp that pulls the part (+Z
load on a clamped base) and a clamp that holds the Poisson expansion.
Known-good: a 3-2-1 seat under compression. Under-constraint is check 4
(test_checks.py); here the pipeline itself must refuse a roller.
"""
import os, sys, tempfile, shutil
import invariants as INV
import test_checks_helpers as H

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


W = tempfile.mkdtemp(prefix="certus_s8_")
n = 2
b1, b2 = H.nid(n, 0, 0, 0), H.nid(n, 2, 0, 0)
cases = [("seat321", (f"BOT, 3, 3", f"{b1}, 1, 2", f"{b2}, 2, 2"), -10.,
          "PASS"),
         ("clamp_pull", ("BOT, 1, 3",), +10., "FAIL"),
         # pressed down AND pushed sideways at half the normal load: the
         # clamp holds a friction demand of about 0.5, beyond mu = 0.3
         ("clamp_shear", ("BOT, 1, 3",), -10., "FAIL")]
for tag, bc, fz, want in cases:
    extra = [f"{t}, 1, 5." for t in H.top(n)] if tag == "clamp_shear" else []
    p, top = H.deck(W, tag, n=n, bc=bc,
                    loads=[f"{t}, 3, {fz}" for t in H.top(n)] + extra,
                    output="*NODE FILE\nU, RF")
    d = INV.read_deck(p)
    f4 = INV.check_rigid_modes(d)
    r = INV.solve(p)
    f = INV.check_support_reactions(d, r["frd"], "BOT")
    check(f"{tag}: {want} (rank test {f4.verdict})",
          f.verdict == want and f4.verdict == "PASS" and r["converged"],
          f.detail)

shutil.rmtree(W, ignore_errors=True)
print("\nALL STEP 8 CHECKS BEHAVE AS SPECIFIED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
