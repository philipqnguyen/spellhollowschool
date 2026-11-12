//! The duplication hunt at scale: a few players hammer the economy at once
//! for several rounds, every kind of item and Crown movement racing every
//! other on the same rows (the same stack listed, mailed, traded and crafted
//! with; one listing bought, taken down and expired at once; one mail
//! collected twice and deleted and cleaned up; one trade key committed
//! twice), then the ledgers must reconcile and nothing may be in two places.
//!
//! Requests that lose a race are refused with the documented codes; any
//! 5xx fails the test. Seeded, so a failure can be replayed
//! (`SHS_STRESS_SEED`); `SHS_STRESS_ROUNDS` runs it longer. The test is the
//! only one in its binary because the mail cleanup it races runs with no
//! retention period over the whole (shared) test database.

mod common;

use std::collections::HashSet;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer};
use rand::rngs::StdRng;
use rand::seq::SliceRandom;
use rand::{Rng, SeedableRng};
use serde_json::json;
use server::AppState;
use server::items::{self, NewItem};
use server::ledger::audit::reconcile;
use server::ledger::{self, Reason};
use server::trade::{self, Side};
use server::wallet;

const WOOL: u16 = 3001;
const SILK: u16 = 3003;
/// Pointed hats: one row each.
const HAT: u16 = 101;
/// Knit a Cable Beanie: three wool, known by everyone, skill 1.
const BEANIE: u16 = 6;
const PLAYERS: usize = 6;

/// One raced request's result: the status and the error code, if any.
type Outcome = (StatusCode, String);

