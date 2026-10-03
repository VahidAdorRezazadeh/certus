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
VIEW_BG, INK = "#F3F6FA", "#102B3F"
PART_COLOR = "#6397B9"


def _layout(fig, height, view, projection, show_axes=True):
    cameras = {
        "Isometric": dict(eye=dict(x=1.65,y=-1.85,z=1.35), up=dict(x=0,y=0,z=1)),
        "Front · XZ": dict(eye=dict(x=0,y=-2.3,z=0), up=dict(x=0,y=0,z=1)),
        "Top · XY": dict(eye=dict(x=0,y=0,z=2.3), up=dict(x=0,y=1,z=0)),
        "Right · YZ": dict(eye=dict(x=2.3,y=0,z=0), up=dict(x=0,y=0,z=1)),
    }
    camera = dict(cameras.get(view, cameras["Isometric"]), projection=dict(type=projection))
    points = []
    for trace in fig.data:
        if all(getattr(trace, key, None) is not None for key in ('x', 'y', 'z')):
            xyz = np.column_stack([trace.x, trace.y, trace.z]).astype(float)
            points.extend(xyz[np.isfinite(xyz).all(axis=1)])
    xyz = np.asarray(points)
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    span = max(float((hi-lo).max()), 1e-9)
    # Plotly's data aspect can normalize a long beam to a huge scene and clip
    # it under a fixed camera. Normalize manually and keep equal mm scale.
    ranges = np.column_stack((lo - span*.10, hi + span*.10))
    extents = ranges[:,1] - ranges[:,0]
    aspect = dict(zip(('x','y','z'), extents/extents.max()))
    def axis(label, i):
        return dict(title=dict(text=f"{label} [mm]", font=dict(size=16,color=INK)), visible=show_axes,
                    range=ranges[i].tolist(), showbackground=False, gridcolor="#D5DEE8",
                    zerolinecolor="#93A6B9", tickfont=dict(size=12,color="#40576D"),
                    nticks=5, showspikes=False)
    bounds = ','.join(f'{v:.6g}' for v in np.r_[lo,hi])
    fig.update_layout(
        template="none", height=height, margin=dict(l=12,r=20,t=36,b=12),
        paper_bgcolor=VIEW_BG, plot_bgcolor=VIEW_BG,
        font=dict(family="IBM Plex Sans, sans-serif",color=INK,size=14),
        scene=dict(bgcolor=VIEW_BG,aspectmode="manual",aspectratio=aspect,camera=camera,
                   dragmode="orbit", xaxis=axis("X",0), yaxis=axis("Y",1), zaxis=axis("Z",2)),
        uirevision=f"certus-{view}-{projection}-{bounds}",showlegend=False,
        modebar=dict(bgcolor="#E4EBF2",color="#40576D",activecolor="#007A7C"),
        hoverlabel=dict(bgcolor="#FFFFFF",font=dict(color=INK,size=14)),
    )
    return fig


def face_labels(catalogue):
    """Names derive from measured surface type and position, not an LLM."""
    labels = {}
    for group in catalogue.groups:
        if group.kind in ('hole', 'boss'):
            axis = 'XYZ'[int(np.argmax(np.abs(group.axis)))] if group.axis else '?'
            name = f"{group.kind.title()} Ø{2*group.radius:g} mm · axis {axis}" if group.radius else group.kind.title()
        elif group.normal:
            normal = np.asarray(group.normal)
            i = int(np.argmax(np.abs(normal)))
            if abs(normal[i]) > .98:
                name = { (0,False): 'X− end face', (0,True): 'X+ end face',
                         (1,False): 'Y− side face', (1,True): 'Y+ side face',
                         (2,False): 'Lower face (Z−)', (2,True): 'Upper face (Z+)'}[(i,normal[i]>0)]
            else:
                name = 'Inclined planar face'
        else:
            name = 'Curved surface'
        for tag in group.tags:
            labels[tag] = f"{name} · group {group.group_id} · area {group.total_area:.5g} mm²"
    return labels


def _arrow(fig, target, direction, length, color, label):
    import plotly.graph_objects as go
    direction = np.asarray(direction, dtype=float)
    norm = np.linalg.norm(direction)
    if not np.isfinite(direction).all() or norm <= 0:
        return
    vector = direction / norm * length
    start = target - vector
    fig.add_trace(_lines(np.array([start,target]),[(0,1)],color,width=5,name=label))
    fig.add_trace(go.Cone(x=[target[0]],y=[target[1]],z=[target[2]],
        u=[vector[0]],v=[vector[1]],w=[vector[2]],anchor="tip",sizemode="absolute",
        sizeref=length*.32,colorscale=[[0,color],[1,color]],showscale=False,
        name=label,hovertemplate=label+'<extra></extra>'))


