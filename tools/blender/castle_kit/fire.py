"""Animated fire: the art side of the client's flames (docs/architecture.md
"Rendering"; pure Python, no `bpy`).

Fires are not modelled any more: a piece that burns (a hearth, a brazier, a
torch, a candle) keeps its logs, coals and wax, and lists where its flames
stand in its meta. The exports write those lists into the manifests and the
client draws every flame as a camera-facing card whose shape, colour and
motion its shader makes (client/src/game/fire/), so a fire moves without
any CPU work per flame and its colours stay palette data
(`toon_palette.json` `fire`).

In a piece's meta (the piece's own frame, like its "lights"):

    "fires":   [{"at": (x, y, z), "tongues": [(dx, dy, dz, h, w), ...],
                 "pool": r, "floor": y, "embers": n}, ...]
    "candles": [(x, y, z, h), ...]

A fire's `tongues` stand on `at` + (dx, dy, dz) (their foot), `h` metres
tall and `w` wide at the belly, listed back to front (the client draws
them in that order); `pool` is the radius of the warm glow it throws on
the floor at height `floor` under `at` (0: none) and `embers` how many
sparks rise from it.
A candle is one small flame whose foot is at (x, y, z), `h` tall.

The manifest form (`fire` on a world manifest cell, on an instance room's
entry), positions in world metres rounded to millimetres:

    {"fires":   [{"at": [x, y, z], "seed": s, "tongues": [[dx, dy, dz, h, w], ...],
                  "pool": r, "floor": y, "embers": n}, ...],
     "candles": [[x, y, z, h], ...],
     "groups":  {"<node name>": [[x, y, z, h], ...]}}

`groups` holds the candles of a moving node (a floating candle group, a
ring chandelier): their positions are where they stand at rest and the
client moves their flames with that node. `seed` (0..1) is the fire's
flicker phase: the client pulses the fire's flames and floor glow with it
and flickers any light that carries the same `flicker` (`link_lights`).
"""

from __future__ import annotations

import math

# The flames' sizes (metres): a hearth tongue, a candle flame.
CANDLE_H = 0.13
CANDLE_W = 0.055
# A light this close to a fire's anchor (metres) flickers with it.
LINK_DISTANCE = 2.5


def seed_of(p) -> float:
    """A deterministic flicker phase (0..1) from a position (rounded to the
    centimetre first, so float noise in a placement never changes it)."""
    x, y, z = (int(round(v * 100.0)) for v in p)
    h = (x * 73856093) ^ (y * 19349663) ^ (z * 83492791)
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return round(((h ^ (h >> 16)) & 0xFFFF) / 65536.0, 4)


