//! Rewards no shipped class gives yet (History of Magic and
//! Divination): lore entries with a hint, and fortunes, through
//! `classes::reward_class` with a test class (the Potions class with
//! `lore` and `fortune` rules) and test lore; the lore book's page; and the
//! fortunes' hooks over HTTP (Keen Mind in the next class's bonus, Quick
//! Hands in a craft's busy time).

mod common;

use axum::http::StatusCode;
use chrono::Utc;
use common::{TestApp, TestPlayer};
use protocol::{EffectKind, FortuneKind};
use serde_json::json;
use server::catalog::shipped;
use server::characters::PageQuery;
use server::classes::{self, Admission, AdmitRequest, RewardOutcome, RewardRequest, lore};
use server::items::{self, NewItem};
use server::ledger::Reason;
use sim_core::classes::FortuneRule;
use sim_core::{ClassDef, Lore, SessionTimes};
use sqlx::PgPool;

const LORE: &str = r#"{
    "version": 1,
    "entries": [
        { "id": 1, "title": "The Lantern Stair", "text": "A test entry about a stair that hums." },
        { "id": 2, "title": "The Quiet Bell", "text": "A test entry about a bell nobody rings." }
    ]
}"#;

/// The Potions class, plus a lore entry and a hint and a Keen Mind fortune
/// for any score.
fn test_class() -> ClassDef {
    let mut c = shipped().class("potions").unwrap().clone();
    c.rewards.lore = true;
    c.rewards.fortune = vec![FortuneRule {
        kind: FortuneKind::KeenMind,
        min_pct: 0,
    }];
    c
}

fn session(k: i64) -> SessionTimes {
    let schedule = &shipped().schedule;
    let potions = schedule.class("potions").unwrap();
    schedule.next(potions, 4_200_000_000_000 + k * 7_200_000)
}

async fn admit(pool: &PgPool, p: &TestPlayer, k: i64) -> i64 {
    let req = AdmitRequest {
        class_id: "potions".into(),
        session: session(k),
        section: 0,
        character_id: p.id,
    };
    match classes::admit(pool, &req).await.unwrap() {
        Admission::Admitted { attendance_id } => attendance_id,
        Admission::AlreadyAttended => panic!("barred"),
    }
}

fn request(p: &TestPlayer, attendance_id: i64, score_pct: u8) -> RewardRequest {
    RewardRequest {
        attendance_id,
        character_id: p.id,
        class_id: "potions".into(),
        score_pct,
        answers: 10,
        section_pct: score_pct,
    }
}

