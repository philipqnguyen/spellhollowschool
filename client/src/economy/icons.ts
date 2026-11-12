// Item icons for the economy windows. An item's icon is a PNG under
// `client/public/assets/icons/` when the art exists: items whose asset key is
// already an icon key (`icons/<kind>/<name>`, materials, dyes, potions, ...)
// at `assets/<asset>.png`, clothing and furniture at
// `assets/icons/<asset>.png`. When `assets/icons/manifest.json`
// (`{ "icons": ["icons/material/wool_skein", ...] }`) is present only the
// listed icons are requested; without it each icon is tried once and a miss
// is remembered. Anything without art gets a generated icon: a silhouette
// for its kind (or slot) in the item's own dye colours, so lists stay
// readable before the art exists. Art shows the default dyes, so a dyed
// instance's icon carries a chip of its actual colours (`itemIcon`).

import { paletteHex, type Dyes } from "../content/catalog";
import type { Finish } from "../wasm/sim_wasm";
import { econDef, type EconItemDef } from "./items";
import { assetUrl } from "../assets";

const SVG_NS = "http://www.w3.org/2000/svg";

/** The icon key of an item (`icons/...`, without the extension). */
export function iconKey(def: Pick<EconItemDef, "asset" | "kind">): string {
  return def.asset.startsWith("icons/") ? def.asset : `icons/${def.asset}`;
}

/** The URL of an item's icon art (src/assets.ts). */
export function iconUrl(def: Pick<EconItemDef, "asset" | "kind">): string {
  return assetUrl(`assets/${iconKey(def)}.png`);
}

/** Icon keys the manifest lists; null while unknown or when there is no manifest. */
let listed: Set<string> | null = null;
let manifestState: "unknown" | "loading" | "present" | "absent" = "unknown";
const missing = new Set<string>();

/** Loads the icon manifest once (call when a window first opens). */
export function loadIconManifest(): void {
  if (manifestState !== "unknown") return;
  manifestState = "loading";
  fetch(assetUrl("assets/icons/manifest.json"))
    .then(async (r) => {
      if (!r.ok) throw new Error(String(r.status));
      const data = (await r.json()) as { icons?: unknown };
      const keys = Array.isArray(data.icons) ? data.icons.filter((k): k is string => typeof k === "string") : [];
      listed = new Set(keys);
      manifestState = "present";
    })
    .catch(() => {
      manifestState = "absent";
    });
}

/** Whether to try loading the PNG for `key`. */
function worthTrying(key: string): boolean {
  if (missing.has(key)) return false;
  if (manifestState === "present") return listed!.has(key);
  // Until the manifest answers, draw the generated icon (no request storm).
  return manifestState === "absent";
}

/** The dyes an item's icon art was rendered in: the catalog defaults. */
function artDyes(def: EconItemDef): [number, number, number] {
  return [def.dyes.primary, def.dyes.secondary, def.dyes.trim];
}

/**
 * Whether an instance in `dyes` looks different from its icon art (which
 * shows the catalog's default dyes). Only clothing and furniture take dyes;
 * for everything else the colour is the item itself (a dye, a potion's tint).
 */
export function dyedAwayFromArt(def: EconItemDef | undefined, dyes: readonly number[] | undefined): boolean {
  if (!def || !dyes || (def.kind !== "clothing" && def.kind !== "furniture")) return false;
  const art = artDyes(def);
  return dyes[0] !== art[0] || dyes[1] !== art[1] || dyes[2] !== art[2];
}

/**
 * An icon element for catalog item `itemDef`, in `dyes` (the instance's;
 * the catalog's defaults when omitted), with `finish` if it has one.
 * Decorative: the item's name is always written next to it.
 *
 * Icon art is rendered once per item in its default dyes, so an instance
 * dyed otherwise gets a swatch chip of its own three colours in the corner
 * (primary, secondary, trim): cheap (three CSS stripes, no image work) and
 * exact, where tinting the art would need a dye mask per icon and per-pixel
 * work for every row. The generated silhouette (no art) is drawn in the
 * instance's dyes already. A finished garment gets a small sparkle mark.
 */
export function itemIcon(itemDef: number, dyes?: Dyes | readonly number[], size: "small" | "large" = "small", finish: Finish = "none"): HTMLElement {
  const def = econDef(itemDef);
  const wrap = document.createElement("span");
  wrap.className = `item-icon item-icon-${size}`;
  wrap.setAttribute("aria-hidden", "true");
  const colours = dyes ?? (def ? artDyes(def) : [1, 2, 3]);
  const art: Element = generatedIcon(def, colours);
  wrap.append(art);
  if (def) {
    const key = iconKey(def);
    if (worthTrying(key)) {
      const img = document.createElement("img");
      img.alt = "";
      // Not `loading = "lazy"`: the image stays out of the document until it
      // has loaded, and a lazy image that is not in the document never loads.
      // Lists are pages of 25 small icons, so eager loading costs little.
      img.decoding = "async";
      // With an Origin header, like every request for a bucket asset (docs/deploy.md, "Assets from a bucket").
      img.crossOrigin = "anonymous";
      img.onload = () => {
        art.replaceWith(img);
        // The art shows the default dyes: say which colours this one has.
        if (dyedAwayFromArt(def, colours)) wrap.append(dyeChip(colours));
      };
      img.onerror = () => missing.add(key);
      img.src = iconUrl(def);
    }
  }
  if (finish !== "none") {
    const mark = document.createElement("span");
    mark.className = `icon-finish icon-finish-${finish}`;
    mark.textContent = "✦";
    wrap.append(mark);
  }
  return wrap;
}

