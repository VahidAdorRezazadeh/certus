#!/usr/bin/env python3
"""test_step10.py - pressure load path end to end, and a seeded label error.

Known-good: 0.1 MPa on the top face of the validation cantilever. The
resultant is integrated from the CAD face (-100 N), the mode is computed
from it, the deck's *DSLOAD resultant matches it (check 1), and the tip
deflection is inside the Milestone A band against beam theory.
Known-bad: one face label in the *SURFACE block moved to the wrong element
face. CalculiX runs it clean; check 1 must FAIL on the resultant.
"""
import io, contextlib, json, os, re, shutil, sys, tempfile
import cantilever as CANT
import geometry_features as GF
import invariants as INV
import model_agent as MA
from geom_session import GeomSession
from locking_check import MaterialSpec

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


W = tempfile.mkdtemp(prefix="certus_s10_")
c = CANT.Cantilever()
step = os.path.join(W, "cant.step")
CANT.write_step(c, step)
with GeomSession(step) as s:
    fix = list(GF.extreme_planar_face(s.catalogue, axis=0, side="min").tags)
    top = list(GF.extreme_planar_face(s.catalogue, axis=2, side="max").tags)
p = 0.1
with contextlib.redirect_stdout(io.StringIO()):
    rd = MA.run(step, "press", MaterialSpec(E=c.E, nu=c.nu, name="steel"),
                top, fix, (0, 0, 0), load_kind="pressure", pressure=p,
                solvers=("calculix",), target_size=1.0,
                solve_with="calculix", run_root=W)
d = json.load(open(os.path.join(rd.path, "run.json")))
F = d.get("pressure_resultant_N")
check("pressure resultant from CAD is -p*L*b in z",
      F and abs(F[2] + p * c.L * c.b) < 1e-6 * p * c.L * c.b, F)
check("mode computed from it, not assumed",
      d["mode_for_meshing"] == {"mode": "bending",
                                "source": "COMPUTED BEFORE MESHING"},
      d["mode_for_meshing"])
chk = {x["rule"]: x for x in d.get("checks", [])}
check("check 1 PASS on the *DSLOAD resultant",
      chk.get("1 LOAD vs INTENT", {}).get("verdict") == "PASS",
      chk.get("1 LOAD vs INTENT", {}).get("detail", "")[:80])
deck = os.path.join(rd.path, "case_calculix", "case.inp")
dk = INV.read_deck(deck)
disp = INV.read_frd_disp(os.path.join(rd.path, "results", "case.frd"))
tip = [n for n, x in dk.nodes.items() if abs(x[0] - c.L) < 1e-6]
uz = abs(sum(disp[n][2] for n in tip) / len(tip))
q = p * c.b
ref = q * c.L ** 4 / (8 * c.E * c.I) + q * c.L ** 2 / (2 * 5 / 6 * c.G * c.A)
check("tip deflection inside the 97-101% band (EB + Timoshenko)",
      0.97 <= uz / ref <= 1.01, f"{uz:.6f} vs {ref:.6f} = {uz/ref:.2%}")
check("load-point displacement reported for a pressure",
      d.get("load_point_displacement_mm") is not None,
      d.get("load_point_displacement_mm"))

# seeded: move one surface group to the opposite label
text = open(deck).read()
m = re.search(r"^(_LOAD_FACE_S\d), (S\d)$", text, re.M)
wrong = {"S1": "S2", "S2": "S1", "S3": "S4", "S4": "S3"}[m.group(2)]
bad = os.path.join(W, "bad.inp")
open(bad, "w").write(text.replace(m.group(0), f"{m.group(1)}, {wrong}", 1))
r = INV.solve(bad)
clean = r["converged"] and "WARNING" not in r["stdout"].upper()
it = INV.Intent(force=tuple(F), units="N-mm-MPa", E_GPa=210.0,
                material_class="elastic", rate_dependent=False, nlgeom=False)
f = INV.check_load_intent(INV.read_deck(bad), it)
check(f"seed: {m.group(1)} relabelled {m.group(2)}->{wrong}: runs clean, "
      f"check 1 FAILs", clean and f.verdict == "FAIL", f.detail[:110])

shutil.rmtree(W, ignore_errors=True)
print("\nALL STEP 10 CHECKS BEHAVE AS SPECIFIED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
