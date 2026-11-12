//! Equipment changes inside a transaction, shared by `POST /api/equipment`,
//! dyeing and wearing a saved outfit.
//!
//! The protocol: [`lock_appearance`] locks the character row first (so
//! concurrent wardrobe requests of one character serialize and versions never
//! skip or repeat), then any number of [`set_slot`] calls, then one
//! [`refresh_appearance`] that rebuilds the worn outfit from `equipment` and
//! bumps `appearance_version` only if the look changed. The handler commits
//! and then tells the world once.

use protocol::{Appearance, GearSlot, Outfit, Worn};
use sim_core::catalog::{Wear, can_wear};
use sqlx::PgConnection;
use sqlx::types::Json;

use super::{LOCATION_EQUIPPED, LOCATION_INVENTORY, dyes_from_db};
use crate::AppState;
use crate::catalog::shipped;
use crate::http::ApiError;
use crate::ledger::{self, ItemMove, ItemOp, LedgerError, Reason};

/// A character's look as stored, locked for this transaction.
#[derive(Debug, Clone, Copy)]
pub(crate) struct LockedLook {
    pub appearance: Appearance,
    pub version: u32,
}

impl LockedLook {
    /// The wear class of this look's body ([`body_wear`]).
    pub fn wear(&self) -> Wear {
        body_wear(&self.appearance)
    }
}

/// The wear class of `appearance`'s body type (the first
/// body's for an unknown index, as [`lock_appearance`] defaults to it).
pub(crate) fn body_wear(appearance: &Appearance) -> Wear {
    let bodies = &shipped().creator.body_types;
    bodies
        .get(usize::from(appearance.body_type))
        .or(bodies.first())
        .map_or(Wear::Male, |b| b.wear)
}

/// `403 wear_restricted`, "That is female only." (or male
/// only): a garment the body may not wear, whichever way it is put on.
pub(crate) fn wear_restricted(item: Wear) -> ApiError {
    ApiError::forbidden("wear_restricted", wear_message(item))
}

/// "That is female only." / "That is male only.": the refusals' text.
pub(crate) fn wear_message(item: Wear) -> String {
    format!("That is {}.", item.label().to_lowercase())
}

/// The class `item` is restricted to if `body` may not wear it.
pub(crate) fn forbidden_for(body: Wear, item: &sim_core::ItemDef) -> Option<Wear> {
    item.wear.filter(|_| !can_wear(body, item.wear))
}

/// Locks the character row (`FOR NO KEY UPDATE`, which still lets other
/// transactions insert rows referring to the character) and reads its look.
pub(crate) async fn lock_appearance(
    conn: &mut PgConnection,
    character_id: i64,
) -> Result<LockedLook, sqlx::Error> {
    let (appearance, version): (Option<Json<Appearance>>, i32) = sqlx::query_as(
        "SELECT appearance, appearance_version FROM characters WHERE id = $1 FOR NO KEY UPDATE",
    )
    .bind(character_id)
    .fetch_one(conn)
    .await?;
    Ok(LockedLook {
        appearance: appearance
            .map(|j| crate::catalog::migrate_look(j.0))
            .unwrap_or_else(|| shipped().creator.default_appearance(0)),
        version: u32::try_from(version).unwrap_or(0),
    })
}

/// Why an item cannot go into a slot.
#[derive(Debug)]
pub(crate) enum SlotError {
    /// No such item, or not the character's (`404 item_not_found`).
    NotFound,
    /// Not clothing for this slot (`400 wrong_slot`).
    WrongSlot,
    /// Placed in the dorm (`409 item_unavailable`).
    Unavailable,
    /// The item needs a higher player level (`403
    /// level_too_low`). Items already worn stay worn.
    LevelTooLow {
        required: u8,
    },
    /// Restricted to the other wear class (`403
    /// wear_restricted`). Only wearing is restricted, never owning.
    WearRestricted(Wear),
    Db(sqlx::Error),
}

impl From<sqlx::Error> for SlotError {
    fn from(e: sqlx::Error) -> Self {
        SlotError::Db(e)
    }
}

