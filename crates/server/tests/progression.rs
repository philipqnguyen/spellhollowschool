//! Player levels over HTTP: `GET /api/progression` along the
//! XP curve (levels, slots, the cap), `GET /api/xp/history` pages, and the
//! required level when wearing a saved outfit (slots that need more are
//! skipped and listed).

mod common;

use axum::http::StatusCode;
use chrono::Utc;
use common::{TestApp, TestPlayer};
use protocol::XpSource;
use serde_json::json;
use server::catalog::shipped;
use server::items::{self, NewItem};
use server::ledger::Reason;
use server::progression::grant_xp;

async fn grant(app: &TestApp, p: &TestPlayer, source: XpSource, ref_id: Option<i64>, amount: u32) {
    let mut tx = app.state.pool.begin().await.unwrap();
    grant_xp(&mut tx, p.id, source, ref_id, amount, Utc::now())
        .await
        .unwrap();
    tx.commit().await.unwrap();
}

#[tokio::test]
async fn progression_follows_the_curve_to_the_cap() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    let prog = &shipped().progression;
    let res = app.get("/api/progression", p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(
        res.body,
        json!({
            "level": 1, "xp": 0, "level_xp": 0, "next_level_xp": prog.levels[1],
            "loadout_slots": 3
        })
    );
    // Straight to level 4.
    grant(&app, &p, XpSource::Admin, None, prog.levels[3] + 5).await;
    let res = app.get("/api/progression", p.token()).await;
    assert_eq!(res.body["level"], 4);
    assert_eq!(res.body["level_xp"], prog.levels[3]);
    assert_eq!(res.body["next_level_xp"], prog.levels[4]);
    // Past the top: level 20, nothing next.
    grant(&app, &p, XpSource::Admin, None, 50_000).await;
    let res = app.get("/api/progression", p.token()).await;
    assert_eq!(res.body["level"], 20);
    assert!(res.body["next_level_xp"].is_null());
    assert_eq!(res.body["loadout_slots"], 5);
    assert_eq!(
        app.get("/api/progression", None).await.status,
        StatusCode::UNAUTHORIZED
    );
}

#[tokio::test]
async fn the_xp_history_pages_newest_first() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    grant(&app, &p, XpSource::ClassAttend, Some(901), 80).await;
    grant(&app, &p, XpSource::ClassBonus, Some(901), 30).await;
    grant(&app, &p, XpSource::Find, None, 15).await;
    let res = app.get("/api/xp/history?limit=2", p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let items = res.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 2);
    assert_eq!(items[0]["source"], "find");
    assert!(items[0]["ref_id"].is_null());
    assert_eq!(items[0]["xp_after"], 125);
    assert_eq!(items[1]["source"], "class_bonus");
    assert_eq!(items[1]["ref_id"], 901);
    assert_eq!(items[1]["requested"], 30);
    assert_eq!(items[1]["level_after"], 1);
    let next = res.body["next"].as_str().unwrap();
    let res = app
        .get(&format!("/api/xp/history?limit=2&after={next}"), p.token())
        .await;
    let items = res.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["source"], "class_attend");
    assert!(res.body["next"].is_null());
    // Nobody else's.
    let q = app.player("Serpentyne").await;
    let res = app.get("/api/xp/history", q.token()).await;
    assert_eq!(res.body["items"], json!([]));
    let res = app.get("/api/xp/history?limit=0", q.token()).await;
    assert_eq!(res.body["error"], "invalid_query");
}

#[tokio::test]
async fn a_saved_outfit_skips_what_needs_a_higher_level() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let item = shipped()
        .catalog
        .items
        .iter()
        .find(|i| i.slot.is_some() && i.required_level >= 2 && !i.retired)
        .expect("some clothing needs a level");
    let slot = item.slot.unwrap().id();
    let mut tx = app.state.pool.begin().await.unwrap();
    let id = items::give(
        &mut tx,
        &NewItem::plain(p.id, item.id, 1, "inventory"),
        Reason::Admin,
        None,
    )
    .await
    .unwrap()[0];
    tx.commit().await.unwrap();
    // Holding it is fine; wearing it needs the level.
    let wear = json!({ "slot": slot, "item_id": id });
    let res = app.post("/api/equipment", wear.clone(), p.token()).await;
    assert_eq!(res.body["error"], "level_too_low");
    assert!(
        res.body["message"]
            .as_str()
            .unwrap()
            .contains(&item.required_level.to_string())
    );
    let need = shipped().progression.level_start(item.required_level);
    grant(&app, &p, XpSource::Admin, None, need).await;
    assert_eq!(
        app.post("/api/equipment", wear, p.token()).await.status,
        StatusCode::OK
    );
    let saved = app
        .post("/api/outfits", json!({ "name": "Best" }), p.token())
        .await;
    assert_eq!(saved.status, StatusCode::CREATED, "{:?}", saved.body);
    let outfit = saved.body["id"].as_i64().unwrap();
    // Take it off, lose the level (an operator's fix, say), wear the outfit.
    let off = json!({ "slot": slot, "item_id": null });
    assert_eq!(
        app.post("/api/equipment", off, p.token()).await.status,
        StatusCode::OK
    );
    sqlx::query("UPDATE characters SET level = 1 WHERE id = $1")
        .bind(p.id)
        .execute(&app.state.pool)
        .await
        .unwrap();
    let res = app
        .post(&format!("/api/outfits/{outfit}/wear"), json!({}), p.token())
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["missing"], json!([slot]));
}
