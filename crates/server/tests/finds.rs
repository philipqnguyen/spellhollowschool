//! Exploration finds end to end: searching a spot in reach
//! rolls its loot table and grants the drops with ledger rows, a spot pays
//! out once per cooldown however fast it is searched, and the cooldowns are
//! listed and cleaned up.

#[path = "effects_harness.rs"]
mod harness;

use harness::{Client, SPOT, TestServer};
use protocol::{ClientMsg, DormErrorCode, FindOutcome, FoundItem, ServerMsg};
use server::catalog::shipped;
use sim_core::Drops;

async fn find_result(c: &mut Client) -> FindOutcome {
    c.expect(|m| match m {
        ServerMsg::FindResult { spot, outcome } if spot == SPOT => Some(outcome.clone()),
        _ => None,
    })
    .await
}

/// What the ledger says finds gave the character: (item units, Crowns).
async fn found_by_ledger(server: &TestServer, id: i64) -> (i64, i64) {
    let units: i64 = sqlx::query_scalar(
        "SELECT COALESCE(SUM(quantity), 0)::bigint FROM item_ledger
         WHERE reason = 'find' AND to_owner = $1",
    )
    .bind(id)
    .fetch_one(server.pool())
    .await
    .unwrap();
    let crowns: i64 = sqlx::query_scalar(
        "SELECT COALESCE(SUM(delta), 0)::bigint FROM crown_ledger
         WHERE reason = 'find' AND character_id = $1",
    )
    .bind(id)
    .fetch_one(server.pool())
    .await
    .unwrap();
    (units, crowns)
}

fn totals(outcome: &FindOutcome) -> (i64, i64) {
    match outcome {
        FindOutcome::Found { items, crowns } => {
            (items.iter().map(|i| i64::from(i.quantity)).sum(), *crowns)
        }
        _ => (0, 0),
    }
}

