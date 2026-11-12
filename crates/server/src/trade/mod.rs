//! Player-to-player trading.
//!
//! The window is realtime ([`world`]): two players in the same zone within
//! `EconomyRules.trade.distance_m` set offers, lock, review and confirm the
//! same revision; any change resets both confirmations; walking out of range
//! (`distance_m + leave_slack_m`), changing zone, disconnecting or
//! cancelling ends it. The commit is one Postgres transaction ([`commit`]):
//! lock both characters (id order), then every offered item, re-check
//! ownership, location `inventory`, quantities, looks and balances, move
//! items (`crate::items::take` + `move_to`, merging into the receiver's
//! stacks) and Crowns (`ledger::adjust_crowns`, `Reason::Trade`), with
//! the `trades` row (unique `trade_key`, so it can never apply twice) as
//! the ledger's reference.
//!
//! HTTP: `GET /api/trades?limit=&after=` (the character's completed trades,
//! newest first).

pub mod world;

use std::collections::HashSet;

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{OfferedItem, TRADE_ITEMS_MAX, TradeErrorCode};
use serde::{Deserialize, Serialize};
use sqlx::types::Json as DbJson;
use sqlx::{PgConnection, PgPool};

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Page, PageQuery, Player};
use crate::http::{ApiError, ApiQuery};
use crate::inventory::LOCATION_INVENTORY;
use crate::items::{self, ItemError, LockedItem};
use crate::ledger::{self, LedgerError, Reason};
use crate::wallet::{lock_characters, split_row};

/// One side of a trade to commit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Side {
    pub character_id: i64,
    /// `(items.id, quantity)` offered.
    pub items: Vec<(i64, u32)>,
    pub crowns: i64,
    /// What the other side reviewed for each offered item (the window's
    /// `OfferedItem`s). An item whose definition, dyes, finish or maker's
    /// mark changed since (dyed or restyled meanwhile) fails the trade
    /// (`ItemUnavailable`). Empty: no look check.
    pub reviewed: Vec<OfferedItem>,
}

/// A completed trade: its `trades.id` and both balances after it (a, b).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Committed {
    pub trade_id: i64,
    pub balances: (i64, i64),
}

/// Commits a confirmed trade in one transaction (see the module docs);
/// returns the `trades.id`. Called from a world job.
pub async fn commit(
    pool: &PgPool,
    trade_key: i64,
    a: &Side,
    b: &Side,
) -> Result<i64, TradeErrorCode> {
    commit_with_balances(pool, trade_key, a, b)
        .await
        .map(|c| c.trade_id)
}

/// [`commit`], also answering both balances for the `Wallet` notices. A
/// `trade_key` that already committed answers that trade again (nothing
/// moves twice).
pub async fn commit_with_balances(
    pool: &PgPool,
    trade_key: i64,
    a: &Side,
    b: &Side,
) -> Result<Committed, TradeErrorCode> {
    check_side(a)?;
    check_side(b)?;
    if a.character_id == b.character_id {
        return Err(TradeErrorCode::SelfTrade);
    }
    match apply(pool, trade_key, a, b).await {
        Ok(done) => Ok(done),
        Err(Refusal::Code(code)) => Err(code),
        Err(Refusal::Db(e)) => {
            tracing::error!(error = %e, trade_key, "trade commit failed");
            Err(TradeErrorCode::Failed)
        }
    }
}

fn check_side(s: &Side) -> Result<(), TradeErrorCode> {
    let max = i64::from(shipped().economy.trade.crowns_max);
    let mut seen = HashSet::new();
    let ok = (0..=max).contains(&s.crowns)
        && s.items.len() <= TRADE_ITEMS_MAX
        && s.items.iter().all(|&(id, q)| q > 0 && seen.insert(id));
    ok.then_some(()).ok_or(TradeErrorCode::InvalidOffer)
}

/// Why a commit did not happen.
enum Refusal {
    Code(TradeErrorCode),
    Db(sqlx::Error),
}

impl From<sqlx::Error> for Refusal {
    fn from(e: sqlx::Error) -> Self {
        Refusal::Db(e)
    }
}

