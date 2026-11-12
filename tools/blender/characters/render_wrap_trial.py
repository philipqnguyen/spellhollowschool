"""Review renders for the sculpt-wrapped bodies (charlib/sculpt_wrap.py).

    blender -b --factory-startup --python tools/blender/characters/render_wrap_trial.py -- \\
        --char "pre-wrap=<assets root>" --char "wrapped=<assets root>" [--char "greywax=<assets root>:body_b"] \\
        --sculpt-dir <sculpt folders> --spell-clips <path to a 1d anim_spells.py> \\
        --out art/renders/body-trial/wrap [--bodies body_a,body_b] [--only compare,poses]

Each `--char` is a label and an assets root (what the client loads), with an
optional `:body,...` filter. Sheets per body:

* `compare_<body>`: front, side and back of every character (cel-shaded,
  base layer and short hair) next to the sculpt it was wrapped onto (grey,
  as imported, oriented and scaled to the spec height). The sculpts are read
  from --sculpt-dir and never written anywhere.
* `poses_<label>_<body>`: deformation check: walk, run, wave, bow and sit from
  the exported clips, then the milestone-1d spell, duel and class poses
  keyed on a fresh rig built from the body's own measurements (the clips'
  module is read from --spell-clips; nothing of it is copied).
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402
import numpy as np  # noqa: E402

import render_body_trial as RB  # noqa: E402
import render_characters as RC  # noqa: E402
from charlib import anim as AN  # noqa: E402
from charlib import atlas as A  # noqa: E402
from charlib import proportions as P  # noqa: E402
from charlib import render as RD  # noqa: E402
from charlib import rig as RG  # noqa: E402
from charlib import shape_target as ST  # noqa: E402
from charlib import toon  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
VIEWS = (("front", 0, 4), ("side", 90, 4), ("back", 180, 6))
EXPORTED = (("walk", 0.0), ("walk", 0.25), ("run", 0.3), ("wave", 0.5), ("bow", 0.5), ("sit", 0.5))
SPELLS = (("cast", 13), ("duel_ready", 10), ("shield_raise", 12), ("hit_react", 7), ("knockback", 14),
          ("stun_fall", 40), ("disarmed", 8), ("gesture", 0.55), ("desk_practice", 20))


def args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--char", action="append", default=[])
    p.add_argument("--sculpt-dir", default=os.environ.get("SHS_SCULPT_DIR", ""))
    p.add_argument("--shape-dirs", default=os.path.join(REPO, ST.DEFAULT_DIR))
    p.add_argument("--spell-clips", default="")
    p.add_argument("--out", default=os.path.join(REPO, "art", "renders", "body-trial", "wrap"))
    p.add_argument("--bodies", default="body_a,body_b")
    p.add_argument("--only", default="compare,poses")
    p.add_argument("--pose-all", action="store_true", help="pose sheets for unshaped bodies too")
    return p.parse_args(argv)


@dataclasses.dataclass
class CharSpec:
    label: str
    root: str
    bodies: set | None

    @classmethod
    def parse(cls, text: str) -> "CharSpec":
        label, _, rest = text.partition("=")
        root, _, bodies = rest.partition(":")
        if ":" in rest and len(rest.split(":")[0]) == 1:  # a Windows drive letter, not a filter
            root, bodies = rest, ""
        return cls(label, os.path.abspath(root), set(bodies.split(",")) if bodies else None)

    def manifest(self) -> dict:
        with open(os.path.join(self.root, "characters", "manifest.json"), encoding="utf-8") as f:
            return json.load(f)


def body_params(spec: CharSpec, body: str, shape_dirs: list[str]):
    """The body's measurements: from its baked shape target (wrapped
    bodies) or the spec."""
    entry = spec.manifest()["assets"][f"characters/{body}"]
    shape = entry.get("shape")
    if shape:
        for d in shape_dirs:
            path = os.path.join(d, shape["target"])
            if os.path.exists(path):
                return ST.load_target(path)["body"], shape
        raise FileNotFoundError(f"shape target {shape['target']} not in {shape_dirs}")
    return P.get(body), None


# ----------------------------------------------------------------------------
# The spell and class clips (read from the given module; trial anim.Clip gains `meta`)
# ----------------------------------------------------------------------------


def load_spell_clips(path: str) -> dict:
    if not path:
        return {}
    if "meta" not in AN.Clip.__dataclass_fields__:
        @dataclasses.dataclass
        class ClipM(AN.Clip):
            meta: dict = dataclasses.field(default_factory=dict)

        AN.Clip = ClipM
    spec = importlib.util.spec_from_file_location("charlib.anim_spells", path)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "charlib"
    sys.modules["charlib.anim_spells"] = mod
    spec.loader.exec_module(mod)
    return {c.name: c for c in mod.CLIPS}


class RigDriver:
    """A Rigify rig built from the body's measurements, posed with a clip and
    copied bone by bone onto the imported character's armature (same
    skeleton, same rest joints)."""

    def __init__(self, body, arm):
        self.body = body
        self.arm = arm
        mr = RG.build_metarig(body, f"wrapmeta_{body.id}_{arm.name}")
        self.rig = RG.generate(mr, f"wraprig_{body.id}_{arm.name}")
        self.rig.hide_render = True
        mr.hide_render = True
        for o in bpy.data.objects:
            if o.name.startswith(("WGT-", "wrapmeta_")):
                o.hide_render = True

    def pose(self, clip, frame: float) -> None:
        self.rig.location = self.arm.location
        bpy.context.view_layer.update()
        AN.pose_rig(self.rig, self.body, clip, frame)
        bpy.context.view_layer.update()
        rw, aw = self.rig.matrix_world, self.arm.matrix_world
        order = sorted(self.arm.pose.bones, key=lambda pb: len(pb.parent_recursive))
        for pb in order:
            src = self.rig.pose.bones.get(pb.name)
            if src is None:
                continue
            delta = rw @ src.matrix @ src.bone.matrix_local.inverted() @ rw.inverted()
            pb.matrix = aw.inverted() @ delta @ aw @ pb.bone.matrix_local
            bpy.context.view_layer.update()


# ----------------------------------------------------------------------------
# Scene helpers
# ----------------------------------------------------------------------------


def grey_material():
    m = bpy.data.materials.get("sculpt_grey")
    if m is None:
        m = bpy.data.materials.new("sculpt_grey")
        m.use_nodes = True
        bsdf = m.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = (0.42, 0.42, 0.44, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.8
        # Lit from every side in the toon studio (its lights face the front).
        bsdf.inputs["Emission Color"].default_value = (0.2, 0.2, 0.21, 1.0)
        bsdf.inputs["Emission Strength"].default_value = 1.0
    return m


def sculpt_object(path: str, facing: str, height: float, name: str):
    """The sculpt as imported, turned to face -Y and scaled to `height`
    (the same orientation the wrap uses, before any smoothing)."""
    from charlib import sculpt_wrap as SW

    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    Vs, Ts, off = [], [], 0
    for o in new:
        if o.type == "MESH":
            me = o.data
            mw = np.array(o.matrix_world)
            co = np.zeros(len(me.vertices) * 3)
            me.vertices.foreach_get("co", co)
            Vs.append(co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3])
            me.calc_loop_triangles()
            t = np.zeros(len(me.loop_triangles) * 3, dtype=np.int64)
            me.loop_triangles.foreach_get("vertices", t)
            Ts.append(t.reshape(-1, 3) + off)
            off += len(Vs[-1])
    for o in new:
        bpy.data.objects.remove(o)
    V = SW.orient(np.concatenate(Vs), facing, height)
    me = bpy.data.meshes.new(name)
    me.from_pydata(V.tolist(), [], np.concatenate(Ts).tolist())
    me.update()
    me.shade_smooth()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(grey_material())
    return obj


class Lineup:
    """Characters and sculpts in one scene; one stands at the origin at a time."""

    def __init__(self):
        self.items: list[tuple[str, object]] = []

    def add(self, label: str, thing) -> None:
        self.items.append((label, thing))

    def solo(self, label: str) -> None:
        for i, (lab, thing) in enumerate(self.items):
            x = 0.0 if lab == label else 50.0 + 5.0 * i
            if isinstance(thing, RC.Character):
                thing.arm.location = (x, 0.0, 0.0)
            else:
                thing.location = (x, 0.0, 0.0)
        bpy.context.view_layer.update()


def base_layer(ch, body: str, hair_idx: int = RB.HAIR) -> None:
    ch.clear_items()
    hair = RC.CREATOR["hair_styles"][hair_idx]["asset"]
    if hair in ch.manifest["assets"] and body in ch.manifest["assets"][hair].get("files", {}):
        ch.add(hair)
        ch.hide_regions(set(ch.manifest["assets"][hair]["hides"]))
    ch.tint(RC.CREATOR["skin_tones"][RB.SKIN]["hex"], RC.CREATOR["hair_colours"][RB.HAIR_COL]["hex"], {})


# ----------------------------------------------------------------------------
# Sheets
# ----------------------------------------------------------------------------


def sheet_compare(body: str, chars: list[CharSpec], a, out: str) -> None:
    from charlib import sculpt_wrap as SW

    bpy.ops.wm.read_factory_settings(use_empty=True)
    RG.enable_rigify()
    A.build_image()
    toon.studio((420, 720))
    line = Lineup()
    order = []
    height = P.get(body).height
    for spec in chars:
        if spec.bodies is not None and body not in spec.bodies:
            continue
        man = spec.manifest()
        if f"characters/{body}" not in man["assets"]:
            continue
        ch = RB.ToonCharacter(spec.root, man, body)
        base_layer(ch, body)
        ch.reset_keys()
        ch.play(None)
        line.add(spec.label, ch)
        order.append(spec.label)
        shape = man["assets"][f"characters/{body}"].get("shape")
        if shape and a.sculpt_dir:
            variant = shape["target"][len(body) + 1:-4] if shape["target"] != f"{body}.npz" else ""
            sc = SW.SCULPTS.get((body, variant))
            path = os.path.join(a.sculpt_dir, sc.file) if sc else ""
            if sc and os.path.exists(path):
                lab = f"sculpt:{sc.author}"
                line.add(lab, sculpt_object(path, sc.facing, height, lab))
                order.append(lab)
    paths = []
    for v, az, el in VIEWS:
        for lab in order:
            line.solo(lab)
            RD.aim((0, 0, height * 0.52), az, el, 3.6, lens=50)
            paths.append(RD.shoot(os.path.join(out, f"_cmp_{body}_{v}_{len(paths)}.png")))
    RD.sheet(paths, os.path.join(out, f"compare_{body}.png"), len(order))
    for p in paths:
        if os.path.exists(p):
            os.remove(p)
    print(f"[render_wrap] compare_{body}: columns {order}", flush=True)


def sheet_poses(body: str, spec: CharSpec, a, out: str, clips: dict) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    RG.enable_rigify()
    A.build_image()
    toon.studio((520, 640))
    man = spec.manifest()
    ch = RB.ToonCharacter(spec.root, man, body)
    base_layer(ch, body)
    ch.reset_keys()
    b, _ = body_params(spec, body, a.shape_dirs.split(","))
    h = b.height
    paths = []
    anims = man["animations"]
    for clip, u in EXPORTED:
        ch.play(clip, round(anims[clip]["frames"] * u))
        RD.aim((0, 0, h * (0.38 if clip == "sit" else 0.5)), 35, 8, 4.0, lens=50)
        paths.append(RD.shoot(os.path.join(out, f"_po_{len(paths)}.png")))
    if clips:
        ch.play(None)
        ad = ch.arm.animation_data
        if ad:
            ad.action = None
        drv = RigDriver(b, ch.arm)
        for name, f in SPELLS:
            clip = clips.get(name) or next((c for n, c in clips.items() if n.startswith(name)), None)
            if clip is None:
                continue
            frame = f * clip.frames if isinstance(f, float) and f < 1 else f
            drv.pose(clip, frame)
            z = 0.25 if name == "stun_fall" else 0.38 if name.startswith("desk") else 0.5
            el = 30 if name == "stun_fall" else 8
            RD.aim((0, -0.3 if name == "stun_fall" else 0, h * z), 35, el, 4.0, lens=50)
            paths.append(RD.shoot(os.path.join(out, f"_po_{len(paths)}.png")))
    RD.sheet(paths, os.path.join(out, f"poses_{spec.label}_{body}.png"), 5)
    for p in paths:
        if os.path.exists(p):
            os.remove(p)
    print(f"[render_wrap] poses_{spec.label}_{body}: {len(paths)} poses", flush=True)


def main() -> None:
    a = args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    chars = [CharSpec.parse(c) for c in a.char]
    only = set(a.only.split(","))
    clips = load_spell_clips(a.spell_clips) if "poses" in only else {}
    for body in a.bodies.split(","):
        if "compare" in only:
            sheet_compare(body, chars, a, out)
        if "poses" in only:
            for spec in chars:
                if spec.bodies is not None and body not in spec.bodies:
                    continue
                if a.pose_all or spec.manifest()["assets"].get(f"characters/{body}", {}).get("shape"):
                    sheet_poses(body, spec, a, out, clips)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
