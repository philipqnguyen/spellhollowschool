import { describe, expect, it } from "vitest";
import type { ClassInfo, LessonBeat } from "../../wasm/sim_wasm";
import { APPROACH_MS, classScript, LessonState, TRACE_HOLD_MS } from "./lesson";
import classFiles, { PROFESSORS } from "./class-files";

const info = (over: Partial<ClassInfo> = {}): ClassInfo => ({
  class: "charms",
  session: 64 * 3 + 0,
  section: 1,
  startUnixMs: 1_000_000,
  joinUntilUnixMs: 1_180_000,
  endUnixMs: 1_540_000,
  professor: 77,
  phase: { kind: "gathering" },
  phaseUntilUnixMs: 1_185_000,
  ...over,
});

const beats = (t0: number, ...gaps: number[]): LessonBeat[] => gaps.map((g, i) => ({ beat: i, prompt: { kind: "trace", gesture: 1 + (i % 2) }, atUnixMs: t0 + g }));

function practising(windowMs = 600): LessonState {
  const l = new LessonState("charms");
  l.join(info());
  l.setPhase({ kind: "practice", round: 1 }, 2_000_000);
  l.startRound(1, windowMs, beats(10_000, 0, 3000, 6000));
  return l;
}

describe("the class files", () => {
  it("ship the Charms lesson with its professor and four practice rounds", () => {
    const s = classScript("charms")!;
    expect(s.professorName.length).toBeGreaterThan(0);
    expect(s.rounds.map((r) => r.round)).toEqual([1, 2, 3, 4]);
    expect(s.rounds.every((r) => r.beats > 0 && r.beats <= 32)).toBe(true);
    expect(PROFESSORS.get(classFiles.charms!.professor.id)).toBe("charms");
    // The timetable and gestures sit in the same directory but are no classes.
    expect(Object.keys(classFiles)).not.toContain("gestures");
  });
});

describe("following a lesson", () => {
  it("shows no beat outside practice", () => {
    const l = new LessonState("charms");
    l.join(info());
    l.startRound(1, 600, beats(10_000, 0));
    expect(l.practising).toBe(false);
    expect(l.current(10_000)).toBeNull();
    expect(l.beatFor(10_000)).toBeNull();
  });

  it("matches a trace to the nearest untried beat within the window", () => {
    const l = practising();
    expect(l.beatFor(10_000 - 601)).toBeNull(); // too early
    expect(l.beatFor(10_000 - 600)?.beat).toBe(0);
    expect(l.beatFor(10_400)?.beat).toBe(0);
    expect(l.beatFor(11_000)).toBeNull(); // between beats, outside both windows
    expect(l.beatFor(12_500)?.beat).toBe(1);
    const b0 = l.beatFor(10_050)!;
    l.sent(b0, { score: 80, grade: "great", rejected: null });
    // Once tried, a beat is not offered again.
    expect(l.beatFor(10_100)).toBeNull();
  });

  it("picks the nearer beat when windows overlap", () => {
    const l = new LessonState("charms");
    l.join(info());
    l.setPhase({ kind: "practice", round: 2 }, 2_000_000);
    l.startRound(2, 600, beats(10_000, 0, 1000));
    expect(l.beatFor(10_400)?.beat).toBe(0);
    expect(l.beatFor(10_600)?.beat).toBe(1);
  });

  it("moves on to the next beat as windows close, marking the untraced ones missed", () => {
    const l = practising();
    expect(l.current(9_000)?.beat).toBe(0);
    expect(l.current(10_600)?.beat).toBe(0);
    expect(l.current(10_601)?.beat).toBe(1);
    expect(l.expire(10_601).map((b) => b.beat)).toEqual([0]);
    expect(l.expire(10_700)).toEqual([]);
    expect(l.current(20_000)).toBeNull();
  });

  it("keeps a beat open while its stroke is still being drawn", () => {
    const l = practising();
    // A careful swish started on the beat outlasts the 600 ms window.
    const b0 = l.beatFor(10_000)!;
    expect(l.expire(11_200, b0)).toEqual([]);
    expect(b0.status).toBe("upcoming");
    l.sent(b0, { score: 80, grade: "great", rejected: null });
    expect(l.expire(11_300)).toEqual([]);
    expect(b0.status).toBe("sent");
  });

  it("takes the server's scores and sums the lesson up", () => {
    const l = practising();
    const [b0, b1] = [l.beatFor(10_000)!, l.beatFor(13_000)!];
    l.sent(b0, { score: 70, grade: "great", rejected: null });
    l.sent(b1, { score: 50, grade: "good", rejected: null });
    expect(l.scored(1, 0, { score: 90, grade: "perfect", rejected: null })?.status).toBe("scored");
    l.scored(1, 1, { score: 0, grade: "miss", rejected: "too_fast" });
    expect(l.scored(9, 0, { score: 1, grade: "miss", rejected: null })).toBeNull();
    l.expire(99_999);
    expect(l.summary()).toEqual({ beats: 3, answered: 2, scored: 1, average: 90, perfect: 1 });
  });

  it("replaces a round sent again rather than counting it twice", () => {
    const l = practising();
    l.startRound(1, 600, beats(10_000, 0, 3000, 6000));
    expect(l.summary().beats).toBe(3);
    expect(l.roundsTotal).toBe(4);
  });
  it("ships tracing rounds with time to finish each trace before the next ring", () => {
    for (const id of ["charms", "defence", "astronomy"]) {
      for (const step of classFiles[id]!.lesson.filter((a) => a.kind === "practice")) {
        const at = step.beats!.map((b) => b.at_ms!);
        expect(at.length, `${id} round ${step.round}`).toBeGreaterThanOrEqual(5);
        for (let i = 1; i < at.length; i++) expect(at[i]! - at[i - 1]!, `${id} round ${step.round}`).toBeGreaterThanOrEqual(TRACE_HOLD_MS + APPROACH_MS);
      }
    }
  });
});

