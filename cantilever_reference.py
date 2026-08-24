"""
Step 0 reference case for Certus Milestone A.

Purpose: establish, independently of any Certus code, that
  (a) CalculiX runs and the deck syntax is correct,
  (b) the unit system is consistent,
  (c) beam theory and the FE model agree, and by how much,
  (d) the tolerance band for the Milestone A comparison is MEASURED, not assumed.

No Gmsh, no build123d, no LLM. Pure Python mesh generation so that the only
thing under test is CalculiX plus the hand calculation.

Geometry: prismatic cantilever, clamped at x = 0.
  x : along the length, 0 .. L
  y : bending direction, 0 .. h   (load acts in -y)
  z : width,             0 .. b

Load: uniform pressure p on the top face (y = h), acting in -y.
      Line load q = p * b  [N/mm]

Units: N, mm, MPa, so E in MPa and stresses come out in MPa.
"""

import os
import re
import subprocess
import sys

# ----------------------------------------------------------------------------
# Problem definition
# ----------------------------------------------------------------------------

L = 200.0        # mm, length
H = 20.0         # mm, height (bending direction, y)
B = 20.0         # mm, width  (z)

E = 210000.0     # MPa
NU = 0.3         # -
P_TOP = 0.5      # MPa, uniform pressure on the top face

CCX = os.environ.get("CCX", "ccx")


# ----------------------------------------------------------------------------
# Analytical reference (Euler-Bernoulli plus Timoshenko shear)
# ----------------------------------------------------------------------------

def beam_theory():
    I = B * H ** 3 / 12.0
    A = B * H
    G = E / (2.0 * (1.0 + NU))
    k = 5.0 / 6.0                      # rectangular section shear coefficient
    q = P_TOP * B                      # N/mm

    d_bending = q * L ** 4 / (8.0 * E * I)
    d_shear = q * L ** 2 / (2.0 * k * G * A)

    M_root = q * L ** 2 / 2.0
    sigma_root = M_root * (H / 2.0) / I

    return {
        "I": I,
        "A": A,
        "G": G,
        "q": q,
        "total_force": q * L,
        "d_bending": d_bending,
        "d_shear": d_shear,
        "d_total": d_bending + d_shear,
        "M_root": M_root,
        "sigma_root": sigma_root,
    }


# ----------------------------------------------------------------------------
# Structured hex mesh
# ----------------------------------------------------------------------------
# Nodes live on a half-index grid (2*nx+1, 2*ny+1, 2*nz+1).
# Linear elements use only even half-indices.
# Quadratic (serendipity, 20-node) elements use every half-index triple with at
# most one odd component: that is exactly the 20-node pattern, with no face
# centres and no body centre.

# Abaqus / CalculiX C3D20 local node order, expressed as (di, dj, dk) offsets
# in half-index units within the element.
C3D20_OFFSETS = [
    (0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0),      # 1-4   bottom corners
    (0, 0, 2), (2, 0, 2), (2, 2, 2), (0, 2, 2),      # 5-8   top corners
    (1, 0, 0), (2, 1, 0), (1, 2, 0), (0, 1, 0),      # 9-12  bottom midsides
    (1, 0, 2), (2, 1, 2), (1, 2, 2), (0, 1, 2),      # 13-16 top midsides
    (0, 0, 1), (2, 0, 1), (2, 2, 1), (0, 2, 1),      # 17-20 vertical midsides
]

C3D8_OFFSETS = C3D20_OFFSETS[:8]


def build_mesh(nx, ny, nz, quadratic):
    """Return (nodes, elements) where nodes maps id -> (x, y, z) and elements is
    a list of node-id tuples in CalculiX local order."""
    NX, NY, NZ = 2 * nx, 2 * ny, 2 * nz

    def keep(i, j, k):
        if not quadratic:
            return i % 2 == 0 and j % 2 == 0 and k % 2 == 0
        return (i % 2) + (j % 2) + (k % 2) <= 1

    ids = {}
    nodes = {}
    nid = 0
    for i in range(NX + 1):
        for j in range(NY + 1):
            for k in range(NZ + 1):
                if not keep(i, j, k):
                    continue
                nid += 1
                ids[(i, j, k)] = nid
                nodes[nid] = (L * i / NX, H * j / NY, B * k / NZ)

    offsets = C3D20_OFFSETS if quadratic else C3D8_OFFSETS
    elements = []
    for a in range(nx):
        for bb in range(ny):
            for c in range(nz):
                base = (2 * a, 2 * bb, 2 * c)
                conn = tuple(
                    ids[(base[0] + d[0], base[1] + d[1], base[2] + d[2])]
                    for d in offsets
                )
                elements.append(conn)

    return nodes, elements, ids, (NX, NY, NZ)


