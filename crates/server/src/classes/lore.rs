//! Lore and hints: what a class with
//! `rewards.lore` gives, and the lore book's route.
//!
//! - [`unlock`] (in the class reward's transaction): the student's next
//!   entry of `data/classes/lore/lore.json` they have not unlocked
//!   (`Lore::next`), as a `lore_unlocks` row naming the attendance (one per
//!   attendance, each entry once per character). Nothing when every entry is
//!   unlocked (or the file is empty, as).
//! - [`hint`]: the display name of the castle surface holding a find spot
//!   the student has not searched within its cooldown (a day: "not searched
//!   today"), on ground they may walk (their own house's gated rooms only).
//!   The spot is chosen by the attendance id, so a retried reward that
//!   needs it again names the same place. The reward stores it on
//!   the attendance row (`class_attendance.hint`), so a retry answers it.
//!
//! Routes (the only source of lore text; the client bundles no
//! entry, and the file lives outside its `data/classes/*.json` glob):
//! - `GET /api/lore?limit=&after=` → `{ items: [{ id, title, text, subject,
//!   unlocked_at }], next }`: the character's unlocked entries, newest first
//!   (keyset on `(unlocked_at, lore_id)`, index `lore_unlocks_page_idx`).
//!   `subject` is the subject of the class that unlocked it (`history`);
//!   NPCs: an entry an NPC told (`lore_unlocks.npc`) has `subject` null and
//!   `npc` its roster id (`npc` is null for a class's).
//! - `GET /api/lore/{id}` → one such entry (for the results card), or `404
//!   lore_locked` for an entry the character has not unlocked (or that does
//!   not exist: the two are not told apart).
//!
//! Nothing serves a locked entry's title or text. The routes are merged
//! into the classes routes (the module lives under `classes`, so `lib.rs`
//! is untouched).

use std::collections::HashSet;
use std::sync::LazyLock;

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::House;
use serde::Serialize;
use sim_core::{Layout, Lore};
use sqlx::PgConnection;

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{ApiPath, Page, PageQuery, Player};
use crate::http::{ApiError, ApiQuery};

/// The shipped castle, for the names of the places hints point at (the
/// find spots of `data/world/finds.json` are the shipped layout's).
static CASTLE: LazyLock<Option<Layout>> =
    LazyLock::new(|| Layout::from_json(include_str!("../../../../data/world/layout.json")).ok());

/// Unlocks the student's next lore entry for attendance `attendance_id`
/// (see the module docs); the entry's id, if one was left.
pub async fn unlock(
    conn: &mut PgConnection,
    lore: &Lore,
    character_id: i64,
    attendance_id: i64,
) -> Result<Option<u16>, sqlx::Error> {
    if lore.entries.is_empty() {
        return Ok(None);
    }
    let known: Vec<i32> =
        sqlx::query_scalar("SELECT lore_id FROM lore_unlocks WHERE character_id = $1")
            .bind(character_id)
            .fetch_all(&mut *conn)
            .await?;
    let known: HashSet<u16> = known
        .into_iter()
        .filter_map(|id| u16::try_from(id).ok())
        .collect();
    let Some(entry) = lore.next(&known) else {
        return Ok(None);
    };
    sqlx::query(
        "INSERT INTO lore_unlocks (character_id, lore_id, attendance_id) VALUES ($1, $2, $3)",
    )
    .bind(character_id)
    .bind(i32::from(entry.id))
    .bind(attendance_id)
    .execute(&mut *conn)
    .await?;
    Ok(Some(entry.id))
}

/// Where find spot `spot` is: the name of the castle surface under it, and
/// that surface's house gate (lower-case id), if any.
pub fn spot_place(spot: &str) -> Option<(&'static str, Option<&'static str>)> {
    let layout = CASTLE.as_ref()?;
    let pos = layout.interactable(spot)?.pos;
    layout
        .surfaces
        .iter()
        .filter(|s| {
            (s.min[0]..=s.max[0]).contains(&pos[0]) && (s.min[1]..=s.max[1]).contains(&pos[2])
        })
        .min_by(|a, b| {
            let (da, db) = ((a.y - pos[1]).abs(), (b.y - pos[1]).abs());
            da.total_cmp(&db)
        })
        .map(|s| (s.name.as_str(), s.house.as_deref()))
}

