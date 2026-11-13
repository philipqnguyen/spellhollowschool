//! Crafting: recipes from
//! `data/crafting/recipes.json` (`catalog::shipped().recipes`), made at a
//! station over HTTP.
//!
//! **Crafting is HTTP**, not realtime: a craft is one database transaction
//! (and lists of recipes are pages), while the one thing only the world
//! knows, that the crafter stands at the right station, comes from the
//! station grant (`realtime::stations::require(state, character,
//! recipe.station)`, `403 not_at_station`), which `Interact` with the
//! station opened (`ServerMsg::StationOpened`). A cauldron placed in a dorm
//! brews only for the room's owner.
//!
//! Routes (docs/architecture.md, "Economy and crafting"):
//! - `GET /api/recipes?discipline=&known=&limit=&after=`: recipes with
//!   whether the character knows them and can make them (skill);
//! - `POST /api/recipes/learn { item_id }`: uses up a pattern item and
//!   records `known_recipes` (`Reason::LearnPattern`);
//! - `POST /api/craft { recipe_id, dyes?, item_id?, request_id? }`: one
//!   transaction ([`craft`]): lock the character (and check
//!   `craft_ready_at`), check the recipe is known and the skill level, lock
//!   and use up the inputs and dyes ([`materials`]), make the output with
//!   the maker's mark (`crate::items::give`) or restyle `item_id` in place
//!   (`ItemOp::Restyle`, keeping dyes, finish and maker), add skill XP,
//!   insert `crafts`, set `craft_ready_at = now() + craft_secs`; `201 {
//!   item, skill, ready_at }`. A repeated `request_id` answers the first craft.
//!   A recipe whose output is tagged `night` crafts only while the
//!   game clock (the world's, dev skew included) says night ([`night_only`]);
//!   by day `403 night_only` and nothing is used up.

pub(crate) mod materials;

use axum::extract::State;
use axum::http::{HeaderValue, StatusCode, header::RETRY_AFTER};
use axum::response::{IntoResponse, Response};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{Finish, StationKind};
use serde::{Deserialize, Serialize};
use sim_core::{Discipline, ItemKind, Recipe, RecipeKind};
use sqlx::PgConnection;
use std::time::Duration;

use self::materials::{Needs, allocate, item_error, lock_for, use_up};
use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Page, PageQuery, Player, Writer, write_limited};
use crate::http::{ApiError, ApiJson, ApiQuery};
use crate::inventory::equip::{
    announce, forbidden_for, item_not_found, item_unavailable, lock_appearance, refresh_appearance,
    wear_restricted,
};
use crate::inventory::{ITEM_SELECT, ItemRow, ItemView, LOCATION_EQUIPPED, LOCATION_INVENTORY};
use crate::items::{self, Maker, NewItem};
use crate::ledger::{self, ItemMove, ItemOp, Reason};
use crate::rate_limit::RateLimit;
use crate::realtime::stations::StationAccess;
use crate::skills::{self, SkillView};

/// Crafting writes (craft, learn) per account: 30, then one a second.
pub const CRAFT_RATE_LIMIT: RateLimit = RateLimit {
    burst: 30,
    per: Duration::from_secs(1),
};

/// Longest `request_id` (characters), as the `crafts.client_key` CHECK.
pub const REQUEST_ID_MAX_CHARS: usize = 64;

/// Checks an optional `request_id` (1-64 characters).
pub(crate) fn request_key(id: Option<String>) -> Result<Option<String>, ApiError> {
    match id {
        None => Ok(None),
        Some(k) if (1..=REQUEST_ID_MAX_CHARS).contains(&k.chars().count()) => Ok(Some(k)),
        Some(_) => Err(ApiError::bad_request(
            "invalid_body",
            "request_id must be 1 to 64 characters.",
        )),
    }
}

fn recipe_not_found() -> ApiError {
    ApiError::not_found("recipe_not_found", "There is no such recipe.")
}

fn invalid_dye(message: &'static str) -> ApiError {
    ApiError::bad_request("invalid_dye", message)
}

