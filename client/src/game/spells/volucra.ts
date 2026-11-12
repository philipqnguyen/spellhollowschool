// Volucra (spells to level 20, protocol v11), Transfiguration: a small
// object becomes a flutter of birds. On `SpellCast` of a `birds` spell a pale
// feather is conjured at the caster's wand tip, floats up in front of them
// shimmering gold, and at CONJURE_S turns, in a puff of white and a burst of
// glints, into `count` small birds. They rise steeply from that spot to a
// circle over the caster's head (from HEAD_CLEARANCE above it; the circle
// keeps over the caster as they move, and clear of everyone's heads),
// circle once, then scatter up and away and fade, all gone by
// `duration_ms`. Everyone near sees the same flutter (the server sends only
// the cast; the flock is drawn here, seeded by the caster).
//
// The birds and the feather are thin instances of four meshes from
// `assets/volucra/birds.glb` (tools/blender/volucra/build_birds.py):
// `bird_body` and the wings `bird_wing_l` / `bird_wing_r`, whose origins are
// their shoulders, so each wing flaps about the body's forward axis, and the
// conjured `bird_feather`. Each bird is tinted from a small palette of
// songbird colours (the instance colour multiplies the model's pale toon
// swatch). Four draws for every bird and feather in the air (the shimmer
// and the puff are sprites in the spell effects' layers); at most
// `BIRDS_MAX` birds alive (the oldest flock goes first). Until the model
// exists a code-built low-poly bird and feather stand in.
//
// `conjureAt` (the feather) and `birdAt` (the flight path) are pure and
// unit tested (game/spells/social.test.ts).

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Matrix, Quaternion, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { VertexData } from "@babylonjs/core/Meshes/mesh.vertexData";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { Scene } from "@babylonjs/core/scene";
import { audio } from "../../audio";
import { ToonPlugin } from "../toon/material";
import { worldRamp } from "../toon/palette";
import { toonifyContainer } from "../toon/convert";
import { assetUrl } from "../../assets";

/** Most birds in the air at once (every flock together). */
export const BIRDS_MAX = 48;
/** How far out the flock circles its caster (m). */
export const CIRCLE_RADIUS = 1.4;
/** The circle starts this far above the caster's head (m), and climbs CIRCLE_CLIMB as it goes round. */
export const HEAD_CLEARANCE = 0.6;
export const CIRCLE_CLIMB = 0.55;
/** The conjured feather turns into the birds this long after the cast (s). */
export const CONJURE_S = 0.9;
/** The feather pops into being at the wand tip over this long (s). */
const FEATHER_APPEAR_S = 0.12;
/** Where the feather floats to before it turns (m from the wand tip: forward and up). */
export const TURN_FORWARD = 0.3;
export const TURN_UP = 0.55;
/** Shares of the birds' flight (from CONJURE_S): the rise to the circle, the circle (once round); the rest is the leaving. */
const RISE = 0.2;
const CIRCLE_END = 0.6;
/** The last share of the flight fades out. */
const FADE = 0.25;
/** A newborn bird grows to full size over this long (s). */
const HATCH_S = 0.25;
/** The puff where the feather turns: how long it lingers (s). */
const PUFF_S = 0.55;
/** Size of a bird (the model is about 0.19 m long) and of the feather (about 0.14 m). */
const BIRD_SCALE = 1;
const FEATHER_SCALE = 2.2;
/** The spell's gold (spells.json `#ffe45c`) and the feather's pale warm white (sRGB 0..1). */
const GOLD: readonly [number, number, number] = [1, 0.894, 0.361];
const FEATHER_WHITE: readonly [number, number, number] = [1, 0.97, 0.88];
const PUFF_WHITE: readonly [number, number, number] = [1, 1, 0.97];

