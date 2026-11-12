//! The account's characters: the picker's list, picking the one to play and
//! deleting one. Creating one is [`super::create`].
//!
//! One character is played at a time, per session: `sessions.character_id`
//! names it, every [`Player`](super::Player) endpoint and the gateway's
//! `Hello` act as it, and `404 no_character` (or `Kick { NoCharacter }`)
//! means none is picked. The account keeps its one live connection
//! (`realtime::Accounts`): entering as another character replaces it.

use axum::Json;
use axum::extract::State;
use axum::http::StatusCode;
use protocol::{Appearance, FriendEvent, House, KickCode};
use serde::{Deserialize, Serialize};
use sqlx::PgExecutor;

use super::extract::load_player;
use super::{
    ApiPath, CHARACTER_COLUMNS, CharacterRow, CharacterView, LimitedSession, MAX_PER_ACCOUNT,
    name_key,
};
use crate::AppState;
use crate::auth::AuthSession;
use crate::auth::token::TokenHash;
use crate::http::{ApiError, ApiJson};
use crate::ledger::{self, ItemMove, ItemOp, Reason};

/// One of the account's characters as the picker shows it.
#[derive(Debug, Serialize)]
pub struct CharacterSummary {
    pub id: i64,
    pub name: String,
    pub house: House,
    pub level: i16,
    pub appearance: Appearance,
    pub appearance_version: u32,
}

/// `GET /api/characters` → `{ characters, max }`.
#[derive(Debug, Serialize)]
pub struct Roster {
    /// Oldest first.
    pub characters: Vec<CharacterSummary>,
    /// How many an account may have ([`MAX_PER_ACCOUNT`]).
    pub max: i64,
}

#[derive(sqlx::FromRow)]
struct SummaryRow {
    #[sqlx(flatten)]
    character: CharacterRow,
    level: i16,
}

/// `GET /api/characters`: the account's characters, oldest first (an empty
/// list sends the client to the creator).
pub async fn list(
    State(state): State<AppState>,
    auth: AuthSession,
) -> Result<Json<Roster>, ApiError> {
    let rows: Vec<SummaryRow> = sqlx::query_as(&format!(
        "SELECT {CHARACTER_COLUMNS}, level FROM characters WHERE account_id = $1 ORDER BY id"
    ))
    .bind(auth.account_id)
    .fetch_all(&state.pool)
    .await?;
    let characters = rows
        .into_iter()
        .filter_map(|r| {
            let level = r.level;
            r.character.into_character().map(|c| CharacterSummary {
                id: c.id,
                name: c.name,
                house: c.house,
                level,
                appearance: c.appearance,
                appearance_version: c.appearance_version,
            })
        })
        .collect();
    Ok(Json(Roster {
        characters,
        max: MAX_PER_ACCOUNT,
    }))
}

/// Makes `character_id` the one the session plays, if it is one of the
/// session's account's characters. Whether it was.
pub async fn select_for_session(
    db: impl PgExecutor<'_>,
    session: &TokenHash,
    character_id: i64,
) -> Result<bool, sqlx::Error> {
    let res = sqlx::query(
        "UPDATE sessions s SET character_id = c.id
         FROM characters c
         WHERE s.token_hash = $1 AND c.id = $2 AND c.account_id = s.account_id",
    )
    .bind(&session[..])
    .bind(character_id)
    .execute(db)
    .await?;
    Ok(res.rows_affected() > 0)
}

/// After the session switched to `character_id`: a live connection of this
/// session playing another character ends (`Replaced`), so one session
/// never acts as two characters at once.
pub(crate) fn end_other_play(state: &AppState, auth: &AuthSession, character_id: i64) {
    if auth.character_id != Some(character_id)
        && let Some(rt) = &state.realtime
    {
        rt.kick_session(auth.account_id, &auth.token_hash, KickCode::Replaced);
    }
}

pub fn character_not_found() -> ApiError {
    ApiError::not_found("character_not_found", "You have no character like that.")
}

/// `POST /api/characters/{id}/select` → `200 Character`: the session plays
/// this character from now on (`404 character_not_found` unless it is one
/// of the account's). A character made before starter wardrobes gets its
/// starter wardrobe on the way, as with `GET /api/characters/me`.
pub async fn select(
    State(state): State<AppState>,
    LimitedSession(auth): LimitedSession,
    ApiPath(id): ApiPath<i64>,
) -> Result<Json<CharacterView>, ApiError> {
    if !select_for_session(&state.pool, &auth.token_hash, id).await? {
        return Err(character_not_found());
    }
    end_other_play(&state, &auth, id);
    let auth = AuthSession {
        character_id: Some(id),
        ..auth
    };
    let player = load_player(&state, &auth).await?;
    Ok(Json(player.character.into()))
}

#[derive(Debug, Deserialize)]
pub struct DeleteCharacter {
    /// The character's name, typed by the player to confirm (case and
    /// surrounding spaces do not matter).
    pub confirm_name: String,
}

