#!/usr/bin/env python3
"""
app.py - Certus in a browser tab. Local web page, nothing leaves the machine
unless the Claude API is chosen as the language model.

    pip install streamlit plotly
    streamlit run app.py

Six stages. The language model works only where the user's words are read
(stages 1 and 3). Everything else is the same deterministic code the command
line uses: cad_agent checks, the feature catalogue, model_agent.run, the
seven checks, the trust verdict.

    1 ASK        prompt, optional sketch or photo, optional STEP file
    2 UNDERSTOOD what was read from the text (with the words it came from),
                 and the part specification with its assumptions
    3 PART       build and measure the part (skipped if a STEP was given)
    4 FACES      3D view: which feature carries the load, which is held
    5 DETAILS    every value the run needs; missing ones are asked here
    6 VERDICT    solve, verdict, numbers, pictures, what was and was not checked
"""

from __future__ import annotations
import contextlib
import io
import json
import os
import shutil
import time

import streamlit as st

import llm
import intent as INT

# Streamlit runs this script in a worker thread. gmsh.initialize() installs a
# Ctrl-C signal handler by default, and Python allows that only in the main
# thread, so every Gmsh call in the chain would fail here. The GUI has no
# terminal Ctrl-C to catch, so the handler is switched off for this process.
import gmsh as _gmsh
if not getattr(_gmsh.initialize, "_certus_gui", False):
    _gmsh_init = _gmsh.initialize

    def _init(argv=[], readConfigFiles=True, run=False, interruptible=False):
        return _gmsh_init(argv, readConfigFiles, run, interruptible)
    _init._certus_gui = True
    _gmsh.initialize = _init

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "runs", "gui")
STAGES = ["Ask", "Understood", "Part", "Faces", "Details", "Verdict"]

st.set_page_config(page_title="Certus", page_icon="🔩", layout="wide")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def captured(label: str):
    """Run a block, keep its terminal output, show it in an expander."""
    buf = io.StringIO()
    err = None
    with contextlib.redirect_stdout(buf):
        try:
            yield buf
        except Exception as e:          # shown, never swallowed
            err = e
    log = buf.getvalue()
    st.session_state.setdefault("logs", []).append((label, log))
    if err is not None:
        st.error(f"{label} failed: {type(err).__name__}: {err}")
        with st.expander("terminal output"):
            st.code(log[-6000:] or "(none)")
        st.stop()


def S():
    return st.session_state


def go(stage: int):
    S().stage = stage
    st.rerun()


def session_dir() -> str:
    if "dir" not in S():
        S().dir = os.path.join(WORK, time.strftime("%Y-%m-%d_%H%M%S"))
        os.makedirs(S().dir, exist_ok=True)
    return S().dir


def save_upload(up, name=None) -> str:
    p = os.path.join(session_dir(), name or up.name)
    with open(p, "wb") as f:
        f.write(up.getbuffer())
    return p


def badge(status: str) -> str:
    return {"read": "✅ read from your text",
            "rejected": "⚠️ not accepted",
            "missing": "❓ not stated"}[status]


# ---------------------------------------------------------------------------
# sidebar: the language model and the solver
# ---------------------------------------------------------------------------

