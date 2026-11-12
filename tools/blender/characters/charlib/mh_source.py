"""MakeHuman base meshes through MPFB2 (Blender side), as plain numpy data.

Only MakeHuman's CC0 assets are used: the hm08 base mesh, its macro and
detail targets and the Rigify rig/weight definitions (see
art/CREDITS-makehuman.md). MPFB itself (GPL) runs as a tool and is never
copied into the repositories; the extension must be installed in the Blender
user resources the build runs with (BLENDER_USER_RESOURCES).

`human(macro)` builds one MakeHuman human with the given macro sliders and
returns every vertex (body and helpers) in Blender metres, -Y forward, feet
on the floor. `target_delta(name)` returns a detail target's per-vertex offset
in the same units. Results are cached as .npz next to the MPFB settings
(gitignored), keyed by the parameters and the MPFB version.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys

import numpy as np

MODULE = "bl_ext.user_default.mpfb"
SCALE = 0.1  # MakeHuman decimetres -> metres
BODY_VERTS = 13380  # hm08: vertices 0..13379 are the skin, the rest helpers and joint cubes


def _mpfb_dir() -> str:
    mod = sys.modules.get(MODULE)
    if mod is None:
        enable()
        mod = sys.modules[MODULE]
    return os.path.dirname(mod.__file__)


def enable() -> None:
    import addon_utils

    if sys.modules.get(MODULE) is None or not addon_utils.check(MODULE)[1]:
        addon_utils.enable(MODULE, default_set=True, persistent=False, handle_error=None)
    if sys.modules.get(MODULE) is None:
        raise RuntimeError(f"{MODULE} is not installed in this Blender's user resources "
                           "(run with BLENDER_USER_RESOURCES=<worktree>/.blender-user; "
                           "install it with tools/blender/install_mpfb.sh)")
    # Only the pinned MPFB (tools/blender/MPFB_VERSION): another release's base
    # mesh, targets or rig would change every body. The add-on lives in
    # <user resources>/extensions/user_default/mpfb.
    resources = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(sys.modules[MODULE].__file__))))
    problem = _pins().mpfb_problem(resources)
    if problem:
        raise RuntimeError(problem)


def _pins():
    """tools/blender/pins.py (the Blender and MPFB pins)."""
    tools = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if tools not in sys.path:
        sys.path.append(tools)
    import pins

    return pins


def data_path(*parts: str) -> str:
    return os.path.join(_mpfb_dir(), "data", *parts)


def version() -> str:
    try:
        with open(os.path.join(_mpfb_dir(), "blender_manifest.toml"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("version"):
                    return line.split("=")[1].strip().strip('"')
    except OSError:
        pass
    return "unknown"


def cache_dir() -> str:
    root = os.environ.get("BLENDER_USER_RESOURCES") or os.path.join(os.path.dirname(_mpfb_dir()), "..", "..")
    d = os.path.join(root, "mh_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _key(kind: str, payload) -> str:
    raw = json.dumps([kind, payload, version()], sort_keys=True).encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def default_macro(**over) -> dict:
    m = {"gender": 0.5, "age": 0.5, "muscle": 0.5, "weight": 0.5, "proportions": 0.5, "height": 0.5,
         "cupsize": 0.5, "firmness": 0.5, "race": {"asian": 1 / 3, "caucasian": 1 / 3, "african": 1 / 3}}
    for k, v in over.items():
        if k == "race":
            m["race"] = dict(v)
        else:
            m[k] = v
    return m


# ----------------------------------------------------------------------------
# Mesh topology (fixed for hm08)
# ----------------------------------------------------------------------------


def faces() -> list[tuple[int, ...]]:
    """hm08 polygons (quads), read from the bundled OBJ."""
    path = os.path.join(cache_dir(), f"faces_{_key('faces', 1)}.npz")
    if os.path.exists(path):
        z = np.load(path)
        return [tuple(int(i) for i in f[: n]) for f, n in zip(z["f"], z["n"])]
    polys = []
    with open(data_path("3dobjs", "base.obj"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("f "):
                polys.append(tuple(int(t.split("/")[0]) - 1 for t in line.split()[1:]))
    n = np.array([len(p) for p in polys])
    arr = np.full((len(polys), int(n.max())), -1, dtype=np.int64)
    for i, p in enumerate(polys):
        arr[i, : len(p)] = p
    np.savez_compressed(path, f=arr, n=n)
    return polys


def face_uvs() -> tuple[list[tuple[int, ...]], list[np.ndarray]]:
    """hm08 polygons with their MakeHuman UV corners (the layout MakeHuman's
    CC0 skin masks are painted in)."""
    path = os.path.join(cache_dir(), f"faceuv_{_key('faceuv', 1)}.npz")
    if os.path.exists(path):
        z = np.load(path)
        polys = [tuple(int(i) for i in f[:n]) for f, n in zip(z["f"], z["n"])]
        uvs = [z["uv"][o:o + n] for o, n in zip(z["o"], z["n"])]
        return polys, uvs
    vt, polys, corner = [], [], []
    with open(data_path("3dobjs", "base.obj"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("vt "):
                vt.append([float(x) for x in line.split()[1:3]])
            elif line.startswith("f "):
                toks = [t.split("/") for t in line.split()[1:]]
                polys.append(tuple(int(t[0]) - 1 for t in toks))
                corner.append([int(t[1]) - 1 for t in toks])
    vt = np.array(vt)
    uvs = [vt[c] for c in corner]
    n = np.array([len(p) for p in polys])
    arr = np.full((len(polys), int(n.max())), -1, dtype=np.int64)
    for i, p in enumerate(polys):
        arr[i, : len(p)] = p
    o = np.concatenate([[0], np.cumsum(n)[:-1]])
    np.savez_compressed(path, f=arr, n=n, o=o, uv=np.vstack(uvs))
    return polys, uvs


def mask_image(name: str, size: int = 512) -> np.ndarray:
    """A MakeHuman CC0 skin mask (data/textures/mpfb_<name>.jpg) as a
    (size, size) float array, row 0 at v = 0."""
    import bpy

    path = os.path.join(cache_dir(), f"mask_{name}_{size}_{_key('mask', name)}.npy")
    if os.path.exists(path):
        return np.load(path)
    img = bpy.data.images.load(data_path("textures", f"mpfb_{name}.jpg"), check_existing=False)
    img.scale(size, size)
    px = np.empty(size * size * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    out = px.reshape(size, size, 4)[:, :, :3].mean(axis=2)
    np.save(path, out)
    return out


def vertex_groups() -> dict[str, np.ndarray]:
    with open(data_path("mesh_metadata", "basemesh_vertex_groups.json"), encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for name, ranges in raw.items():
        ids = [np.arange(a, b + 1) for a, b in ranges]
        out[name] = np.concatenate(ids) if ids else np.zeros(0, dtype=np.int64)
    return out


def rig_definition(name: str = "human") -> dict:
    with open(data_path("rigs", "rigify", f"rig.{name}.json"), encoding="utf-8") as f:
        return json.load(f)["bones"]


def rig_weights(name: str = "human_toes") -> dict[str, list]:
    with open(data_path("rigs", "rigify", f"weights.{name}.json"), encoding="utf-8") as f:
        return json.load(f)["weights"]


# ----------------------------------------------------------------------------
# Humans and targets
# ----------------------------------------------------------------------------


def _create(macro: dict):
    import bpy

    enable()
    from bl_ext.user_default.mpfb.services.humanservice import HumanService  # type: ignore

    human = HumanService.create_human(mask_helpers=False, detailed_helpers=True, extra_vertex_groups=False,
                                      feet_on_ground=False, scale=SCALE, macro_detail_dict=macro)
    dg = bpy.context.evaluated_depsgraph_get()
    ev = human.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.zeros(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    co = co.reshape(-1, 3)
    mw = np.array(human.matrix_world)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    return human, co


def _remove(obj) -> None:
    import bpy

    me = obj.data
    bpy.data.objects.remove(obj)
    if me.users == 0:
        bpy.data.meshes.remove(me)


def human(macro: dict) -> np.ndarray:
    """(19158, 3) vertex positions of a MakeHuman human (feet on the floor)."""
    path = os.path.join(cache_dir(), f"human_{_key('human', macro)}.npy")
    if os.path.exists(path):
        return np.load(path)
    obj, co = _create(macro)
    _remove(obj)
    co[:, 2] -= co[:BODY_VERTS, 2].min()
    np.save(path, co)
    return co


def _read_target(path: str, n: int) -> np.ndarray:
    """MakeHuman .target(.gz): 'index dx dy dz' in MakeHuman axes (Y up, Z
    forward, decimetres) -> Blender metres (X, -Z, Y)."""
    opener = gzip.open if path.endswith(".gz") else open
    d = np.zeros((n, 3))
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) != 4 or parts[0].startswith("#"):
                continue
            i = int(parts[0])
            x, y, z = (float(t) for t in parts[1:])
            d[i] = (x, -z, y)
    return d * SCALE


def target_path(rel: str) -> str:
    """`rel` like 'nose/nose-volume-incr' or 'expression/units/caucasian/mouth-open'."""
    p = data_path("targets", *rel.split("/"))
    return p if p.endswith(".target.gz") else p + ".target.gz"


def target_delta(rel: str, n: int = 19158) -> np.ndarray:
    return _read_target(target_path(rel), n)
