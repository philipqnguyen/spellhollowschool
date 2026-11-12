// Test fixtures: tiny skinned .glb files built in memory, shaped like the art
// pipeline's exports (Rigify bone names, regions, extras, dye masks). Only
// imported by the unit tests.

import { glbBuilder } from "./gltf";

export interface FixtureOptions {
  /** Joint order in the skin; the art may list joints in any order. */
  jointOrder?: string[];
  /** Node names of the mesh primitives (regions come from names). */
  meshName?: string;
  extras?: Record<string, unknown>;
  colors?: boolean;
  morph?: string;
  lodNode?: boolean;
  animation?: string;
  rigidProp?: boolean;
  /** Texture coordinates and an embedded (fake) atlas image. */
  texture?: boolean;
  /** Put an armature node above the root joint, offset by this much. */
  armatureOffset?: [number, number, number];
  /** A `_SHADE` attribute (4 normalised bytes per vertex, the four vertices' values). */
  shade?: [number, number, number, number][];
  /** Extras on the mesh (fringe_hem, fringe_shadow, ...). */
  meshExtras?: Record<string, unknown>;
  /** Two sway joints, `hair.back.01` and `.02`, hanging behind DEF-spine.006 (a hair file's chain). */
  sway?: boolean;
}

/** The fixture's sway chain (with `sway`): rest positions and parents. */
export const FIXTURE_SWAY = ["hair.back.01", "hair.back.02"];

/** Bones: root at the origin, DEF-spine 1 m up, DEF-spine.006 0.5 m above it. */
export const FIXTURE_BONES = ["root", "DEF-spine", "DEF-spine.006"];
const BONE_POS: Record<string, [number, number, number]> = { root: [0, 0, 0], "DEF-spine": [0, 1, 0], "DEF-spine.006": [0, 1.5, 0], "hair.back.01": [0, 1.5, -0.1], "hair.back.02": [0, 1.3, -0.1] };
const PARENT: Record<string, string | null> = { root: null, "DEF-spine": "root", "DEF-spine.006": "DEF-spine", "hair.back.01": "DEF-spine.006", "hair.back.02": "hair.back.01" };

/**
 * A skinned quad (two triangles) around the spine: the bottom vertices on
 * DEF-spine, the top ones on DEF-spine.006.
 */
