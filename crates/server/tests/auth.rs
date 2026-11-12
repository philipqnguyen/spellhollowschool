//! Registration, verification, login, logout and rate limiting against Postgres.

mod common;

use std::time::Duration;

use axum::http::{StatusCode, header};
use chrono::Utc;
use common::{PASSWORD, TestApp, unique_email};
use serde_json::json;
use server::rate_limit::RateLimit;
use sha2::{Digest, Sha256};

async fn account_count(app: &TestApp, email: &str) -> i64 {
    sqlx::query_scalar("SELECT count(*) FROM accounts WHERE email = $1")
        .bind(email.to_lowercase())
        .fetch_one(&app.state.pool)
        .await
        .unwrap()
}

#[tokio::test]
async fn register_adult_creates_account_and_sends_verification() {
    let app = TestApp::new().await;
    let email = unique_email();
    let res = app
        .post(
            "/api/auth/register",
            json!({
                "email": format!("  {}  ", email.to_uppercase()),
                "password": PASSWORD,
                "confirm_adult": true,
            }),
            None,
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    // Outside dev mode the body is empty: the link only goes by email.
    assert_eq!(res.body, json!({}));

    let (stored, attested, verified): (
        String,
        chrono::DateTime<Utc>,
        Option<chrono::DateTime<Utc>>,
    ) = sqlx::query_as(
        "SELECT email, adult_attested_at, email_verified_at FROM accounts WHERE email = $1",
    )
    .bind(&email)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(stored, email, "email stored normalized");
    assert!(Utc::now() - attested < chrono::Duration::minutes(1));
    assert!(verified.is_none());

    let sent = app.mailer.sent.lock().unwrap().clone();
    let mail = sent
        .iter()
        .find(|m| m.to == email)
        .expect("verification email");
    // The link opens the client's confirmation page, not the API.
    assert!(
        mail.text.contains("http://localhost:8080/?verify="),
        "{}",
        mail.text
    );
}

#[tokio::test]
async fn register_in_dev_returns_verify_url() {
    let app = TestApp::dev().await;
    let email = unique_email();
    let res = app.register(&email).await;
    assert_eq!(res.status, StatusCode::CREATED);
    let url = res.body["dev_verify_url"].as_str().expect("dev_verify_url");
    assert!(url.starts_with("http://localhost:8080/?verify="), "{url}");
    assert_eq!(common::token_from_link(url), app.verification_token(&email));
}

#[tokio::test]
async fn register_without_adult_confirmation_is_rejected_and_nothing_is_stored() {
    let app = TestApp::new().await;
    let email = unique_email();
    let unconfirmed = json!({ "email": email, "password": PASSWORD, "confirm_adult": false });
    let missing = json!({ "email": email, "password": PASSWORD });
    for body in [unconfirmed, missing] {
        let res = app.post("/api/auth/register", body, None).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST);
        assert_eq!(res.body["error"], "adult_unconfirmed");
        assert!(res.body["message"].is_string());
    }
    assert_eq!(account_count(&app, &email).await, 0);
    assert!(
        app.mailer
            .sent
            .lock()
            .unwrap()
            .iter()
            .all(|m| m.to != email)
    );
}

#[tokio::test]
async fn accounts_store_no_age_only_the_attestation() {
    let app = TestApp::new().await;
    let columns: Vec<String> = sqlx::query_scalar(
        "SELECT column_name::text FROM information_schema.columns WHERE table_name = 'accounts'",
    )
    .fetch_all(&app.state.pool)
    .await
    .unwrap();
    assert!(columns.contains(&"adult_attested_at".to_owned()));
    assert!(
        !columns
            .iter()
            .any(|c| c.contains("birth") || c.contains("dob")),
        "{columns:?}"
    );
}

#[tokio::test]
async fn register_validates_input() {
    let app = TestApp::new().await;
    let cases = [
        (
            json!({ "email": "not-an-email", "password": PASSWORD, "confirm_adult": true }),
            "invalid_email",
        ),
        (
            json!({ "email": unique_email(), "password": "short", "confirm_adult": true }),
            "weak_password",
        ),
        (
            json!({ "email": unique_email(), "confirm_adult": true }),
            "invalid_body",
        ),
    ];
    for (body, code) in cases {
        let res = app.post("/api/auth/register", body, None).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{code}");
        assert_eq!(res.body["error"], code);
    }
}

