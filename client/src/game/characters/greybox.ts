// The character greybox: low-poly stand-ins for bodies, hairstyles and gear,
// generated from the rig, used wherever the art pipeline's .glb for a part is
// missing (like the world greybox before kit art lands). They follow the art
// contract exactly, so the rest of the pipeline cannot tell them apart:
//
// - skinned to the spec's Rigify bone names (docs/art/base-body-spec.md),
//   around the rig's bind pose, so they also fit an art skeleton;
// - the body is split into the spec's regions, gear lists what it `hides`,
//   long robes say `long`;
// - every vertex carries the tint mask (dye.ts; on gear R, G, B = primary,
//   secondary, trim) over a neutral base;
// - build and face shape keys use the creator's key names;
// - each part has a LOD1 at about half the segments.
//
// Shapes are chosen from asset-key words (`robe`, `skirt`, `boots`, ...),
// never from item ids or names. Pure, unit tested.

import { add3, cross3, lerp3, norm3, scale3, sub3, type V3 } from "./math";
import type { Rig } from "./clips";
import type { MorphData, PartData, PartPrimitive, SkeletonData, BodyRegion } from "./parts";

// ------------------------------------------------------------------ skeleton

/** Body measures from the base body spec, per body type index. */
export const GREYBOX_BODIES = [
  { height: 1.78, shoulder: 0.46, hip: 0.36, inseam: 0.84 },
  { height: 1.68, shoulder: 0.39, hip: 0.37, inseam: 0.78 },
] as const;

type BoneDef = [name: string, parent: string | null, pos: V3];

/** The greybox rig: the spec's main deform bones at the body's proportions, in A-pose. */
export function greyboxSkeleton(bodyIndex: number): SkeletonData {
  const b = GREYBOX_BODIES[bodyIndex] ?? GREYBOX_BODIES[0];
  const H = b.height;
  const sh = b.shoulder / 2 - 0.035;
  const hip = b.hip / 2 - 0.07;
  const ua = 0.17 * H;
  const fa = 0.15 * H;
  const down = Math.SQRT1_2; // arms 45 degrees down
  const defs: BoneDef[] = [["root", null, [0, 0, 0]]];
  const spine: V3[] = [0.53, 0.6, 0.665, 0.72, 0.815, 0.84, 0.865].map((k) => [0, k * H, 0]);
  const spineNames = ["DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003", "DEF-spine.004", "DEF-spine.005", "DEF-spine.006"];
  spineNames.forEach((n, i) => defs.push([n, i === 0 ? "root" : spineNames[i - 1]!, spine[i]!]));
  for (const [side, sx] of [
    ["L", 1],
    ["R", -1],
  ] as const) {
    const shoulder: V3 = [sx * 0.03, 0.805 * H, -0.01];
    const upper: V3 = [sx * sh, 0.8 * H, -0.01];
    const elbow: V3 = [upper[0] + sx * ua * down, upper[1] - ua * down, -0.02];
    const wrist: V3 = [elbow[0] + sx * fa * down, elbow[1] - fa * down, 0.0];
    const thigh: V3 = [sx * hip, b.inseam + 0.06, 0];
    const knee: V3 = [sx * hip * 0.92, 0.285 * H, 0.01];
    const ankle: V3 = [sx * hip * 0.9, 0.05 * H, -0.01];
    const toe: V3 = [sx * hip * 0.9, 0.012, 0.13];
    defs.push(
      [`DEF-shoulder.${side}`, "DEF-spine.003", shoulder],
      [`DEF-upper_arm.${side}`, `DEF-shoulder.${side}`, upper],
      [`DEF-forearm.${side}`, `DEF-upper_arm.${side}`, elbow],
      [`DEF-hand.${side}`, `DEF-forearm.${side}`, wrist],
      [`DEF-thigh.${side}`, "DEF-spine", thigh],
      [`DEF-shin.${side}`, `DEF-thigh.${side}`, knee],
      [`DEF-foot.${side}`, `DEF-shin.${side}`, ankle],
      [`DEF-toe.${side}`, `DEF-foot.${side}`, toe],
    );
  }
  const index = new Map(defs.map((d, i) => [d[0], i]));
  const n = defs.length;
  const rest = new Float32Array(n * 10);
  const inverseBind = new Float32Array(n * 16);
  const rootPre = new Float32Array(n * 16);
  const parents = new Int16Array(n);
  defs.forEach(([, parent, pos], i) => {
    const p = parent ? index.get(parent)! : -1;
    parents[i] = p;
    const pp = p >= 0 ? defs[p]![2] : ([0, 0, 0] as V3);
    rest.set([pos[0] - pp[0], pos[1] - pp[1], pos[2] - pp[2], 0, 0, 0, 1, 1, 1, 1], i * 10);
    inverseBind.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -pos[0], -pos[1], -pos[2], 1], i * 16);
    rootPre.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], i * 16);
  });
  return { bones: defs.map((d) => d[0]), parents, rest, inverseBind, rootPre };
}

// ------------------------------------------------------------------ mesh building

type Influence = [bone: string, weight: number];
type Mask = [number, number, number];
const PRIMARY: Mask = [1, 0, 0];
const SECONDARY: Mask = [0, 1, 0];
const TRIM: Mask = [0, 0, 1];
/** No tint: the vertex keeps its base colour (eyes). */
const NONE: Mask = [0, 0, 0];
/** Neutral base (like the atlas's near-white fabric and skin texels) and the eyes' colour (sRGB). */
const NEUTRAL: V3 = [0.95, 0.95, 0.95];
const EYES: V3 = [0.16, 0.13, 0.12];

/** Shape-key offsets along a vertex's outward direction (m), by key. */
type Bulge = Record<string, number>;

class Prim {
  readonly pos: number[] = [];
  readonly nrm: number[] = [];
  readonly mask: number[] = [];
  readonly joints: number[] = [];
  readonly weights: number[] = [];
  readonly idx: number[] = [];
  readonly morph = new Map<string, number[]>();

