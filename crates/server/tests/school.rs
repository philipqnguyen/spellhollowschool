//! Over the database: the shared XP grant (levels,
//! the ledger, once per ref, daily caps), house points, learned spells and
//! loadouts, required levels when equipping, player XP from crafts and
//! finds, and the dev grant and clock.

mod common;

use axum::http::StatusCode;
use chrono::{TimeZone, Utc};
use common::{TestApp, TestPlayer};
use protocol::{House, XpSource};
use serde_json::json;
use server::catalog::shipped;
use server::house_points::{self, PointsSource};
use server::items::{self, NewItem};
use server::ledger::Reason;
use server::progression::{self, grant_xp};
use sim_core::Drops;

async fn xp_of(app: &TestApp, id: i64) -> (i32, i16) {
    sqlx::query_as("SELECT xp, level FROM characters WHERE id = $1")
        .bind(id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap()
}

/// One grant in its own transaction.
async fn grant(
    app: &TestApp,
    p: &TestPlayer,
    source: XpSource,
    ref_id: Option<i64>,
    amount: u32,
    day: u32,
) -> progression::XpGrant {
    let now = Utc.with_ymd_and_hms(2026, 10, day, 12, 0, 0).unwrap();
    let mut tx = app.state.pool.begin().await.unwrap();
    let g = grant_xp(&mut tx, p.id, source, ref_id, amount, now)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    g
}

#[tokio::test]
async fn xp_grants_level_up_record_the_ledger_and_happen_once() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    assert_eq!(
        xp_of(&app, p.id).await,
        (0, 1),
        "new characters start at level 1"
    );
    let level2 = shipped().progression.levels[1];

    let g = grant(&app, &p, XpSource::ClassAttend, Some(7_001), level2 + 10, 5).await;
    assert_eq!(
        (g.granted, g.xp, g.level_before, g.level),
        (level2 + 10, level2 + 10, 1, 2)
    );
    assert!(g.leveled_up() && !g.capped && !g.duplicate);
    assert_eq!(xp_of(&app, p.id).await, ((level2 + 10) as i32, 2));

    // The same source and ref again: nothing more.
    let again = grant(&app, &p, XpSource::ClassAttend, Some(7_001), 50, 5).await;
    assert!(again.duplicate);
    assert_eq!((again.granted, again.xp), (0, level2 + 10));
    // Another source with the same ref is another grant.
    let bonus = grant(&app, &p, XpSource::ClassBonus, Some(7_001), 30, 5).await;
    assert_eq!(bonus.granted, 30);

    let rows: Vec<(String, Option<i64>, i32, i32)> = sqlx::query_as(
        "SELECT source, ref_id, amount, xp_after FROM xp_ledger WHERE character_id = $1 ORDER BY id",
    )
    .bind(p.id)
    .fetch_all(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(
        rows,
        vec![
            (
                "class_attend".into(),
                Some(7_001),
                (level2 + 10) as i32,
                (level2 + 10) as i32
            ),
            ("class_bonus".into(), Some(7_001), 30, (level2 + 40) as i32),
        ]
    );
    // The ledger is append-only.
    let edit = sqlx::query("UPDATE xp_ledger SET amount = 0 WHERE character_id = $1")
        .bind(p.id)
        .execute(&app.state.pool)
        .await;
    assert!(edit.is_err());
}

#[tokio::test]
async fn capped_sources_stop_at_the_daily_cap() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    let (_, cap) = shipped().progression.daily_cap(XpSource::DuelWin).unwrap();
    let first = grant(&app, &p, XpSource::DuelWin, Some(1), cap - 10, 6).await;
    assert_eq!(first.granted, cap - 10);
    // Losses count against the same duel cap.
    let second = grant(&app, &p, XpSource::DuelLoss, Some(2), 40, 6).await;
    assert_eq!((second.granted, second.capped), (10, true));
    let third = grant(&app, &p, XpSource::DuelDraw, Some(3), 40, 6).await;
    assert_eq!((third.granted, third.capped), (0, true));
    // Capped to nothing is still recorded once (a retry is a duplicate).
    assert!(
        grant(&app, &p, XpSource::DuelDraw, Some(3), 40, 6)
            .await
            .duplicate
    );
    // A new UTC day, a new cap; other groups are separate.
    assert_eq!(
        grant(&app, &p, XpSource::DuelWin, Some(4), 40, 7)
            .await
            .granted,
        40
    );
    assert_eq!(
        grant(&app, &p, XpSource::Find, None, 15, 6).await.granted,
        15
    );
    // Classes share a daily cap too; admin grants are not capped.
    let (_, class_cap) = shipped()
        .progression
        .daily_cap(XpSource::ClassAttend)
        .unwrap();
    let class = grant(&app, &p, XpSource::ClassAttend, Some(5), 5_000, 6).await;
    assert_eq!((class.granted, class.capped), (class_cap, true));
    assert_eq!(
        grant(&app, &p, XpSource::Admin, None, 5_000, 6)
            .await
            .granted,
        5_000
    );
}

