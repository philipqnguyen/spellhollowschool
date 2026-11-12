// The toon look's screen passes: a subtle bloom and, on High, screen-space
// ambient occlusion, in as few full-screen passes as possible.
//
// Outlines (character look pass 2, docs/art/character-look.md section 4): the
// screen-space outline pass that drew a one-pixel dark line round every
// silhouette and crease of the castle and the characters is gone.
// Characters draw Genshin-style inverted-hull outlines instead (toon/
// outline.ts: a second, skinned draw of each character, tinted per
// material, anti-aliased by the scene's MSAA like any other edge). The
// castle has no outline: like Genshin's environments it reads through its
// shading (castle D1's baked vertex AO, the toon ramps, SSAO on High).
// Faint lines on the castle's major silhouettes only were tried and read no
// better than none (9.2 has the stills that decided it), so they are gone.
//
// Anti-aliasing (character look pass, 4.2): with the passes on, the canvas
// does not multisample (the scene renders into the first pass's target,
// scene.ts). At Medium and High the scene's target multisamples instead
// (`samples`, 4x); when a pass reads the depth (SSAO, High) the
// target gets a depth texture, resolved once a frame (one sample a pixel,
// what the passes were tuned on); otherwise a plain depth renderbuffer (no
// depth resolve). Without the scene MSAA (a build or test that asks for
// none), the final pass can run FXAA's light variant on the scene colour
// (`fxaa`: four diagonal taps, four more only on edges).
//
// The frame alpha: while a pass reads it (`frameAlpha`: SSAO) opaque toon materials write their class into it (material.ts
// `setToonOutlineMask`): characters below 0.5, the world from 0.5, glass,
// glow and the sky panels 0 (`decodeOutlineAlpha`).
//
// Bloom: the bright parts (candles, windows, spell light, sunlit highlights)
// are cut out at a quarter of the resolution, blurred once in 2D (nine
// bilinear taps, about the reach of a separable 9-tap Gaussian each way) and
// added back in the final pass, softly. One blur pass instead of a
// horizontal and a vertical one: on a tile-based GPU every pass costs a
// render-target load and store, and the measured frame time came first.
// Passes:
//
//   bloom only:      scene → bright (1/4) → blur (1/4) → final
//
// A pass's size is the size of its input, and it draws into the next pass's
// input (Babylon's chain): bright is full size (the scene), the blurs and
// the final pass a quarter (the final pass still draws the whole canvas).
// Low quality uses none of it: the scene draws straight to the canvas with
// its multisampling. No per-frame allocation: uniforms are plain numbers,
// set from fields.
//
// Screen-space AO (castle D1, docs/art/castle-kit.md; High only): the
// contact shading the castle's baked vertex shade cannot know (characters
// against walls and floors, props on floors, occluders between cells). It
// reads the scene target's depth texture (no depth pre-pass, no geometry
// buffer, no extra draw of any mesh):
//
// - AO at half resolution: the view position from the depth, the normal
//   from four depth taps (the nearer side of each pair, so silhouettes do
//   not bend it), 8 samples on a spiral rotated by a 4 x 4 interleaved
//   pattern, the world radius `post.ssao.radius` projected (at most
//   `SSAO_MAX_PX` half-resolution pixels), each sample weighted by
//   (r² − d²)³ so nothing beyond the radius darkens (no halos round a
//   figure in front of a far wall);
// - one depth-aware blur at half resolution: a 4 x 4 box in four bilinear
//   taps (each weighted by its depth against the centre's), which holds
//   every rotation of the 4 x 4 pattern once, so the pattern cancels;
// - the composite in the final pass: colour · mix(1, tint, (1 − ao) ·
//   strength · fade(z)), by the frame alpha's class: the world full
//   strength, characters `characters` of it, glass, glow and the sky
//   panels and empty pixels none. Tint and numbers are art data
//   (toon_palette.json `post.ssao`, palette.ts TOON_SSAO): a mauve, never grey.
//
// Positions are reconstructed on full-size texel centres (a half-texel
// mismatch between a tap's position and the depth it read shows as stripes
// on floors at a grazing angle). Both AO targets are single-channel (R8): with the bloom on, the bright
// pass's input holds the blurred AO and the bright pass reads the scene
// through `sceneSampler` as the final pass does:
//
//   with SSAO:       scene → ssao (1/2) → ssaoBlur (1/2) → bright (1/4) → blur (1/4) → final
//
// (The arrows name each pass and the size it draws: the ssao pass draws the
// raw AO into the blur's input, the blur draws into the bright pass's input.)

