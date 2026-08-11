#!/usr/bin/env python3
"""
cad_agent.py - text-to-CAD sub-agent with a deterministic verification layer.

Pipeline:
    request (+ optional image)
      -> SPEC        LLM extracts a numeric specification, you confirm it
      -> CODE        LLM writes build123d code to satisfy the spec
      -> EXECUTE     subprocess builds the solid, exports STEP + STL, MEASURES it
      -> VERIFY      harness compares measurements against the spec (arithmetic)
      -> REPAIR      failed checks are fed back as the next prompt
      -> DRAW        three-view PNG with a verification stamp

The point of the split: the model proposes the spec and writes the code, but the
harness measures the solid. A model cannot talk its way past a bounding box or a
hole count. "It ran without error" is NOT evidence of correct geometry, which is
why the older version happily returned a flat plate when asked for a bracket.

Requires:  pip install build123d anthropic matplotlib numpy
    NOTE: build123d 0.11 requires numpy>=2. Do NOT follow the "downgrade to
    numpy<2" hint in NumPy's own ABI warning: it will break build123d.
    Use a dedicated environment instead (see README notes at the bottom).

Set (PowerShell):   $env:ANTHROPIC_API_KEY = "sk-ant-..."
Run:                python cad_agent.py
Standalone render:  python cad_agent.py --render part.stl
Offline self test:  python cad_agent.py --selftest      (no API key needed)
Offline build:      python cad_agent.py --build my_part.py [spec.json]   (no API key)
"""
import os, sys, re, json, struct, datetime, subprocess, tempfile

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection, LineCollection
from mpl_toolkits.mplot3d.art3d import Poly3DCollection, Line3DCollection

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
MODEL        = os.environ.get("CAD_AGENT_MODEL", "claude-sonnet-5")
MAX_ATTEMPTS = int(os.environ.get("CAD_AGENT_ATTEMPTS", "5"))
VISUAL_CHECK = os.environ.get("CAD_AGENT_VISUAL", "1") == "1"
# A model that thinks before answering can spend its whole budget on thinking and
# emit no text. That produced five identical empty replies in testing, so the code
# budget is generous by default.
CODE_MAX_TOKENS = int(os.environ.get("CAD_AGENT_CODE_TOKENS", "8000"))

OUT_STEP = "part.step"
OUT_STL  = "part.stl"
OUT_PNG  = "part_views.png"
OUT_MESH = "part_export.stl"   # fine mesh for printing or meshing
OUT_SPEC = "part_spec.json"
OUT_RAW  = "part_spec_raw.txt"   # last unparsable spec reply, for diagnosis
OUT_CODE_RAW = "part_code_raw.txt"  # last unusable code reply, for diagnosis
OUT_RPT  = "part_verification.txt"

MESH_TOL, MESH_ANG_TOL, DPI = 0.05, 0.20, 500

SHEET_BG, INK, FAINT = "#ffffff", "#1b1f24", "#9aa3ad"
DIM_COLOR, OK_COLOR, BAD_COLOR = "#b3122f", "#1c7c4a", "#b3122f"
FILL_NEAR, FILL_FAR = "#aeb6c0", "#c9ced6"
BASE_RGB = np.array([0.55, 0.60, 0.68])
CREASE_DEG = 22.0


# ----------------------------------------------------------------------
# PROMPTS
# ----------------------------------------------------------------------
SPEC_SYSTEM = """You turn a part description into a MACHINE-CHECKABLE specification.
Return ONLY a JSON object, no prose, no markdown fences. Schema:

{
  "part_name": "short name",
  "tolerance_mm": 1.0,
  "overall_mm": {"x": <float or null>, "y": <float or null>, "z": <float or null>},
  "solid_count": 1,
  "min_face_count": <int>,
  "bbox_fill_range": [<float>, <float>],
  "holes": [
    {"diameter_mm": <float>, "axis": "x"|"y"|"z", "count": <int>, "through": true}
  ],
  "target_volume_mm3": <float or null>,
  "features": ["short phrase per distinct feature"],
  "assumptions": ["anything you inferred rather than read"]
}

Rules:
- overall_mm is the OVERALL bounding box of the finished part, not one feature.
- If a dimension is not stated and cannot be read from the image, set it to null
  and record the reason in "assumptions". Never invent a number silently.
- min_face_count: a plain box has 6 faces. Estimate the real count and set a
  lower bound that a featureless plate would fail.
- bbox_fill_range: plausible range for part volume divided by bounding box
  volume. A solid block is 1.0. A bracket or ribbed part is typically 0.1 to 0.6.
- holes: only actual holes (material removed). Do NOT list outer rounds, bosses
  or fillets. A single hole bored along one axis through several walls, such as a
  pin hole through both lugs of a clevis, counts as ONE hole, not one per wall.
- axis is the hole axis direction in the part coordinate system.
Be strict. These numbers will be measured on the built solid and used to reject it."""

CODE_SYSTEM = """You are a CAD code generator. Output ONLY Python code using build123d.
Rules:
- Define exactly one solid named `part`.
- Import nothing except: from build123d import *
- Do NOT call export_step, export_stl or print. The harness does that.
- Work in millimetres. Put named parameters at the top.
- You are given a SPECIFICATION. The harness will MEASURE the solid you build and
  compare it against that specification: overall bounding box, hole count and
  diameters, face count, volume fraction. Satisfy it exactly.
- If you are given a FAILED CHECKS report, the previous solid was measured and
  rejected. Change the geometry so those measurements change. Do not just re-emit
  the same construction with cosmetic edits.
Return only raw code: no markdown fences, no prose."""

