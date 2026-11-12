"""Register the Meshy hair guides to a body's head and measure them.

    blender -b --factory-startup --python tools/blender/characters/measure_hair_guides.py -- \\
        --blend art/characters/body_a.blend --guides m1_b,m2_b,m3_a \\
        --meshy vendor_assets/meshy --out .out/guides [--render]

The guides (`vendor_assets/meshy/g_<id>.glb`, gitignored, never committed)
are untextured busts with the hair fused onto the head, at an arbitrary
scale. This tool reads their volume, hem and lengths (the lock builder's
numbers in charlib/hair_styles.py were set against them), and its fit is the
registration prepare_hair_meshy.py uses to make the hair source meshes from
the guides (docs/art/character-look.md section 2; recorded per style in
charlib/hair_meshy.py `STYLES`).

Registration: a uniform scale and a forward/up offset (the bust is
symmetric and faces -Y) that put the guide's face front and neck front on
the body's (robust point-to-surface distance) while the body's scalp stays
inside the guide's hair; a coarse grid, then a simplex refine.

Writes `<out>/<id>_<body>.json`: the transform, the hair volume over the
skull per (phi, theta) in head centimetres, the lowest hair point per
azimuth (metres below the eyes) and the fringe hem, and with `--render`
`<out>/<id>_<body>.png` (the registered guide in grey over the body).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

from charlib import hair_frame as HF  # noqa: E402

PHIS = np.arange(0.0, 135.0, 5.0)
THETAS = np.arange(0.0, 360.0, 15.0)


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--guides", required=True)
    p.add_argument("--meshy", default="vendor_assets/meshy")
    p.add_argument("--out", required=True)
    p.add_argument("--render", action="store_true")
    return p.parse_args(argv)


def import_guide(path: str):
    """The guide as numpy arrays (glTF import: data only, no scripts)."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    objs = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    V, T, off = [], [], 0
    for o in objs:
        co, _, t = HF.world_mesh(o)
        V.append(co)
        T.append(t + off)
        off += len(co)
    return objs, np.vstack(V), np.vstack(T)


def sample_points(fr: HF.HeadFrame) -> dict[str, np.ndarray]:
    co, nr, _ = fr.parts["head"]
    face = (co[:, 2] > fr.chin_z + 0.01) & (co[:, 2] < fr.brow_z - 0.004) & (nr[:, 1] < -0.5) & (np.abs(co[:, 0]) < 4.5 * fr.hcm)
    nco, nnr, _ = fr.parts["neck"]
    neck = (nnr[:, 1] < -0.3) & (nco[:, 2] > fr.neck_z + 0.02)
    sco = fr.parts["scalp"][0]
    rng = np.random.default_rng(7)

    def pick(a, n):
        return a[rng.choice(len(a), size=min(n, len(a)), replace=False)] if len(a) > n else a

    return {"face": pick(co[face], 160), "neck": pick(nco[neck], 60), "scalp": pick(sco, 80)}


def objective(tree: BVHTree, pts: dict[str, np.ndarray], s: float, ty: float, tz: float) -> float:
    t = np.array([0.0, ty, tz])
    err = 0.0
    for key, w in (("face", 1.0), ("neck", 0.5)):
        acc = 0.0
        for p in pts[key]:
            q = (p - t) / s
            loc, nrm, _, dist = tree.find_nearest(Vector(tuple(q)))
            if loc is None:
                acc += 0.012 ** 2
                continue
            h = float((q - np.array(loc)) @ np.array(nrm)) * s
            # Outside: robust; inside (the guide's face engulfing ours): not.
            d = min(abs(h), 0.012) if h > 0 else 2.0 * min(-h, 0.04)
            acc += d * d
        err += w * acc / max(len(pts[key]), 1)
    acc = 0.0
    for p in pts["scalp"]:
        q = (p - t) / s
        loc, nrm, _, dist = tree.find_nearest(Vector(tuple(q)))
        if loc is None:
            continue
        out = float((q - np.array(loc)) @ np.array(nrm)) * s
        if out > 0:
            acc += out * out
    err += 8.0 * acc / max(len(pts["scalp"]), 1)
    return err


def nelder_mead(f, x0, step, iters=120):
    n = len(x0)
    xs = [np.array(x0, dtype=np.float64)]
    for i in range(n):
        x = np.array(x0, dtype=np.float64)
        x[i] += step[i]
        xs.append(x)
    fs = [f(x) for x in xs]
    for _ in range(iters):
        order = np.argsort(fs)
        xs = [xs[i] for i in order]
        fs = [fs[i] for i in order]
        c = np.mean(xs[:-1], axis=0)
        xr = c + (c - xs[-1])
        fr_ = f(xr)
        if fr_ < fs[0]:
            xe = c + 2 * (c - xs[-1])
            fe = f(xe)
            xs[-1], fs[-1] = (xe, fe) if fe < fr_ else (xr, fr_)
        elif fr_ < fs[-2]:
            xs[-1], fs[-1] = xr, fr_
        else:
            xc = c + 0.5 * (xs[-1] - c)
            fc = f(xc)
            if fc < fs[-1]:
                xs[-1], fs[-1] = xc, fc
            else:
                xs = [xs[0] + 0.5 * (x - xs[0]) for x in xs]
                fs = [f(x) for x in xs]
    i = int(np.argmin(fs))
    return xs[i], fs[i]


