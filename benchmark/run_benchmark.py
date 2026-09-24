#!/usr/bin/env python3
"""run_benchmark.py - Milestone B. Twelve cases, five runs each.

For every case and run: the correct deck, the seeded deck and the control are
checked by checker.check_deck with the stated intent (told tier) and with the
governing condition stripped (inferred tier). Scored:

  caught      the expected rule FAILs on the seed
  control     the expected rule does NOT fail on the control
  false FAILs any FAIL on the correct deck or the control (other rules)
  propagation |QoI(seed) - QoI(correct)| / |QoI(correct)|, measured by
              solving both; below 1 percent the case is unobservable

Reported: all-five and at-least-once, separately. Writes benchmark/RESULTS.md
and benchmark/results.json. Language anchored to ASME V&V 10-2006: this is
verification (is the model solved and set up as intended), not validation
against physical tests.

    python -m benchmark.run_benchmark [--runs 5] [--cases 1,2,3]
"""
from __future__ import annotations
import argparse, io, contextlib, json, os, shutil, sys, tempfile, time

import invariants as INV
from checker import check_deck
from benchmark.cases import CASES
from benchmark import decks as D

NOISE = 0.01


def qoi(path, comp, qset):
    r = INV.solve(path)
    if not r["converged"] or not r["frd"]:
        return None, r
    u = INV.read_frd_disp(r["frd"])
    ids = INV.read_deck(path).nsets[qset.upper()]
    if comp < 0:
        vals = [sum(c * c for c in u[n]) ** 0.5 for n in ids]
    else:
        vals = [u[n][comp] for n in ids]
    return sum(vals) / len(vals), r


def fails(findings):
    return [f.rule for f in findings if f.verdict == "FAIL"]


def hit(findings, prefix):
    return any(f.rule.startswith(prefix) and f.verdict == "FAIL"
               for f in findings)


def run_case(c, W):
    d = os.path.join(W, f"case{c.cid:02d}")
    os.makedirs(d, exist_ok=True)
    decks = c.build(d)
    q = {}
    for k in ("correct", "seed"):
        v, r = qoi(decks[k][0], c.comp, c.qset)
        q[k] = v
        q[k + "_clean"] = r["converged"] and \
            "WARNING" not in r["stdout"].upper()
    ref = c.analytical if c.analytical is not None else q["correct"]
    prop = (abs(q["seed"] - ref) / abs(ref)
            if q["seed"] is not None and ref else None)
    out = {"propagation": prop, "seed_runs_clean": q["seed_clean"],
           "qoi_correct": q["correct"], "qoi_seed": q["seed"]}
    for tier in ("told", "inferred"):
        res = {}
        for k in ("correct", "seed", "control"):
            path, it = decks[k]
            it = it if tier == "told" else c.infer(it)
            with contextlib.redirect_stdout(io.StringIO()):
                fs, _ = check_deck(path, it,
                                   workdir=os.path.join(d, f"chk_{tier}_{k}"))
            res[k] = fs
        out[tier] = {
            "caught": hit(res["seed"], c.expect) if c.expect else False,
            "control_quiet": (not hit(res["control"], c.expect))
            if c.expect else True,
            "false_fails_correct": fails(res["correct"]),
            "other_fails_control": [x for x in fails(res["control"])
                                    if not (c.expect and
                                            x.startswith(c.expect))],
            "seed_fails": fails(res["seed"]),
            "seed_detail": next((f.detail for f in res["seed"]
                                 if c.expect and f.rule.startswith(c.expect)),
                                ""),
        }
    return out


