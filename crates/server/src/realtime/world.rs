//! The world: one seamless simulation of the castle and grounds, plus the
//! dorm instances (one small room per owner, see [`super::instances`]), all
//! on one thread at a fixed [`WORLD_TICK_HZ`].
//!
//! Connection tasks talk to it only through a bounded command channel
//! ([`WorldCmd`]); it talks back through each player's bounded outbound queue
//! of pre-encoded frames. Database work (dorm rooms, friend reloads) goes out
//! as [`Job`]s and comes back as commands, so the tick never waits on
//! Postgres. Each tick:
//!
//! 1. drains queued commands: joins, leaves, moves (validated immediately with
//!    `sim_core::check_move` against the player's zone, and in the castle
//!    against their house's view of it, so rival common rooms are walls),
//!    chat (rate-limited and filtered, then queued), emotes, doors, dorm
//!    actions, results of database jobs;
//! 2. ends emotes that ran their time, expires invitations;
//! 3. rebuilds the castle's spatial grid;
//! 4. routes the queued chat;
//! 5. builds snapshots, in parallel on the snapshot pool, for the half of the
//!    clients whose phase matches this tick (so each client gets 10 Hz), then
//!    delivers the chat lines and snapshots (delivery wakes the connection
//!    tasks, so it waits until the parallel part is done);
//! 6. sends rosters to the twentieth of the clients due this tick (1 Hz each);
//! 7. disconnects clients whose outbound queue overflowed;
//! 8. every 30 s, hands moved positions (and the map's new reveals)
//!    to the saver.
//!
//! Movement credit: a move may cover as much distance as the time the player
//! has *earned* allows (`credit`, seconds at `run_speed * speed_tolerance`,
//! accruing in real time and capped at [`MOVE_CREDIT_MAX`]). Using credit
//! instead of the gap between two packets keeps honest clients whose moves
//! arrive bunched up by network jitter from being corrected, while a client
//! that sends moves faster than 10 Hz cannot move faster than the speed limit.
//!
//! Spells to level 20 (protocol v11): *hindrance* effects (Rigora,
//! Tardara: `EffectKind::is_hindrance`) multiply the speed in every zone
//! and take hold at once (no grace for a new or stronger one); spell effects
//! end on the tick they expire ([`WorldSim::expire_spell_effects`]); the
//! server can slide a character itself ([`WorldSim::force_move`], Adveni),
//! dropping its moves meanwhile.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use protocol::{
    Anim, Appearance, CharacterInfo, ChatChannel, ChatRejectReason, ClientMsg, EffectKind,
    EntityId, EntityState, FriendEvent, House, KickCode, Location, OwnEffect, PROTOCOL_VERSION,
    QPos, SPEED_PCT_NORMAL, ServerMsg, StationKind, VIEW_LIMIT_MAX, VisibleEffect,
    WORLD_SNAPSHOT_HZ, WORLD_TICK_HZ, encode_server, yaw_to_u8,
};
use rayon::prelude::*;
use sim_core::{MoveCheck, MoveParams, check_move};
use tokio::sync::mpsc::error::{TryRecvError, TrySendError};
use tokio::sync::{mpsc, oneshot};

use super::ConnControl;
use super::chat::{self, WordFilter};
use super::club::ClubInput;
use super::entities::{CASTLE, Client, Entities, Info, MAX_PRIORITY, NewEntity, Social};
use super::ext::{
    ActiveEffect, ClassInput, DuelMsg, ExtDone, ExtJob, PotionInput, SpellInput, TradeInput,
    WorldCtx,
};
use super::grid::Grid;
use super::instances::{FeatureKind, Instances, Invites, RoomQueue};
use super::jobs::{Executor, Job, RoomOutcome};
use super::layouts::InstanceLayouts;
use super::limits;
use super::metrics::{OFF_GROUND, RealtimeMetrics, correction_index};
use super::persist::SaveBatch;
use super::snapshot::{self, Built};
use super::stairs::horizontal;
use super::stations::{self, StationAccess, StationGrant, StationTarget};
use super::visibility::Source;
use crate::characters::{Character, name_key};
use crate::clock::ServerClock;
use crate::dorm::DormShells;
use crate::progression::SchoolState;

pub const TICK: Duration = Duration::from_millis(1000 / WORLD_TICK_HZ as u64);
/// Seconds of movement a player starts with.
pub const JOIN_MOVE_CREDIT: f32 = 0.2;
/// Most movement time a player can bank (a 1 s burst after a network stall).
pub const MOVE_CREDIT_MAX: f32 = 1.0;
/// After a correction, further rejected moves within this window are dropped
/// silently: they were sent before the client saw the correction.
pub const CORRECTION_COOLDOWN: Duration = Duration::from_millis(250);
/// After a zone change, rejected moves within this window are dropped
/// silently: they were sent in the old zone (docs/architecture.md, "Zones").
pub const ZONE_CHANGE_GRACE: Duration = Duration::from_millis(500);
/// Positions of players who moved are saved this often.
pub const SAVE_INTERVAL_TICKS: u32 = 30 * WORLD_TICK_HZ;
/// `Roster.nearby` counts characters within this distance (metres).
pub const NEARBY_RADIUS_M: f32 = 30.0;
/// Capacity of the command channel (about three ticks of moves at the cap).
pub const COMMAND_QUEUE: usize = 32_768;
/// Commands handled per tick at most, so a flood cannot stall snapshots.
const MAX_COMMANDS_PER_TICK: usize = 20_000;
/// Players placed at the same layout spawn fill a spiral of this many slots
/// around it (out to about `SPAWN_SPACING * sqrt(SPAWN_SLOTS)` = 6 m).
const SPAWN_SLOTS: usize = 40;
/// Solid furniture: how far from a saved spot that is now inside a piece the
/// character may be put beside it (metres) before the spawn takes over.
const SAVED_SPOT_SLACK: f32 = 2.5;
/// Distance between neighbouring spawn slots (metres), well over a character's
/// width.
const SPAWN_SPACING: f32 = 1.0;
/// The golden angle (radians): successive slots never line up.
const GOLDEN_ANGLE: f32 = 2.399_963;

pub(super) const TICKS_PER_SNAPSHOT: u32 = WORLD_TICK_HZ / WORLD_SNAPSHOT_HZ;

/// Chat lines and new looks are only queued while more than 1/this of a
/// client's outbound queue is free (8 of 64 frames), keeping room for
/// snapshots and corrections.
const CHAT_QUEUE_RESERVE_DIV: usize = 8;

/// Friend events and invitations waiting for one client, at most.
pub const NOTICES_MAX: usize = 256;

/// Protocol v10: a look target is kept, and shown, within this distance
/// (m): the clients' `LOOK_RANGE_M` plus slack for positions in flight.
pub const LOOK_KEEP_M: f32 = protocol::LOOK_RANGE_M + 2.0;

/// Free frames a client's queue keeps for snapshots and corrections.
fn queue_reserve(c: &Client) -> usize {
    (c.out.max_capacity() / CHAT_QUEUE_RESERVE_DIV).max(1)
}

/// Player input forwarded by the gateway.
#[derive(Debug, Clone, PartialEq)]
pub enum Input {
    Move {
        seq: u32,
        pos: [f32; 3],
        yaw: f32,
        anim: Anim,
    },
    Chat {
        channel: ChatChannel,
        text: String,
        /// Echoed in `ChatRejected` if the line is refused.
        client_seq: u32,
    },
    ViewLimit(u8),
    Emote(protocol::Emote),
    Interact(String),
    LeaveInstance,
    /// Mount (`true`) or land (`false`) (`ClientMsg::Mount`).
    Mount(bool),
    /// Protocol v9: `ClientMsg::EnterWishingRoom`.
    EnterWishingRoom(protocol::WishingRoomKind),
    /// Protocol v10: `ClientMsg::LookAt`.
    LookAt(Option<EntityId>),
    /// Protocol v12: `ClientMsg::NpcInteract` (`crate::residents`).
    Npc(super::ext::NpcInput),
    /// Protocol v14: the party messages (`party.rs`).
    PartyInvite(String),
    PartyRespond {
        invite_id: u32,
        accept: bool,
    },
    PartyLeave,
    PartyKick(i64),
    PartyPromote(i64),
    /// Protocol v15: the club messages (`club.rs`).
    Club(ClubInput),
    /// Protocol v17: `ClientMsg::SetHotbar` (`crate::spells::hotbar`).
    SetHotbar(Vec<Option<protocol::SpellId>>),
    DormPlace {
        item_id: i64,
        pos: QPos,
        yaw: u8,
    },
    DormMove {
        item_id: i64,
        pos: QPos,
        yaw: u8,
    },
    DormRemove {
        item_id: i64,
    },
    DormSetOpen(bool),
    DormInvite(String),
    DormAcceptInvite(u32),
    DormVisit(String),
    DormKick(EntityId),
    DormDeclineInvite(u32),
    UseFurniture(i64),
    Trade(TradeInput),
    Potion(PotionInput),
    Class(ClassInput),
    Spell(SpellInput),
    Duel(DuelMsg),
}

impl Input {
    /// The world's part of a client message; `None` for the ones the gateway
    /// answers itself (`Hello`, `Ping`).
    pub fn from_client(msg: ClientMsg) -> Option<Input> {
        Some(match msg {
            ClientMsg::Hello { .. } | ClientMsg::Ping { .. } => return None,
            // Protocol v8: answered by `ServerMsg::Mounted`.
            ClientMsg::Mount { on } => Input::Mount(on),
            // Protocol v9.
            ClientMsg::EnterWishingRoom { room } => Input::EnterWishingRoom(room),
            // Protocol v10.
            ClientMsg::LookAt { target } => Input::LookAt(target),
            // Protocol v12.
            ClientMsg::NpcInteract { id } => Input::Npc(super::ext::NpcInput::Interact { id }),
            // Protocol v14.
            ClientMsg::PartyInvite { name } => Input::PartyInvite(name),
            ClientMsg::PartyRespond { invite_id, accept } => {
                Input::PartyRespond { invite_id, accept }
            }
            ClientMsg::PartyLeave => Input::PartyLeave,
            ClientMsg::PartyKick { character_id } => Input::PartyKick(character_id),
            ClientMsg::PartyPromote { character_id } => Input::PartyPromote(character_id),
            // Protocol v15.
            ClientMsg::ClubFound {
                secretary,
                name,
                tag,
            } => Input::Club(ClubInput::Found {
                secretary,
                name,
                tag,
            }),
            ClientMsg::ClubInvite { name } => Input::Club(ClubInput::Invite(name)),
            ClientMsg::ClubRespond { invite_id, accept } => {
                Input::Club(ClubInput::Respond { invite_id, accept })
            }
            ClientMsg::ClubLeave => Input::Club(ClubInput::Leave),
            ClientMsg::ClubKick { character_id } => Input::Club(ClubInput::Kick(character_id)),
            ClientMsg::ClubSetRole { character_id, role } => {
                Input::Club(ClubInput::SetRole { character_id, role })
            }
            ClientMsg::ClubTransfer { character_id } => {
                Input::Club(ClubInput::Transfer(character_id))
            }
            ClientMsg::ClubRename { name, tag } => Input::Club(ClubInput::Rename { name, tag }),
            ClientMsg::ClubSetMotd { motd } => Input::Club(ClubInput::SetMotd(motd)),
            ClientMsg::ClubDisband => Input::Club(ClubInput::Disband),
            ClientMsg::SetHotbar { slots } => Input::SetHotbar(slots),
            ClientMsg::Move {
                seq,
                pos,
                yaw,
                anim,
            } => Input::Move {
                seq,
                pos,
                yaw,
                anim,
            },
            ClientMsg::Chat {
                channel,
                text,
                client_seq,
            } => Input::Chat {
                channel,
                text,
                client_seq,
            },
            ClientMsg::SetViewLimit { n } => Input::ViewLimit(n),
            ClientMsg::Emote { emote } => Input::Emote(emote),
            ClientMsg::Interact { target } => Input::Interact(target),
            ClientMsg::LeaveInstance => Input::LeaveInstance,
            ClientMsg::DormPlace { item_id, pos, yaw } => Input::DormPlace { item_id, pos, yaw },
            ClientMsg::DormMove { item_id, pos, yaw } => Input::DormMove { item_id, pos, yaw },
            ClientMsg::DormRemove { item_id } => Input::DormRemove { item_id },
            ClientMsg::DormSetOpen { open } => Input::DormSetOpen(open),
            ClientMsg::DormInvite { character_name } => Input::DormInvite(character_name),
            ClientMsg::DormAcceptInvite { invite_id } => Input::DormAcceptInvite(invite_id),
            ClientMsg::DormVisit { owner_name } => Input::DormVisit(owner_name),
            ClientMsg::DormKick { entity } => Input::DormKick(entity),
            ClientMsg::DormDeclineInvite { invite_id } => Input::DormDeclineInvite(invite_id),
            ClientMsg::UseFurniture { item_id } => Input::UseFurniture(item_id),
            ClientMsg::TradeRequest { target } => Input::Trade(TradeInput::Request { target }),
            ClientMsg::TradeRespond { trade_id, accept } => {
                Input::Trade(TradeInput::Respond { trade_id, accept })
            }
            ClientMsg::TradeSetOffer {
                trade_id,
                items,
                crowns,
            } => Input::Trade(TradeInput::SetOffer {
                trade_id,
                items,
                crowns,
            }),
            ClientMsg::TradeLock { trade_id, locked } => {
                Input::Trade(TradeInput::Lock { trade_id, locked })
            }
            ClientMsg::TradeConfirm { trade_id, rev } => {
                Input::Trade(TradeInput::Confirm { trade_id, rev })
            }
            ClientMsg::TradeCancel { trade_id } => Input::Trade(TradeInput::Cancel { trade_id }),
            ClientMsg::Drink { item_id } => Input::Potion(PotionInput::Drink { item_id }),
            ClientMsg::MimicDraughtAsk { item_id, target } => {
                Input::Potion(PotionInput::MimicDraughtAsk { item_id, target })
            }
            ClientMsg::MimicDraughtAnswer { request_id, accept } => {
                Input::Potion(PotionInput::MimicDraughtAnswer { request_id, accept })
            }
            ClientMsg::TraceGesture {
                round,
                beat,
                start_offset_ms,
                points,
            } => Input::Class(ClassInput::Trace {
                round,
                beat,
                start_offset_ms,
                points,
            }),
            ClientMsg::PickAnswer {
                round,
                beat,
                choice,
                offset_ms,
            } => Input::Class(ClassInput::Pick {
                round,
                beat,
                choice,
                offset_ms,
            }),
            ClientMsg::CastSpell { spell, target } => {
                Input::Spell(SpellInput::Cast { spell, target })
            }
            ClientMsg::EndSpell { spell } => Input::Spell(SpellInput::End { spell }),
            ClientMsg::DuelChallenge { target } => Input::Duel(DuelMsg::Challenge { target }),
            ClientMsg::DuelChallengeName { name } => Input::Duel(DuelMsg::ChallengeName { name }),
            ClientMsg::DuelRespond {
                challenge_id,
                accept,
            } => Input::Duel(DuelMsg::Respond {
                challenge_id,
                accept,
            }),
            ClientMsg::DuelCancel { challenge_id } => Input::Duel(DuelMsg::Cancel { challenge_id }),
            ClientMsg::DuelCast { spell } => Input::Duel(DuelMsg::Cast { spell }),
            ClientMsg::DuelForfeit => Input::Duel(DuelMsg::Forfeit),
            ClientMsg::DuelListRequest => Input::Duel(DuelMsg::List),
            ClientMsg::DuelSpectate { duel_id } => Input::Duel(DuelMsg::Spectate { duel_id }),
        })
    }
}