/** Three stripes in an instance's primary, secondary and trim dyes. */
function dyeChip(dyes: readonly number[]): HTMLElement {
  const chip = document.createElement("span");
  chip.className = "icon-dyes";
  chip.style.setProperty("--dye-p", paletteHex(dyes[0] ?? 1));
  chip.style.setProperty("--dye-s", paletteHex(dyes[1] ?? 2));
  chip.style.setProperty("--dye-t", paletteHex(dyes[2] ?? 3));
  return chip;
}

/** Silhouettes on a 24-unit grid: a body (filled with the primary dye) and details (in the trim). */
const SHAPES: Record<string, { body: string; detail: string }> = {
  hat: { body: "M12 2.5 7 15.5h10Z", detail: "M3.5 16.2c2.4 2 14.6 2 17 0-2.4-1.6-14.6-1.6-17 0Z M8.2 13.2h7.6" },
  body: { body: "M8.5 3.5 4 6.5l1.6 4 1.9-.8V21h9V9.7l1.9.8 1.6-4-4.5-3c-.7 1.6-2 2.4-3.5 2.4S9.2 5.1 8.5 3.5Z", detail: "M12 5.9V21 M7.5 21h9" },
  legs: { body: "M6.5 3.5h11l1 17.5h-4.4L12 9.5 9.9 21H5.5Z", detail: "M6.5 6h11 M12 6v3.5" },
  shoes: { body: "M7 4h5v9.5l6.3 2.6c1.5.6 2.2 1.6 2.2 3V20H4.5v-3.6L7 13.5Z", detail: "M4.5 18.2h16 M9 7.5h3 M9 10.5h3" },
  furniture: { body: "M4.5 9.5h15V18h-15Z", detail: "M6 18v3 M18 18v3 M4.5 9.5 6 5h12l1.5 4.5 M12 9.5V18" },
  material: { body: "M4 12c0-4 3.6-7 8-7s8 3 8 7-3.6 7-8 7-8-3-8-7Z", detail: "M6.5 8.5c3 1.5 8 1.5 11 0 M5 12c3.5 1.6 10.5 1.6 14 0 M6.5 15.5c3-1.5 8-1.5 11 0" },
  dye: { body: "M9.5 8.5h5l2.5 3.5V21H7v-9Z", detail: "M10 3.5h4v5h-4Z M7 15h10" },
  finish: { body: "M12 2.5 14.2 9.8 21.5 12l-7.3 2.2L12 21.5l-2.2-7.3L2.5 12l7.3-2.2Z", detail: "M18.5 3.5v3 M17 5h3 M5.5 17.5v3 M4 19h3" },
  potion: { body: "M10 3.5h4v5.2a7 7 0 1 1-4 0Z", detail: "M9 3.5h6 M6 15h12" },
  pattern: { body: "M5.5 4.5h11a2 2 0 0 1 2 2V19.5h-11a2 2 0 0 1-2-2Z", detail: "M8 8.5h7.5 M8 11.5h7.5 M8 14.5h5 M5.5 17.5a2 2 0 0 0 2 2" },
};

function generatedIcon(def: EconItemDef | undefined, dyes: readonly number[]): SVGSVGElement {
  const shape = SHAPES[def ? (def.kind === "clothing" ? (def.slot ?? "body") : def.kind) : "material"] ?? SHAPES.material!;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("focusable", "false");
  const body = document.createElementNS(SVG_NS, "path");
  body.setAttribute("d", shape.body);
  body.setAttribute("fill", paletteHex(dyes[0] ?? 1));
  body.setAttribute("stroke", paletteHex(dyes[1] ?? 2));
  body.setAttribute("stroke-width", "1.2");
  body.setAttribute("stroke-linejoin", "round");
  const detail = document.createElementNS(SVG_NS, "path");
  detail.setAttribute("d", shape.detail);
  detail.setAttribute("fill", "none");
  detail.setAttribute("stroke", paletteHex(dyes[2] ?? 3));
  detail.setAttribute("stroke-width", "1.3");
  detail.setAttribute("stroke-linecap", "round");
  detail.setAttribute("stroke-linejoin", "round");
  svg.append(body, detail);
  return svg;
}

/** A gold coin, for the purse and prices. */
export function coinIcon(): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.classList.add("coin-icon");
  const outer = document.createElementNS(SVG_NS, "circle");
  outer.setAttribute("cx", "12");
  outer.setAttribute("cy", "12");
  outer.setAttribute("r", "9");
  const inner = document.createElementNS(SVG_NS, "circle");
  inner.setAttribute("cx", "12");
  inner.setAttribute("cy", "12");
  inner.setAttribute("r", "5.6");
  inner.setAttribute("class", "coin-rim");
  svg.append(outer, inner);
  return svg;
}

/** An owl in flight, for the mail button and the delivery flourish. */
export function owlIcon(className = "hud-icon"): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.classList.add(className);
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute(
    "d",
    "M12 7.5c-1.6 0-2.8 1.2-2.8 2.9v3.3c0 1.8 1.3 3.3 2.8 3.3s2.8-1.5 2.8-3.3v-3.3c0-1.7-1.2-2.9-2.8-2.9Z M9.2 9.2 2.5 6.5c1 3.5 3.3 6 6.7 6.8 M14.8 9.2l6.7-2.7c-1 3.5-3.3 6-6.7 6.8 M10.6 10.3h.01 M13.4 10.3h.01 M11.2 17v2.5 M12.8 17v2.5",
  );
  svg.append(path);
  return svg;
}
