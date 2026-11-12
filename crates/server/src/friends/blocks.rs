//! Blocks: blocking someone removes any friendship and pending requests
//! between the two, and stops their requests, whispers, chat (to the
//! blocker) and dorm invitations. Nothing tells them they are blocked.

use axum::Json;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use chrono::{DateTime, Utc};
use protocol::FriendEvent;
use serde::{Deserialize, Serialize};

use super::{announce_unfriended, lock_pair, target_by_name, unfriend};
use crate::AppState;
use crate::characters::{ApiPath, Page, PageQuery, Player, Writer};
use crate::http::{ApiError, ApiJson, ApiQuery};

/// A block in the API: `{ character_id, name, created_at }`.
#[derive(Debug, Clone, Serialize, sqlx::FromRow)]
pub struct BlockView {
    pub character_id: i64,
    pub name: String,
    pub created_at: DateTime<Utc>,
}

/// `GET /api/blocks?limit=&after=`: newest first. Blocks have no id of their
/// own, so the cursor is `(created_at in µs, character_id)`.
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<BlockView>>, ApiError> {
    let after: Option<(i64, i64)> = page.after()?;
    let after = match after {
        None => None,
        Some((micros, id)) => {
            let at = DateTime::<Utc>::from_timestamp_micros(micros).ok_or_else(|| {
                ApiError::bad_request("invalid_cursor", "That page cursor is not valid.")
            })?;
            Some((at, id))
        }
    };
    let (after_at, after_id) = after.unzip();
    let rows: Vec<BlockView> = sqlx::query_as(
        "SELECT c.id AS character_id, c.name, b.created_at
         FROM blocks b JOIN characters c ON c.id = b.blocked_id
         WHERE b.blocker_id = $1
           AND ($2::timestamptz IS NULL OR (b.created_at, b.blocked_id) < ($2, $3))
         ORDER BY b.created_at DESC, b.blocked_id DESC
         LIMIT $4",
    )
    .bind(player.character.id)
    .bind(after_at)
    .bind(after_id)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    let page = Page::from_rows(rows, page.limit()?, |b| {
        (b.created_at.timestamp_micros(), b.character_id)
    });
    Ok(Json(page))
}

#[derive(Debug, Deserialize)]
pub struct BlockRequest {
    pub name: String,
}

/// `POST /api/blocks { name }` → `201 Block` (`200` if already blocked).
pub async fn block(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(req): ApiJson<BlockRequest>,
) -> Result<Response, ApiError> {
    let me = player.character;
    let target = target_by_name(&state, &me, &req.name).await?;
    let mut tx = state.pool.begin().await?;
    lock_pair(&mut tx, me.id, target.id).await?;
    let inserted: Option<DateTime<Utc>> = sqlx::query_scalar(
        "INSERT INTO blocks (blocker_id, blocked_id) VALUES ($1, $2)
         ON CONFLICT DO NOTHING RETURNING created_at",
    )
    .bind(me.id)
    .bind(target.id)
    .fetch_optional(&mut *tx)
    .await?;
    let (status, created_at) = match inserted {
        Some(at) => (StatusCode::CREATED, at),
        None => {
            let at = sqlx::query_scalar(
                "SELECT created_at FROM blocks WHERE blocker_id = $1 AND blocked_id = $2",
            )
            .bind(me.id)
            .bind(target.id)
            .fetch_one(&mut *tx)
            .await?;
            (StatusCode::OK, at)
        }
    };
    let were_friends = unfriend(&mut tx, me.id, target.id).await?;
    let closed: Vec<i64> = sqlx::query_scalar(
        "DELETE FROM friend_requests
         WHERE (from_id = $1 AND to_id = $2) OR (from_id = $2 AND to_id = $1)
         RETURNING id",
    )
    .bind(me.id)
    .bind(target.id)
    .fetch_all(&mut *tx)
    .await?;
    tx.commit().await?;

    if were_friends {
        announce_unfriended(&state, me.id, target.id);
    }
    if let Some(world) = state.world() {
        for request_id in closed {
            world.friend_event(target.id, FriendEvent::RequestClosed { request_id });
        }
        // Blocks changed for both, friends or not (chat, whispers, invites).
        world.friends_changed(me.id);
        world.friends_changed(target.id);
    }
    let view = BlockView {
        character_id: target.id,
        name: target.name,
        created_at,
    };
    Ok((status, Json(view)).into_response())
}

/// `DELETE /api/blocks/{character_id}` → `204`.
pub async fn unblock(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(blocked_id): ApiPath<i64>,
) -> Result<StatusCode, ApiError> {
    let me = player.character.id;
    let res = sqlx::query("DELETE FROM blocks WHERE blocker_id = $1 AND blocked_id = $2")
        .bind(me)
        .bind(blocked_id)
        .execute(&state.pool)
        .await?;
    if res.rows_affected() == 0 {
        return Err(ApiError::not_found(
            "not_blocked",
            "You have not blocked them.",
        ));
    }
    if let Some(world) = state.world() {
        world.friends_changed(me);
        world.friends_changed(blocked_id);
    }
    Ok(StatusCode::NO_CONTENT)
}
