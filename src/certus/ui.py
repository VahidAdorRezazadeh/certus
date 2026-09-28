"""Verimech presentation layer. No model inputs or physics decisions live here."""
from __future__ import annotations

import base64
from functools import lru_cache
from html import escape
from pathlib import Path

ASSETS = Path(__file__).with_name("assets")
STAGES = ("Ask", "Understood", "Part", "Faces", "Details", "Verdict")
STAGE_DESCRIPTIONS = ("Define the problem", "Review the interpretation", "Inspect the geometry",
                      "Confirm load & support", "Complete the model", "Review the evidence")
PLOT_CONFIG = {
    "displaylogo": False, "displayModeBar": True, "scrollZoom": False,
    "toImageButtonOptions": {"format": "png", "filename": "Certus_view", "scale": 2},
    "modeBarButtonsToRemove": ["sendDataToCloud"],
}


@lru_cache(maxsize=12)
def asset_uri(name: str) -> str:
    mime = "image/svg+xml" if name.endswith(".svg") else "font/ttf"
    return f"data:{mime};base64," + base64.b64encode((ASSETS / name).read_bytes()).decode()


@lru_cache(maxsize=1)
def stylesheet() -> str:
    fonts = "\n".join(
        f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};"
        f"font-display:swap;src:url('{asset_uri(file)}') format('truetype');}}"
        for family, weight, file in (
            ("IBM Plex Sans", 400, "Fonts/IBMPlexSans-400.ttf"),
            ("IBM Plex Sans", 600, "Fonts/IBMPlexSans-600.ttf"),
            ("IBM Plex Mono", 400, "Fonts/IBMPlexMono-400.ttf")))
    return fonts + (ASSETS / "workspace.css").read_text()


def apply_theme():
    import streamlit as st
    st.html("<style>" + stylesheet() + "</style>")


def workspace_header(stage: int):
    import streamlit as st
    st.html('<header class="certus-topbar"><div class="certus-product">'
            f'<img src="{asset_uri("Verimech_Icon.svg")}" alt="Verimech emblem">'
            '<span>Certus</span><span class="certus-product-kind">Simulation workspace</span>'
            '</div><span class="certus-pill">DEVELOPMENT PROTOTYPE</span></header>')
    steps = []
    for i, (name, description) in enumerate(zip(STAGES, STAGE_DESCRIPTIONS)):
        cls = "current" if i == stage else "previous" if i < stage else "upcoming"
        # Previous means visited, never "verified" or "passed".
        steps.append(f'<li class="{cls}"' + (' aria-current="step"' if i == stage else '') +
                     f'><span class="certus-step-number">{i+1:02}</span>'
                     f'<div><strong>{name}</strong><small>{description}</small></div></li>')
    st.html('<nav aria-label="Analysis workflow"><ol class="certus-stepper">' +
            ''.join(steps) + '</ol></nav>')


def sidebar_brand():
    import streamlit as st
    st.html(f'<div class="certus-brand"><img src="{asset_uri("Verimech_Logo_Dark.svg")}" '
            'alt="Verimech"><p>ENGINEERING ANALYSIS,<br>GROUNDED IN PHYSICS.</p></div>')


def section_heading(eyebrow: str, title: str, description: str = ""):
    import streamlit as st
    st.html(f'<div class="certus-section-heading"><p class="certus-eyebrow">{escape(eyebrow)}</p>'
            f'<h1>{escape(title)}</h1><p>{escape(description)}</p></div>')


def note_card(title: str, body: str):
    import streamlit as st
    st.html(f'<aside class="certus-note"><h3>{escape(title)}</h3><p>{escape(body)}</p></aside>')


def status_banner(trust, headline: str):
    import streamlit as st
    kind, label = ("pass", "Passed the checks that ran") if trust is True else (
        ("fail", "Unresolved findings · review required") if trust is False else
        ("pending", "No trusted result · refused or not solved"))
    st.html(f'<section role="status" class="certus-verdict {kind}">'
            '<p class="certus-eyebrow">ANALYSIS VERDICT</p>'
            f'<h2>{label}</h2><p>{escape(headline)}</p></section>')
    st.caption("A check verdict applies only to the checks listed below. It is not a certification of the model or result.")


def metric_row(meta):
    import streamlit as st
    values = (("Load-point displacement", meta.get("load_point_displacement_mm"), "mm"),
              ("Peak von Mises · this mesh", meta.get("max_von_mises_MPa"), "MPa"),
              ("Elements", meta.get("n_elements"), ""),
              ("Element size", meta.get("char_size_mm"), "mm"))
    for col, (label, value, unit) in zip(st.columns(4), values):
        col.metric(label, "Not available" if value is None else
                   (f"{value:,}" if label == "Elements" else f"{value:.4g}") +
                   (f" {unit}" if unit else ""))


