// The friends window's model: the friend list, incoming and outgoing
// requests and blocks, each loaded a page at a time over HTTP and kept
// current by `ServerMsg::Friend` events (docs/architecture.md, "Network").
// Pure (no DOM) so it is unit tested; friends.ts renders it.
//
// Pages and events can interleave: a page merges by id, so a friend added by
// an event and then listed again on a later page appears once.

import type { FriendEvent, Location } from "../wasm/sim_wasm";
import type { Block, Friend, FriendRequest, Page } from "./friends-api";

/** One paginated list, keyed by id, in arrival order. */
export class PagedList<T> {
  readonly byId = new Map<number, T>();
  /** Cursor for the next page; null when everything is loaded (or nothing is yet, see `loaded`). */
  next: string | null = null;
  /** At least the first page has arrived. */
  loaded = false;

  constructor(private readonly idOf: (item: T) => number) {}

  /** Adds a page; `first` replaces what was there (a refresh). */
  addPage(page: Page<T>, first: boolean): void {
    if (first) this.byId.clear();
    for (const item of page.items) this.byId.set(this.idOf(item), item);
    this.next = page.next;
    this.loaded = true;
  }

  get hasMore(): boolean {
    return this.loaded && this.next !== null;
  }

  upsert(item: T): void {
    this.byId.set(this.idOf(item), item);
  }

  delete(id: number): boolean {
    return this.byId.delete(id);
  }

  get size(): number {
    return this.byId.size;
  }

  values(): T[] {
    return [...this.byId.values()];
  }
}

const byName = (a: { name: string }, b: { name: string }) => {
  const x = a.name.toLowerCase();
  const y = b.name.toLowerCase();
  return x < y ? -1 : x > y ? 1 : 0;
};

/** Newest first, like the server's request and block lists. */
const newestFirst = (a: { createdAt: string }, b: { createdAt: string }) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0);

export class FriendsState {
  readonly friends = new PagedList<Friend>((f) => f.characterId);
  readonly incoming = new PagedList<FriendRequest>((r) => r.id);
  readonly outgoing = new PagedList<FriendRequest>((r) => r.id);
  readonly blocks = new PagedList<Block>((b) => b.characterId);
  private readonly listeners = new Set<() => void>();

  /** Calls `fn` after every change; returns an unsubscribe function. */
  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  changed(): void {
    for (const fn of this.listeners) fn();
  }

  /** Friends by name, as the server pages them. */
  friendList(): Friend[] {
    return this.friends.values().sort(byName);
  }

  /** Online friends, by name: who can be invited to a room or whispered. */
  onlineFriends(): Friend[] {
    return this.friendList().filter((f) => f.online);
  }

  incomingList(): FriendRequest[] {
    return this.incoming.values().sort(newestFirst);
  }

  outgoingList(): FriendRequest[] {
    return this.outgoing.values().sort(newestFirst);
  }

  blockList(): Block[] {
    return this.blocks.values().sort(newestFirst);
  }

  friendByName(name: string): Friend | undefined {
    const key = name.toLowerCase();
    return this.friends.values().find((f) => f.name.toLowerCase() === key);
  }

  /** Applies a realtime `FriendEvent`. */
  apply(event: FriendEvent, nowIso: string = new Date().toISOString()): void {
    switch (event.kind) {
      case "requestReceived":
        this.incoming.upsert({ id: event.requestId, characterId: event.characterId, name: event.name, house: event.house, createdAt: nowIso });
        break;
      case "added":
        this.addFriend({
          characterId: event.characterId,
          name: event.name,
          house: event.house,
          online: event.location !== null,
          location: event.location,
          roomOpen: event.location !== null && event.roomOpen,
          since: nowIso,
        });
        break;
      case "requestClosed":
        this.incoming.delete(event.requestId);
        this.outgoing.delete(event.requestId);
        break;
      case "removed":
        this.friends.delete(event.characterId);
        break;
      case "online":
        this.setPresence(event.characterId, event.location, event.roomOpen);
        break;
      case "moved":
        this.setPresence(event.characterId, event.location);
        break;
      case "offline":
        this.setPresence(event.characterId, null, false);
        break;
      case "roomOpen": {
        const f = this.friends.byId.get(event.characterId);
        if (f) this.friends.upsert({ ...f, roomOpen: f.online && event.open });
        break;
      }
    }
    this.changed();
  }

  /** A new friend (accepted either way): any request between you is settled. */
  addFriend(friend: Friend): void {
    this.friends.upsert(friend);
    this.dropRequestsWith(friend.characterId);
  }

  /** Unfriended, or blocked: they leave the list and any request goes. */
  dropCharacter(characterId: number): void {
    this.friends.delete(characterId);
    this.dropRequestsWith(characterId);
  }

  private dropRequestsWith(characterId: number): void {
    for (const list of [this.incoming, this.outgoing]) {
      for (const r of list.values()) if (r.characterId === characterId) list.delete(r.id);
    }
  }

  /** `roomOpen` is kept as it was when not given (a zone change says nothing about it). */
  private setPresence(characterId: number, location: Location | null, roomOpen?: boolean): void {
    // Friends on pages not loaded yet are skipped; their page has it right.
    const f = this.friends.byId.get(characterId);
    if (!f) return;
    const online = location !== null;
    this.friends.upsert({ ...f, online, location, roomOpen: online && (roomOpen ?? f.roomOpen) });
  }

  /** Online friends whose room is open to friends: the ones Visit works for. */
  visitable(): Friend[] {
    return this.onlineFriends().filter((f) => f.roomOpen);
  }

  /** Incoming requests for the badge on the Friends button: a count, "+" when more are unloaded. */
  incomingBadge(): string {
    const n = this.incoming.size;
    if (n === 0) return "";
    return this.incoming.next ? `${n}+` : String(n);
  }
}

/** Where a friend is, for the list: "Great Hall", "In their room", "Visiting Basil's room". */
export function locationText(f: Friend, myName: string): string {
  if (!f.online || !f.location) return "Offline";
  const loc = f.location;
  if (loc.kind === "castle") return loc.place || "In the castle";
  // The Wishing Room (its opener's).
  if (loc.kind === "wishing_room") {
    if (loc.ownerName === f.name) return "In the Wishing Room";
    if (loc.ownerName === myName) return "In your Wishing Room";
    return `In ${loc.ownerName}'s Wishing Room`;
  }
  if (loc.ownerName === f.name) return "In their dormitory";
  if (loc.ownerName === myName) return "In your room";
  return `Visiting ${loc.ownerName}'s room`;
}
