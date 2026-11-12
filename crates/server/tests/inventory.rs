//! The inventory, equipment and dye endpoints: pagination, filters, slot and
//! ownership rules, items placed in the dorm, appearance versions, and the
//! wardrobe write budget.

mod common;

use std::collections::HashSet;

use axum::http::{StatusCode, header};
use common::{TestApp, TestPlayer};
use protocol::{GearSlot, House};
use serde_json::{Value, json};
use server::catalog::shipped;
use sim_core::ItemKind;

async fn page(app: &TestApp, p: &TestPlayer, query: &str) -> Value {
    let res = app.get(&format!("/api/inventory?{query}"), p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{query}: {:?}", res.body);
    res.body
}

/// Ids of a player's items matching `query`, from one big page.
async fn ids(app: &TestApp, p: &TestPlayer, query: &str) -> Vec<i64> {
    let body = page(app, p, &format!("limit=100&{query}")).await;
    assert_eq!(body["next"], Value::Null);
    body["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|i| i["id"].as_i64().unwrap())
        .collect()
}

/// The id of one of the player's items with catalog id `def`.
async fn item_of(app: &TestApp, p: &TestPlayer, def: u16) -> i64 {
    let body = page(app, p, "limit=100").await;
    body["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|i| i["item_def"] == def)
        .unwrap_or_else(|| panic!("no item {def}"))["id"]
        .as_i64()
        .unwrap()
}

fn starter_def(house: House, f: impl Fn(&sim_core::ItemDef) -> bool) -> u16 {
    shipped()
        .catalog
        .starter_items(house)
        .find(|i| f(i))
        .unwrap()
        .id
}

/// The starter furniture a new character of `house` finds placed in their
/// room (the bed).
fn placed_starter(house: House) -> u16 {
    server::dorm::shells::shipped().starter_furniture(house)[0].item
}

/// A starter furniture item of `house` granted into the inventory.
fn unplaced_furniture(house: House) -> u16 {
    starter_def(house, |i| {
        i.kind == ItemKind::Furniture && i.id != placed_starter(house)
    })
}

fn hats(house: House) -> Vec<u16> {
    shipped()
        .catalog
        .starter_items(house)
        .filter(|i| i.slot == Some(GearSlot::Hat))
        .map(|i| i.id)
        .collect()
}

async fn equip(app: &TestApp, p: &TestPlayer, slot: &str, item: Value) -> common::Response {
    app.post(
        "/api/equipment",
        json!({ "slot": slot, "item_id": item }),
        p.token(),
    )
    .await
}

#[tokio::test]
async fn endpoints_need_a_session_and_a_character() {
    let app = TestApp::new().await;
    for path in ["/api/inventory", "/api/equipment"] {
        let res = app.get(path, None).await;
        assert_eq!(res.status, StatusCode::UNAUTHORIZED, "{path}");
        assert_eq!(res.body["error"], "unauthorized");
    }
    let res = app
        .post(
            "/api/items/1/dye",
            json!({ "dyes": [0, 0, 0] }),
            Some("garbage"),
        )
        .await;
    assert_eq!(res.status, StatusCode::UNAUTHORIZED);
    let (_, token) = app.account().await;
    let res = app.get("/api/inventory", Some(&token)).await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    assert_eq!(res.body["error"], "no_character");
    let res = app
        .post(
            "/api/equipment",
            json!({ "slot": "hat", "item_id": null }),
            Some(&token),
        )
        .await;
    assert_eq!(res.body["error"], "no_character");
}

#[tokio::test]
async fn inventory_pages_newest_first_without_gaps() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    let everything = ids(&app, &p, "").await;
    let total = shipped().catalog.starter_items(House::Burrowsby).count();
    assert_eq!(everything.len(), total);
    let mut sorted = everything.clone();
    sorted.sort_unstable_by(|a, b| b.cmp(a));
    assert_eq!(everything, sorted, "newest (highest id) first");

    // Walk it five at a time.
    let mut seen = Vec::new();
    let mut after: Option<String> = None;
    let mut pages = 0;
    loop {
        let q = match &after {
            Some(c) => format!("limit=5&after={c}"),
            None => "limit=5".to_owned(),
        };
        let body = page(&app, &p, &q).await;
        let items = body["items"].as_array().unwrap();
        assert!(items.len() <= 5);
        seen.extend(items.iter().map(|i| i["id"].as_i64().unwrap()));
        pages += 1;
        match body["next"].as_str() {
            Some(c) => after = Some(c.to_owned()),
            None => break,
        }
    }
    assert_eq!(seen, everything);
    assert_eq!(pages, total.div_ceil(5));

    // Exactly one full page: no next cursor, and no empty page after it.
    let body = page(&app, &p, &format!("limit={total}")).await;
    assert_eq!(body["items"].as_array().unwrap().len(), total);
    assert_eq!(body["next"], Value::Null);
    let body = page(&app, &p, &format!("limit={}", total - 1)).await;
    let next = body["next"].as_str().unwrap();
    let last = page(&app, &p, &format!("limit={}&after={next}", total - 1)).await;
    assert_eq!(last["items"].as_array().unwrap().len(), 1);
    assert_eq!(last["next"], Value::Null);
    // Default and capped limits.
    let body = page(&app, &p, "").await;
    assert_eq!(body["items"].as_array().unwrap().len(), total.min(25));
    let body = page(&app, &p, "limit=100000").await;
    assert_eq!(body["items"].as_array().unwrap().len(), total);
    // Empty `after` is the first page.
    let body = page(&app, &p, "limit=3&after=").await;
    assert_eq!(body["items"][0]["id"], everything[0]);
}

#[tokio::test]
async fn bad_pages_and_filters_are_refused() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    for (query, code) in [
        ("limit=0", "invalid_query"),
        ("limit=-3", "invalid_query"),
        ("limit=many", "invalid_query"),
        ("after=bm90IGEgY3Vyc29y", "invalid_cursor"),
        ("after=!!", "invalid_cursor"),
        ("kind=wand", "invalid_query"),
        ("slot=cape", "invalid_query"),
        ("location=attic", "invalid_query"),
    ] {
        let res = app.get(&format!("/api/inventory?{query}"), p.token()).await;
        assert_eq!(
            res.status,
            StatusCode::BAD_REQUEST,
            "{query}: {:?}",
            res.body
        );
        assert_eq!(res.body["error"], code, "{query}");
    }
    // Another endpoint's cursor (a different key type) is not valid here.
    let friends_cursor = server::characters::page::encode_cursor(&("ada".to_owned(), 1_i64));
    let res = app
        .get(&format!("/api/inventory?after={friends_cursor}"), p.token())
        .await;
    assert_eq!(res.body["error"], "invalid_cursor");
}

#[tokio::test]
async fn inventory_filters() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    let catalog = &shipped().catalog;
    let starter: Vec<_> = catalog.starter_items(House::Skytalon).collect();
    let count = |f: &dyn Fn(&sim_core::ItemDef) -> bool| starter.iter().filter(|i| f(i)).count();

    let furniture = ids(&app, &p, "kind=furniture").await;
    assert_eq!(furniture.len(), count(&|i| i.kind == ItemKind::Furniture));
    let clothing = ids(&app, &p, "kind=clothing").await;
    assert_eq!(clothing.len(), count(&|i| i.kind == ItemKind::Clothing));
    let shoes = ids(&app, &p, "kind=clothing&slot=shoes").await;
    assert_eq!(shoes.len(), count(&|i| i.slot == Some(GearSlot::Shoes)));
    assert_eq!(ids(&app, &p, "slot=hat").await.len(), 2);
    assert!(ids(&app, &p, "kind=furniture&slot=hat").await.is_empty());
    assert_eq!(ids(&app, &p, "location=equipped").await.len(), 3);
    assert_eq!(ids(&app, &p, "location=equipped&slot=legs").await.len(), 1);
    // The bed comes placed in the dorm room.
    let placed = ids(&app, &p, "location=dorm").await;
    assert_eq!(placed.len(), 1);
    assert_eq!(placed, ids(&app, &p, "location=dorm&kind=furniture").await);

    // Filters page too.
    let body = page(&app, &p, "kind=furniture&limit=4").await;
    let next = body["next"].as_str().unwrap();
    let body2 = page(&app, &p, &format!("kind=furniture&limit=4&after={next}")).await;
    let first: HashSet<_> = body["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|i| i["id"].clone())
        .collect();
    assert!(
        body2["items"]
            .as_array()
            .unwrap()
            .iter()
            .all(|i| !first.contains(&i["id"]))
    );
    assert!(
        body2["items"]
            .as_array()
            .unwrap()
            .iter()
            .all(|i| i["location"] == "inventory")
    );
}

#[tokio::test]
async fn equipping_swaps_the_slot_and_bumps_the_version() {
    let app = TestApp::new().await;
    let p = app.player("Serpentyne").await;
    let [first_hat, second_hat] = hats(House::Serpentyne)[..] else {
        panic!("two starter hats")
    };
    let hat_a = item_of(&app, &p, first_hat).await;
    let hat_b = item_of(&app, &p, second_hat).await;

    let res = equip(&app, &p, "hat", json!(hat_a)).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["appearance_version"], 2);
    assert_eq!(
        res.body["appearance"]["outfit"]["hat"]["item_def"],
        first_hat
    );
    let eq = app.get("/api/equipment", p.token()).await;
    assert_eq!(eq.body["hat"]["id"], hat_a);
    assert_eq!(eq.body["hat"]["slot"], "hat");
    assert_eq!(eq.body["hat"]["location"], "equipped");
    assert_eq!(eq.body["appearance_version"], 2);

    // Swapping returns the previous hat to the inventory.
    let res = equip(&app, &p, "hat", json!(hat_b)).await;
    assert_eq!(res.body["appearance_version"], 3);
    assert_eq!(
        res.body["appearance"]["outfit"]["hat"]["item_def"],
        second_hat
    );
    let in_inventory = ids(&app, &p, "location=inventory&slot=hat").await;
    assert_eq!(in_inventory, vec![hat_a]);

    // Wearing what is already worn changes nothing.
    let res = equip(&app, &p, "hat", json!(hat_b)).await;
    assert_eq!(res.status, StatusCode::OK);
    assert_eq!(res.body["appearance_version"], 3);

    // Emptying the slot, with null and with the field left out.
    let res = equip(&app, &p, "hat", Value::Null).await;
    assert_eq!(res.body["appearance_version"], 4);
    assert_eq!(res.body["appearance"]["outfit"]["hat"], Value::Null);
    let res = app
        .post("/api/equipment", json!({ "slot": "hat" }), p.token())
        .await;
    assert_eq!(res.status, StatusCode::OK);
    assert_eq!(res.body["appearance_version"], 4, "already empty");
    assert_eq!(ids(&app, &p, "location=inventory&slot=hat").await.len(), 2);

    // The character and the world's view of it agree.
    let me = app.get("/api/characters/me", p.token()).await;
    assert_eq!(me.body["appearance_version"], 4);
    assert_eq!(me.body["appearance"], res.body["appearance"]);
    let info = server::validate_session(&app.state.pool, &p.token)
        .await
        .unwrap();
    assert_eq!(info.character.unwrap().appearance_version, 4);
}

