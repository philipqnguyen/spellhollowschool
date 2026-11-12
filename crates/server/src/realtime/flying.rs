//! Brooms in the world (docs/architecture.md, "Flying").
//!
//! A character is walking or mounted (`Mover::mount`); the server's own
//! state picks the check a `Move` goes through, never the client's anim:
//! walking moves through `sim_core::check_move` (`world.rs`), flight moves
//! through `sim_core::flight::check_flight` here, with the character's
//! broom tier (its Flying skill level, cached at join in
//! `Mover::flying_xp`) and broom speed (`Mover::broom_pct`: Featherlight
//! Elixir). A rejected flight move corrects like a walking one, and the
//! rider stays mounted unless the position it would go back to is no
//! longer open sky (then it lands, on the ground below or a spawn).
//!
//! `ClientMsg::Mount { on: true }` mounts where the character stands
//! ([`WorldSim::on_mount`]): it needs the Flying skill (any XP; skipped in a
//! Flying class, which mounts its students itself), not to be busy (sitting,
//! at a station, in a trade window, in a class that does not fly or a duel)
//! and open sky at the spot
//! (`sim_core::flight::mount`). `on: false` lands within `dismount_height`
//! of walkable ground. Every request is answered with `ServerMsg::Mounted`;
//! the server also sends it when it changes the state itself (a zone change
//! lands the rider; a Flying class mounts them; a correction that lands).
//!
//! Rejected moves just after a mount change are dropped silently, as after
//! a zone change: the client sent them before it heard.

use std::sync::Arc;
use std::time::{Duration, Instant};

use protocol::{
    Anim, BROOM_PCT_NORMAL, EffectKind, EntityState, MountRefused, QPos, ServerMsg, encode_server,
    yaw_to_u8,
};
use sim_core::flight::{self, FlightCheck, FlightReject};
use sim_core::{Airspace, FlightRules};

use super::entities::{CASTLE, Mount};
use super::ext::ActiveEffect;
use super::instances::FeatureKind;
use super::metrics::OFF_GROUND;
use super::world::{
    CORRECTION_COOLDOWN, MOVE_CREDIT_MAX, SPEED_DROP_GRACE, WorldSim, ZONE_CHANGE_GRACE,
};

/// After the mount state changes, rejected moves within this window are
/// dropped silently: they were sent before the client heard.
pub const MOUNT_CHANGE_GRACE: Duration = Duration::from_millis(500);

/// Index into `RealtimeMetrics::corrections` of a rejected flight move.
pub fn flight_correction_index(reason: FlightReject) -> usize {
    match reason {
        FlightReject::TooFast => 4,
        FlightReject::OutOfAirspace => 5,
        FlightReject::NoFly => 6,
        FlightReject::BelowGround => 7,
    }
}

/// The shipped flight rules (`data/flying.json`).
pub fn rules() -> &'static FlightRules {
    &crate::catalog::shipped().flight
}

/// The broom speed (percent) a set of active effects gives in a zone that
/// allows boosts: Featherlight's, else normal.
pub fn effective_broom_pct(effects: &[ActiveEffect]) -> u16 {
    if effects.iter().any(|e| e.kind == EffectKind::Featherlight) {
        crate::catalog::shipped()
            .economy
            .effects
            .featherlight
            .broom_pct
            .clamp(BROOM_PCT_NORMAL, protocol::BROOM_PCT_MAX)
    } else {
        BROOM_PCT_NORMAL
    }
}

/// The broom tier of a character with `flying_xp` Flying skill XP.
pub fn tier_for_xp(flying_xp: u32) -> u8 {
    rules().tier_for_level(crate::skills::level_of(flying_xp))
}

impl WorldSim {
    /// The open sky of the zone character `i` is in: the grounds' for the
    /// castle, a Flying class's field, `None` elsewhere (dorms, indoor rooms).
    pub(super) fn air_of(&self, i: usize) -> Option<Arc<Airspace>> {
        let air = match self.ents.zone[i] {
            CASTLE => self.castle_air.clone(),
            z => {
                let f = self.instances.feature(z)?;
                self.instance_layouts.air(&f.layout)?.clone()
            }
        };
        (!air.is_empty()).then_some(air)
    }

