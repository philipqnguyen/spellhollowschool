import { describe, expect, it } from "vitest";
import type { Friend, FriendRequest } from "./friends-api";
import { FriendsState, locationText } from "./friends-state";

const friend = (characterId: number, name: string, online = false): Friend => ({
  characterId,
  name,
  house: "skytalon",
  online,
  location: online ? { kind: "castle", place: "Great Hall" } : null,
  roomOpen: false,
  since: "2026-10-01T00:00:00Z",
});
const request = (id: number, characterId: number, name: string, createdAt = "2026-10-01T00:00:00Z"): FriendRequest => ({
  id,
  characterId,
  name,
  house: "burrowsby",
  createdAt,
});

describe("FriendsState pages", () => {
  it("merges pages by id and sorts by name", () => {
    const s = new FriendsState();
    s.friends.addPage({ items: [friend(2, "basil"), friend(1, "Alina")], next: "c1" }, true);
    expect(s.friends.hasMore).toBe(true);
    // A friend added by an event shows up again on the next page: listed once.
    s.apply({ kind: "added", characterId: 3, name: "Cora", house: "serpentyne", location: null, roomOpen: false });
    s.friends.addPage({ items: [friend(3, "Cora"), friend(4, "dmitri")], next: null }, false);
    expect(s.friendList().map((f) => f.name)).toEqual(["Alina", "basil", "Cora", "dmitri"]);
    expect(s.friends.hasMore).toBe(false);
  });

  it("starts over on a refresh", () => {
    const s = new FriendsState();
    s.friends.addPage({ items: [friend(1, "Alina")], next: null }, true);
    s.friends.addPage({ items: [friend(2, "Basil")], next: null }, true);
    expect(s.friendList().map((f) => f.characterId)).toEqual([2]);
  });
});

describe("FriendsState events", () => {
  it("tracks presence and location", () => {
    const s = new FriendsState();
    s.friends.addPage({ items: [friend(1, "Alina")], next: null }, true);
    s.apply({ kind: "online", characterId: 1, location: { kind: "castle", place: "Entrance Hall" }, roomOpen: false });
    expect(s.onlineFriends().map((f) => f.name)).toEqual(["Alina"]);
    s.apply({ kind: "moved", characterId: 1, location: { kind: "dorm", ownerName: "Alina" } });
    expect(s.friends.byId.get(1)?.location).toEqual({ kind: "dorm", ownerName: "Alina" });
    s.apply({ kind: "offline", characterId: 1 });
    expect(s.friends.byId.get(1)).toMatchObject({ online: false, location: null });
    // Presence of a friend whose page isn't loaded changes nothing.
    s.apply({ kind: "online", characterId: 42, location: { kind: "castle", place: "Courtyard" }, roomOpen: true });
    expect(s.friends.size).toBe(1);
  });

  it("knows whose room is open, so Visit shows only then", () => {
    const s = new FriendsState();
    s.friends.addPage({ items: [friend(1, "Alina"), friend(2, "Basil", true)], next: null }, true);
    expect(s.visitable()).toEqual([]);
    s.apply({ kind: "online", characterId: 1, location: { kind: "castle", place: "Entrance Hall" }, roomOpen: true });
    expect(s.visitable().map((f) => f.name)).toEqual(["Alina"]);
    // Moving between zones keeps it; closing the room or going offline ends it.
    s.apply({ kind: "moved", characterId: 1, location: { kind: "dorm", ownerName: "Alina" } });
    expect(s.friends.byId.get(1)?.roomOpen).toBe(true);
    s.apply({ kind: "roomOpen", characterId: 1, open: false });
    expect(s.visitable()).toEqual([]);
    s.apply({ kind: "roomOpen", characterId: 2, open: true });
    expect(s.visitable().map((f) => f.name)).toEqual(["Basil"]);
    s.apply({ kind: "offline", characterId: 2 });
    expect(s.friends.byId.get(2)).toMatchObject({ online: false, roomOpen: false });
    // Nobody offline is visitable, whatever an event says.
    s.apply({ kind: "roomOpen", characterId: 2, open: true });
    expect(s.visitable()).toEqual([]);
  });

  it("settles requests when a friendship starts or a request closes", () => {
    const s = new FriendsState();
    const changes: number[] = [];
    s.subscribe(() => changes.push(s.incoming.size));
    s.apply({ kind: "requestReceived", requestId: 10, characterId: 5, name: "Eve", house: "roarwing" });
    s.apply({ kind: "requestReceived", requestId: 11, characterId: 6, name: "Finn", house: "roarwing" });
    expect(s.incomingBadge()).toBe("2");
    s.outgoing.addPage({ items: [request(12, 7, "Gus")], next: null }, true);

    s.apply({ kind: "added", characterId: 5, name: "Eve", house: "roarwing", location: { kind: "castle", place: "Great Hall" }, roomOpen: true });
    expect(s.visitable().map((f) => f.name)).toEqual(["Eve"]);
    expect(s.incomingList().map((r) => r.id)).toEqual([11]);
    expect(s.friends.byId.get(5)?.online).toBe(true);

    s.apply({ kind: "requestClosed", requestId: 11 });
    s.apply({ kind: "requestClosed", requestId: 12 }); // the recipient declined our request
    expect(s.incoming.size).toBe(0);
    expect(s.outgoing.size).toBe(0);
    expect(s.incomingBadge()).toBe("");
    expect(changes).toEqual([1, 2, 1, 0, 0]);
  });

  it("drops a removed friend", () => {
    const s = new FriendsState();
    s.friends.addPage({ items: [friend(1, "Alina", true), friend(2, "Basil")], next: null }, true);
    s.apply({ kind: "removed", characterId: 1 });
    expect(s.friendList().map((f) => f.name)).toEqual(["Basil"]);
  });

  it("shows a + on the badge while more incoming requests are unloaded", () => {
    const s = new FriendsState();
    s.incoming.addPage({ items: [request(1, 2, "Ann")], next: "more" }, true);
    expect(s.incomingBadge()).toBe("1+");
  });

  it("drops requests and the friendship of someone blocked", () => {
    const s = new FriendsState();
    s.friends.addPage({ items: [friend(1, "Alina")], next: null }, true);
    s.incoming.addPage({ items: [request(9, 1, "Alina")], next: null }, true);
    s.dropCharacter(1);
    expect(s.friends.size + s.incoming.size).toBe(0);
  });

  it("lists requests newest first and finds friends by name", () => {
    const s = new FriendsState();
    s.incoming.addPage({ items: [request(1, 2, "Ann", "2026-10-01T00:00:00Z"), request(2, 3, "Bo", "2026-10-02T00:00:00Z")], next: null }, true);
    expect(s.incomingList().map((r) => r.name)).toEqual(["Bo", "Ann"]);
    s.friends.addPage({ items: [friend(1, "Alina")], next: null }, true);
    expect(s.friendByName("alina")?.characterId).toBe(1);
  });
});

describe("locationText", () => {
  it("describes where a friend is", () => {
    expect(locationText(friend(1, "Alina"), "Me")).toBe("Offline");
    expect(locationText(friend(1, "Alina", true), "Me")).toBe("Great Hall");
    const inDorm = (ownerName: string): Friend => ({ ...friend(1, "Alina", true), location: { kind: "dorm", ownerName } });
    expect(locationText(inDorm("Alina"), "Me")).toBe("In their dormitory");
    expect(locationText(inDorm("Me"), "Me")).toBe("In your room");
    expect(locationText(inDorm("Basil"), "Me")).toBe("Visiting Basil's room");
  });
});
