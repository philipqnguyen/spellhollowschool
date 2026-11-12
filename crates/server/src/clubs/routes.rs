//! `GET /api/clubs` → `{ clubs: [{ name, tag, members }] }`: the school's
//! register of clubs, the largest first (then by name), at most
//! [`LIST_MAX`]. Signed in; shares the players list's read budget. What the
//! clubs secretary reads out ("Tell me about the clubs"). Every club action
//! itself goes over the game connection (`protocol::club`).

use axum::extract::State;
use axum::{Json, Router};
use serde::Serialize;

use super::store::{self, Listing};
use crate::AppState;
use crate::characters::{Reader, read_limited};
use crate::http::ApiError;

/// Clubs on one register page.
pub const LIST_MAX: i64 = 100;

#[derive(Debug, Serialize)]
pub struct Register {
    pub clubs: Vec<Listing>,
}

pub async fn list(
    State(state): State<AppState>,
    Reader(_player): Reader,
) -> Result<Json<Register>, ApiError> {
    Ok(Json(Register {
        clubs: store::list(&state.pool, LIST_MAX).await?,
    }))
}

/// This module's routes, merged under `/api` by `http::router`.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    read_limited(
        Router::new().route("/clubs", get(list)),
        crate::players::PLAYERS_RATE_LIMIT,
    )
}
