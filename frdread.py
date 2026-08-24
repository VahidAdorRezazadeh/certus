"""Width-detecting .frd displacement reader, shared by results_check and
invariants so there is exactly one copy of this logic."""
import re
from typing import Dict, Optional, Tuple

# candidate node-id field widths and value field widths seen in the wild
_NODEW = (10, 5)
_VALW = (12, 13, 14, 15, 16, 20)


def detect_layout(sample_lines) -> Optional[Tuple[int, int]]:
    """Return (node_width, value_width) from the length of the data lines.

    A CalculiX .frd data line is ' -1' + I<nodew> + 3 * E<valw>. Linux builds
    print a two-digit exponent so valw is 12; MinGW/Windows builds print a
    three-digit exponent (0.00000E+000) so valw is 13. Both were measured on
    the same deck. Guessing 12 read a tip deflection of 81.689800 mm where the
    true value was 0.315876 mm.
    """
    for ln in sample_lines:
        rest = ln[3:].rstrip("\r\n")
        for nw in _NODEW:
            rem = len(rest) - nw
            if rem <= 0 or rem % 3:
                continue
            vw = rem // 3
            if vw not in _VALW:
                continue
            try:
                int(rest[:nw])
                for k in range(3):
                    float(rest[nw + k * vw: nw + (k + 1) * vw]
                          .replace("D", "E").replace("d", "E"))
                return nw, vw
            except ValueError:
                continue
    return None


# The exponent width is tried explicitly, widest first. A greedy \d+ is wrong:
# on the glued line '1.18518E-0025.47570E-006' it swallows the leading 5 of the
# next value as a fourth exponent digit and the line then fails to parse.
_VALPATS = [re.compile(r"[-+]?\d\.\d+[EeDd][-+]\d{3}"),
            re.compile(r"[-+]?\d\.\d+[EeDd][-+]\d{2}"),
            re.compile(r"[-+]?\d*\.\d+[EeDd][-+]\d+")]


def _parse_line(rest: str):
    """Parse one .frd data line by anchoring on the exponent, not on columns.

    Fixed widths do not exist here. CalculiX writes E12.5, but with a
    three-digit exponent the field overflows and the leading space is dropped,
    so a positive value is 12 characters and a negative one is 13. Measured on
    a real line:

        '         61.18518E-0025.47570E-006-3.15866E-001'
         node 6, then 12 + 12 + 13 characters.

    Each value always ends in E<sign><digits> and has exactly one digit before
    the decimal point, so the pattern is unambiguous even when values are
    glued together and even when the node id is glued to the first value. The
    node id is whatever precedes the first value.
    """
    for pat in _VALPATS:
        m = list(pat.finditer(rest))
        if len(m) != 3:
            continue
        head = rest[:m[0].start()].strip()
        if not head or not head.lstrip("+-").isdigit():
            continue
        try:
            return int(head), tuple(
                float(x.group().replace("D", "E").replace("d", "E"))
                for x in m)
        except ValueError:
            continue
    return None


def read_frd_disp(path: str) -> Dict[int, Tuple[float, float, float]]:
    """Node displacements from a CalculiX .frd, layout detected not assumed.

    Two readers were tried before this one and both were wrong.

    Fixed columns [3:13][13:25][25:37][37:49] assume a two-digit exponent.
    MinGW and Windows builds print three digits, 0.00000E+000, so the values
    are 13 characters. Measured on one identical deck: the fixed reader gave
    81.689800 mm where the true tip deflection was 0.315876 mm.

    Extracting numbers by regular expression is subtly worse. On an all-zero
    line the node id and the first value glue into '10.00000E+000' with no
    sign between them, so only three numbers are found and the line is
    dropped. On a loaded model every line carries minus signs and the answer
    looks right. On a ZERO-LOAD model no line does, so the reader returns an
    empty field and the zero-load invariant reports NOT EVALUATED instead of
    PASS. A reader that is only visibly wrong when the answer is zero is worse
    than one that is always wrong.

    The width is therefore derived from the length of each data line, which is
    unambiguous: after the node field, whatever remains divides evenly by
    three.
    """
    raw = open(path, encoding="utf-8", errors="replace").read().splitlines()
    start = next((i for i, l in enumerate(raw)
                  if "DISP" in l and l.strip().startswith("-4")), None)
    if start is None:
        raise RuntimeError(f"no displacement block found in {path}")

    disp: Dict[int, Tuple[float, float, float]] = {}
    skipped = []
    for l in raw[start:]:
        if l.strip().startswith("-3"):
            break
        if not l.startswith(" -1"):
            continue
        got = _parse_line(l[3:].rstrip("\r\n"))
        if got is None:
            if len(skipped) < 3:
                skipped.append(l)
            continue
        disp[got[0]] = got[1]

    if not disp:
        raise RuntimeError(
            f"no displacements parsed from {path}. First unparsed line "
            f"({len(skipped[0][3:].rstrip()) if skipped else 0} chars after "
            f"' -1'): {skipped[0]!r}" if skipped else
            f"no displacements parsed from {path}")
    if skipped:
        raise RuntimeError(
            f"{path}: {len(skipped)}+ data lines could not be parsed while "
            f"others could. Partial data is worse than none. First one: "
            f"{skipped[0]!r}")
    return disp
