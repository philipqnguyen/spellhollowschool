//! Crafting over HTTP: recipes, learning patterns, crafting at
//! stations with dyes and the maker's mark, Transfiguration restyles, skill
//! XP, busy time, idempotent retries, races; and the dye and finish
//! routes. Station checks are switched off (`Config::stations_anywhere`)
//! except in the test that checks them.

mod common;
#[path = "effects_harness.rs"]
mod harness;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer};
use serde_json::{Value, json};
use server::catalog::shipped;
use server::items::{self, NewItem};
use server::ledger::Reason;

/// Wool Skein, Silk Thread, Moonstone Dust, Glowing Mushroom.
const WOOL: u16 = 3001;
const SILK: u16 = 3003;
const MOONSTONE: u16 = 3021;
const MUSHROOM: u16 = 3026;
/// Knit a Cable Beanie (known by everyone, skill 1, wool x3, dyeable).
const BEANIE_RECIPE: u16 = 6;
const BEANIE: u16 = 106;
/// Restyle a knit hat into a Slouch Cap (moonstone x1).
const INTO_SLOUCH_CAP: u16 = 105;
const SLOUCH_CAP: u16 = 107;
/// Restyle a felt hat into a Felt Cloche (another family, skill 1).
const INTO_CLOCHE: u16 = 102;
/// Felt a Cloche: learned from pattern 3402, skill 2.
const CLOCHE_RECIPE: u16 = 4;
const CLOCHE_PATTERN: u16 = 3402;
/// Brew Glow Draught: tinted.
const GLOW_RECIPE: u16 = 321;
const GLOW: u16 = 3301;
/// Cobalt: a blue that is not in the free basic palette.
const COBALT: u8 = 34;

async fn app() -> TestApp {
    TestApp::with_config(|c| c.stations_anywhere = true).await
}

/// Gives `quantity` units of `item_def` (topping up stacks); the first row.
async fn give(app: &TestApp, owner: i64, item_def: u16, quantity: u32) -> i64 {
    let mut tx = app.state.pool.begin().await.unwrap();
    let item = NewItem::plain(owner, item_def, quantity, "inventory");
    let rows = items::give(&mut tx, &item, Reason::Admin, None)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    rows[0]
}

/// Units of `item_def` the character holds anywhere.
async fn units(app: &TestApp, owner: i64, item_def: u16) -> i64 {
    sqlx::query_scalar(
        "SELECT COALESCE(SUM(quantity), 0)::bigint FROM items WHERE owner_id = $1 AND item_def = $2",
    )
    .bind(owner)
    .bind(i32::from(item_def))
    .fetch_one(&app.state.pool)
    .await
    .unwrap()
}

/// Lets the character craft again at once.
async fn rested(app: &TestApp, p: &TestPlayer) {
    sqlx::query("UPDATE characters SET craft_ready_at = NULL WHERE id = $1")
        .bind(p.id)
        .execute(&app.state.pool)
        .await
        .unwrap();
}

fn blue_dye() -> u16 {
    let blues = shipped().palette.family_index("blues").unwrap();
    shipped()
        .catalog
        .items
        .iter()
        .find(|i| {
            i.dye
                .as_ref()
                .is_some_and(|d| shipped().palette.family_index(&d.family) == Some(blues))
        })
        .unwrap()
        .id
}

async fn craft(app: &TestApp, p: &TestPlayer, body: Value) -> common::Response {
    app.post("/api/craft", body, p.token()).await
}

