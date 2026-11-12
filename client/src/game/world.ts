// Loads the sim-wasm module and the walkable layouts: the castle
// (data/world/layout.json) and the instance layouts (data/world/instances/,
// the dorm shells): the same files the server validates movement against.
//
// Worlds are built for the player's house (`new SimWorld(layout, house)`):
// surfaces gated to other houses (their common rooms) are walls, exactly as
// the server enforces, so prediction never walks into a correction.

import type { Seats } from "./castle/seats";
import { FlightBounds } from "./flight-bounds";
import init, { SimWorld, moveParams, protocolConstants, type House, type MoveParams, type ProtocolConstants } from "../wasm/sim_wasm";
// Vite serves the source files in dev and emits content-hashed copies in
// builds, so the layouts are never duplicated in git.
import layoutUrl from "../../../data/world/layout.json?url";
// The broom rules (speeds, climb, clearance), the same file the server checks flights with.
import flyingText from "../../../data/flying.json?raw";
// The hidden passages and the Wishing Room's rules (the castle only).
import secretsText from "../../../data/world/secrets.json?raw";

const instanceUrls = import.meta.glob<string>("../../../data/world/instances/*.json", { query: "?url", import: "default", eager: true });

export interface SurfaceMeta {
  name: string;
  kind: string;
  level: number;
}

/** The parts of a layout file the client reads besides what `SimWorld` exposes. */
export interface LayoutSurface {
  id: string;
  name?: string;
  kind: string;
  level: number;
  cell?: string;
  min: [number, number];
  max: [number, number];
  y?: number;
  ceiling?: number;
  /** Stairs and slopes: the height at the top and the way it rises (`+x`, `-z`, ...). */
  y_top?: number;
  rise?: string;
  /** A moving staircase flight's position (`data/world/stairs.json`). */
  moving?: { flight: string; position: "a" | "b" };
  /** A common room's surfaces (and its door): the house they belong to (lower-case). */
  house?: string;
}

export interface WorldData {
  sim: SimWorld;
  params: MoveParams;
  protocol: ProtocolConstants;
  /** Surface id → display metadata, for the HUD's "where am I". */
  surfaces: Map<string, SurfaceMeta>;
  /** The layout's surfaces as written (instance scenes draw a fallback shell from them). */
  layoutSurfaces: LayoutSurface[];
  /** The layout file's text (the castle's wayfinding graph is built from it). */
  layoutText: string;
  /** A room's seats for the sit emote (instances.json `seats`; the castle's come from its cells). */
  seats?: Seats | null;
  /** A room's floor seats (rugs, cushions: the Gathering Room's), from its manifest cells. */
  floorSeats?: Seats | null;
  /** Where the flight camera may sit (the layout's airspace and no-fly boxes); null without airspace. */
  flightBounds: FlightBounds | null;
}

let wasmReady: Promise<unknown> | null = null;

async function fetchText(url: string, what: string): Promise<string> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`could not load ${what} (${r.status})`);
  return r.text();
}

function buildWorld(layoutText: string, house: House | undefined): WorldData {
  const sim = new SimWorld(layoutText, house);
  // Before any flight call (worlds without open sky never fly, but the rules cost nothing).
  sim.setFlightRules(flyingText);
  const surfaces = new Map<string, SurfaceMeta>();
  const layout = JSON.parse(layoutText) as { surfaces: LayoutSurface[]; areas?: unknown };
  for (const s of layout.surfaces) surfaces.set(s.id, { name: s.name ?? "", kind: s.kind, level: s.level });
  return { sim, params: moveParams(), protocol: protocolConstants(), surfaces, layoutSurfaces: layout.surfaces, layoutText, flightBounds: FlightBounds.fromAreas(layout.areas) };
}

/** The castle, as `house` walks it (no house: every gate open, for tools and tests). */
export async function loadWorldData(house?: House): Promise<WorldData> {
  wasmReady ??= init();
  const [, layoutText] = await Promise.all([wasmReady, fetchText(layoutUrl, "the castle layout")]);
  const world = buildWorld(layoutText, house);
  // The map's reveal rule and the secrets (never named until found).
  try {
    world.sim.setSecrets(secretsText);
  } catch (e) {
    console.error("The castle's secrets are unavailable:", e);
  }
  return world;
}

/** Whether `layoutId` (e.g. `"dorm_skytalon"`) is an instance layout this build ships. */
export function hasInstanceLayout(layoutId: string): boolean {
  return instanceUrl(layoutId) !== undefined;
}

function instanceUrl(layoutId: string): string | undefined {
  return /^[a-z0-9_]+$/.test(layoutId) ? instanceUrls[`../../../data/world/instances/${layoutId}.json`] : undefined;
}

/** An instance (`Zone::Dorm.layout`): data/world/instances/<layoutId>.json. */
export async function loadInstanceWorld(layoutId: string, house?: House): Promise<WorldData> {
  const url = instanceUrl(layoutId);
  if (!url) throw new Error(`unknown instance layout ${layoutId}`);
  wasmReady ??= init();
  const [, layoutText] = await Promise.all([wasmReady, fetchText(url, `the ${layoutId} layout`)]);
  return buildWorld(layoutText, house);
}