#[tokio::test]
async fn house_points_are_recorded_once_per_ref_and_totalled() {
    let app = TestApp::new().await;
    let p = app.player("Serpentyne").await;
    let before = house_points::totals(&app.state.pool).await.unwrap();
    let mut tx = app.state.pool.begin().await.unwrap();
    for (k, want) in [true, false].into_iter().enumerate() {
        let made = house_points::award(
            &mut tx,
            House::Serpentyne,
            5,
            Some(p.id),
            PointsSource::Class,
            Some(42),
        )
        .await
        .unwrap();
        assert_eq!(made, want, "award {k}: once per ref");
    }
    assert!(
        !house_points::award(
            &mut tx,
            House::Serpentyne,
            0,
            Some(p.id),
            PointsSource::Duel,
            None
        )
        .await
        .unwrap(),
        "zero points record nothing"
    );
    tx.commit().await.unwrap();
    let after = house_points::totals(&app.state.pool).await.unwrap();
    let pts = |t: &[house_points::HouseTotal]| {
        t.iter()
            .find(|h| h.house == House::Serpentyne)
            .unwrap()
            .points
    };
    assert!(pts(&after) >= pts(&before) + 5);
    let res = app.get("/api/house-points", None).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["houses"].as_array().unwrap().len(), 4);
}

#[tokio::test]
async fn spells_are_learned_once_and_loadouts_default_to_what_fits() {
    let app = TestApp::new().await;
    let p = app.player("Roarwing").await;
    let spells = &shipped().spells;
    let mut tx = app.state.pool.begin().await.unwrap();
    let new = progression::teach(&mut tx, p.id, "dueling_club", 1)
        .await
        .unwrap();
    assert_eq!(new.len(), 3);
    assert!(
        progression::teach(&mut tx, p.id, "dueling_club", 1)
            .await
            .unwrap()
            .is_empty()
    );
    tx.commit().await.unwrap();
    let school = progression::load_school(&app.state.pool, p.id)
        .await
        .unwrap();
    assert_eq!(school.xp, 0);
    assert_eq!(school.learned.len(), 3);
    assert_eq!(school.loadout, spells.default_loadout(&school.learned, 3));
    // A stored loadout that no longer fits falls back to the default.
    sqlx::query("INSERT INTO loadouts (character_id, spells) VALUES ($1, $2)")
        .bind(p.id)
        .bind(vec![1i32])
        .execute(&app.state.pool)
        .await
        .unwrap();
    let school = progression::load_school(&app.state.pool, p.id)
        .await
        .unwrap();
    assert_eq!(school.loadout.len(), 3, "Lucerna is no dueling spell");
}

