//! The House Cup (docs/architecture.md): the new point
//! sources reach the ledger, `GET /api/house-cup`'s shape and the caller's
//! points, the first-day tour's step (contracts); each source awards once
//! under its daily cap, the weekly close (idempotent, safe across a crash
//! before its commit, ties, the reward mail and its pennant in house
//! colours), the audit, the dev close, and the weekly sum's plan on a
//! million rows.
//!
//! Tests share the database, so every close uses a cup week of its own far
//! in the future (the minute job only ever closes last week, and the route
//! only shows this week and the weeks before it).

mod common;

use axum::http::StatusCode;
use chrono::{DateTime, Utc};
use common::{TestApp, TestPlayer};
use protocol::House;
use rand::Rng;
use serde_json::json;
use server::catalog::shipped;
use server::classes::{self, Admission, AdmitRequest, RewardOutcome, RewardRequest};
use server::house_cup::{self, Closed};
use server::house_points::{self, PointsSource};
use server::items::{self, NewItem};
use server::ledger::Reason;
use sim_core::Drops;
use sim_core::house_cup::{CupWeek, WEEK_MS};
use sqlx::PgPool;

#[tokio::test]
async fn find_and_craft_points_are_recorded_and_the_cup_shows_this_week() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Burrowsby").await;

    let mut tx = pool.begin().await.unwrap();
    for (points, source, ref_id) in [
        (1, PointsSource::Find, None),
        (1, PointsSource::Craft, Some(-p.id)),
        (3, PointsSource::Admin, None),
    ] {
        let wrote = house_points::award(
            &mut tx,
            House::Burrowsby,
            points,
            Some(p.id),
            source,
            ref_id,
        )
        .await
        .unwrap();
        assert!(wrote, "{source:?}");
    }
    // A craft's points are once per craft.
    let again = house_points::award(
        &mut tx,
        House::Burrowsby,
        1,
        Some(p.id),
        PointsSource::Craft,
        Some(-p.id),
    )
    .await
    .unwrap();
    assert!(!again);
    tx.commit().await.unwrap();

    let res = app.get("/api/house-cup", Some(&p.token)).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let b = &res.body;
    let now = Utc::now().timestamp_millis();
    let week = shipped().house_cup.week_of(now);
    assert_eq!(b["week"]["start_ms"].as_i64(), Some(week.start_ms));
    assert_eq!(b["week"]["end_ms"].as_i64(), Some(week.end_ms));
    let resets = b["resets_in_secs"].as_i64().unwrap();
    assert!(resets > 0 && resets <= 7 * 86_400, "{resets}");
    let houses: Vec<&str> = b["houses"]
        .as_array()
        .unwrap()
        .iter()
        .map(|h| h["house"].as_str().unwrap())
        .collect();
    assert_eq!(houses, ["Roarwing", "Burrowsby", "Skytalon", "Serpentyne"]);
    assert_eq!(b["mine"].as_i64(), Some(5), "{b:?}");
    assert!(b["badge"].is_array());
    assert!(b["last"].is_null() || b["last"]["winners"].is_array());

    // Signed out: no cup.
    let res = app.get("/api/house-cup", None).await;
    assert_eq!(res.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn the_tour_step_only_rises() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    let tok = Some(p.token.as_str());
    let path = "/api/characters/me/tour";

    let res = app.get(path, tok).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert_eq!(res.body, json!({ "step": 0 }));
    let res = app.post(path, json!({ "step": 1 }), tok).await;
    assert_eq!(res.body, json!({ "step": 1 }), "{:?}", res.body);
    let res = app.post(path, json!({ "step": 0 }), tok).await;
    assert_eq!(res.body, json!({ "step": 1 }));
    let res = app.post(path, json!({ "step": 3 }), tok).await;
    assert_eq!(res.status, StatusCode::BAD_REQUEST);
    assert_eq!(res.body["error"], "invalid_step");
    let res = app.post(path, json!({ "step": 2 }), tok).await;
    assert_eq!(res.body, json!({ "step": 2 }));
    assert_eq!(app.get(path, tok).await.body, json!({ "step": 2 }));
}