import type { Camera } from "@babylonjs/core/Cameras/camera";
import type { Effect } from "@babylonjs/core/Materials/effect";
import { ShaderStore } from "@babylonjs/core/Engines/shaderStore";
import { PostProcess } from "@babylonjs/core/PostProcesses/postProcess";
import { Texture } from "@babylonjs/core/Materials/Textures/texture";
import type { RenderTargetWrapper } from "@babylonjs/core/Engines/renderTargetWrapper";
import type { Scene } from "@babylonjs/core/scene";
import { setToonOutlineMask, toonOutlineMask } from "./material";
import { TOON_SSAO, type SsaoLook } from "./palette";

/** What the passes do; quality levels choose (settings.ts). */
export interface ToonPostOptions {
  bloom: boolean;
  /** FXAA in the final pass (when neither the canvas nor the scene target multisamples). */
  fxaa?: boolean;
  /** Screen-space AO (High only, settings.ts). */
  ssao?: boolean;
  /**
   * MSAA samples on the scene's render target (the first pass's input)
   * (settings.ts `sceneSamples`; 1 or absent: none).
   */
  samples?: number;
}

/** SSAO: samples per pixel, the projected radius's cap (half-resolution pixels) and the darkening per unit of occlusion. */
export const SSAO_SAMPLES = 8;
export const SSAO_MAX_PX = 40;
export const SSAO_INTENSITY = 2.5;

/** Bloom tuning (art direction). */
export const TOON_POST = {
  /** Bloom: brightness (max channel) where it starts and is full, and how much is added back. */
  bloomThreshold: 0.95,
  bloomKnee: 0.04,
  bloomStrength: 0.35,
};

/** The passes `opts` needs, in chain order (pure; for tests and the F3 overlay). */
export function postPasses(opts: ToonPostOptions): string[] {
  if (!opts.bloom && !ssaoOn(opts)) return [];
  // FXAA, when asked for, runs inside the final pass; the SSAO's composite too.
  const ao = ssaoOn(opts) ? ["ssao", "ssaoBlur"] : [];
  return opts.bloom ? [...ao, "bright", "blur", "final"] : [...ao, "final"];
}

/** The scene target's MSAA samples (1: none). */
export function sceneSamples(opts: ToonPostOptions): number {
  return Math.max(1, Math.floor(opts.samples ?? 1));
}

/** Whether the chain runs the SSAO passes. */
export function ssaoOn(opts: ToonPostOptions): boolean {
  return !!opts.ssao;
}

/**
 * Whether a pass reads the scene target's depth and frame alpha (SSAO): the
 * target then gets a depth texture (resolved with MSAA) and the toon
 * materials write their class into the alpha.
 */
export function frameAlpha(opts: ToonPostOptions): boolean {
  return ssaoOn(opts);
}

/**
 * The SSAO composite's multiplier for one pixel (mirrors FINAL; for tests):
 * `ao` the blurred occlusion (1 open), `alpha` the frame alpha
 * (decodeOutlineAlpha), `z` the distance (m).
 */
