#!/usr/bin/env python3
"""test_step6.py - the LLM visual review cannot veto a measured PASS.

Known-bad (old code): a visual answer match=false discarded a part that had
passed every measured check and regenerated it. Here every LLM call is
replaced by a stub, the measured checks pass, and the visual stub says
'mismatch'. The part must be accepted on attempt 1 with the discrepancy only
written to the report. No API key needed.
"""
import os, sys, tempfile
from certus import cad_agent as CA

calls = {"code": 0, "visual": 0}
W = tempfile.mkdtemp(prefix="certus_t6_")
CA.OUT_RPT = os.path.join(W, "rpt.txt")
CA.OUT_SPEC = os.path.join(W, "spec.json")

CA.call_llm_code = lambda *a, **k: calls.__setitem__("code", calls["code"] + 1) or "code"
CA.run_code = lambda code, s, t: (True, "", {"volume": 1.0})
CA.check_spec = lambda spec, meas: [{"name": "all", "status": "PASS",
                                     "critical": True, "detail": ""}]
CA.render_drawing = lambda *a, **k: os.path.join(W, "d.png")
CA.archive_cad_run = lambda *a, **k: None


def visual(*a, **k):
    calls["visual"] += 1
    return {"match": False, "confidence": "high",
            "discrepancies": ["lugs missing"], "fix_instructions": "add lugs"}


CA.call_llm_visual = visual
out = CA.generate("a bracket", spec={"part_name": "t"}, attempts=3,
                  visual=True, confirm=False)
rpt = open(CA.OUT_RPT).read()
ok = (calls["code"] == 1 and out["verdict"] == "PASS"
      and "ADVISORY VISUAL REVIEW" in rpt and "lugs missing" in rpt)
print(f"code generations: {calls['code']} (old code: 2+), verdict "
      f"{out['verdict']}, advisory note in report: "
      f"{'ADVISORY VISUAL REVIEW' in rpt}")
print("PASS  visual review is advisory only" if ok else "FAIL")
sys.exit(0 if ok else 1)