def sidebar():
    sb = st.sidebar
    sb.title("Certus")
    sb.caption("Physics is computed, never generated.")
    sb.subheader("Language model")
    kind = sb.radio("Where does the language model run?",
                    ["On this computer (Ollama, LM Studio, ...)",
                     "Claude API (cloud)"],
                    index=0 if llm.CONFIG.provider == "openai" else 1)
    if kind.startswith("Claude"):
        model = sb.text_input("model", llm.CONFIG.model if
                              llm.CONFIG.provider == "anthropic"
                              else "claude-sonnet-5")
        llm.configure(provider="anthropic", model=model)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sb.warning("ANTHROPIC_API_KEY is not set in this shell.")
    else:
        presets = {"Ollama": llm.DEFAULT_BASE["ollama"],
                   "LM Studio": llm.DEFAULT_BASE["lmstudio"], "other": None}
        names = list(presets)
        start = next((i for i, n in enumerate(names)
                      if presets[n] == llm.CONFIG.base_url), 2)
        preset = sb.selectbox("server", names, index=start)
        base = sb.text_input("server address",
                             presets[preset] or llm.CONFIG.base_url)
        llm.configure(provider="openai", base_url=base)
        models = llm.list_models()
        if models:
            cur = llm.CONFIG.model if llm.CONFIG.model in models else models[0]
            model = sb.selectbox("model", models, index=models.index(cur))
        else:
            sb.caption("server not reachable, or it lists no models. "
                       "Type the model name.")
            model = sb.text_input(
                "model", "" if llm.CONFIG.model.startswith("claude")
                else llm.CONFIG.model, placeholder="for example qwen2.5vl:32b")
        llm.configure(model=model)
        sb.caption("A sketch or photo needs a vision model "
                   "(for example qwen2.5vl or llama3.2-vision in Ollama). "
                   "Writing build123d code needs a strong coding model.")
    if sb.button("Test the connection"):
        ok, msg = llm.ping()
        (sb.success if ok else sb.error)(msg)

    sb.subheader("Solver")
    ccx = shutil.which("ccx") or shutil.which("ccx.exe")
    if ccx:
        sb.success(f"CalculiX found: {ccx}")
    else:
        sb.error("CalculiX (ccx) is not on PATH. Decks are written but "
                 "nothing is solved, so no verdict is possible.")
    S().ccx = bool(ccx)

    sb.subheader("Language model calls so far")
    if llm.CALL_LOG:
        for c in llm.CALL_LOG[-12:]:
            sb.caption(f"{c['role']}: {c['model']} "
                       f"({c['chars']} chars, stop={c['stop']})")
    else:
        sb.caption("none")
    if sb.button("Start over"):
        for k in list(S().keys()):
            del S()[k]
        llm.CALL_LOG.clear()
        st.rerun()


def header():
    cur = S().get("stage", 0)
    cols = st.columns(len(STAGES))
    for i, (c, name) in enumerate(zip(cols, STAGES)):
        mark = "●" if i == cur else ("✓" if i < cur else "○")
        c.markdown(f"**{mark} {i+1}. {name}**" if i == cur
                   else f"{mark} {i+1}. {name}")
    st.divider()


# ---------------------------------------------------------------------------
# 1 ASK
# ---------------------------------------------------------------------------

EXAMPLE = ("An L-shaped steel bracket, base 60 x 50 mm, 8 mm thick, upright "
           "wall 30 mm high with a 10 mm hole near the top. A pin in the hole "
           "pulls 2 kN downwards. The bottom face is bolted to the table. "
           "Does it yield? Yield is 250 MPa.")


def stage_ask():
    st.header("What do you want to know?")
    st.write("Describe the part, where the load goes, what holds it, and "
             "what you want to find out. Certus will ask for anything "
             "missing.")
    prompt = st.text_area("Your question", S().get("prompt", ""),
                          height=150, placeholder=EXAMPLE)
    c1, c2 = st.columns(2)
    img = c1.file_uploader("Sketch or photo of the part (optional)",
                           type=["png", "jpg", "jpeg", "webp", "gif"])
    stp = c2.file_uploader("I already have a CAD file (STEP, optional)",
                           type=["step", "stp"])
    if img is not None:
        c1.image(img, width=320)
    if st.button("Read my request", type="primary",
                 disabled=not prompt.strip()):
        S().prompt = prompt
        S().image = save_upload(img) if img is not None else None
        S().step = save_upload(stp, "input.step") if stp is not None else None
        with st.spinner("The language model is reading your request..."):
            try:
                S().intent = INT.read_intent(prompt, S().image)
            except Exception as e:
                st.warning(f"The language model could not read the request "
                           f"({type(e).__name__}: {e}). You can fill every "
                           f"value by hand in stage 5.")
                S().intent = INT.validate({}, prompt)
            S().spec = None
            if not S().step:
                import cad_agent as CA
                part_text = S().intent.get("part") or prompt
                with captured("specification"):
                    S().spec = CA.call_llm_spec(part_text, S().image)
        go(1)


# ---------------------------------------------------------------------------
# 2 UNDERSTOOD
# ---------------------------------------------------------------------------

LABELS = {"part": "part", "question": "your question",
          "load_feature": "load acts on", "fix_feature": "held at",
          "load_kind": "load kind", "force_N": "force (N)",
          "direction": "direction", "pressure_MPa": "pressure (MPa)",
          "material": "material", "goal": "goal",
          "support": "support", "yield_MPa": "yield stress (MPa)"}