/** Songbird colours (sRGB 0..1): robin, blue tit, canary, greenfinch, wren, dove, rosefinch. */
export const SONGBIRDS: readonly (readonly [number, number, number])[] = [
  [0.9, 0.42, 0.24],
  [0.36, 0.6, 0.95],
  [1, 0.86, 0.3],
  [0.5, 0.78, 0.36],
  [0.66, 0.47, 0.32],
  [0.97, 0.96, 0.93],
  [0.96, 0.56, 0.72],
];

/** One bird's own numbers (from the flock's seed). */
export interface BirdSeed {
  /** Where on the circle it arrives (radians). */
  angle: number;
  /** Which way round (+1 or -1: the whole flock turns together). */
  turn: number;
  /** Its own height offset (m) and radius scale, so the flock is not a ring of clones. */
  lift: number;
  spread: number;
  /** Where it heads off to (radians from its circling angle). */
  away: number;
  /** Wing beats per second and the beat's phase. */
  flapHz: number;
  phase: number;
  /** Index into `SONGBIRDS`. */
  colour: number;
}

export interface BirdPose {
  /** Relative to the flock's anchor: the caster's feet. */
  x: number;
  y: number;
  z: number;
  /** Wing angle (radians, up positive). */
  flap: number;
  /** 0..1 (fading out at the end; 0 once gone). */
  alpha: number;
  /** Its size as a share of full (a newborn grows). */
  grow: number;
  /** Whether it is in the air (not before the feather turns, nor after the end). */
  alive: boolean;
}

/** The conjured feather (written by `conjureAt`), in the caster's frame from the wand tip. */
export interface FeatherPose {
  /** Metres forward (along the caster's facing), up and to the right of the wand tip. */
  forward: number;
  up: number;
  side: number;
  /** Size as a share of full (0 before and after). */
  scale: number;
  /** Turn about its quill and its tilt (radians). */
  spin: number;
  tilt: number;
  /** The gold shimmer, 0..1, rising until it turns. */
  glow: number;
  alive: boolean;
}

/** A small deterministic generator (mulberry32), so every viewer draws the same flock. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The `count` birds of a flock seeded by `seed`. */
export function flockSeeds(count: number, seed: number): BirdSeed[] {
  const r = rng(seed);
  const turn = r() < 0.5 ? -1 : 1;
  const out: BirdSeed[] = [];
  for (let i = 0; i < count; i++) {
    out.push({
      angle: (i / count) * Math.PI * 2 + (r() - 0.5) * 0.5,
      turn,
      lift: (r() - 0.5) * 0.5,
      spread: 0.85 + r() * 0.3,
      away: (r() - 0.5) * 1.2,
      flapHz: 7 + r() * 4,
      phase: r() * Math.PI * 2,
      colour: Math.floor(r() * SONGBIRDS.length) % SONGBIRDS.length,
    });
  }
  return out;
}

const smooth = (t: number) => t * t * (3 - 2 * t);
const clamp01 = (t: number) => Math.max(0, Math.min(1, t));

/**
 * The conjured feather `t` s after the cast: it pops into being at the wand
 * tip, floats up and forward to the turning point (TURN_FORWARD, TURN_UP)
 * swaying and slowly spinning, its gold shimmer swelling, and shrinks away
 * into the puff at CONJURE_S (gone from then: the birds take over). Pure.
 */
export function conjureAt(t: number, out: FeatherPose): FeatherPose {
  if (t < 0 || t >= CONJURE_S) {
    out.alive = false;
    out.scale = 0;
    out.glow = 0;
    return out;
  }
  const appear = clamp01(t / FEATHER_APPEAR_S);
  // A small overshoot as it pops in.
  const pop = appear < 1 ? smooth(appear) * (1 + 0.25 * Math.sin(Math.PI * appear)) : 1;
  const k = smooth(clamp01((t - FEATHER_APPEAR_S * 0.5) / (CONJURE_S - FEATHER_APPEAR_S * 0.5)));
  // Shrinking into the puff over the last 0.08 s.
  const end = clamp01((CONJURE_S - t) / 0.08);
  out.forward = TURN_FORWARD * k;
  out.up = TURN_UP * k;
  out.side = Math.sin(t * 9) * 0.035 * (1 - k * 0.5);
  out.scale = pop * (0.35 + 0.65 * end);
  out.spin = t * 5.5;
  out.tilt = 0.9 + Math.sin(t * 7) * 0.3;
  out.glow = clamp01((t - 0.15) / (CONJURE_S - 0.2));
  out.alive = true;
  return out;
}

