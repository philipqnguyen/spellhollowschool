// The character adoption's client side (docs/art/character-adoption.md):
// the shade layer and its assembly, faces and the tinted iris, the fringe
// shadow, hair sway, LOD selection, blob shadows and the character shadow
// direction.

import { describe, expect, it } from "vitest";
import { Gltf } from "./gltf";
import { FRINGE_DIP, fringeFrom, partFromGltf, shadowLine } from "./parts";
import { Rig } from "./clips";
import { assemble, fringeShadow, RING_ELEVATION, ringY, vertexRegion, type PartInput } from "./assemble";
import { fixtureGlb, FIXTURE_SWAY } from "./fixtures";
import { regionIndex, regionOfAlpha } from "../toon/palette";
import { applySway, extendRig, swayJoints, swayState, SWAY } from "./sway";
import { CASTLE_IN_VIEW_TARGET, CHARACTER_FLOOR, characterAllowance, characterFloor, drawnTriangles, governTiers, LOD_RULES, PROTECT_NEAREST, tierFor, TIER_TRIANGLES, TRIANGLE_BUDGET, type Tier } from "./lod";
import { blobMatrix, BLOB_SIZE } from "./blobs";
import { CharacterShadows, MIN_ELEVATION, shadowDirection } from "../toon/char-shadows";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { ROOM_SEAT_HEIGHT, Seats, seatPlace } from "../castle/seats";
import { parseCharacterFx } from "./fx-flag";
import { parseInstanceManifest } from "../zones";
import { Scene } from "@babylonjs/core/scene";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import { CharacterRenderer, estimateTriangles } from "./renderer";
import type { PartLibrary } from "./library";
import { composeTRS, mul4 } from "./math";
import { samplePose } from "./clips";

const read = (o = {}) => partFromGltf(Gltf.parse(fixtureGlb(o)));
const SKIN: [number, number, number] = [0.8, 0.6, 0.5];
const HAIR: [number, number, number] = [0.2, 0.1, 0.05];
const EYE: [number, number, number] = [0.3, 0.5, 0.9];

function merge(parts: PartInput[], rig: Rig, lod = 0) {
  return assemble({ rig, parts, lod, skin: SKIN, hair: HAIR, eye: EYE, shapeWeights: new Map() });
}

