"""Placement rules: what the kit puts where in a kitted cell, derived from
the layout and the resolved castle_kit.json (docs/art/castle-kit.md
"Placement rules"). Pure Python, deterministic: identical
inputs give byte-identical placements (`to_json`).

From each kitted cell's wall runs and surfaces:

- a plinth sweep along every wall run at the floor, broken at shaft bases,
  blind doors and wall-mounted find-spot tells (grid.WALL_TELLS, their
  footprint plus grid.TELL_GAP each side) and capped where it stops;
- a cornice under the ceiling along every side, across door headers whose
  arch (rim and keystone) stays below it, mitred at inside corners;
- a string course with its top at the window sill along runs that have
  windows, broken at shafts, niches and blind doors;
- a shaft on every inner bay boundary (schema shafts), a ceiling rib
  between shafts that face each other across the room;
- quoins at outside corners (joins to other floors) and an arch surround at
  every door edge (schema arches; the arch piece carries the jamb quoins);
- the openings and lanterns of castle_kit.json, its hero placements;
- a threshold across every door surface of the cell, a floor border round
  each floor (`floor_border`), a balustrade with newels along every railing
  run, a generated flight per stair surface.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_BLENDER, os.path.join(_BLENDER, "world")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import layout_lib as L  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit.pieces import openings  # noqa: E402

BORDER = 0.6  # floor border band width
NEWEL_HALF = 0.225
CORNER_INSET = 0.26  # A balustrade's end at a jutting corner (its rail is 0.25 m deep)
RIB_MATCH = 0.05  # shafts this close across a room carry a rib
LOD_DISTANCE = 30.0  # balustrades switch to their painted LOD1 beyond this
STRING_MIN = 1.2  # string course pieces shorter than this are left out


@dataclass(frozen=True)
class Use:
    """One placed piece: `piece` built with `params` (sorted (name, value)
    pairs), its frame at `origin` turned by `yaw`."""

    id: str
    role: str
    piece: str
    origin: tuple[float, float, float]
    yaw: float
    params: tuple = ()
    run: str | None = None
    lod_distance: float | None = None
    # Castle kit M3 + M4 (docs/art/castle-kit.md): the named group its
    # faces merge into (`<cell>.<material>.<group>`: a level band `l<k>`, a
    # candle group `candles_<k>`); None = the material's main mesh. The
    # group's LOD and extras come from the cell plan's `groups`.
    group: str | None = None

    def kwargs(self) -> dict:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.params}


def _p(**kw) -> tuple:
    def frz(v):
        if isinstance(v, (list, tuple)):
            return tuple(frz(x) for x in v)
        if isinstance(v, float):
            return round(v, 6) + 0.0
        return v

    return tuple(sorted((k, frz(v)) for k, v in kw.items()))


def _r(v: float) -> float:
    return round(v, 6) + 0.0


def _o(p) -> tuple[float, float, float]:
    return (_r(p[0]), _r(p[1]), _r(p[2]))


# -- frames -------------------------------------------------------------------------


def yaw_for(axis: str, sign: float) -> float:
    """Yaw whose local +z is the face normal (`sign` along `axis`)."""
    if axis == "z":
        return 0.0 if sign > 0 else math.pi
    return math.pi / 2 if sign > 0 else -math.pi / 2


def tdir(axis: str, sign: float) -> float:
    """+1 when the frame's local x runs with t (the run coordinate), else -1."""
    if axis == "z":
        return 1.0 if sign > 0 else -1.0
    return -1.0 if sign > 0 else 1.0


def face_point(axis: str, c: float, sign: float, t: float, y: float, d: float = 0.0):
    """World point at t along the line axis = c, height y, d in front of the face."""
    off = c + sign * d
    return (t, y, off) if axis == "z" else (off, y, t)


@dataclass
class Context:
    layout: L.Layout
    cp: S.CellPlan
    railings: dict = field(default_factory=dict)  # Planner.railings
    tells: list = field(default_factory=list)  # (kit id, model, pos, yaw)
    solid_stairs: dict = field(default_factory=dict)  # stair id -> rests on the floor
    pieces: list = field(default_factory=list)  # the Planner's wall pieces of the cell (WallPiece)

    def walled(self, run: G.WallRun, at: str = "floor") -> list[tuple[float, float]]:
        """Spans of a run where the Planner draws its wall: from the floor
        (`at` "floor"), up to the wall top ("top"), or both ("full"). A run
        along a drop (a railing edge) or open to a lower ceiling has none."""
        spans = []
        for pc in self.pieces:
            if pc.owner.id != run.surface or pc.axis != run.axis or abs(pc.c - run.c) > 1e-6 or pc.sign != run.sign:
                continue
            if at in ("floor", "full") and abs(pc.lo.mid - run.floor) > 0.05:
                continue
            if at in ("top", "full") and abs(pc.hi.mid - run.top) > 0.05:
                continue
            a, b = max(pc.ta, run.t0), min(pc.tb, run.t1)
            if b > a + 1e-6:
                spans.append((a, b))
        spans.sort()
        out: list[list[float]] = []
        for a, b in spans:
            if out and a <= out[-1][1] + 1e-6:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return [(a, b) for a, b in out]


def context(layout: L.Layout, cp: S.CellPlan, kit_entries=(), open_edges=()) -> Context:
    pl = L.Planner(layout, [], open_edges)
    pl.build_walls()
    rail = {k: [list(r) for r in v] for k, v in pl.railings.items() if layout.by_id(k[0]).cell == cp.cell
            and k[0] not in set(open_edges)}
    tells = [(k.id, k.model, k.pos, k.yaw) for k in kit_entries if k.cell == cp.cell and k.model in G.WALL_TELLS
             and k.id not in cp.replaces]
    solid = {s.id: pl._stair_is_solid(s) for s in layout.surfaces if s.cell == cp.cell and s.kind == "stair" and s.sloped}
    pieces = [pc for pc in pl.plan.pieces if pc.owner.cell == cp.cell]
    ctx = Context(layout, cp, rail, tells, solid, pieces)
    ctx.kit_kept = [k for k in kit_entries if k.cell == cp.cell and k.id not in cp.replaces]  # castle kit M3
    return ctx


# -- intervals ----------------------------------------------------------------------------


def _subtract(span, holes):
    """span (a, b) minus holes [(a, b, end kind)], as [(a, b, end at a, end at b)]
    where an end is None (the span's own end) or the hole's kind."""
    a, b = span
    out = []
    cur, cur_kind = a, None
    for h0, h1, kind in sorted(holes):
        if h1 <= cur + 1e-6 or h0 >= b - 1e-6:
            continue
        if h0 > cur + 1e-6:
            out.append((cur, h0, cur_kind, kind))
        cur, cur_kind = max(cur, h1), kind
    if b > cur + 1e-6:
        out.append((cur, b, cur_kind, None))
    return out


@dataclass(frozen=True)
class Line:
    """A wall line: collinear wall runs of the cell's surfaces meeting end to
    end at one floor height (a corridor side is one run; the Entrance
    Hall's north wall is three surfaces' runs). Sweeps follow lines."""

    id: str  # its first run's id
    axis: str
    c: float
    sign: float
    t0: float
    t1: float
    floor: float
    top: float
    runs: tuple

    @property
    def surface(self) -> str:
        return self.runs[0].surface

    @property
    def side(self) -> str:
        return self.runs[0].side


def wall_lines(ctx: Context) -> list[Line]:
    groups: dict = {}
    for r in ctx.cp.runs:
        groups.setdefault((r.axis, round(r.c, 6), r.sign, round(r.floor, 6)), []).append(r)
    out = []
    for key in sorted(groups):
        rs = sorted(groups[key], key=lambda r: r.t0)
        cur = [rs[0]]
        for r in rs[1:]:
            if abs(r.t0 - cur[-1].t1) < 1e-6:
                cur.append(r)
            else:
                out.append(_line_of(cur))
                cur = [r]
        out.append(_line_of(cur))
    return out


def _line_of(rs) -> Line:
    r0 = rs[0]
    return Line(r0.id, r0.axis, r0.c, r0.sign, r0.t0, rs[-1].t1, r0.floor, max(r.top for r in rs), tuple(rs))


def line_walled(ctx: Context, ln, at: str = "floor") -> list[tuple[float, float]]:
    """ctx.walled over every run of a line (or of a single run)."""
    spans = sorted(sp for r in getattr(ln, "runs", (ln,)) for sp in ctx.walled(r, at))
    out: list[list[float]] = []
    for a, b in spans:
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _same_line(o, ln) -> bool:
    return o.axis == ln.axis and abs(o.c - ln.c) < 1e-6 and o.sign == ln.sign


def _corner_run(ctx: Context, run, t: float):
    """The perpendicular wall run meeting `run` (a run or a line) at its end
    t in an inside corner, or None."""
    d_line = 1.0 if abs(t - run.t0) < 1e-6 else -1.0
    for r in ctx.cp.runs:
        if r.axis == run.axis or abs(r.c - t) > 1e-6:
            continue
        if abs(r.t0 - run.c) < 1e-6:
            d_r = 1.0
        elif abs(r.t1 - run.c) < 1e-6:
            d_r = -1.0
        else:
            continue
        if d_r * run.sign > 0 and d_line * r.sign > 0:
            return r
    return None


def _corner_end(ctx: Context, run, t: float) -> str:
    """'corner' when the run (or line) ends at its surface's corner with a
    wall run of the perpendicular side starting there (an inside corner),
    'free' at a door or join, 'open' at a corner without a perpendicular
    wall."""
    rs = getattr(run, "runs", (run,))
    owner = next((r for r in rs if abs(r.t0 - t) < 1e-6 or abs(r.t1 - t) < 1e-6), rs[0])
    s = ctx.layout.by_id(owner.surface)
    _, _, _, (a, b) = G.side_line(s, owner.side)
    if abs(t - a) > 1e-6 and abs(t - b) > 1e-6:
        return "free"
    return "corner" if _corner_run(ctx, run, t) is not None else "open"


def _sweep_use(uid, role, piece, run: G.WallRun, t0, t1, y, end0, end1, extra=None) -> Use:
    """A sweep along a run from t0 to t1 (world), ends as given at t0 / t1."""
    d = tdir(run.axis, run.sign)
    start = t0 if d > 0 else t1
    ends = (end0, end1) if d > 0 else (end1, end0)
    origin = face_point(run.axis, run.c, run.sign, start, y)
    return Use(uid, role, piece, _o(origin), yaw_for(run.axis, run.sign),
               _p(length=t1 - t0, ends=list(ends), **(extra or {})), run.id)


# -- the rules ---------------------------------------------------------------------------


def placements(ctx: Context) -> list[Use]:
    cp = ctx.cp
    uses: list[Use] = []
    runs = {r.id: r for r in cp.runs}
    shafts_by_run: dict[str, list] = {}
    for sh in cp.shafts:
        if _on_wall(ctx.walled(runs[sh.run], "full"), sh.t, 0.43):
            shafts_by_run.setdefault(sh.run, []).append(sh)
    ops_by_run: dict[str, list] = {}
    for op in cp.openings:
        if op.run:
            ops_by_run.setdefault(op.run, []).append(op)

    # Shafts (where the wall stands floor to ceiling) and their lanterns.
    shafts = [sh for sh in cp.shafts if _on_wall(ctx.walled(runs[sh.run], "full"), sh.t, 0.43)]
    kept = {sh.id for sh in shafts}
    for sh in shafts:
        r = runs[sh.run]
        uses.append(Use(f"shaft:{sh.id}", "shaft", sh.piece, _o(face_point(r.axis, r.c, r.sign, sh.t, r.floor)),
                        yaw_for(r.axis, r.sign), (), r.id))
    proud = G.SHAFT.proud
    for lt in cp.lanterns:
        if lt.shaft not in kept:
            continue
        r = runs[lt.run]
        uses.append(Use(f"lantern:{lt.id}", "lantern", lt.piece, _o(face_point(r.axis, r.c, r.sign, lt.t, lt.y, proud)),
                        yaw_for(r.axis, r.sign), (), r.id))

    # Openings.
    for op in cp.openings:
        o = face_point(op.axis, op.c, op.sign, op.centre, op.y0)
        yaw = yaw_for(op.axis, op.sign)
        if op.kind == "arch":
            uses.append(Use(f"arch:{op.id}", "arch", op.piece, _o(o), yaw, _p(reveal=op.reveal, depth=op.depth), None))
            door = ctx.layout.by_id(op.door)
            if op.leaves:
                # Castle kit M2 (the open-leaf rule): the cell whose arch has
                # the leaves builds them, in the reveal or on its wall face.
                w, h = op.width, door.ceiling
                uses.append(Use(f"leaves:{op.id}", "leaves", G.piece_name("door", "oak", w, h, "open"), _o(o), yaw,
                                _p(depth=op.depth, mode=op.leaf_mode, reveal=op.reveal), None))
        elif op.kind == "window":
            depth = op.wall
            if depth is None:
                run = runs[op.run]
                depth = G.wall_mass(ctx.layout, run, op.t0, op.t1, op.y0, op.y1).thickness
            extra = {} if _shell_cut(ctx, op) else {"outer": False}
            uses.append(Use(f"window:{op.id}", "window", op.piece, _o(o), yaw, _p(depth=depth, **extra), op.run))
        else:
            uses.append(Use(f"{op.kind}:{op.id}", op.kind, op.piece, _o(o), yaw, (), op.run))

    # Plinth, string course: along each wall line (collinear runs of the
    # cell's surfaces that meet end to end: the halls' floors are several
    # surfaces side by side).
    hero_walls = _hero_wall_spans(ctx)
    for ln in wall_lines(ctx):
        sh = [s for r in ln.runs for s in shafts_by_run.get(r.id, [])]
        ops = [o for r in ln.runs for o in ops_by_run.get(r.id, [])]
        uses += _plinth(ctx, ln, sh, ops, hero_walls)
        uses += _string(ctx, ln, sh, ops)
    if cp.springing is None:
        uses += _cornices(ctx)
        uses += _ribs(ctx, runs)
    else:
        # Vaulted (castle kit M2): the cornice at the springing line, no
        # flat-ceiling ribs (the vault heroes carry theirs), corbels where
        # the vault's lines land on no shaft.
        uses += _vault_cornices(ctx)
        uses += _vault_corbels(ctx, shafts)
    uses += _outside_quoins(ctx)
    uses += _floors(ctx)
    uses += _dedupe_newels(_balustrades(ctx), _hero_newels(ctx))
    uses += _flights(ctx)
    uses = great_hall_m3(ctx, uses)  # Castle kit M3 (the Great Hall): lines, wainscot, plate cornice, candles, keep_clear
    uses = m4_staircase(ctx, uses)  # Castle kit M4 (the Grand Staircase): docs/art/castle-kit.md "Portraits and tapestries"
    uses = m5a_corridors(ctx, uses)  # Castle M5a (Corridors): docs/art/castle-kit.md
    for h in cp.hero:
        # Castle M5b (Furniture): a ring chandelier hangs in its candle group (schema hero_groups, 26.4).
        uses.append(Use(f"hero:{h.id}", "hero", h.piece, _o(h.pos), _r(h.yaw), h.args, None, None, cp.hero_groups.get(h.id)))
    uses = m5a_tower(ctx, uses)  # Castle M5a (Tower): docs/art/castle-kit.md "The kitted cells", "Pieces"
    uses = m5b_dungeons(ctx, uses)  # Castle M5b (Dungeons): docs/art/castle-kit.md "The kitted cells", "Pieces"
    uses = m5b_upper(ctx, uses)  # Castle M5b (Upper rooms): docs/art/castle-kit.md "The kitted cells"
    uses = m5c_facades(ctx, uses)  # Castle M5c (Facades): docs/art/castle-kit.md "Exterior"
    uses = m5c_courtyard(ctx, uses)  # Castle M5c (Courtyard): docs/art/castle-kit.md "The kitted cells"
    uses = p3c_wings_a(ctx, uses)  # Docs/art/castle-kit.md
    uses = p3d_secrets(ctx, uses)  # Docs/art/castle-kit.md
    uses.sort(key=lambda u: u.id)
    ids = [u.id for u in uses]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{cp.cell}: duplicate placement ids")
    return uses


def _shell_cut(ctx: Context, op) -> bool:
    """Does the Planner cut a through opening from the wall's outer shell
    face? Not where another surface lies within one wall mass behind it
    (layout_lib Planner.build_shell: the shell there shows only above that
    room's walls), e.g. the courtyard beyond the front doors' wall."""
    other = "z" if op.axis == "x" else "x"
    s0 = ctx.layout.by_id(op.surface)
    lo, hi = s0.floor_min, G.wall_top(s0) + G.SLAB
    for s in ctx.layout.surfaces:
        if s.id == op.surface:
            continue
        e0, e1 = s.extent(op.axis)
        d = (e0 - op.c) if op.sign < 0 else (op.c - e1)
        if d <= L.EPS or d > G.WALL_MASS_MAX:
            continue
        o0, o1 = s.extent(other)
        if not (o0 <= op.centre <= o1):
            continue
        if s.floor_min - G.SLAB >= hi or s.struct_top() <= lo:
            continue
        return False
    return True


def _on_wall(spans, t: float, half: float) -> bool:
    return any(a - 1e-6 <= t - half and t + half <= b + 1e-6 for a, b in spans)


def _gaps(run: G.WallRun, spans, kind: str = "cap"):
    """The parts of a run without wall, as holes for _subtract."""
    holes, t = [], run.t0
    for a, b in spans:
        if a > t + 1e-6:
            holes.append((t, a, kind))
        t = max(t, b)
    if run.t1 > t + 1e-6:
        holes.append((t, run.t1, kind))
    return holes


def _tell_spans(ctx: Context, r: G.WallRun):
    """Wall tells on this run's face: (t0, t1, y0, y1) with the tell gap."""
    import kit_models as km

    out = []
    for kid, model, pos, yaw in ctx.tells:
        t = pos[0] if r.axis == "z" else pos[2]
        d = ((pos[2] if r.axis == "z" else pos[0]) - r.c) * r.sign
        if not (r.t0 - 1e-6 <= t <= r.t1 + 1e-6) or abs(d) > G.WALL_TOUCH:
            continue
        lo, hi = km.build(model).mesh.bounds()
        half = G.TELL_HALF_WIDTH + G.TELL_GAP
        out.append((t - half, t + half, pos[1] + lo[1], pos[1] + hi[1]))
    return out


def _plinth(ctx: Context, r, shafts, ops, hero_walls=()) -> list[Use]:
    holes = _gaps(r, line_walled(ctx, r, "floor"))
    for sh in shafts:
        holes.append((sh.t - 0.43, sh.t + 0.43, "shaft"))
    for op in ops:
        if op.kind == "blind_door":
            holes.append((op.t0 - 0.3, op.t1 + 0.3, "cap"))
        elif op.y0 - r.floor < G.PLINTH.height - 1e-6:
            holes.append((op.t0 - 0.25, op.t1 + 0.25, "cap"))
    for t0, t1, _, _ in _tell_spans(ctx, r):
        holes.append((t0, t1, "cap"))
    # Castle kit M2: open leaves lying on this wall face, hero pieces against it (the counter).
    for t0, t1 in _leaf_spans(ctx, r):
        holes.append((t0 - 0.05, t1 + 0.05, "cap"))
    for axis, c, sign, t0, t1, y0, _ in hero_walls:
        if axis == r.axis and abs(c - r.c) < 1e-6 and sign == r.sign and y0 - r.floor < G.PLINTH.height:
            holes.append((t0 - G.TELL_GAP, t1 + G.TELL_GAP, "cap"))
    out = []
    for i, (a, b, ka, kb) in enumerate(_subtract((r.t0, r.t1), holes)):
        ea = _end(ctx, r, a, ka, "plinth")
        eb = _end(ctx, r, b, kb, "plinth")
        out.append(_sweep_use(f"plinth:{r.id}:{i}", "plinth", "kit_stone_plinth", r, a, b, r.floor, ea, eb))
    return out


def _leaf_spans(ctx: Context, ln) -> list[tuple[float, float]]:
    """Where open leaves lie flat on this line's wall face (the open-leaf
    rule's `face` mode, castle kit M2)."""
    out = []
    for op in ctx.cp.openings:
        if op.kind != "arch" or op.leaf_mode != "face" or not _same_line(op, ln):
            continue
        w = op.leaf_width
        out += [(op.t0 - G.ARCH_SURROUND - w, op.t0 - G.ARCH_SURROUND), (op.t1 + G.ARCH_SURROUND, op.t1 + G.ARCH_SURROUND + w)]
    return out


