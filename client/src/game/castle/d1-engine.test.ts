// Castle D1, the Engine's part (docs/art/castle-kit.md): the look
// probe's samples and arithmetic, its targets against grid.py, the F3
// overlay's SSAO and shade parts, the probe's SSAO switch, and the trim
// sheet at full size on Medium again.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { worldRamp, BASE_RAMP } from "../toon/palette";
import { parseTexturesJson } from "./materials";
import { materials as KIT_MATERIALS } from "../../../../data/art/castle_textures.json";
import {
  cornerRays,
  groupedRatio,
  hueSat,
  lightTint,
  lookMetrics,
  lookView,
  luminance,
  LOOK_VIEWS,
  midRays,
  onFloor,
  onSoffit,
  onWall,
  PROBE_BROWN_HUE,
  PROBE_CORNER_RATIO,
  PROBE_SOFFIT_GRADE,
  PROBE_SOFFIT_RATIO,
  PROBE_TINT_SAT,
  PROBE_WALL_SPREAD,
  srgbToLinear,
  tintPasses,
  wallColumn,
  wallSpread,
  type LookSample,
  type LookSetResult,
  type Vec3,
} from "./look-probe";
import { readsShade, shadeSummary, ssaoState } from "./shade";
import { setSsao, ssaoOf, SSAO_PASS } from "../perf-probe";

const REPO = join(__dirname, "..", "..", "..", "..");

const sample = (lum: number, extra: Partial<LookSample> = {}): LookSample => ({ lum, rgb: [lum, lum, lum], albedo: null, y: 0, material: "castle_wall_a", ...extra });
const set = (name: string, samples: LookSample[]): LookSetResult => ({ name, samples, dropped: {} });

describe("the look probe's targets are grid.py's", () => {
  const grid = readFileSync(join(REPO, "tools/blender/castle_kit/grid.py"), "utf8");
  const pair = (name: string): [number, number] => {
    const m = new RegExp(`^${name} = \\(([-\\d.]+), ([-\\d.]+)\\)`, "m").exec(grid);
    if (!m) throw new Error(`${name} not in grid.py`);
    return [Number(m[1]), Number(m[2])];
  };
  const one = (name: string): number => {
    const m = new RegExp(`^${name} = ([-\\d.]+)`, "m").exec(grid);
    if (!m) throw new Error(`${name} not in grid.py`);
    return Number(m[1]);
  };
  it("matches every PROBE_ constant", () => {
    expect(PROBE_CORNER_RATIO).toEqual(pair("PROBE_CORNER_RATIO"));
    expect(PROBE_SOFFIT_RATIO).toEqual(pair("PROBE_SOFFIT_RATIO"));
    expect(PROBE_SOFFIT_GRADE).toEqual(pair("PROBE_SOFFIT_GRADE"));
    expect(PROBE_BROWN_HUE).toEqual(pair("PROBE_BROWN_HUE"));
    expect(PROBE_WALL_SPREAD).toBe(one("PROBE_WALL_SPREAD"));
    expect(PROBE_TINT_SAT).toBe(one("PROBE_TINT_SAT"));
  });
});

