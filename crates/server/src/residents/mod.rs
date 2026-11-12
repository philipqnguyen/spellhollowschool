//! NPCs in the open castle (docs/architecture.md, "NPCs"): the house ghosts, the resident staff, the shopkeepers
//! and the background students, spawned into the castle zone as NPC
//! entities and moved along their routines on the game clock
//! (`sim_core::npcs::RoutinePlan::pose`), and their daily gifts.
//!
//! Talking portraits are the client's alone: nothing here knows them. Every
//! line any NPC says is the client's too (`NpcBook.pick`, local to the
//! talking player): the server decides only reach, the rate limit and the
//! gift.
//!
//! - [`world`]: the runtime on the world thread (`realtime::ext` seam:
//!   `Residents::{on_input, on_left, tick}`; `tick` at the snapshot rate).
//! - [`plan`]: the routines planned against the castle (once per process
//!   and layout; a layout that is not the shipped castle plans nobody).
//! - [`claim`]: today's gift in one transaction (the `npc_gifts` row for
//!   (character, npc, UTC day) is the claim; the roll is
//!   `GiftTable::roll(gift_seed(..))`; lore via `lore_unlocks` (column
//!   `npc`), items via `items::give` (`Reason::NpcGift`), house points via
//!   `house_points::award_capped` (`PointsSource::Npc`), a hint via the
//!   class hint's rule). No XP.
//! - [`audit`]: the ledger checks for NPC gifts (each gift row explains
//!   exactly what it wrote), for tests and `ledger::audit` to call.

pub mod audit;
pub mod plan;
pub mod world;

use std::collections::HashSet;

use chrono::{DateTime, NaiveDate, Utc};
use protocol::{House, NpcGift};
use sim_core::npcs::{GiftDef, GiftKind, gift_day, gift_seed, roll_quantity};
use sqlx::{PgConnection, PgPool};

use crate::catalog::shipped;
use crate::house_points::{self, PointsSource};
use crate::inventory::LOCATION_INVENTORY;
use crate::items::{self, ItemError, NewItem};
use crate::ledger::Reason;

/// What a claim came to.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Claimed {
    /// Today's gift, given now (or given by an earlier attempt of this same
    /// claim that committed: the row's `gift`).
    Gift(NpcGift),
    /// This NPC already gave this character today's gift.
    AlreadyToday,
    /// Nothing to give (no table, another house's ghost, or nothing in the
    /// table is available): no row is written.
    Nothing,
}

/// Attempts of one claim: a failed attempt (a dropped connection, a
/// serialization failure) is retried, and a retry that finds the row its
/// own earlier attempt committed answers that row's gift.
const CLAIM_ATTEMPTS: u32 = 3;

/// Whether NPC `npc` gives gifts to a character of `house` at all (a ghost
/// to its own house only; students, shopkeepers and portraits never).
pub fn gives_to(npc: &str, house: House) -> bool {
    let data = &shipped().npcs;
    let (Some(def), Some(table)) = (data.roster.get(npc), data.gifts.table(npc)) else {
        return false;
    };
    table.gives_to(def.house(), house)
}

/// Claims NPC `npc`'s gift for today (`sim_core::npcs::gift_day(unix_ms)`)
/// for character `character_id` of `house`.
///
/// One transaction: lock the character, insert the `npc_gifts` row (`ON
/// CONFLICT DO NOTHING`; a conflict answers `AlreadyToday`), roll among the
/// available kinds, grant, store the gift in the row, commit. A failed
/// attempt is retried; a retry that finds the row (the earlier attempt's
/// commit landed but its answer was lost) answers the stored gift.
pub async fn claim(
    pool: &PgPool,
    character_id: i64,
    house: House,
    npc: &str,
    unix_ms: i64,
) -> Result<Claimed, sqlx::Error> {
    if !gives_to(npc, house) {
        return Ok(Claimed::Nothing);
    }
    let mut last = None;
    for attempt in 0..CLAIM_ATTEMPTS {
        if attempt > 0 {
            match given_today(pool, character_id, npc, unix_ms).await {
                Ok(Some(gift)) => return Ok(Claimed::Gift(gift)),
                Ok(None) => {}
                Err(e) => {
                    last = Some(e);
                    continue;
                }
            }
        }
        match claim_once(pool, character_id, house, npc, unix_ms).await {
            Ok(c) => return Ok(c),
            Err(e) => {
                tracing::debug!(npc, character_id, attempt, "npc gift claim failed: {e}");
                last = Some(e);
            }
        }
    }
    Err(last.unwrap_or(sqlx::Error::PoolTimedOut))
}

/// What NPC `npc` gave character `character_id` on the UTC day of
/// `unix_ms`, if it gave anything (the row's `gift`): what a retried claim
/// answers.
pub async fn given_today(
    pool: &PgPool,
    character_id: i64,
    npc: &str,
    unix_ms: i64,
) -> Result<Option<NpcGift>, sqlx::Error> {
    let gift: Option<sqlx::types::Json<serde_json::Value>> = sqlx::query_scalar(
        "SELECT gift FROM npc_gifts WHERE character_id = $1 AND npc = $2 AND day = $3",
    )
    .bind(character_id)
    .bind(npc)
    .bind(day_date(unix_ms))
    .fetch_optional(pool)
    .await?;
    Ok(gift.and_then(|g| serde_json::from_value(g.0).ok()))
}

/// The UTC day of `unix_ms` as a date (`npc_gifts.day`, the House Cup's
/// cap day).
pub fn day_date(unix_ms: i64) -> NaiveDate {
    DateTime::<Utc>::from_timestamp_millis(gift_day(unix_ms) * 86_400_000)
        .map(|t| t.date_naive())
        .unwrap_or_default()
}

