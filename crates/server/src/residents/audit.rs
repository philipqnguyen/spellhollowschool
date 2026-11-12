//! The ledger checks for NPC gifts (docs/architecture.md "NPCs"): each
//! `npc_gifts` row explains exactly what its claim wrote, and nothing NPC-
//! sourced exists without one.
//!
//! - every gift row of the characters holds a gift (`protocol::NpcGift`),
//!   from an NPC of the roster with a gift table;
//! - **items**: a gift row's `npc_gift` item rows are `create`s into the
//!   character's inventory of exactly its item and quantity; every
//!   `npc_gift` item row names a gift row of its owner;
//! - **house points**: a `house_point` gift has its one `npc` house-point
//!   row of the same points for the character's house; every `npc` row
//!   names a gift row of the character; per character and day, at most
//!   `house_cup.json` `sources.npc.daily_cap` points;
//! - **lore**: a `lore` gift has its `lore_unlocks` row naming the NPC; every
//!   NPC lore unlock is a gift row's.
//!
//! Called by `ledger::audit::reconcile` (so `shs-server audit`) and the
//! tests (`tests/residents.rs`).

use std::collections::{BTreeMap, BTreeSet};

use protocol::NpcGift;
use sqlx::PgPool;
use sqlx::types::Json;

use crate::catalog::shipped;
use crate::ledger::audit::Problem;

/// An `npc_gift` item ledger row: id, ref_id, item_def, quantity, op,
/// to_owner, to_location.
type ItemRow = (
    i64,
    Option<i64>,
    i32,
    i32,
    String,
    Option<i64>,
    Option<String>,
);

/// One gift row.
#[derive(Debug, sqlx::FromRow)]
struct GiftRow {
    id: i64,
    character_id: i64,
    npc: String,
    day: chrono::NaiveDate,
    gift: Json<serde_json::Value>,
}

