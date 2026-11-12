//! Item instances (`items`), equipment (`equipment`) and dyeing: the routes
//! `GET /api/inventory`, `GET`/`POST /api/equipment` and
//! `POST /api/items/{id}/dye` (docs/architecture.md, "HTTP API"), plus the
//! starter grant for new characters ([`starter`]).
//!
//! Owned by the data side. `items.location` is `inventory` or `equipped`
//! under this module's control; `dorm` belongs to the realtime dorm code
//! (`crate::dorm`), which moves furniture between `inventory` and `dorm`.
//! Adds `mail` (attached to owl mail, owned by the recipient)
//! and `exchange` (listed on the Owl Exchange, owned by the seller), which
//! the economy code moves (`crate::items`). Items anywhere but the inventory
//! or worn can be neither worn nor dyed here (`409 item_unavailable`).
//!
//! The dye route lives in [`dye`] (dyes are consumed when
//! applied).

pub mod dye;
pub(crate) mod equip;
pub mod starter;

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{Appearance, Finish, GearSlot};
use serde::{Deserialize, Serialize};
use sim_core::ItemKind;
use std::time::Duration;

use self::equip::{announce, lock_appearance, refresh_appearance, set_slot};
use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Page, PageQuery, Player, Writer, write_limited};
use crate::http::{ApiError, ApiJson, ApiQuery};
use crate::rate_limit::RateLimit;

/// `items.location` values.
pub const LOCATION_INVENTORY: &str = "inventory";
pub const LOCATION_EQUIPPED: &str = "equipped";
pub const LOCATION_DORM: &str = "dorm";
/// Attached to owl mail (owned by the recipient until collected).
pub const LOCATION_MAIL: &str = "mail";
/// Listed on the Owl Exchange (owned by the seller until sold or returned).
pub const LOCATION_EXCHANGE: &str = "exchange";

/// Wardrobe writes (equip, dye, outfits) per account: a burst for trying
/// things on, then one a second. Each change that shows is broadcast to
/// everyone who can see the character, so this also caps that traffic.
pub const WARDROBE_RATE_LIMIT: RateLimit = RateLimit {
    burst: 30,
    per: Duration::from_secs(1),
};

/// Dye columns as palette indices (the columns' CHECKs keep them in 0..=255).
pub(crate) fn dyes_from_db(p: i16, s: i16, t: i16) -> [u8; 3] {
    [p, s, t].map(|d| u8::try_from(d).unwrap_or(0))
}

/// An item instance in the API: `{ id, item_def, dyes, location, slot,
/// created_at, quantity, maker, finish }`. What the item is (kind, slot,
/// name, asset) comes from the catalog by `item_def`; `slot` here is where
/// it is worn, if it is. `quantity` (1 for clothing and
/// furniture), `maker` (the crafter's name on crafted items, else null) and
/// `finish` (`"none" | "shimmer" | "colour_shift"`).
#[derive(Debug, Clone, Serialize)]
pub struct ItemView {
    pub id: i64,
    pub item_def: u16,
    pub dyes: [u8; 3],
    pub location: String,
    pub slot: Option<GearSlot>,
    pub created_at: DateTime<Utc>,
    pub quantity: u32,
    pub maker: Option<String>,
    pub finish: Finish,
}

#[derive(Debug, sqlx::FromRow)]
pub(crate) struct ItemRow {
    id: i64,
    item_def: i32,
    dye_primary: i16,
    dye_secondary: i16,
    dye_trim: i16,
    location: String,
    slot: Option<String>,
    created_at: DateTime<Utc>,
    quantity: i32,
    maker_name: Option<String>,
    finish: i16,
}

/// Selects [`ItemRow`]s as `i` with their equipment slot; append a `WHERE`.
pub(crate) const ITEM_SELECT: &str = "SELECT i.id, i.item_def, i.dye_primary, i.dye_secondary,
            i.dye_trim, i.location, e.slot, i.created_at, i.quantity, i.maker_name, i.finish
     FROM items i LEFT JOIN equipment e ON e.item_id = i.id";

impl From<ItemRow> for ItemView {
    fn from(r: ItemRow) -> Self {
        ItemView {
            id: r.id,
            item_def: u16::try_from(r.item_def).unwrap_or(0),
            dyes: dyes_from_db(r.dye_primary, r.dye_secondary, r.dye_trim),
            location: r.location,
            slot: r.slot.as_deref().and_then(GearSlot::from_id),
            created_at: r.created_at,
            quantity: u32::try_from(r.quantity).unwrap_or(1),
            maker: r.maker_name,
            finish: crate::items::finish_from_db(r.finish),
        }
    }
}

/// `?location=` values.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ItemLocation {
    Inventory,
    Equipped,
    Dorm,
    Mail,
    Exchange,
}

