"""Content data (catalog, creator) and the per-asset export steps."""

from __future__ import annotations

import json
import os

import bpy

from . import export as EX
from . import gltf_inspect
from . import head as H
from . import pipeline as PL
from .proportions import BODIES

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def load_json(rel: str) -> dict:
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return json.load(f)


def catalog() -> dict:
    return load_json("data/items/catalog.json")


def creator() -> dict:
    return load_json("data/characters/creator.json")


def bodies():
    return list(BODIES.values())


def clothing_items() -> list[dict]:
    return [i for i in catalog()["items"] if i["kind"] == "clothing" and not i.get("retired")]


def hair_styles(body_id: str | None = None) -> list[dict]:
    """The shipped hair styles (creator.json v2: retired ids have no asset),
    optionally only those of one body (each style belongs to one body)."""
    styles = [h for h in creator()["hair_styles"] if not h.get("retired")]
    if body_id is not None:
        styles = [h for h in styles if h.get("body", body_id) == body_id]
    return styles


def shape_key_groups() -> dict:
    return {
        "face": list(H.FACE_KEYS),
        "expressions": list(H.EXPRESSION_KEYS),
        "note": "face and expression keys only on the body head (LOD0 and LOD1); no build keys: every body is the neutral build and height is a uniform root scale",
    }


def asset_file(asset: str, body_id: str | None = None, cap: bool = False) -> str:
    """Path of an asset file relative to the assets root (docs/architecture.md)."""
    if body_id is None:
        return f"{asset}.glb"
    return f"{asset}{'_cap' if cap else ''}_{body_id}.glb"


def export_body(bb: PL.BodyBuild, assets: str, manifest: dict) -> None:
    from . import shade_bake as SB

    asset = f"characters/{bb.id}"
    rel = asset_file(asset)
    from . import face_paint as FP

    shade = SB.bake_body(bb.meshes, bb.skel)
    EX.export_glb(os.path.join(assets, rel), bb.skel, bb.all_meshes(), animations=True)
    lod0 = list(bb.meshes[0].values())
    # The face texture pair (docs/art/character-look.md section 3.1), when the head was built for it.
    face = None
    if bb.id in FP.FACE_BUILDS and "head" in bb.meshes.get(0, {}):
        face = FP.export(bb.id, bb.meshes[0]["head"], assets)
        PL.log(f"{bb.id}: face textures {face['colour']}, {face['mask']}")
    manifest["assets"][asset] = {
        "kind": "body",
        "body": bb.id,
        "file": rel,
        "bounds": EX.bounds(lod0),
        "triangles": EX.lod_tris(bb.meshes),
        "region_triangles": {r: EX.tris([o]) for r, o in sorted(bb.meshes[0].items())},
        "shade": {"attribute": SB.ATTR, "bytes_lod0": SB.shade_bytes(lod0), "bytes": SB.shade_bytes(bb.all_meshes()),
                  **{k: round(v, 3) for k, v in shade.items()}},
        "source": bb.source,
        **({"shape": bb.report["shape"]} if bb.report.get("shape") else {}),
        **({"face": face} if face else {}),
        "animations": sorted(t.name for t in bb.skel.animation_data.nla_tracks) if bb.skel.animation_data else [],
    }
    # The glTF joint order (shared by every file built from this skeleton).
    manifest.setdefault("skeleton", {})["bones"] = gltf_inspect.joint_names(os.path.join(assets, rel))
    PL.log(f"exported {rel}")


def _item_entry(manifest: dict, asset: str, base: dict) -> dict:
    entry = manifest["assets"].get(asset)
    if entry is None or entry.get("kind") != base.get("kind"):
        entry = dict(base)
        entry["files"] = {}
        entry["bounds"] = {}
        entry["triangles"] = {}
        manifest["assets"][asset] = entry
    entry.update(base)
    return entry


def export_item(bb: PL.BodyBuild, asset: str, meshes: dict[int, dict[str, bpy.types.Object]], assets: str, manifest: dict,
                base: dict, cap: bool = False, fitted: bool = False) -> None:
    """`fitted`: the item was fitted to this very body (garment shapes keep
    their own cage and layer offsets), so the MakeHuman skin push is skipped
    (except on a body reshaped onto a sculpt, shape_target.py)."""
    rel = asset_file(asset, bb.id, cap=cap)
    objs = [o for lod in sorted(meshes) for o in meshes[lod].values()]
    reshaped = bool((getattr(bb, "report", None) or {}).get("shape"))
    if getattr(bb, "source", "current") == "makehuman" and (not fitted or reshaped) and (base.get("kind") != "hair" or reshaped):
        # Gear cut against the cage: clear the visible MakeHuman skin. Hair on
        # a reshaped body is cleared before its LODs (hair._clear_visible_skin)
        # and once more here, after the decimation has moved its vertices.
        from . import mh_body

        mh_body.clear_skin(objs, bb, set(base.get("hides", [])), builds=reshaped)
    EX.export_glb(os.path.join(assets, rel), bb.skel, objs, animations=False)
    entry = _item_entry(manifest, asset, base)
    key = "cap_files" if cap else "files"
    entry.setdefault(key, {})[bb.id] = rel
    lod0 = list(meshes[0].values())
    tri_key = "cap_triangles" if cap else "triangles"
    entry.setdefault(tri_key, {})[bb.id] = EX.lod_tris(meshes)
    if not cap and base.get("kind") == "clothing":
        from . import shade_bake as SB

        entry.setdefault("shade_bytes", {})[bb.id] = SB.shade_bytes(objs)
    if not cap:
        entry["bounds"][bb.id] = EX.bounds(lod0)
    PL.log(f"exported {rel}")


def build_hair(bb: PL.BodyBuild, assets: str, manifest: dict, items: set[str], source: str | None = None) -> None:
    """The body's own hair styles (creator.json v2: each style belongs to one
    body): from the Meshy-derived source meshes (charlib/hair_meshy.py,
    `art/characters/hair_meshy/`) or as locks (charlib/hair.py), per the
    style's `source` or the `source` override; files and entries of retired
    styles are removed."""
    from . import hair
    from . import hair_meshy

    hair.remove_retired(assets, manifest)
    for style in hair_styles(bb.id):
        if items and style["asset"] not in items:
            continue
        hair_meshy.build(bb, style, assets, manifest, source=source)


def build_gear(bb: PL.BodyBuild, assets: str, manifest: dict, items: set[str], source: str = "builders") -> None:
    """`source="builders"`: one builder per item (charlib/gear.py). `"shapes"`:
    items with a design in data/items/garment_designs.json are composed from
    a fitted garment shape (charlib/designs.py); the rest use their builder."""
    from . import gear

    if source == "shapes":
        from . import designs

        designs.build_all(bb, assets, manifest, items)
        return

    for item in clothing_items():
        if items and item["asset"] not in items:
            continue
        gear.build_item(bb, item, assets, manifest)
