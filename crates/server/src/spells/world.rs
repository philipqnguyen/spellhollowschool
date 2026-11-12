//! Spells cast outside duels, on the world thread (classes
//! builder): Lucerna, Elevara and flourishes (see
//! [`crate::spells`]). Everything here is memory: cooldowns per character
//! and spell, the furniture floating now (so a piece cannot be lifted
//! again mid-float) and who was hindered a moment ago. Nothing is written
//! to the database.
//!
//! Spells to level 20 (protocol v11; SOCIAL): one arm per `Social`
//! variant. Tinctura recolours an ally's robe (or your own), Rigora
//! and Tardara bind and slow an ally (`EffectKind::{Petrified,
//! Slowed}`, which the world turns into speed), Adveni slides an ally
//! toward the caster (`WorldCtx::force_move`), Dissolvo ends those and a lit
//! wand (or lands a floating piece), Aperio shows its caster the passages
//! and find spots near, Volucra is only its cast. Spells aimed at someone
//! else need an ally (a friend, party member or club member:
//! `WorldCtx::spell_ally`); the hindering three need a free target that was not
//! hindered in the last `immune_ms`; a bound caster may cast only Dissolvo.
//! Custodio goes to [`super::guardian`] (GUARDIAN). See
//! docs/architecture.md, "Spells and dueling".

use std::collections::HashMap;
use std::time::{Duration, Instant};

use protocol::{
    EffectKind, EntityId, SPEED_PCT_NORMAL, ServerMsg, SpellErrorCode, SpellId, SpellTarget,
    VisibleEffect,
};
use sim_core::spells::{Social, SpellDef};

use crate::catalog::shipped;
use crate::realtime::entities::{CASTLE, ZoneId};
use crate::realtime::ext::{ActiveEffect, SpellInput, WorldCtx};

/// Cooldowns and floating furniture (the handler's state).
#[derive(Debug, Default)]
pub struct Spells {
    /// When each (caster, spell) may be cast again.
    ready_at: HashMap<(EntityId, SpellId), Instant>,
    /// Furniture floating now, by (dorm zone, item), until when.
    floating: HashMap<(ZoneId, i64), Instant>,
    /// Spells L20: characters a hindering spell (bind, slow, pull) started
    /// on, immune to the next until when.
    hindered_until: HashMap<EntityId, Instant>,
}

/// The result of a spells database job (`ExtDone::Spell`). Social casts
/// need none; the type exists for the seam.
#[derive(Debug)]
pub enum SpellDone {}

fn refuse(ctx: &mut WorldCtx<'_>, who: EntityId, spell: SpellId, code: SpellErrorCode) {
    ctx.send(who, &ServerMsg::SpellError { spell, code });
}

impl Spells {
    /// `CastSpell` / `EndSpell` (the world already refused casts in arenas).
    pub fn on_input(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, input: SpellInput) {
        match input {
            SpellInput::Cast { spell, target } => self.cast(ctx, who, spell, target),
            // Only a light lasts until ended.
            SpellInput::End { spell } => match shipped().spells.get(spell).map(|s| &s.social) {
                Some(Social::Light { .. }) => ctx.set_lucerna(who, false),
                // Spells L20: the Guardian is dismissed (GUARDIAN).
                Some(Social::Guardian { .. }) => super::guardian::end(ctx, who),
                _ => {}
            },
        }
    }