impl From<LedgerError> for Refusal {
    fn from(e: LedgerError) -> Self {
        match e {
            LedgerError::InsufficientCrowns => Refusal::Code(TradeErrorCode::InsufficientCrowns),
            LedgerError::ZeroQuantity => Refusal::Code(TradeErrorCode::Failed),
            LedgerError::Db(db) => Refusal::Db(db),
        }
    }
}

impl From<ItemError> for Refusal {
    fn from(e: ItemError) -> Self {
        match e {
            ItemError::NotEnough => Refusal::Code(TradeErrorCode::ItemUnavailable),
            ItemError::Ledger(l) => l.into(),
            ItemError::Db(db) => Refusal::Db(db),
            ItemError::UnknownDef(_) | ItemError::OverStack(..) => {
                Refusal::Code(TradeErrorCode::Failed)
            }
        }
    }
}

/// One moved line, as `trades.a_items` / `b_items` record it.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
struct TradedLine {
    item_id: i64,
    item_def: u16,
    quantity: u32,
    /// The row that moved, when units were split off `item_id`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    new_item_id: Option<i64>,
}

async fn apply(pool: &PgPool, key: i64, a: &Side, b: &Side) -> Result<Committed, Refusal> {
    let mut tx = pool.begin().await?;
    if lock_characters(&mut tx, &[a.character_id, b.character_id]).await? != 2 {
        return Err(Refusal::Code(TradeErrorCode::UnknownTarget));
    }
    // The trade's row first, so every ledger row can name it; a key that
    // already committed is answered, not repeated.
    let inserted: Option<i64> = sqlx::query_scalar(
        "INSERT INTO trades (trade_key, a_id, b_id, a_crowns, b_crowns, a_items, b_items)
         VALUES ($1, $2, $3, $4, $5, '[]', '[]')
         ON CONFLICT (trade_key) DO NOTHING RETURNING id",
    )
    .bind(key)
    .bind(a.character_id)
    .bind(b.character_id)
    .bind(a.crowns)
    .bind(b.crowns)
    .fetch_optional(&mut *tx)
    .await?;
    let Some(id) = inserted else {
        let id: i64 = sqlx::query_scalar("SELECT id FROM trades WHERE trade_key = $1")
            .bind(key)
            .fetch_one(&mut *tx)
            .await?;
        let balances = balances(&mut tx, a.character_id, b.character_id).await?;
        tx.rollback().await?;
        return Ok(Committed {
            trade_id: id,
            balances,
        });
    };

    // Crowns out of both purses first (each refusal changes nothing).
    ledger::adjust_crowns(&mut tx, a.character_id, -a.crowns, Reason::Trade, Some(id)).await?;
    ledger::adjust_crowns(&mut tx, b.character_id, -b.crowns, Reason::Trade, Some(id)).await?;

    // Every offered row, locked and checked. Both owners are locked, so
    // every other transaction touching these rows waits for us: the item
    // locks cannot deadlock however they interleave between the owners.
    let a_rows = locked_offer(&mut tx, a).await?;
    let b_rows = locked_offer(&mut tx, b).await?;

    // Split everything first, then move, then merge: a stack received must
    // never be what the receiver's own offer moves away (the locked rows'
    // quantities are what each side offered from).
    let a_parts = split(&mut tx, &a_rows, id).await?;
    let b_parts = split(&mut tx, &b_rows, id).await?;
    for (part, _) in &a_parts {
        items::move_to(
            &mut tx,
            part,
            b.character_id,
            LOCATION_INVENTORY,
            Reason::Trade,
            Some(id),
        )
        .await?;
    }
    for (part, _) in &b_parts {
        items::move_to(
            &mut tx,
            part,
            a.character_id,
            LOCATION_INVENTORY,
            Reason::Trade,
            Some(id),
        )
        .await?;
    }
    merge_received(&mut tx, b.character_id, &a_parts, id).await?;
    merge_received(&mut tx, a.character_id, &b_parts, id).await?;

    ledger::adjust_crowns(&mut tx, a.character_id, b.crowns, Reason::Trade, Some(id)).await?;
    ledger::adjust_crowns(&mut tx, b.character_id, a.crowns, Reason::Trade, Some(id)).await?;
    sqlx::query("UPDATE trades SET a_items = $2, b_items = $3 WHERE id = $1")
        .bind(id)
        .bind(DbJson(lines(&a_parts)))
        .bind(DbJson(lines(&b_parts)))
        .execute(&mut *tx)
        .await?;
    let balances = balances(&mut tx, a.character_id, b.character_id).await?;
    tx.commit().await?;
    Ok(Committed {
        trade_id: id,
        balances,
    })
}

