// Potion effects as the client knows them (docs/architecture.md, "Economy and crafting"),
// without Babylon or the DOM:
//
// - `EffectBook`: the visible effects of every character in view (and our
//   own), from `EntityInfo.effects` (snapshots), `Welcome` and
//   `EffectsChanged`, versioned like appearances;
// - `OwnEffects`: our own timers (`OwnEffects { effects: [{ kind,
//   remainingSecs }], speedPct }`), counted down locally between messages,
//   and the speed to predict with;
// - `summarize`: what a list of `VisibleEffect`s means for drawing.
//
// Spells to level 20 (protocol v11): the spell effects (bound, slowed,
// recoloured, guardian) in `FxSummary`, and what they mean for drawing:
// `animRate` (a bound pose freezes, a slowed one plays in slow motion),
// `liveTint` and `RecolouredLooks` (Tinctura dyes the body garment in the
// look itself, so a Chameleon hair tint carries on beside it). A bound
// character's `speedPct` is 0.
//
// Pure, unit tested in effects.test.ts.

import economyJson from "../../../../data/economy.json";
import type { Appearance, EffectKind, EntityInfo, FortuneKind, GuardianAnimal, VisibleEffect } from "../../wasm/sim_wasm";

/** Every potion effect kind in display order (`protocol::EffectKind::POTIONS`; a Divination fortune shows after them). */
export const EFFECT_KINDS: readonly EffectKind[] = ["glow", "chameleon", "shrinking", "swelling", "babbling", "mimic_draught", "fortunes_draught", "pick_me_up_tonic", "invisibility", "featherlight"];

/** Spells L20: the spell effects (cast, never drunk: no potion, no duration in economy.json). */
export const SPELL_EFFECT_KINDS: readonly EffectKind[] = ["petrified", "slowed", "recoloured", "guardian"];

const RULES = economyJson.effects as unknown as Partial<Record<EffectKind, { duration_secs: number }>>;

/** How long a freshly drunk potion of `kind` lasts (s), from data/economy.json. */
export function effectDuration(kind: EffectKind): number {
  return RULES[kind]?.duration_secs ?? 0;
}

/** `ownEffects.speedPct` without effects (`protocol::SPEED_PCT_NORMAL`). */
export const SPEED_PCT_NORMAL = 100;
/** `ownEffects.broomPct` without effects (`protocol::BROOM_PCT_NORMAL`). */
export const BROOM_PCT_NORMAL = 100;

// ------------------------------------------------------------------ visible effects

interface Entry {
  version: number;
  effects: VisibleEffect[];
}

/**
 * The visible effects per entity id. A version older than the one held is
 * ignored (an `EffectsChanged` can overtake a cached `EntityInfo`); entities
 * that never had effects are not stored, so the usual lookup finds nothing.
 */
export class EffectBook {
  private readonly byId = new Map<number, Entry>();
  /** Bumped on every change, so views can tell when to look again. */
  revision = 0;

  /** Records `id`'s effects at `version`. Returns whether anything changed. */
  set(id: number, version: number, effects: readonly VisibleEffect[]): boolean {
    const held = this.byId.get(id);
    if (held && version < held.version) return false;
    if (held && version === held.version && sameEffects(held.effects, effects)) return false;
    if (!effects.length && !held) return false;
    this.byId.set(id, { version, effects: [...effects] });
    this.revision++;
    return true;
  }

  /** `id`'s visible effects (empty when none). */
  get(id: number): readonly VisibleEffect[] {
    return this.byId.get(id)?.effects ?? NONE;
  }

  version(id: number): number {
    return this.byId.get(id)?.version ?? 0;
  }

  /** A snapshot: entered characters bring their effects, those that left drop theirs. */
  applySnapshot(entered: readonly EntityInfo[], left: readonly number[], selfId: number): void {
    for (const id of left) if (id !== selfId) this.delete(id);
    for (const info of entered) {
      // A cached entry may reappear with an older look than an EffectsChanged
      // already applied; `set` keeps the newer one.
      if (info.id !== selfId) this.setFresh(info.id, info.effectsVersion, info.effects);
    }
  }

