#!/usr/bin/env python3
"""
frd_probe.py - show exactly how THIS CalculiX build writes a .frd DISP block,
and compare a fixed-width read against a format-independent read.

Run it when a displacement looks wrong by a large factor while the reaction
forces look right. That combination means the solve is fine and the reader is
not.

    python frd_probe.py path\\to\\case.frd
"""
import re, sys

FLOAT = re.compile(r"[-+]?\d*\.?\d+(?:[EeDd][-+]?\d+)?")


def fixed_width(line):
    try:
        return int(line[3:13]), (float(line[13:25]), float(line[25:37]),
                                 float(line[37:49]))
    except Exception as e:
        return None, repr(e)


def by_layout(line):
    from frdread import _parse_line
    g = _parse_line(line[3:].rstrip("\r\n"))
    return (g[0], g[1]) if g else (None, "layout not recognised")


def by_tokens(line):
    """Format independent. Works whether the node id is I5 or I10, whether the
    values are E12.5 or E13.6, and even when two values are printed with no
    space between them, because a sign always begins a new number."""
    t = FLOAT.findall(line[3:])
    if len(t) < 4:
        return None, f"only {len(t)} numbers on the line"
    return int(float(t[0])), tuple(float(v.replace("D", "E").replace("d", "E"))
                                   for v in t[1:4])


def main(path):
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    i = next((k for k, l in enumerate(lines)
              if "DISP" in l and l.strip().startswith("-4")), None)
    if i is None:
        print("no DISP block found")
        return 1

    print("HEADER AND FIRST DATA LINES, with a character ruler")
    print("         1         2         3         4         5")
    print("123456789012345678901234567890123456789012345678901234567890")
    shown = 0
    samples = []
    for l in lines[i:]:
        if shown < 10:
            print(repr(l)[1:-1] if not l.strip().startswith("-1")
                  else l.rstrip())
            shown += 1
        if l.startswith(" -1"):
            samples.append(l)
            if len(samples) >= 3:
                break

    print()
    print("PARSE COMPARISON")
    for l in samples:
        fw = fixed_width(l)
        tk = by_layout(l)
        print(f"  fixed width : node {fw[0]}  {fw[1]}")
        print(f"  by layout   : node {tk[0]}  {tk[1]}")
        print(f"  agree       : {fw == tk}")
        print()

    # full-field extremes both ways
    fwm, tkm = 0.0, 0.0
    n = 0
    for l in lines[i:]:
        if l.strip().startswith("-3"):
            break
        if not l.startswith(" -1"):
            continue
        n += 1
        a = fixed_width(l)
        b = by_layout(l)
        if a[0] is not None:
            fwm = max(fwm, max(abs(v) for v in a[1]))
        if b[0] is not None:
            tkm = max(tkm, max(abs(v) for v in b[1]))
    print(f"nodes read            : {n}")
    print(f"max |u|, fixed width  : {fwm:.6f}")
    print(f"max |u|, by layout    : {tkm:.6f}")
    if abs(fwm - tkm) > 1e-9 * max(tkm, 1.0):
        print()
        print("THE TWO READERS DISAGREE. The fixed-width reader in "
              "results_check.py\nand invariants.py is wrong for this "
              "CalculiX build. Use frdread.read_frd_disp.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else "invtest/good_run/inv_base.frd"))