describe("the shade layer", () => {
  it("reads _SHADE as floats 0..1 and carries it into the merged mesh", () => {
    const shade: [number, number, number, number][] = [
      [0.4, 0, 0.5, 1],
      [1, 0, 0.5, 0.5],
      [0.7, 0.3, 0.42, 0.45],
      [0.9, 1, 0.58, 0.45],
    ];
    const gear = read({ meshName: "robe", shade });
    expect(gear.primitives[0]!.shade!.length).toBe(16);
    expect(gear.primitives[0]!.shade![0]).toBeCloseTo(0.4, 2);
    const body = read();
    expect(body.primitives[0]!.shade ?? null).toBeNull();
    const rig = new Rig(body.skeleton!);
    const m = merge([{ role: "base", part: body }, { role: "body", part: gear, dyes: [1, 2, 3] }], rig);
    expect(m.shade.length).toBe(m.vertexCount * 4);
    // Parts without a layer read open, neutral, full outline: the look before the layer.
    expect(Array.from(m.shade.subarray(0, 4))).toEqual([255, 0, 128, 255]);
    // The gear's own values, as bytes.
    expect(Array.from(m.shade.subarray(16, 20))).toEqual([102, 0, 128, 255]);
    expect(Array.from(m.shade.subarray(24, 28))).toEqual([179, 77, 107, 115]);
  });

  it("keeps faces clean, tints the greyscale iris with the eye colour and keeps it flat", () => {
    // Fixture mask per vertex: R, R, G, B (skin, skin, brows, iris).
    const head = read({ meshName: "body_a_head", colors: true, shade: [[0.3, 0, 0.5, 1], [0.3, 0, 0.5, 1], [0.3, 0, 0.5, 1], [0.3, 0, 0.5, 1]] });
    const rig = new Rig(head.skeleton!);
    const m = merge([{ role: "base", part: head }], rig);
    const region = (v: number) => regionOfAlpha(m.colors[v * 4 + 3]!);
    expect(region(0)).toBe(regionIndex("face"));
    expect(region(2)).toBe(regionIndex("hair"));
    expect(region(3)).toBe(regionIndex("flat"));
    // The face's baked occlusion is forced open.
    expect(m.shade[0]).toBe(255);
    // The iris takes the eye colour (the body mask's B channel).
    for (let c = 0; c < 3; c++) expect(Math.abs(m.colors[12 + c]! - EYE[c]! * 255)).toBeLessThanOrEqual(1);
    // Skin elsewhere on the body stays skin, with its occlusion.
    const torso = read({ meshName: "body_a_torso", colors: true, shade: [[0.3, 0, 0.5, 1], [0.3, 0, 0.5, 1], [0.3, 0, 0.5, 1], [0.3, 0, 0.5, 1]] });
    const t = merge([{ role: "base", part: torso }], rig);
    expect(regionOfAlpha(t.colors[3]!)).toBe(regionIndex("skin"));
    expect(t.shade[0]).toBe(77);
    expect(vertexRegion("base", { mask: new Float32Array([0, 0, 1]), region: "torso" }, 0, false)).toBe(regionIndex("flat"));
  });

  it("folds the fringe's soft shadow into the forehead at assembly", () => {
    const fringe = { fringe_hem: [[-0.3, 1.51, 0.1], [0, 1.53, 0.12], [0.3, 1.51, 0.1]], fringe_shadow: { depth_m: 0.03, soft_m: 0.012, strength: 0.55 } };
    const hair = read({ meshName: "hair_test_lod0", meshExtras: fringe });
    expect(hair.fringe).not.toBeNull();
    expect(Array.from(hair.fringe!.hem.subarray(0, 3)).map((v) => Number(v.toFixed(2)))).toEqual([-0.3, 1.51, 0.1]);
    expect(hair.fringe!.strength).toBe(0.55);
    const f = hair.fringe!;
    // The shadow hangs from the smoothed line: at x = 0, (1.51 + 1.53 * 2 + 1.51) / 4 smoothed twice.
    const top = f.line[4]!;
    expect(top).toBeCloseTo(1.5175, 4);
    // Full just under the line, fading out by depth, none outside the hem's span.
    expect(fringeShadow(f, 0, top - 0.005)).toBeCloseTo(0.45);
    expect(fringeShadow(f, 0, top - 0.03)).toBeCloseTo(1);
    expect(fringeShadow(f, 0, top - 0.024)).toBeGreaterThan(0.45);
    expect(fringeShadow(f, 0, top - 0.024)).toBeLessThan(1);
    expect(fringeShadow(f, 0.5, 1.5)).toBe(1);
    // Applied to the face (forward-facing head skin) of a look wearing that hair.
    const head = read({ meshName: "body_a_head", colors: true });
    const rig = new Rig(head.skeleton!);
    const m = merge([{ role: "base", part: head }, { role: "hair", part: hair }], rig);
    // Vertex 3 is iris (flat): untouched; vertices 0, 1 at y = 1 (far below the hem): open.
    expect(m.shade[0]).toBe(255);
    expect(m.shade[12]).toBe(255);
    const noFringe = merge([{ role: "base", part: head }], rig);
    expect(noFringe.shade[0]).toBe(255);
    // Malformed extras are ignored.
    expect(fringeFrom({ fringe_hem: [[0, 1]] })).toBeNull();
    expect(fringeFrom({ fringe_hem: "x" })).toBeNull();
  });

  it("hangs the fringe shadow from the fringe's line, not from every strand tip", () => {
    // A fringe at 1.60 m with one strand tip hanging to 1.54 m (over an eye).
    const hem = new Float32Array([-0.06, 1.6, 0.1, -0.03, 1.6, 0.1, 0, 1.54, 0.1, 0.03, 1.6, 0.1, 0.06, 1.6, 0.1]);
    const line = shadowLine(hem);
    // Held up to the median less FRINGE_DIP, then smoothed: never near the tip.
    for (let k = 0; k < 5; k++) expect(line[k * 3 + 1]!).toBeGreaterThanOrEqual(1.6 - FRINGE_DIP - 1e-6);
    expect(line[7]!).toBeLessThan(1.6);
    // x and z are the hem's.
    expect(line[6]).toBe(hem[6]);
    expect(line[8]).toBe(hem[8]);
    // So the cheek under the tip (5 cm below the fringe) is open.
    const f = { hem, line, depth: 0.03, soft: 0.012, strength: 0.55 };
    expect(fringeShadow(f, 0, 1.55)).toBe(1);
  });

  it("lays the hair's highlight band round the head (one ring at one elevation)", () => {
    const c: [number, number, number] = [0, 1.6, 0];
    // Points at the ring's elevation all read the band's centre, whatever their radius or side.
    for (const [r, a] of [[0.1, 0], [0.14, 1.3], [0.12, Math.PI]] as const) {
      const x = Math.sin(a) * Math.cos(RING_ELEVATION) * r;
      const z = Math.cos(a) * Math.cos(RING_ELEVATION) * r;
      const y = c[1] + Math.sin(RING_ELEVATION) * r;
      expect(ringY(x, y, z, c, 0.28)).toBeCloseTo(0.28, 5);
    }
    // Above the ring (the crown) lower, below it (hanging hair) higher: past the band.
    expect(ringY(0, 1.75, 0.01, c, 0.28)).toBeLessThan(0.28 - 0.5);
    expect(ringY(0, 1.5, 0.12, c, 0.28)).toBeGreaterThan(0.28 + 1);
  });
});