// ------------------------------------------------------------ recipes

/// A recipe in the API: the recipes.json entry plus whether the character
/// knows it and can make it now (known and skilled enough).
#[derive(Debug, Serialize)]
pub struct RecipeView {
    #[serde(flatten)]
    pub recipe: &'static Recipe,
    pub known: bool,
    pub can_make: bool,
}

#[derive(Debug, Default, Deserialize)]
pub struct RecipeFilter {
    pub discipline: Option<Discipline>,
    pub known: Option<bool>,
}

/// The recipes a character knows: everyone's plus those learned.
async fn learned(conn: &mut PgConnection, character_id: i64) -> Result<Vec<u16>, sqlx::Error> {
    let ids: Vec<i32> =
        sqlx::query_scalar("SELECT recipe_id FROM known_recipes WHERE character_id = $1")
            .bind(character_id)
            .fetch_all(conn)
            .await?;
    Ok(ids
        .into_iter()
        .filter_map(|i| u16::try_from(i).ok())
        .collect())
}

fn knows(recipe: &Recipe, learned: &[u16]) -> bool {
    recipe.pattern.is_none() || learned.contains(&recipe.id)
}

/// `GET /api/recipes?discipline=&known=&limit=&after=`, by recipe id.
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(filter): ApiQuery<RecipeFilter>,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<RecipeView>>, ApiError> {
    let after: Option<u16> = page.after()?;
    let limit = page.limit()?;
    let mut conn = state.pool.acquire().await?;
    let id = player.character.id;
    let learned = learned(&mut conn, id).await?;
    let mut levels = [0u8; 3];
    for (slot, d) in levels.iter_mut().zip(Discipline::ALL) {
        *slot = shipped()
            .recipes
            .skill_level(skills::xp_of(&mut conn, id, d).await?);
    }
    let level_of =
        |d: Discipline| levels[Discipline::ALL.iter().position(|&x| x == d).unwrap_or(0)];
    let mut recipes: Vec<&'static Recipe> = shipped().recipes.recipes.iter().collect();
    recipes.sort_by_key(|r| r.id);
    let rows: Vec<RecipeView> = recipes
        .into_iter()
        .filter(|r| after.is_none_or(|a| r.id > a))
        .filter(|r| filter.discipline.is_none_or(|d| r.discipline == d))
        .map(|r| {
            let known = knows(r, &learned);
            RecipeView {
                recipe: r,
                known,
                can_make: known && level_of(r.discipline) >= r.skill,
            }
        })
        .filter(|v| filter.known.is_none_or(|k| v.known == k))
        .take(limit as usize + 1)
        .collect();
    Ok(Json(Page::from_rows(rows, limit, |v| v.recipe.id)))
}

#[derive(Debug, Deserialize)]
pub struct LearnRequest {
    pub item_id: i64,
}

#[derive(Debug, Serialize)]
pub struct Learned {
    pub recipe_id: u16,
}

/// `POST /api/recipes/learn { item_id }`: one pattern from the inventory is
/// used up and its recipe is known from now on.
pub async fn learn(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(req): ApiJson<LearnRequest>,
) -> Result<Json<Learned>, ApiError> {
    let id = player.character.id;
    let mut tx = state.pool.begin().await?;
    lock_character(&mut tx, id).await?;
    let rows = items::lock(&mut tx, id, &[req.item_id]).await?;
    let Some(item) = rows.into_iter().next() else {
        return Err(item_not_found());
    };
    let recipe = shipped()
        .catalog
        .get(item.item_def)
        .and_then(|d| d.pattern)
        .map(|p| p.recipe)
        .ok_or_else(|| ApiError::bad_request("not_a_pattern", "That is not a pattern."))?;
    if item.location != LOCATION_INVENTORY {
        return Err(item_unavailable());
    }
    let inserted = sqlx::query(
        "INSERT INTO known_recipes (character_id, recipe_id) VALUES ($1, $2)
         ON CONFLICT DO NOTHING",
    )
    .bind(id)
    .bind(i32::from(recipe))
    .execute(&mut *tx)
    .await?
    .rows_affected();
    if inserted == 0 {
        return Err(ApiError::conflict(
            "already_known",
            "You already know that recipe.",
        ));
    }
    items::consume(
        &mut tx,
        &item,
        1,
        Reason::LearnPattern,
        Some(i64::from(recipe)),
    )
    .await
    .map_err(item_error)?;
    tx.commit().await?;
    Ok(Json(Learned { recipe_id: recipe }))
}

