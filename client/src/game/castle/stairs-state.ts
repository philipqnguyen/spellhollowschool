// The moving staircases' pure side (docs/architecture.md):
// the geometry of each moving flight read from the layout, who rides
// a flight and how far they have turned, the swing node's turn with the
// warning's shudder, each landing gate leaf's pose, and the frame-to-frame
// watch that turns `SimWorld.flightStates` into events (a switch, a swing
// starting, a warning starting).
//
// The schedule itself is sim-core's (`sim_core::stairs`, through sim-wasm:
// `stairState`, `flightStates`, `carry`): the client computes nothing of
// it. What is here is drawing and bookkeeping. No Babylon, unit tested in
// stairs.test.ts against the real schedule.
//
// Frames: the layout's numbers are the scene's (x east, y up, z south). A
// flight's B is its A turned by `angle_deg` about the vertical through its
// pivot, a point (dx, dz) from the pivot going to
// (dx cos a + dz sin a, -dx sin a + dz cos a): exactly Babylon's
// `Matrix.RotationY(a)` on the same numbers, so a node turned by
// `rotation.y = a` about the pivot lands on B.

import type { FlightView, StairFlightDef, StairGateDef } from "../../wasm/sim_wasm";

/** A stair surface as the layout writes it (only what the client needs). */
export interface StairSurface {
  id: string;
  min: [number, number];
  max: [number, number];
  y?: number;
  y_top?: number;
  rise?: string;
}

/** One moving flight: its bit, pivot, turn and both surfaces. */
export interface FlightGeom {
  id: string;
  /** Bit i of a staircase state: this flight at B. */
  bit: number;
  pivot: [number, number];
  /** A to B (radians, the layout's sense). */
  angle: number;
  a: StairSurface;
  b: StairSurface;
  /** The rectangle round both positions (a cheap first test for riders). */
  box: { x0: number; z0: number; x1: number; z1: number; y0: number; y1: number };
}

/** How far above or below a stair's slope a character may stand and still count as on it (m). */
const ON_DY = 0.75;
/** Rectangles are matched this generously (m): a rider at the very edge still rides. */
const EDGE = 0.02;

/** The moving flights (`SimWorld.stairFlights()`, in bit order) with their surfaces from the layout; flights whose surfaces are missing are left out. */
export function flightGeoms(defs: readonly StairFlightDef[], surfaces: readonly StairSurface[]): FlightGeom[] {
  const byId = new Map(surfaces.map((s) => [s.id, s]));
  const out: FlightGeom[] = [];
  defs.forEach((d, bit) => {
    const a = byId.get(d.a);
    const b = byId.get(d.b);
    if (!a || !b) return;
    const ys = [a.y ?? 0, a.y_top ?? a.y ?? 0, b.y ?? 0, b.y_top ?? b.y ?? 0];
    out.push({
      id: d.id,
      bit,
      pivot: [d.pivot[0], d.pivot[1]],
      angle: (d.angle_deg * Math.PI) / 180,
      a,
      b,
      box: {
        x0: Math.min(a.min[0], b.min[0]) - EDGE,
        z0: Math.min(a.min[1], b.min[1]) - EDGE,
        x1: Math.max(a.max[0], b.max[0]) + EDGE,
        z1: Math.max(a.max[1], b.max[1]) + EDGE,
        y0: Math.min(...ys) - ON_DY,
        y1: Math.max(...ys) + ON_DY,
      },
    });
  });
  return out;
}

/** The height of a stair surface at (x, z): it rises linearly from `y` to `y_top` toward `rise`. */
export function stairHeightAt(s: StairSurface, x: number, z: number): number {
  const y = s.y ?? 0;
  if (s.y_top === undefined || !s.rise) return y;
  const [x0, z0] = s.min;
  const [x1, z1] = s.max;
  const fx = x1 > x0 ? (x - x0) / (x1 - x0) : 0;
  const fz = z1 > z0 ? (z - z0) / (z1 - z0) : 0;
  const t = s.rise === "+x" ? fx : s.rise === "-x" ? 1 - fx : s.rise === "+z" ? fz : s.rise === "-z" ? 1 - fz : 0;
  return y + (s.y_top - y) * Math.min(1, Math.max(0, t));
}

/** Whether a character's feet at (x, y, z) stand on stair surface `s`. */
export function onStair(s: StairSurface, x: number, y: number, z: number): boolean {
  if (x < s.min[0] - EDGE || x > s.max[0] + EDGE || z < s.min[1] - EDGE || z > s.max[1] + EDGE) return false;
  return Math.abs(y - stairHeightAt(s, x, z)) <= ON_DY;
}

/** (x, z) turned by `rad` about `pivot` (the layout's sense; see the header), into `out`. */
export function turnAbout(pivot: readonly [number, number], rad: number, x: number, z: number, out: [number, number]): [number, number] {
  const c = Math.cos(rad);
  const s = Math.sin(rad);
  const dx = x - pivot[0];
  const dz = z - pivot[1];
  out[0] = pivot[0] + dx * c + dz * s;
  out[1] = pivot[1] - dx * s + dz * c;
  return out;
}

