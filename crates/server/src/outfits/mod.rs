//! Saved outfits (`saved_outfits`): `GET`/`POST /api/outfits`,
//! `PATCH`/`DELETE /api/outfits/{id}` and `POST /api/outfits/{id}/wear`
//! (docs/architecture.md, "HTTP API"). At most `protocol::SAVED_OUTFITS_MAX`
//! per character.
//!
//! An outfit records which item instance is in each slot (or that the slot
//! is empty), not what those items looked like: dyeing a saved item changes
//! the outfit too. Items may since have been traded away, deleted or placed
//! in the dorm; wearing skips (and reports) those slots.
//!
//! Owned by the data side.

use axum::extract::State;
use axum::http::StatusCode;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{Appearance, GearSlot, SAVED_OUTFITS_MAX};
use serde::{Deserialize, Serialize};

use crate::AppState;
use crate::characters::{ApiPath, Page, PageQuery, Player, Writer, write_limited};
use crate::http::{ApiError, ApiJson, ApiQuery};
use crate::inventory::WARDROBE_RATE_LIMIT;
use crate::inventory::equip::{SlotError, announce, lock_appearance, refresh_appearance, set_slot};

/// Outfit names are 1-32 characters (the column's CHECK) after trimming.
pub const OUTFIT_NAME_MAX_CHARS: usize = 32;

/// A saved outfit in the API: `{ id, name, hat, body, legs, shoes,
/// created_at }`, each slot an item id or `null` (leave it empty).
#[derive(Debug, Clone, Serialize, sqlx::FromRow)]
pub struct OutfitView {
    pub id: i64,
    pub name: String,
    #[sqlx(rename = "hat_item_id")]
    pub hat: Option<i64>,
    #[sqlx(rename = "body_item_id")]
    pub body: Option<i64>,
    #[sqlx(rename = "legs_item_id")]
    pub legs: Option<i64>,
    #[sqlx(rename = "shoes_item_id")]
    pub shoes: Option<i64>,
    pub created_at: DateTime<Utc>,
}

impl OutfitView {
    fn item(&self, slot: GearSlot) -> Option<i64> {
        match slot {
            GearSlot::Hat => self.hat,
            GearSlot::Body => self.body,
            GearSlot::Legs => self.legs,
            GearSlot::Shoes => self.shoes,
        }
    }
}

const OUTFIT_COLUMNS: &str =
    "id, name, hat_item_id, body_item_id, legs_item_id, shoes_item_id, created_at";

/// Trims and checks an outfit name (`400 invalid_name`): 1-32 characters,
/// no control characters.
pub fn validate_outfit_name(raw: &str) -> Result<String, ApiError> {
    let name = raw.trim();
    let n = name.chars().count();
    if (1..=OUTFIT_NAME_MAX_CHARS).contains(&n) && !name.chars().any(char::is_control) {
        Ok(name.to_owned())
    } else {
        Err(ApiError::bad_request(
            "invalid_name",
            format!("Outfit names are 1 to {OUTFIT_NAME_MAX_CHARS} characters long."),
        ))
    }
}

fn outfit_not_found() -> ApiError {
    ApiError::not_found("outfit_not_found", "You have no such outfit.")
}

/// `GET /api/outfits?limit=&after=`: newest first.
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<OutfitView>>, ApiError> {
    let after: Option<i64> = page.after()?;
    let rows: Vec<OutfitView> = sqlx::query_as(&format!(
        "SELECT {OUTFIT_COLUMNS} FROM saved_outfits
         WHERE character_id = $1 AND ($2::bigint IS NULL OR id < $2)
         ORDER BY id DESC LIMIT $3"
    ))
    .bind(player.character.id)
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    Ok(Json(Page::from_rows(rows, page.limit()?, |o| o.id)))
}

#[derive(Debug, Deserialize)]
pub struct NameRequest {
    pub name: String,
}

