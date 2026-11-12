//! The shared money and item code: `server::ledger` and
//! `server::items` against a real database. Every movement must be in the
//! ledger, every balance must equal the sum of its ledger rows, and the
//! ledgers must refuse to be rewritten.

mod common;

use axum::http::StatusCode;
use common::TestApp;
use serde_json::Value;
use server::catalog::shipped;
use server::items::{self, LockedItem, Maker, NewItem};
use server::ledger::{self, ItemOp, LedgerError, Reason};
use sqlx::PgConnection;

/// Wool skeins stack to 99 (the shipped catalog).
const WOOL: u16 = 3001;

async fn lock_character(conn: &mut PgConnection, id: i64) {
    sqlx::query("SELECT 1 FROM characters WHERE id = $1 FOR NO KEY UPDATE")
        .bind(id)
        .execute(conn)
        .await
        .unwrap();
}

async fn locked(conn: &mut PgConnection, owner: i64, id: i64) -> LockedItem {
    let mut rows = items::lock(conn, owner, &[id]).await.unwrap();
    assert_eq!(rows.len(), 1, "item {id} of {owner}");
    rows.remove(0)
}

/// `(item_id, op, quantity, related_item)` of every ledger row about `ids`.
async fn ledger_rows(app: &TestApp, ids: &[i64]) -> Vec<(i64, String, i32, Option<i64>)> {
    sqlx::query_as(
        "SELECT item_id, op, quantity, related_item FROM item_ledger
         WHERE item_id = ANY($1) OR related_item = ANY($1) ORDER BY id",
    )
    .bind(ids)
    .fetch_all(&app.state.pool)
    .await
    .unwrap()
}

async fn quantity(app: &TestApp, id: i64) -> Option<i32> {
    sqlx::query_scalar("SELECT quantity FROM items WHERE id = $1")
        .bind(id)
        .fetch_optional(&app.state.pool)
        .await
        .unwrap()
}

async fn balance_matches_ledger(app: &TestApp, character: i64) -> i64 {
    let (balance, sum): (i64, Option<i64>) = sqlx::query_as(
        "SELECT c.crowns, (SELECT sum(delta)::bigint FROM crown_ledger g WHERE g.character_id = c.id)
         FROM characters c WHERE c.id = $1",
    )
    .bind(character)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(Some(balance), sum, "balance and ledger agree");
    balance
}

