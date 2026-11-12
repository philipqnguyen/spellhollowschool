// The players list and the player card (ui/players-state.ts).

import { describe, expect, it } from "vitest";
import { defaultAppearance, toWire } from "../content/appearance";
import { KeysetPager } from "../economy/paging";
import { ACTION_LABELS, cardPath, cardSubtitle, listSummary, normaliseQuery, PAGE_LIMIT, parseCard, parsePlayersPage, playerActions, playersError, playersPath, QUERY_MAX, sameName, skillRows, wornRows, type PlayerRow } from "./players-state";

describe("the players list", () => {
  it("asks the server for a trimmed search, a page size and the cursor", () => {
    expect(playersPath("", null)).toBe(`/players?limit=${PAGE_LIMIT}`);
    expect(playersPath("  an ", null)).toBe(`/players?q=an&limit=${PAGE_LIMIT}`);
    expect(playersPath("Anne Marie", "anne marie", 10)).toBe("/players?q=Anne+Marie&limit=10&after=anne+marie");
    expect(normaliseQuery("x".repeat(40))).toHaveLength(QUERY_MAX);
    // Characters, not bytes: an accented name is cut where a person would count.
    expect(normaliseQuery("é".repeat(30))).toBe("é".repeat(QUERY_MAX));
    expect(cardPath("Anne Marie")).toBe("/players/Anne%20Marie/card");
  });

  it("reads a page of names and houses, nothing else", () => {
    const page = parsePlayersPage({ items: [{ name: "Ada", house: "Skytalon", place: "Great Hall" }, { name: "Bo", house: "burrowsby" }, { house: "Serpentyne" }, null], next: "bo" });
    expect(page).toEqual({ items: [{ name: "Ada", house: "skytalon" }, { name: "Bo", house: "burrowsby" }], next: "bo" });
    expect(parsePlayersPage({ items: [], next: null })).toEqual({ items: [], next: null });
    expect(parsePlayersPage(null)).toEqual({ items: [], next: null });
  });

  it("pages forward and back with the economy's keyset pager, a stale answer ignored", () => {
    const pager = new KeysetPager<PlayerRow>();
    const first = pager.begin("first")!;
    expect(first.after).toBeNull();
    expect(pager.receive(first.ticket, { items: [{ name: "Ada", house: "skytalon" }], next: "ada" })).toBe(true);
    const next = pager.begin("next")!;
    expect(next.after).toBe("ada");
    // A new search while page 2 loads: the old answer is dropped.
    const again = pager.begin("first")!;
    expect(pager.receive(next.ticket, { items: [{ name: "Zed", house: "serpentyne" }], next: null })).toBe(false);
    expect(pager.receive(again.ticket, { items: [{ name: "Bo", house: "burrowsby" }], next: null })).toBe(true);
    expect(pager.items.map((p) => p.name)).toEqual(["Bo"]);
  });

  it("offers Add friend, Whisper, Examine and Challenge to a duel, and only Examine on ourselves", () => {
    expect(playerActions("Ada", "Bo").map((a) => ACTION_LABELS[a])).toEqual(["Add friend", "Whisper", "Invite to party", "Examine", "Challenge to a duel"]);
    expect(playerActions("bo", "Bo")).toEqual(["examine"]);
    expect(playerActions("Ada", "Bo", true)).toEqual(["add_friend", "whisper", "party_invite", "club_invite", "examine", "challenge"]);
    expect(playerActions("bo", "Bo", true)).toEqual(["examine"]);
    expect(sameName("ANNE", "anne")).toBe(true);
  });

  it("says what the list shows and why a request failed", () => {
    expect(listSummary(0, "")).toBe("No one is online.");
    expect(listSummary(0, " zz ")).toBe('No one online has "zz" in their name.');
    expect(listSummary(3, "an")).toContain('"an"');
    expect(listSummary(3, "")).toBe("Online now, A to Z");
    expect(playersError("rate_limited", "x")).toContain("Wait a moment");
    expect(playersError("player_not_found", "x")).toBe("No one goes by that name.");
    expect(playersError("invalid_query", "x")).toContain(String(QUERY_MAX));
    expect(playersError("other", "Server words")).toBe("Server words");
  });
});

describe("the player card", () => {
  const look = defaultAppearance("skytalon");
  const raw = {
    name: "Ada",
    house: "Skytalon",
    year: 2,
    year_name: "Second Year",
    level: 5,
    skills: [
      { discipline: "charms", level: 3 },
      { discipline: "history", level: 1 },
    ],
    appearance: toWire({ ...look, bodyType: 1, outfit: { ...look.outfit, legs: { itemDef: 302, dyes: [1, 2, 3] } } }),
  };

  it("reads the card: name, house, year, level, skills and outfit", () => {
    const c = parseCard(raw);
    expect(c.name).toBe("Ada");
    expect(c.house).toBe("skytalon");
    expect(cardSubtitle(c)).toBe("Skytalon · Second Year · Level 5");
    expect(cardSubtitle({ ...c, yearName: "" })).toBe("Skytalon · Year 2 · Level 5");
    const names: Record<string, string> = { charms: "Charms", history: "History of Magic" };
    expect(skillRows(c, (id) => names[id] ?? "")).toEqual([
      { name: "Charms", level: 3 },
      { name: "History of Magic", level: 1 },
    ]);
    expect(skillRows({ skills: [{ subject: "flying", level: 2 }] }, () => "")).toEqual([{ name: "flying", level: 2 }]);
    expect(c.appearance?.bodyType).toBe(1);
    const worn = wornRows(c.appearance);
    expect(worn.find((w) => w.slot === "legs")).toEqual({ slot: "legs", label: "Legs", name: "Pleated School Skirt", wear: "Female only" });
    expect(worn.find((w) => w.slot === "body")?.wear).toBeNull();
    expect(worn.some((w) => w.slot === "hat")).toBe(look.outfit.hat !== null);
  });

  it("copes with a card missing parts", () => {
    const c = parseCard({ name: "Bo" });
    expect(c).toMatchObject({ name: "Bo", house: "", year: 1, level: 1, skills: [], appearance: null });
    expect(wornRows(null)).toEqual([]);
  });
});
