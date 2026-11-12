//! The append-only item and Crown ledgers.
//!
//! Every item movement (create, grant, move between owner or location,
//! split, merge, consume, destroy, restyle) and every change to a
//! character's Crowns appends one row, **inside the transaction that makes
//! the change**, with a [`Reason`] and the id of the row that explains it
//! (the trade, mail, listing, order or craft). A bug that duplicates an item
//! or Crowns then shows up as a ledger that does not add up, and can be
//! traced and reversed. The tables refuse updates and deletes.
//!
//! Shared by the economy and crafting code (docs/architecture.md):
//! call [`record_item_move`] / [`record_item_moves`] for item
//! movements you make yourself, or use the [`crate::items`] helpers, which
//! record for you; change Crowns only through [`adjust_crowns`] (or, for
//! an `UPDATE` you wrote yourself, record it with [`record_crowns`]).
//!
//! Callers hold the character row lock of every character whose items or
//! Crowns they change (`characters ... FOR NO KEY UPDATE`, in id order),
//! taken before any item row lock; that one rule keeps every economy
//! transaction deadlock-free.
//!
//! [`audit::reconcile`] replays both ledgers and reports anything they do
//! not explain (the duplication hunt; tests run it at the end).

pub mod audit;

use sqlx::PgConnection;

/// What happened to an item (`item_ledger.op`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ItemOp {
    /// Units that did not exist in the economy (shop order, find, craft output).
    Create,
    /// Units granted (starter set).
    Grant,
    /// Owner and/or location changed.
    Move,
    /// A new row (`item_id`) took `quantity` units from `related_item`.
    Split,
    /// `quantity` units of `item_id` went into `related_item`.
    Merge,
    /// Units used up (craft inputs, dyes and finishes applied, potions
    /// drunk, patterns learned).
    Consume,
    /// Removed for another reason (moderation, a deleted character).
    Destroy,
    /// `item_def` changed in place (Transfiguration), from `prev_item_def`.
    Restyle,
}

impl ItemOp {
    pub fn id(self) -> &'static str {
        match self {
            ItemOp::Create => "create",
            ItemOp::Grant => "grant",
            ItemOp::Move => "move",
            ItemOp::Split => "split",
            ItemOp::Merge => "merge",
            ItemOp::Consume => "consume",
            ItemOp::Destroy => "destroy",
            ItemOp::Restyle => "restyle",
        }
    }
}

/// Why (`item_ledger.reason`, `crown_ledger.reason`). `ref_id` names the
/// row in the table each reason's comment gives.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Reason {
    /// The starter grant (no ref).
    Starter,
    /// The starting purse (no ref).
    StartingPurse,
    /// Equipping or taking off (no ref).
    Equip,
    /// Placing in or taking back from the dorm (no ref).
    Dorm,
    /// An owl-order purchase: Crowns paid, items created into the
    /// delivery mail (`shop_orders.id`).
    ShopOrder,
    /// A player letter: items attached, Crowns enclosed (`mail.id`).
    MailSend,
    /// Postage for a letter (`mail.id`).
    Postage,
    /// Attachments and Crowns collected from a mail (`mail.id`).
    MailCollect,
    /// Listing on the Exchange: the item to 'exchange' (`exchange_listings.id`).
    ExchangeList,
    /// The listing fee (`exchange_listings.id`).
    ExchangeFee,
    /// A purchase: the buyer's Crowns, the item into the buyer's mail
    /// (`exchange_listings.id`).
    ExchangeBuy,
    /// Proceeds mailed to the seller (`exchange_listings.id`).
    ExchangeSale,
    /// An unsold or taken-down item mailed back (`exchange_listings.id`).
    ExchangeReturn,
    /// A completed trade window (`trades.id`).
    Trade,
    /// Craft inputs used and the output made (`crafts.id`).
    Craft,
    /// A dye or finish applied to an item (the dyed item's `items.id`).
    Dye,
    /// A pattern learned (`known_recipes` has no id: the recipe id).
    LearnPattern,
    /// An exploration find (`find_cooldowns` has no id: no ref).
    Find,
    /// A potion drunk (no ref).
    Drink,
    /// Moderation or support (a ticket id, if any).
    Admin,
    /// Crowns and items a class gave (`class_attendance.id`).
    Class,
    /// The House Cup reward created into its mail
    /// (`house_cups.id`).
    HouseCup,
    /// NPCs: an item an NPC gave as today's gift (`npc_gifts.id`).
    NpcGift,
    /// Its player deleted the character: everything it held is destroyed
    /// and its Crowns leave the economy (no ref; the character row is gone).
    CharacterDeleted,
    /// The fee for founding a club (`clubs.id`).
    ClubFound,
}