/// Locks the character row (`FOR NO KEY UPDATE`): the first lock of every
/// transaction that changes the character's items.
pub(crate) async fn lock_character(
    conn: &mut PgConnection,
    character_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT 1 FROM characters WHERE id = $1 FOR NO KEY UPDATE")
        .bind(character_id)
        .execute(conn)
        .await?;
    Ok(())
}

// ------------------------------------------------------------ crafting

#[derive(Debug, Deserialize)]
pub struct CraftRequest {
    /// Wider than `u16` so an out-of-range id is `recipe_not_found`.
    pub recipe_id: i64,
    /// Dyeable recipes: `[primary, secondary, trim]`; tinted potions: the
    /// first entry is the primary dye (the rest is ignored).
    #[serde(default)]
    pub dyes: Option<Vec<i64>>,
    /// Restyles: the garment to change.
    #[serde(default)]
    pub item_id: Option<i64>,
    #[serde(default)]
    pub request_id: Option<String>,
}

/// `201 { item, skill: { discipline, xp, level, next_level_xp }, ready_at }`.
/// `item` is the stack that received the output (the first, if it filled
/// several), or the restyled garment; `null` only when a replayed craft's
/// output has since gone (used, merged, sold).
#[derive(Debug, Serialize)]
pub struct CraftView {
    pub item: Option<ItemView>,
    pub skill: SkillView,
    pub ready_at: DateTime<Utc>,
}

/// Why a craft was refused: an API error, or busy for some seconds
/// (`409 crafting_busy` with `Retry-After`).
#[derive(Debug)]
pub enum CraftError {
    Api(ApiError),
    Busy { secs: u64 },
}

impl From<ApiError> for CraftError {
    fn from(e: ApiError) -> Self {
        CraftError::Api(e)
    }
}

impl From<sqlx::Error> for CraftError {
    fn from(e: sqlx::Error) -> Self {
        CraftError::Api(e.into())
    }
}

impl IntoResponse for CraftError {
    fn into_response(self) -> Response {
        match self {
            CraftError::Api(e) => e.into_response(),
            CraftError::Busy { secs } => {
                let mut res = ApiError::conflict(
                    "crafting_busy",
                    "You are still finishing your last piece of work.",
                )
                .into_response();
                res.headers_mut()
                    .insert(RETRY_AFTER, HeaderValue::from(secs.max(1)));
                res
            }
        }
    }
}

/// Palette indices from the request (`400 invalid_dye` outside the palette).
fn palette_indices(raw: &[i64]) -> Result<Vec<u8>, ApiError> {
    let colours = shipped().palette.colours.len();
    raw.iter()
        .map(|&d| {
            u8::try_from(d)
                .ok()
                .filter(|&d| usize::from(d) < colours)
                .ok_or_else(|| invalid_dye("Dyes are palette indices inside the palette."))
        })
        .collect()
}

/// Dyes used up by a choice of colours: `(palette family, units)`.
type DyeCost = Vec<(u8, u32)>;

