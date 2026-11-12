//! Hardening: the old-mail cleanup, the ledger checks added
//! to the reconciliation (where Crowns come from, owl orders, postage) and
//! stack keys that respect the maker's mark. Each test ends with the
//! reconciliation of the characters it used.

mod common;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer};
use serde_json::json;
use server::catalog::shipped;
use server::items::{self, Maker, NewItem};
use server::ledger::Reason;
use server::ledger::audit::reconcile;
use server::wallet;

const WOOL: u16 = 3001;

/// `quantity` units of `def` straight into `p`'s inventory (ledgered).
async fn grant(app: &TestApp, p: &TestPlayer, def: u16, quantity: u32) -> i64 {
    let mut tx = app.state.pool.begin().await.unwrap();
    wallet::lock_characters(&mut tx, &[p.id]).await.unwrap();
    let rows = items::give(
        &mut tx,
        &NewItem::plain(p.id, def, quantity, "inventory"),
        Reason::Admin,
        None,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    rows[0]
}

/// Requests through a fresh router (its own write budget).
fn fresh(app: &TestApp) -> TestApp {
    TestApp::from_state(app.state.clone())
}

async fn letter(
    app: &TestApp,
    from: &TestPlayer,
    to: &TestPlayer,
    extra: serde_json::Value,
) -> i64 {
    let mut body = json!({ "to": to.name, "subject": "Hello" });
    body.as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    let res = fresh(app).post("/api/mail", body, from.token()).await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    res.body["mail"]["id"].as_i64().unwrap()
}

async fn mail_exists(app: &TestApp, id: i64) -> bool {
    sqlx::query_scalar("SELECT EXISTS (SELECT 1 FROM mail WHERE id = $1)")
        .bind(id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap()
}

/// Makes mail `id` look collected (or, uncollected, sent) `days` ago.
async fn age(app: &TestApp, id: i64, days: i32) {
    sqlx::query(
        "UPDATE mail SET created_at = now() - make_interval(days => $2),
                         collected_at = CASE WHEN collected_at IS NULL THEN NULL
                                             ELSE now() - make_interval(days => $2) END
         WHERE id = $1",
    )
    .bind(id)
    .bind(days)
    .execute(&app.state.pool)
    .await
    .unwrap();
}

/// Old mail goes once nothing waits in it; items and Crowns that were
/// never collected stay in their mail however old it is.
#[tokio::test]
async fn old_mail_is_cleaned_up_but_never_uncollected_items_or_crowns() {
    let app = TestApp::new().await;
    let (a, b) = (app.player("Roarwing").await, app.player("Burrowsby").await);
    let wool = grant(&app, &a, WOOL, 10).await;
    let plain = letter(&app, &a, &b, json!({})).await;
    let read_plain = letter(&app, &a, &b, json!({})).await;
    let with_items = letter(
        &app,
        &a,
        &b,
        json!({ "items": [{ "item_id": wool, "quantity": 4 }] }),
    )
    .await;
    let with_crowns = letter(&app, &a, &b, json!({ "crowns": 7 })).await;
    let collected = letter(&app, &a, &b, json!({ "crowns": 3 })).await;
    let recent = letter(&app, &a, &b, json!({})).await;
    assert_eq!(
        app.get(&format!("/api/mail/{read_plain}"), b.token())
            .await
            .status,
        StatusCode::OK
    );
    let res = fresh(&app)
        .post(
            &format!("/api/mail/{collected}/collect"),
            json!({}),
            b.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let retention = shipped().economy.mail.retention();
    let days = i32::from(shipped().economy.mail.retention_days) + 1;
    for id in [plain, read_plain, with_items, with_crowns, collected] {
        age(&app, id, days).await;
    }

    let cleaned = server::mail::cleanup(&app.state.pool, retention)
        .await
        .unwrap();
    assert!(cleaned.deleted >= 3, "{cleaned:?}");
    // The plain letter was never read: the owl count changes for its recipient.
    assert!(cleaned.unread_changed.contains(&b.id), "{cleaned:?}");
    assert!(!mail_exists(&app, plain).await);
    assert!(!mail_exists(&app, read_plain).await);
    assert!(!mail_exists(&app, collected).await);
    // Still waiting to be collected, however old: kept with what it carries.
    assert!(mail_exists(&app, with_items).await);
    assert!(mail_exists(&app, with_crowns).await);
    // Inside the retention period: kept.
    assert!(mail_exists(&app, recent).await);
    let waiting = app.get(&format!("/api/mail/{with_items}"), b.token()).await;
    assert_eq!(waiting.body["items"][0]["quantity"], 4);

    // Collected at last: the next run past the retention period takes it.
    for id in [with_items, with_crowns] {
        let res = fresh(&app)
            .post(&format!("/api/mail/{id}/collect"), json!({}), b.token())
            .await;
        assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
        age(&app, id, days).await;
    }
    server::mail::cleanup(&app.state.pool, retention)
        .await
        .unwrap();
    assert!(!mail_exists(&app, with_items).await);
    assert!(!mail_exists(&app, with_crowns).await);
    // The cleanup moves no money and no items: the ledgers still reconcile.
    reconcile(&app.state.pool, &[a.id, b.id])
        .await
        .unwrap()
        .assert_clean();
}

/// The reconciliation's new checks find what the old ones could not: rows
/// that keep every balance and every item replay consistent but take money
/// from the wrong place, pay a purse twice, or deliver more than was paid.
#[tokio::test]
async fn the_audit_checks_where_crowns_and_orders_come_from() {
    // The owl order below skips the catalogue stand's check.
    let app = TestApp::with_config(|c| c.stations_anywhere = true).await;
    let audit = |ids: Vec<i64>| {
        let pool = app.state.pool.clone();
        async move { reconcile(&pool, &ids).await.unwrap() }
    };
    let exec = |sql: &'static str, id: i64| {
        let pool = app.state.pool.clone();
        async move {
            sqlx::query(sql).bind(id).execute(&pool).await.unwrap();
        }
    };

    // A "find" that took Crowns (balance and ledger agree, the sign does not).
    let p = app.player("Skytalon").await;
    audit(vec![p.id]).await.assert_clean();
    exec(
        "WITH c AS (UPDATE characters SET crowns = crowns - 5 WHERE id = $1 RETURNING id, crowns)
         INSERT INTO crown_ledger (character_id, delta, balance_after, reason)
         SELECT id, -5, crowns, 'find' FROM c",
        p.id,
    )
    .await;
    let report = audit(vec![p.id]).await;
    assert_eq!(report.problems.len(), 1, "{:?}", report.problems);
    assert!(
        report.problems[0].detail.contains("'find'"),
        "{:?}",
        report.problems
    );

    // A second starting purse.
    let q = app.player("Serpentyne").await;
    exec(
        "WITH c AS (UPDATE characters SET crowns = crowns + 100 WHERE id = $1 RETURNING id, crowns)
         INSERT INTO crown_ledger (character_id, delta, balance_after, reason)
         SELECT id, 100, crowns, 'starting_purse' FROM c",
        q.id,
    )
    .await;
    let report = audit(vec![q.id]).await;
    assert!(
        report
            .problems
            .iter()
            .any(|p| p.detail.contains("starting purse 2 times")),
        "{:?}",
        report.problems
    );

    // An owl order whose owl brought one unit more than was ordered (the
    // extra unit fully ledgered, so only the order check can see it).
    let r = app.player("Roarwing").await;
    let sold = shipped()
        .catalog
        .shop_items()
        .find(|d| d.stackable)
        .unwrap();
    let res = app
        .post(
            "/api/shop/orders",
            json!({ "lines": [{ "item_def": sold.id, "quantity": 2 }] }),
            r.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let order = res.body["order"]["id"].as_i64().unwrap();
    audit(vec![r.id]).await.assert_clean();
    let mut tx = app.state.pool.begin().await.unwrap();
    wallet::lock_characters(&mut tx, &[r.id]).await.unwrap();
    items::create(
        &mut tx,
        &NewItem::plain(r.id, sold.id, 1, "inventory"),
        server::ledger::ItemOp::Create,
        Reason::ShopOrder,
        Some(order),
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let report = audit(vec![r.id]).await;
    assert_eq!(report.problems.len(), 1, "{:?}", report.problems);
    assert_eq!(report.problems[0].check, "order");

    // A fee paid for someone else's listing.
    let s = app.player("Burrowsby").await;
    let listing: i64 = sqlx::query_scalar(
        "SELECT id FROM exchange_listings WHERE seller_id <> $1 ORDER BY id LIMIT 1",
    )
    .bind(s.id)
    .fetch_optional(&app.state.pool)
    .await
    .unwrap()
    .unwrap_or(i64::MAX);
    sqlx::query(
        "WITH c AS (UPDATE characters SET crowns = crowns - 3 WHERE id = $1 RETURNING id, crowns)
         INSERT INTO crown_ledger (character_id, delta, balance_after, reason, ref_id)
         SELECT id, -3, crowns, 'exchange_fee', $2 FROM c",
    )
    .bind(s.id)
    .bind(listing)
    .execute(&app.state.pool)
    .await
    .unwrap();
    let report = audit(vec![s.id]).await;
    assert!(
        report
            .problems
            .iter()
            .any(|p| p.detail.contains("not the payer's")),
        "{:?}",
        report.problems
    );
}

/// A maker's mark whose character is gone (`maker_id` cleared by the
/// foreign key) still names its maker: plain units never merge into it.
#[tokio::test]
async fn a_makers_mark_never_merges_with_plain_units() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    let maker = app.player("Serpentyne").await;
    let mut tx = app.state.pool.begin().await.unwrap();
    wallet::lock_characters(&mut tx, &[p.id]).await.unwrap();
    let marked = items::give(
        &mut tx,
        &NewItem {
            maker: Some(Maker {
                character_id: maker.id,
                name: maker.name.clone(),
            }),
            ..NewItem::plain(p.id, WOOL, 5, "inventory")
        },
        Reason::Admin,
        None,
    )
    .await
    .unwrap()[0];
    tx.commit().await.unwrap();
    // What `ON DELETE SET NULL` does when the maker's character goes.
    sqlx::query("UPDATE items SET maker_id = NULL WHERE id = $1")
        .bind(marked)
        .execute(&app.state.pool)
        .await
        .unwrap();
    let plain = grant(&app, &p, WOOL, 3).await;
    assert_ne!(plain, marked, "plain wool must not top up the marked stack");
    let (q, name): (i32, Option<String>) =
        sqlx::query_as("SELECT quantity, maker_name FROM items WHERE id = $1")
            .bind(marked)
            .fetch_one(&app.state.pool)
            .await
            .unwrap();
    assert_eq!((q, name.as_deref()), (5, Some(maker.name.as_str())));
    reconcile(&app.state.pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}
