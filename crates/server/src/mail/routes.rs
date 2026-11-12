//! The owl mail routes: the mailbox, reading, letters, collecting,
//! deleting, and the sender's Outbox (see the module docs of [`super`] for
//! the rules).

use std::collections::HashSet;
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use axum::extract::{Extension, State};
use axum::http::StatusCode;
use axum::{Json, Router};
use serde::{Deserialize, Serialize};

use super::{
    IN_MAILBOX, KIND_LETTER, MAIL_SELECT, MailView, NewMail, SENT_SELECT, SentView, attach, create,
    item_views, notify, unread, view, view_any,
};
use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{ApiPath, Page, PageQuery, Player, Writer, find_by_name, write_limited};
use crate::http::{ApiError, ApiJson, ApiQuery};
use crate::inventory::equip::{item_not_found, item_unavailable};
use crate::inventory::{ItemView, LOCATION_INVENTORY};
use crate::items;
use crate::ledger::{self, Reason};
use crate::rate_limit::{AccountKey, RateLimit, RateLimiter};
use crate::realtime::chat::{WordFilter, is_hidden_format};
use crate::wallet::{
    ECONOMY_RATE_LIMIT, item_error, ledger_error, lock_characters, request_key, split_row,
};

/// `GET /api/mail` → `{ items: [Mail], next, unread }`.
#[derive(Debug, Serialize)]
pub struct Mailbox {
    #[serde(flatten)]
    pub page: Page<MailView>,
    pub unread: u32,
}

/// `GET /api/mail?limit=&after=`: newest first (index `mail (recipient_id, id)`).
pub async fn list(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Mailbox>, ApiError> {
    let me = player.character.id;
    let after: Option<i64> = page.after()?;
    let rows: Vec<MailView> = sqlx::query_as(&format!(
        "{MAIL_SELECT}
         WHERE m.recipient_id = $1 AND {IN_MAILBOX} AND ($2::bigint IS NULL OR m.id < $2)
         ORDER BY m.id DESC LIMIT $3"
    ))
    .bind(me)
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    let unread = unread(&state.pool, me).await?;
    Ok(Json(Mailbox {
        page: Page::from_rows(rows, page.limit()?, |m| m.id),
        unread,
    }))
}

/// `MailDetail`: a [`MailView`] with its text and attached items.
#[derive(Debug, Serialize)]
pub struct MailDetail {
    #[serde(flatten)]
    pub mail: MailView,
    pub body: String,
    pub items: Vec<ItemView>,
}

fn mail_not_found() -> ApiError {
    ApiError::not_found("mail_not_found", "There is no such letter in your mailbox.")
}

/// `GET /api/mail/{id}`: the mail with its body and items; marks it read.
pub async fn detail(
    State(state): State<AppState>,
    player: Player,
    ApiPath(id): ApiPath<i64>,
) -> Result<Json<MailDetail>, ApiError> {
    let me = player.character.id;
    let newly_read = sqlx::query(
        "UPDATE mail SET read_at = now()
         WHERE id = $1 AND recipient_id = $2 AND read_at IS NULL AND recipient_deleted_at IS NULL",
    )
    .bind(id)
    .bind(me)
    .execute(&state.pool)
    .await?
    .rows_affected()
        > 0;
    let mail = view(&state.pool, me, id)
        .await?
        .ok_or_else(mail_not_found)?;
    let body: String = sqlx::query_scalar("SELECT body FROM mail WHERE id = $1")
        .bind(id)
        .fetch_one(&state.pool)
        .await?;
    let ids: Vec<i64> =
        sqlx::query_scalar("SELECT item_id FROM mail_attachments WHERE mail_id = $1")
            .bind(id)
            .fetch_all(&state.pool)
            .await?;
    let items = item_views(&state.pool, &ids).await?;
    if newly_read {
        notify(&state, me).await;
    }
    Ok(Json(MailDetail { mail, body, items }))
}

/// One attachment of a letter: an inventory item and how many of its stack.
#[derive(Debug, Clone, Copy, Deserialize)]
pub struct Attachment {
    pub item_id: i64,
    #[serde(default = "one")]
    pub quantity: u32,
}

fn one() -> u32 {
    1
}

/// `POST /api/mail` body.
#[derive(Debug, Deserialize)]
pub struct SendLetter {
    pub to: String,
    pub subject: String,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub items: Vec<Attachment>,
    #[serde(default)]
    pub crowns: i64,
    #[serde(default)]
    pub request_id: Option<String>,
}

/// `201 { mail, crowns }`: the letter as its recipient will see it, and
/// the sender's balance after postage.
#[derive(Debug, Serialize)]
pub struct Sent {
    pub mail: MailView,
    pub crowns: i64,
}

fn invalid_mail(message: impl Into<std::borrow::Cow<'static, str>>) -> ApiError {
    ApiError::bad_request("invalid_mail", message)
}

fn character_not_found() -> ApiError {
    ApiError::not_found("character_not_found", "There is no student by that name.")
}

/// Letters per account on top of the economy write budget
/// (`mail.send_burst`, then one per `mail.send_refill_secs`).
#[derive(Clone)]
pub struct LetterLimiter(Arc<RateLimiter<AccountKey>>);

/// The chat word filter, for letters (loaded once from
/// `Config::blocked_words_path`, empty when the file is missing).
fn word_filter(state: &AppState) -> &'static WordFilter {
    static FILTER: OnceLock<WordFilter> = OnceLock::new();
    FILTER.get_or_init(|| WordFilter::load_or_empty(&state.config.blocked_words_path))
}

