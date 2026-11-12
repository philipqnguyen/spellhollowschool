// Clubs as the client shows them (docs/architecture.md "Clubs"): the rules
// from data/clubs.json, what the clubs secretary says (which line, filled
// in), the club panel's rows and who may do what there, presence updates,
// the words for notices and refusals, the club chat commands and the names
// that wear club-green nameplates. Pure (no DOM, no wasm), unit tested in
// club-state.test.ts; ui/club.ts and ui/club-desk.ts draw it, game/club.ts
// wires it.

import clubsJson from "../../../data/clubs.json";
import type { ClubErrorCode, ClubMember, ClubNotice, ClubRole, ClubView, House } from "../wasm/sim_wasm";
import { sameName } from "./players-state";

/** `data/clubs.json` (`server::clubs::ClubRules`). */
export interface ClubRules {
  found_level: number;
  found_crowns: number;
  members_max: number;
  name_chars: [number, number];
  tag_chars: [number, number];
  motd_chars_max: number;
  secretary: string;
  secretary_lines: Record<SecretaryLine, string>;
}

/** The secretary's lines (`secretary_lines`). */
export type SecretaryLine = "greeting" | "option_found" | "option_list" | "option_goodbye" | "not_level" | "not_crowns" | "not_either" | "in_club" | "eligible" | "sealed";

export const CLUB_RULES = clubsJson as unknown as ClubRules;

// ---------------------------------------------------------------- the secretary

/** What the player asking to found a club is (and has). */
export interface Founder {
  level: number;
  crowns: number;
  /** The club they are in, if any. */
  club: string | null;
}

/** Which answer "Found a club" gets. */
export type FoundCase = "in_club" | "not_level" | "not_crowns" | "not_either" | "eligible";

export function foundCase(f: Founder, rules: ClubRules = CLUB_RULES): FoundCase {
  if (f.club !== null) return "in_club";
  const level = f.level >= rules.found_level;
  const crowns = f.crowns >= rules.found_crowns;
  if (level && crowns) return "eligible";
  if (!level && !crowns) return "not_either";
  return level ? "not_crowns" : "not_level";
}

/** `template` with its `{placeholders}` filled from `vars` (unknown ones left as they are). */
export function fillLine(template: string, vars: Readonly<Record<string, string | number>>): string {
  return template.replace(/\{([a-z_]+)\}/g, (all, key: string) => (key in vars ? String(vars[key]) : all));
}

/** Secretary line `key`, filled with the founding numbers and `vars` ({level} {crowns} {short} {club}). */
export function secretaryLine(key: SecretaryLine, vars: Readonly<Record<string, string | number>> = {}, rules: ClubRules = CLUB_RULES): string {
  return fillLine(rules.secretary_lines[key], { found_level: rules.found_level, found_crowns: rules.found_crowns, ...vars });
}

/** Her answer to "Found a club": which case, and the words. */
export function foundAnswer(f: Founder, rules: ClubRules = CLUB_RULES): { kind: FoundCase; text: string } {
  const kind = foundCase(f, rules);
  const vars = { level: f.level, crowns: f.crowns, short: Math.max(0, rules.found_crowns - f.crowns), club: f.club ?? "" };
  return { kind, text: secretaryLine(kind, vars, rules) };
}

// ---------------------------------------------------------------- names

/** A letter as character names have them (Latin, with accents). */
function isLetter(c: string): boolean {
  return /[A-Za-z]/.test(c) || (c >= "À" && c <= "ſ" && c !== "×" && c !== "÷");
}

const isWord = (c: string) => isLetter(c) || /[0-9]/.test(c);

/** A club name as the server stores it, or why not ("name_invalid"); the word filter is the server's. */
export function cleanName(raw: string, rules: ClubRules = CLUB_RULES): { ok: true; value: string } | { ok: false; code: ClubErrorCode } {
  const name = raw.trim().split(/\s+/).join(" ");
  const chars = [...name];
  const [lo, hi] = rules.name_chars;
  const joiner = (c: string) => c === "'" || c === "-";
  const ok =
    chars.length >= lo &&
    chars.length <= hi &&
    chars.every((c) => isWord(c) || c === " " || joiner(c) || c === "&") &&
    isWord(chars[0] ?? "") &&
    isWord(chars[chars.length - 1] ?? "") &&
    chars.every((c, i) => i === 0 || !(joiner(c) && joiner(chars[i - 1]!)));
  return ok ? { ok: true, value: name } : { ok: false, code: "name_invalid" };
}

