//! Player levels, XP and learned spells. Its shared core, for
//! the classes, dueling, crafting and finds code to rely on:
//!
//! - [`grant_xp`]: adds XP inside the caller's transaction, with the daily
//!   caps ([`caps`]), the `xp_ledger` row and level-up detection. A grant
//!   that names a `ref_id` happens once per character, source and ref: a
//!   retry answers `duplicate` and adds nothing.
//! - [`learn_spells`] / [`teach`]: `known_spells` rows (and the new hotbar
//!   spells into a stored hotbar's empty slots, `spells::hotbar::add_learned`).
//! - [`load_school`]: what the gateway loads at sign-in (XP, learned spells,
//!   loadout, hotbar) for `realtime::world::Joining`.
//! - [`notify`]: tells the world after commit (`Progress` to the character,
//!   `LevelChanged` to viewers on a level change).
//!
//! **Lock order** (docs/architecture.md, "Database"): the character's row
//! first. `grant_xp` locks it itself (`FOR NO KEY UPDATE`, harmless if the
//! caller already holds it) before touching `daily_caps` or the ledger, so
//! it fits any transaction that follows the established order: call it
//! after locking the character and before locking item rows, or after the
//! item work in the same transaction (the character lock is already held).
//!
//! Routes: `GET /api/progression` (level, XP, loadout slots) and
//! `GET /api/xp/history` (the character's ledger, newest first).
//!
//! **Levels** come from total XP (`data/progression.json`, 20 levels; the
//! row's `level` column follows every grant and never goes down). Existing
//! characters started at level 1 with 0 XP: their crafting skill XP stays
//! skill XP (converting it would have handed early crafters levels that
//! classes and duels are meant to give).

pub mod caps;

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{Progress, SpellId, XpGain, XpSource};
use serde::Serialize;
use sqlx::{PgConnection, PgPool};

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Page, PageQuery, Player};
use crate::http::{ApiError, ApiQuery};

/// The result of [`grant_xp`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct XpGrant {
    pub source: XpSource,
    /// What the source asked for.
    pub requested: u32,
    /// What was added (after the daily cap; 0 for a duplicate).
    pub granted: u32,
    /// The daily cap cut it short.
    pub capped: bool,
    /// This `(source, ref_id)` was granted before: nothing was added.
    pub duplicate: bool,
    /// The character's total XP now.
    pub xp: u32,
    pub level_before: u8,
    pub level: u8,
}

impl XpGrant {
    pub fn leveled_up(&self) -> bool {
        self.level > self.level_before
    }

    /// `Progress.gained` for this grant.
    pub fn gain(&self) -> XpGain {
        XpGain {
            source: self.source,
            amount: self.granted,
            capped: self.capped,
        }
    }

    /// The HUD's view of the character after this grant.
    pub fn progress(&self) -> Progress {
        shipped().progression.progress(self.xp)
    }
}