/**
 * Where a bird is `t` seconds after the cast, in a flight of `duration`
 * seconds, relative to the flock's anchor (the caster's feet). It is born
 * at CONJURE_S at `from` (the turning point, anchor-relative) and grows to
 * full size; rises steeply to the circle over the caster's head (from
 * `headY` + HEAD_CLEARANCE, climbing CIRCLE_CLIMB as it goes round),
 * swinging round the outside rather than over the middle; once round; then
 * up and away, fading; gone (alpha 0, `alive` false) before CONJURE_S and
 * from `duration` on. Pure.
 */
export function birdAt(
  t: number,
  duration: number,
  b: BirdSeed,
  from: readonly [number, number, number],
  headY: number,
  out: BirdPose = { x: 0, y: 0, z: 0, flap: 0, alpha: 0, grow: 0, alive: false },
): BirdPose {
  const flight = duration - CONJURE_S;
  if (!(flight > 0) || t < CONJURE_S || t >= duration) {
    out.alive = false;
    out.alpha = 0;
    out.grow = 0;
    return out;
  }
  const age = t - CONJURE_S;
  const u = age / flight;
  const r0 = CIRCLE_RADIUS * b.spread;
  const low = headY + HEAD_CLEARANCE + b.lift * 0.3;
  const sweep = Math.PI * 2 * b.turn;
  let x: number;
  let y: number;
  let z: number;
  if (u < RISE) {
    // Up first (eased out), then round and out to its place on the circle:
    // the radius never shrinks below the smaller of the start's and the
    // circle's, so no bird crosses over the caster's head.
    const k = u / RISE;
    const up = 1 - (1 - k) * (1 - k);
    const round = smooth(clamp01((k - 0.15) / 0.85));
    const a0 = Math.atan2(from[2], from[0]);
    const rStart = Math.hypot(from[0], from[2]);
    let da = b.angle - a0;
    while (da > Math.PI) da -= Math.PI * 2;
    while (da < -Math.PI) da += Math.PI * 2;
    const a = a0 + da * round;
    const r = rStart + (r0 - rStart) * round;
    x = Math.cos(a) * r;
    z = Math.sin(a) * r;
    y = from[1] + (low - from[1]) * up;
  } else if (u < CIRCLE_END) {
    // Once round the caster, climbing.
    const k = (u - RISE) / (CIRCLE_END - RISE);
    const a = b.angle + sweep * k;
    x = Math.cos(a) * r0;
    z = Math.sin(a) * r0;
    y = low + CIRCLE_CLIMB * k;
  } else {
    // Away: spiralling out and up, each bird on its own heading.
    const k = (u - CIRCLE_END) / (1 - CIRCLE_END);
    const a = b.angle + sweep + (b.away + b.turn * 0.8) * k;
    const r = r0 + 9 * k * k + 1.5 * k;
    x = Math.cos(a) * r;
    z = Math.sin(a) * r;
    y = low + CIRCLE_CLIMB + 6 * k * k + 1.2 * k;
  }
  out.x = x;
  out.y = y;
  out.z = z;
  out.flap = Math.sin(t * b.flapHz * Math.PI * 2 + b.phase) * 0.85;
  out.alpha = u > 1 - FADE ? Math.max(0, (1 - u) / FADE) : 1;
  const h = clamp01(age / HATCH_S);
  out.grow = 0.25 + 0.75 * smooth(h);
  out.alive = true;
  return out;
}

