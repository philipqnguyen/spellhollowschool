// Animated fire (docs/architecture.md "Rendering"): every fire, torch and
// candle of the loaded cells and rooms, drawn from the manifests' `fire`
// entries (data.ts) with the shaders of shaders.ts.
//
// Cost: three shared materials (flames, floor glow, embers; a second flame
// material for Low), compiled once. Each cell or room with fire adds up to
// three thin-instanced meshes (its flames, its floor glows, its embers) and
// one flame mesh per moving candle group, parented to that group's node so
// the flames ride its bob (castle/candles.ts) with no work of ours. Per
// frame: one `time` uniform per material and, for the moving groups, a
// visibility copy at Low (castle/candles.ts draws one group a cell there);
// nothing per flame. Instance buffers are written once when a cell loads.
//
// The meshes of a cell hang under its root node, so the cell's visibility
// (castle kit M2 `sees`) shows and hides them with it.

import { Constants } from "@babylonjs/core/Engines/constants";
import { ShaderMaterial } from "@babylonjs/core/Materials/shaderMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Matrix, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { VertexData } from "@babylonjs/core/Meshes/mesh.vertexData";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Node } from "@babylonjs/core/node";
import type { Scene } from "@babylonjs/core/scene";
import type { Quality } from "../../settings";
import { emberBuffers, FIRE_LOOK, parseFire, poolBuffers, tongueBuffers, type FireData, type FireLook, type InstanceBuffers } from "./data";
import { EMBER_FRAGMENT, EMBER_VERTEX, FLAME_FRAGMENT, FLAME_VERTEX, POOL_FRAGMENT, POOL_VERTEX } from "./shaders";
import { fireTime, flicker, ripple } from "./flicker";
import { GLOW_CLOCK } from "../toon/material";

/** Among transparent meshes: the floor glow first, then flames, then embers. */
const ALPHA_INDEX = { pool: 900, flame: 910, ember: 920 } as const;

interface FireSet {
  meshes: Mesh[];
  /** Flame meshes riding a moving node: drawn only while that node is (castle/candles.ts at Low). */
  riders: { mesh: Mesh; node: AbstractMesh }[];
}

/** A light that flickers with a fire (`flicker` on a manifest light): its base intensity and the fire's seed. */
interface Flickering {
  light: { intensity: number };
  base: number;
  seed: number;
  /** A lake window's light: `seed` is its swell's phase (`ripple`), not a fire's. */
  ripple: boolean;
}

const registry = new WeakMap<Scene, FireField>();

/** The scene's fire field (the world scene makes one), for the rooms' shells. */
export function fireOf(scene: Scene): FireField | undefined {
  return registry.get(scene);
}

export class FireField {
  private readonly flame: ShaderMaterial;
  private readonly flameLow: ShaderMaterial;
  private readonly pool: ShaderMaterial;
  private readonly ember: ShaderMaterial;
  private readonly sets = new Map<string, FireSet>();
  private readonly lights = new Map<string, Flickering[]>();
  private low: boolean;
  private flames = 0;
  /** The fire clock (s) of the last update (flicker.ts `fireTime`). */
  time = 0;
  /** Whether a moving node's flames are drawn (castle/candles.ts at Low draws one candle group a cell); null: always. */
  riderShown: ((node: AbstractMesh) => boolean) | null = null;

  constructor(
    private readonly scene: Scene,
    quality: Quality,
    private readonly look: FireLook = FIRE_LOOK,
  ) {
    this.low = quality === "low";
    this.flame = this.flameMaterial("fire-flames", []);
    this.flameLow = this.flameMaterial("fire-flames-low", ["FIRE_LOW"]);
    this.pool = new ShaderMaterial("fire-pools", scene, { vertexSource: POOL_VERTEX, fragmentSource: POOL_FRAGMENT }, {
      attributes: ["position", "flame"],
      uniforms: ["world", "viewProjection", "time", "flickerAmount", "poolColor", "poolStrength"],
      needAlphaBlending: true,
    });
    // Light added to the floor (the destination's alpha kept: the SSAO class stays the floor's).
    this.pool.alphaMode = Constants.ALPHA_ADD;
    this.pool.disableDepthWrite = true;
    this.pool.backFaceCulling = false;
    this.pool.zOffset = -2;
    this.pool.setColor3("poolColor", Color3.FromArray(look.pool));
    this.pool.setFloat("poolStrength", look.poolStrength);
    this.pool.setFloat("flickerAmount", look.flicker);
    this.ember = new ShaderMaterial("fire-embers", scene, { vertexSource: EMBER_VERTEX, fragmentSource: EMBER_FRAGMENT }, {
      attributes: ["position", "flame"],
      uniforms: ["world", "viewProjection", "view", "time", "emberColor", "fireCore"],
      needAlphaBlending: true,
    });
    this.ember.alphaMode = Constants.ALPHA_ADD;
    this.ember.disableDepthWrite = true;
    this.ember.backFaceCulling = false;
    this.ember.setColor3("emberColor", Color3.FromArray(look.ember));
    this.ember.setColor3("fireCore", Color3.FromArray(look.core));
    registry.set(scene, this);
  }