def _hero_wall_spans(ctx: Context) -> list:
    """(axis, c, sign, t0, t1, y0, y1) of hero pieces standing against a wall
    face of the cell (their piece's meta "wall": local x and y extent)."""
    from castle_kit import pieces as P

    out = []
    for h in ctx.cp.hero:
        if h.piece.startswith("kit_vault_"):
            continue
        try:
            meta = P.build(h.piece, **dict(h.args)).meta
        except KeyError:
            continue
        if "wall" not in meta:
            continue
        x0, x1, y0, y1 = meta["wall"]
        for r in ctx.cp.runs:
            t = h.pos[0] if r.axis == "z" else h.pos[2]
            d = ((h.pos[2] if r.axis == "z" else h.pos[0]) - r.c) * r.sign
            if abs(d) > G.WALL_TOUCH or abs(_r(h.yaw) - _r(yaw_for(r.axis, r.sign))) > 1e-3:
                continue
            k = tdir(r.axis, r.sign)
            ta, tb = sorted((t + k * x0, t + k * x1))
            out.append((r.axis, r.c, r.sign, ta, tb, h.pos[1] + y0, h.pos[1] + y1))
            break
    return out


def _end(ctx, r, t, kind, what) -> str:
    """End mode of a sweep at t: at the run's own end (kind None) a mitre
    into an inside corner, else a cap; at a hole, hidden ends ('none') inside
    a shaft, caps elsewhere."""
    if kind is None:
        e = _corner_end(ctx, r, t)
        if e == "corner":
            return "miter"
        if what == "string" and e != "free":
            return "none"
        return "cap"
    return "none" if kind == "shaft" else "cap"


def _string(ctx: Context, r, shafts, ops) -> list[Use]:
    wins = [op for op in ops if op.kind == "window"]
    if not wins:
        return []
    sill = min(op.y0 for op in wins)
    y0, y1 = sill - G.STRING.height, sill
    holes = _gaps(r, line_walled(ctx, r, "floor")) + [(sh.t - 0.35, sh.t + 0.35, "shaft") for sh in shafts]
    for op in ops:
        if op.kind in ("niche", "blind_door", "fireplace") and op.y0 < y1 and op.y1 > y0:
            band = 0.3 if op.kind in ("blind_door", "fireplace") else 0.22  # animated fire: no string across a firebox
            holes.append((op.t0 - band - 0.05, op.t1 + band + 0.05, "cap"))
    for t0, t1, ty0, ty1 in _tell_spans(ctx, r):
        if ty0 < y1 and ty1 > y0:
            holes.append((t0, t1, "cap"))
    # The quoins of arches on this line (jamb quoins run from the plinth to
    # the springing, 0.8 m at most from the jamb), and open leaves on it.
    for op in ctx.cp.openings:
        if op.kind == "arch" and _same_line(op, r):
            spring = G.arch_springing(op.width, op.y1 - op.y0) + op.y0
            if spring > y0:
                holes += [(op.t0 - 0.85, op.t0 + 1e-3, "cap"), (op.t1 - 1e-3, op.t1 + 0.85, "cap")]
    for t0, t1 in _leaf_spans(ctx, r):
        holes.append((t0 - 0.05, t1 + 0.05, "cap"))
    out = []
    for i, (a, b, ka, kb) in enumerate(_subtract((r.t0, r.t1), holes)):
        if b - a < STRING_MIN:
            continue  # a stub between two breaks reads as a stray block
        ea, eb = _end(ctx, r, a, ka, "string"), _end(ctx, r, b, kb, "string")
        # Only mitre into a corner whose other run carries a string too.
        if ea == "miter" and not _string_at_corner(ctx, r, a, sill):
            ea = "none"
        if eb == "miter" and not _string_at_corner(ctx, r, b, sill):
            eb = "none"
        out.append(_sweep_use(f"string:{r.id}:{i}", "string", "kit_stone_string", r, a, b, y0, ea, eb))
    return out


def _string_at_corner(ctx, r, t, sill) -> bool:
    cr = _corner_run(ctx, r, t)
    if cr is None:
        return False
    return any(o.kind == "window" and o.run and _same_line(o, cr) and abs(o.y0 - sill) < 1e-6 for o in ctx.cp.openings)


def _cornices(ctx: Context) -> list[Use]:
    """Per surface side: the runs, joined across door contacts whose arch
    stays below the cornice."""
    cp = ctx.cp
    out = []
    by_side: dict[tuple[str, str], list] = {}
    for r in cp.runs:
        by_side.setdefault((r.surface, r.side), []).append(r)
    spans_by_side = {}
    for (sid, side), rs in sorted(by_side.items()):
        rs.sort(key=lambda r: r.t0)
        s = ctx.layout.by_id(sid)
        if s.kind == "grounds":
            continue  # A grounds floor's low wall (1 m, a parapet's breast) takes no cornice
        top = rs[0].top
        # The wall reaching the ceiling along the whole side (headers over
        # doors included), without door headers too low for the arch.
        axis, c, sign, (a, b) = G.side_line(s, side)
        whole = G.WallRun(f"{sid}:{side}*", sid, s.cell, side, 0, axis, c, sign, a, b, s.y, top)
        spans = [list(x) for x in ctx.walled(whole, "top")]
        holes = []
        for j in G.contacts(ctx.layout, s):
            if j.side != side or not j.is_door:
                continue
            door = ctx.layout.by_id(j.other)
            if door.ceiling is None:
                continue
            arch = openings.arch(j.t1 - j.t0, door.ceiling, rim=True, quoins=False)
            if door.y + arch.meta["outer_apex"] > top - G.CORNICE.height - 1e-6:
                holes.append((j.t0, j.t1, "cap"))
        merged = []
        for x0, x1 in spans:
            for p0, p1, _, _ in _subtract((x0, x1), holes):
                merged.append([p0, p1])
        if merged:
            spans_by_side[(sid, side)] = (merged, rs[0], top)
    for (sid, side), (merged, r0, top) in sorted(spans_by_side.items()):
        s = ctx.layout.by_id(sid)
        _, _, _, (a, b) = G.side_line(s, side)
        for i, (t0, t1) in enumerate(merged):
            ends = []
            for t in (t0, t1):
                at_corner = abs(t - a) < 1e-6 or abs(t - b) < 1e-6
                if not at_corner:
                    ends.append("cap")
                    continue
                perp = ("w" if abs(t - a) < 1e-6 else "e") if side in ("n", "s") else ("n" if abs(t - a) < 1e-6 else "s")
                pm = spans_by_side.get((sid, perp))
                corner_t = r0.c
                ok = pm is not None and any(abs(x - corner_t) < 1e-6 for span in pm[0] for x in span)
                ends.append("miter" if ok else "cap")
            out.append(_sweep_use(f"cornice:{sid}:{side}:{i}", "cornice", "kit_stone_cornice", r0, t0, t1, top,
                                  ends[0], ends[1]))
    return out


def _face_spans(ctx: Context, axis: str, c: float, sign: float, y0: float, y1: float) -> list[tuple[float, float]]:
    """Spans of a face line where the cell's Planner wall is flat and covers
    y0..y1 (door headers included)."""
    spans = []
    for pc in ctx.pieces:
        if pc.axis != axis or abs(pc.c - c) > 1e-6 or pc.sign != sign:
            continue
        if abs(pc.lo.a - pc.lo.b) > 1e-6 or abs(pc.hi.a - pc.hi.b) > 1e-6:
            continue
        if pc.lo.mid > y0 + 0.05 or pc.hi.mid < y1 - 0.05:
            continue
        spans.append((pc.ta, pc.tb))
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _opening_reach(op) -> tuple[float, float]:
    """(half width on the wall face, top) of an opening's surround: an
    arch's hood mould, a window's frame (castle kit pieces)."""
    if op.kind == "arch":
        meta = openings.arch(op.width, op.y1 - op.y0, rim=True, quoins=False).meta
        return meta["outer_half"], op.y0 + meta["outer_apex"]
    if op.piece.startswith("kit_window_tracery"):
        return op.width / 2 + 0.32, op.y1 + 0.32
    return op.width / 2 + 0.26, op.y1 + 0.26


def _vault_cornices(ctx: Context) -> list[Use]:
    """Castle kit M2: in a vaulted cell the cornice runs at the springing
    line (its top at floor + springing) wherever the Planner's wall stands
    there, broken round openings and arch hoods that rise into it, mitred
    at inside corners."""
    cp = ctx.cp
    lines: dict = {}
    for s in sorted((s for s in ctx.layout.surfaces if s.cell == cp.cell and s.kind != "door" and not s.sloped),
                    key=lambda s: s.id):
        top = s.y + cp.springing
        for side in G.SIDES:
            axis, c, sign, _ = G.side_line(s, side)
            key = (axis, round(c, 6), sign, round(top, 6))
            if key in lines:
                continue
            spans = _face_spans(ctx, axis, c, sign, top - G.CORNICE.height, top)
            if spans:
                lines[key] = spans
    segs: dict = {}
    for (axis, c, sign, top), spans in sorted(lines.items()):
        holes = []
        for op in cp.openings:
            if op.axis != axis or abs(op.c - c) > 1e-6 or op.sign != sign:
                continue
            half, reach = _opening_reach(op)
            if reach > top - G.CORNICE.height - 0.05:
                holes.append((op.centre - half - 0.1, op.centre + half + 0.1, "cap"))
        parts = [(a, b) for x0, x1 in spans for a, b, _, _ in _subtract((x0, x1), holes) if b - a > 0.3]
        if parts:
            segs[(axis, c, sign, top)] = parts
    out = []
    for (axis, c, sign, top), parts in sorted(segs.items()):
        for i, (t0, t1) in enumerate(parts):
            ends = []
            for t, d_line in ((t0, 1.0), (t1, -1.0)):
                corner = False
                for (ax2, c2, sg2, top2), parts2 in segs.items():
                    if ax2 == axis or abs(c2 - t) > 1e-6 or abs(top2 - top) > 1e-6:
                        continue
                    for a2, b2 in parts2:
                        d2 = 1.0 if abs(a2 - c) < 1e-6 else (-1.0 if abs(b2 - c) < 1e-6 else 0.0)
                        if d2 * sign > 0 and d_line * sg2 > 0:
                            corner = True
                ends.append("miter" if corner else "cap")
            ln = Line(f"{axis}{_r(c)}{'+' if sign > 0 else '-'}", axis, c, sign, t0, t1, top - cp.springing, top, ())
            out.append(_sweep_use(f"cornice:{ln.id}:{i}", "cornice", "kit_stone_cornice", ln, t0, t1, top, ends[0], ends[1]))
    return out


def _vault_corbels(ctx: Context, shafts) -> list[Use]:
    """Castle kit M2: a corbel under the springing cornice wherever a vault
    hero's line meets a wall with no shaft to carry it: the lierne bays'
    transverse lines on the side walls, the fan vault's springers on its
    end wall (not in corners, not into an opening's surround)."""
    from castle_kit import pieces as P

    cp = ctx.cp
    runs = {r.id: r for r in cp.runs}
    spring_y = None
    spots = {}
    for h in cp.hero:
        if not h.piece.startswith("kit_vault_"):
            continue
        fam, w, d, _ = P.parse(h.piece)
        if abs(math.sin(h.yaw)) > 1e-6:
            raise ValueError(f"{cp.cell}: vault {h.id} must run along z (yaw 0 or pi)")
        hx, hz = w / 2.0, d / 2.0
        if fam == "kit_vault_lierne_bay":
            pts = [("x", h.pos[0] + sx * hx, h.pos[2] + sz * hz, -sx) for sx in (-1.0, 1.0) for sz in (-1.0, 1.0)]
        elif fam == "kit_vault_fan":
            pts = [("z", h.pos[2] - hz * math.cos(h.yaw), h.pos[0] + sx * hx, math.cos(h.yaw)) for sx in (-1.0, 1.0)]
        else:
            continue
        spring_y = h.pos[1]
        for axis, c, t, sign in pts:
            spots[(axis, round(c, 6), round(t, 6), sign)] = (axis, c, t, sign)
    out = []
    if spring_y is None:
        return out
    top = spring_y - G.CORNICE.height
    for key in sorted(spots):
        axis, c, t, sign = spots[key]
        if not any(a + 0.3 <= t <= b - 0.3 for a, b in _face_spans(ctx, axis, c, sign, top - 0.7, spring_y)):
            continue  # no wall there, or a corner
        if any(_same_line(runs[sh.run], _Face(axis, c, sign)) and abs(sh.t - t) < 0.6 for sh in shafts):
            continue  # a shaft carries the line
        clash = False
        for op in cp.openings:
            if op.axis != axis or abs(op.c - c) > 1e-6 or op.sign != sign:
                continue
            half, reach = _opening_reach(op)
            if reach > top - 0.7 and op.centre - half - 0.4 < t < op.centre + half + 0.4:
                clash = True
        if clash:
            continue
        o = face_point(axis, c, sign, t, top)
        out.append(Use(f"corbel:vault:{axis}{_r(c)}:{_r(t)}", "corbel", "kit_stone_corbel_0.6", _o(o), yaw_for(axis, sign),
                       (), None))
    return out


@dataclass(frozen=True)
class _Face:
    axis: str
    c: float
    sign: float


def _ribs(ctx: Context, runs) -> list[Use]:
    """Ceiling ribs on flat ceilings with bay shafts: a transverse rib from
    every shaft of the side with the most shafts across to the opposite
    wall (landing on a shaft there, or on a corbel under the cornice when
    the wall is clear), and in every bay between those ribs (and the end
    walls) diagonal ribs meeting at a boss."""
    out = []
    cp = ctx.cp
    by_surface: dict[str, dict[str, list]] = {}
    for sh in cp.shafts:
        r = runs[sh.run]
        by_surface.setdefault(r.surface, {}).setdefault(r.side, []).append((sh, r))
    for sid, sides in sorted(by_surface.items()):
        s = ctx.layout.by_id(sid)
        if not s.roofed or s.kind == "door":
            continue
        pair = ("n", "s") if (s.x1 - s.x0) >= (s.z1 - s.z0) else ("w", "e")
        prim = max(pair, key=lambda k: (len(sides.get(k, [])), -pair.index(k)))
        other = pair[1] if prim == pair[0] else pair[0]
        if not sides.get(prim):
            continue
        axis, c_p, sign_p, (ea, eb) = G.side_line(s, prim)
        _, c_o, sign_o, _ = G.side_line(s, other)
        top = s.ceil_y
        lines = sorted(sh.t for sh, _ in sides[prim])
        opp = [o.t for o, _ in sides.get(other, [])]

        def pt(t, c):
            return (t, top, c) if axis == "z" else (c, top, t)

        for k, t in enumerate(lines):
            out.append(_rib_use(f"rib:{sid}:{k}", pt(t, c_p), pt(t, c_o), ("none", "none"), sid))
            if any(abs(o - t) <= RIB_MATCH for o in opp):
                continue
            if _corbel_clear(ctx, s, other, t, opp, top):
                o = face_point(axis, c_o, sign_o, t, top - G.CORNICE.height)
                out.append(Use(f"corbel:{sid}:{k}", "corbel", "kit_stone_corbel_0.6", _o(o), yaw_for(axis, sign_o), (),
                               sid))
        bounds = [ea] + lines + [eb]
        for k, (ta, tb) in enumerate(zip(bounds, bounds[1:])):
            if tb - ta < 3.0:
                continue
            tm, cm = (ta + tb) / 2, (c_p + c_o) / 2
            centre = pt(tm, cm)
            for j, (t, c) in enumerate(((ta, c_p), (tb, c_p), (ta, c_o), (tb, c_o))):
                a = pt(t, c)
                d = (centre[0] - a[0], centre[2] - a[2])
                ln = math.hypot(*d)
                u = (d[0] / ln, d[1] / ln)
                start = (a[0] + u[0] * DIAG_WALL, top, a[2] + u[1] * DIAG_WALL)
                end = (centre[0] - u[0] * DIAG_BOSS, top, centre[2] - u[1] * DIAG_BOSS)
                out.append(_rib_use(f"diagonal:{sid}:{k}:{j}", start, end, ("cap", "cap"), sid))
            out.append(Use(f"boss:{sid}:{k}", "boss", "kit_stone_boss", _o((centre[0], top, centre[2])), 0.0,
                           _p(size=0.9), sid))
    return out


DIAG_WALL = 0.45  # diagonal ribs start this far from the wall corner (inside the cornice)
DIAG_BOSS = 0.3  # and stop this far short of the boss's centre


def _rib_use(uid, a, b, ends, sid) -> Use:
    dx, dz = b[0] - a[0], b[2] - a[2]
    length = math.hypot(dx, dz)
    yaw = math.atan2(-dz, dx)
    return Use(uid, "rib", "kit_stone_rib", _o(a), _r(yaw), _p(length=length, ends=list(ends)), sid)


def _corbel_clear(ctx: Context, s: L.Surface, side: str, t: float, shafts, top: float) -> bool:
    """A corbel at t on `side` under the cornice: on a wall, clear of
    shafts, arches and opening surrounds there."""
    half = 0.25
    on_wall = any(r.surface == s.id and r.side == side and r.t0 + half <= t <= r.t1 - half for r in ctx.cp.runs)
    if not on_wall:
        return False
    if any(abs(o - t) < 1.0 for o in shafts):
        return False
    bottom = top - G.CORNICE.height - 0.6
    for op in ctx.cp.openings:
        if op.surface != s.id:
            continue
        line = G.side_line(s, side)
        if op.axis != line[0] or abs(op.c - line[1]) > 1e-6:
            continue
        reach = 1.1 if op.kind != "arch" else 0.9
        if op.t0 - reach - half < t < op.t1 + reach + half and op.y1 + 0.5 > bottom:
            return False
    return True


def _outside_quoins(ctx: Context) -> list[Use]:
    """Corner quoins where a run ends at a join to another floor (not a
    door, not the surface's own corner): the wall turns away there."""
    out = []
    for r in ctx.cp.runs:
        s = ctx.layout.by_id(r.surface)
        joins = [j for j in G.contacts(ctx.layout, s) if j.side == r.side and not j.is_door]
        walls = ctx.walled(r, "full")
        for t, hand in ((r.t0, -1), (r.t1, 1)):
            if not any(abs(j.t0 - t) < 1e-6 or abs(j.t1 - t) < 1e-6 for j in joins):
                continue
            if not any(a - 1e-6 <= t <= b + 1e-6 for a, b in walls):
                continue  # no wall at this end (a railing edge)
            d = tdir(r.axis, r.sign)
            # Frame x along the run, toward the corner when hand * d > 0.
            h = hand * (1 if d > 0 else -1)
            o = face_point(r.axis, r.c, r.sign, t, r.floor)
            out.append(Use(f"quoin:{r.id}:{'a' if hand < 0 else 'b'}", "quoin", "kit_stone_quoin", _o(o),
                           yaw_for(r.axis, r.sign), _p(y0=G.PLINTH.height, y1=r.top - r.floor - G.CORNICE.height,
                                                        corner=True, hand=-h), r.id))
    return out


def _floors(ctx: Context) -> list[Use]:
    """Floor borders and thresholds."""
    out = []
    cp = ctx.cp
    for s in sorted((s for s in ctx.layout.surfaces if s.cell == cp.cell), key=lambda s: s.id):
        if s.kind == "door":
            for j in _door_contacts(ctx, s):
                o_s, side, t0, t1 = j
                axis, c, sign, _ = G.side_line(o_s, side)
                depth = (s.x1 - s.x0) if axis == "x" else (s.z1 - s.z0)
                o = face_point(axis, c, sign, (t0 + t1) / 2, s.y)
                out.append(Use(f"threshold:{s.id}", "threshold", G.piece_name("threshold", None, t1 - t0), _o(o),
                               yaw_for(axis, sign), _p(depth=depth), None))
                break
            continue
        if s.sloped or not cp.floor_border:
            continue
        rects = border_rects(ctx, s)
        if rects:
            out.append(Use(f"floor_border:{s.id}", "floor_border", "kit_floor_border", (0.0, 0.0, 0.0), 0.0,
                           _p(rects=[list(r) for r in rects], y=s.y), None))
    return out


def _door_contacts(ctx: Context, door: L.Surface):
    """(surface of a kitted... any cell, side, t0, t1) where `door` joins a
    flat surface across that surface's edge, nearest first in id order."""
    out = []
    for o in sorted(ctx.layout.surfaces, key=lambda s: (s.cell != ctx.cp.cell, s.id)):
        if o.kind == "door" or o.sloped or o.id == door.id:
            continue
        for j in G.contacts(ctx.layout, o):
            if j.other == door.id:
                out.append((o, j.side, j.t0, j.t1))
    return out


