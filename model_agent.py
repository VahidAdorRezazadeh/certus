#!/usr/bin/env python3
"""
model_agent.py - the orchestrator. STEP in, solvable decks out, report out.

THE RULE THIS FILE ENFORCES

    the agent ASKS for intent          the agent COMPUTES physics
    ---------------------------        ---------------------------
    where is the load applied          dominant deformation mode
    what kind of load, how big         element family, order, integration
    what is held fixed                 mesh size and the retry lever
    what material                      locking verdict and severity
    what are you trying to find out    overconstraint warning
    which solver, if it matters        which solver can deliver the cure

Anything the geometry and the load determine is NOT a question. Asking the
user for the dominant mode would replace a command line flag with a
conversational flag: same silent failure, friendlier interface.

No LLM call anywhere in this file. The questions are a terminal prompt.

Usage:
    python model_agent.py part.step
    python model_agent.py --cantilever                    both decks, report
    python model_agent.py --cantilever --solve calculix   also runs ccx here
    python model_agent.py --cantilever --converge 2.5,1.6,1.0,0.7
"""

from __future__ import annotations
from typing import List, Optional, Sequence, Tuple
import argparse
import os
import shutil
import subprocess
import sys

import geometry_features as GF
from geom_session import GeomSession
from mesh_agent import MeshRequest, run_mesh_agent
from locking_check import MaterialSpec, LoadCase, check_locking
from case_agent import (CaseSpec, LoadSpec, ConstraintSpec, write_case,
                        reconcile_load_case)
from results_check import read_frd_disp, convergence, Comparison
from run_dir import RunDir
import cantilever as CANT

# What the user is trying to find out changes what the agent checks. A goal
# that is collected and then ignored is worse than no question: it implies the
# answer mattered. Each entry states what it changes.
GOALS = {
    "stiffness (deflection)": {
        "metric": "deflection",
        "needs_convergence": False,
        "needs_yield": False,
        "notes": ["Deflection is a global quantity, so a moderately coarse "
                  "mesh is usually enough."],
    },
    "peak stress": {
        "metric": "stress",
        "needs_convergence": True,
        "needs_yield": False,
        "notes": ["Peak stress does NOT converge the way deflection does. It "
                  "keeps rising as the mesh is refined near a re-entrant "
                  "corner, so a single mesh gives a number with no meaning.",
                  "A fully clamped face also creates an artificial stress "
                  "concentration at its edge. If the peak sits on the "
                  "constraint boundary, it is the boundary condition, not "
                  "the part."],
    },
    "does it yield": {
        "metric": "stress",
        "needs_convergence": True,
        "needs_yield": True,
        "notes": ["Comparing an elastic result to a yield stress only tells "
                  "you WHETHER yielding starts, never how much. Past first "
                  "yield the elastic answer is wrong everywhere, not just at "
                  "the peak.",
                  "Once plasticity is expected, the material becomes nearly "
                  "incompressible in the plastic zone, which is what makes "
                  "volumetric locking a real risk on tets."],
    },
    "just check the setup": {
        "metric": "none",
        "needs_convergence": False,
        "needs_yield": False,
        "notes": ["No solve implied. The value of this run is the computed "
                  "mode, the overconstraint check and the locking verdict."],
    },
}

MATERIALS = {
    "steel": MaterialSpec(E=210000.0, nu=0.30, name="structural steel"),
    "aluminium": MaterialSpec(E=70000.0, nu=0.33, name="aluminium 6xxx"),
    "titanium": MaterialSpec(E=110000.0, nu=0.34, name="Ti-6Al-4V"),
}
ALL_SOLVERS = ("abaqus", "calculix")


# ---------------------------------------------------------------------------
# ASK: intent only
# ---------------------------------------------------------------------------

def _pick(prompt: str, options: Sequence[str], default: int = 0) -> str:
    print(f"\n{prompt}")
    for i, o in enumerate(options):
        print(f"  [{i}] {o}" + ("  (default)" if i == default else ""))
    raw = input("  choice: ").strip()
    if not raw:
        return options[default]
    try:
        return options[int(raw)]
    except (ValueError, IndexError):
        print("  not a listed option, using the default")
        return options[default]