#[tokio::test]
async fn a_craft_uses_its_inputs_and_dyes_and_marks_the_maker() {
    let app = app().await;
    let p = app.player("Skytalon").await;
    give(&app, p.id, WOOL, 5).await;
    let basic = &shipped().palette.basic;
    assert!(!basic.contains(&COBALT));
    let dyes = json!([basic[0], basic[4], COBALT]);

    // The blue is not basic: it needs a blue dye, and nothing is used up.
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": BEANIE_RECIPE, "dyes": dyes }),
    )
    .await;
    assert_eq!(res.status, StatusCode::CONFLICT, "{:?}", res.body);
    assert_eq!(res.body["error"], "missing_dyes");
    assert!(res.body["message"].as_str().unwrap().contains("Blues"));
    assert_eq!(units(&app, p.id, WOOL).await, 5);

    give(&app, p.id, blue_dye(), 1).await;
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": BEANIE_RECIPE, "dyes": dyes }),
    )
    .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let item = &res.body["item"];
    assert_eq!(item["item_def"], BEANIE);
    assert_eq!(item["dyes"], dyes);
    assert_eq!(item["maker"], p.name.as_str());
    assert_eq!(item["quantity"], 1);
    assert_eq!(item["location"], "inventory");
    assert_eq!(res.body["skill"]["discipline"], "charms");
    assert_eq!(res.body["skill"]["xp"], 15);
    assert_eq!(res.body["skill"]["level"], 1);
    assert!(res.body["ready_at"].is_string());
    assert_eq!(units(&app, p.id, WOOL).await, 2);
    assert_eq!(units(&app, p.id, blue_dye()).await, 0);

    // The ledger explains it: inputs and dye used, the hat made, one craft.
    let craft_id: i64 = sqlx::query_scalar("SELECT id FROM crafts WHERE character_id = $1")
        .bind(p.id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    let rows: Vec<(i32, String, i32)> = sqlx::query_as(
        "SELECT item_def, op, quantity FROM item_ledger
         WHERE reason = 'craft' AND ref_id = $1 ORDER BY id",
    )
    .bind(craft_id)
    .fetch_all(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(
        rows,
        vec![
            (i32::from(WOOL), "consume".into(), 3),
            (i32::from(blue_dye()), "consume".into(), 1),
            (i32::from(BEANIE), "create".into(), 1),
        ]
    );

    // Busy for the recipe's craft_secs, with a Retry-After.
    give(&app, p.id, WOOL, 1).await;
    let res = craft(&app, &p, json!({ "recipe_id": BEANIE_RECIPE })).await;
    assert_eq!(res.status, StatusCode::CONFLICT);
    assert_eq!(res.body["error"], "crafting_busy");
    let wait: u64 = res.headers["retry-after"]
        .to_str()
        .unwrap()
        .parse()
        .unwrap();
    assert!((1..=8).contains(&wait), "{wait}");
    rested(&app, &p).await;
    // Default dyes are free.
    let res = craft(&app, &p, json!({ "recipe_id": BEANIE_RECIPE })).await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let defaults = shipped().catalog.get(BEANIE).unwrap().dyes.to_array();
    assert_eq!(res.body["item"]["dyes"], json!(defaults));
    assert_eq!(res.body["skill"]["xp"], 30);
}

#[tokio::test]
async fn crafts_are_refused_without_inputs_knowledge_skill_or_a_station() {
    let app = app().await;
    let p = app.player("Burrowsby").await;
    let res = craft(&app, &p, json!({ "recipe_id": BEANIE_RECIPE })).await;
    assert_eq!(res.status, StatusCode::CONFLICT);
    assert_eq!(res.body["error"], "missing_inputs");
    assert!(res.body["message"].as_str().unwrap().contains("Wool"));
    let res = craft(&app, &p, json!({ "recipe_id": 60_000 })).await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    assert_eq!(res.body["error"], "recipe_not_found");
    let res = craft(&app, &p, json!({ "recipe_id": CLOCHE_RECIPE })).await;
    assert_eq!(res.status, StatusCode::FORBIDDEN);
    assert_eq!(res.body["error"], "recipe_unknown");
    // Dyes outside the palette, or for a recipe without chosen colours.
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": BEANIE_RECIPE, "dyes": [0, 0, 999] }),
    )
    .await;
    assert_eq!(res.body["error"], "invalid_dye");
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": BEANIE_RECIPE, "dyes": [0, 0] }),
    )
    .await;
    assert_eq!(res.body["error"], "invalid_dye");
    let res = craft(&app, &p, json!({ "recipe_id": 324, "dyes": [3] })).await;
    assert_eq!(
        res.body["error"], "invalid_dye",
        "the Diminishing Draught is not tinted"
    );

    // Learning the pattern uses one up; a second copy is refused.
    let pattern = give(&app, p.id, CLOCHE_PATTERN, 2).await;
    let res = app
        .post(
            "/api/recipes/learn",
            json!({ "item_id": pattern }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["recipe_id"], CLOCHE_RECIPE);
    assert_eq!(units(&app, p.id, CLOCHE_PATTERN).await, 1);
    let res = app
        .post(
            "/api/recipes/learn",
            json!({ "item_id": pattern }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::CONFLICT);
    assert_eq!(res.body["error"], "already_known");
    assert_eq!(units(&app, p.id, CLOCHE_PATTERN).await, 1, "kept");
    let wool = give(&app, p.id, WOOL, 3).await;
    let res = app
        .post("/api/recipes/learn", json!({ "item_id": wool }), p.token())
        .await;
    assert_eq!(res.body["error"], "not_a_pattern");
    let res = app
        .post("/api/recipes/learn", json!({ "item_id": 1 }), p.token())
        .await;
    assert_eq!(res.body["error"], "item_not_found");
    let learned: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM item_ledger WHERE reason = 'learn_pattern' AND item_id = $1",
    )
    .bind(pattern)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(learned, 1);

    // Known now, but the cloche needs Charms level 2.
    give(&app, p.id, SILK, 1).await;
    let res = craft(&app, &p, json!({ "recipe_id": CLOCHE_RECIPE })).await;
    assert_eq!(res.status, StatusCode::FORBIDDEN);
    assert_eq!(res.body["error"], "skill_too_low");
    sqlx::query("INSERT INTO skills (character_id, discipline, xp) VALUES ($1, 'charms', $2)")
        .bind(p.id)
        .bind(i32::try_from(shipped().recipes.skill_levels[0]).unwrap())
        .execute(&app.state.pool)
        .await
        .unwrap();
    let res = craft(&app, &p, json!({ "recipe_id": CLOCHE_RECIPE })).await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(res.body["skill"]["level"], 2);

    // Without the grant a station opens, nothing is made.
    let strict = TestApp::new().await;
    let res = strict
        .post(
            "/api/craft",
            json!({ "recipe_id": BEANIE_RECIPE }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::FORBIDDEN);
    assert_eq!(res.body["error"], "not_at_station");
}

#[tokio::test]
async fn restyling_keeps_the_garment_its_dyes_and_its_maker() {
    let app = app().await;
    let p = app.player("Serpentyne").await;
    give(&app, p.id, WOOL, 3).await;
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": BEANIE_RECIPE, "dyes": [0, 1, 3] }),
    )
    .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let hat = res.body["item"]["id"].as_i64().unwrap();
    rested(&app, &p).await;
    let worn = app
        .post(
            "/api/equipment",
            json!({ "slot": "hat", "item_id": hat }),
            p.token(),
        )
        .await;
    assert_eq!(worn.status, StatusCode::OK);
    let version = worn.body["appearance_version"].as_u64().unwrap();

    // Not of the recipe's family, no garment named, or no material.
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": INTO_CLOCHE, "item_id": hat }),
    )
    .await;
    assert_eq!(res.body["error"], "wrong_item");
    let res = craft(&app, &p, json!({ "recipe_id": INTO_SLOUCH_CAP })).await;
    assert_eq!(res.body["error"], "wrong_item");
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": INTO_SLOUCH_CAP, "item_id": hat }),
    )
    .await;
    assert_eq!(res.body["error"], "missing_inputs");

    give(&app, p.id, MOONSTONE, 1).await;
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": INTO_SLOUCH_CAP, "item_id": hat }),
    )
    .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let item = &res.body["item"];
    assert_eq!(item["id"], hat, "the same garment");
    assert_eq!(item["item_def"], SLOUCH_CAP);
    assert_eq!(item["dyes"], json!([0, 1, 3]));
    assert_eq!(item["maker"], p.name.as_str());
    assert_eq!(item["location"], "equipped");
    assert_eq!(res.body["skill"]["discipline"], "transfiguration");
    // Worn, so the look changed.
    let me = app.get("/api/characters/me", p.token()).await;
    assert_eq!(me.body["appearance_version"].as_u64().unwrap(), version + 1);
    assert_eq!(
        me.body["appearance"]["outfit"]["hat"]["item_def"],
        SLOUCH_CAP
    );
    let prev: Option<i32> = sqlx::query_scalar(
        "SELECT prev_item_def FROM item_ledger WHERE item_id = $1 AND op = 'restyle'",
    )
    .bind(hat)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(prev, Some(i32::from(BEANIE)));

    // Already a slouch cap.
    rested(&app, &p).await;
    give(&app, p.id, MOONSTONE, 1).await;
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": INTO_SLOUCH_CAP, "item_id": hat }),
    )
    .await;
    assert_eq!(res.body["error"], "wrong_item");
    // Dyes are the garment's own.
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": INTO_SLOUCH_CAP, "item_id": hat, "dyes": [1, 1, 1] }),
    )
    .await;
    assert_eq!(res.body["error"], "invalid_dye");
}