export function ssaoMultiplier(ao: number, alpha: number, z: number, look: SsaoLook = TOON_SSAO): [number, number, number] {
  const cls = alpha > 0.52 ? 1 : alpha > 0.02 && alpha < 0.49 ? look.characters : 0;
  const t = Math.min(1, Math.max(0, (z - look.fadeEnd * 0.5) / Math.max(1e-6, look.fadeEnd * 0.5)));
  const fade = 1 - t * t * (3 - 2 * t);
  const k = (1 - Math.min(1, Math.max(0, ao))) * look.strength * cls * fade;
  return [1 + (look.tint[0] - 1) * k, 1 + (look.tint[1] - 1) * k, 1 + (look.tint[2] - 1) * k];
}

/**
 * The class and weight a frame alpha carries (mirrors the shaders): 0 is
 * empty (sky, glass, glow), below 0.5 a character (weight = 2a), from 0.5
 * the world (weight = 2a - 1).
 */
export function decodeOutlineAlpha(a: number): { character: boolean; weight: number } {
  if (a <= 0.02) return { character: false, weight: 0 };
  return a < 0.49 ? { character: true, weight: Math.min(1, a * 2) } : { character: false, weight: Math.max(0, a * 2 - 1) };
}

const FINAL = `
precision highp float;
varying vec2 vUV;
uniform sampler2D textureSampler;
#if defined(BLOOM) || defined(SSAO)
uniform sampler2D sceneSampler;
#define SCENE sceneSampler
#else
#define SCENE textureSampler
#endif
#ifdef BLOOM
uniform float bloomStrength;
#endif
#ifdef SSAO
// The blurred AO: the bright pass's input with the bloom, else this pass's own.
#ifdef BLOOM
uniform sampler2D aoSampler;
#define AOS aoSampler
#else
#define AOS textureSampler
#endif
uniform vec3 ssaoTint;
uniform vec4 ssaoLook;
#endif
#ifdef FXAA
uniform vec2 texel;
float lumaOf(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }
// FXAA's light variant (four diagonal bilinear taps find the edge and its
// direction; two or four taps along it blend).
vec3 fxaa(vec3 rgbM) {
  vec3 rgbNW = texture2D(SCENE, vUV + vec2(-0.5, -0.5) * texel).rgb;
  vec3 rgbNE = texture2D(SCENE, vUV + vec2(0.5, -0.5) * texel).rgb;
  vec3 rgbSW = texture2D(SCENE, vUV + vec2(-0.5, 0.5) * texel).rgb;
  vec3 rgbSE = texture2D(SCENE, vUV + vec2(0.5, 0.5) * texel).rgb;
  float lNW = lumaOf(rgbNW);
  float lNE = lumaOf(rgbNE);
  float lSW = lumaOf(rgbSW);
  float lSE = lumaOf(rgbSE);
  float lM = lumaOf(rgbM);
  float lMin = min(lM, min(min(lNW, lNE), min(lSW, lSE)));
  float lMax = max(lM, max(max(lNW, lNE), max(lSW, lSE)));
  if (lMax - lMin < max(0.04, lMax * 0.12)) return rgbM;
  vec2 dir = vec2(-((lNW + lNE) - (lSW + lSE)), (lNW + lSW) - (lNE + lSE));
  float reduce = max((lNW + lNE + lSW + lSE) * 0.03125, 0.0078125);
  float rcpMin = 1.0 / (min(abs(dir.x), abs(dir.y)) + reduce);
  dir = clamp(dir * rcpMin, vec2(-4.0), vec2(4.0)) * texel;
  vec3 rgbA = 0.5 * (texture2D(SCENE, vUV + dir * (1.0 / 3.0 - 0.5)).rgb + texture2D(SCENE, vUV + dir * (2.0 / 3.0 - 0.5)).rgb);
  vec3 rgbB = rgbA * 0.5 + 0.25 * (texture2D(SCENE, vUV - dir * 0.5).rgb + texture2D(SCENE, vUV + dir * 0.5).rgb);
  float lB = lumaOf(rgbB);
  return (lB < lMin || lB > lMax) ? rgbA : rgbB;
}
#endif
#ifdef SSAO
uniform sampler2D depthSampler;
uniform vec4 depthParams;
float linearZ(float d) {
  return depthParams.x * depthParams.y / (depthParams.y - d * (depthParams.y - depthParams.x));
}
#endif
void main(void) {
  vec4 scene = texture2D(SCENE, vUV);
#ifdef FXAA
  vec3 col = fxaa(scene.rgb);
#else
  vec3 col = scene.rgb;
#endif
#ifdef SSAO
  float dc = texture2D(depthSampler, vUV).r;
  if (scene.a > 0.02 && dc < 0.99999) {
    float zc = linearZ(dc);
    // The AO, tinted (ssaoLook: strength, fade start, fade end, the characters' share), by the alpha's class.
    float aoCls = scene.a > 0.52 ? 1.0 : (scene.a < 0.49 ? ssaoLook.w : 0.0);
    float aoK = (1.0 - texture2D(AOS, vUV).r) * ssaoLook.x * aoCls * (1.0 - smoothstep(ssaoLook.y, ssaoLook.z, zc));
    col *= mix(vec3(1.0), ssaoTint, aoK);
  }
#endif
#ifdef BLOOM
  col += texture2D(textureSampler, vUV).rgb * bloomStrength;
#endif
  gl_FragColor = vec4(col, 1.0);
}
`;

