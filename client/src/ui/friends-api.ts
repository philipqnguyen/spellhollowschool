// HTTP endpoints for friends, requests and blocks (docs/architecture.md,
// "HTTP API"), with the server's snake_case shapes turned into
// the client's camelCase ones. Lists are keyset-paginated: pass a page's
// `next` back as `after`.

import { authedRequest } from "../api";
import type { Location } from "../wasm/sim_wasm";

export interface Friend {
  characterId: number;
  name: string;
  /** Lower-case house id (`"skytalon"`), as the wasm decoder spells it. */
  house: string;
  online: boolean;
  /** Where they are while online. */
  location: Location | null;
  /** Online with their room open to friends: Visit would let you in. */
  roomOpen: boolean;
  /** RFC 3339 timestamp. */
  since: string;
}

export interface FriendRequest {
  id: number;
  /** The other party: the sender of an incoming request, the recipient of an outgoing one. */
  characterId: number;
  name: string;
  house: string;
  createdAt: string;
}

export interface Block {
  characterId: number;
  name: string;
  createdAt: string;
}

export interface Page<T> {
  items: T[];
  next: string | null;
}

export type RequestDirection = "incoming" | "outgoing";

/** The page size the windows ask for. */
export const PAGE_SIZE = 25;

interface RawLocation {
  kind: string;
  place?: string;
  owner_name?: string;
}

export function parseLocation(raw: RawLocation | null | undefined): Location | null {
  if (!raw) return null;
  if (raw.kind === "dorm") return { kind: "dorm", ownerName: raw.owner_name ?? "" };
  // In a Wishing Room (theirs or a friend's).
  if (raw.kind === "wishing_room") return { kind: "wishing_room", ownerName: raw.owner_name ?? "" };
  return { kind: "castle", place: raw.place ?? "" };
}

interface RawFriend {
  character_id: number;
  name: string;
  house: string;
  online: boolean;
  location: RawLocation | null;
  room_open?: boolean;
  since: string;
}

export function parseFriend(raw: RawFriend): Friend {
  const location = parseLocation(raw.location);
  return {
    characterId: raw.character_id,
    name: raw.name,
    house: raw.house.toLowerCase(),
    online: raw.online,
    location: raw.online ? location : null,
    roomOpen: raw.online && raw.room_open === true,
    since: raw.since,
  };
}

interface RawRequest {
  id: number;
  character_id: number;
  name: string;
  house: string;
  created_at: string;
}

function parseRequest(raw: RawRequest): FriendRequest {
  return { id: raw.id, characterId: raw.character_id, name: raw.name, house: raw.house.toLowerCase(), createdAt: raw.created_at };
}

interface RawBlock {
  character_id: number;
  name: string;
  created_at: string;
}

function parseBlock(raw: RawBlock): Block {
  return { characterId: raw.character_id, name: raw.name, createdAt: raw.created_at };
}

function pageQuery(after: string | null, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams({ limit: String(PAGE_SIZE), ...extra });
  if (after) q.set("after", after);
  return q.toString();
}

async function page<R, T>(path: string, parse: (r: R) => T): Promise<Page<T>> {
  const r = await authedRequest<{ items: R[]; next: string | null }>("GET", path);
  return { items: r.items.map(parse), next: r.next ?? null };
}

export function listFriends(after: string | null = null): Promise<Page<Friend>> {
  return page(`/friends?${pageQuery(after)}`, parseFriend);
}

export function listRequests(direction: RequestDirection, after: string | null = null): Promise<Page<FriendRequest>> {
  return page(`/friends/requests?${pageQuery(after, { direction })}`, parseRequest);
}

export function listBlocks(after: string | null = null): Promise<Page<Block>> {
  return page(`/blocks?${pageQuery(after)}`, parseBlock);
}

/** Asks `name` to be friends. They may already have asked you, in which case you are friends at once. */
export async function sendRequest(name: string): Promise<{ request: FriendRequest } | { friend: Friend }> {
  const r = await authedRequest<{ request?: RawRequest; friend?: RawFriend }>("POST", "/friends/requests", { name });
  if (r.friend) return { friend: parseFriend(r.friend) };
  return { request: parseRequest(r.request!) };
}

export async function acceptRequest(id: number): Promise<Friend> {
  const r = await authedRequest<{ friend: RawFriend }>("POST", `/friends/requests/${id}/accept`);
  return parseFriend(r.friend);
}

export function declineRequest(id: number): Promise<void> {
  return authedRequest("POST", `/friends/requests/${id}/decline`);
}

/** The sender takes back a request. */
export function cancelRequest(id: number): Promise<void> {
  return authedRequest("DELETE", `/friends/requests/${id}`);
}

export function removeFriend(characterId: number): Promise<void> {
  return authedRequest("DELETE", `/friends/${characterId}`);
}

/** Blocks `name`: also ends any friendship and pending requests both ways. */
export async function block(name: string): Promise<Block> {
  return parseBlock(await authedRequest<RawBlock>("POST", "/blocks", { name }));
}

export function unblock(characterId: number): Promise<void> {
  return authedRequest("DELETE", `/blocks/${characterId}`);
}