  /** Like `set`, but an entity re-entering the view starts over at its version. */
  private setFresh(id: number, version: number, effects: readonly VisibleEffect[]): void {
    const held = this.byId.get(id);
    if (held && version < held.version) return;
    if (!effects.length) {
      if (held) this.delete(id);
      return;
    }
    this.set(id, version, effects);
  }

  delete(id: number): void {
    if (this.byId.delete(id)) this.revision++;
  }

  /** Everyone but `keep` (a zone change: the visible set restarts; our own effects stay). */
  clearOthers(keep: number): void {
    for (const id of [...this.byId.keys()]) if (id !== keep) this.byId.delete(id);
    this.revision++;
  }

  clear(): void {
    this.byId.clear();
    this.revision++;
  }

  /** Ids that have any visible effect. */
  ids(): IterableIterator<number> {
    return this.byId.keys();
  }

  get size(): number {
    return this.byId.size;
  }
}

const NONE: readonly VisibleEffect[] = Object.freeze([]) as readonly VisibleEffect[];

function sameEffects(a: readonly VisibleEffect[], b: readonly VisibleEffect[]): boolean {
  return a.length === b.length && a.every((e, i) => JSON.stringify(e) === JSON.stringify(b[i]));
}

// ------------------------------------------------------------------ what to draw

/** What a character's visible effects mean for drawing it. */
export interface FxSummary {
  /** Glow Draught: aura colour (palette index), or null. */
  glow: number | null;
  /** Chameleon Tonic: what cycles and through which family. */
  chameleon: { part: "hair" | "robe"; family: number } | null;
  /** Size in percent (100 = normal). */
  scalePct: number;
  /** Mimic Draught: the look to draw instead. */
  disguise: Appearance | null;
  babbling: boolean;
  lucky: boolean;
  steam: boolean;
  nameplateHidden: boolean;
  /** Spells L20: Rigora: the pose freezes. */
  bound: boolean;
  /** Spells L20: Tardara: slow motion at this percent, or null. */
  slowedPct: number | null;
  /** Spells L20: Tinctura: the robe's colour (palette index), or null. */
  recolour: number | null;
  /** Spells L20: Custodio: the silver animal (`guarding`: a duel ward), or null. */
  guardian: { animal: GuardianAnimal; guarding: boolean } | null;
}

export const NO_FX: Readonly<FxSummary> = Object.freeze({
  glow: null,
  chameleon: null,
  scalePct: 100,
  disguise: null,
  babbling: false,
  lucky: false,
  steam: false,
  nameplateHidden: false,
  bound: false,
  slowedPct: null,
  recolour: null,
  guardian: null,
});

export function summarize(effects: readonly VisibleEffect[]): FxSummary {
  if (!effects.length) return NO_FX;
  const s: FxSummary = { ...NO_FX };
  for (const e of effects) {
    switch (e.kind) {
      case "glow":
        s.glow = e.colour;
        break;
      case "chameleon":
        s.chameleon = { part: e.part, family: e.family };
        break;
      case "scale":
        // Clamped to what the effects can do (Diminishing Draught 50 %, Enlarging Draught 130 %), with margin.
        s.scalePct = Math.max(25, Math.min(200, e.pct));
        break;
      case "disguise":
        s.disguise = e.appearance;
        break;
      case "babbling":
        s.babbling = true;
        break;
      case "lucky":
        s.lucky = true;
        break;
      case "steam":
        s.steam = true;
        break;
      case "nameplateHidden":
        s.nameplateHidden = true;
        break;
      case "bound":
        s.bound = true;
        break;
      case "slowed":
        s.slowedPct = Math.max(1, Math.min(100, e.pct));
        break;
      case "recoloured":
        s.recolour = e.colour;
        break;
      case "guardian":
        s.guardian = { animal: e.animal, guarding: e.guarding };
        break;
    }
  }
  return s;
}

