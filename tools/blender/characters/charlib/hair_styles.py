"""The first six hairstyles (creator.json v2 ids 14-19) as lock data: the
lock builder (charlib/hair.py), each style's fallback since the styles are
made from the Meshy guides themselves (charlib/hair_meshy.py). The second set
(ids 20-24) are Meshy only (hair_meshy.MESHY_ONLY): here they have just
their LENGTH and BODY.

Male styles are designed on `body_a`'s head, female ones on `body_b`'s; a
style is never built for the other body. Shapes follow the 2D concepts and
the Meshy shape guides (`vendor_assets/meshy`, measured and overlaid with
measure_hair_guides.py; never imported into an asset):

| asset | concept / guide | notes |
| --- | --- | --- |
| short_tousled | c_m1_b / g_m1_b | messy fringe to the brows, flicks at the temples, ears free, pointed nape |
| side_swept | c_m2_b / g_m2_b | long fringe swept across the right eye from a left parting, short sides; the concept's volume (the guide is taller and spikier on top) |
| shaggy_layers | c_m3_a / g_m3_a | long fringe, layered sides over the ears, back to the neck, crown tuft |
| long_bangs | c_f1_a / g_f1_a | uneven bangs, long side locks in front of the shoulders, back to mid-back |
| ponytail_locks | c_f2_a / g_f2_a | high ponytail with a dark tie, swept bangs, temple locks to the jaw |
| bob_locks | c_f3_a / g_f3_a | chin bob, uneven bangs, long front locks below the chin |

Angles are degrees in the head frame (`hair_frame`: phi from the top,
theta 0 = the face, 90 = the character's left); lengths in head cm.
`style(name, fr, cap)` builds the style for that head; `cap=True` is the
variant worn under hats (ponytails move to the nape).
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .hair import HairStyle, Lock, periodic, volume_field

LENGTH = {"short_tousled": "short", "side_swept": "short", "shaggy_layers": "medium", "bob_locks": "medium",
          "long_bangs": "long", "ponytail_locks": "long",
          # The second set (ids 20-24) are made from their Meshy guides only
          # (hair_meshy.MESHY_ONLY): no lock data here, just their class.
          "swept_undercut": "short", "low_tail": "medium", "crew_cut": "short",
          "twin_tails": "long", "side_braid": "long"}
BODY = {"short_tousled": "body_a", "side_swept": "body_a", "shaggy_layers": "body_a",
        "long_bangs": "body_b", "ponytail_locks": "body_b", "bob_locks": "body_b",
        "swept_undercut": "body_a", "low_tail": "body_a", "crew_cut": "body_a",
        "twin_tails": "body_b", "side_braid": "body_b"}


def mirror(locks: list[Lock]) -> list[Lock]:
    """The locks plus their mirror images (theta -> 360 - theta)."""
    out = list(locks)
    swap = {"side.L": "side.R", "side.R": "side.L"}
    for lk in locks:
        t0, t1 = lk.root[1] % 360.0, lk.tip[1] % 360.0
        if t0 in (0.0, 180.0) and t1 in (0.0, 180.0) and not lk.bend and not lk.twist:
            continue
        out.append(replace(lk, root=(lk.root[0], (360.0 - t0) % 360.0), tip=(lk.tip[0], (360.0 - t1) % 360.0),
                           bend=-lk.bend, twist=-lk.twist, roll=-lk.roll, chain=swap.get(lk.chain, lk.chain)))
    return out


def brow_phi(fr, theta: float, dz: float, extra: float = 0.6) -> float:
    """Polar angle at azimuth theta where a lock `extra` hcm off the skull
    is `dz` hcm above (negative: below) the brow line."""
    return fr.phi_at_z(theta, fr.brow_z + dz * fr.hcm, extra * fr.hcm)


def eye_phi(fr, theta: float, dz: float, extra: float = 0.6) -> float:
    return fr.phi_at_z(theta, fr.eye_z + dz * fr.hcm, extra * fr.hcm)


def _cap_hem(fr, ctrl):
    base = periodic(ctrl)
    return lambda th: np.maximum(base(th), fr.scalp_border(th) + 3.0)


def _inner(thetas, root_phi, tip_fn, width=4.6, thick=0.6, **kw) -> list[Lock]:
    """Inner fill: wide, flat, hidden under the outer layer (LOD0-1 only)."""
    return [Lock(root=(root_phi, t), tip=(tip_fn(t), t), width=width, thick=thick, lift=(0.55, 0.75, 0.6), layer=0,
                 lods=1, segs=7, group="inner", **kw) for t in thetas]


# ----------------------------------------------------------------------------
# Male (body_a)
# ----------------------------------------------------------------------------


def short_tousled(fr, cap: bool) -> HairStyle:
    vol = volume_field(3.6, [
        (35.0, periodic([(0, 3.0), (60, 3.3), (120, 3.5), (180, 3.6)])),
        (75.0, periodic([(0, 1.8), (50, 2.6), (90, 2.8), (130, 3.0), (180, 3.0)])),
        (110.0, periodic([(0, 1.0), (90, 1.4), (140, 1.9), (180, 2.0)])),
        (135.0, 0.9),
    ])
    locks: list[Lock] = []
    # Fringe: seven uneven pointed locks to the brows, the middle one longest,
    # tips pushed a little left and right (tousled), forehead between them.
    tips = (-40.0, -27.0, -13.0, 0.0, 13.0, 27.0, 40.0)
    dz = (1.0, -0.3, 0.7, -1.7, -0.2, 0.5, 1.3)
    jig = (4.0, -3.0, 5.0, -2.0, 4.0, -4.0, 3.0)
    # The fringe grows from the top of the head (behind the pole) and falls
    # forward over the forehead: it is also the hair of the front top.
    for i, (t, d, j) in enumerate(zip(tips, dz, jig)):
        tt = (t + j) % 360.0
        locks.append(Lock(root=(9.0 + 3.0 * (i % 3), (180.0 - t * 2.2) % 360.0), tip=(brow_phi(fr, tt, d), tt),
                          width=3.7, thick=1.0, lift=(0.85 + 0.05 * (i % 3), 1.0, 0.5), flare=-0.2, curl=0.15, segs=11,
                          group="fringe", lods=3 if i in (1, 3, 5) else 2))
    W = 22.0
    side_top = [Lock(root=(W, 160.0), tip=(52.0, 58.0), width=4.0, thick=1.0, lift=(1.0, 1.1, 0.95), curl=0.6, flare=0.4,
                     layer=2, segs=9, group="crown"),
                Lock(root=(W + 4, 150.0), tip=(62.0, 100.0), width=4.0, thick=1.0, lift=(1.0, 1.1, 0.95), curl=0.5,
                     flare=0.6, layer=2, segs=9, group="crown", lods=3)]
    # Temples: flicks sticking out at the top of the ears.
    sides = [Lock(root=(18.0, 42.0), tip=(eye_phi(fr, 70.0, 0.6, 1.0), 70.0), width=3.2, thick=0.9, lift=(0.9, 1.0, 0.8),
                  flare=0.7, curl=0.4, segs=9, group="side"),
             Lock(root=(22.0, 62.0), tip=(78.0, 86.0), width=3.4, thick=0.9, lift=(0.9, 1.0, 0.85), flare=0.9, curl=0.5,
                  segs=9, group="side", lods=3),
             Lock(root=(26.0, 98.0), tip=(80.0, 104.0), width=3.4, thick=0.9, lift=(0.9, 1.0, 0.85), flare=0.8, curl=0.3,
                  segs=9, group="side"),
             Lock(root=(28.0, 118.0), tip=(92.0, 122.0), width=3.4, thick=0.9, lift=(0.9, 1.0, 0.8), flare=0.7, curl=0.2,
                  segs=9, group="side")]
    # Back: an upper layer of points at ear level over pointed nape locks.
    back = []
    for i, t in enumerate(np.arange(140.0, 221.0, 10.0)):
        back.append(Lock(root=(W + 6.0 + 4.0 * (i % 2), float(t)), tip=(121.0 + 5.0 * ((i + 1) % 2), float(t) + 2.0 * math.sin(i)),
                         width=3.5, thick=0.9, lift=(0.8, 1.0, 0.55), flare=0.25, segs=9, group="back",
                         lods=3 if i in (2, 6) else 2))
    for i, t in enumerate(np.arange(150.0, 211.0, 10.0)):
        back.append(Lock(root=(W - 2.0, float(t)), tip=(94.0 + 6.0 * (i % 2), float(t)), width=3.7, thick=0.95,
                         lift=(1.0, 1.1, 0.95), flare=0.7, curl=0.3, layer=2, segs=9, group="back",
                         lods=3 if i == 3 else 2))
    # Thin strands framing the face, in front of the ears to the cheekbone.
    strand = [Lock(root=(20.0, 44.0), tip=(eye_phi(fr, 57.0, -2.0, 0.8), 57.0), width=1.7, thick=0.6,
                   lift=(0.9, 1.0, 0.7), flare=-0.1, taper=0.3, segs=9, group="strand", band="none")]
    locks += mirror(side_top) + mirror(sides) + back + mirror(strand)
    locks += _inner(np.arange(0.0, 360.0, 360.0 / 14), 18.0,
                    lambda t: float(periodic([(0, 60), (60, 70), (90, 76), (130, 104), (180, 116)])(t)))
    return HairStyle("short_tousled", "body_a", "short", vol, locks,
                     ring_phi=periodic([(0, 34.0), (90, 40.0), (180, 44.0)]),
                     cap_hem=_cap_hem(fr, [(0, 48.0), (60, 62.0), (90, 74.0), (120, 104.0), (150, 118.0), (180, 122.0)]),
                     widen=1.2, widen_fringe=1.25)


def side_swept(fr, cap: bool) -> HairStyle:
    """Parting on the character's left (theta ~45), the fringe swept across
    the forehead and over the right eye; short flat sides and back; the
    top full but rounded (the concept's volume, not the guide's spikes)."""
    vol = volume_field(3.0, [
        (30.0, periodic([(0, 3.0), (45, 2.2), (90, 2.4), (180, 2.6), (300, 2.8)], mirror=False)),
        (65.0, periodic([(0, 1.6), (45, 0.8), (90, 0.7), (180, 1.3), (270, 0.7), (315, 1.4)], mirror=False)),
        (100.0, periodic([(0, 0.6), (90, 0.4), (180, 0.9)])),
        (130.0, 0.4),
    ])
    locks: list[Lock] = []
    part = 46.0
    # The long top: grows from the crown, sweeps forward over the head and
    # across the forehead to the right, the longest lock over the right eye
    # to the cheekbone; the arc over the top is the style's volume.
    sweep = ((318.0, -4.0, 5.0), (330.0, -2.6, 4.6), (343.0, -0.8, 4.2), (355.0, 0.3, 4.0), (8.0, 0.9, 3.8),
             (22.0, 1.6, 3.6))
    for i, (tt, d, w) in enumerate(sweep):
        locks.append(Lock(root=(9.0 + 3.0 * (i % 3), (200.0 - 14.0 * i) % 360.0), tip=(eye_phi(fr, tt, d, 1.0), tt),
                          width=w, thick=1.1, lift=(1.0, 1.1, 0.95 if i < 2 else 0.6), bend=-8.0, flare=-0.05, curl=0.1, taper=0.45,
                          segs=12, group="fringe", clearance=1.0 if i < 2 else 0.35, lods=3 if i in (0, 2, 4) else 2))
    # Three short pointed tufts lifting at the back of the crown (the
    # concept's spiky crown). Curled harder and taller they read as one
    # knot on top of the head in three-quarter view.
    for i, (t0, t1) in enumerate(((200.0, 150.0), (180.0, 128.0), (160.0, 108.0))):
        locks.append(Lock(root=(20.0, t0), tip=(30.0, t1), width=3.0, thick=0.9, lift=(1.0, 1.12, 1.1), curl=0.2,
                          taper=0.6, layer=2, segs=8, group="crown"))
    # Short sides and back: one grooved sheet combed from the hairline up
    # to the crown (under the long top), then pointed nape locks over it.
    hem = periodic([(0, 50.0), (40, 60.0), (70, 74.0), (95, 78.0), (112, 86.0), (130, 104.0), (180, 114.0)])
    sheet = {"hem": hem, "tie": fr.point(14.0, 190.0, 0.6 * fr.hcm), "ring": 0.5 * fr.hcm, "ridges": 18, "vol": 0.7,
             "depth": 0.45}
    for i, t in enumerate(np.arange(130.0, 231.0, 12.5)):
        locks.append(Lock(root=(34.0 + 4 * (i % 2), float(t)), tip=(116.0 + 5.0 * (i % 2), float(t)), width=3.6,
                          thick=0.8, lift=(1.2, 1.3, 0.8), flare=0.15, taper=0.55, segs=9, group="back",
                          lods=3 if i in (2, 6) else 2))
    return HairStyle("side_swept", "body_a", "short", vol, locks, sheet=sheet,
                     ring_phi=periodic([(0, 34.0), (90, 42.0), (180, 46.0)]),
                     cap_hem=_cap_hem(fr, [(0, 48.0), (60, 62.0), (90, 74.0), (120, 102.0), (150, 114.0), (180, 118.0)]),
                     widen=1.15, widen_fringe=1.2)


def shaggy_layers(fr, cap: bool) -> HairStyle:
    vol = volume_field(2.8, [
        (35.0, periodic([(0, 2.3), (60, 2.6), (120, 2.8), (180, 2.9)])),
        (80.0, periodic([(0, 1.8), (60, 2.6), (100, 3.0), (180, 3.0)])),
        (115.0, periodic([(0, 1.4), (90, 2.4), (180, 2.4)])),
        (140.0, 1.2),
    ])
    locks: list[Lock] = []
    tips = (-42.0, -29.0, -16.0, -4.0, 8.0, 20.0, 33.0, 45.0)
    dz = (-0.6, -1.8, -0.4, -2.6, -1.0, -2.0, -0.2, -1.2)
    for i, (t, d) in enumerate(zip(tips, dz)):
        tt = t % 360.0
        locks.append(Lock(root=(14.0 + 5.0 * (i % 2), (t * 0.5) % 360.0), tip=(eye_phi(fr, tt, d + 1.2, 0.9), tt), width=3.3,
                          thick=0.95, lift=(1.0, 1.05, 0.55), flare=-0.1, curl=0.1, segs=10, group="fringe",
                          lods=3 if i in (1, 3, 6) else 2))
    W = 20.0
    for k, (t_root, tip) in enumerate(((170.0, (32.0, 345.0)), (190.0, (30.0, 20.0)), (180.0, (26.0, 0.0)))):
        locks.append(Lock(root=(W, t_root), tip=tip, width=4.4, thick=1.1, lift=(1.0, 1.15, 1.0), curl=0.4, layer=2,
                          segs=10, group="crown", lods=3 if k == 2 else 2))
    # Crown tuft (a short curved lock standing up from the whorl).
    locks.append(Lock(root=(W - 4.0, 178.0), tip=(10.0, 110.0), width=1.6, thick=0.5, lift=(1.1, 1.5, 1.3),
                      curl=0.5, layer=2, segs=7, group="tuft", band="none"))
    # Sides: layered pointed locks over the ears down to the jaw, flaring.
    sides = []
    for i, (t, ph, drop, fl) in enumerate(((55.0, 88.0, 4.0, 0.8), (72.0, 92.0, 7.0, 1.0), (90.0, 94.0, 8.0, 1.2),
                                           (108.0, 96.0, 7.0, 1.2), (126.0, 100.0, 6.0, 1.0))):
        sides.append(Lock(root=(22.0 + 3 * (i % 2), t - 10.0), tip=(ph, t), width=4.0, thick=1.7, roll=35.0, lift=(0.9, 1.0, 0.95),
                          hang=0.8, drop=drop, lean=(fl, 0.4 if i < 2 else 0.0), flare=0.7, curl=0.3, segs=11,
                          group="side", chain="side.L" if i < 3 else None, lods=3 if i in (1, 3) else 2))
    for i, (t, ph) in enumerate(((64.0, 74.0), (84.0, 80.0), (104.0, 82.0), (122.0, 86.0))):
        sides.append(Lock(root=(20.0, t - 8.0), tip=(ph, t), width=4.0, thick=1.4, lift=(1.0, 1.1, 1.0), flare=1.0,
                          curl=0.5, layer=2, segs=9, group="side"))
    # Back: two layers of points, the lower to the neck.
    back = []
    for i, t in enumerate(np.arange(140.0, 221.0, 10.0)):
        back.append(Lock(root=(W + 6.0 + 3 * (i % 2), float(t)), tip=(112.0, float(t)), width=3.6, thick=1.0,
                         lift=(0.9, 1.0, 0.9), hang=0.7, drop=5.0 + 2.0 * ((i + 1) % 2), lean=(0.4, 0.0), flare=0.4,
                         segs=11, group="back", chain="back", lods=3 if i in (2, 6) else 2))
    for i, t in enumerate(np.arange(145.0, 216.0, 14.0)):
        back.append(Lock(root=(W, float(t)), tip=(100.0 + 4 * (i % 2), float(t)), width=3.8, thick=1.0,
                         lift=(1.0, 1.1, 1.0), flare=0.8, curl=0.3, layer=2, segs=9, group="back"))
    locks += mirror(sides) + back
    locks += _inner(np.arange(0.0, 360.0, 360.0 / 14), 18.0,
                    lambda t: float(periodic([(0, 62), (60, 80), (90, 92), (130, 108), (180, 116)])(t)))
    return HairStyle("shaggy_layers", "body_a", "medium", vol, locks,
                     ring_phi=periodic([(0, 33.0), (90, 40.0), (180, 43.0)]),
                     cap_hem=_cap_hem(fr, [(0, 48.0), (60, 64.0), (90, 80.0), (120, 104.0), (150, 116.0), (180, 120.0)]),
                     covers_ears=True, chains=("back", "side.L", "side.R"),
                     trunk_clear=0.02, widen=1.2, widen_fringe=1.25)


# ----------------------------------------------------------------------------
# Female (body_b)
# ----------------------------------------------------------------------------


def long_bangs(fr, cap: bool) -> HairStyle:
    vol = volume_field(2.4, [
        (35.0, periodic([(0, 2.3), (90, 2.5), (180, 2.6)])),
        (80.0, periodic([(0, 1.4), (60, 2.0), (120, 2.4), (180, 2.5)])),
        (110.0, 2.0),
    ])
    locks: list[Lock] = []
    # Uneven bangs from the crown to the eyes, long pointed tips, gaps.
    tips = (-44.0, -33.0, -22.0, -11.0, 0.0, 11.0, 22.0, 33.0, 44.0)
    dz = (-2.0, -0.6, -1.6, -0.4, -2.3, -0.8, -1.4, -0.3, -1.9)
    for i, (t, d) in enumerate(zip(tips, dz)):
        tt = t % 360.0
        locks.append(Lock(root=(8.0 + 2.0 * (i % 3), (180.0 - t * 2.0) % 360.0), tip=(brow_phi(fr, tt, d, 0.7), tt),
                          width=3.5, thick=0.9, lift=(0.85 + 0.05 * (i % 3), 1.0, 0.5), flare=-0.15, taper=0.35, segs=12,
                          group="fringe", lods=3 if i in (1, 4, 7) else 2))
    # Long side locks framing the face, in front of the shoulders.
    sides = [Lock(root=(10.0, 38.0), tip=(86.0, 58.0), width=4.2, thick=1.8, roll=30.0, lift=(0.95, 1.0, 1.0), hang=1.0, drop=28.0,
                  lean=(0.5, 2.6), taper=0.7, segs=15, group="frame", chain="side.L", lods=3),
             Lock(root=(11.0, 56.0), tip=(88.0, 70.0), width=4.6, thick=1.8, roll=30.0, lift=(0.95, 1.0, 1.0), hang=1.0, drop=25.0,
                  lean=(0.6, 2.4), taper=0.7, segs=15, group="frame", chain="side.L"),
             Lock(root=(12.0, 74.0), tip=(92.0, 84.0), width=5.0, thick=1.7, roll=25.0, lift=(1.0, 1.05, 1.0), hang=1.0, drop=22.0,
                  lean=(0.6, 2.6), taper=0.7, segs=14, group="side", chain="side.L")]
    # Back curtain to mid-back, the middle longest, tips uneven.
    back = []
    for i, t in enumerate(np.arange(102.0, 259.0, 10.4)):
        mid = 1.0 - abs(float(t) - 180.0) / 90.0
        back.append(Lock(root=(10.0 + 4.0 * (i % 2), float(t)), tip=(104.0, float(t)), width=5.2, thick=1.5,
                         lift=(0.9, 1.0, 1.0), hang=1.0, drop=6.0 + 30.0 * mid ** 1.2 + 1.5 * ((i * 7) % 3 - 1),
                         lean=(0.6 * mid - 4.5 * (1.0 - mid), -1.0 * (1.0 - mid)), taper=0.72, segs=14, group="back", chain="back", lods=3 if i % 3 == 1 else 2))
    for i, t in enumerate(np.arange(126.0, 235.0, 18.0)):
        mid = 1.0 - abs(float(t) - 180.0) / 90.0
        back.append(Lock(root=(8.0, float(t)), tip=(100.0, float(t)), width=5.4, thick=1.1, lift=(1.0, 1.1, 1.05), hang=1.0,
                         drop=8.0 + 3 * (i % 2), lean=(0.5 * mid - 2.5 * (1 - mid), -1.0 * (1 - mid)), taper=0.6, layer=2, segs=10, group="back", chain="back"))
    # Thin strands beside the face (between the bangs and the side locks).
    strand = [Lock(root=(10.0, 24.0), tip=(90.0, 34.0), width=1.9, thick=0.7, lift=(0.95, 1.0, 1.0), hang=1.0,
                   drop=16.0, lean=(0.3, 0.6), taper=0.45, segs=13, group="strand", chain="side.L", band="none")]
    locks += mirror(sides) + back + mirror(strand)
    locks += _inner(np.arange(0.0, 360.0, 360.0 / 14), 14.0,
                    lambda t: float(periodic([(0, 62), (60, 82), (90, 96), (130, 112), (180, 118)])(t)))
    return HairStyle("long_bangs", "body_b", "long", vol, locks,
                     ring_phi=periodic([(0, 32.0), (90, 38.0), (180, 40.0)]),
                     cap_hem=_cap_hem(fr, [(0, 46.0), (60, 66.0), (90, 84.0), (120, 106.0), (150, 120.0), (180, 128.0)]),
                     covers_ears=True, chains=("back", "side.L", "side.R"),
                     trunk_clear=0.04, arm_clear=0.03, widen=1.15, widen_fringe=1.35)


def _catmull(P: np.ndarray, n: int) -> np.ndarray:
    P = np.vstack([P[0] * 2 - P[1], P, P[-1] * 2 - P[-2]])
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.linspace(0.0, 1.0, n, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(P[-2])
    return np.array(out)


def ponytail_locks(fr, cap: bool) -> HairStyle:
    h = fr.hcm
    vol = volume_field(1.6, [(40.0, periodic([(0, 2.2), (60, 1.6), (120, 1.2), (180, 1.2)])),
                             (80.0, periodic([(0, 1.5), (90, 1.0), (180, 1.0)])), (110.0, 0.8)])
    locks: list[Lock] = []
    tie_phi = 112.0 if cap else 30.0
    tie = fr.point(tie_phi, 180.0, (1.6 if not cap else 1.0) * h)
    # Swept bangs (from the character's left to the right), puffed up off
    # the forehead, one long lock between the eyes.
    tips = (-40.0, -27.0, -13.0, 0.0, 13.0, 26.0, 38.0)
    dz = (-1.2, -0.4, -1.0, -2.6, -0.6, 0.0, 0.6)
    for i, (t, d) in enumerate(zip(tips, dz)):
        tt = t % 360.0
        locks.append(Lock(root=(8.0 + 2.0 * (i % 3), (150.0 - t * 1.6) % 360.0), tip=(brow_phi(fr, tt, d, 0.7), tt),
                          width=4.2, thick=0.95, lift=(1.0, 1.3, 0.55), bend=-7.0, flare=-0.15, curl=0.1, taper=0.4,
                          segs=11, group="fringe", lods=3 if i in (1, 3, 5) else 2))
    for t, d in ((52.0, 0.4), (308.0, -0.2)):
        locks.append(Lock(root=(12.0, 90.0 if t < 180 else 270.0), tip=(eye_phi(fr, t, d, 0.9), t), width=4.4, thick=0.95,
                          lift=(1.0, 1.2, 0.8), flare=0.1, taper=0.45, segs=11, group="fringe"))
    # Temple locks to the jaw, in front of the ears.
    temple = [Lock(root=(17.0, 36.0), tip=(86.0, 58.0), width=4.6, thick=1.4, roll=30.0, lift=(1.3, 1.2, 1.0), hang=1.0, drop=9.0,
                   lean=(0.3, 0.9), flare=0.1, taper=0.55, segs=12, group="frame", chain="side.L", lods=3),
              Lock(root=(21.0, 52.0), tip=(88.0, 70.0), width=4.0, thick=1.3, roll=30.0, lift=(1.3, 1.2, 1.0), hang=1.0, drop=5.5,
                   lean=(0.4, 0.4), flare=0.2, taper=0.5, segs=11, group="frame", chain="side.L")]
    locks += mirror(temple)
    # Everything else combed to the tie: one grooved sheet from the
    # hairline (over the temples, behind the ears) into the tie.
    hem = periodic([(0, 52.0), (40, 66.0), (60, 76.0), (80, 80.0), (95, 80.0), (112, 90.0), (130, 106.0), (180, 118.0)])
    sheet = {"hem": hem, "tie": tie, "ring": 1.5 * h, "ridges": 14, "vol": 1.3, "depth": 0.4}
    # The tail: up out of the tie, an S down the back, a flick at the end.
    if not cap:
        way = np.array([[0, 0, 0], [0, 2.2, 3.8], [0, 6.4, 3.2], [0, 9.0, -4.0], [0, 8.4, -15.0], [0, 6.2, -25.0],
                        [0, 6.4, -32.0], [0, 8.6, -35.5]], dtype=np.float64) * h
    else:
        way = np.array([[0, 0, 0], [0, 2.4, -3.0], [0, 3.0, -10.0], [0, 2.8, -19.0], [0, 4.0, -25.0]], dtype=np.float64) * h
    spine = _catmull(tie + way, 10)
    n7 = 7
    for k in range(n7):
        a = 2 * math.pi * k / n7
        pts = []
        for j, p in enumerate(spine):
            u = j / (len(spine) - 1)
            tan = spine[min(j + 1, len(spine) - 1)] - spine[max(j - 1, 0)]
            tan /= np.linalg.norm(tan)
            e1 = np.array([1.0, 0.0, 0.0])
            e2 = np.cross(tan, e1)
            e2 /= np.linalg.norm(e2)
            r = (3.4 * h) * (0.3 + 0.7 * math.sin(math.pi * min(u * 1.5, 1.0) ** 0.6)) * (1.0 - 0.35 * u)
            pts.append(p + (e1 * math.cos(a + 0.5 * u) * 1.1 + e2 * math.sin(a + 0.5 * u) * 0.9) * r)
        locks.append(Lock(root=(0.0, 0.0), tip=(0.0, 0.0), path=pts, bundle=[q for q in spine], width=5.2 - 0.4 * (k % 2),
                          thick=2.0, taper=0.72,
                          segs=18, group="tail", chain="back", band="s6", clearance=0.5, lods=3 if k % 2 == 0 else 2))
    return HairStyle("ponytail_locks", "body_b", "long", vol, locks,
                     ring_phi=periodic([(0, 30.0), (90, 42.0), (180, 52.0)]),
                     cap_hem=_cap_hem(fr, [(0, 48.0), (60, 64.0), (90, 76.0), (120, 104.0), (150, 116.0), (180, 122.0)]),
                     covers_ears=False, chains=("back", "side.L", "side.R"),
                     tie={"centre": tie, "axis": (spine[2] - spine[0]), "r": 1.7 * h, "half_width": 0.7 * h}, sheet=sheet,
                     trunk_clear=0.024, arm_clear=0.025, widen=1.15, widen_fringe=1.25)


def bob_locks(fr, cap: bool) -> HairStyle:
    vol = volume_field(2.9, [
        (35.0, periodic([(0, 2.7), (90, 3.0), (180, 3.1)])),
        (80.0, periodic([(0, 1.6), (60, 2.7), (120, 3.1), (180, 3.1)])),
        (110.0, periodic([(0, 1.4), (90, 2.6), (180, 2.6)])),
    ])
    locks: list[Lock] = []
    tips = (-36.0, -24.0, -12.0, 0.0, 12.0, 24.0, 36.0)
    dz = (-0.8, 0.2, -1.7, -0.3, -1.1, 0.3, -0.6)
    for i, (t, d) in enumerate(zip(tips, dz)):
        tt = t % 360.0
        locks.append(Lock(root=(8.0 + 2.0 * (i % 3), (180.0 - t * 2.2) % 360.0), tip=(brow_phi(fr, tt, d, 0.7), tt),
                          width=3.9, thick=0.95, lift=(0.9 + 0.05 * (i % 3), 1.0, 0.5), flare=-0.15, taper=0.4, segs=12,
                          group="fringe", lods=3 if i in (1, 3, 5) else 2))
    # Long front locks framing the face, below the chin, curving in.
    front = [Lock(root=(10.0, 38.0), tip=(84.0, 50.0), width=4.4, thick=1.6, roll=25.0, lift=(1.0, 1.05, 1.0), hang=1.0, drop=10.0,
                  lean=(-0.3, 1.2), flare=-0.3, taper=0.65, segs=13, group="frame", chain="side.L", lods=3),
             Lock(root=(12.0, 54.0), tip=(88.0, 62.0), width=4.6, thick=1.6, roll=25.0, lift=(1.0, 1.05, 1.0), hang=1.0, drop=7.5,
                  lean=(-0.4, 0.8), flare=-0.3, taper=0.65, segs=12, group="frame", chain="side.L")]
    # Sides and back: a rounded bob to the jaw curving in at the ends, the
    # nape a little shorter.
    sides = []
    for i, t in enumerate(np.arange(72.0, 180.0, 12.0)):
        nape = max(0.0, (float(t) - 120.0) / 60.0)
        sides.append(Lock(root=(10.0 + 3 * (i % 2), float(t)), tip=(100.0 + 14.0 * nape, float(t)), width=5.6, thick=1.6,
                          lift=(0.9, 1.0, 1.0), hang=0.8 - 0.5 * nape, drop=6.5 - 4.0 * nape + 0.6 * ((i % 3) - 1),
                          lean=(-2.6, 0.6 * (1 - nape)), flare=-0.3, taper=0.88, segs=12, group="side",
                          lods=3 if i % 3 == 0 else 2))
    upper = []
    for i, t in enumerate(np.arange(84.0, 170.0, 19.0)):
        nape = max(0.0, (float(t) - 120.0) / 60.0)
        upper.append(Lock(root=(8.0, float(t)), tip=(98.0 + 10.0 * nape, float(t)), width=5.8, thick=1.2,
                          lift=(1.0, 1.12, 1.05), hang=0.9 - 0.6 * nape, drop=3.0 - 2.0 * nape, lean=(-0.5, 0.0), flare=-0.3,
                          taper=0.75, layer=2, segs=10, group="side"))
    locks += mirror(front) + mirror(sides) + mirror(upper)
    locks.append(Lock(root=(12.0, 180.0), tip=(114.0, 180.0), width=5.6, thick=1.3, lift=(0.9, 1.0, 1.0), hang=0.35,
                      drop=1.5, lean=(-1.0, 0.0), flare=-0.5, taper=0.7, segs=12, group="side", lods=3))
    locks += _inner(np.arange(0.0, 360.0, 360.0 / 14), 14.0,
                    lambda t: float(periodic([(0, 60), (60, 82), (90, 96), (130, 108), (180, 112)])(t)))
    return HairStyle("bob_locks", "body_b", "medium", vol, locks,
                     ring_phi=periodic([(0, 32.0), (90, 38.0), (180, 40.0)]),
                     cap_hem=_cap_hem(fr, [(0, 46.0), (60, 66.0), (90, 84.0), (120, 106.0), (150, 118.0), (180, 124.0)]),
                     covers_ears=True, chains=("side.L", "side.R"),
                     trunk_clear=0.02, widen=1.15, widen_fringe=1.3)


STYLES = {
    "short_tousled": short_tousled,
    "side_swept": side_swept,
    "shaggy_layers": shaggy_layers,
    "long_bangs": long_bangs,
    "ponytail_locks": ponytail_locks,
    "bob_locks": bob_locks,
}


def style(name: str, fr, cap: bool) -> HairStyle:
    if name not in STYLES:
        raise KeyError(f"no hair style {name!r}")
    if BODY[name] != fr.id:
        raise ValueError(f"{name} belongs to {BODY[name]}, not {fr.id}")
    return STYLES[name](fr, cap)
