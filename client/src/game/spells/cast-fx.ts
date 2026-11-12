// Spells to level 20 (protocol v11): what a social cast looks like where it
// lands. A spell aimed at a character sends a short streak in its colour
// from the caster's wand tip to the target's chest, a burst on arrival, and
// then its signature:
//
// - Tinctura: a rainbow sparkle swirl round the target;
// - Rigora: chalk-white rings closing round the body;
// - Tardara: a slow magenta ripple;
// - Adveni: a teal tether from the wand to the target that shortens as they
//   slide in (it follows both every frame);
// - Dissolvo: a gold spiral unravelling up and off the target;
// - Aperio (no target): a mint ring sweeping out from the caster.
//
// Everything is glints and glows in SpellFx's two sprite layers (no new
// shapes, no new draws). `signatureOf`, `streakAt` and `hueRgb` are pure and
// unit tested (cast-fx.test.ts).

import type { SocialSpell } from "./data";
import type { Rgb, SpellFx } from "./vfx";

export type Signature = "swirl" | "rings" | "ripple" | "tether" | "unravel" | "sweep";

/** The streak's flight from wand to target (ms). */
export const STREAK_MS = 220;
/** At most this many signatures at once (the oldest go). */
const ACTIVE_MAX = 24;

/** How long each signature plays (ms), after the streak. */
const LENGTH: Record<Signature, number> = { swirl: 1300, rings: 900, ripple: 1800, tether: 900, unravel: 1100, sweep: 1400 };

/** The signature of a social spell, if it has one. */
export function signatureOf(kind: SocialSpell["kind"]): Signature | null {
  switch (kind) {
    case "recolour":
      return "swirl";
    case "bind":
      return "rings";
    case "slow":
      return "ripple";
    case "pull":
      return "tether";
    case "dispel":
      return "unravel";
    case "reveal":
      return "sweep";
    default:
      return null;
  }
}

/** Where the streak's head is `ageMs` after the cast: 0 at the wand, 1 at the target (eased), clamped. Pure. */
export function streakAt(ageMs: number, flightMs = STREAK_MS): number {
  if (!(flightMs > 0)) return 1;
  const t = Math.max(0, Math.min(1, ageMs / flightMs));
  return 1 - (1 - t) * (1 - t);
}

/** A bright hue (0..1 round the wheel) as sRGB 0..1. Pure. */
export function hueRgb(h: number, out: [number, number, number] = [0, 0, 0]): [number, number, number] {
  const x = (((h % 1) + 1) % 1) * 6;
  // HSV with full saturation and value, lifted a little toward white (sparkles, not paint).
  const f = (n: number) => {
    const k = (n + x) % 6;
    return 1 - Math.max(0, Math.min(k, 4 - k, 1));
  };
  out[0] = 0.35 + 0.65 * f(5);
  out[1] = 0.35 + 0.65 * f(3);
  out[2] = 0.35 + 0.65 * f(1);
  return out;
}

/** Where a character stands, as the signatures need it. */
export interface Spot {
  x: number;
  y: number;
  z: number;
  /** Head height (m). */
  head: number;
}

interface Active {
  kind: Signature;
  caster: number;
  /** The character it landed on (the caster itself for a self cast; -1: none). */
  target: number;
  rgb: [number, number, number];
  start: number;
  /** Signature length (ms; the streak comes first when aimed at someone else). */
  length: number;
  streak: boolean;
  burst: boolean;
  /** Aperio's sweep: where it started and how far it goes. */
  ox: number;
  oy: number;
  oz: number;
  radius: number;
}

const HUE: [number, number, number] = [0, 0, 0];
const WHITE: Rgb = [1, 1, 1];

export class CastSignatures {
  private active: Active[] = [];

  /**
   * A cast of a spell with signature `kind` by `caster` on `target` (-1 for
   * none; the caster for a self cast), from `origin` (the caster's feet,
   * for the sweep), reaching `radius` (m, the sweep), lasting `lengthMs`
   * (0: the signature's own).
   */
  add(kind: Signature, caster: number, target: number, rgb: Rgb, now: number, origin: readonly [number, number, number], radius = 0, lengthMs = 0): void {
    if (this.active.length >= ACTIVE_MAX) this.active.shift();
    this.active.push({
      kind,
      caster,
      target,
      rgb: [rgb[0], rgb[1], rgb[2]],
      start: now,
      length: lengthMs > 0 ? lengthMs : LENGTH[kind],
      streak: target >= 0 && target !== caster,
      burst: false,
      ox: origin[0],
      oy: origin[1],
      oz: origin[2],
      radius,
    });
  }

  get count(): number {
    return this.active.length;
  }

  clear(): void {
    this.active = [];
  }

