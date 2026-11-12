//! The Crown wallet: the starting purse (new characters, characters from
//! before purses existed, and the once-only top-up for a grant that has no
//! purse), `GET /api/wallet`, the paginated history, and the HUD numbers
//! the gateway loads.

mod common;

use axum::http::StatusCode;
use common::TestApp;
use server::catalog::shipped;
use server::ledger::audit::reconcile;
use server::ledger::{self, Reason};
use server::wallet;

fn purse() -> i64 {
    i64::from(shipped().economy.starting_purse)
}

#[tokio::test]
async fn new_characters_start_with_the_purse_and_its_history() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let res = app.get("/api/wallet", p.token()).await;
    assert_eq!(res.status, StatusCode::OK);
    assert_eq!(res.body["crowns"], purse());

    let history = app.get("/api/wallet/history", p.token()).await;
    assert_eq!(history.status, StatusCode::OK, "{:?}", history.body);
    let items = history.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["delta"], purse());
    assert_eq!(items[0]["balance_after"], purse());
    assert_eq!(items[0]["reason"], "starting_purse");
    assert!(history.body["next"].is_null());
    reconcile(&app.state.pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn history_pages_newest_first() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    // Five more movements: -1 .. -5.
    let mut tx = app.state.pool.begin().await.unwrap();
    wallet::lock_characters(&mut tx, &[p.id]).await.unwrap();
    for n in 1..=5 {
        ledger::adjust_crowns(&mut tx, p.id, -n, Reason::Admin, Some(n))
            .await
            .unwrap();
    }
    tx.commit().await.unwrap();

    let mut seen = Vec::new();
    let mut after = String::new();
    loop {
        let res = app
            .get(
                &format!("/api/wallet/history?limit=2&after={after}"),
                p.token(),
            )
            .await;
        assert_eq!(res.status, StatusCode::OK);
        for row in res.body["items"].as_array().unwrap() {
            seen.push(row["delta"].as_i64().unwrap());
        }
        match res.body["next"].as_str() {
            Some(n) => after = n.to_owned(),
            None => break,
        }
    }
    assert_eq!(seen, vec![-5, -4, -3, -2, -1, purse()]);
    let bad = app.get("/api/wallet/history?after=nope", p.token()).await;
    assert_eq!(bad.status, StatusCode::BAD_REQUEST);
    assert_eq!(bad.body["error"], "invalid_cursor");
    reconcile(&app.state.pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

/// A character whose starter grant exists without a purse (granted by an
/// older server after migration 0007) gets the purse once, on sign-in or
/// on its next wallet request, however many race for it.
#[tokio::test]
async fn a_missing_purse_is_granted_exactly_once() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    // Make it look like that character: no purse, no purse ledger row. The
    // ledger is append-only, so a fresh character row stands in for it.
    let (account, token) = app.account().await;
    let id: i64 = sqlx::query_scalar(
        "INSERT INTO characters (account_id, name, name_key, house, starter_granted_at)
         VALUES ($1, $2, lower($2), 2, now()) RETURNING id",
    )
    .bind(account)
    .bind(common::unique_name())
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    app.select_directly(account, id).await;
    let pool = app.state.pool.clone();
    let racers: Vec<_> = (0..6)
        .map(|_| {
            let pool = pool.clone();
            tokio::spawn(async move { wallet::hud(&pool, id).await.unwrap() })
        })
        .collect();
    for r in racers {
        assert_eq!(r.await.unwrap().crowns, purse());
    }
    let res = app.get("/api/wallet", Some(&token)).await;
    assert_eq!(res.body["crowns"], purse());
    let rows: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM crown_ledger WHERE character_id = $1 AND reason = 'starting_purse'",
    )
    .bind(id)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(rows, 1);
    reconcile(&app.state.pool, &[p.id, id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn the_hud_counts_unread_mail() {
    // The owl order below skips the catalogue stand's check.
    let app = TestApp::with_config(|c| c.stations_anywhere = true).await;
    let p = app.player("Serpentyne").await;
    let hud = wallet::hud(&app.state.pool, p.id).await.unwrap();
    assert_eq!(hud.crowns, purse());
    assert_eq!(hud.unread_mail, 0);
    // An owl-order delivery is one unread mail.
    let res = app
        .post(
            "/api/shop/orders",
            serde_json::json!({ "lines": [{ "item_def": 3001, "quantity": 2 }] }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let hud = wallet::hud(&app.state.pool, p.id).await.unwrap();
    assert_eq!(hud.unread_mail, 1);
    assert_eq!(hud.crowns, purse() - 4);
}