#[tokio::test]
async fn a_retried_craft_answers_the_first_and_races_make_one_item() {
    let app = app().await;
    let p = app.player("Roarwing").await;
    give(&app, p.id, WOOL, 3).await;
    let body = json!({ "recipe_id": BEANIE_RECIPE, "request_id": "knit-1" });
    let (one, two) = tokio::join!(craft(&app, &p, body.clone()), craft(&app, &p, body.clone()));
    assert_eq!(one.status, StatusCode::CREATED, "{:?}", one.body);
    assert_eq!(two.status, StatusCode::CREATED, "{:?}", two.body);
    assert_eq!(one.body["item"]["id"], two.body["item"]["id"]);
    let again = craft(&app, &p, body).await;
    assert_eq!(again.body["item"]["id"], one.body["item"]["id"]);
    assert_eq!(units(&app, p.id, BEANIE).await, 1);
    assert_eq!(units(&app, p.id, WOOL).await, 0);

    // Two different crafts racing for one set of inputs: one wins.
    let q = app.player("Roarwing").await;
    give(&app, q.id, WOOL, 3).await;
    let a = json!({ "recipe_id": BEANIE_RECIPE, "request_id": "a" });
    let b = json!({ "recipe_id": BEANIE_RECIPE, "request_id": "b" });
    let (ra, rb) = tokio::join!(craft(&app, &q, a), craft(&app, &q, b));
    let mut statuses = [ra.status, rb.status];
    statuses.sort();
    assert_eq!(statuses, [StatusCode::CREATED, StatusCode::CONFLICT]);
    let refused = if ra.status == StatusCode::CONFLICT {
        &ra
    } else {
        &rb
    };
    assert!(["crafting_busy", "missing_inputs"].contains(&refused.body["error"].as_str().unwrap()));
    assert_eq!(units(&app, q.id, BEANIE).await, 1);
    assert_eq!(units(&app, q.id, WOOL).await, 0);
    // Even once rested, the second has nothing left to use.
    rested(&app, &q).await;
    let res = craft(&app, &q, json!({ "recipe_id": BEANIE_RECIPE })).await;
    assert_eq!(res.body["error"], "missing_inputs");
    let crafts: i64 = sqlx::query_scalar("SELECT count(*) FROM crafts WHERE character_id = $1")
        .bind(q.id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    assert_eq!(crafts, 1);
}

#[tokio::test]
async fn tinted_potions_take_the_primary_dye_and_stack() {
    let app = app().await;
    let p = app.player("Skytalon").await;
    give(&app, p.id, MUSHROOM, 2).await;
    give(&app, p.id, MOONSTONE, 2).await;
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": GLOW_RECIPE, "dyes": [COBALT] }),
    )
    .await;
    assert_eq!(res.body["error"], "missing_dyes");
    give(&app, p.id, blue_dye(), 1).await;
    let res = craft(
        &app,
        &p,
        json!({ "recipe_id": GLOW_RECIPE, "dyes": [COBALT] }),
    )
    .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let item = &res.body["item"];
    assert_eq!(item["item_def"], GLOW);
    assert_eq!(item["quantity"], 2);
    assert_eq!(item["dyes"][0], COBALT);
    assert_eq!(item["maker"], p.name.as_str());
    assert_eq!(res.body["skill"]["discipline"], "potions");
    // A second brew of the same colour tops up the same stack.
    rested(&app, &p).await;
    give(&app, p.id, blue_dye(), 1).await;
    let again = craft(
        &app,
        &p,
        json!({ "recipe_id": GLOW_RECIPE, "dyes": [COBALT, 1, 1] }),
    )
    .await;
    assert_eq!(again.status, StatusCode::CREATED, "{:?}", again.body);
    assert_eq!(again.body["item"]["id"], item["id"]);
    assert_eq!(again.body["item"]["quantity"], 4);
}

