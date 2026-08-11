#!/usr/bin/env python3
"""Prove the single-session chain: STEP -> catalogue -> selection -> mesh
-> deck -> node sets. Every assertion here is a thing that was silently
broken before."""

import re
import geometry_features as GF
from geom_session import GeomSession
from mesh_agent import MeshRequest, run_mesh_agent, _make_test_bracket
from locking_check import MaterialSpec, LoadCase


def deck_stats(path):
    el_types, nsets = [], {}
    cur = None
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s.upper().startswith("*ELEMENT") and "type=" in s:
                el_types.append(re.search(r"type=([A-Za-z0-9]+)", s).group(1))
                cur = None
            elif s.upper().startswith("*NSET"):
                cur = re.search(r"NSET=([^,\s]+)", s, re.I).group(1)
                nsets[cur] = 0
            elif s.startswith("*"):
                cur = None
            elif cur and s:
                nsets[cur] += len([x for x in s.split(",") if x.strip()])
    return el_types, nsets


step = _make_test_bracket("bracket_test.step")
steel = MaterialSpec(E=210e3, nu=0.3, name="generic steel")

with GeomSession(step) as ses:
    print(ses.catalogue.render()[:400], "...\n")

    hole = GF.largest_hole(ses.catalogue)
    base = GF.extreme_planar_face(ses.catalogue, axis=2, side="min")
    assert hole and base, "selectors found nothing"
    ses.add_selection("PIN_HOLE", hole.tags, "load")
    ses.add_selection("MOUNT_FACE", base.tags, "constraint")

    # guard: a tag that does not exist must fail HERE, loudly
    try:
        ses.add_selection("BOGUS", [9999], "load")
        raise SystemExit("FAIL: invalid face tag accepted")
    except KeyError as e:
        print("guard ok, invalid tag rejected:", str(e)[:70])

    req = MeshRequest(step, steel, LoadCase("bending"),
                      solver="calculix", out_prefix="chain")
    res = run_mesh_agent(req, session=ses)
    print()
    print(ses.render_selections())
    print()
    print(res.render())

types, nsets = deck_stats("chain.inp")
print("\nDECK CHECK")
print("  element types :", types)
print("  node sets     :", nsets)

assert types == ["C3D10"], f"deck polluted with non-solid elements: {types}"
assert set(nsets) == {"PIN_HOLE", "MOUNT_FACE"}, f"node sets wrong: {nsets}"
assert all(v > 0 for v in nsets.values()), "empty node set written"
print("\nALL ASSERTIONS PASSED")