describe("the pad's gesture (a trace's time to finish)", () => {
  // Beats 4.8 s apart, as Charms: beat 1's ring starts closing 0.2 s after beat 0's 3 s are over.
  function round(): LessonState {
    const l = new LessonState("charms");
    l.join(info());
    l.setPhase({ kind: "practice", round: 1 }, 2_000_000);
    l.startRound(1, 600, beats(10_000, 0, 4800, 9600));
    return l;
  }

  it("shows the first beat's countdown, then each next gesture only as its ring starts closing", () => {
    const l = round();
    expect(l.shown(5_000)?.beat).toBe(0);
    expect(l.shown(10_000)?.beat).toBe(0);
    // Untraced: beat 0 stays past its window and its 3 s, until beat 1's approach.
    expect(l.expire(10_601).map((b) => b.beat)).toEqual([0]);
    expect(l.shown(10_601)?.beat).toBe(0);
    expect(l.shown(13_199)?.beat).toBe(0);
    expect(l.shown(14_800 - APPROACH_MS)?.beat).toBe(1);
    // The last beat holds its 3 s, then the round is through.
    expect(l.shown(19_600 + TRACE_HOLD_MS)?.beat).toBe(2);
    expect(l.shown(19_600 + TRACE_HOLD_MS + 1)).toBeNull();
  });

  it("keeps the gesture while a stroke is drawn past its window, within 3 s", () => {
    const l = round();
    const b0 = l.beatFor(10_100)!;
    // A careful swish: still drawing 2.9 s after the beat, long after the window.
    for (const t of [10_601, 11_500, 12_900, 13_000]) {
      expect(l.expire(t, b0)).toEqual([]);
      expect(l.shown(t)).toBe(b0);
      expect(l.cutDue(t, b0)).toBe(false);
    }
  });

  it("lets the pad move on once the stroke is lifted and sent", () => {
    // Beats closer than the hold plus the approach (no shipped class has them): the next ring may start within 3 s.
    const l = new LessonState("charms");
    l.join(info());
    l.setPhase({ kind: "practice", round: 1 }, 2_000_000);
    l.startRound(1, 600, beats(10_000, 0, 3500));
    const b0 = l.beatFor(10_000)!;
    // Still drawing: the map stays though beat 1's approach (11 900) has begun.
    expect(l.shown(12_000)).toBe(b0);
    // Pointer up: sent, and the next gesture comes up straight away.
    l.sent(b0, { score: 80, grade: "great", rejected: null });
    expect(l.shown(12_000)?.beat).toBe(1);
    expect(l.cutDue(12_000, b0)).toBe(false);
    // Lifted before the next approach: beat 0 stays up, done, until it starts.
    const m = round();
    const c0 = m.beatFor(10_000)!;
    m.sent(c0, { score: 80, grade: "great", rejected: null });
    expect(m.shown(11_000)).toBe(c0);
    expect(m.shown(14_800 - APPROACH_MS)?.beat).toBe(1);
  });

  it("switches at 3 s, cutting off a stroke still being drawn", () => {
    const l = new LessonState("charms");
    l.join(info());
    l.setPhase({ kind: "practice", round: 1 }, 2_000_000);
    l.startRound(1, 600, beats(10_000, 0, 4600));
    const b0 = l.beatFor(10_200)!;
    expect(l.cutDue(13_000, b0)).toBe(false);
    // 3 s after the beat (beat 1's ring starts closing): the stroke is cut and the pad moves on.
    expect(l.cutDue(13_001, b0)).toBe(true);
    expect(l.shown(13_001)?.beat).toBe(1);
    // The cut stroke is sent for its own beat (the controller's traceEnd)...
    l.sent(b0, { score: 40, grade: "good", rejected: null });
    expect(l.cutDue(13_001, b0)).toBe(false);
    expect(b0.status).toBe("sent");
    // ...or, too little to score, its beat goes by as missed.
    const m = round();
    const c0 = m.beatFor(10_000)!;
    expect(m.cutDue(13_001, c0)).toBe(true);
    expect(m.expire(13_001).map((b) => b.beat)).toEqual([0]);
  });

});
