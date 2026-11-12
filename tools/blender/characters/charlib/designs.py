"""Designs on garment shapes, composed into the per-item clothing files the
client already loads (docs/art/garment-shapes.md).

A design (data/items/garment_designs.json) is:

* `shape` + `options`: which fitted shape (and which of its fitted options:
  neckline, sleeve length, shoe cut, inner insert);
* `base` and `paint`: the dye-region layout and pattern, painted by the
  shape's zones and face coordinates (swatch = atlas zone, dye = COLOR_0
  channel);
* `relief`: optional surface patterns that push the fitted surface outward
  only (cables), so they cannot break the cage;
* `trims`: parts grown from the shape's loops (hems, cuffs, collars, soles);
* `addons`: parts attached at the shape's anchors and lines (ties, buttons,
  collars, pompoms, crests).

`build_item` fits the shape to the body being built, composes the design and
exports exactly what `gear.build_item` exports (same asset key, file name,
node names and extras, LODs, COLOR_0 dye mask, atlas UVs, manifest entry), so
the client, protocol and catalog do not change.
"""

from __future__ import annotations

import math
import time

import numpy as np

from . import addons as AD
from . import content
from . import fit as F
from . import pipeline as PL
from . import rig as RG
from . import shapes as SH

_FRAMES: dict[int, F.BodyFrame] = {}
REPORT: dict[str, dict] = {}


def shapes_data() -> dict:
    return content.load_json("data/items/garment_shapes.json")


def designs_data() -> dict:
    """Designs, with `extends` resolved (a design copies another and overrides keys)."""
    data = content.load_json("data/items/garment_designs.json")
    raw = data["designs"]

    def resolve(key, seen=()):
        d = raw[key]
        if "extends" not in d:
            return dict(d)
        if key in seen:
            raise ValueError(f"design {key}: extends loop")
        base = resolve(d["extends"], seen + (key,))
        base.pop("item_id", None)
        base.pop("preview_dyes", None)
        base.update({k: v for k, v in d.items() if k != "extends"})
        return base

    data["designs"] = {k: resolve(k) for k in raw}
    return data


FIT_TO = "render"  # "render": the body's LOD0 objects (any topology); "cage": bb.md (the generator's layout mesh)


def frame_for(bb) -> F.BodyFrame:
    """The body frame shapes are fitted against. By default it is read back
    from the body's rendered LOD0 objects (`fit.meshdata_from_objects`), i.e.
    exactly the surface the client draws and the clipping check tests, and
    nothing a generator keeps on the side (layout rows, rings, face tags), so
    the same code fits the current body and the MakeHuman body."""
    key = id(bb)
    fr = _FRAMES.get(key)
    if fr is None:
        t0 = time.time()
        objs = list(bb.meshes.get(0, {}).values()) if FIT_TO == "render" else []
        md = F.meshdata_from_objects(objs) if objs else bb.md
        fr = _FRAMES[key] = F.BodyFrame(md, F.Joints.from_armature(bb.skel), bb.id)
        if (getattr(bb, "report", None) or {}).get("shape"):
            # A body reshaped onto a sculpt: its eyes sit lower in a larger
            # cranium than on our heads, so the bounding-box head centre
            # (60% up the head) would put every hat band high on the
            # forehead. Hats use the hair's head frame instead (eye level, as
            # hair.band_phi and the hair caps do), raised by the offset the
            # bounding-box rule has on our own heads (eyes 48.5% up).
            from . import skeleton as K

            fr.head_centre = np.array(fr.head_centre, dtype=np.float64)
            fr.head_centre[2] = float(K.head_centre(bb.body)[2]) + (0.6 - 0.485) * fr.head_size
        PL.log(f"{bb.id}: body frame from {'LOD0 objects' if objs else 'bb.md'}, {len(md.faces)} faces, measured "
                f"({time.time() - t0:.1f}s): {fr.summary()}")
    return fr


# ----------------------------------------------------------------------------
# Painting by zones and patterns
# ----------------------------------------------------------------------------

_RANGE_KEYS = ("u", "front", "up", "phi", "fwd", "x", "z", "s", "sf", "t")


