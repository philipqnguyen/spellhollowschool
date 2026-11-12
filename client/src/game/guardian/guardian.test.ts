import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { VisibleEffect } from "../../wasm/sim_wasm";
import {
  angleDelta,
  followRoom,
  gaitPose,
  GUARD_HEIGHT,
  GUARD_RADIUS,
  GUARD_SIZE,
  guardScale,
  KEEP_MAX,
  KEEP_MIN,
  newFollow,
  newPose,
  PRESENCE,
  smoothDamp,
  stepFollow,
  stepGuard,
  SUMMON_ARC,
  SUMMON_EASE,
  SUMMON_S,
  SUMMON_START_SCALE,
  summonAt,
  type Holder,
  type SummonPose,
} from "./follow";
import {
  DISSOLVE_S,
  MATERIALISE_S,
  MAX_DRAWN,
  MOTES_IDLE,
  MOTES_MOVING,
  motesDue,
  guardianOf,
  pickHolders,
  shownAt,
} from "./hub";
import { ANIMALS, parseManifest } from "./models";
import { MAX_PARTS, partMatrices, transformPoint, type Rig } from "./rig";

const DT = 1 / 60;

function walk(h: Holder, yaw: number, speed: number, dt: number): void {
  h.yaw = yaw;
  h.x += Math.sin(yaw) * speed * dt;
  h.z += Math.cos(yaw) * speed * dt;
}

describe("following", () => {
  it("converges behind its holder, keeps the band and faces its travel", () => {
    const s = newFollow();
    const h: Holder = { x: 10, y: 2, z: -4, yaw: 0 };
    for (const speed of [3, 6.5]) {
      for (let i = 0; i < 6 * 60; i++) {
        walk(h, 0, speed, DT);
        stepFollow(s, h, 0, DT);
        const d = Math.hypot(s.x - h.x, s.z - h.z);
        expect(d).toBeGreaterThanOrEqual(KEEP_MIN - 1e-6);
        expect(d).toBeLessThanOrEqual(KEEP_MAX + 1e-6);
      }
      // Behind (along the holder's facing) and a little to one side.
      expect(s.z - h.z).toBeLessThan(-0.8);
      expect(Math.abs(s.x - h.x)).toBeLessThan(0.9);
      // Travelling with the holder, facing the same way.
      expect(s.speed).toBeCloseTo(speed, 0);
      expect(Math.abs(angleDelta(s.yaw, 0))).toBeLessThan(0.05);
    }
    // A turn: it swings round and faces the new way.
    for (let i = 0; i < 4 * 60; i++) {
      walk(h, Math.PI / 2, 3, DT);
      stepFollow(s, h, 0, DT);
    }
    expect(s.x - h.x).toBeLessThan(-0.8);
    expect(Math.abs(angleDelta(s.yaw, Math.PI / 2))).toBeLessThan(0.1);
  });

  it("never walks into its holder (the band holds from inside too)", () => {
    const s = newFollow();
    const h: Holder = { x: 0, y: 0, z: 0, yaw: 0 };
    stepFollow(s, h, 0, DT);
    // The holder backs into it.
    for (let i = 0; i < 3 * 60; i++) {
      walk(h, Math.PI, 2, DT);
      h.yaw = 0;
      stepFollow(s, h, 0, DT);
      expect(Math.hypot(s.x - h.x, s.z - h.z)).toBeGreaterThanOrEqual(
        KEEP_MIN - 1e-6,
      );
    }
  });

  it("wanders a small circle while its holder stands still", () => {
    const s = newFollow();
    const h: Holder = { x: 0, y: 0, z: 0, yaw: 1 };
    stepFollow(s, h, 0, DT);
    let minX = Infinity,
      maxX = -Infinity,
      minZ = Infinity,
      maxZ = -Infinity;
    let moving = 0;
    for (let i = 0; i < 10 * 60; i++) {
      stepFollow(s, h, 0, DT);
      const d = Math.hypot(s.x - h.x, s.z - h.z);
      expect(d).toBeGreaterThanOrEqual(KEEP_MIN - 1e-6);
      expect(d).toBeLessThanOrEqual(KEEP_MAX + 1e-6);
      if (i > 60) {
        minX = Math.min(minX, s.x);
        maxX = Math.max(maxX, s.x);
        minZ = Math.min(minZ, s.z);
        maxZ = Math.max(maxZ, s.z);
        if (s.speed > 0.05) moving++;
      }
    }
    expect(maxX - minX).toBeGreaterThan(0.25);
    expect(maxZ - minZ).toBeGreaterThan(0.25);
    expect(moving).toBeGreaterThan(8 * 60);
  });

  it("flies the owl at its follow height and snaps after a teleport", () => {
    const s = newFollow();
    const h: Holder = { x: 0, y: 3, z: 0, yaw: 0 };
    for (let i = 0; i < 3 * 60; i++) stepFollow(s, h, 1.45, DT);
    expect(s.y).toBeCloseTo(4.45, 2);
    h.x = 50;
    stepFollow(s, h, 1.45, DT);
    expect(Math.hypot(s.x - h.x, s.z - h.z)).toBeLessThanOrEqual(KEEP_MAX);
  });

  it("smoothDamp settles without overshoot", () => {
    let x = 0;
    const v: [number] = [0];
    let max = 0;
    for (let i = 0; i < 120; i++) {
      x = smoothDamp(x, v[0], 1, 0.3, DT, v);
      max = Math.max(max, x);
    }
    expect(x).toBeCloseTo(1, 2);
    expect(max).toBeLessThanOrEqual(1 + 1e-6);
  });
});

