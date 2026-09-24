#!/usr/bin/env python3
"""
test_gui.py - drive the whole GUI chain with no real language model.

A stub OpenAI-compatible server (the same protocol Ollama and LM Studio
speak) answers each language edge with a fixed reply. Streamlit's AppTest
then clicks through all six stages: prompt -> intent -> spec -> build ->
faces -> details -> CalculiX solve -> verdict.

Two runs:
  good    the stub reads the prompt faithfully. Expect a verdict page with a
          solved result and the load on the hole.
  lying   the stub puts 5000 N and aluminium in the form, words the user
          never wrote. Expect both rejected and asked for in stage 5, so the
          run cannot start until a human fills them.

    python test_gui.py
"""

from __future__ import annotations
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PROMPT = ("An L-shaped steel bracket, base 60 x 50 mm, 8 mm thick, upright "
          "wall 30 mm high with a 10 mm hole near the top. A pin in the hole "
          "pulls 2 kN downwards. The bottom face is bolted to the table. "
          "Does it yield? Yield is 250 MPa.")

INTENT_GOOD = {
    "part": {"value": "L-shaped bracket, base 60 x 50 mm, 8 mm thick, wall "
                      "30 mm high, 10 mm hole",
             "quote": "An L-shaped steel bracket"},
    "question": {"value": "Does it yield?", "quote": "Does it yield?"},
    "load_feature": {"value": "the hole", "quote": "A pin in the hole"},
    "fix_feature": {"value": "the bottom face",
                    "quote": "The bottom face is bolted"},
    "load_kind": {"value": "force", "quote": "pulls 2 kN"},
    "force_N": {"value": 2000, "quote": "pulls 2 kN downwards"},
    "direction": {"value": "-Z", "quote": "pulls 2 kN downwards"},
    "material": {"value": "steel", "quote": "steel bracket"},
    "goal": {"value": "does it yield", "quote": "Does it yield?"},
    "support": {"value": "fully fixed (all translations)",
                "quote": "bolted to the table"},
    "yield_MPa": {"value": 250, "quote": "Yield is 250 MPa"},
}
INTENT_LYING = dict(INTENT_GOOD,
                    force_N={"value": 5000, "quote": "pulls 2 kN downwards"},
                    material={"value": "aluminium",
                              "quote": "an aluminium bracket"})

SPEC = {"part_name": "L bracket", "tolerance_mm": 0.5,
        "overall_mm": {"x": 60.0, "y": 50.0, "z": 38.0},
        "solid_count": 1, "min_face_count": 8,
        "holes": [{"diameter_mm": 10.0, "axis": "y", "count": 1,
                   "through": True}],
        "target_volume_mm3": 37771.7,
        "features": ["base plate 60x50x8", "wall 60x8x30 at +Y edge",
                     "hole d10 along Y through the wall at Z=30"],
        "assumptions": ["hole centre 8 mm below the top of the wall"]}

CODE = """
base = Box(60, 50, 8, align=(Align.CENTER, Align.CENTER, Align.MIN))
wall = Pos(0, 21, 8) * Box(60, 8, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
hole = Pos(0, 21, 30) * Rot(90, 0, 0) * Cylinder(5, 20)
part = base + wall - hole
"""

MODE = {"intent": INTENT_GOOD}