def border_rects(ctx: Context, s: L.Surface) -> list[tuple[float, float, float, float]]:
    """The floor border's bands: BORDER wide along every wall run of the
    surface (none across joins); n/s bands own the corner squares."""
    rects = []
    runs = [r for r in ctx.cp.runs if r.surface == s.id]
    spans = [(r, a, b) for r in sorted(runs, key=lambda r: (G.SIDES.index(r.side), r.t0)) for a, b in ctx.walled(r, "floor")]
    for r, a, b in spans:
        if r.side == "n":
            rects.append((a, s.z0, b, s.z0 + BORDER))
        elif r.side == "s":
            rects.append((a, s.z1 - BORDER, b, s.z1))
    ns = list(rects)
    for r, a, b in spans:
        if r.side == "w":
            band = (s.x0, a, s.x0 + BORDER, b)
        elif r.side == "e":
            band = (s.x1 - BORDER, a, s.x1, b)
        else:
            continue
        rects.extend(L.rect_subtract(band, ns))
    return [tuple(_r(v) for v in rc) for rc in rects if rc[2] - rc[0] > 1e-6 and rc[3] - rc[1] > 1e-6]


def _hero_newels(ctx: Context) -> list:
    """Hero newels (castle kit M2: the Marble Staircase's lamp newels): they
    replace the derived newel at the same spot and split the balustrade."""
    return [h for h in ctx.cp.hero if h.piece.startswith("kit_newel_")]


def _balustrades(ctx: Context) -> list[Use]:
    out = []
    heroes = _hero_newels(ctx)
    for (sid, axis, c, sign), runs in sorted(ctx.railings.items()):
        s = ctx.layout.by_id(sid)
        for i, (ta, tb) in enumerate(runs):
            d = tdir(axis, sign)
            lift = 0.0
            if s.kind == "stair":
                pl = L.Planner(ctx.layout)
                lift = pl._riser(s) / 2

            def floor_at(t):
                x, _, z = face_point(axis, c, sign, t, 0.0)
                return s.height(x, z) + lift

            # Split where a hero newel stands on this railing.
            cuts = sorted({_r(h.pos[2] if axis == "x" else h.pos[0]) for h in heroes
                           if abs((h.pos[0] if axis == "x" else h.pos[2]) - c) < 0.05
                           and ta + 0.5 < (h.pos[2] if axis == "x" else h.pos[0]) < tb - 0.5})
            bounds = [ta] + cuts + [tb]
            for j, (pa, pb) in enumerate(zip(bounds, bounds[1:])):
                start, end = (pa, pb) if d > 0 else (pb, pa)
                y0 = floor_at(start)
                rise = floor_at(end) - y0
                length = pb - pa
                suffix = f"{i}" if len(bounds) == 2 else f"{i}.{j}"
                # Newels at both ends; the parapet between them.
                inner = length - 2 * NEWEL_HALF
                if inner > 0.3:
                    o2 = face_point(axis, c, sign, start + d * NEWEL_HALF, floor_at(start + d * NEWEL_HALF))
                    out.append(Use(f"balustrade:{sid}:{axis}{_r(c)}:{suffix}", "balustrade", "kit_balustrade_gothic",
                                   _o(o2), yaw_for(axis, sign), _p(length=inner, rise=rise * inner / length), sid,
                                   LOD_DISTANCE))
                for k, t in enumerate((start, end)):
                    out.append(Use(f"newel:{sid}:{axis}{_r(c)}:{suffix}:{k}", "newel", "kit_newel_ball",
                                   _o(face_point(axis, c, sign, t, floor_at(t))), yaw_for(axis, sign), (), sid))
    return out


def _dedupe_newels(uses: list[Use], heroes=()) -> list[Use]:
    """One newel where two railings end together (a landing's corner); none
    where a hero newel stands."""
    out, seen = [], []
    for u in sorted(uses, key=lambda u: u.id):
        if u.role != "newel":
            out.append(u)
            continue
        if any(math.hypot(h.pos[0] - u.origin[0], h.pos[2] - u.origin[2]) < 0.35 and abs(h.pos[1] - u.origin[1]) < 1.0
               for h in heroes):
            continue
        twin = next((k for k, v in enumerate(seen) if math.hypot(v.origin[0] - u.origin[0], v.origin[2] - u.origin[2]) < 0.35),
                    None)
        if twin is None:
            seen.append(u)
            continue
        if u.origin[1] > seen[twin].origin[1]:
            seen[twin] = Use(seen[twin].id, "newel", seen[twin].piece, (seen[twin].origin[0], u.origin[1], seen[twin].origin[2]),
                             seen[twin].yaw, seen[twin].params, seen[twin].run)
    return out + seen


def _flights(ctx: Context) -> list[Use]:
    out = []
    for s in sorted((s for s in ctx.layout.surfaces if s.cell == ctx.cp.cell and s.kind == "stair" and s.sloped),
                    key=lambda s: s.id):
        rise = s.y_top - s.y
        if s.rise == "+z":
            origin, yaw, width, length = (s.x0, s.y, s.z0), 0.0, s.x1 - s.x0, s.z1 - s.z0
        elif s.rise == "-z":
            origin, yaw, width, length = (s.x1, s.y, s.z1), math.pi, s.x1 - s.x0, s.z1 - s.z0
        elif s.rise == "+x":
            origin, yaw, width, length = (s.x0, s.y, s.z1), math.pi / 2, s.z1 - s.z0, s.x1 - s.x0
        else:
            origin, yaw, width, length = (s.x1, s.y, s.z0), -math.pi / 2, s.z1 - s.z0, s.x1 - s.x0
        extra = {}
        steps = max(1, round(rise / G.RISER))
        if abs(length / steps - G.TREAD_MARBLE) < 0.015:
            extra["strip"] = "nosing_marble"  # castle kit M2: the Marble Staircase's treads
        out.append(Use(f"flight:{s.id}", "flight", "kit_stair_flight", _o(origin), _r(yaw),
                       _p(width=width, length=length, rise=rise, solid=bool(ctx.solid_stairs.get(s.id, True)), **extra),
                       s.id))
    return out


def to_json(uses: list[Use]) -> str:
    """Canonical text of a placement list (the byte-identity tests)."""
    rows = [{"id": u.id, "role": u.role, "piece": u.piece, "origin": list(u.origin), "yaw": _r(u.yaw),
             "params": [[k, v] for k, v in u.params], "run": u.run, "lod": u.lod_distance} for u in uses]
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))



# =====================================================================================
# Castle kit M3 (the Great Hall, docs/art/castle-kit.md). Cells without the M3
# fields (lines, wainscot, sky, candles, keep_clear) are untouched.
# =====================================================================================

WAINSCOT_CREST_Y = 1.75  # the crest panel's foot above the local floor (on the wainscot's upper tier)
WAINSCOT_ARCH_GAP = 0.85  # the wainscot stops this far beyond an arch's jambs (its quoins, grid.ARCH_SURROUND + 0.05)
WAINSCOT_FLOOR_GAP = 0.4  # ... and this far beyond a fireplace's or blind door's opening (their jambs and surrounds)
TRUSS_POST_GAP = 0.22  # the plate cornice breaks this far either side of a truss's wall post (its ends behind the post)
PLATE_GRAZE = 0.1  # an opening's surround reaching less than this into the plate cornice's underside does not break it


def great_hall_m3(ctx: Context, uses: list[Use]) -> list[Use]:
    """The M3 rules for a cell with bay lines, a wainscot, a sky, candle
    groups or areas kept clear: line shafts and their corbels, the oak
    wainscot (instead of the plinth) with a crest panel per bay, the wall
    plate's cornice at the springing (one level for the whole hall, beside
    the stair too, broken at openings and truss posts), the string course
    broken at the line shafts and at wall-hung props, the ridge beam under
    the sky's ridge, a moving group per candle entry, and nothing of the
    kit's own (flights, floor borders) inside a kept-clear area."""
    cp = ctx.cp
    if not (cp.lines or cp.wainscot or cp.sky or cp.candles or cp.keep_clear):
        return uses
    out = list(uses)
    if cp.keep_clear:
        out = _keep_clear(ctx, out)
    if cp.line_shafts:
        out += _line_shaft_uses(ctx)
        out = [u for u in out if u.role != "string"] + _strings_m3(ctx)
    if cp.wainscot:
        out = [u for u in out if u.role != "plinth"] + _wainscot(ctx)
    if cp.sky:
        out = [u for u in out if u.role != "cornice"] + _plate_cornices(ctx) + _ridge_beam(ctx)
    for cg in cp.candles:
        out.append(Use(f"candles:{cg.id}", "candles", "kit_candles_floating", (0.0, 0.0, 0.0), 0.0,
                       _p(rect=list(cg.rect), y0=cg.y0, y1=cg.y1, count=cg.count, seed=cg.seed), None, None, cg.id))
    return out


def _area_rect(area):
    _, lo, hi = area
    return (lo[0], lo[2], hi[0], hi[2])


def _keep_clear(ctx: Context, uses: list[Use]) -> list[Use]:
    """No flight on a stair inside a kept-clear area (the Dueling Club's
    stage steps are its props.json kit's), no floor border inside one."""
    areas = [_area_rect(a) for a in ctx.cp.keep_clear]
    out = []
    for u in uses:
        if u.role == "flight":
            s = ctx.layout.by_id(u.run)
            if any(a[0] <= s.x0 and s.x1 <= a[2] and a[1] <= s.z0 and s.z1 <= a[3] for a in areas):
                continue
        elif u.role == "floor_border":
            kw = u.kwargs()
            rects = [tuple(r) for r in kw["rects"]]
            kept = []
            for rc in rects:
                parts = [rc]
                for a in areas:
                    parts = [q for part in parts for q in L.rect_subtract(part, [a])]
                kept += [q for q in parts if q[2] - q[0] > 1e-6 and q[3] - q[1] > 1e-6]
            if not kept:
                continue
            if kept != rects:
                u = Use(u.id, u.role, u.piece, u.origin, u.yaw,
                        _p(rects=[[_r(v) for v in rc] for rc in kept], y=kw["y"]), u.run, u.lod_distance, u.group)
        out.append(u)
    return out


def _corbel_size(piece: str) -> float:
    return float(piece.rsplit("_", 1)[1])


def _line_shaft_uses(ctx: Context) -> list[Use]:
    """A plain shaft on every line shaft (from the wainscot's top to its
    corbel) and the corbel on its top; the shaft's capital stops under the
    corbel's moulded underside."""
    out = []
    for ls in ctx.cp.line_shafts:
        yaw = yaw_for(ls.axis, ls.sign)
        size = _corbel_size(ls.corbel)
        out.append(Use(f"line_shaft:{ls.id}", "line_shaft", ls.piece, _o(face_point(ls.axis, ls.c, ls.sign, ls.t, ls.y0)),
                       yaw, _p(top_clear=round(size * 0.9, 4)), ls.run))
        out.append(Use(f"line_corbel:{ls.id}", "corbel", ls.corbel, _o(face_point(ls.axis, ls.c, ls.sign, ls.t, ls.y1)),
                       yaw, (), ls.run))
    return out


def _cell_faces(ctx: Context) -> list[tuple[str, float, float]]:
    """(axis, c, sign) of every side of the cell's flat, non-door surfaces."""
    keys = set()
    for s in ctx.layout.surfaces:
        if s.cell != ctx.cp.cell or s.kind == "door" or s.sloped:
            continue
        for side in G.SIDES:
            axis, c, sign, _ = G.side_line(s, side)
            keys.add((axis, _r(c), sign))
    return sorted(keys)


def _wall_kit_spans(ctx: Context, face) -> list[tuple[float, float, float, float]]:
    """Kept props.json kit entries hanging on this face (wall tells, the
    Dueling Club's banners): (t0, t1, y0, y1) with a small gap."""
    import kit_models as km

    axis, c, sign = face
    out = []
    for e in ctx_kit_entries(ctx):
        t = e.pos[0] if axis == "z" else e.pos[2]
        d = ((e.pos[2] if axis == "z" else e.pos[0]) - c) * sign
        if abs(d) > G.WALL_TOUCH:
            continue
        model = km.build(e.model, **dict(e.args))
        lo, hi = model.mesh.bounds()
        xs = [lo[0], hi[0]]
        half = max(abs(v) for v in xs) + G.TELL_GAP
        out.append((t - half, t + half, e.pos[1] + lo[1], e.pos[1] + hi[1]))
    return out


def ctx_kit_entries(ctx: Context) -> list:
    """The props.json kit entries the cell keeps (set by context_m3)."""
    return getattr(ctx, "kit_kept", [])


def _hero_spans_on(ctx: Context, face) -> list[tuple[float, float, float, float]]:
    axis, c, sign = face
    return [(t0, t1, y0, y1) for ax, cc, sg, t0, t1, y0, y1 in _hero_wall_spans(ctx)
            if ax == axis and abs(cc - c) < 1e-6 and sg == sign]


def _wainscot_segments(ctx: Context) -> dict:
    """Per face: the Planner's walls that rise from a floor of the cell past
    the wainscot's top, merged into segments (t0, t1, floor at t0, floor
    at t1) with a straight bottom (beside a stair it slopes)."""
    h = ctx.cp.wainscot
    out = {}
    for face in _cell_faces(ctx):
        axis, c, sign = face
        segs = []
        for pc in sorted(ctx.pieces, key=lambda pc: pc.ta):
            if pc.axis != axis or abs(pc.c - c) > 1e-6 or pc.sign != sign:
                continue
            ya, yb = pc.lo.a, pc.lo.b
            own = pc.owner
            q = (lambda t: own.height(t, c)) if axis == "z" else (lambda t: own.height(c, t))
            if abs(q(pc.ta) - ya) > 0.05 or abs(q(pc.tb) - yb) > 0.05:
                continue  # a header over a door or a wall standing on a drop
            if min(pc.hi.a, pc.hi.b) < max(ya, yb) + h + 0.3:
                continue  # too low for the wainscot (the stage's side)
            if segs and abs(segs[-1][1] - pc.ta) < 1e-6 and abs(segs[-1][3] - ya) < 1e-6:
                t0, _, y0, _ = segs[-1]
                k_prev = (segs[-1][3] - y0) / (segs[-1][1] - t0)
                k_new = (yb - ya) / (pc.tb - pc.ta)
                if abs(k_prev - k_new) < 1e-6:
                    segs[-1] = (t0, pc.tb, y0, yb)
                    continue
            segs.append((pc.ta, pc.tb, ya, yb))
        if segs:
            out[face] = segs
    return out


def _wainscot_holes(ctx: Context, face) -> list:
    axis, c, sign = face
    fc = _Face(axis, c, sign)
    holes = []
    for op in ctx.cp.openings:
        if not _same_line(op, fc):
            continue
        if op.kind == "arch":
            holes.append((op.t0 - WAINSCOT_ARCH_GAP, op.t1 + WAINSCOT_ARCH_GAP, "cap"))
        elif op.kind in G.FLOOR_OPENINGS:
            holes.append((op.t0 - WAINSCOT_FLOOR_GAP, op.t1 + WAINSCOT_FLOOR_GAP, "cap"))
        elif op.y0 < _floor_under(ctx, op) + ctx.cp.wainscot + G.WAINSCOT_CLEAR - 1e-6:
            holes.append((op.t0 - 0.3, op.t1 + 0.3, "cap"))
    for t0, t1, y0, _ in _wall_kit_spans(ctx, face):
        if y0 < 3.5 + 1e-6:
            holes.append((t0, t1, "cap"))
    for t0, t1 in _leaf_spans(ctx, fc):
        holes.append((t0 - 0.05, t1 + 0.05, "cap"))
    return holes


def _floor_under(ctx: Context, op) -> float:
    s = ctx.layout.by_id(op.surface)
    return s.floor_max


def _wainscot(ctx: Context) -> list[Use]:
    """Oak panelling along every wall of the cell (the plinth's place):
    `wainscot` high above the local floor, its bottom following a stair,
    broken at arches (beyond their quoins), floor openings and wall-hung
    props, mitred at inside corners; a crest panel in the middle of every
    line bay (and of each gable segment) where nothing hangs."""
    cp = ctx.cp
    h = cp.wainscot
    segs = _wainscot_segments(ctx)
    pieces: dict = {}
    for face, ss in segs.items():
        holes = _wainscot_holes(ctx, face)
        parts = []
        for t0, t1, y0, y1 in ss:
            k = (y1 - y0) / (t1 - t0)
            for a, b, ka, kb in _subtract((t0, t1), holes):
                if b - a < 0.3:
                    continue
                parts.append((a, b, y0 + k * (a - t0), y0 + k * (b - t0), ka, kb))
        pieces[face] = parts
    out = []
    for face, parts in sorted(pieces.items()):
        axis, c, sign = face
        d = tdir(axis, sign)
        for i, (a, b, ya, yb, ka, kb) in enumerate(parts):
            ends = []
            for t, kind in ((a, ka), (b, kb)):
                if kind is not None:
                    ends.append("cap")
                elif any(abs(p[0] - t) < 1e-6 or abs(p[1] - t) < 1e-6 for j, p in enumerate(parts) if j != i):
                    ends.append("none")  # it continues (a stair's slope meets the dais)
                elif _wainscot_corner(pieces, face, t):
                    ends.append("miter")
                else:
                    ends.append("cap")
            start, end = (a, b) if d > 0 else (b, a)
            y_start, y_end = (ya, yb) if d > 0 else (yb, ya)
            e0, e1 = (ends[0], ends[1]) if d > 0 else (ends[1], ends[0])
            o = face_point(axis, c, sign, start, y_start)
            out.append(Use(f"wainscot:{axis}{_r(c)}{'+' if sign > 0 else '-'}:{i}", "wainscot", "kit_wainscot", _o(o),
                           yaw_for(axis, sign), _p(length=b - a, height=h, ends=[e0, e1], rise=y_end - y_start), None))
    out += _wainscot_crests(ctx, pieces)
    return out


def _wainscot_corner(pieces: dict, face, t: float) -> bool:
    axis, c, sign = face
    for (ax2, c2, sg2), parts in pieces.items():
        if ax2 == axis or abs(c2 - t) > 1e-6:
            continue
        for a, b, _, _, ka, kb in parts:
            if (abs(a - c) < 1e-6 and ka is None) or (abs(b - c) < 1e-6 and kb is None):
                return True
    return False


def _wainscot_crests(ctx: Context, pieces: dict) -> list[Use]:
    cp = ctx.cp
    out = []
    edges = cp.lines.edges() if cp.lines else ()
    line_sides = G.LINE_SIDES[cp.lines.axis] if cp.lines else ()
    side_of = {}
    for r in cp.runs:
        side_of[(r.axis, _r(r.c), r.sign)] = r.side
    for face, parts in sorted(pieces.items()):
        axis, c, sign = face
        hero = _hero_spans_on(ctx, face)
        kit_spans = _wall_kit_spans(ctx, face)
        on_lines = side_of.get(face) in line_sides
        for a, b, ya, yb, _, _ in parts:
            if abs(yb - ya) > 1e-6:
                continue  # none on a stair's slope
            if on_lines:
                spots = [(e0 + e1) / 2.0 for e0, e1 in zip(edges, edges[1:])]
            else:
                spots = [(a + b) / 2.0]
            for t in spots:
                if not (a + 0.6 <= t <= b - 0.6):
                    continue
                y = ya + WAINSCOT_CREST_Y
                if any(t0 - 0.5 < t < t1 + 0.5 and y0 < y + 1.0 for t0, t1, y0, _ in hero + kit_spans):
                    continue
                o = face_point(axis, c, sign, t, y)
                out.append(Use(f"crest:{axis}{_r(c)}{'+' if sign > 0 else '-'}:{_r(t)}", "crest", "kit_wainscot_crest",
                               _o(o), yaw_for(axis, sign), (), None))
    return out


