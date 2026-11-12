import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { PartyMember, PartyPlace, PartyView } from "../wasm/sim_wasm";
import { membersHere, memberNamed, parsePartyCommand, partyErrorText, partyNoticeText, partyRows, placeText, type PlaceNames } from "./party-state";

const names: PlaceNames = {
  className: (id) => ({ charms: "Charms" })[id],
  venueName: (id) => ({ dueling_club: "Dueling Club" })[id],
};

const member = (characterId: number, name: string, place: PartyPlace | null, zone = 0, level = 3): PartyMember => ({
  characterId,
  name,
  house: "skytalon",
  level,
  online: place ? { entity: 100 + characterId, zone, place } : null,
});

const hall: PartyPlace = { kind: "castle", place: "Great Hall" };

/** Ada leads; Bea (us) and Cy in the castle, Di in a dorm, Eve offline. The server lists the leader first. */
const view: PartyView = {
  leader: 1,
  members: [member(1, "Ada", hall), member(2, "Bea", hall), member(3, "Cy", { kind: "castle", place: "Library" }), member(4, "Di", { kind: "dorm", ownerName: "Di", house: "roarwing" }, 17), member(5, "Eve", null, 0, 9)],
};

describe("the party list", () => {
  it("puts the leader first, then everyone as they joined, ourselves included", () => {
    const rows = partyRows(view, 2, "Bea", names);
    expect(rows.map((r) => r.name)).toEqual(["Ada", "Bea", "Cy", "Di", "Eve"]);
    expect(rows.map((r) => r.leader)).toEqual([true, false, false, false, false]);
    expect(rows.find((r) => r.self)?.name).toBe("Bea");
    // The same order whatever order the members arrive in.
    const shuffled = { ...view, members: [view.members[3]!, view.members[0]!, view.members[4]!, view.members[1]!, view.members[2]!] };
    expect(partyRows(shuffled, 2, "Bea", names).map((r) => r.name)).toEqual(["Ada", "Di", "Eve", "Bea", "Cy"]);
    expect(partyRows(null, 2, "Bea", names)).toEqual([]);
  });

  it("says who is here, elsewhere and offline, with levels and places", () => {
    const rows = partyRows(view, 2, "Bea", names);
    expect(rows.map((r) => r.status)).toEqual(["here", "here", "here", "elsewhere", "offline"]);
    expect(rows[3]!.where).toBe("In Di's dorm room");
    expect(rows[3]!.level).toBe(3);
    expect(rows[4]!.where).toBeNull();
    expect(rows[4]!.level).toBe(9);
    expect(rows[4]!.entity).toBeNull();
    expect(rows[2]!.where).toBe("Library");
    // Seen from the dorm, the castle is elsewhere.
    expect(partyRows(view, 4, "Di", names).map((r) => r.status)).toEqual(["elsewhere", "elsewhere", "elsewhere", "here", "offline"]);
  });

  it("names every kind of place", () => {
    expect(placeText({ kind: "class", class: "charms" }, names, "Bea")).toBe("In class: Charms");
    expect(placeText({ kind: "arena", venue: "dueling_club" }, names, "Bea")).toBe("In a duel at the Dueling Club");
    expect(placeText({ kind: "wishingRoom", ownerName: "Ada" }, names, "Bea")).toBe("In Ada's Wishing Room");
    expect(placeText({ kind: "dorm", ownerName: "bea", house: "skytalon" }, names, "Bea")).toBe("In your dorm room");
    expect(placeText({ kind: "castle", place: "" }, names, "Bea")).toBe("In the castle");
  });

  it("lists the members drawn with us (blue nameplates, map dots)", () => {
    expect([...membersHere(view, 2)].sort()).toEqual([101, 103]);
    expect([...membersHere(view, 4)]).toEqual([]);
    expect(membersHere(null, 2).size).toBe(0);
    expect(memberNamed(view, "cy")?.characterId).toBe(3);
  });
});

