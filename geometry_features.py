#!/usr/bin/env python3
"""
geometry_features.py - CAD face catalogue and selection.

The layer between "a STEP file" and "where do the loads and constraints go".

Design rule, and it is the reason this file exists separately from the mesh
agent: BOUNDARY CONDITIONS ARE ATTACHED TO CAD FACES, NEVER TO NODES.
Node numbers change on every remesh, and the mesh agent remeshes on retry.
CAD face tags survive remeshing, so a BC defined here stays attached to the
physical feature it was meant for.

Division of labour, same pattern as the mesh agent:
    deterministic   building the catalogue, computing axes, radii, normals,
                    grouping coaxial cylinders, all geometric selectors
    LLM             one job only: mapping a human phrase like "the pin hole"
                    onto tags that are already in the catalogue

The LLM never invents a face. It picks from a list that geometry produced.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Tuple, Sequence
import math

import gmsh

Vec = Tuple[float, float, float]


# ---------------------------------------------------------------------------
# small vector helpers
# ---------------------------------------------------------------------------

def _sub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Sequence[float]) -> Vec:
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


def _cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _circumcentre(pa: Vec, pb: Vec, pc: Vec) -> Vec:
    """Centre of the circle through three points in 3D."""
    a = _sub(pa, pc)
    b = _sub(pb, pc)
    axb = _cross(a, b)
    denom = 2.0 * _dot(axb, axb)
    if abs(denom) < 1e-20:
        return tuple((pa[i] + pb[i] + pc[i]) / 3.0 for i in range(3))
    na = _dot(a, a)
    nb = _dot(b, b)
    term = _cross(_sub(tuple(na * b[i] for i in range(3)),
                       tuple(nb * a[i] for i in range(3))), axb)
    return tuple(pc[i] + term[i] / denom for i in range(3))


# ---------------------------------------------------------------------------
# Face feature
# ---------------------------------------------------------------------------

@dataclass
class FaceFeature:
    tag: int
    surface_type: str                 # "Plane", "Cylinder", "Cone", ...
    area: float
    centroid: Vec

    # planes
    normal: Optional[Vec] = None

    # cylinders and cones
    axis: Optional[Vec] = None
    radius: Optional[float] = None
    axial_length: Optional[float] = None
    is_internal: Optional[bool] = None   # True = hole (concave), False = boss
    angular_span_deg: Optional[float] = None  # 360 for a full cylinder

    # grouping
    group_id: Optional[int] = None

    @property
    def is_hole(self) -> bool:
        return self.surface_type == "Cylinder" and bool(self.is_internal)

    @property
    def diameter(self) -> Optional[float]:
        return 2.0 * self.radius if self.radius is not None else None

    def summary(self) -> str:
        c = self.centroid
        s = (f"face {self.tag:<3d} {self.surface_type:<9s} "
             f"area={self.area:8.1f} "
             f"centroid=({c[0]:6.1f},{c[1]:6.1f},{c[2]:6.1f})")
        if self.normal:
            n = self.normal
            s += f" normal=({n[0]:+.2f},{n[1]:+.2f},{n[2]:+.2f})"
        if self.radius is not None:
            a = self.axis or (0, 0, 0)
            kind = "HOLE" if self.is_internal else "boss"
            s += (f" {kind} dia={self.diameter:.2f} len={self.axial_length:.2f}"
                  f" axis=({a[0]:+.2f},{a[1]:+.2f},{a[2]:+.2f})"
                  f" span={self.angular_span_deg:.0f}deg")
        if self.group_id is not None:
            s += f" group={self.group_id}"
        return s

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FaceGroup:
    """Faces that belong to one physical feature.

    The pin hole in a clevis bracket is TWO cylindrical faces, one per ear.
    A user saying 'the pin hole' means both. Grouping coaxial, equal-radius
    cylinders is what turns their words into the right set of tags.
    """
    group_id: int
    kind: str                      # "hole" | "boss" | "coplanar" | "single"
    tags: List[int]
    radius: Optional[float] = None
    axis: Optional[Vec] = None
    axis_point: Optional[Vec] = None
    normal: Optional[Vec] = None
    total_area: float = 0.0

    def summary(self) -> str:
        s = f"group {self.group_id}: {self.kind:<9s} faces={self.tags} area={self.total_area:.1f}"
        if self.radius:
            s += f" dia={2*self.radius:.2f}"
        if self.normal:
            n = self.normal
            s += f" normal=({n[0]:+.2f},{n[1]:+.2f},{n[2]:+.2f})"
        return s


@dataclass
class Catalogue:
    step_path: str
    faces: List[FaceFeature] = field(default_factory=list)
    groups: List[FaceGroup] = field(default_factory=list)
    bbox_min: Vec = (0.0, 0.0, 0.0)
    bbox_max: Vec = (0.0, 0.0, 0.0)
    volume: float = 0.0

    def face(self, tag: int) -> FaceFeature:
        for f in self.faces:
            if f.tag == tag:
                return f
        raise KeyError(f"no face {tag}")

    def group(self, gid: int) -> FaceGroup:
        for g in self.groups:
            if g.group_id == gid:
                return g
        raise KeyError(f"no group {gid}")

    def render(self) -> str:
        lo, hi = self.bbox_min, self.bbox_max
        lines = [
            f"CATALOGUE for {self.step_path}",
            f"  bbox  ({lo[0]:.1f},{lo[1]:.1f},{lo[2]:.1f}) to "
            f"({hi[0]:.1f},{hi[1]:.1f},{hi[2]:.1f})   volume {self.volume:.1f}",
            f"  {len(self.faces)} faces, {len(self.groups)} feature groups",
            "",
            "FACES",
        ]
        lines += ["  " + f.summary() for f in self.faces]
        lines += ["", "FEATURE GROUPS"]
        lines += ["  " + g.summary() for g in self.groups]
        return "\n".join(lines)

    def describe_for_llm(self) -> str:
        """Compact catalogue for the naming step. Deliberately small: the
        model's job is to choose an id, not to reason about geometry."""
        out = ["Feature groups available (choose by group id):"]
        for g in self.groups:
            bits = [f"id={g.group_id}", f"kind={g.kind}",
                    f"faces={g.tags}", f"area={g.total_area:.0f}"]
            if g.radius:
                bits.append(f"diameter={2*g.radius:.2f}")
            if g.normal:
                bits.append("normal=(%.2f,%.2f,%.2f)" % g.normal)
            if g.axis_point:
                bits.append("at=(%.1f,%.1f,%.1f)" % g.axis_point)
            out.append("  " + ", ".join(bits))
        return "\n".join(out)


