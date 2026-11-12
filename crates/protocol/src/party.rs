//! Parties (protocol v14): up to [`PARTY_MAX`] characters of any house who
//! share a chat channel (`ChatChannel::Party`), see each other in the world
//! wherever they are in the same zone, and appear on each other's party
//! list and map. A party lives in the server's memory only (a restart
//! disbands it) and follows characters, not accounts. See
//! docs/architecture.md, "Parties".
//!
//! - `ClientMsg::PartyInvite` asks an online character to join; they get
//!   [`PartyInvited`](crate::ServerMsg::PartyInvited), valid for
//!   [`PARTY_INVITE_TTL_SECS`], and answer with `ClientMsg::PartyRespond`.
//! - Every change sends the whole party to each online member
//!   (`ServerMsg::PartyState`, `None` once the character is in no party), and
//!   a [`PartyNotice`] says what happened.
//! - Refusals answer `ServerMsg::PartyError` with a [`PartyErrorCode`].

use serde::{Deserialize, Serialize};

use crate::{EntityId, House};

/// The most characters in one party.
pub const PARTY_MAX: u8 = 6;
/// A party invitation can be accepted for this long (seconds), like a dorm
/// invitation.
pub const PARTY_INVITE_TTL_SECS: u16 = 120;

/// A party as its members see it (`ServerMsg::PartyState`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PartyView {
    /// The leader's character id (also `members[0]`).
    pub leader: i64,
    /// Every member, the client included: the leader first, then the others
    /// in the order they joined.
    pub members: Vec<PartyMember>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PartyMember {
    pub character_id: i64,
    pub name: String,
    pub house: House,
    /// The level (1-20), the last one known while offline.
    pub level: u8,
    /// `None` while the member is offline (they stay in the party for a
    /// while and may come back).
    pub online: Option<PartyPresence>,
}

/// Where an online member is.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PartyPresence {
    /// Their entity in their zone (the nameplate, the map's dot).
    pub entity: EntityId,
    /// Their zone, as a number that only means something compared with
    /// another member's: members with the same `zone` are together (the
    /// castle is 0), and each sees the others in its snapshots whatever the
    /// distance.
    pub zone: u32,
    pub place: PartyPlace,
}

/// The place an online member is in, for the party list and the map.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum PartyPlace {
    /// In the castle or on the grounds; `place` is the layout surface's
    /// display name (as `Location::Castle`).
    Castle { place: String },
    /// In a dorm room (theirs or a friend's); `house` is the room's.
    Dorm { owner_name: String, house: House },
    /// In a Wishing Room (theirs or a friend's).
    WishingRoom { owner_name: String },
    /// In a class section (`data/classes/schedule.json` class id).
    Class { class: String },
    /// In a duel arena (`data/dueling/venues.json` venue id), dueling or
    /// watching.
    Arena { venue: String },
}

/// What just happened to the party, for the chat log. `name` is the
/// character it happened to (the client's own name: it happened to them).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum PartyNotice {
    /// To the inviter: the invitation went out.
    InviteSent { name: String },
    /// To the inviter: the invitation was declined.
    Declined { name: String },
    /// To every member: `name` joined.
    Joined { name: String },
    /// To every member and the one who left: `name` left (or stayed offline
    /// too long).
    Left { name: String },
    /// To every member and the one removed: the leader removed `name`.
    Removed { name: String },
    /// To every member: `name` leads the party now.
    Leader { name: String },
    /// To the last member: nobody else is left, so the party is gone.
    Disbanded,
}

/// Why a party action was refused (`ServerMsg::PartyError`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum PartyErrorCode {
    /// Nobody by that name is online (also the answer when they blocked
    /// the sender, so blocks stay private).
    NotOnline,
    /// You cannot invite yourself.
    SelfInvite,
    /// They (or, accepting, you) are already in a party.
    InParty,
    /// The party already has `PARTY_MAX` members.
    Full,
    /// The action needs the sender to be in a party.
    NotInParty,
    /// Only the leader may do that.
    NotLeader,
    /// That character is not in the sender's party.
    NotMember,
    /// The invitation expired or does not exist.
    InviteExpired,
    /// The sender blocked that character.
    Blocked,
    /// Too many actions; slow down.
    RateLimited,
}
