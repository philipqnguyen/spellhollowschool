"""Prepare the hair source meshes from the Meshy guides (character look pass).

    blender -b --factory-startup -t 3 --python tools/blender/characters/prepare_hair_meshy.py -- \\
        --blend art/characters/body_a.blend --meshy vendor_assets/meshy --out .out/hair_meshy \\
        [--styles short_tousled,side_swept] [--guide r] [--register] [--debug <dir>]

docs/art/character-look.md section 2. Run by hand, never by the
build (the build reads only the source meshes this writes). For each style of
the body in `--blend` (charlib/hair_meshy.py `STYLES`):

1. **Import** the guide `g_<id>.glb` as glTF (never a .blend), welded.
2. **Register** it to the body's head: the style's recorded fit
   (hair_meshy.STYLES `registration`, from measure_hair_guides.py: a uniform
   scale and offset onto the face front and the neck), or with `--register`
   that fit run again (cached in `<out>/cache/`); written into the source
   mesh's meta.
3. **Segment** the hair from the fused bust (one closed surface): a seeded
   watershed on the guide's surface whose ridges are its concave creases
   (where hair meets skin). Skin seeds: the face front, eyes and cheeks on
   our face, the neck, the bust's cut, the ears (unless the style covers
   them); hair seeds: the guide well outside our body and the top of the
   head (except in the style's `seed_free` boxes, where depth says nothing:
   a guide's neck thicker than ours); the style's seed, keep and drop boxes
   (head centimetres) settle what the creases miss, with `hair_off` boxes
   (surface standing more than `m` off our body: locks lying over the face)
   and `protrude` boxes (surface standing out of the guide's own, along an
   axis: a braid on the guide's chest). Then a mode filter, the hair faces as an open
   shell, islands dropped.
4. **Fit** it to our head with a skull-relative transfer: the guide's skull
   (where rays from the head centre leave the guide through skin away from
   any hair, as a ratio to our skull, filled harmonically under the hair and
   blurred) is mapped onto ours, so the hair keeps its own thickness instead
   of being scaled up with the head; below the ears' lower level each
   azimuth translates with that level (lengths stay the guide's). Optional
   per style: the crown's height compressed (the first M2 guide's spikes),
   hair in front of the face lifted over ours (a lock lying on a guide's
   deeper face), low relief lying on our head dropped (a sculpted undercut,
   thin pieces round the ears), the shell cut on a clean line (`cut_u`),
   and short hair laid on our scalp (`hug`: its mass capped, its detail
   kept, its hem pinned over the skull; character-look.md).
5. **Clean**: the open hem smoothed along itself, Taubin smoothing (hem
   pinned, sharp tips held), quadric decimation to SOURCE_MAX_TRIS.
6. **Clear the body**: long locks splaying out to the sides brought back
   under their root (per style), then long hair below the shoulders hangs from the convex
   outline of the torso, robe and shirt above it, each hanging lock moved as
   one piece (forward in front of the chest, back behind it); then every
   vertex pushed out of the skin (3 mm on the head, a style's clearance below
   the chin), spread over the mesh so sheets move together.
7. **Label**: parts (crown, fringe, sides, back, tail, tie), root-to-tip
   (geodesic from the crown, per part), smooth shading normals
   (hair_meshy.shading_normals: a smooth envelope proxy of the hair mass
   blended with the mesh's own, the tips keeping more of their own).

Writes `<out>/<asset>_<body>.npz` (character-look.md section 2.2) and, with
`--debug`, review images of each stage. `--guide <id>` prepares the styles
that have that guide (hair_meshy.GUIDES) from it instead of their default. The Meshy files stay in the
gitignored vendor_assets/ and never enter a repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

from charlib import hair_frame as HF  # noqa: E402
from charlib import hair_meshops as MO  # noqa: E402
from charlib import hair_meshy as HM  # noqa: E402

TOOL_VERSION = 1  # written into each source mesh's meta


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--meshy", default="vendor_assets/meshy")
    p.add_argument("--out", default=".out/hair_meshy")
    p.add_argument("--styles", default="")
    p.add_argument("--debug", default="")
    p.add_argument("--stage", default="all", help="segment | all")
    p.add_argument("--guide", default="", help="prepare from this guide id where a style has it (hair_meshy.GUIDES)")
    p.add_argument("--register", action="store_true",
                   help="fit the guide to the head again (measure_hair_guides.py) instead of the style's recorded fit")
    return p.parse_args(argv)


def log(msg: str) -> None:
    print(f"[hair-meshy] {msg}", flush=True)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------
# Import and registration
# ----------------------------------------------------------------------------


def import_guide(path: str) -> tuple[np.ndarray, np.ndarray]:
    """The guide's triangles in Blender world space (glTF import: data only,
    no scripts); the imported objects are removed again."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    objs = [o for o in bpy.data.objects if o not in before]
    V, T, off = [], [], 0
    for o in objs:
        if o.type != "MESH":
            continue
        co, _, t = HF.world_mesh(o)
        V.append(co)
        T.append(t + off)
        off += len(co)
    for o in objs:
        me = o.data if o.type == "MESH" else None
        bpy.data.objects.remove(o)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
    V = np.vstack(V)
    T = np.vstack(T)
    # Weld duplicate positions (glTF splits vertices at seams).
    key = np.round(V / 1e-6).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.ravel()
    V = V[first]
    T = inv[T]
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    return V, T


def registration(fr, V, T, cache: str, scales=None) -> dict:
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    import measure_hair_guides as MG

    s, ty, tz, err = MG.register(fr, V, T, **({"scales": tuple(scales)} if scales else {}))
    reg = {"scale": s, "offset": [0.0, ty, tz], "fit_rms_mm": round(float(np.sqrt(err)) * 1000, 2)}
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=1)
    return reg


# ----------------------------------------------------------------------------
# Segmentation
# ----------------------------------------------------------------------------


def head_coords(fr, P: np.ndarray) -> np.ndarray:
    """Head-frame head-centimetres: x = the character's left, f = forward,
    u = up, from the head centre."""
    d = (P - fr.centre) / fr.hcm
    return np.stack([d[:, 0], -d[:, 1], d[:, 2]], axis=1)


def in_box(H: np.ndarray, box) -> np.ndarray:
    x0, x1, f0, f1, u0, u1 = box
    return (H[:, 0] >= x0) & (H[:, 0] <= x1) & (H[:, 1] >= f0) & (H[:, 1] <= f1) & (H[:, 2] >= u0) & (H[:, 2] <= u1)


def signed_depth(tree: BVHTree, P: np.ndarray, reach: float = 0.25) -> np.ndarray:
    """Signed height over the nearest surface of `tree` (negative inside)."""
    out = np.full(len(P), reach)
    for i, p in enumerate(P):
        loc, nrm, _, dist = tree.find_nearest(Vector(tuple(p)), reach)
        if loc is not None:
            out[i] = float((p - np.array(loc)) @ np.array(nrm))
    return out


