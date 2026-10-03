#!/usr/bin/env python3
"""
app.py - Certus in a browser tab. Local web page, nothing leaves the machine
unless the Claude API is chosen as the language model.

    pip install streamlit plotly
    certus-gui            (or: streamlit run src/certus/app.py)

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
from uuid import uuid4

import streamlit as st

from certus import llm, discovery, review
from certus import intent as INT
from certus import ui

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

from certus.paths import RUNS
HERE = os.getcwd()                  # restored after CAD generation
WORK = os.path.join(str(RUNS), "gui")
STAGES = ["Ask", "Understood", "Part", "Faces", "Details", "Verdict"]

st.set_page_config(page_title="Certus · Verimech", page_icon=ui.asset_uri("Verimech_Icon.svg"),
                   layout="wide", initial_sidebar_state="expanded")


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
        S().dir = os.path.join(WORK, time.strftime("%Y-%m-%d_%H%M%S") + "_" + uuid4().hex[:8])
        os.makedirs(S().dir, exist_ok=True)
    return S().dir


def save_upload(up, name=None) -> str:
    p = os.path.join(session_dir(), os.path.basename(name or up.name))
    with open(p, "wb") as f:
        f.write(up.getbuffer())
    return p


def badge(status: str) -> str:
    return {"read": "Read · read from your text",
            "rejected": "Rejected · not accepted",
            "missing": "Missing · not stated"}[status]


# ---------------------------------------------------------------------------
# sidebar: the language model and the solver
# ---------------------------------------------------------------------------

def abaqus_available():
    return bool(discovery.find_abaqus())


def sidebar():
    sb = st.sidebar
    with sb:
        ui.sidebar_brand()
    sb.subheader("Analysis environment")
    sb.caption("Local workspace · CalculiX solver")
    sb.divider()
    sb.subheader("Language model")
    provider = sb.selectbox("Provider", ["Local models", "Claude", "OpenAI-compatible server"],
                            index=1 if llm.CONFIG.provider == "anthropic" else 0, key="llm_provider")
    if provider == "Claude":
        refresh = sb.button("Refresh models", key="refresh_cloud_models")
        if (refresh or "claude_catalogue" not in S()) and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                S().claude_catalogue = llm.list_claude_models()
                S().pop("claude_catalogue_error", None)
            except RuntimeError as exc:
                S().claude_catalogue = []
                S().claude_catalogue_error = str(exc)
        models = S().get("claude_catalogue", [])
        if S().get("claude_catalogue_error"):
            sb.warning(S().claude_catalogue_error)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sb.caption("Set ANTHROPIC_API_KEY in the shell running Certus to load your Claude models.")
        llm.configure(provider="anthropic")
        source_key = "claude"
    else:
        if provider == "Local models":
            refresh = sb.button("Scan this computer")
            if refresh or "local_discovery" not in S():
                S().local_discovery = discovery.scan_local_models()
            detected = S().local_discovery
            servers = detected["servers"]
            selected = sb.selectbox("Model server", range(len(servers)),
                                    index=next((i for i, x in enumerate(servers) if x["models"]), 0),
                                    key="local_server", format_func=lambda i: servers[i]["name"] +
                                    (" · available" if servers[i]["models"] else " · offline"))
            server = servers[selected]
            base = server["url"]
            models = server["models"]
            if not models:
                sb.caption("Start this model server, then scan again to select a model.")
            if detected["ollama_installed"]:
                sb.caption("Ollama models on disk: " + ", ".join(detected["ollama_installed"]))
            if detected.get("lmstudio_files"):
                sb.caption("LM Studio files on disk: " + ", ".join(detected["lmstudio_files"]))
            if detected["runtimes"]:
                sb.caption("Installed command-line runtimes: " + ", ".join(detected["runtimes"]))
        else:
            base = sb.text_input("API base URL", llm.CONFIG.base_url)
            if sb.button("Refresh models", key="refresh_server_models") or S().get("catalogue_url") != base:
                S().server_catalogue = discovery.server_models(base)
                S().catalogue_url = base
            models = S().get("server_catalogue", [])
        llm.configure(provider="openai", base_url=base)
        source_key = base
        sb.caption("For images, select a model with vision support.")
    if models:
        current = llm.CONFIG.model
        model = sb.selectbox("Model", models,
                             index=models.index(current) if current in models else 0,
                             key="model_" + source_key)
    else:
        model = sb.text_input("Model ID", "", key="manual_" + source_key,
                              placeholder="Exact model ID from your provider")
    llm.configure(model=model)
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
    abaqus_override = sb.text_input("Abaqus launcher (optional)",
                                    os.environ.get("CERTUS_ABAQUS_COMMAND", ""),
                                    placeholder="C:/SIMULIA/Commands/abaqus.bat")
    if abaqus_override:
        os.environ["CERTUS_ABAQUS_COMMAND"] = abaqus_override
    else:
        os.environ.pop("CERTUS_ABAQUS_COMMAND", None)
    abaqus = discovery.find_abaqus()
    if abaqus:
        sb.success(f"Abaqus found: {abaqus}")
        sb.caption("Abaqus deck export available. Automated solve and verdict use CalculiX.")
    else:
        sb.caption("Abaqus not found. Set its launcher path above if installed elsewhere.")

    with sb.expander(f"Model activity · {len(llm.CALL_LOG)} calls"):
        if llm.CALL_LOG:
            for c in llm.CALL_LOG[-12:]:
                st.caption(f"{c['role']}: {c['model']} "
                           f"({c['chars']} chars, stop={c['stop']})")
        else:
            st.caption("No language-model calls in this session.")
    sb.divider()
    if sb.button("Start over"):
        for k in list(S().keys()):
            del S()[k]
        llm.CALL_LOG.clear()
        st.rerun()


def header():
    ui.workspace_header(S().get("stage", 0))


# ---------------------------------------------------------------------------
# 1 ASK
# ---------------------------------------------------------------------------

EXAMPLE = ("An L-shaped steel bracket, base 60 x 50 mm, 8 mm thick, upright "
           "wall 30 mm high with a 10 mm hole near the top. A pin in the hole "
           "pulls 2 kN downwards. The bottom face is bolted to the table. "
           "Does it yield? Yield is 250 MPa.")


def stage_ask():
    ui.section_heading("01 / ENGINEERING INTENT", "Define your analysis",
                       "Describe the part, its loads and supports, and the decision you need to make.")
    entry, guide = st.columns([2.1, 1], gap="large")
    with entry:
        prompt = st.text_area("Your question", S().get("prompt", ""),
                              height=230, placeholder=EXAMPLE)
        st.caption("State dimensions and units. Missing values will be asked for, not silently assumed.")
    with guide:
        ui.review_guide()
    c1, c2 = st.columns(2)
    img = c1.file_uploader("Sketch or photo of the part (optional)",
                           type=["png", "jpg", "jpeg", "webp", "gif"])
    stp = c2.file_uploader("I already have a CAD file (STEP, optional)",
                           type=["step", "stp"])
    if img is not None:
        c1.image(img, width=320)
    if st.button("Read my request", type="primary",
                 disabled=not prompt.strip()):
        # Re-entering the workflow must not reuse geometry or results from an
        # earlier request. Keep the display preferences and connection settings.
        review.clear_derived(S())
        S().review_revision = S().get("review_revision", 0) + 1
        S().prompt = prompt
        S().image = save_upload(img) if img is not None else None
        S().step = save_upload(stp, "input.step") if stp is not None else None
        S().uploaded_step = stp is not None
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
                from certus import cad_agent as CA
                with captured("specification"):
                    S().spec = CA.call_llm_spec(prompt, S().image)
        go(1)


# ---------------------------------------------------------------------------
# 2 UNDERSTOOD
# ---------------------------------------------------------------------------

def stage_understood():
    from certus import model_agent as MA
    from certus import cad_agent as CA
    ui.section_heading("02 / INTERPRETATION", "Review what Certus understood",
                       "Complete the engineering inputs and confirm the assumptions before building the model.")
    it: INT.Intent = S().intent
    uploaded = S().get("uploaded_step", bool(S().get("step") and not S().get("cad")))
    spec = S().get("spec") or {}
    revision = S().get("review_revision", 0)
    material = MA.MATERIALS.get(it.get("material"))
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        st.subheader("From your text")
        st.caption("Read values include your exact words. Missing and rejected inputs remain visible. "
                   "Draft dimensions and material presets are labeled separately.")
        ui.review_table(review.review_rows(it, None if uploaded else spec, material))
    assumptions_ok = True
    with right:
        if uploaded:
            st.subheader("Your CAD file")
            st.write(f"{os.path.basename(S().step)} will be used as supplied.")
            st.caption("Corrections update the analysis inputs. To replace this geometry, upload a new CAD file on the Ask page.")
        else:
            st.subheader("Part specification")
            ui.part_summary(spec)
            assumptions = spec.get("assumptions") or []
            if assumptions:
                st.warning("Assumptions to review:\n\n" +
                           "\n".join(f"- {a}" for a in assumptions))
                assumptions_ok = st.checkbox("I accept the listed geometry assumptions",
                                              key=f"review_assumptions_{revision}")
        st.subheader("Information needed")
        questions = review.completion_questions(it, spec, uploaded)
        if questions:
            st.warning("Complete these in the correction prompt below:\n\n" +
                       "\n".join(f"- {q}" for q in questions))
        else:
            st.success("Required draft inputs are present. Review them before continuing.")
        st.caption("Load and support faces will still be confirmed on the 3D part. "
                   "The Details page remains a final review before solving.")
    st.divider()
    st.subheader("Correct or complete the request")
    st.caption("Describe the changes in plain language. Both review panels update together; "
               "later corrections replace conflicting earlier statements.")
    with st.form("review_correction", clear_on_submit=True):
        correction = st.text_area("Your corrections or missing information", height=150,
                                  placeholder="For example: the beam has a solid rectangular section, "
                                  "200 mm wide and 50 mm thick. Apply 2 kN in -Z, not pressure.")
        submitted = st.form_submit_button("Update interpretation", type="primary")
    if submitted:
        with st.spinner("Updating the interpretation and part specification..."):
            try:
                review.apply_revision(S(), correction, INT.read_intent, CA.call_llm_spec)
            except Exception as exc:
                st.error(f"Could not update the interpretation ({type(exc).__name__}: {exc}). "
                         "The previous review remains available. Submit the correction again.")
            else:
                st.rerun()
    material_ok = True
    if material:
        material_ok = st.checkbox(
            f"I confirm the linear-elastic {material.name} preset: E = {material.E:g} MPa, ν = {material.nu:g}",
            key=f"review_material_{revision}")
        st.caption("A supplied yield strength is used to check first yield; it does not define a plastic material law.")
    c1, c2 = st.columns([1, 5])
    if c1.button("Back"):
        go(0)
    if c2.button("Use the CAD file" if uploaded else "Build the part", type="primary",
                 disabled=bool(questions) or not assumptions_ok or not material_ok):
        if not uploaded:
            S().step = None
        go(3 if uploaded else 2)


# ---------------------------------------------------------------------------
# 3 PART
# ---------------------------------------------------------------------------

def stage_part():
    from certus import cad_agent as CA
    ui.section_heading("03 / GEOMETRY", "Inspect the generated part",
                       "Geometric measurements control this gate. The language model's visual review is advisory.")
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
                        S().prompt, S().image,
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
    c1, c2 = st.columns([2.3, 1], gap="large")
    with c1:
        if cad.get("step") and os.path.exists(cad["step"]):
            from certus import viewer
            if S().get("part_view_path") != cad["step"]:
                with captured("geometry preview"):
                    _, S().part_tri = viewer.face_mesh(cad["step"])
                    S().part_view_path = cad["step"]
            ui.geometry_view(S().part_tri, key="part")
        if cad.get("drawing") and os.path.exists(cad["drawing"]):
            with st.expander("Generated drawing"):
                st.image(cad["drawing"])
    c2.subheader("Measurement evidence")
    c2.text(CA.report_text(cad.get("results", [])))
    if cad.get("visual", {}).get("match") is False:
        c2.info("Advisory visual review by the language model (it cannot "
                "change the verdict): " +
                "; ".join(cad["visual"].get("discrepancies") or []))
    b1, b2, b3 = st.columns([1, 1, 4])
    if b1.button("Back to the specification"):
        S().pop("cad")
        S().pop("part_view_path", None)
        go(1)
    if b2.button("Build again"):
        S().pop("cad")
        S().pop("part_view_path", None)
        st.rerun()
    if passed:
        if b3.button("Use this part", type="primary"):
            S().step = cad["step"]      # already inside the session folder
            S().uploaded_step = False
            for key in ("tri", "cat", "rd", "conv"):
                S().pop(key, None)
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
    from certus import geometry_features as GF
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
    from certus import viewer
    from certus import geometry_features as GF
    ui.section_heading("04 / BOUNDARY CONDITIONS", "Confirm the physical setup",
                       "Select catalogue features, inspect them in 3D, then explicitly confirm the load and support.")
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

    left, right = st.columns([1, 2.15], gap="large")
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
        for gid, role in ((lg, "LOAD (orange)"), (fg, "SUPPORT (blue)")):
            if gid is not None:
                st.text(f"{role}\n" + groups[gid].describe(cat.bbox_min,
                                                          cat.bbox_max))
                w = GF.sliver_warning(groups[gid])
                if w:
                    st.warning(w)
        same = lg is not None and lg == fg
        if same:
            st.error("The load and the support are the same feature.")
        ok = st.checkbox("I have looked at the 3D view: orange is where the "
                         "load acts and blue is what is held.",
                         disabled=lg is None or fg is None or same,
                         key=f"confirm_faces_{lg}_{fg}")
    with right:
        hover = {}
        for g in cat.groups:
            for t in g.tags:
                hover[t] = g.summary()
        ui.geometry_view(tri, groups[lg].tags if lg is not None else (),
                         groups[fg].tags if fg is not None else (), hover, key="faces")
    c1, c2 = st.columns([1, 5])
    if c1.button("Back"):
        go(1)
    if c2.button("Next", type="primary", disabled=not ok or lg is None or fg is None or same):
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
    from certus import model_agent as MA
    ui.section_heading("05 / MODEL DEFINITION", "Complete the analysis model",
                       "Review the load, material and analysis goal. No solve starts until the required inputs are present.")
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
    if S().get("tri") is not None:
        with st.expander("Review geometry and selected boundary conditions", expanded=False):
            cat = S().cat
            vector = _vector(dict(force=force, direction=direction)) if kind == "force" else None
            ui.geometry_view(S().tri, cat.group(S().load_gid).tags,
                             cat.group(S().fix_gid).tags, key="details", load_vector=vector)
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
    from certus import model_agent as MA
    from certus.locking_check import MaterialSpec
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
    from certus import model_agent as MA
    from certus import viewer
    ui.section_heading("06 / RESULTS & EVIDENCE", "Review the analysis",
                       "Inspect the numerical result together with the evidence and limitations that qualify it.")
    if "rd" not in S():
        with st.spinner("Meshing, writing the deck, running the pre-solve "
                        "checks, solving with CalculiX, running the "
                        "post-solve checks..."):
            with captured("simulation"):
                S().rd = MA.run(S().step, "gui", **_run_args(),
                                solvers=("calculix", "abaqus") if abaqus_available() else ("calculix",),
                                target_size=S().form["size"],
                                solve_with="calculix",
                                run_root=session_dir())
    rd = S().rd
    meta = json.load(open(os.path.join(rd.path, "run.json")))
    trust = meta.get("result_trustworthy")
    head = meta.get("headline_verdict", "")
    ui.status_banner(trust, head)
    ui.metric_row(meta)
    result_tab, evidence_tab, report_tab = st.tabs(["Results", "Verification evidence", "Report & run data"])
    with result_tab:
        st.subheader("Your answer")
        st.write(answer_text(meta, S().form))
        frd = os.path.join(rd.path, "case_calculix", "case.frd")
        deck = os.path.join(rd.path, (meta.get("decks") or {}).get(
            "calculix", "case_calculix/case.inp"))
        if os.path.exists(frd) and os.path.exists(deck):
            ui.results_view(deck, frd)
        else:
            st.info("No solved field is available to display. Review the verification evidence for the reason.")
        st.caption(f"Element: {meta.get('element_type', 'not available')} · "
                   f"Computed mode: {meta.get('computed_mode', 'not available')}")

    t = meta.get("trust") or {}
    with evidence_tab:
        st.subheader("What the verdict covers")
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

    with report_tab:
        rep = os.path.join(rd.path, "REPORT.txt")
        if os.path.exists(rep):
            with open(rep, "rb") as stream:
                st.download_button("Download REPORT.txt", stream.read(), "REPORT.txt")
            with st.expander("Full report"):
                with open(rep, encoding="utf-8", errors="replace") as stream:
                    st.text(stream.read())
        st.download_button("Download run.json", json.dumps(meta, indent=2), "run.json", "application/json")
        for solver, relative_path in meta.get("decks", {}).items():
            deck_path = os.path.join(rd.path, relative_path)
            if os.path.isfile(deck_path):
                with open(deck_path, "rb") as stream:
                    st.download_button(f"Download {solver} input deck", stream.read(),
                                       f"{solver}.inp", key=f"download_{solver}_deck")
        st.caption(f"Run folder: {rd.path}")
    if st.button("Change the details and run again"):
        S().pop("rd")
        S().pop("conv", None)
        go(4)


# ---------------------------------------------------------------------------

def main():
    S().setdefault("stage", 0)
    ui.apply_theme()
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
