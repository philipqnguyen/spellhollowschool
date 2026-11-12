//! Friend requests: send (by name), accept, decline, cancel, and the
//! incoming / outgoing lists. Only pending requests are stored; accepting or
//! declining deletes the row.

use axum::Json;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use chrono::{DateTime, Utc};
use protocol::{FRIENDS_MAX, FriendEvent, House};
use serde::{Deserialize, Serialize};
use serde_json::json;

use super::{
    Other, announce_friendship, are_friends, befriend, character_not_found, friend_count,
    lock_pair, target_by_name,
};
use crate::AppState;
use crate::characters::{ApiPath, Page, PageQuery, Player, Writer, house_from_db};
use crate::db::is_unique_violation;
use crate::http::{ApiError, ApiJson, ApiQuery};

/// A pending request in the API: `{ id, character_id, name, house,
/// created_at }`, where the character is the other party.
#[derive(Debug, Clone, Serialize)]
pub struct FriendRequestView {
    pub id: i64,
    pub character_id: i64,
    pub name: String,
    pub house: House,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, sqlx::FromRow)]
struct RequestRow {
    id: i64,
    character_id: i64,
    name: String,
    house: i16,
    created_at: DateTime<Utc>,
}

impl From<RequestRow> for FriendRequestView {
    fn from(r: RequestRow) -> Self {
        FriendRequestView {
            id: r.id,
            character_id: r.character_id,
            name: r.name,
            house: house_from_db(r.house).unwrap_or(House::Roarwing),
            created_at: r.created_at,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Direction {
    /// Requests others sent this player (the default).
    #[default]
    Incoming,
    /// Requests this player sent.
    Outgoing,
}

#[derive(Debug, Default, Deserialize)]
pub struct DirectionQuery {
    #[serde(default)]
    pub direction: Direction,
}

/// `GET /api/friends/requests?direction=incoming|outgoing&limit=&after=`:
/// newest first.
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(q): ApiQuery<DirectionQuery>,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<FriendRequestView>>, ApiError> {
    let after: Option<i64> = page.after()?;
    // `mine` is this player's side of the request, `theirs` the other party's.
    let (mine, theirs) = match q.direction {
        Direction::Incoming => ("to_id", "from_id"),
        Direction::Outgoing => ("from_id", "to_id"),
    };
    let rows: Vec<RequestRow> = sqlx::query_as(&format!(
        "SELECT r.id, c.id AS character_id, c.name, c.house, r.created_at
         FROM friend_requests r JOIN characters c ON c.id = r.{theirs}
         WHERE r.{mine} = $1 AND ($2::bigint IS NULL OR r.id < $2)
         ORDER BY r.id DESC
         LIMIT $3"
    ))
    .bind(player.character.id)
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    let page = Page::from_rows(rows, page.limit()?, |r| r.id);
    Ok(Json(page.map(FriendRequestView::from)))
}

#[derive(Debug, Deserialize)]
pub struct SendRequest {
    pub name: String,
}

/// What lies between two characters, read under [`lock_pair`].
async fn blocks_between(
    conn: &mut sqlx::PgConnection,
    me: i64,
    them: i64,
) -> Result<(bool, bool), sqlx::Error> {
    sqlx::query_as(
        "SELECT
             EXISTS (SELECT 1 FROM blocks WHERE blocker_id = $1 AND blocked_id = $2),
             EXISTS (SELECT 1 FROM blocks WHERE blocker_id = $2 AND blocked_id = $1)",
    )
    .bind(me)
    .bind(them)
    .fetch_one(conn)
    .await
}

/// `POST /api/friends/requests { name }` → `201 { request }`, or
/// `200 { friend }` when they had already asked this player (accepted at once).
pub async fn send(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiJson(req): ApiJson<SendRequest>,
) -> Result<Response, ApiError> {
    let me = player.character;
    let target = target_by_name(&state, &me, &req.name).await?;
    let mut tx = state.pool.begin().await?;
    lock_pair(&mut tx, me.id, target.id).await?;

    let (i_blocked, they_blocked) = blocks_between(&mut tx, me.id, target.id).await?;
    if i_blocked {
        return Err(ApiError::conflict(
            "blocked",
            "You blocked them. Unblock them first.",
        ));
    }
    if they_blocked {
        // Indistinguishable from an unknown name: nothing reveals a block.
        return Err(character_not_found());
    }
    if are_friends(&mut tx, me.id, target.id).await? {
        return Err(ApiError::conflict(
            "already_friends",
            "You are already friends.",
        ));
    }
    let pending: Vec<(i64, i64)> = sqlx::query_as(
        "SELECT id, from_id FROM friend_requests
         WHERE (from_id = $1 AND to_id = $2) OR (from_id = $2 AND to_id = $1)",
    )
    .bind(me.id)
    .bind(target.id)
    .fetch_all(&mut *tx)
    .await?;
    if pending.iter().any(|&(_, from)| from == target.id) {
        // They asked first: this is a yes.
        let since = befriend(&mut tx, me.id, target.id).await?;
        tx.commit().await?;
        let friend =
            announce_friendship(&state, Other::from(&me), Other::from(&target), since).await;
        return Ok((StatusCode::OK, Json(json!({ "friend": friend }))).into_response());
    }
    if !pending.is_empty() {
        return Err(request_pending());
    }
    if friend_count(&mut tx, me.id).await? >= i64::from(FRIENDS_MAX) {
        return Err(ApiError::conflict(
            "friend_limit",
            format!("You already have {FRIENDS_MAX} friends."),
        ));
    }
    let (id, created_at): (i64, DateTime<Utc>) = sqlx::query_as(
        "INSERT INTO friend_requests (from_id, to_id) VALUES ($1, $2) RETURNING id, created_at",
    )
    .bind(me.id)
    .bind(target.id)
    .fetch_one(&mut *tx)
    .await
    .map_err(|e| {
        if is_unique_violation(&e, "friend_requests_pair_key") {
            request_pending()
        } else {
            e.into()
        }
    })?;
    tx.commit().await?;

    if let Some(world) = state.world() {
        world.friend_event(
            target.id,
            FriendEvent::RequestReceived {
                request_id: id,
                character_id: me.id,
                name: me.name.clone(),
                house: me.house,
            },
        );
    }
    let request = FriendRequestView {
        id,
        character_id: target.id,
        name: target.name,
        house: target.house,
        created_at,
    };
    Ok((StatusCode::CREATED, Json(json!({ "request": request }))).into_response())
}

fn request_pending() -> ApiError {
    ApiError::conflict("request_pending", "You have already asked them.")
}

fn request_not_found() -> ApiError {
    ApiError::not_found("request_not_found", "That friend request is gone.")
}

/// `POST /api/friends/requests/{id}/accept` → `{ friend }` (recipient only).
pub async fn accept(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(request_id): ApiPath<i64>,
) -> Result<Json<serde_json::Value>, ApiError> {
    let me = player.character;
    let from: Option<i64> =
        sqlx::query_scalar("SELECT from_id FROM friend_requests WHERE id = $1 AND to_id = $2")
            .bind(request_id)
            .bind(me.id)
            .fetch_optional(&state.pool)
            .await?;
    let from = from.ok_or_else(request_not_found)?;
    let mut tx = state.pool.begin().await?;
    lock_pair(&mut tx, me.id, from).await?;
    // Re-check under the locks: it may have been cancelled meanwhile.
    let sender: Option<(String, i16)> = sqlx::query_as(
        "SELECT c.name, c.house FROM friend_requests r JOIN characters c ON c.id = r.from_id
         WHERE r.id = $1 AND r.to_id = $2",
    )
    .bind(request_id)
    .bind(me.id)
    .fetch_optional(&mut *tx)
    .await?;
    let (name, house) = sender.ok_or_else(request_not_found)?;
    let since = befriend(&mut tx, me.id, from).await?;
    tx.commit().await?;
    let other = Other {
        id: from,
        name,
        house: house_from_db(house).unwrap_or(House::Roarwing),
    };
    let friend = announce_friendship(&state, Other::from(&me), other, since).await;
    Ok(Json(json!({ "friend": friend })))
}

/// Deletes a request this player is on the given side of; returns the other
/// party, or `404 request_not_found`.
async fn close(
    state: &AppState,
    me: i64,
    request_id: i64,
    as_recipient: bool,
) -> Result<i64, ApiError> {
    let (mine, theirs) = if as_recipient {
        ("to_id", "from_id")
    } else {
        ("from_id", "to_id")
    };
    let other: Option<i64> = sqlx::query_scalar(&format!(
        "DELETE FROM friend_requests WHERE id = $1 AND {mine} = $2 RETURNING {theirs}"
    ))
    .bind(request_id)
    .bind(me)
    .fetch_optional(&state.pool)
    .await?;
    let other = other.ok_or_else(request_not_found)?;
    if let Some(world) = state.world() {
        world.friend_event(other, FriendEvent::RequestClosed { request_id });
    }
    Ok(other)
}

/// `POST /api/friends/requests/{id}/decline` → `204` (recipient only).
pub async fn decline(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(request_id): ApiPath<i64>,
) -> Result<StatusCode, ApiError> {
    close(&state, player.character.id, request_id, true).await?;
    Ok(StatusCode::NO_CONTENT)
}

/// `DELETE /api/friends/requests/{id}` → `204`: the sender cancels.
pub async fn cancel(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(request_id): ApiPath<i64>,
) -> Result<StatusCode, ApiError> {
    close(&state, player.character.id, request_id, false).await?;
    Ok(StatusCode::NO_CONTENT)
}
