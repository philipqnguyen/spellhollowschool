"""Hair made directly from the Meshy guides (docs/art/character-look.md section 2).

Two stages, so the build never needs `vendor_assets`:

* **prepare** (`prepare_hair_meshy.py`, run by hand): guide -> a cleaned,
  registered, hair-only **source mesh** on the body's own head, saved as
  `<asset>_<body>.npz` (`V`, `F`, `N`, `part`, `root_to_tip`, `meta`) and
  committed at `art/characters/hair_meshy/`;
* **build** (this module, from `content.build_hair` / `build_hair.py`):
  source mesh -> the clean-up (the style's edits, flyaways dropped, small
  holes filled, strands thinner than STRAND_MIN_W widened; character-look.md
  section 2), LOD0-LOD3 (quadric decimation, tips and the open hem held; LOD0 and
  LOD1 with an inner layer, a flipped copy of the shell a few millimetres in,
  shaded darker, so the shell's open edges and undersides show hair instead
  of the background),
  the `_cap` variant (the crown pressed under the hat band line, the tail
  moved to the nape), the inner scalp cap, `_SHADE`, smooth custom normals,
  sway chains and weights, the fringe hem; exported through `hair.py`'s
  path (same files, objects, extras and manifest entries as the lock
  builder, so the client needs no change).

The lock builder (`hair.py`, `hair_styles.py`) stays as the per-style
fallback: `STYLES[name]["source"]` is `"meshy"` or `"locks"`, and
`build_hair.py --source locks|meshy` overrides it. When a style's source
mesh is missing (e.g. `SHS_HAIR_MESHY` points elsewhere) the build falls back to its locks.

Head-frame boxes in the style data are head centimetres round the head
centre: (x0, x1, f0, f1, u0, u1), x = the character's left, f = forward,
u = up.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np
from mathutils import Vector

from . import hair_meshops as MO

SOURCE_MAX_TRIS = 60000
# LOD triangle budgets per length class, everything included (the shell, the
# inner layer, the inner scalp cap): character-look.md section 2 (pass 2: LOD0 and
# LOD1 carry the inner layer, a flipped copy of the shell, so they about
# double; LOD2 and LOD3 are unchanged from 2.3).
BUDGETS = {
    "short": (20000, 6000, 1000, 350),
    "medium": (22000, 7000, 1150, 400),
    "long": (24000, 8000, 1300, 450),
}
INNER_LODS = 2  # LOD0 and LOD1 carry the inner layer (LOD2 starts at 20 m: a few pixels of hair)
INNER_DEPTH = 0.0025  # metres the inner layer lies under the shell
INNER_TAPER = 0.004  # metres from the shell's open edge over which it reaches that depth (0 on the edge: sealed)
INNER_KEEP = 0.0015  # metres it keeps off any drawn skin or garment (never deeper than the shell's room)
INNER_EAR = 0.008  # metres: within this of a visible ear the inner layer stays on the shell
INNER_SHADE = 0.45  # its _SHADE.x: the hair's own shadow, darker than any open surface
INNER_Z = 0.0  # _SHADE.z of the inner surfaces (inner layer, inner cap): no angel ring (the band centre moves off the hair)
PARTS = ("crown", "fringe", "side", "back", "tail", "tie")
CROWN, FRINGE, SIDE, BACK, TAIL, TIE = range(6)
PROXY_SHARE = 0.6  # of the smooth proxy normal in the shading normal
TIP_PROXY_SHARE = 0.45  # at the lock tips (the last 15 % of the length)
ROOT_VALUE = 0.82  # _SHADE.x root darkening at the crown, to 1 by a quarter of the length
ROOT_SPAN = 0.25

# Per style: what every guide of it shares (body, length, chains, the build's
# clearances) and the default guide (`guide_id`); GUIDES holds what is the
# guide's own (the file, its concept, the recorded fit, the segmentation and
# the shaping at prepare time). A style may have more than one guide (a
# Meshy re-generation next to the first one): prepare_hair_meshy.py
# `--guide <id>` prepares from another, and the build reads the guide a
# source mesh was made from out of its meta.
_COMMON: dict[str, dict] = {
    "short_tousled": {"body": "body_a", "length": "short", "source": "meshy", "guide_id": "b",
                      "covers_ears": False, "trunk_clear": 0.012, "chains": {}},
    "side_swept": {"body": "body_a", "length": "short", "source": "meshy", "guide_id": "r",
                   "covers_ears": False, "trunk_clear": 0.012, "chains": {}},
    "shaggy_layers": {
        "body": "body_a", "length": "medium", "source": "meshy", "guide_id": "a", "covers_ears": True, "trunk_clear": 0.02,
        "chains": {"back": {"parts": [BACK], "start_u": -7.0}, "side.L": {"parts": [SIDE], "side": 1, "start_u": -4.0},
                   "side.R": {"parts": [SIDE], "side": -1, "start_u": -4.0}},
        # Build edits (pass 2, character-look.md section 2): the long side layers
        # reaching the collar drawn up (below the chin, smoothly).
        "shorten": [{"parts": [SIDE], "u0": -8.0, "span": 3.0}],
    },
    "long_bangs": {
        "body": "body_b", "length": "long", "source": "meshy", "guide_id": "r", "covers_ears": True,
        "chains": {"back": {"parts": [BACK], "start_u": -8.0}, "side.L": {"parts": [SIDE], "side": 1, "start_u": -8.0},
                   "side.R": {"parts": [SIDE], "side": -1, "start_u": -8.0}},
        # Below the neck the hair lies on the body and moves with it.
        "follow": {"max": 0.9, "back_max": 0.8},
        # The stray thin strand at the left temple, standing in front of the
        # side curtain (the viewer's right).
        "drop": [{"parts": [SIDE], "box": [6.5, 12.0, 3.0, 12.0, -5.0, 6.0]}],
    },
    "ponytail_locks": {
        "body": "body_b", "length": "long", "source": "meshy", "guide_id": "r", "covers_ears": False,
        "chains": {"back": {"parts": [TAIL], "along": "tail"}, "side.L": {"parts": [SIDE], "side": 1, "start_u": -3.0},
                   "side.R": {"parts": [SIDE], "side": -1, "start_u": -3.0}},
        # The thin side strands behind the ears (the side locks in front of
        # them stay).
        "drop": [{"parts": [SIDE], "box": [-30.0, 30.0, -30.0, -2.0, -30.0, 30.0]}],
    },
    "bob_locks": {
        "body": "body_b", "length": "medium", "source": "meshy", "guide_id": "a", "covers_ears": True, "trunk_clear": 0.02,
        "chains": {"side.L": {"parts": [SIDE], "side": 1, "start_u": -6.0, "front": True},
                   "side.R": {"parts": [SIDE], "side": -1, "start_u": -6.0, "front": True}},
    },
    # The second set (ids 20-24): Meshy only, no lock fallback (MESHY_ONLY).
    "swept_undercut": {"body": "body_a", "length": "short", "source": "meshy", "guide_id": "a",
                       "covers_ears": False, "trunk_clear": 0.012, "chains": {}, "no_fringe": True,
                       # The swept top's combed grooves drawn as strokes.
                       "crease_dark": 0.6,
                       # The clipped sides and nape under the swept top: the
                       # hair's own colour a shade darker, with a fine comb
                       # grain, cut in points; at the front only the
                       # hairline's edge (the top is combed back from it).
                       "clip": {"tip_deg": [[0.0, 1.2], [35.0, 1.2], [55.0, 3.0], [305.0, 3.0], [325.0, 1.2]],
                                "shade": 0.72, "valley_x": 0.45, "ridge_mm": 0.5, "period_deg": 5.0,
                                "slant_deg": 4.0, "sideburn_deg": 20.0, "h_top_mm": 4.0, "h_bot_mm": 2.5,
                                "tuck_deg": 5.0}},
    "low_tail": {
        "body": "body_a", "length": "medium", "source": "meshy", "guide_id": "a", "covers_ears": False,
        # The nape's hair clear of the neck; the tail and its tie stand a
        # little further back and up (the head pitches back over the neck in
        # the run).
        "trunk_clear": 0.035,
        "move": [{"parts": [TAIL, TIE], "back_m": 0.03, "up_m": 0.025}],
        "chains": {"back": {"parts": [TAIL], "along": "tail"}},
        # The tail is tied at the nape, under any hat's band: it stays put.
        "cap_keep_tail": True,
    },
    "crew_cut": {"body": "body_a", "length": "short", "source": "meshy", "guide_id": "a",
                 "covers_ears": False, "trunk_clear": 0.012, "chains": {}, "no_fringe": True,
                 "crease_dark": 0.45,
                 # The short sides, nape and hairline: nearly the top's own
                 # tone, a finer grain, short points.
                 "clip": {"shade": 0.82, "valley_x": 0.5,
                          "ridge_mm": 0.4, "period_deg": 4.0, "tip_deg": 2.5, "slant_deg": 3.0, "sideburn_deg": 18.0,
                          "h_top_mm": 4.0, "h_bot_mm": 2.5, "tuck_deg": 6.0}},
    "twin_tails": {
        "body": "body_b", "length": "long", "source": "meshy", "guide_id": "a", "covers_ears": False,
        # Each tail sways on its own side chain (no back chain: nothing hangs
        # down the back).
        "chains": {"side.L": {"parts": [TAIL], "side": 1, "along": "tail"},
                   "side.R": {"parts": [TAIL], "side": -1, "along": "tail"}},
        # Under a hat the tails hang as they are (their ties pressed under
        # the band).
        "cap_press_tails": True,
    },
    "side_braid": {
        "body": "body_b", "length": "long", "source": "meshy", "guide_id": "b", "covers_ears": True,
        # The braid over the left shoulder: one side chain down it; below
        # the neck it lies on the shoulder and chest and moves with them.
        "chains": {"side.L": {"parts": [TAIL], "side": 1, "along": "tail"}},
        "follow": {"max": 0.9},
        # The braid starts at the nape, under any hat's band: it stays put.
        "cap_keep_tail": True,
        # Sleek, combed hair: no angel-ring band across the top.
        "no_ring": True,
    },
}
# Styles with no lock-builder fallback (hair_styles.STYLES): built from their
# source mesh only.
MESHY_ONLY = ("swept_undercut", "low_tail", "crew_cut", "twin_tails", "side_braid")

# `concept_order`: the concept image indices of the front, side and back views.
GUIDES: dict[str, dict[str, dict]] = {
    "short_tousled": {
        "b": {"guide": "g_m1_b.glb", "concept": "c_m1_b", "concept_order": (0, 1, 2),
              "registration": {"scale": 0.19131, "offset": [0.0, -0.02455, 1.6136], "fit_rms_mm": 4.87}, "thickness": 1.0},
    },
    "side_swept": {
        "b": {
            "guide": "g_m2_b.glb", "concept": "c_m2_b", "concept_order": (0, 1, 2),
            "registration": {"scale": 0.20415, "offset": [0.0, -0.02985, 1.62209], "fit_rms_mm": 7.61}, "thickness": 1.0,
            # The concept's volume, not the guide's tall crown spikes; the
            # guide's undercut (low relief down the neck) ends at the ears'
            # lower level.
            "crown_flatten": {"h0_hcm": 2.0, "k": 0.4, "phi_max": 70.0},
            "relief_drop": [{"box": [-20, 20, -20, 3.0, -30, 3.0], "depth_m": 0.011}], "min_part": 1500,
            "segment": {"drop": [[-20, 20, -20, 2.0, -30, -3.2]]},
        },
        # The re-generation: pointed nape locks, no undercut, no tall spikes.
        "r": {"guide": "g_m2_r.glb", "concept": "c_m2_r", "concept_order": (0, 1, 2), "thickness": 1.0,
              "registration": {"scale": 0.23409, "offset": [0.0, -0.00614, 1.6206], "fit_rms_mm": 8.0},
              # The lock swept over the right eye lies on the guide's deeper
              # face: lifted over ours.
              "face_lift": {"clear_m": 0.004},
              # Many fine sculpted grooves: smoothed into larger shapes.
              "smooth_iters": 45,
              # Its short side locks lie on the ear: the build keeps them
              # further off it (the ears bend with the neck in the clips).
              "ear_gap": 0.009,
              # Thin pieces round the ears after the fit (the guide's ear rim,
              # sideburn bits round its smaller ear): dropped, not pushed out
              # into a crumple.
              "relief_drop": [{"box": [6.0, 13.0, -5.5, 3.0, -5.0, 3.5], "depth_m": 0.03},
                              {"box": [-13.0, -6.0, -5.5, 3.0, -5.0, 3.5], "depth_m": 0.03}],
              # The guide's nose stands out of our face; its neck is thicker
              # than ours (both read as hair by depth): skin below the nape
              # locks' tips and the side locks.
              "segment": {"eye_seeds": False,
                          "skin_seeds": [[-1.0, 1.0, 7.0, 30.0, -6.0, 0.0], [-30.0, 30.0, -2.0, 30.0, -40.0, -8.5],
                                         [-30.0, 30.0, -30.0, 30.0, -40.0, -11.0]],
                          "seed_free": [[-30.0, 30.0, -30.0, 30.0, -40.0, -4.0]],
                          # The guide's ears (not quite on ours): skin near
                          # the head, the hair over them stands further off.
                          "skin_near": [[6.0, 11.0, -3.5, 2.0, -4.0, 3.0], [-11.0, -6.0, -3.5, 2.0, -4.0, 3.0]],
                          "skin_near_m": 0.012}},
    },
    "shaggy_layers": {
        "a": {"guide": "g_m3_a.glb", "concept": "c_m3_a", "concept_order": (0, 2, 1),
              "registration": {"scale": 0.21584, "offset": [0.0, -0.00985, 1.59953], "fit_rms_mm": 4.34}, "thickness": 1.0,
              "segment": {"ear_seeds": False, "cheek_seeds": False,
                          "drop": [[4.5, 9.5, -6.0, 0.5, -8.5, -3.0], [-9.5, -4.5, -6.0, 0.5, -8.5, -3.0]]}},
    },
    "long_bangs": {
        "a": {
            "guide": "g_f1_a.glb", "concept": "c_f1_a", "concept_order": (0, 1, 2),
            "registration": {"scale": 0.30375, "offset": [0.0, 0.09392, 1.41878], "fit_rms_mm": 7.05}, "thickness": 1.0,
            "trunk_clear": 0.04, "arm_clear": 0.03, "back_flare": 0.18, "forward_bias": 3.0,
            "segment": {"ear_seeds": False, "cheek_x": [2.5, 5.0], "cheek_reach_m": 0.004,
                        "skin_seeds": [[-4.5, 4.5, 0.0, 30.0, -40.0, -18.0]],
                        "hair_seeds": [[-30.0, 30.0, -60.0, -9.5, -60.0, -14.0]],
                        "skin_near": [[10.0, 30.0, -7.0, 9.0, -40.0, -13.0], [-30.0, -10.0, -7.0, 9.0, -40.0, -13.0]]},
        },
        # The re-generation: a smooth mannequin head on a stand, bare
        # shoulders, the front locks hanging free of the chest.
        "r": {"guide": "g_f1_r.glb", "concept": "c_f1_r", "concept_order": (0, 1, 2), "thickness": 1.0,
              "registration": {"scale": 0.38418, "offset": [0.0, 0.07813, 1.33744], "fit_rms_mm": 6.71},
              # --register: the guide stands on a pole, so its head is a
              # smaller share of its height (the fit's scale search widened).
              "register_scales": (0.30, 0.72),
              # The hair rests on the shoulders, the chest and the back under
              # gravity (2026-11-06: it used to stand 4 cm off them, the back
              # flared out): below the shoulders each lock lies 8 mm off the
              # outline of the body, robe and shirt above it, and follows
              # the chest and back in the clips (`follow`).
              "trunk_clear": 0.008, "arm_clear": 0.02, "rest": {"clear": 0.01, "gather": 0.66, "gather_from": 0.0,
                                                                          "back_slope": 0.06, "blur": (3.0, 3.0)},
              # The front locks hang over the bust's wide cut, splaying out
              # to the sides: a quarter of that drift is kept.
              "straighten": 0.25,
              # The nose stands out of our face: skin.
              "segment": {"ear_seeds": False, "skin_seeds": [[-1.0, 1.0, 7.0, 30.0, -6.0, -1.0]]}},
    },
    "ponytail_locks": {
        "a": {
            "guide": "g_f2_a.glb", "concept": "c_f2_a", "concept_order": (0, 1, 2),
            "registration": {"scale": 0.27901, "offset": [0.0, 0.12982, 1.49416], "fit_rms_mm": 6.99}, "thickness": 1.0,
            "trunk_clear": 0.024, "arm_clear": 0.025,
            # The hair is combed flat to the head (weak creases): the scalp
            # above the ears and the tail behind the head are seeded as hair,
            # the bust in front of the tail as skin.
            "segment": {"cheek_seeds": False, "ear_reach_m": 0.004,
                        "skin_seeds": [[-40.0, 40.0, -8.0, 30.0, -60.0, -16.0], [-30.0, 30.0, -8.0, -2.0, -16.0, -3.0]],
                        "hair_seeds": [[-30.0, 30.0, -60.0, -11.0, -60.0, 40.0], [-30.0, 30.0, -30.0, 3.0, 4.0, 40.0],
                                       [-30.0, 30.0, -30.0, -4.0, -1.0, 40.0]],
                        "hair_noear": [[-30.0, 30.0, -9.0, 2.0, -2.0, 8.0]], "noear_m": 0.003},
            "tail_boxes": [[-30.0, 30.0, -80.0, -10.5, -80.0, 40.0]],
            "tie_boxes": [[-3.0, 3.0, -12.0, -7.0, 8.0, 13.0]],
        },
        # The re-generation: a mannequin bust, the combed-back hair standing
        # off the scalp.
        "r": {"guide": "g_f2_r.glb", "concept": "c_f2_r", "concept_order": (0, 2, 1), "thickness": 1.0,
              "registration": {"scale": 0.26525, "offset": [0.0, 0.13436, 1.51058], "fit_rms_mm": 7.77},
              "trunk_clear": 0.024, "arm_clear": 0.025,
              # Fine sculpted strands and wavy side tendrils: smoothed into
              # larger shapes.
              "smooth_iters": 30,
              # Thin wisps the fit leaves round and behind the ears: dropped
              # (the side locks in front of the ears stay).
              "relief_drop": [{"box": [6.0, 13.0, -6.0, 0.5, -4.0, 4.5], "depth_m": 0.025},
                              {"box": [-13.0, -6.0, -6.0, 0.5, -4.0, 4.5], "depth_m": 0.025}],
              # The hair combed back over the temples (above and in front of
              # the ears) is hair; the neck below the nape's hem and the side
              # locks is skin (its depth says nothing: the guide's neck is
              # thicker than ours).
              "segment": {"cheek_seeds": False, "noear_m": 0.003,
                          "hair_noear": [[5.0, 30.0, -3.0, 4.0, -1.0, 8.0], [-30.0, -5.0, -3.0, 4.0, -1.0, 8.0]],
                          "hair_seeds": [[5.0, 30.0, 1.5, 5.0, 1.0, 8.0], [-30.0, -5.0, 1.5, 5.0, 1.0, 8.0]],
                          "skin_seeds": [[-30.0, 30.0, -9.0, 0.0, -40.0, -6.5], [-4.5, 4.5, -9.0, 30.0, -40.0, -10.0],
                                         [-1.0, 1.0, 7.0, 30.0, -6.0, -1.0]],
                          "seed_free": [[-30.0, 30.0, -9.0, 5.0, -40.0, -3.0]]},
              # The tail: behind the head's back, below the nape behind the
              # neck, and its rise above the tie.
              "tail_boxes": [[-30.0, 30.0, -80.0, -13.2, -80.0, 40.0], [-30.0, 30.0, -80.0, -9.0, -80.0, -4.0],
                             [-30.0, 30.0, -80.0, -8.0, 15.5, 40.0]], "tail_far": {"f_max": -9.0, "h_hcm": 5.5},
              "tie_boxes": [[-3.5, 3.5, -13.0, -7.5, 11.0, 15.5]]},
    },
    "bob_locks": {
        "a": {"guide": "g_f3_a.glb", "concept": "c_f3_a", "concept_order": (0, 1, 2),
              "registration": {"scale": 0.20383, "offset": [0.0, 0.06964, 1.50864], "fit_rms_mm": 6.27}, "thickness": 1.0,
              "segment": {"ear_seeds": False, "cheek_seeds": False}},
    },
    "swept_undercut": {
        "a": {"guide": "g_m4_a.glb", "concept": "c_m4_a", "concept_order": (2, 0, 1), "thickness": 1.0,
              "registration": {"scale": 0.23795, "offset": [0.0, -0.00526, 1.60274], "fit_rms_mm": 5.18},
              # The clipped sides and nape are sculpted as low relief on the
              # guide's skin. The top ends on a clean line round the sides
              # and back (`cut_u`, head cm above the eyes; at the front it
              # runs to the hairline) and the clipped band (`clip`) covers
              # the rest; low relief at the front and round the ears goes.
              "relief_drop": [{"box": [-6.0, 6.0, -30.0, 30.0, -40.0, 6.0], "depth_m": 0.006},
                              {"box": [5.0, 14.0, -8.0, 4.0, -8.0, 4.0], "depth_m": 0.03},
                              {"box": [-14.0, -5.0, -8.0, 4.0, -8.0, 4.0], "depth_m": 0.03}],
              "cut_u": [[0.0, -20.0], [30.0, -20.0], [50.0, 3.0], [90.0, 1.5], [135.0, 0.0], [180.0, -1.0],
                        [225.0, 0.0], [270.0, 1.5], [310.0, 3.0], [330.0, -20.0]],
              "segment": {"skin_seeds": [[-30.0, 30.0, -30.0, 30.0, -40.0, -5.0]]},
              "hug": {"top": [[0.0, 28.0], [40.0, 26.0], [70.0, 18.0], [100.0, 12.0], [140.0, 10.0]],
                      "hem_mm": [[0.0, 6.0], [40.0, 6.0], [80.0, 8.0], [280.0, 8.0], [320.0, 6.0]],
                      "fade_hcm": 1.0, "detail": 1.3},
              "smooth_iters": 6, "proxy_share": 0.12,
              },
    },
    "low_tail": {
        "a": {"guide": "g_m5_a.glb", "concept": "c_m5_a", "concept_order": (1, 0, 2), "thickness": 1.0,
              "registration": {"scale": 0.21771, "offset": [0.0, -0.0196, 1.57679], "fit_rms_mm": 5.54},
              "segment": {"hair_seeds": [[-30.0, 30.0, -30.0, 30.0, 3.0, 40.0]],
                          "hair_off": [{"box": [-30.0, 30.0, -30.0, 30.0, -12.0, 3.0], "m": 0.004}],
                          "skin_near": [[-7.0, 7.0, -30.0, 30.0, -40.0, -5.0]], "skin_near_m": 0.006},
              "tail_boxes": [[-30.0, 30.0, -80.0, -9.0, -80.0, -2.0]]},
    },
    "crew_cut": {
        "a": {"guide": "g_m6_a.glb", "concept": "c_m6_a", "concept_order": (1, 0, 2), "thickness": 1.0,
              "registration": {"scale": 0.25013, "offset": [0.0, -0.01474, 1.585], "fit_rms_mm": 5.87},
              # The clipped back and sides are a thin shell on the guide's
              # skull (kept: short hair); the neck below the nape's hairline
              # and the cheeks in front of the ears are skin.
              "segment": {"skin_seeds": [[-30.0, 30.0, -30.0, 30.0, -40.0, -6.0], [4.0, 13.0, 1.0, 30.0, -40.0, 0.5],
                                         [-13.0, -4.0, 1.0, 30.0, -40.0, 0.5]]},
              # The thin shell round the ears and below the nape's hairline
              # (pushed off the ears and neck it flares out): dropped.
              "relief_drop": [{"box": [5.5, 14.0, -6.0, 4.0, -8.0, 3.0], "depth_m": 0.03},
                              {"box": [-14.0, -5.5, -6.0, 4.0, -8.0, 3.0], "depth_m": 0.03},
                              {"box": [-30.0, 30.0, -30.0, 30.0, -40.0, -4.0], "depth_m": 0.05}],
              "hug": {"top": [[0.0, 13.0], [50.0, 12.0], [80.0, 9.0], [110.0, 7.0], [140.0, 6.0]],
                      "hem_mm": 6.0, "fade_hcm": 1.0, "detail": 0.55, "floor_mm": 6.0},
              "proxy_share": 0.4,
              },
    },
    "twin_tails": {
        "a": {"guide": "g_f4_a.glb", "concept": "c_f4_a", "concept_order": (1, 0, 2), "thickness": 1.0,
              "registration": {"scale": 0.28272, "offset": [0.0, 0.07492, 1.42753], "fit_rms_mm": 7.95},
              "trunk_clear": 0.03, "arm_clear": 0.03,
              # Bangs and side locks lie close over the face: hair where
              # they stand off ours; the nose, the neck and the bust skin.
              "segment": {"hair_seeds": [[-30.0, 30.0, -30.0, 30.0, 3.0, 40.0]],
                          "hair_off": [{"box": [4.5, 30.0, -30.0, 30.0, -9.0, 3.0], "m": 0.005},
                                       {"box": [-30.0, -4.5, -30.0, 30.0, -9.0, 3.0], "m": 0.005},
                                       {"box": [-4.5, 4.5, -30.0, 30.0, -1.0, 3.0], "m": 0.004}],
                          "skin_seeds": [[-1.5, 1.5, 5.0, 30.0, -7.0, -1.0], [-7.0, 7.0, -30.0, 30.0, -60.0, -6.0]]},
              "tail_boxes": [[11.0, 40.0, -30.0, 30.0, 0.0, 14.0], [-40.0, -11.0, -30.0, 30.0, 0.0, 14.0],
                             [9.5, 40.0, -30.0, 30.0, -80.0, 0.0], [-40.0, -9.5, -30.0, 30.0, -80.0, 0.0]]},
    },
    "side_braid": {
        # "b": the re-generated concept (the first, c_f5_a, drew two of its
        # three views empty).
        "b": {"guide": "g_f5_b.glb", "concept": "c_f5_b", "concept_order": (0, 1, 2), "thickness": 1.0,
              "registration": {"scale": 0.27862, "offset": [0.0, 0.07812, 1.4277], "fit_rms_mm": 8.32},
              "trunk_clear": 0.012, "arm_clear": 0.02,
              # A scrap of the guide's bust edge the drape leaves beside the
              # upper arm.
              "drop": [{"parts": [CROWN, FRINGE, SIDE, BACK, TAIL, TIE], "box": [10.5, 40.0, -30.0, 30.0, -60.0, -26.0]}],
              # As twin_tails, plus the braid down the left of the chest
              # (standing well off it).
              "segment": {"ear_seeds": False,
                          "hair_seeds": [[-30.0, 30.0, -30.0, 30.0, 3.0, 40.0]],
                          "hair_off": [{"box": [4.5, 30.0, -30.0, 30.0, -9.0, 3.0], "m": 0.005},
                                       {"box": [-30.0, -4.5, -30.0, 30.0, -9.0, 3.0], "m": 0.005},
                                       {"box": [-4.5, 4.5, -30.0, 30.0, -1.0, 3.0], "m": 0.004},
                                       {"box": [1.0, 16.0, -2.0, 30.0, -60.0, -12.0], "m": 0.012},
                                       {"box": [3.0, 13.0, -12.0, 1.0, -13.0, -4.0], "m": 0.008}],
                          # The braid lies on the guide's chest (inside ours)
                          # and down its nape: where it stands out of the
                          # guide's own surface.
                          "protrude": [{"box": [1.0, 13.0, 0.0, 30.0, -27.0, -17.0], "axis": "f", "h_hcm": 1.5},
                                       {"box": [3.0, 13.0, -4.0, 30.0, -17.0, -8.0], "axis": "f", "h_hcm": 0.7},
                                       {"box": [-2.0, 13.0, -30.0, -2.0, -16.0, -3.0], "axis": "-f", "h_hcm": 1.2},
                                       {"box": [2.0, 14.0, -7.0, 7.0, -17.0, -6.0], "axis": "u", "h_hcm": 1.2}],
                          "skin_seeds": [[-1.5, 1.5, 5.0, 30.0, -7.0, -1.0], [-7.0, 4.0, -30.0, 30.0, -60.0, -6.0],
                                         [-30.0, 30.0, -30.0, -2.0, -60.0, -9.0], [11.0, 40.0, -30.0, 30.0, -60.0, -15.0],
                                         [-40.0, -11.0, -30.0, 30.0, -60.0, -15.0], [-30.0, 30.0, -30.0, 30.0, -60.0, -27.0]],
                          "drop": [[10.0, 40.0, -30.0, 30.0, -60.0, -20.0]]},
              # The plait (2026-11-06: the guide's braid was flat, cut from
              # the bust, and torn from the head at the nape): the guide's
              # braid dropped from the nape down, a three-strand plait built
              # from the nape over the left shoulder, resting on the chest
              # (prepare_hair_meshy.add_braid, charlib/hair_braid.py).
              # (and a scrap of the guide lying on the left jaw)
              "braid": {"drop": [[1.0, 40.0, -2.0, 30.0, -80.0, -10.0], [-3.0, 16.0, -16.0, -1.0, -40.0, -6.0],
                                 [2.0, 11.0, -1.0, 12.0, -14.0, -5.0]],
                        # Round 4 (2026-11-07, after a reference photo of a
                        # real braid from behind): the hair over the head combed
                        # sleek and close, fine flow lines converging on one
                        # gathering point at the centre of the base of the
                        # skull (as in the photo); the plait starts there
                        # and curves over the left shoulder onto the chest.
                        "path": [[0.0, -7.6, -6.4], [1.4, -7.4, -8.8], [4.6, -6.0, -10.8], [7.4, -3.4, -12.3],
                                 [8.8, 0.5, -13.8], [8.0, 4.0, -17.0], [7.5, 7.0, -22.0], [7.2, 8.0, -27.0],
                                 [7.2, 8.0, -31.0]],
                        "chest_u": -13.5, "half_width": 0.012, "half_depth": 0.0048, "strand_a": 0.0105,
                        "strand_b": 0.0085, "period": 0.125, "clear": 0.008, "neck_clear": 0.006, "tuft_len": 0.07,
                        # The plait starts a little wide and slack at the
                        # gathering and tightens over its first crossings.
                        "root_open": 1.35, "root_swell": 1.2, "root_len": 0.05,
                        # The head's hair behind the ears ends on a line
                        # dipping to the gathering point at the centre (no
                        # straight hem); a smooth sheet runs from it into
                        # the plait's first crossing.
                        "loft": {"f_cut": 0.5, "u_cut": -5.6, "x_rise": 0.45, "u_rise": 0.0, "u_top": -1.0, "into_m": 0.02,
                                 "spread": 0.85, "rows": 9, "clear": 0.006},
                        "sleek": {"to": [0.0, -7.6, -6.4], "f_front": 0.5, "f_soft": 3.0, "smooth": 40, "k": 0.92,
                                  "floor": 0.3, "lines": 24, "sharp": 4, "shade": 0.55, "fade_in": 2.0},
                        "budget": 20000}},
    },
}


def style(name: str, guide_id: str | None = None) -> dict:
    """A style's data with one of its guides (default: the style's
    `guide_id`)."""
    base = _COMMON[name]
    gid = guide_id or base["guide_id"]
    return {**base, **GUIDES[name][gid], "guide_id": gid}


def source_style(name: str, meta: dict) -> dict:
    """The style data a source mesh was prepared with (its meta's guide)."""
    gid = meta.get("guide_id")
    if gid is None:
        gid = next((g for g, d in GUIDES[name].items() if d["guide"] == meta.get("source")), None)
    return style(name, gid)


STYLES: dict[str, dict] = {name: style(name) for name in _COMMON}


def source_dir() -> str:
    from . import content

    return os.environ.get("SHS_HAIR_MESHY", os.path.join(content.REPO, "art", "characters", "hair_meshy"))


def source_path(name: str, body: str, root: str | None = None) -> str:
    return os.path.join(root or source_dir(), f"{name}_{body}.npz")


def save_source(path: str, V, F, N, part, rtt, meta: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path, V=np.asarray(V, dtype=np.float32), F=np.asarray(F, dtype=np.int32),
                        N=np.asarray(N, dtype=np.float32), part=np.asarray(part, dtype=np.uint8),
                        root_to_tip=np.asarray(rtt, dtype=np.float32), meta=np.array(json.dumps(meta, sort_keys=True)))


def load_source(path: str) -> dict:
    with np.load(path, allow_pickle=False) as z:
        out = {k: z[k] for k in ("V", "F", "N", "part", "root_to_tip")}
        out["meta"] = json.loads(str(z["meta"]))
    out["V"] = out["V"].astype(np.float64)
    out["N"] = out["N"].astype(np.float64)
    out["F"] = out["F"].astype(np.int64)
    out["root_to_tip"] = out["root_to_tip"].astype(np.float64)
    return out


def use_meshy(name: str, body: str, source: str | None = None) -> bool:
    """Whether a style builds from its Meshy source mesh: the style's
    `source` (or the override) is "meshy" and the source mesh exists; a
    MESHY_ONLY style ignores a "locks" override (it has no locks)."""
    st = STYLES.get(name)
    if st is None or st["body"] != body:
        return False
    want = "meshy" if name in MESHY_ONLY else (source or st.get("source", "meshy"))
    return want == "meshy" and os.path.exists(source_path(name, body))


# `registration`: the guide onto the body's head (measure_hair_guides.py's fit,
# recorded; prepare_hair_meshy.py --register fits again).

# ----------------------------------------------------------------------------
# Head-frame helpers
# ----------------------------------------------------------------------------


def head_coords(fr, P: np.ndarray) -> np.ndarray:
    d = (np.asarray(P) - fr.centre) / fr.hcm
    return np.stack([d[:, 0], -d[:, 1], d[:, 2]], axis=1)


def in_box(H: np.ndarray, box) -> np.ndarray:
    x0, x1, f0, f1, u0, u1 = box
    return (H[:, 0] >= x0) & (H[:, 0] <= x1) & (H[:, 1] >= f0) & (H[:, 1] <= f1) & (H[:, 2] >= u0) & (H[:, 2] <= u1)


def _smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, dtype=np.float64) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ----------------------------------------------------------------------------
# Labels (prepare)
# ----------------------------------------------------------------------------


