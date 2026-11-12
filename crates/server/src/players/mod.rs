//! The players list and the player card.
//!
//! - `GET /api/players?q=&limit=&after=` → `{ items: [{ name, house }],
//!   next }`: the characters online now (any zone), **name and house only,
//!   never where**. `q` (optional, trimmed, at most 24 characters) is a
//!   case-insensitive substring of the name (`characters::name_key`); sorted
//!   by name key, the cursor the last name key (`characters::page`); `limit`
//!   default 25, at most 100. `400 invalid_query` for a longer `q`. Served
//!   from the realtime side's [`Presence`](crate::realtime::presence::Presence)
//!   (no database read for the list); empty when the world is not running.
//! - `GET /api/players/{name}/card` → `{ name, house, level, skills:
//!   [{ discipline, level }], appearance, club }` (`club`: `{ name, tag }` or
//!   null) for any character by name (case-insensitive), online or not;
//!   `404 player_not_found`.
//!
//! Both are signed in and share one read budget per account
//! ([`PLAYERS_RATE_LIMIT`]). No opt-out: the card shows nothing private (name,
//! house, level, skills and look, as anyone in the castle sees them). The card's
//! actions reuse what exists: Add friend is `POST /api/friends/requests`,
//! Whisper the chat's whisper channel, Challenge `DuelChallengeName`.

use std::time::Duration;

use axum::extract::State;
use axum::{Json, Router};
use protocol::{Appearance, House};
use serde::{Deserialize, Serialize};
use sim_core::Subject;
use sqlx::types::Json as DbJson;

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{
    ApiPath, NAME_MAX_CHARS, Page, PageQuery, Reader, house_from_db, name_key, read_limited,
};
use crate::http::{ApiError, ApiQuery};
use crate::progression::ProgressionView;
use crate::rate_limit::RateLimit;
use crate::realtime::presence::Listed;

/// List pages and cards per account: a burst of 20, then one every 500 ms
/// (as the friend list).
pub const PLAYERS_RATE_LIMIT: RateLimit = RateLimit {
    burst: 20,
    per: Duration::from_millis(500),
};

/// The list's own query: the search.
#[derive(Debug, Default, Deserialize)]
pub struct PlayersQuery {
    #[serde(default)]
    pub q: Option<String>,
}

/// The search as a name key: trimmed and case-folded; `400 invalid_query`
/// past the longest name.
pub fn search_key(q: Option<&str>) -> Result<String, ApiError> {
    let q = q.unwrap_or("").trim();
    if q.chars().count() > NAME_MAX_CHARS {
        return Err(ApiError::bad_request(
            "invalid_query",
            format!("Search for at most {NAME_MAX_CHARS} characters."),
        ));
    }
    Ok(name_key(q))
}

/// `GET /api/players?q=&limit=&after=`: online characters by name.
pub async fn list(
    State(state): State<AppState>,
    Reader(_player): Reader,
    ApiQuery(query): ApiQuery<PlayersQuery>,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<Listed>>, ApiError> {
    let q = search_key(query.q.as_deref())?;
    let limit = page.limit()?;
    let after: Option<String> = page.after()?;
    let rows = match &state.realtime {
        Some(rt) => rt.presence.find(&q, after.as_deref(), limit as usize + 1),
        None => Vec::new(),
    };
    let page = Page::from_rows(rows, limit, |(key, _)| key.clone());
    Ok(Json(page.map(|(_, listed)| listed)))
}

/// One subject on the card: its level only.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct CardSkill {
    /// The subject's id (named `discipline` as in `GET /api/skills`).
    pub discipline: Subject,
    pub level: u8,
}

/// `GET /api/players/{name}/card`.
#[derive(Debug, Clone, Serialize)]
pub struct PlayerCard {
    pub name: String,
    pub house: House,
    pub level: u8,
    /// Every subject, in `Subject::ALL` order.
    pub skills: Vec<CardSkill>,
    /// The stored look with the worn outfit (as `GET /api/characters/me`).
    pub appearance: Appearance,
    /// The club they are in (name and tag), if any.
    pub club: Option<CardClub>,
}

/// A club as the card shows it.
#[derive(Debug, Clone, Serialize, sqlx::FromRow)]
pub struct CardClub {
    pub name: String,
    pub tag: String,
}

#[derive(sqlx::FromRow)]
struct CardRow {
    id: i64,
    name: String,
    house: i16,
    xp: i32,
    appearance: Option<DbJson<Appearance>>,
}

fn player_not_found() -> ApiError {
    ApiError::not_found("player_not_found", "There is no one by that name.")
}

/// `GET /api/players/{name}/card`: any character by name, online or not.
pub async fn card(
    State(state): State<AppState>,
    Reader(_player): Reader,
    ApiPath(name): ApiPath<String>,
) -> Result<Json<PlayerCard>, ApiError> {
    let key = name_key(name.trim());
    if key.is_empty() || key.chars().count() > NAME_MAX_CHARS {
        return Err(player_not_found());
    }
    let row: Option<CardRow> = sqlx::query_as(
        "SELECT id, name, house, xp, appearance FROM characters WHERE name_key = $1",
    )
    .bind(&key)
    .fetch_optional(&state.pool)
    .await?;
    let row = row.ok_or_else(player_not_found)?;
    let house = house_from_db(row.house)
        .ok_or_else(|| ApiError::internal("character row has an unknown house"))?;
    let xp: Vec<(String, i32)> =
        sqlx::query_as("SELECT discipline, xp FROM skills WHERE character_id = $1")
            .bind(row.id)
            .fetch_all(&state.pool)
            .await?;
    let skills = Subject::ALL
        .into_iter()
        .map(|s| {
            let xp = xp
                .iter()
                .find(|(id, _)| id == s.id())
                .and_then(|&(_, xp)| u32::try_from(xp).ok())
                .unwrap_or(0);
            CardSkill {
                discipline: s,
                level: crate::skills::level_of(xp),
            }
        })
        .collect();
    let club: Option<CardClub> = sqlx::query_as(
        "SELECT c.name, c.tag FROM club_members m JOIN clubs c ON c.id = m.club_id
         WHERE m.character_id = $1",
    )
    .bind(row.id)
    .fetch_optional(&state.pool)
    .await?;
    let progress = ProgressionView::new(u32::try_from(row.xp).unwrap_or(0));
    let appearance = row.appearance.map_or_else(
        || shipped().creator.default_appearance(0),
        |j| crate::catalog::migrate_look(j.0),
    );
    Ok(Json(PlayerCard {
        name: row.name,
        house,
        level: progress.level,
        skills,
        appearance,
        club,
    }))
}

/// This module's routes, merged under `/api` by `http::router`.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    let router = Router::new()
        .route("/players", get(list))
        .route("/players/{name}/card", get(card));
    read_limited(router, PLAYERS_RATE_LIMIT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn searches_are_name_keys_of_at_most_a_name() {
        assert_eq!(search_key(None).unwrap(), "");
        assert_eq!(search_key(Some("  ÉMi ")).unwrap(), "émi");
        assert!(search_key(Some(&"é".repeat(24))).is_ok());
        assert_eq!(
            search_key(Some(&"a".repeat(25))).unwrap_err().code,
            "invalid_query"
        );
    }
}
