//! Potion effects in the database (owned by the crafting
//! builder): the `active_effects` rows that let an effect survive a relog,
//! Chatterbrew's scrambling, and the cleanup of expired rows.
//!
//! The live state is the world's (`realtime::ext::ActiveEffect`, applied
//! with `WorldCtx::apply_effect`); the gateway loads [`load_active`] into
//! `Joining.effects` at sign-in. The potions code writes a row with
//! [`store`] in the drink's transaction.
//!
//! The Divination fortune (`EffectKind::Fortune`, item 0) keeps
//! which fortune in `active_effects.fortune`; [`active_fortune`] is how the
//! HTTP side (crafting's Quick Hands, a class's Keen Mind) asks for it, and
//! [`fortune_effect`] builds one for the world.
//!
//! Route: `GET /api/effects` → `{ effects: [{ kind, item_def, fortune,
//! started_at, expires_at }] }` (the character's own, for the HUD after a
//! reload; `fortune` is null for a potion).
//! Background job: [`cleanup_expired`] (every minute, `main.rs`).

use std::time::{Duration, Instant};

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{EffectKind, FortuneKind, VisibleEffect};
use serde::Serialize;
use sqlx::PgConnection;
use sqlx::types::Json as DbJson;

use crate::AppState;
use crate::characters::Player;
use crate::http::ApiError;
use crate::realtime::ext::ActiveEffect;

/// An `active_effects` row with the seconds it has left.
#[derive(sqlx::FromRow)]
struct EffectRow {
    kind: String,
    visible: Option<DbJson<VisibleEffect>>,
    speed_pct: i16,
    item_def: i32,
    fortune: Option<String>,
    remaining_secs: f64,
}

/// The character's effects that have not expired, as the world keeps them
/// (`until` counted from now).
pub async fn load_active(
    pool: &sqlx::PgPool,
    character_id: i64,
) -> Result<Vec<ActiveEffect>, sqlx::Error> {
    let rows: Vec<EffectRow> = sqlx::query_as(
        "SELECT kind, visible, speed_pct, item_def, fortune,
                EXTRACT(EPOCH FROM (expires_at - now()))::float8 AS remaining_secs
         FROM active_effects WHERE character_id = $1 AND expires_at > now()
         ORDER BY kind",
    )
    .bind(character_id)
    .fetch_all(pool)
    .await?;
    let now = Instant::now();
    Ok(rows
        .into_iter()
        .filter_map(|r| {
            let kind = EffectKind::from_id(&r.kind)?;
            // Spells L20: spell effects are never stored (and never restored).
            if kind.is_spell() {
                return None;
            }
            let fortune = r.fortune.as_deref().and_then(FortuneKind::from_id);
            if (kind == EffectKind::Fortune) != fortune.is_some() {
                return None;
            }
            Some(ActiveEffect {
                fortune,
                kind,
                item_def: u16::try_from(r.item_def).ok()?,
                // A disguise copies a look: migrate it like a stored one.
                visible: r.visible.map(|v| match v.0 {
                    VisibleEffect::Disguise { appearance } => VisibleEffect::Disguise {
                        appearance: crate::catalog::migrate_look(appearance),
                    },
                    other => other,
                }),
                speed_pct: u16::try_from(r.speed_pct).unwrap_or(protocol::SPEED_PCT_NORMAL),
                until: now + Duration::from_secs_f64(r.remaining_secs.max(0.0)),
            })
        })
        .collect())
}

/// Writes (or replaces) the row for `effect`, lasting `duration` from now,
/// and deletes the row of the kind it excludes (Diminishing Draught ↔ Enlarging Draught). Runs
/// in the drink's transaction.
pub async fn store(
    conn: &mut PgConnection,
    character_id: i64,
    effect: &ActiveEffect,
    duration: Duration,
) -> Result<(), sqlx::Error> {
    if let Some(excluded) = effect.kind.excludes() {
        remove(&mut *conn, character_id, excluded).await?;
    }
    sqlx::query(
        "INSERT INTO active_effects (character_id, kind, visible, speed_pct, item_def,
                                     fortune, started_at, expires_at)
         VALUES ($1, $2, $3, $4, $5, $7, now(), now() + make_interval(secs => $6))
         ON CONFLICT (character_id, kind) DO UPDATE
         SET visible = EXCLUDED.visible, speed_pct = EXCLUDED.speed_pct,
             item_def = EXCLUDED.item_def, fortune = EXCLUDED.fortune,
             started_at = EXCLUDED.started_at, expires_at = EXCLUDED.expires_at",
    )
    .bind(character_id)
    .bind(effect.kind.id())
    .bind(effect.visible.map(DbJson))
    .bind(i16::try_from(effect.speed_pct).unwrap_or(100))
    .bind(i32::from(effect.item_def))
    .bind(duration.as_secs_f64())
    .bind(effect.fortune.map(FortuneKind::id))
    .execute(conn)
    .await?;
    Ok(())
}