    fn cast(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, spell: SpellId, target: SpellTarget) {
        if ctx.is_npc(who) {
            return;
        }
        let learned = ctx.school(who).is_some_and(|s| s.learned.contains(&spell));
        let Some(def) = shipped().spells.get(spell).filter(|_| learned) else {
            return refuse(ctx, who, spell, SpellErrorCode::NotLearned);
        };
        if !ctx.take_action(who) {
            return refuse(ctx, who, spell, SpellErrorCode::RateLimited);
        }
        let now = ctx.now();
        if self.ready_at.get(&(who, spell)).is_some_and(|&t| t > now) {
            return refuse(ctx, who, spell, SpellErrorCode::Cooldown);
        }
        // Bound stiff: only Dissolvo (to free yourself).
        if ctx.has_effect(who, EffectKind::Petrified)
            && !matches!(def.social, Social::Dispel { .. })
        {
            return refuse(ctx, who, spell, SpellErrorCode::Busy);
        }
        let done = match &def.social {
            Social::Flourish { range_m, .. } => flourish(ctx, who, def, target, *range_m),
            Social::Light { radius_m, .. } => {
                ctx.set_lucerna(who, true);
                cast_near(ctx, who, def.id, SpellTarget::None, *radius_m);
                Ok(())
            }
            Social::Levitate {
                range_m,
                duration_ms,
                ..
            } => self.levitate(ctx, who, def, target, *range_m, *duration_ms),
            // Spells to level 20.
            Social::Recolour { .. } => self.recolour(ctx, who, def, target),
            Social::Bind { .. } => self.bind(ctx, who, def, target),
            Social::Birds { .. } => self.birds(ctx, who, def, target),
            Social::Pull { .. } => self.pull(ctx, who, def, target),
            Social::Dispel { .. } => self.dispel(ctx, who, def, target),
            Social::Reveal { .. } => self.reveal(ctx, who, def, target),
            Social::Slow { .. } => self.slow(ctx, who, def, target),
            Social::Guardian {
                range_m,
                duration_ms,
                ..
            } => super::guardian::cast(ctx, who, def, *range_m, *duration_ms),
        };
        match done {
            Ok(()) => {
                let cooldown = Duration::from_millis(u64::from(def.social.cooldown_ms()));
                self.ready_at.insert((who, spell), now + cooldown);
            }
            Err(code) => refuse(ctx, who, spell, code),
        }
    }

    /// Elevara on a piece placed in the dorm room the caster is
    /// in (owner or guest alike), within `range_m`, not already floating.
    fn levitate(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        def: &SpellDef,
        target: SpellTarget,
        range_m: f32,
        duration_ms: u32,
    ) -> Result<(), SpellErrorCode> {
        let SpellTarget::Furniture { item_id } = target else {
            return Err(SpellErrorCode::BadTarget);
        };
        let (Some(zone), Some(pos)) = (ctx.zone(who), ctx.position(who)) else {
            return Err(SpellErrorCode::BadTarget);
        };
        // Only dorm rooms have placed furniture.
        if zone == CASTLE || ctx.instance(zone).is_some() {
            return Err(SpellErrorCode::NotHere);
        }
        let Some((placed, _owner)) = ctx.placed_item(zone, item_id) else {
            return Err(SpellErrorCode::BadTarget);
        };
        let at = placed.pos.to_m();
        let d2: f32 = (0..3).map(|k| (at[k] - pos[k]).powi(2)).sum();
        if d2 > range_m * range_m {
            return Err(SpellErrorCode::TooFar);
        }
        let now = ctx.now();
        if self
            .floating
            .get(&(zone, item_id))
            .is_some_and(|&until| until > now)
        {
            return Err(SpellErrorCode::Busy);
        }
        let duration = u16::try_from(duration_ms).unwrap_or(u16::MAX);
        self.floating.insert(
            (zone, item_id),
            now + Duration::from_millis(u64::from(duration)),
        );
        ctx.play_cast(who);
        let target = SpellTarget::Furniture { item_id };
        ctx.broadcast(
            zone,
            &ServerMsg::SpellCast {
                caster: who,
                spell: def.id,
                target,
            },
        );
        ctx.broadcast(
            zone,
            &ServerMsg::Levitating {
                item_id,
                by: who,
                duration_ms: duration,
            },
        );
        Ok(())
    }

    // ---- Spells to level 20 (protocol v11). Each reads its numbers from
    // `def.social`, answers `Ok(())` for a cast that went through (the
    // caller starts the cooldown) or the `SpellErrorCode` to refuse with.

