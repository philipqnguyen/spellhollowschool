// The talking portraits in the castle (docs/architecture.md "NPCs";
// client only, never an entity): a gilt shadow-box frame on
// its wall for each of `NpcBook.portraits()`, and inside it the subject,
// drawn with the character system from `NpcBook.look`: a half-length
// figure standing 0.15 m off the box's back, clipped to the canvas
// (styles.ts `portraitStyle`). Near us it is alive (portrait-state.ts): the
// idle clip breathes and shifts it, a slow turn sways it, and its head
// follows us through the characters' look-at (characters/look.ts); farther
// it holds still like a painting; beyond `DRAW_M` only the frame shows.
// The castle's decorative portrait clusters are kit meshes this never
// touches: they stay still and silent.
//
// The frames: the built ones (`assets/npcs/manifest.json`: a GLB frame per
// canvas size and four backdrops) when they are built, else a placeholder
// box (palette gilt and a palette backdrop), so the portraits work before the
// art merges. A frame is drawn only near (`FRAME_M`, our floor).

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Texture } from "@babylonjs/core/Materials/Textures/texture";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import { CreatePlane } from "@babylonjs/core/Meshes/Builders/planeBuilder";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import type { AssetContainer } from "@babylonjs/core/assetContainer";
import type { Scene } from "@babylonjs/core/scene";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { CastleLibrary } from "../castle/materials";
import type { Appearance, PortraitView } from "../../wasm/sim_wasm";
import { paletteRgb } from "../castle/terrain";
import { toonifyContainer } from "../toon/convert";
import type { CharacterFx, CharacterStyle } from "../characters/renderer";
import type { Figure } from "../characters/cast";
import type { Npcs } from "./book";
import { FLOOR_BAND_M, NOD_DIP_M, nodDip, shiftYaw, subjectStates, type SubjectState } from "./portrait-state";
import { portraitLook, portraitStyle } from "./styles";
import { assetUrl } from "../../assets";

/** Frames are drawn within this (m) on our floor. */
export const FRAME_M = 45;
/** The shadow box's depth (the canvas recessed into it) and border (m), until the manifest says. */
export const BOX_DEPTH_M = 0.35;
export const BORDER_M = 0.09;
/** The subject stands this far in front of the box's back (m). */
export const SUBJECT_OFF_BACK_M = 0.15;
/** The subject's distance off the wall face and the box opening's (m), until the manifest says (the Art build's). */
export const SUBJECT_Z_M = 0.162;
export const OPEN_Z_M = 0.362;
/** Beyond this (m) the Art frame draws its one-box distant version, until the manifest says. */
export const FRAME_LOD1_M = 18;
/** The frame GLB's material bound to the castle's shared trim sheet. */
const TRIM_MATERIAL = "castle_trim";
/** The top of the subject's head sits this far below the canvas top (m). */
export const HEAD_ROOM_M = 0.14;
/** Figure ids are this minus the portrait's index: negative, never an entity's. */
export const FIGURE_ID_BASE = -1000;

/** The backdrops' palette colours (castle_textures.json), darkened: a painted room behind the sitter. */
const BACKDROP_NAMES: readonly (readonly string[])[] = [["tapestry_umber", "frame_oak"], ["ledger_green", "map_green"], ["wine_red", "book_red"], ["ink_blue", "book_blue"]];
const BACKDROP_SHADE = 0.55;
const GILT = paletteRgb(["frame_gilt", "gilt", "brass"]) ?? ([0.8, 0.62, 0.3] as const);

const ALIVE_FX: CharacterFx = { scale: 1, tint: null, animRate: 1 };
const STILL_FX: CharacterFx = { scale: 1, tint: null, animRate: 0 };

/** `assets/npcs/manifest.json` (the built portrait frames). */
export interface PortraitManifest {
  frames: Record<string, string>;
  backdrops: string[];
  depth_m: number;
  border_m: number;
  subject_z_m: number;
  open_z_m: number;
  lod1_m: number;
}

export function parseManifest(raw: unknown): PortraitManifest | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const frames = o.frames && typeof o.frames === "object" && !Array.isArray(o.frames) ? (o.frames as Record<string, unknown>) : null;
  if (!frames || !Array.isArray(o.backdrops)) return null;
  const f: Record<string, string> = {};
  for (const [k, v] of Object.entries(frames)) if (typeof v === "string" && /^\d+x\d+$/.test(k)) f[k] = v;
  return {
    frames: f,
    backdrops: o.backdrops.filter((b): b is string => typeof b === "string"),
    depth_m: typeof o.depth_m === "number" && o.depth_m > 0 ? o.depth_m : BOX_DEPTH_M,
    border_m: typeof o.border_m === "number" && o.border_m >= 0 ? o.border_m : BORDER_M,
    subject_z_m: typeof o.subject_z_m === "number" && o.subject_z_m > 0 ? o.subject_z_m : SUBJECT_Z_M,
    open_z_m: typeof o.open_z_m === "number" && o.open_z_m > 0 ? o.open_z_m : OPEN_Z_M,
    lod1_m: typeof o.lod1_m === "number" && o.lod1_m > 0 ? o.lod1_m : FRAME_LOD1_M,
  };
}

