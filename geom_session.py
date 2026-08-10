#!/usr/bin/env python3
"""
geom_session.py - one Gmsh session, one tag namespace, CAD to deck.

Why this file exists.

Before this, geometry_features.build_catalogue() opened a Gmsh session, read
the faces, and closed it. mesh_agent.mesh_step() then opened a SECOND session
and imported the same STEP again. Two consequences, both bad:

  1. Physical groups created in session 1 do not exist in session 2, so face
     selections never reached the deck. The solver deck arrived at the load
     and constraint writer with a solid volume and nothing to attach a BC to.
  2. Face tag N in session 1 and face tag N in session 2 are only the same
     face because both imports happened to number the faces identically. That
     is a strong expectation, not a guarantee. A shifted tag would attach a
     load to the wrong face and still produce a clean, plausible, wrong
     answer.

GeomSession removes both. The STEP is imported once. The catalogue, the
selections, the mesh and the deck all live in that one model, so a face tag
means one thing for the whole run.

Lifetime rules, in order:

    import STEP  ->  synchronize  ->  catalogue  ->  select faces
        ->  mesh  ->  write deck  ->  extract node sets  ->  append NSETs

Node tags are mesh entities. mesh.clear() invalidates them, so node sets must
be extracted after the FINAL mesh, never before a retry.

Verified behaviour (gmsh 4.15.2, measured not assumed):
  - physical groups survive mesh.clear() and a full remesh
  - setOrder(2) after clear() does not compound
  - a 2D physical group causes Gmsh to write CPS6 surface elements into the
    .inp deck, which is why selections become node sets and not 2D groups
"""

from __future__ import annotations
from typing import List, Optional, Dict, Sequence
import os

import gmsh

import geometry_features as GF
from geometry_features import Catalogue, NamedSelection


class GeomSession:
    """Context manager owning a single Gmsh session for one part.

    Usage:
        with GeomSession("part.step") as ses:
            hole = GF.largest_hole(ses.catalogue)
            base = GF.extreme_planar_face(ses.catalogue, axis=2, side="min")
            ses.add_selection("PIN_HOLE", hole.tags, "load")
            ses.add_selection("MOUNT_FACE", base.tags, "constraint")
            result = run_mesh_agent(req, session=ses)
    """

    def __init__(self, step_path: str, verbose: bool = False):
        if not os.path.exists(step_path):
            raise FileNotFoundError(step_path)
        self.step_path = step_path
        self.verbose = verbose
        self.catalogue: Optional[Catalogue] = None
        self.selections: List[NamedSelection] = []
        self.node_sets: Dict[str, List[int]] = {}
        self._open = False
        self._solid_group: Optional[int] = None

    # -- lifetime ---------------------------------------------------------

    def __enter__(self) -> "GeomSession":
        gmsh.initialize()
        self._open = True
        gmsh.option.setNumber("General.Terminal", 1 if self.verbose else 0)
        gmsh.model.add("part")
        gmsh.model.occ.importShapes(self.step_path)
        gmsh.model.occ.synchronize()

        vols = [t for d, t in gmsh.model.getEntities(3)]
        if not vols:
            gmsh.finalize()
            self._open = False
            raise RuntimeError(f"{self.step_path} contains no 3D solid")
        # 3D physical group ONLY. This is what makes Gmsh write solid elements
        # to the deck and nothing else. Do not add 2D groups here.
        self._solid_group = gmsh.model.addPhysicalGroup(3, vols, name="SOLID")

        self.catalogue = GF.catalogue_from_open_model(self.step_path)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._open:
            gmsh.finalize()
            self._open = False
        return False

    def _require_open(self):
        if not self._open:
            raise RuntimeError("GeomSession is closed. Every tag from it is "
                               "now meaningless. Do the work inside the with "
                               "block.")

    # -- selection --------------------------------------------------------

    def add_selection(self, name: str, face_tags: Sequence[int],
                      role: str, source: str = "deterministic"
                      ) -> NamedSelection:
        """Register a named set of CAD faces. Validated against the catalogue,
        so a tag that does not exist fails here and not silently in the deck.
        """
        self._require_open()
        known = {f.tag for f in self.catalogue.faces}
        bad = [t for t in face_tags if t not in known]
        if bad:
            raise KeyError(f"selection '{name}' references faces {bad} that "
                           f"are not in the catalogue of {self.step_path}")
        if any(s.name == name for s in self.selections):
            raise ValueError(f"selection name '{name}' already used")
        if role not in ("load", "constraint", "observe"):
            raise ValueError(f"role must be load, constraint or observe, "
                             f"got '{role}'")
        sel = NamedSelection(name=name, tags=list(face_tags), role=role,
                             source=source)
        self.selections.append(sel)
        return sel

    # -- node sets --------------------------------------------------------

    def extract_node_sets(self, include_boundary: bool = True
                          ) -> Dict[str, List[int]]:
        """Node tags per selection from the CURRENT mesh. Call after the final
        mesh generation, never before a retry."""
        self._require_open()
        if not self.selections:
            return {}
        self.node_sets = GF.face_node_sets(self.selections,
                                           include_boundary=include_boundary)
        return self.node_sets

    def write_node_sets(self, deck_path: str) -> Optional[str]:
        """Append the node sets to an .inp deck. No-op for other formats."""
        if not self.node_sets:
            return None
        if not deck_path.endswith(".inp"):
            return None
        return GF.append_node_sets_inp(deck_path, self.node_sets)

    # -- reporting --------------------------------------------------------

    def render_selections(self) -> str:
        if not self.selections:
            return ("SELECTIONS: none. The deck will contain a solid with no "
                    "named faces, so no BC can be attached to it.")
        lines = ["SELECTIONS"]
        for s in self.selections:
            n = len(self.node_sets.get(s.name, []))
            lines.append(f"  {s.summary()}  nodes={n if n else 'not extracted'}")
        return "\n".join(lines)
