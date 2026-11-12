//! Characters: up to [`MAX_PER_ACCOUNT`] per account, each with a unique
//! (case-insensitive) name, a house and an appearance, played one at a time:
//! the session remembers which (`sessions.character_id`, set by picking or
//! creating one; see [`roster`]). Also the persistence helpers the world
//! uses (load, save positions).
//!
//! Owned by the data side (docs/architecture.md): the HTTP
//! routes, creation with an appearance and the starter grant live here; the
//! realtime side only reads [`Character`] and calls [`save_positions`].
//!
//! Submodules hold what every data-side endpoint builds on: [`extract`]
//! ([`Player`], [`Writer`] and the per-account write budgets, [`ApiPath`])
//! and [`page`] (keyset pagination). [`tour`] (the first-day
//! tour's step). [`roster`]: the account's characters (list, pick, delete).

pub mod extract;
pub mod page;
pub mod roster;
pub mod tour;

use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::{Json, Router};
use protocol::{Appearance, House};
use serde::{Deserialize, Serialize};
use sqlx::types::Json as DbJson;
use sqlx::{PgExecutor, PgPool};

pub use self::extract::{
    ApiPath, LimitedSession, Player, Reader, Writer, read_limited, write_limited,
};
pub use self::page::{Page, PageQuery};
use crate::AppState;
use crate::catalog::shipped;
use crate::db::is_unique_violation;
use crate::http::{ApiError, ApiJson};
use crate::inventory::starter;
use crate::rate_limit::RateLimit;

pub const NAME_MIN_CHARS: usize = 3;
pub const NAME_MAX_CHARS: usize = 24;

/// Most characters one account may have.
pub const MAX_PER_ACCOUNT: i64 = 5;

/// Character creation, picking and deletion per account. A handful of
/// attempts (names taken, typos) is plenty.
pub const CREATE_RATE_LIMIT: RateLimit = RateLimit {
    burst: 10,
    per: Duration::from_secs(10),
};

/// Which zone a saved position is in (`characters.zone`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SavedZone {
    /// The castle and grounds.
    Castle,
    /// The character's own dorm room. A guest in someone else's room is saved
    /// at their return point instead, so this always means "my room".
    Dorm,
}

impl SavedZone {
    pub fn to_db(self) -> &'static str {
        match self {
            SavedZone::Castle => "castle",
            SavedZone::Dorm => "dorm",
        }
    }

    pub fn from_db(s: &str) -> Option<SavedZone> {
        match s {
            "castle" => Some(SavedZone::Castle),
            "dorm" => Some(SavedZone::Dorm),
            _ => None,
        }
    }
}

/// A character as the server keeps it.
#[derive(Debug, Clone, PartialEq)]
pub struct Character {
    pub id: i64,
    pub name: String,
    pub house: House,
    /// Where the character resumes: `zone`, at `last_pos` (metres) in it;
    /// `None` means "use that zone's layout spawn". New characters start in
    /// the castle without a position: on the front steps.
    pub zone: SavedZone,
    pub last_pos: Option<[f32; 3]>,
    pub appearance_version: u32,
    /// `Appearance::default()` until a character made before starter outfits gets one.
    pub appearance: Appearance,
}

/// The database form of [`Character`].
#[derive(Debug, sqlx::FromRow)]
pub(crate) struct CharacterRow {
    pub id: i64,
    pub name: String,
    pub house: i16,
    pub zone: String,
    pub pos_x: Option<f32>,
    pub pos_y: Option<f32>,
    pub pos_z: Option<f32>,
    pub appearance_version: i32,
    pub appearance: Option<DbJson<Appearance>>,
}

impl CharacterRow {
    pub(crate) fn into_character(self) -> Option<Character> {
        let Some(house) = house_from_db(self.house) else {
            tracing::error!(
                character_id = self.id,
                house = self.house,
                "unknown house index"
            );
            return None;
        };
        let last_pos = match (self.pos_x, self.pos_y, self.pos_z) {
            (Some(x), Some(y), Some(z)) => Some([x, y, z]),
            _ => None,
        };
        Some(Character {
            id: self.id,
            name: self.name,
            house,
            // The column's CHECK allows only the two values.
            zone: SavedZone::from_db(&self.zone).unwrap_or(SavedZone::Castle),
            last_pos,
            appearance_version: u32::try_from(self.appearance_version).unwrap_or(0),
            appearance: self
                .appearance
                .map(|j| crate::catalog::migrate_look(j.0))
                .unwrap_or_default(),
        })
    }
}

