import { describe, expect, it } from "vitest";
import { byKind, LoadoutDraft, taughtWhere } from "./loadout-state";
import { formatElapsed } from "./spectate";

const SCUTARA = 10;
const TORPEO = 11;
const DIMITTO = 12;
const TITILLARA = 13;
const REPULSA = 14;

describe("the loadout draft", () => {
  it("adds, removes and reorders within the slots, as the server checks", () => {
    const d = new LoadoutDraft({ learned: [1, SCUTARA, TORPEO, DIMITTO, TITILLARA], loadout: [SCUTARA, TORPEO, DIMITTO], slots: 3 });
    expect(d.dirty).toBe(false);
    expect(d.known().map((s) => s.id)).toEqual([SCUTARA, TORPEO, DIMITTO, TITILLARA]);
    // Spells L20 (CORE): the new dueling spells are not learned either (file order).
    expect(d.unknown().map((s) => s.id)).toEqual([REPULSA, 15, 4, 16, 17, 18]);
    expect(d.add(TITILLARA)).toContain("full");
    expect(d.add(TORPEO)).toContain("already");
    expect(d.add(REPULSA)).toContain("haven't learned");
    expect(d.add(1)).toContain("can't be cast in a duel");
    d.remove(DIMITTO);
    expect(d.dirty).toBe(true);
    expect(d.toggle(TITILLARA)).toBeNull();
    expect(d.spells).toEqual([SCUTARA, TORPEO, TITILLARA]);
    d.move(2, -2);
    expect(d.spells).toEqual([TITILLARA, SCUTARA, TORPEO]);
    d.move(0, -1); // off the end: nothing
    expect(d.spells).toEqual([TITILLARA, SCUTARA, TORPEO]);
    expect(d.problem()).toBeNull();
    d.revert();
    expect(d.spells).toEqual([SCUTARA, TORPEO, DIMITTO]);
    expect(d.dirty).toBe(false);
  });

  it("checks like the server and warns without a shield or with nothing", () => {
    const d = new LoadoutDraft({ learned: [SCUTARA, TORPEO], loadout: [], slots: 3 });
    expect(d.warning()).toContain("no spells");
    d.spells = [TORPEO, TORPEO];
    expect(d.problem()).toContain("twice");
    d.spells = [TORPEO, REPULSA];
    expect(d.problem()).toContain("haven't learned");
    d.spells = [TORPEO, SCUTARA, 1, 2];
    expect(d.problem()).toContain("At most 3");
    d.spells = [TORPEO];
    expect(d.problem()).toBeNull();
    expect(d.warning()).toContain("No shield");
  });

  it("adopts a new spellbook, keeping an edit where it is still allowed", () => {
    const d = new LoadoutDraft({ learned: [SCUTARA, TORPEO, DIMITTO], loadout: [SCUTARA, TORPEO, DIMITTO], slots: 3 });
    // Not editing: taken as is.
    d.adopt({ learned: [SCUTARA, TORPEO, DIMITTO, TITILLARA], loadout: [TORPEO], slots: 3 });
    expect(d.spells).toEqual([TORPEO]);
    // Editing: kept, trimmed to what is allowed now.
    d.add(DIMITTO);
    d.add(TITILLARA);
    d.adopt({ learned: [SCUTARA, TORPEO, DIMITTO], loadout: [TORPEO], slots: 2 });
    expect(d.spells).toEqual([TORPEO, DIMITTO]);
    expect(d.slots).toBe(2);
  });

  it("offers all ten dueling spells to a level-19 duelist for five slots, keys 1-5, grouped by kind", () => {
    const ALL = [SCUTARA, TORPEO, DIMITTO, TITILLARA, REPULSA, 15, 4, 16, 17, 18];
    // A level-19 spellbook: every spell learned, the five-slot loadout.
    const d = new LoadoutDraft({ learned: [1, 2, 3, 5, 30, ...ALL], loadout: [SCUTARA, TORPEO, DIMITTO, TITILLARA, REPULSA], slots: 5 });
    expect(d.known().map((s) => s.id)).toEqual(ALL);
    expect(d.unknown()).toEqual([]);
    expect(d.add(15)).toContain("full");
    d.remove(TITILLARA);
    d.remove(REPULSA);
    for (const id of [15, 17]) expect(d.add(id)).toBeNull();
    expect(d.spells).toEqual([SCUTARA, TORPEO, DIMITTO, 15, 17]);
    // Slot order is the key order: key 4 is Binding Curse, key 5 Slowing Jinx.
    d.move(4, -1);
    expect(d.spells.indexOf(17) + 1).toBe(4);
    expect(d.problem()).toBeNull();
    // Non-dueling spells (Colour Charm, Bird Conjuring, Revealing Charm) are not offered.
    expect(d.add(3)).toContain("can't be cast in a duel");
    expect(d.add(30)).toContain("can't be cast in a duel");

    const groups = byKind(d.known());
    expect(groups.map((g) => g.label)).toEqual(["Shield", "Attack", "Disarm", "Bind", "Hinder", "Summon", "Counter", "Ward"]);
    expect(groups.find((g) => g.kind === "attack")!.spells.map((s) => s.id)).toEqual([TORPEO, TITILLARA, REPULSA]);
    expect(groups.reduce((n, g) => n + g.spells.length, 0)).toBe(10);
    expect(byKind([])).toEqual([]);
  });

  it("names where each dueling spell is taught: the club, or Defensive Magic", () => {
    expect(taughtWhere("dueling_club")).toBe("at the Dueling Club");
    expect(taughtWhere("defence")).toBe("in Defensive Magic");
  });

  it("formats how long a watched duel has run", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(67_400)).toBe("1:07");
    expect(formatElapsed(-5)).toBe("0:00");
  });
});
