// The Flying class's ring course (docs/architecture.md):
// the class layout's rings (`SimWorld.rings()`, data/world/instances/
// class_pitch.json) drawn as one glowing hoop mesh with a thin instance per
// ring (one draw call for the course). Rings glow in turn on the beat: the
// ring of the next beat pulses, brightest on its beat; a ring you flew
// through in its window flashes green (at once from our own prediction,
// `SimWorld.passesRing`; the server's `BeatScored` decides the marks); a
// missed one dims for a moment. Colours are palette names
// (data/art/castle_textures.json): the hoops `gilt`, the next ring
// `glow_sun`, a pass `glow_green`, a miss `steel_dark`.
//
// The hoop is the Pitch export's `ring_hoop` prop (`assets/instances/
// props/ring_hoop.glb`: a 1 m radius hoop facing +Z, one draw) when it
// exists, else a torus built here; either way it is scaled to each ring's
// radius, turned to its yaw and coloured per instance, unlit, so it reads
// as a glow at any hour. `ringLooks` (pure, rings.test.ts) decides each
// ring's colour and glow.

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Matrix, Quaternion, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { CreateTorus } from "@babylonjs/core/Meshes/Builders/torusBuilder";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { Scene } from "@babylonjs/core/scene";
import type { Ring } from "../../wasm/sim_wasm";
import { paletteRgb } from "../castle/terrain";
import type { BeatState } from "./lesson";
import { assetUrl } from "../../assets";

const PROP_PATH = "assets/instances/props/ring_hoop.glb";
/** A pass (or a miss) shows this long after its beat's window (ms). */
export const FLASH_MS = 1500;
/** The next ring's glow pulses this many times a second as its beat nears. */
const PULSE_HZ = 2;
/** It starts to brighten this long before its beat (ms), or a whole window if longer. */
export const LEAD_MS = 2500;
/** The lowest glow of the next ring (so it always stands out from the rest). */
const NEXT_FLOOR = 0.45;

export type RingColour = "rest" | "next" | "passed" | "missed";

export interface RingLook {
  colour: RingColour;
  /** 0..1: how bright it glows (the rest are steady). */
  glow: number;
}

/**
 * Each ring's look at `nowMs` (server clock) for a round's `beats` (null:
 * no round under way: every ring at rest). Of the beats, the first not yet
 * tried whose window is open or to come is the next: its ring pulses, more
 * as the beat nears; a beat flown through (sent, or scored above 0) flashes
 * its ring green for FLASH_MS after its window; a miss dims it as long.
 * The next ring wins over a flash (the course's second lap reuses rings).
 */
export function ringLooks(ringCount: number, beats: readonly BeatState[] | null, windowMs: number, nowMs: number, out: RingLook[] = []): RingLook[] {
  out.length = ringCount;
  for (let i = 0; i < ringCount; i++) {
    const l = (out[i] ??= { colour: "rest", glow: 0 });
    l.colour = "rest";
    l.glow = 0;
  }
  if (!beats) return out;
  for (const b of beats) {
    if (b.kind !== "ring" || b.ring < 0 || b.ring >= ringCount) continue;
    const since = nowMs - (b.atMs + windowMs);
    const passed = b.status === "sent" || (b.status === "scored" && !!b.result && !b.result.rejected && b.result.score > 0);
    const missed = b.status === "missed" || (b.status === "scored" && !passed);
    if (since > FLASH_MS) continue;
    if (passed) set(out[b.ring]!, "passed", 1);
    else if (missed && since >= 0 && out[b.ring]!.colour === "rest") set(out[b.ring]!, "missed", 0);
  }
  const next = beats.find((b) => b.kind === "ring" && b.status === "upcoming" && nowMs <= b.atMs + windowMs && b.ring >= 0 && b.ring < ringCount);
  if (next) {
    const lead = next.atMs - nowMs;
    const reach = Math.max(LEAD_MS, windowMs);
    const near = 1 - Math.min(1, Math.abs(lead) / reach);
    const beat = 0.5 + 0.5 * Math.cos(((nowMs - next.atMs) / 1000) * PULSE_HZ * 2 * Math.PI);
    set(out[next.ring]!, "next", Math.min(1, NEXT_FLOOR + (1 - NEXT_FLOOR) * near * (0.6 + 0.4 * beat)));
  }
  return out;
}

function set(l: RingLook, colour: RingColour, glow: number): void {
  l.colour = colour;
  l.glow = glow;
}

type Rgb = [number, number, number];

/** The course's palette colours (sRGB 0..1). */
export function ringColours(): Record<RingColour, Rgb> {
  const grey: Rgb = [0.6, 0.6, 0.6];
  return {
    rest: paletteRgb(["gilt", "brass"]) ?? grey,
    next: paletteRgb(["glow_sun", "glow_lantern"]) ?? grey,
    passed: paletteRgb(["glow_green", "plant"]) ?? grey,
    missed: paletteRgb(["steel_dark", "iron"]) ?? grey,
  };
}

/** How bright each look draws (a multiplier of its colour): the rest dimmer, the next one by its glow. */
export function ringBrightness(l: RingLook): number {
  switch (l.colour) {
    case "rest":
      return 0.55;
    case "missed":
      return 0.45;
    case "passed":
      return 1;
    case "next":
      return 0.55 + 0.45 * l.glow;
  }
}