  constructor(
    readonly material: string,
    readonly region: BodyRegion | null,
    readonly lod: number,
    readonly base: V3,
    private readonly boneOf: (name: string) => number,
  ) {}

  get count(): number {
    return this.pos.length / 3;
  }

  vertex(p: V3, n: V3, mask: Mask, inf: readonly Influence[], bulge?: Bulge, out?: V3): number {
    const i = this.count;
    this.pos.push(p[0], p[1], p[2]);
    this.nrm.push(n[0], n[1], n[2]);
    this.mask.push(mask[0], mask[1], mask[2]);
    const top = [...inf].sort((a, b) => b[1] - a[1]).slice(0, 4);
    const sum = top.reduce((s, x) => s + x[1], 0) || 1;
    for (let k = 0; k < 4; k++) {
      this.joints.push(top[k] ? this.boneOf(top[k]![0]) : 0);
      this.weights.push(top[k] ? top[k]![1] / sum : 0);
    }
    const dir = out ?? n;
    for (const [key, amount] of Object.entries(bulge ?? {})) {
      let d = this.morph.get(key);
      if (!d) {
        d = new Array<number>(i * 3).fill(0);
        this.morph.set(key, d);
      }
      while (d.length < i * 3) d.push(0);
      d.push(dir[0] * amount, dir[1] * amount, dir[2] * amount);
    }
    return i;
  }

  tri(a: number, b: number, c: number): void {
    this.idx.push(a, b, c);
  }

  /** Adds a triangle wound counter-clockwise as seen from the side `out` points to (glTF front faces). */
  triFacing(a: number, b: number, c: number, out: V3): void {
    const P = this.pos;
    const u = [P[b * 3]! - P[a * 3]!, P[b * 3 + 1]! - P[a * 3 + 1]!, P[b * 3 + 2]! - P[a * 3 + 2]!];
    const v = [P[c * 3]! - P[a * 3]!, P[c * 3 + 1]! - P[a * 3 + 1]!, P[c * 3 + 2]! - P[a * 3 + 2]!];
    const nx = u[1]! * v[2]! - u[2]! * v[1]!;
    const ny = u[2]! * v[0]! - u[0]! * v[2]!;
    const nz = u[0]! * v[1]! - u[1]! * v[0]!;
    if (nx * out[0] + ny * out[1] + nz * out[2] < 0) this.idx.push(a, c, b);
    else this.idx.push(a, b, c);
  }

  /** The stored normal of vertex `i`. */
  normalOf(i: number): V3 {
    return [this.nrm[i * 3]!, this.nrm[i * 3 + 1]!, this.nrm[i * 3 + 2]!];
  }

  build(name: string): PartPrimitive {
    const n = this.count;
    const morphs = new Map<string, MorphData>();
    for (const [key, d] of this.morph) {
      while (d.length < n * 3) d.push(0);
      morphs.set(key, { positions: Float32Array.from(d), normals: null });
    }
    return {
      name,
      material: this.material,
      region: this.region,
      lod: this.lod,
      positions: Float32Array.from(this.pos),
      normals: Float32Array.from(this.nrm),
      mask: Float32Array.from(this.mask),
      uvs: null,
      base: [this.base[0], this.base[1], this.base[2]],
      joints: Uint16Array.from(this.joints),
      weights: Float32Array.from(this.weights),
      indices: Uint32Array.from(this.idx),
      morphs,
    };
  }
}

class Builder {
  readonly bones: string[] = [];
  private readonly boneIndex = new Map<string, number>();
  private readonly prims: { key: string; prim: Prim }[] = [];

  constructor(readonly lod: number) {}

  private bone = (name: string): number => {
    let i = this.boneIndex.get(name);
    if (i === undefined) {
      i = this.bones.length;
      this.bones.push(name);
      this.boneIndex.set(name, i);
    }
    return i;
  };

  prim(material: string, region: BodyRegion | null, base: V3): Prim {
    const key = `${material}/${region ?? ""}`;
    const found = this.prims.find((p) => p.key === key);
    if (found) return found.prim;
    const prim = new Prim(material, region, this.lod, base, this.bone);
    this.prims.push({ key, prim });
    return prim;
  }

  primitives(): PartPrimitive[] {
    return this.prims.filter((p) => p.prim.count > 0).map((p) => p.prim.build(p.key));
  }
}

/** One ring of a tube: an ellipse around `center` in the plane of `side` and `front`. */
interface Ring {
  center: V3;
  side: V3;
  front: V3;
  rx: number;
  rz: number;
  /** Skin weights of the ring, or per vertex as a function of its angle (0 = `side`, π/2 = `front`). */
  inf: readonly Influence[] | ((angle: number) => Influence[]);
  mask: Mask | ((angle: number) => Mask);
  bulge?: Bulge;
  /** Radial wobble (pleats, knit cables): extra radius as a function of the angle. */
  wobble?: (angle: number) => number;
}

