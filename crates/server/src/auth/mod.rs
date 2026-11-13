//! Accounts: registration (with the adult attestation), email verification
//! (which signs in), login, logout and password reset ([`reset`]). See docs/architecture.md (HTTP API) for the contract.
//!
//! Per-account limits (`AppState::account_limits`, on top of the per-IP
//! `rate_limit::limit_auth`): the emails sent to an account on request
//! (reset links and re-sent verification links, one count) and its wrong
//! passwords, each a fixed window (`Config::email_limit`,
//! `Config::login_failure_limit`). Signing out ends
//! the game connection that signed in with that session; a password reset
//! ends the account's connection whatever its session.

pub mod password;
pub mod reset;
pub mod rules;
pub mod session;
pub mod token;

use axum::Json;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Redirect, Response};
use chrono::{DateTime, Utc};
use protocol::KickCode;
use serde::{Deserialize, Serialize};
use serde_json::json;

use self::rules::RuleError;
pub use self::session::{AuthSession, SessionInfo, create_session, validate_session};
use crate::AppState;
use crate::db::is_unique_violation;
use crate::email;
use crate::http::{ApiError, ApiJson, ApiQuery};
use crate::rate_limit::{self, AccountKey};

impl From<RuleError> for ApiError {
    fn from(e: RuleError) -> Self {
        use RuleError::*;
        match e {
            InvalidEmail => ApiError::bad_request("invalid_email", "Enter a valid email address."),
            AdultUnconfirmed => ApiError::bad_request(
                "adult_unconfirmed",
                "You must be 18 or older to play: confirm it to continue.",
            ),
            PasswordTooShort => ApiError::bad_request(
                "weak_password",
                format!("Use at least {} characters.", rules::PASSWORD_MIN_CHARS),
            ),
            PasswordTooLong => ApiError::bad_request(
                "weak_password",
                format!("Use at most {} characters.", rules::PASSWORD_MAX_CHARS),
            ),
            PasswordIsEmail => ApiError::bad_request(
                "weak_password",
                "Your password must not be your email address.",
            ),
        }
    }
}

fn blocking_failed(e: tokio::task::JoinError) -> ApiError {
    ApiError::internal_error(&e)
}

#[derive(Debug, Deserialize)]
pub struct RegisterRequest {
    pub email: String,
    pub password: String,
    /// "I am 18 or older": required. Only the time of the attestation is stored.
    #[serde(default)]
    pub confirm_adult: bool,
}

#[derive(Debug, Serialize)]
pub struct RegisterResponse {
    /// Dev only (`SHS_DEV=1`): the link the verification email carries.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dev_verify_url: Option<String>,
}

/// `POST /api/auth/register`
pub async fn register(
    State(state): State<AppState>,
    ApiJson(req): ApiJson<RegisterRequest>,
) -> Result<(StatusCode, Json<RegisterResponse>), ApiError> {
    rules::check_adult(req.confirm_adult)?;
    let email = rules::normalize_email(&req.email)?;
    rules::check_password(&req.password, &email)?;
    let password_hash = password::hash(req.password)
        .await
        .map_err(blocking_failed)?;

    let mut tx = state.pool.begin().await?;
    let inserted: Result<i64, sqlx::Error> = sqlx::query_scalar(
        "INSERT INTO accounts (email, password_hash, adult_attested_at)
         VALUES ($1, $2, now()) RETURNING id",
    )
    .bind(&email)
    .bind(&password_hash)
    .fetch_one(&mut *tx)
    .await;
    let account_id = match inserted {
        Ok(id) => id,
        Err(e) if is_unique_violation(&e, "accounts_email_key") => {
            // The client sends them to sign in, where an unverified account is
            // sent a fresh confirmation link. Nothing is created or emailed here.
            return Err(ApiError::conflict(
                "email_taken",
                "You already have an account with this email. Sign in instead.",
            ));
        }
        Err(e) => return Err(e.into()),
    };
    let link = issue_verification(&state, &mut tx, account_id).await?;
    tx.commit().await?;
    state.metrics.registrations.inc();
    send_verification(&state, &email, &link, account_id).await;

    let dev_verify_url = state.config.dev.then_some(link);
    Ok((
        StatusCode::CREATED,
        Json(RegisterResponse { dev_verify_url }),
    ))
}