/// The output's dyes and what choosing them costs, by the dye rule.
fn output_dyes(
    recipe: &Recipe,
    defaults: [u8; 3],
    raw: Option<&[i64]>,
) -> Result<([u8; 3], DyeCost), ApiError> {
    let palette = &shipped().palette;
    let wanted = match (raw, recipe.dyeable, recipe.tint) {
        (None, _, _) => defaults,
        (Some(raw), true, _) => {
            let d = palette_indices(raw)?;
            let [p, s, t] = d[..] else {
                return Err(invalid_dye("Pick three dyes: primary, secondary and trim."));
            };
            [p, s, t]
        }
        (Some(raw), false, true) => {
            let d = palette_indices(raw)?;
            let Some(&p) = d.first() else {
                return Err(invalid_dye("Pick the potion's colour."));
            };
            [p, defaults[1], defaults[2]]
        }
        (Some(_), false, false) => {
            return Err(invalid_dye("This recipe's colours cannot be chosen."));
        }
    };
    let cost = palette
        .dyes_needed(defaults, defaults, wanted)
        .map_err(|_| invalid_dye("Dyes are palette indices inside the palette."))?;
    Ok((wanted, cost))
}

/// The output tag of a brew that can only be made at night.
pub const NIGHT_TAG: &str = "night";

/// `403 night_only` when `output` is a night brew and the game clock at
/// `now` (Unix ms) is not in the night period.
pub fn night_only(output: &sim_core::ItemDef, now: i64) -> Result<(), ApiError> {
    if output.has_tag(NIGHT_TAG)
        && shipped().schedule.clock.period(now) != sim_core::clock::Period::Night
    {
        return Err(ApiError::forbidden(
            "night_only",
            "That brew can only be made at night.",
        ));
    }
    Ok(())
}

/// The game clock's now (Unix ms): the world's, with any development skew.
fn game_now(state: &AppState) -> i64 {
    match state.world() {
        Some(w) => w.clock().now_unix_ms(),
        None => crate::clock::ServerClock::new().now_unix_ms(),
    }
}

/// `POST /api/craft`.
pub async fn craft_route(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(req): ApiJson<CraftRequest>,
) -> Response {
    match craft(&state, &player, req).await {
        Ok(view) => (StatusCode::CREATED, Json(view)).into_response(),
        Err(e) => e.into_response(),
    }
}