/// `POST /api/outfits { name }` → `201 Outfit`, saving what is worn now.
pub async fn create(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(req): ApiJson<NameRequest>,
) -> Result<(StatusCode, Json<OutfitView>), ApiError> {
    let name = validate_outfit_name(&req.name)?;
    let id = player.character.id;
    let mut tx = state.pool.begin().await?;
    // The lock makes the count and the insert one step per character.
    lock_appearance(&mut tx, id).await?;
    let count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM saved_outfits WHERE character_id = $1")
            .bind(id)
            .fetch_one(&mut *tx)
            .await?;
    if count >= i64::from(SAVED_OUTFITS_MAX) {
        return Err(ApiError::conflict(
            "outfit_limit",
            format!("You can save up to {SAVED_OUTFITS_MAX} outfits. Delete one first."),
        ));
    }
    let outfit: OutfitView = sqlx::query_as(&format!(
        "INSERT INTO saved_outfits
             (character_id, name, hat_item_id, body_item_id, legs_item_id, shoes_item_id)
         SELECT $1, $2,
                max(item_id) FILTER (WHERE slot = 'hat'),
                max(item_id) FILTER (WHERE slot = 'body'),
                max(item_id) FILTER (WHERE slot = 'legs'),
                max(item_id) FILTER (WHERE slot = 'shoes')
         FROM equipment WHERE character_id = $1
         RETURNING {OUTFIT_COLUMNS}"
    ))
    .bind(id)
    .bind(&name)
    .fetch_one(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok((StatusCode::CREATED, Json(outfit)))
}

/// `PATCH /api/outfits/{id} { name }` → the renamed outfit.
pub async fn rename(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(outfit_id): ApiPath<i64>,
    ApiJson(req): ApiJson<NameRequest>,
) -> Result<Json<OutfitView>, ApiError> {
    let name = validate_outfit_name(&req.name)?;
    let outfit: Option<OutfitView> = sqlx::query_as(&format!(
        "UPDATE saved_outfits SET name = $3, updated_at = now()
         WHERE id = $1 AND character_id = $2
         RETURNING {OUTFIT_COLUMNS}"
    ))
    .bind(outfit_id)
    .bind(player.character.id)
    .bind(&name)
    .fetch_optional(&state.pool)
    .await?;
    outfit.map(Json).ok_or_else(outfit_not_found)
}

/// `DELETE /api/outfits/{id}` → `204`.
pub async fn delete(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(outfit_id): ApiPath<i64>,
) -> Result<StatusCode, ApiError> {
    let res = sqlx::query("DELETE FROM saved_outfits WHERE id = $1 AND character_id = $2")
        .bind(outfit_id)
        .bind(player.character.id)
        .execute(&state.pool)
        .await?;
    if res.rows_affected() == 0 {
        return Err(outfit_not_found());
    }
    Ok(StatusCode::NO_CONTENT)
}

/// `POST /api/outfits/{id}/wear` → `{ appearance, appearance_version, missing }`.
#[derive(Debug, Serialize)]
pub struct WearView {
    pub appearance: Appearance,
    pub appearance_version: u32,
    /// Slots left as they were because their item is no longer the
    /// character's, or is placed in the dorm.
    pub missing: Vec<GearSlot>,
}

/// Equips each slot's item and empties the slots saved empty, in one
/// transaction and one appearance change. A saved garment the body may not
/// wear refuses the whole outfit, `403 wear_restricted`.
pub async fn wear(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(outfit_id): ApiPath<i64>,
) -> Result<Json<WearView>, ApiError> {
    let id = player.character.id;
    let mut tx = state.pool.begin().await?;
    let before = lock_appearance(&mut tx, id).await?;
    let outfit: Option<OutfitView> = sqlx::query_as(&format!(
        "SELECT {OUTFIT_COLUMNS} FROM saved_outfits WHERE id = $1 AND character_id = $2"
    ))
    .bind(outfit_id)
    .bind(id)
    .fetch_optional(&mut *tx)
    .await?;
    let outfit = outfit.ok_or_else(outfit_not_found)?;
    let mut missing = Vec::new();
    for slot in GearSlot::ALL {
        match set_slot(&mut tx, id, before.wear(), slot, outfit.item(slot)).await {
            Ok(()) => {}
            Err(
                SlotError::NotFound
                | SlotError::Unavailable
                | SlotError::WrongSlot
                | SlotError::LevelTooLow { .. },
            ) => {
                missing.push(slot);
            }
            // A garment the body may not wear refuses the
            // whole outfit (`403 wear_restricted`; nothing changes).
            Err(e @ SlotError::WearRestricted(_)) => return Err(e.into()),
            Err(SlotError::Db(e)) => return Err(e.into()),
        }
    }
    let look = refresh_appearance(&mut tx, id, before).await?;
    tx.commit().await?;
    announce(&state, id, &look);
    Ok(Json(WearView {
        appearance: look.appearance,
        appearance_version: look.version,
        missing,
    }))
}

/// This module's routes, merged under `/api` by `http::router`.
pub fn routes() -> Router<AppState> {
    use axum::routing::{get, patch, post};
    let router = Router::new()
        .route("/outfits", get(list).post(create))
        .route("/outfits/{id}", patch(rename).delete(delete))
        .route("/outfits/{id}/wear", post(wear));
    write_limited(router, WARDROBE_RATE_LIMIT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn outfit_names() {
        assert_eq!(
            validate_outfit_name("  Sunday best ").unwrap(),
            "Sunday best"
        );
        assert!(validate_outfit_name(&"é".repeat(32)).is_ok());
        for bad in ["", "   ", "tab\there", &"x".repeat(33)] {
            assert_eq!(
                validate_outfit_name(bad).unwrap_err().code,
                "invalid_name",
                "{bad:?}"
            );
        }
    }
}
