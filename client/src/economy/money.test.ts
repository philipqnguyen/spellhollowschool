import { describe, expect, it } from "vitest";
import { addChecked, divFloor, ECONOMY, formatDelta, formatCrowns, crownsText, listingQuote, mulChecked, parseWhole, priceOk, proceeds, salesTax, sendCost, type EconomyRules } from "./money";

const rules = (pct: number, fee = 3): EconomyRules => ({ ...ECONOMY, exchange: { ...ECONOMY.exchange, sales_tax_pct: pct, listing_fee: fee } });

describe("shipped rules", () => {
  it("are the economy file's numbers", () => {
    expect(ECONOMY.starting_purse).toBe(100);
    expect(ECONOMY.exchange.listing_fee).toBe(3);
    expect(ECONOMY.exchange.sales_tax_pct).toBe(5);
    expect(ECONOMY.exchange.listing_secs).toBe(5 * 86_400);
    expect(ECONOMY.mail.postage).toBe(1);
  });
});

describe("sales tax", () => {
  it("is the percentage rounded down, as sim_core computes it", () => {
    expect(salesTax(100)).toBe(5);
    expect(salesTax(19)).toBe(0); // 0.95 rounds down
    expect(salesTax(20)).toBe(1);
    expect(salesTax(39)).toBe(1); // 1.95
    expect(salesTax(1_000_000)).toBe(50_000);
    expect(salesTax(0)).toBe(0);
  });

  it("refuses negative, fractional and unsafe prices", () => {
    expect(salesTax(-1)).toBeNull();
    expect(salesTax(1.5)).toBeNull();
    expect(salesTax(Number.MAX_SAFE_INTEGER)).toBeNull(); // the product would overflow
    expect(salesTax(Number.NaN)).toBeNull();
  });

  it("matches floor(price * pct / 100) for every price up to 10,000 at several rates", () => {
    for (const pct of [0, 5, 7, 13, 100]) {
      const r = rules(pct);
      for (let p = 0; p <= 10_000; p++) {
        const tax = salesTax(p, r)!;
        expect(Number.isInteger(tax)).toBe(true);
        expect(tax * 100 <= p * pct && (tax + 1) * 100 > p * pct).toBe(true);
        expect(proceeds(p, r)).toBe(p - tax);
      }
    }
  });
});

describe("checked arithmetic", () => {
  it("stays whole or answers null", () => {
    expect(addChecked(2, 3)).toBe(5);
    expect(addChecked(Number.MAX_SAFE_INTEGER, 1)).toBeNull();
    expect(addChecked(0.5, 1)).toBeNull();
    expect(mulChecked(12, 99)).toBe(1188);
    expect(mulChecked(2 ** 30, 2 ** 30)).toBeNull();
    expect(divFloor(199, 100)).toBe(1);
    expect(divFloor(200, 100)).toBe(2);
  });
});

describe("prices and letters", () => {
  it("allows prices within the Exchange's bounds", () => {
    expect(priceOk(0)).toBe(false);
    expect(priceOk(1)).toBe(true);
    expect(priceOk(1_000_000)).toBe(true);
    expect(priceOk(1_000_001)).toBe(false);
    expect(priceOk(2.5)).toBe(false);
  });

  it("charges postage on top of the Crowns enclosed", () => {
    expect(sendCost(0)).toBe(1);
    expect(sendCost(50)).toBe(51);
    expect(sendCost(-1)).toBeNull();
    expect(sendCost(1.2)).toBeNull();
  });

  it("quotes a listing: fee now, tax and proceeds at sale", () => {
    expect(listingQuote(100)).toEqual({ price: 100, fee: 3, tax: 5, proceeds: 95, net: 92 });
    expect(listingQuote(2)).toEqual({ price: 2, fee: 3, tax: 0, proceeds: 2, net: -1 });
    expect(listingQuote(0)).toBeNull();
  });
});

describe("typing and showing amounts", () => {
  it("reads whole numbers only", () => {
    expect(parseWhole("42")).toBe(42);
    expect(parseWhole(" 1,250 ")).toBe(1250);
    expect(parseWhole("1_000")).toBe(1000);
    expect(parseWhole("")).toBeNull();
    expect(parseWhole("-5")).toBeNull();
    expect(parseWhole("1.5")).toBeNull();
    expect(parseWhole("1e3")).toBeNull();
    expect(parseWhole("12abc")).toBeNull();
    expect(parseWhole("99999999999999999")).toBeNull();
  });

  it("formats with grouping, words and signed changes", () => {
    expect(formatCrowns(1234567)).toBe("1,234,567");
    expect(crownsText(1)).toBe("1 Crown");
    expect(crownsText(1234)).toBe("1,234 Crowns");
    expect(formatDelta(25)).toBe("+25");
    expect(formatDelta(-3)).toBe("−3");
  });
});
