//! House points (shared): an append-only ledger
//! (`house_points`) that classes and duels write to (finds and
//! crafts too, `crate::house_cup` sums them by week), and the all-time
//! totals per house (`GET /api/house-points`).
//!
//! [`award`] runs inside the caller's transaction, after the character's
//! row lock (it touches no character or item rows, so it fits any lock
//! order). An award that names a `ref_id` (the class session, the duel) is
//! recorded once per source, ref and character: a retry adds nothing.
//!
//! Classes, finds and crafts award through [`award_capped`], under
//! the House Cup's per-player daily caps (`data/house_cup.json`, cap groups
//! `hp_class`, `hp_find`, `hp_craft` in `daily_caps`); duels keep their own
//! cap, counted in wins.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::House;
use serde::Serialize;
use sim_core::house_cup::CupSource;
use sqlx::PgConnection;

use crate::AppState;
use crate::http::ApiError;
use crate::progression::caps;

/// What earned the points (`house_points.source`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PointsSource {
    Class,
    Duel,
    Admin,
    /// A find that turned something up (no `ref_id`: finds have no
    /// row; the find's ledger rows share the transaction's `at`).
    Find,
    /// NPCs: a house ghost's daily gift (`ref_id`: `npc_gifts.id`; cap
    /// group `hp_npc`).
    Npc,
    /// A completed craft of skill `min_skill` or more (`ref_id`:
    /// `crafts.id`).
    Craft,
}

impl PointsSource {
    pub fn id(self) -> &'static str {
        match self {
            PointsSource::Class => "class",
            PointsSource::Duel => "duel",
            PointsSource::Admin => "admin",
            PointsSource::Find => "find",
            PointsSource::Craft => "craft",
            PointsSource::Npc => "npc",
        }
    }
}

/// Records `points` for `house` (earned by `character_id`). Zero points
/// record nothing. True if a row was written, false for nothing to record
/// or an award of the same `(source, ref_id, character_id)` made before.
pub async fn award(
    conn: &mut PgConnection,
    house: House,
    points: i32,
    character_id: Option<i64>,
    source: PointsSource,
    ref_id: Option<i64>,
) -> Result<bool, sqlx::Error> {
    if points == 0 {
        return Ok(false);
    }
    let id: Option<i64> = sqlx::query_scalar(
        "INSERT INTO house_points (house, points, character_id, source, ref_id)
         VALUES ($1, $2, $3, $4, $5)
         ON CONFLICT (source, ref_id, character_id) WHERE ref_id IS NOT NULL DO NOTHING
         RETURNING id",
    )
    .bind(house.id())
    .bind(points)
    .bind(character_id)
    .bind(source.id())
    .bind(ref_id)
    .fetch_optional(conn)
    .await?;
    Ok(id.is_some())
}

/// The House Cup source a points source counts under (`admin`
/// has none: it is never capped).
pub fn cup_source(source: PointsSource) -> Option<CupSource> {
    match source {
        PointsSource::Class => Some(CupSource::Class),
        PointsSource::Duel => Some(CupSource::Duel),
        PointsSource::Find => Some(CupSource::Find),
        PointsSource::Craft => Some(CupSource::Craft),
        PointsSource::Npc => Some(CupSource::Npc),
        PointsSource::Admin => None,
    }
}

/// [`award`] under the source's per-player daily cap
/// (`data/house_cup.json` `sources.<source>.daily_cap`, cap group
/// `CupSource::cap_group`, the UTC day of `now`): records at most what is
/// left of the day's cap and returns the points recorded (0 when the cap is
/// used up). In the caller's transaction, after the character's row lock.
///
/// The cap is taken before the row is written, so the caller must award
/// each `(source, ref_id)` once (a class attendance's `rewarded_at`, a new
/// craft, a claimed find cooldown). Duels keep their own cap counted in
/// wins (`dueling`'s `duel_points`) and do not come through here: a source
/// without a cap group records `want` as it is.
pub async fn award_capped(
    conn: &mut PgConnection,
    house: House,
    want: u32,
    character_id: i64,
    source: PointsSource,
    ref_id: Option<i64>,
    now: DateTime<Utc>,
) -> Result<u32, sqlx::Error> {
    if want == 0 {
        return Ok(0);
    }
    let rules = &crate::catalog::shipped().house_cup;
    let granted = match cup_source(source).and_then(|s| s.cap_group().map(|g| (s, g))) {
        Some((s, group)) => {
            let limit = u32::from(rules.rule(s).daily_cap);
            caps::take(
                &mut *conn,
                character_id,
                now.date_naive(),
                group,
                want,
                limit,
            )
            .await?
        }
        None => want,
    };
    if granted == 0 {
        return Ok(0);
    }
    let points = i32::try_from(granted).unwrap_or(i32::MAX);
    let wrote = award(conn, house, points, Some(character_id), source, ref_id).await?;
    Ok(if wrote { granted } else { 0 })
}

/// One house's total.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct HouseTotal {
    pub house: House,
    pub points: i64,
}

/// Every house's total, in `House::ALL` order (houses without points: 0).
pub async fn totals(pool: &sqlx::PgPool) -> Result<Vec<HouseTotal>, sqlx::Error> {
    let rows: Vec<(String, i64)> = sqlx::query_as(
        "SELECT house, COALESCE(SUM(points), 0)::bigint FROM house_points GROUP BY house",
    )
    .fetch_all(pool)
    .await?;
    Ok(House::ALL
        .into_iter()
        .map(|house| HouseTotal {
            house,
            points: rows
                .iter()
                .find(|(h, _)| h == house.id())
                .map_or(0, |r| r.1),
        })
        .collect())
}

#[derive(Debug, Serialize)]
pub struct TotalsView {
    pub houses: Vec<HouseTotal>,
}

/// Totals change slowly and are the same for everyone: computed at most
/// every [`TOTALS_TTL`].
const TOTALS_TTL: Duration = Duration::from_secs(30);

static TOTALS: Mutex<Option<(Instant, Vec<HouseTotal>)>> = Mutex::new(None);

/// `GET /api/house-points` → `{ houses: [{ house, points }] }`. Public (the
/// hourglasses stand in the Entrance Hall for everyone to see).
pub async fn get_totals(State(state): State<AppState>) -> Result<Json<TotalsView>, ApiError> {
    let cached = TOTALS
        .lock()
        .expect("totals lock")
        .as_ref()
        .filter(|(at, _)| at.elapsed() < TOTALS_TTL)
        .map(|(_, t)| t.clone());
    let houses = match cached {
        Some(t) => t,
        None => {
            let t = totals(&state.pool).await?;
            *TOTALS.lock().expect("totals lock") = Some((Instant::now(), t.clone()));
            t
        }
    };
    Ok(Json(TotalsView { houses }))
}

pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new().route("/house-points", get(get_totals))
}
