import { describe, expect, it } from "vitest";
import { SPELLS, type SpellDef } from "../game/spells/data";
import {
  autoAdd,
  castableHere,
  cleanSlots,
  defaultHotbar,
  hotbarPlace,
  dragRefusal,
  HOTBAR_SLOTS,
  hotbarSpells,
  isHotbarSpell,
  moveSlot,
  normalize,
  placeSpell,
  removeSlot,
  sameSlots,
  slotKey,
  slotOfCode,
  slotViews,
  type HotbarContext,
} from "./hotbar-state";

const byKey = (key: string) => SPELLS.find((s) => s.key === key)!;
const id = (key: string) => byKey(key).id;
const ALL = SPELLS.map((s) => s.id);
const EMPTY = normalize([]);
const ctx: HotbarContext = { learned: ALL, zone: "castle", lit: false, guardian: false, aiming: false, cooldown: () => 0 };

describe("which spells go on the hotbar", () => {
  it("takes every spell that does something outside a duel, from the data, in spellbook order", () => {
    expect(hotbarSpells().map((s) => s.key)).toEqual(["lucerna", "elevara", "tinctura", "adveni", "aperio", "volucra", "rigora", "dissolvo", "tardara", "custodio"]);
    for (const s of SPELLS) expect(isHotbarSpell(s)).toBe(s.social.kind !== "flourish");
  });

  it("follows the data: a new flourish stays off, a new social spell goes on", () => {
    const flourish: SpellDef = { ...byKey("torpeo"), id: 900, key: "x", school: "charms" };
    const light: SpellDef = { ...byKey("lucerna"), id: 901, key: "y", school: "transfiguration" };
    expect(hotbarSpells([...SPELLS, flourish, light]).map((s) => s.id)).toContain(901);
    expect(hotbarSpells([...SPELLS, flourish, light]).map((s) => s.id)).not.toContain(900);
  });

  it("lets only learned hotbar spells be dragged, and says why not", () => {
    expect(dragRefusal(byKey("lucerna"), [id("lucerna")])).toBeNull();
    expect(dragRefusal(byKey("lucerna"), [])).toMatch(/Learn it first/);
    expect(dragRefusal(byKey("torpeo"), ALL)).toMatch(/flourish/);
  });
});

describe("the layout", () => {
  it("defaults to the learned hotbar spells in spellbook order", () => {
    const bar = defaultHotbar([id("rigora"), id("torpeo"), id("lucerna"), id("volucra")]);
    expect(bar).toEqual([id("lucerna"), id("volucra"), id("rigora"), null, null, null, null, null, null, null]);
    expect(defaultHotbar(ALL).every((s) => s !== null)).toBe(true);
    expect(defaultHotbar(ALL)).toHaveLength(HOTBAR_SLOTS);
  });

  it("reorders: a slot dragged onto an empty one moves there", () => {
    const bar = normalize([id("lucerna"), id("elevara")]);
    expect(moveSlot(bar, 0, 5)).toEqual(normalize([null, id("elevara"), null, null, null, id("lucerna")]));
  });

  it("swaps when dropped on an occupied slot", () => {
    const bar = normalize([id("lucerna"), id("elevara"), id("aperio")]);
    expect(moveSlot(bar, 0, 2)).toEqual(normalize([id("aperio"), id("elevara"), id("lucerna")]));
    expect(moveSlot(bar, 1, 1)).toEqual(bar);
    expect(moveSlot(bar, 1, 10)).toEqual(bar);
  });

  it("places a spell from the spellbook, replacing what is there; one already on the bar moves", () => {
    const bar = normalize([id("lucerna"), id("elevara")]);
    expect(placeSpell(bar, id("aperio"), 1)).toEqual(normalize([id("lucerna"), id("aperio")]));
    expect(placeSpell(bar, id("lucerna"), 4)).toEqual(normalize([null, id("elevara"), null, null, id("lucerna")]));
    expect(placeSpell(bar, id("lucerna"), 0)).toEqual(bar);
    expect(placeSpell(bar, id("aperio"), -1)).toEqual(bar);
  });

  it("removes a slot", () => {
    const bar = normalize([id("lucerna"), id("elevara")]);
    expect(removeSlot(bar, 0)).toEqual(normalize([null, id("elevara")]));
    expect(removeSlot(bar, 7)).toEqual(bar);
  });

  it("adds spells just learned to the first empty slots, once, while there is room", () => {
    const bar = normalize([id("lucerna"), null, id("elevara")]);
    expect(autoAdd(bar, [id("torpeo"), id("tinctura"), id("lucerna"), id("adveni")])).toEqual(normalize([id("lucerna"), id("tinctura"), id("elevara"), id("adveni")]));
    const full = normalize(hotbarSpells().map((s) => s.id));
    expect(autoAdd(full, [id("custodio")])).toEqual(full);
    expect(autoAdd(EMPTY, [999])).toEqual(EMPTY);
  });

  it("drops what can't be there: unlearned, flourishes, unknown ids and repeats", () => {
    const bar = [id("lucerna"), id("torpeo"), 999, id("lucerna"), id("custodio"), id("aperio")];
    expect(cleanSlots(bar, [id("lucerna"), id("torpeo"), id("aperio")])).toEqual(normalize([id("lucerna"), null, null, null, null, id("aperio")]));
  });

  it("compares layouts of any length", () => {
    expect(sameSlots([id("lucerna")], normalize([id("lucerna")]))).toBe(true);
    expect(sameSlots([id("lucerna")], [null, id("lucerna")])).toBe(false);
  });
});

