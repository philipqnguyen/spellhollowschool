//! Dueling: challenges in a venue's dueling area, arena
//! instances, spectators, the duel loop and rewards. Opt-in, and nothing is
//! ever lost: no item or Crown is touched by anything here.
//!
//! **Challenges** ([`world::Duels::on_input`]): both players stand in the
//! same venue area (`WorldCtx::area(e, AreaKind::DuelingClub)`, then
//! `Venues::by_area`), neither is dueling, spectating or has a challenge
//! pending (`Busy`), no block either way (`UnknownTarget`), the challenger
//! has dueling spells (`NoSpells`). Two players challenging each other at
//! once start the duel (the second challenge accepts the first). The
//! Dueling Club teaches the dueling spells a character's level allows
//! while they stand in its area ([`teach`], checked once a second, so a
//! level-up in the club teaches at once). `DuelChallenged` goes to the
//! target (`notice`) and back to the challenger; it ends after
//! `challenge_secs`, on a cancel or decline, when either leaves the area
//! (checked once a second, or at once on a zone change) or disconnects
//! (`DuelChallengeEnded`). Accepting opens an arena
//! (`open_instance(FeatureKind::Arena { duel_id, venue }, arena_layout)`,
//! with a random 63-bit `duel_key` for the record) and enters both at their
//! `Venue::duelists` marks; both duelists are the arena's priority entities.
//!
//! **The duel**: `sim_core::Duel::new` with each side's loadout at the start
//! (`SchoolState::loadout`, checked against the learned spells and the
//! level's slots again; the default loadout if it no longer fits), stepped
//! every world tick from [`world::Duels::tick`] with the duelists'
//! positions (arena x and z, clamped to the venue's ring; the stage's edges
//! already stop moves, since nobody steps down 0.9 m) and the cast received
//! since the last tick (`DuelCast`; the latest wins). Events go to the
//! arena's members only (`DuelEvents`; `Refused` to its duelist only);
//! `play_cast` on a cast start. Spectators (`DuelSpectate` from the venue
//! area, up to `max_spectators`) enter on the floor around the stage and get
//! `DuelStart` with `Duel::snapshot`; nothing they send changes a duel. A
//! duelist leaving the arena (`LeaveInstance`) or forfeiting loses at once;
//! one who disconnects has `disconnect_grace_ms` to come back (the duel goes
//! on, they stand still; a new login is taken straight back into the arena
//! and everyone gets a fresh `DuelStart`), then loses by `Disconnect`. The
//! world already suspends potions and boosts in arenas (`ZoneRules`).
//!
//! **The end** ([`record`]): one transaction per duel, both characters
//! locked in id order: the `duels` row (unique `duel_key`: a retry finds it
//! and grants nothing), `progression::grant_xp` for each duelist
//! (`DuelWin`/`DuelLoss`/`DuelDraw`, ref the duel id; the daily duel cap
//! applies, and nothing below `min_fight_ms` of fighting or past
//! `pair_daily_max` rewarded duels a day between the two, counted in
//! `progression::caps` with group `pair:<other id>`), `house_points::award`
//! for a win (`win_house_points`, at most `house_point_wins_daily` a day,
//! group `duel_points`). Then `DuelResult` and `Progress`; after
//! `return_delay_ms` everyone returns (`close_instance`).
//!
//! **The server stopping** ([`record_no_contest`]): a duel still
//! in its countdown or fight when the world stops is recorded as no
//! contest: result `'draw'`, reason [`REASON_SHUTDOWN`] (the server's word,
//! not a protocol reason), both poise as they stood, the fighting time so
//! far, no XP and no house points, and nothing sent to the duelists (they
//! are being disconnected). The world leaves those duels in
//! [`world::Unfinished`] as it stops, and `Realtime::shutdown` records them
//! before the pool closes.
//!
//! Routes ([`routes`]): `GET /api/duels?limit=&after=` (the character's
//! duels, newest first).

pub mod world;

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{DuelEndReason, DuelRewards, DuelSide, SpellId, XpSource};
use serde::Serialize;
use sqlx::{PgConnection, PgPool};

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Page, PageQuery, Player, house_from_db};
use crate::house_points::{self, PointsSource};
use crate::http::{ApiError, ApiQuery};
use crate::progression::{self, XpGrant, caps};

/// The daily counter of wins that gave house points (`daily_caps.cap`).
pub const CAP_POINTS: &str = "duel_points";

