//! Login sessions: creation, lookup for the WebSocket gateway
//! ([`validate_session`], then [`session_playing`] to re-check a live
//! connection) and the bearer-token extractor for HTTP handlers
//! ([`AuthSession`]).

use std::time::Duration;

use axum::extract::FromRequestParts;
use axum::http::header::AUTHORIZATION;
use axum::http::request::Parts;
use chrono::{DateTime, Utc};
use sqlx::{PgExecutor, PgPool};

use super::token::{self, TokenHash};
use crate::AppState;
use crate::characters::{Character, CharacterRow};
use crate::http::ApiError;

/// Who a session token belongs to: what the gateway needs to admit a player.
#[derive(Debug, Clone, PartialEq)]
pub struct SessionInfo {
    pub account_id: i64,
    /// The character the session plays (`sessions.character_id`); `None`
    /// until the player picks or creates one.
    pub character: Option<Character>,
    /// The character has had its starter wardrobe (false for one made before
    /// starter wardrobes existed, which the gateway then grants on `Hello`).
    pub starter_granted: bool,
}

/// Creates a session for `account_id` and returns the token (shown to the
/// client once, never stored) and its expiry.
pub async fn create_session(
    db: impl PgExecutor<'_>,
    account_id: i64,
    ttl: Duration,
) -> Result<(String, DateTime<Utc>), sqlx::Error> {
    let (token, hash) = token::generate();
    let ttl = chrono::Duration::from_std(ttl).expect("session ttl fits chrono");
    let expires_at: DateTime<Utc> = sqlx::query_scalar(
        "INSERT INTO sessions (token_hash, account_id, expires_at)
         VALUES ($1, $2, now() + $3) RETURNING expires_at",
    )
    .bind(&hash[..])
    .bind(account_id)
    .bind(ttl)
    .fetch_one(db)
    .await?;
    Ok((token, expires_at))
}

/// Resolves a session token (from `ClientMsg::Hello`) to its account and
/// the character it plays. `None` for unknown, malformed or expired tokens, and when the
/// database is unreachable (logged), so callers simply refuse the connection.
pub async fn validate_session(pool: &PgPool, token: &str) -> Option<SessionInfo> {
    if !token::looks_valid(token) {
        return None;
    }
    let hash = token::hash(token);
    let row: Result<Option<SessionRow>, _> = sqlx::query_as(
        "SELECT s.account_id,
                c.id AS character_id, c.name, c.house, c.zone, c.pos_x, c.pos_y, c.pos_z,
                c.appearance_version, c.appearance,
                c.starter_granted_at IS NOT NULL AS starter_granted
         FROM sessions s
         LEFT JOIN characters c ON c.id = s.character_id AND c.account_id = s.account_id
         WHERE s.token_hash = $1 AND s.expires_at > now()",
    )
    .bind(&hash[..])
    .fetch_optional(pool)
    .await;
    match row {
        Ok(row) => row.map(SessionRow::into_info),
        Err(e) => {
            tracing::error!(error = %e, "session lookup failed");
            None
        }
    }
}

#[derive(sqlx::FromRow)]
struct SessionRow {
    account_id: i64,
    character_id: Option<i64>,
    name: Option<String>,
    house: Option<i16>,
    zone: Option<String>,
    pos_x: Option<f32>,
    pos_y: Option<f32>,
    pos_z: Option<f32>,
    appearance_version: Option<i32>,
    appearance: Option<sqlx::types::Json<protocol::Appearance>>,
    starter_granted: Option<bool>,
}

impl SessionRow {
    fn into_info(self) -> SessionInfo {
        let character = match (
            self.character_id,
            self.name,
            self.house,
            self.zone,
            self.appearance_version,
        ) {
            (Some(id), Some(name), Some(house), Some(zone), Some(appearance_version)) => {
                CharacterRow {
                    id,
                    name,
                    house,
                    zone,
                    pos_x: self.pos_x,
                    pos_y: self.pos_y,
                    pos_z: self.pos_z,
                    appearance_version,
                    appearance: self.appearance,
                }
                .into_character()
            }
            _ => None,
        };
        SessionInfo {
            account_id: self.account_id,
            character,
            starter_granted: self.starter_granted.unwrap_or(false),
        }
    }
}

/// What a session (by token hash) says now: the gateway's periodic check
/// of a live connection.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionNow {
    /// Deleted or expired.
    Ended,
    /// Live, playing this character (`None`: none, as after the one it
    /// played was deleted).
    Plays(Option<i64>),
}

/// The session's state now; `None` when the database could not say (logged).
pub async fn session_playing(pool: &PgPool, hash: &TokenHash) -> Option<SessionNow> {
    let row: Result<Option<Option<i64>>, _> = sqlx::query_scalar(
        "SELECT character_id FROM sessions WHERE token_hash = $1 AND expires_at > now()",
    )
    .bind(&hash[..])
    .fetch_optional(pool)
    .await;
    match row {
        Ok(Some(character)) => Some(SessionNow::Plays(character)),
        Ok(None) => Some(SessionNow::Ended),
        Err(e) => {
            tracing::warn!(error = %e, "session check failed");
            None
        }
    }
}

/// Deletes one session. Returns whether it existed.
pub async fn delete_session(
    db: impl PgExecutor<'_>,
    hash: &TokenHash,
) -> Result<bool, sqlx::Error> {
    let res = sqlx::query("DELETE FROM sessions WHERE token_hash = $1")
        .bind(&hash[..])
        .execute(db)
        .await?;
    Ok(res.rows_affected() > 0)
}

/// Extractor for endpoints that need a signed-in player
/// (`Authorization: Bearer <token>`); rejects with `401 unauthorized`.
#[derive(Debug, Clone)]
pub struct AuthSession {
    pub account_id: i64,
    pub token_hash: TokenHash,
    /// The character the session plays (`sessions.character_id`), if one
    /// has been picked (`characters::Player` loads it).
    pub character_id: Option<i64>,
}

impl FromRequestParts<AppState> for AuthSession {
    type Rejection = ApiError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &AppState,
    ) -> Result<Self, Self::Rejection> {
        let token = bearer_token(parts).ok_or_else(ApiError::unauthorized)?;
        if !token::looks_valid(token) {
            return Err(ApiError::unauthorized());
        }
        let token_hash = token::hash(token);
        let row: Option<(i64, Option<i64>)> = sqlx::query_as(
            "SELECT account_id, character_id FROM sessions
             WHERE token_hash = $1 AND expires_at > now()",
        )
        .bind(&token_hash[..])
        .fetch_optional(&state.pool)
        .await?;
        match row {
            Some((account_id, character_id)) => Ok(Self {
                account_id,
                token_hash,
                character_id,
            }),
            None => Err(ApiError::unauthorized()),
        }
    }
}

fn bearer_token(parts: &Parts) -> Option<&str> {
    let value = parts.headers.get(AUTHORIZATION)?.to_str().ok()?;
    let (scheme, token) = value.split_once(' ')?;
    scheme.eq_ignore_ascii_case("bearer").then(|| token.trim())
}