describe("the look probe's colour arithmetic", () => {
  it("linearises sRGB and weighs luminance as the calibration (Rec. 709)", () => {
    expect(srgbToLinear(0)).toBe(0);
    expect(srgbToLinear(255)).toBeCloseTo(1, 6);
    expect(srgbToLinear(188)).toBeCloseTo(0.5029, 3);
    expect(luminance(1, 1, 1)).toBeCloseTo(1, 6);
    expect(luminance(0, 1, 0)).toBeCloseTo(0.7152, 6);
  });

  it("gives hue and saturation like Python's colorsys", () => {
    expect(hueSat([1, 0, 0])).toEqual({ hue: 0, sat: 1 });
    expect(hueSat([0, 0, 1]).hue).toBeCloseTo(240, 6);
    expect(hueSat([0.5, 0.5, 0.5]).sat).toBe(0);
    // The cream sandstone albedo #E4D2B0: hue 39, saturation 0.23.
    const s = hueSat([0xe4, 0xd2, 0xb0]);
    expect(s.hue).toBeCloseTo(39.2, 1);
    expect(s.sat).toBeCloseTo(0.228, 2);
  });

  it("judges the light on undersides, not the stone: brown light fails, grey or cool light passes", () => {
    // Pre-D1 look-up (measured on this branch): t hue 18, saturation 0.35.
    expect(tintPasses([1, 0.8, 0.65])).toBe(false);
    expect(tintPasses([0.5, 0.49, 0.48])).toBe(true); // nearly grey
    expect(tintPasses([0.8, 0.75, 0.95])).toBe(true); // mauve: hue outside 15-50
    const albedo: Vec3 = [0.6, 0.5, 0.35];
    const lit = (k: Vec3): LookSample => sample(0, { rgb: [albedo[0] * k[0], albedo[1] * k[1], albedo[2] * k[2]], albedo });
    const t = lightTint([lit([0.4, 0.4, 0.42]), lit([0.4, 0.4, 0.42]), lit([0.4, 0.4, 0.42])]);
    expect(t.n).toBe(3);
    expect(t.t![0]).toBeCloseTo(0.4, 6);
    expect(t.t![2]).toBeCloseTo(0.42, 6);
    expect(lightTint([lit([1, 1, 1])]).t).toBeNull(); // too few
  });
});

describe("the look probe's samples", () => {
  it("casts onto walls from 1.5 m out, accepting mouldings up to 0.35 m proud", () => {
    const r = onWall([-28, 0.3, 13], [0, 0, -1], "z13");
    expect(r.from).toEqual([-28, 0.3, 11.5]);
    expect(r.dir).toEqual([-0, -0, 1]);
    expect([r.near, r.far]).toEqual([1.15, 1.55]);
    expect(r.group).toBe("z13");
    expect(onSoffit([0, 9, 0])).toMatchObject({ from: [0, 7, 0], dir: [0, 1, 0] });
    expect(onFloor([0, 27, 0])).toMatchObject({ from: [0, 28, 0], dir: [0, -1, 0] });
  });

  it("places corner samples 0.25-0.35 m from the corner and 0.2-0.4 m above the floor", () => {
    const rays = cornerRays([-59, -28], 63, [1, 0, 0], [0, 0, 1], "z-28");
    expect(rays).toHaveLength(9);
    for (const r of rays) {
      const x = r.from[0];
      const y = r.from[1];
      expect(x - -59).toBeGreaterThanOrEqual(0.25 - 1e-9);
      expect(x - -59).toBeLessThanOrEqual(0.35 + 1e-9);
      expect(y - 63).toBeGreaterThanOrEqual(0.2 - 1e-9);
      expect(y - 63).toBeLessThanOrEqual(0.4 + 1e-9);
      expect(r.from[2]).toBeCloseTo(-26.5, 9);
    }
    expect(midRays([[-50, -28]], 63, [0, 0, 1], "z-28").map((r) => r.from[1])).toEqual([63.2, 63.3, 63.4]);
    const col = wallColumn(40, 9, [0, 0, 1], 3.4, 4.4);
    expect(col.map((r) => r.from[1])).toEqual([3.4, 3.65, 3.9, 4.15, 4.4]);
    expect(new Set(col.map((r) => r.group))).toEqual(new Set(["40,9"]));
  });

  it("covers the framings of 19.10, each with samples for its numbers", () => {
    expect(LOOK_VIEWS.map((v) => v.id)).toEqual(["gf_eye", "eh_doors", "gh_long", "gh_side_bay", "gs_up", "gs_l3", "gs_guardian"]);
    for (const v of LOOK_VIEWS) {
      const names = new Set(v.sets.map((s) => s.name));
      for (const m of v.metrics) {
        const own = m.kind === "corner" ? [m.corner, m.mid] : m.kind === "grade" ? [m.wall, m.edge] : m.kind === "soffit" ? [m.soffit, ...(m.view ? [] : [m.floor])] : [m.set];
        for (const n of own) expect(names, `${v.id} ${m.kind}`).toContain(n);
        if (m.kind === "soffit" && m.view) expect(lookView(m.view)!.sets.map((s) => s.name)).toContain(m.floor);
      }
      for (const s of v.sets) {
        expect(s.rays.length, `${v.id} ${s.name}`).toBeGreaterThan(0);
        for (const r of s.rays) expect(Math.hypot(...r.dir)).toBeCloseTo(1, 9);
      }
    }
    // Every number of 19.10 is measured somewhere.
    const kinds = new Set(LOOK_VIEWS.flatMap((v) => v.metrics.map((m) => m.kind)));
    expect(kinds).toEqual(new Set(["corner", "soffit", "grade", "spread", "tint"]));
  });
});