  /**
   * Per frame, between `fx.begin` and `fx.end`. `where` finds a drawn
   * character (null: not drawn), `tip` a caster's wand tip.
   */
  update(now: number, fx: SpellFx, where: (id: number) => Spot | null, tip: (id: number) => readonly [number, number, number] | null): void {
    for (let i = this.active.length - 1; i >= 0; i--) {
      const a = this.active[i]!;
      const age = now - a.start;
      const lead = a.streak ? STREAK_MS : 0;
      if (age > lead + a.length) {
        this.active.splice(i, 1);
        continue;
      }
      const target = a.target >= 0 ? where(a.target) : null;
      if (a.target >= 0 && !target) continue;
      const from = a.streak || a.kind === "tether" ? tip(a.caster) : null;
      if (a.streak && target && from && age < STREAK_MS) {
        this.streak(fx, from, target, a.rgb, streakAt(age));
        continue;
      }
      if (a.streak && target && !a.burst) {
        a.burst = true;
        fx.burst(target.x, target.y + target.head * 0.62, target.z, a.rgb, now, 12, 0.5);
      }
      const k = Math.max(0, Math.min(1, (age - lead) / a.length));
      const fade = k > 0.75 ? (1 - k) / 0.25 : 1;
      switch (a.kind) {
        case "swirl":
          if (target) this.swirl(fx, target, k, fade, now);
          break;
        case "rings":
          if (target) this.rings(fx, target, k, fade);
          break;
        case "ripple":
          if (target) this.ripple(fx, target, a.rgb, k, fade);
          break;
        case "tether":
          if (target && from) this.streak(fx, from, target, a.rgb, 1, fade);
          break;
        case "unravel":
          if (target) this.unravel(fx, target, a.rgb, k, fade, now);
          break;
        case "sweep":
          this.sweep(fx, a, k, fade);
          break;
      }
    }
  }

  /** A line of glints from the wand to the chest, `reach` (0..1) of the way, its head a bright spark. */
  private streak(fx: SpellFx, from: readonly [number, number, number], t: Spot, rgb: Rgb, reach: number, alpha = 1): void {
    const tx = t.x;
    const ty = t.y + t.head * 0.62;
    const tz = t.z;
    const n = 12;
    for (let k = 0; k <= n; k++) {
      const s = (k / n) * reach;
      const x = from[0] + (tx - from[0]) * s;
      const y = from[1] + (ty - from[1]) * s;
      const z = from[2] + (tz - from[2]) * s;
      fx.pushGlint(x, y, z, 0.07 + 0.05 * (k / n), rgb, alpha * (0.35 + 0.65 * (k / n)));
    }
    const hx = from[0] + (tx - from[0]) * reach;
    const hy = from[1] + (ty - from[1]) * reach;
    const hz = from[2] + (tz - from[2]) * reach;
    fx.pushGlow(hx, hy, hz, 0.4, rgb, 0.8 * alpha);
    fx.pushGlint(hx, hy, hz, 0.18, WHITE, alpha);
  }

  private swirl(fx: SpellFx, t: Spot, k: number, fade: number, now: number): void {
    const n = 16;
    for (let i = 0; i < n; i++) {
      const u = i / n;
      const ang = u * Math.PI * 4 + k * Math.PI * 5;
      const r = 0.45 - 0.15 * k;
      const y = t.y + t.head * (0.1 + 0.9 * ((u + k * 0.6) % 1));
      hueRgb(u + now / 1500, HUE);
      fx.pushGlint(t.x + Math.cos(ang) * r, y, t.z + Math.sin(ang) * r, 0.12, HUE, fade);
    }
  }

  private rings(fx: SpellFx, t: Spot, k: number, fade: number): void {
    const closing = 1 - (1 - k) * (1 - k);
    const r = 0.95 - 0.68 * closing;
    for (let ring = 0; ring < 3; ring++) {
      const y = t.y + t.head * (0.25 + 0.3 * ring);
      for (let i = 0; i < 14; i++) {
        const ang = (i / 14) * Math.PI * 2 + ring * 0.7 + k * (ring % 2 ? -2 : 2);
        fx.pushGlint(t.x + Math.cos(ang) * r, y, t.z + Math.sin(ang) * r, 0.1, WHITE, 0.9 * fade);
      }
    }
  }

  private ripple(fx: SpellFx, t: Spot, rgb: Rgb, k: number, fade: number): void {
    for (let ring = 0; ring < 2; ring++) {
      const u = Math.max(0, k * 1.3 - ring * 0.3);
      if (u <= 0) continue;
      const r = 0.3 + 1.2 * Math.min(1, u);
      const y = t.y + 0.08 + t.head * 0.45 * ring;
      for (let i = 0; i < 18; i++) {
        const ang = (i / 18) * Math.PI * 2;
        const a = fade * (1 - 0.5 * Math.min(1, u));
        fx.pushGlint(t.x + Math.cos(ang) * r, y, t.z + Math.sin(ang) * r, 0.16, rgb, a);
        if (i % 3 === 0) fx.pushGlow(t.x + Math.cos(ang) * r, y, t.z + Math.sin(ang) * r, 0.35, rgb, 0.5 * a);
      }
    }
  }

  private unravel(fx: SpellFx, t: Spot, rgb: Rgb, k: number, fade: number, now: number): void {
    const n = 20;
    for (let i = 0; i < n; i++) {
      const u = i / n;
      const ang = u * Math.PI * 6 + now / 260;
      const r = 0.25 + 0.55 * k * u;
      const y = t.y + t.head * (0.05 + 1.15 * u) + k * 0.4;
      fx.pushGlint(t.x + Math.cos(ang) * r, y, t.z + Math.sin(ang) * r, 0.1 + 0.04 * u, rgb, fade * (0.4 + 0.6 * (1 - Math.abs(u - k))));
    }
  }

  private sweep(fx: SpellFx, a: Active, k: number, fade: number): void {
    const r = a.radius * (1 - (1 - k) * (1 - k));
    const n = Math.max(24, Math.min(64, Math.round(r * 3)));
    for (let i = 0; i < n; i++) {
      const ang = (i / n) * Math.PI * 2;
      fx.pushGlint(a.ox + Math.cos(ang) * r, a.oy + 0.1, a.oz + Math.sin(ang) * r, 0.14 + 0.1 * k, a.rgb, 0.85 * fade);
    }
  }
}