describe("hair sway", () => {
  const body = read();
  const base = new Rig(body.skeleton!);
  const hair = read({ meshName: "hair_test", sway: true });

  it("appends a hair file's sway joints to the body's rig", () => {
    expect(swayJoints(hair)).toEqual(FIXTURE_SWAY);
    const s = extendRig(base, "body_a", hair)!;
    expect(s).not.toBeNull();
    expect(s.rig.count).toBe(base.count + 2);
    expect(s.rig.bones.slice(-2)).toEqual(FIXTURE_SWAY);
    expect(s.rig.parents[base.count]).toBe(base.boneIndex("DEF-spine.006"));
    expect(s.rig.parents[base.count + 1]).toBe(base.count);
    expect(s.chains.map((c) => [c.kind, c.bones.length])).toEqual([["back", 2]]);
    expect(s.key).toBe("body_a+hair.back.01,hair.back.02");
    // Two hair files with the same chains get different keys (their joints' rest transforms differ).
    expect(extendRig(base, "body_a", hair, "hair/long_bangs")!.key).toBe("body_a+hair/long_bangs:hair.back.01,hair.back.02");
    expect(extendRig(base, "body_a", hair, "hair/ponytail_locks")!.key).not.toBe(extendRig(base, "body_a", hair, "hair/long_bangs")!.key);
    // Short styles without joints: no sway rig.
    expect(extendRig(base, "body_a", body)).toBeNull();
  });

  /** World position of the chain's tip joint under `pose` (model frame). */
  function tip(rig: Rig, pose: Float32Array, bone: number): [number, number, number] {
    const path: number[] = [];
    for (let b = bone; b >= 0; b = rig.parents[b]!) path.unshift(b);
    let m = pose.slice(path[0]! * 16, path[0]! * 16 + 16);
    const tmp = new Float32Array(16);
    for (const b of path.slice(1)) {
      mul4(m, pose, tmp, 0, b * 16, 0);
      m = tmp.slice();
    }
    // The tip's child would sit 0.2 m down: a point below the last joint.
    const p = new Float32Array(16);
    composeTRS(0, -0.2, 0, 0, 0, 0, 1, 1, 1, 1, p);
    mul4(m, p, tmp);
    return [tmp[12]!, tmp[13]!, tmp[14]!];
  }

  it("swings the back hair back when the character speeds up, and settles at rest", () => {
    const s = extendRig(base, "body_a", hair)!;
    const pose = new Float32Array(s.rig.count * 16);
    const state = swayState();
    const root = { x: 0, y: 0, z: 0, yaw: 0, scale: 1 };
    const tipBone = s.rig.count - 1;
    samplePose(s.rig, [], pose);
    const rest = tip(s.rig, pose, tipBone);
    // Standing still for a while: at rest (the idle sine is a couple of degrees).
    for (let i = 0; i < 120; i++) applySway(s, pose, state, root, 1 / 60, i / 60);
    const still = tip(s.rig, pose, tipBone);
    expect(Math.abs(still[2] - rest[2])).toBeLessThan(0.02);
    // Accelerate forward (+Z) at 8 m/s²: the tip lags behind (toward -Z).
    let v = 0;
    let lag = 0;
    for (let i = 0; i < 20; i++) {
      v += 8 / 60;
      root.z += v / 60;
      samplePose(s.rig, [], pose);
      applySway(s, pose, state, root, 1 / 60, 2 + i / 60);
      lag = Math.min(lag, tip(s.rig, pose, tipBone)[2] - rest[2]);
    }
    expect(lag).toBeLessThan(-0.01);
    // Limits hold even for violent motion.
    for (let i = 0; i < 30; i++) {
      root.x += (i % 2 ? 1 : -1) * 0.3;
      applySway(s, pose, state, root, 1 / 60, 3 + i / 60);
    }
    for (let c = 0; c < s.chains.length; c++) {
      expect(Math.abs(state[8 + c * 4]!)).toBeLessThanOrEqual(SWAY.limits.back.back + 1e-6);
      expect(Math.abs(state[8 + c * 4 + 2]!)).toBeLessThanOrEqual(SWAY.limits.back.side + 1e-6);
    }
  });
});