#[tokio::test]
async fn lore_hints_and_fortunes_are_granted_once_and_keen_mind_boosts_the_next_class() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Skytalon").await;
    let class = test_class();
    let lore = Lore::from_json(LORE).unwrap();
    // A spot searched today is never the hint.
    let spots = &shipped().finds.spots;
    for s in spots.iter().skip(1) {
        sqlx::query(
            "INSERT INTO find_cooldowns (character_id, spot_id, ready_at)
             VALUES ($1, $2, now() + interval '1 hour')",
        )
        .bind(p.id)
        .bind(&s.id)
        .execute(pool)
        .await
        .unwrap();
    }
    let first = admit(pool, &p, 1).await;
    let RewardOutcome::Granted(r) =
        classes::reward_class(pool, &class, &lore, &request(&p, first, 50), Utc::now())
            .await
            .unwrap()
    else {
        panic!("granted");
    };
    assert_eq!(r.rewards.lore, Some(1));
    assert_eq!(r.rewards.fortune, Some(FortuneKind::KeenMind));
    let (only, gate) = lore::spot_place(&spots[0].id).unwrap();
    if gate.is_none_or(|g| g == "skytalon") {
        assert_eq!(r.rewards.hint.as_deref(), Some(only));
    } else {
        assert_eq!(r.rewards.hint, None, "another house's room is never hinted");
    }
    // Before the fortune, the bonus was plain.
    let (attend, bonus) = classes::class_xp(50);
    assert_eq!(r.rewards.xp, attend + bonus);
    // A retry answers the lore and the fortune, and grants nothing more.
    let RewardOutcome::AlreadyRewarded(again) =
        classes::reward_class(pool, &class, &lore, &request(&p, first, 50), Utc::now())
            .await
            .unwrap()
    else {
        panic!("a retry");
    };
    assert_eq!(
        (again.lore, again.fortune, again.crowns),
        (Some(1), Some(FortuneKind::KeenMind), r.rewards.crowns)
    );
    // The fortune is a stored effect that survives a relog.
    let effects = server::effects::load_active(pool, p.id).await.unwrap();
    assert_eq!(effects.len(), 1);
    assert_eq!(effects[0].kind, EffectKind::Fortune);
    assert_eq!(effects[0].fortune, Some(FortuneKind::KeenMind));
    assert_eq!(effects[0].item_def, 0);
    assert_eq!(effects[0].visible, None);
    let listed = app.get("/api/effects", p.token()).await;
    assert_eq!(listed.body["effects"][0]["kind"], "fortune");
    assert_eq!(listed.body["effects"][0]["fortune"], "keen_mind");

    // The next class: Keen Mind adds its share to the bonus; the next entry.
    let second = admit(pool, &p, 2).await;
    let RewardOutcome::Granted(r2) =
        classes::reward_class(pool, &class, &lore, &request(&p, second, 50), Utc::now())
            .await
            .unwrap()
    else {
        panic!("granted");
    };
    assert_eq!(r2.rewards.xp, attend + classes::keen_mind_bonus(bonus));
    assert!(classes::keen_mind_bonus(bonus) > bonus);
    assert_eq!(r2.rewards.lore, Some(2));
    // Every entry known: nothing more to unlock (the hint still comes).
    let third = admit(pool, &p, 3).await;
    let RewardOutcome::Granted(r3) =
        classes::reward_class(pool, &class, &lore, &request(&p, third, 50), Utc::now())
            .await
            .unwrap()
    else {
        panic!("granted");
    };
    assert_eq!(r3.rewards.lore, None);

    // The lore book: newest first, in pages.
    let page = |limit: u32, after: Option<String>| PageQuery {
        limit: Some(limit),
        after,
    };
    let one = lore::lore_page(pool, &lore, p.id, &page(1, None))
        .await
        .unwrap();
    assert_eq!(one.items.len(), 1);
    assert_eq!(one.items[0].id, 2);
    assert_eq!(one.items[0].title, "The Quiet Bell");
    let two = lore::lore_page(pool, &lore, p.id, &page(1, one.next.clone()))
        .await
        .unwrap();
    assert_eq!(two.items[0].id, 1);
    assert!(two.next.is_none());
    // The route serves the shipped entries the character unlocked, signed in only.
    let res = app.get("/api/lore", p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    assert!(res.body["items"].is_array());
    assert_eq!(
        app.get("/api/lore", None).await.status,
        StatusCode::UNAUTHORIZED
    );
    let bad = app.get("/api/lore?after=zzz", p.token()).await;
    assert_eq!(bad.body["error"], "invalid_cursor");

    server::ledger::audit::reconcile(pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn the_audit_flags_a_lore_unlock_or_class_crowns_nothing_explains() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Roarwing").await;
    let q = app.player("Roarwing").await;
    let class = test_class();
    let lore = Lore::from_json(LORE).unwrap();
    let id = admit(pool, &p, 4).await;
    classes::reward_class(pool, &class, &lore, &request(&p, id, 80), Utc::now())
        .await
        .unwrap();
    server::ledger::audit::reconcile(pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
    // Someone else claims Crowns for that class.
    let mut tx = pool.begin().await.unwrap();
    server::ledger::adjust_crowns(&mut tx, q.id, 5, Reason::Class, Some(id))
        .await
        .unwrap();
    let item = NewItem::plain(q.id, 3021, 1, "inventory");
    items::give(&mut tx, &item, Reason::Class, Some(id))
        .await
        .unwrap();
    tx.commit().await.unwrap();
    let report = server::ledger::audit::reconcile(pool, &[q.id])
        .await
        .unwrap();
    assert!(
        report.problems.iter().any(|p| p.check == "crowns"),
        "{:?}",
        report.problems
    );
    assert!(
        report.problems.iter().any(|p| p.check == "class"),
        "{:?}",
        report.problems
    );
}

#[tokio::test]
async fn quick_hands_cuts_a_crafts_busy_time() {
    let app = TestApp::with_config(|c| c.stations_anywhere = true).await;
    let pool = &app.state.pool;
    let p = app.player("Burrowsby").await;
    // Wool for a Cable Beanie (recipe 6, known by everyone).
    let mut tx = pool.begin().await.unwrap();
    items::give(
        &mut tx,
        &NewItem::plain(p.id, 3001, 3, "inventory"),
        Reason::Admin,
        None,
    )
    .await
    .unwrap();
    let duration = server::effects::fortune_duration();
    let effect = server::effects::fortune_effect(
        FortuneKind::QuickHands,
        std::time::Instant::now() + duration,
    );
    server::effects::store(&mut tx, p.id, &effect, duration)
        .await
        .unwrap();
    tx.commit().await.unwrap();
    let before = Utc::now();
    let res = app
        .post("/api/craft", json!({ "recipe_id": 6 }), p.token())
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let ready: chrono::DateTime<Utc> = res.body["ready_at"].as_str().unwrap().parse().unwrap();
    let secs = shipped().recipes.get(6).unwrap().craft_secs;
    let cut = shipped().economy.effects.fortune.quick_hands_pct;
    let expected_ms = i64::from(secs) * 1_000 * i64::from(100 - cut) / 100;
    let took = (ready - before).num_milliseconds();
    assert!(
        (expected_ms - 300..=expected_ms + 1_500).contains(&took),
        "{took} ms, expected about {expected_ms}"
    );
}
