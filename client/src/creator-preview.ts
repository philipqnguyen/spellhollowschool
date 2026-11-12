// Dev page (`vite` serves /creator-preview.html, from the body trial): the real
// character creator mounted without a server, so its live preview can be
// screenshotted on the local assets. Nothing is sent: creating a character
// needs the API and is not exercised here.
//
// `window.previewStats()` reports what the preview scene drew in its last frame:
// draw calls (SceneInstrumentation), and each active mesh with its triangles
// and whether it is skinned, so a dressed character can be confirmed to be a
// single mesh and a single draw call.

import "./style.css";
import { EngineStore } from "@babylonjs/core/Engines/engineStore";
import { SceneInstrumentation } from "@babylonjs/core/Instrumentation/sceneInstrumentation";
import type { Scene } from "@babylonjs/core/scene";
import { renderCreator } from "./ui/creator";

const screen = document.getElementById("screen") as HTMLElement;
renderCreator(screen, { onCreated: () => {}, onSignOut: () => {} });

const probes = new Map<Scene, SceneInstrumentation>();

function previewScene(): Scene | null {
  for (const engine of EngineStore.Instances) {
    for (const scene of engine.scenes) return scene;
  }
  return null;
}

function stats() {
  const scene = previewScene();
  if (!scene) return null;
  let probe = probes.get(scene);
  if (!probe) {
    probe = new SceneInstrumentation(scene);
    probe.captureFrameTime = false;
    probes.set(scene, probe);
  }
  const meshes = scene.getActiveMeshes().data.slice(0, scene.getActiveMeshes().length).map((m) => ({
    name: m.name,
    triangles: Math.round((m.getTotalIndices() || 0) / 3),
    vertices: m.getTotalVertices(),
    skinned: !!m.skeleton,
    subMeshes: m.subMeshes?.length ?? 0,
  }));
  return { drawCalls: probe.drawCallsCounter.lastSecAverage, drawCallsFrame: probe.drawCallsCounter.current, meshes };
}

(window as unknown as { previewStats: typeof stats }).previewStats = stats;

/**
 * `window.previewFocus(targetY, radius, alpha?, beta?)` holds the preview
 * camera on a point (a face close-up for screenshots); `previewFocus(null)`
 * lets the creator frame the character again.
 */
let focus: { y: number; radius: number; alpha?: number; beta?: number } | null = null;
let hooked = false;
function previewFocus(y: number | null, radius = 1, alpha?: number, beta?: number): void {
  focus = y === null ? null : { y, radius, alpha, beta };
  const scene = previewScene();
  if (!scene || hooked) return;
  hooked = true;
  scene.onBeforeRenderObservable.add(() => {
    const cam = scene.activeCamera as unknown as { target?: { y: number }; radius?: number; alpha?: number; beta?: number; lowerRadiusLimit?: number } | null;
    if (!focus || !cam?.target) return;
    cam.lowerRadiusLimit = 0.3;
    cam.target.y = focus.y;
    cam.radius = focus.radius;
    if (focus.alpha !== undefined) cam.alpha = focus.alpha;
    if (focus.beta !== undefined) cam.beta = focus.beta;
  });
}
(window as unknown as { previewFocus: typeof previewFocus }).previewFocus = previewFocus;
