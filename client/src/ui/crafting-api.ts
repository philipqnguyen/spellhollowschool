// The crafting side's HTTP endpoints (docs/architecture.md, "HTTP API"):
// recipes, learning patterns, crafting,
// skills, finishes, find cooldowns and active effects, plus the inventory
// reads they need (materials, dyes, potions, patterns by stack).
//
// Every list is a keyset page `{ items, next }`; `allPages` follows `next`
// for the short lists a window needs whole (a discipline's recipes, the
// stacks of one kind), with a hard cap so a misbehaving server cannot make
// it loop forever.

import { authedRequest } from "../api";
import type { Dyes } from "../content/catalog";
import type { EffectKind, Finish, GearSlot } from "../wasm/sim_wasm";
import type { Discipline, RecipeView } from "./crafting-content";

/** `Item` in the API (the base shape plus quantity, maker and finish). */
export interface BagItem {
  id: number;
  item_def: number;
  dyes: Dyes;
  location: "inventory" | "equipped" | "dorm" | "mail" | "exchange";
  slot: GearSlot | null;
  quantity: number;
  maker: string | null;
  finish: Finish;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  next: string | null;
}

export type BagKind = "clothing" | "furniture" | "material" | "dye" | "finish" | "potion" | "pattern";

export interface SkillView {
  discipline: Discipline;
  /** The class subject (the nine `Subject` ids), when the server names it so. */
  subject?: string;
  xp: number;
  level: number;
  next_level_xp: number | null;
}

export interface CraftResult {
  /** The stack that received the output, or the restyled garment (null when a replayed craft's output has since gone). */
  item: BagItem | null;
  skill: SkillView;
  /** RFC 3339: the crafter is busy until then. */
  ready_at: string;
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** At most this many pages are followed for one list. */
const PAGE_CAP = 20;
const PAGE_LIMIT = 100;

/** Every item of a keyset list, page by page (up to `PAGE_CAP` pages). */
export async function allPages<T>(fetchPage: (after: string | null) => Promise<Page<T>>): Promise<T[]> {
  const out: T[] = [];
  let after: string | null = null;
  for (let i = 0; i < PAGE_CAP; i++) {
    const page: Page<T> = await fetchPage(after);
    out.push(...page.items);
    if (!page.next || page.next === after) break;
    after = page.next;
  }
  return out;
}

/** One page of the inventory; `location` null = everywhere (worn and placed too). */
export function inventoryPage(kind: BagKind | undefined, after: string | null, limit = PAGE_LIMIT, location: string | null = "inventory"): Promise<Page<BagItem>> {
  return authedRequest("GET", `/inventory${query({ kind, location, limit, after })}`);
}

/** Every stack of `kind` in the inventory (materials, dyes, potions, patterns stack, so this is short). */
export function bagOf(kind: BagKind): Promise<BagItem[]> {
  return allPages((after) => inventoryPage(kind, after));
}

/** Units held per catalog id across stacks. */
export function countByDef(items: readonly BagItem[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const i of items) if (i.location === "inventory") out.set(i.item_def, (out.get(i.item_def) ?? 0) + Math.max(1, i.quantity ?? 1));
  return out;
}

export function recipesPage(discipline: Discipline | undefined, after: string | null, known?: boolean): Promise<Page<RecipeView>> {
  return authedRequest("GET", `/recipes${query({ discipline, known, limit: PAGE_LIMIT, after })}`);
}

export function allRecipes(discipline: Discipline): Promise<RecipeView[]> {
  return allPages((after) => recipesPage(discipline, after));
}

export function learn(itemId: number): Promise<{ recipe_id: number }> {
  return authedRequest("POST", "/recipes/learn", { item_id: itemId });
}

export interface CraftRequest {
  recipe_id: number;
  dyes?: Dyes;
  item_id?: number;
  request_id?: string;
}

export function craft(req: CraftRequest): Promise<CraftResult> {
  return authedRequest("POST", "/craft", req);
}

export async function skills(): Promise<SkillView[]> {
  return (await authedRequest<{ skills: SkillView[] }>("GET", "/skills")).skills;
}

export function finish(itemId: number, finishItemId: number): Promise<BagItem> {
  return authedRequest("POST", `/items/${itemId}/finish`, { finish_item_id: finishItemId });
}

export interface FindCooldown {
  spot: string;
  ready_at: string;
}

export function findCooldowns(): Promise<FindCooldown[]> {
  return allPages((after) => authedRequest<Page<FindCooldown>>("GET", `/finds${query({ limit: PAGE_LIMIT, after })}`));
}

export interface EffectRow {
  kind: EffectKind;
  item_def: number;
  started_at: string;
  expires_at: string;
}

export async function activeEffects(): Promise<EffectRow[]> {
  return (await authedRequest<{ effects: EffectRow[] }>("GET", "/effects")).effects;
}

/** A fresh `request_id` for a retryable write (1-64 chars, unique per character). */
export function requestId(prefix: string): string {
  const rand = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${rand}`.slice(0, 64);
}
