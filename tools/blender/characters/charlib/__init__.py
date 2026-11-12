"""Scripted character pipeline: base bodies, rig, animations, hair and gear.

Modules without `bpy` (sdf, proportions, skeleton, head and the geometry parts
of body/gear/hair) are plain numpy so their maths can be unit-tested; the bpy
side (meshutil, rig, anim, export, render) turns the data into Blender objects
and glTF files. See tools/blender/README.md ("Characters").
"""
