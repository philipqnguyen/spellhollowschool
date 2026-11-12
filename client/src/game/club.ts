// Clubs in the running game (docs/architecture.md "Clubs"): the club the
// server last sent (kept current by presence updates), the club window
// (ui/club.ts), invitation toasts, the chat commands (/cinvite, /cleave;
// /c is club chat, chatparse.ts), the notices and refusals in the chat log,
// which characters in view wear club-green nameplates (party blue wins:
// ui/groups.ts), and the clubs secretary's desk (ui/club-desk.ts), where
// clubs are founded.

import { authedRequest } from "../api";
import { audio } from "../audio";
import {
  encodeClubDisband,
  encodeClubFound,
  encodeClubInvite,
  encodeClubKick,
  encodeClubLeave,
  encodeClubRename,
  encodeClubRespond,
  encodeClubSetMotd,
  encodeClubSetRole,
  encodeClubTransfer,
  type ClubErrorCode,
  type ClubView,
  type ServerMessage,
} from "../wasm/sim_wasm";
import { ClubPanel } from "../ui/club";
import { ClubDesk, type DeskListing } from "../ui/club-desk";
import { applyPresence, canInvite, cleanName, cleanTag, CLUB_HELP, clubErrorText, clubNoticeText, foundAnswer, memberNamed, myRole, onlineNames, parseClubCommand, secretaryLine } from "../ui/club-state";
import type { GroupMembership } from "../ui/groups";
import { InviteToasts } from "../ui/invites";
import { sameName } from "../ui/players-state";

export interface ClubHubOptions {
  hudRoot: HTMLElement;
  /** The menu bar the club button joins. */
  hudActions: HTMLElement;
  myCharacterId: number;
  myName: string;
  /** "Miss Odette Penrose, Secretary of Clubs and Societies" (the empty window names her). */
  secretary: string;
  send: (frame: Uint8Array) => void;
  notify: (text: string) => void;
  /** Opens the chat box with `/w Name `. */
  whisper: (name: string) => void;
  /** The name of the character drawn as entity `id` (null: not drawn). */
  nameOf: (id: number) => string | null;
  /** Our level and Crowns now (the secretary's answer). */
  level: () => number;
  crowns: () => number;
  /** Whether we still stand in talking reach of entity `id`. */
  inReach: (id: number) => boolean;
}

export class ClubHub {
  private view: ClubView | null = null;
  private names = new Set<string>();
  readonly panel: ClubPanel;
  private readonly desk: ClubDesk;
  private readonly toasts: InviteToasts;
  /** The secretary we talk to (her entity), while the desk is open. */
  private secretaryId: number | null = null;
  /** The name of the last character invited or acted on: a refusal names them. */
  private lastTarget: string | undefined;
  /** For nameplates (`groups.ts`): the other members online, by name. */
  readonly membership: GroupMembership = {
    kind: "club",
    has: (id) => {
      const name = this.opts.nameOf(id);
      return name !== null && this.names.has(name.toLocaleLowerCase());
    },
  };

  constructor(private readonly opts: ClubHubOptions) {
    this.panel = new ClubPanel({
      myCharacterId: opts.myCharacterId,
      secretary: opts.secretary,
      onInvite: (name) => this.invite(name),
      onWhisper: (name) => opts.whisper(name),
      onLeave: () => this.leave(),
      onKick: (id, name) => this.act(name, encodeClubKick(id)),
      onSetRole: (id, name, role) => this.act(name, encodeClubSetRole(id, role)),
      onTransfer: (id, name) => this.act(name, encodeClubTransfer(id)),
      onRename: (name, tag) => this.rename(name, tag),
      onSetMotd: (motd) => opts.send(encodeClubSetMotd(motd)),
      onDisband: () => opts.send(encodeClubDisband()),
    });
    this.desk = new ClubDesk({
      onFound: () => this.deskFound(),
      onList: () => void this.deskList(),
      onSeal: (name, tag) => this.deskSeal(name, tag),
      onClose: () => (this.secretaryId = null),
    });
    this.toasts = new InviteToasts({
      onAccept: (id) => opts.send(encodeClubRespond(id, true)),
      onDecline: (id) => opts.send(encodeClubRespond(id, false)),
      what: (extra) => ` invites you to join ${extra ?? "their club"}.`,
    });
    opts.hudActions.append(this.panel.button);
    opts.hudRoot.append(this.panel.root, this.desk.root, this.toasts.root);
  }

  get club(): ClubView | null {
    return this.view;
  }

  get inClub(): boolean {
    return this.view !== null;
  }

  /** Our club's tag, for club chat lines. */
  get tag(): string | null {
    return this.view?.tag ?? null;
  }

  /** Whether we may invite (an officer or the owner). */
  get mayInvite(): boolean {
    return canInvite(myRole(this.view, this.opts.myCharacterId));
  }

  /** Whether `name` is in our club. */
  isMember(name: string): boolean {
    return memberNamed(this.view, name) !== undefined;
  }

