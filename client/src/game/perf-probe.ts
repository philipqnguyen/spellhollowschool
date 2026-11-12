// A measurement hook for renderer work: with `?probe` in the page URL the
// game exposes `window.shsProbe`, which drives the camera along a fixed path
// and reports frame times, draw calls and render-target memory. Scripts
// (Playwright) use it to compare builds on identical views: the same path
// over the same seconds, whatever the frame rate. Never loaded without the
// URL flag, so normal sessions carry none of it.
//
// Castle D1 (docs/art/castle-kit.md "Budgets"):
//
// - SSAO (High only): `ssao(on?)` reads or switches the pass on the live
//   view; a path may run with it on or off (`ProbePath.ssao`) and time the
//   SSAO passes' own GPU work (`passGpu`: a timer query round the passes
//   whose names contain "ssao", instead of the whole frame's, which WebGL
//   cannot nest); `ssaoCost(path)` runs the path with SSAO on and off in
//   alternation and reports the difference (frame interval, GPU frame
//   time, render-target memory) and the passes' own GPU time.
// - The look probe: `look(id)` measures the framing `id` of
//   castle/look-probe.ts on the frame the camera shows now (the caller has
//   placed it), with the characters hidden for that frame; `lookMetrics()`
//   recomputes every framing measured so far (the look-up's soffit ratio
//   reads the gallery floor of `gs_l3`).
//
// Character look pass (docs/art/character-look.md section 5): `post(patch?)` reads
// or changes the live view's screen-pass options (`samples`, `fxaa`,
// `bloom`: a measurement only, the next quality change restores them), and
// `resolution()` the render scale in use, `holdResolution(true)` keeps the
// dynamic-resolution guard from stepping during a measurement; a path's
// `passGpu` may name a pass to time it alone.
//
// Character look pass 2 (docs/art/character-look.md section 4): `outlines(on?)`
// forces the characters' inverted-hull outlines off (false) or on (true), or
// gives them back to the quality (null); no argument reads the override.

