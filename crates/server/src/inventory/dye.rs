//! Recolouring and finishing items: `POST /api/items/{id}/dye` and
//! `POST /api/items/{id}/finish`.
//!
//! The dye rule
//! (docs/architecture.md, "Economy and crafting"; `sim_core::Palette::
//! dyes_needed`): a channel recoloured to its default or to a colour of the
//! free basic palette costs nothing; any other colour uses up one dye item
//! of that colour's family from the inventory (`crate::items::consume`,
//! `ledger::Reason::Dye`, ref the dyed item; `409 missing_dyes` naming the
//! families short). Finishes (shimmer, colour-shift) always use up a brewed
//! finish item. Both lock the character row first (`lock_appearance`), then
//! every item involved in one ascending-id lock.

use axum::extract::State;
use axum::{Json, Router};
use protocol::Finish;
use serde::Deserialize;
use sim_core::ItemKind;

use super::equip::{
    announce, item_not_found, item_unavailable, lock_appearance, refresh_appearance,
};
use super::{ITEM_SELECT, ItemRow, ItemView, LOCATION_EQUIPPED, LOCATION_INVENTORY};
use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{ApiPath, Writer};
use crate::crafting::materials::{Needs, allocate, item_error, lock_for, use_up};
use crate::http::{ApiError, ApiJson};
use crate::items::{self, finish_to_db};
use crate::ledger::Reason;

#[derive(Debug, Deserialize)]
pub struct DyeRequest {
    /// Palette indices for primary, secondary and trim. Wider than `u8` so
    /// an out-of-range number is `invalid_dye` rather than a parse error.
    pub dyes: [i64; 3],
}

fn invalid_dye(message: String) -> ApiError {
    ApiError::bad_request("invalid_dye", message)
}

/// `POST /api/items/{id}/dye { dyes: [p, s, t] }` → the dyed item. Dyeing a
/// worn item changes the look (and is broadcast).
pub async fn dye(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(item_id): ApiPath<i64>,
    ApiJson(req): ApiJson<DyeRequest>,
) -> Result<Json<ItemView>, ApiError> {
    let content = shipped();
    let colours = content.palette.colours.len();
    let mut wanted = [0_u8; 3];
    for (out, d) in wanted.iter_mut().zip(req.dyes) {
        *out = u8::try_from(d)
            .ok()
            .filter(|&d| usize::from(d) < colours)
            .ok_or_else(|| {
                invalid_dye(format!(
                    "Dyes are palette indices from 0 to {}.",
                    colours - 1
                ))
            })?;
    }
    let id = player.character.id;
    let mut tx = state.pool.begin().await?;
    let before = lock_appearance(&mut tx, id).await?;
    // What the recolouring costs depends on the item; under the character
    // lock its row cannot change before the locked read below.
    let current: Option<(i32, i16, i16, i16)> = sqlx::query_as(
        "SELECT item_def, dye_primary, dye_secondary, dye_trim FROM items
         WHERE id = $1 AND owner_id = $2",
    )
    .bind(item_id)
    .bind(id)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((def, p, s, t)) = current else {
        return Err(item_not_found());
    };
    let def = u16::try_from(def)
        .ok()
        .and_then(|d| content.catalog.get(d))
        .ok_or_else(item_not_found)?;
    if !matches!(def.kind, ItemKind::Clothing | ItemKind::Furniture) {
        return Err(invalid_dye("Only clothing and furniture take dyes.".into()));
    }
    let current = super::dyes_from_db(p, s, t);
    let defaults = def.granted_dyes(player.character.house, &content.palette);
    let cost = content
        .palette
        .dyes_needed(defaults, current, wanted)
        .map_err(|e| invalid_dye(e.to_string()))?;
    let needs = Needs {
        items: Vec::new(),
        dyes: cost,
    };
    let (target, candidates) = lock_for(&mut tx, id, &needs, &[item_id]).await?;
    let target = target.into_iter().next().ok_or_else(item_not_found)?;
    if target.location != LOCATION_INVENTORY && target.location != LOCATION_EQUIPPED {
        return Err(item_unavailable());
    }
    let allocation = allocate(&needs, &candidates, Some(item_id)).map_err(|s| s.to_api())?;
    use_up(&mut tx, &allocation, Reason::Dye, Some(item_id))
        .await
        .map_err(item_error)?;
    sqlx::query(
        "UPDATE items SET dye_primary = $2, dye_secondary = $3, dye_trim = $4 WHERE id = $1",
    )
    .bind(item_id)
    .bind(i16::from(wanted[0]))
    .bind(i16::from(wanted[1]))
    .bind(i16::from(wanted[2]))
    .execute(&mut *tx)
    .await?;
    let item: ItemRow = sqlx::query_as(&format!("{ITEM_SELECT} WHERE i.id = $1"))
        .bind(item_id)
        .fetch_one(&mut *tx)
        .await?;
    // Only a worn item changes the look; refreshing finds out.
    let look = refresh_appearance(&mut tx, id, before).await?;
    tx.commit().await?;
    announce(&state, id, &look);
    Ok(Json(item.into()))
}

