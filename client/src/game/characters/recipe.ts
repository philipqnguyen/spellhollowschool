// What to draw for an appearance: which part files, in which order of
// preference, with which dyes and tints (docs/architecture.md, "Content data",
// asset keys). Pure, unit tested.
//
//   body       characters/body_a.glb               (creator body type `asset`)
//   hair       hair/<style>_<body>.glb, or hair/<style>_cap_<body>.glb under a hat
//              (creator.json v2: each style belongs to one body; a retired
//              or foreign style id draws the body's migrated equivalent)
//   gear       gear/<slot>/<item>_<body>.glb        (catalog `asset`, fitted per body)
//   legs under a long robe: <item>_under_<body>.glb when it exists

import type { Appearance, Finish, GearSlot } from "../../wasm/sim_wasm";
import { bodyType, clothingDef, CATALOG, CREATOR, eyeColourHex, GEAR_SLOTS, hairColourHex, hairStyle, hexToRgb01, isShippedStyle, migrateHair, skinToneHex, type BodyType, type Catalog, type Creator, type Dyes } from "../../content/catalog";
import { heightScale, shapeKeyWeights } from "../../content/appearance";
import type { PartRole } from "./assemble";
import type { Rgb } from "./dye";

export interface PartRequest {
  role: PartRole;
  /** Asset key, e.g. `gear/body/robe_school_house_a`; picks the greybox shape when no file exists. */
  asset: string;
  /** Files to try, best first, relative to the assets directory. */
  files: string[];
  /** Gear: palette indices (primary, secondary, trim). */
  dyes?: Dyes;
  /** Gear: a brewed finish (shimmer, colour-shift), when the item has one. */
  finish?: Finish;
  /** Hair under a hat. */
  cap?: boolean;
}

export interface Recipe {
  body: BodyType;
  /** Base body first, then hair, then gear in slot order. */
  parts: PartRequest[];
  skin: Rgb;
  hair: Rgb;
  /** The iris tint (the body mask's B channel, painted greyscale). */
  eye: Rgb;
  /** Creator palette indices of the eye and hair colour (the face texture's tints, docs/art/character-look.md section 3.1). */
  eyeIndex: number;
  hairIndex: number;
  shapeWeights: Map<string, number>;
  /** Uniform scale for the height slider. */
  scale: number;
}

/** The `_under` variant file of a fitted legs item (worn under long robes). */
export function underFile(asset: string, body: BodyType): string {
  return `${asset}_under_${body.id}.glb`;
}

export function recipeFor(a: Appearance, creator: Creator = CREATOR, catalog: Catalog = CATALOG): Recipe {
  const body = bodyType(a.bodyType, creator);
  const parts: PartRequest[] = [{ role: "base", asset: body.asset, files: [`${body.asset}.glb`] }];
  const hatOn = !!(a.outfit.hat && clothingDef(a.outfit.hat.itemDef, "hat", catalog));
  const style = hairStyle(migrateHair(a.hairStyle, a.bodyType, creator), creator);
  if (style && isShippedStyle(style)) {
    const plain = `${style.asset}_${body.id}.glb`;
    parts.push({
      role: "hair",
      asset: style.asset,
      files: hatOn ? [`${style.asset}_cap_${body.id}.glb`, plain] : [plain],
      cap: hatOn,
    });
  }
  for (const slot of GEAR_SLOTS) {
    const worn = a.outfit[slot as GearSlot];
    if (!worn) continue;
    const item = clothingDef(worn.itemDef, slot, catalog);
    if (!item) continue; // unknown or retired id: nothing to draw
    parts.push({ role: slot, asset: item.asset, files: [`${item.asset}_${body.id}.glb`], dyes: [worn.dyes[0], worn.dyes[1], worn.dyes[2]], ...(worn.finish && worn.finish !== "none" ? { finish: worn.finish } : {}) });
  }
  return {
    body,
    parts,
    skin: hexToRgb01(skinToneHex(a.skinTone, creator)),
    hair: hexToRgb01(hairColourHex(a.hairColour, creator)),
    eye: hexToRgb01(eyeColourHex(a.eyeColour, creator)),
    eyeIndex: creator.eye_colours[a.eyeColour] ? a.eyeColour : creator.default_eye_colour,
    hairIndex: creator.hair_colours[a.hairColour] ? a.hairColour : 0,
    shapeWeights: shapeKeyWeights(a, creator),
    scale: heightScale(a, creator),
  };
}