import "@babylonjs/core/Engines/Extensions/engine.query";
import "@babylonjs/core/Engines/AbstractEngine/abstractEngine.timeQuery";
import { EngineInstrumentation } from "@babylonjs/core/Instrumentation/engineInstrumentation";
import { SceneInstrumentation } from "@babylonjs/core/Instrumentation/sceneInstrumentation";
import { Ray } from "@babylonjs/core/Culling/ray";
import { Matrix, Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import type { Engine } from "@babylonjs/core/Engines/engine";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { PostProcess } from "@babylonjs/core/PostProcesses/postProcess";
import type { Scene } from "@babylonjs/core/scene";
import { gpuTextureBytes } from "./castle/ktx2";
import { OUTLINE_OVERRIDE } from "./toon/outline";
import {
  lookMetrics,
  lookView,
  luminance,
  LOOK_VIEWS,
  srgbToLinear,
  type LookSample,
  type LookSet,
  type LookSetResult,
  type LookView,
  type MetricResult,
  type Vec3,
} from "./castle/look-probe";
import { assetUrl } from "../assets";

/** A camera path: the orbit angles sweep linearly from `from` to `to` over `seconds`. */
export interface ProbePath {
  seconds: number;
  /** Seconds to hold the first view before measuring (shaders, streaming). */
  warmup?: number;
  from: { alpha: number; beta: number };
  to: { alpha: number; beta: number };
  /** Castle D1: run with the SSAO pass on or off (restored afterwards); omitted: as it is. */
  ssao?: boolean;
  /**
   * Castle D1: time the SSAO passes' GPU work (`ssaoGpuMs`) instead of the
   * whole frame's (`gpuMean` is then null); a string times the passes whose
   * names match it as a regular expression instead (e.g. "toonBright|toonBlur").
   */
  passGpu?: boolean | string;
}

export interface ProbeResult {
  frames: number;
  /** Wall time between frames (ms): the real cost with the frame-rate cap off. */
  intervalMean: number;
  intervalP50: number;
  intervalP95: number;
  /** CPU time of a frame (Babylon's frame time counter, ms). */
  cpuMean: number;
  /** GPU time of a frame (ms), when the browser exposes timer queries; else null. */
  gpuMean: number | null;
  drawCallsMean: number;
  drawCallsMax: number;
  activeMeshesMean: number;
  /** Render width and height. */
  width: number;
  height: number;
  /** Bytes of every render target texture (colour, depth, MSAA samples), estimated. */
  renderTargetBytes: number;
  /** Bytes of every GPU texture the engine holds, estimated. */
  textureBytes: number;
  heapStart: number | null;
  heapEnd: number | null;
  /** Castle D1: whether the SSAO pass ran (null: this build has none). */
  ssao: boolean | null;
  /** The camera's screen passes, in chain order. */
  passes: string[];
  /** Castle D1, with `passGpu`: the timed passes' GPU time a frame (ms, mean), null without timer queries or such passes. */
  ssaoGpuMs: number | null;
}

export interface ProbeHandles {
  engine: Engine;
  scene: Scene;
  camera: ArcRotateCamera;
  /** The world scene (scene.ts), for scripts that switch passes or lights between runs. */
  view?: unknown;
}

/** `ssaoCost`: the path with SSAO on and off. */
export interface SsaoCost {
  on: ProbeResult[];
  off: ProbeResult[];
  /** Medians over the runs: on, off, on - off. */
  intervalP50: [number, number, number];
  intervalP95: [number, number, number];
  gpuMean: [number, number, number] | null;
  renderTargetBytes: [number, number, number];
  drawCallsMean: [number, number, number];
  /** The SSAO passes' own GPU time a frame (ms), from one more run with `passGpu`. */
  passGpuMs: number | null;
}

/** `look`: one framing measured. */
export interface LookResult {
  id: string;
  width: number;
  height: number;
  /** Per set: valid samples, mean luminance and linear rgb, the materials hit, and why samples were dropped. */
  sets: Record<string, { n: number; lum: number | null; rgb: Vec3 | null; materials: Record<string, number>; dropped: Record<string, number> }>;
  metrics: MetricResult[];
  /** Milliseconds the probe took (ray casts and the frame read). */
  ms: number;
}

export function installProbe(h: ProbeHandles): void {
  const looks: Record<string, Record<string, LookSetResult>> = {};
  const probe = {
    handles: h,
    run: (path: ProbePath) => runPath(h, path),
    textures: () => textureBytes(h.engine),
    /** Reads (no argument) or changes the screen passes' options (samples, fxaa, bloom); null without passes. */
    post: (patch?: Record<string, unknown>) => postOptions(h.view, patch),
    /** The render scale in use and the quality's own (null on a build without the guard). */
    resolution: () => (h.view as { renderScale?: unknown } | undefined)?.renderScale ?? null,
    /** Holds (true) or releases the dynamic-resolution guard: measurements run at a fixed scale. */
    holdResolution: (hold: boolean) => (h.view as { holdResolution?: (on: boolean) => void } | undefined)?.holdResolution?.(hold),
    /** Forces the character outlines on or off (null: the quality decides); returns the override. */
    outlines: (on?: boolean | null) => {
      if (on !== undefined) OUTLINE_OVERRIDE.on = on;
      return OUTLINE_OVERRIDE.on;
    },
    /** Reads (no argument) or switches the SSAO pass; null when this build has none. */
    ssao: (on?: boolean) => (on === undefined ? ssaoOf(h.view) : setSsao(h.view, on)),
    ssaoCost: (path: ProbePath, pairs = 2) => ssaoCost(h, path, pairs),
    /** Measures framing `id` on the frame shown now; `mark` draws the samples used over the page (for a screenshot; `unmark` clears them). */
    look: async (id: string, opts: { mark?: boolean } = {}) => {
      const view = lookView(id);
      if (!view) throw new Error(`unknown look framing ${JSON.stringify(id)} (${LOOK_VIEWS.map((v) => v.id).join(", ")})`);
      return look(h, view, looks, opts.mark ?? false);
    },
    unmark: () => document.getElementById(MARKS_ID)?.remove(),
    /** Every framing measured so far, its numbers recomputed with all the others (cross-view metrics). */
    lookMetrics: () => Object.fromEntries(Object.entries(looks).map(([id, sets]) => [id, lookMetrics(lookView(id)!, sets, looks)])),
    lookViews: () => LOOK_VIEWS.map((v) => ({ id: v.id, description: v.description })),
  };
  (window as unknown as { shsProbe: typeof probe }).shsProbe = probe;
}

function heap(): number | null {
  const m = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory;
  return m ? m.usedJSHeapSize : null;
}

// ------------------------------------------------------------ pass options

interface PostView {
  post?: { options: object; configure: (o: object) => void } | null;
}

/** The live view's screen-pass options, after applying `patch` (a measurement only). */
export function postOptions(view: unknown, patch?: Record<string, unknown>): object | null {
  const post = (view as PostView | undefined)?.post;
  if (!post) return null;
  if (patch) post.configure({ ...post.options, ...patch });
  return { ...post.options };
}

// ------------------------------------------------------------ SSAO switch

/** The live view's SSAO switch (scene.ts `WorldScene`: `quality.ssao`, set through `setSsao` or the profile). */
interface SsaoView {
  quality?: { ssao?: boolean };
  setSsao?: (on: boolean) => void;
  profile?: object;
  applyProfile?: () => void;
}

/** Whether the view runs SSAO now; null when the build has no SSAO. */
export function ssaoOf(view: unknown): boolean | null {
  const q = (view as SsaoView | undefined)?.quality;
  return q && typeof q.ssao === "boolean" ? q.ssao : null;
}

/**
 * Switches SSAO on the live view (a measurement only: the next quality
 * change sets it from the profile again). Returns the state after, null when
 * the build has none.
 */
export function setSsao(view: unknown, on: boolean): boolean | null {
  const v = view as SsaoView | undefined;
  if (!v || ssaoOf(v) === null) return null;
  if (typeof v.setSsao === "function") v.setSsao(on);
  else if (v.profile && typeof v.applyProfile === "function") {
    v.profile = { ...v.profile, ssao: on };
    v.applyProfile();
  }
  return ssaoOf(v);
}

/** The camera's screen passes, in chain order. */
function cameraPasses(camera: ArcRotateCamera): PostProcess[] {
  const list = (camera as unknown as { _postProcesses?: (PostProcess | null)[] })._postProcesses ?? [];
  return list.filter((p): p is PostProcess => !!p);
}

/** Passes the SSAO timer covers: every pass whose name contains "ssao". */
export const SSAO_PASS = /ssao/i;

/**
 * GPU time of some passes per frame (the SSAO passes: `match` SSAO_PASS): a
 * WebGL2 TIME_ELAPSED query from before the first one draws to after the
 * last one has, polled each frame.
 * Never while Babylon's own frame timer runs (WebGL cannot nest them).
 */
function passTimer(engine: Engine, scene: Scene, camera: ArcRotateCamera, match: RegExp): { stop: () => number[] } | null {
  const gl = (engine as unknown as { _gl?: WebGL2RenderingContext })._gl;
  const ext = gl?.getExtension("EXT_disjoint_timer_query_webgl2") as { TIME_ELAPSED_EXT: number; GPU_DISJOINT_EXT: number } | null | undefined;
  const passes = cameraPasses(camera).filter((p) => match.test(p.name));
  if (!gl || !ext || passes.length === 0) return null;
  const first = passes[0]!;
  const last = passes[passes.length - 1]!;
  const pending: WebGLQuery[] = [];
  const results: number[] = [];
  let active: WebGLQuery | null = null;
  const begin = first.onBeforeRenderObservable.add(() => {
    if (active) return;
    active = gl.createQuery();
    if (active) gl.beginQuery(ext.TIME_ELAPSED_EXT, active);
  });
  const end = last.onAfterRenderObservable.add(() => {
    if (!active) return;
    gl.endQuery(ext.TIME_ELAPSED_EXT);
    pending.push(active);
    active = null;
  });
  const poll = scene.onAfterRenderObservable.add(() => {
    while (pending.length && gl.getQueryParameter(pending[0]!, gl.QUERY_RESULT_AVAILABLE)) {
      const q = pending.shift()!;
      if (!gl.getParameter(ext.GPU_DISJOINT_EXT)) results.push((gl.getQueryParameter(q, gl.QUERY_RESULT) as number) * 1e-6);
      gl.deleteQuery(q);
    }
  });
  return {
    stop: () => {
      first.onBeforeRenderObservable.remove(begin);
      last.onAfterRenderObservable.remove(end);
      scene.onAfterRenderObservable.remove(poll);
      if (active) gl.endQuery(ext.TIME_ELAPSED_EXT);
      for (const q of pending) gl.deleteQuery(q);
      if (active) gl.deleteQuery(active);
      return results;
    },
  };
}

// ------------------------------------------------------------ paths

async function runPath(h: ProbeHandles, path: ProbePath): Promise<ProbeResult> {
  const { scene, engine, camera } = h;
  const ssaoBefore = ssaoOf(h.view);
  if (path.ssao !== undefined) setSsao(h.view, path.ssao);
  const sceneInst = new SceneInstrumentation(scene);
  sceneInst.captureFrameTime = true;
  const engineInst = new EngineInstrumentation(engine);
  // WebGL cannot nest timer queries: timing passes needs the frame timer off
  // (also one left on by anything else, as the F3 overlay's).
  if (path.passGpu) engine.captureGPUFrameTime(false);
  else engineInst.captureGPUFrameTime = true;
  const intervals: number[] = [];
  const cpu: number[] = [];
  const gpu: number[] = [];
  let draws = 0;
  let drawsMax = 0;
  let meshes = 0;
  let frames = 0;
  let measuring = false;
  let last = 0;
  let start = 0;
  let timer: { stop: () => number[] } | null = null;
  const warm = (path.warmup ?? 2) * 1000;
  const span = path.seconds * 1000;
  const set = (t: number) => {
    const k = Math.min(1, Math.max(0, t));
    camera.inertialAlphaOffset = 0;
    camera.inertialBetaOffset = 0;
    camera.alpha = path.from.alpha + (path.to.alpha - path.from.alpha) * k;
    camera.beta = path.from.beta + (path.to.beta - path.from.beta) * k;
  };
  const before = scene.onBeforeRenderObservable.add(() => {
    const now = performance.now();
    if (!start) start = now;
    const t = now - start - warm;
    set(t / span);
    if (t >= 0 && !measuring) {
      measuring = true;
      last = now;
      // The passes exist now (a switch rebuilt the chain during the warm-up).
      if (path.passGpu) timer = passTimer(engine, scene, camera, typeof path.passGpu === "string" ? new RegExp(path.passGpu) : SSAO_PASS);
      return;
    }
    if (measuring) {
      intervals.push(now - last);
      last = now;
    }
  });
  const after = scene.onAfterRenderObservable.add(() => {
    if (!measuring) return;
    frames++;
    const d = sceneInst.drawCallsCounter.current;
    draws += d;
    drawsMax = Math.max(drawsMax, d);
    meshes += scene.getActiveMeshes().length;
    cpu.push(sceneInst.frameTimeCounter.current);
    const g = engineInst.gpuFrameTimeCounter.current;
    if (!path.passGpu && g > 0) gpu.push(g * 1e-6); // nanoseconds
  });
  const heapStart = heap();
  await new Promise((r) => setTimeout(r, warm + span));
  scene.onBeforeRenderObservable.remove(before);
  scene.onAfterRenderObservable.remove(after);
  const passTimes = (timer as { stop: () => number[] } | null)?.stop() ?? [];
  sceneInst.dispose();
  // Dispose alone leaves the engine's frame timer running (a query every frame).
  engineInst.captureGPUFrameTime = false;
  engineInst.dispose();
  const ssaoRan = ssaoOf(h.view);
  const passes = cameraPasses(camera).map((p) => p.name);
  const tex = textureBytes(engine);
  if (path.ssao !== undefined && ssaoBefore !== null) setSsao(h.view, ssaoBefore);
  const sorted = [...intervals].sort((a, b) => a - b);
  const mean = (a: number[]) => (a.length ? a.reduce((s, v) => s + v, 0) / a.length : 0);
  return {
    frames,
    intervalMean: mean(intervals),
    intervalP50: sorted[Math.floor(sorted.length * 0.5)] ?? 0,
    intervalP95: sorted[Math.floor(sorted.length * 0.95)] ?? 0,
    cpuMean: mean(cpu),
    gpuMean: gpu.length ? mean(gpu) : null,
    drawCallsMean: frames ? draws / frames : 0,
    drawCallsMax: drawsMax,
    activeMeshesMean: frames ? meshes / frames : 0,
    width: engine.getRenderWidth(),
    height: engine.getRenderHeight(),
    renderTargetBytes: tex.renderTargets,
    textureBytes: tex.all,
    heapStart,
    heapEnd: heap(),
    ssao: ssaoRan,
    passes,
    ssaoGpuMs: passTimes.length ? mean(passTimes) : null,
  };
}

const median = (a: number[]): number => {
  const s = [...a].sort((x, y) => x - y);
  return s.length ? (s.length % 2 ? s[(s.length - 1) / 2]! : (s[s.length / 2 - 1]! + s[s.length / 2]!) / 2) : 0;
};

/** The path with SSAO on and off in alternation (`pairs` of each), then once more on with the passes timed. */
async function ssaoCost(h: ProbeHandles, path: ProbePath, pairs: number): Promise<SsaoCost> {
  const was = ssaoOf(h.view);
  if (was === null) throw new Error("this build has no SSAO pass");
  const on: ProbeResult[] = [];
  const off: ProbeResult[] = [];
  try {
    for (let i = 0; i < pairs; i++) {
      for (const s of i % 2 ? [false, true] : [true, false]) (s ? on : off).push(await runPath(h, { ...path, ssao: s, passGpu: false }));
    }
    const timed = await runPath(h, { ...path, ssao: true, passGpu: true });
    const triple = (f: (r: ProbeResult) => number): [number, number, number] => {
      const a = median(on.map(f));
      const b = median(off.map(f));
      return [a, b, a - b];
    };
    const gpuOk = on.every((r) => r.gpuMean !== null) && off.every((r) => r.gpuMean !== null);
    return {
      on,
      off,
      intervalP50: triple((r) => r.intervalP50),
      intervalP95: triple((r) => r.intervalP95),
      gpuMean: gpuOk ? triple((r) => r.gpuMean!) : null,
      renderTargetBytes: triple((r) => r.renderTargetBytes),
      drawCallsMean: triple((r) => r.drawCallsMean),
      passGpuMs: timed.ssaoGpuMs,
    };
  } finally {
    setSsao(h.view, was);
  }
}

// ------------------------------------------------------------ the look probe

/** Waits for `n` rendered frames. */
function frames(scene: Scene, n: number): Promise<void> {
  return new Promise((resolve) => {
    let left = n;
    const o = scene.onAfterRenderObservable.add(() => {
      if (--left > 0) return;
      scene.onAfterRenderObservable.remove(o);
      resolve();
    });
  });
}

/** The finished frame's pixels (RGBA, rows bottom first), read right after it is drawn. */
function captureFrame(engine: Engine, scene: Scene): Promise<{ w: number; h: number; px: Uint8Array }> {
  return new Promise((resolve, reject) => {
    scene.onAfterRenderObservable.addOnce(() => {
      const w = engine.getRenderWidth();
      const h = engine.getRenderHeight();
      engine.restoreDefaultFramebuffer();
      engine.readPixels(0, 0, w, h, true, false).then((d) => resolve({ w, h, px: new Uint8Array(d.buffer, d.byteOffset, d.byteLength) }), reject);
    });
  });
}

/** Meshes the probe's rays may hit: drawn, static (no skeleton), LOD0 (Babylon LOD levels draw in their base's place). */
function castleMeshes(scene: Scene): AbstractMesh[] {
  return scene.meshes.filter(
    (m) =>
      m.isEnabled() &&
      m.isVisible &&
      m.visibility > 0 &&
      !m.skeleton &&
      !m.infiniteDistance &&
      !!m.material &&
      m.getTotalIndices() > 0 &&
      !(m as unknown as { _masterMesh?: unknown })._masterMesh,
  );
}

interface Hit {
  dist: number;
  point: Vector3;
  normal: Vector3 | null;
  mesh: AbstractMesh;
}

function cast(meshes: AbstractMesh[], ray: Ray): Hit | null {
  let best: Hit | null = null;
  for (const m of meshes) {
    const info = ray.intersectsMesh(m, false);
    if (!info.hit || !info.pickedPoint || (best && info.distance >= best.dist)) continue;
    best = { dist: info.distance, point: info.pickedPoint.clone(), normal: info.getNormal(true, false), mesh: m };
  }
  return best;
}

/** Linear rgb means of each castle texture (albedo), read once per page from its PNG. */
const albedoCache = new Map<string, Promise<Vec3 | null>>();
let texturesJson: Promise<Record<string, { base?: string }>> | null = null;
const TEXTURES = "assets/world/textures/";

function albedoOf(material: string): Promise<Vec3 | null> {
  let p = albedoCache.get(material);
  if (p) return p;
  p = (async () => {
    texturesJson ??= fetch(assetUrl(`${TEXTURES}textures.json`))
      .then((r) => r.json())
      .then((j: { materials?: Record<string, { base?: string }> }) => j.materials ?? {})
      .catch(() => ({}));
    const base = (await texturesJson)[material]?.base;
    if (!base) return null;
    try {
      const img = await createImageBitmap(await (await fetch(assetUrl(TEXTURES + base))).blob());
      const canvas = new OffscreenCanvas(img.width, img.height);
      const ctx = canvas.getContext("2d");
      if (!ctx) return null;
      ctx.drawImage(img, 0, 0);
      const d = ctx.getImageData(0, 0, img.width, img.height).data;
      const lut = Array.from({ length: 256 }, (_, i) => srgbToLinear(i));
      const s: Vec3 = [0, 0, 0];
      for (let i = 0; i < d.length; i += 4) {
        s[0] += lut[d[i]!]!;
        s[1] += lut[d[i + 1]!]!;
        s[2] += lut[d[i + 2]!]!;
      }
      const n = d.length / 4;
      return [s[0] / n, s[1] / n, s[2] / n];
    } catch {
      return null;
    }
  })();
  albedoCache.set(material, p);
  return p;
}

/** Hides the characters (and their blob shadows) for the probe's frame; returns the undo. */
function hideCharacters(scene: Scene): () => void {
  const hidden = scene.meshes.filter((m) => m.isVisible && (!!m.skeleton || m.name === "character-blobs"));
  for (const m of hidden) m.isVisible = false;
  return () => {
    for (const m of hidden) m.isVisible = true;
  };
}

async function look(h: ProbeHandles, view: LookView, looks: Record<string, Record<string, LookSetResult>>, mark: boolean): Promise<LookResult> {
  const { engine, scene, camera } = h;
  const t0 = performance.now();
  const marks: [number, number, string][] | null = mark ? [] : null;
  document.getElementById(MARKS_ID)?.remove();
  const restore = hideCharacters(scene);
  let frame: { w: number; h: number; px: Uint8Array };
  try {
    await frames(scene, 3);
    frame = await captureFrame(engine, scene);
  } finally {
    restore();
  }
  const { w, h: ht, px } = frame;
  const meshes = castleMeshes(scene);
  const transform = scene.getTransformMatrix().clone();
  const viewport = camera.viewport.toGlobal(w, ht);
  const eye = camera.globalPosition.clone();
  const ray = new Ray(Vector3.Zero(), Vector3.Up(), 1);
  const up = Vector3.Up();
  const results: Record<string, LookSetResult> = {};
  const summary: LookResult["sets"] = {};
  for (const set of view.sets) {
    const samples: LookSample[] = [];
    const dropped: Record<string, number> = {};
    const drop = (why: string) => (dropped[why] = (dropped[why] ?? 0) + 1);
    for (const r of set.rays) {
      const s = await sampleRay(r, set);
      if (typeof s === "string") drop(s);
      else samples.push(s);
    }
    results[set.name] = { name: set.name, samples, dropped };
    const materials: Record<string, number> = {};
    for (const s of samples) materials[s.material] = (materials[s.material] ?? 0) + 1;
    const n = samples.length;
    const rgb: Vec3 | null = n ? [0, 1, 2].map((i) => samples.reduce((a, s) => a + s.rgb[i]!, 0) / n) as Vec3 : null;
    summary[set.name] = { n, lum: rgb ? luminance(rgb[0], rgb[1], rgb[2]) : null, rgb, materials, dropped };
  }
  looks[view.id] = results;
  if (marks) drawMarks(engine, marks, view.sets.map((s) => s.name));
  return { id: view.id, width: w, height: ht, sets: summary, metrics: lookMetrics(view, results, looks), ms: performance.now() - t0 };

  async function sampleRay(r: LookSet["rays"][number], set: LookSet): Promise<LookSample | string> {
    ray.origin.copyFromFloats(r.from[0], r.from[1], r.from[2]);
    ray.direction.copyFromFloats(r.dir[0], r.dir[1], r.dir[2]);
    ray.length = r.far + 0.5;
    const hit = cast(meshes, ray);
    if (!hit) return "miss";
    if (hit.dist < r.near) return "in front";
    if (hit.dist > r.far) return "behind";
    // Across the ray (the face normal's sign follows the winding, which the glTF import may mirror).
    if (!hit.normal || Math.abs(Vector3.Dot(hit.normal, ray.direction)) < 0.7) return "facing";
    if (Vector3.Dot(hit.normal, ray.direction) > 0) hit.normal.scaleInPlace(-1);
    const material = hit.mesh.material?.name ?? "";
    if (set.materials && !set.materials.some((p) => material.startsWith(p))) return "material";
    if (set.clear) {
      // Mid-wall: nothing within `clear` along the wall either way (corners, shafts, piers, jambs).
      const tangent = Vector3.Cross(hit.normal, up);
      if (tangent.lengthSquared() > 1e-6) {
        tangent.normalize();
        ray.origin.copyFrom(hit.point).addInPlace(hit.normal.scale(0.05));
        ray.length = set.clear;
        for (const sgn of [1, -1]) {
          ray.direction.copyFrom(tangent).scaleInPlace(sgn);
          if (cast(meshes, ray)) return "near a corner";
        }
      }
    }
    // The camera sees this very point.
    const toPoint = hit.point.subtract(eye);
    const d = toPoint.length();
    ray.origin.copyFrom(eye);
    ray.direction.copyFrom(toPoint).scaleInPlace(1 / d);
    ray.length = d + 0.5;
    const seen = cast(meshes, ray);
    if (!seen || seen.dist < d - (0.05 + 0.005 * d)) return "hidden";
    const p = Vector3.Project(hit.point, Matrix.IdentityReadOnly, transform, viewport);
    if (p.z < 0 || p.z > 1 || p.x < 2 || p.y < 2 || p.x > w - 3 || p.y > ht - 3) return "off screen";
    // A 3 x 3 patch (rows bottom first in the read).
    const cx = Math.round(p.x);
    const cy = ht - 1 - Math.round(p.y);
    const rgb: Vec3 = [0, 0, 0];
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const i = ((cy + dy) * w + (cx + dx)) * 4;
        rgb[0] += srgbToLinear(px[i]!) / 9;
        rgb[1] += srgbToLinear(px[i + 1]!) / 9;
        rgb[2] += srgbToLinear(px[i + 2]!) / 9;
      }
    }
    marks?.push([p.x, p.y, set.name]);
    return { lum: luminance(rgb[0], rgb[1], rgb[2]), rgb, albedo: await albedoOf(material), y: hit.point.y, material, ...(r.group ? { group: r.group } : {}) };
  }
}