/// Adds `amount` XP from `source` to a character, in the caller's
/// transaction (see the module docs for the lock order). Capped sources
/// (`XpSource::cap_group`) count against the day's cap (UTC day of `now`);
/// a `ref_id` makes the grant happen at most once. Records an `xp_ledger`
/// row for every grant that is not a duplicate (also when capped to 0),
/// except a grant of 0 without a ref, which records nothing.
pub async fn grant_xp(
    conn: &mut PgConnection,
    character_id: i64,
    source: XpSource,
    ref_id: Option<i64>,
    amount: u32,
    now: DateTime<Utc>,
) -> Result<XpGrant, sqlx::Error> {
    let progression = &shipped().progression;
    let (xp, level): (i32, i16) =
        sqlx::query_as("SELECT xp, level FROM characters WHERE id = $1 FOR NO KEY UPDATE")
            .bind(character_id)
            .fetch_one(&mut *conn)
            .await?;
    let xp = u32::try_from(xp).unwrap_or(0);
    let level_before = u8::try_from(level).unwrap_or(1);
    let unchanged = |duplicate| XpGrant {
        source,
        requested: amount,
        granted: 0,
        capped: false,
        duplicate,
        xp,
        level_before,
        level: level_before,
    };
    // Nothing to add and nothing to remember: no ledger row.
    if amount == 0 && ref_id.is_none() {
        return Ok(unchanged(false));
    }
    if let Some(r) = ref_id {
        let seen: Option<i64> = sqlx::query_scalar(
            "SELECT id FROM xp_ledger WHERE character_id = $1 AND source = $2 AND ref_id = $3",
        )
        .bind(character_id)
        .bind(source.id())
        .bind(r)
        .fetch_optional(&mut *conn)
        .await?;
        if seen.is_some() {
            return Ok(unchanged(true));
        }
    }
    let mut granted = amount;
    if let Some((group, cap)) = progression.daily_cap(source)
        && amount > 0
    {
        granted = caps::take(
            &mut *conn,
            character_id,
            now.date_naive(),
            group,
            amount,
            cap,
        )
        .await?;
    }
    // The column is an `integer`: totals stop at i32::MAX (nobody gets there).
    let total = u64::from(xp) + u64::from(granted);
    let new_xp = u32::try_from(total.min(i32::MAX as u64)).expect("clamped");
    let granted = new_xp - xp;
    let level = progression.level_for(new_xp).max(level_before);
    if granted > 0 || level != level_before {
        sqlx::query("UPDATE characters SET xp = $2, level = $3 WHERE id = $1")
            .bind(character_id)
            .bind(new_xp as i32)
            .bind(i16::from(level))
            .execute(&mut *conn)
            .await?;
    }
    let inserted: Option<i64> = sqlx::query_scalar(
        "INSERT INTO xp_ledger (character_id, source, ref_id, requested, amount, xp_after, level_after)
         VALUES ($1, $2, $3, $4, $5, $6, $7)
         ON CONFLICT (character_id, source, ref_id) WHERE ref_id IS NOT NULL DO NOTHING
         RETURNING id",
    )
    .bind(character_id)
    .bind(source.id())
    .bind(ref_id)
    .bind(i32::try_from(amount).unwrap_or(i32::MAX))
    .bind(granted as i32)
    .bind(new_xp as i32)
    .bind(i16::from(level))
    .fetch_optional(&mut *conn)
    .await?;
    if inserted.is_none() {
        // Unreachable under the character lock (checked above); never
        // count a grant twice if it happens anyway.
        return Err(sqlx::Error::Protocol("xp grant raced its own ref".into()));
    }
    Ok(XpGrant {
        source,
        requested: amount,
        granted,
        capped: granted < amount,
        duplicate: false,
        xp: new_xp,
        level_before,
        level,
    })
}

/// Records spells as learned (`known_spells`), returning the ones that were
/// new, in the order given. Taught at `taught_at` (a class or venue id).
/// The new ones that belong on the social hotbar go into its first empty
/// slots (`spells::hotbar::add_learned`, which locks the character's row).
pub async fn learn_spells(
    conn: &mut PgConnection,
    character_id: i64,
    spells: &[SpellId],
    taught_at: &str,
) -> Result<Vec<SpellId>, sqlx::Error> {
    let mut new = Vec::new();
    for &spell in spells {
        let row: Option<i32> = sqlx::query_scalar(
            "INSERT INTO known_spells (character_id, spell_id, taught_at) VALUES ($1, $2, $3)
             ON CONFLICT (character_id, spell_id) DO NOTHING RETURNING spell_id",
        )
        .bind(character_id)
        .bind(i32::from(spell))
        .bind(taught_at)
        .fetch_optional(&mut *conn)
        .await?;
        if row.is_some() {
            new.push(spell);
        }
    }
    crate::spells::hotbar::add_learned(conn, character_id, &new).await?;
    Ok(new)
}

/// Learns every spell place `at` teaches a character of `level`
/// (`Spells::taught_at`); the newly learned ones.
pub async fn teach(
    conn: &mut PgConnection,
    character_id: i64,
    at: &str,
    level: u8,
) -> Result<Vec<SpellId>, sqlx::Error> {
    let spells: Vec<SpellId> = shipped()
        .spells
        .taught_at(at, level)
        .map(|s| s.id)
        .collect();
    learn_spells(conn, character_id, &spells, at).await
}

