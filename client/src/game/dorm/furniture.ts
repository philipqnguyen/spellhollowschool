// Draws a dorm room's furniture, and the ghost preview while decorating.
//
// Every catalog item is one asset (`client/public/assets/<asset>.glb`, the
// asset key contract of docs/architecture.md), loaded once per room. Placed
// copies are grouped by their dyes: each (item, dyes) group is one *variant*,
// a copy of the asset's meshes with the dyes baked into its vertex colours,
// drawn with thin instances (one per placed copy). A room therefore costs one
// draw call per mesh of each distinct dyed item however many copies it holds;
// the exported furniture is one mesh each, so a starter set is ~12 draw calls.
//
// Dyes follow the art pipeline (tools/blender/README.md): `COLOR_0` is a tint mask (R = primary, G = secondary, B = trim,
// black = undyed) and
//
//   albedo = baseColor · ((1 − r − g − b) + r·P + g·S + b·T)
//
// with the item's palette colours in sRGB (the art's PBR materials become
// toon StandardMaterials on load, toon/convert.ts, which multiply vertex
// colours in sRGB; a PBR material would want them linear). The bracket
// replaces the mask as the variant's vertex colour. A mesh without `COLOR_0` keeps its authored look.
// An asset that is missing or does not load is drawn as a greybox block of
// its catalog footprint in its primary dye, so rooms work before the art exists.
//
// While decorating, the ghost is a translucent copy turned exactly as the
// piece will stand (Q/E turn it), inside a wire box turned with it; a placed
// piece under the cursor is drawn highlighted (a gilt overlay and outline,
// palette colours) in place of its instance, so it reads as clickable.

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import type { AssetContainer } from "@babylonjs/core/assetContainer";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Matrix } from "@babylonjs/core/Maths/math.vector";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { PBRMaterial } from "@babylonjs/core/Materials/PBR/pbrMaterial";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import "@babylonjs/core/Rendering/outlineRenderer";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { Scene } from "@babylonjs/core/scene";
import { PALETTE, paletteHex } from "../../content/catalog";
import type { Vec3 } from "../../wasm/sim_wasm";
import { tintVertex, type Channels, type Rgb } from "../characters/dye";
import { furnitureDef, type FurnitureDef } from "./catalog";
import { orientedBox, unitsToYaw } from "./placement";
import { toonifyContainer } from "../toon/convert";
import { ToonPlugin } from "../toon/material";
import { worldRamp } from "../toon/palette";
import { parseFire, placeFire, type FireData } from "../fire/data";
import { fireOf } from "../fire/field";

/** Lights a room's furniture takes: the room's six lamps and its fill (zones-scene.ts). */
const ROOM_LIGHTS = 7;

/** A palette colour by id (data/items/palette.json), for the decorating highlights. */
function paletteColor(id: string): Color3 {
  const c = PALETTE.colours.find((p) => p.id === id);
  return Color3.FromHexString(c?.hex ?? paletteHex(-1));
}

/** The hover highlight's colour, and the ghost's where it fits and where it does not. */
const HOVER_COLOUR = "canary";
const GHOST_OK_COLOUR = "gold";
const GHOST_BAD_COLOUR = "crimson";
import type { DormRoom, RoomItem } from "./room";
import { assetUrl } from "../../assets";

type Dyes = readonly [number, number, number] | readonly number[];

/** One mesh of an asset as loaded, never drawn itself (variants copy it). */
export interface Source {
  mesh: Mesh;
  /** The per-vertex tint mask (RGBA floats), or null when the mesh is not dyeable. */
  mask: Float32Array | null;
  /** The material wants linear colours (PBR, from glTF) rather than sRGB. */
  linear: boolean;
}

/** The asset's meshes tinted with one set of dyes, drawn for every copy with those dyes. */
interface Variant {
  meshes: Mesh[];
  count: number;
}