/// The daily counter of rewarded duels against one opponent.
pub fn pair_cap(other: i64) -> String {
    format!("pair:{other}")
}

/// A finished duel, as the world hands it to [`record`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finished {
    /// The arena's random key (`duels.duel_key`): recording twice is a no-op.
    pub duel_key: i64,
    pub venue: String,
    /// Characters A (the challenger) and B.
    pub characters: [i64; 2],
    /// `None`: a draw.
    pub winner: Option<DuelSide>,
    pub reason: DuelEndReason,
    pub poise: [u16; 2],
    /// Fighting time (after the countdown).
    pub fought_ms: u32,
    pub started_at: DateTime<Utc>,
    /// Also the day the daily caps count in (UTC).
    pub ended_at: DateTime<Utc>,
}

/// What [`record`] did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Recorded {
    /// `duels.id` (the grants' `ref_id`).
    pub id: i64,
    /// This duel was recorded before: nothing was granted now.
    pub duplicate: bool,
    /// Each side's rewards (`DuelResult.rewards`).
    pub rewards: [DuelRewards; 2],
    /// Each side's XP grant, if one was made now (for `Progress`).
    pub grants: [Option<XpGrant>; 2],
}

/// The XP source and amount a side earns from a result, before any cap
/// (`data/progression.json` `xp.duel_*`; never scaled by level).
pub fn xp_for(winner: Option<DuelSide>, side: DuelSide) -> (XpSource, u32) {
    let xp = &shipped().progression.xp;
    match winner {
        None => (XpSource::DuelDraw, xp.duel_draw),
        Some(w) if w == side => (XpSource::DuelWin, xp.duel_win),
        Some(_) => (XpSource::DuelLoss, xp.duel_loss),
    }
}

fn result_id(winner: Option<DuelSide>) -> &'static str {
    match winner {
        Some(DuelSide::A) => "a",
        Some(DuelSide::B) => "b",
        None => "draw",
    }
}

pub fn reason_id(reason: DuelEndReason) -> &'static str {
    match reason {
        DuelEndReason::Poise => "poise",
        DuelEndReason::Timeout => "timeout",
        DuelEndReason::Forfeit => "forfeit",
        DuelEndReason::Disconnect => "disconnect",
    }
}

const NO_REWARDS: DuelRewards = DuelRewards {
    xp: 0,
    capped: false,
    house_points: 0,
};