  private flameMaterial(name: string, defines: string[]): ShaderMaterial {
    const m = new ShaderMaterial(name, this.scene, { vertexSource: FLAME_VERTEX, fragmentSource: FLAME_FRAGMENT }, {
      attributes: ["position", "flame"],
      uniforms: ["world", "viewProjection", "view", "time", "flickerAmount", "fireCore", "fireMid", "fireEdge", "fireTip"],
      defines,
      needAlphaBlending: true,
    });
    m.alphaMode = Constants.ALPHA_COMBINE;
    m.disableDepthWrite = true;
    m.backFaceCulling = false;
    m.setColor3("fireCore", Color3.FromArray(this.look.core));
    m.setColor3("fireMid", Color3.FromArray(this.look.mid));
    m.setColor3("fireEdge", Color3.FromArray(this.look.edge));
    m.setColor3("fireTip", Color3.FromArray(this.look.tip));
    m.setFloat("flickerAmount", this.look.flicker);
    return m;
  }

  /** Low: the flames' simpler shader, no embers. */
  setQuality(q: Quality): void {
    const low = q === "low";
    if (low === this.low) return;
    this.low = low;
    for (const s of this.sets.values()) {
      for (const m of s.meshes) {
        if (m.material === this.flame || m.material === this.flameLow) m.material = low ? this.flameLow : this.flame;
        else if (m.material === this.ember) m.isVisible = !low;
      }
    }
  }

  /** Flames drawn (instances) in the sets now held. */
  get flameCount(): number {
    return this.flames;
  }

  get setCount(): number {
    return this.sets.size;
  }

  /**
   * A world cell's fire (its manifest entry's `fire`): meshes under `root`
   * (the cell's root node; null: the scene), candle groups under their
   * nodes among `meshes`. Replaces any set of the same key.
   */
  wireCell(key: string, raw: unknown, root: Node | null, meshes: readonly AbstractMesh[]): number {
    return this.add(key, parseFire(raw), root, meshes);
  }

  /** Adds a set of fires (see `wireCell`); returns how many flames it draws. */
  add(key: string, data: FireData | null, root: Node | null, meshes: readonly AbstractMesh[] = []): number {
    this.remove(key);
    if (!data) return 0;
    const set: FireSet = { meshes: [], riders: [] };
    let n = 0;
    const tongues = tongueBuffers(data.fires, data.candles);
    if (tongues.count) {
      set.meshes.push(this.instanced(`${key}.fire-flames`, tongues, this.low ? this.flameLow : this.flame, root, ALPHA_INDEX.flame, "foot"));
      n += tongues.count;
    }
    const pools = poolBuffers(data.fires);
    if (pools.count) set.meshes.push(this.instanced(`${key}.fire-pools`, pools, this.pool, root, ALPHA_INDEX.pool, "centre"));
    const embers = emberBuffers(data.fires);
    if (embers.count) {
      const m = this.instanced(`${key}.fire-embers`, embers, this.ember, root, ALPHA_INDEX.ember, "centre");
      m.isVisible = !this.low;
      set.meshes.push(m);
    }
    for (const [name, rows] of data.groups) {
      const node = meshes.find((m) => m.name === name) ?? null;
      if (!node) {
        console.warn(`fire: no node ${name} for its candles`);
        continue;
      }
      // The group's flames in the node's frame at rest (the node's world matrix now), then they ride it.
      node.computeWorldMatrix(true);
      const inv = Matrix.Invert(node.getWorldMatrix());
      const local = rows.map(([x, y, z, h]) => {
        const p = Vector3.TransformCoordinates(new Vector3(x, y, z), inv);
        return [p.x, p.y, p.z, h] as [number, number, number, number];
      });
      const b = tongueBuffers([], local);
      const m = this.instanced(`${key}.fire-${name}`, b, this.low ? this.flameLow : this.flame, node, ALPHA_INDEX.flame, "foot", false);
      set.meshes.push(m);
      set.riders.push({ mesh: m, node });
      n += b.count;
    }
    this.sets.set(key, set);
    this.flames += n;
    return n;
  }

