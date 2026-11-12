//! Character creation (with an appearance and the starter grant), lookup,
//! the content endpoints, and the session/persistence API the world uses.

mod common;

use axum::http::{Method, Request, StatusCode, header};
use common::{TestApp, unique_name};
use protocol::{Appearance, GearSlot, House, Worn};
use serde_json::{Value, json};
use server::catalog::shipped;
use server::characters::{self, SavedPosition, SavedZone};
use server::inventory::starter;
use sim_core::ItemKind;

/// The starter set's size for `house`: (clothing, furniture).
fn starter_counts(house: House) -> (usize, usize) {
    let items: Vec<_> = shipped().catalog.starter_items(house).collect();
    let clothing = items
        .iter()
        .filter(|i| i.kind == ItemKind::Clothing)
        .count();
    (clothing, items.len() - clothing)
}

/// Every item a player owns (one big page).
async fn all_items(app: &TestApp, token: &str) -> Vec<Value> {
    let res = app.get("/api/inventory?limit=100", Some(token)).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    res.body["items"].as_array().unwrap().clone()
}

/// A starter item of `house` worn in `slot` that is not the default one.
fn non_default_starter(house: House, slot: GearSlot) -> u16 {
    shipped()
        .catalog
        .starter_items(house)
        .find(|i| i.slot == Some(slot) && !i.has_tag("default"))
        .unwrap()
        .id
}

#[tokio::test]
async fn creation_grants_the_starter_set_and_wears_the_default_outfit() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let items = all_items(&app, &p.token).await;
    let (clothing, furniture) = starter_counts(House::Roarwing);
    assert_eq!(items.len(), clothing + furniture);
    assert!((12..=16).contains(&clothing) && (10..=14).contains(&furniture));

    // Exactly the default outfit is worn, in the house colours.
    let outfit = starter::default_outfit(House::Roarwing);
    let worn: Vec<&Value> = items
        .iter()
        .filter(|i| i["location"] == "equipped")
        .collect();
    assert_eq!(worn.len(), 3);
    for slot in [GearSlot::Body, GearSlot::Legs, GearSlot::Shoes] {
        let expected = outfit.get(slot).unwrap();
        let item = worn.iter().find(|i| i["slot"] == slot.id()).unwrap();
        assert_eq!(item["item_def"], expected.item_def);
        assert_eq!(item["dyes"], json!(expected.dyes));
    }
    let robe = outfit.body.unwrap();
    let house = shipped().palette.house(House::Roarwing).unwrap();
    assert!(
        robe.dyes.contains(&house.primary),
        "the robe has house trim"
    );
    // House-dyed starter items carry the house colours; others their defaults.
    for item in &items {
        let def = shipped()
            .catalog
            .get(item["item_def"].as_u64().unwrap() as u16)
            .unwrap();
        if item["location"] == "inventory" {
            assert_eq!(item["slot"], Value::Null);
            assert_eq!(
                item["dyes"],
                json!(def.granted_dyes(House::Roarwing, &shipped().palette))
            );
        }
        assert!(def.is_starter_for(House::Roarwing), "{}", def.id);
    }
}