    /// Whether `zone` is a section of a class that flies (a Flying class).
    pub(super) fn is_flying_class(&self, zone: super::entities::ZoneId) -> bool {
        match self.instances.feature(zone).map(|f| &f.kind) {
            Some(FeatureKind::Class { class, .. }) => crate::catalog::shipped()
                .class(class)
                .is_some_and(|c| c.has_rings()),
            _ => false,
        }
    }

    /// Character `i`'s broom tier (by its cached Flying skill).
    pub(super) fn broom_tier(&self, i: usize) -> u8 {
        tier_for_xp(self.ents.mover[i].flying_xp)
    }

    /// The broom speed (percent) character `i`'s flight moves are checked
    /// with now (the old one for a moment after it dropped).
    pub(super) fn broom_pct_at(&mut self, i: usize, at: Instant) -> u16 {
        let m = &mut self.ents.mover[i];
        match m.broom_grace {
            Some((pct, until)) if at < until => pct.max(m.broom_pct),
            Some(_) => {
                m.broom_grace = None;
                m.broom_pct
            }
            None => m.broom_pct,
        }
    }

    /// Sets character `i`'s broom speed after its effects or zone changed.
    pub(super) fn set_broom_pct(&mut self, i: usize, pct: u16, now: Instant) {
        let m = &mut self.ents.mover[i];
        if pct < m.broom_pct {
            m.broom_grace = Some((m.broom_pct, now + SPEED_DROP_GRACE));
        }
        m.broom_pct = pct;
    }

    /// Whether character `i` is too busy to mount: sitting, being moved by
    /// a spell (an Adveni slide or its hold), bound (Rigora), or at a
    /// station still in reach (a class or an arena is checked by the
    /// caller).
    fn mount_busy(&self, i: usize, now: Instant) -> bool {
        if self.ents.state[i].anim == Anim::Sit || self.ents.mover[i].forced.is_some() {
            return true;
        }
        if self.ents.effects[i]
            .active
            .iter()
            .any(|e| e.kind == EffectKind::Petrified)
        {
            return true;
        }
        let cid = self.ents.info[i].character_id;
        self.station_grants
            .keys()
            .filter(|(c, _)| *c == cid)
            .any(|&(_, kind)| self.station_access(cid, kind, now).is_some())
    }

    fn send_mounted(&mut self, i: usize, reason: Option<MountRefused>) {
        let (on, tier) = match self.ents.mover[i].mount {
            Some(m) => (true, m.tier),
            None => (false, 0),
        };
        self.send_msg(i, &ServerMsg::Mounted { on, tier, reason });
    }

    /// Puts character `i` at `pos` (its state too), `anim` showing.
    fn place(&mut self, i: usize, pos: [f32; 3], anim: Anim) {
        self.ents.pos[i] = pos;
        let state = &mut self.ents.state[i];
        state.pos = QPos::from_m(pos);
        state.anim = anim;
        let m = &mut self.ents.mover[i];
        m.emote_until = None;
        m.unsaved = true;
    }

    /// Mounts character `i` at tier `tier` where it stands, in its zone's
    /// airspace, and says so (`Mounted`). The refusal otherwise (nothing
    /// sent).
    fn mount_here(&mut self, i: usize, tier: u8, at: Instant) -> Result<(), MountRefused> {
        let air = self.air_of(i).ok_or(MountRefused::NoAirspace)?;
        let world = self.world_of(i).clone();
        let pct = self.broom_pct_at(i, at);
        let params = rules().params(tier, pct);
        let lifted = flight::mount(&world, &air, &params, self.ents.pos[i])?;
        self.place(i, lifted, Anim::Hover);
        let m = &mut self.ents.mover[i];
        m.mount = Some(Mount { tier });
        m.mount_changed_at = Some(at);
        m.moved_at = Some(at);
        self.metrics.mounts.inc();
        self.send_mounted(i, None);
        Ok(())
    }

