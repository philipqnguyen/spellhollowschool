//! Password reset: ask for a link by email, then set a new password with it.
//! See docs/architecture.md (HTTP API) for the contract.
//!
//! The token is 32 random bytes stored only as a hash, valid for
//! `Config::password_reset_ttl` (an hour), single-use, and one per account
//! (a new request replaces the old link). A reset signs the account out
//! everywhere, its live game connection included, and counts as proof of
//! the email address.
//!
//! Each link sent counts towards the account's email allowance
//! (`Config::email_limit`, shared with verification links): over it the
//! request is `429 too_many_emails` with `retry_after`, nothing is sent and
//! the last link stays valid.

use axum::Json;
use axum::extract::State;
use protocol::KickCode;
use serde::{Deserialize, Serialize};
use serde_json::json;

use super::{blocking_failed, password, rules, token};
use crate::AppState;
use crate::email;
use crate::http::{ApiError, ApiJson};
use crate::rate_limit::{self, AccountKey};

#[derive(Debug, Deserialize)]
pub struct ResetRequest {
    pub email: String,
}

#[derive(Debug, Serialize)]
pub struct ResetRequestResponse {
    /// Dev only (`SHS_DEV=1`): the link the reset email carries.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dev_reset_url: Option<String>,
}

/// `POST /api/auth/password-reset`
///
/// An unknown email is `404 no_account`: registering already says when an
/// email has an account (`409 email_taken`), so hiding it here would protect
/// nothing and only leave a mistyped address unexplained.
pub async fn request(
    State(state): State<AppState>,
    ApiJson(req): ApiJson<ResetRequest>,
) -> Result<Json<ResetRequestResponse>, ApiError> {
    let email = rules::normalize_email(&req.email)?;
    let account: Option<(i64, String)> =
        sqlx::query_as("SELECT id, email FROM accounts WHERE lower(email) = $1")
            .bind(&email)
            .fetch_optional(&state.pool)
            .await?;
    let Some((account_id, to)) = account else {
        return Err(ApiError::not_found(
            "no_account",
            "There's no account with this email. Check it, or create an account.",
        ));
    };

    // Before replacing the link: refused, the one already sent keeps working.
    if let Err(wait) = state.account_limits.emails.take(AccountKey(account_id)) {
        state.metrics.rate_limited.inc();
        return Err(rate_limit::too_many_emails(wait));
    }

    let (reset_token, reset_hash) = token::generate();
    let ttl = chrono::Duration::from_std(state.config.password_reset_ttl)
        .expect("password reset ttl fits chrono");
    sqlx::query(
        "INSERT INTO password_resets (account_id, token_hash, expires_at)
         VALUES ($1, $2, now() + $3)
         ON CONFLICT (account_id) DO UPDATE
         SET token_hash = excluded.token_hash,
             created_at = now(),
             expires_at = excluded.expires_at",
    )
    .bind(account_id)
    .bind(&reset_hash[..])
    .bind(ttl)
    .execute(&state.pool)
    .await?;

    let link = format!("{}/?reset={reset_token}", state.config.public_url);
    let minutes = state.config.password_reset_ttl.as_secs() / 60;
    if let Err(e) = state
        .mailer
        .send(email::password_reset_email(&to, &link, minutes))
        .await
    {
        tracing::error!(error = %e, account_id, "password reset email failed");
        return Err(ApiError::internal(format!("password reset email: {e}")));
    }
    Ok(Json(ResetRequestResponse {
        dev_reset_url: state.config.dev.then_some(link),
    }))
}

#[derive(Debug, Deserialize)]
pub struct ResetConfirm {
    pub token: String,
    pub password: String,
}

/// `POST /api/auth/password-reset/confirm`
pub async fn confirm(
    State(state): State<AppState>,
    ApiJson(req): ApiJson<ResetConfirm>,
) -> Result<Json<serde_json::Value>, ApiError> {
    let invalid = || {
        ApiError::bad_request(
            "invalid_token",
            "This reset link is invalid or has expired. Ask for a new one.",
        )
    };
    if !token::looks_valid(&req.token) {
        return Err(invalid());
    }
    let hash = token::hash(&req.token);
    // Look the token up first, so a weak password doesn't use up the link.
    let found: Option<(i64, String)> = sqlx::query_as(
        "SELECT a.id, a.email FROM password_resets r JOIN accounts a ON a.id = r.account_id
         WHERE r.token_hash = $1 AND r.expires_at > now()",
    )
    .bind(&hash[..])
    .fetch_optional(&state.pool)
    .await?;
    let Some((account_id, email)) = found else {
        return Err(invalid());
    };
    rules::check_password(&req.password, &email)?;
    let password_hash = password::hash(req.password)
        .await
        .map_err(blocking_failed)?;

    let mut tx = state.pool.begin().await?;
    // Consuming the row is the single-use guard: of two racing confirms, one wins.
    let consumed =
        sqlx::query("DELETE FROM password_resets WHERE token_hash = $1 AND expires_at > now()")
            .bind(&hash[..])
            .execute(&mut *tx)
            .await?
            .rows_affected();
    if consumed == 0 {
        return Err(invalid());
    }
    // The link reached the inbox, so the address is proven.
    sqlx::query(
        "UPDATE accounts
         SET password_hash = $2, email_verified_at = coalesce(email_verified_at, now())
         WHERE id = $1",
    )
    .bind(account_id)
    .bind(&password_hash)
    .execute(&mut *tx)
    .await?;
    sqlx::query("DELETE FROM sessions WHERE account_id = $1")
        .bind(account_id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    // Signed out everywhere means the game too.
    if let Some(rt) = &state.realtime {
        rt.kick_account(account_id, KickCode::SessionExpired);
    }
    tracing::info!(account_id, "password reset; all sessions revoked");
    Ok(Json(json!({})))
}