// ------------------------------------------------------------ helpers

/// A cup week of this test's own, far in the future (nothing else writes
/// points there, and nothing else closes it).
fn own_week() -> CupWeek {
    let k: i64 = rand::thread_rng().gen_range(0..1_000_000);
    shipped().house_cup.week_of(4_200_000_000_000 + k * WEEK_MS)
}

fn time(ms: i64) -> DateTime<Utc> {
    DateTime::<Utc>::from_timestamp_millis(ms).unwrap()
}

/// Records `points` for `p` (of `house`) at `at_ms` (source `admin`, which
/// the audit does not trace: the test stands in for classes and duels).
async fn points_at(pool: &PgPool, p: &TestPlayer, house: House, points: i32, at_ms: i64) {
    sqlx::query(
        "INSERT INTO house_points (at, house, points, character_id, source)
         VALUES ($1, $2, $3, $4, 'admin')",
    )
    .bind(time(at_ms))
    .bind(house.id())
    .bind(points)
    .bind(p.id)
    .execute(pool)
    .await
    .unwrap();
}

/// A character's points from `source` over all time.
async fn points_of(pool: &PgPool, id: i64, source: &str) -> i64 {
    sqlx::query_scalar(
        "SELECT COALESCE(SUM(points), 0)::bigint FROM house_points
         WHERE character_id = $1 AND source = $2",
    )
    .bind(id)
    .bind(source)
    .fetch_one(pool)
    .await
    .unwrap()
}

/// The character's House Cup reward mails: (mail id, ref_id, collected).
async fn reward_mails(pool: &PgPool, id: i64) -> Vec<(i64, Option<i64>, bool)> {
    sqlx::query_as(
        "SELECT id, ref_id, collected_at IS NOT NULL FROM mail
         WHERE recipient_id = $1 AND kind = 'house_cup' ORDER BY id",
    )
    .bind(id)
    .fetch_all(pool)
    .await
    .unwrap()
}

/// The items attached to mail `mail_id`: (item_def, dyes, quantity).
async fn attached(pool: &PgPool, mail_id: i64) -> Vec<(i32, [i16; 3], i32)> {
    let rows: Vec<(i32, i16, i16, i16, i32)> = sqlx::query_as(
        "SELECT i.item_def, i.dye_primary, i.dye_secondary, i.dye_trim, i.quantity
         FROM mail_attachments a JOIN items i ON i.id = a.item_id
         WHERE a.mail_id = $1 ORDER BY i.id",
    )
    .bind(mail_id)
    .fetch_all(pool)
    .await
    .unwrap();
    rows.into_iter()
        .map(|(d, a, b, c, q)| (d, [a, b, c], q))
        .collect()
}

/// The reward pennant's dyes for a member of `house`.
fn pennant_dyes(house: House) -> [i16; 3] {
    let c = shipped();
    let def = c.catalog.get(c.house_cup.reward.item).unwrap();
    def.granted_dyes(house, &c.palette).map(i16::from)
}

async fn audit_clean(pool: &PgPool, ids: &[i64]) {
    server::ledger::audit::reconcile(pool, ids)
        .await
        .unwrap()
        .assert_clean();
}

async fn give(pool: &PgPool, owner: i64, item_def: u16, quantity: u32) {
    let mut tx = pool.begin().await.unwrap();
    let item = NewItem::plain(owner, item_def, quantity, "inventory");
    items::give(&mut tx, &item, Reason::Admin, None)
        .await
        .unwrap();
    tx.commit().await.unwrap();
}

// ------------------------------------------------------------ sources