def _support(fig, center, normal, size, label):
    import plotly.graph_objects as go
    normal = normal/max(np.linalg.norm(normal),1e-12)
    tangent = np.cross(normal, [0.,0.,1.])
    if np.linalg.norm(tangent)<1e-8:
        tangent = np.cross(normal,[0.,1.,0.])
    tangent /= max(np.linalg.norm(tangent),1e-12)
    other = np.cross(normal,tangent)
    base = center + normal*size
    points = np.array([center, base-tangent*size*.65-other*size*.4,
                       base+tangent*size*.65-other*size*.4, base+other*size*.65])
    fig.add_trace(go.Mesh3d(x=points[:,0],y=points[:,1],z=points[:,2],
        i=[0,0,0,1],j=[1,2,3,2],k=[2,3,1,3],color=SUPPORT_COLOR,
        name=label,flatshading=True,hovertemplate=label+'<extra></extra>'))


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
                 show_axes=True, show_labels=False, opacity=1.0, load_vector=None,
                 load_kind="force", support_label="Selected support face"):
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
            ("Support",SUPPORT_COLOR) if tag in fix_tags else ("Face",PART_COLOR))
        text = escape((hover or {}).get(tag,f"Face {tag}"))
        fig.add_trace(go.Mesh3d(
            x=xyz[:,0],y=xyz[:,1],z=xyz[:,2],i=t[:,0],j=t[:,1],k=t[:,2],
            color=color, opacity=1.0 if selected else opacity, flatshading=True,
            name=f"{role} · face {tag}",
            hovertemplate=f"<b>{role} · face {tag}</b><br>{text}<br>"+
                          "X %{x:.4g} mm<br>Y %{y:.4g} mm<br>Z %{z:.4g} mm<extra></extra>",
            lighting=dict(ambient=.58,diffuse=.8,specular=.25,roughness=.55),
            lightposition=dict(x=1000,y=-1500,z=1800)))
        # The boundary of each CAD face stays visible; optional triangulation is separate.
        from collections import Counter
        counts = Counter(tuple(sorted((int(a),int(b)))) for tr in t
                         for a,b in ((tr[0],tr[1]),(tr[1],tr[2]),(tr[2],tr[0])))
        edges = list(counts) if show_edges else [e for e,n in counts.items() if n == 1]
        fig.add_trace(_lines(xyz,edges,"#102B3F",width=1.4 if selected else .8,opacity=.65))
        area_vectors = np.cross(xyz[t[:,1]]-xyz[t[:,0]],xyz[t[:,2]]-xyz[t[:,0]])
        areas = np.linalg.norm(area_vectors,axis=1)
        center = np.average(xyz[t].mean(axis=1),axis=0,weights=areas) if areas.sum() else xyz.mean(axis=0)
        if show_labels:
            label = (hover or {}).get(tag,f"Face {tag}").split(" · group")[0]
            centers.append((center, f"{role} · {label}" if selected else label))
        if tag in load_tags:
            if load_kind == 'pressure':
                for i in np.linspace(0,len(t)-1,min(3,len(t)),dtype=int):
                    target = xyz[t[i]].mean(axis=0)
                    _arrow(fig,target,-area_vectors[i],span*.12,LOAD_COLOR,'Pressure normal (schematic)')
            elif load_vector is not None:
                _arrow(fig,center,load_vector,span*.16,LOAD_COLOR,'Force direction (schematic)')
        if tag in fix_tags:
            from html import escape as _escape
            for i in np.linspace(0,len(t)-1,min(3,len(t)),dtype=int):
                _support(fig,xyz[t[i]].mean(axis=0),area_vectors[i],span*.035,
                         _escape(support_label)+' · schematic support symbol')
    if centers:
        points=np.asarray([p for p,_ in centers])
        fig.add_trace(go.Scatter3d(x=points[:,0],y=points[:,1],z=points[:,2],
                    mode="markers+text",text=[s for _,s in centers],textposition="top center",
                    marker=dict(size=3,color=INK),textfont=dict(size=13,color=INK),hoverinfo="skip"))
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