/// A hint for attendance `attendance_id` (see the module docs).
pub async fn hint(
    conn: &mut PgConnection,
    character_id: i64,
    house: House,
    attendance_id: i64,
) -> Result<Option<String>, sqlx::Error> {
    let candidates = hint_places(conn, character_id, house).await?;
    if candidates.is_empty() {
        return Ok(None);
    }
    let k = attendance_id.rem_euclid(candidates.len() as i64) as usize;
    Ok(Some(candidates[k].to_owned()))
}

/// NPCs: the class hint's rule for an NPC's daily gift, the place chosen by
/// `seed` (`sim_core::npcs::gift_seed`), so a retried claim names the same
/// place.
pub async fn hint_seeded(
    conn: &mut PgConnection,
    character_id: i64,
    house: House,
    seed: u64,
) -> Result<Option<String>, sqlx::Error> {
    let candidates = hint_places(conn, character_id, house).await?;
    if candidates.is_empty() {
        return Ok(None);
    }
    let k = (seed % candidates.len() as u64) as usize;
    Ok(Some(candidates[k].to_owned()))
}

/// The places a hint may name for the character, in find-spot order: the
/// surface of each find spot not searched within its cooldown, on ground
/// the character may walk (its own house's gated rooms only). Never a
/// secret surface (a hidden passage or room is found by exploring, never
/// told).
pub async fn hint_places(
    conn: &mut PgConnection,
    character_id: i64,
    house: House,
) -> Result<Vec<&'static str>, sqlx::Error> {
    let searched: Vec<String> = sqlx::query_scalar(
        "SELECT spot_id FROM find_cooldowns WHERE character_id = $1 AND ready_at > now()",
    )
    .bind(character_id)
    .fetch_all(&mut *conn)
    .await?;
    Ok(shipped()
        .finds
        .spots
        .iter()
        .filter(|s| !searched.contains(&s.id))
        .filter_map(|s| spot_place(&s.id))
        .filter(|(name, _)| !secret_place(name))
        .filter(|(_, gate)| gate.is_none_or(|g| g == house.id()))
        .map(|(name, _)| name)
        .collect())
}

/// Whether `name` is the display name of a secret surface of the castle.
pub fn secret_place(name: &str) -> bool {
    CASTLE
        .as_ref()
        .is_some_and(|l| l.surfaces.iter().any(|s| s.secret && s.name == name))
}

// ---------------------------------------------------------------- route

/// One entry of the lore book.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct LoreView {
    pub id: u16,
    pub title: String,
    pub text: String,
    /// The subject of the class that unlocked it (`"history"`),
    /// if that class is still shipped.
    pub subject: Option<&'static str>,
    /// NPCs: the roster id of the NPC that told it (`data/npcs/roster.json`,
    /// e.g. `ghost_house_c`); `None` for a class's (and then `subject`
    /// names the class).
    pub npc: Option<String>,
    pub unlocked_at: DateTime<Utc>,
}

/// An unlock row: the entry, when, the class of the attendance (none for
/// an NPC's) and the NPC that told it (none for a class's).
type UnlockRow = (i32, DateTime<Utc>, Option<String>, Option<String>);

/// The view of an unlocked entry of `lore` (none for an entry no longer in
/// the file).
fn view(lore: &Lore, (id, unlocked_at, class, npc): UnlockRow) -> Option<LoreView> {
    let e = lore.get(u16::try_from(id).ok()?)?;
    Some(LoreView {
        id: e.id,
        title: e.title.clone(),
        text: e.text.clone(),
        subject: class
            .and_then(|c| shipped().class(&c))
            .map(|c| c.subject.id()),
        npc,
        unlocked_at,
    })
}

