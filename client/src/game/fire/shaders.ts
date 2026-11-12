// Animated fire's shaders (docs/architecture.md "Rendering"). Everything
// moves on the GPU from one `time` uniform: no per-flame CPU work.
//
// - Flames: one camera-facing card per tongue (turned about the tongue's up
//   axis only, so a tongue stays upright seen from above). The fragment
//   shader draws a teardrop tongue whose tip sways and stretches on
//   scrolling value noise with a per-tongue phase, its edge licked by a
//   second noise, and two smaller teardrops inside it (the layered core):
//   edge (redder toward the tip), mid, a near-white core, in crisp toon
//   bands with a pixel of soft edge; now and then a drop breaks off the tip
//   and rises, shrinking. Candles (kind 1) are the same with a calmer sway
//   and no pieces. Low (FIRE_LOW): a sine sway, no noise, no pieces.
// - Floor glow: a flat disc on the floor, a soft radial falloff, added.
// - Embers: tiny camera-facing sparks rising and drifting on a loop.
//
// Brightness pulses with `fireFlicker` (flicker.ts) on the fire's seed, the
// same sum its light flickers with. Flames and embers blend over the
// scene; the floor glow adds light. None of them writes depth, so they
// never hide one another; the opaque world hides them.

import { FLICKER_GLSL } from "./flicker";
import { CARD_H, CARD_W } from "./data";

const f = (v: number) => (Number.isInteger(v) ? v.toFixed(1) : String(v));

/** Thin instances: the instance matrix times the mesh's world (a moving candle group's node). */
const INSTANCE = /* glsl */ `
attribute vec4 world0;
attribute vec4 world1;
attribute vec4 world2;
attribute vec4 world3;
uniform mat4 world;
`;

const NOISE = /* glsl */ `
float fireHash(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}
float fireNoise(vec2 p) {
  vec2 i = floor(p);
  vec2 u = fract(p);
  u = u * u * (3.0 - 2.0 * u);
  return mix(mix(fireHash(i), fireHash(i + vec2(1.0, 0.0)), u.x), mix(fireHash(i + vec2(0.0, 1.0)), fireHash(i + vec2(1.0, 1.0)), u.x), u.y);
}
`;

export const FLAME_VERTEX = /* glsl */ `
precision highp float;
attribute vec3 position;
attribute vec4 flame;
${INSTANCE}
uniform mat4 viewProjection;
uniform mat4 view;
uniform float time;
uniform float flickerAmount;
varying vec2 vUv;
varying vec4 vFlame;
varying float vPulse;
${FLICKER_GLSL}
void main(void) {
  mat4 w = world * mat4(world0, world1, world2, world3);
  vec3 foot = w[3].xyz;
  float width = length(w[0].xyz);
  float height = length(w[1].xyz);
  vec3 up = w[1].xyz / max(height, 1e-6);
  // The camera's right, flattened against the tongue's axis: the card turns about it.
  vec3 camRight = vec3(view[0][0], view[1][0], view[2][0]);
  vec3 right = camRight - up * dot(camRight, up);
  float rl = length(right);
  right = rl > 1e-4 ? right / rl : vec3(1.0, 0.0, 0.0);
  vec3 p = foot + right * (position.x * width) + up * (position.y * height);
  gl_Position = viewProjection * vec4(p, 1.0);
  vUv = position.xy;
  vFlame = flame;
  vPulse = fireFlicker(time, flame.y, flickerAmount);
}
`;