impl Reason {
    pub fn id(self) -> &'static str {
        match self {
            Reason::Starter => "starter",
            Reason::StartingPurse => "starting_purse",
            Reason::Equip => "equip",
            Reason::Dorm => "dorm",
            Reason::ShopOrder => "shop_order",
            Reason::MailSend => "mail_send",
            Reason::Postage => "postage",
            Reason::MailCollect => "mail_collect",
            Reason::ExchangeList => "exchange_list",
            Reason::ExchangeFee => "exchange_fee",
            Reason::ExchangeBuy => "exchange_buy",
            Reason::ExchangeSale => "exchange_sale",
            Reason::ExchangeReturn => "exchange_return",
            Reason::Trade => "trade",
            Reason::Craft => "craft",
            Reason::Dye => "dye",
            Reason::LearnPattern => "learn_pattern",
            Reason::Find => "find",
            Reason::Drink => "drink",
            Reason::Admin => "admin",
            Reason::Class => "class",
            Reason::HouseCup => "house_cup",
            Reason::NpcGift => "npc_gift",
            Reason::CharacterDeleted => "character_deleted",
            Reason::ClubFound => "club_found",
        }
    }
}

/// One item movement (`item_ledger` row).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ItemMove<'a> {
    pub item_id: i64,
    pub item_def: u16,
    pub op: ItemOp,
    /// Units moved, created, used up... (at least 1).
    pub quantity: u32,
    pub from_owner: Option<i64>,
    pub to_owner: Option<i64>,
    pub from_location: Option<&'a str>,
    pub to_location: Option<&'a str>,
    /// Split: the stack the units came from. Merge: the stack they went into.
    pub related_item: Option<i64>,
    /// Restyle: the definition before.
    pub prev_item_def: Option<u16>,
    pub reason: Reason,
    pub ref_id: Option<i64>,
}

impl<'a> ItemMove<'a> {
    /// A movement of `quantity` units of `item_id` with no owner or location
    /// set yet (fill in what applies).
    pub fn new(item_id: i64, item_def: u16, op: ItemOp, quantity: u32, reason: Reason) -> Self {
        Self {
            item_id,
            item_def,
            op,
            quantity,
            from_owner: None,
            to_owner: None,
            from_location: None,
            to_location: None,
            related_item: None,
            prev_item_def: None,
            reason,
            ref_id: None,
        }
    }

    pub fn from(mut self, owner: i64, location: &'a str) -> Self {
        self.from_owner = Some(owner);
        self.from_location = Some(location);
        self
    }

    pub fn to(mut self, owner: i64, location: &'a str) -> Self {
        self.to_owner = Some(owner);
        self.to_location = Some(location);
        self
    }

    pub fn related(mut self, item_id: i64) -> Self {
        self.related_item = Some(item_id);
        self
    }

    pub fn ref_id(mut self, id: i64) -> Self {
        self.ref_id = Some(id);
        self
    }
}