describe("guarding", () => {
  it("circles the duelist at the orbit radius and chest height, facing out", () => {
    const s = newFollow();
    const h: Holder = { x: 5, y: 1, z: 5, yaw: 0 };
    const midY = 0.3;
    const angles = new Set<number>();
    for (let i = 0; i < 120; i++) {
      stepGuard(s, h, midY, DT);
      const dx = s.x - h.x;
      const dz = s.z - h.z;
      expect(Math.hypot(dx, dz)).toBeCloseTo(GUARD_RADIUS, 5);
      expect(s.y).toBeCloseTo(h.y + GUARD_HEIGHT - midY, 5);
      // Facing out: its forward is the radius.
      expect(Math.abs(angleDelta(s.yaw, Math.atan2(dx, dz)))).toBeLessThan(
        1e-5,
      );
      angles.add(Math.round(Math.atan2(dx, dz) * 2));
    }
    expect(angles.size).toBeGreaterThan(4);
  });

  it("shrinks a big animal to fit round a duelist", () => {
    expect(guardScale([0.4, 1.6, 1.7])).toBeCloseTo(GUARD_SIZE / 1.7, 5);
    expect(guardScale([0.2, 0.5, 0.5])).toBe(1);
  });
});

describe("the hub's rules", () => {
  const fx = (animal: "owl" | "fox", guarding = false): VisibleEffect => ({
    kind: "guardian",
    animal,
    guarding,
  });

  it("draws companions for the nearest 12 holders only", () => {
    const subjects = Array.from({ length: 30 }, (_, i) => ({ id: i + 1 }));
    const effects = (id: number): VisibleEffect[] =>
      id % 2 === 0
        ? [{ kind: "lucerna" }, fx("fox")]
        : [{ kind: "glow", colour: 1 } as VisibleEffect];
    const out: number[] = [];
    pickHolders(subjects, effects, MAX_DRAWN, out);
    expect(MAX_DRAWN).toBe(12);
    expect(out).toEqual([2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24]);
    expect(guardianOf([{ kind: "lucerna" }, fx("owl", true)])).toEqual(
      fx("owl", true),
    );
    expect(guardianOf([])).toBeNull();
  });

  it("materialises and dissolves on time", () => {
    expect(shownAt(0, null)).toBe(0);
    expect(shownAt(MATERIALISE_S / 2, null)).toBeCloseTo(0.5, 5);
    expect(shownAt(MATERIALISE_S, null)).toBe(1);
    expect(shownAt(60, null)).toBe(1);
    expect(shownAt(60, 0)).toBe(1);
    expect(shownAt(60, DISSOLVE_S / 2)).toBeCloseTo(0.5, 5);
    expect(shownAt(60, DISSOLVE_S)).toBe(0);
    // Dissolving while still forming fades from where it was.
    expect(shownAt(MATERIALISE_S / 2, DISSOLVE_S / 2)).toBeCloseTo(0.25, 5);
    let last = 0;
    for (let t = 0; t <= MATERIALISE_S; t += 0.01) {
      const v = shownAt(t, null);
      expect(v).toBeGreaterThanOrEqual(last);
      last = v;
    }
  });
});