/// An online character as friend lists show it (`WorldHandle::presence`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnlinePresence {
    pub location: Location,
    /// Their room is open to friends, so `DormVisit` would let a friend in.
    pub room_open: bool,
}

/// A character joining, with what the gateway loaded for it.
#[derive(Debug, Clone)]
pub struct Joining {
    pub character: Character,
    pub social: Social,
    /// The character's own room is open to friends.
    pub dorm_open: bool,
    /// The balance and unread mail for the HUD (sent as
    /// `Wallet` and `MailNotice` after `Welcome`), and the potion effects
    /// still running (`crate::effects::load_active`).
    pub crowns: i64,
    pub unread_mail: u32,
    pub effects: Vec<ActiveEffect>,
    /// XP, learned spells and the loadout
    /// (`crate::progression::load_school`), sent as `Progress` and
    /// `Spellbook` after `Welcome`.
    pub school: SchoolState,
    /// The Flying skill's XP (`skills` row `flying`; 0 without
    /// one): above 0 the character may mount a broom.
    pub flying_xp: u32,
    /// The map's reveals and the secrets found
    /// (`crate::map::load`).
    pub explored: crate::map::Explored,
    /// The club it is in (`crate::clubs::store::load_of`).
    pub club: Option<crate::clubs::Club>,
}

impl Joining {
    /// A character with no friends, blocks, open room, Crowns, mail,
    /// effects, XP or spells (bots, tests).
    pub fn alone(character: Character) -> Self {
        Self {
            character,
            social: Social::default(),
            dorm_open: false,
            crowns: 0,
            unread_mail: 0,
            effects: Vec::new(),
            school: SchoolState::default(),
            flying_xp: 0,
            explored: crate::map::Explored::default(),
            club: None,
        }
    }
}

/// A lifecycle callback for the feature handlers, delivered
/// at the next settle (the end of the command or tick that caused it), so
/// a handler that moves someone never hears of it re-entrantly.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum FeatureEvent {
    /// Entity `id` moved out of zone `from`.
    ZoneChanged {
        id: EntityId,
        from: super::entities::ZoneId,
    },
    /// A feature instance may have emptied: close it if nobody is left.
    Closing(super::entities::ZoneId),
}

pub enum WorldCmd {
    /// Adds a character. The world sends `Welcome` through `out` and replies
    /// with the entity id (`None` if the world is full).
    Join {
        joining: Box<Joining>,
        out: mpsc::Sender<Bytes>,
        control: Arc<ConnControl>,
        reply: oneshot::Sender<Option<EntityId>>,
    },
    /// Removes a character (saving its position). Stale ids are ignored.
    Leave { id: EntityId },
    /// `at` is when the gateway received it.
    Input {
        id: EntityId,
        at: Instant,
        input: Input,
    },
    /// A character's look changed (from the HTTP API): store it and tell
    /// everyone who sees them. Ignored if they are offline.
    AppearanceChanged {
        character_id: i64,
        appearance: Appearance,
        version: u32,
    },
    /// Sends a pre-encoded frame to a character if they are online.
    Notify { character_id: i64, frame: Bytes },
    /// Where each of these characters is; offline ones are left out.
    OnlineStatus {
        ids: Vec<i64>,
        reply: oneshot::Sender<HashMap<i64, OnlinePresence>>,
    },
    /// Whether the world still counts on this character: in the world, or
    /// away from a duel within its disconnect grace (deleting it is refused).
    InPlay {
        character_id: i64,
        reply: oneshot::Sender<bool>,
    },
    /// A character's friends or blocks changed in the database: reload them.
    FriendsChanged { character_id: i64 },
    /// A club changed in the database outside the world (a member's
    /// character deleted): reload it and tell its members `notices`.
    ClubChanged {
        club_id: i64,
        notices: Vec<protocol::ClubNotice>,
    },
    /// A friends reload finished (`None` if it failed).
    SocialLoaded {
        character_id: i64,
        seq: u64,
        social: Option<Social>,
    },
    /// A dorm room operation (load, edit, open flag) finished.
    RoomDone { owner: i64, outcome: RoomOutcome },
    /// Whether a character may use a station of `kind` now (`stations`).
    StationAccess {
        character_id: i64,
        kind: StationKind,
        reply: oneshot::Sender<Option<StationAccess>>,
    },
    /// Development only (`POST /api/dev/teleport`): moves a character to
    /// `pos` in their current zone if it is walkable ground there.
    DevTeleport {
        character_id: i64,
        pos: [f32; 3],
        reply: oneshot::Sender<bool>,
    },
    /// A feature's database job finished (`ext`).
    ExtDone(ExtDone),
    /// A character's XP changed (HTTP side, after committing
    /// a grant): store it, send `Progress`, and `LevelChanged` on a new level.
    ProgressChanged {
        character_id: i64,
        xp: u32,
        gained: Option<protocol::XpGain>,
    },
    /// A character's Flying skill XP changed (HTTP side, a dev
    /// grant): the world's cache of it (may they mount, at which tier).
    FlyingChanged { character_id: i64, xp: u32 },
    /// A character's spells or loadout changed (HTTP side).
    SpellbookChanged {
        character_id: i64,
        learned: Vec<protocol::SpellId>,
        loadout: Vec<protocol::SpellId>,
        new: Vec<protocol::SpellId>,
    },
    /// What an online character has explored (`GET /api/map`);
    /// `None` when offline.
    Explored {
        character_id: i64,
        reply: oneshot::Sender<Option<crate::map::Explored>>,
    },
    /// Disconnects everyone, saves every position and stops the world.
    Shutdown { done: oneshot::Sender<()> },
}

pub(super) struct PendingChat {
    pub from: EntityId,
    pub at: Instant,
    pub channel: ChatChannel,
    pub text: String,
    pub client_seq: u32,
}

pub struct WorldSim {
    /// The castle layout (interactables, place names) with every moving
    /// staircase flight at rest in position A.
    pub(super) castle: Arc<sim_core::World>,
    /// The castle file as loaded, both positions of every moving
    /// flight (`sim_core::HouseWorlds::new(castle_all.layout())` builds the
    /// 4 x 8 worlds the moves are checked against, by the schedule).
    pub(super) castle_all: Arc<sim_core::World>,
    /// The moving staircases' schedule (`data/world/stairs.json`),
    /// when the castle has moving flights.
    pub(super) stairs: Option<Arc<sim_core::Stairs>>,
    /// The castle as each house walks it (`World::for_house`), in every
    /// staircase state (4 x 8 worlds): moves are checked against
    /// the state's world, so other houses' common rooms are walls and each
    /// moving flight is where the schedule has it.
    pub(super) house_worlds: sim_core::HouseWorlds,
    /// The staircase state the castle's stored positions are in
    /// (`Stairs::state` at the last tick; riders are carried when it
    /// changes, [`WorldSim::step_stairs`]). Always 0 without moving flights.
    pub(super) stair_state: u8,
    /// The grounds' open sky (the castle layout's airspace and
    /// no-fly boxes; the same for every house).
    pub(super) castle_air: Arc<sim_core::Airspace>,
    pub(super) shells: DormShells,
    pub(super) params: MoveParams,
    pub(super) ents: Entities,
    /// The castle zone only; dorm occupants are left out.
    pub(super) grid: Grid,
    tick: u32,
    pub(super) by_character: HashMap<i64, EntityId>,
    pub(super) by_name: HashMap<String, EntityId>,
    pub(super) filter: WordFilter,
    saver: Option<mpsc::UnboundedSender<SaveBatch>>,
    pub(super) metrics: Arc<RealtimeMetrics>,
    pool: rayon::ThreadPool,
    pub(super) next_spawn: usize,
    pending_chat: Vec<PendingChat>,
    pub(super) instances: Instances,
    /// Each owner's queue of dorm room operations (outlives the instance).
    pub(super) rooms: HashMap<i64, RoomQueue>,
    pub(super) invites: Invites,
    /// Parties and their invitations (`party.rs`).
    pub(super) parties: super::party::Parties,
    /// Copies of the clubs with a member online, and club invitations
    /// (`club.rs`).
    pub(super) clubs: super::club::Clubs,
    /// Guests who disconnected inside a friend's dorm, by character id: a
    /// quick reconnect puts them back in while the owner is still online.
    pub(super) guest_memo: HashMap<i64, super::instances::GuestMemo>,
    /// Guests an owner sent out, by (owner, guest) character ids: until when
    /// they may not walk back in (`DormVisit`) uninvited.
    pub(super) kick_bars: HashMap<(i64, i64), Instant>,
    /// Friend-list changes for characters that were offline (or joining) when
    /// they happened; a join within a few seconds reloads.
    pub(super) social_changed: HashMap<i64, Instant>,
    /// Looks that changed (HTTP) while the character was offline or joining:
    /// the gateway may have read the character before the change committed,
    /// so a join within a few seconds takes the newer one.
    pub(super) pending_looks: HashMap<i64, (Appearance, u32, Instant)>,
    /// Characters whose look changed this tick (`flush_looks` sends them).
    looks_changed: Vec<EntityId>,
    /// Characters whose visible effects changed this tick (`flush_effects`).
    effects_changed: Vec<EntityId>,
    /// Spells L20: characters holding a spell effect (they expire on the
    /// tick, not once a second), and characters being moved by the server.
    spell_fx: Vec<EntityId>,
    forced: Vec<EntityId>,
    /// Station grants by (character id, kind) (`stations`).
    pub(super) station_grants: HashMap<(i64, StationKind), StationGrant>,
    /// Feature state, handled outside `realtime/` through
    /// [`WorldCtx`] (`ext`).
    pub(super) trades: crate::trade::world::Trades,
    pub(super) potions: crate::potions::world::Potions,
    pub(super) finds: crate::finds::world::Finds,
    /// Feature state (`ext`): classes, spells and duels.
    pub(super) classes: crate::classes::world::Classes,
    pub(super) spells: crate::spells::world::Spells,
    pub(super) duels: crate::dueling::world::Duels,
    /// NPCs in the open castle (`crate::residents`): the
    /// ghosts, staff, shopkeepers and students and their talk and gifts.
    pub(super) residents: crate::residents::world::Residents,
    /// Lifecycle callbacks waiting for the next settle.
    pub(super) feature_events: std::collections::VecDeque<FeatureEvent>,
    /// The castle's surfaces and secrets as exploring looks them
    /// up, and each online character's reveals, secrets found, pacing and
    /// Wishing Room door, by character id (`explore`).
    pub(super) explore_index: Arc<super::explore::ExploreIndex>,
    pub(super) explore: HashMap<i64, super::explore::Explorer>,
    /// Class rooms and arenas (`layouts`).
    pub(super) instance_layouts: InstanceLayouts,
    /// "Now" in Unix ms for schedules and beats (shared with the gateway).
    pub(super) clock: Arc<ServerClock>,
    /// NPC entities in `ents` (not players).
    pub(super) npc_count: usize,
    /// Characters whose level changed this tick (`flush_levels`).
    levels_changed: Vec<EntityId>,
    /// Their database work, waiting for the executor.
    pub(super) ext_jobs: Vec<ExtJob>,
    /// Database work produced while handling commands.
    pub(super) jobs: Vec<Job>,
    executor: Option<Executor>,
    // Scratch buffers kept between ticks.
    recipients: Vec<u32>,
    kicks: Vec<EntityId>,
    /// This tick's snapshot per client (indexed like the clients), before sending.
    snapshot_frames: Vec<Built>,
    /// Chat lines routed this tick (recipient, frame), delivered with the snapshots.
    chat_out: Vec<(u32, Bytes)>,
}

impl WorldSim {
    /// `threads` sizes the snapshot pool (the machine's cores in production).
    /// Dorms use the built-in shells until [`WorldSim::set_dorm_shells`].
    pub fn new(
        world: Arc<sim_core::World>,
        filter: WordFilter,
        saver: Option<mpsc::UnboundedSender<SaveBatch>>,
        metrics: Arc<RealtimeMetrics>,
        threads: usize,
    ) -> Self {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(threads.max(1))
            .thread_name(|i| format!("snapshot-{i}"))
            .build()
            .expect("snapshot thread pool starts");
        // `castle` is the castle at rest (every moving flight in
        // position A: its interactables, areas and place names are the same
        // in every state); moves are checked against the staircase state's
        // world (`house_worlds`).
        let castle_all = world;
        let stairs = (!castle_all.layout().flights().is_empty()).then(|| {
            Arc::new(
                sim_core::Stairs::with_layout(
                    crate::catalog::shipped().stairs.clone(),
                    castle_all.layout(),
                )
                .expect("data/world/stairs.json fits the castle (load_world checks)"),
            )
        });
        let world = if stairs.is_some() {
            Arc::new(
                sim_core::World::new(castle_all.layout().for_stairs(0))
                    .expect("a valid layout stays valid in every staircase state"),
            )
        } else {
            castle_all.clone()
        };
        let house_worlds = sim_core::HouseWorlds::new(castle_all.layout())
            .expect("a valid layout stays valid for every house and staircase state");
        let explore_index = Arc::new(super::explore::ExploreIndex::new(&castle_all));
        let clock = Arc::new(ServerClock::new());
        let stair_state = stairs.as_ref().map_or(0, |s| s.state(clock.now_unix_ms()));
        Self {
            grid: Grid::for_world(&castle_all),
            castle_all,
            stairs,
            stair_state,
            castle_air: Arc::new(sim_core::Airspace::new(&world)),
            castle: world,
            house_worlds,
            shells: DormShells::embedded(),
            params: MoveParams::default(),
            ents: Entities::default(),
            tick: 0,
            by_character: HashMap::new(),
            by_name: HashMap::new(),
            filter,
            saver,
            metrics,
            pool,
            next_spawn: 0,
            pending_chat: Vec::new(),
            instances: Instances::default(),
            rooms: HashMap::new(),
            invites: Invites::default(),
            parties: Default::default(),
            clubs: Default::default(),
            guest_memo: HashMap::new(),
            kick_bars: HashMap::new(),
            social_changed: HashMap::new(),
            pending_looks: HashMap::new(),
            looks_changed: Vec::new(),
            effects_changed: Vec::new(),
            spell_fx: Vec::new(),
            forced: Vec::new(),
            station_grants: HashMap::new(),
            trades: Default::default(),
            potions: Default::default(),
            finds: Default::default(),
            classes: Default::default(),
            spells: Default::default(),
            duels: Default::default(),
            residents: Default::default(),
            feature_events: Default::default(),
            explore_index,
            explore: HashMap::new(),
            instance_layouts: InstanceLayouts::embedded(),
            clock,
            npc_count: 0,
            levels_changed: Vec::new(),
            ext_jobs: Vec::new(),
            jobs: Vec::new(),
            executor: None,
            recipients: Vec::new(),
            kicks: Vec::new(),
            snapshot_frames: Vec::new(),
            chat_out: Vec::new(),
        }
    }

