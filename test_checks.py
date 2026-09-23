#!/usr/bin/env python3
"""test_checks.py - seed, solve, verdict for each of the seven checks.

For every check: one seeded defect that CalculiX runs with exit 0 and no
warning, where the check returns FAIL; and a known-good case where it returns
PASS. Where a check can abstain, the abstention is tested too. Needs ccx.
"""
import os, shutil, sys, tempfile
import invariants as INV
from invariants import Intent

W = tempfile.mkdtemp(prefix="certus_chk_")
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


def nid(n, i, j, k, off=0):
    return off + 1 + i + (n + 1) * (j + (n + 1) * k)


def block(n=2, z0=0.0, off=0, eoff=0, elset="EALL"):
    h = 10.0 / n
    N = [f"{nid(n,i,j,k,off)}, {i*h}, {j*h}, {z0+k*h}"
         for k in range(n + 1) for j in range(n + 1) for i in range(n + 1)]
    E, e = [], eoff + 1
    for k in range(n):
        for j in range(n):
            for i in range(n):
                c = [nid(n, *p, off) for p in [(i,j,k),(i+1,j,k),(i+1,j+1,k),
                     (i,j+1,k),(i,j,k+1),(i+1,j,k+1),(i+1,j+1,k+1),(i,j+1,k+1)]]
                E.append(f"{e}, " + ", ".join(map(str, c))); e += 1
    return N, [f"*ELEMENT, TYPE=C3D8, ELSET={elset}"] + E


def deck(name, n=2, bc=("BOT, 1, 3",), loads=None, E="210000.",
         mat_extra="", step="*STEP", proc="*STATIC", extra_cards="",
         load_set=None, steps=None):
    N, El = block(n)
    L = ["*NODE"] + N + El
    L += ["*NSET, NSET=BOT"] + [f"{nid(n,i,j,0)}," for i in range(n+1)
                                for j in range(n+1)]
    top = [nid(n, i, j, n) for i in range(n + 1) for j in range(n + 1)]
    L += ["*NSET, NSET=TOP"] + [f"{t}," for t in top]
    if load_set:
        L += [f"*NSET, NSET={load_set[0]}"] + [f"{t}," for t in load_set[1]]
    L += ["*MATERIAL, NAME=S", "*ELASTIC", f"{E}, 0.3"]
    L += [mat_extra] if mat_extra else []
    L += ["*SOLID SECTION, ELSET=EALL, MATERIAL=S", extra_cards]
    if steps is None:
        if loads is None:
            loads = [f"{t}, 3, -10." for t in top]
        steps = [(step, proc, list(bc), loads)]
    first = True
    for st, pr, b, ld in steps:
        L += [st, pr]
        if first:
            L += ["*BOUNDARY"] + b
            first = False
        L += ["*CLOAD"] + ld + ["*NODE FILE", "U", "*END STEP"]
    p = os.path.join(W, name + ".inp")
    open(p, "w").write("\n".join(x for x in L if x) + "\n")
    return p, top


def solved(p):
    r = INV.solve(p)
    return r, (r["converged"] and "WARNING" not in r["stdout"].upper())


steel = dict(units="N-mm-MPa", E_GPa=210.0, material_class="elastic",
             rate_dependent=False, nlgeom=False)

# ---- 1 LOAD vs INTENT ------------------------------------------------------
p, top = deck("c1_good", load_set=("LOAD_FACE", None) and None)
p, top = deck("c1_good", load_set=("LOAD_FACE", [nid(2, i, j, 2)
              for i in range(3) for j in range(3)]))
it = Intent(force=(0, 0, -90.0), load_set="LOAD_FACE", **steel)
f = INV.check_load_intent(INV.read_deck(p), it)
check("1 good: stated 90 N on the top, deck agrees", f.verdict == "PASS", f.detail[:70])
p, _ = deck("c1_units", E="210.", load_set=("LOAD_FACE", top))
r, clean = solved(p)
f = INV.check_load_intent(INV.read_deck(p), it)
check("1 seed E=210 in N-mm-MPa: solves clean, check FAILs",
      clean and f.verdict == "FAIL" and "factor 1e+03" in f.detail, f.detail[:90])
ld = [f"{t}, 3, -10." for t in top[1:]] + [f"{nid(2,0,0,0)}, 3, -10."]
p, _ = deck("c1_shared", loads=ld, load_set=("LOAD_FACE", top))
r, clean = solved(p)
f = INV.check_load_intent(INV.read_deck(p), it)
check("1 seed load node on the clamp: solves clean, check FAILs",
      clean and f.verdict == "FAIL" and "constrained" in f.detail, f.detail[:90])
