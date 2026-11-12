import { describe, expect, it } from "vitest";
import type { ClubView } from "../wasm/sim_wasm";
import {
  applyPresence,
  canInvite,
  canKick,
  canSetMotd,
  cleanName,
  cleanTag,
  CLUB_RULES,
  clubErrorText,
  clubNoticeText,
  clubRows,
  foundAnswer,
  foundCase,
  isOwner,
  memberNamed,
  myRole,
  onlineCount,
  onlineNames,
  parseClubCommand,
  rowActions,
  secretaryLine,
} from "./club-state";

const view = (): ClubView => ({
  id: 7,
  name: "The Night Owls",
  tag: "OWL",
  motd: "",
  members: [
    { characterId: 1, name: "Ada", house: "skytalon", level: 6, role: "owner", online: true },
    { characterId: 2, name: "Bea", house: "roarwing", level: 4, role: "officer", online: true },
    { characterId: 3, name: "Cy", house: "burrowsby", level: 2, role: "member", online: false },
    { characterId: 4, name: "Di", house: "serpentyne", level: 9, role: "member", online: true },
  ],
});

describe("the clubs secretary", () => {
  it("founds at level 4 for 150 Crowns, from data/clubs.json", () => {
    expect(CLUB_RULES.found_level).toBe(4);
    expect(CLUB_RULES.found_crowns).toBe(150);
    expect(CLUB_RULES.members_max).toBe(100);
  });

  it("greets with her three options", () => {
    expect(secretaryLine("greeting")).toBe("Clubs and Societies! Come to start one of your own, or just curious what's on offer?");
    expect([secretaryLine("option_found"), secretaryLine("option_list"), secretaryLine("option_goodbye")]).toEqual(["Found a club", "Tell me about the clubs", "Goodbye"]);
  });

  it("picks the answer for each case", () => {
    expect(foundCase({ level: 3, crowns: 200, club: null })).toBe("not_level");
    expect(foundCase({ level: 4, crowns: 149, club: null })).toBe("not_crowns");
    expect(foundCase({ level: 2, crowns: 10, club: null })).toBe("not_either");
    expect(foundCase({ level: 4, crowns: 150, club: null })).toBe("eligible");
    expect(foundCase({ level: 20, crowns: 9999, club: "The Night Owls" })).toBe("in_club");
    // Being in a club comes first, whatever else.
    expect(foundCase({ level: 1, crowns: 0, club: "Owls" })).toBe("in_club");
  });

  it("says the approved words with the numbers filled in", () => {
    expect(foundAnswer({ level: 2, crowns: 300, club: null }).text).toBe(
      "Oh, I do love the enthusiasm! But the school only grants charters from level 4, and you're level 2 now. A few more lessons and you'll be there. Do come back and see me then.",
    );
    expect(foundAnswer({ level: 5, crowns: 120, club: null }).text).toBe(
      "Your level is just right for a charter. It's the fee that's the trouble: founding a club costs 150 Crowns, and you have 120. That's 30 Crowns to go. Lessons pay well, and so does the Owl Exchange. I'll keep a blank charter ready for you.",
    );
    expect(foundAnswer({ level: 3, crowns: 40, club: null }).text).toBe(
      "Not just yet, I'm afraid. A charter needs two things: you must be level 4 (you're level 3), and the fee is 150 Crowns (you have 40). Keep attending your lessons. They'll take care of both, and I'll be right here when you're ready.",
    );
    expect(foundAnswer({ level: 9, crowns: 900, club: "The Night Owls" }).text).toBe(
      "You already belong to The Night Owls! A student may only be a member of one club. If you'd like to start your own, you'll need to leave that one first.",
    );
    const ok = foundAnswer({ level: 4, crowns: 150, club: null });
    expect(ok.kind).toBe("eligible");
    expect(ok.text).toBe(
      "Splendid, everything is in order. The fee is 150 Crowns, paid when the charter is sealed. Now, what will your club be called, and what short tag should go beside your members' names?",
    );
    expect(secretaryLine("sealed", { club: "The Night Owls" })).toBe("There we are: The Night Owls is officially on the school's register! Invite your friends, and may it flourish.");
    for (const line of Object.values(CLUB_RULES.secretary_lines)) expect(line.toLowerCase()).not.toContain("year");
  });
});

describe("names and tags", () => {
  it("checks a name as the server does", () => {
    expect(cleanName("  The   Night Owls ")).toEqual({ ok: true, value: "The Night Owls" });
    expect(cleanName("Knights & Dragons").ok).toBe(true);
    expect(cleanName("Café Crème").ok).toBe(true);
    expect(cleanName("Ab")).toEqual({ ok: false, code: "name_invalid" });
    expect(cleanName("a".repeat(25)).ok).toBe(false);
    expect(cleanName("-Owls").ok).toBe(false);
    expect(cleanName("Owls!").ok).toBe(false);
    expect(cleanName("Ow--ls").ok).toBe(false);
  });

  it("keeps tags upper case", () => {
    expect(cleanTag(" owl ")).toEqual({ ok: true, value: "OWL" });
    expect(cleanTag("A")).toEqual({ ok: false, code: "tag_invalid" });
    expect(cleanTag("ABCDE").ok).toBe(false);
    expect(cleanTag("A B").ok).toBe(false);
  });
});