def segment(fr, st: dict, V: np.ndarray, F: np.ndarray, dbg: dict) -> np.ndarray:
    """True for the guide's hair vertices."""
    n = len(V)
    E = MO.edges(F)
    indptr, indices = MO.csr(n, E)
    N = MO.vertex_normals(V, F)
    curv = MO.mean_curvature(V, F, E, N)
    curv = MO.smooth_values(curv, E, st.get("curv_smooth", 3))
    H = head_coords(fr, V)
    depth = signed_depth(fr.collide, V)
    dbg["depth"] = depth
    seg = st.get("segment", {})
    labels = -np.ones(n, dtype=np.int64)  # 0 skin, 1 hair
    # Skin seeds: the face front (on our face after registration), the neck
    # front, and the bust's cut and lower chest (far below the hair's reach
    # unless the style is long).
    eye_u = (fr.eye_z - fr.centre[2]) / fr.hcm
    chin_u = (fr.chin_z - fr.centre[2]) / fr.hcm
    face = (np.abs(depth) < 0.006) & (np.abs(H[:, 0]) < seg.get("face_half_width", 3.5)) & (H[:, 2] < eye_u - 1.2) \
        & (H[:, 2] > chin_u + 0.8) & (N[:, 1] < -0.6)
    neck = (np.abs(H[:, 0]) < 2.5) & (H[:, 2] < chin_u - 1.0) & (H[:, 2] > chin_u - 6.0) & (N[:, 1] < -0.5) & (H[:, 1] > 0)
    zmin = V[:, 2].min()
    cut = V[:, 2] < zmin + seg.get("cut_band_m", 0.012)
    labels[face | neck | cut] = 0
    # The eyes and cheeks on our face (a fringe over them stands further off).
    if seg.get("eye_seeds", True):
        eyes = (np.abs(H[:, 0]) > 1.2) & (np.abs(H[:, 0]) < 4.2) & (H[:, 2] > eye_u - 1.5) & (H[:, 2] < eye_u + 1.0) \
            & (np.abs(depth) < seg.get("eye_reach_m", 0.006)) & (N[:, 1] < -0.3)
        cx = seg.get("cheek_x", [2.5, 6.0])
        cheeks = seg.get("cheek_seeds", True) & (np.abs(H[:, 0]) > cx[0]) & (np.abs(H[:, 0]) < cx[1]) \
            & (H[:, 2] > chin_u + 2.0) & (H[:, 2] < eye_u - 2.0) & (np.abs(depth) < seg.get("cheek_reach_m", 0.005)) \
            & (N[:, 1] < -0.2)
        labels[eyes | cheeks] = 0
    # The ears (unless the style covers them): guide surface on our ears.
    if seg.get("ear_seeds", True) and fr.ears is not None:
        e = fr.ear
        near_ear = (np.abs(V[:, 0]) > e["x"] - 0.03) & (V[:, 2] < e["top"] + 0.004) & (V[:, 2] > e["bottom"] - 0.004)
        idx = np.nonzero(near_ear)[0]
        d_ear = np.abs(signed_depth(fr.ears, V[idx], 0.05))
        labels[idx[d_ear < seg.get("ear_reach_m", 0.007)]] = 0
    for box in seg.get("skin_seeds", []):
        labels[in_box(H, box)] = 0
    # Boxes where only the guide surface near our body is skin (hair hanging
    # through the box stands further off).
    for box in seg.get("skin_near", []):
        labels[in_box(H, box) & (np.abs(depth) < seg.get("skin_near_m", 0.015))] = 0
    # Hair seeds: well outside our body (the guide's head is smaller than
    # ours, so its skin is at or inside ours), above the chin.
    hair = (depth > seg.get("hair_out_m", 0.012)) & (H[:, 2] > chin_u)
    top = V[:, 2] > V[:, 2].max() - 0.01
    labels[(hair | top) & (labels < 0)] = 1
    # Boxes where depth says nothing (the guide's neck thicker than ours,
    # under the locks lying on it): left to the watershed.
    for box in seg.get("seed_free", []):
        labels[in_box(H, box) & (labels == 1)] = -1
    for box in seg.get("hair_seeds", []):
        labels[in_box(H, box)] = 1
    # Boxes where guide surface standing more than `m` off our body is hair
    # (side locks and bangs lying close over the cheeks and forehead: the
    # guide's own skin there is within a few millimetres of ours).
    for rule in seg.get("hair_off", []):
        labels[in_box(H, rule["box"]) & (depth > rule["m"])] = 1
    # Boxes where hair lies on the guide's own bust (a braid over the chest,
    # inside our body where depth says nothing): hair where the guide stands
    # out of its own surface along `axis` ("f", "x", "u", or "-" one) by more
    # than `h_hcm` over the lower envelope of the box (the `pct` percentile
    # of the outermost surface in a `win_hcm` window round each cell).
    for rule in seg.get("protrude", []):
        idx = np.nonzero(in_box(H, rule["box"]))[0]
        if not len(idx):
            continue
        sign = -1.0 if rule["axis"].startswith("-") else 1.0
        ax = {"x": 0, "f": 1, "u": 2}[rule["axis"].lstrip("-")]
        o1, o2 = [k for k in range(3) if k != ax]
        h = sign * H[idx, ax]
        cell = rule.get("cell_hcm", 0.5)
        ga = np.floor(H[idx, o1] / cell).astype(np.int64)
        gu = np.floor(H[idx, o2] / cell).astype(np.int64)
        ga -= ga.min()
        gu -= gu.min()
        top = np.full((ga.max() + 1, gu.max() + 1), np.nan)
        np.fmax.at(top, (ga, gu), h)
        r = max(1, int(round(rule.get("win_hcm", 8.0) / cell / 2)))
        base = np.full_like(top, np.nan)
        for i in range(top.shape[0]):
            for j in range(top.shape[1]):
                win = top[max(0, i - r):i + r + 1, max(0, j - r):j + r + 1]
                win = win[np.isfinite(win)]
                if len(win):
                    base[i, j] = np.percentile(win, rule.get("pct", 30.0))
        b = base[ga, gu]
        labels[idx[np.isfinite(b) & (h > b + rule["h_hcm"])]] = 1
    # Boxes of hair except near our ears (the guide's ear is skin).
    if seg.get("hair_noear") and fr.ears is not None:
        for box in seg["hair_noear"]:
            idx = np.nonzero(in_box(H, box))[0]
            d_ear = np.abs(signed_depth(fr.ears, V[idx], 0.05))
            labels[idx[d_ear > seg.get("noear_m", 0.012)]] = 1
    dbg["seeds"] = labels.copy()
    # Cost: concave creases are ridges the fronts meet on.
    cost = np.clip(curv, 0.0, None)
    cost = cost / max(float(np.percentile(cost[cost > 0], 95)), 1e-9)
    out = MO.watershed(cost, indptr, indices, labels)
    for box in seg.get("keep", []):
        out[in_box(H, box)] = 1
    for box in seg.get("drop", []):
        out[in_box(H, box)] = 0
    out = MO.majority(out, E, passes=st.get("majority", 4), nlab=2)
    dbg["curv"] = curv
    return out == 1


