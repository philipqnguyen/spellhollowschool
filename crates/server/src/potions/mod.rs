//! Potions: drinking is
//! realtime ([`world`]) because the effect shows at once and Mimic Draught needs
//! a friend's consent; the potion is used up in a job's transaction
//! ([`drink`]: `crate::items::consume`, `Reason::Drink`) that also writes
//! the `active_effects` row (so the effect survives a relog), then the world
//! applies it (`WorldCtx::apply_effect`).
//!
//! What each potion does (durations and numbers: `data/economy.json`
//! `effects`; `potion` and dyes: the catalog) is [`effect_for`]. Two rules
//! beyond `EffectKind::excludes` (Diminishing Draught ↔ Enlarging Draught) keep looks honest:
//! Mimic Draught and the Invisibility Draught end each other ([`conflicts`]),
//! so a borrowed face never walks around without the drinker's own
//! nameplate.
//!
//! No HTTP routes of its own (potions are items: `GET /api/inventory?kind=potion`).

pub mod world;

use std::time::{Duration, Instant};

use axum::Router;
use protocol::{Appearance, EffectKind, PotionErrorCode, VisibleEffect};
use sqlx::PgPool;

use crate::AppState;
use crate::catalog::shipped;
use crate::inventory::LOCATION_INVENTORY;
use crate::items;
use crate::ledger::Reason;
use crate::realtime::ext::ActiveEffect;

/// How long an effect of `kind` lasts.
pub fn duration_of(kind: EffectKind) -> Duration {
    Duration::from_secs(u64::from(shipped().economy.effects.duration_secs(kind)))
}

/// The kind a new effect of `kind` ends besides `EffectKind::excludes`:
/// Mimic Draught and Invisibility never run together (a friend's look with no
/// nameplate would pass for the friend).
pub fn conflicts(kind: EffectKind) -> Option<EffectKind> {
    match kind {
        EffectKind::MimicDraught => Some(EffectKind::Invisibility),
        EffectKind::Invisibility => Some(EffectKind::MimicDraught),
        _ => None,
    }
}

/// The effect drinking a potion of `item_def` (with its instance's `dyes`)
/// starts at `now`; `disguise` is the consenting friend's look for
/// Mimic Draught. `None` for an item that is not a potion, Mimic Draught without a
/// look, or a look offered for any other potion.
pub fn effect_for(
    item_def: u16,
    dyes: [u8; 3],
    disguise: Option<Appearance>,
    now: Instant,
) -> Option<ActiveEffect> {
    let content = shipped();
    let potion = content.catalog.get(item_def)?.potion?;
    let rules = &content.economy.effects;
    let normal = protocol::SPEED_PCT_NORMAL;
    if disguise.is_some() != (potion.effect == EffectKind::MimicDraught) {
        return None;
    }
    if potion.effect == EffectKind::Featherlight {
        // A faster broom (`OwnEffects.broom_pct`); nobody else sees it.
        return Some(ActiveEffect {
            fortune: None,
            kind: potion.effect,
            item_def,
            visible: None,
            speed_pct: normal,
            until: now + duration_of(potion.effect),
        });
    }
    let (visible, speed_pct) = match potion.effect {
        EffectKind::Glow => (VisibleEffect::Glow { colour: dyes[0] }, normal),
        EffectKind::Chameleon => (
            VisibleEffect::Chameleon {
                part: potion.part?,
                // The tint (the potion's primary dye) picks the colours it cycles.
                family: content.palette.family_of(dyes[0])?,
            },
            normal,
        ),
        EffectKind::Shrinking => (
            VisibleEffect::Scale {
                pct: rules.shrinking.scale_pct,
            },
            normal,
        ),
        EffectKind::Swelling => (
            VisibleEffect::Scale {
                pct: rules.swelling.scale_pct,
            },
            normal,
        ),
        EffectKind::Babbling => (VisibleEffect::Babbling, normal),
        EffectKind::MimicDraught => (
            VisibleEffect::Disguise {
                appearance: disguise?,
            },
            normal,
        ),
        EffectKind::FortunesDraught => (VisibleEffect::Lucky, normal),
        EffectKind::PickMeUpTonic => (
            VisibleEffect::Steam,
            rules
                .pick_me_up_tonic
                .speed_pct
                .clamp(normal, protocol::SPEED_PCT_MAX),
        ),
        EffectKind::Invisibility => (VisibleEffect::NameplateHidden, normal),
        // A Divination fortune, never a potion (the catalog refuses one);
        // Featherlight is above. Spells L20: spell effects are cast, never drunk.
        EffectKind::Fortune
        | EffectKind::Featherlight
        | EffectKind::Petrified
        | EffectKind::Slowed
        | EffectKind::Recoloured
        | EffectKind::Guardian => return None,
    };
    Some(ActiveEffect {
        fortune: None,
        kind: potion.effect,
        item_def,
        visible: Some(visible),
        speed_pct,
        until: now + duration_of(potion.effect),
    })
}

