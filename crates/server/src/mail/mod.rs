//! Owl mail: a paginated
//! mailbox readable anywhere, player letters (text, optional items and
//! Crowns, postage) and system mail (owl-order deliveries, Exchange
//! purchases, proceeds and returns), whose attachments and Crowns are
//! collected in one transaction each.
//!
//! Routes ([`routes`], docs/architecture.md, "HTTP API"):
//! `GET /api/mail`, `GET /api/mail/{id}` (marks it read), `POST /api/mail`
//! (a letter), `POST /api/mail/{id}/collect`, `DELETE /api/mail/{id}`, and
//! the sender's Outbox: `GET /api/mail/sent`, `GET /api/mail/sent/{id}`.
//!
//! The rules every mail follows:
//!
//! - Attached items are owned by the **recipient** with location `mail`
//!   (one `mail_attachments` row each) from the moment the mail is sent
//!   until it is collected (`crate::items::into_inventory`, merging into the
//!   recipient's stacks, `ledger::Reason::MailCollect`). Crowns travel in
//!   `mail.crowns`: taken from the sender (letters) or set aside from a
//!   sale (proceeds) when sent, paid out on collection.
//! - `collected_at IS NULL` exactly while something waits to be collected,
//!   so a mail with nothing attached is "collected" from the start, and a
//!   mail can be deleted only once collected (`409 uncollected`).
//! - Every write locks first the characters whose items or Crowns change
//!   (`wallet::lock_characters`), then the mail row, then item rows by
//!   ascending id. Collecting twice finds `collected_at` set under the mail
//!   row's lock (`409 already_collected`).
//! - New items for a mail (owl-order deliveries) are always fresh rows
//!   ([`deliver_new`]), never topped-up stacks: a stack in another mail of
//!   the same recipient is that mail's.
//! - After a change to someone's unread count, [`notify`] sends
//!   `WorldHandle::mail_changed(recipient, unread)`.
//! - A letter is one row seen from both ends. The recipient's delete only
//!   hides it from their mailbox (`recipient_deleted_at`), so the sender's
//!   Outbox copy ([`SentView`]) stays; every recipient-side query filters
//!   on [`IN_MAILBOX`]. System mail is deleted outright. What a letter
//!   carried is kept as sent (`sent_items`): attachment rows go on collect.
//! - Old mail goes: [`cleanup`] (the server's minute job) deletes mail
//!   `mail.retention_days` after nothing waits in it any more. Mail whose
//!   items or Crowns were never collected stays until they are.

mod routes;

use chrono::{DateTime, Utc};
use serde::Serialize;
use sqlx::{PgConnection, PgExecutor};

use crate::AppState;
use crate::catalog::shipped;
use crate::inventory::{ITEM_SELECT, ItemRow, ItemView, LOCATION_MAIL};
use crate::items::{self, ItemError, LockedItem, NewItem};
use crate::ledger::{ItemOp, Reason};

pub use routes::routes;

/// System senders' display names (`mail.sender_name` for system mail).
pub const SENDER_OWL_ORDER: &str = "Owl-Order Catalogue";
pub const SENDER_EXCHANGE: &str = "Owl Exchange";

/// `mail.kind` values.
pub const KIND_LETTER: &str = "letter";
pub const KIND_SHOP: &str = "shop";
pub const KIND_EXCHANGE_SOLD: &str = "exchange_sold";
pub const KIND_EXCHANGE_BOUGHT: &str = "exchange_bought";
pub const KIND_EXCHANGE_RETURNED: &str = "exchange_returned";
pub const KIND_EXCHANGE_EXPIRED: &str = "exchange_expired";
pub const KIND_SYSTEM: &str = "system";

/// The database's limit on `mail.subject` (characters).
const SUBJECT_COLUMN_MAX: usize = 80;

/// A mail in a mailbox page: `{ id, kind, sender_id, sender_name, subject,
/// created_at, read, collected, crowns, attachments }`.
#[derive(Debug, Clone, Serialize, sqlx::FromRow)]
pub struct MailView {
    pub id: i64,
    pub kind: String,
    pub sender_id: Option<i64>,
    pub sender_name: String,
    pub subject: String,
    pub created_at: DateTime<Utc>,
    pub read: bool,
    pub collected: bool,
    pub crowns: i64,
    /// Items still attached (0 once collected).
    pub attachments: i64,
}