    /// `ClientMsg::Mount`: mount where the character stands, or land.
    pub(super) fn on_mount(&mut self, i: usize, at: Instant, on: bool) {
        let m = &mut self.ents.mover[i];
        if !m.mounts.take(
            at,
            super::limits::EMOTE_BURST,
            super::limits::EMOTE_PER_SECOND,
        ) {
            return self.send_mounted(i, Some(MountRefused::RateLimited));
        }
        match (on, m.mount) {
            (true, Some(_)) | (false, None) => self.send_mounted(i, None),
            (true, None) => {
                if let Err(reason) = self.try_mount(i, at) {
                    self.metrics.mount_refusals.inc();
                    self.send_mounted(i, Some(reason));
                }
            }
            (false, Some(_)) => {
                let world = self.world_of(i).clone();
                match flight::dismount(&world, rules(), self.ents.pos[i]) {
                    Ok(p) => {
                        self.land_at(i, p, at);
                        self.send_mounted(i, None);
                    }
                    Err(reason) => {
                        self.metrics.mount_refusals.inc();
                        self.send_mounted(i, Some(reason));
                    }
                }
            }
        }
    }

    /// The checks of a mount request, then the mount.
    fn try_mount(&mut self, i: usize, at: Instant) -> Result<(), MountRefused> {
        let zone = self.ents.zone[i];
        let flying_class = self.is_flying_class(zone);
        if zone != CASTLE && self.instances.feature(zone).is_some() && !flying_class {
            // A class that does not fly, or a duel.
            return Err(MountRefused::Busy);
        }
        if !flying_class && self.ents.mover[i].flying_xp == 0 {
            return Err(MountRefused::NoSkill);
        }
        if self.mount_busy(i, at) || self.trades.is_trading(self.ents.ids[i]) {
            return Err(MountRefused::Busy);
        }
        let tier = self.broom_tier(i);
        self.mount_here(i, tier, at)
    }

    /// A Flying class mounts its student `i` (no skill needed). False when
    /// the room has no open sky at the spot (nothing sent).
    pub(super) fn mount_for_class(&mut self, i: usize, at: Instant) -> bool {
        if self.ents.mover[i].mount.is_some() {
            return true;
        }
        let tier = self.broom_tier(i);
        self.mount_here(i, tier, at).is_ok()
    }

    /// Stands rider `i` at `p` (walkable ground) unmounted (no message).
    fn land_at(&mut self, i: usize, p: [f32; 3], at: Instant) {
        let level = self.world_of(i).level_at(p[0], p[2], p[1]);
        self.place(i, p, Anim::Idle);
        if let Some(level) = level {
            self.ents.level[i] = level;
        }
        let m = &mut self.ents.mover[i];
        m.mount = None;
        m.mount_changed_at = Some(at);
    }

    /// Takes rider `i` off its broom where it is (a zone change follows,
    /// which places it): `Mounted { on: false }` first.
    pub(super) fn unmount_for_zone_change(&mut self, i: usize, at: Instant) {
        let m = &mut self.ents.mover[i];
        if m.mount.take().is_some() {
            m.mount_changed_at = Some(at);
            self.send_mounted(i, None);
        }
    }

    /// Where rider `i` stands when it leaves the air without landing (a
    /// logout, a save, a new connection): the ground below, or the zone's
    /// first spawn (over the water). Walkers stand where they are.
    pub(super) fn standing_pos(&self, i: usize) -> [f32; 3] {
        let pos = self.ents.pos[i];
        if self.ents.mover[i].mount.is_none() {
            return pos;
        }
        let world = self.world_of(i);
        flight::landing_spot(world, pos)
            .or_else(|| world.spawns().first().map(|s| s.pos))
            .unwrap_or(pos)
    }