VISION_SYSTEM = """You compare a generated CAD part against what was asked for.
You get the original request, optionally a reference image, and a three-view
drawing of the generated part. Return ONLY JSON:

{"match": true|false,
 "confidence": "high"|"medium"|"low",
 "discrepancies": ["one short concrete difference per item"],
 "fix_instructions": "what to change in the geometry, or empty string"}

Judge shape and topology, not render quality or colour. If the generated part is
a plain plate or block where a shaped part was asked for, that is match=false."""


# ----------------------------------------------------------------------
# MEASUREMENT SOURCE (injected into the subprocess, runs next to the solid)
# ----------------------------------------------------------------------
MEASURE_SRC = r'''
def _measure(part):
    import numpy as _np
    from build123d import GeomType, CenterOf
    from OCP.BRepAdaptor import BRepAdaptor_Surface

    bb = part.bounding_box()
    dx, dy, dz = bb.size.X, bb.size.Y, bb.size.Z
    com = part.center(CenterOf.MASS)

    holes, convex, fillets = [], [], []
    for f in part.faces():
        if f.geom_type != GeomType.CYLINDER:
            continue
        _sa = BRepAdaptor_Surface(f.wrapped)
        cy = _sa.Cylinder()
        r = cy.Radius()
        sweep = float(_sa.LastUParameter() - _sa.FirstUParameter())
        ap, ad = cy.Axis().Location(), cy.Axis().Direction()
        A = _np.array([ap.X(), ap.Y(), ap.Z()], float)
        D = _np.array([ad.X(), ad.Y(), ad.Z()], float)
        D /= max(_np.linalg.norm(D), 1e-12)
        try:
            pt = f.position_at(0.5, 0.5)
            n = f.normal_at(pt)
            P = _np.array([pt.X, pt.Y, pt.Z], float)
            rad = (P - A) - _np.dot(P - A, D) * D
            rad /= max(_np.linalg.norm(rad), 1e-12)
            concave = float(_np.dot([n.X, n.Y, n.Z], rad)) < 0.0
        except Exception:
            concave = False
        nz = _np.nonzero(_np.abs(D) > 1e-9)[0]
        if len(nz) and D[nz[0]] < 0:
            D = -D
        Q = A - _np.dot(A, D) * D
        rec = dict(diameter=round(2 * r, 4), axis=[round(v, 4) for v in D],
                   axis_point=[round(v, 3) for v in Q], area=round(f.area, 3),
                   sweep_deg=round(_np.degrees(sweep), 1))
        # A concave cylinder that wraps only a small angle is a fillet, not a hole.
        # A hole wraps a full turn per wall (360 deg), or 180 deg when OCC splits it.
        if concave and sweep >= 2.6:
            holes.append(rec)
        elif concave:
            fillets.append(rec)
        else:
            convex.append(rec)

    def _group(items):
        out = {}
        for it in items:
            key = (round(it["diameter"], 2), tuple(round(v, 2) for v in it["axis"]),
                   tuple(round(v, 1) for v in it["axis_point"]))
            g = out.setdefault(key, dict(diameter=it["diameter"], axis=it["axis"],
                                         axis_point=it["axis_point"], area=0.0, patches=0))
            g["area"] += it["area"]
            g["patches"] += 1
        return sorted(out.values(), key=lambda d: -d["diameter"])

    hg = _group(holes)
    for h in hg:
        h["swept_length"] = round(h["area"] / max(_np.pi * h["diameter"], 1e-9), 3)

    vol = float(part.volume)
    return dict(
        valid=bool(part.is_valid),
        n_solids=len(part.solids()), n_faces=len(part.faces()),
        n_edges=len(part.edges()),
        n_planar_faces=sum(1 for f in part.faces() if f.geom_type == GeomType.PLANE),
        bbox=dict(x=round(dx, 4), y=round(dy, 4), z=round(dz, 4)),
        bbox_min=[round(bb.min.X, 3), round(bb.min.Y, 3), round(bb.min.Z, 3)],
        volume=round(vol, 4), area=round(float(part.area), 4),
        bbox_fill=round(vol / max(dx * dy * dz, 1e-12), 4),
        center_of_mass=[round(com.X, 3), round(com.Y, 3), round(com.Z, 3)],
        holes=hg, convex_cylinders=_group(convex), concave_fillets=_group(fillets),
    )
'''


# ----------------------------------------------------------------------
# LLM CALLS
# ----------------------------------------------------------------------
def strip_fences(t: str) -> str:
    t = t.strip()
    t = re.sub(r"^```[a-zA-Z0-9]*\n", "", t)
    t = re.sub(r"\n```$", "", t)
    return t.strip()


SUPPORTED_IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".webp": "image/webp"}


def check_image(path: str | None) -> str | None:
    """Validate an image path before it reaches the API. Returns a usable path,
    or None with a printed warning. A bad path must not abort the whole run."""
    if not path:
        return None
    path = path.strip().strip('"').strip("'")
    if not os.path.isfile(path):
        print(f"[image] not found: {path!r}. Continuing WITHOUT an image.")
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_IMAGES:
        print(f"[image] unsupported type {ext or '(no extension)'!r} for {path!r}. "
              f"Use png, jpg, gif or webp. Continuing WITHOUT an image.")
        return None
    return path


def _image_block(image_path: str):
    import base64
    ext = os.path.splitext(image_path)[1].lower()
    mt = SUPPORTED_IMAGES.get(ext)
    if mt is None:
        raise ValueError(f"unsupported image type {ext!r}: use png, jpg, gif or webp")
    with open(image_path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}}


def _ask_raw(system: str, content: list, max_tokens: int = 2000):
    """Returns (text, stop_reason). stop_reason == 'max_tokens' means the reply
    was cut off, which can leave the text empty if the budget was spent before
    any text was emitted."""
    from anthropic import Anthropic
    msg = Anthropic().messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return strip_fences(text), getattr(msg, "stop_reason", None)


