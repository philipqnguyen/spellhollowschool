// The friends window (O, or the Friends button): the friend list with who is
// online and where, requests in and out, and blocks. Friends
// are added from the players list (U) or a player's card, not by typing a
// name here (`addFriend` is what they call; the endpoint is the same).
// Quick actions per online friend: whisper, invite to your room, and visit
// theirs while it is open to friends (`roomOpen`, kept current by events).
//
// Lists load a page at a time over HTTP (friends-api.ts) and stay current
// through `ServerMsg::Friend` events (friends-state.ts), so the window never
// polls. The incoming-request count shows on the HUD button.

import { ApiError } from "../api";
import { audio } from "../audio";
import { houseInfo } from "../content/houses";
import { h } from "./dom";
import * as friendsApi from "./friends-api";
import type { Friend, Page, RequestDirection } from "./friends-api";
import { FriendsState, locationText, type PagedList } from "./friends-state";
import type { FriendEvent } from "../wasm/sim_wasm";
import "./friends.css";

export interface FriendsWindowOptions {
  myName: string;
  state: FriendsState;
  onWhisper: (name: string) => void;
  onInvite: (name: string) => void;
  /** Invite to our party (parties: any online character). */
  onPartyInvite?: (name: string) => void;
  /** Invite to our club, while we may (an officer or its owner). */
  onClubInvite?: (name: string) => void;
  canClubInvite?: () => boolean;
  onVisit: (name: string) => void;
  /** A line for the chat log (new requests, new friends). */
  notify: (text: string) => void;
  /** Opens the players list (where friends are found). */
  onFindPlayers?: () => void;
}

type Tab = "friends" | "requests" | "blocked";

/** A destructive button asks once more before acting (ms the second chance lasts). */
const CONFIRM_MS = 3000;

export class FriendsWindow {
  readonly root: HTMLElement;
  /** The HUD button that opens the window, with the incoming-request badge. */
  readonly button: HTMLButtonElement;
  private readonly badge: HTMLElement;
  private readonly body: HTMLElement;
  private readonly tabs = new Map<Tab, HTMLButtonElement>();
  private readonly error: HTMLElement;
  private tab: Tab = "friends";
  private busy = false;
  private readonly unsubscribe: () => void;
  private disposed = false;

  constructor(private readonly opts: FriendsWindowOptions) {
    this.badge = h("span", { class: "badge", hidden: true }, "");
    this.button = h(
      "button",
      { type: "button", class: "icon-button hud-friends", "aria-label": "Friends", "aria-expanded": "false", title: "Friends (O)", onclick: () => this.toggle() },
      "☍",
      this.badge,
    );
    const tabBar = h("div", { class: "friends-tabs", role: "tablist" });
    for (const [id, label] of [
      ["friends", "Friends"],
      ["requests", "Requests"],
      ["blocked", "Blocked"],
    ] as [Tab, string][]) {
      const b = h("button", { type: "button", role: "tab", class: "friends-tab", onclick: () => this.show(id) }, label);
      this.tabs.set(id, b);
      tabBar.append(b);
    }
    this.error = h("p", { class: "friends-error", role: "alert", hidden: true });
    this.body = h("div", { class: "friends-body" });
    this.root = h(
      "aside",
      { class: "friends panel", hidden: true, "aria-label": "Friends" },
      h("header", { class: "friends-head" }, h("h2", {}, "Friends"), h("button", { type: "button", class: "icon-button", "aria-label": "Close", onclick: () => this.toggle(false) }, "✕")),
      tabBar,
      this.error,
      this.body,
    );
    this.unsubscribe = opts.state.subscribe(() => this.refresh());
  }

  /** Loads the first pages (the friend list for invites and whisper names, incoming requests for the badge). */
  async start(): Promise<void> {
    await Promise.all([this.loadPage(this.opts.state.friends, (a) => friendsApi.listFriends(a), true), this.loadPage(this.opts.state.incoming, (a) => friendsApi.listRequests("incoming", a), true)]);
  }

  get isOpen(): boolean {
    return !this.root.hidden;
  }

  toggle(open = this.root.hidden): void {
    this.root.hidden = !open;
    this.button.setAttribute("aria-expanded", String(open));
    if (open) this.show(this.tab);
  }

