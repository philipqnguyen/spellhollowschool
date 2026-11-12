// The guardian animals' meshes (spells to level 20, GUARDIAN): the six
// animals of `assets/guardian/guardian.glb` (tools/blender/guardian,
// build_guardian.py) and its manifest.json. Each animal's part nodes
// `guardian_<animal>_<part>` (origins at their joints) are merged into one
// mesh in the animal's frame whose vertices carry their part's index (a
// `part` attribute), plus the part's pivot from where its node sits: the
// silver shader moves each part by its own matrix (rig.ts, material.ts), so
// a guardian is one draw. Each companion is a clone sharing its animal's
// geometry.
//
// Until the glb loads (or if it is missing: no Blender) a code-built
// stand-in of the same parts (ellipsoids) takes its place.

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import { Matrix, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { VertexData } from "@babylonjs/core/Meshes/mesh.vertexData";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import type { Scene } from "@babylonjs/core/scene";
import type { AnimalKey, Gait } from "./follow";
import { isRole, MAX_PARTS, type Rig, type Role } from "./rig";
import { assetUrl } from "../../assets";

export const ANIMALS: readonly AnimalKey[] = [
  "hare",
  "fox",
  "cat",
  "owl",
  "otter",
  "horse",
];
const GAIT_NAMES: readonly Gait[] = [
  "hop",
  "trot",
  "glide",
  "fly",
  "swim",
  "gallop",
];

/** One animal as the manifest describes it. */
export interface AnimalSpec {
  parts: Role[];
  nodes: Record<string, string>;
  /** x across, y up, z long (m). */
  size: [number, number, number];
  gait: Gait;
  /** Above the holder's feet (m): the owl flies at the shoulder, the otter swims at the knee. */
  followHeight: number;
  /** Joints in the glb's frame (right-handed, facing +z). */
  pivots: Record<string, [number, number, number]>;
}

export type GuardianManifest = Record<AnimalKey, AnimalSpec>;

function num(v: unknown, what: string): number {
  if (typeof v !== "number" || !Number.isFinite(v))
    throw new Error(`guardian manifest: ${what} is not a number`);
  return v;
}

function vec3(v: unknown, what: string): [number, number, number] {
  if (!Array.isArray(v) || v.length !== 3)
    throw new Error(`guardian manifest: ${what} is not [x, y, z]`);
  return [num(v[0], what), num(v[1], what), num(v[2], what)];
}

/** Reads manifest.json (throws on anything malformed: the stand-ins are used then). */
export function parseManifest(json: unknown): GuardianManifest {
  const root = json as { animals?: Record<string, unknown> } | null;
  if (
    !root ||
    typeof root !== "object" ||
    !root.animals ||
    typeof root.animals !== "object"
  )
    throw new Error("guardian manifest: no animals");
  const out = {} as GuardianManifest;
  for (const key of ANIMALS) {
    const a = root.animals[key] as Record<string, unknown> | undefined;
    if (!a) throw new Error(`guardian manifest: no ${key}`);
    const parts = a.parts;
    if (!Array.isArray(parts) || parts.length === 0 || parts.length > MAX_PARTS)
      throw new Error(`guardian manifest: ${key} parts`);
    for (const p of parts)
      if (typeof p !== "string" || !isRole(p))
        throw new Error(`guardian manifest: ${key} part ${String(p)}`);
    if (!parts.includes("body"))
      throw new Error(`guardian manifest: ${key} has no body`);
    const gait = a.gait as Gait;
    if (!GAIT_NAMES.includes(gait))
      throw new Error(`guardian manifest: ${key} gait ${String(a.gait)}`);
    const nodes = (a.nodes ?? {}) as Record<string, string>;
    const pivotsIn = (a.pivots ?? {}) as Record<string, unknown>;
    const pivots: Record<string, [number, number, number]> = {};
    for (const p of parts as string[]) {
      if (typeof nodes[p] !== "string")
        throw new Error(`guardian manifest: ${key} ${p} has no node`);
      pivots[p] = vec3(pivotsIn[p], `${key} ${p} pivot`);
    }
    out[key] = {
      parts: parts as Role[],
      nodes,
      size: vec3(a.size_m, `${key} size`),
      gait,
      followHeight: num(a.follow_height_m ?? 0, `${key} follow height`),
      pivots,
    };
  }
  return out;
}

/** One animal ready to draw: its merged template mesh (disabled) and rig. */
export interface AnimalModel {
  key: AnimalKey;
  template: Mesh;
  rig: Rig;
  gait: Gait;
  followHeight: number;
  size: [number, number, number];
  /** Its middle above its origin (m), unscaled: a guarding animal circles at chest height by it. */
  midY: number;
  /** Where the motes are shed from and the halo sits, above the origin (m). */
  glowY: number;
}

/** Built-in fallback numbers (the shipped manifest's), for the stand-ins before the glb loads. */
const FALLBACK: Readonly<
  Record<
    AnimalKey,
    { gait: Gait; height: number; size: [number, number, number] }
  >
> = {
  hare: { gait: "hop", height: 0, size: [0.24, 0.58, 0.5] },
  fox: { gait: "trot", height: 0, size: [0.2, 0.64, 1.28] },
  cat: { gait: "glide", height: 0, size: [0.16, 0.58, 0.7] },
  owl: { gait: "fly", height: 1.45, size: [0.95, 0.3, 0.53] },
  otter: { gait: "swim", height: 0.55, size: [0.18, 0.2, 1.05] },
  horse: { gait: "gallop", height: 0, size: [0.42, 1.57, 1.72] },
};

function finish(
  key: AnimalKey,
  mesh: Mesh,
  roles: Role[],
  pivots: Float32Array,
  spec: { gait: Gait; height: number; size: [number, number, number] },
): AnimalModel {
  mesh.isPickable = false;
  mesh.setEnabled(false);
  const ground = spec.height === 0;
  const midY = ground ? spec.size[1] * 0.5 : 0;
  return {
    key,
    template: mesh,
    rig: { roles, pivots },
    gait: spec.gait,
    followHeight: spec.height,
    size: spec.size,
    midY,
    glowY: ground ? spec.size[1] * 0.45 : 0,
  };
}

/** Merged geometry arrays: positions, normals, part index per vertex, triangles. */
interface Merged {
  positions: number[];
  normals: number[];
  parts: number[];
  indices: number[];
}

function mergedMesh(name: string, m: Merged, scene: Scene): Mesh {
  const mesh = new Mesh(name, scene);
  const vd = new VertexData();
  vd.positions = m.positions;
  vd.normals = m.normals;
  vd.indices = m.indices;
  vd.applyToMesh(mesh, false);
  mesh.setVerticesData("part", new Float32Array(m.parts), false, 1);
  return mesh;
}

/** A stand-in animal: ellipsoids for its parts (code-built, before the glb). */
export function standIn(key: AnimalKey, scene: Scene): AnimalModel {
  const spec = FALLBACK[key];
  const [w, h, l] = spec.size;
  const ground = spec.height === 0;
  const by = ground ? h * 0.55 : 0;
  const parts: {
    role: Role;
    c: [number, number, number];
    r: [number, number, number];
    p: [number, number, number];
  }[] = [
    {
      role: "body",
      c: [0, by, 0],
      r: [w * 0.35, h * 0.2, l * 0.28],
      p: [0, by, 0],
    },
    {
      role: "head",
      c: [0, by + h * 0.2, l * 0.3],
      r: [w * 0.3, h * 0.13, l * 0.12],
      p: [0, by + h * 0.1, l * 0.22],
    },
  ];
  if (key === "owl") {
    for (const s of [1, -1])
      parts.push({
        role: s > 0 ? "wing_r" : "wing_l",
        c: [s * w * 0.3, by, 0],
        r: [w * 0.22, 0.015, l * 0.15],
        p: [s * w * 0.08, by, 0],
      });
  } else {
    const legs: Role[] = ["leg_fl", "leg_fr", "leg_bl", "leg_br"];
    legs.forEach((role, i) => {
      const x = (i % 2 === 0 ? -1 : 1) * w * 0.22;
      const z = (i < 2 ? 1 : -1) * l * 0.18;
      const top = by;
      parts.push({
        role,
        c: [x, top * 0.5, z],
        r: [w * 0.08, top * 0.5, w * 0.08],
        p: [x, top, z],
      });
    });
  }
  const merged: Merged = { positions: [], normals: [], parts: [], indices: [] };
  const roles: Role[] = [];
  const pivots = new Float32Array(MAX_PARTS * 3);
  parts.forEach((part, i) => {
    roles.push(part.role);
    pivots.set(part.p, i * 3);
    const seg = 8;
    const rings = 5;
    const base = merged.positions.length / 3;
    for (let r = 0; r <= rings; r++) {
      const v = (r / rings) * Math.PI;
      for (let s = 0; s <= seg; s++) {
        const u = (s / seg) * Math.PI * 2;
        const nx = Math.sin(v) * Math.cos(u);
        const ny = Math.cos(v);
        const nz = Math.sin(v) * Math.sin(u);
        merged.positions.push(
          part.c[0] + nx * part.r[0],
          part.c[1] + ny * part.r[1],
          part.c[2] + nz * part.r[2],
        );
        merged.normals.push(nx, ny, nz);
        merged.parts.push(i);
      }
    }
    for (let r = 0; r < rings; r++) {
      for (let s = 0; s < seg; s++) {
        const a = base + r * (seg + 1) + s;
        const b = a + seg + 1;
        merged.indices.push(a, a + 1, b, a + 1, b + 1, b);
      }
    }
  });
  return finish(
    key,
    mergedMesh(`guardian-${key}-standin`, merged, scene),
    roles,
    pivots,
    spec,
  );
}

const WORLD = new Matrix();
const P = new Vector3();
const N = new Vector3();

/**
 * Loads the glb and the manifest: one merged model per animal, or null when
 * either is missing or malformed (the caller keeps the stand-ins).
 */
export async function loadModels(
  scene: Scene,
  base: string,
): Promise<{
  models: Map<AnimalKey, AnimalModel>;
  manifest: GuardianManifest;
} | null> {
  let manifest: GuardianManifest;
  try {
    const res = await fetch(assetUrl(`${base}guardian/manifest.json`));
    if (!res.ok) return null;
    manifest = parseManifest(await res.json());
  } catch {
    return null;
  }
  const container = await LoadAssetContainerAsync(
    assetUrl(`${base}guardian/guardian.glb`),
    scene,
    { pluginExtension: ".glb" },
  ).catch(() => null);
  if (!container) return null;
  try {
    const byName = new Map(container.meshes.map((m) => [m.name, m]));
    const models = new Map<AnimalKey, AnimalModel>();
    for (const key of ANIMALS) {
      const spec = manifest[key];
      const merged: Merged = {
        positions: [],
        normals: [],
        parts: [],
        indices: [],
      };
      const pivots = new Float32Array(MAX_PARTS * 3);
      let ok = true;
      spec.parts.forEach((role, i) => {
        const node = byName.get(spec.nodes[role]!);
        const pos = node?.getVerticesData(VertexBuffer.PositionKind);
        const nor = node?.getVerticesData(VertexBuffer.NormalKind);
        const idx = node?.getIndices();
        if (!node || !pos || !nor || !idx) {
          ok = false;
          return;
        }
        node.computeWorldMatrix(true);
        WORLD.copyFrom(node.getWorldMatrix());
        // The joint: where the node sits (the loader's handedness root included).
        const t = WORLD.getTranslation();
        pivots[i * 3] = t.x;
        pivots[i * 3 + 1] = t.y;
        pivots[i * 3 + 2] = t.z;
        const base = merged.positions.length / 3;
        for (let v = 0; v < pos.length / 3; v++) {
          Vector3.TransformCoordinatesFromFloatsToRef(
            pos[v * 3]!,
            pos[v * 3 + 1]!,
            pos[v * 3 + 2]!,
            WORLD,
            P,
          );
          Vector3.TransformNormalFromFloatsToRef(
            nor[v * 3]!,
            nor[v * 3 + 1]!,
            nor[v * 3 + 2]!,
            WORLD,
            N,
          );
          N.normalize();
          merged.positions.push(P.x, P.y, P.z);
          merged.normals.push(N.x, N.y, N.z);
          merged.parts.push(i);
        }
        // A mirroring transform (the loader's root) turns the triangles over.
        const flip = WORLD.determinant() < 0;
        for (let k = 0; k < idx.length; k += 3) {
          if (flip)
            merged.indices.push(
              base + idx[k]!,
              base + idx[k + 2]!,
              base + idx[k + 1]!,
            );
          else
            merged.indices.push(
              base + idx[k]!,
              base + idx[k + 1]!,
              base + idx[k + 2]!,
            );
        }
      });
      if (!ok) return null;
      const mesh = mergedMesh(`guardian-${key}`, merged, scene);
      models.set(
        key,
        finish(key, mesh, [...spec.parts], pivots, {
          gait: spec.gait,
          height: spec.followHeight,
          size: spec.size,
        }),
      );
    }
    return { models, manifest };
  } finally {
    container.dispose();
  }
}
