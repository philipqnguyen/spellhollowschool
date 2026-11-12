"""The four Herbology class plants, the animated
props of `class_greenhouse` (docs/architecture.md).
Invented plants, cartoonish and readable at a distance:

* `plant_vine`, the Gnashvine: a twisting vine with broad leaves, two curled
  tendrils, and a pod head at its top whose jaws stand open toward +z, a
  row of pale teeth on each.
* `plant_root`, the Shriekroot: a squat root bulb half out of the earth with
  a face toward +z (two dark eyes, a wide open mouth) under a tuft of broad
  leaves.
* `plant_briar`, the Needlebriar: a rounded shrub of dark leaf clumps on a
  short woody stem, bristling with pale thorns.
* `plant_fern`, the Shiverfern: a crown of arching, frosted blue-green
  fronds and two curled fiddleheads.

Each plant's origin is at its base, on its pot's earth (the layout landmark
of the same id, kit_plant_staging's PLANT_SOIL_Y), facing +z (the
students). The client moves each whole by node transforms (sway, lunge,
recoil, snap): no rig, no shape keys, no glTF animation. Pure Python
`meshkit` models (`MODELS`: id -> builder returning (mesh, zones)); colours
are castle_textures.json palette names (zones), painted with the castle
kit's tiles onto the plant's own small atlas (`objects`, inside Blender):
one mesh, one material, one draw; no baked shade. At most PLANT_TRIS
triangles each."""

from __future__ import annotations

import math

import meshkit as mk

PLANT_TRIS = 3_000  # the contract's cap for an animated prop (and 2 draws: these are 1)

# Zone -> palette name (castle_textures.json `palette`); TILE: the castle kit tile it is painted on.
VINE, MAW, TOOTH, LEAF, LEAF_LIGHT, ROOT, DARK, BRIAR, THORN, STEM, FERN = range(1, 12)
ZONES = {VINE: "gnashvine", MAW: "gnashvine_maw", TOOTH: "letter_white", LEAF: "plant", LEAF_LIGHT: "leaf_light",
         ROOT: "shriekroot", DARK: "soot", BRIAR: "needlebriar", THORN: "needlebriar_thorn", STEM: "oak_aged",
         FERN: "shiverfern"}
TILE = {VINE: "leaf", MAW: "fabric", TOOTH: "fabric", LEAF: "leaf", LEAF_LIGHT: "leaf", ROOT: "leather", DARK: "fabric",
        BRIAR: "leaf", THORN: "fabric", STEM: "wood", FERN: "leaf"}


# -- helpers ---------------------------------------------------------------------------------------


def _poly_out(m: mk.Mesh, pts, centre, zone: int) -> None:
    """A polygon wound to face away from `centre`."""
    n = mk.newell(pts)
    c = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
    if mk.dot(n, mk.sub(c, centre)) < 0:
        pts = list(reversed(pts))
    m.poly(pts, TILE[zone], zone=zone)


def _card(m: mk.Mesh, pts, zone: int) -> None:
    """A double-sided card (leaves, fronds)."""
    m.poly(pts, TILE[zone], zone=zone)
    m.poly(list(reversed(pts)), TILE[zone], zone=zone)


def _ellipsoid(m: mk.Mesh, c, r, zone: int, seg: int = 8, rings: int = 5) -> None:
    """An ellipsoid of radii r = (rx, ry, rz) about c."""
    prof = [(math.sin(math.pi * i / rings), -math.cos(math.pi * i / rings)) for i in range(rings + 1)]
    mark = m.mark()
    mk.lathe(m, prof, seg, TILE[zone], zone, cap_top=False)
    m.transform(mark, lambda p: (c[0] + p[0] * r[0], c[1] + p[1] * r[1], c[2] + p[2] * r[2]))


def _spine(m: mk.Mesh, pts, r0: float, r1: float, zone: int, seg: int = 6) -> None:
    """Rods along a polyline, tapering from r0 to r1."""
    n = len(pts) - 1
    for i in range(n):
        r = r0 + (r1 - r0) * i / max(1, n - 1)
        mk.rod(m, pts[i], pts[i + 1], r, seg, TILE[zone], zone, caps=(i == n - 1))
        if i:  # a knuckle at each bend, so the rods' ends do not gape
            _ellipsoid(m, pts[i], (r * 1.05, r * 1.05, r * 1.05), zone, seg, 3)


def _bezier(a, b, c, d, n: int):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append(tuple(u ** 3 * a[k] + 3 * u * u * t * b[k] + 3 * u * t * t * c[k] + t ** 3 * d[k] for k in range(3)))
    return out


