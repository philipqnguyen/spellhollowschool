"""The two creatures of the Creature Keeping
class, `beast_puff` and `beast_lynxkit` (docs/architecture.md). Each is its own small glb,
`client/public/assets/instances/props/<id>.glb` (build_school.py
`build_prop`, through school_rooms_paddock.CLASS_PROPS), that the client
stands at the paddock layout's landmark of the same id and moves by node
transforms only. Cartoonish and invented in look; nothing more.

* `beast_puff`: a Fluffbell-style fluffy ball (our own design): a lumpy
  custard-yellow puff with darker tufts, two round black eyes and a long
  pink tongue that curls out of its mouth. About 0.45 m tall.
* `beast_lynxkit`: a Lynxkit-style small cat in a simple low-poly shape: a
  speckled sandy coat, a big head with large tufted ears, amber eyes, a
  pale muzzle and paws, and a long tail ending in a lion-like tuft. About
  0.5 m tall to the ear tips, 0.7 m long with the tail.

Each prop is two nodes: the body (the root, origin on the ground at its
base, facing +z, the students) and one child the client may also swing
(the Fluffbell's tongue, the Lynxkit's tail), its origin at its root. Both
share one material (a small swatch texture of palette names: two draws in
all, under 3,000 triangles). Colours come from castle_textures.json
`palette` by name (castle_kit/props_atlas.palette); no colour literal here.
Pure Python but `blender_objects` (imports bpy).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import meshkit as mk
from meshkit import Mesh

SWATCH_GRID = 4  # 4 x 4 swatches
SWATCH_PX = 64  # 16 px each
MAX_TRIS = 3_000
MAX_DRAWS = 2

# Zones -> palette names (castle_textures.json `palette`).
PUFF_ZONES = {1: "beast_puff_fur", 2: "beast_puff_tuft", 3: "beast_tongue", 4: "soot", 5: "letter_white"}
LYNXKIT_ZONES = {1: "beast_lynxkit_fur", 2: "beast_lynxkit_spot", 3: "owl_pale", 4: "beast_tongue", 5: "gilt",
                 6: "soot", 7: "tapestry_umber", 8: "letter_white"}


@dataclass
class Part:
    """One node of a prop: its mesh in the node's frame and the node's
    origin in the prop's frame (Y up, metres)."""

    name: str
    mesh: Mesh
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class Beast:
    pid: str
    zones: dict
    parts: list  # [body, child]

    def triangles(self) -> int:
        return sum(p.mesh.triangle_count() for p in self.parts)


def _noise(p, k: float) -> float:
    """A smooth, deterministic bump field for the fur's lumps."""
    x, y, z = p
    return (math.sin(x * 31.0 + k) * math.sin(y * 27.0 + 2 * k) * math.sin(z * 29.0 + 3 * k)
            + 0.5 * math.sin(x * 57.0 - y * 41.0 + z * 13.0 + k))


def _sphere(m: Mesh, c, r: float, zone: int, seg: int = 8, rings: int = 6, scale=(1.0, 1.0, 1.0)) -> tuple[int, int]:
    """A lathe sphere centred on `c`, squashed by `scale` (x, y, z); returns its mark."""
    mark = m.mark()
    prof = [(r * math.sin(math.pi * i / rings), -r * math.cos(math.pi * i / rings)) for i in range(rings + 1)]
    mk.lathe(m, prof, seg, "fur", zone, cap_top=False)
    sx, sy, sz = scale
    m.transform(mark, lambda p: (c[0] + p[0] * sx, c[1] + p[1] * sy, c[2] + p[2] * sz))
    return mark