/// Records a finished duel and grants its rewards, in one transaction:
/// both characters locked in id order, the `duels` row, then each side's
/// XP (daily caps, the same-opponent limit) and the winner's house points.
/// A duel recorded before (same `duel_key`) answers that record with
/// `duplicate` and grants nothing, so a retry is always safe. No item or
/// Crown row is read or written.
pub async fn record(pool: &PgPool, f: &Finished) -> Result<Recorded, sqlx::Error> {
    let rules = &shipped().duel_rules.rewards;
    let [a, b] = f.characters;
    let mut tx = pool.begin().await?;
    let mut ids = [a, b];
    ids.sort_unstable();
    let houses: Vec<(i64, i16)> = sqlx::query_as(
        "SELECT id, house FROM characters WHERE id = ANY($1) ORDER BY id FOR NO KEY UPDATE",
    )
    .bind(&ids[..])
    .fetch_all(&mut *tx)
    .await?;
    if let Some(done) = recorded(&mut tx, f.duel_key).await? {
        return Ok(done);
    }
    if houses.len() != 2 {
        // A character was deleted while dueling: nothing to record.
        return Err(sqlx::Error::RowNotFound);
    }
    let inserted: Option<i64> = sqlx::query_scalar(
        "INSERT INTO duels (duel_key, venue, a_id, b_id, result, reason, a_poise, b_poise,
                            fought_ms, started_at, ended_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
         ON CONFLICT (duel_key) DO NOTHING
         RETURNING id",
    )
    .bind(f.duel_key)
    .bind(&f.venue)
    .bind(a)
    .bind(b)
    .bind(result_id(f.winner))
    .bind(reason_id(f.reason))
    .bind(i16::try_from(f.poise[0]).unwrap_or(i16::MAX))
    .bind(i16::try_from(f.poise[1]).unwrap_or(i16::MAX))
    .bind(i32::try_from(f.fought_ms).unwrap_or(i32::MAX))
    .bind(f.started_at)
    .bind(f.ended_at)
    .fetch_optional(&mut *tx)
    .await?;
    let Some(id) = inserted else {
        // Unreachable under the character locks (checked above).
        return Err(sqlx::Error::Protocol("duel record raced itself".into()));
    };

    let day = f.ended_at.date_naive();
    let eligible = f.fought_ms >= rules.min_fight_ms;
    let mut rewards = [NO_REWARDS; 2];
    let mut grants = [None; 2];
    let mut paired = [false; 2];
    for side in DuelSide::BOTH {
        if !eligible {
            continue;
        }
        let i = side.index();
        let (me, other) = (f.characters[i], f.characters[side.other().index()]);
        let pair = caps::take(
            &mut tx,
            me,
            day,
            &pair_cap(other),
            1,
            u32::from(rules.pair_daily_max),
        )
        .await?;
        if pair == 0 {
            // Enough duels against this opponent today: nothing more.
            rewards[i].capped = true;
            continue;
        }
        paired[i] = true;
        let (source, amount) = xp_for(f.winner, side);
        let grant =
            progression::grant_xp(&mut tx, me, source, Some(id), amount, f.ended_at).await?;
        rewards[i].xp = grant.granted;
        rewards[i].capped = grant.capped;
        grants[i] = Some(grant);
    }
    if let Some(w) = f.winner
        && paired[w.index()]
        && rules.win_house_points > 0
    {
        let me = f.characters[w.index()];
        let house = houses
            .iter()
            .find(|r| r.0 == me)
            .and_then(|r| house_from_db(r.1));
        let allowed = caps::take(
            &mut tx,
            me,
            day,
            CAP_POINTS,
            1,
            u32::from(rules.house_point_wins_daily),
        )
        .await?;
        if allowed == 1
            && let Some(house) = house
            && house_points::award(
                &mut tx,
                house,
                i32::from(rules.win_house_points),
                Some(me),
                PointsSource::Duel,
                Some(id),
            )
            .await?
        {
            rewards[w.index()].house_points = rules.win_house_points;
        }
    }
    let points = rewards
        .iter()
        .map(|r| i32::from(r.house_points))
        .sum::<i32>();
    sqlx::query("UPDATE duels SET a_xp = $2, b_xp = $3, house_points = $4 WHERE id = $1")
        .bind(id)
        .bind(i32::try_from(rewards[0].xp).unwrap_or(i32::MAX))
        .bind(i32::try_from(rewards[1].xp).unwrap_or(i32::MAX))
        .bind(points)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(Recorded {
        id,
        duplicate: false,
        rewards,
        grants,
    })
}

/// The record of a duel already written, as [`record`] answers a retry.
async fn recorded(conn: &mut PgConnection, duel_key: i64) -> Result<Option<Recorded>, sqlx::Error> {
    let row: Option<(i64, String, i32, i32, i32)> = sqlx::query_as(
        "SELECT id, result, a_xp, b_xp, house_points FROM duels WHERE duel_key = $1",
    )
    .bind(duel_key)
    .fetch_optional(conn)
    .await?;
    Ok(row.map(|(id, result, a_xp, b_xp, points)| {
        let points = u16::try_from(points).unwrap_or(0);
        let side = |xp: i32, won: bool| DuelRewards {
            xp: u32::try_from(xp).unwrap_or(0),
            capped: false,
            house_points: if won { points } else { 0 },
        };
        Recorded {
            id,
            duplicate: true,
            rewards: [side(a_xp, result == "a"), side(b_xp, result == "b")],
            grants: [None; 2],
        }
    }))
}

/// `duels.reason` of a duel cut short by the server stopping (migration
/// 0018). Not a `protocol::DuelEndReason`: nobody is sent a result for it.
pub const REASON_SHUTDOWN: &str = "shutdown";

/// A duel still in its countdown or fight when the server stopped, as the
/// world leaves it for [`record_no_contest`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NoContest {
    /// The arena's random key (`duels.duel_key`): recording twice is a no-op.
    pub duel_key: i64,
    pub venue: String,
    /// Characters A (the challenger) and B.
    pub characters: [i64; 2],
    /// Each side's poise as it stood.
    pub poise: [u16; 2],
    /// Fighting time so far (0 in the countdown).
    pub fought_ms: u32,
    pub started_at: DateTime<Utc>,
    pub ended_at: DateTime<Utc>,
}

