// Seats fit the sitter's legs (characters/seated.ts, castle/seats.ts).
//
// The rule is tested twice: on a simple bone-length model of the legs
// (always), and on the exported bodies (client/public/assets/characters,
// skipped when the art is not there): for both bodies at the height
// slider's minimum, default and maximum, on every kind of seat (its front
// edge, back and drop from the furniture code, and every distinct fit in
// the world manifest and instances.json when they carry one), the sitting
// loop is posed as the renderer poses it (the knees lifted where the feet
// would reach below the floor), the lower legs are skinned (the body's
// calves and feet, every shoe and pair of trousers for that body), and
//   - no hanging vertex is inside the seat's box (from its back to its
//     front edge, from the floor its feet rest on to its top),
//   - no vertex is below that floor,
//   - the back of the knee is at or a few cm past the front edge,
//   - the body's seat and lower back stay off the backrest, and the sit
//     bones on the seat.

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { CREATOR } from "../../content/catalog";
import { ROOM_SEAT_HEIGHT, Seats, seatPlace } from "../castle/seats";
import { clipSet, Rig, samplePose, type BakedClip } from "./clips";
import { Gltf } from "./gltf";
import { greyboxSkeleton } from "./greybox";
import { mul4 } from "./math";
import { partFromGltf, type PartData } from "./parts";
import { BACK_CLEAR, CALF_CLEAR, kneeLift, legBones, liftKnees, measureSeatedLegs, seatShift, SIT_HOLD, worldMatrices, type SeatedLegs } from "./seated";

const SPEEDS = { walk: 1.4, run: 4 };

/** A bone-length model of the seated legs (about body_a's numbers): calves 0.31 m ahead of the hips, heels 0.46 m down, a 0.46 m thigh, the seat 0.09 m behind. */
const MODEL: SeatedLegs = { calfBack: 0.31, back: 0.09, heelDrop: 0.46, thigh: 0.46 };

describe("seatShift (the bone-length model)", () => {
  it("slides a short sitter forward on a deep seat until the calves clear its front edge", () => {
    // The common rooms' sofa: the cushions' edge 0.40 ahead of the row's centre line, the back cushion 0.25 behind.
    const s = 0.9;
    const d = seatShift(MODEL, s, 0.4, 0.25, 0.46);
    expect(d).toBeGreaterThan(0);
    expect(d + MODEL.calfBack * s).toBeCloseTo(0.4 + CALF_CLEAR, 9);
    // The old place (the row's centre line) had the calves 0.12 m inside the cushions.
    expect(MODEL.calfBack * s).toBeLessThan(0.4 - 0.1);
  });

  it("slides a tall sitter back, but never into the backrest", () => {
    // A study chair: its seat 0.24 ahead, the back panel 0.19 behind.
    const s = 1.06;
    const d = seatShift(MODEL, s, 0.24, 0.19, 0.46);
    expect(d).toBeCloseTo(-0.19 + (MODEL.back + BACK_CLEAR) * s, 9);
    expect(d + MODEL.calfBack * s).toBeGreaterThan(0.24 + CALF_CLEAR);
  });

  it("keeps the sit bones on the seat however small the sitter", () => {
    const s = 0.15; // a very strong Diminishing Draught
    expect(seatShift(MODEL, s, 0.4, 0.25, 0.46)).toBeCloseTo(0.4 - SIT_HOLD * s, 9);
  });

  it("raises the knees only when the heels would reach below the floor", () => {
    expect(kneeLift(MODEL, 0.95, 0.46)).toBe(0);
    const lift = kneeLift(MODEL, 1.05, 0.45);
    expect(lift).toBeGreaterThan(0);
    // The heels, carried up with the knees, rest on the floor.
    expect(MODEL.heelDrop * 1.05 - MODEL.thigh * 1.05 * Math.sin(lift)).toBeCloseTo(0.45, 9);
    // Raised knees swing the shins back a little: the hips go a little further forward.
    expect(seatShift(MODEL, 1.05, 0.4, 0.25, 0.45)).toBeGreaterThan(seatShift(MODEL, 1.05, 0.4, 0.25, 0.6));
  });
});