/// Selects [`MailView`]s as `m`; append a `WHERE`.
const MAIL_SELECT: &str =
    "SELECT m.id, m.kind, m.sender_id, m.sender_name, m.subject, m.created_at,
            m.read_at IS NOT NULL AS read, m.collected_at IS NOT NULL AS collected, m.crowns,
            (SELECT count(*) FROM mail_attachments a WHERE a.mail_id = m.id) AS attachments
     FROM mail m";

/// The condition (on `m`) for mail still in its recipient's mailbox: not
/// thrown away by them (the sender may still see it in their Outbox).
pub(crate) const IN_MAILBOX: &str = "m.recipient_deleted_at IS NULL";

/// One mail in `recipient`'s mailbox (for the views after a change).
pub(crate) async fn view(
    db: impl PgExecutor<'_>,
    recipient: i64,
    id: i64,
) -> Result<Option<MailView>, sqlx::Error> {
    sqlx::query_as(&format!(
        "{MAIL_SELECT} WHERE m.id = $1 AND m.recipient_id = $2 AND {IN_MAILBOX}"
    ))
    .bind(id)
    .bind(recipient)
    .fetch_optional(db)
    .await
}

/// Any mail by id as its recipient sees it, thrown away or not (a letter
/// just sent, for its sender's reply). Callers check who is asking.
pub(crate) async fn view_any(
    db: impl PgExecutor<'_>,
    id: i64,
) -> Result<Option<MailView>, sqlx::Error> {
    sqlx::query_as(&format!("{MAIL_SELECT} WHERE m.id = $1"))
        .bind(id)
        .fetch_optional(db)
        .await
}

/// A letter in its sender's Outbox: `{ id, recipient_id, recipient_name,
/// subject, created_at, read, collected, crowns, attachments }`. `read`
/// and `collected` are the recipient's; `crowns` and `attachments` are
/// what was sent (kept after collection).
#[derive(Debug, Clone, Serialize, sqlx::FromRow)]
pub struct SentView {
    pub id: i64,
    pub recipient_id: i64,
    pub recipient_name: String,
    pub subject: String,
    pub created_at: DateTime<Utc>,
    pub read: bool,
    pub collected: bool,
    pub crowns: i64,
    /// Items sent with it.
    pub attachments: i64,
}

/// Selects [`SentView`]s as `m`; append a `WHERE` naming the sender. Only
/// player letters belong in an Outbox: callers also require
/// `m.kind = 'letter'`.
pub(crate) const SENT_SELECT: &str =
    "SELECT m.id, m.recipient_id, c.name AS recipient_name, m.subject, m.created_at,
            m.read_at IS NOT NULL AS read, m.collected_at IS NOT NULL AS collected, m.crowns,
            coalesce(jsonb_array_length(m.sent_items), 0)::bigint AS attachments
     FROM mail m JOIN characters c ON c.id = m.recipient_id";

/// The character's unread mail (the new-mail owl), by the partial index.
pub async fn unread(db: impl PgExecutor<'_>, character_id: i64) -> Result<u32, sqlx::Error> {
    let n: i64 = sqlx::query_scalar(&format!(
        "SELECT count(*) FROM mail m
         WHERE m.recipient_id = $1 AND m.read_at IS NULL AND {IN_MAILBOX}"
    ))
    .bind(character_id)
    .fetch_one(db)
    .await?;
    Ok(u32::try_from(n).unwrap_or(u32::MAX))
}

/// After committing a change to `recipient`'s mail: `MailNotice` with their
/// unread count, if they are online. A failed count is only logged (the
/// change itself committed; the next notice corrects the owl).
pub async fn notify(state: &AppState, recipient: i64) {
    let Some(world) = state.world() else {
        return;
    };
    match unread(&state.pool, recipient).await {
        Ok(n) => world.mail_changed(recipient, n),
        Err(e) => tracing::warn!(error = %e, recipient, "counting unread mail failed"),
    }
}

