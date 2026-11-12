// A hat over a hairstyle (docs/art/character-adoption.md section 4.4):
// the hair stays at its full volume right up to the brim. The hair build
// computes, per hairstyle x hat, the hat's cover line (how far down its
// crown reaches round the head) and the scale about the head centre that
// lets the hat clear the full hair left under it; the art manifest carries
// them (`hat_fit`, per body, on the hair's entry). Here the hair the hat's
// crown covers is dropped (its inner cap, `_SCALP`, stays: the shade under
// the hat) and the hat is scaled. A scale is capped (1.15); where the hair
// needs more, the hair still under the crown is pressed in to the hat's
// recorded reach (`limit`), so the brim sits a little into the hair. All of
// it in the bind pose, once per look (the library), never per frame.

import type { PartData, PartPrimitive } from "./parts";

/** One hairstyle x hat fit, ready to apply. */
export interface HatFit {
  /** Head centre, glTF rest frame (metres, +Y up, facing +Z). */
  centre: [number, number, number];
  /** The hat's scale about `centre` (1 = as built). */
  scale: number;
  /** Polar angle (degrees from straight up) the crown covers down to, per `step` degrees of azimuth from straight ahead toward the left. */
  cover: number[];
  step: number;
  /** Degrees inside the cover line past which hair is dropped. */
  margin: number;
  /** Capped hats: how far from `centre` the hair under the crown may reach, per row of `limitRows` (degrees above the cover line) x azimuth sample; 0 = no hat there. */
  limit: number[][] | null;
  limitRows: number[];
}

