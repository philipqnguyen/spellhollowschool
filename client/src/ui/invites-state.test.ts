import { describe, expect, it } from "vitest";
import { InviteQueue, MAX_INVITES, formatCountdown, secondsLeft } from "./invites-state";

describe("InviteQueue", () => {
  it("expires invitations after their time", () => {
    const q = new InviteQueue();
    q.add(1, "Basil", 120, 0);
    q.add(2, "Alina", 30, 1000);
    expect(q.expire(30_999).map((i) => i.inviteId)).toEqual([]);
    expect(q.expire(31_000).map((i) => i.inviteId)).toEqual([2]);
    expect(q.items.map((i) => i.inviteId)).toEqual([1]);
  });

  it("keeps one invitation per person, the newest", () => {
    const q = new InviteQueue();
    q.add(1, "Basil", 120, 0);
    q.add(2, "Basil", 120, 5000);
    expect(q.items.map((i) => i.inviteId)).toEqual([2]);
  });

  it("caps the number shown", () => {
    const q = new InviteQueue();
    for (let i = 0; i < MAX_INVITES + 2; i++) q.add(i, `P${i}`, 120, 0);
    expect(q.items.length).toBe(MAX_INVITES);
    expect(q.items[0]!.inviteId).toBe(2);
  });

  it("removes an answered invitation", () => {
    const q = new InviteQueue();
    q.add(7, "Basil", 120, 0);
    expect(q.remove(7)?.fromName).toBe("Basil");
    expect(q.remove(7)).toBeUndefined();
    expect(q.items).toEqual([]);
  });

  it("counts down in whole seconds", () => {
    const q = new InviteQueue();
    const inv = q.add(1, "Basil", 120, 0);
    expect(secondsLeft(inv, 0)).toBe(120);
    expect(secondsLeft(inv, 500)).toBe(120);
    expect(secondsLeft(inv, 119_001)).toBe(1);
    expect(secondsLeft(inv, 200_000)).toBe(0);
    expect(formatCountdown(120)).toBe("2:00");
    expect(formatCountdown(42)).toBe("0:42");
  });
});