def view_options(key: str):
    import streamlit as st
    a, b, c = st.columns([1.4, 1.1, 1])
    view = a.selectbox("View orientation", ["Isometric", "Front · XZ", "Top · XY", "Right · YZ"], key=f"{key}_view")
    projection = b.selectbox("Projection", ["Orthographic", "Perspective"], key=f"{key}_projection")
    edges = c.toggle("Mesh edges", value=False, key=f"{key}_edges")
    return dict(view=view, projection=projection.lower(), show_edges=edges)


def geometry_view(tri, load_tags=(), fix_tags=(), hover=None, *, key="geometry", load_vector=None):
    import streamlit as st
    from certus import viewer
    with st.container(border=True, key=f"{key}_viewport"):
        st.markdown("**Geometry viewport**")
        opts = view_options(key)
        with st.expander("Display settings"):
            a, b, c = st.columns(3)
            opacity = a.slider("Surface opacity", 0.15, 1.0, 1.0, 0.05, key=f"{key}_opacity")
            axes = b.toggle("Coordinate axes", value=True, key=f"{key}_axes")
            labels = c.toggle("Face labels", value=False, key=f"{key}_labels")
        fig = viewer.faces_figure(tri, load_tags, fix_tags, hover, height=560,
                                  opacity=opacity, show_axes=axes, show_labels=labels,
                                  load_vector=load_vector, **opts)
        st.plotly_chart(fig, width="stretch", theme=None, key=f"{key}_plot", config=PLOT_CONFIG)
        st.html('<div class="certus-viewport-footer"><span><i class="load"></i>Load selection</span>'
                '<span><i class="support"></i>Support selection</span>'
                '<span>Drag to orbit · right-drag to pan · toolbar to zoom / export</span></div>')
        st.caption("Surface triangulation for viewing; not the solver mesh. Coordinates in mm. Arrows, when shown, indicate force direction only.")


def results_view(deck, frd, *, key="results"):
    import streamlit as st
    from certus import viewer
    with st.container(border=True, key=f"{key}_viewport"):
        st.markdown("**Results viewport**")
        a, b = st.columns([1.7, 1])
        fields = {"Displacement magnitude |U| · mm": "U", "Displacement Ux · mm": "UX",
                  "Displacement Uy · mm": "UY", "Displacement Uz · mm": "UZ",
                  "von Mises stress · MPa": "S"}
        which = a.selectbox("Result field", list(fields), key=f"{key}_field")
        deformation = b.selectbox("Deformation", ["Auto magnification", "True scale ×1", "Undeformed", "Custom factor"], key=f"{key}_deformation")
        scale = {"Auto magnification": None, "True scale ×1": 1.0, "Undeformed": 0.0}.get(deformation)
        if deformation == "Custom factor":
            scale = st.number_input("Deformation factor", min_value=0.0, value=1.0, key=f"{key}_scale")
        opts = view_options(key)
        with st.expander("Contour & display settings"):
            c1, c2, c3 = st.columns(3)
            colorscale = c1.selectbox("Colour map", ["Viridis", "Cividis", "Turbo"], key=f"{key}_colors")
            reference = c2.toggle("Undeformed outline", value=True, key=f"{key}_reference")
            extrema = c3.toggle("Surface min / max", value=False, key=f"{key}_extrema")
            axes = c2.toggle("Coordinate axes", value=True, key=f"{key}_axes")
        try:
            fig = viewer.result_figure(deck, frd, fields[which], scale=scale, height=600,
                                      colorscale=colorscale, show_reference=reference,
                                      show_extrema=extrema, show_axes=axes, **opts)
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            st.error(f"Result view unavailable: {exc}")
            return
        st.plotly_chart(fig, width="stretch", theme=None, key=f"{key}_plot", config=PLOT_CONFIG)
        m = fig.layout.meta
        st.caption(f"Deformation ×{m['deformation_scale']:.4g} · {m['surface_nodes']:,} surface nodes · "
                   "hover for node values · coordinates and displacement in mm")
        if fields[which] == "S":
            st.caption("Nodal stress extrapolated by CalculiX. A smooth contour does not establish stress convergence.")
        st.caption("Contour limits and labelled extrema refer to the displayed surface. Reported analysis maxima may include interior nodes.")