    pub fn set_dorm_shells(&mut self, shells: DormShells) {
        self.shells = shells;
    }

    /// The class rooms and arenas (built-in ones until this is called).
    pub fn set_instance_layouts(&mut self, layouts: InstanceLayouts) {
        self.instance_layouts = layouts;
    }

    /// The clock schedules and beats are read from.
    pub fn clock(&self) -> &Arc<ServerClock> {
        &self.clock
    }

    /// Replaces the clock (tests pin it with `ServerClock::starting_at`).
    /// The castle takes the new clock's staircase state at once,
    /// riders carried.
    pub fn set_clock(&mut self, clock: Arc<ServerClock>) {
        self.clock = clock;
        self.step_stairs(Instant::now());
    }

    /// The moving staircases switch every `period_s` seconds
    /// instead of `stairs.json`'s `period_s` (development only:
    /// `SHS_STAIRS_PERIOD_S` with `SHS_DEV=1`, the load test's short period;
    /// the phases scale with it). The castle takes the new schedule's state
    /// at once (call it before anyone joins).
    pub fn set_stairs_period(&mut self, period_s: u32) -> Result<(), sim_core::StairsError> {
        if let Some(stairs) = &self.stairs {
            let s = Arc::new(stairs.with_period(period_s)?);
            self.stair_state = s.state(self.clock.now_unix_ms());
            self.stairs = Some(s);
        }
        Ok(())
    }

    /// Players in the world (NPCs not counted).
    pub fn players(&self) -> usize {
        self.ents.len() - self.npc_count
    }

    /// Database jobs run here from now on (else they wait in the outbox).
    pub fn set_executor(&mut self, executor: Executor) {
        self.executor = Some(executor);
    }

    pub fn len(&self) -> usize {
        self.ents.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ents.is_empty()
    }

    pub fn current_tick(&self) -> u32 {
        self.tick
    }

    /// Current position of a character, if it is in the world.
    pub fn position(&self, id: EntityId) -> Option<[f32; 3]> {
        self.ents.index_of(id).map(|i| self.ents.pos[i])
    }

    /// Applies one command. Returns false once the world has shut down.
    pub fn handle(&mut self, cmd: WorldCmd) -> bool {
        match cmd {
            WorldCmd::Join {
                joining,
                out,
                control,
                reply,
            } => {
                let id = self.join(*joining, out, control, Instant::now());
                let _ = reply.send(id);
            }
            WorldCmd::Leave { id } => {
                self.remove_player(id);
            }
            WorldCmd::Input { id, at, input } => self.input(id, at, input),
            WorldCmd::AppearanceChanged {
                character_id,
                appearance,
                version,
            } => self.appearance_changed(character_id, appearance, version),
            WorldCmd::Notify {
                character_id,
                frame,
            } => {
                if let Some(i) = self.index_of_character(character_id) {
                    self.notice(i, frame);
                }
            }
            WorldCmd::OnlineStatus { ids, reply } => {
                let _ = reply.send(self.presence_of(&ids));
            }
            WorldCmd::InPlay {
                character_id,
                reply,
            } => {
                let _ = reply.send(self.in_play(character_id));
            }
            WorldCmd::FriendsChanged { character_id } => {
                self.friends_changed(character_id, Instant::now())
            }
            WorldCmd::ClubChanged { club_id, notices } => self.club_changed(club_id, notices),
            WorldCmd::SocialLoaded {
                character_id,
                seq,
                social,
            } => self.social_loaded(character_id, seq, social),
            WorldCmd::RoomDone { owner, outcome } => self.room_done(owner, outcome),
            WorldCmd::StationAccess {
                character_id,
                kind,
                reply,
            } => {
                let _ = reply.send(self.station_access(character_id, kind, Instant::now()));
            }
            WorldCmd::DevTeleport {
                character_id,
                pos,
                reply,
            } => {
                let _ = reply.send(self.dev_teleport(character_id, pos, Instant::now()));
            }
            WorldCmd::ExtDone(done) => self.ext_done(done, Instant::now()),
            WorldCmd::ProgressChanged {
                character_id,
                xp,
                gained,
            } => {
                if let Some(i) = self.index_of_character(character_id) {
                    self.set_progress(i, xp, gained);
                }
            }
            WorldCmd::FlyingChanged { character_id, xp } => {
                if let Some(i) = self.index_of_character(character_id) {
                    let m = &mut self.ents.mover[i];
                    m.flying_xp = m.flying_xp.max(xp);
                }
            }
            WorldCmd::SpellbookChanged {
                character_id,
                learned,
                loadout,
                new,
            } => {
                if let Some(i) = self.index_of_character(character_id) {
                    self.set_spellbook(i, learned, loadout, new);
                }
            }
            WorldCmd::Explored {
                character_id,
                reply,
            } => {
                let _ = reply.send(self.explored_of(character_id));
            }
            WorldCmd::Shutdown { done } => {
                self.shutdown();
                let _ = done.send(());
                return false;
            }
        }
        self.settle(Instant::now());
        self.flush_jobs();
        true
    }

    /// Delivers the lifecycle callbacks waiting since the last settle (zone
    /// changes to every handler, closings of emptied feature instances to
    /// their owner), until none are left. Runs at the end of every command,
    /// input and tick: no handler is ever running then, so callbacks never
    /// nest, and no dense index is held across the NPC removals.
    pub(super) fn settle(&mut self, now: Instant) {
        while let Some(ev) = self.feature_events.pop_front() {
            match ev {
                FeatureEvent::ZoneChanged { id, from } => self.features_zone_changed(id, from, now),
                FeatureEvent::Closing(zone) => {
                    if let Some(kind) = self.close_feature_if_empty(zone) {
                        match kind {
                            FeatureKind::Class { .. } => self
                                .with_classes(now, |c, ctx| c.on_instance_closed(ctx, zone, kind)),
                            FeatureKind::Arena { .. } => {
                                self.with_duels(now, |d, ctx| d.on_instance_closed(ctx, zone, kind))
                            }
                        }
                    }
                }
            }
        }
        // Whatever changed in a party since the last settle goes out once.
        self.flush_parties();
    }

    /// Hands queued database jobs to the executor (if there is one).
    fn flush_jobs(&mut self) {
        if let Some(ex) = &self.executor {
            for job in self.jobs.drain(..) {
                ex.run(job);
            }
            for job in self.ext_jobs.drain(..) {
                ex.run_ext(job);
            }
        }
    }

    /// Takes the jobs queued so far (tests without a database run them).
    pub fn take_jobs(&mut self) -> Vec<Job> {
        std::mem::take(&mut self.jobs)
    }

    /// Takes the feature jobs queued so far (tests without a database).
    pub fn take_ext_jobs(&mut self) -> Vec<ExtJob> {
        std::mem::take(&mut self.ext_jobs)
    }

    // ---- Features (`ext`). Each handler runs with its state
    // taken out of the world, so it can hold a `WorldCtx` (all of the world
    // but that state) at the same time.