def _leaf(m: mk.Mesh, base, direction, length: float, width: float, zone: int, droop: float = 0.3) -> None:
    """A broad leaf from `base` along `direction`, bent down a little at
    its middle: two double-sided quads (base, sides, tip)."""
    d = mk.normalize(direction)
    side = mk.cross(d, (0.0, 1.0, 0.0))
    side = mk.normalize(side) if mk.length(side) > 1e-6 else (1.0, 0.0, 0.0)
    mid = mk.add(base, mk.scale(d, length * 0.5))
    tip = mk.add(base, mk.scale(d, length))
    tip = (tip[0], tip[1] - droop * length, tip[2])
    left = mk.add(mid, mk.scale(side, width / 2))
    right = mk.sub(mid, mk.scale(side, width / 2))
    _card(m, [base, right, left], zone)
    _card(m, [left, right, tip], zone)


def _cone(m: mk.Mesh, base, direction, length: float, radius: float, zone: int, seg: int = 3) -> None:
    """A thin pointed cone (a thorn, a tooth) from `base` along `direction`."""
    d = mk.normalize(direction)
    tip = mk.add(base, mk.scale(d, length))
    a = mk.cross(d, (0.0, 1.0, 0.0))
    if mk.length(a) < 1e-6:
        a = mk.cross(d, (1.0, 0.0, 0.0))
    a = mk.normalize(a)
    b = mk.cross(d, a)
    ring = [mk.add(base, mk.add(mk.scale(a, radius * math.cos(2 * math.pi * k / seg)),
                                mk.scale(b, radius * math.sin(2 * math.pi * k / seg)))) for k in range(seg)]
    for k in range(seg):
        _poly_out(m, [ring[k], ring[(k + 1) % seg], tip], base, zone)


def _rotate_x(p, pivot, angle: float):
    """p turned by `angle` about the x axis through `pivot` (a positive angle lifts what lies toward +z)."""
    y, z = p[1] - pivot[1], p[2] - pivot[2]
    c, s = math.cos(angle), math.sin(angle)
    return (p[0], pivot[1] + y * c + z * s, pivot[2] - y * s + z * c)


# -- the Gnashvine -----------------------------------------------------------------------------


def _jaw(m: mk.Mesh, c, r, upper: bool, gape: float) -> None:
    """Half of the pod head about c (radii r = (rx, ry, rz), its axis along
    +z): the upper or lower shell, the maw's flat face and its teeth, turned
    about the hinge at the head's back by `gape` radians."""
    rx, ry, rz = r
    mark = m.mark()
    nt, nv = 6, 5
    sign = 1.0 if upper else -1.0

    def pt(t, v):
        return (c[0] + rx * math.sin(t) * math.cos(v), c[1] + sign * ry * math.sin(t) * math.sin(v), c[2] + rz * math.cos(t))

    for i in range(nt):
        t0, t1 = math.pi * i / nt, math.pi * (i + 1) / nt
        for j in range(nv):
            v0, v1 = math.pi * j / nv, math.pi * (j + 1) / nv
            pts = [pt(t0, v0), pt(t1, v0), pt(t1, v1), pt(t0, v1)]
            pts = [p for k, p in enumerate(pts) if mk.length(mk.sub(p, pts[k - 1])) > 1e-7]
            if len(pts) >= 3:
                _poly_out(m, pts, c, VINE)
    # The maw: the flat half-ellipse across the cut, facing into the mouth.
    rim = [(c[0] + rx * math.sin(math.pi * k / 12), c[1], c[2] + rz * math.cos(math.pi * k / 12)) for k in range(13)]
    rim += [(c[0] - rx * math.sin(math.pi * k / 12), c[1], c[2] + rz * math.cos(math.pi * k / 12)) for k in range(11, 0, -1)]
    inside = (c[0], c[1] + sign * ry, c[2])
    _poly_out(m, rim, inside, MAW)
    # Teeth round the front of the rim, pointing across the mouth.
    for k in range(1, 6):
        t = 0.25 + 0.22 * k
        for sx in (-1.0, 1.0):
            base = (c[0] + sx * rx * math.sin(t) * 0.92, c[1] - sign * 0.004, c[2] + rz * math.cos(t) * 0.92)
            _cone(m, base, (0.0, -sign, 0.0), 0.045, 0.014, TOOTH)
    hinge = (c[0], c[1], c[2] - rz * 0.8)
    m.transform(mark, lambda p: _rotate_x(p, hinge, sign * gape))


