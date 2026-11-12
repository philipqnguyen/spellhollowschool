// Custodio's companions in the running game (spells to level 20,
// GUARDIAN; docs/architecture.md "Spells and dueling").
//
// Every drawn character whose visible effects hold a `guardian` (the
// effects book, filled by the server's EffectsChanged and snapshots, so
// every nearby player sees the same animal) gets a silver companion: the
// nearest MAX_DRAWN of them (the fx subjects come nearest the camera
// first), the rest not drawn. A companion follows its holder (follow.ts),
// or circles them while `guarding` (a duel ward), animates its parts by its
// animal's gait, and dissolves when the effect ends while its holder is in
// view (a holder who leaves the view, logs out or changes zone takes theirs
// at once, as their body goes). Cast while we watch, it bursts out of the
// holder's wand tip in a silver flash and bounds to its place (follow.ts
// `summonAt`); one already out when its holder comes into view materialises
// where it stands, in a burst of silver mist. It is drawn about the real
// animal's size (follow.ts `PRESENCE`), with a soft halo and a trail of
// light motes as it moves. `flare(entity)` is a ward's block: a bright pulse
// and a silver shatter of sparks.
//
// Cost: one draw per companion (one merged mesh, parts moved in its vertex
// shader: rig.ts, material.ts), at most MAX_DRAWN, none beyond DRAW_FAR of
// the camera; their halos, motes, flashes and mist share one VfxLayer (one
// draw). Beyond DETAIL_FAR a companion holds an idle pose and trails no
// motes. Nothing allocates per frame once a companion exists.
//
// Sound: `guardian.fade` as one dissolves, a soft `guardian.ambient` loop
// on the nearest (the summon sound is the effects controller's, as every
// visible effect's beginning is).

import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { ShaderMaterial } from "@babylonjs/core/Materials/shaderMaterial";
import { BoundingInfo } from "@babylonjs/core/Culling/boundingInfo";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { Scene } from "@babylonjs/core/scene";
import { audio, type LoopHandle } from "../../audio";
import type { VisibleEffect } from "../../wasm/sim_wasm";
import type { FxSubject } from "../effects/controller";
import { BurstPool, type BurstSpec } from "../vfx/bursts";
import { Shape, VfxLayer } from "../vfx/layer";
import {
  cadence,
  followRoom,
  gaitPose,
  GAITS,
  guardScale,
  newFollow,
  newPose,
  PRESENCE,
  stepFollow,
  stepGuard,
  summonAt,
  type AnimalKey,
  type FollowState,
  type Pose,
  type SummonPose,
} from "./follow";
import { createGuardianMaterial, SILVER, type GuardianDraw } from "./material";
import { ANIMALS, loadModels, standIn, type AnimalModel } from "./models";
import { MAX_PARTS, partMatrices } from "./rig";

/** Most companions drawn at once (the nearest holders). */
export const MAX_DRAWN = 12;
/** Beyond this camera distance (m) a companion is not drawn. */
export const DRAW_FAR = 45;
/** Beyond this camera distance (m) it holds an idle pose and trails no motes. */
export const DETAIL_FAR = 22;
/** Materialising and dissolving (s). */
export const MATERIALISE_S = 0.8;
export const DISSOLVE_S = 1.0;
/** A ward's flare (s). */
export const FLARE_S = 0.7;
/** Light motes shed along the body: per second standing, and more at a full stride. */
export const MOTES_IDLE = 4;
export const MOTES_MOVING = 16;
/** How long a mote lingers (s). */
const MOTE_LIFE = 0.9;
/** The ambient loop plays on the nearest companion within this distance (m). */
const AMBIENT_RANGE = 14;
const SPRITES = 900;
/** Particles alive at once (motes, flashes, mist, shatters), every companion together. */
const PARTICLES = 480;
/** How far (m) a part may swing past the animal's resting box (culling margin), as a share of its size. */
const POSE_REACH = 0.3;
const MIST: readonly [number, number, number] = [0.82, 0.92, 1];
/** The body's soft halo at rest (a flare adds to it), and the brighter core inside it. */
const HALO_ALPHA = 0.34;
const CORE_ALPHA = 0.14;
/** A summon's shown ramp (s): it comes out of the flash already formed. */
const SUMMON_FORM_S = 0.2;

/**
 * How many motes a companion moving at `speed` m/s (a full stride at
 * `full`) sheds over `dt` s: the whole ones due now; the fraction stays in
 * `carry.owed` for the next frame.
 */
