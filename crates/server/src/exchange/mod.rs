//! The Owl Exchange: fixed
//! buyout listings for 5 real days, a flat listing fee, sales tax removed
//! from the economy, purchases and proceeds by owl mail, a per-player
//! active-listing limit and rate-limited searches. Only at the counter in
//! the Entrance Hall: browsing, listing and buying need
//! `realtime::stations::require(state, character, StationKind::OwlExchange)`;
//! a seller's own listings and take-downs work anywhere.
//!
//! Routes (docs/architecture.md, "HTTP API"):
//! `GET /api/exchange/listings?category&subcategory&q&min_level&max_level&colour&house&sort=price|price_desc|newest&limit&after`
//! ([`search`]), `GET /api/exchange/mine?state=&limit=&after=`,
//! `POST /api/exchange/listings`, `DELETE /api/exchange/listings/{id}`,
//! `POST /api/exchange/listings/{id}/buy`.
//!
//! Integrity: each write is one transaction locking the characters whose
//! items or Crowns change in id order (seller and buyer), then the
//! listing row, then the item. A listed item is its own row (a stack is
//! split for it), owned by the seller with location `exchange`; closing a
//! listing (sold, taken down, expired) clears its `item_id` in the same
//! statement that changes its state, then mails the item on. Two buyers of
//! one listing serialize on the listing row and the second finds it closed
//! (`409 listing_closed`); a take-down racing a purchase does the same.
//!
//! Background job: [`expire_listings`] (called every minute by `main.rs`)
//! closes listings past `expires_at` and mails the items back.

pub mod search;

use axum::extract::State;
use axum::http::StatusCode;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{Finish, StationKind};
use serde::{Deserialize, Serialize};
use sqlx::{PgConnection, PgPool};

use self::search::{BrowseQuery, Cursor, Search};
use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{
    ApiPath, Page, PageQuery, Player, Reader, Writer, read_limited, write_limited,
};
use crate::http::{ApiError, ApiJson, ApiQuery};
use crate::inventory::LOCATION_EXCHANGE;
use crate::inventory::LOCATION_INVENTORY;
use crate::inventory::equip::{item_not_found, item_unavailable};
use crate::items::{self, finish_from_db, finish_to_db};
use crate::ledger::{self, Reason};
use crate::mail::{self, NewMail, SENDER_EXCHANGE};
use crate::rate_limit::RateLimit;
use crate::realtime::stations;
use crate::realtime::world::WorldHandle;
use crate::wallet::{
    ECONOMY_RATE_LIMIT, item_error, ledger_error, lock_characters, request_key, split_row,
};

/// `exchange_listings.state` values.
pub const STATE_ACTIVE: &str = "active";
pub const STATE_SOLD: &str = "sold";
pub const STATE_CANCELLED: &str = "cancelled";
pub const STATE_EXPIRED: &str = "expired";

/// A listing in the API: `{ id, item_def, quantity, price, dyes, finish,
/// maker, seller_name, category, subcategory, required_level, colour,
/// house_theme, created_at, expires_at, state }`.
#[derive(Debug, Clone, Serialize)]
pub struct Listing {
    pub id: i64,
    pub item_def: u16,
    pub quantity: u32,
    pub price: i64,
    pub dyes: [u8; 3],
    pub finish: Finish,
    pub maker: Option<String>,
    pub seller_name: String,
    pub category: String,
    pub subcategory: String,
    pub required_level: u8,
    pub colour: u8,
    pub house_theme: Option<String>,
    pub created_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub state: String,
}

#[derive(Debug, sqlx::FromRow)]
pub(crate) struct ListingRow {
    id: i64,
    item_def: i32,
    quantity: i32,
    price: i64,
    colour: i16,
    dye_secondary: i16,
    dye_trim: i16,
    finish: i16,
    maker_name: Option<String>,
    seller_name: String,
    category: String,
    subcategory: String,
    required_level: i16,
    house_theme: Option<String>,
    created_at: DateTime<Utc>,
    expires_at: DateTime<Utc>,
    state: String,
}

