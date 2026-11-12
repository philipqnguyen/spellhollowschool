//! The map's reveals and the secrets found, in the database
//! (docs/architecture.md "Secrets and the map"; `0020_secrets.sql`).
//!
//! - **Reveals** (`map_reveals`): a place (a castle layout surface, by its
//!   stable id) is revealed once the character has stood on it. The world
//!   marks new ones per character (`realtime::explore`) and hands them to the
//!   saver with the 30 s position save and at logout; [`save_reveals`] writes
//!   a batch in one statement, `ON CONFLICT DO NOTHING`, so a retried save
//!   changes nothing.
//! - **Discoveries** (`discoveries`): [`discover`] records a secret found
//!   and grants its reward in one transaction: the row (its primary key makes
//!   it once per character), the XP (`explore`, ref_id = the secret's `num`),
//!   one House Cup point from the `find` source under its daily cap, and the
//!   recipe it teaches (`known_recipes`). A second call finds the row and
//!   grants nothing.
//! - **`GET /api/map`**: `{ surfaces: [id, ...], secrets: [id, ...] }`, the
//!   revealed surfaces and the found secrets: the database's, plus what the
//!   world holds for the character while it is online and not saved yet.

use std::collections::BTreeSet;
use std::sync::Arc;

use axum::extract::State;
use axum::{Json, Router};
use chrono::Utc;
use serde::Serialize;
use sqlx::PgPool;

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::Player;
use crate::http::ApiError;

/// What a character has explored, as loaded at sign-in
/// (`realtime::world::Joining`).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Explored {
    /// Revealed surface ids.
    pub reveals: Vec<String>,
    /// Found secret ids.
    pub secrets: Vec<String>,
}

/// One revealed surface waiting to be saved.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Reveal {
    pub character_id: i64,
    pub surface: Arc<str>,
}

/// A character's reveals and found secrets.
pub async fn load(pool: &PgPool, character_id: i64) -> Result<Explored, sqlx::Error> {
    let reveals = sqlx::query_scalar(
        "SELECT surface FROM map_reveals WHERE character_id = $1 ORDER BY surface",
    )
    .bind(character_id)
    .fetch_all(pool)
    .await?;
    let secrets = sqlx::query_scalar(
        "SELECT secret FROM discoveries WHERE character_id = $1 ORDER BY secret",
    )
    .bind(character_id)
    .fetch_all(pool)
    .await?;
    Ok(Explored { reveals, secrets })
}

/// Writes a batch of reveals in one statement (unknown characters are
/// skipped; reveals already saved are left as they are). The rows written.
pub async fn save_reveals(pool: &PgPool, reveals: &[Reveal]) -> Result<u64, sqlx::Error> {
    if reveals.is_empty() {
        return Ok(0);
    }
    let ids: Vec<i64> = reveals.iter().map(|r| r.character_id).collect();
    let surfaces: Vec<&str> = reveals.iter().map(|r| &*r.surface).collect();
    let res = sqlx::query(
        "INSERT INTO map_reveals (character_id, surface)
         SELECT u.id, u.surface FROM unnest($1::bigint[], $2::text[]) AS u(id, surface)
         JOIN characters c ON c.id = u.id
         ON CONFLICT DO NOTHING",
    )
    .bind(ids)
    .bind(surfaces)
    .execute(pool)
    .await?;
    Ok(res.rows_affected())
}

/// What finding a secret gave (see [`discover`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Discovery {
    pub secret: String,
    /// The XP grant (`explore`).
    pub xp: crate::progression::XpGrant,
    /// A House Cup point came with it (the `find` cap was not used up).
    pub house_point: bool,
    /// The recipe it taught, if any.
    pub recipe: Option<u16>,
}

/// Records that the character found `secret` and grants its reward, in one
/// transaction; `None` when it had found it before (nothing is granted).
pub async fn discover(
    pool: &PgPool,
    character_id: i64,
    secret: &sim_core::Secret,
) -> Result<Option<Discovery>, sqlx::Error> {
    let now = Utc::now();
    let mut tx = pool.begin().await?;
    // The character's row first (the established lock order).
    let house: Option<i16> =
        sqlx::query_scalar("SELECT house FROM characters WHERE id = $1 FOR NO KEY UPDATE")
            .bind(character_id)
            .fetch_optional(&mut *tx)
            .await?;
    let Some(house) = house.and_then(crate::characters::house_from_db) else {
        return Err(sqlx::Error::RowNotFound);
    };
    let reward = &secret.reward;
    let recorded: Option<i64> = sqlx::query_scalar(
        "INSERT INTO discoveries (character_id, secret, xp, recipe) VALUES ($1, $2, $3, $4)
         ON CONFLICT DO NOTHING RETURNING character_id",
    )
    .bind(character_id)
    .bind(&secret.id)
    .bind(i32::try_from(reward.xp).unwrap_or(i32::MAX))
    .bind(reward.recipe.map(i32::from))
    .fetch_optional(&mut *tx)
    .await?;
    if recorded.is_none() {
        tx.rollback().await?;
        return Ok(None);
    }
    let xp = crate::progression::grant_xp(
        &mut tx,
        character_id,
        protocol::XpSource::Explore,
        Some(i64::from(secret.num)),
        reward.xp,
        now,
    )
    .await?;
    if xp.granted != reward.xp {
        // `explore` has no cap and the ref is new under the row above.
        return Err(sqlx::Error::Protocol(format!(
            "secret {}: {} of {} XP granted",
            secret.id, xp.granted, reward.xp
        )));
    }
    let points = shipped().house_cup.find_points(true);
    let awarded = crate::house_points::award_capped(
        &mut tx,
        house,
        u32::from(points),
        character_id,
        crate::house_points::PointsSource::Find,
        None,
        now,
    )
    .await?;
    if let Some(recipe) = reward.recipe {
        sqlx::query(
            "INSERT INTO known_recipes (character_id, recipe_id) VALUES ($1, $2)
             ON CONFLICT DO NOTHING",
        )
        .bind(character_id)
        .bind(i32::from(recipe))
        .execute(&mut *tx)
        .await?;
    }
    tx.commit().await?;
    Ok(Some(Discovery {
        secret: secret.id.clone(),
        xp,
        house_point: awarded > 0,
        recipe: reward.recipe,
    }))
}

/// `GET /api/map`.
#[derive(Debug, Serialize)]
pub struct MapView {
    pub surfaces: Vec<String>,
    pub secrets: Vec<String>,
}

pub async fn get_map(
    State(state): State<AppState>,
    player: Player,
) -> Result<Json<MapView>, ApiError> {
    let id = player.character.id;
    let saved = load(&state.pool, id).await?;
    let mut surfaces: BTreeSet<String> = saved.reveals.into_iter().collect();
    let mut secrets: BTreeSet<String> = saved.secrets.into_iter().collect();
    // An online character's reveals not saved yet.
    if let Some(rt) = &state.realtime
        && let Some(live) = rt.world.explored(id).await
    {
        surfaces.extend(live.reveals);
        secrets.extend(live.secrets);
    }
    Ok(Json(MapView {
        surfaces: surfaces.into_iter().collect(),
        secrets: secrets.into_iter().collect(),
    }))
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new().route("/map", get(get_map))
}