/// A letter's text: invisible formatting removed (as in chat), other
/// control characters turned into spaces (the body keeps its line breaks),
/// trimmed.
fn clean(raw: &str, keep_newlines: bool) -> String {
    raw.chars()
        .filter(|&c| !is_hidden_format(c) && !(keep_newlines && c == '\r'))
        .map(|c| match c {
            '\n' if keep_newlines => '\n',
            c if c.is_control() || matches!(c, '\u{2028}' | '\u{2029}') => ' ',
            c => c,
        })
        .collect::<String>()
        .trim()
        .to_owned()
}

/// The letter's checked contents: (subject, body, attachments).
fn check_letter(
    state: &AppState,
    req: &SendLetter,
) -> Result<(String, String, Vec<Attachment>), ApiError> {
    let rules = &shipped().economy.mail;
    let subject = clean(&req.subject, false);
    let body = clean(&req.body, true);
    let subject_len = subject.chars().count();
    if subject_len == 0 || subject_len > usize::from(rules.subject_max_chars) {
        return Err(invalid_mail(format!(
            "The subject must be 1-{} characters.",
            rules.subject_max_chars
        )));
    }
    if body.chars().count() > usize::from(rules.body_max_chars) {
        return Err(invalid_mail("The letter is too long."));
    }
    let filter = word_filter(state);
    if filter.apply(&subject) != subject || filter.apply(&body) != body {
        return Err(invalid_mail(
            "Your letter contains words that are not allowed.",
        ));
    }
    if req.items.len() > usize::from(rules.attachments_max) {
        return Err(invalid_mail("Too many items for one owl."));
    }
    let mut seen = HashSet::new();
    if req
        .items
        .iter()
        .any(|a| a.quantity == 0 || !seen.insert(a.item_id))
    {
        return Err(invalid_mail(
            "Each item may be attached once, with at least one unit.",
        ));
    }
    if !(0..=i64::from(rules.crowns_max)).contains(&req.crowns) {
        return Err(invalid_mail("That is not an amount an owl can carry."));
    }
    Ok((subject, body, req.items.clone()))
}