describe("gaits and the rig", () => {
  const rig: Rig = {
    roles: ["body", "head", "leg_fl", "leg_fr", "leg_bl", "leg_br", "tail"],
    pivots: new Float32Array([
      0, 0.3, 0, 0, 0.4, 0.2, -0.05, 0.3, 0.2, 0.05, 0.3, 0.2, -0.05, 0.3, -0.2,
      0.05, 0.3, -0.2, 0, 0.35, -0.3, 0, 0, 0,
    ]),
  };

  it("swings each part about its own joint", () => {
    const pose = gaitPose("trot", 0.25, 1, 0, newPose());
    const m = partMatrices(rig, pose, new Float32Array(MAX_PARTS * 16));
    for (let i = 0; i < rig.roles.length; i++) {
      const p = [
        rig.pivots[i * 3]!,
        rig.pivots[i * 3 + 1]!,
        rig.pivots[i * 3 + 2]!,
      ] as const;
      const q = transformPoint(m, i * 16, p[0], p[1], p[2]);
      // The joint stays put but for the body's lift.
      expect(q[0]).toBeCloseTo(p[0], 5);
      expect(q[1]).toBeCloseTo(p[1] + pose.bodyY, 5);
      expect(q[2]).toBeCloseTo(p[2], 5);
    }
    // A positive swing brings the foot forward: leg_fl's foot (0.3 m below its hip).
    expect(pose.legs[0]).toBeGreaterThan(0);
    const foot = transformPoint(m, 2 * 16, -0.05, 0, 0.2);
    expect(foot[2]).toBeGreaterThan(0.2);
    // Trot: diagonal pairs together.
    expect(pose.legs[0]).toBeCloseTo(pose.legs[3], 6);
    expect(pose.legs[1]).toBeCloseTo(pose.legs[2], 6);
    expect(pose.legs[0]).toBeCloseTo(-pose.legs[1], 6);
  });

  it("stands still at a standstill and lifts both wing tips on a positive beat", () => {
    const still = gaitPose("gallop", 0.3, 0, 0, newPose());
    for (const l of still.legs) expect(Math.abs(l)).toBe(0);
    const wings: Rig = {
      roles: ["body", "wing_l", "wing_r"],
      pivots: new Float32Array([0, 0, 0, -0.05, 0, 0, 0.05, 0, 0]),
    };
    const pose = newPose();
    pose.wings[0] = pose.wings[1] = 0.6;
    const m = partMatrices(wings, pose, new Float32Array(MAX_PARTS * 16));
    expect(transformPoint(m, 16, -0.45, 0, 0)[1]).toBeGreaterThan(0.1);
    expect(transformPoint(m, 32, 0.45, 0, 0)[1]).toBeGreaterThan(0.1);
  });

  it("every gait moves the animal when it goes", () => {
    for (const gait of [
      "hop",
      "trot",
      "glide",
      "fly",
      "swim",
      "gallop",
    ] as const) {
      let spread = 0;
      for (let p = 0; p < 1; p += 0.05) {
        const pose = gaitPose(gait, p, 1, 0, newPose());
        spread = Math.max(
          spread,
          Math.abs(pose.legs[0]) +
            Math.abs(pose.wings[0]) +
            Math.abs(pose.bodyY) +
            Math.abs(pose.bodyPitch),
        );
      }
      expect(spread, gait).toBeGreaterThan(0.1);
    }
  });
});