impl From<ListingRow> for Listing {
    fn from(r: ListingRow) -> Self {
        let byte = |v: i16| u8::try_from(v).unwrap_or(0);
        Listing {
            id: r.id,
            item_def: u16::try_from(r.item_def).unwrap_or(0),
            quantity: u32::try_from(r.quantity).unwrap_or(1),
            price: r.price,
            dyes: [byte(r.colour), byte(r.dye_secondary), byte(r.dye_trim)],
            finish: finish_from_db(r.finish),
            maker: r.maker_name,
            seller_name: r.seller_name,
            category: r.category,
            subcategory: r.subcategory,
            required_level: byte(r.required_level),
            colour: byte(r.colour),
            house_theme: r.house_theme,
            created_at: r.created_at,
            expires_at: r.expires_at,
            state: r.state,
        }
    }
}

/// The `exchange_listings` columns of a [`ListingRow`] (as `x`; the seller's
/// name comes from a join).
pub(crate) const LISTING_FIELDS: &str = "x.id, x.seller_id, x.item_def, x.quantity, x.price,
            x.colour, x.dye_secondary, x.dye_trim, x.finish, x.maker_name, x.category,
            x.subcategory, x.required_level, x.house_theme, x.created_at, x.expires_at, x.state";

/// Selects [`ListingRow`]s as `x`; append a `WHERE`.
pub(crate) const LISTING_SELECT: &str = "SELECT x.id, x.item_def, x.quantity, x.price, x.colour,
            x.dye_secondary, x.dye_trim, x.finish, x.maker_name, c.name AS seller_name,
            x.category, x.subcategory, x.required_level, x.house_theme, x.created_at,
            x.expires_at, x.state
     FROM exchange_listings x JOIN characters c ON c.id = x.seller_id";

async fn listing(conn: &mut PgConnection, id: i64) -> Result<Listing, sqlx::Error> {
    let row: ListingRow = sqlx::query_as(&format!("{LISTING_SELECT} WHERE x.id = $1"))
        .bind(id)
        .fetch_one(conn)
        .await?;
    Ok(row.into())
}

fn listing_not_found() -> ApiError {
    ApiError::not_found("listing_not_found", "There is no such listing.")
}

fn listing_closed() -> ApiError {
    ApiError::conflict(
        "listing_closed",
        "That listing has already been sold, taken down or has run out.",
    )
}

// ------------------------------------------------------------------ browse

/// `GET /api/exchange/listings`: one page of active listings. At the
/// counter only, and rate limited per account (`exchange.search_burst`).
pub async fn browse(
    State(state): State<AppState>,
    Reader(player): Reader,
    ApiQuery(query): ApiQuery<BrowseQuery>,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<Listing>>, ApiError> {
    stations::require(&state, player.character.id, StationKind::OwlExchange).await?;
    let after: Option<Cursor> = page.after()?;
    let search = Search::from_query(&query, after, page.fetch_limit()?)?;
    if !search.can_match() {
        return Ok(Json(Page {
            items: Vec::new(),
            next: None,
        }));
    }
    let rows: Vec<ListingRow> = search::build(&search, "")
        .build_query_as()
        .fetch_all(&state.pool)
        .await?;
    let page = Page::from_rows(rows, page.limit()?, |r| search.cursor(r.price, r.id));
    Ok(Json(page.map(Listing::from)))
}

/// `?state=` of `GET /api/exchange/mine`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateFilter {
    Active,
    Sold,
    Cancelled,
    Expired,
}

impl StateFilter {
    fn to_db(self) -> &'static str {
        match self {
            StateFilter::Active => STATE_ACTIVE,
            StateFilter::Sold => STATE_SOLD,
            StateFilter::Cancelled => STATE_CANCELLED,
            StateFilter::Expired => STATE_EXPIRED,
        }
    }
}

#[derive(Debug, Default, Deserialize)]
pub struct MineQuery {
    pub state: Option<StateFilter>,
}

