// The moving staircases in the running game (docs/architecture.md).
// Three Grand Staircase flights swing between two landings on
// one schedule every end computes from the server's clock: no message says
// a staircase moved. Each frame:
//
// - `SimWorld.stairState(serverNow)` gives the walk state. When it changes,
//   a player standing on a switched flight is carried (`SimWorld.carry`, the
//   server's own function: the server carries it at the same instant and
//   sends no correction), then the castle world predicts in the new state
//   (`setStairState`) and the Monstra Viam trail plans in it.
// - `SimWorld.flightStates` gives each flight's phase and turn: the swing
//   node (`<cell>.castle_props.swing_<flight>`, modelled at A, castle/nodes.ts)
//   is turned about its pivot (its camera collider, if it has one, with it);
//   riders (us and the others) are drawn turned with it; our input is held
//   while we stand on a swinging flight. During the 5-second warning the
//   flight shudders, dust falls and, for players in the Grand Staircase near
//   that flight (within 8 m of either position), the chat says "The
//   staircase is moving!".
// - The landing gates (stairs.json `gates`) are stretches of the staircase's
//   own balustrade, thin instances of the Wings A prop
//   `instances/props/stair_rail.glb` (one draw, the castle's shared trim):
//   shut by `flightStates[].gates[].open`, a rail is conjured in from its
//   middle under a cloud of smoke (castle/rail-smoke.ts) and a soft poof;
//   opening, it vanishes the same way. Nothing swings.
//
// The schedule calls are cheap, but `flightStates` builds objects, so it is
// read every frame only around a swing (from the warning to the gates
// settling after the switch) and four times a second otherwise.

import { LoadAssetContainerAsync } from "@babylonjs/core/Loading/sceneLoader";
import "@babylonjs/loaders/glTF/2.0/glTFLoader";
import { BoundingInfo } from "@babylonjs/core/Culling/boundingInfo";
import { Matrix, Quaternion, Vector3 } from "@babylonjs/core/Maths/math.vector";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import "@babylonjs/core/Meshes/thinInstanceMesh";
import type { AbstractMesh } from "@babylonjs/core/Meshes/abstractMesh";
import type { TransformNode } from "@babylonjs/core/Meshes/transformNode";
import type { Scene } from "@babylonjs/core/scene";
import { audio, type LoopHandle } from "../../audio";
import type { FlightView, SimWorld, StairFlightDef, StairGateDef } from "../../wasm/sim_wasm";
import { toonifyContainer } from "../toon/convert";
import { cameraAxes, SpriteLayer } from "../effects/billboards";
import type { CastleLibrary } from "./materials";
import { RailSmoke } from "./rail-smoke";
import { swingAnimOf } from "./nodes";
import { entersSwinging, warnsNear, flightGeoms, gateRail, onStair, railShown, riderTurn, shudder, stairHeightAt, StairWatch, turnPose, type FlightGeom, type RailPose, type StairEvents, type StairSurface } from "./stairs-state";
import { assetUrl } from "../../assets";

/** The landing rails' prop (grid.py RAIL_PROP) and its one material (the castle's shared trim). */
const RAIL_PROP = "stair_rail";
const RAIL_MATERIAL = "castle_trim";

/** The Grand Staircase's cells: their players hear the warning, and the gates draw while one is shown. */
export const GRAND_STAIRCASE_CELLS: ReadonlySet<string> = new Set(["grand_staircase", "grand_staircase_upper"]);
/** The chat line of the warning. */
export const WARNING_LINE = "The staircase is moving!";
/** Reading `flightStates` this often while nothing moves (ms). */
const IDLE_READ_MS = 250;

type Node = AbstractMesh | TransformNode;

// ------------------------------------------------------------------ swing nodes

interface SwingMesh {
  mesh: AbstractMesh;
  /** Its world matrix as loaded (position A). */
  base: Matrix;
  /** The matrix it is drawn with (assigned to the mesh once, written in place). */
  world: Matrix;
}

interface Swing {
  cell: string;
  flight: string;
  meshes: SwingMesh[];
}

/** The swing nodes of the resident cells (wired by cells.ts like the candles and the House Cup's sand). */
export class KitSwings {
  private swings: Swing[] = [];
  private readonly turn = new Matrix();
  private readonly scratch = new Matrix();
  /** The turn each flight was last drawn at (radians), to skip still frames. */
  private readonly drawn = new Map<string, number>();

  /** Swing nodes wired (resident cells). */
  get count(): number {
    return this.swings.length;
  }