/** Connects rings into a tube; `caps` closes the first and / or last ring. */
function tube(p: Prim, rings: readonly Ring[], segs: number, caps: { start?: boolean; end?: boolean } = {}): void {
  const starts: number[] = [];
  for (const r of rings) {
    starts.push(p.count);
    for (let s = 0; s < segs; s++) {
      const a = (s / segs) * Math.PI * 2;
      const ca = Math.cos(a);
      const sa = Math.sin(a);
      const extra = r.wobble ? r.wobble(a) : 0;
      const out = norm3(add3(scale3(r.side, ca * r.rz), scale3(r.front, sa * r.rx)));
      const pos = add3(r.center, add3(scale3(r.side, ca * (r.rx + extra)), scale3(r.front, sa * (r.rz + extra))));
      const mask = typeof r.mask === "function" ? r.mask(a) : r.mask;
      p.vertex(pos, out, mask, typeof r.inf === "function" ? r.inf(a) : r.inf, r.bulge, out);
    }
  }
  for (let k = 0; k + 1 < rings.length; k++) {
    const a0 = starts[k]!;
    const b0 = starts[k + 1]!;
    for (let s = 0; s < segs; s++) {
      const s1 = (s + 1) % segs;
      const out = add3(p.normalOf(a0 + s), p.normalOf(b0 + s1));
      p.triFacing(a0 + s, b0 + s, b0 + s1, out);
      p.triFacing(a0 + s, b0 + s1, a0 + s1, out);
    }
  }
  const cap = (ring: Ring, start: number, away: V3) => {
    const axis = norm3(away);
    const mask = typeof ring.mask === "function" ? ring.mask(0) : ring.mask;
    const inf = typeof ring.inf === "function" ? ring.inf(Math.PI / 2) : ring.inf;
    const c = p.vertex(ring.center, axis, mask, inf, ring.bulge, [0, 0, 0]);
    for (let s = 0; s < segs; s++) p.triFacing(c, start + s, start + ((s + 1) % segs), axis);
  };
  // Caps face away from the tube: back along it at the start, onward at the end.
  const along = (i: number, j: number): V3 => (rings.length > 1 ? sub3(rings[j]!.center, rings[i]!.center) : norm3(cross3(rings[0]!.side, rings[0]!.front)));
  if (caps.start && rings.length) cap(rings[0]!, starts[0]!, along(1, 0));
  if (caps.end && rings.length) cap(rings[rings.length - 1]!, starts[starts.length - 1]!, along(rings.length - 2, rings.length - 1));
}

/** An ellipsoid (optionally only the part above `fromV`, 0 = bottom pole, 1 = top). */
function ellipsoid(
  p: Prim,
  center: V3,
  radii: V3,
  segs: number,
  inf: readonly Influence[] | ((pos: V3) => Influence[]),
  mask: Mask | ((pos: V3, n: V3) => Mask),
  opts: { fromV?: number; bulge?: (n: V3) => Bulge | undefined; keep?: (n: V3) => boolean } = {},
): void {
  const rows = Math.max(3, Math.round(segs / 2));
  const from = Math.round((opts.fromV ?? 0) * rows);
  const start = p.count;
  for (let r = from; r <= rows; r++) {
    const v = r / rows;
    const phi = Math.PI * (1 - v); // pi at the bottom pole, 0 at the top
    for (let s = 0; s < segs; s++) {
      const th = (s / segs) * Math.PI * 2;
      const n: V3 = [Math.sin(phi) * Math.sin(th), Math.cos(phi), Math.sin(phi) * Math.cos(th)];
      const pos: V3 = [center[0] + n[0] * radii[0], center[1] + n[1] * radii[1], center[2] + n[2] * radii[2]];
      const nn = norm3([n[0] / radii[0], n[1] / radii[1], n[2] / radii[2]]);
      p.vertex(pos, nn, typeof mask === "function" ? mask(pos, nn) : mask, typeof inf === "function" ? inf(pos) : inf, opts.bulge?.(nn), nn);
    }
  }
  const ringCount = rows - from + 1;
  for (let r = 0; r + 1 < ringCount; r++) {
    for (let s = 0; s < segs; s++) {
      const s1 = (s + 1) % segs;
      const a = start + r * segs;
      const b = start + (r + 1) * segs;
      if (opts.keep && ![a + s, a + s1, b + s1, b + s].every((i) => opts.keep!(p.normalOf(i)))) continue;
      const out = add3(p.normalOf(a + s), p.normalOf(b + s1));
      p.triFacing(a + s, a + s1, b + s1, out);
      p.triFacing(a + s, b + s1, b + s, out);
    }
  }
}