const MARKS_ID = "shs-look-marks";
const MARK_COLOURS = ["magenta", "cyan", "yellow", "lime", "orange"]; // debug dots only, never game colours

/** Dots over the canvas where the look probe read pixels, one colour per set (debug screenshots only). */
function drawMarks(engine: Engine, marks: [number, number, string][], sets: string[]): void {
  const canvas = engine.getRenderingCanvas();
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const k = rect.width / Math.max(1, engine.getRenderWidth());
  const overlay = document.createElement("canvas");
  overlay.id = MARKS_ID;
  overlay.width = Math.round(rect.width);
  overlay.height = Math.round(rect.height);
  Object.assign(overlay.style, { position: "fixed", left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`, pointerEvents: "none", zIndex: "1000" });
  const ctx = overlay.getContext("2d");
  if (!ctx) return;
  for (const [x, y, set] of marks) {
    ctx.fillStyle = MARK_COLOURS[Math.max(0, sets.indexOf(set)) % MARK_COLOURS.length]!;
    ctx.fillRect(x * k - 2, y * k - 2, 4, 4);
  }
  ctx.font = "12px sans-serif";
  sets.forEach((s, i) => {
    ctx.fillStyle = MARK_COLOURS[i % MARK_COLOURS.length]!;
    ctx.fillText(s, 8, 16 + 14 * i);
  });
  document.body.appendChild(overlay);
}

// ------------------------------------------------------------ memory

/** Rough bytes per texel for the engine's texture types and formats. */
function texelBytes(t: { type: number; format: number }): number {
  // Constants: TEXTURETYPE_UNSIGNED_BYTE 0, FLOAT 1, HALF_FLOAT 2; formats R 6, RG 7, RGB 4, RGBA 5.
  const channels = t.format === 6 ? 1 : t.format === 7 ? 2 : 4;
  const size = t.type === 1 ? 4 : t.type === 2 ? 2 : 1;
  return channels * size;
}

function textureBytes(engine: Engine): { all: number; renderTargets: number } {
  let all = 0;
  for (const t of engine.getLoadedTexturesCache()) {
    // In the texture's real GPU format (KTX2 textures are block-compressed; castle kit M4).
    const base = gpuTextureBytes(t.width, t.height, t.format, t.type, t.generateMipMaps);
    all += base * (t.isCube ? 6 : 1) * Math.max(1, t.samples || 1);
  }
  // Render targets (post-process buffers, shadow maps): colour plus depth attachments.
  let renderTargets = 0;
  type Wrapper = { width: number; height: number; samples: number; texture?: { type: number; format: number } | null; _depthStencilTexture?: unknown; _generateDepthBuffer?: boolean };
  const wrappers = ((engine as unknown as { _renderTargetWrapperCache?: Wrapper[] })._renderTargetWrapperCache ?? []) as Wrapper[];
  for (const w of wrappers) {
    const px = w.width * w.height * Math.max(1, w.samples || 1);
    renderTargets += px * (w.texture ? texelBytes(w.texture) : 4) + (w._generateDepthBuffer || w._depthStencilTexture ? px * 4 : 0);
  }
  return { all, renderTargets };
}