/// The columns [`CharacterRow`] reads, for `SELECT {CHARACTER_COLUMNS} FROM characters`.
pub(crate) const CHARACTER_COLUMNS: &str =
    "id, name, house, zone, pos_x, pos_y, pos_z, appearance_version, appearance";

/// Houses are stored as their index in `protocol::House` declaration order.
pub fn house_to_db(house: House) -> i16 {
    match house {
        House::Roarwing => 0,
        House::Burrowsby => 1,
        House::Skytalon => 2,
        House::Serpentyne => 3,
    }
}

pub fn house_from_db(index: i16) -> Option<House> {
    Some(match index {
        0 => House::Roarwing,
        1 => House::Burrowsby,
        2 => House::Skytalon,
        3 => House::Serpentyne,
        _ => return None,
    })
}

/// Why a name was refused (all map to `400 invalid_name`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NameError {
    Length,
    Characters,
}

/// Checks a character name and returns it trimmed.
///
/// Allowed: 3–24 characters of Latin letters (ASCII and accented), with single
/// spaces, apostrophes or hyphens between letters ("Mary-Kate", "O'Neil",
/// "Anne Marie"). Restricting the script keeps look-alike names from other
/// alphabets from impersonating players.
pub fn validate_name(raw: &str) -> Result<String, NameError> {
    let name = raw.trim();
    let n = name.chars().count();
    if !(NAME_MIN_CHARS..=NAME_MAX_CHARS).contains(&n) {
        return Err(NameError::Length);
    }
    let chars: Vec<char> = name.chars().collect();
    let is_letter = |c: char| {
        c.is_ascii_alphabetic()
            || (('\u{C0}'..='\u{17F}').contains(&c) && c != '\u{D7}' && c != '\u{F7}')
    };
    let is_joiner = |c: char| matches!(c, ' ' | '\'' | '-');
    let first_last_ok = is_letter(chars[0]) && is_letter(chars[n - 1]);
    let body_ok = chars.iter().all(|&c| is_letter(c) || is_joiner(c));
    let no_double_joiners = chars
        .windows(2)
        .all(|w| !(is_joiner(w[0]) && is_joiner(w[1])));
    if first_last_ok && body_ok && no_double_joiners {
        Ok(name.to_owned())
    } else {
        Err(NameError::Characters)
    }
}

/// The case-folded key names are unique by.
pub fn name_key(name: &str) -> String {
    name.to_lowercase()
}

impl From<NameError> for ApiError {
    fn from(e: NameError) -> Self {
        let message = match e {
            NameError::Length => {
                format!("Names are {NAME_MIN_CHARS} to {NAME_MAX_CHARS} characters long.")
            }
            NameError::Characters => {
                "Use letters, with single spaces, hyphens or apostrophes between them.".to_owned()
            }
        };
        ApiError::bad_request("invalid_name", message)
    }
}

/// Inserts a character starting in `zone` at its layout spawn (players and
/// load-test bots both start in the castle). Unique
/// violations are returned as-is for the caller to map (see [`map_insert_error`]).
pub async fn insert(
    db: impl PgExecutor<'_>,
    account_id: i64,
    name: &str,
    house: House,
    zone: SavedZone,
) -> Result<Character, sqlx::Error> {
    let row: CharacterRow = sqlx::query_as(&format!(
        "INSERT INTO characters (account_id, name, name_key, house, zone)
         VALUES ($1, $2, $3, $4, $5) RETURNING {CHARACTER_COLUMNS}"
    ))
    .bind(account_id)
    .bind(name)
    .bind(name_key(name))
    .bind(house_to_db(house))
    .bind(zone.to_db())
    .fetch_one(db)
    .await?;
    row.into_character()
        .ok_or_else(|| sqlx::Error::Protocol("inserted character has no valid house".into()))
}

/// Turns the unique violations of [`insert`] into their API errors.
pub fn map_insert_error(e: sqlx::Error) -> ApiError {
    if is_unique_violation(&e, "characters_name_key") {
        ApiError::conflict("name_taken", "That name is taken. Try another.")
    } else {
        e.into()
    }
}

