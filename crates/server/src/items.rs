//! Item-row operations shared by the economy and crafting code:
//! creating items, splitting and merging stacks, moving and using them
//! up, each recorded in the item ledger ([`crate::ledger`]) in the caller's
//! transaction.
//!
//! Rules every caller follows (docs/architecture.md, "Economy and crafting"):
//!
//! - Run inside one transaction per player action, after locking the
//!   `characters` row of every character whose items or Crowns change
//!   (`FOR NO KEY UPDATE`, in id order), then the item rows it reads with
//!   [`lock`] (ascending id).
//! - Clothing and furniture are one row each (`quantity` 1); stackable items
//!   (materials, dyes, finishes, potions, patterns) hold up to the catalog's
//!   `max_stack` per row. Rows of the same stack key (owner, location,
//!   definition, dyes, finish, maker) merge in the inventory; a stack is
//!   split when part of it moves.
//! - Equipped and placed items never move here: [`lock`] reports their
//!   location and the caller refuses them.

use protocol::Finish;
use sqlx::PgConnection;

use crate::catalog::shipped;
use crate::inventory::{LOCATION_INVENTORY, dyes_from_db};
use crate::ledger::{self, ItemMove, ItemOp, LedgerError, Reason};

/// `items.finish` values.
pub fn finish_to_db(f: Finish) -> i16 {
    match f {
        Finish::None => 0,
        Finish::Shimmer => 1,
        Finish::ColourShift => 2,
    }
}

pub fn finish_from_db(v: i16) -> Finish {
    match v {
        1 => Finish::Shimmer,
        2 => Finish::ColourShift,
        _ => Finish::None,
    }
}

/// A crafted item's maker's mark.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Maker {
    pub character_id: i64,
    pub name: String,
}

/// An item row as locked by [`lock`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LockedItem {
    pub id: i64,
    pub owner_id: i64,
    pub item_def: u16,
    pub dyes: [u8; 3],
    pub finish: Finish,
    pub quantity: u32,
    pub location: String,
    pub maker_id: Option<i64>,
    pub maker_name: Option<String>,
}

#[derive(sqlx::FromRow)]
struct Row {
    id: i64,
    owner_id: i64,
    item_def: i32,
    dye_primary: i16,
    dye_secondary: i16,
    dye_trim: i16,
    finish: i16,
    quantity: i32,
    location: String,
    maker_id: Option<i64>,
    maker_name: Option<String>,
}

impl From<Row> for LockedItem {
    fn from(r: Row) -> Self {
        LockedItem {
            id: r.id,
            owner_id: r.owner_id,
            item_def: u16::try_from(r.item_def).unwrap_or(0),
            dyes: dyes_from_db(r.dye_primary, r.dye_secondary, r.dye_trim),
            finish: finish_from_db(r.finish),
            quantity: u32::try_from(r.quantity).unwrap_or(0),
            location: r.location,
            maker_id: r.maker_id,
            maker_name: r.maker_name,
        }
    }
}

const COLUMNS: &str = "id, owner_id, item_def, dye_primary, dye_secondary, dye_trim, finish,
                       quantity, location, maker_id, maker_name";

/// Locks `owner`'s items among `ids` (`FOR UPDATE`, ascending id) and
/// returns them in that order. Ids that are not `owner`'s are left out, so
/// compare the lengths to find a missing item.
pub async fn lock(
    conn: &mut PgConnection,
    owner: i64,
    ids: &[i64],
) -> Result<Vec<LockedItem>, sqlx::Error> {
    let rows: Vec<Row> = sqlx::query_as(&format!(
        "SELECT {COLUMNS} FROM items WHERE owner_id = $1 AND id = ANY($2) ORDER BY id FOR UPDATE"
    ))
    .bind(owner)
    .bind(ids)
    .fetch_all(conn)
    .await?;
    Ok(rows.into_iter().map(LockedItem::from).collect())
}

/// A new item row's contents.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewItem {
    pub owner: i64,
    pub item_def: u16,
    pub dyes: [u8; 3],
    pub finish: Finish,
    pub quantity: u32,
    pub maker: Option<Maker>,
    pub location: &'static str,
}

