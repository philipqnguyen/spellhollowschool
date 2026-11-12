//! The wear rule on every way a garment is put on (the
//! wardrobe, a saved outfit, the creator, a worn restyle), and never on
//! owning or trading; the take-off guard for a character already wearing
//! what its body may not wear, with the ledger clean.

mod common;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer, unique_name};
use protocol::{Appearance, House, Worn};
use serde_json::{Value, json};
use server::catalog::shipped;
use server::inventory::starter;
use server::items::{self, NewItem};
use server::ledger::Reason;
use server::ledger::audit::reconcile;
use server::trade::{self, Side};

/// Starter clothing for everyone, female only.
const PLEATED_SKIRT: u16 = 302;
/// Crafted, male only.
const KILT: u16 = 305;
/// Crafted, female only, of the kilt's restyle family.
const ALINE_SKIRT: u16 = 304;
/// Moonstone Dust: the skirts' restyle input.
const MOONSTONE: u16 = 3021;
/// Restyle into a Tartan Kilt (known by everyone, skill 1).
const INTO_KILT: u16 = 122;
const HOUSE: House = House::Burrowsby;
const MALE: u8 = 0;
const FEMALE: u8 = 1;

/// A new character of `HOUSE` with body type `body` wearing the house's
/// default outfit.
async fn player(app: &TestApp, body: u8) -> TestPlayer {
    let (account_id, token) = app.account().await;
    let look = Appearance {
        outfit: starter::default_outfit(HOUSE),
        ..shipped().creator.default_appearance(body)
    };
    let res = app
        .post(
            "/api/characters",
            json!({ "name": unique_name(), "house": "Burrowsby", "appearance": look }),
            Some(&token),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    TestPlayer {
        account_id,
        token,
        id: res.body["id"].as_i64().unwrap(),
        name: res.body["name"].as_str().unwrap().to_owned(),
        house: "Burrowsby".into(),
    }
}

/// Gives one `def` (a new row); its id.
async fn give(app: &TestApp, owner: i64, def: u16, quantity: u32) -> i64 {
    let mut tx = app.state.pool.begin().await.unwrap();
    let rows = items::give(
        &mut tx,
        &NewItem::plain(owner, def, quantity, "inventory"),
        Reason::Admin,
        None,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    rows[0]
}

/// Lifts each player past level 2 (the crafted garments need it) through
/// the dev grant (`TestApp::dev`), so the XP ledger agrees.
async fn level_up(app: &TestApp, players: &[&TestPlayer]) {
    for p in players {
        let res = app
            .post("/api/dev/grant", json!({ "xp": 5_000 }), p.token())
            .await;
        assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
        assert!(res.body["level"].as_u64().unwrap() >= 2);
    }
}

/// The first of the character's items of `def`.
async fn item_of(app: &TestApp, owner: i64, def: u16) -> i64 {
    sqlx::query_scalar("SELECT id FROM items WHERE owner_id = $1 AND item_def = $2 ORDER BY id")
        .bind(owner)
        .bind(i32::from(def))
        .fetch_one(&app.state.pool)
        .await
        .unwrap()
}

async fn wear(app: &TestApp, p: &TestPlayer, slot: &str, item: i64) -> common::Response {
    app.post(
        "/api/equipment",
        json!({ "slot": slot, "item_id": item }),
        p.token(),
    )
    .await
}

async fn worn_legs(app: &TestApp, p: &TestPlayer) -> Value {
    app.get("/api/characters/me", p.token()).await.body["appearance"]["outfit"]["legs"].clone()
}

fn refused(res: &common::Response, message: &str) {
    assert_eq!(res.status, StatusCode::FORBIDDEN, "{:?}", res.body);
    assert_eq!(res.body["error"], "wear_restricted");
    assert_eq!(res.body["message"], message);
}

async fn clean(app: &TestApp, ids: &[i64]) {
    reconcile(&app.state.pool, ids)
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn the_wardrobe_refuses_what_the_body_may_not_wear_and_only_that() {
    let app = TestApp::dev().await;
    let (m, f) = (player(&app, MALE).await, player(&app, FEMALE).await);
    // Everyone owns the pleated skirt (a starter item); anyone may own a kilt.
    let m_skirt = item_of(&app, m.id, PLEATED_SKIRT).await;
    let f_skirt = item_of(&app, f.id, PLEATED_SKIRT).await;
    let m_kilt = give(&app, m.id, KILT, 1).await;
    level_up(&app, &[&m, &f]).await;
    let f_kilt = give(&app, f.id, KILT, 1).await;
    let trousers = worn_legs(&app, &m).await;
    assert_eq!(trousers["item_def"], 301);

    refused(
        &wear(&app, &m, "legs", m_skirt).await,
        "That is female only.",
    );
    assert_eq!(worn_legs(&app, &m).await, trousers, "nothing changed");
    refused(&wear(&app, &f, "legs", f_kilt).await, "That is male only.");

    let res = wear(&app, &f, "legs", f_skirt).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(
        res.body["appearance"]["outfit"]["legs"]["item_def"],
        PLEATED_SKIRT
    );
    let res = wear(&app, &m, "legs", m_kilt).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    // The female-only hat trims too.
    let bow = give(&app, m.id, 111, 1).await;
    refused(&wear(&app, &m, "hat", bow).await, "That is female only.");
    clean(&app, &[m.id, f.id]).await;
}

#[tokio::test]
async fn a_saved_outfit_with_a_garment_the_body_may_not_wear_is_refused_whole() {
    let app = TestApp::new().await;
    let (m, f) = (player(&app, MALE).await, player(&app, FEMALE).await);
    // The female body saves and wears an outfit with the skirt.
    let f_skirt = item_of(&app, f.id, PLEATED_SKIRT).await;
    assert_eq!(wear(&app, &f, "legs", f_skirt).await.status, StatusCode::OK);
    let saved = app
        .post("/api/outfits", json!({ "name": "Kilted" }), f.token())
        .await;
    assert_eq!(saved.status, StatusCode::CREATED, "{:?}", saved.body);
    let res = app
        .post(
            &format!("/api/outfits/{}/wear", saved.body["id"]),
            json!({}),
            f.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);

    // A male character's outfit holding the skirt (saved before the rule)
    // refuses whole: the hat in it does not go on either.
    let m_skirt = item_of(&app, m.id, PLEATED_SKIRT).await;
    let hat = item_of(&app, m.id, 101).await;
    let outfit: i64 = sqlx::query_scalar(
        "INSERT INTO saved_outfits (character_id, name, hat_item_id, legs_item_id)
         VALUES ($1, 'Old', $2, $3) RETURNING id",
    )
    .bind(m.id)
    .bind(hat)
    .bind(m_skirt)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    let before = app.get("/api/characters/me", m.token()).await.body;
    let res = app
        .post(&format!("/api/outfits/{outfit}/wear"), json!({}), m.token())
        .await;
    refused(&res, "That is female only.");
    let after = app.get("/api/characters/me", m.token()).await.body;
    assert_eq!(after["appearance"], before["appearance"]);
    assert_eq!(after["appearance_version"], before["appearance_version"]);
    clean(&app, &[m.id, f.id]).await;
}

#[tokio::test]
async fn the_creator_offers_the_starter_skirt_to_the_female_body_only() {
    let app = TestApp::new().await;
    let look = |body| {
        let mut a = Appearance {
            outfit: starter::default_outfit(HOUSE),
            ..shipped().creator.default_appearance(body)
        };
        a.outfit.legs = Some(Worn::new(PLEATED_SKIRT, [0; 3]));
        a
    };
    let (_, token) = app.account().await;
    let res = app
        .post(
            "/api/characters",
            json!({ "name": unique_name(), "house": "Burrowsby", "appearance": look(MALE) }),
            Some(&token),
        )
        .await;
    assert_eq!(res.status, StatusCode::BAD_REQUEST, "{:?}", res.body);
    assert_eq!(res.body["error"], "invalid_appearance");
    assert_eq!(res.body["message"], "That is female only.");
    let res = app
        .post(
            "/api/characters",
            json!({ "name": unique_name(), "house": "Burrowsby", "appearance": look(FEMALE) }),
            Some(&token),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(
        res.body["appearance"]["outfit"]["legs"]["item_def"],
        PLEATED_SKIRT
    );
}

#[tokio::test]
async fn a_worn_garment_is_not_restyled_into_one_the_body_may_not_wear() {
    let app = TestApp::with_config(|c| c.stations_anywhere = true).await;
    let f = player(&app, FEMALE).await;
    let worn = give(&app, f.id, ALINE_SKIRT, 1).await;
    let spare = give(&app, f.id, ALINE_SKIRT, 1).await;
    assert_ne!(worn, spare);
    give(&app, f.id, MOONSTONE, 2).await;
    assert_eq!(wear(&app, &f, "legs", worn).await.status, StatusCode::OK);

    let res = app
        .post(
            "/api/craft",
            json!({ "recipe_id": INTO_KILT, "item_id": worn }),
            f.token(),
        )
        .await;
    refused(&res, "That is male only.");
    let def: i32 = sqlx::query_scalar("SELECT item_def FROM items WHERE id = $1")
        .bind(worn)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    assert_eq!(def, i32::from(ALINE_SKIRT), "unchanged");

    // Unworn, the same restyle is free: she may own a kilt.
    let res = app
        .post(
            "/api/craft",
            json!({ "recipe_id": INTO_KILT, "item_id": spare }),
            f.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(res.body["item"]["item_def"], KILT);
    clean(&app, &[f.id]).await;
}

#[tokio::test]
async fn restricted_items_trade_and_mail_freely() {
    let app = TestApp::dev().await;
    let (m, f) = (player(&app, MALE).await, player(&app, FEMALE).await);
    let kilt = give(&app, f.id, KILT, 1).await;
    level_up(&app, &[&m, &f]).await;
    let skirt = item_of(&app, m.id, PLEATED_SKIRT).await;
    let side = |p: &TestPlayer, items: Vec<(i64, u32)>| Side {
        character_id: p.id,
        items,
        crowns: 0,
        reviewed: Vec::new(),
    };
    let key = (rand::random::<i64>() & i64::MAX).max(1);
    trade::commit(
        &app.state.pool,
        key,
        &side(&f, vec![(kilt, 1)]),
        &side(&m, vec![(skirt, 1)]),
    )
    .await
    .expect("traded");
    // Each now wears what came: the kilt for him, the second skirt for her.
    assert_eq!(wear(&app, &m, "legs", kilt).await.status, StatusCode::OK);
    assert_eq!(wear(&app, &f, "legs", skirt).await.status, StatusCode::OK);

    // And by owl: she mails him her own skirt; he owns it, may not wear it.
    let own = item_of(&app, f.id, PLEATED_SKIRT).await;
    let own = if own == skirt {
        sqlx::query_scalar(
            "SELECT id FROM items WHERE owner_id = $1 AND item_def = $2 AND id <> $3",
        )
        .bind(f.id)
        .bind(i32::from(PLEATED_SKIRT))
        .bind(skirt)
        .fetch_one(&app.state.pool)
        .await
        .unwrap()
    } else {
        own
    };
    let res = app
        .post(
            "/api/mail",
            json!({ "to": m.name, "subject": "For you", "body": "", "items": [{ "item_id": own, "quantity": 1 }] }),
            f.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    clean(&app, &[m.id, f.id]).await;
}

#[tokio::test]
async fn a_character_wearing_what_its_body_may_not_is_undressed_at_load() {
    let app = TestApp::new().await;
    let p = player(&app, FEMALE).await;
    let skirt = item_of(&app, p.id, PLEATED_SKIRT).await;
    assert_eq!(wear(&app, &p, "legs", skirt).await.status, StatusCode::OK);
    // A dev character dressed before the rule: the body is now male.
    sqlx::query(
        "UPDATE characters SET appearance = jsonb_set(appearance, '{body_type}', '0')
         WHERE id = $1",
    )
    .bind(p.id)
    .execute(&app.state.pool)
    .await
    .unwrap();
    let before: i32 = sqlx::query_scalar("SELECT appearance_version FROM characters WHERE id = $1")
        .bind(p.id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();

    let me = app.get("/api/characters/me", p.token()).await;
    assert_eq!(me.status, StatusCode::OK, "{:?}", me.body);
    assert_eq!(me.body["appearance"]["outfit"]["legs"], Value::Null);
    assert_eq!(me.body["appearance"]["body_type"], 0);
    assert_eq!(me.body["appearance_version"], before + 1);
    let location: String = sqlx::query_scalar("SELECT location FROM items WHERE id = $1")
        .bind(skirt)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    assert_eq!(location, "inventory", "still owned");
    // Once is enough: the next load changes nothing.
    let again = app.get("/api/characters/me", p.token()).await;
    assert_eq!(again.body["appearance_version"], before + 1);
    clean(&app, &[p.id]).await;
}
