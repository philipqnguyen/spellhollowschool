// Character parts: one body, hairstyle or gear item, as plain vertex arrays
// ready to be merged (assemble.ts). `partFromGltf` reads the art pipeline's
// .glb files (docs/art/base-body-spec.md):
//
// - every mesh is skinned to the shared skeleton, bound by bone *name*, so
//   files may list their joints in any order;
// - the base body is split into regions (`head`, `torso`, ...) that gear can
//   hide: a node's `extras.region`, or a node / mesh name ending in the
//   region (`body_a_torso`, `torso_lod1`);
// - gear lists the regions it hides in `extras.hides` and long robes say
//   `extras.long: true` (on any node, the mesh, the scene or the asset);
// - vertex colours (`COLOR_0`) are the tint mask (dye.ts): on gear R, G, B =
//   primary, secondary, trim dye; on the body R = skin tone, G = hair colour;
//   on hair R = hair colour. Without one the whole primitive takes channel R;
// - every character file shares one small atlas texture (neutral base
//   colours, painted eyes), embedded as the base colour texture; it is kept
//   so all characters can share one textured material;
// - LODs are nodes named `<name>_lod<N>`, or listed by `MSFT_lod`;
// - unskinned meshes under a joint (rigid props on sockets) are bound to it;
// - the shade layer (docs/art/character-adoption.md section 3): a custom
//   vertex attribute `_SHADE` (VEC4: shade, root-to-tip, band phase,
//   outline weight; float or normalised unsigned byte / short), read as
//   floats 0..1; absent on files made before it;
// - the outline layer (docs/art/character-look.md section 4): a custom vertex
//   attribute `_OUTLINE` (VEC3 float: the inverted hull's smoothed normal
//   times its width weight, charlib/outline_bake.py); absent on files made
//   before it (those parts get no outline);
// - hair files (character-adoption.md section 4): the fringe's lower edge `extras.fringe_hem` and
//   `extras.fringe_shadow` on the LOD0 mesh, and sway joints
//   (`hair.back.01`, `hair.side.L.01`, ...) in the skin, whose rest pose
//   and inverse bind matrices are the skeleton's like any other joint.
//
// Pure, unit tested.

import { Gltf, type Extras, type GltfNode } from "./gltf";
import { composeTRS, IDENTITY, invert4, mul4, quatFromMatrix, transformPoint } from "./math";
import { CHARACTER_REGIONS, type CharacterRegion } from "../toon/palette";

/** The body regions gear may hide (base body spec, "Clipping rules"). */
export const BODY_REGIONS = ["head", "scalp", "neck", "torso", "upper_arms", "forearms", "hands", "hips", "thighs", "calves", "feet"] as const;
export type BodyRegion = (typeof BODY_REGIONS)[number];

export interface MorphData {
  positions: Float32Array;
  normals: Float32Array | null;
}

export interface PartPrimitive {
  /** Node (or mesh) name, for diagnostics. */
  name: string;
  /** Material name: tells skin from eyes on the body. */
  material: string;
  /** Body region, when the primitive is one. */
  region: BodyRegion | null;
  /** 0 = full detail. */
  lod: number;
  positions: Float32Array;
  normals: Float32Array | null;
  /** Tint mask, 3 floats per vertex (channel weights, see dye.ts), or null. */
  mask: Float32Array | null;
  /**
   * Toon hints (docs/art/style-guide.md, "Toon rendering"): vertices whose
   * `COLOR_0` alpha is below 0.5 are metal (1 per vertex); null when none is.
   */
  metal?: Uint8Array | null;
  /** A whole-primitive toon region from `extras.toon` (`"metal"`, `"skin"`, ...), or null. */
  toon?: CharacterRegion | null;
  /** Material base colour factor, sRGB 0..1 (multiplies the atlas where textured). */
  base: [number, number, number];
  /** Atlas coordinates, 2 per vertex, when the material is textured. */
  uvs: Float32Array | null;
  /** 4 per vertex, indices into `PartData.bones`. */
  joints: Uint16Array;
  /** 4 per vertex. */
  weights: Float32Array;
  indices: Uint32Array;
  morphs: Map<string, MorphData>;
  /** The shade layer, 4 floats 0..1 per vertex (`_SHADE`), or null when the file has none. */
  shade?: Float32Array | null;
  /** Outlines: the outline layer, 3 floats per vertex (`_OUTLINE`: smoothed normal x width weight), or null. */
  outline?: Float32Array | null;
  /**
   * Garments: the body vertex each vertex lies over (`_BODYV`, an index into
   * the part's `covers`) and its height over the skin there (`_BODYH`,
   * metres), or null when the file has none (charlib/layers.py).
   */
  bodyVertex?: Float32Array | null;
  bodyHeight?: Float32Array | null;
  /** Hair worn under hats: 1 on the inner cap (`_SCALP`), which no hat drops (hatfit.ts), or null when the file has none. */
  scalp?: Uint8Array | null;
  /**
   * The body's head maps into the body's face texture (`extras.face_texture`,
   * docs/art/character-look.md section 3.1): its UVs are face-texture coordinates,
   * and every vertex is the `face` region.
   */
  faceTexture?: boolean;
}

