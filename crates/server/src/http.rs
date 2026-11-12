//! The HTTP router, the JSON error type and the small endpoints (health, metrics).
//!
//! Every error response is `{ "error": "<code>", "message": "<text>" }` with a
//! 4xx/5xx status (docs/architecture.md, HTTP API); a 429 from a per-account
//! limit adds `retry_after` (seconds, also the `Retry-After` header). Handlers return
//! `Result<_, ApiError>` and extract bodies with [`ApiJson`] so that malformed
//! JSON also gets that shape.

use std::borrow::Cow;
use std::time::{Duration, Instant};

use axum::extract::rejection::{JsonRejection, QueryRejection};
use axum::extract::{FromRequest, FromRequestParts, Request, State};
use axum::http::{StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Serialize;
use serde_json::json;
use tower_http::trace::TraceLayer;

use crate::static_files::StaticFiles;
use crate::{
    AppState, auth, catalog, characters, classes, crafting, dev, dueling, effects, exchange, finds,
    friends, house_cup, house_points, inventory, mail, outfits, players, potions, progression,
    rate_limit, realtime, shop, skills, spells, trade, wallet,
};

/// An error response. `code` is a stable machine-readable identifier the client
/// switches on; `message` is human-readable and may change.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApiError {
    pub status: StatusCode,
    pub code: &'static str,
    pub message: Cow<'static, str>,
    /// A 429's wait: sent as `retry_after` (whole seconds) in the body and as
    /// the `Retry-After` header.
    pub retry_after: Option<Duration>,
}

impl ApiError {
    pub fn new(
        status: StatusCode,
        code: &'static str,
        message: impl Into<Cow<'static, str>>,
    ) -> Self {
        Self {
            status,
            code,
            message: message.into(),
            retry_after: None,
        }
    }

    /// `429` with how long to wait (`retry_after` seconds, at least one).
    pub fn too_many(
        code: &'static str,
        message: impl Into<Cow<'static, str>>,
        wait: Duration,
    ) -> Self {
        Self {
            retry_after: Some(wait),
            ..Self::new(StatusCode::TOO_MANY_REQUESTS, code, message)
        }
    }

    pub fn bad_request(code: &'static str, message: impl Into<Cow<'static, str>>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, code, message)
    }

    pub fn unauthorized() -> Self {
        Self::new(
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "Sign in to continue.",
        )
    }

    pub fn not_found(code: &'static str, message: impl Into<Cow<'static, str>>) -> Self {
        Self::new(StatusCode::NOT_FOUND, code, message)
    }

    pub fn conflict(code: &'static str, message: impl Into<Cow<'static, str>>) -> Self {
        Self::new(StatusCode::CONFLICT, code, message)
    }

    pub fn forbidden(code: &'static str, message: impl Into<Cow<'static, str>>) -> Self {
        Self::new(StatusCode::FORBIDDEN, code, message)
    }

    /// `501 not_implemented`: a documented route whose handler is not
    /// implemented yet.
    pub fn not_implemented() -> Self {
        Self::new(
            StatusCode::NOT_IMPLEMENTED,
            "not_implemented",
            "This part of the game is not built yet.",
        )
    }

    /// A 500. The cause is logged here and never sent to the client.
    pub fn internal(cause: impl std::fmt::Display) -> Self {
        tracing::error!(error = %cause, "internal error");
        Self::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "internal",
            "Something went wrong on our side. Please try again.",
        )
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let mut body = json!({ "error": self.code, "message": self.message });
        let Some(wait) = self.retry_after else {
            return (self.status, Json(body)).into_response();
        };
        // Rounded up, so waiting this long is always enough.
        let secs = wait.as_secs() + u64::from(wait.subsec_nanos() > 0);
        let secs = secs.max(1);
        body["retry_after"] = secs.into();
        (
            self.status,
            [(header::RETRY_AFTER, secs.to_string())],
            Json(body),
        )
            .into_response()
    }
}

impl From<sqlx::Error> for ApiError {
    fn from(e: sqlx::Error) -> Self {
        Self::internal(e)
    }
}

impl From<JsonRejection> for ApiError {
    fn from(r: JsonRejection) -> Self {
        let status = match r.status() {
            StatusCode::UNSUPPORTED_MEDIA_TYPE => StatusCode::UNSUPPORTED_MEDIA_TYPE,
            _ => StatusCode::BAD_REQUEST,
        };
        Self::new(status, "invalid_body", r.body_text())
    }
}

/// `axum::Json`, but rejections become [`ApiError`]s.
#[derive(Debug, FromRequest)]
#[from_request(via(axum::Json), rejection(ApiError))]
pub struct ApiJson<T>(pub T);

impl<T: Serialize> IntoResponse for ApiJson<T> {
    fn into_response(self) -> Response {
        Json(self.0).into_response()
    }
}

impl From<QueryRejection> for ApiError {
    fn from(r: QueryRejection) -> Self {
        Self::bad_request("invalid_query", r.body_text())
    }
}

