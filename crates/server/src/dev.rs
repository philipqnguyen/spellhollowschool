//! Development-only endpoints, mounted only with `SHS_DEV=1`.
//!
//! `POST /api/dev/bot-session { name }` hands load-test bots (tools/loadtest) a
//! session without going through email verification. Bot accounts are marked
//! `is_bot`, have no usable password, and are reused when the same bot name
//! asks again, so repeated load-test runs do not pile up accounts. An optional
//! `body` (an `Appearance` whose outfit is ignored) gives a bot its body type,
//! sliders, skin and hair, so load tests carry realistic appearances; it
//! applies until the bot's starter grant builds its look on that body.
//! An optional `start: [x, y, z]` is a new bot's castle
//! position (ignored for an existing bot; the world falls back to the
//! layout's spawns if it is not ground). Without it a bot starts on the front
//! steps like a new player: the class tests rely on bots starting at their
//! world's spawns, so the server never moves bots unless asked.
//!
//! `POST /api/dev/grant { items?: [{ item_def, quantity }], crowns?,
//! skills?: { "<subject>": xp } }` hands the signed-in character test
//! goods: playtests reach high-skill recipes and rare potions without hours
//! of brewing, and load-test bots get a potion to drink. It is one
//! transaction like every other grant, recorded in both ledgers with reason
//! `admin`, so the audit still reconciles.
//!
//! The grant also takes `xp` (player XP, source `admin`) and
//! `spells` (ids learned at once), so playtests reach high levels and
//! dueling spells quickly; `POST /api/dev/clock { skew_ms } | { at_unix_ms }`
//! shifts the game clock (`crate::clock::ServerClock`) to reach the next
//! class session without waiting.
//!
//! `POST /api/dev/teleport { pos: [x, y, z] }` moves the signed-in, online
//! character to walkable ground in their zone (the client follows the
//! server's `Correction`): browser playtests reach the far corners of the
//! castle without minutes of walking. `409 teleport_failed` when the
//! character is offline or the spot is not ground they may stand on.
//!
//! `POST /api/dev/house-cup/close[?at_unix_ms=]` closes the
//! running House Cup week now (`crate::house_cup::close_week`): the
//! winners' row and reward mails; the badge and banners switch at once.

use axum::Router;
use axum::extract::State;
use axum::routing::post;
use protocol::{Appearance, House, Outfit};
use rand::RngCore;
use rand::rngs::OsRng;
use serde::{Deserialize, Serialize};
use sim_core::Subject;
use std::collections::BTreeMap;

use crate::AppState;
use crate::auth::{create_session, password};
use crate::catalog::shipped;
use crate::characters::{self, NameError, Player, SavedZone, name_key};
use crate::http::{ApiError, ApiJson, ApiQuery};
use crate::items::{self, NewItem};
use crate::ledger::{self, Reason};
use crate::{inventory, progression, skills, wallet};

pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/dev/bot-session", post(bot_session))
        .route("/dev/grant", post(grant))
        .route("/dev/teleport", post(teleport))
        .route("/dev/clock", post(clock))
        .route("/dev/house-cup/close", post(house_cup_close))
}

#[derive(Debug, Deserialize)]
pub struct BotSessionRequest {
    pub name: String,
    /// Body type, sliders, skin tone and hair (`outfit` is ignored: bots
    /// dress through the wardrobe API).
    #[serde(default)]
    pub body: Option<Appearance>,
    /// A new bot's castle position (metres), e.g. one of the
    /// load test's `nav::BOT_STARTS` in the Entrance Hall.
    #[serde(default)]
    pub start: Option<[f32; 3]>,
}

#[derive(Debug, Serialize)]
pub struct BotSessionResponse {
    pub token: String,
}

const HOUSES: [House; 4] = [
    House::Roarwing,
    House::Burrowsby,
    House::Skytalon,
    House::Serpentyne,
];

/// Bot names are 3-24 ASCII letters, digits, `_` or `-`, starting with a letter
/// (e.g. `bot0042`). Player names cannot contain digits, so numbered bots never
/// collide with real players.
pub fn validate_bot_name(raw: &str) -> Result<String, NameError> {
    let name = raw.trim();
    if !(characters::NAME_MIN_CHARS..=characters::NAME_MAX_CHARS).contains(&name.len()) {
        return Err(NameError::Length);
    }
    let ok = name.starts_with(|c: char| c.is_ascii_alphabetic())
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-');
    if ok {
        Ok(name.to_owned())
    } else {
        Err(NameError::Characters)
    }
}