#[tokio::test]
async fn malformed_json_gets_the_error_shape() {
    let app = TestApp::new().await;
    let req = axum::http::Request::post("/api/auth/login")
        .header(header::CONTENT_TYPE, "application/json")
        .body(axum::body::Body::from("{not json"))
        .unwrap();
    let res = app.send(req).await;
    assert_eq!(res.status, StatusCode::BAD_REQUEST);
    assert_eq!(res.body["error"], "invalid_body");
    assert!(res.body["message"].is_string());
}

#[tokio::test]
async fn existing_email_is_sent_to_sign_in() {
    let app = TestApp::new().await;
    let email = unique_email();
    assert_eq!(app.register(&email).await.status, StatusCode::CREATED);
    // Case-insensitive: the same address in capitals is the same account.
    let res = app.register(&email.to_uppercase()).await;
    assert_eq!(res.status, StatusCode::CONFLICT, "{:?}", res.body);
    assert_eq!(res.body["error"], "email_taken");
    assert!(res.body["message"].as_str().unwrap().contains("Sign in"));
    assert_eq!(account_count(&app, &email).await, 1);
    // Nothing more is emailed: only the first registration's verification.
    let sent = app.mailer.sent.lock().unwrap().clone();
    assert_eq!(sent.iter().filter(|m| m.to == email).count(), 1);
}

#[tokio::test]
async fn verify_marks_email_verified_once_and_signs_in() {
    let app = TestApp::new().await;
    let email = unique_email();
    app.register(&email).await;
    let token = app.verification_token(&email);

    let res = app.verify(&token).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let verified: Option<chrono::DateTime<Utc>> =
        sqlx::query_scalar("SELECT email_verified_at FROM accounts WHERE email = $1")
            .bind(&email)
            .fetch_one(&app.state.pool)
            .await
            .unwrap();
    assert!(verified.is_some());

    // The answer is a session, exactly like signing in.
    let session = res.body["token"].as_str().expect("session token");
    assert_eq!(session.len(), 43);
    let expires_at: chrono::DateTime<Utc> =
        res.body["expires_at"].as_str().unwrap().parse().unwrap();
    assert!(expires_at > Utc::now() + chrono::Duration::days(29));
    assert_eq!(
        app.get("/api/characters/me", Some(session)).await.status,
        StatusCode::NOT_FOUND,
        "signed in, no character yet"
    );
    assert!(
        server::validate_session(&app.state.pool, session)
            .await
            .is_some()
    );
    assert_eq!(app.state.metrics.logins_ok.get(), 1);

    // Single use: clicking the link again signs nobody in.
    let again = app.verify(&token).await;
    assert_eq!(again.status, StatusCode::BAD_REQUEST);
    assert_eq!(again.body["error"], "invalid_token");
    assert!(again.body.get("token").is_none());
    // The account stays confirmed, so signing in works.
    assert_eq!(app.login(&email, PASSWORD).await.status, StatusCode::OK);
}

