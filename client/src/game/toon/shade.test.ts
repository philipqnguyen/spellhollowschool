// Castle D1 (docs/art/castle-kit.md "Shading: the AO bake"): the world shade path, the
// near-camera fade, the world shade fields and the SSAO's look.
import { describe, expect, it } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines/nullEngine";
import { Scene } from "@babylonjs/core/scene";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MaterialDefines } from "@babylonjs/core/Materials/materialDefines";
import { CreateBox } from "@babylonjs/core/Meshes/Builders/boxBuilder";
import { VertexBuffer } from "@babylonjs/core/Buffers/buffer";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import "@babylonjs/core/Shaders/default.fragment";
import toonPaletteJson from "../../../../data/art/toon_palette.json";
import { BASE_RAMP, packWorldFields, parseSsao, resolveRamp, SSAO_DEFAULT, TOON_SSAO, worldNearFades, worldRamp, worldShades } from "./palette";
import { ToonPlugin } from "./material";
import { postPasses, ssaoMultiplier } from "./post";

/** Rows of the castle and the greybox that read the baked shade. */
const SHADED = ["castle_wall_a", "castle_floor_a", "castle_ceiling", "castle_trim", "castle_wood", "castle_props", "castle_portraits_a", "castle_cloth", "wall", "wall_dungeon", "wall_exterior", "wall_low", "trim", "ceiling", "roof"];
/** Rows that never do (glass, sky panels, glows; floor_grounds's COLOR_0 is a hue ramp; kit, furniture, props). */
const UNSHADED = ["castle_glass", "castle_sky", "glow_fire", "floor_grounds", "floor_room", "kit", "furniture_bed", "prop_wood", "fabric_house_a", "water", "dorm_house_a_floor"];

/**
 * A tiny GLSL preprocessor for the tests: keeps the lines whose
 * #ifdef / #ifndef / #if defined(..) (with ||, && and !) / #else / #endif
 * hold for `defines`; other directives and includes pass through.
 */