#[tokio::test]
async fn finds_give_one_point_each_up_to_the_daily_cap() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Roarwing").await;
    let rules = &shipped().house_cup;
    let cap = i64::from(rules.sources.find.daily_cap);
    let per_find = i64::from(rules.find_points(true));
    assert_eq!((per_find, cap), (1, 5), "one point a find, five a day");
    let spots = &shipped().finds.spots;
    let something = Drops {
        items: vec![(3001, 1)],
        crowns: 0,
    };
    // A find that turns up nothing gives no points.
    server::finds::grant(pool, p.id, &spots[0].id, &Drops::default())
        .await
        .unwrap();
    assert_eq!(points_of(pool, p.id, "find").await, 0);
    // One point a find that turned something up (Crowns count too).
    let coins = Drops {
        items: Vec::new(),
        crowns: 3,
    };
    server::finds::grant(pool, p.id, &spots[1].id, &coins)
        .await
        .unwrap();
    assert_eq!(points_of(pool, p.id, "find").await, 1);
    // A spot on cooldown pays out nothing, points included.
    server::finds::grant(pool, p.id, &spots[1].id, &something)
        .await
        .unwrap();
    assert_eq!(points_of(pool, p.id, "find").await, 1);
    // Up to the cap, then no more today.
    for spot in &spots[2..9] {
        server::finds::grant(pool, p.id, &spot.id, &something)
            .await
            .unwrap();
    }
    assert_eq!(points_of(pool, p.id, "find").await, cap);
    let rows: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM house_points WHERE character_id = $1 AND source = 'find'",
    )
    .bind(p.id)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(rows, cap, "one row a find");
    let used: i32 = sqlx::query_scalar(
        "SELECT used FROM daily_caps WHERE character_id = $1 AND cap = 'hp_find'",
    )
    .bind(p.id)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(i64::from(used), cap);
    audit_clean(pool, &[p.id]).await;
}

#[tokio::test]
async fn crafts_of_skill_two_or_more_give_points_up_to_the_daily_cap() {
    let app = TestApp::with_config(|c| c.stations_anywhere = true).await;
    let pool = &app.state.pool;
    let p = app.player("Serpentyne").await;
    let rules = &shipped().house_cup;
    let cap = i64::from(rules.sources.craft.daily_cap);
    // Felt a Cloche (recipe 4, skill 2: wool x3, silk x1) and Knit a Cable
    // Beanie (recipe 6, skill 1: wool x3).
    assert_eq!(shipped().recipes.get(4).unwrap().skill, 2);
    assert_eq!(shipped().recipes.get(6).unwrap().skill, 1);
    sqlx::query("INSERT INTO known_recipes (character_id, recipe_id) VALUES ($1, 4)")
        .bind(p.id)
        .execute(pool)
        .await
        .unwrap();
    let level_2 = shipped().recipes.skill_levels[0];
    sqlx::query("INSERT INTO skills (character_id, discipline, xp) VALUES ($1, 'charms', $2)")
        .bind(p.id)
        .bind(level_2 as i32)
        .execute(pool)
        .await
        .unwrap();
    give(pool, p.id, 3001, 3 * 8).await;
    give(pool, p.id, 3003, 7).await;
    let rested = || async {
        sqlx::query("UPDATE characters SET craft_ready_at = NULL WHERE id = $1")
            .bind(p.id)
            .execute(pool)
            .await
            .unwrap();
    };
    // A skill 1 craft gives none.
    let res = app
        .post("/api/craft", json!({ "recipe_id": 6 }), p.token())
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(points_of(pool, p.id, "craft").await, 0);
    for k in 1..=7i64 {
        rested().await;
        let res = app
            .post("/api/craft", json!({ "recipe_id": 4 }), p.token())
            .await;
        assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
        assert_eq!(
            points_of(pool, p.id, "craft").await,
            k.min(cap),
            "craft {k}"
        );
    }
    // Each point names its craft.
    let unnamed: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM house_points h WHERE h.character_id = $1 AND h.source = 'craft'
           AND NOT EXISTS (SELECT 1 FROM crafts c WHERE c.id = h.ref_id
                             AND c.character_id = h.character_id AND c.recipe_id = 4)",
    )
    .bind(p.id)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(unnamed, 0);
    audit_clean(pool, &[p.id]).await;
}