def hearth_tongues(width: float, height: float, depth: float = 0.3, rows: int = 2, count: int = 7,
                   seed: int = 1) -> list[tuple[float, float, float, float, float]]:
    """A layered bed of flames on a hearth `width` wide: `rows` rows from the
    back (tallest, `height`) to the front (shorter), `depth` deep, `count`
    tongues in all, tallest in the middle of each row, a little jitter by
    `seed` (deterministic). Back to front, as the client draws them."""
    rnd = _Lcg(seed)
    out = []
    per = [count // rows + (1 if r < count % rows else 0) for r in range(rows)]
    for r in range(rows):
        n = per[r]
        z = -depth / 2.0 + depth * (r / max(1, rows - 1))  # back row first
        row_h = height * (1.0 - 0.28 * r / max(1, rows - 1))
        for i in range(n):
            t = (i + 0.5) / n
            x = (t - 0.5) * width + (rnd.next() - 0.5) * width / n * 0.5
            middle = 1.0 - abs(t - 0.5) * 1.1  # tallest in the middle
            h = row_h * (0.55 + 0.45 * middle) * (0.88 + 0.24 * rnd.next())
            w = max(0.12, min(width / n * 1.25, h * 0.42)) * (0.9 + 0.2 * rnd.next())
            out.append((round(x, 3), 0.0, round(z + (rnd.next() - 0.5) * 0.06, 3), round(h, 3), round(w, 3)))
    return out


def cluster_tongues(radius: float, height: float, count: int = 5, seed: int = 1) -> list:
    """A round cluster of flames (a brazier's basket, a torch's head):
    the tallest in the middle, the others round it, back to front along -z."""
    rnd = _Lcg(seed)
    out = [(0.0, 0.0, 0.0, round(height, 3), round(max(0.06, height * 0.42), 3))]
    for i in range(count - 1):
        a = 2.0 * math.pi * (i + 0.3 * rnd.next()) / max(1, count - 1)
        r = radius * (0.55 + 0.35 * rnd.next())
        h = height * (0.55 + 0.25 * rnd.next())
        out.append((round(r * math.cos(a), 3), 0.0, round(r * math.sin(a), 3), round(h, 3),
                    round(max(0.05, h * 0.45), 3)))
    out.sort(key=lambda t: t[2])
    return out


def fire(at, tongues, pool: float = 0.0, embers: int = 0, floor: float = 0.0) -> dict:
    """A piece meta entry (the piece's frame); `floor` the height its floor
    glow lies at (the piece's frame: 0 its floor)."""
    return {"at": tuple(float(v) for v in at), "tongues": [tuple(t) for t in tongues], "pool": float(pool),
            "floor": float(floor), "embers": int(embers)}


def placed(meta: dict, point) -> tuple[list[dict], list[list[float]]]:
    """A piece's `fires` and `candles` meta through `point` (its placement:
    piece frame -> world), in the manifest's form."""
    fires = []
    for f in meta.get("fires") or []:
        at = point(tuple(f["at"]))
        tongues = []
        for dx, dy, dz, h, w in f["tongues"]:
            q = point((f["at"][0] + dx, f["at"][1] + dy, f["at"][2] + dz))
            tongues.append([_r(q[0] - at[0]), _r(q[1] - at[1]), _r(q[2] - at[2]), _r(h), _r(w)])
        entry = {"at": [_r(v) for v in at], "seed": seed_of(at), "tongues": tongues}
        if f.get("pool"):
            entry["pool"] = _r(f["pool"])
            entry["floor"] = _r(point((f["at"][0], f.get("floor", 0.0), f["at"][2]))[1])
        if f.get("embers"):
            entry["embers"] = int(f["embers"])
        fires.append(entry)
    candles = []
    for x, y, z, h in meta.get("candles") or []:
        q = point((x, y, z))
        candles.append([_r(q[0]), _r(q[1]), _r(q[2]), _r(h)])
    return fires, candles


def manifest(fires: list, candles: list, groups: dict | None = None) -> dict | None:
    """The manifest's `fire` entry (None when nothing burns)."""
    out: dict = {}
    if fires:
        out["fires"] = sorted(fires, key=lambda f: f["at"])
    if candles:
        out["candles"] = sorted(candles)
    g = {k: sorted(v) for k, v in sorted((groups or {}).items()) if v}
    if g:
        out["groups"] = g
    return out or None


def link_lights(lights: list[dict], fires: list[dict], distance: float = LINK_DISTANCE) -> int:
    """Gives each light (dicts with "pos") within `distance` of a fire's
    anchor that fire's seed as its `flicker`, so the client flickers it with
    the flames. Returns how many lights it linked."""
    n = 0
    for lt in lights:
        best, seed = distance, None
        for f in fires:
            d = math.dist(lt["pos"], f["at"])
            if d <= best:
                best, seed = d, f["seed"]
        if seed is not None:
            lt["flicker"] = seed
            n += 1
    return n


def note(mesh, fires=(), candles=()) -> None:
    """For the rooms built straight into one meshkit Mesh (the dorms, the
    duel arena, the Charms classroom): remembers fires (meta form) and
    candles (x, y, z, h) on the mesh, in the room's metres."""
    notes = mesh.__dict__.setdefault("fire_notes", {"fires": [], "candles": []})
    notes["fires"].extend(fires)
    notes["candles"].extend(tuple(c) for c in candles)


def noted(mesh) -> dict | None:
    """The manifest `fire` entry of what `note` remembered on `mesh`."""
    notes = mesh.__dict__.get("fire_notes")
    if not notes:
        return None
    fires, candles = placed(notes, lambda p: p)
    return manifest(fires, candles)


def _r(v: float) -> float:
    return round(float(v), 3)


class _Lcg:
    """A tiny deterministic generator (the same numbers on every machine)."""

    def __init__(self, seed: int) -> None:
        self.s = (int(seed) * 2654435761 + 12345) & 0x7FFFFFFF

    def next(self) -> float:
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF
