//! The owl-order catalogue: the
//! basic items of `data/items/catalog.json` that have a `price`
//! (`Catalog::shop_items`), delivered by owl mail (readable anywhere).
//! A Crown sink that never sells crafted fashion (the data checks it).
//!
//! **A place.** The catalogue lies on a stand in each house common room
//! (interactables of kind `owl_order`, one per house). Ordering, and listing
//! unless [`BROWSE_ANYWHERE`], needs the station grant `Interact` with the
//! character's own house's stand gives (`realtime::stations::require` with
//! `StationKind::OwlOrder`): in the castle, in their common room, within the
//! stand's reach by the world's authoritative position. Away from it the
//! routes answer `403 not_at_station` ("Visit the Owl Order Catalogue in
//! your common room.").
//!
//! Routes: `GET /api/shop?kind=&category=&subcategory=&limit=&after=` and
//! `POST /api/shop/orders { lines: [{ item_def, quantity }], request_id? }`:
//! one transaction locks the buyer, creates a `shop_orders` row, takes the
//! total (`ledger::adjust_crowns`, `Reason::ShopOrder`) and sends a `shop`
//! mail holding the new items (fresh rows created into location `mail`,
//! `mail::deliver_new`). A repeated `request_id` answers the first order.

use std::collections::HashSet;

use axum::extract::State;
use axum::http::StatusCode;
use axum::{Json, Router};
use protocol::StationKind;
use serde::{Deserialize, Serialize};
use sim_core::ItemKind;
use sqlx::types::Json as DbJson;

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Page, PageQuery, Player, Writer, write_limited};
use crate::http::{ApiError, ApiJson, ApiQuery};
use crate::ledger::{self, Reason};
use crate::mail::{self, KIND_SHOP, NewMail, SENDER_OWL_ORDER};
use crate::realtime::stations;
use crate::wallet::{ECONOMY_RATE_LIMIT, item_error, ledger_error, lock_characters, request_key};

/// Whether `GET /api/shop` (browsing) works away from the stand. Ordering
/// always needs the stand. Off: the catalogue is a place players go to.
pub const BROWSE_ANYWHERE: bool = false;

/// Most lines in one order.
pub const ORDER_LINES_MAX: usize = 10;
/// Most units of one stackable item per line.
pub const LINE_UNITS_MAX: u32 = 99;
/// Most clothing or furniture items per line (one row each).
pub const LINE_PIECES_MAX: u32 = 10;

/// `GET /api/shop` filters.
#[derive(Debug, Default, Deserialize)]
pub struct ShopFilter {
    pub kind: Option<ItemKind>,
    pub category: Option<String>,
    pub subcategory: Option<String>,
}

/// One thing the catalogue sells: `{ item_def, price }` (name, kind and
/// look come from the catalog the client already has).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct ShopItem {
    pub item_def: u16,
    pub price: u32,
}

fn invalid_query(message: &'static str) -> ApiError {
    ApiError::bad_request("invalid_query", message)
}

/// `GET /api/shop`: what the catalogue sells, by item id.
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(filter): ApiQuery<ShopFilter>,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<ShopItem>>, ApiError> {
    if !BROWSE_ANYWHERE {
        stations::require(&state, player.character.id, StationKind::OwlOrder).await?;
    }
    let after: Option<u16> = page.after()?;
    let category = filter.category.as_deref();
    let subcategory = filter.subcategory.as_deref();
    if category == Some("") || subcategory == Some("") {
        return Err(invalid_query("Filters may not be empty."));
    }
    match (category, subcategory) {
        (None, Some(_)) => return Err(invalid_query("A subcategory needs its category.")),
        (Some(c), s) if !sim_core::exchange::is_valid(c, s) => {
            return Err(invalid_query("Unknown category or subcategory."));
        }
        _ => {}
    }
    let mut items: Vec<ShopItem> = shipped()
        .catalog
        .shop_items()
        .filter(|d| filter.kind.is_none_or(|k| d.kind == k))
        .filter(|d| category.is_none_or(|c| d.exchange.category == c))
        .filter(|d| subcategory.is_none_or(|s| d.exchange.subcategory == s))
        .filter(|d| after.is_none_or(|a| d.id > a))
        .filter_map(|d| {
            Some(ShopItem {
                item_def: d.id,
                price: d.price?,
            })
        })
        .collect();
    items.sort_by_key(|i| i.item_def);
    items.truncate(usize::try_from(page.fetch_limit()?).unwrap_or(usize::MAX));
    Ok(Json(Page::from_rows(items, page.limit()?, |i| i.item_def)))
}

