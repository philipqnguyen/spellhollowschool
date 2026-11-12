// How NPCs are drawn apart from their looks (characters/renderer.ts
// `CharacterStyle`), every colour from palette data
// (data/art/toon_palette.json `npcs`, data/art/castle_textures.json):
//
// - the house ghosts: one translucent tinted copy of the character material
//   with a fresnel rim (`npcs.ghost`), floating `ghost_float_m`, no shadow,
//   no outline, drawn after the opaque pass (a depth pre-pass keeps their own
//   limbs from showing through);
// - background students: the usual material, the coarsest tier beyond 10 m;
// - a talking portrait's subject: clipped to its canvas (four clip planes,
//   so the half-length figure ends at the frame), varnish-tinted
//   (`npcs.portrait.tint`), no shadow, no outline.

import { Color3 } from "@babylonjs/core/Maths/math.color";
import { Plane } from "@babylonjs/core/Maths/math.plane";
import { FresnelParameters } from "@babylonjs/core/Materials/fresnelParameters";
import type { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import toonPaletteJson from "../../../../data/art/toon_palette.json";
import type { CharacterStyle } from "../characters/renderer";

type Rgb = readonly [number, number, number];

export interface GhostLook {
  tint: Rgb;
  desaturate: number;
  alpha: number;
  rim: Rgb;
  rimPower: number;
  rimStrength: number;
}

export interface PortraitLook {
  saturation: number;
  valueSteps: number;
  tint: Rgb;
  grain: number;
  sheen: number;
}

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const num = (v: unknown, d: number, lo = 0, hi = Infinity): number => (typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : d);
const rgb = (v: unknown, d: Rgb): Rgb => (Array.isArray(v) && v.length === 3 && v.every((c) => typeof c === "number" && c >= 0 && c <= 1) ? [v[0], v[1], v[2]] : d);

const WHITE: Rgb = [1, 1, 1];

/** `npcs.ghost` of the toon palette (each field falls back to a plain white, half-seen look). */
export function ghostLook(palette: unknown = toonPaletteJson): GhostLook {
  const g = isObj(palette) && isObj(palette.npcs) && isObj(palette.npcs.ghost) ? palette.npcs.ghost : {};
  return {
    tint: rgb(g.tint, WHITE),
    desaturate: num(g.desaturate, 0, 0, 1),
    alpha: num(g.alpha, 0.5, 0.05, 1),
    rim: rgb(g.rim, WHITE),
    rimPower: num(g.rimPower, 2, 0.1, 16),
    rimStrength: num(g.rimStrength, 0.5, 0, 4),
  };
}

/** `npcs.portrait` of the toon palette. */
export function portraitLook(palette: unknown = toonPaletteJson): PortraitLook {
  const p = isObj(palette) && isObj(palette.npcs) && isObj(palette.npcs.portrait) ? palette.npcs.portrait : {};
  return {
    saturation: num(p.saturation, 1, 0, 2),
    valueSteps: num(p.valueSteps, 0, 0, 16),
    tint: rgb(p.tint, WHITE),
    grain: num(p.grain, 0, 0, 1),
    sheen: num(p.sheen, 0, 0, 1),
  };
}

/**
 * The tint multiplied over a ghost's albedo, with `desaturate` folded in the
 * one way a plain multiply can: a desaturated look is lighter and closer to
 * the tint, so the tint is pulled toward white by `desaturate` and the rest
 * of the colour comes from the emissive tint below.
 */
export function ghostDiffuse(g: GhostLook): Rgb {
  const k = 1 - g.desaturate * 0.5;
  return [g.tint[0] * k + (1 - k) * 0.85, g.tint[1] * k + (1 - k) * 0.85, g.tint[2] * k + (1 - k) * 0.85];
}

/** The ghosts' style (one shared material copy per character atlas). */
export function ghostStyle(floatM: number, g: GhostLook = ghostLook()): CharacterStyle {
  const diffuse = ghostDiffuse(g);
  return {
    key: "npc-ghost",
    outline: false,
    shadow: false,
    lift: floatM,
    coarseBeyond: Infinity,
    tweak: (m: StandardMaterial) => {
      m.diffuseColor = new Color3(diffuse[0], diffuse[1], diffuse[2]);
      // The colour a desaturated body loses comes back as the tint's own glow.
      m.emissiveColor = new Color3(g.tint[0] * g.desaturate * 0.35, g.tint[1] * g.desaturate * 0.35, g.tint[2] * g.desaturate * 0.35);
      m.alpha = g.alpha;
      m.needDepthPrePass = true;
      const rim = new Color3(g.rim[0] * g.rimStrength, g.rim[1] * g.rimStrength, g.rim[2] * g.rimStrength);
      m.emissiveFresnelParameters = new FresnelParameters({ leftColor: rim, rightColor: Color3.Black(), power: g.rimPower, bias: 0 });
      // Seen from inside (walking through one) the back faces show the body's far side.
      m.backFaceCulling = true;
    },
  };
}

/** Background students: as everyone, but at the coarsest tier beyond `STUDENT_COARSE_M`. */
export const STUDENT_COARSE_M = 10;
export const STUDENT_STYLE: CharacterStyle = { key: "npc-student", tweak: null, outline: true, shadow: true, lift: 0, coarseBeyond: STUDENT_COARSE_M };

/** A canvas on its wall: its centre, the way it faces (yaw 0 faces +z) and its size (m). */
export interface CanvasPlace {
  pos: readonly number[];
  yaw: number;
  size: readonly [number, number] | readonly number[];
}

/** The four clip planes of a canvas (Babylon clips where `ax + by + cz + d > 0`): above, below, left and right of it. */
export function canvasPlanes(c: CanvasPlace, margin = 0): [Plane, Plane, Plane, Plane] {
  const [x, y, z] = c.pos as [number, number, number];
  const hw = c.size[0]! / 2 + margin;
  const hh = c.size[1]! / 2 + margin;
  // Along the wall (the canvas's right, seen from the room): forward (sin yaw, cos yaw) turned a quarter.
  const tx = Math.cos(c.yaw);
  const tz = -Math.sin(c.yaw);
  const along = x * tx + z * tz;
  return [new Plane(0, 1, 0, -(y + hh)), new Plane(0, -1, 0, y - hh), new Plane(tx, 0, tz, -(along + hw)), new Plane(-tx, 0, -tz, along - hw)];
}

/** A portrait subject's style: clipped to its canvas, varnished. One material copy per portrait (`key`). */
export function portraitStyle(npc: string, c: CanvasPlace, look: PortraitLook = portraitLook(), openZ = Infinity): CharacterStyle {
  const planes = canvasPlanes(c);
  // In front of the box's opening (`openZ` off the wall): a hand or an elbow never leaves the frame.
  const fx = Math.sin(c.yaw);
  const fz = Math.cos(c.yaw);
  const front = Number.isFinite(openZ) ? new Plane(fx, 0, fz, -(fx * c.pos[0]! + fz * c.pos[2]! + openZ)) : null;
  return {
    key: `npc-portrait-${npc}`,
    outline: false,
    shadow: false,
    lift: 0,
    coarseBeyond: Infinity,
    tweak: (m: StandardMaterial) => {
      m.clipPlane = planes[0];
      m.clipPlane2 = planes[1];
      m.clipPlane3 = planes[2];
      m.clipPlane4 = planes[3];
      if (front) m.clipPlane5 = front;
      m.diffuseColor = new Color3(look.tint[0], look.tint[1], look.tint[2]);
    },
  };
}
