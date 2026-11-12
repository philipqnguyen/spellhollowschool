//! Per-account limits on the auth endpoints: the emails an account is sent
//! on request (password reset links and re-sent verification links, one
//! count) and its wrong passwords, each a fixed window that clears
//! completely once it has passed. The limits are the defaults; the tests let
//! a window pass by moving the limiter's clock (`WindowLimiter::advance`),
//! and `rate_limit`'s unit tests cover the arithmetic with an injected
//! `Instant`.

mod common;

use std::time::Duration;

use axum::http::{StatusCode, header};
use common::{PASSWORD, TestApp, unique_email};
use serde_json::json;
use server::rate_limit::{FixedWindow, RateLimit};

const QUARTER_HOUR: Duration = Duration::from_secs(15 * 60);

/// The default per-account limits; the per-IP limit out of the way (every
/// test request comes from the same address).
async fn app() -> TestApp {
    TestApp::with_config(|c| {
        c.auth_rate_limit = RateLimit {
            burst: 1000,
            per: Duration::from_millis(1),
        };
    })
    .await
}

fn emails_to(app: &TestApp, email: &str) -> usize {
    let to = email.to_lowercase();
    app.mailer
        .sent
        .lock()
        .unwrap()
        .iter()
        .filter(|m| m.to == to)
        .count()
}

async fn request_reset(app: &TestApp, email: &str) -> common::Response {
    app.post("/api/auth/password-reset", json!({ "email": email }), None)
        .await
}

