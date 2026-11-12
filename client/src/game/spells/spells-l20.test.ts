// Spells L20 (CORE): the new spells' casting words and target rules
// (data.ts), and the spell effects in the drawing summary (effects/state.ts).

import { describe, expect, it } from "vitest";
import { summarize } from "../effects/state";
import { castLabel, mayTargetSelf, needsAlly, SPELLS, type SpellDef } from "./data";

const byKey = (key: string): SpellDef => SPELLS.find((s) => s.key === key)!;
const off = { lit: false, guardian: false };

describe("spells to level 20", () => {
  it("reads all fifteen spells, in three schools", () => {
    expect(SPELLS).toHaveLength(15);
    expect(new Set(SPELLS.map((s) => s.school))).toEqual(new Set(["charms", "transfiguration", "defence"]));
    expect(byKey("volucra").school).toBe("transfiguration");
    expect(byKey("custodio").unlock).toEqual({ level: 19, at: "defence" });
  });

  it("labels every cast button", () => {
    const labels = Object.fromEntries(SPELLS.map((s) => [s.key, castLabel(s, off)]));
    expect(labels).toMatchObject({
      lucerna: "Light (L)",
      elevara: "Levitate… (V)",
      torpeo: "Flourish",
      tinctura: "Recolour",
      rigora: "Bind",
      volucra: "Release birds",
      adveni: "Summon",
      dissolvo: "End spells",
      aperio: "Reveal",
      tardara: "Slow",
      custodio: "Summon guardian",
    });
    expect(castLabel(byKey("lucerna"), { lit: true, guardian: false })).toBe("Put out (L)");
    expect(castLabel(byKey("custodio"), { lit: false, guardian: true })).toBe("Dismiss guardian");
  });

  it("knows which spells need a friend and which fall back to yourself", () => {
    const friend = SPELLS.filter(needsAlly).map((s) => s.key);
    expect(friend).toEqual(["rigora", "adveni", "tardara"]);
    const self = SPELLS.filter(mayTargetSelf).map((s) => s.key);
    expect(self).toEqual(["tinctura", "dissolvo"]);
  });

  it("summarizes the spell effects for drawing", () => {
    const s = summarize([{ kind: "bound" }, { kind: "slowed", pct: 40 }, { kind: "recoloured", colour: 21 }, { kind: "guardian", animal: "owl", guarding: true }]);
    expect(s.bound).toBe(true);
    expect(s.slowedPct).toBe(40);
    expect(s.recolour).toBe(21);
    expect(s.guardian).toEqual({ animal: "owl", guarding: true });
    const none = summarize([{ kind: "lucerna" }]);
    expect([none.bound, none.slowedPct, none.recolour, none.guardian]).toEqual([false, null, null, null]);
  });
});