def _plate_cornices(ctx: Context) -> list[Use]:
    """The wall plate's cornice: one level, its top at the cell's lowest
    floor + springing, on every wall that stands there (beside a stair
    too), broken round openings that rise into it and at the trusses'
    wall posts, mitred at inside corners."""
    cp = ctx.cp
    base = min(s.y for s in ctx.layout.surfaces if s.cell == cp.cell and s.kind != "door" and not s.sloped)
    top = base + cp.springing
    lines = {}
    for axis, c, sign in _cell_faces(ctx):
        spans = []
        for pc in ctx.pieces:
            if pc.axis != axis or abs(pc.c - c) > 1e-6 or pc.sign != sign:
                continue
            if max(pc.lo.a, pc.lo.b) > top - G.CORNICE.height + 0.05 or min(pc.hi.a, pc.hi.b) < top - 0.05:
                continue
            spans.append((pc.ta, pc.tb))
        merged: list[list[float]] = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1] + 1e-6:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        if merged:
            lines[(axis, c, sign)] = [(a, b) for a, b in merged]
    posts = []
    for h in cp.hero:
        if h.piece.startswith("kit_truss_") and not dict(h.args).get("engaged"):
            posts.append(h.pos)
    segs = {}
    for (axis, c, sign), spans in sorted(lines.items()):
        holes = []
        for op in cp.openings:
            if op.axis != axis or abs(op.c - c) > 1e-6 or op.sign != sign:
                continue
            half, reach = _opening_reach(op)
            # (A frame whose pointed tip only grazes the cornice's underside, the
            # side lancets' at 16.86 m, keeps the cornice whole above it.)
            if reach > top - G.CORNICE.height + PLATE_GRAZE:
                holes.append((op.centre - half - 0.1, op.centre + half + 0.1, "cap"))
        for pos in posts:
            t = pos[0] if axis == "z" else pos[2]
            holes.append((t - TRUSS_POST_GAP, t + TRUSS_POST_GAP, "cap"))
        parts = [(a, b) for x0, x1 in spans for a, b, _, _ in _subtract((x0, x1), holes) if b - a > 0.3]
        if parts:
            segs[(axis, c, sign)] = parts
    out = []
    for (axis, c, sign), parts in sorted(segs.items()):
        for i, (t0, t1) in enumerate(parts):
            ends = []
            for t, d_line in ((t0, 1.0), (t1, -1.0)):
                corner = False
                for (ax2, c2, sg2), parts2 in segs.items():
                    if ax2 == axis or abs(c2 - t) > 1e-6:
                        continue
                    for a2, b2 in parts2:
                        d2 = 1.0 if abs(a2 - c) < 1e-6 else (-1.0 if abs(b2 - c) < 1e-6 else 0.0)
                        if d2 * sign > 0 and d_line * sg2 > 0:
                            corner = True
                ends.append("miter" if corner else "cap")
            ln = Line(f"{axis}{_r(c)}{'+' if sign > 0 else '-'}", axis, c, sign, t0, t1, base, top, ())
            out.append(_sweep_use(f"cornice:{ln.id}:{i}", "cornice", "kit_stone_cornice", ln, t0, t1, top, ends[0], ends[1]))
    return out


def _ridge_beam(ctx: Context) -> list[Use]:
    """The ridge beam the two sky pitches meet under, along the whole cell."""
    cp = ctx.cp
    ln = cp.lines
    flat = [s for s in ctx.layout.surfaces if s.cell == cp.cell and s.kind != "door"]
    other = "z" if ln.axis == "x" else "x"
    c0 = min(s.extent(other)[0] for s in flat)
    c1 = max(s.extent(other)[1] for s in flat)
    lo, hi = ln.ends
    slope = (cp.sky_ridge - cp.sky_plate) / ((c1 - c0) / 2.0)
    mid = (c0 + c1) / 2.0
    origin = (lo, cp.sky_ridge, mid) if ln.axis == "x" else (mid, cp.sky_ridge, lo)
    yaw = 0.0 if ln.axis == "x" else -math.pi / 2
    return [Use("ridge:beam", "ridge", "kit_beam_ridge", _o(origin), yaw, _p(length=hi - lo, slope=round(slope, 6)), None)]


def _strings_m3(ctx: Context) -> list[Use]:
    """The string course under the windows (as _string), also broken at the
    line shafts (its ends hidden in them) and at props hung on the wall
    across its band (the Dueling Club's banners)."""
    cp = ctx.cp
    out = []
    ops_by_run: dict[str, list] = {}
    for op in cp.openings:
        if op.run:
            ops_by_run.setdefault(op.run, []).append(op)
    for r in wall_lines(ctx):
        ops = [o for rr in r.runs for o in ops_by_run.get(rr.id, [])]
        wins = [op for op in ops if op.kind == "window"]
        if not wins:
            continue
        sill = min(op.y0 for op in wins)
        y0, y1 = sill - G.STRING.height, sill
        holes = _gaps(r, line_walled(ctx, r, "floor"))
        for ls in cp.line_shafts:
            if _same_line(ls, r) and r.t0 - 0.5 < ls.t < r.t1 + 0.5:
                holes.append((ls.t - 0.35, ls.t + 0.35, "shaft"))
        for op in ops:
            if op.kind in ("niche", "blind_door", "fireplace") and op.y0 < y1 and op.y1 > y0:
                band = 0.3 if op.kind in ("blind_door", "fireplace") else 0.22  # animated fire: no string across a firebox
                holes.append((op.t0 - band - 0.05, op.t1 + band + 0.05, "cap"))
        for t0, t1, ty0, ty1 in _wall_kit_spans(ctx, (r.axis, r.c, r.sign)):
            if ty0 < y1 + 0.1 and ty1 > y0 - 0.1:
                holes.append((t0, t1, "cap"))
        for op in cp.openings:
            if op.kind == "arch" and _same_line(op, r):
                spring = G.arch_springing(op.width, op.y1 - op.y0) + op.y0
                if spring > y0:
                    holes += [(op.t0 - 0.85, op.t0 + 1e-3, "cap"), (op.t1 - 1e-3, op.t1 + 0.85, "cap")]
        for i, (a, b, ka, kb) in enumerate(_subtract((r.t0, r.t1), holes)):
            if b - a < STRING_MIN:
                continue
            ea, eb = _end(ctx, r, a, ka, "string"), _end(ctx, r, b, kb, "string")
            if ea == "miter" and not _string_at_corner(ctx, r, a, sill):
                ea = "none"
            if eb == "miter" and not _string_at_corner(ctx, r, b, sill):
                eb = "none"
            out.append(_sweep_use(f"string:{r.id}:{i}", "string", "kit_stone_string", r, a, b, y0, ea, eb))
    return out



# =====================================================================================
# Castle kit M4 (the Grand Staircase): docs/art/castle-kit.md "Portraits and tapestries". Spot
# lanterns on walls without bay shafts, portrait clusters in their level
# bands, gallery edges on the railing runs of `railings: gallery_edge` cells,
# hanging flights with stepped undersides, plinths and floor borders on
# galleries whose walls another surface owns, and flush inlays (the floor
# compass) cut out of the floor field and its border.
# =====================================================================================

EDGE_SAMPLE = 0.25  # gallery edges: clearance samples along the run ...
EDGE_REACH = (0.05, 0.14, 0.22)  # ... at these distances into the void
EDGE_MARGIN = 0.03  # kept on top of the 2.4 m overhead rule
EDGE_SMOOTH = 0.5  # a dressing segment keeps the shallowest variant within this of every point
EDGE_MIN = 0.5  # railing runs shorter than this get no gallery edge (the pier landings' corner stubs)
LANTERN_KEEP = (0.21, 0.6, 0.15)  # a wall lantern's reach on its face: half width, below and above the bracket
FLOOR_CUT_PIECES = ("kit_floor_compass",)
_PLANNER_CACHE: dict = {}


def _planner_all(layout: L.Layout):
    """(wall pieces, railings) of the whole layout, any owner (cached)."""
    key = id(layout)
    if key not in _PLANNER_CACHE:
        pl = L.Planner(layout, [])
        pl.build_walls()
        _PLANNER_CACHE.clear()
        _PLANNER_CACHE[key] = (pl.plan.pieces, dict(pl.railings))
    return _PLANNER_CACHE[key]


def m4_staircase(ctx: Context, uses: list[Use]) -> list[Use]:
    cp = ctx.cp
    out = list(uses)
    out += _spot_lanterns(ctx)
    out += _portrait_clusters(ctx)
    if cp.railings == "gallery_edge":
        out = _gallery_edges(ctx, out)
        out = _hanging_flights(ctx, out)
        out += _gallery_plinths(ctx, {u.id for u in out})
    return _floor_cuts(ctx, out)


def _spot_lanterns(ctx: Context) -> list[Use]:
    """Lanterns at castle_kit.json `lanterns.spots` (shaft ""): on the run's
    wall face itself, whoever owns the wall."""
    runs = {r.id: r for r in ctx.cp.runs}
    out = []
    for lt in ctx.cp.lanterns:
        if lt.shaft:
            continue
        r = runs[lt.run]
        out.append(Use(f"lantern:{lt.id}", "lantern", lt.piece, _o(face_point(r.axis, r.c, r.sign, lt.t, lt.y)),
                       yaw_for(r.axis, r.sign), (), r.id))
    return out


def portrait_keep_out(ctx: Context, axis: str, c: float, sign: float) -> list[tuple[float, float, float, float]]:
    """Rectangles (t0, t1, y0, y1) on a face that frames keep clear of: wall
    lanterns, wall tells, hero pieces against the face and (NPCs) the
    talking portraits' frames, each grown by grid.PORTRAIT_KEEP_OUT."""
    import kit_models as km

    ko = G.PORTRAIT_KEEP_OUT
    runs = {r.id: r for r in ctx.cp.runs}
    out = []
    hw, below, above = LANTERN_KEEP
    for lt in ctx.cp.lanterns:
        r = runs[lt.run]
        if r.axis == axis and abs(r.c - c) < 1e-6 and r.sign == sign:
            out.append((lt.t - hw - ko, lt.t + hw + ko, lt.y - below - ko, lt.y + above + ko))
    for kid, model, pos, yaw in ctx.tells:
        t = pos[0] if axis == "z" else pos[2]
        d = ((pos[2] if axis == "z" else pos[0]) - c) * sign
        if abs(d) > G.WALL_TOUCH:
            continue
        lo, hi = km.build(model).mesh.bounds()
        half = G.TELL_HALF_WIDTH
        out.append((t - half - ko, t + half + ko, pos[1] + lo[1] - ko, pos[1] + hi[1] + ko))
    for ax, cc, sg, t0, t1, y0, y1 in _hero_wall_spans(ctx):
        if ax == axis and abs(cc - c) < 1e-6 and sg == sign:
            out.append((t0 - ko, t1 + ko, y0 - ko, y1 + ko))
    # NPCs (docs/art/castle-kit.md "Portraits and tapestries"): the talking portraits' frames (data/npcs/portraits.json).
    from npcs import placements as NPC_PL

    out += NPC_PL.keep_outs(ctx.layout, axis, c, sign, ko)
    return sorted(out)


def portrait_frames(ctx: Context) -> dict:
    """Cluster id -> the generator's frames [(t, y, w, h, shape, canvas)]:
    clusters in face order (each face plane and sheet keeps one history, so
    no canvas repeats within 6 m on a face)."""
    from castle_kit.pieces import frames as FR

    histories: dict = {}
    out = {}
    for pc in sorted(ctx.cp.portraits, key=lambda q: (q.axis, round(q.c, 6), q.sign, q.material, q.y0, q.t0, q.id)):
        key = (pc.axis, round(pc.c, 6), pc.sign, pc.material)
        keep = portrait_keep_out(ctx, pc.axis, pc.c, pc.sign)
        out[pc.id] = FR.layout_cluster(pc.t0, pc.t1, pc.y0, pc.y1, pc.seed, G.PORTRAIT_MIXES[pc.mix], keep,
                                       pc.material, histories.setdefault(key, []))
    return out


def _portrait_clusters(ctx: Context) -> list[Use]:
    from castle_kit.pieces import frames as FR

    if not ctx.cp.portraits:
        return []
    laid = portrait_frames(ctx)
    out = []
    for pc in ctx.cp.portraits:
        d = tdir(pc.axis, pc.sign)
        start = pc.t0 if d > 0 else pc.t1
        rows = []
        for ta, yb, w, h, shape, canvas in laid[pc.id]:
            x0 = ta - start if d > 0 else start - (ta + w)
            rows.append([x0, yb, w, h, FR.SHAPE_INDEX[shape], canvas, FR.border_for(w, h), FR.depth_for(w, h)])
        out.append(Use(f"portraits:{pc.id}", "portraits", "kit_portrait_cluster",
                       _o(face_point(pc.axis, pc.c, pc.sign, start, 0.0)), yaw_for(pc.axis, pc.sign), _p(frames=rows),
                       pc.run, None, pc.band))
    return out


# -- gallery edges -------------------------------------------------------------------------


def _void_dir(axis: str, sign: float) -> tuple[float, float]:
    """(x, z) direction from a railing line into the void (away from its surface)."""
    return (0.0, -sign) if axis == "z" else (-sign, 0.0)


def _along(axis: str, k: float) -> tuple[float, float]:
    return (k, 0.0) if axis == "z" else (0.0, k)


def edge_corner(rails: dict, layout: L.Layout, sid: str, axis: str, c: float, sign: float, t: float, t_other: float) -> str:
    """The dressing's mode at one end of a railing run: "outer" where a
    perpendicular railing run of a flat surface starts at the same point and
    both runs' dressings reach into the same quadrant of the void (every
    corner round the Grand Staircase's void), "miter" where they reach away
    from each other, else "cap"."""
    v1 = _void_dir(axis, sign)
    e1 = _along(axis, 1.0 if t_other > t else -1.0)
    for (sid2, ax2, c2, sg2), spans in sorted(rails.items()):
        if ax2 == axis or layout.by_id(sid2).sloped or abs(c2 - t) > 1e-6:
            continue
        for a2, b2 in spans:
            if b2 - a2 < EDGE_MIN:
                continue
            if abs(a2 - c) < 1e-6:
                e2 = _along(ax2, 1.0)
            elif abs(b2 - c) < 1e-6:
                e2 = _along(ax2, -1.0)
            else:
                continue
            v2 = _void_dir(ax2, sg2)
            k1 = e2[0] * v1[0] + e2[1] * v1[1]
            k2 = e1[0] * v2[0] + e1[1] * v2[1]
            if k1 > 0 and k2 > 0:
                return "outer"
            if k1 < 0 and k2 < 0:
                return "miter"
    return "cap"


def edge_clearance(ctx: Context, s: L.Surface, axis: str, c: float, sign: float, t: float) -> float:
    """How far a gallery edge's dressing may reach below the floor at t:
    2.4 m (+ a margin) stays clear over every walkable surface under its
    reach into the void; 0 where wall stands under the edge (the solid pier
    under the first pier landing)."""
    pieces, _ = _planner_all(ctx.layout)
    if G.face_walled(pieces, axis, c, -sign, t - 0.05, t + 0.05, s.y - 1.0, s.y - 0.5):
        return 0.0
    best = 99.0
    vx, vz = _void_dir(axis, sign)
    for reach in EDGE_REACH:
        px, pz = face_point(axis, c, sign, t, 0.0)[0] + vx * reach, face_point(axis, c, sign, t, 0.0)[2] + vz * reach
        for o in ctx.layout.surfaces:
            if o.id == s.id or not (o.x0 + 1e-6 < px < o.x1 - 1e-6 and o.z0 + 1e-6 < pz < o.z1 - 1e-6):
                continue
            h = o.height(px, pz)
            if h > s.y - 0.3:
                continue
            best = min(best, s.y - h - G.OVERHEAD_CLEARANCE - EDGE_MARGIN)
    return best


def edge_drops(ctx: Context, s: L.Surface, axis: str, c: float, sign: float, ta: float, tb: float):
    """[(t0, t1, variant)] along a railing run: the deepest dressing that
    keeps its clearance (edge_clearance), the shallowest one within
    EDGE_SMOOTH of each point (no short flickering segments)."""
    from castle_kit.pieces import staircase as ST

    n = max(2, int(math.ceil((tb - ta) / EDGE_SAMPLE)) + 1)
    ts = [ta + (tb - ta) * i / (n - 1) for i in range(n)]
    ts[0], ts[-1] = ta + 0.02, tb - 0.02
    order = ST.EDGE_VARIANTS  # deepest first
    raw = []
    for t in ts:
        cl = edge_clearance(ctx, s, axis, c, sign, t)
        raw.append(next((i for i, v in enumerate(order) if ST.EDGE_DROPS[v] <= cl + 1e-9), len(order) - 1))
    smooth = [max(raw[j] for j in range(n) if abs(ts[j] - ts[i]) <= EDGE_SMOOTH + 1e-9) for i in range(n)]
    segs = []
    start = ta
    for i in range(1, n):
        if smooth[i] != smooth[i - 1]:
            cut = _r(round(((ts[i] + ts[i - 1]) / 2) / 0.05) * 0.05)
            segs.append((start, cut, order[smooth[i - 1]]))
            start = cut
    segs.append((start, tb, order[smooth[-1]]))
    return [sg for sg in segs if sg[1] - sg[0] > 1e-6]


def _kit_cells() -> dict:
    """castle_kit.json's cells as written (cached): the Grand Staircase's two
    cells see each other's gallery edges and pier stack."""
    if "kit" not in _PLANNER_CACHE:
        try:
            _PLANNER_CACHE["kit"] = S.load_json(S.KIT_PATH).get("cells", {})
        except OSError:
            _PLANNER_CACHE["kit"] = {}
    return _PLANNER_CACHE["kit"]


def gallery_cells(cp: S.CellPlan) -> set:
    """The cells whose railing runs carry gallery edges (castle_kit.json
    `railings: gallery_edge`), this one included."""
    return {cid for cid, c in _kit_cells().items() if c.get("railings") == "gallery_edge"} | {cp.cell}


def colonnettes(cp: S.CellPlan) -> list[tuple[float, float, float, float]]:
    """(x, z, y0, y1) of every pier stack's corner colonnettes, from this
    cell's heroes and the other gallery-edge cells' (the lower cell's pier
    stack reaches the upper cell's pier landings)."""
    from castle_kit import pieces as P
    from castle_kit.pieces import geom
    from castle_kit.pieces import staircase as ST

    heroes = [(h.piece, h.pos, h.yaw, dict(h.args)) for h in cp.hero]
    for cid in sorted(gallery_cells(cp) - {cp.cell}):
        for h in _kit_cells().get(cid, {}).get("hero", []):
            heroes.append((h["piece"], tuple(h["pos"]), float(h.get("yaw", 0.0)), dict(h.get("args", {}))))
    out = set()
    for piece, pos, yaw, args in heroes:
        if not piece.startswith("kit_pier_stack"):
            continue
        _, size, _, _ = P.parse(piece)
        lands = ST.pier_landings(args.get("top", 49.5))
        pl = geom.Placement(pos, yaw)
        for y0, y1 in zip(lands, lands[1:]):
            for cx, cz in ST.colonnette_spots(size):
                x, _, z = pl.point((cx, 0.0, cz))
                out.add((_r(x), _r(z), y0, _r(y1 - ST.SLAB)))
    return sorted(out)


def _gallery_edges(ctx: Context, uses: list[Use]) -> list[Use]:
    """Railing runs of flat surfaces: the gallery edge instead of the plain
    balustrade (flights keep kit_balustrade_gothic). Newels stay at the runs'
    ends, but not where a colonnette stands and, at a corner shared with the
    other Grand Staircase cell, only in the lower cell."""
    layout = ctx.layout
    _, rails_all = _planner_all(layout)
    gs_cells = gallery_cells(ctx.cp)
    rails = {k: v for k, v in rails_all.items() if layout.by_id(k[0]).cell in gs_cells}
    cols = colonnettes(ctx.cp)
    out = []
    for u in uses:
        if u.role == "balustrade" and u.run and not layout.by_id(u.run).sloped:
            continue
        if u.role == "newel":
            x, y, z = u.origin
            if any(math.hypot(x - cx, z - cz) < 0.5 and abs(y - cy0) < 0.5 for cx, cz, cy0, _ in cols):
                continue  # a colonnette rises from this corner
            if ctx.cp.cell != min(gs_cells) and _shared_corner(rails, layout, ctx.cp.cell, x, z, y):
                continue
        out.append(u)
    for (sid, axis, c, sign), spans in sorted(ctx.railings.items()):
        s = layout.by_id(sid)
        if s.sloped:
            continue
        for i, (ta, tb) in enumerate(spans):
            if tb - ta < EDGE_MIN:
                continue
            d = tdir(axis, sign)
            start = ta if d > 0 else tb
            ea = edge_corner(rails, layout, sid, axis, c, sign, ta, tb)
            eb = edge_corner(rails, layout, sid, axis, c, sign, tb, ta)
            ends = [ea, eb] if d > 0 else [eb, ea]
            segs = edge_drops(ctx, s, axis, c, sign, ta, tb)
            # Clear the colonnettes' tops under a pier landing's edge.
            for cx, cz, _, cy1 in cols:
                if abs(cy1 - (s.y - 0.4)) > 0.05:
                    continue
                ct, cc = (cx, cz) if axis == "z" else (cz, cx)
                if abs(cc - c) > 0.5:
                    continue
                segs = _clip_segs(segs, ct - 0.45, ct + 0.45)
            local = []
            for t0, t1, var in segs:
                x0, x1 = (t0 - start, t1 - start) if d > 0 else (start - t1, start - t0)
                local.append([_r(x0), _r(x1), var])
            local.sort()
            # At a walkable corner jutting into the void (the balconies), the two
            # runs' balustrades stop clear of each other's rail (the corner newel covers it).
            inset = [CORNER_INSET if e == "miter" else NEWEL_HALF for e in ends]
            out.append(Use(f"gallery_edge:{sid}:{axis}{_r(c)}:{i}", "gallery_edge", "kit_gallery_edge",
                           _o(face_point(axis, c, sign, start, s.y)), yaw_for(axis, sign),
                           _p(length=tb - ta, ends=ends, inset=inset, drops=local), sid,
                           LOD_DISTANCE))
    return out