#[tokio::test]
async fn equip_checks_slot_ownership_and_location() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let other = app.player("Roarwing").await;
    let hat = item_of(&app, &p, hats(House::Roarwing)[0]).await;
    let piece = item_of(&app, &p, unplaced_furniture(House::Roarwing)).await;
    let theirs = item_of(&app, &other, hats(House::Roarwing)[0]).await;

    let res = equip(&app, &p, "shoes", json!(hat)).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::BAD_REQUEST, json!("wrong_slot"))
    );
    let res = equip(&app, &p, "body", json!(piece)).await;
    assert_eq!(res.body["error"], "wrong_slot", "furniture is not worn");
    let res = equip(&app, &p, "hat", json!(theirs)).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::NOT_FOUND, json!("item_not_found"))
    );
    let res = equip(&app, &p, "hat", json!(i64::MAX)).await;
    assert_eq!(res.body["error"], "item_not_found");
    let res = equip(&app, &p, "cape", json!(hat)).await;
    assert_eq!(res.body["error"], "invalid_body");

    // An item placed in the dorm cannot be worn.
    sqlx::query("UPDATE items SET location = 'dorm' WHERE id = $1")
        .bind(hat)
        .execute(&app.state.pool)
        .await
        .unwrap();
    let res = equip(&app, &p, "hat", json!(hat)).await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::CONFLICT, json!("item_unavailable"))
    );

    // Nothing changed for either player.
    let eq = app.get("/api/equipment", p.token()).await;
    assert_eq!(
        (
            eq.body["hat"].clone(),
            eq.body["appearance_version"].clone()
        ),
        (Value::Null, json!(1))
    );
    let theirs_eq = app.get("/api/equipment", other.token()).await;
    assert_eq!(theirs_eq.body["hat"], Value::Null);
    assert_eq!(
        ids(&app, &other, "location=inventory&slot=hat").await.len(),
        2
    );
}