def label_parts(fr, st: dict, V: np.ndarray, F: np.ndarray) -> np.ndarray:
    """Part per vertex (PARTS) from the head frame: the fringe in front of
    the forehead, sides beside the face, the back, the crown above; the tail
    and its tie from the style's boxes; then a mode filter."""
    H = head_coords(fr, V)
    phi, theta, _ = fr.polar(V)
    th = (theta + 180.0) % 360.0 - 180.0  # -180..180, 0 = the face
    brow_u = (fr.brow_z - fr.centre[2]) / fr.hcm
    ear_top_u = ((fr.ear["top"] if fr.ear else fr.eye_z) - fr.centre[2]) / fr.hcm
    lab = np.full(len(V), CROWN, dtype=np.int64)
    fr_lim = st.get("fringe_theta", 52.0)
    lab[(np.abs(th) <= fr_lim) & (H[:, 2] < brow_u + st.get("fringe_above_brow", 3.0))] = FRINGE
    side = (np.abs(th) > fr_lim) & (np.abs(th) < 132.0) & (H[:, 2] < ear_top_u + 1.0)
    lab[side] = SIDE
    lab[(np.abs(th) >= 132.0) & (H[:, 2] < ear_top_u + 1.0)] = BACK
    # Long hair below the chin in front of the shoulders: side locks.
    chin_u = (fr.chin_z - fr.centre[2]) / fr.hcm
    lab[(H[:, 2] < chin_u) & (H[:, 1] > st.get("front_lock_f", 1.0))] = SIDE
    for box in st.get("tail_boxes", []):
        lab[in_box(H, box)] = TAIL
    # A tail standing off the head: behind `f_max` and more than `h_hcm`
    # over the skull (the head's own hair lies closer).
    far = st.get("tail_far")
    if far:
        ro = fr.skull_r(phi, theta)
        R = np.linalg.norm(np.asarray(V) - fr.centre, axis=1)
        lab[(H[:, 1] < far["f_max"]) & ((R - ro) / fr.hcm > far["h_hcm"])] = TAIL
    for box in st.get("tie_boxes", []):
        lab[in_box(H, box)] = TIE
    E = MO.edges(F)
    keep = (lab == TAIL) | (lab == TIE)
    out = MO.majority(lab, E, passes=2, nlab=len(PARTS))
    out[keep] = lab[keep]
    if st.get("no_fringe"):
        # Combed back or cropped: the short pieces at the hairline are the
        # crown's (no fringe hem, no fringe shadow).
        out[out == FRINGE] = CROWN
    return out.astype(np.uint8)