#[tokio::test]
async fn searching_a_spot_finds_once_then_waits_a_day() {
    let server = TestServer::start().await;
    let ada = server.bot().await;
    let mut a = server.join(&ada).await;
    let purse = server.get(&ada, "/api/wallet").await["crowns"]
        .as_i64()
        .unwrap();
    a.send(&ClientMsg::Interact {
        target: SPOT.into(),
    })
    .await;
    let first = find_result(&mut a).await;
    assert!(
        matches!(first, FindOutcome::Found { .. } | FindOutcome::Nothing),
        "{first:?}"
    );
    let (units, crowns) = totals(&first);
    if crowns > 0 {
        let wallet = a
            .expect(|m| match m {
                ServerMsg::Wallet { crowns } => Some(*crowns),
                _ => None,
            })
            .await;
        assert_eq!(wallet, purse + crowns);
    }
    assert_eq!(found_by_ledger(&server, ada.id).await, (units, crowns));
    if let FindOutcome::Found { items, .. } = &first {
        for FoundItem { item_def, quantity } in items {
            assert!(server.units(ada.id, *item_def).await >= i64::from(*quantity));
        }
    }

    a.send(&ClientMsg::Interact {
        target: SPOT.into(),
    })
    .await;
    match find_result(&mut a).await {
        FindOutcome::Cooldown { ready_in_secs } => {
            let day = shipped().finds.cooldown_secs;
            assert!(
                ready_in_secs > day - 60 && ready_in_secs <= day,
                "{ready_in_secs}"
            );
        }
        other => panic!("expected a cooldown, got {other:?}"),
    }
    // Out of reach.
    a.send(&ClientMsg::Interact {
        target: "find_far_away".into(),
    })
    .await;
    let code = a
        .expect(|m| match m {
            ServerMsg::DormError { code, .. } => Some(*code),
            _ => None,
        })
        .await;
    assert_eq!(code, DormErrorCode::TooFar);

    let listed = server.get(&ada, "/api/finds").await;
    let items = listed["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["spot"], SPOT);
    assert!(items[0]["ready_at"].is_string());
    assert!(listed["next"].is_null());
}

#[tokio::test]
async fn find_spam_pays_out_once() {
    let server = TestServer::start().await;
    let ada = server.bot().await;
    let mut a = server.join(&ada).await;
    for _ in 0..6 {
        a.send(&ClientMsg::Interact {
            target: SPOT.into(),
        })
        .await;
    }
    let first = loop {
        match find_result(&mut a).await {
            FindOutcome::Cooldown { .. } => continue,
            other => break other,
        }
    };
    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
    assert_eq!(found_by_ledger(&server, ada.id).await, totals(&first));

    // Straight at the database, from many tasks at once.
    let bea = server.bot().await;
    let pool = server.pool().clone();
    let tasks: Vec<_> = (0..10)
        .map(|_| {
            let pool = pool.clone();
            tokio::spawn(async move { server::finds::search(&pool, bea.id, SPOT, None).await })
        })
        .collect();
    let mut paid = 0;
    let mut waited = 0;
    for t in tasks {
        match t.await.unwrap().unwrap().outcome {
            FindOutcome::Cooldown { .. } => waited += 1,
            FindOutcome::Found { .. } | FindOutcome::Nothing => paid += 1,
            FindOutcome::Failed => panic!("failed"),
        }
    }
    assert_eq!((paid, waited), (1, 9));
    let cooldowns: i64 =
        sqlx::query_scalar("SELECT count(*) FROM find_cooldowns WHERE character_id = $1")
            .bind(bea.id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(cooldowns, 1);
}

#[tokio::test]
async fn grants_stack_pay_crowns_and_cooldowns_pass() {
    let server = TestServer::start().await;
    let ada = server.bot().await;
    let pool = server.pool().clone();
    let wool = 3001;
    let before = server.units(ada.id, wool).await;
    let drops = Drops {
        items: vec![(wool, 4)],
        crowns: 7,
    };
    let purse = server::ledger::balance(&pool, ada.id).await.unwrap();
    let got = server::finds::grant(&pool, ada.id, SPOT, &drops)
        .await
        .unwrap();
    assert_eq!(
        got.outcome,
        FindOutcome::Found {
            items: vec![FoundItem {
                item_def: wool,
                quantity: 4
            }],
            crowns: 7
        }
    );
    assert_eq!(got.crowns, Some(purse + 7));
    assert_eq!(server.units(ada.id, wool).await, before + 4);
    let row: (i64, String) = sqlx::query_as(
        "SELECT balance_after, reason FROM crown_ledger WHERE character_id = $1
         ORDER BY id DESC LIMIT 1",
    )
    .bind(ada.id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(row, (purse + 7, "find".to_owned()));

    // The cooldown passes; the janitor clears it; an empty roll is "nothing".
    sqlx::query(
        "UPDATE find_cooldowns SET ready_at = now() - interval '1 second' WHERE character_id = $1",
    )
    .bind(ada.id)
    .execute(&pool)
    .await
    .unwrap();
    assert!(server::effects::cleanup_expired(&pool).await.unwrap() >= 1);
    let empty = server::finds::grant(&pool, ada.id, SPOT, &Drops::default())
        .await
        .unwrap();
    assert_eq!(empty.outcome, FindOutcome::Nothing);
    assert_eq!(empty.crowns, None);
    // Unknown spots are refused before anything happens.
    assert!(
        server::finds::search(&pool, ada.id, "find_nowhere", None)
            .await
            .is_err()
    );
    // Fortune's Draught lends the economy's luck.
    let luck = server::finds::fortunes_draught_luck();
    assert_eq!(
        luck.extra_rolls,
        shipped().economy.effects.fortunes_draught.extra_rolls
    );
    assert_eq!(
        luck.rare_weight_pct,
        shipped().economy.effects.fortunes_draught.rare_weight_pct
    );
}