export function fixtureGlb(o: FixtureOptions = {}): ArrayBuffer {
  const order = o.jointOrder ?? (o.sway ? [...FIXTURE_BONES, ...FIXTURE_SWAY] : FIXTURE_BONES);
  const pre = o.armatureOffset;
  // Nodes: joints in `order`, then the mesh node(s), then optionally an armature.
  const nodes: Record<string, unknown>[] = order.map((name) => {
    const parent = PARENT[name];
    const pos = BONE_POS[name]!;
    const ppos = parent ? BONE_POS[parent]! : [0, 0, 0];
    return { name, translation: [pos[0] - ppos[0], pos[1] - ppos[1], pos[2] - ppos[2]] };
  });
  order.forEach((name, i) => {
    const kids = order.map((n, j) => (PARENT[n] === name ? j : -1)).filter((j) => j >= 0);
    if (kids.length) nodes[i]!.children = kids;
  });
  const b = glbBuilder({ asset: { version: "2.0", extras: o.extras } });
  const json = b.json;
  json.nodes = nodes as never;

  const positions = new Float32Array([-0.2, 1, 0, 0.2, 1, 0, 0.2, 1.5, 0, -0.2, 1.5, 0]);
  const normals = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1]);
  const spine = order.indexOf("DEF-spine");
  const head = order.indexOf("DEF-spine.006");
  const joints = new Uint16Array([spine, 0, 0, 0, spine, 0, 0, 0, head, 0, 0, 0, head, 0, 0, 0]);
  const weights = new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]);
  const indices = new Uint16Array([0, 1, 2, 0, 2, 3]);
  const attributes: Record<string, number> = {
    POSITION: b.add(positions, "VEC3"),
    NORMAL: b.add(normals, "VEC3"),
    JOINTS_0: b.add(joints, "VEC4"),
    WEIGHTS_0: b.add(weights, "VEC4"),
  };
  if (o.shade) attributes._SHADE = b.add(new Uint8Array(o.shade.flat().map((v) => Math.round(v * 255))), "VEC4", { normalized: true });
  if (o.colors) attributes.COLOR_0 = b.add(new Uint8Array([255, 0, 0, 255, 255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255]), "VEC4", { normalized: true });
  const targets = o.morph ? [{ POSITION: b.add(new Float32Array([0.1, 0, 0, 0.1, 0, 0, 0.1, 0, 0, 0.1, 0, 0]), "VEC3") }] : undefined;
  json.materials = [{ name: "skin", pbrMetallicRoughness: { baseColorFactor: [1, 1, 1, 1] } }];
  if (o.texture) {
    attributes.TEXCOORD_0 = b.add(new Float32Array([0.1, 0.9, 0.2, 0.9, 0.2, 0.8, 0.1, 0.8]), "VEC2");
    const image = b.add(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3, 4]), "SCALAR");
    json.images = [{ bufferView: json.accessors![image]!.bufferView!, mimeType: "image/png" }];
    json.textures = [{ source: 0 }];
    json.materials[0]!.pbrMetallicRoughness!.baseColorTexture = { index: 0 };
  }
  const meshExtras = o.morph || o.meshExtras ? { ...(o.morph ? { targetNames: [o.morph] } : {}), ...o.meshExtras } : undefined;
  json.meshes = [{ name: "mesh", primitives: [{ attributes, indices: b.add(indices, "SCALAR"), material: 0, targets }], extras: meshExtras }];

  // Inverse bind matrices: translations by minus each joint's world position.
  const ibm = new Float32Array(order.length * 16);
  order.forEach((name, i) => {
    const p = BONE_POS[name]!;
    const off = pre ?? [0, 0, 0];
    ibm.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -(p[0] + off[0]), -(p[1] + off[1]), -(p[2] + off[2]), 1], i * 16);
  });
  json.skins = [{ joints: order.map((_, i) => i), inverseBindMatrices: b.add(ibm, "MAT4") }];
  const meshNode = nodes.length;
  nodes.push({ name: o.meshName ?? "body_a_torso", mesh: 0, skin: 0, extras: o.extras ? undefined : undefined });
  if (o.lodNode) nodes.push({ name: `${o.meshName ?? "body_a_torso"}_lod1`, mesh: 0, skin: 0 });
  if (o.rigidProp) {
    // An unskinned mesh under DEF-spine.006, 0.1 m above it.
    const prop = nodes.length;
    nodes.push({ name: "prop", mesh: 0, translation: [0, 0.1, 0] });
    const headNode = nodes[head]!;
    headNode.children = [...((headNode.children as number[]) ?? []), prop];
  }
  const roots = [order.indexOf("root"), meshNode];
  if (pre) {
    const arm = nodes.length;
    nodes.push({ name: "Armature", translation: pre, children: [order.indexOf("root")] });
    roots[0] = arm;
  }
  json.scenes = [{ nodes: roots }];
  json.scene = 0;

  if (o.animation) {
    const times = new Float32Array([0, 1]);
    const s = Math.sin(0.25);
    const rot = new Float32Array([0, 0, 0, 1, s, 0, 0, Math.cos(0.25)]); // 0 → 0.5 rad about X
    json.animations = [
      {
        name: o.animation,
        channels: [{ sampler: 0, target: { node: spine, path: "rotation" } }],
        samplers: [{ input: b.add(times, "SCALAR"), output: b.add(rot, "VEC4"), interpolation: "LINEAR" }],
      },
    ];
  }
  return b.build();
}
