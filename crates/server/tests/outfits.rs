//! Saved outfits: save what is worn, list, rename, delete, the limit of 10,
//! and wearing (including slots whose item is gone or placed).

mod common;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer};
use protocol::{GearSlot, House, SAVED_OUTFITS_MAX};
use serde_json::{Value, json};
use server::catalog::shipped;

async fn save(app: &TestApp, p: &TestPlayer, name: &str) -> common::Response {
    app.post("/api/outfits", json!({ "name": name }), p.token())
        .await
}

/// The id of one of the player's items with catalog id `def`.
async fn item_of(app: &TestApp, p: &TestPlayer, def: u16) -> i64 {
    let res = app.get("/api/inventory?limit=100", p.token()).await;
    res.body["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|i| i["item_def"] == def)
        .unwrap()["id"]
        .as_i64()
        .unwrap()
}

fn non_default(house: House, slot: GearSlot) -> u16 {
    shipped()
        .catalog
        .starter_items(house)
        .find(|i| i.slot == Some(slot) && !i.has_tag("default"))
        .unwrap()
        .id
}

async fn equip(app: &TestApp, p: &TestPlayer, slot: GearSlot, item: Option<i64>) {
    let res = app
        .post(
            "/api/equipment",
            json!({ "slot": slot, "item_id": item }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
}

async fn equipment(app: &TestApp, p: &TestPlayer) -> Value {
    app.get("/api/equipment", p.token()).await.body
}

#[tokio::test]
async fn outfits_need_a_session() {
    let app = TestApp::new().await;
    assert_eq!(
        app.get("/api/outfits", None).await.status,
        StatusCode::UNAUTHORIZED
    );
    let res = app.post("/api/outfits", json!({ "name": "x" }), None).await;
    assert_eq!(res.status, StatusCode::UNAUTHORIZED);
    assert_eq!(
        app.delete("/api/outfits/1", None).await.status,
        StatusCode::UNAUTHORIZED
    );
    let res = app.post("/api/outfits/1/wear", json!({}), None).await;
    assert_eq!(res.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn save_wear_rename_and_delete() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let school = equipment(&app, &p).await;

    let res = save(&app, &p, "  School day ").await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let school_id = res.body["id"].as_i64().unwrap();
    assert_eq!(res.body["name"], "School day");
    for slot in ["body", "legs", "shoes"] {
        assert_eq!(res.body[slot], school[slot]["id"], "{slot}");
    }
    assert_eq!(res.body["hat"], Value::Null);

    // Change into something else and save that too.
    let hat = item_of(&app, &p, non_default(House::Roarwing, GearSlot::Hat)).await;
    let jumper = item_of(&app, &p, non_default(House::Roarwing, GearSlot::Body)).await;
    equip(&app, &p, GearSlot::Hat, Some(hat)).await;
    equip(&app, &p, GearSlot::Body, Some(jumper)).await;
    equip(&app, &p, GearSlot::Shoes, None).await;
    let res = save(&app, &p, "Weekend").await;
    assert_eq!(res.status, StatusCode::CREATED);
    let weekend_id = res.body["id"].as_i64().unwrap();
    assert_eq!(
        (res.body["hat"].clone(), res.body["body"].clone()),
        (json!(hat), json!(jumper))
    );
    assert_eq!(res.body["shoes"], Value::Null);

    // Wearing the first restores it, emptying the hat slot, in one version.
    let version = equipment(&app, &p).await["appearance_version"]
        .as_u64()
        .unwrap();
    let res = app
        .post(
            &format!("/api/outfits/{school_id}/wear"),
            json!({}),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["missing"], json!([]));
    assert_eq!(res.body["appearance_version"], version + 1);
    assert_eq!(res.body["appearance"]["outfit"]["hat"], Value::Null);
    let now = equipment(&app, &p).await;
    for slot in ["hat", "body", "legs", "shoes"] {
        assert_eq!(now[slot]["id"], school[slot]["id"], "{slot}");
    }
    // Wearing it again changes nothing.
    let res = app
        .post(
            &format!("/api/outfits/{school_id}/wear"),
            json!({}),
            p.token(),
        )
        .await;
    assert_eq!(res.body["appearance_version"], version + 1);
    // And the other one again.
    let res = app
        .post(
            &format!("/api/outfits/{weekend_id}/wear"),
            json!({}),
            p.token(),
        )
        .await;
    assert_eq!(res.body["appearance"]["outfit"]["shoes"], Value::Null);
    assert_eq!(equipment(&app, &p).await["hat"]["id"], hat);

    // Rename and list (newest first).
    let res = app
        .patch(
            &format!("/api/outfits/{weekend_id}"),
            json!({ "name": "Bramblewick" }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK);
    assert_eq!(res.body["name"], "Bramblewick");
    let list = app.get("/api/outfits", p.token()).await;
    let names: Vec<&str> = list.body["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|o| o["name"].as_str().unwrap())
        .collect();
    assert_eq!(names, ["Bramblewick", "School day"]);
    assert_eq!(list.body["next"], Value::Null);

    // Delete.
    let res = app
        .delete(&format!("/api/outfits/{school_id}"), p.token())
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    let res = app
        .delete(&format!("/api/outfits/{school_id}"), p.token())
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::NOT_FOUND, json!("outfit_not_found"))
    );
    let res = app
        .post(
            &format!("/api/outfits/{school_id}/wear"),
            json!({}),
            p.token(),
        )
        .await;
    assert_eq!(res.body["error"], "outfit_not_found");
    let list = app.get("/api/outfits", p.token()).await;
    assert_eq!(list.body["items"].as_array().unwrap().len(), 1);
}

#[tokio::test]
async fn names_and_the_limit() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    for bad in ["", "   ", &"x".repeat(33)] {
        let res = save(&app, &p, bad).await;
        assert_eq!(
            (res.status, res.body["error"].clone()),
            (StatusCode::BAD_REQUEST, json!("invalid_name")),
            "{bad:?}"
        );
    }
    let mut ids = Vec::new();
    for i in 0..SAVED_OUTFITS_MAX {
        let res = save(&app, &p, &format!("Look {i}")).await;
        assert_eq!(res.status, StatusCode::CREATED, "{i}");
        ids.push(res.body["id"].as_i64().unwrap());
    }
    let res = save(&app, &p, "One too many").await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::CONFLICT, json!("outfit_limit"))
    );
    let res = app
        .patch(
            &format!("/api/outfits/{}", ids[0]),
            json!({ "name": "" }),
            p.token(),
        )
        .await;
    assert_eq!(res.body["error"], "invalid_name");

    // Paging through all ten, three at a time, newest first.
    let mut seen = Vec::new();
    let mut after = String::new();
    loop {
        let res = app
            .get(&format!("/api/outfits?limit=3&after={after}"), p.token())
            .await;
        assert_eq!(res.status, StatusCode::OK);
        seen.extend(
            res.body["items"]
                .as_array()
                .unwrap()
                .iter()
                .map(|o| o["id"].as_i64().unwrap()),
        );
        match res.body["next"].as_str() {
            Some(c) => after = c.to_owned(),
            None => break,
        }
    }
    ids.reverse();
    assert_eq!(seen, ids);

    // Deleting one makes room again.
    assert_eq!(
        app.delete(&format!("/api/outfits/{}", ids[3]), p.token())
            .await
            .status,
        StatusCode::NO_CONTENT
    );
    assert_eq!(
        save(&app, &p, "Room again").await.status,
        StatusCode::CREATED
    );
}

#[tokio::test]
async fn concurrent_saves_respect_the_limit() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    for i in 0..SAVED_OUTFITS_MAX - 1 {
        save(&app, &p, &format!("Look {i}")).await;
    }
    let (a, b, c) = tokio::join!(
        save(&app, &p, "A"),
        save(&app, &p, "B"),
        save(&app, &p, "C")
    );
    let created = [&a, &b, &c]
        .iter()
        .filter(|r| r.status == StatusCode::CREATED)
        .count();
    assert_eq!(created, 1);
    let count: i64 =
        sqlx::query_scalar("SELECT count(*) FROM saved_outfits WHERE character_id = $1")
            .bind(p.id)
            .fetch_one(&app.state.pool)
            .await
            .unwrap();
    assert_eq!(count, i64::from(SAVED_OUTFITS_MAX));
}