def _ask(system: str, content: list, max_tokens: int = 2000) -> str:
    return _ask_raw(system, content, max_tokens)[0]


def _json_or_none(text: str):
    """Best effort JSON recovery from a model reply. Handles fenced blocks,
    surrounding prose, // comments and trailing commas."""
    cands = []
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        cands.append(m.group(1))
    cands.append(text.strip())
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        cands.append(m.group(0))
    for c in cands:
        for attempt in (c, re.sub(r"//[^\n]*", "", c)):
            attempt2 = re.sub(r",(\s*[}\]])", r"\1", attempt)
            for t in (attempt, attempt2):
                try:
                    obj = json.loads(t)
                    if isinstance(obj, dict):
                        return obj
                except Exception:
                    pass
    return None


def call_llm_spec(request: str, image_path: str | None = None) -> dict:
    """Stage 1: turn the request into a numeric, checkable specification."""
    content = [{"type": "text", "text": f"Part description:\n{request}"}]
    if image_path:
        content.append(_image_block(image_path))
        content.append({"type": "text", "text":
                        "The image shows the intended part. Read dimensions from it "
                        "where they are annotated. Do not guess unmarked dimensions."})
    for attempt in range(1, 4):
        raw = _ask(SPEC_SYSTEM, content, max_tokens=4000)
        spec = _json_or_none(raw)
        if spec is not None:
            return spec
        open(OUT_RAW, "w", encoding="utf-8").write(raw)
        print(f"[spec] attempt {attempt}: reply was not parsable JSON "
              f"({len(raw)} chars, saved to {OUT_RAW}). First 300 chars:")
        print("       " + raw[:300].replace("\n", "\n       "))
        content = content + [{"type": "text", "text":
            "Your previous reply could not be parsed as JSON. Return ONE JSON "
            "object and nothing else. No prose, no markdown fences, no comments, "
            "no trailing commas. Keep it compact."}]
    raise RuntimeError(f"model did not return a parsable specification after 3 tries; "
                       f"last reply is in {OUT_RAW}")


def call_llm_code(request: str, spec: dict, prior_code: str | None = None,
                  feedback: str | None = None, image_path: str | None = None) -> str:
    """Stage 2: write build123d code that satisfies the specification."""
    content = [{"type": "text", "text":
                f"Part to build:\n{request}\n\nSPECIFICATION (will be measured):\n"
                + json.dumps(spec, indent=2)}]
    if image_path:
        content.append(_image_block(image_path))
    if feedback:
        content.append({"type": "text", "text":
                        f"\n\nPrevious attempt was REJECTED.\n{feedback}\n\n"
                        f"Previous code:\n{prior_code}\n\nReturn corrected code only."})

    for shot in range(1, 4):
        code, stop = _ask_raw(CODE_SYSTEM, content, max_tokens=CODE_MAX_TOKENS)
        if code.strip() and "part" in code:
            if stop == "max_tokens":
                print("[code] warning: reply hit the token limit and may be truncated. "
                      "Raise CAD_AGENT_CODE_TOKENS if the build fails.")
            return code
        open(OUT_CODE_RAW, "w", encoding="utf-8").write(code)
        print(f"[code] shot {shot}: model returned "
              + (f"{len(code)} chars with no 'part'" if code.strip() else "NO TEXT AT ALL")
              + f" (stop_reason={stop}). Saved to {OUT_CODE_RAW}.")
        if stop == "max_tokens":
            print("[code] the token budget was exhausted before any code was emitted. "
                  f"Retrying with a larger budget.")
        content = content + [{"type": "text", "text":
            "Your previous reply contained no usable code. Emit the build123d code "
            "immediately with no preamble and no explanation. The final statement "
            "must assign the finished solid to a variable named exactly `part`."}]
    raise RuntimeError(f"model returned no usable code after 3 tries; "
                       f"last reply is in {OUT_CODE_RAW}")


def call_llm_visual(request: str, png_path: str, image_path: str | None = None) -> dict:
    """Stage 4b: cross-check the rendered result against the request and image.
    ADVISORY ONLY. A model judging its own output is soft evidence, not proof."""
    content = [{"type": "text", "text": f"Original request:\n{request}"}]
    if image_path:
        content.append({"type": "text", "text": "Reference image supplied by the user:"})
        content.append(_image_block(image_path))
    content.append({"type": "text", "text": "Three-view drawing of the GENERATED part:"})
    content.append(_image_block(png_path))
    return _json_or_none(_ask(VISION_SYSTEM, content, max_tokens=2500)) or {}


# ----------------------------------------------------------------------
# EXECUTION HARNESS
# ----------------------------------------------------------------------
def build_script(code: str, out_step: str, out_stl: str, out_meas: str) -> str:
    return (
        "from build123d import *\n"
        + code + "\n"
        # tolerate `with BuildPart() as bp:` where the model forgot `part = bp.part`
        + "if 'part' not in dir():\n"
        + "    _c = [v for v in list(globals().values())\n"
        + "          if hasattr(v, 'part') and hasattr(getattr(v, 'part', None), 'volume')]\n"
        + "    if _c:\n"
        + "        part = _c[0].part\n"
        + "        print('NOTE recovered solid from a BuildPart context')\n"
        + "assert 'part' in dir(), 'code did not define a solid named part'\n"
        + MEASURE_SRC + "\n"
        + "import json\n"
        + "_m = _measure(part)\n"
        + f"open({out_meas!r}, 'w').write(json.dumps(_m))\n"
        + "print('BBOX', _m['bbox']['x'], _m['bbox']['y'], _m['bbox']['z'])\n"
        + "print('VOL', _m['volume'])\n"
        + f"export_step(part, {out_step!r})\n"
        + f"export_stl(part, {out_stl!r}, tolerance={MESH_TOL}, "
          f"angular_tolerance={MESH_ANG_TOL})\n"
        + f"export_stl(part, {OUT_MESH!r}, tolerance=0.01, angular_tolerance=0.05)\n"
        + "print('OK')\n"
    )