def stage_understood():
    st.header("What Certus understood")
    it: INT.Intent = S().intent
    left, right = st.columns([1, 1])
    with left:
        st.subheader("From your text")
        st.caption("A value is kept only if the model can quote the words "
                   "you used, and any number must match the number and unit "
                   "you wrote. Nothing here is a default.")
        rows = []
        for k in INT.FIELDS:
            f = it.fields.get(k, INT.Field())
            rows.append({"field": LABELS[k],
                         "value": "" if f.value is None else str(f.value),
                         "status": badge(f.status),
                         "your words / reason": f.quote if f.status == "read"
                         else f.note})
        st.dataframe(rows, hide_index=True, width="stretch")
    with right:
        if S().step:
            st.subheader("Your CAD file")
            st.write(f"`{os.path.basename(S().step)}` will be used as it is. "
                     "No part is generated.")
        else:
            st.subheader("Part specification (will be measured)")
            spec = S().spec or {}
            if spec.get("assumptions"):
                st.warning("The model ASSUMED these. Check them:\n\n" +
                           "\n".join(f"- {a}" for a in spec["assumptions"]))
            txt = st.text_area("specification (edit if wrong)",
                               json.dumps(spec, indent=2), height=380)
            try:
                S().spec = json.loads(txt)
            except json.JSONDecodeError as e:
                st.error(f"not valid JSON: {e}")
    c1, c2 = st.columns([1, 5])
    if c1.button("Back"):
        go(0)
    if c2.button("Use the CAD file" if S().step else "Build the part",
                 type="primary"):
        go(3 if S().step else 2)


# ---------------------------------------------------------------------------
# 3 PART
# ---------------------------------------------------------------------------

def stage_part():
    import cad_agent as CA
    st.header("The part")
    if "cad" not in S():
        with st.spinner("Writing build123d code, building, measuring. "
                        "A local model can take several minutes."):
            # cad_agent writes part.step, part_spec.json ... into the
            # current folder. Run it inside this session's folder so the
            # reference bracket in the repo is never overwritten. (chdir is
            # process wide: fine for a one-user local app.)
            with captured("CAD generation"):
                os.chdir(session_dir())
                try:
                    S().cad = CA.generate(
                        S().intent.get("part") or S().prompt, S().image,
                        spec=S().spec, confirm=False)
                finally:
                    os.chdir(HERE)
    cad = S().cad
    for k in ("step", "drawing"):
        if cad.get(k) and not os.path.isabs(cad[k]):
            cad[k] = os.path.join(session_dir(), cad[k])
    v = cad.get("verdict", "")
    passed = v.startswith("PASS")
    (st.success if passed else st.error)(f"Measured verification: {v}")
    c1, c2 = st.columns([3, 2])
    if cad.get("drawing") and os.path.exists(cad["drawing"]):
        c1.image(cad["drawing"])
    c2.text(CA.report_text(cad.get("results", [])))
    if cad.get("visual", {}).get("match") is False:
        c2.info("Advisory visual review by the language model (it cannot "
                "change the verdict): " +
                "; ".join(cad["visual"].get("discrepancies") or []))
    b1, b2, b3 = st.columns([1, 1, 4])
    if b1.button("Back to the specification"):
        S().pop("cad")
        go(1)
    if b2.button("Build again"):
        S().pop("cad")
        st.rerun()
    if passed:
        if b3.button("Use this part", type="primary"):
            S().step = cad["step"]      # already inside the session folder
            go(3)
    else:
        b3.warning("Certus does not simulate a part that failed its own "
                   "measurements. Fix the specification or build again.")


# ---------------------------------------------------------------------------
# 4 FACES
# ---------------------------------------------------------------------------

def _suggest(phrase, cat):
    """Language edge: phrase -> group id. Pre-fills the menu only when the
    model is confident about exactly one group. The user still confirms."""
    if not phrase:
        return None, "no phrase in your text"
    import geometry_features as GF
    try:
        r = GF.resolve_selection(phrase, cat)
    except Exception as e:
        return None, f"model call failed: {e}"
    ids = r.get("group_ids", [])
    if r.get("confident") and len(ids) == 1:
        return ids[0], f"'{phrase}' → group {ids[0]} ({r.get('reason', '')})"
    return None, (f"'{phrase}' is ambiguous: candidates {ids}. "
                  f"{r.get('reason', '')}")