/// `axum::extract::Query`, but rejections become [`ApiError`]s.
#[derive(Debug, FromRequestParts)]
#[from_request(via(axum::extract::Query), rejection(ApiError))]
pub struct ApiQuery<T>(pub T);

/// Builds the full application router. `GET /ws` (the game's WebSocket) sits
/// next to `/metrics`, outside `/api` and its rate-limited auth group.
pub fn router(state: AppState) -> Router {
    // Unauthenticated auth endpoints share one per-IP limiter.
    let auth_limited = Router::new()
        .route("/auth/register", post(auth::register))
        .route("/auth/verify", get(auth::verify_link).post(auth::verify))
        .route("/auth/login", post(auth::login))
        .route("/auth/password-reset", post(auth::reset::request))
        .route("/auth/password-reset/confirm", post(auth::reset::confirm))
        .route_layer(middleware::from_fn_with_state(
            state.clone(),
            rate_limit::limit_auth,
        ));

    // Each module brings its own routes, so the data side's modules
    // (characters, catalog, inventory, outfits, friends) grow without
    // touching this file.
    let mut api = Router::new()
        .merge(auth_limited)
        .route("/auth/logout", post(auth::logout))
        .merge(characters::routes())
        .merge(catalog::routes())
        .merge(inventory::routes())
        .merge(outfits::routes())
        .merge(friends::routes())
        // The economy modules...
        .merge(wallet::routes())
        .merge(mail::routes())
        .merge(shop::routes())
        .merge(trade::routes())
        .merge(exchange::routes())
        // ...and the crafting ones.
        .merge(crafting::routes())
        .merge(skills::routes())
        .merge(potions::routes())
        .merge(finds::routes())
        .merge(effects::routes())
        // The classes and dueling modules and the shared house
        // points.
        .merge(classes::routes())
        .merge(progression::routes())
        .merge(spells::routes())
        .merge(dueling::routes())
        .merge(house_points::routes())
        .merge(house_cup::routes())
        // The map.
        .merge(crate::map::routes())
        // The players list and card.
        .merge(players::routes())
        // The clubs' register.
        .merge(crate::clubs::routes::routes())
        .route("/health", get(health));
    if state.config.dev {
        api = api.merge(dev::routes());
    }

    let app = Router::new()
        // Its own fallback, so an unknown /api path stays a JSON 404 when the
        // client's files are served below.
        .nest("/api", api.fallback(not_found))
        .route("/metrics", get(metrics))
        .route("/ws", get(realtime::gateway::ws_handler));
    // The built client (`SHS_STATIC_DIR`): `/` is `index.html`, every other
    // path a file or the JSON 404, each with its caching rule
    // (static_files.rs).
    let app = match &state.config.static_dir {
        Some(dir) => {
            let files = StaticFiles::new(dir.clone(), state.config.assets_cdn_url.clone());
            app.fallback(move |req: Request| files.clone().serve(req))
        }
        None => app.fallback(not_found),
    };
    app.layer(middleware::from_fn_with_state(
            state.clone(),
            record_metrics,
        ))
        // Spans record the path only: query strings can carry secrets (verify tokens).
        .layer(
            TraceLayer::new_for_http().make_span_with(|req: &Request| {
                tracing::debug_span!("request", method = %req.method(), path = %req.uri().path())
            }),
        )
        .with_state(state)
}

/// `200 { status: "ok" }` when the database answers, `503` otherwise.
async fn health(State(state): State<AppState>) -> Result<Json<serde_json::Value>, ApiError> {
    let ping = sqlx::query("SELECT 1").execute(&state.pool);
    match tokio::time::timeout(Duration::from_secs(2), ping).await {
        Ok(Ok(_)) => Ok(Json(json!({ "status": "ok" }))),
        Ok(Err(e)) => {
            tracing::warn!(error = %e, "health check: database error");
            Err(unavailable())
        }
        Err(_) => Err(unavailable()),
    }
}

fn unavailable() -> ApiError {
    ApiError::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "unavailable",
        "The database is not responding.",
    )
}

async fn metrics(State(state): State<AppState>) -> Response {
    (
        [(
            header::CONTENT_TYPE,
            "text/plain; version=0.0.4; charset=utf-8",
        )],
        state.metrics.registry.render(),
    )
        .into_response()
}

pub(crate) async fn not_found() -> ApiError {
    ApiError::not_found("not_found", "No such endpoint.")
}

/// The handler of a route that is declared but not implemented yet: `501 not_implemented` (the route, its auth and rate limit exist).
pub async fn stub() -> ApiError {
    ApiError::not_implemented()
}

async fn record_metrics(State(state): State<AppState>, req: Request, next: Next) -> Response {
    let start = Instant::now();
    let res = next.run(req).await;
    state
        .metrics
        .record_http(res.status().as_u16(), start.elapsed());
    res
}