/// `POST /api/mail`: a letter. One transaction: both characters locked, the
/// items split off and handed to the recipient (location `mail`), the
/// Crowns and postage taken from the sender. A repeated `request_id`
/// answers the letter already sent.
pub async fn send(
    State(state): State<AppState>,
    Writer(player): Writer,
    Extension(letters): Extension<LetterLimiter>,
    ApiJson(req): ApiJson<SendLetter>,
) -> Result<(StatusCode, Json<Sent>), ApiError> {
    let me = player.character;
    let key = request_key(req.request_id.as_deref())?;
    let (subject, body, attachments) = check_letter(&state, &req)?;
    let to = find_by_name(&state.pool, &req.to)
        .await?
        .ok_or_else(character_not_found)?;
    if to.id == me.id {
        return Err(ApiError::bad_request(
            "self",
            "You cannot send an owl to yourself.",
        ));
    }
    let blocked = crate::friends::block_lists(&state.pool, me.id).await?;
    if blocked.blocked.contains(&to.id) || blocked.blocked_by.contains(&to.id) {
        return Err(character_not_found());
    }
    if let Err(wait) = letters.0.check(AccountKey(player.account_id)) {
        return Err(ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            "rate_limited",
            format!(
                "Your owls need a rest. Try again in {} s.",
                wait.as_secs().max(1)
            ),
        ));
    }
    let rules = &shipped().economy.mail;

    let mut tx = state.pool.begin().await?;
    lock_characters(&mut tx, &[me.id, to.id]).await?;
    if let Some(key) = &key {
        let earlier: Option<i64> =
            sqlx::query_scalar("SELECT id FROM mail WHERE sender_id = $1 AND client_key = $2")
                .bind(me.id)
                .bind(key)
                .fetch_optional(&mut *tx)
                .await?;
        if let Some(id) = earlier {
            // As sent, even if the recipient has thrown it away since.
            let mail = view_any(&mut *tx, id).await?.ok_or_else(mail_not_found)?;
            let crowns = ledger::adjust_crowns(&mut tx, me.id, 0, Reason::MailSend, None)
                .await
                .map_err(ledger_error)?;
            tx.commit().await?;
            return Ok((StatusCode::CREATED, Json(Sent { mail, crowns })));
        }
    }
    let mail_id = create(
        &mut tx,
        &NewMail {
            recipient: to.id,
            sender_id: Some(me.id),
            sender_name: &me.name,
            kind: KIND_LETTER,
            subject: &subject,
            body: &body,
            crowns: req.crowns,
            ref_id: None,
            client_key: key.as_deref(),
            with_items: !attachments.is_empty(),
        },
    )
    .await?;

    let ids: Vec<i64> = attachments.iter().map(|a| a.item_id).collect();
    let locked = items::lock(&mut tx, me.id, &ids).await?;
    if locked.len() != ids.len() {
        return Err(item_not_found());
    }
    for item in &locked {
        let want = attachments
            .iter()
            .find(|a| a.item_id == item.id)
            .map_or(0, |a| a.quantity);
        if item.location != LOCATION_INVENTORY || want > item.quantity {
            return Err(item_unavailable());
        }
        let part = items::take(&mut tx, item, want, Reason::MailSend, Some(mail_id))
            .await
            .map_err(item_error)?;
        attach(
            &mut tx,
            mail_id,
            to.id,
            &split_row(item, part, want),
            Reason::MailSend,
            mail_id,
        )
        .await
        .map_err(item_error)?;
    }
    // The Outbox's record of what went (attachment rows go on collect).
    let attached: Vec<i64> =
        sqlx::query_scalar("SELECT item_id FROM mail_attachments WHERE mail_id = $1")
            .bind(mail_id)
            .fetch_all(&mut *tx)
            .await?;
    let sent_items = item_views(&mut *tx, &attached).await?;
    sqlx::query("UPDATE mail SET sent_items = $2 WHERE id = $1")
        .bind(mail_id)
        .bind(sqlx::types::Json(&sent_items))
        .execute(&mut *tx)
        .await?;
    ledger::adjust_crowns(&mut tx, me.id, -req.crowns, Reason::MailSend, Some(mail_id))
        .await
        .map_err(ledger_error)?;
    let crowns = ledger::adjust_crowns(
        &mut tx,
        me.id,
        -i64::from(rules.postage),
        Reason::Postage,
        Some(mail_id),
    )
    .await
    .map_err(ledger_error)?;
    let mail = view(&mut *tx, to.id, mail_id)
        .await?
        .ok_or_else(mail_not_found)?;
    tx.commit().await?;

    if let Some(world) = state.world() {
        world.wallet_changed(me.id, crowns);
    }
    notify(&state, to.id).await;
    Ok((StatusCode::CREATED, Json(Sent { mail, crowns })))
}

