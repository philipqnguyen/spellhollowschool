import { describe, expect, it } from "vitest";
import { MUSIC_BRIEF, STINGER_BRIEF } from "./brief";
import { contour, imitates, pitchesOf, type FamousOpening } from "./motifs";
import type { Score } from "./score";
import { INSTRUMENTS } from "./score";
import { SOUNDS } from "../catalogue";

// Every score (music/scores/<name>.ts, default export).
const scores = Object.values(import.meta.glob<Score>("./scores/*.ts", { eager: true, import: "default" }));

// A made-up opening for the tests (no real tune), as semitones.
const TEST_TUNES: readonly FamousOpening[] = [{ name: "test opening", intervals: [5, 3, -1, -2, 7, -2, -3] }];

describe("the originality guard", () => {
  it("reads notes as the tune: chords by their top note, repeats and rests skipped", () => {
    expect(pitchesOf("c4 e4:2 . g4+c5 ~:2 a4!:.5*3 bf3? fs4")).toEqual([60, 64, 72, 69, 69, 69, 58, 66]);
    expect(pitchesOf("c4", 1)).toEqual([72]);
    expect(contour([60, 60, 62, 62, 59])).toEqual([2, -3]);
  });

  it("finds a listed opening in any key and rhythm", () => {
    // C F Ab G F C: the opening's first five intervals, with held and restruck notes.
    expect(imitates(pitchesOf("c4 f4:1.5 f4 af4:.5 g4 f4:2 c5"), TEST_TUNES)).toEqual({ tune: "test opening", at: 0, how: "exact" });
    // The same run in another key, after a lead-in.
    expect(imitates([50, 52, 54, 59, 62, 61, 59, 66], TEST_TUNES)?.tune).toBe("test opening");
  });

  it("leaves ordinary tunes alone", () => {
    expect(imitates(pitchesOf("c4 d4 e4 f4 g4 a4 b4 c5 b4 a4 g4 f4 e4 d4 c4"))).toBeNull();
    expect(imitates(pitchesOf("e4 g4 a4 b4 a4 g4 e4 d4 e4 g4 e4 d4 b3"))).toBeNull();
    expect(imitates(pitchesOf("e4 g4 a4 b4 a4 g4 e4 d4 e4 g4 e4 d4 b3"), TEST_TUNES)).toBeNull();
  });

  it("no score's melodic line follows a famous opening", () => {
    for (const s of scores) {
      for (const [name, section] of Object.entries(s.sections)) {
        for (const p of section.parts) {
          const hit = imitates(pitchesOf(p.notes, p.octave ?? 0));
          expect(hit, `${s.id} section ${name} (${p.inst}) ${hit?.how}ly follows ${hit?.tune}`).toBeNull();
        }
      }
    }
  });
});

describe("the music brief", () => {
  it("briefs every music and stinger id, with instruments of the palette", () => {
    const music = SOUNDS.filter((s) => s.group === "music").map((s) => s.id).sort();
    const stingers = SOUNDS.filter((s) => s.group === "stingers").map((s) => s.id).sort();
    expect(Object.keys(MUSIC_BRIEF).sort()).toEqual(music);
    expect(Object.keys(STINGER_BRIEF).sort()).toEqual(stingers);
    for (const b of Object.values(MUSIC_BRIEF)) {
      for (const i of b.palette) expect(INSTRUMENTS).toContain(i);
      expect(b.bpm[0]).toBeLessThanOrEqual(b.bpm[1]);
    }
  });

  it("every score is for a music or stinger id, at most one each", () => {
    const ids = scores.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const s of scores) {
      expect(SOUNDS.find((x) => x.id === s.id)?.source).toBe("score");
      for (const f of s.form) expect(s.sections[f], `${s.id}: form names ${f}`).toBeDefined();
    }
  });
});