def stage_faces():
    import viewer
    import geometry_features as GF
    st.header("Where is the load, and what holds the part?")
    if "tri" not in S():
        with st.spinner("Reading faces..."):
            with captured("face catalogue"):
                S().cat, S().tri = viewer.face_mesh(S().step)
            it = S().intent
            S().sug_load = _suggest(it.get("load_feature"), S().cat)
            S().sug_fix = _suggest(it.get("fix_feature"), S().cat)
    cat, tri = S().cat, S().tri
    groups = {g.group_id: g for g in cat.groups}
    opts = list(groups)
    fmt = lambda gid: groups[gid].summary()

    left, right = st.columns([2, 3])
    with left:
        sl, sf = S().sug_load, S().sug_fix
        st.caption(f"Suggestion for the load: {sl[1]}")
        lg = st.selectbox("Load acts on", opts, format_func=fmt,
                          index=opts.index(sl[0]) if sl[0] in opts else None,
                          placeholder="choose the feature")
        st.caption(f"Suggestion for the support: {sf[1]}")
        fg = st.selectbox("Held fixed at", opts, format_func=fmt,
                          index=opts.index(sf[0]) if sf[0] in opts else None,
                          placeholder="choose the feature")
        for gid, role in ((lg, "LOAD (red)"), (fg, "FIXED (blue)")):
            if gid is not None:
                st.text(f"{role}\n" + groups[gid].describe(cat.bbox_min,
                                                          cat.bbox_max))
                w = GF.sliver_warning(groups[gid])
                if w:
                    st.warning(w)
        same = lg is not None and lg == fg
        if same:
            st.error("The load and the support are the same feature.")
        ok = st.checkbox("I have looked at the 3D view: red is where the "
                         "load acts and blue is what is held.",
                         disabled=lg is None or fg is None or same)
    with right:
        hover = {}
        for g in cat.groups:
            for t in g.tags:
                hover[t] = g.summary()
        st.plotly_chart(viewer.faces_figure(
            tri, groups[lg].tags if lg is not None else (),
            groups[fg].tags if fg is not None else (), hover),
            width="stretch")
    c1, c2 = st.columns([1, 5])
    if c1.button("Back"):
        go(1)
    if c2.button("Next", type="primary", disabled=not ok):
        S().load_gid, S().fix_gid = lg, fg
        go(4)


# ---------------------------------------------------------------------------
# 5 DETAILS
# ---------------------------------------------------------------------------

def _choice(label, options, key, it):
    v = it.get(key)
    f = it.fields.get(key, INT.Field())
    help_ = (f"read from your words: '{f.quote}'" if f.status == "read"
             else "Certus needs this")
    return st.selectbox(label, options,
                        index=options.index(v) if v in options else None,
                        placeholder="Certus needs this", help=help_)


def _num(label, key, it, **kw):
    f = it.fields.get(key, INT.Field())
    return st.number_input(label, value=it.get(key), placeholder=
                           "Certus needs this", help=(
                               f"read from your words: '{f.quote}'"
                               if f.status == "read" else "Certus needs this"),
                           **kw)


