// The Owl Loft's owls (docs/architecture.md; castle_kit
// grid.py OWL_MARKER, OWL_PROP): the `owl_loft` cell exports one empty marker
// node per owl, `<cell>.owl_<k>` with `extras.kit_anim = { kind: "owl",
// phase }`, at its perch, facing as the owl faces. The owl itself is the
// Wings B prop `instances/props/owl.glb`, two meshes, `owl_body` and
// `owl_head` (the head's origin at the neck); every owl is drawn as thin
// instances of those two meshes (two draws for all of them), bobbing on its
// perch and turning its head now and then, each by its marker's phase.
//
// Until the prop exists a plain owl (two spheres in the palette's owl
// colours) stands in. Owls draw only while their cell is shown.

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import { BoundingInfo } from "@babylonjs/core/Culling/boundingInfo";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Matrix, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { CreateSphere } from "@babylonjs/core/Meshes/Builders/sphereBuilder";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import type { Scene } from "@babylonjs/core/scene";
import { audio } from "../../audio";
import { paletteRgb } from "./terrain";
import { toonifyContainer } from "../toon/convert";
import { owlPhaseOf } from "./nodes";
import { assetUrl } from "../../assets";

interface Owl {
  cell: string;
  x: number;
  y: number;
  z: number;
  /** Babylon `rotation.y` of the marker. */
  yaw: number;
  phase: number;
}

/** The bob (m) and the head's turn (radians) of an owl with `phase` at `t` (s). Pure. */
export function owlPose(t: number, phase: number): { bob: number; head: number } {
  const bob = 0.015 * Math.sin((2 * Math.PI * t) / 1.7 + phase);
  // Looks one way, holds, looks the other: a clipped slow sine.
  const s = Math.sin(t * 0.45 + phase * 1.7);
  const head = 1.1 * Math.max(-1, Math.min(1, s * 1.8));
  return { bob, head };
}

export class KitOwls {
  private owls: Owl[] = [];
  private body: Mesh | null = null;
  private head: Mesh | null = null;
  /** The neck's place in the prop (the head's origin). */
  private readonly neck = new Vector3(0, 0.3, 0);
  private buf = new Float32Array(16 * 16);
  private headBuf = new Float32Array(16 * 16);
  private disposed = false;
  private source = "loading";
  private drawn = 0;
  /** Sound: when an owl in view next calls (s, the update's clock). */
  private nextCall = 0;
  private readonly m = new Matrix();
  private readonly r = new Matrix();
  private readonly t = new Matrix();

  constructor(
    private readonly scene: Scene,
    assetsBase: string,
  ) {
    void this.load(assetUrl(`${assetsBase}instances/props/owl.glb`));
  }

  /** Owls wired (resident cells) and drawn last frame. */
  get counts(): { wired: number; drawn: number } {
    return { wired: this.owls.length, drawn: this.drawn };
  }

  /** A freshly loaded cell's transform nodes: its owl markers. */
  wire(cell: string, nodes: readonly TransformNode[]): number {
    let n = 0;
    for (const node of nodes) {
      const phase = owlPhaseOf(node);
      if (phase === null) continue;
      node.computeWorldMatrix(true);
      const p = node.getAbsolutePosition();
      // The marker's facing: where its +z points, as a Y turn.
      const f = Vector3.TransformNormal(Vector3.Forward(), node.getWorldMatrix());
      this.owls.push({ cell, x: p.x, y: p.y, z: p.z, yaw: Math.atan2(f.x, f.z), phase });
      n++;
    }
    if (n) this.fitBounds();
    return n;
  }

  unwire(cell: string): void {
    this.owls = this.owls.filter((o) => o.cell !== cell);
    this.fitBounds();
  }

  private fitBounds(): void {
    if (!this.body || !this.head || this.owls.length === 0) return;
    const min = new Vector3(Infinity, Infinity, Infinity);
    const max = new Vector3(-Infinity, -Infinity, -Infinity);
    for (const o of this.owls) {
      min.minimizeInPlaceFromFloats(o.x - 1, o.y - 1, o.z - 1);
      max.maximizeInPlaceFromFloats(o.x + 1, o.y + 1.5, o.z + 1);
    }
    for (const m of [this.body, this.head]) m.setBoundingInfo(new BoundingInfo(min, max));
  }