/// A character's school state as the world keeps it.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SchoolState {
    /// Total XP (the level follows from it).
    pub xp: u32,
    /// Learned spells, ascending.
    pub learned: Vec<SpellId>,
    /// The dueling loadout: the chosen one, or the default while none is.
    pub loadout: Vec<SpellId>,
    /// The social hotbar (protocol v17): the stored one, or the default
    /// while none is (`spells::hotbar`).
    pub hotbar: sim_core::spells::Hotbar,
}

impl SchoolState {
    pub fn level(&self) -> u8 {
        shipped().progression.level_for(self.xp)
    }

    pub fn slots(&self) -> u8 {
        shipped().progression.loadout_slots(self.level())
    }
}

/// The learned spells and the effective loadout of a character (the stored
/// one, trimmed to what is still allowed, or the default).
pub async fn spellbook(
    conn: &mut PgConnection,
    character_id: i64,
    level: u8,
) -> Result<(Vec<SpellId>, Vec<SpellId>), sqlx::Error> {
    let learned: Vec<i32> = sqlx::query_scalar(
        "SELECT spell_id FROM known_spells WHERE character_id = $1 ORDER BY spell_id",
    )
    .bind(character_id)
    .fetch_all(&mut *conn)
    .await?;
    let learned: Vec<SpellId> = learned
        .into_iter()
        .filter_map(|s| SpellId::try_from(s).ok())
        .collect();
    let stored: Option<Vec<i32>> =
        sqlx::query_scalar("SELECT spells FROM loadouts WHERE character_id = $1")
            .bind(character_id)
            .fetch_optional(&mut *conn)
            .await?;
    let content = shipped();
    let slots = content.progression.loadout_slots(level);
    let loadout = match stored {
        Some(s) => {
            let chosen: Vec<SpellId> = s
                .into_iter()
                .filter_map(|x| SpellId::try_from(x).ok())
                .collect();
            if content
                .spells
                .check_loadout(&learned, &chosen, slots)
                .is_ok()
            {
                chosen
            } else {
                content.spells.default_loadout(&learned, slots)
            }
        }
        None => content.spells.default_loadout(&learned, slots),
    };
    Ok((learned, loadout))
}

/// What the gateway loads at sign-in (`Joining.school`).
pub async fn load_school(pool: &PgPool, character_id: i64) -> Result<SchoolState, sqlx::Error> {
    let mut conn = pool.acquire().await?;
    let xp: i32 = sqlx::query_scalar("SELECT xp FROM characters WHERE id = $1")
        .bind(character_id)
        .fetch_one(&mut *conn)
        .await?;
    let xp = u32::try_from(xp).unwrap_or(0);
    let level = shipped().progression.level_for(xp);
    let (learned, loadout) = spellbook(&mut conn, character_id, level).await?;
    let hotbar = crate::spells::hotbar::load(&mut conn, character_id, &learned).await?;
    Ok(SchoolState {
        xp,
        learned,
        loadout,
        hotbar,
    })
}

/// The character's Flying skill XP (0 without a row), cached by
/// the world at join: above 0 the character may mount a broom.
pub async fn load_flying_xp(pool: &PgPool, character_id: i64) -> Result<u32, sqlx::Error> {
    let mut conn = pool.acquire().await?;
    crate::skills::xp_of(&mut conn, character_id, sim_core::Subject::Flying).await
}

/// After committing a grant: `Progress` to the character, and on a level
/// change `LevelChanged` to everyone who sees them. Duplicates tell nobody.
pub fn notify(
    world: Option<&crate::realtime::world::WorldHandle>,
    character_id: i64,
    grant: &XpGrant,
) {
    if let Some(w) = world
        && !grant.duplicate
    {
        w.progress_changed(character_id, grant.xp, Some(grant.gain()));
    }
}