/// Spreads bots over the houses deterministically by name.
fn house_for(name: &str) -> House {
    let sum: u32 = name.bytes().map(u32::from).sum();
    HOUSES[(sum % 4) as usize]
}

async fn bot_session(
    State(state): State<AppState>,
    ApiJson(req): ApiJson<BotSessionRequest>,
) -> Result<ApiJson<BotSessionResponse>, ApiError> {
    let name = validate_bot_name(&req.name)?;
    let body = match req.body {
        Some(a) => {
            let a = Appearance {
                outfit: Outfit::default(),
                ..a
            };
            let c = shipped();
            c.creator
                .check_appearance(&a, &c.catalog, &c.palette)
                .map_err(|e| {
                    ApiError::bad_request("invalid_appearance", format!("Not a valid body: {e}."))
                })?;
            Some(a)
        }
        None => None,
    };
    if req.start.is_some_and(|p| p.iter().any(|v| !v.is_finite())) {
        return Err(ApiError::bad_request(
            "invalid_start",
            "A start is three finite numbers.",
        ));
    }
    let mut tx = state.pool.begin().await?;

    // Reuse the bot that already has this name; never hand out a real player's character.
    let existing: Option<(i64, bool, i64)> = sqlx::query_as(
        "SELECT a.id, a.is_bot, c.id FROM characters c JOIN accounts a ON a.id = c.account_id
         WHERE c.name_key = $1",
    )
    .bind(name_key(&name))
    .fetch_optional(&mut *tx)
    .await?;

    let (account_id, character_id) = match existing {
        Some((id, true, character)) => (id, character),
        Some((_, false, _)) => {
            return Err(ApiError::conflict(
                "name_taken",
                "That name belongs to a player.",
            ));
        }
        None => {
            let mut tag = [0u8; 8];
            OsRng.fill_bytes(&mut tag);
            let email = format!("bot-{}@bots.invalid", u64::from_le_bytes(tag));
            let id: i64 = sqlx::query_scalar(
                "INSERT INTO accounts (email, password_hash, adult_attested_at, email_verified_at, is_bot)
                 VALUES ($1, $2, now(), now(), true) RETURNING id",
            )
            .bind(email)
            .bind(password::NO_PASSWORD)
            .fetch_one(&mut *tx)
            .await?;
            // Bots start in the castle: the load tests measure the seamless world.
            let bot = characters::insert(&mut *tx, id, &name, house_for(&name), SavedZone::Castle)
                .await
                .map_err(characters::map_insert_error)?;
            if let Some([x, y, z]) = req.start {
                sqlx::query(
                    "UPDATE characters SET pos_x = $2, pos_y = $3, pos_z = $4 WHERE id = $1",
                )
                .bind(bot.id)
                .bind(x)
                .bind(y)
                .bind(z)
                .execute(&mut *tx)
                .await?;
            }
            (id, bot.id)
        }
    };

    if let Some(body) = body {
        // Only before the starter grant, which builds the look on this body.
        sqlx::query(
            "UPDATE characters SET appearance = $2
             WHERE id = $1 AND starter_granted_at IS NULL",
        )
        .bind(character_id)
        .bind(sqlx::types::Json(body))
        .execute(&mut *tx)
        .await?;
    }
    let (token, _) = create_session(&mut *tx, account_id, state.config.session_ttl).await?;
    characters::roster::select_for_session(
        &mut *tx,
        &crate::auth::token::hash(&token),
        character_id,
    )
    .await?;
    tx.commit().await?;
    Ok(ApiJson(BotSessionResponse { token }))
}

/// Most units of one item definition a grant line may hand out (as the
/// owl-order catalogue's line limit, but generous for playtests).
const GRANT_UNITS_MAX: u32 = 999;
/// Most Crowns one grant may hand out.
const GRANT_CROWNS_MAX: i64 = 1_000_000;
/// Most lines one grant may carry.
const GRANT_LINES_MAX: usize = 50;

#[derive(Debug, Deserialize)]
pub struct GrantLine {
    pub item_def: u16,
    pub quantity: u32,
}

#[derive(Debug, Default, Deserialize)]
pub struct GrantRequest {
    #[serde(default)]
    pub items: Vec<GrantLine>,
    #[serde(default)]
    pub crowns: i64,
    /// XP to add per subject id (any of the nine subjects, not
    /// only the three crafting disciplines).
    #[serde(default)]
    pub skills: BTreeMap<String, u32>,
    /// Player XP (`xp_ledger` source `admin`, no cap).
    #[serde(default)]
    pub xp: u32,
    /// Spells to learn at once (`data/spells.json` ids).
    #[serde(default)]
    pub spells: Vec<u16>,
}