    /// A `Move` of rider `i` (mounted at `mount`), the sequence already
    /// checked: `check_flight` against its zone's airspace.
    pub(super) fn on_flight_move(
        &mut self,
        i: usize,
        at: Instant,
        seq: u32,
        pos: [f32; 3],
        yaw: f32,
        anim: Anim,
    ) {
        let Some(mount) = self.ents.mover[i].mount else {
            return;
        };
        let world = self.world_of(i).clone();
        let Some(air) = self.air_of(i) else {
            // Mounted where no broom may fly (cannot happen: zone changes
            // land the rider); land it where it is.
            let p = self.standing_pos(i);
            self.land_at(i, p, at);
            self.send_mounted(i, None);
            return self.correct(i, at, seq, p, OFF_GROUND, false);
        };
        let pct = self.broom_pct_at(i, at);
        let params = rules().params(mount.tier, pct);
        let m = &mut self.ents.mover[i];
        let earned = at.saturating_duration_since(m.credit_at).as_secs_f32();
        m.credit = (m.credit + earned).min(MOVE_CREDIT_MAX);
        m.credit_at = m.credit_at.max(at);
        let prev = self.ents.pos[i];
        match flight::check_flight(&world, &air, &params, prev, pos, m.credit) {
            FlightCheck::Accept { pos } => {
                let (dx, dy, dz) = (pos[0] - prev[0], pos[1] - prev[1], pos[2] - prev[2]);
                let used_h = (dx * dx + dz * dz).sqrt() / (params.speed * params.tolerance);
                let used_v = dy.abs() / (params.max_vertical_speed() * params.tolerance);
                m.credit = (m.credit - used_h.max(used_v)).max(0.0);
                let moved_at = m.moved_at.replace(at).unwrap_or(at);
                self.ents.pos[i] = pos;
                let old = self.ents.state[i];
                let qpos = QPos::from_m(pos);
                let anim = match anim {
                    Anim::Fly | Anim::Hover if qpos != old.pos => anim,
                    _ if qpos != old.pos => Anim::Fly,
                    _ => Anim::Hover,
                };
                let m = &mut self.ents.mover[i];
                m.emote_until = None;
                if qpos != old.pos {
                    m.unsaved = true;
                }
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
                // A Flying class scores ring crossings from checked moves.
                if self.ents.zone[i] != CASTLE {
                    let id = self.ents.ids[i];
                    let (a_ms, b_ms) = (self.clock.unix_ms_at(moved_at), self.clock.unix_ms_at(at));
                    self.with_classes(at, |c, ctx| c.on_flight(ctx, id, prev, pos, a_ms, b_ms));
                }
            }
            FlightCheck::Reject(reason) => {
                if m.zone_entered_at
                    .is_some_and(|t| at.saturating_duration_since(t) < ZONE_CHANGE_GRACE)
                    || m.mount_changed_at
                        .is_some_and(|t| at.saturating_duration_since(t) < MOUNT_CHANGE_GRACE)
                {
                    // Sent before the client heard of the change.
                    return;
                }
                if air.check_point(&world, &params, prev).is_ok() {
                    self.correct(i, at, seq, prev, flight_correction_index(reason), true);
                } else {
                    // Where it would go back to is no longer open sky: land.
                    let p = self.standing_pos(i);
                    self.land_at(i, p, at);
                    self.send_mounted(i, None);
                    self.correct(i, at, seq, p, OFF_GROUND, false);
                }
            }
        }
    }

    /// Counts a correction of kind `kind` and sends it (back to `back`),
    /// unless `cooling` applies and another went out a moment ago.
    pub(super) fn correct(
        &mut self,
        i: usize,
        at: Instant,
        seq: u32,
        back: [f32; 3],
        kind: usize,
        may_cool: bool,
    ) {
        self.metrics.corrections[kind].inc();
        let m = &mut self.ents.mover[i];
        let cooling = m
            .last_correction
            .is_some_and(|t| at.saturating_duration_since(t) < CORRECTION_COOLDOWN);
        if may_cool && cooling {
            return;
        }
        m.last_correction = Some(at);
        let msg = ServerMsg::Correction {
            seq,
            pos: QPos::from_m(back),
        };
        self.send(i, encode_server(&msg).into());
    }
}
