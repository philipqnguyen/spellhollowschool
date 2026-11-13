//! Dueling on the world thread: challenges, arenas, the duel
//! loop and spectators (rules in [`crate::dueling`]). The world owns a
//! [`Duels`] and calls it:
//!
//! - `on_input` for every `ClientMsg::Duel*` (as `DuelMsg`);
//! - `on_done` with the [`DuelDone`] of a job it spawned (the duel's record
//!   and rewards, a Dueling Club lesson);
//! - `on_left` before a character leaves the world: its challenge ends as
//!   `Disconnected`; a duelist gets `disconnect_grace_ms` to come back;
//! - `on_zone_changed` after a zone change: a challenge ends as
//!   `WalkedAway`, a duelist walking out of the arena forfeits, a spectator
//!   stops watching;
//! - `on_instance_closed` when an arena emptied or was closed;
//! - `tick` every world tick: every running duel steps once
//!   (`sim_core::Duel::step`); once a second, challenges expire or end when
//!   a side walked out of the area, and the Dueling Club teaches whoever
//!   stands in it the spells their level allows.
//!
//! When the world stops, the [`Duels`] it drops leave every duel
//! still in its countdown or fight in [`Unfinished`] (if the realtime side
//! handed it one), for `Realtime::shutdown` to record as no contest.
//!
//! Idle, it costs a map lookup per tick; a running duel costs one step and
//! at most one small `DuelEvents` frame per member for a tick with events.
//!
//! Spells to level 20 (protocol v11): the duel's statuses become world
//! effects on the duelists ([`world_effects`]): a root is `Petrified`
//! (speed 0), a slow `Slowed`, Adveni's pull a forced move
//! (`WorldCtx::force_move`) held for the stagger, Dissolvo removes both
//! hindrances, a ward is the duelist's guarding `Guardian`. A duel's start
//! and end clear them all; a duelist coming back mid-duel gets them again
//! from the duel ([`restore_statuses`]).

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use protocol::{
    ArenaPos, ChallengeEnd, DuelEndReason, DuelErrorCode, DuelEvent, DuelRole, DuelSide,
    DuelSummary, DuelistInfo, EffectKind, EntityId, ServerMsg, SpellId, VisibleEffect,
};
use sim_core::{AreaKind, Duel, DuelInput, Venue};
use sqlx::PgPool;

use super::{Finished, NoContest, Recorded, Taught};
use crate::catalog::shipped;
use crate::realtime::entities::{CASTLE, ZoneId};
use crate::realtime::ext::{ActiveEffect, DuelMsg, ExtDone, FeatureKind, WorldCtx};

/// After a Dueling Club lesson, the next check for that character waits
/// this long (a lesson that changed nothing cannot loop).
const TEACH_COOLDOWN: Duration = Duration::from_secs(5);
/// After a failed lesson (the database), the next try waits this long.
const TEACH_RETRY: Duration = Duration::from_secs(30);
/// Spectators stand in rows of this many along the stage.
const SPECTATOR_ROW: usize = 10;
/// Between neighbouring spectators, and between rows (metres).
const SPECTATOR_SPACING_M: f32 = 1.3;

/// What a dueling job hands back to the world thread.
#[derive(Debug)]
pub enum DuelDone {
    /// A finished duel was recorded (or the record failed).
    Recorded {
        duel_id: u32,
        zone: ZoneId,
        finished: Finished,
        result: Result<Recorded, String>,
    },
    /// The Dueling Club taught a character (or the lesson failed).
    Taught {
        character_id: i64,
        result: Result<Taught, String>,
    },
}

#[derive(Debug)]
struct Challenge {
    from: EntityId,
    to: EntityId,
    /// The venue both stood in.
    venue: String,
    expires: Instant,
}

/// One side of a running duel.
#[derive(Debug)]
struct Duelist {
    /// The current entity (a reconnect brings a new one).
    entity: EntityId,
    character_id: i64,
    info: DuelistInfo,
    /// The cast received since the last step (the latest wins).
    cast: Option<SpellId>,
    /// A forfeit (or a lapsed reconnect grace) for the next step.
    leave: Option<DuelEndReason>,
    /// Where they stand in the ring (arena x, z), as last seen.
    pos: [f32; 2],
    /// In the arena: their moves and casts count.
    present: bool,
    /// Disconnected mid-duel: they may come back until then.
    away_until: Option<Instant>,
}

#[derive(Debug)]
struct Arena {
    zone: ZoneId,
    /// `duels.duel_key`.
    key: i64,
    venue: String,
    duel: Duel,
    sides: [Duelist; 2],
    spectators: Vec<EntityId>,
    started_at: DateTime<Utc>,
    /// Once over: when everyone returns to the club.
    return_at: Option<Instant>,
}

impl Arena {
    fn side(&self, side: DuelSide) -> &Duelist {
        &self.sides[side.index()]
    }

    /// `side` gives up (a forfeit, or walking out) at the next step, unless
    /// the duel is over or the other side already did: the first to go
    /// loses.
    fn forfeit(&mut self, side: DuelSide) {
        if !self.duel.is_over() && self.side(side.other()).leave.is_none() {
            let d = &mut self.sides[side.index()];
            d.leave = d.leave.or(Some(DuelEndReason::Forfeit));
        }
    }
}

/// The duels still running when the world stopped, left by the
/// dropped [`Duels`] for the shutdown to record ([`super::record_no_contests`]).
/// Cheap to clone; every clone is the same list.
#[derive(Debug, Clone, Default)]
pub struct Unfinished(Arc<Mutex<Vec<NoContest>>>);

impl Unfinished {
    /// Takes what was left (empty until the world has stopped).
    pub fn take(&self) -> Vec<NoContest> {
        std::mem::take(&mut *self.0.lock().expect("unfinished duels lock poisoned"))
    }
}