def match(pred: dict, c: dict, fs: SH.FittedShape, ends: dict) -> bool:
    """Does a face (its shape coordinates) satisfy a zone predicate?"""
    for k, v in pred.items():
        if k == "part":
            if c.get("part") not in (v if isinstance(v, list) else [v]):
                return False
        elif k in _RANGE_KEYS:
            val = c.get(k)
            if val is None or not (v[0] <= val <= v[1]):
                return False
        elif k.startswith("abs_") and k[4:] in _RANGE_KEYS:
            val = c.get(k[4:])
            if val is None or not (v[0] <= abs(val) <= v[1]):
                return False
        elif k == "row":
            base = fs.levels[v[0]] if isinstance(v[0], str) else 0
            r = c.get("row")
            if r is None or not (base + v[-2] <= r <= base + v[-1]):
                return False
        elif k == "ring":
            r = c.get("ring")
            if r is None or not (v[0] <= r <= v[1]):
                return False
        elif k == "ring_from_end":
            r = c.get("ring")
            end = ends.get((c.get("part"), c.get("side")))
            if r is None or end is None or not (v[0] <= end - r <= v[1]):
                return False
        elif k == "col_mod":
            col = c.get("col")
            if col is None or (col % v[0]) not in (v[1] if isinstance(v[1], list) else [v[1]]):
                return False
        elif k == "not":
            if match(v, c, fs, ends):
                return False
        elif k == "any":
            if not any(match(p, c, fs, ends) for p in v):
                return False
        elif c.get(k) != v:
            return False
    return True


def _ends(fs: SH.FittedShape) -> dict:
    out: dict = {}
    for fi in fs.shape_faces:
        c = fs.g.coords[fi]
        if "ring" in c:
            key = (c.get("part"), c.get("side"))
            out[key] = max(out.get(key, -1), c["ring"])
    return out