/** Sprites the flock draws into (the spells' effect layers: game/spells/vfx.ts `SpellFx`). */
export interface VolucraFx {
  pushGlow(x: number, y: number, z: number, size: number, rgb: readonly [number, number, number], a: number): void;
  pushGlint(x: number, y: number, z: number, size: number, rgb: readonly [number, number, number], a: number): void;
  burst(x: number, y: number, z: number, rgb: readonly [number, number, number], now: number, count?: number, radius?: number): void;
}

interface Flock {
  caster: number;
  /** The anchor (the caster's feet, following them until the birds leave). */
  x: number;
  y: number;
  z: number;
  /** The wand tip at the cast (absolute) and the caster's facing then. */
  tipX: number;
  tipY: number;
  tipZ: number;
  yaw: number;
  /** The turning point relative to the anchor at the cast (the birds' birthplace). */
  from: [number, number, number];
  /** The caster's head height above their feet (m). */
  head: number;
  start: number;
  durationS: number;
  birds: BirdSeed[];
  /** Whether the puff has been thrown (once, as the feather turns). */
  puffed: boolean;
}

const POSE: BirdPose = { x: 0, y: 0, z: 0, flap: 0, alpha: 0, grow: 0, alive: false };
const AHEAD: BirdPose = { x: 0, y: 0, z: 0, flap: 0, alpha: 0, grow: 0, alive: false };
const FEATHER: FeatherPose = { forward: 0, up: 0, side: 0, scale: 0, spin: 0, tilt: 0, glow: 0, alive: false };
/** How fast the anchor follows its caster (1/s, exponential). */
const FOLLOW_RATE = 3;

export class Volucra {
  private flocks: Flock[] = [];
  private body: Mesh | null = null;
  private wingL: Mesh | null = null;
  private wingR: Mesh | null = null;
  private feather: Mesh | null = null;
  /** The shoulders' places in the model (the wings' origins). */
  private readonly shoulderL = new Vector3(0.025, 0.02, 0);
  private readonly shoulderR = new Vector3(-0.025, 0.02, 0);
  private bodyBuf = new Float32Array(BIRDS_MAX * 16);
  private wingLBuf = new Float32Array(BIRDS_MAX * 16);
  private wingRBuf = new Float32Array(BIRDS_MAX * 16);
  private colours = new Float32Array(BIRDS_MAX * 4);
  private featherBuf = new Float32Array(BIRDS_MAX * 16);
  private featherColours = new Float32Array(BIRDS_MAX * 4);
  private disposed = false;
  private source = "loading";
  private drawn = 0;
  private feathers = 0;
  private lastMs = 0;
  private readonly m = new Matrix();
  private readonly w = new Matrix();
  private readonly s = new Matrix();
  private readonly q = new Quaternion();
  private readonly v = new Vector3();
  private readonly one = new Vector3(BIRD_SCALE, BIRD_SCALE, BIRD_SCALE);

  constructor(
    private readonly scene: Scene,
    assetsBase: string,
  ) {
    void this.load(assetUrl(`${assetsBase}volucra/birds.glb`));
  }

  /** Birds and feathers in the air last frame, and where the model came from. */
  get counts(): { birds: number; feathers: number; flocks: number; source: string } {
    return { birds: this.drawn, feathers: this.feathers, flocks: this.flocks.length, source: this.source };
  }

