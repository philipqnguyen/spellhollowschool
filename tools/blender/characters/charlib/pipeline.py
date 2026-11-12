"""Build steps shared by the build, render and check scripts."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import bpy

from . import anim as AN
from . import atlas as A
from . import body as BD
from . import body_mesh as BM
from . import lod as LOD
from . import meshutil as MU
from . import proportions as P
from . import rig as RG
from .meshutil import MeshData

LOD1_RATIO = 0.35
BODY_SOURCES = ("current", "makehuman")
LODS = (0, 1, 2, 3)
# Triangle budgets per tier and part (docs/art/character-adoption.md section 6):
# LOD0 near (smooth face, hands and hair), LOD1, LOD2, LOD3 (budget only).
# Hair: the Meshy-derived styles' long class (docs/art/character-look.md section 2:
# pass 2's inner layer on LOD0 and LOD1; charlib/hair_meshy.py BUDGETS
# per length class).
LOD_BUDGETS = {
    "body": (16000, 5000, 1500, 500),
    "hair": (24000, 8000, 1300, 450),
    "body_garment": (8000, 2700, 800, 250),
    "legs": (4000, 1300, 400, 120),
    "shoes": (1500, 500, 150, 50),
    "hat": (2500, 800, 250, 80),
}
DRESSED_MAX = (52000, 17500, 4000, 1350)  # character-look.md section 2: the tiers rise by the hair's increase
SLOT_PART = {"body": "body_garment", "legs": "legs", "shoes": "shoes", "hat": "hat"}
# Which shape keys bodies carry: face and expression keys only. The build
# keys (slim / muscular / heavy) are gone (character adoption: every body is
# the neutral build, height is a uniform scale).
NO_BUILDS = True


def log(msg: str) -> None:
    print(f"[characters] {msg}", flush=True)


@dataclass
class BodyBuild:
    body: P.Body
    layout: BD.BodyLayout
    md: MeshData  # LOD0, all shape keys
    rig: bpy.types.Object
    skel: bpy.types.Object
    material: bpy.types.Material
    meshes: dict[int, dict[str, bpy.types.Object]] = field(default_factory=dict)  # lod -> region -> object
    collection: bpy.types.Collection | None = None
    source: str = "current"  # "makehuman": md is the fitting cage, meshes are MakeHuman's topology
    report: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.body.id

    def all_meshes(self) -> list[bpy.types.Object]:
        return [o for lod in sorted(self.meshes) for o in self.meshes[lod].values()]


def new_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    RG.enable_rigify()
    bpy.context.scene.render.fps = AN.FPS


def collection(name: str) -> bpy.types.Collection:
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
    return c


def move_to(obj: bpy.types.Object, coll: bpy.types.Collection) -> None:
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def make_lods(name: str, md: MeshData, material, coll, regions: list[str] | None = None,
              ratio: float = LOD1_RATIO, budget: int | None = None,
              extra_protect=None, keep_weights=None, budgets: tuple | None = None) -> dict[int, dict[str, bpy.types.Object]]:
    """LOD0 to LOD3 objects, split by face region when `regions` is given
    (otherwise one object per LOD keyed by `name`). An item over its LOD0
    triangle `budget` (docs/art/base-body-spec.md section 6) is decimated to
    it first; LOD1 is about `ratio` of LOD0 (capped by `budgets[1]`), LOD2
    and LOD3 are plain collapses of LOD0 to `budgets[2]` / `budgets[3]`
    (docs/art/character-adoption.md section 6; no shape keys: expressions
    and face sliders do not read beyond 20 m). `extra_protect` adds vertices
    the decimation keeps (besides region borders, open edges and eyes);
    `keep_weights` (0..1 per vertex) steers where the LOD0 decimation spends
    its triangles."""
    if _drop_bad_faces(md):
        md.compact()
    full = MU.to_object(name + "_full", md, A, material, collection=coll)
    names = regions or sorted(set(md.region))
    LOD.add_region_attribute(full, md.region, names)
    protect = LOD.border_vertices(md)
    if extra_protect is not None and len(extra_protect):
        import numpy as np

        protect = np.union1d(protect, np.asarray(extra_protect, dtype=np.int64))
    tris0 = md.tri_count()
    if budget and tris0 > budget:
        reduced = LOD.decimate_object(full, budget / tris0 * 0.97, protect=protect, keep_weights=keep_weights)
        LOD.rebind_keys(reduced, md)
        bpy.data.objects.remove(full)
        full = reduced
        full.name = full.data.name = name + "_full"
        log(f"{name}: {tris0} tris over the {budget} budget; decimated to {LOD.triangle_count(full)}")
    t_full = LOD.triangle_count(full)
    if not budgets:
        lod1 = LOD.decimate_object(full, ratio, protect=protect if not budget or tris0 <= budget else None)
        LOD.rebind_keys(lod1, md)
        levels = [(0, full), (1, lod1)]
    else:
        # Each tier under its budget and under 55-60% of the tier above.
        target1 = min(budgets[1], int(t_full * 0.55))
        lod1 = reduce_to(full, target1, protect=protect if not budget or tris0 <= budget else None, first_ratio=min(ratio, target1 / max(t_full, 1)))
        LOD.rebind_keys(lod1, md)
        levels = [(0, full), (1, lod1)]
        prev = LOD.triangle_count(lod1)
        for level in (2, 3):
            target = min(budgets[level], int(prev * 0.6))
            low = reduce_to(full, target)
            levels.append((level, low))
            prev = LOD.triangle_count(low)
    out: dict[int, dict[str, bpy.types.Object]] = {}
    for level, obj in levels:
        face_regions = LOD.region_ids_to_names(obj, names)
        if regions:
            parts = MU.face_region_split(obj, face_regions)
            for region, part in parts.items():
                part.name = part.data.name = f"{name}_{region}_lod{level}"
                part["region"] = region
                part["lod"] = level
            bpy.data.objects.remove(obj)
            out[level] = parts
        else:
            obj.name = obj.data.name = f"{name}_lod{level}"
            obj["lod"] = level
            out[level] = {name: obj}
    for parts in out.values():
        for o in parts.values():
            attr = o.data.attributes.get("region_id")
            if attr is not None:
                o.data.attributes.remove(attr)
            drop_noop_keys(o)
    return out


def _drop_bad_faces(md: MeshData) -> int:
    """Faces Blender's mesh validation would remove (a repeated vertex, or the
    same vertex set twice), dropped beforehand so the per-face attributes
    stay aligned with the mesh. Returns how many were dropped."""
    keep, seen = [], set()
    for fi, f in enumerate(md.faces):
        key = tuple(sorted(f))
        if len(set(f)) != len(f) or key in seen:
            continue
        seen.add(key)
        keep.append(fi)
    dropped = len(md.faces) - len(keep)
    if dropped:
        md.faces = [md.faces[i] for i in keep]
        md.region = [md.region[i] for i in keep]
        md.swatch = [md.swatch[i] for i in keep]
        md.mask = [md.mask[i] for i in keep]
        remap = {old: new for new, old in enumerate(keep)}
        md.corner_uv = {remap[k]: v for k, v in md.corner_uv.items() if k in remap}
        if md.tags:
            md.tags = [md.tags[i] for i in keep]
    return dropped


def reduce_to(src: bpy.types.Object, target: int, protect=None, first_ratio: float | None = None) -> bpy.types.Object:
    """A collapse of `src` to at most `target` triangles: the ratio shrinks
    until it fits (protected borders only on the first try); if the collapse
    stalls (many small loose parts: buttons, crests, feathers), the smallest
    loose parts are dropped, as they do not read at that distance anyway."""
    t = max(LOD.triangle_count(src), 1)
    r = min(1.0, first_ratio if first_ratio else target / t * 0.97)
    obj = None
    split = None
    for attempt in range(7):
        if obj is not None:
            bpy.data.objects.remove(obj)
        if attempt == 0:
            obj = LOD.decimate_object(src, r, protect=protect)
        else:
            # A collapse stops at non-manifold edges (linings joined to the
            # outer cloth by their rims, trims grown from loops): split them.
            if split is None:
                split = LOD.split_nonmanifold(src)
            obj = LOD.decimate_object(split, r)
        if LOD.triangle_count(obj) <= target:
            break
        r *= 0.6
    else:
        # Still stalled (rigid hats' ring bands): cluster the vertices on a
        # coarse grid first, then collapse; else drop the smallest parts.
        clustered = LOD.cluster(split, 0.015)
        best = None
        for rr in (0.5, 0.3, 0.15, 0.07):
            cand = LOD.decimate_object(clustered, rr)
            n = LOD.triangle_count(cand)
            if 0 < n <= target:
                best = cand
                break
            bpy.data.objects.remove(cand)
        bpy.data.objects.remove(clustered)
        if best is not None:
            bpy.data.objects.remove(obj)
            obj = best
        else:
            LOD.cull_small_parts(obj, target)
    if split is not None:
        bpy.data.objects.remove(split)
    return obj


def drop_noop_keys(obj: bpy.types.Object, eps: float = 2e-5) -> None:
    """Remove shape keys that do not move this object (e.g. face keys on the
    calves); the client applies weights by target name."""
    keys = obj.data.shape_keys
    if keys is None:
        return
    import numpy as np

    n = len(obj.data.vertices)
    base = np.zeros(n * 3, dtype=np.float32)
    keys.key_blocks[0].data.foreach_get("co", base)
    for kb in list(keys.key_blocks[1:]):
        co = np.zeros(n * 3, dtype=np.float32)
        kb.data.foreach_get("co", co)
        if np.abs(co - base).max() < eps:
            obj.shape_key_remove(kb)
    if len(obj.data.shape_keys.key_blocks) == 1:
        obj.shape_key_clear()


def build_body(body_id: str, bake: bool = True, source: str = "current", shape=None) -> BodyBuild:
    """The base body, its rig and animations. `source` "current" is our own
    sculpted body; "makehuman" is the MakeHuman (MPFB2) body fitted to the same
    skeleton (`mh_body`): its own topology is exported, and `md` (what gear and
    hair are built against) is our layout resampled onto it.

    `shape` (MakeHuman only): an object that reshapes the visible mesh just
    before the painted face is cut in (`shape_target.Reshaper`: a baked body
    shape wrapped onto a third-party sculpt, the face then painted on the
    sculpt's features; or `shape_target.Capture`, which records the mesh and
    stops). Topology, weights, UVs, paint and key names stay; the body's
    proportions (and so the skeleton, the cage gear and hair are cut against)
    come from the shape."""
    if source not in BODY_SOURCES:
        raise ValueError(f"unknown body source {source!r} (expected one of {BODY_SOURCES})")
    t0 = time.time()
    body = P.get(body_id)
    lay = BD.BodyLayout(body)
    img = A.build_image()
    mat = A.material("character", img)
    coll = collection(f"{body_id}_body")
    report: dict = {}
    if source == "makehuman":
        from . import mh_body as MB

        keys = [k for k in BM.SHAPE_KEYS if not (NO_BUILDS and k in P.BUILDS)]
        mh = MB.build(lay, keys, shape=shape)
        md = mh.cage
        report = mh.report
        log(f"{body_id}: MakeHuman mesh {mh.visible.tri_count()} tris, {len(mh.visible.keys)} shape keys "
            f"({time.time() - t0:.1f}s)")
        if shape is not None:
            body, lay, md = shape.finish(mh)
            report = dict(report, shape=getattr(shape, "report", {}))
        meshes = make_lods(body_id, mh.visible, mat, coll, regions=list(BD.REGIONS), budget=MB.LOD0_BUDGET,
                           extra_protect=mh.protect, keep_weights=mh.keep_weights, budgets=LOD_BUDGETS["body"])
        MB.finish_lods(meshes, mh)
    else:
        md = BM.body_meshdata(lay, with_keys=True)
        if NO_BUILDS:
            md.keys = {k: v for k, v in md.keys.items() if k not in P.BUILDS}
        log(f"{body_id}: mesh {md.tri_count()} tris, {len(md.keys)} shape keys ({time.time() - t0:.1f}s)")
        meshes = make_lods(body_id, md, mat, coll, regions=list(BD.REGIONS), budgets=LOD_BUDGETS["body"])
    for parts in meshes.values():
        for o in parts.values():
            o["asset"] = f"characters/{body_id}"
    mr = RG.build_metarig(body, f"metarig_{body_id}")
    rig = RG.generate(mr, f"rig_{body_id}")
    skel = RG.build_skeleton(rig, body, f"{body_id}_skeleton")
    skel["body_params"] = P.to_json(body)  # the proportions this skeleton was built from (checks read them back)
    skel["body_source"] = source  # refit_garment_shapes.py fits a saved body as the build did
    if report.get("shape"):
        skel["body_shape"] = str(report["shape"].get("target") or "sculpt")
    move_to(skel, coll)
    bb = BodyBuild(body, lay, md, rig, skel, mat, meshes, coll, source=source, report=report)
    if bake:
        RG.constrain_to_rig(skel, rig)
        AN.bake(rig, skel, body)
        RG.clear_constraints(skel)
        log(f"{body_id}: baked {len(AN.CLIPS)} clips ({time.time() - t0:.1f}s)")
    for o in bb.all_meshes():
        RG.bind(o, skel)
    return bb


def save_blend(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.context.preferences.filepaths.save_version = 0  # no .blend1 backups in the art repo
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