n4 = 4
left = [nid(n4, 0, j, n4) for j in range(n4 + 1)]
right = [nid(n4, n4, j, n4) for j in range(n4 + 1)]
ld = [f"{t}, 3, -18." for t in left]
p, _ = deck("c1_onelug", n=n4, loads=ld, load_set=("LOAD_FACE", left + right))
r, clean = solved(p)
f = INV.check_load_intent(INV.read_deck(p), it)
check("1 seed one of two load patches unloaded: clean, FAILs",
      clean and f.verdict == "FAIL" and "carries 0%" in f.detail, f.detail[-90:])
ld = [f"{t}, 3, -9." for t in left + right]
p, _ = deck("c1_twolug", n=n4, loads=ld, load_set=("LOAD_FACE", left + right))
f = INV.check_load_intent(INV.read_deck(p), it)
check("1 good: both patches loaded", f.verdict == "PASS", f.detail[:60])
f = INV.check_load_intent(INV.read_deck(p), Intent())
check("1 abstains without stated intent", f.verdict == "NOT EVALUATED")

# ---- 2 SMALL STRAIN --------------------------------------------------------
p, top = deck("c2_good")
r, clean = solved(p)
f = INV.check_small_strain(INV.read_deck(p), INV.read_frd_disp(r["frd"]), Intent(**steel))
check("2 good: 90 N on a steel cube", f.verdict == "PASS", f.detail)
p, top = deck("c2_bad", loads=[f"{t}, 1, 3.e5" for t in top])
r, clean = solved(p)
f = INV.check_small_strain(INV.read_deck(p), INV.read_frd_disp(r["frd"]), Intent(**steel))
check("2 seed 2.7 MN shear, NLGEOM off: clean, FAILs",
      clean and f.verdict == "FAIL", f.detail[:80])
p2 = p.replace(".inp", "_nl.inp")
open(p2, "w").write(open(p).read().replace("*STEP", "*STEP, NLGEOM"))
f = INV.check_small_strain(INV.read_deck(p2), INV.read_frd_disp(r["frd"]), Intent(**steel))
check("2 abstains when NLGEOM is on", f.verdict == "NOT EVALUATED")

# ---- 3 PENETRATION ---------------------------------------------------------
def contact(name, K, n=4):
    Nl, El = block(n, 0.0, 0, 0, "LOW")
    Nu, Eu = block(n, 10.0, 1000, n ** 3, "UP")
    L = ["*NODE"] + Nl + Nu + El + Eu
    L += ["*NSET, NSET=BOT"] + [f"{nid(n,i,j,0)}," for i in range(n+1) for j in range(n+1)]
    L += ["*NSET, NSET=TOPU"] + [f"{nid(n,i,j,n,1000)}," for i in range(n+1) for j in range(n+1)]
    L += ["*NSET, NSET=SLAVEN"] + [f"{nid(n,i,j,0,1000)}," for i in range(n+1) for j in range(n+1)]
    L += ["*ELSET, ELSET=MTOP"] + [f"{1+i+n*(j+n*(n-1))}," for i in range(n) for j in range(n)]
    L += ["*SURFACE, NAME=MSURF, TYPE=ELEMENT", "MTOP, S2",
          "*SURFACE, NAME=SSURF, TYPE=NODE", "SLAVEN",
          "*MATERIAL, NAME=S", "*ELASTIC", "210000., 0.3",
          "*SOLID SECTION, ELSET=LOW, MATERIAL=S",
          "*SOLID SECTION, ELSET=UP, MATERIAL=S",
          "*SURFACE INTERACTION, NAME=SI",
          "*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR", f"{K}, 0.",
          "*CONTACT PAIR, INTERACTION=SI, TYPE=NODE TO SURFACE", "SSURF, MSURF",
          "*STEP, INC=100", "*STATIC", "0.1, 1., 1e-5, 0.1", "*BOUNDARY", "BOT, 1, 3",
          "TOPU, 1, 2", "*CLOAD", "TOPU, 3, -2000.", "*NODE FILE", "U",
          "*END STEP"]
    p = os.path.join(W, name + ".inp")
    open(p, "w").write("\n".join(L) + "\n")
    return p
for tag, K, want in (("good", 1e7, "PASS"), ("bad", 1e5, "FAIL")):
    p = contact("c3_" + tag, K)
    r, clean = solved(p)
    f = INV.check_penetration(INV.read_deck(p), INV.read_frd_disp(r["frd"]), Intent())
    check(f"3 {tag}: penalty {K:g}{', clean' if tag == 'bad' else ''}",
          clean and f.verdict == want, f.detail)
p, _ = deck("c3_none")
f = INV.check_penetration(INV.read_deck(p), {}, Intent())
check("3 not needed without a contact pair", f.verdict == "NOT NEEDED")