  /**
   * A flock released by `caster` standing at (`x`, `y`, `z`) facing `yaw`,
   * its wand tip at `tip`, its head `head` m above its feet: the feather,
   * then `count` birds for `durationMs`, seeded by the caster and the time so
   * viewers see much the same flight.
   */
  release(caster: number, x: number, y: number, z: number, yaw: number, head: number, tip: readonly [number, number, number], count: number, durationMs: number, now: number): void {
    const n = Math.max(1, Math.min(12, Math.floor(count)));
    const seed = (caster * 2654435761 + Math.floor(Date.now() / 1000)) >>> 0;
    const fx = Math.sin(yaw);
    const fz = Math.cos(yaw);
    // (The wand hand may still be low as the cast lands: the feather starts no lower than the chest.)
    const tipY = Math.max(tip[1], y + head * 0.62);
    const from: [number, number, number] = [tip[0] + fx * TURN_FORWARD - x, tipY + TURN_UP - y, tip[2] + fz * TURN_FORWARD - z];
    this.flocks.push({ caster, x, y, z, tipX: tip[0], tipY, tipZ: tip[2], yaw, from, head, start: now, durationS: Math.max(CONJURE_S + 0.5, durationMs / 1000), birds: flockSeeds(n, seed), puffed: false });
    // At most BIRDS_MAX in the air: the oldest flocks give way.
    let total = this.flocks.reduce((a, f) => a + f.birds.length, 0);
    while (total > BIRDS_MAX && this.flocks.length > 1) total -= this.flocks.shift()!.birds.length;
    const turn: [number, number, number] = [x + from[0], y + from[1], z + from[2]];
    audio.playAt("volucra.flutter", turn, { owner: caster, delayS: CONJURE_S - 0.05 });
    const chirps = 2 + (seed % 2);
    for (let k = 0; k < chirps; k++) audio.playAt("volucra.chirp", [x, y + head + HEAD_CLEARANCE, z], { owner: caster, delayS: CONJURE_S + 0.3 + k * 0.7 + ((seed >> (k * 3)) % 7) * 0.08 });
  }

  private async load(url: string): Promise<void> {
    let body: Mesh | null = null;
    let wingL: Mesh | null = null;
    let wingR: Mesh | null = null;
    let feather: Mesh | null = null;
    try {
      const c = await LoadAssetContainerAsync(url, this.scene, { pluginExtension: ".glb" });
      if (this.disposed) {
        c.dispose();
        return;
      }
      toonifyContainer(c, this.scene);
      c.addAllToScene();
      for (const m of c.meshes) {
        if (!(m instanceof Mesh) || m.getTotalVertices() === 0) continue;
        m.computeWorldMatrix(true);
        const name = m.name.toLowerCase();
        const wing = name.includes("wing_l") ? "l" : name.includes("wing_r") ? "r" : null;
        const pivot = m.getAbsolutePosition().clone();
        m.setParent(null);
        m.bakeCurrentTransformIntoVertices();
        if (wing) {
          // Origin at the shoulder: the wing flaps about it.
          m.bakeTransformIntoVertices(Matrix.Translation(-pivot.x, -pivot.y, -pivot.z));
          (wing === "l" ? this.shoulderL : this.shoulderR).copyFrom(pivot);
          if (wing === "l") wingL = m;
          else wingR = m;
        } else if (name.includes("feather")) {
          m.bakeTransformIntoVertices(Matrix.Translation(-pivot.x, -pivot.y, -pivot.z));
          feather = m;
        } else if (name.includes("body")) body = m;
      }
      for (const n of c.transformNodes) n.dispose();
      for (const m of c.meshes) if (m !== body && m !== wingL && m !== wingR && m !== feather && !m.isDisposed()) m.dispose();
      this.source = body && wingL && wingR && feather ? "birds.glb" : "placeholder";
    } catch {
      this.source = "placeholder";
    }
    if (this.disposed) {
      for (const m of [body, wingL, wingR, feather]) m?.dispose();
      return;
    }
    if (!body || !wingL || !wingR) {
      for (const m of [body, wingL, wingR]) m?.dispose();
      [body, wingL, wingR] = placeholderBird(this.scene, this.shoulderL, this.shoulderR);
    }
    feather ??= placeholderFeather(this.scene);
    for (const [m, name, buf, colours] of [
      [body, "volucra-body", this.bodyBuf, this.colours],
      [wingL, "volucra-wing-l", this.wingLBuf, this.colours],
      [wingR, "volucra-wing-r", this.wingRBuf, this.colours],
      [feather, "volucra-feather", this.featherBuf, this.featherColours],
    ] as const) {
      m.name = name;
      m.isPickable = false;
      // Birds fly round whoever cast them, anywhere: never culled as a whole.
      m.alwaysSelectAsActiveMesh = true;
      m.thinInstanceSetBuffer("matrix", buf, 16, false);
      m.thinInstanceSetBuffer("color", colours, 4, false);
      m.thinInstanceCount = 0;
      m.setEnabled(false);
    }
    this.body = body;
    this.wingL = wingL;
    this.wingR = wingR;
    this.feather = feather;
  }