  /** The flights with a swing node now. */
  flights(): Set<string> {
    return new Set(this.swings.map((s) => s.flight));
  }

  /** A freshly loaded cell's meshes and transform nodes (after cells.ts froze them). Returns how many swing nodes. */
  wire(cell: string, nodes: readonly Node[]): number {
    let n = 0;
    for (const node of nodes) {
      const a = swingAnimOf(node);
      if (!a) continue;
      const own = "getTotalVertices" in node ? [node as AbstractMesh] : [];
      const meshes: SwingMesh[] = [];
      for (const m of [...own, ...node.getChildMeshes(false)]) {
        m.computeWorldMatrix(true);
        const base = m.getWorldMatrix().clone();
        const world = base.clone();
        // It moves: its bounds follow it (frustum culling and the camera's rays).
        m.doNotSyncBoundingInfo = false;
        m.freezeWorldMatrix(world);
        meshes.push({ mesh: m, base, world });
      }
      this.swings.push({ cell, flight: a.flight, meshes });
      this.drawn.delete(a.flight);
      n++;
    }
    return n;
  }

  /** Forgets a cell's swing nodes (it is being unloaded). */
  unwire(cell: string): void {
    this.swings = this.swings.filter((s) => s.cell !== cell);
  }

  /**
   * Turns flight `flight`'s node by `rad` about the vertical through
   * `pivot` (the layout's sense, which is Babylon's `RotationY` on the
   * scene's numbers): world = base · T(-p) · R(rad) · T(p).
   */
  setTurn(flight: string, pivot: readonly [number, number], rad: number): void {
    if (this.drawn.get(flight) === rad) return;
    this.drawn.set(flight, rad);
    Matrix.TranslationToRef(-pivot[0], 0, -pivot[1], this.turn);
    Matrix.RotationYToRef(rad, this.scratch);
    this.turn.multiplyToRef(this.scratch, this.turn);
    Matrix.TranslationToRef(pivot[0], 0, pivot[1], this.scratch);
    this.turn.multiplyToRef(this.scratch, this.turn);
    for (const s of this.swings) {
      if (s.flight !== flight) continue;
      for (const m of s.meshes) {
        m.base.multiplyToRef(this.turn, m.world);
        m.mesh.freezeWorldMatrix(m.world);
      }
    }
  }

  dispose(): void {
    this.swings = [];
    this.drawn.clear();
  }
}

// ------------------------------------------------------------------ gates

/** Puffs the rails' smoke holds at once (every gate of a flight at once, twice over). */
const SMOKE_CAPACITY = 220;

/**
 * Every landing gate as a stretch of the staircase's own balustrade: thin
 * instances of one mesh (`instances/props/stair_rail.glb`, its castle_trim
 * bound to the castle's shared material: one draw). Shut, the rail stands
 * across the landing edge; open, it is gone. Between, it is conjured from
 * (or vanishes into) its middle under a cloud of smoke and a soft poof.
 */
export class StairGates {
  private mesh: Mesh | null = null;
  private matrices: Float32Array;
  private readonly gates: { gate: StairGateDef; flight: number; gateIndex: number }[] = [];
  /** Each gate's `open` as last drawn (-1: not yet, or not since the gates were last shown). */
  private readonly opens: number[];
  private drawn = 0;
  private dirty = true;
  private shown = false;
  private disposed = false;
  private source = "loading";
  private trimTaken = false;
  private readonly pose: RailPose = { x: 0, y: 0, z: 0, rotY: 0, sx: 1, s: 1 };
  private readonly q = new Quaternion();
  private readonly m = new Matrix();
  private readonly sv = new Vector3();
  private readonly p = new Vector3();
  private readonly bounds: { min: Vector3; max: Vector3 } | null;
  private readonly smoke = new RailSmoke(SMOKE_CAPACITY);
  private readonly puffs: SpriteLayer | null;
  private readonly camRight = new Vector3();
  private readonly camUp = new Vector3();
  private lastMs = -1;

