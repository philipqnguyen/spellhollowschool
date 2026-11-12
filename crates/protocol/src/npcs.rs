//! NPCs in the open castle (protocol v12). See docs/architecture.md, "NPCs".
//!
//! The castle's resident NPCs that move (the four house ghosts, the three
//! resident staff, the background students) and the shopkeepers are
//! ordinary NPC entities of the castle zone: snapshots carry them like any
//! character and `EntityInfo.npc` names their `data/npcs/roster.json` id,
//! which tells the client what kind each one is. Talking portraits are not
//! entities at all: the client places them from `data/npcs/portraits.json`
//! and everything they say is local to that player.
//!
//! **Lines are the client's.** Every line an NPC says (`data/npcs/lines.json`)
//! is picked and shown on the talking player's own screen only
//! (`sim_core::npcs::Lines::pick`); no message carries a line and nobody else
//! sees it.
//!
//! **Talking to an entity NPC.** `ClientMsg::NpcInteract { id }` with a
//! ghost, a member of staff or a student in reach
//! (`sim_core::npcs::NpcRules::talk_reach_m`) is answered by
//! `ServerMsg::NpcReply { id, reply }`: [`NpcReply::Gift`] when that NPC gave
//! the character today's gift just now (once per character, per NPC, per UTC
//! day, in one transaction), else [`NpcReply::Talk`] (`given_today` when the
//! gift was already given today), or [`NpcReply::Refused`]. The client opens
//! its talk card on the reply. A shopkeeper is talked to with the plain
//! `ClientMsg::Interact { target }` of the station it keeps (its roster
//! `station`), which opens the shop as before; `NpcInteract` with a
//! shopkeeper answers `Talk`.

use serde::{Deserialize, Serialize};

/// Something an NPC gave (`NpcReply::Gift`). Recorded in `npc_gifts.gift`
/// in this serde JSON shape (externally tagged: `{"item": {...}}`), so a
/// retried claim answers what was given.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NpcGift {
    /// A lore entry the character had not unlocked (`lore_unlocks`, read
    /// with `GET /api/lore/{id}`); `title` for the toast.
    Lore { lore_id: u16, title: String },
    /// Catalog items into the inventory (stackable materials only).
    Item { item_def: u16, quantity: u16 },
    /// House points for the character's house (`house_points` source
    /// `npc`, under its daily cap).
    HousePoint { points: u8 },
    /// The display name of a castle place with a find spot the character
    /// has not searched today (the class hint's rule).
    Hint { place: String },
}

/// Why `NpcInteract` was refused.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NpcErrorCode {
    /// Farther than `talk_reach_m`, or in another zone.
    TooFar,
    /// No such NPC (gone, a player, or a professor).
    UnknownNpc,
    /// The shared action bucket is empty.
    RateLimited,
    /// A database error; nothing was given.
    Failed,
}

/// The answer to `NpcInteract`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum NpcReply {
    /// Just talk. `given_today`: this NPC gives this character a gift and
    /// already gave today's (the client may pick a "come back tomorrow"
    /// line).
    Talk {
        given_today: bool,
    },
    /// Today's gift, given just now.
    Gift {
        gift: NpcGift,
    },
    Refused {
        code: NpcErrorCode,
    },
}
