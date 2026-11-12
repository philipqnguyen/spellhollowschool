// A class room's animated props: Herbology's plants on the bench
// and the paddock's creatures. Each is its own small glb,
// `assets/instances/props/<prop id>.glb` (built by the room's Blender module,
// at most 2 draws and 3,000 triangles), stood at the room layout's landmark
// of the same id when the student enters the class. The class's stage
// (fx/stage.ts) says how each one moves this frame; this applies it to the
// prop's root node (lean, turn, lift, squash: node transforms only, no rigs,
// glTF animations or shape keys), and floats the need a creature shows (the
// beat's prompt icon) over it as a small HUD badge. A missing glb draws
// nothing and logs once (the art may land after the client); a need still
// shows over its landmark.

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import type { AssetContainer } from "@babylonjs/core/assetContainer";
import { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import type { Scene } from "@babylonjs/core/scene";
import { h } from "../../ui/dom";
import { pickIcon } from "../../ui/classes-pick";
import type { Landmark } from "../../wasm/sim_wasm";
import { toonifyContainer } from "../toon/convert";
import type { ClassStage } from "./fx";
import { assetUrl } from "../../assets";

const PROPS_BASE = "assets/instances/props/";
/** The room's lamps, the sky fill and the character shadow light (as the room's own materials: zones-scene.ts). */
const MAX_LIGHTS = 8;
/** A need badge floats this far over the top of its creature (m). */
const NEED_ABOVE_M = 0.35;
/** A prop's height until its glb has loaded (or when it is missing), m. */
const DEFAULT_HEIGHT_M = 0.6;
/** Props whose glb or landmark was missing, already logged once. */
const logged = new Set<string>();

interface LoadedProp {
  root: TransformNode;
  /** Where it stands (the landmark: its base). */
  base: [number, number, number];
  /** Its height as built (m), for the need badge. */
  height: number;
  container: AssetContainer;
}

interface NeedBadge {
  el: HTMLElement;
  key: string;
  shown: boolean;
}

export class ClassProps {
  private readonly props = new Map<string, LoadedProp>();
  /** Every prop's landmark (its base), loaded or not: the need badges stand over it. */
  private readonly spots = new Map<string, [number, number, number]>();
  private readonly badges = new Map<string, NeedBadge>();
  private readonly layer: HTMLElement;
  /** Bumped on every load and clear: a load that finishes late is thrown away. */
  private generation = 0;

  constructor(
    private readonly scene: Scene,
    hudRoot: HTMLElement,
    private readonly base = PROPS_BASE,
  ) {
    this.layer = h("div", { class: "class-needs", "aria-hidden": "true" });
    hudRoot.append(this.layer);
  }

  /** Loads `ids`, each at the landmark of the same id in the room's layout (`landmarks`). */
  load(ids: readonly string[], landmarks: readonly Landmark[]): void {
    this.clear();
    const gen = this.generation;
    for (const id of ids) {
      const lm = landmarks.find((l) => l.id === id);
      if (!lm) {
        logOnce(id, `[class props] the room has no landmark "${id}"; the prop is not drawn`);
        continue;
      }
      const at: [number, number, number] = [lm.pos[0], lm.pos[1], lm.pos[2]];
      this.spots.set(id, at);
      void this.loadOne(id, at, gen);
    }
  }

  private async loadOne(id: string, at: [number, number, number], gen: number): Promise<void> {
    let c: AssetContainer;
    try {
      c = await LoadAssetContainerAsync(assetUrl(`${this.base}${id}.glb`), this.scene, { pluginExtension: ".glb" });
    } catch (e) {
      logOnce(id, `[class props] ${id}.glb is not available; nothing drawn (${e instanceof Error ? e.message.split("\n")[0] : e})`);
      return;
    }
    if (gen !== this.generation) {
      c.dispose();
      return;
    }
    toonifyContainer(c, this.scene, MAX_LIGHTS);
    // In the scene first, then under the prop's root (a container's hierarchy may not reach outside it).
    c.addAllToScene();
    const root = new TransformNode(`class-prop-${id}`, this.scene);
    root.position.set(at[0], at[1], at[2]);
    for (const n of c.rootNodes) n.parent = root;
    root.computeWorldMatrix(true);
    for (const n of c.transformNodes) n.computeWorldMatrix(true);
    let top = 0;
    for (const m of c.meshes) {
      m.isPickable = false;
      const mat = m.material as { maxSimultaneousLights?: number } | null;
      if (mat && typeof mat.maxSimultaneousLights === "number") mat.maxSimultaneousLights = MAX_LIGHTS;
      if (m.getTotalVertices() > 0) {
        m.computeWorldMatrix(true);
        top = Math.max(top, m.getBoundingInfo().boundingBox.maximumWorld.y - at[1]);
      }
    }
    this.props.set(id, { root, base: at, height: top > 0 ? top : DEFAULT_HEIGHT_M, container: c });
  }

  /** Before each frame renders: every prop takes its pose from the stage (server time `now`, ms). */
  update(stage: ClassStage | null, now: number): void {
    if (!stage || this.props.size === 0) return;
    for (const [id, p] of this.props) {
      const pose = stage.pose(id, now);
      p.root.position.y = p.base[1] + pose.lift;
      p.root.rotation.set(pose.tilt, pose.yaw, 0);
      p.root.scaling.set(pose.sxz, pose.sy, pose.sxz);
    }
  }

  /** After the frame renders: the creatures' need badges over them (row-major view-projection, canvas px). */
  overlay(stage: ClassStage | null, viewProj: ArrayLike<number>, width: number, height: number, now: number): void {
    const needs = stage?.needs(now) ?? [];
    const live = new Set<string>();
    for (const n of needs) {
      const base = this.spots.get(n.prop);
      if (!base) continue;
      const top = this.props.get(n.prop)?.height ?? DEFAULT_HEIGHT_M;
      live.add(n.prop);
      let b = this.badges.get(n.prop);
      if (!b) {
        b = { el: h("div", { class: "class-need", hidden: true }), key: "", shown: false };
        this.badges.set(n.prop, b);
        this.layer.append(b.el);
      }
      if (b.key !== n.prompt.key) {
        b.key = n.prompt.key;
        b.el.replaceChildren(pickIcon(n.prompt));
        b.el.title = n.prompt.label;
      }
      const at = screenAt(viewProj, base[0], base[1] + top + NEED_ABOVE_M, base[2], width, height);
      if (at) {
        b.el.style.transform = `translate3d(${at[0].toFixed(1)}px, ${at[1].toFixed(1)}px, 0) translate(-50%, -100%) scale(${(0.6 + 0.4 * n.strength).toFixed(2)})`;
        b.el.style.opacity = Math.min(1, n.strength * 1.5).toFixed(2);
      }
      setShown(b, !!at);
    }
    for (const [id, b] of this.badges) if (!live.has(id)) setShown(b, false);
  }

  /** The player left the room: every prop and badge goes. */
  clear(): void {
    this.generation++;
    for (const p of this.props.values()) {
      // Back out from under the prop's root first: a container's hierarchy may not reach outside it.
      for (const n of p.container.rootNodes) n.parent = null;
      p.container.removeAllFromScene();
      p.container.dispose();
      p.root.dispose();
    }
    this.props.clear();
    this.spots.clear();
    for (const b of this.badges.values()) b.el.remove();
    this.badges.clear();
  }

  /** For the F3 overlay. */
  perfLine(): [string, string] {
    return ["class props", `${this.props.size} loaded, ${[...this.badges.values()].filter((b) => b.shown).length} needs shown`];
  }

  dispose(): void {
    this.clear();
    this.layer.remove();
  }
}

function setShown(b: NeedBadge, shown: boolean): void {
  if (b.shown === shown) return;
  b.shown = shown;
  b.el.hidden = !shown;
}

/** Screen position (px) of a world point, or null behind the camera or well off screen. */
function screenAt(m: ArrayLike<number>, x: number, y: number, z: number, width: number, height: number): [number, number] | null {
  const w = x * m[3]! + y * m[7]! + z * m[11]! + m[15]!;
  if (w <= 0.1) return null;
  const sx = ((x * m[0]! + y * m[4]! + z * m[8]! + m[12]!) / w) * 0.5 + 0.5;
  const sy = 0.5 - ((x * m[1]! + y * m[5]! + z * m[9]! + m[13]!) / w) * 0.5;
  if (sx < -0.1 || sx > 1.1 || sy < -0.1 || sy > 1.1) return null;
  return [sx * width, sy * height];
}

function logOnce(id: string, line: string): void {
  if (logged.has(id)) return;
  logged.add(id);
  console.info(line);
}