#[tokio::test]
async fn class_points_stop_at_the_daily_cap_and_the_attendance_records_what_was_given() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Skytalon").await;
    let now = Utc::now();
    let cap = i32::from(shipped().house_cup.sources.class.daily_cap);
    let earned = shipped()
        .class("potions")
        .unwrap()
        .rewards
        .house_points(100);
    assert!(earned > 2);
    sqlx::query(
        "INSERT INTO daily_caps (character_id, day, cap, used) VALUES ($1, $2, 'hp_class', $3)",
    )
    .bind(p.id)
    .bind(now.date_naive())
    .bind(cap - 2)
    .execute(pool)
    .await
    .unwrap();
    let base: i64 = rand::thread_rng().gen_range(0..1_000_000);
    let mut given = Vec::new();
    for k in 0..2 {
        let schedule = &shipped().schedule;
        let session = schedule.next(
            schedule.class("potions").unwrap(),
            4_300_000_000_000 + (base * 4 + k) * 7_200_000,
        );
        let req = AdmitRequest {
            class_id: "potions".into(),
            session,
            section: 0,
            character_id: p.id,
        };
        let Admission::Admitted { attendance_id } = classes::admit(pool, &req).await.unwrap()
        else {
            panic!("admitted");
        };
        let req = RewardRequest {
            attendance_id,
            character_id: p.id,
            class_id: "potions".into(),
            score_pct: 100,
            answers: 20,
            section_pct: 100,
        };
        let RewardOutcome::Granted(r) = classes::reward(pool, &req, now).await.unwrap() else {
            panic!("granted");
        };
        given.push(r.rewards.house_points);
        let recorded: i32 =
            sqlx::query_scalar("SELECT house_points FROM class_attendance WHERE id = $1")
                .bind(attendance_id)
                .fetch_one(pool)
                .await
                .unwrap();
        assert_eq!(recorded, i32::from(r.rewards.house_points));
    }
    assert_eq!(given, [2, 0], "what was left of the cap, then nothing");
    assert_eq!(points_of(pool, p.id, "class").await, 2);
    audit_clean(pool, &[p.id]).await;
}

// ------------------------------------------------------------ the close

