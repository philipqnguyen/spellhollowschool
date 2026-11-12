//! Dorm persistence: the `dorms` row (created on first use), the placed
//! furniture (`dorm_placements`) and moving items between `inventory` and
//! `dorm` (`items.location`), always in one transaction with the placement.
//!
//! These run on tokio tasks spawned by the world (`realtime::jobs`); the world
//! serializes each room's operations, so a room never has two in flight.
//!
//! Locks follow the server-wide order: the owner's `characters` row first
//! ([`lock_owner`], as the wardrobe, outfits and friends do), then item rows.
//! Every transaction that touches a character's items therefore queues on
//! the character row before taking any item lock, so a dorm edit cannot
//! deadlock with a wardrobe change (or, later, a trade) of the same items.

use protocol::{DORM_ITEM_CAP, DormErrorCode, PlacedFurniture, QPos};
use sim_core::{Catalog, PlaceArea};
use sqlx::{PgConnection, PgExecutor, PgPool};

use super::rules;
use crate::inventory::{LOCATION_DORM, LOCATION_INVENTORY};
use crate::ledger::{self, ItemMove, ItemOp, Reason};

/// Why a store operation failed: a rule (sent to the player as `DormError`)
/// or the database (logged, sent as `Failed`).
#[derive(Debug)]
pub enum StoreError {
    Rule(DormErrorCode),
    Db(sqlx::Error),
}

impl From<sqlx::Error> for StoreError {
    fn from(e: sqlx::Error) -> Self {
        StoreError::Db(e)
    }
}

impl From<DormErrorCode> for StoreError {
    fn from(c: DormErrorCode) -> Self {
        StoreError::Rule(c)
    }
}

impl StoreError {
    /// The code the player is told.
    pub fn code(&self) -> DormErrorCode {
        match self {
            StoreError::Rule(c) => *c,
            StoreError::Db(_) => DormErrorCode::Failed,
        }
    }
}

/// Locks the owner's character row (`FOR NO KEY UPDATE`, like
/// `inventory::equip::lock_appearance`): the first lock of every item change.
async fn lock_owner(conn: &mut PgConnection, owner: i64) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT 1 FROM characters WHERE id = $1 FOR NO KEY UPDATE")
        .bind(owner)
        .execute(conn)
        .await?;
    Ok(())
}

/// Whether `owner`'s room is open to friends (`false` before first use).
pub async fn open_to_friends(db: impl PgExecutor<'_>, owner: i64) -> Result<bool, sqlx::Error> {
    let open: Option<bool> =
        sqlx::query_scalar("SELECT open_to_friends FROM dorms WHERE owner_id = $1")
            .bind(owner)
            .fetch_optional(db)
            .await?;
    Ok(open.unwrap_or(false))
}

/// Stores the open-to-friends setting, creating the room's row if needed.
pub async fn set_open(db: impl PgExecutor<'_>, owner: i64, open: bool) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO dorms (owner_id, open_to_friends) VALUES ($1, $2)
         ON CONFLICT (owner_id) DO UPDATE SET open_to_friends = $2, updated_at = now()",
    )
    .bind(owner)
    .bind(open)
    .execute(db)
    .await?;
    Ok(())
}

#[derive(sqlx::FromRow)]
struct PlacementRow {
    item_id: i64,
    item_def: i32,
    dye_primary: i16,
    dye_secondary: i16,
    dye_trim: i16,
    pos_x: i32,
    pos_y: i32,
    pos_z: i32,
    yaw: i16,
}

impl PlacementRow {
    fn into_placed(self) -> PlacedFurniture {
        // The columns' CHECK constraints keep these in range.
        let byte = |v: i16| u8::try_from(v).unwrap_or(0);
        PlacedFurniture {
            item_id: self.item_id,
            item_def: u16::try_from(self.item_def).unwrap_or(0),
            dyes: [
                byte(self.dye_primary),
                byte(self.dye_secondary),
                byte(self.dye_trim),
            ],
            pos: QPos {
                x: self.pos_x,
                y: self.pos_y,
                z: self.pos_z,
            },
            yaw: byte(self.yaw),
        }
    }
}

/// Every item placed in `owner`'s room, oldest first.
pub async fn load_room(
    db: impl PgExecutor<'_>,
    owner: i64,
) -> Result<Vec<PlacedFurniture>, sqlx::Error> {
    let rows: Vec<PlacementRow> = sqlx::query_as(
        "SELECT p.item_id, i.item_def, i.dye_primary, i.dye_secondary, i.dye_trim,
                p.pos_x, p.pos_y, p.pos_z, p.yaw
         FROM dorm_placements p JOIN items i ON i.id = p.item_id AND i.owner_id = p.owner_id
         WHERE p.owner_id = $1
         ORDER BY p.placed_at, p.item_id",
    )
    .bind(owner)
    .fetch_all(db)
    .await?;
    Ok(rows.into_iter().map(PlacementRow::into_placed).collect())
}

