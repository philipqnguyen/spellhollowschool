import { describe, expect, it } from "vitest";
import type { KickCode } from "../wasm/sim_wasm";
import { isRetryableKick } from "../game/net";
import { CHAT_DEFAULT_SIZE, CHAT_MIN_SIZE, clampChatSize, dragChatSize, kickNotice, quoteName, rejectionText } from "./chat";
import {
  ALWAYS_IN_ALL,
  CHAT_ALL_DEFAULTS,
  CHAT_TABS,
  channelLabel,
  emptyTabText,
  inputChannel,
  lineView,
  npcLineView,
  ownTab,
  parseChatAll,
  parseTab,
  shownOn,
  stepTab,
  tabChannel,
  type ChatAllFilter,
  type ChatLineKind,
  type ChatTab,
} from "./chat-state";

const KINDS: ChatLineKind[] = ["say", "house", "whisper", "party", "club", "npc", "system"];
const labels = { houseName: "Roarwing", clubTag: "OWL" };
/** The tabs that show a line of `kind` (and `about`). */
const tabsShowing = (kind: ChatLineKind, filter: ChatAllFilter = CHAT_ALL_DEFAULTS, about?: ChatTab) => CHAT_TABS.filter((t) => shownOn(t, kind, about, filter));

describe("the chat's tabs", () => {
  it("All shows every kind of line; each other tab only its own channel", () => {
    expect(tabsShowing("say")).toEqual(["all"]);
    expect(tabsShowing("npc")).toEqual(["all"]);
    expect(tabsShowing("system")).toEqual(["all"]);
    expect(tabsShowing("house")).toEqual(["all", "house"]);
    expect(tabsShowing("party")).toEqual(["all", "party"]);
    expect(tabsShowing("club")).toEqual(["all", "club"]);
    expect(tabsShowing("whisper")).toEqual(["all", "whisper"]);
  });

  it("shows a notice about a channel on that channel's tab too", () => {
    expect(tabsShowing("system", CHAT_ALL_DEFAULTS, "party")).toEqual(["all", "party"]);
    expect(tabsShowing("system", CHAT_ALL_DEFAULTS, "all")).toEqual(["all"]);
    expect(ownTab("whisper")).toBe("whisper");
    expect(ownTab("say")).toBeNull();
  });

  it("sends a plain line to the tab's channel (All: say)", () => {
    expect(CHAT_TABS.map(tabChannel)).toEqual(["say", "house", "party", "club", "whisper"]);
  });

  it("keeps the indicator in step with the tab, a command overriding it", () => {
    expect(inputChannel("hello", "all")).toBe("say");
    expect(inputChannel("hello", "party")).toBe("party");
    expect(inputChannel("", "club")).toBe("club");
    expect(inputChannel("/s hi", "party")).toBe("say");
    expect(inputChannel("/p hi", "house")).toBe("party");
    expect(inputChannel("/h hi", "whisper")).toBe("house");
    expect(inputChannel("/w Tom hi", "all")).toBe("whisper");
    expect(inputChannel("/c hi", "all")).toBe("club");
    expect(inputChannel("/invite Tom", "club")).toBe("club");
    expect(channelLabel("house", "Roarwing", null)).toBe("Roarwing");
    expect(channelLabel("whisper", "", "Basil")).toBe("To Basil");
    expect(channelLabel("whisper", "", null)).toBe("Whisper");
  });

  it("remembers only a real tab", () => {
    for (const t of CHAT_TABS) expect(parseTab(t)).toBe(t);
    expect(parseTab(null)).toBe("all");
    expect(parseTab("npc")).toBe("all");
    expect(parseTab(3)).toBe("all");
  });

  it("moves with the arrow keys, Home and End, wrapping round", () => {
    expect(stepTab("all", "ArrowRight")).toBe("house");
    expect(stepTab("whisper", "ArrowRight")).toBe("all");
    expect(stepTab("all", "ArrowLeft")).toBe("whisper");
    expect(stepTab("party", "Home")).toBe("all");
    expect(stepTab("party", "End")).toBe("whisper");
    expect(stepTab("party", "a")).toBeNull();
  });

  it("says when a tab has nothing", () => {
    expect(emptyTabText("party")).toBe("No party messages yet.");
    expect(emptyTabText("whisper")).toMatch(/No whispers yet/);
  });
});

describe("Settings > Chats (the All tab's filter)", () => {
  it("shows every channel in All by default", () => {
    expect(CHAT_ALL_DEFAULTS).toEqual({ house: true, party: true, club: true, whisper: true });
    expect(parseChatAll(undefined)).toEqual(CHAT_ALL_DEFAULTS);
    for (const k of KINDS) expect(shownOn("all", k, undefined, CHAT_ALL_DEFAULTS)).toBe(true);
  });

  it("takes an unticked channel out of All but not out of its own tab", () => {
    const f = parseChatAll({ party: false, whisper: false });
    expect(f).toEqual({ house: true, party: false, club: true, whisper: false });
    expect(tabsShowing("party", f)).toEqual(["party"]);
    expect(tabsShowing("whisper", f)).toEqual(["whisper"]);
    expect(tabsShowing("house", f)).toEqual(["all", "house"]);
    expect(tabsShowing("club", f)).toEqual(["all", "club"]);
  });

  it("always shows say, NPC and system lines in All, whatever is stored", () => {
    expect(ALWAYS_IN_ALL.map((a) => a.kind)).toEqual(["say", "npc", "system"]);
    const stored = parseChatAll({ say: false, npc: false, system: false });
    expect(stored).toEqual(CHAT_ALL_DEFAULTS);
    // Even a filter object that names them (not one parseChatAll makes).
    const forced = { ...CHAT_ALL_DEFAULTS, say: false, npc: false, system: false } as ChatAllFilter;
    for (const k of ["say", "npc", "system"] as const) expect(shownOn("all", k, undefined, forced)).toBe(true);
  });

  it("reads anything but false as shown", () => {
    expect(parseChatAll({ house: "no", party: 0, club: null })).toEqual(CHAT_ALL_DEFAULTS);
    expect(parseChatAll("rubbish")).toEqual(CHAT_ALL_DEFAULTS);
  });
});

