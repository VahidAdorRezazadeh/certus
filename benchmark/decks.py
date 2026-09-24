"""Structured decks of the one fixed part: a steel cantilever L=100, b=10,
h=5 mm (x length, y width, z depth). Built without a mesher so every case is
bit-for-bit reproducible across platforms, which the Gmsh path is not."""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Sequence, Tuple

L, B, H = 100.0, 10.0, 5.0
E, NU = 210000.0, 0.3
F_TIP = 50.0 * (B * H ** 3 / 12.0) / (L * H / 2.0)     # root stress 50 MPa


def reference_tip(F=F_TIP, E_=E, nu=NU, plane=None):
    """Euler-Bernoulli plus Timoshenko shear, tip load."""
    Ee = E_ / (1 - nu ** 2) if plane == "strain" else E_
    I = B * H ** 3 / 12.0
    G = E_ / (2 * (1 + nu))
    return F * L ** 3 / (3 * Ee * I) + F * L / ((5 / 6) * G * B * H)


class Grid:
    def __init__(self, nx, ny, nz, y0=0.0, width=B):
        self.nx, self.ny, self.nz = nx, ny, nz
        self.y0, self.w = y0, width

    def nid(self, i, j, k):
        return 1 + i + (self.nx + 1) * (j + (self.ny + 1) * k)

    def xyz(self, i, j, k):
        return (L * i / self.nx, self.y0 + self.w * j / self.ny,
                H * k / self.nz)

    def nodes(self):
        return [(self.nid(i, j, k), self.xyz(i, j, k))
                for k in range(self.nz + 1) for j in range(self.ny + 1)
                for i in range(self.nx + 1)]

    def where(self, pred):
        return [n for n, p in self.nodes() if pred(*p)]


def _hexes(g: Grid):
    for k in range(g.nz):
        for j in range(g.ny):
            for i in range(g.nx):
                yield [g.nid(i, j, k), g.nid(i + 1, j, k),
                       g.nid(i + 1, j + 1, k), g.nid(i, j + 1, k),
                       g.nid(i, j, k + 1), g.nid(i + 1, j, k + 1),
                       g.nid(i + 1, j + 1, k + 1), g.nid(i, j + 1, k + 1)]


# Kuhn split of a hex into 6 tets along the 0-6 diagonal: conforming across
# faces for a structured grid.
_KUHN = [(0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6), (0, 7, 4, 6),
         (0, 4, 5, 6), (0, 5, 1, 6)]


def _tet_ok(pts):
    a, b, c, d = pts
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    w = [d[k] - a[k] for k in range(3)]
    return (u[0] * (v[1] * w[2] - v[2] * w[1]) - u[1] * (v[0] * w[2] -
            v[2] * w[0]) + u[2] * (v[0] * w[1] - v[1] * w[0])) > 0


def solid_deck(path, etype="C3D8I", n=(20, 2, 2), nu=NU, E_=E,
               bc: Sequence[str] = ("ROOT, 1, 3",),
               loads: Optional[List[str]] = None, mat_extra="",
               step="*STEP", proc="*STATIC", y0=0.0, width=B,
               extra_sets: Optional[Dict[str, List[int]]] = None,
               F=F_TIP, fz=True):
    if etype.startswith("C3D20"):
        return _solid20(path, etype, n, nu, E_, bc, loads, mat_extra, step,
                        proc, y0, width, extra_sets, F)
    g = Grid(*n, y0=y0, width=width)
    xyz = dict(g.nodes())
    L_ = ["*NODE"] + [f"{i}, {p[0]:.9g}, {p[1]:.9g}, {p[2]:.9g}"
                      for i, p in g.nodes()]
    if etype == "C3D4":
        L_.append("*ELEMENT, TYPE=C3D4, ELSET=EALL")
        e = 1
        for hx in _hexes(g):
            for t in _KUHN:
                conn = [hx[i] for i in t]
                if not _tet_ok([xyz[c] for c in conn]):
                    conn[1], conn[2] = conn[2], conn[1]
                L_.append(f"{e}, " + ", ".join(map(str, conn)))
                e += 1
    else:
        L_.append(f"*ELEMENT, TYPE={etype}, ELSET=EALL")
        for e, hx in enumerate(_hexes(g), 1):
            L_.append(f"{e}, " + ", ".join(map(str, hx)))
    sets = {"ROOT": g.where(lambda x, y, z: x < 1e-9),
            "TIP": g.where(lambda x, y, z: x > L - 1e-9)}
    sets.update(extra_sets(g) if callable(extra_sets) else (extra_sets or {}))
    for name, ids in sets.items():
        L_ += [f"*NSET, NSET={name}"] + [f"{t}," for t in ids]
    if loads is None:
        tip = sets["TIP"]
        loads = [f"{t}, 3, {-F / len(tip):.10g}" for t in tip]
    L_ += ["*MATERIAL, NAME=STEEL", "*ELASTIC", f"{E_:g}, {nu:g}"]
    if mat_extra:
        L_.append(mat_extra)
    L_ += ["*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL", step, proc,
           "*BOUNDARY"] + list(bc) + ["*CLOAD"] + loads + \
        ["*NODE FILE", "U, RF", "*EL FILE", "S", "*END STEP"]
    open(path, "w").write("\n".join(L_) + "\n")
    return g, sets


