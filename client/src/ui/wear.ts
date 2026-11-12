// The "Female only" / "Male only" tag every item list shows
// on a restricted piece of clothing (catalogue, wardrobe, trade, mail, the
// Exchange, crafting, the creator). Anyone may own, buy, sell, trade and
// mail it; only wearing is restricted. The words come from the data
// (content/catalog.ts `wearLabel`); the tag's look is in style.css.

import { wearLabel, type ItemDef } from "../content/catalog";
import { h } from "./dom";

/** The tag for `item` (a catalog id or entry), or null when anyone may wear it. */
export function wearTag(item: ItemDef | number | undefined): HTMLElement | null {
  const label = wearLabel(item);
  return label ? h("span", { class: "wear-tag" }, label) : null;
}