/// `GET /api/progression`: the HUD's `Progress` plus the loadout's slots.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct ProgressionView {
    pub level: u8,
    pub xp: u32,
    pub level_xp: u32,
    pub next_level_xp: Option<u32>,
    pub loadout_slots: u8,
}

impl ProgressionView {
    pub fn new(xp: u32) -> Self {
        let p = &shipped().progression;
        let progress = p.progress(xp);
        ProgressionView {
            level: progress.level,
            xp,
            level_xp: progress.level_xp,
            next_level_xp: progress.next_level_xp,
            loadout_slots: p.loadout_slots(progress.level),
        }
    }
}

pub async fn get_progression(
    State(state): State<AppState>,
    player: Player,
) -> Result<Json<ProgressionView>, ApiError> {
    let xp: i32 = sqlx::query_scalar("SELECT xp FROM characters WHERE id = $1")
        .bind(player.character.id)
        .fetch_one(&state.pool)
        .await?;
    Ok(Json(ProgressionView::new(u32::try_from(xp).unwrap_or(0))))
}

/// One row of `GET /api/xp/history`.
#[derive(Debug, Clone, Serialize, sqlx::FromRow)]
pub struct XpRowView {
    pub id: i64,
    pub source: String,
    pub ref_id: Option<i64>,
    pub requested: i32,
    pub amount: i32,
    pub xp_after: i32,
    pub level_after: i16,
    pub at: DateTime<Utc>,
}

/// `GET /api/xp/history?limit=&after=`: the ledger, newest first (keyset on
/// the id, `xp_ledger (character_id, id)`).
pub async fn get_history(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<XpRowView>>, ApiError> {
    let limit = page.limit()?;
    let after: Option<i64> = page.after()?;
    let rows: Vec<XpRowView> = sqlx::query_as(
        "SELECT id, source, ref_id, requested, amount, xp_after, level_after, at
         FROM xp_ledger
         WHERE character_id = $1 AND ($2::bigint IS NULL OR id < $2)
         ORDER BY id DESC LIMIT $3",
    )
    .bind(player.character.id)
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    Ok(Json(Page::from_rows(rows, limit, |r| r.id)))
}

/// This module's routes: `GET /api/progression`, `GET /api/xp/history`.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new()
        .route("/progression", get(get_progression))
        .route("/xp/history", get(get_history))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grants_report_level_ups_and_gains() {
        let g = XpGrant {
            source: XpSource::DuelWin,
            requested: 50,
            granted: 20,
            capped: true,
            duplicate: false,
            xp: 210,
            level_before: 1,
            level: 2,
        };
        assert!(g.leveled_up());
        assert_eq!(
            g.gain(),
            XpGain {
                source: XpSource::DuelWin,
                amount: 20,
                capped: true
            }
        );
        assert_eq!(g.progress().level, 2);
        let s = SchoolState {
            xp: 0,
            ..Default::default()
        };
        assert_eq!((s.level(), s.slots()), (1, 3));
    }

    /// The curve: every level's first XP gives it, one less the level
    /// before; the cap is level 20.
    #[test]
    fn the_level_curve() {
        let p = &shipped().progression;
        assert_eq!(p.levels.len(), usize::from(protocol::LEVEL_MAX));
        for (k, &start) in p.levels.iter().enumerate() {
            let level = k as u8 + 1;
            assert_eq!(ProgressionView::new(start).level, level);
            if start > 0 {
                assert_eq!(ProgressionView::new(start - 1).level, level - 1);
            }
        }
        let top = ProgressionView::new(u32::MAX);
        assert_eq!((top.level, top.next_level_xp), (20, None));
        let fresh = ProgressionView::new(0);
        assert_eq!(fresh.next_level_xp, Some(p.levels[1]));
        assert_eq!(fresh.loadout_slots, 3);
        assert_eq!(ProgressionView::new(p.levels[4]).loadout_slots, 4);
        assert_eq!(ProgressionView::new(p.levels[9]).loadout_slots, 5);
    }
}
