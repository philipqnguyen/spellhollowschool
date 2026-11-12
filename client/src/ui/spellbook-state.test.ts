import { describe, expect, it } from "vitest";
import { SPELLS } from "../game/spells/data";
import { aimText, outsideText, spellbookGroups, type SpellbookState } from "./spellbook-state";

const ALL = SPELLS.map((s) => s.id);
const none: SpellbookState = { learned: [], loadout: [], lit: false, guardian: false };
const byKey = (key: string) => SPELLS.find((s) => s.key === key)!;

describe("the spellbook", () => {
  it("shows all 15 spells at level 19 in three groups, in level order within each", () => {
    const groups = spellbookGroups(19, { ...none, learned: ALL });
    expect(groups.map((g) => g.title)).toEqual(["Charms", "Transfiguration", "Defence and dueling"]);
    expect(groups.reduce((n, g) => n + g.rows.length, 0)).toBe(15);
    expect(groups[0]!.rows.map((r) => r.spell.key)).toEqual(["lucerna", "elevara", "tinctura", "adveni", "aperio"]);
    expect(groups[1]!.rows.map((r) => r.spell.key)).toEqual(["volucra"]);
    expect(groups[2]!.rows.map((r) => r.spell.unlock.level)).toEqual([1, 1, 1, 3, 5, 8, 12, 16, 19]);
    for (const g of groups) for (const r of g.rows) expect(r.learned).toBe(true);
  });

  it("labels each cast button from castLabel (lit wand, guardian out)", () => {
    const label = (st: SpellbookState) => new Map(spellbookGroups(19, st).flatMap((g) => g.rows.map((r) => [r.spell.key, r.button] as const)));
    const b = label({ ...none, learned: ALL });
    expect(b.get("lucerna")).toBe("Light (L)");
    expect(b.get("elevara")).toBe("Levitate… (V)");
    expect(b.get("tinctura")).toBe("Recolour");
    expect(b.get("volucra")).toBe("Release birds");
    expect(b.get("adveni")).toBe("Summon");
    expect(b.get("aperio")).toBe("Reveal");
    expect(b.get("scutara")).toBe("Flourish");
    expect(b.get("rigora")).toBe("Bind");
    expect(b.get("dissolvo")).toBe("End spells");
    expect(b.get("tardara")).toBe("Slow");
    expect(b.get("custodio")).toBe("Summon guardian");
    const out = label({ ...none, learned: ALL, lit: true, guardian: true });
    expect(out.get("lucerna")).toBe("Put out (L)");
    expect(out.get("custodio")).toBe("Dismiss guardian");
  });

  it("says where a locked spell is taught and from which level, and how to aim spells cast on someone else", () => {
    const rows = spellbookGroups(1, none).flatMap((g) => g.rows);
    const row = (key: string) => rows.find((r) => r.spell.key === key)!;
    expect(row("volucra").button).toBeNull();
    expect(row("volucra").where).toBe("Reach level 9, then learn it in Transfiguration class.");
    expect(row("custodio").where).toContain("Reach level 19");
    expect(row("volucra").tag).toBe("Lv 9 · Transfiguration class");
    expect(row("scutara").tag).toBe("Lv 1 · the Dueling Club");
    expect(row("aperio").where).toContain("Charms class");
    expect(aimText(byKey("rigora"))).toContain("Select a friend, party or club member first");
    expect(aimText(byKey("tinctura"))).toContain("yourself");
    expect(aimText(byKey("volucra"))).toBeNull();
    // Dueling spells say what they do in a duel; the new ones are no longer "a harmless flourish" outside it.
    expect(row("tardara").duel).toMatch(/^In a duel: 8 Poise · a wave 3 m wide/);
    expect(row("aperio").duel).toBeNull();
    expect(outsideText(byKey("torpeo"))).toContain("harmless flourish");
    for (const key of ["rigora", "adveni", "dissolvo", "tardara", "custodio"]) expect(outsideText(byKey(key)), key).not.toContain("flourish");
    expect(outsideText(byKey("tardara"))).toBe("Outside a duel: a friend, party or club member walks at 40% for 4 s.");
  });

  it("says truthfully per group which spells can go into a duel and where they are learned", () => {
    const g = new Map(spellbookGroups(19, { ...none, learned: ALL }).map((x) => [x.id, x]));
    for (const group of g.values()) {
      const dueling = group.rows.filter((r) => r.duel !== null);
      if (group.id === "defence") {
        expect(dueling.length, "every Defence spell duels").toBe(group.rows.length);
        expect(group.blurb).toContain("Every one can go in your dueling loadout");
        expect(new Set(group.rows.map((r) => r.spell.unlock.at))).toEqual(new Set(["dueling_club", "defence"]));
        expect(group.blurb).toMatch(/Dueling Club.*Defensive Magic/);
      } else {
        // A group that is not all dueling names its dueling spells (Charms: Summoning Charm), or says none duel.
        for (const r of dueling) expect(group.blurb, group.id).toContain(r.spell.name);
        if (!dueling.length) expect(group.blurb).toContain("not for duels");
        expect(group.blurb).not.toMatch(/anywhere outside/);
      }
    }
    expect(g.get("charms")!.rows.find((r) => r.spell.key === "adveni")!.duel).not.toBeNull();
  });
});