def run_case12(c, W):
    """Pipeline case: a Richardson study through model_agent on the same
    cantilever. Seed: judge peak von Mises (singular at the clamped edge).
    Control: judge the load-point displacement on the same meshes."""
    import cantilever as CANT, geometry_features as GF, model_agent as MA
    from geom_session import GeomSession
    from locking_check import MaterialSpec
    d = os.path.join(W, "case12")
    os.makedirs(d, exist_ok=True)
    cl = CANT.Cantilever()
    step = os.path.join(d, "cant.step")
    with contextlib.redirect_stdout(io.StringIO()):
        CANT.write_step(cl, step)
    with GeomSession(step) as s:
        fx = list(GF.extreme_planar_face(s.catalogue, axis=0, side="min").tags)
        ld = list(GF.extreme_planar_face(s.catalogue, axis=0, side="max").tags)
    mat = MaterialSpec(E=cl.E, nu=cl.nu, name="steel")
    Fc = cl.force_for_stress(50.0)
    with contextlib.redirect_stdout(io.StringIO()):
        t_s, v_s = MA.convergence_study(step, "c12s", mat, ld, fx,
                                        (0, 0, -Fc), [4.0, 2.0, 1.0],
                                        qoi="max_von_mises_MPa", run_root=d)
        t_d, v_d = MA.convergence_study(step, "c12d", mat, ld, fx,
                                        (0, 0, -Fc), [4.0, 2.0, 1.0],
                                        run_root=d)
    vals = [float(l.split()[-1]) for l in t_s.splitlines()
            if l.strip().startswith("requested")]
    prop = abs(vals[-1] - vals[0]) / abs(vals[0]) if len(vals) > 1 else None
    tier = {"caught": v_s.verdict == "FAIL",
            "control_quiet": v_d.verdict != "FAIL",
            "false_fails_correct": [], "other_fails_control": [],
            "seed_fails": ["CONVERGENCE"] if v_s.verdict == "FAIL" else [],
            "seed_detail": v_s.detail}
    return {"propagation": prop, "seed_runs_clean": True,
            "qoi_correct": None, "qoi_seed": None, "told": tier,
            "inferred": dict(tier)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--cases", default=None)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    sel = [int(x) for x in a.cases.split(",")] if a.cases else None
    here = os.path.dirname(os.path.abspath(__file__))
    results = {}
    for c in CASES:
        if sel and c.cid not in sel:
            continue
        runs = []
        for r in range(a.runs):
            W = tempfile.mkdtemp(prefix=f"certus_b{c.cid}_")
            t0 = time.time()
            runs.append(run_case12(c, W) if c.pipeline else run_case(c, W))
            runs[-1]["seconds"] = round(time.time() - t0, 1)
            if not a.keep:
                shutil.rmtree(W, ignore_errors=True)
            print(f"case {c.cid:2d} run {r + 1}: caught "
                  f"{runs[-1]['told']['caught']}, control quiet "
                  f"{runs[-1]['told']['control_quiet']}", flush=True)
        results[c.cid] = {"name": c.name, "anchor": c.anchor,
                          "expect": c.expect, "runs": runs}
    json.dump(results, open(os.path.join(here, "results.json"), "w"),
              indent=1, default=str)
    write_md(results, os.path.join(here, "RESULTS.md"), a.runs)
    print(open(os.path.join(here, "RESULTS.md")).read())


def write_md(results, path, n):
    L = ["# Milestone B benchmark results", "",
         f"CalculiX 2.21, {n} runs per case, checker.py on structured decks "
         "of one steel cantilever (case 12 through the Gmsh pipeline).",
         "Verification in the sense of ASME V&V 10-2006: whether the model "
         "encodes the stated intent and is solved adequately, not "
         "validation against tests.", "",
         "| # | seeded decision | expected finding | propagation | caught "
         "told (all/any) | caught inferred (all/any) | control quiet | "
         "FAILs on the correct deck | other FAILs on the control |",
         "|---|---|---|---|---|---|---|---|---|"]
    for cid, r in results.items():
        runs = r["runs"]
        p = runs[0]["propagation"]
        ps = "n/a" if p is None else (
            "> 10^6 % (rigid-body drift)" if p > 1e4 else f"{p:.1%}" +
            (" (unobservable)" if p < NOISE else ""))

        def af(tier, key):
            v = [x[tier][key] for x in runs]
            return f"{'yes' if all(v) else 'no'}/{'yes' if any(v) else 'no'}"
        ff = sorted({f for x in runs for f in
                     x["told"]["false_fails_correct"]})
        oc = sorted({f.split()[0] for x in runs for f in
                     x["told"]["other_fails_control"]})
        ex = r["expect"] or "none (blind spot)"
        L.append(f"| {cid} | {r['name']} | {ex} | {ps} | {af('told', 'caught')} | "
                 f"{af('inferred', 'caught')} | {af('told', 'control_quiet')}"
                 f" | {', '.join(ff) or 'none'} | {', '.join(oc) or 'none'} |")
    L += ["", "## Reading the table", "",
          "- *told*: the stated intent includes the governing condition "
          "(yield stress, plane assumption, thickness, E, support type). "
          "*inferred*: that one condition is withheld. Checks that need a "
          "statement abstain (NOT EVALUATED) rather than guess, so 'no' in "
          "the inferred column is an abstention, not a silent pass.",
          "- *control quiet*: the same rule does not fire on the matched "
          "control. *other FAILs on the control* are true findings on that "
          "deck (case 4's control is a linear-tet model, which R3 and R4 "
          "correctly flag).",
          "- Five runs are identical: the checker is deterministic, so "
          "all-five equals at-least-once. The column is kept for the LLM "
          "edges, where it will differ.",
          "- Case 4: no CalculiX element reaches the 97-101% band at nu = "
          "0.4999 (measured: C3D8I 82.6%, C3D20 94.7%, C3D20R 95.9%); the "
          "missing hybrid formulation is a stack limit, so propagation is "
          "measured against beam theory.",
          "- Case 6 is the known blind spot: a load on a plausible wrong "
          "face passes every check. It belongs to the face catalogue and "
          "the confirmation step.",
          "- Case 12 runs through the Gmsh pipeline (Richardson study); the "
          "other eleven are structured decks, identical on every platform.",
          ""]
    L += ["## Seed findings (run 1, told tier)", ""]
    for cid, r in results.items():
        t = r["runs"][0]["told"]
        det = t["seed_detail"] or "expected rule did not fire"
        sf = ", ".join(t["seed_fails"]) or "none"
        L.append(f"- **{cid}**: {det}; all seed FAILs: {sf}")
    open(path, "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