    /// Tinctura (`Social::Recolour`): an ally's (or, with no target, the
    /// caster's) body garment shows a bright dye from `colours` for
    /// `duration_ms`: never its own primary dye nor the colour it shows now.
    fn recolour(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        def: &SpellDef,
        target: SpellTarget,
    ) -> Result<(), SpellErrorCode> {
        let Social::Recolour {
            range_m,
            duration_ms,
            colours,
            ..
        } = &def.social
        else {
            return Err(SpellErrorCode::NotHere);
        };
        let target = self_or_ally(ctx, who, target, *range_m)?;
        let own = ctx
            .appearance(target)
            .and_then(|a| a.outfit.body)
            .map(|w| w.dyes[0]);
        let showing = ctx.effects(target).iter().find_map(|e| match e.visible {
            Some(VisibleEffect::Recoloured { colour }) => Some(colour),
            _ => None,
        });
        let colour = pick_colour(colours, &[own, showing], rand::random::<u32>())
            .ok_or(SpellErrorCode::NotHere)?;
        let until = ctx.now() + Duration::from_millis(u64::from(*duration_ms));
        ctx.apply_effect(
            target,
            spell_effect(
                EffectKind::Recoloured,
                VisibleEffect::Recoloured { colour },
                SPEED_PCT_NORMAL,
                until,
            ),
        );
        cast_near(ctx, who, def.id, aimed(who, target), *range_m);
        Ok(())
    }

    /// Rigora outside duels (`Social::Bind`): an ally stiffens
    /// (speed 0, the pose frozen) for `duration_ms`.
    fn bind(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        def: &SpellDef,
        target: SpellTarget,
    ) -> Result<(), SpellErrorCode> {
        let Social::Bind {
            range_m,
            duration_ms,
            immune_ms,
            ..
        } = def.social
        else {
            return Err(SpellErrorCode::NotHere);
        };
        let target = self.hinder_target(ctx, who, target, range_m)?;
        let until = ctx.now() + Duration::from_millis(u64::from(duration_ms));
        ctx.apply_effect(
            target,
            spell_effect(EffectKind::Petrified, VisibleEffect::Bound, 0, until),
        );
        self.hindered(ctx, target, immune_ms);
        cast_near(
            ctx,
            who,
            def.id,
            SpellTarget::Entity { id: target },
            range_m,
        );
        Ok(())
    }

    /// Volucra (`Social::Birds`): nothing but the `SpellCast` (clients draw
    /// the flock round the caster).
    fn birds(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        def: &SpellDef,
        target: SpellTarget,
    ) -> Result<(), SpellErrorCode> {
        let Social::Birds { range_m, .. } = def.social else {
            return Err(SpellErrorCode::NotHere);
        };
        if !matches!(target, SpellTarget::None) {
            return Err(SpellErrorCode::BadTarget);
        }
        cast_near(ctx, who, def.id, SpellTarget::None, range_m);
        Ok(())
    }

    /// Adveni outside duels (`Social::Pull`): an ally slides toward the
    /// caster along safe ground (`WorldCtx::pull_destination`; `NoPath`
    /// when there is none), moved by the server (`WorldCtx::force_move`).
    fn pull(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        def: &SpellDef,
        target: SpellTarget,
    ) -> Result<(), SpellErrorCode> {
        let Social::Pull {
            range_m,
            distance_m,
            min_m,
            gap_m,
            move_ms,
            hold_ms,
            immune_ms,
            ..
        } = def.social
        else {
            return Err(SpellErrorCode::NotHere);
        };
        let target = self.hinder_target(ctx, who, target, range_m)?;
        let to = ctx
            .pull_destination(target, who, distance_m, min_m, gap_m)
            .map_err(|_| SpellErrorCode::NoPath)?;
        let (move_ms, hold_ms) = (wire_ms(move_ms), wire_ms(hold_ms));
        if !ctx.force_move(target, to, move_ms, hold_ms, who) {
            return Err(SpellErrorCode::Unavailable);
        }
        self.hindered(ctx, target, immune_ms);
        cast_near(
            ctx,
            who,
            def.id,
            SpellTarget::Entity { id: target },
            range_m,
        );
        Ok(())
    }

