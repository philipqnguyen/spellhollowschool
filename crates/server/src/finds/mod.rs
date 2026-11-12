//! Exploration finds: hidden
//! `find_spot` interactables (`data/world/layout.json`) with loot tables
//! (`data/world/finds.json`, `data/world/loot.json`) and a per-character
//! cooldown per spot (`find_cooldowns`, once per real day).
//!
//! Searching is realtime ([`world`]): `Interact` with a spot in reach; one
//! job ([`search`]) rolls the table (`sim_core::finds::LootTable::roll`,
//! with `Luck` while Fortune's Draught is active), then one transaction locks
//! the character, claims the cooldown, gives the items
//! (`crate::items::give`, `Reason::Find`) and Crowns
//! (`ledger::adjust_crowns`, `Reason::Find`); the world answers
//! `ServerMsg::FindResult` (and `Wallet` when Crowns were found). The
//! cooldown is claimed with one conditional upsert under the character's
//! row lock, so however fast a spot is searched, it pays out once a day.
//!
//! HTTP: `GET /api/finds?limit=&after=` → the character's spots on cooldown
//! (`{ items: [{ spot, ready_at }], next }`, by spot id), so the client can
//! mark them.

pub mod world;

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{FindOutcome, FoundItem};
use rand::Rng;
use serde::Serialize;
use sim_core::{Drops, Luck};
use sqlx::PgPool;

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Page, PageQuery, Player};
use crate::http::{ApiError, ApiQuery};
use crate::inventory::LOCATION_INVENTORY;
use crate::items::{self, ItemError, NewItem};
use crate::ledger::{self, Reason};

/// A finished search: what to tell the searcher, and their balance when
/// Crowns were found.
#[derive(Debug, Clone, PartialEq)]
pub struct Searched {
    pub outcome: FindOutcome,
    pub crowns: Option<i64>,
    /// The player XP a find that turned something up gave
    /// (`data/progression.json` `xp.find`, daily cap).
    pub xp: Option<crate::progression::XpGrant>,
}