/** One catalog item's asset, shared by all its placed copies. */
export interface Model {
  def: FurnitureDef;
  state: "loading" | "ready";
  sources: Source[];
  container: AssetContainer | null;
  /** Variants by dye key (`p,s,t`; one shared variant when nothing is dyeable). */
  variants: Map<string, Variant>;
  /** Animated fire: the model's flames in its own frame (the glb's node extras `fire`), if it burns. */
  fire?: FireData | null;
}

/** Counts renderers, for their fire set keys. */
let renderers = 0;

export class FurnitureRenderer {
  private readonly models = new Map<number, Model>();
  /** Items not drawn (the one being moved). */
  private readonly hidden = new Set<number>();
  /** Items drawn raised by this much (m), floating under Elevara. */
  private readonly lifts = new Map<number, number>();
  private drawnVersion = -1;
  private ghost: Ghost | null = null;
  /** The placed piece drawn highlighted (hovered while decorating), and its copy. */
  private highlighted: number | null = null;
  private highlight: Mesh[] = [];
  private disposed = false;
  /** This renderer's fire set keys are `${fireKey}-<item def>`. */
  private readonly fireKey = `furniture-${++renderers}`;

  constructor(
    private readonly scene: Scene,
    /** `assets/` (a logical path, src/assets.ts) */
    private readonly assetsBase: string,
    private readonly room: DormRoom,
  ) {}

  /** Redraws when the room changed since the last call (cheap to call every frame). */
  sync(force = false): void {
    if (!force && this.room.version === this.drawnVersion) return;
    this.drawnVersion = this.room.version;
    const byDef = new Map<number, RoomItem[]>();
    for (const it of this.room.items.values()) {
      if (this.hidden.has(it.itemId) || it.itemId === this.highlighted) continue;
      let list = byDef.get(it.itemDef);
      if (!list) byDef.set(it.itemDef, (list = []));
      list.push(it);
    }
    for (const [defId, items] of byDef) {
      const model = this.model(defId);
      if (model?.state === "ready") this.fill(model, items);
    }
    for (const [defId, model] of this.models) if (!byDef.has(defId)) this.fill(model, []);
    this.drawHighlight();
  }

  /**
   * Draws placed piece `itemId` highlighted (null: none): a copy with a gilt
   * overlay and outline replaces its instance until the highlight moves on.
   */
  setHighlight(itemId: number | null): void {
    if (itemId === this.highlighted) return;
    this.highlighted = itemId;
    this.sync(true);
  }

  private drawHighlight(): void {
    for (const m of this.highlight) m.dispose();
    this.highlight = [];
    const it = this.highlighted === null ? undefined : this.room.items.get(this.highlighted);
    const model = it ? this.models.get(it.itemDef) : undefined;
    if (!it || model?.state !== "ready" || this.hidden.has(it.itemId)) return;
    const colour = paletteColor(HOVER_COLOUR);
    this.highlight = model.sources.map((s) => {
      const copy = tintedCopy(s, it.dyes, "hover");
      copy.rotationQuaternion = null;
      copy.position.set(it.pos[0], it.pos[1] + (this.lifts.get(it.itemId) ?? 0), it.pos[2]);
      copy.rotation.set(0, unitsToYaw(it.yawUnits), 0);
      copy.renderOverlay = true;
      copy.overlayColor = colour;
      copy.overlayAlpha = 0.3;
      copy.renderOutline = true;
      copy.outlineColor = colour;
      copy.outlineWidth = 0.015;
      copy.setEnabled(true);
      return copy;
    });
  }

  /** Hides or shows one placed item (decorating mode lifts it while it is moved). */
  setHidden(itemId: number, hidden: boolean): void {
    if (hidden === this.hidden.has(itemId)) return;
    if (hidden) this.hidden.add(itemId);
    else this.hidden.delete(itemId);
    this.sync(true);
  }

