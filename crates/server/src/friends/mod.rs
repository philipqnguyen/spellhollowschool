//! Friends, friend requests and blocks (tables `friendships`,
//! `friend_requests`, `blocks`; HTTP routes under `/api/friends` and
//! `/api/blocks`, see docs/architecture.md).
//!
//! Owned by the data side. The realtime side uses only the read helpers
//! below (to load friend sets for priority players, online notifications,
//! chat blocking and dorm invites); the HTTP handlers tell it about changes
//! through `realtime::world::WorldHandle::{friends_changed, friend_event}`.
//!
//! Rules every change follows:
//! - Both characters' rows are locked (in id order, [`lock_pair`]) before
//!   reading what is between them, so two requests crossing, or a request
//!   racing a block, cannot leave an inconsistent pair.
//! - Pushed events go to the *other* party (the actor has the HTTP
//!   response), except `Added` and `Removed`, which both sides get.
//! - After committing, both sides' `friends_changed` whenever the friendship
//!   or block between them changed.

mod blocks;
mod requests;

use std::collections::HashMap;
use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{FRIENDS_MAX, FriendEvent, House, Location};
use serde::Serialize;
use sqlx::{PgConnection, PgExecutor};

use crate::AppState;
use crate::characters::{
    ApiPath, Character, Page, PageQuery, Reader, Writer, find_by_name, house_from_db, read_limited,
    write_limited,
};
use crate::http::{ApiError, ApiQuery};
use crate::rate_limit::RateLimit;
use crate::realtime::world::OnlinePresence;

/// Friend and block writes per account: a burst, then one every two seconds.
pub const FRIENDS_RATE_LIMIT: RateLimit = RateLimit {
    burst: 20,
    per: Duration::from_secs(2),
};

/// Friend-list pages per account: a burst, then two a second. Each page asks
/// the world thread where up to 100 friends are, so an unlimited loop of
/// them would cost every player in the castle tick time.
pub const FRIEND_LIST_RATE_LIMIT: RateLimit = RateLimit {
    burst: 20,
    per: Duration::from_millis(500),
};

/// The character ids `character_id` is friends with.
pub async fn friend_ids(
    db: impl PgExecutor<'_>,
    character_id: i64,
) -> Result<Vec<i64>, sqlx::Error> {
    sqlx::query_scalar("SELECT friend_id FROM friendships WHERE character_id = $1")
        .bind(character_id)
        .fetch_all(db)
        .await
}

/// Who `character_id` blocked, and who blocked them.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct BlockLists {
    /// Characters this one blocked: their chat is not delivered to it.
    pub blocked: Vec<i64>,
    /// Characters that blocked this one: it cannot whisper or invite them.
    pub blocked_by: Vec<i64>,
}

/// Both block lists of `character_id`, in one query.
pub async fn block_lists(
    db: impl PgExecutor<'_>,
    character_id: i64,
) -> Result<BlockLists, sqlx::Error> {
    let rows: Vec<(i64, i64)> = sqlx::query_as(
        "SELECT blocker_id, blocked_id FROM blocks WHERE blocker_id = $1 OR blocked_id = $1",
    )
    .bind(character_id)
    .fetch_all(db)
    .await?;
    let mut lists = BlockLists::default();
    for (blocker, blocked) in rows {
        if blocker == character_id {
            lists.blocked.push(blocked);
        } else {
            lists.blocked_by.push(blocker);
        }
    }
    Ok(lists)
}

// ------------------------------------------------------------------ views

/// Where an online friend is, in the API: `{ kind: "castle", place }`,
/// `{ kind: "dorm", owner_name }` or `{ kind: "wishing_room",
/// owner_name }`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum LocationView {
    Castle { place: String },
    Dorm { owner_name: String },
    WishingRoom { owner_name: String },
}

impl From<Location> for LocationView {
    fn from(l: Location) -> Self {
        match l {
            Location::Castle { place } => LocationView::Castle { place },
            Location::Dorm { owner_name } => LocationView::Dorm { owner_name },
            Location::WishingRoom { owner_name } => LocationView::WishingRoom { owner_name },
        }
    }
}

/// A friend in the API: `{ character_id, name, house, online, location,
/// room_open, since }`.
#[derive(Debug, Clone, Serialize)]
pub struct FriendView {
    pub character_id: i64,
    pub name: String,
    pub house: House,
    pub online: bool,
    pub location: Option<LocationView>,
    /// Online with their room open to friends: a visit would let you in.
    pub room_open: bool,
    pub since: DateTime<Utc>,
}