def _cone(m: Mesh, base, tip, r: float, zone: int, seg: int = 4) -> None:
    d = mk.sub(tip, base)
    side = mk.normalize(mk.cross(d, (0.0, 1.0, 0.0)) if abs(d[1]) < 0.9 * mk.length(d) else mk.cross(d, (1.0, 0.0, 0.0)))
    up = mk.normalize(mk.cross(side, d))
    ring = []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        ring.append(mk.add(base, mk.add(mk.scale(side, r * math.cos(a)), mk.scale(up, r * math.sin(a)))))
    for i in range(seg):
        tri = [ring[i], ring[(i + 1) % seg], tip]
        if mk.dot(mk.newell(tri), mk.sub(mk.scale(mk.add(ring[i], ring[(i + 1) % seg]), 0.5), base)) < 0:
            tri.reverse()
        m.poly(tri, "fur", zone=zone)


def _tube(m: Mesh, pts, radii, zone: int, seg: int = 6, flat: float = 1.0) -> None:
    """A continuous tapering tube through `pts`, which lie in the y-z plane
    (rings across the curve; `flat` > 1 widens it in x into a ribbon);
    both ends capped."""
    rings = []
    for i, p in enumerate(pts):
        a, b = pts[max(0, i - 1)], pts[min(len(pts) - 1, i + 1)]
        t = mk.normalize(mk.sub(b, a))
        n2 = mk.normalize(mk.cross(t, (1.0, 0.0, 0.0)))
        r = radii[i]
        rings.append([mk.add(p, mk.add((r * flat * math.cos(2 * math.pi * k / seg), 0.0, 0.0),
                                       mk.scale(n2, r * math.sin(2 * math.pi * k / seg)))) for k in range(seg)])
    g = m.group()
    for i in range(len(rings) - 1):
        for k in range(seg):
            q = [rings[i][k], rings[i][(k + 1) % seg], rings[i + 1][(k + 1) % seg], rings[i + 1][k]]
            out = mk.sub(mk.scale(mk.add(q[0], q[2]), 0.5), mk.scale(mk.add(pts[i], pts[i + 1]), 0.5))
            if mk.dot(mk.newell(q), out) < 0:
                q.reverse()
            m.poly(q, "fur", zone=zone, group=g, smooth=True)
    for ring, end, away in ((rings[0], pts[0], mk.sub(pts[0], pts[1])), (rings[-1], pts[-1], mk.sub(pts[-1], pts[-2]))):
        cap = list(ring)
        if mk.dot(mk.newell(cap), away) < 0:
            cap.reverse()
        m.poly(cap, "fur", zone=zone)


# -- the Fluffbell -----------------------------------------------------------------------------


def fluffbell() -> Beast:
    m = Mesh()
    r, cy = 0.2, 0.2
    mark = _sphere(m, (0.0, cy, 0.0), r, 1, seg=12, rings=8)
    # Lumpy fluff: push the vertices in and out a little (not the base, so it sits flat).
    m.transform(mark, lambda p: (p[0] * (1 + 0.07 * _noise(p, 1.0)), max(0.0, cy + (p[1] - cy) * (1 + 0.06 * _noise(p, 2.0))),
                                 p[2] * (1 + 0.07 * _noise(p, 1.0))))
    # Darker clumps of fluff all round (not on the face): squashed balls half out of the body.
    for i in range(22):
        a = 2.39996 * i  # golden angle
        y = 0.95 - 1.6 * (i + 0.5) / 22
        rr = math.sqrt(max(0.0, 1 - y * y))
        d = (rr * math.sin(a), y, rr * math.cos(a))
        if d[2] > 0.5 and d[1] > -0.35:
            continue  # keep the face clear
        c = (d[0] * r * 0.97, cy + d[1] * r * 0.97, d[2] * r * 0.97)
        cm = _sphere(m, (0.0, 0.0, 0.0), 0.05, 2, seg=6, rings=3, scale=(1.0, 0.6, 1.0))
        up = d
        side = mk.normalize(mk.cross(up, (0.0, 0.0, 1.0)) if abs(up[2]) < 0.9 else mk.cross(up, (1.0, 0.0, 0.0)))
        fwd = mk.cross(side, up)
        m.transform(cm, lambda q, c=c, up=up, side=side, fwd=fwd: mk.add(c, mk.add(mk.add(mk.scale(side, q[0]),
                                                                                       mk.scale(up, q[1])),
                                                                               mk.scale(fwd, q[2]))))
    # Two round black eyes with a white glint, high on the front.
    for sx in (-1.0, 1.0):
        e = (sx * 0.068, cy + 0.07, 0.172)
        _sphere(m, e, 0.03, 4, seg=8, rings=4)
        _sphere(m, (e[0] + 0.008, e[1] + 0.012, e[2] + 0.026), 0.008, 5, seg=4, rings=2)
    body = Part("beast_puff", m)
    # The tongue: a long pink curl from the mouth, its node's origin at the root.
    t = Mesh()
    pts = [(0.0, 0.0, -0.01), (0.0, -0.02, 0.05), (0.0, -0.05, 0.1), (0.0, -0.068, 0.15), (0.0, -0.064, 0.19),
           (0.0, -0.042, 0.214), (0.0, -0.018, 0.21), (0.0, -0.008, 0.19)]
    radii = [0.024 - 0.0017 * i for i in range(len(pts))]
    _tube(t, pts, radii, 3, seg=6, flat=1.5)
    tongue = Part("beast_puff_tongue", t, (0.0, cy - 0.05, r * 0.92))
    return Beast("beast_puff", PUFF_ZONES, [body, tongue])


