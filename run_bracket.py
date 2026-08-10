#!/usr/bin/env python3
"""
run_bracket.py - entry point for the CAD-to-deck chain on a real STEP file.

Usage:
    python run_bracket.py part.step
    python run_bracket.py part.step --size 1.5
    python run_bracket.py part.step --oracle abaqus
    python run_bracket.py part.step --mode axial

Do NOT run mesh_agent.py directly for real work. Its __main__ block builds a
synthetic bracket and passes no GeomSession, so the deck comes out with no
named faces and nothing to attach a BC to.
"""

import argparse
import sys

import geometry_features as GF
from geom_session import GeomSession
from mesh_agent import MeshRequest, run_mesh_agent
from locking_check import MaterialSpec, LoadCase


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", help="path to the STEP file from cad_agent.py")
    ap.add_argument("--out", default="brk", help="output file prefix")
    ap.add_argument("--solver", default="calculix")
    ap.add_argument("--oracle", default=None, help="e.g. abaqus")
    ap.add_argument("--mode", default="bending",
                    choices=["bending", "axial", "shear", "torsion",
                             "mixed", "unknown"],
                    help="dominant deformation mode. WRONG VALUE HERE "
                         "SILENTLY DISABLES THE SHEAR LOCKING CHECK.")
    ap.add_argument("--size", type=float, default=None,
                    help="target element size in mm. Omit to size from "
                         "elements-across-min-dimension.")
    ap.add_argument("--across", type=int, default=4)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--yielding", action="store_true",
                    help="expect plastic response (triggers rule R2)")
    args = ap.parse_args()

    mat = MaterialSpec(E=210e3, nu=0.3,
                       yield_stress=350.0 if args.yielding else None,
                       plastic_response_expected=args.yielding,
                       name="generic steel")

    with GeomSession(args.step) as ses:
        print(ses.catalogue.render())
        print()

        # ---- face selection. Deterministic selectors, no LLM. -----------
        # Replace these two lines with your own selectors, or with
        # GF.resolve_selection("the pin hole", ses.catalogue) if you want the
        # LLM edge. It can only pick group ids the catalogue already produced.
        hole = GF.largest_hole(ses.catalogue)
        base = GF.extreme_planar_face(ses.catalogue, axis=2, side="min")

        if hole is None or base is None:
            print("SELECTION FAILED: no hole and/or no bottom face found. "
                  "Inspect the catalogue above and pick faces manually with "
                  "ses.add_selection(name, [tags], role).")
            return 1

        ses.add_selection("PIN_HOLE", hole.tags, "load")
        ses.add_selection("MOUNT_FACE", base.tags, "constraint")

        req = MeshRequest(
            step_path=args.step,
            material=mat,
            load_case=LoadCase(args.mode),
            target_size=args.size,
            elements_across_min_dim=args.across,
            out_prefix=args.out,
            solver=args.solver,
            oracle_solver=args.oracle,
        )
        res = run_mesh_agent(req, max_retries=args.retries, session=ses)

        print(ses.render_selections())
        print()
        print(res.render())

    return 0 if res.ok_to_solve else 2


if __name__ == "__main__":
    sys.exit(main())