  /**
   * Once a frame (between the effect layers' begin and end): the feathers
   * float and turn, every bird in the air flies on, finished flocks go.
   * `casterAt` is where a caster stands now (null: not drawn); the flock's
   * circle keeps over them until the birds leave.
   */
  update(now: number, fx: VolucraFx, casterAt: (id: number) => { x: number; y: number; z: number } | null): void {
    const dt = this.lastMs ? Math.min(0.1, Math.max(0, (now - this.lastMs) / 1000)) : 0;
    this.lastMs = now;
    this.flocks = this.flocks.filter((f) => (now - f.start) / 1000 < f.durationS);
    const body = this.body;
    const wl = this.wingL;
    const wr = this.wingR;
    const fe = this.feather;
    if (!body || !wl || !wr || !fe) return;
    let n = 0;
    let nf = 0;
    for (const f of this.flocks) {
      const t = (now - f.start) / 1000;
      // The circle keeps over the caster while the birds rise and circle.
      const leaving = t > CONJURE_S + (f.durationS - CONJURE_S) * CIRCLE_END;
      const p = leaving ? null : casterAt(f.caster);
      if (p) {
        const k = 1 - Math.exp(-dt * FOLLOW_RATE);
        f.x += (p.x - f.x) * k;
        f.y += (p.y - f.y) * k;
        f.z += (p.z - f.z) * k;
      }
      if (t < CONJURE_S) {
        this.drawFeather(f, t, nf++, fx);
        continue;
      }
      if (!f.puffed) {
        f.puffed = true;
        fx.burst(f.x + f.from[0], f.y + f.from[1], f.z + f.from[2], GOLD, now, 14, 0.45);
      }
      if (t < CONJURE_S + PUFF_S) this.drawPuff(f, (t - CONJURE_S) / PUFF_S, fx);
      for (const b of f.birds) {
        if (n >= BIRDS_MAX) break;
        birdAt(t, f.durationS, b, f.from, f.head, POSE);
        if (!POSE.alive || POSE.alpha <= 0.01) continue;
        birdAt(t + 0.05, f.durationS, b, f.from, f.head, AHEAD);
        const dx = AHEAD.x - POSE.x;
        const dy = AHEAD.y - POSE.y;
        const dz = AHEAD.z - POSE.z;
        const yaw = Math.atan2(dx, dz);
        const pitch = -Math.atan2(dy, Math.hypot(dx, dz) || 1e-4);
        // Newborns grow; fading birds shrink a little as they fly off into the distance.
        const size = BIRD_SCALE * POSE.grow * (0.6 + 0.4 * POSE.alpha);
        this.one.set(size, size, size);
        Quaternion.RotationYawPitchRollToRef(yaw, pitch * 0.6, 0, this.q);
        this.v.set(f.x + POSE.x, f.y + POSE.y, f.z + POSE.z);
        Matrix.ComposeToRef(this.one, this.q, this.v, this.m);
        this.m.copyToArray(this.bodyBuf, n * 16);
        for (const [shoulder, sign, buf] of [
          [this.shoulderL, 1, this.wingLBuf],
          [this.shoulderR, -1, this.wingRBuf],
        ] as const) {
          // The wing turns about the body's forward axis at its shoulder, then goes with the body.
          Matrix.RotationZToRef(sign * POSE.flap, this.w);
          Matrix.TranslationToRef(shoulder.x, shoulder.y, shoulder.z, this.s);
          this.w.multiplyToRef(this.s, this.w);
          this.w.multiplyToRef(this.m, this.w);
          this.w.copyToArray(buf, n * 16);
        }
        const c = SONGBIRDS[b.colour]!;
        const o = n * 4;
        this.colours[o] = c[0];
        this.colours[o + 1] = c[1];
        this.colours[o + 2] = c[2];
        this.colours[o + 3] = 1;
        n++;
      }
    }
    this.drawn = n;
    this.feathers = nf;
    for (const [m, count] of [
      [body, n],
      [wl, n],
      [wr, n],
      [fe, nf],
    ] as const) {
      m.thinInstanceCount = count;
      if (count > 0) {
        m.thinInstanceBufferUpdated("matrix");
        m.thinInstanceBufferUpdated("color");
      }
      m.setEnabled(count > 0);
    }
  }

