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
    st.html('<div role="banner" class="certus-topbar"><div class="certus-product">'
            f'<img src="{asset_uri("Verimech_Icon_Reversed.svg")}" alt="Verimech emblem">'
            '<div class="certus-product-title"><span class="certus-company">VERIMECH</span>'
            '<span class="certus-tool">Certus</span></div>'
            '<span class="certus-product-kind">Simulation workspace</span>'
            '</div><span class="certus-pill">DEVELOPMENT PROTOTYPE</span></div>')
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
    number, separator, category = eyebrow.partition(" / ")
    marker = f'<span class="certus-section-number">{escape(number)}</span>' if separator else ''
    st.html(f'<div class="certus-section-heading">{marker}<div>'
            f'<p class="certus-section-category">{escape(category if separator else eyebrow)}</p>'
            f'<h1>{escape(title)}</h1><p class="certus-section-description">{escape(description)}</p></div></div>')


def review_guide():
    """Task guidance, not a promise of universal verification."""
    import streamlit as st
    st.html('<aside class="certus-guide"><div class="certus-guide-title">'
            '<span>ANALYSIS PROTOCOL</span><h2>Your review checkpoints</h2></div>'
            '<ol><li><span>1</span><div><h3>Review the interpretation</h3>'
            '<p>Check dimensions, units and assumptions against your request.</p></div></li>'
            '<li><span>2</span><div><h3>Confirm the physical setup</h3>'
            '<p>Inspect the geometry and confirm the load and support faces.</p></div></li>'
            '<li><span>3</span><div><h3>Assess the evidence</h3>'
            '<p>Review results, unresolved findings and which checks ran.</p></div></li></ol>'
            '<footer>Checks cover the reported scope. They do not certify the model.</footer></aside>')


def note_card(title: str, body: str):
    import streamlit as st
    st.html(f'<aside class="certus-note"><h3>{escape(title)}</h3><p>{escape(body)}</p></aside>')


