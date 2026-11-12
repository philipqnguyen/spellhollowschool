// The party as the HUD shows it (docs/architecture.md "Parties"): the list's
// rows in a fixed order (the leader first, then the others as they joined),
// who is here, elsewhere or offline, the words for places, notices and
// refusals, and the party chat commands. Pure (no DOM, no wasm), unit tested
// in party-state.test.ts; party.ts draws it and game/party.ts wires it.

import type { House, PartyErrorCode, PartyMember, PartyNotice, PartyPlace, PartyView } from "../wasm/sim_wasm";
import { sameName } from "./players-state";

/** Where a member is, seen from us: in our zone, in another one, or offline. */
export type MemberStatus = "here" | "elsewhere" | "offline";

/** One row of the party list. */
export interface PartyRow {
  characterId: number;
  name: string;
  house: House;
  level: number;
  leader: boolean;
  /** This is us. */
  self: boolean;
  status: MemberStatus;
  /** Where they are, in words ("Great Hall", "in a dorm"); null when offline. */
  where: string | null;
  /** Their entity while online (their nameplate, their dot on the map). */
  entity: number | null;
}

/** Display names of the places a member can be in (class and venue ids from the data files). */
export interface PlaceNames {
  className(id: string): string | undefined;
  venueName(id: string): string | undefined;
}

/** A place in words, as the party list says it. */
export function placeText(place: PartyPlace, names: PlaceNames, myName: string): string {
  switch (place.kind) {
    case "castle":
      return place.place || "In the castle";
    case "dorm":
      return sameName(place.ownerName, myName) ? "In your dorm room" : `In ${place.ownerName}'s dorm room`;
    case "wishingRoom":
      return sameName(place.ownerName, myName) ? "In your Wishing Room" : `In ${place.ownerName}'s Wishing Room`;
    case "class":
      return `In class: ${names.className(place.class) ?? place.class}`;
    case "arena":
      return `In a duel at the ${names.venueName(place.venue) ?? place.venue}`;
  }
}

/** Our own entry of `view` (by character id), if we are in it. */
export function selfMember(view: PartyView | null, myCharacterId: number): PartyMember | undefined {
  return view?.members.find((m) => m.characterId === myCharacterId);
}

/**
 * The list's rows: the leader first, then the others in the server's order
 * (the order they joined), each with where they are seen from us.
 */
export function partyRows(view: PartyView | null, myCharacterId: number, myName: string, names: PlaceNames): PartyRow[] {
  if (!view) return [];
  const me = selfMember(view, myCharacterId);
  const myZone = me?.online?.zone ?? null;
  const leader = view.members.filter((m) => m.characterId === view.leader);
  const others = view.members.filter((m) => m.characterId !== view.leader);
  return [...leader, ...others].map((m) => {
    const status: MemberStatus = !m.online ? "offline" : m.characterId === myCharacterId || m.online.zone === myZone ? "here" : "elsewhere";
    return {
      characterId: m.characterId,
      name: m.name,
      house: m.house,
      level: m.level,
      leader: m.characterId === view.leader,
      self: m.characterId === myCharacterId,
      status,
      where: m.online ? placeText(m.online.place, names, myName) : null,
      entity: m.online?.entity ?? null,
    };
  });
}

/** The entities of the other members online in our zone (drawn in the world: blue nameplates, map dots). */
export function membersHere(view: PartyView | null, myCharacterId: number): Set<number> {
  const out = new Set<number>();
  const myZone = selfMember(view, myCharacterId)?.online?.zone;
  if (!view || myZone === undefined) return out;
  for (const m of view.members) {
    if (m.characterId !== myCharacterId && m.online && m.online.zone === myZone) out.add(m.online.entity);
  }
  return out;
}

/** A notice for the chat log; `myName` turns "Ada left" into "You left" when it is us. */
export function partyNoticeText(notice: PartyNotice, myName: string): string {
  const me = "name" in notice && sameName(notice.name, myName);
  switch (notice.kind) {
    case "inviteSent":
      return `You invited ${notice.name} to your party.`;
    case "declined":
      return `${notice.name} declined your party invitation.`;
    case "joined":
      return me ? "You joined the party. Type /p and a message to talk to it." : `${notice.name} joined the party.`;
    case "left":
      return me ? "You left the party." : `${notice.name} left the party.`;
    case "removed":
      return me ? "You were removed from the party." : `${notice.name} was removed from the party.`;
    case "leader":
      return me ? "You lead the party now." : `${notice.name} leads the party now.`;
    case "disbanded":
      return "Everyone else has left, so the party is over.";
  }
}

/** Why a party action was refused, for the chat log (`name`: who it was about, when known). */
export function partyErrorText(code: PartyErrorCode, name?: string): string {
  switch (code) {
    case "not_online":
      return name ? `${name} is not online.` : "Nobody by that name is online.";
    case "self_invite":
      return "You can't invite yourself.";
    case "in_party":
      return name ? `${name} is already in a party.` : "Leave your party first (/leave).";
    case "full":
      return "The party is full (six at most).";
    case "not_in_party":
      return "You're not in a party.";
    case "not_leader":
      return "Only the party's leader can do that.";
    case "not_member":
      return name ? `${name} is not in your party.` : "That character is not in your party.";
    case "invite_expired":
      return "That party invitation has run out.";
    case "blocked":
      return name ? `You have blocked ${name}; unblock them to invite them.` : "You have blocked that character.";
    case "rate_limited":
      return "Too quick: wait a moment and try again.";
  }
}

/** A party command typed in the chat box. */
export type PartyCommand =
  | { verb: "invite"; name: string }
  | { verb: "leave" }
  | { verb: "kick"; name: string }
  | { verb: "promote"; name: string }
  /** Asked for help (`/party` with nothing after it). */
  | { verb: "help" };

export const PARTY_HELP = "Party: /invite Name (or /pinvite), /p message, /leave (or /pleave), /pkick Name, /ppromote Name.";

/**
 * The party's local commands: /invite and /pinvite Name, /leave and /pleave,
 * /pkick Name, /ppromote Name, and /party or /p alone (help). Quotes around a
 * name are dropped. Null when `line` is none of them (/p and /party with a
 * message are party chat: chatparse.ts).
 */
export function parsePartyCommand(line: string): PartyCommand | { error: string } | null {
  const m = /^\/([a-z]+)(?:\s+(.*))?$/is.exec(line.trim());
  if (!m) return null;
  const verb = m[1]!.toLowerCase();
  const arg = (m[2] ?? "").trim().replace(/^"(.*)"$/s, "$1").trim();
  switch (verb) {
    case "invite":
    case "pinvite":
      return arg ? { verb: "invite", name: arg } : { error: "Invite whom? Type /invite Name." };
    case "leave":
    case "pleave":
      return { verb: "leave" };
    case "pkick":
      return arg ? { verb: "kick", name: arg } : { error: "Remove whom? Type /pkick Name." };
    case "ppromote":
      return arg ? { verb: "promote", name: arg } : { error: "Who should lead? Type /ppromote Name." };
    case "p":
    case "party":
      return arg ? null : { verb: "help" };
    default:
      return null;
  }
}

/** The member of `view` called `name` (case folded), if any. */
export function memberNamed(view: PartyView | null, name: string): PartyMember | undefined {
  return view?.members.find((m) => sameName(m.name, name));
}
