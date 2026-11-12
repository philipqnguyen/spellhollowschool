"""Reads the JSON chunk of a .glb (no bpy): used by tests and the check script."""

from __future__ import annotations

import json
import struct


def read_glb_json(path: str) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError(f"{path}: not a glb")
    chunk_len, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A:
        raise ValueError(f"{path}: first chunk is not JSON")
    return json.loads(data[20:20 + chunk_len].decode("utf-8"))


def summary(path: str) -> dict:
    g = read_glb_json(path)
    nodes = g.get("nodes", [])
    meshes = g.get("meshes", [])
    out = {
        "nodes": len(nodes),
        "skins": [{"joints": len(s["joints"])} for s in g.get("skins", [])],
        "animations": [a.get("name") for a in g.get("animations", [])],
        "meshes": [],
        "images": len(g.get("images", [])),
        "materials": [m.get("name") for m in g.get("materials", [])],
    }
    for n in nodes:
        if "mesh" not in n:
            continue
        m = meshes[n["mesh"]]
        prims = m["primitives"]
        out["meshes"].append({
            "node": n.get("name"),
            "extras": n.get("extras", {}),
            "attributes": sorted(prims[0]["attributes"].keys()),
            "targets": len(prims[0].get("targets", [])),
            "target_names": m.get("extras", {}).get("targetNames", []),
            "primitives": len(prims),
        })
    return out


def joint_names(path: str) -> list[str]:
    g = read_glb_json(path)
    nodes = g["nodes"]
    return [nodes[j]["name"] for j in g["skins"][0]["joints"]]