/// Challenges and running duels.
#[derive(Debug, Default)]
pub struct Duels {
    next_challenge: u32,
    next_duel: u32,
    challenges: HashMap<u32, Challenge>,
    /// Both sides of each pending challenge, to its id.
    challenged: HashMap<EntityId, u32>,
    /// Running duels (and finished ones until everyone has returned).
    arenas: HashMap<u32, Arena>,
    /// Arena zone to duel id.
    by_zone: HashMap<ZoneId, u32>,
    /// Duelists and spectators inside an arena: the duel and their role.
    inside: HashMap<EntityId, (u32, DuelRole)>,
    /// Duelists who disconnected mid-duel (character id to duel id).
    away: HashMap<i64, u32>,
    /// Dueling Club lessons by character: in flight (`None`), or no new
    /// check until then.
    teaching: HashMap<i64, Option<Instant>>,
    next_second: Option<Instant>,
    // Scratch buffers kept between ticks.
    order: Vec<u32>,
    events: Vec<DuelEvent>,
    /// Where the running duels go when this is dropped.
    unfinished: Option<Unfinished>,
}

impl Duels {
    /// When these duels are dropped (the world stopped), every
    /// duel still in its countdown or fight goes to `to` as no contest.
    pub fn keep_unfinished_in(&mut self, to: Unfinished) {
        self.unfinished = Some(to);
    }

    /// The duels still in their countdown or fight, as no contest now.
    pub fn unfinished(&self) -> Vec<NoContest> {
        let mut v: Vec<NoContest> = self
            .arenas
            .values()
            .filter(|a| !a.duel.is_over())
            .map(|a| NoContest {
                duel_key: a.key,
                venue: a.venue.clone(),
                characters: [a.sides[0].character_id, a.sides[1].character_id],
                poise: [
                    a.duel.duelist(DuelSide::A).poise,
                    a.duel.duelist(DuelSide::B).poise,
                ],
                fought_ms: a.duel.fought_ms(),
                // On the duel's own clock from its start, so a dev skew of
                // the game clock moves both ends alike.
                started_at: a.started_at,
                ended_at: a.started_at + chrono::Duration::milliseconds(i64::from(a.duel.now_ms())),
            })
            .collect();
        v.sort_by_key(|n| n.duel_key);
        v
    }
}

impl Drop for Duels {
    fn drop(&mut self) {
        // The world thread's `with_duels` swaps in an empty default (no
        // list, no arenas) for a moment: nothing to do for that one.
        if let Some(to) = &self.unfinished
            && !self.arenas.is_empty()
        {
            let left = self.unfinished();
            to.0.lock()
                .expect("unfinished duels lock poisoned")
                .extend(left);
        }
    }
}

fn error(ctx: &mut WorldCtx<'_>, to: EntityId, challenge_id: Option<u32>, code: DuelErrorCode) {
    ctx.send(to, &ServerMsg::DuelError { challenge_id, code });
}