export function motesDue(speed: number, full: number, dt: number, carry: { owed: number }): number {
  const rate = MOTES_IDLE + (MOTES_MOVING - MOTES_IDLE) * Math.min(1, speed / Math.max(1e-3, full));
  const due = carry.owed + rate * Math.max(0, dt);
  const n = Math.floor(due);
  carry.owed = due - n;
  return n;
}

export interface GuardianHubOptions {
  scene: Scene;
  /** Where `guardian/guardian.glb` and its manifest live. */
  assetsBase: string;
  /** A character's visible effects (the effects book). */
  effectsOf: (id: number) => readonly VisibleEffect[];
  /** Where a drawn character's wand tip is (the summon bursts from it); false: estimated from its pose. */
  wandTip?: (id: number, out: Vector3) => boolean;
}

type GuardianEffect = Extract<VisibleEffect, { kind: "guardian" }>;

/** The guardian `effects` show, if any. */
export function guardianOf(
  effects: readonly VisibleEffect[],
): GuardianEffect | null {
  for (const e of effects) if (e.kind === "guardian") return e;
  return null;
}

/**
 * The holders that get a companion this frame: the first `max` of
 * `subjects` (nearest the camera first) that show a guardian, into `out`.
 */
export function pickHolders(
  subjects: readonly { id: number }[],
  effectsOf: (id: number) => readonly VisibleEffect[],
  max: number,
  out: number[],
): number[] {
  out.length = 0;
  for (const s of subjects) {
    if (out.length >= max) break;
    if (guardianOf(effectsOf(s.id))) out.push(s.id);
  }
  return out;
}

/**
 * How formed a companion is (0..1): eased in over MATERIALISE_S of `age`,
 * out over DISSOLVE_S once it has been dissolving for `dissolving` s
 * (null: not dissolving), from however formed it was.
 */
export function shownAt(age: number, dissolving: number | null): number {
  const ease = (x: number) => {
    const c = Math.max(0, Math.min(1, x));
    return c * c * (3 - 2 * c);
  };
  const formed = ease(age / MATERIALISE_S);
  if (dissolving === null) return formed;
  return formed * (1 - ease(dissolving / DISSOLVE_S));
}

interface Companion {
  id: number;
  animal: AnimalKey;
  guarding: boolean;
  model: AnimalModel;
  mesh: Mesh;
  draw: GuardianDraw;
  follow: FollowState;
  pose: Pose;
  phase: number;
  age: number;
  /** Seconds since it began dissolving, or null. */
  dissolving: number | null;
  flare: number;
  /** Seconds since it burst from the wand tip (`tip`), or null: not being summoned. */
  summon: number | null;
  tip: [number, number, number];
  /** Light motes: the fraction of one owed (`motesDue`). */
  motes: { owed: number };
  /** Its glow point this frame. */
  gx: number;
  gy: number;
  gz: number;
  scale: number;
  drawn: boolean;
}

export class GuardianHub {
  private readonly effectsOf: (id: number) => readonly VisibleEffect[];
  private readonly wandTip: ((id: number, out: Vector3) => boolean) | null;
  private readonly tipV = new Vector3();
  /** Characters drawn last frame without a guardian: one appearing on them now was cast as we watched (a summon). */
  private without = new Set<number>();
  private withoutNext = new Set<number>();
  private readonly summonPose: SummonPose = { x: 0, y: 0, z: 0, grow: 1 };
  /** A mote's spec, reused (the pool copies it). */
  private readonly mote: BurstSpec = { shape: Shape.orb, x: 0, y: 0, z: 0, life: MOTE_LIFE, size: 0.08, sizeEnd: 0.02, rgb: [0.82, 0.92, 1], alpha: 0.85, gravity: -0.15 };
  private readonly material: ShaderMaterial;
  private readonly layer: VfxLayer;
  private readonly bursts = new BurstPool(PARTICLES);
  private models = new Map<AnimalKey, AnimalModel>();
  private source = "stand-in";
  private readonly companions = new Map<number, Companion>();
  private readonly picked: number[] = [];
  private readonly pickedSet = new Set<number>();
  private readonly gone: number[] = [];
  private subjects: readonly FxSubject[] = [];
  private camera: Camera | null = null;
  private clock = 0;
  private drawnCount = 0;
  private ambient: LoopHandle | null = null;
  private readonly ambientAt: [number, number, number] = [0, 0, 0];
  private seed = 1;
  private disposed = false;