/** `hat_fit[body]` of a hair's manifest entry, as written by the hair build. */
export interface HatFitTable {
  centre?: unknown;
  cover_step?: unknown;
  cut_margin?: unknown;
  limit_rows?: unknown;
  hats?: Record<string, { scale?: unknown; cover?: unknown; limit?: unknown }>;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The fit of `hat` over a hair whose manifest entry has `table` for this body, or null when there is none (or it is malformed). */
export function hatFitFor(table: HatFitTable | undefined, hat: string): HatFit | null {
  if (!table || typeof table !== "object") return null;
  const c = table.centre;
  const h = table.hats?.[hat];
  if (!Array.isArray(c) || c.length !== 3 || !c.every(finite) || !h) return null;
  const step = table.cover_step;
  const margin = table.cut_margin;
  if (!finite(step) || step <= 0 || !finite(margin) || !finite(h.scale) || !Array.isArray(h.cover)) return null;
  const cover = h.cover;
  if (cover.length !== Math.round(360 / step) || !cover.every(finite)) return null;
  // A scale outside 1..2 is not a fit the build writes.
  const scale = Math.min(2, Math.max(1, h.scale));
  const rows = table.limit_rows;
  const lim = h.limit;
  const okRows = Array.isArray(rows) && rows.length >= 2 && rows.every(finite);
  const okLimit =
    okRows && Array.isArray(lim) && lim.length === rows.length && lim.every((r) => Array.isArray(r) && r.length === cover.length && r.every(finite));
  return {
    centre: [c[0], c[1], c[2]],
    scale,
    cover: cover as number[],
    step,
    margin,
    limit: okLimit ? (lim as number[][]) : null,
    limitRows: okRows ? (rows as number[]) : [],
  };
}

/** The cover line at azimuth `theta` (degrees), interpolated round the head. */
export function coverAt(fit: HatFit, theta: number): number {
  const n = fit.cover.length;
  const t = (((theta % 360) + 360) % 360) / fit.step;
  const i = Math.floor(t);
  const f = t - i;
  return fit.cover[i % n]! * (1 - f) + fit.cover[(i + 1) % n]! * f;
}

/** Degrees a rest-frame point lies above the hat's cover line (positive: under the crown), with its distance from the centre. */
export function aboveCover(fit: HatFit, x: number, y: number, z: number): { up: number; r: number; theta: number } {
  const dx = x - fit.centre[0];
  const dy = y - fit.centre[1];
  const dz = z - fit.centre[2];
  const r = Math.hypot(dx, dy, dz);
  if (r < 1e-9) return { up: -180, r, theta: 0 };
  const phi = (Math.acos(Math.max(-1, Math.min(1, dy / r))) * 180) / Math.PI;
  const theta = (Math.atan2(dx, dz) * 180) / Math.PI;
  return { up: coverAt(fit, theta) - phi, r, theta };
}

/** Whether a rest-frame point lies more than the fit's margin inside the hat's cover. */
export function coveredDeep(fit: HatFit, x: number, y: number, z: number): boolean {
  return aboveCover(fit, x, y, z).up > fit.margin;
}

/** A capped hat's reach at azimuth `theta`, `up` degrees above the cover line (interpolated; 0 = no limit). */
export function limitAt(fit: HatFit, theta: number, up: number): number {
  const lim = fit.limit;
  if (!lim) return 0;
  const rows = fit.limitRows;
  const n = fit.cover.length;
  const t = (((theta % 360) + 360) % 360) / fit.step;
  const i0 = Math.floor(t) % n;
  const i1 = (i0 + 1) % n;
  const ft = t - Math.floor(t);
  const u = Math.min(rows[rows.length - 1]!, Math.max(rows[0]!, up));
  let j = 0;
  while (j < rows.length - 2 && u >= rows[j + 1]!) j++;
  const fu = (u - rows[j]!) / (rows[j + 1]! - rows[j]!);
  const a = lim[j]![i0]! * (1 - ft) + lim[j]![i1]! * ft;
  const b = lim[j + 1]![i0]! * (1 - ft) + lim[j + 1]![i1]! * ft;
  return a > 0 && b > 0 ? a * (1 - fu) + b * fu : Math.max(a, b);
}

function fitPrimitive(p: PartPrimitive, fit: HatFit): PartPrimitive {
  const n = p.positions.length / 3;
  const deep = new Uint8Array(n);
  const up = new Float32Array(n);
  const theta = new Float32Array(n);
  const rad = new Float32Array(n);
  for (let v = 0; v < n; v++) {
    const a = aboveCover(fit, p.positions[v * 3]!, p.positions[v * 3 + 1]!, p.positions[v * 3 + 2]!);
    up[v] = a.up;
    theta[v] = a.theta;
    rad[v] = a.r;
    if (p.scalp && p.scalp[v]) continue;
    deep[v] = a.up > fit.margin ? 1 : 0;
  }
  const out: number[] = [];
  const idx = p.indices;
  const used = new Uint8Array(n);
  for (let k = 0; k + 2 < idx.length; k += 3) {
    const a = idx[k]!;
    const b = idx[k + 1]!;
    const c = idx[k + 2]!;
    if (deep[a] && deep[b] && deep[c]) continue;
    out.push(a, b, c);
    used[a] = used[b] = used[c] = 1;
  }
  // A capped hat: the hair left under its crown pressed in to its reach.
  let positions = p.positions;
  if (fit.limit) {
    for (let v = 0; v < n; v++) {
      if (!used[v] || (p.scalp && p.scalp[v]) || up[v] <= 0) continue;
      const lim = limitAt(fit, theta[v]!, up[v]!);
      if (lim <= 0 || rad[v]! <= lim) continue;
      if (positions === p.positions) positions = Float32Array.from(p.positions);
      const k = lim / rad[v]!;
      for (let i = 0; i < 3; i++) positions[v * 3 + i] = fit.centre[i]! + (p.positions[v * 3 + i]! - fit.centre[i]!) * k;
    }
  }
  if (out.length === idx.length && positions === p.positions) return p;
  return { ...p, positions, indices: out.length === idx.length ? idx : Uint32Array.from(out) };
}

/** The hair under a hat, every LOD: the triangles wholly under the crown dropped (the inner cap kept) and, under a capped hat, the rest under the crown pressed in. */
export function fitHair(part: PartData, fit: HatFit): PartData {
  const primitives = part.primitives.map((p) => fitPrimitive(p, fit));
  return primitives.every((p, i) => p === part.primitives[i]) ? part : { ...part, primitives };
}

/** The hat scaled about the head centre (positions and morph offsets; directions are unchanged by a uniform scale). */
export function scaleHat(part: PartData, fit: HatFit): PartData {
  if (fit.scale === 1) return part;
  const [cx, cy, cz] = fit.centre;
  const s = fit.scale;
  const primitives = part.primitives.map((p) => {
    const pos = new Float32Array(p.positions.length);
    for (let k = 0; k < pos.length; k += 3) {
      pos[k] = cx + (p.positions[k]! - cx) * s;
      pos[k + 1] = cy + (p.positions[k + 1]! - cy) * s;
      pos[k + 2] = cz + (p.positions[k + 2]! - cz) * s;
    }
    const morphs = new Map(
      [...p.morphs].map(([name, m]) => [name, { ...m, positions: m.positions.map((d) => d * s) }] as const),
    );
    return { ...p, positions: pos, morphs };
  });
  return { ...part, primitives };
}