  constructor(
    private readonly scene: Scene,
    assetsBase: string,
    flights: readonly StairFlightDef[],
    private readonly library?: CastleLibrary,
  ) {
    flights.forEach((f, flight) => f.gates.forEach((gate, gateIndex) => this.gates.push({ gate, flight, gateIndex })));
    this.matrices = new Float32Array(Math.max(1, this.gates.length) * 16);
    this.opens = this.gates.map(() => -1);
    // Every rail stays on its line: one box round them all (the instances' bounds).
    if (this.gates.length) {
      const min = new Vector3(Infinity, Infinity, Infinity);
      const max = new Vector3(-Infinity, -Infinity, -Infinity);
      for (const { gate } of this.gates) {
        for (const h of gate.line) {
          min.minimizeInPlaceFromFloats(h[0] - 0.5, gate.y - 0.5, h[1] - 0.5);
          max.maximizeInPlaceFromFloats(h[0] + 0.5, gate.y + 1.5, h[1] + 0.5);
        }
      }
      this.bounds = { min, max };
      this.puffs = new SpriteLayer(scene, "stair-smoke", { shape: "puff", blend: "alpha", capacity: SMOKE_CAPACITY });
      void this.load(assetUrl(`${assetsBase}instances/props/${RAIL_PROP}.glb`));
    } else {
      this.bounds = null;
      this.puffs = null;
    }
  }

  private async load(url: string): Promise<void> {
    let mesh: Mesh | null = null;
    try {
      const [c] = await Promise.all([LoadAssetContainerAsync(url, this.scene, { pluginExtension: ".glb" }), this.library?.ready()]);
      if (this.disposed) {
        c.dispose();
        return;
      }
      // The rail's castle_trim is the castle's shared material (one reference, released on dispose).
      const lib = this.library;
      toonifyContainer(c, this.scene, undefined, {
        shared: lib && {
          take: (name, placeholder) => {
            if (name !== RAIL_MATERIAL || this.trimTaken) return null;
            const mat = lib.take(name, placeholder);
            this.trimTaken = mat !== null;
            return mat;
          },
        },
      });
      c.addAllToScene();
      const parts = c.meshes.filter((m): m is Mesh => m instanceof Mesh && m.getTotalVertices() > 0);
      for (const m of parts) {
        m.computeWorldMatrix(true);
        m.setParent(null);
        m.bakeCurrentTransformIntoVertices();
      }
      mesh = parts.length > 1 ? Mesh.MergeMeshes(parts, true, true, undefined, false, true) : (parts[0] ?? null);
      for (const n of c.transformNodes) n.dispose();
      for (const m of c.meshes) if (m !== mesh && !m.isDisposed()) m.dispose();
      this.source = mesh ? `${RAIL_PROP}.glb` : "missing";
    } catch {
      this.source = "missing";
    }
    if (this.disposed || !mesh) {
      mesh?.dispose();
      return;
    }
    mesh.name = "stair-rails";
    mesh.isPickable = false;
    mesh.thinInstanceSetBuffer("matrix", this.matrices, 16, false);
    mesh.thinInstanceCount = 0;
    if (this.bounds) {
      mesh.setBoundingInfo(new BoundingInfo(this.bounds.min, this.bounds.max));
      mesh.doNotSyncBoundingInfo = true;
    }
    mesh.setEnabled(this.shown);
    this.mesh = mesh;
    this.dirty = true;
  }

  /** Shows the rails (the Grand Staircase is in view) or hides them (and their smoke). */
  setShown(on: boolean): void {
    if (on === this.shown) return;
    this.shown = on;
    this.mesh?.setEnabled(on);
    if (!on) {
      // Back in view, the gates are drawn as they are then, without a cloud for what changed meanwhile.
      this.opens.fill(-1);
      this.smoke.clear();
      this.puffs?.begin();
      this.puffs?.end(this.camRight, this.camUp);
      this.lastMs = -1;
    }
  }

  /**
   * Once a frame while shown: stands each rail as far as its gate is shut now
   * (`flightStates`), blooms a cloud (and a poof) where a gate starts to
   * open or shut, and moves the smoke. Rewrites the rails' buffer only when one moved.
   */
  update(views: readonly FlightView[], nowMs = performance.now()): void {
    for (let i = 0; i < this.gates.length; i++) {
      const g = this.gates[i]!;
      const open = views[g.flight]?.gates[g.gateIndex]?.open ?? 1;
      const was = this.opens[i]!;
      if (open === was) continue;
      if ((was === 0 || was === 1) && open !== was) this.conjure(g.gate);
      this.opens[i] = open;
      this.dirty = true;
    }
    if (this.dirty && this.mesh) {
      this.dirty = false;
      let n = 0;
      for (let i = 0; i < this.gates.length; i++) {
        const shown = railShown(Math.max(0, this.opens[i]!));
        if (shown <= 0.001) continue;
        gateRail(this.gates[i]!.gate, shown, this.pose);
        Quaternion.RotationYawPitchRollToRef(this.pose.rotY, 0, 0, this.q);
        this.sv.set(this.pose.sx, this.pose.s, this.pose.s);
        this.p.set(this.pose.x, this.pose.y, this.pose.z);
        Matrix.ComposeToRef(this.sv, this.q, this.p, this.m);
        this.m.copyToArray(this.matrices, n * 16);
        n++;
      }
      this.drawn = n;
      this.mesh.thinInstanceCount = n;
      this.mesh.thinInstanceBufferUpdated("matrix");
    }
    this.smokeFrame(nowMs);
  }