  /** The conjured feather of flock `f` at `t` s, as instance `i`, with its gold shimmer. */
  private drawFeather(f: Flock, t: number, i: number, fx: VolucraFx): void {
    const p = conjureAt(t, FEATHER);
    if (!p.alive || i >= BIRDS_MAX) return;
    const sx = Math.sin(f.yaw);
    const sz = Math.cos(f.yaw);
    // Right of the caster: (cos, -sin) of the facing.
    const x = f.tipX + sx * p.forward + sz * p.side;
    const y = f.tipY + p.up;
    const z = f.tipZ + sz * p.forward - sx * p.side;
    const size = FEATHER_SCALE * p.scale;
    this.one.set(size, size, size);
    // Its quill along the caster's facing, tilted up, turning slowly about itself.
    Quaternion.RotationYawPitchRollToRef(f.yaw, -p.tilt, p.spin, this.q);
    this.v.set(x, y, z);
    Matrix.ComposeToRef(this.one, this.q, this.v, this.m);
    this.m.copyToArray(this.featherBuf, i * 16);
    // Pale, warming to gold as the shimmer swells.
    const o = i * 4;
    const g = p.glow;
    this.featherColours[o] = FEATHER_WHITE[0] + (GOLD[0] - FEATHER_WHITE[0]) * g * 0.8;
    this.featherColours[o + 1] = FEATHER_WHITE[1] + (GOLD[1] - FEATHER_WHITE[1]) * g * 0.8;
    this.featherColours[o + 2] = FEATHER_WHITE[2] + (GOLD[2] - FEATHER_WHITE[2]) * g * 0.8;
    this.featherColours[o + 3] = 1;
    // The shimmer: a soft gold glow and glints circling it, quickening.
    fx.pushGlow(x, y, z, 0.12 + 0.14 * g, GOLD, (0.12 + 0.3 * g) * p.scale);
    const glints = 3;
    for (let k = 0; k < glints; k++) {
      const a = t * (6 + 6 * g) + (k / glints) * Math.PI * 2;
      const r = 0.07 + 0.05 * g;
      fx.pushGlint(x + Math.cos(a) * r, y + Math.sin(a * 1.3) * r * 0.8, z + Math.sin(a) * r, 0.05 + 0.06 * g, GOLD, 0.4 + 0.6 * g);
    }
  }

  /** The puff where the feather turned, `k` 0..1 through it: soft white glows swelling and fading. */
  private drawPuff(f: Flock, k: number, fx: VolucraFx): void {
    const x = f.x + f.from[0];
    const y = f.y + f.from[1];
    const z = f.z + f.from[2];
    const fade = (1 - k) * (1 - k);
    const r = 0.08 + 0.22 * Math.sqrt(k);
    fx.pushGlow(x, y, z, 0.25 + 0.3 * k, PUFF_WHITE, 0.6 * fade);
    for (let i = 0; i < 5; i++) {
      const a = (i / 5) * Math.PI * 2 + 0.6;
      fx.pushGlow(x + Math.cos(a) * r, y + Math.sin(a * 2) * r * 0.5 + 0.05 * k, z + Math.sin(a) * r, 0.16 + 0.18 * k, PUFF_WHITE, 0.45 * fade);
    }
  }