/// The venue whose dueling area `e` stands in.
fn venue_of(ctx: &WorldCtx<'_>, e: EntityId) -> Option<&'static Venue> {
    let area = ctx.area(e, AreaKind::DuelingClub)?;
    shipped().venues.by_area(area)
}

fn in_venue(ctx: &WorldCtx<'_>, e: EntityId, venue: &str) -> bool {
    venue_of(ctx, e).is_some_and(|v| v.id == venue)
}

/// The spells `e` would duel with: their loadout if it still fits what they
/// learned and their level's slots, else the default loadout.
fn loadout_of(ctx: &WorldCtx<'_>, e: EntityId) -> Vec<SpellId> {
    let (Some(school), Some(level)) = (ctx.school(e), ctx.level(e)) else {
        return Vec::new();
    };
    let content = shipped();
    let slots = content.progression.loadout_slots(level);
    let chosen = &school.loadout;
    if !chosen.is_empty()
        && content
            .spells
            .check_loadout(&school.learned, chosen, slots)
            .is_ok()
    {
        chosen.clone()
    } else {
        content.spells.default_loadout(&school.learned, slots)
    }
}

/// A position as the duel sees it: arena x and z, inside the ring.
fn ring_pos(venue: Option<&Venue>, p: [f32; 3]) -> [f32; 2] {
    match venue {
        Some(v) => [
            p[0].clamp(v.ring.min[0], v.ring.max[0]),
            p[2].clamp(v.ring.min[1], v.ring.max[1]),
        ],
        None => [p[0], p[2]],
    }
}

/// Where the `n`th spectator stands: rows of [`SPECTATOR_ROW`] along the
/// stage, alternating between its two sides (the venue's spot, and the
/// same distance across the ring facing the other way), the later rows a
/// step further back. A spot off the arena's ground falls back to its
/// spawns (`enter_instance`).
fn spectator_spot(venue: &Venue, n: usize) -> ([f32; 3], f32) {
    let s = venue.spectators;
    let (row, col) = (n / SPECTATOR_ROW, n % SPECTATOR_ROW);
    let mid_z = (venue.ring.min[1] + venue.ring.max[1]) / 2.0;
    let off = s.pos[2] - mid_z;
    let back = off.signum() * (row / 2) as f32 * SPECTATOR_SPACING_M;
    let (sign, yaw) = if row % 2 == 0 {
        (1.0, s.yaw)
    } else {
        (
            -1.0,
            (s.yaw + std::f32::consts::PI).rem_euclid(std::f32::consts::TAU),
        )
    };
    let x = s.pos[0] + (col as f32 - (SPECTATOR_ROW as f32 - 1.0) / 2.0) * SPECTATOR_SPACING_M;
    ([x, s.pos[1], mid_z + sign * (off + back)], yaw)
}

/// Spells L20: the duel's status effects on the world.
const DUEL_STATUS_EFFECTS: [EffectKind; 3] = [
    EffectKind::Petrified,
    EffectKind::Slowed,
    EffectKind::Guardian,
];

/// Spells L20: takes the duel's status effects off `e` (a duel starting or
/// ending).
fn clear_statuses(ctx: &mut WorldCtx<'_>, e: EntityId) {
    for kind in DUEL_STATUS_EFFECTS {
        ctx.remove_effect(e, kind);
    }
}

/// Spells L20: what the world does for one duel event (contract 4.2).
/// `entity` maps a side to its duelist's entity (when present), `at_ms` is
/// the duel clock of the step, `pull` keeps a `Pulled` until its
/// `Staggered` (they come in that order).
fn world_effects(
    ctx: &mut WorldCtx<'_>,
    entity: [Option<(EntityId, i64)>; 2],
    ev: &DuelEvent,
    at_ms: u32,
    pull: &mut [Option<(ArenaPos, u32)>; 2],
) {
    let now = ctx.now();
    let until =
        |until_ms: u32| now + Duration::from_millis(u64::from(until_ms.saturating_sub(at_ms)));
    let effect = |kind, visible, speed_pct, until| ActiveEffect {
        kind,
        item_def: 0,
        fortune: None,
        visible: Some(visible),
        speed_pct,
        until,
    };
    let of = |side: DuelSide| entity[side.index()];
    match *ev {
        DuelEvent::Rooted { side, until_ms } => {
            if let Some((e, _)) = of(side) {
                let fx = effect(
                    EffectKind::Petrified,
                    VisibleEffect::Bound,
                    0,
                    until(until_ms),
                );
                ctx.apply_effect(e, fx);
            }
        }
        DuelEvent::Slowed {
            side,
            until_ms,
            pct,
        } => {
            if let Some((e, _)) = of(side) {
                let visible = VisibleEffect::Slowed { pct };
                let fx = effect(EffectKind::Slowed, visible, u16::from(pct), until(until_ms));
                ctx.apply_effect(e, fx);
            }
        }
        DuelEvent::Pulled {
            side, to, until_ms, ..
        } => pull[side.index()] = Some((to, until_ms)),
        DuelEvent::Staggered { side, until_ms } => {
            if let (Some((to, end_ms)), Some((e, _))) = (pull[side.index()].take(), of(side)) {
                let y = ctx.position(e).map_or(0.0, |p| p[1]);
                let dest = [f32::from(to[0]) / 100.0, y, f32::from(to[1]) / 100.0];
                let ms = |t: u32| u16::try_from(t.saturating_sub(at_ms)).unwrap_or(u16::MAX);
                let by = of(side.other()).map_or(e, |(o, _)| o);
                ctx.force_move(e, dest, ms(end_ms), ms(until_ms), by);
            }
        }
        DuelEvent::Cleansed { side, .. } => {
            if let Some((e, _)) = of(side) {
                ctx.remove_effect(e, EffectKind::Petrified);
                ctx.remove_effect(e, EffectKind::Slowed);
            }
        }
        DuelEvent::WardUp { side, until_ms, .. } => {
            if let Some((e, cid)) = of(side) {
                let visible = VisibleEffect::Guardian {
                    animal: sim_core::guardian_animal(cid),
                    guarding: true,
                };
                let fx = effect(
                    EffectKind::Guardian,
                    visible,
                    protocol::SPEED_PCT_NORMAL,
                    until(until_ms),
                );
                ctx.apply_effect(e, fx);
            }
        }
        DuelEvent::Warded { by: side, .. } | DuelEvent::WardFaded { side } => {
            if let Some((e, _)) = of(side) {
                ctx.remove_effect(e, EffectKind::Guardian);
            }
        }
        _ => {}
    }
}

/// Spells L20: puts the duel's statuses of `side` back on its duelist `e`
/// (character `cid`) as world effects, for the time each has left. A
/// duelist coming back mid-duel (a new login within the away grace) has a
/// new entity, and entering the arena took every status off it. A pull or
/// a stagger in progress is not restored: the duel holds the position
/// itself and both are over within a second.
fn restore_statuses(ctx: &mut WorldCtx<'_>, duel: &Duel, side: DuelSide, e: EntityId, cid: i64) {
    let now_ms = duel.now_ms();
    let d = duel.duelist(side);
    let mut events = Vec::with_capacity(3);
    if d.rooted(now_ms) {
        events.push(DuelEvent::Rooted {
            side,
            until_ms: d.rooted_until,
        });
    }
    if let Some((until_ms, pct)) = d.slowed.filter(|&(until, _)| until > now_ms) {
        events.push(DuelEvent::Slowed {
            side,
            until_ms,
            pct,
        });
    }
    if let Some(w) = d.ward.filter(|w| w.until_ms > now_ms) {
        events.push(DuelEvent::WardUp {
            side,
            spell: w.spell,
            until_ms: w.until_ms,
        });
    }
    let mut entity = [None; 2];
    entity[side.index()] = Some((e, cid));
    let mut pull = [None; 2];
    for ev in &events {
        world_effects(ctx, entity, ev, now_ms, &mut pull);
    }
}

fn utc(ctx: &WorldCtx<'_>) -> DateTime<Utc> {
    DateTime::<Utc>::from_timestamp_millis(ctx.unix_ms()).unwrap_or_else(Utc::now)
}

fn ms_until(t: Instant, now: Instant) -> u32 {
    u32::try_from(t.saturating_duration_since(now).as_millis()).unwrap_or(u32::MAX)
}

impl Duels {
    /// A dueling message from `who`.
    pub fn on_input(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, input: DuelMsg) {
        match input {
            DuelMsg::Challenge { target } => self.challenge(ctx, who, target),
            DuelMsg::ChallengeName { name } => match ctx.entity_by_name(&name) {
                Some(target) => self.challenge(ctx, who, target),
                None if !ctx.take_action(who) => error(ctx, who, None, DuelErrorCode::RateLimited),
                None => error(ctx, who, None, DuelErrorCode::UnknownTarget),
            },
            DuelMsg::Respond {
                challenge_id,
                accept,
            } => self.respond(ctx, who, challenge_id, accept),
            DuelMsg::Cancel { challenge_id } => self.cancel(ctx, who, challenge_id),
            DuelMsg::Cast { spell } => self.cast(ctx, who, spell),
            DuelMsg::Forfeit => self.forfeit(ctx, who),
            DuelMsg::List => self.list(ctx, who),
            DuelMsg::Spectate { duel_id } => self.spectate(ctx, who, duel_id),
        }
    }

    pub fn on_done(&mut self, ctx: &mut WorldCtx<'_>, done: DuelDone) {
        match done {
            DuelDone::Recorded {
                duel_id,
                zone,
                finished,
                result,
            } => self.recorded(ctx, duel_id, zone, &finished, result),
            DuelDone::Taught {
                character_id,
                result,
            } => self.taught(ctx, character_id, result),
        }
    }

    /// `who` is about to leave the world: its challenge ends; a duelist may
    /// come back within the grace (else they lose by `Disconnect`).
    pub fn on_left(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId) {
        if let Some(&id) = self.challenged.get(&who) {
            self.end(ctx, id, ChallengeEnd::Disconnected);
        }
        let Some((duel_id, role)) = self.inside.remove(&who) else {
            return;
        };
        let Some(arena) = self.arenas.get_mut(&duel_id) else {
            return;
        };
        match role {
            DuelRole::Spectator => arena.spectators.retain(|&s| s != who),
            DuelRole::Duelist(side) => {
                let over = arena.duel.is_over();
                let d = &mut arena.sides[side.index()];
                d.present = false;
                if !over {
                    let grace = shipped().duel_rules.disconnect_grace_ms;
                    d.away_until = Some(ctx.now() + Duration::from_millis(u64::from(grace)));
                    self.away.insert(d.character_id, duel_id);
                }
            }
        }
    }

    /// `who` moved out of zone `from`: a challenge cannot follow it, a
    /// duelist walking out of a running duel forfeits, a spectator stops
    /// watching.
    pub fn on_zone_changed(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, from: ZoneId) {
        if let Some(&id) = self.challenged.get(&who) {
            self.end(ctx, id, ChallengeEnd::WalkedAway);
        }
        let Some(&(duel_id, role)) = self.inside.get(&who) else {
            return;
        };
        let Some(arena) = self.arenas.get_mut(&duel_id) else {
            return;
        };
        if arena.zone != from {
            // Coming in. Spells L20: a duelist back mid-duel gets its
            // statuses again (the zone change took them off just before).
            if let DuelRole::Duelist(side) = role
                && ctx.zone(who) == Some(arena.zone)
                && !arena.duel.is_over()
            {
                let cid = arena.side(side).character_id;
                restore_statuses(ctx, &arena.duel, side, who, cid);
            }
            return;
        }
        self.inside.remove(&who);
        match role {
            DuelRole::Spectator => arena.spectators.retain(|&s| s != who),
            DuelRole::Duelist(side) => {
                arena.sides[side.index()].present = false;
                arena.forfeit(side);
            }
        }
    }

    /// An arena closed: everyone has gone. A duel still running ends here
    /// (whoever walked out loses; if nobody did, both disconnected: a draw)
    /// and is recorded like any other.
    pub fn on_instance_closed(&mut self, ctx: &mut WorldCtx<'_>, zone: ZoneId, kind: FeatureKind) {
        let FeatureKind::Arena { duel_id, .. } = kind else {
            return;
        };
        if self.by_zone.get(&zone) != Some(&duel_id) {
            return;
        }
        self.by_zone.remove(&zone);
        let rules = &shipped().duel_rules;
        if let Some(arena) = self.arenas.get_mut(&duel_id)
            && !arena.duel.is_over()
        {
            let pending = [arena.sides[0].leave.take(), arena.sides[1].leave.take()];
            let leave = if pending.iter().any(Option::is_some) {
                pending
            } else {
                [Some(DuelEndReason::Disconnect); 2]
            };
            let input = [0, 1].map(|i| DuelInput {
                cast: None,
                pos: arena.sides[i].pos,
                leave: leave[i],
            });
            self.events.clear();
            arena
                .duel
                .step(rules, &shipped().spells, &input, &mut self.events);
            self.events.clear();
            self.finish(ctx, duel_id);
        }
        let Some(arena) = self.arenas.remove(&duel_id) else {
            return;
        };
        for e in arena
            .sides
            .iter()
            .map(|d| d.entity)
            .chain(arena.spectators.iter().copied())
        {
            if self.inside.get(&e).is_some_and(|x| x.0 == duel_id) {
                self.inside.remove(&e);
            }
        }
        for d in &arena.sides {
            if self.away.get(&d.character_id) == Some(&duel_id) {
                self.away.remove(&d.character_id);
            }
        }
    }

    /// Every world tick: each running duel steps once; once a second,
    /// challenges and lessons are looked after.
    pub fn tick(&mut self, ctx: &mut WorldCtx<'_>) {
        let now = ctx.now();
        if self.next_second.is_none_or(|t| now >= t) {
            self.next_second = Some(now + Duration::from_secs(1));
            self.each_second(ctx);
        }
        if self.arenas.is_empty() {
            return;
        }
        let mut order = std::mem::take(&mut self.order);
        order.clear();
        order.extend(self.arenas.keys().copied());
        for &id in &order {
            self.step(ctx, id);
        }
        self.order = order;
    }

    /// Spells L20: whether `e` is inside an arena (dueling or watching) or
    /// has a challenge pending (`WorldCtx::dueling`).
    pub fn is_dueling(&self, e: EntityId) -> bool {
        self.inside.contains_key(&e) || self.challenged.contains_key(&e)
    }

    /// Running duels and pending challenges (metrics, tests).
    pub fn open(&self) -> (usize, usize) {
        (self.arenas.len(), self.challenges.len())
    }

    /// The character left mid-duel and may still come back to it (the
    /// disconnect grace): the duel will be recorded for it.
    pub fn is_away(&self, character_id: i64) -> bool {
        self.away.contains_key(&character_id)
    }

    // ---- challenges

    /// Dueling, spectating, away from a duel or in a challenge.
    fn busy(&self, ctx: &WorldCtx<'_>, e: EntityId) -> bool {
        self.challenged.contains_key(&e)
            || self.inside.contains_key(&e)
            || ctx
                .character_id(e)
                .is_some_and(|c| self.away.contains_key(&c))
    }

    fn challenge(&mut self, ctx: &mut WorldCtx<'_>, from: EntityId, target: EntityId) {
        if !ctx.take_action(from) {
            return error(ctx, from, None, DuelErrorCode::RateLimited);
        }
        if target == from {
            return error(ctx, from, None, DuelErrorCode::SelfDuel);
        }
        // A block either way looks like an unknown player (blocks stay private).
        if ctx.character_id(target).is_none() || ctx.blocked_either(from, target) {
            return error(ctx, from, None, DuelErrorCode::UnknownTarget);
        }
        let Some(venue) = venue_of(ctx, from) else {
            return error(ctx, from, None, DuelErrorCode::NotInArea);
        };
        if !in_venue(ctx, target, &venue.id) {
            return error(ctx, from, None, DuelErrorCode::TargetNotInArea);
        }
        // They challenged each other at once: this answers theirs.
        if let Some(&id) = self.challenged.get(&target)
            && self
                .challenges
                .get(&id)
                .is_some_and(|c| c.from == target && c.to == from)
        {
            return self.accept(ctx, from, id);
        }
        if self.busy(ctx, from) || self.busy(ctx, target) {
            return error(ctx, from, None, DuelErrorCode::Busy);
        }
        if loadout_of(ctx, from).is_empty() {
            return error(ctx, from, None, DuelErrorCode::NoSpells);
        }
        let secs = shipped().duel_rules.challenge_secs;
        let id = loop {
            self.next_challenge = self.next_challenge.wrapping_add(1).max(1);
            if !self.challenges.contains_key(&self.next_challenge) {
                break self.next_challenge;
            }
        };
        self.challenges.insert(
            id,
            Challenge {
                from,
                to: target,
                venue: venue.id.clone(),
                expires: ctx.now() + Duration::from_secs(u64::from(secs)),
            },
        );
        self.challenged.insert(from, id);
        self.challenged.insert(target, id);
        let msg = ServerMsg::DuelChallenged {
            challenge_id: id,
            from,
            from_name: ctx.name(from).unwrap_or_default().to_owned(),
            from_level: ctx.level(from).unwrap_or(1),
            expires_in_secs: secs,
        };
        ctx.notice(target, &msg);
        ctx.send(from, &msg);
    }

    fn respond(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, id: u32, accept: bool) {
        let Some(c) = self.challenges.get(&id).filter(|c| c.to == who) else {
            return error(ctx, who, Some(id), DuelErrorCode::UnknownChallenge);
        };
        if c.expires <= ctx.now() {
            self.end(ctx, id, ChallengeEnd::Expired);
            return error(ctx, who, Some(id), DuelErrorCode::Expired);
        }
        if accept {
            self.accept(ctx, who, id);
        } else {
            self.end(ctx, id, ChallengeEnd::Declined);
        }
    }

    fn cancel(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, id: u32) {
        if !self.challenges.get(&id).is_some_and(|c| c.from == who) {
            return error(ctx, who, Some(id), DuelErrorCode::UnknownChallenge);
        }
        self.end(ctx, id, ChallengeEnd::Cancelled);
    }

    /// `who` (the challenged side) accepts challenge `id`: the duel starts
    /// if both may still fight.
    fn accept(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, id: u32) {
        let Some(c) = self.challenges.get(&id) else {
            return;
        };
        let (a, b) = (c.from, c.to);
        let Some(venue) = shipped().venues.get(&c.venue) else {
            return self.end(ctx, id, ChallengeEnd::Cancelled);
        };
        if !in_venue(ctx, a, &venue.id) || !in_venue(ctx, b, &venue.id) {
            return self.end(ctx, id, ChallengeEnd::WalkedAway);
        }
        if ctx.blocked_either(a, b) {
            return self.end(ctx, id, ChallengeEnd::Cancelled);
        }
        let loadouts = [loadout_of(ctx, a), loadout_of(ctx, b)];
        if loadouts[1].is_empty() {
            // The challenge stays: the club may still teach them.
            return error(ctx, who, Some(id), DuelErrorCode::NoSpells);
        }
        if loadouts[0].is_empty() {
            return self.end(ctx, id, ChallengeEnd::Cancelled);
        }
        let running = self.arenas.values().filter(|x| x.venue == venue.id).count();
        if running >= usize::from(venue.max_arenas) {
            return error(ctx, who, Some(id), DuelErrorCode::Full);
        }
        self.end(ctx, id, ChallengeEnd::Started);
        self.start(ctx, venue, [a, b], loadouts);
    }

    /// Ends challenge `id` for both sides. `Started` goes straight out (the
    /// zone change follows it); the rest through the notice backlog.
    fn end(&mut self, ctx: &mut WorldCtx<'_>, id: u32, reason: ChallengeEnd) {
        let Some(c) = self.challenges.remove(&id) else {
            return;
        };
        self.challenged.remove(&c.from);
        self.challenged.remove(&c.to);
        let msg = ServerMsg::DuelChallengeEnded {
            challenge_id: id,
            reason,
        };
        for e in [c.from, c.to] {
            if reason == ChallengeEnd::Started {
                ctx.send(e, &msg);
            } else {
                ctx.notice(e, &msg);
            }
        }
    }

    // ---- arenas

    fn start(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        venue: &Venue,
        who: [EntityId; 2],
        loadouts: [Vec<SpellId>; 2],
    ) {
        let rules = &shipped().duel_rules;
        let duel_id = loop {
            self.next_duel = self.next_duel.wrapping_add(1).max(1);
            if !self.arenas.contains_key(&self.next_duel) {
                break self.next_duel;
            }
        };
        let kind = FeatureKind::Arena {
            duel_id,
            venue: venue.id.clone(),
        };
        let Some(zone) = ctx.open_instance(kind, &venue.arena_layout) else {
            tracing::error!(layout = %venue.arena_layout, "the arena layout is missing");
            for e in who {
                error(ctx, e, None, DuelErrorCode::Failed);
            }
            return;
        };
        let mut entered = true;
        for (i, &e) in who.iter().enumerate() {
            let mark = venue.duelists[i];
            entered &= ctx.enter_instance(e, zone, Some((mark.pos, mark.yaw)));
        }
        if !entered {
            // Both were checked to be in the castle: should not happen.
            ctx.close_instance(zone);
            for e in who {
                error(ctx, e, None, DuelErrorCode::Failed);
            }
            return;
        }
        ctx.set_instance_priority(zone, who.to_vec());
        // Spells L20: nobody brings a bind, slow or guardian into a duel.
        for e in who {
            clear_statuses(ctx, e);
        }
        let [la, lb] = loadouts;
        let sides = [(DuelSide::A, la), (DuelSide::B, lb)].map(|(side, loadout)| {
            let e = who[side.index()];
            let mark = venue.duelists[side.index()].pos;
            Duelist {
                entity: e,
                character_id: ctx.character_id(e).unwrap_or_default(),
                info: DuelistInfo {
                    entity: e,
                    name: ctx.name(e).unwrap_or_default().to_owned(),
                    house: ctx.house(e).unwrap_or(protocol::House::Roarwing),
                    level: ctx.level(e).unwrap_or(1),
                    loadout,
                },
                cast: None,
                leave: None,
                pos: [mark[0], mark[2]],
                present: true,
                away_until: None,
            }
        });
        let duel = Duel::new(
            rules,
            [sides[0].info.loadout.clone(), sides[1].info.loadout.clone()],
            [sides[0].pos, sides[1].pos],
        );
        for (side, &e) in DuelSide::BOTH.iter().zip(&who) {
            self.inside.insert(e, (duel_id, DuelRole::Duelist(*side)));
        }
        self.by_zone.insert(zone, duel_id);
        self.arenas.insert(
            duel_id,
            Arena {
                zone,
                // Positive and non-zero, unique per duel (`duels.duel_key`).
                key: (rand::random::<i64>() & i64::MAX).max(1),
                venue: venue.id.clone(),
                duel,
                sides,
                spectators: Vec::new(),
                started_at: utc(ctx),
                return_at: None,
            },
        );
        self.send_start(ctx, duel_id, &who);
    }

    /// `DuelStart` (where the duel stands now) to each of `to` inside it.
    fn send_start(&self, ctx: &mut WorldCtx<'_>, duel_id: u32, to: &[EntityId]) {
        let Some(arena) = self.arenas.get(&duel_id) else {
            return;
        };
        let rules = &shipped().duel_rules;
        let state = arena.duel.snapshot();
        for &e in to {
            let Some(&(id, you)) = self.inside.get(&e) else {
                continue;
            };
            if id != duel_id {
                continue;
            }
            let msg = ServerMsg::DuelStart {
                duel_id,
                you,
                a: arena.side(DuelSide::A).info.clone(),
                b: arena.side(DuelSide::B).info.clone(),
                poise_max: rules.poise_max,
                time_limit_ms: rules.time_limit_ms,
                state: state.clone(),
            };
            ctx.send(e, &msg);
        }
    }

    fn cast(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, spell: SpellId) {
        if let Some(&(id, DuelRole::Duelist(side))) = self.inside.get(&who)
            && let Some(arena) = self.arenas.get_mut(&id)
        {
            // Refusals (cooldown, disarmed, not in the loadout, over) come
            // from the step, as `Refused`.
            arena.sides[side.index()].cast = Some(spell);
        } else if ctx.take_action(who) {
            // Spectators (and everyone else) cast nothing.
            error(ctx, who, None, DuelErrorCode::UnknownDuel);
        }
    }

    fn forfeit(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId) {
        if let Some(&(id, DuelRole::Duelist(side))) = self.inside.get(&who)
            && let Some(arena) = self.arenas.get_mut(&id)
            && !arena.duel.is_over()
        {
            arena.forfeit(side);
        } else if ctx.take_action(who) {
            error(ctx, who, None, DuelErrorCode::UnknownDuel);
        }
    }

    fn list(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId) {
        if !ctx.take_action(who) {
            return error(ctx, who, None, DuelErrorCode::RateLimited);
        }
        let Some(venue) = venue_of(ctx, who) else {
            return error(ctx, who, None, DuelErrorCode::NotInArea);
        };
        let mut duels: Vec<DuelSummary> = self
            .arenas
            .iter()
            .filter(|(_, a)| a.venue == venue.id && !a.duel.is_over())
            .map(|(&duel_id, a)| {
                let (x, y) = (&a.side(DuelSide::A).info, &a.side(DuelSide::B).info);
                DuelSummary {
                    duel_id,
                    a_name: x.name.clone(),
                    a_house: x.house,
                    b_name: y.name.clone(),
                    b_house: y.house,
                    spectators: u16::try_from(a.spectators.len()).unwrap_or(u16::MAX),
                    elapsed_ms: a.duel.now_ms(),
                }
            })
            .collect();
        duels.sort_by_key(|d| d.duel_id);
        ctx.send(who, &ServerMsg::DuelList { duels });
    }

    fn spectate(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, duel_id: u32) {
        if !ctx.take_action(who) {
            return error(ctx, who, None, DuelErrorCode::RateLimited);
        }
        let Some(venue) = venue_of(ctx, who) else {
            return error(ctx, who, None, DuelErrorCode::NotInArea);
        };
        if self.busy(ctx, who) {
            return error(ctx, who, None, DuelErrorCode::Busy);
        }
        let Some(arena) = self
            .arenas
            .get_mut(&duel_id)
            .filter(|a| a.venue == venue.id && !a.duel.is_over())
        else {
            return error(ctx, who, None, DuelErrorCode::UnknownDuel);
        };
        if arena.spectators.len() >= usize::from(venue.max_spectators) {
            return error(ctx, who, None, DuelErrorCode::Full);
        }
        let spot = spectator_spot(venue, arena.spectators.len());
        if !ctx.enter_instance(who, arena.zone, Some(spot)) {
            return error(ctx, who, None, DuelErrorCode::Failed);
        }
        arena.spectators.push(who);
        self.inside.insert(who, (duel_id, DuelRole::Spectator));
        self.send_start(ctx, duel_id, &[who]);
    }

    /// One tick of duel `id`: reconnects, positions and casts in, the step,
    /// its events out; the end, or the return to the club after it.
    fn step(&mut self, ctx: &mut WorldCtx<'_>, id: u32) {
        let content = shipped();
        let now = ctx.now();
        let Some(arena) = self.arenas.get_mut(&id) else {
            return;
        };
        if arena.duel.is_over() {
            if arena.return_at.is_some_and(|t| now >= t) {
                arena.return_at = None;
                ctx.close_instance(arena.zone);
            }
            return;
        }
        let venue = content.venues.get(&arena.venue);
        let mut rejoined = false;
        for side in DuelSide::BOTH {
            let d = &mut arena.sides[side.index()];
            if let Some(until) = d.away_until {
                // A new login lands in the castle: straight back in.
                if let Some(e) = ctx.entity_of(d.character_id)
                    && ctx.zone(e) == Some(CASTLE)
                {
                    let (y, yaw) = venue.map_or((0.0, 0.0), |v| {
                        let mark = v.duelists[side.index()];
                        (mark.pos[1], mark.yaw)
                    });
                    let spot = ([d.pos[0], y, d.pos[1]], yaw);
                    if ctx.enter_instance(e, arena.zone, Some(spot)) {
                        d.entity = e;
                        d.info.entity = e;
                        d.present = true;
                        d.away_until = None;
                        self.away.remove(&d.character_id);
                        self.inside.insert(e, (id, DuelRole::Duelist(side)));
                        rejoined = true;
                        continue;
                    }
                }
                if now >= until {
                    d.leave = d.leave.or(Some(DuelEndReason::Disconnect));
                }
            } else if d.present
                && let Some(p) = ctx.position(d.entity)
            {
                d.pos = ring_pos(venue, p);
            }
        }
        let input = [0, 1].map(|i| {
            let d = &mut arena.sides[i];
            DuelInput {
                cast: d.cast.take().filter(|_| d.present),
                pos: d.pos,
                leave: d.leave.take(),
            }
        });
        let mut events = std::mem::take(&mut self.events);
        events.clear();
        arena
            .duel
            .step(&content.duel_rules, &content.spells, &input, &mut events);
        if !events.is_empty() {
            let at_ms = arena.duel.now_ms();
            let mut shared = Vec::with_capacity(events.len());
            // Spells L20: the statuses' world effects.
            let entity = [0, 1].map(|k| {
                let d = &arena.sides[k];
                d.present.then_some((d.entity, d.character_id))
            });
            let mut pull = [None; 2];
            for ev in events.drain(..) {
                world_effects(ctx, entity, &ev, at_ms, &mut pull);
                match ev {
                    // A refusal is the caster's business only.
                    DuelEvent::Refused { side, .. } => {
                        let d = arena.side(side);
                        if d.present {
                            let msg = ServerMsg::DuelEvents {
                                duel_id: id,
                                at_ms,
                                events: vec![ev],
                            };
                            ctx.send(d.entity, &msg);
                        }
                    }
                    DuelEvent::CastStarted { side, .. } => {
                        let d = arena.side(side);
                        if d.present {
                            ctx.play_cast(d.entity);
                        }
                        shared.push(ev);
                    }
                    _ => shared.push(ev),
                }
            }
            if !shared.is_empty() {
                let msg = ServerMsg::DuelEvents {
                    duel_id: id,
                    at_ms,
                    events: shared,
                };
                ctx.broadcast(arena.zone, &msg);
            }
        }
        self.events = events;
        let (zone, over) = (arena.zone, arena.duel.is_over());
        let duelists = [arena.sides[0].entity, arena.sides[1].entity];
        if rejoined {
            // A new entity for a duelist: everyone starts from a fresh view.
            ctx.set_instance_priority(zone, duelists.to_vec());
            let members = ctx
                .instance(zone)
                .map(|f| f.members.clone())
                .unwrap_or_default();
            self.send_start(ctx, id, &members);
        }
        if over {
            self.finish(ctx, id);
        }
    }

    /// Duel `id` just ended: the record job, and the return in a moment.
    fn finish(&mut self, ctx: &mut WorldCtx<'_>, id: u32) {
        let rules = &shipped().duel_rules;
        let Some(arena) = self.arenas.get_mut(&id) else {
            return;
        };
        let Some((winner, reason)) = arena.duel.result() else {
            return;
        };
        arena.return_at = Some(ctx.now() + Duration::from_millis(u64::from(rules.return_delay_ms)));
        // Spells L20: nobody keeps a bind, slow or ward after the duel.
        for d in arena.sides.iter().filter(|d| d.present) {
            clear_statuses(ctx, d.entity);
        }
        for d in &mut arena.sides {
            // Nobody needs to come back to a finished duel.
            if d.away_until.take().is_some() {
                self.away.remove(&d.character_id);
            }
        }
        let finished = Finished {
            duel_key: arena.key,
            venue: arena.venue.clone(),
            characters: [arena.sides[0].character_id, arena.sides[1].character_id],
            winner,
            reason,
            poise: [
                arena.duel.duelist(DuelSide::A).poise,
                arena.duel.duelist(DuelSide::B).poise,
            ],
            fought_ms: arena.duel.fought_ms(),
            started_at: arena.started_at,
            ended_at: utc(ctx),
        };
        let zone = arena.zone;
        ctx.spawn(Box::new(move |pool: PgPool| {
            Box::pin(async move {
                let result = super::record_with_retry(&pool, &finished)
                    .await
                    .map_err(|e| {
                        tracing::error!(error = %e, duel_key = finished.duel_key, "recording a duel failed");
                        // RowNotFound: a duellist was deleted meanwhile.
                        if !matches!(e, sqlx::Error::RowNotFound) {
                            goodsorcery::capture_error(&e);
                        }
                        e.to_string()
                    });
                ExtDone::Duel(DuelDone::Recorded {
                    duel_id: id,
                    zone,
                    finished,
                    result,
                })
            })
        }));
    }

    /// A duel's record is in: `DuelResult` to everyone still watching and to
    /// both duelists wherever they are, then their `Progress`.
    fn recorded(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        duel_id: u32,
        zone: ZoneId,
        f: &Finished,
        result: Result<Recorded, String>,
    ) {
        let (rewards, grants) = match result {
            Ok(r) => (Some(r.rewards), r.grants),
            Err(_) => (None, [None; 2]),
        };
        let now = ctx.now();
        // Duel ids and zones are never reused while live: together they
        // name this duel's arena, if it is still open.
        let arena = self.arenas.get(&duel_id).filter(|a| a.zone == zone);
        let return_in_ms = arena
            .and_then(|a| a.return_at)
            .map_or(0, |t| ms_until(t, now));
        let msg = |rewards| ServerMsg::DuelResult {
            duel_id,
            winner: f.winner,
            reason: f.reason,
            poise: f.poise,
            rewards,
            return_in_ms,
        };
        if let Some(a) = arena {
            let watching = a.spectators.clone();
            for e in watching {
                ctx.send(e, &msg(None));
            }
        }
        for side in DuelSide::BOTH {
            let i = side.index();
            let Some(e) = ctx.entity_of(f.characters[i]) else {
                continue;
            };
            ctx.send(e, &msg(rewards.map(|r| r[i])));
            if let Some(g) = grants[i].filter(|g| !g.duplicate) {
                ctx.progress_changed(e, g.xp, Some(g.gain()));
            }
        }
    }

    // ---- the Dueling Club's lessons

    /// Once a second: challenges expire or end when a side walked out of
    /// the area; whoever stands in a venue's area learns what it teaches.
    fn each_second(&mut self, ctx: &mut WorldCtx<'_>) {
        let now = ctx.now();
        let ended: Vec<(u32, ChallengeEnd)> = self
            .challenges
            .iter()
            .filter_map(|(&id, c)| {
                if c.expires <= now {
                    Some((id, ChallengeEnd::Expired))
                } else if !in_venue(ctx, c.from, &c.venue) || !in_venue(ctx, c.to, &c.venue) {
                    Some((id, ChallengeEnd::WalkedAway))
                } else {
                    None
                }
            })
            .collect();
        for (id, reason) in ended {
            self.end(ctx, id, reason);
        }

        self.teaching.retain(|_, t| t.is_none_or(|t| t > now));
        let spells = &shipped().spells;
        for e in ctx.players_in_areas(AreaKind::DuelingClub) {
            let (Some(cid), Some(level), Some(venue), Some(school)) = (
                ctx.character_id(e),
                ctx.level(e),
                venue_of(ctx, e),
                ctx.school(e),
            ) else {
                continue;
            };
            if self.teaching.contains_key(&cid)
                || spells
                    .taught_at(&venue.id, level)
                    .all(|s| school.learned.contains(&s.id))
            {
                continue;
            }
            self.teaching.insert(cid, None);
            let venue = venue.id.clone();
            ctx.spawn(Box::new(move |pool: PgPool| {
                Box::pin(async move {
                    let result = super::teach(&pool, cid, &venue, level)
                        .await
                        .map_err(|e| {
                            tracing::warn!(error = %e, character_id = cid, "a Dueling Club lesson failed");
                            e.to_string()
                        });
                    ExtDone::Duel(DuelDone::Taught {
                        character_id: cid,
                        result,
                    })
                })
            }));
        }
    }

    fn taught(&mut self, ctx: &mut WorldCtx<'_>, cid: i64, result: Result<Taught, String>) {
        let wait = match result {
            Ok(t) => {
                if let Some(e) = ctx.entity_of(cid)
                    && (!t.new.is_empty() || ctx.school(e).is_some_and(|s| s.learned != t.learned))
                {
                    ctx.set_spellbook(e, t.learned, t.loadout, t.new);
                }
                TEACH_COOLDOWN
            }
            Err(_) => TEACH_RETRY,
        };
        self.teaching.insert(cid, Some(ctx.now() + wait));
    }
}

#[cfg(test)]
#[path = "world_tests.rs"]
mod tests;
