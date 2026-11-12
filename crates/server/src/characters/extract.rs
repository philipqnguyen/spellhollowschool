//! Request extractors shared by the data side's endpoints (characters,
//! inventory, outfits, friends):
//!
//! - [`Player`]: a signed-in player with the character their session plays
//!   (`401 unauthorized`, `404 no_character` until one is picked or created).
//!   A character made before starter wardrobes gets its
//!   starter wardrobe here on its first request (or on its first WebSocket
//!   `Hello`, if that comes first).
//! - [`Writer`]: a [`Player`] that also spends one token of the route group's
//!   per-account write budget (`429 rate_limited` with `Retry-After`).
//!   [`LimitedSession`] is the same without a character (character creation).
//! - [`Reader`]: a [`Player`] that spends a token of a read budget
//!   ([`read_limited`]), for reads that cost the world work (the friend list).
//! - [`ApiPath`]: `axum::extract::Path` with `{ error, message }` rejections.
//!
//! Write budgets are per account and per route group: each module's
//! `routes()` attaches its own limiter with [`write_limited`], so spamming
//! friend requests does not eat into the wardrobe budget. The limiter is
//! `rate_limit::RateLimiter`, keyed by `rate_limit::AccountKey`.

use std::sync::Arc;

use axum::extract::rejection::PathRejection;
use axum::extract::{Extension, FromRequestParts};
use axum::http::request::Parts;
use axum::http::{HeaderValue, StatusCode, header::RETRY_AFTER};
use axum::response::{IntoResponse, Response};
use axum::{Router, extract::Path};
use serde::de::DeserializeOwned;

use super::{CHARACTER_COLUMNS, Character, CharacterRow};
use crate::AppState;
use crate::auth::AuthSession;
use crate::http::ApiError;
use crate::inventory::starter;
use crate::rate_limit::{AccountKey, RateLimit, RateLimiter};

/// A signed-in player and the character their session plays
/// (`sessions.character_id`).
#[derive(Debug, Clone)]
pub struct Player {
    pub account_id: i64,
    pub character: Character,
}

impl FromRequestParts<AppState> for Player {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, state: &AppState) -> Result<Self, ApiError> {
        let auth = AuthSession::from_request_parts(parts, state).await?;
        load_player(state, &auth).await
    }
}

#[derive(sqlx::FromRow)]
struct PlayerRow {
    #[sqlx(flatten)]
    character: CharacterRow,
    granted: bool,
}

/// Loads the character the session plays, granting the starter wardrobe
/// first if it predates starter wardrobes.
pub(crate) async fn load_player(state: &AppState, auth: &AuthSession) -> Result<Player, ApiError> {
    let account_id = auth.account_id;
    let Some(character_id) = auth.character_id else {
        return Err(no_character());
    };
    let row: Option<PlayerRow> = sqlx::query_as(&format!(
        "SELECT {CHARACTER_COLUMNS}, starter_granted_at IS NOT NULL AS granted
         FROM characters WHERE id = $1 AND account_id = $2"
    ))
    .bind(character_id)
    .bind(account_id)
    .fetch_optional(&state.pool)
    .await?;
    let Some(row) = row else {
        return Err(no_character());
    };
    let granted = row.granted;
    let character = row
        .character
        .into_character()
        .ok_or_else(|| ApiError::internal("character row has an unknown house"))?;
    let character = if granted {
        character
    } else {
        starter::ensure_granted(state, character).await?
    };
    // Off with anything the body may not wear (a no-op
    // unless the stored look breaks the rule).
    let character = starter::take_off_unwearable(&state.pool, state.world(), character).await?;
    Ok(Player {
        account_id,
        character,
    })
}

/// No character is being played: the session has not picked one (or the
/// one it played was deleted). The client goes back to the picker.
pub fn no_character() -> ApiError {
    ApiError::not_found("no_character", "Choose a character first.")
}

/// A [`Player`] making a change: one token of the route group's write budget.
#[derive(Debug, Clone)]
pub struct Writer(pub Player);

impl FromRequestParts<AppState> for Writer {
    type Rejection = Response;

