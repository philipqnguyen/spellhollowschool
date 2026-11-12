// Potions without the DOM: names and words for effects (names come from the
// catalog's potions, so IP names stay in data files), the potions in the
// bag, who a Mimic Draught can copy, the consent requests waiting for an
// answer, and friendlier error text. Pure, unit tested in potions.test.ts.

import economyJson from "../../../data/economy.json";
import { CATALOG, type Catalog } from "../content/catalog";
import type { EffectKind, FortuneKind, PotionErrorCode } from "../wasm/sim_wasm";
import { effectDuration } from "../game/effects/state";
import type { BagItem } from "./crafting-api";
import type { ItemDef1c } from "./crafting-content";

/** Mimic Draught copies a friend within this distance (m), as the server checks. */
export const MIMIC_DRAUGHT_RANGE_M: number = economyJson.effects.mimic_draught.range_m;

/** The catalog name of the potion that gives `kind` ("Chameleon Tonic" without its part). */
export function effectName(kind: EffectKind, catalog: Catalog = CATALOG): string {
  const potion = (catalog.items as ItemDef1c[]).find((i) => i.kind === "potion" && i.potion?.effect === kind);
  if (!potion) return kind;
  return kind === "chameleon" ? potion.name.replace(/\s*\([^)]*\)\s*$/, "") : potion.name;
}

/** What an effect does, in a few words (no IP names: those are the potions' names). */
export function effectBlurb(kind: EffectKind): string {
  switch (kind) {
    case "glow":
      return "A soft coloured aura around you.";
    case "chameleon":
      return "Your hair or robes cycle through a family of colours.";
    case "shrinking":
      return "You are half your size.";
    case "swelling":
      return "You are a good deal bigger.";
    case "babbling":
      return "Everything you say comes out scrambled.";
    case "mimic_draught":
      return "You look exactly like a consenting friend (your name stays yours).";
    case "fortunes_draught":
      return "Luck is on your side: better exploration finds.";
    case "pick_me_up_tonic":
      return "Steam from your ears and a spring in your step.";
    case "invisibility":
      return "Your name is hidden from everyone (you are still seen).";
    case "fortune":
      return "A small fortune from Divination, seen only by you.";
    case "featherlight":
      return `Your broom flies ${economyJson.effects.featherlight.broom_pct - 100} % faster.`;
    // Spells L20 (SOCIAL): the spell effects (never potions; ui/effects.ts words their chips).
    case "petrified":
      return "Stiff as a board: you cannot walk.";
    case "slowed":
      return "Everything you do is slowed down.";
    case "recoloured":
      return "Your robe shows another colour.";
    case "guardian":
      return "Your silver guardian is at your side.";
  }
}

/** A Divination fortune's name (invented names). */
export function fortuneName(kind: FortuneKind): string {
  switch (kind) {
    case "luck":
      return "Lucky Find";
    case "quick_hands":
      return "Quick Hands";
    case "keen_mind":
      return "Keen Mind";
  }
}

/** What a fortune does, in a few words. */
export function fortuneBlurb(kind: FortuneKind): string {
  switch (kind) {
    case "luck":
      return "Exploration finds turn up rarer things a little more often.";
    case "quick_hands":
      return "Your crafts and brews finish sooner.";
    case "keen_mind":
      return "A little more bonus XP from your classes.";
  }
}

/** "15 min", "5 min", "90 s": how long a potion lasts. */
export function durationText(kind: EffectKind): string {
  const s = effectDuration(kind);
  return s >= 60 ? `${Math.round(s / 60)} min` : `${s} s`;
}

/** The effect a catalog potion gives, or null for anything else. */
export function potionEffect(itemDef: number, catalog: Catalog = CATALOG): EffectKind | null {
  const d = (catalog.items as ItemDef1c[]).find((i) => i.id === itemDef);
  return d?.kind === "potion" ? (d.potion?.effect ?? null) : null;
}

/** Potion stacks in the inventory, grouped by definition and dyes (a Glow colour is its own kind). */
export interface PotionStack {
  /** A stack to drink from (the server picks by item id). */
  itemId: number;
  itemDef: number;
  effect: EffectKind;
  /** Primary dye (Glow colour, Chameleon family). */
  colour: number;
  count: number;
}

export function potionStacks(items: readonly BagItem[], catalog: Catalog = CATALOG): PotionStack[] {
  const by = new Map<string, PotionStack>();
  for (const i of items) {
    if (i.location !== "inventory") continue;
    const effect = potionEffect(i.item_def, catalog);
    if (!effect) continue;
    const key = `${i.item_def}:${i.dyes[0]}`;
    const e = by.get(key);
    if (e) e.count += i.quantity;
    else by.set(key, { itemId: i.id, itemDef: i.item_def, effect, colour: i.dyes[0], count: i.quantity });
  }
  return [...by.values()].sort((a, b) => a.itemDef - b.itemDef || a.colour - b.colour);
}

/** A character in view who a Mimic Draught could copy. */
export interface MimicDraughtCandidate {
  id: number;
  name: string;
  distance: number;
}

/** Online friends in view within range, nearest first (the server checks again). */
export function mimicDraughtCandidates(nearby: readonly { id: number; name: string; distance: number }[], isFriend: (name: string) => boolean, range = MIMIC_DRAUGHT_RANGE_M): MimicDraughtCandidate[] {
  return nearby.filter((c) => c.distance <= range && isFriend(c.name)).sort((a, b) => a.distance - b.distance || a.name.localeCompare(b.name));
}

/** A Mimic Draught consent request waiting for our answer. */
export interface ConsentRequest {
  requestId: number;
  fromName: string;
  /** Local time (ms) it lapses. */
  expiresAt: number;
}

/** Pending consent requests: newest per asker, lapsing on their own. */
export class ConsentQueue {
  private list: ConsentRequest[] = [];

  add(requestId: number, fromName: string, expiresInSecs: number, nowMs: number): ConsentRequest {
    this.list = this.list.filter((r) => r.fromName !== fromName && r.requestId !== requestId);
    const r = { requestId, fromName, expiresAt: nowMs + expiresInSecs * 1000 };
    this.list.push(r);
    return r;
  }

  remove(requestId: number): void {
    this.list = this.list.filter((r) => r.requestId !== requestId);
  }

  /** Drops and returns the lapsed requests. */
  expire(nowMs: number): ConsentRequest[] {
    const gone = this.list.filter((r) => r.expiresAt <= nowMs);
    if (gone.length) this.list = this.list.filter((r) => r.expiresAt > nowMs);
    return gone;
  }

  get items(): readonly ConsentRequest[] {
    return this.list;
  }
}

export function potionErrorText(code: PotionErrorCode): string {
  switch (code) {
    case "item_not_found":
      return "That potion is no longer in your bag.";
    case "not_a_potion":
      return "That is not something you can drink.";
    case "needs_target":
      return "Mimic Draught needs a friend to copy: choose one nearby.";
    case "not_friend":
      return "Mimic Draught can only copy a friend.";
    case "too_far":
      return "Your friend needs to be close by.";
    case "not_online":
      return "Your friend is not online.";
    case "expired":
      return "That request has lapsed.";
    case "disabled":
      return "Potions have no effect here.";
    case "busy":
      return "There is already a request waiting for an answer.";
    case "rate_limited":
      return "Slow down: one sip at a time.";
    case "failed":
      return "The potion fizzled. Please try again.";
  }
}