impl From<SlotError> for ApiError {
    fn from(e: SlotError) -> Self {
        match e {
            SlotError::NotFound => item_not_found(),
            SlotError::WrongSlot => {
                ApiError::bad_request("wrong_slot", "That item is not worn in that slot.")
            }
            SlotError::Unavailable => item_unavailable(),
            SlotError::LevelTooLow { required } => ApiError::forbidden(
                "level_too_low",
                format!("You need to be level {required} to wear that."),
            ),
            SlotError::WearRestricted(w) => wear_restricted(w),
            SlotError::Db(e) => e.into(),
        }
    }
}

pub(crate) fn item_not_found() -> ApiError {
    ApiError::not_found("item_not_found", "You do not have that item.")
}

pub(crate) fn item_unavailable() -> ApiError {
    ApiError::conflict(
        "item_unavailable",
        "That item is placed in your dorm room, travelling by owl or listed on the Exchange.",
    )
}

/// Puts `item_id` into `slot` (or empties it for `None`), returning the
/// previous item there to the inventory. Checks ownership, slot and location
/// before changing anything, so a failure leaves the slot as it was.
/// Requires [`lock_appearance`] earlier in the transaction; `body` is that
/// look's wear class ([`LockedLook::wear`]).
pub(crate) async fn set_slot(
    conn: &mut PgConnection,
    character_id: i64,
    body: Wear,
    slot: GearSlot,
    item_id: Option<i64>,
) -> Result<(), SlotError> {
    if let Some(id) = item_id {
        let item: Option<(i32, String)> = sqlx::query_as(
            "SELECT item_def, location FROM items WHERE id = $1 AND owner_id = $2 FOR UPDATE",
        )
        .bind(id)
        .bind(character_id)
        .fetch_optional(&mut *conn)
        .await?;
        let Some((def, location)) = item else {
            return Err(SlotError::NotFound);
        };
        let item_def = u16::try_from(def)
            .ok()
            .and_then(|d| shipped().catalog.clothing(d, slot));
        let Some(item_def) = item_def else {
            return Err(SlotError::WrongSlot);
        };
        // A garment restricted to the other wear class is
        // refused here, when putting it on (never on buying or owning it).
        if let Some(w) = forbidden_for(body, item_def) {
            return Err(SlotError::WearRestricted(w));
        }
        // A required level is checked when putting an item on
        // (never on buying or holding it); one already worn stays worn.
        if item_def.required_level > 1 && location != LOCATION_EQUIPPED {
            let level: i16 = sqlx::query_scalar("SELECT level FROM characters WHERE id = $1")
                .bind(character_id)
                .fetch_one(&mut *conn)
                .await?;
            if level < i16::from(item_def.required_level) {
                return Err(SlotError::LevelTooLow {
                    required: item_def.required_level,
                });
            }
        }
        // Placed in the dorm, travelling by owl or listed on the Exchange.
        if location != LOCATION_INVENTORY && location != LOCATION_EQUIPPED {
            return Err(SlotError::Unavailable);
        }
    }
    // Free the slot, and the item if it is somehow worn elsewhere.
    let freed: Vec<i64> = sqlx::query_scalar(
        "DELETE FROM equipment
         WHERE character_id = $1 AND (slot = $2 OR item_id = $3)
         RETURNING item_id",
    )
    .bind(character_id)
    .bind(slot.id())
    .bind(item_id)
    .fetch_all(&mut *conn)
    .await?;
    let taken_off: Vec<(i64, i32)> = sqlx::query_as(
        "UPDATE items SET location = $2 WHERE id = ANY($1) AND location = $3
         RETURNING id, item_def",
    )
    .bind(&freed)
    .bind(LOCATION_INVENTORY)
    .bind(LOCATION_EQUIPPED)
    .fetch_all(&mut *conn)
    .await?;
    let mut moves: Vec<ItemMove<'_>> = taken_off
        .iter()
        .map(|&(id, def)| {
            ItemMove::new(id, def_u16(def), ItemOp::Move, 1, Reason::Equip)
                .from(character_id, LOCATION_EQUIPPED)
                .to(character_id, LOCATION_INVENTORY)
        })
        .collect();
    if let Some(id) = item_id {
        sqlx::query("INSERT INTO equipment (character_id, slot, item_id) VALUES ($1, $2, $3)")
            .bind(character_id)
            .bind(slot.id())
            .bind(id)
            .execute(&mut *conn)
            .await?;
        let def: i32 =
            sqlx::query_scalar("UPDATE items SET location = $2 WHERE id = $1 RETURNING item_def")
                .bind(id)
                .bind(LOCATION_EQUIPPED)
                .fetch_one(&mut *conn)
                .await?;
        moves.push(
            ItemMove::new(id, def_u16(def), ItemOp::Move, 1, Reason::Equip)
                .from(character_id, LOCATION_INVENTORY)
                .to(character_id, LOCATION_EQUIPPED),
        );
    }
    ledger::record_item_moves(conn, &moves)
        .await
        .map_err(|e| match e {
            LedgerError::Db(db) => SlotError::Db(db),
            other => SlotError::Db(sqlx::Error::Protocol(other.to_string())),
        })?;
    Ok(())
}

