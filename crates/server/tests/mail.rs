//! Owl mail: letters with items, Crowns and postage, the mailbox and its
//! unread count, collecting (exactly once, even when raced), deleting only
//! what was collected, and the refusals; the ledger must explain every
//! movement afterwards.

mod common;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer};
use serde_json::{Value, json};
use server::catalog::shipped;
use server::items::{self, NewItem};
use server::ledger::Reason;
use server::ledger::audit::reconcile;
use server::wallet;

const WOOL: u16 = 3001;

fn purse() -> i64 {
    i64::from(shipped().economy.starting_purse)
}

fn postage() -> i64 {
    i64::from(shipped().economy.mail.postage)
}

/// `quantity` units of `def` straight into `p`'s inventory (ledgered).
async fn grant(app: &TestApp, p: &TestPlayer, def: u16, quantity: u32) -> Vec<i64> {
    let mut tx = app.state.pool.begin().await.unwrap();
    wallet::lock_characters(&mut tx, &[p.id]).await.unwrap();
    let rows = items::give(
        &mut tx,
        &NewItem::plain(p.id, def, quantity, "inventory"),
        Reason::Admin,
        None,
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    rows
}

async fn send(app: &TestApp, from: &TestPlayer, body: Value) -> common::Response {
    app.post("/api/mail", body, from.token()).await
}

async fn crowns(app: &TestApp, p: &TestPlayer) -> i64 {
    app.get("/api/wallet", p.token()).await.body["crowns"]
        .as_i64()
        .unwrap()
}

async fn quantity(app: &TestApp, id: i64) -> Option<i32> {
    sqlx::query_scalar("SELECT quantity FROM items WHERE id = $1")
        .bind(id)
        .fetch_optional(&app.state.pool)
        .await
        .unwrap()
}

#[tokio::test]
async fn a_letter_carries_items_and_crowns_and_is_collected_once() {
    let app = TestApp::new().await;
    let (ada, bea) = (app.player("Roarwing").await, app.player("Skytalon").await);
    let wool = grant(&app, &ada, WOOL, 30).await[0];
    let hat = app
        .get("/api/inventory?slot=hat&location=inventory", ada.token())
        .await
        .body["items"][0]["id"]
        .as_i64()
        .unwrap();

    let res = send(
        &app,
        &ada,
        json!({
            "to": bea.name.to_lowercase(), "subject": "  For your knitting  ",
            "body": "Ten skeins\nand a hat.",
            "items": [{ "item_id": wool, "quantity": 10 }, { "item_id": hat }],
            "crowns": 25
        }),
    )
    .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(res.body["crowns"], purse() - 25 - postage());
    let mail = &res.body["mail"];
    assert_eq!(mail["subject"], "For your knitting");
    assert_eq!(mail["kind"], "letter");
    assert_eq!(mail["sender_name"], ada.name);
    assert_eq!(mail["attachments"], 2);
    assert_eq!(mail["collected"], false);
    let id = mail["id"].as_i64().unwrap();
    assert_eq!(quantity(&app, wool).await, Some(20), "ten split off");

    // Bea's mailbox: one unread letter. Reading it marks it read.
    let mailbox = app.get("/api/mail", bea.token()).await;
    assert_eq!(mailbox.body["unread"], 1);
    assert_eq!(mailbox.body["items"][0]["id"], id);
    let detail = app.get(&format!("/api/mail/{id}"), bea.token()).await;
    assert_eq!(detail.status, StatusCode::OK);
    assert_eq!(detail.body["body"], "Ten skeins\nand a hat.");
    assert_eq!(detail.body["read"], true);
    let attached = detail.body["items"].as_array().unwrap();
    assert_eq!(attached.len(), 2);
    assert!(attached.iter().all(|i| i["location"] == "mail"));
    assert_eq!(app.get("/api/mail", bea.token()).await.body["unread"], 0);
    // Not Ada's to read.
    let theirs = app.get(&format!("/api/mail/{id}"), ada.token()).await;
    assert_eq!(theirs.status, StatusCode::NOT_FOUND);
    assert_eq!(theirs.body["error"], "mail_not_found");

    // Not deletable until collected; collected once.
    let early = app.delete(&format!("/api/mail/{id}"), bea.token()).await;
    assert_eq!(early.status, StatusCode::CONFLICT);
    assert_eq!(early.body["error"], "uncollected");
    let collected = app
        .post(&format!("/api/mail/{id}/collect"), json!({}), bea.token())
        .await;
    assert_eq!(collected.status, StatusCode::OK, "{:?}", collected.body);
    assert_eq!(collected.body["crowns"], purse() + 25);
    assert_eq!(collected.body["items"].as_array().unwrap().len(), 2);
    let again = app
        .post(&format!("/api/mail/{id}/collect"), json!({}), bea.token())
        .await;
    assert_eq!(again.status, StatusCode::CONFLICT);
    assert_eq!(again.body["error"], "already_collected");
    assert_eq!(crowns(&app, &bea).await, purse() + 25);
    let owner: i64 = sqlx::query_scalar("SELECT owner_id FROM items WHERE id = $1")
        .bind(hat)
        .fetch_one(&app.state.pool)
        .await
        .unwrap();
    assert_eq!(owner, bea.id);

    let deleted = app.delete(&format!("/api/mail/{id}"), bea.token()).await;
    assert_eq!(deleted.status, StatusCode::NO_CONTENT);
    let gone = app.get(&format!("/api/mail/{id}"), bea.token()).await;
    assert_eq!(gone.status, StatusCode::NOT_FOUND);
    reconcile(&app.state.pool, &[ada.id, bea.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Duplication hunt: many collects of one mail at once pay out once.
#[tokio::test]
async fn racing_collects_pay_out_once() {
    let app = TestApp::new().await;
    let (ada, bea) = (
        app.player("Burrowsby").await,
        app.player("Serpentyne").await,
    );
    let wool = grant(&app, &ada, WOOL, 40).await[0];
    let res = send(
        &app,
        &ada,
        json!({ "to": bea.name, "subject": "Race", "items": [{ "item_id": wool, "quantity": 40 }], "crowns": 50 }),
    )
    .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    let id = res.body["mail"]["id"].as_i64().unwrap();
    // Bea already holds some wool, so the collect merges.
    grant(&app, &bea, WOOL, 7).await;

    let racers: Vec<_> = (0..8)
        .map(|_| {
            let app = TestApp::from_state(app.state.clone());
            let bea = bea.clone();
            tokio::spawn(async move {
                app.post(&format!("/api/mail/{id}/collect"), json!({}), bea.token())
                    .await
                    .status
            })
        })
        .collect();
    let mut ok = 0;
    for r in racers {
        match r.await.unwrap() {
            StatusCode::OK => ok += 1,
            StatusCode::CONFLICT => {}
            other => panic!("unexpected {other}"),
        }
    }
    assert_eq!(ok, 1);
    assert_eq!(crowns(&app, &bea).await, purse() + 50);
    let held: i64 = sqlx::query_scalar(
        "SELECT sum(quantity)::bigint FROM items WHERE owner_id = $1 AND item_def = $2",
    )
    .bind(bea.id)
    .bind(i32::from(WOOL))
    .fetch_one(&app.state.pool)
    .await
    .unwrap();
    assert_eq!(held, 47);
    reconcile(&app.state.pool, &[ada.id, bea.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn letters_are_refused_without_changing_anything() {
    let app = TestApp::new().await;
    let (ada, bea, cy) = (
        app.player("Roarwing").await,
        app.player("Burrowsby").await,
        app.player("Skytalon").await,
    );
    let wool = grant(&app, &ada, WOOL, 5).await[0];
    let bea_wool = grant(&app, &bea, WOOL, 5).await[0];
    let worn = app.get("/api/equipment", ada.token()).await.body["body"]["id"]
        .as_i64()
        .unwrap();
    let letter = |extra: Value| {
        let mut body = json!({ "to": bea.name, "subject": "Hello" });
        for (k, v) in extra.as_object().unwrap() {
            body[k] = v.clone();
        }
        body
    };
    let cases = [
        (
            json!({ "to": "Nobodyatall" }),
            StatusCode::NOT_FOUND,
            "character_not_found",
        ),
        (json!({ "to": ada.name }), StatusCode::BAD_REQUEST, "self"),
        (
            json!({ "subject": " " }),
            StatusCode::BAD_REQUEST,
            "invalid_mail",
        ),
        (
            json!({ "subject": "x".repeat(81) }),
            StatusCode::BAD_REQUEST,
            "invalid_mail",
        ),
        (
            json!({ "body": "x".repeat(2001) }),
            StatusCode::BAD_REQUEST,
            "invalid_mail",
        ),
        (
            json!({ "crowns": -1 }),
            StatusCode::BAD_REQUEST,
            "invalid_mail",
        ),
        (
            json!({ "crowns": 100_001 }),
            StatusCode::BAD_REQUEST,
            "invalid_mail",
        ),
        (
            json!({ "items": [{ "item_id": wool }, { "item_id": wool }] }),
            StatusCode::BAD_REQUEST,
            "invalid_mail",
        ),
        (
            json!({ "items": (0..7).map(|i| json!({ "item_id": i })).collect::<Vec<_>>() }),
            StatusCode::BAD_REQUEST,
            "invalid_mail",
        ),
        (
            json!({ "items": [{ "item_id": bea_wool }] }),
            StatusCode::NOT_FOUND,
            "item_not_found",
        ),
        (
            json!({ "items": [{ "item_id": worn }] }),
            StatusCode::CONFLICT,
            "item_unavailable",
        ),
        (
            json!({ "items": [{ "item_id": wool, "quantity": 6 }] }),
            StatusCode::CONFLICT,
            "item_unavailable",
        ),
        // Everything plus postage is one Crown too many.
        (
            json!({ "crowns": purse() }),
            StatusCode::CONFLICT,
            "insufficient_crowns",
        ),
    ];
    for (extra, status, code) in cases {
        // A fresh router each time: refusals spend write tokens too.
        let fresh = TestApp::from_state(app.state.clone());
        let res = send(&fresh, &ada, letter(extra.clone())).await;
        assert_eq!(res.status, status, "{extra}: {:?}", res.body);
        assert_eq!(res.body["error"], code, "{extra}");
    }
    // Blocks either way: the other looks unknown.
    let blocked = app
        .post("/api/blocks", json!({ "name": cy.name }), bea.token())
        .await;
    assert_eq!(blocked.status, StatusCode::CREATED);
    for (from, to) in [(&cy, &bea), (&bea, &cy)] {
        let res = send(&app, from, json!({ "to": to.name, "subject": "Hi" })).await;
        assert_eq!(res.status, StatusCode::NOT_FOUND);
        assert_eq!(res.body["error"], "character_not_found");
    }
    assert_eq!(crowns(&app, &ada).await, purse());
    assert_eq!(quantity(&app, wool).await, Some(5));
    assert!(
        app.get("/api/mail", bea.token()).await.body["items"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    reconcile(&app.state.pool, &[ada.id, bea.id, cy.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn retried_letters_go_once_and_plain_letters_need_no_collecting() {
    let app = TestApp::new().await;
    let (ada, bea) = (app.player("Serpentyne").await, app.player("Roarwing").await);
    let body = json!({ "to": bea.name, "subject": "Once", "crowns": 3, "request_id": "abc" });
    let first = send(&app, &ada, body.clone()).await;
    let second = send(&app, &ada, body).await;
    assert_eq!(first.status, StatusCode::CREATED);
    assert_eq!(second.status, StatusCode::CREATED);
    assert_eq!(first.body["mail"]["id"], second.body["mail"]["id"]);
    assert_eq!(second.body["crowns"], purse() - 3 - postage());

    // A letter with nothing enclosed: nothing to collect, deletable at once.
    let plain = send(
        &app,
        &ada,
        json!({ "to": bea.name, "subject": "Just words" }),
    )
    .await;
    let id = plain.body["mail"]["id"].as_i64().unwrap();
    assert_eq!(plain.body["mail"]["collected"], true);
    let collect = app
        .post(&format!("/api/mail/{id}/collect"), json!({}), bea.token())
        .await;
    assert_eq!(collect.body["error"], "already_collected");
    let delete = app.delete(&format!("/api/mail/{id}"), bea.token()).await;
    assert_eq!(delete.status, StatusCode::NO_CONTENT);
    assert_eq!(crowns(&app, &ada).await, purse() - 3 - 2 * postage());
    reconcile(&app.state.pool, &[ada.id, bea.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn the_mailbox_pages_newest_first_and_letters_are_rate_limited() {
    let app = TestApp::new().await;
    let (ada, bea) = (app.player("Skytalon").await, app.player("Burrowsby").await);
    let burst = shipped().economy.mail.send_burst;
    let mut sent = Vec::new();
    for n in 0..burst {
        let res = send(
            &app,
            &ada,
            json!({ "to": bea.name, "subject": format!("No. {n}") }),
        )
        .await;
        assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
        sent.push(res.body["mail"]["id"].as_i64().unwrap());
    }
    let over = send(
        &app,
        &ada,
        json!({ "to": bea.name, "subject": "One too many" }),
    )
    .await;
    assert_eq!(over.status, StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(over.body["error"], "rate_limited");

    let mut seen = Vec::new();
    let mut after = String::new();
    loop {
        let res = app
            .get(&format!("/api/mail?limit=3&after={after}"), bea.token())
            .await;
        assert_eq!(res.body["unread"], i64::from(burst));
        seen.extend(
            res.body["items"]
                .as_array()
                .unwrap()
                .iter()
                .map(|m| m["id"].as_i64().unwrap()),
        );
        match res.body["next"].as_str() {
            Some(n) => after = n.to_owned(),
            None => break,
        }
    }
    sent.reverse();
    assert_eq!(seen, sent);
    reconcile(&app.state.pool, &[ada.id, bea.id])
        .await
        .unwrap()
        .assert_clean();
}

// ---------------------------------------------------------------- the Outbox

/// A letter through a fresh router (its own write and letter budget).
async fn letter(app: &TestApp, from: &TestPlayer, to: &TestPlayer, extra: Value) -> i64 {
    let mut body = json!({ "to": to.name, "subject": "Hello" });
    body.as_object_mut()
        .unwrap()
        .extend(extra.as_object().unwrap().clone());
    let res = send(&TestApp::from_state(app.state.clone()), from, body).await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    res.body["mail"]["id"].as_i64().unwrap()
}

/// Every page of `path` (`?limit=` given) for `p`, following `next`.
async fn all_pages(app: &TestApp, p: &TestPlayer, path: &str) -> Vec<Value> {
    let mut out = Vec::new();
    let mut after = String::new();
    loop {
        let res = app.get(&format!("{path}&after={after}"), p.token()).await;
        assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
        out.extend(res.body["items"].as_array().unwrap().iter().cloned());
        match res.body["next"].as_str() {
            Some(n) => after = n.to_owned(),
            None => break,
        }
    }
    out
}

fn ids(rows: &[Value]) -> Vec<i64> {
    rows.iter().map(|m| m["id"].as_i64().unwrap()).collect()
}

/// A system mail (no sender) to `to`, as the shop or the Exchange sends it.
async fn system_mail(app: &TestApp, to: &TestPlayer) -> i64 {
    let mut tx = app.state.pool.begin().await.unwrap();
    let id = server::mail::create(
        &mut tx,
        &server::mail::NewMail {
            recipient: to.id,
            sender_id: None,
            sender_name: server::mail::SENDER_EXCHANGE,
            kind: server::mail::KIND_SYSTEM,
            subject: "A notice",
            body: "",
            crowns: 0,
            ref_id: None,
            client_key: None,
            with_items: false,
        },
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    id
}

async fn mail_row_exists(app: &TestApp, id: i64) -> bool {
    sqlx::query_scalar("SELECT EXISTS (SELECT 1 FROM mail WHERE id = $1)")
        .bind(id)
        .fetch_one(&app.state.pool)
        .await
        .unwrap()
}

/// The Outbox lists the sender's own letters newest first, a page at a
/// time, with who they went to, what went with them (still after
/// collection) and whether they were read or collected; never letters
/// received nor system mail.
#[tokio::test]
async fn the_outbox_pages_own_letters_newest_first_with_their_status() {
    let app = TestApp::new().await;
    let (ada, bea, cy) = (
        app.player("Roarwing").await,
        app.player("Burrowsby").await,
        app.player("Skytalon").await,
    );
    let wool = grant(&app, &ada, WOOL, 10).await[0];
    let mut sent = vec![
        letter(&app, &ada, &bea, json!({ "subject": "First" })).await,
        letter(&app, &ada, &cy, json!({ "subject": "Second", "body": "Dear Cy" })).await,
        letter(
            &app,
            &ada,
            &bea,
            json!({ "subject": "Parcel", "items": [{ "item_id": wool, "quantity": 4 }], "crowns": 9 }),
        )
        .await,
        letter(&app, &ada, &cy, json!({ "subject": "Fourth" })).await,
        letter(&app, &ada, &bea, json!({ "subject": "Fifth" })).await,
    ];
    let parcel = sent[2];
    // Not Ada's sending: a letter to her, and system mail to her.
    let to_ada = letter(&app, &bea, &ada, json!({ "subject": "To Ada" })).await;
    let notice = system_mail(&app, &ada).await;
    let inbox = ids(&all_pages(&app, &ada, "/api/mail?limit=10").await);
    assert_eq!(inbox, vec![notice, to_ada]);

    // Bea reads the first and collects the parcel.
    assert_eq!(
        app.get(&format!("/api/mail/{}", sent[0]), bea.token())
            .await
            .status,
        StatusCode::OK
    );
    let res = app
        .post(
            &format!("/api/mail/{parcel}/collect"),
            json!({}),
            bea.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);

    let outbox = all_pages(&app, &ada, "/api/mail/sent?limit=2").await;
    sent.reverse();
    assert_eq!(ids(&outbox), sent, "newest first over three pages");
    let row = |id: i64| outbox.iter().find(|m| m["id"] == id).unwrap().clone();
    let first = row(sent[4]);
    assert_eq!(first["recipient_name"], bea.name);
    assert_eq!(first["recipient_id"], bea.id);
    assert_eq!(first["subject"], "First");
    assert_eq!(first["read"], true);
    assert_eq!(first["collected"], true, "nothing to collect");
    assert_eq!(first["attachments"], 0);
    let second = row(sent[3]);
    assert_eq!(second["recipient_name"], cy.name);
    assert_eq!(second["read"], false);
    let p = row(parcel);
    assert_eq!(p["read"], true, "collecting reads it");
    assert_eq!(p["collected"], true);
    assert_eq!(p["attachments"], 1, "kept after collection");
    assert_eq!(p["crowns"], 9);
    assert!(p["created_at"].is_string());

    // The read-only view: the text and the items as sent; reading it marks nothing.
    let detail = app
        .get(&format!("/api/mail/sent/{}", sent[3]), ada.token())
        .await;
    assert_eq!(detail.status, StatusCode::OK, "{:?}", detail.body);
    assert_eq!(detail.body["body"], "Dear Cy");
    assert_eq!(detail.body["items"], json!([]));
    assert_eq!(app.get("/api/mail", cy.token()).await.body["unread"], 2);
    let detail = app
        .get(&format!("/api/mail/sent/{parcel}"), ada.token())
        .await;
    let items = detail.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["item_def"], WOOL);
    assert_eq!(items[0]["quantity"], 4);
    assert_eq!(detail.body["crowns"], 9);

    // Bea's Outbox has only her letter; Cy has sent nothing.
    let bea_out = all_pages(&app, &bea, "/api/mail/sent?limit=10").await;
    assert_eq!(ids(&bea_out), vec![to_ada]);
    assert_eq!(bea_out[0]["recipient_name"], ada.name);
    assert!(
        all_pages(&app, &cy, "/api/mail/sent?limit=10")
            .await
            .is_empty()
    );
    reconcile(&app.state.pool, &[ada.id, bea.id, cy.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Only the sender lists or reads their sent letters: not the recipient,
/// not a third player, not anyone signed out; and system mail is in no
/// one's Outbox.
#[tokio::test]
async fn only_the_sender_reads_their_outbox() {
    let app = TestApp::new().await;
    let (ada, bea, cy) = (
        app.player("Serpentyne").await,
        app.player("Roarwing").await,
        app.player("Burrowsby").await,
    );
    let id = letter(&app, &ada, &bea, json!({ "body": "Secret" })).await;
    let notice = system_mail(&app, &bea).await;
    for who in [&bea, &cy] {
        let res = app.get(&format!("/api/mail/sent/{id}"), who.token()).await;
        assert_eq!(res.status, StatusCode::NOT_FOUND, "{:?}", res.body);
        assert_eq!(res.body["error"], "mail_not_found");
        assert!(!ids(&all_pages(&app, who, "/api/mail/sent?limit=10").await).contains(&id));
    }
    // A letter received is not one sent, nor is system mail.
    for (who, mail) in [(&bea, notice), (&ada, notice)] {
        let res = app
            .get(&format!("/api/mail/sent/{mail}"), who.token())
            .await;
        assert_eq!(res.status, StatusCode::NOT_FOUND);
    }
    for path in ["/api/mail/sent".to_owned(), format!("/api/mail/sent/{id}")] {
        let res = app.get(&path, None).await;
        assert_eq!(res.status, StatusCode::UNAUTHORIZED, "{path}");
    }
    // The sender still cannot open it as if it were in her own mailbox.
    let res = app.get(&format!("/api/mail/{id}"), ada.token()).await;
    assert_eq!(res.status, StatusCode::NOT_FOUND);
    let res = app.get(&format!("/api/mail/sent/{id}"), ada.token()).await;
    assert_eq!(res.status, StatusCode::OK);
    assert_eq!(res.body["body"], "Secret");
}

/// The recipient throwing a letter away takes it out of their mailbox (and
/// their unread count) only; the sender's copy stays, a retried send still
/// answers it, and system mail is still deleted outright.
#[tokio::test]
async fn the_senders_copy_survives_the_recipient_deleting_it() {
    let app = TestApp::new().await;
    let (ada, bea) = (app.player("Skytalon").await, app.player("Serpentyne").await);
    let wool = grant(&app, &ada, WOOL, 3).await[0];
    let unread = letter(
        &app,
        &ada,
        &bea,
        json!({ "subject": "Unread", "request_id": "outbox-1" }),
    )
    .await;
    let parcel = letter(
        &app,
        &ada,
        &bea,
        json!({ "subject": "Parcel", "items": [{ "item_id": wool, "quantity": 3 }] }),
    )
    .await;
    assert_eq!(app.get("/api/mail", bea.token()).await.body["unread"], 2);

    // Thrown away unread: gone from Bea's mailbox and count.
    let res = app
        .delete(&format!("/api/mail/{unread}"), bea.token())
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    let mailbox = app.get("/api/mail", bea.token()).await;
    assert_eq!(mailbox.body["unread"], 1);
    assert_eq!(ids(mailbox.body["items"].as_array().unwrap()), vec![parcel]);
    let hud = wallet::hud(&app.state.pool, bea.id).await.unwrap();
    assert_eq!(hud.unread_mail, 1);
    for res in [
        app.get(&format!("/api/mail/{unread}"), bea.token()).await,
        app.delete(&format!("/api/mail/{unread}"), bea.token())
            .await,
        app.post(
            &format!("/api/mail/{unread}/collect"),
            json!({}),
            bea.token(),
        )
        .await,
    ] {
        assert_eq!(res.status, StatusCode::NOT_FOUND, "{:?}", res.body);
    }
    // Collected, then thrown away.
    let res = app
        .post(
            &format!("/api/mail/{parcel}/collect"),
            json!({}),
            bea.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK);
    let res = app
        .delete(&format!("/api/mail/{parcel}"), bea.token())
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    assert!(
        app.get("/api/mail", bea.token()).await.body["items"]
            .as_array()
            .unwrap()
            .is_empty()
    );

    // Ada's Outbox still has both, as sent.
    let outbox = all_pages(&app, &ada, "/api/mail/sent?limit=10").await;
    assert_eq!(ids(&outbox), vec![parcel, unread]);
    assert_eq!(outbox[1]["read"], false, "thrown away unread");
    let detail = app
        .get(&format!("/api/mail/sent/{parcel}"), ada.token())
        .await;
    assert_eq!(detail.status, StatusCode::OK);
    assert_eq!(detail.body["items"][0]["quantity"], 3);
    // A retried send answers the letter it already sent.
    let retry = send(
        &TestApp::from_state(app.state.clone()),
        &ada,
        json!({ "to": bea.name, "subject": "Unread", "request_id": "outbox-1" }),
    )
    .await;
    assert_eq!(retry.status, StatusCode::CREATED, "{:?}", retry.body);
    assert_eq!(retry.body["mail"]["id"], unread);

    // System mail has no Outbox: deleting it deletes it.
    let notice = system_mail(&app, &bea).await;
    let res = app
        .delete(&format!("/api/mail/{notice}"), bea.token())
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    assert!(!mail_row_exists(&app, notice).await);
    assert!(mail_row_exists(&app, unread).await);
    reconcile(&app.state.pool, &[ada.id, bea.id])
        .await
        .unwrap()
        .assert_clean();
}

/// The Outbox keeps a letter as long as the mail cleanup does: past
/// `mail.retention_days` after nothing waits in it, it leaves the Outbox
/// too, whether or not its recipient threw it away; a letter whose
/// Crowns wait to be collected stays.
#[tokio::test]
async fn the_outbox_follows_the_mail_retention() {
    let app = TestApp::new().await;
    let (ada, bea) = (app.player("Burrowsby").await, app.player("Roarwing").await);
    let kept = letter(&app, &ada, &bea, json!({ "subject": "Kept" })).await;
    let thrown = letter(&app, &ada, &bea, json!({ "subject": "Thrown" })).await;
    let waiting = letter(
        &app,
        &ada,
        &bea,
        json!({ "subject": "Waiting", "crowns": 2 }),
    )
    .await;
    let recent = letter(&app, &ada, &bea, json!({ "subject": "Recent" })).await;
    let res = app
        .delete(&format!("/api/mail/{thrown}"), bea.token())
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT);
    let days = i32::from(shipped().economy.mail.retention_days) + 1;
    sqlx::query(
        "UPDATE mail SET created_at = now() - make_interval(days => $2),
                         collected_at = CASE WHEN collected_at IS NULL THEN NULL
                                             ELSE now() - make_interval(days => $2) END
         WHERE id = ANY($1)",
    )
    .bind(vec![kept, thrown, waiting])
    .bind(days)
    .execute(&app.state.pool)
    .await
    .unwrap();

    let cleaned = server::mail::cleanup(&app.state.pool, shipped().economy.mail.retention())
        .await
        .unwrap();
    assert!(cleaned.deleted >= 2, "{cleaned:?}");
    let outbox = ids(&all_pages(&app, &ada, "/api/mail/sent?limit=10").await);
    assert_eq!(outbox, vec![recent, waiting]);
    for id in [kept, thrown] {
        let res = app.get(&format!("/api/mail/sent/{id}"), ada.token()).await;
        assert_eq!(res.status, StatusCode::NOT_FOUND);
    }
    reconcile(&app.state.pool, &[ada.id, bea.id])
        .await
        .unwrap()
        .assert_clean();
}