/// A new character's room starts furnished with their Four-Poster Bed: the
/// granted instance, in its house's colours, placed at the house's starter
/// spot (the dorm shell's `starter_furniture`), as the room loads it.
#[tokio::test]
async fn a_new_characters_dorm_has_the_bed_placed_at_the_house_spot() {
    let app = TestApp::new().await;
    let c = shipped();
    for house in [House::Roarwing, House::Burrowsby, House::Serpentyne] {
        let p = app.player(&format!("{house:?}")).await;
        let spot = server::dorm::shells::shipped().starter_furniture(house)[0];
        let bed = c.catalog.get(spot.item).unwrap();
        let room = server::dorm::store::load_room(&app.state.pool, p.id)
            .await
            .unwrap();
        assert_eq!(room.len(), 1, "{house:?}");
        let placed = room[0];
        assert_eq!(placed.item_def, bed.id);
        assert_eq!(placed.pos, protocol::QPos::from_m(spot.pos));
        assert_eq!(placed.yaw, protocol::yaw_to_u8(spot.yaw));
        assert_eq!(placed.dyes, bed.granted_dyes(house, &c.palette));
        let colours = c.palette.house(house).unwrap();
        assert!(
            placed.dyes.contains(&colours.primary),
            "{house:?}: house hangings"
        );
        // The inventory lists it in the dorm, not as a second bed to place.
        let items = all_items(&app, &p.token).await;
        let beds: Vec<_> = items.iter().filter(|i| i["item_def"] == bed.id).collect();
        assert_eq!(beds.len(), 1);
        assert_eq!(beds[0]["location"], "dorm");
        assert_eq!(beds[0]["id"], placed.item_id);
        // Granted into the room in the ledger.
        let to: String = sqlx::query_scalar(
            "SELECT to_location FROM item_ledger WHERE item_id = $1 AND reason = 'starter'",
        )
        .bind(placed.item_id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
        assert_eq!(to, "dorm");
    }
}

#[tokio::test]
async fn creation_with_an_appearance() {
    let app = TestApp::new().await;
    let (_, token) = app.account().await;
    let shirt = non_default_starter(House::Skytalon, GearSlot::Body);
    let hat = non_default_starter(House::Skytalon, GearSlot::Hat);
    let mut look = Appearance {
        body_type: 1,
        height: 40,
        face: [0, 255, 128, 90, 30],
        skin_tone: 5,
        hair_style: 19,
        hair_colour: 16,
        eye_colour: 7,
        ..Appearance::default()
    };
    look.outfit.body = Some(Worn::new(shirt, [4, 5, 6]));
    look.outfit.hat = Some(Worn::new(hat, [7, 8, 9]));
    let res = app
        .post(
            "/api/characters",
            json!({ "name": unique_name(), "house": "Skytalon", "appearance": look }),
            Some(&token),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(res.body["appearance"], serde_json::to_value(look).unwrap());

    // The worn instances have the chosen dyes; legs and shoes stay empty.
    let eq = app.get("/api/equipment", Some(&token)).await;
    assert_eq!(eq.status, StatusCode::OK);
    assert_eq!(eq.body["body"]["item_def"], shirt);
    assert_eq!(eq.body["body"]["dyes"], json!([4, 5, 6]));
    assert_eq!(eq.body["hat"]["dyes"], json!([7, 8, 9]));
    assert_eq!(eq.body["legs"], Value::Null);
    assert_eq!(eq.body["appearance_version"], 1);
    let (clothing, furniture) = starter_counts(House::Skytalon);
    assert_eq!(all_items(&app, &token).await.len(), clothing + furniture);
}

/// Creator v2: hair styles belong to one body, retired ones are refused on
/// creation (only stored looks are migrated), eye colours are checked.
#[tokio::test]
async fn hair_of_the_other_body_retired_hair_and_bad_eyes_are_refused() {
    let app = TestApp::new().await;
    let (_, token) = app.account().await;
    let base = starter::default_appearance(House::Burrowsby);
    for (what, appearance) in [
        (
            "a retired hair style",
            Appearance {
                hair_style: 2,
                ..base
            },
        ),
        (
            "a hair style of the other body",
            Appearance {
                hair_style: 18,
                ..base
            },
        ),
        (
            "eye colour",
            Appearance {
                eye_colour: 13,
                ..base
            },
        ),
    ] {
        let res = app
            .post(
                "/api/characters",
                json!({ "name": unique_name(), "house": "Burrowsby", "appearance": appearance }),
                Some(&token),
            )
            .await;
        assert_eq!(
            res.status,
            StatusCode::BAD_REQUEST,
            "{what}: {:?}",
            res.body
        );
        assert_eq!(res.body["error"], "invalid_appearance", "{what}");
    }
    // A female look in the bolder colours (indices 18 and 12) is fine.
    let look = Appearance {
        body_type: 1,
        hair_style: 17,
        hair_colour: 18,
        eye_colour: 12,
        ..base
    };
    let res = app
        .post(
            "/api/characters",
            json!({ "name": unique_name(), "house": "Burrowsby", "appearance": look }),
            Some(&token),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(res.body["appearance"]["eye_colour"], 12);
}

#[tokio::test]
async fn invalid_appearances_are_refused_and_create_nothing() {
    let app = TestApp::new().await;
    let (_, token) = app.account().await;
    let base = starter::default_appearance(House::Serpentyne);
    let roarwing_robe = starter::default_outfit(House::Roarwing).body;
    let hat = non_default_starter(House::Serpentyne, GearSlot::Hat);
    let not_starter = shipped()
        .catalog
        .items
        .iter()
        .find(|i| {
            i.kind == ItemKind::Clothing
                && !i.is_starter_for(House::Serpentyne)
                && i.house().is_none()
        })
        .map(|i| i.id);
    let mut cases: Vec<(&str, Appearance)> = vec![
        (
            "body type",
            Appearance {
                body_type: 7,
                ..base
            },
        ),
        (
            "skin tone",
            Appearance {
                skin_tone: 250,
                ..base
            },
        ),
        (
            "hair style",
            Appearance {
                hair_style: 9_999,
                ..base
            },
        ),
        (
            "hair colour",
            Appearance {
                hair_colour: 250,
                ..base
            },
        ),
    ];
    let mut with = |what, f: &dyn Fn(&mut Appearance)| {
        let mut a = base;
        f(&mut a);
        cases.push((what, a));
    };
    with("another house's robe", &|a| a.outfit.body = roarwing_robe);
    with("a hat worn as shoes", &|a| {
        a.outfit.shoes = Some(Worn::new(hat, [0; 3]))
    });
    with("a dye outside the palette", &|a| {
        a.outfit.body.as_mut().unwrap().dyes[2] = 200
    });
    with("an unknown item", &|a| {
        a.outfit.legs = Some(Worn::new(60_000, [0; 3]))
    });
    if let Some(def) = not_starter {
        with("a non-starter item", &|a| {
            a.outfit.body = Some(Worn::new(def, [0; 3]))
        });
    }
    for (what, appearance) in cases {
        let res = app
            .post(
                "/api/characters",
                json!({ "name": unique_name(), "house": "Serpentyne", "appearance": appearance }),
                Some(&token),
            )
            .await;
        assert_eq!(
            res.status,
            StatusCode::BAD_REQUEST,
            "{what}: {:?}",
            res.body
        );
        assert_eq!(res.body["error"], "invalid_appearance", "{what}");
    }
    // A malformed appearance is a malformed body.
    let res = app
        .post(
            "/api/characters",
            json!({ "name": unique_name(), "house": "Serpentyne", "appearance": { "body_type": 0 } }),
            Some(&token),
        )
        .await;
    assert_eq!(res.body["error"], "invalid_body");
    let me = app.get("/api/characters/me", Some(&token)).await;
    assert_eq!(me.status, StatusCode::NOT_FOUND, "nothing was created");
    // Ten refusals used up this account's creation budget; the same look on
    // a fresh account is fine.
    let (_, fresh) = app.account().await;
    let res = app
        .post(
            "/api/characters",
            json!({ "name": unique_name(), "house": "Serpentyne", "appearance": base }),
            Some(&fresh),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED);
}

#[tokio::test]
async fn a_failed_creation_grants_nothing() {
    let app = TestApp::new().await;
    let taken = app.player("Burrowsby").await;
    let (account_id, token) = app.account().await;
    let res = app
        .post(
            "/api/characters",
            json!({ "name": taken.name.to_uppercase(), "house": "Burrowsby" }),
            Some(&token),
        )
        .await;
    assert_eq!(res.body["error"], "name_taken");
    let orphans: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM items i JOIN characters c ON c.id = i.owner_id WHERE c.account_id = $1",
    )
    .bind(account_id)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(orphans, 0);
    let (c, f) = starter_counts(House::Burrowsby);
    assert_eq!(all_items(&app, &taken.token).await.len(), c + f);
}

#[tokio::test]
async fn characters_from_before_1b_get_the_starter_set_once() {
    let app = TestApp::new().await;
    let (account_id, token) = app.account().await;
    // As the first characters were made: no appearance, no grant, resuming in the castle.
    let name = unique_name();
    let old = characters::insert(
        &app.state.pool,
        account_id,
        &name,
        House::Skytalon,
        SavedZone::Castle,
    )
    .await
    .unwrap();
    app.select_directly(account_id, old.id).await;
    let before = server::validate_session(&app.state.pool, &token)
        .await
        .unwrap();
    assert_eq!(before.character.unwrap().appearance_version, 0);

    let me = app.get("/api/characters/me", Some(&token)).await;
    assert_eq!(me.status, StatusCode::OK, "{:?}", me.body);
    assert_eq!(me.body["appearance_version"], 1);
    let expected = starter::default_appearance(House::Skytalon);
    assert_eq!(
        me.body["appearance"],
        serde_json::to_value(expected).unwrap()
    );
    let (c, f) = starter_counts(House::Skytalon);
    assert_eq!(all_items(&app, &token).await.len(), c + f);

    // Later requests change nothing, and the world sees the new look.
    let again = app.get("/api/characters/me", Some(&token)).await;
    assert_eq!(again.body, me.body);
    assert_eq!(all_items(&app, &token).await.len(), c + f);
    let after = server::validate_session(&app.state.pool, &token)
        .await
        .unwrap();
    let after = after.character.unwrap();
    assert_eq!((after.appearance, after.appearance_version), (expected, 1));
    assert_eq!(after.zone, SavedZone::Castle, "the resume point is kept");
}

#[tokio::test]
async fn concurrent_first_requests_grant_once() {
    let app = TestApp::new().await;
    let (account_id, token) = app.account().await;
    let old = characters::insert(
        &app.state.pool,
        account_id,
        &unique_name(),
        House::Roarwing,
        SavedZone::Castle,
    )
    .await
    .unwrap();
    app.select_directly(account_id, old.id).await;
    let (a, b, c) = tokio::join!(
        app.get("/api/characters/me", Some(&token)),
        app.get("/api/inventory", Some(&token)),
        app.get("/api/equipment", Some(&token)),
    );
    for r in [&a, &b, &c] {
        assert_eq!(r.status, StatusCode::OK, "{:?}", r.body);
    }
    let (cl, f) = starter_counts(House::Roarwing);
    assert_eq!(all_items(&app, &token).await.len(), cl + f);
    assert_eq!(a.body["appearance_version"], 1);
}

#[tokio::test]
async fn content_endpoints_serve_the_shipped_files_with_caching() {
    let app = TestApp::new().await;
    let catalog = app.get("/api/catalog", None).await;
    assert_eq!(catalog.status, StatusCode::OK);
    let shipped_catalog: Value =
        serde_json::from_str(include_str!("../../../data/items/catalog.json")).unwrap();
    assert_eq!(catalog.body, shipped_catalog);
    assert_eq!(
        catalog.headers[header::CACHE_CONTROL],
        "public, max-age=3600"
    );

    let creator = app.get("/api/creator", None).await;
    assert_eq!(creator.status, StatusCode::OK);
    let shipped_creator: Value =
        serde_json::from_str(include_str!("../../../data/characters/creator.json")).unwrap();
    let shipped_palette: Value =
        serde_json::from_str(include_str!("../../../data/items/palette.json")).unwrap();
    assert_eq!(creator.body["creator"], shipped_creator);
    assert_eq!(creator.body["palette"], shipped_palette);

    // Revalidation: the same ETag gives 304, the other file's does not.
    let etag = catalog.headers[header::ETAG].to_str().unwrap().to_owned();
    assert_ne!(etag, creator.headers[header::ETAG].to_str().unwrap());
    let req = |tag: &str| {
        Request::builder()
            .method(Method::GET)
            .uri("/api/catalog")
            .header(header::IF_NONE_MATCH, tag)
            .body(axum::body::Body::empty())
            .unwrap()
    };
    let cached = app.send(req(&etag)).await;
    assert_eq!(cached.status, StatusCode::NOT_MODIFIED);
    assert_eq!(cached.body, Value::Null);
    let stale = app.send(req("\"0000\"")).await;
    assert_eq!(stale.status, StatusCode::OK);
}

#[tokio::test]
async fn character_endpoints_require_a_session() {
    let app = TestApp::new().await;
    let me = app.get("/api/characters/me", None).await;
    assert_eq!(me.status, StatusCode::UNAUTHORIZED);
    assert_eq!(me.body["error"], "unauthorized");
    let bogus = app.get("/api/characters/me", Some("not-a-token")).await;
    assert_eq!(bogus.status, StatusCode::UNAUTHORIZED);
    let create = app
        .post(
            "/api/characters",
            json!({ "name": unique_name(), "house": "Skytalon" }),
            None,
        )
        .await;
    assert_eq!(create.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn create_then_get_me() {
    let app = TestApp::new().await;
    let (_, token) = app.signed_in().await;

    let me = app.get("/api/characters/me", Some(&token)).await;
    assert_eq!(me.status, StatusCode::NOT_FOUND);
    assert_eq!(me.body["error"], "no_character");

    let name = unique_name();
    let res = app
        .post(
            "/api/characters",
            json!({ "name": format!(" {name} "), "house": "Burrowsby" }),
            Some(&token),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(res.body["name"], name);
    assert_eq!(res.body["house"], "Burrowsby");
    assert_eq!(res.body["appearance_version"], 1);
    assert!(res.body["id"].as_i64().unwrap() > 0);
    let expected = serde_json::to_value(starter::default_appearance(House::Burrowsby)).unwrap();
    assert_eq!(res.body["appearance"], expected);

    let me = app.get("/api/characters/me", Some(&token)).await;
    assert_eq!(me.status, StatusCode::OK);
    assert_eq!(me.body, res.body);
}

#[tokio::test]
async fn names_are_unique_ignoring_case() {
    let app = TestApp::new().await;
    let (_, a) = app.signed_in().await;
    let (_, b) = app.signed_in().await;
    // Includes a non-ASCII letter: uniqueness must fold Unicode case, not just ASCII.
    let name = format!("Émile{}", &unique_name()[4..]);
    let res = app
        .post(
            "/api/characters",
            json!({ "name": name, "house": "Skytalon" }),
            Some(&a),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);

    let res = app
        .post(
            "/api/characters",
            json!({ "name": name.to_uppercase(), "house": "Roarwing" }),
            Some(&b),
        )
        .await;
    assert_eq!(res.status, StatusCode::CONFLICT);
    assert_eq!(res.body["error"], "name_taken");
}

#[tokio::test]
async fn invalid_names_and_houses_are_rejected() {
    let app = TestApp::new().await;
    let (_, token) = app.signed_in().await;
    for name in ["Al", "Bad1Name", "--Dash", "Two  Spaces"] {
        let res = app
            .post(
                "/api/characters",
                json!({ "name": name, "house": "Roarwing" }),
                Some(&token),
            )
            .await;
        assert_eq!(res.status, StatusCode::BAD_REQUEST, "{name}");
        assert_eq!(res.body["error"], "invalid_name");
    }
    let res = app
        .post(
            "/api/characters",
            json!({ "name": unique_name(), "house": "Nowhere" }),
            Some(&token),
        )
        .await;
    assert_eq!(res.status, StatusCode::BAD_REQUEST);
    assert_eq!(res.body["error"], "invalid_body");
}

#[tokio::test]
async fn validate_session_returns_character_and_saved_position() {
    let app = TestApp::new().await;
    let (_, token) = app.signed_in().await;
    let name = unique_name();
    app.post(
        "/api/characters",
        json!({ "name": name, "house": "Serpentyne" }),
        Some(&token),
    )
    .await;

    let info = server::validate_session(&app.state.pool, &token)
        .await
        .unwrap();
    let c = info.character.expect("character");
    assert_eq!(c.name, name);
    assert_eq!(c.house, House::Serpentyne);
    assert_eq!(c.last_pos, None, "new characters spawn at the layout spawn");
    assert_eq!(c.appearance_version, 1);
    assert_eq!(
        c.zone,
        SavedZone::Castle,
        "new characters start in the castle, on the front steps"
    );
    assert_eq!(c.appearance, starter::default_appearance(House::Serpentyne));

    let saved = characters::save_positions(
        &app.state.pool,
        &[
            SavedPosition {
                character_id: c.id,
                zone: SavedZone::Castle,
                pos: [12.5, 3.0, -40.25],
            },
            SavedPosition {
                character_id: i64::MAX,
                zone: SavedZone::Dorm,
                pos: [0.0; 3],
            },
        ],
    )
    .await
    .unwrap();
    assert_eq!(saved, 1);
    let info = server::validate_session(&app.state.pool, &token)
        .await
        .unwrap();
    let c = info.character.unwrap();
    assert_eq!(c.last_pos, Some([12.5, 3.0, -40.25]));
    assert_eq!(c.zone, SavedZone::Castle);

    let by_id = characters::find(&app.state.pool, c.id).await.unwrap();
    assert_eq!(by_id.map(|c| c.name), Some(name));
}

#[tokio::test]
async fn validate_session_rejects_garbage() {
    let app = TestApp::new().await;
    assert!(
        server::validate_session(&app.state.pool, "")
            .await
            .is_none()
    );
    assert!(
        server::validate_session(&app.state.pool, &"A".repeat(43))
            .await
            .is_none()
    );
}