# -- the Lynxkit -------------------------------------------------------------------------------


def lynxkit() -> Beast:
    m = Mesh()
    # The body: an egg along z, speckled (every few faces in the spot colour).
    mark = _sphere(m, (0.0, 0.27, -0.03), 1.0, 1, seg=10, rings=7, scale=(0.13, 0.12, 0.23))
    for i, f in enumerate(m.faces[mark[1]:]):
        if (i * 7 + 3) % 5 == 0:
            f.zone = 2
    # A pale chest under the head.
    _sphere(m, (0.0, 0.28, 0.14), 1.0, 3, seg=8, rings=5, scale=(0.09, 0.1, 0.07))
    # Four legs and pale paws.
    for sx in (-1.0, 1.0):
        for z in (0.13, -0.17):
            x = sx * 0.075
            mk.rod(m, (x, 0.04, z), (x * 1.05, 0.26, z), 0.032, 6, "fur", 1, caps=False)
            _sphere(m, (x, 0.03, z + 0.015), 1.0, 3, seg=6, rings=3, scale=(0.04, 0.03, 0.05))
    # The head: big and round, a pale muzzle, a pink nose.
    hc = (0.0, 0.42, 0.2)
    _sphere(m, hc, 0.115, 1, seg=10, rings=6, scale=(1.08, 0.95, 1.0))
    _sphere(m, (0.0, 0.39, 0.3), 1.0, 3, seg=8, rings=4, scale=(0.065, 0.045, 0.04))
    _sphere(m, (0.0, 0.415, 0.335), 0.014, 4, seg=6, rings=3)
    # Amber eyes with slit pupils and a glint.
    for sx in (-1.0, 1.0):
        e = (sx * 0.048, 0.445, 0.305)
        _sphere(m, e, 1.0, 5, seg=8, rings=4, scale=(0.026, 0.03, 0.014))
        _sphere(m, (e[0], e[1], e[2] + 0.011), 1.0, 6, seg=4, rings=2, scale=(0.006, 0.022, 0.006))
        _sphere(m, (e[0] + 0.009, e[1] + 0.012, e[2] + 0.013), 0.004, 8, seg=4, rings=2)
    # Large tufted ears: an outer triangle in fur, a pink inner, a dark tuft at the tip.
    for sx in (-1.0, 1.0):
        base_out, base_in = (sx * 0.105, 0.48, 0.19), (sx * 0.03, 0.52, 0.2)
        tip = (sx * 0.1, 0.63, 0.17)
        back = (sx * 0.07, 0.5, 0.15)
        for tri, zone in (([base_in, base_out, tip], 1), ([base_out, back, tip], 1), ([back, base_in, tip], 1)):
            n = mk.newell(tri)
            centre = mk.scale(mk.add(mk.add(tri[0], tri[1]), tri[2]), 1 / 3)
            if mk.dot(n, mk.sub(centre, (sx * 0.07, 0.53, 0.18))) < 0:
                tri = list(reversed(tri))
            m.poly(tri, "fur", zone=zone)
        inner = [mk.lerp(base_in, base_out, 0.18), mk.lerp(base_in, base_out, 0.82), mk.lerp(mk.lerp(base_in, base_out, 0.5), tip, 0.8)]
        inner = [mk.add(p, (0.0, 0.0, 0.004)) for p in inner]
        if mk.newell(inner)[2] < 0:
            inner.reverse()
        m.poly(inner, "fur", zone=4)
        _cone(m, mk.lerp(mk.lerp(base_in, base_out, 0.5), tip, 0.82), mk.add(tip, (sx * 0.008, 0.05, -0.01)), 0.016, 7,
              seg=4)
    # Whisker pads' whiskers: thin pale strokes either side.
    for sx in (-1.0, 1.0):
        for dy in (-0.008, 0.008):
            a, b = (sx * 0.04, 0.39 + dy, 0.315), (sx * 0.13, 0.39 + dy * 3, 0.3)
            mk.rod(m, a, b, 0.002, 3, "fur", 8, caps=False)
    body = Part("beast_lynxkit", m)
    # The tail: up and over in a gentle S, a lion-like tuft at its end.
    t = Mesh()
    pts = [(0.0, 0.0, 0.0), (0.0, 0.05, -0.1), (0.0, 0.14, -0.17), (0.0, 0.25, -0.19), (0.0, 0.33, -0.15)]
    _tube(t, pts, [0.03, 0.026, 0.022, 0.02, 0.018], 1)
    tm = t.mark()
    _sphere(t, (0.0, 0.37, -0.13), 1.0, 7, seg=8, rings=5, scale=(0.05, 0.065, 0.05))
    t.transform(tm, lambda p: (p[0] * (1 + 0.12 * _noise(p, 3.0)), p[1], p[2] * (1 + 0.12 * _noise(p, 4.0))))
    tail = Part("beast_lynxkit_tail", t, (0.0, 0.3, -0.24))
    return Beast("beast_lynxkit", LYNXKIT_ZONES, [body, tail])