  /** A zone change: the birds stay behind. */
  clear(): void {
    this.flocks = [];
  }

  dispose(): void {
    this.disposed = true;
    for (const m of [this.body, this.wingL, this.wingR, this.feather]) m?.dispose(false, true);
    this.body = this.wingL = this.wingR = this.feather = null;
    this.flocks = [];
  }
}

function standinMaterial(scene: Scene): StandardMaterial {
  const mat = new StandardMaterial("volucra-standin", scene);
  mat.diffuseColor = new Color3(1, 1, 1);
  mat.specularColor = Color3.Black();
  mat.backFaceCulling = false;
  new ToonPlugin(mat, worldRamp("volucra"));
  return mat;
}

function flatMesh(name: string, scene: Scene, p: number[], idx: number[], mat: StandardMaterial): Mesh {
  const m = new Mesh(name, scene);
  const d = new VertexData();
  d.positions = p;
  d.indices = idx;
  const n: number[] = [];
  VertexData.ComputeNormals(p, idx, n);
  d.normals = n;
  d.applyToMesh(m);
  m.material = mat;
  return m;
}

/**
 * The stand-in bird (no model yet): a small tapered body with a beak and two
 * flat wings, pale (the instance colour tints it), toon lit. Its wings'
 * origins are at the shoulders `l` and `r`.
 */
function placeholderBird(scene: Scene, l: Vector3, r: Vector3): [Mesh, Mesh, Mesh] {
  const mat = standinMaterial(scene);
  // Body: an octahedron stretched along +z (head) with a tail fan; beak a small point.
  const bp = [0, 0, 0.09, 0.03, 0, 0.01, 0, 0.035, 0.01, -0.03, 0, 0.01, 0, -0.025, 0.01, 0, 0.005, -0.05, 0.03, 0.005, -0.1, -0.03, 0.005, -0.1, 0, 0.012, 0.12];
  const bi = [0, 1, 2, 0, 2, 3, 0, 3, 4, 0, 4, 1, 5, 2, 1, 5, 3, 2, 5, 4, 3, 5, 1, 4, 5, 6, 7, 0, 2, 8];
  const body = flatMesh("volucra-body", scene, bp, bi, mat);
  const wing = (side: 1 | -1, at: Vector3): Mesh => {
    at.set(side * 0.025, 0.02, 0.01);
    const p = [0, 0, 0.03, 0, 0, -0.03, side * 0.11, 0.005, -0.02, side * 0.07, 0.004, 0.025];
    const idx = side > 0 ? [0, 2, 1, 0, 3, 2] : [0, 1, 2, 0, 2, 3];
    return flatMesh(side > 0 ? "volucra-wing-l" : "volucra-wing-r", scene, p, idx, mat);
  };
  return [body, wing(1, l), wing(-1, r)];
}

/** The stand-in feather (no model yet): a flat leaf-shaped vane along +z, about its middle. */
function placeholderFeather(scene: Scene): Mesh {
  const p = [0, 0, -0.07, 0.016, 0.002, -0.03, 0.02, 0.004, 0.01, 0.012, 0.006, 0.05, 0, 0.008, 0.065, -0.011, 0.006, 0.04, -0.013, 0.004, 0.0, -0.01, 0.002, -0.035];
  const idx = [0, 1, 7, 1, 2, 6, 1, 6, 7, 2, 3, 5, 2, 5, 6, 3, 4, 5];
  return flatMesh("volucra-feather", scene, p, idx, standinMaterial(scene));
}