describe("the manifest", () => {
  const sample = {
    version: 1,
    glb: "guardian.glb",
    animals: Object.fromEntries(
      ANIMALS.map((key) => {
        const parts =
          key === "owl"
            ? ["body", "head", "wing_l", "wing_r", "tail"]
            : ["body", "head", "leg_fl", "leg_fr", "leg_bl", "leg_br", "tail"];
        return [
          key,
          {
            parts,
            nodes: Object.fromEntries(
              parts.map((p) => [p, `guardian_${key}_${p}`]),
            ),
            size_m: [0.3, 0.5, 0.6],
            gait: {
              hare: "hop",
              fox: "trot",
              cat: "glide",
              owl: "fly",
              otter: "swim",
              horse: "gallop",
            }[key],
            follow_height_m: key === "owl" ? 1.45 : 0,
            pivots: Object.fromEntries(parts.map((p) => [p, [0, 0.2, 0]])),
          },
        ];
      }),
    ),
  };

  it("parses, and refuses a malformed one", () => {
    const m = parseManifest(sample);
    expect(m.owl.followHeight).toBe(1.45);
    expect(m.owl.parts).toContain("wing_l");
    expect(m.horse.gait).toBe("gallop");
    expect(() => parseManifest({})).toThrow();
    const bad = structuredClone(sample) as typeof sample;
    (bad.animals.fox as { gait: string }).gait = "moonwalk";
    expect(() => parseManifest(bad)).toThrow();
    const noBody = structuredClone(sample) as typeof sample;
    (noBody.animals.cat as { parts: string[] }).parts = ["head"];
    expect(() => parseManifest(noBody)).toThrow();
  });

  it("reads the built manifest when the assets are there", () => {
    const path = resolve(
      __dirname,
      "../../../public/assets/guardian/manifest.json",
    );
    if (!existsSync(path)) return;
    const m = parseManifest(JSON.parse(readFileSync(path, "utf8")));
    for (const key of ANIMALS)
      expect(m[key].parts.length).toBeLessThanOrEqual(MAX_PARTS);
    expect(m.owl.followHeight).toBeGreaterThan(1.2);
  });
});

// The built manifest (client/public/assets): absent in a checkout that has
// neither built nor fetched the assets, as in CI.
const BUILT_MANIFEST = resolve(
  __dirname,
  "../../../public/assets/guardian/manifest.json",
);
const HAVE_MANIFEST = existsSync(BUILT_MANIFEST);