impl ItemLocation {
    pub fn to_db(self) -> &'static str {
        match self {
            ItemLocation::Inventory => LOCATION_INVENTORY,
            ItemLocation::Equipped => LOCATION_EQUIPPED,
            ItemLocation::Dorm => LOCATION_DORM,
            ItemLocation::Mail => LOCATION_MAIL,
            ItemLocation::Exchange => LOCATION_EXCHANGE,
        }
    }
}

/// `GET /api/inventory` filters (all optional).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct InventoryFilter {
    pub kind: Option<ItemKind>,
    pub slot: Option<GearSlot>,
    pub location: Option<ItemLocation>,
}

impl InventoryFilter {
    /// The catalog ids matching `kind` and `slot`, or `None` for no filter.
    fn item_defs(&self) -> Option<Vec<i32>> {
        if self.kind.is_none() && self.slot.is_none() {
            return None;
        }
        let defs = shipped()
            .catalog
            .items
            .iter()
            .filter(|i| self.kind.is_none_or(|k| i.kind == k))
            .filter(|i| self.slot.is_none_or(|s| i.slot == Some(s)))
            .map(|i| i32::from(i.id))
            .collect();
        Some(defs)
    }
}

/// `GET /api/inventory?kind=&slot=&location=&limit=&after=`: the player's
/// items, newest first.
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(filter): ApiQuery<InventoryFilter>,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<ItemView>>, ApiError> {
    let after: Option<i64> = page.after()?;
    let rows: Vec<ItemRow> = sqlx::query_as(&format!(
        "{ITEM_SELECT}
         WHERE i.owner_id = $1
           AND ($2::int[] IS NULL OR i.item_def = ANY($2))
           AND ($3::text IS NULL OR i.location = $3)
           AND ($4::bigint IS NULL OR i.id < $4)
         ORDER BY i.id DESC
         LIMIT $5"
    ))
    .bind(player.character.id)
    .bind(filter.item_defs())
    .bind(filter.location.map(ItemLocation::to_db))
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    let page = Page::from_rows(rows, page.limit()?, |r| r.id);
    Ok(Json(page.map(ItemView::from)))
}

/// `GET /api/equipment` → `{ hat, body, legs, shoes, appearance_version }`.
#[derive(Debug, Serialize)]
pub struct EquipmentView {
    pub hat: Option<ItemView>,
    pub body: Option<ItemView>,
    pub legs: Option<ItemView>,
    pub shoes: Option<ItemView>,
    pub appearance_version: u32,
}

pub async fn equipment(
    State(state): State<AppState>,
    player: Player,
) -> Result<Json<EquipmentView>, ApiError> {
    let rows: Vec<ItemRow> = sqlx::query_as(&format!(
        "{ITEM_SELECT} WHERE i.owner_id = $1 AND e.character_id = $1"
    ))
    .bind(player.character.id)
    .fetch_all(&state.pool)
    .await?;
    let mut view = EquipmentView {
        hat: None,
        body: None,
        legs: None,
        shoes: None,
        appearance_version: player.character.appearance_version,
    };
    for item in rows.into_iter().map(ItemView::from) {
        let slot = match item.slot {
            Some(GearSlot::Hat) => &mut view.hat,
            Some(GearSlot::Body) => &mut view.body,
            Some(GearSlot::Legs) => &mut view.legs,
            Some(GearSlot::Shoes) => &mut view.shoes,
            None => continue,
        };
        *slot = Some(item);
    }
    Ok(Json(view))
}

#[derive(Debug, Deserialize)]
pub struct EquipRequest {
    pub slot: GearSlot,
    /// `null` (or absent) empties the slot.
    #[serde(default)]
    pub item_id: Option<i64>,
}

/// The look after a wardrobe change: `{ appearance, appearance_version }`.
#[derive(Debug, Serialize)]
pub struct LookView {
    pub appearance: Appearance,
    pub appearance_version: u32,
}

/// `POST /api/equipment { slot, item_id | null }`: wears the item in the slot
/// (the previous one goes back to the inventory) or empties it.
pub async fn equip(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(req): ApiJson<EquipRequest>,
) -> Result<Json<LookView>, ApiError> {
    let id = player.character.id;
    let mut tx = state.pool.begin().await?;
    let before = lock_appearance(&mut tx, id).await?;
    set_slot(&mut tx, id, before.wear(), req.slot, req.item_id).await?;
    let look = refresh_appearance(&mut tx, id, before).await?;
    tx.commit().await?;
    announce(&state, id, &look);
    Ok(Json(LookView {
        appearance: look.appearance,
        appearance_version: look.version,
    }))
}

/// This module's routes, merged under `/api` by `http::router`.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    let router = Router::new()
        .route("/inventory", get(list))
        .route("/equipment", get(equipment).post(equip))
        .merge(dye::routes());
    write_limited(router, WARDROBE_RATE_LIMIT)
}
