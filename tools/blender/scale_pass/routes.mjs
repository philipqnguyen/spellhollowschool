// Travel times over a layout (written for the castle's scale pass, M1.0):
// walking routes over a layout from the Entrance Hall (FROM) to the key
// places, horizontal metres only (sim-core moves by a horizontal delta, so
// time = horizontal length / speed; run 6.5 m/s, walk 3.0 m/s, MoveParams).
//
//   node tools/blender/scale_pass/routes.mjs data/world/layout.json [--before old-layout.json]
//       [--max-growth 0.4] [--json]
//
// Shortest paths over the walkable rectangles: nodes are portal points
// sampled along every contact where two surfaces connect (heights within
// the 0.35 m step), joined by straight lines across each (convex) surface.
// House gates are ignored (each house walks to its own common room).
// With --before it prints both layouts and checks the pass's gate: no key
// trip over 60 s at run speed and none more than 40 % longer; exit 1 if not.
// --max-growth sets that growth limit (castle M5: 0, no existing trip
// longer). A trip whose target the before layout lacks is "new": only the
// 60 s limit applies to it. --json prints {label: metres or null} instead.
// The moving staircase flights are measured at rest in position A
// (staircase state 0); --state N puts flight i at B where bit i of N is set
// (flights in the layout's order, sim_core::stairs). sim-core's test
// `staircase_trip_rules` checks every state.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const STEP = 0.35, EPS = 1e-3, RUN = 6.5, WALK = 3.0;
const GATE_S = 60;

/** Key trips from the Entrance Hall (FROM): [label, kind, id]. */
const TRIPS = [
  ["Owl Exchange counter", "interactable", "owl_exchange"],
  ["Great Hall dais", "landmark", "staff_table"],
  ["Grand Staircase foot", "landmark", "grand_staircase"],
  ["Transfiguration door (L1)", "landmark", "classroom_door_2"],
  ["Potions cauldron (L-1)", "interactable", "cauldron_classroom_1"],
  ["Common room B door (L-1)", "landmark", "common_room_entrance_2"],
  ["Common room D door (L-2)", "landmark", "common_room_entrance_4"],
  ["Courtyard gate", "surface", "courtyard_gate"],
  ["Lake shore", "landmark", "lake_shore"],
  ["Charms classroom door (L3)", "interactable", "charms_classroom_door"],
  ["Grand Staircase top (L7)", "landmark", "grand_staircase_top"],
  ["Common room C door (L6)", "landmark", "common_room_entrance_3"],
  ["Common room A door (L7)", "landmark", "common_room_entrance_1"],
  // Castle M5a's new spaces (docs/art/castle-kit.md).
  ["West tower, sixth-floor door (L6)", "surface", "wt_door_l6"],
  ["Undercroft Well (L0)", "landmark", "undercroft_well"],
  ["Long Gallery, rose window (L1)", "landmark", "gallery_rose_window"],
  // Castle M5b's new spaces (docs/art/castle-kit.md).
  ["Trophy Room, armillary (L0)", "landmark", "trophy_armillary"],
  ["Second-Floor Library, floor map (L2)", "landmark", "library_floor_map"],
  ["Kitchens (L-1)", "landmark", "kitchens_tables"],
  // Castle M5b's extra loops.
  ["Scullery Passage (L-1)", "surface", "scullery_passage"],
  ["Trophy Stair, gallery landing (L1)", "surface", "trophy_stair_landing"],
  // Castle M5c's new door: the Owl Gate into the Exchange Yard.
  ["Exchange Yard, by the Owl Gate (L0)", "landmark", "exchange_yard"],
];

