//! The Owl Exchange over HTTP: listing (fee, split stacks, the listing
//! limit, items that cannot be listed), browsing with every filter and sort
//! and keyset pages, buying (price, tax, both mails), taking down, expiry,
//! the counter requirement and the search limit, and the duplication hunt:
//! racing buyers, a take-down racing a purchase. Each test ends with the
//! ledger reconciliation of the characters it used.
//!
//! The Exchange is shared by every test (and earlier runs), so browse
//! checks look at this test's own listings within correctly sorted pages
//! rather than at whole pages.

mod common;

use std::collections::HashSet;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer};
use protocol::QPos;
use serde_json::{Value, json};
use server::AppState;
use server::catalog::shipped;
use server::items::{self, NewItem};
use server::ledger::Reason;
use server::ledger::audit::reconcile;
use sim_core::{PlaceArea, PlaceSurface};

const WOOL: u16 = 3001;
/// Rare materials nobody else's tests list.
const COILWING: u16 = 3028;
const VEILAPE: u16 = 3030;

fn purse() -> i64 {
    i64::from(shipped().economy.starting_purse)
}

fn fee() -> i64 {
    i64::from(shipped().economy.exchange.listing_fee)
}

/// An app whose characters may use the counter from anywhere.
async fn app() -> TestApp {
    TestApp::with_config(|c| c.stations_anywhere = true).await
}

