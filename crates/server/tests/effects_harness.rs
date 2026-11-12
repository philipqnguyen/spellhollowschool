//! A small end-to-end harness for the potion and find tests (included by
//! `potions.rs` and `finds.rs` as a module; on its own it has no tests): a
//! real server on a random local port with the world running, dev bot
//! sessions, real WebSocket clients and the test database (`DATABASE_URL`,
//! default `postgres://localhost/shs_test`).
//!
//! The castle is a 200 m hall whose spawn stands on a real find spot of
//! `data/world/finds.json` and next to a classroom cauldron, so bots can
//! search and brew where they appear; a knitting corner stands far away.

#![allow(dead_code)]

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use futures_util::{SinkExt, StreamExt};
use protocol::{
    Appearance, ClientMsg, EntityId, PROTOCOL_VERSION, ServerMsg, decode_server, encode_client,
};
use rand::Rng;
use server::items::{self, NewItem};
use server::ledger::Reason;
use server::realtime::chat::WordFilter;
use server::{AppState, Config, db, email, http};
use sqlx::PgPool;
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};
use tower::ServiceExt;

/// A find spot of the shipped finds.json, placed at the spawn.
pub const SPOT: &str = "find_eh_west_alcove";

const LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 }
    ],
    "spawns": [{ "id": "south", "pos": [100, 0, 100], "yaw": 0 }],
    "interactables": [
        { "id": "find_eh_west_alcove", "kind": "find_spot", "pos": [100, 0, 100], "radius": 2.0 },
        { "id": "find_far_away", "kind": "find_spot", "pos": [10, 0, 10], "radius": 1.0 },
        { "id": "cauldron_classroom_1", "kind": "cauldron", "pos": [101, 0, 100], "radius": 2.0 },
        { "id": "craft_charms_far", "kind": "craft_charms", "pos": [10, 0, 190], "radius": 1.5 }
    ]
}"#;

/// Long enough for a slow machine, short enough to fail fast.
pub const WAIT: Duration = Duration::from_secs(5);

pub struct TestServer {
    pub addr: SocketAddr,
    pub state: AppState,
}

impl TestServer {
    pub async fn start() -> Self {
        Self::start_with(sim_core::World::from_json(LAYOUT).unwrap()).await
    }

    /// The same server over another castle (the shipped one, say).
    pub async fn start_with(castle: sim_core::World) -> Self {
        let config = Config {
            database_url: std::env::var("DATABASE_URL")
                .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
            dev: true,
            ..Config::default()
        };
        let pool = db::connect_with(&config.database_url, 6)
            .await
            .expect("test database reachable (createdb shs_test)");
        let mut state = AppState::new(pool, config, Arc::new(email::DevMailer));
        state.start_realtime(castle, WordFilter::from_list("badword"));
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

    pub fn pool(&self) -> &PgPool {
        &self.state.pool
    }

    /// A fresh bot account with a character in the castle: (name, token, id).
    pub async fn bot(&self) -> Bot {
        let name = format!("bot{:08}", rand::thread_rng().gen_range(0..100_000_000));
        let req = Request::post("/api/dev/bot-session")
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(serde_json::json!({ "name": name }).to_string()))
            .unwrap();
        let res = http::router(self.state.clone()).oneshot(req).await.unwrap();
        assert_eq!(res.status(), StatusCode::OK);
        let body = axum::body::to_bytes(res.into_body(), usize::MAX)
            .await
            .unwrap();
        let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
        let token = v["token"].as_str().unwrap().to_owned();
        let id = sqlx::query_scalar("SELECT id FROM characters WHERE name = $1")
            .bind(&name)
            .fetch_one(self.pool())
            .await
            .unwrap();
        Bot { name, token, id }
    }

    /// Connects, says Hello and waits for Welcome.
    pub async fn join(&self, bot: &Bot) -> Client {
        let (ws, _) = tokio_tungstenite::connect_async(format!("ws://{}/ws", self.addr))
            .await
            .expect("websocket connects");
        let mut c = Client {
            ws,
            id: 0,
            appearance: Appearance::default(),
        };
        c.send(&ClientMsg::Hello {
            protocol_version: PROTOCOL_VERSION,
            token: bot.token.clone(),
            character_id: None,
        })
        .await;
        (c.id, c.appearance) = c
            .expect(|m| match m {
                ServerMsg::Welcome {
                    your_id,
                    appearance,
                    ..
                } => Some((*your_id, *appearance)),
                _ => None,
            })
            .await;
        c
    }