/// The NPC gift problems of `characters`.
pub async fn problems(pool: &PgPool, characters: &[i64]) -> Result<Vec<Problem>, sqlx::Error> {
    let mut tx = pool.begin().await?;
    sqlx::query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        .execute(&mut *tx)
        .await?;
    let mut out = Vec::new();
    let mut add = |detail: String| {
        out.push(Problem {
            check: "npc_gifts",
            detail,
        })
    };
    let data = &shipped().npcs;
    let cap = u32::from(shipped().house_cup.rule(sim_core::CupSource::Npc).daily_cap);

    let rows: Vec<GiftRow> = sqlx::query_as(
        "SELECT id, character_id, npc, day, gift FROM npc_gifts
         WHERE character_id = ANY($1) ORDER BY id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    // Item rows by gift row: (item_def, units, every one a create into the
    // owner's inventory).
    let items: Vec<ItemRow> = sqlx::query_as(
        "SELECT id, ref_id, item_def, quantity, op, to_owner, to_location FROM item_ledger
             WHERE reason = 'npc_gift' AND to_owner = ANY($1) ORDER BY id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    let points: Vec<(i64, Option<i64>, i64, i32, String, String)> = sqlx::query_as(
        "SELECT h.id, h.ref_id, h.character_id, h.points, h.house,
                (ARRAY['roarwing', 'burrowsby', 'skytalon', 'serpentyne'])[c.house + 1]
         FROM house_points h JOIN characters c ON c.id = h.character_id
         WHERE h.source = 'npc' AND h.character_id = ANY($1) ORDER BY h.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    let lore: Vec<(i64, i32, String)> = sqlx::query_as(
        "SELECT character_id, lore_id, npc FROM lore_unlocks
         WHERE npc IS NOT NULL AND character_id = ANY($1)",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    tx.commit().await?;

    let by_id: BTreeMap<i64, &GiftRow> = rows.iter().map(|r| (r.id, r)).collect();
    let mut item_units: BTreeMap<i64, BTreeMap<i32, i64>> = BTreeMap::new();
    for (id, ref_id, def, qty, op, owner, loc) in &items {
        let row = ref_id.and_then(|r| by_id.get(&r));
        match row {
            Some(r) if Some(r.character_id) == *owner => {
                if op != "create" || loc.as_deref() != Some(crate::inventory::LOCATION_INVENTORY) {
                    add(format!(
                        "item ledger row {id} of gift {} is a {op} into {loc:?}",
                        r.id
                    ));
                }
                *item_units.entry(r.id).or_default().entry(*def).or_default() += i64::from(*qty);
            }
            _ => add(format!(
                "item ledger row {id} ('npc_gift', ref {ref_id:?}) names no gift of its owner"
            )),
        }
    }
    let mut point_rows: BTreeMap<i64, Vec<i32>> = BTreeMap::new();
    let mut per_day: BTreeMap<(i64, chrono::NaiveDate), i64> = BTreeMap::new();
    for (id, ref_id, cid, pts, house, char_house) in &points {
        match ref_id.and_then(|r| by_id.get(&r)) {
            Some(r) if r.character_id == *cid => {
                point_rows.entry(r.id).or_default().push(*pts);
                *per_day.entry((*cid, r.day)).or_default() += i64::from(*pts);
                if house != char_house {
                    add(format!(
                        "house point row {id} went to {house}, not {char_house}"
                    ));
                }
            }
            _ => add(format!(
                "house point row {id} ('npc', ref {ref_id:?}) names no gift of its character"
            )),
        }
    }
    for ((cid, day), total) in per_day {
        if total > i64::from(cap) {
            add(format!(
                "character {cid} got {total} NPC house points on {day} (cap {cap})"
            ));
        }
    }
    let mut lore_told: BTreeSet<(i64, i32, String)> = lore.into_iter().collect();

    for r in &rows {
        let gift: Option<NpcGift> = serde_json::from_value(r.gift.0.clone()).ok();
        let Some(gift) = gift else {
            add(format!("gift row {} holds no gift: {}", r.id, r.gift.0));
            continue;
        };
        if data.gifts.table(&r.npc).is_none() {
            add(format!("gift row {}: {} gives no gifts", r.id, r.npc));
        }
        let units = item_units.remove(&r.id).unwrap_or_default();
        let pts = point_rows.remove(&r.id).unwrap_or_default();
        match &gift {
            NpcGift::Item { item_def, quantity } => {
                let want: BTreeMap<i32, i64> =
                    [(i32::from(*item_def), i64::from(*quantity))].into();
                if units != want {
                    add(format!(
                        "gift row {} gave {quantity} of item {item_def}; the ledger created {units:?}",
                        r.id
                    ));
                }
            }
            NpcGift::HousePoint { points } => {
                if pts != [i32::from(*points)] {
                    add(format!(
                        "gift row {} gave {points} house points; the rows say {pts:?}",
                        r.id
                    ));
                }
            }
            NpcGift::Lore { lore_id, .. } => {
                if !lore_told.remove(&(r.character_id, i32::from(*lore_id), r.npc.clone())) {
                    add(format!(
                        "gift row {} told lore {lore_id}; no unlock names {}",
                        r.id, r.npc
                    ));
                }
            }
            NpcGift::Hint { place } => {
                if place.trim().is_empty() {
                    add(format!("gift row {} hints at no place", r.id));
                }
            }
        }
        if !matches!(gift, NpcGift::Item { .. }) && !units.is_empty() {
            add(format!("gift row {} created items {units:?}", r.id));
        }
        if !matches!(gift, NpcGift::HousePoint { .. }) && !pts.is_empty() {
            add(format!("gift row {} awarded house points {pts:?}", r.id));
        }
    }
    for (cid, lore_id, npc) in lore_told {
        add(format!(
            "character {cid}'s lore {lore_id} told by {npc} has no gift row"
        ));
    }
    Ok(out)
}