/// A character by id.
pub async fn find(db: impl PgExecutor<'_>, id: i64) -> Result<Option<Character>, sqlx::Error> {
    let row: Option<CharacterRow> = sqlx::query_as(&format!(
        "SELECT {CHARACTER_COLUMNS} FROM characters WHERE id = $1"
    ))
    .bind(id)
    .fetch_optional(db)
    .await?;
    Ok(row.and_then(CharacterRow::into_character))
}

/// Where a character resumes, as the world saves it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SavedPosition {
    pub character_id: i64,
    pub zone: SavedZone,
    pub pos: [f32; 3],
}

/// Saves many characters' resume points in one statement (the world flushes
/// these periodically and on logout). Unknown ids are ignored.
pub async fn save_positions(
    pool: &PgPool,
    positions: &[SavedPosition],
) -> Result<u64, sqlx::Error> {
    if positions.is_empty() {
        return Ok(0);
    }
    let ids: Vec<i64> = positions.iter().map(|p| p.character_id).collect();
    let zones: Vec<&str> = positions.iter().map(|p| p.zone.to_db()).collect();
    let xs: Vec<f32> = positions.iter().map(|p| p.pos[0]).collect();
    let ys: Vec<f32> = positions.iter().map(|p| p.pos[1]).collect();
    let zs: Vec<f32> = positions.iter().map(|p| p.pos[2]).collect();
    let res = sqlx::query(
        "UPDATE characters c
         SET zone = u.zone, pos_x = u.x, pos_y = u.y, pos_z = u.z, updated_at = now()
         FROM unnest($1::bigint[], $2::text[], $3::real[], $4::real[], $5::real[])
              AS u(id, zone, x, y, z)
         WHERE c.id = u.id",
    )
    .bind(ids)
    .bind(zones)
    .bind(xs)
    .bind(ys)
    .bind(zs)
    .execute(pool)
    .await?;
    Ok(res.rows_affected())
}

/// A character in the HTTP API: `{ id, name, house, appearance,
/// appearance_version }`.
#[derive(Debug, Serialize)]
pub struct CharacterView {
    pub id: i64,
    pub name: String,
    pub house: House,
    pub appearance: Appearance,
    pub appearance_version: u32,
}

impl From<Character> for CharacterView {
    fn from(c: Character) -> Self {
        Self {
            id: c.id,
            name: c.name,
            house: c.house,
            appearance: c.appearance,
            appearance_version: c.appearance_version,
        }
    }
}

/// `GET /api/characters/me`: the character the session plays (`404
/// no_character` until one is picked or created). A character made before
/// starter wardrobes gets its starter wardrobe on the way (see [`Player`]).
pub async fn me(player: Player) -> Json<CharacterView> {
    Json(player.character.into())
}

#[derive(Debug, Deserialize)]
pub struct CreateCharacter {
    pub name: String,
    pub house: House,
    /// The creator's choices; absent means the creator defaults wearing the
    /// house's default starter outfit.
    #[serde(default)]
    pub appearance: Option<Appearance>,
}

/// Checks a creator appearance against the content data and the starter
/// wardrobe of `house` (`400 invalid_appearance`).
pub fn check_new_appearance(appearance: &Appearance, house: House) -> Result<(), ApiError> {
    let c = shipped();
    c.creator
        .check_appearance(appearance, &c.catalog, &c.palette)
        .map_err(|e| {
            ApiError::bad_request(
                "invalid_appearance",
                format!("That look is not available: {e}."),
            )
        })?;
    starter::check_starter_outfit(appearance, house)
}

/// The `appearance_version` of a newly created character (0 is "never had an
/// appearance", as for characters made before starter wardrobes).
const FIRST_APPEARANCE_VERSION: i32 = 1;