#[derive(Debug, thiserror::Error)]
pub enum SearchError {
    #[error("unknown find spot {0:?}")]
    UnknownSpot(String),
    #[error(transparent)]
    Items(#[from] ItemError),
    #[error(transparent)]
    Ledger(#[from] ledger::LedgerError),
    #[error(transparent)]
    Db(#[from] sqlx::Error),
}

/// Fortune's Draught's luck, from `EffectRules.fortunes_draught`.
pub fn fortunes_draught_luck() -> Luck {
    let f = &shipped().economy.effects.fortunes_draught;
    Luck {
        extra_rolls: f.extra_rolls,
        rare_weight_pct: f.rare_weight_pct,
    }
}

/// Searches `spot` for the character: rolls its loot table (with `luck`)
/// and grants the drops in one transaction, unless the spot is on cooldown.
pub async fn search(
    pool: &PgPool,
    character_id: i64,
    spot: &str,
    luck: Option<Luck>,
) -> Result<Searched, SearchError> {
    let content = shipped();
    let table = content
        .finds
        .spot(spot)
        .and_then(|s| content.loot.get(&s.loot))
        .ok_or_else(|| SearchError::UnknownSpot(spot.to_owned()))?;
    // Rolled before any await: the thread's generator is not `Send`.
    let drops = {
        let mut rng = rand::thread_rng();
        table.roll(luck, &mut || rng.r#gen())
    };
    grant(pool, character_id, spot, &drops).await
}

/// Claims the spot's cooldown and grants `drops` in one transaction (see
/// [`search`]; tests pass their own drops).
pub async fn grant(
    pool: &PgPool,
    character_id: i64,
    spot: &str,
    drops: &Drops,
) -> Result<Searched, SearchError> {
    let cooldown = f64::from(shipped().finds.cooldown_secs);
    let mut tx = pool.begin().await?;
    crate::crafting::lock_character(&mut tx, character_id).await?;
    // Claim the spot: a new row, or one whose cooldown has passed.
    let claimed: Option<DateTime<Utc>> = sqlx::query_scalar(
        "INSERT INTO find_cooldowns (character_id, spot_id, ready_at)
         VALUES ($1, $2, now() + make_interval(secs => $3))
         ON CONFLICT (character_id, spot_id) DO UPDATE SET ready_at = EXCLUDED.ready_at
         WHERE find_cooldowns.ready_at <= now()
         RETURNING ready_at",
    )
    .bind(character_id)
    .bind(spot)
    .bind(cooldown)
    .fetch_optional(&mut *tx)
    .await?;
    if claimed.is_none() {
        let left: f64 = sqlx::query_scalar(
            "SELECT EXTRACT(EPOCH FROM (ready_at - now()))::float8 FROM find_cooldowns
             WHERE character_id = $1 AND spot_id = $2",
        )
        .bind(character_id)
        .bind(spot)
        .fetch_one(&mut *tx)
        .await?;
        tx.rollback().await?;
        let ready_in_secs = left.ceil().clamp(1.0, f64::from(u32::MAX)) as u32;
        return Ok(Searched {
            outcome: FindOutcome::Cooldown { ready_in_secs },
            crowns: None,
            xp: None,
        });
    }
    for &(item_def, quantity) in &drops.items {
        let item = NewItem::plain(character_id, item_def, quantity, LOCATION_INVENTORY);
        items::give(&mut tx, &item, Reason::Find, None).await?;
    }
    let crowns = if drops.crowns > 0 {
        let delta = i64::from(drops.crowns);
        Some(ledger::adjust_crowns(&mut tx, character_id, delta, Reason::Find, None).await?)
    } else {
        None
    };
    // Finding something gives player XP (daily cap).
    let xp = if drops.is_empty() {
        None
    } else {
        let amount = shipped().progression.xp.find;
        let grant = crate::progression::grant_xp(
            &mut tx,
            character_id,
            protocol::XpSource::Find,
            None,
            amount,
            Utc::now(),
        )
        .await?;
        Some(grant)
    };
    // ---- House cup: a find that turned something
    // up gives `CupRules::find_points(true)` house points under the daily
    // cap group `hp_find` (`progression::caps::take`), source `find`, no
    // ref_id, in this transaction.
    let points = shipped().house_cup.find_points(!drops.is_empty());
    if points > 0 {
        let house: i16 = sqlx::query_scalar("SELECT house FROM characters WHERE id = $1")
            .bind(character_id)
            .fetch_one(&mut *tx)
            .await?;
        let house = crate::characters::house_from_db(house)
            .ok_or_else(|| sqlx::Error::Protocol("character row has an unknown house".into()))?;
        crate::house_points::award_capped(
            &mut tx,
            house,
            u32::from(points),
            character_id,
            crate::house_points::PointsSource::Find,
            None,
            Utc::now(),
        )
        .await?;
    }
    // ---- end house cup.
    tx.commit().await?;
    let outcome = if drops.is_empty() {
        FindOutcome::Nothing
    } else {
        FindOutcome::Found {
            items: drops
                .items
                .iter()
                .map(|&(item_def, quantity)| FoundItem { item_def, quantity })
                .collect(),
            crowns: i64::from(drops.crowns),
        }
    };
    Ok(Searched {
        outcome,
        crowns,
        xp,
    })
}

#[derive(Debug, Serialize, sqlx::FromRow)]
pub struct CooldownView {
    #[sqlx(rename = "spot_id")]
    pub spot: String,
    pub ready_at: DateTime<Utc>,
}

/// `GET /api/finds?limit=&after=`: spots still on cooldown, by spot id
/// (the primary key's order, so each page is an index range).
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<CooldownView>>, ApiError> {
    let after: Option<String> = page.after()?;
    let rows: Vec<CooldownView> = sqlx::query_as(
        "SELECT spot_id, ready_at FROM find_cooldowns
         WHERE character_id = $1 AND ready_at > now() AND ($2::text IS NULL OR spot_id > $2)
         ORDER BY spot_id LIMIT $3",
    )
    .bind(player.character.id)
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    Ok(Json(Page::from_rows(rows, page.limit()?, |r| {
        r.spot.clone()
    })))
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new().route("/finds", get(list))
}
