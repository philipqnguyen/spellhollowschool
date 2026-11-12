//! Shared harness for the integration tests. They run against a real Postgres:
//! `DATABASE_URL` (default `postgres://localhost/shs_test`; create it with
//! `createdb shs_test`). Tests share the database, so every test uses unique
//! emails and names instead of truncating tables.

#![allow(dead_code)] // Each test binary uses a different subset.

use std::net::SocketAddr;
use std::sync::{Arc, Mutex};

use axum::Router;
use axum::body::Body;
use axum::extract::connect_info::MockConnectInfo;
use axum::http::{Method, Request, StatusCode, header};
use rand::Rng;
use serde_json::{Value, json};
use server::email::{Email, MailFuture, Mailer};
use server::{AppState, Config, db, http};
use tower::ServiceExt;

pub const PASSWORD: &str = "correct horse battery";

/// Records emails instead of sending them.
#[derive(Default)]
pub struct CaptureMailer {
    pub sent: Mutex<Vec<Email>>,
}

impl Mailer for CaptureMailer {
    fn send(&self, email: Email) -> MailFuture<'_> {
        self.sent.lock().unwrap().push(email);
        Box::pin(async { Ok(()) })
    }
}

pub struct TestApp {
    pub state: AppState,
    pub mailer: Arc<CaptureMailer>,
    router: Router,
}

pub struct Response {
    pub status: StatusCode,
    pub headers: axum::http::HeaderMap,
    pub body: Value,
}

impl TestApp {
    /// A production-like app (`SHS_DEV` unset).
    pub async fn new() -> Self {
        Self::with_config(|_| {}).await
    }

    pub async fn dev() -> Self {
        Self::with_config(|c| c.dev = true).await
    }

    pub async fn with_config(tweak: impl FnOnce(&mut Config)) -> Self {
        Self::with_pool(4, tweak).await
    }

    /// [`TestApp::with_config`] with `connections` database connections
    /// (stress tests race more requests than four connections carry).
    pub async fn with_pool(connections: u32, tweak: impl FnOnce(&mut Config)) -> Self {
        let mut config = Config {
            database_url: std::env::var("DATABASE_URL")
                .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
            ..Config::default()
        };
        tweak(&mut config);
        let pool = db::connect_with(&config.database_url, connections)
            .await
            .expect("test database reachable (createdb shs_test)");
        let mailer = Arc::new(CaptureMailer::default());
        let state = AppState::new(pool, config, mailer.clone());
        let router = http::router(state.clone()).layer(MockConnectInfo(SocketAddr::from((
            [203, 0, 113, 7],
            50_000,
        ))));
        Self {
            state,
            mailer,
            router,
        }
    }

    pub async fn request(
        &self,
        method: Method,
        path: &str,
        body: Option<Value>,
        token: Option<&str>,
    ) -> Response {
        let mut req = Request::builder().method(method).uri(path);
        if let Some(t) = token {
            req = req.header(header::AUTHORIZATION, format!("Bearer {t}"));
        }
        let req = match body {
            Some(b) => req
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(b.to_string())),
            None => req.body(Body::empty()),
        }
        .unwrap();
        self.send(req).await
    }

    pub async fn send(&self, req: Request<Body>) -> Response {
        let res = self.router.clone().oneshot(req).await.unwrap();
        let status = res.status();
        let headers = res.headers().clone();
        let bytes = axum::body::to_bytes(res.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = if bytes.is_empty() {
            Value::Null
        } else {
            serde_json::from_slice(&bytes)
                .unwrap_or_else(|_| Value::String(String::from_utf8_lossy(&bytes).into()))
        };
        Response {
            status,
            headers,
            body,
        }
    }

    pub async fn get(&self, path: &str, token: Option<&str>) -> Response {
        self.request(Method::GET, path, None, token).await
    }

    pub async fn post(&self, path: &str, body: Value, token: Option<&str>) -> Response {
        self.request(Method::POST, path, Some(body), token).await
    }

    pub async fn register(&self, email: &str) -> Response {
        self.post(
            "/api/auth/register",
            json!({ "email": email, "password": PASSWORD, "confirm_adult": true }),
            None,
        )
        .await
    }

    /// The verification token from the last email sent to `email`.
    pub fn verification_token(&self, email: &str) -> String {
        let sent = self.mailer.sent.lock().unwrap();
        let mail = sent
            .iter()
            .rev()
            .find(|m| m.to == email.to_lowercase())
            .expect("verification email sent");
        token_from_link(&mail.text)
    }

    /// Follows a verification link: `POST /api/auth/verify` with its token.
    pub async fn verify(&self, token: &str) -> Response {
        self.post("/api/auth/verify", json!({ "token": token }), None)
            .await
    }

    pub async fn login(&self, email: &str, password: &str) -> Response {
        self.post(
            "/api/auth/login",
            json!({ "email": email, "password": password }),
            None,
        )
        .await
    }

    /// Registers a fresh account and follows its verification link, which
    /// signs in; returns (email, session token).
    pub async fn signed_in(&self) -> (String, String) {
        let email = unique_email();
        assert_eq!(self.register(&email).await.status, StatusCode::CREATED);
        let token = self.verification_token(&email);
        let res = self.verify(&token).await;
        assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
        (email, res.body["token"].as_str().unwrap().to_owned())
    }
}