def _shared_corner(rails, layout, cell, x, z, y) -> bool:
    """Does a railing run of another cell end at (x, z) at this height?"""
    for (sid, axis, c, sign), spans in rails.items():
        s = layout.by_id(sid)
        if s.cell == cell or s.sloped or abs(s.y - y) > 0.05:
            continue
        for a, b in spans:
            for t in (a, b):
                px, pz = (t, c) if axis == "z" else (c, t)
                if math.hypot(px - x, pz - z) < 0.35:
                    return True
    return False


def _clip_segs(segs, t0: float, t1: float):
    out = []
    for a, b, var in segs:
        for p0, p1, _, _ in _subtract((a, b), [(t0, t1, "cap")]):
            if p1 - p0 > 0.05:
                out.append((_r(p0), _r(p1), var))
    return out


def _hanging_flights(ctx: Context, uses: list[Use]) -> list[Use]:
    """Flights that do not stand on the cell's lowest floor hang across the
    void: kit_stair_flight_stepped (stepped stringers and underside), even
    where the Planner calls them solid because a gallery at their foot runs
    beside them."""
    lowest = min(s.y for s in ctx.layout.surfaces if s.cell == ctx.cp.cell and s.kind != "door" and not s.sloped)
    out = []
    for u in uses:
        if u.role == "flight" and u.piece == "kit_stair_flight":
            s = ctx.layout.by_id(u.run)
            if not (ctx.solid_stairs.get(s.id, True) and abs(s.y - lowest) < 1e-6):
                kw = u.kwargs()
                foot, crown = _flight_ends_walled(ctx, s)
                extra = {k: False for k, walled in (("foot", foot), ("crown", crown)) if walled}
                u = Use(u.id, u.role, "kit_stair_flight_stepped", u.origin, u.yaw,
                        _p(width=kw["width"], length=kw["length"], rise=kw["rise"], **extra), u.run)
        out.append(u)
    return out


def _flight_ends_walled(ctx: Context, s: L.Surface) -> tuple[bool, bool]:
    """Does Planner wall stand on the plane of the flight's foot (under it)
    or of its top edge (where its top half riser would be)? The flights
    through the pier landing meet the solid pier's faces there."""
    pieces, _ = _planner_all(ctx.layout)
    axis = "x" if s.rise in ("+x", "-x") else "z"
    lo, hi = (s.x0, s.x1) if axis == "x" else (s.z0, s.z1)
    foot_c, top_c = (lo, hi) if s.rise in ("+x", "+z") else (hi, lo)
    t0, t1 = (s.z0, s.z1) if axis == "x" else (s.x0, s.x1)

    def walled(c, y0, y1):
        for sign in (1.0, -1.0):
            for pc in G.face_pieces(pieces, None, axis, c, sign):
                if pc.ta < t1 - 1e-6 and t0 + 1e-6 < pc.tb and min(pc.lo.a, pc.lo.b) < y1 and max(pc.hi.a, pc.hi.b) > y0:
                    return True
        return False

    return walled(foot_c, s.y - 0.4, s.y - 0.05), walled(top_c, s.y_top - 0.1, s.y_top - 0.01)


def _wall_spans_any(pieces, r: G.WallRun, y0: float, y1: float) -> list[tuple[float, float]]:
    spans = []
    for pc in G.face_pieces(pieces, None, r.axis, r.c, r.sign):
        if max(pc.lo.a, pc.lo.b) > y0 + 0.05 or min(pc.hi.a, pc.hi.b) < y1 - 0.05:
            continue
        a, b = max(pc.ta, r.t0), min(pc.tb, r.t1)
        if b > a + 1e-6:
            spans.append((a, b))
    return G._merge(spans)


def _gallery_plinths(ctx: Context, ids: set) -> list[Use]:
    """Galleries hang on walls another surface owns (the tower's ground
    floor): a plinth along those walls and a floor border, as rooms with
    their own walls get (castle kit M1 rules)."""
    pieces, _ = _planner_all(ctx.layout)
    out = []
    for s in sorted((s for s in ctx.layout.surfaces if s.cell == ctx.cp.cell and s.kind != "door" and not s.sloped),
                    key=lambda s: s.id):
        runs = [r for r in ctx.cp.runs if r.surface == s.id and not ctx.walled(r, "floor")]
        rects = []
        spans_by_run = []
        for r in sorted(runs, key=lambda r: (G.SIDES.index(r.side), r.t0)):
            spans = _wall_spans_any(pieces, r, r.floor, r.floor + G.PLINTH.height + 0.05)
            if not spans:
                continue
            spans_by_run.append((r, spans))
            holes = _gaps(r, spans) + [(t0, t1, "cap") for t0, t1, _, _ in _tell_spans(ctx, r)]
            for i, (a, b, ka, kb) in enumerate(_subtract((r.t0, r.t1), holes)):
                if b - a < 0.3:
                    continue
                ea, eb = _end(ctx, r, a, ka, "plinth"), _end(ctx, r, b, kb, "plinth")
                uid = f"plinth:{r.id}:{i}"
                if uid not in ids:
                    out.append(_sweep_use(uid, "plinth", "kit_stone_plinth", r, a, b, r.floor, ea, eb))
        if not ctx.cp.floor_border or f"floor_border:{s.id}" in ids:
            continue
        for r, spans in spans_by_run:
            for a, b in spans:
                if r.side == "n":
                    rects.append((a, s.z0, b, s.z0 + BORDER))
                elif r.side == "s":
                    rects.append((a, s.z1 - BORDER, b, s.z1))
        ns = list(rects)
        for r, spans in spans_by_run:
            for a, b in spans:
                if r.side == "w":
                    rects.extend(L.rect_subtract((s.x0, a, s.x0 + BORDER, b), ns))
                elif r.side == "e":
                    rects.extend(L.rect_subtract((s.x1 - BORDER, a, s.x1, b), ns))
        rects = [tuple(_r(v) for v in rc) for rc in rects if rc[2] - rc[0] > 1e-6 and rc[3] - rc[1] > 1e-6]
        if rects:
            out.append(Use(f"floor_border:{s.id}", "floor_border", "kit_floor_border", (0.0, 0.0, 0.0), 0.0,
                           _p(rects=[list(rc) for rc in rects], y=s.y), None))
    return out


# -- flush inlays --------------------------------------------------------------------------


FLOOR_CUT_Y = 0.05  # An inlay cuts only the floor it lies on (not a landing above it)


def floor_cuts(cp: S.CellPlan, y: float | None = None) -> list[tuple[float, float, float, float]]:
    """World rectangles a flush hero inlay (meta "floor_cut", the floor
    compass) takes out of the floor field and the floor border; the hero
    draws that floor again. With `y`, only the inlays on the floor at that
    height (the compass round the pier's foot took the pier
    landing's floor 13.5 m above it as well)."""
    from castle_kit import pieces as P
    from castle_kit.pieces import geom

    out = []
    for h in cp.hero:
        if not h.piece.startswith(FLOOR_CUT_PIECES + M5B_UPPER_FLOOR_CUTS):  # Castle M5b (Upper rooms): the floor map
            continue
        if y is not None and abs(h.pos[1] - y) > FLOOR_CUT_Y:
            continue
        meta = P.build(h.piece, **dict(h.args)).meta
        pl = geom.Placement(h.pos, h.yaw)
        for x0, z0, x1, z1 in meta.get("floor_cut", ()):
            pts = [pl.point((x, 0.0, z)) for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1))]
            out.append((_r(min(p[0] for p in pts)), _r(min(p[2] for p in pts)), _r(max(p[0] for p in pts)),
                        _r(max(p[2] for p in pts))))
    return out


def _floor_cuts(ctx: Context, uses: list[Use]) -> list[Use]:
    if not floor_cuts(ctx.cp):
        return uses
    out = []
    for u in uses:
        if u.role == "floor_border":
            kw = u.kwargs()
            cuts = floor_cuts(ctx.cp, kw["y"])
            rects = [r2 for rc in kw["rects"] for r2 in L.rect_subtract(tuple(rc), cuts)]
            rects = [[_r(v) for v in rc] for rc in rects if rc[2] - rc[0] > 1e-6 and rc[3] - rc[1] > 1e-6]
            if not rects:
                continue
            u = Use(u.id, u.role, u.piece, u.origin, u.yaw, _p(rects=rects, y=kw["y"]), u.run)
        out.append(u)
    return out


# =====================================================================================
# Castle M5a (Corridors): docs/art/castle-kit.md. The M5a
# cells only (grid.M5A_CELLS); every other cell's placements are untouched.
# =====================================================================================

M5A_SHAFT_KEEP = G.SHAFT.height / 2  # a portrait keeps clear of a bay shaft's half width (+ PORTRAIT_KEEP_OUT)
M5A_QUOIN_REACH = 0.9  # ... and of an outside-corner quoin's blocks (0.8 m long, 0.1 proud) either side of its corner


def m5a_corridors(ctx: Context, uses: list[Use]) -> list[Use]:
    """The M5a corridors' rules on top of the M1-M4 ones:

    - an arch whose jamb meets an inside corner of the wall it stands in (the
      N3 vestibules' tower doors, flush with the vestibule's north wall) loses
      that jamb's quoins, which would lie in the other wall's face; the other
      jamb keeps its quoins (a kit_stone_quoin of its own);
    - portrait clusters on walls with bay shafts (the fourth-floor Portrait
      Gallery) keep clear of the shafts, as of the lanterns."""
    if G.M5A_CELLS.get(ctx.cp.cell) != "corridors":
        return uses
    return _m5a_portraits(ctx, _m5a_corner_arches(ctx, uses))


def _m5a_corner_arches(ctx: Context, uses: list[Use]) -> list[Use]:
    ops = {f"arch:{o.id}": o for o in ctx.cp.openings if o.kind == "arch"}
    out = []
    for u in uses:
        op = ops.get(u.id)
        if op is None:
            out.append(u)
            continue
        corner = [t for t in (op.t0, op.t1) if _corner_run(ctx, op, t) is not None]
        if not corner:
            out.append(u)
            continue
        out.append(Use(u.id, u.role, u.piece, u.origin, u.yaw, _p(**u.kwargs(), quoins=False), u.run, u.lod_distance,
                       u.group))
        spring = G.arch_springing(op.width, op.y1 - op.y0)
        k = tdir(op.axis, op.sign)
        for t in (op.t0, op.t1):
            if t in corner:
                continue
            side = 1 if (t - op.centre) * k > 0 else -1  # the quoin's local side (+x to the right of the arch)
            o = face_point(op.axis, op.c, op.sign, t, op.y0)
            out.append(Use(f"quoin:{op.id}:{'r' if side > 0 else 'l'}", "quoin", "kit_stone_quoin", _o(o), u.yaw,
                           _p(y0=G.PLINTH.height, y1=_r(spring), corner=False, side=side), None))
    return out


def m5a_portrait_keep_out(ctx: Context, axis: str, c: float, sign: float) -> list[tuple[float, float, float, float]]:
    """portrait_keep_out plus the bay shafts on the face (their whole height)
    and the outside-corner quoins whose corner lies on it."""
    ko = G.PORTRAIT_KEEP_OUT
    runs = {r.id: r for r in ctx.cp.runs}
    out = list(portrait_keep_out(ctx, axis, c, sign))
    for sh in ctx.cp.shafts:
        r = runs[sh.run]
        if r.axis == axis and abs(r.c - c) < 1e-6 and r.sign == sign:
            out.append((sh.t - M5A_SHAFT_KEEP - ko, sh.t + M5A_SHAFT_KEEP + ko, r.floor - ko, r.top + ko))
    for q in _outside_quoins(ctx):
        x, y, z = q.origin
        if abs((z if axis == "z" else x) - c) < 1e-6:
            t = x if axis == "z" else z
            out.append((t - M5A_QUOIN_REACH - ko, t + M5A_QUOIN_REACH + ko, y - ko, y + G.STOREY + ko))
    return sorted(out)


def m5a_portrait_frames(ctx: Context) -> dict:
    """portrait_frames with m5a_portrait_keep_out."""
    from castle_kit.pieces import frames as FR

    histories: dict = {}
    out = {}
    for pc in sorted(ctx.cp.portraits, key=lambda q: (q.axis, round(q.c, 6), q.sign, q.material, q.y0, q.t0, q.id)):
        key = (pc.axis, round(pc.c, 6), pc.sign, pc.material)
        keep = m5a_portrait_keep_out(ctx, pc.axis, pc.c, pc.sign)
        out[pc.id] = FR.layout_cluster(pc.t0, pc.t1, pc.y0, pc.y1, pc.seed, G.PORTRAIT_MIXES[pc.mix], keep,
                                       pc.material, histories.setdefault(key, []))
    return out


def _m5a_portraits(ctx: Context, uses: list[Use]) -> list[Use]:
    from castle_kit.pieces import frames as FR

    if not ctx.cp.portraits or not ctx.cp.shafts:
        return uses
    laid = m5a_portrait_frames(ctx)
    out = [u for u in uses if u.role != "portraits"]
    for pc in ctx.cp.portraits:
        d = tdir(pc.axis, pc.sign)
        start = pc.t0 if d > 0 else pc.t1
        rows = []
        for ta, yb, w, h, shape, canvas in laid[pc.id]:
            x0 = ta - start if d > 0 else start - (ta + w)
            rows.append([x0, yb, w, h, FR.SHAPE_INDEX[shape], canvas, FR.border_for(w, h), FR.depth_for(w, h)])
        out.append(Use(f"portraits:{pc.id}", "portraits", "kit_portrait_cluster",
                       _o(face_point(pc.axis, pc.c, pc.sign, start, 0.0)), yaw_for(pc.axis, pc.sign), _p(frames=rows),
                       pc.run, None, pc.band))
    return out


# =====================================================================================
# Castle M5a (Tower): the west tower's spiral stair (docs/art/castle-kit.md "The kitted cells", "Pieces").
# A cell of budget kind "tower": its flights hang between the newel core and the
# shell (kit_stair_flight_stepped, the Grand Staircase's pattern: the treads and
# nosing_stone risers of kit_stair_flight over stepped undersides), the newel core
# hero gets its arcade runs and the shaft's base from here, the shell's cornice runs
# round the shaft's top whichever surface owns each wall, the landings above the
# lowest turn get plinths and floor borders (the walls are the lowest turn's) and
# corbels under their slabs, the slit windows keep no string course, and an arch
# whose jamb stands in an inside corner (the N3 doors on the corner landings) has no
# quoins to push into the other wall.
# =====================================================================================

TOWER_CORBEL = "kit_stone_corbel_0.8"
TOWER_CORBEL_INSET = 1.2  # corbels this far from each end of a landing's wall (clear of the corner's other pair)
TOWER_CORE = "kit_spiral_core"
TOWER_QUOIN_ROOM = 0.4  # an arch keeps its quoins only where wall runs on this far beyond each jamb
_TOWER_PLANNER: dict = {}


def m5a_tower(ctx: Context, uses: list[Use]) -> list[Use]:
    cp = ctx.cp
    if cp.budget != "tower":
        return uses
    patched = {(ln[0], ln[1], ln[2]) for ln in tower_shell_patches(ctx.layout, cp)}
    ops = {f"window:{o.id}": o for o in cp.openings if o.kind == "window"}
    archs = {f"arch:{o.id}": o for o in cp.openings if o.kind == "arch"}
    out = []
    for u in uses:
        if u.role in ("string", "cornice"):
            continue  # the slits keep no sill course; the shaft's cornice is _tower_cornices'
        if u.role == "flight" and u.piece == "kit_stair_flight":
            s = ctx.layout.by_id(u.run)
            kw = u.kwargs()
            foot, crown = _flight_ends_walled(ctx, s)
            extra = {k: False for k, walled in (("foot", foot), ("crown", crown)) if walled}
            u = Use(u.id, u.role, "kit_stair_flight_stepped", u.origin, u.yaw,
                    _p(width=kw["width"], length=kw["length"], rise=kw["rise"], **extra), u.run)
        elif u.role == "arch" and not _tower_quoin_room(ctx, archs[u.id]):
            u = Use(u.id, u.role, u.piece, u.origin, u.yaw, _p(**u.kwargs(), quoins=False), u.run, u.lod_distance)
        elif u.role == "window" and u.id in ops:
            op = ops[u.id]
            if (op.axis, _r(op.c), op.sign) in patched:
                kw = {k: v for k, v in u.kwargs().items() if k != "outer"}
                u = Use(u.id, u.role, u.piece, u.origin, u.yaw, _p(**kw), u.run)
        elif u.role == "hero" and u.piece.startswith(TOWER_CORE + "_"):
            h = next(x for x in cp.hero if f"hero:{x.id}" == u.id)
            args = dict(h.args)
            args.update(base=h.pos[1], runs=core_runs(ctx.layout, cp, h))
            u = Use(u.id, u.role, u.piece, u.origin, u.yaw, _p(**args), u.run)
        out.append(u)
    out += _tower_cornices(ctx)
    out += _gallery_plinths(ctx, {u.id for u in out})
    out += _tower_corbels(ctx)
    return out


def _tower_bounds(layout: L.Layout, cp) -> tuple[float, float, float, float, float]:
    """(x0, z0, x1, z1, top) of the tower cell's shaft: its non-door surfaces."""
    ss = [s for s in layout.surfaces if s.cell == cp.cell and s.kind != "door"]
    return (min(s.x0 for s in ss), min(s.z0 for s in ss), max(s.x1 for s in ss), max(s.z1 for s in ss),
            max(s.ceil_y for s in ss if s.roofed))


def core_runs(layout: L.Layout, cp, h) -> list:
    """The newel core's arcade runs: every stretch of floor or flight along a
    face of the core (the hero's square, centred on its pos), as [side, u0,
    u1, y0, y1] in the core's face frames (pieces/tower.py core_face), the
    floor's heights above the hero's foot."""
    from castle_kit.pieces import parse
    from castle_kit.pieces import tower

    size = parse(h.piece)[1]
    half = size / 2.0
    cx, base, cz = h.pos
    out = []
    for s in sorted((s for s in layout.surfaces if s.cell == cp.cell and s.kind != "door"), key=lambda s: s.id):
        for side, axis, c, edge in (("n", "z", cz - half, s.z1), ("s", "z", cz + half, s.z0),
                                    ("e", "x", cx + half, s.x0), ("w", "x", cx - half, s.x1)):
            if abs(edge - c) > 1e-6:
                continue
            lo, hi = (s.x0, s.x1) if axis == "z" else (s.z0, s.z1)
            ca, cb = (cx - half, cx + half) if axis == "z" else (cz - half, cz + half)
            a, b = max(lo, ca), min(hi, cb)
            if b - a < 0.5:
                continue
            pts = []
            for t in (a, b):
                x, z = (t, c) if axis == "z" else (c, t)
                pts.append((tower.core_face(side, half, x - cx, z - cz), s.height(x, z) - base))
            pts.sort()
            (u0, y0), (u1, y1) = pts
            out.append([side, _r(u0), _r(u1), _r(y0), _r(y1)])
    return out


def _tower_quoin_room(ctx: Context, op) -> bool:
    """Does the wall run on beyond both jambs of an arch (on its face, at
    door height), so its jamb quoins lie on wall?"""
    pieces, _ = _planner_all(ctx.layout)
    y = op.y0 + 1.0
    for t in (op.t0 - TOWER_QUOIN_ROOM, op.t1 + TOWER_QUOIN_ROOM):
        if not any(pc.ta - 1e-6 <= t <= pc.tb + 1e-6 and min(pc.lo.a, pc.lo.b) <= y <= max(pc.hi.a, pc.hi.b)
                   for pc in G.face_pieces(pieces, None, op.axis, op.c, op.sign)):
            return False
    return True


def _tower_cornices(ctx: Context) -> list[Use]:
    """The cornice under the shaft's ceiling along each side of the shell,
    corner to corner (mitred), whichever surface owns each stretch of wall."""
    cp = ctx.cp
    x0, z0, x1, z1, top = _tower_bounds(ctx.layout, cp)
    out = []
    for side, axis, c, sign, (a, b) in (("n", "z", z0, 1.0, (x0, x1)), ("s", "z", z1, -1.0, (x0, x1)),
                                        ("w", "x", x0, 1.0, (z0, z1)), ("e", "x", x1, -1.0, (z0, z1))):
        run = G.WallRun(f"{cp.cell}:{side}*", cp.cell, cp.cell, side, 0, axis, c, sign, a, b, top, top)
        out.append(_sweep_use(f"cornice:{cp.cell}:{side}", "cornice", "kit_stone_cornice", run, a, b, top, "miter", "miter"))
    return out


