//! Spells over HTTP: `GET /api/spells` and `PUT
//! /api/loadout` (learned dueling spells only, no repeats, at most the
//! level's slots, the message naming the spell; stored, and read back as
//! the effective loadout).

mod common;

use axum::http::{Method, StatusCode};
use common::{TestApp, TestPlayer};
use serde_json::json;
use server::catalog::shipped;
use server::progression;

const LUCERNA: u16 = 1;
const SCUTARA: u16 = 10;
const TORPEO: u16 = 11;
const DIMITTO: u16 = 12;
const TITILLARA: u16 = 13;
const REPULSA: u16 = 14;

async fn learn(app: &TestApp, p: &TestPlayer, spells: &[u16]) {
    let mut tx = app.state.pool.begin().await.unwrap();
    progression::learn_spells(&mut tx, p.id, spells, "admin")
        .await
        .unwrap();
    tx.commit().await.unwrap();
}

async fn set_level(app: &TestApp, p: &TestPlayer, level: u8) {
    let xp = shipped().progression.level_start(level);
    sqlx::query("UPDATE characters SET xp = $2, level = $3 WHERE id = $1")
        .bind(p.id)
        .bind(xp as i32)
        .bind(i16::from(level))
        .execute(&app.state.pool)
        .await
        .unwrap();
}

async fn put(app: &TestApp, p: &TestPlayer, spells: &[u16]) -> common::Response {
    app.request(
        Method::PUT,
        "/api/loadout",
        Some(json!({ "spells": spells })),
        p.token(),
    )
    .await
}

#[tokio::test]
async fn spells_list_what_was_learned_and_the_default_loadout() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let res = app.get("/api/spells", p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(
        res.body,
        json!({ "learned": [], "loadout": [], "slots": 3 })
    );
    learn(&app, &p, &[LUCERNA, SCUTARA, TORPEO, DIMITTO, TITILLARA]).await;
    let res = app.get("/api/spells", p.token()).await;
    let learned = res.body["learned"].as_array().unwrap();
    assert_eq!(learned.len(), 5);
    assert_eq!(learned[0]["id"], LUCERNA);
    assert_eq!(learned[0]["taught_at"], "admin");
    assert!(learned[0]["learned_at"].is_string());
    // The default: learned dueling spells in file order, as many as fit.
    assert_eq!(res.body["loadout"], json!([SCUTARA, TORPEO, DIMITTO]));
    assert_eq!(
        app.get("/api/spells", None).await.status,
        StatusCode::UNAUTHORIZED
    );
}

#[tokio::test]
async fn loadouts_are_checked_and_stored() {
    let app = TestApp::new().await;
    let p = app.player("Serpentyne").await;
    learn(&app, &p, &[LUCERNA, SCUTARA, TORPEO, DIMITTO, TITILLARA]).await;

    let refused = [
        (vec![SCUTARA, TORPEO, DIMITTO, TITILLARA], "3 slots"),
        (vec![TORPEO, TORPEO], "twice"),
        (vec![LUCERNA], "Lantern Charm"),
        (vec![REPULSA], "Knockback Jinx"),
        (vec![999], "999"),
    ];
    for (spells, says) in refused {
        let res = put(&app, &p, &spells).await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{spells:?}");
        assert_eq!(res.body["error"], "invalid_loadout");
        let msg = res.body["message"].as_str().unwrap();
        assert!(msg.contains(says), "{msg} should name {says}");
    }
    let res = put(&app, &p, &[TITILLARA, SCUTARA]).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(
        res.body,
        json!({ "loadout": [TITILLARA, SCUTARA], "slots": 3 })
    );
    let res = app.get("/api/spells", p.token()).await;
    assert_eq!(
        res.body["loadout"],
        json!([TITILLARA, SCUTARA]),
        "slot order kept"
    );
    let school = progression::load_school(&app.state.pool, p.id)
        .await
        .unwrap();
    assert_eq!(school.loadout, vec![TITILLARA, SCUTARA]);
    // An empty loadout is a choice too.
    assert_eq!(put(&app, &p, &[]).await.status, StatusCode::OK);
    assert_eq!(
        app.get("/api/spells", p.token()).await.body["loadout"],
        json!([])
    );

    // Level 5 opens a fourth slot: options, not power.
    set_level(&app, &p, 5).await;
    let four = [SCUTARA, TORPEO, DIMITTO, TITILLARA];
    let res = put(&app, &p, &four).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["slots"], 4);
    // Too many spells in the body at all.
    let res = put(&app, &p, &[SCUTARA; 9]).await;
    assert_eq!(res.body["error"], "invalid_loadout");
}

#[tokio::test]
async fn loadout_writes_are_rate_limited() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    let mut limited = false;
    for _ in 0..12 {
        if put(&app, &p, &[]).await.status == StatusCode::TOO_MANY_REQUESTS {
            limited = true;
        }
    }
    assert!(limited, "10, then one every 2 s");
}