/// Another character as a friend list needs it.
#[derive(Debug, Clone)]
pub(crate) struct Other {
    pub id: i64,
    pub name: String,
    pub house: House,
}

impl From<&Character> for Other {
    fn from(c: &Character) -> Self {
        Other {
            id: c.id,
            name: c.name.clone(),
            house: c.house,
        }
    }
}

/// Where each of `ids` is (and whether their room is open), if online.
/// Empty when the world is not running.
pub(crate) async fn presence(state: &AppState, ids: &[i64]) -> HashMap<i64, OnlinePresence> {
    match state.world() {
        Some(world) if !ids.is_empty() => world.presence(ids).await,
        _ => HashMap::new(),
    }
}

fn friend_view(other: Other, since: DateTime<Utc>, presence: Option<OnlinePresence>) -> FriendView {
    FriendView {
        character_id: other.id,
        name: other.name,
        house: other.house,
        online: presence.is_some(),
        room_open: presence.as_ref().is_some_and(|p| p.room_open),
        location: presence.map(|p| LocationView::from(p.location)),
        since,
    }
}

// ------------------------------------------------------------------ shared steps

/// Finds the character a request or block names: `404 character_not_found`
/// for unknown names, `400 self` for the player's own.
pub(crate) async fn target_by_name(
    state: &AppState,
    me: &Character,
    name: &str,
) -> Result<Character, ApiError> {
    let target = find_by_name(&state.pool, name)
        .await?
        .ok_or_else(character_not_found)?;
    if target.id == me.id {
        return Err(ApiError::bad_request("self", "That is you."));
    }
    Ok(target)
}

pub(crate) fn character_not_found() -> ApiError {
    ApiError::not_found("character_not_found", "No one goes by that name.")
}

/// Locks both characters' rows, in id order so concurrent pairs never deadlock.
pub(crate) async fn lock_pair(conn: &mut PgConnection, a: i64, b: i64) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT id FROM characters WHERE id = ANY($1) ORDER BY id FOR NO KEY UPDATE")
        .bind([a.min(b), a.max(b)])
        .execute(conn)
        .await?;
    Ok(())
}

pub(crate) async fn friend_count(
    conn: &mut PgConnection,
    character_id: i64,
) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar("SELECT count(*) FROM friendships WHERE character_id = $1")
        .bind(character_id)
        .fetch_one(conn)
        .await
}

pub(crate) async fn are_friends(
    conn: &mut PgConnection,
    a: i64,
    b: i64,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM friendships WHERE character_id = $1 AND friend_id = $2)",
    )
    .bind(a)
    .bind(b)
    .fetch_one(conn)
    .await
}

/// Makes `a` and `b` friends (both rows) and drops any pending requests
/// between them. `409 friend_limit` if either is at `FRIENDS_MAX`. Returns
/// the friendship's start. Requires [`lock_pair`].
pub(crate) async fn befriend(
    conn: &mut PgConnection,
    a: i64,
    b: i64,
) -> Result<DateTime<Utc>, ApiError> {
    let max = i64::from(FRIENDS_MAX);
    if friend_count(&mut *conn, a).await? >= max {
        return Err(ApiError::conflict(
            "friend_limit",
            format!("You already have {FRIENDS_MAX} friends."),
        ));
    }
    if friend_count(&mut *conn, b).await? >= max {
        return Err(ApiError::conflict(
            "friend_limit",
            "They cannot add any more friends.",
        ));
    }
    let since: DateTime<Utc> = sqlx::query_scalar(
        "INSERT INTO friendships (character_id, friend_id)
         VALUES ($1, $2), ($2, $1)
         RETURNING created_at",
    )
    .bind(a)
    .bind(b)
    .fetch_one(&mut *conn)
    .await?;
    sqlx::query(
        "DELETE FROM friend_requests
         WHERE (from_id = $1 AND to_id = $2) OR (from_id = $2 AND to_id = $1)",
    )
    .bind(a)
    .bind(b)
    .execute(&mut *conn)
    .await?;
    Ok(since)
}

/// After a friendship between `me` and `other` is committed: both get
/// `Added` (with the other's location), the world reloads both friend sets,
/// and `me` gets the new friend's view.
pub(crate) async fn announce_friendship(
    state: &AppState,
    me: Other,
    other: Other,
    since: DateTime<Utc>,
) -> FriendView {
    let mut where_ = presence(state, &[me.id, other.id]).await;
    let mine = where_.remove(&me.id);
    let theirs = where_.remove(&other.id);
    if let Some(world) = state.world() {
        world.friend_event(
            me.id,
            FriendEvent::Added {
                character_id: other.id,
                name: other.name.clone(),
                house: other.house,
                location: theirs.as_ref().map(|p| p.location.clone()),
                room_open: theirs.as_ref().is_some_and(|p| p.room_open),
            },
        );
        world.friend_event(
            other.id,
            FriendEvent::Added {
                character_id: me.id,
                name: me.name,
                house: me.house,
                location: mine.as_ref().map(|p| p.location.clone()),
                room_open: mine.as_ref().is_some_and(|p| p.room_open),
            },
        );
        world.friends_changed(me.id);
        world.friends_changed(other.id);
    }
    friend_view(other, since, theirs)
}

