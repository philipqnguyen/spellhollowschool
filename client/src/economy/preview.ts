// The 3D preview in the Owl Exchange's item detail. Clothing is shown on
// your own character in the wardrobe's mirror engine (`CharacterPreview`),
// wearing the listed piece in its dyes and finish over what you have on.
// Furniture is drawn with the dorm's own furniture renderer (the same
// asset, dye baking and greybox stand-in as in a room) on a small turntable
// in its own engine. Everything else (materials, dyes, potions, patterns)
// gets its icon, large.
//
// Only one engine runs at a time: switching between clothing and furniture
// disposes the other and starts on a fresh canvas. The economy windows live
// in the game chunk, which has Babylon and the dorm renderer already; the
// character preview is imported on first use, as the wardrobe does.

import { Engine } from "@babylonjs/core/Engines/engine";
import { Scene } from "@babylonjs/core/scene";
import { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { cloneAppearance } from "../content/appearance";
import { bodyCanWear, wearLabel, type Dyes } from "../content/catalog";
import type { CharacterPreview } from "../game/characters/preview";
import { FurnitureRenderer } from "../game/dorm/furniture";
import { DormRoom } from "../game/dorm/room";
import type { Appearance, Finish } from "../wasm/sim_wasm";
import { h } from "../ui/dom";
import { itemIcon } from "./icons";
import { econDef } from "./items";

/** Furniture asset keys resolve under this logical folder (src/assets.ts). */
const ASSETS_BASE = "assets/";

export interface PreviewItem {
  itemDef: number;
  dyes: Dyes;
  finish: Finish;
}

/** `base` wearing `item` in its slot (dyes and finish as listed). */
export function wearing(base: Appearance, item: PreviewItem): Appearance {
  const def = econDef(item.itemDef);
  const a = cloneAppearance(base);
  if (def?.kind === "clothing" && def.slot) {
    a.outfit[def.slot] = { itemDef: item.itemDef, dyes: [item.dyes[0], item.dyes[1], item.dyes[2]], ...(item.finish !== "none" ? { finish: item.finish } : {}) };
  }
  return a;
}

export class ItemPreview {
  readonly root: HTMLElement;
  private readonly note: HTMLElement;
  private character: { canvas: HTMLCanvasElement; preview: CharacterPreview } | null = null;
  private furniture: FurniturePreview | null = null;
  /** Bumped on every `show`, so a slow import for an older item does nothing. */
  private generation = 0;
  private disposed = false;

  constructor(private readonly baseAppearance: () => Promise<Appearance | null>) {
    this.note = h("p", { class: "preview-note", "aria-live": "polite" });
    this.root = h("div", { class: "item-preview" }, this.note);
  }

  async show(item: PreviewItem): Promise<void> {
    const gen = ++this.generation;
    const def = econDef(item.itemDef);
    this.note.textContent = "";
    try {
      if (def?.kind === "clothing") {
        this.stopFurniture();
        const base = await this.baseAppearance();
        if (gen !== this.generation || this.disposed) return;
        if (!base) return this.iconOnly(item);
        // What this body may not wear is not tried on (anyone may still buy it).
        if (!bodyCanWear(base.bodyType, def)) {
          this.iconOnly(item);
          this.note.textContent = `${wearLabel(item.itemDef) ?? ""}: it cannot be tried on you.`;
          return;
        }
        const look = wearing(base, item);
        if (this.character) {
          this.character.preview.setAppearance(look);
        } else {
          const { CharacterPreview } = await import("../game/characters/preview");
          if (gen !== this.generation || this.disposed) return;
          const canvas = h("canvas", { class: "preview-canvas", "aria-label": `${def.name} on you: drag to turn` });
          this.root.replaceChildren(canvas, this.note);
          this.character = { canvas, preview: new CharacterPreview(canvas, look) };
        }
        this.note.textContent = "On you: drag to turn";
      } else if (def?.kind === "furniture") {
        this.stopCharacter();
        if (this.furniture) {
          this.furniture.setItem(item);
        } else {
          const canvas = h("canvas", { class: "preview-canvas", "aria-label": `${def.name}: drag to turn` });
          const preview = await FurniturePreview.create(canvas, item);
          if (gen !== this.generation || this.disposed) {
            preview.dispose();
            return;
          }
          this.root.replaceChildren(canvas, this.note);
          this.furniture = preview;
        }
        this.note.textContent = "Drag to turn";
      } else {
        this.iconOnly(item);
      }
    } catch (e) {
      console.error(e);
      if (gen === this.generation) {
        this.iconOnly(item);
        this.note.textContent = "No 3D view here.";
      }
    }
  }

  private iconOnly(item: PreviewItem): void {
    this.stopCharacter();
    this.stopFurniture();
    this.root.replaceChildren(h("div", { class: "preview-icon" }, itemIcon(item.itemDef, item.dyes, "large", item.finish)), this.note);
  }

  private stopCharacter(): void {
    this.character?.preview.dispose();
    this.character = null;
  }

  private stopFurniture(): void {
    this.furniture?.dispose();
    this.furniture = null;
  }

  /** Stops the engine (the detail closed). */
  clear(): void {
    this.generation++;
    this.stopCharacter();
    this.stopFurniture();
    this.root.replaceChildren(this.note);
    this.note.textContent = "";
  }

  dispose(): void {
    this.disposed = true;
    this.clear();
  }
}

/** One furniture item on a turntable, drawn by the dorm's furniture renderer. */
class FurniturePreview {
  private constructor(
    private readonly engine: Engine,
    private readonly scene: Scene,
    private readonly camera: ArcRotateCamera,
    private readonly room: DormRoom,
    private readonly renderer: FurnitureRenderer,
    private readonly resize: ResizeObserver,
  ) {}

  static async create(canvas: HTMLCanvasElement, item: PreviewItem): Promise<FurniturePreview> {
    const engine = new Engine(canvas, true, { alpha: true, premultipliedAlpha: false, stencil: false, preserveDrawingBuffer: false, audioEngine: false }, false);
    engine.disableManifestCheck = true;
    engine.setHardwareScalingLevel(1 / Math.min(window.devicePixelRatio || 1, 2));
    const scene = new Scene(engine);
    scene.useRightHandedSystem = true;
    scene.clearColor = new Color4(0, 0, 0, 0);
    scene.skipPointerMovePicking = true;
    const hemi = new HemisphericLight("preview-sky", new Vector3(0, 1, 0), scene);
    hemi.intensity = 0.65;
    hemi.groundColor = new Color3(0.3, 0.24, 0.18);
    hemi.specular = Color3.Black();
    const key = new DirectionalLight("preview-key", new Vector3(-0.5, -0.8, -0.9), scene);
    key.intensity = 0.8;
    key.diffuse = new Color3(1, 0.9, 0.75);
    const cam = new ArcRotateCamera("preview-camera", Math.PI / 2 - 0.5, 1.15, 3, new Vector3(0, 0.5, 0), scene);
    cam.minZ = 0.05;
    cam.fov = 0.6;
    cam.lowerBetaLimit = 0.3;
    cam.upperBetaLimit = 1.55;
    cam.panningSensibility = 0;
    cam.wheelPrecision = 60;
    cam.inputs.removeByType("ArcRotateCameraKeyboardMoveInput");
    cam.attachControl(true);
    const room = new DormRoom();
    const renderer = new FurnitureRenderer(scene, ASSETS_BASE, room);
    const resize = new ResizeObserver(() => engine.resize());
    resize.observe(canvas);
    const preview = new FurniturePreview(engine, scene, cam, room, renderer, resize);
    preview.setItem(item);
    engine.runRenderLoop(() => {
      renderer.sync();
      scene.render();
    });
    return preview;
  }

  /** Shows `item` (one copy at the origin) and frames it. */
  setItem(item: PreviewItem): void {
    this.room.reset({ openToFriends: false, cap: 1, items: [{ itemId: 1, itemDef: item.itemDef, dyes: [item.dyes[0], item.dyes[1], item.dyes[2]], pos: [0, 0, 0], yaw: 0 }] });
    const f = econDef(item.itemDef)?.furniture;
    const w = f?.footprint[0] ?? 1;
    const d = f?.footprint[1] ?? 1;
    const height = f?.height ?? 1;
    const size = Math.max(w, d, height, 0.3);
    this.camera.target.set(0, height / 2, f?.surface === "wall" ? d / 2 : 0);
    this.camera.radius = size * 1.9 + 0.3;
    this.camera.lowerRadiusLimit = size * 0.9;
    this.camera.upperRadiusLimit = size * 4 + 1;
  }

  dispose(): void {
    this.resize.disconnect();
    this.engine.stopRenderLoop();
    this.renderer.dispose();
    this.camera.detachControl();
    this.scene.dispose();
    this.engine.dispose();
  }
}
