//! The dev-only bot-session endpoint.

mod common;

use axum::http::StatusCode;
use common::{TestApp, unique_name};
use rand::Rng;
use serde_json::json;

fn bot_name() -> String {
    format!("bot{:08}", rand::thread_rng().gen_range(0..100_000_000))
}

#[tokio::test]
async fn bot_session_is_not_mounted_without_dev() {
    let app = TestApp::new().await;
    let res = app
        .post("/api/dev/bot-session", json!({ "name": bot_name() }), None)
        .await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    assert_eq!(res.body["error"], "not_found");
}

#[tokio::test]
async fn bot_session_creates_a_verified_account_with_a_character() {
    let app = TestApp::dev().await;
    let name = bot_name();
    let res = app
        .post("/api/dev/bot-session", json!({ "name": name }), None)
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let token = res.body["token"].as_str().unwrap().to_owned();

    let me = app.get("/api/characters/me", Some(&token)).await;
    assert_eq!(me.status, StatusCode::OK);
    assert_eq!(me.body["name"], name);

    let info = server::validate_session(&app.state.pool, &token)
        .await
        .unwrap();
    let (is_bot, verified): (bool, bool) =
        sqlx::query_as("SELECT is_bot, email_verified_at IS NOT NULL FROM accounts WHERE id = $1")
            .bind(info.account_id)
            .fetch_one(&app.state.pool)
            .await
            .unwrap();
    assert!(is_bot && verified);

    // Asking again for the same bot reuses its account with a fresh session.
    let again = app
        .post("/api/dev/bot-session", json!({ "name": name }), None)
        .await;
    assert_eq!(again.status, StatusCode::OK);
    let token2 = again.body["token"].as_str().unwrap();
    assert_ne!(token2, token);
    let info2 = server::validate_session(&app.state.pool, token2)
        .await
        .unwrap();
    assert_eq!(info2.account_id, info.account_id);
}

#[tokio::test]
async fn bot_sessions_can_choose_a_body_before_the_grant() {
    let app = TestApp::dev().await;
    let name = bot_name();
    let body = json!({
        "body_type": 1, "height": 30, "face": [10, 250, 128, 60, 200],
        "skin_tone": 3, "hair_style": 18, "hair_colour": 14, "eye_colour": 11,
        // Ignored: bots dress through the wardrobe.
        "outfit": { "hat": { "item_def": 101, "dyes": [1, 2, 3] }, "body": null, "legs": null, "shoes": null }
    });
    let res = app
        .post(
            "/api/dev/bot-session",
            json!({ "name": name, "body": body }),
            None,
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let token = res.body["token"].as_str().unwrap().to_owned();
    // The grant dresses that body in the starter outfit.
    let me = app.get("/api/characters/me", Some(&token)).await;
    let a = &me.body["appearance"];
    assert_eq!(
        (a["body_type"].clone(), a["height"].clone()),
        (json!(1), json!(30))
    );
    assert_eq!(a["face"], json!([10, 250, 128, 60, 200]));
    assert_eq!(a["hair_style"], 18);
    assert_eq!(a["eye_colour"], 11);
    assert!(a.get("build").is_none());
    assert!(a["outfit"]["body"].is_object(), "{a}");

    // After the grant, a body in the request changes nothing.
    let again = app
        .post(
            "/api/dev/bot-session",
            json!({ "name": name, "body": { "body_type": 0, "height": 128,
                    "face": [128, 128, 128, 128, 128], "skin_tone": 0, "hair_style": 14,
                    "hair_colour": 0, "outfit": { "hat": null, "body": null, "legs": null, "shoes": null } } }),
            None,
        )
        .await;
    assert_eq!(again.status, StatusCode::OK);
    let token2 = again.body["token"].as_str().unwrap();
    let me2 = app.get("/api/characters/me", Some(token2)).await;
    assert_eq!(me2.body["appearance"], me.body["appearance"]);

    // An impossible body is refused.
    let bad = app
        .post(
            "/api/dev/bot-session",
            json!({ "name": bot_name(), "body": { "body_type": 9, "height": 0,
                    "face": [0, 0, 0, 0, 0], "skin_tone": 0, "hair_style": 14, "hair_colour": 0,
                    "outfit": { "hat": null, "body": null, "legs": null, "shoes": null } } }),
            None,
        )
        .await;
    assert_eq!(bad.status, StatusCode::BAD_REQUEST);
    assert_eq!(bad.body["error"], "invalid_appearance");

    // So is a hair style of the other body.
    let other = app
        .post(
            "/api/dev/bot-session",
            json!({ "name": bot_name(), "body": { "body_type": 0, "height": 0,
                    "face": [0, 0, 0, 0, 0], "skin_tone": 0, "hair_style": 17, "hair_colour": 0,
                    "outfit": { "hat": null, "body": null, "legs": null, "shoes": null } } }),
            None,
        )
        .await;
    assert_eq!(other.status, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn bot_session_never_takes_over_a_player() {
    let app = TestApp::dev().await;
    let (_, token) = app.signed_in().await;
    let name = unique_name();
    app.post(
        "/api/characters",
        json!({ "name": name, "house": "Roarwing" }),
        Some(&token),
    )
    .await;

    let res = app
        .post(
            "/api/dev/bot-session",
            json!({ "name": name.to_lowercase() }),
            None,
        )
        .await;
    assert_eq!(res.status, StatusCode::CONFLICT);
    assert_eq!(res.body["error"], "name_taken");

    let bad = app
        .post("/api/dev/bot-session", json!({ "name": "7 bad" }), None)
        .await;
    assert_eq!(bad.status, StatusCode::BAD_REQUEST);
    assert_eq!(bad.body["error"], "invalid_name");
}

#[tokio::test]
async fn dev_grant_hands_out_recorded_goods_in_dev_only() {
    let plain = TestApp::new().await;
    let p = plain.player("Skytalon").await;
    let res = plain
        .post("/api/dev/grant", json!({ "crowns": 5 }), Some(&p.token))
        .await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);

    let app = TestApp::dev().await;
    let p = app.player("Burrowsby").await;
    let res = app
        .post(
            "/api/dev/grant",
            json!({
                "items": [{ "item_def": 3307, "quantity": 3 }, { "item_def": 3011, "quantity": 12 }],
                "crowns": 250,
                "skills": { "potions": 1500 }
            }),
            Some(&p.token),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["items"].as_array().unwrap().len(), 2);
    let purse = i64::from(server::catalog::shipped().economy.starting_purse);
    assert_eq!(res.body["crowns"], purse + 250);
    let potions = app.get("/api/inventory?kind=potion", Some(&p.token)).await;
    assert_eq!(potions.body["items"][0]["quantity"], 3);
    let skills = app.get("/api/skills", Some(&p.token)).await;
    let brew = skills.body["skills"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["discipline"] == "potions")
        .unwrap()
        .clone();
    assert_eq!(brew["xp"], 1500);

    for bad in [
        json!({ "items": [{ "item_def": 1, "quantity": 1 }] }),
        json!({ "items": [{ "item_def": 3307, "quantity": 0 }] }),
        json!({ "crowns": -1 }),
        // Any of the subjects is fine (Flying too);
        // nothing else is.
        json!({ "skills": { "flying_pitch": 10 } }),
    ] {
        let res = app.post("/api/dev/grant", bad, Some(&p.token)).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{:?}", res.body);
        assert_eq!(res.body["error"], "invalid_grant");
    }
    server::ledger::audit::reconcile(&app.state.pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}