  /** A realtime friend event: update the lists and mention what matters. */
  onEvent(event: FriendEvent): void {
    this.opts.state.apply(event);
    if (event.kind === "requestReceived") {
      audio.play("ui.invite");
      this.opts.notify(`${event.name} would like to be friends. Open Friends (O) to answer.`);
    } else if (event.kind === "added") {
      audio.play("ui.notify");
      this.opts.notify(`You and ${event.name} are now friends.`);
    } else if (event.kind === "online") audio.play("ui.friend_online");
  }

  /**
   * Asks `name` to be friends (the players list and the
   * card), or befriends them at once when they had asked us. Errors are
   * said in the chat (the window may be closed).
   */
  async addFriend(name: string): Promise<void> {
    const s = this.opts.state;
    try {
      const r = await friendsApi.sendRequest(name);
      if (this.disposed) return;
      if ("friend" in r) {
        s.addFriend(r.friend);
        this.opts.notify(`You and ${r.friend.name} are now friends.`);
      } else {
        s.outgoing.upsert(r.request);
        this.opts.notify(`Friend request sent to ${r.request.name}.`);
      }
    } catch (e) {
      this.opts.notify(e instanceof Error ? e.message : String(e));
    } finally {
      s.changed();
    }
  }

  /** Escape closes the window. */
  handleKey(e: KeyboardEvent): boolean {
    if (e.code === "KeyO") {
      this.toggle();
      return true;
    }
    if (e.key === "Escape" && this.isOpen) {
      this.toggle(false);
      return true;
    }
    return false;
  }

  private show(tab: Tab): void {
    this.tab = tab;
    this.setError(null);
    const s = this.opts.state;
    // First visit to a tab loads its first page.
    if (tab === "requests" && !s.outgoing.loaded) void this.loadPage(s.outgoing, (a) => friendsApi.listRequests("outgoing", a), true);
    if (tab === "blocked" && !s.blocks.loaded) void this.loadPage(s.blocks, (a) => friendsApi.listBlocks(a), true);
    this.refresh();
  }

  private async loadPage<T>(list: PagedList<T>, fetchPage: (after: string | null) => Promise<Page<T>>, first: boolean): Promise<void> {
    try {
      const page = await fetchPage(first ? null : list.next);
      if (this.disposed) return;
      list.addPage(page, first);
      this.opts.state.changed();
    } catch (e) {
      if (!this.disposed) this.setError(e);
    }
  }

  private setError(e: unknown): void {
    const text = e === null ? "" : e instanceof Error ? e.message : String(e);
    this.error.textContent = text;
    this.error.hidden = !text;
  }

