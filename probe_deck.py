"""
probe_deck.py

Read-back probe. Answers one question: does certus_physics.read_deck() correctly
parse the deck that mesh_agent actually writes?

Usage, from the Certus folder, in the cadagent env:

    python probe_deck.py brk.inp
    python probe_deck.py runs\\bracket_01\\brk.inp

Compare the counts below against what verify_sets.py reports. If they disagree,
the parser is wrong and Step 1 must not proceed.
"""

import os
import sys

import invariants as P


def main():
    if len(sys.argv) < 2:
        print("usage: python probe_deck.py <path to deck.inp>")
        return 1
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"NOT FOUND: {os.path.abspath(path)}")
        return 1

    d = P.read_deck(path)

    print(f"deck            : {os.path.abspath(path)}")
    print(f"nodes           : {len(d.nodes)}")
    print(f"elements        : {len(d.elements)}")
    print(f"element types   : {sorted(d.element_types)}")
    print(f"keywords present: {sorted(set(d.keywords))}")
    print()
    print("ELSETS")
    for k, v in d.elsets.items():
        print(f"  {k:24s} {len(set(v)):8d} elements")
    print("NSETS")
    for k, v in d.nsets.items():
        print(f"  {k:24s} {len(set(v)):8d} nodes")
    print()

    # sanity checks that do not need any Certus code to be correct
    problems = []
    bad = d.element_types & P.NON_SOLID
    if bad:
        problems.append(f"non-solid element types present: {sorted(bad)}")
    unknown = {t for t in d.element_types if t not in P.ELEMENT_NODES}
    if unknown:
        problems.append(f"element types the writer does not know: {sorted(unknown)}")
    for eid, (etype, conn) in d.elements.items():
        want = P.ELEMENT_NODES.get(etype)
        if want and len(conn) != want:
            problems.append(
                f"element {eid} of type {etype} parsed with {len(conn)} nodes, "
                f"expected {want}  -> connectivity parsing is wrong")
            break
    used = {n for _, conn in d.elements.values() for n in conn}
    missing = used - set(d.nodes)
    if missing:
        problems.append(f"{len(missing)} nodes referenced by elements but not "
                        f"defined in *NODE")
    orphan = set(d.nodes) - used
    if orphan:
        problems.append(f"{len(orphan)} nodes defined but used by no element "
                        f"(may be legitimate, check)")
    for name, members in d.nsets.items():
        outside = set(members) - set(d.nodes)
        if outside:
            problems.append(f"NSET {name} references {len(outside)} undefined nodes")
    for name, members in d.elsets.items():
        outside = set(members) - set(d.elements)
        if outside:
            problems.append(f"ELSET {name} references {len(outside)} undefined elements")

    if problems:
        print("PROBLEMS")
        for p in problems:
            print(f"  - {p}")
    else:
        print("PROBLEMS: none. The parse is self-consistent.")

    print()
    print("Cross-check these numbers against verify_sets.py before Step 1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