/// `GET /api/exchange/mine?state=&limit=&after=`: the character's listings,
/// newest first (index `exchange_listings (seller_id, id)`), anywhere.
pub async fn mine(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(query): ApiQuery<MineQuery>,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<Listing>>, ApiError> {
    let after: Option<i64> = page.after()?;
    let rows: Vec<ListingRow> = sqlx::query_as(&format!(
        "{LISTING_SELECT}
         WHERE x.seller_id = $1 AND ($2::text IS NULL OR x.state = $2)
           AND ($3::bigint IS NULL OR x.id < $3)
         ORDER BY x.id DESC LIMIT $4"
    ))
    .bind(player.character.id)
    .bind(query.state.map(StateFilter::to_db))
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    let page = Page::from_rows(rows, page.limit()?, |r| r.id);
    Ok(Json(page.map(Listing::from)))
}

// ------------------------------------------------------------------ list

/// `POST /api/exchange/listings` body.
#[derive(Debug, Deserialize)]
pub struct NewListing {
    pub item_id: i64,
    #[serde(default = "one")]
    pub quantity: u32,
    pub price: i64,
    #[serde(default)]
    pub request_id: Option<String>,
}

fn one() -> u32 {
    1
}

/// `201 { listing, crowns }`.
#[derive(Debug, Serialize)]
pub struct Listed {
    pub listing: Listing,
    pub crowns: i64,
}

/// `POST /api/exchange/listings`: lists units of an inventory item for
/// `listing_secs` at a fixed price; pays the fee. At the counter only.
pub async fn create(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(req): ApiJson<NewListing>,
) -> Result<(StatusCode, Json<Listed>), ApiError> {
    let me = player.character.id;
    let rules = &shipped().economy.exchange;
    let key = request_key(req.request_id.as_deref())?;
    if !rules.price_ok(req.price) {
        return Err(ApiError::bad_request(
            "invalid_price",
            format!(
                "Prices go from {} to {} Crowns.",
                rules.price_min, rules.price_max
            ),
        ));
    }
    if req.quantity == 0 {
        return Err(ApiError::bad_request(
            "invalid_body",
            "quantity must be at least 1.",
        ));
    }
    stations::require(&state, me, StationKind::OwlExchange).await?;

    let mut tx = state.pool.begin().await?;
    lock_characters(&mut tx, &[me]).await?;
    if let Some(key) = &key {
        let earlier: Option<i64> = sqlx::query_scalar(
            "SELECT id FROM exchange_listings WHERE seller_id = $1 AND client_key = $2",
        )
        .bind(me)
        .bind(key)
        .fetch_optional(&mut *tx)
        .await?;
        if let Some(id) = earlier {
            let listing = listing(&mut tx, id).await?;
            let crowns = ledger::adjust_crowns(&mut tx, me, 0, Reason::ExchangeFee, None)
                .await
                .map_err(ledger_error)?;
            tx.commit().await?;
            return Ok((StatusCode::CREATED, Json(Listed { listing, crowns })));
        }
    }
    let active: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM exchange_listings WHERE seller_id = $1 AND state = 'active'",
    )
    .bind(me)
    .fetch_one(&mut *tx)
    .await?;
    if active >= i64::from(rules.active_listings_max) {
        return Err(ApiError::conflict(
            "listing_limit",
            format!(
                "You already have {} items on the Exchange.",
                rules.active_listings_max
            ),
        ));
    }
    let item = items::lock(&mut tx, me, &[req.item_id])
        .await?
        .pop()
        .ok_or_else(item_not_found)?;
    if item.location != LOCATION_INVENTORY || req.quantity > item.quantity {
        return Err(item_unavailable());
    }
    let def = shipped()
        .catalog
        .get(item.item_def)
        .ok_or_else(|| ApiError::internal(format!("item {} has no definition", item.id)))?;
    let fee = i64::from(rules.listing_fee);
    // The listing first (on the stack row, which the seller owns), so the
    // ledger rows below can name it; then the units split off and listed.
    let id: i64 = sqlx::query_scalar(
        "INSERT INTO exchange_listings
             (seller_id, item_id, item_def, quantity, price, fee, category, subcategory,
              required_level, colour, dye_secondary, dye_trim, finish, house_theme, maker_name,
              expires_at, client_key)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                 now() + make_interval(secs => $16), $17)
         RETURNING id",
    )
    .bind(me)
    .bind(item.id)
    .bind(i32::from(item.item_def))
    .bind(i32::try_from(req.quantity).unwrap_or(i32::MAX))
    .bind(req.price)
    .bind(fee)
    .bind(&def.exchange.category)
    .bind(&def.exchange.subcategory)
    .bind(i16::from(def.required_level))
    .bind(i16::from(item.dyes[0]))
    .bind(i16::from(item.dyes[1]))
    .bind(i16::from(item.dyes[2]))
    .bind(finish_to_db(item.finish))
    .bind(def.house_theme.as_deref())
    .bind(item.maker_name.as_deref())
    .bind(f64::from(rules.listing_secs))
    .bind(key.as_deref())
    .fetch_one(&mut *tx)
    .await?;
    let part = items::take(&mut tx, &item, req.quantity, Reason::ExchangeList, Some(id))
        .await
        .map_err(item_error)?;
    let listed = split_row(&item, part, req.quantity);
    items::move_to(
        &mut tx,
        &listed,
        me,
        LOCATION_EXCHANGE,
        Reason::ExchangeList,
        Some(id),
    )
    .await
    .map_err(item_error)?;
    if part != item.id {
        sqlx::query("UPDATE exchange_listings SET item_id = $2 WHERE id = $1")
            .bind(id)
            .bind(part)
            .execute(&mut *tx)
            .await?;
    }
    let crowns = ledger::adjust_crowns(&mut tx, me, -fee, Reason::ExchangeFee, Some(id))
        .await
        .map_err(ledger_error)?;
    let listing = listing(&mut tx, id).await?;
    tx.commit().await?;

    if fee > 0
        && let Some(world) = state.world()
    {
        world.wallet_changed(me, crowns);
    }
    Ok((StatusCode::CREATED, Json(Listed { listing, crowns })))
}