#[tokio::test]
async fn recipes_and_skills_list_in_pages() {
    let app = app().await;
    let p = app.player("Burrowsby").await;
    let first = app
        .get("/api/recipes?discipline=potions&limit=5", p.token())
        .await;
    assert_eq!(first.status, StatusCode::OK);
    let items = first.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 5);
    assert!(items.iter().all(|r| r["discipline"] == "potions"));
    let first_ids: Vec<i64> = items.iter().map(|r| r["id"].as_i64().unwrap()).collect();
    assert!(first_ids.windows(2).all(|w| w[0] < w[1]));
    let next = first.body["next"].as_str().unwrap();
    let second = app
        .get(
            &format!("/api/recipes?discipline=potions&limit=5&after={next}"),
            p.token(),
        )
        .await;
    let second_first = second.body["items"][0]["id"].as_i64().unwrap();
    assert!(second_first > *first_ids.last().unwrap());
    // Known-by-default recipes, can be made at skill 1.
    let charms = app
        .get("/api/recipes?discipline=charms&limit=100", p.token())
        .await;
    let beanie = charms.body["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["id"] == BEANIE_RECIPE)
        .unwrap()
        .clone();
    assert_eq!(beanie["known"], true);
    assert_eq!(beanie["can_make"], true);
    assert_eq!(beanie["station"], "craft_charms");
    let unknown = app
        .get("/api/recipes?known=false&limit=100", p.token())
        .await;
    let list = unknown.body["items"].as_array().unwrap();
    assert!(!list.is_empty());
    assert!(
        list.iter()
            .all(|r| r["known"] == false && r["pattern"].is_number())
    );
    let bad = app.get("/api/recipes?discipline=alchemy", p.token()).await;
    assert_eq!(bad.status, StatusCode::BAD_REQUEST);

    let skills = app.get("/api/skills", p.token()).await;
    assert_eq!(skills.status, StatusCode::OK);
    let s = skills.body["skills"].as_array().unwrap();
    assert_eq!(s.len(), 10, "every school subject, Flying included");
    assert_eq!(s[0]["discipline"], "charms");
    assert_eq!(s[0]["xp"], 0);
    assert_eq!(s[0]["level"], 1);
    assert_eq!(s[0]["next_level_xp"], shipped().recipes.skill_levels[0]);
}