#[tokio::test]
async fn required_levels_are_checked_when_equipping_not_when_held() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    let item = shipped()
        .catalog
        .items
        .iter()
        .find(|i| i.slot.is_some() && i.required_level >= 2 && !i.retired)
        .expect("some clothing needs a level");
    let mut tx = app.state.pool.begin().await.unwrap();
    let new = NewItem::plain(p.id, item.id, 1, "inventory");
    let id = items::give(&mut tx, &new, Reason::Admin, None)
        .await
        .unwrap()[0];
    tx.commit().await.unwrap();
    let slot = item.slot.unwrap().id();
    let wear = json!({ "slot": slot, "item_id": id });
    let res = app.post("/api/equipment", wear.clone(), p.token()).await;
    assert_eq!(res.status, StatusCode::FORBIDDEN, "{:?}", res.body);
    assert_eq!(res.body["error"], "level_too_low");
    // At the level it needs, it goes on.
    let need = shipped().progression.level_start(item.required_level);
    grant(&app, &p, XpSource::Admin, None, need, 8).await;
    let res = app.post("/api/equipment", wear.clone(), p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    // Worn items stay worn (and may be put on again) whatever the level.
    sqlx::query("UPDATE characters SET level = 1 WHERE id = $1")
        .bind(p.id)
        .execute(&app.state.pool)
        .await
        .unwrap();
    let res = app.post("/api/equipment", wear, p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
}

#[tokio::test]
async fn finds_and_crafts_give_player_xp() {
    let app = TestApp::with_config(|c| c.stations_anywhere = true).await;
    let p = app.player("Burrowsby").await;
    let spot = &shipped().finds.spots[0].id;
    let drops = Drops {
        items: vec![(3001, 1)],
        crowns: 0,
    };
    let found = server::finds::grant(&app.state.pool, p.id, spot, &drops)
        .await
        .unwrap();
    let find_xp = shipped().progression.xp.find;
    assert_eq!(found.xp.map(|g| g.granted), Some(find_xp));
    // Knit a Cable Beanie (known by everyone): wool x3.
    let mut tx = app.state.pool.begin().await.unwrap();
    let wool = NewItem::plain(p.id, 3001, 5, "inventory");
    items::give(&mut tx, &wool, Reason::Admin, None)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    let basic = &shipped().palette.basic;
    let res = app
        .post(
            "/api/craft",
            json!({ "recipe_id": 6, "dyes": [basic[0], basic[1], basic[2]] }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let recipe_xp = shipped().recipes.get(6).unwrap().xp;
    let craft_xp = shipped().progression.craft_xp(recipe_xp);
    assert_eq!(xp_of(&app, p.id).await.0 as u32, find_xp + craft_xp);
}

#[tokio::test]
async fn the_dev_grant_gives_xp_and_spells_and_the_clock_needs_the_world() {
    let app = TestApp::dev().await;
    let p = app.player("Roarwing").await;
    let res = app
        .post(
            "/api/dev/grant",
            json!({ "xp": 1_000, "spells": [10, 11] }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body["xp"], 1_000);
    assert_eq!(res.body["level"], shipped().progression.level_for(1_000));
    let school = progression::load_school(&app.state.pool, p.id)
        .await
        .unwrap();
    assert_eq!(school.learned, vec![10, 11]);
    let res = app
        .post("/api/dev/grant", json!({ "spells": [999] }), p.token())
        .await;
    assert_eq!(res.body["error"], "invalid_grant");
    let res = app
        .post("/api/dev/clock", json!({ "skew_ms": 60_000 }), p.token())
        .await;
    assert_eq!(res.status, StatusCode::CONFLICT, "{:?}", res.body);
    assert_eq!(res.body["error"], "no_world");
    // The feature routes are built (their own suites test them:
    // `tests/{progression,classes,spells,dueling}.rs`).
    for path in [
        "/api/progression",
        "/api/classes/schedule",
        "/api/spells",
        "/api/duels",
    ] {
        assert_eq!(
            app.get(path, p.token()).await.status,
            StatusCode::OK,
            "{path}"
        );
    }
}