#[derive(Debug, Serialize)]
pub struct GrantResponse {
    /// Every inventory row that received units, in line order.
    pub items: Vec<i64>,
    pub crowns: i64,
    /// The character's total XP and level after the grant.
    pub xp: u32,
    pub level: u8,
}

/// `POST /api/dev/grant`: test goods for the signed-in character (dev only).
async fn grant(
    State(state): State<AppState>,
    player: Player,
    ApiJson(req): ApiJson<GrantRequest>,
) -> Result<ApiJson<GrantResponse>, ApiError> {
    let me = player.character.id;
    let catalog = &shipped().catalog;
    let invalid = |m: &'static str| ApiError::bad_request("invalid_grant", m);
    if req.items.len() > GRANT_LINES_MAX {
        return Err(invalid("Too many lines."));
    }
    for line in &req.items {
        let def = catalog
            .get(line.item_def)
            .ok_or_else(|| invalid("Unknown item."))?;
        if line.quantity == 0 || line.quantity > GRANT_UNITS_MAX || def.retired {
            return Err(invalid("Bad quantity or retired item."));
        }
    }
    if !(0..=GRANT_CROWNS_MAX).contains(&req.crowns) {
        return Err(invalid("Crowns out of range."));
    }
    let mut subjects = Vec::new();
    for (id, xp) in &req.skills {
        let s = Subject::from_id(id).ok_or_else(|| invalid("Unknown subject."))?;
        subjects.push((s, *xp));
    }
    if req.xp > GRANT_XP_MAX || req.spells.len() > 32 {
        return Err(invalid("Too much XP or too many spells."));
    }
    if req
        .spells
        .iter()
        .any(|&s| shipped().spells.get(s).is_none())
    {
        return Err(invalid("Unknown spell."));
    }

    let mut tx = state.pool.begin().await?;
    wallet::lock_characters(&mut tx, &[me]).await?;
    let mut rows = Vec::new();
    for line in &req.items {
        let new = NewItem::plain(
            me,
            line.item_def,
            line.quantity,
            inventory::LOCATION_INVENTORY,
        );
        rows.extend(
            items::give(&mut tx, &new, Reason::Admin, None)
                .await
                .map_err(wallet::item_error)?,
        );
    }
    let crowns = ledger::adjust_crowns(&mut tx, me, req.crowns, Reason::Admin, None)
        .await
        .map_err(wallet::ledger_error)?;
    let mut flying_xp = None;
    for (s, xp) in subjects {
        let total = skills::add_xp(&mut tx, me, s, xp).await?;
        if s == Subject::Flying {
            flying_xp = Some(total);
        }
    }
    let grant = progression::grant_xp(
        &mut tx,
        me,
        protocol::XpSource::Admin,
        None,
        req.xp,
        chrono::Utc::now(),
    )
    .await?;
    let learned = progression::learn_spells(&mut tx, me, &req.spells, "admin").await?;
    let (all, loadout) = progression::spellbook(&mut tx, me, grant.level).await?;
    tx.commit().await?;
    if let Some(world) = state.world() {
        if req.crowns > 0 {
            world.wallet_changed(me, crowns);
        }
        if req.xp > 0 {
            progression::notify(Some(world), me, &grant);
        }
        if !learned.is_empty() {
            world.spellbook_changed(me, all, loadout, learned);
        }
        // A Flying grant lets the character mount at once.
        if let Some(xp) = flying_xp {
            world.flying_changed(me, xp);
        }
    }
    Ok(ApiJson(GrantResponse {
        items: rows,
        crowns,
        xp: grant.xp,
        level: grant.level,
    }))
}

/// Most player XP one dev grant adds (more than the whole curve).
const GRANT_XP_MAX: u32 = 100_000;

#[derive(Debug, Deserialize)]
pub struct ClockRequest {
    /// Shift the game clock by this much (ms; 0 resets)...
    #[serde(default)]
    pub skew_ms: Option<i64>,
    /// ...or so that it reads this Unix ms now.
    #[serde(default)]
    pub at_unix_ms: Option<i64>,
}

#[derive(Debug, Serialize)]
pub struct ClockResponse {
    pub unix_ms: i64,
    pub skew_ms: i64,
}