const BRIGHT = `
precision highp float;
varying vec2 vUV;
#ifdef SSAO
// The input holds the blurred AO: the scene comes from the first pass's input.
uniform sampler2D sceneSampler;
#define SRC sceneSampler
#else
uniform sampler2D textureSampler;
#define SRC textureSampler
#endif
uniform vec2 texel;
uniform vec2 bloomCut;
vec3 cut(vec3 c) {
  float m = max(c.r, max(c.g, c.b));
  float k = smoothstep(bloomCut.x, bloomCut.x + bloomCut.y, m);
  return c * k;
}
void main(void) {
  // Four bilinear taps cover a 4 x 4 block of the full-size frame.
  vec3 c = cut(texture2D(SRC, vUV + vec2(-texel.x, -texel.y)).rgb);
  c += cut(texture2D(SRC, vUV + vec2(texel.x, -texel.y)).rgb);
  c += cut(texture2D(SRC, vUV + vec2(-texel.x, texel.y)).rgb);
  c += cut(texture2D(SRC, vUV + vec2(texel.x, texel.y)).rgb);
  gl_FragColor = vec4(c * 0.25, 1.0);
}
`;

const BLUR = `
precision highp float;
varying vec2 vUV;
uniform sampler2D textureSampler;
uniform vec2 texel;
void main(void) {
  // A 2D blur in nine bilinear fetches: a 3 x 3 grid 1.5 texels apart, each
  // fetch averaging 2 x 2 texels, so it covers 6 x 6 quarter-size texels
  // with a soft (tent-like) falloff from the weights.
  vec2 o = texel * 1.5;
  vec3 c = texture2D(textureSampler, vUV).rgb * 0.25;
  c += (texture2D(textureSampler, vUV + vec2(o.x, 0.0)).rgb + texture2D(textureSampler, vUV - vec2(o.x, 0.0)).rgb
      + texture2D(textureSampler, vUV + vec2(0.0, o.y)).rgb + texture2D(textureSampler, vUV - vec2(0.0, o.y)).rgb) * 0.125;
  c += (texture2D(textureSampler, vUV + o).rgb + texture2D(textureSampler, vUV - o).rgb
      + texture2D(textureSampler, vUV + vec2(o.x, -o.y)).rgb + texture2D(textureSampler, vUV + vec2(-o.x, o.y)).rgb) * 0.0625;
  gl_FragColor = vec4(c, 1.0);
}
`;