describe("liftKnees", () => {
  it("turns the thighs up about the hips and keeps the shins hanging as they were", () => {
    const rig = new Rig(greyboxSkeleton(0));
    const sit = clipSet(rig, [], SPEEDS).get("sit")!;
    const bones = legBones(rig)!;
    expect(bones).not.toBeNull();
    const local = new Float32Array(rig.count * 16);
    const before = new Float32Array(rig.count * 16);
    const after = new Float32Array(rig.count * 16);
    samplePose(rig, [{ clip: sit, time: 0.5, weight: 1 }], local);
    worldMatrices(rig, local, before);
    liftKnees(rig, local, bones, 0.3);
    worldMatrices(rig, local, after);
    const thigh = rig.boneIndex("DEF-thigh.L");
    const knee = rig.boneIndex("DEF-shin.L");
    const foot = rig.boneIndex("DEF-foot.L");
    const at = (w: Float32Array, b: number) => [w[b * 16 + 12]!, w[b * 16 + 13]!, w[b * 16 + 14]!];
    const sub = (a: number[], b: number[]) => a.map((v, i) => v - b[i]!);
    // The hip stays; the knee turns 0.3 rad up about the side axis.
    expect(at(after, thigh)).toEqual(at(before, thigh).map((v) => expect.closeTo(v, 6)));
    const t0 = sub(at(before, knee), at(before, thigh));
    const t1 = sub(at(after, knee), at(after, thigh));
    const ang = (v: number[]) => Math.atan2(v[1]!, v[2]!);
    expect(ang(t1) - ang(t0)).toBeCloseTo(0.3, 5);
    expect(t1[0]).toBeCloseTo(t0[0]!, 6);
    // The shin keeps its direction (and the foot below it): it moved with the knee.
    expect(sub(at(after, foot), at(after, knee))).toEqual(sub(at(before, foot), at(before, knee)).map((v) => expect.closeTo(v, 5)));
    for (let k = 0; k < 11; k++) if (k % 4 !== 3) expect(after[foot * 16 + k]).toBeCloseTo(before[foot * 16 + k]!, 5);
  });
});

describe("seat rows with their fit", () => {
  it("reads the nine-number rows and draws the sitter on the row's centre line", () => {
    const seats = Seats.fromCells([{ id: "common_room_house_a", seats: [[-45.9, -39.12, -43.1, -38.68, 63.46, 0, 0.4, 0.25, 0.46], [0, 0, 1, 1, 0.5, 0, -0.1, 0.2, 0.46]] }]);
    expect(seats.count).toBe(1); // a negative reach is malformed
    const out = seatPlace();
    // Standing in front of the sofa, a little off its middle.
    expect(seats.find(-44.2, 63.02, -38.3, out)).toBe(true);
    expect(out).toMatchObject({ x: -44.2, y: 63.46, z: expect.closeTo(-38.9, 9), yaw: 0, ahead: 0.4, behind: 0.25, drop: 0.46 });
  });

  it("reads a room seat's fit (the Charms desk bench), its drop defaulting to the room seat height", () => {
    const seats = Seats.fromRoomSeats([{ pos: [-1.95, 0.3, 1.52], yaw: Math.PI, front: 0.13, back: 0.23 }, { pos: [3, 0, 0], yaw: 0 }])!;
    const out = seatPlace();
    expect(seats.find(-1.9, 0.3, 1.2, out)).toBe(true);
    expect(out).toMatchObject({ y: expect.closeTo(0.3 + ROOM_SEAT_HEIGHT, 9), z: expect.closeTo(1.52, 9), ahead: 0.13, behind: 0.23, drop: ROOM_SEAT_HEIGHT });
    // Without a fit: drawn as before, unfitted.
    expect(seats.find(3, 0, 0.2, out)).toBe(true);
    expect(out.ahead).toBeNaN();
  });
});

// ------------------------------------------------------------------ the exported bodies

