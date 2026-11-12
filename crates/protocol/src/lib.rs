//! Wire protocol shared by the game server, the browser client (via `sim-wasm`)
//! and the load-test bots.
//!
//! Every WebSocket frame is a binary message: one [`PROTOCOL_VERSION`]-checked
//! `postcard` encoding of [`ClientMsg`] (client → server) or [`ServerMsg`]
//! (server → client). This crate is the single definition; nothing else
//! hand-writes message layouts (the server's borrowed snapshot encoder is
//! tested byte for byte against it).
//!
//! - [`appearance`]: what a character looks like ([`Appearance`]).
//! - [`snapshot`]: snapshot v2 rules, [`EntityDelta`] and [`SnapshotReceiver`].
//! - [`looks`]: cached looks in snapshot entries.
//! - [`effects`]: potion effects ([`VisibleEffect`], [`EffectKind`]).
//! - [`economy`]: stations, finds, the trade window.
//! - [`school`]: the clock, classes, levels, spells outside duels and NPCs,
//!   and [`dueling`]: challenges, arenas, duels and spectators.
//! - [`flying`]: mounting a broom and the refusals (protocol v8).
//! - [`secrets`]: hidden passages, the map and the Wishing Room
//!   (protocol v9).
//! - [`npcs`]: talking to the open castle's NPCs and their daily gifts
//!   (protocol v12).
//! - [`party`]: parties, their chat channel and their list (protocol v14).
//! - [`club`]: clubs, their chat channel and their roster (protocol v15).
//!
//! Zones: the castle and grounds are one seamless world; dorm rooms are small
//! instances. A client is in exactly one zone at a time. `Welcome` says which,
//! `ZoneChanged` moves it to another, and snapshots, `Say` chat and rosters are
//! per zone. See docs/architecture.md, "Zones".

pub mod appearance;
pub mod club;
pub mod dueling;
pub mod economy;
pub mod effects;
pub mod flying;
pub mod looks;
pub mod npcs;
pub mod party;
pub mod school;
pub mod secrets;
pub mod snapshot;

use serde::{Deserialize, Serialize};

pub use appearance::{
    Appearance, EYE_COLOUR_DEFAULT, Finish, GearSlot, HAIR_STYLE_DEFAULT, Outfit, SLIDER_NEUTRAL,
    Worn,
};
pub use club::{CLUB_INVITE_TTL_SECS, ClubErrorCode, ClubMember, ClubNotice, ClubRole, ClubView};
pub use dueling::{
    ArenaPos, CastRefusal, ChallengeEnd, DUEL_CHALLENGE_SECS, DuelEndReason, DuelErrorCode,
    DuelEvent, DuelRewards, DuelRole, DuelSide, DuelSnapshot, DuelSummary, DuelistInfo,
    DuelistState, ProjectileState,
};
pub use economy::{
    FindOutcome, FoundItem, OfferItem, OfferedItem, STATION_GRANT_SECS, StationKind,
    TRADE_ITEMS_MAX, TRADE_REQUEST_SECS, TradeErrorCode, TradeOffer, TradeOutcome, TradeView,
};
pub use effects::{
    ChameleonPart, EffectKind, GuardianAnimal, OwnEffect, PotionErrorCode, SCALE_PCT_NORMAL,
    SPEED_PCT_MAX, SPEED_PCT_MIN, SPEED_PCT_NORMAL, VisibleEffect,
};
pub use flying::{BROOM_PCT_MAX, BROOM_PCT_NORMAL, MountRefused};
pub use looks::{EntityEntry, LOOK_CACHE_MAX, LookCache, LookVersion, SentLooks};
pub use npcs::{NpcErrorCode, NpcGift, NpcReply};
pub use party::{
    PARTY_INVITE_TTL_SECS, PARTY_MAX, PartyErrorCode, PartyMember, PartyNotice, PartyPlace,
    PartyPresence, PartyView,
};
pub use school::{
    BeatPrompt, ClassErrorCode, ClassInfo, ClassRewards, FortuneKind, GestureId, GestureResult,
    Grade, LESSON_BEATS_MAX, LEVEL_MAX, LOADOUT_MAX, LessonBeat, LessonPhase, NpcEvent,
    PICK_OPTIONS_MAX, PICK_OPTIONS_MIN, PickIndex, Progress, REVEAL_SPOTS_MAX, RevealKind,
    RevealedSpot, RewardItem, SpellErrorCode, SpellId, SpellTarget, TRACE_POINTS_MAX, TracePoint,
    TraceReject, XpGain, XpSource,
};
pub use secrets::{WISHING_ROOM_MAX, WishingRoomKind};
pub use snapshot::{
    Desync, EntityDelta, LOOK_RANGE_M, LOOK_SENDS_PER_SEC, LookChange, MAX_UPDATE_GAP_SNAPSHOTS,
    RESYNC_SNAPSHOTS, SnapshotReceiver,
};

/// Bumped on any breaking change to the message types below.
/// 2: snapshot deltas, appearance, emotes, zones and dorms, friend events,
/// kick codes and chat rejections.
/// 3: cached appearances in snapshot entries (`EntityEntry`), `DormErrorCode::Full`,
/// declining invitations, friends' room-open status.
/// 4: the economy and crafting: finishes on worn items, potion
/// effects in looks (`EntityInfo.effects`, `effects_version` in cached
/// entries), wallet and mail notices, stations and the Owl Exchange
/// counter, find spots, the trade window and potions.
/// 5: school life and dueling: the clock in `Welcome`, levels
/// in looks (`EntityInfo.level`, `LevelChanged`, `Progress`), NPCs
/// (`EntityInfo.npc`, `NpcEvent`), the `Cast` anim and `Lucerna` effect, class
/// and arena zones, class sessions and traced gestures, spells outside duels,
/// challenges, duels and spectators.
/// 6: character adoption: `Appearance` drops `build` and gains
/// `eye_colour` (the six new hair styles and eye colours of
/// `creator.json` v2).
/// 7: the school day (every realtime change of the school day): beats
/// are traced or picked (`LessonBeat.prompt: BeatPrompt`, `PickAnswer`,
/// `GestureScored` renamed `BeatScored`, `TraceReject::WrongBeatKind` and
/// `BadChoice`), class rewards v2 (`ClassRewards` with Crowns, items,
/// recipes, lore, a hint, a fortune, `xp_capped`), and the Divination
/// fortune effect (`EffectKind::Fortune`, `FortuneKind`,
/// `OwnEffect.fortune`).
/// 8: flying: `ClientMsg::Mount`, `ServerMsg::Mounted` with
/// `MountRefused`, `Anim::Fly` and `Anim::Hover` (snapshot deltas of a flying
/// or hovering entity count in 2 cm units), `BeatPrompt::Ring` (the Flying
/// class's ring beats), `EffectKind::Featherlight` and `OwnEffects.broom_pct`,
/// `DormErrorCode::Mounted` (doors and stations refuse riders).
/// 9: secrets and the map: `Zone::WishingRoom` and
/// `Location::WishingRoom` (the Wishing Room), `ClientMsg::EnterWishingRoom`,
/// `ServerMsg::WishingRoomDoor` and `ServerMsg::Discovered`,
/// `DormInviteReceived.wishing_room` (an invitation into the inviter's Wishing
/// Room), `XpSource::Explore`.
/// 10: characters look at their target: `ClientMsg::LookAt` and
/// `Snapshot.looks` ([`snapshot::LookChange`], docs/architecture.md,
/// "Anims and emotes").
/// 11: spells to level 20: hindrance and spell effects
/// (`EffectKind::{Petrified, Slowed, Recoloured, Guardian}`,
/// `VisibleEffect::{Bound, Slowed, Recoloured, Guardian}`, `GuardianAnimal`),
/// `ServerMsg::ForcedMove` and `Revealed`, new spell error codes, duel binds,
/// slows, pulls, cleanses and wards (`DuelEvent`s, `DuelistState` fields,
/// `CastRefusal::Staggered`).
/// 12: NPCs in the open castle: `ClientMsg::NpcInteract` and
/// `ServerMsg::NpcReply` ([`npcs`]: talk, today's gift, refusals).
/// 13: several characters per account: `Hello.character_id` (the character
/// the client expects to play). An older client's `Hello` no longer decodes;
/// [`hello_version`] still reads its version, so it gets `VersionMismatch`.
/// 14: parties ([`party`]): `ChatChannel::Party`, `ChatRejectReason::NoChannel`,
/// `ClientMsg::{PartyInvite, PartyRespond, PartyLeave, PartyKick,
/// PartyPromote}` and `ServerMsg::{PartyInvited, PartyState, PartyNotice,
/// PartyError}`.
/// 15: clubs ([`club`]): `ChatChannel::Club`, `ClientMsg::{ClubFound,
/// ClubInvite, ClubRespond, ClubLeave, ClubKick, ClubSetRole, ClubTransfer,
/// ClubRename, ClubSetMotd, ClubDisband}` and `ServerMsg::{ClubInvited,
/// ClubState, ClubPresence, ClubNotice, ClubError}`.
/// 16: no school years: `Progress` drops `year`.
pub const PROTOCOL_VERSION: u16 = 16;

/// Mimic Draught consent requests may be answered for this long (seconds).
pub const MIMIC_DRAUGHT_REQUEST_SECS: u16 = 30;

/// Simulation and network rates for the castle world (docs/architecture.md).
pub const WORLD_TICK_HZ: u32 = 20;
pub const WORLD_SNAPSHOT_HZ: u32 = 10;
pub const CLIENT_MOVE_HZ: u32 = 10;

/// Visible-set sizes offered by the graphics setting; the server clamps to
/// `VIEW_LIMIT_MAX`. Priority players count toward the limit, so a visible
/// set never holds more than `VIEW_LIMIT_MAX` entities (`EntityDelta::index`
/// relies on it).
pub const VIEW_LIMIT_DEFAULT: u8 = 50;
pub const VIEW_LIMIT_MAX: u8 = 100;

/// Maximum chat message length in bytes, enforced by both ends.
pub const CHAT_MAX_LEN: usize = 256;

/// One-shot emotes play this long before the server returns the character to
/// `Anim::Idle` (milliseconds). `Sit` lasts until the character moves.
pub const EMOTE_WAVE_MS: u32 = 2_500;
pub const EMOTE_BOW_MS: u32 = 2_000;

/// Most furniture items placed in one dorm room (GPU budget).
pub const DORM_ITEM_CAP: u16 = 150;
/// A dorm invitation can be accepted for this long (seconds).
pub const DORM_INVITE_TTL_SECS: u16 = 120;
/// A guest the owner sent out may not visit that room again for this long
/// (seconds), unless the owner invites them.
pub const DORM_KICK_BAR_SECS: u16 = 600;
/// Saved outfits per character (enforced by the HTTP API).
pub const SAVED_OUTFITS_MAX: u32 = 10;
/// Friends per character (enforced by the HTTP API).
pub const FRIENDS_MAX: u32 = 200;

pub type EntityId = u32;

/// Never a character: `ServerMsg::Chat` from this id is a notice from the
/// server itself, with an empty `from_name`, on the channel of the message it
/// answers. Refused chat lines are answered with `ChatRejected` instead.
pub const SERVER_ENTITY_ID: EntityId = 0;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum House {
    Roarwing,
    Burrowsby,
    Skytalon,
    Serpentyne,
}

impl House {
    pub const ALL: [House; 4] = [
        House::Roarwing,
        House::Burrowsby,
        House::Skytalon,
        House::Serpentyne,
    ];

    /// The lower-case id data files use for this house (layout `house` gates,
    /// catalog `house:<id>` tags, instance layout names `dorm_<id>`, the
    /// client's content files). The HTTP API spells houses as serde does
    /// (`"Roarwing"`).
    pub fn id(self) -> &'static str {
        match self {
            House::Roarwing => "roarwing",
            House::Burrowsby => "burrowsby",
            House::Skytalon => "skytalon",
            House::Serpentyne => "serpentyne",
        }
    }

    pub fn from_id(id: &str) -> Option<House> {
        House::ALL.into_iter().find(|h| h.id() == id)
    }
}

/// Animation state shown to other players. The emote states are entered with
/// `ClientMsg::Emote`, never through `Move.anim`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Anim {
    Idle,
    Walk,
    Run,
    Wave,
    Bow,
    /// Sitting on the ground.
    Sit,
    /// A wand gesture (a spell cast, a traced class gesture, a
    /// professor's demonstration). Set by the server for [`CAST_ANIM_MS`],
    /// never through `Move.anim`; moving ends it, like an emote.
    Cast,
    /// Riding a broom and moving (`Move.anim` while mounted).
    Fly,
    /// Riding a broom and holding still in the air (or low over
    /// the ground).
    Hover,
}

impl Anim {
    /// Walking or running.
    pub fn is_moving(self) -> bool {
        matches!(self, Anim::Walk | Anim::Run)
    }

    /// On a broom (flying or hovering). Snapshot deltas of such a
    /// state count in 2 cm units ([`snapshot::EntityDelta`]).
    pub fn is_flying(self) -> bool {
        matches!(self, Anim::Fly | Anim::Hover)
    }
}

/// How long the server shows `Anim::Cast` after a cast (milliseconds).
pub const CAST_ANIM_MS: u32 = 900;

/// Social actions (`ClientMsg::Emote`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Emote {
    Wave,
    Bow,
    Sit,
}

impl Emote {
    pub fn anim(self) -> Anim {
        match self {
            Emote::Wave => Anim::Wave,
            Emote::Bow => Anim::Bow,
            Emote::Sit => Anim::Sit,
        }
    }

