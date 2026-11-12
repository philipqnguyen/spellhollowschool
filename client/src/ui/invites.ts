// Incoming invitations as toasts: "Basil invites you to their room",
// Accept / Decline and a countdown until it lapses (invites-state.ts).
// Accepting takes the player straight into the room (`DormAcceptInvite`);
// declining tells the inviter (`DormDeclineInvite`). One left alone lapses.
// Party invitations use the same toasts with their own words (`what`).

import { h } from "./dom";
import { formatCountdown, InviteQueue, secondsLeft, type Invite } from "./invites-state";
import "./invites.css";

export interface InviteToastsOptions {
  onAccept: (inviteId: number) => void;
  onDecline: (inviteId: number) => void;
  /** What follows the inviter's name (`extra`: what `add` was given); a dorm room's words when omitted. */
  what?: (extra: string | null) => string;
}

export class InviteToasts {
  readonly root: HTMLElement;
  private readonly queue = new InviteQueue();
  private readonly views = new Map<number, { el: HTMLElement; clock: HTMLElement }>();
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly opts: InviteToastsOptions) {
    this.root = h("div", { class: "toasts", "aria-live": "polite" });
  }

  /** `wishingRoom`: the invitation is into the inviter's Wishing Room (its room's name); passed to `what` when set. */
  add(inviteId: number, fromName: string, expiresInSecs: number, wishingRoom: string | null = null): void {
    const now = performance.now();
    // A newer invitation from the same person replaces theirs.
    for (const old of this.queue.items) if (old.fromName === fromName) this.drop(old.inviteId);
    const invite = this.queue.add(inviteId, fromName, expiresInSecs, now);
    // The queue may have pushed out the oldest.
    for (const id of [...this.views.keys()]) if (!this.queue.items.some((i) => i.inviteId === id)) this.drop(id);
    this.render(invite, now, wishingRoom);
    this.timer ??= setInterval(() => this.tick(), 500);
  }

  private render(invite: Invite, now: number, wishingRoom: string | null): void {
    const clock = h("span", { class: "toast-clock", title: "Time left" }, formatCountdown(secondsLeft(invite, now)));
    const el = h(
      "div",
      { class: "toast panel", role: "dialog", "aria-label": `Invitation from ${invite.fromName}` },
      h("p", {}, h("strong", {}, invite.fromName), this.opts.what ? this.opts.what(wishingRoom) : wishingRoom ? ` invites you into their Wishing Room (the ${wishingRoom}).` : " invites you to their room."),
      h(
        "div",
        { class: "toast-actions" },
        h(
          "button",
          {
            type: "button",
            class: "secondary toast-accept",
            onclick: () => {
              this.drop(invite.inviteId);
              this.opts.onAccept(invite.inviteId);
            },
          },
          "Accept",
        ),
        h(
          "button",
          {
            type: "button",
            class: "secondary",
            onclick: () => {
              this.drop(invite.inviteId);
              this.opts.onDecline(invite.inviteId);
            },
          },
          "Decline",
        ),
        clock,
      ),
    );
    this.views.set(invite.inviteId, { el, clock });
    this.root.append(el);
  }

  private tick(): void {
    const now = performance.now();
    for (const gone of this.queue.expire(now)) this.drop(gone.inviteId);
    for (const invite of this.queue.items) {
      const v = this.views.get(invite.inviteId);
      if (v) v.clock.textContent = formatCountdown(secondsLeft(invite, now));
    }
    if (this.queue.items.length === 0 && this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private drop(inviteId: number): void {
    this.queue.remove(inviteId);
    this.views.get(inviteId)?.el.remove();
    this.views.delete(inviteId);
  }

  dispose(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.queue.clear();
    this.views.clear();
    this.root.replaceChildren();
  }
}
