//! Spells outside duels and the dueling loadout.
//!
//! **Casting** ([`world::Spells`], `ClientMsg::CastSpell` / `EndSpell`):
//! the caster must have learned the spell (`SchoolState::learned`;
//! `SpellError { NotLearned }`), the zone must allow social casts (the
//! world refuses in arenas: `NotHere`), the shared action bucket must have
//! a token (`RateLimited`) and its social cooldown must have passed
//! (`Cooldown`; per character and spell, in memory, started by a cast that
//! went through). Every cast plays `WorldCtx::play_cast` and sends
//! `SpellCast`. Then by `Social`:
//!
//! - **Light** (Lucerna): `WorldCtx::set_lucerna(e, true)` (`VisibleEffect::Lucerna`
//!   for everyone who sees the caster) and `SpellCast` within the light's
//!   `radius_m`; `EndSpell` puts it out, as do a zone change and leaving.
//! - **Levitate** (Elevara): target `Furniture { item_id }`
//!   placed in the dorm room the caster is in (`WorldCtx::placed_item`; the
//!   owner and guests alike; `NotHere` elsewhere, `BadTarget` for anything
//!   else), within `range_m` of the piece (`TooFar`), not already floating
//!   (`Busy`): `SpellCast` and `Levitating { item_id, by, duration_ms }`
//!   to the room (`broadcast`). Positions never change; no database work.
//! - **Flourish** (the first five dueling spells outside a duel):
//!   `SpellCast` within `range_m`, aimed at nobody or at a character in
//!   range.
//! - Spells to level 20 (protocol v11), in [`world::Spells`]:
//!   **Recolour** (Tinctura: an ally's or your own robe shows a bright
//!   dye, `EffectKind::Recoloured`), **Bind** (Rigora: `Petrified`,
//!   speed 0) and **Slow** (Tardara: `Slowed`) on an ally, **Pull**
//!   (Adveni: an ally slides toward you along safe ground,
//!   `WorldCtx::pull_destination` then `force_move`; `NoPath`), **Dispel**
//!   (Dissolvo: ends an ally's or your own lit wand, bind, slow and
//!   recolour, or lands a floating piece), **Reveal** (Aperio: `Revealed`
//!   to the caster only) and **Birds** (Volucra: the cast alone);
//!   **Guardian** in [`guardian`]. Spells aimed at someone else need an
//!   ally in range: a friend, party member or club member, no block
//!   either way (`WorldCtx::spell_ally`; `NotFriend`, `TooFar`, `BadTarget`); the hindering
//!   three need one (`NeedsTarget`) who is free
//!   (`WorldCtx::spell_target_free`, else `Unavailable`) and was not
//!   hindered in the last `immune_ms` (`Busy`); a bound caster may cast
//!   only Dissolvo (`Busy`). See docs/architecture.md, "Spells and dueling".
//!
//! Routes ([`routes`]): `GET /api/spells` (`{ learned: [{ id, taught_at,
//! learned_at }], loadout, slots }`) and `PUT /api/loadout` (`{ spells:
//! [id] }` → `200 { loadout, slots }`; `400 invalid_loadout` from
//! `Spells::check_loadout`, naming the spell), which stores `loadouts` and
//! calls `WorldHandle::spellbook_changed`. A duel keeps the loadout it
//! started with (the dueling code copies it at the start).

pub mod guardian;
pub mod world;

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{LOADOUT_MAX, SpellId};
use serde::{Deserialize, Serialize};
use sim_core::spells::LoadoutError;

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Player, Writer, write_limited};
use crate::http::{ApiError, ApiJson};
use crate::progression;
use crate::rate_limit::RateLimit;

/// School writes: a burst of 10, then one every 2 s per account.
pub const SCHOOL_WRITES: RateLimit = RateLimit {
    burst: 10,
    per: std::time::Duration::from_secs(2),
};