/// `POST /api/mail/{id}/collect` → `{ items, crowns }`: the items now in
/// the inventory (after merging into stacks) and the balance after.
#[derive(Debug, Serialize)]
pub struct Collected {
    pub items: Vec<ItemView>,
    pub crowns: i64,
}

/// Collects a mail's items and Crowns in one transaction: the recipient
/// locked, then the mail row (so a second collect waits and then finds it
/// collected), then the items.
pub async fn collect(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(id): ApiPath<i64>,
) -> Result<Json<Collected>, ApiError> {
    let me = player.character.id;
    let mut tx = state.pool.begin().await?;
    lock_characters(&mut tx, &[me]).await?;
    let mail: Option<(i64, bool, bool)> = sqlx::query_as(
        "SELECT crowns, collected_at IS NOT NULL, read_at IS NULL FROM mail
         WHERE id = $1 AND recipient_id = $2 AND recipient_deleted_at IS NULL FOR UPDATE",
    )
    .bind(id)
    .bind(me)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((enclosed, collected, was_unread)) = mail else {
        return Err(mail_not_found());
    };
    if collected {
        return Err(ApiError::conflict(
            "already_collected",
            "You already collected what came with that owl.",
        ));
    }
    let ids: Vec<i64> =
        sqlx::query_scalar("DELETE FROM mail_attachments WHERE mail_id = $1 RETURNING item_id")
            .bind(id)
            .fetch_all(&mut *tx)
            .await?;
    let locked = items::lock(&mut tx, me, &ids).await?;
    if locked.len() != ids.len() || locked.iter().any(|i| i.location != super::LOCATION_MAIL) {
        return Err(ApiError::internal(format!(
            "mail {id}: attachments are not all in the recipient's mail"
        )));
    }
    let mut landed = Vec::new();
    for item in &locked {
        let rows = items::into_inventory(&mut tx, item, Reason::MailCollect, Some(id))
            .await
            .map_err(item_error)?;
        landed.extend(rows);
    }
    let crowns = ledger::adjust_crowns(&mut tx, me, enclosed, Reason::MailCollect, Some(id))
        .await
        .map_err(ledger_error)?;
    sqlx::query(
        "UPDATE mail SET collected_at = now(), read_at = coalesce(read_at, now()) WHERE id = $1",
    )
    .bind(id)
    .execute(&mut *tx)
    .await?;
    landed.sort_unstable();
    landed.dedup();
    let items = item_views(&mut *tx, &landed).await?;
    tx.commit().await?;

    if enclosed > 0
        && let Some(world) = state.world()
    {
        world.wallet_changed(me, crowns);
    }
    if was_unread {
        notify(&state, me).await;
    }
    Ok(Json(Collected { items, crowns }))
}

/// `DELETE /api/mail/{id}` → `204`, once nothing waits in it. A player's
/// letter only leaves this mailbox (its sender's Outbox keeps it until the
/// cleanup); system mail is deleted.
pub async fn delete(
    State(state): State<AppState>,
    Writer(player): Writer,
    ApiPath(id): ApiPath<i64>,
) -> Result<StatusCode, ApiError> {
    let me = player.character.id;
    let mut tx = state.pool.begin().await?;
    let mail: Option<(bool, bool, bool)> = sqlx::query_as(
        "SELECT collected_at IS NOT NULL, read_at IS NULL,
                kind = 'letter' AND sender_id IS NOT NULL
         FROM mail
         WHERE id = $1 AND recipient_id = $2 AND recipient_deleted_at IS NULL FOR UPDATE",
    )
    .bind(id)
    .bind(me)
    .fetch_optional(&mut *tx)
    .await?;
    let Some((collected, was_unread, in_outbox)) = mail else {
        return Err(mail_not_found());
    };
    if !collected {
        return Err(ApiError::conflict(
            "uncollected",
            "Collect what came with that owl before throwing the letter away.",
        ));
    }
    let sql = if in_outbox {
        "UPDATE mail SET recipient_deleted_at = now() WHERE id = $1"
    } else {
        "DELETE FROM mail WHERE id = $1"
    };
    sqlx::query(sql).bind(id).execute(&mut *tx).await?;
    tx.commit().await?;
    if was_unread {
        notify(&state, me).await;
    }
    Ok(StatusCode::NO_CONTENT)
}

