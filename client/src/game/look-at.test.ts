// Characters look at their target (look-at.ts): whom our character looks at
// and what the server is told, remote looks from snapshots, NPCs' picks.

import { describe, expect, it } from "vitest";
import type { ServerMessage } from "../wasm/sim_wasm";
import { chooseOwnLook, LookDirector, LookSender, npcLook, NPC_KEEP_M, NPC_NEAR_M, RemoteLooks, type Drawn, type OwnLookContext } from "./look-at";

const none: OwnLookContext = { duelOpponent: null, spellAim: null, tradePartner: null, selected: null };

describe("chooseOwnLook", () => {
  const all = () => true;
  it("follows the priority: duel opponent, spell aim, trade partner, selection", () => {
    const aim = { x: 1, y: 2, z: 3 };
    expect(chooseOwnLook({ duelOpponent: 7, spellAim: aim, tradePartner: 8, selected: 9 }, all)).toEqual({ target: 7, point: null, shared: 7 });
    expect(chooseOwnLook({ ...none, spellAim: aim, tradePartner: 8, selected: 9 }, all)).toEqual({ target: -1, point: aim, shared: -1 });
    expect(chooseOwnLook({ ...none, tradePartner: 8, selected: 9 }, all)).toEqual({ target: 8, point: null, shared: 8 });
    expect(chooseOwnLook({ ...none, selected: 9 }, all)).toEqual({ target: 9, point: null, shared: 9 });
    expect(chooseOwnLook(none, all)).toEqual({ target: -1, point: null, shared: -1 });
  });

  it("skips who cannot be seen (not drawn, invisible, too far)", () => {
    const seen = (id: number) => id === 9;
    expect(chooseOwnLook({ ...none, duelOpponent: 7, tradePartner: 8, selected: 9 }, seen).target).toBe(9);
    expect(chooseOwnLook({ ...none, selected: 4 }, seen).target).toBe(-1);
  });
});

describe("LookSender", () => {
  it("sends each change once, at most four a second, the last change always following", () => {
    const s = new LookSender(4);
    expect(s.update(0, -1)).toBeNull(); // the server already holds none
    expect(s.update(0, 5)).toBe(5);
    expect(s.update(10, 5)).toBeNull();
    // Changes within 250 ms wait...
    expect(s.update(100, 6)).toBeNull();
    expect(s.update(200, 7)).toBeNull();
    // ...and the newest goes out once the gap has passed.
    expect(s.update(250, 7)).toBe(7);
    expect(s.update(600, -1)).toBe(-1);
    // A flicker back to what was sent sends nothing.
    expect(s.update(700, 3)).toBeNull();
    expect(s.update(800, -1)).toBeNull();
    expect(s.update(2000, -1)).toBeNull();
    s.reset();
    expect(s.update(2001, 4)).toBe(4);
  });

  it("never sends more than four a second however often the target changes", () => {
    const s = new LookSender(4);
    let sent = 0;
    for (let t = 0; t < 10_000; t += 16) if (s.update(t, Math.floor(t / 30) % 5) !== null) sent++;
    expect(sent).toBeLessThanOrEqual(41);
    expect(sent).toBeGreaterThan(30);
  });
});

describe("npcLook", () => {
  const npc = { x: 0, y: 0, z: 0 };
  it("turns to the nearest student within a few metres, else nobody", () => {
    expect(npcLook(npc, [{ id: 1, x: 3, y: 0, z: 0 }, { id: 2, x: 2, y: 0, z: 0 }], -1, -1, 20)).toBe(2);
    expect(npcLook(npc, [{ id: 1, x: NPC_NEAR_M + 1, y: 0, z: 0 }], -1, -1, 20)).toBe(-1);
  });
  it("keeps the student it looks at until someone is clearly nearer or they walk off", () => {
    const a = { id: 1, x: 2, y: 0, z: 0 };
    const b = { id: 2, x: 1.6, y: 0, z: 0 };
    expect(npcLook(npc, [a, b], 1, -1, 20)).toBe(1);
    b.x = 1;
    expect(npcLook(npc, [a, b], 1, -1, 20)).toBe(2);
    a.x = NPC_KEEP_M - 0.1;
    expect(npcLook(npc, [a], 1, -1, 20)).toBe(1);
    a.x = NPC_KEEP_M + 0.1;
    expect(npcLook(npc, [a], 1, -1, 20)).toBe(-1);
  });
  it("looks at the student who engages it (selected it, answered in class) across the room", () => {
    const front = { id: 1, x: 1, y: 0, z: 0 };
    const back = { id: 2, x: 12, y: 0, z: 0 };
    expect(npcLook(npc, [front, back], 1, 2, 20)).toBe(2);
    // Out of range it falls back to the nearest.
    back.x = 25;
    expect(npcLook(npc, [front, back], 1, 2, 20)).toBe(1);
  });
});