async fn grant(app: &TestApp, p: &TestPlayer, def: u16, quantity: u32) -> i64 {
    let mut tx = app.state.pool.begin().await.unwrap();
    server::wallet::lock_characters(&mut tx, &[p.id])
        .await
        .unwrap();
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

/// Requests through a fresh router (its own write budget), so a test can
/// make more writes than one burst.
fn fresh(state: &AppState) -> TestApp {
    TestApp::from_state(state.clone())
}

async fn list(
    app: &TestApp,
    p: &TestPlayer,
    item: i64,
    quantity: u32,
    price: i64,
) -> common::Response {
    fresh(&app.state)
        .post(
            "/api/exchange/listings",
            json!({ "item_id": item, "quantity": quantity, "price": price }),
            p.token(),
        )
        .await
}

async fn listed(app: &TestApp, p: &TestPlayer, item: i64, quantity: u32, price: i64) -> i64 {
    let res = list(app, p, item, quantity, price).await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    res.body["listing"]["id"].as_i64().unwrap()
}

async fn buy(state: &AppState, p: &TestPlayer, id: i64) -> common::Response {
    fresh(state)
        .post(
            &format!("/api/exchange/listings/{id}/buy"),
            json!({}),
            p.token(),
        )
        .await
}

async fn crowns(app: &TestApp, p: &TestPlayer) -> i64 {
    app.get("/api/wallet", p.token()).await.body["crowns"]
        .as_i64()
        .unwrap()
}

async fn item_place(app: &TestApp, id: i64) -> Option<(i64, String, i32)> {
    sqlx::query_as("SELECT owner_id, location, quantity FROM items WHERE id = $1")
        .bind(id)
        .fetch_optional(&app.state.pool)
        .await
        .unwrap()
}

/// The mails of `p` (first page, newest first).
async fn mails(app: &TestApp, p: &TestPlayer) -> Vec<Value> {
    app.get("/api/mail?limit=100", p.token()).await.body["items"]
        .as_array()
        .unwrap()
        .clone()
}

#[tokio::test]
async fn listing_pays_the_fee_and_splits_the_stack() {
    let app = app().await;
    let p = app.player("Burrowsby").await;
    let wool = grant(&app, &p, WOOL, 30).await;
    let res = list(&app, &p, wool, 10, 45).await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(res.body["crowns"], purse() - fee());
    let l = &res.body["listing"];
    assert_eq!(l["item_def"], WOOL);
    assert_eq!(l["quantity"], 10);
    assert_eq!(l["price"], 45);
    assert_eq!(l["state"], "active");
    assert_eq!(l["seller_name"], p.name);
    assert_eq!(l["category"], "materials");
    assert_eq!(l["subcategory"], "fibers");
    let dyes = shipped().catalog.get(WOOL).unwrap().dyes.to_array();
    assert_eq!(l["dyes"], json!(dyes));
    assert_eq!(l["colour"], dyes[0]);
    let id = l["id"].as_i64().unwrap();
    assert_eq!(item_place(&app, wool).await.unwrap().2, 20);
    let on_exchange = app.get("/api/inventory?location=exchange", p.token()).await;
    assert_eq!(on_exchange.body["items"][0]["quantity"], 10);

    // A retried listing (same request id) is answered, not repeated.
    let body = json!({ "item_id": wool, "quantity": 5, "price": 9, "request_id": "l-1" });
    let first = fresh(&app.state)
        .post("/api/exchange/listings", body.clone(), p.token())
        .await;
    let again = fresh(&app.state)
        .post("/api/exchange/listings", body, p.token())
        .await;
    assert_eq!(first.status, StatusCode::CREATED);
    assert_eq!(first.body["listing"]["id"], again.body["listing"]["id"]);
    assert_eq!(item_place(&app, wool).await.unwrap().2, 15);
    assert_eq!(crowns(&app, &p).await, purse() - 2 * fee());

    // Mine, newest first, filterable by state.
    let mine = app.get("/api/exchange/mine?state=active", p.token()).await;
    let ids: Vec<i64> = mine.body["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|l| l["id"].as_i64().unwrap())
        .collect();
    assert_eq!(ids, vec![first.body["listing"]["id"].as_i64().unwrap(), id]);
    let sold = app.get("/api/exchange/mine?state=sold", p.token()).await;
    assert!(sold.body["items"].as_array().unwrap().is_empty());
    reconcile(&app.state.pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Worn, placed, mailed, someone else's: none of these can be listed, and a
/// refusal changes nothing.
#[tokio::test]
async fn items_that_are_not_free_cannot_be_listed() {
    let app = app().await;
    let (p, q) = (app.player("Roarwing").await, app.player("Skytalon").await);
    let worn = app.get("/api/equipment", p.token()).await.body["body"]["id"]
        .as_i64()
        .unwrap();
    // A chair placed in the dorm, through the dorm store.
    let chair = app
        .get(
            "/api/inventory?kind=furniture&location=inventory&limit=100",
            p.token(),
        )
        .await
        .body["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|i| i["item_def"] == 1005)
        .unwrap()["id"]
        .as_i64()
        .unwrap();
    let floor = [PlaceArea {
        id: "floor".into(),
        surface: PlaceSurface::Floor,
        min: [-5.0, 0.0, -5.0],
        max: [5.0, 0.0, 5.0],
        facing: None,
        clear: None,
        frame: None,
    }];
    server::dorm::store::place(
        &app.state.pool,
        &shipped().catalog,
        p.id,
        chair,
        QPos { x: 0, y: 0, z: 0 },
        0,
        &floor,
        &server::dorm::rules::Way {
            shell: server::dorm::DormShells::embedded()
                .world(protocol::House::Roarwing)
                .clone(),
            room: Vec::new(),
            people: Vec::new(),
        },
    )
    .await
    .unwrap();
    // Wool sent by letter to Q: in Q's mail until collected.
    let wool = grant(&app, &p, WOOL, 4).await;
    let letter = app
        .post(
            "/api/mail",
            json!({ "to": q.name, "subject": "Wool", "items": [{ "item_id": wool, "quantity": 4 }] }),
            p.token(),
        )
        .await;
    assert_eq!(letter.status, StatusCode::CREATED, "{:?}", letter.body);
    let theirs = grant(&app, &q, WOOL, 2).await;
    let hat = grant(&app, &p, 101, 1).await;

    for (item, quantity, price, code) in [
        (worn, 1, 10, "item_unavailable"),
        (chair, 1, 10, "item_unavailable"),
        // Sent away: it is Q's now.
        (wool, 1, 10, "item_not_found"),
        (theirs, 1, 10, "item_not_found"),
        (hat, 2, 10, "item_unavailable"),
        (hat, 1, 0, "invalid_price"),
        (hat, 1, 1_000_001, "invalid_price"),
    ] {
        let res = list(&app, &p, item, quantity, price).await;
        assert_eq!(res.body["error"], code, "item {item}: {:?}", res.body);
    }
    // Q could list the wool only after collecting it.
    let res = list(&app, &q, wool, 1, 10).await;
    assert_eq!(res.body["error"], "item_unavailable");
    assert_eq!(
        crowns(&app, &p).await,
        purse() - i64::from(shipped().economy.mail.postage)
    );
    reconcile(&app.state.pool, &[p.id, q.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn the_listing_limit_and_the_fee_are_enforced() {
    let app = app().await;
    let p = app.player("Serpentyne").await;
    let wool = grant(&app, &p, WOOL, 30).await;
    let max = shipped().economy.exchange.active_listings_max;
    for _ in 0..max {
        listed(&app, &p, wool, 1, 5).await;
    }
    let over = list(&app, &p, wool, 1, 5).await;
    assert_eq!(over.status, StatusCode::CONFLICT);
    assert_eq!(over.body["error"], "listing_limit");
    // Taking one down frees a place.
    let mine = app.get("/api/exchange/mine?limit=1", p.token()).await;
    let one = mine.body["items"][0]["id"].as_i64().unwrap();
    let down = fresh(&app.state)
        .delete(&format!("/api/exchange/listings/{one}"), p.token())
        .await;
    assert_eq!(down.status, StatusCode::NO_CONTENT);
    let balance = crowns(&app, &p).await;
    assert_eq!(balance, purse() - i64::from(max) * fee());

    // Not enough for the fee: refused, nothing listed.
    let q = app.player("Roarwing").await;
    let hat = grant(&app, &q, 101, 1).await;
    let spend = app
        .post(
            "/api/shop/orders",
            json!({ "lines": [{ "item_def": 1001, "quantity": 1 }, { "item_def": 3001, "quantity": 9 }] }),
            q.token(),
        )
        .await;
    assert_eq!(spend.status, StatusCode::CREATED, "{:?}", spend.body);
    assert!(spend.body["crowns"].as_i64().unwrap() < fee());
    let poor = list(&app, &q, hat, 1, 10).await;
    assert_eq!(poor.body["error"], "insufficient_crowns");
    assert_eq!(item_place(&app, hat).await.unwrap().1, "inventory");
    reconcile(&app.state.pool, &[p.id, q.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn a_purchase_mails_the_item_and_the_proceeds_minus_tax() {
    let app = app().await;
    let (seller, buyer) = (app.player("Skytalon").await, app.player("Burrowsby").await);
    let hat = grant(&app, &seller, 101, 1).await;
    let price = 60;
    let id = listed(&app, &seller, hat, 1, price).await;

    let own = buy(&app.state, &seller, id).await;
    assert_eq!(own.status, StatusCode::BAD_REQUEST);
    assert_eq!(own.body["error"], "own_listing");
    let res = buy(&app.state, &buyer, id).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["crowns"], purse() - price);
    let mail_id = res.body["mail_id"].as_i64().unwrap();
    // Asking again (a retry) answers the same purchase.
    let again = buy(&app.state, &buyer, id).await;
    assert_eq!(again.status, StatusCode::OK);
    assert_eq!(again.body["mail_id"], mail_id);
    assert_eq!(crowns(&app, &buyer).await, purse() - price);
    // Someone else is too late.
    let late = app.player("Serpentyne").await;
    let res = buy(&app.state, &late, id).await;
    assert_eq!(res.status, StatusCode::CONFLICT);
    assert_eq!(res.body["error"], "listing_closed");

    // The buyer's mail holds the hat; the seller's the proceeds.
    let bought = &mails(&app, &buyer).await[0];
    assert_eq!(bought["id"], mail_id);
    assert_eq!(bought["kind"], "exchange_bought");
    assert_eq!(bought["attachments"], 1);
    assert_eq!(
        item_place(&app, hat).await.unwrap(),
        (buyer.id, "mail".into(), 1)
    );
    let tax = shipped().economy.exchange.sales_tax(price).unwrap();
    let sold = &mails(&app, &seller).await[0];
    assert_eq!(sold["kind"], "exchange_sold");
    assert_eq!(sold["crowns"], price - tax);
    let collect = app
        .post(
            &format!("/api/mail/{}/collect", sold["id"]),
            json!({}),
            seller.token(),
        )
        .await;
    assert_eq!(collect.body["crowns"], purse() - fee() + price - tax);
    let listing = app
        .get("/api/exchange/mine?state=sold", seller.token())
        .await;
    assert_eq!(listing.body["items"][0]["id"], id);
    let (state, recorded_tax): (String, i64) =
        sqlx::query_as("SELECT state, tax FROM exchange_listings WHERE id = $1")
            .bind(id)
            .fetch_one(&app.state.pool)
            .await
            .unwrap();
    assert_eq!((state.as_str(), recorded_tax), ("sold", tax));

    // Too dear: refused, the listing stays up.
    let pricey = grant(&app, &seller, 102, 1).await;
    let dear = listed(&app, &seller, pricey, 1, 500).await;
    let res = buy(&app.state, &late, dear).await;
    assert_eq!(res.body["error"], "insufficient_crowns");
    let missing = buy(&app.state, &late, i64::MAX).await;
    assert_eq!(missing.body["error"], "listing_not_found");
    assert_eq!(item_place(&app, pricey).await.unwrap().1, "exchange");
    reconcile(&app.state.pool, &[seller.id, buyer.id, late.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Duplication hunt: eight buyers race for one listing. Exactly one gets
/// it, exactly once; everyone else keeps their Crowns.
#[tokio::test]
async fn racing_buyers_get_one_item_once() {
    let app = app().await;
    let seller = app.player("Roarwing").await;
    let mut buyers = Vec::new();
    for _ in 0..8 {
        buyers.push(app.player("Serpentyne").await);
    }
    let wool = grant(&app, &seller, WOOL, 12).await;
    let id = listed(&app, &seller, wool, 12, 40).await;
    let racers: Vec<_> = buyers
        .iter()
        .map(|b| {
            let (state, b) = (app.state.clone(), b.clone());
            tokio::spawn(async move { buy(&state, &b, id).await })
        })
        .collect();
    let mut winners = Vec::new();
    for (r, b) in racers.into_iter().zip(&buyers) {
        let res = r.await.unwrap();
        match res.status {
            StatusCode::OK => winners.push(b.id),
            StatusCode::CONFLICT => assert_eq!(res.body["error"], "listing_closed"),
            other => panic!("unexpected {other}: {:?}", res.body),
        }
    }
    assert_eq!(winners.len(), 1);
    let mut spent = 0;
    for b in &buyers {
        spent += purse() - crowns(&app, b).await;
    }
    assert_eq!(spent, 40, "paid once");
    let sold_mails: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM mail WHERE ref_id = $1 AND kind IN ('exchange_sold', 'exchange_bought')",
    )
    .bind(id)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(sold_mails, 2, "one purchase mail and one proceeds mail");
    let mut everyone: Vec<i64> = buyers.iter().map(|b| b.id).collect();
    everyone.push(seller.id);
    reconcile(&app.state.pool, &everyone)
        .await
        .unwrap()
        .assert_clean();
}

/// Duplication hunt: a take-down racing purchases. One of them wins; the
/// item ends up in exactly one mail.
#[tokio::test]
async fn a_take_down_racing_a_purchase_settles_once() {
    let app = app().await;
    let seller = app.player("Burrowsby").await;
    let buyers = [app.player("Skytalon").await, app.player("Roarwing").await];
    for round in 0..4 {
        let hat = grant(&app, &seller, 101, 1).await;
        let id = listed(&app, &seller, hat, 1, 5).await;
        let down = {
            let (state, s) = (app.state.clone(), seller.clone());
            tokio::spawn(async move {
                fresh(&state)
                    .delete(&format!("/api/exchange/listings/{id}"), s.token())
                    .await
                    .status
            })
        };
        let purchases: Vec<_> = buyers
            .iter()
            .map(|b| {
                let (state, b) = (app.state.clone(), b.clone());
                tokio::spawn(async move { buy(&state, &b, id).await.status })
            })
            .collect();
        let mut wins = usize::from(down.await.unwrap() == StatusCode::NO_CONTENT);
        for p in purchases {
            wins += usize::from(p.await.unwrap() == StatusCode::OK);
        }
        assert_eq!(wins, 1, "round {round}");
        let attached: i64 =
            sqlx::query_scalar("SELECT count(*) FROM mail_attachments WHERE item_id = $1")
                .bind(hat)
                .fetch_one(&app.state.pool)
                .await
                .unwrap();
        assert_eq!(attached, 1, "round {round}");
    }
    reconcile(&app.state.pool, &[seller.id, buyers[0].id, buyers[1].id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn take_downs_and_expiry_mail_the_item_back() {
    let app = app().await;
    let (p, other) = (app.player("Serpentyne").await, app.player("Roarwing").await);
    let wool = grant(&app, &p, WOOL, 9).await;
    let id = listed(&app, &p, wool, 4, 20).await;
    let theirs = fresh(&app.state)
        .delete(&format!("/api/exchange/listings/{id}"), other.token())
        .await;
    assert_eq!(theirs.body["error"], "listing_not_found");
    let down = fresh(&app.state)
        .delete(&format!("/api/exchange/listings/{id}"), p.token())
        .await;
    assert_eq!(down.status, StatusCode::NO_CONTENT);
    let twice = fresh(&app.state)
        .delete(&format!("/api/exchange/listings/{id}"), p.token())
        .await;
    assert_eq!(twice.body["error"], "listing_closed");
    let returned = &mails(&app, &p).await[0];
    assert_eq!(returned["kind"], "exchange_returned");
    assert_eq!(returned["sender_name"], server::mail::SENDER_EXCHANGE);
    assert_eq!(returned["attachments"], 1);
    // The fee is not refunded.
    assert_eq!(crowns(&app, &p).await, purse() - fee());

    // Five days on, the expiry job sends the next one back.
    let id = listed(&app, &p, wool, 5, 20).await;
    sqlx::query(
        "UPDATE exchange_listings SET expires_at = now() - interval '1 second' WHERE id = $1",
    )
    .bind(id)
    .execute(&app.state.pool)
    .await
    .unwrap();
    // Expired listings can no longer be bought or browsed while the job is due.
    let res = buy(&app.state, &other, id).await;
    assert_eq!(res.body["error"], "listing_closed");
    let closed = server::exchange::expire_listings(&app.state.pool, None)
        .await
        .unwrap();
    assert!(closed >= 1);
    let state: String = sqlx::query_scalar("SELECT state FROM exchange_listings WHERE id = $1")
        .bind(id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    assert_eq!(state, "expired");
    let expired = &mails(&app, &p).await[0];
    assert_eq!(expired["kind"], "exchange_expired");
    let collect = app
        .post(
            &format!("/api/mail/{}/collect", expired["id"]),
            json!({}),
            p.token(),
        )
        .await;
    assert_eq!(collect.status, StatusCode::OK);
    // Merged back into the original stack: 9 - 4 (still in the mail) = 5.
    assert_eq!(item_place(&app, wool).await.unwrap().2, 5);
    reconcile(&app.state.pool, &[p.id, other.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Every page of `path` (a browse query), following `next`.
async fn browse_all(app: &TestApp, p: &TestPlayer, path: &str) -> Vec<Value> {
    let mut all = Vec::new();
    let mut after = String::new();
    loop {
        let res = fresh(&app.state)
            .get(&format!("{path}&limit=7&after={after}"), p.token())
            .await;
        assert_eq!(res.status, StatusCode::OK, "{path}: {:?}", res.body);
        all.extend(res.body["items"].as_array().unwrap().iter().cloned());
        match res.body["next"].as_str() {
            Some(n) => after = n.to_owned(),
            None => return all,
        }
    }
}

fn keys(rows: &[Value]) -> Vec<(i64, i64)> {
    rows.iter()
        .map(|l| (l["price"].as_i64().unwrap(), l["id"].as_i64().unwrap()))
        .collect()
}

#[tokio::test]
async fn browsing_filters_sorts_and_pages() {
    let app = app().await;
    let p = app.player("Roarwing").await;
    let viewer = app.player("Burrowsby").await;
    let coilwing = grant(&app, &p, COILWING, 3).await;
    let veilape = grant(&app, &p, VEILAPE, 1).await;
    let mut mine = Vec::new();
    for price in [30, 10, 20] {
        mine.push(listed(&app, &p, coilwing, 1, price).await);
    }
    let hair = listed(&app, &p, veilape, 1, 15).await;
    let banner = app
        .get(
            "/api/inventory?kind=furniture&location=inventory&limit=100",
            p.token(),
        )
        .await
        .body["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|i| i["item_def"] == 1009)
        .unwrap()["id"]
        .as_i64()
        .unwrap();
    let banner = listed(&app, &p, banner, 1, 25).await;
    let ours: HashSet<i64> = mine.iter().copied().chain([hair, banner]).collect();
    let only_ours = |rows: &[Value]| -> Vec<i64> {
        rows.iter()
            .map(|l| l["id"].as_i64().unwrap())
            .filter(|id| ours.contains(id))
            .collect()
    };

    // By price, cheapest first, across pages.
    let rows = browse_all(
        &app,
        &viewer,
        "/api/exchange/listings?q=coilwing&sort=price",
    )
    .await;
    let k = keys(&rows);
    assert!(k.windows(2).all(|w| w[0] < w[1]), "sorted by (price, id)");
    assert_eq!(only_ours(&rows), vec![mine[1], mine[2], mine[0]]);
    assert!(rows.iter().all(|l| l["item_def"] == COILWING));
    // Dearest first.
    let rows = browse_all(
        &app,
        &viewer,
        "/api/exchange/listings?q=coilwing&sort=price_desc",
    )
    .await;
    let k = keys(&rows);
    assert!(k.windows(2).all(|w| w[0] > w[1]));
    assert_eq!(only_ours(&rows), vec![mine[0], mine[2], mine[1]]);
    // Newest first (the default).
    let rows = browse_all(&app, &viewer, "/api/exchange/listings?q=COILWING").await;
    let ids: Vec<i64> = rows.iter().map(|l| l["id"].as_i64().unwrap()).collect();
    assert!(ids.windows(2).all(|w| w[0] > w[1]));
    assert_eq!(only_ours(&rows), vec![mine[2], mine[1], mine[0]]);

    // Category, subcategory, colour, level and house filters.
    let rows = browse_all(
        &app,
        &viewer,
        "/api/exchange/listings?category=materials&subcategory=ingredients&q=veilape",
    )
    .await;
    assert_eq!(only_ours(&rows), vec![hair]);
    let colour = shipped().catalog.get(COILWING).unwrap().dyes.primary;
    let rows = browse_all(
        &app,
        &viewer,
        &format!("/api/exchange/listings?q=coilwing&colour={colour}&max_level=1"),
    )
    .await;
    assert_eq!(only_ours(&rows).len(), 3);
    assert!(rows.iter().all(|l| l["colour"] == colour));
    let other = (colour + 1) % 48;
    let rows = browse_all(
        &app,
        &viewer,
        &format!("/api/exchange/listings?q=coilwing&colour={other}"),
    )
    .await;
    assert!(only_ours(&rows).is_empty());
    let rows = browse_all(
        &app,
        &viewer,
        "/api/exchange/listings?q=coilwing&min_level=2",
    )
    .await;
    assert!(only_ours(&rows).is_empty());
    let rows = browse_all(
        &app,
        &viewer,
        "/api/exchange/listings?category=furniture&house=Roarwing&sort=price",
    )
    .await;
    assert!(only_ours(&rows).contains(&banner));
    assert!(rows.iter().all(|l| l["house_theme"] == "roarwing"));
    let rows = browse_all(
        &app,
        &viewer,
        "/api/exchange/listings?category=furniture&house=serpentyne",
    )
    .await;
    assert!(!only_ours(&rows).contains(&banner));
    let none = app
        .get(
            "/api/exchange/listings?q=nothingsellsasthis",
            viewer.token(),
        )
        .await;
    assert_eq!(none.body["items"], json!([]));

    // Bad queries.
    for bad in [
        "/api/exchange/listings?category=wands",
        "/api/exchange/listings?subcategory=fibers",
        "/api/exchange/listings?category=hats&subcategory=fibers",
        "/api/exchange/listings?sort=cheapest",
        "/api/exchange/listings?house=no_such_house",
        "/api/exchange/listings?min_level=0",
        "/api/exchange/listings?q=",
    ] {
        let res = fresh(&app.state).get(bad, viewer.token()).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{bad}: {:?}", res.body);
        assert_eq!(res.body["error"], "invalid_query", "{bad}");
    }
    // A cursor of another sort.
    let page = app
        .get(
            "/api/exchange/listings?q=coilwing&sort=price&limit=1",
            viewer.token(),
        )
        .await;
    let next = page.body["next"].as_str().unwrap();
    let res = app
        .get(
            &format!("/api/exchange/listings?q=coilwing&after={next}"),
            viewer.token(),
        )
        .await;
    assert_eq!(res.body["error"], "invalid_cursor");

    // Clean up: the Exchange outlives this test.
    for id in ours {
        let res = fresh(&app.state)
            .delete(&format!("/api/exchange/listings/{id}"), p.token())
            .await;
        assert_eq!(res.status, StatusCode::NO_CONTENT);
    }
    reconcile(&app.state.pool, &[p.id, viewer.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Away from the counter (no world running and no test override), the
/// Exchange's counter routes refuse; a seller's own list and take-downs
/// work anywhere. Searches are rate limited.
#[tokio::test]
async fn the_counter_is_required_and_searches_are_limited() {
    let away = TestApp::new().await;
    let p = away.player("Skytalon").await;
    let hat = grant(&away, &p, 101, 1).await;
    for res in [
        away.get("/api/exchange/listings", p.token()).await,
        away.post(
            "/api/exchange/listings",
            json!({ "item_id": hat, "quantity": 1, "price": 5 }),
            p.token(),
        )
        .await,
        away.post("/api/exchange/listings/1/buy", json!({}), p.token())
            .await,
    ] {
        assert_eq!(res.status, StatusCode::FORBIDDEN, "{:?}", res.body);
        assert_eq!(res.body["error"], "not_at_station");
    }
    assert_eq!(
        away.get("/api/exchange/mine", p.token()).await.status,
        StatusCode::OK
    );

    let app = app().await;
    let burst = shipped().economy.exchange.search_burst;
    for _ in 0..burst {
        let res = app.get("/api/exchange/listings?limit=1", p.token()).await;
        assert_eq!(res.status, StatusCode::OK);
    }
    let over = app.get("/api/exchange/listings?limit=1", p.token()).await;
    assert_eq!(over.status, StatusCode::TOO_MANY_REQUESTS);
    assert!(over.headers.contains_key("retry-after"));
}