  /**
   * Draws one placed item `dy` metres above its place (0 puts it back):
   * a piece floating under a levitation charm. Only that item's model is
   * redrawn; its placement never changes.
   */
  setLift(itemId: number, dy: number): void {
    const was = this.lifts.get(itemId) ?? 0;
    if (dy === was) return;
    if (dy) this.lifts.set(itemId, dy);
    else this.lifts.delete(itemId);
    const item = this.room.items.get(itemId);
    const model = item ? this.models.get(item.itemDef) : undefined;
    if (!item || model?.state !== "ready" || this.hidden.has(itemId)) return;
    this.fill(model, [...this.room.items.values()].filter((it) => it.itemDef === item.itemDef && !this.hidden.has(it.itemId)));
  }

  /** Starts loading an item's model ahead of use (the decorating strip's selection). */
  preload(itemDef: number): void {
    this.model(itemDef);
  }

  private model(defId: number): Model | null {
    const existing = this.models.get(defId);
    if (existing) return existing;
    const def = furnitureDef(defId);
    if (!def) return null;
    const model: Model = { def, state: "loading", sources: [], container: null, variants: new Map() };
    this.models.set(defId, model);
    void this.load(model);
    return model;
  }

  private async load(model: Model): Promise<void> {
    try {
      const container = await LoadAssetContainerAsync(assetUrl(`${this.assetsBase}${model.def.asset}.glb`), this.scene, { pluginExtension: ".glb" });
      if (this.disposed) {
        container.dispose();
        return;
      }
      // Toon materials (a room's lamps plus its fill), so dyes are sRGB from here on.
      toonifyContainer(container, this.scene, ROOM_LIGHTS, { vertexColours: "mask" });
      container.addAllToScene();
      model.container = container;
      // Animated fire (docs/architecture.md): the flames the art lists on the model's node.
      for (const n of [...container.meshes, ...container.transformNodes]) {
        const raw = (n.metadata as { gltf?: { extras?: { fire?: unknown } } } | null)?.gltf?.extras?.fire;
        if (typeof raw !== "string") continue;
        try {
          model.fire = parseFire(JSON.parse(raw));
        } catch {
          model.fire = null;
        }
      }
      for (const m of container.meshes) {
        if (!(m instanceof Mesh) || m.getTotalVertices() === 0) continue;
        // Bake each mesh's place in the asset into its vertices, so a copy's
        // transform is just the thin instance's matrix.
        m.computeWorldMatrix(true);
        m.setParent(null);
        m.makeGeometryUnique();
        m.bakeCurrentTransformIntoVertices();
        const colours = m.getVerticesData(VertexBuffer.ColorKind);
        const mask = colours ? Float32Array.from(colours) : null;
        model.sources.push({ mesh: m, mask, linear: m.material instanceof PBRMaterial });
      }
      // The glTF root node(s) stay as empty parents; nothing to draw there.
    } catch {
      if (this.disposed) return;
      // No art yet (or it failed to load): a block of the catalog shape.
      model.sources = [this.greyboxSource(model.def)];
    }
    for (const s of model.sources) hideSource(s.mesh);
    model.state = "ready";
    this.sync(true);
    this.ghost?.modelReady(model);
  }

  private greyboxSource(def: FurnitureDef): Source {
    const [w, d] = def.footprint;
    const h = Math.max(def.height, 0.02);
    const box = CreateBox(`furniture-${def.id}`, { width: w, height: h, depth: Math.max(d, 0.02) }, this.scene);
    // Floor and table items stand on their origin; wall items hang from the middle of their back.
    box.bakeTransformIntoVertices(Matrix.Translation(0, h / 2, def.surface === "wall" ? d / 2 : 0));
    const mat = new StandardMaterial(`furniture-${def.id}`, this.scene);
    mat.diffuseColor = Color3.White();
    mat.specularColor = Color3.Black();
    mat.maxSimultaneousLights = ROOM_LIGHTS;
    new ToonPlugin(mat, worldRamp("furniture"));
    box.material = mat;
    // The whole block takes the primary dye.
    const mask = new Float32Array(box.getTotalVertices() * 4);
    for (let i = 0; i < mask.length; i += 4) mask.set([1, 0, 0, 1], i);
    return { mesh: box, mask, linear: false };
  }

