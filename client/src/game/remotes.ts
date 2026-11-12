// The other players in this client's visible set. The server decides who is
// in the set (nearest N, same floor first, with hysteresis); this only draws
// what it is sent: `entered` adds (with the character's appearance),
// `left` removes, `states` feed each character's interpolation track, and
// `appearanceChanged` re-dresses a character already in view.

import type { Anim, Appearance, EntityInfo, ServerMessage } from "../wasm/sim_wasm";
import { INTERP_DELAY_MS, SnapshotClock, Track, type Pose } from "./interp";

export interface Remote {
  id: number;
  name: string;
  /** Lower-case house id. */
  house: string;
  appearance: Appearance;
  /** Cache key with `id` for the assembled character. */
  appearanceVersion: number;
  track: Track;
  /** Interpolated pose for the current frame. */
  pose: Pose;
  /** Smoothed ground speed of the drawn pose (m/s), for walk / run playback. */
  speed: number;
  /** Pose position last frame, for `speed`. */
  lastX: number;
  lastZ: number;
  posed: boolean;
}

type Snapshot = Extract<ServerMessage, { type: "snapshot" }>;

/** Speeds above this between two frames are teleports, not movement (m/s). */
const MAX_PLAUSIBLE_SPEED = 20;
/** Smoothing rate of the speed estimate (1/s). */
const SPEED_SMOOTHING = 10;

export class RemotePlayers {
  readonly byId = new Map<number, Remote>();
  private readonly clock = new SnapshotClock();
  private readonly snapshotMs: number;
  private readonly tickMs: number;

  constructor(
    private readonly selfId: number,
    tickHz: number,
    snapshotHz: number,
  ) {
    this.tickMs = 1000 / tickHz;
    this.snapshotMs = 1000 / snapshotHz;
  }

  apply(msg: Snapshot, nowMs: number): void {
    const serverMs = msg.tick * this.tickMs;
    this.clock.observe(serverMs, nowMs);
    for (const id of msg.left) this.byId.delete(id);
    for (const info of msg.entered) this.add(info);
    for (const s of msg.states) {
      const r = this.byId.get(s.id);
      // States for unknown ids (e.g. ourselves) are ignored: only the visible set is drawn.
      r?.track.push({ t: serverMs, x: s.x, y: s.y, z: s.z, yaw: s.yaw, anim: s.anim });
    }
  }

  private add(info: EntityInfo): void {
    if (info.id === this.selfId) return;
    const existing = this.byId.get(info.id);
    const house = info.character.house.toLowerCase();
    if (existing && existing.name === info.character.name && existing.house === house) {
      this.setAppearance(info.id, info.appearanceVersion, info.appearance);
      return;
    }
    this.byId.set(info.id, {
      id: info.id,
      name: info.character.name,
      house,
      appearance: info.appearance,
      appearanceVersion: info.appearanceVersion,
      track: new Track(this.snapshotMs),
      pose: { x: 0, y: 0, z: 0, yaw: 0, anim: "idle" as Anim },
      speed: 0,
      lastX: 0,
      lastZ: 0,
      posed: false,
    });
  }

  /** `appearanceChanged` for a character in view; older versions than the one shown are ignored. Returns whether it applied. */
  setAppearance(id: number, version: number, appearance: Appearance): boolean {
    const r = this.byId.get(id);
    if (!r || version < r.appearanceVersion) return false;
    r.appearance = appearance;
    r.appearanceVersion = version;
    return true;
  }

  /** Interpolates every remote for local time `nowMs`; returns those with a pose. */
  update(nowMs: number, dt: number, out: Remote[]): Remote[] {
    out.length = 0;
    const t = this.clock.serverTime(nowMs) - INTERP_DELAY_MS;
    for (const r of this.byId.values()) {
      if (!r.track.sampleAt(t, r.pose)) continue;
      trackSpeed(r, dt);
      out.push(r);
    }
    return out;
  }

  /** Drops everyone, e.g. on disconnect: a new connection resends the visible set. */
  clear(): void {
    this.byId.clear();
    this.clock.reset();
  }

  /** Character names currently visible, for whisper completion. */
  names(): string[] {
    return [...this.byId.values()].map((r) => r.name);
  }
}

/** Updates `r.speed` from how far its drawn pose moved since last frame. */
export function trackSpeed(r: Pick<Remote, "pose" | "speed" | "lastX" | "lastZ" | "posed">, dt: number): void {
  const moving = r.pose.anim === "walk" || r.pose.anim === "run";
  if (r.posed && dt > 0) {
    const v = Math.hypot(r.pose.x - r.lastX, r.pose.z - r.lastZ) / dt;
    const target = moving && v < MAX_PLAUSIBLE_SPEED ? v : 0;
    r.speed += (target - r.speed) * Math.min(1, dt * SPEED_SMOOTHING);
  }
  r.lastX = r.pose.x;
  r.lastZ = r.pose.z;
  r.posed = true;
}
