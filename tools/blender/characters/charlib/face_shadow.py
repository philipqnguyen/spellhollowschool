"""The face-shadow threshold map (docs/art/character-look.md section 3.4).

A Genshin-style face shadow: the key light's lit share on the face comes
from a designed map instead of the toon step on the face's normals. Each
texel stores the light angle (round the head's up axis, 0 = straight in
front, 180 = behind, the light on the +X side) at which it goes dark,
divided by 180, in the B channel of `face_mask_<body>.png`. Light from the
-X side samples the map mirrored in u (the layout is symmetric about
u = 0.5, `face_paint.FaceMap`). The shader: `lit = smoothstep(t - w, t + w,
threshold)` with `t` the light's angle / 180.

The design, per texel (`theta_dark`):

* **Base**: the toon step on the smooth face normals (the head's custom
  normals: the face ellipsoid's, `mh_head.face_normals`) for a horizontal
  key: lit while `h cos(theta - phi) > THRESHOLD`, with `phi` the normal's
  horizontal direction and `h` its horizontal length. So the shadow comes in
  from the far side as one clean, nearly vertical line, reaches the nose
  bridge at about 96 degrees and leaves only the near cheek lit from behind.
* **Nose shadow**: a small wedge beside and under the nose tip on the far
  side goes dark early (from `NOSE_DARK[0]` degrees at the nose to
  `NOSE_DARK[1]` at its outer tip), the triangle Genshin faces show with a
  side light.
* **Soft jaw**: where the normal turns down (under the jaw) the texel goes
  dark early (`JAW_DARK`), so the jaw's underside is a soft shadow over the
  neck instead of a lit sliver.
* Softened with a few box passes (no stair steps), so between two angles
  the line sweeps smoothly; one value per texel, so the lit masks of any
  two angles are nested (monotonic by construction).
* **Nose bridge** (`BRIDGE`, after the softening, `with_bridge`): a short
  crisp stroke continuing the wedge up the far side of the bridge, dark
  from `BRIDGE_DARK` degrees, so the nose reads as a painted anime nose
  whenever the key is off to one side, always on the side away from it.

The method is the "threshold map" of the contract with the mask sequence
evaluated in closed form instead of rendered at 2.5 degree steps and
interpolated by signed distance: the same map, exactly monotonic, no
rendering.
"""

from __future__ import annotations

import math

import numpy as np

THRESHOLD = -0.1  # the face row's toon threshold (n.l), data/art/toon_palette.json
NOSE_DARK = (24.0, 46.0)
JAW_DARK = 38.0
SOFTEN = 6  # box passes (3 x 3) at the map's size
# The nose bridge's shadow stroke (user, 2026-11-01: a more obvious nose, by
# paint): a short tapered stroke down the far (-X) side of the bridge,
# continuing the nose wedge's top up toward the eyes, that goes dark as soon as the key is
# BRIDGE_DARK degrees off the face's front, so it is always on the side away
# from the key light (the map is mirrored for a key on -X). Laid over the
# softened map with its own one-texel edge, so it stays a crisp stroke.
# Points (u, v above the nose point) and half-widths, metres x the mouth
# scale, from the bottom end to the top end.
BRIDGE = ((-0.00075, 0.0026, 0.00050), (-0.00100, 0.0078, 0.00028))
BRIDGE_DARK = 8.0


def _raster(mesh: dict, size: int) -> tuple[np.ndarray, np.ndarray]:
    """Corner normals interpolated over the head's UV layout: (size, size, 3)
    normals and a coverage mask (row 0 at the top, v = 1)."""
    N = np.zeros((size, size, 3))
    hit = np.zeros((size, size), dtype=bool)
    uv = mesh["uv"]
    for tri in mesh["tris"]:
        q = uv[tri] * size
        q = np.stack([q[:, 0], (1.0 - uv[tri][:, 1]) * size], axis=1)  # pixel x, y (y down)
        x0, x1 = int(math.floor(q[:, 0].min())), int(math.ceil(q[:, 0].max()))
        y0, y1 = int(math.floor(q[:, 1].min())), int(math.ceil(q[:, 1].max()))
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, size - 1), min(y1, size - 1)
        if x1 < x0 or y1 < y0:
            continue
        a, b, c = q
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-9:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        l0 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / den
        l1 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / den
        l2 = 1.0 - l0 - l1
        ins = (l0 >= -1e-4) & (l1 >= -1e-4) & (l2 >= -1e-4)
        if not ins.any():
            continue
        n = mesh["N"][tri]
        val = l0[..., None] * n[0] + l1[..., None] * n[1] + l2[..., None] * n[2]
        sub = N[y0:y1 + 1, x0:x1 + 1]
        sub[ins] = val[ins]
        hit[y0:y1 + 1, x0:x1 + 1] |= ins
    N /= np.maximum(np.linalg.norm(N, axis=2, keepdims=True), 1e-9)
    return N, hit


def _fill(a: np.ndarray, hit: np.ndarray) -> np.ndarray:
    """Texels no triangle covers take their nearest covered neighbour's value
    (dilation), so filtering never pulls in garbage at the layout's edges."""
    a = a.copy()
    h = hit.copy()
    for _ in range(a.shape[0]):
        if h.all():
            break
        acc = np.zeros_like(a)
        cnt = np.zeros(h.shape)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(np.roll(h, dy, 0), dx, 1)
            sa = np.roll(np.roll(a, dy, 0), dx, 1)
            acc[sh] += sa[sh]
            cnt += sh
        new = (~h) & (cnt > 0)
        a[new] = acc[new] / (cnt[new][:, None] if a.ndim == 3 else cnt[new])
        h |= new
    return a