/// The character's running fortune, if any (in the caller's transaction).
pub async fn active_fortune(
    conn: &mut PgConnection,
    character_id: i64,
) -> Result<Option<FortuneKind>, sqlx::Error> {
    let id: Option<String> = sqlx::query_scalar(
        "SELECT fortune FROM active_effects
         WHERE character_id = $1 AND kind = 'fortune' AND expires_at > now()",
    )
    .bind(character_id)
    .fetch_optional(conn)
    .await?
    .flatten();
    Ok(id.as_deref().and_then(FortuneKind::from_id))
}

/// How long a fortune lasts (`data/economy.json` `effects.fortune`).
pub fn fortune_duration() -> Duration {
    let secs = crate::catalog::shipped()
        .economy
        .effects
        .duration_secs(EffectKind::Fortune);
    Duration::from_secs(u64::from(secs))
}

/// A fortune effect of `kind` running until `until` (nobody else sees it).
pub fn fortune_effect(kind: FortuneKind, until: Instant) -> ActiveEffect {
    ActiveEffect {
        kind: EffectKind::Fortune,
        item_def: 0,
        fortune: Some(kind),
        visible: None,
        speed_pct: protocol::SPEED_PCT_NORMAL,
        until,
    }
}

/// Deletes the character's row of `kind`, if any (an effect ended early).
pub async fn remove(
    conn: &mut PgConnection,
    character_id: i64,
    kind: EffectKind,
) -> Result<(), sqlx::Error> {
    sqlx::query("DELETE FROM active_effects WHERE character_id = $1 AND kind = $2")
        .bind(character_id)
        .bind(kind.id())
        .execute(conn)
        .await?;
    Ok(())
}

/// Deletes expired effect rows and find cooldowns that have passed.
pub async fn cleanup_expired(pool: &sqlx::PgPool) -> Result<u64, sqlx::Error> {
    let effects = sqlx::query("DELETE FROM active_effects WHERE expires_at <= now()")
        .execute(pool)
        .await?
        .rows_affected();
    let cooldowns = sqlx::query("DELETE FROM find_cooldowns WHERE ready_at <= now()")
        .execute(pool)
        .await?
        .rows_affected();
    Ok(effects + cooldowns)
}

// ------------------------------------------------------------ Chatterbrew

/// A small deterministic generator (xorshift64*), so a line babbles the
/// same way for everyone who receives it and tests can pin the output.
struct Xorshift(u64);

impl Xorshift {
    fn new(seed: u64) -> Self {
        // Never zero (xorshift's fixed point); the constant spreads small seeds.
        Xorshift(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1)
    }

    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
}

/// Chatterbrew: `text` as it comes out of the drinker's mouth,
/// scrambled deterministically from `seed` (the world passes the line's
/// `client_seq`). The letters of each word are shuffled among themselves;
/// spaces, digits and punctuation stay where they are, and each ASCII
/// letter position keeps its case, so the line has exactly the same length
/// in bytes (it still fits `CHAT_MAX_LEN`) and keeps its shape.
pub fn babble(text: &str, seed: u64) -> String {
    let mut rng = Xorshift::new(seed);
    let mut out = String::with_capacity(text.len());
    let mut word: Vec<char> = Vec::new();
    let flush = |word: &mut Vec<char>, out: &mut String, rng: &mut Xorshift| {
        if word.is_empty() {
            return;
        }
        let upper: Vec<bool> = word.iter().map(char::is_ascii_uppercase).collect();
        let mut letters: Vec<char> = word.iter().map(|c| c.to_ascii_lowercase()).collect();
        let original = letters.clone();
        for i in (1..letters.len()).rev() {
            letters.swap(i, rng.below(i + 1));
        }
        // A shuffle that changed nothing (short words) rotates by one, so
        // every word with two different letters comes out different.
        if letters == original {
            letters.rotate_left(1);
        }
        for (c, up) in letters.into_iter().zip(upper) {
            out.push(if up { c.to_ascii_uppercase() } else { c });
        }
        word.clear();
    };
    for c in text.chars() {
        if c.is_alphabetic() {
            word.push(c);
        } else {
            flush(&mut word, &mut out, &mut rng);
            out.push(c);
        }
    }
    flush(&mut word, &mut out, &mut rng);
    out
}