async fn give(state: &AppState, owner: i64, def: u16, quantity: u32) {
    let mut tx = state.pool.begin().await.unwrap();
    wallet::lock_characters(&mut tx, &[owner]).await.unwrap();
    items::give(
        &mut tx,
        &NewItem::plain(owner, def, quantity, "inventory"),
        Reason::Admin,
        None,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
}

async fn fund(state: &AppState, owner: i64, crowns: i64) {
    let mut tx = state.pool.begin().await.unwrap();
    wallet::lock_characters(&mut tx, &[owner]).await.unwrap();
    ledger::adjust_crowns(&mut tx, owner, crowns, Reason::Admin, None)
        .await
        .unwrap();
    tx.commit().await.unwrap();
}

/// `(id, item_def, quantity)` of a character's inventory rows.
async fn inventory(state: &AppState, owner: i64) -> Vec<(i64, i32, i32)> {
    sqlx::query_as(
        "SELECT id, item_def, quantity FROM items
         WHERE owner_id = $1 AND location = 'inventory' AND item_def = ANY($2) ORDER BY id",
    )
    .bind(owner)
    .bind([i32::from(WOOL), i32::from(SILK), i32::from(HAT)])
    .fetch_all(&state.pool)
    .await
    .unwrap()
}

fn outcome(res: common::Response) -> Outcome {
    let code = res.body["error"].as_str().unwrap_or_default().to_owned();
    (res.status, code)
}

/// A fresh router per request: every request has its own write budget.
async fn post(state: &AppState, path: String, body: serde_json::Value, p: &TestPlayer) -> Outcome {
    outcome(
        TestApp::from_state(state.clone())
            .post(&path, body, p.token())
            .await,
    )
}

async fn delete(state: &AppState, path: String, p: &TestPlayer) -> Outcome {
    outcome(
        TestApp::from_state(state.clone())
            .delete(&path, p.token())
            .await,
    )
}

#[tokio::test]
async fn racing_every_economy_action_never_makes_or_loses_anything() {
    let seed: u64 = std::env::var("SHS_STRESS_SEED")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0x1c_5eed);
    let rounds: usize = std::env::var("SHS_STRESS_ROUNDS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(6);
    let mut rng = StdRng::seed_from_u64(seed);
    let app = TestApp::with_pool(16, |c| c.stations_anywhere = true).await;
    let state = app.state.clone();
    let houses = ["Roarwing", "Burrowsby", "Skytalon", "Serpentyne"];
    let mut players = Vec::new();
    for i in 0..PLAYERS {
        let p = app.player(houses[i % 4]).await;
        give(&state, p.id, WOOL, 150).await;
        give(&state, p.id, SILK, 20).await;
        give(&state, p.id, HAT, 4).await;
        fund(&state, p.id, 5_000).await;
        players.push(p);
    }
    let ids: Vec<i64> = players.iter().map(|p| p.id).collect();
    println!(
        "stress seed {seed}, players: {}",
        players
            .iter()
            .map(|p| p.name.as_str())
            .collect::<Vec<_>>()
            .join(" ")
    );

    let mut seen: HashSet<(StatusCode, String)> = HashSet::new();
    for round in 0..rounds {
        let mut tasks: Vec<tokio::task::JoinHandle<(&'static str, Outcome)>> = Vec::new();
        let inv: Vec<Vec<(i64, i32, i32)>> = {
            let mut v = Vec::new();
            for p in &players {
                v.push(inventory(&state, p.id).await);
            }
            v
        };
        let listings: Vec<(i64, i64)> = sqlx::query_as(
            "SELECT id, seller_id FROM exchange_listings WHERE seller_id = ANY($1) AND state = 'active'",
        )
        .bind(&ids)
        .fetch_all(&state.pool)
        .await
        .unwrap();
        let mails: Vec<(i64, i64)> = sqlx::query_as(
            "SELECT id, recipient_id FROM mail WHERE recipient_id = ANY($1) ORDER BY id DESC LIMIT 60",
        )
        .bind(&ids)
        .fetch_all(&state.pool)
        .await
        .unwrap();
        // Every listing due now, so the expiry job races buyers and sellers.
        let expiring: Vec<i64> = listings
            .iter()
            .filter(|_| rng.gen_bool(0.3))
            .map(|l| l.0)
            .collect();
        sqlx::query("UPDATE exchange_listings SET expires_at = now() WHERE id = ANY($1)")
            .bind(&expiring)
            .execute(&state.pool)
            .await
            .unwrap();
        sqlx::query("UPDATE characters SET craft_ready_at = NULL WHERE id = ANY($1)")
            .bind(&ids)
            .execute(&state.pool)
            .await
            .unwrap();

        for _ in 0..40 {
            let who = rng.gen_range(0..PLAYERS);
            let p = players[who].clone();
            let other = players[(who + rng.gen_range(1..PLAYERS)) % PLAYERS].clone();
            let row = inv[who].choose(&mut rng).copied();
            let st = state.clone();
            match rng.gen_range(0..9) {
                0 => {
                    let Some((item, _, q)) = row else { continue };
                    let qty = rng.gen_range(1..=q.min(15)).max(1);
                    let price = rng.gen_range(1..60);
                    tasks.push(tokio::spawn(async move {
                        (
                            "list",
                            post(
                                &st,
                                "/api/exchange/listings".into(),
                                json!({ "item_id": item, "quantity": qty, "price": price }),
                                &p,
                            )
                            .await,
                        )
                    }));
                }
                1 => {
                    let Some(&(id, seller)) = listings.choose(&mut rng) else {
                        continue;
                    };
                    let buyer = players.iter().find(|x| x.id != seller).unwrap().clone();
                    let buyer2 = players
                        .iter()
                        .rev()
                        .find(|x| x.id != seller)
                        .unwrap()
                        .clone();
                    let st2 = st.clone();
                    tasks.push(tokio::spawn(async move {
                        (
                            "buy",
                            post(
                                &st,
                                format!("/api/exchange/listings/{id}/buy"),
                                json!({}),
                                &buyer,
                            )
                            .await,
                        )
                    }));
                    tasks.push(tokio::spawn(async move {
                        (
                            "buy",
                            post(
                                &st2,
                                format!("/api/exchange/listings/{id}/buy"),
                                json!({}),
                                &buyer2,
                            )
                            .await,
                        )
                    }));
                }
                2 => {
                    let Some(&(id, seller)) = listings.choose(&mut rng) else {
                        continue;
                    };
                    let s = players.iter().find(|x| x.id == seller).unwrap().clone();
                    tasks.push(tokio::spawn(async move {
                        (
                            "take_down",
                            delete(&st, format!("/api/exchange/listings/{id}"), &s).await,
                        )
                    }));
                }
                3 => {
                    let attach = row.map(|(item, _, q)| json!([{ "item_id": item, "quantity": rng.gen_range(1..=q.max(1)) }])).unwrap_or(json!([]));
                    let crowns = rng.gen_range(0..30);
                    let to = other.name.clone();
                    tasks.push(tokio::spawn(async move {
                        ("letter", post(&st, "/api/mail".into(), json!({ "to": to, "subject": "Stress", "items": attach, "crowns": crowns }), &p).await)
                    }));
                }
                4 => {
                    let Some(&(id, recipient)) = mails.choose(&mut rng) else {
                        continue;
                    };
                    let r = players.iter().find(|x| x.id == recipient).unwrap().clone();
                    let (st2, r2) = (st.clone(), r.clone());
                    tasks.push(tokio::spawn(async move {
                        (
                            "collect",
                            post(&st, format!("/api/mail/{id}/collect"), json!({}), &r).await,
                        )
                    }));
                    tasks.push(tokio::spawn(async move {
                        (
                            "collect",
                            post(&st2, format!("/api/mail/{id}/collect"), json!({}), &r2).await,
                        )
                    }));
                }
                5 => {
                    let Some(&(id, recipient)) = mails.choose(&mut rng) else {
                        continue;
                    };
                    let r = players.iter().find(|x| x.id == recipient).unwrap().clone();
                    tasks.push(tokio::spawn(async move {
                        (
                            "delete_mail",
                            delete(&st, format!("/api/mail/{id}"), &r).await,
                        )
                    }));
                }
                6 => {
                    // Both sides offer what they hold now; the same key twice at once.
                    let o = players.iter().position(|x| x.id == other.id).unwrap();
                    let mut offer = |rows: &[(i64, i32, i32)], n: usize| -> Vec<(i64, u32)> {
                        let picked: Vec<(i64, i32, i32)> =
                            rows.choose_multiple(&mut rng, n).copied().collect();
                        picked
                            .into_iter()
                            .map(|(id, _, q)| (id, rng.gen_range(1..=q.max(1)) as u32))
                            .collect()
                    };
                    let mine = offer(&inv[who], 2);
                    let theirs = offer(&inv[o], 1);
                    let a = Side {
                        character_id: p.id,
                        items: mine,
                        crowns: rng.gen_range(0..40),
                        reviewed: Vec::new(),
                    };
                    let b = Side {
                        character_id: other.id,
                        items: theirs,
                        crowns: rng.gen_range(0..40),
                        reviewed: Vec::new(),
                    };
                    let key = (rng.r#gen::<i64>() & i64::MAX).max(1);
                    for _ in 0..2 {
                        let (pool, a, b) = (st.pool.clone(), a.clone(), b.clone());
                        tasks.push(tokio::spawn(async move {
                            let r = trade::commit(&pool, key, &a, &b).await;
                            let outcome = match r {
                                Ok(_) => (StatusCode::OK, String::new()),
                                Err(code) => (StatusCode::CONFLICT, format!("{code:?}")),
                            };
                            ("trade", outcome)
                        }));
                    }
                }
                7 => {
                    tasks.push(tokio::spawn(async move {
                        (
                            "craft",
                            post(&st, "/api/craft".into(), json!({ "recipe_id": BEANIE }), &p)
                                .await,
                        )
                    }));
                }
                _ => {
                    tasks.push(tokio::spawn(async move {
                        let n = server::exchange::expire_listings(&st.pool, None)
                            .await
                            .unwrap();
                        ("expire", (StatusCode::OK, format!("{}", n.min(1))))
                    }));
                    let st = state.clone();
                    tasks.push(tokio::spawn(async move {
                        server::mail::cleanup(&st.pool, Duration::ZERO)
                            .await
                            .unwrap();
                        ("cleanup", (StatusCode::OK, String::new()))
                    }));
                }
            }
        }
        for t in tasks {
            let (what, (status, code)) = t.await.unwrap();
            assert!(
                !status.is_server_error(),
                "round {round}: {what} answered {status} {code}"
            );
            seen.insert((status, format!("{what}:{code}")));
        }
        let report = reconcile(&state.pool, &ids).await.unwrap();
        report.assert_clean();
    }
    // Every item in exactly one place: no row is both attached and listed.
    let doubled: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM mail_attachments a JOIN exchange_listings x ON x.item_id = a.item_id",
    )
    .fetch_one(&state.pool)
    .await
    .unwrap();
    assert_eq!(doubled, 0);
    let mut outcomes: Vec<String> = seen
        .into_iter()
        .map(|(s, c)| format!("{} {c}", s.as_u16()))
        .collect();
    outcomes.sort();
    println!("outcomes seen: {outcomes:?}");
}