// ------------------------------------------------------------------ close

/// A listing row locked for closing.
#[derive(Debug, sqlx::FromRow)]
struct OpenListing {
    id: i64,
    seller_id: i64,
    item_id: Option<i64>,
    item_def: i32,
    quantity: i32,
    price: i64,
    state: String,
    expired: bool,
}

/// Locks listing `id` (after the characters).
async fn lock_listing(
    conn: &mut PgConnection,
    id: i64,
) -> Result<Option<OpenListing>, sqlx::Error> {
    sqlx::query_as(
        "SELECT id, seller_id, item_id, item_def, quantity, price, state,
                expires_at <= now() AS expired
         FROM exchange_listings WHERE id = $1 AND seller_id IS NOT NULL FOR UPDATE",
    )
    .bind(id)
    .fetch_optional(conn)
    .await
}

/// Closes an active listing as `state` and mails its item back to the
/// seller (`kind`, `Reason::ExchangeReturn`). The seller's row and the
/// listing row are locked. Returns the mail id.
async fn close_and_return(
    conn: &mut PgConnection,
    open: &OpenListing,
    state: &str,
    kind: &'static str,
    subject: &str,
) -> Result<i64, ApiError> {
    let item_id = open
        .item_id
        .ok_or_else(|| ApiError::internal(format!("active listing {} has no item", open.id)))?;
    let item = items::lock(conn, open.seller_id, &[item_id])
        .await?
        .pop()
        .filter(|i| i.location == LOCATION_EXCHANGE)
        .ok_or_else(|| ApiError::internal(format!("listing {}: item is not listed", open.id)))?;
    sqlx::query(
        "UPDATE exchange_listings SET state = $2, item_id = NULL, closed_at = now() WHERE id = $1",
    )
    .bind(open.id)
    .bind(state)
    .execute(&mut *conn)
    .await?;
    let mail_id = mail::create(
        conn,
        &NewMail {
            recipient: open.seller_id,
            sender_id: None,
            sender_name: SENDER_EXCHANGE,
            kind,
            subject,
            body: "",
            crowns: 0,
            ref_id: Some(open.id),
            client_key: None,
            with_items: true,
        },
    )
    .await?;
    mail::attach(
        conn,
        mail_id,
        open.seller_id,
        &item,
        Reason::ExchangeReturn,
        open.id,
    )
    .await
    .map_err(item_error)?;
    Ok(mail_id)
}

fn def_u16(def: i32) -> u16 {
    u16::try_from(def).unwrap_or(0)
}

fn units(quantity: i32) -> u32 {
    u32::try_from(quantity).unwrap_or(1)
}

/// `DELETE /api/exchange/listings/{id}`: the seller takes a listing down
/// (anywhere); the item comes back by mail and the fee stays paid.
pub async fn take_down(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(id): ApiPath<i64>,
) -> Result<StatusCode, ApiError> {
    let me = player.character.id;
    let mut tx = state.pool.begin().await?;
    lock_characters(&mut tx, &[me]).await?;
    let open = lock_listing(&mut tx, id)
        .await?
        .filter(|l| l.seller_id == me)
        .ok_or_else(listing_not_found)?;
    if open.state != STATE_ACTIVE {
        return Err(listing_closed());
    }
    let subject = mail::subject_about("Returned", def_u16(open.item_def), units(open.quantity));
    close_and_return(
        &mut tx,
        &open,
        STATE_CANCELLED,
        mail::KIND_EXCHANGE_RETURNED,
        &subject,
    )
    .await?;
    tx.commit().await?;
    mail::notify(&state, me).await;
    Ok(StatusCode::NO_CONTENT)
}