#[tokio::test]
async fn the_close_mails_the_winners_once_even_after_a_crash_before_its_commit() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let week = own_week();
    let mid = week.start_ms + WEEK_MS / 2;
    let g1 = app.player("Roarwing").await;
    let g2 = app.player("Roarwing").await;
    let h1 = app.player("Burrowsby").await;
    // Roarwing 12 + 9 = 21 beats Burrowsby's 15; only g1 has the 10.
    points_at(pool, &g1, House::Roarwing, 7, week.start_ms).await;
    points_at(pool, &g1, House::Roarwing, 5, mid).await;
    points_at(pool, &g2, House::Roarwing, 9, mid).await;
    points_at(pool, &h1, House::Burrowsby, 15, week.end_ms - 1).await;
    // Outside the week: not counted.
    points_at(pool, &g2, House::Roarwing, 50, week.end_ms).await;
    points_at(pool, &h1, House::Burrowsby, 50, week.start_ms - 1).await;

    // A crash before the commit: the work is lost, nothing is kept.
    let mut tx = pool.begin().await.unwrap();
    let lost = house_cup::close_in(&mut tx, week).await.unwrap().unwrap();
    assert_eq!(lost.mailed, [g1.id]);
    drop(tx);
    let rows: i64 = sqlx::query_scalar("SELECT count(*) FROM house_cups WHERE week_start = $1")
        .bind(time(week.start_ms))
        .fetch_one(pool)
        .await
        .unwrap();
    assert_eq!(rows, 0);
    assert!(reward_mails(pool, g1.id).await.is_empty());

    // The next run closes it.
    let Closed {
        id,
        week: closed_week,
        winners,
        houses,
        mailed,
    } = house_cup::close_week(pool, week).await.unwrap().unwrap();
    assert_eq!(closed_week, week);
    assert_eq!(winners, [House::Roarwing]);
    let totals: Vec<(House, i64)> = houses.iter().map(|h| (h.house, h.points)).collect();
    assert_eq!(
        totals,
        [
            (House::Roarwing, 21),
            (House::Burrowsby, 15),
            (House::Skytalon, 0),
            (House::Serpentyne, 0)
        ]
    );
    assert_eq!(mailed, [g1.id]);
    // Once: a second run, or the minute job after the week, does nothing.
    assert_eq!(house_cup::close_week(pool, week).await.unwrap(), None);
    assert_eq!(
        house_cup::close_due(pool, week.next().start_ms + 60_000)
            .await
            .unwrap(),
        None
    );
    let (winners_row, totals_row): (Vec<String>, serde_json::Value) =
        sqlx::query_as("SELECT winners, totals FROM house_cups WHERE id = $1")
            .bind(id)
            .fetch_one(pool)
            .await
            .unwrap();
    assert_eq!(winners_row, ["roarwing"]);
    assert_eq!(
        totals_row,
        json!({ "roarwing": 21, "burrowsby": 15, "skytalon": 0, "serpentyne": 0 })
    );

    // The reward: one mail naming the cup, the pennant in house colours.
    let mails = reward_mails(pool, g1.id).await;
    assert_eq!(mails.len(), 1);
    let (mail_id, ref_id, collected) = mails[0];
    assert_eq!((ref_id, collected), (Some(id), false));
    let item = i32::from(shipped().house_cup.reward.item);
    assert_eq!(
        attached(pool, mail_id).await,
        [(item, pennant_dyes(House::Roarwing), 1)]
    );
    let (sender, subject): (String, String) =
        sqlx::query_as("SELECT sender_name, subject FROM mail WHERE id = $1")
            .bind(mail_id)
            .fetch_one(pool)
            .await
            .unwrap();
    let (want_sender, want_subject, _) = shipped().house_cup.reward_mail(House::Roarwing);
    assert_eq!((sender, subject), (want_sender, want_subject));
    assert!(
        reward_mails(pool, g2.id).await.is_empty(),
        "under 10 points"
    );
    assert!(reward_mails(pool, h1.id).await.is_empty(), "not a winner");
    let ledger: Vec<(String, String, Option<i64>)> = sqlx::query_as(
        "SELECT op, reason, ref_id FROM item_ledger WHERE to_owner = $1 AND reason = 'house_cup'",
    )
    .bind(g1.id)
    .fetch_all(pool)
    .await
    .unwrap();
    assert_eq!(ledger, [("create".into(), "house_cup".into(), Some(id))]);

    // Collected into the inventory like any mail; the audit stays clean.
    let res = app
        .post(
            &format!("/api/mail/{mail_id}/collect"),
            json!({}),
            g1.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let held: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM items WHERE owner_id = $1 AND item_def = $2
           AND location = 'inventory'",
    )
    .bind(g1.id)
    .bind(item)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(held, 1);
    audit_clean(pool, &[g1.id, g2.id, h1.id]).await;
}

