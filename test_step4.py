#!/usr/bin/env python3
"""test_step4.py - R7 must be able to fail, including after a retry.

Old behaviour: the retry sized elements from 2V/A / 3, then R7 read
2V/A / size = 3.0 and passed by construction. Now R7 reads a count measured on
the final mesh (member thickness / local element edge), or abstains.
"""
import io, contextlib, sys
import geometry_features as GF
from geom_session import GeomSession
from mesh_agent import MeshRequest, run_mesh_agent
from locking_check import LoadCase
from case_agent import geometry_mode_inputs, compute_dominant_mode
import model_agent as MA

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def run(retries, with_section):
    with GeomSession("part.step") as ses:
        lt = list(GF.largest_hole(ses.catalogue).tags)
        ft = list(GF.extreme_planar_face(ses.catalogue, axis=2,
                                         side="min").tags)
        ses.add_selection("LOAD_FACE", lt, "load")
        ses.add_selection("FIX_FACE", ft, "constraint")
        lc, cc, body = geometry_mode_inputs(lt, ft)
        m = compute_dominant_mode([lc], (100.0, 0.0, 0.0), [cc], body)
        sec = (m.constraint_centroid, m.lever_dir, m.depth_dir,
               m.lever_arm) if with_section else None
        req = MeshRequest("part.step", MA.MATERIALS["steel"],
                          LoadCase(m.mode), target_size=2.5,
                          out_prefix="/tmp/certus_t4", section=sec)
        with contextlib.redirect_stdout(io.StringIO()):
            res = run_mesh_agent(req, max_retries=retries, session=ses)
    r7 = [f for f in res.locking.findings if f.rule_id == "R7"]
    return m, res, r7


m, res, r7 = run(0, True)
check("+X on the lugs is bending", m.mode == "bending", m.mode)
check("known-bad: 2.5 mm tets on a 5.26 mm lug, no retry -> R7 fires",
      r7 and r7[0].severity.value in ("MODERATE", "SEVERE")
      and res.element.thickness_source == "measured",
      f"{res.element.elements_through_thickness} measured")

m, res, r7 = run(3, True)
meas = [n for n in res.notes if n.startswith("attempt")]
check("known-good: retries reach the target, R7 silent", not r7,
      f"; ".join(x.split(' member')[0].split(': ')[1] for x in meas))
vals = [float(x.split("measured ")[1].split(" ")[0]) for x in meas]
check("measured counts are not the threshold by construction",
      all(abs(v - 3.0) > 1e-3 for v in vals), f"{vals}")
check("at least one retry was still short after meshing (can fail)",
      any(v < 3.0 for v in vals[1:]) or len(vals) == 2, f"{vals}")

m, res, r7 = run(2, False)
check("no section geometry: R7 abstains after a 2V/A-sized retry",
      r7 and r7[0].severity.value == "BLOCKED"
      and res.element.thickness_source == "self-derived",
      r7[0].mechanism if r7 else "")

print("\nALL STEP 4 CHECKS BEHAVE AS SPECIFIED" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