describe("the look probe's numbers", () => {
  it("compares a corner with the middle of its own wall, then averages the walls", () => {
    const corner = [...[0.2, 0.2, 0.2].map((l) => sample(l, { group: "a" })), ...[0.6, 0.6, 0.6].map((l) => sample(l, { group: "b" }))];
    const mid = [...[0.25, 0.25, 0.25].map((l) => sample(l, { group: "a" })), ...[0.8, 0.8, 0.8].map((l) => sample(l, { group: "b" }))];
    const r = groupedRatio(corner, mid);
    expect(r.value).toBeCloseTo(0.775, 9); // (0.8 + 0.75) / 2, not 0.8 / 1.05
    expect(r.n).toBe(6);
    // A wall with too few samples on one side does not count.
    expect(groupedRatio([...corner.slice(0, 2), ...corner.slice(3)], mid).value).toBeCloseTo(0.75, 9);
    expect(groupedRatio([], mid).value).toBeNull();
  });

  it("takes a wall's spread from its floor band to its cornice band, per column", () => {
    const col = (g: string, bottom: number, top: number) =>
      [0.7, 0.95, 1.2, 1.45, 3, 4, 5, 6.5, 7, 7.5, 8].map((y) => sample(y < 2 ? bottom : y > 6 ? top : (bottom + top) / 2, { y, group: g }));
    const s = wallSpread([...col("a", 0.3, 0.4), ...col("b", 0.36, 0.4)]);
    expect(s.value).toBeCloseTo((0.25 + 0.1) / 2, 9);
    // Too short a wall gives nothing.
    expect(wallSpread([0.7, 0.9, 1.1, 1.3, 1.5, 1.7].map((y) => sample(0.3, { y, group: "c" }))).value).toBeNull();
  });

  it("checks each number against its target and reads cross-view sets", () => {
    const up = lookView("gs_up")!;
    const soffit = [0.1, 0.1, 0.1, 0.12, 0.12, 0.12].map((l) => sample(l, { material: "castle_ceiling" }));
    const sets = {
      soffit_wall: set("soffit_wall", soffit.slice(0, 3)),
      soffit_edge: set("soffit_edge", soffit.slice(3)),
      soffit: set("soffit", soffit),
    };
    const floor = { floor: set("floor", [0.2, 0.2, 0.2].map((l) => sample(l, { material: "castle_floor_square" }))) };
    const m = lookMetrics(up, sets, { gs_l3: floor });
    const grade = m.find((x) => x.kind === "grade")!;
    expect(grade.value).toBeCloseTo(0.1 / 0.12, 9);
    expect(grade.pass).toBe(true);
    const ratio = m.find((x) => x.kind === "soffit")!;
    expect(ratio.value).toBeCloseTo(0.11 / 0.2, 9);
    expect(ratio.pass).toBe(true);
    const tint = m.find((x) => x.kind === "tint")!;
    expect(tint.value).toBeNull(); // no albedo
    expect(tint.pass).toBeNull();
    // Without gs_l3 measured the look-up's soffit ratio waits.
    expect(lookMetrics(up, sets).find((x) => x.kind === "soffit")!.value).toBeNull();
  });
});