/// A side's offered rows with the units offered from each, locked and
/// checked: owned, in the inventory, enough units, as reviewed.
async fn locked_offer(
    conn: &mut PgConnection,
    side: &Side,
) -> Result<Vec<(LockedItem, u32)>, Refusal> {
    let ids: Vec<i64> = side.items.iter().map(|&(id, _)| id).collect();
    let rows = items::lock(conn, side.character_id, &ids).await?;
    if rows.len() != ids.len() {
        return Err(Refusal::Code(TradeErrorCode::ItemUnavailable));
    }
    let mut out = Vec::with_capacity(rows.len());
    for row in rows {
        let quantity = side
            .items
            .iter()
            .find(|&&(id, _)| id == row.id)
            .map_or(0, |&(_, q)| q);
        let as_reviewed = side.reviewed.is_empty()
            || side.reviewed.iter().any(|r| {
                r.item_id == row.id
                    && r.item_def == row.item_def
                    && r.dyes == row.dyes
                    && r.finish == row.finish
                    && r.maker == row.maker_name
            });
        if row.location != LOCATION_INVENTORY || quantity > row.quantity || !as_reviewed {
            return Err(Refusal::Code(TradeErrorCode::ItemUnavailable));
        }
        out.push((row, quantity));
    }
    Ok(out)
}

/// Splits each offered quantity off its row: `(the row that will move, the
/// row it came from)`.
async fn split(
    conn: &mut PgConnection,
    rows: &[(LockedItem, u32)],
    trade_id: i64,
) -> Result<Vec<(LockedItem, i64)>, Refusal> {
    let mut parts = Vec::with_capacity(rows.len());
    for (row, quantity) in rows {
        let part = items::take(conn, row, *quantity, Reason::Trade, Some(trade_id)).await?;
        parts.push((split_row(row, part, *quantity), row.id));
    }
    Ok(parts)
}

/// Merges received stackable rows into the receiver's inventory stacks.
/// Each row is locked again first: an earlier merge may have topped it up
/// (or emptied it into another), so its snapshot from the move is stale.
async fn merge_received(
    conn: &mut PgConnection,
    receiver: i64,
    parts: &[(LockedItem, i64)],
    trade_id: i64,
) -> Result<(), Refusal> {
    for (part, _) in parts {
        let stacks = shipped()
            .catalog
            .get(part.item_def)
            .is_some_and(|d| d.stackable);
        if !stacks {
            continue;
        }
        if let Some(now) = items::lock(conn, receiver, &[part.id]).await?.pop() {
            items::into_inventory(conn, &now, Reason::Trade, Some(trade_id)).await?;
        }
    }
    Ok(())
}

fn lines(parts: &[(LockedItem, i64)]) -> Vec<TradedLine> {
    parts
        .iter()
        .map(|(part, from)| TradedLine {
            item_id: *from,
            item_def: part.item_def,
            quantity: part.quantity,
            new_item_id: (part.id != *from).then_some(part.id),
        })
        .collect()
}

async fn balances(conn: &mut PgConnection, a: i64, b: i64) -> Result<(i64, i64), sqlx::Error> {
    let rows: Vec<(i64, i64)> =
        sqlx::query_as("SELECT id, crowns FROM characters WHERE id = ANY($1)")
            .bind([a, b])
            .fetch_all(conn)
            .await?;
    let of = |c: i64| rows.iter().find(|r| r.0 == c).map_or(0, |r| r.1);
    Ok((of(a), of(b)))
}