/// Records a duel the server's stop cut short as no contest (see the
/// module docs): one `duels` row, result `'draw'`, reason
/// [`REASON_SHUTDOWN`], 0 XP and 0 house points, and no grant of any kind.
/// The unique `duel_key` makes it idempotent: true if the row was written
/// now, false if the duel was already recorded (a finished duel's record
/// that got there first, or a second call).
pub async fn record_no_contest(pool: &PgPool, n: &NoContest) -> Result<bool, sqlx::Error> {
    let [a, b] = n.characters;
    let inserted: Option<i64> = sqlx::query_scalar(
        "INSERT INTO duels (duel_key, venue, a_id, b_id, result, reason, a_poise, b_poise,
                            fought_ms, started_at, ended_at)
         VALUES ($1, $2, $3, $4, 'draw', $5, $6, $7, $8, $9, $10)
         ON CONFLICT (duel_key) DO NOTHING
         RETURNING id",
    )
    .bind(n.duel_key)
    .bind(&n.venue)
    .bind(a)
    .bind(b)
    .bind(REASON_SHUTDOWN)
    .bind(i16::try_from(n.poise[0]).unwrap_or(i16::MAX))
    .bind(i16::try_from(n.poise[1]).unwrap_or(i16::MAX))
    .bind(i32::try_from(n.fought_ms).unwrap_or(i32::MAX))
    .bind(n.started_at)
    .bind(n.ended_at)
    .fetch_optional(pool)
    .await?;
    Ok(inserted.is_some())
}

/// Records every duel in `unfinished` as no contest ([`record_no_contest`]),
/// logging each failure (the server is stopping: nothing else to do).
/// Returns how many rows were written.
pub async fn record_no_contests(pool: &PgPool, unfinished: &[NoContest]) -> usize {
    let mut written = 0;
    for n in unfinished {
        match record_no_contest(pool, n).await {
            Ok(true) => written += 1,
            Ok(false) => {}
            Err(e) => tracing::error!(
                error = %e,
                duel_key = n.duel_key,
                "recording a duel cut short by the shutdown failed"
            ),
        }
    }
    written
}

/// [`record`], tried a few times (a lost connection, a serialization
/// failure): safe, since a second attempt finds the first if it committed.
pub async fn record_with_retry(pool: &PgPool, f: &Finished) -> Result<Recorded, sqlx::Error> {
    let mut attempt = 0;
    loop {
        match record(pool, f).await {
            Err(e) if attempt < 2 && !matches!(e, sqlx::Error::RowNotFound) => {
                attempt += 1;
                tracing::warn!(error = %e, duel_key = f.duel_key, "duel record failed; retrying");
                tokio::time::sleep(std::time::Duration::from_millis(200 * attempt)).await;
            }
            other => return other,
        }
    }
}

/// What the Dueling Club taught (`DuelDone::Taught`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Taught {
    pub learned: Vec<SpellId>,
    pub loadout: Vec<SpellId>,
    pub new: Vec<SpellId>,
}

/// Teaches a character of `level` the spells venue `venue` teaches
/// (`progression::teach`) and reads back the spellbook.
pub async fn teach(
    pool: &PgPool,
    character_id: i64,
    venue: &str,
    level: u8,
) -> Result<Taught, sqlx::Error> {
    let mut tx = pool.begin().await?;
    let new = progression::teach(&mut tx, character_id, venue, level).await?;
    let (learned, loadout) = progression::spellbook(&mut tx, character_id, level).await?;
    tx.commit().await?;
    Ok(Taught {
        learned,
        loadout,
        new,
    })
}

// ---------------------------------------------------------------- HTTP

