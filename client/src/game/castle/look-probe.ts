// The castle depth pass's look probe (castle D1, docs/art/castle-kit.md):
// numbers that say whether the castle reads as 3D, measured on the
// rendered frame of fixed framings (Medium, noon) and checked against
// targets calibrated on the study references (grid.py "the A9 look probe").
//
// A probe is a set of world-space samples per framing. Each sample is a short
// ray that finds the real surface (a wall 0.3 m above the floor beside an
// inner corner, a gallery soffit near its wall, a gallery floor...): the
// probe (perf-probe.ts `look`) casts it against the castle's meshes, keeps the
// hit when it lies where the sample expects the surface (not a prop in front
// of it) and faces the right way, checks the camera sees that very point,
// and reads the rendered pixels there (characters hidden for that frame).
// This file holds the framings, the samples and the arithmetic; it is pure
// and unit tested. The numbers:
//
// - corner: wall pixels 0.3 m above the floor, 0.3 m from an inner corner,
//   over the same wall at mid-wall (no corner, shaft or pier within 1.5 m):
//   PROBE_CORNER_RATIO;
// - soffit: gallery soffit over gallery floor (the Grand Staircase):
//   PROBE_SOFFIT_RATIO; soffit within 1 m of its wall over at its free edge:
//   PROBE_SOFFIT_GRADE;
// - spread: a flat wall's luminance from its floor band to its cornice band,
//   (top - bottom) / top: at least PROBE_WALL_SPREAD;
// - tint: the light on undersides, t = rendered / albedo (per channel,
//   linear, means over the soffit pixels; the albedo is the mean of the
//   material's texture): HSV saturation below PROBE_TINT_SAT or hue outside
//   PROBE_BROWN_HUE (the brown ground bounce coloured it before D1).
//
// Ratios are of mean linear luminance (Rec. 709), as the calibration.

export type Vec3 = [number, number, number];

/** grid.py "the A9 look probe" (castle_kit/grid.py; a vitest keeps them equal). */
export const PROBE_CORNER_RATIO: [number, number] = [0.65, 0.85];
export const PROBE_SOFFIT_RATIO: [number, number] = [0.45, 0.75];
export const PROBE_SOFFIT_GRADE: [number, number] = [0.6, 0.92];
export const PROBE_WALL_SPREAD = 0.06;
export const PROBE_TINT_SAT = 0.08;
export const PROBE_BROWN_HUE: [number, number] = [15, 50];

/**
 * One sample: cast from `from` along `dir` (unit) for up to `max` metres; the
 * hit counts when it is between `near` and `far` metres along the ray (where
 * the surface is expected; anything nearer is a prop in front of it) and its
 * face normal points within about 45 degrees of `-dir` (the surface faces the
 * ray).
 */
export interface LookRay {
  from: Vec3;
  dir: Vec3;
  near: number;
  far: number;
  /** One wall (or one column of a wall): ratios and spreads are taken within a group, then averaged over the groups. */
  group?: string;
}

/** A named group of samples whose pixels are averaged. */
export interface LookSet {
  name: string;
  rays: LookRay[];
  /** Keep only hits on materials whose names start with one of these. */
  materials?: string[];
  /**
   * Mid-wall samples: drop a hit with any surface within this many metres
   * along the wall either way (a corner, a shaft, a pier, a jamb).
   */
  clear?: number;
}

export type LookMetric =
  | { kind: "corner"; corner: string; mid: string }
  | { kind: "soffit"; soffit: string; floor: string; view?: string }
  | { kind: "grade"; wall: string; edge: string }
  | { kind: "spread"; set: string }
  | { kind: "tint"; set: string };

/** A framing of 19.10 with its samples and the numbers they give. */
export interface LookView {
  id: string;
  description: string;
  sets: LookSet[];
  metrics: LookMetric[];
}

/** What the probe measured for one sample (perf-probe.ts). */
export interface LookSample {
  /** Linear Rec. 709 luminance of the pixels there. */
  lum: number;
  /** Linear rgb of the pixels there. */
  rgb: Vec3;
  /** Linear rgb mean of the hit material's texture, or null (not a texture-library material). */
  albedo: Vec3 | null;
  /** The hit point's height (m). */
  y: number;
  material: string;
  /** The ray's group (LookRay.group). */
  group?: string;
}

