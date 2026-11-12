//! Several characters per account: the cap, the picker's list, picking the
//! character a session plays, and deleting one.

mod common;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer, unique_name};
use serde_json::json;
use server::characters::MAX_PER_ACCOUNT;
use server::ledger::audit::reconcile;

/// Creates a character on the session `token` (which then plays it).
async fn create(app: &TestApp, token: &str, house: &str) -> common::Response {
    app.post(
        "/api/characters",
        json!({ "name": unique_name(), "house": house }),
        Some(token),
    )
    .await
}

/// A second session of `p`'s account, as signing in on another device makes.
async fn another_session(app: &TestApp, account_id: i64) -> String {
    server::auth::create_session(
        &app.state.pool,
        account_id,
        std::time::Duration::from_secs(3600),
    )
    .await
    .unwrap()
    .0
}

#[tokio::test]
async fn an_account_holds_five_characters() {
    let app = TestApp::new().await;
    let (_, token) = app.account().await;
    let list = app.get("/api/characters", Some(&token)).await;
    assert_eq!(list.status, StatusCode::OK);
    assert_eq!(
        list.body,
        json!({ "characters": [], "max": MAX_PER_ACCOUNT })
    );

    let houses = [
        "Roarwing",
        "Burrowsby",
        "Skytalon",
        "Serpentyne",
        "Roarwing",
    ];
    let mut ids = Vec::new();
    for house in houses {
        let res = create(&app, &token, house).await;
        assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
        // Each new character is the one the session plays.
        let me = app.get("/api/characters/me", Some(&token)).await;
        assert_eq!(me.body["id"], res.body["id"]);
        ids.push(res.body["id"].as_i64().unwrap());
    }
    let sixth = create(&app, &token, "Skytalon").await;
    assert_eq!(sixth.status, StatusCode::CONFLICT);
    assert_eq!(sixth.body["error"], "character_limit");

    let list = app.get("/api/characters", Some(&token)).await;
    let listed: Vec<i64> = list.body["characters"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c["id"].as_i64().unwrap())
        .collect();
    assert_eq!(listed, ids, "oldest first");
    let first = &list.body["characters"][0];
    assert_eq!(first["house"], "Roarwing");
    assert_eq!(first["level"], 1);
    assert_eq!(first["appearance_version"], 1);
    assert!(first["appearance"].is_object());
    // Each character is sorted on its own.
    assert_eq!(list.body["characters"][3]["house"], "Serpentyne");
}

