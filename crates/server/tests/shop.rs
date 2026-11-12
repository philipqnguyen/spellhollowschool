//! The owl-order catalogue: what it sells, orders paid in one transaction
//! and delivered by owl mail, retries answered instead of repeated, and the
//! ledger explaining every Crown and item afterwards.

mod common;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer};
use serde_json::{Value, json};
use server::catalog::shipped;
use server::ledger::audit::reconcile;

const WOOL: u16 = 3001;
const STOOL: u16 = 1017;

fn purse() -> i64 {
    i64::from(shipped().economy.starting_purse)
}

fn price_of(def: u16) -> i64 {
    i64::from(shipped().catalog.get(def).unwrap().price.unwrap())
}

async fn order(app: &TestApp, p: &TestPlayer, body: Value) -> common::Response {
    app.post("/api/shop/orders", body, p.token()).await
}

/// The catalogue's own rules, with the stand check switched off
/// (`Config::stations_anywhere`; `owl_order.rs` checks the place).
async fn app() -> TestApp {
    TestApp::with_config(|c| c.stations_anywhere = true).await
}

/// Away from the stand (no world running, no test override) the catalogue
/// neither lists nor takes orders, and says where to go.
#[tokio::test]
async fn away_from_the_stand_the_catalogue_is_closed() {
    let away = TestApp::new().await;
    let p = away.player("Burrowsby").await;
    for res in [
        away.get("/api/shop?limit=5", p.token()).await,
        order(
            &away,
            &p,
            json!({ "lines": [{ "item_def": WOOL, "quantity": 1 }] }),
        )
        .await,
    ] {
        assert_eq!(res.status, StatusCode::FORBIDDEN, "{:?}", res.body);
        assert_eq!(res.body["error"], "not_at_station");
        assert_eq!(
            res.body["message"],
            "Visit the Owl Order Catalogue in your common room."
        );
    }
    let wallet = away.get("/api/wallet", p.token()).await;
    assert_eq!(wallet.body["crowns"], purse());
}

