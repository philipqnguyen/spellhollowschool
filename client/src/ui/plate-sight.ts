// Whether a name above a head may show: only while the head itself could be
// seen, with no wall, floor or ceiling between it and the camera. Plates are
// DOM labels drawn over the canvas, so the depth buffer never hides them; a
// ray from the camera to the head against the level's static geometry does.
//
// Rays are not free (the camera's own two a frame already walk every shown
// collider), so they are rationed: a character just come into view is tested
// at once (it starts hidden: a name must never flash through a floor), the
// rest are retested in turn a few a frame, each at most every RECHECK_MS.
// The answer is eased into an opacity so a name fades in or out rather than
// popping as someone walks past a pillar or through a door.

/** True when level geometry stands between (ax, ay, az) and (bx, by, bz). */
export type SightTest = (ax: number, ay: number, az: number, bx: number, by: number, bz: number) => boolean;

/** A plate's anchor (above the head) in world space. */
export interface SightTarget {
  id: number;
  x: number;
  y: number;
  z: number;
}

/** The ray aims this far below the plate's anchor (m): the anchor sits 0.32 m over the crown, this is mid-head. */
export const SIGHT_DROP = 0.45;
/** Rays stop this short of the head (m), so geometry the character stands against or under does not count. */
export const SIGHT_SHORT = 0.3;
/** Fade time from hidden to shown or back (ms). */
export const FADE_MS = 160;
/** A plate's line of sight is retested at most this often (ms), staggered per id. */
export const RECHECK_MS = 180;
/** Tests a frame for characters with no answer yet (new in view, or not looked at for STALE_MS). */
export const NEW_PER_FRAME = 6;
/** Tests a frame for the routine retests, taken in turn. */
export const RETEST_PER_FRAME = 3;
/** An answer older than this (ms) is no answer: the character was out of view or off screen. */
const STALE_MS = 600;
/** States unseen for this long (ms) are dropped. */
const FORGET_MS = 3000;
/** A camera move this long (m) in one frame (a cut, a teleport) makes every answer stale. */
const CUT_M = 3;

interface Sight {
  clear: boolean;
  /** Eased visibility, 0 (hidden) to 1. */
  alpha: number;
  /** When it was last tested (ms), or -Infinity. */
  tested: number;
  seen: number;
}

export class PlateSight {
  private readonly states = new Map<number, Sight>();
  private test: SightTest | null;
  /** How far below the given anchor the head is aimed at (m). */
  private readonly drop: number;
  private cursor = 0;
  private lastNow = -1;
  private readonly lastEye: [number, number, number] = [NaN, NaN, NaN];
  /** Rays cast in the last update, and since the counters were read. */
  raysLastFrame = 0;
  private rays = 0;
  private frames = 0;
  private ms = 0;

  /** `drop`: how far the head's middle lies under the anchors given (SIGHT_DROP for a plate's). */
  constructor(test: SightTest | null = null, drop = SIGHT_DROP) {
    this.test = test;
    this.drop = drop;
  }

  /** The level to test against; null: nothing blocks (no world yet). Every answer is retaken. */
  setTest(test: SightTest | null): void {
    this.test = test;
    for (const s of this.states.values()) s.tested = -Infinity;
  }

  /**
   * Once a frame: the opacity (0..1) each of `targets` (nearest first) may
   * show at, into `out` by index. 0 is hidden and must not be drawn (nor take
   * room from other plates).
   */
  update(targets: readonly SightTarget[], eye: readonly [number, number, number], now: number, out: number[]): number[] {
    const t0 = performance.now();
    out.length = targets.length;
    const dt = this.lastNow < 0 ? 0 : Math.min(100, Math.max(0, now - this.lastNow));
    this.lastNow = now;
    const le = this.lastEye;
    if (!(Math.hypot(eye[0] - le[0], eye[1] - le[1], eye[2] - le[2]) < CUT_M)) for (const s of this.states.values()) s.tested = -Infinity;
    le[0] = eye[0];
    le[1] = eye[1];
    le[2] = eye[2];

    let rays = 0;
    const n = targets.length;
    // States first, and the characters with no answer yet are tested now, nearest first.
    let fresh = NEW_PER_FRAME;
    for (let i = 0; i < n; i++) {
      const t = targets[i]!;
      let s = this.states.get(t.id);
      if (!s) {
        s = { clear: false, alpha: 0, tested: -Infinity, seen: now };
        this.states.set(t.id, s);
      }
      s.seen = now;
      if (now - s.tested > STALE_MS && fresh > 0) {
        fresh--;
        rays++;
        this.look(s, t, eye, now);
        // An old answer (back on screen, or after a cut) proved wrong: gone at once, no fade through the wall.
        if (!s.clear) s.alpha = 0;
      }
    }
    // Then the routine retests, in turn from where the last frame stopped.
    let retest = RETEST_PER_FRAME;
    for (let k = 0; k < n && retest > 0; k++) {
      const i = (this.cursor + k) % n;
      const t = targets[i]!;
      const s = this.states.get(t.id)!;
      if (now - s.tested < RECHECK_MS + (t.id % 7) * 10) continue;
      retest--;
      rays++;
      this.look(s, t, eye, now);
      this.cursor = i + 1;
    }
    if (n > 0) this.cursor %= n;

    const step = FADE_MS > 0 ? dt / FADE_MS : 1;
    for (let i = 0; i < n; i++) {
      const s = this.states.get(targets[i]!.id)!;
      // Never shown before its first answer; once answered, eased toward it.
      if (s.tested === -Infinity && s.alpha === 0) out[i] = 0;
      else {
        s.alpha = s.clear ? Math.min(1, s.alpha + step) : Math.max(0, s.alpha - step);
        out[i] = s.alpha;
      }
    }
    if (this.states.size > n) for (const [id, s] of this.states) if (now - s.seen > FORGET_MS) this.states.delete(id);

    this.raysLastFrame = rays;
    this.rays += rays;
    this.frames++;
    this.ms += performance.now() - t0;
    return out;
  }

  private look(s: Sight, t: SightTarget, eye: readonly [number, number, number], now: number): void {
    s.tested = now;
    if (!this.test) {
      s.clear = true;
      return;
    }
    const hx = t.x;
    const hy = t.y - this.drop;
    const hz = t.z;
    const dx = hx - eye[0];
    const dy = hy - eye[1];
    const dz = hz - eye[2];
    const len = Math.hypot(dx, dy, dz);
    if (len <= SIGHT_SHORT) {
      s.clear = true;
      return;
    }
    const k = (len - SIGHT_SHORT) / len;
    s.clear = !this.test(eye[0], eye[1], eye[2], eye[0] + dx * k, eye[1] + dy * k, eye[2] + dz * k);
  }

  /** For the F3 overlay: rays a frame and the time spent, averaged since the last call. */
  stats(): { raysPerFrame: number; msPerFrame: number; tracked: number } {
    const f = Math.max(1, this.frames);
    const out = { raysPerFrame: this.rays / f, msPerFrame: this.ms / f, tracked: this.states.size };
    this.rays = 0;
    this.frames = 0;
    this.ms = 0;
    return out;
  }
}
