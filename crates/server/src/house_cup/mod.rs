//! The House Cup (docs/architecture.md, rules in
//! `data/house_cup.json`, `sim_core::house_cup`).
//!
//! - **Standings**: a house's score for a cup week is the raw sum of its
//!   `house_points` rows with `at` in `[week.start, week.end)`
//!   ([`week_totals`]). The four sources and their daily caps are applied
//!   when points are awarded (`house_points::award_capped` in the class,
//!   find and craft transactions; duels keep their own cap), not here.
//! - **`GET /api/house-cup`** ([`get_cup`]): this week's standings, the time
//!   to the reset, the caller's points this week, the last closed week and
//!   the houses wearing the cup badge (and hanging their banners in the Great
//!   Hall) now. Everything but `mine` is the same for everyone and cached
//!   [`CUP_TTL`], like `GET /api/house-points`; `mine` is one indexed sum
//!   per request.
//! - **The close** ([`close_due`], [`close_week`], [`close_in`]): when a
//!   week has ended and has no `house_cups` row, one transaction writes the
//!   row (winners: `sim_core::house_cup::winners`,
//!   ties shared, none for an empty week) and mails the reward
//!   (`CupRules::reward`, mail kind `house_cup`, ledger reason
//!   `Reason::HouseCup`, both naming `house_cups.id`) to every character of a
//!   winning house with at least `reward.min_points` that week. The PK on
//!   `week_start` makes it idempotent: a second run (or a crash before the
//!   commit, then a restart) never mails twice. The minute job in `main.rs`
//!   calls [`close_due`]; `POST /api/dev/house-cup/close` (dev only,
//!   `dev.rs`) closes the current week early.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{Finish, House};
use serde::Serialize;
use sim_core::house_cup::CupWeek;
use sqlx::{PgConnection, PgPool};

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Player, house_from_db, house_to_db};
use crate::http::ApiError;
use crate::inventory::LOCATION_MAIL;
use crate::items::{ItemError, NewItem};
use crate::ledger::{LedgerError, Reason};
use crate::mail::{self, NewMail};

/// The shared part of `GET /api/house-cup` is computed at most this often.
pub const CUP_TTL: Duration = Duration::from_secs(30);

/// One house's points.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct HousePoints {
    pub house: House,
    pub points: i64,
}

/// A cup week on the wire (Unix ms).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct WeekView {
    pub start_ms: i64,
    pub end_ms: i64,
}

impl From<CupWeek> for WeekView {
    fn from(w: CupWeek) -> Self {
        WeekView {
            start_ms: w.start_ms,
            end_ms: w.end_ms,
        }
    }
}

/// A closed week (`house_cups` row).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ClosedView {
    pub week: WeekView,
    /// The houses on the top score (ties shared); empty if nobody scored.
    pub winners: Vec<House>,
    /// All four houses, `House::ALL` order.
    pub houses: Vec<HousePoints>,
    pub closed_at_ms: i64,
}

/// `GET /api/house-cup`.
#[derive(Debug, Clone, Serialize)]
pub struct CupView {
    /// The running week.
    pub week: WeekView,
    /// Seconds to the reset (the running week's end).
    pub resets_in_secs: i64,
    /// This week's standings: all four houses, `House::ALL` order.
    pub houses: Vec<HousePoints>,
    /// The caller's points this week (all sources).
    pub mine: i64,
    /// The newest closed week (normally last week; after a dev close, this
    /// one), or none yet.
    pub last: Option<ClosedView>,
    /// The houses whose members wear the cup badge on their nameplates and
    /// whose cup banners hang in the Great Hall now: `last.winners` when
    /// `last` is last week or this week, else none.
    pub badge: Vec<House>,
}

/// The part of [`CupView`] that is the same for everyone.
#[derive(Debug, Clone)]
struct Shared {
    week: CupWeek,
    houses: Vec<HousePoints>,
    last: Option<ClosedView>,
    badge: Vec<House>,
}

static SHARED: Mutex<Option<(Instant, Shared)>> = Mutex::new(None);

fn ms_to_time(ms: i64) -> DateTime<Utc> {
    DateTime::<Utc>::from_timestamp_millis(ms).unwrap_or_default()
}

fn in_house_order(rows: &[(String, i64)]) -> Vec<HousePoints> {
    House::ALL
        .into_iter()
        .map(|house| HousePoints {
            house,
            points: rows
                .iter()
                .filter(|(h, _)| h == house.id())
                .map(|r| r.1)
                .sum(),
        })
        .collect()
}

/// Each house's raw sum over `week`, `House::ALL` order.
pub async fn week_totals(pool: &PgPool, week: CupWeek) -> Result<Vec<HousePoints>, sqlx::Error> {
    let rows: Vec<(String, i64)> = sqlx::query_as(
        "SELECT house, COALESCE(SUM(points), 0)::bigint FROM house_points
         WHERE at >= $1 AND at < $2 GROUP BY house",
    )
    .bind(ms_to_time(week.start_ms))
    .bind(ms_to_time(week.end_ms))
    .fetch_all(pool)
    .await?;
    Ok(in_house_order(&rows))
}