def _tower_corbels(ctx: Context) -> list[Use]:
    """Corbels under the slabs of the landings above the lowest turn, on the
    shell walls they hang from (the Grand Staircase's landing pattern): two
    a wall, TOWER_CORBEL_INSET from its ends."""
    from castle_kit.pieces import parse

    cp = ctx.cp
    pieces, _ = _planner_all(ctx.layout)
    size = parse(TOWER_CORBEL)[1]
    x0, z0, x1, z1, _ = _tower_bounds(ctx.layout, cp)
    ss = [s for s in ctx.layout.surfaces if s.cell == cp.cell and s.kind != "door"]
    out = []
    for s in sorted((s for s in ss if not s.sloped), key=lambda s: s.id):
        below = [u for u in ss if u.floor_max < s.floor_min - G.INTRUSION_HEIGHT and min(s.x1, u.x1) - max(s.x0, u.x0) > 1e-6
                 and min(s.z1, u.z1) - max(s.z0, u.z0) > 1e-6]
        if not below:
            continue
        y = s.y - L.UNDER_SLAB
        doors = [(j.side, j.t0, j.t1) for j in G.contacts(ctx.layout, s) if j.is_door]
        for side, edge in (("n", z0), ("s", z1), ("w", x0), ("e", x1)):
            axis, c, sign, (a, b) = G.side_line(s, side)
            if abs(c - edge) > 1e-6:
                continue
            run = G.WallRun(f"{s.id}:{side}*", s.id, cp.cell, side, 0, axis, c, sign, a, b, s.y, s.y)
            spans = _wall_spans_any(pieces, run, y - size, y)
            for k, t in enumerate((a + TOWER_CORBEL_INSET, b - TOWER_CORBEL_INSET)):
                if not _on_wall(spans, t, 0.3) or any(sd == side and t0 - 0.5 < t < t1 + 0.5 for sd, t0, t1 in doors):
                    continue
                out.append(Use(f"corbel:{s.id}:{side}{k}", "corbel", TOWER_CORBEL, _o(face_point(axis, c, sign, t, y)),
                               yaw_for(axis, sign), (), None))
    return out


def tower_shell_patches(layout: L.Layout, cp) -> list:
    """Wall lines of a tower cell whose through windows the Planner leaves
    without an outer face: its build_shell counts the wall mass as shared
    with a room beyond over the wall's whole height, though the room stands
    only above the windows (common room C over the tower's north wall). For
    each, the missing exterior face (assemble draws it as the cell's own,
    holed by the windows): [(axis, c, sign, ta, tb, y0, y1, plane, holes)]."""
    if cp.budget != "tower":
        return []
    key = id(layout)
    if key not in _TOWER_PLANNER:
        pl = L.Planner(layout, [])
        pl.build_walls()
        _TOWER_PLANNER.clear()
        _TOWER_PLANNER[key] = pl
    pl = _TOWER_PLANNER[key]
    lines: dict = {}
    for op in cp.openings:
        if op.kind != "window" or op.depth is not None:
            continue
        mine = [pc for pc in pl.plan.pieces if pc.owner.cell == cp.cell and pc.axis == op.axis
                and abs(pc.c - op.c) < 1e-6 and pc.sign == op.sign]
        pc = next((pc for pc in mine if pc.ta - 1e-6 <= op.t0 and op.t1 <= pc.tb + 1e-6), None)
        if pc is None:
            continue
        other = "z" if pc.axis == "x" else "x"
        here = [s for _, s in pl._near(pc, min(pc.lo.a, pc.lo.b), max(pc.hi.a, pc.hi.b) + L.SLAB)
                if s.extent(other)[0] <= op.t0 and op.t1 <= s.extent(other)[1]]
        if not here or min(s.floor_min for s in here) - L.SLAB < op.y1 - 1e-6:
            continue  # the shell is drawn there (or the room is level with the window: not a tower case)
        k = (op.axis, _r(op.c), op.sign)
        ln = lines.setdefault(k, {"pieces": mine, "top": min(s.floor_min for s in here) - L.SLAB, "holes": []})
        ln["top"] = min(ln["top"], min(s.floor_min for s in here) - L.SLAB)
        ln["holes"].append((_r(op.t0), _r(op.y0), _r(op.t1), _r(op.y1)))
    out = []
    for (axis, c, sign), ln in sorted(lines.items()):
        pcs = sorted(ln["pieces"], key=lambda p: p.ta)
        thick = G.EXTERIOR_SHELL
        first, last = pcs[0], pcs[-1]
        ta = first.ta - pl._corner_extension(first.owner, first, first.ta, -1, thick)
        tb = last.tb + pl._corner_extension(last.owner, last, last.tb, 1, thick)
        y0 = min(min(p.lo.a, p.lo.b) for p in pcs) - L.SLAB
        # Castle M5b layout (N4): a roofed room stacked under the tower with
        # its wall on the same line (the Second-Floor Library under the north
        # wall) draws its own outer face up to its roof slab; start above it.
        other = "z" if axis == "x" else "x"
        floor = min(min(p.lo.a, p.lo.b) for p in pcs)
        for s in layout.surfaces:
            if s.cell == cp.cell or not s.roofed or s.struct_top() > floor + 1e-6:
                continue
            e0, e1 = s.extent(axis)
            o0, o1 = s.extent(other)
            if abs((e0 if sign > 0 else e1) - c) < 1e-6 and min(tb, o1) - max(ta, o0) >= 0.5 * (tb - ta):
                y0 = max(y0, s.struct_top())
        out.append((axis, c, sign, _r(ta), _r(tb), _r(y0), _r(ln["top"]), _r(c - sign * thick), sorted(ln["holes"])))
    return out


# =====================================================================================
# Castle M5b (Dungeons): docs/art/castle-kit.md "The kitted cells", "Pieces". The dungeon cells
# only (grid.M5B_CELLS "dungeons"); every other cell's placements are untouched.
# =====================================================================================

# Doors the kit covers whole on one side: that side's arch keeps its spandrels and soffit
# but no rim, hood or quoins, so common room D's hidden stone door is a plain stretch of
# the dungeon corridor's wall and the Fruit Bowl Painting hangs on a plain wall.
M5B_PLAIN_ARCHES = (("dungeon_west", "cr_d_wall"), ("basement_east", "kitchen_painting_door"))
# Arches whose jambs stand flush with the side walls of a stair as wide as the door (common
# room D's steps): no quoins, which would lie in those walls' faces.
M5B_FLUSH_ARCHES = (("common_room_house_d", "cr_d_wall"),)
# Review fixes: cells whose hooded fireplace faces its hood in the vault web's stone
# (kit_fireplace_hooded `hood` "web"): on the dark dungeon wall the wall stone's courses
# ran on across the hood and it read flat head-on.
M5B_WEB_HOODS = ("common_room_house_d",)
# Cells whose hooded fireplace burns bigger (kit_fireplace_hooded `blaze`): the fire is the
# room's focal point and its warm key light (common room D).
BLAZING_HEARTHS = {"common_room_house_d": 1.3}
# Cells whose flat ceiling is carried by oak beams (castle_kit.json heroes) instead of the
# stone ribs between facing shafts: common room B's low beams.
M5B_BEAMED = ("common_room_house_b",)


def m5b_dungeons(ctx: Context, uses: list[Use]) -> list[Use]:
    """The dungeon rules on top of the M1-M5a ones:

    - the basement steps (dungeon, kitchen and common room D's) run between
      walls down to their floor, so their flights are solid (treads and
      risers; no stringer sides, which overlapped step by step, nor a soffit
      in the ground), with a kit_stair_skirt closing the strip under each
      wall beside each step (the walls stop at the ramp line);
    - the arches of M5B_PLAIN_ARCHES keep no rim or quoins, those of
      M5B_FLUSH_ARCHES no quoins;
    - beamed cells (M5B_BEAMED) drop the stone ribs, diagonals, bosses and
      rib corbels of their flat ceiling;
    - a hooded fireplace faces its hood in the web's stone where its cell
      asks for it (hood "web") and burns a bigger fire in the cells of
      BLAZING_HEARTHS (`blaze`);
    - every transverse rib of a barrel vault (kit_vault_barrel) lands on a
      bay shaft or, where none stands, on a corbel under the springing
      cornice (as the Entrance Hall's vault lines)."""
    cp = ctx.cp
    if G.m5b_rules(cp.cell) != "dungeons":
        return uses
    from dataclasses import replace

    plain = {d for c, d in M5B_PLAIN_ARCHES if c == cp.cell}
    flush = {d for c, d in M5B_FLUSH_ARCHES if c == cp.cell}
    beamed = cp.cell in M5B_BEAMED
    out = []
    for u in uses:
        if u.role == "fireplace" and cp.cell in M5B_WEB_HOODS:
            u = replace(u, params=_p(**dict(u.kwargs(), hood="web")))
        if u.role == "fireplace" and cp.cell in BLAZING_HEARTHS:
            u = replace(u, params=_p(**dict(u.kwargs(), blaze=BLAZING_HEARTHS[cp.cell])))
        if u.role == "flight":
            kw = u.kwargs()
            kw["solid"] = True
            # Review fixes (castle M5b): stone treads (nosing_stone). An 18 m flight for 9 m
            # has the Marble Staircase's tread, so _flights gave it the white marble strip.
            kw.pop("strip", None)
            u = replace(u, params=_p(**kw))
            out.append(Use(f"skirt:{u.run}", "skirt", "kit_stair_skirt", u.origin, u.yaw,
                           _p(width=kw["width"], length=kw["length"], rise=kw["rise"]), u.run))
        elif u.role == "arch":
            door = u.id.rsplit(":arch:", 1)[-1]
            if door in plain or door in flush:
                kw = u.kwargs()
                kw["quoins"] = False
                if door in plain:
                    kw["rim"] = False
                u = replace(u, params=_p(**kw))
        elif beamed and (u.id.startswith(("rib:", "diagonal:", "boss:"))
                         or (u.role == "corbel" and not u.id.startswith("corbel:vault"))):
            continue
        out.append(u)
    out += _m5b_rib_corbels(ctx)
    out.sort(key=lambda u: u.id)
    return out


def _m5b_rib_corbels(ctx: Context) -> list[Use]:
    """A kit_stone_corbel_0.6 under the springing cornice at each foot of a
    barrel vault's transverse rib with no bay shaft under it (on a wall,
    clear of the openings' surrounds)."""
    from castle_kit import pieces as P

    cp = ctx.cp
    runs = {r.id: r for r in cp.runs}
    shafts = [sh for sh in cp.shafts if _on_wall(ctx.walled(runs[sh.run], "full"), sh.t, 0.43)]
    out = []
    for h in cp.hero:
        if not h.piece.startswith(G.VAULT_BARREL + "_"):
            continue
        meta = P.build(h.piece, **dict(h.args)).meta
        hs = meta["span"] / 2.0
        top = h.pos[1] - G.CORNICE.height
        for z in meta["ribs"]:
            for x in (-hs, hs):
                lx, lz = (x, z) if meta["axis"] == 0 else (z, -x)
                wx, wz = h.pos[0] + lx, h.pos[2] + lz
                # The wall the foot lands on: x = wx (axis 0) or z = wz (axis 1), facing the vault.
                if meta["axis"] == 0:
                    axis, c, t, sign = "x", wx, wz, (1.0 if x < 0 else -1.0)
                else:
                    axis, c, t, sign = "z", wz, wx, (1.0 if lz < 0 else -1.0)
                if not any(a + 0.3 <= t <= b - 0.3 for a, b in _face_spans(ctx, axis, c, sign, top - 0.7, h.pos[1])):
                    continue  # no wall there (an open end, a corner)
                if any(_same_line(runs[sh.run], _Face(axis, c, sign)) and abs(sh.t - t) < 0.6 for sh in shafts):
                    continue  # a shaft carries the rib
                clash = False
                for op in cp.openings:
                    if op.axis != axis or abs(op.c - c) > 1e-6 or op.sign != sign:
                        continue
                    half, reach = _opening_reach(op)
                    if reach > top - 0.7 and op.centre - half - 0.4 < t < op.centre + half + 0.4:
                        clash = True
                if clash:
                    continue
                o = face_point(axis, c, sign, t, top)
                out.append(Use(f"corbel:vault:{axis}{_r(c)}:{_r(t)}", "corbel", "kit_stone_corbel_0.6", _o(o),
                               yaw_for(axis, sign), (), None))
    return out


# =====================================================================================
# Castle M5b (Upper rooms): docs/art/castle-kit.md "The kitted cells", "Pieces". The Upper rooms
# builder's cells only (grid.M5B_CELLS); every other cell's placements are untouched.
# =====================================================================================

M5B_UPPER_FLOOR_CUTS = (G.FLOOR_MAP,)  # flush inlays (the library's floor map) cut the floor like the compass


def m5b_upper(ctx: Context, uses: list[Use]) -> list[Use]:
    """The Upper rooms' rules on top of the others:

    - a flight between walls on both sides (the Trophy Stair's, in its
      stairwell) is built solid: the stringer sides would lie in the walls'
      planes (and overlap step to step) and its soffit is never seen;
    - the string course under a wall's windows breaks round the wall pieces
      standing against that wall across its height (bookcases, trophy cases,
      dorm doors; the drapes hang in front of it), as the plinth does."""
    if G.m5b_rules(ctx.cp.cell) != "upper_rooms":
        return uses
    runs = {r.id: r for r in ctx.cp.runs}
    walls = [w for w, h in zip(_hero_wall_spans(ctx), _m5b_wall_heroes(ctx)) if not h.startswith(M5B_UPPER_STRING_PASS)]
    out = []
    for u in uses:
        if u.role == "flight" and not u.kwargs().get("solid", True):
            kw = dict(u.kwargs(), solid=True)
            u = Use(u.id, u.role, u.piece, u.origin, u.yaw, _p(**kw), u.run, u.lod_distance, u.group)
        if u.role == "flight":
            # Review fixes (castle M5b): the stairwell's walls stop at the ramp line, so the
            # triangle beside each step under it was open to the sky; close it as the
            # Dungeons' flights do (kit_stair_skirt, in the wall planes).
            kw = u.kwargs()
            out.append(Use(f"skirt:{u.run}", "skirt", "kit_stair_skirt", u.origin, u.yaw,
                           _p(width=kw["width"], length=kw["length"], rise=kw["rise"]), u.run))
        if u.role == "string" and u.run in runs:
            out += _m5b_string_breaks(u, runs[u.run], walls)
            continue
        out.append(u)
    return out


M5B_UPPER_STRING_PASS = ("kit_drapes",)  # wall pieces the string course runs behind


def _m5b_wall_heroes(ctx: Context) -> list[str]:
    """The pieces of _hero_wall_spans' entries, in its order."""
    from castle_kit import pieces as P

    out = []
    for h in ctx.cp.hero:
        if h.piece.startswith("kit_vault_"):
            continue
        try:
            meta = P.build(h.piece, **dict(h.args)).meta
        except KeyError:
            continue
        if "wall" not in meta:
            continue
        for r in ctx.cp.runs:
            t = h.pos[0] if r.axis == "z" else h.pos[2]
            d = ((h.pos[2] if r.axis == "z" else h.pos[0]) - r.c) * r.sign
            if abs(d) > G.WALL_TOUCH or abs(_r(h.yaw) - _r(yaw_for(r.axis, r.sign))) > 1e-3:
                continue
            out.append(h.piece)
            break
    return out


def _m5b_string_breaks(u: Use, r: G.WallRun, walls) -> list[Use]:
    """A string course use with the wall pieces on its face taken out."""
    kw = u.kwargs()
    d = tdir(r.axis, r.sign)
    start = u.origin[0] if r.axis == "z" else u.origin[2]
    t0, t1 = sorted((start, start + d * kw["length"]))
    e0, e1 = (kw["ends"][0], kw["ends"][1]) if d > 0 else (kw["ends"][1], kw["ends"][0])
    y0, y1 = u.origin[1], u.origin[1] + G.STRING.height
    holes = [(a - 0.05, b + 0.05, "cap") for axis, c, sign, a, b, wy0, wy1 in walls
             if axis == r.axis and abs(c - r.c) < 1e-6 and sign == r.sign and wy0 < y1 and wy1 > y0 and b > t0 and a < t1]
    if not holes:
        return [u]
    out = []
    for i, (a, b, ka, kb) in enumerate(_subtract((t0, t1), holes)):
        if b - a < STRING_MIN:
            continue
        ea = e0 if abs(a - t0) < 1e-6 else "cap"
        eb = e1 if abs(b - t1) < 1e-6 else "cap"
        out.append(_sweep_use(f"{u.id}:{i}", "string", u.piece, r, a, b, u.origin[1], ea, eb))
    return out


# =====================================================================================
# Castle M5c (Facades): docs/art/castle-kit.md "Exterior". The facade rules: the
# facade pieces (pieces/facades.py) on every cell with a `facade` (castle_kit.json),
# placed on the cell's outer planes (the Planner's wall_exterior faces of
# the facade's sides) and wall tops; an outdoor cell's (the courtyard's) facades are its
# own walls, seen from inside it. Every facade piece draws its LOD1 beyond FACADE_LOD.
# =====================================================================================

FACADE_SIDES = {"n": ("z", -1.0), "s": ("z", 1.0), "w": ("x", -1.0), "e": ("x", 1.0)}  # side -> axis, outward sign
FACADE_PARAPET = {"height": 1.6, "thick": 0.45, "proud": 0.12}  # kit_parapet_crenel args on every facade
FACADE_PINNACLE_TALL = 40.0  # a wall top this high takes the tall corner pinnacles
FACADE_PINNACLE_H = (6.0, 4.5, 4.0)  # corner pinnacles: tall walls, other buildings, the courtyard's walls
FACADE_PINNACLE_W = (0.8, 1.0)  # a building's corner pinnacle, the courtyard's (on its 1 m wall top)
BUTTRESS_BAY = 7.5  # a corner stretch this long or longer takes buttresses
BUTTRESS_WIDTH = 1.2
BUTTRESS_CLEAR = 0.5  # a buttress keeps this clear of an opening's reveal
BUTTRESS_PROUD = {"ground": (1.4, 0.9), "wall_top": (0.8, 0.55)}  # foot / top proud: from the ground, on a wall top
BUTTRESS_PINNACLE = (5.0, 4.0)  # a buttress's pinnacle: from the ground, on a wall top
ROOF_PITCH = 50.0  # over 45 degrees: the world-mapped slates course along the ridge
ROOF_OVERHANG = 0.3
ROOF_CORNICE = 0.6
PARAPET_CORNER_CLEAR = 0.13  # courtyard corners: x-line parapets also clear two copings' overhang (0.06 each)
TRACERY_MARGIN = 1.0  # the outer tracery's screen beyond its window each side (kit_window_tracery_outer)

_SHELL: dict = {}


def facade_shell(layout: L.Layout):
    """The Planner's walls and outer shell of the whole layout (no holes, no
    props), built once per layout."""
    got = _SHELL.get("plan")
    if got is None or got[0] is not layout:
        pl = L.Planner(layout, [])
        pl.build_walls()
        pl.build_shell()
        got = (layout, pl.plan)
        _SHELL["plan"] = got
    return got[1]


@dataclass
class FacadeFace:
    """One outer plane of a facing cell: `side`, the plane `axis = c` facing
    `sign` (outward; for the courtyard, into the courtyard), the wall top
    `top` (its highest edge), its lowest edge `bottom`, its extent `t0..t1`
    and the spans along its top edge."""

    side: str
    axis: str
    c: float
    sign: float
    top: float
    bottom: float
    t0: float
    t1: float
    spans: list
    outdoor: bool = False


def is_outdoor_cell(layout: L.Layout, cell: str) -> bool:
    return any(s.kind in G.OUTDOOR_KINDS for s in layout.surfaces if s.cell == cell)


def facade_faces(ctx: Context) -> list[FacadeFace]:
    """The outer planes of the cell's facade sides, in side order."""
    from castle_kit.pieces import geom

    fa = ctx.cp.facade
    if fa is None:
        return []
    out = []
    if is_outdoor_cell(ctx.layout, ctx.cp.cell):
        for s in sorted((s for s in ctx.layout.surfaces if s.cell == ctx.cp.cell and s.kind in G.OUTDOOR_KINDS),
                        key=lambda s: s.id):
            for side in fa.sides:
                axis, c, sign, _ = G.side_line(s, side)
                pcs = [pc for pc in ctx.pieces if pc.owner.id == s.id and pc.axis == axis and abs(pc.c - c) < 1e-6
                       and pc.sign == sign]
                if not pcs:
                    continue
                top = max(max(pc.hi.a, pc.hi.b) for pc in pcs)
                spans = G._merge([(pc.ta, pc.tb) for pc in pcs if abs(pc.hi.mid - top) < 0.05])
                out.append(FacadeFace(side, axis, c, sign, top, min(min(pc.lo.a, pc.lo.b) for pc in pcs),
                                      min(pc.ta for pc in pcs), max(pc.tb for pc in pcs), spans, True))
        return out
    plan = facade_shell(ctx.layout)
    for side in fa.sides:
        axis, sign = FACADE_SIDES[side]
        k = 0 if axis == "x" else 2
        planes: dict = {}
        for poly in plan.polys.get((ctx.cp.cell, "wall_exterior"), []):
            n = geom.unit(geom.newell(poly))
            if abs(n[k] - sign) > 1e-3:
                continue
            c = round(poly[0][k], 4)
            ts = [p[2] if axis == "x" else p[0] for p in poly]
            ys = [p[1] for p in poly]
            planes.setdefault(c, []).append((min(ts), max(ts), min(ys), max(ys)))
        for c, rects in sorted(planes.items()):
            top = max(r[3] for r in rects)
            spans = G._merge([(r[0], r[1]) for r in rects if abs(r[3] - top) < 0.05])
            out.append(FacadeFace(side, axis, c, sign, top, min(r[2] for r in rects), min(r[0] for r in rects),
                                  max(r[1] for r in rects), spans))
    return out