#[tokio::test]
async fn dyeing() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    let eq = app.get("/api/equipment", p.token()).await;
    let robe = eq.body["body"]["id"].as_i64().unwrap();
    let boots = item_of(
        &app,
        &p,
        starter_def(House::Burrowsby, |i| {
            i.slot == Some(GearSlot::Shoes) && !i.has_tag("default")
        }),
    )
    .await;

    // A worn item: the look changes. (Basic palette colours are free;
    // the costs of other colours are tests/crafting.rs.)
    let res = app
        .post(
            &format!("/api/items/{robe}/dye"),
            json!({ "dyes": [3, 7, 9] }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["dyes"], json!([3, 7, 9]));
    assert_eq!(res.body["slot"], "body");
    assert_eq!(res.body["id"], robe);
    let me = app.get("/api/characters/me", p.token()).await;
    assert_eq!(
        me.body["appearance"]["outfit"]["body"]["dyes"],
        json!([3, 7, 9])
    );
    assert_eq!(me.body["appearance_version"], 2);

    // An item in the wardrobe: stored, the look stays.
    let res = app
        .post(
            &format!("/api/items/{boots}/dye"),
            json!({ "dyes": [1, 10, 3] }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK);
    assert_eq!(res.body["location"], "inventory");
    assert_eq!(res.body["slot"], Value::Null);
    let me = app.get("/api/characters/me", p.token()).await;
    assert_eq!(me.body["appearance_version"], 2);
    // Wearing it later shows its dyes.
    let res = equip(&app, &p, "shoes", json!(boots)).await;
    assert_eq!(
        res.body["appearance"]["outfit"]["shoes"]["dyes"],
        json!([1, 10, 3])
    );

    let palette = shipped().palette.colours.len();
    for dyes in [
        json!([0, 0, palette]),
        json!([-1, 0, 0]),
        json!([300, 0, 0]),
    ] {
        let res = app
            .post(
                &format!("/api/items/{boots}/dye"),
                json!({ "dyes": dyes }),
                p.token(),
            )
            .await;
        assert_eq!(
            (res.status, res.body["error"].clone()),
            (StatusCode::BAD_REQUEST, json!("invalid_dye")),
            "{dyes}"
        );
    }
    for body in [
        json!({ "dyes": [1, 2] }),
        json!({ "dyes": "red" }),
        json!({}),
    ] {
        let res = app
            .post(&format!("/api/items/{boots}/dye"), body, p.token())
            .await;
        assert_eq!(res.body["error"], "invalid_body");
    }
    let res = app
        .post(
            "/api/items/nope/dye",
            json!({ "dyes": [1, 10, 3] }),
            p.token(),
        )
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::BAD_REQUEST, json!("invalid_path"))
    );

    // Another player's item.
    let other = app.player("Burrowsby").await;
    let res = app
        .post(
            &format!("/api/items/{robe}/dye"),
            json!({ "dyes": [9, 9, 9] }),
            other.token(),
        )
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::NOT_FOUND, json!("item_not_found"))
    );
    let me = app.get("/api/characters/me", p.token()).await;
    assert_eq!(
        me.body["appearance"]["outfit"]["body"]["dyes"],
        json!([3, 7, 9]),
        "untouched"
    );
}

