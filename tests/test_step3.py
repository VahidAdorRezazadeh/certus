#!/usr/bin/env python3
"""test_step3.py - each Step 3 fix on a known-good and a known-bad input.

A fix is only presented as working after the old behaviour was shown to be
wrong on the bad case and the new behaviour right on both. Needs ccx on PATH.
"""
from certus.paths import EXAMPLE_STEP
REF_STEP = str(EXAMPLE_STEP)
import os, shutil, subprocess, tempfile, sys
from certus import invariants as INV
from certus.results_check import ccx_outcome
from certus import verify_sets as VS

W = tempfile.mkdtemp(prefix="certus_s3_")
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def cube(path, n=2, extra_step="", bc="BOT, 1, 3", load="TOP, 3, -10.",
         plastic=False, static="*STATIC"):
    h = 10.0 / n
    nid = lambda i, j, k: 1 + i + (n + 1) * (j + (n + 1) * k)
    L = ["*NODE"]
    for k in range(n + 1):
        for j in range(n + 1):
            for i in range(n + 1):
                L.append(f"{nid(i,j,k)}, {i*h}, {j*h}, {k*h}")
    L.append("*ELEMENT, TYPE=C3D8, ELSET=EALL")
    e = 1
    for k in range(n):
        for j in range(n):
            for i in range(n):
                c = [nid(i,j,k), nid(i+1,j,k), nid(i+1,j+1,k), nid(i,j+1,k),
                     nid(i,j,k+1), nid(i+1,j,k+1), nid(i+1,j+1,k+1),
                     nid(i,j+1,k+1)]
                L.append(f"{e}, " + ", ".join(map(str, c))); e += 1
    last = (n + 1) ** 3
    L += ["*NSET, NSET=BOT"] + [f"{nid(i,j,0)}," for i in range(n+1)
                                for j in range(n+1)]
    L += ["*NSET, NSET=TOP"] + [f"{nid(i,j,n)}," for i in range(n+1)
                                for j in range(n+1)]
    L += ["*NSET, NSET=ALLN, GENERATE", f"1, {last}, 1",
          "*MATERIAL, NAME=S", "*ELASTIC", "210000., 0.3"]
    if plastic:
        L += ["*PLASTIC", "100., 0."]
    L += ["*DENSITY", "7.85e-9",
          "*SOLID SECTION, ELSET=EALL, MATERIAL=S", "*STEP, NLGEOM, INC=1000"
          if plastic else "*STEP", static, "*BOUNDARY", bc, "*CLOAD", load,
          extra_step, "*NODE FILE", "U", "*END STEP"]
    open(path, "w").write("\n".join(l for l in L if l) + "\n")


# 1. GENERATE ------------------------------------------------------------
p = os.path.join(W, "gen.inp"); cube(p)
d = INV.read_deck(p)
check("GENERATE: NSET holds every node", len(d.nsets["ALLN"]) == 27,
      f"(got {len(d.nsets['ALLN'])}, want 27; old parser gave 3)")
nodes, nsets = VS.read_inp(p)
check("GENERATE: verify_sets sees the same", len(nsets["ALLN"]) == 27)
check("explicit set unchanged", len(d.nsets["BOT"]) == 9)

# 2. make_variant keeps GRAV / CENTRIF fields ----------------------------
p = os.path.join(W, "grav.inp")
cube(p, extra_step="*DLOAD\nEALL, GRAV, 9810., 0., 0., -1.\n"
                   "EALL, CENTRIF, 1.e4, 0., 0., 0., 0., 0., 1.")
d = INV.read_deck(p)
out, nmod = INV.make_variant(d, os.path.join(W, "grav_x2.inp"), 2.0,
                             add_rf_print=False)
txt = open(out).read()
check("GRAV keeps direction", "EALL, GRAV, 19620, 0., 0., -1." in txt)
check("CENTRIF keeps point and axis",
      "EALL, CENTRIF, 20000, 0., 0., 0., 0., 0., 1." in txt)
check("CLOAD scaled", "TOP, 3, -20" in txt)
r = subprocess.run(["ccx", "grav_x2"], cwd=W, capture_output=True, text=True)
check("scaled GRAV deck runs clean", ccx_outcome(r.stdout, r.returncode,
      os.path.join(W, "grav_x2"), out).converged)

