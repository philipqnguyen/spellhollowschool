//! Custodio outside duels (spells to level 20, protocol
//! v11). The spell dispatch ([`super::world::Spells`]) calls in
//! here; everything is the effect itself, kept in memory like every spell
//! effect (never stored):
//!
//! - [`cast`]: `apply_effect(who, Guardian, visible Guardian { animal:
//!   sim_core::guardian_animal(cid), guarding: false }, speed 100,
//!   duration_ms)`; recasting refreshes it (the client sends `EndSpell` to
//!   dismiss instead, as Lucerna toggles). `SpellCast` goes to everyone within
//!   `range_m`; the companion itself is seen by every viewer of the caster
//!   through the effect (`EffectsChanged`, a snapshot's entered entities).
//!   Refused in a class (`NotHere`: entering one dismisses it anyway).
//! - [`end`]: `EndSpell` dismisses it (never a duel ward's).
//! - [`on_zone_changed`]: entering a class or an arena instance dismisses it;
//!   a duel ward (`guarding: true`) never leaves its arena.
//!
//! Logging out needs nothing here: the effect leaves with the character.
//! The duel ward's companion (`guarding: true`) is the dueling code's
//! (`crate::dueling::world`).

use std::time::{Duration, Instant};

use protocol::{
    EffectKind, EntityId, SPEED_PCT_NORMAL, ServerMsg, SpellErrorCode, SpellTarget, VisibleEffect,
};
use sim_core::spells::SpellDef;

use crate::realtime::entities::ZoneId;
use crate::realtime::ext::{ActiveEffect, FeatureKind, WorldCtx};

/// The effect a cast starts: character `cid`'s silver animal, following for
/// `duration_ms` from `now`.
fn summoned(cid: i64, now: Instant, duration_ms: u32) -> ActiveEffect {
    ActiveEffect {
        kind: EffectKind::Guardian,
        item_def: 0,
        fortune: None,
        visible: Some(VisibleEffect::Guardian {
            animal: sim_core::guardian_animal(cid),
            guarding: false,
        }),
        speed_pct: SPEED_PCT_NORMAL,
        until: now + Duration::from_millis(u64::from(duration_ms)),
    }
}

/// Whether `who`'s Guardian effect is a duel ward (it circles its duelist).
fn guarding(ctx: &WorldCtx<'_>, who: EntityId) -> bool {
    ctx.effects(who).iter().any(|e| {
        matches!(
            e.visible,
            Some(VisibleEffect::Guardian { guarding: true, .. })
        )
    })
}

/// A cast of `def` (`Social::Guardian { range_m, duration_ms }`) by `who`:
/// `Ok(())` when it went through (the caller starts the cooldown).
pub fn cast(
    ctx: &mut WorldCtx<'_>,
    who: EntityId,
    def: &SpellDef,
    range_m: f32,
    duration_ms: u32,
) -> Result<(), SpellErrorCode> {
    let Some(cid) = ctx.character_id(who) else {
        return Err(SpellErrorCode::BadTarget);
    };
    // Entering a class dismisses it, so it is not cast there either (arenas
    // refuse every social cast before the dispatch; a ward is the duel's).
    if ctx.in_class(who) || guarding(ctx, who) {
        return Err(SpellErrorCode::NotHere);
    }
    let now = ctx.now();
    ctx.apply_effect(who, summoned(cid, now, duration_ms));
    ctx.play_cast(who);
    let msg = ServerMsg::SpellCast {
        caster: who,
        spell: def.id,
        target: SpellTarget::None,
    };
    ctx.send_near(who, range_m, &msg);
    Ok(())
}

/// `EndSpell` for the Guardian: dismiss `who`'s (a duel ward stays).
pub fn end(ctx: &mut WorldCtx<'_>, who: EntityId) {
    if ctx.has_effect(who, EffectKind::Guardian) && !guarding(ctx, who) {
        ctx.remove_effect(who, EffectKind::Guardian);
    }
}

/// `who` moved out of zone `from`: into a class or an arena it is
/// dismissed; a duel ward never follows its duelist out of the arena.
pub fn on_zone_changed(ctx: &mut WorldCtx<'_>, who: EntityId, from: ZoneId) {
    let _ = from;
    if !ctx.has_effect(who, EffectKind::Guardian) {
        return;
    }
    let into_arena = ctx.zone(who).is_some_and(|z| {
        ctx.instance(z)
            .is_some_and(|f| matches!(f.kind, FeatureKind::Arena { .. }))
    });
    if into_arena || ctx.in_class(who) || guarding(ctx, who) {
        ctx.remove_effect(who, EffectKind::Guardian);
    }
}

#[cfg(test)]
#[path = "guardian_tests.rs"]
mod tests;