# ---- 4 ZERO-ENERGY MODES ---------------------------------------------------
for tag, bc, want, nfree in (("clamp", "BOT, 1, 3", "PASS", 0),
                             ("roller", "BOT, 3, 3", "FAIL", 3),
                             # in-plane DOFs fixed on a plane: Tz and the two
                             # tilts about in-plane axes stay free
                             ("fixXY", "BOT, 1, 2", "FAIL", 3)):
    p, _ = deck("c4_" + tag, bc=(bc,))
    f = INV.check_rigid_modes(INV.read_deck(p))
    r, clean = solved(p)
    got = int(f.detail.split("keeps ")[1].split()[0]) if "keeps" in f.detail else 0
    check(f"4 {tag}: {want}, {nfree} free mode(s), ccx exit clean={clean}",
          f.verdict == want and got == nfree and clean, f.detail[:90])
p, _ = deck("c4_eq", extra_cards="*EQUATION\n2\n1, 1, 1., 2, 1, -1.")
f = INV.check_rigid_modes(INV.read_deck(p))
check("4 abstains on *EQUATION", f.verdict == "NOT EVALUATED", f.detail)

# ---- 5 REVERSIBILITY -------------------------------------------------------
plastic = "*PLASTIC\n100., 0.\n200., 0.1"
for tag, mag, want in (("below", -50., "PASS"), ("past", -3000., "FAIL")):
    p, top = deck("c5_" + tag, mat_extra=plastic,
                  step="*STEP, INC=1000", proc="*STATIC\n0.1, 1., 1e-5, 0.1",
                  loads=None)
    txt = open(p).read().replace(", 3, -10.", f", 3, {mag}")
    open(p, "w").write(txt)
    r, clean = solved(p)
    f = INV.check_reversibility(INV.read_deck(p), Intent(**steel), W)
    check(f"5 declared elastic, *PLASTIC, load {tag} yield: {want}"
          f"{', clean' if tag == 'past' else ''}",
          clean and f.verdict == want, f.detail[:90])
f = INV.check_reversibility(INV.read_deck(p), Intent(**{**steel,
                            "material_class": "elastic-plastic"}), W)
check("5 abstains when declared elastic-plastic", f.verdict == "NOT EVALUATED")
p, _ = deck("c5_plain")
f = INV.check_reversibility(INV.read_deck(p), Intent(**steel), W)
check("5 not needed without a path-dependent card", f.verdict == "NOT NEEDED")

# ---- 6 RATE INDEPENDENCE ---------------------------------------------------
for tag, A, want in (("inactive", "1.e-30", "PASS"), ("creep", "1.e-6", "FAIL")):
    p, _ = deck("c6_" + tag, mat_extra=f"*CREEP, LAW=NORTON\n{A}, 3., 0.",
                n=3, proc="*VISCO, CETOL=1e-3\n0.01, 1.", step="*STEP, INC=1000",
                loads=["TOP, 3, -100."])
    r, clean = solved(p)
    f = INV.check_rate(INV.read_deck(p), Intent(**steel), W,
                       INV.read_frd_disp(r["frd"]))
    check(f"6 declared rate-independent, *CREEP A={A}: {want}"
          f"{', clean' if tag == 'creep' else ''}",
          clean and f.verdict == want, f.detail[:90])
p, _ = deck("c6_plain")
f = INV.check_rate(INV.read_deck(p), Intent(**steel), W, {})
check("6 not needed without a rate-dependent card", f.verdict == "NOT NEEDED")
f = INV.check_rate(INV.read_deck(p), Intent(**{**steel, "rate_dependent": True}), W, {})
check("6 abstains when declared rate-dependent", f.verdict == "NOT EVALUATED")

# ---- 7 INCREMENT CONVERGENCE -----------------------------------------------
hard = "*PLASTIC\n250., 0.\n400., 0.2"
for tag, inc, want in (("coarse", "1., 1., 1e-5, 1.", "FAIL"),
                       ("fine", "0.02, 1., 1e-5, 0.02", "PASS")):
    steps = [("*STEP, NLGEOM, INC=1000", "*STATIC\n0.1, 1., 1e-5, 0.1",
              ["BOT, 1, 3"], ["TOP, 1, 600."]),
             ("*STEP, NLGEOM, INC=1000", f"*STATIC\n{inc}",
              [], ["TOP, 3, -1200."])]
    p, _ = deck("c7_" + tag, n=4, mat_extra=hard, steps=steps)
    r, clean = solved(p)
    f = INV.check_increments(INV.read_deck(p), W, INV.read_frd_disp(r["frd"]))
    check(f"7 non-proportional plastic step, increment {inc.split(',')[0]}: {want}"
          f"{', clean' if tag == 'coarse' else ''}",
          clean and f.verdict == want, f.detail[:90])
p, _ = deck("c7_lin")
f = INV.check_increments(INV.read_deck(p), W, {})
check("7 not needed on a linear step", f.verdict == "NOT NEEDED")

shutil.rmtree(W, ignore_errors=True)
print("\nALL SEVEN CHECKS PASS SEED/SOLVE/VERDICT" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