    /// How long the server holds a one-shot emote; `None` for `Sit`, which
    /// lasts until the character moves.
    pub fn duration_ms(self) -> Option<u32> {
        match self {
            Emote::Wave => Some(EMOTE_WAVE_MS),
            Emote::Bow => Some(EMOTE_BOW_MS),
            Emote::Sit => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ChatChannel {
    /// Everyone within the say radius, in the speaker's zone.
    Say,
    /// Everyone online in the sender's house, in any zone.
    House,
    /// One player, by character name.
    Whisper { to: String },
    /// Protocol v14: every member of the sender's party, online anywhere
    /// (any zone, any distance). Refused with `ChatRejectReason::NoChannel`
    /// outside a party.
    Party,
    /// Protocol v15: every member of the sender's club, online anywhere.
    /// Refused with `ChatRejectReason::NoChannel` outside a club.
    Club,
}

/// Position quantized to centimetres. World coordinates are metres, right-handed,
/// Y up (same as glTF): x = east, y = up, z = south.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct QPos {
    pub x: i32,
    pub y: i32,
    pub z: i32,
}

impl QPos {
    pub fn from_m(p: [f32; 3]) -> Self {
        Self {
            x: (p[0] * 100.0).round() as i32,
            y: (p[1] * 100.0).round() as i32,
            z: (p[2] * 100.0).round() as i32,
        }
    }
    pub fn to_m(self) -> [f32; 3] {
        [
            self.x as f32 / 100.0,
            self.y as f32 / 100.0,
            self.z as f32 / 100.0,
        ]
    }
}

/// Yaw in radians quantized to one byte (256 steps per full turn).
pub fn yaw_to_u8(yaw: f32) -> u8 {
    let turn = std::f32::consts::TAU;
    let norm = ((yaw % turn) + turn) % turn;
    ((norm / turn * 256.0).round() as u32 % 256) as u8
}

pub fn yaw_from_u8(q: u8) -> f32 {
    q as f32 / 256.0 * std::f32::consts::TAU
}

// ---------------------------------------------------------------- client → server

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ClientMsg {
    /// First message on a new socket. `token` is the session token from `POST /api/auth/login`.
    /// Protocol v13: `character_id` is the character the client was built
    /// for (the one it picked); the server plays the session's picked
    /// character and kicks with `Replaced` when it is another, so a client
    /// that reconnects never comes back as a character it is not showing.
    /// `None` takes whichever the session picked (bots, load tests).
    Hello {
        protocol_version: u16,
        token: String,
        character_id: Option<i64>,
    },
    /// Client-predicted position in the current zone, sent at `CLIENT_MOVE_HZ`.
    /// `seq` increases by one per message (and keeps counting across zones).
    /// `anim` must be `Idle`, `Walk` or `Run`; the server treats anything else
    /// as `Idle`. A move that changes the position ends an emote; one that
    /// does not keeps it. Protocol v8: while mounted (`Mounted { on: true }`)
    /// the position is a flight position and `anim` is `Fly` or `Hover`
    /// (anything else counts as `Hover`); the server's mount state, never the
    /// anim, decides which check applies.
    Move {
        seq: u32,
        pos: [f32; 3],
        yaw: f32,
        anim: Anim,
    },
    /// `client_seq` is chosen by the client (increasing) so a `ChatRejected`
    /// can name the line it refuses; lines that are delivered are not acknowledged.
    Chat {
        channel: ChatChannel,
        text: String,
        client_seq: u32,
    },
    /// Graphics setting: how many characters this client wants drawn (20, 50 or 100).
    SetViewLimit {
        n: u8,
    },
    Ping {
        client_time_ms: u32,
    },
    /// Starts an emote. Ignored while walking or running.
    Emote {
        emote: Emote,
    },
    /// Uses an interactable of the current zone's layout (`Layout.interactables`
    /// `id`), e.g. the dorm door in a common room or the dorm's way out. The
    /// character must stand within the interactable's `radius`.
    Interact {
        target: String,
    },
    /// Leaves the current instance: owners return to their common room's dorm
    /// door, guests to where they were before they entered. Ignored in the castle.
    LeaveInstance,
    /// Places a furniture item from the inventory in the owner's dorm (owner only).
    DormPlace {
        item_id: i64,
        pos: QPos,
        yaw: u8,
    },
    /// Moves or rotates a placed item (owner only).
    DormMove {
        item_id: i64,
        pos: QPos,
        yaw: u8,
    },
    /// Takes a placed item back into the inventory (owner only).
    DormRemove {
        item_id: i64,
    },
    /// Whether friends may walk in (`DormVisit`) while the owner is online.
    DormSetOpen {
        open: bool,
    },
    /// Invites an online friend into the sender's dorm.
    DormInvite {
        character_name: String,
    },
    /// Accepts an invitation (`DormInviteReceived.invite_id`): the guest goes
    /// straight into the inviter's dorm.
    DormAcceptInvite {
        invite_id: u32,
    },
    /// Walks into a friend's dorm that is open to friends while its owner is
    /// online (from the friend list).
    DormVisit {
        owner_name: String,
    },
    /// Sends a guest out of the sender's dorm (owner only). They may not
    /// walk back in (`DormVisit`) for `DORM_KICK_BAR_SECS` unless invited again.
    DormKick {
        entity: EntityId,
    },
    /// Declines an invitation (`DormInviteReceived.invite_id`): it is used up
    /// and the inviter, if online, gets `DormInviteDeclined`.
    DormDeclineInvite {
        invite_id: u32,
    },

    // ---- Protocol v4.
    /// Uses a furniture item placed in the dorm the sender is in (a
    /// cauldron: `ServerMsg::StationOpened`). Out of reach (1.5 m from the
    /// item's origin) answers `DormError { TooFar }`.
    UseFurniture {
        item_id: i64,
    },
    /// Asks a player in the same zone, within the trade distance, to trade.
    /// They get `TradeRequested`; the sender gets `TradeError` if refused.
    TradeRequest {
        target: EntityId,
    },
    /// Answers `TradeRequested`: accepting opens the window for both
    /// (`TradeState`), declining ends it (`TradeResult { Declined }`).
    TradeRespond {
        trade_id: u32,
        accept: bool,
    },
    /// Replaces the sender's whole offer. Refused while the sender's offer is
    /// locked. Items must be in the sender's inventory.
    TradeSetOffer {
        trade_id: u32,
        items: Vec<OfferItem>,
        crowns: i64,
    },
    /// Locks (done editing) or unlocks the sender's offer. Unlocking clears
    /// both confirmations and bumps the revision.
    TradeLock {
        trade_id: u32,
        locked: bool,
    },
    /// Approves revision `rev` of the trade (both offers locked). When both
    /// sides confirmed the same revision, the trade completes.
    TradeConfirm {
        trade_id: u32,
        rev: u32,
    },
    /// Ends the trade (or withdraws a pending request) for both sides.
    TradeCancel {
        trade_id: u32,
    },
    /// Drinks a potion from the inventory (any potion but Mimic Draught).
    Drink {
        item_id: i64,
    },
    /// Mimic Draught: asks a friend in the same zone, within range, for consent
    /// to copy their look. The potion is used only if they accept.
    MimicDraughtAsk {
        item_id: i64,
        target: EntityId,
    },
    /// Answers `MimicDraughtRequest`.
    MimicDraughtAnswer {
        request_id: u32,
        accept: bool,
    },

    // ---- Protocol v5.
    /// A traced gesture for beat `beat` of practice round `round` (a class
    /// in practice). `start_offset_ms` is when the trace began relative to
    /// the beat's `at_unix_ms` (negative: early), measured with the clock
    /// the client corrected by `Welcome.unix_ms`. At most `TRACE_POINTS_MAX`
    /// samples. Answered by `BeatScored`.
    TraceGesture {
        round: u8,
        beat: u8,
        start_offset_ms: i16,
        points: Vec<TracePoint>,
    },
    /// Casts a learned spell outside a duel (Lucerna on, Elevara
    /// on a furniture piece, a flourish). Refusals answer `SpellError`.
    CastSpell {
        spell: SpellId,
        target: SpellTarget,
    },
    /// Ends a lasting spell (Lucerna off). Ignored if it is not on.
    EndSpell {
        spell: SpellId,
    },
    /// Challenges a visible player (both in the same dueling area).
    DuelChallenge {
        target: EntityId,
    },
    /// `/duel name`: the same, by character name.
    DuelChallengeName {
        name: String,
    },
    /// Answers `DuelChallenged`.
    DuelRespond {
        challenge_id: u32,
        accept: bool,
    },
    /// Withdraws a challenge the sender made.
    DuelCancel {
        challenge_id: u32,
    },
    /// A duelist casts a spell of their loadout at the opponent.
    DuelCast {
        spell: SpellId,
    },
    /// A duelist gives up (so does `LeaveInstance` in an arena).
    DuelForfeit,
    /// The duels a player in a dueling area may watch (`DuelList`).
    DuelListRequest,
    /// Watches a duel (from its venue's dueling area): joins its arena.
    DuelSpectate {
        duel_id: u32,
    },

    // ---- Protocol v7.
    /// The student's pick for pick beat `beat` of practice round `round`:
    /// `choice` is the position in the beat's `BeatPrompt::Pick.options`
    /// (0..`PICK_OPTIONS_MAX`), and `offset_ms` when it was made relative
    /// to the beat's `at_unix_ms` (negative: early; prompts arrive with the
    /// round, so a pick may land on the beat), on the clock the client
    /// corrected by `Welcome.unix_ms`. One pick per beat counts. Answered by
    /// `BeatScored`.
    PickAnswer {
        round: u8,
        beat: u8,
        choice: u8,
        offset_ms: i16,
    },

    // ---- Protocol v8.
    /// Mount a broom where the character stands (`on: true`) or land and
    /// dismount (`on: false`). Answered by `Mounted`.
    Mount {
        on: bool,
    },

    // ---- Protocol v9.
    /// Enters the Wishing Room through its door, which the server
    /// opened for the sender (`WishingRoomDoor`), standing within the
    /// `wishing_room_door` interactable's reach: `ZoneChanged` with
    /// `Zone::WishingRoom` follows. While the sender's own room is occupied,
    /// this joins it (whatever `room` says). Refusals answer `DormError`
    /// (`Closed`: no door open for you; `TooFar`, `Mounted`, `Full`).
    EnterWishingRoom {
        room: WishingRoomKind,
    },
    /// Protocol v10: the character the sender's character looks at now
    /// (its selection, a duel opponent, a trade partner), or `None`. Sent
    /// only when it changes, at most [`LOOK_SENDS_PER_SEC`] a second. The
    /// server keeps it while the target exists in the sender's zone within
    /// [`LOOK_RANGE_M`] (anything else counts as `None`) and shows it to
    /// viewers in `Snapshot.looks`.
    LookAt {
        target: Option<EntityId>,
    },
    /// Protocol v12: talks to an NPC of the castle (a ghost, a member of
    /// staff, a student) within `sim_core::npcs::NpcRules::talk_reach_m`.
    /// Answered by `ServerMsg::NpcReply` (today's gift, if one is due).
    NpcInteract {
        id: EntityId,
    },

    // ---- Protocol v14: parties ([`party`]).
    /// Invites an online character (by name, any house) into the sender's
    /// party, or into a new one the sender leads. They get `PartyInvited`
    /// and the sender `PartyNotice::InviteSent`; refusals answer
    /// `PartyError`.
    PartyInvite {
        name: String,
    },
    /// Answers `PartyInvited`: accepting joins the inviter's party (or starts
    /// one with them); declining tells the inviter
    /// (`PartyNotice::Declined`).
    PartyRespond {
        invite_id: u32,
        accept: bool,
    },
    /// Leaves the sender's party. When the leader leaves, the next member
    /// leads; a party left with one member disbands.
    PartyLeave,
    /// The leader removes a member (online or not).
    PartyKick {
        character_id: i64,
    },
    /// The leader hands leadership to an online member.
    PartyPromote {
        character_id: i64,
    },

    // ---- Protocol v15: clubs ([`club`]).
    /// Founds a club at the clubs secretary's desk: `secretary` is her
    /// entity, in talking reach. Needs `found_level` and `found_crowns`
    /// (`data/clubs.json`; the fee is paid) and no club. Answered by
    /// `ClubNotice::Founded` and `ClubState`, or `ClubError`.
    ClubFound {
        secretary: EntityId,
        name: String,
        tag: String,
    },
    /// An officer or the owner invites an online character (by name, any
    /// house) into the club. They get `ClubInvited`, the sender
    /// `ClubNotice::InviteSent`; refusals answer `ClubError`.
    ClubInvite {
        name: String,
    },
    /// Answers `ClubInvited`: accepting joins the club; declining tells the
    /// inviter (`ClubNotice::Declined`).
    ClubRespond {
        invite_id: u32,
        accept: bool,
    },
    /// Leaves the sender's club. An owner leaving hands the club to the
    /// longest-serving officer, else the longest-serving member; the last
    /// member leaving disbands it.
    ClubLeave,
    /// Removes a member (online or not): officers remove members, the owner
    /// anyone else.
    ClubKick {
        character_id: i64,
    },
    /// The owner appoints (`Officer`) or dismisses (`Member`) an officer.
    ClubSetRole {
        character_id: i64,
        role: ClubRole,
    },
    /// The owner hands the club to another member and becomes an officer.
    ClubTransfer {
        character_id: i64,
    },
    /// The owner renames the club (name and tag).
    ClubRename {
        name: String,
        tag: String,
    },
    /// An officer or the owner sets the message of the day (empty clears it).
    ClubSetMotd {
        motd: String,
    },
    /// The owner disbands the club.
    ClubDisband,
}

// ---------------------------------------------------------------- server → client

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CharacterInfo {
    pub name: String,
    pub house: House,
}

/// Sent once when an entity enters a client's visible set, before its first state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EntityInfo {
    pub id: EntityId,
    pub character: CharacterInfo,
    /// Bumped when the entity's look changes; clients cache assembled
    /// characters by (id, version).
    pub appearance_version: u32,
    pub appearance: Appearance,
    /// Bumped by the world when the visible potion effects change (starts at
    /// 0 each time the character enters the world).
    pub effects_version: u32,
    /// Active potion effects as viewers draw them (usually empty), plus
    /// `Lucerna` while the wand is lit.
    pub effects: Vec<VisibleEffect>,
    /// The player's level (1-20), for the nameplate; 0 for NPCs. A change
    /// bumps `effects_version` (it is part of the world's half of the look)
    /// and is sent as `LevelChanged`.
    pub level: u8,
    /// `Some` for a scripted NPC (the professor): its id in
    /// the data (`data/classes/<class>.json` `professor.id`). NPCs cannot be
    /// traded with, challenged, whispered or befriended.
    pub npc: Option<String>,
}

/// An absolute state (`Snapshot.full`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct EntityState {
    pub id: EntityId,
    pub pos: QPos,
    pub yaw: u8,
    pub anim: Anim,
}