#[derive(Debug, sqlx::FromRow)]
struct DuelRow {
    id: i64,
    /// `None` for a duelist whose character has been deleted.
    a_id: Option<i64>,
    b_id: Option<i64>,
    result: String,
    reason: String,
    a_xp: i32,
    b_xp: i32,
    house_points: i32,
    fought_ms: i32,
    ended_at: DateTime<Utc>,
    a_name: Option<String>,
    b_name: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Opponent {
    /// `None` once the opponent's character has been deleted (`name` is then
    /// [`DELETED_OPPONENT`]).
    pub character_id: Option<i64>,
    pub name: String,
}

/// The opponent's name in the history once their character is deleted.
pub const DELETED_OPPONENT: &str = "A former student";

/// One duel as `GET /api/duels` shows it to one of its duelists.
#[derive(Debug, Clone, Serialize)]
pub struct DuelRecord {
    pub id: i64,
    pub opponent: Opponent,
    /// `"win" | "loss" | "draw"`, for the character asking.
    pub result: &'static str,
    pub reason: String,
    /// XP this character got from it.
    pub xp: i64,
    /// House points this character won.
    pub house_points: i64,
    pub fought_ms: i64,
    pub ended_at: DateTime<Utc>,
}

impl DuelRow {
    fn view(self, me: i64) -> DuelRecord {
        let mine = if self.a_id == Some(me) { "a" } else { "b" };
        let (opponent, name, xp) = if self.a_id == Some(me) {
            (self.b_id, self.b_name, self.a_xp)
        } else {
            (self.a_id, self.a_name, self.b_xp)
        };
        let result = match self.result.as_str() {
            "draw" => "draw",
            r if r == mine => "win",
            _ => "loss",
        };
        DuelRecord {
            id: self.id,
            opponent: Opponent {
                character_id: opponent,
                name: match (opponent, name) {
                    (Some(_), Some(name)) => name,
                    _ => DELETED_OPPONENT.to_owned(),
                },
            },
            result,
            reason: self.reason,
            xp: i64::from(xp),
            house_points: if result == "win" {
                i64::from(self.house_points)
            } else {
                0
            },
            fought_ms: i64::from(self.fought_ms),
            ended_at: self.ended_at,
        }
    }
}

/// `GET /api/duels?limit=&after=`: the character's duels, newest first (one
/// index scan per side, `duels (a_id, id)` and `duels (b_id, id)`).
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<DuelRecord>>, ApiError> {
    let me = player.character.id;
    let after: Option<i64> = page.after()?;
    let rows: Vec<DuelRow> = sqlx::query_as(
        "SELECT d.id, d.a_id, d.b_id, d.result, d.reason, d.a_xp, d.b_xp, d.house_points,
                d.fought_ms, d.ended_at, ca.name AS a_name, cb.name AS b_name
         FROM ((SELECT * FROM duels WHERE a_id = $1 AND ($2::bigint IS NULL OR id < $2)
                ORDER BY id DESC LIMIT $3)
               UNION ALL
               (SELECT * FROM duels WHERE b_id = $1 AND ($2::bigint IS NULL OR id < $2)
                ORDER BY id DESC LIMIT $3)) d
         LEFT JOIN characters ca ON ca.id = d.a_id
         LEFT JOIN characters cb ON cb.id = d.b_id
         ORDER BY d.id DESC LIMIT $3",
    )
    .bind(me)
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    let page = Page::from_rows(rows, page.limit()?, |r| r.id);
    Ok(Json(page.map(|r| r.view(me))))
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new().route("/duels", get(list))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn results_give_the_data_amounts_whatever_the_level() {
        let xp = &shipped().progression.xp;
        assert_eq!(
            xp_for(Some(DuelSide::A), DuelSide::A),
            (XpSource::DuelWin, xp.duel_win)
        );
        assert_eq!(
            xp_for(Some(DuelSide::A), DuelSide::B),
            (XpSource::DuelLoss, xp.duel_loss)
        );
        assert_eq!(
            xp_for(None, DuelSide::B),
            (XpSource::DuelDraw, xp.duel_draw)
        );
        assert!(xp.duel_win > xp.duel_draw && xp.duel_draw > xp.duel_loss);
        assert_eq!(result_id(None), "draw");
        assert_eq!(reason_id(DuelEndReason::Disconnect), "disconnect");
    }

    #[test]
    fn records_read_from_either_side() {
        let row = |a_name: &str| DuelRow {
            id: 9,
            a_id: Some(1),
            b_id: Some(2),
            result: "a".into(),
            reason: "poise".into(),
            a_xp: 40,
            b_xp: 15,
            house_points: 2,
            fought_ms: 20_000,
            ended_at: Utc::now(),
            a_name: Some(a_name.into()),
            b_name: Some("Bea".into()),
        };
        let a = row("Ada").view(1);
        assert_eq!((a.result, a.xp, a.house_points), ("win", 40, 2));
        assert_eq!(a.opponent.name, "Bea");
        let b = row("Ada").view(2);
        assert_eq!((b.result, b.xp, b.house_points), ("loss", 15, 0));
        assert_eq!(
            (b.opponent.character_id, b.opponent.name.as_str()),
            (Some(1), "Ada")
        );
        // The winner's character was deleted since.
        let gone = DuelRow {
            a_id: None,
            a_name: None,
            ..row("Ada")
        }
        .view(2);
        assert_eq!((gone.result, gone.opponent.character_id), ("loss", None));
        assert_eq!(gone.opponent.name, DELETED_OPPONENT);
    }
}