describe("keys", () => {
  it("names the slots 1-9 then 0", () => {
    expect(Array.from({ length: HOTBAR_SLOTS }, (_, i) => slotKey(i)).join("")).toBe("1234567890");
  });

  it("maps the digit keys and the keypad to slots", () => {
    expect(slotOfCode("Digit1")).toBe(0);
    expect(slotOfCode("Digit9")).toBe(8);
    expect(slotOfCode("Digit0")).toBe(9);
    expect(slotOfCode("Numpad0")).toBe(9);
    expect(slotOfCode("KeyL")).toBeNull();
  });
});

describe("what a slot shows", () => {
  it("names the spell, its key and how to aim it", () => {
    const [slot] = slotViews([id("rigora")], ctx);
    expect(slot!.spell?.key).toBe("rigora");
    expect(slot!.title.startsWith(`${byKey("rigora").name} (1)\n`)).toBe(true);
    expect(slot!.title).toContain("Tab");
    const empty = slotViews([], ctx)[9]!;
    expect(empty.spell).toBeNull();
    expect(empty.key).toBe("0");
  });

  it("glows while a lasting spell is on", () => {
    const bar = [id("lucerna"), id("custodio"), id("elevara"), id("aperio")];
    expect(slotViews(bar, ctx).map((s) => s.on).slice(0, 4)).toEqual([false, false, false, false]);
    expect(slotViews(bar, { ...ctx, lit: true, guardian: true, aiming: true }).map((s) => s.on).slice(0, 4)).toEqual([true, true, true, false]);
    expect(slotViews(bar, { ...ctx, lit: true })[0]!.title).toMatch(/puts your light out/);
  });

  it("dims what can't be cast here: everything in an arena, Elevara outside a dorm room", () => {
    const bar = [id("lucerna"), id("elevara")];
    expect(slotViews(bar, ctx).map((s) => s.off).slice(0, 2)).toEqual([false, true]);
    expect(slotViews(bar, { ...ctx, zone: "dorm" }).map((s) => s.off).slice(0, 2)).toEqual([false, false]);
    expect(slotViews(bar, { ...ctx, zone: "arena" }).map((s) => s.off).slice(0, 2)).toEqual([true, true]);
    expect(castableHere(byKey("aperio"), "class")).toBe(true);
  });

  it("shows the cooldown left, clamped", () => {
    const views = slotViews([id("lucerna"), id("aperio")], { ...ctx, cooldown: (s) => (s === id("aperio") ? 1.5 : 0.25) });
    expect(views[0]!.cooldown).toBe(0.25);
    expect(views[1]!.cooldown).toBe(1);
  });

  it("shows an unlearned spell as empty", () => {
    expect(slotViews([id("custodio")], { ...ctx, learned: [] })[0]!.spell).toBeNull();
  });
});

describe("where the bar sits", () => {
  const box = (left: number, top: number, right: number, bottom: number) => ({ left, top, right, bottom });
  const chat = box(12, 700, 452, 888);

  it("keeps to the bottom centre when the chat and the menu bar leave the middle free", () => {
    // 1600 wide: the bar spans 560-1040.
    expect(hotbarPlace(480, 52, [chat, box(1200, 840, 1588, 888), null], 1600, 900)).toEqual({ centre: 800, bottom: 12 });
  });

  it("moves sideways into the gap between them when it fits there", () => {
    // 1440 wide: centred, the bar (446) would reach 943 and the menu bar starts at 919; the gap from the chat is 467.
    expect(hotbarPlace(446, 50, [chat, box(919, 840, 1428, 888), box(1262, 785, 1428, 835)], 1440, 900)).toEqual({ centre: 690, bottom: 12 });
    // Only the menu bar beside it: it slides left of it.
    expect(hotbarPlace(480, 52, [box(600, 840, 988, 888)], 1000, 900)).toEqual({ centre: 354, bottom: 12 });
  });

  it("rises above whatever it would overlap when the gap is too narrow", () => {
    // 1000 wide: no room between the chat (to 452) and the menu bar (from 600).
    expect(hotbarPlace(480, 52, [chat, box(600, 840, 988, 888)], 1000, 900)).toEqual({ centre: 500, bottom: 206 });
    expect(hotbarPlace(480, 52, [box(200, 840, 400, 888), box(600, 840, 988, 888)], 1000, 900)).toEqual({ centre: 500, bottom: 66 });
  });

  it("ignores boxes higher up, and never leaves the screen", () => {
    expect(hotbarPlace(480, 52, [box(0, 100, 1000, 300)], 1000, 900)).toEqual({ centre: 500, bottom: 12 });
    expect(hotbarPlace(480, 52, [box(0, 10, 300, 900), box(400, 10, 1000, 900)], 1000, 300)).toEqual({ centre: 500, bottom: 236 });
  });
});