def _select(user_text: str) -> dict:
    phrase = user_text.split("Phrase:")[-1].strip().lower()
    groups = []
    for line in user_text.splitlines():
        m = re.search(r"id=(\d+), kind=(\w+)", line)
        if m:
            n = re.search(r"normal=\(([^)]*)\)", line)
            groups.append((m.group(1), m.group(2), n.group(1) if n else ""))
    if "hole" in phrase:
        ids = [int(i) for i, k, _ in groups if k == "hole"]
    else:
        ids = [int(i) for i, k, n in groups
               if n and n.replace(" ", "").endswith("-1.00")]
    return {"group_ids": ids[:1], "confident": len(ids) == 1,
            "reason": "stub"}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "stub-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = req["messages"][0]["content"]
        user = req["messages"][1]["content"]
        user = user if isinstance(user, str) else " ".join(
            b.get("text", "") for b in user)
        if "fill a form" in system:
            out = json.dumps(MODE["intent"])
        elif system.startswith("You turn a part description"):
            out = json.dumps(SPEC)
        elif system.startswith("You are a CAD code generator"):
            out = "```python\n" + CODE + "\n```"
        elif system.startswith("You compare a generated CAD part"):
            out = json.dumps({"match": True, "discrepancies": []})
        elif "map an engineer's phrase" in system:
            out = json.dumps(_select(user))
        else:
            out = "OK"
        body = json.dumps({"choices": [{"message": {"content": out},
                                        "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def click(at, label):
    for b in at.button:
        if b.label == label:
            b.click()
            return at.run()
    raise AssertionError(f"no button {label!r}; have "
                         f"{[b.label for b in at.button]}")


def drive(port: int, lying: bool):
    from streamlit.testing.v1 import AppTest
    MODE["intent"] = INTENT_LYING if lying else INTENT_GOOD
    os.environ["CERTUS_LLM_PROVIDER"] = "openai"
    import llm
    llm.CALL_LOG.clear()
    at = AppTest.from_file("app.py", default_timeout=900)
    at.run()
    at.sidebar.selectbox[0].set_value("other")
    at.run()
    at.sidebar.text_input[0].set_value(f"http://127.0.0.1:{port}/v1")
    at.run()
    at.text_area[0].input(PROMPT)
    at.run()
    at = click(at, "Read my request")
    assert not at.exception, at.exception
    assert at.session_state.stage == 1, at.session_state.stage
    table = at.dataframe[0].value
    status = dict(zip(table["field"], table["status"]))
    print("  stage 2 form:", {k: v.split()[0] for k, v in status.items()})
    if lying:
        assert "not accepted" in status["force (N)"], status
        assert "not accepted" in status["material"], status
    else:
        assert all("read" in v for k, v in status.items()
                   if k != "pressure (MPa)"), status
        assert "not stated" in status["pressure (MPa)"]

    at = click(at, "Build the part")
    assert not at.exception, at.exception
    print("  stage 3:", [s.value for s in at.success] or
          [e.value for e in at.error])
    at = click(at, "Use this part")
    assert at.session_state.stage == 3
    if at.exception or at.error:
        print("  stage 4 errors:", [e.value for e in at.exception],
              [e.value for e in at.error], [c.value for c in at.code][:1])
    lg, fg = at.selectbox[0].value, at.selectbox[1].value
    cat = at.session_state.cat
    print(f"  stage 4 suggestion: load group {lg} "
          f"({cat.group(lg).kind if lg is not None else '-'}), "
          f"fixed group {fg}")
    assert lg is not None and cat.group(lg).kind == "hole"
    assert fg is not None and cat.group(fg).normal[2] < -0.99
    at.checkbox[0].check()
    at.run()
    at = click(at, "Next")
    assert at.session_state.stage == 4
    run_btn = [b for b in at.button if b.label == "Run the simulation"][0]
    warns = [w.value for w in at.warning]
    print("  stage 5:", warns or "all values present")
    if lying:
        # the lying values were dropped: two questions stay open, and the
        # run button stays disabled until a human answers them
        assert run_btn.disabled and any("2 value" in w for w in warns), warns
        return "lying run blocked at stage 5 as intended"
    assert not run_btn.disabled
    at = click(at, "Run the simulation")
    assert not at.exception, at.exception
    rd = at.session_state.rd
    m = json.load(open(os.path.join(rd.path, "run.json")))
    print("  stage 6 headline:", m["headline_verdict"][:160])
    print("  answer:", " ".join(at.markdown[i].value for i in
                                range(len(at.markdown)))[:0] or "")
    assert m["result_trustworthy"] is not None, m["headline_verdict"]
    assert abs(m["force_N"][2] + 2000) < 1e-6
    roles = [c["role"] for c in llm.CALL_LOG]
    print("  language model roles used:", sorted(set(roles)))
    return (f"good run reached a verdict: trustworthy="
            f"{m['result_trustworthy']}, vM max "
            f"{m.get('max_von_mises_MPa'):.3g} MPa, u "
            f"{m.get('load_point_displacement_mm'):.3g} mm")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    import hashlib
    ref = lambda: hashlib.sha1(open("part.step", "rb").read()).hexdigest()
    before = ref()
    results = []
    for lying in (False, True):
        print(f"\n=== {'lying' if lying else 'good'} model ===")
        results.append(drive(port, lying))
    srv.shutdown()
    assert ref() == before, "the GUI overwrote the reference part.step"
    results.append("reference part.step untouched")
    print("\nRESULT")
    for r in results:
        print("  " + r)
    sys.exit(0)