#[derive(Debug, thiserror::Error)]
pub enum LedgerError {
    #[error("not enough Crowns")]
    InsufficientCrowns,
    #[error("a ledger row needs a quantity of at least 1")]
    ZeroQuantity,
    #[error(transparent)]
    Db(#[from] sqlx::Error),
}

/// Appends one item movement. Call it inside the transaction that moves the item.
pub async fn record_item_move(
    conn: &mut PgConnection,
    m: &ItemMove<'_>,
) -> Result<(), LedgerError> {
    record_item_moves(conn, std::slice::from_ref(m)).await
}

/// Appends several item movements in one statement, in order.
pub async fn record_item_moves(
    conn: &mut PgConnection,
    moves: &[ItemMove<'_>],
) -> Result<(), LedgerError> {
    if moves.is_empty() {
        return Ok(());
    }
    if moves.iter().any(|m| m.quantity == 0) {
        return Err(LedgerError::ZeroQuantity);
    }
    let col = |f: &dyn Fn(&ItemMove<'_>) -> Option<i64>| moves.iter().map(f).collect::<Vec<_>>();
    let text = |f: &dyn for<'m> Fn(&'m ItemMove<'m>) -> Option<&'m str>| {
        moves
            .iter()
            .map(|m| f(m).map(str::to_owned))
            .collect::<Vec<_>>()
    };
    let item_ids: Vec<i64> = moves.iter().map(|m| m.item_id).collect();
    let defs: Vec<i32> = moves.iter().map(|m| i32::from(m.item_def)).collect();
    let ops: Vec<&str> = moves.iter().map(|m| m.op.id()).collect();
    let quantities: Vec<i32> = moves
        .iter()
        .map(|m| i32::try_from(m.quantity).unwrap_or(i32::MAX))
        .collect();
    let prev_defs: Vec<Option<i32>> = moves
        .iter()
        .map(|m| m.prev_item_def.map(i32::from))
        .collect();
    let reasons: Vec<&str> = moves.iter().map(|m| m.reason.id()).collect();
    sqlx::query(
        "INSERT INTO item_ledger (item_id, item_def, op, quantity, from_owner, to_owner,
                                  from_location, to_location, related_item, prev_item_def,
                                  reason, ref_id)
         SELECT * FROM unnest($1::bigint[], $2::int[], $3::text[], $4::int[], $5::bigint[],
                              $6::bigint[], $7::text[], $8::text[], $9::bigint[], $10::int[],
                              $11::text[], $12::bigint[])",
    )
    .bind(&item_ids)
    .bind(&defs)
    .bind(&ops)
    .bind(&quantities)
    .bind(col(&|m| m.from_owner))
    .bind(col(&|m| m.to_owner))
    .bind(text(&|m| m.from_location))
    .bind(text(&|m| m.to_location))
    .bind(col(&|m| m.related_item))
    .bind(&prev_defs)
    .bind(&reasons)
    .bind(col(&|m| m.ref_id))
    .execute(conn)
    .await?;
    Ok(())
}

/// Appends one Crown movement for a balance change the caller already
/// made (`balance_after` is the new balance). Prefer [`adjust_crowns`],
/// which makes the change and records it together. A zero `delta` records
/// nothing.
pub async fn record_crowns(
    conn: &mut PgConnection,
    character_id: i64,
    delta: i64,
    balance_after: i64,
    reason: Reason,
    ref_id: Option<i64>,
) -> Result<(), LedgerError> {
    if delta == 0 {
        return Ok(());
    }
    sqlx::query(
        "INSERT INTO crown_ledger (character_id, delta, balance_after, reason, ref_id)
         VALUES ($1, $2, $3, $4, $5)",
    )
    .bind(character_id)
    .bind(delta)
    .bind(balance_after)
    .bind(reason.id())
    .bind(ref_id)
    .execute(conn)
    .await?;
    Ok(())
}

/// Adds `delta` Crowns (negative to take) to a character's balance and
/// records it; returns the new balance. Refuses with
/// [`LedgerError::InsufficientCrowns`] (changing nothing) when the balance
/// would go below zero. A zero `delta` changes and records nothing.
///
/// The caller holds the character's row lock (`FOR NO KEY UPDATE`) and runs
/// this inside its transaction; the `WHERE` re-checks the balance anyway, so
/// the non-negative rule never depends on the caller's read.
pub async fn adjust_crowns(
    conn: &mut PgConnection,
    character_id: i64,
    delta: i64,
    reason: Reason,
    ref_id: Option<i64>,
) -> Result<i64, LedgerError> {
    if delta == 0 {
        let balance: i64 = sqlx::query_scalar("SELECT crowns FROM characters WHERE id = $1")
            .bind(character_id)
            .fetch_one(&mut *conn)
            .await?;
        return Ok(balance);
    }
    let balance: Option<i64> = sqlx::query_scalar(
        "UPDATE characters SET crowns = crowns + $2
         WHERE id = $1 AND crowns + $2 >= 0
         RETURNING crowns",
    )
    .bind(character_id)
    .bind(delta)
    .fetch_optional(&mut *conn)
    .await?;
    let Some(balance) = balance else {
        return Err(LedgerError::InsufficientCrowns);
    };
    record_crowns(conn, character_id, delta, balance, reason, ref_id).await?;
    Ok(balance)
}

/// A character's balance (no lock; for display).
pub async fn balance(pool: &sqlx::PgPool, character_id: i64) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar("SELECT crowns FROM characters WHERE id = $1")
        .bind(character_id)
        .fetch_one(pool)
        .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_fit_the_table_checks() {
        // The CHECK in db/migrations/0007_economy.sql lists exactly these ops.
        let ops = [
            ItemOp::Create,
            ItemOp::Grant,
            ItemOp::Move,
            ItemOp::Split,
            ItemOp::Merge,
            ItemOp::Consume,
            ItemOp::Destroy,
            ItemOp::Restyle,
        ];
        let migration = include_str!("../../../../db/migrations/0007_economy.sql");
        for op in ops {
            assert!(migration.contains(&format!("'{}'", op.id())), "{op:?}");
        }
        let m = ItemMove::new(1, 3001, ItemOp::Split, 4, Reason::Trade)
            .from(7, "inventory")
            .to(7, "inventory")
            .related(2)
            .ref_id(9);
        assert_eq!(m.from_owner, Some(7));
        assert_eq!(m.related_item, Some(2));
        assert_eq!(m.ref_id, Some(9));
    }
}