#[tokio::test]
async fn wearing_skips_items_no_longer_available() {
    let app = TestApp::new().await;
    let p = app.player("Serpentyne").await;
    let other = app.player("Serpentyne").await;
    let hat = item_of(&app, &p, non_default(House::Serpentyne, GearSlot::Hat)).await;
    equip(&app, &p, GearSlot::Hat, Some(hat)).await;
    let saved = equipment(&app, &p).await;
    let res = save(&app, &p, "Full kit").await;
    let outfit = res.body["id"].as_i64().unwrap();

    // Change everything, then lose the hat (traded) and have the shoes in an unwearable place.
    let boots = item_of(&app, &p, non_default(House::Serpentyne, GearSlot::Shoes)).await;
    equip(&app, &p, GearSlot::Shoes, Some(boots)).await;
    equip(&app, &p, GearSlot::Hat, None).await;
    equip(&app, &p, GearSlot::Legs, None).await;
    sqlx::query("UPDATE items SET owner_id = $2 WHERE id = $1")
        .bind(hat)
        .bind(other.id)
        .execute(&app.state.pool)
        .await
        .unwrap();
    let shoes = saved["shoes"]["id"].as_i64().unwrap();
    sqlx::query("UPDATE items SET location = 'dorm' WHERE id = $1")
        .bind(shoes)
        .execute(&app.state.pool)
        .await
        .unwrap();

    let res = app
        .post(&format!("/api/outfits/{outfit}/wear"), json!({}), p.token())
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["missing"], json!(["hat", "shoes"]));
    let now = equipment(&app, &p).await;
    assert_eq!(now["hat"], Value::Null, "skipped: left as it was (empty)");
    assert_eq!(now["shoes"]["id"], boots, "skipped: left as it was");
    assert_eq!(now["legs"]["id"], saved["legs"]["id"], "restored");
    assert_eq!(now["body"]["id"], saved["body"]["id"]);
    assert_eq!(
        res.body["appearance"]["outfit"]["legs"]["item_def"],
        saved["legs"]["item_def"]
    );
    // The hat's new owner was not touched.
    assert_eq!(equipment(&app, &other).await["hat"], Value::Null);
}