function height(s, x, z) {
  // A grounds heightfield, bilinear and clamped (sim_core::layout::Heights).
  if (s.heights) {
    const { origin, step, cols, rows, h } = s.heights;
    const fx = Math.min(Math.max((x - origin[0]) / step, 0), cols - 1);
    const fz = Math.min(Math.max((z - origin[1]) / step, 0), rows - 1);
    const i = Math.min(Math.floor(fx), cols - 2), j = Math.min(Math.floor(fz), rows - 2);
    const tx = fx - i, tz = fz - j, r = j * cols + i;
    const a = h[r] + (h[r + 1] - h[r]) * tx, c = h[r + cols] + (h[r + cols + 1] - h[r + cols]) * tx;
    return a + (c - a) * tz;
  }
  if (s.y_top == null) return s.y;
  const w = s.max[0] - s.min[0], d = s.max[1] - s.min[1], dy = s.y_top - s.y;
  switch (s.rise) {
    case "+x": return s.y + dy * (x - s.min[0]) / w;
    case "-x": return s.y + dy * (s.max[0] - x) / w;
    case "+z": return s.y + dy * (z - s.min[1]) / d;
    case "-z": return s.y + dy * (s.max[1] - z) / d;
  }
  throw new Error(`bad rise ${s.rise}`);
}

function graph(layout) {
  const S = layout.surfaces;
  const nodes = []; // {x, z, s: [i, j]}
  const bySurf = S.map(() => []);
  for (let i = 0; i < S.length; i++) for (let j = i + 1; j < S.length; j++) {
    const a = S[i], b = S[j];
    const x0 = Math.max(a.min[0], b.min[0]), x1 = Math.min(a.max[0], b.max[0]);
    const z0 = Math.max(a.min[1], b.min[1]), z1 = Math.min(a.max[1], b.max[1]);
    if (x1 < x0 - EPS || z1 < z0 - EPS) continue;
    if (x1 - x0 < EPS && z1 - z0 < EPS) continue; // corner touch
    // Portals every ~2 m along the contact (at most 17), where the step fits.
    const len = Math.max(x1 - x0, z1 - z0);
    const n = Math.min(16, Math.max(1, Math.ceil(len / 2)));
    for (let k = 0; k <= n; k++) {
      const f = n === 0 ? 0.5 : k / n;
      const x = x0 + (x1 - x0) * f, z = z0 + (z1 - z0) * f;
      if (Math.abs(height(a, x, z) - height(b, x, z)) > STEP + 1e-4) continue;
      const id = nodes.length;
      nodes.push({ x, z, s: [i, j] });
      bySurf[i].push(id);
      bySurf[j].push(id);
    }
  }
  return { S, nodes, bySurf };
}

function surfaceAt(S, p) {
  let best = -1, bd = Infinity;
  S.forEach((s, i) => {
    if (p[0] < s.min[0] - EPS || p[0] > s.max[0] + EPS || p[2] < s.min[1] - EPS || p[2] > s.max[1] + EPS) return;
    const d = Math.abs(height(s, p[0], p[2]) - p[1]);
    if (d < bd) { bd = d; best = i; }
  });
  return bd <= 1.5 ? best : -1;
}

/** Shortest horizontal path length from p to q (Dijkstra over portals). */
function route(g, p, q) {
  const { S, nodes, bySurf } = g;
  const sp = surfaceAt(S, p), sq = surfaceAt(S, q);
  if (sp < 0 || sq < 0) return null;
  if (sp === sq) return Math.hypot(p[0] - q[0], p[2] - q[2]);
  const dist = new Map(), done = new Set();
  for (const n of bySurf[sp]) dist.set(n, Math.hypot(nodes[n].x - p[0], nodes[n].z - p[2]));
  let best = Infinity;
  for (;;) {
    let u = -1, du = Infinity;
    for (const [n, d] of dist) if (!done.has(n) && d < du) { du = d; u = n; }
    if (u < 0 || du >= best) break;
    done.add(u);
    for (const s of nodes[u].s) {
      if (s === sq) best = Math.min(best, du + Math.hypot(nodes[u].x - q[0], nodes[u].z - q[2]));
      for (const v of bySurf[s]) {
        if (done.has(v)) continue;
        const dv = du + Math.hypot(nodes[u].x - nodes[v].x, nodes[u].z - nodes[v].z);
        if (dv < (dist.get(v) ?? Infinity)) dist.set(v, dv);
      }
    }
  }
  return best === Infinity ? null : best;
}