def _face_pt(ff: FacadeFace, t: float, y: float, d: float = 0.0):
    return face_point(ff.axis, ff.c, ff.sign, t, y, d)


def m5c_facades(ctx: Context, uses: list[Use]) -> list[Use]:
    """The facade pieces of a cell with a `facade` (castle M5c, 30.5): a slate
    roof over its roof slab (`roof`), corner piers and buttresses between its
    windows with a pinnacle on each (`buttresses`, `pinnacles`), pinnacles at
    the corners of its wall tops (`pinnacles`), a crenellated parapet along
    them (`parapet`), each with its LOD1 beyond grid.FACADE_LOD; a facade as
    tall as FACADE_STOREY_BANDS marks its storeys with string courses."""
    fa = ctx.cp.facade
    if fa is None:
        return uses
    faces = facade_faces(ctx)
    out = list(uses)
    roof = _facade_roof(ctx) if fa.roof else None
    if roof is not None:
        out.append(roof[0])
    buttresses = _facade_buttresses(ctx, faces, roof[1] if roof else None, fa.pinnacles) if fa.buttresses else []
    piers = [u for u in buttresses if u.id.startswith("facade:pier:")]
    corners = _facade_corners(faces) if fa.pinnacles else []
    # (A corner pier carries its corner's pinnacle, or none on a gable.)
    corners = [c for c in corners if not any(abs(c[0] - u.origin[0]) < 1.5 and abs(c[2] - u.origin[2]) < 1.5 for u in piers)]
    for i, (x, y, z, h, w) in enumerate(corners):
        out.append(Use(f"facade:pinnacle:corner:{i}", "facade", G.PINNACLE, _o((x, y, z)), 0.0,
                       _p(height=h, width=w), None, G.FACADE_LOD))
    if fa.parapet:
        out += _facade_parapets(ctx, faces, corners)
    out += buttresses
    out += _facade_storeys(ctx, faces)
    out += _facade_windows(ctx, faces)  # castle M5c review
    return out


def _facade_roof(ctx: Context):
    """(the roof's Use, its frame) over the cell's highest roofed floors: the
    rectangle of their outer planes, the ridge along its longer side."""
    from castle_kit.pieces import geom

    ss = [s for s in ctx.layout.surfaces if s.cell == ctx.cp.cell and s.roofed and s.kind != "door"]
    if not ss:
        return None
    ceil = max(s.ceil_y for s in ss)
    top = ceil + G.SLAB
    ss = [s for s in ss if abs(s.ceil_y - ceil) < 0.01]
    rect = [min(s.x0 for s in ss), min(s.z0 for s in ss), max(s.x1 for s in ss), max(s.z1 for s in ss)]
    planes: dict = {"x": [], "z": []}
    for poly in facade_shell(ctx.layout).polys.get((ctx.cp.cell, "wall_exterior"), []):
        n = geom.unit(geom.newell(poly))
        if abs(n[1]) > 0.5 or abs(max(p[1] for p in poly) - top) > 0.05:
            continue
        axis, k = ("x", 0) if abs(n[0]) > 0.5 else ("z", 2)
        planes[axis].append((poly[0][k], 1.0 if n[k] > 0 else -1.0))
    for i, (axis, sign) in enumerate((("x", -1.0), ("z", -1.0), ("x", 1.0), ("z", 1.0))):
        edge = rect[i]
        got = [c for c, s in planes[axis] if s == sign and 0.0 <= (c - edge) * sign <= G.WALL_MASS_MAX]
        if got:
            rect[i] = min(got, key=lambda c: abs(c - edge))
    x0, z0, x1, z1 = (_r(v) for v in rect)
    along_x = (x1 - x0) >= (z1 - z0)
    span, length = (z1 - z0, x1 - x0) if along_x else (x1 - x0, z1 - z0)
    span, length = float(G.fmt_m(span)), float(G.fmt_m(length))
    name = G.piece_name("roof", "slate", span, length)
    if along_x:
        origin, yaw = (x0, top, (z0 + z1) / 2.0), 0.0
    else:
        origin, yaw = ((x0 + x1) / 2.0, top, z0), -math.pi / 2
    u = Use("facade:roof", "facade", name, _o(origin), _r(yaw),
            _p(pitch=ROOF_PITCH, overhang=ROOF_OVERHANG, verge=ROOF_OVERHANG, cornice=ROOF_CORNICE), None, G.FACADE_LOD)
    return u, {"along_x": along_x, "span": span, "top": top, "ridge": (z0 + z1) / 2.0 if along_x else (x0 + x1) / 2.0}


def _roof_limit(ff: FacadeFace, frame: dict | None, t: float, half_w: float) -> float:
    """The height a piece standing on the face at t (half_w wide) stays under:
    the eave's cornice on an eave side, the gable's outline on a gable side,
    the wall top without a roof."""
    if frame is None:
        return ff.top
    eave_side = (ff.axis == "z") == frame["along_x"]
    if eave_side:
        return ff.top - ROOF_CORNICE - 0.2
    from castle_kit.pieces import facades as FP

    off = min(frame["span"] / 2.0, abs(t - frame["ridge"]) + half_w)
    return ff.top + FP.roof_underside(frame["span"], ROOF_PITCH, off) - 0.2


def _facade_corners(faces: list[FacadeFace]) -> list[tuple]:
    """Corner pinnacles: (x, y, z, height, width) at both ends of each face's
    top edge where it ends the face (its extent), standing on the wall top
    inside the corner (a building's) or on the corner square of the
    courtyard's walls; one per corner."""
    out: list[tuple] = []
    for ff in faces:
        if not ff.spans:
            continue
        for t, tin in ((ff.spans[0][0], 1.0), (ff.spans[-1][1], -1.0)):
            if abs(t - ff.t0) > 1e-3 and abs(t - ff.t1) > 1e-3:
                continue
            if ff.outdoor:
                h, w = FACADE_PINNACLE_H[2], FACADE_PINNACLE_W[1]
                # Into the corner square of the two walls' masses: back from the face and past the end.
                along, back = -tin * (w / 2.0), -w / 2.0
            else:
                h = FACADE_PINNACLE_H[0] if ff.top >= FACADE_PINNACLE_TALL else FACADE_PINNACLE_H[1]
                w = FACADE_PINNACLE_W[0]
                along, back = tin * (w / 2.0 - 0.05), -(w / 2.0 - 0.05)
            x, y, z = _face_pt(ff, t + along, ff.top, back)
            if any(abs(x - o[0]) < 1.0 and abs(z - o[2]) < 1.0 and abs(y - o[1]) < 0.5 for o in out):
                continue
            out.append((_r(x), _r(y), _r(z), h, w))
    return out


def _gatehouse_span(ctx: Context, ff: FacadeFace) -> list[tuple[float, float]]:
    """The courtyard's gatehouse (Courtyard: a kit_gatehouse_* hero) along the
    face it stands on, whose own top crowns it: the hero's footprint, or,
    before it exists, the gate arch's with its quoins."""
    from castle_kit import pieces as P
    from castle_kit.pieces import geom

    out = []
    k, ck = (2, 0) if ff.axis == "x" else (0, 2)
    for h in ctx.cp.hero:
        if not h.piece.startswith(G.GATEHOUSE):
            continue
        pc = P.build(h.piece, **dict(h.args))
        pl = geom.Placement(h.pos, h.yaw)
        pts = [pl.point(q) for f in pc.faces for q in f.pts]
        if min(abs(q[ck] - ff.c) for q in pts) > G.WALL_MASS_MAX:
            continue
        out.append((min(q[k] for q in pts) - 0.05, max(q[k] for q in pts) + 0.05))
    if out:
        return out
    for op in ctx.cp.openings:
        if op.kind == "arch" and op.axis == ff.axis and abs(op.c - ff.c) < 1e-6 and op.sign == ff.sign \
                and op.door is not None and ctx.layout.by_id(op.door).cell == ctx.cp.cell:
            out.append((op.t0 - 0.85, op.t1 + 0.85))
    return out


def facade_parapet_holes(ctx: Context, ff: FacadeFace, corners: list[tuple]) -> list[tuple]:
    """Where a face's parapet breaks: the window over the front doors rising
    through the courtyard's wall (grid.FRONT_WINDOW_OUTSIDE and the outer
    tracery's screen), the courtyard's gatehouse, a building's corner
    pinnacles."""
    holes = []
    if ff.outdoor:
        if ff.axis == "z" and ff.sign > 0:
            x0, x1 = G.FRONT_WINDOW_OUTSIDE[0], G.FRONT_WINDOW_OUTSIDE[1]
            holes.append((x0 - TRACERY_MARGIN, x1 + TRACERY_MARGIN, "cap"))
        holes += [(a, b, "cap") for a, b in _gatehouse_span(ctx, ff)]
        return holes
    for x, _y, z, _h, w in corners:
        t, c = (x, z) if ff.axis == "z" else (z, x)
        if abs(c - ff.c) <= w + 0.1:
            holes.append((t - w / 2.0 - 0.06, t + w / 2.0 + 0.06, "cap"))
    return holes


def _facade_parapets(ctx: Context, faces: list[FacadeFace], corners: list[tuple]) -> list[Use]:
    """A crenellated parapet along each face's top edge (runs of at most 60
    m), broken at facade_parapet_holes; at the courtyard's inside corners the
    x-line runs stop at the z-line runs' fronts."""
    prm = FACADE_PARAPET
    out = []
    for ff in faces:
        holes = facade_parapet_holes(ctx, ff, corners)
        k = 0
        for t0, t1 in ff.spans:
            if ff.outdoor and ff.axis == "x":
                # (Integration: past the z-line run's front and both copings' overhang, so the
                # two runs' merlon copings do not overlap at the corner.)
                t0 += prm["proud"] + PARAPET_CORNER_CLEAR if abs(t0 - ff.t0) < 1e-3 else 0.0
                t1 -= prm["proud"] + PARAPET_CORNER_CLEAR if abs(t1 - ff.t1) < 1e-3 else 0.0
            for a, b, _ka, _kb in _subtract((t0, t1), holes):
                n = max(1, math.ceil((b - a) / 60.0 - 1e-9))
                step = (b - a) / n
                for i in range(n):
                    sa, sb = a + i * step, a + (i + 1) * step
                    if sb - sa < 2.0:
                        continue
                    start = sa if tdir(ff.axis, ff.sign) > 0 else sb
                    out.append(Use(f"facade:parapet:{ff.side}:{k}", "facade", G.PARAPET_CRENEL,
                                   _o(_face_pt(ff, start, ff.top)), yaw_for(ff.axis, ff.sign),
                                   _p(length=_r(sb - sa), **prm), None, G.FACADE_LOD))
                    k += 1
    return out


CORNER_PIER_W = 1.2  # a corner pier's width: it clasps the corner, PIER_PROUD past the side plane
PIER_PROUD = 0.04  # (and its top over the wall top's: no face of it in the shell's planes)
FACADE_STOREY_BANDS = 40.0  # a facade this tall carries a string course at each storey


def _outer_planes(ctx: Context) -> dict:
    """(axis, sign) -> the plane offsets of the cell's vertical outer faces."""
    from castle_kit.pieces import geom

    out: dict = {}
    for poly in facade_shell(ctx.layout).polys.get((ctx.cp.cell, "wall_exterior"), []):
        n = geom.unit(geom.newell(poly))
        for axis, k in (("x", 0), ("z", 2)):
            if abs(abs(n[k]) - 1.0) < 1e-6:
                out.setdefault((axis, 1.0 if n[k] > 0 else -1.0), set()).add(round(poly[0][k], 4))
    return out


def _corner_reach(planes: dict, ff: FacadeFace, end: int) -> tuple[float, bool]:
    """Where a face's end corner lies along it (the side plane it meets) and
    whether the shell leaves the corner open (the face stops short of it)."""
    other = "x" if ff.axis == "z" else "z"
    t = ff.t0 if end == 0 else ff.t1
    sgn = -1.0 if end == 0 else 1.0
    got = [c for c in planes.get((other, sgn), ()) if 0.0 <= (c - t) * sgn <= G.EXTERIOR_SHELL + 0.05]
    if not got:
        return t, False
    c = max(got, key=lambda c: (c - t) * sgn)
    return c, abs(c - t) > 0.05