/// Places an inventory furniture item of `owner` in their room. `areas` are
/// the room's areas when the edit started (`rules::room_areas`), `way` what it
/// must leave open (`rules::Way`: solid furniture).
#[allow(clippy::too_many_arguments)]
pub async fn place(
    pool: &PgPool,
    catalog: &Catalog,
    owner: i64,
    item_id: i64,
    pos: QPos,
    yaw: u8,
    areas: &[PlaceArea],
    way: &rules::Way,
) -> Result<PlacedFurniture, StoreError> {
    let mut tx = pool.begin().await?;
    lock_owner(&mut tx, owner).await?;
    let item: Option<(i32, i16, i16, i16, String)> = sqlx::query_as(
        "SELECT item_def, dye_primary, dye_secondary, dye_trim, location
         FROM items WHERE id = $1 AND owner_id = $2 FOR UPDATE",
    )
    .bind(item_id)
    .bind(owner)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((def, p, s, t, location)) = item else {
        return Err(DormErrorCode::ItemNotFound.into());
    };
    let item_def = u16::try_from(def).map_err(|_| DormErrorCode::NotFurniture)?;
    rules::shape_of(catalog, item_def)?;
    if location != LOCATION_INVENTORY {
        return Err(DormErrorCode::ItemUnavailable.into());
    }
    rules::check(catalog, item_def, areas, pos, yaw)?;
    way.check(catalog, item_def, item_id, pos, yaw)?;

    // The room row, locked so the count below cannot race another placement.
    sqlx::query("INSERT INTO dorms (owner_id) VALUES ($1) ON CONFLICT (owner_id) DO NOTHING")
        .bind(owner)
        .execute(&mut *tx)
        .await?;
    sqlx::query("SELECT 1 FROM dorms WHERE owner_id = $1 FOR UPDATE")
        .bind(owner)
        .execute(&mut *tx)
        .await?;
    let placed: i64 =
        sqlx::query_scalar("SELECT count(*) FROM dorm_placements WHERE owner_id = $1")
            .bind(owner)
            .fetch_one(&mut *tx)
            .await?;
    if placed >= i64::from(DORM_ITEM_CAP) {
        return Err(DormErrorCode::CapReached.into());
    }
    sqlx::query("UPDATE items SET location = $3 WHERE id = $1 AND owner_id = $2")
        .bind(item_id)
        .bind(owner)
        .bind(LOCATION_DORM)
        .execute(&mut *tx)
        .await?;
    sqlx::query(
        "INSERT INTO dorm_placements (item_id, owner_id, pos_x, pos_y, pos_z, yaw)
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(item_id)
    .bind(owner)
    .bind(pos.x)
    .bind(pos.y)
    .bind(pos.z)
    .bind(i16::from(yaw))
    .execute(&mut *tx)
    .await?;
    record_move(
        &mut tx,
        owner,
        item_id,
        item_def,
        LOCATION_INVENTORY,
        LOCATION_DORM,
    )
    .await?;
    tx.commit().await?;
    let byte = |v: i16| u8::try_from(v).unwrap_or(0);
    Ok(PlacedFurniture {
        item_id,
        item_def,
        dyes: [byte(p), byte(s), byte(t)],
        pos,
        yaw,
    })
}

/// Moves or turns a placed item (already checked against the room).
pub async fn move_item(
    db: impl PgExecutor<'_>,
    owner: i64,
    item_id: i64,
    pos: QPos,
    yaw: u8,
) -> Result<(), StoreError> {
    let res = sqlx::query(
        "UPDATE dorm_placements SET pos_x = $3, pos_y = $4, pos_z = $5, yaw = $6, updated_at = now()
         WHERE item_id = $1 AND owner_id = $2",
    )
    .bind(item_id)
    .bind(owner)
    .bind(pos.x)
    .bind(pos.y)
    .bind(pos.z)
    .bind(i16::from(yaw))
    .execute(db)
    .await?;
    if res.rows_affected() == 0 {
        return Err(DormErrorCode::ItemNotFound.into());
    }
    Ok(())
}

/// Takes a placed item back into the inventory.
pub async fn remove(pool: &PgPool, owner: i64, item_id: i64) -> Result<(), StoreError> {
    let mut tx = pool.begin().await?;
    lock_owner(&mut tx, owner).await?;
    let res = sqlx::query("DELETE FROM dorm_placements WHERE item_id = $1 AND owner_id = $2")
        .bind(item_id)
        .bind(owner)
        .execute(&mut *tx)
        .await?;
    if res.rows_affected() == 0 {
        return Err(DormErrorCode::ItemNotFound.into());
    }
    let def: Option<i32> = sqlx::query_scalar(
        "UPDATE items SET location = $3 WHERE id = $1 AND owner_id = $2 AND location = $4
         RETURNING item_def",
    )
    .bind(item_id)
    .bind(owner)
    .bind(LOCATION_INVENTORY)
    .bind(LOCATION_DORM)
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(def) = def {
        let item_def = u16::try_from(def).unwrap_or(0);
        record_move(
            &mut tx,
            owner,
            item_id,
            item_def,
            LOCATION_DORM,
            LOCATION_INVENTORY,
        )
        .await?;
    }
    tx.commit().await?;
    Ok(())
}

/// The item ledger's row for a placement or a take-back.
async fn record_move(
    conn: &mut PgConnection,
    owner: i64,
    item_id: i64,
    item_def: u16,
    from: &str,
    to: &str,
) -> Result<(), sqlx::Error> {
    let m = ItemMove::new(item_id, item_def, ItemOp::Move, 1, Reason::Dorm)
        .from(owner, from)
        .to(owner, to);
    ledger::record_item_move(conn, &m)
        .await
        .map_err(|e| match e {
            ledger::LedgerError::Db(db) => db,
            other => sqlx::Error::Protocol(other.to_string()),
        })
}
