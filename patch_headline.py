#!/usr/bin/env python3
"""
patch_headline.py - run once, from the Certus folder.

Fixes: a run that printed "OK TO SOLVE: False" then reported
"HEADLINE: SOLVED. No closed form reference for this case."

A verification layer that computes a blocking finding and then publishes a
headline which does not mention it has hidden its own verdict. Refusal, or at
minimum a qualified verdict, has to be a first class output. Nothing else in
the reviewed literature does this, and Certus was violating it in its own
report.

    python patch_headline.py
"""
import shutil, sys

F = "model_agent.py"
ANCHOR = '    rd.set("headline_verdict", headline)'
NEW = '''    # ---- unresolved physics findings must reach the headline -----------
    # A run that computed a MODERATE or worse locking finding, never cleared
    # it, and then published "SOLVED" is asserting a confidence it did not
    # earn. The numbers may still be useful, but the headline must carry the
    # qualification, and run.json must carry it in machine readable form so a
    # benchmark table can aggregate it.
    unresolved = []
    try:
        unresolved = lreport.actionable() if lreport else []
    except NameError:
        unresolved = []
    if unresolved:
        ids = ", ".join(f"{f.rule_id} {f.severity.value}" for f in unresolved)
        if headline.startswith("SOLVED"):
            headline = (f"SOLVED, RESULT NOT TRUSTWORTHY. Unresolved physics "
                        f"finding(s): {ids}")
        rd.warn(f"The locking check reported {ids} and it was never cleared. "
                f"Every number in this run carries that bias.")
        rd.action(f"Resolve {ids} before quoting any number from this run. "
                  f"See the cure availability table above: a cure that no "
                  f"available solver offers is a stack decision, not a fix.")
    rd.set("unresolved_findings",
           [{"rule": f.rule_id, "severity": f.severity.value}
            for f in unresolved])
    rd.set("result_trustworthy", not unresolved)

'''

src = open(F, encoding="utf-8").read()
if "unresolved physics findings must reach the headline" in src:
    print("already patched, nothing to do")
    sys.exit(0)
if ANCHOR not in src:
    print(f"anchor line not found in {F}. Patch NOT applied.")
    print(f"expected to find: {ANCHOR!r}")
    sys.exit(1)

shutil.copy2(F, F + ".bak")
open(F, "w", encoding="utf-8").write(src.replace(ANCHOR, NEW + ANCHOR, 1))
print(f"patched {F}, original saved as {F}.bak")
print("re-run the bracket case and check the HEADLINE line in REPORT.txt")