/** A hair style's fringe shadow (character-adoption.md section 4.3): the hem as seen from the front and how the shadow falls. */
export interface FringeData {
  /** [x, y, z] per point (glTF rest frame, metres, +Y up, facing +Z), sorted by x. */
  hem: Float32Array;
  /**
   * The line the shadow hangs from: the hem's heights with single strand
   * tips that reach far below the fringe's general line held up to it and
   * the rest smoothed (`shadowLine`), so the shadow is one soft band under
   * the fringe instead of a streak under every lock tip. Same x as `hem`.
   */
  line: Float32Array;
  /** How far below the hem the shadow reaches, its soft edge, and its strength (0..1). */
  depth: number;
  soft: number;
  strength: number;
}

/** Defaults of `extras.fringe_shadow`. */
export const FRINGE_SHADOW = { depth_m: 0.03, soft_m: 0.012, strength: 0.55 } as const;

/** The skeleton a part was skinned to, in the file's joint order. */
export interface SkeletonData {
  bones: string[];
  /** Parent bone index, -1 for roots. */
  parents: Int16Array;
  /** Rest pose: 10 floats per bone (translation 3, rotation 4, scale 3). */
  rest: Float32Array;
  /** Inverse bind matrices, 16 floats per bone (column-major). */
  inverseBind: Float32Array;
  /**
   * For root bones, the rest transform of the non-joint nodes above them
   * (an armature object), applied after the bone's own local transform.
   * Identity for every other bone.
   */
  rootPre: Float32Array;
}

export type Interpolation = "LINEAR" | "STEP" | "CUBICSPLINE";

export interface ChannelData {
  times: Float32Array;
  /** Keyframe values (vec3 or quat), tangents already stripped. */
  values: Float32Array;
  interpolation: Interpolation;
}

export interface TrackData {
  translation?: ChannelData;
  rotation?: ChannelData;
  scale?: ChannelData;
}

/** An animation as authored: keyframe channels by bone name. */
export interface ClipData {
  name: string;
  duration: number;
  tracks: Map<string, TrackData>;
}

/** An embedded image (the character atlas). */
export interface AtlasImage {
  mimeType: string;
  bytes: Uint8Array;
}

export interface PartData {
  /** Joint names that `PartPrimitive.joints` index into. */
  bones: string[];
  primitives: PartPrimitive[];
  hides: string[];
  long: boolean;
  skeleton: SkeletonData | null;
  clips: ClipData[];
  /** The base colour texture its textured primitives sample. */
  atlas: AtlasImage | null;
  /** Hair: the fringe shadow it casts on the forehead, when the file says. */
  fringe?: FringeData | null;
  /**
   * Garments of the legs and body layers: how far out the garment covers
   * each body vertex, in `COVER_STEP_M` steps (0 = not covered), from
   * `extras.covers` (base64, one byte per body vertex), or null. A higher
   * layer's cover hides a lower layer's triangles under it (assemble.ts).
   */
  covers?: Uint8Array | null;
}

