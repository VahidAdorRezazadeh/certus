"""Read-only mesh extraction for the viewer; never changes a solver deck.

Supported solid skins: C3D4, C3D10, C3D8 and C3D20 (including listed variants).
Quadratic midside nodes are retained in the surface tessellation. Unsupported,
incomplete and non-finite data raise errors instead of producing a plausible plot.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import csv
import numpy as np

NODE_COUNTS = {"C3D4": 4, "C3D10": 10, "C3D10M": 10, "C3D8": 8,
               "C3D8R": 8, "C3D8I": 8, "C3D20": 20, "C3D20R": 20}
# Each face is (corners, midsides in cyclic edge order).
FACES = {
    4: [((0,2,1),()), ((0,1,3),()), ((1,2,3),()), ((2,0,3),())],
    10: [((0,2,1),(6,5,4)), ((0,1,3),(4,8,7)),
         ((1,2,3),(5,9,8)), ((2,0,3),(6,7,9))],
    8: [((0,3,2,1),()), ((4,5,6,7),()), ((0,1,5,4),()),
        ((1,2,6,5),()), ((2,3,7,6),()), ((3,0,4,7),())],
    20: [((0,3,2,1),(11,10,9,8)), ((4,5,6,7),(12,13,14,15)),
         ((0,1,5,4),(8,17,12,16)), ((1,2,6,5),(9,18,13,17)),
         ((2,3,7,6),(10,19,14,18)), ((3,0,4,7),(11,16,15,19))],
}


def read_inp_mesh(deck_path):
    nodes, elements = {}, []
    element_ids = set()
    stack = set()

    def walk(path):
        path = Path(path).resolve()
        if path in stack:
            raise ValueError("Cyclic *INCLUDE in the mesh deck.")
        stack.add(path)
        mode, count, pending = None, 0, []
        try:
            with path.open(errors="replace") as stream:
                for raw in stream:
                    s = raw.strip()
                    if not s or s.startswith("**"):
                        continue
                    if s.startswith("*"):
                        if pending:
                            raise ValueError("Incomplete element connectivity before " + s)
                        fields = next(csv.reader([s], skipinitialspace=True))
                        keyword = fields[0].strip().upper()
                        options = dict((k.strip().upper(), v.strip().strip('"')) for item in fields[1:]
                                       if '=' in item for k, v in [item.split('=', 1)])
                        mode = None
                        if keyword in ("*SYSTEM", "*PART", "*INSTANCE"):
                            raise ValueError(f"Viewer requires a flat global-coordinate mesh; {keyword} is not supported.")
                        if keyword == "*INCLUDE":
                            if not options.get("INPUT"):
                                raise ValueError("*INCLUDE has no INPUT path.")
                            walk(path.parent / options["INPUT"])
                        elif keyword == "*NODE":
                            mode = "node"
                        elif keyword == "*ELEMENT":
                            kind = options.get("TYPE", "").upper()
                            if kind not in NODE_COUNTS:
                                raise ValueError(f"Viewer does not support element type {kind or '(unspecified)'}. Supported: C3D4/10/8/20 solids.")
                            mode, count = "element", NODE_COUNTS[kind]
                        continue
                    parts = s.replace(',', ' ').split()
                    if mode == "node":
                        if len(parts) != 4:
                            raise ValueError("Viewer expects Cartesian nodes with id, x, y, z.")
                        nid = int(parts[0])
                        if nid in nodes:
                            raise ValueError(f"Duplicate node {nid} in mesh deck.")
                        nodes[nid] = tuple(float(v.replace('D', 'E').replace('d', 'e')) for v in parts[1:])
                    elif mode == "element":
                        pending.extend(int(p) for p in parts)
                        if len(pending) > count + 1:
                            raise ValueError("Element connectivity has too many nodes.")
                        if len(pending) == count + 1:
                            eid, *conn = pending
                            if eid in element_ids:
                                raise ValueError(f"Duplicate element {eid} in mesh deck.")
                            element_ids.add(eid)
                            elements.append(conn)
                            pending = []
            if pending:
                raise ValueError("Incomplete element connectivity at end of deck.")
        finally:
            stack.remove(path)
    walk(deck_path)
    if not nodes or not elements:
        raise ValueError("No supported solid mesh was found in the deck.")
    missing = {n for e in elements for n in e} - nodes.keys()
    if missing:
        raise ValueError(f"Element connectivity refers to missing node {min(missing)}.")
    if not np.isfinite(np.asarray(list(nodes.values()))).all():
        raise ValueError("Mesh coordinates contain non-finite values.")
    return nodes, elements


def boundary_faces(elements):
    counts, faces = Counter(), {}
    for element in elements:
        if len(element) not in FACES:
            raise ValueError(f"Unsupported {len(element)}-node element in viewer.")
        for corners, mids in FACES[len(element)]:
            c = tuple(element[i] for i in corners)
            m = tuple(element[i] for i in mids)
            key = tuple(sorted(c))
            counts[key] += 1
            faces[key] = c, m
    if any(n > 2 for n in counts.values()):
        raise ValueError("Non-manifold solid mesh: more than two elements share a face.")
    return [faces[k] for k, n in counts.items() if n == 1]


def skin(elements):
    triangles = []
    for c, m in boundary_faces(elements):
        if not m:
            triangles.extend((c[0], c[i], c[i+1]) for i in range(1, len(c)-1))
        elif len(c) == 3:
            a,b,d = c; ab,bd,da = m
            triangles.extend(((a,ab,da),(ab,b,bd),(da,bd,d),(ab,bd,da)))
        else:
            a,b,c_,d = c; ab,bc,cd,da = m
            triangles.extend(((a,ab,da),(ab,b,bc),(bc,c_,cd),(cd,d,da),
                              (ab,bc,cd),(ab,cd,da)))
    return triangles


def skin_edges(elements):
    edges = set()
    for c, m in boundary_faces(elements):
        ring = [n for pair in zip(c, m) for n in pair] if m else list(c)
        for a,b in zip(ring, ring[1:] + ring[:1]):
            edges.add(tuple(sorted((a,b))))
    return sorted(edges)


def result_data(deck_path, frd_path, field="U", scale=None):
    from certus.frdread import read_frd_field, read_frd_stress, von_mises
    if field not in ("U", "UX", "UY", "UZ", "S"):
        raise ValueError(f"Unsupported result field {field}.")
    nodes, elements = read_inp_mesh(deck_path)
    triangles = skin(elements)
    used = sorted({n for tri in triangles for n in tri})
    if not used:
        raise ValueError("The mesh has no external surface to display.")
    idx = {n: i for i,n in enumerate(used)}
    X = np.asarray([nodes[n] for n in used], dtype=float)
    displacement = read_frd_field(frd_path, "DISP")
    missing = set(used) - displacement.keys()
    if missing:
        raise ValueError(f"Displacement is missing for {len(missing)} surface node(s); no zero values have been substituted.")
    U = np.asarray([displacement[n][:3] for n in used], dtype=float)
    if not np.isfinite(U).all():
        raise ValueError("Displacement contains non-finite values.")
    magnitude = np.linalg.norm(U, axis=1)
    if field == "S":
        stress = read_frd_stress(frd_path)
        missing = set(used) - stress.keys()
        if missing:
            raise ValueError(f"Stress is missing for {len(missing)} surface node(s).")
        values = np.asarray([von_mises(stress[n]) for n in used])
        title, units = "von Mises", "MPa"
    elif field == "U":
        values, title, units = magnitude, "Displacement |U|", "mm"
    else:
        values = U[:, {"UX":0, "UY":1, "UZ":2}[field]]
        title, units = f"Displacement {field}", "mm"
    if not np.isfinite(values).all():
        raise ValueError("The selected field contains non-finite values.")
    if scale is None:
        maximum = float(magnitude.max())
        scale = .08 * float(np.ptp(X, axis=0).max()) / maximum if maximum else 0.0
    if not np.isfinite(scale) or scale < 0:
        raise ValueError("Deformation factor must be finite and non-negative.")
    return dict(nodes=used, X=X, U=U, P=X+scale*U, values=values, units=units,
                title=title, scale=float(scale), triangles=np.asarray([[idx[n] for n in t] for t in triangles]),
                edges=np.asarray([[idx[a],idx[b]] for a,b in skin_edges(elements)]))