export const FLAME_FRAGMENT = /* glsl */ `
#extension GL_OES_standard_derivatives : enable
precision highp float;
varying vec2 vUv;
varying vec4 vFlame;
varying float vPulse;
uniform float time;
uniform vec3 fireCore;
uniform vec3 fireMid;
uniform vec3 fireEdge;
uniform vec3 fireTip;
${NOISE}
// A teardrop's half width at height y (0 foot .. 1 tip), 0.5 at the belly (y ~ 0.31).
float tear(float y) {
  float c = clamp(y, 0.0, 1.0);
  return 1.49 * pow(c + 0.001, 0.55) * pow(1.0 - c, 1.2);
}
// Coverage of a shape whose distance-like value is d (inside below 1), with a pixel of soft edge.
float cover(float d) {
  float e = clamp(fwidth(d) * 1.2, 0.015, 0.45);
  return 1.0 - smoothstep(1.0 - e, 1.0, d);
}
void main(void) {
  float seed = vFlame.x;
  float candle = vFlame.w;
  float calm = 1.0 - candle * 0.65;
  float t = time;
  // Card units: the tongue's belly half width 0.5, its foot y 0, its tip y 1.
  vec2 q = vec2(vUv.x * ${f(CARD_W)}, vUv.y * ${f(CARD_H)});
  // The tongue licks upward: it stretches and settles, and with the fire's pulse.
  float lick = 1.0 + (0.11 * sin(t * (3.1 + seed * 1.9) + seed * 40.0) + 0.05 * sin(t * 5.3 + seed * 17.0)) * calm + (vPulse - 1.0) * 1.5;
  float y = q.y / lick;
  float tipw = y * y;
  // Low, and every candle (hundreds in a hall, a few pixels each): two sines, no noise.
  float sway = (sin(y * 3.4 - t * 4.2 + seed * 6.283) * 0.16 + sin(y * 1.7 - t * 2.3 + seed * 3.1) * 0.12) * tipw;
  float hw = tear(y);
#ifndef FIRE_LOW
  if (candle < 0.5) {
    sway = (fireNoise(vec2(seed * 17.0, y * 1.5 - t * 1.6)) - 0.5) * 1.0 * tipw
         + sin(y * 5.5 - t * 5.2 + seed * 6.283) * 0.09 * y;
    // The edge licks: scrolling noise eats into the tongue, more toward the tip (wavy, never a straight spike).
    float n = fireNoise(vec2((q.x - sway) * 2.4 + seed * 9.0, y * 3.0 - t * 3.3));
    hw *= 1.0 + (n - 0.5) * 0.8 * smoothstep(0.12, 0.95, y);
  }
#endif
  sway *= calm;
  float x = q.x - sway;
  float d = (y < 0.0 || y > 1.0) ? 9.0 : abs(x) / max(hw, 1e-3);
  // The layered core: two smaller teardrops on the same foot, each with its own small drift.
  float drift = (fireNoise(vec2(seed * 5.0 + 3.0, t * 2.1)) - 0.5) * 0.12 * calm;
  float midH = mix(0.74, 0.8, candle);
  float ym = y / midH;
  float dm = (ym > 1.0) ? 9.0 : abs(x - drift * ym) / max(tear(ym) * mix(0.7, 0.76, candle), 1e-3);
  float coreH = mix(0.46, 0.56, candle) * (0.92 + 0.08 * vPulse);
  float yc = y / coreH;
  float dc = (yc > 1.0) ? 9.0 : abs(x - drift * 1.4 * yc) / max(tear(yc) * mix(0.42, 0.5, candle), 1e-3);
  float a = cover(d);
  vec3 col = mix(fireEdge, fireTip, smoothstep(0.42, 0.95, y) * calm);
  col = mix(col, fireMid, cover(dm));
  col = mix(col, fireCore, cover(dc));
#ifndef FIRE_LOW
  // Now and then a drop breaks off the tip and rises, shrinking (fire tongues only).
  if (candle < 0.5) {
    float cyc = t * (0.6 + seed * 0.35) + seed * 7.0;
    float k = floor(cyc);
    float p = fract(cyc);
    if (fireHash(vec2(k, seed * 31.0)) > 0.5) {
      float by = (0.82 + p * 0.5) * lick;
      float bx = (fireNoise(vec2(seed * 17.0, (0.82 + p * 0.5) * 1.5 - t * 1.6)) - 0.5) * 0.9 + (fireHash(vec2(seed, k)) - 0.5) * 0.15;
      float br = 0.085 * (1.0 - p) + 0.008;
      float ab = cover(length(vec2(q.x - bx, (q.y - by) * 0.6)) / br) * (1.0 - smoothstep(0.75, 1.0, p));
      col = mix(col, mix(fireTip, fireEdge, 1.0 - p), ab * (1.0 - a));
      a = max(a, ab);
    }
  }
#endif
  if (a < 0.01) discard;
  col *= vPulse * vFlame.z;
  gl_FragColor = vec4(clamp(col, 0.0, 1.0), a);
}
`;