const SSAO = `
precision highp float;
varying vec2 vUV;
uniform sampler2D depthSampler;
uniform vec4 depthParams;
// tan(fov / 2) across and up, the full-size texel (uv).
uniform vec4 ssaoProj;
// radius (m), half-res pixels per metre at 1 m, the cap (half-res px), intensity.
uniform vec4 ssaoParams;
// the half-res texel (uv).
uniform vec2 ssaoTexel;
float linearZ(float d) {
  return depthParams.x * depthParams.y / (depthParams.y - d * (depthParams.y - depthParams.x));
}
// On a full-size texel centre, so the position matches the depth it read (a
// half-texel mismatch shows as stripes on floors seen at a grazing angle).
vec2 snapUV(vec2 uv) {
  return (floor(uv / ssaoProj.zw) + 0.5) * ssaoProj.zw;
}
vec3 viewPos(vec2 uv) {
  float z = linearZ(texture2D(depthSampler, uv).r);
  return vec3((uv * 2.0 - 1.0) * ssaoProj.xy * z, z);
}
float bayer2(vec2 a) {
  a = floor(a);
  return fract(a.x / 2.0 + a.y * a.y * 0.75);
}
void main(void) {
  vec2 uv = snapUV(vUV);
  float d = texture2D(depthSampler, uv).r;
  if (d >= 0.99999) {
    gl_FragColor = vec4(1.0);
    return;
  }
  vec3 P = viewPos(uv);
  // The normal from four depth taps: per axis the nearer neighbour (silhouettes keep their own plane).
  vec3 pr = viewPos(uv + vec2(ssaoProj.z, 0.0)) - P;
  vec3 pl = P - viewPos(uv - vec2(ssaoProj.z, 0.0));
  vec3 pu = viewPos(uv + vec2(0.0, ssaoProj.w)) - P;
  vec3 pd = P - viewPos(uv - vec2(0.0, ssaoProj.w));
  vec3 dx = abs(pr.z) < abs(pl.z) ? pr : pl;
  vec3 dy = abs(pu.z) < abs(pd.z) ? pu : pd;
  vec3 N = normalize(cross(dx, dy));
  if (dot(N, P) > 0.0) N = -N;
  float r = ssaoParams.x;
  float r2 = r * r;
  float rPx = min(ssaoParams.y * r / P.z, ssaoParams.z);
  if (rPx < 1.0) {
    gl_FragColor = vec4(1.0);
    return;
  }
  // A 4 x 4 interleaved rotation (the blur averages it away).
  float rot = (bayer2(0.5 * gl_FragCoord.xy) * 0.25 + bayer2(gl_FragCoord.xy)) * 6.2831853;
  float bias = 0.01 + 0.002 * P.z;
  float sum = 0.0;
  for (int i = 0; i < ${SSAO_SAMPLES}; i++) {
    float a = (float(i) + 0.5) / ${SSAO_SAMPLES}.0;
    float ang = a * 18.849556 + rot;
    vec3 v = viewPos(snapUV(uv + vec2(cos(ang), sin(ang)) * (a * rPx) * ssaoTexel)) - P;
    float vv = dot(v, v);
    float f = max(r2 - vv, 0.0);
    sum += f * f * f * max((dot(v, N) - bias) / (vv + 0.01), 0.0);
  }
  // (r² − d²)³ / r⁶ keeps the weight in 0..1; the r makes the sum independent of the radius.
  float ao = clamp(1.0 - sum * r * ssaoParams.w * 5.0 / (r2 * r2 * r2 * ${SSAO_SAMPLES}.0), 0.0, 1.0);
  gl_FragColor = vec4(ao, ao, ao, 1.0);
}
`;

