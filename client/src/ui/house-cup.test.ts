import { describe, expect, it } from "vitest";
import { PALETTE } from "../content/catalog";
import { KitCup, cupNodeOf } from "../game/castle/cup";
import { castsSunShadow } from "../game/castle/nodes";
import { easeFill, fillShares, houseColours, houseSlot, lastText, mineText, parseCup, resetText, resetWhen, slotHouse, standings, untilText, type CupHouse } from "./house-cup-state";

const NOW = 1_800_000_000_000;

/** `GET /api/house-cup` as the server sends it (docs/architecture.md). */
function wire(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    week: { start_ms: 1_799_539_200_000, end_ms: 1_800_144_000_000 },
    resets_in_secs: 3 * 86_400 + 4 * 3600 + 120,
    houses: [
      { house: "Roarwing", points: 120 },
      { house: "Burrowsby", points: 95 },
      { house: "Skytalon", points: 130 },
      { house: "Serpentyne", points: 130 },
    ],
    mine: 14,
    last: {
      week: { start_ms: 1_798_934_400_000, end_ms: 1_799_539_200_000 },
      winners: ["Serpentyne"],
      houses: [
        { house: "Roarwing", points: 40 },
        { house: "Burrowsby", points: 10 },
        { house: "Skytalon", points: 0 },
        { house: "Serpentyne", points: 77 },
      ],
      closed_at_ms: 1_799_539_260_000,
    },
    badge: ["Serpentyne"],
    ...over,
  };
}

describe("the House Cup's standings", () => {
  it("reads the route's answer: four houses in order, shares of the leader, ties lead together", () => {
    const s = parseCup(wire(), NOW)!;
    expect(s.houses.map((h) => h.id)).toEqual(["roarwing", "burrowsby", "skytalon", "serpentyne"]);
    expect(s.houses.map((h) => h.points)).toEqual([120, 95, 130, 130]);
    expect(s.houses.map((h) => h.leading)).toEqual([false, false, true, true]);
    expect(s.houses[0]!.share).toBeCloseTo(120 / 130, 6);
    expect(s.houses[2]!.share).toBe(1);
    expect(s.mine).toBe(14);
    expect([...s.badge]).toEqual(["serpentyne"]);
    expect(s.last?.winners).toEqual(["serpentyne"]);
    expect(s.resetAtMs).toBe(NOW + (3 * 86_400 + 4 * 3600 + 120) * 1000);
  });

  it("puts missing houses at zero and ignores junk", () => {
    const s = standings([{ house: "Skytalon", points: 5 }, { house: "Nowhere", points: 99 }, null, { house: "burrowsby", points: -3 }]);
    expect(s.map((h) => h.points)).toEqual([0, 0, 5, 0]);
    expect(s.map((h) => h.share)).toEqual([0, 0, 1, 0]);
    expect(parseCup(null, NOW)).toBeNull();
    expect(parseCup({ houses: "x" }, NOW)).toBeNull();
    const bare = parseCup({ houses: [] }, NOW)!;
    expect(bare.last).toBeNull();
    expect(bare.badge.size).toBe(0);
    expect(bare.houses.every((h) => h.points === 0 && !h.leading)).toBe(true);
  });

  it("says when it resets, your points and last week's winner, ties shared", () => {
    const s = parseCup(wire(), NOW)!;
    expect(resetWhen()).toBe("Monday 00:00 UTC");
    expect(resetWhen({ week_start: { weekday: "sunday", hour_utc: 6 } })).toBe("Sunday 06:00 UTC");
    expect(resetText(s, NOW)).toBe("Resets Monday 00:00 UTC, in 3 d 4 h");
    expect(untilText(30_000)).toBe("under a minute");
    expect(untilText(12 * 60_000)).toBe("12 min");
    expect(untilText((5 * 60 + 7) * 60_000)).toBe("5 h 07 min");
    expect(lastText(s)).toBe("Last week: Serpentyne won the House Cup with 77 points.");
    const tie = parseCup(wire({ last: { ...(wire().last as object), winners: ["Roarwing", "Serpentyne"], houses: [{ house: "Roarwing", points: 77 }, { house: "Serpentyne", points: 77 }] } }), NOW)!;
    expect(lastText(tie)).toBe("Last week: Roarwing and Serpentyne shared the House Cup with 77 points each.");
    const none = parseCup(wire({ last: { ...(wire().last as object), winners: [] } }), NOW)!;
    expect(lastText(none)).toBe("Last week: no house scored, so nobody won the cup.");
    expect(lastText(parseCup(wire({ last: null }), NOW)!)).toMatch(/No week has closed yet/);
    // After a dev close the newest closed week is this one.
    const closedNow = parseCup(wire({ last: { ...(wire().last as object), week: wire().week } }), NOW)!;
    expect(lastText(closedNow)).toMatch(/^This week: Serpentyne won/);
    expect(mineText(s, "skytalon")).toBe("You have earned 14 points for Skytalon this week.");
    expect(mineText({ ...s, mine: 3 }, "skytalon")).toMatch(/3 points for Skytalon this week\. 10 or more/);
    expect(mineText({ ...s, mine: 0 }, "Roarwing")).toMatch(/^You have not earned points for Roarwing yet/);
  });

  it("colours the bars with the houses' palette colours", () => {
    const hex = (i: number) => PALETTE.colours.find((c) => c.index === i)!.hex;
    for (const id of ["roarwing", "burrowsby", "skytalon", "serpentyne"] as CupHouse[]) {
      expect(houseColours(id)).toEqual({ fill: hex(PALETTE.houses[id]!.primary), edge: hex(PALETTE.houses[id]!.secondary) });
    }
  });
});