const ASSETS = new URL("../../../public/assets/", import.meta.url).pathname;
const HAVE_ART = existsSync(`${ASSETS}characters/body_a.glb`) && existsSync(`${ASSETS}characters/body_b.glb`);

/**
 * Every kind of seat, from the furniture code: how far it reaches ahead of
 * the hips' row centre line (its front edge: cushion, board or frame),
 * behind it (the backrest's face, or a backless seat's back edge), and its
 * top above the floor the feet rest on.
 */
const SEAT_KINDS: Record<string, { front: number; back: number; drop: number }> = {
  "sofa / armchair (common rooms, Gathering Room, Divination)": { front: 0.4, back: 0.25, drop: 0.46 },
  "study chair": { front: 0.24, back: 0.19, drop: 0.46 },
  "bench (Great Hall, kitchens, pitch stands)": { front: 0.2, back: 0.2, drop: 0.45 },
  "staff chair": { front: 0.24, back: 0.19, drop: 0.52 },
  "high chair": { front: 0.24, back: 0.19, drop: 0.56 },
  "spectator bench": { front: 0.2, back: 0.2, drop: 0.48 },
  "fountain coping (feet on the step)": { front: 0.26, back: 0.22, drop: 0.34 },
  "class desk bench (Charms)": { front: 0.13, back: 0.23, drop: 0.46 },
};

/** The distinct fits in the exported world manifest and instances.json (none in older exports). */
function exportedFits(): Record<string, { front: number; back: number; drop: number }> {
  const out: Record<string, { front: number; back: number; drop: number }> = {};
  const add = (front: unknown, back: unknown, drop: unknown) => {
    if (typeof front === "number" && typeof back === "number" && typeof drop === "number") out[`exported ${front}/${back}/${drop}`] = { front, back, drop };
  };
  const manifest = `${ASSETS}world/manifest.json`;
  if (existsSync(manifest)) {
    for (const c of (JSON.parse(readFileSync(manifest, "utf8")) as { cells: { seats?: number[][] }[] }).cells) for (const r of c.seats ?? []) if (r.length === 9) add(r[6], r[7], r[8]);
  }
  const instances = `${ASSETS}world/instances.json`;
  if (existsSync(instances)) {
    for (const room of Object.values(JSON.parse(readFileSync(instances, "utf8")) as Record<string, { seats?: { front?: number; back?: number; drop?: number }[] }>)) {
      for (const s of room.seats ?? []) add(s.front, s.back, s.drop ?? ROOM_SEAT_HEIGHT);
    }
  }
  return out;
}

function load(path: string): PartData {
  const buf = readFileSync(ASSETS + path);
  return partFromGltf(Gltf.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer));
}

/** The LOD0 vertices of `part`'s primitives passing `keep`, skinned to `rig` posed as `world` (model space, 3 per vertex). */
function skinned(part: PartData, rig: Rig, world: Float32Array, keep: (region: string | null) => boolean): Float32Array {
  const sk = part.skeleton ?? rig.data;
  const own = new Map(sk.bones.map((b, i) => [b, i]));
  const mats = new Float32Array(part.bones.length * 16);
  const ok = part.bones.map((b, j) => {
    const r = rig.boneIndex(b);
    const i = own.get(b);
    if (r < 0 || i === undefined) return false;
    mul4(world, sk.inverseBind, mats, r * 16, i * 16, j * 16);
    return true;
  });
  const pts: number[] = [];
  for (const prim of part.primitives) {
    if (prim.lod !== 0 || !keep(prim.region)) continue;
    const p = prim.positions;
    for (let v = 0; v < p.length / 3; v++) {
      let x = 0;
      let y = 0;
      let z = 0;
      for (let k = 0; k < 4; k++) {
        const w = prim.weights[v * 4 + k]!;
        const j = prim.joints[v * 4 + k]!;
        if (w <= 0 || !ok[j]) continue;
        const o = j * 16;
        x += w * (mats[o]! * p[v * 3]! + mats[o + 4]! * p[v * 3 + 1]! + mats[o + 8]! * p[v * 3 + 2]! + mats[o + 12]!);
        y += w * (mats[o + 1]! * p[v * 3]! + mats[o + 5]! * p[v * 3 + 1]! + mats[o + 9]! * p[v * 3 + 2]! + mats[o + 13]!);
        z += w * (mats[o + 2]! * p[v * 3]! + mats[o + 6]! * p[v * 3 + 1]! + mats[o + 10]! * p[v * 3 + 2]! + mats[o + 14]!);
      }
      pts.push(x, y, z);
    }
  }
  return Float32Array.from(pts);
}