// ------------------------------------------------------------------ HTTP

/// The other side of a past trade (`null` once their character is gone).
#[derive(Debug, Clone, Serialize)]
pub struct Partner {
    pub character_id: i64,
    pub name: String,
}

/// An item line of a past trade.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct TradedItem {
    pub item_def: u16,
    pub quantity: u32,
}

/// What one side handed over.
#[derive(Debug, Clone, Serialize)]
pub struct Handed {
    pub items: Vec<TradedItem>,
    pub crowns: i64,
}

/// `{ id, partner, gave, got, created_at }`, from the asker's side.
#[derive(Debug, Clone, Serialize)]
pub struct TradeRecord {
    pub id: i64,
    pub partner: Option<Partner>,
    pub gave: Handed,
    pub got: Handed,
    pub created_at: DateTime<Utc>,
}

#[derive(sqlx::FromRow)]
struct TradeRow {
    id: i64,
    a_id: Option<i64>,
    b_id: Option<i64>,
    a_crowns: i64,
    b_crowns: i64,
    a_items: DbJson<Vec<TradedLine>>,
    b_items: DbJson<Vec<TradedLine>>,
    created_at: DateTime<Utc>,
    a_name: Option<String>,
    b_name: Option<String>,
}

impl TradeRow {
    fn view(self, me: i64) -> TradeRecord {
        let handed = |lines: &[TradedLine], crowns| Handed {
            items: lines
                .iter()
                .map(|l| TradedItem {
                    item_def: l.item_def,
                    quantity: l.quantity,
                })
                .collect(),
            crowns,
        };
        let a = handed(&self.a_items.0, self.a_crowns);
        let b = handed(&self.b_items.0, self.b_crowns);
        let (partner, partner_name, gave, got) = if self.a_id == Some(me) {
            (self.b_id, self.b_name, a, b)
        } else {
            (self.a_id, self.a_name, b, a)
        };
        TradeRecord {
            id: self.id,
            partner: partner
                .zip(partner_name)
                .map(|(character_id, name)| Partner { character_id, name }),
            gave,
            got,
            created_at: self.created_at,
        }
    }
}

/// `GET /api/trades?limit=&after=`: completed trades, newest first (one
/// index scan per side, `trades (a_id, id)` and `trades (b_id, id)`).
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<TradeRecord>>, ApiError> {
    let me = player.character.id;
    let after: Option<i64> = page.after()?;
    let rows: Vec<TradeRow> = sqlx::query_as(
        "SELECT t.id, t.a_id, t.b_id, t.a_crowns, t.b_crowns, t.a_items, t.b_items,
                t.created_at, ca.name AS a_name, cb.name AS b_name
         FROM ((SELECT * FROM trades WHERE a_id = $1 AND ($2::bigint IS NULL OR id < $2)
                ORDER BY id DESC LIMIT $3)
               UNION ALL
               (SELECT * FROM trades WHERE b_id = $1 AND ($2::bigint IS NULL OR id < $2)
                ORDER BY id DESC LIMIT $3)) t
         LEFT JOIN characters ca ON ca.id = t.a_id
         LEFT JOIN characters cb ON cb.id = t.b_id
         ORDER BY t.id DESC LIMIT $3",
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
    Router::new().route("/trades", get(list))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn side(items: Vec<(i64, u32)>, crowns: i64) -> Side {
        Side {
            character_id: 1,
            items,
            crowns,
            reviewed: Vec::new(),
        }
    }

    #[test]
    fn offers_are_checked_before_the_database() {
        assert!(check_side(&side(vec![(1, 1), (2, 5)], 10)).is_ok());
        let bad = [
            side(vec![(1, 0)], 0),
            side(vec![(1, 1), (1, 2)], 0),
            side(vec![], -1),
            side(vec![], i64::from(shipped().economy.trade.crowns_max) + 1),
            side((0..=TRADE_ITEMS_MAX as i64).map(|i| (i, 1)).collect(), 0),
        ];
        for s in bad {
            assert_eq!(check_side(&s), Err(TradeErrorCode::InvalidOffer), "{s:?}");
        }
    }
}