/**
 * How far (radians) a character standing at (x, y, z) is drawn turned with
 * flight `g`, whose A model is turned `turnDeg` now; null when it is on
 * neither position. On A the rider turns with the flight; on B (where the
 * walk position is B, or a remote's interpolation still shows it there
 * after a switch back) by the turn less the whole angle. So a rider on a
 * resting flight is drawn where it stands, a rider on a swinging flight
 * swings with it, and a remote whose snapshots still put it on the old
 * position just after the switch is drawn where it was carried.
 */
export function riderTurn(g: FlightGeom, turnDeg: number, x: number, y: number, z: number): number | null {
  const b = g.box;
  if (x < b.x0 || x > b.x1 || z < b.z0 || z > b.z1 || y < b.y0 || y > b.y1) return null;
  const turn = (turnDeg * Math.PI) / 180;
  if (onStair(g.a, x, y, z)) return turn;
  if (onStair(g.b, x, y, z)) return turn - g.angle;
  return null;
}

/** A drawn pose turned with a flight: position about the pivot, facing by the same angle (forward = (sin yaw, cos yaw)). */
export function turnPose(pose: { x: number; z: number; yaw: number }, pivot: readonly [number, number], rad: number, scratch: [number, number]): void {
  if (rad === 0) return;
  turnAbout(pivot, rad, pose.x, pose.z, scratch);
  pose.x = scratch[0];
  pose.z = scratch[1];
  pose.yaw += rad;
}

/**
 * Whether a step from `prev` to `next` (feet) walks onto a flight that is
 * swinging, from off it. Its old position stays walkable until the switch
 * (the server accepts moves there), but it is drawn swinging away behind a
 * shut gate, so the client never predicts that step: the player stops at
 * the gate. Stricter than the server, so never corrected. Riders already on
 * it are not affected (their input is held anyway).
 */
export function entersSwinging(geoms: readonly FlightGeom[], views: readonly Pick<FlightView, "phase" | "walk">[], prev: readonly [number, number, number], next: readonly [number, number, number]): boolean {
  for (const g of geoms) {
    const v = views[g.bit];
    if (v?.phase !== "swing") continue;
    const s = v.walk === "b" ? g.b : g.a;
    if (onStair(s, next[0], next[1], next[2]) && !onStair(s, prev[0], prev[1], prev[2])) return true;
  }
  return false;
}

/** How near a warned flight (m, round the box of both its positions) a player hears the chat line. */
export const WARN_NEAR_M = 8;

/**
 * Whether the warning of the flights in `bits` concerns a player at (x, y, z):
 * within `WARN_NEAR_M` of either position of one of them (sideways, and
 * above or below). A player elsewhere in the well sees the shudder and the
 * dust but gets no chat line, so the line means "the flight by you".
 */
export function warnsNear(geoms: readonly FlightGeom[], bits: number, x: number, y: number, z: number): boolean {
  for (const g of geoms) {
    if (!(bits & (1 << g.bit))) continue;
    const b = g.box;
    const n = WARN_NEAR_M;
    if (x >= b.x0 - n && x <= b.x1 + n && z >= b.z0 - n && z <= b.z1 + n && y >= b.y0 - n && y <= b.y1 + n) return true;
  }
  return false;
}

/** The warning's shudder (radians added to the flight's turn): a small fast tremble that grows toward the swing. */
export const SHUDDER_RAD = (0.35 * Math.PI) / 180;
export function shudder(view: Pick<FlightView, "warning" | "nextSwitchMs" | "phase">, unixMs: number, swingMs: number, warningMs: number): number {
  if (!view.warning || view.phase !== "rest") return 0;
  // 0 at the warning's start, 1 as the swing begins.
  const toSwing = view.nextSwitchMs - swingMs - unixMs;
  const k = Math.min(1, Math.max(0, 1 - toSwing / warningMs));
  const t = unixMs / 1000;
  return SHUDDER_RAD * (0.35 + 0.65 * k) * (Math.sin(t * 2 * Math.PI * 7.3) * 0.7 + Math.sin(t * 2 * Math.PI * 11.9) * 0.3);
}

/**
 * The state trails are planned in: the walk state with every swinging
 * flight already at the position it swings to (the way that will exist
 * when the swing ends; the way it leaves is already closing).
 */
export function planState(state: number, views: readonly Pick<FlightView, "phase">[]): number {
  let s = state;
  views.forEach((v, i) => {
    if (v.phase === "swing") s ^= 1 << i;
  });
  return s;
}

/** One flight's switch: the position it left and the one it reached. */
export interface StairSwitch {
  flight: number;
  from: "a" | "b";
  to: "a" | "b";
}

