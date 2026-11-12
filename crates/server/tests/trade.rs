//! Player-to-player trading: a whole trade window over real WebSockets
//! (request, accept, offers, locks, confirmations, the commit and the
//! wallets), and the commit itself under attack: the same confirmation
//! committed many times at once, one item offered in two trades at once, an
//! item mailed away while it is offered, stacks merging on both sides, and
//! looks or purses that changed after review. Every test ends with the
//! ledger reconciliation of its characters.

mod common;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use common::{TestApp, TestPlayer};
use futures_util::{SinkExt, StreamExt};
use protocol::{
    ClientMsg, EntityId, OfferItem, PROTOCOL_VERSION, ServerMsg, TradeErrorCode, TradeOutcome,
    TradeView, decode_server, encode_client,
};
use serde_json::json;
use server::catalog::shipped;
use server::items::{self, NewItem};
use server::ledger::Reason;
use server::ledger::audit::reconcile;
use server::realtime::chat::WordFilter;
use server::trade::{self, Side};
use server::{AppState, Config, db, email, http};
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};
use tower::ServiceExt;

const WOOL: u16 = 3001;
const WAIT: Duration = Duration::from_secs(5);

/// One spawn, so every bot starts next to every other.
const LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 }
    ],
    "spawns": [{ "id": "middle", "pos": [100, 0, 100], "yaw": 0 }]
}"#;

fn purse() -> i64 {
    i64::from(shipped().economy.starting_purse)
}