describe("the hourglasses", () => {
  it("fill by the share of the leader's points: the leader full, all empty while nobody scored", () => {
    expect(fillShares([120, 95, 130, 130])).toEqual([120 / 130, 95 / 130, 1, 1]);
    expect(fillShares([0, 0, 0, 0])).toEqual([0, 0, 0, 0]);
    expect(fillShares([0, 0, 7, 0])).toEqual([0, 0, 1, 0]);
    expect(fillShares([-5, 10, 0, 5])).toEqual([0, 1, 0, 0.5]);
  });

  it("ease toward the fill and land on it", () => {
    let f = 0;
    f = easeFill(f, 1, 1.2);
    expect(f).toBeCloseTo(1 - Math.exp(-1), 6);
    for (let i = 0; i < 100; i++) f = easeFill(f, 1, 0.1);
    expect(f).toBe(1);
    expect(easeFill(0.5, 0.5, 0.016)).toBe(0.5);
    expect(easeFill(0.8, 0.2, 0)).toBe(0.8);
  });

  it("map house_a..d to the houses in House::ALL order", () => {
    expect(["roarwing", "burrowsby", "skytalon", "serpentyne"].map((h) => houseSlot(h as CupHouse))).toEqual(["house_a", "house_b", "house_c", "house_d"]);
    expect(slotHouse("house_d")).toBe("serpentyne");
    expect(slotHouse("house_e")).toBeNull();
  });

  // Plain stand-ins for Babylon nodes: what KitCup touches.
  function node(name: string, kitAnim?: unknown) {
    return {
      name,
      metadata: kitAnim ? { gltf: { extras: { kit_anim: kitAnim } } } : undefined,
      scaling: { y: 1 },
      isVisible: true,
      getTotalVertices: () => 24,
      getChildMeshes: () => [] as unknown[],
      unfreezeWorldMatrix() {},
    };
  }

  it("finds the built pennant's nodes by extras or name, and keeps them out of the sun's shadow", () => {
    expect(cupNodeOf(node("great_hall.castle_props.hourglass_sand_house_b", { kind: "fill", house: "house_b" }))).toEqual({ kind: "fill", house: "burrowsby" });
    expect(cupNodeOf(node("great_hall.castle_props.cup_banners_house_c"))).toEqual({ kind: "cup_banners", house: "skytalon" });
    expect(cupNodeOf(node("x", { kind: "fill", house: "house_q" }))).toBeNull();
    expect(cupNodeOf(node("great_hall.castle_props.candles_1", { kind: "bob", group: "candles_1", amp: 0.1, period: 4, phase: 0 }))).toBeNull();
    expect(castsSunShadow(node("great_hall.castle_props.cup_banners_house_a") as never)).toBe(false);
    expect(castsSunShadow(node("great_hall.castle_wall") as never)).toBe(true);
  });

  it("scale the sand toward the standings and hang only the holders' banners", () => {
    const kit = new KitCup();
    const sand = (["a", "b", "c", "d"] as const).map((k) => node(`great_hall.castle_props.hourglass_sand_house_${k}`, { kind: "fill", house: `house_${k}` }));
    const banners = (["a", "b", "c", "d"] as const).map((k) => node(`great_hall.castle_props.cup_banners_house_${k}`, { kind: "cup_banners", house: `house_${k}` }));
    expect(kit.wire("great_hall", [...sand, ...banners, node("great_hall.castle_wall")] as never)).toBe(8);
    // Hidden by default; the sand starts (nearly) empty.
    expect(banners.map((b) => b.isVisible)).toEqual([false, false, false, false]);
    expect(sand.every((s) => s.scaling.y <= 1e-3)).toBe(true);
    const s = parseCup(wire(), NOW)!;
    kit.setFills(new Map(s.houses.map((h) => [h.id, h.share])));
    kit.setBadge(s.badge);
    expect(banners.map((b) => b.isVisible)).toEqual([false, false, false, true]);
    for (let i = 0; i < 300; i++) kit.update(1 / 30);
    expect(sand.map((x) => x.scaling.y)).toEqual([120 / 130, 95 / 130, 1, 1]);
    expect([...kit.shownFills().values()]).toEqual([120 / 130, 95 / 130, 1, 1]);
    // A week closes with a tie: both banners hang.
    kit.setBadge(new Set<CupHouse>(["roarwing", "serpentyne"]));
    expect(banners.map((b) => b.isVisible)).toEqual([true, false, false, true]);
    kit.unwire("great_hall");
    expect(kit.counts).toEqual({ sands: 0, banners: 0 });
  });
});