def stage_details():
    import model_agent as MA
    st.header("The details the simulation needs")
    st.caption("Values read from your text are filled in. Empty boxes are "
               "the questions. Material constants come from Certus's own "
               "table, never from the language model.")
    it = S().intent
    c1, c2 = st.columns(2)
    with c1:
        kind = _choice("Kind of load", ["force", "pressure"], "load_kind", it)
        force = direction = pressure = None
        asserted = "not sure"
        if kind == "force":
            force = _num("Force magnitude (N)", "force_N", it, min_value=0.0)
            direction = _choice("Direction", INT.DIRECTIONS, "direction", it)
        elif kind == "pressure":
            pressure = _num("Pressure (MPa, positive pushes into the face)",
                            "pressure_MPa", it)
            asserted = st.selectbox(
                "Only used if the pressure has no net force (a full hole): "
                "what deformation dominates?",
                ["not sure", "bending", "axial", "shear", "torsion"])
        support = _choice("How is the fixed feature held?", INT.SUPPORTS,
                          "support", it)
    with c2:
        material = _choice("Material", INT.MATERIAL_NAMES, "material", it)
        if material:
            m = MA.MATERIALS[material]
            st.caption(f"{m.name}: E = {m.E:.0f} MPa, nu = {m.nu}")
        goal = _choice("What do you want to find out?", INT.GOAL_NAMES,
                       "goal", it)
        ys = None
        if goal and MA.GOALS[goal]["needs_yield"]:
            ys = _num("Yield stress (MPa)", "yield_MPa", it, min_value=0.0)
        if goal:
            for n in MA.GOALS[goal]["notes"]:
                st.info(n)
        with st.expander("Advanced"):
            size = st.number_input("Element size (mm). Empty = computed "
                                   "by Certus", value=None)
    need = [kind, material, goal, support] + \
        ([force, direction] if kind == "force" else []) + \
        ([pressure] if kind == "pressure" else []) + \
        ([ys] if goal and MA.GOALS[goal]["needs_yield"] else [])
    missing = sum(v is None or v == "" for v in need)
    if missing:
        st.warning(f"{missing} value(s) still needed.")
    b1, b2 = st.columns([1, 5])
    if b1.button("Back"):
        go(3)
    if b2.button("Run the simulation", type="primary", disabled=bool(missing)
                 or not S().get("ccx")):
        S().form = dict(kind=kind, force=force, direction=direction,
                        pressure=pressure, asserted=asserted, support=support,
                        material=material, goal=goal, ys=ys, size=size)
        S().pop("rd", None)
        go(5)


# ---------------------------------------------------------------------------
# 6 VERDICT
# ---------------------------------------------------------------------------

def _vector(f):
    mag = f["force"] or 0.0
    return {"-Z": (0, 0, -mag), "+Z": (0, 0, mag), "-Y": (0, -mag, 0),
            "+Y": (0, mag, 0), "-X": (-mag, 0, 0), "+X": (mag, 0, 0)
            }.get(f["direction"], (0.0, 0.0, 0.0))


def _run_args():
    import model_agent as MA
    from locking_check import MaterialSpec
    f = S().form
    mat = MA.MATERIALS[f["material"]]
    if f["ys"]:
        mat = MaterialSpec(E=mat.E, nu=mat.nu, yield_stress=f["ys"],
                           plastic_response_expected=True, name=mat.name)
    vec = _vector(f)
    if f["support"].startswith("fully") or f["kind"] != "force":
        dofs = (1, 2, 3)
    else:
        dofs = (1 + max(range(3), key=lambda i: abs(vec[i])),)
    cat = S().cat
    return dict(material=mat, load_tags=list(cat.group(S().load_gid).tags),
                fix_tags=list(cat.group(S().fix_gid).tags), force=vec,
                goal=f["goal"], load_kind=f["kind"],
                pressure=f["pressure"] or 0.0, fix_dofs=dofs,
                asserted_mode=None if f["asserted"] == "not sure"
                else f["asserted"])


def answer_text(meta, form) -> str:
    """The plain-language answer. Built from computed numbers only."""
    if meta.get("result_trustworthy") is None:
        return ("There is no answer, because there is no solved result to "
                "trust. The reason is in the verdict above.")
    lines = []
    d, k = meta.get("load_point_displacement_mm"), meta.get(
        "stiffness_N_per_mm")
    vm = meta.get("max_von_mises_MPa")
    goal = form["goal"]
    if d is not None:
        lines.append(f"Where the load acts, the part moves {d:.4g} mm "
                     f"(stiffness {k:.4g} N/mm)." if k else
                     f"Where the load acts, the part moves {d:.4g} mm.")
    if vm is not None and goal in ("peak stress", "does it yield"):
        lines.append(f"The highest von Mises stress on this mesh is "
                     f"{vm:.4g} MPa. A single-mesh peak is not a converged "
                     f"number: run the convergence study below before "
                     f"quoting it.")
    if goal == "does it yield" and vm is not None and form["ys"]:
        r = vm / form["ys"]
        lines.append(
            f"That is {100*r:.0f} percent of the yield stress you gave "
            f"({form['ys']:g} MPa). " +
            ("On this mesh the peak is below yield, so an elastic model is "
             "consistent with its own answer." if r < 1 else
             "On this mesh the peak reaches yield. From that point the "
             "elastic result is not valid anywhere in the part, not only "
             "at the peak."))
    if meta.get("result_trustworthy") is False:
        lines.append("Do not use these numbers yet: the verdict lists "
                     "findings that were not resolved.")
    return "\n\n".join(lines)


