// A single character in its own canvas: the creator's live preview and the
// wardrobe's mirror. It runs its own small engine and scene (the world's
// lights, fog and cells have no business here) but shares the page's part
// library, so parts already loaded for the world are not fetched again.
// Drag to turn the view, wheel to zoom.

import { Engine } from "@babylonjs/core/Engines/engine";
import { Scene } from "@babylonjs/core/scene";
import { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { CreateDisc } from "@babylonjs/core/Meshes/Builders/discBuilder";
import type { Anim, Appearance } from "../../wasm/sim_wasm";
import { heightMetres } from "../../content/appearance";
import { CharacterRenderer, sharedLibrary } from "./index";

const ID = 1;
/** Camera framing: distance per metre of height (plus a margin) and the fraction of the height it looks at. */
const DISTANCE_PER_M = 2.1;
const DISTANCE_MARGIN = 0.2;
const DISTANCE = 1.72 * DISTANCE_PER_M + DISTANCE_MARGIN;
const LOOK_AT = 0.52;

export class CharacterPreview {
  private readonly engine: Engine;
  private readonly scene: Scene;
  private readonly camera: ArcRotateCamera;
  private readonly renderer: CharacterRenderer;
  private readonly resize: ResizeObserver;
  private appearance: Appearance;
  private version = 0;
  private anim: Anim = "idle";
  private targetY = 1;
  /** The height last framed for. */
  private framedHeight = 0;

  constructor(
    canvas: HTMLCanvasElement,
    appearance: Appearance,
  ) {
    this.appearance = appearance;
    this.engine = new Engine(canvas, true, { alpha: true, premultipliedAlpha: false, stencil: false, preserveDrawingBuffer: false, audioEngine: false }, false);
    this.engine.disableManifestCheck = true;
    this.engine.setHardwareScalingLevel(1 / Math.min(window.devicePixelRatio || 1, 2));
    const scene = new Scene(this.engine);
    this.scene = scene;
    scene.useRightHandedSystem = true;
    scene.clearColor = new Color4(0, 0, 0, 0);
    scene.ambientColor = new Color3(0.25, 0.22, 0.2);
    scene.skipPointerMovePicking = true;

    const hemi = new HemisphericLight("preview-sky", new Vector3(0, 1, 0), scene);
    hemi.intensity = 0.36; // the toon model adds fill and key (toon/material.ts)
    hemi.diffuse = new Color3(0.95, 0.95, 1);
    hemi.groundColor = new Color3(0.78, 0.68, 0.6);
    hemi.specular = Color3.Black();
    const key = new DirectionalLight("preview-key", new Vector3(-0.5, -0.8, -0.9), scene);
    key.intensity = 0.62;
    key.diffuse = new Color3(1, 0.9, 0.75); // candlelight from the front left

    const floor = CreateDisc("preview-floor", { radius: 0.75, tessellation: 40 }, scene);
    floor.rotation.x = Math.PI / 2;
    floor.position.y = -0.002;
    const mat = new StandardMaterial("preview-floor", scene);
    mat.diffuseColor = new Color3(0.2, 0.16, 0.13);
    mat.specularColor = Color3.Black();
    mat.alpha = 0.85;
    floor.material = mat;
    floor.isPickable = false;

    // The character faces +Z; start the camera in front of it.
    const cam = new ArcRotateCamera("preview-camera", Math.PI / 2, 1.38, DISTANCE, new Vector3(0, 1, 0), scene);
    cam.minZ = 0.05;
    cam.fov = 0.55;
    cam.lowerBetaLimit = 0.9;
    cam.upperBetaLimit = 1.75;
    cam.lowerRadiusLimit = 1.4;
    cam.upperRadiusLimit = 4.5;
    cam.wheelPrecision = 60;
    cam.panningSensibility = 0;
    cam.angularSensibilityX = 350;
    cam.inputs.removeByType("ArcRotateCameraKeyboardMoveInput");
    cam.attachControl(true);
    this.camera = cam;

    this.renderer = new CharacterRenderer(scene, { library: sharedLibrary() });
    this.frame(appearance);

    let last = performance.now();
    this.engine.runRenderLoop(() => {
      const now = performance.now();
      const dt = Math.min((now - last) / 1000, 0.1);
      last = now;
      this.renderer.begin();
      this.renderer.show(ID, this.version, this.appearance, 0, 0, 0, 0, this.anim, 0);
      this.renderer.update(dt, cam.position);
      // Ease the camera's look-at height to the character's (the height slider moves it).
      cam.target.y += (this.targetY - cam.target.y) * Math.min(1, dt * 8);
      scene.render();
    });
    this.resize = new ResizeObserver(() => this.engine.resize());
    this.resize.observe(canvas);
  }

  private frame(a: Appearance): void {
    const h = heightMetres(a);
    this.targetY = h * LOOK_AT;
    // Step back for tall characters so head and feet stay in the frame; a
    // zoom the player chose is kept until the height changes.
    if (Math.abs(h - this.framedHeight) > 0.005) {
      this.framedHeight = h;
      this.camera.radius = Math.min(this.camera.upperRadiusLimit ?? 5, h * DISTANCE_PER_M + DISTANCE_MARGIN);
    }
  }

  /** Shows `a` (assembled on the next frames). */
  setAppearance(a: Appearance): void {
    this.appearance = a;
    this.version++;
    this.frame(a);
  }

  /** Plays an animation, e.g. a wave to greet a new character. */
  setAnim(anim: Anim): void {
    this.anim = anim;
  }

  /** Turns the view to the character's front, side or back. */
  turnTo(alpha: number): void {
    this.camera.alpha = alpha;
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