    /// Dissolvo outside duels (`Social::Dispel`): on an ally or
    /// the caster (no target), ends a lit wand and the bind, slow and
    /// recolour spells (never a potion or the Guardian; `BadTarget` when
    /// none of them was on); on a piece floating in the caster's dorm room,
    /// lands it (`Levitating { duration_ms: 0 }`).
    fn dispel(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        def: &SpellDef,
        target: SpellTarget,
    ) -> Result<(), SpellErrorCode> {
        let Social::Dispel { range_m, .. } = def.social else {
            return Err(SpellErrorCode::NotHere);
        };
        if let SpellTarget::Furniture { item_id } = target {
            return self.land(ctx, who, def, item_id, range_m);
        }
        let target = self_or_ally(ctx, who, target, range_m)?;
        let lit = ctx.lucerna(target);
        let ends = [
            EffectKind::Petrified,
            EffectKind::Slowed,
            EffectKind::Recoloured,
        ];
        let on: Vec<EffectKind> = ends
            .into_iter()
            .filter(|&k| ctx.has_effect(target, k))
            .collect();
        if !lit && on.is_empty() {
            return Err(SpellErrorCode::BadTarget);
        }
        if lit {
            ctx.set_lucerna(target, false);
        }
        for kind in on {
            ctx.remove_effect(target, kind);
        }
        cast_near(ctx, who, def.id, aimed(who, target), range_m);
        Ok(())
    }

    /// Dissolvo on furniture: a piece floating in the caster's dorm room,
    /// within `range_m`, lands now.
    fn land(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        def: &SpellDef,
        item_id: i64,
        range_m: f32,
    ) -> Result<(), SpellErrorCode> {
        let (Some(zone), Some(pos)) = (ctx.zone(who), ctx.position(who)) else {
            return Err(SpellErrorCode::BadTarget);
        };
        let now = ctx.now();
        let floating = self
            .floating
            .get(&(zone, item_id))
            .is_some_and(|&until| until > now);
        let Some((placed, _owner)) = ctx.placed_item(zone, item_id).filter(|_| floating) else {
            return Err(SpellErrorCode::BadTarget);
        };
        let at = placed.pos.to_m();
        let d2: f32 = (0..3).map(|k| (at[k] - pos[k]).powi(2)).sum();
        if d2 > range_m * range_m {
            return Err(SpellErrorCode::TooFar);
        }
        self.floating.remove(&(zone, item_id));
        ctx.play_cast(who);
        let cast = ServerMsg::SpellCast {
            caster: who,
            spell: def.id,
            target: SpellTarget::Furniture { item_id },
        };
        ctx.broadcast(zone, &cast);
        let landed = ServerMsg::Levitating {
            item_id,
            by: who,
            duration_ms: 0,
        };
        ctx.broadcast(zone, &landed);
        Ok(())
    }

    /// Aperio (`Social::Reveal`): the passage doors and find spots near
    /// the caster, to the caster alone (`Revealed`; an empty list is still
    /// a cast); everyone near sees the cast.
    fn reveal(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        def: &SpellDef,
        target: SpellTarget,
    ) -> Result<(), SpellErrorCode> {
        let Social::Reveal {
            radius_m,
            height_m,
            duration_ms,
            ..
        } = def.social
        else {
            return Err(SpellErrorCode::NotHere);
        };
        if !matches!(target, SpellTarget::None) {
            return Err(SpellErrorCode::BadTarget);
        }
        let spots = ctx.reveal_near(who, radius_m, height_m);
        cast_near(ctx, who, def.id, SpellTarget::None, radius_m);
        let msg = ServerMsg::Revealed {
            spell: def.id,
            duration_ms: wire_ms(duration_ms),
            spots,
        };
        ctx.send(who, &msg);
        Ok(())
    }