def stage_verdict():
    import model_agent as MA
    import viewer
    st.header("Verdict")
    if "rd" not in S():
        with st.spinner("Meshing, writing the deck, running the pre-solve "
                        "checks, solving with CalculiX, running the "
                        "post-solve checks..."):
            with captured("simulation"):
                S().rd = MA.run(S().step, "gui", **_run_args(),
                                solvers=("calculix",),
                                target_size=S().form["size"],
                                solve_with="calculix",
                                run_root=session_dir())
    rd = S().rd
    meta = json.load(open(os.path.join(rd.path, "run.json")))
    trust = meta.get("result_trustworthy")
    head = meta.get("headline_verdict", "")
    if trust is True:
        st.success("RESULT PASSED EVERY CHECK THAT RAN")
    elif trust is False:
        st.error("SOLVED, BUT THE RESULT IS NOT TRUSTWORTHY")
    else:
        st.warning("NO TRUSTED RESULT (refused or not solved)")
    st.write(head)

    st.subheader("Your answer")
    st.write(answer_text(meta, S().form))

    t = meta.get("trust") or {}
    c1, c2 = st.columns(2)
    with c1:
        if t.get("blockers"):
            st.markdown("**Unresolved findings (these block the result)**")
            st.dataframe(t["blockers"], hide_index=True,
                         width="stretch")
        if t.get("caveats"):
            st.markdown("**Caveats**")
            for c in t["caveats"]:
                st.write(f"- {c['source']}: {c['detail']}")
        st.markdown("**Checked**")
        for c in t.get("checked", []):
            st.write(f"- {c}")
        st.markdown("**Not checked**")
        for c in t.get("not_checked", []):
            st.write(f"- {c}")
    with c2:
        frd = os.path.join(rd.path, "case_calculix", "case.frd")
        deck = os.path.join(rd.path, (meta.get("decks") or {}).get(
            "calculix", "case_calculix/case.inp"))
        if os.path.exists(frd) and os.path.exists(deck):
            which = st.radio("colour by", ["displacement", "von Mises"],
                             horizontal=True)
            st.plotly_chart(viewer.result_figure(
                deck, frd, "U" if which == "displacement" else "S"),
                width="stretch")
        st.caption(f"element {meta.get('element_type')}, "
                   f"{meta.get('n_elements')} elements, size "
                   f"{meta.get('char_size_mm') or 0:.3g} mm, mode "
                   f"{meta.get('computed_mode')}")

    st.subheader("Mesh convergence")
    if "conv" in S():
        st.code(S().conv)
    elif trust is not None and st.button(
            "Run a convergence study (three more solves)"):
        h = meta.get("char_size_mm") or 2.0
        q = ("max_von_mises_MPa" if S().form["goal"] in
             ("peak stress", "does it yield") else
             "load_point_displacement_mm")
        a = _run_args()
        with st.spinner("Solving at three mesh sizes..."):
            with captured("convergence"):
                text, _ = MA.convergence_study(
                    S().step, "gui", a.pop("material"), a.pop("load_tags"),
                    a.pop("fix_tags"), a.pop("force"),
                    [round(2.0 * h, 3), round(1.41 * h, 3), round(h, 3)],
                    qoi=q, run_root=session_dir(), **a)
        S().conv = text
        st.rerun()

    rep = os.path.join(rd.path, "REPORT.txt")
    if os.path.exists(rep):
        st.download_button("Download REPORT.txt", open(rep, "rb").read(),
                           "REPORT.txt")
        with st.expander("full report"):
            st.text(open(rep, encoding="utf-8", errors="replace").read())
    st.caption(f"run folder: {rd.path}")
    if st.button("Change the details and run again"):
        S().pop("rd")
        S().pop("conv", None)
        go(4)


# ---------------------------------------------------------------------------

def main():
    S().setdefault("stage", 0)
    sidebar()
    header()
    [stage_ask, stage_understood, stage_part, stage_faces, stage_details,
     stage_verdict][S().stage]()
    logs = S().get("logs", [])
    if logs:
        with st.expander("terminal output of every step"):
            for label, log in logs:
                st.markdown(f"**{label}**")
                st.code(log[-4000:] or "(none)")


main()