/** Spells L20: how fast a character's animation plays: 0 bound (the pose freezes), `pct / 100` slowed, else 1. */
export function animRate(s: FxSummary): number {
  if (s.bound) return 0;
  if (s.slowedPct !== null) return Math.max(0.05, Math.min(1, s.slowedPct / 100));
  return 1;
}

/**
 * Spells L20: the part a Chameleon Tonic recolours live now, or null.
 * Tinctura is not a live tint: it dyes the body garment in the look itself
 * (`recolouredLook`), so a hair tonic keeps cycling the hair beside it, and a
 * robe tonic waits (Tinctura's colour wins on the robe) until it ends.
 */
export function liveTint(s: FxSummary): "hair" | "robe" | null {
  const ch = s.chameleon;
  if (!ch) return null;
  return ch.part === "robe" && s.recolour !== null ? null : ch.part;
}

/**
 * Spells L20: `a` with Tinctura's `colour` (palette index) as the body
 * garment's primary dye; `a` itself when it wears no body garment or already
 * shows that colour.
 */
export function recolouredLook(a: Appearance, colour: number): Appearance {
  const body = a.outfit.body;
  if (!body || body.dyes[0] === colour) return a;
  return { ...a, outfit: { ...a.outfit, body: { ...body, dyes: [colour, body.dyes[1], body.dyes[2]] } } };
}

/**
 * Spells L20: Tinctura's looks are numbered from here down (a renderer
 * version: appearance versions are positive, Mimic Draught's small negatives).
 */
export const RECOLOUR_VERSION_BASE = -(2 ** 30);

/** How many characters' Tinctura looks are kept before the cache starts over. */
const RECOLOURED_MAX = 256;

/** The look a character is drawn in: an appearance and the version that keys it. */
export interface ShownLook {
  appearance: Appearance;
  version: number;
}

/**
 * Spells L20: the look to draw per character: the effects' own (Mimic Draught)
 * or the character's, with Tinctura's colour dyed on (`recolouredLook`).
 * Each combination keeps one appearance object and one version while it
 * lasts, so the renderer assembles it once (sharing the geometry with anyone
 * else in the same look), not every frame.
 */
export class RecolouredLooks {
  private readonly byId = new Map<number, { srcVersion: number; colour: number; out: ShownLook }>();
  private serial = 0;

  /**
   * What to draw `id` in: `look` carries the effects' appearance (or null)
   * and Tinctura's colour (or null); `appearance`/`version` are the
   * character's own. Null: draw the character as it is.
   */
  shown(id: number, look: { appearance: Appearance | null; version: number; recolour: number | null } | null, appearance: Appearance, version: number): ShownLook | null {
    // (The effects' own look object itself: no allocation per frame.)
    const base = look?.appearance ? (look as ShownLook) : null;
    if (look?.recolour == null) {
      this.byId.delete(id);
      return base;
    }
    const src = base ?? { appearance, version };
    const known = this.byId.get(id);
    if (known && known.srcVersion === src.version && known.colour === look.recolour) return known.out;
    const dyed = recolouredLook(src.appearance, look.recolour);
    if (dyed === src.appearance) {
      this.byId.delete(id);
      return base;
    }
    if (this.byId.size >= RECOLOURED_MAX) this.byId.clear();
    const out = { appearance: dyed, version: RECOLOUR_VERSION_BASE - ++this.serial };
    this.byId.set(id, { srcVersion: src.version, colour: look.recolour, out });
    return out;
  }
}

/** Whether a summary draws anything beyond the character itself. */
export function hasParticles(s: FxSummary): boolean {
  return s.glow !== null || s.lucky || s.steam;
}

// ------------------------------------------------------------------ our own timers

export interface OwnTimer {
  kind: EffectKind;
  /** Which fortune (the `fortune` kind only). */
  fortune: FortuneKind | null;
  /** Local time (ms, `performance.now()`) it runs out. */
  endsAt: number;
  /** Its full length (s), for the HUD's progress ring. */
  totalSecs: number;
}