describe("character LOD", () => {
  it("picks tiers by rank and distance per quality, with hysteresis", () => {
    const high = LOD_RULES.high;
    expect(tierFor(high, 3, 5, 1)).toBe(0);
    expect(tierFor(high, 9, 5, 1)).toBe(1); // rank 9: not among the nearest 8 (+ you)
    expect(tierFor(high, 3, 20, 1)).toBe(1);
    expect(tierFor(high, 3, 40, 1)).toBe(2);
    expect(tierFor(high, 3, 400, 2)).toBe(2); // LOD3 at High only by the budget
    // Promotion needs a metre and a rank of margin; demotion is immediate.
    expect(tierFor(high, 3, 11.5, 1)).toBe(1);
    expect(tierFor(high, 3, 10.9, 1)).toBe(0);
    expect(tierFor(high, 8, 5, 1)).toBe(1);
    expect(tierFor(high, 8, 5, 0)).toBe(0);
    expect(tierFor(high, 3, 12.5, 0)).toBe(1);
    // Low: LOD0 for the nearest 2 within 6 m, LOD3 beyond 25 m; you always LOD0.
    const low = LOD_RULES.low;
    expect(tierFor(low, 2, 4, 0)).toBe(0);
    expect(tierFor(low, 3, 4, 0)).toBe(1);
    expect(tierFor(low, 4, 30, 2)).toBe(3);
    expect(tierFor(low, 0, 100, 3, true)).toBe(0);
    expect(LOD_RULES.medium.total).toBe(1_200_000);
  });

  it("gives the characters what the world in view leaves of the total (castle D1)", () => {
    expect(TRIANGLE_BUDGET).toEqual({ high: 2_000_000, medium: 1_200_000, low: 500_000 });
    expect(CASTLE_IN_VIEW_TARGET).toEqual({ high: 400_000, medium: 300_000, low: 300_000 });
    expect(characterAllowance(TRIANGLE_BUDGET.medium, 300_000)).toBe(900_000);
    expect(characterAllowance(TRIANGLE_BUDGET.low, 200_000)).toBe(300_000);
    // Never below the nearest three at LOD0, whatever the world costs.
    expect(CHARACTER_FLOOR).toBe(PROTECT_NEAREST * TIER_TRIANGLES[0]!);
    expect(characterAllowance(TRIANGLE_BUDGET.low, 450_000)).toBe(CHARACTER_FLOOR);
    expect(characterAllowance(TRIANGLE_BUDGET.high, -5)).toBe(TRIANGLE_BUDGET.high);
  });

  it("demotes the farthest until the characters fit the budget, never the nearest three", () => {
    const n = 100;
    const tiers = new Uint8Array(n);
    const order = Int32Array.from({ length: n }, (_, i) => i);
    tiers.fill(0);
    const tris = (_i: number, t: Tier) => TIER_TRIANGLES[t]!;
    const medium = characterAllowance(TRIANGLE_BUDGET.medium, CASTLE_IN_VIEW_TARGET.medium);
    const total = governTiers(tiers, order, n, PROTECT_NEAREST, medium, tris);
    expect(total).toBeLessThanOrEqual(medium);
    for (let i = 0; i < PROTECT_NEAREST; i++) expect(tiers[i]).toBe(0);
    // The nearest keep the finest tiers.
    for (let i = 2; i < n; i++) expect(tiers[i]!).toBeGreaterThanOrEqual(tiers[i - 1]!);
    expect(tiers[n - 1]).toBeGreaterThan(1);
    // The contract's worst case at High: 9 x LOD0, 30 x LOD1, 61 x LOD2 is
    // 1.237 M with look pass 2's tiers (docs/art/character-look.md section 2: the
    // inner hair layer); it fits what a castle at its guideline leaves of 2 M
    // without the governor.
    const high = new Uint8Array(n);
    for (let i = 0; i < n; i++) high[i] = i < 9 ? 0 : i < 39 ? 1 : 2;
    const highAllowance = characterAllowance(TRIANGLE_BUDGET.high, CASTLE_IN_VIEW_TARGET.high);
    expect(9 * TIER_TRIANGLES[0]! + 30 * TIER_TRIANGLES[1]! + 61 * TIER_TRIANGLES[2]!).toBe(1_237_000);
    expect(governTiers(high, order, n, PROTECT_NEAREST, highAllowance, tris)).toBe(1_237_000);
    expect(Array.from(high.subarray(0, 9))).toEqual(new Array(9).fill(0));
    // Low with the perf check's 51 characters: 3 near characters at LOD0 and 10
    // at LOD1 are cut toward what a castle at its guideline leaves of 500 k
    // (200 k). With look pass 2's tier budgets (52 k / 1.35 k) the floor (the
    // nearest three at LOD0, the rest at LOD3) is 220.8 k, just over that
    // allowance: Low is a fallback (minimum spec), so the governor stops there.
    const m = 51;
    const low = new Uint8Array(m);
    for (let i = 0; i < m; i++) low[i] = i < 3 ? 0 : i < 13 ? 1 : 2;
    const lowAllowance = characterAllowance(TRIANGLE_BUDGET.low, CASTLE_IN_VIEW_TARGET.low);
    const lowTotal = governTiers(low, order, m, PROTECT_NEAREST, lowAllowance, tris);
    expect(lowTotal).toBeLessThanOrEqual(Math.max(lowAllowance, 3 * TIER_TRIANGLES[0]! + (m - 3) * TIER_TRIANGLES[3]!));
    expect(lowTotal).toBe(3 * TIER_TRIANGLES[0]! + (m - 3) * TIER_TRIANGLES[3]!);
    expect(lowTotal).toBe(220_800);
    expect([low[0], low[1], low[2]]).toEqual([0, 0, 0]);
    // With 100 listed at Low the floor is the limit: the nearest three at LOD0
    // and 97 at LOD3 (287 k) do not fit 200 k; the governor stops at LOD3.
    const hundred = new Uint8Array(n);
    for (let i = 0; i < n; i++) hundred[i] = i < 3 ? 0 : i < 13 ? 1 : 2;
    expect(governTiers(hundred, order, n, PROTECT_NEAREST, lowAllowance, tris)).toBe(3 * TIER_TRIANGLES[0]! + 97 * TIER_TRIANGLES[3]!);
    // A world over the whole budget: the nearest three keep LOD0, everyone else drops to LOD3.
    const flood = new Uint8Array(n);
    const floodTotal = governTiers(flood, order, n, PROTECT_NEAREST, characterAllowance(TRIANGLE_BUDGET.low, 600_000), tris);
    expect([flood[0], flood[1], flood[2], flood[3], flood[n - 1]]).toEqual([0, 0, 0, 3, 3]);
    expect(floodTotal).toBe(3 * TIER_TRIANGLES[0]! + 97 * TIER_TRIANGLES[3]!);
  });

  it("counts the outline hulls it draws (castle M5c): a Medium crowd of 47 in one room fits 1.2 M drawn", () => {
    // The hull is the tier's geometry drawn again (Medium and High, tiers 0-2, within 34 m).
    expect(drawnTriangles(52_000, true)).toBe(104_000);
    expect(drawnTriangles(52_000, false)).toBe(52_000);
    expect(characterFloor(false)).toBe(CHARACTER_FLOOR);
    expect(characterFloor(true)).toBe(2 * CHARACTER_FLOOR);
    expect(characterAllowance(TRIANGLE_BUDGET.medium, 1_150_000, characterFloor(true))).toBe(2 * CHARACTER_FLOOR);
    // The M5b common-room crowd: 47 characters within 24 m (all outlined), the
    // room's world about 95 k in view; LOD rules first (nearest 9 within 10 m
    // at LOD0, the rest at LOD1), then the governor.
    const n = 47;
    const order = Int32Array.from({ length: n }, (_, i) => i);
    const dist = (i: number) => 2 + (i * 22) / n;
    const outline = { maxTier: 2, fadeEnd: 34 };
    const hullOf = (i: number, t: Tier) => t <= outline.maxTier && dist(i) < outline.fadeEnd;
    const drawn = (i: number, t: Tier) => drawnTriangles(TIER_TRIANGLES[t]!, hullOf(i, t));
    const counted = (_i: number, t: Tier) => TIER_TRIANGLES[t]!; // before: one copy counted
    const start = () => {
      const t = new Uint8Array(n);
      for (let i = 0; i < n; i++) t[i] = tierFor(LOD_RULES.medium, i, dist(i), 1, i === 0);
      return t;
    };
    const world = 95_000;
    const allowance = characterAllowance(TRIANGLE_BUDGET.medium, world, characterFloor(true));
    const sum = (t: Uint8Array) => Array.from(t).reduce((a, tier, i) => a + drawn(i, tier as Tier), 0);
    // Before: the governor kept its count under the allowance but drew far more with the hulls.
    const before = start();
    governTiers(before, order, n, PROTECT_NEAREST, characterAllowance(TRIANGLE_BUDGET.medium, world), counted);
    expect(world + sum(before)).toBeGreaterThan(TRIANGLE_BUDGET.medium);
    // After: what it counts is what is drawn, and that fits the Medium budget with the world.
    const after = start();
    const total = governTiers(after, order, n, PROTECT_NEAREST, allowance, drawn);
    expect(total).toBe(sum(after));
    expect(world + total).toBeLessThanOrEqual(TRIANGLE_BUDGET.medium);
    // The nearest keep LOD0 (the protected three, and the rule's others while they fit).
    expect(Array.from(after.subarray(0, PROTECT_NEAREST))).toEqual([0, 0, 0]);
    for (let i = 1; i < n; i++) expect(after[i]!).toBeGreaterThanOrEqual(after[i - 1]!);
  });

  it("estimates a look's triangles per tier from its parts", () => {
    const body = read({ lodNode: true });
    const parts: PartInput[] = [{ role: "base", part: body }];
    const est = estimateTriangles(parts, Uint8Array.from([0, 1, 1, 1]));
    expect(Array.from(est)).toEqual([2, 2, 2, 2]);
  });
});