/// `DELETE /api/characters/{id} { confirm_name }` → `204`. Deletes one of
/// the account's characters for good: its name is free again and
/// everything it had goes with it (items and Crowns, its uncollected
/// mail's Crowns too, recorded in the ledgers as destroyed with reason
/// `character_deleted`, mail, friends,
/// listings, progress, its club membership: a club it owned passes on as if
/// it left). Records it shares with others stay theirs (a duel
/// in the opponent's history, a listing they bought, a trade, letters it
/// sent), with the deleted side empty.
///
/// `404 character_not_found` unless it is the account's (checked first),
/// `409 character_online` while it is in the world or away from a duel
/// within its disconnect grace (another device playing it counts),
/// `400 name_mismatch` when `confirm_name` is not its name.
/// Sessions that played it play nothing until they pick again.
pub async fn delete(
    State(state): State<AppState>,
    LimitedSession(auth): LimitedSession,
    ApiPath(id): ApiPath<i64>,
    ApiJson(req): ApiJson<DeleteCharacter>,
) -> Result<StatusCode, ApiError> {
    let mut tx = state.pool.begin().await?;
    // The character row first, as every economy transaction locks it.
    let found: Option<(String, i64)> = sqlx::query_as(
        "SELECT name, crowns FROM characters WHERE id = $1 AND account_id = $2 FOR UPDATE",
    )
    .bind(id)
    .bind(auth.account_id)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((name, crowns)) = found else {
        return Err(character_not_found());
    };
    // Only for the account's own characters: anyone else's id is 404.
    if in_play(&state, id).await {
        return Err(character_online());
    }
    if name_key(req.confirm_name.trim()) != name_key(&name) {
        return Err(ApiError::bad_request(
            "name_mismatch",
            format!("Type {name} exactly to delete this character."),
        ));
    }

    // Who to tell once it is gone.
    let friends: Vec<i64> = sqlx::query_scalar(
        "SELECT friend_id FROM friendships WHERE character_id = $1 ORDER BY friend_id",
    )
    .bind(id)
    .fetch_all(&mut *tx)
    .await?;
    let requests: Vec<(i64, i64)> = sqlx::query_as(
        "SELECT id, CASE WHEN from_id = $1 THEN to_id ELSE from_id END
         FROM friend_requests WHERE from_id = $1 OR to_id = $1",
    )
    .bind(id)
    .fetch_all(&mut *tx)
    .await?;
    let blocks: Vec<i64> = sqlx::query_scalar(
        "SELECT CASE WHEN blocker_id = $1 THEN blocked_id ELSE blocker_id END
         FROM blocks WHERE blocker_id = $1 OR blocked_id = $1",
    )
    .bind(id)
    .fetch_all(&mut *tx)
    .await?;

    // Everything it holds leaves the economy, in the ledgers.
    let items: Vec<(i64, i32, i32, String)> = sqlx::query_as(
        "SELECT id, item_def, quantity, location FROM items WHERE owner_id = $1
         ORDER BY id FOR UPDATE",
    )
    .bind(id)
    .fetch_all(&mut *tx)
    .await?;
    let moves: Vec<ItemMove<'_>> = items
        .iter()
        .map(|(item, def, quantity, location)| {
            ItemMove::new(
                *item,
                u16::try_from(*def).unwrap_or(0),
                ItemOp::Destroy,
                u32::try_from(*quantity).unwrap_or(1),
                Reason::CharacterDeleted,
            )
            .from(id, location)
        })
        .collect();
    ledger::record_item_moves(&mut tx, &moves)
        .await
        .map_err(crate::wallet::ledger_error)?;
    // Crowns waiting in its uncollected mail (letters, Exchange proceeds)
    // leave too: each is collected into its balance and destroyed, both
    // naming the mail, so the ledger says where they went.
    let mailed: Vec<(i64, i64)> = sqlx::query_as(
        "SELECT id, crowns FROM mail
         WHERE recipient_id = $1 AND collected_at IS NULL AND crowns > 0
         ORDER BY id FOR UPDATE",
    )
    .bind(id)
    .fetch_all(&mut *tx)
    .await?;
    for &(mail_id, amount) in &mailed {
        ledger::adjust_crowns(&mut tx, id, amount, Reason::MailCollect, Some(mail_id))
            .await
            .map_err(crate::wallet::ledger_error)?;
        ledger::adjust_crowns(
            &mut tx,
            id,
            -amount,
            Reason::CharacterDeleted,
            Some(mail_id),
        )
        .await
        .map_err(crate::wallet::ledger_error)?;
    }
    ledger::adjust_crowns(&mut tx, id, -crowns, Reason::CharacterDeleted, None)
        .await
        .map_err(crate::wallet::ledger_error)?;
    // Its active listings go with their items; closed ones keep their
    // buyer's purchase with the seller emptied (ON DELETE SET NULL).
    sqlx::query("DELETE FROM exchange_listings WHERE seller_id = $1 AND state = 'active'")
        .bind(id)
        .execute(&mut *tx)
        .await?;
    // Its club membership goes as if it left: the club passes to the
    // longest-serving officer (else member), or disbands when emptied.
    let club = crate::clubs::store::remove_deleted(&mut tx, id).await?;
    // The rest cascades (see db/migrations/0024_multiple_characters.sql).
    sqlx::query("DELETE FROM characters WHERE id = $1")
        .bind(id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    tracing::info!(
        character_id = id,
        account_id = auth.account_id,
        "character deleted"
    );

    if let Some(world) = state.world() {
        for &friend in &friends {
            crate::friends::announce_unfriended(&state, id, friend);
        }
        for &(request_id, other) in &requests {
            world.friend_event(other, FriendEvent::RequestClosed { request_id });
        }
        for &other in &blocks {
            world.friends_changed(other);
        }
        if let Some((club_id, notices)) = club {
            world.club_changed(club_id, notices);
        }
    }
    // A connection that signed in as it after the check (registered, maybe
    // still on its way into the world) ends now; one that registers later
    // finds its session playing nothing (the gateway's first session check).
    if let Some(rt) = &state.realtime {
        rt.kick_character(auth.account_id, id, KickCode::NoCharacter);
    }
    Ok(StatusCode::NO_CONTENT)
}

/// In the world, or away from a duel it may still come back to.
async fn in_play(state: &AppState, id: i64) -> bool {
    match state.world() {
        Some(world) => world.in_play(id).await,
        None => false,
    }
}

fn character_online() -> ApiError {
    ApiError::conflict(
        "character_online",
        "That character is in the castle right now. Leave the world first.",
    )
}