describe("chat line kinds", () => {
  it("labels each channel", () => {
    expect(lineView({ channel: "say", from: "Tom", text: "hi" }, labels)).toEqual({ kind: "say", who: "Tom: " });
    expect(lineView({ channel: "house", from: "Tom", text: "hi" }, labels)).toEqual({ kind: "house", who: "[Roarwing] Tom: " });
    expect(lineView({ channel: "party", from: "Tom", text: "hi" }, labels)).toEqual({ kind: "party", who: "[Party] Tom: " });
    expect(lineView({ channel: "club", from: "Tom", text: "hi" }, labels)).toEqual({ kind: "club", who: "[OWL] Tom: " });
    expect(lineView({ channel: "club", from: "Tom", text: "hi" }, { houseName: "", clubTag: null }).who).toBe("[Club] Tom: ");
    expect(lineView({ channel: "whisper", from: "Tom", text: "hi" }, labels).who).toBe("Tom whispers: ");
    expect(lineView({ channel: "whisper", to: "Tom", outgoing: true, text: "hi" }, labels).who).toBe("To Tom: ");
    expect(lineView({ channel: "system", text: "Welcome" }, labels)).toEqual({ kind: "system", who: null });
  });

  it("gives residents' words their own kind, shown in All only", () => {
    expect(npcLineView("Professor Quill")).toEqual({ kind: "npc", who: "Professor Quill: " });
    expect(tabsShowing("npc")).toEqual(["all"]);
  });

  it("never makes a player's line an NPC's, whatever the player is called", () => {
    for (const channel of ["say", "house", "whisper", "party", "club"] as const) {
      expect(lineView({ channel, from: "Professor Quill", text: "Detention!" }, labels).kind).toBe(channel);
    }
  });
});

describe("the chat's size", () => {
  const big = { width: 1600, height: 1000 };
  it("grows dragging up and to the right, shrinks dragging back", () => {
    expect(dragChatSize(CHAT_DEFAULT_SIZE, 100, -150, big)).toEqual({ width: 540, height: 340 });
    expect(dragChatSize({ width: 540, height: 340 }, -100, 150, big)).toEqual(CHAT_DEFAULT_SIZE);
  });
  it("stays between its minimum and what the window has room for", () => {
    expect(dragChatSize(CHAT_DEFAULT_SIZE, -1000, 1000, big)).toEqual(CHAT_MIN_SIZE);
    const huge = dragChatSize(CHAT_DEFAULT_SIZE, 5000, -5000, big);
    expect(huge.width).toBeLessThanOrEqual(big.width - 24);
    expect(huge.height).toBeLessThanOrEqual(big.height - 200);
    expect(clampChatSize({ width: Number.NaN, height: 200 }, big)).toEqual({ width: CHAT_MIN_SIZE.width, height: 200 });
  });
});

describe("rejectionText", () => {
  it("explains each refusal", () => {
    expect(rejectionText("rate_limited")).toMatch(/too quickly/);
    expect(rejectionText("filtered")).toMatch(/does not allow/);
    expect(rejectionText("invalid")).toMatch(/empty or too long/);
    expect(rejectionText("no_channel")).toMatch(/not in a party/);
    expect(rejectionText("no_channel", { channel: "club" })).toBe("Not sent: you're not in a club.");
  });

  it("names the whisper target that is not there", () => {
    expect(rejectionText("unknown_target", { channel: "whisper", to: "Basil" })).toBe("Not sent: Basil is not online.");
    expect(rejectionText("unknown_target")).toMatch(/nobody by that name/);
  });
});

describe("kickNotice", () => {
  const codes: KickCode[] = ["server_restart", "timeout", "too_slow", "server_full", "replaced", "banned", "protocol_error", "version_mismatch", "session_expired", "no_character", "flood"];

  it("has a heading and body for every code, keyed by code rather than the reason text", () => {
    for (const code of codes) {
      const n = kickNotice(code, "");
      expect(n.heading.length).toBeGreaterThan(0);
      expect(n.body.length).toBeGreaterThan(0);
    }
    expect(kickNotice("replaced", "whatever the server said").heading).toBe("Signed in elsewhere");
    expect(kickNotice("banned", "Suspended until Friday.").body).toBe("Suspended until Friday.");
  });

  it("retries only the temporary codes", () => {
    expect(codes.filter(isRetryableKick)).toEqual(["server_restart", "timeout", "too_slow", "server_full"]);
  });
});

describe("quoteName", () => {
  it("quotes names with spaces for /w", () => {
    expect(quoteName("Basil")).toBe("Basil");
    expect(quoteName("Anne Marie")).toBe('"Anne Marie"');
  });
});