#[tokio::test]
async fn a_tie_mails_every_tied_house_and_an_empty_week_has_no_winner() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let week = own_week();
    let r = app.player("Skytalon").await;
    let s = app.player("Serpentyne").await;
    let g = app.player("Roarwing").await;
    points_at(pool, &r, House::Skytalon, 10, week.start_ms + 1).await;
    points_at(pool, &s, House::Serpentyne, 10, week.start_ms + 2).await;
    points_at(pool, &g, House::Roarwing, 9, week.start_ms + 3).await;
    let closed = house_cup::close_week(pool, week).await.unwrap().unwrap();
    assert_eq!(closed.winners, [House::Skytalon, House::Serpentyne]);
    assert_eq!(closed.mailed, [r.id, s.id]);
    let item = i32::from(shipped().house_cup.reward.item);
    for (p, house) in [(&r, House::Skytalon), (&s, House::Serpentyne)] {
        let mails = reward_mails(pool, p.id).await;
        assert_eq!(mails.len(), 1);
        assert_eq!(mails[0].1, Some(closed.id));
        assert_eq!(
            attached(pool, mails[0].0).await,
            [(item, pennant_dyes(house), 1)]
        );
    }
    assert_ne!(
        pennant_dyes(House::Skytalon),
        pennant_dyes(House::Serpentyne),
        "each in its own house's colours"
    );
    audit_clean(pool, &[r.id, s.id, g.id]).await;

    // Nobody scored: closed with no winner and no mail.
    let empty = own_week();
    let closed = house_cup::close_week(pool, empty).await.unwrap().unwrap();
    assert!(closed.winners.is_empty());
    assert!(closed.mailed.is_empty());
    assert!(closed.houses.iter().all(|h| h.points == 0));
}

#[tokio::test]
async fn the_audit_flags_reward_mails_and_points_that_nothing_explains() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let week = own_week();
    let w = app.player("Burrowsby").await;
    let loser = app.player("Roarwing").await;
    let few = app.player("Burrowsby").await;
    points_at(pool, &w, House::Burrowsby, 12, week.start_ms).await;
    points_at(pool, &loser, House::Roarwing, 11, week.start_ms).await;
    points_at(pool, &few, House::Burrowsby, 3, week.start_ms).await;
    let closed = house_cup::close_week(pool, week).await.unwrap().unwrap();
    assert_eq!(closed.mailed, [w.id]);
    audit_clean(pool, &[w.id, loser.id, few.id]).await;

    // A reward mail to a character whose house did not win, one to a winner
    // with too few points that week, a find point with no find and a craft
    // point with no craft: all flagged.
    let mut tx = pool.begin().await.unwrap();
    for (recipient, subject) in [(loser.id, "Not yours"), (few.id, "Not earned")] {
        server::mail::create(
            &mut tx,
            &server::mail::NewMail {
                recipient,
                sender_id: None,
                sender_name: "Nobody",
                kind: house_cup::MAIL_KIND,
                subject,
                body: "",
                crowns: 0,
                ref_id: Some(closed.id),
                client_key: None,
                with_items: false,
            },
        )
        .await
        .unwrap();
    }
    house_points::award(
        &mut tx,
        House::Roarwing,
        1,
        Some(loser.id),
        PointsSource::Find,
        None,
    )
    .await
    .unwrap();
    house_points::award(
        &mut tx,
        House::Roarwing,
        1,
        Some(loser.id),
        PointsSource::Craft,
        Some(-loser.id),
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let report = server::ledger::audit::reconcile(pool, &[w.id, loser.id, few.id])
        .await
        .unwrap();
    let checks: Vec<&str> = report.problems.iter().map(|p| p.check).collect();
    assert_eq!(
        checks,
        ["house_points", "house_points", "house_cup", "house_cup"],
        "{:?}",
        report.problems
    );
}