#[tokio::test]
async fn new_characters_get_a_ledgered_wardrobe_and_purse() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    let purse = i64::from(shipped().economy.starting_purse);
    assert_eq!(balance_matches_ledger(&app, p.id).await, purse);
    let wallet = app.get("/api/wallet", p.token()).await;
    assert_eq!(wallet.status, StatusCode::OK);
    assert_eq!(wallet.body["crowns"], purse);

    // One 'grant' row per starter item, at the location it was granted to.
    let (granted, items): (i64, i64) = sqlx::query_as(
        "SELECT (SELECT count(*) FROM item_ledger WHERE to_owner = $1 AND op = 'grant' AND reason = 'starter'),
                (SELECT count(*) FROM items WHERE owner_id = $1)",
    )
    .bind(p.id)
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(granted, items);

    // Items carry the economy fields.
    let inv = app.get("/api/inventory?limit=1", p.token()).await;
    let item = &inv.body["items"][0];
    assert_eq!(item["quantity"], 1);
    assert_eq!(item["maker"], Value::Null);
    assert_eq!(item["finish"], "none");

    // Equipping moves the item, and the ledger knows.
    let shirt = app
        .get("/api/inventory?slot=body&location=inventory", p.token())
        .await;
    let id = shirt.body["items"][0]["id"].as_i64().unwrap();
    let res = app
        .post(
            "/api/equipment",
            serde_json::json!({ "slot": "body", "item_id": id }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let moves: Vec<(String, Option<String>, Option<String>)> = sqlx::query_as(
        "SELECT reason, from_location, to_location FROM item_ledger WHERE item_id = $1 AND op = 'move'",
    )
    .bind(id)
    .fetch_all(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(
        moves,
        vec![(
            "equip".into(),
            Some("inventory".into()),
            Some("equipped".into())
        )]
    );
}

#[tokio::test]
async fn stacks_split_merge_and_get_used_up_with_every_step_recorded() {
    let app = TestApp::new().await;
    let p = app.player("Skytalon").await;
    let mut tx = app.state.pool.begin().await.unwrap();
    lock_character(&mut tx, p.id).await;

    // 150 wool: one full stack of 99 and one of 51.
    let wool = NewItem::plain(p.id, WOOL, 150, "inventory");
    let rows = items::give(&mut tx, &wool, Reason::Find, None)
        .await
        .unwrap();
    assert_eq!(rows.len(), 2);
    // 60 more top up the 51 to 99 and start a third stack of 12.
    let more = NewItem::plain(p.id, WOOL, 60, "inventory");
    let rows2 = items::give(&mut tx, &more, Reason::Find, None)
        .await
        .unwrap();
    assert_eq!(rows2[0], rows[1]);
    assert_eq!(rows2.len(), 2);
    tx.commit().await.unwrap();
    assert_eq!(quantity(&app, rows[0]).await, Some(99));
    assert_eq!(quantity(&app, rows[1]).await, Some(99));
    assert_eq!(quantity(&app, rows2[1]).await, Some(12));

    // A maker's mark or another colour is another stack.
    let mut tx = app.state.pool.begin().await.unwrap();
    lock_character(&mut tx, p.id).await;
    let marked = NewItem {
        maker: Some(Maker {
            character_id: p.id,
            name: p.name.clone(),
        }),
        ..NewItem::plain(p.id, WOOL, 5, "inventory")
    };
    let made = items::give(&mut tx, &marked, Reason::Craft, Some(1))
        .await
        .unwrap();
    assert!(!rows.contains(&made[0]) && made[0] != rows2[1]);

    // Splitting 30 off the third stack, moving them to mail, using up 2.
    let third = locked(&mut tx, p.id, rows2[1]).await;
    let part = items::take(&mut tx, &third, 5, Reason::MailSend, Some(7))
        .await
        .unwrap();
    assert_ne!(part, third.id);
    let part_row = locked(&mut tx, p.id, part).await;
    assert_eq!(part_row.quantity, 5);
    items::move_to(&mut tx, &part_row, p.id, "mail", Reason::MailSend, Some(7))
        .await
        .unwrap();
    let third = locked(&mut tx, p.id, rows2[1]).await;
    items::consume(&mut tx, &third, 2, Reason::Craft, Some(2))
        .await
        .unwrap();
    assert!(matches!(
        items::consume(&mut tx, &third, 99, Reason::Craft, None).await,
        Err(items::ItemError::NotEnough)
    ));
    // Collecting the mailed part merges it back into the inventory stack.
    let mailed = locked(&mut tx, p.id, part).await;
    assert_eq!(mailed.location, "mail");
    let into = items::into_inventory(&mut tx, &mailed, Reason::MailCollect, Some(7))
        .await
        .unwrap();
    assert_eq!(into, vec![rows2[1]]);
    tx.commit().await.unwrap();
    assert_eq!(quantity(&app, rows2[1]).await, Some(12 - 5 - 2 + 5));
    assert_eq!(quantity(&app, part).await, None, "merged away");

    let log = ledger_rows(&app, &[rows2[1], part]).await;
    let ops: Vec<&str> = log.iter().map(|r| r.1.as_str()).collect();
    assert_eq!(ops, ["create", "split", "move", "consume", "merge"]);
    let split = log.iter().find(|r| r.1 == "split").unwrap();
    assert_eq!((split.0, split.2, split.3), (part, 5, Some(rows2[1])));

    // Units in the economy = created - consumed, for this character's wool.
    let (created, consumed, held): (i64, i64, i64) = sqlx::query_as(
        "SELECT (SELECT coalesce(sum(quantity), 0) FROM item_ledger WHERE to_owner = $1 AND item_def = $2 AND op = 'create')::bigint,
                (SELECT coalesce(sum(quantity), 0) FROM item_ledger WHERE from_owner = $1 AND item_def = $2 AND op = 'consume')::bigint,
                (SELECT coalesce(sum(quantity), 0) FROM items WHERE owner_id = $1 AND item_def = $2)::bigint",
    )
    .bind(p.id)
    .bind(i32::from(WOOL))
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(created - consumed, held);

    // Clothing never stacks.
    let mut tx = app.state.pool.begin().await.unwrap();
    let hats = NewItem::plain(p.id, 106, 2, "inventory");
    let rows = items::give(&mut tx, &hats, Reason::ShopOrder, None)
        .await
        .unwrap();
    assert_eq!(rows.len(), 2);
    let too_many = NewItem::plain(p.id, 106, 2, "inventory");
    assert!(matches!(
        items::create(&mut tx, &too_many, ItemOp::Create, Reason::Admin, None).await,
        Err(items::ItemError::OverStack(106, 1))
    ));
    tx.rollback().await.unwrap();
}

#[tokio::test]
async fn crowns_never_go_negative_and_the_ledgers_are_append_only() {
    let app = TestApp::new().await;
    let p = app.player("Serpentyne").await;
    let purse = i64::from(shipped().economy.starting_purse);
    let mut tx = app.state.pool.begin().await.unwrap();
    lock_character(&mut tx, p.id).await;
    let after = ledger::adjust_crowns(&mut tx, p.id, -30, Reason::ShopOrder, Some(1))
        .await
        .unwrap();
    assert_eq!(after, purse - 30);
    assert!(matches!(
        ledger::adjust_crowns(&mut tx, p.id, -(purse - 29), Reason::ShopOrder, Some(2)).await,
        Err(LedgerError::InsufficientCrowns)
    ));
    assert_eq!(
        ledger::adjust_crowns(&mut tx, p.id, 0, Reason::Find, None)
            .await
            .unwrap(),
        purse - 30
    );
    let after = ledger::adjust_crowns(&mut tx, p.id, 12, Reason::Find, None)
        .await
        .unwrap();
    assert_eq!(after, purse - 18);
    tx.commit().await.unwrap();
    assert_eq!(balance_matches_ledger(&app, p.id).await, purse - 18);

    // The database itself refuses a negative balance.
    let err = sqlx::query("UPDATE characters SET crowns = -1 WHERE id = $1")
        .bind(p.id)
        .execute(&app.state.pool)
        .await
        .unwrap_err();
    assert!(
        err.to_string().contains("characters_crowns_nonneg"),
        "{err}"
    );

    // No rewriting history.
    for sql in [
        "UPDATE crown_ledger SET delta = 1000 WHERE character_id = $1",
        "DELETE FROM crown_ledger WHERE character_id = $1",
        "UPDATE item_ledger SET quantity = 2 WHERE to_owner = $1",
        "DELETE FROM item_ledger WHERE to_owner = $1",
    ] {
        let err = sqlx::query(sql)
            .bind(p.id)
            .execute(&app.state.pool)
            .await
            .unwrap_err();
        assert!(err.to_string().contains("append-only"), "{sql}: {err}");
    }
}

// ------------------------------------------------------------------ the audit

/// The reconciliation catches what it exists to catch: each corruption
/// below happens behind the ledger's back, as a duplication or loss bug
/// would, and must be reported.
#[tokio::test]
async fn the_reconciliation_finds_what_the_ledger_cannot_explain() {
    use server::ledger::audit::reconcile;

    let app = TestApp::new().await;
    let audit = |ids: Vec<i64>| {
        let pool = app.state.pool.clone();
        async move { reconcile(&pool, &ids).await.unwrap() }
    };
    let corrupt = |sql: &'static str, id: i64| {
        let pool = app.state.pool.clone();
        async move {
            sqlx::query(sql).bind(id).execute(&pool).await.unwrap();
        }
    };

    // Clean to begin with: a starter wardrobe, a purse, a stack.
    let p = app.player("Roarwing").await;
    let mut tx = app.state.pool.begin().await.unwrap();
    lock_character(&mut tx, p.id).await;
    let wool = items::give(
        &mut tx,
        &NewItem::plain(p.id, WOOL, 10, "inventory"),
        Reason::Find,
        None,
    )
    .await
    .unwrap()[0];
    tx.commit().await.unwrap();
    let report = audit(vec![p.id]).await;
    report.assert_clean();
    assert!(report.items_checked > 10);

    // Units appearing from nowhere.
    corrupt(
        "UPDATE items SET quantity = quantity + 1 WHERE id = $1",
        wool,
    )
    .await;
    let report = audit(vec![p.id]).await;
    assert_eq!(report.problems.len(), 1, "{:?}", report.problems);
    assert_eq!(report.problems[0].check, "item");
    assert!(report.problems[0].detail.contains("11 units"));

    // Crowns appearing from nowhere.
    let q = app.player("Burrowsby").await;
    corrupt(
        "UPDATE characters SET crowns = crowns + 5 WHERE id = $1",
        q.id,
    )
    .await;
    let report = audit(vec![q.id]).await;
    assert_eq!(report.problems[0].check, "crowns", "{:?}", report.problems);

    // An item in the mail that no mail carries; an item vanishing.
    let r = app.player("Skytalon").await;
    let inv = app
        .get("/api/inventory?location=inventory&limit=2", r.token())
        .await;
    let (one, two) = (
        inv.body["items"][0]["id"].as_i64().unwrap(),
        inv.body["items"][1]["id"].as_i64().unwrap(),
    );
    corrupt("UPDATE items SET location = 'mail' WHERE id = $1", one).await;
    corrupt("DELETE FROM items WHERE id = $1", two).await;
    let report = audit(vec![r.id]).await;
    let checks: Vec<&str> = report.problems.iter().map(|p| p.check).collect();
    assert!(checks.contains(&"place"), "{:?}", report.problems);
    assert!(
        report.problems.iter().any(|p| p.detail.contains("is gone")),
        "{:?}",
        report.problems
    );
    // Another character's mess is not this one's.
    audit(vec![app.player("Serpentyne").await.id])
        .await
        .assert_clean();
}

/// XP must be what its ledger adds up to, and house points
/// must name what earned them.
#[tokio::test]
async fn the_reconciliation_checks_xp_and_house_points() {
    use protocol::{House, XpSource};
    use server::house_points::{self, PointsSource};
    use server::ledger::audit::reconcile;

    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Burrowsby").await;
    let mut tx = pool.begin().await.unwrap();
    server::progression::grant_xp(&mut tx, p.id, XpSource::Find, None, 15, chrono::Utc::now())
        .await
        .unwrap();
    server::progression::grant_xp(
        &mut tx,
        p.id,
        XpSource::Admin,
        None,
        300,
        chrono::Utc::now(),
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let report = reconcile(pool, &[p.id]).await.unwrap();
    report.assert_clean();
    assert_eq!(report.xp_rows_checked, 2);

    // XP from nowhere: the total and the level no longer match the ledger.
    sqlx::query("UPDATE characters SET xp = xp + 500 WHERE id = $1")
        .bind(p.id)
        .execute(pool)
        .await
        .unwrap();
    let report = reconcile(pool, &[p.id]).await.unwrap();
    let xp: Vec<&str> = report.problems.iter().map(|p| p.detail.as_str()).collect();
    assert!(
        report.problems.iter().all(|p| p.check == "xp") && xp.len() == 2,
        "{:?}",
        report.problems
    );

    // A class house point for an attendance that does not exist, and in
    // another house than the character's.
    let q = app.player("Skytalon").await;
    let mut tx = pool.begin().await.unwrap();
    assert!(
        house_points::award(
            &mut tx,
            House::Roarwing,
            3,
            Some(q.id),
            PointsSource::Class,
            Some(-7)
        )
        .await
        .unwrap()
    );
    tx.commit().await.unwrap();
    let report = reconcile(pool, &[q.id]).await.unwrap();
    assert_eq!(report.problems.len(), 1, "{:?}", report.problems);
    assert_eq!(report.problems[0].check, "house_points");
}

/// `shs-server audit NAME`: the operator's reconciliation command.
#[tokio::test]
async fn the_audit_command_reconciles_named_characters() {
    let app = TestApp::new().await;
    let p = app.player("Burrowsby").await;
    let url =
        std::env::var("DATABASE_URL").unwrap_or_else(|_| "postgres://localhost/shs_test".into());
    let run = |name: String| {
        let url = url.clone();
        tokio::task::spawn_blocking(move || {
            std::process::Command::new(env!("CARGO_BIN_EXE_shs-server"))
                .args(["audit", &name])
                .env("DATABASE_URL", url)
                .output()
                .unwrap()
        })
    };
    let out = run(p.name.clone()).await.unwrap();
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{stdout}");
    assert!(stdout.contains("audit: 1 characters"), "{stdout}");
    assert!(stdout.contains(" 0 problems"), "{stdout}");

    // Minted Crowns: exit status 1, and the problem is printed.
    sqlx::query("UPDATE characters SET crowns = crowns + 5 WHERE id = $1")
        .bind(p.id)
        .execute(&app.state.pool)
        .await
        .unwrap();
    let out = run(p.name.clone()).await.unwrap();
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(!out.status.success(), "{stdout}");
    assert!(stdout.contains("crowns"), "{stdout}");
}
