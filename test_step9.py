#!/usr/bin/env python3
"""test_step9.py - interface defects: menus, --solvers, fill band, footer."""
import builtins, io, contextlib, sys, tempfile
import model_agent as MA
import cad_agent as CA
from run_dir import RunDir

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def feed(answers):
    it = iter(answers)
    builtins.input = lambda *a, **k: next(it)


opts = ["-Z", "+Z", "-Y"]
feed(["0", "7", "2"])
with contextlib.redirect_stdout(io.StringIO()):
    got = MA._pick("dir?", opts, 0)
check("menu: '0' and '7' are re-asked, '2' is the 2nd option", got == "+Z",
      got)
feed(["x", "y", "z"])
try:
    with contextlib.redirect_stdout(io.StringIO()):
        MA._pick("dir?", opts, 0)
    check("menu: 3 bad answers stop the run", False)
except SystemExit:
    check("menu: 3 bad answers stop the run (no silent default)", True)
feed([""])
check("menu: Enter still takes the default", MA._pick("d", opts, 1) == "+Z")
feed(["1OO", "250"])
with contextlib.redirect_stdout(io.StringIO()):
    v = MA._number("N?", 100.0)
check("number: a typo is re-asked, not replaced by 100", v == 250.0, v)

# --solvers on the STEP path replaces the solver question
seen = {}
MA.ask_face = lambda cat, role: type("G", (), {"tags": [1]})()
MA.run = lambda *a, **k: seen.update(k)
feed(["", "", "", "", "", ""])         # material, goal, kind, dir, N, hold
sys.argv = ["model_agent.py", "part.step", "--solvers", "calculix"]
with contextlib.redirect_stdout(io.StringIO()):
    MA.main()
check("--solvers calculix is used on the STEP path",
      seen.get("solvers") == ("calculix",), seen.get("solvers"))

# fill band: an invented band is not a check; a stated one is
meas = dict(bbox=(63.4, 50.7, 33.04), volume=13207.5, bbox_fill=0.124,
            n_faces=34, n_solids=1, holes=[], convex_cylinders=[],
            concave_fillets=[])
for src, want in ((None, "PASS"), ("user", "FAIL")):
    spec = {"bbox_fill_range": [0.15, 0.45], "overall_mm": {}}
    if src:
        spec["bbox_fill_range_source"] = src
    try:
        res = CA.check_spec(spec, meas)
        r = [x for x in res if x["name"] == "bbox_fill"][0]
        check(f"fill band {'stated' if src else 'model-invented'} "
              f"[0.15, 0.45] on true 0.124: {want}", r["status"] == want,
              r["detail"])
    except Exception as e:
        check("fill band check runs", False, repr(e))

# footer says what it can back
d = tempfile.mkdtemp()
rd = RunDir("t", root=d, solvers=())
text = open(rd.write_report()).read()
check("footer names inputs taken as given and LLM use",
      "Not computed here" in text and "Language model use in this run: none"
      in text and "Every number above was produced" not in text)

print("\nALL STEP 9 CHECKS BEHAVE AS SPECIFIED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