export const POOL_VERTEX = /* glsl */ `
precision highp float;
attribute vec3 position;
attribute vec4 flame;
${INSTANCE}
uniform mat4 viewProjection;
uniform float time;
uniform float flickerAmount;
varying vec2 vUv;
varying float vPulse;
${FLICKER_GLSL}
void main(void) {
  mat4 w = world * mat4(world0, world1, world2, world3);
  vec3 p = (w * vec4(position.x, 0.0, position.y, 1.0)).xyz;
  gl_Position = viewProjection * vec4(p, 1.0);
  vUv = position.xy * 2.0;
  // The floor glow breathes a little more than the flames (it is all the fire's light on the floor).
  vPulse = fireFlicker(time, flame.y, flickerAmount * 1.6);
}
`;

export const POOL_FRAGMENT = /* glsl */ `
precision highp float;
varying vec2 vUv;
varying float vPulse;
uniform vec3 poolColor;
uniform float poolStrength;
void main(void) {
  float r2 = dot(vUv, vUv);
  if (r2 >= 1.0) discard;
  float k = 1.0 - r2;
  // Soft: brightest under the fire, fading to nothing at the rim.
  float a = k * k * (0.35 + 0.65 * k) * poolStrength * vPulse;
  gl_FragColor = vec4(poolColor, a);
}
`;

export const EMBER_VERTEX = /* glsl */ `
precision highp float;
attribute vec3 position;
attribute vec4 flame;
${INSTANCE}
uniform mat4 viewProjection;
uniform mat4 view;
uniform float time;
varying vec2 vUv;
varying float vLife;
void main(void) {
  mat4 w = world * mat4(world0, world1, world2, world3);
  vec3 base = w[3].xyz;
  float spread = length(w[0].xyz);
  float rise = length(w[1].xyz);
  float seed = flame.x;
  float life = 1.7 + seed * 1.3;
  float p = fract(time / life + seed * 13.7);
  float k = floor(time / life + seed * 13.7);
  float s2 = fract(sin(k * 12.9898 + seed * 78.233) * 43758.5453);
  // Up and out, curling as it climbs.
  vec3 c = base + vec3((s2 - 0.5) * 2.0 * spread + sin(seed * 91.0 + time * 1.7) * spread * p,
                       rise * p * (0.8 + 0.4 * s2),
                       cos(seed * 37.0 + time * 1.3) * spread * p * 0.6);
  float size = 0.035 * (1.0 - 0.65 * p) * (0.7 + 0.6 * s2);
  vec3 r = vec3(view[0][0], view[1][0], view[2][0]);
  vec3 u = vec3(view[0][1], view[1][1], view[2][1]);
  gl_Position = viewProjection * vec4(c + (r * position.x + u * position.y) * size, 1.0);
  vUv = position.xy * 2.0;
  vLife = p;
}
`;

export const EMBER_FRAGMENT = /* glsl */ `
precision highp float;
varying vec2 vUv;
varying float vLife;
uniform vec3 emberColor;
uniform vec3 fireCore;
void main(void) {
  float r2 = dot(vUv, vUv);
  if (r2 >= 1.0) discard;
  float a = (1.0 - r2) * smoothstep(0.0, 0.08, vLife) * (1.0 - smoothstep(0.55, 1.0, vLife));
  vec3 col = mix(fireCore, emberColor, smoothstep(0.0, 0.5, vLife));
  gl_FragColor = vec4(col, a);
}
`;
