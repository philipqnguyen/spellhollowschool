//! The social hotbar's storage (protocol v16): `characters.hotbar`, ten
//! spell ids or NULLs in key order; NULL for the column while the character
//! never set one.
//!
//! - [`load`]: at join (`progression::load_school`), the stored bar trimmed to
//!   what is still allowed, or the default (learned hotbar spells in
//!   spellbook order, `Spells::default_hotbar`).
//! - [`add_learned`]: inside `progression::learn_spells`, so a spell learned
//!   anywhere, online or not, goes into the first empty slot of a stored bar
//!   (a bar never set already shows every learned spell by default). Only
//!   then: a spell the player took off stays off.
//! - [`save`]: the world's changes (`SetHotbar`, spells learned while
//!   online), written by the position saver (`realtime::persist`) in the
//!   order the world made them.
//!
//! The world keeps an online character's bar (`SchoolState::hotbar`), checks
//! `SetHotbar` against it (`Spells::check_hotbar`) and adds spells learned
//! while online itself. See docs/architecture.md, "Spells and dueling".

use protocol::{HOTBAR_SLOTS, SpellId};
use sim_core::spells::Hotbar;
use sqlx::{PgConnection, PgPool};

use crate::catalog::shipped;

/// A hotbar as the column holds it.
pub fn to_db(bar: &Hotbar) -> Vec<Option<i32>> {
    bar.iter().map(|s| s.map(i32::from)).collect()
}

fn from_db(stored: Vec<Option<i32>>) -> Vec<Option<SpellId>> {
    stored
        .into_iter()
        .map(|s| s.and_then(|id| SpellId::try_from(id).ok()))
        .collect()
}

/// A hotbar on the wire (`Spellbook.hotbar`, `Hotbar.slots`).
pub fn to_wire(bar: &Hotbar) -> Vec<Option<SpellId>> {
    bar.to_vec()
}

/// Character `character_id`'s hotbar, given its learned spells.
pub async fn load(
    conn: &mut PgConnection,
    character_id: i64,
    learned: &[SpellId],
) -> Result<Hotbar, sqlx::Error> {
    let stored: Option<Vec<Option<i32>>> =
        sqlx::query_scalar("SELECT hotbar FROM characters WHERE id = $1")
            .bind(character_id)
            .fetch_one(&mut *conn)
            .await?;
    let spells = &shipped().spells;
    Ok(match stored {
        Some(s) => spells.trim_hotbar(learned, &from_db(s)),
        None => spells.default_hotbar(learned),
    })
}

/// Spells `new` were just learned: each hotbar spell among them goes into
/// the first empty slot of the stored bar, while there is room. Locks the
/// character's row (`FOR NO KEY UPDATE`; the lock order's first step).
pub async fn add_learned(
    conn: &mut PgConnection,
    character_id: i64,
    new: &[SpellId],
) -> Result<(), sqlx::Error> {
    let spells = &shipped().spells;
    if !new
        .iter()
        .any(|&id| spells.get(id).is_some_and(|s| s.on_hotbar()))
    {
        return Ok(());
    }
    let stored: Option<Vec<Option<i32>>> =
        sqlx::query_scalar("SELECT hotbar FROM characters WHERE id = $1 FOR NO KEY UPDATE")
            .bind(character_id)
            .fetch_one(&mut *conn)
            .await?;
    // Never set: the default already lists every learned spell.
    let Some(stored) = stored else {
        return Ok(());
    };
    let mut bar: Hotbar = [None; HOTBAR_SLOTS];
    for (slot, id) in bar.iter_mut().zip(from_db(stored)) {
        *slot = id;
    }
    if spells.hotbar_add(&mut bar, new) {
        sqlx::query("UPDATE characters SET hotbar = $2 WHERE id = $1")
            .bind(character_id)
            .bind(to_db(&bar))
            .execute(&mut *conn)
            .await?;
    }
    Ok(())
}

/// Stores the latest hotbar of each character (one statement each: they
/// change only when a player rearranges theirs or learns a spell).
pub async fn save(pool: &PgPool, rows: &[(i64, Hotbar)]) -> Result<(), sqlx::Error> {
    for (character_id, bar) in rows {
        sqlx::query("UPDATE characters SET hotbar = $2 WHERE id = $1")
            .bind(character_id)
            .bind(to_db(bar))
            .execute(pool)
            .await?;
    }
    Ok(())
}