const SSAO_BLUR = `
precision highp float;
varying vec2 vUV;
uniform sampler2D textureSampler;
uniform sampler2D depthSampler;
uniform vec4 depthParams;
uniform vec2 texel;
float linearZ(float d) {
  return depthParams.x * depthParams.y / (depthParams.y - d * (depthParams.y - depthParams.x));
}
float tap(vec2 uv, float zc, float w, inout float wsum) {
  // Taps across a depth step (another surface) count less.
  float wz = w / (1.0 + 24.0 * abs(linearZ(texture2D(depthSampler, uv).r) - zc) / zc);
  wsum += wz;
  return texture2D(textureSampler, uv).r * wz;
}
void main(void) {
  // A 4 x 4 box in four bilinear taps (each the mean of 2 x 2 texels): it
  // holds every rotation of the AO's 4 x 4 interleaved pattern once, so
  // the pattern cancels (taps on texel corners: the block is half a texel
  // off centre); each tap weighted by its depth against the centre's.
  float zc = linearZ(texture2D(depthSampler, vUV).r);
  float wsum = 0.0;
  float c = tap(vUV + vec2(-1.5, -1.5) * texel, zc, 1.0, wsum);
  c += tap(vUV + vec2(0.5, -1.5) * texel, zc, 1.0, wsum);
  c += tap(vUV + vec2(-1.5, 0.5) * texel, zc, 1.0, wsum);
  c += tap(vUV + vec2(0.5, 0.5) * texel, zc, 1.0, wsum);
  float ao = c / max(wsum, 1e-4);
  gl_FragColor = vec4(ao, ao, ao, 1.0);
}
`;

ShaderStore.ShadersStore["toonSsaoFragmentShader"] = SSAO;
ShaderStore.ShadersStore["toonSsaoBlurFragmentShader"] = SSAO_BLUR;
ShaderStore.ShadersStore["toonFinalFragmentShader"] = FINAL;
ShaderStore.ShadersStore["toonBrightFragmentShader"] = BRIGHT;
ShaderStore.ShadersStore["toonBlurFragmentShader"] = BLUR;

/** Depth texture format: 32-bit float. */
const DEPTH32F = 14;
/** Babylon's texture formats: one channel (R8 with unsigned bytes) and RGBA. */
const TEXTUREFORMAT_R = 6;
const TEXTUREFORMAT_RGBA = 5;

export class ToonPost {
  private passes: PostProcess[] = [];
  private opts: ToonPostOptions = { bloom: false, fxaa: false, ssao: false, samples: 1 };
  private first: PostProcess | null = null;
  private bright: PostProcess | null = null;
  private ssaoBlur: PostProcess | null = null;
  /**
   * The SSAO's look (palette data, `TOON_SSAO`); a copy, so a tuning or
   * measurement session can change it live (read when the final pass binds).
   */
  readonly ssaoLook: SsaoLook = { ...TOON_SSAO, tint: [...TOON_SSAO.tint] };

  constructor(
    private readonly scene: Scene,
    private readonly camera: Camera,
  ) {}

  get options(): ToonPostOptions {
    return this.opts;
  }