describe("the F3 overlay's D1 parts", () => {
  it("counts the materials on the shade path from their rows", () => {
    expect(readsShade({ shadeShift: 0, aoFloor: 1 })).toBe(false);
    expect(readsShade({ shadeShift: 0.35, aoFloor: 1 })).toBe(true);
    expect(readsShade({ shadeShift: 0, aoFloor: 0.55 })).toBe(true);
    expect(readsShade({ shadeShift: 0, aoFloor: 1, valueFloor: 0.85 })).toBe(true);
    expect(readsShade(BASE_RAMP)).toBe(false);
    // The D1 rows of toon_palette.json: the castle walls read the shade, glass and sky do not.
    expect(readsShade(worldRamp("castle_wall_a"))).toBe(true);
    expect(readsShade(worldRamp("castle_glass"))).toBe(false);
    expect(readsShade(worldRamp("castle_sky"))).toBe(false);
    expect(shadeSummary(["castle_wall_a", "castle_floor_long", "castle_glass"], (n) => worldRamp(n))).toBe("shade 2/3");
    expect(shadeSummary([], (n) => worldRamp(n))).toBe("shade 0/0");
  });

  it("shows SSAO on, off, or none for a build without it", () => {
    expect(ssaoState({ ssao: true })).toBe("on");
    expect(ssaoState({ ssao: false })).toBe("off");
    expect(ssaoState({ outlines: true })).toBe("none");
  });
});

describe("the probe's SSAO switch", () => {
  it("reads and switches the live view's SSAO through its profile", () => {
    class View {
      profile: { outlines: boolean; ssao: boolean } = { outlines: true, ssao: true };
      applied = 0;
      get quality() {
        return this.profile;
      }
      applyProfile() {
        this.applied++;
      }
    }
    const v = new View();
    expect(ssaoOf(v)).toBe(true);
    expect(setSsao(v, false)).toBe(false);
    expect(v.profile).toEqual({ outlines: true, ssao: false });
    expect(v.applied).toBe(1);
    // A view with its own switch is used as it is.
    const calls: boolean[] = [];
    const w = { quality: { ssao: false }, setSsao: (on: boolean) => (calls.push(on), (w.quality.ssao = on)) };
    expect(setSsao(w, true)).toBe(true);
    expect(calls).toEqual([true]);
    // A build without SSAO.
    expect(ssaoOf({ quality: { outlines: true } })).toBeNull();
    expect(setSsao({ quality: {} }, true)).toBeNull();
    expect(setSsao(undefined, true)).toBeNull();
  });

  it("times the passes named for SSAO only", () => {
    expect(["toonSsao", "toonSsaoBlur", "ssao"].every((n) => SSAO_PASS.test(n))).toBe(true);
    expect(["toonBright", "toonBlur", "toonFinal"].some((n) => SSAO_PASS.test(n))).toBe(false);
  });
});

describe("the trim sheet at Medium", () => {
  it("is full size at Medium again: castle_trim has no medium flag", () => {
    const trim = (KIT_MATERIALS as Record<string, { medium?: string }>).castle_trim!;
    expect(trim).toBeDefined();
    expect(trim.medium).toBeUndefined();
    for (const [name, m] of Object.entries(KIT_MATERIALS as Record<string, { medium?: string }>)) expect(m.medium, name).toBeUndefined();
  });

  it("parses the built textures.json entry without it as full size at Medium", () => {
    const specs = parseTexturesJson({ version: 1, materials: { castle_trim: { base: "trim_castle.png", lo: "trim_castle_lo.png", px: [2048, 2048] } } });
    expect(specs.get("castle_trim")!.mediumLo).toBe(false);
  });
});