  /** The variant drawing `model` with `dyes`, created on first use. */
  private variant(model: Model, dyes: Dyes): Variant {
    const dyeable = model.sources.some((s) => s.mask);
    const key = dyeable ? `${dyes[0]},${dyes[1]},${dyes[2]}` : "";
    let v = model.variants.get(key);
    if (!v) {
      v = { meshes: model.sources.map((s) => tintedCopy(s, dyes, `${model.def.asset}:${key}`)), count: 0 };
      model.variants.set(key, v);
    }
    return v;
  }

  private fill(model: Model, items: readonly RoomItem[]): void {
    const groups = new Map<Variant, RoomItem[]>();
    for (const it of items) {
      const v = this.variant(model, it.dyes);
      let list = groups.get(v);
      if (!list) groups.set(v, (list = []));
      list.push(it);
    }
    for (const v of model.variants.values()) {
      const list = groups.get(v) ?? [];
      if (list.length === 0 && v.count === 0) continue;
      const matrices = new Float32Array(list.length * 16);
      list.forEach((it, i) => writeMatrix(matrices, i, it.pos, unitsToYaw(it.yawUnits), this.lifts.get(it.itemId) ?? 0));
      for (const mesh of v.meshes) {
        mesh.thinInstanceSetBuffer("matrix", list.length ? matrices : null, 16, true);
        mesh.thinInstanceCount = list.length;
        mesh.setEnabled(list.length > 0);
      }
      v.count = list.length;
    }
    // Animated fire: the flames of every placed copy (rebuilt with the copies, never per frame).
    if (model.fire) {
      const places = items.map((it) => ({ pos: it.pos, yaw: unitsToYaw(it.yawUnits), lift: this.lifts.get(it.itemId) ?? 0 }));
      fireOf(this.scene)?.add(`${this.fireKey}-${model.def.id}`, placeFire(model.fire, places), null);
    }
  }

  // ---------------------------------------------------------------- ghost

  /**
   * Shows the ghost of `itemDef` at `pos` turned by `yawUnits` (null hides
   * it). `valid` colours its outline: palette gold where it can go, crimson
   * where the server would refuse it.
   */
  showGhost(itemDef: number | null, dyes: readonly number[], pos: Vec3 | null, yawUnits: number, valid: boolean): void {
    if (itemDef === null || pos === null) {
      this.ghost?.hide();
      return;
    }
    const model = this.model(itemDef);
    if (!model) return;
    if (this.ghost?.model !== model) {
      this.ghost?.dispose();
      this.ghost = new Ghost(this.scene, model);
    }
    this.ghost.place(pos, yawUnits, valid, dyes);
  }

  dispose(): void {
    this.disposed = true;
    this.ghost?.dispose();
    for (const m of this.highlight) m.dispose();
    this.highlight = [];
    for (const model of this.models.values()) {
      for (const v of model.variants.values()) for (const m of v.meshes) m.dispose();
      if (model.container) {
        model.container.removeAllFromScene();
        model.container.dispose();
      } else {
        for (const s of model.sources) {
          s.mesh.material?.dispose();
          s.mesh.dispose();
        }
      }
    }
    const fire = fireOf(this.scene);
    for (const model of this.models.values()) if (model.fire) fire?.remove(`${this.fireKey}-${model.def.id}`);
    this.models.clear();
  }
}

/** A translucent copy of the item in the chosen dyes and its outline, following the cursor (exported for tests). */
export class Ghost {
  private copies: Mesh[] = [];
  private copiesKey = "";
  readonly outline: Mesh;
  private readonly outlineMat: StandardMaterial;
  private readonly ok = paletteColor(GHOST_OK_COLOUR);
  private readonly bad = paletteColor(GHOST_BAD_COLOUR);

  constructor(
    scene: Scene,
    readonly model: Model,
  ) {
    this.outline = CreateBox("ghost-outline", { size: 1 }, scene);
    this.outlineMat = new StandardMaterial("ghost-outline", scene);
    this.outlineMat.wireframe = true;
    this.outlineMat.disableLighting = true;
    this.outline.material = this.outlineMat;
    this.outline.isPickable = false;
  }