/// A starter garment of a Roarwing whose granted dyes are none of `avoid`.
async fn starter_garment(app: &TestApp, p: &TestPlayer, avoid: &[u8]) -> (i64, [u8; 3]) {
    let inv = app
        .get("/api/inventory?kind=clothing&limit=100", p.token())
        .await;
    for item in inv.body["items"].as_array().unwrap() {
        let dyes: [u8; 3] = serde_json::from_value(item["dyes"].clone()).unwrap();
        if dyes.iter().all(|d| !avoid.contains(d)) && item["location"] == "inventory" {
            return (item["id"].as_i64().unwrap(), dyes);
        }
    }
    panic!("no starter garment without those colours");
}

#[tokio::test]
async fn dyeing_is_free_in_the_basic_palette_and_costs_dyes_beyond_it() {
    let app = app().await;
    let p = app.player("Roarwing").await;
    let blues = [32, 33, 34, 35, 36, 37];
    let (garment, defaults) = starter_garment(&app, &p, &blues).await;
    let dye = |dyes: Value| {
        let app = &app;
        let p = &p;
        async move {
            app.post(
                &format!("/api/items/{garment}/dye"),
                json!({ "dyes": dyes }),
                p.token(),
            )
            .await
        }
    };
    let basic = shipped().palette.basic.clone();
    let res = dye(json!([basic[1], basic[2], basic[3]])).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    // Three blues: three blue dyes.
    give(&app, p.id, blue_dye(), 2).await;
    let res = dye(json!([33, 34, 35])).await;
    assert_eq!(res.status, StatusCode::CONFLICT);
    assert_eq!(res.body["error"], "missing_dyes");
    assert_eq!(units(&app, p.id, blue_dye()).await, 2, "nothing used");
    give(&app, p.id, blue_dye(), 1).await;
    let res = dye(json!([33, 34, 35])).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["dyes"], json!([33, 34, 35]));
    assert_eq!(units(&app, p.id, blue_dye()).await, 0);
    let used: i64 = sqlx::query_scalar(
        "SELECT COALESCE(SUM(quantity), 0)::bigint FROM item_ledger
         WHERE reason = 'dye' AND ref_id = $1 AND op = 'consume'",
    )
    .bind(garment)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(used, 3);
    // Keeping colours and going back to the defaults is free.
    let res = dye(json!([33, defaults[1], defaults[2]])).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let res = dye(json!(defaults)).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    // Only clothing and furniture take dyes; the palette is finite.
    let wool = give(&app, p.id, WOOL, 1).await;
    let res = app
        .post(
            &format!("/api/items/{wool}/dye"),
            json!({ "dyes": [0, 0, 0] }),
            p.token(),
        )
        .await;
    assert_eq!(res.body["error"], "invalid_dye");
    let res = dye(json!([0, 0, 255])).await;
    assert_eq!(res.body["error"], "invalid_dye");
}