  /** A gate starts to open or shut: its cloud and its poof. */
  private conjure(gate: StairGateDef): void {
    const [[x0, z0], [x1, z1]] = gate.line;
    this.smoke.burst(x0, z0, x1, z1, gate.y);
    audio.playAt("stairs.gate", [(x0 + x1) / 2, gate.y + 0.8, (z0 + z1) / 2]);
  }

  private smokeFrame(nowMs: number): void {
    const dt = this.lastMs < 0 ? 0 : Math.min(0.1, (nowMs - this.lastMs) / 1000);
    this.lastMs = nowMs;
    if (!this.puffs || (this.smoke.count === 0 && this.puffs.size === 0)) return;
    this.smoke.step(dt);
    const cam = this.scene.activeCamera;
    if (!cam) return;
    cameraAxes(cam, this.camRight, this.camUp);
    this.puffs.begin();
    this.smoke.draw(this.puffs);
    this.puffs.end(this.camRight, this.camUp);
  }

  /** How open each gate is drawn (tests, the F3 overlay). */
  get gateOpens(): readonly number[] {
    return this.opens;
  }

  perfLine(): string {
    return `rails ${this.drawn}/${this.gates.length} standing (${this.source}, ${this.shown ? "1 draw" : "hidden"}), smoke ${this.smoke.count}`;
  }

  dispose(): void {
    this.disposed = true;
    this.mesh?.dispose();
    this.mesh = null;
    this.puffs?.dispose();
    if (this.trimTaken) this.library?.release(RAIL_MATERIAL);
    this.trimTaken = false;
  }
}

// ------------------------------------------------------------------ the controller

/** A character pose the staircases may turn (ours or a remote's, drawn this frame). */
export interface TurnablePose {
  x: number;
  y: number;
  z: number;
  yaw: number;
}

export interface StaircaseHooks {
  /** A rider was carried: put the predicted player there. */
  carried(pos: ArrayLike<number>, yaw: number): void;
  /** The warning began for players in the Grand Staircase. */
  warn(text: string): void;
  /** Dust at a point (the existing spell VFX). */
  dust(x: number, y: number, z: number): void;
  /** The frame's events, for the Monstra Viam trail (`riding`: we stand on a swinging flight). */
  trail(e: StairEvents, riding: boolean): void;
}

/** stairs.json's timings the client needs besides the schedule (ms). */
interface Timings {
  swingMs: number;
  warningMs: number;
  gateMs: number;
}

function timingsOf(json: string): Timings {
  try {
    const j = JSON.parse(json) as { swing_s?: number; warning_s?: number; gate_s?: number };
    return { swingMs: (j.swing_s ?? 6) * 1000, warningMs: (j.warning_s ?? 5) * 1000, gateMs: (j.gate_s ?? 1) * 1000 };
  } catch {
    return { swingMs: 6000, warningMs: 5000, gateMs: 1000 };
  }
}

/**
 * The development period (`?stairsPeriod=20`, as the server's
 * `SHS_STAIRS_PERIOD_S` and the load test's `--stairs-period-s`): honoured
 * by a development build or on this machine only.
 */
export function devStairsPeriod(search: string, dev: boolean, host: string): number | undefined {
  if (!dev && host !== "localhost" && host !== "127.0.0.1") return undefined;
  const v = Number(new URLSearchParams(search).get("stairsPeriod"));
  return Number.isInteger(v) && v > 0 ? v : undefined;
}

export class Staircases {
  readonly swings = new KitSwings();
  readonly gates: StairGates | null;
  private readonly geoms: FlightGeom[];
  private readonly watch = new StairWatch();
  private readonly timings: Timings;
  private views: FlightView[] = [];
  /** Sound: the grinding loop of each flight swinging now (by bit). */
  private readonly grinding = new Map<number, LoopHandle>();
  private nextRead = 0;
  private live = false;
  private riding = false;
  /** Turn of each flight's A model now (radians, without the shudder): riders turn with it. */
  private readonly turns: number[];
  private readonly scratch: [number, number] = [0, 0];
  private readonly dustAt: [number, number, number][] = [];
  private switches = 0;
  private carries = 0;
  private readonly enabled: boolean;