export interface TimerView {
  kind: EffectKind;
  fortune: FortuneKind | null;
  remainingSecs: number;
  /** 1 when freshly drunk, 0 when it runs out. */
  fraction: number;
}

/**
 * Our own effects from `OwnEffects`. The server sends the remaining whole
 * seconds when anything changes (drink, expiry, relog); in between the
 * client counts down itself. An effect the client counted down to zero is
 * shown until the server's message removes it, at most a second later.
 */
export class OwnEffects {
  private timers: OwnTimer[] = [];
  speedPct = SPEED_PCT_NORMAL;
  /** The broom's speed percent (`ownEffects.broomPct`: Featherlight Elixir), 100 without. */
  broomPct = BROOM_PCT_NORMAL;
  revision = 0;

  set(effects: readonly { kind: EffectKind; remainingSecs: number; fortune?: FortuneKind | null }[], speedPct: number, nowMs: number, broomPct = BROOM_PCT_NORMAL): void {
    this.timers = effects
      .map((e) => {
        const total = Math.max(effectDuration(e.kind), e.remainingSecs);
        return { kind: e.kind, fortune: e.fortune ?? null, endsAt: nowMs + e.remainingSecs * 1000, totalSecs: total };
      })
      .sort((a, b) => displayOrder(a.kind) - displayOrder(b.kind));
    // Spells L20: 0 is a speed (Rigora: stiff as a board).
    this.speedPct = Number.isFinite(speedPct) && speedPct >= 0 ? speedPct : SPEED_PCT_NORMAL;
    this.broomPct = Number.isFinite(broomPct) && broomPct > 0 ? broomPct : BROOM_PCT_NORMAL;
    this.revision++;
  }

  clear(): void {
    this.timers = [];
    this.speedPct = SPEED_PCT_NORMAL;
    this.broomPct = BROOM_PCT_NORMAL;
    this.revision++;
  }

  has(kind: EffectKind): boolean {
    return this.timers.some((t) => t.kind === kind);
  }

  /** Seconds left of `kind` at `nowMs` (rounded up; 0 when not active or run out). */
  remaining(kind: EffectKind, nowMs: number): number {
    const t = this.timers.find((x) => x.kind === kind);
    return t ? remainingSecs(t, nowMs) : 0;
  }

  /** Every active timer at `nowMs`, in display order. */
  view(nowMs: number): TimerView[] {
    return this.timers.map((t) => {
      const left = remainingSecs(t, nowMs);
      return { kind: t.kind, fortune: t.fortune, remainingSecs: left, fraction: t.totalSecs > 0 ? Math.min(1, left / t.totalSecs) : 0 };
    });
  }

  get count(): number {
    return this.timers.length;
  }
}

/** Potions in their order, then anything else (a fortune). */
function displayOrder(kind: EffectKind): number {
  const i = EFFECT_KINDS.indexOf(kind);
  return i < 0 ? EFFECT_KINDS.length : i;
}

function remainingSecs(t: OwnTimer, nowMs: number): number {
  return Math.max(0, Math.ceil((t.endsAt - nowMs) / 1000 - 1e-9));
}

/**
 * A Glow Draught's colour as its aura draws it (sRGB 0..1): the potion's
 * dye, more saturated and at full brightness, so a muted dye still glows and
 * a pale one still has a hue (the stylized, saturated look); near black
 * becomes a cool white glow.
 */
export function glowColour(rgb: readonly [number, number, number]): [number, number, number] {
  const max = Math.max(rgb[0], rgb[1], rgb[2]);
  if (max <= 0.02) return [0.85, 0.87, 1];
  const grey = (rgb[0] + rgb[1] + rgb[2]) / 3;
  const vivid = rgb.map((c) => Math.max(0, grey + (c - grey) * 1.6));
  const top = Math.max(vivid[0]!, vivid[1]!, vivid[2]!, 0.001);
  return [vivid[0]! / top, vivid[1]! / top, vivid[2]! / top];
}

/** "14:59", "1:05", "9 s": what an effect timer shows. */
export function formatRemaining(secs: number): string {
  const s = Math.max(0, Math.ceil(secs));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}