def gnashvine() -> mk.Mesh:
    m = mk.Mesh()
    stalk = _bezier((0.0, 0.0, 0.0), (-0.14, 0.32, -0.12), (0.16, 0.62, -0.06), (0.0, 0.92, 0.06), 9)
    _spine(m, stalk, 0.05, 0.03, VINE)
    for k, (i, a) in enumerate(((2, 0.4), (3, 2.6), (4, 4.6), (5, 1.4), (6, 3.6), (7, 5.6))):
        base = stalk[i]
        d = (math.cos(a), 0.35, math.sin(a))
        _leaf(m, base, d, 0.28 - 0.015 * i, 0.15, LEAF if k % 2 else VINE, droop=0.35)
    # Two tendrils curling off the stalk.
    for i, a in ((3, 1.2), (6, 4.2)):
        p0 = stalk[i]
        curl = [p0]
        for s in range(1, 7):
            ang = a + s * 0.55
            rr = 0.16 * (1 - s / 9)
            curl.append((p0[0] + rr * math.cos(ang) * s / 6 * 1.6, p0[1] + 0.03 * s, p0[2] + rr * math.sin(ang) * s / 6 * 1.6))
        _spine(m, curl, 0.012, 0.006, VINE, seg=4)
    head = (0.0, 1.02, 0.12)
    _jaw(m, head, (0.13, 0.09, 0.17), True, 0.38)
    _jaw(m, head, (0.13, 0.08, 0.17), False, 0.22)
    # The neck into the head's back.
    mk.rod(m, stalk[-1], (0.0, 1.02, 0.0), 0.034, 6, TILE[VINE], VINE)
    return m


# -- the Shriekroot ----------------------------------------------------------------------------


def shriekroot() -> mk.Mesh:
    m = mk.Mesh()
    # The bulb, half sunk in the earth (its bottom under the soil).
    mk.lathe(m, [(0.0, -0.08), (0.13, -0.06), (0.17, 0.04), (0.15, 0.13), (0.09, 0.2), (0.0, 0.22)], 10, TILE[ROOT], ROOT,
             cap_top=False)
    # The face toward +z: eyes, brows and a wide open mouth.
    for sx in (-1.0, 1.0):
        _ellipsoid(m, (sx * 0.055, 0.135, 0.13), (0.026, 0.03, 0.02), DARK, 6, 3)
        mk.rod(m, (sx * 0.02, 0.18, 0.13), (sx * 0.09, 0.165, 0.115), 0.012, 4, TILE[ROOT], ROOT)
    _ellipsoid(m, (0.0, 0.06, 0.155), (0.06, 0.045, 0.025), DARK, 8, 3)
    _ellipsoid(m, (0.0, 0.06, 0.15), (0.07, 0.055, 0.025), MAW, 8, 3)  # the lips round it
    # Root nubs and a pointed nose.
    _cone(m, (0.0, 0.1, 0.15), (0.0, -0.2, 1.0), 0.05, 0.022, ROOT, 5)
    for a in (0.6, 2.3, 4.0, 5.3):
        _cone(m, (0.16 * math.cos(a), 0.02, 0.16 * math.sin(a)), (math.cos(a), -0.6, math.sin(a)), 0.09, 0.025, ROOT, 4)
    # The tuft of broad leaves.
    for k in range(8):
        a = 2 * math.pi * k / 8 + 0.2
        d = (math.cos(a) * 0.55, 1.0, math.sin(a) * 0.55)
        if math.sin(a) > 0.6:  # keep the face clear
            d = (math.cos(a) * 0.3, 1.0, math.sin(a) * 0.2)
        _leaf(m, (0.02 * math.cos(a), 0.2, 0.02 * math.sin(a)), d, 0.34 + 0.06 * (k % 3), 0.13,
              LEAF if k % 2 else LEAF_LIGHT, droop=0.25)
    return m


# -- the Needlebriar ---------------------------------------------------------------------------


def needlebriar() -> mk.Mesh:
    m = mk.Mesh()
    mk.rod(m, (0.0, -0.02, 0.0), (0.0, 0.18, 0.0), 0.04, 6, TILE[STEM], STEM)
    for a, b in ((0.3, 0.22), (2.4, 0.2), (4.4, 0.21)):
        mk.rod(m, (0.0, 0.12, 0.0), (b * math.cos(a), 0.3, b * math.sin(a)), 0.02, 4, TILE[STEM], STEM)
    clumps = [((0.0, 0.42, 0.0), (0.26, 0.24, 0.26)), ((0.17, 0.34, 0.08), (0.18, 0.17, 0.18)),
              ((-0.16, 0.36, 0.06), (0.17, 0.16, 0.17)), ((0.05, 0.36, -0.17), (0.18, 0.16, 0.16)),
              ((-0.06, 0.62, 0.03), (0.18, 0.17, 0.18)), ((0.1, 0.56, 0.14), (0.14, 0.13, 0.14))]
    for c, r in clumps:
        _ellipsoid(m, c, r, BRIAR, 8, 5)
    # Thorns bristling out of the clumps (a fixed spiral, so the build is deterministic).
    n = 0
    for c, r in clumps:
        for k in range(9):
            t = (k + 0.5) / 9
            pol = math.acos(1 - 1.6 * t)  # the upper part of the clump
            az = k * 2.39996 + n
            d = (math.sin(pol) * math.cos(az), math.cos(pol), math.sin(pol) * math.sin(az))
            base = (c[0] + d[0] * r[0] * 0.92, c[1] + d[1] * r[1] * 0.92, c[2] + d[2] * r[2] * 0.92)
            _cone(m, base, d, 0.1, 0.016, THORN, 3)
        n += 1
    return m