/// Deletes the friendship between `a` and `b` (both rows). Whether there was one.
pub(crate) async fn unfriend(conn: &mut PgConnection, a: i64, b: i64) -> Result<bool, sqlx::Error> {
    let res = sqlx::query(
        "DELETE FROM friendships
         WHERE (character_id = $1 AND friend_id = $2) OR (character_id = $2 AND friend_id = $1)",
    )
    .bind(a)
    .bind(b)
    .execute(conn)
    .await?;
    Ok(res.rows_affected() > 0)
}

/// After an unfriending is committed: `Removed` to both, and the world
/// reloads both friend sets.
pub(crate) fn announce_unfriended(state: &AppState, a: i64, b: i64) {
    if let Some(world) = state.world() {
        world.friend_event(a, FriendEvent::Removed { character_id: b });
        world.friend_event(b, FriendEvent::Removed { character_id: a });
        world.friends_changed(a);
        world.friends_changed(b);
    }
}

// ------------------------------------------------------------------ friend list

#[derive(Debug, sqlx::FromRow)]
struct FriendRow {
    id: i64,
    name: String,
    name_key: String,
    house: i16,
    since: DateTime<Utc>,
}

/// `GET /api/friends?limit=&after=`: by name (case-insensitive), with online
/// status and location from the world.
pub async fn list(
    State(state): State<AppState>,
    Reader(player): Reader,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<FriendView>>, ApiError> {
    let after: Option<(String, i64)> = page.after()?;
    let (after_key, after_id) = after.unzip();
    let rows: Vec<FriendRow> = sqlx::query_as(
        "SELECT c.id, c.name, c.name_key, c.house, f.created_at AS since
         FROM friendships f JOIN characters c ON c.id = f.friend_id
         WHERE f.character_id = $1
           AND ($2::text IS NULL OR (c.name_key, c.id) > ($2, $3))
         ORDER BY c.name_key, c.id
         LIMIT $4",
    )
    .bind(player.character.id)
    .bind(after_key)
    .bind(after_id)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    let page = Page::from_rows(rows, page.limit()?, |r| (r.name_key.clone(), r.id));
    let ids: Vec<i64> = page.items.iter().map(|r| r.id).collect();
    let mut where_ = presence(&state, &ids).await;
    Ok(Json(page.map(|r| {
        let other = Other {
            id: r.id,
            name: r.name,
            house: house_from_db(r.house).unwrap_or(House::Roarwing),
        };
        let online = where_.remove(&r.id);
        friend_view(other, r.since, online)
    })))
}

/// `DELETE /api/friends/{character_id}` → `204`, for both sides.
pub async fn remove(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(friend_id): ApiPath<i64>,
) -> Result<StatusCode, ApiError> {
    let me = player.character.id;
    let mut tx = state.pool.begin().await?;
    lock_pair(&mut tx, me, friend_id).await?;
    if !unfriend(&mut tx, me, friend_id).await? {
        return Err(ApiError::not_found(
            "not_friends",
            "You are not friends with them.",
        ));
    }
    tx.commit().await?;
    announce_unfriended(&state, me, friend_id);
    Ok(StatusCode::NO_CONTENT)
}

/// This module's routes, merged under `/api` by `http::router`.
pub fn routes() -> Router<AppState> {
    use axum::routing::{delete, get, post};
    let reads = read_limited(
        Router::new().route("/friends", get(list)),
        FRIEND_LIST_RATE_LIMIT,
    );
    let router = Router::new()
        .route("/friends/{character_id}", delete(remove))
        .route(
            "/friends/requests",
            get(requests::list).post(requests::send),
        )
        .route("/friends/requests/{id}", delete(requests::cancel))
        .route("/friends/requests/{id}/accept", post(requests::accept))
        .route("/friends/requests/{id}/decline", post(requests::decline))
        .route("/blocks", get(blocks::list).post(blocks::block))
        .route("/blocks/{character_id}", delete(blocks::unblock));
    write_limited(router, FRIENDS_RATE_LIMIT).merge(reads)
}