function preprocess(code: string, defines: ReadonlySet<string>): string {
  const evalIf = (expr: string): boolean => {
    const js = expr.replace(/!?defined\s*\(\s*(\w+)\s*\)/g, (m, n: string) => (m.startsWith("!") ? String(!defines.has(n)) : String(defines.has(n))));
    if (!/^[\s()truefals|&!]*$/.test(js)) return false; // numeric conditions (#if X>0): off
    return Function(`return (${js});`)() as boolean;
  };
  const out: string[] = [];
  const stack: { on: boolean; taken: boolean }[] = [];
  const live = () => stack.every((s) => s.on);
  for (const line of code.split("\n")) {
    const t = line.trim();
    let m: RegExpMatchArray | null;
    if ((m = t.match(/^#ifdef\s+(\w+)/))) {
      const on = defines.has(m[1]!);
      stack.push({ on, taken: on });
    } else if ((m = t.match(/^#ifndef\s+(\w+)/))) {
      const on = !defines.has(m[1]!);
      stack.push({ on, taken: on });
    } else if ((m = t.match(/^#if\s+(.*)$/))) {
      const on = evalIf(m[1]!);
      stack.push({ on, taken: on });
    } else if ((m = t.match(/^#elif\s+(.*)$/))) {
      const s = stack.at(-1)!;
      s.on = !s.taken && evalIf(m[1]!);
      s.taken ||= s.on;
    } else if (t.startsWith("#else")) {
      const s = stack.at(-1)!;
      s.on = !s.taken;
      s.taken = true;
    } else if (t.startsWith("#endif")) {
      stack.pop();
    } else if (live()) {
      out.push(line);
    }
  }
  return out.join("\n");
}

/** Babylon's default fragment shader with the plugin's regex injections applied (as MaterialPluginManager does). */
function injectedFragment(plugin: ToonPlugin): string {
  let code = ShaderStore.ShadersStore["defaultPixelShader"]!;
  const points = plugin.getCustomCode("fragment")!;
  for (const [point, inject] of Object.entries(points)) {
    if (point.startsWith("!")) code = code.replace(new RegExp(point.slice(1), "g"), inject);
    else code = code.replace(`#define ${point}`, `\n${inject}`);
  }
  return code;
}

describe("castle D1: the world shade fields", () => {
  it("are neutral in the base ramp, so rows without them are unchanged", () => {
    expect(BASE_RAMP.valueFloor).toBe(1);
    expect(BASE_RAMP.nearFade).toEqual([0, 0]);
    const r = resolveRamp({});
    expect(worldShades(r)).toBe(false);
    expect(worldNearFades(r)).toBe(false);
    expect(r.characterRim).toBe(false);
  });

  it("clamps valueFloor and turns a backwards nearFade off", () => {
    expect(resolveRamp({ valueFloor: 4 }).valueFloor).toBe(1);
    expect(resolveRamp({ valueFloor: -1 }).valueFloor).toBe(0);
    expect(resolveRamp({ nearFade: [2.5, 1.2] }).nearFade).toEqual([0, 0]);
    expect(resolveRamp({ nearFade: [1.2, 2.5] }).nearFade).toEqual([1.2, 2.5]);
  });

  it("puts the castle and greybox rows on the shade path, never glass, sky, glow or floor_grounds", () => {
    for (const n of SHADED) expect(worldShades(worldRamp(n)), n).toBe(true);
    for (const n of UNSHADED) expect(worldShades(worldRamp(n)), n).toBe(false);
  });

  it("fades only the walk-through rows near the camera", () => {
    expect(worldRamp("castle_props").nearFade).toEqual([1.2, 2.5]);
    expect(worldRamp("castle_cloth").nearFade).toEqual([1.2, 2.5]);
    for (const n of [...SHADED, ...UNSHADED].filter((x) => x !== "castle_props" && x !== "castle_cloth")) expect(worldNearFades(worldRamp(n)), n).toBe(false);
  });

  it("gives the characters' rim to rows that set rimWidth only", () => {
    for (const n of ["castle_wall_a", "castle_trim", "castle_wood", "castle_props"]) expect(worldRamp(n).characterRim, n).toBe(true);
    for (const n of ["castle_floor_a", "castle_ceiling", "castle_cloth", "wall", "kit", "floor_room"]) expect(worldRamp(n).characterRim, n).toBe(false);
  });

  it("packs D = shadeShift, aoFloor, skyLift, rimWidth and E = valueFloor, nearFade, rim mode", () => {
    const d = new Float32Array(4);
    const e = new Float32Array(4);
    const r = worldRamp("castle_props");
    packWorldFields(r, d, e);
    expect([...d]).toEqual([r.shadeShift, r.aoFloor, r.skyLift, r.rimWidth].map(Math.fround));
    expect([...e]).toEqual([r.valueFloor, 1.2, 2.5, 1].map(Math.fround));
    packWorldFields(worldRamp("castle_floor_a"), d, e);
    expect(e[3]).toBe(0);
  });
});

describe("castle D1: the world shade path in the toon plugin", () => {
  const engine = new NullEngine();
  const scene = new Scene(engine);
  const plain = CreateBox("plain", {}, scene);
  const coloured = CreateBox("coloured", {}, scene);
  const n = coloured.getTotalVertices();
  coloured.setVerticesData(VertexBuffer.ColorKind, new Float32Array(n * 4).fill(0.6), false, 4);

  it("reads COLOR_0 as shade only on a shading row with vertex colours", () => {
    const wall = new ToonPlugin(new StandardMaterial("castle_wall_a", scene), worldRamp("castle_wall_a"));
    const defines = new MaterialDefines();
    wall.prepareDefines(defines, scene, coloured);
    expect(defines["TOON_WORLD_SHADE"]).toBe(true);
    wall.prepareDefines(defines, scene, plain);
    expect(defines["TOON_WORLD_SHADE"]).toBe(false);
    coloured.useVertexColors = false;
    wall.prepareDefines(defines, scene, coloured);
    expect(defines["TOON_WORLD_SHADE"]).toBe(false);
    coloured.useVertexColors = true;
    // floor_grounds keeps Babylon's multiply (its COLOR_0 is a hue ramp).
    const grounds = new ToonPlugin(new StandardMaterial("floor_grounds", scene), worldRamp("floor_grounds"));
    grounds.prepareDefines(defines, scene, coloured);
    expect(defines["TOON_WORLD_SHADE"]).toBe(false);
    // Characters never: their vertex colour carries the region.
    const chars = new ToonPlugin(new StandardMaterial("characters", scene), "regions");
    chars.prepareDefines(defines, scene, coloured);
    expect(defines["TOON_WORLD_SHADE"]).toBe(false);
    expect(defines["TOON_NEARFADE"]).toBe(false);
    // Off (plain StandardMaterial lighting for measurements): no shade path either.
    wall.active = false;
    wall.prepareDefines(defines, scene, coloured);
    expect(defines["TOON_WORLD_SHADE"]).toBe(false);
  });

  it("compiles the near-camera fade only into the walk-through rows", () => {
    const defines = new MaterialDefines();
    new ToonPlugin(new StandardMaterial("castle_props", scene), worldRamp("castle_props")).prepareDefines(defines, scene, plain);
    expect(defines["TOON_NEARFADE"]).toBe(true);
    new ToonPlugin(new StandardMaterial("castle_trim", scene), worldRamp("castle_trim")).prepareDefines(defines, scene, plain);
    expect(defines["TOON_NEARFADE"]).toBe(false);
  });

  it("never multiplies the albedo by a per-channel vertex colour on the shade path", () => {
    const wall = new ToonPlugin(new StandardMaterial("castle_wall_a", scene), worldRamp("castle_wall_a"));
    const code = injectedFragment(wall);
    const shaded = preprocess(code, new Set(["TOON", "TOON_WORLD_SHADE", "VERTEXCOLOR", "CUSTOMUSERLIGHTING", "NORMAL"]));
    expect(shaded).toContain("toonShadeX=pow(vColor.r,2.2);");
    // vColor is read once, one channel, as a value; never into the albedo.
    expect(shaded.match(/vColor/g)?.length).toBe(1 + (shaded.match(/varying vec4 vColor;/g)?.length ?? 0));
    expect(shaded).not.toMatch(/vColor\.(rgb|g|b|a|xyz)/);
    expect(shaded).not.toMatch(/baseColor[^;]*vColor/);
    // Without the define (floor_grounds, kit, characters) Babylon's multiply stays.
    const plainCode = preprocess(code, new Set(["TOON", "VERTEXCOLOR", "CUSTOMUSERLIGHTING", "NORMAL"]));
    expect(plainCode).toContain("baseColor.rgb*=vColor.rgb;");
    expect(plainCode).not.toContain("toonShadeX=pow(vColor.r");
  });

  it("keeps the characters' path, the light limit and the character shadow light's slot", () => {
    const chars = new ToonPlugin(new StandardMaterial("characters", scene), "regions");
    const code = injectedFragment(chars);
    const c = preprocess(code, new Set(["TOON", "TOON_REGIONS", "TOON_SHADE", "VERTEXCOLOR", "CUSTOMUSERLIGHTING", "NORMAL"]));
    expect(c).toContain("toonShadeX = vToonShade.x;");
    expect(c).toContain("baseColor.rgb*=vColor.rgb;");
    // The world-only lines are compiled out of the characters' shader.
    expect(c).not.toContain("toonLocalLit *= mix(toonPD.y");
    expect(c).not.toContain("diffuseBase *= mix(toonPE.x");
    const points = chars.getCustomCode("fragment")!;
    expect(Object.keys(points).filter((p) => p.startsWith("CUSTOM_LIGHT")).length).toBe(9);
    expect(points["CUSTOM_LIGHT8_COLOR"]).toContain("toonKind = 3.0;");
  });

  it("takes the sky's hue for the ground term on rows with skyLift (no brown undersides)", () => {
    const wall = new ToonPlugin(new StandardMaterial("castle_wall_a", scene), worldRamp("castle_wall_a"));
    const hemi = preprocess(wall.getCustomCode("fragment")!["CUSTOM_LIGHT0_COLOR"]!, new Set(["TOON", "HEMILIGHT0"]));
    expect(hemi).toContain("if (toonPD.z > 0.0) toonGr = diffuse0.rgb * (dot(toonGr");
    expect(hemi).toContain("max(0.0, 1.0 + toonPD.z * normalW.y)");
  });
});

/** The ground term the world shade path uses for a hemisphere (mirrors CUSTOM_LIGHT{X}_COLOR). */
function groundTerm(sky: [number, number, number], ground: [number, number, number], skyLift: number): [number, number, number] {
  if (skyLift <= 0) return ground;
  const l = (c: readonly number[]) => 0.3 * c[0]! + 0.59 * c[1]! + 0.11 * c[2]!;
  const k = l(ground) / Math.max(l(sky), 1e-4);
  return [sky[0] * k, sky[1] * k, sky[2] * k];
}

describe("castle D1: the ground term (the Grand Staircase look-up)", () => {
  it("keeps the ground's level in the sky's hue", () => {
    const sky: [number, number, number] = [0.8, 0.9, 1.05];
    const brown: [number, number, number] = [0.75, 0.6, 0.45];
    const g = groundTerm(sky, brown, 0.2);
    const l = (c: readonly number[]) => 0.3 * c[0]! + 0.59 * c[1]! + 0.11 * c[2]!;
    expect(l(g)).toBeCloseTo(l(brown), 6);
    // Blue over red, as the sky: no brown cast on undersides.
    expect(g[2] / g[0]).toBeCloseTo(sky[2] / sky[0], 6);
    expect(groundTerm(sky, brown, 0)).toEqual(brown);
  });
});

describe("castle D1: SSAO (High only)", () => {
  it("reads its look from the palette data, a tint, never grey", () => {
    const raw = (toonPaletteJson as unknown as { post: { ssao: typeof SSAO_DEFAULT } }).post.ssao;
    expect(TOON_SSAO).toEqual(parseSsao(toonPaletteJson));
    expect(TOON_SSAO.tint).toEqual(raw.tint);
    expect(Math.max(...TOON_SSAO.tint) - Math.min(...TOON_SSAO.tint)).toBeGreaterThan(0.05);
    expect(parseSsao({})).toEqual(SSAO_DEFAULT);
    const c = parseSsao({ post: { ssao: { tint: [2, -1, 0.5], strength: 3, radius: 0, fadeEnd: -4, characters: 9 } } });
    expect(c.tint).toEqual([1, 0, 0.5]);
    expect(c.strength).toBe(1);
    expect(c.radius).toBeGreaterThan(0);
    expect(c.fadeEnd).toBeGreaterThan(0);
    expect(c.characters).toBe(1);
  });

  it("darkens the world fully, characters lightly, glass, glow and the sky not at all", () => {
    const look = { tint: [0.7, 0.62, 0.8] as [number, number, number], strength: 0.55, radius: 0.6, fadeEnd: 60, characters: 0.3 };
    const world = ssaoMultiplier(0, 0.9, 5, look);
    expect(world[0]).toBeCloseTo(1 - 0.3 * 0.55, 6);
    expect(world[1]).toBeCloseTo(1 - 0.38 * 0.55, 6);
    const ch = ssaoMultiplier(0, 0.25, 5, look);
    expect(1 - ch[0]).toBeCloseTo((1 - world[0]) * 0.3, 6);
    expect(ssaoMultiplier(0, 0.5, 5, look)).toEqual([1, 1, 1]);
    expect(ssaoMultiplier(0, 0.01, 5, look)).toEqual([1, 1, 1]);
    // Open (ao 1) and far beyond fadeEnd: no change.
    expect(ssaoMultiplier(1, 0.9, 5, look)).toEqual([1, 1, 1]);
    expect(ssaoMultiplier(0, 0.9, 61, look)).toEqual([1, 1, 1]);
    // A tint: the channels darken by different amounts (mauve, not grey).
    expect(world[1]).toBeLessThan(world[2]);
  });

  it("adds two passes on High and none at Medium or Low", () => {
    expect(postPasses({ bloom: true, ssao: true }).length - postPasses({ bloom: true }).length).toBe(2);
  });
});
