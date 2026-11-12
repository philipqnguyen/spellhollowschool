// The selection in the world. A left click that does not
// drag the camera selects the character under the pointer (players and
// NPCs; a click on nobody clears it) and draws a thin ring of palette gilt
// under their feet; a double-click on a player, or E while a player is
// selected, opens their card; Tab steps through the characters nearby,
// nearest first. The rules are in ui/selection-state.ts; game.ts routes the
// keys (Tab, E, Esc) here and calls `update` once a frame.
//
// One small unlit torus, drawn only while something is selected (one draw).

import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { CreateTorus } from "@babylonjs/core/Meshes/Builders/torusBuilder";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import type { Scene } from "@babylonjs/core/scene";
import { audio } from "../audio";
import { pickCharacter, type PickTarget } from "../economy/pick";
import { eAction, isClick, isDoubleClick, nextTarget, Selection, tabTargets, type PointerAt, type SelectTarget } from "../ui/selection-state";
import { paletteRgb } from "./castle/terrain";

/** The ring's colour: the palette's gilt (castle_textures.json). */
const RING_RGB = paletteRgb(["gilt", "frame_gilt", "brass"]) ?? ([0.93, 0.73, 0.3] as const);
/** The ring's size (m) and how far above the feet it floats (no z-fighting with the floor). */
const RING_DIAMETER_M = 0.95;
const RING_THICKNESS_M = 0.045;
const RING_LIFT_M = 0.04;

/** A character in view, with its head height for picking. */
export type SelectCandidate = SelectTarget & Pick<PickTarget, "height">;

export interface SelectionHooks {
  scene: Scene;
  canvas: HTMLCanvasElement;
  /** Everyone in view this frame but ourselves (players and NPCs). */
  inView: () => readonly SelectCandidate[];
  /** Our feet. */
  feet: () => [number, number, number];
  /** The camera's view-projection (`scene.getTransformMatrix().m`). */
  viewProjection: () => ArrayLike<number>;
  /** Opens a player's card. */
  openCard: (name: string) => void;
  /** Whether a click on the world is the selection's (not while decorating or aiming a spell). */
  enabled: () => boolean;
  /** NPCs: a double-click on a resident talks to it (when in reach). */
  onNpcDouble?: (id: number) => void;
}

export class WorldSelection {
  readonly state = new Selection();
  private ring: Mesh | null = null;
  private press: PointerAt | null = null;
  private lastClick: (PointerAt & { id: number }) | null = null;
  private readonly onDown = (e: PointerEvent) => {
    if (e.button === 0) this.press = { x: e.clientX, y: e.clientY, t: performance.now() };
  };
  private readonly onUp = (e: PointerEvent) => {
    const p = this.press;
    this.press = null;
    if (e.button !== 0 || !p || !this.hooks.enabled()) return;
    const now = { x: e.clientX, y: e.clientY, t: performance.now() };
    if (isClick(p, now)) this.clickAt(now);
  };

  constructor(private readonly hooks: SelectionHooks) {
    hooks.canvas.addEventListener("pointerdown", this.onDown);
    hooks.canvas.addEventListener("pointerup", this.onUp);
  }

  /** Who is selected (null: nobody). */
  get selected(): SelectTarget | null {
    return this.state.selected;
  }

  private clickAt(at: PointerAt): void {
    const rect = this.hooks.canvas.getBoundingClientRect();
    const hit = pickCharacter(this.hooks.inView(), this.hooks.viewProjection(), rect.width, rect.height, at.x - rect.left, at.y - rect.top) as SelectCandidate | null;
    if (!hit) {
      this.state.clear();
      this.lastClick = null;
      return;
    }
    const double = isDoubleClick(this.lastClick, at) && this.lastClick?.id === hit.id;
    if (this.state.id !== hit.id) audio.play("ui.select");
    this.state.select(hit);
    this.lastClick = double ? null : { ...at, id: hit.id };
    if (double && !hit.npc) this.hooks.openCard(hit.name);
    else if (double) this.hooks.onNpcDouble?.(hit.id);
  }

  /** Tab (Shift+Tab back): the next character nearby. True when someone is near. */
  cycle(dir: 1 | -1): boolean {
    const next = nextTarget(tabTargets(this.hooks.inView(), this.hooks.feet()), this.state.id, dir);
    if (!next) return false;
    this.state.select(next);
    audio.play("ui.select");
    return true;
  }

  /** E: a selected player's card (true), or false for E's usual use (an NPC, nobody selected). */
  use(): boolean {
    const s = this.state.selected;
    if (eAction(s) !== "card" || !s) return false;
    this.hooks.openCard(s.name);
    return true;
  }

  /** Esc: true when something was selected (and is no longer). */
  clear(): boolean {
    const had = this.state.clear();
    if (had) this.ring?.setEnabled(false);
    return had;
  }

  /** Once a frame after the characters are posed: follow the selected one with the ring. */
  update(): void {
    // Nothing selected: nothing to look up (the list is built only while someone is).
    if (!this.state.selected) {
      if (this.ring?.isEnabled()) this.ring.setEnabled(false);
      return;
    }
    const s = this.state.follow(this.hooks.inView(), this.hooks.feet());
    if (!s) {
      this.ring?.setEnabled(false);
      return;
    }
    const ring = (this.ring ??= this.makeRing());
    ring.position.set(s.x, s.y + RING_LIFT_M, s.z);
    ring.setEnabled(true);
  }

  private makeRing(): Mesh {
    const scene = this.hooks.scene;
    const ring = CreateTorus("selection-ring", { diameter: RING_DIAMETER_M, thickness: RING_THICKNESS_M, tessellation: 40 }, scene);
    const mat = new StandardMaterial("selection-ring", scene);
    mat.disableLighting = true;
    mat.emissiveColor = new Color3(RING_RGB[0], RING_RGB[1], RING_RGB[2]);
    mat.diffuseColor = Color3.Black();
    mat.specularColor = Color3.Black();
    ring.material = mat;
    ring.isPickable = false;
    ring.receiveShadows = false;
    ring.setEnabled(false);
    return ring;
  }

  dispose(): void {
    this.hooks.canvas.removeEventListener("pointerdown", this.onDown);
    this.hooks.canvas.removeEventListener("pointerup", this.onUp);
    this.ring?.material?.dispose();
    this.ring?.dispose();
    this.ring = null;
  }
}
