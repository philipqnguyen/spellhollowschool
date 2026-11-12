//! Skills (the nine school subjects): XP per
//! character and subject (`skills` rows; none = 0 XP), levels from
//! `Recipes::skill_level` (one curve for every subject). Crafting adds XP to
//! its three disciplines (whose ids are their subjects'); every class adds
//! XP to its subject.
//!
//! Route: `GET /api/skills` → `{ skills: [{ discipline, xp, level,
//! next_level_xp | null }] }` (all nine subjects, always, in
//! `Subject::ALL` order; the field keeps its old name `discipline`).
//! Helpers for crafting and classes: [`xp_of`] and [`add_xp`] inside the
//! caller's transaction (either a crafting [`Discipline`] or a class
//! [`Subject`]), [`SkillView::new`] for its answer.

use axum::extract::State;
use axum::{Json, Router};
use serde::Serialize;
use sim_core::{Discipline, Subject};
use sqlx::PgConnection;

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::Player;
use crate::http::ApiError;

/// A skill track: a class [`Subject`], or a crafting [`Discipline`] (which
/// is the subject of the same id).
pub trait Track: Copy {
    fn subject(self) -> Subject;
}

impl Track for Subject {
    fn subject(self) -> Subject {
        self
    }
}

impl Track for Discipline {
    fn subject(self) -> Subject {
        Discipline::subject(self)
    }
}

/// Adds `xp` to a character's skill in `track` (creating the row) and
/// returns the new total. Runs in the caller's transaction (character row
/// already locked). The total saturates at `i32::MAX` rather than failing:
/// nobody reaches it, and a craft must not fail over a counter.
pub async fn add_xp(
    conn: &mut PgConnection,
    character_id: i64,
    track: impl Track,
    xp: u32,
) -> Result<u32, sqlx::Error> {
    let total: i32 = sqlx::query_scalar(
        "INSERT INTO skills (character_id, discipline, xp) VALUES ($1, $2, $3)
         ON CONFLICT (character_id, discipline)
         DO UPDATE SET xp = LEAST(skills.xp::bigint + EXCLUDED.xp, 2147483647)::int,
                       updated_at = now()
         RETURNING xp",
    )
    .bind(character_id)
    .bind(track.subject().id())
    .bind(i32::try_from(xp).unwrap_or(i32::MAX))
    .fetch_one(conn)
    .await?;
    Ok(u32::try_from(total).unwrap_or(0))
}

/// A character's XP in `track` (0 without a row).
pub async fn xp_of(
    conn: &mut PgConnection,
    character_id: i64,
    track: impl Track,
) -> Result<u32, sqlx::Error> {
    let xp: Option<i32> =
        sqlx::query_scalar("SELECT xp FROM skills WHERE character_id = $1 AND discipline = $2")
            .bind(character_id)
            .bind(track.subject().id())
            .fetch_optional(conn)
            .await?;
    Ok(xp.and_then(|x| u32::try_from(x).ok()).unwrap_or(0))
}

/// The skill level of `xp` (every subject shares the crafting curve).
pub fn level_of(xp: u32) -> u8 {
    shipped().recipes.skill_level(xp)
}

/// One subject in the API: `{ discipline, xp, level, next_level_xp }`.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SkillView {
    /// The subject's id (named `discipline`).
    pub discipline: Subject,
    pub xp: u32,
    pub level: u8,
    /// XP the next level needs; `null` at the top level.
    pub next_level_xp: Option<u32>,
}

impl SkillView {
    pub fn new(track: impl Track, xp: u32) -> Self {
        let recipes = &shipped().recipes;
        let level = recipes.skill_level(xp);
        // `skill_levels[0]` is the XP for level 2, so level L's next is [L - 1].
        let next_level_xp = recipes.skill_levels.get(usize::from(level) - 1).copied();
        SkillView {
            discipline: track.subject(),
            xp,
            level,
            next_level_xp,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct SkillsView {
    pub skills: Vec<SkillView>,
}

/// `GET /api/skills`: every subject, in `Subject::ALL` order.
pub async fn skills(
    State(state): State<AppState>,
    player: Player,
) -> Result<Json<SkillsView>, ApiError> {
    let rows: Vec<(String, i32)> =
        sqlx::query_as("SELECT discipline, xp FROM skills WHERE character_id = $1")
            .bind(player.character.id)
            .fetch_all(&state.pool)
            .await?;
    let skills = Subject::ALL
        .into_iter()
        .map(|s| {
            let xp = rows
                .iter()
                .find(|(id, _)| id == s.id())
                .and_then(|&(_, xp)| u32::try_from(xp).ok())
                .unwrap_or(0);
            SkillView::new(s, xp)
        })
        .collect();
    Ok(Json(SkillsView { skills }))
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new().route("/skills", get(skills))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn views_name_the_next_level() {
        let levels = &shipped().recipes.skill_levels;
        let fresh = SkillView::new(Discipline::Charms, 0);
        assert_eq!((fresh.level, fresh.next_level_xp), (1, Some(levels[0])));
        let two = SkillView::new(Discipline::Potions, levels[0]);
        assert_eq!((two.level, two.next_level_xp), (2, Some(levels[1])));
        let top = SkillView::new(Discipline::Transfiguration, u32::MAX);
        assert_eq!(top.level, shipped().recipes.max_level());
        assert_eq!(top.next_level_xp, None);
    }

    #[test]
    fn disciplines_and_subjects_share_ids() {
        for d in Discipline::ALL {
            assert_eq!(Track::subject(d).id(), d.id());
            assert_eq!(SkillView::new(d, 5), SkillView::new(Track::subject(d), 5));
        }
        let json = serde_json::to_value(SkillView::new(Subject::Creatures, 0)).unwrap();
        assert_eq!(json["discipline"], "creatures");
        assert_eq!(level_of(0), 1);
    }
}
