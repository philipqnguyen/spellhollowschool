//! The Crown wallet:
//! `GET /api/wallet` and `GET /api/wallet/history`, the HUD numbers the
//! gateway loads at sign-in ([`hud`]), and the pieces every economy
//! transaction shares (locking characters, request ids, error answers).
//!
//! Balances change only through `ledger::adjust_crowns` inside the
//! transaction that causes the change; after committing, handlers call
//! `WorldHandle::wallet_changed(character_id, new_balance)` so the HUD
//! updates (`ServerMsg::Wallet`).
//!
//! **Starting purse.** New characters get it with their starter grant
//! (`inventory::starter`); characters granted before purses existed got
//! it from migration 0007. [`ensure_purse`] closes the last gap: a character
//! whose starter grant exists but whose purse does not (a grant made by an
//! older server after the migration ran) receives it on sign-in or on its
//! next wallet request, exactly once (`purse_granted_at`).

use std::time::Duration;

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::Serialize;
use sqlx::{PgConnection, PgPool};

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Page, PageQuery, Player};
use crate::http::{ApiError, ApiQuery};
use crate::items::ItemError;
use crate::ledger::LedgerError;
use crate::rate_limit::RateLimit;

/// Economy writes per account and route group (orders, letters, listings,
/// purchases, take-downs): a burst, then one every two seconds.
pub const ECONOMY_RATE_LIMIT: RateLimit = RateLimit {
    burst: 10,
    per: Duration::from_secs(2),
};

/// What the HUD shows at sign-in (`Joining.crowns`, `Joining.unread_mail`).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Hud {
    pub crowns: i64,
    pub unread_mail: u32,
}

/// The character's balance and unread mail count (granting a missing
/// starting purse first, see the module docs).
pub async fn hud(pool: &PgPool, character_id: i64) -> Result<Hud, sqlx::Error> {
    let (crowns, unread, purse_due): (i64, i64, bool) = sqlx::query_as(
        "SELECT c.crowns,
                (SELECT count(*) FROM mail m WHERE m.recipient_id = c.id AND m.read_at IS NULL
                                                AND m.recipient_deleted_at IS NULL),
                c.purse_granted_at IS NULL AND c.starter_granted_at IS NOT NULL
         FROM characters c WHERE c.id = $1",
    )
    .bind(character_id)
    .fetch_one(pool)
    .await?;
    let crowns = if purse_due {
        match ensure_purse(pool, character_id).await? {
            Some(balance) => balance,
            // Someone else granted it meanwhile: read the balance after.
            None => crate::ledger::balance(pool, character_id).await?,
        }
    } else {
        crowns
    };
    Ok(Hud {
        crowns,
        unread_mail: u32::try_from(unread).unwrap_or(u32::MAX),
    })
}

/// Gives the starting purse to a character that has its starter grant but
/// no purse yet, with its ledger row, in one statement (the row lock of the
/// `UPDATE` makes it once only). Returns the new balance if it granted.
pub async fn ensure_purse(pool: &PgPool, character_id: i64) -> Result<Option<i64>, sqlx::Error> {
    let purse = i64::from(shipped().economy.starting_purse);
    sqlx::query_scalar(
        "WITH granted AS (
             UPDATE characters SET crowns = crowns + $2, purse_granted_at = now()
             WHERE id = $1 AND purse_granted_at IS NULL AND starter_granted_at IS NOT NULL
             RETURNING id, crowns
         )
         INSERT INTO crown_ledger (character_id, delta, balance_after, reason)
         SELECT id, $2, crowns, $3 FROM granted
         RETURNING balance_after",
    )
    .bind(character_id)
    .bind(purse)
    .bind(crate::ledger::Reason::StartingPurse.id())
    .fetch_optional(pool)
    .await
}

/// `GET /api/wallet` → `{ crowns }`.
#[derive(Debug, Serialize)]
pub struct WalletView {
    pub crowns: i64,
}

pub async fn wallet(
    State(state): State<AppState>,
    player: Player,
) -> Result<Json<WalletView>, ApiError> {
    let crowns = hud(&state.pool, player.character.id).await?.crowns;
    Ok(Json(WalletView { crowns }))
}

