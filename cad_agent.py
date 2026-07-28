#!/usr/bin/env python3
"""
cad_agent.py - a minimal text-to-CAD sub-agent.

Talk to it in plain language. It asks an LLM to write build123d code,
runs that code in a subprocess, reads any error, asks the LLM to repair,
and exports a STEP file. This is the "agent": a generate -> execute ->
repair -> export loop.

The same loop is reused later for a mesh agent (LLM writes Gmsh) and a
solver agent (LLM writes CalculiX). This harness is plumbing. The value
you sell is the VERIFICATION you attach to it, not this loop.

Requires:  pip install build123d anthropic
Set:       export ANTHROPIC_API_KEY=...     (or point call_llm at any model provider)
Run:       python cad_agent.py
"""
import os, sys, re, subprocess, tempfile

# Model name is CONFIG, not a fact to hardcode. Model names change.
# Pick a current one from https://docs.claude.com/en/api/overview
MODEL       = os.environ.get("CAD_AGENT_MODEL", "claude-sonnet-5")
MAX_REPAIRS = 3
OUT         = "part.step"

SYSTEM_PROMPT = (
    "You are a CAD code generator. Output ONLY Python code using the build123d library.\n"
    "Rules:\n"
    "- Define exactly one solid named `part`.\n"
    "- Import nothing except: from build123d import *\n"
    "- Do NOT call export_step or print. The harness does that.\n"
    "- Work in millimetres. Put named parameters at the top.\n"
    "- If you are given an ERROR from a previous attempt, fix it.\n"
    "Return only raw code: no markdown fences, no prose."
)

def strip_fences(t: str) -> str:
    t = t.strip()
    t = re.sub(r"^```[a-zA-Z0-9]*\n", "", t)
    t = re.sub(r"\n```$", "", t)
    return t.strip()

def call_llm(user_request: str, prior_code: str | None = None, error: str | None = None,
             image_path: str | None = None) -> str:
    """Ask the model for build123d code. image_path is optional: pass a
    photo or sketch of the part and the model sees it alongside your text."""
    from anthropic import Anthropic
    import base64
    client = Anthropic()

    content = [{"type": "text", "text": f"Part to build:\n{user_request}"}]
    if image_path:
        with open(image_path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        media_type = "image/png" if image_path.lower().endswith("png") else "image/jpeg"
        content.append({"type": "image",
                         "source": {"type": "base64", "media_type": media_type, "data": b64}})
    if error:
        content.append({"type": "text", "text":
            f"\n\nYour previous code FAILED with this error:\n{error}\n\n"
            f"Previous code:\n{prior_code}\n\nReturn corrected code only."})

    msg = client.messages.create(
        model=MODEL, max_tokens=2000, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    return strip_fences("".join(b.text for b in msg.content if b.type == "text"))

def build_script(code: str, out: str) -> str:
    """Wrap the LLM's code with harness-controlled export and sanity checks."""
    return (
        "from build123d import *\n"
        + code + "\n"
        + "assert 'part' in dir(), 'code did not define a solid named part'\n"
        + "bb = part.bounding_box()\n"
        + "print('BBOX', round(bb.size.X,3), round(bb.size.Y,3), round(bb.size.Z,3))\n"
        + "print('VOL', round(part.volume,3))\n"
        + f"export_step(part, {out!r})\n"
        + "print('OK')\n"
    )

def run_code(code: str, out: str):
    """Execute LLM code in a subprocess. Returns (ok, log).
    SECURITY: this runs arbitrary generated code. Fine on your own machine for
    a prototype. For a product, run it inside a locked-down container."""
    script = build_script(code, out)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        p = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "execution timed out"
    ok = (p.returncode == 0) and ("OK" in p.stdout)
    return ok, (p.stdout + "\n" + p.stderr).strip()

def generate(user_request: str, out: str = OUT, llm=call_llm, image_path: str | None = None):
    """The agent loop: generate, execute, repair, repeat."""
    code, err = None, None
    for attempt in range(1, MAX_REPAIRS + 2):
        code = llm(user_request, code, err, image_path=image_path)
        ok, log = run_code(code, out)
        if ok:
            print(f"[attempt {attempt}] SUCCESS")
            print(log)
            return code, out
        print(f"[attempt {attempt}] failed, repairing...")
        print(log[-600:])
        err = log
    raise RuntimeError("agent could not produce valid geometry after repairs")

if __name__ == "__main__":
    print("CAD agent ready. Describe a part in plain language, or type 'quit'.")
    while True:
        req = input("\npart> ").strip()
        if req.lower() in ("quit", "exit", ""):
            break
        img = input("image path (blank for none)> ").strip() or None
        try:
            _, path = generate(req, image_path=img)
            print(f"--> STEP written to {path}")
        except Exception as e:
            print("Failed:", e)