/// The craft's checks and its transaction (see the module docs).
pub async fn craft(
    state: &AppState,
    player: &Player,
    req: CraftRequest,
) -> Result<CraftView, CraftError> {
    let content = shipped();
    let recipe = u16::try_from(req.recipe_id)
        .ok()
        .and_then(|id| content.recipes.get(id))
        .ok_or_else(recipe_not_found)?;
    let key = request_key(req.request_id)?;
    let id = player.character.id;
    // A retry answers the first craft, even away from the station.
    if let Some(key) = &key
        && let Some(view) = replay(&mut *state.pool.acquire().await?, id, key).await?
    {
        return Ok(view);
    }
    let output = content
        .catalog
        .get(recipe.output.item)
        .ok_or_else(|| ApiError::internal("recipe output missing from the catalog"))?;
    let defaults = output.granted_dyes(player.character.house, &content.palette);
    let (dyes, dye_cost) = match recipe.kind {
        RecipeKind::Craft => output_dyes(recipe, defaults, req.dyes.as_deref())?,
        RecipeKind::Restyle if req.dyes.is_some() => {
            return Err(invalid_dye("A restyle keeps the garment's dyes.").into());
        }
        RecipeKind::Restyle => (defaults, Vec::new()),
    };
    let target = match (recipe.kind, req.item_id) {
        (RecipeKind::Restyle, Some(item)) => Some(item),
        (RecipeKind::Restyle, None) => {
            return Err(
                ApiError::bad_request("wrong_item", "Choose the garment to restyle.").into(),
            );
        }
        (RecipeKind::Craft, _) => None,
    };
    let access = crate::realtime::stations::require(state, id, recipe.station).await?;
    station_serves(&access, id)?;
    night_only(output, game_now(state))?;

    let mut tx = state.pool.begin().await?;
    let before = lock_appearance(&mut tx, id).await?;
    // Checked again under the lock: a concurrent retry may have just committed.
    if let Some(key) = &key
        && let Some(view) = replay(&mut tx, id, key).await?
    {
        return Ok(view);
    }
    let busy: Option<f64> = sqlx::query_scalar(
        "SELECT EXTRACT(EPOCH FROM (craft_ready_at - now()))::float8 FROM characters
         WHERE id = $1 AND craft_ready_at > now()",
    )
    .bind(id)
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(secs) = busy {
        return Err(CraftError::Busy {
            secs: secs.ceil() as u64,
        });
    }
    if !knows(recipe, &learned(&mut tx, id).await?) {
        return Err(
            ApiError::forbidden("recipe_unknown", "You have not learned that recipe.").into(),
        );
    }
    let xp_before = skills::xp_of(&mut tx, id, recipe.discipline).await?;
    if content.recipes.skill_level(xp_before) < recipe.skill {
        return Err(ApiError::forbidden(
            "skill_too_low",
            format!(
                "That needs {} level {}.",
                recipe.discipline.id(),
                recipe.skill
            ),
        )
        .into());
    }

    let craft_id: i64 = sqlx::query_scalar(
        "INSERT INTO crafts (character_id, recipe_id, quantity, xp, client_key)
         VALUES ($1, $2, $3, $4, $5) RETURNING id",
    )
    .bind(id)
    .bind(i32::from(recipe.id))
    .bind(i32::try_from(recipe.output.quantity).unwrap_or(1))
    .bind(i32::try_from(recipe.xp).unwrap_or(i32::MAX))
    .bind(key.as_deref())
    .fetch_one(&mut *tx)
    .await?;
    let needs = Needs {
        items: recipe.inputs.iter().map(|i| (i.item, i.quantity)).collect(),
        dyes: dye_cost,
    };
    let (worked, candidates) = lock_for(&mut tx, id, &needs, target.as_slice()).await?;
    let worked = match target {
        Some(_) => {
            let item = worked.into_iter().next().ok_or_else(item_not_found)?;
            check_restyle(recipe, &item)?;
            // A worn garment may not become one its body
            // may not wear (unworn restyles are free).
            if item.location == LOCATION_EQUIPPED
                && let Some(w) = forbidden_for(before.wear(), output)
            {
                return Err(wear_restricted(w).into());
            }
            Some(item)
        }
        None => None,
    };
    let allocation = allocate(&needs, &candidates, target).map_err(|short| short.to_api())?;
    use_up(&mut tx, &allocation, Reason::Craft, Some(craft_id))
        .await
        .map_err(item_error)?;

    let output_id = match worked {
        Some(item) => {
            restyle(&mut tx, &item, recipe.output.item, craft_id).await?;
            item.id
        }
        None => {
            let made = NewItem {
                owner: id,
                item_def: recipe.output.item,
                dyes,
                finish: Finish::None,
                quantity: recipe.output.quantity,
                maker: Some(Maker {
                    character_id: id,
                    name: player.character.name.clone(),
                }),
                location: LOCATION_INVENTORY,
            };
            let rows = items::give(&mut tx, &made, Reason::Craft, Some(craft_id))
                .await
                .map_err(item_error)?;
            *rows
                .first()
                .ok_or_else(|| ApiError::internal("a craft made no item"))?
        }
    };
    let xp = skills::add_xp(&mut tx, id, recipe.discipline, recipe.xp).await?;
    // Player XP too (a share of the skill XP, daily cap; once
    // per craft).
    let player_xp = shipped().progression.craft_xp(recipe.xp);
    let grant = crate::progression::grant_xp(
        &mut tx,
        id,
        protocol::XpSource::Craft,
        Some(craft_id),
        player_xp,
        Utc::now(),
    )
    .await?;
    // ---- Fortune: Quick Hands cuts the busy time
    // by `effects.fortune.quick_hands_pct` while the crafter has it.
    let mut craft_secs = f64::from(recipe.craft_secs);
    if crate::effects::active_fortune(&mut tx, id).await? == Some(protocol::FortuneKind::QuickHands)
    {
        let cut = shipped().economy.effects.fortune.quick_hands_pct.min(100);
        craft_secs *= f64::from(100 - cut) / 100.0;
    }
    // ---- end fortune.
    // ---- House cup: a craft of a recipe of skill
    // `sources.craft.min_skill` or more gives `CupRules::craft_points(
    // recipe.skill)` house points under the daily cap group `hp_craft`
    // (`progression::caps::take`), source `craft`, ref_id `craft_id`, in this
    // transaction.
    let cup_points = shipped().house_cup.craft_points(recipe.skill);
    if cup_points > 0 {
        crate::house_points::award_capped(
            &mut tx,
            player.character.house,
            u32::from(cup_points),
            id,
            crate::house_points::PointsSource::Craft,
            Some(craft_id),
            Utc::now(),
        )
        .await?;
    }
    // ---- end house cup.
    let ready_at: DateTime<Utc> = sqlx::query_scalar(
        "UPDATE characters SET craft_ready_at = now() + make_interval(secs => $2)
         WHERE id = $1 RETURNING craft_ready_at",
    )
    .bind(id)
    .bind(craft_secs)
    .fetch_one(&mut *tx)
    .await?;
    sqlx::query("UPDATE crafts SET output_item_id = $2 WHERE id = $1")
        .bind(craft_id)
        .bind(output_id)
        .execute(&mut *tx)
        .await?;
    let item = item_view(&mut tx, id, output_id).await?;
    // A restyled garment that is worn changes the look.
    let look = refresh_appearance(&mut tx, id, before).await?;
    tx.commit().await?;
    announce(state, id, &look);
    crate::progression::notify(state.world(), id, &grant);
    Ok(CraftView {
        item,
        skill: SkillView::new(recipe.discipline, xp),
        ready_at,
    })
}

