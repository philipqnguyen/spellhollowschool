import { describe, expect, it } from "vitest";
import type { OfferedItem, TradeOffer, TradeView } from "../wasm/sim_wasm";
import { changedLines, errorText, offerProblem, outcomeText, sameOffer, TradeSession, withCrowns, withLine, withoutLine } from "./trade-state";

const item = (itemId: number, quantity = 1, itemDef = 3001): OfferedItem => ({ itemId, itemDef, dyes: [1, 2, 3], finish: "none", quantity, maker: null });
const offer = (items: OfferedItem[] = [], crowns = 0): TradeOffer => ({ items, crowns });

function view(over: Partial<TradeView> = {}): TradeView {
  return {
    tradeId: 9,
    partner: 42,
    partnerName: "Basil",
    rev: 1,
    mine: offer(),
    theirs: offer(),
    mineLocked: false,
    theirsLocked: false,
    mineConfirmed: false,
    theirsConfirmed: false,
    ...over,
  };
}

describe("requests", () => {
  it("refuses to ask yourself, or while busy", () => {
    const s = new TradeSession();
    expect(s.canRequest(5, 5)).toMatch(/yourself/);
    s.requested(42, "Basil", 0, 30);
    expect(s.canRequest(43, 5)).toMatch(/Basil/);
    s.onState(view());
    expect(s.canRequest(43, 5)).toMatch(/Finish/);
  });

  it("keeps one incoming request per person, at most three", () => {
    const s = new TradeSession();
    s.onRequested(1, 100, "A", 30, 0);
    s.onRequested(2, 100, "A", 30, 0); // A again: replaces
    s.onRequested(3, 101, "B", 30, 0);
    s.onRequested(4, 102, "C", 30, 0);
    s.onRequested(5, 103, "D", 30, 0);
    expect(s.incoming.map((r) => r.tradeId)).toEqual([3, 4, 5]);
    expect(s.responded(4)?.fromName).toBe("C");
    expect(s.incoming.map((r) => r.tradeId)).toEqual([3, 5]);
  });

  it("lets requests lapse", () => {
    const s = new TradeSession();
    s.onRequested(1, 100, "A", 30, 0);
    s.requested(42, "Basil", 0, 30);
    expect(s.expire(29_000).incoming).toEqual([]);
    const gone = s.expire(33_000);
    expect(gone.incoming.map((r) => r.tradeId)).toEqual([1]);
    expect(gone.outgoing?.targetName).toBe("Basil");
    expect(s.outgoing).toBeNull();
  });

  it("learns that our request was declined from a result for a trade it never saw", () => {
    const s = new TradeSession();
    s.requested(42, "Basil", 0, 30);
    expect(s.onResult(77, { kind: "declined" })).toBe("Basil declined to trade.");
    expect(s.outgoing).toBeNull();
  });

  it("learns our request's id from the server's echo, and matches its end by it", () => {
    const s = new TradeSession();
    s.requested(42, "Basil", 0, 30);
    expect(s.outgoing?.tradeId).toBeNull();
    s.acknowledged(77, 30, 100);
    expect(s.outgoing?.tradeId).toBe(77);
    expect(s.onResult(78, { kind: "declined" })).toBeNull(); // someone else's
    expect(s.onResult(77, { kind: "cancelled" })).toBe("Your trade request to Basil was withdrawn.");
    expect(s.outgoing).toBeNull();
  });

  it("forgets a refused request", () => {
    const s = new TradeSession();
    s.requested(42, "Basil", 0, 30);
    expect(s.onError(undefined, "too_far")).toMatch(/closer/);
    expect(s.outgoing).toBeNull();
  });
});