  /** Rebuilds the chain for `opts` (a quality change; never per frame). */
  configure(opts: ToonPostOptions): void {
    if (
      opts.bloom === this.opts.bloom &&
      !!opts.fxaa === !!this.opts.fxaa &&
      ssaoOn(opts) === ssaoOn(this.opts) &&
      sceneSamples(opts) === sceneSamples(this.opts)
    )
      return;
    this.opts = { bloom: opts.bloom, fxaa: !!opts.fxaa, ssao: ssaoOn(opts), samples: sceneSamples(opts) };
    const ssao = this.opts.ssao === true;
    const samples = this.opts.samples ?? 1;
    const depth = frameAlpha(this.opts);
    this.disposePasses();
    setToonOutlineMask(depth ? this.scene : null);
    const plan = postPasses(this.opts);
    if (plan.length === 0) return;
    const engine = this.scene.getEngine();
    const cam = this.camera;
    const bilinear = Texture.BILINEAR_SAMPLINGMODE;
    // A pass's size is the size of its *input*; each pass draws into the
    // next one's input. So: bright reads the full-size scene and draws a
    // quarter-size image, the blur works at a quarter, and the final pass's
    // input is the quarter-size bloom while it draws the full canvas.
    // With SSAO the AO pass comes first (its input is the scene) and draws
    // half size; the blur and whatever follows it hold the AO in R8.
    if (ssao) {
      // Bilinear: its input is the scene, which the bright pass and FXAA sample between texels.
      const ao = new PostProcess("toonSsao", "toonSsao", ["depthParams", "ssaoProj", "ssaoParams", "ssaoTexel"], ["depthSampler"], 1, cam, bilinear, engine, false);
      ao.onApply = (e: Effect) => this.applySsao(e);
      const aoBlur = new PostProcess("toonSsaoBlur", "toonSsaoBlur", ["depthParams", "texel"], ["depthSampler"], 0.5, cam, bilinear, engine, false, null, 0, undefined, undefined, false, TEXTUREFORMAT_R);
      aoBlur.onApply = (e: Effect) => {
        e._bindTexture("depthSampler", this.first!.inputTexture.depthStencilTexture);
        e.setFloat4("depthParams", this.camera.minZ, this.camera.maxZ, 0, 0);
        e.setFloat2("texel", 1 / Math.max(1, aoBlur.width), 1 / Math.max(1, aoBlur.height));
      };
      this.ssaoBlur = aoBlur;
      this.passes.push(ao, aoBlur);
    }
    if (this.opts.bloom) {
      const bright = ssao
        ? new PostProcess("toonBright", "toonBright", ["texel", "bloomCut"], ["sceneSampler"], 0.5, cam, bilinear, engine, false, "#define SSAO", 0, undefined, undefined, false, TEXTUREFORMAT_R)
        : new PostProcess("toonBright", "toonBright", ["texel", "bloomCut"], null, 1, cam, bilinear, engine, false);
      bright.onApply = (e: Effect) => {
        if (ssao) e.setTextureFromPostProcess("sceneSampler", this.first);
        // Half a full-size texel diagonally: each bilinear tap averages a 2 x 2 block.
        e.setFloat2("texel", 1 / Math.max(1, engine.getRenderWidth()), 1 / Math.max(1, engine.getRenderHeight()));
        e.setFloat2("bloomCut", TOON_POST.bloomThreshold, TOON_POST.bloomKnee);
      };
      const blur = new PostProcess("toonBlur", "toonBlur", ["texel"], null, 0.25, cam, bilinear, engine, false);
      blur.onApply = (e: Effect) => e.setFloat2("texel", 1 / Math.max(1, blur.width), 1 / Math.max(1, blur.height));
      this.bright = bright;
      this.passes.push(bright, blur);
    }

    const defines = [
      this.opts.bloom ? "#define BLOOM" : "",
      this.opts.fxaa ? "#define FXAA" : "",
      ssao ? "#define SSAO" : "",
    ].join("\n");
    const final = new PostProcess(
      "toonFinal",
      "toonFinal",
      ["texel", "depthParams", "bloomStrength", "ssaoTint", "ssaoLook"],
      ["sceneSampler", "depthSampler", "aoSampler"],
      this.opts.bloom ? 0.25 : ssao ? 0.5 : 1,
      cam,
      // Bilinear for the bloom, the AO and FXAA's in-between taps (centre taps land on texel centres).
      this.opts.bloom || this.opts.fxaa || ssao ? bilinear : Texture.NEAREST_SAMPLINGMODE,
      engine,
      false,
      defines,
      0,
      undefined,
      undefined,
      false,
      // Without the bloom the final pass's input is the blurred AO.
      ssao && !this.opts.bloom ? TEXTUREFORMAT_R : TEXTUREFORMAT_RGBA,
    );
    final.onApply = (e: Effect) => this.applyFinal(e);
    this.passes.push(final);
    const first = this.passes[0]!;
    this.first = first;
    // MSAA on the scene's target (before its first resize creates it). When a
    // pass reads the depth, the target gets a depth texture and the
    // multisampled depth resolves into it once a frame (resolveMSAADepth);
    // otherwise the target keeps its plain depth renderbuffer.
    first.samples = samples;
    if (depth) {
      first.onSizeChangedObservable.add((pp) => {
        ensureDepthTexture(pp.inputTexture);
        if (pp.inputTexture) pp.inputTexture.resolveMSAADepth = pp.samples > 1;
      });
    }
  }