  /** Runs an HTTP action with the window marked busy; errors show in the window. */
  private async act(fn: () => Promise<void>): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.setError(null);
    this.root.setAttribute("aria-busy", "true");
    try {
      await fn();
    } catch (e) {
      this.setError(e instanceof ApiError || e instanceof Error ? e : String(e));
    } finally {
      this.busy = false;
      this.root.removeAttribute("aria-busy");
      this.opts.state.changed();
    }
  }

  private refresh(): void {
    const badge = this.opts.state.incomingBadge();
    this.badge.textContent = badge;
    this.badge.hidden = !badge;
    const reqTab = this.tabs.get("requests")!;
    reqTab.textContent = badge ? `Requests (${badge})` : "Requests";
    if (this.root.hidden) return;
    for (const [id, b] of this.tabs) b.setAttribute("aria-selected", String(id === this.tab));
    // Keep what is being typed in a name box across redraws.
    const typing = document.activeElement instanceof HTMLInputElement && this.body.contains(document.activeElement) ? document.activeElement : null;
    const kept = typing ? { name: typing.name, value: typing.value } : null;
    this.body.replaceChildren(...(this.tab === "friends" ? this.friendsTab() : this.tab === "requests" ? this.requestsTab() : this.blockedTab()));
    if (kept) {
      const input = this.body.querySelector<HTMLInputElement>(`input[name="${kept.name}"]`);
      if (input) {
        input.value = kept.value;
        input.focus();
      }
    }
  }

  // ---------------------------------------------------------------- tabs

  private friendsTab(): (Node | string)[] {
    const s = this.opts.state;
    const list = s.friendList();
    const online = list.filter((f) => f.online).length;
    return [
      ...(this.opts.onFindPlayers ? [h("button", { type: "button", class: "secondary friends-find", onclick: () => this.opts.onFindPlayers?.() }, "Find players online (U)")] : []),
      h("p", { class: "friends-summary" }, s.friends.loaded ? `${online} of ${list.length}${s.friends.hasMore ? "+" : ""} online` : "Loading…"),
      h("ul", { class: "friends-list" }, ...(list.length ? list.map((f) => this.friendRow(f)) : [h("li", { class: "friends-empty" }, s.friends.loaded ? "No friends yet. Find someone in the players list (U) and choose Add friend." : "")])),
      this.moreButton(s.friends, (a) => friendsApi.listFriends(a)),
    ];
  }

  private friendRow(f: Friend): HTMLElement {
    const where = locationText(f, this.opts.myName);
    return h(
      "li",
      { class: `friend${f.online ? " online" : ""}` },
      h("span", { class: "presence", title: f.online ? "Online" : "Offline" }),
      h("div", { class: "friend-who" }, h("span", { class: "friend-name", style: `--house: ${houseInfo(f.house).colour}` }, f.name), h("span", { class: "friend-where" }, where)),
      h(
        "div",
        { class: "friend-actions" },
        f.online ? h("button", { type: "button", class: "link-button", onclick: () => this.opts.onWhisper(f.name) }, "Whisper") : null,
        f.online ? h("button", { type: "button", class: "link-button", onclick: () => this.opts.onInvite(f.name), title: "Invite to your room" }, "Invite to room") : null,
        f.online && this.opts.onPartyInvite ? h("button", { type: "button", class: "link-button", onclick: () => this.opts.onPartyInvite?.(f.name), title: "Invite to your party" }, "Invite to party") : null,
        f.online && this.opts.onClubInvite && this.opts.canClubInvite?.() ? h("button", { type: "button", class: "link-button", onclick: () => this.opts.onClubInvite?.(f.name), title: "Invite to your club" }, "Invite to club") : null,
        // Only while their room is open to friends: anything else would be refused.
        f.online && f.roomOpen ? h("button", { type: "button", class: "link-button", onclick: () => this.opts.onVisit(f.name), title: "Visit their room (it is open to friends)" }, "Visit") : null,
        this.confirmButton("Remove", `Remove ${f.name} from your friends`, () =>
          this.act(async () => {
            await friendsApi.removeFriend(f.characterId);
            this.opts.state.dropCharacter(f.characterId);
          }),
        ),
        this.confirmButton("Block", `Block ${f.name}`, () => this.blockByName(f.name)),
      ),
    );
  }

  private requestsTab(): (Node | string)[] {
    const s = this.opts.state;
    const incoming = s.incomingList();
    const outgoing = s.outgoingList();
    const row = (r: friendsApi.FriendRequest, dir: RequestDirection) =>
      h(
        "li",
        { class: "friend" },
        h("div", { class: "friend-who" }, h("span", { class: "friend-name", style: `--house: ${houseInfo(r.house).colour}` }, r.name), h("span", { class: "friend-where" }, houseInfo(r.house).name)),
        h(
          "div",
          { class: "friend-actions" },
          ...(dir === "incoming"
            ? [
                h(
                  "button",
                  {
                    type: "button",
                    class: "secondary",
                    onclick: () =>
                      this.act(async () => {
                        const friend = await friendsApi.acceptRequest(r.id);
                        s.addFriend(friend);
                      }),
                  },
                  "Accept",
                ),
                h(
                  "button",
                  {
                    type: "button",
                    class: "link-button",
                    onclick: () =>
                      this.act(async () => {
                        await friendsApi.declineRequest(r.id);
                        s.incoming.delete(r.id);
                      }),
                  },
                  "Decline",
                ),
              ]
            : [
                h(
                  "button",
                  {
                    type: "button",
                    class: "link-button",
                    onclick: () =>
                      this.act(async () => {
                        await friendsApi.cancelRequest(r.id);
                        s.outgoing.delete(r.id);
                      }),
                  },
                  "Cancel",
                ),
              ]),
        ),
      );
    return [
      h("h3", {}, "Wants to be your friend"),
      h("ul", { class: "friends-list" }, ...(incoming.length ? incoming.map((r) => row(r, "incoming")) : [h("li", { class: "friends-empty" }, "No requests waiting.")])),
      this.moreButton(s.incoming, (a) => friendsApi.listRequests("incoming", a)),
      h("h3", {}, "You asked"),
      h("ul", { class: "friends-list" }, ...(outgoing.length ? outgoing.map((r) => row(r, "outgoing")) : [h("li", { class: "friends-empty" }, s.outgoing.loaded ? "No requests sent." : "Loading…")])),
      this.moreButton(s.outgoing, (a) => friendsApi.listRequests("outgoing", a)),
    ];
  }

  private blockedTab(): (Node | string)[] {
    const s = this.opts.state;
    const blocks = s.blockList();
    return [
      this.nameForm("block", "Block someone by name", "Block", (name) => this.blockByName(name)),
      h("p", { class: "friends-summary" }, "Blocked characters can't whisper you, send requests or visit, and you won't see their chat."),
      h(
        "ul",
        { class: "friends-list" },
        ...(blocks.length
          ? blocks.map((b) =>
              h(
                "li",
                { class: "friend" },
                h("div", { class: "friend-who" }, h("span", { class: "friend-name" }, b.name)),
                h(
                  "div",
                  { class: "friend-actions" },
                  h(
                    "button",
                    {
                      type: "button",
                      class: "link-button",
                      onclick: () =>
                        this.act(async () => {
                          await friendsApi.unblock(b.characterId);
                          s.blocks.delete(b.characterId);
                        }),
                    },
                    "Unblock",
                  ),
                ),
              ),
            )
          : [h("li", { class: "friends-empty" }, s.blocks.loaded ? "Nobody is blocked." : "Loading…")]),
      ),
      this.moreButton(s.blocks, (a) => friendsApi.listBlocks(a)),
    ];
  }

  private blockByName(name: string): Promise<void> {
    return this.act(async () => {
      const b = await friendsApi.block(name);
      const s = this.opts.state;
      s.blocks.upsert(b);
      s.dropCharacter(b.characterId);
      this.opts.notify(`You have blocked ${b.name}.`);
    });
  }

  // ---------------------------------------------------------------- bits

  private nameForm(name: string, label: string, action: string, run: (name: string) => Promise<void>): HTMLElement {
    const input = h("input", { type: "text", name, class: "friends-input", placeholder: "Character name", "aria-label": label, maxlength: 32, autocomplete: "off", spellcheck: "false" });
    input.addEventListener("keydown", (e) => e.stopPropagation()); // typing never reaches the game's keys
    return h(
      "form",
      {
        class: "friends-form",
        onsubmit: (e: Event) => {
          e.preventDefault();
          const v = input.value.trim();
          if (!v) return;
          void run(v).then(() => {
            if (!this.error.textContent) input.value = "";
          });
        },
      },
      input,
      h("button", { type: "submit", class: "secondary" }, action),
    );
  }

  private moreButton<T>(list: PagedList<T>, fetchPage: (after: string | null) => Promise<Page<T>>): HTMLElement | string {
    if (!list.hasMore) return "";
    return h("button", { type: "button", class: "secondary friends-more", onclick: () => void this.loadPage(list, fetchPage, false) }, "Show more");
  }

  /** A button that needs a second click within a few seconds. */
  private confirmButton(label: string, title: string, run: () => void): HTMLElement {
    let armed: ReturnType<typeof setTimeout> | null = null;
    const b = h("button", { type: "button", class: "link-button danger", title }, label);
    b.addEventListener("click", () => {
      if (armed === null) {
        b.textContent = `${label}?`;
        b.classList.add("armed");
        armed = setTimeout(() => {
          armed = null;
          b.textContent = label;
          b.classList.remove("armed");
        }, CONFIRM_MS);
        return;
      }
      clearTimeout(armed);
      armed = null;
      run();
    });
    return b;
  }

  dispose(): void {
    this.disposed = true;
    this.unsubscribe();
    this.root.remove();
    this.button.remove();
  }
}
