// Third-person camera: orbits the player with a mouse drag (left or right
// button) or the arrow keys (game.ts: `turn` and `tilt`), zooms with the wheel, and pulls in when a wall is between it and
// the player so the view never ends up inside masonry. In small rooms it
// rises over the shoulder instead of pulling in tight (camera-fit.ts).
// How far the wheel may zoom out depends on the cell (castle/camera-max.ts:
// 24 m in the big halls, 20 m in common rooms, 16 m elsewhere, 18 m at most
// on Low); walking into a smaller cell pulls the camera in smoothly.
//
// The walls it keeps in front of are the camera colliders of kitted cells
// and every mesh of the other cells (cells.ts `cameraMeshes`). A collider is
// the Planner's flat wall and ceiling faces, and the kit's mouldings stand
// proud of them, so the camera treats a collider face as if it stood that
// relief further into the room (KIT_WALL_RELIEF, KIT_CEILING_RELIEF).

import { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import { Ray } from "@babylonjs/core/Culling/ray";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { Scene } from "@babylonjs/core/scene";
import type { FloatArray, IndicesArray } from "@babylonjs/core/types";
import { betaAboveFloor, clampLift, FLIGHT_DISTANCE, FLIGHT_FLOOR_CLEARANCE, FLIGHT_FOLLOW_RATE, liftFor } from "./camera-fit";

/** The floor under a point for a flyer at height `y` (`SimWorld.floorAt`), undefined outside the airspace. */
export type FloorAt = (x: number, z: number, y: number) => number | undefined;
import { ABSOLUTE_MAX_DISTANCE, DEFAULT_DISTANCE, ZoomLimit } from "./castle/camera-max";
import { isCameraCollider } from "./castle/nodes";
import type { FlightBounds } from "./flight-bounds";

/** Height of the orbit target above the feet (m): about the shoulders. */
export const TARGET_HEIGHT = 1.45;
/**
 * Gap kept between the camera and a wall it was pulled in by (m): the
 * camera's collision radius. With the near plane at 5 cm this keeps walls
 * from clipping while letting the camera use more of a small room.
 */
const WALL_MARGIN = 0.2;
const NEAR_PLANE = 0.05;
/** Closest a wall may pull the camera to the shoulders (m); the lift does the rest. */
const MIN_PULL = 0.5;
/** How fast the over-the-shoulder lift follows the room (1/s). */
const LIFT_RATE = 5;
/**
 * Kitted cells (docs/art/castle-kit.md "Coordinates, grid and scale", "glTF conventions"): how far kit
 * geometry stands proud of a collider's wall faces (m): shafts 0.25 m
 * (the intrusion limit), plus the near plane. Not more than the player's
 * 0.3 m radius, so a ray heading away from a wall the player touches never
 * starts behind the moved face.
 */
export const KIT_WALL_RELIEF = 0.3;
/** ... and below a collider's ceiling faces (m): the 0.7 m cornice and the ribs, plus a margin. */
export const KIT_CEILING_RELIEF = 0.8;
/** How much further than the zoom the rays reach, for collider faces moved by their relief (m). */
const KIT_REACH = 3;

export class FollowCamera {
  readonly camera: ArcRotateCamera;
  /** Zoom chosen with the wheel and the cell's limit; the actual distance is shorter when a wall is in the way. */
  private readonly zoom = new ZoomLimit();
  private distance = DEFAULT_DISTANCE;
  /** Pitch added in tight spots (radians), on top of the player's own. */
  private lift = 0;
  private readonly ray = new Ray(Vector3.Zero(), Vector3.Forward(), 1);
  // Scratch vectors for a collider face's normal (no allocation per frame).
  private readonly e1 = new Vector3();
  private readonly e2 = new Vector3();
  private readonly normal = new Vector3();
  /**
   * Each collider's positions and indices, read once: `getVerticesData`
   * makes a new typed-array view of the glTF buffer on every call, and the
   * rays run every frame.
   */
  private readonly colliderData = new WeakMap<AbstractMesh, { pos: FloatArray; ind: IndicesArray } | null>();
  /** The flight camera's bounds this frame (null on foot). */
  private bounds: FlightBounds | null = null;
  private readonly target = new Vector3();
  private fresh = true;
  private readonly onWheel: (e: WheelEvent) => void;

  constructor(
    scene: Scene,
    private readonly canvas: HTMLCanvasElement,
    farPlane: number,
  ) {
    // alpha/beta: Babylon's spherical angles around the target. beta 1.2 rad looks slightly down.
    const cam = new ArcRotateCamera("follow", Math.PI / 2, 1.2, this.distance, Vector3.Zero(), scene);
    cam.minZ = NEAR_PLANE;
    cam.maxZ = farPlane;
    cam.fov = 0.9;
    cam.lowerBetaLimit = 0.35;
    cam.upperBetaLimit = 1.62; // a little below the horizon, to look up at vaulted ceilings
    cam.lowerRadiusLimit = 0.5;
    cam.upperRadiusLimit = ABSOLUTE_MAX_DISTANCE;
    cam.panningSensibility = 0;
    cam.inertia = 0.55;
    cam.angularSensibilityX = 450;
    cam.angularSensibilityY = 450;
    // Babylon's own arrow keys are off: game.ts turns the camera with them only when no control
    // or spell targeting has them (input.ts). The wheel is handled below (zoom vs. wall pull-in).
    cam.inputs.removeByType("ArcRotateCameraKeyboardMoveInput");
    cam.inputs.removeByType("ArcRotateCameraMouseWheelInput");
    cam.attachControl(true);
    const pointers = cam.inputs.attached.pointers as unknown as { buttons: number[] } | undefined;
    if (pointers) pointers.buttons = [0, 2];
    this.camera = cam;

    this.onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const step = Math.sign(e.deltaY) * Math.min(Math.abs(e.deltaY) / 100, 3);
      this.zoom.wheel(step);
    };
    canvas.addEventListener("wheel", this.onWheel, { passive: false });
    canvas.addEventListener("contextmenu", preventDefault);
  }

  /** The furthest zoom of the cell the player is in (castle/camera-max.ts `cameraMaxFor`); eased in or out. */
  setMaxDistance(max: number): void {
    this.zoom.setTarget(max);
  }

  /** The furthest zoom applied now (m), for the HUD and tests. */
  get maxDistance(): number {
    return this.zoom.limit;
  }

  /** Horizontal direction the camera looks along, for camera-relative movement. */
  lookDirection(): [number, number] {
    const p = this.camera.position;
    return [this.target.x - p.x, this.target.z - p.z];
  }

  /** Places the camera behind a player facing `yaw` (forward = (sin yaw, cos yaw)). */
  faceAlong(yaw: number): void {
    // ArcRotateCamera sits at target + r·(cos α sin β, cos β, sin α sin β); behind means opposite forward.
    this.camera.alpha = Math.atan2(-Math.cos(yaw), -Math.sin(yaw));
  }

  /**
   * Swings the camera round the player by `dAlpha` radians (A / D
   * on a broom: the broom follows the camera's view). Positive turns the
   * view to the right.
   */
  turn(dAlpha: number): void {
    this.camera.alpha += dAlpha;
  }

  /**
   * Tilts the camera by `dBeta` radians within its pitch limits (Up / Down
   * arrows). Positive looks up: the camera sinks toward the horizon, as
   * dragging the mouse up does.
   */
  tilt(dBeta: number): void {
    const cam = this.camera;
    cam.beta = Math.min(cam.upperBetaLimit ?? Math.PI, Math.max(cam.lowerBetaLimit ?? 0, cam.beta + dBeta));
  }

  /**
   * Follows the player's feet at (x, y, z). `walls` are the world meshes the
   * camera must stay in front of. `flight` (on a broom) sits the
   * camera further back, follows more smoothly and keeps it over the floor
   * under it (`SimWorld.floorAt`: the ground or the lake), never inside it.
   * And inside the zone's open sky, out of its no-fly boxes
   * (`bounds`, flight-bounds.ts: the castle's outer walls from the grounds,
   * the Flying class field's walls and towers).
   */
  update(x: number, y: number, z: number, dt: number, walls: Iterable<AbstractMesh>, flight: FloorAt | null = null, bounds: FlightBounds | null = null): void {
    this.bounds = flight ? bounds : null;
    const t = this.target;
    const ty = y + TARGET_HEIGHT;
    const jump = (t.x - x) ** 2 + (t.y - ty) ** 2 + (t.z - z) ** 2;
    if (this.fresh || jump > 25) {
      // First frame, a Correction or a respawn: cut, don't swoop.
      t.set(x, ty, z);
      this.fresh = false;
    } else {
      // Smooth the 1-frame steps of movement a little (critically damped follow); a broom more so.
      const follow = 1 - Math.exp(-dt * (flight ? FLIGHT_FOLLOW_RATE : 18));
      t.x += (x - t.x) * follow;
      t.y += (ty - t.y) * follow;
      t.z += (z - t.z) * follow;
    }
    const cam = this.camera;
    cam.target.copyFrom(t);
    const zoom = this.zoom.update(dt);
    const want = flight ? Math.max(zoom, Math.min(FLIGHT_DISTANCE, this.zoom.limit)) : zoom;
    if (flight) {
      // Over the floor under where the camera would sit (a hill behind, the lake below).
      const sb = Math.sin(cam.beta);
      const cx = t.x + Math.cos(cam.alpha) * sb * want;
      const cz = t.z + Math.sin(cam.alpha) * sb * want;
      // Asked from the camera's highest possible spot, so a hill rising behind the broom (flying
      // downhill near the grass) is found too, not only ground below the broom's own height.
      const floor = flight(cx, cz, t.y + want) ?? flight(x, z, y);
      if (floor !== undefined) cam.beta = betaAboveFloor(t.y, want, cam.beta, floor, FLIGHT_FLOOR_CLEARANCE, cam.lowerBetaLimit ?? 0);
    }

    // The pitch the player chose (drags change `cam.beta`; the lift is ours).
    const beta = cam.beta + this.lift;
    // In a tight spot, rise over the shoulder rather than pull in close
    // (camera-fit.ts). The free space straight back decides how much.
    // Only a wall counts: a player who zoomed in close chose that view.
    const back = this.room(t, cam.alpha, beta, want, walls);
    const walled = back < want - 0.01;
    const wantLift = walled ? clampLift(liftFor(back), beta, cam.lowerBetaLimit ?? 0) : 0;
    this.lift += (wantLift - this.lift) * (1 - Math.exp(-dt * LIFT_RATE));
    cam.beta = beta - this.lift;
    const limit = this.lift > 0.001 ? this.room(t, cam.alpha, cam.beta, want, walls) : back;
    // Pull in immediately (never show the inside of a wall), ease back out.
    this.distance = limit < this.distance ? limit : this.distance + (limit - this.distance) * (1 - Math.exp(-dt * 4));
    cam.radius = this.distance;
  }

  /** How far the camera may sit from `target` along (`alpha`, `beta`), up to `want`, before a wall (m). */
  private room(target: Vector3, alpha: number, beta: number, want: number, walls: Iterable<AbstractMesh>): number {
    const sb = Math.sin(beta);
    this.ray.direction.set(Math.cos(alpha) * sb, Math.cos(beta), Math.sin(alpha) * sb);
    this.ray.origin.copyFrom(target);
    this.ray.length = want + WALL_MARGIN + KIT_REACH;
    let limit = want;
    if (this.bounds) {
      const b = this.bounds.room(target.x, target.y, target.z, this.ray.direction.x, this.ray.direction.y, this.ray.direction.z, want);
      if (b < limit) limit = Math.max(MIN_PULL, b);
    }
    for (const mesh of walls) {
      const hit = this.ray.intersectsMesh(mesh, false);
      if (!hit.hit) continue;
      const d = isCameraCollider(mesh) ? hit.distance - this.kitInset(mesh, hit.faceId) : hit.distance;
      if (d - WALL_MARGIN < limit) limit = Math.max(MIN_PULL, d - WALL_MARGIN);
    }
    return limit;
  }

  /**
   * How much nearer than a collider face the ray must stop (m): the kit's
   * relief over that face, measured along the ray (relief / cos of the angle
   * to the face's normal), so the camera keeps the relief clear of the face
   * whatever the angle. Walls get KIT_WALL_RELIEF, faces above the ray's
   * origin KIT_CEILING_RELIEF, floors nothing. Either winding works.
   */
  private kitInset(mesh: AbstractMesh, faceId: number): number {
    let data = this.colliderData.get(mesh);
    if (data === undefined) {
      const ind = mesh.getIndices();
      const pos = mesh.getVerticesData(VertexBuffer.PositionKind);
      data = ind && pos ? { pos, ind } : null;
      this.colliderData.set(mesh, data);
    }
    if (!data) return 0;
    const { pos, ind } = data;
    if (faceId < 0 || faceId * 3 + 2 >= ind.length) return 0;
    const a = ind[faceId * 3]! * 3;
    const b = ind[faceId * 3 + 1]! * 3;
    const c = ind[faceId * 3 + 2]! * 3;
    this.e1.set(pos[b]! - pos[a]!, pos[b + 1]! - pos[a + 1]!, pos[b + 2]! - pos[a + 2]!);
    this.e2.set(pos[c]! - pos[a]!, pos[c + 1]! - pos[a + 1]!, pos[c + 2]! - pos[a + 2]!);
    Vector3.CrossToRef(this.e1, this.e2, this.normal);
    Vector3.TransformNormalToRef(this.normal, mesh.getWorldMatrix(), this.normal);
    const len = this.normal.length();
    if (len < 1e-9) return 0;
    const ny = this.normal.y / len;
    const cos = Math.abs(Vector3.Dot(this.normal, this.ray.direction)) / len;
    if (cos < 1e-3) return 0;
    const relief = Math.abs(ny) < 0.5 ? KIT_WALL_RELIEF : this.ray.direction.y > 0 ? KIT_CEILING_RELIEF : 0;
    return relief / cos;
  }

  setFarPlane(far: number): void {
    this.camera.maxZ = far;
  }

  dispose(): void {
    this.canvas.removeEventListener("wheel", this.onWheel);
    this.canvas.removeEventListener("contextmenu", preventDefault);
    this.camera.detachControl();
    this.camera.dispose();
  }
}

function preventDefault(e: Event): void {
  e.preventDefault();
}