describe("the club window", () => {
  it("lists every member in the server's order and counts who is online", () => {
    const rows = clubRows(view(), 2);
    expect(rows.map((r) => [r.name, r.role, r.self])).toEqual([
      ["Ada", "owner", false],
      ["Bea", "officer", true],
      ["Cy", "member", false],
      ["Di", "member", false],
    ]);
    expect(onlineCount(view())).toEqual({ online: 3, total: 4 });
    expect(clubRows(null, 2)).toEqual([]);
  });

  it("offers what each rank may do", () => {
    const v = view();
    expect(myRole(v, 1)).toBe("owner");
    expect(myRole(v, 9)).toBeNull();
    expect([canInvite("owner"), canInvite("officer"), canInvite("member"), canInvite(null)]).toEqual([true, true, false, false]);
    expect([canSetMotd("officer"), canSetMotd("member")]).toEqual([true, false]);
    expect([isOwner("owner"), isOwner("officer")]).toEqual([true, false]);
    // Officers remove members only; the owner anyone else.
    expect([canKick("officer", "member"), canKick("officer", "officer"), canKick("officer", "owner")]).toEqual([true, false, false]);
    expect([canKick("owner", "officer"), canKick("owner", "member"), canKick("member", "member")]).toEqual([true, true, false]);
    const rows = clubRows(v, 1);
    // The owner on an officer, a member offline, a member online, and themselves.
    expect(rowActions(rows[1]!, "owner")).toEqual(["whisper", "demote", "transfer", "kick"]);
    expect(rowActions(rows[2]!, "owner")).toEqual(["promote", "transfer", "kick"]);
    expect(rowActions(rows[3]!, "officer")).toEqual(["whisper", "kick"]);
    expect(rowActions(rows[1]!, "officer")).toEqual(["whisper"]);
    expect(rowActions(rows[0]!, "member")).toEqual(["leave"]);
  });

  it("follows members coming and going", () => {
    const v = applyPresence(view(), { characterId: 3, online: true, level: 3 })!;
    expect(v.members[2]).toMatchObject({ name: "Cy", online: true, level: 3 });
    expect(onlineCount(v).online).toBe(4);
    // Someone not in the club: unchanged.
    const same = view();
    expect(applyPresence(same, { characterId: 99, online: true, level: 1 })).toBe(same);
    expect(applyPresence(null, { characterId: 3, online: true, level: 3 })).toBeNull();
  });

  it("tints the other members online, by name", () => {
    expect([...onlineNames(view(), 1)].sort()).toEqual(["bea", "di"]);
    expect(onlineNames(null, 1).size).toBe(0);
    expect(memberNamed(view(), "cy")?.characterId).toBe(3);
  });
});

describe("words", () => {
  it("says notices from our side", () => {
    expect(clubNoticeText({ kind: "joined", name: "Bea" }, "Ada")).toBe("Bea joined the club.");
    expect(clubNoticeText({ kind: "joined", name: "ada" }, "Ada")).toMatch(/^You joined the club/);
    expect(clubNoticeText({ kind: "owner", name: "Ada" }, "Ada")).toBe("You are the club's president now.");
    expect(clubNoticeText({ kind: "renamed", club: "Owls", tag: "OW" }, "Ada")).toBe("The club is now called Owls [OW].");
    expect(clubNoticeText({ kind: "disbanded", club: "Owls" }, "Ada")).toBe("Owls has been disbanded.");
  });

  it("explains refusals", () => {
    expect(clubErrorText("not_online", "Bea")).toBe("Bea is not online.");
    expect(clubErrorText("full")).toBe("The club is full (100 members at most).");
    expect(clubErrorText("not_enough_crowns")).toBe("Founding a club costs 150 Crowns.");
    expect(clubErrorText("name_taken")).toMatch(/already has that name/);
  });
});

describe("club commands", () => {
  it("parses /cinvite, /cleave and help", () => {
    expect(parseClubCommand("/cinvite Anne Marie")).toEqual({ verb: "invite", name: "Anne Marie" });
    expect(parseClubCommand('/CINVITE "Anne Marie"')).toEqual({ verb: "invite", name: "Anne Marie" });
    expect(parseClubCommand("/cinvite")).toEqual({ error: "Invite whom? Type /cinvite Name." });
    expect(parseClubCommand("/cleave")).toEqual({ verb: "leave" });
    expect(parseClubCommand("/c")).toEqual({ verb: "help" });
    expect(parseClubCommand("/club")).toEqual({ verb: "help" });
    // With a message, /c and /club are club chat (chatparse.ts); other commands are not ours.
    expect(parseClubCommand("/c hello")).toBeNull();
    expect(parseClubCommand("/cast")).toBeNull();
    expect(parseClubCommand("/cup")).toBeNull();
    expect(parseClubCommand("/leave")).toBeNull();
  });
});