/// `200 { mail_id, crowns }`. A purchase is recognised by its listing:
/// a repeat by the same buyer answers the first result, so the optional
/// `request_id` of the body is not needed (and not read).
#[derive(Debug, Serialize)]
pub struct Bought {
    pub mail_id: i64,
    pub crowns: i64,
}

/// The `exchange_bought` mail of an earlier purchase of listing `id` by `buyer`.
async fn earlier_purchase(
    conn: &mut PgConnection,
    id: i64,
    buyer: i64,
) -> Result<Option<Bought>, sqlx::Error> {
    let row: Option<(i64, i64)> = sqlx::query_as(
        "SELECT m.id, c.crowns FROM exchange_listings x
         JOIN mail m ON m.recipient_id = x.buyer_id AND m.kind = $3 AND m.ref_id = x.id
         JOIN characters c ON c.id = x.buyer_id
         WHERE x.id = $1 AND x.buyer_id = $2 AND x.state = 'sold'",
    )
    .bind(id)
    .bind(buyer)
    .bind(mail::KIND_EXCHANGE_BOUGHT)
    .fetch_optional(conn)
    .await?;
    Ok(row.map(|(mail_id, crowns)| Bought { mail_id, crowns }))
}

/// Buys a listing: one transaction locks buyer and seller (id order), then
/// the listing; the buyer pays the price and gets the item by mail, the
/// seller gets the price minus tax by mail, the tax leaves the economy.
pub async fn buy(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(id): ApiPath<i64>,
) -> Result<Json<Bought>, ApiError> {
    let me = player.character.id;
    let rules = &shipped().economy.exchange;
    stations::require(&state, me, StationKind::OwlExchange).await?;
    // A closed listing whose seller has been deleted has no seller left.
    let seller: Option<Option<i64>> =
        sqlx::query_scalar("SELECT seller_id FROM exchange_listings WHERE id = $1")
            .bind(id)
            .fetch_optional(&state.pool)
            .await?;
    let seller = match seller.ok_or_else(listing_not_found)? {
        Some(seller) => seller,
        None => {
            let mut conn = state.pool.acquire().await?;
            return match earlier_purchase(&mut conn, id, me).await? {
                Some(done) => Ok(Json(done)),
                None => Err(listing_closed()),
            };
        }
    };
    if seller == me {
        return Err(ApiError::bad_request(
            "own_listing",
            "That is your own listing; take it down instead.",
        ));
    }

    let mut tx = state.pool.begin().await?;
    lock_characters(&mut tx, &[me, seller]).await?;
    let open = lock_listing(&mut tx, id)
        .await?
        .ok_or_else(listing_not_found)?;
    if open.state != STATE_ACTIVE || open.expired {
        // A repeat of this buyer's own purchase answers it again.
        if let Some(done) = earlier_purchase(&mut tx, id, me).await? {
            tx.commit().await?;
            return Ok(Json(done));
        }
        return Err(listing_closed());
    }
    let item_id = open
        .item_id
        .ok_or_else(|| ApiError::internal(format!("active listing {id} has no item")))?;
    let item = items::lock(&mut tx, seller, &[item_id])
        .await?
        .pop()
        .filter(|i| i.location == LOCATION_EXCHANGE)
        .ok_or_else(|| ApiError::internal(format!("listing {id}: item is not listed")))?;
    let tax = rules
        .sales_tax(open.price)
        .ok_or_else(|| ApiError::internal("sales tax overflow"))?;
    let proceeds = rules
        .proceeds(open.price)
        .ok_or_else(|| ApiError::internal("proceeds overflow"))?;

    let crowns = ledger::adjust_crowns(&mut tx, me, -open.price, Reason::ExchangeBuy, Some(id))
        .await
        .map_err(ledger_error)?;
    sqlx::query(
        "UPDATE exchange_listings
         SET state = 'sold', item_id = NULL, buyer_id = $2, tax = $3, closed_at = now()
         WHERE id = $1",
    )
    .bind(id)
    .bind(me)
    .bind(tax)
    .execute(&mut *tx)
    .await?;
    let (def, quantity) = (def_u16(open.item_def), units(open.quantity));
    let bought_subject = mail::subject_about("Bought", def, quantity);
    let mail_id = mail::create(
        &mut tx,
        &NewMail {
            recipient: me,
            sender_id: None,
            sender_name: SENDER_EXCHANGE,
            kind: mail::KIND_EXCHANGE_BOUGHT,
            subject: &bought_subject,
            body: "",
            crowns: 0,
            ref_id: Some(id),
            client_key: None,
            with_items: true,
        },
    )
    .await?;
    mail::attach(&mut tx, mail_id, me, &item, Reason::ExchangeBuy, id)
        .await
        .map_err(item_error)?;
    let sold_subject = mail::subject_about("Sold", def, quantity);
    let body = format!(
        "Sold for {} Crowns. Sales tax: {tax}. Enclosed: {proceeds}.",
        open.price
    );
    mail::create(
        &mut tx,
        &NewMail {
            recipient: seller,
            sender_id: None,
            sender_name: SENDER_EXCHANGE,
            kind: mail::KIND_EXCHANGE_SOLD,
            subject: &sold_subject,
            body: &body,
            crowns: proceeds,
            ref_id: Some(id),
            client_key: None,
            with_items: false,
        },
    )
    .await?;
    tx.commit().await?;

    if let Some(world) = state.world() {
        world.wallet_changed(me, crowns);
    }
    mail::notify(&state, me).await;
    mail::notify(&state, seller).await;
    Ok(Json(Bought { mail_id, crowns }))
}