#[derive(Debug, Deserialize)]
pub struct FinishRequest {
    /// The finish item (shimmer lacquer, colour-shift varnish) to apply.
    pub finish_item_id: i64,
}

/// `POST /api/items/{id}/finish { finish_item_id }` → the finished garment.
/// One unit of the finish is used up; the garment's previous finish, if
/// any, is replaced. Applying the finish it already has is refused
/// (`409 already_finished`) rather than wasting a brew.
pub async fn finish(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(item_id): ApiPath<i64>,
    ApiJson(req): ApiJson<FinishRequest>,
) -> Result<Json<ItemView>, ApiError> {
    let content = shipped();
    let id = player.character.id;
    let mut tx = state.pool.begin().await?;
    let before = lock_appearance(&mut tx, id).await?;
    let rows = items::lock(&mut tx, id, &[item_id, req.finish_item_id]).await?;
    let find = |want: i64| rows.iter().find(|r| r.id == want);
    let (Some(target), Some(potion)) = (find(item_id), find(req.finish_item_id)) else {
        return Err(item_not_found());
    };
    let finish = content
        .catalog
        .get(potion.item_def)
        .and_then(|d| d.finish)
        .filter(|&f| f != Finish::None)
        .ok_or_else(|| ApiError::bad_request("not_a_finish", "That is not a finish."))?;
    let is_clothing = content
        .catalog
        .get(target.item_def)
        .is_some_and(|d| d.kind == ItemKind::Clothing);
    if !is_clothing {
        return Err(ApiError::bad_request(
            "not_clothing",
            "Finishes go on clothing.",
        ));
    }
    let target_ok = target.location == LOCATION_INVENTORY || target.location == LOCATION_EQUIPPED;
    if !target_ok || potion.location != LOCATION_INVENTORY {
        return Err(item_unavailable());
    }
    if target.finish == finish {
        return Err(ApiError::conflict(
            "already_finished",
            "That garment already has this finish.",
        ));
    }
    items::consume(&mut tx, potion, 1, Reason::Dye, Some(item_id))
        .await
        .map_err(item_error)?;
    sqlx::query("UPDATE items SET finish = $2 WHERE id = $1")
        .bind(item_id)
        .bind(finish_to_db(finish))
        .execute(&mut *tx)
        .await?;
    let item: ItemRow = sqlx::query_as(&format!("{ITEM_SELECT} WHERE i.id = $1"))
        .bind(item_id)
        .fetch_one(&mut *tx)
        .await?;
    let look = refresh_appearance(&mut tx, id, before).await?;
    tx.commit().await?;
    announce(&state, id, &look);
    Ok(Json(item.into()))
}

/// The routes, merged into the inventory's (wardrobe rate limit).
pub fn routes() -> Router<AppState> {
    use axum::routing::post;
    Router::new()
        .route("/items/{id}/dye", post(dye))
        .route("/items/{id}/finish", post(finish))
}