/// A character's points over `week`.
pub async fn character_points(
    pool: &PgPool,
    character_id: i64,
    week: CupWeek,
) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar(
        "SELECT COALESCE(SUM(points), 0)::bigint FROM house_points
         WHERE character_id = $1 AND at >= $2 AND at < $3",
    )
    .bind(character_id)
    .bind(ms_to_time(week.start_ms))
    .bind(ms_to_time(week.end_ms))
    .fetch_one(pool)
    .await
}

#[derive(sqlx::FromRow)]
struct CupRow {
    week_start: DateTime<Utc>,
    week_end: DateTime<Utc>,
    winners: Vec<String>,
    totals: sqlx::types::Json<serde_json::Value>,
    closed_at: DateTime<Utc>,
}

/// The newest closed week starting before `before_ms`.
pub async fn last_closed(pool: &PgPool, before_ms: i64) -> Result<Option<ClosedView>, sqlx::Error> {
    let row: Option<CupRow> = sqlx::query_as(
        "SELECT week_start, week_end, winners, totals, closed_at FROM house_cups
         WHERE week_start < $1 ORDER BY week_start DESC LIMIT 1",
    )
    .bind(ms_to_time(before_ms))
    .fetch_optional(pool)
    .await?;
    Ok(row.map(|r| {
        let rows: Vec<(String, i64)> = House::ALL
            .into_iter()
            .map(|h| (h.id().to_owned(), r.totals.0[h.id()].as_i64().unwrap_or(0)))
            .collect();
        ClosedView {
            week: WeekView {
                start_ms: r.week_start.timestamp_millis(),
                end_ms: r.week_end.timestamp_millis(),
            },
            winners: r.winners.iter().filter_map(|h| House::from_id(h)).collect(),
            houses: in_house_order(&rows),
            closed_at_ms: r.closed_at.timestamp_millis(),
        }
    }))
}

async fn shared(pool: &PgPool, now_ms: i64) -> Result<Shared, sqlx::Error> {
    let week = shipped().house_cup.week_of(now_ms);
    let cached = SHARED
        .lock()
        .expect("house cup lock")
        .as_ref()
        .filter(|(at, s)| at.elapsed() < CUP_TTL && s.week == week)
        .map(|(_, s)| s.clone());
    if let Some(s) = cached {
        return Ok(s);
    }
    let houses = week_totals(pool, week).await?;
    let last = last_closed(pool, week.end_ms).await?;
    let badge = last
        .as_ref()
        .filter(|l| l.week.start_ms >= week.prev().start_ms)
        .map(|l| l.winners.clone())
        .unwrap_or_default();
    let s = Shared {
        week,
        houses,
        last,
        badge,
    };
    *SHARED.lock().expect("house cup lock") = Some((Instant::now(), s.clone()));
    Ok(s)
}

/// Forgets the cached standings (after a close, so the badge and banners
/// switch at once).
pub fn forget_cached() {
    *SHARED.lock().expect("house cup lock") = None;
}

/// `GET /api/house-cup` → [`CupView`].
pub async fn get_cup(
    State(state): State<AppState>,
    player: Player,
) -> Result<Json<CupView>, ApiError> {
    let now_ms = Utc::now().timestamp_millis();
    let s = shared(&state.pool, now_ms).await?;
    let mine = character_points(&state.pool, player.character.id, s.week).await?;
    Ok(Json(CupView {
        week: s.week.into(),
        resets_in_secs: s.week.ends_in_ms(now_ms).div_euclid(1000),
        houses: s.houses,
        mine,
        last: s.last,
        badge: s.badge,
    }))
}

/// What a close wrote.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Closed {
    /// `house_cups.id`.
    pub id: i64,
    pub week: CupWeek,
    pub winners: Vec<House>,
    pub houses: Vec<HousePoints>,
    /// Characters mailed the reward (their unread counts changed).
    pub mailed: Vec<i64>,
}

/// The reward mail's kind (`mail.kind`).
pub const MAIL_KIND: &str = "house_cup";

/// Closes `week` in one transaction, as the module docs say. `Ok(None)`
/// when it was already closed (by an earlier run, or by another server or
/// the dev route racing this one: the second waits on the `week_start` key
/// and then finds the row).
pub async fn close_week(pool: &PgPool, week: CupWeek) -> Result<Option<Closed>, sqlx::Error> {
    let mut tx = pool.begin().await?;
    let closed = close_in(&mut tx, week).await?;
    if closed.is_some() {
        tx.commit().await?;
    } else {
        tx.rollback().await?;
    }
    Ok(closed)
}

