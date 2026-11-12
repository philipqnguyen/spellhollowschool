// The HUD's layout rules. A long name with its house
// ("Ivy_Thornfield", "Skytalon") ran into the online count at 1280 px; the
// name gives way with an ellipsis (the whole name and house in the title),
// the place shrinks to nothing first, and the counts, the purse and the
// buttons never shrink. The HUD layout pass: the name and counts in the
// top-right corner, the menu bar at the bottom right (docs/architecture.md
// "Client"). Checked here on the stylesheet's
// rules and the pure corner arithmetic (vitest has no layout); the browser
// check was a set of screenshots (hud-layout-*.png, at 1920x1080, 1440x900
// and 1280x720), not kept in the repository.

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { hudCorners } from "./hud-corners";
import { clearOf, PLATE_HEIGHT } from "./nameplates";

const CSS = readFileSync(new URL("../style.css", import.meta.url), "utf8");
const HUD = readFileSync(new URL("./hud.ts", import.meta.url), "utf8");

/** The declarations of the first rule whose selector list is exactly `selector`. */
function rule(selector: string): Record<string, string> {
  const at = CSS.search(new RegExp(`(^|\\n)${selector.replace(/[.*+?^${}()|[\]\\>]/g, "\\$&")}\\s*\\{`));
  expect(at, selector).toBeGreaterThanOrEqual(0);
  const open = CSS.indexOf("{", at);
  const body = CSS.slice(open + 1, CSS.indexOf("}", open));
  const out: Record<string, string> = {};
  for (const decl of body.replace(/\/\*[\s\S]*?\*\//g, "").split(";")) {
    const [k, ...v] = decl.split(":");
    if (k && v.length) out[k.trim()] = v.join(":").trim();
  }
  return out;
}

describe("the HUD top bar", () => {
  it("cuts a long name and house short with an ellipsis instead of overlapping", () => {
    const me = rule(".hud-me");
    expect(me["min-width"]).toBe("0");
    expect(me.overflow).toBe("hidden");
    expect(me.flex).toBe("0 1 auto");
    for (const sel of [".hud-name", ".hud-house"]) {
      const r = rule(sel);
      expect(r["white-space"], sel).toBe("nowrap");
      expect(r.overflow, sel).toBe("hidden");
      expect(r["text-overflow"], sel).toBe("ellipsis");
    }
    // Review fix: the house sits under the name (a column), so the name has the house's width too,
    // and it keeps a few letters at the least.
    const who = rule(".hud-who");
    expect(who["flex-direction"]).toBe("column");
    expect(who["min-width"]).toBe("3em");
    expect(HUD).toMatch(/h\("span", \{ class: "hud-who" \}, h\("span", \{ class: "hud-name" \}, opts\.name\), h\("span", \{ class: "hud-house" \}, opts\.houseName\)\)/);
  });

  it("hides the place's words when it is squeezed to a sliver", () => {
    expect(rule(".hud-place")["container-type"]).toBe("inline-size");
    expect(CSS).toMatch(/@container \(max-width: 4em\) \{\s*\.hud-place-text \{\s*visibility: hidden;/);
  });

  it("never shrinks the counts, the purse or the buttons; the place gives way first", () => {
    expect(rule(".hud-counts").flex).toBe("none");
    expect(rule(".crest,\n.hud-dock > .hud-purse,\n.hud-dock > .hud-actions,\n.hud-dock > .icon-button").flex).toBe("none");
    const place = rule(".hud-place");
    expect(place.flex).toBe("1 1 0");
    expect(place["min-width"]).toBe("0");
    expect(place["text-overflow"]).toBe("ellipsis");
  });

  it("shows the whole name and house on hover", () => {
    expect(HUD).toMatch(/class: "hud-me", title: `\$\{opts\.name\} · \$\{opts\.houseName\}`/);
  });
});

describe("the HUD corners", () => {
  it("puts the name and counts at the top right and the menu bar at the bottom right", () => {
    const corner = rule(".hud-layer > .hud-corner");
    expect(corner.top).toBe("12px");
    expect(corner.right).toBe("12px");
    expect(corner.left).toBeUndefined();
    const dock = rule(".hud-layer > .hud-dock");
    expect(dock.bottom).toBe("12px");
    expect(dock.right).toBe("12px");
    // The purse, the feature buttons and Settings are in the bar; the counts are not.
    expect(HUD).toMatch(/h\("nav", \{ class: "hud-dock panel", "aria-label": "Menu" \}, this\.purse, this\.actions, this\.menuButton\)/);
    expect(HUD).toMatch(/h\("div", \{ class: "hud-corner" \}, this\.top\)/);
  });

  it("opens Settings upward from the menu bar, on screen", () => {
    const menus = CSS.match(/\n\.menu \{[^}]*\}/g)!.join("");
    expect(menus).toMatch(/bottom: calc\(var\(--hud-dock-h\) \+ 8px\)/);
    expect(menus).toMatch(/right: 12px/);
    expect(menus).not.toMatch(/\btop:/);
    expect(menus).toMatch(/max-height: calc\(100vh - var\(--hud-dock-h\) - 24px\)/);
  });

  it("measures the corners into keep-outs and the CSS the rest of the HUD sits by", () => {
    const { keepouts, vars } = hudCorners({
      corner: { left: 1018, top: 12, right: 1428, bottom: 127.2 },
      dock: { left: 915, top: 846, right: 1428, bottom: 888 },
      emotes: { left: 1260, top: 788, right: 1428, bottom: 840 },
      width: 1440,
      height: 900,
    });
    expect(vars).toEqual({ "--hud-corner-bottom": "127px", "--hud-dock-h": "54px", "--hud-dock-w": "525px", "--hud-br-h": "112px" });
    expect(keepouts).toEqual([
      { left: 1018, right: 1428, bottom: 127.2, edge: "top" },
      { left: 915, right: 1428, top: 846, bottom: 888, edge: "bottom" },
      { left: 1260, right: 1428, top: 788, bottom: 840, edge: "bottom" },
    ]);
    // Before layout (or with the HUD hidden): nothing to keep clear of, the stylesheet's defaults stand.
    expect(hudCorners({ corner: null, dock: null, emotes: null, width: 800, height: 600 })).toEqual({ keepouts: [], vars: {} });
    // The party list at the top left keeps plates below it while it shows.
    const party = { left: 12, top: 12, right: 232, bottom: 140 };
    expect(hudCorners({ corner: null, dock: null, emotes: null, topLeft: [party, null], width: 800, height: 600 }).keepouts).toEqual([{ left: 12, right: 232, bottom: 140, edge: "top" }]);
    // No emote bar: the bottom-right stack is the menu bar alone.
    expect(hudCorners({ corner: null, dock: { left: 500, top: 586, right: 1012, bottom: 628 }, emotes: null, width: 1024, height: 640 }).vars["--hud-br-h"]).toBe("54px");
  });

  it("keeps nameplates out of both corners: down below the top right, up above the bottom right", () => {
    const { keepouts } = hudCorners({
      corner: { left: 1018, top: 12, right: 1428, bottom: 127 },
      dock: { left: 915, top: 846, right: 1428, bottom: 888 },
      emotes: { left: 1260, top: 788, right: 1428, bottom: 840 },
      width: 1440,
      height: 900,
    });
    const plate = (x: number, y: number) => ({ id: 1, x, y, halfWidth: 40 });
    const top = plate(1200, 60);
    clearOf(top, keepouts);
    expect(top.y).toBe(127 + PLATE_HEIGHT);
    const bottom = plate(1000, 870);
    clearOf(bottom, keepouts);
    expect(bottom.y).toBe(846);
    const emote = plate(1300, 830);
    clearOf(emote, keepouts);
    expect(emote.y).toBe(788);
    // The top centre and the bottom left are the world's: plates there stay put.
    const free = plate(700, 40);
    clearOf(free, keepouts);
    expect(free.y).toBe(40);
    const left = plate(300, 870);
    clearOf(left, keepouts);
    expect(left.y).toBe(870);
  });

  it("moves the panels that sat under the old top bar to the corners they belong to", () => {
    const css = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
    // Nothing is placed by the old centred bar any more.
    for (const p of ["./potions.css", "./friends.css", "./players.css", "./classes.css", "./dorm.css", "./trade.css", "./schedule.css", "./invites.css", "../style.css"]) {
      expect(css(p), p).not.toMatch(/100% - 820px/);
    }
    expect(css("./emotes.css")).toMatch(/bottom: calc\(var\(--hud-dock-h\) \+ 6px\)/);
    expect(css("./schedule.css")).toMatch(/top: calc\(var\(--hud-corner-bottom\) \+ 6px\)/);
    expect(css("./duel.css")).toMatch(/bottom: calc\(var\(--hud-br-h\) \+ 8px\)/);
  });
});
