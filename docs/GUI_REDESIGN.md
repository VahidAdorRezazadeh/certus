# Certus workspace redesign

Company: **Verimech**. Tool: **Certus**. The public website is unchanged.

## Run locally

From the Certus repository, in the existing environment with Gmsh and CalculiX:

```sh
pip install -e ".[gui]"
certus-gui
```

The existing CAD-generation and language-model dependencies remain necessary for
their respective workflow stages. No new hosted service or image API is required.
The CLI applies the light workspace theme. Logos and IBM Plex fonts are packaged
with the application and do not load from a third-party font server.

## Interface

- Original Verimech SVG logo/emblem, navy/teal palette, IBM Plex Sans and Mono.
- Consistent six-stage progress header; earlier stages mean visited, not verified.
- Wider geometry and results viewports, distinct environment sidebar.
- Geometry preview in Part, face-selection preview in Faces, and an optional
  setup preview in Details.
- Separate Results, Verification evidence, and Report & run data tabs.
- Report and JSON downloads. Existing solver, checking and convergence routines
  remain the source of results and verdicts.

## Visualization

- Isometric, front, top and right camera presets; orthographic/perspective modes.
- Orbit, pan, zoom and local PNG export through the Plotly toolbar.
- Geometry opacity, face labels and surface triangulation edges.
- Orange load selection, blue support selection; text labels supplement colour.
- Schematic force-direction arrows in Details when a force direction is known.
  They do not represent the actual nodal load distribution. Pressure arrows are
  deliberately not inferred.
- Displacement magnitude/components and nodal von Mises contours.
- Explicit auto/true/undeformed/custom deformation scaling; magnification never
  alters the reported nodal field values.
- Undeformed reference, solver-mesh edge overlay, surface extrema and node hover
  readouts. Signed components use a zero-centred diverging colour map.
- C3D4/10/8/20 surface extraction, with quadratic midside nodes retained.

Surface extrema are not necessarily whole-model extrema. The geometry-stage
triangulation is a display mesh, not the solver mesh. Nodal stress is extrapolated
by CalculiX; a smooth contour is not evidence of convergence. Unsupported mesh
types and incomplete/non-finite fields produce an error rather than a misleading
plot. Flat global-coordinate decks are supported; assembly and *SYSTEM decks
are rejected by this viewer.

## Tests

```sh
python -m unittest discover -s tests -p test_viewer.py -v
python tests/test_gui.py
```

Viewer tests include both valid and invalid meshes/results, quadratic nodes,
shared-face removal, units, signed components, missing fields, and deformation
scaling. Synthetic field fixtures verify display mapping, not solver accuracy.
The existing end-to-end GUI test uses honest and lying language-model stubs and
a real CalculiX solve. A pull-request workflow runs both suites with the GUI
dependencies installed.

No claim is made that this change validates the physics, fixes the prototype's
known wrong-load-face blind spot, or broadens its solver capabilities.

## 28 September: workflow and first-page refinement

- Larger IBM Plex Sans stage numbers and titles; navy active stage with a teal marker.
- Stronger numbered section headings across all six stages.
- Replaced the first-page slogans with a single, structured review-checkpoint panel.
- Claude catalogue refresh uses the account's Models API, with manual ID entry and
  explicit failure handling. Install `.[claude]` if Claude support is missing.
- Sidebar explains why a local Abaqus installation is not an executable backend.

Validation this update: 32 viewer tests and 3 catalogue unit tests passed. The real
Streamlit first page loaded without exceptions through AppTest; catalogue refresh,
selection, manual entry and error recovery passed with a stub API. No live Claude
request, new full solver run, or Windows run was performed. The cloud browser blocked the local preview connection. The supplied PNG is an
illustrative layout preview, not a browser screenshot; native controls and connection
messages can differ. Visual browser QA remains outstanding.
