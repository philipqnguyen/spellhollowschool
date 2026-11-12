//! Dueling (protocol v5): challenges in a dueling area, the
//! arena instance, the duel itself and spectators. See docs/architecture.md,
//! "Spells and dueling".
//!
//! **Challenges.** Two players standing in a venue's dueling area (the
//! Dueling Club in the Great Hall): `DuelChallenge` → the target gets
//! `DuelChallenged` (the challenger an echo with the id), `DuelRespond`
//! accepts or declines, `DuelCancel` withdraws. `DuelChallengeEnded` closes
//! a challenge for both.
//!
//! **The duel.** Accepting moves both into a new arena instance
//! (`ZoneChanged` with `Zone::Arena`), followed by `DuelStart`. The duel is
//! decided entirely by the server at the 20 Hz tick
//! (`sim_core::duel::Duel::step`): duelists send `DuelCast` (a spell of their
//! loadout) and move as usual (sidestepping projectiles inside the ring);
//! the instance's members get `DuelEvents` (casts, projectiles, blocks,
//! reflects, hits, disarms) once per tick that has any. A projectile flies
//! in a straight line from `Launched.from` toward `to` at `speed_cms`
//! (centimetres per second), so clients draw it between events without
//! updates. Arena positions are centimetres (`ArenaPos`).
//! `DuelResult` ends it; a few seconds later everyone returns to the club.
//!
//! **Spectators.** Players in the dueling area ask for `DuelList` and
//! `DuelSpectate` a duel: they join its arena (no actions) and get
//! `DuelStart` with the current state. `LeaveInstance` takes them back.
//! Nothing is ever lost in a duel: no items, no Crowns.
//!
//! **Protocol v11** (spells to level 20): binds ([`DuelEvent::Rooted`]),
//! slows ([`DuelEvent::Slowed`]), Adveni's pull and stagger
//! ([`DuelEvent::Pulled`], [`DuelEvent::Staggered`],
//! [`CastRefusal::Staggered`]), Dissolvo's cleanse ([`DuelEvent::Cleansed`])
//! and the Guardian ward ([`DuelEvent::WardUp`], [`DuelEvent::Warded`],
//! [`DuelEvent::WardFaded`]); [`DuelistState`] carries each status.

use serde::{Deserialize, Serialize};

use crate::school::SpellId;
use crate::{EntityId, House};

/// A duel challenge may be answered for this long (seconds).
pub const DUEL_CHALLENGE_SECS: u16 = 30;

/// The two duelists. `A` is the challenger.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DuelSide {
    A,
    B,
}

impl DuelSide {
    pub const BOTH: [DuelSide; 2] = [DuelSide::A, DuelSide::B];

    pub fn index(self) -> usize {
        match self {
            DuelSide::A => 0,
            DuelSide::B => 1,
        }
    }

    pub fn other(self) -> DuelSide {
        match self {
            DuelSide::A => DuelSide::B,
            DuelSide::B => DuelSide::A,
        }
    }
}

/// How the client takes part in an arena.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DuelRole {
    Duelist(DuelSide),
    Spectator,
}

/// One duelist as `DuelStart` introduces them.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DuelistInfo {
    pub entity: EntityId,
    pub name: String,
    pub house: House,
    pub level: u8,
    /// Spells they may cast this duel (their loadout when it started).
    pub loadout: Vec<SpellId>,
}

/// An arena position: centimetres in the arena layout's x and z (the
/// arena is small, so `i16` is plenty).
pub type ArenaPos = [i16; 2];

/// A projectile in flight (`DuelSnapshot`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectileState {
    pub id: u16,
    /// Whose projectile it is now (reflected ones change sides).
    pub side: DuelSide,
    pub spell: SpellId,
    pub pos: ArenaPos,
    /// The point it flies toward (at the end of its range).
    pub to: ArenaPos,
    /// Centimetres per second.
    pub speed_cms: u16,
}

/// What one duelist is doing (`DuelSnapshot`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DuelistState {
    pub poise: u16,
    /// A cast in progress and when it releases (duel clock, ms).
    pub casting: Option<(SpellId, u32)>,
    /// A shield up until (duel clock, ms).
    pub shield_until: Option<u32>,
    /// Disarmed until (duel clock, ms).
    pub disarmed_until: Option<u32>,
    /// Spells cooling down and when each is ready (duel clock, ms).
    pub cooldowns: Vec<(SpellId, u32)>,
    /// Protocol v11: rooted (no moving) until (duel clock, ms).
    pub rooted_until: Option<u32>,
    /// Protocol v11: slowed until (duel clock, ms), to `pct`% speed.
    pub slowed: Option<(u32, u8)>,
    /// Protocol v11: staggered (no casting, no moving) until (duel clock, ms).
    pub staggered_until: Option<u32>,
    /// Protocol v11: a Guardian ward up until (duel clock, ms).
    pub ward_until: Option<u32>,
}

/// Where a duel stands (spectators joining late, `DuelStart.state`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DuelSnapshot {
    /// The duel clock (ms since the arena opened; the countdown is part of it).
    pub at_ms: u32,
    /// When the fight starts (the end of the countdown), duel clock ms.
    pub fight_at_ms: u32,
    pub sides: [DuelistState; 2],
    pub projectiles: Vec<ProjectileState>,
}

/// Why a duelist's cast was refused (`DuelEvent::Refused`, sent to that
/// duelist only).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CastRefusal {
    /// Not in the duelist's loadout (or not a dueling spell).
    NotInLoadout,
    /// Still cooling down.
    Cooldown,
    /// Disarmed: no casting for a moment.
    Disarmed,
    /// Already casting.
    Busy,
    /// The countdown is still running, or the duel is over.
    NotFighting,
    /// Protocol v11: staggered by Adveni: no casting for a moment.
    Staggered,
}