  constructor(
    scene: Scene,
    assetsBase: string,
    private readonly sim: SimWorld,
    surfaces: readonly StairSurface[],
    stairsJson: string,
    periodS: number | undefined,
    private readonly hooks: StaircaseHooks,
    library?: CastleLibrary,
  ) {
    let defs: StairFlightDef[] = [];
    try {
      sim.setStairs(stairsJson, periodS ?? null);
      defs = sim.stairFlights();
    } catch (e) {
      console.error("The moving staircases are unavailable:", e);
    }
    this.enabled = defs.length > 0;
    this.geoms = flightGeoms(defs, surfaces);
    this.turns = defs.map(() => 0);
    this.timings = timingsOf(stairsJson);
    this.gates = this.enabled ? new StairGates(scene, assetsBase, defs, library) : null;
  }

  /** Whether we stood on a swinging flight at the last frame (our input is held). */
  get ridingSwing(): boolean {
    return this.riding;
  }

  /** Each flight's walk state now (for the F3 overlay and tests). */
  get flightViews(): readonly FlightView[] {
    return this.views;
  }

  /** The moving flights' geometry (bit order). */
  get flights(): readonly FlightGeom[] {
    return this.geoms;
  }

  /**
   * Once a frame, before the player moves. `unixMs` is the server's time
   * (`ServerClock.now()`), `player` our predicted feet in the castle (null
   * in a room: nobody is carried, the state still follows), `gsShown`
   * whether a Grand Staircase cell is shown, `inGs` whether we stand in one.
   */
  update(unixMs: number, player: TurnablePose | null, gsShown: boolean, inGs: boolean): void {
    if (!this.enabled) return;
    const state = this.sim.stairState(unixMs);
    const changedState = state !== this.sim.currentStairState;
    if (changedState || this.live || unixMs >= this.nextRead) {
      this.views = this.sim.flightStates(unixMs);
      this.nextRead = unixMs + IDLE_READ_MS;
      const t = this.timings;
      // Read every frame from the warning to the gates settling after the switch.
      this.live = this.views.some((v) => v.phase === "swing" || v.warning || unixMs < v.lastSwitchMs + t.gateMs + 100 || unixMs >= v.nextSwitchMs - t.swingMs - t.warningMs - 100);
    }
    const e = this.watch.update(state, this.views);
    if (e.changed) {
      this.switches += e.switched.length;
      for (const s of e.switched) {
        if (!player) continue;
        const to = this.sim.carry(player.x, player.y, player.z, s.flight, s.from, s.to);
        if (!to) continue;
        const g = this.geoms.find((x) => x.bit === s.flight);
        const turn = g ? (s.to === "b" ? g.angle : -g.angle) : 0;
        player.x = to[0]!;
        player.y = to[1]!;
        player.z = to[2]!;
        player.yaw += turn;
        this.hooks.carried(to, player.yaw);
        this.carries++;
      }
    }
    if (e.first || e.changed) this.sim.setStairState(state);

    // The flights' turns, the swing nodes and the gates.
    for (const g of this.geoms) {
      const v = this.views[g.bit];
      const deg = v?.turnDeg ?? 0;
      this.turns[g.bit] = (deg * Math.PI) / 180;
      const quake = v ? shudder(v, unixMs, this.timings.swingMs, this.timings.warningMs) : 0;
      this.swings.setTurn(g.id, g.pivot, this.turns[g.bit]! + quake);
    }
    this.gates?.setShown(gsShown);
    if (gsShown) this.gates?.update(this.views);

    this.sounds(e, gsShown);

    // The warning: dust where each warned flight rests, and once, the chat line.
    if (e.warningStarted || e.swingStarted) {
      for (const g of this.geoms) {
        if (!((e.warningStarted | e.swingStarted) & (1 << g.bit))) continue;
        if (gsShown) for (const p of this.dustPoints(g)) this.hooks.dust(p[0], p[1], p[2]);
      }
      // The chat line only for players by a warned flight (the whole well would hear one a minute).
      if (e.warningStarted && inGs && player && warnsNear(this.geoms, e.warningStarted, player.x, player.y, player.z)) this.hooks.warn(WARNING_LINE);
    }

    this.riding = !!player && this.onSwinging(player);
    this.hooks.trail(e, this.riding);
  }

