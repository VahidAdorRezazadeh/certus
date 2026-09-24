#!/usr/bin/env python3
"""
viewer.py - geometry and result pictures for the GUI. No physics here.

face_mesh(step)      one Gmsh session: catalogue AND a surface triangulation
                     from the SAME import, so a face tag in the picture is the
                     face tag in the catalogue. (Two imports would only agree
                     by numbering luck; see geom_session.py.)
faces_figure(...)    Plotly 3D view, chosen load faces red, fixed faces blue
result_figure(...)   the solved mesh coloured by displacement or von Mises
"""

from __future__ import annotations
from typing import Dict, List, Optional, Sequence, Tuple
import numpy as np


def face_mesh(step_path: str, size_factor: float = 0.06):
    """Returns (catalogue, {face_tag: (xyz Nx3, tri Mx3)})."""
    import gmsh
    from geom_session import GeomSession
    with GeomSession(step_path) as ses:
        cat = ses.catalogue
        span = max(np.subtract(cat.bbox_max, cat.bbox_min))
        gmsh.option.setNumber("Mesh.MeshSizeMax", size_factor * span)
        gmsh.option.setNumber("Mesh.MeshSizeMin", 0.2 * size_factor * span)
        gmsh.model.mesh.generate(2)
        out = {}
        for dim, tag in gmsh.model.getEntities(2):
            ntags, xyz, _ = gmsh.model.mesh.getNodes(2, tag, True)
            etypes, _, enodes = gmsh.model.mesh.getElements(2, tag)
            if not len(ntags):
                continue
            idx = {int(t): i for i, t in enumerate(ntags)}
            tris = []
            for et, en in zip(etypes, enodes):
                if et == 2:   # 3-node triangle
                    tris += [[idx[int(a)] for a in en[i:i + 3]]
                             for i in range(0, len(en), 3)]
            out[tag] = (np.asarray(xyz).reshape(-1, 3), np.asarray(tris))
        gmsh.model.mesh.clear()
    return cat, out


def faces_figure(tri: Dict[int, Tuple[np.ndarray, np.ndarray]],
                 load_tags: Sequence[int] = (), fix_tags: Sequence[int] = (),
                 hover: Optional[Dict[int, str]] = None, height: int = 520):
    import plotly.graph_objects as go
    traces = []
    load_tags, fix_tags = set(load_tags), set(fix_tags)
    for tag, (xyz, t) in tri.items():
        if not len(t):
            continue
        if tag in load_tags:
            color, name = "#d62728", "LOAD"
        elif tag in fix_tags:
            color, name = "#1f77b4", "FIXED"
        else:
            color, name = "#c8c8c8", "face"
        traces.append(go.Mesh3d(
            x=xyz[:, 0], y=xyz[:, 1], z=xyz[:, 2],
            i=t[:, 0], j=t[:, 1], k=t[:, 2], color=color, opacity=1.0,
            flatshading=True, name=f"{name} face {tag}",
            hovertext=(hover or {}).get(tag, f"face {tag}"),
            hoverinfo="text",
            lighting=dict(ambient=0.55, diffuse=0.7, specular=0.1)))
    fig = go.Figure(traces)
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=0, b=0),
                      scene=dict(aspectmode="data",
                                 xaxis_title="X", yaxis_title="Y",
                                 zaxis_title="Z"),
                      showlegend=False)
    return fig


def _read_inp_mesh(deck_path: str):
    """Nodes and C3D4/C3D10 (and hex) elements from a CalculiX deck,
    following *INCLUDE lines. Enough to draw the skin."""
    import os
    nodes, elems = {}, []
    mode = None

    def walk(path):
        nonlocal mode
        base = os.path.dirname(path)
        for line in open(path, errors="replace"):
            s = line.strip()
            if not s or s.startswith("**"):
                continue
            if s.startswith("*"):
                up = s.upper()
                if up.startswith("*INCLUDE"):
                    inc = s.split("=", 1)[1].strip().strip('"')
                    walk(os.path.join(base, inc))
                    mode = None
                elif up.startswith("*NODE") and not up.startswith("*NODE ") \
                        and "PRINT" not in up and "FILE" not in up:
                    mode = "n"
                elif up.startswith("*ELEMENT"):
                    t = up.split("TYPE=")[1].split(",")[0] if "TYPE=" in up \
                        else ""
                    mode = "e" if t.startswith("C3D") else None
                else:
                    mode = None
                continue
            parts = [p for p in s.replace(",", " ").split() if p]
            if mode == "n" and len(parts) >= 4:
                nodes[int(parts[0])] = tuple(float(v) for v in parts[1:4])
            elif mode == "e":
                elems.append([int(p) for p in parts[1:]])
    walk(deck_path)
    return nodes, elems


def _skin(elems) -> List[Tuple[int, int, int]]:
    """Boundary triangles of a tet mesh (corner nodes only)."""
    from collections import Counter
    faces = Counter()
    keep = {}
    for e in elems:
        if len(e) < 4:
            continue
        a, b, c, d = e[:4]
        for f in ((a, b, c), (a, b, d), (a, c, d), (b, c, d)):
            k = tuple(sorted(f))
            faces[k] += 1
            keep[k] = f
    return [keep[k] for k, n in faces.items() if n == 1]


def result_figure(deck_path: str, frd_path: str, field: str = "U",
                  scale: Optional[float] = None, height: int = 520):
    """Deformed skin coloured by |U| (mm) or von Mises (MPa)."""
    import plotly.graph_objects as go
    from frdread import read_frd_field, read_frd_stress, von_mises
    nodes, elems = _read_inp_mesh(deck_path)
    tris = _skin(elems)
    used = sorted({n for t in tris for n in t})
    idx = {n: i for i, n in enumerate(used)}
    X = np.array([nodes[n] for n in used])
    U = read_frd_field(frd_path, "DISP")
    D = np.array([U.get(n, (0, 0, 0))[:3] for n in used])
    umag = np.linalg.norm(D, axis=1)
    if field == "U":
        val, title = umag, "|U| (mm)"
    else:
        S = read_frd_stress(frd_path)
        val = np.array([von_mises(S[n]) if n in S else np.nan for n in used])
        title = "von Mises (MPa), nodal, extrapolated by CalculiX"
    span = float(np.ptp(X, axis=0).max())
    if scale is None:
        scale = 0.08 * span / umag.max() if umag.max() > 0 else 0.0
    P = X + scale * D
    T = np.array([[idx[a], idx[b], idx[c]] for a, b, c in tris])
    fig = go.Figure(go.Mesh3d(
        x=P[:, 0], y=P[:, 1], z=P[:, 2], i=T[:, 0], j=T[:, 1], k=T[:, 2],
        intensity=val, colorscale="Turbo", colorbar=dict(title=title),
        flatshading=False, hoverinfo="skip"))
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=30, b=0),
                      title=f"deformation shown x{scale:.3g}",
                      scene=dict(aspectmode="data"))
    return fig