/// [`babble`], but never a line the chat filter would refuse: shuffled
/// letters could spell a word the player never wrote. `acceptable` is the
/// world's filter; a few other shuffles are tried, then every letter
/// becomes a mumbled `m` (same length).
pub fn babble_checked(text: &str, seed: u64, acceptable: impl Fn(&str) -> bool) -> String {
    for attempt in 0..4 {
        let line = babble(text, seed.wrapping_add(attempt));
        if acceptable(&line) {
            return line;
        }
    }
    text.chars()
        .map(|c| match c {
            c if c.is_ascii_uppercase() => 'M',
            c if c.is_ascii_alphabetic() => 'm',
            // Non-ASCII letters keep their byte length: `m` would shorten
            // the line, never lengthen it, so it still fits.
            c if c.is_alphabetic() => 'm',
            c => c,
        })
        .collect()
}

// ------------------------------------------------------------ HTTP

#[derive(Debug, Serialize, sqlx::FromRow)]
pub struct EffectView {
    pub kind: String,
    pub item_def: i32,
    /// Which fortune, for a `fortune` effect.
    pub fortune: Option<String>,
    pub started_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
pub struct EffectsView {
    pub effects: Vec<EffectView>,
}

/// `GET /api/effects`: the character's active effects, soonest to end first
/// (at most one per kind, so never more than ten: no page needed).
pub async fn list(
    State(state): State<AppState>,
    player: Player,
) -> Result<Json<EffectsView>, ApiError> {
    let effects: Vec<EffectView> = sqlx::query_as(
        "SELECT kind, item_def, fortune, started_at, expires_at FROM active_effects
         WHERE character_id = $1 AND expires_at > now()
         ORDER BY expires_at, kind",
    )
    .bind(player.character.id)
    .fetch_all(&state.pool)
    .await?;
    Ok(Json(EffectsView { effects }))
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new().route("/effects", get(list))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn babble_shuffles_letters_and_keeps_the_shape() {
        let line = "Hello there, Professor! See you at 9?";
        let out = babble(line, 7);
        assert_ne!(out, line);
        assert_eq!(out.len(), line.len());
        assert_eq!(babble(line, 7), out, "deterministic");
        for (a, b) in line.chars().zip(out.chars()) {
            // Non-letters stay put; letters keep their case.
            assert_eq!(a.is_alphabetic(), b.is_alphabetic(), "{line} / {out}");
            if a.is_alphabetic() {
                assert_eq!(a.is_ascii_uppercase(), b.is_ascii_uppercase());
            } else {
                assert_eq!(a, b);
            }
        }
        // Each word keeps its letters.
        for (w, v) in line.split(' ').zip(out.split(' ')) {
            let sorted = |s: &str| {
                let mut c: Vec<char> = s.to_lowercase().chars().collect();
                c.sort_unstable();
                c
            };
            assert_eq!(sorted(w), sorted(v));
        }
        assert_eq!(babble("", 1), "");
        assert_eq!(babble("ab", 1), "ba", "two-letter words always change");
        // Multi-byte letters keep the byte length.
        let accents = "Ça déjà vu, naïve café";
        assert_eq!(babble(accents, 3).len(), accents.len());
    }

    #[test]
    fn babble_never_says_what_the_filter_refuses() {
        // A filter that refuses everything but mumbling.
        let out = babble_checked("Hello Bob", 1, |t| {
            t.chars().all(|c| c == 'm' || c == 'M' || c == ' ')
        });
        assert_eq!(out, "Mmmmm Mmm");
        let ok = babble_checked("Hello Bob", 1, |_| true);
        assert_eq!(ok, babble("Hello Bob", 1));
        // The fallback is never longer than the line.
        let long = "é".repeat(100);
        assert!(babble_checked(&long, 1, |_| false).len() <= long.len());
    }
}
