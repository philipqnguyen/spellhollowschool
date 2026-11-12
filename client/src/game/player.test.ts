import { describe, expect, it } from "vitest";
import { MoveSender } from "./player";

describe("MoveSender", () => {
  it("sends at the move rate with increasing seq while moving", () => {
    const s = new MoveSender(10);
    const seqs: number[] = [];
    let x = 0;
    for (let frame = 0; frame < 60; frame++) {
      x += 0.05; // moving every frame at 60 fps for 1 s
      const m = s.tick(1 / 60, x, 0, 0, 0, "walk");
      if (m) seqs.push(m.seq);
    }
    expect(seqs.length).toBeGreaterThanOrEqual(9);
    expect(seqs.length).toBeLessThanOrEqual(10);
    expect(seqs).toEqual(seqs.map((_, i) => i + 1));
  });

  it("only sends a slow heartbeat while standing still", () => {
    const s = new MoveSender(10, 1);
    let sent = 0;
    for (let frame = 0; frame < 300; frame++) if (s.tick(1 / 60, 1, 0, 1, 0, "idle")) sent++;
    // The first report, then about one per second for 5 s.
    expect(sent).toBeGreaterThanOrEqual(5);
    expect(sent).toBeLessThanOrEqual(6);
  });

  it("does not burst after a long stall", () => {
    const s = new MoveSender(10);
    expect(s.tick(5, 1, 0, 0, 0, "walk")).not.toBeNull();
    expect(s.tick(0.001, 2, 0, 0, 0, "walk")).not.toBeNull(); // the one carried-over interval
    expect(s.tick(0.001, 3, 0, 0, 0, "walk")).toBeNull();
  });

  it("ignores corrections for moves sent before the last snap", () => {
    const s = new MoveSender(10);
    for (let i = 1; i <= 5; i++) s.tick(0.1, i, 0, 0, 0, "walk");
    expect(s.lastSeq).toBe(5);
    expect(s.acceptCorrection(3)).toBe(true); // snap; moves 4 and 5 were already in flight
    expect(s.acceptCorrection(4)).toBe(false);
    expect(s.acceptCorrection(5)).toBe(false);
    s.tick(0.1, 0, 0, 0, 0, "walk");
    expect(s.acceptCorrection(6)).toBe(true);
  });

  it("restarts seq for a new connection", () => {
    const s = new MoveSender(10);
    s.tick(0.1, 1, 0, 0, 0, "walk");
    s.reset();
    expect(s.tick(0.1, 2, 0, 0, 0, "walk")?.seq).toBe(1);
  });
});