# ----------------------------------------------------------------------------
# Deck writing
# ----------------------------------------------------------------------------
# Hex face numbering (Abaqus / CalculiX):
#   F1 = 1-2-3-4, F2 = 5-6-7-8, F3 = 1-2-6-5,
#   F4 = 2-3-7-6, F5 = 3-4-8-7, F6 = 4-1-5-8
# With the offsets above, the top face (j = max) is 3-4-8-7 = F5.
TOP_FACE = "P5"


def write_deck(path, eltype, nodes, elements, ids, grid, quadratic):
    NX, NY, NZ = grid
    tol = 1e-9

    nfix = sorted(n for n, (x, y, z) in nodes.items() if abs(x) < tol)
    # probe: tip section, mid-height, mid-width
    probe_key = (NX, NY // 2, NZ // 2)
    if probe_key not in ids:                      # odd-count safety
        probe_key = (NX, NY // 2 if NY // 2 % 2 == 0 else NY // 2 + 1,
                     NZ // 2 if NZ // 2 % 2 == 0 else NZ // 2 + 1)
    nprobe = ids[probe_key]

    # elements whose top face lies on y = H, i.e. the last layer in j
    top_elems = []
    for idx, conn in enumerate(elements, start=1):
        ys = [nodes[n][1] for n in conn[:8]]
        if abs(max(ys) - H) < tol:
            top_elems.append(idx)

    # root element set for stress printing
    root_elems = []
    for idx, conn in enumerate(elements, start=1):
        xs = [nodes[n][0] for n in conn[:8]]
        if abs(min(xs)) < tol:
            root_elems.append(idx)

    def chunk(seq, n=8):
        seq = list(seq)
        return [seq[i:i + n] for i in range(0, len(seq), n)]

    lines = []
    lines.append("*HEADING")
    lines.append(f"Certus Milestone A reference cantilever, {eltype}")
    lines.append("*NODE, NSET=NALL")
    for n in sorted(nodes):
        x, y, z = nodes[n]
        lines.append(f"{n}, {x:.6f}, {y:.6f}, {z:.6f}")

    lines.append(f"*ELEMENT, TYPE={eltype}, ELSET=EALL")
    for idx, conn in enumerate(elements, start=1):
        # CalculiX accepts continuation lines; keep <= 15 entries per line
        first = conn[:15]
        rest = conn[15:]
        s = f"{idx}, " + ", ".join(str(c) for c in first)
        if rest:
            s += ",\n" + ", ".join(str(c) for c in rest)
        lines.append(s)

    lines.append("*NSET, NSET=NFIX")
    for c in chunk(nfix):
        lines.append(", ".join(str(v) for v in c))

    lines.append("*NSET, NSET=NPROBE")
    lines.append(str(nprobe))

    lines.append("*ELSET, ELSET=EROOT")
    for c in chunk(root_elems):
        lines.append(", ".join(str(v) for v in c))

    lines.append("*MATERIAL, NAME=STEEL")
    lines.append("*ELASTIC")
    lines.append(f"{E:.1f}, {NU}")
    lines.append("*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL")

    lines.append("*STEP")
    lines.append("*STATIC")
    lines.append("*BOUNDARY")
    lines.append("NFIX, 1, 3, 0.0")
    lines.append("*DLOAD")
    for idx in top_elems:
        lines.append(f"{idx}, {TOP_FACE}, {P_TOP}")
    lines.append("*NODE PRINT, NSET=NPROBE")
    lines.append("U")
    lines.append("*NODE PRINT, NSET=NFIX, TOTALS=ONLY")
    lines.append("RF")
    lines.append("*EL PRINT, ELSET=EROOT")
    lines.append("S")
    lines.append("*NODE FILE")
    lines.append("U")
    lines.append("*EL FILE")
    lines.append("S")
    lines.append("*END STEP")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")

    return {"n_nodes": len(nodes), "n_elems": len(elements),
            "n_fix": len(nfix), "n_top": len(top_elems),
            "probe": nprobe}


# ----------------------------------------------------------------------------
# Run and parse
# ----------------------------------------------------------------------------

def run_ccx(jobname, workdir):
    res = subprocess.run([CCX, jobname], cwd=workdir,
                         capture_output=True, text=True, timeout=1800)
    return res


def parse_dat(path):
    """Extract probe displacement, total reaction force, and root stress range.

    CalculiX writes a header line, a blank line, then data lines. Splitting on
    blank lines separates the header from its own data, which is what broke the
    first version of this function. Parse line by line instead.
    """
    with open(path) as f:
        lines = f.read().splitlines()

    out = {"u": None, "rf": None, "sxx_min": None, "sxx_max": None,
           "sxx_all": []}
    mode = None
    for ln in lines:
        low = ln.lower()
        if "for set" in low and "time" in low:
            if "displacements" in low:
                mode = "u"
            elif "total force" in low:
                mode = "rf"
            elif "stresses" in low:
                mode = "s"
            else:
                mode = None
            continue
        if not ln.strip():
            continue
        parts = ln.split()
        try:
            vals = [float(v) for v in parts]
        except ValueError:
            mode = None
            continue
        if mode == "u" and len(vals) >= 4:
            out["u"] = tuple(vals[1:4])
        elif mode == "rf" and len(vals) >= 3:
            out["rf"] = tuple(vals[-3:])
        elif mode == "s" and len(vals) >= 8:
            out["sxx_all"].append(vals[2])
    if out["sxx_all"]:
        out["sxx_min"] = min(out["sxx_all"])
        out["sxx_max"] = max(out["sxx_all"])
    return out


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

CASES = [
    # (label, eltype, quadratic, (nx, ny, nz))
    ("C3D8  coarse",  "C3D8",   False, (20, 2, 2)),
    ("C3D8  fine",    "C3D8",   False, (40, 4, 4)),
    ("C3D8I coarse",  "C3D8I",  False, (20, 2, 2)),
    ("C3D8I fine",    "C3D8I",  False, (40, 4, 4)),
    ("C3D8R fine",    "C3D8R",  False, (40, 4, 4)),
    ("C3D20R coarse", "C3D20R", True,  (10, 2, 2)),
    ("C3D20R fine",   "C3D20R", True,  (20, 4, 4)),
]


def main():
    workdir = os.path.abspath(os.path.dirname(__file__) or ".")
    ref = beam_theory()

    print("=" * 78)
    print("ANALYTICAL REFERENCE  (Euler-Bernoulli + Timoshenko shear)")
    print("=" * 78)
    print(f"  geometry            L={L} H={H} B={B} mm,  L/H = {L/H:.1f}")
    print(f"  E={E} MPa, nu={NU}, p_top={P_TOP} MPa")
    print(f"  I                   {ref['I']:.4f} mm^4")
    print(f"  q (line load)       {ref['q']:.4f} N/mm")
    print(f"  total applied force {ref['total_force']:.4f} N")
    print(f"  tip defl, bending   {ref['d_bending']:.6f} mm")
    print(f"  tip defl, shear     {ref['d_shear']:.6f} mm  "
          f"({100*ref['d_shear']/ref['d_total']:.2f}% of total)")
    print(f"  tip defl, TOTAL     {ref['d_total']:.6f} mm")
    print(f"  root moment         {ref['M_root']:.1f} N.mm")
    print(f"  root bending stress {ref['sigma_root']:.4f} MPa")
    print()

    results = []
    for label, eltype, quad, (nx, ny, nz) in CASES:
        job = label.replace(" ", "_").lower()
        nodes, elements, ids, grid = build_mesh(nx, ny, nz, quad)
        meta = write_deck(os.path.join(workdir, job + ".inp"),
                          eltype, nodes, elements, ids, grid, quad)
        res = run_ccx(job, workdir)
        datp = os.path.join(workdir, job + ".dat")
        parsed = parse_dat(datp) if os.path.exists(datp) else {}
        ok = os.path.exists(os.path.join(workdir, job + ".frd"))
        results.append((label, eltype, (nx, ny, nz), meta, parsed, ok,
                        res.returncode, res.stdout[-1500:], res.stderr[-800:]))

    print("=" * 78)
    print("CALCULIX RESULTS")
    print("=" * 78)
    hdr = (f"{'case':16s} {'nel':>7s} {'ndof':>8s} "
           f"{'uy_tip [mm]':>13s} {'vs theory':>11s} "
           f"{'sum RFy [N]':>12s} {'sxx root [MPa]':>15s}")
    print(hdr)
    print("-" * len(hdr))
    for (label, eltype, dims, meta, parsed, ok, rc, so, se) in results:
        if not ok or parsed.get("u") is None:
            print(f"{label:16s} {meta['n_elems']:7d} "
                  f"{'':>8s}  FAILED rc={rc}")
            print("   stdout tail:", so.replace("\n", " | ")[-400:])
            print("   stderr tail:", se.replace("\n", " | ")[-300:])
            continue
        uy = parsed["u"][1]
        ratio = abs(uy) / ref["d_total"]
        rf = parsed.get("rf")
        rfy = rf[1] if rf else float("nan")
        smin = parsed.get("sxx_min")
        smax = parsed.get("sxx_max")
        srange = f"{smin:.1f}/{smax:.1f}" if smin is not None else "n/a"
        ndof = meta["n_nodes"] * 3
        print(f"{label:16s} {meta['n_elems']:7d} {ndof:8d} "
              f"{uy:13.6f} {100*ratio:10.2f}% "
              f"{rfy:12.3f} {srange:>15s}")

    print()
    print("Equilibrium check: sum of reaction forces in y must equal "
          f"{ref['total_force']:.3f} N")
    print("Theory tip deflection (magnitude): "
          f"{ref['d_total']:.6f} mm; 'vs theory' is |uy_FE| / theory.")


if __name__ == "__main__":
    sys.exit(main())