def paint_shape(fs: SH.FittedShape, design: dict, shape_def: dict) -> None:
    g = fs.g
    zones = shape_def.get("zones", {})
    ends = _ends(fs)
    base = AD.paint(design.get("base"))
    for fi in fs.shape_faces:
        g.swatch[fi], g.mask[fi] = base
    for layer in design.get("paint", []):
        pred = dict(zones[layer["zone"]]) if "zone" in layer else {}
        pred.update(layer.get("where", {}))
        pattern = layer.get("pattern")
        for fi in fs.shape_faces:
            c = g.coords[fi]
            if not match(pred, c, fs, ends):
                continue
            if pattern is None:
                g.swatch[fi], g.mask[fi] = AD.paint(layer["paint"])
            elif pattern == "stripes":
                by = layer.get("by", "row")
                key = c.get(by, 0) - (fs.levels.get(layer["from"], 0) if layer.get("from") else 0)
                paints = layer["paints"]
                sw, m = AD.paint(paints[(int(key) // layer.get("band", 1)) % len(paints)])
                if layer.get("ribs") and c.get("col", 0) % 2:
                    sw = "dye_soft"
                g.swatch[fi], g.mask[fi] = sw, m
            elif pattern == "ribs":
                if c.get("col", 0) % layer.get("every", 2) == layer.get("phase", 1):
                    g.swatch[fi] = layer.get("swatch", "dye_soft")
            elif pattern == "check":
                # Tartan / plaid: vertical bands by column, horizontal bands by
                # ring (or row); paints = [ground, vertical, horizontal, crossing].
                v = (int(c.get("col", 0)) // layer.get("every", 4)) % 2
                h = (int(c.get("ring", c.get("row", 0))) // layer.get("band", 2)) % 2
                g.swatch[fi], g.mask[fi] = AD.paint(layer["paints"][v + 2 * h])
            elif pattern == "tartan":
                # A woven sett: a letter per column (warp) and per ring or row
                # (weft) from the strings `warp` / `weft`; G ground, B band,
                # T thin trim line. T wins, B x B is the darker crossing (X),
                # B over G stays B: the graphic, flat-colour plaid of the style.
                warp, weft = layer["warp"], layer["weft"]
                a = warp[int(c.get("col", 0)) % len(warp)]
                b = weft[int(c.get("ring", c.get("row", 0))) % len(weft)]
                key = "T" if "T" in (a, b) else ("X" if a == b == "B" else ("B" if "B" in (a, b) else "G"))
                g.swatch[fi], g.mask[fi] = AD.paint(layer["paints"][key])
            elif pattern == "cable":
                u = math.radians(c.get("u", 0.0))
                n = layer.get("cables", 10)
                if math.cos(n * u) < -0.55 and abs(math.cos(u)) > layer.get("front_only", 0.45):
                    g.swatch[fi] = layer.get("swatch", "dye_soft")
            else:
                raise ValueError(f"unknown pattern {pattern}")


def relief(fs: SH.FittedShape, spec: dict) -> None:
    """Outward-only surface relief on fitted vertices (cable knit)."""
    g = fs.g
    if spec["type"] != "cable":
        raise ValueError(spec["type"])
    amount = spec.get("amount", 0.0045)
    n = spec.get("cables", 10)
    ids = [v for v, c in g.vcoords.items() if c.get("part") in spec.get("parts", ["torso"])]
    if not ids:
        return
    for s in g.shapes:
        P = g.positions(s)
        N = g.normals(s)
        for v in ids:
            th = math.radians(g.vcoords[v].get("u", 0.0))
            z = g.positions("base")[v][2]
            twist = 1.8 * math.sin(z * 70.0)
            cable = 0.5 + 0.5 * math.cos(n * th + twist * (abs(math.sin(n / 2 * th)) > 0.4))
            P[v] = P[v] + N[v] * amount * cable * abs(math.cos(th)) ** 0.5
        g.pos[s] = [P]


# ----------------------------------------------------------------------------
# Composition and export
# ----------------------------------------------------------------------------


_FITTED: dict[tuple, SH.FittedShape] = {}
ADDON_CLEAR = 0.003  # metres between skin and any trim or add-on vertex (before linings)


def fitted(fr: F.BodyFrame, shape: str, shape_def: dict, options: dict | None) -> tuple[SH.FittedShape, float, bool]:
    """The shape fitted to this body with these options: fitted once per body
    and (shape, options), then cloned for every design that uses it."""
    import json

    key = (id(fr), shape, json.dumps(options or {}, sort_keys=True))
    hit = _FITTED.get(key)
    t0 = time.time()
    if hit is None:
        hit = _FITTED[key] = SH.build(fr, shape, shape_def, options)
        return hit.clone(), time.time() - t0, False
    return hit.clone(), time.time() - t0, True


def compose(fr: F.BodyFrame, asset: str, design: dict, shapes: dict) -> tuple[SH.FittedShape, dict]:
    shape_def = shapes[design["shape"]]
    fs, t_fit, reused = fitted(fr, design["shape"], shape_def, design.get("options"))
    paint_shape(fs, design, shape_def)
    if design.get("relief"):
        relief(fs, design["relief"])
    thicken = []
    # Lined parts of the shape (skirts, bell sleeves): a thickness with the lining inside.
    ends = _ends(fs)
    for spec in design.get("thicken", []):
        pred = dict(shape_def.get("zones", {}).get(spec.get("zone"), {}))
        pred.update(spec.get("where", {}))
        faces = [fi for fi in fs.shape_faces if match(pred, fs.g.coords[fi], fs, ends)]
        thicken.append((faces, spec.get("thickness", 0.004), AD.paint(spec["inner"]), AD.paint(spec["rim"])))
    n_shape = fs.g.n
    for t in design.get("trims", []):
        AD.TRIMS[t["type"]](fs, t)
    for a in design.get("addons", []):
        thicken += AD.ADDONS[a["type"]](fs, a) or []
    if shape_def.get("slot") == "body":
        fs.stats["neck_weights_evened"] = AD.even_neck_weights(fs)  # collars turn with the neck in one piece
    g = fs.g
    # The cage guarantee extends to trims and add-ons (collars round the
    # neck, cuffs, hems): their vertices clear the skin in every build by
    # enough room for the lining that thickening adds inward afterwards.
    # Only against skin the item leaves visible (a sole may sit inside the
    # hidden foot; a collar must clear the visible neck).
    lining = max([th for _, th, _, _ in thicken] or [0.0])
    visible = tuple(r for r in F.ALL if r not in fs.hides or r in design.get("show", []))
    fs.stats["addon_clearance_moves"] = F.enforce_clearance(g, range(n_shape, g.n), ADDON_CLEAR + lining, visible)
    g.orient()
    for faces, thickness, inner, rim in thicken:
        g.thicken(faces, thickness, inner, rim)
    hides = list(fs.hides)
    for r in design.get("show", []):
        if r in hides:
            hides.remove(r)
    for r in design.get("hide", []):  # extra regions a design covers (earflaps over the ears)
        if r not in hides:
            hides.append(r)
    meta = {"hides": hides, "long": bool(design.get("long", False)), "budget": shape_def.get("budget"),
            "fit_s": round(t_fit, 2), "fit_reused": reused, "shape_stats": fs.stats, "shape_tris": int(sum(len(g.faces[f]) - 2 for f in fs.shape_faces))}
    return fs, meta


def build_item(bb, asset: str, design: dict, shapes: dict, assets: str, manifest: dict, item: dict | None = None) -> None:
    fr = frame_for(bb)
    fs, meta = compose(fr, asset, design, shapes)
    shape_def = shapes[design["shape"]]
    slot = shape_def["slot"]
    md = fs.g.meshdata()
    name = asset.split("/")[-1] + "_" + bb.id
    coll = PL.collection(f"{bb.id}_gear")
    budgets = PL.LOD_BUDGETS[PL.SLOT_PART[slot]]
    budget = min(meta["budget"] or budgets[0], budgets[0])
    meshes = PL.make_lods(name, md, bb.material, coll, budget=budget, budgets=budgets)
    for parts in meshes.values():
        for o in parts.values():
            o["asset"] = asset
            o["slot"] = slot
            o["hides"] = meta["hides"]
            o["long"] = meta["long"]
            RG.bind(o, bb.skel)
    # The shade layer: AO with the body as occluder, the shape's fold bands, the gradient.
    from . import shade_bake as SB

    bands = SB.folds_for(design["shape"], design)
    shade = SB.bake_item(meshes, bb.skel, list(bb.meshes[0].values()), bands)
    # Hidden covered geometry (charlib/layers.py): where every vertex lies over
    # the body, and what the item covers of a lower layer's (legs and body items).
    from . import drape as DR
    from . import layers as LY

    if fs.hanging and slot in LY.COVERING_SLOTS:
        # The sitting drape: a shape key per seated pose (charlib/drape.py).
        meta["drape"] = DR.bake(fs, meshes, bb, slot)
    if slot in LY.ANCHORED_SLOTS:
        lod0 = list(meshes[0].values())
        if slot in LY.COVERING_SLOTS:
            heights, meta["covers"] = LY.covers(lod0, bb)
            for o in lod0:
                o["covers"] = LY.encode(heights)
        LY.anchor([o for parts in meshes.values() for o in parts.values()], bb)
    item_ids = [item["id"]] if item else []
    base = {"kind": "clothing", "slot": slot, "item_ids": item_ids, "hides": meta["hides"], "long": meta["long"],
            "dye_mask": ["primary", "secondary", "trim"]}
    entry = manifest["assets"].get(asset)
    if entry and item and item["id"] not in entry.get("item_ids", []):
        base["item_ids"] = sorted(set(entry.get("item_ids", [])) | {item["id"]})
    content.export_item(bb, asset, meshes, assets, manifest, base, fitted=True)
    tris = {lvl: sum(len(p.vertices) - 2 for o in parts.values() for p in o.data.polygons) for lvl, parts in meshes.items()}
    REPORT.setdefault(asset, {})[bb.id] = {"shape": design["shape"], "options": design.get("options", {}),
                                           "lod0": tris[0], "lod1": tris[1], "lod2": tris.get(2), "lod3": tris.get(3),
                                           "shade": shade, "folds": bands, "fit_s": meta["fit_s"], "fit_reused": meta["fit_reused"],
                                           "shape_tris": meta["shape_tris"], "covers": meta.get("covers"), "drape": meta.get("drape"),
                                           "clearance_moves": meta["shape_stats"].get("clearance_moves", 0)}
    cov = meta.get("covers")
    PL.log(f"{asset} [{design['shape']}] {bb.id}: {tris[0]} tris (fit {meta['fit_s']}s{', reused' if meta['fit_reused'] else ''})"
           + (f", covers {cov['kept']} body vertices" if cov else ""))
    for o in [x for parts in meshes.values() for x in parts.values()]:
        o.hide_set(True)
        o.hide_render = True


def build_all(bb, assets: str, manifest: dict, items: set[str]) -> None:
    """Every catalog clothing item: from its design when it has one, else from
    its old builder; then the designs that are not in the catalog yet."""
    from . import gear

    shapes = shapes_data()["shapes"]
    designs = designs_data()["designs"]
    catalog = {i["asset"]: i for i in content.clothing_items()}
    for asset, item in catalog.items():
        if items and asset not in items:
            continue
        d = designs.get(asset)
        if d is None:
            gear.build_item(bb, item, assets, manifest)
        else:
            build_item(bb, asset, d, shapes, assets, manifest, item)
    for asset, d in designs.items():
        if asset in catalog or (items and asset not in items):
            continue
        build_item(bb, asset, d, shapes, assets, manifest, None)