/// Stores a new single-use verification token for the account and returns the
/// link that carries it: the client's confirmation page (`/?verify=…`), which
/// spends the token with `POST /api/auth/verify`. Earlier unexpired links stay valid.
async fn issue_verification(
    state: &AppState,
    conn: &mut sqlx::PgConnection,
    account_id: i64,
) -> Result<String, ApiError> {
    let (verify_token, verify_hash) = token::generate();
    let ttl = chrono::Duration::from_std(state.config.verification_ttl)
        .expect("verification ttl fits chrono");
    sqlx::query(
        "INSERT INTO email_verifications (token_hash, account_id, expires_at)
         VALUES ($1, $2, now() + $3)",
    )
    .bind(&verify_hash[..])
    .bind(account_id)
    .bind(ttl)
    .execute(conn)
    .await?;
    Ok(verify_page(state, &verify_token))
}

/// The client's confirmation page for a verification token.
fn verify_page(state: &AppState, token: &str) -> String {
    format!("{}/?verify={token}", state.config.public_url)
}

/// Emails a verification link. A failure is only logged: the account exists,
/// and signing in with the right password sends a new link.
async fn send_verification(state: &AppState, to: &str, link: &str, account_id: i64) {
    let hours = state.config.verification_ttl.as_secs() / 3600;
    if let Err(e) = state
        .mailer
        .send(email::verification_email(to, link, hours))
        .await
    {
        tracing::error!(error = %e, account_id, "verification email failed");
        goodsorcery::capture_error(&e);
    }
}

#[derive(Debug, Deserialize)]
pub struct VerifyQuery {
    #[serde(default)]
    pub token: String,
}

/// `GET /api/auth/verify?token=…`: where links emailed before the client's
/// confirmation page existed point. Sends the browser on to that page without
/// spending the token. A malformed token still lands there, as `invalid`, so
/// the page explains it.
pub async fn verify_link(
    State(state): State<AppState>,
    ApiQuery(q): ApiQuery<VerifyQuery>,
) -> Redirect {
    let token = if token::looks_valid(&q.token) {
        q.token.as_str()
    } else {
        "invalid"
    };
    Redirect::to(&verify_page(&state, token))
}

#[derive(Debug, Deserialize)]
pub struct VerifyRequest {
    #[serde(default)]
    pub token: String,
}

/// `POST /api/auth/verify`: confirms the email with a link's token and signs
/// in, answering like [`login`]. Following the link proves the inbox (as a
/// password reset does), so the player goes straight on to their character.
/// Tokens are single-use and expire (`Config::verification_ttl`).
pub async fn verify(
    State(state): State<AppState>,
    ApiJson(req): ApiJson<VerifyRequest>,
) -> Result<Json<LoginResponse>, ApiError> {
    let invalid = || {
        ApiError::bad_request(
            "invalid_token",
            "This link is invalid or has expired. If you already confirmed your email, just sign in.",
        )
    };
    if !token::looks_valid(&req.token) {
        return Err(invalid());
    }
    let hash = token::hash(&req.token);
    let mut tx = state.pool.begin().await?;
    // Consume the token whether or not it is still valid, then verify if it was.
    let verified: Option<i64> = sqlx::query_scalar(
        "WITH v AS (
             DELETE FROM email_verifications WHERE token_hash = $1
             RETURNING account_id, expires_at
         )
         UPDATE accounts a
         SET email_verified_at = coalesce(a.email_verified_at, now())
         FROM v
         WHERE a.id = v.account_id AND v.expires_at > now()
         RETURNING a.id",
    )
    .bind(&hash[..])
    .fetch_optional(&mut *tx)
    .await?;
    let Some(account_id) = verified else {
        tx.commit().await?;
        return Err(invalid());
    };
    let (token, expires_at) =
        create_session(&mut *tx, account_id, state.config.session_ttl).await?;
    tx.commit().await?;
    state.metrics.logins_ok.inc();
    Ok(Json(LoginResponse { token, expires_at }))
}