/// `POST /api/characters { name, house, appearance? }` → `201 Character`.
/// Creates the character in the castle (no position: it appears on the
/// front steps, the layout's spawns), grants the starter
/// wardrobe and furniture, wears the chosen outfit and makes it the one the
/// session plays, all in one transaction. `409 character_limit` once the
/// account has [`MAX_PER_ACCOUNT`]: the account row is locked while
/// counting, so concurrent creations cannot pass the cap.
pub async fn create(
    State(state): State<AppState>,
    LimitedSession(auth): LimitedSession,
    ApiJson(req): ApiJson<CreateCharacter>,
) -> Result<(StatusCode, Json<CharacterView>), ApiError> {
    let name = validate_name(&req.name)?;
    let appearance = match req.appearance {
        Some(a) => {
            check_new_appearance(&a, req.house)?;
            a
        }
        None => starter::default_appearance(req.house),
    };

    let mut tx = state.pool.begin().await?;
    sqlx::query("SELECT 1 FROM accounts WHERE id = $1 FOR NO KEY UPDATE")
        .bind(auth.account_id)
        .execute(&mut *tx)
        .await?;
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM characters WHERE account_id = $1")
        .bind(auth.account_id)
        .fetch_one(&mut *tx)
        .await?;
    if count >= MAX_PER_ACCOUNT {
        return Err(character_limit());
    }
    let character = insert(
        &mut *tx,
        auth.account_id,
        &name,
        req.house,
        SavedZone::Castle,
    )
    .await
    .map_err(map_insert_error)?;
    starter::grant(&mut tx, character.id, req.house, &appearance.outfit).await?;
    sqlx::query(
        "UPDATE characters
         SET appearance = $2, appearance_version = $3, starter_granted_at = now()
         WHERE id = $1",
    )
    .bind(character.id)
    .bind(DbJson(appearance))
    .bind(FIRST_APPEARANCE_VERSION)
    .execute(&mut *tx)
    .await?;
    roster::select_for_session(&mut *tx, &auth.token_hash, character.id).await?;
    tx.commit().await?;
    roster::end_other_play(&state, &auth, character.id);

    let character = Character {
        appearance,
        appearance_version: FIRST_APPEARANCE_VERSION as u32,
        ..character
    };
    if let Some(world) = state.world() {
        world.appearance_changed(character.id, appearance, character.appearance_version);
    }
    Ok((StatusCode::CREATED, Json(character.into())))
}

/// `409 character_limit`: the account has [`MAX_PER_ACCOUNT`] characters.
pub fn character_limit() -> ApiError {
    ApiError::conflict(
        "character_limit",
        format!(
            "You already have {MAX_PER_ACCOUNT} characters, the most an account can have. \
             Delete one to make room."
        ),
    )
}

/// Finds a character by name, ignoring case (friend requests, blocks).
pub async fn find_by_name(
    db: impl PgExecutor<'_>,
    name: &str,
) -> Result<Option<Character>, sqlx::Error> {
    let row: Option<CharacterRow> = sqlx::query_as(&format!(
        "SELECT {CHARACTER_COLUMNS} FROM characters WHERE name_key = $1"
    ))
    .bind(name_key(name.trim()))
    .fetch_optional(db)
    .await?;
    Ok(row.and_then(CharacterRow::into_character))
}

/// This module's routes, merged under `/api` by `http::router`
/// (docs/architecture.md, "HTTP API").
pub fn routes() -> Router<AppState> {
    use axum::routing::{delete, get, post};
    let router = Router::new()
        .route("/characters/me", get(me))
        .route(
            "/characters/me/tour",
            get(tour::get_tour).post(tour::post_tour),
        )
        .route("/characters", get(roster::list).post(create))
        .route("/characters/{id}", delete(roster::delete))
        .route("/characters/{id}/select", post(roster::select));
    write_limited(router, CREATE_RATE_LIMIT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn house_index_round_trips() {
        for h in [
            House::Roarwing,
            House::Burrowsby,
            House::Skytalon,
            House::Serpentyne,
        ] {
            assert_eq!(house_from_db(house_to_db(h)), Some(h));
        }
        assert_eq!(house_from_db(4), None);
    }

    #[test]
    fn accepts_reasonable_names() {
        for ok in [
            "Ada",
            "Mary-Kate",
            "O'Neil",
            "Anne Marie",
            "Zoë",
            "Émile Durand",
            "  Trimmed  ",
        ] {
            assert!(validate_name(ok).is_ok(), "{ok:?}");
        }
        assert_eq!(validate_name("  Trimmed ").unwrap(), "Trimmed");
    }

    #[test]
    fn rejects_bad_names() {
        assert_eq!(validate_name("Al"), Err(NameError::Length));
        assert_eq!(validate_name(&"a".repeat(25)), Err(NameError::Length));
        for bad in [
            "-Ada", "Ada-", "Ada  Bee", "Ada--Bee", "Ada1", "Ada_Bee", "Аda", "A×da", "Ada\tBee",
        ] {
            assert_eq!(validate_name(bad), Err(NameError::Characters), "{bad:?}");
        }
    }

    #[test]
    fn name_keys_fold_unicode_case() {
        assert_eq!(name_key("ÉMILE"), name_key("émile"));
    }
}