#[tokio::test]
async fn the_catalogue_lists_basic_items_by_id_with_filters() {
    let app = app().await;
    let p = app.player("Roarwing").await;
    let all = app.get("/api/shop?limit=100", p.token()).await;
    assert_eq!(all.status, StatusCode::OK);
    let ids: Vec<u64> = all.body["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|i| i["item_def"].as_u64().unwrap())
        .collect();
    let mut sorted = ids.clone();
    sorted.sort_unstable();
    assert_eq!(ids, sorted, "by item id");
    assert_eq!(ids.len(), shipped().catalog.shop_items().count());
    // Nothing crafted is sold.
    for id in &ids {
        let def = shipped().catalog.get(u16::try_from(*id).unwrap()).unwrap();
        assert!(!def.has_tag("crafted"), "{}", def.name);
    }

    // Pages of 5 walk the same list.
    let mut paged = Vec::new();
    let mut after = String::new();
    loop {
        let res = app
            .get(&format!("/api/shop?limit=5&after={after}"), p.token())
            .await;
        paged.extend(
            res.body["items"]
                .as_array()
                .unwrap()
                .iter()
                .map(|i| i["item_def"].as_u64().unwrap()),
        );
        match res.body["next"].as_str() {
            Some(n) => after = n.to_owned(),
            None => break,
        }
    }
    assert_eq!(paged, ids);

    let dyes = app.get("/api/shop?kind=dye", p.token()).await;
    assert!(dyes.body["items"].as_array().unwrap().iter().all(|i| {
        shipped()
            .catalog
            .get(i["item_def"].as_u64().unwrap() as u16)
            .unwrap()
            .kind
            == sim_core::ItemKind::Dye
    }));
    let fibers = app
        .get("/api/shop?category=materials&subcategory=fibers", p.token())
        .await;
    assert!(
        fibers.body["items"]
            .as_array()
            .unwrap()
            .iter()
            .any(|i| i["item_def"] == WOOL)
    );
    for bad in [
        "/api/shop?category=wands",
        "/api/shop?subcategory=fibers",
        "/api/shop?category=hats&subcategory=fibers",
        "/api/shop?kind=",
        "/api/shop?category=",
    ] {
        let res = app.get(bad, p.token()).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{bad}");
        assert_eq!(res.body["error"], "invalid_query", "{bad}");
    }
}

#[tokio::test]
async fn an_order_is_paid_and_arrives_by_owl() {
    let app = app().await;
    let p = app.player("Burrowsby").await;
    let res = order(
        &app,
        &p,
        json!({ "lines": [{ "item_def": WOOL, "quantity": 12 }, { "item_def": STOOL, "quantity": 2 }] }),
    )
    .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let total = 12 * price_of(WOOL) + 2 * price_of(STOOL);
    assert_eq!(res.body["order"]["total"], total);
    assert_eq!(res.body["crowns"], purse() - total);
    assert_eq!(res.body["order"]["lines"][0]["unit_price"], price_of(WOOL));
    let mail_id = res.body["order"]["mail_id"].as_i64().unwrap();

    // The delivery waits in the mailbox: unread, uncollected, three items
    // (a stack of wool and two stools, one row each).
    let mailbox = app.get("/api/mail", p.token()).await;
    assert_eq!(mailbox.body["unread"], 1);
    let mail = &mailbox.body["items"][0];
    assert_eq!(mail["id"], mail_id);
    assert_eq!(mail["kind"], "shop");
    assert_eq!(mail["sender_name"], server::mail::SENDER_OWL_ORDER);
    assert_eq!(mail["sender_id"], Value::Null);
    assert_eq!(mail["collected"], false);
    assert_eq!(mail["attachments"], 3);
    let in_mail = app.get("/api/inventory?location=mail", p.token()).await;
    assert_eq!(in_mail.body["items"].as_array().unwrap().len(), 3);

    let collected = app
        .post(
            &format!("/api/mail/{mail_id}/collect"),
            json!({}),
            p.token(),
        )
        .await;
    assert_eq!(collected.status, StatusCode::OK, "{:?}", collected.body);
    let wool: Vec<&Value> = collected.body["items"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|i| i["item_def"] == WOOL)
        .collect();
    assert_eq!(wool.len(), 1);
    assert_eq!(wool[0]["quantity"], 12);
    assert_eq!(wool[0]["location"], "inventory");

    // A second delivery of wool merges into the same stack on collection.
    let res = order(
        &app,
        &p,
        json!({ "lines": [{ "item_def": WOOL, "quantity": 5 }] }),
    )
    .await;
    let second = res.body["order"]["mail_id"].as_i64().unwrap();
    let collected = app
        .post(&format!("/api/mail/{second}/collect"), json!({}), p.token())
        .await;
    assert_eq!(collected.body["items"][0]["quantity"], 17);
    assert_eq!(collected.body["items"][0]["id"], wool[0]["id"]);
    reconcile(&app.state.pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn bad_orders_and_empty_purses_change_nothing() {
    let app = app().await;
    let p = app.player("Skytalon").await;
    let crafted = shipped()
        .catalog
        .items
        .iter()
        .find(|d| d.has_tag("crafted"))
        .unwrap()
        .id;
    for body in [
        json!({ "lines": [] }),
        json!({ "lines": [{ "item_def": crafted, "quantity": 1 }] }),
        json!({ "lines": [{ "item_def": 9999, "quantity": 1 }] }),
        json!({ "lines": [{ "item_def": WOOL, "quantity": 0 }] }),
        json!({ "lines": [{ "item_def": WOOL, "quantity": 100 }] }),
        json!({ "lines": [{ "item_def": STOOL, "quantity": 11 }] }),
        json!({ "lines": [{ "item_def": WOOL, "quantity": 1 }, { "item_def": WOOL, "quantity": 1 }] }),
    ] {
        let res = order(&app, &p, body.clone()).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{body}");
        assert_eq!(res.body["error"], "invalid_order", "{body}");
    }
    // Two beds cost more than the purse.
    let res = order(
        &app,
        &p,
        json!({ "lines": [{ "item_def": 1001, "quantity": 2 }] }),
    )
    .await;
    assert_eq!(res.status, StatusCode::CONFLICT);
    assert_eq!(res.body["error"], "insufficient_crowns");
    let wallet = app.get("/api/wallet", p.token()).await;
    assert_eq!(wallet.body["crowns"], purse());
    let mailbox = app.get("/api/mail", p.token()).await;
    assert!(mailbox.body["items"].as_array().unwrap().is_empty());
    let orders: i64 =
        sqlx::query_scalar("SELECT count(*) FROM shop_orders WHERE character_id = $1")
            .bind(p.id)
            .fetch_one(&app.state.pool)
            .await
            .unwrap();
    assert_eq!(orders, 0, "the refused order rolled back");
    reconcile(&app.state.pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

/// A retried order (the same `request_id`), even many at once, is placed
/// and paid once.
#[tokio::test]
async fn retried_orders_are_answered_not_repeated() {
    let app = app().await;
    let p = app.player("Serpentyne").await;
    let body = json!({ "lines": [{ "item_def": WOOL, "quantity": 3 }], "request_id": "order-1" });
    let racers: Vec<_> = (0..5)
        .map(|_| {
            let app = TestApp::from_state(app.state.clone());
            let (p, body) = (p.clone(), body.clone());
            tokio::spawn(async move { order(&app, &p, body).await })
        })
        .collect();
    let mut ids = Vec::new();
    for r in racers {
        let res = r.await.unwrap();
        assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
        ids.push(res.body["order"]["id"].as_i64().unwrap());
        assert_eq!(res.body["crowns"], purse() - 3 * price_of(WOOL));
    }
    ids.dedup();
    assert_eq!(ids.len(), 1, "one order");
    let mailbox = app.get("/api/mail", p.token()).await;
    assert_eq!(mailbox.body["items"].as_array().unwrap().len(), 1);
    // A new request id is a new order.
    let res = order(
        &app,
        &p,
        json!({ "lines": [{ "item_def": WOOL, "quantity": 3 }], "request_id": "order-2" }),
    )
    .await;
    assert_ne!(res.body["order"]["id"].as_i64().unwrap(), ids[0]);
    reconcile(&app.state.pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}