/// Which world a client is in.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Zone {
    /// The seamless castle and grounds (`data/world/layout.json`).
    Castle,
    /// A dorm room instance.
    Dorm {
        /// The room's owner (the client's own name when it is their room).
        owner_name: String,
        /// The owner's house, which sets the room's style.
        house: House,
        /// Instance layout id: `data/world/instances/<layout>.json`
        /// (`dorm_<house id>`).
        layout: String,
    },
    /// A section of a class session (`ClassJoined` follows).
    Class {
        /// `data/classes/schedule.json` class id ("charms").
        class: String,
        section: u8,
        /// Instance layout id (`class_charms`).
        layout: String,
    },
    /// A dueling arena (`DuelStart` follows).
    Arena {
        duel_id: u32,
        /// `data/dueling/venues.json` venue id ("dueling_club").
        venue: String,
        /// Instance layout id (`duel_arena`).
        layout: String,
    },
    /// A Wishing Room, its opener's (`DormState` follows,
    /// with no furniture and its open flag).
    WishingRoom {
        /// Who opened it (the client's own name when it is theirs).
        owner_name: String,
        room: WishingRoomKind,
        /// Instance layout id (`lost_property_vault`, `wishing_room_gathering`).
        layout: String,
    },
}

/// Where an online friend is, for the friend list.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Location {
    /// In the castle; `place` is the display `name` of the layout surface they
    /// stand on (e.g. the hall or corridor).
    Castle { place: String },
    /// In a dorm room, theirs or a friend's.
    Dorm { owner_name: String },
    /// In a Wishing Room, theirs or a friend's.
    WishingRoom { owner_name: String },
}

/// One furniture item placed in a dorm. `pos` is the item's origin (see
/// `sim_core::placement`) in the room's coordinates.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlacedFurniture {
    /// The item instance (`items.id`).
    pub item_id: i64,
    pub item_def: u16,
    pub dyes: [u8; 3],
    pub pos: QPos,
    pub yaw: u8,
}

/// A live edit of a dorm's furniture, sent to everyone in the room (the
/// editor included, which confirms its edit).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum FurnitureEdit {
    Placed(PlacedFurniture),
    Moved { item_id: i64, pos: QPos, yaw: u8 },
    Removed { item_id: i64 },
}

/// Changes to the friend list, pushed so the HUD updates without polling.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum FriendEvent {
    /// Someone sent this player a friend request.
    RequestReceived {
        request_id: i64,
        character_id: i64,
        name: String,
        house: House,
    },
    /// A request this player sent or received was accepted: a new friend.
    Added {
        character_id: i64,
        name: String,
        house: House,
        /// `Some` while the new friend is online.
        location: Option<Location>,
        /// Their room is open to friends (`DormVisit` works) while they are online.
        room_open: bool,
    },
    /// A pending request disappeared (cancelled by its sender or declined).
    RequestClosed { request_id: i64 },
    /// This player is no longer friends with them (removed or blocked).
    Removed { character_id: i64 },
    /// A friend came online.
    Online {
        character_id: i64,
        location: Location,
        /// Their room is open to friends.
        room_open: bool,
    },
    /// An online friend changed zone (castle ↔ a dorm), or
    /// walked into another place of the castle: at most one every 2 s per
    /// walker, a change held back going with a later move.
    Moved {
        character_id: i64,
        location: Location,
    },
    /// A friend went offline.
    Offline { character_id: i64 },
    /// An online friend opened or closed their room to friends.
    RoomOpen { character_id: i64, open: bool },
}

/// Why the server is closing the connection. The client decides from the code
/// whether to reconnect ([`KickCode::is_retryable`]); the reason text is only shown.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum KickCode {
    /// The server is restarting or shutting down.
    ServerRestart,
    /// Nothing heard from the client for too long, or no `Hello` in time.
    Timeout,
    /// The client could not keep up with its outbound queue.
    TooSlow,
    /// The world is full and no slot opened up.
    ServerFull,
    /// The same account signed in somewhere else.
    Replaced,
    /// The account is banned or suspended.
    Banned,
    /// A frame that is not a valid message, or a message out of order.
    ProtocolError,
    /// `Hello.protocol_version` is not this server's.
    VersionMismatch,
    /// The session token is unknown or expired.
    SessionExpired,
    /// The account has no character yet.
    NoCharacter,
    /// Too many messages.
    Flood,
}

impl KickCode {
    /// Temporary conditions: the client should reconnect with backoff. Every
    /// other code is final (reconnecting after `Replaced` would make two tabs
    /// kick each other forever).
    pub fn is_retryable(self) -> bool {
        matches!(
            self,
            KickCode::ServerRestart | KickCode::Timeout | KickCode::TooSlow | KickCode::ServerFull
        )
    }
}

/// Why a chat line was refused. The line was not delivered to anyone; the
/// client removes its local echo of it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ChatRejectReason {
    /// Sending faster than the chat rate limit allows.
    RateLimited,
    /// The word filter refused the line.
    Filtered,
    /// The whisper recipient is not online. Also the answer when the recipient
    /// has blocked the sender, so blocks stay private.
    UnknownTarget,
    /// Empty after trimming, too long, or otherwise not sendable.
    Invalid,
    /// Protocol v14: the sender is not in the group the channel goes to
    /// (`ChatChannel::Party` outside a party; protocol v15:
    /// `ChatChannel::Club` outside a club).
    NoChannel,
}