    pub(super) fn with_trades(
        &mut self,
        now: Instant,
        f: impl FnOnce(&mut crate::trade::world::Trades, &mut WorldCtx<'_>),
    ) {
        let mut t = std::mem::take(&mut self.trades);
        f(&mut t, &mut WorldCtx { sim: self, now });
        self.trades = t;
    }

    pub(super) fn with_potions(
        &mut self,
        now: Instant,
        f: impl FnOnce(&mut crate::potions::world::Potions, &mut WorldCtx<'_>),
    ) {
        let mut p = std::mem::take(&mut self.potions);
        f(&mut p, &mut WorldCtx { sim: self, now });
        self.potions = p;
    }

    pub(super) fn with_finds(
        &mut self,
        now: Instant,
        f: impl FnOnce(&mut crate::finds::world::Finds, &mut WorldCtx<'_>),
    ) {
        let mut x = std::mem::take(&mut self.finds);
        f(&mut x, &mut WorldCtx { sim: self, now });
        self.finds = x;
    }

    pub(super) fn with_classes(
        &mut self,
        now: Instant,
        f: impl FnOnce(&mut crate::classes::world::Classes, &mut WorldCtx<'_>),
    ) {
        let mut x = std::mem::take(&mut self.classes);
        f(&mut x, &mut WorldCtx { sim: self, now });
        self.classes = x;
    }

    pub(super) fn with_spells(
        &mut self,
        now: Instant,
        f: impl FnOnce(&mut crate::spells::world::Spells, &mut WorldCtx<'_>),
    ) {
        let mut x = std::mem::take(&mut self.spells);
        f(&mut x, &mut WorldCtx { sim: self, now });
        self.spells = x;
    }

    pub(super) fn with_residents(
        &mut self,
        now: Instant,
        f: impl FnOnce(&mut crate::residents::world::Residents, &mut WorldCtx<'_>),
    ) {
        let mut x = std::mem::take(&mut self.residents);
        f(&mut x, &mut WorldCtx { sim: self, now });
        self.residents = x;
    }

    pub(super) fn with_duels(
        &mut self,
        now: Instant,
        f: impl FnOnce(&mut crate::dueling::world::Duels, &mut WorldCtx<'_>),
    ) {
        let mut x = std::mem::take(&mut self.duels);
        f(&mut x, &mut WorldCtx { sim: self, now });
        self.duels = x;
    }

    /// A feature's database job came back: hand it to its feature.
    pub fn ext_done(&mut self, done: ExtDone, now: Instant) {
        match done {
            ExtDone::Trade(d) => self.with_trades(now, |t, ctx| t.on_done(ctx, d)),
            ExtDone::Potion(d) => self.with_potions(now, |p, ctx| p.on_done(ctx, d)),
            ExtDone::Find(d) => self.with_finds(now, |f, ctx| f.on_done(ctx, d)),
            ExtDone::Class(d) => self.with_classes(now, |c, ctx| c.on_done(ctx, d)),
            ExtDone::Spell(d) => self.with_spells(now, |s, ctx| s.on_done(ctx, d)),
            ExtDone::Duel(d) => self.with_duels(now, |x, ctx| x.on_done(ctx, d)),
            ExtDone::Run(f) => f(&mut WorldCtx { sim: self, now }),
        }
        self.settle(now);
    }

    /// Character `id` is about to leave the world (or be replaced by a new
    /// connection): its trade, requests, challenges and duel end.
    pub(super) fn features_left(&mut self, id: EntityId, now: Instant) {
        self.with_trades(now, |t, ctx| t.on_left(ctx, id));
        self.with_potions(now, |p, ctx| p.on_left(ctx, id));
        self.with_classes(now, |c, ctx| c.on_left(ctx, id));
        self.with_spells(now, |s, ctx| s.on_left(ctx, id));
        self.with_duels(now, |d, ctx| d.on_left(ctx, id));
        self.with_residents(now, |r, ctx| r.on_left(ctx, id));
        if let Some(i) = self.ents.index_of(id) {
            let cid = self.ents.info[i].character_id;
            self.station_grants.retain(|(c, _), _| *c != cid);
        }
    }

    /// Character `id` changed zone (out of `from`): a trade window cannot
    /// follow it; classes and duels learn who came and went.
    pub(super) fn features_zone_changed(
        &mut self,
        id: EntityId,
        from: super::entities::ZoneId,
        now: Instant,
    ) {
        // Spells L20: binds, slows and a forced move stay behind (a
        // recolour and a guardian are their spells' business).
        if let Some(i) = self.ents.index_of(id) {
            self.ents.mover[i].forced = None;
            self.remove_effect(i, EffectKind::Petrified, now);
            self.remove_effect(i, EffectKind::Slowed, now);
        }
        self.with_trades(now, |t, ctx| t.on_zone_changed(ctx, id));
        self.with_potions(now, |p, ctx| p.on_zone_changed(ctx, id));
        self.with_classes(now, |c, ctx| c.on_zone_changed(ctx, id, from));
        self.with_spells(now, |s, ctx| s.on_zone_changed(ctx, id, from));
        self.with_duels(now, |d, ctx| d.on_zone_changed(ctx, id, from));
    }

    // ---- Potion effects (core state, see `ext`).

    /// Starts `effect` on character `i`, replacing one of the same kind and
    /// the kind it excludes.
    pub(super) fn apply_effect(&mut self, i: usize, effect: ActiveEffect, now: Instant) {
        if effect.kind.is_spell() && !self.spell_fx.contains(&self.ents.ids[i]) {
            self.spell_fx.push(self.ents.ids[i]);
        }
        let list = &mut self.ents.effects[i].active;
        let excluded = effect.kind.excludes();
        list.retain(|e| e.kind != effect.kind && Some(e.kind) != excluded);
        list.push(effect);
        list.sort_by_key(|e| e.kind);
        self.effects_updated(i, now);
    }

    /// Ends effect `kind` on character `i` if it is active.
    pub(super) fn remove_effect(&mut self, i: usize, kind: EffectKind, now: Instant) {
        let list = &mut self.ents.effects[i].active;
        let before = list.len();
        list.retain(|e| e.kind != kind);
        if list.len() != before {
            self.effects_updated(i, now);
        }
    }

    /// Recomputes character `i`'s visible effects and speed after its list
    /// (or Lucerna, or its zone's rules) changed: viewers are told next tick,
    /// the character at once. Zones that suspend boosts (arenas) hide the
    /// Diminishing Draught/Enlarging Draught scale and replace the speed with their own.
    pub(super) fn effects_updated(&mut self, i: usize, now: Instant) {
        let rules = self.instances.rules(self.ents.zone[i]);
        let state = &mut self.ents.effects[i];
        state.suspended = !rules.boosts;
        let mut visible: Vec<VisibleEffect> = state
            .active
            .iter()
            .filter_map(|e| e.visible)
            .filter(|v| rules.boosts || !matches!(v, VisibleEffect::Scale { .. }))
            .collect();
        if state.lucerna {
            visible.push(VisibleEffect::Lucerna);
        }
        let base = match rules.move_speed_pct {
            Some(pct) => pct,
            None if rules.boosts => effective_speed(&state.active),
            None => SPEED_PCT_NORMAL,
        };
        // Spells L20: binds and slows multiply it, in every zone.
        let hinder = hindrance_pct(&state.active);
        let speed = (u32::from(base) * u32::from(hinder) / 100) as u16;
        let info = &mut self.ents.info[i];
        if info.effects != visible {
            info.effects = visible;
            info.effects_version = info.effects_version.wrapping_add(1);
            self.effects_changed.push(self.ents.ids[i]);
        }
        let m = &mut self.ents.mover[i];
        if hinder < m.hinder_pct {
            // A bind or slow just started or got stronger: it holds now, no
            // grace (moves in flight at the old speed are rejected and
            // corrected). Any other change while hindered keeps the rules
            // below (a lost boost still gets its grace).
            m.speed_grace = None;
        } else if speed < m.speed_pct {
            // Moves already in flight at the old speed still pass.
            m.speed_grace = Some((m.speed_pct, now + SPEED_DROP_GRACE));
        }
        m.speed_pct = speed;
        m.hinder_pct = hinder;
        // Featherlight speeds the broom where boosts count.
        let broom = if rules.boosts {
            super::flying::effective_broom_pct(&self.ents.effects[i].active)
        } else {
            protocol::BROOM_PCT_NORMAL
        };
        self.set_broom_pct(i, broom, now);
        self.send_own_effects(i, now);
    }

    // ---- Lucerna, animations, levels and spellbooks.

    /// Lights or puts out character `i`'s wand.
    pub(super) fn set_lucerna(&mut self, i: usize, on: bool, now: Instant) {
        if self.ents.effects[i].lucerna != on {
            self.ents.effects[i].lucerna = on;
            self.effects_updated(i, now);
        }
    }

    /// Shows `anim` (a one-shot like `Cast`, a wave) on entity `i` for `ms`;
    /// moving ends it, as for emotes.
    pub(super) fn play_anim(&mut self, i: usize, anim: Anim, ms: u32, now: Instant) {
        // A rider keeps showing its broom.
        if self.ents.mover[i].mount.is_some() {
            return;
        }
        self.ents.state[i].anim = anim;
        self.ents.mover[i].emote_until = Some(now + Duration::from_millis(u64::from(ms)));
    }

    /// Stores character `i`'s new XP total and sends `Progress`; a new level
    /// goes to viewers next tick (`LevelChanged`, bumping `effects_version`).
    pub(super) fn set_progress(&mut self, i: usize, xp: u32, gained: Option<protocol::XpGain>) {
        let content = crate::catalog::shipped();
        let school = &mut self.ents.school[i];
        let slots_before = school.slots();
        school.xp = school.xp.max(xp);
        let progress = content.progression.progress(school.xp);
        let slots = school.slots();
        let info = &mut self.ents.info[i];
        let player = info.npc.is_none();
        if player && info.level != progress.level {
            info.level = progress.level;
            info.effects_version = info.effects_version.wrapping_add(1);
            self.levels_changed.push(self.ents.ids[i]);
            self.party_level(i);
            self.club_level(i);
        }
        self.send_msg(i, &ServerMsg::Progress { progress, gained });
        // A level that opens a loadout slot changes the spellbook whatever
        // granted the XP (a class sends its own; a craft, find or duel
        // doesn't): a default loadout grows into the new slot, a chosen one
        // stays as it is.
        if player && slots != slots_before {
            let school = &mut self.ents.school[i];
            if school.loadout
                == content
                    .spells
                    .default_loadout(&school.learned, slots_before)
            {
                school.loadout = content.spells.default_loadout(&school.learned, slots);
            }
            self.send_spellbook(i, Vec::new());
        }
    }

    /// Stores character `i`'s spells and loadout and sends `Spellbook`.
    /// The hotbar spells among `new` (just learned) go into the hotbar's
    /// first empty slots, saved like a `SetHotbar` (the database did the
    /// same in `learn_spells`; this keeps the world's copy, which may hold
    /// a newer layout, the one that lasts).
    pub(super) fn set_spellbook(
        &mut self,
        i: usize,
        learned: Vec<protocol::SpellId>,
        loadout: Vec<protocol::SpellId>,
        new: Vec<protocol::SpellId>,
    ) {
        let school = &mut self.ents.school[i];
        school.learned = learned;
        school.loadout = loadout;
        if crate::catalog::shipped()
            .spells
            .hotbar_add(&mut school.hotbar, &new)
        {
            self.save_hotbar(i);
        }
        self.send_spellbook(i, new);
    }

    /// `Spellbook` for character `i`.
    fn send_spellbook(&mut self, i: usize, new: Vec<protocol::SpellId>) {
        let school = &self.ents.school[i];
        let msg = ServerMsg::Spellbook {
            learned: school.learned.clone(),
            loadout: school.loadout.clone(),
            slots: school.slots(),
            new,
            hotbar: crate::spells::hotbar::to_wire(&school.hotbar),
        };
        self.send_msg(i, &msg);
    }

    /// `SetHotbar`: learned hotbar spells only, none twice
    /// (`Spells::check_hotbar`). Answered by `Hotbar` with the layout kept
    /// (the old one when refused); a change is saved.
    fn on_set_hotbar(&mut self, i: usize, slots: &[Option<protocol::SpellId>]) {
        let school = &mut self.ents.school[i];
        let checked = crate::catalog::shipped()
            .spells
            .check_hotbar(&school.learned, slots);
        let changed = match checked {
            Ok(bar) if bar != school.hotbar => {
                school.hotbar = bar;
                true
            }
            Ok(_) => false,
            Err(e) => {
                tracing::debug!(error = %e, "hotbar refused");
                false
            }
        };
        if changed {
            self.save_hotbar(i);
        }
        let msg = ServerMsg::Hotbar {
            slots: crate::spells::hotbar::to_wire(&self.ents.school[i].hotbar),
        };
        self.send_msg(i, &msg);
    }

    /// Hands character `i`'s hotbar to the saver.
    fn save_hotbar(&self, i: usize) {
        let batch = SaveBatch::hotbar(self.ents.info[i].character_id, self.ents.school[i].hotbar);
        self.save(batch);
    }

    /// Sends `msg` to every player in `i`'s zone within `radius` of it.
    pub(super) fn send_near(&mut self, i: usize, radius: f32, msg: &ServerMsg) {
        let frame: Bytes = encode_server(msg).into();
        let p = self.ents.pos[i];
        let r2 = radius * radius;
        let mut near = std::mem::take(&mut self.recipients);
        near.clear();
        match self.ents.zone[i] {
            CASTLE => {
                let pos = &self.ents.pos;
                let zone = &self.ents.zone;
                // The grid is as of the last tick (someone may have left
                // since): check the index, the zone and the distance again.
                self.grid.for_each_near(p[0], p[2], radius, |j| {
                    let k = j as usize;
                    if k < pos.len() && zone[k] == CASTLE && dist2(pos[k], p) <= r2 {
                        near.push(j);
                    }
                });
                if !near.contains(&(i as u32)) {
                    near.push(i as u32);
                }
            }
            z => {
                for &m in self.instances.members(z) {
                    if let Some(j) = self.ents.index_of(m)
                        && dist2(self.ents.pos[j], p) <= r2
                    {
                        near.push(j as u32);
                    }
                }
            }
        }
        for &j in &near {
            if self.ents.info[j as usize].npc.is_none() {
                self.send(j as usize, frame.clone());
            }
        }
        self.recipients = near;
    }

    /// `OwnEffects` for character `i`.
    pub(super) fn send_own_effects(&mut self, i: usize, now: Instant) {
        let effects = self.ents.effects[i]
            .active
            .iter()
            .map(|e| OwnEffect {
                kind: e.kind,
                remaining_secs: remaining_secs(e.until, now),
                fortune: e.fortune,
            })
            .collect();
        // Featherlight Elixir speeds the broom; flight moves are
        // checked with exactly this.
        let msg = ServerMsg::OwnEffects {
            effects,
            speed_pct: self.ents.mover[i].speed_pct,
            broom_pct: self.ents.mover[i].broom_pct,
        };
        self.send_msg(i, &msg);
    }

    /// Once a second: ends effects whose time is up.
    fn expire_effects(&mut self, now: Instant) {
        for i in 0..self.ents.len() {
            let list = &mut self.ents.effects[i].active;
            if list.iter().any(|e| e.until <= now) {
                list.retain(|e| e.until > now);
                self.effects_updated(i, now);
            }
        }
    }

    /// Spells L20: every tick, ends the spell effects (binds, slows,
    /// recolours, guardians) whose time is up, on the few characters that
    /// hold one.
    pub(super) fn expire_spell_effects(&mut self, now: Instant) {
        let mut k = 0;
        while k < self.spell_fx.len() {
            let Some(i) = self.ents.index_of(self.spell_fx[k]) else {
                self.spell_fx.swap_remove(k);
                continue;
            };
            let list = &mut self.ents.effects[i].active;
            if list.iter().any(|e| e.kind.is_spell() && e.until <= now) {
                list.retain(|e| !(e.kind.is_spell() && e.until <= now));
                self.effects_updated(i, now);
            }
            if self.ents.effects[i]
                .active
                .iter()
                .any(|e| e.kind.is_spell())
            {
                k += 1;
            } else {
                self.spell_fx.swap_remove(k);
            }
        }
    }

    /// Spells L20: starts sliding player `i` to `to` (snapped to its zone's
    /// ground) over `move_ms`, held still until `hold_ms` (both from `now`),
    /// and sends it `ForcedMove`. False for NPCs, riders, someone already
    /// being moved, or `to` off the ground.
    pub(super) fn force_move(
        &mut self,
        i: usize,
        to: [f32; 3],
        move_ms: u16,
        hold_ms: u16,
        by: EntityId,
        now: Instant,
    ) -> bool {
        let m = &self.ents.mover[i];
        if self.ents.info[i].npc.is_some() || m.mount.is_some() || m.forced.is_some() {
            return false;
        }
        if !to.iter().all(|v| v.is_finite()) {
            return false;
        }
        let Some(g) = self.world_of(i).ground_at(to[0], to[2], to[1]) else {
            return false;
        };
        let to = [to[0], g.y, to[2]];
        let hold_ms = hold_ms.max(move_ms);
        let ms = |v: u16| Duration::from_millis(u64::from(v));
        let forced = super::entities::Forced {
            from: self.ents.pos[i],
            to,
            start: now,
            end: now + ms(move_ms),
            hold_until: now + ms(hold_ms),
            by,
        };
        let m = &mut self.ents.mover[i];
        m.forced = Some(forced);
        m.emote_until = None;
        let id = self.ents.ids[i];
        if !self.forced.contains(&id) {
            self.forced.push(id);
        }
        self.send_msg(
            i,
            &ServerMsg::ForcedMove {
                to: QPos::from_m(to),
                move_ms,
                hold_ms,
                by,
            },
        );
        true
    }

    /// Spells L20: every tick (before duels step), moves the characters the
    /// server is sliding along their forced moves; each ends
    /// [`FORCED_TAIL`] after its hold. A zone change or a correction since
    /// it started ends it at once.
    pub(super) fn step_forced(&mut self, now: Instant) {
        let mut k = 0;
        while k < self.forced.len() {
            let Some(i) = self.ents.index_of(self.forced[k]) else {
                self.forced.swap_remove(k);
                continue;
            };
            let m = &self.ents.mover[i];
            let Some(f) = m.forced else {
                self.forced.swap_remove(k);
                continue;
            };
            let moved_away = m.zone_entered_at.is_some_and(|t| t > f.start);
            // A rider is never slid along the ground (mounting refuses a
            // forced move; this guards any other way onto a broom).
            if moved_away || m.mount.is_some() || now >= f.hold_until + FORCED_TAIL {
                self.ents.mover[i].forced = None;
                self.forced.swap_remove(k);
                continue;
            }
            k += 1;
            let span = f.end.saturating_duration_since(f.start).as_secs_f32();
            let t = if span > 0.0 {
                (now.saturating_duration_since(f.start).as_secs_f32() / span).clamp(0.0, 1.0)
            } else {
                1.0
            };
            let mut p = [0.0; 3];
            for (c, v) in p.iter_mut().enumerate() {
                *v = f.from[c] + (f.to[c] - f.from[c]) * t;
            }
            let prev = self.ents.pos[i];
            let world = self.world_of(i).clone();
            if let Some(g) = world.ground_at(p[0], p[2], prev[1]) {
                p[1] = g.y;
                self.ents.level[i] = g.level;
            }
            if p == prev {
                continue;
            }
            self.ents.pos[i] = p;
            let state = &mut self.ents.state[i];
            state.pos = QPos::from_m(p);
            state.anim = Anim::Idle;
            let m = &mut self.ents.mover[i];
            m.unsaved = true;
            m.moved_at = Some(now);
            if self.ents.zone[i] == CASTLE {
                self.explore_moved(i, now);
            }
        }
    }

    /// The speed (percent) character `i`'s moves are checked with now.
    pub(super) fn move_speed_pct(&mut self, i: usize, at: Instant) -> u16 {
        let m = &mut self.ents.mover[i];
        match m.speed_grace {
            Some((pct, until)) if at < until => pct.max(m.speed_pct),
            Some(_) => {
                m.speed_grace = None;
                m.speed_pct
            }
            None => m.speed_pct,
        }
    }

    // ---- Stations (`stations`).

    /// Records a grant for character `i` and tells the client to open it.
    pub(super) fn open_station(
        &mut self,
        i: usize,
        kind: StationKind,
        target: StationTarget,
        now: Instant,
    ) {
        let grant = StationGrant {
            kind,
            zone: self.ents.zone[i],
            target,
            until: now + stations::grant_ttl(),
        };
        let msg = ServerMsg::StationOpened {
            kind,
            target: grant.target.wire(),
            expires_in_secs: protocol::STATION_GRANT_SECS,
        };
        self.station_grants
            .insert((self.ents.info[i].character_id, kind), grant);
        self.send_msg(i, &msg);
    }

    /// Whether character `character_id` may use a `kind` station now: a
    /// grant that has not run out, in the zone it is in, still in reach.
    pub fn station_access(
        &self,
        character_id: i64,
        kind: StationKind,
        now: Instant,
    ) -> Option<StationAccess> {
        let grant = self.station_grants.get(&(character_id, kind))?;
        let i = self.index_of_character(character_id)?;
        if grant.until <= now || self.ents.zone[i] != grant.zone {
            return None;
        }
        let pos = self.ents.pos[i];
        let mut dorm_owner = None;
        let reach = match &grant.target {
            StationTarget::Interactable { id } => {
                let it = match self.instances.world(grant.zone) {
                    Some(w) => w.interactable(id),
                    None => self.castle.interactable(id),
                }?;
                // A house's station (a common room's crafting corners, its
                // Owl Order Catalogue) serves that house only.
                let own = it
                    .house
                    .as_deref()
                    .is_none_or(|h| h == self.ents.house[i].id());
                own && within(pos, it.pos, it.radius + stations::REACH_SLACK_M)
            }
            StationTarget::Furniture { item_id } => {
                let d = self.instances.get(grant.zone)?;
                dorm_owner = Some(d.owner);
                let item = d.items.as_ref()?.iter().find(|p| p.item_id == *item_id)?;
                within(
                    pos,
                    item.pos.to_m(),
                    stations::FURNITURE_REACH_M + stations::REACH_SLACK_M,
                )
            }
        };
        reach.then(|| StationAccess {
            kind,
            target: grant.target.wire(),
            dorm_owner,
        })
    }

    /// Adds a character where it resumes and sends `Welcome` (and `DormState`
    /// when it starts in a dorm that is loaded).
    pub fn join(
        &mut self,
        joining: Joining,
        out: mpsc::Sender<Bytes>,
        control: Arc<ConnControl>,
        now: Instant,
    ) -> Option<EntityId> {
        let Joining {
            character,
            social,
            dorm_open,
            crowns,
            unread_mail,
            effects,
            mut school,
            mut flying_xp,
            explored,
            club,
        } = joining;
        let mut effects: Vec<ActiveEffect> =
            effects.into_iter().filter(|e| e.until > now).collect();
        // The same character signing in again (another tab or device) takes
        // over where the old connection stands, in the same zone; the old one
        // is disconnected without its friends seeing it go.
        let replaced = self.by_character.get(&character.id).copied();
        // The newest look known: the one the gateway read, unless a wardrobe
        // change committed after that reached the world first (noted while
        // offline, or applied to the connection being replaced).
        let mut look = (character.appearance, character.appearance_version);
        if let Some((a, v, at)) = self.pending_looks.remove(&character.id)
            && v > look.1
            && now.saturating_duration_since(at) < SOCIAL_CHANGE_MEMORY
        {
            look = (a, v);
        }
        let (start, dorm_open) = match replaced {
            Some(old) => {
                let i = self.ents.index_of(old).expect("indexed character is live");
                let old_info = &self.ents.info[i];
                if old_info.appearance_version > look.1 {
                    look = (old_info.appearance, old_info.appearance_version);
                }
                // Newer than the database if a grant is still in flight.
                let old_school = &self.ents.school[i];
                if old_school.xp > school.xp {
                    school = old_school.clone();
                }
                flying_xp = flying_xp.max(self.ents.mover[i].flying_xp);
                let start = match self.ents.presence[i].return_to {
                    // A class or arena does not carry over to a new
                    // connection (its handler sees the old one leave):
                    // start where they came from.
                    Some(super::entities::ReturnPoint::Castle { pos, yaw })
                        if self.instances.feature(self.ents.zone[i]).is_some() =>
                    {
                        let castle = self.castle_world(character.house).clone();
                        let pos = self.to_stair_state(pos);
                        let (pos, level, _) = self.spawn_in(&castle, Some(pos));
                        super::instances::Start {
                            zone: CASTLE,
                            pos,
                            level,
                            yaw,
                            return_to: None,
                        }
                    }
                    // A rider starts again on the ground below.
                    return_to => super::instances::Start {
                        zone: self.ents.zone[i],
                        pos: self.standing_pos(i),
                        level: self.ents.level[i],
                        yaw: self.ents.state[i].yaw,
                        return_to,
                    },
                };
                // Newer than the database if a save is still in flight.
                let open = self.ents.presence[i].dorm_open;
                effects = self.ents.effects[i].active.clone();
                self.ents.client[i].control.kick(KickCode::Replaced);
                self.leave(old, super::instances::Leaving::Replaced, now);
                (start, open)
            }
            None => (self.start_point(&character, &social, now), dorm_open),
        };
        let key = name_key(&character.name);
        let info = CharacterInfo {
            name: character.name.clone(),
            house: character.house,
        };
        let (appearance, appearance_version) = look;
        effects.sort_by_key(|e| e.kind);
        effects.dedup_by_key(|e| e.kind);
        let visible: Vec<VisibleEffect> = effects.iter().filter_map(|e| e.visible).collect();
        let speed_pct = effective_speed(&effects);
        let broom_pct = super::flying::effective_broom_pct(&effects);
        let Some(id) = self.ents.insert(
            NewEntity {
                info: Info {
                    character_id: character.id,
                    name: character.name,
                    name_key: key.clone(),
                    appearance_version,
                    appearance,
                    effects_version: 0,
                    effects: visible.clone(),
                    level: school.level(),
                    npc: None,
                },
                house: character.house,
                zone: start.zone,
                pos: start.pos,
                level: start.level,
                yaw: start.yaw,
                client: Client::new(out, control, now),
                social,
                return_to: start.return_to,
                dorm_open,
                effects,
                school,
            },
            now,
        ) else {
            // Full: an instance created for this join must not linger (nor
            // a replaced connection's exploring state).
            self.instances.teardown_if_empty(start.zone);
            self.explore.remove(&character.id);
            return None;
        };
        self.by_character.insert(character.id, id);
        self.by_name.insert(key, id);
        // The map's reveals and the secrets found (a replaced
        // connection's state is kept and added to).
        self.explore_joined(character.id, &explored);
        let i = self.ents.index_of(id).expect("just inserted");
        let m = &mut self.ents.mover[i];
        m.speed_pct = speed_pct;
        m.broom_pct = broom_pct;
        m.flying_xp = flying_xp;
        self.instances.add_member(start.zone, id);
        let welcome = ServerMsg::Welcome {
            protocol_version: PROTOCOL_VERSION,
            your_id: id,
            character: info,
            appearance_version,
            appearance,
            effects_version: 0,
            effects: visible,
            zone: self.protocol_zone(start.zone),
            spawn: QPos::from_m(start.pos),
            yaw: start.yaw,
            tick_hz: WORLD_TICK_HZ,
            snapshot_hz: WORLD_SNAPSHOT_HZ,
            unix_ms: u64::try_from(self.clock.unix_ms_at(now)).unwrap_or(0),
        };
        self.send(i, encode_server(&welcome).into());
        // The HUD's wallet, new-mail owl and effect timers.
        self.send_msg(i, &ServerMsg::Wallet { crowns });
        self.send_msg(
            i,
            &ServerMsg::MailNotice {
                unread: unread_mail,
            },
        );
        self.send_own_effects(i, now);
        // Level, XP and spells.
        let progress = crate::catalog::shipped()
            .progression
            .progress(self.ents.school[i].xp);
        self.send_msg(
            i,
            &ServerMsg::Progress {
                progress,
                gained: None,
            },
        );
        self.send_spellbook(i, Vec::new());
        self.send_dorm_state(i);
        self.connect_friends(i, replaced.is_none());
        // Its party sees it online (with its new entity) and it gets the party.
        self.party_online(i, now);
        // Its club sees it online, and it gets the club (loaded if need be).
        self.club_online(i, club);
        if self
            .social_changed
            .remove(&character.id)
            .is_some_and(|at| now.saturating_duration_since(at) < SOCIAL_CHANGE_MEMORY)
        {
            self.request_social(i);
        }
        self.metrics.players_online.set(self.players() as f64);
        Some(id)
    }

    /// The saved position if it is still on walkable ground of `world`, else
    /// the next spawn of `world`.
    pub(super) fn spawn_in(
        &mut self,
        world: &sim_core::World,
        last: Option<[f32; 3]>,
    ) -> ([f32; 3], i32, u8) {
        if let Some(p) = last.filter(|p| p.iter().all(|v| v.is_finite())) {
            if let Some(g) = world.ground_at(p[0], p[2], p[1]) {
                return ([p[0], g.y, p[2]], g.level, 0);
            }
            // Solid furniture: a spot saved where a piece now stands (a table
            // added under it) gives way to the nearest spot beside the piece.
            if let Some(q) = sim_core::stand_near(world, &self.params, p, SAVED_SPOT_SLACK)
                && let Some(g) = world.ground_at(q[0], q[2], q[1])
            {
                return (q, g.level, 0);
            }
        }
        let spawns = world.spawns();
        if !spawns.is_empty() {
            let n = self.next_spawn;
            self.next_spawn = n.wrapping_add(1);
            let s = &spawns[n % spawns.len()];
            let (x, z) = (s.pos[0], s.pos[2]);
            let Some(g) = world.ground_at(x, z, s.pos[1]) else {
                return (s.pos, 0, yaw_to_u8(s.yaw));
            };
            // Each lap round the spawns uses the next slot of a spiral, so
            // players arriving together don't stand inside one another.
            let slot = (n / spawns.len()) % SPAWN_SLOTS;
            let p = spawn_slot(world, &self.params, [x, g.y, z], slot);
            let level = world
                .ground_at(p[0], p[2], p[1])
                .map_or(g.level, |g| g.level);
            return (p, level, yaw_to_u8(s.yaw));
        }
        // A layout without spawns: the middle of its first surface.
        let s = &world.surfaces()[0];
        let (x, z) = ((s.min[0] + s.max[0]) / 2.0, (s.min[1] + s.max[1]) / 2.0);
        ([x, s.height_at(x, z), z], s.level, 0)
    }

    /// Moves a character to `pos` on walkable ground of the zone they are in
    /// (development only): the client follows the `Correction`, like a
    /// refused move, and its later moves continue from there. False when
    /// they are offline or `pos` is not ground they may stand on.
    fn dev_teleport(&mut self, character_id: i64, pos: [f32; 3], at: Instant) -> bool {
        let Some(i) = self.index_of_character(character_id) else {
            return false;
        };
        let world = self.world_of(i).clone();
        let Some(g) = pos
            .iter()
            .all(|v| v.is_finite())
            .then(|| world.ground_at(pos[0], pos[2], pos[1]))
            .flatten()
        else {
            return false;
        };
        self.put_at(i, [pos[0], g.y, pos[2]], g.level, at);
        true
    }

    /// Puts character `i` at `p` (on ground of their zone, floor `level`) as
    /// a correction: a dev teleport, or (solid furniture) out of a piece
    /// placed where they stood.
    pub(super) fn put_at(&mut self, i: usize, p: [f32; 3], level: i32, at: Instant) {
        // A rider is put down on the ground.
        if self.ents.mover[i].mount.is_some() {
            self.unmount_for_zone_change(i, at);
            self.ents.state[i].anim = Anim::Idle;
        }
        self.ents.pos[i] = p;
        self.ents.level[i] = level;
        self.ents.state[i].pos = QPos::from_m(p);
        let m = &mut self.ents.mover[i];
        m.unsaved = true;
        m.last_correction = Some(at);
        m.emote_until = None;
        // A move already on its way from the old spot is dropped silently,
        // as after a zone change (the perf viewers' teleports
        // gave the load test its stray walking `too_fast`).
        m.zone_entered_at = Some(at);
        let seq = m.last_seq.unwrap_or(0);
        let msg = ServerMsg::Correction {
            seq,
            pos: QPos::from_m(p),
        };
        self.send(i, encode_server(&msg).into());
    }

    pub(super) fn index_of_character(&self, character_id: i64) -> Option<usize> {
        let id = self.by_character.get(&character_id)?;
        self.ents.index_of(*id)
    }

    /// The moving staircases' schedule, when the castle has
    /// moving flights.
    pub fn stairs(&self) -> Option<&Arc<sim_core::Stairs>> {
        self.stairs.as_ref()
    }

    /// The castle file as loaded (both positions of every moving
    /// flight).
    pub fn castle_all(&self) -> &Arc<sim_core::World> {
        &self.castle_all
    }

    /// NPCs: the residents of the open castle (tests, tools).
    pub fn residents(&self) -> &crate::residents::world::Residents {
        &self.residents
    }

    /// The staircase state the castle's positions are in.
    pub fn stair_state(&self) -> u8 {
        self.stair_state
    }

    /// The castle as `house` walks it in the current staircase state.
    pub(super) fn castle_world(&self, house: House) -> &Arc<sim_core::World> {
        self.house_worlds.get(house, self.stair_state)
    }

    /// The world a character walks: their house's castle in the current
    /// staircase state, or their room's.
    pub(super) fn world_of(&self, i: usize) -> &Arc<sim_core::World> {
        match self.ents.zone[i] {
            CASTLE => self.castle_world(self.ents.house[i]),
            z => self
                .instances
                .world(z)
                .unwrap_or_else(|| self.castle_world(self.ents.house[i])),
        }
    }

    /// Stores a character's new look and sends it to the character and to
    /// every client that has them in view; others get it when they next see
    /// them (`EntityInfo`). Stale versions are ignored.
    pub fn appearance_changed(&mut self, character_id: i64, appearance: Appearance, version: u32) {
        let Some(i) = self.index_of_character(character_id) else {
            // Perhaps joining with what the gateway read before this change.
            let newest = self
                .pending_looks
                .get(&character_id)
                .is_none_or(|&(_, v, _)| version > v);
            if newest {
                self.pending_looks
                    .insert(character_id, (appearance, version, Instant::now()));
            }
            return;
        };
        let info = &mut self.ents.info[i];
        if version <= info.appearance_version {
            return;
        }
        info.appearance = appearance;
        info.appearance_version = version;
        // Sent once per tick with the newest look (`flush_looks`), however
        // many changes arrive meanwhile.
        self.looks_changed.push(self.ents.ids[i]);
    }

    /// Sends a friend event or invitation to client `i`: at once while its
    /// outbound queue has room above the reserve, else in order from a
    /// backlog over the next ticks (`flush_notices`). Other players cause
    /// these (friend requests, invitations), so they must not be able to
    /// fill someone's queue and get them disconnected; past [`NOTICES_MAX`]
    /// waiting, the oldest is dropped (and counted).
    pub(super) fn notice(&mut self, i: usize, frame: Bytes) {
        let c = &mut self.ents.client[i];
        if c.notices.is_empty() && c.out.capacity() > queue_reserve(c) {
            if let Err(TrySendError::Full(_)) = c.out.try_send(frame) {
                c.too_slow = true;
            }
            return;
        }
        if c.notices.len() >= NOTICES_MAX {
            c.notices.pop_front();
            self.metrics.notices_dropped.inc();
        }
        c.notices.push_back(frame);
    }

    /// Sends each client's waiting notices that fit above the queue reserve.
    fn flush_notices(&mut self) {
        for c in &mut self.ents.client {
            while !c.notices.is_empty() && c.out.capacity() > queue_reserve(c) {
                let frame = c.notices.pop_front().expect("not empty");
                if let Err(TrySendError::Full(_)) = c.out.try_send(frame) {
                    c.too_slow = true;
                }
            }
        }
    }

    /// Hands this tick's look changes to the character and everyone who has
    /// it in view, then sends each client what fits in its outbound queue
    /// above the chat reserve; the rest waits for the next tick. A burst of
    /// wardrobe changes around a player therefore never fills their queue
    /// (which would disconnect them as too slow), and no change is lost:
    /// what is sent is always the newest look.
    fn flush_looks(&mut self) {
        self.flush_changes(Change::Look);
    }

    /// [`WorldSim::flush_looks`] for visible potion effects (`EffectsChanged`).
    fn flush_effects(&mut self) {
        self.flush_changes(Change::Effects);
    }

    /// [`WorldSim::flush_looks`] for levels (`LevelChanged`).
    fn flush_levels(&mut self) {
        self.flush_changes(Change::Level);
    }

    fn flush_changes(&mut self, kind: Change) {
        let changed_list = match kind {
            Change::Look => &mut self.looks_changed,
            Change::Effects => &mut self.effects_changed,
            Change::Level => &mut self.levels_changed,
        };
        let mut changed = std::mem::take(changed_list);
        changed.sort_unstable();
        changed.dedup();
        for &id in &changed {
            let Some(i) = self.ents.index_of(id) else {
                continue;
            };
            for j in 0..self.ents.len() {
                let c = &mut self.ents.client[j];
                let in_view = j == i || c.visible.binary_search(&id).is_ok();
                let pending = kind.pending(c);
                if in_view && !pending.contains(&id) {
                    pending.push(id);
                }
            }
        }
        changed.clear();
        match kind {
            Change::Look => self.looks_changed = changed,
            Change::Effects => self.effects_changed = changed,
            Change::Level => self.levels_changed = changed,
        }

        let mut frames: HashMap<EntityId, Bytes> = HashMap::new();
        for j in 0..self.ents.len() {
            if kind.pending(&mut self.ents.client[j]).is_empty() {
                continue;
            }
            let me = self.ents.ids[j];
            let mut pending = std::mem::take(kind.pending(&mut self.ents.client[j]));
            // Characters that left the view get the new look when they come
            // back (`EntityInfo`); they need nothing now.
            let visible = &self.ents.client[j].visible;
            pending.retain(|&id| id == me || visible.binary_search(&id).is_ok());
            let mut sent = 0;
            for &id in &pending {
                let c = &self.ents.client[j];
                if c.out.capacity() <= queue_reserve(c) {
                    break;
                }
                let Some(i) = self.ents.index_of(id) else {
                    sent += 1;
                    continue;
                };
                let info = &self.ents.info[i];
                let frame = frames.entry(id).or_insert_with(|| {
                    let msg = match kind {
                        Change::Look => ServerMsg::AppearanceChanged {
                            id,
                            appearance_version: info.appearance_version,
                            appearance: info.appearance,
                        },
                        Change::Effects => ServerMsg::EffectsChanged {
                            id,
                            effects_version: info.effects_version,
                            effects: info.effects.clone(),
                        },
                        Change::Level => ServerMsg::LevelChanged {
                            id,
                            level: info.level,
                            effects_version: info.effects_version,
                        },
                    };
                    encode_server(&msg).into()
                });
                let frame = frame.clone();
                self.send(j, frame);
                sent += 1;
            }
            pending.drain(..sent);
            *kind.pending(&mut self.ents.client[j]) = pending;
        }
    }

    /// Location of each online character among `ids`.
    pub fn online_status(&self, ids: &[i64]) -> HashMap<i64, Location> {
        ids.iter()
            .filter_map(|&cid| Some((cid, self.location_of(self.index_of_character(cid)?))))
            .collect()
    }

    /// Location and room-open flag of each online character among `ids`.
    /// Whether the world still counts on the character: in the world, or
    /// away from a duel within its disconnect grace (it may come back, and
    /// the duel is recorded for it either way).
    pub fn in_play(&self, character_id: i64) -> bool {
        self.index_of_character(character_id).is_some() || self.duels.is_away(character_id)
    }

    pub fn presence_of(&self, ids: &[i64]) -> HashMap<i64, OnlinePresence> {
        ids.iter()
            .filter_map(|&cid| {
                let i = self.index_of_character(cid)?;
                let p = OnlinePresence {
                    location: self.location_of(i),
                    room_open: self.ents.presence[i].dorm_open,
                };
                Some((cid, p))
            })
            .collect()
    }

    /// Removes a character and saves its position. False for stale ids.
    pub fn remove_player(&mut self, id: EntityId) -> bool {
        let now = Instant::now();
        let left = self.leave(id, super::instances::Leaving::Disconnect, now);
        self.settle(now);
        left
    }

    pub fn input(&mut self, id: EntityId, at: Instant, input: Input) {
        self.dispatch_input(id, at, input);
        self.settle(at);
    }

    fn dispatch_input(&mut self, id: EntityId, at: Instant, input: Input) {
        let Some(i) = self.ents.index_of(id) else {
            return;
        };
        // NPCs have no connection; nothing comes from them.
        if self.ents.info[i].npc.is_some() {
            return;
        }
        match input {
            Input::Move {
                seq,
                pos,
                yaw,
                anim,
            } => self.on_move(i, at, seq, pos, yaw, anim),
            Input::Chat {
                channel,
                text,
                client_seq,
            } => self.on_chat(i, at, channel, text, client_seq),
            Input::ViewLimit(n) => self.ents.client[i].view_limit = n.clamp(1, VIEW_LIMIT_MAX),
            Input::Emote(e) => self.on_emote(i, at, e),
            Input::Interact(target) => self.on_interact(i, at, &target),
            Input::Mount(on) => self.on_mount(i, at, on),
            Input::EnterWishingRoom(room) => self.on_enter_wishing_room(i, at, room),
            Input::LookAt(target) => self.on_look_at(i, at, target),
            // Out of a dorm, a class or an arena (a duelist leaving forfeits:
            // the duel handler hears the zone change).
            Input::LeaveInstance => {
                if self.ents.zone[i] != CASTLE {
                    self.leave_dorm(i, at);
                }
            }
            Input::DormPlace { item_id, pos, yaw } => self.on_dorm_place(i, at, item_id, pos, yaw),
            Input::DormMove { item_id, pos, yaw } => self.on_dorm_move(i, at, item_id, pos, yaw),
            Input::DormRemove { item_id } => self.on_dorm_remove(i, at, item_id),
            Input::DormSetOpen(open) => self.on_dorm_set_open(i, at, open),
            Input::DormInvite(name) => self.on_dorm_invite(i, at, &name),
            Input::DormAcceptInvite(invite) => self.on_accept_invite(i, at, invite),
            Input::DormVisit(name) => self.on_dorm_visit(i, at, &name),
            Input::DormKick(entity) => self.on_dorm_kick(i, at, entity),
            Input::DormDeclineInvite(invite) => self.on_decline_invite(i, at, invite),
            Input::UseFurniture(item_id) => self.on_use_furniture(i, at, item_id),
            Input::Trade(input) => self.with_trades(at, |t, ctx| t.on_input(ctx, id, input)),
            Input::Potion(input) => {
                // No drinking in an arena.
                let item_id = match input {
                    PotionInput::Drink { item_id }
                    | PotionInput::MimicDraughtAsk { item_id, .. } => Some(item_id),
                    PotionInput::MimicDraughtAnswer { .. } => None,
                };
                if item_id.is_some() && !self.instances.rules(self.ents.zone[i]).potions {
                    let msg = ServerMsg::PotionError {
                        code: protocol::PotionErrorCode::Disabled,
                        item_id,
                    };
                    return self.send_msg(i, &msg);
                }
                self.with_potions(at, |p, ctx| p.on_input(ctx, id, input))
            }
            Input::Class(input) => self.with_classes(at, |c, ctx| c.on_input(ctx, id, input)),
            Input::Spell(input) => {
                // Arenas allow dueling casts only (ending Lucerna still works).
                if let SpellInput::Cast { spell, .. } = input
                    && !self.instances.rules(self.ents.zone[i]).social_spells
                {
                    let msg = ServerMsg::SpellError {
                        spell,
                        code: protocol::SpellErrorCode::NotHere,
                    };
                    return self.send_msg(i, &msg);
                }
                self.with_spells(at, |s, ctx| s.on_input(ctx, id, input))
            }
            Input::Duel(input) => self.with_duels(at, |d, ctx| d.on_input(ctx, id, input)),
            Input::Npc(input) => self.with_residents(at, |r, ctx| r.on_input(ctx, id, input)),
            Input::PartyInvite(name) => self.on_party_invite(i, at, &name),
            Input::PartyRespond { invite_id, accept } => {
                self.on_party_respond(i, at, invite_id, accept)
            }
            Input::PartyLeave => self.on_party_leave(i, at),
            Input::PartyKick(target) => self.on_party_kick(i, at, target),
            Input::PartyPromote(target) => self.on_party_promote(i, at, target),
            Input::Club(input) => self.on_club_input(i, at, input),
            Input::SetHotbar(slots) => self.on_set_hotbar(i, &slots),
        }
    }

    fn on_move(&mut self, i: usize, at: Instant, seq: u32, pos: [f32; 3], yaw: f32, anim: Anim) {
        let world = self.world_of(i).clone();
        if self.ents.mover[i].last_seq.is_some_and(|last| seq <= last) {
            return;
        }
        // Spells L20: the server is moving the character (Adveni): its moves
        // are dropped, not corrected; the credit keeps accruing so the
        // first move after it passes.
        if self.ents.mover[i].forced.is_some() {
            let m = &mut self.ents.mover[i];
            m.last_seq = Some(seq);
            let earned = at.saturating_duration_since(m.credit_at).as_secs_f32();
            m.credit = (m.credit + earned).min(MOVE_CREDIT_MAX);
            m.credit_at = m.credit_at.max(at);
            return;
        }
        // A rider's move is a flight (the server's mount state
        // decides, never the anim).
        if self.ents.mover[i].mount.is_some() {
            self.ents.mover[i].last_seq = Some(seq);
            return self.on_flight_move(i, at, seq, pos, yaw, anim);
        }
        // Pick-Me-Up Tonic and friends: validated with exactly the speed the client
        // was told (`OwnEffects.speed_pct`).
        let speed_pct = self.move_speed_pct(i, at);
        let params = self.params.with_speed_pct(speed_pct);
        let m = &mut self.ents.mover[i];
        m.last_seq = Some(seq);
        let earned = at.saturating_duration_since(m.credit_at).as_secs_f32();
        m.credit = (m.credit + earned).min(MOVE_CREDIT_MAX);
        m.credit_at = m.credit_at.max(at);
        let credit = m.credit;
        let prev = self.ents.pos[i];

        // In the castle, against the staircase state's world (and
        // near a switch, either side of it: `stairs::check_walk`).
        let (check, walked) = if self.ents.zone[i] == CASTLE {
            self.check_walk(i, at, &params, prev, pos, credit)
        } else {
            let check = check_move(&world, &params, prev, pos, credit);
            (check, horizontal(prev, pos))
        };
        let m = &mut self.ents.mover[i];
        match check {
            MoveCheck::Accept { pos } => {
                let max_speed = params.max_speed();
                // Spells L20: a bound character (speed 0) earns nothing.
                m.credit = if max_speed > 0.0 {
                    (m.credit - walked / max_speed).max(0.0)
                } else {
                    0.0
                };
                self.ents.pos[i] = pos;
                if let Some(level) = world.level_at(pos[0], pos[2], pos[1]) {
                    self.ents.level[i] = level;
                }
                let old = self.ents.state[i];
                let qpos = QPos::from_m(pos);
                // Only idle, walk and run come from moves; emotes stay until
                // the character actually moves.
                let walking = match anim {
                    Anim::Walk | Anim::Run => anim,
                    _ => Anim::Idle,
                };
                let anim = if qpos == old.pos && is_emote(old.anim) {
                    old.anim
                } else {
                    m.emote_until = None;
                    walking
                };
                if qpos != old.pos {
                    m.unsaved = true;
                }
                m.moved_at = Some(at);
                self.ents.state[i] = EntityState {
                    id: old.id,
                    pos: qpos,
                    yaw: if yaw.is_finite() {
                        yaw_to_u8(yaw)
                    } else {
                        old.yaw
                    },
                    anim,
                };
                // The map's reveal, a secret found, the pacing.
                if self.ents.zone[i] == CASTLE {
                    self.explore_moved(i, at);
                }
            }
            MoveCheck::Reject(reason) => {
                if m.zone_entered_at
                    .is_some_and(|t| at.saturating_duration_since(t) < ZONE_CHANGE_GRACE)
                    || m.mount_changed_at.is_some_and(|t| {
                        at.saturating_duration_since(t) < super::flying::MOUNT_CHANGE_GRACE
                    })
                {
                    // Sent before the client saw the zone change.
                    return;
                }
                let cooling = m
                    .last_correction
                    .is_some_and(|t| at.saturating_duration_since(t) < CORRECTION_COOLDOWN);
                let (back, kind) = if world.ground_at(prev[0], prev[2], prev[1]).is_some() {
                    (prev, correction_index(reason))
                } else {
                    // The stored position itself is off the ground (the layout
                    // changed under a saved position): correcting back to it
                    // would loop, so put the player beside it (solid furniture
                    // placed there) or on a spawn.
                    let (p, level, _) = self.spawn_in(&world, Some(prev));
                    self.ents.pos[i] = p;
                    self.ents.level[i] = level;
                    self.ents.state[i].pos = QPos::from_m(p);
                    (p, OFF_GROUND)
                };
                self.metrics.corrections[kind].inc();
                // Fix: traceable round the moving staircases.
                if self.ents.zone[i] == CASTLE {
                    self.stair_correction(i, at, prev, pos, reason);
                }
                if cooling && kind != OFF_GROUND {
                    return;
                }
                self.ents.mover[i].last_correction = Some(at);
                let msg = ServerMsg::Correction {
                    seq,
                    pos: QPos::from_m(back),
                };
                self.send(i, encode_server(&msg).into());
            }
        }
    }

    /// Starts an emote if the character is standing still and not over the
    /// emote rate limit; otherwise ignores it.
    fn on_emote(&mut self, i: usize, at: Instant, emote: protocol::Emote) {
        // Riders show their broom, not emotes.
        if self.ents.state[i].anim.is_moving() || self.ents.mover[i].mount.is_some() {
            return;
        }
        let m = &mut self.ents.mover[i];
        if !m
            .emotes
            .take(at, limits::EMOTE_BURST, limits::EMOTE_PER_SECOND)
        {
            return;
        }
        m.emote_until = emote
            .duration_ms()
            .map(|ms| at + Duration::from_millis(u64::from(ms)));
        self.ents.state[i].anim = emote.anim();
        self.metrics.emotes.inc();
    }

    /// Protocol v10: the character now looks at `target`. Kept only when
    /// the target is another live character in the same zone within
    /// [`LOOK_KEEP_M`]; anything else counts as looking at nobody. Over the
    /// rate limit the message is dropped. Viewers hear of it in their next
    /// snapshot (snapshot.rs `shown_look`), which checks again per viewer.
    fn on_look_at(&mut self, i: usize, at: Instant, target: Option<EntityId>) {
        if !self.ents.mover[i]
            .looks
            .take(at, limits::LOOK_BURST, limits::LOOK_PER_SECOND)
        {
            self.metrics.look_dropped.inc();
            return;
        }
        self.metrics.look_targets.inc();
        let me = self.ents.ids[i];
        let kept = target.filter(|&t| {
            let Some(j) = self.ents.index_of(t) else {
                return false;
            };
            let (a, b) = (self.ents.pos[i], self.ents.pos[j]);
            let d2 = (a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2);
            t != me && self.ents.zone[j] == self.ents.zone[i] && d2 <= LOOK_KEEP_M * LOOK_KEEP_M
        });
        self.ents.look[i] = kept;
    }

    /// Returns characters whose wave or bow ran its time to idle.
    fn end_emotes(&mut self, now: Instant) {
        for i in 0..self.ents.len() {
            let m = &mut self.ents.mover[i];
            if m.emote_until.is_some_and(|t| t <= now) {
                m.emote_until = None;
                if is_emote(self.ents.state[i].anim) {
                    self.ents.state[i].anim = Anim::Idle;
                }
            }
        }
    }

    fn on_chat(
        &mut self,
        i: usize,
        at: Instant,
        channel: ChatChannel,
        text: String,
        client_seq: u32,
    ) {
        let Some(text) = chat::clean_text(&text) else {
            self.reject_chat(i, client_seq, ChatRejectReason::Invalid);
            return;
        };
        // Party or club chat outside one goes nowhere (and costs no token).
        let no_channel = match channel {
            ChatChannel::Party => self.party_chat_recipients(i).is_none(),
            ChatChannel::Club => self.club_chat_recipients(i).is_none(),
            _ => false,
        };
        if no_channel {
            self.reject_chat(i, client_seq, ChatRejectReason::NoChannel);
            return;
        }
        let c = &mut self.ents.client[i];
        if !chat::take_token(&mut c.chat_tokens, &mut c.chat_at, at) {
            self.reject_chat(i, client_seq, ChatRejectReason::RateLimited);
            return;
        }
        // Protocol v2 refuses filtered lines instead of masking them.
        if matches!(self.filter.apply(&text), std::borrow::Cow::Owned(_)) {
            self.reject_chat(i, client_seq, ChatRejectReason::Filtered);
            return;
        }
        // Chatterbrew: the line comes out scrambled (after the filter,
        // which judged what the player wrote).
        let text = if self.ents.effects[i]
            .active
            .iter()
            .any(|e| e.kind == EffectKind::Babbling)
        {
            // Shuffled letters could spell what the filter refuses: it
            // judges the babble too.
            crate::effects::babble_checked(&text, u64::from(client_seq), |line| {
                matches!(self.filter.apply(line), std::borrow::Cow::Borrowed(_))
            })
        } else {
            text
        };
        // Routed during the tick, once the grid matches everyone's position.
        self.pending_chat.push(PendingChat {
            from: self.ents.ids[i],
            at,
            channel,
            text,
            client_seq,
        });
    }

    fn reject_chat(&mut self, i: usize, client_seq: u32, reason: ChatRejectReason) {
        self.metrics.chat_rejected.inc();
        let frame = encode_server(&ServerMsg::ChatRejected { client_seq, reason }).into();
        self.send(i, frame);
    }

    /// Queues a frame for one client; a full queue marks it for disconnection.
    pub(super) fn send(&mut self, i: usize, frame: Bytes) {
        let c = &mut self.ents.client[i];
        if let Err(TrySendError::Full(_)) = c.out.try_send(frame) {
            c.too_slow = true;
        }
    }

    pub(super) fn send_msg(&mut self, i: usize, msg: &ServerMsg) {
        self.send(i, encode_server(msg).into());
    }

    pub(super) fn save(&self, batch: SaveBatch) {
        if let Some(saver) = &self.saver {
            let _ = saver.send(batch);
        }
    }

    /// One simulation step; `now` is the tick's start.
    pub fn tick(&mut self, now: Instant) {
        self.tick = self.tick.wrapping_add(1);
        let t = self.tick;
        // A moving flight that switched carries its riders.
        self.step_stairs(now);
        self.end_emotes(now);
        // Spells L20: binds and slows end on their tick; Adveni's slides.
        self.expire_spell_effects(now);
        self.step_forced(now);
        if t.is_multiple_of(WORLD_TICK_HZ) {
            self.expire(now);
            self.expire_effects(now);
            self.with_trades(now, |x, ctx| x.tick(ctx));
            self.with_potions(now, |x, ctx| x.tick(ctx));
            self.with_finds(now, |x, ctx| x.tick(ctx));
            self.with_spells(now, |x, ctx| x.tick(ctx));
        }
        // Lessons and duels run at the full tick rate (a duel
        // steps every tick); both cost nothing while idle.
        self.with_classes(now, |x, ctx| x.tick(ctx));
        self.with_duels(now, |x, ctx| x.tick(ctx));
        // NPCs: the residents move at the snapshot rate.
        if t.is_multiple_of(TICKS_PER_SNAPSHOT) {
            self.with_residents(now, |x, ctx| x.tick(ctx));
        }
        self.settle(now);
        let zone = &self.ents.zone;
        self.grid
            .rebuild_where(&self.ents.pos, |i| zone[i] == CASTLE);
        self.route_chats();
        self.flush_notices();
        self.flush_looks();
        self.flush_effects();
        self.flush_levels();
        self.build_snapshots(t, now);
        self.send_rosters(t);
        self.drop_slow_clients();
        if t.is_multiple_of(SAVE_INTERVAL_TICKS) {
            self.save_moved();
        }
        self.metrics.players_online.set(self.players() as f64);
        self.metrics.dorm_instances.set(self.instances.len() as f64);
        self.metrics
            .wishing_room_instances
            .set(self.instances.wishing_room_count() as f64);
        let (classes, arenas) = self.instances.feature_counts();
        self.metrics.class_instances.set(classes as f64);
        self.metrics.arena_instances.set(arenas as f64);
        self.flush_jobs();
    }

    /// Once a second: forgets expired invitations, guest reconnect memos,
    /// kick bars and friend-change notes.
    fn expire(&mut self, now: Instant) {
        self.invites.expire(now);
        self.parties.expire(now);
        self.clubs.expire(now);
        self.guest_memo.retain(|_, m| m.until > now);
        self.kick_bars.retain(|_, until| *until > now);
        self.social_changed
            .retain(|_, at| now.saturating_duration_since(*at) < SOCIAL_CHANGE_MEMORY);
        self.pending_looks
            .retain(|_, (_, _, at)| now.saturating_duration_since(*at) < SOCIAL_CHANGE_MEMORY);
        self.station_grants.retain(|_, g| g.until > now);
    }

    fn route_chats(&mut self) {
        let mut pending = std::mem::take(&mut self.pending_chat);
        for msg in pending.drain(..) {
            self.route_chat(msg);
        }
        self.pending_chat = pending;
    }

    fn route_chat(&mut self, msg: PendingChat) {
        let Some(i) = self.ents.index_of(msg.from) else {
            return;
        };
        let sender = self.ents.info[i].character_id;
        let mut recipients = std::mem::take(&mut self.recipients);
        recipients.clear();
        let channel_is_club = msg.channel == ChatChannel::Club;
        let channel = match msg.channel {
            ChatChannel::Say => {
                let p = self.ents.pos[i];
                let r2 = chat::SAY_RADIUS_M * chat::SAY_RADIUS_M;
                let pos = &self.ents.pos;
                match self.ents.zone[i] {
                    CASTLE => self
                        .grid
                        .for_each_near(p[0], p[2], chat::SAY_RADIUS_M, |j| {
                            if dist2(pos[j as usize], p) <= r2 {
                                recipients.push(j);
                            }
                        }),
                    z => {
                        for &id in self.instances.members(z) {
                            if let Some(j) = self.ents.index_of(id)
                                && dist2(pos[j], p) <= r2
                            {
                                recipients.push(j as u32);
                            }
                        }
                    }
                }
                ChatChannel::Say
            }
            ChatChannel::House => {
                let house = self.ents.house[i];
                recipients.extend((0..self.ents.len() as u32).filter(|&j| {
                    self.ents.house[j as usize] == house && self.ents.info[j as usize].npc.is_none()
                }));
                ChatChannel::House
            }
            ChatChannel::Whisper { to } => {
                let target = self
                    .by_name
                    .get(&name_key(to.trim()))
                    .and_then(|&id| self.ents.index_of(id))
                    // Someone who blocked the sender looks offline to them.
                    .filter(|&t| !self.ents.social[t].has_blocked(sender));
                let Some(t) = target else {
                    self.metrics.chat_rejected.inc();
                    let frame = encode_server(&ServerMsg::ChatRejected {
                        client_seq: msg.client_seq,
                        reason: ChatRejectReason::UnknownTarget,
                    })
                    .into();
                    self.chat_out.push((i as u32, frame));
                    self.recipients = recipients;
                    return;
                };
                // Whisper partners stay visible to each other for a while.
                let until = msg.at + chat::WHISPER_PRIORITY;
                let (from_id, to_id) = (self.ents.ids[i], self.ents.ids[t]);
                if t != i {
                    add_priority(&mut self.ents.client[i], to_id, until);
                    add_priority(&mut self.ents.client[t], from_id, until);
                }
                recipients.push(t as u32);
                if t != i {
                    recipients.push(i as u32);
                }
                ChatChannel::Whisper {
                    to: self.ents.info[t].name.clone(),
                }
            }
            // Every online member, in any zone at any distance.
            ChatChannel::Party | ChatChannel::Club => {
                let members = if channel_is_club {
                    self.club_chat_recipients(i)
                } else {
                    self.party_chat_recipients(i)
                };
                let Some(members) = members else {
                    // Left the party since the line was accepted.
                    self.metrics.chat_rejected.inc();
                    let frame = encode_server(&ServerMsg::ChatRejected {
                        client_seq: msg.client_seq,
                        reason: ChatRejectReason::NoChannel,
                    })
                    .into();
                    self.chat_out.push((i as u32, frame));
                    self.recipients = recipients;
                    return;
                };
                recipients.extend(members.into_iter().map(|j| j as u32));
                if channel_is_club {
                    ChatChannel::Club
                } else {
                    ChatChannel::Party
                }
            }
        };
        // Lines from a blocked player are not delivered to the blocker.
        let social = &self.ents.social;
        recipients.retain(|&r| !social[r as usize].has_blocked(sender));
        let frame: Bytes = encode_server(&ServerMsg::Chat {
            from: msg.from,
            from_name: self.ents.info[i].name.clone(),
            channel,
            text: msg.text,
        })
        .into();
        self.chat_out
            .extend(recipients.iter().map(|&r| (r, frame.clone())));
        self.metrics.chat_messages.add(recipients.len() as u64);
        self.recipients = recipients;
    }

    /// Builds the due snapshots in parallel, then hands this tick's chat
    /// lines and the snapshots to the connections (each client still gets its
    /// chat before its snapshot, as when chat was sent while routing).
    ///
    /// Delivering a frame wakes that client's connection task. Doing all of it
    /// after the parallel part keeps the woken tokio workers from competing
    /// with the snapshot workers for the cores (the launch server has two),
    /// and keeps the snapshot workers off tokio's shared run queue.
    fn build_snapshots(&mut self, t: u32, now: Instant) {
        let parity = (t % TICKS_PER_SNAPSHOT) as u8;
        let Self {
            pool,
            grid,
            ents,
            metrics,
            instances,
            snapshot_frames: frames,
            chat_out,
            ..
        } = self;
        let (view, clients) = ents.split();
        let grid = &*grid;
        let instances = &*instances;
        pool.install(|| {
            clients
                .par_iter_mut()
                .enumerate()
                .with_min_len(16)
                .map(|(i, c)| {
                    // NPCs see nobody: they have no connection.
                    if u32::from(c.snapshot_phase) % TICKS_PER_SNAPSHOT != u32::from(parity)
                        || c.too_slow
                        || view.info[i].npc.is_some()
                    {
                        return Built::default();
                    }
                    let (source, zone_priority) = match view.zone[i] {
                        CASTLE => (Source::Grid(grid), &[][..]),
                        z => (
                            Source::Members(instances.candidates(z)),
                            instances.zone_priority(z),
                        ),
                    };
                    snapshot::with_scratch(|s| {
                        snapshot::build(view, source, zone_priority, i, c, t, now, s)
                    })
                })
                .collect_into_vec(frames);
        });
        for (r, frame) in chat_out.drain(..) {
            let c = &mut clients[r as usize];
            // Chat may not take the last few slots: a crowd talking at once
            // (13 people each sending a burst of 5 lines fill a 64-frame
            // queue) must cost a listener some lines, not the connection.
            // Snapshots and corrections still disconnect when the queue is
            // truly full, so a slow client is still found out.
            if c.out.capacity() <= queue_reserve(c) {
                metrics.chat_dropped.inc();
                continue;
            }
            if let Err(TrySendError::Full(_)) = c.out.try_send(frame) {
                c.too_slow = true;
            }
        }
        let (mut full, mut deltas, mut looks) = (0u64, 0u64, 0u64);
        for (c, built) in clients.iter_mut().zip(frames.drain(..)) {
            full += u64::from(built.full);
            deltas += u64::from(built.deltas);
            looks += u64::from(built.looks);
            // A client whose queue just overflowed with chat is dropped this
            // tick; it would not have been sent a snapshot either.
            if let Some(frame) = built.frame.filter(|_| !c.too_slow) {
                metrics.snapshot_bytes.observe(frame.len() as f64);
                if let Err(TrySendError::Full(_)) = c.out.try_send(frame) {
                    c.too_slow = true;
                }
            }
        }
        metrics.snapshot_full_states.add(full);
        metrics.snapshot_deltas.add(deltas);
        metrics.snapshot_looks.add(looks);
    }

    fn send_rosters(&mut self, t: u32) {
        let due = (t % WORLD_TICK_HZ) as u8;
        let online = self.players() as u32;
        let r2 = NEARBY_RADIUS_M * NEARBY_RADIUS_M;
        for i in 0..self.ents.len() {
            if self.ents.client[i].roster_phase != due || self.ents.info[i].npc.is_some() {
                continue;
            }
            let p = self.ents.pos[i];
            let mut nearby = 0u32;
            let pos = &self.ents.pos;
            let info = &self.ents.info;
            match self.ents.zone[i] {
                // NPCs (the castle's residents) are not players nearby.
                CASTLE => self.grid.for_each_near(p[0], p[2], NEARBY_RADIUS_M, |j| {
                    if j as usize != i
                        && dist2(pos[j as usize], p) <= r2
                        && info[j as usize].npc.is_none()
                    {
                        nearby += 1;
                    }
                }),
                z => nearby = self.instances.members(z).len().saturating_sub(1) as u32,
            }
            self.send_msg(i, &ServerMsg::Roster { online, nearby });
        }
    }

    fn drop_slow_clients(&mut self) {
        let mut kicks = std::mem::take(&mut self.kicks);
        kicks.clear();
        kicks.extend(
            (0..self.ents.len())
                .filter(|&i| self.ents.client[i].too_slow)
                .map(|i| self.ents.ids[i]),
        );
        for &id in &kicks {
            if let Some(i) = self.ents.index_of(id) {
                tracing::info!(name = %self.ents.info[i].name, "disconnecting a client that cannot keep up");
                self.ents.client[i].control.kick(KickCode::TooSlow);
                self.metrics.kicks_slow.inc();
                self.remove_player(id);
            }
        }
        self.kicks = kicks;
    }

    fn save_moved(&mut self) {
        let mut batch = SaveBatch::new();
        for i in 0..self.ents.len() {
            if std::mem::take(&mut self.ents.mover[i].unsaved) {
                batch.positions.push(self.resume_point(i));
            }
        }
        // The map's reveals, in the same batch.
        batch.reveals = self.take_all_reveals();
        if !batch.is_empty() {
            self.save(batch);
        }
    }

    /// Disconnects everyone and hands every resume point to the saver, then
    /// closes the saver channel so it can finish.
    pub fn shutdown(&mut self) {
        let batch = SaveBatch {
            positions: (0..self.ents.len())
                .filter(|&i| self.ents.info[i].npc.is_none())
                .map(|i| self.resume_point(i))
                .collect(),
            reveals: self.take_all_reveals(),
            hotbars: Vec::new(),
        };
        for c in &self.ents.client {
            c.control.kick(KickCode::ServerRestart);
        }
        self.save(batch);
        self.saver = None;
        while let Some(&id) = self.ents.ids.last() {
            self.ents.remove(id);
        }
        self.by_character.clear();
        self.by_name.clear();
        self.explore.clear();
        self.instances = Instances::default();
        self.npc_count = 0;
        self.metrics.players_online.set(0.0);
    }
}

/// How long a friend-list change for an offline character is remembered (a
/// join racing the change reloads).
pub(super) const SOCIAL_CHANGE_MEMORY: Duration = Duration::from_secs(10);

/// Wave, bow or sit.
pub(super) fn is_emote(anim: Anim) -> bool {
    matches!(anim, Anim::Wave | Anim::Bow | Anim::Sit | Anim::Cast)
}

/// When a character's speed drops (Pick-Me-Up Tonic ends), moves at the old speed
/// still pass for this long: they were sent before the client heard.
pub const SPEED_DROP_GRACE: Duration = Duration::from_secs(1);

/// Spells L20: a forced move ends this long after its hold, so the client's
/// last moves from before it heard are dropped too.
pub const FORCED_TAIL: Duration = Duration::from_millis(250);

/// The movement speed a set of effects gives (the fastest of the boosts;
/// hindrances count apart, [`hindrance_pct`]).
pub(super) fn effective_speed(effects: &[ActiveEffect]) -> u16 {
    effects
        .iter()
        .filter(|e| !e.kind.is_hindrance())
        .map(|e| e.speed_pct)
        .max()
        .unwrap_or(SPEED_PCT_NORMAL)
        .clamp(SPEED_PCT_NORMAL, protocol::SPEED_PCT_MAX)
}

/// Spells L20: the multiplier (percent) binds and slows put on the speed:
/// the slowest of them, 100 with none.
pub(super) fn hindrance_pct(effects: &[ActiveEffect]) -> u16 {
    effects
        .iter()
        .filter(|e| e.kind.is_hindrance())
        .map(|e| e.speed_pct)
        .min()
        .unwrap_or(SPEED_PCT_NORMAL)
        .clamp(protocol::SPEED_PCT_MIN, SPEED_PCT_NORMAL)
}

/// Whole seconds from `now` to `until`, rounded up.
pub(super) fn remaining_secs(until: Instant, now: Instant) -> u32 {
    let d = until.saturating_duration_since(now);
    let secs = d.as_secs() + u64::from(d.subsec_nanos() > 0);
    u32::try_from(secs).unwrap_or(u32::MAX)
}

/// Whether `pos` is within `radius` of `target` horizontally, on the same floor.
pub(super) fn within(pos: [f32; 3], target: [f32; 3], radius: f32) -> bool {
    let (dx, dz) = (pos[0] - target[0], pos[2] - target[2]);
    dx * dx + dz * dz <= radius * radius
        && (pos[1] - target[1]).abs() <= sim_core::layout::INTERACT_HEIGHT_TOLERANCE
}

/// Which per-tick change `flush_changes` delivers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Change {
    Look,
    Effects,
    Level,
}

impl Change {
    fn pending(self, c: &mut Client) -> &mut Vec<EntityId> {
        match self {
            Change::Look => &mut c.pending_looks,
            Change::Effects => &mut c.pending_effects,
            Change::Level => &mut c.pending_levels,
        }
    }
}

/// A friend-event frame.
pub(super) fn friend_frame(event: FriendEvent) -> Bytes {
    encode_server(&ServerMsg::Friend { event }).into()
}

pub(super) fn dist2(a: [f32; 3], b: [f32; 3]) -> f32 {
    let (dx, dy, dz) = (a[0] - b[0], a[1] - b[1], a[2] - b[2]);
    dx * dx + dy * dy + dz * dz
}

/// Slot `slot` of the spiral around a spawn (slot 0 is the spawn itself).
/// The character walks there with `move_and_slide`, so the spot is always
/// reachable from the spawn and fits its footprint; walls just pull a slot in.
fn spawn_slot(
    world: &sim_core::World,
    params: &MoveParams,
    spawn: [f32; 3],
    slot: usize,
) -> [f32; 3] {
    if slot == 0 {
        return spawn;
    }
    // Vogel's spiral: equal area per slot, so neighbours stay about
    // SPAWN_SPACING apart however many there are.
    let r = SPAWN_SPACING * (slot as f32).sqrt();
    let a = slot as f32 * GOLDEN_ANGLE;
    sim_core::move_and_slide(world, params, spawn, [r * a.cos(), r * a.sin()])
}

/// Adds or refreshes a priority player, evicting the one expiring soonest.
fn add_priority(c: &mut Client, id: EntityId, until: Instant) {
    if let Some(p) = c.priority.iter_mut().find(|(p, _)| *p == id) {
        p.1 = until;
    } else if c.priority.len() < MAX_PRIORITY {
        c.priority.push((id, until));
    } else if let Some(p) = c.priority.iter_mut().min_by_key(|(_, t)| *t) {
        *p = (id, until);
    }
}

/// The world thread's loop: drain commands, tick, sleep until the next slot.
pub fn run(mut sim: WorldSim, mut rx: mpsc::Receiver<WorldCmd>) {
    let metrics = sim.metrics.clone();
    let mut next = Instant::now() + TICK;
    loop {
        let start = Instant::now();
        for _ in 0..MAX_COMMANDS_PER_TICK {
            match rx.try_recv() {
                Ok(cmd) => {
                    if !sim.handle(cmd) {
                        return;
                    }
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    sim.shutdown();
                    return;
                }
            }
        }
        sim.tick(start);
        metrics.tick_seconds.observe_duration(start.elapsed());
        // The ticks within a second of a staircase switch, apart.
        if sim.near_stair_switch(start) {
            metrics
                .stairs_switch_tick_seconds
                .observe_duration(start.elapsed());
        }
        let now = Instant::now();
        if now < next {
            std::thread::sleep(next - now);
            next += TICK;
        } else {
            metrics.tick_overruns.inc();
            next = now + TICK;
        }
    }
}

/// Ends the process if the world thread panics. Without the world the server
/// would stay up and healthy-looking (HTTP still answers) while kicking every
/// player and refusing every join until someone noticed; exiting lets the
/// supervisor (`restart: unless-stopped` in docker/compose.yml) start a fresh
/// one. Positions saved in the last 30 s, and on every leave, are kept.
struct AbortOnPanic;

impl Drop for AbortOnPanic {
    fn drop(&mut self) {
        if std::thread::panicking() {
            tracing::error!("the world thread panicked; exiting so the server restarts");
            std::process::abort();
        }
    }
}

/// How the gateway reaches the world. Cheap to clone.
#[derive(Clone)]
pub struct WorldHandle {
    tx: mpsc::Sender<WorldCmd>,
    metrics: Arc<RealtimeMetrics>,
    clock: Arc<ServerClock>,
}

impl WorldHandle {
    /// Starts `sim` on its own thread. With a `pool`, database jobs (dorm
    /// rooms, friend reloads) run on the current tokio runtime.
    pub fn spawn(
        mut sim: WorldSim,
        pool: Option<sqlx::PgPool>,
    ) -> (WorldHandle, std::thread::JoinHandle<()>) {
        let (tx, rx) = mpsc::channel(COMMAND_QUEUE);
        if let Some(pool) = pool {
            let runtime = tokio::runtime::Handle::current();
            sim.set_executor(Executor::new(pool, runtime, tx.downgrade()));
        }
        let metrics = sim.metrics.clone();
        let clock = sim.clock.clone();
        let thread = std::thread::Builder::new()
            .name("world".into())
            .spawn(move || {
                let _abort_on_panic = AbortOnPanic;
                run(sim, rx);
            })
            .expect("world thread starts");
        (WorldHandle { tx, metrics, clock }, thread)
    }

    /// Adds a character; `None` if the world is full or stopped.
    pub async fn join(
        &self,
        joining: Joining,
        out: mpsc::Sender<Bytes>,
        control: Arc<ConnControl>,
    ) -> Option<EntityId> {
        let (reply, rx) = oneshot::channel();
        self.tx
            .send(WorldCmd::Join {
                joining: Box::new(joining),
                out,
                control,
                reply,
            })
            .await
            .ok()?;
        rx.await.ok().flatten()
    }

    pub async fn leave(&self, id: EntityId) {
        let _ = self.tx.send(WorldCmd::Leave { id }).await;
    }

    /// Forwards input without waiting; dropped (and counted) if the world is
    /// backed up. Returns false once the world has stopped.
    pub fn input(&self, id: EntityId, input: Input) -> bool {
        let cmd = WorldCmd::Input {
            id,
            at: Instant::now(),
            input,
        };
        match self.tx.try_send(cmd) {
            Ok(()) => true,
            Err(TrySendError::Full(_)) => {
                self.metrics.inbound_dropped.inc();
                true
            }
            Err(TrySendError::Closed(_)) => false,
        }
    }

    // ---- Seams for the HTTP side (docs/architecture.md).
    // The HTTP handlers call these after committing their database change;
    // none of them fails or blocks on a busy world (a dropped notification is
    // logged and costs only freshness). Through `AppState::world()`, which is
    // `None` when the realtime side is not running (HTTP-only tests).

    /// Queues a command for the world without waiting; dropped (and logged)
    /// if the command channel is full or the world has stopped.
    fn notify_world(&self, cmd: WorldCmd) {
        if let Err(e) = self.tx.try_send(cmd) {
            tracing::warn!(
                full = matches!(e, TrySendError::Full(_)),
                "world notification dropped"
            );
        }
    }

    /// A character's appearance changed (equip, dye, outfit, creation):
    /// `version` is the new `characters.appearance_version`. Everyone who sees
    /// them, and the character itself, gets `AppearanceChanged`.
    pub fn appearance_changed(&self, character_id: i64, appearance: Appearance, version: u32) {
        self.notify_world(WorldCmd::AppearanceChanged {
            character_id,
            appearance,
            version,
        });
    }

    /// A character's friends or blocks changed (request accepted, friend
    /// removed, block or unblock): the world reloads them with
    /// `friends::friend_ids` / `friends::block_lists` for priority players,
    /// chat and invite checks, and sends `FriendEvent::Online` both ways for
    /// new friends who are online. Call it for both characters involved.
    pub fn friends_changed(&self, character_id: i64) {
        self.notify_world(WorldCmd::FriendsChanged { character_id });
    }

    /// Club `club_id` changed in the database outside the world (a member's
    /// character was deleted): the world reloads it, sends it to its online
    /// members and tells them `notices`.
    pub fn club_changed(&self, club_id: i64, notices: Vec<protocol::ClubNotice>) {
        self.notify_world(WorldCmd::ClubChanged { club_id, notices });
    }

    /// Pushes a friend-list change to a character if they are online (the
    /// HTTP side sends `RequestReceived`, `Added`, `RequestClosed` and
    /// `Removed`; the realtime side itself sends `Online`, `Moved`, `Offline`).
    pub fn friend_event(&self, character_id: i64, event: FriendEvent) {
        self.notify_world(WorldCmd::Notify {
            character_id,
            frame: friend_frame(event),
        });
    }

    /// Which of `ids` are online, and where. Offline characters are absent
    /// from the map. Waits for the world (at most a tick or two); an empty
    /// map if it has stopped.
    pub async fn online_status(&self, ids: &[i64]) -> HashMap<i64, Location> {
        self.presence(ids)
            .await
            .into_iter()
            .map(|(id, p)| (id, p.location))
            .collect()
    }

    /// [`WorldHandle::online_status`] with whether each one's room is open
    /// to friends (friend lists show Visit only then).
    /// Whether the world still counts on the character: in the world, or
    /// away from a duel it may come back to (its disconnect grace). False
    /// if the world has stopped.
    pub async fn in_play(&self, character_id: i64) -> bool {
        let (reply, rx) = oneshot::channel();
        let cmd = WorldCmd::InPlay {
            character_id,
            reply,
        };
        if self.tx.send(cmd).await.is_err() {
            return false;
        }
        rx.await.unwrap_or(false)
    }

    pub async fn presence(&self, ids: &[i64]) -> HashMap<i64, OnlinePresence> {
        let (reply, rx) = oneshot::channel();
        let cmd = WorldCmd::OnlineStatus {
            ids: ids.to_vec(),
            reply,
        };
        if self.tx.send(cmd).await.is_err() {
            return HashMap::new();
        }
        rx.await.unwrap_or_default()
    }

    /// What an online character has explored and the world has
    /// not saved yet (with what it loaded); `None` when offline.
    pub async fn explored(&self, character_id: i64) -> Option<crate::map::Explored> {
        let (reply, rx) = oneshot::channel();
        let cmd = WorldCmd::Explored {
            character_id,
            reply,
        };
        if self.tx.send(cmd).await.is_err() {
            return None;
        }
        rx.await.ok().flatten()
    }

    // ---- Seams for the HTTP side.

    /// A character's Crown balance changed (shop order, mail collected,
    /// Exchange purchase or listing fee, letter sent): `Wallet` to them if
    /// online. Call it after committing, with the new balance.
    pub fn wallet_changed(&self, character_id: i64, crowns: i64) {
        self.notify_world(WorldCmd::Notify {
            character_id,
            frame: encode_server(&ServerMsg::Wallet { crowns }).into(),
        });
    }

    /// A character's unread mail count changed (mail arrived, or they read
    /// or deleted some): `MailNotice` to them if online.
    pub fn mail_changed(&self, character_id: i64, unread: u32) {
        self.notify_world(WorldCmd::Notify {
            character_id,
            frame: encode_server(&ServerMsg::MailNotice { unread }).into(),
        });
    }

    /// Whether the character may use a `kind` station right now: they
    /// opened one with `Interact` / `UseFurniture` (`StationOpened`) within
    /// `STATION_GRANT_SECS` and still stand within its reach, in the same
    /// zone. `None` otherwise, or if the world has stopped. Prefer
    /// `realtime::stations::require`, which answers `403 not_at_station`.
    pub async fn station_access(
        &self,
        character_id: i64,
        kind: StationKind,
    ) -> Option<StationAccess> {
        let (reply, rx) = oneshot::channel();
        let cmd = WorldCmd::StationAccess {
            character_id,
            kind,
            reply,
        };
        self.tx.send(cmd).await.ok()?;
        rx.await.ok().flatten()
    }

    // ---- Seams for the HTTP side.

    /// The game's clock (shared with the world thread and the gateway).
    pub fn clock(&self) -> &Arc<ServerClock> {
        &self.clock
    }

    /// A character's XP changed (after committing a grant, e.g. a craft):
    /// `Progress` to them with `gained`, `LevelChanged` to viewers on a new
    /// level. Prefer `progression::notify`.
    pub fn progress_changed(&self, character_id: i64, xp: u32, gained: Option<protocol::XpGain>) {
        self.notify_world(WorldCmd::ProgressChanged {
            character_id,
            xp,
            gained,
        });
    }

    /// A character's Flying skill XP changed outside a class (a
    /// dev grant): the world's cached value.
    pub fn flying_changed(&self, character_id: i64, xp: u32) {
        self.notify_world(WorldCmd::FlyingChanged { character_id, xp });
    }

    /// A character's learned spells or loadout changed (`PUT /api/loadout`,
    /// spells learned outside the world): `Spellbook` to them.
    pub fn spellbook_changed(
        &self,
        character_id: i64,
        learned: Vec<protocol::SpellId>,
        loadout: Vec<protocol::SpellId>,
        new: Vec<protocol::SpellId>,
    ) {
        self.notify_world(WorldCmd::SpellbookChanged {
            character_id,
            learned,
            loadout,
            new,
        });
    }

    /// Development only: moves an online character to `pos` in their zone
    /// (`WorldCmd::DevTeleport`). False if offline, off the ground or the
    /// world has stopped.
    pub async fn dev_teleport(&self, character_id: i64, pos: [f32; 3]) -> bool {
        let (reply, rx) = oneshot::channel();
        let cmd = WorldCmd::DevTeleport {
            character_id,
            pos,
            reply,
        };
        if self.tx.send(cmd).await.is_err() {
            return false;
        }
        rx.await.unwrap_or(false)
    }

    /// Stops the world after disconnecting and saving everyone.
    pub async fn shutdown(&self) {
        let (done, rx) = oneshot::channel();
        if self.tx.send(WorldCmd::Shutdown { done }).await.is_ok() {
            let _ = rx.await;
        }
    }
}

#[cfg(test)]
#[path = "world_tests.rs"]
pub(crate) mod tests;

#[cfg(test)]
#[path = "hotbar_tests.rs"]
mod hotbar_tests;