BEASTS = {"beast_puff": fluffbell, "beast_lynxkit": lynxkit}


def build(pid: str) -> Beast:
    return BEASTS[pid]()


def swatch_colours(beast: Beast, names: dict[str, str]) -> list[str]:
    """The swatch's colours, one per zone in zone order (castle_textures.json palette by name)."""
    return [names[beast.zones[z]] for z in sorted(beast.zones)]


def zone_uv(beast: Beast, zone: int) -> tuple[float, float]:
    import paint

    return paint.swatch_uv(sorted(beast.zones).index(zone), SWATCH_GRID)


def blender_objects(pid: str):
    """The prop as Blender objects: the body (root) and its child, one
    material (the swatch texture). Runs inside Blender (build_school.py)."""
    import bpy  # noqa: F401

    import bpyutil
    import paint
    from castle_kit import props_atlas

    beast = build(pid)
    names = props_atlas.palette()
    img = bpyutil.image_from_array(f"{pid}_swatch", paint.swatches(SWATCH_PX, swatch_colours(beast, names), SWATCH_GRID))
    mat = bpyutil.principled(pid, None, img, 0.85)
    tex = next(n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE")
    tex.interpolation = "Closest"
    objs = []
    for part in beast.parts:
        uvs = [[zone_uv(beast, f.zone)] * len(f.idx) for f in part.mesh.faces]
        o = bpyutil.mesh_object(part.name, part.mesh, {"fur": mat}, uvs=uvs)
        o.location = bpyutil.to_blender(part.origin)
        objs.append(o)
    for child in objs[1:]:
        child.parent = objs[0]
    return objs