def hair_shell(V: np.ndarray, F: np.ndarray, is_hair: np.ndarray, min_part: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The hair faces (two or three hair corners) as their own mesh, without
    islands smaller than `min_part` vertices. Returns (V, F, guide index)."""
    keep = is_hair[F].sum(axis=1) >= 2
    Vh, Fh, used = MO.submesh(V, F, keep)
    comp = MO.components(len(Vh), MO.edges(Fh))
    ids, cnt = np.unique(comp, return_counts=True)
    big = np.isin(comp, ids[cnt >= min_part])
    Vh2, Fh2, used2 = MO.submesh(Vh, Fh, big[Fh].all(axis=1))
    return Vh2, Fh2, used[used2]


# ----------------------------------------------------------------------------
# Skull-relative transfer
# ----------------------------------------------------------------------------

GRID_STEP = 2.0


def guide_skull(fr, V: np.ndarray, F: np.ndarray, is_hair: np.ndarray, dbg: dict) -> np.ndarray:
    """The guide's skull radius as a ratio to ours, k(phi, theta) on the
    head frame's 2-degree grid: measured where a ray from the head centre
    leaves the guide through skin (not the ears), interpolated under the
    hair (harmonic fill), smoothed."""
    tree = BVHTree.FromPolygons(V.tolist(), F.tolist(), all_triangles=True)
    # Skin well away from any hair (the crease faces between are neither).
    E = MO.edges(F)
    near = is_hair.astype(np.float64)
    for _ in range(5):
        nb = near.copy()
        np.maximum.at(nb, E[:, 0], near[E[:, 1]])
        np.maximum.at(nb, E[:, 1], near[E[:, 0]])
        near = nb
    face_hair = near[F].max(axis=1) > 0
    ear_tree = fr.ears
    phis = np.arange(0.0, 182.0, GRID_STEP)
    thetas = np.arange(0.0, 362.0, GRID_STEP)
    K = np.full((len(phis), len(thetas)), np.nan)
    c = Vector(tuple(fr.centre))
    for i, ph in enumerate(phis):
        if ph > 135.0 or ph < 35.0:
            continue
        for j, th in enumerate(thetas[:-1]):
            d = fr.dirs(ph, th)
            loc, _, idx, dist = tree.ray_cast(c, Vector(tuple(d)), 0.4)
            if loc is None or face_hair[idx]:
                continue
            if ear_tree is not None:
                el, _, _, ed = ear_tree.find_nearest(loc, 0.02)
                if el is not None:
                    continue
            k = dist / float(fr.skull_r(ph, th))
            if 0.75 < k < 1.15:
                K[i, j] = k
    K[:, -1] = K[:, 0]
    known = ~np.isnan(K)
    dbg["skull_known"] = float(known.mean())
    fill = np.where(known, K, np.nanmedian(K))
    # Harmonic fill: unknown cells relax to the mean of their neighbours.
    for _ in range(1500):
        up = np.vstack([fill[:1], fill[:-1]])
        dn = np.vstack([fill[1:], fill[-1:]])
        lf = np.roll(fill, 1, axis=1)
        rt = np.roll(fill, -1, axis=1)
        avg = 0.25 * (up + dn + lf + rt)
        fill = np.where(known, K, avg)
    # Smooth the whole field (measurement noise on the skin, the nose and
    # brows): the skull ratio varies slowly.
    for _ in range(25):
        up = np.vstack([fill[:1], fill[:-1]])
        dn = np.vstack([fill[1:], fill[-1:]])
        fill = 0.5 * fill + 0.125 * (up + dn + np.roll(fill, 1, axis=1) + np.roll(fill, -1, axis=1))
    fill[:, -1] = fill[:, 0]
    return fill


def grid_lookup(G: np.ndarray, phi: np.ndarray, theta: np.ndarray) -> np.ndarray:
    fp = np.clip(phi, 0.0, 180.0) / GRID_STEP
    ft = (np.asarray(theta) % 360.0) / GRID_STEP
    i0 = np.clip(np.floor(fp).astype(int), 0, G.shape[0] - 2)
    j0 = np.clip(np.floor(ft).astype(int), 0, G.shape[1] - 2)
    a, b = fp - i0, ft - j0
    return (1 - a) * (1 - b) * G[i0, j0] + a * (1 - b) * G[i0 + 1, j0] + (1 - a) * b * G[i0, j0 + 1] + a * b * G[i0 + 1, j0 + 1]


def transfer(fr, K: np.ndarray, P: np.ndarray, thickness: float, line_z: float, blend_m: float = 0.02) -> np.ndarray:
    """Hair points from the guide's skull onto ours (character-look.md section 2.2):
    radius = our skull + (guide radius - guide skull) x thickness above
    `line_z`; below it each azimuth translates with the line (lengths kept),
    blended over `blend_m`."""
    phi, theta, R = fr.polar(P)
    d = (P - fr.centre) / np.maximum(R[:, None], 1e-12)
    ro = fr.skull_r(phi, theta)
    rg = grid_lookup(K, phi, theta) * ro
    radial = fr.centre + d * (ro + (R - rg) * thickness)[:, None]
    # The line's displacement per azimuth (on the skull surface).
    th_line = np.arange(0.0, 360.0, 2.0)
    disp = np.zeros((len(th_line), 3))
    for j, th in enumerate(th_line):
        ph = fr.phi_at_z(th, line_z)
        dd = fr.dirs(ph, th)
        r_o = float(fr.skull_r(ph, th))
        r_g = float(grid_lookup(K, np.array([ph]), np.array([th]))[0]) * r_o
        disp[j] = dd * (r_o - r_g)
    dl = np.stack([np.interp(theta, np.concatenate([th_line, [360.0]]), np.concatenate([disp[:, k], disp[:1, k]]))
                   for k in range(3)], axis=1)
    column = P + dl
    w = np.clip((line_z - P[:, 2]) / blend_m + 0.5, 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    return radial * (1 - w[:, None]) + column * w[:, None]


def flatten_crown(fr, P: np.ndarray, h0_hcm: float, k: float, phi_max: float) -> np.ndarray:
    """Hair height over our skull beyond `h0_hcm` compressed by `k` on the
    crown (polar angle under `phi_max`, fading over 15 degrees): the guide's
    tall spikes brought down to the concept's volume."""
    phi, theta, R = fr.polar(P)
    ro = fr.skull_r(phi, theta)
    h = R - ro
    h0 = h0_hcm * fr.hcm
    hn = np.where(h > h0, h0 + (h - h0) * k, h)
    w = np.clip((phi_max - phi) / 15.0, 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    Rn = ro + h * (1 - w) + hn * w
    d = (P - fr.centre) / np.maximum(R[:, None], 1e-12)
    return fr.centre + d * Rn[:, None]


def lift_over_face(fr, P: np.ndarray, clear_m: float, radius: float = 0.02, above_brow_hcm: float = 4.0) -> np.ndarray:
    """Hair in front of the face (a fringe lock lying over the guide's eye)
    lifted along the head rays to `clear_m` over our face where the fit left
    it inside: the guide's face sits deeper than ours. The lifts are made
    coherent in space so a lock moves as one piece."""
    phi, theta, R = fr.polar(P)
    H = head_coords(fr, P)
    brow_u = (fr.brow_z - fr.centre[2]) / fr.hcm
    front = (H[:, 1] > 2.0) & (H[:, 2] < brow_u + above_brow_hcm)
    need = np.where(front, np.maximum(fr.skull_r(phi, theta) + clear_m - R, 0.0), 0.0)
    d = (P - fr.centre) / np.maximum(R[:, None], 1e-12)
    return P + coherent(P, d * need[:, None], radius)


HUG_HEM_LOOP = 0.08  # m: open-edge loops at least this long are the hem (shorter ones: pits between spikes)


def _periodic(ctrl, theta: np.ndarray) -> np.ndarray:
    """A number, or [[theta, value], ...] interpolated round the head."""
    if isinstance(ctrl, (int, float)):
        return np.full(len(theta), float(ctrl))
    c = np.array(sorted(ctrl), dtype=np.float64)
    t = np.concatenate([c[:, 0] - 360.0, c[:, 0], c[:, 0] + 360.0])
    v = np.concatenate([c[:, 1]] * 3)
    return np.interp(np.asarray(theta) % 360.0, t, v)


def height_base(fr, P: np.ndarray, h: np.ndarray, blur: int = 16) -> np.ndarray:
    """The low-frequency part of the hair's height over our skull: the mean
    height per 2-degree cell of the head frame, empty cells filled, blurred
    `blur` passes (wrapping round the head), read back at each point."""
    phi, theta, _ = fr.polar(P)
    nP, nT = int(180 / GRID_STEP) + 1, int(360 / GRID_STEP) + 1
    i = np.clip(np.round(phi / GRID_STEP).astype(int), 0, nP - 1)
    j = np.round((theta % 360.0) / GRID_STEP).astype(int) % (nT - 1)
    S = np.zeros((nP, nT - 1))
    C = np.zeros((nP, nT - 1))
    np.add.at(S, (i, j), h)
    np.add.at(C, (i, j), 1.0)
    known = C > 0
    G = np.where(known, S / np.maximum(C, 1), np.nan)
    fill = np.where(known, G, np.nanmedian(G))
    for _ in range(400):
        up = np.vstack([fill[:1], fill[:-1]])
        dn = np.vstack([fill[1:], fill[-1:]])
        avg = 0.25 * (up + dn + np.roll(fill, 1, axis=1) + np.roll(fill, -1, axis=1))
        fill = np.where(known, G, avg)
    for _ in range(blur):
        up = np.vstack([fill[:1], fill[:-1]])
        dn = np.vstack([fill[1:], fill[-1:]])
        fill = 0.5 * fill + 0.125 * (up + dn + np.roll(fill, 1, axis=1) + np.roll(fill, -1, axis=1))
    fill = np.hstack([fill, fill[:, :1]])
    return grid_lookup(fill, phi, theta)


def hug(fr, P: np.ndarray, F: np.ndarray, top, hem_mm=5.0, fade_hcm: float = 1.2, detail: float = 1.0,
        floor_mm: float = 5.0, blur: int = 16) -> np.ndarray:
    """Short hair laid onto our scalp (character-look.md section 2): the transfer
    keeps the guide's own hair height over its (mostly unseen, extrapolated)
    skull, and a fused Meshy bust's short hair stands 3-5 cm off it, its cut
    hem 1-2 cm off ours: a helmet. Here the height over our skull is split
    into its low-frequency mass (`height_base`) and the detail on it (locks,
    grooves, spikes); the mass is capped softly by `top` ([[phi, mm], ...]:
    at most that height, tanh), the detail kept x `detail`, never below
    `floor_mm`; and the hem (open-edge loops longer than HUG_HEM_LOOP) is
    laid at `hem_mm` (a number or [[theta, mm], ...]) over the skull, the
    height rising from it over `fade_hcm` along the mesh."""
    phi, theta, R = fr.polar(P)
    ro = fr.skull_r(phi, theta)
    h = R - ro
    base = height_base(fr, P, h, blur)
    tp = np.array(sorted(top), dtype=np.float64)
    hmax = np.interp(phi, tp[:, 0], tp[:, 1]) * 1e-3
    capped = hmax * np.tanh(np.maximum(base, 0.0) / np.maximum(hmax, 1e-6))
    full = np.maximum(capped + (h - base) * detail, floor_mm * 1e-3)
    hem_h = _periodic(hem_mm, theta) * 1e-3
    hem = np.zeros(len(P), dtype=bool)
    for loop in HM.boundary_loops(F):
        ring = P[np.concatenate([loop, loop[:1]])]
        if np.linalg.norm(np.diff(ring, axis=0), axis=1).sum() >= HUG_HEM_LOOP:
            hem[loop] = True
    fade = fade_hcm * fr.hcm
    if hem.any():
        indptr, indices = MO.csr(len(P), MO.edges(F))
        dist = MO.dijkstra(P, indptr, indices, hem, limit=fade * 1.5)
        w = HM._smoothstep(0.0, fade, np.minimum(dist, fade))
    else:
        w = np.ones(len(P))
    hn = hem_h + (full - hem_h) * w
    log(f"hug: hem {int(hem.sum())} vertices, height there {np.percentile(h[hem], 50) * 1000:.1f} -> "
        f"{np.percentile(hn[hem], 50) * 1000:.1f} mm; mass {np.percentile(base, 95) * 1000:.1f} -> "
        f"{np.percentile(capped, 95) * 1000:.1f} mm (95th)")
    d = (P - fr.centre) / np.maximum(R[:, None], 1e-12)
    return fr.centre + d * (ro + hn)[:, None]


push_out = HM.push_out


def _hull(pts: np.ndarray) -> np.ndarray:
    """2D convex hull (monotone chain), counter-clockwise."""
    pts = np.unique(np.round(pts, 6), axis=0)
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for q in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in pts[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return np.array(lower[:-1] + upper[:-1])


def _out_of_hull(H: np.ndarray, Q: np.ndarray, margin: float) -> np.ndarray:
    """Horizontal moves taking points Q (n, 2) out of the convex polygon H
    grown by `margin` (zero for points already outside)."""
    n = len(H)
    A, B = H, np.roll(H, -1, axis=0)
    e = B - A
    nrm = np.stack([e[:, 1], -e[:, 0]], axis=1)  # outward for a CCW polygon
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    d = (Q[:, None, :] - A[None]) @ np.zeros(2) if False else np.einsum("pk,ek->pe", Q, nrm) - np.einsum("ek,ek->e", A, nrm)[None]
    inside = (d < margin).all(axis=1)
    out = np.zeros_like(Q)
    if not inside.any():
        return out
    k = np.argmax(d[inside], axis=1)  # the nearest edge (least inside)
    push = margin - d[inside][np.arange(int(inside.sum())), k]
    out[inside] = nrm[k] * push[:, None]
    return out


FINE_SHARE = 0.8  # long hair: share of the style's clearance the exact push keeps after the drape
FORWARD_BIAS = 1.0  # weight of straight forward / back against the outline's normal
LOCK_MOVE_PCT = 90.0  # percentile of a lock's asked moves it moves by (the rest: the fine push)


def _exit_along(H: np.ndarray, Q: np.ndarray, dirs: np.ndarray, margin: float) -> np.ndarray:
    """Moves along `dirs` taking points Q out of the convex polygon H grown
    by `margin` (zero for points already outside)."""
    A, B = H, np.roll(H, -1, axis=0)
    e = B - A
    nrm = np.stack([e[:, 1], -e[:, 0]], axis=1)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    d = Q @ nrm.T - np.einsum("ek,ek->e", A, nrm)[None]
    inside = (d < margin).all(axis=1)
    nd = dirs @ nrm.T
    t = np.where(nd > 1e-6, (margin - d) / np.maximum(nd, 1e-6), np.inf)
    tt = t.min(axis=1)
    tt = np.where(inside & np.isfinite(tt), tt, 0.0)
    return dirs * tt[:, None]


def drape(fr, P: np.ndarray, F: np.ndarray, trunk_clear: float, flare: float = 0.0, forward_bias: float = 1.0,
          **_) -> np.ndarray:
    """Long hair below the shoulders moved horizontally out of the convex
    outline of the torso above it (the chest or the shoulder blades it
    hangs from), grown by `trunk_clear`: hair hangs straight down in front of
    the chest and the back instead of following the body back in. The moves
    are smoothed over the hair so locks move as sheets."""
    if "torso" not in fr.parts:
        return P
    tco = fr.parts["torso"][0]
    half = float(np.percentile(np.abs(tco[:, 0]), 98))
    band = (np.abs(tco[:, 0]) > 0.45 * half) & (np.abs(tco[:, 0]) < 0.75 * half)
    shoulder_z = float(tco[band, 2].max()) if band.any() else float(fr.neck_z)
    # The robe and shirt worn under the hair are part of the outline (not
    # their sleeves).
    for co, _, _ in HM.garment_parts(fr):
        tco = np.vstack([tco, co[np.abs(co[:, 0]) < 1.05 * half]])
    top = shoulder_z - 0.02
    low = np.nonzero(P[:, 2] < top)[0]
    D = np.zeros_like(P)
    yc = float(np.median(fr.parts["torso"][0][:, 1]))
    if len(low):
        dz = 0.01
        zmin = float(P[low, 2].min())
        for z0 in np.arange(zmin, top, dz):
            sel = low[(P[low, 2] >= z0) & (P[low, 2] < z0 + dz)]
            if not len(sel):
                continue
            src = tco[(tco[:, 2] >= z0) & (tco[:, 2] <= top)]
            if len(src) < 3:
                continue
            H = _hull(src[:, :2])
            if len(H) < 3:
                continue
            # The hull's own outward direction, turned toward straight
            # forward (in front of the chest) or back (behind it): long locks
            # hang in front of the chest and the back, not out to the arms.
            n = _out_of_hull(H, P[sel, :2], 1e-3 + trunk_clear)
            nn = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
            fb = np.zeros_like(nn)
            fb[:, 1] = np.where(P[sel, 1] < yc, -1.0, 1.0)
            w = 1.0 - np.clip((np.abs(P[sel, 0]) - 0.6 * half) / (0.3 * half), 0.0, 1.0)
            dirs = nn + fb * (forward_bias * w)[:, None]
            dirs /= np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-12)
            moved = np.linalg.norm(n, axis=1) > 0
            D[sel[moved], :2] = _exit_along(H, P[sel[moved], :2], dirs[moved], trunk_clear)
    # Each hanging lock (a connected piece of the hair below the shoulders)
    # moves as one: the largest move it asks for, so it hangs straight and
    # keeps its shape; it bends over the 5 cm below the shoulder line.
    out = np.zeros_like(P)
    lowm = np.zeros(len(P), dtype=bool)
    lowm[low] = True
    keep = lowm[F].all(axis=1)
    if keep.any():
        Vs, Fs, idx = MO.submesh(P, F, keep)
        comp = MO.components(len(Vs), MO.edges(Fs))
        for c in np.unique(comp):
            members = idx[comp == c]
            Dm = D[members]
            mag = np.linalg.norm(Dm, axis=1)
            if mag.max() < 1e-5:
                continue
            dirn = Dm.sum(axis=0)
            dirn /= max(float(np.linalg.norm(dirn)), 1e-9)
            amount = float(np.percentile(Dm @ dirn, LOCK_MOVE_PCT))
            out[members] = dirn * amount
    fade = np.clip((top - P[:, 2]) / 0.05, 0.0, 1.0)
    fade = fade * fade * (3 - 2 * fade)
    # Hair down the back hangs a little away from it the lower it goes (an
    # A-line over the robe), so a bow or a stoop does not sweep its ends
    # into the back.
    if flare:
        behind = np.clip((P[:, 1] - yc) / 0.04, 0.0, 1.0)
        out[:, 1] += flare * np.maximum(top - P[:, 2], 0.0) * behind
    return P + out * fade[:, None]


def add_braid(fr, V: np.ndarray, F: np.ndarray, bst: dict, st: dict):
    """The guide's braid dropped (faces with a vertex in `drop` boxes, head
    cm, and the small pieces that leaves) and a plait added
    (charlib/hair_braid.py) along a path through `path` (head-cm control
    points, root first): above `chest_u` it keeps `neck_clear` (plus its own
    half-width) off the body; below, it rests on the convex outline of the
    torso, robe and shirt above it, `clear` off it (hanging straight down
    from the bust, not back under it). Returns the merged mesh and the
    plait's mask, positions, parts and root-to-tip values."""
    from charlib import hair_braid as HB

    H = head_coords(fr, V)
    hit = np.zeros(len(V), dtype=bool)
    for box in bst.get("drop", []):
        hit |= in_box(H, box)
    V, F, _ = MO.submesh(V, F, ~hit[F].any(axis=1))
    comp = MO.components(len(V), MO.edges(F))
    ids, cnt = np.unique(comp, return_counts=True)
    big = np.isin(comp, ids[cnt >= st.get("min_part", 300)])
    V, F, _ = MO.submesh(V, F, big[F].all(axis=1))
    hcm = fr.hcm
    lo = bst.get("loft")
    if lo:
        # The head's hair behind the ears ends on a clean line round the
        # base of the skull (the guide's nape hair and its scraps go); a
        # smooth sheet is lofted from there into the plait below.
        H = head_coords(fr, V)
        # (The line dips to `u_cut` at the centre of the back and rises
        # `x_rise` head cm per head cm to the sides: the hair tapers down
        # to the gathering point, no straight hem.)
        line = lo["u_cut"] + lo.get("x_rise", 0.0) * np.abs(H[:, 0]) \
            + lo.get("u_rise", 0.0) * np.clip(H[:, 1] - lo["f_cut"] + 6.0, 0.0, 6.0) / 6.0
        cut = (H[:, 1] < lo["f_cut"]) & (H[:, 2] < line)
        V, F, _ = MO.submesh(V, F, ~cut[F].any(axis=1))
        comp = MO.components(len(V), MO.edges(F))
        ids, cnt = np.unique(comp, return_counts=True)
        big = np.isin(comp, ids[cnt >= st.get("min_part", 300)])
        V, F, _ = MO.submesh(V, F, big[F].all(axis=1))
        bnd = MO.boundary_vertices(F, len(V))
        V = MO.taubin(V, MO.boundary_edges(F), 10, weight=bnd.astype(np.float64))
    sl = bst.get("sleek")
    if sl:
        V = sleek_shell(fr, V, F, sl, st)
    C = np.array(bst["path"], dtype=np.float64)
    Cw = fr.centre + np.stack([C[:, 0], -C[:, 1], C[:, 2]], axis=1) * hcm
    P = HB.catmull_rom(Cw, 16)
    P = HB.resample(P, 0.004)
    hw, hd = bst["half_width"], bst["half_depth"]
    sa, sb = bst["strand_a"], bst["strand_b"]
    face_off = hd + sb + bst.get("clear", 0.008)
    side_off = hw + sa + bst.get("neck_clear", 0.006)
    chest_z = fr.centre[2] + bst["chest_u"] * hcm
    top = HM.shoulder_line(fr)
    out = [fr.parts["torso"][0]] + [co for co, _, _ in HM.garment_parts(fr)]
    pts = np.vstack(out)
    # Below chest_z: on the outline above, in front (the body faces -y).
    for i in range(len(P)):
        p = P[i]
        if p[2] < chest_z:
            m = (np.abs(pts[:, 0] - p[0]) < 0.015) & (pts[:, 2] >= p[2] - 0.004) & (pts[:, 2] <= max(top, p[2] + 0.01))
            if m.any():
                # Resting on it, faded in over 2 cm below chest_z.
                k = float(np.clip((chest_z - p[2]) / 0.02, 0.0, 1.0))
                y_rest = float(pts[m, 1].min()) - face_off
                P[i, 1] = (1.0 - k) * min(p[1], y_rest) + k * y_rest
    # Above: kept off the neck, the shoulders and the head.
    for _ in range(3):
        for i in range(len(P)):
            if P[i, 2] >= chest_z - 0.01:
                P[i] = fr.clear(P[i], side_off, fr.collide, reach=side_off + 0.03)
        for _ in range(4):
            P[1:-1] = 0.5 * P[1:-1] + 0.25 * (P[:-2] + P[2:])
    # Out of the face: away from the body (nearest surface), smoothed.
    O = np.zeros_like(P)
    for i, p in enumerate(P):
        loc, nrm, _, _ = fr.collide.find_nearest(Vector(tuple(p)), 0.3)
        d = p - np.array(loc) if loc is not None else np.array([0.0, -1.0, 0.0])
        O[i] = d / max(float(np.linalg.norm(d)), 1e-9)
    for _ in range(8):
        O[1:-1] = 0.5 * O[1:-1] + 0.25 * (O[:-2] + O[2:])
    O /= np.maximum(np.linalg.norm(O, axis=1, keepdims=True), 1e-12)
    b = HB.plait(P, O, half_width=hw, half_depth=hd, period=bst["period"], strand_a=sa, strand_b=sb,
                 ring=bst.get("ring", 10), ds=bst.get("ds", 0.0025), root_len=bst.get("root_len", 0.03),
                 end_scale=bst.get("end_scale", 0.72), tuft_len=bst.get("tuft_len", 0.055),
                 root_open=bst.get("root_open", 0.0), root_swell=bst.get("root_swell", 1.0))
    if lo:
        V, F = loft_into_plait(fr, V, F, b, lo, bst)
    bV, bF = b["V"], b["F"]
    bpart, brtt = b["part"], b["rtt"]
    n0 = len(V)
    V2 = np.vstack([V, bV])
    F2 = np.vstack([F, bF + n0])
    mask = np.zeros(len(V2), dtype=bool)
    mask[n0:] = True
    return V2, F2, {"mask": mask, "V": bV, "part": bpart, "rtt": brtt, "path": b["path"],
                    "tris": int(len(bF))}


def sleek_shell(fr, V: np.ndarray, F: np.ndarray, sl: dict, st: dict) -> np.ndarray:
    """The hair over the head combed sleek toward a gathering point (a low
    braid's root, `to`, head cm): behind `f_front` (head cm; the bangs and
    the hair in front of the ears untouched) smoothed (`smooth` passes, the
    hem held) and brought `k` of the way to `floor` head cm over the skull
    (its flow lines are shading: hair_meshy.flow_lines)."""
    hcm = fr.hcm
    E = MO.edges(F)
    H = head_coords(fr, V)
    w = np.clip((sl.get("f_front", 0.5) - H[:, 1]) / sl.get("f_soft", 3.0), 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    hold = HM.tip_weight(V, F)
    sw = w * (1.0 - 0.85 * hold)
    sw[MO.boundary_vertices(F, len(V))] = 0.0
    V = MO.taubin(V, E, sl.get("smooth", 40), weight=sw)
    phi, theta, r = fr.polar(V)
    rs = fr.skull_r(phi, theta)
    floor = sl.get("floor", 0.35) * hcm
    h = np.maximum(r - rs - floor, 0.0)
    rn = rs + floor + h * (1.0 - sl.get("k", 0.5) * w)
    rn = np.where(r - rs > floor, rn, r)
    d = (V - fr.centre) / np.maximum(r[:, None], 1e-12)
    V = fr.centre + d * rn[:, None]
    return V


def loft_into_plait(fr, V: np.ndarray, F: np.ndarray, b: dict, lo: dict, bst: dict) -> tuple[np.ndarray, np.ndarray]:
    """A smooth sheet from the head hair's lower edge behind the ears (its
    open boundary behind `f_cut` head cm, below `u_top`) into the plait's
    first `into_m`: each edge vertex, in order round the head, runs to its
    place across the plait's width (`spread` of its half-width), bowed
    `clear` off the head and neck; `rows` rows. The edge vertices are
    shared: the sheet continues the hair with no seam."""
    from charlib import hair_braid as HB

    H = head_coords(fr, V)
    bnd = np.nonzero(MO.boundary_vertices(F, len(V)))[0]
    sel = bnd[(H[bnd, 1] < lo["f_cut"] - lo.get("f_in", 0.5)) & (H[bnd, 2] < lo.get("u_top", 0.0))]
    # Round the head from the right side, past the back, to the left.
    az = np.degrees(np.arctan2(H[sel, 0], -H[sel, 1]))
    sel = sel[np.argsort(az)]
    az = np.sort(az)
    path, T, Wd = b["path"], b["T"], b["W"]
    sp = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))])
    k0 = int(np.searchsorted(sp, lo.get("into_m", 0.02)))
    hw = b.get("hw", bst["half_width"])
    lat = (az - az.min()) / max(float(az.max() - az.min()), 1e-6) * 2.0 - 1.0
    rows = lo.get("rows", 8)
    cl = lo.get("clear", 0.006)
    newV = []
    for i, vi in zip(lat, sel):
        B = V[vi]
        # The plait's far side is its left (the path turns left round the
        # neck): the head's right-side hair meets its right edge.
        R = path[k0] - Wd[k0] * i * hw * lo.get("spread", 0.85)
        M = 0.5 * (B + R)
        M = fr.clear(M, cl + 0.004, fr.collide, reach=0.06)
        # Leaving the edge along the hair's own downward run.
        D = B + (M - B) * 0.35
        Pc = HB.catmull_rom(np.array([B, D, M, R]), 10)
        Pc = HB.resample(Pc, max(float(np.linalg.norm(np.diff(Pc, axis=0), axis=1).sum()) / (rows - 1), 1e-4))
        if len(Pc) != rows:
            t = np.linspace(0, len(Pc) - 1, rows)
            Pc = np.array([Pc[int(np.floor(x))] * (1 - (x - np.floor(x))) + Pc[min(int(np.floor(x)) + 1, len(Pc) - 1)]
                           * (x - np.floor(x)) for x in t])
        for j in range(1, rows):
            Pc[j] = fr.clear(Pc[j], cl, fr.collide, reach=cl + 0.03)
        newV.append(Pc[1:])
    newV = np.array(newV)  # (cols, rows - 1, 3)
    # Smoothed across the columns (neighbouring edge vertices can be uneven).
    for _ in range(3):
        newV[1:-1] = 0.5 * newV[1:-1] + 0.25 * (newV[:-2] + newV[2:])
    n0 = len(V)
    idx = np.zeros((len(sel), rows), dtype=np.int64)
    idx[:, 0] = sel
    idx[:, 1:] = n0 + np.arange(len(sel) * (rows - 1)).reshape(len(sel), rows - 1)
    Fl = []
    for i in range(len(sel) - 1):
        for j in range(rows - 1):
            a_, b_, c_, d_ = idx[i, j], idx[i + 1, j], idx[i + 1, j + 1], idx[i, j + 1]
            Fl += [[a_, b_, c_], [a_, c_, d_]]
    Fl = np.array(Fl, dtype=np.int64)
    V2 = np.vstack([V, newV.reshape(-1, 3)])
    # Facing out: as the shell's faces at the shared edge.
    fc = V2[Fl].mean(axis=1)
    nrm = np.cross(V2[Fl[:, 1]] - V2[Fl[:, 0]], V2[Fl[:, 2]] - V2[Fl[:, 0]])
    out = fc - fr.centre
    if float(np.sum(np.einsum("ij,ij->i", nrm, out) > 0)) < 0.5 * len(Fl):
        Fl = Fl[:, [0, 2, 1]]
    return V2, np.vstack([F, Fl])


