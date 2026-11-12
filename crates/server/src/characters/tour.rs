//! The first-day tour's progress, kept on the
//! server so it follows the character to another device:
//! `characters.tour_step`.
//!
//! - `0`: not toured yet: the client runs the fixed tour (the dorm, the
//!   common room's notice board, the Entrance Hall) on the next login.
//! - `1`: toured or skipped: the one-time "class starting, follow the
//!   trail" prompt is still due.
//! - `2`: done: the prompt was shown (followed or ignored), never again.
//!
//! `GET /api/characters/me/tour` → `{ step }`; `POST /api/characters/me/tour
//! { step }` → `{ step }`: the step only rises (a lower one answers the
//! current step unchanged), so two devices never undo each other.

use axum::Json;
use axum::extract::State;
use serde::{Deserialize, Serialize};

use super::{Player, Writer};
use crate::AppState;
use crate::http::{ApiError, ApiJson};

/// The last step.
pub const TOUR_DONE: u8 = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct TourView {
    pub step: u8,
}

fn step_of(v: i16) -> u8 {
    u8::try_from(v).unwrap_or(0).min(TOUR_DONE)
}

/// `GET /api/characters/me/tour`.
pub async fn get_tour(
    State(state): State<AppState>,
    player: Player,
) -> Result<Json<TourView>, ApiError> {
    let step: i16 = sqlx::query_scalar("SELECT tour_step FROM characters WHERE id = $1")
        .bind(player.character.id)
        .fetch_one(&state.pool)
        .await?;
    Ok(Json(TourView {
        step: step_of(step),
    }))
}

/// `POST /api/characters/me/tour { step }` (0-2; `400 invalid_step` else).
pub async fn post_tour(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(req): ApiJson<TourView>,
) -> Result<Json<TourView>, ApiError> {
    if req.step > TOUR_DONE {
        return Err(ApiError::bad_request(
            "invalid_step",
            format!("The tour's step is 0 to {TOUR_DONE}."),
        ));
    }
    let step: i16 = sqlx::query_scalar(
        "UPDATE characters SET tour_step = GREATEST(tour_step, $2) WHERE id = $1
         RETURNING tour_step",
    )
    .bind(player.character.id)
    .bind(i16::from(req.step))
    .fetch_one(&state.pool)
    .await?;
    Ok(Json(TourView {
        step: step_of(step),
    }))
}