/// Why a dorm or door action failed (`ServerMsg::DormError`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum DormErrorCode {
    /// Only the room's owner may do that.
    NotOwner,
    /// The action needs the sender to be in their own dorm.
    NotInDorm,
    /// No such item in the sender's inventory or room.
    ItemNotFound,
    /// The item is not furniture.
    NotFurniture,
    /// The item is equipped or already placed.
    ItemUnavailable,
    /// The position is outside the room's placeable areas, or the yaw does not
    /// suit a wall item.
    OutOfBounds,
    /// The room already holds `DORM_ITEM_CAP` items.
    CapReached,
    /// Invitations and visits are for friends only.
    NotFriend,
    /// The other player is not online.
    NotOnline,
    /// The room is not open to friends.
    Closed,
    /// The invitation expired or does not exist.
    InviteExpired,
    /// The interactable is out of reach.
    TooFar,
    /// The door belongs to another house.
    WrongHouse,
    /// No interactable or entity with that id here.
    UnknownTarget,
    /// Too many actions; slow down.
    RateLimited,
    /// The server could not complete the action (e.g. a database error).
    Failed,
    /// The room already holds its owner and the most guests it takes.
    Full,
    /// Protocol v8: doors and stations refuse riders (land and dismount first).
    Mounted,
    /// Something stands on the piece's top or shelves: it cannot be moved or
    /// put away until that is taken off.
    NotEmpty,
    /// Solid furniture: the piece would stand on someone, on the room's entry,
    /// or close the way from the entry to the door out or to someone in the
    /// room (`sim_core::keeps_way`). Appended: older codes keep their bytes.
    BlocksWay,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ServerMsg {
    /// The client is in the world. Resets the visible set (see [`snapshot`]).
    Welcome {
        protocol_version: u16,
        your_id: EntityId,
        character: CharacterInfo,
        appearance_version: u32,
        appearance: Appearance,
        /// The client's own visible effects (which survive a relog until
        /// they expire); `OwnEffects` follows with their timers.
        effects_version: u32,
        effects: Vec<VisibleEffect>,
        /// The zone the client starts in (a new character's first one is
        /// their dorm), and its position and facing there.
        zone: Zone,
        spawn: QPos,
        yaw: u8,
        tick_hz: u32,
        snapshot_hz: u32,
        /// The server's clock (Unix ms, UTC) when this was
        /// sent. Game time and the class schedule are pure functions of it
        /// (`sim_core::clock`); the client corrects its own clock by the
        /// difference (and refines it with `Pong.server_time_ms`).
        unix_ms: u64,
    },
    /// Visible-set update; the exact rules are in [`snapshot`] and, for
    /// `entered`, [`looks`].
    Snapshot {
        tick: u32,
        entered: Vec<EntityEntry>,
        full: Vec<EntityState>,
        deltas: Vec<EntityDelta>,
        left: Vec<EntityId>,
        /// Protocol v10: whom members of the visible set now look at, for
        /// those whose look changed (see [`snapshot`], "Looks"); usually
        /// empty (one byte).
        looks: Vec<LookChange>,
    },
    /// Player counts for the HUD; sent about once a second. `nearby` counts
    /// characters near the client in its zone.
    Roster { online: u32, nearby: u32 },
    /// A chat line. `from == SERVER_ENTITY_ID` marks a server notice.
    Chat {
        from: EntityId,
        from_name: String,
        channel: ChatChannel,
        text: String,
    },
    /// The server rejected the client's move with sequence `seq`; snap to `pos`.
    Correction { seq: u32, pos: QPos },
    /// `server_time_ms` is the server clock (Unix ms, UTC), like `Welcome.unix_ms`.
    Pong {
        client_time_ms: u32,
        server_time_ms: u64,
    },
    /// The server is at its player cap; the client is waiting in the login queue.
    /// Sent on join and whenever the position changes; `Welcome` follows when admitted.
    Queued { position: u32 },
    /// The connection is about to close. `reason` is player-facing text.
    Kick { code: KickCode, reason: String },
    /// The chat line the client sent with `client_seq` was not delivered.
    ChatRejected {
        client_seq: u32,
        reason: ChatRejectReason,
    },
    /// The client is now in another zone, at `spawn` facing `yaw`: swap the
    /// scene, drop every remote character (the visible set resets) and reset
    /// movement prediction. A dorm zone is followed by `DormState`.
    ZoneChanged { zone: Zone, spawn: QPos, yaw: u8 },
    /// A visible entity (or the client itself, `id == your_id`) changed its look.
    AppearanceChanged {
        id: EntityId,
        appearance_version: u32,
        appearance: Appearance,
    },
    /// The dorm the client just entered: every placed item, whether it is open
    /// to friends and the item cap.
    DormState {
        open_to_friends: bool,
        cap: u16,
        items: Vec<PlacedFurniture>,
    },
    /// A live furniture edit in the client's current dorm.
    DormFurniture { edit: FurnitureEdit },
    /// The owner opened or closed the room to friends.
    DormOpenChanged { open: bool },
    /// A friend invites the client into their dorm, or (protocol v9,
    /// `wishing_room` set) into the Wishing Room they are in.
    DormInviteReceived {
        invite_id: u32,
        from_name: String,
        expires_in_secs: u16,
        wishing_room: Option<WishingRoomKind>,
    },
    /// A dorm action, invitation, visit or door interaction failed.
    /// `item_id` names the item for furniture actions.
    DormError {
        code: DormErrorCode,
        item_id: Option<i64>,
    },
    /// A change to the client's friend list.
    Friend { event: FriendEvent },
    /// The friend the client invited declined (`ClientMsg::DormDeclineInvite`).
    DormInviteDeclined { by_name: String },

    // ---- Protocol v4.
    /// The client's Crown balance: sent after `Welcome` and whenever it
    /// changes (purchases, trades, finds, mail collected).
    Wallet { crowns: i64 },
    /// Unread owl mail: sent after `Welcome` and whenever mail arrives or is
    /// read, so the HUD can show the new-mail owl. Mail itself is HTTP.
    MailNotice { unread: u32 },
    /// The client used a station or the Owl Exchange counter: open its UI.
    /// The HTTP side accepts that station's requests while the client stays
    /// within reach, for at most `expires_in_secs`. `target` is the
    /// interactable id, or `item:<items.id>` for a placed cauldron.
    StationOpened {
        kind: StationKind,
        target: String,
        expires_in_secs: u16,
    },
    /// The result of searching a find spot (`Interact` with a `find_spot`).
    FindResult { spot: String, outcome: FindOutcome },
    /// Another player asks the client to trade.
    TradeRequested {
        trade_id: u32,
        from: EntityId,
        from_name: String,
        expires_in_secs: u16,
    },
    /// The trade window, after every change (to both sides).
    TradeState { view: TradeView },
    /// The trade window or request ended (to both sides).
    TradeResult {
        trade_id: u32,
        outcome: TradeOutcome,
    },
    /// A trade action was refused; the window, if any, is unchanged.
    TradeError {
        trade_id: Option<u32>,
        code: TradeErrorCode,
    },
    /// A visible character's potion effects changed (the client itself
    /// included, `id == your_id`). Sent once per tick with the newest set.
    EffectsChanged {
        id: EntityId,
        effects_version: u32,
        effects: Vec<VisibleEffect>,
    },
    /// The client's own active effects with their timers, and the movement
    /// speed (percent of normal) the server now validates its moves with:
    /// predict with `MoveParams` scaled by it. Sent after `Welcome` and on
    /// every change.
    OwnEffects {
        effects: Vec<OwnEffect>,
        speed_pct: u16,
        /// Protocol v8: the broom's speed in percent of its tier's
        /// (`BROOM_PCT_NORMAL`; Featherlight Elixir 115), which the server
        /// validates flight with.
        broom_pct: u16,
    },
    /// A potion was drunk (`Drink`, or `MimicDraughtAsk` once consented); the
    /// item is used up. `OwnEffects` follows.
    Drank { item_id: i64, effect: EffectKind },
    /// A friend asks to copy the client's look with Mimic Draught.
    MimicDraughtRequest {
        request_id: u32,
        from: EntityId,
        from_name: String,
        expires_in_secs: u16,
    },
    /// The friend asked with `MimicDraughtAsk` declined; the potion is kept.
    MimicDraughtDeclined { by_name: String },
    /// A potion action failed. `item_id` names the potion when there is one.
    PotionError {
        code: PotionErrorCode,
        item_id: Option<i64>,
    },

    // ---- Protocol v5: levels and spells.
    /// The client's own level and XP: after `Welcome`, and after every XP
    /// grant (`gained` says what it was; `None` after `Welcome`).
    Progress {
        progress: Progress,
        gained: Option<XpGain>,
    },
    /// A visible character's (or the client's own) level changed. Like
    /// `EffectsChanged`, it carries the new `effects_version` the look is
    /// cached under.
    LevelChanged {
        id: EntityId,
        level: u8,
        effects_version: u32,
    },
    /// The client's spells: every learned one, the dueling loadout and how
    /// many slots it has. After `Welcome` and on every change; `new` lists
    /// the spells just learned (empty after `Welcome`).
    Spellbook {
        learned: Vec<SpellId>,
        loadout: Vec<SpellId>,
        slots: u8,
        new: Vec<SpellId>,
    },
    /// Someone cast a spell outside a duel (a flourish, Lucerna, Elevara): the
    /// gesture and colour, for everyone near enough to see it.
    SpellCast {
        caster: EntityId,
        spell: SpellId,
        target: SpellTarget,
    },
    /// A furniture piece of this dorm room floats and bobs for
    /// `duration_ms` (Elevara); its position does not change.
    Levitating {
        item_id: i64,
        by: EntityId,
        duration_ms: u16,
    },
    /// A spell cast outside a duel was refused.
    SpellError {
        spell: SpellId,
        code: SpellErrorCode,
    },
    /// A scripted NPC acts (speech, a wand demonstration, an emote).
    NpcEvent { id: EntityId, event: NpcEvent },

    // ---- Classes.
    /// The client entered a class section (after `ZoneChanged`).
    ClassJoined { info: ClassInfo },
    /// The lesson moved to `phase` (to every student of the section).
    LessonPhaseChanged {
        phase: LessonPhase,
        until_unix_ms: u64,
    },
    /// A practice round's beats (to every student, as it starts). A trace
    /// counts when it starts within `window_ms` of its beat, a pick when it
    /// lands within `window_ms` of it.
    LessonRound {
        round: u8,
        window_ms: u16,
        beats: Vec<LessonBeat>,
    },
    /// The server's score for one `TraceGesture` or `PickAnswer` (protocol
    /// v7; was `GestureScored`).
    BeatScored {
        round: u8,
        beat: u8,
        result: GestureResult,
    },
    /// The client's result and rewards for the session (once, at Results).
    ClassResult {
        class: String,
        session: i64,
        /// Average of the beats (missed ones count 0), 0-100.
        score_pct: u8,
        grade: Grade,
        rewards: ClassRewards,
    },
    /// Entering or taking part in a class failed.
    ClassError { code: ClassErrorCode },

    // ---- Dueling.
    /// A duel challenge, to its target (through the notice backlog) and
    /// echoed to the challenger (`from` = its own id) with the id to cancel.
    DuelChallenged {
        challenge_id: u32,
        from: EntityId,
        from_name: String,
        from_level: u8,
        expires_in_secs: u16,
    },
    /// A challenge ended (to both sides).
    DuelChallengeEnded {
        challenge_id: u32,
        reason: ChallengeEnd,
    },
    /// A duel action was refused.
    DuelError {
        challenge_id: Option<u32>,
        code: DuelErrorCode,
    },
    /// The client is in an arena (after `ZoneChanged`): the duelists, the
    /// rules' numbers and where the duel stands.
    DuelStart {
        duel_id: u32,
        you: DuelRole,
        a: DuelistInfo,
        b: DuelistInfo,
        poise_max: u16,
        time_limit_ms: u32,
        state: DuelSnapshot,
    },
    /// What happened this tick (to the arena's members only).
    DuelEvents {
        duel_id: u32,
        at_ms: u32,
        events: Vec<DuelEvent>,
    },
    /// The duel is over; everyone returns to the club in `return_in_ms`.
    /// `rewards` for duelists only.
    DuelResult {
        duel_id: u32,
        winner: Option<DuelSide>,
        reason: DuelEndReason,
        poise: [u16; 2],
        rewards: Option<DuelRewards>,
        return_in_ms: u32,
    },
    /// Duels the client may watch (`DuelListRequest`).
    DuelList { duels: Vec<DuelSummary> },

    // ---- Protocol v8: flying.
    /// The client's mount state: after every `Mount` request (with `reason`
    /// when it was refused) and whenever the server changes it. `tier` is
    /// the broom's speed tier while mounted (1..; `data/flying.json`
    /// `tiers`, by the Flying skill level), 0 when not. Predict flight with
    /// `sim_core::flight::FlightRules::params(tier, OwnEffects.broom_pct)`.
    Mounted {
        on: bool,
        tier: u8,
        reason: Option<MountRefused>,
    },

    // ---- Protocol v9: secrets and the Wishing Room.
    /// The Wishing Room's door shows for the client on the blank wall
    /// until `open_until_ms` (the server's Unix ms); 0 takes it away now.
    WishingRoomDoor { open_until_ms: u64 },
    /// The client found a secret (`data/world/secrets.json` id) for the
    /// first time: the XP it gave (`Progress` follows), whether a House Cup
    /// point came with it, and the recipe it taught.
    Discovered {
        secret: String,
        xp: u32,
        house_point: bool,
        recipe: Option<u16>,
    },

    // ---- Protocol v11: spells to level 20.
    /// Protocol v11: the server moves the client's character (Adveni): slide
    /// from where it is to `to` over `move_ms`, then stand still until
    /// `hold_ms` (both from now). Input is ignored meanwhile; the client's
    /// moves are dropped (not corrected) until then. `by` cast it.
    ForcedMove {
        to: QPos,
        move_ms: u16,
        hold_ms: u16,
        by: EntityId,
    },
    /// Protocol v11: Aperio, to its caster only: these spots shine for
    /// `duration_ms` (at most `REVEAL_SPOTS_MAX`, nearest first).
    Revealed {
        spell: SpellId,
        duration_ms: u16,
        spots: Vec<RevealedSpot>,
    },

    // ---- Protocol v12: NPCs in the open castle.
    /// Protocol v12: the answer to `NpcInteract { id }`, to the talking
    /// player only. The line itself is the client's (`data/npcs/lines.json`).
    NpcReply { id: EntityId, reply: NpcReply },

    // ---- Protocol v14: parties ([`party`]).
    /// Someone invites the client into their party (through the notice
    /// backlog). Answer with `PartyRespond` within `expires_in_secs`.
    PartyInvited {
        invite_id: u32,
        from_name: String,
        from_house: House,
        from_level: u8,
        expires_in_secs: u16,
    },
    /// The client's party after any change (members, leader, who is online
    /// and where), or `None`: the client is in no party (it left, was
    /// removed, or the party disbanded). Also sent after `Welcome` while the
    /// character is in a party.
    PartyState { party: Option<PartyView> },
    /// What happened to the party, for the chat log.
    PartyNotice { notice: PartyNotice },
    /// A party action was refused.
    PartyError { code: PartyErrorCode },

    // ---- Protocol v15: clubs ([`club`]).
    /// An officer or the owner invites the client into their club (through
    /// the notice backlog). Answer with `ClubRespond` within
    /// `expires_in_secs`.
    ClubInvited {
        invite_id: u32,
        club_name: String,
        club_tag: String,
        from_name: String,
        expires_in_secs: u16,
    },
    /// The client's club after any change of members, ranks, name, tag or
    /// message of the day, or `None`: the client is in no club (it left, was
    /// removed, or the club disbanded). Also sent after `Welcome` while the
    /// character is in a club.
    ClubState { club: Option<ClubView> },
    /// A member of the client's club came online, went offline or changed
    /// level (that roster row only; the rest of the club is unchanged).
    ClubPresence {
        character_id: i64,
        online: bool,
        level: u8,
    },
    /// What happened to the club, for the chat log.
    ClubNotice { notice: ClubNotice },
    /// A club action was refused.
    ClubError { code: ClubErrorCode },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CodecError {
    Malformed,
}

pub fn encode_client(msg: &ClientMsg) -> Vec<u8> {
    postcard::to_allocvec(msg).expect("client message encodes")
}

pub fn decode_client(bytes: &[u8]) -> Result<ClientMsg, CodecError> {
    postcard::from_bytes(bytes).map_err(|_| CodecError::Malformed)
}

/// The protocol version of a frame that starts like a `ClientMsg::Hello`
/// of any version (the variant, then `protocol_version`), even when the
/// rest does not decode with this version's fields. `None` for anything
/// else.
pub fn hello_version(bytes: &[u8]) -> Option<u16> {
    #[derive(Deserialize)]
    enum HelloStart {
        Hello { protocol_version: u16 },
    }
    match postcard::take_from_bytes::<HelloStart>(bytes) {
        Ok((HelloStart::Hello { protocol_version }, _)) => Some(protocol_version),
        Err(_) => None,
    }
}

pub fn encode_server(msg: &ServerMsg) -> Vec<u8> {
    postcard::to_allocvec(msg).expect("server message encodes")
}

pub fn decode_server(bytes: &[u8]) -> Result<ServerMsg, CodecError> {
    postcard::from_bytes(bytes).map_err(|_| CodecError::Malformed)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dressed() -> Appearance {
        let worn = |item_def, dyes| Some(Worn::new(item_def, dyes));
        Appearance {
            body_type: 1,
            height: 140,
            face: [128, 90, 160, 128, 255],
            skin_tone: 4,
            hair_style: 18,
            hair_colour: 3,
            eye_colour: 9,
            outfit: Outfit {
                hat: worn(1001, [3, 4, 5]),
                body: worn(1, [0, 1, 2]),
                legs: worn(200, [6, 6, 6]),
                shoes: worn(300, [12, 0, 47]),
            },
        }
    }

    fn server_round_trip(msg: ServerMsg) {
        assert_eq!(decode_server(&encode_server(&msg)).unwrap(), msg, "{msg:?}");
    }

    fn client_round_trip(msg: ClientMsg) {
        assert_eq!(decode_client(&encode_client(&msg)).unwrap(), msg, "{msg:?}");
    }

    #[test]
    fn snapshots_round_trip() {
        server_round_trip(ServerMsg::Snapshot {
            tick: 7,
            entered: vec![
                EntityEntry::Full(EntityInfo {
                    id: 3,
                    character: CharacterInfo {
                        name: "Ada".into(),
                        house: House::Skytalon,
                    },
                    appearance_version: 4,
                    appearance: dressed(),
                    effects_version: 2,
                    effects: vec![
                        VisibleEffect::Glow { colour: 21 },
                        VisibleEffect::Scale { pct: 50 },
                        VisibleEffect::Disguise {
                            appearance: Appearance::default(),
                        },
                        VisibleEffect::Lucerna,
                    ],
                    level: 7,
                    npc: None,
                }),
                EntityEntry::Cached {
                    id: 70_004,
                    appearance_version: 2,
                    effects_version: 0,
                },
            ],
            full: vec![
                EntityState {
                    id: 3,
                    pos: QPos::from_m([12.34, 5.0, -100.25]),
                    yaw: yaw_to_u8(1.0),
                    anim: Anim::Walk,
                },
                EntityState {
                    id: 70_004,
                    pos: QPos::from_m([1.0, 0.0, 2.0]),
                    yaw: 0,
                    anim: Anim::Idle,
                },
            ],
            deltas: vec![EntityDelta {
                index: 0,
                dx: -40,
                dy: 0,
                dz: 12,
                yaw: 3,
                anim: Anim::Sit,
            }],
            left: vec![9],
            looks: vec![
                LookChange {
                    id: 3,
                    target: Some(70_004),
                },
                LookChange {
                    id: 70_004,
                    target: None,
                },
            ],
        });
    }

    #[test]
    fn every_server_message_round_trips() {
        let dorm = Zone::Dorm {
            owner_name: "Ada".into(),
            house: House::Burrowsby,
            layout: "dorm_burrowsby".into(),
        };
        let placed = PlacedFurniture {
            item_id: 9_007_199_254_740_000,
            item_def: 2001,
            dyes: [1, 2, 3],
            pos: QPos::from_m([1.25, 0.0, -2.5]),
            yaw: 64,
        };
        let castle = Location::Castle {
            place: "Great Hall".into(),
        };
        let msgs = vec![
            ServerMsg::Welcome {
                protocol_version: PROTOCOL_VERSION,
                your_id: 5,
                character: CharacterInfo {
                    name: "Mara".into(),
                    house: House::Skytalon,
                },
                appearance_version: 2,
                appearance: dressed(),
                effects_version: 1,
                effects: vec![VisibleEffect::NameplateHidden],
                zone: dorm.clone(),
                spawn: QPos::from_m([1.5, 0.0, 2.25]),
                yaw: 128,
                tick_hz: WORLD_TICK_HZ,
                snapshot_hz: WORLD_SNAPSHOT_HZ,
                unix_ms: 1_790_000_000_000,
            },
            ServerMsg::Roster {
                online: 900,
                nearby: 12,
            },
            ServerMsg::Chat {
                from: 4,
                from_name: "Tobin".into(),
                channel: ChatChannel::Whisper { to: "Mara".into() },
                text: "hi".into(),
            },
            ServerMsg::Correction {
                seq: 3,
                pos: QPos::from_m([1.0, 2.0, 3.0]),
            },
            ServerMsg::Pong {
                client_time_ms: 1,
                server_time_ms: 1_700_000_000_000,
            },
            ServerMsg::Queued { position: 4 },
            ServerMsg::Kick {
                code: KickCode::Replaced,
                reason: "elsewhere".into(),
            },
            ServerMsg::ChatRejected {
                client_seq: 77,
                reason: ChatRejectReason::UnknownTarget,
            },
            ServerMsg::ZoneChanged {
                zone: Zone::Castle,
                spawn: QPos::from_m([10.0, 42.0, -3.0]),
                yaw: 0,
            },
            ServerMsg::ZoneChanged {
                zone: dorm,
                spawn: QPos::from_m([0.0, 0.0, 0.0]),
                yaw: 200,
            },
            ServerMsg::AppearanceChanged {
                id: 70_000,
                appearance_version: 9,
                appearance: Appearance::default(),
            },
            ServerMsg::DormState {
                open_to_friends: true,
                cap: DORM_ITEM_CAP,
                items: vec![placed],
            },
            ServerMsg::DormFurniture {
                edit: FurnitureEdit::Placed(placed),
            },
            ServerMsg::DormFurniture {
                edit: FurnitureEdit::Moved {
                    item_id: 5,
                    pos: QPos::from_m([0.5, 0.0, 0.5]),
                    yaw: 32,
                },
            },
            ServerMsg::DormFurniture {
                edit: FurnitureEdit::Removed { item_id: 5 },
            },
            ServerMsg::DormOpenChanged { open: false },
            ServerMsg::DormInviteReceived {
                invite_id: 12,
                from_name: "Ada".into(),
                expires_in_secs: DORM_INVITE_TTL_SECS,
                wishing_room: None,
            },
            ServerMsg::DormInviteReceived {
                invite_id: 13,
                from_name: "Ada".into(),
                expires_in_secs: DORM_INVITE_TTL_SECS,
                wishing_room: Some(WishingRoomKind::Gathering),
            },
            ServerMsg::DormError {
                code: DormErrorCode::CapReached,
                item_id: Some(5),
            },
            ServerMsg::DormError {
                code: DormErrorCode::WrongHouse,
                item_id: None,
            },
            ServerMsg::DormError {
                code: DormErrorCode::Full,
                item_id: None,
            },
            ServerMsg::DormInviteDeclined {
                by_name: "Bea".into(),
            },
        ];
        for m in msgs
            .into_iter()
            .chain(economy_messages())
            .chain(school_messages())
            .chain(dueling_messages())
            .chain(flying_messages())
            .chain(secrets_messages())
            .chain(spells_l20_messages())
        {
            server_round_trip(m);
        }
        let events = [
            FriendEvent::RequestReceived {
                request_id: 1,
                character_id: 2,
                name: "Bea".into(),
                house: House::Serpentyne,
            },
            FriendEvent::Added {
                character_id: 2,
                name: "Bea".into(),
                house: House::Serpentyne,
                location: Some(castle.clone()),
                room_open: true,
            },
            FriendEvent::RequestClosed { request_id: 1 },
            FriendEvent::Removed { character_id: 2 },
            FriendEvent::Online {
                character_id: 2,
                location: castle,
                room_open: false,
            },
            FriendEvent::Moved {
                character_id: 2,
                location: Location::Dorm {
                    owner_name: "Bea".into(),
                },
            },
            FriendEvent::Offline { character_id: 2 },
            FriendEvent::RoomOpen {
                character_id: 2,
                open: true,
            },
        ];
        for event in events {
            server_round_trip(ServerMsg::Friend { event });
        }
    }

    #[test]
    fn an_older_hello_still_shows_its_version() {
        // A v12 Hello: no `character_id`.
        #[derive(Serialize)]
        enum OldClientMsg {
            Hello {
                protocol_version: u16,
                token: String,
            },
        }
        let old = postcard::to_allocvec(&OldClientMsg::Hello {
            protocol_version: 12,
            token: "tok".into(),
        })
        .unwrap();
        assert!(decode_client(&old).is_err());
        assert_eq!(hello_version(&old), Some(12));
        let new = encode_client(&ClientMsg::Hello {
            protocol_version: PROTOCOL_VERSION,
            token: "tok".into(),
            character_id: None,
        });
        assert_eq!(hello_version(&new), Some(PROTOCOL_VERSION));
        assert_eq!(
            hello_version(&encode_client(&ClientMsg::Ping { client_time_ms: 1 })),
            None
        );
    }

    #[test]
    fn every_client_message_round_trips() {
        let msgs = vec![
            ClientMsg::Hello {
                protocol_version: PROTOCOL_VERSION,
                token: "tok".into(),
                character_id: Some(42),
            },
            ClientMsg::Move {
                seq: 1,
                pos: [1.0, 2.0, 3.0],
                yaw: 0.5,
                anim: Anim::Run,
            },
            ClientMsg::Chat {
                channel: ChatChannel::House,
                text: "hello".into(),
                client_seq: 3,
            },
            ClientMsg::SetViewLimit { n: 100 },
            ClientMsg::Ping { client_time_ms: 9 },
            ClientMsg::Emote { emote: Emote::Bow },
            ClientMsg::Interact {
                target: "dorm_door_house_b".into(),
            },
            ClientMsg::LeaveInstance,
            ClientMsg::DormPlace {
                item_id: 42,
                pos: QPos::from_m([1.0, 0.0, 2.0]),
                yaw: 64,
            },
            ClientMsg::DormMove {
                item_id: 42,
                pos: QPos::from_m([1.05, 0.0, 2.0]),
                yaw: 65,
            },
            ClientMsg::DormRemove { item_id: 42 },
            ClientMsg::DormSetOpen { open: true },
            ClientMsg::DormInvite {
                character_name: "Bea".into(),
            },
            ClientMsg::DormAcceptInvite { invite_id: 12 },
            ClientMsg::DormVisit {
                owner_name: "Bea".into(),
            },
            ClientMsg::DormKick { entity: 70_001 },
            ClientMsg::DormDeclineInvite { invite_id: 12 },
            ClientMsg::UseFurniture { item_id: 77 },
            ClientMsg::TradeRequest { target: 70_002 },
            ClientMsg::TradeRespond {
                trade_id: 3,
                accept: true,
            },
            ClientMsg::TradeSetOffer {
                trade_id: 3,
                items: vec![
                    OfferItem {
                        item_id: 9_007_199_254_740_000,
                        quantity: 1,
                    },
                    OfferItem {
                        item_id: 12,
                        quantity: 40,
                    },
                ],
                crowns: 250,
            },
            ClientMsg::TradeLock {
                trade_id: 3,
                locked: true,
            },
            ClientMsg::TradeConfirm {
                trade_id: 3,
                rev: 9,
            },
            ClientMsg::TradeCancel { trade_id: 3 },
            ClientMsg::Drink { item_id: 5 },
            ClientMsg::MimicDraughtAsk {
                item_id: 5,
                target: 70_002,
            },
            ClientMsg::MimicDraughtAnswer {
                request_id: 4,
                accept: false,
            },
            ClientMsg::TraceGesture {
                round: 1,
                beat: 3,
                start_offset_ms: -40,
                points: vec![
                    TracePoint {
                        dt_ms: 0,
                        x: -80,
                        y: 40,
                    },
                    TracePoint {
                        dt_ms: 16,
                        x: -70,
                        y: 20,
                    },
                ],
            },
            ClientMsg::CastSpell {
                spell: 2,
                target: SpellTarget::Furniture {
                    item_id: 9_007_199_254_740_000,
                },
            },
            ClientMsg::CastSpell {
                spell: 1,
                target: SpellTarget::None,
            },
            ClientMsg::CastSpell {
                spell: 11,
                target: SpellTarget::Entity { id: 70_002 },
            },
            ClientMsg::EndSpell { spell: 1 },
            ClientMsg::PickAnswer {
                round: 2,
                beat: 5,
                choice: 3,
                offset_ms: -180,
            },
            ClientMsg::DuelChallenge { target: 70_002 },
            ClientMsg::DuelChallengeName { name: "Bea".into() },
            ClientMsg::DuelRespond {
                challenge_id: 2,
                accept: true,
            },
            ClientMsg::DuelCancel { challenge_id: 2 },
            ClientMsg::DuelCast { spell: 11 },
            ClientMsg::DuelForfeit,
            ClientMsg::DuelListRequest,
            ClientMsg::DuelSpectate { duel_id: 9 },
            ClientMsg::Mount { on: true },
            ClientMsg::Mount { on: false },
            ClientMsg::Move {
                seq: 9,
                pos: [12.5, 30.25, 210.0],
                yaw: 1.0,
                anim: Anim::Fly,
            },
        ];
        for m in msgs {
            client_round_trip(m);
        }
    }

    /// One of every protocol v9 server message and value.
    fn secrets_messages() -> Vec<ServerMsg> {
        let mut out: Vec<ServerMsg> = WishingRoomKind::ALL
            .map(|room| ServerMsg::ZoneChanged {
                zone: Zone::WishingRoom {
                    owner_name: "Ada".into(),
                    room,
                    layout: "wishing_room_gathering".into(),
                },
                spawn: QPos::from_m([0.0, 0.0, 6.0]),
                yaw: 128,
            })
            .into();
        out.extend([
            ServerMsg::WishingRoomDoor {
                open_until_ms: 1_790_000_260_000,
            },
            ServerMsg::WishingRoomDoor { open_until_ms: 0 },
            ServerMsg::Discovered {
                secret: "weavers_stair".into(),
                xp: 100,
                house_point: true,
                recipe: Some(20),
            },
            ServerMsg::Discovered {
                secret: "sculptors_gap".into(),
                xp: 100,
                house_point: false,
                recipe: None,
            },
            ServerMsg::Friend {
                event: FriendEvent::Moved {
                    character_id: 2,
                    location: Location::WishingRoom {
                        owner_name: "Bea".into(),
                    },
                },
            },
        ]);
        out
    }

    /// Protocol v9 appends its messages and values after v8's.
    #[test]
    fn v9_appends_its_variants() {
        let first = |b: Vec<u8>| b[0];
        assert_eq!(
            first(encode_client(&ClientMsg::Mount { on: true })) + 1,
            first(encode_client(&ClientMsg::EnterWishingRoom {
                room: WishingRoomKind::LostProperty
            }))
        );
        let mounted = first(encode_server(&ServerMsg::Mounted {
            on: false,
            tier: 0,
            reason: None,
        }));
        assert_eq!(
            mounted + 1,
            first(encode_server(&ServerMsg::WishingRoomDoor {
                open_until_ms: 0
            }))
        );
        assert_eq!(
            mounted + 2,
            first(encode_server(&ServerMsg::Discovered {
                secret: String::new(),
                xp: 0,
                house_point: false,
                recipe: None
            }))
        );
        for m in [
            ClientMsg::EnterWishingRoom {
                room: WishingRoomKind::Gathering,
            },
            ClientMsg::EnterWishingRoom {
                room: WishingRoomKind::LostProperty,
            },
        ] {
            client_round_trip(m);
        }
    }

    /// Protocol v10 appends `LookAt` after v9's messages; a snapshot with
    /// no look changes costs one byte more than v9's, and a look change a
    /// few.
    #[test]
    fn v10_look_at_appends_and_stays_small() {
        let first = |b: Vec<u8>| b[0];
        assert_eq!(
            first(encode_client(&ClientMsg::EnterWishingRoom {
                room: WishingRoomKind::LostProperty
            })) + 1,
            first(encode_client(&ClientMsg::LookAt { target: None }))
        );
        for m in [
            ClientMsg::LookAt { target: None },
            ClientMsg::LookAt {
                target: Some(0x0003_0011),
            },
            ClientMsg::LookAt {
                target: Some(EntityId::MAX),
            },
        ] {
            client_round_trip(m);
        }
        assert_eq!(encode_client(&ClientMsg::LookAt { target: None }).len(), 2);
        let empty = |looks: Vec<LookChange>| {
            encode_server(&ServerMsg::Snapshot {
                tick: 1_000,
                entered: Vec::new(),
                full: Vec::new(),
                deltas: Vec::new(),
                left: vec![5],
                looks,
            })
        };
        let none = empty(Vec::new());
        assert_eq!(*none.last().unwrap(), 0, "an empty look list is one byte");
        let one = empty(vec![LookChange {
            id: 0x0001_0002,
            target: Some(0x0001_0003),
        }]);
        assert!(
            one.len() - none.len() <= 8,
            "{} bytes",
            one.len() - none.len()
        );
        let cleared = empty(vec![LookChange {
            id: 0x0001_0002,
            target: None,
        }]);
        assert!(cleared.len() - none.len() <= 5);
        server_round_trip(decode_server(&one).unwrap());
    }

    /// One of every protocol v11 (spells to level 20) server message and value.
    fn spells_l20_messages() -> Vec<ServerMsg> {
        let mut out = vec![
            ServerMsg::ForcedMove {
                to: QPos::from_m([12.5, 3.0, -40.25]),
                move_ms: 350,
                hold_ms: 450,
                by: 0x0001_0007,
            },
            ServerMsg::Revealed {
                spell: 5,
                duration_ms: 10_000,
                spots: vec![
                    RevealedSpot {
                        kind: RevealKind::Passage,
                        pos: QPos::from_m([1.0, 0.0, 2.0]),
                    },
                    RevealedSpot {
                        kind: RevealKind::FindSpot,
                        pos: QPos::from_m([-3.0, 4.5, 8.0]),
                    },
                ],
            },
            ServerMsg::Revealed {
                spell: 5,
                duration_ms: 10_000,
                spots: Vec::new(),
            },
            ServerMsg::Levitating {
                item_id: 9,
                by: 0x0001_0002,
                duration_ms: 0,
            },
            ServerMsg::EffectsChanged {
                id: 0x0001_0002,
                effects_version: 3,
                effects: vec![
                    VisibleEffect::Bound,
                    VisibleEffect::Slowed { pct: 40 },
                    VisibleEffect::Recoloured { colour: 21 },
                ],
            },
            ServerMsg::DuelEvents {
                duel_id: 4,
                at_ms: 9_000,
                events: vec![
                    DuelEvent::Rooted {
                        side: DuelSide::A,
                        until_ms: 10_500,
                    },
                    DuelEvent::Slowed {
                        side: DuelSide::B,
                        until_ms: 12_000,
                        pct: 50,
                    },
                    DuelEvent::Pulled {
                        side: DuelSide::B,
                        from: [550, -20],
                        to: [250, -10],
                        until_ms: 9_300,
                    },
                    DuelEvent::Staggered {
                        side: DuelSide::B,
                        until_ms: 9_500,
                    },
                    DuelEvent::Cleansed {
                        side: DuelSide::A,
                        spell: 16,
                    },
                    DuelEvent::WardUp {
                        side: DuelSide::A,
                        spell: 18,
                        until_ms: 19_000,
                    },
                    DuelEvent::Warded {
                        id: 7,
                        by: DuelSide::A,
                    },
                    DuelEvent::WardFaded { side: DuelSide::B },
                    DuelEvent::Refused {
                        side: DuelSide::B,
                        spell: 11,
                        reason: CastRefusal::Staggered,
                    },
                ],
            },
        ];
        out.extend(GuardianAnimal::ALL.map(|animal| ServerMsg::EffectsChanged {
            id: 3,
            effects_version: 1,
            effects: vec![VisibleEffect::Guardian {
                animal,
                guarding: animal == GuardianAnimal::Owl,
            }],
        }));
        out.extend(
            [
                SpellErrorCode::NotFriend,
                SpellErrorCode::NeedsTarget,
                SpellErrorCode::Unavailable,
                SpellErrorCode::NoPath,
            ]
            .map(|code| ServerMsg::SpellError { spell: 4, code }),
        );
        out
    }

    /// Protocol v11 appends its messages and values after v10's: older
    /// variants keep their bytes.
    #[test]
    fn v11_appends_its_variants() {
        let first = |b: Vec<u8>| b[0];
        let discovered = first(encode_server(&ServerMsg::Discovered {
            secret: String::new(),
            xp: 0,
            house_point: false,
            recipe: None,
        }));
        assert_eq!(
            discovered + 1,
            first(encode_server(&ServerMsg::ForcedMove {
                to: QPos::from_m([0.0; 3]),
                move_ms: 0,
                hold_ms: 0,
                by: 0,
            }))
        );
        assert_eq!(
            discovered + 2,
            first(encode_server(&ServerMsg::Revealed {
                spell: 0,
                duration_ms: 0,
                spots: Vec::new(),
            }))
        );
        // Enums: postcard writes the variant index first; the new ones come last.
        let tag = |v: Vec<u8>| v[0];
        let last_old = tag(postcard::to_allocvec(&EffectKind::Featherlight).unwrap());
        for (k, kind) in [
            EffectKind::Petrified,
            EffectKind::Slowed,
            EffectKind::Recoloured,
            EffectKind::Guardian,
        ]
        .into_iter()
        .enumerate()
        {
            assert_eq!(
                tag(postcard::to_allocvec(&kind).unwrap()),
                last_old + 1 + k as u8
            );
        }
        let lucerna = tag(postcard::to_allocvec(&VisibleEffect::Lucerna).unwrap());
        assert_eq!(
            tag(postcard::to_allocvec(&VisibleEffect::Bound).unwrap()),
            lucerna + 1
        );
        assert_eq!(
            tag(postcard::to_allocvec(&VisibleEffect::Guardian {
                animal: GuardianAnimal::Hare,
                guarding: false
            })
            .unwrap()),
            lucerna + 4
        );
        let rate = tag(postcard::to_allocvec(&SpellErrorCode::RateLimited).unwrap());
        assert_eq!(
            tag(postcard::to_allocvec(&SpellErrorCode::NoPath).unwrap()),
            rate + 4
        );
        let not_fighting = tag(postcard::to_allocvec(&CastRefusal::NotFighting).unwrap());
        assert_eq!(
            tag(postcard::to_allocvec(&CastRefusal::Staggered).unwrap()),
            not_fighting + 1
        );
        let ended = tag(postcard::to_allocvec(&DuelEvent::Ended {
            winner: None,
            reason: DuelEndReason::Poise,
        })
        .unwrap());
        assert_eq!(
            tag(postcard::to_allocvec(&DuelEvent::Rooted {
                side: DuelSide::A,
                until_ms: 0
            })
            .unwrap()),
            ended + 1
        );
        assert_eq!(
            tag(postcard::to_allocvec(&DuelEvent::WardFaded { side: DuelSide::A }).unwrap()),
            ended + 8
        );
        // Ids and kinds.
        for a in GuardianAnimal::ALL {
            assert_eq!(GuardianAnimal::from_id(a.id()), Some(a));
            assert_eq!(serde_json::to_value(a).unwrap(), a.id());
        }
        assert_eq!(GuardianAnimal::from_id("dragon"), None);
        for k in EffectKind::ALL {
            assert_eq!(
                k.is_hindrance(),
                matches!(k, EffectKind::Petrified | EffectKind::Slowed),
                "{k:?}"
            );
            assert_eq!(k.is_potion(), EffectKind::POTIONS.contains(&k), "{k:?}");
            if k.is_hindrance() {
                assert!(k.is_spell() && !k.is_potion());
            }
        }
        assert!(EffectKind::Recoloured.is_spell() && EffectKind::Guardian.is_spell());
        assert!(!EffectKind::Fortune.is_spell() && !EffectKind::Glow.is_spell());
        assert_eq!(
            serde_json::to_value(RevealKind::FindSpot).unwrap(),
            "find_spot"
        );
        assert_eq!(SPEED_PCT_MIN, 0);
    }

    /// Protocol v12 appends `NpcInteract` after `LookAt` and `NpcReply`
    /// after `Revealed`; every reply round-trips and stays small.
    #[test]
    fn v12_npcs_append_and_round_trip() {
        let first = |b: Vec<u8>| b[0];
        assert_eq!(
            first(encode_client(&ClientMsg::LookAt { target: None })) + 1,
            first(encode_client(&ClientMsg::NpcInteract { id: 7 }))
        );
        let revealed = first(encode_server(&ServerMsg::Revealed {
            spell: 0,
            duration_ms: 0,
            spots: Vec::new(),
        }));
        assert_eq!(
            revealed + 1,
            first(encode_server(&ServerMsg::NpcReply {
                id: 1,
                reply: NpcReply::Talk { given_today: false },
            }))
        );
        client_round_trip(ClientMsg::NpcInteract { id: 0x0004_0021 });
        let replies = [
            NpcReply::Talk { given_today: false },
            NpcReply::Talk { given_today: true },
            NpcReply::Gift {
                gift: NpcGift::Lore {
                    lore_id: 14,
                    title: "The Portrait Guardians".into(),
                },
            },
            NpcReply::Gift {
                gift: NpcGift::Item {
                    item_def: 3025,
                    quantity: 2,
                },
            },
            NpcReply::Gift {
                gift: NpcGift::HousePoint { points: 1 },
            },
            NpcReply::Gift {
                gift: NpcGift::Hint {
                    place: "Kitchen Corridor".into(),
                },
            },
            NpcReply::Refused {
                code: NpcErrorCode::TooFar,
            },
            NpcReply::Refused {
                code: NpcErrorCode::UnknownNpc,
            },
            NpcReply::Refused {
                code: NpcErrorCode::RateLimited,
            },
            NpcReply::Refused {
                code: NpcErrorCode::Failed,
            },
        ];
        for reply in replies {
            server_round_trip(ServerMsg::NpcReply { id: 9, reply });
        }
        let gift = encode_server(&ServerMsg::NpcReply {
            id: 0x0004_0021,
            reply: NpcReply::Gift {
                gift: NpcGift::Item {
                    item_def: 3025,
                    quantity: 2,
                },
            },
        });
        assert!(gift.len() <= 10, "{} bytes", gift.len());
        // The gift's JSON shape (`npc_gifts.gift`) is externally tagged.
        assert_eq!(
            serde_json::to_value(NpcGift::HousePoint { points: 1 }).unwrap(),
            serde_json::json!({ "house_point": { "points": 1 } })
        );
        assert_eq!(
            serde_json::to_value(NpcErrorCode::UnknownNpc).unwrap(),
            "unknown_npc"
        );
    }

    /// Protocol v14 appends the party messages after `NpcInteract` and
    /// `NpcReply`, `ChatChannel::Party` after `Whisper` and
    /// `ChatRejectReason::NoChannel` after `Invalid`; older values keep
    /// their bytes and every party message round-trips.
    #[test]
    fn v14_parties_append_and_round_trip() {
        let first = |b: Vec<u8>| b[0];
        assert_eq!(
            first(encode_client(&ClientMsg::NpcInteract { id: 7 })) + 1,
            first(encode_client(&ClientMsg::PartyInvite { name: "A".into() }))
        );
        assert_eq!(
            first(encode_server(&ServerMsg::NpcReply {
                id: 1,
                reply: NpcReply::Talk { given_today: false },
            })) + 1,
            first(encode_server(&ServerMsg::PartyInvited {
                invite_id: 1,
                from_name: "A".into(),
                from_house: House::Skytalon,
                from_level: 3,
                expires_in_secs: PARTY_INVITE_TTL_SECS,
            }))
        );
        // Older chat values keep their encoding.
        let chat = |channel| {
            encode_client(&ClientMsg::Chat {
                channel,
                text: String::new(),
                client_seq: 0,
            })
        };
        assert_eq!(chat(ChatChannel::Say)[1], 0);
        assert_eq!(chat(ChatChannel::House)[1], 1);
        assert_eq!(chat(ChatChannel::Party)[1], 3);
        assert_eq!(
            encode_server(&ServerMsg::ChatRejected {
                client_seq: 0,
                reason: ChatRejectReason::NoChannel,
            })[2],
            4
        );
        for msg in [
            ClientMsg::PartyInvite {
                name: "Anne Marie".into(),
            },
            ClientMsg::PartyRespond {
                invite_id: 9,
                accept: true,
            },
            ClientMsg::PartyLeave,
            ClientMsg::PartyKick { character_id: 42 },
            ClientMsg::PartyPromote { character_id: 43 },
            ClientMsg::Chat {
                channel: ChatChannel::Party,
                text: "to the library".into(),
                client_seq: 5,
            },
        ] {
            client_round_trip(msg);
        }
        let member = |id: i64, place: Option<PartyPlace>| PartyMember {
            character_id: id,
            name: format!("M{id}"),
            house: House::Burrowsby,
            level: 4,
            online: place.map(|place| PartyPresence {
                entity: 0x0003_0007,
                zone: 12,
                place,
            }),
        };
        let view = PartyView {
            leader: 1,
            members: vec![
                member(
                    1,
                    Some(PartyPlace::Castle {
                        place: "Great Hall".into(),
                    }),
                ),
                member(
                    2,
                    Some(PartyPlace::Dorm {
                        owner_name: "M2".into(),
                        house: House::Serpentyne,
                    }),
                ),
                member(
                    3,
                    Some(PartyPlace::WishingRoom {
                        owner_name: "M1".into(),
                    }),
                ),
                member(
                    4,
                    Some(PartyPlace::Class {
                        class: "charms".into(),
                    }),
                ),
                member(
                    5,
                    Some(PartyPlace::Arena {
                        venue: "dueling_club".into(),
                    }),
                ),
                member(6, None),
            ],
        };
        let state = ServerMsg::PartyState {
            party: Some(view.clone()),
        };
        // A full party of six stays well inside one frame.
        assert!(encode_server(&state).len() < 400);
        server_round_trip(state);
        server_round_trip(ServerMsg::PartyState { party: None });
        for notice in [
            PartyNotice::InviteSent { name: "A".into() },
            PartyNotice::Declined { name: "A".into() },
            PartyNotice::Joined { name: "A".into() },
            PartyNotice::Left { name: "A".into() },
            PartyNotice::Removed { name: "A".into() },
            PartyNotice::Leader { name: "A".into() },
            PartyNotice::Disbanded,
        ] {
            server_round_trip(ServerMsg::PartyNotice { notice });
        }
        server_round_trip(ServerMsg::PartyError {
            code: PartyErrorCode::Full,
        });
        server_round_trip(ServerMsg::Chat {
            from: 3,
            from_name: "A".into(),
            channel: ChatChannel::Party,
            text: "hi".into(),
        });
        assert_eq!(usize::from(PARTY_MAX), view.members.len());
    }

    /// Protocol v15 appends the club messages after the party ones and
    /// `ChatChannel::Club` after `Party`; every club message round-trips.
    #[test]
    fn v15_clubs_append_and_round_trip() {
        let first = |b: Vec<u8>| b[0];
        assert_eq!(
            first(encode_client(&ClientMsg::PartyPromote { character_id: 1 })) + 1,
            first(encode_client(&ClientMsg::ClubFound {
                secretary: 1,
                name: "Chess Club".into(),
                tag: "CHS".into(),
            }))
        );
        assert_eq!(
            first(encode_server(&ServerMsg::PartyError {
                code: PartyErrorCode::Full,
            })) + 1,
            first(encode_server(&ServerMsg::ClubInvited {
                invite_id: 1,
                club_name: "Chess Club".into(),
                club_tag: "CHS".into(),
                from_name: "A".into(),
                expires_in_secs: CLUB_INVITE_TTL_SECS,
            }))
        );
        let chat = encode_client(&ClientMsg::Chat {
            channel: ChatChannel::Club,
            text: String::new(),
            client_seq: 0,
        });
        assert_eq!(chat[1], 4);
        for msg in [
            ClientMsg::ClubFound {
                secretary: 0x0001_0002,
                name: "The Night Owls".into(),
                tag: "OWL".into(),
            },
            ClientMsg::ClubInvite {
                name: "Anne Marie".into(),
            },
            ClientMsg::ClubRespond {
                invite_id: 3,
                accept: false,
            },
            ClientMsg::ClubLeave,
            ClientMsg::ClubKick { character_id: 9 },
            ClientMsg::ClubSetRole {
                character_id: 9,
                role: ClubRole::Officer,
            },
            ClientMsg::ClubTransfer { character_id: 9 },
            ClientMsg::ClubRename {
                name: "Owls".into(),
                tag: "OWL2".into(),
            },
            ClientMsg::ClubSetMotd {
                motd: "Meet at the lake".into(),
            },
            ClientMsg::ClubDisband,
            ClientMsg::Chat {
                channel: ChatChannel::Club,
                text: "hello".into(),
                client_seq: 2,
            },
        ] {
            client_round_trip(msg);
        }
        let members: Vec<ClubMember> = (0..100)
            .map(|k| ClubMember {
                character_id: 1_000 + k,
                name: format!("Member Number {k}"),
                house: House::Skytalon,
                level: 20,
                role: match k {
                    0 => ClubRole::Owner,
                    1..=4 => ClubRole::Officer,
                    _ => ClubRole::Member,
                },
                online: k % 2 == 0,
            })
            .collect();
        let state = ServerMsg::ClubState {
            club: Some(ClubView {
                id: 7,
                name: "The Night Owls".into(),
                tag: "OWL".into(),
                motd: "Meet at the lake".into(),
                members,
            }),
        };
        // A full club of a hundred stays a few kilobytes.
        assert!(encode_server(&state).len() < 4_000);
        server_round_trip(state);
        server_round_trip(ServerMsg::ClubState { club: None });
        server_round_trip(ServerMsg::ClubPresence {
            character_id: 4,
            online: true,
            level: 9,
        });
        for notice in [
            ClubNotice::Founded { club: "A".into() },
            ClubNotice::InviteSent { name: "A".into() },
            ClubNotice::Declined { name: "A".into() },
            ClubNotice::Joined { name: "A".into() },
            ClubNotice::Left { name: "A".into() },
            ClubNotice::Removed { name: "A".into() },
            ClubNotice::Promoted { name: "A".into() },
            ClubNotice::Demoted { name: "A".into() },
            ClubNotice::Owner { name: "A".into() },
            ClubNotice::Renamed {
                club: "A".into(),
                tag: "AB".into(),
            },
            ClubNotice::Motd { name: "A".into() },
            ClubNotice::Disbanded { club: "A".into() },
        ] {
            server_round_trip(ServerMsg::ClubNotice { notice });
        }
        server_round_trip(ServerMsg::ClubError {
            code: ClubErrorCode::NameTaken,
        });
        server_round_trip(ServerMsg::Chat {
            from: 3,
            from_name: "A".into(),
            channel: ChatChannel::Club,
            text: "hi".into(),
        });
        for role in [ClubRole::Member, ClubRole::Officer, ClubRole::Owner] {
            assert_eq!(ClubRole::from_id(role.id()), Some(role));
        }
        assert!(ClubRole::Member < ClubRole::Officer && ClubRole::Officer < ClubRole::Owner);
    }

    /// One of every protocol v8 server message and value.
    fn flying_messages() -> Vec<ServerMsg> {
        let mut out = vec![
            ServerMsg::Mounted {
                on: true,
                tier: 2,
                reason: None,
            },
            ServerMsg::DormError {
                code: DormErrorCode::Mounted,
                item_id: None,
            },
            ServerMsg::LessonRound {
                round: 1,
                window_ms: 2_000,
                beats: vec![LessonBeat {
                    beat: 0,
                    prompt: BeatPrompt::Ring { ring: 7 },
                    at_unix_ms: 1_790_000_206_000,
                }],
            },
            ServerMsg::Drank {
                item_id: 77,
                effect: EffectKind::Featherlight,
            },
        ];
        out.extend(MountRefused::ALL.map(|r| ServerMsg::Mounted {
            on: false,
            tier: 0,
            reason: Some(r),
        }));
        out
    }

    /// One of every protocol v4 (economy and crafting) server message.
    fn economy_messages() -> Vec<ServerMsg> {
        let offer = TradeOffer {
            items: vec![OfferedItem {
                item_id: 12,
                item_def: 3001,
                dyes: [9, 9, 9],
                finish: Finish::None,
                quantity: 40,
                maker: None,
            }],
            crowns: 0,
        };
        let crafted = TradeOffer {
            items: vec![OfferedItem {
                item_id: 13,
                item_def: 110,
                dyes: [16, 21, 0],
                finish: Finish::Shimmer,
                quantity: 1,
                maker: Some("Mara".into()),
            }],
            crowns: 1_000_000,
        };
        vec![
            ServerMsg::Wallet { crowns: 1_250 },
            ServerMsg::MailNotice { unread: 3 },
            ServerMsg::StationOpened {
                kind: StationKind::OwlExchange,
                target: "owl_exchange_counter".into(),
                expires_in_secs: STATION_GRANT_SECS,
            },
            ServerMsg::StationOpened {
                kind: StationKind::Cauldron,
                target: "item:77".into(),
                expires_in_secs: STATION_GRANT_SECS,
            },
            ServerMsg::FindResult {
                spot: "find_eh_alcove_west".into(),
                outcome: FindOutcome::Found {
                    items: vec![FoundItem {
                        item_def: 3001,
                        quantity: 2,
                    }],
                    crowns: 5,
                },
            },
            ServerMsg::FindResult {
                spot: "find_wt_top".into(),
                outcome: FindOutcome::Cooldown {
                    ready_in_secs: 3600,
                },
            },
            ServerMsg::FindResult {
                spot: "find_wt_top".into(),
                outcome: FindOutcome::Nothing,
            },
            ServerMsg::TradeRequested {
                trade_id: 3,
                from: 70_001,
                from_name: "Ada".into(),
                expires_in_secs: TRADE_REQUEST_SECS,
            },
            ServerMsg::TradeState {
                view: TradeView {
                    trade_id: 3,
                    partner: 70_001,
                    partner_name: "Ada".into(),
                    rev: 4,
                    mine: offer,
                    theirs: crafted,
                    mine_locked: true,
                    theirs_locked: false,
                    mine_confirmed: false,
                    theirs_confirmed: false,
                },
            },
            ServerMsg::TradeResult {
                trade_id: 3,
                outcome: TradeOutcome::Completed,
            },
            ServerMsg::TradeResult {
                trade_id: 3,
                outcome: TradeOutcome::Failed(TradeErrorCode::InsufficientCrowns),
            },
            ServerMsg::TradeError {
                trade_id: None,
                code: TradeErrorCode::Busy,
            },
            ServerMsg::EffectsChanged {
                id: 70_001,
                effects_version: 7,
                effects: vec![
                    VisibleEffect::Chameleon {
                        part: ChameleonPart::Hair,
                        family: 3,
                    },
                    VisibleEffect::Steam,
                    VisibleEffect::Lucky,
                    VisibleEffect::Babbling,
                ],
            },
            ServerMsg::OwnEffects {
                effects: vec![
                    OwnEffect {
                        kind: EffectKind::PickMeUpTonic,
                        remaining_secs: 599,
                        fortune: None,
                    },
                    OwnEffect {
                        kind: EffectKind::Fortune,
                        remaining_secs: 3_600,
                        fortune: Some(FortuneKind::QuickHands),
                    },
                ],
                speed_pct: 115,
                broom_pct: 115,
            },
            ServerMsg::Drank {
                item_id: 5,
                effect: EffectKind::Glow,
            },
            ServerMsg::MimicDraughtRequest {
                request_id: 4,
                from: 70_001,
                from_name: "Ada".into(),
                expires_in_secs: MIMIC_DRAUGHT_REQUEST_SECS,
            },
            ServerMsg::MimicDraughtDeclined {
                by_name: "Bea".into(),
            },
            ServerMsg::PotionError {
                code: PotionErrorCode::NotFriend,
                item_id: Some(5),
            },
        ]
    }

    /// One of every protocol v5 school message (levels, spells, NPCs, classes).
    fn school_messages() -> Vec<ServerMsg> {
        let progress = Progress {
            level: 4,
            xp: 820,
            level_xp: 760,
            next_level_xp: Some(1_160),
        };
        vec![
            ServerMsg::Progress {
                progress,
                gained: None,
            },
            ServerMsg::Progress {
                progress: Progress {
                    level: LEVEL_MAX,
                    next_level_xp: None,
                    ..progress
                },
                gained: Some(XpGain {
                    source: XpSource::DuelWin,
                    amount: 12,
                    capped: true,
                }),
            },
            ServerMsg::LevelChanged {
                id: 70_001,
                level: 5,
                effects_version: 3,
            },
            ServerMsg::Spellbook {
                learned: vec![1, 2, 10, 11, 12],
                loadout: vec![10, 11, 12],
                slots: 3,
                new: vec![2],
            },
            ServerMsg::SpellCast {
                caster: 70_001,
                spell: 11,
                target: SpellTarget::Entity { id: 70_002 },
            },
            ServerMsg::Levitating {
                item_id: 77,
                by: 70_001,
                duration_ms: 5_000,
            },
            ServerMsg::SpellError {
                spell: 2,
                code: SpellErrorCode::NotHere,
            },
            ServerMsg::NpcEvent {
                id: 70_009,
                event: NpcEvent::Say {
                    text: "Twirl and tap!".into(),
                },
            },
            ServerMsg::NpcEvent {
                id: 70_009,
                event: NpcEvent::Demonstrate {
                    gesture: 1,
                    spell: Some(2),
                    duration_ms: 1_800,
                },
            },
            ServerMsg::NpcEvent {
                id: 70_009,
                event: NpcEvent::Emote { emote: Emote::Bow },
            },
            ServerMsg::ClassJoined {
                info: ClassInfo {
                    class: "charms".into(),
                    session: 1_234_567,
                    section: 1,
                    start_unix_ms: 1_790_000_000_000,
                    join_until_unix_ms: 1_790_000_180_000,
                    end_unix_ms: 1_790_000_540_000,
                    professor: 70_009,
                    phase: LessonPhase::Gathering,
                    phase_until_unix_ms: 1_790_000_180_000,
                },
            },
            ServerMsg::LessonPhaseChanged {
                phase: LessonPhase::Practice { round: 2 },
                until_unix_ms: 1_790_000_300_000,
            },
            ServerMsg::LessonRound {
                round: 2,
                window_ms: 600,
                beats: vec![
                    LessonBeat {
                        beat: 0,
                        prompt: BeatPrompt::Trace { gesture: 1 },
                        at_unix_ms: 1_790_000_242_000,
                    },
                    LessonBeat {
                        beat: 1,
                        prompt: BeatPrompt::Pick {
                            prompt: 7,
                            options: vec![3, 7, 11, 300, 0, 65_535],
                        },
                        at_unix_ms: 1_790_000_244_500,
                    },
                ],
            },
            ServerMsg::BeatScored {
                round: 2,
                beat: 1,
                result: GestureResult {
                    score: 0,
                    grade: Grade::Miss,
                    rejected: Some(TraceReject::WrongBeatKind),
                },
            },
            ServerMsg::BeatScored {
                round: 2,
                beat: 0,
                result: GestureResult {
                    score: 0,
                    grade: Grade::Miss,
                    rejected: Some(TraceReject::TooPerfect),
                },
            },
            ServerMsg::ClassResult {
                class: "charms".into(),
                session: 1_234_567,
                score_pct: 81,
                grade: Grade::Great,
                rewards: ClassRewards {
                    xp: 140,
                    xp_capped: false,
                    skill_xp: 60,
                    house_points: 5,
                    spells: vec![1, 2],
                    crowns: 0,
                    items: Vec::new(),
                    recipes: Vec::new(),
                    lore: None,
                    hint: None,
                    fortune: None,
                },
            },
            ServerMsg::ClassResult {
                class: "potions".into(),
                session: 1_234_570,
                score_pct: 92,
                grade: Grade::Perfect,
                rewards: ClassRewards {
                    xp: 60,
                    xp_capped: true,
                    skill_xp: 66,
                    house_points: 5,
                    spells: Vec::new(),
                    crowns: 19,
                    items: vec![
                        RewardItem {
                            item_def: 3021,
                            quantity: 2,
                        },
                        RewardItem {
                            item_def: 3020,
                            quantity: 1,
                        },
                    ],
                    recipes: vec![330],
                    lore: Some(4),
                    hint: Some("Third-Floor Charms Corridor".into()),
                    fortune: Some(FortuneKind::KeenMind),
                },
            },
            ServerMsg::ClassError {
                code: ClassErrorCode::NotOpen,
            },
        ]
    }

    /// One of every protocol v5 dueling message.
    fn dueling_messages() -> Vec<ServerMsg> {
        let duelist = |entity, name: &str| DuelistInfo {
            entity,
            name: name.into(),
            house: House::Roarwing,
            level: 6,
            loadout: vec![10, 11, 12, 13],
        };
        let idle = DuelistState {
            poise: 100,
            casting: None,
            shield_until: None,
            disarmed_until: None,
            cooldowns: Vec::new(),
            rooted_until: None,
            slowed: None,
            staggered_until: None,
            ward_until: None,
        };
        vec![
            ServerMsg::DuelChallenged {
                challenge_id: 2,
                from: 70_001,
                from_name: "Ada".into(),
                from_level: 6,
                expires_in_secs: DUEL_CHALLENGE_SECS,
            },
            ServerMsg::DuelChallengeEnded {
                challenge_id: 2,
                reason: ChallengeEnd::Started,
            },
            ServerMsg::DuelError {
                challenge_id: None,
                code: DuelErrorCode::TargetNotInArea,
            },
            ServerMsg::DuelStart {
                duel_id: 9,
                you: DuelRole::Duelist(DuelSide::B),
                a: duelist(70_001, "Ada"),
                b: duelist(70_002, "Bea"),
                poise_max: 100,
                time_limit_ms: 90_000,
                state: DuelSnapshot {
                    at_ms: 4_200,
                    fight_at_ms: 3_000,
                    sides: [
                        DuelistState {
                            poise: 76,
                            casting: Some((12, 5_100)),
                            cooldowns: vec![(11, 5_800)],
                            ..idle.clone()
                        },
                        DuelistState {
                            shield_until: Some(4_600),
                            disarmed_until: Some(6_000),
                            ..idle
                        },
                    ],
                    projectiles: vec![ProjectileState {
                        id: 3,
                        side: DuelSide::A,
                        spell: 11,
                        pos: [-310, 0],
                        to: [2_400, 15],
                        speed_cms: 1_400,
                    }],
                },
            },
            ServerMsg::DuelEvents {
                duel_id: 9,
                at_ms: 4_250,
                events: vec![
                    DuelEvent::Fight,
                    DuelEvent::CastStarted {
                        side: DuelSide::A,
                        spell: 11,
                        release_ms: 4_850,
                    },
                    DuelEvent::Refused {
                        side: DuelSide::B,
                        spell: 12,
                        reason: CastRefusal::Cooldown,
                    },
                    DuelEvent::Interrupted {
                        side: DuelSide::B,
                        spell: 12,
                    },
                    DuelEvent::ShieldUp {
                        side: DuelSide::B,
                        spell: 10,
                        until_ms: 4_950,
                        perfect_until_ms: 4_450,
                    },
                    DuelEvent::Launched {
                        id: 4,
                        side: DuelSide::A,
                        spell: 11,
                        from: [-600, 0],
                        to: [2_400, 0],
                        speed_cms: 1_400,
                    },
                    DuelEvent::Blocked {
                        id: 4,
                        by: DuelSide::B,
                    },
                    DuelEvent::Reflected {
                        id: 5,
                        by: DuelSide::B,
                        from: [590, 0],
                        to: [-2_400, 0],
                    },
                    DuelEvent::ShieldBroken { side: DuelSide::B },
                    DuelEvent::Hit {
                        id: 5,
                        target: DuelSide::A,
                        damage: 18,
                        poise: 58,
                    },
                    DuelEvent::Disarmed {
                        side: DuelSide::B,
                        until_ms: 6_250,
                    },
                    DuelEvent::Missed { id: 6 },
                    DuelEvent::Ended {
                        winner: Some(DuelSide::A),
                        reason: DuelEndReason::Poise,
                    },
                ],
            },
            ServerMsg::DuelResult {
                duel_id: 9,
                winner: None,
                reason: DuelEndReason::Timeout,
                poise: [40, 40],
                rewards: Some(DuelRewards {
                    xp: 35,
                    capped: false,
                    house_points: 0,
                }),
                return_in_ms: 5_000,
            },
            ServerMsg::DuelList {
                duels: vec![DuelSummary {
                    duel_id: 9,
                    a_name: "Ada".into(),
                    a_house: House::Roarwing,
                    b_name: "Bea".into(),
                    b_house: House::Serpentyne,
                    spectators: 12,
                    elapsed_ms: 31_000,
                }],
            },
        ]
    }

    #[test]
    fn duel_traffic_is_compact() {
        // A typical tick: one cast and one projectile launch.
        let tick = encode_server(&ServerMsg::DuelEvents {
            duel_id: 9,
            at_ms: 41_250,
            events: vec![
                DuelEvent::CastStarted {
                    side: DuelSide::A,
                    spell: 11,
                    release_ms: 41_850,
                },
                DuelEvent::Launched {
                    id: 14,
                    side: DuelSide::B,
                    spell: 13,
                    from: [600, -12],
                    to: [-2_400, 30],
                    speed_cms: 1_800,
                },
            ],
        });
        assert!(tick.len() <= 32, "{} bytes", tick.len());
        // A traced gesture of 60 samples fits easily in an inbound frame.
        let trace = encode_client(&ClientMsg::TraceGesture {
            round: 1,
            beat: 7,
            start_offset_ms: 120,
            points: vec![
                TracePoint {
                    dt_ms: 16,
                    x: -100,
                    y: 100,
                };
                60
            ],
        });
        assert!(trace.len() <= 200, "{} bytes", trace.len());
        // A pick is a handful of bytes (variant, round, beat, choice, offset).
        let pick = encode_client(&ClientMsg::PickAnswer {
            round: 4,
            beat: 11,
            choice: 5,
            offset_ms: -1_200,
        });
        assert!(pick.len() <= 8, "{} bytes", pick.len());
        // A round of 12 six-option picks stays small.
        let round = encode_server(&ServerMsg::LessonRound {
            round: 4,
            window_ms: 1_400,
            beats: (0..12)
                .map(|k| LessonBeat {
                    beat: k,
                    prompt: BeatPrompt::Pick {
                        prompt: 300,
                        options: vec![1, 2, 3, 200, 201, 202],
                    },
                    at_unix_ms: 1_790_000_242_000 + u64::from(k) * 2_500,
                })
                .collect(),
        });
        assert!(round.len() <= 12 * 24, "{} bytes", round.len());
        // The most samples fit the 2 KB inbound frame.
        const { assert!(TRACE_POINTS_MAX * 4 < 2_048) };
    }

    #[test]
    fn effects_are_compact() {
        // The usual case: no effects costs one byte per entry.
        assert_eq!(
            postcard::to_allocvec(&Vec::<VisibleEffect>::new())
                .unwrap()
                .len(),
            1
        );
        let glow = postcard::to_allocvec(&vec![VisibleEffect::Glow { colour: 21 }]).unwrap();
        assert!(glow.len() <= 3, "{} bytes", glow.len());
        let notice = encode_server(&ServerMsg::EffectsChanged {
            id: 70_001,
            effects_version: 3,
            effects: vec![VisibleEffect::Steam, VisibleEffect::Scale { pct: 130 }],
        });
        assert!(notice.len() <= 12, "{} bytes", notice.len());
    }

    #[test]
    fn states_and_appearances_are_compact() {
        let s = EntityState {
            id: 900,
            pos: QPos::from_m([250.0, 12.5, -310.0]),
            yaw: 200,
            anim: Anim::Run,
        };
        assert!(postcard::to_allocvec(&s).unwrap().len() <= 14);
        // Fully dressed: about 44 bytes (one finish byte per slot), sent once
        // per enter.
        let a = postcard::to_allocvec(&dressed()).unwrap().len();
        assert!(a <= 48, "{a} bytes");
        let naked = postcard::to_allocvec(&Appearance::default()).unwrap().len();
        assert!(naked <= 18, "{naked} bytes");
    }

    #[test]
    fn appearance_json_is_the_documented_shape() {
        let json = serde_json::to_value(dressed()).unwrap();
        assert_eq!(json["outfit"]["hat"]["item_def"], 1001);
        assert_eq!(json["outfit"]["body"]["dyes"], serde_json::json!([0, 1, 2]));
        assert!(json.get("build").is_none());
        assert_eq!(json["eye_colour"], 9);
        assert_eq!(json["outfit"]["hat"]["finish"], "none");
        let back: Appearance = serde_json::from_value(json).unwrap();
        assert_eq!(back, dressed());
        assert_eq!(
            serde_json::to_value(GearSlot::Shoes).unwrap(),
            serde_json::json!("shoes")
        );
        // Looks stored before finishes existed have no finish.
        let old: Worn = serde_json::from_value(serde_json::json!({
            "item_def": 201, "dyes": [0, 16, 21]
        }))
        .unwrap();
        assert_eq!(old, Worn::new(201, [0, 16, 21]));
        // Looks stored before protocol 6 have a build and no eye colour: the
        // build is ignored and the eyes read as Brown.
        let mut v1 = serde_json::to_value(dressed()).unwrap();
        let o = v1.as_object_mut().unwrap();
        o.remove("eye_colour");
        o.insert("build".into(), serde_json::json!([30, 200, 0]));
        let back: Appearance = serde_json::from_value(v1).unwrap();
        assert_eq!(back.eye_colour, EYE_COLOUR_DEFAULT);
        assert_eq!(back.hair_style, 18);
    }

    #[test]
    fn ids_and_codes() {
        for h in House::ALL {
            assert_eq!(House::from_id(h.id()), Some(h));
        }
        for s in GearSlot::ALL {
            assert_eq!(GearSlot::from_id(s.id()), Some(s));
        }
        assert!(KickCode::ServerRestart.is_retryable());
        assert!(!KickCode::Replaced.is_retryable());
        assert_eq!(Emote::Sit.duration_ms(), None);
        assert_eq!(Emote::Wave.anim(), Anim::Wave);
        for f in Finish::ALL {
            assert_eq!(Finish::from_id(f.id()), Some(f));
            assert_eq!(serde_json::to_value(f).unwrap(), f.id());
        }
        for k in EffectKind::ALL {
            assert_eq!(EffectKind::from_id(k.id()), Some(k));
            assert_eq!(serde_json::to_value(k).unwrap(), k.id());
        }
        for k in StationKind::ALL {
            assert_eq!(StationKind::from_id(k.id()), Some(k));
            assert_eq!(serde_json::to_value(k).unwrap(), k.id());
        }
        for s in XpSource::ALL {
            assert_eq!(XpSource::from_id(s.id()), Some(s));
            assert_eq!(serde_json::to_value(s).unwrap(), s.id());
        }
        assert_eq!(XpSource::DuelLoss.cap_group(), Some("duel"));
        // Both class sources share the daily `class` cap.
        assert_eq!(XpSource::ClassAttend.cap_group(), Some("class"));
        assert_eq!(XpSource::ClassBonus.cap_group(), Some("class"));
        assert_eq!(XpSource::Admin.cap_group(), None);
        for f in FortuneKind::ALL {
            assert_eq!(FortuneKind::from_id(f.id()), Some(f));
            assert_eq!(serde_json::to_value(f).unwrap(), f.id());
        }
        assert!(EffectKind::POTIONS.iter().all(|k| k.is_potion()));
        assert!(!EffectKind::Fortune.is_potion());
        // Protocol v11: the fortune and the four spell effects are not potions.
        assert_eq!(EffectKind::ALL.len(), EffectKind::POTIONS.len() + 5);
        assert_eq!(DuelSide::A.other(), DuelSide::B);
        assert_eq!(DuelSide::B.index(), 1);
        assert_eq!(
            serde_json::to_value(Grade::Perfect).unwrap(),
            Grade::Perfect.id()
        );
        assert_eq!(EffectKind::Shrinking.excludes(), Some(EffectKind::Swelling));
        assert_eq!(EffectKind::Glow.excludes(), None);
        // Protocol v8.
        assert!(EffectKind::Featherlight.is_potion());
        assert_eq!(
            EffectKind::from_id("featherlight"),
            Some(EffectKind::Featherlight)
        );
        for r in MountRefused::ALL {
            assert_eq!(serde_json::to_value(r).unwrap(), r.id());
        }
        assert!(Anim::Fly.is_flying() && Anim::Hover.is_flying());
        assert!(!Anim::Fly.is_moving() && !Anim::Run.is_flying());
    }

    /// Protocol v8 appended its variants: every older message, anim, effect
    /// and code keeps its index.
    #[test]
    fn v8_appends_its_variants() {
        let first = |b: Vec<u8>| b[0];
        assert_eq!(
            first(encode_client(&ClientMsg::PickAnswer {
                round: 1,
                beat: 0,
                choice: 0,
                offset_ms: 0
            })) + 1,
            first(encode_client(&ClientMsg::Mount { on: true }))
        );
        assert_eq!(
            first(encode_server(&ServerMsg::DuelList { duels: Vec::new() })) + 1,
            first(encode_server(&ServerMsg::Mounted {
                on: false,
                tier: 0,
                reason: None
            }))
        );
        let byte = |v: &dyn erased::Encode| v.bytes()[0];
        assert_eq!(byte(&Anim::Cast) + 1, byte(&Anim::Fly));
        assert_eq!(byte(&Anim::Fly) + 1, byte(&Anim::Hover));
        assert_eq!(
            byte(&EffectKind::Fortune) + 1,
            byte(&EffectKind::Featherlight)
        );
        assert_eq!(
            byte(&DormErrorCode::Full) + 1,
            byte(&DormErrorCode::Mounted)
        );
        assert_eq!(
            byte(&DormErrorCode::Mounted) + 1,
            byte(&DormErrorCode::NotEmpty)
        );
        assert_eq!(
            byte(&DormErrorCode::NotEmpty) + 1,
            byte(&DormErrorCode::BlocksWay)
        );
    }

    /// A tiny object-safe postcard encoder for the variant-index checks.
    mod erased {
        pub trait Encode {
            fn bytes(&self) -> Vec<u8>;
        }
        impl<T: serde::Serialize> Encode for T {
            fn bytes(&self) -> Vec<u8> {
                postcard::to_allocvec(self).unwrap()
            }
        }
    }

    /// Protocol v7 kept every older message's variant index: only
    /// `GestureScored` was renamed (in place) and `PickAnswer` appended.
    #[test]
    fn v7_keeps_the_variant_order() {
        let first = |b: Vec<u8>| b[0];
        assert_eq!(
            first(encode_client(&ClientMsg::DuelSpectate { duel_id: 1 })) + 1,
            first(encode_client(&ClientMsg::PickAnswer {
                round: 1,
                beat: 0,
                choice: 0,
                offset_ms: 0
            }))
        );
        let round = first(encode_server(&ServerMsg::LessonRound {
            round: 1,
            window_ms: 600,
            beats: Vec::new(),
        }));
        let scored = first(encode_server(&ServerMsg::BeatScored {
            round: 1,
            beat: 0,
            result: GestureResult {
                score: 0,
                grade: Grade::Miss,
                rejected: None,
            },
        }));
        assert_eq!(round + 1, scored);
    }

    #[test]
    fn yaw_quantization_wraps() {
        assert_eq!(yaw_to_u8(0.0), 0);
        assert_eq!(yaw_to_u8(std::f32::consts::TAU), 0);
        assert_eq!(yaw_to_u8(-std::f32::consts::PI), 128);
    }
}