/// One learned spell in `GET /api/spells`.
#[derive(Debug, Clone, Serialize, sqlx::FromRow)]
pub struct LearnedView {
    pub id: i32,
    pub taught_at: String,
    pub learned_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SpellsView {
    pub learned: Vec<LearnedView>,
    pub loadout: Vec<SpellId>,
    pub slots: u8,
}

/// The character's level, from its row.
async fn level_of(conn: &mut sqlx::PgConnection, character_id: i64) -> Result<u8, sqlx::Error> {
    let level: i16 = sqlx::query_scalar("SELECT level FROM characters WHERE id = $1")
        .bind(character_id)
        .fetch_one(conn)
        .await?;
    Ok(u8::try_from(level).unwrap_or(1))
}

/// `GET /api/spells`.
pub async fn get_spells(
    State(state): State<AppState>,
    player: Player,
) -> Result<Json<SpellsView>, ApiError> {
    let id = player.character.id;
    let mut conn = state.pool.acquire().await?;
    let level = level_of(&mut conn, id).await?;
    let learned: Vec<LearnedView> = sqlx::query_as(
        "SELECT spell_id AS id, taught_at, learned_at FROM known_spells
         WHERE character_id = $1 ORDER BY spell_id",
    )
    .bind(id)
    .fetch_all(&mut *conn)
    .await?;
    let (_, loadout) = progression::spellbook(&mut conn, id, level).await?;
    Ok(Json(SpellsView {
        learned,
        loadout,
        slots: shipped().progression.loadout_slots(level),
    }))
}

#[derive(Debug, Deserialize)]
pub struct LoadoutBody {
    pub spells: Vec<SpellId>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct LoadoutView {
    pub loadout: Vec<SpellId>,
    pub slots: u8,
}

/// The `invalid_loadout` message, naming the spell.
fn loadout_message(e: &LoadoutError, slots: u8) -> String {
    let name = |id: &SpellId| {
        shipped()
            .spells
            .get(*id)
            .map_or_else(|| format!("Spell {id}"), |s| s.name.clone())
    };
    match e {
        LoadoutError::TooMany => format!("Your loadout has {slots} slots."),
        LoadoutError::Duplicate(id) => format!("{} is in the loadout twice.", name(id)),
        LoadoutError::NotDueling(id) => format!("{} is not a dueling spell.", name(id)),
        LoadoutError::NotLearned(id) => format!("You have not learned {} yet.", name(id)),
    }
}

/// `PUT /api/loadout { spells }`: learned dueling spells, no repeats, at
/// most the level's slots. Checked and stored with the character's row
/// locked, so a concurrent level change or lesson cannot slip between.
pub async fn put_loadout(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(body): ApiJson<LoadoutBody>,
) -> Result<Json<LoadoutView>, ApiError> {
    let id = player.character.id;
    if body.spells.len() > LOADOUT_MAX {
        return Err(ApiError::bad_request(
            "invalid_loadout",
            format!("A loadout holds at most {LOADOUT_MAX} spells."),
        ));
    }
    let mut tx = state.pool.begin().await?;
    let level: i16 =
        sqlx::query_scalar("SELECT level FROM characters WHERE id = $1 FOR NO KEY UPDATE")
            .bind(id)
            .fetch_one(&mut *tx)
            .await?;
    let level = u8::try_from(level).unwrap_or(1);
    let slots = shipped().progression.loadout_slots(level);
    let (learned, _) = progression::spellbook(&mut tx, id, level).await?;
    if let Err(e) = shipped()
        .spells
        .check_loadout(&learned, &body.spells, slots)
    {
        return Err(ApiError::bad_request(
            "invalid_loadout",
            loadout_message(&e, slots),
        ));
    }
    let stored: Vec<i32> = body.spells.iter().map(|&s| i32::from(s)).collect();
    sqlx::query(
        "INSERT INTO loadouts (character_id, spells) VALUES ($1, $2)
         ON CONFLICT (character_id) DO UPDATE SET spells = EXCLUDED.spells, updated_at = now()",
    )
    .bind(id)
    .bind(&stored)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    if let Some(w) = state.world() {
        w.spellbook_changed(id, learned, body.spells.clone(), Vec::new());
    }
    Ok(Json(LoadoutView {
        loadout: body.spells,
        slots,
    }))
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::{get, put};
    let router = Router::new()
        .route("/spells", get(get_spells))
        .route("/loadout", put(put_loadout));
    write_limited(router, SCHOOL_WRITES)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loadout_errors_name_the_spell() {
        let lucerna = shipped().spells.by_key("lucerna").unwrap();
        let msg = loadout_message(&LoadoutError::NotDueling(lucerna.id), 3);
        assert!(msg.contains(&lucerna.name), "{msg}");
        assert!(loadout_message(&LoadoutError::TooMany, 3).contains('3'));
        assert!(loadout_message(&LoadoutError::NotLearned(999), 3).contains("999"));
    }
}
