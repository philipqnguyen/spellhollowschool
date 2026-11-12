import { describe, expect, it } from "vitest";
import { isRetryable, KeysetPager, RetryKeys, type Page } from "./paging";

/** Three pages of ids 1..7 with opaque cursors, as a server would answer. */
function serve(after: string | null): Page<{ id: number }> {
  const pages: Record<string, Page<{ id: number }>> = {
    "": { items: [{ id: 1 }, { id: 2 }, { id: 3 }], next: "c3" },
    c3: { items: [{ id: 4 }, { id: 5 }, { id: 6 }], next: "c6" },
    c6: { items: [{ id: 7 }], next: null },
  };
  return pages[after ?? ""]!;
}

function go(p: KeysetPager<{ id: number }>, move: "first" | "next" | "prev" | "reload"): boolean {
  const req = p.begin(move);
  if (!req) return false;
  return p.receive(req.ticket, serve(req.after));
}

describe("KeysetPager", () => {
  it("walks forward and back by the cursors that opened each page", () => {
    const p = new KeysetPager<{ id: number }>();
    expect(p.loaded).toBe(false);
    go(p, "first");
    expect(p.items.map((i) => i.id)).toEqual([1, 2, 3]);
    expect(p.hasPrev).toBe(false);
    expect(p.hasNext).toBe(true);
    expect(p.label()).toBe("Page 1");

    go(p, "next");
    expect(p.items.map((i) => i.id)).toEqual([4, 5, 6]);
    expect(p.page).toBe(1);
    go(p, "next");
    expect(p.items.map((i) => i.id)).toEqual([7]);
    expect(p.hasNext).toBe(false);
    expect(p.label()).toBe("Page 3 of 3");
    expect(go(p, "next")).toBe(false); // nowhere to go

    go(p, "prev");
    expect(p.items.map((i) => i.id)).toEqual([4, 5, 6]);
    go(p, "prev");
    expect(p.items.map((i) => i.id)).toEqual([1, 2, 3]);
    expect(go(p, "prev")).toBe(false);
  });

  it("asks a page again by the cursor that opened it", () => {
    const p = new KeysetPager<{ id: number }>();
    go(p, "first");
    go(p, "next");
    const req = p.begin("reload")!;
    expect(req.after).toBe("c3");
    p.receive(req.ticket, { items: [{ id: 5 }, { id: 6 }, { id: 7 }], next: null });
    expect(p.page).toBe(1);
    expect(p.hasNext).toBe(false);
  });

  it("ignores a reply that a newer request overtook", () => {
    const p = new KeysetPager<{ id: number }>();
    go(p, "first");
    const slow = p.begin("next")!;
    const fresh = p.begin("first")!; // the filters changed meanwhile
    expect(p.receive(slow.ticket, serve(slow.after))).toBe(false);
    expect(p.receive(fresh.ticket, serve(fresh.after))).toBe(true);
    expect(p.page).toBe(0);
    expect(p.items.map((i) => i.id)).toEqual([1, 2, 3]);
  });

  it("keeps the page shown when a request fails", () => {
    const p = new KeysetPager<{ id: number }>();
    go(p, "first");
    const req = p.begin("next")!;
    expect(p.loading).toBe(true);
    expect(p.fail(req.ticket)).toBe(true);
    expect(p.loading).toBe(false);
    expect(p.page).toBe(0);
    expect(p.items.map((i) => i.id)).toEqual([1, 2, 3]);
    // And the next attempt still goes to page 2.
    go(p, "next");
    expect(p.items.map((i) => i.id)).toEqual([4, 5, 6]);
  });

  it("starts over on `first`", () => {
    const p = new KeysetPager<{ id: number }>();
    go(p, "first");
    go(p, "next");
    const req = p.begin("first")!;
    expect(req.after).toBeNull();
    expect(p.loaded).toBe(false);
    expect(p.items).toEqual([]);
  });

  it("updates and removes entries on the page shown", () => {
    const p = new KeysetPager<{ id: number; read?: boolean }>();
    const req = p.begin("first")!;
    p.receive(req.ticket, { items: [{ id: 1 }, { id: 2 }], next: null });
    p.update((i) => i.id === 2, (i) => ({ ...i, read: true }));
    expect(p.items[1]).toEqual({ id: 2, read: true });
    p.remove((i) => i.id === 1);
    expect(p.items.map((i) => i.id)).toEqual([2]);
  });
});

describe("RetryKeys", () => {
  it("keeps an action's id until it settles", () => {
    let n = 0;
    const keys = new RetryKeys(() => `id${++n}`);
    expect(keys.get("buy:7")).toBe("id1");
    expect(keys.get("buy:7")).toBe("id1"); // a retry of the same purchase
    expect(keys.get("buy:8")).toBe("id2");
    keys.settle("buy:7");
    expect(keys.get("buy:7")).toBe("id3"); // a new purchase
  });

  it("retries only unanswered or failed-on-the-server requests", () => {
    expect(isRetryable(0)).toBe(true);
    expect(isRetryable(503)).toBe(true);
    expect(isRetryable(409)).toBe(false);
    expect(isRetryable(400)).toBe(false);
  });
});