interface Body {
  id: string;
  rig: Rig;
  loop: BakedClip;
  part: PartData;
  legs: SeatedLegs;
  /** The shoes and trousers made for this body (worn on the legs below the seat). */
  wear: PartData[];
  heights: [number, number, number];
  defaultHeight: number;
}

function body(index: number): Body {
  const bt = CREATOR.body_types[index]!;
  const part = load(`${bt.asset}.glb`);
  const rig = new Rig(part.skeleton!);
  const sit = clipSet(rig, part.clips, SPEEDS).get("sit")!;
  const loop = sit.next ?? sit;
  const legs = measureSeatedLegs(rig, loop, part)!;
  const wear: PartData[] = [];
  for (const dir of ["shoes", "legs"]) {
    for (const f of readdirSync(`${ASSETS}gear/${dir}`)) {
      if (f.endsWith(`_${bt.id}.glb`) && (dir === "shoes" || f.startsWith("trousers_"))) wear.push(load(`gear/${dir}/${f}`));
    }
  }
  const [lo, hi] = bt.height_m;
  return { id: bt.id, rig, loop, part, legs, wear, heights: [lo, bt.default_height_m, hi], defaultHeight: bt.default_height_m };
}

/**
 * Below this under the seat's top a vertex hangs (m): above it the
 * trousers' seat and the thighs' undersides press into the seat (up to
 * 2 cm at the tallest).
 */
const HANGING = 0.04;

interface Fit {
  /** The worst gap: how far inside the seat's box the deepest hanging vertex is (m, > 0 = inside). */
  inside: number;
  /** The lowest vertex above the floor (m, < 0 = below it). */
  floorGap: number;
  /** The back of the knee past the front edge (m). */
  kneePast: number;
  /** The body's seat and lower back off the backrest (m). */
  backGap: number;
  /** The hips (root) behind the front edge (m). */
  hipsIn: number;
  lift: number;
}

/** Poses `b` at `height` on a seat as the renderer does (shift `d` or the rule's, knees lifted or not) and measures it in the seat's frame. */
function sitOn(b: Body, height: number, seat: { front: number; back: number; drop: number }, fitted = true): Fit {
  const s = height / b.defaultHeight;
  const d = fitted ? seatShift(b.legs, s, seat.front, seat.back, seat.drop) : 0;
  const lift = fitted ? kneeLift(b.legs, s, seat.drop) : 0;
  const bones = legBones(b.rig)!;
  const local = new Float32Array(b.rig.count * 16);
  const world = new Float32Array(b.rig.count * 16);
  let inside = -Infinity;
  let floorGap = Infinity;
  let knee = Infinity;
  let back = -Infinity;
  for (const f of [0, 0.25, 0.5, 0.75]) {
    samplePose(b.rig, [{ clip: b.loop, time: f * b.loop.duration, weight: 1 }], local);
    liftKnees(b.rig, local, bones, lift);
    worldMatrices(b.rig, local, world);
    const legs = [skinned(b.part, b.rig, world, (r) => r === "calves" || r === "feet"), ...b.wear.map((w) => skinned(w, b.rig, world, () => true))];
    for (const [k, pts] of legs.entries()) {
      for (let i = 0; i < pts.length; i += 3) {
        const y = pts[i + 1]! * s;
        const z = d + pts[i + 2]! * s;
        floorGap = Math.min(floorGap, y + seat.drop);
        if (y < -HANGING && y > -seat.drop) inside = Math.max(inside, Math.min(seat.front - z, z + seat.back, y + seat.drop, -y - HANGING));
        // The back of the knee: the body's calf just under the thigh.
        if (k === 0 && y < -HANGING && y > -0.12 * s) knee = Math.min(knee, z);
      }
    }
    const seatPts = skinned(b.part, b.rig, world, (r) => r === "hips" || r === "torso");
    for (let i = 0; i < seatPts.length; i += 3) {
      const y = seatPts[i + 1]! * s;
      if (y >= 0 && y <= 0.45) back = Math.max(back, -(d + seatPts[i + 2]! * s));
    }
  }
  return { inside, floorGap, kneePast: knee - seat.front, backGap: seat.back - back, hipsIn: seat.front - d, lift };
}