describe("the window", () => {
  it("opens on the first state and clears the request", () => {
    const s = new TradeSession();
    s.requested(42, "Basil", 0, 30);
    s.onState(view());
    expect(s.open).toBe(true);
    expect(s.outgoing).toBeNull();
    const st = s.status()!;
    expect(st.step).toBe("editing");
    expect(st.canEdit && st.canLock).toBe(true);
    expect(st.canConfirm).toBe(false);
  });

  it("walks editing → review → waiting → completing", () => {
    const s = new TradeSession();
    s.onState(view());
    s.onState(view({ rev: 2, mineLocked: true }));
    let st = s.status()!;
    expect(st.step).toBe("editing");
    expect(st.mine).toBe("locked");
    expect(st.canEdit).toBe(false);
    expect(st.canUnlock).toBe(true);
    expect(st.canConfirm).toBe(false);
    expect(st.headline).toMatch(/Waiting for Basil to lock/);

    s.onState(view({ rev: 3, mineLocked: true, theirsLocked: true }));
    st = s.status()!;
    expect(st.step).toBe("review");
    expect(st.canConfirm).toBe(true);

    s.sent("confirm");
    expect(s.status()!.canConfirm).toBe(false); // no double confirm while in flight
    s.onState(view({ rev: 3, mineLocked: true, theirsLocked: true, mineConfirmed: true }));
    st = s.status()!;
    expect(st.step).toBe("waiting");
    expect(st.mine).toBe("confirmed");
    expect(st.canConfirm).toBe(false);

    s.onState(view({ rev: 3, mineLocked: true, theirsLocked: true, mineConfirmed: true, theirsConfirmed: true }));
    st = s.status()!;
    expect(st.step).toBe("completing");
    expect(st.canUnlock).toBe(false);
  });

  it("says why both confirmations were reset, and marks what changed", () => {
    const s = new TradeSession();
    s.onState(view({ rev: 3, mineLocked: true, theirsLocked: true, theirs: offer([item(5)], 10), mineConfirmed: true }));
    expect(s.resetReason).toBeNull();
    // Basil unlocks (rev 4, confirmations cleared) ...
    s.onState(view({ rev: 4, mineLocked: true, theirs: offer([item(5)], 10) }));
    expect(s.resetReason).toMatch(/Basil unlocked their offer/);
    expect(s.resetReason).toMatch(/reset/);
    // ... changes the offer and locks again: the reason stays, the new line is marked.
    s.onState(view({ rev: 5, mineLocked: true, theirs: offer([item(5), item(6, 3)], 25) }));
    expect(s.changedTheirs.has("6:3")).toBe(true);
    expect(s.changedTheirs.has("crowns")).toBe(true);
    expect(s.changedTheirs.has("5:1")).toBe(false);
    s.onState(view({ rev: 6, mineLocked: true, theirsLocked: true, theirs: offer([item(5), item(6, 3)], 25) }));
    expect(s.changedTheirs.has("6:3")).toBe(true); // a lock alone keeps the marks
    expect(s.resetReason).toMatch(/unlocked/);
    // Confirming what we saw clears both.
    s.onState(view({ rev: 6, mineLocked: true, theirsLocked: true, mineConfirmed: true, theirs: offer([item(5), item(6, 3)], 25) }));
    expect(s.resetReason).toBeNull();
    expect(s.changedTheirs.size).toBe(0);
  });

  it("blames an offer change over a lock change", () => {
    const s = new TradeSession();
    s.onState(view({ rev: 2, mineLocked: true, theirsLocked: true, theirsConfirmed: true, theirs: offer([], 5) }));
    s.onState(view({ rev: 3, mineLocked: false, theirsLocked: true, mine: offer([item(1)]), theirs: offer([], 5) }));
    expect(s.resetReason).toMatch(/^Your offer changed/);
  });

  it("does not shout when nothing had been confirmed", () => {
    const s = new TradeSession();
    s.onState(view({ rev: 1 }));
    s.onState(view({ rev: 2, theirs: offer([item(5)]) }));
    expect(s.resetReason).toBeNull();
    expect(s.changedTheirs.has("5:1")).toBe(true);
  });

  it("drops a refused action's wait but keeps the window", () => {
    const s = new TradeSession();
    s.onState(view({ rev: 3, mineLocked: true, theirsLocked: true }));
    s.sent("confirm");
    expect(s.onError(9, "stale_revision")).toMatch(/changed/);
    expect(s.busy).toBe(false);
    expect(s.open).toBe(true);
  });

  it("closes on the result and says how it ended", () => {
    const s = new TradeSession();
    s.onState(view());
    expect(s.onResult(10, { kind: "completed" })).toBeNull(); // not ours
    expect(s.onResult(9, { kind: "completed" })).toBe("Trade with Basil complete.");
    expect(s.open).toBe(false);
    expect(s.status()).toBeNull();
  });

  it("forgets everything on a new connection", () => {
    const s = new TradeSession();
    s.onRequested(1, 100, "A", 30, 0);
    s.onState(view());
    s.reset();
    expect(s.open).toBe(false);
    expect(s.incoming).toEqual([]);
  });

  it("drafts our offer from the window", () => {
    const s = new TradeSession();
    s.onState(view({ mine: offer([item(1, 2)], 7) }));
    expect(s.draft()).toEqual({ items: [{ itemId: 1, quantity: 2 }], crowns: 7 });
  });
});