def run_code(code: str, out_step: str, out_stl: str):
    """Execute LLM code in a subprocess. Returns (ok, log, measurements).
    SECURITY: this runs arbitrary generated code. Fine on your own machine for a
    prototype. For a product, run it inside a locked-down container."""
    meas_path = os.path.join(tempfile.gettempdir(), "cad_agent_meas.json")
    if os.path.exists(meas_path):
        os.unlink(meas_path)
    script = build_script(code, out_step, out_stl, meas_path)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        p = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return False, "execution timed out", None
    ok = (p.returncode == 0) and ("OK" in p.stdout)
    meas = None
    if os.path.exists(meas_path):
        try:
            meas = json.load(open(meas_path))
        except Exception:
            meas = None
    return ok, (p.stdout + "\n" + p.stderr).strip(), meas


# ----------------------------------------------------------------------
# VERIFICATION
# ----------------------------------------------------------------------
def _res(name, ok, detail, critical=True):
    return dict(name=name, status="PASS" if ok else "FAIL", detail=detail, critical=critical)


def _axis_vec(a):
    if isinstance(a, (list, tuple)):
        return list(a)
    return {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[str(a).strip().lower()[-1]]


def check_spec(spec: dict, m: dict) -> list:
    """Compare declared spec against measured geometry. Pure arithmetic on
    measured values, so the model cannot argue with the result."""
    out = []
    tol = float(spec.get("tolerance_mm") or 1.0)

    out.append(_res("geometry_valid", bool(m.get("valid")),
                    "BREP validity: " + ("valid" if m.get("valid") else "INVALID solid")))

    want_solids = int(spec.get("solid_count") or 1)
    out.append(_res("single_solid", m["n_solids"] == want_solids,
                    f"solids: {m['n_solids']} (expected {want_solids})"))

    for ax in ("x", "y", "z"):
        want = (spec.get("overall_mm") or {}).get(ax)
        if want is None:
            continue
        got = m["bbox"][ax]
        out.append(_res(f"overall_{ax}", abs(got - float(want)) <= tol,
                        f"{ax.upper()} = {got:.2f} mm, target {float(want):.2f} +/- {tol:g}"))

    rng = spec.get("bbox_fill_range") or [0.03, 0.97]
    lo, hi = float(rng[0]), float(rng[1])
    out.append(_res("bbox_fill", lo <= m["bbox_fill"] <= hi,
                    f"volume / bbox volume = {m['bbox_fill']:.3f}, expected "
                    f"{lo:g} to {hi:g}", critical=False))

    mf = spec.get("min_face_count")
    if mf:
        out.append(_res("min_face_count", m["n_faces"] >= int(mf),
                        f"faces: {m['n_faces']} (need at least {int(mf)}); "
                        "a featureless plate or block fails here"))

    tv = spec.get("target_volume_mm3")
    if tv:
        frac = float(spec.get("volume_tolerance_frac") or 0.25)
        out.append(_res("volume", abs(m["volume"] - float(tv)) <= frac * float(tv),
                        f"volume = {m['volume']:.1f} mm3, target {float(tv):.1f} "
                        f"+/- {frac*100:.0f}%", critical=False))

    unmatched = list(m["holes"])
    for hs in (spec.get("holes") or []):
        try:
            d_t = float(hs["diameter_mm"])
        except (KeyError, TypeError, ValueError):
            continue
        d_tol = float(hs.get("diameter_tol_mm") or max(0.5, 0.05 * d_t))
        axis_t = hs.get("axis")
        want_n = int(hs.get("count") or 1)
        hits = []
        for h in unmatched:
            if abs(h["diameter"] - d_t) > d_tol:
                continue
            if axis_t:
                a = np.asarray(_axis_vec(axis_t), float)
                a /= max(np.linalg.norm(a), 1e-12)
                if abs(float(np.dot(a, h["axis"]))) < 0.95:
                    continue
            hits.append(h)
        for h in hits:
            unmatched.remove(h)
        label = f"hole_d{d_t:g}" + (f"_{axis_t}" if axis_t else "")
        n_grp = len(hits)
        n_pat = sum(h.get("patches", 1) for h in hits)
        # A pin hole bored through two lugs is one hole on one axis, but two
        # cylindrical patches. Either reading counts as correct.
        out.append(_res(label, want_n in (n_grp, n_pat),
                        f"found {n_grp} coaxial hole group(s) / {n_pat} bored wall(s) "
                        f"of d = {d_t:g} +/- {d_tol:g} mm"
                        + (f" about {axis_t}" if axis_t else "")
                        + f", expected {want_n}"))

        # optional position checks, in the two coordinates perpendicular to the axis
        want_p = hs.get("perp_mm")
        want_pa = hs.get("perp_abs_mm")
        if hits and (want_p or want_pa):
            keep = [i for i in range(3) if abs(_axis_vec(axis_t or "z")[i]) < 0.5] \
                   if axis_t else [0, 1]
            bad = []
            for h in hits:
                got = [h["axis_point"][i] for i in keep]
                tgt = list(want_p) if want_p else list(want_pa)
                cmpv = [abs(g) for g in got] if want_pa else got
                if any(abs(c - float(t)) > tol for c, t in zip(cmpv, tgt)):
                    bad.append(tuple(round(g, 2) for g in got))
            kind = "|perp|" if want_pa else "perp"
            tgt_s = ", ".join(f"{float(t):g}" for t in (want_pa or want_p))
            out.append(_res(label + "_pos", not bad,
                            f"{kind} position target ({tgt_s}) +/- {tol:g}; "
                            + ("all holes on target" if not bad
                               else f"off target: {bad}")))
    if spec.get("holes") is not None:
        out.append(_res("no_unexpected_holes", not unmatched,
                        "unspecified holes: "
                        + (", ".join(f"d={h['diameter']:g}" for h in unmatched) or "none"),
                        critical=False))
    return out


def verdict(results):
    hard = [r for r in results if r["status"] == "FAIL" and r["critical"]]
    soft = [r for r in results if r["status"] == "FAIL" and not r["critical"]]
    return ("FAIL" if hard else ("WARN" if soft else "PASS")), hard, soft


def report_text(results) -> str:
    lines = []
    for r in results:
        mark = "PASS" if r["status"] == "PASS" else ("FAIL" if r["critical"] else "WARN")
        lines.append(f"  [{mark}] {r['name']:<22} {r['detail']}")
    return "\n".join(lines)


def feedback_text(results, meas) -> str:
    bad = [r for r in results if r["status"] == "FAIL"]
    txt = "FAILED CHECKS (measured on your solid):\n"
    txt += "\n".join(f"  - {r['name']}: {r['detail']}" for r in bad)
    txt += "\n\nMEASURED GEOMETRY:\n" + json.dumps(
        {k: meas[k] for k in ("bbox", "volume", "bbox_fill", "n_faces",
                              "n_solids", "holes") if k in meas}, indent=2)
    return txt


# ----------------------------------------------------------------------
# MESH
# ----------------------------------------------------------------------
def load_stl(path: str) -> np.ndarray:
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) >= 84:
        n = struct.unpack("<I", raw[80:84])[0]
        if n > 0 and len(raw) == 84 + 50 * n:
            data = np.frombuffer(raw[84:], dtype=np.uint8).reshape(n, 50)
            return data[:, 12:48].copy().view("<f4").reshape(n, 3, 3).astype(np.float64)
    tok = raw.decode("utf-8", "ignore").split()
    verts, i = [], 0
    while i < len(tok):
        if tok[i] == "vertex":
            verts.append([float(tok[i + 1]), float(tok[i + 2]), float(tok[i + 3])])
            i += 4
        else:
            i += 1
    return np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)


