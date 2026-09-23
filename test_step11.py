#!/usr/bin/env python3
"""test_step11.py - the convergence check can fail, on any STEP case.

Synthetic: f = 1 + h^2 gives p = 2 exactly; identical results, oscillation
and growth FAIL. Real: the validation cantilever, three sizes, mesh retries
off. Load-point displacement converges (PASS). Peak von Mises sits at the
clamped edge, a stress singularity, and must FAIL on the same meshes.
"""
import io, contextlib, os, shutil, sys, tempfile
import cantilever as CANT
import geometry_features as GF
import model_agent as MA
from geom_session import GeomSession
from locking_check import MaterialSpec
from results_check import richardson

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


v = richardson([(0.4, 1.16), (0.2, 1.04), (0.1, 1.01)])
check("synthetic 1 + h^2: observed order 2, extrapolates to 1",
      abs(v.p - 2) < 1e-9 and abs(v.extrapolated - 1) < 1e-9, v.detail)
check("identical results FAIL", richardson([(1, 2.), (.7, 2.), (.5, 2.)])
      .verdict == "FAIL")
check("oscillation FAIL", richardson([(1, 1.1), (.5, .95), (.25, 1.02)])
      .verdict == "FAIL")
check("growth FAIL", richardson([(1, 10.), (.5, 14.), (.25, 19.)])
      .verdict == "FAIL")

W = tempfile.mkdtemp(prefix="certus_s11_")
c = CANT.Cantilever()
F = c.force_for_stress(50.0)
step = os.path.join(W, "cant.step")
with contextlib.redirect_stdout(io.StringIO()):
    CANT.write_step(c, step)
with GeomSession(step) as s:
    fix = list(GF.extreme_planar_face(s.catalogue, axis=0, side="min").tags)
    load = list(GF.extreme_planar_face(s.catalogue, axis=0, side="max").tags)
mat = MaterialSpec(E=c.E, nu=c.nu, name="steel")
sizes = [4.0, 2.0, 1.0]
with contextlib.redirect_stdout(io.StringIO()):
    t1, v1 = MA.convergence_study(step, "cd", mat, load, fix, (0, 0, -F),
                                  sizes, run_root=W)
print(t1)
check("load-point displacement converges on the cantilever",
      v1.verdict == "PASS", v1.detail)
with contextlib.redirect_stdout(io.StringIO()):
    t2, v2 = MA.convergence_study(step, "cs", mat, load, fix, (0, 0, -F),
                                  sizes, qoi="max_von_mises_MPa", run_root=W)
print(t2)
check("peak stress at the clamped edge (singular) FAILs",
      v2.verdict == "FAIL", v2.detail)

shutil.rmtree(W, ignore_errors=True)
print("\nALL STEP 11 CHECKS BEHAVE AS SPECIFIED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
