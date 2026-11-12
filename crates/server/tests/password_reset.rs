//! Password reset: request a link, confirm with a new password (Postgres).

mod common;

use std::time::Duration;

use axum::http::StatusCode;
use common::{PASSWORD, TestApp, unique_email};
use serde_json::json;
use server::rate_limit::RateLimit;

const NEW_PASSWORD: &str = "a brand new passphrase";

/// The `reset` token from a link (`…/?reset=<token>`).
fn reset_token_from(text: &str) -> String {
    let start = text.find("reset=").expect("link has a reset token") + "reset=".len();
    text[start..]
        .chars()
        .take_while(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .collect()
}

async fn request_reset(app: &TestApp, email: &str) -> common::Response {
    app.post("/api/auth/password-reset", json!({ "email": email }), None)
        .await
}

async fn confirm(app: &TestApp, token: &str, password: &str) -> common::Response {
    app.post(
        "/api/auth/password-reset/confirm",
        json!({ "token": token, "password": password }),
        None,
    )
    .await
}

/// The reset token from the last reset email sent to `email`.
fn emailed_reset_token(app: &TestApp, email: &str) -> String {
    let sent = app.mailer.sent.lock().unwrap();
    let mail = sent
        .iter()
        .rev()
        .find(|m| m.to == email.to_lowercase() && m.subject.contains("Reset"))
        .expect("reset email sent");
    reset_token_from(&mail.text)
}

fn reset_emails_to(app: &TestApp, email: &str) -> usize {
    app.mailer
        .sent
        .lock()
        .unwrap()
        .iter()
        .filter(|m| m.to == email && m.subject.contains("Reset"))
        .count()
}

async fn sessions_of(app: &TestApp, email: &str) -> i64 {
    sqlx::query_scalar(
        "SELECT count(*) FROM sessions s JOIN accounts a ON a.id = s.account_id WHERE a.email = $1",
    )
    .bind(email)
    .fetch_one(&app.state.pool)
    .await
    .unwrap()
}

#[tokio::test]
async fn reset_sets_a_new_password_and_revokes_every_session() {
    let app = TestApp::new().await;
    let (email, first) = app.signed_in().await;
    let second = app.login(&email, PASSWORD).await.body["token"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(sessions_of(&app, &email).await, 2);

    // Case-insensitive, like sign-in.
    let res = request_reset(&app, &email.to_uppercase()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(
        res.body,
        json!({}),
        "the link only goes by email outside dev"
    );
    let token = emailed_reset_token(&app, &email);
    assert_eq!(token.len(), 43);
    // Stored only as a hash.
    let stored: Vec<u8> = sqlx::query_scalar(
        "SELECT r.token_hash FROM password_resets r JOIN accounts a ON a.id = r.account_id WHERE a.email = $1",
    )
    .bind(&email)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_ne!(stored, token.as_bytes());
    assert_eq!(stored.len(), 32);

    let res = confirm(&app, &token, NEW_PASSWORD).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body, json!({}));

    // Every session is gone...
    assert_eq!(sessions_of(&app, &email).await, 0);
    for t in [&first, &second] {
        assert!(server::validate_session(&app.state.pool, t).await.is_none());
        assert_eq!(
            app.get("/api/characters/me", Some(t)).await.status,
            StatusCode::UNAUTHORIZED
        );
    }
    // ...the old password no longer works, the new one does.
    assert_eq!(
        app.login(&email, PASSWORD).await.status,
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(app.login(&email, NEW_PASSWORD).await.status, StatusCode::OK);
}

#[tokio::test]
async fn reset_links_are_single_use() {
    let app = TestApp::new().await;
    let (email, _) = app.signed_in().await;
    request_reset(&app, &email).await;
    let token = emailed_reset_token(&app, &email);
    assert_eq!(
        confirm(&app, &token, NEW_PASSWORD).await.status,
        StatusCode::OK
    );
    let again = confirm(&app, &token, "yet another passphrase").await;
    assert_eq!(again.status, StatusCode::BAD_REQUEST);
    assert_eq!(again.body["error"], "invalid_token");
    assert_eq!(app.login(&email, NEW_PASSWORD).await.status, StatusCode::OK);
}

#[tokio::test]
async fn a_new_request_replaces_the_old_link() {
    let app = TestApp::new().await;
    let (email, _) = app.signed_in().await;
    request_reset(&app, &email).await;
    let old = emailed_reset_token(&app, &email);
    request_reset(&app, &email).await;
    let new = emailed_reset_token(&app, &email);
    assert_ne!(old, new);
    assert_eq!(reset_emails_to(&app, &email), 2);

    assert_eq!(
        confirm(&app, &old, NEW_PASSWORD).await.body["error"],
        "invalid_token"
    );
    assert_eq!(
        confirm(&app, &new, NEW_PASSWORD).await.status,
        StatusCode::OK
    );
}

#[tokio::test]
async fn expired_and_unknown_links_are_rejected() {
    let app = TestApp::with_config(|c| c.password_reset_ttl = Duration::from_secs(0)).await;
    let (email, session) = app.signed_in().await;
    request_reset(&app, &email).await;
    let token = emailed_reset_token(&app, &email);
    let res = confirm(&app, &token, NEW_PASSWORD).await;
    assert_eq!(res.status, StatusCode::BAD_REQUEST);
    assert_eq!(res.body["error"], "invalid_token");
    // Nothing changed.
    assert_eq!(app.login(&email, PASSWORD).await.status, StatusCode::OK);
    assert!(
        server::validate_session(&app.state.pool, &session)
            .await
            .is_some()
    );

    for bad in ["", "nope", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"] {
        let res = confirm(&app, bad, NEW_PASSWORD).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{bad}");
        assert_eq!(res.body["error"], "invalid_token");
    }
}

#[tokio::test]
async fn a_weak_password_keeps_the_link() {
    let app = TestApp::new().await;
    let (email, _) = app.signed_in().await;
    request_reset(&app, &email).await;
    let token = emailed_reset_token(&app, &email);
    for weak in ["short", email.as_str()] {
        let res = confirm(&app, &token, weak).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{weak}");
        assert_eq!(res.body["error"], "weak_password");
    }
    assert_eq!(
        confirm(&app, &token, NEW_PASSWORD).await.status,
        StatusCode::OK
    );
}

#[tokio::test]
async fn reset_verifies_an_unverified_account() {
    let app = TestApp::new().await;
    let email = unique_email();
    app.register(&email).await;
    assert_eq!(
        app.login(&email, PASSWORD).await.body["error"],
        "unverified"
    );

    request_reset(&app, &email).await;
    let token = emailed_reset_token(&app, &email);
    assert_eq!(
        confirm(&app, &token, NEW_PASSWORD).await.status,
        StatusCode::OK
    );
    let res = app.login(&email, NEW_PASSWORD).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
}

#[tokio::test]
async fn unknown_or_malformed_email_is_reported() {
    let app = TestApp::new().await;
    let unknown = unique_email();
    let res = request_reset(&app, &unknown).await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    assert_eq!(res.body["error"], "no_account");
    assert!(res.body["message"].is_string());
    assert_eq!(reset_emails_to(&app, &unknown), 0);

    let res = request_reset(&app, "not-an-email").await;
    assert_eq!(res.status, StatusCode::BAD_REQUEST);
    assert_eq!(res.body["error"], "invalid_email");
}

#[tokio::test]
async fn reset_in_dev_returns_the_link() {
    let app = TestApp::dev().await;
    let (email, _) = app.signed_in().await;
    let res = request_reset(&app, &email).await;
    let url = res.body["dev_reset_url"].as_str().expect("dev_reset_url");
    assert!(url.starts_with("http://localhost:8080/?reset="), "{url}");
    assert_eq!(reset_token_from(url), emailed_reset_token(&app, &email));
}

#[tokio::test]
async fn reset_endpoints_share_the_auth_rate_limit() {
    let app = TestApp::with_config(|c| {
        c.auth_rate_limit = RateLimit {
            burst: 2,
            per: Duration::from_secs(60),
        }
    })
    .await;
    let email = unique_email();
    assert_eq!(
        request_reset(&app, &email).await.status,
        StatusCode::NOT_FOUND
    );
    let res = request_reset(&app, &email).await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    let res = request_reset(&app, &email).await;
    assert_eq!(res.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(res.body["error"], "rate_limited");
    let res = confirm(
        &app,
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        NEW_PASSWORD,
    )
    .await;
    assert_eq!(res.status, StatusCode::TOO_MANY_REQUESTS);
}

#[tokio::test]
async fn expired_resets_are_cleaned_up() {
    let app = TestApp::with_config(|c| c.password_reset_ttl = Duration::from_secs(0)).await;
    let (email, _) = app.signed_in().await;
    request_reset(&app, &email).await;
    server::db::delete_expired(&app.state.pool).await.unwrap();
    let left: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM password_resets r JOIN accounts a ON a.id = r.account_id WHERE a.email = $1",
    )
    .bind(&email)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(left, 0);
}