/// `POST /api/dev/clock { skew_ms? | at_unix_ms? }`: shifts the game clock
/// (dev only) so playtests reach the next class without waiting. Everyone
/// shares it; clients re-sync through `Pong`. `409 no_world` without the
/// realtime side. At most a year either way.
async fn clock(
    State(state): State<AppState>,
    _player: Player,
    ApiJson(req): ApiJson<ClockRequest>,
) -> Result<ApiJson<ClockResponse>, ApiError> {
    const YEAR_MS: i64 = 366 * 86_400_000;
    let Some(rt) = state.realtime.as_deref() else {
        return Err(ApiError::conflict("no_world", "The world is not running."));
    };
    let clock = &rt.clock;
    let skew = match (req.skew_ms, req.at_unix_ms) {
        (Some(s), None) => s,
        (None, Some(at)) => at - (clock.now_unix_ms() - clock.skew_ms()),
        _ => {
            return Err(ApiError::bad_request(
                "invalid_clock",
                "Give skew_ms or at_unix_ms.",
            ));
        }
    };
    if skew.abs() > YEAR_MS {
        return Err(ApiError::bad_request(
            "invalid_clock",
            "At most a year either way.",
        ));
    }
    clock.set_skew_ms(skew);
    Ok(ApiJson(ClockResponse {
        unix_ms: clock.now_unix_ms(),
        skew_ms: skew,
    }))
}

#[derive(Debug, Deserialize)]
pub struct TeleportRequest {
    pub pos: [f32; 3],
}

/// `POST /api/dev/teleport`: moves the signed-in character (dev only).
async fn teleport(
    State(state): State<AppState>,
    player: Player,
    ApiJson(req): ApiJson<TeleportRequest>,
) -> Result<axum::http::StatusCode, ApiError> {
    let moved = match state.world() {
        Some(world) => world.dev_teleport(player.character.id, req.pos).await,
        None => false,
    };
    if moved {
        Ok(axum::http::StatusCode::NO_CONTENT)
    } else {
        Err(ApiError::conflict(
            "teleport_failed",
            "Not online, or not walkable ground there.",
        ))
    }
}

#[derive(Debug, Deserialize)]
pub struct CupCloseQuery {
    /// Close the cup week holding this Unix ms instead of the running one
    /// (tests close weeks of their own).
    #[serde(default)]
    pub at_unix_ms: Option<i64>,
}

/// `POST /api/dev/house-cup/close`'s answer: `{ closed: false }` alone when
/// the week already had its row.
#[derive(Debug, Serialize)]
pub struct CupCloseResponse {
    pub closed: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub week: Option<crate::house_cup::WeekView>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub winners: Option<Vec<House>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub houses: Option<Vec<crate::house_cup::HousePoints>>,
    /// Characters mailed the reward.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mailed: Option<usize>,
}

/// `POST /api/dev/house-cup/close[?at_unix_ms=]` (dev only):
/// closes the running House Cup week now (real UTC time, not the dev game
/// clock) as the minute job closes an ended one: the winners' row and the
/// reward mails. Then the cached standings are dropped, so the badge and
/// the Great Hall banners switch at once, and the mailed characters'
/// unread counts go out. The real close at the week's end then finds the
/// row and does nothing.
async fn house_cup_close(
    State(state): State<AppState>,
    _player: Player,
    ApiQuery(q): ApiQuery<CupCloseQuery>,
) -> Result<ApiJson<CupCloseResponse>, ApiError> {
    let at = q
        .at_unix_ms
        .unwrap_or_else(|| chrono::Utc::now().timestamp_millis());
    let week = shipped().house_cup.week_of(at);
    let Some(closed) = crate::house_cup::close_week(&state.pool, week).await? else {
        return Ok(ApiJson(CupCloseResponse {
            closed: false,
            week: None,
            winners: None,
            houses: None,
            mailed: None,
        }));
    };
    crate::house_cup::forget_cached();
    for &recipient in &closed.mailed {
        crate::mail::notify(&state, recipient).await;
    }
    Ok(ApiJson(CupCloseResponse {
        closed: true,
        week: Some(closed.week.into()),
        winners: Some(closed.winners),
        houses: Some(closed.houses),
        mailed: Some(closed.mailed.len()),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn houses_spread_by_name() {
        assert_eq!(house_for("bot0001"), house_for("bot0001"));
        let distinct: std::collections::HashSet<i16> = ["bot0000", "bot0001", "bot0002", "bot0003"]
            .iter()
            .map(|n| characters::house_to_db(house_for(n)))
            .collect();
        assert_eq!(distinct.len(), 4);
    }

    #[test]
    fn bot_names() {
        assert!(validate_bot_name("bot0042").is_ok());
        assert!(validate_bot_name("load_test-7").is_ok());
        assert_eq!(validate_bot_name("b1"), Err(NameError::Length));
        assert_eq!(validate_bot_name("7bot"), Err(NameError::Characters));
        assert_eq!(validate_bot_name("bot 7"), Err(NameError::Characters));
    }
}