# ---------------------------------------------------------------------------
# Building the catalogue
# ---------------------------------------------------------------------------

_ANG_SAMPLES = 16


def _plane_normal(tag: int) -> Optional[Vec]:
    try:
        (umin, vmin), (umax, vmax) = _param_bounds(tag)
        n = gmsh.model.getNormal(tag, [(umin + umax) / 2, (vmin + vmax) / 2])
        return _unit(n)
    except Exception:
        return None


def _param_bounds(tag: int):
    b = gmsh.model.getParametrizationBounds(2, tag)
    return (b[0][0], b[0][1]), (b[1][0], b[1][1])


def _cylinder_props(tag: int):
    """Derive axis, radius, axial length, internal/external and angular span.

    Gmsh does not expose the underlying OCC cylinder directly, so this samples
    the parametrisation. For an OCC cylindrical face the parameters are
    (angle, axial position), which is what makes this robust.
    """
    (umin, vmin), (umax, vmax) = _param_bounds(tag)

    # axis: move along v at fixed u
    p_lo = gmsh.model.getValue(2, tag, [umin, vmin])
    p_hi = gmsh.model.getValue(2, tag, [umin, vmax])
    axis = _unit(_sub(p_hi, p_lo))
    axial_length = _norm(_sub(p_hi, p_lo))

    # BUG FOUND IN TESTING: averaging sampled points gives the axis centre only
    # for a FULL 360 degree cylinder. On a 90 degree fillet face the mean sits
    # off-axis and the radius came out roughly a third of the true value.
    # Circumcircle through three arc points is correct for any angular span.
    vm = (vmin + vmax) / 2.0
    pts = []
    for i in range(_ANG_SAMPLES):
        u = umin + (umax - umin) * i / max(1, _ANG_SAMPLES - 1)
        pts.append(gmsh.model.getValue(2, tag, [u, vm]))

    pa, pb, pc = pts[0], pts[len(pts) // 2], pts[-1]
    ab, bc, ca = _sub(pb, pa), _sub(pc, pb), _sub(pa, pc)
    a, b, c = _norm(bc), _norm(ca), _norm(ab)
    area2 = _norm(_cross(ab, _sub(pc, pa)))     # 2 * triangle area
    if area2 > 1e-12:
        radius = (a * b * c) / (2.0 * area2)
        centre = _circumcentre(pa, pb, pc)
    else:
        centre = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
        radius = sum(_norm(_sub(p, centre)) for p in pts) / len(pts)

    # internal (hole) or external (boss): the outward surface normal of a hole
    # points TOWARD the axis, so its dot with the outward radial vector is
    # negative.
    p0 = pts[0]
    radial = _unit(_sub(p0, centre))
    try:
        n0 = _unit(gmsh.model.getNormal(tag, [umin, vm]))
        is_internal = _dot(n0, radial) < 0.0
    except Exception:
        is_internal = None

    span = math.degrees(abs(umax - umin))
    return axis, radius, axial_length, is_internal, span, centre


def build_catalogue(step_path: str) -> Catalogue:
    """Read a STEP and produce the face catalogue. Deterministic, no LLM."""
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("catalogue")
        gmsh.model.occ.importShapes(step_path)
        gmsh.model.occ.synchronize()

        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
        vol = sum(gmsh.model.occ.getMass(3, t)
                  for d, t in gmsh.model.getEntities(3))

        faces: List[FaceFeature] = []
        for dim, tag in gmsh.model.getEntities(2):
            stype = gmsh.model.getType(2, tag)
            area = gmsh.model.occ.getMass(2, tag)
            com = gmsh.model.occ.getCenterOfMass(2, tag)
            f = FaceFeature(tag=tag, surface_type=stype, area=area,
                            centroid=(com[0], com[1], com[2]))
            if stype == "Plane":
                f.normal = _plane_normal(tag)
            elif stype == "Cylinder":
                try:
                    ax, r, L, internal, span, _c = _cylinder_props(tag)
                    f.axis, f.radius, f.axial_length = ax, r, L
                    f.is_internal, f.angular_span_deg = internal, span
                except Exception:
                    pass
            faces.append(f)

        cat = Catalogue(step_path=step_path, faces=faces,
                        bbox_min=(xmin, ymin, zmin), bbox_max=(xmax, ymax, zmax),
                        volume=vol)
        cat.groups = _group_faces(cat)
        return cat
    finally:
        gmsh.finalize()


# ---------------------------------------------------------------------------
# Grouping: turn faces into physical features
# ---------------------------------------------------------------------------

def _axes_parallel(a: Vec, b: Vec, tol_deg: float = 2.0) -> bool:
    d = abs(_dot(_unit(a), _unit(b)))
    return d > math.cos(math.radians(tol_deg))


def _axes_collinear(a_axis: Vec, a_pt: Vec, b_pt: Vec, tol: float) -> bool:
    """Is b_pt on the line through a_pt along a_axis, within tol."""
    d = _sub(b_pt, a_pt)
    perp = _cross(d, _unit(a_axis))
    return _norm(perp) < tol


def _group_faces(cat: Catalogue) -> List[FaceGroup]:
    span = max(cat.bbox_max[i] - cat.bbox_min[i] for i in range(3))
    tol = max(1e-4, span * 1e-3)

    groups: List[FaceGroup] = []
    gid = 0
    used = set()

    # --- coaxial cylinders of equal radius -> one hole or boss -------------
    cyls = [f for f in cat.faces
            if f.surface_type == "Cylinder" and f.radius is not None]
    for i, f in enumerate(cyls):
        if f.tag in used:
            continue
        members = [f]
        for g in cyls[i + 1:]:
            if g.tag in used:
                continue
            if (abs(g.radius - f.radius) < tol
                    and g.is_internal == f.is_internal
                    and _axes_parallel(f.axis, g.axis)
                    and _axes_collinear(f.axis, f.centroid, g.centroid, tol)):
                members.append(g)
        for m in members:
            used.add(m.tag)
            m.group_id = gid
        groups.append(FaceGroup(
            group_id=gid,
            kind="hole" if f.is_internal else "boss",
            tags=[m.tag for m in members],
            radius=f.radius, axis=f.axis, axis_point=f.centroid,
            total_area=sum(m.area for m in members)))
        gid += 1

    # --- coplanar planes -> one flat feature -------------------------------
    planes = [f for f in cat.faces
              if f.surface_type == "Plane" and f.normal is not None]
    for i, f in enumerate(planes):
        if f.tag in used:
            continue
        members = [f]
        for g in planes[i + 1:]:
            if g.tag in used:
                continue
            same_dir = _dot(f.normal, g.normal) > math.cos(math.radians(2.0))
            same_plane = abs(_dot(_sub(g.centroid, f.centroid), f.normal)) < tol
            if same_dir and same_plane:
                members.append(g)
        for m in members:
            used.add(m.tag)
            m.group_id = gid
        groups.append(FaceGroup(
            group_id=gid, kind="coplanar" if len(members) > 1 else "single",
            tags=[m.tag for m in members], normal=f.normal,
            axis_point=f.centroid,
            total_area=sum(m.area for m in members)))
        gid += 1

    # --- anything else stays on its own ------------------------------------
    for f in cat.faces:
        if f.tag in used:
            continue
        f.group_id = gid
        groups.append(FaceGroup(group_id=gid, kind="single", tags=[f.tag],
                                axis_point=f.centroid, total_area=f.area))
        used.add(f.tag)
        gid += 1

    return groups


# ---------------------------------------------------------------------------
# Deterministic selectors
# ---------------------------------------------------------------------------

def holes(cat: Catalogue, diameter: Optional[float] = None,
          tol: float = 0.2) -> List[FaceGroup]:
    out = [g for g in cat.groups if g.kind == "hole"]
    if diameter is not None:
        out = [g for g in out if abs(2 * g.radius - diameter) <= tol]
    return sorted(out, key=lambda g: -g.total_area)


def largest_hole(cat: Catalogue) -> Optional[FaceGroup]:
    h = holes(cat)
    return max(h, key=lambda g: g.radius) if h else None


def planar_groups(cat: Catalogue, normal: Optional[Vec] = None,
                  tol_deg: float = 5.0) -> List[FaceGroup]:
    out = [g for g in cat.groups
           if g.kind in ("coplanar", "single") and g.normal is not None]
    if normal is not None:
        u = _unit(normal)
        out = [g for g in out
               if _dot(g.normal, u) > math.cos(math.radians(tol_deg))]
    return sorted(out, key=lambda g: -g.total_area)


def extreme_planar_face(cat: Catalogue, axis: int, side: str = "min"
                        ) -> Optional[FaceGroup]:
    """The flat face at the bottom / top / one end of the part.

    axis: 0=x, 1=y, 2=z. side: 'min' or 'max'.
    This is how 'the mounting face' usually gets found without asking anyone.
    """
    want = (0.0, 0.0, 0.0)
    want = tuple(-1.0 if (i == axis and side == "min") else
                 (1.0 if (i == axis and side == "max") else 0.0)
                 for i in range(3))
    cands = planar_groups(cat, normal=want, tol_deg=5.0)
    if not cands:
        return None
    # BUG FOUND IN TESTING: picking the largest candidate returned the top of
    # the base plate (z=2.5) instead of the ear tops (z=30.5), because both
    # face +z and the base plate is bigger. 'Extreme' must mean extreme in
    # POSITION. Area is only the tie-break.
    def key(g: FaceGroup):
        pos = g.axis_point[axis] if g.axis_point else 0.0
        return (pos if side == "max" else -pos, g.total_area)
    return max(cands, key=key)


def largest_face(cat: Catalogue) -> FaceGroup:
    return max(cat.groups, key=lambda g: g.total_area)


# ---------------------------------------------------------------------------
# Writing the selection into the mesh
# ---------------------------------------------------------------------------

@dataclass
class NamedSelection:
    name: str          # becomes the ELSET / NSET name in the deck
    tags: List[int]    # CAD face tags
    role: str          # "load" | "constraint" | "observe"
    source: str = "deterministic"   # or "llm" or "user"

    def summary(self) -> str:
        return (f"{self.name:<16s} role={self.role:<10s} faces={self.tags} "
                f"[{self.source}]")


def tag_selections_in_gmsh(selections: Sequence[NamedSelection]) -> Dict[str, int]:
    """Create a physical surface group per selection.

    Call this AFTER importing the STEP and BEFORE meshing. Gmsh then writes the
    face sets into the deck, which is what the load and constraint writer will
    reference. Without this step the deck contains only the solid and there is
    nothing to attach a BC to.
    """
    created = {}
    for sel in selections:
        pg = gmsh.model.addPhysicalGroup(2, sel.tags, name=sel.name)
        created[sel.name] = pg
    return created


# ---------------------------------------------------------------------------
# LLM edge: phrase -> group id. Optional, never invents geometry.
# ---------------------------------------------------------------------------

SELECT_SYSTEM = (
    "You map an engineer's phrase onto feature group ids from a CAD "
    "catalogue.\n"
    "Return ONLY a JSON object, no prose, no markdown fences:\n"
    '  {"group_ids": [int, ...], "confident": true/false, "reason": "..."}\n'
    "Rules:\n"
    "- You may ONLY return ids that appear in the catalogue.\n"
    "- If more than one group plausibly matches, return confident=false and "
    "list all plausible ids. Do not pick one to seem decisive.\n"
    "- If nothing matches, return an empty list and confident=false.\n"
    "- A phrase like 'the pin hole' may map to a group containing several "
    "faces. That is normal, return the group id."
)


def resolve_selection(phrase: str, cat: Catalogue,
                      model: Optional[str] = None) -> Dict[str, Any]:
    """Optional. Requires ANTHROPIC_API_KEY."""
    import os
    import json
    from anthropic import Anthropic
    model = model or os.environ.get("BC_AGENT_MODEL", "claude-sonnet-5")
    client = Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=500, system=SELECT_SYSTEM,
        messages=[{"role": "user", "content":
                   f"{cat.describe_for_llm()}\n\nPhrase: {phrase}"}])
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    out = json.loads(raw)
    valid = {g.group_id for g in cat.groups}
    bad = [i for i in out.get("group_ids", []) if i not in valid]
    if bad:
        # the model invented a group. Do not pass it downstream.
        out["group_ids"] = [i for i in out["group_ids"] if i in valid]
        out["confident"] = False
        out["reason"] = f"model returned invalid ids {bad}; " + out.get("reason", "")
    return out


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import mesh_agent as M
    step = M._make_test_bracket()
    cat = build_catalogue(step)
    print(cat.render())

    print("\n" + "=" * 70)
    print("DETERMINISTIC SELECTORS")
    print("=" * 70)
    h = largest_hole(cat)
    print("largest hole      :", h.summary() if h else "none")
    print("all 8mm holes     :", [g.group_id for g in holes(cat, diameter=8.0)])
    bottom = extreme_planar_face(cat, axis=2, side="min")
    print("bottom face       :", bottom.summary() if bottom else "none")
    top = extreme_planar_face(cat, axis=2, side="max")
    print("top face          :", top.summary() if top else "none")

    print("\n" + "=" * 70)
    print("WHAT THE LLM WOULD SEE")
    print("=" * 70)
    print(cat.describe_for_llm())

    print("\n" + "=" * 70)
    print("EXAMPLE SELECTION SET")
    print("=" * 70)
    sels = []
    if h:
        sels.append(NamedSelection("PIN_HOLE", h.tags, "load"))
    if bottom:
        sels.append(NamedSelection("MOUNT_FACE", bottom.tags, "constraint"))
    for s in sels:
        print("  " + s.summary())
