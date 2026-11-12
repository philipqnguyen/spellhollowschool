//! Secrets and the map (protocol v9). See docs/architecture.md,
//! "Secrets and the map".
//!
//! **Hidden passages.** The five passages are layout surfaces with `secret:
//! true` (`data/world/secrets.json` groups them). The first time a character
//! stands on one the server records the discovery (once per character, in the
//! same transaction as its reward) and sends `ServerMsg::Discovered`: the
//! secret's id, the XP it gave, whether a House Cup point came with it (the
//! `find` source's daily cap may be used up) and the recipe it taught, if
//! any. The client shows the toast and draws the passage on its map from then
//! on. Unfound secrets are never named or drawn.
//!
//! **The map.** Places are revealed whole, the surface you stand on
//! (`sim_core::secrets::reveal_at`). The client predicts its own reveals with
//! the same rule; the server saves them with the 30 s position save and at
//! logout, and `GET /api/map` returns the revealed surface ids and the found
//! secrets. No realtime message carries reveals.
//!
//! **The Wishing Room.** Pacing three times between the two marks of
//! the seventh-floor east corridor within a minute
//! (`sim_core::wishing_room::Pacing`) makes the server send
//! `ServerMsg::WishingRoomDoor { open_until_ms }`: the door shows on the blank
//! wall for that player only, until then. Using it (`Interact`, answered with
//! nothing) offers the room choice; `ClientMsg::EnterWishingRoom { room }`
//! enters the opener's room (`Zone::WishingRoom`). While an opener's room is
//! occupied there is one instance of it: entering again joins it whatever
//! room was asked for. Guests come by the dorm messages: `DormInvite` sent
//! from inside one's own room invites there (`DormInviteReceived.wishing_room`
//! names the room), `DormSetOpen` opens it to friends (`DormVisit` by the
//! opener's name joins it while it is open), `DormKick` sends a guest out.
//! `WishingRoomDoor { open_until_ms: 0 }` takes the door away (entered).

use serde::{Deserialize, Serialize};

/// The most people in one Wishing Room (the opener and guests).
pub const WISHING_ROOM_MAX: u8 = 20;

/// A room of the Wishing Room (`data/world/secrets.json`
/// `wishing_room.rooms`, in this order).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WishingRoomKind {
    /// A cluttered hall of unclaimed oddments: one rare find a day per character
    /// (its find spot, `find_lost_property_vault`).
    LostProperty,
    /// A lounge with seats and floor seats for up to `WISHING_ROOM_MAX`.
    Gathering,
}

impl WishingRoomKind {
    pub const ALL: [WishingRoomKind; 2] =
        [WishingRoomKind::LostProperty, WishingRoomKind::Gathering];

    /// The id in `data/world/secrets.json` and JSON.
    pub fn id(self) -> &'static str {
        match self {
            WishingRoomKind::LostProperty => "lost_property",
            WishingRoomKind::Gathering => "gathering",
        }
    }

    pub fn from_id(id: &str) -> Option<WishingRoomKind> {
        WishingRoomKind::ALL.into_iter().find(|r| r.id() == id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn room_ids_round_trip() {
        for r in WishingRoomKind::ALL {
            assert_eq!(WishingRoomKind::from_id(r.id()), Some(r));
        }
        assert_eq!(WishingRoomKind::from_id("attic"), None);
    }
}