type Snapshot = Extract<ServerMessage, { type: "snapshot" }>;
function snap(over: Partial<Snapshot>): Snapshot {
  return { type: "snapshot", tick: 1, entered: [], states: [], left: [], looks: [], ...over };
}

describe("RemoteLooks", () => {
  it("keeps what the server says until a change, a re-entry or a departure", () => {
    const r = new RemoteLooks();
    r.snapshot(snap({ looks: [{ id: 3, target: 9 }] }));
    expect(r.of(3)).toBe(9);
    r.snapshot(snap({}));
    expect(r.of(3)).toBe(9);
    r.snapshot(snap({ looks: [{ id: 3, target: null }] }));
    expect(r.of(3)).toBe(-1);
    r.snapshot(snap({ looks: [{ id: 3, target: 4 }] }));
    r.snapshot(snap({ left: [3] }));
    expect(r.of(3)).toBe(-1);
    r.snapshot(snap({ looks: [{ id: 5, target: 4 }] }));
    r.clear();
    expect(r.of(5)).toBe(-1);
  });
});

describe("LookDirector", () => {
  function setup(over: Partial<{ own: OwnLookContext; hidden: number[]; npcs: number[]; engages: number[]; npcAt: [number, number, number] }> = {}) {
    const sent: (number | null)[] = [];
    const [nx, ny, nz] = over.npcAt ?? [0, 0, 2];
    const drawn: Drawn[] = [
      { id: 2, pose: { x: 3, y: 0, z: 0 } },
      { id: 3, pose: { x: 30, y: 0, z: 0 } },
      { id: 50, pose: { x: nx, y: ny, z: nz } },
    ];
    const ctx = { own: over.own ?? none };
    const d = new LookDirector(
      {
        selfId: () => 1,
        self: () => ({ x: 0, y: 0, z: 0 }),
        drawn: () => drawn,
        isNpc: (id) => (over.npcs ?? [50]).includes(id),
        hidden: (id) => (over.hidden ?? []).includes(id),
        own: () => ctx.own,
        engages: (id) => (over.engages ?? []).includes(id),
        send: (t) => sent.push(t),
      },
      20,
      4,
    );
    return { d, sent, ctx };
  }

  it("looks at our selection at once and tells the server once", () => {
    const { d, sent, ctx } = setup({ own: { ...none, selected: 2 } });
    d.frame(0);
    expect(d.targetOf(1)).toBe(2);
    d.frame(16);
    expect(sent).toEqual([2]);
    // Too far (30 m): no look, and the server hears none.
    ctx.own = { ...none, selected: 3 };
    d.frame(500);
    expect(d.targetOf(1)).toBe(-1);
    expect(sent).toEqual([2, null]);
  });

  it("never looks at an invisible character", () => {
    const { d, sent } = setup({ own: { ...none, selected: 2 }, hidden: [2] });
    d.frame(0);
    expect(d.targetOf(1)).toBe(-1);
    expect(sent).toEqual([]);
  });

  it("aims at a point for the spell, telling the server nobody", () => {
    const { d, sent } = setup({ own: { ...none, selected: 2, spellAim: { x: 1, y: 1, z: 1 } } });
    d.frame(0);
    const p = { x: 0, y: 0, z: 0 };
    expect(d.pointOf(1, p)).toBe(true);
    expect(p).toEqual({ x: 1, y: 1, z: 1 });
    expect(d.pointOf(2, p)).toBe(false);
    expect(sent).toEqual([]);
  });

  it("turns the professor to the nearest student, or to us when we engage it", () => {
    const near = setup();
    near.d.frame(0);
    // We stand 2 m from the NPC, student 2 about 3.6 m.
    expect(near.d.targetOf(50)).toBe(1);
    // At the front of a classroom, 1 m from student 2 and 3.2 m from us: student 2...
    const front = setup({ npcAt: [3, 0, 1] });
    front.d.frame(0);
    expect(front.d.targetOf(50)).toBe(2);
    // ...unless we have it selected or just answered in its class.
    const engaged = setup({ npcAt: [3, 0, 1], engages: [50] });
    engaged.d.frame(0);
    expect(engaged.d.targetOf(50)).toBe(1);
  });

  it("shows remote looks from snapshots and forgets them on a reset", () => {
    const { d } = setup();
    d.snapshot(snap({ looks: [{ id: 2, target: 1 }] }));
    expect(d.targetOf(2)).toBe(1);
    d.reset();
    expect(d.targetOf(2)).toBe(-1);
  });
});