/** A box with its own normals (8-ish triangles a face is plenty for the greybox). */
function box(p: Prim, center: V3, half: V3, inf: readonly Influence[], mask: Mask, bulge?: Bulge): void {
  const faces: [V3, V3, V3][] = [
    [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    [[-1, 0, 0], [0, 0, 1], [0, 1, 0]],
    [[0, 1, 0], [0, 0, 1], [1, 0, 0]],
    [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
    [[0, 0, 1], [1, 0, 0], [0, 1, 0]],
    [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
  ];
  for (const [n, u, v] of faces) {
    const c = add3(center, [n[0] * half[0], n[1] * half[1], n[2] * half[2]]);
    const U = [u[0] * half[0], u[1] * half[1], u[2] * half[2]] as V3;
    const Vv = [v[0] * half[0], v[1] * half[1], v[2] * half[2]] as V3;
    const i = p.count;
    for (const [su, sv] of [
      [-1, -1],
      [1, -1],
      [1, 1],
      [-1, 1],
    ] as const) {
      p.vertex(add3(c, add3(scale3(U, su), scale3(Vv, sv))), n, mask, inf, bulge, n);
    }
    p.triFacing(i, i + 1, i + 2, n);
    p.triFacing(i, i + 2, i + 3, n);
  }
}

// ------------------------------------------------------------------ rig measures

interface Measures {
  H: number;
  at: (bone: string) => V3;
  has: (bone: string) => boolean;
  shoulderHalf: number;
  hipHalf: number;
  /** 1 for the broader body, smaller for the narrower one. */
  breadth: number;
  /** The head as an ellipsoid, from the art body's `head` region when there is one. */
  head: { center: V3; radii: V3 } | null;
}

function measures(rig: Rig, body?: PartData): Measures {
  const at = (bone: string): V3 => rig.bonePos(bone) ?? [0, 0, 0];
  const has = (bone: string) => rig.boneIndex(bone) >= 0;
  const head = has("DEF-spine.006") ? at("DEF-spine.006")[1] : 1.54;
  const H = head / 0.865;
  const shoulderHalf = Math.abs(at("DEF-upper_arm.L")[0]) + 0.035 || 0.2;
  const hipHalf = Math.abs(at("DEF-thigh.L")[0]) + 0.07 || 0.18;
  return { H, at, has, shoulderHalf, hipHalf, breadth: Math.min(1, shoulderHalf / 0.23), head: body ? headBounds(body) : null };
}

/** Centre and half extents of a body part's full-detail `head` and `scalp` regions, or null. */
export function headBounds(body: PartData): { center: V3; radii: V3 } | null {
  const lo: V3 = [Infinity, Infinity, Infinity];
  const hi: V3 = [-Infinity, -Infinity, -Infinity];
  for (const p of body.primitives) {
    if ((p.region !== "head" && p.region !== "scalp") || p.lod !== 0) continue;
    for (let i = 0; i < p.positions.length; i += 3) {
      for (let c = 0; c < 3; c++) {
        lo[c] = Math.min(lo[c]!, p.positions[i + c]!);
        hi[c] = Math.max(hi[c]!, p.positions[i + c]!);
      }
    }
  }
  if (!(lo[0] < hi[0])) return null;
  return { center: lerp3(lo, hi, 0.5), radii: scale3(sub3(hi, lo), 0.5) };
}

/** Ring frame for an axis from `a` to `b`: side and front perpendicular to it. */
function frame(a: V3, b: V3): { side: V3; front: V3 } {
  const d = norm3(sub3(b, a));
  const ref: V3 = Math.abs(d[2]) > 0.8 ? [0, 1, 0] : [0, 0, 1];
  const side = norm3(cross3(d, ref));
  const front = norm3(cross3(side, d));
  return { side, front };
}

/** Influences along a bone chain at parameter t (0..1) of segment `bone`, blending into neighbours near joints. */
function chainInf(parent: string | null, bone: string, child: string | null, t: number): Influence[] {
  if (t < 0.2 && parent) return [[bone, 0.5 + 2.5 * t], [parent, 0.5 - 2.5 * t]];
  if (t > 0.8 && child) return [[bone, 1 - 2.5 * (t - 0.8)], [child, 2.5 * (t - 0.8)]];
  return [[bone, 1]];
}

interface LimbSpec {
  /** Bone chain, joint heads in order; the last entry is where the limb ends. */
  chain: string[];
  /** Radii (x, z) at each chain joint. */
  radii: [number, number][];
  /** Rings per segment. */
  rings: number;
  mask: Mask | ((angle: number) => Mask);
  /** Mask of the last ring (cuffs, hems), if different. */
  endMask?: Mask;
  bulge?: Bulge;
  /** Clip the limb at this fraction of its length (rolled sleeves, short boots). */
  until?: number;
  /** Start at this fraction (boot shafts start at the ankle, etc.). */
  from?: number;
  /** Extra end point beyond the last joint (wrist → mid-hand). */
  end?: V3;
}

function limb(p: Prim, m: Measures, spec: LimbSpec, segs: number, caps: { start?: boolean; end?: boolean } = {}): void {
  const pts = spec.chain.map((b) => m.at(b));
  if (spec.end) pts.push(spec.end);
  const rings: Ring[] = [];
  const total = pts.length - 1;
  const from = spec.from ?? 0;
  const until = spec.until ?? 1;
  const steps = total * spec.rings;
  for (let k = 0; k <= steps; k++) {
    const u = from + ((until - from) * k) / steps; // 0..1 along the whole limb
    const seg = Math.min(total - 1, Math.floor(u * total));
    const t = u * total - seg;
    const a = pts[seg]!;
    const b = pts[seg + 1]!;
    const center = lerp3(a, b, t);
    const { side, front } = frame(a, b);
    const r0 = spec.radii[seg]!;
    const r1 = spec.radii[Math.min(seg + 1, spec.radii.length - 1)]!;
    const bone = spec.chain[Math.min(seg, spec.chain.length - 1)]!;
    const parent = seg > 0 ? spec.chain[seg - 1]! : null;
    const child = seg + 1 < spec.chain.length ? spec.chain[seg + 1]! : null;
    rings.push({
      center,
      side,
      front,
      rx: r0[0] + (r1[0] - r0[0]) * t,
      rz: r0[1] + (r1[1] - r0[1]) * t,
      inf: chainInf(parent, bone, child, t),
      mask: k === steps && spec.endMask ? spec.endMask : spec.mask,
      bulge: spec.bulge,
    });
  }
  tube(p, rings, segs, caps);
}

const SIDES = ["L", "R"] as const;

/** Vertical rings around the spine at heights (fractions of H) with radii, skinned to the nearest spine bones. */
function trunk(
  p: Prim,
  m: Measures,
  levels: readonly { y: number; rx: number; rz: number; mask?: Mask | ((a: number) => Mask); z?: number; wobble?: (a: number) => number }[],
  segs: number,
  opts: { mask: Mask | ((a: number) => Mask); bulge?: (y: number) => Bulge | undefined; legs?: boolean; caps?: { start?: boolean; end?: boolean } },
): void {
  const spine = ["DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003", "DEF-spine.004", "DEF-spine.005", "DEF-spine.006"].filter(m.has);
  const ys = spine.map((b) => m.at(b)[1]);
  const infAt = (y: number): Influence[] => {
    if (y <= ys[0]!) return [[spine[0]!, 1]];
    for (let i = 0; i + 1 < ys.length; i++) {
      if (y <= ys[i + 1]!) {
        const t = (y - ys[i]!) / (ys[i + 1]! - ys[i]!);
        return [
          [spine[i]!, 1 - t],
          [spine[i + 1]!, t],
        ];
      }
    }
    return [[spine[spine.length - 1]!, 1]];
  };
  const rings: Ring[] = levels.map((l) => {
    const y = l.y * m.H;
    return {
      center: [0, y, l.z ?? 0],
      side: [1, 0, 0],
      front: [0, 0, 1],
      rx: l.rx,
      rz: l.rz,
      inf: infAt(y),
      mask: l.mask ?? opts.mask,
      bulge: opts.bulge?.(l.y),
      wobble: l.wobble,
    };
  });
  if (opts.legs) {
    // Skirts and robe hems below the hips follow the thigh on their side a little.
    const hipY = m.at("DEF-thigh.L")[1];
    const kneeY = m.at("DEF-shin.L")[1];
    for (const r of rings) {
      if (r.center[1] >= hipY) continue;
      const k = 0.6 * Math.min(1, (hipY - r.center[1]) / Math.max(0.1, hipY - kneeY));
      r.inf = (a: number): Influence[] => {
        const left = Math.max(0, Math.cos(a)); // side = +X = the character's left
        const right = Math.max(0, -Math.cos(a));
        return [
          ["DEF-spine", 1 - k * (left + right)],
          ["DEF-thigh.L", k * left],
          ["DEF-thigh.R", k * right],
        ];
      };
    }
  }
  tube(p, rings, segs, opts.caps);
}

// ------------------------------------------------------------------ parts

export type GreyboxKind =
  | "body"
  | "hair_short"
  | "hair_long"
  | "hair_tail"
  | "hair_bun"
  | "hair_volume"
  | "hat_pointed"
  | "hat_knit"
  | "robe"
  | "cloak"
  | "shirt"
  | "shirt_rolled"
  | "jumper"
  | "trousers"
  | "skirt"
  | "shoes"
  | "boots";

/** The greybox shape for an asset key, from its words (`gear/legs/skirt_pleated_a` → skirt). */
export function greyboxKind(role: "base" | "hair" | "hat" | "body" | "legs" | "shoes", asset: string): GreyboxKind {
  const words = asset.toLowerCase().split(/[^a-z]+/);
  const has = (...w: string[]) => w.some((x) => words.includes(x));
  switch (role) {
    case "base":
      return "body";
    case "hair":
      if (has("afro")) return "hair_volume";
      if (has("bun")) return "hair_bun";
      if (has("ponytail", "plait", "braid")) return "hair_tail";
      if (has("long", "waves", "curls", "braids", "shoulder")) return "hair_long";
      return "hair_short";
    case "hat":
      return has("bobble", "knit", "beanie", "cap") ? "hat_knit" : "hat_pointed";
    case "body":
      if (has("robe", "gown")) return "robe";
      if (has("cloak", "cape", "coat")) return "cloak";
      if (has("rolled", "linen")) return "shirt_rolled";
      if (has("jumper", "sweater", "cardigan")) return "jumper";
      return "shirt";
    case "legs":
      return has("skirt", "kilt") ? "skirt" : "trousers";
    case "shoes":
      return has("boots", "boot") ? "boots" : "shoes";
  }
}

const BUILD_TORSO: Bulge = { build_slim: -0.018, build_muscular: 0.022, build_heavy: 0.045 };
const BUILD_LIMB: Bulge = { build_slim: -0.008, build_muscular: 0.014, build_heavy: 0.02 };
const BUILD_HIPS: Bulge = { build_slim: -0.015, build_muscular: 0.008, build_heavy: 0.05 };

/**
 * A greybox part for `kind` fitted to `rig`. `cap` makes the hat-friendly
 * hair variant (`<asset>_cap`). LOD0 and LOD1 are both included.
 */
export function greyboxPart(kind: GreyboxKind, rig: Rig, opts: { cap?: boolean; under?: boolean; body?: PartData } = {}): PartData {
  const lods = [new Builder(0), new Builder(1)];
  const m = measures(rig, opts.body);
  for (const b of lods) build(b, kind, m, opts.cap === true, opts.under === true);
  const bones: string[] = [];
  const primitives: PartPrimitive[] = [];
  for (const b of lods) {
    // Each builder numbers its own bones; remap into one list.
    const map = b.bones.map((name) => {
      let i = bones.indexOf(name);
      if (i < 0) i = bones.push(name) - 1;
      return i;
    });
    for (const prim of b.primitives()) {
      for (let k = 0; k < prim.joints.length; k++) prim.joints[k] = map[prim.joints[k]!]!;
      primitives.push(prim);
    }
  }
  return { bones, primitives, hides: HIDES[kind], long: kind === "robe" || kind === "cloak", skeleton: null, clips: [], atlas: null };
}

const HIDES: Record<GreyboxKind, string[]> = {
  body: [],
  hair_short: [],
  hair_long: [],
  hair_tail: [],
  hair_bun: [],
  hair_volume: [],
  hat_pointed: ["scalp"],
  hat_knit: ["scalp"],
  robe: ["torso", "upper_arms", "forearms", "hips", "thighs", "calves"],
  cloak: ["torso", "upper_arms", "forearms", "hips", "thighs", "calves"],
  shirt: ["torso", "upper_arms", "forearms"],
  shirt_rolled: ["torso", "upper_arms"],
  jumper: ["torso", "upper_arms", "forearms"],
  trousers: ["hips", "thighs", "calves"],
  skirt: ["hips", "thighs"],
  shoes: ["feet"],
  boots: ["feet"],
};

function build(b: Builder, kind: GreyboxKind, m: Measures, cap: boolean, under: boolean): void {
  const segs = b.lod === 0 ? 12 : 7;
  const rings = b.lod === 0 ? 3 : 1;
  const H = m.H;
  const head = m.at("DEF-spine.006");
  const headC: V3 = m.head ? m.head.center : [0, head[1] + 0.07 * (H / 1.78), 0.012];
  const headR: V3 = m.head ? m.head.radii : ([0.083, 0.112, 0.098].map((r) => r * (H / 1.78)) as V3);
  const sh = m.shoulderHalf;
  const hip = m.hipHalf;
  const br = m.breadth;

  switch (kind) {
    case "body": {
      const skin = (region: BodyRegion) => b.prim("skin", region, NEUTRAL);
      // Head: jaw and cheek keys widen the lower and middle face; the nose is its own little block.
      ellipsoid(skin("head"), headC, headR, segs + 2, [["DEF-spine.006", 1]], PRIMARY, {
        bulge: (n) => {
          const side = Math.abs(n[0]);
          const lower = n[1] < -0.1 ? side * Math.min(1, -n[1] * 2) : 0;
          const mid = n[1] > -0.3 && n[1] < 0.3 ? side : 0;
          return {
            face_jaw_narrow: -0.012 * lower,
            face_jaw_wide: 0.016 * lower,
            face_cheek_soft: -0.006 * mid,
            face_cheek_high: 0.009 * mid,
            face_brow_low: n[1] > 0.25 && n[2] > 0.5 ? -0.006 : 0,
            face_brow_high: n[1] > 0.25 && n[2] > 0.5 ? 0.008 : 0,
          };
        },
      });
      const nose = skin("head");
      box(nose, [0, headC[1] - 0.01, headC[2] + headR[2] - 0.004], [0.011, 0.02, 0.016], [["DEF-spine.006", 1]], PRIMARY, {
        face_nose_small: -0.005,
        face_nose_large: 0.008,
      });
      const eyes = b.prim("eyes", "head", EYES);
      for (const sx of [1, -1]) {
        ellipsoid(eyes, [sx * 0.032 * (H / 1.78), headC[1] + 0.012, headC[2] + headR[2] * 0.86], [0.012, 0.009, 0.006], 8, [["DEF-spine.006", 1]], NONE, {
          bulge: () => ({ face_eyes_narrow: -0.0025, face_eyes_round: 0.003 }),
        });
      }
      // Neck, torso, hips.
      limb(skin("neck"), m, { chain: ["DEF-spine.004", "DEF-spine.006"], radii: [[0.055, 0.05], [0.047, 0.045]], rings, mask: PRIMARY, end: headC }, segs);
      trunk(
        skin("torso"),
        m,
        [
          { y: 0.6, rx: 0.13 * br + 0.01, rz: 0.1 },
          { y: 0.67, rx: 0.135 * br + 0.01, rz: 0.105 },
          { y: 0.73, rx: 0.155 * br + 0.01, rz: 0.115 },
          { y: 0.79, rx: sh - 0.02, rz: 0.1 },
          { y: 0.82, rx: sh - 0.07, rz: 0.075 },
        ],
        segs,
        { mask: PRIMARY, bulge: () => BUILD_TORSO, caps: { end: true } },
      );
      trunk(
        skin("hips"),
        m,
        [
          { y: (m.at("DEF-thigh.L")[1] - 0.09) / H, rx: hip * 0.8, rz: 0.1 },
          { y: 0.53, rx: hip, rz: 0.115 },
          { y: 0.6, rx: 0.13 * br + 0.01, rz: 0.1 },
        ],
        segs,
        { mask: PRIMARY, bulge: () => BUILD_HIPS, caps: { start: true } },
      );
      for (const s of SIDES) {
        limb(skin("upper_arms"), m, { chain: [`DEF-upper_arm.${s}`, `DEF-forearm.${s}`], radii: [[0.048, 0.048], [0.04, 0.04]], rings, mask: PRIMARY, bulge: BUILD_LIMB }, segs, { start: true });
        limb(skin("forearms"), m, { chain: [`DEF-forearm.${s}`, `DEF-hand.${s}`], radii: [[0.04, 0.04], [0.03, 0.026]], rings, mask: PRIMARY, bulge: BUILD_LIMB }, segs);
        const wrist = m.at(`DEF-hand.${s}`);
        const elbow = m.at(`DEF-forearm.${s}`);
        const dir = norm3(sub3(wrist, elbow));
        ellipsoid(skin("hands"), add3(wrist, scale3(dir, 0.058)), [0.03, 0.06, 0.022], segs, [[`DEF-hand.${s}`, 1]], PRIMARY);
        limb(skin("thighs"), m, { chain: [`DEF-thigh.${s}`, `DEF-shin.${s}`], radii: [[0.08 * br + 0.01, 0.085], [0.055, 0.058]], rings, mask: PRIMARY, bulge: BUILD_HIPS }, segs, { start: true });
        limb(skin("calves"), m, { chain: [`DEF-shin.${s}`, `DEF-foot.${s}`], radii: [[0.055, 0.058], [0.035, 0.038]], rings, mask: PRIMARY, bulge: BUILD_LIMB }, segs);
        feet(skin("feet"), m, s, 0, PRIMARY, PRIMARY);
      }
      break;
    }

    case "hair_short":
    case "hair_long":
    case "hair_tail":
    case "hair_bun":
    case "hair_volume": {
      const hair = b.prim("hair", null, NEUTRAL);
      const inf: Influence[] = [["DEF-spine.006", 1]];
      const grow = kind === "hair_volume" && !cap ? 0.05 : cap ? 0.004 : 0.014;
      const r: V3 = [headR[0] + grow, headR[1] + grow * 0.8, headR[2] + grow];
      // A cap over the crown, cut at the forehead in front and lower at the back.
      ellipsoid(hair, [headC[0], headC[1] + (cap ? -0.005 : 0.004), headC[2] - 0.006], r, segs + 2, inf, PRIMARY, {
        fromV: cap ? 0.5 : 0.3,
        // Down to the nape at the back and sides, but only to the brow in front.
        keep: (n) => n[1] > (cap ? 0.3 : 0.2) || n[2] < -0.1,
      });
      if (kind === "hair_long") {
        const top: V3 = [0, headC[1] - 0.02, headC[2] - r[2] * 0.75];
        const bottom: V3 = [0, m.at("DEF-spine.003")[1] + 0.02, -0.1];
        tube(
          hair,
          [0, 0.5, 1].map((t) => ({
            center: lerp3(top, bottom, t),
            side: [1, 0, 0] as V3,
            front: [0, 0, 1] as V3,
            rx: r[0] * (1 + 0.15 * t),
            rz: 0.045,
            inf: t < 0.5 ? inf : ([["DEF-spine.006", 0.5], ["DEF-spine.004", 0.5]] as Influence[]),
            mask: PRIMARY,
          })),
          segs,
          { end: true },
        );
      } else if (kind === "hair_tail") {
        const from: V3 = [0, headC[1] + 0.02, headC[2] - r[2] * 0.95];
        const to: V3 = [0, m.at("DEF-spine.004")[1] - 0.12, -0.13];
        limbBetween(hair, from, to, 0.028, 0.012, inf, [["DEF-spine.004", 1]], segs, rings);
      } else if (kind === "hair_bun") {
        ellipsoid(hair, [0, headC[1] - 0.035, headC[2] - r[2] - 0.02], [0.045, 0.04, 0.04], segs, inf, PRIMARY);
      }
      break;
    }

    case "hat_pointed": {
      const hat = b.prim("gear", null, NEUTRAL);
      const inf: Influence[] = [["DEF-spine.006", 1]];
      const base = headC[1] + headR[1] * 0.45;
      // A thin disc for the brim (seen from above and below), then the cone.
      ellipsoid(hat, [0, base, headC[2]], [0.2, 0.008, 0.2], segs + 4, inf, TRIM);
      const crown: Ring = { center: [0, base, headC[2]], side: [1, 0, 0], front: [0, 0, 1], rx: headR[0] + 0.015, rz: headR[2] + 0.015, inf, mask: PRIMARY };
      const band: Ring = { ...crown, center: [0, base + 0.035, headC[2] - 0.005], rx: headR[0] + 0.006, rz: headR[2] + 0.006, mask: SECONDARY };
      const mid: Ring = { ...crown, center: [0, base + 0.17, headC[2] - 0.04], rx: 0.045, rz: 0.045, mask: PRIMARY };
      const tip: Ring = { ...crown, center: [0, base + 0.33, headC[2] - 0.12], rx: 0.004, rz: 0.004, mask: PRIMARY };
      tube(hat, [crown, band, mid, tip], segs);
      break;
    }

    case "hat_knit": {
      const hat = b.prim("gear", null, NEUTRAL);
      const inf: Influence[] = [["DEF-spine.006", 1]];
      const r: V3 = [headR[0] + 0.02, headR[1] + 0.015, headR[2] + 0.02];
      ellipsoid(hat, [headC[0], headC[1] + 0.012, headC[2] - 0.004], r, segs + 2, inf, (pos) => (pos[1] < headC[1] + r[1] * 0.42 ? SECONDARY : PRIMARY), { fromV: 0.6 });
      ellipsoid(hat, [0, headC[1] + r[1] + 0.03, headC[2] - 0.01], [0.04, 0.04, 0.04], segs, inf, TRIM);
      break;
    }

    case "robe":
    case "cloak": {
      const robe = b.prim("gear", null, NEUTRAL);
      const cloak = kind === "cloak";
      const flare = cloak ? 0.04 : 0;
      trunk(
        robe,
        m,
        [
          { y: 0.06, rx: 0.27 + flare, rz: 0.24 + flare, mask: TRIM },
          { y: 0.08, rx: 0.265 + flare, rz: 0.235 + flare },
          { y: 0.28, rx: 0.24 + flare, rz: 0.2 + flare },
          { y: 0.5, rx: hip + 0.05 + flare, rz: 0.16 + flare },
          { y: 0.6, rx: 0.15 * br + 0.04 + flare, rz: 0.14 + flare },
          { y: 0.73, rx: 0.17 * br + 0.04 + flare, rz: 0.145 + flare },
          { y: 0.79, rx: sh + 0.01 + flare, rz: 0.13 + flare },
          { y: 0.825, rx: sh - 0.05, rz: 0.1, mask: SECONDARY },
        ],
        segs + 4,
        { mask: PRIMARY, legs: true, bulge: (y) => (y > 0.45 ? BUILD_TORSO : BUILD_HIPS) },
      );
      // Hood folded at the back of the neck, lined in the secondary colour.
      ellipsoid(robe, [0, m.at("DEF-spine.004")[1] + 0.015, -0.12], [0.13, 0.07, 0.06], segs, [["DEF-spine.004", 1]], SECONDARY);
      for (const s of SIDES) {
        limb(
          robe,
          m,
          {
            chain: [`DEF-upper_arm.${s}`, `DEF-forearm.${s}`, `DEF-hand.${s}`],
            radii: [[0.07, 0.07], [0.075, 0.075], [0.1, 0.1]],
            rings,
            mask: PRIMARY,
            endMask: TRIM,
            bulge: BUILD_LIMB,
          },
          segs,
        );
      }
      break;
    }

    case "shirt":
    case "shirt_rolled":
    case "jumper": {
      const top = b.prim("gear", null, NEUTRAL);
      const knit = kind === "jumper";
      const thick = knit ? 0.022 : 0.012;
      const vneck = (a: number): Mask => (knit && Math.abs(a - Math.PI / 2) < 0.35 ? TRIM : PRIMARY);
      trunk(
        top,
        m,
        [
          { y: 0.5, rx: hip + thick - 0.01, rz: 0.115 + thick, mask: knit ? SECONDARY : PRIMARY },
          { y: 0.53, rx: hip + thick - 0.005, rz: 0.115 + thick },
          { y: 0.6, rx: 0.13 * br + 0.01 + thick, rz: 0.1 + thick },
          { y: 0.67, rx: 0.135 * br + 0.01 + thick, rz: 0.105 + thick },
          { y: 0.73, rx: 0.155 * br + 0.01 + thick, rz: 0.115 + thick },
          { y: 0.79, rx: sh - 0.02 + thick, rz: 0.1 + thick, mask: vneck },
          { y: 0.82, rx: sh - 0.07 + thick, rz: 0.075 + thick, mask: kind === "shirt" ? TRIM : vneck },
        ],
        segs,
        { mask: PRIMARY, bulge: () => BUILD_TORSO, caps: { end: true } },
      );
      if (kind === "shirt") {
        // Collar and tie.
        limb(top, m, { chain: ["DEF-spine.004", "DEF-spine.005"], radii: [[0.066, 0.06], [0.06, 0.056]], rings: 1, mask: TRIM }, segs);
        box(top, [0, 0.73 * H, 0.105 + thick + 0.012], [0.022, 0.07 * (H / 1.78), 0.006], [["DEF-spine.003", 1]], SECONDARY);
      }
      for (const s of SIDES) {
        limb(
          top,
          m,
          {
            chain: [`DEF-upper_arm.${s}`, `DEF-forearm.${s}`, `DEF-hand.${s}`],
            radii: [[0.052 + thick, 0.052 + thick], [0.044 + thick, 0.044 + thick], [0.034 + thick, 0.032 + thick]],
            rings,
            mask: PRIMARY,
            endMask: kind === "shirt_rolled" ? SECONDARY : knit ? SECONDARY : TRIM,
            bulge: BUILD_LIMB,
            until: kind === "shirt_rolled" ? 0.72 : 1,
          },
          segs,
          { start: true },
        );
      }
      break;
    }

    case "trousers":
    case "skirt": {
      const legs = b.prim("gear", null, NEUTRAL);
      const t = 0.012;
      trunk(
        legs,
        m,
        [
          { y: (m.at("DEF-thigh.L")[1] - 0.09) / H, rx: hip * 0.8 + t, rz: 0.1 + t },
          { y: 0.53, rx: hip + t, rz: 0.115 + t },
          { y: 0.57, rx: hip - 0.005 + t, rz: 0.11 + t, mask: TRIM },
          { y: 0.59, rx: hip - 0.01 + t, rz: 0.105 + t, mask: TRIM },
        ],
        segs,
        { mask: PRIMARY, bulge: () => BUILD_HIPS, caps: { start: kind === "trousers" } },
      );
      if (kind === "skirt") {
        const pleats = (a: number) => 0.012 * Math.abs(Math.sin(a * 8));
        trunk(
          legs,
          m,
          [
            { y: 0.53, rx: hip + t, rz: 0.115 + t, wobble: pleats },
            // Under a long robe (the `_under` cut) the skirt stays short and close so it cannot show through.
            ...(under
              ? [{ y: 0.43, rx: hip + 0.03, rz: 0.14, mask: SECONDARY, wobble: pleats }]
              : [
                  { y: 0.42, rx: hip + 0.07, rz: 0.17, wobble: pleats },
                  { y: 0.3, rx: hip + 0.12, rz: 0.21, mask: SECONDARY, wobble: pleats },
                ]),
          ],
          segs + 8,
          { mask: PRIMARY, legs: true, bulge: () => BUILD_HIPS },
        );
      } else {
        for (const s of SIDES) {
          limb(
            legs,
            m,
            {
              chain: [`DEF-thigh.${s}`, `DEF-shin.${s}`, `DEF-foot.${s}`],
              radii: [[0.09 * br + 0.02, 0.095], [0.066, 0.068], [0.052, 0.054]],
              rings,
              mask: PRIMARY,
              endMask: SECONDARY,
              bulge: BUILD_LIMB,
              // The `_under` cut stops above the knee.
              until: under ? 0.42 : 1,
            },
            segs,
          );
        }
      }
      break;
    }

    case "shoes":
    case "boots": {
      const shoes = b.prim("gear", null, NEUTRAL);
      for (const s of SIDES) {
        feet(shoes, m, s, 0.012, PRIMARY, TRIM);
        if (kind === "boots") {
          limb(shoes, m, { chain: [`DEF-shin.${s}`, `DEF-foot.${s}`], radii: [[0.062, 0.065], [0.05, 0.053]], rings, mask: PRIMARY, endMask: SECONDARY, from: 0.55 }, segs);
        }
      }
      break;
    }
  }
}

/** A foot: a rounded block from heel to toe; `grow` thickens it into a shoe, `sole` colours the bottom. */
function feet(p: Prim, m: Measures, s: "L" | "R", grow: number, upper: Mask, sole: Mask): void {
  const ankle = m.at(`DEF-foot.${s}`);
  const toe = m.at(`DEF-toe.${s}`);
  const len = Math.max(0.18, toe[2] - ankle[2] + 0.12);
  const center: V3 = [ankle[0], 0.04 + grow * 0.5, ankle[2] - 0.05 + len / 2];
  box(p, center, [0.045 + grow, 0.04 + grow * 0.5, len / 2 + grow], [[`DEF-foot.${s}`, 1]], upper);
  box(p, [center[0], 0.006, center[2]], [0.046 + grow, 0.006, len / 2 + grow + 0.002], [[`DEF-foot.${s}`, 1]], sole);
}

/** A tapering tube between two free points (hair tails), blending from `infA` to `infB`. */
function limbBetween(p: Prim, a: V3, b: V3, ra: number, rb: number, infA: Influence[], infB: Influence[], segs: number, rings: number): void {
  const { side, front } = frame(a, b);
  const list: Ring[] = [];
  const steps = rings * 2;
  for (let k = 0; k <= steps; k++) {
    const t = k / steps;
    list.push({
      center: lerp3(a, b, t),
      side,
      front,
      rx: ra + (rb - ra) * t,
      rz: ra + (rb - ra) * t,
      inf: t < 0.5 ? infA : infB,
      mask: PRIMARY,
    });
  }
  tube(p, list, segs, { end: true });
}