def _box(a: np.ndarray, passes: int) -> np.ndarray:
    for _ in range(passes):
        p = np.pad(a, 1, mode="edge")
        a = sum(p[1 + dy:1 + dy + a.shape[0], 1 + dx:1 + dx + a.shape[1]] for dy in (-1, 0, 1) for dx in (-1, 0, 1)) / 9.0
    return a


def theta_dark(N: np.ndarray, qu: np.ndarray, qv: np.ndarray, fr) -> np.ndarray:
    """Degrees of light angle (light on +X) at which each texel goes dark,
    from the normals (Blender axes: +X left of the character, -Y forward,
    +Z up) and the face-projection coordinates of each texel."""
    nx, ny, nz = N[..., 0], -N[..., 1], N[..., 2]  # ny: forward
    h = np.hypot(nx, ny)
    phi = np.degrees(np.arctan2(nx, ny))
    base = phi + np.degrees(np.arccos(np.clip(THRESHOLD / np.maximum(h, 1e-6), -1.0, 1.0)))
    # Soft jaw: the underside goes dark early.
    jaw = np.clip((-nz - 0.35) / 0.3, 0.0, 1.0)
    jaw = jaw * jaw * (3 - 2 * jaw)
    out = base * (1 - jaw) + np.minimum(base, JAW_DARK) * jaw
    # Nose wedge on the far (-X) side of the tip.
    st = fr.st
    km = st.get("mouth_k", 1.0)
    nv = float(fr.nose[1])
    wedge = np.array([[0.0004, nv + 0.0050], [-0.0012, nv + 0.0042], [-0.0046, nv - 0.0010],
                      [-0.0034, nv - 0.0036], [0.0004, nv - 0.0030]])
    wedge[:, 0] *= km
    wedge[:, 1] = nv + (wedge[:, 1] - nv) * km
    from . import mh_face as MF

    box = (np.abs(qu) < 0.012 * km) & (np.abs(qv - nv) < 0.012 * km)
    if box.any():
        d = MF.poly_sdf(np.stack([qu[box], qv[box]], axis=1), wedge)
        along = np.clip(-qu[box] / (0.0046 * km), 0.0, 1.0)
        nose = NOSE_DARK[0] + (NOSE_DARK[1] - NOSE_DARK[0]) * along + np.maximum(d, 0.0) / (0.0004 * km) * 45.0
        sub = out[box]
        out[box] = np.minimum(sub, nose)
    return out


def bridge_cover(qu: np.ndarray, qv: np.ndarray, fr, texel: float) -> np.ndarray:
    """The nose bridge stroke's coverage (0..1, edges one `texel` wide) at
    face coordinates (qu, qv): a capsule tapering from BRIDGE's bottom end
    to its top end, on the -X side."""
    km = fr.st.get("mouth_k", 1.0)
    nv = float(fr.nose[1])
    (u0, v0, r0), (u1, v1, r1) = BRIDGE
    a = np.array([u0 * km, nv + v0 * km])
    b = np.array([u1 * km, nv + v1 * km])
    ab = b - a
    t = np.clip(((qu - a[0]) * ab[0] + (qv - a[1]) * ab[1]) / float(ab @ ab), 0.0, 1.0)
    d = np.hypot(qu - (a[0] + t * ab[0]), qv - (a[1] + t * ab[1])) - (r0 + (r1 - r0) * t) * km
    return np.clip(0.5 - d / texel, 0.0, 1.0)


def with_bridge(t: np.ndarray, qu: np.ndarray, qv: np.ndarray, fr, texel: float) -> np.ndarray:
    """The softened map `t` (degrees) with the bridge stroke laid over it."""
    c = bridge_cover(qu, qv, fr, texel)
    return np.minimum(t, t * (1.0 - c) + BRIDGE_DARK * c)


def bake(mesh: dict, fmap, fr, size: int) -> np.ndarray:
    """The threshold map (size x size, 0..1, row 0 at the top)."""
    N, hit = _raster(mesh, size)
    N = _fill(N, hit)
    N /= np.maximum(np.linalg.norm(N, axis=2, keepdims=True), 1e-9)
    c = (np.arange(size) + 0.5) / size
    qu, qv = fmap.face_coords(c[None, :].repeat(size, 0), (1.0 - c)[:, None].repeat(size, 1))
    t = theta_dark(N, qu, qv, fr)
    t = _box(t, SOFTEN)
    mid = size // 2
    texel = float(abs(qu[mid, mid + 1] - qu[mid, mid]))
    t = with_bridge(t, qu, qv, fr, texel)
    return np.clip(t / 180.0, 0.0, 1.0)


def lit_mask(threshold: np.ndarray, angle_deg: float, mirror: bool = False, w: float = 0.02) -> np.ndarray:
    """The lit share the shader computes for a horizontal key at `angle_deg`
    (0 front .. 180 behind) on the +X side, or the -X side with `mirror`."""
    m = threshold[:, ::-1] if mirror else threshold
    t = angle_deg / 180.0
    x = np.clip((m - (t - w)) / (2 * w), 0.0, 1.0)
    return x * x * (3 - 2 * x)


def meta() -> dict:
    return {"method": "closed-form threshold map (toon step on the smooth face normals for a horizontal key, nose wedge, soft jaw), softened, nose bridge stroke over it",
            "threshold": THRESHOLD, "nose_dark_deg": list(NOSE_DARK), "jaw_dark_deg": JAW_DARK, "soften_passes": SOFTEN,
            "bridge_dark_deg": BRIDGE_DARK}