def root_to_tip(fr, V: np.ndarray, F: np.ndarray, part: np.ndarray, crown_phi: float = 14.0) -> np.ndarray:
    """0 at the crown (where the hair grows from) to 1 at the tips: the
    geodesic distance from the crown over the surface, over its 97th
    percentile within the part (a tail over the tail's), clipped."""
    E = MO.edges(F)
    indptr, indices = MO.csr(len(V), E)
    phi, _, _ = fr.polar(V)
    src = phi < crown_phi
    if not src.any():
        src = phi <= np.percentile(phi, 1)
    d = MO.dijkstra(V, indptr, indices, src)
    if np.isinf(d).any():
        d[np.isinf(d)] = np.nanmax(d[~np.isinf(d)])
    # A tail grows from its tie: its own geodesic from the tie, over the tail.
    tail = (part == TAIL) | (part == TIE)
    if (part == TAIL).any():
        keep = tail[F].all(axis=1)
        Vt, Ft, idx = MO.submesh(V, F, keep)
        Et = MO.edges(Ft)
        ip, ii = MO.csr(len(Vt), Et)
        src_t = part[idx] == TIE
        if not src_t.any():
            src_t = d[idx] <= np.percentile(d[idx], 2)
        dt = MO.dijkstra(Vt, ip, ii, src_t)
        dt[np.isinf(dt)] = np.nanmax(dt[~np.isinf(dt)]) if (~np.isinf(dt)).any() else 0.0
        dd = np.zeros(len(V))
        dd[idx] = dt
        d = np.where(tail, dd, d)
    out = np.zeros(len(V))
    for p in np.unique(part):
        m = part == p
        L = max(float(np.percentile(d[m], 97)), 1e-6)
        out[m] = np.clip(d[m] / L, 0.0, 1.0)
    out[part == TIE] = 0.0
    return out


# ----------------------------------------------------------------------------
# Smooth shading normals
# ----------------------------------------------------------------------------

ENV_STEP = 4.0  # degrees (spherical) / of azimuth (cylindrical)
ENV_DZ = 0.01  # metres per cylindrical row


def _blur(G: np.ndarray, passes: int, wrap_axis: int = 1) -> np.ndarray:
    g = G.copy()
    for _ in range(passes):
        g = 0.5 * g + 0.25 * (np.roll(g, 1, axis=wrap_axis) + np.roll(g, -1, axis=wrap_axis))
        up = np.concatenate([g[:1], g[:-1]], axis=0)
        dn = np.concatenate([g[1:], g[-1:]], axis=0)
        g = 0.5 * g + 0.25 * (up + dn)
    return g


def _bilinear(G: np.ndarray, fi: np.ndarray, fj: np.ndarray) -> np.ndarray:
    i0 = np.clip(np.floor(fi).astype(int), 0, G.shape[0] - 2)
    j0 = np.floor(fj).astype(int) % G.shape[1]
    j1 = (j0 + 1) % G.shape[1]
    a = np.clip(fi - i0, 0.0, 1.0)
    b = fj - np.floor(fj)
    return (1 - a) * (1 - b) * G[i0, j0] + a * (1 - b) * G[i0 + 1, j0] + (1 - a) * b * G[i0, j1] + a * b * G[i0 + 1, j1]


def envelope_normals(fr, V: np.ndarray, line_z: float, passes: int = 9) -> np.ndarray:
    """Normals of a smooth closed proxy of the hair mass: above `line_z`
    the outer envelope seen from the head centre (the largest radius per
    direction, blurred over the sphere), below it the outer envelope round
    the head's vertical axis (blurred over azimuth and height), blended over
    2 cm."""
    c = fr.centre
    # Spherical envelope.
    phi, theta, R = fr.polar(V)
    nph, nth = int(180 / ENV_STEP) + 1, int(360 / ENV_STEP)
    G = np.zeros((nph, nth))
    ip = np.clip(np.round(phi / ENV_STEP).astype(int), 0, nph - 1)
    jt = np.round(theta / ENV_STEP).astype(int) % nth
    above = V[:, 2] > line_z - 0.03
    np.maximum.at(G, (ip[above], jt[above]), R[above])
    P, T = np.meshgrid(np.arange(nph) * ENV_STEP, np.arange(nth) * ENV_STEP, indexing="ij")
    skull = fr.skull_r(P, T)
    G = np.where(G > 0, np.maximum(G, skull), skull + 0.004)
    G = _blur(G, passes)
    dGp = np.gradient(G, math.radians(ENV_STEP), axis=0)
    dGt = (np.roll(G, -1, axis=1) - np.roll(G, 1, axis=1)) / (2 * math.radians(ENV_STEP))
    fi, fj = phi / ENV_STEP, theta / ENV_STEP
    Rv = _bilinear(G, fi, fj)
    Rp = _bilinear(dGp, fi, fj)
    Rt = _bilinear(dGt, fi, fj)
    p, t = np.radians(phi), np.radians(theta)
    d = np.stack([np.sin(p) * np.sin(t), -np.sin(p) * np.cos(t), np.cos(p)], axis=1)
    ph = np.stack([np.cos(p) * np.sin(t), -np.cos(p) * np.cos(t), -np.sin(p)], axis=1)
    thh = np.stack([np.cos(t), np.sin(t), np.zeros_like(t)], axis=1)
    sp = np.maximum(np.sin(p), 0.15)
    n_sph = d - (Rp / Rv)[:, None] * ph - (Rt / (Rv * sp))[:, None] * thh
    # Cylindrical envelope (hanging hair).
    x, f = V[:, 0] - c[0], -(V[:, 1] - c[1])
    rho = np.hypot(x, f)
    tc = np.degrees(np.arctan2(x, f)) % 360.0
    z0 = float(V[:, 2].min()) - 0.02
    nz = int(math.ceil((line_z + 0.05 - z0) / ENV_DZ)) + 1
    C = np.zeros((nz, nth))
    iz = np.clip(np.round((V[:, 2] - z0) / ENV_DZ).astype(int), 0, nz - 1)
    jc = np.round(tc / ENV_STEP).astype(int) % nth
    np.maximum.at(C, (iz, jc), rho)
    # Empty cells: the nearest filled row of the same column, else the column mean.
    for j in range(nth):
        col = C[:, j]
        ok = col > 0
        if ok.any():
            C[:, j] = np.interp(np.arange(nz), np.nonzero(ok)[0], col[ok])
    empty = ~(C > 0).any(axis=0)
    if empty.any():
        C[:, empty] = C[:, ~empty].mean(axis=1, keepdims=True) if (~empty).any() else 0.05
    C = _blur(C, passes)
    dCz = np.gradient(C, ENV_DZ, axis=0)
    dCt = (np.roll(C, -1, axis=1) - np.roll(C, 1, axis=1)) / (2 * math.radians(ENV_STEP))
    fz, fj2 = (V[:, 2] - z0) / ENV_DZ, tc / ENV_STEP
    Cv = np.maximum(_bilinear(C, fz, fj2), 1e-3)
    Cz = _bilinear(dCz, fz, fj2)
    Ct = _bilinear(dCt, fz, fj2)
    a = np.radians(tc)
    rh = np.stack([np.sin(a), -np.cos(a), np.zeros_like(a)], axis=1)
    ah = np.stack([np.cos(a), np.sin(a), np.zeros_like(a)], axis=1)
    n_cyl = rh - (Ct / Cv)[:, None] * ah - Cz[:, None] * np.array([0.0, 0.0, 1.0])
    n_sph /= np.maximum(np.linalg.norm(n_sph, axis=1, keepdims=True), 1e-12)
    n_cyl /= np.maximum(np.linalg.norm(n_cyl, axis=1, keepdims=True), 1e-12)
    w = _smoothstep(line_z + 0.01, line_z - 0.03, V[:, 2])[:, None]
    n = n_sph * (1 - w) + n_cyl * w
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def centreline(V: np.ndarray, t: np.ndarray, n: int) -> np.ndarray:
    """Centre line of a tube-like set of points ordered by `t` (0..1):
    centroids of `n` slices."""
    out = []
    edges_ = np.linspace(0.0, 1.0, n + 1)
    for a, b in zip(edges_[:-1], edges_[1:]):
        m = (t >= a) & (t <= b)
        if m.any():
            out.append(V[m].mean(axis=0))
    return np.array(out)


def tube_normals(V: np.ndarray, line: np.ndarray) -> np.ndarray:
    """Normals pointing away from a polyline (a tail's smooth proxy)."""
    best = np.full(len(V), np.inf)
    foot = np.zeros_like(V)
    for a, b in zip(line[:-1], line[1:]):
        ab = b - a
        t = np.clip(((V - a) @ ab) / max(float(ab @ ab), 1e-12), 0.0, 1.0)
        q = a + t[:, None] * ab
        d = np.linalg.norm(V - q, axis=1)
        m = d < best
        best[m] = d[m]
        foot[m] = q[m]
    n = V - foot
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def shading_normals(fr, V: np.ndarray, F: np.ndarray, part: np.ndarray, rtt: np.ndarray, line_z: float,
                    proxy_share: float | None = None) -> np.ndarray:
    """PROXY_SHARE (or a style's `proxy_share`) of the smooth proxy plus
    the rest of the mesh's own (lightly smoothed) normal; the lock tips keep
    more of their own. A lower share lets sculpted grooves shade (combed
    clumps)."""
    E = MO.edges(F)
    own = MO.smooth_values(MO.vertex_normals(V, F), E, 1)
    own /= np.maximum(np.linalg.norm(own, axis=1, keepdims=True), 1e-12)
    prox = envelope_normals(fr, V, line_z)
    tail = (part == TAIL) | (part == TIE)
    if tail.any() and tail.sum() > 50:
        line = centreline(V[tail], rtt[tail], 12)
        if len(line) >= 2:
            prox[tail] = tube_normals(V[tail], line)
    ps = PROXY_SHARE if proxy_share is None else proxy_share
    k = ps + (min(TIP_PROXY_SHARE, ps) - ps) * _smoothstep(0.8, 0.95, rtt)
    n = prox * k[:, None] + own * (1 - k[:, None])
    n = MO.smooth_values(n, E, 1)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


# ----------------------------------------------------------------------------
# Clearance from the body
# ----------------------------------------------------------------------------


GARMENTS = ("gear/body/robe_school_house_a", "gear/body/shirt_oxford_tie_a")  # worn under hair (check_hair.py)
GARMENT_CLEAR = 0.006  # metres every hair vertex keeps from them


def garment_parts(fr) -> list:
    """(vertices, triangles) of the shipped outfit's garments worn under the
    hair (the school robe and the shirt) in the body file, rest pose; cached
    on the head frame. Empty when the file has none."""
    if hasattr(fr, "_garments"):
        return fr._garments
    import bpy

    from . import hair_frame as HF

    out = []
    for o in bpy.data.objects:
        if o.type == "MESH" and o.get("asset") in GARMENTS and o.get("lod") == 0 and not o.get("region"):
            out.append(HF.world_mesh(o))
    fr._garments = out
    fr._garment_trees = [HF.bvh_of([g]) for g in out]
    return out


def push_out(fr, P: np.ndarray, covers_ears: bool, trunk_clear: float, head_clear: float = 0.003,
             arm_clear: float = 0.0, rounds: int = 3, garments: bool = False) -> np.ndarray:
    """Every point out of the visible body: `head_clear` on the head and
    face, `trunk_clear` below the chin (hair.clear_vertices' rule), and below
    the chin out of the robe and shirt worn under the hair too."""
    trees = [t for r, t in fr.region_trees.items() if not (covers_ears and r == "ears")]
    trees.append(fr.collide_noears if covers_ears else fr.collide)
    if not covers_ears:
        trees.append(fr.check_tree)
    gtrees = []
    if garments:
        garment_parts(fr)
        gtrees = fr._garment_trees
    out = P.copy()
    for _ in range(rounds):
        moved = 0
        for i in range(len(out)):
            p = out[i]
            low = p[2] <= fr.chin_z + 0.01
            c = trunk_clear if low else head_clear
            # The robe's collar and hood reach above the chin at the back:
            # every vertex keeps GARMENT_CLEAR from them at least.
            for g in gtrees:
                cg = max(c, GARMENT_CLEAR)
                loc, nrm, _, dist = g.find_nearest(Vector(tuple(p)), cg + 0.025)
                if loc is None:
                    continue
                h = float((p - np.array(loc)) @ np.array(nrm))
                if h < 0 and -h < 0.5 * dist:
                    continue
                if h < cg:
                    p = p + np.array(nrm) * min(cg - h, 0.03)
                    moved += 1
            for tree in trees:
                loc, nrm, _, dist = tree.find_nearest(Vector(tuple(p)), c + 0.025)
                if loc is None:
                    continue
                h = float((p - np.array(loc)) @ np.array(nrm))
                # Inside only when the offset runs along the nearest face's
                # normal (beside an open rim or a thin part the nearest face
                # can be its far side); never pushed more than 3 cm.
                if h < 0 and -h < 0.5 * dist:
                    continue
                if h < c:
                    p = p + np.array(nrm) * min(c - h, 0.03)
                    moved += 1
            if arm_clear and fr.arms is not None:
                p = fr.clear(p, arm_clear, fr.arms, reach=arm_clear + 0.02)
            out[i] = p
        if not moved:
            break
    return out



# ----------------------------------------------------------------------------
# Decimation (Blender's quadric collapse)
# ----------------------------------------------------------------------------