/** A tag as the server stores it (upper case), or why not. */
export function cleanTag(raw: string, rules: ClubRules = CLUB_RULES): { ok: true; value: string } | { ok: false; code: ClubErrorCode } {
  const tag = raw.trim();
  const [lo, hi] = rules.tag_chars;
  return tag.length >= lo && tag.length <= hi && /^[A-Za-z0-9]+$/.test(tag) ? { ok: true, value: tag.toUpperCase() } : { ok: false, code: "tag_invalid" };
}

// ---------------------------------------------------------------- the club

/** One row of the club panel's roster. */
export interface ClubRow {
  characterId: number;
  name: string;
  house: House;
  level: number;
  role: ClubRole;
  online: boolean;
  self: boolean;
}

/** The roster in the server's order (the owner, officers, members; each longest-serving first). */
export function clubRows(view: ClubView | null, myCharacterId: number): ClubRow[] {
  return (view?.members ?? []).map((m) => ({ ...m, self: m.characterId === myCharacterId }));
}

/** Members online, of how many. */
export function onlineCount(view: ClubView | null): { online: number; total: number } {
  const members = view?.members ?? [];
  return { online: members.filter((m) => m.online).length, total: members.length };
}

export function myRole(view: ClubView | null, myCharacterId: number): ClubRole | null {
  return view?.members.find((m) => m.characterId === myCharacterId)?.role ?? null;
}

const RANK: Record<ClubRole, number> = { member: 0, officer: 1, owner: 2 };

/** Officers and the owner invite. */
export function canInvite(me: ClubRole | null): boolean {
  return me !== null && RANK[me] >= RANK.officer;
}

/** Officers remove members; the owner anyone else. */
export function canKick(me: ClubRole | null, target: ClubRole): boolean {
  if (me === "owner") return target !== "owner";
  return me === "officer" && target === "member";
}

/** Officers and the owner set the message of the day. */
export function canSetMotd(me: ClubRole | null): boolean {
  return canInvite(me);
}

/** The owner appoints and dismisses officers, hands the club on, renames and disbands it. */
export function isOwner(me: ClubRole | null): boolean {
  return me === "owner";
}

/** What may be done to `row` from the panel, given our rank. */
export function rowActions(row: ClubRow, me: ClubRole | null): ("whisper" | "promote" | "demote" | "transfer" | "kick" | "leave")[] {
  if (row.self) return ["leave"];
  const out: ("whisper" | "promote" | "demote" | "transfer" | "kick" | "leave")[] = [];
  if (row.online) out.push("whisper");
  if (isOwner(me) && row.role === "member") out.push("promote");
  if (isOwner(me) && row.role === "officer") out.push("demote");
  if (isOwner(me)) out.push("transfer");
  if (canKick(me, row.role)) out.push("kick");
  return out;
}

/** `view` with one member's presence changed (`ServerMsg::ClubPresence`); the same view when they are not in it. */
export function applyPresence(view: ClubView | null, p: { characterId: number; online: boolean; level: number }): ClubView | null {
  if (!view || !view.members.some((m) => m.characterId === p.characterId)) return view;
  return { ...view, members: view.members.map((m): ClubMember => (m.characterId === p.characterId ? { ...m, online: p.online, level: p.level } : m)) };
}

/** The other members online, by folded name: whose nameplates are club green. */
export function onlineNames(view: ClubView | null, myCharacterId: number): Set<string> {
  const out = new Set<string>();
  for (const m of view?.members ?? []) if (m.online && m.characterId !== myCharacterId) out.add(m.name.toLocaleLowerCase());
  return out;
}

export function memberNamed(view: ClubView | null, name: string): ClubMember | undefined {
  return view?.members.find((m) => sameName(m.name, name));
}

export const ROLE_LABELS: Readonly<Record<ClubRole, string>> = { owner: "President", officer: "Officer", member: "Member" };

// ---------------------------------------------------------------- words