fn last_reset_token(app: &TestApp, email: &str) -> String {
    let sent = app.mailer.sent.lock().unwrap();
    let mail = sent
        .iter()
        .rev()
        .find(|m| m.to == email.to_lowercase())
        .unwrap();
    let start = mail.text.find("reset=").expect("a reset link") + "reset=".len();
    mail.text[start..]
        .chars()
        .take_while(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .collect()
}

/// The 429's `retry_after` (body) and `Retry-After` (header), which agree.
fn retry_after(res: &common::Response) -> u64 {
    let body = res.body["retry_after"].as_u64().expect("retry_after");
    let header: u64 = res.headers[header::RETRY_AFTER]
        .to_str()
        .unwrap()
        .parse()
        .unwrap();
    assert_eq!(body, header);
    body
}

/// Lets the email window pass (`by` after it opened, roughly).
fn emails_later(app: &TestApp, by: Duration) {
    app.state.account_limits.emails.advance(by);
}

fn logins_later(app: &TestApp, by: Duration) {
    app.state.account_limits.login_failures.advance(by);
}

#[tokio::test]
async fn reset_emails_stop_after_three_and_come_back_after_the_window() {
    let app = app().await;
    let (email, _) = app.signed_in().await;
    let before = emails_to(&app, &email);

    for _ in 0..3 {
        assert_eq!(request_reset(&app, &email).await.status, StatusCode::OK);
    }
    assert_eq!(emails_to(&app, &email), before + 3);

    let res = request_reset(&app, &email).await;
    assert_eq!(res.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(res.body["error"], "too_many_emails");
    assert_eq!(
        res.body["message"],
        "Too many emails sent. Try again in 15 minutes."
    );
    assert!((899..=900).contains(&retry_after(&res)));
    assert_eq!(emails_to(&app, &email), before + 3, "nothing sent");
    // Refusals are not counted, and the last link sent still works.
    assert_eq!(
        request_reset(&app, &email).await.status,
        StatusCode::TOO_MANY_REQUESTS
    );
    let token = last_reset_token(&app, &email);
    let res = app
        .post(
            "/api/auth/password-reset/confirm",
            json!({ "token": token, "password": "a brand new password" }),
            None,
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);

    // Still refused 90 s before the end, with the minutes left.
    emails_later(&app, QUARTER_HOUR - Duration::from_secs(90));
    let res = request_reset(&app, &email).await;
    assert_eq!(res.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(
        res.body["message"],
        "Too many emails sent. Try again in 2 minutes."
    );
    // After the window the whole allowance is back, not one email.
    emails_later(&app, Duration::from_secs(90));
    for _ in 0..3 {
        assert_eq!(request_reset(&app, &email).await.status, StatusCode::OK);
    }
    assert_eq!(
        request_reset(&app, &email).await.status,
        StatusCode::TOO_MANY_REQUESTS
    );
}

#[tokio::test]
async fn verification_resends_share_the_email_allowance() {
    let app = app().await;
    let email = unique_email();
    assert_eq!(app.register(&email).await.status, StatusCode::CREATED);
    // Registering's own email is not counted.
    assert_eq!(emails_to(&app, &email), 1);

    for _ in 0..2 {
        let res = app.login(&email, PASSWORD).await;
        assert_eq!(res.status, StatusCode::FORBIDDEN);
        assert_eq!(res.body["error"], "unverified");
    }
    assert_eq!(request_reset(&app, &email).await.status, StatusCode::OK);
    assert_eq!(emails_to(&app, &email), 4);

    // The fourth of either kind is refused, and sends nothing.
    let res = app.login(&email, PASSWORD).await;
    assert_eq!(res.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(res.body["error"], "too_many_emails");
    retry_after(&res);
    assert_eq!(
        request_reset(&app, &email).await.status,
        StatusCode::TOO_MANY_REQUESTS
    );
    assert_eq!(emails_to(&app, &email), 4);
    // A wrong password is still just wrong: no email is at stake.
    assert_eq!(
        app.login(&email, "not the password").await.status,
        StatusCode::UNAUTHORIZED
    );

    // Other accounts are untouched.
    let other = unique_email();
    app.register(&other).await;
    assert_eq!(
        app.login(&other, PASSWORD).await.status,
        StatusCode::FORBIDDEN
    );

    emails_later(&app, QUARTER_HOUR);
    let res = app.login(&email, PASSWORD).await;
    assert_eq!(res.status, StatusCode::FORBIDDEN);
    assert_eq!(emails_to(&app, &email), 5);
}

#[tokio::test]
async fn ten_wrong_passwords_lock_the_account_until_the_window_ends() {
    let app = app().await;
    let (email, _) = app.signed_in().await;
    for _ in 0..10 {
        assert_eq!(
            app.login(&email, "not the password").await.status,
            StatusCode::UNAUTHORIZED
        );
    }
    // Even the right password is refused now.
    let res = app.login(&email, PASSWORD).await;
    assert_eq!(res.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(res.body["error"], "too_many_attempts");
    assert_eq!(
        res.body["message"],
        "Too many sign-in attempts. Try again in 15 minutes."
    );
    assert!((800..=900).contains(&retry_after(&res)));
    assert!(res.body.get("token").is_none());

    // Other accounts sign in as usual.
    let (other, _) = app.signed_in().await;
    assert_eq!(app.login(&other, PASSWORD).await.status, StatusCode::OK);

    // The window's end clears the count completely.
    logins_later(&app, QUARTER_HOUR);
    for _ in 0..9 {
        assert_eq!(
            app.login(&email, "not the password").await.status,
            StatusCode::UNAUTHORIZED
        );
    }
    let res = app.login(&email, PASSWORD).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
}

#[tokio::test]
async fn signing_in_clears_the_wrong_passwords() {
    let app = app().await;
    let (email, _) = app.signed_in().await;
    for _ in 0..9 {
        app.login(&email, "not the password").await;
    }
    assert_eq!(app.login(&email, PASSWORD).await.status, StatusCode::OK);
    // A fresh count: ten more wrong ones before the lock.
    for _ in 0..10 {
        assert_eq!(
            app.login(&email, "not the password").await.status,
            StatusCode::UNAUTHORIZED
        );
    }
    assert_eq!(
        app.login(&email, PASSWORD).await.status,
        StatusCode::TOO_MANY_REQUESTS
    );
}

#[tokio::test]
async fn parallel_guesses_cannot_slip_past_the_limit() {
    let app = app().await;
    let (email, _) = app.signed_in().await;
    let guesses = (0..25).map(|_| app.login(&email, "not the password"));
    let statuses: Vec<StatusCode> = futures_util::future::join_all(guesses)
        .await
        .into_iter()
        .map(|r| r.status)
        .collect();
    let wrong = statuses
        .iter()
        .filter(|s| **s == StatusCode::UNAUTHORIZED)
        .count();
    let refused = statuses
        .iter()
        .filter(|s| **s == StatusCode::TOO_MANY_REQUESTS)
        .count();
    assert_eq!((wrong, refused), (10, 15), "{statuses:?}");
    assert_eq!(
        app.login(&email, PASSWORD).await.status,
        StatusCode::TOO_MANY_REQUESTS
    );
}

#[tokio::test]
async fn the_limits_default_to_three_emails_and_ten_attempts_per_quarter_hour() {
    let c = server::Config::default();
    let quarter = Duration::from_secs(15 * 60);
    assert_eq!(
        c.email_limit,
        FixedWindow {
            max: 3,
            window: quarter
        }
    );
    assert_eq!(
        c.login_failure_limit,
        FixedWindow {
            max: 10,
            window: quarter
        }
    );
}