#[tokio::test]
async fn outfits_are_private() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let thief = app.player("Roarwing").await;
    let outfit = save(&app, &p, "Mine").await.body["id"].as_i64().unwrap();
    for res in [
        app.post(
            &format!("/api/outfits/{outfit}/wear"),
            json!({}),
            thief.token(),
        )
        .await,
        app.delete(&format!("/api/outfits/{outfit}"), thief.token())
            .await,
        app.patch(
            &format!("/api/outfits/{outfit}"),
            json!({ "name": "Ours" }),
            thief.token(),
        )
        .await,
    ] {
        assert_eq!(
            (res.status, res.body["error"].clone()),
            (StatusCode::NOT_FOUND, json!("outfit_not_found"))
        );
    }
    let list = app.get("/api/outfits", thief.token()).await;
    assert!(list.body["items"].as_array().unwrap().is_empty());
    let mine = app.get("/api/outfits", p.token()).await;
    assert_eq!(mine.body["items"][0]["name"], "Mine");
    // Wearing someone else's items through a forged outfit row is impossible
    // too: the thief's outfit pointing at p's robe skips that slot.
    let robe = equipment(&app, &p).await["body"]["id"].as_i64().unwrap();
    let forged: i64 = sqlx::query_scalar(
        "INSERT INTO saved_outfits (character_id, name, body_item_id) VALUES ($1, 'Forged', $2) RETURNING id",
    )
    .bind(thief.id)
    .bind(robe)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    let res = app
        .post(
            &format!("/api/outfits/{forged}/wear"),
            json!({}),
            thief.token(),
        )
        .await;
    assert_eq!(res.body["missing"], json!(["body"]));
    assert_eq!(equipment(&app, &p).await["body"]["id"], robe);
}
