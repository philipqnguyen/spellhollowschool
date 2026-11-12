// The chat panel's state, kept apart from the DOM (ui/chat.ts) so it is unit
// tested: the tabs (All, House, Party, Club, Whisper), which lines each tab
// shows (All's own filter is Settings > Chats), where a line typed on a tab
// goes, and what kind each line is (docs/architecture.md, "Chat").
//
// Every line goes into one log; a tab is a view over it. A line's kind comes
// from where it came from, never from its sender's name: a player's line is
// the channel the server says it came on (`ChatChannelName`, which has no
// NPC channel), a resident's words come only through `ChatPanel.npc`, so a
// player named like a professor still chats in the say colour.

import type { ChatChannelName } from "../wasm/sim_wasm";

export type ChatTab = "all" | "house" | "party" | "club" | "whisper";

export const CHAT_TABS: readonly ChatTab[] = ["all", "house", "party", "club", "whisper"];

export const CHAT_TAB_LABELS: Readonly<Record<ChatTab, string>> = {
  all: "All",
  house: "House",
  party: "Party",
  club: "Club",
  whisper: "Whisper",
};

/** What a line in the log is: a player's line on a channel, a resident's (NPC's) words, or a notice. */
export type ChatLineKind = ChatChannelName | "npc" | "system";

export interface ChatLine {
  channel: ChatChannelName | "system";
  from?: string;
  /** Whisper partner: recipient of ours, sender of theirs. */
  to?: string;
  outgoing?: boolean;
  text: string;
  /** Our own line's `Chat.client_seq`, so a `ChatRejected` can find it. */
  clientSeq?: number;
  /** A notice's tab besides All (a refused party line's notice shows on Party too). */
  about?: ChatTab;
}

/** What the log draws for a line: its kind (the CSS class `chat-<kind>`) and the label before the text. */
export interface LineView {
  kind: ChatLineKind;
  who: string | null;
}

/** The kinds Settings > Chats may take out of the All tab (each keeps its own tab). */
export const ALL_FILTERABLE = ["house", "party", "club", "whisper"] as const;
export type AllFilterable = (typeof ALL_FILTERABLE)[number];
/** Settings > Chats: which of the filterable kinds the All tab shows. */
export type ChatAllFilter = Record<AllFilterable, boolean>;
export const CHAT_ALL_DEFAULTS: Readonly<ChatAllFilter> = { house: true, party: true, club: true, whisper: true };
/** The kinds All always shows, listed in Settings > Chats as checked and disabled. */
export const ALWAYS_IN_ALL: readonly { kind: "say" | "npc" | "system"; label: string }[] = [
  { kind: "say", label: "Say" },
  { kind: "npc", label: "NPC" },
  { kind: "system", label: "System" },
];

/** A stored filter: every filterable kind is shown unless stored as `false`; anything else stored is ignored. */
export function parseChatAll(raw: unknown): ChatAllFilter {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  return { house: o.house !== false, party: o.party !== false, club: o.club !== false, whisper: o.whisper !== false };
}

export interface LineLabels {
  houseName: string;
  /** Our club's tag (null: in no club). */
  clubTag: string | null;
}

/** The channel tab (not All) that shows a line of `kind` too: its own; a notice's `about`; none for say and residents' words. */
export function ownTab(kind: ChatLineKind, about?: ChatTab): ChatTab | null {
  switch (kind) {
    case "house":
    case "party":
    case "club":
    case "whisper":
      return kind;
    case "system":
      return about && about !== "all" ? about : null;
    case "say":
    case "npc":
      return null;
  }
}

/**
 * Whether `tab` shows a line of `kind`. All shows everything but the kinds
 * `allFilter` turns off (say, residents' words and notices always); a
 * channel tab shows its own lines, whatever `allFilter` says.
 */
export function shownOn(tab: ChatTab, kind: ChatLineKind, about: ChatTab | undefined, allFilter: ChatAllFilter): boolean {
  if (tab !== "all") return ownTab(kind, about) === tab;
  return (ALL_FILTERABLE as readonly string[]).includes(kind) ? allFilter[kind as AllFilterable] !== false : true;
}

/** What an empty tab says. */
export function emptyTabText(tab: ChatTab): string {
  switch (tab) {
    case "all":
      return "Nothing to show yet.";
    case "whisper":
      return "No whispers yet. /w Name message whispers to someone.";
    default:
      return `No ${CHAT_TAB_LABELS[tab].toLowerCase()} messages yet.`;
  }
}

/** A player's line (ours, or one the server sent) or a notice. Never an NPC's: see `npcLineView`. */
export function lineView(line: ChatLine, labels: LineLabels): LineView {
  const kind: ChatLineKind = line.channel;
  const who =
    kind === "system"
      ? null
      : kind === "whisper"
        ? line.outgoing
          ? `To ${line.to}: `
          : `${line.from} whispers: `
        : kind === "house"
          ? `[${labels.houseName || "House"}] ${line.from}: `
          : kind === "party"
            ? `[Party] ${line.from}: `
            : kind === "club"
              ? `[${labels.clubTag ?? "Club"}] ${line.from}: `
              : `${line.from}: `;
  return { kind, who };
}

/** A resident's words (a professor in class): apricot, All only. */
export function npcLineView(from: string): LineView {
  return { kind: "npc", who: `${from}: ` };
}

/** Where a line typed without a command goes on `tab` (All: say). Slash commands override it. */
export function tabChannel(tab: ChatTab): ChatChannelName {
  return tab === "all" ? "say" : tab;
}

/** The tab remembered in storage, or All for anything else. */
export function parseTab(raw: unknown): ChatTab {
  return typeof raw === "string" && (CHAT_TABS as readonly string[]).includes(raw) ? (raw as ChatTab) : "all";
}

/** The tab an arrow, Home or End key moves to from `tab` (wrapping), or null for another key. */
export function stepTab(tab: ChatTab, key: string): ChatTab | null {
  const i = CHAT_TABS.indexOf(tab);
  const n = CHAT_TABS.length;
  switch (key) {
    case "ArrowRight":
      return CHAT_TABS[(i + 1) % n]!;
    case "ArrowLeft":
      return CHAT_TABS[(i + n - 1) % n]!;
    case "Home":
      return CHAT_TABS[0]!;
    case "End":
      return CHAT_TABS[n - 1]!;
    default:
      return null;
  }
}

/**
 * The channel the input line will go to, for the indicator before it: a chat
 * command's (/h, /w, /r, /p, /c, /s), else the tab's.
 */
export function inputChannel(input: string, tab: ChatTab): ChatChannelName {
  const v = input.trimStart().toLowerCase();
  if (/^\/(h|house)\b/.test(v)) return "house";
  if (/^\/(w|whisper|tell|r|reply)\b/.test(v)) return "whisper";
  if (/^\/(p|party)\s/.test(v)) return "party";
  if (/^\/(c|club)\s/.test(v)) return "club";
  if (/^\/(s|say)\b/.test(v)) return "say";
  return tabChannel(tab);
}

/** The indicator's words for `channel`; the Whisper tab's default names who it goes to. */
export function channelLabel(channel: ChatChannelName, houseName: string, whisperTo: string | null): string {
  switch (channel) {
    case "house":
      return houseName || "House";
    case "whisper":
      return whisperTo ? `To ${whisperTo}` : "Whisper";
    case "party":
      return "Party";
    case "club":
      return "Club";
    case "say":
      return "Say";
  }
}