def _facade_buttresses(ctx: Context, faces: list[FacadeFace], roof: dict | None, pinnacles: bool) -> list[Use]:
    """Corner piers and buttresses on the faces that stand on the ground or on
    the courtyard's wall top (a face starting higher, the Great Hall's gable
    over the hall, has nothing to stand on):

    - a pier clasping each end corner, flush with the side plane: where the
      shell leaves the corner open (a floor below, beyond it, stops the
      Planner closing it) the pier runs on behind the face to close it and
      rises to the wall top, with the corner's pinnacle on it (none under a
      gable); a closed corner takes a buttress like the others;
    - a buttress in the middle of each stretch between two windows, and along
      a stretch from a corner one every BUTTRESS_BAY; never across a window's
      reveal (BUTTRESS_CLEAR; the outer tracery's screen for a window over a
      door).

    A buttress rises to under the roof (its pinnacle's height below the eave's
    cornice or the gable's outline) and carries a pinnacle on its top."""
    out = []
    planes = _outer_planes(ctx)
    for ff in faces:
        if ff.outdoor or ff.bottom > G.COURT_WALL_TOP + 0.01:
            continue
        ground = ff.bottom <= 1.0
        proud, top_proud = BUTTRESS_PROUD["ground" if ground else "wall_top"]
        ph = BUTTRESS_PINNACLE[0 if ground else 1] if pinnacles else 0.0
        sink = 0.3 if ground else 1.6  # (on a wall top: down into the courtyard wall's mass, hidden)
        foot = ff.bottom - sink
        gable = roof is not None and (ff.axis == "z") != roof["along_x"]
        holes = []
        for op in ctx.cp.openings:
            if op.axis != ff.axis or op.sign != -ff.sign or abs(op.c - ff.c) > G.WALL_MASS_MAX:
                continue
            if op.y1 <= ff.bottom or op.y0 >= ff.top:
                continue
            m = BUTTRESS_CLEAR + (TRACERY_MARGIN if op.over else 0.0)
            holes.append((op.t0 - m, op.t1 + m))
        segs = []
        lo, hi = ff.t0, ff.t1
        for end in (0, 1):
            corner, open_ = _corner_reach(planes, ff, end)
            t = corner + (CORNER_PIER_W / 2.0 - PIER_PROUD) * (1.0 if end == 0 else -1.0)
            if end == 0:
                lo = t + CORNER_PIER_W / 2.0 + 0.2
            else:
                hi = t - CORNER_PIER_W / 2.0 - 0.2
            if open_:
                # (To the wall top: at most BUTTRESS_HEIGHT, its foot raised if need be.)
                top = ff.top + PIER_PROUD
                h = float(G.fmt_m(min(G.BUTTRESS_HEIGHT[1], top - foot)))
                args = dict(proud=proud, top_proud=top_proud, cap="flat", back=G.EXTERIOR_SHELL)
                if not ground or top - h > foot + 1e-6:
                    args["plinth"] = 0.0
                out.append(Use(f"facade:pier:{ff.side}:{end}", "facade", G.piece_name("buttress", None, h),
                               _o(_face_pt(ff, _r(t), top - h)), yaw_for(ff.axis, ff.sign), _p(**args), None,
                               G.FACADE_LOD))
                if ph and not gable:
                    out.append(Use(f"facade:pinnacle:pier:{ff.side}:{end}", "facade", G.PINNACLE,
                                   _o(_face_pt(ff, _r(t), top, top_proud / 2.0)), 0.0,
                                   _p(height=FACADE_PINNACLE_H[1], width=min(FACADE_PINNACLE_W[0], top_proud)), None,
                                   G.FACADE_LOD))
            else:
                segs.append(t)
        holes = G._merge(holes)
        edges = [lo] + [v for h in holes for v in h] + [hi]
        for a, b in zip(edges[0::2], edges[1::2]):
            a, b = max(a, lo), min(b, hi)
            if b - a < BUTTRESS_WIDTH + 0.4:
                continue
            if abs(a - lo) < 1e-6 or abs(b - hi) < 1e-6:
                n = int((b - a) // BUTTRESS_BAY)
                segs += [a + (i + 0.5) * (b - a) / n for i in range(n)]
            else:
                segs.append((a + b) / 2.0)
        for i, t in enumerate(sorted(segs)):
            y_top = _roof_limit(ff, roof, t, top_proud) - ph - 0.2
            h = min(G.BUTTRESS_HEIGHT[1], y_top - foot)
            if h < G.BUTTRESS_HEIGHT[0]:
                continue
            h = float(G.fmt_m(h))
            args = dict(proud=proud, top_proud=top_proud, cap="flat" if ph else "slope")
            if not ground:
                args["plinth"] = 0.0
            out.append(Use(f"facade:buttress:{ff.side}:{i}", "facade", G.piece_name("buttress", None, h),
                           _o(_face_pt(ff, _r(t), foot)), yaw_for(ff.axis, ff.sign), _p(**args), None, G.FACADE_LOD))
            if ph:
                out.append(Use(f"facade:pinnacle:{ff.side}:{i}", "facade", G.PINNACLE,
                               _o(_face_pt(ff, _r(t), foot + h, top_proud / 2.0)), 0.0,
                               _p(height=ph, width=min(FACADE_PINNACLE_W[0], top_proud)), None, G.FACADE_LOD))
    return out


def _facade_storeys(ctx: Context, faces: list[FacadeFace]) -> list[Use]:
    """String courses (kit_stone_string, reused) at each storey (grid.STOREY)
    across a facade FACADE_STOREY_BANDS tall or more, above the courtyard's
    wall top, where its outer face stands at that height (the west range's
    72 m stack): its scale read from the courtyard. Not on the courtyard's own
    walls."""
    from castle_kit.pieces import geom

    out = []
    plan = facade_shell(ctx.layout)
    for ff in faces:
        if ff.outdoor or ff.top < FACADE_STOREY_BANDS:
            continue
        k = 0 if ff.axis == "x" else 2
        rects = []
        for poly in plan.polys.get((ctx.cp.cell, "wall_exterior"), []):
            n = geom.unit(geom.newell(poly))
            if abs(n[k] - ff.sign) > 1e-3 or abs(poly[0][k] - ff.c) > 1e-3:
                continue
            ts = [p[2] if ff.axis == "x" else p[0] for p in poly]
            ys = [p[1] for p in poly]
            rects.append((min(ts), max(ts), min(ys), max(ys)))
        j = 0
        y = G.STOREY * (math.floor(G.COURT_WALL_TOP / G.STOREY) + 1)  # (above the courtyard's wall: what it sees)
        while y <= ff.top - 3.0:
            spans = G._merge([(a, b) for a, b, y0, y1 in rects if y0 <= y - 0.1 and y1 >= y + G.STRING.height + 0.1])
            for a, b in spans:
                if b - a < 2.0:
                    continue
                start = a if tdir(ff.axis, ff.sign) > 0 else b
                out.append(Use(f"facade:storey:{ff.side}:{j}", "facade", "kit_stone_string", _o(_face_pt(ff, start, y)),
                               yaw_for(ff.axis, ff.sign), _p(length=_r(b - a), ends=["cap", "cap"]), None))
                j += 1
            y += G.STOREY
    return out


# -- castle M5c review (castle-kit.md): blind windows on the tall facades ----------------

BLIND_SILL = 1.5  # a blind window's sill above its storey line (the string course's top at 0.3)
BLIND_PITCH = 7.5  # at most this between blind windows along a storey
BLIND_MARGIN = 2.5  # and none nearer a stretch's end (the corner pinnacles, the next plane's corner)


def _facade_windows(ctx: Context, faces: list[FacadeFace]) -> list[Use]:
    """Blind lancets (grid.BLIND_WINDOW) in every storey band between the
    string courses of a facade FACADE_STOREY_BANDS tall (the west range's
    72 m stack and common room A's tower, whose portrait-lined or furnished
    insides take no cut), evenly along each stretch of outer face that stands
    whole over the window's height: the stack reads as a windowed tower from
    the courtyard. Not on the courtyard's own walls."""
    from castle_kit import pieces as P
    from castle_kit.pieces import geom

    h = P.parse(G.BLIND_WINDOW)[2]
    out = []
    plan = facade_shell(ctx.layout)
    for ff in faces:
        if ff.outdoor or ff.top < FACADE_STOREY_BANDS:
            continue
        k = 0 if ff.axis == "x" else 2
        rects = []
        for poly in plan.polys.get((ctx.cp.cell, "wall_exterior"), []):
            n = geom.unit(geom.newell(poly))
            if abs(n[k] - ff.sign) > 1e-3 or abs(poly[0][k] - ff.c) > 1e-3:
                continue
            ts = [p[2] if ff.axis == "x" else p[0] for p in poly]
            ys = [p[1] for p in poly]
            rects.append((min(ts), max(ts), min(ys), max(ys)))
        j = 0
        y = G.STOREY * (math.floor(G.COURT_WALL_TOP / G.STOREY) + 1)  # the first storey line above the courtyard's wall
        while y + G.STOREY <= ff.top + 1.0:
            sill = y + BLIND_SILL
            spans = G._merge([(a, b) for a, b, y0, y1 in rects if y0 <= sill - 0.4 and y1 >= sill + h + 0.8])
            for a, b in spans:
                room = b - a - 2 * BLIND_MARGIN
                if room < 0:
                    continue
                count = int(math.floor(room / BLIND_PITCH + 1e-9)) + 1
                step = room / (count - 1) if count > 1 else 0.0
                t0 = a + BLIND_MARGIN if count > 1 else (a + b) / 2
                for i in range(count):
                    out.append(Use(f"facade:window:{ff.side}:{j}", "facade", G.BLIND_WINDOW,
                                   _o(_face_pt(ff, _r(t0 + i * step), sill)), yaw_for(ff.axis, ff.sign), (), None,
                                   G.FACADE_LOD))
                    j += 1
            y += G.STOREY
    return out


# =====================================================================================
# Castle M5c (Courtyard): docs/art/castle-kit.md "The kitted cells". The courtyard only
# (grid.M5C_CELLS); every other cell's placements are untouched (the hall's court
# dressing is plain hero placements).
# =====================================================================================

# Arches the courtyard kit frames whole: the gatehouse's towers stand against the
# courtyard gate's jambs, so its arch keeps its rim and hood but no quoins (they would lie
# in the towers' faces).
M5C_NO_QUOINS = (("courtyard", "courtyard_gate"),)


def m5c_courtyard(ctx: Context, uses: list[Use]) -> list[Use]:
    """The courtyard rules on top of the others:

    - every arcade bay (grid.ARCADE_BAY) draws its LOD1 beyond grid.FACADE_LOD
      (at every quality, the existing kit_lod mechanism);
    - the arches of M5C_NO_QUOINS keep no quoins.

    The plinth breaks round the gatehouse by its meta "wall" (as round the
    counter); the rest is the cell's hero placements."""
    cp = ctx.cp
    if cp.cell not in G.M5C_CELLS:
        return uses
    from dataclasses import replace

    plain = {d for c, d in M5C_NO_QUOINS if c == cp.cell}
    runs = {r.id: r for r in cp.runs}
    out = []
    for u in uses:
        if u.role == "hero" and u.piece == G.ARCADE_BAY:
            u = replace(u, lod_distance=G.FACADE_LOD)
        elif u.role == "arch" and u.id.rsplit(":arch:", 1)[-1] in plain:
            u = replace(u, params=_p(**dict(u.kwargs(), quoins=False)))
        elif u.role == "cornice" and u.run in runs:
            out += _m5c_window_cornice(u, runs[u.run])
            continue
        out.append(u)
    return out


CORNICE_WINDOW_CLEAR = 0.05  # the cornice stops this far short of the parapet ends over the window (no shared end plane)


def _m5c_window_cornice(u: Use, run: G.WallRun) -> list[Use]:
    """(Integration, castle-kit.md) The courtyard's wall-top cornice on its
    north wall breaks where the window over the front doors rises through
    the wall (grid.FRONT_WINDOW_OUTSIDE and the outer tracery's screen,
    TRACERY_MARGIN either side, as the parapet above it), capped at the
    break; elsewhere unchanged."""
    m = TRACERY_MARGIN + CORNICE_WINDOW_CLEAR
    x0, x1 = G.FRONT_WINDOW_OUTSIDE[0] - m, G.FRONT_WINDOW_OUTSIDE[1] + m
    if run.axis != "z" or run.sign <= 0 or abs(run.c - G.COURT_RECT[1]) > 1e-6:
        return [u]
    kw = u.kwargs()
    length, ends = kw["length"], list(kw["ends"])
    d = tdir(run.axis, run.sign)
    a = u.origin[0] if d > 0 else u.origin[0] - length
    b = a + length
    if b <= x0 + 1e-6 or a >= x1 - 1e-6:
        return [u]
    end_a, end_b = (ends[0], ends[1]) if d > 0 else (ends[1], ends[0])
    out = []
    for k, (t0, t1, e0, e1) in enumerate(((a, x0, end_a, "cap"), (x1, b, "cap", end_b))):
        if t1 - t0 > 0.5:
            extra = {k2: v for k2, v in kw.items() if k2 not in ("length", "ends")}
            out.append(_keep_lod_m5c(_sweep_use(f"{u.id}:{k}", u.role, u.piece, run, t0, t1, u.origin[1], e0, e1,
                                                 extra), u))
    return out


def _keep_lod_m5c(new: Use, old: Use) -> Use:
    """`new` with `old`'s LOD distance and group (a split keeps them)."""
    from dataclasses import replace

    return replace(new, lod_distance=old.lod_distance, group=old.group)


# =====================================================================================
# Docs/art/castle-kit.md. The Grand Staircase's moving
# flights (swing groups), its landing gates' edges, newels and the pier's central column;
# the Clock Tower Courtyard's parapet over the crag; the covered bridge's kind.
# =====================================================================================

STAIRS_PATH = os.path.join(_BLENDER, "..", "..", "data", "world", "stairs.json")
GS_CELLS = ("grand_staircase", "grand_staircase_upper")
GATE_FASCIA_BACK = 0.01  # the fascia under an A gate stands this far behind the landing's edge (a resting flight's crown in front)
NEWEL_DY = 0.6  # two newels this close in height at one spot are one (the M4 dedupe merged every height)
NEWEL_NEAR = 0.45  # ... and this close in plan (two gates' hinges at a pier corner share one)
CLOCK_PARAPET = 1.2  # the Clock Tower Courtyard's south wall: a parapet this high over the crag
CLOCK_PARAPET_RUNS = ("clock_courtyard:s0", "clock_courtyard:s1")
CLOCK_PASSAGE_QUOINS = ("quoin:clock_courtyard:w0:b", "quoin:clock_courtyard:w1:a")


def stairs_file() -> dict:
    """data/world/stairs.json (cached)."""
    if "stairs" not in _PLANNER_CACHE:
        import json

        with open(STAIRS_PATH, encoding="utf-8") as f:
            _PLANNER_CACHE["stairs"] = json.load(f)
    return _PLANNER_CACHE["stairs"]


def swing_group(flight: str) -> str:
    """The swing node's group name (grid.SWING_NODE: `<cell>.castle_props.swing_<flight>`)."""
    return f"swing_{flight}"


def moving_flights(layout: L.Layout, cell: str) -> list[dict]:
    """stairs.json's flights whose A surface is in `cell` (the cell draws their swing nodes)."""
    ids = {s.id for s in layout.surfaces if s.cell == cell}
    return [f for f in stairs_file()["flights"] if f["a"] in ids]


_colonnettes_m4 = colonnettes


def colonnettes(cp: S.CellPlan) -> list[tuple[float, float, float, float]]:  # noqa: F811
    """M4's colonnettes less the pier stack's open corners (castle_kit.json
    `args.open` of the kit_pier_stack hero: the corners a moving half-flight sweeps)."""
    gone = []
    heroes = [(h.piece, h.pos, dict(h.args)) for h in cp.hero]
    for cid in sorted(gallery_cells(cp) - {cp.cell}):
        for h in _kit_cells().get(cid, {}).get("hero", []):
            heroes.append((h["piece"], tuple(h["pos"]), dict(h.get("args", {}))))
    from castle_kit import pieces as P
    from castle_kit.pieces import wings_a

    for piece, pos, args in heroes:
        if piece.startswith("kit_pier_stack"):
            size = P.parse(piece)[1]
            gone += [(pos[0], pos[2], sx, sz, y0)
                     for sx, sz, y0 in wings_a.open_corners(size, args.get("top", 49.5), args.get("open", 0))]
    return [c for c in _colonnettes_m4(cp)
            if not any((c[0] - px) * sx > 0 and (c[1] - pz) * sz > 0 and abs(c[2] - y0) < 1e-6
                       for px, pz, sx, sz, y0 in gone)]


def p3c_wings_a(ctx: Context, uses: list[Use]) -> list[Use]:
    cp = ctx.cp
    if cp.cell in GS_CELLS:
        uses = _p3c_staircase(ctx, uses)
    if cp.cell == "clock_courtyard":
        uses = _p3c_clock_court(ctx, uses)
    if cp.budget == "bridge":
        # The bridge kind draws only its hero (kit_bridge_covered: deck, trusses, railings, roof),
        # its LOD1 beyond the facades' distance (it is seen from the grounds).
        from dataclasses import replace

        uses = [replace(u, lod_distance=G.FACADE_LOD) for u in uses if u.role == "hero"]
    return uses


def _p3c_staircase(ctx: Context, uses: list[Use]) -> list[Use]:
    from dataclasses import replace

    layout = ctx.layout
    flights = moving_flights(layout, ctx.cp.cell)
    a_ids = {f["a"]: f for f in stairs_file()["flights"]}
    own = {f["a"]: swing_group(f["id"]) for f in flights}
    out = []
    for u in uses:
        if u.role == "newel":
            continue  # recomputed below, per height
        if u.run in own and u.role in ("flight", "balustrade"):
            u = replace(u, group=own[u.run], lod_distance=None)
        out.append(u)
    gates = [g for f in stairs_file()["flights"] for g in f["gates"] if layout.by_id(g["landing"]).cell == ctx.cp.cell]
    out = _gate_edges(ctx, out, gates)
    # Newels: the railings' ends at their own heights (the moving flights' own ends stand on
    # the gates' hinge newels instead), one per spot and height, the hero newels' spots
    # kept clear, a corner shared with the other cell only in the lower one.
    heroes = _hero_newels(ctx)
    raw = [u for u in _balustrades(ctx) if u.role == "newel" and u.run not in a_ids]
    for g in gates:
        s = layout.by_id(g["landing"])
        for k, (x, z) in enumerate(g["line"]):
            raw.append(Use(f"newel:gate:{g['id']}:{k}", "newel", "kit_newel_ball", _o((x, s.y, z)),
                           _r(math.atan2(g["opens"][0], g["opens"][1])), (), s.id))
    _, rails_all = _planner_all(layout)
    gs_cells = gallery_cells(ctx.cp)
    rails = {k: v for k, v in rails_all.items() if layout.by_id(k[0]).cell in gs_cells}
    kept: list[Use] = []
    for u in sorted(raw, key=lambda u: (not u.id.startswith("newel:gate:"), u.id)):
        x, y, z = u.origin
        if any(math.hypot(h.pos[0] - x, h.pos[2] - z) < 0.35 and abs(h.pos[1] - y) < 1.0 for h in heroes):
            continue
        if any(math.hypot(v.origin[0] - x, v.origin[2] - z) < NEWEL_NEAR and abs(v.origin[1] - y) < NEWEL_DY for v in kept):
            continue
        if ctx.cp.cell != min(gs_cells) and _shared_corner(rails, layout, ctx.cp.cell, x, z, y):
            continue
        kept.append(u)
    return out + kept


def _gate_span(axis: str, g: dict) -> tuple[float, float, float] | None:
    """(c, t0, t1) of a gate line on an edge along `axis` (None: the line runs the other way)."""
    (xa, za), (xb, zb) = g["line"]
    if axis == "z" and abs(za - zb) < 1e-6:
        return za, min(xa, xb), max(xa, xb)
    if axis == "x" and abs(xa - xb) < 1e-6:
        return xa, min(za, zb), max(za, zb)
    return None


def _gate_edges(ctx: Context, uses: list[Use], gates: list) -> list[Use]:
    """Each gate's landing edge: where a gallery edge runs (a railing edge with the flight
    away, the B gates), its balustrade and dressing stop over the gate (the Planner's
    slab fascia stays; the resting flight meets it); where the edge is a walkable join
    with the flight at A (the A gates), the plain slab fascia the Planner leaves out."""
    out = list(uses)
    for g in gates:
        s = ctx.layout.by_id(g["landing"])
        hit = False
        res = []
        for u in out:
            if u.role != "gallery_edge" or u.run != s.id:
                res.append(u)
                continue
            axis = "z" if abs(u.yaw) < 1e-3 or abs(abs(u.yaw) - math.pi) < 1e-3 else "x"
            span = _gate_span(axis, g)
            if span is None or abs((u.origin[2] if axis == "z" else u.origin[0]) - span[0]) > 1e-3:
                res.append(u)
                continue
            parts = _split_edge(u, axis, span[1], span[2])
            if parts is None:
                res.append(u)
                continue
            hit = True
            res += parts
        out = res
        if not hit:
            (xa, za), (xb, zb) = g["line"]
            ox, oz = -g["opens"][0], -g["opens"][1]  # the void side
            length = math.hypot(xb - xa, zb - za)
            # Local +z toward the void, local x along the line (yaw_for: +z is the face normal).
            axis = "z" if abs(za - zb) < 1e-6 else "x"
            sign = oz if axis == "z" else ox
            d = tdir(axis, sign)
            t0, t1 = (min(xa, xb), max(xa, xb)) if axis == "z" else (min(za, zb), max(za, zb))
            c = za if axis == "z" else xa
            start = t0 if d > 0 else t1
            o = face_point(axis, c, sign, start, s.y, -GATE_FASCIA_BACK)
            out.append(Use(f"gate_fascia:{g['id']}", "gate_fascia", "kit_slab_fascia", _o(o), yaw_for(axis, sign),
                           _p(length=length), s.id))
    return out


def _split_edge(u: Use, axis: str, g0: float, g1: float) -> list[Use] | None:
    """A kit_gallery_edge use less its balustrade and dressing over [g0, g1] (world t);
    None when the gate does not overlap it."""
    kw = u.kwargs()
    length = kw["length"]
    sign = 1.0 if (axis == "z" and abs(u.yaw) < 1e-3) or (axis == "x" and u.yaw > 0) else -1.0
    d = tdir(axis, sign)
    start = u.origin[0] if axis == "z" else u.origin[2]
    la, lb = sorted(((g0 - start) * d, (g1 - start) * d))
    la, lb = max(0.0, la), min(length, lb)
    if lb - la < 0.05:
        return None  # the gate is not on this run
    ends, inset = list(kw["ends"]), list(kw["inset"])
    drops = [list(x) for x in kw["drops"]]
    out = []
    for k, (a, b) in enumerate(((0.0, la), (lb, length))):
        if b - a < 0.05:
            continue
        part_drops = []
        for x0, x1, var in drops:
            p0, p1 = max(x0, a), min(x1, b)
            if p1 - p0 > 0.05:
                part_drops.append([_r(p0 - a), _r(p1 - a), var])
        e = [ends[0] if a < 1e-6 else "cap", ends[1] if b > length - 1e-6 else "cap"]
        ins = [inset[0] if a < 1e-6 else NEWEL_HALF, inset[1] if b > length - 1e-6 else NEWEL_HALF]
        t = start + d * a
        y = u.origin[1]
        o = (t, y, u.origin[2]) if axis == "z" else (u.origin[0], y, t)
        out.append(Use(f"{u.id}:{k}", u.role, u.piece, _o(o), u.yaw,
                       _p(length=_r(b - a), ends=e, inset=ins, drops=part_drops or [[0.0, _r(b - a), "none"]]),
                       u.run, u.lod_distance, u.group))
    return out


def _p3c_clock_court(ctx: Context, uses: list[Use]) -> list[Use]:
    """The Clock Tower Courtyard: the south wall is a parapet over the crag (assemble.py
    cuts the Planner's faces; its wall-top cornice and quoins go), a coping along its top;
    the arcades' LOD1 beyond grid.FACADE_LOD (the M5c courtyard's)."""
    from dataclasses import replace

    out = []
    for u in uses:
        if u.run in CLOCK_PARAPET_RUNS and u.role in ("cornice", "quoin", "string"):
            continue
        if u.role == "quoin" and (abs(u.origin[2] - 74.0) < 1.5 or u.id in CLOCK_PASSAGE_QUOINS):
            continue  # (the parapet's corners; the passage's mouth: its ceiling cuts them)
        if u.role == "hero" and u.piece == G.ARCADE_BAY:
            u = replace(u, lod_distance=G.FACADE_LOD)
        out.append(u)
    runs = {r.id: r for r in ctx.cp.runs}
    for rid in CLOCK_PARAPET_RUNS:
        r = runs.get(rid)
        if r is None:
            continue
        d = tdir(r.axis, r.sign)
        start = r.t0 if d > 0 else r.t1
        o = face_point(r.axis, r.c, r.sign, start, r.floor + CLOCK_PARAPET)
        out.append(Use(f"coping:{rid}", "coping", "kit_parapet_coping", _o(o), yaw_for(r.axis, r.sign),
                       _p(length=_r(r.t1 - r.t0)), r.id))
    return out


# =====================================================================================
# Docs/art/castle-kit.md. The hidden passages (budget kind
# `passage`, grid.P3D_CELLS) and their entrances in the ten public rooms beside them.
# - Every arch of a passage door (`secret` door surfaces), on either side, keeps its
#   spandrels and soffit but no rim, hood or quoins (like M5B_PLAIN_ARCHES): on the public
#   side the entrance piece closes the opening. Inside, the door is as high as the passage
#   (3.5 m), so the passage side draws no arch: the opening is the passage's own section and
#   the public side's pointed head shows 0.5 m before the entrance piece.
# - In a passage the steps run between walls down to their floor: solid flights with a
#   kit_stair_skirt (as the M5b basement steps); the walls are rough stone, so no
#   outside-corner quoins and no moulded cornice or plinth.
# - The entrance pieces (castle_kit.json heroes `entrance_<door>`) and the
#   Wishing Room's door (hero `wishing_room_door`) each go in their own named group, one
#   castle_props node (grid.ENTRANCE_NODE, grid.WISHING_ROOM_DOOR_NODE; assemble.py
#   gives them their kit_anim and origin).
# =====================================================================================

P3D_ENTRANCE_PREFIX = "entrance_"
P3D_DOOR_HERO = "wishing_room_door"


def secret_doors(layout: L.Layout) -> set:
    """The passage doors: every `door` surface marked secret."""
    return {s.id for s in layout.surfaces if s.kind == "door" and getattr(s, "secret", False)}


def p3d_group(u: Use) -> str | None:
    """The moving node's group of a secrets hero (None: not one)."""
    if u.role != "hero":
        return None
    hid = u.id.split(":", 1)[1]
    if hid.startswith(P3D_ENTRANCE_PREFIX) or hid == P3D_DOOR_HERO:
        return hid
    return None


def p3d_secrets(ctx: Context, uses: list[Use]) -> list[Use]:
    from dataclasses import replace

    cp = ctx.cp
    doors = secret_doors(ctx.layout)
    passage = cp.budget == "passage"
    out = []
    for u in uses:
        if u.role == "arch" and u.id.rsplit(":arch:", 1)[-1] in doors:
            if passage:
                continue  # the door is as high as the passage: its opening is the passage's own section
            u = replace(u, params=_p(**dict(u.kwargs(), quoins=False, rim=False)))
        elif passage and (u.id.startswith("quoin:") or u.role in ("cornice", "plinth")):
            continue  # rough stone: no quoins, no moulded cornice or plinth (the warm trim)
        elif passage and u.role == "flight":
            kw = u.kwargs()
            kw["solid"] = True
            kw.pop("strip", None)
            u = replace(u, params=_p(**kw))
            out.append(Use(f"skirt:{u.run}", "skirt", "kit_stair_skirt", u.origin, u.yaw,
                           _p(width=kw["width"], length=kw["length"], rise=kw["rise"]), u.run))
        grp = p3d_group(u)
        if grp is not None:
            u = replace(u, group=grp)
        out.append(u)
    return out