def review_table_html(rows):
    statuses = {"read": "Read from your text", "missing": "Missing · not stated",
                "rejected": "Rejected · not accepted", "draft": "Draft · review",
                "preset": "Preset · confirm"}
    parts = ['<div class="certus-review-table"><table><caption>Engineering input review</caption>'
             '<thead><tr><th scope="col">Field</th><th scope="col">Value</th>'
             '<th scope="col">Status / source</th></tr></thead><tbody>']
    group = None
    for row in rows:
        if row["group"] != group:
            group = row["group"]
            parts.append(f'<tr class="certus-review-group"><th colspan="3" scope="rowgroup">{escape(group)}</th></tr>')
        status = row["status"] if row["status"] in statuses else "missing"
        value = "—" if row["value"] is None else str(row["value"])
        parts.append(f'<tr><th scope="row">{escape(row["field"])}</th>'
                     f'<td class="certus-review-value">{escape(value)}</td><td>'
                     f'<span class="certus-review-status {status}">{statuses[status]}</span>'
                     f'<span class="certus-review-source">{escape(row["source"])}</span></td></tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


def review_table(rows):
    import streamlit as st
    st.html(review_table_html(rows))


def part_summary(spec):
    """Readable draft geometry, with no editable implementation format."""
    import streamlit as st
    if not spec:
        st.info("No part specification yet. Describe the geometry in the correction prompt.")
        return
    overall = spec.get("overall_mm") or {}
    if not isinstance(overall, dict):
        overall = {}
    items = [("Part", spec.get("part_name") or "Not stated")]
    for axis in ('x', 'y', 'z'):
        value = overall.get(axis)
        items.append((f"Overall {axis.upper()}", "Missing" if value is None else f"{value} mm"))
    features = spec.get("features") or []
    if isinstance(features, list) and features:
        items.append(("Features", "; ".join(str(v) for v in features)))
    holes = spec.get("holes") or []
    if isinstance(holes, list):
        for i, hole in enumerate(holes, 1):
            if isinstance(hole, dict):
                description = f"Diameter: {hole.get('diameter_mm', 'missing')} mm · axis: {hole.get('axis', 'missing')} · count: {hole.get('count', 'missing')}"
                items.append((f"Hole {i}", description))
    st.html('<section class="certus-spec-summary" aria-label="Draft part specification"><dl>' +
            ''.join(f'<div><dt>{escape(label)}</dt><dd>{escape(str(value))}</dd></div>' for label, value in items) +
            '</dl><p>Draft geometry. The built part will be measured against this specification.</p></section>')


def status_banner(trust, headline: str):
    import streamlit as st
    kind, label = ("pass", "Passed the checks that ran") if trust is True else (
        ("fail", "Unresolved findings · review required") if trust is False else
        ("pending", "No trusted result · refused or not solved"))
    st.html(f'<section role="status" class="certus-verdict {kind}">'
            '<p class="certus-eyebrow">ANALYSIS VERDICT</p>'
            f'<h2>{label}</h2><p>{escape(headline)}</p></section>')
    st.caption("A check verdict applies only to the checks listed below. It is not a certification of the model or result.")


def measurement_html(results):
    names = {'geometry_valid': 'Solid validity', 'single_solid': 'Solid count',
             'overall_x': 'Overall X dimension', 'overall_y': 'Overall Y dimension',
             'overall_z': 'Overall Z dimension', 'bbox_fill': 'Bounding-box fill ratio',
             'min_face_count': 'Face count', 'volume': 'Solid volume',
             'no_unexpected_holes': 'Unexpected holes'}
    body = []
    for result in results:
        status = 'PASS' if result['status'] == 'PASS' else 'FAIL' if result['critical'] else 'WARN'
        name = names.get(result['name'], result['name'].replace('_', ' ').title())
        body.append(f'<article class="certus-check-row {status.lower()}">'
                    f'<div><h4>{escape(name)}</h4><span>{status}</span></div>'
                    f'<p>{escape(result["detail"])}</p></article>')
    count = sum(r['status'] == 'PASS' for r in results)
    return ('<section class="certus-measurements" aria-label="Geometry measurement evidence">'
            f'<header><span>MEASURED GEOMETRY</span><strong>{count} / {len(results)} checks passed</strong></header>' +
            ''.join(body) + '<footer>Arithmetic checks against the confirmed part specification. '
            'These checks do not verify the simulation physics.</footer></section>')


def measurement_evidence(results):
    import streamlit as st
    st.html(measurement_html(results))


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


def geometry_view(tri, load_tags=(), fix_tags=(), hover=None, *, key="geometry", load_vector=None,
                  load_kind="force", support_label="Selected support face"):
    import streamlit as st
    from certus import viewer
    with st.container(border=True, key=f"{key}_viewport"):
        title, fit = st.columns([4,1])
        title.markdown("**Geometry viewport**")
        if fit.button("Fit part", key=f"{key}_fit"):
            st.session_state[f"{key}_camera_revision"] = st.session_state.get(f"{key}_camera_revision",0) + 1
        opts = view_options(key)
        with st.expander("Display settings"):
            a, b, c = st.columns(3)
            opacity = a.slider("Surface opacity", 0.15, 1.0, 1.0, 0.05, key=f"{key}_opacity")
            axes = b.toggle("Coordinate axes", value=True, key=f"{key}_axes")
            labels = c.toggle("Face labels", value=False, key=f"{key}_labels")
        fig = viewer.faces_figure(tri, load_tags, fix_tags, hover, height=560,
                                  opacity=opacity, show_axes=axes, show_labels=labels,
                                  load_vector=load_vector, load_kind=load_kind,
                                  support_label=support_label, **opts)
        fig.layout.uirevision += f"-{st.session_state.get(f'{key}_camera_revision',0)}"
        st.plotly_chart(fig, width="stretch", theme=None, key=f"{key}_plot", config=PLOT_CONFIG)
        if load_tags or fix_tags:
            st.html('<div class="certus-viewport-footer"><span><i class="load"></i>Selected load faces</span>'
                    '<span><i class="support"></i>Selected support faces</span></div>')
        else:
            st.caption("Face roles have not been assigned. Load and support faces are selected at the next step."
                       if key == "part" else "Choose load and support features to preview their locations.")
        st.caption("Drag to orbit · scroll to zoom · hover for face names and coordinates · Fit part to reset. "
                   "Surface triangulation is for viewing, not the solver mesh. Arrows and support symbols are schematic.")


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
            signed = fields[which] in ("UX", "UY", "UZ")
            colorscale = c1.selectbox("Colour map", ["RdBu_r"] if signed else ["Viridis", "Cividis", "Turbo"],
                                     key=f"{key}_colors_{signed}", disabled=signed)
            if signed:
                c1.caption("Signed components use a diverging scale centred on zero.")
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