describe.skipIf(!HAVE_MANIFEST)("presence", () => {
  const manifest = HAVE_MANIFEST
    ? parseManifest(JSON.parse(readFileSync(BUILT_MANIFEST, "utf8")))
    : (null as unknown as ReturnType<typeof parseManifest>);

  it("draws each animal about its real size, the horse still fitting indoors", () => {
    const drawn = (k: (typeof ANIMALS)[number]) =>
      manifest[k].size.map((v) => v * PRESENCE[k]);
    // Nose to tail (z), height (y), wingspan (x) in metres.
    expect(drawn("hare")[2]).toBeGreaterThan(0.6);
    expect(drawn("cat")[2]).toBeGreaterThan(0.85);
    expect(drawn("fox")[2]).toBeGreaterThan(1.4);
    expect(drawn("otter")[2]).toBeGreaterThan(1.2);
    expect(drawn("owl")[0]).toBeGreaterThan(1.1);
    const horse = drawn("horse");
    expect(horse[1]).toBeGreaterThan(1.5);
    expect(horse[1]).toBeLessThan(1.8);
    expect(horse[2]).toBeLessThan(2.0);
    for (const k of ANIMALS) {
      // Bigger than the model (presence), never more than half as big again.
      expect(PRESENCE[k]).toBeGreaterThanOrEqual(1);
      expect(PRESENCE[k]).toBeLessThanOrEqual(1.5);
    }
  });

  it("keeps a long animal clear of its holder", () => {
    expect(followRoom(0.7)).toBe(0);
    const room = followRoom(1.72 * PRESENCE.horse);
    expect(room).toBeGreaterThan(0.4);
    const s = newFollow();
    const h: Holder = { x: 0, y: 0, z: 0, yaw: 0 };
    for (let i = 0; i < 4 * 60; i++) {
      walk(h, 0.3, 2.5, DT);
      stepFollow(s, h, 0, DT, room);
      const d = Math.hypot(s.x - h.x, s.z - h.z);
      expect(d).toBeGreaterThanOrEqual(KEEP_MIN + room - 1e-6);
      expect(d).toBeLessThanOrEqual(KEEP_MAX + room + 1e-6);
    }
  });
});

describe("the summon", () => {
  it("bursts from the wand tip small, bounds up and over, and lands at its place full size", () => {
    const p: SummonPose = { x: 0, y: 0, z: 0, grow: 0 };
    const tip = [0.4, 1.2, 0.3] as const;
    const to = [-1.0, 0, -1.2] as const;
    expect(summonAt(0, ...tip, ...to, p)).toBe(true);
    expect([p.x, p.y, p.z]).toEqual([...tip]);
    expect(p.grow).toBeCloseTo(SUMMON_START_SCALE, 5);
    // On the way it arcs above the straight line between tip and place.
    summonAt(SUMMON_S * 0.3, ...tip, ...to, p);
    const e = 1 - Math.pow(0.7, SUMMON_EASE);
    expect(p.y).toBeGreaterThan(tip[1] + (to[1] - tip[1]) * e + SUMMON_ARC * 0.5);
    // Moving steadily toward its place, growing.
    let last = Infinity;
    let grow = 0;
    for (let t = 0; t < SUMMON_S; t += 0.02) {
      summonAt(t, ...tip, ...to, p);
      const d = Math.hypot(p.x - to[0], p.z - to[2]);
      expect(d).toBeLessThanOrEqual(last + 1e-9);
      expect(p.grow).toBeGreaterThanOrEqual(grow);
      last = d;
      grow = p.grow;
    }
    expect(summonAt(SUMMON_S, ...tip, ...to, p)).toBe(false);
    expect(p.x).toBeCloseTo(to[0], 5);
    expect(p.y).toBeCloseTo(to[1], 5);
    expect(p.z).toBeCloseTo(to[2], 5);
    expect(p.grow).toBe(1);
  });
});

describe("light motes", () => {
  it("sheds a few standing and more at a stride, carrying the fraction", () => {
    const carry = { owed: 0 };
    let idle = 0;
    for (let i = 0; i < 60; i++) idle += motesDue(0, 3, DT, carry);
    // (A second of frames sums to the rate, within one for float rounding.)
    expect(Math.abs(idle - MOTES_IDLE)).toBeLessThanOrEqual(1);
    carry.owed = 0;
    let moving = 0;
    for (let i = 0; i < 60; i++) moving += motesDue(3, 3, DT, carry);
    expect(Math.abs(moving - MOTES_MOVING)).toBeLessThanOrEqual(1);
    expect(moving).toBeGreaterThan(idle * 3);
    // Twelve companions striding along stay well inside the shared particle pool (480).
    expect(12 * MOTES_MOVING * 0.9).toBeLessThan(480 * 0.5);
  });
});