/** A notice for the chat log; `myName` turns "Ada left" into "You left" when it is us. */
export function clubNoticeText(notice: ClubNotice, myName: string): string {
  const me = "name" in notice && sameName(notice.name, myName);
  switch (notice.kind) {
    case "founded":
      return `${notice.club} is on the school's register. Invite members from their card or with /cinvite Name; /c talks to the club.`;
    case "inviteSent":
      return `You invited ${notice.name} to the club.`;
    case "declined":
      return `${notice.name} declined your club invitation.`;
    case "joined":
      return me ? "You joined the club. Type /c and a message to talk to it." : `${notice.name} joined the club.`;
    case "left":
      return me ? "You left the club." : `${notice.name} left the club.`;
    case "removed":
      return me ? "You were removed from the club." : `${notice.name} was removed from the club.`;
    case "promoted":
      return me ? "You are an officer of the club now." : `${notice.name} is an officer of the club now.`;
    case "demoted":
      return me ? "You are no longer an officer of the club." : `${notice.name} is no longer an officer of the club.`;
    case "owner":
      return me ? "You are the club's president now." : `${notice.name} is the club's president now.`;
    case "renamed":
      return `The club is now called ${notice.club} [${notice.tag}].`;
    case "motd":
      return me ? "You changed the club's message of the day." : `${notice.name} changed the club's message of the day.`;
    case "disbanded":
      return `${notice.club} has been disbanded.`;
  }
}

/** Why a club action was refused (`name`: who it was about, when known). */
export function clubErrorText(code: ClubErrorCode, name?: string, rules: ClubRules = CLUB_RULES): string {
  switch (code) {
    case "not_online":
      return name ? `${name} is not online.` : "Nobody by that name is online.";
    case "self_invite":
      return "You can't invite yourself.";
    case "in_club":
      return name ? `${name} is already in a club.` : "They are already in a club.";
    case "already_in_club":
      return "You're already in a club. Leave it first (/cleave).";
    case "full":
      return `The club is full (${rules.members_max} members at most).`;
    case "not_in_club":
      return "You're not in a club.";
    case "not_allowed":
      return "Your rank in the club doesn't allow that.";
    case "not_member":
      return name ? `${name} is not in your club.` : "That character is not in your club.";
    case "invite_expired":
      return "That club invitation has run out.";
    case "blocked":
      return name ? `You have blocked ${name}; unblock them to invite them.` : "You have blocked that character.";
    case "rate_limited":
      return "Too quick: wait a moment and try again.";
    case "name_invalid":
      return `A club name is ${rules.name_chars[0]} to ${rules.name_chars[1]} letters, digits, spaces, apostrophes, hyphens or ampersands, starting and ending with a letter or digit.`;
    case "tag_invalid":
      return `A tag is ${rules.tag_chars[0]} to ${rules.tag_chars[1]} letters or digits.`;
    case "filtered":
      return "That has a word the school doesn't allow. Try another.";
    case "name_taken":
      return "Another club already has that name.";
    case "tag_taken":
      return "Another club already uses that tag.";
    case "motd_invalid":
      return `The message of the day is ${rules.motd_chars_max} characters at most.`;
    case "level_too_low":
      return `Founding a club needs level ${rules.found_level}.`;
    case "not_enough_crowns":
      return `Founding a club costs ${rules.found_crowns} Crowns.`;
    case "too_far":
      return "Step up to the secretary's desk first.";
    case "failed":
      return "Something went wrong; nothing changed. Try again in a moment.";
  }
}

// ---------------------------------------------------------------- commands

/** A club command typed in the chat box. */
export type ClubCommand = { verb: "invite"; name: string } | { verb: "leave" } | { verb: "help" };

export const CLUB_HELP = "Club: /c message (or /club), /cinvite Name (officers), /cleave. The club window is on the menu bar.";

/**
 * The club's local commands: /cinvite Name, /cleave, and /c or /club alone
 * (help). Quotes around a name are dropped. Null when `line` is none of them
 * (/c and /club with a message are club chat: chatparse.ts).
 */
export function parseClubCommand(line: string): ClubCommand | { error: string } | null {
  const m = /^\/([a-z]+)(?:\s+(.*))?$/is.exec(line.trim());
  if (!m) return null;
  const verb = m[1]!.toLowerCase();
  const arg = (m[2] ?? "").trim().replace(/^"(.*)"$/s, "$1").trim();
  switch (verb) {
    case "cinvite":
      return arg ? { verb: "invite", name: arg } : { error: "Invite whom? Type /cinvite Name." };
    case "cleave":
      return { verb: "leave" };
    case "c":
    case "club":
      return arg ? null : { verb: "help" };
    default:
      return null;
  }
}
