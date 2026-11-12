// Parties in the running game (docs/architecture.md "Parties"): the party
// the server last sent, its list at the HUD's top left (ui/party.ts),
// invitation toasts, the chat commands (/invite, /leave, /pkick,
// /ppromote; /p is party chat, chatparse.ts), the notices and refusals in
// the chat log, which characters in view wear party-blue nameplates, and the
// members' pins for the map.

import { audio } from "../audio";
import { encodePartyInvite, encodePartyKick, encodePartyLeave, encodePartyPromote, encodePartyRespond, type PartyPlace, type PartyView, type ServerMessage } from "../wasm/sim_wasm";
import { houseInfo } from "../content/houses";
import { InviteToasts } from "../ui/invites";
import { PartyPanel } from "../ui/party";
import { membersHere, memberNamed, parsePartyCommand, PARTY_HELP, partyErrorText, partyNoticeText, partyRows, selfMember, type PlaceNames } from "../ui/party-state";
import type { GroupMembership } from "../ui/groups";
import type { PartyMapMember } from "../ui/map-state";
import { scheduledClass } from "./clock/schedule";
import { venue } from "./dueling/content";
import { sameName } from "../ui/players-state";

export interface PartyHubOptions {
  hudRoot: HTMLElement;
  myCharacterId: number;
  myName: string;
  /** The most members of a party (`protocolConstants().partyMax`). */
  max: number;
  send: (frame: Uint8Array) => void;
  notify: (text: string) => void;
  /** Opens the chat box with `/w Name `. */
  whisper: (name: string) => void;
  /** Where a character in view stands (null when not drawn). */
  poseOf: (entity: number) => { x: number; z: number; surfaceId: string | undefined } | null;
  /** A key place's spot on the castle map (`data/world/key_places.json` id): a class door, the Dueling Club. */
  keyPlace: (id: string) => { x: number; y: number; z: number; surfaceId: string | undefined } | null;
  /** Where house `house`'s dorm rooms are entered, for the map (`surfaceId` undefined when it is ground our house does not walk). */
  dormDoor: (house: string) => { x: number; y: number; z: number; surfaceId: string | undefined } | null;
}

const NAMES: PlaceNames = {
  className: (id) => scheduledClass(id)?.name,
  venueName: (id) => venue(id)?.name,
};

export class PartyHub {
  private view: PartyView | null = null;
  private here = new Set<number>();
  private readonly panel: PartyPanel;
  private readonly toasts: InviteToasts;
  /** The name of the last character invited, kicked or promoted: a refusal names them. */
  private lastTarget: string | undefined;
  /** For nameplates (`groups.ts`): the members drawn with us. */
  readonly membership: GroupMembership = { kind: "party", has: (id) => this.here.has(id) };

  constructor(private readonly opts: PartyHubOptions) {
    this.panel = new PartyPanel({
      onWhisper: (name) => opts.whisper(name),
      onLeave: () => this.leave(),
      onKick: (id, name) => this.kick(name, id),
      onPromote: (id, name) => this.promote(name, id),
    });
    this.toasts = new InviteToasts({
      onAccept: (id) => opts.send(encodePartyRespond(id, true)),
      onDecline: (id) => opts.send(encodePartyRespond(id, false)),
      what: (extra) => `${extra ? ` ${extra}` : ""} invites you to join their party.`,
    });
    opts.hudRoot.append(this.panel.root, this.toasts.root);
  }

  /** The list's element (the HUD keeps nameplates clear of it). */
  get root(): HTMLElement {
    return this.panel.root;
  }

  get party(): PartyView | null {
    return this.view;
  }

  /** In a party with someone. */
  get inParty(): boolean {
    return this.view !== null;
  }

  /** Whether `name` is a member of our party. */
  isMember(name: string): boolean {
    return memberNamed(this.view, name) !== undefined;
  }