  /** The club's messages; true when `msg` was one. */
  onMessage(msg: ServerMessage): boolean {
    switch (msg.type) {
      case "clubInvited":
        audio.play("ui.invite");
        this.toasts.add(msg.inviteId, msg.fromName, msg.expiresInSecs, `${msg.clubName} [${msg.clubTag}]`);
        this.opts.notify(`${msg.fromName} invites you to join ${msg.clubName} [${msg.clubTag}].`);
        return true;
      case "clubState":
        this.set(msg.club);
        return true;
      case "clubPresence":
        this.set(applyPresence(this.view, msg));
        return true;
      case "clubNotice":
        if (msg.notice.kind === "founded" && this.desk.isOpen) {
          audio.play("ui.notify");
          this.desk.say(secretaryLine("sealed", { club: msg.notice.club }));
        }
        this.opts.notify(clubNoticeText(msg.notice, this.opts.myName));
        return true;
      case "clubError":
        audio.play("ui.error");
        if (this.desk.awaitingSeal) this.desk.formError(clubErrorText(msg.code));
        else this.opts.notify(clubErrorText(msg.code, this.lastTarget));
        return true;
      case "welcome":
        // A new connection: the server sends the club again if there is one.
        this.set(null);
        return false;
      case "zoneChanged":
        this.desk.close();
        return false;
      default:
        return false;
    }
  }

  private set(view: ClubView | null): void {
    this.view = view;
    this.names = onlineNames(view, this.opts.myCharacterId);
    this.panel.update(view);
  }

  /** Invites `name` into our club (officers and the owner). */
  invite(name: string): void {
    const who = name.trim();
    if (!who) return;
    if (!this.view) return this.opts.notify(clubErrorText("not_in_club"));
    if (!this.mayInvite) return this.opts.notify(clubErrorText("not_allowed"));
    if (sameName(who, this.opts.myName)) return this.opts.notify(clubErrorText("self_invite"));
    if (this.isMember(who)) return this.opts.notify(`${who} is already in your club.`);
    this.lastTarget = who;
    this.opts.send(encodeClubInvite(who));
  }

  leave(): void {
    if (!this.view) return this.opts.notify(clubErrorText("not_in_club"));
    this.opts.send(encodeClubLeave());
  }

  private act(name: string, frame: Uint8Array): void {
    this.lastTarget = name;
    this.opts.send(frame);
  }

  private rename(name: string, tag: string): void {
    const n = cleanName(name);
    const t = cleanTag(tag);
    if (!n.ok) return this.opts.notify(clubErrorText(n.code));
    if (!t.ok) return this.opts.notify(clubErrorText(t.code));
    this.opts.send(encodeClubRename(n.value, t.value));
  }

  /** Chat commands: /cinvite Name, /cleave, and /c or /club alone (help). True if `line` was one. */
  command(line: string): boolean {
    const cmd = parseClubCommand(line);
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
      case "help":
        this.opts.notify(CLUB_HELP);
        break;
    }
    return true;
  }

  // ---------------------------------------------------------------- the desk

  /** Talking to the clubs secretary (entity `id`; her `name` and `title`): her greeting and options. */
  openDesk(id: number, name: string, title: string | undefined): void {
    this.secretaryId = id;
    this.desk.open(name, title);
  }

  get deskOpen(): boolean {
    return this.desk.isOpen;
  }

  private deskFound(): void {
    const answer = foundAnswer({ level: this.opts.level(), crowns: this.opts.crowns(), club: this.view?.name ?? null });
    this.desk.say(answer.text);
    if (answer.kind === "eligible") this.desk.showForm();
  }

  private async deskList(): Promise<void> {
    this.desk.showRegister([]);
    try {
      const r = await authedRequest<{ clubs: DeskListing[] }>("GET", "/clubs");
      if (this.desk.isOpen) this.desk.showRegister(Array.isArray(r.clubs) ? r.clubs : []);
    } catch {
      if (this.desk.isOpen) this.desk.showRegister(null);
    }
  }

  private deskSeal(name: string, tag: string): void {
    const n = cleanName(name);
    const t = cleanTag(tag);
    const bad: ClubErrorCode | null = !n.ok ? n.code : !t.ok ? t.code : null;
    if (bad || !n.ok || !t.ok || this.secretaryId === null) {
      this.desk.formError(clubErrorText(bad ?? "too_far"));
      return;
    }
    this.desk.sealing();
    this.opts.send(encodeClubFound(this.secretaryId, n.value, t.value));
  }

  /** Esc closes the desk; J the club window. */
  handleKey(e: KeyboardEvent): boolean {
    return this.desk.handleKey(e) || this.panel.handleKey(e);
  }

  /** A few times a second: the desk closes once we walk away from her. */
  update(): void {
    if (this.secretaryId !== null && this.desk.isOpen && !this.opts.inReach(this.secretaryId)) this.desk.close();
  }

  dispose(): void {
    this.toasts.dispose();
    this.panel.root.remove();
    this.panel.button.remove();
    this.desk.root.remove();
    this.toasts.root.remove();
  }
}