def _ctrl(fr, c, tree=None) -> np.ndarray:
    """A control point: ("p", phi, theta, extra head cm) over the skull,
    ("s", phi, theta, extra) over the hair's own surface (the skull + 1.2
    head cm where the ray misses it), or ("h", x, f, u) in head cm."""
    if c[0] == "s" and tree is not None:
        d = fr.dirs(c[1], c[2])
        o = fr.centre + d * 0.4
        hit = tree.ray_cast(Vector(tuple(o)), Vector(tuple(-d)), 0.4)
        if hit[0] is not None:
            return np.array(hit[0]) + d * c[3] * fr.hcm
        return fr.point(c[1], c[2], (1.2 + c[3]) * fr.hcm)
    if c[0] in ("p", "s"):
        return fr.point(c[1], c[2], c[3] * fr.hcm)
    return fr.centre + np.array([c[1], -c[2], c[3]]) * fr.hcm


def braid_normals(V: np.ndarray, F: np.ndarray, braid: dict, share: float = 0.35) -> np.ndarray:
    """The plait's shading normals: mostly its own (the strands' round
    sections and crossings read in the toon step), `share` of the round
    tube about its path."""
    own = MO.vertex_normals(V, F)[braid["mask"]]
    Vb = V[braid["mask"]]
    tube = HM.tube_normals(Vb, braid["path"][::4])
    n = own * (1.0 - share) + tube * share
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def _hull_dist(H: np.ndarray, Q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Signed distance of points Q (n, 2) to the convex polygon H (outside
    positive; the largest edge-plane distance) and the outward normal of
    that edge."""
    A, B = H, np.roll(H, -1, axis=0)
    e = B - A
    nrm = np.stack([e[:, 1], -e[:, 0]], axis=1)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    d = Q @ nrm.T - np.einsum("ek,ek->e", A, nrm)[None]
    k = np.argmax(d, axis=1)
    return d[np.arange(len(Q)), k], nrm[k]


def _blur_grid(G: np.ndarray, ok: np.ndarray, sa: float, sz: float) -> np.ndarray:
    """Normalised Gaussian blur of a (azimuth, z, ...) grid over its valid
    cells, wrapping in azimuth; cells with no valid neighbour stay 0."""
    def kern(s):
        r = max(1, int(round(3 * s)))
        k = np.exp(-0.5 * (np.arange(-r, r + 1) / max(s, 1e-6)) ** 2)
        return k / k.sum(), r

    ka, ra = kern(sa)
    kz, rz = kern(sz)
    w = ok.astype(np.float64)
    val = G * (w[..., None] if G.ndim == 3 else w)
    def conv(X):
        Xp = np.concatenate([X[-ra:], X, X[:ra]], axis=0)
        X = sum(ka[i] * Xp[i:i + X.shape[0]] for i in range(len(ka)))
        pad = [(0, 0)] * X.ndim
        pad[1] = (rz, rz)
        Xp = np.pad(X, pad, mode="edge")
        return sum(kz[i] * Xp[:, i:i + X.shape[1]] for i in range(len(kz)))
    num = conv(val)
    den = conv(w)
    den = den[..., None] if G.ndim == 3 else den
    return np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0.0)


def rest_drape(fr, P: np.ndarray, F: np.ndarray, clear: float, pull_max: float = 0.08, pull_slope: float = 0.6,
               below: float = 0.02, fade_m: float = 0.03, bins: int = 72, gather: float = 1.0,
               gather_back: float = 1.0, gather_from: float = 0.07, shift_in: float = 0.0,
               back_slope: float = 0.0, fb_bias: float = 0.0, fb_depth: float = 0.08, blur: tuple = (1.5, 2.0),
               **_) -> np.ndarray:
    """Long hair below the shoulders brought to rest on the body under
    gravity. Round the body's axis, per azimuth (5 degrees) and 1 cm slice,
    the convex outline of the torso, robe and shirt above the slice (the
    chest or the shoulder blades the hair hangs from: below the bust it
    hangs straight down from it, not back in) and its outward normal there;
    the innermost hair in the cell is moved along that normal to lie `clear`
    off it: pushed out where it is inside, drawn in where it stands off in
    front of the chest or behind the back (by at most `pull_max`, and at
    most `pull_slope` x the depth below the shoulder line, so it bends in
    smoothly from the shoulders). The moves are one smooth field over
    azimuth and height applied to every vertex in the cell, so locks keep
    their shape and layers and move as sheets. `gather` < 1 first draws the
    hair beyond `gather_from` m out from the middle in toward it (that
    distance scaled by `gather` in front of the body, `gather_back` behind,
    from just above the shoulder line down, blended in from the chin): the
    curtains falling over the front and back of the shoulders, onto the
    chest and the shoulder blades, not out over the shoulder caps.
    `shift_in` does the same for the hair in front by moving it sideways
    toward the middle by that many metres instead of scaling (locks keep
    their width and spacing, so neighbouring strands never cross). `back_slope`: behind the body the clearance grows by
    that much per metre below the shoulder line (the hair's ends clear of a
    robe swinging at the waist in the walk and the run). `fb_bias`: near
    the shoulders (fading out over `fb_depth` below the shoulder line) the
    moves turn toward straight forward or back by that weight, so hair
    caught in the shoulders goes in front of them or behind, not out over
    their tops as a flap."""
    if "torso" not in fr.parts:
        return P
    tco = fr.parts["torso"][0]
    half = float(np.percentile(np.abs(tco[:, 0]), 98))
    top = HM.shoulder_line(fr) - below
    yc = float(np.median(tco[:, 1]))
    if shift_in:
        P = P.copy()
        z1 = HM.shoulder_line(fr) + 0.01
        t = np.clip((fr.chin_z - P[:, 2]) / max(fr.chin_z - z1, 1e-6), 0.0, 1.0)
        t = t * t * (3 - 2 * t)
        front = np.clip((yc - P[:, 1]) / 0.03 + 0.5, 0.0, 1.0)
        side = np.clip((np.abs(P[:, 0]) - 0.03) / 0.03, 0.0, 1.0)
        P[:, 0] -= np.sign(P[:, 0]) * shift_in * t * front * side
    if gather < 1.0 or gather_back < 1.0:
        P = P.copy()
        z1 = HM.shoulder_line(fr) + 0.01
        t = np.clip((fr.chin_z - P[:, 2]) / max(fr.chin_z - z1, 1e-6), 0.0, 1.0)
        t = t * t * (3 - 2 * t)
        front = np.clip((yc - P[:, 1]) / 0.03 + 0.5, 0.0, 1.0)
        g = gather_back + (gather - gather_back) * front
        r = np.abs(P[:, 0])
        over = np.maximum(r - gather_from, 0.0)
        P[:, 0] = np.sign(P[:, 0]) * (r - over * (1.0 - g) * t)
    for co, _, _ in HM.garment_parts(fr):
        tco = np.vstack([tco, co[np.abs(co[:, 0]) < 1.05 * half]])
    low = np.nonzero(P[:, 2] < top)[0]
    if not len(low):
        return P
    dz = 0.01
    z0 = float(P[low, 2].min())
    nz = int(np.ceil((top - z0) / dz))
    c = np.array([0.0, yc])
    az = np.arctan2(P[:, 1] - yc, P[:, 0])
    ai = np.floor((az + np.pi) / (2 * np.pi) * bins).astype(np.int64) % bins
    zi = np.clip(np.floor((P[:, 2] - z0) / dz).astype(np.int64), 0, nz - 1)
    M = np.zeros((bins, nz, 2))
    ok = np.zeros((bins, nz), dtype=bool)
    centres = (np.arange(bins) + 0.5) / bins * 2 * np.pi - np.pi
    for k in range(nz):
        zz = z0 + k * dz
        src = tco[(tco[:, 2] >= zz) & (tco[:, 2] <= top)]
        if len(src) < 3:
            continue
        H = _hull(src[:, :2])
        if len(H) < 3:
            continue
        # The outline's boundary densely sampled; per azimuth bin the
        # boundary point and edge normal it faces.
        A, B = H, np.roll(H, -1, axis=0)
        e = B - A
        nrm = np.stack([e[:, 1], -e[:, 0]], axis=1)
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        t = np.linspace(0.0, 1.0, 24, endpoint=False)
        pts = (A[:, None, :] + e[:, None, :] * t[None, :, None]).reshape(-1, 2)
        pn = np.repeat(nrm, len(t), axis=0)
        pa = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
        diff = np.abs(((pa[None, :] - centres[:, None]) + np.pi) % (2 * np.pi) - np.pi)
        j = np.argmin(diff, axis=1)
        hb, nb = pts[j], pn[j]
        sel = low[zi[low] == k]
        if not len(sel):
            continue
        d = np.einsum("ij,ij->i", P[sel, :2] - hb[ai[sel]], nb[ai[sel]])
        for a_ in np.unique(ai[sel]):
            m = sel[ai[sel] == a_]
            dd = d[ai[sel] == a_]
            dd = dd[dd < 0.2]
            if not len(dd):
                continue
            mv = clear + back_slope * max(top - zz, 0.0) * float(max(nb[a_, 1], 0.0) ** 2) - float(dd.min())
            if mv < 0.0:
                lim = min(pull_max, pull_slope * max(top - (zz + 0.5 * dz), 0.0))
                mv = max(mv, -lim) * float(nb[a_, 1] ** 2)
            dirn = nb[a_].copy()
            if fb_bias:
                bb_ = fb_bias * max(0.0, 1.0 - max(top - zz, 0.0) / fb_depth)
                dirn = dirn + np.array([0.0, np.sign(np.sin(centres[a_])) * bb_])
                dirn /= max(float(np.linalg.norm(dirn)), 1e-9)
                # The same clearance along the outline's normal.
                dirn /= max(float(dirn @ nb[a_]), 0.3)
            M[a_, k] = dirn * mv
            ok[a_, k] = True
    M = _blur_grid(M, ok, *blur)
    # Bilinear in azimuth and height at each vertex.
    fa = (az + np.pi) / (2 * np.pi) * bins - 0.5
    a0 = np.floor(fa).astype(np.int64)
    ta = fa - a0
    fz = np.clip((P[:, 2] - z0) / dz - 0.5, 0.0, nz - 1.0)
    k0 = np.minimum(np.floor(fz).astype(np.int64), nz - 1)
    k1 = np.minimum(k0 + 1, nz - 1)
    tz = fz - k0
    a0m, a1m = a0 % bins, (a0 + 1) % bins
    D2 = ((1 - ta)[:, None] * ((1 - tz)[:, None] * M[a0m, k0] + tz[:, None] * M[a0m, k1])
          + ta[:, None] * ((1 - tz)[:, None] * M[a1m, k0] + tz[:, None] * M[a1m, k1]))
    fade = np.clip((top - P[:, 2]) / fade_m, 0.0, 1.0)
    fade = fade * fade * (3 - 2 * fade)
    out = P.copy()
    out[:, :2] += D2 * fade[:, None]
    return out


def straighten(fr, P: np.ndarray, F: np.ndarray, keep: float, below: float = 0.02) -> np.ndarray:
    """Long locks hanging out to the sides brought back under their root:
    each hanging piece (connected hair below `below` under the shoulders)
    keeps `keep` of the sideways drift of its centre line from where it
    leaves the shoulders, its width and shape unchanged. A piece hanging
    straight or centred (the back curtain) does not move."""
    if "torso" not in fr.parts:
        return P
    tco = fr.parts["torso"][0]
    half = float(np.percentile(np.abs(tco[:, 0]), 98))
    band = (np.abs(tco[:, 0]) > 0.45 * half) & (np.abs(tco[:, 0]) < 0.75 * half)
    top = (float(tco[band, 2].max()) if band.any() else float(fr.neck_z)) - below
    lowm = P[:, 2] < top
    keepf = lowm[F].all(axis=1)
    out = P.copy()
    if not keepf.any():
        return out
    Vs, Fs, idx = MO.submesh(P, F, keepf)
    comp = MO.components(len(Vs), MO.edges(Fs))
    for c in np.unique(comp):
        m = idx[comp == c]
        z = P[m, 2]
        if z.max() - z.min() < 0.05:
            continue
        x0 = float(P[m][z > z.max() - 0.03, 0].mean())
        # The centre line's x per 1 cm slice, smoothed, against the root's.
        bins = np.floor((z.max() - z) / 0.01).astype(int)
        cx = np.array([P[m][bins == b, 0].mean() if (bins == b).any() else np.nan for b in range(bins.max() + 1)])
        ok = ~np.isnan(cx)
        cx = np.interp(np.arange(len(cx)), np.nonzero(ok)[0], cx[ok])
        cx = np.convolve(np.pad(cx, 2, mode="edge"), np.ones(5) / 5.0, mode="valid")
        drift = cx[bins] - x0
        out[m, 0] -= drift * (1.0 - keep)
    return out


def coherent(P: np.ndarray, D: np.ndarray, radius: float) -> np.ndarray:
    """Moves made coherent in space (not along the mesh): each vertex takes
    the largest move asked within `radius`, then the tent-weighted mean of
    those over `radius`, so both faces of a lock, neighbouring locks and a
    sheet move together; never less than a vertex's own move along it."""
    from mathutils.kdtree import KDTree

    mag = np.linalg.norm(D, axis=1)
    moving = np.nonzero(mag > 1e-6)[0]
    if not len(moving):
        return D
    mt = KDTree(len(moving))
    for i in moving:
        mt.insert(Vector(tuple(P[i])), int(i))
    mt.balance()
    big = np.zeros_like(D)
    near = np.zeros(len(P), dtype=bool)
    for i in range(len(P)):
        best, bm = -1, 0.0
        for _, j, _ in mt.find_range(Vector(tuple(P[i])), radius):
            if mag[j] > bm:
                bm, best = mag[j], j
        if best >= 0:
            big[i] = D[best]
            near[i] = True
    idx = np.nonzero(near)[0]
    tree = KDTree(len(idx))
    for i in idx:
        tree.insert(Vector(tuple(P[i])), int(i))
    tree.balance()
    out = np.zeros_like(D)
    for i in range(len(P)):
        hits = tree.find_range(Vector(tuple(P[i])), 2.0 * radius)
        if not hits:
            continue
        j = np.array([h[1] for h in hits])
        w = np.maximum(1.0 - np.array([h[2] for h in hits]) / (2.0 * radius), 0.0)
        # Vertices with no move near them count as zero moves.
        out[i] = (big[j] * w[:, None]).sum(axis=0) / max(float(w.sum()) + 1e-9, 1e-9)
    own = np.einsum("ij,ij->i", out, D) / np.maximum(mag, 1e-12)
    short = np.where(mag > 1e-9, np.maximum(mag - own, 0.0) / np.maximum(mag, 1e-12), 0.0)
    return out + D * short[:, None]


def push_out_smooth(fr, P: np.ndarray, F: np.ndarray, rounds: int = 4, radius: float = 0.025,
                    head_exact: bool = False, **clear) -> np.ndarray:
    """Out of the body (and the robe and shirt) as sheets, not vertex by
    vertex: each round the pushes asked for are made coherent in space
    (`coherent`) before they are applied, so hair resting on the chest, the
    shoulders or the collar moves out together instead of crumpling; a last
    exact push settles what is left.

    `head_exact` (short hair laid on the scalp, `hug`): above the chin each
    vertex takes only its own push. The coherent move gives every vertex the
    largest push asked within `radius` (2.5 cm), so one dip into the skin
    beside an ear lifts the whole hem round it off the scalp, each round
    again (a hugged crew cut's hem went from 5 to 9 mm over the skull)."""
    out = P.copy()
    head = P[:, 2] > fr.chin_z + 0.01 if head_exact else np.zeros(len(P), dtype=bool)
    for _ in range(rounds):
        need = push_out(fr, out, rounds=1, **clear) - out
        if np.linalg.norm(need, axis=1).max() < 5e-4:
            break
        move = coherent(out, np.where(head[:, None], 0.0, need), radius)
        move[head] = need[head]
        out = out + move
    return push_out(fr, out, **clear)


# ----------------------------------------------------------------------------
# Debug renders
# ----------------------------------------------------------------------------


def debug_mesh(name: str, V: np.ndarray, F: np.ndarray, colours: np.ndarray | None = None):
    me = bpy.data.meshes.new(name)
    me.from_pydata(V.tolist(), [], F.tolist())
    me.update()
    if colours is not None:
        a = me.color_attributes.new("dbg", "FLOAT_COLOR", "POINT")
        c = np.ones((len(V), 4), dtype=np.float32)
        c[:, :3] = colours
        a.data.foreach_set("color", c.ravel())
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    return o


SEG_VIEWS = ((0, 5), (35, 8), (90, 5), (180, 8), (-90, 5))  # the segmentation's: both sides


def debug_shots(path: str, fr, objs, views=((0, 5), (35, 8), (90, 5), (180, 8)), dist=0.9, drop=0.06, size=(480, 560)):
    from charlib import montage
    from charlib import render as RD

    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "VERTEX"
    sc.render.resolution_x, sc.render.resolution_y = size
    if sc.camera is None:
        cam = bpy.data.cameras.new("dbg_cam")
        co = bpy.data.objects.new("dbg_cam", cam)
        sc.collection.objects.link(co)
        sc.camera = co
    for o in bpy.data.objects:
        if o.type == "MESH":
            o.hide_render = o not in objs
    files = []
    tgt = (0.0, float(fr.centre[1]), float(fr.centre[2]) - drop)
    for i, (az, el) in enumerate(views):
        RD.aim(tgt, az, el, dist, lens=60)
        files.append(RD.shoot(f"{path}_{i}.png"))
    montage.tile(files, path + ".png", len(files), remove=True)


def body_debug_objects(fr):
    out = []
    for r in ("head", "ears", "neck", "torso", "scalp"):
        if r in fr.parts:
            co, _, t = fr.parts[r]
            out.append(debug_mesh(f"dbg_body_{r}", co, t, np.tile([0.85, 0.55, 0.5], (len(co), 1))))
    return out


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def remove_debug(*objs) -> None:
    for o in objs:
        me = o.data
        bpy.data.objects.remove(o)
        if me.users == 0:
            bpy.data.meshes.remove(me)


def prepare(a, bb, fr, name: str, st: dict) -> dict:
    t0 = time.time()
    guide = os.path.join(os.path.abspath(a.meshy), st["guide"])
    V, F = import_guide(guide)
    log(f"{name}: guide {len(V)} vertices {len(F)} triangles ({time.time() - t0:.1f}s)")
    out = os.path.abspath(a.out)
    if st.get("registration") and not a.register:
        reg = dict(st["registration"])
    else:
        reg = registration(fr, V, F, os.path.join(out, "cache", f"{name}_{st['guide_id']}_{bb.id}_reg.json"),
                           st.get("register_scales"))
    Vr = V * reg["scale"] + np.array(reg["offset"])
    log(f"{name}: registered scale {reg['scale']:.4f} offset {reg['offset']} rms {reg['fit_rms_mm']} mm")
    dbg: dict = {}
    t1 = time.time()
    is_hair = segment(fr, st, Vr, F, dbg)
    log(f"{name}: segmented {int(is_hair.sum())} hair vertices of {len(Vr)} ({time.time() - t1:.1f}s)")
    d = os.path.abspath(a.debug) if a.debug else ""
    if d:
        os.makedirs(d, exist_ok=True)
        seeds = dbg["seeds"]
        col = np.tile([0.75, 0.75, 0.75], (len(Vr), 1))
        col[seeds == 0] = [0.2, 0.4, 1.0]
        col[seeds == 1] = [1.0, 0.3, 0.2]
        o = debug_mesh("dbg_seeds", Vr, F, col)
        debug_shots(os.path.join(d, f"{name}_1seeds"), fr, [o], views=SEG_VIEWS)
        col = np.tile([0.8, 0.7, 0.6], (len(Vr), 1))
        col[is_hair] = [0.25, 0.55, 0.95]
        o2 = debug_mesh("dbg_seg", Vr, F, col)
        debug_shots(os.path.join(d, f"{name}_2seg"), fr, [o2], views=SEG_VIEWS)
        remove_debug(o, o2)
    if a.stage == "segment":
        return {}
    # The guide's skull and the transfer onto ours.
    t1 = time.time()
    K = guide_skull(fr, Vr, F, is_hair, dbg)
    # Low relief on the skin (an undercut sculpted onto the neck): skin, by
    # its height over the guide's skull; then the skull again.
    Vh, Fh, gidx = hair_shell(Vr, F, is_hair, st.get("min_part", 300))
    line_z = fr.ear["bottom"] if fr.ear else fr.eye_z - 3.0 * fr.hcm
    Vt = transfer(fr, K, Vh, st.get("thickness", 1.0), line_z)
    if st.get("crown_flatten"):
        Vt = flatten_crown(fr, Vt, **st["crown_flatten"])
    if st.get("face_lift"):
        Vt = lift_over_face(fr, Vt, **st["face_lift"])
    # Low relief lying on our head after the fit (an undercut sculpted onto
    # the guide's skin): its faces go, the inner cap's deep shade shows.
    for rule in st.get("relief_drop", []):
        Hh = head_coords(fr, Vt)
        dep = signed_depth(fr.collide, Vt, 0.05)
        thin = in_box(Hh, rule["box"]) & (dep < rule["depth_m"])
        keepf = ~thin[Fh].any(axis=1)
        Vt, Fh, sub = MO.submesh(Vt, Fh, keepf)
        comp = MO.components(len(Vt), MO.edges(Fh))
        ids, cnt = np.unique(comp, return_counts=True)
        big = np.isin(comp, ids[cnt >= st.get("min_part", 300)])
        Vt, Fh, sub2 = MO.submesh(Vt, Fh, big[Fh].all(axis=1))
        Vh = Vh[sub][sub2]
    if st.get("cut_u"):
        # A clean cut line round the head (an undercut's edge): the shell
        # below it goes, whatever its height (a depth threshold cuts low
        # relief into islands and slits).
        Hh = head_coords(fr, Vt)
        _, th_c, _ = fr.polar(Vt)
        below = Hh[:, 2] < _periodic(st["cut_u"], th_c)
        Vt, Fh, sub = MO.submesh(Vt, Fh, ~below[Fh].any(axis=1))
        comp = MO.components(len(Vt), MO.edges(Fh))
        ids, cnt = np.unique(comp, return_counts=True)
        big = np.isin(comp, ids[cnt >= st.get("min_part", 300)])
        Vt, Fh, sub2 = MO.submesh(Vt, Fh, big[Fh].all(axis=1))
        Vh = Vh[sub][sub2]
    if st.get("hug"):
        Vt = hug(fr, Vt, Fh, **st["hug"])
    log(f"{name}: skull ratio known on {dbg['skull_known'] * 100:.0f}% of the grid, k {np.percentile(K, 5):.3f}-"
        f"{np.percentile(K, 95):.3f}; shell {len(Vh)} vertices {len(Fh)} triangles ({time.time() - t1:.1f}s)")
    if d:
        body = body_debug_objects(fr)
        o = debug_mesh("dbg_transfer", Vt, Fh, np.tile([0.3, 0.55, 0.95], (len(Vt), 1)))
        o2 = debug_mesh("dbg_reg", Vh, Fh, np.tile([0.95, 0.85, 0.3], (len(Vh), 1)))
        debug_shots(os.path.join(d, f"{name}_3reg"), fr, [o2] + body)
        debug_shots(os.path.join(d, f"{name}_4transfer"), fr, [o] + body)
        remove_debug(o, o2, *body)
    # Clean: Taubin smoothing with the hem pinned and the tips held.
    t1 = time.time()
    E = MO.edges(Fh)
    hold = HM.tip_weight(Vt, Fh)
    w = 1.0 - 0.85 * hold
    bnd = MO.boundary_vertices(Fh, len(Vt))
    w[bnd] = 0.0
    # The open hem (where the hair met the guide's skin) smoothed along
    # itself first: the watershed's crease line is ragged at the scale of a
    # few triangles.
    BE = MO.boundary_edges(Fh)
    Vt = MO.taubin(Vt, BE, 8, weight=bnd.astype(np.float64))
    Vs = MO.taubin(Vt, E, st.get("smooth_iters", 12), weight=w)
    bst = st.get("braid")
    budget = bst.get("budget", 14000) if bst else 0
    Vd, Fd = HM.decimate(Vs, Fh, HM.SOURCE_MAX_TRIS - budget, protect=HM.tip_weight(Vs, Fh))
    braid = None
    if bst:
        # The guide's own braid goes; the plait is built along a path resting
        # on the body and joined to the head hair (charlib/hair_braid.py).
        Vd, Fd, braid = add_braid(fr, Vd, Fd, bst, st)
        log(f"{name}: plait {int(braid['mask'].sum())} vertices {braid['tris']} triangles")
    log(f"{name}: smoothed and decimated to {len(Vd)} vertices {len(Fd)} triangles ({time.time() - t1:.1f}s)")
    t1 = time.time()
    clear = dict(covers_ears=st.get("covers_ears", False), trunk_clear=st.get("trunk_clear", 0.012),
                 arm_clear=st.get("arm_clear", 0.0))
    if d:
        body = body_debug_objects(fr)
        o = debug_mesh("dbg_dec", Vd, Fd, np.tile([0.9, 0.9, 0.92], (len(Vd), 1)))
        o2 = debug_mesh("dbg_sm", Vs, Fh, np.tile([0.9, 0.9, 0.92], (len(Vs), 1)))
        debug_shots(os.path.join(d, f"{name}_4bsmooth"), fr, [o2] + body, drop=0.12 if st["length"] == "long" else 0.06,
                    dist=1.3 if st["length"] == "long" else 0.9)
        debug_shots(os.path.join(d, f"{name}_4cdec"), fr, [o] + body, drop=0.12 if st["length"] == "long" else 0.06,
                    dist=1.3 if st["length"] == "long" else 0.9)
        remove_debug(o, o2, *body)
    if braid is not None:
        # The plait's vertices are restored after the drape and the pushes.
        pass
    if st.get("straighten") is not None:
        Vd = straighten(fr, Vd, Fd, st["straighten"])
    if st.get("rest"):
        # Resting on the body under gravity (the style follows the body's
        # chest and back in the clips: hair_meshy.body_follow).
        Vp = rest_drape(fr, Vd, Fd, **st["rest"])
    elif st["length"] == "long":
        Vp = drape(fr, Vd, Fd, flare=st.get("back_flare", 0.0), forward_bias=st.get("forward_bias", FORWARD_BIAS), **clear)
    else:
        Vp = Vd
    if d:
        body = body_debug_objects(fr)
        o = debug_mesh("dbg_drape", Vp, Fd, np.tile([0.9, 0.9, 0.92], (len(Vp), 1)))
        debug_shots(os.path.join(d, f"{name}_4ddrape"), fr, [o] + body, drop=0.12 if st["length"] == "long" else 0.06,
                    dist=1.3 if st["length"] == "long" else 0.9)
        remove_debug(o, *body)
    # The torso is cleared by the drape: the exact push only fixes what is
    # left (a smaller clearance below the chin).
    long = st["length"] == "long"
    fine = dict(clear, trunk_clear=clear["trunk_clear"] * (FINE_SHARE if long else 1.0))
    # Hair laid on the scalp (`hug`) is pushed vertex by vertex on the head
    # (push_out_smooth) and keeps its hem where the hug laid it.
    hugged = bool(st.get("hug"))
    Vp = push_out_smooth(fr, Vp, Fd, head_exact=hugged, **fine)
    E2 = MO.edges(Fd)
    w2 = 1.0 - HM.tip_weight(Vp, Fd)
    if braid is not None:
        # The plait was laid on the body already: it keeps its shape.
        Vp[braid["mask"]] = braid["V"]
        w2[braid["mask"]] = 0.0
    if hugged:
        w2[MO.boundary_vertices(Fd, len(Vp))] = 0.0
    Vp = MO.taubin(Vp, E2, 4, weight=w2)
    Vp = push_out_smooth(fr, Vp, Fd, rounds=3, head_exact=hugged, **fine)
    if braid is not None:
        Vp[braid["mask"]] = braid["V"]
    part = HM.label_parts(fr, st, Vp, Fd)
    rtt = HM.root_to_tip(fr, Vp, Fd, part)
    if braid is not None:
        part[braid["mask"]] = braid["part"]
        rtt[braid["mask"]] = braid["rtt"]
    N = HM.shading_normals(fr, Vp, Fd, part, rtt, line_z, st.get("proxy_share"))
    if braid is not None and "path" in braid:
        N[braid["mask"]] = braid_normals(Vp, Fd, braid)
    log(f"{name}: pushed out, labelled, normals ({time.time() - t1:.1f}s); parts "
        + ", ".join(f"{HM.PARTS[p]} {int((part == p).sum())}" for p in range(len(HM.PARTS)) if (part == p).any()))
    meta = {"source": st["guide"], "guide_id": st["guide_id"], "sha256": sha256(guide), "body": bb.id, "tool_version": TOOL_VERSION,
            "registration": reg, "thickness": st.get("thickness", 1.0), "line_z": round(float(line_z), 5),
            "smooth_iters": st.get("smooth_iters", 12), "segment": st.get("segment", {}),
            "skull_ratio": {"p5": round(float(np.percentile(K, 5)), 4), "p95": round(float(np.percentile(K, 95)), 4),
                            "known": round(dbg["skull_known"], 3)},
            "triangles": int(len(Fd))}
    path = HM.source_path(name, bb.id, out)
    HM.save_source(path, Vp, Fd, N, part, rtt, meta)
    log(f"{name}: wrote {path} ({os.path.getsize(path) / 1024:.0f} KB, {time.time() - t0:.0f}s in all)")
    if d:
        body = body_debug_objects(fr)
        pal = np.array([[0.85, 0.85, 0.85], [0.95, 0.45, 0.3], [0.35, 0.75, 0.4], [0.35, 0.5, 0.95], [0.85, 0.7, 0.2],
                        [0.2, 0.2, 0.2]])
        o = debug_mesh("dbg_parts", Vp, Fd, pal[part])
        debug_shots(os.path.join(d, f"{name}_5parts"), fr, [o] + body, drop=0.12 if st["length"] == "long" else 0.06,
                    dist=1.3 if st["length"] == "long" else 0.9)
        o2 = debug_mesh("dbg_final", Vp, Fd, np.tile([0.9, 0.9, 0.92], (len(Vp), 1)))
        o2.data.normals_split_custom_set_from_vertices(N.tolist())
        debug_shots(os.path.join(d, f"{name}_6normals"), fr, [o2] + body, drop=0.12 if st["length"] == "long" else 0.06,
                    dist=1.3 if st["length"] == "long" else 0.9)
        o3 = debug_mesh("dbg_raw", Vp, Fd, np.tile([0.9, 0.9, 0.92], (len(Vp), 1)))
        debug_shots(os.path.join(d, f"{name}_7geo"), fr, [o3] + body, drop=0.12 if st["length"] == "long" else 0.06,
                    dist=1.3 if st["length"] == "long" else 0.9)
        remove_debug(o, o2, o3, *body)
    return meta


def main() -> None:
    a = args()
    bb = HF.load_body(os.path.abspath(a.blend))
    fr = HF.HeadFrame(bb)
    only = set(filter(None, a.styles.split(",")))
    for name in HM.STYLES:
        st = HM.style(name, a.guide if a.guide in HM.GUIDES[name] else None)
        if st["body"] != bb.id or (only and name not in only):
            continue
        prepare(a, bb, fr, name, st)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
