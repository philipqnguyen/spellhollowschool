"""Extracts the walk and run motion from Quaternius's Universal Animation Library.

    python3 -I tools/blender/characters/extract_ual.py --glb <path>/UAL1.glb

(any Python 3 with NumPy, e.g. Blender's bundled one). Reads the in-place
library (`UAL1.glb`, not `UAL1_RM.glb`, which has root motion) and its
`License.txt` (CC0 1.0, by Quaternius), never writing next to them, and
writes `ual/ual1_walk_sprint.json` and `ual/License.txt` beside this script:
the source's rest transforms of the bones the retarget reads and the raw
keys (times, translations, rotations; glTF axes) of `Walk_Loop` and
`Sprint_Loop`, nothing else. `charlib/ual.py` retargets them onto our rig.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "ual")
CLIPS = ("Walk_Loop", "Sprint_Loop")
SIDES = ("l", "r")
# The bones the retarget reads (charlib/ual.py), with every ancestor up to the
# scene root; `middle_01` and `ball_leaf` only for their rest positions.
BONES = ["root", "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head"] + [
    f"{b}_{s}" for s in SIDES for b in ("clavicle", "upperarm", "lowerarm", "hand", "middle_01", "thigh", "calf", "foot", "ball", "ball_leaf")
]


def load(path: str):
    raw = open(path, "rb").read()
    n = struct.unpack_from("<I", raw, 12)[0]
    return json.loads(raw[20:20 + n]), raw[28 + n:], raw


def values(g: dict, b: bytes, i: int) -> np.ndarray:
    a = g["accessors"][i]
    v = g["bufferViews"][a["bufferView"]]
    assert a["componentType"] == 5126 and "sparse" not in a
    width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[a["type"]]
    return np.ndarray((a["count"], width), dtype="<f4", buffer=b, offset=v.get("byteOffset", 0) + a.get("byteOffset", 0),
                      strides=(v.get("byteStride", width * 4), 4)).copy()


def r6(a) -> list:
    return [round(float(x), 6) for x in a]


def extract(glb: str) -> dict:
    g, b, raw = load(glb)
    parents = {c: i for i, n in enumerate(g["nodes"]) for c in n.get("children", [])}
    index = {n["name"]: i for i, n in enumerate(g["nodes"])}
    wanted = set()
    for name in BONES:
        i = index[name]
        while i is not None:
            wanted.add(i)
            i = parents.get(i)
    nodes = {}
    for i in sorted(wanted):
        n = g["nodes"][i]
        nodes[n["name"]] = {"parent": g["nodes"][parents[i]]["name"] if i in parents else None,
                            "translation": r6(n.get("translation", [0, 0, 0])), "rotation": r6(n.get("rotation", [0, 0, 0, 1])),
                            "scale": r6(n.get("scale", [1, 1, 1]))}
    clips = {}
    for a in g["animations"]:
        if a["name"] not in CLIPS:
            continue
        times, channels = None, {}
        for c in a["channels"]:
            node = c["target"]["node"]
            path = c["target"]["path"]
            if node not in wanted or path == "scale":
                continue
            s = a["samplers"][c["sampler"]]
            assert s.get("interpolation", "LINEAR") == "LINEAR"
            t = values(g, b, s["input"])[:, 0]
            if times is None:
                times = t
            assert np.allclose(t, times), "one key time list per clip"
            channels.setdefault(g["nodes"][node]["name"], {})[path] = [r6(v) for v in values(g, b, s["output"])]
        clips[a["name"]] = {"times": r6(times), "channels": dict(sorted(channels.items()))}
    assert set(clips) == set(CLIPS), clips.keys()
    return {
        "source": {"file": os.path.basename(glb), "sha256": hashlib.sha256(raw).hexdigest(),
                   "work": "Universal Animation Library (Pro), UAL1", "author": "Quaternius", "licence": "CC0 1.0",
                   "url": "https://quaternius.com", "variant": "in place (no root motion)"},
        "axes": "glTF: +Y up, the character faces +Z, its left is +X",
        "nodes": nodes,
        "clips": clips,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--glb", required=True, help="UAL1.glb (the in-place library)")
    p.add_argument("--license", default="", help="the pack's License.txt (default: two folders up from the glb)")
    p.add_argument("--out", default=OUT_DIR)
    a = p.parse_args()
    if os.path.basename(a.glb).upper().endswith("_RM.GLB"):
        raise SystemExit("use the in-place library (UAL1.glb), not the root-motion one")
    lic = a.license or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(a.glb))), "License.txt")
    text = open(lic, "rb").read()  # copied byte for byte
    assert b"CC0 1.0 Universal" in text and b"Quaternius" in text
    data = extract(a.glb)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "ual1_walk_sprint.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
        f.write("\n")
    with open(os.path.join(a.out, "License.txt"), "wb") as f:
        f.write(text)
    print({k: len(v["times"]) for k, v in data["clips"].items()}, len(data["nodes"]), "nodes")


if __name__ == "__main__":
    main()