/** A canvas size's manifest key: centimetres, `<w>x<h>`. */
export function sizeKey(size: readonly number[]): string {
  return `${Math.round(size[0]! * 100)}x${Math.round(size[1]! * 100)}`;
}

interface Placed {
  view: PortraitView;
  root: TransformNode;
  shown: boolean;
  look: Appearance | undefined;
  style: CharacterStyle;
  figure: Figure;
  nodAt: number;
  /** The Art frame's full-detail meshes and its distant box (empty for placeholders). */
  lod0: AbstractMesh[];
  lod1: AbstractMesh[];
  far: boolean;
}

export class TalkingPortraits {
  private readonly placed: Placed[] = [];
  private readonly states: SubjectState[] = [];
  private readonly figures: Figure[] = [];
  private readonly materials: StandardMaterial[] = [];
  private readonly textures: Texture[] = [];
  private readonly containers: AssetContainer[] = [];
  private disposed = false;
  private source = "placeholder";
  private framesShown = 0;
  private trimTaken = 0;
  private subjectZ = SUBJECT_Z_M;
  private lod1M = FRAME_LOD1_M;

  constructor(
    private readonly scene: Scene,
    private readonly npcs: Npcs,
    private readonly assetsBase: string,
    /** The drawn head height of a figure (m; 1.75 until it is drawn). */
    private readonly headHeight: (id: number) => number,
    /** The castle's shared materials (the frames' castle_trim); absent in tests. */
    private readonly library?: CastleLibrary,
  ) {
    const look = portraitLook();
    this.npcs.portraits.forEach((view, i) => {
      const root = new TransformNode(`npc-portrait-${view.npc}`, scene);
      root.position.set(view.pos[0]!, view.pos[1]!, view.pos[2]!);
      root.rotation.y = view.yaw;
      root.setEnabled(false);
      const style = portraitStyle(view.npc, view, look, OPEN_Z_M);
      const appearance = npcs.book.look(view.npc);
      this.placed.push({
        view,
        root,
        shown: false,
        look: appearance,
        style,
        nodAt: -Infinity,
        lod0: [],
        lod1: [],
        far: false,
        figure: { id: FIGURE_ID_BASE - i, version: 1, appearance: appearance!, x: 0, y: 0, z: 0, yaw: view.yaw, anim: "idle", fx: STILL_FX, style, look: null },
      });
    });
    void this.build();
  }

  /** The frames: the built ones when they exist, else placeholders. */
  private async build(): Promise<void> {
    let manifest: PortraitManifest | null = null;
    try {
      const r = await fetch(assetUrl(`${this.assetsBase}npcs/manifest.json`));
      if (r.ok && (r.headers.get("content-type") ?? "").includes("json")) manifest = parseManifest(await r.json());
    } catch {
      manifest = null;
    }
    if (this.disposed) return;
    if (manifest && Object.keys(manifest.frames).length) {
      try {
        await this.buildFromManifest(manifest);
        this.source = "art";
        return;
      } catch (e) {
        console.warn("[npcs] portrait frames unavailable, drawing placeholders:", e);
      }
    }
    if (!this.disposed) this.buildPlaceholders();
  }

