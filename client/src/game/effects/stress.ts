// A local stress scene for potion effects (dev only, not part of the build):
// `/src/game/effects/stress.html?n=50&fx=all&quality=medium` on the Vite dev
// server. It draws `n` characters walking in circles, each with a mix of
// effects (glows, chameleon robes and hair, shrunk and swollen, steam,
// glints, disguises, hidden nameplates) through the same renderer, effects
// controller and nameplates as the game, plus shimmering find spots, and
// reports frames per second, frame time and draw calls on screen and as
// `window.fxStats` (for scripted measurements). `fx=none` is the baseline.

import { SceneInstrumentation } from "@babylonjs/core/Instrumentation/sceneInstrumentation";
import { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { CreateGround } from "@babylonjs/core/Meshes/Builders/groundBuilder";
import { defaultAppearance } from "../../content/appearance";
import { CATALOG, hairStylesFor, PALETTE } from "../../content/catalog";
import { houseInfo } from "../../content/houses";
import type { Appearance, House, VisibleEffect } from "../../wasm/sim_wasm";
import { Nameplates, type PlateTarget } from "../../ui/nameplates";
import { CharacterRenderer, sharedLibrary } from "../characters";
import { WorldScene } from "../scene";
import { EffectsController, type FxSubject } from "./controller";
import { dyeFamilies } from "./chameleon";

const params = new URLSearchParams(location.search);
const N = Math.max(1, Math.min(200, Number(params.get("n") ?? 50)));
const MODE = params.get("fx") ?? "all";
const QUALITY = (params.get("quality") ?? "medium") as "low" | "medium" | "high";
const HOUSES: House[] = ["roarwing", "burrowsby", "skytalon", "serpentyne"];

/** A small deterministic generator, so runs are comparable. */
let seed = 7;
const rand = () => {
  seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
  return seed / 4294967296;
};
const pick = <T>(list: readonly T[]): T => list[Math.floor(rand() * list.length)]!;

function randomAppearance(i: number): Appearance {
  const a = defaultAppearance(HOUSES[i % 4]!);
  a.bodyType = i % 2;
  a.hairStyle = pick(hairStylesFor(a.bodyType)).id;
  a.height = 60 + Math.floor(rand() * 140);
  a.skinTone = Math.floor(rand() * 8);
  a.hairColour = Math.floor(rand() * 20);
  a.eyeColour = Math.floor(rand() * 13);
  // A crafted garment now and then, in random dyes.
  const crafted = CATALOG.items.filter((d) => d.kind === "clothing" && d.tags.includes("crafted"));
  if (rand() < 0.5 && crafted.length) {
    const d = pick(crafted);
    a.outfit[d.slot!] = { itemDef: d.id, dyes: [Math.floor(rand() * 48), Math.floor(rand() * 48), Math.floor(rand() * 48)] };
  }
  // Brewed finishes on the robe now and then (the toon material keeps them working).
  const body = a.outfit.body;
  if (body && MODE !== "none" && rand() < 0.35) a.outfit.body = { ...body, finish: rand() < 0.5 ? "shimmer" : "colour_shift" };
  return a;
}

/** A mix of effects for character `i` (every kind is well represented). */
function effectsFor(i: number, looks: Appearance[]): VisibleEffect[] {
  if (MODE === "none") return [];
  const fams = dyeFamilies().length;
  const out: VisibleEffect[] = [];
  const k = i % 9;
  if (k === 0 || k === 5 || rand() < 0.3) out.push({ kind: "glow", colour: Math.floor(rand() * PALETTE.colours.length) });
  if (k === 1 || k === 6) out.push({ kind: "chameleon", part: k === 1 ? "robe" : "hair", family: Math.floor(rand() * fams) });
  if (k === 2) out.push({ kind: "scale", pct: 50 });
  if (k === 3) out.push({ kind: "scale", pct: 130 });
  if (k === 4 || k === 7 || rand() < 0.25) out.push({ kind: "steam" });
  if (k === 8 || rand() < 0.2) out.push({ kind: "lucky" });
  if (k === 5) out.push({ kind: "disguise", appearance: looks[(i + 1) % looks.length]! });
  if (k === 7) out.push({ kind: "nameplateHidden" });
  if (k === 6) out.push({ kind: "babbling" });
  return out;
}

function main(): void {
  const canvas = document.getElementById("world") as HTMLCanvasElement;
  const stats = document.getElementById("stats")!;
  const view = new WorldScene(canvas, QUALITY);
  const scene = view.scene;
  const ground = CreateGround("stress-ground", { width: 80, height: 80 }, scene);
  const mat = new StandardMaterial("stress-ground", scene);
  mat.diffuseColor = new Color3(0.32, 0.28, 0.24);
  mat.specularColor = Color3.Black();
  ground.material = mat;
  view.addStatic([ground]);

  const cam = new ArcRotateCamera("stress-camera", -Math.PI / 2, 1.05, 22, new Vector3(0, 1, 0), scene);
  cam.minZ = 0.05;
  cam.attachControl(true);
  view.attachCamera(cam); // the toon look's outline and bloom passes

  const renderer = new CharacterRenderer(scene, { library: sharedLibrary(), onMesh: (m, added) => (added ? view.addDynamicCaster(m) : view.removeDynamicCaster(m)) });
  const fx = new EffectsController({ scene, headHeight: (id) => renderer.headHeight(id) });
  const plates = new Nameplates(document.getElementById("hud")!);
  const looks = Array.from({ length: N }, (_, i) => randomAppearance(i));
  const people = looks.map((a, i) => ({ id: i + 1, appearance: a, radius: 3 + (i % 10) * 1.1, phase: rand() * Math.PI * 2, speed: 0.25 + rand() * 0.2, name: `Student ${i + 1}`, house: HOUSES[i % 4]! }));
  for (const p of people) fx.book.set(p.id, 1, effectsFor(p.id - 1, looks));
  fx.setFindSpots([
    { id: "a", pos: [1.5, 0, 1.5] },
    { id: "b", pos: [-2, 0, 2.5] },
    { id: "c", pos: [0, 0, -3] },
  ]);

  const inst = new SceneInstrumentation(scene);
  inst.captureFrameTime = true;
  const subjects: FxSubject[] = people.map((p) => ({ id: p.id, x: 0, y: 0, z: 0, yaw: 0 }));
  const targets: PlateTarget[] = people.map((p) => ({ id: p.id, name: p.name, colour: houseInfo(p.house).colour, x: 0, y: 0, z: 0 }));
  const shown: PlateTarget[] = [];
  const eye: [number, number, number] = [0, 0, 0];
  let t = 0;
  let frames = 0;
  let worstMs = 0;
  let windowStart = performance.now();
  let last = performance.now();
  const out = { fps: 0, frameMs: 0, worstFrameMs: 0, drawCalls: 0, characters: N, mode: MODE, glows: 0, glints: 0, puffs: 0, recoloured: 0, assembled: false };
  (window as unknown as { fxStats: typeof out }).fxStats = out;

  view.engine.runRenderLoop(() => {
    const now = performance.now();
    const dt = Math.min((now - last) / 1000, 0.1);
    worstMs = Math.max(worstMs, now - last);
    last = now;
    t += dt;
    for (let i = 0; i < people.length; i++) {
      const p = people[i]!;
      const a = p.phase + t * p.speed * (i % 2 ? 1 : -1);
      const s = subjects[i]!;
      s.x = Math.cos(a) * p.radius;
      s.z = Math.sin(a) * p.radius;
      s.y = 0;
      s.yaw = Math.atan2(-Math.sin(a), Math.cos(a)) * (i % 2 ? 1 : -1);
    }
    fx.prepare(dt, subjects, Date.now());
    renderer.begin();
    for (let i = 0; i < people.length; i++) {
      const p = people[i]!;
      const s = subjects[i]!;
      const look = fx.look(p.id);
      const disguise = look?.appearance ?? null;
      renderer.show(p.id, disguise ? look!.version : 1, disguise ?? p.appearance, s.x, s.y, s.z, s.yaw, "walk", p.speed * p.radius, look?.fx ?? null);
    }
    renderer.update(dt, cam.position);
    fx.render(dt, subjects, cam, [0, 0, 0]);
    scene.render();

    shown.length = 0;
    for (let i = 0; i < people.length; i++) {
      const p = people[i]!;
      if (fx.plateHidden(p.id)) continue;
      const tg = targets[i]!;
      tg.x = subjects[i]!.x;
      tg.y = renderer.plateHeight(p.id);
      tg.z = subjects[i]!.z;
      tg.name = fx.babbling(p.id) ? `${p.name} ⋯` : p.name;
      shown.push(tg);
    }
    eye[0] = cam.position.x;
    eye[1] = cam.position.y;
    eye[2] = cam.position.z;
    plates.update(shown, scene.getTransformMatrix().m, eye, canvas.clientWidth, canvas.clientHeight);

    frames++;
    if (now - windowStart >= 1000) {
      const r = renderer.stats;
      out.fps = Math.round((frames * 1000) / (now - windowStart));
      out.frameMs = Number(inst.frameTimeCounter.lastSecAverage.toFixed(2));
      out.worstFrameMs = Number(worstMs.toFixed(1));
      out.drawCalls = inst.drawCallsCounter.current;
      out.recoloured = r.recoloured;
      out.assembled = r.pending === 0 && r.visible === N;
      const [, line] = fx.perfLines()[0]!;
      const m = /(\d+) glows, (\d+) glints, (\d+) puffs/.exec(line);
      if (m) [out.glows, out.glints, out.puffs] = [Number(m[1]), Number(m[2]), Number(m[3])];
      stats.textContent = `${N} characters, fx=${MODE}, ${QUALITY}\n${out.fps} fps, frame ${out.frameMs} ms (worst ${out.worstFrameMs} ms)\n${out.drawCalls} draw calls\n${line}\n${r.visible} drawn, ${r.lod1} at LOD1, ${r.recoloured} recoloured, ${r.pending} pending`;
      frames = 0;
      worstMs = 0;
      windowStart = now;
    }
  });
}

main();