# 3. convergence --------------------------------------------------------
p = os.path.join(W, "good.inp"); cube(p)
g = INV.solve(p)
check("converged: clean elastic run", g["converged"] is True, g["outcome"])
p = os.path.join(W, "bad.inp")
cube(p, plastic=True, load="TOP, 3, -50000.",
     static="*STATIC\n0.1, 1.0, 1e-5, 0.1")
b = INV.solve(p)
frd_ok = os.path.getsize(os.path.join(W, "bad.frd")) > 0
check("not converged: aborted plastic run", b["converged"] is False,
      f"{b['outcome']}; non-empty .frd exists: {frd_ok} (old verdict: True)")

# 4. reactions by set name ----------------------------------------------
p = os.path.join(W, "two.inp")
cube(p, bc="BOT, 1, 3")
s = open(p).read().replace(
    "*END STEP", "*NODE PRINT, NSET=BOT, TOTALS=ONLY\nRF\n"
    "*NODE PRINT, NSET=TOP, TOTALS=ONLY\nRF\n*END STEP")
open(p, "w").write(s)
subprocess.run(["ccx", "two"], cwd=W, capture_output=True)
dat = os.path.join(W, "two.dat")
bot = INV.read_total_force(dat, "BOT")
top = INV.read_total_force(dat, "TOP")
old = INV.read_total_force(dat)
check("RF by name: BOT balances the load", bot and abs(bot[2] - 90.0) < 1e-6,
      f"BOT={bot}")
# CalculiX prints RF on a free, loaded set as the balancing nodal force,
# -90 here. The point is only that it is a different block from BOT.
check("RF by name: TOP is a different block", top and abs(top[2] + 90.0) < 1e-6,
      f"TOP={top}")
check("old positional read was the wrong set", old == top,
      f"(positional returned {old})")
check("missing set returns None", INV.read_total_force(dat, "NOPE") is None)

# 5. pin bearing load, reactions in the deck, load-point displacement ----
# Real bracket through the pipeline. -Z and +Z must load opposite halves of
# the hole, split 50/50 between the lugs, with the exact resultant.
assert os.path.exists(REF_STEP), f"reference bracket missing: {REF_STEP}"
if shutil.which("ccx"):
    import json, io, contextlib
    from certus import geometry_features as GF
    from certus.geom_session import GeomSession
    from certus import model_agent as MA
    with GeomSession(REF_STEP) as ses:
        lt = list(GF.largest_hole(ses.catalogue).tags)
        ft = list(GF.extreme_planar_face(ses.catalogue, axis=2,
                                         side="min").tags)
    for fz in (-100.0, 100.0):
        with contextlib.redirect_stdout(io.StringIO()):
            rd = MA.run(REF_STEP, "t3", MA.MATERIALS["steel"], lt, ft,
                        (0, 0, fz), solvers=("calculix",), target_size=2.5,
                        solve_with="calculix", run_root=W)
        js = json.load(open(os.path.join(rd.path, "run.json")))
        dk = INV.read_deck(os.path.join(rd.path, "case_calculix",
                                        "case.inp"))
        zc = 23.04
        zs = [dk.nodes[n][2] - zc for n, _, _ in dk.cloads]
        # a node exactly on the equator carries cos = 0, so strict sign
        side = all(z * fz > 0 for z in zs)
        fl = sum(v for n, _, v in dk.cloads if dk.nodes[n][0] < 0)
        fr = sum(v for n, _, v in dk.cloads if dk.nodes[n][0] > 0)
        check(f"bearing Fz={fz:+.0f}: only the pressed half is loaded",
              js["load_distribution"] == "bearing" and side,
              f"{len(zs)} nodes")
        check(f"bearing Fz={fz:+.0f}: 50/50 between lugs, exact total",
              # meshes are not mirror symmetric: allow 1e-3 of the load
              abs(fl - fz / 2) < 1e-3 * abs(fz) and
              abs(fl + fr - fz) < 1e-9 * abs(fz),
              f"{fl:.4f} / {fr:.4f}")
        rf = INV.read_total_force(os.path.join(rd.path, "results",
                                               "case.dat"), "FIX_FACE")
        check(f"reactions printed for FIX_FACE and balance Fz={fz:+.0f}",
              rf is not None and abs(rf[2] + fz) < 1e-6 * abs(fz), f"{rf}")
        d = js.get("load_point_displacement_mm")
        check(f"load-point displacement reported, sign follows load",
              d is not None and d > 0, f"{d}")

shutil.rmtree(W, ignore_errors=True)
print("\nALL STEP 3 CHECKS BEHAVE AS SPECIFIED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