/// Whether a granted station serves `crafter`: the common-room corners and
/// tables and the classroom cauldrons serve anyone the world let use them;
/// a cauldron placed in a dorm brews only for the room's owner (a guest's
/// grant opens it to look at, not to brew).
fn station_serves(access: &StationAccess, crafter: i64) -> Result<(), ApiError> {
    if access.kind == StationKind::Cauldron && access.dorm_owner.is_some_and(|o| o != crafter) {
        return Err(ApiError::forbidden(
            "not_at_station",
            "A cauldron in a friend's room brews only for them. Use your own, or the Potions classroom.",
        ));
    }
    Ok(())
}

/// A restyle's garment: clothing of the recipe's family, not already the
/// target, in the inventory or worn.
fn check_restyle(recipe: &Recipe, item: &items::LockedItem) -> Result<(), ApiError> {
    let def = shipped().catalog.get(item.item_def);
    let fits = def.is_some_and(|d| {
        d.kind == ItemKind::Clothing
            && d.family.is_some()
            && d.family == recipe.family
            && d.id != recipe.output.item
    });
    if !fits {
        return Err(ApiError::bad_request(
            "wrong_item",
            "That garment cannot be restyled into this.",
        ));
    }
    if item.location != LOCATION_INVENTORY && item.location != LOCATION_EQUIPPED {
        return Err(item_unavailable());
    }
    Ok(())
}

/// Changes a garment into `into` in place (same row, dyes, finish and
/// maker's mark), recorded as `ItemOp::Restyle` from its old definition.
async fn restyle(
    conn: &mut PgConnection,
    item: &items::LockedItem,
    into: u16,
    craft_id: i64,
) -> Result<(), ApiError> {
    sqlx::query("UPDATE items SET item_def = $2 WHERE id = $1")
        .bind(item.id)
        .bind(i32::from(into))
        .execute(&mut *conn)
        .await?;
    let mut m = ItemMove::new(item.id, into, ItemOp::Restyle, item.quantity, Reason::Craft)
        .from(item.owner_id, &item.location)
        .to(item.owner_id, &item.location)
        .ref_id(craft_id);
    m.prev_item_def = Some(item.item_def);
    ledger::record_item_move(conn, &m)
        .await
        .map_err(|e| ApiError::internal_error(&e))?;
    Ok(())
}