#[tokio::test]
async fn concurrent_creations_never_pass_the_cap() {
    let app = TestApp::new().await;
    let (account_id, token) = app.account().await;
    for _ in 0..3 {
        assert_eq!(
            create(&app, &token, "Burrowsby").await.status,
            StatusCode::CREATED
        );
    }
    let racers =
        futures_util::future::join_all((0..6).map(|_| create(&app, &token, "Skytalon"))).await;
    let made = racers
        .iter()
        .filter(|r| r.status == StatusCode::CREATED)
        .count();
    assert_eq!(made, 2);
    for r in racers.iter().filter(|r| r.status != StatusCode::CREATED) {
        assert_eq!(r.body["error"], "character_limit", "{:?}", r.body);
    }
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM characters WHERE account_id = $1")
        .bind(account_id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    assert_eq!(count, MAX_PER_ACCOUNT);
}

#[tokio::test]
async fn a_session_plays_the_character_it_picked() {
    let app = TestApp::new().await;
    let ada = app.player("Roarwing").await;
    let bea = create(&app, &ada.token, "Serpentyne").await;
    let bea_id = bea.body["id"].as_i64().unwrap();

    // A new session (another device) has picked nothing yet.
    let other = another_session(&app, ada.account_id).await;
    let me = app.get("/api/characters/me", Some(&other)).await;
    assert_eq!(me.status, StatusCode::NOT_FOUND);
    assert_eq!(me.body["error"], "no_character");
    let inventory = app.get("/api/inventory", Some(&other)).await;
    assert_eq!(inventory.body["error"], "no_character");
    let info = server::validate_session(&app.state.pool, &other)
        .await
        .unwrap();
    assert_eq!(info.character, None);
    // But it sees the whole account.
    let list = app.get("/api/characters", Some(&other)).await;
    assert_eq!(list.body["characters"].as_array().unwrap().len(), 2);

    // Picking: each session separately.
    let picked = app.select(&other, ada.id).await;
    assert_eq!(picked.status, StatusCode::OK, "{:?}", picked.body);
    assert_eq!(picked.body["name"], ada.name.as_str());
    let me = app.get("/api/characters/me", Some(&other)).await;
    assert_eq!(me.body["id"], ada.id);
    let me = app.get("/api/characters/me", Some(&ada.token)).await;
    assert_eq!(me.body["id"], bea_id, "the first session still plays Bea");
    let info = server::validate_session(&app.state.pool, &other)
        .await
        .unwrap();
    assert_eq!(info.character.unwrap().id, ada.id);

    app.select(&ada.token, ada.id).await;
    let me = app.get("/api/characters/me", Some(&ada.token)).await;
    assert_eq!(me.body["id"], ada.id);
}

#[tokio::test]
async fn nobody_picks_another_accounts_character() {
    let app = TestApp::new().await;
    let ada = app.player("Roarwing").await;
    let eve = app.player("Skytalon").await;
    let res = app.select(&eve.token, ada.id).await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    assert_eq!(res.body["error"], "character_not_found");
    let me = app.get("/api/characters/me", Some(&eve.token)).await;
    assert_eq!(me.body["id"], eve.id, "unchanged");
    let missing = app.select(&eve.token, i64::MAX).await;
    assert_eq!(missing.body["error"], "character_not_found");
    let list = app.get("/api/characters", Some(&eve.token)).await;
    assert_eq!(list.body["characters"].as_array().unwrap().len(), 1);
    // The other account's character is untouched by a delete attempt too.
    let res = app.delete_character(&eve.token, ada.id, &ada.name).await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    assert_eq!(res.body["error"], "character_not_found");
    let me = app.get("/api/characters/me", Some(&ada.token)).await;
    assert_eq!(me.body["id"], ada.id);
}

/// Makes `a` and `b` friends straight in the database.
async fn befriend(app: &TestApp, a: &TestPlayer, b: i64) {
    sqlx::query("INSERT INTO friendships (character_id, friend_id) VALUES ($1, $2), ($2, $1)")
        .bind(a.id)
        .bind(b)
        .execute(&app.state.pool)
        .await
        .unwrap();
}

#[tokio::test]
async fn deleting_a_character_frees_its_name_and_takes_what_it_had() {
    let app = TestApp::new().await;
    let ada = app.player("Burrowsby").await;
    let bea = create(&app, &ada.token, "Roarwing").await;
    let bea_id = bea.body["id"].as_i64().unwrap();
    let bea_name = bea.body["name"].as_str().unwrap().to_owned();
    let friend = app.player("Skytalon").await;
    befriend(&app, &friend, bea_id).await;
    let held: i64 = sqlx::query_scalar("SELECT count(*) FROM items WHERE owner_id = $1")
        .bind(bea_id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    assert!(held > 0, "the starter set");

    let wrong = app.delete_character(&ada.token, bea_id, &ada.name).await;
    assert_eq!(wrong.status, StatusCode::BAD_REQUEST);
    assert_eq!(wrong.body["error"], "name_mismatch");
    let no_body = app
        .delete(&format!("/api/characters/{bea_id}"), Some(&ada.token))
        .await;
    assert!(no_body.status.is_client_error(), "{:?}", no_body.status);

    // Case and spaces do not matter.
    let typed = format!("  {}  ", bea_name.to_uppercase());
    let res = app.delete_character(&ada.token, bea_id, &typed).await;
    assert_eq!(res.status, StatusCode::NO_CONTENT, "{:?}", res.body);

    // The session played Bea: it plays nothing now, and the picker lists Ada only.
    let me = app.get("/api/characters/me", Some(&ada.token)).await;
    assert_eq!(me.body["error"], "no_character");
    let list = app.get("/api/characters", Some(&ada.token)).await;
    assert_eq!(list.body["characters"].as_array().unwrap().len(), 1);
    assert_eq!(list.body["characters"][0]["id"], ada.id);
    assert_eq!(
        app.delete_character(&ada.token, bea_id, &bea_name)
            .await
            .status,
        StatusCode::NOT_FOUND
    );

    // Its rows went with it; the ledgers record the items and Crowns leaving.
    for table in [
        "items",
        "friendships",
        "equipment",
        "skills",
        "known_spells",
    ] {
        let column = match table {
            "items" => "owner_id",
            _ => "character_id",
        };
        let n: i64 =
            sqlx::query_scalar(&format!("SELECT count(*) FROM {table} WHERE {column} = $1"))
                .bind(bea_id)
                .fetch_one(&app.state.pool)
                .await
                .unwrap();
        assert_eq!(n, 0, "{table}");
    }
    let destroyed: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM item_ledger
         WHERE from_owner = $1 AND op = 'destroy' AND reason = 'character_deleted'",
    )
    .bind(bea_id)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(destroyed, held);
    let crowns: Option<i64> =
        sqlx::query_scalar("SELECT sum(delta)::bigint FROM crown_ledger WHERE character_id = $1")
            .bind(bea_id)
            .fetch_one(&app.state.pool)
            .await
            .unwrap();
    assert_eq!(crowns, Some(0), "its purse left the economy");
    let friends = app.get("/api/friends", Some(&friend.token)).await;
    assert_eq!(friends.body["items"], json!([]));

    // The name is free again, for anyone.
    let (_, stranger) = app.account().await;
    let reuse = app
        .post(
            "/api/characters",
            json!({ "name": bea_name, "house": "Skytalon" }),
            Some(&stranger),
        )
        .await;
    assert_eq!(reuse.status, StatusCode::CREATED, "{:?}", reuse.body);

    reconcile(&app.state.pool, &[ada.id, friend.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn shared_records_stay_with_the_other_side() {
    let app = TestApp::new().await;
    let ada = app.player("Burrowsby").await;
    let bea = app.player("Roarwing").await;
    // A duel too short to reward anything (so the ledgers have nothing to say).
    sqlx::query(
        "INSERT INTO duels (duel_key, venue, a_id, b_id, result, reason, a_poise, b_poise,
                            fought_ms, started_at)
         VALUES ((random() * 1e15)::bigint, 'courtyard', $1, $2, 'b', 'forfeit', 3, 3, 500, now())",
    )
    .bind(ada.id)
    .bind(bea.id)
    .execute(&app.state.pool)
    .await
    .unwrap();
    // One of Bea's items on the Exchange, and a listing of hers Ada bought.
    let item: (i64, i32) = sqlx::query_as(
        "UPDATE items SET location = 'exchange'
         WHERE id = (SELECT id FROM items WHERE owner_id = $1 AND location = 'inventory' LIMIT 1)
         RETURNING id, item_def",
    )
    .bind(bea.id)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    let listings: Vec<i64> = sqlx::query_scalar(
        "INSERT INTO exchange_listings (seller_id, item_id, item_def, quantity, price, fee,
             category, subcategory, colour, state, expires_at, closed_at, buyer_id)
         VALUES ($1, $2, $3, 1, 10, 1, 'clothing', 'hats', 0, 'active', now() + interval '1 day', NULL, NULL),
                ($1, NULL, $3, 1, 10, 1, 'clothing', 'hats', 0, 'sold', now(), now(), $4)
         RETURNING id",
    )
    .bind(bea.id)
    .bind(item.0)
    .bind(item.1)
    .bind(ada.id)
    .fetch_all(&app.state.pool)
    .await
    .unwrap();

    let res = app.delete_character(&bea.token, bea.id, &bea.name).await;
    assert_eq!(res.status, StatusCode::NO_CONTENT, "{:?}", res.body);

    let duels = app.get("/api/duels", Some(&ada.token)).await;
    assert_eq!(duels.status, StatusCode::OK, "{:?}", duels.body);
    let duel = &duels.body["items"][0];
    assert_eq!(duel["result"], "loss");
    assert_eq!(duel["opponent"]["character_id"], json!(null));
    assert_eq!(duel["opponent"]["name"], "A former student");

    let left: Vec<(i64, Option<i64>, Option<i64>)> = sqlx::query_as(
        "SELECT id, seller_id, buyer_id FROM exchange_listings WHERE id = ANY($1) ORDER BY id",
    )
    .bind(&listings)
    .fetch_all(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(left, vec![(listings[1], None, Some(ada.id))]);
}

#[tokio::test]
async fn crowns_waiting_in_the_mail_leave_through_the_ledger() {
    let app = TestApp::new().await;
    let ada = app.player("Skytalon").await;
    let purse: i64 = sqlx::query_scalar("SELECT crowns FROM characters WHERE id = $1")
        .bind(ada.id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    let mails: Vec<i64> = sqlx::query_scalar(
        "INSERT INTO mail (recipient_id, sender_name, kind, subject, crowns)
         VALUES ($1, 'Owl Exchange', 'system', 'Proceeds', 300),
                ($1, 'Owl Exchange', 'system', 'Nothing enclosed', 0)
         RETURNING id",
    )
    .bind(ada.id)
    .fetch_all(&app.state.pool)
    .await
    .unwrap();
    let res = app.delete_character(&ada.token, ada.id, &ada.name).await;
    assert_eq!(res.status, StatusCode::NO_CONTENT, "{:?}", res.body);

    let rows: Vec<(i64, String, Option<i64>)> = sqlx::query_as(
        "SELECT delta, reason, ref_id FROM crown_ledger
         WHERE character_id = $1 AND reason <> 'starting_purse' ORDER BY id",
    )
    .bind(ada.id)
    .fetch_all(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(
        rows,
        vec![
            (300, "mail_collect".to_owned(), Some(mails[0])),
            (-300, "character_deleted".to_owned(), Some(mails[0])),
            (-purse, "character_deleted".to_owned(), None),
        ]
    );
    let (sum, last): (Option<i64>, Option<i64>) = sqlx::query_as(
        "SELECT (SELECT sum(delta)::bigint FROM crown_ledger WHERE character_id = $1),
                (SELECT balance_after FROM crown_ledger WHERE character_id = $1
                 ORDER BY id DESC LIMIT 1)",
    )
    .bind(ada.id)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!((sum, last), (Some(0), Some(0)), "the chain ends at nothing");
}
