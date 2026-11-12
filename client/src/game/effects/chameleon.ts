// Chameleon Tonic: a character's hair or robe colour steps through the
// colours of one palette dye family (`VisibleEffect::Chameleon.family`, an
// index into palette.json `families`), one step every
// `effects.chameleon.cycle_ms` (data/economy.json).
//
// Every viewer must see the same colour at the same moment without the
// server sending anything per step, so the sequence is a pure function of a
// seed (the character's entity id, the same for everyone in that world) and
// the wall clock: the family's colours in a seeded order, cycled. Two
// chameleons side by side therefore drift through their colours out of step
// with each other, and consecutive steps never repeat a colour. Pure, unit
// tested in effects.test.ts.

import economyJson from "../../../../data/economy.json";
import { PALETTE, type Palette } from "../../content/catalog";

/** Milliseconds per colour step (data/economy.json `effects.chameleon.cycle_ms`). */
export const CHAMELEON_CYCLE_MS: number = economyJson.effects.chameleon.cycle_ms;

/** A palette family as palette.json lists it. */
export interface DyeFamily {
  id: string;
  name: string;
  colours: number[];
}

/** The palette's dye families, in position order. */
export function dyeFamilies(palette: Palette = PALETTE): readonly DyeFamily[] {
  return (palette as Palette & { families?: DyeFamily[] }).families ?? [];
}

/** Palette indices of family `index`, or an empty list for an unknown one. */
export function familyColours(index: number, palette: Palette = PALETTE): readonly number[] {
  return dyeFamilies(palette)[index]?.colours ?? [];
}

/** The family (position) a palette colour belongs to (`Palette::family_of`), or -1. */
export function familyOf(colour: number, palette: Palette = PALETTE): number {
  return dyeFamilies(palette).findIndex((f) => f.colours.includes(colour));
}

/** mulberry32: a tiny, well-mixed 32-bit generator; deterministic per seed. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The family's colours in the order seed `seed` cycles them (a seeded shuffle). */
export function chameleonOrder(colours: readonly number[], seed: number): number[] {
  const out = [...colours];
  const next = rng(seed ^ 0x9e3779b9);
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(next() * (i + 1));
    [out[i], out[j]] = [out[j]!, out[i]!];
  }
  return out;
}

/** Which step of the cycle wall-clock time `timeMs` falls in. */
export function chameleonStep(timeMs: number, cycleMs: number = CHAMELEON_CYCLE_MS): number {
  return Math.floor(timeMs / Math.max(1, cycleMs));
}

/**
 * The palette index shown at `timeMs` (ms since the epoch, e.g. `Date.now()`)
 * by a chameleon seeded `seed` cycling `colours`; -1 for an empty family.
 * `order` may pass `chameleonOrder(colours, seed)` precomputed.
 */
export function chameleonColour(colours: readonly number[], seed: number, timeMs: number, cycleMs: number = CHAMELEON_CYCLE_MS, order?: readonly number[]): number {
  const seq = order ?? chameleonOrder(colours, seed);
  if (!seq.length) return -1;
  const step = chameleonStep(timeMs, cycleMs);
  return seq[((step % seq.length) + seq.length) % seq.length]!;
}