/// One line of an order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct OrderLine {
    pub item_def: u16,
    pub quantity: u32,
}

/// A line as stored in `shop_orders.lines` and answered: with its unit price.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct PricedLine {
    pub item_def: u16,
    pub quantity: u32,
    pub unit_price: u32,
}

/// `POST /api/shop/orders` body.
#[derive(Debug, Deserialize)]
pub struct PlaceOrder {
    pub lines: Vec<OrderLine>,
    #[serde(default)]
    pub request_id: Option<String>,
}

/// `{ id, total, lines, mail_id }`.
#[derive(Debug, Clone, Serialize, sqlx::FromRow)]
pub struct OrderView {
    pub id: i64,
    pub total: i64,
    pub lines: DbJson<Vec<PricedLine>>,
    pub mail_id: Option<i64>,
}

/// `201 { order, crowns }`.
#[derive(Debug, Serialize)]
pub struct Ordered {
    pub order: OrderView,
    pub crowns: i64,
}

fn invalid_order(message: &'static str) -> ApiError {
    ApiError::bad_request("invalid_order", message)
}

/// Prices an order: every line a distinct item the catalogue sells, in an
/// allowed quantity; the total in checked arithmetic.
pub fn price(lines: &[OrderLine]) -> Result<(Vec<PricedLine>, i64), ApiError> {
    if lines.is_empty() || lines.len() > ORDER_LINES_MAX {
        return Err(invalid_order("An order has 1-10 lines."));
    }
    let mut seen = HashSet::new();
    let mut priced = Vec::with_capacity(lines.len());
    let mut total: i64 = 0;
    for line in lines {
        let def = shipped()
            .catalog
            .get(line.item_def)
            .filter(|d| d.in_shop())
            .ok_or_else(|| invalid_order("The catalogue does not sell that."))?;
        let unit_price = def
            .price
            .ok_or_else(|| invalid_order("The catalogue does not sell that."))?;
        let max = if def.stackable {
            LINE_UNITS_MAX
        } else {
            LINE_PIECES_MAX
        };
        if !(1..=max).contains(&line.quantity) {
            return Err(invalid_order("That quantity cannot be ordered."));
        }
        if !seen.insert(line.item_def) {
            return Err(invalid_order("Each item may be ordered on one line."));
        }
        let cost = i64::from(unit_price)
            .checked_mul(i64::from(line.quantity))
            .and_then(|c| total.checked_add(c))
            .ok_or_else(|| invalid_order("That order is too large."))?;
        total = cost;
        priced.push(PricedLine {
            item_def: line.item_def,
            quantity: line.quantity,
            unit_price,
        });
    }
    Ok((priced, total))
}