  private async buildFromManifest(m: PortraitManifest): Promise<void> {
    const base = `${this.assetsBase}npcs/`;
    this.subjectZ = m.subject_z_m;
    this.lod1M = m.lod1_m;
    await this.library?.ready();
    if (this.disposed) return;
    const bySize = new Map<string, AssetContainer>();
    for (const p of this.placed) {
      const key = sizeKey(p.view.size);
      const file = m.frames[key];
      if (!file) throw new Error(`no frame for ${key}`);
      if (!bySize.has(key)) {
        const c = await LoadAssetContainerAsync(assetUrl(base + file), this.scene, { pluginExtension: ".glb" });
        if (this.disposed) {
          c.dispose();
          return;
        }
        // castle_trim is the castle's shared trim sheet (one reference per frame file, released on dispose).
        const lib = this.library;
        toonifyContainer(c, this.scene, undefined, {
          shared: lib && {
            take: (name, placeholder) => {
              if (name !== TRIM_MATERIAL) return null;
              const mat = lib.take(name, placeholder);
              if (mat) this.trimTaken++;
              return mat;
            },
          },
        });
        bySize.set(key, c);
        this.containers.push(c);
      }
    }
    // The box's inside (back and sides, and the distant box) takes the placement's painted backdrop.
    const backdrops = m.backdrops.map((file) => {
      const t = new Texture(assetUrl(base + file), this.scene);
      this.textures.push(t);
      const mat = new StandardMaterial(`npc-backdrop-${file}`, this.scene);
      mat.diffuseTexture = t;
      mat.specularColor = Color3.Black();
      mat.maxSimultaneousLights = 8;
      this.materials.push(mat);
      return mat;
    });
    for (const p of this.placed) {
      const inst = bySize.get(sizeKey(p.view.size))!.instantiateModelsToScene((n) => `${n}-${p.view.npc}`, false);
      const mat = backdrops[p.view.backdrop] ?? backdrops[0];
      for (const n of inst.rootNodes) {
        n.parent = p.root;
        for (const mesh of n.getChildMeshes(false).concat(n instanceof Mesh ? [n] : [])) {
          mesh.isPickable = false;
          if (mesh.name.includes("npc_backdrop") && mat) mesh.material = mat;
          if (mesh.name.includes("npc_backdrop_lod1")) {
            mesh.setEnabled(false);
            p.lod1.push(mesh);
          } else p.lod0.push(mesh);
        }
      }
    }
  }

  /** A placeholder box: the backdrop on its back and sides, a gilt border at its front. */
  private buildPlaceholders(): void {
    const gilt = this.material("npc-frame-gilt", GILT);
    const backs = BACKDROP_NAMES.map((names, i) => {
      const c = paletteRgb(names) ?? ([0.3, 0.25, 0.2] as const);
      return this.material(`npc-backdrop-${i}`, [c[0] * BACKDROP_SHADE, c[1] * BACKDROP_SHADE, c[2] * BACKDROP_SHADE]);
    });
    for (const p of this.placed) {
      const [w, h] = p.view.size as [number, number];
      const d = BOX_DEPTH_M;
      const b = BORDER_M;
      const back = backs[p.view.backdrop] ?? backs[0]!;
      this.backPanel(p, back, 0.004);
      // The box's inner sides (the backdrop, a little darker by the light), then the gilt border round the opening.
      const sides: Mesh[] = [
        box(this.scene, w, 0.02, d, 0, h / 2, d / 2),
        box(this.scene, w, 0.02, d, 0, -h / 2, d / 2),
        box(this.scene, 0.02, h, d, w / 2, 0, d / 2),
        box(this.scene, 0.02, h, d, -w / 2, 0, d / 2),
      ];
      const inner = Mesh.MergeMeshes(sides, true)!;
      inner.material = back;
      inner.parent = p.root;
      const bars: Mesh[] = [
        box(this.scene, w + 2 * b, b, 0.05, 0, h / 2 + b / 2, d),
        box(this.scene, w + 2 * b, b, 0.05, 0, -h / 2 - b / 2, d),
        box(this.scene, b, h, 0.05, w / 2 + b / 2, 0, d),
        box(this.scene, b, h, 0.05, -w / 2 - b / 2, 0, d),
        // The box's outer shell behind the border, so the frame reads as a box from the side.
        box(this.scene, w + 2 * b, 0.03, d, 0, h / 2 + b, d / 2),
        box(this.scene, w + 2 * b, 0.03, d, 0, -h / 2 - b, d / 2),
        box(this.scene, 0.03, h + 2 * b, d, w / 2 + b, 0, d / 2),
        box(this.scene, 0.03, h + 2 * b, d, -w / 2 - b, 0, d / 2),
      ];
      const frame = Mesh.MergeMeshes(bars, true)!;
      frame.material = gilt;
      frame.parent = p.root;
      for (const m of [inner, frame]) {
        m.isPickable = false;
        m.freezeWorldMatrix();
      }
    }
  }

  private backPanel(p: Placed, mat: StandardMaterial, lift: number): void {
    const [w, h] = p.view.size as [number, number];
    const back = CreatePlane(`npc-canvas-${p.view.npc}`, { width: w, height: h }, this.scene);
    // A plane faces -z; the canvas faces into the room (+z of the frame).
    back.rotation.y = Math.PI;
    back.position.z = lift;
    back.parent = p.root;
    back.material = mat;
    back.isPickable = false;
  }