const scratchQ = new Quaternion();
const scratchS = new Vector3();
const scratchP = new Vector3();
const scratchM = new Matrix();

/** The ring course in the scene: one mesh, a thin instance per ring. */
export class RingCourseView {
  private mesh: Mesh | null = null;
  private rings: readonly Ring[] = [];
  private matrices = new Float32Array(0);
  private colours = new Float32Array(0);
  private readonly looks: RingLook[] = [];
  private readonly palette = ringColours();
  private generation = 0;
  private disposed = false;

  constructor(private readonly scene: Scene) {}

  /** Shows `rings` (a class layout's; an empty list hides the course). */
  show(rings: readonly Ring[]): void {
    this.rings = rings;
    const gen = ++this.generation;
    if (!rings.length) {
      this.mesh?.setEnabled(false);
      return;
    }
    this.matrices = new Float32Array(rings.length * 16);
    this.colours = new Float32Array(rings.length * 4);
    rings.forEach((r, i) => {
      Quaternion.RotationYawPitchRollToRef(r.yaw, 0, 0, scratchQ);
      scratchS.setAll(r.radius);
      scratchP.set(r.pos[0], r.pos[1], r.pos[2]);
      Matrix.ComposeToRef(scratchS, scratchQ, scratchP, scratchM);
      scratchM.copyToArray(this.matrices, i * 16);
    });
    if (this.mesh) this.attach(this.mesh);
    else void this.build(gen);
  }

  private async build(gen: number): Promise<void> {
    let mesh: Mesh | null = null;
    try {
      const c = await LoadAssetContainerAsync(assetUrl(PROP_PATH), this.scene, { pluginExtension: ".glb" });
      c.addAllToScene();
      const parts = c.meshes.filter((m): m is Mesh => m instanceof Mesh && m.getTotalVertices() > 0);
      for (const m of parts) {
        m.computeWorldMatrix(true);
        m.setParent(null);
        m.bakeCurrentTransformIntoVertices();
      }
      mesh = parts.length > 1 ? Mesh.MergeMeshes(parts, true, true) : (parts[0] ?? null);
      for (const n of c.transformNodes) n.dispose();
      for (const m of c.meshes) if (m !== mesh && !m.isDisposed()) m.dispose();
      for (const m of c.materials) if (m !== mesh?.material) m.dispose();
    } catch {
      // The Pitch export's prop is not here: the torus below.
    }
    if (this.disposed || this.mesh) {
      mesh?.dispose();
      return;
    }
    mesh ??= torusHoop(this.scene);
    // Unlit and coloured per ring: the instance colour is the glow (unlit, Babylon's
    // standard material shows the emissive colour times the instance colour).
    mesh.material?.dispose();
    const mat = new StandardMaterial("ring-course", this.scene);
    mat.disableLighting = true;
    mat.emissiveColor = Color3.White();
    mat.diffuseColor = Color3.Black();
    mat.specularColor = Color3.Black();
    mat.backFaceCulling = false;
    mesh.material = mat;
    mesh.name = "ring-course";
    mesh.isPickable = false;
    mesh.alwaysSelectAsActiveMesh = true;
    this.mesh = mesh;
    if (gen === this.generation) this.attach(mesh);
    else this.show(this.rings);
  }

  private attach(mesh: Mesh): void {
    mesh.thinInstanceSetBuffer("matrix", this.matrices, 16, true);
    mesh.thinInstanceSetBuffer("color", this.colours, 4, false);
    mesh.thinInstanceCount = this.rings.length;
    mesh.setEnabled(this.rings.length > 0);
  }

  /** Colours the rings for this frame (`ringLooks` of the round under way; null: all at rest). */
  update(beats: readonly BeatState[] | null, windowMs: number, nowMs: number): void {
    const mesh = this.mesh;
    if (!mesh || !this.rings.length) return;
    ringLooks(this.rings.length, beats, windowMs, nowMs, this.looks);
    for (let i = 0; i < this.rings.length; i++) {
      const l = this.looks[i]!;
      const rgb = this.palette[l.colour];
      const k = ringBrightness(l);
      this.colours[i * 4] = rgb[0] * k;
      this.colours[i * 4 + 1] = rgb[1] * k;
      this.colours[i * 4 + 2] = rgb[2] * k;
      this.colours[i * 4 + 3] = 1;
    }
    mesh.thinInstanceBufferUpdated("color");
  }

  /** The look of ring `i` this frame (for the HUD cue), if shown. */
  look(i: number): RingLook | undefined {
    return this.looks[i];
  }

  dispose(): void {
    this.disposed = true;
    this.mesh?.dispose(false, true);
    this.mesh = null;
  }
}

/** A 1 m radius hoop facing +Z (the ring_hoop contract), until the prop exists. */
function torusHoop(scene: Scene): Mesh {
  const t = CreateTorus("ring-hoop", { diameter: 2, thickness: 0.12, tessellation: 40 }, scene);
  t.rotation.x = Math.PI / 2; // Babylon's torus lies flat (its axis +Y); stand it up facing +Z.
  t.bakeCurrentTransformIntoVertices();
  return t;
}
