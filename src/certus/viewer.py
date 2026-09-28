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
    from certus.geom_session import GeomSession
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


# Stable presentation constants. The legend uses the same colours as the selection UI.
LOAD_COLOR, SUPPORT_COLOR = "#df623e", "#327ca8"
VIEW_BG, INK = "#122C3F", "#DCE7ED"


def _layout(fig, height, view, projection, show_axes=True):
    cameras = {
        "Isometric": dict(eye=dict(x=1.45,y=-1.65,z=1.2), up=dict(x=0,y=0,z=1)),
        "Front · XZ": dict(eye=dict(x=0,y=-2.1,z=0), up=dict(x=0,y=0,z=1)),
        "Top · XY": dict(eye=dict(x=0,y=0,z=2.1), up=dict(x=0,y=1,z=0)),
        "Right · YZ": dict(eye=dict(x=2.1,y=0,z=0), up=dict(x=0,y=0,z=1)),
    }
    camera = dict(cameras.get(view, cameras["Isometric"]), projection=dict(type=projection))
    def axis(label):
        return dict(title=dict(text=f"{label} · mm", font=dict(size=10)), visible=show_axes,
                    showbackground=False, gridcolor="#294554", zerolinecolor="#426170",
                    tickfont=dict(size=9,color="#9AB1BE"), showspikes=False)
    fig.update_layout(
        template="none", height=height, margin=dict(l=10,r=20,t=36,b=12),
        paper_bgcolor=VIEW_BG, plot_bgcolor=VIEW_BG,
        font=dict(family="IBM Plex Sans, sans-serif",color=INK,size=12),
        scene=dict(bgcolor=VIEW_BG,aspectmode="data",camera=camera,dragmode="orbit",
                   xaxis=axis("X"), yaxis=axis("Y"), zaxis=axis("Z")),
        # Preserve a user's orbit when changing fields, but apply a requested preset.
        uirevision=f"certus-{view}-{projection}",showlegend=False,
        modebar=dict(bgcolor="#183A4F",color="#9AB1BE",activecolor="#72D0C8"),
        hoverlabel=dict(bgcolor="#F4F7F8",font=dict(color="#102B3F",size=12)),
    )
    return fig


def _lines(points, edges, color, *, width=1, opacity=1, name="Mesh edges"):
    import plotly.graph_objects as go
    xyz = np.full((len(edges)*3,3), np.nan)
    if len(edges):
        xyz[0::3] = points[np.asarray(edges)[:,0]]
        xyz[1::3] = points[np.asarray(edges)[:,1]]
    return go.Scatter3d(x=xyz[:,0],y=xyz[:,1],z=xyz[:,2],mode="lines",
                        line=dict(color=color,width=width),opacity=opacity,
                        hoverinfo="skip",showlegend=False,name=name)


def faces_figure(tri: Dict[int, Tuple[np.ndarray,np.ndarray]],
                 load_tags: Sequence[int]=(), fix_tags: Sequence[int]=(),
                 hover: Optional[Dict[int,str]]=None, height: int=560, *,
                 view="Isometric", projection="orthographic", show_edges=False,
                 show_axes=True, show_labels=False, opacity=1.0, load_vector=None):
    import plotly.graph_objects as go
    from html import escape
    fig = go.Figure()
    load_tags,fix_tags = set(load_tags),set(fix_tags)
    centers = []
    nonempty = [xyz for xyz,t in tri.values() if len(xyz) and len(t)]
    if not nonempty:
        raise ValueError("No surface triangles are available to display.")
    span = float(np.ptp(np.concatenate(nonempty),axis=0).max())
    for tag,(xyz,t) in tri.items():
        if not len(t):
            continue
        selected = tag in load_tags or tag in fix_tags
        role,color = ("Load + support", "#A879C5") if tag in load_tags & fix_tags else (
            ("Load",LOAD_COLOR) if tag in load_tags else
            ("Support",SUPPORT_COLOR) if tag in fix_tags else ("Geometry","#B9CBD3"))
        text = escape((hover or {}).get(tag,f"Face {tag}"))
        fig.add_trace(go.Mesh3d(
            x=xyz[:,0],y=xyz[:,1],z=xyz[:,2],i=t[:,0],j=t[:,1],k=t[:,2],
            color=color, opacity=1.0 if selected else opacity, flatshading=False,
            name=f"{role} · face {tag}",
            hovertemplate=f"<b>{role} · face {tag}</b><br>{text}<br>"+
                          "X %{x:.4g} mm<br>Y %{y:.4g} mm<br>Z %{z:.4g} mm<extra></extra>",
            lighting=dict(ambient=.48,diffuse=.85,specular=.22,roughness=.6),
            lightposition=dict(x=1000,y=-1500,z=1800)))
        # The boundary of each CAD face stays visible; optional triangulation is separate.
        from collections import Counter
        counts = Counter(tuple(sorted((int(a),int(b)))) for tr in t
                         for a,b in ((tr[0],tr[1]),(tr[1],tr[2]),(tr[2],tr[0])))
        edges = list(counts) if show_edges else [e for e,n in counts.items() if n == 1]
        fig.add_trace(_lines(xyz,edges,"#102B3F",width=1.4 if selected else .8,opacity=.65))
        center = xyz.mean(axis=0)
        if show_labels:
            centers.append((center,f"{role} {tag}"))
        if tag in load_tags and load_vector is not None:
            vec = np.asarray(load_vector,dtype=float)
            norm = np.linalg.norm(vec)
            if np.isfinite(vec).all() and norm > 0:
                vec = vec/norm * span*.2
                start = center-vec
                fig.add_trace(_lines(np.array([start,center]),[(0,1)],LOAD_COLOR,width=5,name="Force direction"))
                fig.add_trace(go.Cone(x=[center[0]],y=[center[1]],z=[center[2]],
                    u=[vec[0]],v=[vec[1]],w=[vec[2]],anchor="tip",sizemode="absolute",
                    sizeref=span*.07,colorscale=[[0,LOAD_COLOR],[1,LOAD_COLOR]],showscale=False,
                    hovertemplate="Force direction (schematic)<extra></extra>"))
    if centers:
        points=np.asarray([p for p,_ in centers])
        fig.add_trace(go.Scatter3d(x=points[:,0],y=points[:,1],z=points[:,2],
                    mode="markers+text",text=[s for _,s in centers],textposition="top center",
                    marker=dict(size=3,color=INK),textfont=dict(size=11,color=INK),hoverinfo="skip"))
    return _layout(fig,height,view,projection,show_axes)