def _solid20(path, etype, n, nu, E_, bc, loads, mat_extra, step, proc, y0,
             width, extra_sets, F):
    """20-node hexes on the half-step lattice; corner nodes form a Grid of
    (2nx, 2ny, 2nz) so node sets are selected the same way."""
    nx, ny, nz = n
    g = Grid(2 * nx, 2 * ny, 2 * nz, y0=y0, width=width)
    used = set()
    E = []
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                a, b, c = 2 * i, 2 * j, 2 * k
                N = lambda di, dj, dk: g.nid(a + di, b + dj, c + dk)
                conn = [N(0, 0, 0), N(2, 0, 0), N(2, 2, 0), N(0, 2, 0),
                        N(0, 0, 2), N(2, 0, 2), N(2, 2, 2), N(0, 2, 2),
                        N(1, 0, 0), N(2, 1, 0), N(1, 2, 0), N(0, 1, 0),
                        N(1, 0, 2), N(2, 1, 2), N(1, 2, 2), N(0, 1, 2),
                        N(0, 0, 1), N(2, 0, 1), N(2, 2, 1), N(0, 2, 1)]
                used.update(conn)
                E.append(conn)
    nodes = [(i, p) for i, p in g.nodes() if i in used]
    L_ = ["*NODE"] + [f"{i}, {p[0]:.9g}, {p[1]:.9g}, {p[2]:.9g}"
                      for i, p in nodes]
    L_.append(f"*ELEMENT, TYPE={etype}, ELSET=EALL")
    for e, conn in enumerate(E, 1):
        L_.append(f"{e}, " + ", ".join(map(str, conn[:15])) + ",")
        L_.append(", ".join(map(str, conn[15:])))
    pick = lambda pred: [i for i, p in nodes if pred(*p)]
    sets = {"ROOT": pick(lambda x, y, z: x < 1e-9),
            "TIP": pick(lambda x, y, z: x > L - 1e-9)}
    if callable(extra_sets):
        sets.update({k: [i for i in v if i in used]
                     for k, v in extra_sets(g).items()})
    for name, ids in sets.items():
        L_ += [f"*NSET, NSET={name}"] + [f"{t}," for t in ids]
    if loads is None:
        # consistent tip load for a quadratic face is not uniform per node;
        # the resultant is what matters here, applied equally on the face
        tip = sets["TIP"]
        loads = [f"{t}, 3, {-F / len(tip):.10g}" for t in tip]
    L_ += ["*MATERIAL, NAME=STEEL", "*ELASTIC", f"{E_:g}, {nu:g}"]
    if mat_extra:
        L_.append(mat_extra)
    L_ += ["*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL", step, proc,
           "*BOUNDARY"] + list(bc) + ["*CLOAD"] + loads + \
        ["*NODE FILE", "U, RF", "*EL FILE", "S", "*END STEP"]
    open(path, "w").write("\n".join(L_) + "\n")
    return g, sets


def plane_deck(path, etype="CPS8", nx=40, ny=4, thickness: Optional[float] = B,
               F=F_TIP, E_=E, nu=NU):
    """Quadratic 8-node quads in the x-y plane: length x, depth y."""
    def nid(i, j):                    # on the (2nx+1) x (2ny+1) lattice
        return 1 + i + (2 * nx + 1) * j
    pts = {}
    for j in range(2 * ny + 1):
        for i in range(2 * nx + 1):
            if i % 2 and j % 2:
                continue              # serendipity: no centre node
            pts[nid(i, j)] = (L * i / (2 * nx), H * j / (2 * ny), 0.0)
    L_ = ["*NODE"] + [f"{n}, {p[0]:.9g}, {p[1]:.9g}, 0" for n, p in
                      pts.items()]
    L_.append(f"*ELEMENT, TYPE={etype}, ELSET=EALL")
    e = 1
    for j in range(ny):
        for i in range(nx):
            a, b = 2 * i, 2 * j
            c = [nid(a, b), nid(a + 2, b), nid(a + 2, b + 2), nid(a, b + 2),
                 nid(a + 1, b), nid(a + 2, b + 1), nid(a + 1, b + 2),
                 nid(a, b + 1)]
            L_.append(f"{e}, " + ", ".join(map(str, c)))
            e += 1
    root = [n for n, p in pts.items() if p[0] < 1e-9]
    tip = [n for n, p in pts.items() if p[0] > L - 1e-9]
    L_ += ["*NSET, NSET=ROOT"] + [f"{t}," for t in root]
    L_ += ["*NSET, NSET=TIP"] + [f"{t}," for t in tip]
    L_ += ["*MATERIAL, NAME=STEEL", "*ELASTIC", f"{E_:g}, {nu:g}",
           "*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL",
           f"{thickness:g}" if thickness else ",", "*STEP", "*STATIC",
           "*BOUNDARY", "ROOT, 1, 2", "*CLOAD"] + \
        [f"{t}, 2, {-F / len(tip):.10g}" for t in tip] + \
        ["*NODE FILE", "U, RF", "*EL FILE", "S", "*END STEP"]
    open(path, "w").write("\n".join(L_) + "\n")
    return root, tip