  private instanced(name: string, b: InstanceBuffers, material: ShaderMaterial, parent: Node | null, alphaIndex: number, card: "foot" | "centre", freeze = true): Mesh {
    const mesh = new Mesh(name, this.scene);
    const quad = new VertexData();
    // Flames stand on their foot (y 0..1); floor glows and embers are centred (-0.5..0.5).
    quad.positions = card === "foot" ? [-0.5, 0, 0, 0.5, 0, 0, 0.5, 1, 0, -0.5, 1, 0] : [-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0];
    quad.indices = [0, 1, 2, 0, 2, 3];
    quad.applyToMesh(mesh, false);
    mesh.thinInstanceSetBuffer("matrix", b.matrix, 16, true);
    mesh.thinInstanceSetBuffer("flame", b.data, 4, true);
    mesh.isPickable = false;
    // The shaders place the cards (billboards, rising sparks): the instances' boxes do not bound them.
    mesh.alwaysSelectAsActiveMesh = true;
    mesh.doNotSyncBoundingInfo = true;
    mesh.material = material;
    mesh.alphaIndex = alphaIndex;
    if (parent) mesh.parent = parent;
    if (freeze) mesh.freezeWorldMatrix();
    return mesh;
  }

  /** Disposes a set (a cell unloading, a room left). */
  remove(key: string): void {
    const s = this.sets.get(key);
    if (!s) return;
    for (const m of s.meshes) {
      this.flames -= m.material === this.flame || m.material === this.flameLow ? m.thinInstanceCount : 0;
      m.dispose(false, false);
    }
    this.sets.delete(key);
  }

  /**
   * Lights that flicker with their fires (`seed` = the manifest light's `flicker`), or with `ripple` swell
   * slowly like light through water (`seed` = its `ripple`), keyed like the sets.
   */
  setLights(key: string, list: { light: { intensity: number }; seed: number; ripple?: boolean }[]): void {
    if (list.length === 0) {
      this.lights.delete(key);
      return;
    }
    this.lights.set(key, list.map((l) => ({ light: l.light, base: l.light.intensity, seed: l.seed, ripple: l.ripple === true })));
  }

  clearLights(key: string): void {
    const list = this.lights.get(key);
    if (list) for (const l of list) l.light.intensity = l.base;
    this.lights.delete(key);
  }

  /** The light flicker at the current fire clock for `seed` (torches.ts uses it for the torch pool). */
  lightLevel(seed: number): number {
    return flicker(this.time, seed, this.look.lightFlicker);
  }

  /** Once a frame: the clock into the shaders, the flickering lights, the riders' visibility. No allocation. */
  update(nowMs: number): void {
    const t = fireTime(nowMs);
    this.time = t;
    GLOW_CLOCK.t = t; // the toon glow's flicker (material.ts)
    this.flame.setFloat("time", t);
    this.flameLow.setFloat("time", t);
    this.pool.setFloat("time", t);
    this.ember.setFloat("time", t);
    for (const list of this.lights.values()) {
      for (let i = 0; i < list.length; i++) {
        const l = list[i]!;
        l.light.intensity = l.base * (l.ripple ? ripple(t, l.seed) : flicker(t, l.seed, this.look.lightFlicker));
      }
    }
    for (const s of this.sets.values()) {
      for (let i = 0; i < s.riders.length; i++) {
        const r = s.riders[i]!;
        const shown = r.node.isEnabled() && r.node.isVisible && (this.riderShown === null || this.riderShown(r.node));
        if (r.mesh.isVisible !== shown) r.mesh.isVisible = shown;
      }
    }
  }

  dispose(): void {
    for (const k of [...this.sets.keys()]) this.remove(k);
    for (const k of [...this.lights.keys()]) this.clearLights(k);
    this.flame.dispose();
    this.flameLow.dispose();
    this.pool.dispose();
    this.ember.dispose();
    registry.delete(this.scene);
  }
}
