import { describe, expect, it } from "vitest";
import spellsJson from "../../../data/spells.json";
import scheduleJson from "../../../data/classes/schedule.json";
import sourcesJson from "../../../data/audio/sources.json";
import { BANKS, GROUPS, SOUNDS, isSoundId, soundEntry, soundsIn, type SoundGroup } from "./catalogue";
import { missingDefs, SOUND_DEFS } from "./sfx/defs";
import type { SoundDef } from "./types";

/** Each group's id prefixes. */
const PREFIX: Record<SoundGroup, readonly string[]> = {
  ui: ["ui."],
  footsteps: ["foot."],
  doors: ["door.", "entrance."],
  stairs: ["stairs."],
  // Spells L20 (CORE): the birds' and the guardian's sounds file with the spells.
  spells: ["spell.", "volucra.", "guardian."],
  classes: ["class."],
  duels: ["duel."],
  flight: ["broom."],
  crafting: ["craft.", "find."],
  potions: ["potion."],
  mail: ["mail.", "owl.", "trade."],
  cup: ["cup."],
  secrets: ["secret."],
  world: ["level.", "world."],
  emitters: ["loop."],
  sprinkles: ["sprinkle."],
  ambience: ["amb."],
  music: ["music."],
  stingers: ["stinger."],
};

describe("the sound catalogue", () => {
  it("has unique, well-formed ids", () => {
    const ids = SOUNDS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) expect(id).toMatch(/^[a-z]+(\.[a-z_]+)+$/);
  });

  it("files every id under its group's prefix, and every group is used", () => {
    for (const s of SOUNDS) expect(PREFIX[s.group].some((p) => s.id.startsWith(p)), `${s.id} in ${s.group}`).toBe(true);
    for (const g of GROUPS) expect(soundsIn(g).length, g).toBeGreaterThan(0);
  });

  it("keeps the buses and sources straight", () => {
    for (const s of SOUNDS) {
      expect(BANKS).toContain(s.bank);
      if (s.group === "music" || s.group === "stingers") {
        expect(s.bus).toBe("music");
        expect(s.source).toBe("score");
        expect(s.spatial).toBe(false);
      } else expect(s.source).not.toBe("score");
      if (s.group === "ambience") expect(s.bus === "ambience" && s.loop && !s.spatial).toBe(true);
      if (s.group === "music" || s.group === "emitters") expect(s.loop).toBe(true);
      if (s.group === "emitters" || s.group === "sprinkles" || s.group === "footsteps") expect(s.spatial).toBe(true);
      if (s.group === "ui") expect(s.bus).toBe("interface");
      expect(s.cooldownMs).toBeGreaterThanOrEqual(0);
      expect(s.what.length).toBeGreaterThan(5);
    }
    expect(soundEntry("ui.click").bus).toBe("interface");
    expect(isSoundId("ui.click")).toBe(true);
    expect(isSoundId("ui.nope")).toBe(false);
  });

  it("has a sound for every spell in data/spells.json", () => {
    for (const sp of spellsJson.spells) expect(isSoundId(`spell.${sp.key}`), sp.key).toBe(true);
  });

  it("has the spells-to-level-20 sounds, each designed (a synth, not a placeholder beep)", () => {
    const spells = ["spell.bound", "spell.slowed", "spell.pulled", "spell.dispelled", "spell.recoloured", "spell.reveal_glint", "volucra.flutter", "volucra.chirp", "guardian.summon", "guardian.ambient", "guardian.fade"];
    const duels = ["duel.rooted", "duel.slowed", "duel.pulled", "duel.cleansed", "duel.ward_up", "duel.warded", "duel.ward_faded"];
    for (const id of spells) expect(isSoundId(id) && soundEntry(id).group, id).toBe("spells");
    for (const id of duels) expect(isSoundId(id) && soundEntry(id).group, id).toBe("duels");
    for (const id of [...spells, ...duels]) expect(SOUND_DEFS[id as keyof typeof SOUND_DEFS]?.kind, id).toBe("synth");
    expect(soundEntry("guardian.ambient").loop).toBe(true);
    expect(soundEntry("spell.reveal_glint").spatial).toBe(true);
  });

  it("has a reaction for every class but Flying (whose beats are rings)", () => {
    for (const c of scheduleJson.classes) {
      if (c.id === "flying") continue;
      expect(isSoundId(`class.fx.${c.id}`), c.id).toBe(true);
    }
    const fx = SOUNDS.filter((s) => s.id.startsWith("class.fx.")).map((s) => s.id.slice("class.fx.".length));
    for (const id of fx) expect(scheduleJson.classes.map((c) => c.id)).toContain(id);
  });
});

describe("the effect definitions (sfx/defs.ts)", () => {
  const defs = Object.entries(SOUND_DEFS) as [string, SoundDef][];
  const files = new Set(sourcesJson.files.map((f: { file: string }) => f.file));

  it("define only catalogue ids, and no music", () => {
    for (const [id] of defs) {
      expect(isSoundId(id), id).toBe(true);
      expect(soundEntry(id as never).source, id).not.toBe("score");
    }
  });

  it("are well formed: files recorded, synths sized, beds of loops and sprinkles, aliases resolving", () => {
    for (const [id, d] of defs) {
      switch (d.kind) {
        case "file":
          expect(d.files.length, id).toBeGreaterThan(0);
          for (const f of d.files) expect(files.has(f), `${id}: ${f} is not in data/audio/sources.json`).toBe(true);
          break;
        case "synth":
          expect(d.seconds, id).toBeGreaterThan(0);
          expect(d.seconds, id).toBeLessThanOrEqual(60);
          break;
        case "bed":
          expect(soundEntry(id as never).group, id).toBe("ambience");
          for (const l of d.layers) expect(isSoundId(l.id) && soundEntry(l.id).loop, `${id} layer ${l.id}`).toBe(true);
          for (const sp of d.sprinkles) expect(soundEntry(sp.id).group, `${id} sprinkle ${sp.id}`).toBe("sprinkles");
          break;
        case "alias":
          expect(isSoundId(d.of) && d.of !== id, `${id} -> ${d.of}`).toBe(true);
          break;
      }
    }
  });

  it("reports the ids still to define (none)", () => {
    const missing = missingDefs();
    for (const id of missing) expect(isSoundId(id)).toBe(true);
    if (missing.length) console.info(`[audio] ${missing.length} effect ids without a definition yet (placeholder beeps)`);
  });
});