describe("shadows", () => {
  it("places a blob under the feet, sized and turned with the character", () => {
    const m = new Float32Array(16);
    blobMatrix(1, 2, 3, 0, 1, m, 0);
    expect(m[0]).toBeCloseTo(BLOB_SIZE[0]);
    expect(m[10]).toBeCloseTo(BLOB_SIZE[1]);
    expect([m[12], m[14]]).toEqual([1, 3]);
    expect(m[13]).toBeGreaterThan(2);
    expect(m[13]).toBeLessThan(2.05);
    blobMatrix(0, 0, 0, Math.PI / 2, 1.1, m, 0);
    // Turned a quarter: the shoulders' axis now along -Z.
    expect(m[2]).toBeCloseTo(-BLOB_SIZE[0] * 1.1);
    expect(m[8]).toBeCloseTo(BLOB_SIZE[1] * 1.1);
  });

  it("makes floors loaded before the first character compile the character shadow", () => {
    const scene = new Scene(new NullEngine());
    const shadows = new CharacterShadows(scene, () => null);
    shadows.setQuality("medium");
    const floor = CreateBox("floor", {}, scene);
    shadows.addReceiver(floor, false);
    const light = scene.lights.find((l) => l.name === "character-shadow")!;
    let dirty = 0;
    const mark = light._markMeshesAsLightDirty.bind(light);
    light._markMeshesAsLightDirty = () => { dirty++; mark(); };
    const a = CreateBox("a", {}, scene);
    const b = CreateBox("b", {}, scene);
    shadows.setCasters([a], 1, 0, 0, 0);
    expect(dirty).toBe(1); // the first caster: receivers re-check the light
    shadows.setCasters([a, b], 2, 0, 0, 0);
    shadows.setCasters([b], 1, 0, 0, 0);
    expect(dirty).toBe(1); // a different set of casters: no recompiles
    shadows.setCasters([], 0, 0, 0, 0);
    expect(dirty).toBe(2); // the last one gone
    scene.dispose();
  });

  it("keeps the character shadow at least 40 degrees up", () => {
    const d = shadowDirection([1, -0.1, 0]);
    expect(Math.asin(-d[1])).toBeCloseTo(MIN_ELEVATION);
    expect(d[0]).toBeGreaterThan(0);
    expect(Math.hypot(...d)).toBeCloseTo(1);
    const steep = shadowDirection([0.2, -1, 0.1]);
    expect(steep[1]).toBeCloseTo(-1 / Math.hypot(0.2, 1, 0.1));
    expect(shadowDirection([0, 1, 0])[1]).toBeCloseTo(-Math.sin(MIN_ELEVATION)); // a light from below never casts up
  });
});