/// `GET /api/mail/sent?limit=&after=` → `{ items: [SentMail], next }`: the
/// player's own letters, newest first (index `mail (sender_id, id) WHERE
/// kind = 'letter'`). Never system mail, never anyone else's.
pub async fn sent(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<SentView>>, ApiError> {
    let me = player.character.id;
    let after: Option<i64> = page.after()?;
    let rows: Vec<SentView> = sqlx::query_as(&format!(
        "{SENT_SELECT}
         WHERE m.sender_id = $1 AND m.kind = '{KIND_LETTER}' AND ($2::bigint IS NULL OR m.id < $2)
         ORDER BY m.id DESC LIMIT $3"
    ))
    .bind(me)
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    Ok(Json(Page::from_rows(rows, page.limit()?, |m| m.id)))
}

/// `SentMailDetail`: a [`SentView`] with its text and the items as sent.
#[derive(Debug, Serialize)]
pub struct SentDetail {
    #[serde(flatten)]
    pub mail: SentView,
    pub body: String,
    /// `[Item]` as they were attached (location `mail`).
    pub items: serde_json::Value,
}

/// `GET /api/mail/sent/{id}`: one of the player's own letters, read-only
/// (it marks nothing). `404 mail_not_found` for anyone but its sender.
pub async fn sent_detail(
    State(state): State<AppState>,
    player: Player,
    ApiPath(id): ApiPath<i64>,
) -> Result<Json<SentDetail>, ApiError> {
    let me = player.character.id;
    let mail: SentView = sqlx::query_as(&format!(
        "{SENT_SELECT} WHERE m.id = $1 AND m.sender_id = $2 AND m.kind = '{KIND_LETTER}'"
    ))
    .bind(id)
    .bind(me)
    .fetch_optional(&state.pool)
    .await?
    .ok_or_else(sent_not_found)?;
    let (body, items): (String, Option<serde_json::Value>) =
        sqlx::query_as("SELECT body, sent_items FROM mail WHERE id = $1")
            .bind(id)
            .fetch_one(&state.pool)
            .await?;
    Ok(Json(SentDetail {
        mail,
        body,
        items: items.unwrap_or_else(|| serde_json::Value::Array(Vec::new())),
    }))
}

fn sent_not_found() -> ApiError {
    ApiError::not_found("mail_not_found", "You sent no such letter.")
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::{get, post};
    let rules = &shipped().economy.mail;
    let letters = LetterLimiter(Arc::new(RateLimiter::new(RateLimit {
        burst: rules.send_burst,
        per: Duration::from_secs(u64::from(rules.send_refill_secs)),
    })));
    let router = Router::new()
        .route("/mail", get(list).post(send))
        .route("/mail/sent", get(sent))
        .route("/mail/sent/{id}", get(sent_detail))
        .route("/mail/{id}", get(detail).delete(delete))
        .route("/mail/{id}/collect", post(collect))
        .layer(Extension(letters));
    write_limited(router, ECONOMY_RATE_LIMIT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn letters_keep_line_breaks_but_lose_hidden_characters() {
        assert_eq!(
            clean("  Hi\u{200B} there\r\nBye\t! ", true),
            "Hi there\nBye !"
        );
        assert_eq!(clean("Sub\nject\u{202E}", false), "Sub ject");
    }
}