function target(layout, kind, id) {
  if (kind === "landmark") return layout.landmarks.find((l) => l.id === id)?.pos;
  if (kind === "interactable") return layout.interactables.find((i) => i.id === id)?.pos;
  const s = layout.surfaces.find((x) => x.id === id);
  if (!s) return undefined;
  const cx = (s.min[0] + s.max[0]) / 2, cz = (s.min[1] + s.max[1]) / 2;
  return [cx, height(s, cx, cz), cz];
}

/**
 * Where the trips start: the Entrance Hall by the front doors, where the
 * first spawn stood before the spawns moved outside onto the
 * front steps (the trip rules keep measuring from the hall).
 */
const FROM = [14, 0, 32];

function trips(layout) {
  const g = graph(layout);
  const from = FROM;
  return TRIPS.map(([label, kind, id]) => {
    const q = target(layout, kind, id);
    return { label, found: q != null, m: q ? route(g, from, q) : null };
  });
}

function forState(layout, state) {
  const flights = [];
  for (const s of layout.surfaces) if (s.moving && !flights.includes(s.moving.flight)) flights.push(s.moving.flight);
  const keep = (s) => !s.moving || (s.moving.position === "b") === Boolean((state >> flights.indexOf(s.moving.flight)) & 1);
  return { ...layout, surfaces: layout.surfaces.filter(keep) };
}

function load(arg) {
  let layout;
  if (arg.startsWith("git:")) {
    const [rev, path] = arg.slice(4).split(":");
    layout = JSON.parse(execFileSync("git", ["show", `${rev}:${path}`], { encoding: "utf8" }));
  } else {
    layout = JSON.parse(readFileSync(arg, "utf8"));
  }
  return forState(layout, STATE);
}

const args = process.argv.slice(2);
const bi = args.indexOf("--before"), gi = args.indexOf("--max-growth"), si = args.indexOf("--state");
const STATE = si >= 0 ? Number(args[si + 1]) : 0;
const valued = new Set([bi, gi, si].filter((i) => i >= 0).map((i) => i + 1));
const before = bi >= 0 ? load(args[bi + 1]) : null;
const GATE_GROWTH = gi >= 0 ? Number(args[gi + 1]) : 0.4;
const layoutArg = args.find((a, i) => !a.startsWith("--") && !valued.has(i)) ?? "data/world/layout.json";
const now = trips(load(layoutArg));
if (args.includes("--json")) {
  console.log(JSON.stringify(Object.fromEntries(now.map((t) => [t.label, t.m == null ? null : Math.round(t.m * 10) / 10]))));
  process.exit(0);
}
const fmt = (m) => (m == null ? "?" : m.toFixed(0));
const sec = (m, v) => (m == null ? "?" : `${(m / v).toFixed(1)} s`);
let ok = true;
if (before) {
  const old = trips(before);
  console.log("| From spawn to | Before m | Before run | After m | After run | After walk | Change |");
  console.log("| --- | --- | --- | --- | --- | --- | --- |");
  now.forEach((t, k) => {
    const o = old[k].m;
    const change = o && t.m != null ? (t.m - o) / o : null;
    const bad = t.m == null || t.m / RUN > GATE_S || (change != null && change > GATE_GROWTH + 1e-9);
    if (bad) ok = false;
    const pct = !old[k].found ? "new" : change == null ? "?" : `${change >= 0 ? "+" : ""}${Math.round(change * 100)}%`;
    console.log(`| ${t.label} | ${fmt(o)} | ${sec(o, RUN)} | ${fmt(t.m)} | ${sec(t.m, RUN)} | ${sec(t.m, WALK)} | ${pct}${bad ? " (over the gate)" : ""} |`);
  });
  console.log(ok ? `\ngate OK: no trip over ${GATE_S} s running, none more than +${Math.round(GATE_GROWTH * 100)}%`
    : "\ngate FAILED");
} else {
  for (const t of now) console.log(`${t.label.padEnd(30)} ${fmt(t.m).padStart(5)} m ${sec(t.m, RUN).padStart(8)} run ${sec(t.m, WALK).padStart(8)} walk`);
}
process.exit(ok ? 0 : 1);