  /** The translucent copies drawn now (one per mesh of the model). */
  get meshes(): readonly Mesh[] {
    return this.copies;
  }

  /** The model finished loading: the next `place` builds the copies. */
  modelReady(model: Model): void {
    if (model === this.model) this.copiesKey = "";
  }

  place(pos: Vec3, yawUnits: number, valid: boolean, dyes: readonly number[]): void {
    const key = `${dyes[0]},${dyes[1]},${dyes[2]}`;
    if (this.model.state === "ready" && key !== this.copiesKey) {
      for (const c of this.copies) c.dispose();
      this.copies = this.model.sources.map((s) => {
        const copy = tintedCopy(s, dyes, "ghost");
        // The loaded meshes keep an identity rotation quaternion, which would
        // override `rotation` below: the ghost must turn as the piece will.
        copy.rotationQuaternion = null;
        copy.setEnabled(true);
        copy.visibility = 0.6;
        return copy;
      });
      this.copiesKey = key;
    }
    const yaw = unitsToYaw(yawUnits);
    for (const c of this.copies) {
      c.setEnabled(true);
      c.position.set(pos[0], pos[1], pos[2]);
      c.rotation.set(0, yaw, 0);
    }
    // The wire box turns with the piece too, so its front shows the way it faces.
    const box = orientedBox(this.model.def, pos, yawUnits);
    this.outline.setEnabled(true);
    this.outline.position.set(box.centre[0], box.centre[1], box.centre[2]);
    this.outline.rotation.set(0, box.yaw, 0);
    this.outline.scaling.set(box.size[0] + 0.02, box.size[1] + 0.02, box.size[2] + 0.02);
    this.outlineMat.emissiveColor = valid ? this.ok : this.bad;
  }

  hide(): void {
    for (const c of this.copies) c.setEnabled(false);
    this.outline.setEnabled(false);
  }

  dispose(): void {
    for (const c of this.copies) c.dispose();
    this.outline.dispose();
    this.outlineMat.dispose();
  }
}

/** A drawable copy of `source` with `dyes` baked into its vertex colours (own geometry, shared material). */
function tintedCopy(source: Source, dyes: Dyes, name: string): Mesh {
  const copy = source.mesh.clone(`${source.mesh.name}-${name}`, null, true, false);
  copy.makeGeometryUnique();
  if (source.mask) {
    const channels = dyeChannels(dyes, source.linear);
    const n = source.mask.length / 4;
    const out = new Float32Array(n * 4);
    const white = [1, 1, 1];
    for (let i = 0; i < n; i++) {
      tintVertex(white, source.mask, i * 4, channels, out, i * 4);
      out[i * 4 + 3] = 1;
    }
    copy.setVerticesData(VertexBuffer.ColorKind, out, false, 4);
    copy.useVertexColors = true;
    copy.hasVertexAlpha = false;
  }
  copy.isPickable = false;
  copy.thinInstanceEnablePicking = false;
  // A room is small: skip per-frame bounds over all copies.
  copy.alwaysSelectAsActiveMesh = true;
  copy.doNotSyncBoundingInfo = true;
  copy.setEnabled(false);
  return copy;
}

function dyeChannels(dyes: Dyes, linear: boolean): Channels {
  const rgb = (i: number): Rgb => {
    let c = Color3.FromHexString(paletteHex(dyes[i] ?? 0));
    if (linear) c = c.toLinearSpace();
    return [c.r, c.g, c.b];
  };
  return [rgb(0), rgb(1), rgb(2)];
}

function hideSource(mesh: Mesh): void {
  mesh.isPickable = false;
  mesh.setEnabled(false);
}

/** Feet at `pos` (raised by `lift` m), turned by `yaw` about Y (+Z to (sin yaw, 0, cos yaw)), in Babylon's row-vector layout. */
function writeMatrix(out: Float32Array, i: number, pos: Vec3, yaw: number, lift = 0): void {
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  out.set([c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, pos[0], pos[1] + lift, pos[2], 1], i * 16);
}