def register(fr: HF.HeadFrame, V: np.ndarray, T: np.ndarray, scales=(0.14, 0.42)) -> tuple[float, float, float, float]:
    tree = BVHTree.FromPolygons(V.tolist(), T.tolist(), all_triangles=True)
    pts = sample_points(fr)
    sub = {k: v[:: max(1, len(v) // 40)] for k, v in pts.items()}
    top_g = V[:, 2].max()
    crown = V[V[:, 2] > top_g - 0.25 * (top_g - V[:, 2].min())]
    best = (np.inf, None)
    for s in np.arange(scales[0], scales[1], 0.02):
        tz0 = fr.top_z + 0.025 - top_g * s
        ty0 = float(fr.parts["scalp"][0][:, 1].mean()) - s * float(crown[:, 1].mean())
        for dz in np.arange(-0.08, 0.081, 0.01):
            for dy in np.arange(-0.06, 0.061, 0.01):
                e = objective(tree, sub, s, ty0 + dy, tz0 + dz)
                if e < best[0]:
                    best = (e, (s, ty0 + dy, tz0 + dz))
    x, e = nelder_mead(lambda x: objective(tree, pts, *x), best[1], (0.01, 0.005, 0.005))
    return float(x[0]), float(x[1]), float(x[2]), float(e)


def measure(fr: HF.HeadFrame, Vr: np.ndarray, T: np.ndarray) -> dict:
    tree = BVHTree.FromPolygons(Vr.tolist(), T.tolist(), all_triangles=True)
    c = Vector(tuple(fr.centre))
    vol = []
    for phi in PHIS:
        row = []
        for th in THETAS:
            d = fr.dirs(phi, th)
            v = Vector(tuple(d))
            hit = tree.ray_cast(c + v * 0.5, -v, 0.5)
            if hit[0] is None:
                row.append(None)
                continue
            r = 0.5 - hit[3]
            row.append(round((r - float(fr.skull_r(phi, th))) / fr.hcm, 2))
        vol.append(row)
    # Hair vertices: outside the body by more than 5 mm, above the shoulders' line.
    sub = Vr[:: max(1, len(Vr) // 60000)]
    hair = []
    for p in sub:
        if fr.depth(p, reach=0.05) > 0.005:
            hair.append(p)
    hair = np.array(hair)
    phi, theta, r = fr.polar(hair)
    low = {}
    for th in THETAS:
        m = np.abs(((theta - th + 180.0) % 360.0) - 180.0) < 7.5
        m &= np.hypot(hair[:, 0] - fr.centre[0], hair[:, 1] - fr.centre[1]) < 0.16
        if m.any():
            low[int(th)] = round(float(fr.eye_z - hair[m, 2].min()), 3)
    # Fringe hem: lowest hair point in front of the face per x (head cm).
    front = hair[(hair[:, 1] < fr.centre[1] - 6.0 * fr.hcm) & (hair[:, 2] > fr.chin_z)]
    hem = []
    for x in np.arange(-6.0, 6.01, 1.0):
        m = np.abs(front[:, 0] / fr.hcm - x) < 0.5
        if m.any():
            hem.append([float(x), round(float((front[m, 2].min() - fr.eye_z) / fr.hcm), 2)])
    return {"volume_hcm": {"phi": PHIS.tolist(), "theta": THETAS.tolist(), "rows": vol},
            "lowest_below_eyes_m": low, "fringe_hem_hcm": hem, "hair_points": int(len(hair))}


def render(fr: HF.HeadFrame, guide_objs, out_png: str) -> None:
    from charlib import render as RD

    gm = bpy.data.materials.new("guide")
    gm.diffuse_color = (0.8, 0.8, 0.82, 1)
    gm.use_nodes = True
    gm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.75, 0.75, 0.78, 1)
    for o in guide_objs:
        o.data.materials.clear()
        o.data.materials.append(gm)
    sk = bpy.data.materials.new("skin")
    sk.use_nodes = True
    sk.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.9, 0.45, 0.4, 1)
    for o in bpy.data.objects:
        if o.type == "MESH" and o not in guide_objs:
            show = o.get("region") in ("head", "ears", "neck", "torso") and o.get("lod") == 0
            o.hide_render = not show
            if show:
                o.data.materials.clear()
                o.data.materials.append(sk)
    RD.studio((420, 520))
    paths = []
    tgt = (0.0, float(fr.centre[1]), float(fr.centre[2]) - 0.03)
    for az in (0, 45, 90, 180):
        RD.aim(tgt, az, 4, 1.25, lens=85)
        paths.append(RD.shoot(out_png.replace(".png", f"_{az}.png")))
    RD.sheet(paths, out_png, 4)


def main() -> None:
    a = args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    for gid in a.guides.split(","):
        bb = HF.load_body(os.path.abspath(a.blend))
        fr = HF.HeadFrame(bb)
        objs, V, T = import_guide(os.path.join(os.path.abspath(a.meshy), f"g_{gid}.glb"))
        s, ty, tz, err = register(fr, V, T)
        Vr = V * s + np.array([0.0, ty, tz])
        res = {"guide": gid, "body": bb.id, "scale": round(s, 5), "offset": [0.0, round(ty, 5), round(tz, 5)],
               "fit_rms_mm": round(float(np.sqrt(err)) * 1000, 2), "frame": fr.summary()}
        res.update(measure(fr, Vr, T))
        with open(os.path.join(out, f"{gid}_{bb.id}.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
        print(f"[guides] {gid} on {bb.id}: scale {s:.4f} offset ({ty:.4f}, {tz:.4f}) rms {res['fit_rms_mm']} mm", flush=True)
        if a.render:
            for o in objs:
                mw = o.matrix_world.copy()
                from mathutils import Matrix

                o.matrix_world = Matrix.Translation((0.0, ty, tz)) @ Matrix.Scale(s, 4) @ mw
            render(fr, objs, os.path.join(out, f"{gid}_{bb.id}.png"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