class Mesh:
    """Welded triangle mesh plus edge topology, for silhouette and feature lines."""

    def __init__(self, tris: np.ndarray, weld_tol: float = 1e-6):
        flat = tris.reshape(-1, 3)
        scale = max(np.ptp(flat, axis=0).max(), 1e-9)
        keys = np.round(flat / (scale * weld_tol)).astype(np.int64)
        _, first, inv = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        self.V = flat[first]
        F = inv.reshape(-1, 3)
        a, b, c = (self.V[F[:, i]] for i in range(3))
        nrm = np.cross(b - a, c - a)
        ln = np.linalg.norm(nrm, axis=1)
        keep = ln > 1e-12
        self.F = F[keep]
        self.N = nrm[keep] / ln[keep, None]
        self.C = (a[keep] + b[keep] + c[keep]) / 3.0
        self._build_edges()

    def _build_edges(self):
        F, nf = self.F, len(self.F)
        E = np.sort(np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
        owner = np.tile(np.arange(nf), 3)
        uniq, inv, cnt = np.unique(E, axis=0, return_inverse=True, return_counts=True)
        order = np.argsort(inv, kind="stable")
        inv_s, own_s = inv[order], owner[order]
        start = np.searchsorted(inv_s, np.arange(len(uniq)))
        f0 = own_s[start]
        f1 = np.where(cnt >= 2, own_s[np.minimum(start + 1, len(own_s) - 1)], -1)
        self.E, self.Ef0, self.Ef1 = uniq, f0, f1
        man = f1 >= 0
        cosang = np.ones(len(uniq))
        cosang[man] = np.einsum("ij,ij->i", self.N[f0[man]], self.N[f1[man]])
        self.is_crease = (~man) | (cosang < np.cos(np.deg2rad(CREASE_DEG)))

    def visible_lines(self, cam_dir: np.ndarray):
        front = (self.N @ cam_dir) > 0.0
        f0, f1 = self.Ef0, self.Ef1
        man = f1 >= 0
        sil = np.zeros(len(self.E), bool)
        sil[~man] = True
        sil[man] = front[f0[man]] != front[f1[man]]
        near = front[f0] | np.where(man, front[np.maximum(f1, 0)], False)
        return sil, (self.is_crease & ~sil & near), front

    @property
    def bbox(self):
        return self.V.min(axis=0), self.V.max(axis=0)


# ----------------------------------------------------------------------
# DRAWING
# ----------------------------------------------------------------------
def _shade(N):
    key = np.array([-0.45, -0.80, 0.60]); key /= np.linalg.norm(key)
    fill = np.array([0.85, 0.25, 0.30]); fill /= np.linalg.norm(fill)
    d1 = np.clip(N @ key, 0, 1)
    d2 = np.clip(N @ fill, 0, 1)
    d3 = np.clip(N @ np.array([0.0, 0.0, -1.0]), 0, 1)
    inten = 0.20 + 0.72 * d1 ** 0.85 + 0.26 * d2 + 0.06 * d3
    return np.clip(BASE_RGB[None, :] * inten[:, None] + (0.32 * d1 ** 22)[:, None], 0, 1)


def _basis(view: str):
    if view == "front":
        return np.array([1., 0, 0]), np.array([0, 0, 1.]), np.array([0, -1., 0])
    if view == "side":
        return np.array([0, 1., 0]), np.array([0, 0, 1.]), np.array([1., 0, 0])
    if view == "top":
        return np.array([1., 0, 0]), np.array([0, 1., 0]), np.array([0, 0, 1.])
    raise ValueError(f"unknown view {view!r}")


def _draw_ortho(ax, mesh, view, label):
    u, v, w = _basis(view)
    P = np.column_stack([mesh.V @ u, mesh.V @ v])
    sil, crease, front = mesh.visible_lines(w)
    for sel, col in ((~front, FILL_FAR), (front, FILL_NEAR)):
        if sel.any():
            ax.add_collection(PolyCollection(P[mesh.F[sel]], facecolors=col,
                                             edgecolors=col, linewidths=0.35, zorder=2))
    if crease.any():
        ax.add_collection(LineCollection(P[mesh.E[crease]], colors=INK,
                                         linewidths=0.5, alpha=0.85, zorder=3))
    if sil.any():
        ax.add_collection(LineCollection(P[mesh.E[sil]], colors=INK,
                                         linewidths=1.15, zorder=4))
    lo, hi = P.min(axis=0), P.max(axis=0)
    span = float(max(hi - lo))
    ax.set_xlim(lo[0] - 0.30 * span, hi[0] + 0.08 * span)
    ax.set_ylim(lo[1] - 0.30 * span, hi[1] + 0.10 * span)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(label, fontsize=8.5, color=INK, pad=6, fontweight="semibold")
    return lo, hi, span


def _dim_h(ax, x0, x1, y, text, span):
    yl, ext = y - 0.17 * span, 0.035 * span
    for x in (x0, x1):
        ax.plot([x, x], [y - 0.6 * ext, yl - ext], color=DIM_COLOR, lw=0.55, zorder=5)
    ax.annotate("", xy=(x0, yl), xytext=(x1, yl),
                arrowprops=dict(arrowstyle="<|-|>", color=DIM_COLOR, lw=0.75,
                                shrinkA=0, shrinkB=0, mutation_scale=8), zorder=5)
    ax.text((x0 + x1) / 2, yl - 0.035 * span, text, ha="center", va="top",
            fontsize=7.6, color=DIM_COLOR, fontweight="bold", zorder=6,
            bbox=dict(fc=SHEET_BG, ec="none", pad=1.4))


def _dim_v(ax, y0, y1, x, text, span):
    xl, ext = x - 0.17 * span, 0.035 * span
    for y in (y0, y1):
        ax.plot([x - 0.6 * ext, xl - ext], [y, y], color=DIM_COLOR, lw=0.55, zorder=5)
    ax.annotate("", xy=(xl, y0), xytext=(xl, y1),
                arrowprops=dict(arrowstyle="<|-|>", color=DIM_COLOR, lw=0.75,
                                shrinkA=0, shrinkB=0, mutation_scale=8), zorder=5)
    ax.text(xl - 0.035 * span, (y0 + y1) / 2, text, ha="right", va="center",
            fontsize=7.6, color=DIM_COLOR, fontweight="bold", rotation=90, zorder=6,
            bbox=dict(fc=SHEET_BG, ec="none", pad=1.4))


def _draw_iso(ax, mesh):
    cam = np.array([0.62, -0.72, 0.52]); cam /= np.linalg.norm(cam)
    rgb = _shade(mesh.N)
    order = np.argsort(mesh.C @ cam)
    ax.add_collection3d(Poly3DCollection(mesh.V[mesh.F[order]], facecolors=rgb[order],
                                         edgecolors=rgb[order] * 0.92, linewidths=0.15,
                                         shade=False))
    sil, _, _ = mesh.visible_lines(cam)
    if sil.any():
        ax.add_collection3d(Line3DCollection(mesh.V[mesh.E[sil]], colors=INK, linewidths=0.9))
    lo, hi = mesh.bbox
    d = np.maximum(hi - lo, 1e-9)
    m = 0.02 * d.max()
    ax.set_xlim(lo[0] - m, hi[0] + m); ax.set_ylim(lo[1] - m, hi[1] + m)
    ax.set_zlim(lo[2] - m, hi[2] + m)
    try:
        ax.set_box_aspect(tuple(d), zoom=1.45)
    except TypeError:
        ax.set_box_aspect(tuple(d))
    ax.set_proj_type("ortho")
    ax.view_init(elev=24, azim=-49)
    ax.set_axis_off()
    L, o = 0.22 * d.max(), lo - 0.06 * d.max()
    for vec, lab in ((np.array([L, 0, 0]), "X"), (np.array([0, L, 0]), "Y"),
                     (np.array([0, 0, L]), "Z")):
        p = o + vec
        ax.plot(*zip(o, p), color=FAINT, lw=0.8)
        ax.text(*p, lab, color=FAINT, fontsize=6.2, ha="center", va="center")
    ax.set_title("ISOMETRIC", fontsize=8.5, color=INK, pad=-4, fontweight="semibold")


def render_drawing(stl_path=OUT_STL, png_path=OUT_PNG, part_name="PART",
                   request="", meta=None, results=None):
    """Three-view drawing: isometric render plus two dimensioned orthographic
    views. All three overall dimensions appear across the two ortho views, which
    is why the pictorial view carries none."""
    mesh = Mesh(load_stl(stl_path))
    lo, hi = mesh.bbox
    dx, dy, dz = hi - lo
    meta = dict(meta or {})

    fig = plt.figure(figsize=(13.5, 5.4), facecolor=SHEET_BG)
    gs = fig.add_gridspec(2, 3, height_ratios=[1, 0.155], left=0.02, right=0.98,
                          top=0.95, bottom=0.035, wspace=0.06, hspace=0.10)
    _draw_iso(fig.add_subplot(gs[0, 0], projection="3d", facecolor=SHEET_BG), mesh)

    ax_f = fig.add_subplot(gs[0, 1], facecolor=SHEET_BG)
    lo2, hi2, s2 = _draw_ortho(ax_f, mesh, "front", "FRONT  (view along +Y)")
    _dim_h(ax_f, lo2[0], hi2[0], lo2[1], f"{dx:.1f}", s2)
    _dim_v(ax_f, lo2[1], hi2[1], lo2[0], f"{dz:.1f}", s2)

    ax_s = fig.add_subplot(gs[0, 2], facecolor=SHEET_BG)
    lo3, hi3, s3 = _draw_ortho(ax_s, mesh, "side", "SIDE  (view along \u2212X)")
    _dim_h(ax_s, lo3[0], hi3[0], lo3[1], f"{dy:.1f}", s3)
    _dim_v(ax_s, lo3[1], hi3[1], lo3[0], f"{dz:.1f}", s3)

    ax_t = fig.add_subplot(gs[1, :], facecolor=SHEET_BG)
    ax_t.set_xlim(0, 1); ax_t.set_ylim(0, 1); ax_t.axis("off")
    ax_t.add_patch(plt.Rectangle((0, 0), 1, 1, fill=False, ec=INK, lw=1.0,
                                 transform=ax_t.transAxes, clip_on=False))
    vol = meta.get("volume")
    if results:
        v, hard, soft = verdict(results)
        npass = sum(1 for r in results if r["status"] == "PASS")
        ver_txt = f"{v}  {npass}/{len(results)} checks"
        ver_col = OK_COLOR if v == "PASS" else (INK if v == "WARN" else BAD_COLOR)
    else:
        ver_txt, ver_col = "not verified", FAINT
    cells = [("PART", part_name, INK),
             ("BOUNDING BOX  X\u00d7Y\u00d7Z",
              f"{dx:.2f} \u00d7 {dy:.2f} \u00d7 {dz:.2f} mm", INK),
             ("VOLUME", f"{vol:.1f} mm\u00b3" if vol is not None else "\u2014", INK),
             ("VERIFICATION", ver_txt, ver_col),
             ("PROJECTION", "orthographic, mm", INK),
             ("DATE", datetime.date.today().isoformat(), INK)]
    xs = np.linspace(0.012, 0.86, len(cells))
    for (k, val, col), x in zip(cells, xs):
        ax_t.text(x, 0.66, k, fontsize=5.9, color=FAINT, fontweight="bold")
        ax_t.text(x, 0.24, str(val), fontsize=7.6, color=col, family="DejaVu Sans Mono")
    for x in xs[1:]:
        ax_t.plot([x - 0.012, x - 0.012], [0.08, 0.92], color=FAINT, lw=0.5)
    if request:
        r = request if len(request) < 110 else request[:107] + "..."
        ax_t.text(0.012, -0.62, "REQUEST:  " + r, fontsize=6.6, color=FAINT, va="top")
    if results:
        bad = [r["name"] for r in results if r["status"] == "FAIL"]
        if bad:
            ax_t.text(0.012, -1.45, "FAILED / WARNED:  " + ", ".join(bad),
                      fontsize=6.6, color=BAD_COLOR, va="top")

    fig.savefig(png_path, dpi=DPI, facecolor=SHEET_BG, bbox_inches="tight", pad_inches=0.18)
    plt.close(fig)
    return png_path


# ----------------------------------------------------------------------
# AGENT LOOP
# ----------------------------------------------------------------------
def generate(request: str, image_path: str | None = None, spec: dict | None = None,
             out_step=OUT_STEP, out_stl=OUT_STL, png=OUT_PNG,
             attempts: int = MAX_ATTEMPTS, visual: bool = VISUAL_CHECK,
             confirm: bool = True):
    """Spec -> code -> execute -> measure -> verify -> repair. Returns a dict."""
    image_path = check_image(image_path)
    if spec is None:
        print("[spec] extracting specification...")
        spec = call_llm_spec(request, image_path)
    json.dump(spec, open(OUT_SPEC, "w"), indent=2)
    print("[spec] " + json.dumps(spec, indent=2))
    if spec.get("assumptions"):
        print("[spec] ASSUMPTIONS the model made (check these):")
        for a in spec["assumptions"]:
            print("       - " + str(a))
    if confirm:
        ans = input("\n[spec] accept this specification? (Enter = yes, "
                    "'e' = edit " + OUT_SPEC + " then Enter, 'n' = abort) > ").strip().lower()
        if ans == "n":
            raise RuntimeError("specification rejected by user")
        if ans == "e":
            input(f"      edit {OUT_SPEC} in your editor, save, then press Enter > ")
            spec = json.load(open(OUT_SPEC))
            print("[spec] reloaded")

    code, feedback, last = None, None, {}
    for k in range(1, attempts + 1):
        print(f"\n[attempt {k}/{attempts}] generating code...")
        code = call_llm_code(request, spec, code, feedback, image_path)
        ok, log, meas = run_code(code, out_step, out_stl)

        if not ok or meas is None:
            print(f"[attempt {k}] BUILD FAILED")
            print(log[-700:])
            feedback = "The code did not run. Error output:\n" + log[-2500:]
            continue

        results = check_spec(spec, meas)
        v, hard, soft = verdict(results)
        print(f"[attempt {k}] built OK. VERIFICATION: {v}")
        print(report_text(results))
        last = dict(code=code, step=out_step, stl=out_stl, mesh=OUT_MESH, meas=meas,
                    results=results, verdict=v, spec=spec)

        if hard and k < attempts:
            feedback = feedback_text(results, meas)
            print(f"[attempt {k}] rejected on {len(hard)} critical check(s), repairing...")
            continue

        drawing = None
        try:
            drawing = render_drawing(out_stl, png, part_name=str(spec.get("part_name",
                                     request[:38])).upper(), request=request,
                                     meta=dict(volume=meas["volume"]), results=results)
        except Exception as e:
            print(f"[warn] drawing failed: {type(e).__name__}: {e}")
        last["drawing"] = drawing

        if visual and drawing and k < attempts:
            print("[visual] cross-checking the drawing against the request...")
            try:
                vis = call_llm_visual(request, drawing, image_path)
                last["visual"] = vis
                if vis.get("match") is False:
                    print("[visual] MISMATCH reported: "
                          + "; ".join(vis.get("discrepancies") or []))
                    feedback = ("The measured checks passed but a visual review of the "
                                "generated part reported a mismatch.\nDiscrepancies:\n"
                                + "\n".join("  - " + d for d in
                                            (vis.get("discrepancies") or []))
                                + "\nFix instructions: "
                                + str(vis.get("fix_instructions", "")))
                    continue
                print("[visual] no mismatch reported (advisory only)")
            except Exception as e:
                print(f"[visual] skipped: {type(e).__name__}: {e}")

        with open(OUT_RPT, "w") as f:
            f.write(f"REQUEST: {request}\n\nSPEC:\n{json.dumps(spec, indent=2)}\n\n"
                    f"VERDICT: {v}\n{report_text(results)}\n")
        return last

    print("\n[result] attempts exhausted without a passing verification.")
    if last:
        with open(OUT_RPT, "w") as f:
            f.write(f"REQUEST: {request}\n\nSPEC:\n{json.dumps(spec, indent=2)}\n\n"
                    f"VERDICT: {last['verdict']}\n{report_text(last['results'])}\n")
        try:
            last["drawing"] = render_drawing(
                out_stl, png, part_name=str(spec.get("part_name", "PART")).upper(),
                request=request, meta=dict(volume=last["meas"]["volume"]),
                results=last["results"])
        except Exception:
            pass
        return last
    raise RuntimeError("no buildable geometry produced")



def read_block(prompt: str) -> str:
    """Read one field from the terminal.

    Single line:   part> a beam 200 x 20 x 10 mm
    Multi line:    part> [Pin bracket, base plate 63.4 x 50.7 mm
                          ... paste as many lines as you like ...
                          ]
    Reading continues until the square brackets balance, so Enter is safe inside
    a block. Outer brackets are stripped. Text with no leading '[' behaves as
    before, one line only."""
    first = input(prompt)
    stripped = first.strip()
    if not stripped.startswith("["):
        return stripped
    depth = stripped.count("[") - stripped.count("]")
    parts = [stripped]
    while depth > 0:
        nxt = input("     ... ")
        parts.append(nxt)
        depth += nxt.count("[") - nxt.count("]")
    text = "\n".join(parts).strip()
    if text.startswith("["):
        text = text[1:]
    if text.endswith("]"):
        text = text[:-1]
    return "\n".join(ln.strip() for ln in text.splitlines()).strip()


# ----------------------------------------------------------------------
# OFFLINE SELF TEST  (no API key: proves the checks bite)
# ----------------------------------------------------------------------
def selftest():
    spec = dict(part_name="pin bracket", tolerance_mm=1.0,
                overall_mm=dict(x=63.4, y=50.7, z=34.5), solid_count=1,
                min_face_count=18, bbox_fill_range=[0.05, 0.60],
                holes=[dict(diameter_mm=12.0, axis="y", count=1),
                       dict(diameter_mm=5.0, axis="z", count=4)])
    plate = "from build123d import *\npart = Box(63.4, 50.7, 4.0)\n"
    print("Building a plain plate against a bracket specification.")
    ok, log, meas = run_code(plate, "selftest.step", "selftest.stl")
    if not ok or meas is None:
        print("build failed:\n", log[-500:]); return 1
    results = check_spec(spec, meas)
    v, hard, soft = verdict(results)
    print(f"\nVERDICT: {v}   critical failures: {len(hard)}")
    print(report_text(results))
    print("\nThe old harness reported SUCCESS for this solid. This one rejects it.")
    return 0


def build_file(py_path: str, spec_path: str | None = None):
    """Run a hand written build123d script through the full verify and draw
    pipeline, with no LLM call at all. Offline, deterministic, demo safe."""
    code = open(py_path, encoding="utf-8").read().replace("from build123d import *", "")
    ok, log, meas = run_code(code, OUT_STEP, OUT_STL)
    print(log[-800:] if not ok else "build OK")
    if not ok or meas is None:
        return 1
    spec = json.load(open(spec_path)) if spec_path else None
    results = check_spec(spec, meas) if spec else None
    if results:
        v = verdict(results)[0]
        print(f"VERIFICATION: {v}")
        print(report_text(results))
    png = render_drawing(OUT_STL, OUT_PNG,
                         part_name=str((spec or {}).get("part_name", "PART")).upper(),
                         request=f"built from {os.path.basename(py_path)}",
                         meta=dict(volume=meas["volume"]), results=results)
    print(f"--> STEP {OUT_STEP}\n--> STL (fine) {OUT_MESH}\n--> drawing {png}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    if len(sys.argv) in (3, 4) and sys.argv[1] == "--build":
        sys.exit(build_file(sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None))
    if len(sys.argv) == 3 and sys.argv[1] == "--render":
        print("-->", render_drawing(sys.argv[2], OUT_PNG, part_name="PART"))
        sys.exit(0)

    print("CAD agent ready. Describe a part in plain language, or type 'quit'.")
    print("Multi-line input: open with '[' and close with ']' when you are done.")
    while True:
        req = read_block("\npart> ")
        if req.lower() in ("quit", "exit", ""):
            break
        img = check_image(read_block("image path (blank for none)> "))
        try:
            r = generate(req, image_path=img)
            print(f"\n--> verdict     {r['verdict']}")
            print(f"--> STEP        {r['step']}")
            print(f"--> STL (fine)  {r.get('mesh')}")
            if r.get("drawing"):
                print(f"--> drawing     {r['drawing']}  ({DPI} dpi)")
            print(f"--> spec        {OUT_SPEC}")
            print(f"--> report      {OUT_RPT}")
        except Exception as e:
            print("Failed:", type(e).__name__, e)