/// One `crown_ledger` row: `{ id, delta, balance_after, reason, ref_id, at }`.
#[derive(Debug, Serialize, sqlx::FromRow)]
pub struct HistoryEntry {
    pub id: i64,
    pub delta: i64,
    pub balance_after: i64,
    pub reason: String,
    pub ref_id: Option<i64>,
    pub at: DateTime<Utc>,
}

/// `GET /api/wallet/history?limit=&after=`: the character's Crown
/// movements, newest first (index `crown_ledger (character_id, id)`).
pub async fn history(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<HistoryEntry>>, ApiError> {
    let after: Option<i64> = page.after()?;
    let rows: Vec<HistoryEntry> = sqlx::query_as(
        "SELECT id, delta, balance_after, reason, ref_id, at FROM crown_ledger
         WHERE character_id = $1 AND ($2::bigint IS NULL OR id < $2)
         ORDER BY id DESC LIMIT $3",
    )
    .bind(player.character.id)
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    Ok(Json(Page::from_rows(rows, page.limit()?, |r| r.id)))
}

// ------------------------------------------------------------------ shared

/// Locks the `characters` rows of everyone whose items or Crowns the
/// transaction changes, in id order (`FOR NO KEY UPDATE`, the first locks of
/// every economy transaction; docs/architecture.md, "Economy and crafting").
/// Duplicates are fine. Returns how many of them exist.
pub async fn lock_characters(conn: &mut PgConnection, ids: &[i64]) -> Result<usize, sqlx::Error> {
    let mut sorted = ids.to_vec();
    sorted.sort_unstable();
    sorted.dedup();
    let locked: Vec<i64> = sqlx::query_scalar(
        "SELECT id FROM characters WHERE id = ANY($1) ORDER BY id FOR NO KEY UPDATE",
    )
    .bind(&sorted)
    .fetch_all(conn)
    .await?;
    Ok(locked.len())
}

/// A retryable write's `request_id` (1-64 characters): the key a repeat is
/// recognised by. Absent or `null` means the request is not retry-safe.
pub fn request_key(raw: Option<&str>) -> Result<Option<String>, ApiError> {
    match raw {
        None => Ok(None),
        Some(k) if (1..=64).contains(&k.chars().count()) && !k.chars().any(char::is_control) => {
            Ok(Some(k.to_owned()))
        }
        Some(_) => Err(ApiError::bad_request(
            "invalid_body",
            "request_id must be 1-64 printable characters.",
        )),
    }
}

/// `409 insufficient_crowns`.
pub fn insufficient_crowns() -> ApiError {
    ApiError::conflict(
        "insufficient_crowns",
        "You do not have enough Crowns for that.",
    )
}

/// A ledger refusal as the HTTP answer.
pub fn ledger_error(e: LedgerError) -> ApiError {
    match e {
        LedgerError::InsufficientCrowns => insufficient_crowns(),
        LedgerError::ZeroQuantity => ApiError::internal("a ledger row with no units"),
        LedgerError::Db(db) => db.into(),
    }
}

/// An item-helper refusal as the HTTP answer: not enough units is the
/// player's `409 item_unavailable`; anything else is ours.
pub fn item_error(e: ItemError) -> ApiError {
    match e {
        ItemError::NotEnough => crate::inventory::equip::item_unavailable(),
        ItemError::Ledger(l) => ledger_error(l),
        ItemError::Db(db) => db.into(),
        other @ (ItemError::UnknownDef(_) | ItemError::OverStack(..)) => {
            ApiError::internal_error(&other)
        }
    }
}

/// The units of a stack row `items::take` split off, as a locked row of
/// their own (same owner, place and look; the lock comes with the split).
pub fn split_row(
    from: &crate::items::LockedItem,
    id: i64,
    quantity: u32,
) -> crate::items::LockedItem {
    crate::items::LockedItem {
        id,
        quantity,
        ..from.clone()
    }
}

/// This module's routes (docs/architecture.md, "HTTP API").
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new()
        .route("/wallet", get(wallet))
        .route("/wallet/history", get(history))
}