/// A potion drunk: the effect to apply, and the effects it ended (the kind
/// it excludes or conflicts with), which the world removes.
#[derive(Debug, Clone, PartialEq)]
pub struct Drunk {
    pub effect: ActiveEffect,
    pub ended: Vec<EffectKind>,
}

/// Checks that `item_id` is a potion in the character's inventory, and of
/// the Mimic Draught kind exactly when `mimic_draught` (the consent request's
/// check; nothing is used up).
pub async fn check(
    pool: &PgPool,
    character_id: i64,
    item_id: i64,
    mimic_draught: bool,
) -> Result<(), PotionErrorCode> {
    let row: Option<(i32, String)> =
        sqlx::query_as("SELECT item_def, location FROM items WHERE id = $1 AND owner_id = $2")
            .bind(item_id)
            .bind(character_id)
            .fetch_optional(pool)
            .await
            .map_err(failed)?;
    let Some((def, location)) = row else {
        return Err(PotionErrorCode::ItemNotFound);
    };
    if location != LOCATION_INVENTORY {
        return Err(PotionErrorCode::ItemNotFound);
    }
    potion_kind(def, mimic_draught).map(|_| ())
}

/// The effect of potion definition `def`, refusing Mimic Draught unless
/// `mimic_draught` (it needs a friend: `NeedsTarget`) and anything else when it is.
fn potion_kind(def: i32, mimic_draught: bool) -> Result<EffectKind, PotionErrorCode> {
    let kind = u16::try_from(def)
        .ok()
        .and_then(|d| shipped().catalog.get(d))
        .and_then(|d| d.potion)
        .map(|p| p.effect)
        .ok_or(PotionErrorCode::NotAPotion)?;
    match (kind == EffectKind::MimicDraught, mimic_draught) {
        (true, false) => Err(PotionErrorCode::NeedsTarget),
        (false, true) => Err(PotionErrorCode::NotAPotion),
        _ => Ok(kind),
    }
}

fn failed(e: impl std::error::Error) -> PotionErrorCode {
    tracing::error!(error = %e, "drinking a potion failed");
    goodsorcery::capture_error(&e);
    PotionErrorCode::Failed
}

/// Drinks potion `item_id` of the character's inventory in one transaction:
/// the character's row is locked, then the potion's; one unit is used up
/// (`Reason::Drink`) and the effect's row written (replacing the same kind,
/// and deleting the kinds it ends). `disguise` is the friend's look for
/// Mimic Draught. Two drinks of the same last potion serialize on the locks:
/// the second finds nothing to drink (`ItemNotFound`).
pub async fn drink(
    pool: &PgPool,
    character_id: i64,
    item_id: i64,
    disguise: Option<Appearance>,
) -> Result<Drunk, PotionErrorCode> {
    let mut tx = pool.begin().await.map_err(failed)?;
    crate::crafting::lock_character(&mut tx, character_id)
        .await
        .map_err(failed)?;
    let rows = items::lock(&mut tx, character_id, &[item_id])
        .await
        .map_err(failed)?;
    let Some(item) = rows.into_iter().next() else {
        return Err(PotionErrorCode::ItemNotFound);
    };
    if item.location != LOCATION_INVENTORY {
        return Err(PotionErrorCode::ItemNotFound);
    }
    potion_kind(i32::from(item.item_def), disguise.is_some())?;
    let effect = effect_for(item.item_def, item.dyes, disguise, Instant::now())
        .ok_or(PotionErrorCode::NotAPotion)?;
    items::consume(&mut tx, &item, 1, Reason::Drink, None)
        .await
        .map_err(failed)?;
    crate::effects::store(&mut tx, character_id, &effect, duration_of(effect.kind))
        .await
        .map_err(failed)?;
    let ended: Vec<EffectKind> = effect
        .kind
        .excludes()
        .into_iter()
        .chain(conflicts(effect.kind))
        .collect();
    if let Some(other) = conflicts(effect.kind) {
        crate::effects::remove(&mut tx, character_id, other)
            .await
            .map_err(failed)?;
    }
    tx.commit().await.map_err(failed)?;
    Ok(Drunk { effect, ended })
}

