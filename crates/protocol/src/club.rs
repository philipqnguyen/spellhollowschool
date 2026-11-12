//! Clubs (protocol v15): lasting groups of up to `members_max` characters
//! (`data/clubs.json`) with an owner, officers and members, a chat channel
//! (`ChatChannel::Club`) and a roster. Unlike a party a club is kept in the
//! database and outlives restarts; it follows characters, not accounts. See
//! docs/architecture.md, "Clubs".
//!
//! - `ClientMsg::ClubFound` founds one at the clubs secretary's desk (a level
//!   and a fee, `data/clubs.json`); the founder hears
//!   [`ClubNotice::Founded`].
//! - An officer or the owner invites an online character
//!   (`ClientMsg::ClubInvite`); they get
//!   [`ClubInvited`](crate::ServerMsg::ClubInvited), valid for
//!   [`CLUB_INVITE_TTL_SECS`], and answer with `ClientMsg::ClubRespond`.
//! - Every change of members, roles, names or the message of the day sends
//!   the whole club to each online member (`ServerMsg::ClubState`, `None`
//!   once the character is in no club); a member coming online, going
//!   offline or levelling sends only `ServerMsg::ClubPresence`. A
//!   [`ClubNotice`] says what happened.
//! - Refusals answer `ServerMsg::ClubError` with a [`ClubErrorCode`].

use serde::{Deserialize, Serialize};

use crate::House;

/// A club invitation can be accepted for this long (seconds), like a party
/// invitation.
pub const CLUB_INVITE_TTL_SECS: u16 = 120;

/// A member's rank. Ordered: `Member < Officer < Owner`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum ClubRole {
    Member,
    /// Invites characters, removes members, sets the message of the day.
    Officer,
    /// The club's president: everything an officer does, and renames,
    /// disbands, appoints and dismisses officers, hands the club on.
    Owner,
}

impl ClubRole {
    /// The database and client name.
    pub fn id(self) -> &'static str {
        match self {
            ClubRole::Member => "member",
            ClubRole::Officer => "officer",
            ClubRole::Owner => "owner",
        }
    }

    pub fn from_id(id: &str) -> Option<ClubRole> {
        match id {
            "member" => Some(ClubRole::Member),
            "officer" => Some(ClubRole::Officer),
            "owner" => Some(ClubRole::Owner),
            _ => None,
        }
    }
}

/// A club as its members see it (`ServerMsg::ClubState`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClubView {
    pub id: i64,
    pub name: String,
    /// The short tag shown beside members' names and on club chat lines
    /// (upper case letters and digits).
    pub tag: String,
    /// The pinned message of the day (empty: none).
    pub motd: String,
    /// Every member, the client included: the owner first, then officers,
    /// then members, each rank longest-serving first.
    pub members: Vec<ClubMember>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClubMember {
    pub character_id: i64,
    pub name: String,
    pub house: House,
    /// The level (1-20), the last one known while offline.
    pub level: u8,
    pub role: ClubRole,
    pub online: bool,
}

/// What just happened to the club, for the chat log. `name` is the
/// character it happened to (the client's own name: it happened to them).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ClubNotice {
    /// To the founder: the club is on the register.
    Founded { club: String },
    /// To the inviter: the invitation went out.
    InviteSent { name: String },
    /// To the inviter: the invitation was declined.
    Declined { name: String },
    /// To every member: `name` joined.
    Joined { name: String },
    /// To every member and the one who left.
    Left { name: String },
    /// To every member and the one removed.
    Removed { name: String },
    /// To every member: `name` is an officer now.
    Promoted { name: String },
    /// To every member: `name` is no longer an officer.
    Demoted { name: String },
    /// To every member: `name` owns the club now (handed over, or passed on
    /// when the owner left).
    Owner { name: String },
    /// To every member: the club's new name and tag.
    Renamed { club: String, tag: String },
    /// To every member: `name` changed the message of the day.
    Motd { name: String },
    /// To every member: the club is gone (disbanded by its owner, or left
    /// empty).
    Disbanded { club: String },
}

/// Why a club action was refused (`ServerMsg::ClubError`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ClubErrorCode {
    /// Nobody by that name is online (also the answer when they blocked the
    /// sender, so blocks stay private).
    NotOnline,
    /// You cannot invite yourself.
    SelfInvite,
    /// They are already in a club.
    InClub,
    /// You are already in a club (founding, accepting).
    AlreadyInClub,
    /// The club already has `members_max` members.
    Full,
    /// The action needs the sender to be in a club.
    NotInClub,
    /// The sender's rank does not allow it (on that member).
    NotAllowed,
    /// That character is not in the sender's club.
    NotMember,
    /// The invitation expired, does not exist, or its officer no longer may
    /// invite.
    InviteExpired,
    /// The sender blocked that character.
    Blocked,
    /// Too many actions; slow down.
    RateLimited,
    /// The name is too short or long or has characters a name may not.
    NameInvalid,
    /// The tag is not `tag_chars` letters or digits.
    TagInvalid,
    /// The name, tag or message of the day has a word the chat filter
    /// refuses.
    Filtered,
    /// Another club has that name (case ignored).
    NameTaken,
    /// Another club has that tag.
    TagTaken,
    /// The message of the day is too long.
    MotdInvalid,
    /// Founding: below `found_level`.
    LevelTooLow,
    /// Founding: fewer Crowns than `found_crowns`.
    NotEnoughCrowns,
    /// Founding: not at the secretary's desk.
    TooFar,
    /// Something went wrong on the server; nothing changed.
    Failed,
}