/// A mail to insert ([`create`]).
#[derive(Debug, Clone)]
pub struct NewMail<'a> {
    pub recipient: i64,
    /// `None` for system mail.
    pub sender_id: Option<i64>,
    pub sender_name: &'a str,
    pub kind: &'static str,
    pub subject: &'a str,
    pub body: &'a str,
    /// Crowns travelling with it (already taken from wherever they came from).
    pub crowns: i64,
    /// The row it is about (`exchange_listings.id`, `shop_orders.id`).
    pub ref_id: Option<i64>,
    /// Letters: the sender's request id.
    pub client_key: Option<&'a str>,
    /// Whether items will be attached ([`attach`], [`deliver_new`]) in this
    /// transaction: then it waits to be collected even with no Crowns.
    pub with_items: bool,
}

/// Inserts a mail; returns its id. Attach its items in the same transaction.
pub async fn create(conn: &mut PgConnection, mail: &NewMail<'_>) -> Result<i64, sqlx::Error> {
    let waiting = mail.with_items || mail.crowns > 0;
    sqlx::query_scalar(
        "INSERT INTO mail (recipient_id, sender_id, sender_name, kind, subject, body, crowns,
                           ref_id, client_key, collected_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, CASE WHEN $10 THEN NULL ELSE now() END)
         RETURNING id",
    )
    .bind(mail.recipient)
    .bind(mail.sender_id)
    .bind(mail.sender_name)
    .bind(mail.kind)
    .bind(clip(mail.subject, SUBJECT_COLUMN_MAX))
    .bind(mail.body)
    .bind(mail.crowns)
    .bind(mail.ref_id)
    .bind(mail.client_key)
    .bind(waiting)
    .fetch_one(conn)
    .await
}

/// Puts a locked item row (all of it: split first with `items::take`) into
/// mail `mail_id` for `recipient`: it becomes theirs with location `mail`.
pub async fn attach(
    conn: &mut PgConnection,
    mail_id: i64,
    recipient: i64,
    item: &LockedItem,
    reason: Reason,
    ref_id: i64,
) -> Result<(), ItemError> {
    items::move_to(conn, item, recipient, LOCATION_MAIL, reason, Some(ref_id)).await?;
    insert_attachment(conn, mail_id, item.id, recipient).await?;
    Ok(())
}

/// Makes `quantity` new plain units of `item_def` attached to mail
/// `mail_id` (`ItemOp::Create`): fresh rows of at most a stack each, so no
/// other mail's stack grows. Returns the rows.
pub async fn deliver_new(
    conn: &mut PgConnection,
    mail_id: i64,
    recipient: i64,
    item_def: u16,
    quantity: u32,
    reason: Reason,
    ref_id: i64,
) -> Result<Vec<i64>, ItemError> {
    let def = shipped()
        .catalog
        .get(item_def)
        .ok_or(ItemError::UnknownDef(item_def))?;
    let limit = def.stack_limit();
    let mut rows = Vec::new();
    let mut left = quantity;
    while left > 0 {
        let n = left.min(limit);
        let item = NewItem::plain(recipient, item_def, n, LOCATION_MAIL);
        let id = items::create(conn, &item, ItemOp::Create, reason, Some(ref_id)).await?;
        insert_attachment(conn, mail_id, id, recipient).await?;
        rows.push(id);
        left -= n;
    }
    Ok(rows)
}

/// Makes one new row from `item` (built by the caller: its dyes,
/// finish and maker as they should be; `owner` and `location` are set to
/// `recipient` and `mail`) attached to mail `mail_id` (`ItemOp::Create`,
/// at most a stack). Returns the row. [`deliver_new`] is the plain-dyes
/// version.
pub async fn deliver_item(
    conn: &mut PgConnection,
    mail_id: i64,
    recipient: i64,
    item: &NewItem,
    reason: Reason,
    ref_id: i64,
) -> Result<i64, ItemError> {
    let item = NewItem {
        owner: recipient,
        location: LOCATION_MAIL,
        ..item.clone()
    };
    let id = items::create(conn, &item, ItemOp::Create, reason, Some(ref_id)).await?;
    insert_attachment(conn, mail_id, id, recipient).await?;
    Ok(id)
}

async fn insert_attachment(
    conn: &mut PgConnection,
    mail_id: i64,
    item_id: i64,
    owner: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query("INSERT INTO mail_attachments (mail_id, item_id, owner_id) VALUES ($1, $2, $3)")
        .bind(mail_id)
        .bind(item_id)
        .bind(owner)
        .execute(conn)
        .await?;
    Ok(())
}