  /** The scene target's MSAA samples now (1: none; the GPU may cap the request). */
  get samples(): number {
    return this.first?.samples ?? 1;
  }

  private applyFinal(e: Effect): void {
    const first = this.first!;
    const input = first.inputTexture;
    if (this.opts.bloom || this.opts.ssao) e.setTextureFromPostProcess("sceneSampler", first);
    if (this.opts.ssao && this.opts.bloom) e.setTextureFromPostProcess("aoSampler", this.bright);
    const w = input.width;
    const h = input.height;
    if (this.opts.fxaa) e.setFloat2("texel", 1 / Math.max(1, w), 1 / Math.max(1, h));
    if (this.opts.ssao) {
      e._bindTexture("depthSampler", input.depthStencilTexture);
      e.setFloat4("depthParams", this.camera.minZ, this.camera.maxZ, 0, 0);
    }
    if (this.opts.bloom) e.setFloat("bloomStrength", TOON_POST.bloomStrength);
    if (this.opts.ssao) {
      const look = this.ssaoLook;
      e.setFloat3("ssaoTint", look.tint[0], look.tint[1], look.tint[2]);
      e.setFloat4("ssaoLook", look.strength, look.fadeEnd * 0.5, look.fadeEnd, look.characters);
    }
  }

  /** The AO pass's uniforms: the depth, the camera's projection and the half-size target it draws. */
  private applySsao(e: Effect): void {
    const input = this.first!.inputTexture;
    e._bindTexture("depthSampler", input.depthStencilTexture);
    e.setFloat4("depthParams", this.camera.minZ, this.camera.maxZ, 0, 0);
    const w = Math.max(1, input.width);
    const h = Math.max(1, input.height);
    const fov = (this.camera as unknown as { fov?: number }).fov ?? 0.9;
    const tanY = Math.tan(fov / 2);
    e.setFloat4("ssaoProj", tanY * (w / h), tanY, 1 / w, 1 / h);
    // The AO draws into the blur's input (half size).
    const blur = this.ssaoBlur!;
    const hw = Math.max(1, blur.width);
    const hh = Math.max(1, blur.height);
    e.setFloat4("ssaoParams", this.ssaoLook.radius, hh / (2 * tanY), SSAO_MAX_PX, SSAO_INTENSITY);
    e.setFloat2("ssaoTexel", 1 / hw, 1 / hh);
  }

  /** The render targets this chain holds (bytes, estimated: RGBA8 colour, 32-bit depth). */
  memoryBytes(): number {
    let bytes = 0;
    for (const p of this.passes) {
      const t = p.inputTexture;
      if (!t) continue;
      // The AO targets are one byte a pixel (R8).
      const px = t.width * t.height;
      const colour = t.texture?.format === TEXTUREFORMAT_R ? 1 : 4;
      bytes += px * colour;
      // The first pass's depth: a texture (resolved) or a renderbuffer.
      if (t.depthStencilTexture || p === this.first) bytes += px * 4;
      // MSAA: the multisampled colour and depth renderbuffers besides the resolved pair.
      if (t.samples > 1) bytes += px * t.samples * (colour + 4);
    }
    return bytes;
  }

  private disposePasses(): void {
    for (const p of this.passes) p.dispose(this.camera);
    this.passes = [];
    this.first = null;
    this.bright = null;
    this.ssaoBlur = null;
  }

  dispose(): void {
    this.disposePasses();
    if (toonOutlineMask(this.scene)) setToonOutlineMask(null);
  }
}

function ensureDepthTexture(rt: RenderTargetWrapper | undefined): void {
  if (!rt || rt.depthStencilTexture) return;
  rt.createDepthStencilTexture(0, false, false, 1, DEPTH32F, "toon-depth");
}
