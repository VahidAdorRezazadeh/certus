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
                        reconcile_load_case, compute_dominant_mode,
                        geometry_mode_inputs)
from results_check import (read_frd_disp, convergence, Comparison,
                           ccx_outcome)
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
    """Numbered from 1. Enter takes the default. A wrong entry is asked
    again, never silently replaced by the default: the old menu turned a
    typo into a different load direction without saying so."""
    print(f"\n{prompt}")
    for i, o in enumerate(options, 1):
        print(f"  [{i}] {o}" + ("  (default)" if i - 1 == default else ""))
    for _ in range(3):
        raw = input("  choice: ").strip()
        if not raw:
            return options[default]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print(f"  '{raw}' is not 1 to {len(options)}. Try again.")
    raise SystemExit("no valid choice after 3 tries; nothing was run")


def _number(prompt: str, default: float) -> float:
    """Enter takes the default. A non-number is asked again, never replaced
    by the default: a typo in a load magnitude must not become 100 N."""
    for _ in range(3):
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print(f"  '{raw}' is not a number. Try again.")
    raise SystemExit("no valid number after 3 tries; nothing was run")


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
    out = ccx_outcome(log, p.returncode, base, deck_path)
    if not out.converged or not os.path.exists(base + ".frd"):
        return False, out.reason if not out.converged else "no .frd written"
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
        asserted_mode: Optional[str] = None,
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

        # ---- dominant mode BEFORE meshing, because the mesh retry lever
        # depends on it. Order of authority: computed from the geometry and
        # the force; else asserted by the user; else an ASSUMED default that
        # is reported as such. Never labelled as something the user said.
        pre = None
        if load_kind == "force" and any(abs(c) > 0 for c in force):
            lc, cc, body = geometry_mode_inputs(load_tags, fix_tags)
            pre = compute_dominant_mode([lc], tuple(force), [cc], body)
        if pre is not None and pre.mode != "unknown":
            mode_src, mode0 = "COMPUTED BEFORE MESHING", pre.mode
        elif asserted_mode:
            mode_src, mode0 = "ASSERTED", asserted_mode
        else:
            mode_src, mode0 = "ASSUMED", "bending"
            rd.warn("The dominant mode could not be computed (no force "
                    "resultant) and was not stated. The mesh was sized for "
                    "'bending', the conservative default because it enables "
                    "the shear locking rules. This is an assumption, not a "
                    "finding.")
        rd.section("DOMINANT MODE USED TO SIZE THE MESH",
                   f"  source  {mode_src}\n  mode    {mode0}"
                   + (f"\n\n{pre.render()}" if pre is not None else ""))
        rd.set("mode_for_meshing", {"mode": mode0, "source": mode_src})
        assumed = LoadCase(mode0)
        req = MeshRequest(step_path, material, assumed,
                          target_size=target_size,
                          out_prefix=rd.prefix("mesh", "mesh"),
                          solver="calculix",
                          section=((pre.constraint_centroid, pre.lever_dir,
                                    pre.depth_dir, pre.lever_arm)
                                   if pre is not None and pre.lever_dir
                                   else None))
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

        # ---- how the force enters a hole: computed, not asked ------------
        # A pin loads only the half of a hole it presses on. A uniform
        # traction over all 360 degrees pulls the far side of the hole
        # toward the load, which no pin can do.
        dist, axes = "uniform", None
        if load_kind == "force":
            try:
                lf = [ses.catalogue.face(int(t)) for t in load_tags]
            except KeyError:
                lf = []
            fmag = sum(v * v for v in force) ** 0.5
            if lf and fmag > 0 and all(f.is_hole and f.axis and f.axis_point
                                       for f in lf):
                axial = max(abs(sum(f.axis[k] * force[k] for k in range(3)))
                            / fmag for f in lf)
                if axial < 0.1:
                    dist = "bearing"
                    axes = {f.tag: (f.axis, f.axis_point) for f in lf}
                else:
                    rd.warn(f"The load face is a hole but the force has "
                            f"{axial:.0%} of its magnitude along the hole "
                            f"axis. A pin cannot transmit that by bearing. "
                            f"Loaded as a uniform traction instead.")
        rd.set("load_distribution", dist)

        decks, creport = {}, None
        for sv in solvers:
            deck = rd.case(sv, "case.inp")
            with open(mres.deck_path) as src, open(deck, "w") as dst:
                dst.write(src.read())
            GF.append_surfaces_inp(deck, facets)
            spec = CaseSpec(material=material, solver=sv, nlgeom=nlgeom,
                            loads=[LoadSpec("LOAD_FACE", load_kind,
                                            tuple(force),
                                            pressure=pressure,
                                            distribution=dist,
                                            bearing_axes=axes)],
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

        clashes = reconcile_load_case(creport.mode, assumed, mode_src)
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
    # ---- the seven checks: stated intent, from the answers, not the deck
    import invariants as INV
    intent = INV.Intent(
        force=tuple(force) if load_kind == "force" else None,
        units="N-mm-MPa", E_GPa=material.E / 1000.0,
        material_class=("elastic-plastic" if material.plastic_response_expected
                        else "elastic"),
        rate_dependent=False, nlgeom=nlgeom, load_set="LOAD_FACE")
    checks: list = []
    refused = None
    if "calculix" in decks:
        f4 = INV.check_rigid_modes(INV.read_deck(decks["calculix"]))
        if f4.verdict == "FAIL":
            refused = f4
            rd.warn(f"REFUSED TO SOLVE. {f4.detail}. CalculiX would run this "
                    f"with exit 0 and a plausible-looking answer.")
            rd.action(f"{f4.cure}.")
    if solve_with and refused is not None:
        headline = (f"REFUSED. The supports leave free rigid-body modes "
                    f"({refused.detail.split(': ', 1)[-1]}). Not solved.")
        checks = [refused]
        solve_with = None
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
            # ---- the displacement that answers "how stiff": work
            # conjugate to the applied load, sum(F_i . u_i) / |F|. Max |U|
            # anywhere is a local peak and is reported only for context.
            try:
                from invariants import read_deck
                dk = read_deck(deck)
                disp = read_frd_disp(info)
                F = [0.0, 0.0, 0.0]
                work = 0.0
                for nid, dof, val in dk.cloads:
                    if 1 <= dof <= 3 and nid in disp:
                        F[dof - 1] += val
                        work += val * disp[nid][dof - 1]
                fm = sum(v * v for v in F) ** 0.5
                if fm > 0:
                    delta = work / fm
                    umax = max(sum(c * c for c in v) ** 0.5
                               for v in disp.values())
                    rd.set("load_point_displacement_mm", delta)
                    rd.set("max_abs_U_mm", umax)
                    rd.set("stiffness_N_per_mm", fm / delta if delta else None)
                    rd.section("LOAD-POINT DISPLACEMENT (work conjugate)",
                               f"  sum(F.u)/|F|   {delta:.6e} mm along the "
                               f"load\n  stiffness      {fm / delta:.4g} N/mm"
                               f"\n  max |U|        {umax:.6e} mm (local "
                               f"peak, context only)")
                    if not reference:
                        headline = (f"SOLVED. Load-point displacement "
                                    f"{delta:.4e} mm, stiffness "
                                    f"{fm / delta:.4g} N/mm. No closed form "
                                    f"reference for this case.")
            except Exception as exc:          # report, never hide
                rd.warn(f"load-point displacement not computed: {exc}")
        else:
            rd.warn(f"solve failed: {info}")
            headline = f"SOLVE FAILED: {info}"

    if solve_with and headline.startswith("SOLVED"):
        checks = INV.run_checks(decks[solve_with], intent,
                                workdir=os.path.join(rd.path, "checks"),
                                solved={"converged": True, "frd": info},
                                verbose=False)
        # Step 8: is a fully fixed support acting like a real seat? Reported
        # as a caveat on the stated idealisation, never silently.
        sup = INV.check_support_reactions(
            INV.read_deck(decks[solve_with]), info, "FIX_FACE")
        rd.section("SUPPORT REACTIONS (constraint realism)", sup.render())
        rd.set("support_reactions", {"verdict": sup.verdict,
                                     "detail": sup.detail})
        if sup.verdict == "FAIL":
            rd.warn("The fixed support does not behave like a surface the "
                    "part rests on: " + sup.detail + ". The result is stiffer "
                    "than a bolted joint.")
    if checks:
        rd.section("THE SEVEN CHECKS (deterministic, each passed "
                   "seed/solve/verdict)",
                   "\n".join(f.render() for f in checks)
                   + "\n\nBlind spot: a load on a wrong but plausible face "
                     "passes all seven.")
        rd.set("checks", [{"rule": f.rule, "verdict": f.verdict,
                           "detail": f.detail, "owner": f.owner,
                           "lever": f.cure} for f in checks])

    # ---- trust verdict ---------------------------------------------------
    # result_trustworthy is only as wide as what was checked. It says which
    # checks it covers and which it does not, so a True is never read as
    # "verified". None means no solve result exists to judge.
    blockers = [{"source": "locking", "id": f.rule_id,
                 "severity": f.severity.value}
                for f in lreport.actionable()]
    for f in checks:
        if f.verdict == "FAIL":
            blockers.append({"source": "seven checks",
                             "id": f.rule.split()[0] + "_" +
                             "_".join(f.rule.split()[1:3]),
                             "severity": "FAIL", "detail": f.detail})
    if clashes:
        blockers.append({"source": "load case", "id": "MODE_MISMATCH",
                         "severity": "MODERATE",
                         "detail": clashes[0]})
    caveats = [{"source": "case agent", "detail": w}
               for w in creport.warnings]
    if rd.meta.get("support_reactions", {}).get("verdict") == "FAIL":
        caveats.append({"source": "support reactions",
                        "detail": rd.meta["support_reactions"]["detail"]})
    solved = headline.startswith("SOLVED")
    abstained = [f.rule_id for f in lreport.findings
                 if f.severity.value == "BLOCKED"]
    checked = [("locking rules R1-R7" if not abstained else
                "locking rules except " + ", ".join(abstained)),
               "load case consistency"] + \
        [f"check {f.rule}" for f in checks if f.verdict in ("PASS", "FAIL")]
    not_checked = [f"{r} (abstained, input not measurable)"
                   for r in abstained] + \
        [f"check {f.rule} ({f.verdict.lower()})" for f in checks
         if f.verdict == "NOT EVALUATED"] + \
        ["overconstraint (node count heuristic only, reported as a caveat)",
         "mesh convergence"]
    if blockers:
        ids = ", ".join(f"{b['id']} {b['severity']}" for b in blockers)
        if solved:
            headline = (f"SOLVED, RESULT NOT TRUSTWORTHY. Unresolved "
                        f"finding(s): {ids}")
        rd.warn(f"Unresolved finding(s) {ids}. They were never cleared.")
        rd.action(f"Resolve {ids} before quoting any number from this run. "
                  f"See the cure availability table above: a cure that no "
                  f"available solver offers is a stack decision, not a fix.")
    elif solved:
        headline = headline.rstrip(". ") + ". " + ("Checked: " + ", ".join(checked) + ". Not checked: "
                     + ", ".join(not_checked) + ".")
    verdict = (not blockers) if solved else None
    rd.set("result_trustworthy", verdict)
    rd.set("trust", {"verdict": verdict, "blockers": blockers,
                     "caveats": caveats, "checked": checked,
                     "not_checked": not_checked})
    rd.set("unresolved_findings", blockers)

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
    ap.add_argument("--solvers", default=None,
                    help="comma list; on the STEP path it replaces the "
                         "solver question")
    ap.add_argument("--solve", default=None,
                    help="run this solver here (calculix only)")
    ap.add_argument("--converge", default=None,
                    help="comma separated element sizes, cantilever only")
    ap.add_argument("--size", type=float, default=None)
    ap.add_argument("--nlgeom", action="store_true")
    ap.add_argument("--runs", default="runs")
    a = ap.parse_args()
    solvers = tuple(s.strip() for s in (a.solvers or "abaqus,calculix")
                    .split(",") if s.strip())
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

    vec, press, asserted = (0.0, 0.0, 0.0), 0.0, "not sure"
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
              "dominant mode cannot be derived from it here.")
        asserted = _pick("What deformation dominates? (it sizes the mesh; "
                         "'not sure' uses bending and reports it as an "
                         "assumption)",
                         ["not sure", "bending", "axial", "shear", "torsion"],
                         0)

    hold = _pick("How is the constraint face held?",
                 ["fully fixed (all translations)",
                  "fixed in the load direction only"], 0)
    dofs = (1, 2, 3) if hold.startswith("fully") else \
        ({"1": (1,), "2": (2,), "3": (3,)}[
            str(1 + max(range(3), key=lambda i: abs(vec[i])))]
         if load_kind == "force" else (1, 2, 3))

    if a.solvers is None:
        sv = _pick("Which solver do you want decks for?",
                   ["both abaqus and calculix", "abaqus only",
                    "calculix only"], 0)
        solvers = {"both abaqus and calculix": ("abaqus", "calculix"),
                   "abaqus only": ("abaqus",),
                   "calculix only": ("calculix",)}[sv]
    else:
        print(f"\nsolvers from --solvers: {', '.join(solvers)}")
    if a.solve and a.solve not in solvers:
        raise SystemExit(f"--solve {a.solve} needs a {a.solve} deck, but the "
                         f"solvers are {solvers}")

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
        asserted_mode=None if asserted == "not sure" else asserted,
        run_root=a.runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