/// No routes (see the module docs).
pub fn routes() -> Router<AppState> {
    Router::new()
}

#[cfg(test)]
mod tests {
    use sim_core::ItemKind;

    use super::*;

    fn potion(kind: EffectKind) -> u16 {
        shipped()
            .catalog
            .items
            .iter()
            .find(|i| i.kind == ItemKind::Potion && i.potion.is_some_and(|p| p.effect == kind))
            .unwrap()
            .id
    }

    #[test]
    fn every_potion_makes_its_effect() {
        let now = Instant::now();
        let rules = &shipped().economy.effects;
        for kind in EffectKind::POTIONS {
            let def = potion(kind);
            let disguise = (kind == EffectKind::MimicDraught).then(Appearance::default);
            let e = effect_for(def, [34, 0, 0], disguise, now).unwrap();
            assert_eq!(e.kind, kind);
            // Featherlight is felt, not seen (a faster broom).
            assert_eq!(
                e.visible.is_some(),
                kind != EffectKind::Featherlight,
                "{kind:?} shows"
            );
            assert_eq!(e.until - now, duration_of(kind));
            let expect_speed = if kind == EffectKind::PickMeUpTonic {
                rules.pick_me_up_tonic.speed_pct
            } else {
                100
            };
            assert_eq!(e.speed_pct, expect_speed, "{kind:?}");
        }
        let glow = effect_for(potion(EffectKind::Glow), [34, 0, 0], None, now).unwrap();
        assert_eq!(glow.visible, Some(VisibleEffect::Glow { colour: 34 }));
        let chameleon = effect_for(potion(EffectKind::Chameleon), [34, 0, 0], None, now).unwrap();
        let blues = shipped().palette.family_of(34).unwrap();
        assert!(matches!(
            chameleon.visible,
            Some(VisibleEffect::Chameleon { family, .. }) if family == blues
        ));
        let small = effect_for(potion(EffectKind::Shrinking), [0; 3], None, now).unwrap();
        assert_eq!(small.visible, Some(VisibleEffect::Scale { pct: 50 }));
    }

    #[test]
    fn mimic_draught_needs_a_look_and_nothing_else_takes_one() {
        let now = Instant::now();
        assert!(effect_for(potion(EffectKind::MimicDraught), [0; 3], None, now).is_none());
        let look = Some(Appearance::default());
        assert!(effect_for(potion(EffectKind::Glow), [0; 3], look, now).is_none());
        assert!(
            effect_for(3001, [0; 3], None, now).is_none(),
            "not a potion"
        );
        assert_eq!(
            potion_kind(i32::from(potion(EffectKind::MimicDraught)), false),
            Err(PotionErrorCode::NeedsTarget)
        );
        assert_eq!(
            potion_kind(i32::from(potion(EffectKind::Glow)), true),
            Err(PotionErrorCode::NotAPotion)
        );
        assert_eq!(potion_kind(3001, false), Err(PotionErrorCode::NotAPotion));
        assert_eq!(
            conflicts(EffectKind::MimicDraught),
            Some(EffectKind::Invisibility)
        );
        assert_eq!(
            conflicts(EffectKind::Invisibility),
            Some(EffectKind::MimicDraught)
        );
        assert_eq!(conflicts(EffectKind::Glow), None);
    }
}