def _number(prompt: str, default: float) -> float:
    raw = input(f"{prompt} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"  not a number, using {default}")
        return default


def _fmt_groups(cat, groups) -> None:
    lo, hi = cat.bbox_min, cat.bbox_max
    for g in groups:
        print(f"  [{g.group_id:>2}] {g.summary()}")


def ask_face(cat, role: str):
    """Choose a face group, with enough information to choose correctly.

    A list of 27 lines reading 'hole dia=5.00' is not a choice, it is a
    guess, and a wrong guess produces a clean solve with the load on the
    wrong feature. Three things fix that:
      1. every line now carries the axis direction and the position
      2. the list can be filtered to holes, planes or a diameter
      3. the pick is echoed back geometrically and must be confirmed

    Shortcuts accepted instead of a group id:
      holes / planes         filter the list
      dia 8                  filter to cylinders of that diameter
      largest hole           the deterministic selector
      largest face           the deterministic selector
      bottom / top           extreme planar face along Z
    """
    lo, hi = cat.bbox_min, cat.bbox_max
    print(f"\n{'='*70}\nWhich face group carries the {role}?")
    print(f"part bounding box  X {lo[0]:.1f}..{hi[0]:.1f}   "
          f"Y {lo[1]:.1f}..{hi[1]:.1f}   Z {lo[2]:.1f}..{hi[2]:.1f}")
    print("type a group id, or: holes | planes | dia 8 | largest hole | "
          "largest face | bottom | top | all")
    print("-" * 70)
    shown = list(cat.groups)
    _fmt_groups(cat, shown)

    while True:
        raw = input("\n  choice: ").strip().lower()
        if not raw:
            print("  nothing entered")
            continue
        pick = None

        if raw in ("all",):
            shown = list(cat.groups); _fmt_groups(cat, shown); continue
        if raw.startswith("hole"):
            shown = [g for g in cat.groups if g.kind == "hole"]
            print(f"  {len(shown)} hole group(s):"); _fmt_groups(cat, shown)
            continue
        if raw.startswith("plane") or raw.startswith("flat"):
            shown = [g for g in cat.groups if g.normal is not None]
            print(f"  {len(shown)} planar group(s):"); _fmt_groups(cat, shown)
            continue
        if raw.startswith("dia"):
            try:
                d = float(raw.split()[1])
            except (IndexError, ValueError):
                print("  use for example: dia 8"); continue
            shown = [g for g in cat.groups if g.radius
                     and abs(2 * g.radius - d) < 0.05 * max(d, 1.0)]
            print(f"  {len(shown)} group(s) at diameter {d}:")
            _fmt_groups(cat, shown); continue
        if raw == "largest hole":
            pick = GF.largest_hole(cat)
        elif raw == "largest face":
            pick = GF.largest_face(cat)
        elif raw in ("bottom", "top"):
            pick = GF.extreme_planar_face(
                cat, axis=2, side="min" if raw == "bottom" else "max")
        else:
            try:
                pick = cat.group(int(raw))
            except (ValueError, KeyError):
                print("  not a group id and not a known shortcut")
                continue

        if pick is None:
            print("  that selector found nothing on this geometry")
            continue

        # CONFIRM. A selection that is never echoed back is a selection that
        # can be wrong silently.
        print("\n  you selected:")
        print(pick.describe(lo, hi))
        sw = GF.sliver_warning(pick)
        if sw:
            print(f"\n  ! WARNING: {sw}")
        if input("\n  is that the right feature? [Y/n]: ").strip().lower() \
                in ("", "y", "yes"):
            return pick
        print("  not confirmed, choose again")
        shown = list(cat.groups)


def _cure_table(element, lreport) -> str:
    """Which solvers can actually deliver the cure a finding asks for.

    A locking finding is only actionable if the cure exists somewhere. This
    turns 'you need a hybrid element' into 'CalculiX cannot, Abaqus and
    FEniCSx can', which is a stack decision instead of a dead end.
    """
    from solvers import cure_availability
    needed = set()
    for f in getattr(lreport, "findings", []) or []:
        txt = (f.recommended_action + " " + f.consequence + " "
               + f.reason).lower()
        for cure in ("hybrid", "reduced", "incompatible"):
            if cure in txt:
                needed.add(cure)
    if not needed:
        return ""
    out = ["", "CURE AVAILABILITY BY SOLVER",
           f"  element in use: {element.family}/order{element.order}"]
    for cure in sorted(needed):
        avail = cure_availability(element.family, element.order, cure)
        yes = [k for k, v in avail.items() if v]
        no = [k for k, v in avail.items() if not v]
        out.append(f"  '{cure}' available in : "
                   + (", ".join(yes) if yes else "NONE"))
        out.append(f"  '{cure}' missing from : "
                   + (", ".join(no) if no else "none"))
    out.append("  A cure that no available solver offers is not a fix. It is "
               "a stack decision.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ccx_available() -> Optional[str]:
    return shutil.which("ccx") or shutil.which("ccx_2.21")


def _run_ccx(deck_path: str, timeout: int = 3600) -> Tuple[bool, str]:
    exe = _ccx_available()
    if not exe:
        return False, "ccx not on PATH"
    base = os.path.splitext(deck_path)[0]
    try:
        p = subprocess.run([exe, base], capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"ccx timed out after {timeout} s"
    log = (p.stdout or "") + (p.stderr or "")
    with open(base + ".log", "w") as f:
        f.write(log)
    ok = os.path.exists(base + ".frd") and "Job finished" in log
    if not ok:
        err = [l for l in log.splitlines() if "ERROR" in l.upper()]
        return False, "; ".join(err[:3]) or "ccx did not finish"
    return True, base + ".frd"


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def run(step_path: str,
        label: str,
        material: MaterialSpec,
        load_tags: Sequence[int],
        fix_tags: Sequence[int],
        force: Tuple[float, float, float],
        goal: str = "stiffness (deflection)",
        load_kind: str = "force",
        pressure: float = 0.0,
        fix_dofs: Tuple[int, ...] = (1, 2, 3),
        solvers: Sequence[str] = ALL_SOLVERS,
        target_size: Optional[float] = None,
        nlgeom: bool = False,
        reference: Optional[dict] = None,
        solve_with: Optional[str] = None,
        run_root: str = "runs") -> RunDir:

    rd = RunDir(label, root=run_root, solvers=tuple(solvers), meta={
        "step": os.path.abspath(step_path),
        "material": material.name,
        "E_MPa": material.E, "nu": material.nu,
        "force_N": list(force),
        "goal": goal,
        "nlgeom": nlgeom,
        "requested_size_mm": target_size,
    })
    rd.adopt(step_path, "geometry")
    print(f"\nrun folder: {rd.path}")

    with GeomSession(step_path) as ses:
        rd.section("GEOMETRY AND FEATURE CATALOGUE", ses.catalogue.render())

        ses.add_selection("LOAD_FACE", load_tags, "load")
        ses.add_selection("FIX_FACE", fix_tags, "constraint")

        assumed = LoadCase("bending")
        req = MeshRequest(step_path, material, assumed,
                          target_size=target_size,
                          out_prefix=rd.prefix("mesh", "mesh"),
                          solver="calculix")
        mres = run_mesh_agent(req, session=ses)
        print(mres.render())

        rd.section("WHAT WAS ASKED FOR (intent, supplied by the user)",
                   f"  load face group    {list(load_tags)}\n"
                   f"  constraint group   {list(fix_tags)}\n"
                   + (f"  load               {force} N (concentrated "
                      f"resultant, area weighted)\n" if load_kind == "force"
                      else f"  load               {pressure} MPa pressure "
                           f"into the surface\n")
                   + f"  material           {material.name}  "
                   f"E={material.E} MPa  nu={material.nu}\n"
                   f"  question asked     {goal}\n"
                   f"  geometric nonlin.  {'ON' if nlgeom else 'OFF'}")
        rd.section("MESH", mres.render() + "\n\n" + ses.render_selections())

        facets = GF.surface_facets(ses.selections)
        total_nodes = mres.quality.n_nodes

        decks, creport = {}, None
        for sv in solvers:
            deck = rd.case(sv, "case.inp")
            with open(mres.deck_path) as src, open(deck, "w") as dst:
                dst.write(src.read())
            GF.append_surfaces_inp(deck, facets)
            spec = CaseSpec(material=material, solver=sv, nlgeom=nlgeom,
                            loads=[LoadSpec("LOAD_FACE", load_kind,
                                            tuple(force),
                                            pressure=pressure)],
                            constraints=[ConstraintSpec("FIX_FACE",
                                                        dofs=fix_dofs,
                                                        encastre=fix_dofs ==
                                                        (1, 2, 3))])
            creport = write_case(deck, spec, ses.node_sets, ses.selections,
                                 total_nodes)
            decks[sv] = deck
        print(creport.render())

        rd.section("COMPUTED FINDINGS (derived, not supplied)",
                   creport.render())
        for wmsg in creport.warnings:
            rd.warn(wmsg)

        clashes = reconcile_load_case(creport.mode, assumed)
        for c in clashes:
            rd.warn(c)
            print(f"\n  ! {c}")

        mode = creport.mode.mode if creport.mode else assumed.dominant_mode
        lreport = check_locking(mres.element, material, LoadCase(mode))
        print(lreport.render())
        cure_text = _cure_table(mres.element, lreport)
        rd.section("VERIFICATION CHECK 3: ELEMENT AND LOCKING",
                   lreport.render() + cure_text)
        if cure_text:
            print(cure_text)

        rd.set("computed_mode", mode)
        rd.set("n_elements", mres.quality.n_elements)
        rd.set("n_nodes", mres.quality.n_nodes)
        rd.set("char_size_mm", mres.quality.char_size)
        rd.set("element_type", f"{mres.element.family}/order{mres.element.order}/{mres.element.integration}")
        rd.set("decks", {k: os.path.relpath(v, rd.path)
                         for k, v in decks.items()})

    # ---- reference and optional solve, outside the Gmsh session --------
    if reference:
        rd.section("VERIFICATION CHECK 2: ANALYTICAL REFERENCE (closed form)",
                   reference["text"])
        rd.set("analytical_tip_deflection_mm", reference["deflection"])
        rd.set("analytical_root_stress_MPa", reference.get("stress"))

    g = GOALS.get(goal, {})
    if g:
        body = [f"  question       {goal}",
                f"  metric         {g['metric']}"]
        body += [f"  - {n}" for n in g["notes"]]
        rd.section("WHAT THIS RUN IS FOR, AND WHAT THAT CHANGES",
                   "\n".join(body))
        if g.get("needs_convergence"):
            rd.warn("The chosen goal depends on a stress value. Stress is "
                    "mesh dependent in a way deflection is not. This run used "
                    "ONE mesh, so the stress it produces is not yet a result.")
            rd.action("Repeat at two finer mesh sizes and confirm the peak "
                      "stress changes by less than a few percent.")
        if g.get("needs_yield") and not material.yield_stress:
            rd.warn("The goal is yielding but the material carries no yield "
                    "stress, so nothing can be compared against.")

    headline = "DECKS WRITTEN, NOT YET SOLVED"
    if solve_with:
        deck = decks[solve_with]
        print(f"\nsolving with {solve_with} ...")
        ok, info = _run_ccx(deck) if solve_with == "calculix" \
            else (False, "only calculix can be run from here")
        if ok:
            for ext in (".frd", ".dat", ".sta", ".cvg", ".log"):
                p = os.path.splitext(deck)[0] + ext
                if os.path.exists(p):
                    rd.adopt(p, "results")
            if reference:
                cmp_ = Comparison("TIP DEFLECTION (max |Uz|)",
                                  abs(min(v[2] for v in
                                          read_frd_disp(info).values())),
                                  abs(reference["deflection"]), 0.05)
                print(cmp_.render())
                rd.section("RESULT VERSUS REFERENCE", cmp_.render())
                rd.set("fe_tip_deflection_mm", cmp_.computed)
                rd.set("analytical_error_pct", cmp_.error * 100)
                rd.set("analytical_verdict", cmp_.verdict)
                headline = (f"SOLVED. Tip deflection {cmp_.computed:.6f} mm "
                            f"vs {cmp_.reference:.6f} mm reference, "
                            f"{cmp_.error*100:+.2f}%, {cmp_.verdict}")
                if cmp_.verdict != "PASS":
                    rd.action("The analytical check did not pass. Do not "
                              "trust this model until it does.")
            else:
                headline = "SOLVED. No closed form reference for this case."
        else:
            rd.warn(f"solve failed: {info}")
            headline = f"SOLVE FAILED: {info}"

    rd.set("headline_verdict", headline)
    if not solve_with:
        rd.action(f"Submit case_abaqus/case.inp in Abaqus, or run "
                  f"'ccx case' inside case_calculix/.")
        rd.action("Then compare the tip deflection to section 2 of this "
                  "report.")
    path = rd.write_report()
    print(f"\nreport: {path}")
    return rd


# ---------------------------------------------------------------------------
# convergence, as its own run section
# ---------------------------------------------------------------------------

def cantilever_convergence(sizes: Sequence[float], rd: RunDir,
                           c: CANT.Cantilever, F: float, step: str) -> str:
    """Verification check 1. Needs max_retries=0.

    With retries on, the R7 wall rule forces every requested size to the same
    est_t/3 value, so four different sizes produce four identical meshes and
    the study reports perfect convergence by construction. That is a defect in
    R7, not a property of the geometry.
    """
    mat = MaterialSpec(E=c.E, nu=c.nu, name="validation steel")
    pairs, rows = [], []
    work = os.path.join(rd.sub("results"), "convergence")
    os.makedirs(work, exist_ok=True)
    for size in sizes:
        tag = os.path.join(work, f"conv_s{size:g}")
        with GeomSession(step) as ses:
            fix = GF.extreme_planar_face(ses.catalogue, axis=0, side="min")
            load = GF.extreme_planar_face(ses.catalogue, axis=0, side="max")
            ses.add_selection("LOAD_FACE", load.tags, "load")
            ses.add_selection("FIX_FACE", fix.tags, "constraint")
            req = MeshRequest(step, mat, LoadCase("bending"),
                              target_size=size, out_prefix=tag,
                              solver="calculix")
            r = run_mesh_agent(req, max_retries=0, session=ses)
            GF.append_surfaces_inp(tag + ".inp",
                                   GF.surface_facets(ses.selections))
            write_case(tag + ".inp",
                       CaseSpec(material=mat, solver="calculix",
                                loads=[LoadSpec("LOAD_FACE", "force",
                                                (0, 0, -F))],
                                constraints=[ConstraintSpec("FIX_FACE",
                                                            encastre=True)]),
                       ses.node_sets, ses.selections, r.quality.n_nodes)
        ok, info = _run_ccx(tag + ".inp")
        if not ok:
            rows.append(f"  size {size:6.3f}  SOLVE FAILED: {info}")
            continue
        uz = abs(min(v[2] for v in read_frd_disp(info).values()))
        rows.append(f"  size {size:6.3f} mm   {r.quality.n_elements:8d} "
                    f"elements   uz {uz:.6f} mm")
        pairs.append((r.quality.char_size, uz))
        print(rows[-1])
    text = "\n".join(rows) + "\n\n" + convergence(pairs)
    rd.set("convergence", [{"size": s, "uz": v} for s, v in pairs])
    return text


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def cantilever_run(solvers=ALL_SOLVERS, target_size=1.0, run_root="runs",
                   sigma_target=50.0, solve_with=None,
                   converge: Optional[Sequence[float]] = None) -> RunDir:
    c = CANT.Cantilever()
    F = c.force_for_stress(sigma_target)
    print(c.render(F))

    os.makedirs(run_root, exist_ok=True)
    step = os.path.join(run_root, "_cantilever.step")
    CANT.write_step(c, step)

    with GeomSession(step) as probe:
        fix = GF.extreme_planar_face(probe.catalogue, axis=0, side="min")
        load = GF.extreme_planar_face(probe.catalogue, axis=0, side="max")
        fix_tags, load_tags = list(fix.tags), list(load.tags)

    d = c.tip_deflection(F)
    ref = {"text": c.render(F), "deflection": d["total"],
           "stress": c.root_stress(F)}

    rd = run(step, f"cantilever_L{c.L:g}_h{c.h:g}",
             MaterialSpec(E=c.E, nu=c.nu, name="validation steel"),
             load_tags, fix_tags, (0.0, 0.0, -F),
             goal="tip deflection, validated against beam theory",
             solvers=solvers, target_size=target_size, reference=ref,
             solve_with=solve_with, run_root=run_root)

    if converge:
        print("\nmesh convergence study ...")
        text = cantilever_convergence(converge, rd, c, F, step)
        rd.section("VERIFICATION CHECK 1: MESH CONVERGENCE", text)
    else:
        rd.action("Mesh convergence was NOT checked. Any agreement with the "
                  "reference may be discretisation error cancelling out. Add "
                  "--converge 2.5,1.6,1.0,0.7")
    rd.write_report()
    return rd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", nargs="?")
    ap.add_argument("--cantilever", action="store_true")
    ap.add_argument("--solvers", default="abaqus,calculix")
    ap.add_argument("--solve", default=None,
                    help="run this solver here (calculix only)")
    ap.add_argument("--converge", default=None,
                    help="comma separated element sizes, cantilever only")
    ap.add_argument("--size", type=float, default=None)
    ap.add_argument("--nlgeom", action="store_true")
    ap.add_argument("--runs", default="runs")
    a = ap.parse_args()
    solvers = tuple(s.strip() for s in a.solvers.split(",") if s.strip())
    conv = [float(x) for x in a.converge.split(",")] if a.converge else None

    if a.cantilever:
        cantilever_run(solvers=solvers, target_size=a.size or 1.0,
                       run_root=a.runs, solve_with=a.solve, converge=conv)
        return 0
    if not a.step:
        ap.error("give a STEP file, or use --cantilever")

    with GeomSession(a.step) as probe:
        print(probe.catalogue.render())
        lg = ask_face(probe.catalogue, "LOAD")
        cg = ask_face(probe.catalogue, "CONSTRAINT (what is held fixed)")
        if lg is None or cg is None:
            print("both a load face and a constraint face are required")
            return 1
        load_tags, fix_tags = list(lg.tags), list(cg.tags)

    # ---------------------------------------------------------------
    # ASK. Intent only. Every question here is something the geometry
    # cannot answer. Nothing below this line asks for physics.
    # ---------------------------------------------------------------
    matname = _pick("What material?", list(MATERIALS), 0)
    mat = MATERIALS[matname]

    goal = _pick("What are you trying to find out?", list(GOALS), 0)
    gspec = GOALS[goal]

    if gspec["needs_yield"]:
        ys = _number("  What is the yield stress, in MPa", 250.0)
        mat = MaterialSpec(E=mat.E, nu=mat.nu, yield_stress=ys,
                           plastic_response_expected=True, name=mat.name)
        print("  -> plastic response is now expected. The locking rules will "
              "treat the material as near incompressible in the plastic "
              "zone, which is where tets lock.")

    kind = _pick("What kind of load?",
                 ["concentrated force on the face (N)",
                  "pressure on the face (MPa)"], 0)
    load_kind = "force" if kind.startswith("concentrated") else "pressure"

    vec, press = (0.0, 0.0, 0.0), 0.0
    if load_kind == "force":
        axis = _pick("Which direction is the load?",
                     ["-Z", "+Z", "-Y", "+Y", "-X", "+X"], 0)
        mag = _number("How big is the load, in newtons", 100.0)
        vec = {"-Z": (0, 0, -mag), "+Z": (0, 0, mag), "-Y": (0, -mag, 0),
               "+Y": (0, mag, 0), "-X": (-mag, 0, 0),
               "+X": (mag, 0, 0)}[axis]
    else:
        press = _number("How big is the pressure, in MPa (positive pushes "
                        "INTO the surface)", 1.0)
        print("  -> a pressure has no single resultant direction, so the "
              "dominant mode cannot be derived from it. It will be reported "
              "as not computed rather than guessed.")

    hold = _pick("How is the constraint face held?",
                 ["fully fixed (all translations)",
                  "fixed in the load direction only"], 0)
    dofs = (1, 2, 3) if hold.startswith("fully") else \
        ({"1": (1,), "2": (2,), "3": (3,)}[
            str(1 + max(range(3), key=lambda i: abs(vec[i])))]
         if load_kind == "force" else (1, 2, 3))

    sv = _pick("Which solver do you want decks for?",
               ["both abaqus and calculix", "abaqus only", "calculix only"], 0)
    solvers = {"both abaqus and calculix": ("abaqus", "calculix"),
               "abaqus only": ("abaqus",),
               "calculix only": ("calculix",)}[sv]

    print("\n" + "=" * 70)
    print("EVERYTHING BELOW IS COMPUTED, NOT ASKED:")
    print("  dominant deformation mode, element family and order, mesh size,")
    print("  the retry lever, the locking verdict, the overconstraint check,")
    print("  and which solvers can deliver any cure that is needed.")
    print("=" * 70)

    run(a.step, os.path.splitext(os.path.basename(a.step))[0], mat,
        load_tags, fix_tags, vec, goal=goal, solvers=solvers,
        load_kind=load_kind, pressure=press, fix_dofs=dofs,
        target_size=a.size, nlgeom=a.nlgeom, solve_with=a.solve,
        run_root=a.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
