// Player-facing text for `DormError` codes (docs/architecture.md, "Dorms and decorating").
// Pure, unit tested.

import type { DormErrorCode } from "../../wasm/sim_wasm";

/** What went wrong, in a sentence. `name` is the friend involved, when known. */
export function dormErrorText(code: DormErrorCode, name?: string): string {
  switch (code) {
    case "not_owner":
      return "Only the room's owner can do that.";
    case "not_in_dorm":
      return "You need to be in your room to do that.";
    case "item_not_found":
      return "That piece is no longer yours.";
    case "not_furniture":
      return "Only furniture can go in your room.";
    case "item_unavailable":
      return "That piece is in use elsewhere.";
    case "out_of_bounds":
      return "That doesn't fit there.";
    case "cap_reached":
      return "Your room is full. Put something away first.";
    case "not_friend":
      return name ? `${name} is not your friend.` : "Only friends can visit.";
    case "not_online":
      return name ? `${name} is not online.` : "They are not online.";
    case "closed":
      return name ? `${name}'s room is not open to friends.` : "That room is not open to friends.";
    case "invite_expired":
      return "That invitation has lapsed.";
    case "too_far":
      return "You are too far away. Walk up to the door.";
    case "wrong_house":
      return "Only members of that house may pass.";
    case "unknown_target":
      return "There is nothing to use here.";
    case "rate_limited":
      return "Slow down a little.";
    case "failed":
      return "Something went wrong. Please try again.";
    case "full":
      return name ? `${name}'s room is full right now.` : "That room is full right now.";
    case "mounted":
      return "Land and get off your broom first.";
    case "not_empty":
      return "Something stands on that piece. Take it off first.";
    case "blocks_way":
      return "That would stand on someone or block the way.";
  }
}

/** Why `EnterWishingRoom` was refused (a `DormError` answering it). */
export function wishingRoomErrorText(code: DormErrorCode): string {
  switch (code) {
    case "closed":
      return "The door has faded back into the wall. Pace past the blank wall again to call it back.";
    case "too_far":
      return "Walk up to the door first.";
    case "full":
      return "Your Wishing Room is full right now.";
    default:
      return dormErrorText(code);
  }
}

/** Codes about a placed or placing piece (decorating), as opposed to visits and doors. */
export function isDecorError(code: DormErrorCode): boolean {
  return ["not_owner", "not_in_dorm", "item_not_found", "not_furniture", "item_unavailable", "out_of_bounds", "cap_reached", "not_empty", "blocks_way"].includes(code);
}