/// `GET /api/lore?limit=&after=`: the character's unlocked entries, newest
/// first ([`lore_page`] with the shipped entries).
pub async fn get_lore(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<LoreView>>, ApiError> {
    let page = lore_page(&state.pool, &shipped().lore, player.character.id, &page).await?;
    Ok(Json(page))
}

/// One page of a character's unlocked entries of `lore`, newest first (an
/// entry no longer in the file is left out).
pub async fn lore_page(
    pool: &sqlx::PgPool,
    lore: &Lore,
    character_id: i64,
    page: &PageQuery,
) -> Result<Page<LoreView>, ApiError> {
    let limit = page.limit()?;
    // The cursor: the last row's (unlocked_at in Unix microseconds, lore id).
    let after: Option<(i64, i32)> = page.after()?;
    let (at, id) = match after {
        Some((us, id)) => (DateTime::from_timestamp_micros(us), Some(id)),
        None => (None, None),
    };
    let rows: Vec<UnlockRow> = sqlx::query_as(
        "SELECT l.lore_id, l.unlocked_at, a.class_id, l.npc
         FROM lore_unlocks l LEFT JOIN class_attendance a ON a.id = l.attendance_id
         WHERE l.character_id = $1
           AND ($2::timestamptz IS NULL OR l.unlocked_at < $2
                OR (l.unlocked_at = $2 AND l.lore_id > $3))
         ORDER BY l.unlocked_at DESC, l.lore_id
         LIMIT $4",
    )
    .bind(character_id)
    .bind(at)
    .bind(id.unwrap_or(0))
    .bind(page.fetch_limit()?)
    .fetch_all(pool)
    .await?;
    let page = Page::from_rows(rows, limit, |(id, at, _, _)| (at.timestamp_micros(), *id));
    Ok(Page {
        items: page
            .items
            .into_iter()
            .filter_map(|row| view(lore, row))
            .collect(),
        next: page.next,
    })
}

/// `GET /api/lore/{id}`: one entry the character has unlocked
/// ([`lore_entry`] with the shipped entries).
pub async fn get_lore_entry(
    State(state): State<AppState>,
    player: Player,
    ApiPath(id): ApiPath<i32>,
) -> Result<Json<LoreView>, ApiError> {
    lore_entry(&state.pool, &shipped().lore, player.character.id, id)
        .await?
        .map(Json)
        .ok_or_else(|| ApiError::not_found("lore_locked", "No such entry in your lore book."))
}

/// Entry `id` of `lore` if the character has unlocked it (else `None`,
/// whether or not it exists).
pub async fn lore_entry(
    pool: &sqlx::PgPool,
    lore: &Lore,
    character_id: i64,
    id: i32,
) -> Result<Option<LoreView>, ApiError> {
    let row: Option<UnlockRow> = sqlx::query_as(
        "SELECT l.lore_id, l.unlocked_at, a.class_id, l.npc
         FROM lore_unlocks l LEFT JOIN class_attendance a ON a.id = l.attendance_id
         WHERE l.character_id = $1 AND l.lore_id = $2",
    )
    .bind(character_id)
    .bind(id)
    .fetch_optional(pool)
    .await?;
    Ok(row.and_then(|r| view(lore, r)))
}

/// This module's routes (merged by [`super::routes`]).
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new()
        .route("/lore", get(get_lore))
        .route("/lore/{id}", get(get_lore_entry))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_hint_place_is_a_secret() {
        // NPCs: a hint (a class's or an NPC's gift) never names a hidden
        // passage or room, found or not.
        for s in &shipped().finds.spots {
            if let Some((name, _)) = spot_place(&s.id) {
                assert!(!secret_place(name), "{} is on a secret surface", s.id);
            }
        }
        let layout = CASTLE.as_ref().unwrap();
        let secret = layout.surfaces.iter().find(|s| s.secret).unwrap();
        assert!(secret_place(&secret.name));
    }

    #[test]
    fn every_find_spot_has_a_named_place() {
        let finds = &shipped().finds;
        assert!(!finds.spots.is_empty());
        for s in &finds.spots {
            // The Lost Property Vault's heap is in no castle place,
            // so a class hint never names it.
            if s.id == "find_lost_property_vault" {
                assert!(spot_place(&s.id).is_none());
                continue;
            }
            let (name, gate) = spot_place(&s.id).unwrap_or_else(|| panic!("{} has no place", s.id));
            assert!(!name.trim().is_empty(), "{}", s.id);
            assert!(gate.is_none_or(|g| House::from_id(g).is_some()), "{}", s.id);
        }
        assert!(spot_place("no_such_spot").is_none());
    }
}