/// A player made by [`TestApp::player`]: a session and a character.
#[derive(Debug, Clone)]
pub struct TestPlayer {
    pub account_id: i64,
    pub token: String,
    /// The character's id and name as `POST /api/characters` returned them.
    pub id: i64,
    pub name: String,
    pub house: String,
}

impl TestPlayer {
    pub fn token(&self) -> Option<&str> {
        Some(&self.token)
    }
}

impl TestApp {
    /// Requests against an existing state (one with the world running, say),
    /// through a fresh router. Emails are not captured.
    pub fn from_state(state: AppState) -> Self {
        let router = http::router(state.clone()).layer(MockConnectInfo(SocketAddr::from((
            [203, 0, 113, 7],
            50_000,
        ))));
        Self {
            state,
            mailer: Arc::new(CaptureMailer::default()),
            router,
        }
    }

    pub async fn delete(&self, path: &str, token: Option<&str>) -> Response {
        self.request(Method::DELETE, path, None, token).await
    }

    pub async fn patch(&self, path: &str, body: Value, token: Option<&str>) -> Response {
        self.request(Method::PATCH, path, Some(body), token).await
    }

    /// A verified account with a session, made directly in the database:
    /// no password hashing and no auth rate limit, for tests that need many
    /// players. Returns (account id, token).
    pub async fn account(&self) -> (i64, String) {
        let account_id: i64 = sqlx::query_scalar(
            "INSERT INTO accounts (email, password_hash, adult_attested_at, email_verified_at)
             VALUES ($1, '!', now(), now()) RETURNING id",
        )
        .bind(unique_email())
        .fetch_one(&self.state.pool)
        .await
        .unwrap();
        let (token, _) = server::auth::create_session(
            &self.state.pool,
            account_id,
            std::time::Duration::from_secs(3600),
        )
        .await
        .unwrap();
        (account_id, token)
    }

    /// A fresh account with a character of `house` (`"Roarwing"`, ...)
    /// created through `POST /api/characters` with the default look.
    pub async fn player(&self, house: &str) -> TestPlayer {
        self.player_named(house, &unique_name()).await
    }

    /// Makes every session of `account_id` play character `id`, straight in
    /// the database (no starter grant on the way, unlike [`TestApp::select`]).
    pub async fn select_directly(&self, account_id: i64, id: i64) {
        sqlx::query("UPDATE sessions SET character_id = $2 WHERE account_id = $1")
            .bind(account_id)
            .bind(id)
            .execute(&self.state.pool)
            .await
            .unwrap();
    }

    /// `POST /api/characters/{id}/select`: the session plays character `id`.
    pub async fn select(&self, token: &str, id: i64) -> Response {
        self.post(
            &format!("/api/characters/{id}/select"),
            json!({}),
            Some(token),
        )
        .await
    }

    /// `DELETE /api/characters/{id} { confirm_name }`.
    pub async fn delete_character(&self, token: &str, id: i64, confirm_name: &str) -> Response {
        self.request(
            Method::DELETE,
            &format!("/api/characters/{id}"),
            Some(json!({ "confirm_name": confirm_name })),
            Some(token),
        )
        .await
    }

    pub async fn player_named(&self, house: &str, name: &str) -> TestPlayer {
        let (account_id, token) = self.account().await;
        let res = self
            .post(
                "/api/characters",
                json!({ "name": name, "house": house }),
                Some(&token),
            )
            .await;
        assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
        TestPlayer {
            account_id,
            token,
            id: res.body["id"].as_i64().unwrap(),
            name: res.body["name"].as_str().unwrap().to_owned(),
            house: house.to_owned(),
        }
    }
}

/// The token from a verification link (`…/?verify=<token>`).
pub fn token_from_link(text: &str) -> String {
    let start = text.find("verify=").expect("link has a verify token") + "verify=".len();
    text[start..]
        .chars()
        .take_while(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .collect()
}

fn random_letters(n: usize) -> String {
    let mut rng = rand::thread_rng();
    (0..n).map(|_| rng.gen_range(b'a'..=b'z') as char).collect()
}

pub fn unique_email() -> String {
    format!("test-{}@example.org", random_letters(16))
}

/// A unique valid character name (letters only).
pub fn unique_name() -> String {
    format!("Test{}", random_letters(12))
}