export interface StairEvents {
  /** The first frame (nothing to compare with): apply `state`, carry nobody. */
  first: boolean;
  /** The walk state now (bit i: flight i at B). */
  state: number;
  /** Whether it changed since the last frame. */
  changed: boolean;
  /** The flights whose walk position changed (carry their riders). */
  switched: StairSwitch[];
  /** Bits of the flights that began swinging this frame. */
  swingStarted: number;
  /** Bits of the flights whose warning began this frame (shudder, dust, the chat line). */
  warningStarted: number;
  /** `planState` now. */
  plan: number;
}

/** Frame to frame: what changed in the staircases. Pure; feed it `stairState` and `flightStates` for one instant. */
export class StairWatch {
  private last: number | null = null;
  private swinging = 0;
  private warning = 0;
  private readonly events: StairEvents = { first: true, state: 0, changed: false, switched: [], swingStarted: 0, warningStarted: 0, plan: 0 };

  update(state: number, views: readonly Pick<FlightView, "phase" | "warning">[]): StairEvents {
    const e = this.events;
    let swinging = 0;
    let warning = 0;
    views.forEach((v, i) => {
      if (v.phase === "swing") swinging |= 1 << i;
      if (v.warning) warning |= 1 << i;
    });
    e.first = this.last === null;
    e.state = state;
    e.changed = !e.first && state !== this.last;
    e.switched.length = 0;
    if (e.changed) {
      const flipped = state ^ this.last!;
      for (let i = 0; i < views.length; i++) {
        if (!(flipped & (1 << i))) continue;
        const toB = (state & (1 << i)) !== 0;
        e.switched.push({ flight: i, from: toB ? "a" : "b", to: toB ? "b" : "a" });
      }
    }
    e.swingStarted = e.first ? 0 : swinging & ~this.swinging;
    e.warningStarted = e.first ? 0 : warning & ~this.warning;
    e.plan = planState(state, views);
    this.last = state;
    this.swinging = swinging;
    this.warning = warning;
    return e;
  }
}

/**
 * The Monstra Viam trail and the staircases: the trail plans in `planState` and
 * plans again when the walk state changes or a flight its way crosses
 * begins to swing (`Wayfinder.pathFlights` after each plan).
 */
export class TrailStairs {
  /** The state the trail's graph is in. */
  plan = 0;
  /** Bits of the flights the current trail crosses. */
  flights = 0;

  /** After a frame's events: whether the trail should plan again (the caller sets its graph to `plan` first). */
  onStairs(e: Pick<StairEvents, "changed" | "swingStarted" | "plan">): boolean {
    this.plan = e.plan;
    return e.changed || (e.swingStarted & this.flights) !== 0;
  }
}

/**
 * A gate's rail pose: where the rail prop's origin goes, the Babylon
 * `rotation.y` that lays its +x along the gate line, its length along x
 * (`sx`: 1 for a gate as wide as the prop) and how much of it stands (`s`,
 * its y and z scale: 0 vanished, 1 whole).
 */
export interface RailPose {
  x: number;
  y: number;
  z: number;
  rotY: number;
  sx: number;
  s: number;
}

/** The rail prop's length (m): grid.py GATE_WIDTH (every gate's hinges are this far apart). */
export const RAIL_W = 4;
/** The rail's middle above its foot (m): it is conjured from and vanishes into this point. */
export const RAIL_MID_Y = 0.5;

/** How much of a gate's rail stands at `open` (0 shut: all of it; 1 open: none), eased in and out. */
export function railShown(open: number): number {
  const v = 1 - Math.min(1, Math.max(0, open));
  return v * v * (3 - 2 * v);
}

/**
 * The rail of `gate` with `shown` of it standing (railShown), into `out`.
 * Its origin sits on a hinge and its +x runs to the other; Babylon's Y turn
 * takes +x to (cos r, -sin r) and +z to (sin r, cos r), so r = atan2(-u.z,
 * u.x), and the hinge it starts from is the one that turns the prop's +z (the
 * side its coping does not overhang) onto the landing (`opens`). It scales
 * about its middle (half way along, RAIL_MID_Y up, on the line), which stays put.
 */
export function gateRail(gate: Pick<StairGateDef, "line" | "opens" | "y">, shown: number, out: RailPose): RailPose {
  let h = gate.line[0];
  const o = gate.line[1];
  let dx = o[0] - h[0];
  let dz = o[1] - h[1];
  const len = Math.hypot(dx, dz) || 1;
  dx /= len;
  dz /= len;
  if (-dz * gate.opens[0] + dx * gate.opens[1] < 0) {
    h = o;
    dx = -dx;
    dz = -dz;
  }
  const s = Math.min(1, Math.max(0, shown));
  const sx = len / RAIL_W;
  const a = ((sx * RAIL_W) / 2) * (1 - s);
  out.x = h[0] + a * dx;
  out.y = gate.y + RAIL_MID_Y * (1 - s);
  out.z = h[1] + a * dz;
  out.rotY = Math.atan2(-dz, dx);
  out.sx = sx * s;
  out.s = s;
  return out;
}