# Preserve the earlier helper names for callers that use them.
from certus.visual_mesh import read_inp_mesh as _read_inp_mesh, skin as _skin


def result_figure(deck_path: str, frd_path: str, field: str="U",
                  scale: Optional[float]=None, height: int=600, *,
                  view="Isometric", projection="orthographic", show_edges=False,
                  show_axes=True, colorscale="Viridis", show_reference=True, show_extrema=False):
    import plotly.graph_objects as go
    from certus.visual_mesh import result_data
    d = result_data(deck_path,frd_path,field,scale)
    P,T,val = d["P"],d["triangles"],d["values"]
    custom=np.column_stack([d["nodes"],d["X"],d["U"],val])
    cmin,cmax = float(val.min()),float(val.max())
    # Signed components use a symmetric diverging scale around zero.
    if field in ("UX","UY","UZ"):
        limit=max(abs(cmin),abs(cmax)) or 1e-12
        cmin,cmax,colorscale = -limit,limit,"RdBu_r"
    elif cmin == cmax:
        cmax=cmin+max(abs(cmin)*.01,1e-12)
    fig=go.Figure(go.Mesh3d(
        x=P[:,0],y=P[:,1],z=P[:,2],i=T[:,0],j=T[:,1],k=T[:,2],intensity=val,
        intensitymode="vertex", colorscale=colorscale,cmin=cmin,cmax=cmax,
        colorbar=dict(title=dict(text=f"{d['title']}<br>{d['units']}",side="top"),
                      thickness=15,len=.7,x=1.01,tickformat=".3g",outlinewidth=0,
                      tickfont=dict(size=10),bgcolor=VIEW_BG),
        customdata=custom,flatshading=False,
        lighting=dict(ambient=.85,diffuse=.35,specular=.05,roughness=.9),
        hovertemplate="<b>Node %{customdata[0]:.0f}</b><br>"+
                      f"{d['title']}: %{{customdata[7]:.5g}} {d['units']}<br>"+
                      "X %{customdata[1]:.4g}, Y %{customdata[2]:.4g}, Z %{customdata[3]:.4g} mm<br>"+
                      "Ux %{customdata[4]:.4g}, Uy %{customdata[5]:.4g}, Uz %{customdata[6]:.4g} mm<extra></extra>"))
    if show_edges:
        fig.add_trace(_lines(P,d["edges"],"#102B3F",width=1,opacity=.65))
    if show_reference and d["scale"] != 0:
        fig.add_trace(_lines(d["X"],d["edges"],"#ACC0CA",width=1,opacity=.18,name="Undeformed reference"))
    if show_extrema:
        ids=[int(np.argmin(val)),int(np.argmax(val))]
        fig.add_trace(go.Scatter3d(x=P[ids,0],y=P[ids,1],z=P[ids,2],
            mode="markers+text",marker=dict(size=5,color="#FFFFFF",line=dict(color="#102B3F",width=2)),
            text=[f"Surface {label} · {val[i]:.4g} {d['units']}" for label,i in zip(("min","max"),ids)],
            textposition="top center",textfont=dict(size=11),
            hovertext=[f"Node {d['nodes'][i]}" for i in ids],hoverinfo="text"))
    _layout(fig,height,view,projection,show_axes)
    fig.update_layout(title=dict(text=f"{d['title']}  /  {d['units']}     ·     Deformation ×{d['scale']:.4g}",
                                 x=.025,font=dict(size=12)),
                      meta=dict(deformation_scale=d["scale"],surface_nodes=len(d["nodes"]),
                                field=field,units=d["units"],surface_min=float(val.min()),surface_max=float(val.max())))
    return fig