impl NewItem {
    /// `quantity` units of `item_def` in its catalog dyes, no finish or
    /// maker, for `owner` at `location`.
    pub fn plain(owner: i64, item_def: u16, quantity: u32, location: &'static str) -> Self {
        let dyes = shipped()
            .catalog
            .get(item_def)
            .map_or([0; 3], |d| d.dyes.to_array());
        Self {
            owner,
            item_def,
            dyes,
            finish: Finish::None,
            quantity,
            maker: None,
            location,
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ItemError {
    #[error("unknown item definition {0}")]
    UnknownDef(u16),
    #[error("a row of item {0} holds at most {1}")]
    OverStack(u16, u32),
    #[error("the item does not hold that many units")]
    NotEnough,
    #[error(transparent)]
    Ledger(#[from] LedgerError),
    #[error(transparent)]
    Db(#[from] sqlx::Error),
}

/// Inserts one row (at most one stack's worth) and records `op` (`Create`
/// or `Grant`) with `reason`; returns its id.
pub async fn create(
    conn: &mut PgConnection,
    item: &NewItem,
    op: ItemOp,
    reason: Reason,
    ref_id: Option<i64>,
) -> Result<i64, ItemError> {
    let def = shipped()
        .catalog
        .get(item.item_def)
        .ok_or(ItemError::UnknownDef(item.item_def))?;
    if item.quantity == 0 || item.quantity > def.stack_limit() {
        return Err(ItemError::OverStack(item.item_def, def.stack_limit()));
    }
    let id: i64 = sqlx::query_scalar(
        "INSERT INTO items (owner_id, item_def, dye_primary, dye_secondary, dye_trim, finish,
                            quantity, maker_id, maker_name, location)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
    )
    .bind(item.owner)
    .bind(i32::from(item.item_def))
    .bind(i16::from(item.dyes[0]))
    .bind(i16::from(item.dyes[1]))
    .bind(i16::from(item.dyes[2]))
    .bind(finish_to_db(item.finish))
    .bind(i32::try_from(item.quantity).unwrap_or(i32::MAX))
    .bind(item.maker.as_ref().map(|m| m.character_id))
    .bind(item.maker.as_ref().map(|m| m.name.clone()))
    .bind(item.location)
    .fetch_one(&mut *conn)
    .await?;
    let mut m =
        ItemMove::new(id, item.item_def, op, item.quantity, reason).to(item.owner, item.location);
    m.ref_id = ref_id;
    ledger::record_item_move(conn, &m).await?;
    Ok(id)
}

/// Gives `item.quantity` new units (`ItemOp::Create`, e.g. a find, a craft
/// output, a shop delivery into mail): first topping up the owner's existing
/// stacks with the same key at `item.location` (oldest first), then new rows
/// of at most `max_stack` each. Returns every row that received units. For
/// non-stackable items, makes `quantity` rows of one.
pub async fn give(
    conn: &mut PgConnection,
    item: &NewItem,
    reason: Reason,
    ref_id: Option<i64>,
) -> Result<Vec<i64>, ItemError> {
    let def = shipped()
        .catalog
        .get(item.item_def)
        .ok_or(ItemError::UnknownDef(item.item_def))?;
    let limit = def.stack_limit();
    let mut left = item.quantity;
    let mut rows = Vec::new();
    if def.stackable {
        let maker_name = item.maker.as_ref().map(|m| m.name.as_str());
        let stacks = same_stacks(conn, item, maker_name, limit).await?;
        for (id, have) in stacks {
            if left == 0 {
                break;
            }
            let add = left.min(limit - have);
            sqlx::query("UPDATE items SET quantity = quantity + $2 WHERE id = $1")
                .bind(id)
                .bind(i32::try_from(add).unwrap_or(i32::MAX))
                .execute(&mut *conn)
                .await?;
            let mut m = ItemMove::new(id, item.item_def, ItemOp::Create, add, reason)
                .to(item.owner, item.location);
            m.ref_id = ref_id;
            ledger::record_item_move(conn, &m).await?;
            rows.push(id);
            left -= add;
        }
    }
    while left > 0 {
        let n = left.min(limit);
        let one = NewItem {
            quantity: n,
            ..item.clone()
        };
        rows.push(create(conn, &one, ItemOp::Create, reason, ref_id).await?);
        left -= n;
    }
    Ok(rows)
}

/// Rows of `item`'s stack key with room, oldest first, locked. The maker's
/// mark is compared by name as well as by character (`maker_name`): a mark
/// whose character is gone (`maker_id` set to NULL) must not merge into
/// unmarked units, nor unmarked units take on someone's name.
async fn same_stacks(
    conn: &mut PgConnection,
    item: &NewItem,
    maker_name: Option<&str>,
    limit: u32,
) -> Result<Vec<(i64, u32)>, sqlx::Error> {
    let rows: Vec<(i64, i32)> = sqlx::query_as(
        "SELECT id, quantity FROM items
         WHERE owner_id = $1 AND location = $2 AND item_def = $3
           AND dye_primary = $4 AND dye_secondary = $5 AND dye_trim = $6 AND finish = $7
           AND maker_id IS NOT DISTINCT FROM $8 AND maker_name IS NOT DISTINCT FROM $10
           AND quantity < $9
         ORDER BY id FOR UPDATE",
    )
    .bind(item.owner)
    .bind(item.location)
    .bind(i32::from(item.item_def))
    .bind(i16::from(item.dyes[0]))
    .bind(i16::from(item.dyes[1]))
    .bind(i16::from(item.dyes[2]))
    .bind(finish_to_db(item.finish))
    .bind(item.maker.as_ref().map(|m| m.character_id))
    .bind(i32::try_from(limit).unwrap_or(i32::MAX))
    .bind(maker_name)
    .fetch_all(conn)
    .await?;
    Ok(rows
        .into_iter()
        .map(|(id, q)| (id, u32::try_from(q).unwrap_or(0)))
        .collect())
}

/// Takes `quantity` units of a locked row so they can move on their own:
/// the row itself when that is all of it, else a new row split off it
/// (`ItemOp::Split`, same owner and location). Returns the row to move.
pub async fn take(
    conn: &mut PgConnection,
    item: &LockedItem,
    quantity: u32,
    reason: Reason,
    ref_id: Option<i64>,
) -> Result<i64, ItemError> {
    if quantity == 0 || quantity > item.quantity {
        return Err(ItemError::NotEnough);
    }
    if quantity == item.quantity {
        return Ok(item.id);
    }
    sqlx::query("UPDATE items SET quantity = quantity - $2 WHERE id = $1")
        .bind(item.id)
        .bind(i32::try_from(quantity).unwrap_or(i32::MAX))
        .execute(&mut *conn)
        .await?;
    let new_id: i64 = sqlx::query_scalar(
        "INSERT INTO items (owner_id, item_def, dye_primary, dye_secondary, dye_trim, finish,
                            quantity, maker_id, maker_name, location, created_at)
         SELECT owner_id, item_def, dye_primary, dye_secondary, dye_trim, finish,
                $2, maker_id, maker_name, location, created_at
         FROM items WHERE id = $1 RETURNING id",
    )
    .bind(item.id)
    .bind(i32::try_from(quantity).unwrap_or(i32::MAX))
    .fetch_one(&mut *conn)
    .await?;
    let mut m = ItemMove::new(new_id, item.item_def, ItemOp::Split, quantity, reason)
        .from(item.owner_id, &item.location)
        .to(item.owner_id, &item.location)
        .related(item.id);
    m.ref_id = ref_id;
    ledger::record_item_move(conn, &m).await?;
    Ok(new_id)
}

/// Moves a whole row to another owner and/or location (`ItemOp::Move`).
/// The row must be locked and not equipped or placed (the database refuses
/// an owner change of an equipped or placed item anyway).
pub async fn move_to(
    conn: &mut PgConnection,
    item: &LockedItem,
    to_owner: i64,
    to_location: &str,
    reason: Reason,
    ref_id: Option<i64>,
) -> Result<(), ItemError> {
    sqlx::query("UPDATE items SET owner_id = $2, location = $3 WHERE id = $1")
        .bind(item.id)
        .bind(to_owner)
        .bind(to_location)
        .execute(&mut *conn)
        .await?;
    let mut m = ItemMove::new(item.id, item.item_def, ItemOp::Move, item.quantity, reason)
        .from(item.owner_id, &item.location)
        .to(to_owner, to_location);
    m.ref_id = ref_id;
    ledger::record_item_move(conn, &m).await?;
    Ok(())
}

/// Uses up `quantity` units of a locked row (`ItemOp::Consume`): the row
/// shrinks, or is deleted when nothing is left.
pub async fn consume(
    conn: &mut PgConnection,
    item: &LockedItem,
    quantity: u32,
    reason: Reason,
    ref_id: Option<i64>,
) -> Result<(), ItemError> {
    if quantity == 0 || quantity > item.quantity {
        return Err(ItemError::NotEnough);
    }
    if quantity == item.quantity {
        sqlx::query("DELETE FROM items WHERE id = $1")
            .bind(item.id)
            .execute(&mut *conn)
            .await?;
    } else {
        sqlx::query("UPDATE items SET quantity = quantity - $2 WHERE id = $1")
            .bind(item.id)
            .bind(i32::try_from(quantity).unwrap_or(i32::MAX))
            .execute(&mut *conn)
            .await?;
    }
    let mut m = ItemMove::new(item.id, item.item_def, ItemOp::Consume, quantity, reason)
        .from(item.owner_id, &item.location);
    m.ref_id = ref_id;
    ledger::record_item_move(conn, &m).await?;
    Ok(())
}

/// Moves a locked row into its owner's inventory, merging its units into
/// existing inventory stacks of the same key where they fit
/// (`ItemOp::Merge`; a row emptied this way is deleted) and moving the rest
/// as the row itself (`ItemOp::Move`). Mail collection uses it, and the
/// trade commit for units just moved into the inventory (which only merge:
/// no move is recorded for a row already there). Returns the rows now
/// holding the units.
pub async fn into_inventory(
    conn: &mut PgConnection,
    item: &LockedItem,
    reason: Reason,
    ref_id: Option<i64>,
) -> Result<Vec<i64>, ItemError> {
    let def = shipped()
        .catalog
        .get(item.item_def)
        .ok_or(ItemError::UnknownDef(item.item_def))?;
    let mut left = item.quantity;
    let mut rows = Vec::new();
    if def.stackable {
        let key = NewItem {
            owner: item.owner_id,
            item_def: item.item_def,
            dyes: item.dyes,
            finish: item.finish,
            quantity: 0,
            maker: item.maker_id.map(|character_id| Maker {
                character_id,
                name: item.maker_name.clone().unwrap_or_default(),
            }),
            location: LOCATION_INVENTORY,
        };
        let limit = def.stack_limit();
        for (id, have) in same_stacks(conn, &key, item.maker_name.as_deref(), limit).await? {
            if left == 0 || id == item.id {
                continue;
            }
            let add = left.min(limit - have);
            sqlx::query("UPDATE items SET quantity = quantity + $2 WHERE id = $1")
                .bind(id)
                .bind(i32::try_from(add).unwrap_or(i32::MAX))
                .execute(&mut *conn)
                .await?;
            let mut m = ItemMove::new(item.id, item.item_def, ItemOp::Merge, add, reason)
                .from(item.owner_id, &item.location)
                .to(item.owner_id, LOCATION_INVENTORY)
                .related(id);
            m.ref_id = ref_id;
            ledger::record_item_move(conn, &m).await?;
            rows.push(id);
            left -= add;
        }
    }
    if left == 0 {
        sqlx::query("DELETE FROM items WHERE id = $1")
            .bind(item.id)
            .execute(&mut *conn)
            .await?;
        return Ok(rows);
    }
    sqlx::query("UPDATE items SET quantity = $2, location = $3 WHERE id = $1")
        .bind(item.id)
        .bind(i32::try_from(left).unwrap_or(i32::MAX))
        .bind(LOCATION_INVENTORY)
        .execute(&mut *conn)
        .await?;
    // A row already in the inventory (units just traded in) only merged:
    // its place did not change, so there is no move to record.
    if item.location != LOCATION_INVENTORY {
        let mut m = ItemMove::new(item.id, item.item_def, ItemOp::Move, left, reason)
            .from(item.owner_id, &item.location)
            .to(item.owner_id, LOCATION_INVENTORY);
        m.ref_id = ref_id;
        ledger::record_item_move(conn, &m).await?;
    }
    rows.push(item.id);
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finishes_round_trip_through_the_column() {
        for f in Finish::ALL {
            assert_eq!(finish_from_db(finish_to_db(f)), f);
        }
    }
}
