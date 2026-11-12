"""The clips (charlib/anim_spells.py): names the client can tell
apart, one clip per gesture of data/classes/gestures.json with sensible
timing, and the arm IK actually putting the wand tip on the gesture path
(needs bpy and the Rigify add-on)."""

import json
import math
import re
import unittest

import bpy
from mathutils import Vector

from charlib import anim as AN
from charlib import anim_spells as SP
from charlib import rig as RG
from charlib.proportions import BODIES

# Words client/src/game/characters/clips.ts maps to its built-in animations
# (animForClipName); only `cast` may use one. The client never maps a
# `gesture_<key>` clip, so a gesture key may use them (`colour_wave`).
CLIENT_WORDS = {"idle", "walk", "run", "wave", "bow", "sit", "cast", "sitting", "seated", "waving", "bowing", "running",
                "jog", "walking"}


def tokens(name: str) -> set[str]:
    return set(filter(None, re.split(r"[^a-z]+", name.lower())))


class ClipListTests(unittest.TestCase):
    def test_registered_once_with_unique_names(self):
        names = [c.name for c in AN.CLIPS]
        self.assertEqual(len(names), len(set(names)))
        for c in SP.CLIPS:
            self.assertIn(c, AN.CLIPS)
        self.assertEqual(set(AN.clip_meta()), set(names))

    def test_names_do_not_collide_with_client_animations(self):
        for c in SP.CLIPS:
            if c.name == "cast" or c.name.startswith("gesture_"):
                continue
            self.assertFalse(tokens(c.name) & CLIENT_WORDS, c.name)

    def test_gesture_clips_are_prefixed(self):
        # The prefix is what keeps the client from mapping them.
        for c in SP.CLIPS:
            if "gesture" in c.meta:
                self.assertTrue(c.name.startswith("gesture_"), c.name)

    def test_one_clip_per_gesture_with_timing(self):
        with open(SP.GESTURES_PATH, encoding="utf-8") as f:
            gestures = json.load(f)["gestures"]
        by_name = {c.name: c for c in SP.CLIPS}
        for g in gestures:
            clip = by_name.get(f"gesture_{g['key']}")
            self.assertIsNotNone(clip, g["key"])
            start, end = clip.meta["stroke_s"]
            self.assertEqual(clip.meta["release_s"], end)
            self.assertLess(0.0, start)
            self.assertLess(start, end)
            self.assertLessEqual(end, clip.frames / AN.FPS)
            self.assertFalse(clip.loop)
            # The stroke is no faster than the scorer's slowest bound allows.
            self.assertGreaterEqual((end - start) * 1000, min(g["min_ms"], 350) - 1)

    def test_cast_matches_the_protocol(self):
        cast = next(c for c in SP.CLIPS if c.name == "cast")
        self.assertEqual(cast.frames, 27)  # CAST_ANIM_MS 900 at 30 fps
        self.assertLess(cast.meta["release_s"], cast.frames / AN.FPS)

    def test_shield_follows_scutara_numbers(self):
        with open(SP.REPO + "/data/spells.json", encoding="utf-8") as f:
            shield = next(s["duel"] for s in json.load(f)["spells"] if (s.get("duel") or {}).get("effect", {}).get("kind")
                          == "shield")
        clip = next(c for c in SP.CLIPS if c.name == "shield_raise")
        # Up within the cast time, held at least as long as the shield lasts.
        self.assertLessEqual(clip.meta["raised_s"] * 1000, shield["cast_ms"] + 10)
        held = (clip.meta["lowered_s"] - clip.meta["raised_s"]) * 1000
        self.assertGreaterEqual(held, shield["effect"]["shield_ms"] - 10)


class WandIkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        RG.enable_rigify()
        cls.rigs = {}
        for bid, body in BODIES.items():
            cls.rigs[bid] = (RG.generate(RG.build_metarig(body, f"metarig_{bid}"), f"rig_{bid}"), body)

    def tip(self, rig, body) -> Vector:
        bpy.context.view_layer.update()
        g = SP.Grip(body)
        hand = rig.pose.bones["hand_fk.R"]
        d = SP._delta(hand)
        return hand.head + d @ (g.grip - g.wrist + g.wand * SP.WAND_TIP)

    def test_gesture_tip_traces_the_template(self):
        clip = next(c for c in SP.CLIPS if c.name == "gesture_circle")
        start, end = clip.meta["stroke_s"]
        with open(SP.GESTURES_PATH, encoding="utf-8") as f:
            pts = next(g["points"] for g in json.load(f)["gestures"] if g["key"] == "circle")
        for bid, (rig, body) in self.rigs.items():
            plane = SP.Plane(body)
            for k in range(5):
                u = (k + 0.5) / 5
                t = start + (end - start) * u
                AN.pose_rig(rig, body, clip, t * AN.FPS)
                x, y = SP.along(pts, SP.stroke_progress(u))
                want = plane.tip(x, y)
                self.assertLess((self.tip(rig, body) - want).length, 0.02, (bid, k))

    def wrist(self, rig) -> tuple[float, float]:
        """The right wrist as posed: its bend from straight on the forearm and
        its twist about the forearm (degrees; rest is straight, untwisted)."""
        bpy.context.view_layer.update()
        fo, ha = rig.pose.bones["forearm_fk.R"], rig.pose.bones["hand_fk.R"]
        df, dh = SP._delta(fo), SP._delta(ha)
        rel = df.inverted() @ dh
        fore = (fo.bone.tail_local - fo.bone.head_local).normalized()
        hand = (ha.bone.tail_local - ha.bone.head_local).normalized()
        _, twist = SP._swing_twist_deg(rel, fore)
        return math.degrees((rel @ hand).angle(hand)), twist

    def test_wrist_stays_natural_holding_the_wand(self):
        """The arm is solved so the hand holds the wand on a natural wrist
        (no hand folded back to aim, no wrist wrung about the forearm): the
        clips the game plays with a wand out, and the gestures and stance."""
        names = ["cast", "wand_light", "duel_ready", "gesture_circle", "gesture_jab", "gesture_rising_spiral"]
        for name in names:
            clip = next(c for c in SP.CLIPS if c.name == name)
            for bid, (rig, body) in self.rigs.items():
                for k in range(0, clip.frames + 1, 2):
                    AN.pose_rig(rig, body, clip, float(k))
                    bend, twist = self.wrist(rig)
                    self.assertLess(bend, 62.0, (name, bid, k))
                    self.assertLess(abs(twist), 25.0, (name, bid, k))

    def test_grip_closes_the_hand(self):
        """Every clip holding the wand keys the grip's finger curls."""
        clip = next(c for c in SP.CLIPS if c.name == "wand_light")
        for bid, (rig, body) in self.rigs.items():
            AN.pose_rig(rig, body, clip, 0.0)
            bpy.context.view_layer.update()
            curls = SP.WG.grip_curls(body)
            for f in ("f_index", "f_middle", "f_ring", "f_pinky"):
                pb = rig.pose.bones[f"{f}.02.R"]
                self.assertGreater(math.degrees(pb.rotation_quaternion.angle), curls[f][1] - 1.0, (bid, f))

    def test_seated_clips_sit_on_the_seat_hands_in_lap(self):
        """Character adoption: every seated clip has its sit bones on the root
        (the seat top), the knees forward and level-ish, the heels on a floor
        0.46 m below the seat at the body's maximum height, and the free hand
        (both hands in sit and desk_rest) resting in the lap: nothing reaches
        for a desk."""
        clips = {c.name: c for c in AN.CLIPS}
        for name in ("sit", "desk_rest", "desk_practice"):
            clip = clips[name]
            for bid, (rig, body) in self.rigs.items():
                AN.pose_rig(rig, body, clip, 0.0)
                bpy.context.view_layer.update()
                hip = rig.pose.bones["ORG-thigh.R"].head
                knee = rig.pose.bones["ORG-shin.R"].head
                ankle = rig.pose.bones["ORG-foot.R"].head
                self.assertAlmostEqual(hip.z, AN.sit_height(body), delta=0.03, msg=(name, bid))
                self.assertGreater(knee.z, hip.z - 0.12, (name, bid))  # knees forward and up, not folded down
                self.assertLess(knee.y, hip.y - 0.25, (name, bid))
                # Heels on the floor at the maximum height (the ankle above the sole by its rest height).
                floor = AN.seat_floor(body)
                self.assertAlmostEqual(ankle.z - body.ankle[2], floor, delta=0.02, msg=(name, bid))
                hands = ("hand_fk.L", "hand_fk.R") if name != "desk_practice" else ("hand_fk.L",)
                for h in hands:
                    w = rig.pose.bones[h].head
                    self.assertLess(w.z, hip.z + 0.3, (name, bid, h))  # in the lap, not up on a desk
                    self.assertLess(w.y, hip.y - 0.05, (name, bid, h))

    def test_desk_practice_is_wand_practice_between_lap_and_chest(self):
        clip = next(c for c in SP.CLIPS if c.name == "desk_practice")
        for bid, (rig, body) in self.rigs.items():
            zs = []
            for f in range(0, clip.frames, 5):
                AN.pose_rig(rig, body, clip, float(f))
                bpy.context.view_layer.update()
                zs.append(rig.pose.bones["hand_fk.R"].head.z - AN.sit_height(body))
            self.assertGreater(min(zs), 0.0, bid)
            self.assertLess(max(zs), 0.55 * body.height / 1.78, bid)
            self.assertGreater(max(zs) - min(zs), 0.05, bid)  # it moves

    def test_stun_fall_ends_lying_down(self):
        clip = next(c for c in SP.CLIPS if c.name == "stun_fall")
        for bid, (rig, body) in self.rigs.items():
            AN.pose_rig(rig, body, clip, float(clip.frames))
            bpy.context.view_layer.update()
            head = rig.pose.bones["ORG-spine.006"].head
            self.assertLess(head.z, 0.45, bid)
            rise = next(c for c in SP.CLIPS if c.name == "stun_rise")
            AN.pose_rig(rig, body, rise, 0.0)
            bpy.context.view_layer.update()
            self.assertLess((rig.pose.bones["ORG-spine.006"].head - head).length, 0.12, bid)
            AN.pose_rig(rig, body, rise, float(rise.frames))
            bpy.context.view_layer.update()
            self.assertGreater(rig.pose.bones["ORG-spine.006"].head.z, body.height * 0.7, bid)


if __name__ == "__main__":
    unittest.main()