#[derive(Debug, Deserialize)]
pub struct LoginRequest {
    pub email: String,
    pub password: String,
}

#[derive(Debug, Serialize)]
pub struct LoginResponse {
    pub token: String,
    pub expires_at: DateTime<Utc>,
}

#[derive(sqlx::FromRow)]
struct LoginRow {
    id: i64,
    email: String,
    password_hash: String,
    email_verified_at: Option<DateTime<Utc>>,
}

/// `POST /api/auth/login`
pub async fn login(
    State(state): State<AppState>,
    ApiJson(req): ApiJson<LoginRequest>,
) -> Result<Response, ApiError> {
    let wrong = || {
        ApiError::new(
            StatusCode::UNAUTHORIZED,
            "invalid_credentials",
            "That email and password don't match.",
        )
    };
    let row: Option<LoginRow> =
        match rules::normalize_email(&req.email) {
            Ok(email) => sqlx::query_as(
                "SELECT id, email, password_hash, email_verified_at FROM accounts WHERE lower(email) = $1",
            )
            .bind(email)
            .fetch_optional(&state.pool)
            .await?,
            Err(_) => None,
        };

    // Too many wrong passwords for this account: refused until the window
    // ends, the right password included (before the costly hash). The
    // attempt is counted up front, so guesses racing in parallel cannot slip
    // past the limit; the right password then clears the count.
    let key = row.as_ref().map(|r| AccountKey(r.id));
    if let Some(key) = key
        && let Err(wait) = state.account_limits.login_failures.take(key)
    {
        state.metrics.rate_limited.inc();
        state.metrics.logins_failed.inc();
        return Err(rate_limit::too_many_attempts(wait));
    }

    let phc = row.as_ref().map(|r| r.password_hash.clone());
    let ok = password::verify(req.password, phc)
        .await
        .map_err(blocking_failed)?;
    let row = match row {
        Some(row) if ok => row,
        _ => {
            state.metrics.logins_failed.inc();
            return Err(wrong());
        }
    };
    state
        .account_limits
        .login_failures
        .clear(AccountKey(row.id));
    // Checked only after the password, so this does not reveal which emails exist.
    // The right password proves the owner, so a fresh confirmation link goes out
    // (the first may have expired or been lost) and signing in never dead-ends.
    if row.email_verified_at.is_none() {
        state.metrics.logins_failed.inc();
        // Over the email allowance no new link goes out; the earlier ones
        // still work (the client keeps them on the "check your email" card).
        if let Err(wait) = state.account_limits.emails.take(AccountKey(row.id)) {
            state.metrics.rate_limited.inc();
            return Err(rate_limit::too_many_emails(wait));
        }
        let mut conn = state.pool.acquire().await?;
        let link = issue_verification(&state, &mut conn, row.id).await?;
        drop(conn);
        send_verification(&state, &row.email, &link, row.id).await;
        let mut body = json!({
            "error": "unverified",
            "message": "Confirm your email address first: we sent you a new link.",
        });
        if state.config.dev {
            body["dev_verify_url"] = link.into();
        }
        return Ok((StatusCode::FORBIDDEN, Json(body)).into_response());
    }

    let (token, expires_at) = create_session(&state.pool, row.id, state.config.session_ttl).await?;
    state.metrics.logins_ok.inc();
    Ok(Json(LoginResponse { token, expires_at }).into_response())
}

/// `POST /api/auth/logout`: ends the session whose token authenticated the
/// request, and the game connection that signed in with it (not one another
/// device opened with another session).
pub async fn logout(
    State(state): State<AppState>,
    auth: AuthSession,
) -> Result<StatusCode, ApiError> {
    session::delete_session(&state.pool, &auth.token_hash).await?;
    if let Some(rt) = &state.realtime {
        rt.kick_session(auth.account_id, &auth.token_hash, KickCode::SessionExpired);
    }
    Ok(StatusCode::NO_CONTENT)
}