/// The owner's item `item_id` as the API shows it.
async fn item_view(
    conn: &mut PgConnection,
    owner: i64,
    item_id: i64,
) -> Result<Option<ItemView>, sqlx::Error> {
    let row: Option<ItemRow> = sqlx::query_as(&format!(
        "{ITEM_SELECT} WHERE i.id = $1 AND i.owner_id = $2"
    ))
    .bind(item_id)
    .bind(owner)
    .fetch_optional(conn)
    .await?;
    Ok(row.map(ItemView::from))
}

/// The answer to an earlier craft with the same `request_id`, if any.
async fn replay(
    conn: &mut PgConnection,
    character_id: i64,
    key: &str,
) -> Result<Option<CraftView>, sqlx::Error> {
    let row: Option<(i32, Option<i64>, DateTime<Utc>)> = sqlx::query_as(
        "SELECT recipe_id, output_item_id, created_at FROM crafts
         WHERE character_id = $1 AND client_key = $2",
    )
    .bind(character_id)
    .bind(key)
    .fetch_optional(&mut *conn)
    .await?;
    let Some((recipe_id, output, created_at)) = row else {
        return Ok(None);
    };
    let Some(recipe) = u16::try_from(recipe_id)
        .ok()
        .and_then(|r| shipped().recipes.get(r))
    else {
        return Ok(None);
    };
    let item = match output {
        Some(item_id) => item_view(&mut *conn, character_id, item_id).await?,
        None => None,
    };
    let xp = skills::xp_of(conn, character_id, recipe.discipline).await?;
    Ok(Some(CraftView {
        item,
        skill: SkillView::new(recipe.discipline, xp),
        ready_at: created_at + chrono::Duration::seconds(i64::from(recipe.craft_secs)),
    }))
}

/// This module's routes (crafting write limit).
pub fn routes() -> Router<AppState> {
    use axum::routing::{get, post};
    let router = Router::new()
        .route("/recipes", get(list))
        .route("/recipes/learn", post(learn))
        .route("/craft", post(craft_route));
    write_limited(router, CRAFT_RATE_LIMIT)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn access(kind: StationKind, dorm_owner: Option<i64>) -> StationAccess {
        StationAccess {
            kind,
            target: "x".into(),
            dorm_owner,
        }
    }

    #[test]
    fn a_dorm_cauldron_brews_for_its_owner_only() {
        assert!(station_serves(&access(StationKind::Cauldron, None), 7).is_ok());
        assert!(station_serves(&access(StationKind::Cauldron, Some(7)), 7).is_ok());
        let guest = station_serves(&access(StationKind::Cauldron, Some(8)), 7).unwrap_err();
        assert_eq!(guest.code, "not_at_station");
        assert!(station_serves(&access(StationKind::CraftCharms, None), 7).is_ok());
    }

    #[test]
    fn chosen_dyes_follow_the_recipe() {
        let recipes = &shipped().recipes;
        let beanie = recipes.get(6).unwrap();
        let defaults = [28, 47, 47];
        assert_eq!(
            output_dyes(beanie, defaults, None).unwrap(),
            (defaults, vec![])
        );
        let (dyes, cost) = output_dyes(beanie, defaults, Some(&[34, 47, 0])).unwrap();
        assert_eq!(dyes, [34, 47, 0]);
        // Cobalt costs a blue; the default and a basic colour are free.
        let blues = shipped().palette.family_of(34).unwrap();
        assert_eq!(cost, vec![(blues, 1)]);
        assert!(output_dyes(beanie, defaults, Some(&[1, 2])).is_err());
        let glow = recipes.get(321).unwrap();
        let (dyes, _) = output_dyes(glow, [22, 25, 9], Some(&[34, 0, 0])).unwrap();
        assert_eq!(dyes, [34, 25, 9], "tint: the primary only");
        let shrinking = recipes.get(324).unwrap();
        assert!(output_dyes(shrinking, [0; 3], Some(&[1])).is_err());
        assert!(request_key(Some(String::new())).is_err());
        assert!(request_key(Some("x".repeat(65))).is_err());
        assert_eq!(request_key(Some("k".into())).unwrap(), Some("k".into()));
    }
}