    /// Gives `quantity` units of `item_def` with `dyes` (the catalog's when
    /// `None`); returns the first row.
    pub async fn give(
        &self,
        owner: i64,
        item_def: u16,
        quantity: u32,
        dyes: Option<[u8; 3]>,
    ) -> i64 {
        let mut item = NewItem::plain(owner, item_def, quantity, "inventory");
        if let Some(d) = dyes {
            item.dyes = d;
        }
        let mut tx = self.pool().begin().await.unwrap();
        let rows = items::give(&mut tx, &item, Reason::Admin, None)
            .await
            .unwrap();
        tx.commit().await.unwrap();
        rows[0]
    }

    pub async fn befriend(&self, a: i64, b: i64) {
        sqlx::query("INSERT INTO friendships (character_id, friend_id) VALUES ($1, $2), ($2, $1)")
            .bind(a)
            .bind(b)
            .execute(self.pool())
            .await
            .unwrap();
    }

    /// Units of `item_def` the character holds anywhere.
    pub async fn units(&self, owner: i64, item_def: u16) -> i64 {
        sqlx::query_scalar(
            "SELECT COALESCE(SUM(quantity), 0)::bigint FROM items
             WHERE owner_id = $1 AND item_def = $2",
        )
        .bind(owner)
        .bind(i32::from(item_def))
        .fetch_one(self.pool())
        .await
        .unwrap()
    }

    /// An HTTP GET as the bot; the JSON body.
    pub async fn get(&self, bot: &Bot, path: &str) -> serde_json::Value {
        let req = Request::get(path)
            .header(header::AUTHORIZATION, format!("Bearer {}", bot.token))
            .body(Body::empty())
            .unwrap();
        let res = http::router(self.state.clone()).oneshot(req).await.unwrap();
        assert_eq!(res.status(), StatusCode::OK);
        let body = axum::body::to_bytes(res.into_body(), usize::MAX)
            .await
            .unwrap();
        serde_json::from_slice(&body).unwrap()
    }
}

impl TestServer {
    /// An HTTP POST as the bot: (status, JSON body).
    pub async fn post(
        &self,
        bot: &Bot,
        path: &str,
        body: serde_json::Value,
    ) -> (StatusCode, serde_json::Value) {
        let req = Request::post(path)
            .header(header::AUTHORIZATION, format!("Bearer {}", bot.token))
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(body.to_string()))
            .unwrap();
        let res = http::router(self.state.clone()).oneshot(req).await.unwrap();
        let status = res.status();
        let bytes = axum::body::to_bytes(res.into_body(), usize::MAX)
            .await
            .unwrap();
        (status, serde_json::from_slice(&bytes).unwrap_or_default())
    }
}

pub struct Bot {
    pub name: String,
    pub token: String,
    pub id: i64,
}

pub struct Client {
    ws: WebSocketStream<MaybeTlsStream<TcpStream>>,
    pub id: EntityId,
    /// The character's own look, from `Welcome`.
    pub appearance: Appearance,
}

impl Client {
    pub async fn send(&mut self, msg: &ClientMsg) {
        self.ws
            .send(Message::Binary(encode_client(msg).into()))
            .await
            .unwrap();
    }

    /// The next server message; `None` once the socket closes.
    pub async fn recv(&mut self) -> Option<ServerMsg> {
        loop {
            match tokio::time::timeout(WAIT, self.ws.next()).await {
                Err(_) => panic!("no message within {WAIT:?}"),
                Ok(None | Some(Err(_))) => return None,
                Ok(Some(Ok(Message::Binary(b)))) => return Some(decode_server(&b).unwrap()),
                Ok(Some(Ok(Message::Close(_)))) => return None,
                Ok(Some(Ok(_))) => continue,
            }
        }
    }

    /// Skips messages until `f` picks one.
    pub async fn expect<T>(&mut self, mut f: impl FnMut(&ServerMsg) -> Option<T>) -> T {
        loop {
            let msg = self.recv().await.expect("connection still open");
            if let Some(t) = f(&msg) {
                return t;
            }
        }
    }

    pub async fn close(mut self) {
        let _ = self.ws.close(None).await;
        while self.recv().await.is_some() {}
    }
}

/// Polls until `f` holds.
pub async fn eventually<F, Fut>(mut f: F)
where
    F: FnMut() -> Fut,
    Fut: std::future::Future<Output = bool>,
{
    for _ in 0..50 {
        if f().await {
            return;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("condition not reached");
}