  constructor(opts: GuardianHubOptions) {
    this.effectsOf = opts.effectsOf;
    this.wandTip = opts.wandTip ?? null;
    this.material = createGuardianMaterial(opts.scene, () => this.camera);
    this.layer = new VfxLayer(opts.scene, "guardian", SPRITES);
    for (const key of ANIMALS) this.models.set(key, standIn(key, opts.scene));
    void loadModels(opts.scene, opts.assetsBase).then((loaded) => {
      if (this.disposed) {
        for (const m of loaded?.models.values() ?? []) m.template.dispose();
        return;
      }
      if (!loaded) {
        this.source = "stand-in (no guardian.glb)";
        return;
      }
      for (const m of this.models.values()) m.template.dispose();
      this.models = loaded.models;
      this.source = "guardian.glb";
      // Companions out now take the real animal.
      for (const c of this.companions.values()) this.remesh(c);
    });
  }

  /** Companions alive (drawn or not). */
  get size(): number {
    return this.companions.size;
  }

  /**
   * Once a frame, after the characters are posed: `subjects` are the
   * characters drawn this frame, nearest the camera first.
   */
  update(dt: number, subjects: readonly FxSubject[], camera: Camera): void {
    this.camera = camera;
    this.subjects = subjects;
    this.clock += dt;
    pickHolders(subjects, this.effectsOf, MAX_DRAWN, this.picked);
    this.pickedSet.clear();
    for (const id of this.picked) this.pickedSet.add(id);
    // New, changed and returning companions.
    for (const id of this.picked) {
      const pe = guardianOf(this.effectsOf(id))!;
      let c = this.companions.get(id);
      if (c && c.animal !== pe.animal) {
        this.remove(c);
        c = undefined;
      }
      if (!c) {
        // Cast as we watched (drawn last frame without one): it bursts from the wand.
        c = this.create(id, pe.animal, this.without.has(id));
        this.companions.set(id, c);
      } else if (c.dissolving !== null) {
        // The effect is back (a recast as it faded): form again from here.
        c.age = shownAt(c.age, c.dissolving) * MATERIALISE_S;
        c.dissolving = null;
      }
      c.guarding = pe.guarding;
    }
    // Those not picked: dissolve when the effect ended in view; otherwise gone at once.
    this.gone.length = 0;
    for (const c of this.companions.values()) {
      if (this.pickedSet.has(c.id)) continue;
      const holder = this.subject(c.id);
      if (holder && !guardianOf(this.effectsOf(c.id))) {
        if (c.dissolving === null) this.startDissolve(c);
      } else if (c.dissolving === null || !holder) this.gone.push(c.id);
    }
    for (const id of this.gone) this.remove(this.companions.get(id)!);
    // Who is drawn without a guardian now (next frame's summons).
    const next = this.withoutNext;
    next.clear();
    for (const s of subjects) if (!guardianOf(this.effectsOf(s.id))) next.add(s.id);
    this.withoutNext = this.without;
    this.without = next;

    // Move, pose and draw.
    const eye = camera.globalPosition;
    this.bursts.step(dt);
    this.layer.begin();
    this.drawnCount = 0;
    let nearest: Companion | null = null;
    let nearestD = AMBIENT_RANGE;
    this.gone.length = 0;
    for (const c of this.companions.values()) {
      const holder = this.subject(c.id);
      c.age += dt;
      if (c.summon !== null) c.summon += dt;
      if (c.dissolving !== null) {
        c.dissolving += dt;
        if (c.dissolving >= DISSOLVE_S) {
          this.gone.push(c.id);
          continue;
        }
      }
      c.flare = Math.max(0, c.flare - dt / FLARE_S);
      if (holder) this.move(c, holder, dt);
      const dx = c.follow.x - eye.x;
      const dy = c.follow.y - eye.y;
      const dz = c.follow.z - eye.z;
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz);
      c.drawn = d <= DRAW_FAR && this.drawnCount < MAX_DRAWN;
      if (!c.drawn) {
        c.mesh.setEnabled(false);
        continue;
      }
      this.drawnCount++;
      this.pose(c, dt, d <= DETAIL_FAR);
      if (d <= DETAIL_FAR) this.shedMotes(c, dt);
      this.glow(c);
      if (c.dissolving === null && d < nearestD) {
        nearest = c;
        nearestD = d;
      }
    }
    for (const id of this.gone) this.remove(this.companions.get(id)!);
    this.bursts.forEach((shape, x, y, z, size, r, g, b, a) => {
      this.layer.push(shape, x, y, z, size, r, g, b, a);
    });
    this.layer.end(camera);
    this.ambientFor(nearest);
  }

  /** A ward blocked a spell: the guarding companion (or, if none, the spot) flares. */
  flare(entity: number): void {
    const c = this.companions.get(entity);
    if (c) {
      c.flare = 1;
      this.shatter(c.gx, c.gy, c.gz, c.scale);
      return;
    }
    const s = this.subject(entity);
    if (s) this.shatter(s.x, s.y + 1.15, s.z, 0.6);
  }

  perfLines(): [string, string][] {
    return [
      [
        "guardian",
        `${this.drawnCount}/${this.companions.size} drawn (${this.source}), ${this.layer.size} sprites`,
      ],
    ];
  }

  dispose(): void {
    this.disposed = true;
    for (const c of [...this.companions.values()]) this.remove(c);
    for (const m of this.models.values()) m.template.dispose();
    this.models.clear();
    this.ambient?.stop(0.2);
    this.ambient = null;
    this.layer.dispose();
    this.material.dispose();
  }

  // ---------------------------------------------------------------- internals

  private subject(id: number): FxSubject | null {
    for (const s of this.subjects) if (s.id === id) return s;
    return null;
  }

  private create(id: number, animal: AnimalKey, summoned: boolean): Companion {
    const model = this.models.get(animal)!;
    const draw: GuardianDraw = {
      parts: new Float32Array(MAX_PARTS * 16),
      time: 0,
      shown: 0,
      flare: 0,
      seed: (this.seed++ * 7.31) % 50,
    };
    const c: Companion = {
      id,
      animal,
      guarding: false,
      model,
      mesh: null as unknown as Mesh,
      draw,
      follow: newFollow(),
      pose: newPose(),
      phase: 0,
      age: 0,
      dissolving: null,
      flare: 0,
      summon: null,
      tip: [0, 0, 0],
      motes: { owed: 0 },
      gx: 0,
      gy: 0,
      gz: 0,
      scale: 1,
      drawn: false,
    };
    this.remesh(c);
    const s = this.subject(id);
    if (s) {
      this.move(c, s, 0);
      this.glowPoint(c);
      if (summoned) {
        // Out of the wand tip in a silver flash, bounding to its place.
        const t = this.tipV;
        if (!this.wandTip?.(id, t)) {
          const f = Math.sin(s.yaw);
          const g = Math.cos(s.yaw);
          t.set(s.x + f * 0.45, s.y + 1.15, s.z + g * 0.45);
        }
        c.tip[0] = t.x;
        c.tip[1] = t.y;
        c.tip[2] = t.z;
        c.summon = 0;
        this.flash(t.x, t.y, t.z);
      } else this.mist(c.gx, c.gy, c.gz, 1);
    }
    return c;
  }

  /** How formed `c` is now: a summon comes out of its flash formed within SUMMON_FORM_S. */
  private shown(c: Companion): number {
    if (c.summon !== null && c.dissolving === null) return Math.min(1, c.summon / SUMMON_FORM_S);
    return shownAt(c.age, c.dissolving);
  }

  /** (Re)builds a companion's mesh from its animal's current model (a clone sharing the geometry). */
  private remesh(c: Companion): void {
    c.model = this.models.get(c.animal)!;
    c.mesh?.dispose();
    const mesh = c.model.template.clone(`guardian-${c.id}`, null, true, false);
    mesh.material = this.material;
    mesh.metadata = { guardian: c.draw };
    mesh.isPickable = false;
    mesh.alphaIndex = 999;
    // Culled by the camera's frustum: the animal's box, grown by how far a swinging part may reach.
    const box = c.model.template.getBoundingInfo().boundingBox;
    const grow = POSE_REACH * Math.max(...c.model.size) + 0.1;
    mesh.setBoundingInfo(
      new BoundingInfo(
        box.minimum.subtract(new Vector3(grow, grow, grow)),
        box.maximum.add(new Vector3(grow, grow, grow)),
      ),
    );
    mesh.doNotSyncBoundingInfo = false;
    mesh.setEnabled(false);
    c.mesh = mesh;
  }

  private remove(c: Companion): void {
    c.mesh.dispose();
    this.companions.delete(c.id);
  }

  private startDissolve(c: Companion): void {
    if (c.summon !== null) {
      // Dismissed mid-summon: it fades from however formed it came out.
      c.age = this.shown(c) * MATERIALISE_S;
      c.summon = null;
    }
    c.dissolving = 0;
    this.mist(c.gx, c.gy, c.gz, 0.8);
    audio.playAt("guardian.fade", [c.gx, c.gy, c.gz], { owner: c.id });
  }

  private move(c: Companion, h: FxSubject, dt: number): void {
    const m = c.model;
    if (c.guarding) {
      c.scale = guardScale(m.size);
      stepGuard(c.follow, h, m.midY * c.scale, dt);
    } else {
      // About the real animal's size; a long one keeps a little further off.
      c.scale = PRESENCE[c.animal];
      stepFollow(c.follow, h, m.followHeight, dt, followRoom(m.size[2] * c.scale));
    }
  }

  private pose(c: Companion, dt: number, detail: boolean): void {
    const m = c.model;
    const f = c.follow;
    const gait = m.gait;
    // A guard hovers in place: a gentle stride; a follower strides by its speed.
    const speed = c.guarding ? GAITS[gait].full * 0.3 : f.speed;
    const amount = Math.min(1, speed / GAITS[gait].full);
    if (detail) {
      c.phase = (c.phase + dt * cadence(gait, speed)) % 1;
      gaitPose(gait, c.phase, amount, this.clock + c.draw.seed, c.pose);
    } else {
      gaitPose(gait, 0, 0, 0, c.pose);
    }
    partMatrices(m.rig, c.pose, c.draw.parts);
    const mesh = c.mesh;
    let x = f.x;
    let y = f.y;
    let z = f.z;
    let yaw = f.yaw;
    let scale = c.scale;
    if (c.summon !== null) {
      // Bounding out of the wand tip to its place (the follow keeps moving its place meanwhile).
      const sp = this.summonPose;
      const [tx, ty, tz] = c.tip;
      if (summonAt(c.summon, tx, ty, tz, f.x, f.y, f.z, sp)) {
        x = sp.x;
        y = sp.y;
        z = sp.z;
        scale *= sp.grow;
        // Facing the way it bounds (its feet were at the tip: lowered by its middle so it leaves the wand body first).
        y -= m.glowY * scale * (1 - Math.min(1, c.summon / 0.3));
        const dx = f.x - tx;
        const dz = f.z - tz;
        if (dx * dx + dz * dz > 1e-4) yaw = Math.atan2(dx, dz);
      } else {
        c.summon = null;
        c.age = Math.max(c.age, MATERIALISE_S);
      }
    }
    mesh.position.set(x, y, z);
    mesh.rotation.y = yaw;
    mesh.scaling.setAll(scale);
    const shown = this.shown(c);
    c.draw.time = this.clock;
    c.draw.shown = shown;
    c.draw.flare = Math.max(c.flare, c.summon !== null ? 1 - c.summon / 0.5 : 0);
    mesh.setEnabled(shown > 0.002);
    c.gx = x;
    c.gy = y + m.glowY * scale;
    c.gz = z;
  }

  private glowPoint(c: Companion): void {
    c.gx = c.follow.x;
    c.gy = c.follow.y + c.model.glowY * c.scale;
    c.gz = c.follow.z;
  }

  /**
   * Light motes shed along the body (more as it moves): each stays where it
   * was shed, drifting up and fading, so a moving companion leaves a soft
   * trail of light behind it.
   */
  private shedMotes(c: Companion, dt: number): void {
    const shown = this.shown(c);
    if (shown < 0.3) return;
    const m = c.model;
    const n = motesDue(c.follow.speed, GAITS[m.gait].full, dt, c.motes);
    if (n <= 0) return;
    const b = this.bursts;
    const half = m.size[2] * c.scale * 0.45;
    const tall = Math.max(0.12, m.size[1] * c.scale * 0.35);
    const fx = Math.sin(c.follow.yaw);
    const fz = Math.cos(c.follow.yaw);
    const p = this.mote;
    for (let i = 0; i < n; i++) {
      // Anywhere along the body, from the chest back to the tail.
      const along = (b.jitter() * 0.5 - 0.3) * 2 * half;
      p.x = c.gx + fx * along + b.jitter() * 0.06;
      p.y = c.gy + b.jitter() * tall;
      p.z = c.gz + fz * along + b.jitter() * 0.06;
      p.vx = b.jitter() * 0.08;
      p.vy = 0.22 + 0.12 * b.jitter();
      p.vz = b.jitter() * 0.08;
      p.shape = i % 3 === 0 ? Shape.spark : Shape.orb;
      p.size = (i % 3 === 0 ? 0.11 : 0.075) * Math.max(0.8, Math.min(1.4, c.scale));
      p.sizeEnd = 0.02;
      p.alpha = 0.85 * shown;
      b.spawn(p);
    }
  }

  /** The halo round the body and a brighter core inside it: a glow even without bloom (and the flare's). */
  private glow(c: Companion): void {
    const shown = this.shown(c);
    const m = c.model;
    const size = Math.max(m.size[0], m.size[1], m.size[2]) * c.scale;
    this.layer.push(
      Shape.orb,
      c.gx,
      c.gy,
      c.gz,
      size * (1.55 + c.flare * 0.9),
      SILVER[0],
      SILVER[1],
      SILVER[2],
      (HALO_ALPHA + 0.5 * c.flare) * shown,
    );
    this.layer.push(Shape.orb, c.gx, c.gy, c.gz, size * 0.7, MIST[0], MIST[1], MIST[2], CORE_ALPHA * shown);
  }

  /** A summon's flash at the wand tip: a bright silver burst, a ring and sparks flying out. */
  private flash(x: number, y: number, z: number): void {
    const b = this.bursts;
    b.spawn({ shape: Shape.orb, x, y, z, life: 0.35, size: 1.1, sizeEnd: 0.3, rgb: [1, 1, 1], alpha: 1 });
    b.spawn({ shape: Shape.orb, x, y, z, life: 0.6, size: 0.5, sizeEnd: 1.6, rgb: SILVER, alpha: 0.6 });
    b.spawn({ shape: Shape.ring, x, y, z, life: 0.45, size: 0.2, sizeEnd: 1.4, rgb: MIST, alpha: 0.9 });
    b.sparks(24, x, y, z, 2.8, MIST, 0.55, 0.13);
    this.mist(x, y, z, 0.6);
  }

  /** A burst of silver mist (materialise, dissolve). */
  private mist(x: number, y: number, z: number, k: number): void {
    const b = this.bursts;
    for (let i = 0; i < 16 * k; i++) {
      const a = (i / 16) * Math.PI * 2;
      const v = 0.5 + 0.4 * Math.abs(b.jitter());
      b.spawn({
        shape: Shape.orb,
        x,
        y,
        z,
        vx: Math.cos(a) * v,
        vy: 0.3 + 0.3 * b.jitter(),
        vz: Math.sin(a) * v,
        life: 0.9 + 0.3 * b.jitter(),
        size: 0.2,
        sizeEnd: 0.55,
        rgb: MIST,
        alpha: 0.35,
        gravity: -0.4,
      });
    }
    b.sparks(Math.round(10 * k), x, y, z, 1.4, MIST, 0.6, 0.1);
  }

  /** A ward's block: a ring, shards and sparks of silver. */
  private shatter(x: number, y: number, z: number, scale: number): void {
    const b = this.bursts;
    b.spawn({
      shape: Shape.ring,
      x,
      y,
      z,
      life: 0.45,
      size: 0.4 * scale + 0.2,
      sizeEnd: 1.8,
      rgb: MIST,
      alpha: 0.9,
    });
    b.spawn({
      shape: Shape.orb,
      x,
      y,
      z,
      life: 0.35,
      size: 0.9,
      sizeEnd: 1.4,
      rgb: [1, 1, 1],
      alpha: 0.8,
    });
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * Math.PI * 2;
      b.spawn({
        shape: Shape.shard,
        x,
        y,
        z,
        vx: Math.cos(a) * 2.4,
        vy: 0.8 + b.jitter(),
        vz: Math.sin(a) * 2.4,
        life: 0.5,
        size: 0.14,
        sizeEnd: 0.05,
        rgb: MIST,
        alpha: 0.9,
        gravity: 4,
      });
    }
    b.sparks(18, x, y, z, 2.6, MIST, 0.5, 0.12);
  }

  private ambientFor(c: Companion | null): void {
    if (!c) {
      if (this.ambient) {
        this.ambient.stop(0.6);
        this.ambient = null;
      }
      return;
    }
    const at = this.ambientAt;
    at[0] = c.gx;
    at[1] = c.gy;
    at[2] = c.gz;
    if (!this.ambient)
      this.ambient = audio.loop("guardian.ambient", { at, gain: 0.5 });
    else this.ambient.setPosition(at);
  }
}