describe("offers", () => {
  it("edits lines and Crowns", () => {
    let d = { items: [{ itemId: 1, quantity: 2 }], crowns: 0 };
    d = withLine(d, 2, 5);
    d = withLine(d, 1, 3);
    expect(d.items).toEqual([
      { itemId: 2, quantity: 5 },
      { itemId: 1, quantity: 3 },
    ]);
    d = withoutLine(d, 2);
    expect(d.items).toEqual([{ itemId: 1, quantity: 3 }]);
    expect(withCrowns(d, 40).crowns).toBe(40);
    expect(withLine(d, 1, 0).items).toEqual([]);
  });

  it("checks what the server checks", () => {
    const have = new Map([
      [1, 5],
      [2, 1],
    ]);
    const ok = { items: [{ itemId: 1, quantity: 5 }], crowns: 10 };
    expect(offerProblem(ok, have, 10, 12)).toBeNull();
    expect(offerProblem({ ...ok, crowns: 11 }, have, 10, 12)).toMatch(/don't have/);
    expect(offerProblem({ ...ok, crowns: -1 }, have, 10, 12)).toMatch(/whole/);
    expect(offerProblem({ ...ok, crowns: 1.5 }, have, 10, 12)).toMatch(/whole/);
    expect(offerProblem({ items: [{ itemId: 1, quantity: 6 }], crowns: 0 }, have, 10, 12)).toMatch(/only have 5/);
    expect(offerProblem({ items: [{ itemId: 1, quantity: 0 }], crowns: 0 }, have, 10, 12)).toMatch(/at least one/);
    expect(
      offerProblem(
        {
          items: [
            { itemId: 1, quantity: 1 },
            { itemId: 1, quantity: 1 },
          ],
          crowns: 0,
        },
        have,
        10,
        12,
      ),
    ).toMatch(/already/);
    const many = { items: Array.from({ length: 13 }, (_, i) => ({ itemId: 100 + i, quantity: 1 })), crowns: 0 };
    expect(offerProblem(many, have, 10, 12)).toMatch(/12/);
    expect(offerProblem({ items: [], crowns: 2_000_000 }, have, 5_000_000, 12)).toMatch(/1,000,000/);
  });

  it("compares offers by lines and Crowns", () => {
    expect(sameOffer(offer([item(1), item(2, 3)], 5), offer([item(2, 3), item(1)], 5))).toBe(true);
    expect(sameOffer(offer([item(1)], 5), offer([item(1)], 6))).toBe(false);
    expect(sameOffer(offer([item(1)]), offer([item(1, 2)]))).toBe(false);
    expect([...changedLines(offer([item(1), item(2)]), offer([item(1)]))]).toEqual(["removed"]);
  });
});

describe("words", () => {
  it("explains every outcome and error", () => {
    expect(outcomeText({ kind: "walked_away" }, "Basil", false)).toMatch(/too far apart/);
    expect(outcomeText({ kind: "expired" }, "Basil", true)).toMatch(/didn't answer/);
    expect(outcomeText({ kind: "failed", code: "insufficient_crowns" }, "Basil", false)).toMatch(/Nothing was exchanged.*Crowns/);
    expect(errorText("busy", "Basil")).toMatch(/Basil is busy/);
    expect(errorText("item_unavailable")).toMatch(/no longer/);
  });
});