#[tokio::test]
async fn the_dev_close_closes_the_running_week_and_the_badge_follows() {
    let app = TestApp::dev().await;
    let pool = &app.state.pool;
    let p = app.player("Serpentyne").await;
    let tok = p.token();

    // A week of its own: the full answer, then nothing the second time.
    let week = own_week();
    points_at(pool, &p, House::Serpentyne, 10, week.start_ms + 1).await;
    let path = format!("/api/dev/house-cup/close?at_unix_ms={}", week.start_ms + 5);
    let res = app.post(&path, json!({}), tok).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let b = &res.body;
    assert_eq!(b["closed"], true);
    assert_eq!(
        b["week"],
        json!({ "start_ms": week.start_ms, "end_ms": week.end_ms })
    );
    assert_eq!(b["winners"], json!(["Serpentyne"]));
    assert_eq!(b["houses"].as_array().unwrap().len(), 4);
    assert_eq!(
        b["houses"][3],
        json!({ "house": "Serpentyne", "points": 10 })
    );
    assert_eq!(b["mailed"], 1);
    let res = app.post(&path, json!({}), tok).await;
    assert_eq!(res.body, json!({ "closed": false }));
    assert_eq!(reward_mails(pool, p.id).await.len(), 1);

    // The running week (an earlier run on this database may have closed it
    // already): afterwards the cup shows it as the last week, and the badge
    // is its winners.
    let res = app.post("/api/dev/house-cup/close", json!({}), tok).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let now = shipped().house_cup.week_of(Utc::now().timestamp_millis());
    if res.body["closed"] == true {
        assert_eq!(res.body["week"]["start_ms"].as_i64(), Some(now.start_ms));
    }
    let cup = app.get("/api/house-cup", tok).await.body;
    assert_eq!(cup["last"]["week"]["start_ms"].as_i64(), Some(now.start_ms));
    assert_eq!(cup["badge"], cup["last"]["winners"]);
    let res = app.post("/api/dev/house-cup/close", json!({}), tok).await;
    assert_eq!(res.body, json!({ "closed": false }));

    // Signed in only; and not there at all without dev mode.
    let res = app.post("/api/dev/house-cup/close", json!({}), None).await;
    assert_eq!(res.status, StatusCode::UNAUTHORIZED);
    let plain = TestApp::new().await;
    let q = plain.player("Burrowsby").await;
    let res = plain
        .post("/api/dev/house-cup/close", json!({}), q.token())
        .await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    audit_clean(pool, &[p.id]).await;
}

/// The week's sum (`house_cup::week_totals`, the close's standings) on a
/// million rows reads an index on `at`, not the whole table: one million
/// rows over 200 weeks, inside a transaction that is rolled back (the
/// shared database keeps none of them, nor their statistics).
#[tokio::test]
async fn the_weekly_sum_reads_an_index_on_a_million_rows() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let first = own_week();
    let mut tx = pool.begin().await.unwrap();
    sqlx::query(
        "INSERT INTO house_points (at, house, points, character_id, source)
         SELECT $1 + (n % 200) * interval '7 days' + (n % 600000) * interval '1 second',
                (ARRAY['roarwing', 'burrowsby', 'skytalon', 'serpentyne'])[n % 4 + 1],
                1 + n % 5, NULL, 'admin'
         FROM generate_series(1, 1000000) AS n",
    )
    .bind(time(first.start_ms))
    .execute(&mut *tx)
    .await
    .unwrap();
    sqlx::query("ANALYZE house_points")
        .execute(&mut *tx)
        .await
        .unwrap();
    let week = CupWeek {
        start_ms: first.start_ms + 100 * WEEK_MS,
        end_ms: first.start_ms + 101 * WEEK_MS,
    };
    let plan: Vec<String> = sqlx::query_scalar(
        "EXPLAIN (ANALYZE, BUFFERS) SELECT house, COALESCE(SUM(points), 0)::bigint
         FROM house_points WHERE at >= $1 AND at < $2 GROUP BY house",
    )
    .bind(time(week.start_ms))
    .bind(time(week.end_ms))
    .fetch_all(&mut *tx)
    .await
    .unwrap();
    let plan = plan.join("\n");
    println!("{plan}");
    assert!(!plan.contains("Seq Scan on house_points"), "{plan}");
    assert!(plan.contains("Index"), "{plan}");
    tx.rollback().await.unwrap();
}