async fn grant(pool: &sqlx::PgPool, owner: i64, def: u16, quantity: u32) -> i64 {
    let mut tx = pool.begin().await.unwrap();
    server::wallet::lock_characters(&mut tx, &[owner])
        .await
        .unwrap();
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

async fn quantity(pool: &sqlx::PgPool, id: i64) -> Option<(i64, i32)> {
    sqlx::query_as("SELECT owner_id, quantity FROM items WHERE id = $1")
        .bind(id)
        .fetch_optional(pool)
        .await
        .unwrap()
}

async fn held(pool: &sqlx::PgPool, owner: i64, def: u16) -> i64 {
    sqlx::query_scalar(
        "SELECT coalesce(sum(quantity), 0)::bigint FROM items WHERE owner_id = $1 AND item_def = $2",
    )
    .bind(owner)
    .bind(i32::from(def))
    .fetch_one(pool)
    .await
    .unwrap()
}

async fn balance(pool: &sqlx::PgPool, id: i64) -> i64 {
    server::ledger::balance(pool, id).await.unwrap()
}

fn side(p: &TestPlayer, items: Vec<(i64, u32)>, crowns: i64) -> Side {
    Side {
        character_id: p.id,
        items,
        crowns,
        reviewed: Vec::new(),
    }
}

fn key() -> i64 {
    (rand::random::<i64>() & i64::MAX).max(1)
}

// ------------------------------------------------------------------ the window

struct TestServer {
    addr: SocketAddr,
    state: AppState,
}

impl TestServer {
    async fn start() -> Self {
        let config = Config {
            database_url: std::env::var("DATABASE_URL")
                .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
            dev: true,
            ..Config::default()
        };
        let pool = db::connect_with(&config.database_url, 4).await.unwrap();
        let mut state = AppState::new(pool, config, Arc::new(email::DevMailer));
        state.start_realtime(
            sim_core::World::from_json(LAYOUT).unwrap(),
            WordFilter::from_list(""),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let app = http::router(state.clone());
        tokio::spawn(async move {
            axum::serve(
                listener,
                app.into_make_service_with_connect_info::<SocketAddr>(),
            )
            .await
            .unwrap();
        });
        Self { addr, state }
    }

    /// A dev bot's session token.
    async fn bot(&self) -> String {
        let name = format!("trader{:08}", rand::random::<u32>() % 100_000_000);
        let req = Request::post("/api/dev/bot-session")
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(json!({ "name": name }).to_string()))
            .unwrap();
        let res = http::router(self.state.clone()).oneshot(req).await.unwrap();
        assert_eq!(res.status(), StatusCode::OK);
        let body = axum::body::to_bytes(res.into_body(), usize::MAX)
            .await
            .unwrap();
        let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
        v["token"].as_str().unwrap().to_owned()
    }

    async fn join(&self, token: &str) -> Client {
        let (ws, _) = tokio_tungstenite::connect_async(format!("ws://{}/ws", self.addr))
            .await
            .unwrap();
        let mut c = Client { ws, id: 0 };
        c.send(&ClientMsg::Hello {
            protocol_version: PROTOCOL_VERSION,
            token: token.into(),
            character_id: None,
        })
        .await;
        c.id = c
            .expect(|m| match m {
                ServerMsg::Welcome { your_id, .. } => Some(*your_id),
                _ => None,
            })
            .await;
        c
    }
}

struct Client {
    ws: WebSocketStream<MaybeTlsStream<TcpStream>>,
    id: EntityId,
}

impl Client {
    async fn send(&mut self, msg: &ClientMsg) {
        self.ws
            .send(Message::Binary(encode_client(msg).into()))
            .await
            .unwrap();
    }

    async fn expect<T>(&mut self, mut f: impl FnMut(&ServerMsg) -> Option<T>) -> T {
        loop {
            let frame = tokio::time::timeout(WAIT, self.ws.next())
                .await
                .expect("a message in time")
                .expect("socket open")
                .unwrap();
            if let Message::Binary(b) = frame
                && let Some(t) = f(&decode_server(&b).unwrap())
            {
                return t;
            }
        }
    }

    async fn view(&mut self) -> TradeView {
        self.expect(|m| match m {
            ServerMsg::TradeState { view } => Some(view.clone()),
            _ => None,
        })
        .await
    }

    async fn result(&mut self) -> TradeOutcome {
        self.expect(|m| match m {
            ServerMsg::TradeResult { outcome, .. } => Some(*outcome),
            _ => None,
        })
        .await
    }
}

async fn character_of(state: &AppState, token: &str) -> i64 {
    let app = TestApp::from_state(state.clone());
    app.get("/api/characters/me", Some(token)).await.body["id"]
        .as_i64()
        .unwrap()
}

#[tokio::test]
async fn a_trade_window_moves_items_and_crowns_once_both_confirm() {
    let server = TestServer::start().await;
    let (ta, tb) = (server.bot().await, server.bot().await);
    let mut a = server.join(&ta).await;
    let mut b = server.join(&tb).await;
    let pool = server.state.pool.clone();
    let (ca, cb) = (
        character_of(&server.state, &ta).await,
        character_of(&server.state, &tb).await,
    );
    let wool = grant(&pool, ca, WOOL, 30).await;
    let hat = grant(&pool, cb, 101, 1).await;

    a.send(&ClientMsg::TradeRequest { target: b.id }).await;
    let trade_id = b
        .expect(|m| match m {
            ServerMsg::TradeRequested { trade_id, from, .. } if *from == a.id => Some(*trade_id),
            _ => None,
        })
        .await;
    b.send(&ClientMsg::TradeRespond {
        trade_id,
        accept: true,
    })
    .await;
    assert_eq!(a.view().await.partner, b.id);
    b.view().await;

    a.send(&ClientMsg::TradeSetOffer {
        trade_id,
        items: vec![OfferItem {
            item_id: wool,
            quantity: 10,
        }],
        crowns: 20,
    })
    .await;
    let seen = b.view().await;
    assert_eq!(seen.theirs.crowns, 20);
    assert_eq!(seen.theirs.items[0].quantity, 10);
    assert_eq!(seen.theirs.items[0].item_def, WOOL);
    a.view().await;
    b.send(&ClientMsg::TradeSetOffer {
        trade_id,
        items: vec![OfferItem {
            item_id: hat,
            quantity: 1,
        }],
        crowns: 0,
    })
    .await;
    a.view().await;
    b.view().await;
    // Offering more than you have is refused, the window unchanged.
    b.send(&ClientMsg::TradeSetOffer {
        trade_id,
        items: vec![],
        crowns: purse() + 1,
    })
    .await;
    let code = b
        .expect(|m| match m {
            ServerMsg::TradeError { code, .. } => Some(*code),
            _ => None,
        })
        .await;
    assert_eq!(code, TradeErrorCode::InsufficientCrowns);

    for c in [&mut a, &mut b] {
        c.send(&ClientMsg::TradeLock {
            trade_id,
            locked: true,
        })
        .await;
    }
    // Each side sees two changes (its own lock and the other's).
    let rev = {
        a.view().await;
        a.view().await.rev
    };
    b.view().await;
    b.view().await;
    for c in [&mut a, &mut b] {
        c.send(&ClientMsg::TradeConfirm { trade_id, rev }).await;
    }
    assert_eq!(a.result().await, TradeOutcome::Completed);
    assert_eq!(b.result().await, TradeOutcome::Completed);
    let wallet = b
        .expect(|m| match m {
            ServerMsg::Wallet { crowns } => Some(*crowns),
            _ => None,
        })
        .await;
    assert_eq!(wallet, purse() + 20);

    assert_eq!(held(&pool, ca, WOOL).await, 20);
    assert_eq!(held(&pool, cb, WOOL).await, 10);
    assert_eq!(quantity(&pool, hat).await.unwrap().0, ca);
    assert_eq!(balance(&pool, ca).await, purse() - 20);
    // Both see it in their history.
    let app = TestApp::from_state(server.state.clone());
    let history = app.get("/api/trades", Some(&tb)).await;
    let record = &history.body["items"][0];
    assert_eq!(record["partner"]["character_id"], ca);
    assert_eq!(record["got"]["crowns"], 20);
    assert_eq!(record["got"]["items"][0]["quantity"], 10);
    assert_eq!(record["gave"]["items"][0]["item_def"], 101);
    let theirs = app.get("/api/trades", Some(&ta)).await;
    assert_eq!(theirs.body["items"][0]["id"], record["id"]);
    assert_eq!(theirs.body["items"][0]["gave"]["crowns"], 20);
    reconcile(&pool, &[ca, cb]).await.unwrap().assert_clean();
}

// ------------------------------------------------------------------ the commit

/// Duplication hunt: the same confirmed trade committed by six jobs at
/// once (a retried job, a double confirmation) moves everything once.
#[tokio::test]
async fn one_trade_key_commits_once_however_often_it_is_sent() {
    let app = TestApp::new().await;
    let (a, b) = (app.player("Roarwing").await, app.player("Serpentyne").await);
    let pool = app.state.pool.clone();
    let wool = grant(&pool, a.id, WOOL, 50).await;
    let k = key();
    let (sa, sb) = (side(&a, vec![(wool, 20)], 7), side(&b, vec![], 30));
    let racers: Vec<_> = (0..6)
        .map(|_| {
            let (pool, sa, sb) = (pool.clone(), sa.clone(), sb.clone());
            tokio::spawn(async move { trade::commit(&pool, k, &sa, &sb).await })
        })
        .collect();
    let mut ids = Vec::new();
    for r in racers {
        ids.push(r.await.unwrap().expect("committed"));
    }
    ids.dedup();
    assert_eq!(ids.len(), 1, "one trade row");
    assert_eq!(held(&pool, a.id, WOOL).await, 30);
    assert_eq!(held(&pool, b.id, WOOL).await, 20);
    assert_eq!(balance(&pool, a.id).await, purse() - 7 + 30);
    assert_eq!(balance(&pool, b.id).await, purse() + 7 - 30);
    reconcile(&pool, &[a.id, b.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Duplication hunt: one stack offered in two trades confirmed at the same
/// moment goes to one partner only.
#[tokio::test]
async fn one_item_in_two_trades_at_once_goes_once() {
    let app = TestApp::new().await;
    let (a, b, c) = (
        app.player("Burrowsby").await,
        app.player("Skytalon").await,
        app.player("Roarwing").await,
    );
    let pool = app.state.pool.clone();
    for round in 0..5 {
        let hat = grant(&pool, a.id, 101, 1).await;
        let t1 = {
            let pool = pool.clone();
            let (sa, sb) = (side(&a, vec![(hat, 1)], 0), side(&b, vec![], 1));
            tokio::spawn(async move { trade::commit(&pool, key(), &sa, &sb).await })
        };
        let t2 = {
            let pool = pool.clone();
            let (sa, sc) = (side(&a, vec![(hat, 1)], 0), side(&c, vec![], 1));
            tokio::spawn(async move { trade::commit(&pool, key(), &sa, &sc).await })
        };
        let (r1, r2) = (t1.await.unwrap(), t2.await.unwrap());
        let ok = usize::from(r1.is_ok()) + usize::from(r2.is_ok());
        assert_eq!(ok, 1, "round {round}: {r1:?} {r2:?}");
        assert!(
            [r1, r2].contains(&Err(TradeErrorCode::ItemUnavailable)),
            "round {round}"
        );
        let owner = quantity(&pool, hat).await.unwrap().0;
        assert!(owner == b.id || owner == c.id);
    }
    // Five Crowns in, one per hat; both partners together paid five.
    assert_eq!(balance(&pool, a.id).await, purse() + 5);
    assert_eq!(
        balance(&pool, b.id).await + balance(&pool, c.id).await,
        2 * purse() - 5
    );
    reconcile(&pool, &[a.id, b.id, c.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Duplication hunt: an item mailed away while it is offered in a trade
/// window. Whichever commits first wins; the other moves nothing.
#[tokio::test]
async fn an_item_mailed_while_offered_in_a_trade_goes_once() {
    let app = TestApp::new().await;
    let (a, b, c) = (
        app.player("Serpentyne").await,
        app.player("Burrowsby").await,
        app.player("Skytalon").await,
    );
    let pool = app.state.pool.clone();
    // In order: mailed first, so the trade finds it gone and moves nothing.
    let wool = grant(&pool, a.id, WOOL, 10).await;
    let res = app
        .post(
            "/api/mail",
            json!({ "to": c.name, "subject": "Yours", "items": [{ "item_id": wool, "quantity": 10 }] }),
            a.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED);
    let before = (balance(&pool, a.id).await, balance(&pool, b.id).await);
    let refused = trade::commit(
        &pool,
        key(),
        &side(&a, vec![(wool, 10)], 0),
        &side(&b, vec![], 5),
    )
    .await;
    assert_eq!(refused, Err(TradeErrorCode::ItemUnavailable));
    assert_eq!(
        before,
        (balance(&pool, a.id).await, balance(&pool, b.id).await)
    );

    // At the same moment, repeatedly.
    for round in 0..5 {
        let wool = grant(&pool, a.id, WOOL, 6).await;
        let trade = {
            let pool = pool.clone();
            let (sa, sb) = (side(&a, vec![(wool, 6)], 0), side(&b, vec![], 1));
            tokio::spawn(async move { trade::commit(&pool, key(), &sa, &sb).await })
        };
        let letter = {
            let app = TestApp::from_state(app.state.clone());
            let (a, c) = (a.clone(), c.clone());
            tokio::spawn(async move {
                app.post(
                    "/api/mail",
                    json!({ "to": c.name, "subject": "Race", "items": [{ "item_id": wool, "quantity": 6 }] }),
                    a.token(),
                )
                .await
                .status
            })
        };
        let traded = trade.await.unwrap().is_ok();
        let mailed = letter.await.unwrap() == StatusCode::CREATED;
        assert!(
            traded != mailed,
            "round {round}: exactly one ({traded}, {mailed})"
        );
    }
    reconcile(&pool, &[a.id, b.id, c.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Stacks: received units merge into the receiver's stacks, never into a
/// stack the receiver is giving away, and two received stacks of one kind
/// merge without losing a unit.
#[tokio::test]
async fn traded_stacks_merge_without_losing_or_making_units() {
    let app = TestApp::new().await;
    let (a, b) = (app.player("Roarwing").await, app.player("Skytalon").await);
    let pool = app.state.pool.clone();
    // A: two separate stacks of wool (a full one forces the second).
    let first = grant(&pool, a.id, WOOL, 99).await;
    let second = grant(&pool, a.id, WOOL, 12).await;
    assert_ne!(first, second);
    // B: a stack with little room left, of which B gives some to A.
    let theirs = grant(&pool, b.id, WOOL, 95).await;
    trade::commit(
        &pool,
        key(),
        &side(&a, vec![(first, 7), (second, 12)], 0),
        &side(&b, vec![(theirs, 95)], 0),
    )
    .await
    .unwrap();
    assert_eq!(held(&pool, a.id, WOOL).await, 99 - 7 + 95);
    assert_eq!(held(&pool, b.id, WOOL).await, 7 + 12);
    // No stack over its limit, no empty rows.
    let rows: Vec<(i64, i32)> = sqlx::query_as(
        "SELECT owner_id, quantity FROM items WHERE owner_id = ANY($1) AND item_def = $2",
    )
    .bind([a.id, b.id])
    .bind(i32::from(WOOL))
    .fetch_all(&pool)
    .await
    .unwrap();
    assert!(rows.iter().all(|&(_, q)| (1..=99).contains(&q)), "{rows:?}");
    assert_eq!(
        rows.iter().filter(|r| r.0 == b.id).count(),
        1,
        "merged: {rows:?}"
    );
    reconcile(&pool, &[a.id, b.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn trades_that_no_longer_hold_are_refused_whole() {
    let app = TestApp::new().await;
    let (a, b) = (
        app.player("Burrowsby").await,
        app.player("Serpentyne").await,
    );
    let pool = app.state.pool.clone();
    let hat = grant(&pool, a.id, 101, 1).await;
    let wool = grant(&pool, b.id, WOOL, 5).await;
    let snapshot = || async {
        (
            balance(&pool, a.id).await,
            balance(&pool, b.id).await,
            quantity(&pool, hat).await,
            quantity(&pool, wool).await,
        )
    };
    let before = snapshot().await;
    let cases = [
        // More Crowns than B has.
        (
            side(&a, vec![(hat, 1)], 0),
            side(&b, vec![(wool, 5)], purse() + 1),
            TradeErrorCode::InsufficientCrowns,
        ),
        // More wool than B has.
        (
            side(&a, vec![(hat, 1)], 0),
            side(&b, vec![(wool, 6)], 0),
            TradeErrorCode::ItemUnavailable,
        ),
        // Someone else's item.
        (
            side(&a, vec![(wool, 1)], 0),
            side(&b, vec![], 0),
            TradeErrorCode::ItemUnavailable,
        ),
        // Malformed.
        (
            side(&a, vec![(hat, 1), (hat, 1)], 0),
            side(&b, vec![], 0),
            TradeErrorCode::InvalidOffer,
        ),
        (
            side(&a, vec![], -5),
            side(&b, vec![], 0),
            TradeErrorCode::InvalidOffer,
        ),
        (
            side(&a, vec![], 0),
            side(&a, vec![], 0),
            TradeErrorCode::SelfTrade,
        ),
    ];
    for (sa, sb, code) in cases {
        assert_eq!(trade::commit(&pool, key(), &sa, &sb).await, Err(code));
    }
    // The hat was dyed after B reviewed it: refused.
    let mut reviewed = side(&a, vec![(hat, 1)], 0);
    let item = items::lock(&mut pool.acquire().await.unwrap(), a.id, &[hat])
        .await
        .unwrap()
        .remove(0);
    reviewed.reviewed = vec![protocol::OfferedItem {
        item_id: hat,
        item_def: item.item_def,
        dyes: [item.dyes[0].wrapping_add(1), item.dyes[1], item.dyes[2]],
        finish: item.finish,
        quantity: 1,
        maker: None,
    }];
    assert_eq!(
        trade::commit(&pool, key(), &reviewed, &side(&b, vec![], 1)).await,
        Err(TradeErrorCode::ItemUnavailable)
    );
    // As reviewed, it goes through.
    reviewed.reviewed[0].dyes = item.dyes;
    trade::commit(&pool, key(), &reviewed, &side(&b, vec![], 1))
        .await
        .unwrap();
    let after = snapshot().await;
    assert_eq!(before.3, after.3, "B's wool untouched by every refusal");
    assert_eq!(after.2.unwrap().0, b.id);
    reconcile(&pool, &[a.id, b.id])
        .await
        .unwrap()
        .assert_clean();
}