#[tokio::test]
async fn furniture_placed_in_the_dorm_cannot_be_dyed() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    // The starter grant placed the bed in the room.
    let bed = item_of(&app, &p, placed_starter(House::Skytalon)).await;

    let res = app
        .post(
            &format!("/api/items/{bed}/dye"),
            json!({ "dyes": [1, 1, 1] }),
            p.token(),
        )
        .await;
    assert_eq!(
        (res.status, res.body["error"].clone()),
        (StatusCode::CONFLICT, json!("item_unavailable"))
    );
    assert_eq!(ids(&app, &p, "location=dorm").await, vec![bed]);
    // Unplaced furniture dyes fine.
    let other_piece = ids(&app, &p, "kind=furniture&location=inventory").await[0];
    let res = app
        .post(
            &format!("/api/items/{other_piece}/dye"),
            json!({ "dyes": [1, 1, 1] }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK);
}

#[tokio::test]
async fn wardrobe_writes_are_rate_limited_per_account() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let other = app.player("Roarwing").await;
    let mut limited = None;
    for _ in 0..45 {
        let res = equip(&app, &p, "hat", Value::Null).await;
        if res.status == StatusCode::TOO_MANY_REQUESTS {
            limited = Some(res);
            break;
        }
        assert_eq!(res.status, StatusCode::OK);
    }
    let res = limited.expect("the burst runs out");
    assert_eq!(res.body["error"], "rate_limited");
    assert!(
        res.headers[header::RETRY_AFTER]
            .to_str()
            .unwrap()
            .parse::<u64>()
            .unwrap()
            >= 1
    );
    // Reads are not limited, and other players have their own budget.
    assert_eq!(
        app.get("/api/equipment", p.token()).await.status,
        StatusCode::OK
    );
    assert_eq!(
        equip(&app, &other, "hat", Value::Null).await.status,
        StatusCode::OK
    );
}