  /**
   * Sound, while the Grand Staircase is in view: a flight shudders (its
   * warning), grinds while it swings (a loop at its pivot) and settles at
   * its new landing. The rails' poofs are the gates' own (StairGates).
   */
  private sounds(e: StairEvents, gsShown: boolean): void {
    for (const g of this.geoms) {
      const bit = 1 << g.bit;
      const at: [number, number, number] = [g.pivot[0], (g.a.y ?? 0) + 1.2, g.pivot[1]];
      if (gsShown && e.warningStarted & bit) {
        audio.playAt("stairs.warning", at);
      }
      const swinging = this.views[g.bit]?.phase === "swing";
      const loop = this.grinding.get(g.bit);
      if (swinging && !loop && gsShown) this.grinding.set(g.bit, audio.loop("stairs.swing", { at, gain: 0.8 }));
      else if (loop && (!swinging || !gsShown)) {
        loop.stop(0.4);
        this.grinding.delete(g.bit);
        if (!swinging && gsShown) audio.playAt("stairs.dock", at);
      }
    }
  }

  /** Whether `p` stands on a flight that is swinging now. */
  private onSwinging(p: TurnablePose): boolean {
    for (const g of this.geoms) {
      const v = this.views[g.bit];
      if (v?.phase !== "swing") continue;
      if (onStair(v.walk === "a" ? g.a : g.b, p.x, p.y, p.z)) return true;
    }
    return false;
  }

  /** Whether a step from `prev` to `next` walks onto a swinging flight from off it (the player stops at the gate; see `entersSwinging`). */
  blocksStep(prev: readonly [number, number, number], next: readonly [number, number, number]): boolean {
    return this.enabled && entersSwinging(this.geoms, this.views, prev, next);
  }

  /** Turns a drawn pose with the flight it stands on (see `riderTurn`). Returns whether it turned. */
  turnDrawn(pose: TurnablePose): boolean {
    for (const g of this.geoms) {
      const rad = riderTurn(g, (this.turns[g.bit]! * 180) / Math.PI, pose.x, pose.y, pose.z);
      if (rad === null) continue;
      if (rad === 0) return false;
      turnPose(pose, g.pivot, rad, this.scratch);
      return true;
    }
    return false;
  }

  /** Under the treads of `g` where it rests: its two ends and its middle. */
  private dustPoints(g: FlightGeom): [number, number, number][] {
    const v = this.views[g.bit];
    const s = v?.walk === "b" ? g.b : g.a;
    const cx = (s.min[0] + s.max[0]) / 2;
    const cz = (s.min[1] + s.max[1]) / 2;
    const alongX = s.max[0] - s.min[0] > s.max[1] - s.min[1];
    const pts: [number, number][] = alongX
      ? [
          [s.min[0] + 1, cz],
          [cx, cz],
          [s.max[0] - 1, cz],
        ]
      : [
          [cx, s.min[1] + 1],
          [cx, cz],
          [cx, s.max[1] - 1],
        ];
    this.dustAt.length = 0;
    for (const [x, z] of pts) this.dustAt.push([x, stairHeightAt(s, x, z) - 0.35, z]);
    return this.dustAt;
  }

  /** The A model's turn of each flight now (radians, by flight id, with its pivot): the `?layout` overlay. */
  turnsById(): Map<string, { pivot: readonly [number, number]; rad: number }> {
    return new Map(this.geoms.map((g) => [g.id, { pivot: g.pivot, rad: this.turns[g.bit]! }]));
  }

  /** For the F3 overlay. */
  perfLine(): [string, string] {
    if (!this.enabled) return ["stairs", "off"];
    const st = this.views.map((v) => `${v.id.replace("flight_", "")} ${v.phase === "swing" ? `swing ${Math.round(v.progress * 100)}%` : v.warning ? "warning" : v.walk.toUpperCase()}`).join(", ");
    return ["stairs", `state ${this.sim.currentStairState.toString(2).padStart(this.geoms.length, "0")}: ${st}; ${this.swings.count} swing nodes, ${this.gates?.perfLine() ?? "no gates"}; ${this.switches} switches, ${this.carries} carried${this.riding ? ", riding" : ""}`];
  }

  dispose(): void {
    this.swings.dispose();
    this.gates?.dispose();
  }
}
