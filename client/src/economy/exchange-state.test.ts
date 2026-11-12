import { describe, expect, it } from "vitest";
import { activeFilterCount, browseQuery, defaultFilters, eachText, exchangeError, filtersKey, normalizeFilters, sellProblem, sellQuote, timeLeft } from "./exchange-state";
import type { EconItem } from "./items";

const item = (over: Partial<EconItem> = {}): EconItem => ({ id: 7, item_def: 3001, dyes: [47, 47, 47], location: "inventory", slot: null, created_at: "", quantity: 12, maker: null, finish: "none", ...over });

describe("browse filters", () => {
  it("make the query the server reads, with keyset paging", () => {
    expect(browseQuery(defaultFilters(), null)).toBe("?sort=newest&limit=25");
    const f = { ...defaultFilters(), category: "hats", subcategory: "hoods", q: " wool ", minLevel: 2, maxLevel: 5, colour: 16, house: "skytalon", sort: "price" as const };
    expect(browseQuery(f, "abc")).toBe("?category=hats&subcategory=hoods&q=wool&min_level=2&max_level=5&colour=16&house=skytalon&sort=price&limit=25&after=abc");
  });

  it("drop what the server would refuse", () => {
    const n = normalizeFilters({ ...defaultFilters(), category: "wands", subcategory: "hoods", colour: 999, house: "no_such_house", minLevel: 30, maxLevel: 0 });
    expect(n.category).toBeNull();
    expect(n.subcategory).toBeNull();
    expect(n.colour).toBeNull();
    expect(n.house).toBeNull();
    // Out-of-range levels are clamped, and swapped bounds put back in order.
    expect([n.minLevel, n.maxLevel]).toEqual([1, 20]);
    expect(normalizeFilters({ ...defaultFilters(), category: "hats", subcategory: "boots" }).subcategory).toBeNull();
    expect(normalizeFilters({ ...defaultFilters(), category: "potions", subcategory: "fortunes_draught" }).subcategory).toBe("fortunes_draught");
  });

  it("know when a search would repeat itself", () => {
    const a = { ...defaultFilters(), q: "wool" };
    expect(filtersKey(a)).toBe(filtersKey({ ...a, q: " wool  " }));
    expect(filtersKey(a)).not.toBe(filtersKey({ ...a, sort: "price" }));
    expect(activeFilterCount(a)).toBe(1);
    expect(activeFilterCount({ ...a, colour: 3, house: "serpentyne" })).toBe(3);
  });
});

describe("selling", () => {
  it("quotes the fee, tax and proceeds in whole Crowns", () => {
    expect(sellQuote({ quantity: 1, price: 250 })).toEqual({ price: 250, fee: 3, tax: 12, proceeds: 238, net: 235 });
    expect(sellQuote({ quantity: 1, price: 0 })).toBeNull();
  });

  it("checks the item, the price, the slots and the fee", () => {
    expect(sellProblem(item(), { quantity: 12, price: 30 }, 10, 0)).toBeNull();
    expect(sellProblem(item({ location: "equipped" }), { quantity: 1, price: 30 }, 10, 0)).toMatch(/trunk/);
    expect(sellProblem(item(), { quantity: 13, price: 30 }, 10, 0)).toMatch(/only have 12/);
    expect(sellProblem(item(), { quantity: 0, price: 30 }, 10, 0)).toMatch(/at least one/);
    expect(sellProblem(item(), { quantity: 1, price: 0 }, 10, 0)).toMatch(/between 1 and 1,000,000/);
    expect(sellProblem(item(), { quantity: 1, price: 2.5 }, 10, 0)).toMatch(/between/);
    expect(sellProblem(item(), { quantity: 1, price: 30 }, 10, 20)).toMatch(/20 listings/);
    expect(sellProblem(item(), { quantity: 1, price: 30 }, 2, 0)).toMatch(/fee/);
    // Unknown listing count: the server decides.
    expect(sellProblem(item(), { quantity: 1, price: 30 }, 10, null)).toBeNull();
  });

  it("splits a lot's price per unit only when it divides evenly", () => {
    expect(eachText(30, 10)).toBe("3 each");
    expect(eachText(31, 10)).toBe("");
    expect(eachText(30, 1)).toBe("");
  });
});

describe("time left", () => {
  const now = Date.parse("2026-10-24T12:00:00Z");
  it("is coarse and never negative", () => {
    expect(timeLeft("2026-10-29T11:00:00Z", now)).toBe("4 d 23 h");
    expect(timeLeft("2026-10-24T17:12:30Z", now)).toBe("5 h 12 min");
    expect(timeLeft("2026-10-24T12:12:00Z", now)).toBe("12 min");
    expect(timeLeft("2026-10-24T12:00:30Z", now)).toBe("under a minute");
    expect(timeLeft("2026-10-24T11:00:00Z", now)).toBe("ended");
    expect(timeLeft("garbage", now)).toBe("");
  });
});

describe("refusals", () => {
  it("send the player back to the counter", () => {
    expect(exchangeError("not_at_station", "")).toMatch(/counter/);
    expect(exchangeError("listing_closed", "")).toMatch(/Too late/);
    expect(exchangeError("brand_new_code", "From the server")).toBe("From the server");
  });
});