describe("the governor's world in view (castle D1 integration)", () => {
  it("counts the camera's draw phase only, not the shadow maps and other render targets before it", () => {
    const scene = new Scene(new NullEngine());
    const r = new CharacterRenderer(scene, { library: {} as PartLibrary, blobs: false });
    // One frame as Babylon counts it: every Mesh.render adds its indices to the
    // scene's counter, render targets (shadow maps) first, then the draw phase.
    const counter = (scene as unknown as { _activeIndices: { fetchNewFrame(): void; addCount(n: number, fetch: boolean): void } })._activeIndices;
    counter.fetchNewFrame();
    counter.addCount(900, false); // the sun's and the characters' shadow maps
    scene.onBeforeDrawPhaseObservable.notifyObservers(scene);
    counter.addCount(300, false); // the camera's own meshes
    scene.onAfterDrawPhaseObservable.notifyObservers(scene);
    expect(scene.getActiveIndices()).toBe(1200);
    expect((r as unknown as { drawPhaseIndices: number }).drawPhaseIndices).toBe(300);
    r.dispose();
    expect(scene.onBeforeDrawPhaseObservable.hasObservers()).toBe(false);
    scene.dispose();
  });
});

describe("room seats (the classroom's desks)", () => {
  it("seats a sitter on the bench top above the tier, facing the front", () => {
    const manifest = parseInstanceManifest({
      class_charms: { cells: [{ id: "class_charms", file: "class_charms.glb", bounds: { min: [0, 0, 0], max: [1, 1, 1] }, level: 0 }], seats: [{ pos: [-1.95, 0.3, 1.52], yaw: Math.PI }, { pos: "bad" }] },
    });
    const seats = Seats.fromRoomSeats(manifest.get("class_charms")!.seats)!;
    expect(seats.count).toBe(1);
    const at = seatPlace();
    expect(seats.find(-1.9, 0.3, 1.3, at)).toBe(true);
    expect(at.y).toBeCloseTo(0.3 + ROOM_SEAT_HEIGHT);
    expect(at.yaw).toBeCloseTo(Math.PI);
    expect(Math.abs(at.x + 1.95)).toBeLessThan(0.06);
    expect(Seats.fromRoomSeats(undefined)).toBeNull();
  });
});

describe("the measurement switch", () => {
  it("turns off every feature, or only the ones listed", () => {
    expect(Object.values(parseCharacterFx(null)).every(Boolean)).toBe(true);
    expect(Object.values(parseCharacterFx("off")).some(Boolean)).toBe(false);
    const f = parseCharacterFx("-shade,-sway");
    expect([f.shade, f.sway, f.shadows, f.blobs, f.lod0, f.lod]).toEqual([false, false, true, true, true, true]);
    expect(parseCharacterFx("-lod0").lod0).toBe(false);
    const face = parseCharacterFx("-facesdf");
    expect([face.face, face.facesdf, face.shade]).toEqual([true, false, true]);
    expect(parseCharacterFx("-face").face).toBe(false);
  });
});