/// How a duel ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DuelEndReason {
    /// A duelist's Poise reached 0.
    Poise,
    /// The time limit ran out: the higher Poise wins (equal is a draw).
    Timeout,
    /// A duelist gave up (`DuelForfeit` or left the arena).
    Forfeit,
    /// A duelist disconnected.
    Disconnect,
}

/// Something that happened in a duel this tick. Times are the duel clock (ms).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DuelEvent {
    /// The countdown is over: casting is open.
    Fight,
    /// A cast began (telegraphed by gesture and colour); it releases at `release_ms`.
    CastStarted {
        side: DuelSide,
        spell: SpellId,
        release_ms: u32,
    },
    /// A cast was refused (to that duelist only).
    Refused {
        side: DuelSide,
        spell: SpellId,
        reason: CastRefusal,
    },
    /// A long cast was broken by a hit, or by being disarmed.
    Interrupted { side: DuelSide, spell: SpellId },
    /// A shield is up until `until_ms`; blocks that land before
    /// `perfect_until_ms` reflect.
    ShieldUp {
        side: DuelSide,
        spell: SpellId,
        until_ms: u32,
        perfect_until_ms: u32,
    },
    /// A projectile left the wand.
    Launched {
        id: u16,
        side: DuelSide,
        spell: SpellId,
        from: ArenaPos,
        to: ArenaPos,
        speed_cms: u16,
    },
    /// A shield stopped it.
    Blocked { id: u16, by: DuelSide },
    /// A perfectly timed shield sent it back: it now flies toward `to` and belongs
    /// to `by` (same speed).
    Reflected {
        id: u16,
        by: DuelSide,
        from: ArenaPos,
        to: ArenaPos,
    },
    /// A shield-breaking spell (Dimitto) shattered the shield.
    ShieldBroken { side: DuelSide },
    /// A hit: `poise` is the target's Poise after it.
    Hit {
        id: u16,
        target: DuelSide,
        damage: u16,
        poise: u16,
    },
    /// No casting until `until_ms`.
    Disarmed { side: DuelSide, until_ms: u32 },
    /// It flew out of range without hitting anything.
    Missed { id: u16 },
    /// The duel is over (`DuelResult` follows with the rewards).
    Ended {
        winner: Option<DuelSide>,
        reason: DuelEndReason,
    },
    /// Protocol v11: Rigora: no moving until `until_ms` (casting and
    /// shielding still work).
    Rooted { side: DuelSide, until_ms: u32 },
    /// Protocol v11: Tardara: moving at `pct`% until `until_ms`.
    Slowed {
        side: DuelSide,
        until_ms: u32,
        pct: u8,
    },
    /// Protocol v11: Adveni: the duelist slides in a straight line from
    /// `from` to `to`, arriving at `until_ms` (`Staggered` follows).
    Pulled {
        side: DuelSide,
        from: ArenaPos,
        to: ArenaPos,
        until_ms: u32,
    },
    /// Protocol v11: no casting and no moving until `until_ms`.
    Staggered { side: DuelSide, until_ms: u32 },
    /// Protocol v11: Dissolvo released: the duelist's root and
    /// slow are gone (sent even when there was nothing to clear).
    Cleansed { side: DuelSide, spell: SpellId },
    /// Protocol v11: a Guardian ward is up until `until_ms`: it blocks the
    /// next spell that would land, from any direction.
    WardUp {
        side: DuelSide,
        spell: SpellId,
        until_ms: u32,
    },
    /// Protocol v11: the ward of `by` stopped projectile `id` (and is gone).
    Warded { id: u16, by: DuelSide },
    /// Protocol v11: the ward faded unused.
    WardFaded { side: DuelSide },
}

/// How a challenge ended (`ServerMsg::DuelChallengeEnded`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChallengeEnd {
    /// Accepted: `ZoneChanged` into the arena follows.
    Started,
    Declined,
    Cancelled,
    Expired,
    /// Either left the dueling area.
    WalkedAway,
    Disconnected,
}

/// What a duelist earned (`DuelResult.rewards`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct DuelRewards {
    pub xp: u32,
    /// The daily duel XP cap (or the same-opponent limit) cut it.
    pub capped: bool,
    /// For the winner's house.
    pub house_points: u16,
}

/// A duel spectators can watch (`ServerMsg::DuelList`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DuelSummary {
    pub duel_id: u32,
    pub a_name: String,
    pub a_house: House,
    pub b_name: String,
    pub b_house: House,
    pub spectators: u16,
    /// Since the arena opened.
    pub elapsed_ms: u32,
}

/// Why a duel action was refused (`ServerMsg::DuelError`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DuelErrorCode {
    /// No such player here (or one who blocked the sender).
    UnknownTarget,
    /// Challenging yourself.
    SelfDuel,
    /// The sender is not in a dueling area.
    NotInArea,
    /// The other player is not in the same dueling area.
    TargetNotInArea,
    /// Either side is dueling, spectating or has a challenge pending.
    Busy,
    /// No such challenge, or not this player's.
    UnknownChallenge,
    /// The challenge expired.
    Expired,
    /// No such duel to watch.
    UnknownDuel,
    /// The arena has as many spectators as it takes.
    Full,
    /// The sender has no dueling spells yet (the Dueling Club teaches them).
    NoSpells,
    /// Too many actions; slow down.
    RateLimited,
    /// The server could not complete it.
    Failed,
}
