// The wardrobe's dye and finish costs: which dyes a recolour
// uses up by the server's dye rule (crafting-content.ts `dyesNeeded`: going
// back to an item's own colours or picking a basic-palette colour is free,
// any other colour takes one dye of its family), whether the bag holds them,
// and the brewed finishes (Shimmer Lacquer, Colour-Shift Varnish) a garment
// can take. The wardrobe asks this for its lines and buttons; the requests
// stay the wardrobe's.

import type { Dyes } from "../content/catalog";
import { paletteHex } from "../content/catalog";
import type { Finish, House } from "../wasm/sim_wasm";
import { bagOf, countByDef, type BagItem } from "./crafting-api";
import { def1c, defaultDyes, dyeCost, dyeCostAffordable, dyesNeeded, isBasic, itemName, type DyeCostLine } from "./crafting-content";
import { h } from "./dom";

/** What the wardrobe knows of an item instance for pricing it. */
export interface DyeableItem {
  id: number;
  item_def: number;
  dyes: Dyes;
  finish?: Finish;
}

export const FINISH_LABELS: Record<Finish, string> = { none: "No finish", shimmer: "Shimmer", colour_shift: "Colour-shift" };

export class WardrobeDyes {
  /** Dye units held per catalog id. */
  private dyes = new Map<number, number>();
  /** Finish stacks held. */
  private finishes: BagItem[] = [];
  private loaded = false;

  constructor(private readonly house: House | null) {}

  /** (Re)reads the dyes and finishes in the bag. Errors leave the last known counts. */
  async load(): Promise<void> {
    const [dyes, finishes] = await Promise.all([bagOf("dye"), bagOf("finish")]);
    this.dyes = countByDef(dyes);
    this.finishes = finishes.filter((f) => f.location === "inventory");
    this.loaded = true;
  }

  /** The cost lines of recolouring `item` to `wanted` (empty = free); null for colours outside the palette. */
  cost(item: DyeableItem, wanted: Dyes): DyeCostLine[] | null {
    const defaults = defaultDyes(item.item_def, this.house) ?? item.dyes;
    const need = dyesNeeded(defaults, item.dyes, wanted);
    return need ? dyeCost(need, this.dyes) : null;
  }

  /** Whether the bag pays for recolouring `item` to `wanted` (before the first load: let the server decide). */
  affordable(item: DyeableItem, wanted: Dyes): boolean {
    const lines = this.cost(item, wanted);
    return lines !== null && (!this.loaded || dyeCostAffordable(lines));
  }

  /** Whether choosing `colour` for `channel` costs nothing (the current colour, the item's own or a basic one). */
  isFree(item: DyeableItem, channel: number, colour: number): boolean {
    const defaults = defaultDyes(item.item_def, this.house) ?? item.dyes;
    return colour === item.dyes[channel] || colour === defaults[channel] || isBasic(colour);
  }

  /** "Free" / "Uses 1 × Madder Red Dye (you have 2)" under the palette. */
  costLine(item: DyeableItem, wanted: Dyes): HTMLElement {
    const lines = this.cost(item, wanted);
    if (lines === null) return h("p", { class: "dye-cost short" }, "That colour is not in the dye palette.");
    if (!lines.length) {
      const changed = wanted.some((d, i) => d !== item.dyes[i]);
      return h("p", { class: "dye-cost free" }, changed ? "Free: back to its own colours." : "Pick a colour. Its own colours and the plain basics (dotted) are free; others use up a dye.");
    }
    const ok = !this.loaded || dyeCostAffordable(lines);
    return h(
      "p",
      { class: `dye-cost ${ok ? "" : "short"}` },
      "Uses ",
      ...lines.flatMap((l, i) => [
        i ? ", " : "",
        h("span", { class: "dye-cost-line" }, h("span", { class: "swatch", style: `--swatch: ${familySwatch(l)}` }), `${l.need} × ${l.item === null ? "an unknown dye" : itemName(l.item)}`, this.loaded ? h("span", { class: "dye-cost-have" }, ` (you have ${l.have})`) : null),
      ]),
      ok ? "." : ". Brew or buy more dyes first.",
    );
  }

  /** The finishes the bag holds, with how many of each. */
  finishStacks(): { item: number; finish: Exclude<Finish, "none">; count: number; stack: number }[] {
    const by = new Map<number, { item: number; finish: Exclude<Finish, "none">; count: number; stack: number }>();
    for (const f of this.finishes) {
      const finish = def1c(f.item_def)?.finish;
      if (!finish) continue;
      const e = by.get(f.item_def);
      if (e) e.count += f.quantity;
      else by.set(f.item_def, { item: f.item_def, finish, count: f.quantity, stack: f.id });
    }
    return [...by.values()].sort((a, b) => a.item - b.item);
  }

  /**
   * The wardrobe's finish section for a clothing item: what it has now and
   * a button per finish held. Null when there is nothing to show.
   */
  finishSection(item: DyeableItem, busy: boolean, onApply: (finishStackId: number, finishDef: number) => void): HTMLElement | null {
    const current = item.finish ?? "none";
    const stacks = this.finishStacks();
    if (!stacks.length && current === "none") return null;
    return h(
      "div",
      { class: "finish-section" },
      h("p", { class: "detail-label" }, "Finish"),
      h("p", { class: "detail-sub" }, current === "none" ? "No finish yet. A brewed finish is used up when applied." : `${FINISH_LABELS[current]}. Another finish replaces it.`),
      ...(stacks.length
        ? [
            h(
              "div",
              { class: "detail-actions" },
              ...stacks.map((s) =>
                h(
                  "button",
                  { type: "button", class: "secondary", disabled: busy || s.finish === current, title: `Uses one ${itemName(s.item)}`, onclick: () => onApply(s.stack, s.item) },
                  `${itemName(s.item)} (${s.count})`,
                ),
              ),
            ),
          ]
        : [h("p", { class: "muted-line" }, "Brew a finish at a cauldron to give clothes a shimmer.")]),
    );
  }
}

/** A representative colour of a dye line's family (its dye item's primary). */
function familySwatch(l: DyeCostLine): string {
  const d = l.item === null ? undefined : def1c(l.item);
  return paletteHex(d?.dyes.primary ?? 0);
}