#[tokio::test]
async fn finishes_are_used_up_and_show_on_the_worn_look() {
    let app = app().await;
    let p = app.player("Burrowsby").await;
    let (garment, _) = starter_garment(&app, &p, &[]).await;
    let def: i64 = sqlx::query_scalar("SELECT item_def::bigint FROM items WHERE id = $1")
        .bind(garment)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    let slot = shipped()
        .catalog
        .get(u16::try_from(def).unwrap())
        .unwrap()
        .slot
        .unwrap();
    let worn = app
        .post(
            "/api/equipment",
            json!({ "slot": slot.id(), "item_id": garment }),
            p.token(),
        )
        .await;
    assert_eq!(worn.status, StatusCode::OK, "{:?}", worn.body);
    let shimmer = shipped()
        .catalog
        .items
        .iter()
        .find(|i| i.finish == Some(protocol::Finish::Shimmer))
        .unwrap()
        .id;
    let lacquer = give(&app, p.id, shimmer, 2).await;
    let finish = |item: i64, with: i64| {
        let app = &app;
        let p = &p;
        async move {
            app.post(
                &format!("/api/items/{item}/finish"),
                json!({ "finish_item_id": with }),
                p.token(),
            )
            .await
        }
    };
    let res = finish(garment, lacquer).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["finish"], "shimmer");
    assert_eq!(units(&app, p.id, shimmer).await, 1);
    let me = app.get("/api/characters/me", p.token()).await;
    assert_eq!(
        me.body["appearance"]["outfit"][slot.id()]["finish"],
        "shimmer"
    );
    // Again: refused, nothing wasted.
    let res = finish(garment, lacquer).await;
    assert_eq!(res.body["error"], "already_finished");
    assert_eq!(units(&app, p.id, shimmer).await, 1);
    let wool = give(&app, p.id, WOOL, 1).await;
    assert_eq!(finish(garment, wool).await.body["error"], "not_a_finish");
    let furniture: i64 = sqlx::query_scalar(
        "SELECT i.id FROM items i WHERE i.owner_id = $1 AND i.item_def >= 1001 AND i.item_def < 2000
         LIMIT 1",
    )
    .bind(p.id)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(
        finish(furniture, lacquer).await.body["error"],
        "not_clothing"
    );
    assert_eq!(finish(garment, 1).await.body["error"], "item_not_found");
}

#[tokio::test]
async fn brewing_needs_the_cauldron_the_world_saw_you_use() {
    use protocol::{ClientMsg, DormErrorCode, ServerMsg, StationKind};
    let server = harness::TestServer::start().await;
    let ada = server.bot().await;
    let wrinklefig = 3024;
    server.give(ada.id, wrinklefig, 4, None).await;
    let mut a = server.join(&ada).await;
    let brew = json!({ "recipe_id": 324 });
    let (status, body) = server.post(&ada, "/api/craft", brew.clone()).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["error"], "not_at_station");

    a.send(&ClientMsg::Interact {
        target: "cauldron_classroom_1".into(),
    })
    .await;
    let opened = a
        .expect(|m| match m {
            ServerMsg::StationOpened { kind, target, .. } => Some((*kind, target.clone())),
            _ => None,
        })
        .await;
    assert_eq!(
        opened,
        (StationKind::Cauldron, "cauldron_classroom_1".to_owned())
    );
    let (status, body) = server.post(&ada, "/api/craft", brew).await;
    assert_eq!(status, StatusCode::CREATED, "{body:?}");
    assert_eq!(body["item"]["quantity"], 2);
    // The cauldron's grant is not a knitting corner's.
    server.give(ada.id, WOOL, 3, None).await;
    let (status, body) = server
        .post(&ada, "/api/craft", json!({ "recipe_id": BEANIE_RECIPE }))
        .await;
    assert_eq!(
        (status, body["error"].as_str()),
        (StatusCode::FORBIDDEN, Some("not_at_station"))
    );
    a.send(&ClientMsg::Interact {
        target: "craft_charms_far".into(),
    })
    .await;
    let code = a
        .expect(|m| match m {
            ServerMsg::DormError { code, .. } => Some(*code),
            _ => None,
        })
        .await;
    assert_eq!(code, DormErrorCode::TooFar);
}