    /// Tardara outside duels (`Social::Slow`): an ally walks at `pct`%
    /// (and moves in slow motion) for `duration_ms`.
    fn slow(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        def: &SpellDef,
        target: SpellTarget,
    ) -> Result<(), SpellErrorCode> {
        let Social::Slow {
            range_m,
            duration_ms,
            pct,
            immune_ms,
            ..
        } = def.social
        else {
            return Err(SpellErrorCode::NotHere);
        };
        let target = self.hinder_target(ctx, who, target, range_m)?;
        let until = ctx.now() + Duration::from_millis(u64::from(duration_ms));
        ctx.apply_effect(
            target,
            spell_effect(
                EffectKind::Slowed,
                VisibleEffect::Slowed { pct },
                u16::from(pct),
                until,
            ),
        );
        self.hindered(ctx, target, immune_ms);
        cast_near(
            ctx,
            who,
            def.id,
            SpellTarget::Entity { id: target },
            range_m,
        );
        Ok(())
    }

    /// The target of a hindering spell (bind, slow, pull): an ally in
    /// range (`NeedsTarget` without one or for yourself), free to be
    /// hindered (`Unavailable`: seated, mounted, in class, trading,
    /// dueling, being moved) and not hindered a moment ago (`Busy`).
    fn hinder_target(
        &self,
        ctx: &WorldCtx<'_>,
        who: EntityId,
        target: SpellTarget,
        range_m: f32,
    ) -> Result<EntityId, SpellErrorCode> {
        let id = match target {
            SpellTarget::Entity { id } if id != who => id,
            SpellTarget::Furniture { .. } => return Err(SpellErrorCode::BadTarget),
            _ => return Err(SpellErrorCode::NeedsTarget),
        };
        ally_in_range(ctx, who, id, range_m)?;
        if !ctx.spell_target_free(id) {
            return Err(SpellErrorCode::Unavailable);
        }
        let now = ctx.now();
        if self.hindered_until.get(&id).is_some_and(|&t| t > now) {
            return Err(SpellErrorCode::Busy);
        }
        Ok(id)
    }

    /// A hindrance started on `target`: it is immune to the next for
    /// `immune_ms`.
    fn hindered(&mut self, ctx: &WorldCtx<'_>, target: EntityId, immune_ms: u32) {
        let until = ctx.now() + Duration::from_millis(u64::from(immune_ms));
        self.hindered_until.insert(target, until);
    }

    pub fn on_done(&mut self, ctx: &mut WorldCtx<'_>, done: SpellDone) {
        let _ = ctx;
        match done {}
    }

    /// `who` is about to leave the world: its cooldowns go (the wand goes
    /// out with the character).
    pub fn on_left(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId) {
        let _ = ctx;
        self.ready_at.retain(|(e, _), _| *e != who);
        self.hindered_until.remove(&who);
    }

    /// `who` moved out of zone `from`: the wand goes out; the Guardian
    /// decides about its own (GUARDIAN).
    pub fn on_zone_changed(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, from: ZoneId) {
        ctx.set_lucerna(who, false);
        super::guardian::on_zone_changed(ctx, who, from);
    }

    /// Once a second: forgets cooldowns that have passed, floats that have
    /// landed and immunities that have run out.
    pub fn tick(&mut self, ctx: &mut WorldCtx<'_>) {
        let now = ctx.now();
        self.ready_at.retain(|_, t| *t > now);
        self.floating.retain(|_, t| *t > now);
        self.hindered_until.retain(|_, t| *t > now);
    }

    /// Cooldowns, floats and immunities remembered (tests: memory stays
    /// bounded).
    pub fn remembered(&self) -> usize {
        self.ready_at.len() + self.floating.len() + self.hindered_until.len()
    }
}

/// `SpellCast` to everyone within `radius_m` of the caster, with the cast
/// animation.
fn cast_near(
    ctx: &mut WorldCtx<'_>,
    who: EntityId,
    spell: SpellId,
    target: SpellTarget,
    radius_m: f32,
) {
    ctx.play_cast(who);
    let msg = ServerMsg::SpellCast {
        caster: who,
        spell,
        target,
    };
    ctx.send_near(who, radius_m, &msg);
}