fn def_u16(def: i32) -> u16 {
    u16::try_from(def).unwrap_or(0)
}

/// The outfit `equipment` describes: each slot's item definition and dyes.
pub(crate) async fn worn_outfit(
    conn: &mut PgConnection,
    character_id: i64,
) -> Result<Outfit, sqlx::Error> {
    let rows: Vec<(String, i32, i16, i16, i16, i16)> = sqlx::query_as(
        "SELECT e.slot, i.item_def, i.dye_primary, i.dye_secondary, i.dye_trim, i.finish
         FROM equipment e JOIN items i ON i.id = e.item_id
         WHERE e.character_id = $1",
    )
    .bind(character_id)
    .fetch_all(conn)
    .await?;
    let mut outfit = Outfit::default();
    for (slot, def, p, s, t, finish) in rows {
        if let (Some(slot), Ok(item_def)) = (GearSlot::from_id(&slot), u16::try_from(def)) {
            let worn = Worn {
                item_def,
                dyes: dyes_from_db(p, s, t),
                finish: crate::items::finish_from_db(finish),
            };
            outfit.set(slot, Some(worn));
        }
    }
    Ok(outfit)
}

/// The look after this transaction's equipment changes, and whether it
/// differs from `before` (then it is stored with `appearance_version + 1`).
#[derive(Debug, Clone, Copy)]
pub(crate) struct NewLook {
    pub appearance: Appearance,
    pub version: u32,
    pub changed: bool,
}

/// Rebuilds the worn outfit from `equipment` and stores it if it changed.
pub(crate) async fn refresh_appearance(
    conn: &mut PgConnection,
    character_id: i64,
    before: LockedLook,
) -> Result<NewLook, sqlx::Error> {
    let outfit = worn_outfit(&mut *conn, character_id).await?;
    if outfit == before.appearance.outfit {
        return Ok(NewLook {
            appearance: before.appearance,
            version: before.version,
            changed: false,
        });
    }
    let appearance = Appearance {
        outfit,
        ..before.appearance
    };
    let version: i32 = sqlx::query_scalar(
        "UPDATE characters
         SET appearance = $2, appearance_version = appearance_version + 1, updated_at = now()
         WHERE id = $1 RETURNING appearance_version",
    )
    .bind(character_id)
    .bind(Json(appearance))
    .fetch_one(conn)
    .await?;
    Ok(NewLook {
        appearance,
        version: u32::try_from(version).unwrap_or(0),
        changed: true,
    })
}

/// After commit: tells the world about a changed look (once per request).
pub(crate) fn announce(state: &AppState, character_id: i64, look: &NewLook) {
    if let (true, Some(world)) = (look.changed, state.world()) {
        world.appearance_changed(character_id, look.appearance, look.version);
    }
}