# -- the Shiverfern ----------------------------------------------------------------------------


def _frond(m: mk.Mesh, a: float, length: float, lift: float, zone: int) -> None:
    """One arching frond at azimuth `a`: a thin rachis curving up and out and
    drooping at the tip, a pointed leaflet either side at every joint, the
    widest at the frond's middle."""
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    n = 8
    for i in range(n + 1):
        t = i / n
        r = length * t
        pts.append((r * ca, lift * math.sin(math.pi * t * 0.78) + 0.03, r * sa))
    for i in range(n):
        mk.rod(m, pts[i], pts[i + 1], 0.009 - 0.004 * i / n, 3, TILE[zone], zone, caps=False)
    side = (-sa, 0.0, ca)
    for i in range(1, n):
        t = i / n
        p0, p1 = pts[i], pts[i + 1]
        w = (0.085 * math.sin(math.pi * min(1.0, t * 1.15)) + 0.02) * (0.6 if i == n - 1 else 1.0)
        step = mk.sub(p1, p0)
        for s in (-1.0, 1.0):
            tip = mk.add(mk.add(p0, mk.scale(step, 0.55)), mk.scale(side, s * w))
            tip = (tip[0], tip[1] - 0.025, tip[2])
            _card(m, [p0, tip, mk.add(p0, mk.scale(step, 0.8))], zone)


def shiverfern() -> mk.Mesh:
    m = mk.Mesh()
    for k in range(9):
        a = 2 * math.pi * k / 9 + 0.15
        _frond(m, a, 0.5 + 0.08 * (k % 3), 0.42 + 0.05 * ((k + 1) % 3), FERN if k % 3 else LEAF_LIGHT)
    for k in range(3):  # upright inner fronds
        a = 2 * math.pi * k / 3 + 0.9
        _frond(m, a, 0.22, 0.6, FERN)
    for sx in (-1.0, 1.0):  # two fiddleheads
        curl = [(sx * 0.03, 0.0, 0.02)]
        for s in range(1, 9):
            ang = s * 0.7
            rr = 0.07 * (1 - s / 12)
            curl.append((sx * (0.05 + rr * math.sin(ang)), 0.22 + rr * math.cos(ang) + 0.1 * (1 - s / 8), 0.02))
        _spine(m, curl, 0.014, 0.008, FERN, seg=4)
    _ellipsoid(m, (0.0, 0.0, 0.0), (0.09, 0.06, 0.09), STEM, 8, 3)  # the crown at the earth
    return m


MODELS = {"plant_vine": gnashvine, "plant_root": shriekroot, "plant_briar": needlebriar, "plant_fern": shiverfern}


def build(pid: str) -> tuple[mk.Mesh, dict]:
    """(mesh, zone -> palette name) of a plant."""
    return MODELS[pid](), dict(ZONES)


# -- inside Blender ----------------------------------------------------------------------------


def objects(pid: str) -> list:
    """The plant as one Blender object with its own small atlas (the castle
    kit's tiles in its palette colours): one material, one draw. For
    build_school.build_prop (CLASS_PROPS)."""
    import bpyutil
    import kit
    from castle_kit import props_atlas

    m, zones = build(pid)
    names = props_atlas.palette()
    atlas = kit.Atlas()
    slots = [atlas.slot(f.material, names[zones[f.zone]]) for f in m.faces]
    uvs = [kit.face_uvs(f, atlas.rect(i), kit.TILES[f.material][1], k) for k, (f, i) in enumerate(zip(m.faces, slots))]
    base, _ = atlas.paint()
    img = bpyutil.image_from_array(f"{pid}_atlas", base)
    mat = bpyutil.principled(pid, None, img, 0.9)
    return [bpyutil.mesh_object(pid, m, {t: mat for t in m.materials()}, uvs=uvs)]