  private async load(url: string): Promise<void> {
    let body: Mesh | null = null;
    let head: Mesh | null = null;
    try {
      const c = await LoadAssetContainerAsync(url, this.scene, { pluginExtension: ".glb" });
      if (this.disposed) {
        c.dispose();
        return;
      }
      toonifyContainer(c, this.scene);
      c.addAllToScene();
      const parts = c.meshes.filter((m): m is Mesh => m instanceof Mesh && m.getTotalVertices() > 0);
      for (const m of parts) {
        m.computeWorldMatrix(true);
        const isHead = /head/i.test(m.name);
        if (isHead) this.neck.copyFrom(m.getAbsolutePosition());
        m.setParent(null);
        m.bakeCurrentTransformIntoVertices();
        if (isHead) {
          // Origin at the neck: the head turns about it.
          m.bakeTransformIntoVertices(Matrix.Translation(-this.neck.x, -this.neck.y, -this.neck.z));
          head = m;
        } else body = body ?? m;
      }
      for (const n of c.transformNodes) n.dispose();
      for (const m of c.meshes) if (m !== body && m !== head && !m.isDisposed()) m.dispose();
      this.source = body && head ? "owl.glb" : "placeholder";
    } catch {
      this.source = "placeholder";
    }
    if (this.disposed) {
      body?.dispose();
      head?.dispose();
      return;
    }
    if (!body || !head) {
      body?.dispose();
      head?.dispose();
      [body, head] = placeholderOwl(this.scene, this.neck);
    }
    for (const [m, name, buf] of [
      [body, "owls-body", this.buf],
      [head, "owls-head", this.headBuf],
    ] as const) {
      m.name = name;
      m.isPickable = false;
      m.doNotSyncBoundingInfo = true;
      m.thinInstanceSetBuffer("matrix", buf, 16, false);
      m.thinInstanceCount = 0;
      m.setEnabled(false);
    }
    this.body = body;
    this.head = head;
    this.fitBounds();
  }

  /** Once a frame: every owl of a shown cell bobs and looks about. */
  update(t: number, shown: (cell: string) => boolean): void {
    const shownCell = shown;
    const body = this.body;
    const head = this.head;
    if (!body || !head) return;
    let n = 0;
    for (const o of this.owls) {
      if (!shown(o.cell)) continue;
      if ((n + 1) * 16 > this.buf.length) this.grow();
      const p = owlPose(t, o.phase);
      // Body: turned to the marker's facing, at the perch, bobbing.
      Matrix.RotationYToRef(o.yaw, this.r);
      Matrix.TranslationToRef(o.x, o.y + p.bob, o.z, this.t);
      this.r.multiplyToRef(this.t, this.m);
      this.m.copyToArray(this.buf, n * 16);
      // Head: its own turn about the neck, then the body's.
      Matrix.RotationYToRef(p.head, this.r);
      Matrix.TranslationToRef(this.neck.x, this.neck.y, this.neck.z, this.t);
      this.r.multiplyToRef(this.t, this.r);
      this.r.multiplyToRef(this.m, this.r);
      this.r.copyToArray(this.headBuf, n * 16);
      n++;
    }
    this.drawn = n;
    // Sound: now and then, one of the owls in view hoots or shuffles its wings.
    if (n > 0 && t >= this.nextCall) {
      this.nextCall = t + 5 + Math.random() * 12;
      const shown = this.owls.filter((o) => shownCell(o.cell));
      const o = shown[Math.floor(Math.random() * shown.length)];
      if (o) audio.playAt(Math.random() < 0.6 ? "owl.hoot" : "owl.flutter", [o.x, o.y + 0.3, o.z]);
    } else if (n === 0) this.nextCall = t + 2;
    for (const m of [body, head]) {
      m.thinInstanceCount = n;
      m.setEnabled(n > 0);
      if (n > 0) m.thinInstanceBufferUpdated("matrix");
    }
  }

  private grow(): void {
    const b = new Float32Array(this.buf.length * 2);
    b.set(this.buf);
    this.buf = b;
    const h = new Float32Array(this.headBuf.length * 2);
    h.set(this.headBuf);
    this.headBuf = h;
    this.body?.thinInstanceSetBuffer("matrix", this.buf, 16, false);
    this.head?.thinInstanceSetBuffer("matrix", this.headBuf, 16, false);
  }

  perfLine(): string {
    return `owls ${this.drawn}/${this.owls.length} (${this.source})`;
  }

  dispose(): void {
    this.disposed = true;
    this.owls = [];
    this.body?.dispose();
    this.head?.dispose();
    this.body = null;
    this.head = null;
  }
}

/** Until the Wings B prop exists: a body and a head, spheres in the palette's owl browns. */
function placeholderOwl(scene: Scene, neck: Vector3): [Mesh, Mesh] {
  const brown = paletteRgb(["owl_brown", "oak"]) ?? [0.4, 0.3, 0.2];
  const pale = paletteRgb(["owl_pale", "canvas"]) ?? [0.8, 0.75, 0.65];
  const body = CreateSphere("owl-body", { diameterX: 0.22, diameterY: 0.3, diameterZ: 0.2, segments: 6 }, scene);
  body.position.y = 0.15;
  body.bakeCurrentTransformIntoVertices();
  const head = CreateSphere("owl-head", { diameter: 0.17, segments: 6 }, scene);
  head.position.y = 0.06;
  head.bakeCurrentTransformIntoVertices();
  neck.set(0, 0.3, 0);
  const mat = new StandardMaterial("owl-placeholder", scene);
  mat.diffuseColor = Color3.White();
  mat.specularColor = Color3.Black();
  for (const [m, rgb] of [
    [body, brown],
    [head, pale],
  ] as const) {
    const n = m.getTotalVertices();
    const colours = new Float32Array(n * 4);
    for (let i = 0; i < n; i++) colours.set([rgb[0], rgb[1], rgb[2], 1], i * 4);
    m.setVerticesData(VertexBuffer.ColorKind, colours);
    m.useVertexColors = true;
    m.material = mat;
  }
  return [body, head];
}