/// `POST /api/shop/orders`: pays and sends the delivery mail at once.
pub async fn order(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(req): ApiJson<PlaceOrder>,
) -> Result<(StatusCode, Json<Ordered>), ApiError> {
    let me = player.character.id;
    let key = request_key(req.request_id.as_deref())?;
    let (lines, total) = price(&req.lines)?;
    stations::require(&state, me, StationKind::OwlOrder).await?;

    let mut tx = state.pool.begin().await?;
    lock_characters(&mut tx, &[me]).await?;
    if let Some(key) = &key {
        let earlier: Option<OrderView> = sqlx::query_as(
            "SELECT id, total, lines, mail_id FROM shop_orders
             WHERE character_id = $1 AND client_key = $2",
        )
        .bind(me)
        .bind(key)
        .fetch_optional(&mut *tx)
        .await?;
        if let Some(order) = earlier {
            let crowns = ledger::adjust_crowns(&mut tx, me, 0, Reason::ShopOrder, None)
                .await
                .map_err(ledger_error)?;
            tx.commit().await?;
            return Ok((StatusCode::CREATED, Json(Ordered { order, crowns })));
        }
    }
    let order_id: i64 = sqlx::query_scalar(
        "INSERT INTO shop_orders (character_id, lines, total, client_key)
         VALUES ($1, $2, $3, $4) RETURNING id",
    )
    .bind(me)
    .bind(DbJson(&lines))
    .bind(total)
    .bind(key.as_deref())
    .fetch_one(&mut *tx)
    .await?;
    let crowns = ledger::adjust_crowns(&mut tx, me, -total, Reason::ShopOrder, Some(order_id))
        .await
        .map_err(ledger_error)?;
    let subject = match lines.as_slice() {
        [one] => mail::subject_about("Delivery", one.item_def, one.quantity),
        _ => format!("Delivery: your order of {} items", lines.len()),
    };
    let mail_id = mail::create(
        &mut tx,
        &NewMail {
            recipient: me,
            sender_id: None,
            sender_name: SENDER_OWL_ORDER,
            kind: KIND_SHOP,
            subject: &subject,
            body: "",
            crowns: 0,
            ref_id: Some(order_id),
            client_key: None,
            with_items: true,
        },
    )
    .await?;
    for line in &lines {
        mail::deliver_new(
            &mut tx,
            mail_id,
            me,
            line.item_def,
            line.quantity,
            Reason::ShopOrder,
            order_id,
        )
        .await
        .map_err(item_error)?;
    }
    sqlx::query("UPDATE shop_orders SET mail_id = $2 WHERE id = $1")
        .bind(order_id)
        .bind(mail_id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;

    if let Some(world) = state.world() {
        world.wallet_changed(me, crowns);
    }
    mail::notify(&state, me).await;
    let order = OrderView {
        id: order_id,
        total,
        lines: DbJson(lines),
        mail_id: Some(mail_id),
    };
    Ok((StatusCode::CREATED, Json(Ordered { order, crowns })))
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::{get, post};
    let router = Router::new()
        .route("/shop", get(list))
        .route("/shop/orders", post(order));
    write_limited(router, ECONOMY_RATE_LIMIT)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sold() -> (u16, u32, bool) {
        let d = shipped()
            .catalog
            .shop_items()
            .find(|d| d.stackable)
            .unwrap();
        (d.id, d.price.unwrap(), d.stackable)
    }

    #[test]
    fn orders_are_priced_with_checked_totals() {
        let (id, unit, _) = sold();
        let (lines, total) = price(&[OrderLine {
            item_def: id,
            quantity: 3,
        }])
        .unwrap();
        assert_eq!(total, i64::from(unit) * 3);
        assert_eq!(lines[0].unit_price, unit);
        let bad = |lines: &[OrderLine]| price(lines).unwrap_err().code;
        assert_eq!(bad(&[]), "invalid_order");
        let line = |q| OrderLine {
            item_def: id,
            quantity: q,
        };
        assert_eq!(bad(&[line(0)]), "invalid_order");
        assert_eq!(bad(&[line(100)]), "invalid_order");
        assert_eq!(bad(&[line(1), line(2)]), "invalid_order", "duplicate line");
        // Crafted items are never sold.
        let crafted = shipped()
            .catalog
            .items
            .iter()
            .find(|d| d.price.is_none())
            .unwrap();
        assert_eq!(
            bad(&[OrderLine {
                item_def: crafted.id,
                quantity: 1
            }]),
            "invalid_order"
        );
        // Clothing and furniture: at most ten a line.
        let piece = shipped()
            .catalog
            .shop_items()
            .find(|d| !d.stackable)
            .unwrap();
        let p = |q| OrderLine {
            item_def: piece.id,
            quantity: q,
        };
        assert!(price(&[p(10)]).is_ok());
        assert_eq!(bad(&[p(11)]), "invalid_order");
    }
}