  private material(name: string, c: readonly [number, number, number]): StandardMaterial {
    const m = new StandardMaterial(name, this.scene);
    m.diffuseColor = new Color3(c[0], c[1], c[2]);
    m.specularColor = Color3.Black();
    this.materials.push(m);
    return m;
  }

  /** The subject of `npc` nods (a pass noticed). */
  nod(npc: string, nowMs: number): void {
    const p = this.placed.find((q) => q.view.npc === npc);
    if (p) p.nodAt = nowMs;
  }

  /**
   * Once a frame (in the castle; `feet` null elsewhere): the frames near us
   * shown, and the subjects to draw this frame, posed (handed to the cast).
   * `head`: our eyes, where the heads turn.
   */
  update(feet: readonly [number, number, number] | null, head: { x: number; y: number; z: number } | null, nowMs: number): readonly Figure[] {
    const figures = this.figures;
    figures.length = 0;
    let shown = 0;
    for (const p of this.placed) {
      const near = !!feet && this.frameNear(p.view, feet);
      if (near !== p.shown) {
        p.root.setEnabled(near);
        p.shown = near;
      }
      if (near) {
        shown++;
        if (p.lod1.length) {
          const far = Math.hypot(p.view.pos[0]! - feet![0], p.view.pos[2]! - feet![2]) > this.lod1M;
          if (far !== p.far) {
            p.far = far;
            for (const m of p.lod0) m.setEnabled(!far);
            for (const m of p.lod1) m.setEnabled(far);
          }
        }
      }
    }
    this.framesShown = shown;
    const states = subjectStates(this.npcs.portraits, feet, this.npcs.rules, this.states);
    const secs = nowMs / 1000;
    for (const s of states) {
      const p = this.placed[s.index]!;
      // Beyond the distant box's distance the box is shut: no subject to draw.
      if (!p.look || !p.shown || p.far) continue;
      const v = p.view;
      const f = p.figure;
      const fx = Math.sin(v.yaw);
      const fz = Math.cos(v.yaw);
      const off = this.source === "art" ? this.subjectZ : SUBJECT_OFF_BACK_M;
      f.x = v.pos[0]! + fx * off;
      f.z = v.pos[2]! + fz * off;
      f.y = v.pos[1]! + v.size[1]! / 2 - HEAD_ROOM_M - this.headHeight(f.id);
      f.yaw = v.yaw + (s.alive ? shiftYaw(secs, s.index) : 0);
      f.fx = s.alive ? ALIVE_FX : STILL_FX;
      if (s.looks && head) {
        const dip = nodDip(p.nodAt, nowMs) * NOD_DIP_M;
        const lp = (f.look = this.lookPointFor(s.index));
        lp.x = head.x;
        lp.y = head.y - dip;
        lp.z = head.z;
      } else f.look = null;
      figures.push(f);
    }
    return figures;
  }

  private readonly lookPoints: { x: number; y: number; z: number }[] = [];
  private lookPointFor(i: number): { x: number; y: number; z: number } {
    return (this.lookPoints[i] ??= { x: 0, y: 0, z: 0 });
  }

  private frameNear(v: PortraitView, feet: readonly [number, number, number]): boolean {
    const dy = v.pos[1]! - feet[1];
    if (dy < FLOOR_BAND_M[0] - 1 || dy > FLOOR_BAND_M[1] + 1) return false;
    return Math.hypot(v.pos[0]! - feet[0], v.pos[2]! - feet[2]) <= FRAME_M;
  }

  /** Where `npc`'s canvas hangs (subtitles anchor there), or null. */
  canvasOf(npc: string): PortraitView | null {
    return this.placed.find((p) => p.view.npc === npc)?.view ?? null;
  }

  perfLine(): [string, string] {
    return ["portraits", `${this.placed.length} talking (${this.source} frames), ${this.framesShown} frames shown, ${this.figures.length} subjects drawn (${this.figures.filter((f) => f.fx === ALIVE_FX).length} alive)`];
  }

  dispose(): void {
    this.disposed = true;
    for (const p of this.placed) p.root.dispose(false, false);
    for (const m of this.materials) m.dispose();
    for (const t of this.textures) t.dispose();
    for (const c of this.containers) c.dispose();
    for (; this.trimTaken > 0; this.trimTaken--) this.library?.release(TRIM_MATERIAL);
    this.placed.length = 0;
  }
}

function box(scene: Scene, w: number, h: number, d: number, x: number, y: number, z: number): Mesh {
  const m = CreateBox("npc-frame-part", { width: w, height: h, depth: d }, scene);
  m.position.set(x, y, z);
  return m;
}
