"""The Flying Pitch's resident cell, `pitch`, as plain
geometry, and its lint (docs/architecture.md). Pure Python, no `bpy`:
`pitch_export.py` turns the result into Blender objects for greybox.py's
outdoor hook; the tests run `build` and `lint` on it.

Everything is placed from `data/world/terrain/grounds.json` `pitch` (written by
terrain_layout.py) and the layout's surfaces and areas:

* the field: an oval of turf (`castle_floor_grass`, world UVs, the grounds'
  own turf texture) FIELD_LIFT over the terrace, mown in stripes across the
  pitch (a lighter and a darker COLOR_0 grey in turn), and its markings in
  palette white (`letter_white` on the props atlas's fabric tile) LINE_LIFT
  over the terrace: the boundary, the halfway line and centre circle, and a
  keeper's line before each end's hoops. The terrain under it stays the
  Terrain export's;
* the six hoops (`kit_pitch_hoop`, gilt) at `pitch.hoops`;
* the four spectator towers (`kit_pitch_tower`) at `pitch.towers`, draped in
  their house's banners (house_a..house_d);
* the two covered stands (`kit_pitch_stand`) on `pitch.stands` with their
  steps, and three rows of oak benches (`kit_bench_<len>`) on each deck,
  exported as the manifest's `seats` rows;
* the Pitch Gate (`kit_pitch_gate`) at `pitch.gate.prop`.

One mesh per material (`castle_floor_grass`, `castle_props`, `castle_wood`,
`castle_roof`, `castle_cloth`), each with a `_lod1` drawn beyond
grid.P3A_GROUNDS_LOD (the benches, the gate, the railings, the braces and the
stripes drop out; the hoops are lighter). COLOR_0 is a plain grey, darker at
the pieces' foot (grounds_build's shade), the turf's stripes and the lines
their own.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
for _p in (BLENDER, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import grounds_build as GB  # noqa: E402
import grounds_terrain as GT  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402
from castle_kit.pieces import hall_furniture as HF  # noqa: E402

CELL = G.P3B_PITCH_CELL
TURF = "castle_floor_grass"
LINE_TAG = G.palette_tag("fabric", "letter_white")
FIELD_LIFT = 0.01  # the turf over the terrace (at most 0.02: the contract)
LINE_LIFT = 0.02  # the markings over the terrace
LINE_W = 0.2
STRIPE_M = 4.8  # the mown stripes' width across the pitch (x)
STRIPE_GREYS = (1.0, 0.9)
LINE_GREY = 0.96
SEG = 96  # segments of the boundary's ellipse
CENTRE_R = 4.6
KEEPER_LINE = 13.0  # a keeper's line this far in from each end of the field
BENCH_LEN = 4.8  # 1.2 m between neighbours on the 6 m pitch: a way through from row to row (sim radius 0.3)
BENCH_PITCH = 6.0
BENCH_ROWS = (-1.0, -2.3, -3.6)  # the benches' middles behind the deck's front edge (local z)
LOD1_SUFFIX = "_lod1"


@dataclass
class Placed:
    id: str
    piece: str
    args: dict
    pos: tuple
    yaw: float
    lod: str  # "split" (the piece's own LOD1), "both" (the same far), "near" (dropped beyond the LOD distance)


@dataclass
class Pitch:
    cell: str
    meshes: dict  # node -> MeshData
    lod_meshes: dict  # node_lod1 -> (MeshData, node)
    seats: list  # [x0, z0, x1, z1, y, yaw, front, back, drop]
    placed: list
    faces: list = field(default_factory=list, repr=False)  # LOD0 WFaces (the lint's)
    lights: list = field(default_factory=list)

    def draws(self) -> int:
        return len(self.meshes)

    def tris(self) -> int:
        return sum(m.triangles() for m in self.meshes.values())

    def lod1_tris(self) -> int:
        return sum(m.triangles() for m, _ in self.lod_meshes.values())

    def bounds(self):
        pts = [v for m in self.meshes.values() for v in m.verts]
        lo = [round(min(p[k] for p in pts), 3) for k in range(3)]
        hi = [round(max(p[k] for p in pts), 3) for k in range(3)]
        return lo, hi

    def library(self) -> list[str]:
        return sorted({m.material for m in self.meshes.values() if m.material.startswith("castle_")})

    def digest(self) -> str:
        """A fingerprint of the drawn geometry, its LOD1, shade and seats (the determinism test)."""
        h = hashlib.sha1()
        for name in sorted(self.meshes) + sorted(self.lod_meshes):
            m = self.meshes.get(name) or self.lod_meshes[name][0]
            h.update(name.encode())
            h.update(json.dumps([[round(c, 6) for c in v] for v in m.verts]).encode())
            h.update(json.dumps([list(f) for f in m.faces]).encode())
            h.update(json.dumps([[[round(a, 6), round(b, 6)] for a, b in uv] for uv in m.uvs]).encode())
            h.update(json.dumps([[[round(c, 4) for c in col] for col in face] for face in m.colors]).encode())
        h.update(json.dumps(self.seats).encode())
        return h.hexdigest()[:16]


# -- placements ---------------------------------------------------------------------------------


def _local(pos, yaw: float, p) -> tuple:
    return geom.Placement(tuple(pos), yaw).point(p)


def placements(meta: dict) -> tuple[list[Placed], list]:
    """The pieces of the pitch (grounds.json `pitch`) and the benches' seat rows."""
    y = float(meta["y"])
    out: list[Placed] = []
    seats = []
    for st in meta["stands"]:
        x0, z0, x1, z1 = st["deck"]
        flights = st["steps"]  # [x0, z0, x1, z1] each: east of the deck, west of it, or both
        deck = float(st["y"]) - y
        north = abs(st["seats_face"]) < 1e-6  # the field to +z
        length, depth = x1 - x0, z1 - z0
        steps = flights[0][2] - flights[0][0]
        east = any(f[0] >= x1 - 1e-6 for f in flights)
        west = any(f[2] <= x0 + 1e-6 for f in flights)
        ends = 0 if east and west else (1 if east else -1)  # in world x: +1 east, -1 west, 0 both
        if north:
            pos, yaw, steps_at = (x0, y, z1), 0.0, ends
        else:
            pos, yaw, steps_at = (x1, y, z0), math.pi, -ends
        out.append(Placed(st["id"], "kit_pitch_stand", {"length": length, "depth": depth, "deck": deck,
                                                        "roof": float(st["roof"]), "steps": steps,
                                                        "steps_at": steps_at}, pos, yaw, "split"))
        n = max(1, int(length // BENCH_PITCH))
        gap = (length - n * BENCH_PITCH) / 2.0
        name = f"kit_bench_{BENCH_LEN:g}"
        for r, bz in enumerate(BENCH_ROWS):
            for i in range(n):
                bx = gap + (i + 0.5) * BENCH_PITCH
                at = _local(pos, yaw, (bx, deck, bz))
                out.append(Placed(f"{st['id']}_bench_{r}_{i}", name, {}, at, yaw, "near"))
                a = _local(pos, yaw, (bx - BENCH_LEN / 2, deck, bz - HF.BENCH_DEPTH / 2))
                b = _local(pos, yaw, (bx + BENCH_LEN / 2, deck, bz + HF.BENCH_DEPTH / 2))
                seats.append([round(min(a[0], b[0]), 3), round(min(a[2], b[2]), 3), round(max(a[0], b[0]), 3),
                              round(max(a[2], b[2]), 3), round(st["y"] + HF.BENCH_SEAT, 3),
                              round(float(st["seats_face"]), 7), HF.BENCH_DEPTH / 2, HF.BENCH_DEPTH / 2, HF.BENCH_SEAT])
    for i, t in enumerate(meta["towers"]):
        cx, cz = t["centre"]
        house = int(t["house"].rsplit("_", 1)[1], 36) - 10  # house_a..d -> 0..3
        out.append(Placed(t["id"], "kit_pitch_tower", {"house": house, "size": float(t["size"]),
                                                       "height": float(t["top"]) - y}, (cx, y, cz), 0.0, "split"))
    for h in meta["hoops"]:
        x, hy, z = h["pos"]
        out.append(Placed(h["id"], "kit_pitch_hoop", {"height": round(hy - y, 4), "radius": float(h["radius"])},
                          (x, y, z), float(h["yaw"]), "split"))
    gt = meta["gate"]
    out.append(Placed(gt["id"], "kit_pitch_gate", {}, tuple(gt["prop"]), float(gt["yaw"]), "near"))
    return out, seats


# -- the field ------------------------------------------------------------------------------------


def _ellipse(meta: dict):
    x0, z0, x1, z1 = meta["field"]
    return (x0 + x1) / 2.0, (z0 + z1) / 2.0, (x1 - x0) / 2.0, (z1 - z0) / 2.0


def _half_height(a: float, b: float, dx: float) -> float:
    return b * math.sqrt(max(0.0, 1.0 - (dx / a) ** 2))


def turf_bands(meta: dict) -> list[tuple[list, int]]:
    """The oval split into stripes across x: (outline (x, z) CCW seen from above, stripe index)."""
    cx, cz, a, b = _ellipse(meta)
    n = max(2, round(2 * a / STRIPE_M))
    w = 2 * a / n
    out = []
    for i in range(n):
        xa, xb = cx - a + i * w, cx - a + (i + 1) * w
        k = 8
        xs = [xa + (xb - xa) * j / k for j in range(k + 1)]
        lower = [(x, cz - _half_height(a, b, x - cx)) for x in xs]
        upper = [(x, cz + _half_height(a, b, x - cx)) for x in reversed(xs)]
        pts = lower + upper
        out.append((pts, i))
    return out


def _strip(path, width: float, closed: bool) -> list[list]:
    """Quads of a line `width` wide along a polyline of (x, z) points."""
    quads = []
    n = len(path)
    hw = width / 2.0

    def normal(i):
        a = path[(i - 1) % n] if (closed or i > 0) else path[i]
        b = path[(i + 1) % n] if (closed or i < n - 1) else path[i]
        dx, dz = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dz) or 1.0
        return -dz / ln, dx / ln

    norms = [normal(i) for i in range(n)]
    last = n if closed else n - 1
    for i in range(last):
        j = (i + 1) % n
        (ax, az), (bx, bz) = path[i], path[j]
        (nax, naz), (nbx, nbz) = norms[i], norms[j]
        quads.append([(ax - nax * hw, az - naz * hw), (bx - nbx * hw, bz - nbz * hw), (bx + nbx * hw, bz + nbz * hw),
                      (ax + nax * hw, az + naz * hw)])
    return quads


def line_quads(meta: dict) -> list[list]:
    """The markings (x, z quads): the boundary just inside the oval, the halfway
    line, the centre circle, a keeper's line before each end."""
    cx, cz, a, b = _ellipse(meta)
    inset = 0.35
    ring = [(cx + (a - inset) * math.cos(2 * math.pi * i / SEG), cz + (b - inset) * math.sin(2 * math.pi * i / SEG))
            for i in range(SEG)]
    out = _strip(ring, LINE_W, True)
    circle = [(cx + CENTRE_R * math.cos(2 * math.pi * i / 40), cz + CENTRE_R * math.sin(2 * math.pi * i / 40))
              for i in range(40)]
    out += _strip(circle, LINE_W, True)
    for x in (cx, cx - a + KEEPER_LINE, cx + a - KEEPER_LINE):
        hh = _half_height(a - inset, b - inset, x - cx) - LINE_W / 2
        if x == cx:  # the halfway line stops at the centre circle
            for za, zb in ((cz - hh, cz - CENTRE_R - LINE_W / 2), (cz + CENTRE_R + LINE_W / 2, cz + hh)):
                out += _strip([(x, za), (x, zb)], LINE_W, False)
        else:
            out += _strip([(x, cz - hh), (x, cz + hh)], LINE_W, False)
    return out


# -- the build ------------------------------------------------------------------------------------


def _shade(g: GT.Grounds, f: A.WFace) -> list:
    if f.src.startswith("turf"):
        v = STRIPE_GREYS[int(f.src.partition(":")[2] or 0) % 2]
        return [(v, v, v)] * len(f.pts)
    if f.src == "lines":
        return [(LINE_GREY,) * 3] * len(f.pts)
    return GB._shade(g, f)


def _merge(faces: list, g: GT.Grounds) -> tuple[dict, dict]:
    """One mesh per material and its `_lod1` (always written: every node has one)."""
    by: dict[str, list] = {}
    for f in faces:
        by.setdefault(f.material, []).append(f)
    meshes, lods = {}, {}
    for mat in sorted(by):
        near = [f for f in by[mat] if f.lod in ("both", "lod0")]
        far = [f for f in by[mat] if f.lod in ("both", "lod1")]
        name = G.node_name(CELL, mat)
        md = A.Builder._mesh(name, mat, near, (0.0, 1.0))
        md.colors = [_shade(g, f) for f in near]
        meshes[name] = md
        lname = name + LOD1_SUFFIX
        lm = A.Builder._mesh(lname, mat, far, (0.0, 1.0))
        lm.colors = [_shade(g, f) for f in far]
        lods[lname] = (lm, name)
    return meshes, lods


def build(layout, terrain_dir: str, textures: dict, meta: dict | None = None, props_path: str = PA.PROPS_PATH,
          textures_path: str = PA.TEXTURES_PATH) -> Pitch:
    g = GT.Grounds.load(terrain_dir, layout)
    meta = meta or g.meta["pitch"]
    _, atlas = PA.build(props_path, textures_path)
    uv = GB._Uv(textures, atlas)
    placed, seats = placements(meta)
    faces: list[A.WFace] = []
    k = 0
    for p in placed:
        piece = P.build(p.piece, **p.args)
        pf = uv.piece_faces(piece, geom.Placement(p.pos, p.yaw), GB.MATERIALS, p.id, lod_split=(p.lod == "split"), k0=k)
        k += sum(1 for f in pf if f.mode == "kit")
        if p.lod == "near":
            for f in pf:
                f.lod = "lod0"
        faces += pf
    # The turf: striped bands (LOD0), one plain oval (LOD1).
    y = float(meta["y"])
    for pts, i in turf_bands(meta):
        w = [(x, y + FIELD_LIFT, z) for x, z in pts]
        faces.append(uv.face(w, f"{TURF}|world", None, 0.0, f"turf:{i}", {}, k, False, "lod0"))
    cx, cz, a, b = _ellipse(meta)
    oval = [(cx + a * math.cos(2 * math.pi * i / 48), y + FIELD_LIFT, cz - b * math.sin(2 * math.pi * i / 48))
            for i in range(48)]
    if geom.newell(oval)[1] < 0:
        oval.reverse()
    for tri in geom.ear_clip(oval) if not geom.is_convex(oval) else [tuple(range(len(oval)))]:
        faces.append(uv.face([oval[t] for t in tri], f"{TURF}|world", None, 0.0, "turf", {}, k, False, "lod1"))
    for q in line_quads(meta):
        w = [(x, y + LINE_LIFT, z) for x, z in q]
        if geom.newell(w)[1] < 0:
            w.reverse()
        faces.append(uv.face(w, LINE_TAG, None, 0.0, "lines", {}, k, False, "both"))
        k += 1
    meshes, lods = _merge(faces, g)
    return Pitch(CELL, meshes, lods, seats, placed, [f for f in faces if f.lod in ("both", "lod0")])


# -- the lint -------------------------------------------------------------------------------------


def _grey_ok(colors) -> bool:
    return all(abs(c[0] - c[1]) < 1e-6 and abs(c[1] - c[2]) < 1e-6 and 0.0 <= c[0] <= 1.0 for face in colors for c in face)


def _inside(p, box, tol: float = 1e-6) -> bool:
    (x0, y0, z0), (x1, y1, z1) = box
    return x0 - tol <= p[0] <= x1 + tol and y0 - tol <= p[1] <= y1 + tol and z0 - tol <= p[2] <= z1 + tol


def _areas(terrain_dir: str) -> list[dict]:
    """The layout's areas (layout_lib keeps only the solids): data/world/layout.json next to the terrain."""
    with open(os.path.join(os.path.dirname(terrain_dir), "layout.json"), encoding="utf-8") as f:
        return json.load(f).get("areas", [])


def lint(pt: Pitch, layout, terrain_dir: str) -> tuple[list, list, list]:
    """(errors, warnings, report lines): the budgets (grid P3B_PITCH_*), every node's
    LOD1, greyscale COLOR_0, the turf and lines on the terrace within 2 cm, the
    stands and towers inside their no-fly boxes, the decks' tops on the layout's
    decks and the seats on them."""
    errs, warns, rep = [], [], []
    g = GT.Grounds.load(terrain_dir, layout)
    b = G.P3B_PITCH_BUDGET
    d, t, t1 = pt.draws(), pt.tris(), pt.lod1_tris()
    rep.append(f"{pt.cell:16s} {d} draws {t:6d} tris  LOD1 {t1:5d} tris  ({', '.join(sorted(pt.meshes))})")
    if d > b.draws:
        errs.append(f"{pt.cell}: {d} draws (at most {b.draws})")
    if t > b.tris[1]:
        errs.append(f"{pt.cell}: {t} triangles at LOD0 (at most {b.tris[1]})")
    if t < b.tris[0]:
        warns.append(f"{pt.cell}: {t} triangles at LOD0 (the guideline's low end is {b.tris[0]})")
    if t1 > G.P3B_PITCH_LOD1_TRIS:
        errs.append(f"{pt.cell}: {t1} triangles at LOD1 (at most {G.P3B_PITCH_LOD1_TRIS})")
    for name in pt.meshes:
        if name + LOD1_SUFFIX not in pt.lod_meshes:
            errs.append(f"{name}: no {LOD1_SUFFIX} node")
    for md in list(pt.meshes.values()) + [lm for lm, _ in pt.lod_meshes.values()]:
        if md.colors is None or not _grey_ok(md.colors):
            errs.append(f"{md.name}: COLOR_0 is not greyscale in 0..1")
        if not md.material.startswith("castle_"):
            errs.append(f"{md.name}: not a castle_* material")
    for f in pt.faces:
        if f.src.startswith("turf") or f.src == "lines":
            for x, y, z in f.pts:
                lift = y - g.height(x, z)
                if not 0.0 < lift <= 0.02 + 1e-9:
                    errs.append(f"{f.src}: drawn {lift:.3f} m over the terrace at ({x:.1f}, {z:.1f})")
                    break
    areas = {a["id"]: a for a in _areas(terrain_dir)}
    for p in pt.placed:
        if p.piece in ("kit_pitch_tower", "kit_pitch_stand"):
            box = areas.get(f"nofly_{p.id}")
            if box is None:
                errs.append(f"{p.id}: no no-fly box nofly_{p.id} in the layout")
                continue
            lim = (tuple(box["min"]), tuple(box["max"]))
            for f in pt.faces:
                if f.src == p.id and not all(_inside(q, lim) for q in f.pts):
                    errs.append(f"{p.id}: drawn outside its no-fly box")
                    break
    decks = {s.id: s for s in layout.surfaces if s.cell == pt.cell and s.kind != "stair"}
    for sid, s in decks.items():
        tops = [f for f in pt.faces if f.src == sid and f.normal()[1] > 0.99
                and all(s.x0 - 1e-6 <= q[0] <= s.x1 + 1e-6 and s.z0 - 1e-6 <= q[2] <= s.z1 + 1e-6 for q in f.pts)]
        area = sum(f.area() for f in tops if all(abs(q[1] - s.y) < 1e-6 for q in f.pts))
        if abs(area - (s.x1 - s.x0) * (s.z1 - s.z0)) > 1e-3:
            errs.append(f"{sid}: the deck's top covers {area:.2f} m2 at y {s.y}, not the surface's")
    for row in pt.seats:
        x0, z0, x1, z1, y, *_ = row
        if not any(s.x0 <= x0 and x1 <= s.x1 and s.z0 <= z0 and z1 <= s.z1 and abs(s.y + HF.BENCH_SEAT - y) < 1e-6
                   for s in decks.values()):
            errs.append(f"seat {row}: not on a deck")
    rep.append(f"{'':16s} {len(pt.seats)} seats; {len(pt.placed)} pieces")
    return errs, warns, rep