/** One set's samples, valid ones only, and why the others were dropped. */
export interface LookSetResult {
  name: string;
  samples: LookSample[];
  dropped: Record<string, number>;
}

// ------------------------------------------------------------ colour

/** sRGB byte (0..255) to linear 0..1. */
export function srgbToLinear(byte: number): number {
  const c = byte / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** Rec. 709 luminance of linear rgb (the calibration's weights). */
export function luminance(r: number, g: number, b: number): number {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** HSV hue (degrees, 0..360) and saturation of an rgb triple (any scale). */
export function hueSat(rgb: Vec3): { hue: number; sat: number } {
  const [r, g, b] = rgb;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  const sat = max > 0 ? d / max : 0;
  if (d <= 0) return { hue: 0, sat };
  let h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  h *= 60;
  return { hue: h < 0 ? h + 360 : h, sat };
}

// ------------------------------------------------------------ samples

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a: Vec3, k: number): Vec3 => [a[0] * k, a[1] * k, a[2] * k];

/**
 * A ray onto a wall: `p` on the wall's plane, `n` its normal into the room
 * (unit, horizontal). Cast from 1.5 m out; mouldings at the wall's foot (a
 * plinth, a wainscot) stand up to 0.35 m proud of the plane.
 */
export function onWall(p: Vec3, n: Vec3, group?: string): LookRay {
  return { from: add(p, scale(n, 1.5)), dir: scale(n, -1), near: 1.15, far: 1.55, ...(group ? { group } : {}) };
}

/** A ray up onto a soffit whose underside is about `p` (its height a guess within 0.6 m; slabs and fascias vary). */
export function onSoffit(p: Vec3): LookRay {
  return { from: sub(p, [0, 2, 0]), dir: [0, 1, 0], near: 1.4, far: 2.6 };
}

/** A ray down onto a floor at `p`. */
export function onFloor(p: Vec3): LookRay {
  return { from: add(p, [0, 1, 0]), dir: [0, -1, 0], near: 0.85, far: 1.15 };
}

/** A column of wall rays from `y0` to `y1` every `step` metres at `x, z` on a wall with normal `n`. */
export function wallColumn(x: number, z: number, n: Vec3, y0: number, y1: number, step = 0.25): LookRay[] {
  const out: LookRay[] = [];
  for (let y = y0; y <= y1 + 1e-6; y += step) out.push(onWall([x, y, z], n, `${x},${z}`));
  return out;
}

/** Corner samples: 0.2-0.4 m above `floor`, 0.25-0.35 m along `along` from the inner corner at `c` (x, z), on the wall with normal `n`. */
export function cornerRays(c: [number, number], floor: number, along: Vec3, n: Vec3, group: string): LookRay[] {
  const out: LookRay[] = [];
  for (const d of [0.25, 0.3, 0.35]) for (const h of [0.2, 0.3, 0.4]) out.push(onWall([c[0] + along[0] * d, floor + h, c[1] + along[2] * d], n, group));
  return out;
}

/** Mid-wall samples at the corner samples' heights at each point (x, z) of a wall with normal `n`. */
export function midRays(points: [number, number][], floor: number, n: Vec3, group: string): LookRay[] {
  return points.flatMap(([x, z]) => [0.2, 0.3, 0.4].map((h) => onWall([x, floor + h, z], n, group)));
}

const range = (a: number, b: number, step: number): number[] => {
  const out: number[] = [];
  for (let v = a; v <= b + 1e-6; v += step) out.push(Math.round(v * 1000) / 1000);
  return out;
};

// ------------------------------------------------------------ framings

const N_PX: Vec3 = [1, 0, 0];
const N_NX: Vec3 = [-1, 0, 0];
const N_PZ: Vec3 = [0, 0, 1];
const N_NZ: Vec3 = [0, 0, -1];
const WALLS = ["castle_wall"];

/**
 * The Grand Staircase's gallery soffits (layout.json: galleries at y 9k round
 * the shaft x -66..-30, z -16..20; north and south 4.5 m deep, west and east
 * 7.75 m): within 1 m of the outer wall and 0.75 m inside the free edge, for
 * levels `levels`.
 */
function galleries(levels: number[]): { wall: LookRay[]; edge: LookRay[] } {
  const wall: LookRay[] = [];
  const edge: LookRay[] = [];
  for (const k of levels) {
    const y = 9 * k - 0.4;
    for (const x of range(-62, -34, 4)) {
      wall.push(onSoffit([x, y, -15.2]), onSoffit([x, y, 19.2]));
      edge.push(onSoffit([x, y, -12.25]), onSoffit([x, y, 16.25]));
    }
    for (const z of range(-8, 12, 4)) {
      wall.push(onSoffit([-65.2, y, z]), onSoffit([-30.8, y, z]));
      edge.push(onSoffit([-59.0, y, z]), onSoffit([-37.0, y, z]));
    }
  }
  return { wall, edge };
}

const gsUp = galleries([2, 3, 4, 5, 6]);
const gsL3 = galleries([4]);

/** The framings of 19.10 (their cameras: VIEWS; `look` measures whatever the camera shows). */
export const LOOK_VIEWS: LookView[] = [
  {
    id: "gf_eye",
    description: "gf_corridor_west at eye level, looking west (x -28..-2, z 4..13, floor 0)",
    sets: [
      {
        name: "corner",
        // The side walls at the west end wall (whose middle is the arch to the staircase).
        rays: [...cornerRays([-28, 13], 0, N_PX, N_NZ, "z13"), ...cornerRays([-28, 4], 0, N_PX, N_PZ, "z4")],
      },
      {
        name: "mid",
        rays: [...midRays(range(-25, -8, 1).map((x) => [x, 13]), 0, N_NZ, "z13"), ...midRays(range(-25, -8, 1).map((x) => [x, 4]), 0, N_PZ, "z4")],
        clear: 1.5,
      },
      { name: "wall_column", rays: range(-25, -8, 1.5).flatMap((x) => wallColumn(x, 13, N_NZ, 0.7, 8.4).concat(wallColumn(x, 4, N_PZ, 0.7, 8.4))), materials: WALLS, clear: 0.4 },
    ],
    metrics: [
      { kind: "corner", corner: "corner", mid: "mid" },
      { kind: "spread", set: "wall_column" },
    ],
  },
  {
    id: "eh_doors",
    description: "the Entrance Hall from the front doors, looking north (x 0..32, z 0..40, floor 0, springing 14)",
    sets: [
      {
        name: "corner",
        rays: [...cornerRays([0, 0], 0, N_PX, N_PZ, "z0"), ...cornerRays([32, 0], 0, N_NX, N_PZ, "z0")],
      },
      { name: "mid", rays: midRays([...range(2, 10, 1), ...range(22, 30, 1)].map((x) => [x, 0]), 0, N_PZ, "z0"), clear: 1.5 },
      { name: "wall_column", rays: [3, 6, 9, 23, 26, 29].flatMap((x) => wallColumn(x, 0, N_PZ, 0.7, 14)), materials: WALLS, clear: 0.4 },
    ],
    metrics: [
      { kind: "corner", corner: "corner", mid: "mid" },
      { kind: "spread", set: "wall_column" },
    ],
  },
  {
    id: "gh_long",
    description: "the Great Hall long view from the doors, looking east (x 34..100, z 9..31; the wainscot to 3.2 m, the cornice at 17.5)",
    // Columns 1.25 m inside each bay from its shafts, clear of the lancets (3.2 m wide, centred in the bays).
    sets: [{ name: "wall_column", rays: range(35.25, 92.75, 7.5).flatMap((x) => [x, x + 5]).flatMap((x) => wallColumn(x, 9, N_PZ, 3.4, 17.4).concat(wallColumn(x, 31, N_NZ, 3.4, 17.4))), materials: WALLS, clear: 0.4 }],
    metrics: [{ kind: "spread", set: "wall_column" }],
  },
  {
    id: "gh_side_bay",
    description: "a Great Hall side bay with braziers, looking north at the wall z 9",
    sets: [{ name: "wall_column", rays: range(37.75, 67.75, 1.5).flatMap((x) => wallColumn(x, 9, N_PZ, 3.4, 17.4)), materials: WALLS, clear: 0.4 }],
    metrics: [{ kind: "spread", set: "wall_column" }],
  },
  {
    id: "gs_up",
    description: "the Grand Staircase look-up from (-50.5, 1.6, 2) to the skylight: the gallery soffits of levels 2-6",
    sets: [
      { name: "soffit_wall", rays: gsUp.wall },
      { name: "soffit_edge", rays: gsUp.edge },
      { name: "soffit", rays: [...gsUp.wall, ...gsUp.edge] },
    ],
    metrics: [
      { kind: "grade", wall: "soffit_wall", edge: "soffit_edge" },
      { kind: "tint", set: "soffit" },
      // The gallery floors are not in the look-up: the L3 gallery's floor from gs_l3.
      { kind: "soffit", soffit: "soffit", floor: "floor", view: "gs_l3" },
    ],
  },
  {
    id: "gs_l3",
    description: "the Grand Staircase L3 gallery (y 27): its floor and the L4 soffit above",
    sets: [
      { name: "soffit_wall", rays: gsL3.wall },
      { name: "soffit_edge", rays: gsL3.edge },
      { name: "soffit", rays: [...gsL3.wall, ...gsL3.edge] },
      {
        name: "floor",
        rays: [
          ...range(-65, -59.5, 1.5).flatMap((x) => range(-10, 14, 2).map((z) => onFloor([x, 27, z]))),
          ...range(-56, -32, 2).flatMap((x) => [16.5, 18, 19.2].map((z) => onFloor([x, 27, z]))),
        ],
        materials: ["castle_floor"],
      },
    ],
    metrics: [
      { kind: "soffit", soffit: "soffit", floor: "floor" },
      { kind: "grade", wall: "soffit_wall", edge: "soffit_edge" },
      { kind: "tint", set: "soffit" },
    ],
  },
  {
    id: "gs_guardian",
    description: "l7_corridor_north at the guardian portrait (x -59..-38, z -28..-18, floor 63)",
    sets: [
      {
        name: "corner",
        rays: [...cornerRays([-59, -28], 63, N_PX, N_PZ, "z-28"), ...cornerRays([-38, -28], 63, N_NX, N_PZ, "z-28")],
      },
      { name: "mid", rays: midRays(range(-57, -40, 1).map((x) => [x, -28]), 63, N_PZ, "z-28"), clear: 1.5 },
      { name: "wall_column", rays: range(-57, -40, 1.5).flatMap((x) => wallColumn(x, -28, N_PZ, 63.7, 71.4)), materials: WALLS, clear: 0.4 },
    ],
    metrics: [
      { kind: "corner", corner: "corner", mid: "mid" },
      { kind: "spread", set: "wall_column" },
    ],
  },
];

export function lookView(id: string): LookView | undefined {
  return LOOK_VIEWS.find((v) => v.id === id);
}

// ------------------------------------------------------------ the numbers

export interface MetricResult {
  kind: LookMetric["kind"];
  /** The ratio, spread or saturation measured (null: too few samples). */
  value: number | null;
  /** For `tint`: the hue of t (degrees). */
  hue?: number;
  target: string;
  pass: boolean | null;
  /** Samples behind the value (the smaller set's count for ratios). */
  n: number;
}

/** Fewest valid samples a set needs to count. */
export const MIN_SAMPLES = 3;

const meanLum = (s: LookSample[]): number => s.reduce((a, v) => a + v.lum, 0) / Math.max(1, s.length);

/** Samples by group (one group "" when they have none). */
function byGroup(s: LookSample[]): Map<string, LookSample[]> {
  const m = new Map<string, LookSample[]>();
  for (const v of s) {
    const k = v.group ?? "";
    const list = m.get(k);
    if (list) list.push(v);
    else m.set(k, [v]);
  }
  return m;
}

/**
 * mean(a) / mean(b) within each group both sets have (at least MIN_SAMPLES
 * each), averaged over those groups: a corner against the middle of its own
 * wall, never of a wall the sun lights differently.
 */
export function groupedRatio(a: LookSample[], b: LookSample[]): { value: number | null; n: number } {
  const gb = byGroup(b);
  const ratios: number[] = [];
  let n = 0;
  for (const [g, sa] of byGroup(a)) {
    const sb = gb.get(g);
    if (!sb || sa.length < MIN_SAMPLES || sb.length < MIN_SAMPLES) continue;
    ratios.push(meanLum(sa) / Math.max(1e-6, meanLum(sb)));
    n += Math.min(sa.length, sb.length);
  }
  return { value: ratios.length ? ratios.reduce((x, y) => x + y, 0) / ratios.length : null, n };
}

function ratioResult(kind: LookMetric["kind"], a: LookSample[], b: LookSample[], target: [number, number]): MetricResult {
  const t = `${target[0]}-${target[1]}`;
  const { value: v, n } = groupedRatio(a, b);
  if (v === null) return { kind, value: null, target: t, pass: null, n };
  return { kind, value: v, target: t, pass: v >= target[0] && v <= target[1], n };
}

/**
 * A wall's spread from its samples' heights: the floor band is the lowest
 * 0.9 m of samples, the cornice band the highest 1.5 m; (top - bottom) / top.
 */
export function wallSpread(samples: LookSample[]): { value: number | null; n: number } {
  // Per column (group), averaged: each column's own floor and cornice bands.
  const groups = byGroup(samples);
  if (groups.size > 1 || !groups.has("")) {
    const spreads: number[] = [];
    let n = 0;
    for (const g of groups.values()) {
      const s = columnSpread(g);
      if (s === null) continue;
      spreads.push(s);
      n += g.length;
    }
    return { value: spreads.length ? spreads.reduce((x, y) => x + y, 0) / spreads.length : null, n };
  }
  return { value: columnSpread(samples), n: samples.length };
}

function columnSpread(samples: LookSample[]): number | null {
  if (samples.length < 2 * MIN_SAMPLES) return null;
  const ys = samples.map((s) => s.y);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  if (hi - lo < 2.4) return null;
  const bottom = samples.filter((s) => s.y <= lo + 0.9);
  const top = samples.filter((s) => s.y >= hi - 1.5);
  if (bottom.length < MIN_SAMPLES || top.length < MIN_SAMPLES) return null;
  const t = meanLum(top);
  return (t - meanLum(bottom)) / Math.max(1e-6, t);
}

/**
 * The light's tint on a set of undersides: t = mean rendered / mean albedo per
 * channel (linear), over the samples whose material has an albedo.
 */
export function lightTint(samples: LookSample[]): { t: Vec3 | null; n: number } {
  const withAlbedo = samples.filter((s) => s.albedo);
  if (withAlbedo.length < MIN_SAMPLES) return { t: null, n: withAlbedo.length };
  const r: Vec3 = [0, 0, 0];
  const a: Vec3 = [0, 0, 0];
  for (const s of withAlbedo) {
    for (let i = 0; i < 3; i++) {
      r[i] += s.rgb[i]!;
      a[i] += s.albedo![i]!;
    }
  }
  return { t: [r[0] / Math.max(1e-6, a[0]), r[1] / Math.max(1e-6, a[1]), r[2] / Math.max(1e-6, a[2])], n: withAlbedo.length };
}

/** Whether a light tint passes: grey enough, or not brown. */
export function tintPasses(t: Vec3): boolean {
  const { hue, sat } = hueSat(t);
  return sat < PROBE_TINT_SAT || hue < PROBE_BROWN_HUE[0] || hue > PROBE_BROWN_HUE[1];
}

/**
 * The numbers of a view from its measured sets; `others` holds the other
 * views measured in the same session (a metric whose set lives in another
 * view, as the look-up's gallery floor, reads it there).
 */
export function lookMetrics(view: LookView, sets: Record<string, LookSetResult>, others: Record<string, Record<string, LookSetResult>> = {}): MetricResult[] {
  const get = (name: string, viewId?: string): LookSample[] => (viewId ? others[viewId]?.[name]?.samples : sets[name]?.samples) ?? [];
  return view.metrics.map((m): MetricResult => {
    switch (m.kind) {
      case "corner":
        return ratioResult("corner", get(m.corner), get(m.mid), PROBE_CORNER_RATIO);
      case "soffit":
        return ratioResult("soffit", get(m.soffit), get(m.floor, m.view), PROBE_SOFFIT_RATIO);
      case "grade":
        return ratioResult("grade", get(m.wall), get(m.edge), PROBE_SOFFIT_GRADE);
      case "spread": {
        const s = wallSpread(get(m.set));
        return { kind: "spread", value: s.value, target: `>= ${PROBE_WALL_SPREAD}`, pass: s.value === null ? null : s.value >= PROBE_WALL_SPREAD, n: s.n };
      }
      case "tint": {
        const { t, n } = lightTint(get(m.set));
        const target = `sat < ${PROBE_TINT_SAT} or hue outside ${PROBE_BROWN_HUE[0]}-${PROBE_BROWN_HUE[1]}`;
        if (!t) return { kind: "tint", value: null, target, pass: null, n };
        const { hue, sat } = hueSat(t);
        return { kind: "tint", value: sat, hue, target, pass: tintPasses(t), n };
      }
    }
  });
}