    async fn from_request_parts(parts: &mut Parts, state: &AppState) -> Result<Self, Response> {
        let LimitedSession(auth) = LimitedSession::from_request_parts(parts, state).await?;
        load_player(state, &auth)
            .await
            .map(Writer)
            .map_err(IntoResponse::into_response)
    }
}

/// A [`Player`] reading something that costs the world work (the friend list
/// asks it where each friend is): one token of the route's read budget, set
/// with [`read_limited`]. Plain database reads need no budget.
#[derive(Debug, Clone)]
pub struct Reader(pub Player);

impl FromRequestParts<AppState> for Reader {
    type Rejection = Response;

    async fn from_request_parts(parts: &mut Parts, state: &AppState) -> Result<Self, Response> {
        let auth = AuthSession::from_request_parts(parts, state)
            .await
            .map_err(IntoResponse::into_response)?;
        let Some(limiter) = parts.extensions.get::<ReadLimiter>().cloned() else {
            return Err(ApiError::internal("read route has no rate limiter").into_response());
        };
        if let Err(wait) = limiter.0.check(AccountKey(auth.account_id)) {
            return Err(rate_limited(wait));
        }
        load_player(state, &auth)
            .await
            .map(Reader)
            .map_err(IntoResponse::into_response)
    }
}

/// A route group's read limiter, attached by [`read_limited`].
#[derive(Debug, Clone)]
pub struct ReadLimiter(Arc<RateLimiter<AccountKey>>);

/// Gives every route of `router` (so far) a per-account read budget, spent
/// by [`Reader`].
pub fn read_limited(router: Router<AppState>, limit: RateLimit) -> Router<AppState> {
    router.layer(Extension(ReadLimiter(Arc::new(RateLimiter::new(limit)))))
}

/// A signed-in account making a change (one write token), for endpoints that
/// do not need an existing character.
#[derive(Debug, Clone)]
pub struct LimitedSession(pub AuthSession);

impl FromRequestParts<AppState> for LimitedSession {
    type Rejection = Response;

    async fn from_request_parts(parts: &mut Parts, state: &AppState) -> Result<Self, Response> {
        let auth = AuthSession::from_request_parts(parts, state)
            .await
            .map_err(IntoResponse::into_response)?;
        let Some(limiter) = parts.extensions.get::<WriteLimiter>().cloned() else {
            // A route using `Writer` without `write_limited`: a wiring bug.
            return Err(ApiError::internal("write route has no rate limiter").into_response());
        };
        match limiter.0.check(AccountKey(auth.account_id)) {
            Ok(()) => Ok(LimitedSession(auth)),
            Err(wait) => Err(rate_limited(wait)),
        }
    }
}

/// A route group's write limiter, attached by [`write_limited`].
#[derive(Debug, Clone)]
pub struct WriteLimiter(Arc<RateLimiter<AccountKey>>);

/// Gives every route of `router` (so far) a fresh per-account write budget,
/// spent by [`Writer`] and [`LimitedSession`]. Call it last in a module's
/// `routes()`.
pub fn write_limited(router: Router<AppState>, limit: RateLimit) -> Router<AppState> {
    let limiter = WriteLimiter(Arc::new(RateLimiter::new(limit)));
    router.layer(Extension(limiter))
}

fn rate_limited(wait: std::time::Duration) -> Response {
    let mut res = ApiError::new(
        StatusCode::TOO_MANY_REQUESTS,
        "rate_limited",
        "You are doing that too often. Please wait a moment.",
    )
    .into_response();
    res.headers_mut()
        .insert(RETRY_AFTER, HeaderValue::from(wait.as_secs().max(1)));
    res
}

impl From<PathRejection> for ApiError {
    fn from(r: PathRejection) -> Self {
        Self::bad_request("invalid_path", r.body_text())
    }
}

/// `axum::extract::Path`, but rejections become [`ApiError`]s.
#[derive(Debug)]
pub struct ApiPath<T>(pub T);

impl<T, S> FromRequestParts<S> for ApiPath<T>
where
    T: DeserializeOwned + Send,
    S: Send + Sync,
{
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, ApiError> {
        let Path(t) = Path::<T>::from_request_parts(parts, state).await?;
        Ok(ApiPath(t))
    }
}