def decimate(V: np.ndarray, F: np.ndarray, target: int, protect: np.ndarray | None = None,
             factor: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """At most `target` triangles by Blender's collapse decimation (quadric
    error); `protect` (0..1 per vertex) makes those vertices costlier to
    collapse (tips, the open hem). Deterministic for the same input."""
    import bpy

    if len(F) <= target:
        return V.copy(), F.copy()
    me = bpy.data.meshes.new("_hm_dec")
    me.from_pydata(V.tolist(), [], F.tolist())
    me.update()
    o = bpy.data.objects.new("_hm_dec", me)
    bpy.context.scene.collection.objects.link(o)
    mod = o.modifiers.new("dec", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.use_collapse_triangulate = True
    if protect is not None:
        g = o.vertex_groups.new(name="protect")
        w = np.clip(np.asarray(protect, dtype=np.float64), 0.0, 1.0)
        for val in np.unique(np.round(w, 2)):
            idx = np.nonzero(np.round(w, 2) == val)[0].tolist()
            if idx:
                g.add(idx, float(val), "REPLACE")
        # Weight 1 collapses freely, 0 costs extra (Blender's collapse), so
        # the protection is inverted.
        mod.vertex_group = "protect"
        mod.invert_vertex_group = True
        mod.vertex_group_factor = factor
    ratio = target / len(F)
    best = None
    try:
        for _ in range(16):
            mod.ratio = float(np.clip(ratio, 1e-4, 1.0))
            dg = bpy.context.evaluated_depsgraph_get()
            ev = o.evaluated_get(dg)
            m2 = ev.to_mesh()
            m2.calc_loop_triangles()
            nt = len(m2.loop_triangles)
            co = np.zeros(len(m2.vertices) * 3)
            m2.vertices.foreach_get("co", co)
            tri = np.zeros(nt * 3, dtype=np.int64)
            m2.loop_triangles.foreach_get("vertices", tri)
            ev.to_mesh_clear()
            if nt <= target:
                if best is None or nt > len(best[1]):
                    best = (co.reshape(-1, 3), tri.reshape(-1, 3))
                if nt >= 0.97 * target:
                    break
            ratio = mod.ratio * (target / max(nt, 1)) * (0.995 if nt > target else 1.0)
    finally:
        bpy.data.objects.remove(o)
        bpy.data.meshes.remove(me)
    if best is None and protect is not None:
        # The held tips stall the collapse this far down: decimate freely.
        return decimate(V, F, target, None)
    if best is None:
        raise ValueError(f"decimation could not reach {target} triangles")
    Vd, Fd = best
    # Drop unused vertices and degenerate triangles.
    Fd = Fd[(Fd[:, 0] != Fd[:, 1]) & (Fd[:, 1] != Fd[:, 2]) & (Fd[:, 0] != Fd[:, 2])]
    Vd, Fd, _ = MO.submesh(Vd, Fd, np.ones(len(Fd), dtype=bool))
    return Vd, Fd


def closed_shell(V: np.ndarray, F: np.ndarray, thickness: float = 0.006,
                 voxel: float = 0.005) -> tuple[np.ndarray, np.ndarray]:
    """The open shell as a closed thin solid (Solidify, then a voxel remesh),
    triangulated: for the far LODs only."""
    import bpy

    me = bpy.data.meshes.new("_hm_closed")
    me.from_pydata(V.tolist(), [], F.tolist())
    me.update()
    o = bpy.data.objects.new("_hm_closed", me)
    bpy.context.scene.collection.objects.link(o)
    so = o.modifiers.new("solid", "SOLIDIFY")
    so.thickness = thickness
    so.offset = 0.0
    rm = o.modifiers.new("remesh", "REMESH")
    rm.mode = "VOXEL"
    rm.voxel_size = voxel
    try:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = o.evaluated_get(dg)
        m2 = ev.to_mesh()
        m2.calc_loop_triangles()
        co = np.zeros(len(m2.vertices) * 3)
        m2.vertices.foreach_get("co", co)
        tri = np.zeros(len(m2.loop_triangles) * 3, dtype=np.int64)
        m2.loop_triangles.foreach_get("vertices", tri)
        ev.to_mesh_clear()
    finally:
        bpy.data.objects.remove(o)
        bpy.data.meshes.remove(me)
    return co.reshape(-1, 3), tri.reshape(-1, 3)


def decimate_or_thin(V: np.ndarray, F: np.ndarray, target: int, protect: np.ndarray | None = None,
                     factor: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """decimate; when the collapse stalls above `target` (many separate
    strands, each needing a few triangles: twin tails at LOD2), the smallest
    separate pieces go first, a tenth of them at a time, until it reaches it."""
    while True:
        try:
            return decimate(V, F, target, protect, factor)
        except ValueError:
            comp = MO.components(len(V), MO.edges(F))
            ids, cnt = np.unique(comp[F[:, 0]], return_counts=True)
            if len(ids) <= 3 or cnt.min() > 0.05 * len(F):
                # A few big sheets whose long open hems stall the collapse
                # (twin tails' strands at LOD2): closed as a thin solid
                # (voxel remesh), which collapses freely.
                Vc, Fc = closed_shell(V, F)
                try:
                    return decimate(Vc, Fc, target, None)
                except ValueError:
                    # Still too many (a small cap variant's last few
                    # pieces): the smallest goes, as below.
                    if len(ids) <= 1:
                        raise
            drop = ids[np.argsort(cnt, kind="stable")[:max(1, len(ids) // 10)]]
            keep = ~np.isin(comp, drop)
            V, F, sub = MO.submesh(V, F, keep[F].all(axis=1))
            if protect is not None:
                protect = np.asarray(protect)[sub]


def tip_weight(V: np.ndarray, F: np.ndarray, rtt: np.ndarray | None = None) -> np.ndarray:
    """0..1: how much a vertex is a lock tip (sharp convex points) or on the
    open hem; held by smoothing and decimation."""
    E = MO.edges(F)
    curv = MO.mean_curvature(V, F, E)
    sharp = np.clip(-curv / max(float(np.percentile(-curv[curv < 0], 97)) if (curv < 0).any() else 1.0, 1e-9), 0, 1)
    sharp = MO.smooth_values(sharp, E, 1)
    w = np.maximum(sharp, MO.boundary_vertices(F, len(V)).astype(np.float64))
    if rtt is not None:
        w = np.maximum(w, _smoothstep(0.9, 1.0, rtt))
    return np.clip(w, 0.0, 1.0)


# ----------------------------------------------------------------------------
# Build: source clean-up (pass 2, character-look.md section 2)
# ----------------------------------------------------------------------------

MIN_PIECE_AREA = 1.5e-4  # m² (1.5 cm²): separate pieces smaller than this (flyaways) are dropped
HOLE_PERIMETER = 0.03  # m: a closed open-edge loop shorter than this is a hole in the shell, filled
STRAND_MIN_W = 0.003  # m: strands narrower than this are widened to it
STRAND_TIP_SPAN = 3.0  # x STRAND_MIN_W: the edge's other side nearer than this along the edge is the same tip (kept pointed)
WIDEN_MAX = 0.0015  # m: at most this outward per edge vertex
RELAX_ITERS = 4  # Taubin steps relaxing the triangles behind a widened edge (no shrinking)


def boundary_loops(F: np.ndarray) -> list[np.ndarray]:
    """The open edge as loops of vertices, in the faces' winding (directed
    boundary edges a -> b of the faces); at a vertex where two loops touch,
    each outgoing edge is used once."""
    D = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    key = np.sort(D, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    B = D[cnt[inv.ravel()] == 1]
    out_e: dict[int, list[int]] = {}
    for i, (a, _) in enumerate(B):
        out_e.setdefault(int(a), []).append(i)
    used = np.zeros(len(B), dtype=bool)
    loops = []
    for s in range(len(B)):
        if used[s]:
            continue
        loop = []
        e = s
        while e is not None and not used[e]:
            used[e] = True
            loop.append(int(B[e, 0]))
            nxt = [k for k in out_e.get(int(B[e, 1]), []) if not used[k]]
            e = nxt[0] if nxt else None
        loops.append(np.array(loop, dtype=np.int64))
    return loops


def _keep_vertices(src: dict, keep_faces: np.ndarray) -> dict:
    V, F, idx = MO.submesh(src["V"], src["F"], keep_faces)
    out = dict(src)
    out.update({"V": V, "F": F, "N": src["N"][idx], "part": src["part"][idx], "root_to_tip": src["root_to_tip"][idx]})
    return out


def style_edits(fr, src: dict, st: dict) -> dict:
    """The style's build edits: `drop` (faces with a vertex of the listed
    parts inside the head-frame box go; with `piece`, the whole separate
    piece touching it), `move` (parts shifted, the shift fading into their
    neighbours) and `shorten` (the listed parts below
    `u0` head cm drawn up smoothly: a depth d below u0 becomes span x
    tanh(d / span), so the hair just below u0 stays and the longest tips end
    at most `span` below it)."""
    out = src
    for rule in st.get("drop", []):
        H = head_coords(fr, out["V"])
        hit = in_box(H, rule["box"]) & np.isin(out["part"], rule["parts"])
        if hit.any() and rule.get("piece"):
            # The whole separate piece any of whose vertices is in the box.
            comp = MO.components(len(out["V"]), MO.edges(out["F"]))
            hit = np.isin(comp, np.unique(comp[hit]))
        if hit.any():
            out = _keep_vertices(out, ~hit[out["F"]].any(axis=1))
    for rule in st.get("shorten", []):
        V = out["V"].copy()
        m = np.isin(out["part"], rule["parts"])
        u0 = fr.centre[2] + rule["u0"] * fr.hcm
        span = rule["span"] * fr.hcm
        d = np.maximum(u0 - V[:, 2], 0.0)
        low = m & (d > 0)
        V[low, 2] = u0 - span * np.tanh(d[low] / span)
        out = dict(out)
        out["V"] = V
    for rule in st.get("stretch", []):
        # The listed parts below `u0` head cm drawn longer: a depth d below
        # u0 becomes k x d, faded in over `fade` head cm (locks the guide's
        # shorter neck left ending at ours' middle).
        H = head_coords(fr, out["V"])
        m = np.isin(out["part"], rule["parts"]) & (H[:, 2] < rule["u0"])
        if m.any():
            V = out["V"].copy()
            u0 = fr.centre[2] + rule["u0"] * fr.hcm
            d = u0 - V[m, 2]
            f = rule.get("fade", 2.0) * fr.hcm
            ramp = np.clip(d / max(f, 1e-6), 0.0, 1.0)
            V[m, 2] = u0 - d * (1.0 + (rule["k"] - 1.0) * ramp)
            # Their shading normals follow the new, longer shape.
            own = MO.vertex_normals(V, out["F"])
            N = np.array(out["N"], dtype=np.float64)
            w = np.zeros(len(V))
            w[m] = 0.75 * ramp
            N = N * (1.0 - w[:, None]) + own * w[:, None]
            out = dict(out)
            out["V"] = V
            out["N"] = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
    for rule in st.get("move", []):
        # The listed parts moved by `back_m` (away from the face) and `up_m`,
        # the move fading out over the hair next to them (no tear where a
        # tail's tie meets the head's hair).
        m = np.isin(out["part"], rule["parts"]).astype(np.float64)
        w = np.clip(MO.smooth_values(m, MO.edges(out["F"]), rule.get("fade", 8)) * 1.5, 0.0, 1.0)
        w = np.maximum(w, m)
        V = out["V"].copy()
        V[:, 1] += w * rule.get("back_m", 0.0)  # +Y is behind the face (the body faces -Y)
        V[:, 2] += w * rule.get("up_m", 0.0)
        out = dict(out)
        out["V"] = V
    return out


def drop_pieces(src: dict, min_area: float = MIN_PIECE_AREA) -> dict:
    """Separate pieces smaller than `min_area` go (flyaways); the largest
    piece always stays."""
    V, F = src["V"], src["F"]
    comp = MO.components(len(V), MO.edges(F))
    area = np.linalg.norm(MO.face_normals(V, F, unit=False), axis=1) * 0.5
    ids, inv = np.unique(comp[F[:, 0]], return_inverse=True)
    tot = np.bincount(inv.ravel(), weights=area, minlength=len(ids))
    keep = tot >= min_area
    keep[np.argmax(tot)] = True
    if keep.all():
        return src
    return _keep_vertices(src, keep[inv.ravel()])


def fill_holes(src: dict, max_perimeter: float = HOLE_PERIMETER) -> dict:
    """Closed open-edge loops shorter than `max_perimeter` (holes in the
    shell, never the hem: the longest loop stays open) are filled with a fan
    round a new centre vertex (the loop's mean normal, part and root-to-tip)."""
    V, F = src["V"], src["F"]
    loops = [lp for lp in boundary_loops(F) if len(lp) >= 3]
    if not loops:
        return src
    per = [float(np.linalg.norm(V[lp] - V[np.roll(lp, -1)], axis=1).sum()) for lp in loops]
    longest = int(np.argmax(per))
    addV, addN, addP, addR, addF = [], [], [], [], []
    n = len(V)
    for i, (lp, p) in enumerate(zip(loops, per)):
        if i == longest or p >= max_perimeter:
            continue
        c = n + len(addV)
        addV.append(V[lp].mean(axis=0))
        nn = src["N"][lp].mean(axis=0)
        addN.append(nn / max(float(np.linalg.norm(nn)), 1e-12))
        addP.append(np.bincount(src["part"][lp].astype(np.int64)).argmax())
        addR.append(float(src["root_to_tip"][lp].mean()))
        # The loop runs along the faces' edges a -> b: the fan closes it b -> a.
        for a, b in zip(lp, np.roll(lp, -1)):
            addF.append((int(b), int(a), c))
    if not addV:
        return src
    out = dict(src)
    out["V"] = np.vstack([V, np.array(addV)])
    out["N"] = np.vstack([src["N"], np.array(addN)])
    out["part"] = np.concatenate([src["part"], np.array(addP, dtype=src["part"].dtype)])
    out["root_to_tip"] = np.concatenate([src["root_to_tip"], np.array(addR)])
    out["F"] = np.vstack([F, np.array(addF, dtype=F.dtype)])
    return out


def strand_widths(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per open-edge vertex (indices `bv`): the strand's width there (the
    distance to the nearest open-edge vertex across it: on another loop, or
    on the same loop but more than STRAND_TIP_SPAN x STRAND_MIN_W away along
    it, so a tip's two sides converging at its point do not count), and the
    in-plane outward direction of the edge. Width inf where nothing lies
    across within 2 x STRAND_MIN_W."""
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    loops = [lp for lp in boundary_loops(F) if len(lp) >= 3]
    if not loops:
        e = np.zeros(0, dtype=np.int64)
        return e, np.zeros(0), np.zeros((0, 3))
    bv = np.concatenate(loops)
    lid = np.concatenate([np.full(len(lp), i) for i, lp in enumerate(loops)])
    arc = np.zeros(len(bv))
    tot = np.zeros(len(loops))
    off = 0
    for i, lp in enumerate(loops):
        seg = np.linalg.norm(V[np.roll(lp, -1)] - V[lp], axis=1)
        arc[off:off + len(lp)] = np.concatenate([[0.0], np.cumsum(seg)[:-1]])
        tot[i] = seg.sum()
        off += len(lp)
    kd = KDTree(len(bv))
    for i, v in enumerate(bv):
        kd.insert(Vector(tuple(V[v])), i)
    kd.balance()
    span = STRAND_TIP_SPAN * STRAND_MIN_W
    width = np.full(len(bv), np.inf)
    for i, v in enumerate(bv):
        for _, j, d in kd.find_range(Vector(tuple(V[v])), 2.0 * STRAND_MIN_W):
            if j == i:
                continue
            if lid[j] == lid[i]:
                a = abs(arc[j] - arc[i])
                if min(a, tot[lid[i]] - a) <= span:
                    continue
            width[i] = min(width[i], d)
    # Outward in-plane direction: (b - a) x n of each open edge a -> b, summed at its two ends.
    fn = MO.face_normals(V, F)
    D = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    Dn = np.vstack([fn, fn, fn])
    key = np.sort(D, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    b = cnt[inv.ravel()] == 1
    out_dir = np.cross(V[D[b, 1]] - V[D[b, 0]], Dn[b])
    acc = np.zeros_like(V)
    np.add.at(acc, D[b, 0], out_dir)
    np.add.at(acc, D[b, 1], out_dir)
    od = acc[bv]
    od /= np.maximum(np.linalg.norm(od, axis=1, keepdims=True), 1e-12)
    return bv, width, od


def widen_strands(src: dict) -> tuple[dict, int]:
    """Strands narrower than STRAND_MIN_W widened to it: each open-edge
    vertex there moves outward in the surface's plane by half the shortfall
    (at most WIDEN_MAX); the triangles behind it relaxed by a few Taubin
    steps (no shrinking; the edge itself pinned, so the tips keep their
    points and width)."""
    V, F = src["V"], src["F"]
    bv, width, od = strand_widths(V, F)
    thin = np.isfinite(width) & (width < STRAND_MIN_W)
    if not thin.any():
        return src, 0
    V = V.copy()
    move = np.minimum((STRAND_MIN_W - width[thin]) * 0.5, WIDEN_MAX)
    V[bv[thin]] += od[thin] * move[:, None]
    E = MO.edges(F)
    w = np.zeros(len(V))
    w[bv[thin]] = 1.0
    w = np.clip(MO.smooth_values(w, E, 3, keep=0.0) * 3.0, 0.0, 1.0)
    w[MO.boundary_vertices(F, len(V))] = 0.0
    V = MO.taubin(V, E, RELAX_ITERS, weight=w)
    out = dict(src)
    out["V"] = V
    return out, int(thin.sum())


def clean_source(fr, src: dict, st: dict, report: dict | None = None) -> dict:
    """The build's clean-up of a source mesh before its LODs: the style's
    edits, flyaways dropped, small holes in the shell filled, thin strands
    widened."""
    n0 = len(src["F"])
    out = style_edits(fr, src, st)
    n1 = len(out["F"])
    out = drop_pieces(out)
    n2 = len(out["F"])
    out = fill_holes(out)
    n3 = len(out["F"])
    out, widened = widen_strands(out)
    if report is not None:
        report.update({"edit_dropped_tris": n0 - n1, "pieces_dropped_tris": n1 - n2, "hole_fill_tris": n3 - n2,
                       "widened_edge_vertices": widened})
    return out


# ----------------------------------------------------------------------------
# Build: the inner layer (pass 2, character-look.md section 2)
# ----------------------------------------------------------------------------


def inner_room(fr, V: np.ndarray, covers_ears: bool) -> np.ndarray:
    """Per shell vertex: how far it is from the nearest drawn skin or garment
    (the regions a style hides, the scalp and the ears under long hair,
    do not count)."""
    trees = [t for r, t in fr.region_trees.items() if r != "scalp" and not (covers_ears and r == "ears")]
    garment_parts(fr)
    trees += [t for t in fr._garment_trees if t is not None]
    room = np.full(len(V), 1.0)
    reach = INNER_DEPTH + INNER_KEEP + 0.002
    for i, p in enumerate(V):
        q = Vector(tuple(p))
        for t in trees:
            loc, _, _, dist = t.find_nearest(q, reach)
            if loc is not None and dist < room[i]:
                room[i] = dist
    return room


def inner_layer(fr, V: np.ndarray, F: np.ndarray, covers_ears: bool) -> np.ndarray:
    """The inner layer's vertices for the shell (V, F) (its faces are F
    flipped): each vertex moved in along the shell's smoothed normal by
    INNER_DEPTH, tapered to 0 on the shell's open edge (so the two sheets
    meet there and the edge reads solid, its tips still points), and never
    nearer than INNER_KEEP to any drawn skin or garment."""
    E = MO.edges(F)
    n = MO.smooth_values(MO.vertex_normals(V, F), E, 2)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    indptr, indices = MO.csr(len(V), E)
    edge = MO.boundary_vertices(F, len(V))
    if edge.any():
        db = MO.dijkstra(V, indptr, indices, edge, limit=INNER_TAPER * 1.5)
    else:
        db = np.full(len(V), np.inf)
    depth = INNER_DEPTH * _smoothstep(0.0, INNER_TAPER, np.minimum(db, INNER_TAPER))
    depth = np.minimum(depth, np.maximum(inner_room(fr, V, covers_ears) - INNER_KEEP, 0.0))
    return V - n * depth[:, None]


# ----------------------------------------------------------------------------
# Build: the cap variant
# ----------------------------------------------------------------------------

HEM_ABOVE_BROW = 0.5  # hcm: the fringe hem (the fringe shadow's line) is held this far above the brows
CAP_HEM_MAX = 125.0  # degrees: the inner cap never reaches further down (the neck)
EAR_GAP = 0.006  # over the ears (they bend a little with the neck in the clips; the hair is rigid on the head)
SKIN_GAP = 0.0015  # metres over the skin the last clear keeps (the checks allow 2 mm inside)
MIN_ISLAND = 12  # vertices: smaller pieces of a LOD are dropped
CAP_LIFT = 0.003  # metres the inner scalp cap stands off the skull (the shell keeps 3 mm off the head)
CAP_TIE_PHI = 112.0  # the ponytail's tie under a hat: at the nape (as the lock builder)
CAP_TAIL_RISE = 0.25  # share of the tail's rise above its tie kept under a hat
CAP_PRESS = 0.007  # metres over the skull twin tails' roots lie under a hat's band
CAP_TUCK_M = 0.015  # metres below the band line over which they come back to their own height


# Hats over the hair (2026-11-07, round 4: the full hair right up to the
# brim, no tuck-in strip): per hair x hat a fit, computed
# at the hair build and applied by the client (characters/library.ts) and
# check_hair_hats.py. The hair under a hat's crown (its cover line less
# HAT_CUT_MARGIN) is dropped; the hat is scaled about the head centre just
# enough to clear the full hair left under its crown (HAT_FIT_TOL), at most
# HAT_FIT_MAX; beyond that the hair under the crown is pressed in to the
# hat (hat_limits, hat_press) and the brim sits a little into the hair.
HAT_COVER_STEP = 5.0  # degrees of azimuth between a hat's cover-line samples
HAT_BRIM_SLOPE = 0.003  # metres per degree: rays meeting the hat ever farther out this fast have left the crown (a brim)...
HAT_CROWN_LOOK = 25.0  # ...unless within this many degrees further down the hat comes back as near the head (a knit's fold)
HAT_CUT_MARGIN = 1.5  # degrees inside a hat's cover line: hair further in is dropped under it
HAT_FIT_TOL = 0.003  # metres the fitted hat clears the hair it covers
HAT_FIT_MAX = 1.15  # largest scale a hat takes over a hairstyle (beyond it the hair under the crown is pressed in)
HAT_LIMIT_ROWS = (0.0, 0.75, 1.5, 3.0, 4.5, 6.0, 9.0, 12.0)  # degrees above the cover line where a capped hat's reach is recorded


def hat_cover_line(fr, tree) -> np.ndarray:
    """Per azimuth (0, HAT_COVER_STEP, ... degrees) the polar angle down to
    which a hat's crown covers the head: rays from the head centre, from
    the top down, that meet it, until one misses or the hat runs away from
    the head (HAT_BRIM_SLOPE: under a brim)."""
    out = []
    c = Vector(tuple(fr.centre))
    phs = np.arange(0.5, 150.0, 0.5)
    for t in np.arange(0.0, 360.0, HAT_COVER_STEP):
        d = []
        for ph in phs:
            hit = tree.ray_cast(c, Vector(tuple(fr.dirs(float(ph), float(t)))), 0.6)
            d.append(None if hit[0] is None else float(hit[3]))
        last, k, first = 0.0, 0, None
        while k < len(phs):
            if d[k] is None:
                if first is not None:
                    break
                k += 1
                continue
            if first is None:
                first = k
            j0 = max(first, k - 4)
            prev = d[j0]
            # Rays meeting the hat ever farther out, fast (a brim's
            # underside, or a jump to it): the crown ends, unless within
            # HAT_CROWN_LOOK further down the hat comes back near the head
            # as near again (a fold of a slouchy knit).
            if k > j0 and (d[k] - prev) / (0.5 * (k - j0)) > HAT_BRIM_SLOPE:
                back = None
                for j in range(k + 1, min(len(phs), k + int(HAT_CROWN_LOOK / 0.5))):
                    if d[j] is None:
                        break
                    if d[j] <= prev:
                        back = j
                        break
                if back is None:
                    break
            last = float(phs[k])
            k += 1
        out.append(last)
    return np.array(out)


def cover_at(line: np.ndarray, theta: np.ndarray) -> np.ndarray:
    """A cover line (hat_cover_line) at azimuths `theta` (degrees),
    interpolated round the head."""
    t = np.r_[np.arange(0.0, 360.0, HAT_COVER_STEP), 360.0]
    return np.interp(np.asarray(theta) % 360.0, t, np.r_[line, line[0]])


def hat_cut_faces(fr, V: np.ndarray, F: np.ndarray, line: np.ndarray, keep: np.ndarray | None = None) -> np.ndarray:
    """Faces of a hair mesh left under a hat with cover line `line`: all but
    those wholly more than HAT_CUT_MARGIN inside it (`keep`: vertices never
    dropped, the inner cap)."""
    phi, theta, _ = fr.polar(V)
    deep = phi < cover_at(line, theta) - HAT_CUT_MARGIN
    if keep is not None:
        deep &= ~keep
    return ~deep[F].all(axis=1)


def hat_fit_scale(fr, tree, line: np.ndarray, V: np.ndarray, F: np.ndarray, keep: np.ndarray | None = None,
                  skip: np.ndarray | None = None) -> float:
    """The scale about the head centre a hat (BVH `tree`, cover line `line`)
    needs to clear the hair left under its crown by HAT_FIT_TOL: 1 when it
    already does, at most HAT_FIT_MAX (`skip`: vertices it leaves out, the
    twin tails hanging out from under the brim). Uncapped: hat_fits caps it
    at HAT_FIT_MAX and presses the rest in (hat_limits)."""
    kf = hat_cut_faces(fr, V, F, line, keep)
    used = np.zeros(len(V), dtype=bool)
    used[F[kf].ravel()] = True
    if keep is not None:
        used &= ~keep
    from . import hair_frame as HF

    phi, theta, r = fr.polar(V)
    band = np.array([HF.band_phi(t) for t in theta])
    under = used & (phi < cover_at(line, theta)) & (phi < band + HAT_FIT_BELOW)
    if skip is not None:
        under &= ~skip
    s = 1.0
    c = Vector(tuple(fr.centre))
    for i in np.nonzero(under)[0]:
        d = (V[i] - fr.centre) / max(float(r[i]), 1e-9)
        hit = tree.ray_cast(c, Vector(tuple(d)), 0.4)
        if hit[0] is not None and hit[3] > 1e-4:
            s = max(s, (float(r[i]) + HAT_FIT_TOL) / hit[3])
    return float(s)


def hat_limits(fr, tree, line: np.ndarray, scale: float) -> np.ndarray:
    """Where a hat capped at HAT_FIT_MAX is still short of the hair under
    its crown: per row of HAT_LIMIT_ROWS (degrees above the cover line) and
    azimuth sample, how far from the head centre the hair may reach, the
    scaled hat's inner surface less HAT_FIT_TOL (0: no hat there)."""
    c = Vector(tuple(fr.centre))
    out = np.zeros((len(HAT_LIMIT_ROWS), len(line)))
    rows = np.asarray(HAT_LIMIT_ROWS)
    for j, up in enumerate(HAT_LIMIT_ROWS):
        # The nearest the hat comes round each sample (half a step either way
        # in azimuth and toward the neighbouring rows): interpolating
        # between samples never reaches past it.
        ups = [u for u in (up, 0.5 * (up + rows[max(j - 1, 0)]), 0.5 * (up + rows[min(j + 1, len(rows) - 1)]))]
        for k, t in enumerate(np.arange(0.0, 360.0, HAT_COVER_STEP)):
            best = None
            for dt in (-0.5 * HAT_COVER_STEP, -0.25 * HAT_COVER_STEP, 0.0, 0.25 * HAT_COVER_STEP, 0.5 * HAT_COVER_STEP):
                ln = float(cover_at(line, np.array([t + dt]))[0])
                for u in ups:
                    ph = max(ln - u, 0.5)
                    hit = tree.ray_cast(c, Vector(tuple(fr.dirs(ph, float(t + dt)))), 0.6)
                    if hit[0] is not None:
                        best = hit[3] if best is None else min(best, hit[3])
            if best is not None:
                out[j, k] = best * scale - HAT_FIT_TOL
    return out


def _tighten_limits(fr, hv, ht, line, limit, scale, V, F, scalp, skip, rounds: int = 6) -> np.ndarray:
    """hat_limits checked against this hair: where a pressed vertex still
    reaches past the scaled hat (a fold or a trim between the samples), the
    limit round it is lowered to the hat there."""
    from mathutils.bvhtree import BVHTree

    from . import hair_frame as HF

    c = fr.centre
    tree = BVHTree.FromPolygons((c + (hv - c) * scale).tolist(), ht.tolist(), all_triangles=True)
    limit = limit.copy()
    rows = np.asarray(HAT_LIMIT_ROWS)
    n = limit.shape[1]
    for _ in range(rounds):
        P = hat_press(fr, V, F, line, limit, scalp)
        kf = hat_cut_faces(fr, P, F, line, scalp)
        used = np.zeros(len(P), dtype=bool)
        used[F[kf].ravel()] = True
        used &= ~scalp
        if skip is not None:
            used &= ~skip
        phi, theta, r = fr.polar(P)
        band = np.array([HF.band_phi(t) for t in theta])
        up = cover_at(line, theta) - phi
        idx = np.nonzero(used & (up > 0) & (phi < band + HAT_FIT_BELOW))[0]
        changed = False
        for i in idx:
            hit = tree.ray_cast(Vector(tuple(c)), Vector(tuple((P[i] - c) / r[i])), 0.4)
            if hit[0] is None or hit[3] >= r[i] - 0.0005:
                continue
            want = hit[3] - HAT_FIT_TOL
            t = (theta[i] % 360.0) / HAT_COVER_STEP
            k0 = int(np.floor(t)) % n
            u = float(np.clip(up[i], rows[0], rows[-1]))
            j0 = int(np.clip(np.searchsorted(rows, u, side="right") - 1, 0, len(rows) - 2))
            for j in (j0, j0 + 1):
                for k in (k0, (k0 + 1) % n):
                    if limit[j, k] <= 0 or limit[j, k] > want:
                        limit[j, k] = want
                        changed = True
        if not changed:
            break
    return limit


def hat_press(fr, V: np.ndarray, F: np.ndarray, line: np.ndarray, limit: np.ndarray, keep: np.ndarray | None = None,
              centre: np.ndarray | None = None) -> np.ndarray:
    """The hair left under a hat's crown (hat_cut_faces) pressed in to its
    `limit` (hat_limits) where it reaches past it: inside the hat only,
    the hair below the cover line keeps its shape (the client's
    hatfit.ts does the same)."""
    c = fr.centre if centre is None else centre
    kf = hat_cut_faces(fr, V, F, line, keep)
    used = np.zeros(len(V), dtype=bool)
    used[F[kf].ravel()] = True
    if keep is not None:
        used &= ~keep
    d = V - c
    r = np.linalg.norm(d, axis=1)
    phi = np.degrees(np.arccos(np.clip(d[:, 2] / np.maximum(r, 1e-12), -1.0, 1.0)))
    theta = np.degrees(np.arctan2(d[:, 0], -d[:, 1])) % 360.0
    up = cover_at(line, theta) - phi
    lim = limit_at(limit, theta, up)
    m = used & (up > 0.0) & (lim > 0.0) & (r > lim)
    out = V.copy()
    out[m] = c + d[m] * (lim[m] / r[m])[:, None]
    return out


def limit_at(limit: np.ndarray, theta: np.ndarray, up: np.ndarray) -> np.ndarray:
    """hat_limits at azimuths `theta`, `up` degrees above the cover line
    (interpolated; beyond the last row, the last row)."""
    n = limit.shape[1]
    t = (np.asarray(theta) % 360.0) / HAT_COVER_STEP
    i0 = np.floor(t).astype(int) % n
    i1 = (i0 + 1) % n
    ft = t - np.floor(t)
    rows = np.asarray(HAT_LIMIT_ROWS, dtype=np.float64)
    u = np.clip(np.asarray(up, dtype=np.float64), rows[0], rows[-1])
    j0 = np.clip(np.searchsorted(rows, u, side="right") - 1, 0, len(rows) - 2)
    fu = (u - rows[j0]) / (rows[j0 + 1] - rows[j0])
    a = limit[j0, i0] * (1 - ft) + limit[j0, i1] * ft
    b = limit[j0 + 1, i0] * (1 - ft) + limit[j0 + 1, i1] * ft
    # A row without the hat (0) does not limit.
    out = a * (1 - fu) + b * fu
    return np.where((a > 0) & (b > 0), out, np.maximum(a, b))


SCALP_ATTRIBUTE = "_SCALP"  # hat variant: 1 on the inner cap (kept under every hat)
HAT_FIT_BELOW = 10.0  # degrees below the band line the fit and the check look (hoods reach the shoulders)


def hat_meshes() -> list:
    """The hats in the open .blend that cover the hair (`hides` scalp):
    (asset, rest vertices in world space, triangles), LOD0."""
    import bpy

    out = []
    for o in bpy.data.objects:
        if o.type != "MESH" or o.get("lod") != 0 or o.get("region") or not str(o.get("asset", "")).startswith("gear/hat/"):
            continue
        if "scalp" not in list(o.get("hides", [])):
            continue
        me = o.data
        V = np.array([tuple(o.matrix_world @ v.co) for v in me.vertices])
        me.calc_loop_triangles()
        T = np.array([tuple(t.vertices) for t in me.loop_triangles], dtype=np.int64)
        out.append((str(o["asset"]), V, T))
    return sorted(out, key=lambda x: x[0])


def hat_fits(fr, V: np.ndarray, F: np.ndarray, scalp: np.ndarray, skip: np.ndarray | None = None) -> dict:
    """The hat variant's fit under every hat in the .blend (character-
    adoption.md 5.5): the head centre (glTF axes) and per hat its scale
    and cover line (hat_cover_line, HAT_COVER_STEP apart from straight
    ahead toward the left)."""
    from mathutils.bvhtree import BVHTree

    hats = {}
    found = hat_meshes()
    if not found:
        from . import pipeline as PL

        PL.log("hair: no hats in the scene to fit (build the gear first): hat_fit is empty")
    for asset, hv, ht in found:
        tree = BVHTree.FromPolygons(hv.tolist(), ht.tolist(), all_triangles=True)
        line = hat_cover_line(fr, tree)
        need = hat_fit_scale(fr, tree, line, V, F, scalp, skip)
        s = min(need, HAT_FIT_MAX)
        hats[asset] = {"scale": round(s, 4), "cover": [round(float(x), 1) for x in line]}
        if need > HAT_FIT_MAX + 1e-6:
            # The rest pressed in, under the crown only.
            lim = hat_limits(fr, tree, line, round(s, 4))
            lim = _tighten_limits(fr, hv, ht, line, lim, round(s, 4), V, F, scalp, skip)
            hats[asset]["limit"] = [[round(float(x), 4) for x in row] for row in lim]
    c = fr.centre
    return {"centre": [round(float(c[0]), 5), round(float(c[2]), 5), round(float(-c[1]), 5)],
            "cover_step": HAT_COVER_STEP, "cut_margin": HAT_CUT_MARGIN, "limit_rows": list(HAT_LIMIT_ROWS),
            "hats": hats}


def tie_centre(src: dict) -> np.ndarray | None:
    tie = src["part"] == TIE
    if tie.sum() >= 8:
        return src["V"][tie].mean(axis=0)
    tail = src["part"] == TAIL
    if tail.sum() >= 8:
        # The tail's root: its vertices nearest the crown.
        r = src["root_to_tip"][tail]
        return src["V"][tail][r <= np.percentile(r, 3)].mean(axis=0)
    return None


def cap_needed(st: dict, src: dict) -> bool:
    """Whether a style needs its own hat variant: its tails change under a
    hat (cap_source). Otherwise its own file is worn under hats."""
    tail = (src["part"] == TAIL) | (src["part"] == TIE)
    if not tail.any() or st.get("cap_keep_tail"):
        return False
    return bool(st.get("cap_press_tails")) or tie_centre(src) is not None


def cap_source(fr, src: dict, st: dict) -> dict:
    """The source mesh worn under hats (character-adoption.md section 4.4, round 4
    of 2026-11-07): the style at its full shape; each hat drops the hair its
    crown covers and is scaled just enough to sit on the rest (the client
    and check_hair_hats.py apply the per-hat fit the build computes,
    hat_fit_scale). A tail tied high above the band (a ponytail) is turned
    down and moved so its tie sits at the nape (it cannot stay tied under a
    hat); twin tails (`cap_press_tails`) keep their place with their roots
    pressed under the band; tails from below the band (a braid,
    `cap_keep_tail`) are untouched."""
    from . import hair as HR
    from . import hair_frame as HF

    V = src["V"].copy()
    part = src["part"]
    tail = (part == TAIL) | (part == TIE)
    keep = np.zeros(len(V), dtype=bool)
    if st.get("cap_keep_tail"):
        keep = tail.copy()
    elif not st.get("cap_press_tails"):
        c = tie_centre(src)
        if tail.any() and c is not None:
            # The rise out of the tie squashed (under a hat the tail falls
            # from the nape), then the whole tail moved down to the nape.
            rel = V[tail] - c
            rel[:, 2] = np.where(rel[:, 2] > 0, rel[:, 2] * CAP_TAIL_RISE, rel[:, 2])
            target = fr.point(CAP_TIE_PHI, 180.0, 1.0 * fr.hcm)
            V[tail] = rel + target
            V[tail] = push_out(fr, V[tail], st.get("covers_ears", False), st.get("trunk_clear", 0.012))
            keep = tail.copy()
    out = dict(src)
    out["V"] = V
    if st.get("cap_press_tails"):
        # Twin tails tied above the band: their roots near the head pressed
        # under the hats' band; the tails hang as styled, out from under the
        # brim (the fit leaves them out, hat_fits).
        out["V"] = tuck_cap(fr, V, part, st, ~tail)
    return out


# ----------------------------------------------------------------------------
# Build: LODs, attributes, weights
# ----------------------------------------------------------------------------


def tuck_cap(fr, V: np.ndarray, part: np.ndarray, st: dict, keep: np.ndarray | None = None) -> np.ndarray:
    """Twin tails' roots tucked under the hats' band (round 4 of
    2026-11-07: only tails take it, the hair keeps its full shape): above
    the band line (plus CAP_MARGIN) within CAP_PRESS of the skull, below it
    its own height back over CAP_TUCK_M. Tails standing well off the head
    are not pulled in; `keep` vertices not at all.
    Vertices the tuck would put into an ear stay put below the band, and
    above it lie on the ear's outer surface (press_cap's rule)."""
    from . import hair as HR
    from . import hair_frame as HF

    phi, theta, r = fr.polar(V)
    rs = fr.skull_r(phi, theta)
    bline = np.array([HF.band_phi(t) for t in theta]) + HR.CAP_MARGIN
    tdeg = np.degrees(CAP_TUCK_M / np.maximum(rs, 1e-6))
    s = _smoothstep(bline, bline + tdeg, phi)
    h = r - rs
    want = np.where(h > CAP_PRESS, CAP_PRESS + s * (h - CAP_PRESS), h)
    w = np.ones(len(V))
    tail = (part == TAIL) | (part == TIE)
    # Tails are pressed only where a hat covers them (above the band line),
    # and not far off the head (outside any hat): no tuck below the line.
    w[tail] = (1.0 - _smoothstep(0.03, 0.05, h[tail])) * (phi[tail] < bline[tail])
    if keep is not None:
        w[keep] = 0.0
    newr = r + w * (rs + want - r)
    d = (V - fr.centre) / np.maximum(r[:, None], 1e-12)
    out = fr.centre + d * newr[:, None]
    moved = np.nonzero(np.abs(newr - r) > 1e-6)[0]
    if fr.ears is not None and len(moved):
        for i in moved:
            if fr.inside(out[i], fr.collide, reach=0.03) > 0.0 or fr.depth(out[i], fr.check_tree, reach=0.05) < 0.0025:
                out[i] = V[i]
        # Covered vertices left standing beside the ear: onto its outer
        # surface, under the brim.
        phi2, theta2, r2 = fr.polar(out)
        band = np.array([HF.band_phi(t) for t in theta2]) + HR.CAP_MARGIN
        rs2 = fr.skull_r(phi2, theta2)
        left = np.nonzero((phi2 < band) & (r2 > rs2 + CAP_PRESS + 1e-4) & (w > 0.5))[0]
        c = Vector(tuple(fr.centre))
        for i in left:
            dd = (out[i] - fr.centre) / r2[i]
            rr = rs2[i] + CAP_PRESS
            far = 0.3
            hit = fr.ears.ray_cast(c + Vector(tuple(dd * far)), Vector(tuple(-dd)), far)
            if hit[0] is not None:
                rr = max(rr, far - hit[3] + 0.001)
            out[i] = fr.centre + dd * min(rr, r2[i])
        if len(left):
            out[left] = push_out(fr, out[left], False, st.get("trunk_clear", 0.012), rounds=2)
    return out



class _Lods(list):
    """build_lods' result: per LOD its tuple; `scalp`, per LOD the vertices
    of the inner cap (and its inner copy), which no hat drops."""

    def __init__(self):
        super().__init__()
        self.scalp = []


class _Style:
    """What hair.inner_cap needs of a style: the cap hem, and no mass (the
    Meshy shell keeps its own at every LOD)."""

    def __init__(self, cap_hem):
        self.cap_hem = cap_hem

    @staticmethod
    def vol(ph, th):
        return np.zeros(np.broadcast(np.asarray(ph), np.asarray(th)).shape)


def inner_cap_hem(fr, src: dict, lo_extra: float = 3.0):
    """theta -> phi the inner cap reaches: over the hidden scalp, and down
    under the hair that lies on the head (to 4 degrees above its lowest
    point near the skull, at most CAP_HEM_MAX), so looking up into the hair
    at the nape or the temples shows deep shade, never the skin through the
    gaps between its points."""
    from . import hair as HR

    phi, theta, r = fr.polar(src["V"])
    near = (r - fr.skull_r(phi, theta) < 0.03) & (phi < CAP_HEM_MAX + 10.0) & (src["part"] != TAIL) & (src["part"] != TIE)
    th = np.arange(0.0, 360.0, 10.0)
    ctrl = []
    for t in th:
        m = near & (np.abs(((theta - t + 180.0) % 360.0) - 180.0) < 6.0)
        lo = fr.scalp_border(t) + lo_extra
        hem = float(np.percentile(phi[m], 97)) - 4.0 if m.sum() > 10 else lo
        ctrl.append((float(t), float(np.clip(hem, lo, max(lo, CAP_HEM_MAX)))))
    base = HR.periodic(ctrl, mirror=False)
    return lambda t: np.maximum(base(t), fr.scalp_border(t) + lo_extra)


def inner_cap_mesh(fr, cap_hem, lod: int) -> tuple[np.ndarray, np.ndarray]:
    from . import hair as HR

    g = HR.Geo()
    # Under the shell (which keeps 3 mm off the head): 1.5 mm off the skull.
    HR.inner_cap(g, fr, _Style(cap_hem), lod, False, lift=CAP_LIFT / fr.hcm)
    V = np.array(g.verts)
    # Over the skull's local bulges too: each vertex at least CAP_LIFT over
    # the highest skull within half a grid step round it (the cap's flat
    # triangles would cut under a bulge between their corners).
    phi, theta, r = fr.polar(V)
    step = 360.0 / (36, 20, 10, 8)[lod] * 0.6
    top = fr.skull_r(phi, theta)
    for dp in (-step, 0.0, step):
        for dt in (-step, 0.0, step):
            top = np.maximum(top, fr.skull_r(np.clip(phi + dp, 0.0, 180.0), theta + dt))
    want = top + CAP_LIFT * (r - fr.skull_r(phi, theta) > 0)
    lift = np.maximum(want, r)
    d = (V - fr.centre) / np.maximum(r[:, None], 1e-12)
    V = fr.centre + d * lift[:, None]
    T = []
    for f in g.faces:
        for i in range(1, len(f) - 1):
            T.append((f[0], f[i], f[i + 1]))
    return V, np.array(T, dtype=np.int64)


# ----------------------------------------------------------------------------
# Build: the clipped band (short styles' close-cut sides, nape and hairline)
# ----------------------------------------------------------------------------

CLIP_STEP = 2.0  # degrees of azimuth between the cover lines' samples
# The band's bottom line below the scalp region's border (degrees, by
# azimuth): down to where the face texture's painted scalp ends
# (face_paint.SCALP_LINE: the forehead line, the temples, over the ear, behind
# it and the nape), less half a hem point, so the points end on its soft
# edge (measured on body_a's head; the sideburns are the clip's
# `sideburn_deg` bump, the ears the build's own clearance).
CLIP_BOTTOM = [[0.0, 7.0], [30.0, 5.0], [45.0, 6.0], [60.0, 6.0], [90.0, 0.5], [120.0, 1.0], [135.0, 11.0],
               [150.0, 4.0], [180.0, 1.0], [210.0, 4.0], [225.0, 11.0], [240.0, 1.0], [270.0, 0.5], [300.0, 6.0],
               [315.0, 6.0], [330.0, 5.0]]


def _periodic_ctrl(ctrl, theta) -> np.ndarray:
    """A number, or [[theta, value], ...] interpolated round the head."""
    theta = np.asarray(theta, dtype=np.float64) % 360.0
    if isinstance(ctrl, (int, float)):
        return np.full(theta.shape, float(ctrl))
    c = np.array(sorted(ctrl), dtype=np.float64)
    t = np.concatenate([c[:, 0] - 360.0, c[:, 0], c[:, 0] + 360.0])
    return np.interp(theta, t, np.concatenate([c[:, 1]] * 3))


def _circ_smooth(x: np.ndarray, passes: int) -> np.ndarray:
    for _ in range(passes):
        x = 0.5 * x + 0.25 * (np.roll(x, 1) + np.roll(x, -1))
    return x


def clip_lines(fr, src: dict, spec: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per azimuth (CLIP_STEP): where the shell's cover of the head ends
    (rays from the head centre down each azimuth meet it from the crown down
    to that polar angle; a gap of 4 degrees ends it, so a lock lying on the
    forehead below the hairline does not count, but a lower roll of the same
    hair across a slit does; the median within 6 degrees either side), and
    the band's top (tucked `tuck_deg` under it; its tone fades in from the
    cap's deep shade, so a slit between locks above it still shows shade) and bottom (`bottom_deg` below the scalp region's
    border, a `sideburn_deg` bump in front of the ears, and never nearer the
    ears than the build keeps hair from them)."""
    from mathutils.bvhtree import BVHTree

    keep = (src["part"] != TAIL) & (src["part"] != TIE)
    keepf = keep[src["F"]].all(axis=1)
    tree = BVHTree.FromPolygons(src["V"].tolist(), src["F"][keepf].tolist(), all_triangles=True)
    th = np.arange(0.0, 360.0, CLIP_STEP)
    end = np.zeros(len(th))
    c = Vector(tuple(fr.centre))
    for j, t in enumerate(th):
        miss, last = 0, 0.0
        for ph in np.arange(4.0, 150.0, 1.0):
            d = fr.dirs(ph, t)
            hit = tree.ray_cast(c, Vector(tuple(d)), 0.3)
            if hit[0] is None:
                miss += 1
                if miss >= 4:
                    break
            else:
                miss, last = 0, ph
        end[j] = last
    med = np.array([np.median(np.take(end, range(j - 3, j + 4), mode="wrap")) for j in range(len(th))])
    end = _circ_smooth(med, 2)
    top = end - spec.get("tuck_deg", 5.0)
    bottom = fr.scalp_border(th) + _periodic_ctrl(spec.get("bottom_deg", CLIP_BOTTOM), th)
    sb = spec.get("sideburn_deg", 0.0)
    if sb:
        for centre in (78.0, 282.0):
            s = np.clip(1.0 - np.abs(((th - centre + 180.0) % 360.0) - 180.0) / 10.0, 0.0, 1.0)
            bottom += sb * s * s * (3 - 2 * s)
    if fr.ears is not None:
        reach = EAR_GAP + 0.003
        h = spec.get("h_top_mm", 4.0) * 1e-3
        for j, t in enumerate(th):
            for ph in np.arange(max(top[j], 20.0), bottom[j] + 1.0, 1.0):
                p = fr.point(ph, t, h)
                if fr.ears.find_nearest(Vector(tuple(p)), reach)[0] is not None:
                    bottom[j] = min(bottom[j], ph - 2.0)
                    break
    bottom = np.maximum(bottom, top + spec.get("min_deg", 3.0))
    return th, top, bottom


def clip_band_mesh(fr, lines, spec: dict, lod: int):
    """The clipped band for one LOD: a grid over the head frame from each
    azimuth's top line to its bottom line, `h_top_mm` over the skull at the
    top falling to `h_bot_mm` at the hem; at LOD0 every other column a comb
    ridge `ridge_mm` high, slanting back as it runs down (the clipped hair's
    grain), the hem cut in points (each ridge runs on `tip_deg` below it).
    The points may vary round the head ([[theta, value], ...]). Returns
    (V, F, valley 0..1, down 0..1) or None (no band at this LOD)."""
    rows = (7, 4, 2, 0)[lod]
    if rows == 0:
        return None
    th, top, bottom = lines
    step = spec.get("period_deg", 5.0) / 2.0 * (1, 2, 4, 4)[lod]
    ridged = lod == 0
    cols = int(round(360.0 / step))
    tc = np.arange(cols) * step
    tl = np.concatenate([th, [360.0]])
    t_top = np.interp(tc, tl, np.concatenate([top, top[:1]]))
    t_bot = np.interp(tc, tl, np.concatenate([bottom, bottom[:1]]))
    crest = (np.arange(cols) % 2 == 0).astype(np.float64)
    tip = _periodic_ctrl(spec.get("tip_deg", 2.5), tc) * crest
    slant = spec.get("slant_deg", 3.0)
    side = np.sin(np.radians(tc))  # back along each side, none at the front and the back
    h_top, h_bot = spec.get("h_top_mm", 4.0) * 1e-3, spec.get("h_bot_mm", 2.5) * 1e-3
    ridge = spec.get("ridge_mm", 0.5) * 1e-3 if ridged else 0.0
    V, valley, down = [], [], []
    for r in range(rows + 1):
        f = r / rows
        ph = t_top + (t_bot - t_top) * f + (tip if r == rows else 0.0)
        h = h_top + (h_bot - h_top) * f + ridge * crest * (1.0 - 0.6 * f)
        V.append(fr.point(ph, tc + slant * f * side, h))
        valley.append((1.0 - crest) if ridged else np.zeros(cols))
        down.append(np.full(cols, f))
    V = np.vstack(V)
    F = []
    for r in range(rows):
        for k in range(cols):
            a, b = r * cols + k, r * cols + (k + 1) % cols
            c, d = a + cols, b + cols
            F += [(a, c, d), (a, d, b)]
    return V, np.array(F, dtype=np.int64), np.concatenate(valley), np.concatenate(down)


def drop_cap_under_band(fr, Vc: np.ndarray, Fc: np.ndarray, lines, margin: float = 3.0):
    """The inner cap without its faces reaching under the clipped band
    (a corner more than `margin` degrees below its top line, above its
    bottom line): the band covers the scalp there, and the cap's coarse rows
    (lifted over the skull's bulges) would stand through it."""
    th, top, bottom = lines
    phi, theta, _ = fr.polar(Vc)
    tl = np.concatenate([th, [360.0]])
    under = (phi > np.interp(theta, tl, np.concatenate([top, top[:1]])) + margin) \
        & (phi < np.interp(theta, tl, np.concatenate([bottom, bottom[:1]])) - 1.0)
    keep = ~under[Fc].any(axis=1)
    used = np.unique(Fc[keep].ravel())
    remap = -np.ones(len(Vc), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return Vc[used], remap[Fc[keep]]


def transfer_attrs(src: dict, V: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normals, root-to-tip and parts at new vertices from the source mesh
    (the nearest point on it, barycentric; parts from the nearest corner)."""
    fi, bc = MO.nearest_on(src["V"], src["F"], V)
    N = MO.interpolate(src["N"], src["F"], fi, bc)
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
    rtt = np.clip(MO.interpolate(src["root_to_tip"], src["F"], fi, bc), 0.0, 1.0)
    tri = src["F"][fi]
    part = src["part"][tri[np.arange(len(V)), np.argmax(bc, axis=1)]]
    return N, rtt, part


def shade(fr, V: np.ndarray, F: np.ndarray, rtt: np.ndarray, is_cap: np.ndarray, occ_extra: list) -> np.ndarray:
    """_SHADE per vertex (character-adoption.md section 3): x = occlusion (hair.py's
    ray bake against the hair and the body) x the root darkening, y = the
    root-to-tip value, z = the band phase (0.5 +- hair.BAND_PHASE, varied
    smoothly by azimuth), w = the outline hint."""
    from mathutils.bvhtree import BVHTree

    from . import hair as HR

    occ_V = [V]
    occ_T = [F]
    off = len(V)
    for co, t in occ_extra:
        occ_V.append(co)
        occ_T.append(t + off)
        off += len(co)
    occ = BVHTree.FromPolygons(np.vstack(occ_V).tolist(), np.vstack(occ_T).tolist(), all_triangles=True)
    geoN = MO.vertex_normals(V, F)
    E = MO.edges(F)
    ao = MO.smooth_values(HR.ambient_occlusion(V, geoN, occ), E, 2)
    root = ROOT_VALUE + (1.0 - ROOT_VALUE) * _smoothstep(0.0, ROOT_SPAN, rtt)
    root[is_cap] = ROOT_VALUE
    x = np.clip((1.0 - HR.AO_WEIGHT + HR.AO_WEIGHT * np.power(np.clip(ao, 0, 1), 0.8)) * root, HR.SHADE_FLOOR, 1.0)
    x[is_cap] = np.minimum(x[is_cap], 0.5)
    y = np.where(is_cap, 1.0, rtt)
    _, theta, _ = fr.polar(V)
    z = 0.5 + HR.BAND_PHASE * np.sin(np.radians(theta) * 9.0 + 0.7)
    z[is_cap] = INNER_Z
    w = np.full(len(V), HR.OUTLINE_W)
    return np.stack([x, y, z, w], axis=1)


def chain_members(fr, spec: dict, V: np.ndarray, part: np.ndarray) -> np.ndarray:
    m = np.isin(part, spec["parts"])
    H = head_coords(fr, V)
    if spec.get("side"):
        m &= H[:, 0] * spec["side"] > 0
    if spec.get("front"):
        m &= H[:, 1] > -1.0
    if "start_u" in spec:
        m &= H[:, 2] < spec["start_u"]
    return m


def chain_param(fr, spec: dict, V: np.ndarray, rtt: np.ndarray, members: np.ndarray):
    """Per vertex position along the chain (0 at its start, 1 at its end)
    and the chain's (start, end) in that measure."""
    if spec.get("along") == "tail":
        t = rtt
        if members.any():
            a, b = float(np.percentile(t[members], 1)), float(np.percentile(t[members], 99.5))
        else:
            a, b = 0.0, 1.0
        return np.clip((t - a) / max(b - a, 1e-6), 0.0, 1.0)
    z0 = fr.centre[2] + spec["start_u"] * fr.hcm
    z1 = float(np.percentile(V[members, 2], 0.5)) if members.any() else z0 - 0.1
    return np.clip((z0 - V[:, 2]) / max(z0 - z1, 1e-6), 0.0, 1.0)


def chain_joints(fr, chains: dict, V: np.ndarray, part: np.ndarray, rtt: np.ndarray) -> dict:
    """Rest joints of each chain: centroids of its member vertices at even
    steps along the chain."""
    from . import hair as HR

    out = {}
    for name, spec in chains.items():
        m = chain_members(fr, spec, V, part)
        if m.sum() < 20:
            continue
        n = HR.CHAIN_JOINTS[name.split(".")[0] if name.startswith(("back", "front")) else name]
        t = chain_param(fr, spec, V, rtt, m)
        pts = []
        for k in range(n + 1):
            c = k / n
            w = np.exp(-((t[m] - c) / 0.08) ** 2)
            pts.append((V[m] * w[:, None]).sum(axis=0) / max(float(w.sum()), 1e-9))
        out[name] = np.array(pts)
    return out


def sway_weights(fr, chains: dict, joints: dict, V: np.ndarray, F: np.ndarray, part: np.ndarray,
                 rtt: np.ndarray, follow: dict | None = None) -> list[dict]:
    """Per vertex {bone: weight}: head near the scalp; down a chain the hat
    function over its bones (hair.sway_weights' rule); where two chains'
    hair meets in one sheet (the side locks and the back) the memberships
    are blended over a few centimetres so the sheet never tears; with
    `follow` (the style's), hair below the neck resting on the shoulders,
    chest and back blended into the body's own weights there
    (`body_follow`); at most four influences."""
    from . import hair as HR

    E = MO.edges(F)
    W = [dict() for _ in range(len(V))]
    total = np.zeros(len(V))
    memb = {}
    for name in joints:
        m = chain_members(fr, chains[name], V, part).astype(np.float64)
        memb[name] = MO.smooth_values(m, E, 6)
    s = sum(memb.values()) if memb else np.zeros(len(V))
    for name, mm in memb.items():
        mm = np.where(s > 1.0, mm / np.maximum(s, 1e-9), mm)
        spec = chains[name]
        t = chain_param(fr, spec, V, rtt, chain_members(fr, spec, V, part))
        bones = HR.chain_bone_names(name)
        nb = len(bones)
        for i in np.nonzero(mm > 1e-3)[0]:
            sig = t[i] * nb
            w = {}
            for k in range(nb):
                v = max(0.0, 1.0 - abs(sig - (k + 0.5)))
                if k == nb - 1 and sig > nb - 0.5:
                    v = 1.0
                if k == 0 and sig < 0.5:
                    v = sig / 0.5
                if v > 1e-4:
                    w[bones[k]] = v
            if sig < 0.5:
                w[HR.HEAD_BONE] = 1.0 - sig / 0.5
            tot = sum(w.values()) or 1.0
            for b, v in w.items():
                W[i][b] = W[i].get(b, 0.0) + mm[i] * v / tot
            total[i] += mm[i]
    if follow:
        b, BW = body_follow(fr, V, F, follow)
    out = []
    for i in range(len(V)):
        w = W[i]
        rest = max(0.0, 1.0 - total[i])
        if rest > 1e-6:
            w[HR.HEAD_BONE] = w.get(HR.HEAD_BONE, 0.0) + rest
        if follow and b[i] > 1e-4:
            tot = sum(w.values()) or 1.0
            w = {k: (1.0 - b[i]) * v / tot for k, v in w.items()}
            for k, v in BW[i].items():
                w[k] = w.get(k, 0.0) + b[i] * v
        top = sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
        tot = sum(v for _, v in top)
        out.append({k: v / tot for k, v in top})
    return out


# Hair resting on the body (2026-11-06): the bones whose skin weights hair
# below the neck takes from the body surface under it (the chest, the
# shoulder tops and the upper back; never the arms, the neck or the head).
FOLLOW_BONES = ("DEF-spine.001", "DEF-spine.002", "DEF-spine.003", "DEF-spine.004", "DEF-breast.L", "DEF-breast.R",
                "DEF-shoulder.L", "DEF-shoulder.R")
FOLLOW_REGIONS = ("torso", "neck")


def _region_weights(obj, bones: tuple) -> np.ndarray:
    """(n, len(bones)) skin weights of a body region object's vertices."""
    idx = {g.index: bones.index(g.name) for g in obj.vertex_groups if g.name in bones}
    out = np.zeros((len(obj.data.vertices), len(bones)))
    for v in obj.data.vertices:
        for g in v.groups:
            k = idx.get(g.group)
            if k is not None:
                out[v.index, k] = g.weight
    return out


def body_follow(fr, V: np.ndarray, F: np.ndarray, spec: dict) -> tuple[np.ndarray, list[dict]]:
    """How much each hair vertex follows the body (0 at and above `top`
    head cm, the chin by default, to `max` at `bottom` m below the shoulder
    line, smooth), and the body weights it follows: the FOLLOW_BONES
    weights of the torso and neck skin nearest it (inverse-distance over the
    `k` nearest vertices), renormalised; smoothed over the hair mesh so
    neighbouring vertices of a lock move together."""
    from mathutils.kdtree import KDTree

    if not hasattr(fr, "_follow"):
        cos, ws = [], []
        for r in FOLLOW_REGIONS:
            o = getattr(fr, "objects", {}).get(r)
            if o is None or r not in fr.parts:
                continue
            cos.append(fr.parts[r][0])
            ws.append(_region_weights(o, FOLLOW_BONES))
        co = np.vstack(cos)
        bw = np.vstack(ws)
        tree = KDTree(len(co))
        for i, p in enumerate(co):
            tree.insert(Vector(tuple(p)), i)
        tree.balance()
        fr._follow = (co, bw, tree)
    co, bw, tree = fr._follow
    k = int(spec.get("k", 8))
    Wb = np.zeros((len(V), len(FOLLOW_BONES)))
    for i, p in enumerate(V):
        hits = tree.find_n(Vector(tuple(p)), k)
        j = np.array([h[1] for h in hits])
        d = np.array([h[2] for h in hits])
        wt = 1.0 / np.maximum(d, 0.005) ** 2
        Wb[i] = (bw[j] * wt[:, None]).sum(axis=0) / wt.sum()
    E = MO.edges(F)
    for c in range(Wb.shape[1]):
        Wb[:, c] = MO.smooth_values(Wb[:, c], E, spec.get("smooth", 6))
    s = Wb.sum(axis=1)
    spine = FOLLOW_BONES.index("DEF-spine.003")
    Wb[s < 1e-3, spine] = 1.0
    Wb /= np.maximum(Wb.sum(axis=1, keepdims=True), 1e-9)
    top = fr.centre[2] + spec.get("top_u", (fr.chin_z - fr.centre[2]) / fr.hcm) * fr.hcm
    bottom = shoulder_line(fr) - spec.get("bottom_m", 0.02)
    t = np.clip((top - V[:, 2]) / max(top - bottom, 1e-6), 0.0, 1.0)
    # Behind the body (hair down the back) at most `back_max`: in a
    # forward lean (the run) the back's hair keeps more of the head's
    # upright hang instead of swinging out with the shoulder blades.
    tco = fr.parts["torso"][0]
    yc = float(np.median(tco[:, 1]))
    back = np.clip((V[:, 1] - yc) / 0.04 + 0.5, 0.0, 1.0)
    mx = spec.get("max", 1.0) + (spec.get("back_max", spec.get("max", 1.0)) - spec.get("max", 1.0)) * back
    b = mx * t * t * (3.0 - 2.0 * t)
    BW = []
    for i in range(len(V)):
        nz = np.nonzero(Wb[i] > 0.02)[0]
        ww = {FOLLOW_BONES[c]: float(Wb[i, c]) for c in nz}
        tot = sum(ww.values()) or 1.0
        BW.append({kk: vv / tot for kk, vv in ww.items()})
    return b, BW


def shoulder_line(fr) -> float:
    """Height of the shoulder tops (the torso's highest points half to three
    quarters of the way out), as the drape measures it."""
    tco = fr.parts["torso"][0]
    half = float(np.percentile(np.abs(tco[:, 0]), 98))
    band = (np.abs(tco[:, 0]) > 0.45 * half) & (np.abs(tco[:, 0]) < 0.75 * half)
    return float(tco[band, 2].max()) if band.any() else float(fr.neck_z)


def lod_weights(fr, chains: dict, joints: dict, V: np.ndarray, F: np.ndarray, part: np.ndarray, rtt: np.ndarray,
                twin: np.ndarray, follow: dict | None = None) -> list[dict]:
    """sway_weights on the LOD's shell and cap; the inner layer's vertices
    take their shell twins' weights exactly (the two sheets sway as one)."""
    main = twin == np.arange(len(V))
    idx = np.nonzero(main)[0]
    Fm = F[main[F].all(axis=1)]
    remap = -np.ones(len(V), dtype=np.int64)
    remap[idx] = np.arange(len(idx))
    w = sway_weights(fr, chains, joints, V[idx], remap[Fm], part[idx], rtt[idx], follow)
    return [w[remap[t]] for t in twin]


def fringe_hem(fr, V: np.ndarray, part: np.ndarray) -> list[list[float]]:
    """The fringe's lower edge seen from the front (hair.fringe_hem's rule
    on the fringe part): per x bin the lowest fringe vertex in front of the
    face, in glTF axes (+Y up, +Z forward), sorted by x."""
    sel = (part == FRINGE) & (V[:, 1] < fr.centre[1] - 3.0 * fr.hcm)
    P = V[sel]
    out = []
    xs = np.linspace(-6.5, 6.5, 17) * fr.hcm
    half = (xs[1] - xs[0]) * 0.5
    for x in xs:
        m = np.abs(P[:, 0] - x) < half
        if not m.any():
            continue
        p = P[m][np.argmin(P[m][:, 2])]
        # The shadow hangs from the hem: never from below the brows (fringe
        # tips reaching past them would shade the eyes).
        z = max(float(p[2]), fr.brow_z + HEM_ABOVE_BROW * fr.hcm)
        out.append([round(float(p[0]), 4), round(z, 4), round(float(-p[1]), 4)])
    return out


def _skin_tree(fr):
    """The whole LOD0 body as check_characters.py merges it (bpy.data.objects
    order) and each face's region; cached on the head frame."""
    if hasattr(fr, "_skin"):
        return fr._skin
    import bpy
    from mathutils.bvhtree import BVHTree

    # Evaluated as the check sees the saved file: its shape keys as saved
    # (the face's), the skeleton at rest.
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    saved = [a.data.pose_position for a in arms]
    for a in arms:
        a.data.pose_position = "REST"
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    verts, polys, regions = [], [], []
    try:
        for o in bpy.data.objects:
            if o.type == "MESH" and o.get("region") and o.get("lod") == 0:
                ev = o.evaluated_get(dg)
                me = ev.to_mesh()
                mw = o.matrix_world
                off = len(verts)
                verts.extend([tuple(mw @ v.co) for v in me.vertices])
                for poly in me.polygons:
                    polys.append(tuple(off + i for i in poly.vertices))
                    regions.append(str(o["region"]))
                ev.to_mesh_clear()
    finally:
        for a, pp in zip(arms, saved):
            a.data.pose_position = pp
        bpy.context.view_layer.update()
    fr._skin = (BVHTree.FromPolygons(verts, polys), regions)
    return fr._skin


def skin_clear(fr, V: np.ndarray, hides: set, gap: float = SKIN_GAP, rounds: int = 3,
               ear_gap: float = EAR_GAP) -> np.ndarray:
    """The skin check's own rule (check_characters.py `penetration`): a vertex
    whose nearest visible skin face has it behind its plane goes out along
    that face's normal to `gap` over it (at most 1.5 cm). The build's clear
    pushes use a stricter inside test that leaves a few of these beside the
    ears' rims and the eyelids."""
    tree, regions = _skin_tree(fr)
    out = V.copy()
    for _ in range(rounds):
        moved = 0
        for i in range(len(out)):
            loc, nrm, idx, dist = tree.find_nearest(Vector(tuple(out[i])), 0.05)
            if loc is None or regions[idx] in hides:
                continue
            h = float((out[i] - np.array(loc)) @ np.array(nrm))
            g = ear_gap if regions[idx] == "ears" else gap
            if h < g * 0.5:
                out[i] = out[i] + np.array(nrm) * min(g - h, 0.015)
                moved += 1
        if not moved:
            break
    return out


def _check_tree(fr):
    """The LOD0 body exactly as check_hair.py measures it in the rest pose:
    every shape key off, evaluated (modifiers included), its polygons
    merged; and each polygon's region. Cached on the head frame."""
    if hasattr(fr, "_check"):
        return fr._check
    import bpy
    from mathutils.bvhtree import BVHTree

    parts = [o for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0]
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    saved_pp = [a.data.pose_position for a in arms]
    saved_keys = []
    for o in parts:
        sk = o.data.shape_keys
        if sk:
            for kb in sk.key_blocks[1:]:
                saved_keys.append((kb, kb.value))
                kb.value = 0.0
    for a in arms:
        a.data.pose_position = "REST"
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    verts, polys, regions = [], [], []
    try:
        for o in parts:
            ev = o.evaluated_get(dg)
            me = ev.to_mesh()
            off = len(verts)
            verts.extend([tuple(o.matrix_world @ v.co) for v in me.vertices])
            for poly in me.polygons:
                polys.append(tuple(off + i for i in poly.vertices))
                regions.append(str(o["region"]))
            ev.to_mesh_clear()
    finally:
        for kb, v in saved_keys:
            kb.value = v
        for a, pp in zip(arms, saved_pp):
            a.data.pose_position = pp
        bpy.context.view_layer.update()
    fr._check = (BVHTree.FromPolygons(verts, polys), regions)
    return fr._check


def ear_clear(fr, V: np.ndarray, gap: float) -> np.ndarray:
    """Shell or inner-cap vertices the clipping check would count inside a visible ear (its
    rule on the body as it evaluates it, skin_clear's tree: there an ear's
    nearest face can face inward) moved out, away from the ear's own
    surface, until it no longer does (at most 2 cm)."""
    if fr.ears is None:
        return V
    tree, regions = _check_tree(fr)
    out = V.copy()

    jit = [np.zeros(3)] + [np.eye(3)[k] * sgn * 0.0005 for k in range(3) for sgn in (1, -1)]

    def inside(p):
        # In the ear by its own surface.
        loc, nrm, _, dist = fr.ears.find_nearest(Vector(tuple(p)), 0.03)
        if loc is not None:
            h = float((p - np.array(loc)) @ np.array(nrm))
            if h < -0.0015 and -h >= 0.5 * dist:
                return True
        # Beside an ear's rim the nearest face is a tie between its two
        # sides, which the check may break either way: any small jitter
        # that reads inside counts.
        for j in jit:
            q = p + j
            loc, nrm, idx, dist = tree.find_nearest(Vector(tuple(q)), 0.05)
            if loc is None or regions[idx] != "ears":
                continue
            h = float((q - np.array(loc)) @ np.array(nrm))
            if h < -0.0015 and -h >= 0.5 * dist:
                return True
        return False

    def flagged(p):
        # The check's own rule only (its nearest ear face can be one facing
        # inward, coincident with the outer one: a point centimetres out of
        # the ear can read inside).
        loc, nrm, idx, dist = tree.find_nearest(Vector(tuple(p)), 0.05)
        if loc is None or regions[idx] != "ears":
            return False
        h = float((p - np.array(loc)) @ np.array(nrm))
        return h < -0.0015 and -h >= 0.5 * dist

    nudges = [np.eye(3)[k] * sgn * m for m in (0.0005, 0.001, 0.002, 0.003) for k in range(3) for sgn in (1, -1)]
    for i in range(len(out)):
        hit = fr.ears.find_nearest(Vector(tuple(out[i])), 0.04)
        if hit[0] is None or not inside(out[i]):
            continue
        loc, nrm, _, dist = fr.ears.find_nearest(Vector(tuple(out[i])), 0.04)
        hh = float((out[i] - np.array(loc)) @ np.array(nrm))
        if hh > 0.004:
            # Out of the ear already: the smallest nudge the check reads
            # clean.
            for dv in nudges:
                if not flagged(out[i] + dv):
                    out[i] = out[i] + dv
                    break
            continue
        d = out[i] - np.array(hit[0])
        en = np.array(hit[1])
        # Out of the ear: along its surface's normal when the point is in
        # it, else straight away from it.
        n = en if (d @ en) < 0 or np.linalg.norm(d) < 1e-6 else d / float(np.linalg.norm(d))
        for _ in range(15):
            out[i] = out[i] + n * 0.002
            if not inside(out[i]):
                break
    return out


def flow_lines(fr, V: np.ndarray, sl: dict) -> np.ndarray:
    """0..1 per vertex: the fine flow lines of hair combed toward a
    gathering point (`sl`: a braid's `sleek` settings, prepare_hair_meshy
    sleek_shell): `lines` narrow lines round a full turn about the point
    `to` (head cm), fading in from `fade_in` head cm round it and out in
    front of `f_front`."""
    hcm = fr.hcm
    G = fr.centre + np.array([sl["to"][0], -sl["to"][1], sl["to"][2]]) * hcm
    dG = (G - fr.centre) / np.linalg.norm(G - fr.centre)
    e1 = np.array([1.0, 0.0, 0.0]) - dG * dG[0]
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(dG, e1)
    if e2[2] < 0:
        e2 = -e2
    v = V - G
    a = np.arctan2(v @ e1, v @ e2)
    rho = np.linalg.norm(v, axis=1) / hcm
    # Narrow lines, slightly uneven in strength.
    line = (0.5 + 0.5 * np.cos(sl["lines"] * a)) ** sl.get("sharp", 6)
    line *= 0.75 + 0.25 * np.cos(sl["lines"] * 0.37 * a + 1.3)
    f = -(V[:, 1] - fr.centre[1]) / hcm
    w = np.clip((sl.get("f_front", 0.5) - f) / sl.get("f_soft", 3.0), 0.0, 1.0)
    fade = np.clip((rho - sl.get("fade_in", 2.5)) / 2.5, 0.0, 1.0) * w * w * (3 - 2 * w)
    return line * fade


def build_lods(fr, src: dict, st: dict, cap_hem, cap: bool, occ_extra: list, lines=None):
    """LOD0-LOD3 of one variant: per LOD (V, F, face kinds, normals, _SHADE,
    root-to-tip, parts, twin), the inner cap appended and, on the first
    INNER_LODS, the inner layer (the shell's vertices again, moved in, its
    faces flipped). `twin` is per vertex the shell or cap vertex whose sway
    weights it takes (itself, or the shell vertex an inner vertex copies).
    With the style's `clip`, the clipped band (on `lines`, by default
    clip_lines of `src`) joins the cap, the cap's faces under it dropped."""
    from . import hair as HR

    if lines is None and st.get("clip"):
        lines = clip_lines(fr, src, st["clip"])
    budgets = BUDGETS[st["length"]]
    covers = st.get("covers_ears", False)
    fine = min(st.get("trunk_clear", 0.012), st.get("fine_clear", 0.01))
    hold = _smoothstep(0.85, 1.0, src["root_to_tip"])
    curV, curF, cur_hold = src["V"], src["F"], hold
    lods = _Lods()
    for lod in range(4):
        # (The hat variant's inner cap as the plain style's: the hats are
        # fitted over the hair, nothing is pressed.)
        Vc, Fc = inner_cap_mesh(fr, cap_hem, lod)
        band = clip_band_mesh(fr, lines, st["clip"], lod) if lines is not None else None
        if band is not None:
            Vc, Fc = drop_cap_under_band(fr, Vc, Fc, lines)
            nb0 = len(Vc)
            Fc = np.vstack([Fc, band[1] + nb0])
        inner = lod < INNER_LODS
        target = (budgets[lod] - len(Fc) * (2 if inner else 1)) // (2 if inner else 1)
        Vd, Fd = decimate_or_thin(curV, curF, target, protect=cur_hold, factor=1.0)
        # Crumbs a deep decimation leaves of thin strands go.
        comp = MO.components(len(Vd), MO.edges(Fd))
        ids, cnt = np.unique(comp, return_counts=True)
        big = np.isin(comp, ids[cnt >= MIN_ISLAND])
        if not big.all():
            Vd, Fd, _ = MO.submesh(Vd, Fd, big[Fd].all(axis=1))
        N, rtt, part = transfer_attrs(src, Vd)
        # On thin strands a decimated vertex can lie nearest the source's
        # far side and take its normal, facing away: its own instead.
        own = MO.vertex_normals(Vd, Fd)
        ok = np.linalg.norm(own, axis=1) > 0.5
        N = np.where((ok & ((N * own).sum(axis=1) < 0.0))[:, None], own, N)
        Vd = push_out(fr, Vd, covers, fine, rounds=2)
        # Inner cap: below the hair, spherical proxy normals; off the ears
        # where the hair leaves them free.
        if not covers:
            Vc = push_out(fr, Vc, covers, fine, rounds=2)
        hides = {"scalp", "ears"} if covers else {"scalp"}
        Vd = skin_clear(fr, Vd, hides, ear_gap=st.get("ear_gap", EAR_GAP))
        Vc = skin_clear(fr, Vc, hides, ear_gap=st.get("ear_gap", EAR_GAP))
        if not covers:
            Vd = ear_clear(fr, Vd, st.get("ear_gap", EAR_GAP))
        Nc = HR.proxy_normals(fr, Vc)
        is_band = np.zeros(len(Vc), dtype=bool)
        if band is not None:
            # The band lies 2.5-4.5 mm on the skin: only the skin's own rule
            # (no 3 mm head push; under hats the per-hat fit keeps it, as the
            # inner cap).
            Vb = skin_clear(fr, band[0], hides, ear_gap=st.get("ear_gap", EAR_GAP))
            # Its comb ridges shade with their own facets (half the head's
            # smooth field).
            gn = MO.vertex_normals(Vb, band[1])
            Nb = gn * 0.55 + HR.proxy_normals(fr, Vb) * 0.45
            Nb /= np.maximum(np.linalg.norm(Nb, axis=1, keepdims=True), 1e-12)
            Vc = np.vstack([Vc, Vb])
            Nc = np.vstack([Nc, Nb])
            is_band = np.concatenate([is_band, np.ones(len(Vb), dtype=bool)])
        nc = len(Vd)
        V = np.vstack([Vd, Vc])
        F = np.vstack([Fd, Fc + nc])
        Nn = np.vstack([N, Nc])
        rtt_all = np.concatenate([rtt, np.where(is_band, 1.0, 0.0)])
        part_all = np.concatenate([part, np.full(len(Vc), CROWN, dtype=part.dtype)])
        is_cap = np.zeros(len(V), dtype=bool)
        is_cap[nc:] = ~is_band
        # The inner cap and the clipped band: kept under every hat.
        scalp = np.zeros(len(V), dtype=bool)
        scalp[nc:] = True
        sh = shade(fr, V, F, rtt_all, is_cap, occ_extra)
        sl = (st.get("braid") or {}).get("sleek")
        if sl and sl.get("lines") and lod < 2:
            # Fine flow lines over the sleek hair, converging on the braid's
            # gathering point: darker streaks in its own shade.
            hair = np.arange(len(V)) < nc
            hair &= (part_all != TAIL) & (part_all != TIE)
            g = flow_lines(fr, V, sl) * hair
            sh[:, 0] = np.maximum(sh[:, 0] * (1.0 - sl.get("shade", 0.4) * g), HR.SHADE_FLOOR)
        if st.get("crease_dark"):
            # The sculpt's grooves (combed clumps, the gaps between spikes)
            # drawn as strokes: the concave creases darker, so the toon
            # step puts their bottoms in shadow before the clumps round them.
            Es = MO.edges(Fd)
            cv = MO.smooth_values(MO.mean_curvature(Vd, Fd, Es), Es, 1)
            cn = cv / max(float(np.percentile(np.abs(cv), 90)), 1e-12)
            sh[:nc, 0] = np.maximum(sh[:nc, 0] * (1.0 - st["crease_dark"] * _smoothstep(0.2, 1.0, cn)), HR.SHADE_FLOOR)
        if band is not None:
            # The clipped hair: the hair's own colour (the same atlas cell
            # and tint as the top: never a lighter or greyer mix), a shade
            # darker (`shade` on the comb's crests, `valley_x` in its
            # valleys: under the toon step the grain shows as fine strokes
            # where the light falls across the side of the head). The
            # occlusion only tempers it (the band lies under the top's
            # overhang and on the skin).
            spec = st["clip"]
            rows = nc + np.nonzero(is_band)[0]
            x0 = sh[rows, 0]
            x = np.where(band[2] > 0.5, spec.get("valley_x", 0.45), spec.get("shade", 0.8)) * (0.85 + 0.15 * x0)
            # Under the top's hem it is the deep shade the cap was.
            f0, f1 = spec.get("fade", (0.15, 0.6))
            x = INNER_SHADE + (x - INNER_SHADE) * _smoothstep(f0, f1, band[3])
            sh[rows, 0] = np.clip(x, HR.SHADE_FLOOR, 1.0)
            sh[rows, 2] = INNER_Z
        if st.get("no_ring"):
            # No angel ring: the band centre moved off the hair (a messy
            # updo whose top reads matte, the reference's look).
            sh[:, 2] = INNER_Z
        twin = np.arange(len(V))
        if inner:
            # The inner layer: seen only where the shell is seen from behind
            # (a lock's underside, a strand's back, the inside of an open
            # edge), it shows the hair in its own shadow instead of the
            # background. Its faces are flipped but its shading normals are
            # the shell's own (smooth, proxy-led): it takes the light as the
            # hair mass does at that spot, so a fold seen from behind on the
            # back of the head stays in the back's shadow (flipped normals
            # lit it from a front key) and reads as the same hair, darker
            # (INNER_SHADE), with no ring; the shell's weights. The inner cap
            # gets its flipped copy too (looking up under the hem beside an
            # ear shows its inside, with the hidden scalp behind).
            Vi0 = inner_layer(fr, Vd, Fd, covers)
            Vi = skin_clear(fr, Vi0, hides, ear_gap=st.get("ear_gap", EAR_GAP))
            # Where the skin rule or the hat contract would move an inner
            # vertex (the shell within a gap of the skin, beside an ear, on
            # the neck; a lock's underside at the hat line), it stays on its
            # shell vertex instead, which already keeps both.
            moved = np.linalg.norm(Vi - Vi0, axis=1) > 1e-7
            if not covers and fr.ears is not None:
                # Beside a visible ear's thin rim the layer under the shell
                # can end up inside it (the ears' saved shape keys): it stays
                # on the shell there.
                for i in np.nonzero(~moved)[0]:
                    hit = fr.ears.find_nearest(Vector(tuple(Vd[i])), INNER_EAR)
                    if hit[0] is not None:
                        moved[i] = True
            Vi[moved] = Vd[moved]
            off = len(V)
            ncap = len(Vc)
            V = np.vstack([V, Vi, Vc])
            F = np.vstack([F, Fd[:, [0, 2, 1]] + off, Fc[:, [0, 2, 1]] + off + nc])
            Nn = np.vstack([Nn, N, Nn[nc:nc + ncap]])
            rtt_all = np.concatenate([rtt_all, rtt, rtt_all[nc:nc + ncap]])
            part_all = np.concatenate([part_all, part, part_all[nc:nc + ncap]])
            shi = sh[:nc + ncap].copy()
            shi[:, 0] = np.minimum(shi[:, 0], INNER_SHADE)
            shi[:, 2] = INNER_Z
            sh = np.vstack([sh, shi])
            twin = np.concatenate([twin, np.arange(nc + ncap)])
            scalp = np.concatenate([scalp, np.zeros(nc, dtype=bool), np.ones(ncap, dtype=bool)])
        if not covers:
            # Last: nothing the clipping check counts inside a visible ear
            # (shell, inner layer or inner cap); a moved vertex's inner twin
            # moves with it.
            delta = ear_clear(fr, V, st.get("ear_gap", EAR_GAP)) - V
            # Per surface point the largest move asked by it or its twin.
            dm = np.zeros_like(V)
            mag = np.zeros(len(V))
            for i in np.nonzero(np.linalg.norm(delta, axis=1) > 1e-9)[0]:
                m = float(np.linalg.norm(delta[i]))
                if m > mag[twin[i]]:
                    mag[twin[i]] = m
                    dm[twin[i]] = delta[i]
            V = V + dm[twin]
        tie_face = (part_all[F] == TIE).all(axis=1)
        kinds = ["tie" if t else "hair" for t in tie_face]
        lods.append((V, F, kinds, Nn, sh, rtt_all, part_all, twin))
        lods.scalp.append(scalp)
        curV, curF = Vd, Fd
        cur_hold = _smoothstep(0.85, 1.0, rtt)
    return lods


def build_style(bb, style_def: dict, assets: str, manifest: dict, report: dict | None = None) -> dict:
    """Build, export and register one style from its source mesh (both
    variants, four LODs) through hair.py's export path."""
    import time

    from . import atlas as A
    from . import content
    from . import export as EX
    from . import hair as HR
    from . import hair_frame as HF
    from . import pipeline as PL
    from . import rig as RG

    t0 = time.time()
    fr = HF.HeadFrame(bb)
    asset = style_def["asset"]
    short = asset.split("/")[-1]
    src0 = load_source(source_path(short, bb.id))
    st = source_style(short, src0["meta"])
    clean = {}
    src0 = clean_source(fr, src0, st, clean)
    PL.log(f"{short}: clean-up {clean}")
    occ_extra = [(fr.parts[r][0], fr.parts[r][2]) for r in ("head", "scalp", "neck", "torso") if r in fr.parts]
    out = {"clean": clean}
    coll = PL.collection(f"{bb.id}_hair")
    # A separate hat variant only where the tails change under a hat (a
    # ponytail tied high, twin tails); every other style wears its own
    # file under hats, with the per-hat fit (round 4 of 2026-11-07).
    need_cap = cap_needed(st, src0)
    for cap in ((False, True) if need_cap else (False,)):
        src = cap_source(fr, src0, st) if cap else src0
        hatted = cap or not need_cap
        cap_hem = inner_cap_hem(fr, src)
        lods = build_lods(fr, src, st, cap_hem, cap, occ_extra)
        V0, F0, _, _, _, rtt0, part0, twin0 = lods[0]
        main0 = twin0 == np.arange(len(V0))
        # Twin tails hang out from under any brim: the fit and the check
        # leave them out (`hat_skip`, kept in the .blend only).
        skips = [((l[6] == TAIL) | (l[6] == TIE)) if (cap and st.get("cap_press_tails")) else np.zeros(len(l[0]), dtype=bool)
                 for l in lods]
        fits = hat_fits(fr, V0, F0, lods.scalp[0], skips[0]) if hatted else None
        joints = chain_joints(fr, st.get("chains", {}), V0[main0], part0[main0], rtt0[main0])
        hides = ["scalp", "ears"] if st.get("covers_ears") else ["scalp"]
        objs = []
        tri = {}
        for lod, (V, F, kinds, N, sh, rtt, part, twin) in enumerate(lods):
            weights = lod_weights(fr, st.get("chains", {}), joints, V, F, part, rtt, twin, st.get("follow"))
            hl = HR.HairLod(V, [tuple(int(i) for i in f) for f in F], kinds, N, sh, weights, None, None)
            oname = f"hair_{short}{'_cap' if cap else ''}_{bb.id}_lod{lod}"
            o = HR.to_object(oname, hl, bb.material, coll, A)
            # Which shell or cap vertex each vertex copies (itself, or for the
            # inner layer its twin): the checks count surface points. No
            # leading underscore, so the glTF export leaves it out.
            tw = o.data.attributes.new("inner_twin", "INT", "POINT")
            tw.data.foreach_set("value", twin.astype(np.int32))
            if hatted:
                # The inner cap, which no hat drops (exported: the client
                # keeps it when it drops the hair a hat's crown covers).
                sc = o.data.attributes.new(SCALP_ATTRIBUTE, "FLOAT", "POINT")
                sc.data.foreach_set("value", lods.scalp[lod].astype(np.float32))
                o["hat_fit"] = json.dumps(fits)
                hs = o.data.attributes.new("hat_skip", "INT", "POINT")
                hs.data.foreach_set("value", skips[lod].astype(np.int32))
            o["asset"] = asset
            o["lod"] = lod
            o["hides"] = hides
            o["cap"] = cap
            o["toon"] = "hair"
            o["body"] = bb.id
            o["source"] = "meshy"
            o["guide"] = st["guide"]
            o["sway_joints"] = {c: [[round(float(x), 5) for x in j] for j in J] for c, J in sorted(joints.items())}
            if lod == 0:
                o["fringe_hem"] = fringe_hem(fr, V[main0], part[main0]) or [[0.0, float(fr.brow_z), 0.0]]
                o["fringe_shadow"] = dict(HR.FRINGE_SHADOW)
                o["sway"] = sorted(joints)
            RG.bind(o, bb.skel)
            objs.append(o)
            tri[f"lod{lod}"] = int(len(F))
        rel = content.asset_file(asset, bb.id, cap=cap)
        HR.export_hair_glb(os.path.join(assets, rel), bb.skel, objs, joints)
        entry = manifest["assets"].get(asset)
        if entry is None or entry.get("kind") != "hair":
            entry = {}
            manifest["assets"][asset] = entry
        entry.update({"kind": "hair", "style_id": style_def["id"], "body": bb.id, "hides": hides,
                      "tint": ["hair_colour", None, None], "sway": sorted(joints), "length": st["length"],
                      "source": "meshy", "budgets": dict(zip(("lod0", "lod1", "lod2", "lod3"), BUDGETS[st["length"]]))})
        entry.setdefault("cap_files" if cap else "files", {})[bb.id] = rel
        if hatted:
            entry.setdefault("hat_fit", {})[bb.id] = fits
        if not need_cap:
            # No hat variant: its old file and entries go.
            old = os.path.join(assets, content.asset_file(asset, bb.id, cap=True))
            if os.path.exists(old):
                os.remove(old)
            for key in ("cap_files", "cap_triangles"):
                entry.get(key, {}).pop(bb.id, None)
                if key in entry and not entry[key]:
                    del entry[key]
        entry.setdefault("cap_triangles" if cap else "triangles", {})[bb.id] = tri
        if not cap:
            entry.setdefault("bounds", {})[bb.id] = EX.bounds([objs[0]])
        for o in objs:
            o.hide_set(True)
            o.hide_render = True
        out["cap" if cap else "hair"] = {"triangles": tri, "file": rel,
                                         "bytes": os.path.getsize(os.path.join(assets, rel)),
                                         "sway_bones": sum(len(HR.chain_bone_names(c)) for c in joints)}
        PL.log(f"exported {rel} {tri} (meshy)")
    out["seconds"] = round(time.time() - t0, 1)
    out["source"] = "meshy"
    if report is not None:
        report[asset] = out
    return out


def build(bb, style_def: dict, assets: str, manifest: dict, report: dict | None = None, source: str | None = None) -> dict:
    """One style through its chosen builder: from the Meshy source mesh when
    the style's source (or `source`) is "meshy" and the mesh is there, else
    the lock builder (hair.build_style)."""
    from . import hair as HR
    from . import pipeline as PL

    name = style_def["asset"].split("/")[-1]
    if use_meshy(name, bb.id, source):
        return build_style(bb, style_def, assets, manifest, report)
    if name in MESHY_ONLY:
        raise FileNotFoundError(f"{name} is made from its Meshy source mesh only (no locks): "
                                f"{source_path(name, bb.id)} is missing (art/characters/hair_meshy)")
    want = source or STYLES.get(name, {}).get("source", "locks")
    if want == "meshy":
        PL.log(f"{name}: no source mesh at {source_path(name, bb.id)}; building its locks")
    return HR.build_style(bb, style_def, assets, manifest, report)