describe.skipIf(!HAVE_ART)("the exported bodies on every seat", () => {
  const bodies = HAVE_ART ? [body(0), body(1)] : [];
  const kinds = { ...SEAT_KINDS, ...(HAVE_ART ? exportedFits() : {}) };

  it("measures the seated legs off the body's own mesh", () => {
    for (const b of bodies) {
      expect(b.legs.calfBack).toBeGreaterThan(0.2);
      expect(b.legs.calfBack).toBeLessThan(0.4);
      expect(b.legs.heelDrop).toBeGreaterThan(0.4);
      expect(b.legs.heelDrop).toBeLessThan(0.5);
      expect(b.legs.thigh).toBeGreaterThan(0.35);
      expect(b.legs.back).toBeGreaterThan(0.05);
    }
  });

  it("had the short sitter's shins through the sofa's front before the fit (the reported bug)", () => {
    const sofa = SEAT_KINDS["sofa / armchair (common rooms, Gathering Room, Divination)"]!;
    const before = sitOn(bodies[1]!, bodies[1]!.heights[0], sofa, false);
    expect(before.inside).toBeGreaterThan(0.05);
  });

  const rows: string[] = [];
  for (const [i, who] of [
    [0, "male"],
    [1, "female"],
  ] as const) {
    for (const [h, label] of [
      [0, "shortest"],
      [1, "default"],
      [2, "tallest"],
    ] as const) {
      it(`fits the ${label} ${who} on every seat: shins clear, feet on or over the floor, knees at the edge`, () => {
        const b = bodies[i]!;
        const height = b.heights[h];
        for (const [name, seat] of Object.entries(kinds)) {
          const f = sitOn(b, height, seat);
          const at = `${b.id} ${height} m on ${name}`;
          rows.push(`${at}: inside ${(f.inside * 100).toFixed(1)} cm, floor ${(f.floorGap * 100).toFixed(1)} cm, knee past ${(f.kneePast * 100).toFixed(1)} cm, back gap ${(f.backGap * 100).toFixed(1)} cm, lift ${((f.lift * 180) / Math.PI).toFixed(1)} deg`);
          expect(f.inside, `${at}: a hanging vertex inside the seat`).toBeLessThanOrEqual(0);
          expect(f.floorGap, `${at}: a foot below the floor`).toBeGreaterThanOrEqual(-0.005);
          expect(f.kneePast, `${at}: the back of the knee short of the front edge`).toBeGreaterThanOrEqual(0);
          // Unless the sitter is back against the backrest (or a backless seat's back edge): then the knees reach further.
          const atBack = f.backGap <= BACK_CLEAR * (height / b.defaultHeight) + 0.005;
          expect(atBack || f.kneePast <= 0.1, `${at}: the back of the knee ${f.kneePast} m past the front edge`).toBe(true);
          expect(f.backGap, `${at}: the body in the backrest`).toBeGreaterThanOrEqual(0);
          expect(f.hipsIn, `${at}: the sit bones off the seat`).toBeGreaterThanOrEqual(SIT_HOLD * 0.5);
        }
      });
    }
  }

  it("prints the fit table", () => {
    if (process.env.SEAT_FIT_TABLE) console.log(rows.join("\n"));
    expect(rows.length).toBe(6 * Object.keys(kinds).length);
  });
});