  /** The party's messages; true when `msg` was one. */
  onMessage(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "partyInvited": {
        audio.play("ui.invite");
        const house = houseInfo(msg.fromHouse);
        this.toasts.add(msg.inviteId, msg.fromName, msg.expiresInSecs, `(level ${msg.fromLevel}, ${house.name})`);
        this.opts.notify(`${msg.fromName} invites you to join their party.`);
        return true;
      }
      case "partyState":
        this.view = msg.party;
        this.refresh();
        return true;
      case "partyNotice":
        this.opts.notify(partyNoticeText(msg.notice, this.opts.myName));
        return true;
      case "partyError":
        audio.play("ui.error");
        this.opts.notify(partyErrorText(msg.code, this.lastTarget));
        return true;
      case "welcome":
        // A new connection: the server sends the party again if there is one.
        this.view = null;
        this.refresh();
        return false;
      default:
        return false;
    }
  }

  /** The list and the nameplates follow the party and where everyone is. */
  private refresh(): void {
    this.here = membersHere(this.view, this.opts.myCharacterId);
    this.panel.update(partyRows(this.view, this.opts.myCharacterId, this.opts.myName, NAMES), this.opts.max);
  }

  /** Invites `name` into our party (or a new one we lead). */
  invite(name: string): void {
    const who = name.trim();
    if (!who) return;
    if (sameName(who, this.opts.myName)) {
      this.opts.notify(partyErrorText("self_invite"));
      return;
    }
    if (this.isMember(who)) {
      this.opts.notify(`${who} is already in your party.`);
      return;
    }
    if (this.view && this.view.members.length >= this.opts.max) {
      this.opts.notify(partyErrorText("full"));
      return;
    }
    this.lastTarget = who;
    this.opts.send(encodePartyInvite(who));
  }

  leave(): void {
    if (!this.view) {
      this.opts.notify(partyErrorText("not_in_party"));
      return;
    }
    this.opts.send(encodePartyLeave());
  }

  private kick(name: string, characterId = memberNamed(this.view, name)?.characterId): void {
    if (!this.view) return this.opts.notify(partyErrorText("not_in_party"));
    if (characterId === undefined) return this.opts.notify(partyErrorText("not_member", name));
    this.lastTarget = name;
    this.opts.send(encodePartyKick(characterId));
  }

  private promote(name: string, characterId = memberNamed(this.view, name)?.characterId): void {
    if (!this.view) return this.opts.notify(partyErrorText("not_in_party"));
    if (characterId === undefined) return this.opts.notify(partyErrorText("not_member", name));
    this.lastTarget = name;
    this.opts.send(encodePartyPromote(characterId));
  }

  /** Chat commands: /invite, /pinvite, /leave, /pleave, /pkick, /ppromote, and /party alone (help). True if `line` was one. */
  command(line: string): boolean {
    const cmd = parsePartyCommand(line);
    if (!cmd) return false;
    if ("error" in cmd) {
      this.opts.notify(cmd.error);
      return true;
    }
    switch (cmd.verb) {
      case "invite":
        this.invite(cmd.name);
        break;
      case "leave":
        this.leave();
        break;
      case "kick":
        this.kick(cmd.name);
        break;
      case "promote":
        this.promote(cmd.name);
        break;
      case "help":
        this.opts.notify(PARTY_HELP);
        break;
    }
    return true;
  }

  /**
   * The members for the map (not us): those drawn with us at their spot,
   * those elsewhere at their place's entrance (a class door, the Dueling
   * Club, the way into the dorm rooms: `dormDoor`) with a note, those in the castle but
   * not drawn by their place's name. A Wishing Room is a secret: listed, never pinned.
   */
  mapMembers(): PartyMapMember[] {
    const view = this.view;
    if (!view) return [];
    const myZone = selfMember(view, this.opts.myCharacterId)?.online?.zone;
    const out: PartyMapMember[] = [];
    for (const m of view.members) {
      if (m.characterId === this.opts.myCharacterId || !m.online) continue;
      const o = m.online;
      if (o.zone === myZone && o.place.kind === "castle") {
        const at = this.opts.poseOf(o.entity);
        out.push(at ? { name: m.name, at } : { name: m.name, place: o.place.place });
        continue;
      }
      const entrance = this.entranceOf(o.place);
      if (entrance) out.push({ name: m.name, entrance: { ...entrance.spot, note: entrance.note } });
      else if (o.place.kind === "castle") out.push({ name: m.name, place: o.place.place });
    }
    return out;
  }

  private entranceOf(place: PartyPlace): { spot: { x: number; y: number; z: number; surfaceId: string | undefined }; note: string } | null {
    const at = (id: string, note: string) => {
      const spot = this.opts.keyPlace(id);
      return spot ? { spot, note } : null;
    };
    switch (place.kind) {
      case "class":
        return at(`class_${place.class}`, `in ${NAMES.className(place.class) ?? place.class}`);
      case "dorm": {
        const spot = this.opts.dormDoor(place.house);
        return spot ? { spot, note: "in a dorm room" } : null;
      }
      case "arena":
        return at(place.venue, "dueling");
      default:
        return null;
    }
  }

  dispose(): void {
    this.toasts.dispose();
    this.panel.root.remove();
    this.toasts.root.remove();
  }
}