/** The step of `PartData.covers` (metres). */
export const COVER_STEP_M = 0.002;

/** Decodes `extras.covers` (base64), or null when it is not a non-empty string. */
export function coversFrom(value: unknown): Uint8Array | null {
  if (typeof value !== "string" || value.length === 0) return null;
  try {
    const bin = atob(value);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

/** Reads `fringe_hem` / `fringe_shadow` extras (8 to 64 finite points), or null. */
export function fringeFrom(...extras: (Extras | undefined)[]): FringeData | null {
  for (const e of extras) {
    const hem = e?.fringe_hem;
    if (!Array.isArray(hem) || hem.length < 2 || hem.length > 64) continue;
    const pts = hem.filter((p): p is number[] => Array.isArray(p) && p.length === 3 && p.every((v) => typeof v === "number" && Number.isFinite(v)));
    if (pts.length !== hem.length) continue;
    pts.sort((a, b) => a[0]! - b[0]!);
    const s = (e?.fringe_shadow ?? {}) as Record<string, unknown>;
    const num = (v: unknown, d: number, lo: number, hi: number) => (typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : d);
    const hemArr = Float32Array.from(pts.flat());
    return {
      hem: hemArr,
      line: shadowLine(hemArr),
      depth: num(s.depth_m, FRINGE_SHADOW.depth_m, 0.001, 0.2),
      soft: num(s.soft_m, FRINGE_SHADOW.soft_m, 0.0005, 0.1),
      strength: num(s.strength, FRINGE_SHADOW.strength, 0, 1),
    };
  }
  return null;
}

/** How far below the fringe's median height a hem point may pull the shadow line (m). */
export const FRINGE_DIP = 0.006;

/**
 * The fringe shadow's line from a hem (`FringeData.line`): each point's
 * height held to at least the median minus `FRINGE_DIP` (a long strand or
 * a side lock hanging over an eye would otherwise drag a streak of shadow
 * down to the cheek), then two passes of a [1 2 1] smoothing so the line
 * follows the fringe's general shape, not every lock tip.
 */
export function shadowLine(hem: Float32Array): Float32Array {
  const n = hem.length / 3;
  const ys: number[] = [];
  for (let k = 0; k < n; k++) ys.push(hem[k * 3 + 1]!);
  const sorted = [...ys].sort((a, b) => a - b);
  const median = n % 2 ? sorted[(n - 1) / 2]! : 0.5 * (sorted[n / 2 - 1]! + sorted[n / 2]!);
  let y = ys.map((v) => Math.max(v, median - FRINGE_DIP));
  for (let pass = 0; pass < 2; pass++) y = y.map((v, k) => 0.25 * y[Math.max(0, k - 1)]! + 0.5 * v + 0.25 * y[Math.min(n - 1, k + 1)]!);
  const out = Float32Array.from(hem);
  for (let k = 0; k < n; k++) out[k * 3 + 1] = y[k]!;
  return out;
}

/** The vertex attribute name of the shade layer. */
export const SHADE_ATTRIBUTE = "_SHADE";

/** Outlines: the vertex attribute name of the outline layer (docs/art/character-look.md section 4). */
export const OUTLINE_ATTRIBUTE = "_OUTLINE";

/** Garments: the body vertex under each vertex and the height over it (charlib/layers.py). */
export const BODY_VERTEX_ATTRIBUTE = "_BODYV";
export const BODY_HEIGHT_ATTRIBUTE = "_BODYH";

/** Hair worn under hats: the inner cap's vertices (charlib/hair_meshy.py SCALP_ATTRIBUTE). */
export const SCALP_ATTRIBUTE = "_SCALP";

const LOD_SUFFIX = /_lod(\d+)$/i;

export function regionFromName(name: string): BodyRegion | null {
  const n = name.toLowerCase().replace(LOD_SUFFIX, "");
  // Longest first so `upper_arms` wins over a hypothetical `arms`.
  for (const r of [...BODY_REGIONS].sort((a, b) => b.length - a.length)) {
    if (n === r || n.endsWith(`_${r}`) || n.endsWith(`.${r}`)) return r;
  }
  return null;
}

function regionOf(...extras: (Extras | undefined)[]): BodyRegion | null {
  for (const e of extras) {
    const r = e?.region;
    if (typeof r === "string" && (BODY_REGIONS as readonly string[]).includes(r)) return r as BodyRegion;
  }
  return null;
}

/** The first `extras.toon` that names a character toon region. */
function toonOf(...extras: (Extras | undefined)[]): CharacterRegion | null {
  for (const e of extras) {
    const r = e?.toon;
    if (typeof r === "string" && (CHARACTER_REGIONS as readonly string[]).includes(r)) return r as CharacterRegion;
  }
  return null;
}

/** Whether any `extras.face_texture` is set (the head's UVs are face-texture coordinates). */
function faceTextureOf(...extras: (Extras | undefined)[]): boolean {
  return extras.some((e) => e?.face_texture === true || e?.face_texture === 1);
}

/** Local rest matrix of a glTF node. */
export function nodeMatrix(n: GltfNode, out: number[] | Float32Array = new Array<number>(16)): number[] | Float32Array {
  if (n.matrix && n.matrix.length === 16) {
    for (let i = 0; i < 16; i++) out[i] = n.matrix[i]!;
    return out;
  }
  const t = n.translation ?? [0, 0, 0];
  const r = n.rotation ?? [0, 0, 0, 1];
  const s = n.scale ?? [1, 1, 1];
  composeTRS(t[0], t[1], t[2], r[0], r[1], r[2], r[3], s[0], s[1], s[2], out);
  return out;
}

/** Decomposes a node's rest transform into [t, q, s] (matrix nodes assume no shear). */
function nodeTRS(n: GltfNode): number[] {
  if (n.matrix && n.matrix.length === 16) {
    const m = n.matrix;
    const sx = Math.hypot(m[0]!, m[1]!, m[2]!);
    const sy = Math.hypot(m[4]!, m[5]!, m[6]!);
    const sz = Math.hypot(m[8]!, m[9]!, m[10]!);
    const q = quatFromMatrix(m);
    return [m[12]!, m[13]!, m[14]!, q[0], q[1], q[2], q[3], sx, sy, sz];
  }
  const t = n.translation ?? [0, 0, 0];
  const r = n.rotation ?? [0, 0, 0, 1];
  const s = n.scale ?? [1, 1, 1];
  return [t[0], t[1], t[2], r[0], r[1], r[2], r[3], s[0], s[1], s[2]];
}

/** Reads a part from a parsed .glb. Throws `GltfError` on malformed files. */
export function partFromGltf(gltf: Gltf): PartData {
  const json = gltf.json;
  let atlas: AtlasImage | null = null;
  const nodes = json.nodes ?? [];
  const parent = new Int32Array(nodes.length).fill(-1);
  nodes.forEach((n, i) => n.children?.forEach((c) => (parent[c] = i)));

  // Rest world matrices of every node (parents before children via recursion + memo).
  const world: (Float32Array | null)[] = nodes.map(() => null);
  const worldOf = (i: number): Float32Array => {
    const cached = world[i];
    if (cached) return cached;
    const local = nodeMatrix(nodes[i]!, new Float32Array(16));
    let w = local as Float32Array;
    if (parent[i]! >= 0) {
      w = new Float32Array(16);
      mul4(worldOf(parent[i]!), local, w);
    }
    world[i] = w;
    return w;
  };

  // LOD membership: names, and MSFT_lod lists (ids[k] is LOD k + 1).
  const lodOf = new Map<number, number>();
  nodes.forEach((n) => n.extensions?.MSFT_lod?.ids.forEach((id, k) => lodOf.set(id, k + 1)));
  const lodLevel = (i: number): number => {
    const named = LOD_SUFFIX.exec(nodes[i]!.name ?? "");
    return lodOf.get(i) ?? (named ? Number(named[1]) : 0);
  };

  // The skeleton: the skin with the most joints (every file carries the full one).
  const skins = json.skins ?? [];
  let skinIndex = -1;
  skins.forEach((s, i) => {
    if (skinIndex < 0 || s.joints.length > skins[skinIndex]!.joints.length) skinIndex = i;
  });
  const skeleton = skinIndex >= 0 ? readSkeleton(gltf, skinIndex, parent, worldOf) : null;
  const bones = skeleton ? skeleton.bones.slice() : [];
  const boneIndex = new Map(bones.map((b, i) => [b, i]));
  const boneFor = (name: string): number => {
    let i = boneIndex.get(name);
    if (i === undefined) {
      i = bones.length;
      bones.push(name);
      boneIndex.set(name, i);
    }
    return i;
  };
  const jointNodes = new Set(skins.flatMap((s) => s.joints));

  const primitives: PartPrimitive[] = [];
  let fringe: FringeData | null = null;
  const hides = new Set<string>();
  let long = false;
  let covers: Uint8Array | null = null;
  const noteExtras = (e: Extras | undefined) => {
    if (!e) return;
    if (Array.isArray(e.hides)) for (const h of e.hides) if (typeof h === "string") hides.add(h);
    if (e.long === true) long = true;
    covers ??= coversFrom(e.covers);
  };
  noteExtras(json.asset.extras);
  json.scenes?.forEach((s) => noteExtras(s.extras));

  nodes.forEach((node, ni) => {
    noteExtras(node.extras);
    if (node.mesh === undefined) return;
    const mesh = json.meshes?.[node.mesh];
    if (!mesh) return;
    noteExtras(mesh.extras);
    const name = node.name ?? mesh.name ?? `node${ni}`;
    const lod = lodLevel(ni);
    const targetNames = Array.isArray(mesh.extras?.targetNames) ? mesh.extras.targetNames : [];
    if (lod === 0) fringe ??= fringeFrom(node.extras, mesh.extras);

    // Joint remap for skinned meshes; for rigid ones, the joint above the node.
    let jointMap: number[] | null = null;
    let rigidBone = -1;
    let rigidMatrix: Float32Array | null = null;
    if (node.skin !== undefined && skins[node.skin]) {
      jointMap = skins[node.skin]!.joints.map((j) => boneFor(nodes[j]?.name ?? `joint${j}`));
    } else {
      let a = parent[ni]!;
      while (a >= 0 && !jointNodes.has(a)) a = parent[a]!;
      rigidBone = a >= 0 ? boneFor(nodes[a]!.name ?? `joint${a}`) : bones.length > 0 ? 0 : boneFor("root");
      rigidMatrix = worldOf(ni);
    }

    for (const prim of mesh.primitives) {
      if (prim.mode !== undefined && prim.mode !== 4) continue; // triangles only
      const posAcc = prim.attributes.POSITION;
      if (posAcc === undefined) continue;
      const positions = gltf.floats(posAcc);
      const count = positions.length / 3;
      const normals = prim.attributes.NORMAL !== undefined ? gltf.floats(prim.attributes.NORMAL) : null;
      const indices = prim.indices !== undefined ? gltf.uints(prim.indices) : Uint32Array.from({ length: count }, (_, i) => i);

      let mask: Float32Array | null = null;
      let metal: Uint8Array | null = null;
      if (prim.attributes.COLOR_0 !== undefined) {
        const c = gltf.floats(prim.attributes.COLOR_0);
        const n = gltf.components(prim.attributes.COLOR_0);
        mask = new Float32Array(count * 3);
        for (let v = 0; v < count; v++) {
          mask[v * 3] = c[v * n]!;
          mask[v * 3 + 1] = c[v * n + 1]!;
          mask[v * 3 + 2] = c[v * n + 2]!;
          if (n === 4 && c[v * n + 3]! < 0.5) (metal ??= new Uint8Array(count))[v] = 1;
        }
      }

      const joints = new Uint16Array(count * 4);
      const weights = new Float32Array(count * 4);
      if (jointMap && prim.attributes.JOINTS_0 !== undefined && prim.attributes.WEIGHTS_0 !== undefined) {
        const j = gltf.uints(prim.attributes.JOINTS_0);
        const w = gltf.floats(prim.attributes.WEIGHTS_0);
        for (let k = 0; k < count * 4; k++) {
          joints[k] = jointMap[j[k]!] ?? 0;
          weights[k] = w[k]!;
        }
        normaliseWeights(weights);
      } else {
        // Rigid: bake the node's rest transform and follow one bone.
        const m = rigidMatrix ?? new Float32Array(IDENTITY);
        const bone = jointMap ? 0 : rigidBone;
        bakeRigid(positions, normals, m);
        for (let v = 0; v < count; v++) {
          joints[v * 4] = bone;
          weights[v * 4] = 1;
        }
      }

      let shade: Float32Array | null = null;
      const shadeAcc = prim.attributes[SHADE_ATTRIBUTE];
      if (shadeAcc !== undefined && gltf.components(shadeAcc) === 4) {
        shade = gltf.floats(shadeAcc);
        // Integer components written without the normalized flag: still 0..1.
        const a = gltf.accessor(shadeAcc);
        if (!a.normalized && (a.componentType === 5121 || a.componentType === 5123)) {
          const scale = a.componentType === 5121 ? 1 / 255 : 1 / 65535;
          for (let k = 0; k < shade.length; k++) shade[k]! *= scale;
        }
        if (shade.length !== count * 4) shade = null;
      }

      // --- Outlines: the outline layer (rigid parts: into the node's frame, lengths kept) ---
      let outline: Float32Array | null = null;
      const outlineAcc = prim.attributes[OUTLINE_ATTRIBUTE];
      if (outlineAcc !== undefined && gltf.components(outlineAcc) === 3) {
        outline = gltf.floats(outlineAcc);
        if (outline.length !== count * 3) outline = null;
        else if (!jointMap) rotateKeepingLength(outline, rigidMatrix ?? IDENTITY);
      }
      // --- end Outlines ---

      const scalar = (name: string): Float32Array | null => {
        const acc = prim.attributes[name];
        if (acc === undefined || gltf.components(acc) !== 1) return null;
        const v = gltf.floats(acc);
        return v.length === count ? v : null;
      };
      const bodyVertex = scalar(BODY_VERTEX_ATTRIBUTE);
      const bodyHeight = bodyVertex ? scalar(BODY_HEIGHT_ATTRIBUTE) : null;
      const scalpF = scalar(SCALP_ATTRIBUTE);
      const scalp = scalpF ? Uint8Array.from(scalpF, (v) => (v > 0.5 ? 1 : 0)) : null;

      const morphs = new Map<string, MorphData>();
      prim.targets?.forEach((t, k) => {
        const key = typeof targetNames[k] === "string" ? (targetNames[k] as string) : `target${k}`;
        if (t.POSITION === undefined) return;
        morphs.set(key, { positions: gltf.floats(t.POSITION), normals: t.NORMAL !== undefined ? gltf.floats(t.NORMAL) : null });
      });

      const mat = prim.material !== undefined ? json.materials?.[prim.material] : undefined;
      const f = mat?.pbrMetallicRoughness?.baseColorFactor ?? [1, 1, 1, 1];
      const tex = mat?.pbrMetallicRoughness?.baseColorTexture;
      const uvAcc = prim.attributes[`TEXCOORD_${tex?.texCoord ?? 0}`];
      const textured = tex !== undefined && uvAcc !== undefined && atlasFor(tex.index) !== null;
      primitives.push({
        name,
        material: mat?.name ?? "",
        region: regionOf(prim.extras, node.extras, mesh.extras) ?? regionFromName(name) ?? (mesh.name ? regionFromName(mesh.name) : null),
        lod,
        positions,
        normals,
        mask,
        metal,
        toon: toonOf(prim.extras, node.extras, mesh.extras),
        faceTexture: faceTextureOf(prim.extras, node.extras, mesh.extras),
        base: [linearToSrgb(f[0] ?? 1), linearToSrgb(f[1] ?? 1), linearToSrgb(f[2] ?? 1)],
        uvs: textured ? gltf.floats(uvAcc) : null,
        joints,
        weights,
        indices,
        morphs,
        shade,
        outline,
        bodyVertex: bodyHeight ? bodyVertex : null,
        bodyHeight,
        scalp,
      });
    }
  });

  const clips = readClips(gltf);
  return { bones, primitives, hides: [...hides], long, skeleton, clips, atlas, fringe, covers };

  /** The embedded image behind texture `index`; the first one found becomes the part's atlas. */
  function atlasFor(index: number): AtlasImage | null {
    const image = json.images?.[json.textures?.[index]?.source ?? -1];
    if (!image || image.bufferView === undefined) return null;
    atlas ??= { mimeType: image.mimeType ?? "image/png", bytes: gltf.bufferViewBytes(image.bufferView) };
    return atlas;
  }
}

function readSkeleton(gltf: Gltf, skinIndex: number, parent: Int32Array, worldOf: (i: number) => Float32Array): SkeletonData {
  const nodes = gltf.json.nodes ?? [];
  const skin = gltf.json.skins![skinIndex]!;
  const joints = skin.joints;
  const n = joints.length;
  const jointIndex = new Map(joints.map((j, i) => [j, i]));
  const bones = joints.map((j) => nodes[j]?.name ?? `joint${j}`);
  const parents = new Int16Array(n);
  const rest = new Float32Array(n * 10);
  const rootPre = new Float32Array(n * 16);
  const inverseBind = new Float32Array(n * 16);
  const ibm = skin.inverseBindMatrices !== undefined ? gltf.floats(skin.inverseBindMatrices) : null;
  const tmp = new Float32Array(16);
  for (let i = 0; i < n; i++) {
    const node = nodes[joints[i]!]!;
    let p = parent[joints[i]!]!;
    while (p >= 0 && !jointIndex.has(p)) p = parent[p]!;
    parents[i] = p >= 0 ? jointIndex.get(p)! : -1;
    rest.set(nodeTRS(node), i * 10);
    const direct = parent[joints[i]!]!;
    if (parents[i] === -1 && direct >= 0) rootPre.set(worldOf(direct), i * 16);
    else if (parents[i]! >= 0 && direct >= 0 && !jointIndex.has(direct)) {
      // A non-joint node between two joints: fold it into the child's rest pose.
      const between = new Float32Array(16);
      invert4(worldOf(joints[parents[i]!]!), tmp);
      mul4(tmp, worldOf(direct), between);
      const local = nodeMatrix(node, new Float32Array(16));
      const m = new Float32Array(16);
      mul4(between, local, m);
      rest.set(nodeTRS({ matrix: Array.from(m) }), i * 10);
      rootPre.set(IDENTITY, i * 16);
    } else rootPre.set(IDENTITY, i * 16);
    if (ibm) inverseBind.set(ibm.subarray(i * 16, i * 16 + 16), i * 16);
    else {
      invert4(worldOf(joints[i]!), tmp);
      inverseBind.set(tmp, i * 16);
    }
  }
  // Parents must come before children for posing; glTF does not promise that.
  return sortSkeleton({ bones, parents, rest, inverseBind, rootPre });
}

/** Reorders bones so every parent precedes its children (stable otherwise). */
export function sortSkeleton(s: SkeletonData): SkeletonData {
  const n = s.bones.length;
  const order: number[] = [];
  const placed = new Uint8Array(n);
  const visit = (i: number) => {
    if (placed[i]) return;
    if (s.parents[i]! >= 0) visit(s.parents[i]!);
    placed[i] = 1;
    order.push(i);
  };
  for (let i = 0; i < n; i++) visit(i);
  if (order.every((v, i) => v === i)) return s;
  const newIndex = new Int16Array(n);
  order.forEach((old, i) => (newIndex[old] = i));
  const out: SkeletonData = {
    bones: order.map((i) => s.bones[i]!),
    parents: Int16Array.from(order, (i) => (s.parents[i]! >= 0 ? newIndex[s.parents[i]!]! : -1)),
    rest: new Float32Array(n * 10),
    inverseBind: new Float32Array(n * 16),
    rootPre: new Float32Array(n * 16),
  };
  order.forEach((old, i) => {
    out.rest.set(s.rest.subarray(old * 10, old * 10 + 10), i * 10);
    out.inverseBind.set(s.inverseBind.subarray(old * 16, old * 16 + 16), i * 16);
    out.rootPre.set(s.rootPre.subarray(old * 16, old * 16 + 16), i * 16);
  });
  return out;
}

function readClips(gltf: Gltf): ClipData[] {
  const json = gltf.json;
  const nodes = json.nodes ?? [];
  const clips: ClipData[] = [];
  for (const anim of json.animations ?? []) {
    const tracks = new Map<string, TrackData>();
    let duration = 0;
    for (const ch of anim.channels) {
      const path = ch.target.path;
      if (ch.target.node === undefined || path === "weights") continue;
      const sampler = anim.samplers[ch.sampler];
      if (!sampler) continue;
      const times = gltf.floats(sampler.input);
      let values = gltf.floats(sampler.output);
      const interpolation = sampler.interpolation ?? "LINEAR";
      const width = path === "rotation" ? 4 : 3;
      if (interpolation === "CUBICSPLINE") {
        // [in-tangent, value, out-tangent] per key: keep the values.
        const v = new Float32Array(times.length * width);
        for (let k = 0; k < times.length; k++) v.set(values.subarray((k * 3 + 1) * width, (k * 3 + 2) * width), k * width);
        values = v;
      }
      duration = Math.max(duration, times[times.length - 1] ?? 0);
      const bone = nodes[ch.target.node]?.name ?? `joint${ch.target.node}`;
      const track = tracks.get(bone) ?? {};
      track[path] = { times, values, interpolation };
      tracks.set(bone, track);
    }
    if (tracks.size > 0) clips.push({ name: anim.name ?? `clip${clips.length}`, duration, tracks });
  }
  return clips;
}

function normaliseWeights(w: Float32Array): void {
  for (let v = 0; v < w.length; v += 4) {
    const sum = w[v]! + w[v + 1]! + w[v + 2]! + w[v + 3]!;
    if (sum > 0 && Math.abs(sum - 1) > 1e-4) for (let k = 0; k < 4; k++) w[v + k]! /= sum;
    else if (sum === 0) w[v] = 1;
  }
}

function bakeRigid(positions: Float32Array, normals: Float32Array | null, m: ArrayLike<number>): void {
  const mm = Array.from(m);
  for (let i = 0; i < positions.length; i += 3) {
    const p = transformPoint(mm, positions[i]!, positions[i + 1]!, positions[i + 2]!);
    positions.set(p, i);
  }
  if (normals) {
    for (let i = 0; i < normals.length; i += 3) {
      const x = normals[i]!;
      const y = normals[i + 1]!;
      const z = normals[i + 2]!;
      const nx = mm[0]! * x + mm[4]! * y + mm[8]! * z;
      const ny = mm[1]! * x + mm[5]! * y + mm[9]! * z;
      const nz = mm[2]! * x + mm[6]! * y + mm[10]! * z;
      const len = Math.hypot(nx, ny, nz) || 1;
      normals[i] = nx / len;
      normals[i + 1] = ny / len;
      normals[i + 2] = nz / len;
    }
  }
}

/** Outlines: rotates vectors by `m`'s upper 3 x 3, each keeping its length (the outline layer's weight). */
function rotateKeepingLength(v: Float32Array, m: ArrayLike<number>): void {
  for (let i = 0; i < v.length; i += 3) {
    const x = v[i]!;
    const y = v[i + 1]!;
    const z = v[i + 2]!;
    const nx = m[0]! * x + m[4]! * y + m[8]! * z;
    const ny = m[1]! * x + m[5]! * y + m[9]! * z;
    const nz = m[2]! * x + m[6]! * y + m[10]! * z;
    const k = Math.hypot(x, y, z) / (Math.hypot(nx, ny, nz) || 1);
    v[i] = nx * k;
    v[i + 1] = ny * k;
    v[i + 2] = nz * k;
  }
}

/** glTF base colours are linear; the character material works in sRGB like the palette. */
export function linearToSrgb(c: number): number {
  const v = Math.max(0, Math.min(1, c));
  return v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055;
}