/// A harmless burst of colour, perhaps aimed at someone in range.
fn flourish(
    ctx: &mut WorldCtx<'_>,
    who: EntityId,
    def: &SpellDef,
    target: SpellTarget,
    range_m: f32,
) -> Result<(), SpellErrorCode> {
    let target = match target {
        SpellTarget::None => SpellTarget::None,
        // NPCs are never a spell's target (a professor, a castle resident).
        SpellTarget::Entity { id } if ctx.is_npc(id) => return Err(SpellErrorCode::BadTarget),
        SpellTarget::Entity { id } => match ctx.distance(who, id) {
            None => return Err(SpellErrorCode::BadTarget),
            Some(d) if d > range_m => return Err(SpellErrorCode::TooFar),
            Some(_) => SpellTarget::Entity { id },
        },
        SpellTarget::Furniture { .. } => return Err(SpellErrorCode::BadTarget),
    };
    cast_near(ctx, who, def.id, target, range_m);
    Ok(())
}

/// The character a spell that may fall back to its caster is aimed at:
/// the caster for no target (or itself), else an ally in range.
fn self_or_ally(
    ctx: &WorldCtx<'_>,
    who: EntityId,
    target: SpellTarget,
    range_m: f32,
) -> Result<EntityId, SpellErrorCode> {
    match target {
        SpellTarget::None => Ok(who),
        SpellTarget::Entity { id } if id == who => Ok(who),
        SpellTarget::Entity { id } => ally_in_range(ctx, who, id, range_m).map(|()| id),
        SpellTarget::Furniture { .. } => Err(SpellErrorCode::BadTarget),
    }
}

/// `id` is a player in the caster's zone (`BadTarget` for an NPC, someone
/// gone or elsewhere), a friend, party member or club member with no
/// block either way (`NotFriend`), within `range_m` (`TooFar`).
fn ally_in_range(
    ctx: &WorldCtx<'_>,
    who: EntityId,
    id: EntityId,
    range_m: f32,
) -> Result<(), SpellErrorCode> {
    if ctx.character_id(id).is_none() {
        return Err(SpellErrorCode::BadTarget);
    }
    let Some(d) = ctx.distance(who, id) else {
        return Err(SpellErrorCode::BadTarget);
    };
    if !ctx.spell_ally(who, id) {
        return Err(SpellErrorCode::NotFriend);
    }
    if d > range_m {
        return Err(SpellErrorCode::TooFar);
    }
    Ok(())
}

/// The `SpellCast` target for a spell on `target`: nobody when it is the
/// caster itself.
fn aimed(who: EntityId, target: EntityId) -> SpellTarget {
    if target == who {
        SpellTarget::None
    } else {
        SpellTarget::Entity { id: target }
    }
}

/// A spell effect (never stored, never a potion).
fn spell_effect(
    kind: EffectKind,
    visible: VisibleEffect,
    speed_pct: u16,
    until: Instant,
) -> ActiveEffect {
    ActiveEffect {
        kind,
        item_def: 0,
        fortune: None,
        visible: Some(visible),
        speed_pct,
        until,
    }
}

/// A duration as it crosses the wire (u16 ms; spells.json keeps them
/// within it).
fn wire_ms(ms: u32) -> u16 {
    u16::try_from(ms).unwrap_or(u16::MAX)
}

/// Tinctura's colour: one of `colours` (`roll` picks) that is none of
/// `not` (the robe's own dye, the colour it shows now); any of them if
/// every one is excluded. `None` only for an empty list.
pub(crate) fn pick_colour(colours: &[u8], not: &[Option<u8>], roll: u32) -> Option<u8> {
    let fresh: Vec<u8> = colours
        .iter()
        .copied()
        .filter(|c| !not.contains(&Some(*c)))
        .collect();
    let from = if fresh.is_empty() { colours } else { &fresh };
    (!from.is_empty()).then(|| from[roll as usize % from.len()])
}

#[cfg(test)]
#[path = "world_tests.rs"]
mod tests;