// ------------------------------------------------------------------ expiry

/// Listings closed per batch of the expiry job.
const EXPIRE_BATCH: i64 = 200;

/// Closes every active listing whose time is up: each in its own
/// transaction, the item mailed back to its seller (`exchange_expired`
/// mail, `Reason::ExchangeReturn`). Returns how many it closed. Called by
/// the server's background loop with the world (for `mail_changed`).
pub async fn expire_listings(
    pool: &PgPool,
    world: Option<&WorldHandle>,
) -> Result<u64, sqlx::Error> {
    let mut closed = 0;
    loop {
        let due: Vec<(i64, i64)> = sqlx::query_as(
            "SELECT id, seller_id FROM exchange_listings
             WHERE state = 'active' AND expires_at <= now()
             ORDER BY expires_at LIMIT $1",
        )
        .bind(EXPIRE_BATCH)
        .fetch_all(pool)
        .await?;
        let mut progressed = false;
        for &(id, seller) in &due {
            match expire_one(pool, id, seller).await {
                Ok(true) => {
                    closed += 1;
                    progressed = true;
                    if let Some(world) = world {
                        world.mail_changed(seller, mail::unread(pool, seller).await?);
                    }
                }
                // Bought, taken down or already expired meanwhile.
                Ok(false) => {}
                Err(e) => {
                    tracing::error!(listing = id, error = %e.message, "expiring a listing failed")
                }
            }
        }
        if !progressed || i64::try_from(due.len()).unwrap_or(0) < EXPIRE_BATCH {
            return Ok(closed);
        }
    }
}

async fn expire_one(pool: &PgPool, id: i64, seller: i64) -> Result<bool, ApiError> {
    let mut tx = pool.begin().await?;
    lock_characters(&mut tx, &[seller]).await?;
    let Some(open) = lock_listing(&mut tx, id).await? else {
        return Ok(false);
    };
    if open.state != STATE_ACTIVE || !open.expired {
        return Ok(false);
    }
    let subject = mail::subject_about("Unsold", def_u16(open.item_def), units(open.quantity));
    close_and_return(
        &mut tx,
        &open,
        STATE_EXPIRED,
        mail::KIND_EXCHANGE_EXPIRED,
        &subject,
    )
    .await?;
    tx.commit().await?;
    Ok(true)
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::{delete, get, post};
    let rules = &shipped().economy.exchange;
    let search_limit = RateLimit {
        burst: rules.search_burst,
        per: std::time::Duration::from_secs(u64::from(rules.search_refill_secs)),
    };
    let searches = read_limited(
        Router::new().route("/exchange/listings", get(browse)),
        search_limit,
    );
    let writes = Router::new()
        .route("/exchange/listings", post(create))
        .route("/exchange/mine", get(mine))
        .route("/exchange/listings/{id}", delete(take_down))
        .route("/exchange/listings/{id}/buy", post(buy));
    write_limited(searches.merge(writes), ECONOMY_RATE_LIMIT)
}