/// The items with these ids, as the API shows them (ascending id).
pub(crate) async fn item_views(
    db: impl PgExecutor<'_>,
    ids: &[i64],
) -> Result<Vec<ItemView>, sqlx::Error> {
    let rows: Vec<ItemRow> =
        sqlx::query_as(&format!("{ITEM_SELECT} WHERE i.id = ANY($1) ORDER BY i.id"))
            .bind(ids)
            .fetch_all(db)
            .await?;
    Ok(rows.into_iter().map(ItemView::from).collect())
}

/// Mail deleted per statement of [`cleanup`], and statements per run (the
/// rest waits for the next minute, so a backlog never holds the job long).
const CLEANUP_BATCH: i64 = 500;
const CLEANUP_BATCHES_MAX: usize = 20;

/// What one [`cleanup`] run removed.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Cleaned {
    pub deleted: u64,
    /// Recipients who lost unread mail (their new-mail count changed).
    pub unread_changed: Vec<i64>,
}

/// Deletes mail that nothing has waited in for `retention`
/// (`collected_at` older than that; mail sent with nothing to collect is
/// collected from the start), oldest first, in batches. Letters their
/// recipient threw away go the same way, so a sender's Outbox keeps a
/// letter as long as its recipient could have. Never a mail whose
/// items or Crowns wait (`collected_at IS NULL`), nor one that still has
/// attachment rows (which a collected mail never has; deleting would
/// strand the items). Rows another transaction holds (a collect or a
/// delete in progress) are skipped until the next run.
pub async fn cleanup(
    pool: &sqlx::PgPool,
    retention: std::time::Duration,
) -> Result<Cleaned, sqlx::Error> {
    let mut out = Cleaned::default();
    for _ in 0..CLEANUP_BATCHES_MAX {
        let rows: Vec<(i64, bool)> = sqlx::query_as(
            "WITH doomed AS (
                 SELECT m.id FROM mail m
                 WHERE m.collected_at IS NOT NULL
                   AND m.collected_at < now() - make_interval(secs => $1)
                   AND NOT EXISTS (SELECT 1 FROM mail_attachments a WHERE a.mail_id = m.id)
                 ORDER BY m.collected_at
                 LIMIT $2
                 FOR UPDATE SKIP LOCKED
             )
             DELETE FROM mail m USING doomed d WHERE m.id = d.id
             RETURNING m.recipient_id, m.read_at IS NULL AND m.recipient_deleted_at IS NULL",
        )
        .bind(retention.as_secs_f64())
        .bind(CLEANUP_BATCH)
        .fetch_all(pool)
        .await?;
        out.deleted += rows.len() as u64;
        out.unread_changed
            .extend(rows.iter().filter(|r| r.1).map(|r| r.0));
        if (rows.len() as i64) < CLEANUP_BATCH {
            break;
        }
    }
    out.unread_changed.sort_unstable();
    out.unread_changed.dedup();
    Ok(out)
}

/// A system mail's subject, `"<what>: <item name>"`, within the column's limit.
pub fn subject_about(what: &str, item_def: u16, quantity: u32) -> String {
    let name = shipped()
        .catalog
        .get(item_def)
        .map_or("an item", |d| d.name.as_str());
    let s = if quantity > 1 {
        format!("{what}: {name} ×{quantity}")
    } else {
        format!("{what}: {name}")
    };
    clip(&s, SUBJECT_COLUMN_MAX)
}

/// At most `max` characters of `s` (never splitting a character).
fn clip(s: &str, max: usize) -> String {
    match s.char_indices().nth(max) {
        Some((cut, _)) => s[..cut].to_owned(),
        None => s.to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn subjects_name_the_item_and_fit_the_column() {
        let wool = shipped().catalog.get(3001).unwrap();
        assert_eq!(
            subject_about("Sold", 3001, 1),
            format!("Sold: {}", wool.name)
        );
        assert!(subject_about("Sold", 3001, 5).ends_with("×5"));
        assert_eq!(subject_about("Sold", 0, 1), "Sold: an item");
        let long = "é".repeat(200);
        assert_eq!(clip(&long, 80).chars().count(), 80);
        assert_eq!(clip("short", 80), "short");
    }
}
