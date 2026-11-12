// The Credits panel: who made the art, models, animations, sounds, fonts and
// code libraries in the game, with their licences and links. Drawn from the
// model client/scripts/credits.mjs compiles in (`virtual:shs-credits`, from
// data/credits.json), so the panel, client/public/assets/CREDITS.md and
// THIRD-PARTY-NOTICES.txt never disagree.
//
// Two places show it: a card of its own from the sign-in screens
// (`renderCredits`, screens.ts) and the Credits page of Settings
// (`creditsBody`, hud.ts). Both load this module lazily, so the credits stay
// out of the entry chunk.

import credits, { type CreditEntry, type CreditSection, type Credits } from "virtual:shs-credits";
import { h, mount } from "./dom";
import "./credits.css";

/** A link that opens in a new tab and tells the page nothing about the game. */
function external(text: string, href: string): HTMLAnchorElement {
  return h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);
}

function entryItem(e: CreditEntry): HTMLElement {
  return h(
    "li",
    { class: "credit" },
    h("span", { class: "credit-work" }, e.link ? external(e.work, e.link) : e.work),
    e.author ? h("span", { class: "credit-by" }, `by ${e.author}`) : null,
    h("span", { class: "credit-licence" }, e.licenceUrl ? external(e.licence, e.licenceUrl) : e.licence),
    e.note ? h("span", { class: "credit-note" }, e.note) : null,
  );
}

function section(s: CreditSection): HTMLElement {
  return h(
    "section",
    { class: "credit-section", "data-section": s.id },
    h("h3", {}, s.title),
    s.intro ? h("p", { class: "credit-intro" }, s.intro) : null,
    s.entries.length > 0 ? h("ul", { class: "credit-list" }, ...s.entries.map(entryItem)) : null,
  );
}

/** The game's own licences, the source, and the files that carry the full texts. */
function licences(data: Credits): HTMLElement {
  const base = import.meta.env.BASE_URL;
  return h(
    "section",
    { class: "credit-section", "data-section": "licences" },
    h("h3", {}, "Licences"),
    h(
      "ul",
      { class: "credit-list" },
      ...data.game.licences.map((l) => h("li", { class: "credit" }, h("span", { class: "credit-work" }, l.what), h("span", { class: "credit-licence" }, external(l.licence, l.licenceUrl)))),
    ),
    h(
      "p",
      { class: "credit-intro" },
      external("Source code and art sources", data.game.repository),
      " · ",
      external("Asset credits", `${base}assets/CREDITS.md`),
      " · ",
      external("Third-party notices", `${base}${data.notices}`),
    ),
  );
}

/** Every credit, section by section, then the game's licences. */
export function creditsBody(data: Credits = credits): HTMLElement {
  return h("div", { class: "credits", "data-testid": "credits" }, ...data.sections.map(section), licences(data));
}

export interface CreditsCardOptions {
  /** Back to the screen that opened the credits. */
  onBack: () => void;
}

/** The credits on a card of their own (from the sign-in screens). */
export function renderCredits(root: HTMLElement, opts: CreditsCardOptions): void {
  const back = h("button", { type: "button", class: "link", onclick: (e: Event) => (e.preventDefault(), opts.onBack()) }, "Back");
  mount(
    root,
    h(
      "section",
      { class: "card credits-card", role: "dialog", "aria-labelledby": "card-heading" },
      h("p", { class: "card-kicker" }, credits.game.name),
      h("h1", { id: "card-heading" }, "Credits"),
      h("p", { class: "card-sub" }, "With thanks to everyone whose work is in the castle."),
      h("div", { class: "rule", "aria-hidden": "true" }),
      creditsBody(),
      h("p", { class: "card-foot" }, back),
    ),
  );
  back.focus();
}