fn db_err(e: ItemError) -> sqlx::Error {
    match e {
        ItemError::Db(e) => e,
        other => sqlx::Error::Protocol(format!("npc gift: {other}")),
    }
}

/// One attempt of [`claim`].
async fn claim_once(
    pool: &PgPool,
    character_id: i64,
    house: House,
    npc: &str,
    unix_ms: i64,
) -> Result<Claimed, sqlx::Error> {
    let content = shipped();
    let Some(table) = content.npcs.gifts.table(npc) else {
        return Ok(Claimed::Nothing);
    };
    let day = day_date(unix_ms);
    let now = DateTime::<Utc>::from_timestamp_millis(unix_ms).unwrap_or_else(Utc::now);
    let mut tx = pool.begin().await?;
    crate::crafting::lock_character(&mut tx, character_id).await?;
    let row: Option<i64> = sqlx::query_scalar(
        "INSERT INTO npc_gifts (character_id, npc, day, at, gift) VALUES ($1, $2, $3, $4, '{}')
         ON CONFLICT (character_id, npc, day) DO NOTHING
         RETURNING id",
    )
    .bind(character_id)
    .bind(npc)
    .bind(day)
    .bind(now)
    .fetch_optional(&mut *tx)
    .await?;
    let Some(row) = row else {
        tx.rollback().await?;
        return Ok(Claimed::AlreadyToday);
    };

    // What is available today: only what the table could roll is looked up.
    let kinds: HashSet<GiftKind> = table.entries.iter().map(|e| e.gift.kind()).collect();
    let next_lore = if kinds.contains(&GiftKind::Lore) {
        next_lore(&mut tx, character_id).await?
    } else {
        None
    };
    let rule = content.house_cup.rule(sim_core::CupSource::Npc);
    let point_room = if kinds.contains(&GiftKind::HousePoint) {
        let used = crate::progression::caps::used(&mut tx, character_id, day, "hp_npc").await?;
        used < u32::from(rule.daily_cap)
    } else {
        false
    };
    let seed = gift_seed(character_id, npc, gift_day(unix_ms));
    let hint = if kinds.contains(&GiftKind::Hint) {
        crate::classes::lore::hint_seeded(&mut tx, character_id, house, seed).await?
    } else {
        None
    };
    let available = |k: GiftKind| match k {
        GiftKind::Lore => next_lore.is_some(),
        GiftKind::Item => true,
        GiftKind::HousePoint => point_room,
        GiftKind::Hint => hint.is_some(),
    };
    let Some(def) = table.roll(seed, available) else {
        tx.rollback().await?;
        return Ok(Claimed::Nothing);
    };

    let gift = match def {
        GiftDef::Lore => {
            let entry = next_lore.expect("available");
            sqlx::query(
                "INSERT INTO lore_unlocks (character_id, lore_id, npc, unlocked_at)
                 VALUES ($1, $2, $3, $4)",
            )
            .bind(character_id)
            .bind(i32::from(entry))
            .bind(npc)
            .bind(now)
            .execute(&mut *tx)
            .await?;
            let title = content
                .lore
                .get(entry)
                .map(|e| e.title.clone())
                .unwrap_or_default();
            NpcGift::Lore {
                lore_id: entry,
                title,
            }
        }
        GiftDef::Item { item, quantity } => {
            let n = roll_quantity(*quantity, seed);
            let new = NewItem::plain(character_id, *item, u32::from(n), LOCATION_INVENTORY);
            items::give(&mut tx, &new, Reason::NpcGift, Some(row))
                .await
                .map_err(db_err)?;
            NpcGift::Item {
                item_def: *item,
                quantity: n,
            }
        }
        GiftDef::HousePoint => {
            let want = u32::from(rule.points.unwrap_or(1));
            let granted = house_points::award_capped(
                &mut tx,
                house,
                want,
                character_id,
                PointsSource::Npc,
                Some(row),
                now,
            )
            .await?;
            if granted == 0 {
                // The room was checked above under the character's lock.
                tx.rollback().await?;
                return Ok(Claimed::Nothing);
            }
            NpcGift::HousePoint {
                points: u8::try_from(granted).unwrap_or(u8::MAX),
            }
        }
        GiftDef::Hint => NpcGift::Hint {
            place: hint.expect("available"),
        },
    };
    let json = serde_json::to_value(&gift).map_err(|e| sqlx::Error::Protocol(e.to_string()))?;
    sqlx::query("UPDATE npc_gifts SET gift = $2 WHERE id = $1")
        .bind(row)
        .bind(sqlx::types::Json(json))
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(Claimed::Gift(gift))
}

/// The character's next locked lore entry (`Lore::next`), if any.
async fn next_lore(conn: &mut PgConnection, character_id: i64) -> Result<Option<u16>, sqlx::Error> {
    let lore = &shipped().lore;
    if lore.entries.is_empty() {
        return Ok(None);
    }
    let known: Vec<i32> =
        sqlx::query_scalar("SELECT lore_id FROM lore_unlocks WHERE character_id = $1")
            .bind(character_id)
            .fetch_all(&mut *conn)
            .await?;
    let known: HashSet<u16> = known
        .into_iter()
        .filter_map(|id| u16::try_from(id).ok())
        .collect();
    Ok(lore.next(&known).map(|e| e.id))
}

/// Whether roster id `npc` is a background student (`NpcKind::Student`):
/// ranked behind players in views and capped per view
/// (`realtime::visibility`).
pub fn is_student(npc: &str) -> bool {
    static STUDENTS: std::sync::LazyLock<HashSet<String>> = std::sync::LazyLock::new(|| {
        shipped()
            .npcs
            .roster
            .of_kind(sim_core::npcs::NpcKind::Student)
            .map(|n| n.id.clone())
            .collect()
    });
    STUDENTS.contains(npc)
}