/// [`close_week`]'s work inside the caller's transaction (nothing is kept
/// unless it commits: a crash before the commit leaves the week open and the
/// next run closes it again, mailing once). In order: the week's standings,
/// the claim (`INSERT .. ON CONFLICT DO NOTHING RETURNING id`, so a week is
/// closed once), the recipients (every character of a winning house with
/// at least `reward.min_points` that week, ties mailing every tied house),
/// their row locks (ascending ids, the mail rule), and one reward mail each
/// with the pennant created in it in the recipient's house colours
/// (`ItemDef::granted_dyes`), both naming the `house_cups` row.
pub async fn close_in(
    conn: &mut PgConnection,
    week: CupWeek,
) -> Result<Option<Closed>, sqlx::Error> {
    let content = shipped();
    let rules = &content.house_cup;
    let (start, end) = (ms_to_time(week.start_ms), ms_to_time(week.end_ms));
    // Cheap first look (the minute job asks every minute).
    let done: Option<i64> = sqlx::query_scalar("SELECT id FROM house_cups WHERE week_start = $1")
        .bind(start)
        .fetch_optional(&mut *conn)
        .await?;
    if done.is_some() {
        return Ok(None);
    }
    let rows: Vec<(String, i64)> = sqlx::query_as(
        "SELECT house, COALESCE(SUM(points), 0)::bigint FROM house_points
         WHERE at >= $1 AND at < $2 GROUP BY house",
    )
    .bind(start)
    .bind(end)
    .fetch_all(&mut *conn)
    .await?;
    let houses = in_house_order(&rows);
    let pairs: Vec<(House, i64)> = houses.iter().map(|h| (h.house, h.points)).collect();
    let winners = sim_core::house_cup::winners(&pairs);
    let totals: serde_json::Map<String, serde_json::Value> = houses
        .iter()
        .map(|h| (h.house.id().to_owned(), h.points.into()))
        .collect();
    let id: Option<i64> = sqlx::query_scalar(
        "INSERT INTO house_cups (week_start, week_end, winners, totals) VALUES ($1, $2, $3, $4)
         ON CONFLICT (week_start) DO NOTHING RETURNING id",
    )
    .bind(start)
    .bind(end)
    .bind(winners.iter().map(|h| h.id()).collect::<Vec<_>>())
    .bind(sqlx::types::Json(serde_json::Value::Object(totals)))
    .fetch_optional(&mut *conn)
    .await?;
    let Some(id) = id else {
        return Ok(None);
    };

    let mut mailed = Vec::new();
    if !winners.is_empty() {
        let recipients: Vec<(i64, i16)> = sqlx::query_as(
            "SELECT h.character_id, c.house FROM house_points h
             JOIN characters c ON c.id = h.character_id
             WHERE h.at >= $1 AND h.at < $2 AND c.house = ANY($3)
             GROUP BY h.character_id, c.house
             HAVING sum(h.points) >= $4
             ORDER BY h.character_id",
        )
        .bind(start)
        .bind(end)
        .bind(
            winners
                .iter()
                .map(|&h| house_to_db(h))
                .collect::<Vec<i16>>(),
        )
        .bind(i64::from(rules.reward.min_points))
        .fetch_all(&mut *conn)
        .await?;
        let ids: Vec<i64> = recipients.iter().map(|r| r.0).collect();
        crate::wallet::lock_characters(&mut *conn, &ids).await?;
        let def = content.catalog.get(rules.reward.item).ok_or_else(|| {
            sqlx::Error::Protocol(format!(
                "reward item {} not in the catalog",
                rules.reward.item
            ))
        })?;
        for (recipient, house) in recipients {
            let Some(house) = house_from_db(house) else {
                continue;
            };
            let (sender, subject, body) = rules.reward_mail(house);
            let mail_id = mail::create(
                &mut *conn,
                &NewMail {
                    recipient,
                    sender_id: None,
                    sender_name: &sender,
                    kind: MAIL_KIND,
                    subject: &subject,
                    body: &body,
                    crowns: 0,
                    ref_id: Some(id),
                    client_key: None,
                    with_items: true,
                },
            )
            .await?;
            let pennant = NewItem {
                owner: recipient,
                item_def: def.id,
                dyes: def.granted_dyes(house, &content.palette),
                finish: Finish::None,
                quantity: 1,
                maker: None,
                location: LOCATION_MAIL,
            };
            mail::deliver_item(
                &mut *conn,
                mail_id,
                recipient,
                &pennant,
                Reason::HouseCup,
                id,
            )
            .await
            .map_err(|e| match e {
                ItemError::Db(e) | ItemError::Ledger(LedgerError::Db(e)) => e,
                other => sqlx::Error::Protocol(format!("house cup reward: {other}")),
            })?;
            mailed.push(recipient);
        }
    }
    Ok(Some(Closed {
        id,
        week,
        winners,
        houses,
        mailed,
    }))
}

/// The minute job: closes the week before the one holding `now_ms` if it
/// has no `house_cups` row yet (only that week: older weeks stay open).
pub async fn close_due(pool: &PgPool, now_ms: i64) -> Result<Option<Closed>, sqlx::Error> {
    let week = shipped().house_cup.week_of(now_ms).prev();
    close_week(pool, week).await
}

pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new().route("/house-cup", get(get_cup))
}