#[tokio::test]
async fn verify_rejects_unknown_and_expired_tokens() {
    let app = TestApp::with_config(|c| c.verification_ttl = Duration::from_secs(0)).await;
    for body in [
        json!({}),
        json!({ "token": "nope" }),
        json!({ "token": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" }),
    ] {
        let res = app.post("/api/auth/verify", body.clone(), None).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{body}");
        assert_eq!(res.body["error"], "invalid_token");
    }

    // A zero TTL makes the freshly issued link already expired.
    let email = unique_email();
    app.register(&email).await;
    let token = app.verification_token(&email);
    let res = app.verify(&token).await;
    assert_eq!(res.body["error"], "invalid_token");
    assert!(res.body.get("token").is_none(), "no session");
    let verified: Option<chrono::DateTime<Utc>> =
        sqlx::query_scalar("SELECT email_verified_at FROM accounts WHERE email = $1")
            .bind(&email)
            .fetch_one(&app.state.pool)
            .await
            .unwrap();
    assert!(verified.is_none());
    // The expired token is used up too.
    let left: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM email_verifications v JOIN accounts a ON a.id = v.account_id
         WHERE a.email = $1",
    )
    .bind(&email)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(left, 0);
}

#[tokio::test]
async fn old_api_links_redirect_to_the_confirmation_page_without_spending_the_token() {
    let app = TestApp::new().await;
    let email = unique_email();
    app.register(&email).await;
    let token = app.verification_token(&email);

    let res = app
        .get(&format!("/api/auth/verify?token={token}"), None)
        .await;
    assert_eq!(res.status, StatusCode::SEE_OTHER);
    assert_eq!(
        res.headers[header::LOCATION],
        format!("http://localhost:8080/?verify={token}").as_str()
    );
    // The page then spends it.
    assert_eq!(app.verify(&token).await.status, StatusCode::OK);

    // A malformed token still reaches the page, which explains it.
    for path in ["/api/auth/verify", "/api/auth/verify?token=a%22b%3Cc"] {
        let res = app.get(path, None).await;
        assert_eq!(res.status, StatusCode::SEE_OTHER, "{path}");
        assert_eq!(
            res.headers[header::LOCATION],
            "http://localhost:8080/?verify=invalid"
        );
    }
}

#[tokio::test]
async fn login_requires_verified_email_and_sends_a_new_link() {
    let app = TestApp::new().await;
    let email = unique_email();
    app.register(&email).await;
    let first = app.verification_token(&email);
    let res = app.login(&email, PASSWORD).await;
    assert_eq!(res.status, StatusCode::FORBIDDEN);
    assert_eq!(res.body["error"], "unverified");
    assert!(res.body["message"].is_string());
    assert!(res.body.get("dev_verify_url").is_none(), "dev only");

    // The right password earns a fresh link (the first may be lost or expired)...
    let fresh = app.verification_token(&email);
    assert_ne!(fresh, first);
    assert_eq!(
        app.mailer
            .sent
            .lock()
            .unwrap()
            .iter()
            .filter(|m| m.to == email)
            .count(),
        2
    );
    // ...which confirms the email, and then signing in works.
    let res = app.verify(&fresh).await;
    assert_eq!(res.status, StatusCode::OK);
    assert_eq!(app.login(&email, PASSWORD).await.status, StatusCode::OK);
}

#[tokio::test]
async fn unverified_login_resends_even_after_the_first_link_expired() {
    let app = TestApp::with_config(|c| c.verification_ttl = Duration::from_secs(0)).await;
    let email = unique_email();
    app.register(&email).await;
    let first = app.verification_token(&email);
    let res = app.verify(&first).await;
    assert_eq!(res.body["error"], "invalid_token");
    // Signing in still offers a way forward: a new link goes out.
    assert_eq!(
        app.login(&email, PASSWORD).await.body["error"],
        "unverified"
    );
    assert_ne!(app.verification_token(&email), first);
}

#[tokio::test]
async fn unverified_login_in_dev_returns_verify_url() {
    let app = TestApp::dev().await;
    let email = unique_email();
    app.register(&email).await;
    let res = app.login(&email, PASSWORD).await;
    assert_eq!(res.status, StatusCode::FORBIDDEN);
    assert_eq!(res.body["error"], "unverified");
    let url = res.body["dev_verify_url"].as_str().expect("dev_verify_url");
    assert_eq!(common::token_from_link(url), app.verification_token(&email));
    assert!(url.starts_with("http://localhost:8080/?verify="), "{url}");
    let res = app.verify(&common::token_from_link(url)).await;
    assert_eq!(res.status, StatusCode::OK);
}

#[tokio::test]
async fn login_rejects_wrong_password_and_unknown_email() {
    let app = TestApp::new().await;
    let (email, _) = app.signed_in().await;
    for (e, p) in [
        (email.as_str(), "wrong password!"),
        ("nobody-here@example.org", PASSWORD),
        ("junk", PASSWORD),
    ] {
        let res = app.login(e, p).await;
        assert_eq!(res.status, StatusCode::UNAUTHORIZED, "{e}");
        assert_eq!(res.body["error"], "invalid_credentials");
    }
    // An unverified account with a wrong password must not reveal that it
    // exists, nor get a new link.
    let unverified = unique_email();
    app.register(&unverified).await;
    assert_eq!(
        app.login(&unverified, "wrong password!").await.body["error"],
        "invalid_credentials"
    );
    assert_eq!(
        app.mailer
            .sent
            .lock()
            .unwrap()
            .iter()
            .filter(|m| m.to == unverified)
            .count(),
        1
    );
}

#[tokio::test]
async fn login_returns_session_token_stored_only_as_hash() {
    let app = TestApp::new().await;
    let email = unique_email();
    app.register(&email).await;
    let token = app.verification_token(&email);
    app.verify(&token).await;

    let res = app.login(&email.to_uppercase(), PASSWORD).await;
    assert_eq!(res.status, StatusCode::OK);
    let token = res.body["token"].as_str().unwrap();
    assert_eq!(token.len(), 43, "32 bytes, base64url without padding");
    let expires_at: chrono::DateTime<Utc> =
        res.body["expires_at"].as_str().unwrap().parse().unwrap();
    assert!(expires_at > Utc::now() + chrono::Duration::days(29));

    let hash = Sha256::digest(token.as_bytes()).to_vec();
    let stored: i64 = sqlx::query_scalar("SELECT count(*) FROM sessions WHERE token_hash = $1")
        .bind(&hash)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    assert_eq!(stored, 1);

    let info = server::validate_session(&app.state.pool, token)
        .await
        .expect("valid session");
    assert!(info.character.is_none());
}

#[tokio::test]
async fn logout_ends_the_session() {
    let app = TestApp::new().await;
    let (_, token) = app.signed_in().await;
    assert_eq!(
        app.get("/api/characters/me", Some(&token)).await.status,
        StatusCode::NOT_FOUND
    );

    let res = app
        .request(
            axum::http::Method::POST,
            "/api/auth/logout",
            None,
            Some(&token),
        )
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    assert_eq!(
        app.get("/api/characters/me", Some(&token)).await.status,
        StatusCode::UNAUTHORIZED
    );
    assert!(
        server::validate_session(&app.state.pool, &token)
            .await
            .is_none()
    );

    // Logging out again (or without a token) is unauthorized.
    let again = app
        .request(
            axum::http::Method::POST,
            "/api/auth/logout",
            None,
            Some(&token),
        )
        .await;
    assert_eq!(again.status, StatusCode::UNAUTHORIZED);
    assert_eq!(again.body["error"], "unauthorized");
}

#[tokio::test]
async fn expired_sessions_are_rejected() {
    let app = TestApp::with_config(|c| c.session_ttl = Duration::from_secs(0)).await;
    let (_, token) = app.signed_in().await;
    assert_eq!(
        app.get("/api/characters/me", Some(&token)).await.status,
        StatusCode::UNAUTHORIZED
    );
    assert!(
        server::validate_session(&app.state.pool, &token)
            .await
            .is_none()
    );
}

#[tokio::test]
async fn auth_endpoints_are_rate_limited_per_ip() {
    let app = TestApp::with_config(|c| {
        c.auth_rate_limit = RateLimit {
            burst: 3,
            per: Duration::from_secs(60),
        }
    })
    .await;
    for _ in 0..3 {
        let res = app.login("nobody@example.org", PASSWORD).await;
        assert_eq!(res.status, StatusCode::UNAUTHORIZED);
    }
    // The bucket is shared across the auth endpoints, register included
    // (whether the email is new or already taken).
    let res = app.register(&unique_email()).await;
    assert_eq!(res.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(res.body["error"], "rate_limited");
    let retry: u64 = res.headers[header::RETRY_AFTER]
        .to_str()
        .unwrap()
        .parse()
        .unwrap();
    assert!((1..=60).contains(&retry));
    assert_eq!(app.state.metrics.rate_limited.get(), 1);

    // Non-auth endpoints are unaffected.
    assert_eq!(app.get("/api/health", None).await.status, StatusCode::OK);
}

#[tokio::test]
async fn health_metrics_and_unknown_routes() {
    let app = TestApp::new().await;
    let res = app.get("/api/health", None).await;
    assert_eq!(res.status, StatusCode::OK);
    assert_eq!(res.body, json!({ "status": "ok" }));

    let res = app.get("/api/nope", None).await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    assert_eq!(res.body["error"], "not_found");

    let res = app.get("/metrics", None).await;
    assert_eq!(res.status, StatusCode::OK);
    assert!(
        res.headers[header::CONTENT_TYPE]
            .to_str()
            .unwrap()
            .starts_with("text/plain")
    );
    let text = res.body.as_str().unwrap();
    assert!(
        text.contains("shs_http_responses_total{class=\"2xx\"} 1\n"),
        "{text}"
    );
    assert!(text.contains("shs_http_responses_total{class=\"4xx\"} 1\n"));
    assert!(text.contains("# TYPE shs_http_request_duration_seconds histogram"));
}