describe("party words", () => {
  it("tells notices from our side", () => {
    expect(partyNoticeText({ kind: "joined", name: "Cy" }, "Bea")).toBe("Cy joined the party.");
    expect(partyNoticeText({ kind: "joined", name: "bea" }, "Bea")).toMatch(/^You joined the party/);
    expect(partyNoticeText({ kind: "removed", name: "Bea" }, "Bea")).toBe("You were removed from the party.");
    expect(partyNoticeText({ kind: "leader", name: "Ada" }, "Bea")).toBe("Ada leads the party now.");
    expect(partyNoticeText({ kind: "disbanded" }, "Bea")).toMatch(/party is over/);
    expect(partyNoticeText({ kind: "inviteSent", name: "Cy" }, "Bea")).toBe("You invited Cy to your party.");
  });

  it("explains every refusal", () => {
    const codes = ["not_online", "self_invite", "in_party", "full", "not_in_party", "not_leader", "not_member", "invite_expired", "blocked", "rate_limited"] as const;
    for (const c of codes) expect(partyErrorText(c).length).toBeGreaterThan(5);
    expect(partyErrorText("not_online", "Cy")).toBe("Cy is not online.");
    expect(partyErrorText("in_party", "Cy")).toBe("Cy is already in a party.");
  });
});

describe("party commands", () => {
  it("reads /invite, /pinvite, /leave, /pleave, /pkick and /ppromote", () => {
    expect(parsePartyCommand("/invite Anne Marie")).toEqual({ verb: "invite", name: "Anne Marie" });
    expect(parsePartyCommand('/pinvite "Anne Marie"')).toEqual({ verb: "invite", name: "Anne Marie" });
    expect(parsePartyCommand("/INVITE tom")).toEqual({ verb: "invite", name: "tom" });
    expect(parsePartyCommand("/leave")).toEqual({ verb: "leave" });
    expect(parsePartyCommand("/pleave")).toEqual({ verb: "leave" });
    expect(parsePartyCommand("/pkick Cy")).toEqual({ verb: "kick", name: "Cy" });
    expect(parsePartyCommand("/ppromote Cy")).toEqual({ verb: "promote", name: "Cy" });
    expect(parsePartyCommand("/invite")).toEqual({ error: "Invite whom? Type /invite Name." });
    expect(parsePartyCommand("/party")).toEqual({ verb: "help" });
  });

  it("leaves chat and other commands alone", () => {
    expect(parsePartyCommand("/p hello all")).toBeNull();
    expect(parsePartyCommand("/party on my way")).toBeNull();
    expect(parsePartyCommand("/w Cy hi")).toBeNull();
    expect(parsePartyCommand("/way library")).toBeNull();
    expect(parsePartyCommand("hello")).toBeNull();
  });
});

describe("party blue", () => {
  const css = readFileSync(new URL("../style.css", import.meta.url), "utf8");
  const token = (name: string) => new RegExp(`${name}:\\s*(#[0-9a-f]{6})`, "i").exec(css)?.[1];

  const lum = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
  };
  const contrast = (a: string, b: string) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x! + 0.05) / (y! + 0.05);
  };
  /** `fg` at `alpha` over `bg`. */
  const over = (fg: string, alpha: number, bg: string) =>
    "#" +
    [1, 3, 5]
      .map((i) => Math.round(Number.parseInt(fg.slice(i, i + 2), 16) * alpha + Number.parseInt(bg.slice(i, i + 2), 16) * (1 - alpha)))
      .map((v) => v.toString(16).padStart(2, "0"))
      .join("");
  const hue = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number];
    const max = Math.max(r, g, b);
    const d = max - Math.min(r, g, b);
    const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return { h: (h * 60 + 360) % 360, s: max === 0 ? 0 : d / max };
  };

  it("reads well on the chat panel, even over a mid-grey scene", () => {
    const party = token("--party")!;
    expect(party).toBeTruthy();
    // The chat panel is rgba(18, 14, 20, 0.45) over the world.
    expect(contrast(party, over("#121014", 0.45, "#808080"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(party, "#121014")).toBeGreaterThanOrEqual(7);
  });

  it("is a saturated azure, clearly not a house tint (Skytalon's house chat is a pale slate blue)", () => {
    const party = hue(token("--party")!);
    // House chat: 30% of the house colour, 70% white.
    const skytalon = hue(over("#24427a", 0.3, "#ffffff"));
    expect(party.s).toBeGreaterThan(0.5);
    expect(skytalon.s).toBeLessThan(0.3);
    expect(party.h).toBeGreaterThan(185);
    expect(party.h).toBeLessThan(215);
    expect(Math.abs(party.h - skytalon.h)).toBeGreaterThan(12);
  });

  it("colours party lines, the channel hint and party nameplates", () => {
    expect(css).toMatch(/\.chat-party\s*\{[^}]*var\(--party\)/);
    expect(css).toMatch(/\.chat-channel\[data-channel="party"\]\s*\{[^}]*var\(--party\)/);
    expect(css).toMatch(/\.nameplate\[data-group="party"\]\s*\{[^}]*var\(--party\)/);
  });
});
