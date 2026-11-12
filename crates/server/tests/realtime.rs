//! End-to-end tests of the WebSocket gateway and the castle world: a real
//! server on a random local port, real WebSocket clients (tokio-tungstenite)
//! signed in with dev bot sessions, and the test Postgres database
//! (`DATABASE_URL`, default `postgres://localhost/shs_test`).
//!
//! The world uses a small synthetic layout so positions are predictable: a
//! 200 m square hall with two spawns 60 m apart.

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::extract::connect_info::MockConnectInfo;
use axum::http::{Request, StatusCode, header};
use futures_util::{SinkExt, StreamExt};
use protocol::{
    Anim, ChatChannel, ChatRejectReason, ClientMsg, DormErrorCode, Emote, EntityEntry, EntityId,
    FriendEvent, FurnitureEdit, KickCode, Location, LookCache, PROTOCOL_VERSION, QPos, ServerMsg,
    SnapshotReceiver, Zone, decode_server, encode_client,
};
use rand::Rng;
use server::realtime::chat::WordFilter;
use server::realtime::{KICK_PROTOCOL, KICK_REPLACED, KICK_RESTART, KICK_SESSION, KICK_VERSION};
use server::{AppState, Config, db, email, http};
use sqlx::PgPool;
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};
use tower::ServiceExt;

const LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 },
        { "id": "common_r", "kind": "room", "name": "Red Common Room", "level": 0, "cell": "c",
          "min": [200, 90], "max": [220, 110], "y": 0, "ceiling": 6, "house": "roarwing" }
    ],
    "spawns": [
        { "id": "south", "pos": [100, 0, 100], "yaw": 0 },
        { "id": "north", "pos": [100, 0, 160], "yaw": 0 }
    ],
    "interactables": [
        { "id": "dorm_door_r", "kind": "dorm_door", "house": "roarwing",
          "pos": [215, 0, 100], "yaw": 1.5707964, "radius": 1.5 }
    ]
}"#;

/// Long enough for a slow CI machine, short enough to fail fast.
const WAIT: Duration = Duration::from_secs(5);

struct TestServer {
    addr: SocketAddr,
    state: AppState,
}

impl TestServer {
    async fn start(player_cap: u32) -> Self {
        Self::start_with(player_cap, |_| {}).await
    }

    async fn start_with(player_cap: u32, tweak: impl FnOnce(&mut Config)) -> Self {
        let mut config = Config {
            database_url: std::env::var("DATABASE_URL")
                .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
            dev: true,
            player_cap,
            ..Config::default()
        };
        tweak(&mut config);
        let pool = db::connect_with(&config.database_url, 4)
            .await
            .expect("test database reachable (createdb shs_test)");
        let mut state = AppState::new(pool, config, Arc::new(email::DevMailer));
        state.start_realtime(
            sim_core::World::from_json(LAYOUT).unwrap(),
            WordFilter::from_list("badword"),
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

    /// A fresh bot account with a character; returns (name, token).
    async fn bot(&self) -> (String, String) {
        let name = format!("bot{:08}", rand::thread_rng().gen_range(0..100_000_000));
        let token = self.bot_named(&name).await;
        (name, token)
    }

    /// A session for the bot called `name` (the same account each time).
    async fn bot_named(&self, name: &str) -> String {
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
        v["token"].as_str().unwrap().to_owned()
    }

    /// A JSON request through the router (as from one client address);
    /// returns the status and body.
    async fn api(
        &self,
        path: &str,
        body: serde_json::Value,
        token: Option<&str>,
    ) -> (StatusCode, serde_json::Value) {
        let mut req = Request::post(path).header(header::CONTENT_TYPE, "application/json");
        if let Some(t) = token {
            req = req.header(header::AUTHORIZATION, format!("Bearer {t}"));
        }
        let req = req.body(Body::from(body.to_string())).unwrap();
        let res = http::router(self.state.clone())
            .layer(MockConnectInfo(SocketAddr::from((
                [203, 0, 113, 8],
                50_000,
            ))))
            .oneshot(req)
            .await
            .unwrap();
        let status = res.status();
        let bytes = axum::body::to_bytes(res.into_body(), usize::MAX)
            .await
            .unwrap();
        (status, serde_json::from_slice(&bytes).unwrap_or_default())
    }

    async fn socket(&self) -> Client {
        Client::connect(self.addr).await
    }

    /// Connects, says Hello and waits for Welcome.
    async fn join(&self, token: &str) -> Client {
        let mut c = self.socket().await;
        c.hello(token).await;
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
    /// Applies snapshot deltas, as the browser's decoder does.
    snapshots: SnapshotReceiver,
    /// Resolves cached entries, as the browser's decoder does.
    looks: LookCache,
    next_chat: u32,
}

impl Client {
    async fn connect(addr: SocketAddr) -> Self {
        let (ws, _) = tokio_tungstenite::connect_async(format!("ws://{addr}/ws"))
            .await
            .expect("websocket connects");
        Client {
            ws,
            id: 0,
            snapshots: SnapshotReceiver::new(),
            looks: LookCache::new(),
            next_chat: 0,
        }
    }

    /// Tracks the visible set; a snapshot comes back with `full` holding
    /// every state it updated, absolute (deltas applied), no deltas, and
    /// every entry resolved to `EntityEntry::Full`.
    fn track(&mut self, msg: ServerMsg) -> ServerMsg {
        match msg {
            ServerMsg::Welcome { .. } => {
                self.snapshots.reset();
                self.looks.clear();
                msg
            }
            ServerMsg::ZoneChanged { .. } => {
                self.snapshots.reset();
                msg
            }
            ServerMsg::Snapshot {
                tick,
                entered,
                full,
                deltas,
                left,
                looks,
            } => {
                let mut infos = Vec::new();
                self.looks
                    .resolve(&entered, &mut infos)
                    .expect("cached entries resolve");
                let mut states = Vec::new();
                self.snapshots
                    .apply(
                        entered.iter().map(EntityEntry::id),
                        &full,
                        &deltas,
                        &left,
                        &mut states,
                    )
                    .expect("snapshots stay in sync");
                ServerMsg::Snapshot {
                    tick,
                    entered: infos.into_iter().map(EntityEntry::Full).collect(),
                    full: states,
                    deltas: Vec::new(),
                    left,
                    looks,
                }
            }
            other => other,
        }
    }

    /// Hello, then the `(id, spawn)` from `Welcome`.
    async fn sign_in(&mut self, token: &str) -> (EntityId, QPos) {
        self.hello(token).await;
        let welcome = self
            .expect(|m| match m {
                ServerMsg::Welcome { your_id, spawn, .. } => Some((*your_id, *spawn)),
                _ => None,
            })
            .await;
        self.id = welcome.0;
        welcome
    }

    async fn send(&mut self, msg: &ClientMsg) {
        self.ws
            .send(Message::Binary(encode_client(msg).into()))
            .await
            .unwrap();
    }

    async fn hello(&mut self, token: &str) {
        self.send(&ClientMsg::Hello {
            protocol_version: PROTOCOL_VERSION,
            token: token.into(),
            character_id: None,
        })
        .await;
    }

    /// The next server message; `None` once the socket closes.
    async fn recv(&mut self) -> Option<ServerMsg> {
        loop {
            match tokio::time::timeout(WAIT, self.ws.next()).await {
                Err(_) => panic!("no message within {WAIT:?}"),
                Ok(None | Some(Err(_))) => return None,
                Ok(Some(Ok(Message::Binary(b)))) => {
                    return Some(self.track(decode_server(&b).unwrap()));
                }
                Ok(Some(Ok(Message::Close(_)))) => return None,
                Ok(Some(Ok(_))) => continue,
            }
        }
    }

    /// Skips messages until `f` picks one.
    async fn expect<T>(&mut self, mut f: impl FnMut(&ServerMsg) -> Option<T>) -> T {
        loop {
            let msg = self.recv().await.expect("connection still open");
            if let Some(t) = f(&msg) {
                return t;
            }
        }
    }

    /// The next chat line (skipping snapshots and rosters).
    async fn chat(&mut self) -> (EntityId, ChatChannel, String) {
        self.expect(|m| match m {
            ServerMsg::Chat {
                from,
                channel,
                text,
                ..
            } => Some((*from, channel.clone(), text.clone())),
            _ => None,
        })
        .await
    }

    async fn say(&mut self, channel: ChatChannel, text: &str) {
        self.next_chat += 1;
        let client_seq = self.next_chat;
        self.send(&ClientMsg::Chat {
            channel,
            text: text.into(),
            client_seq,
        })
        .await;
    }

    async fn move_to(&mut self, seq: u32, pos: [f32; 3]) {
        self.send(&ClientMsg::Move {
            seq,
            pos,
            yaw: 0.0,
            anim: Anim::Walk,
        })
        .await;
    }

    /// Pings and waits for the pong: the connection is still open.
    async fn still_open(&mut self) {
        self.send(&ClientMsg::Ping { client_time_ms: 7 }).await;
        self.expect(|m| match m {
            ServerMsg::Pong {
                client_time_ms: 7, ..
            } => Some(()),
            ServerMsg::Kick { code, .. } => panic!("kicked: {code:?}"),
            _ => None,
        })
        .await;
    }

    /// The `Kick` reason, then the socket must close.
    async fn kicked(&mut self) -> String {
        self.kicked_with().await.1
    }

    /// The `Kick` code and reason, then the socket must close.
    async fn kicked_with(&mut self) -> (KickCode, String) {
        let kick = self
            .expect(|m| match m {
                ServerMsg::Kick { code, reason } => Some((*code, reason.clone())),
                _ => None,
            })
            .await;
        while self.recv().await.is_some() {}
        kick
    }

    /// The next `ChatRejected` (skipping everything else).
    async fn chat_rejected(&mut self) -> (u32, ChatRejectReason) {
        self.expect(|m| match m {
            ServerMsg::ChatRejected { client_seq, reason } => Some((*client_seq, *reason)),
            _ => None,
        })
        .await
    }

    /// The next `DormError` code.
    async fn dorm_error(&mut self) -> DormErrorCode {
        self.expect(|m| match m {
            ServerMsg::DormError { code, .. } => Some(*code),
            _ => None,
        })
        .await
    }

    /// The next furniture edit in the room.
    async fn furniture(&mut self) -> FurnitureEdit {
        self.expect(|m| match m {
            ServerMsg::DormFurniture { edit } => Some(*edit),
            _ => None,
        })
        .await
    }
}

#[tokio::test]
async fn players_see_each_other_move_and_chat() {
    let server = TestServer::start(1000).await;
    let (_, ta) = server.bot().await;
    let (b_name, tb) = server.bot().await;
    let (_, tc) = server.bot().await;
    // Spawns alternate: A and C at the south spawn, B 60 m north.
    let mut a = server.join(&ta).await;
    let mut b = server.join(&tb).await;
    let mut c = server.join(&tc).await;

    // B's view fills with A (and C).
    let a_id = a.id;
    b.expect(|m| match m {
        ServerMsg::Snapshot { entered, .. } => entered.iter().any(|e| e.id() == a_id).then_some(()),
        _ => None,
    })
    .await;

    // A walks half a metre east; B sees it.
    a.move_to(1, [100.5, 0.0, 100.0]).await;
    let want = QPos::from_m([100.5, 0.0, 100.0]);
    b.expect(|m| match m {
        ServerMsg::Snapshot { full, .. } => full
            .iter()
            .any(|s| s.id == a_id && s.pos == want && s.anim == Anim::Walk)
            .then_some(()),
        _ => None,
    })
    .await;

    // Say reaches C next to A, and A itself, but not B 60 m away.
    a.say(ChatChannel::Say, "hello there").await;
    let heard = (a.id, ChatChannel::Say, "hello there".to_owned());
    assert_eq!(c.chat().await, heard);
    assert_eq!(a.chat().await, heard);

    // A line the filter refuses reaches no one; A is told which line.
    a.say(ChatChannel::Say, "hello there badword").await;
    let refused = a.next_chat;
    assert_eq!(
        a.chat_rejected().await,
        (refused, ChatRejectReason::Filtered)
    );

    // A whisper reaches B (whose first chat line this is: no Say before it).
    a.say(
        ChatChannel::Whisper {
            to: b_name.to_uppercase(),
        },
        "psst",
    )
    .await;
    let whisper = (
        a.id,
        ChatChannel::Whisper { to: b_name.clone() },
        "psst".to_owned(),
    );
    assert_eq!(b.chat().await, whisper);
    assert_eq!(a.chat().await, whisper);

    // C's next line is the next Say, so it never got the whisper.
    a.say(ChatChannel::Say, "sync").await;
    assert_eq!(c.chat().await.2, "sync");
    assert_eq!(a.chat().await.2, "sync");

    // Whispering someone offline is refused.
    a.say(
        ChatChannel::Whisper {
            to: "nobody".into(),
        },
        "hi",
    )
    .await;
    assert_eq!(
        a.chat_rejected().await,
        (a.next_chat, ChatRejectReason::UnknownTarget)
    );

    // A teleport across the hall is corrected back to the last accepted spot.
    a.move_to(2, [150.0, 0.0, 100.0]).await;
    let corrected = a
        .expect(|m| match m {
            ServerMsg::Correction { seq, pos } => Some((*seq, *pos)),
            _ => None,
        })
        .await;
    assert_eq!(corrected, (2, want));

    // Ping is answered.
    a.send(&ClientMsg::Ping { client_time_ms: 42 }).await;
    a.expect(|m| {
        matches!(
            m,
            ServerMsg::Pong {
                client_time_ms: 42,
                ..
            }
        )
        .then_some(())
    })
    .await;

    // Rosters arrive about once a second.
    let online = a
        .expect(|m| match m {
            ServerMsg::Roster { online, .. } => Some(*online),
            _ => None,
        })
        .await;
    assert_eq!(online, 3);

    // B leaving shows up in A's snapshots.
    let b_id = b.id;
    drop(b);
    a.expect(|m| match m {
        ServerMsg::Snapshot { left, .. } => left.contains(&b_id).then_some(()),
        _ => None,
    })
    .await;
}

#[tokio::test]
async fn logins_queue_at_the_player_cap() {
    let server = TestServer::start(1).await;
    let (_, ta) = server.bot().await;
    let (_, tb) = server.bot().await;
    let (_, tc) = server.bot().await;
    let a = server.join(&ta).await;

    let mut b = server.socket().await;
    b.hello(&tb).await;
    assert_eq!(b.recv().await, Some(ServerMsg::Queued { position: 1 }));
    let mut c = server.socket().await;
    c.hello(&tc).await;
    assert_eq!(c.recv().await, Some(ServerMsg::Queued { position: 2 }));
    assert_eq!(
        server
            .state
            .realtime
            .as_ref()
            .unwrap()
            .admission
            .queue_len(),
        2
    );

    // A leaves: B is admitted and C moves up.
    drop(a);
    b.expect(|m| matches!(m, ServerMsg::Welcome { .. }).then_some(()))
        .await;
    assert_eq!(c.recv().await, Some(ServerMsg::Queued { position: 1 }));

    // Waiting players can still ping.
    c.send(&ClientMsg::Ping { client_time_ms: 7 }).await;
    assert!(matches!(
        c.recv().await,
        Some(ServerMsg::Pong {
            client_time_ms: 7,
            ..
        })
    ));
}

#[tokio::test]
async fn a_second_login_replaces_the_first() {
    let server = TestServer::start(1).await;
    let (name, t1) = server.bot().await;
    let t2 = server.bot_named(&name).await;
    let mut first = server.join(&t1).await;
    first.move_to(1, [100.4, 0.0, 100.0]).await;
    // Give the move time to land before the second login takes over.
    tokio::time::sleep(Duration::from_millis(150)).await;

    // At the cap of one, the second login does not queue: it inherits the slot.
    let mut second = server.socket().await;
    second.hello(&t2).await;
    let spawn = second
        .expect(|m| match m {
            ServerMsg::Welcome { spawn, .. } => Some(*spawn),
            ServerMsg::Queued { .. } => panic!("queued behind itself"),
            _ => None,
        })
        .await;
    assert_eq!(
        spawn,
        QPos::from_m([100.4, 0.0, 100.0]),
        "carries on where it stood"
    );
    assert_eq!(first.kicked().await, KICK_REPLACED);
}

/// The account email of the bot called `name`.
async fn bot_email(pool: &PgPool, name: &str) -> String {
    sqlx::query_scalar(
        "SELECT a.email FROM accounts a JOIN characters c ON c.account_id = a.id WHERE c.name = $1",
    )
    .bind(name)
    .fetch_one(pool)
    .await
    .unwrap()
}

#[tokio::test]
async fn a_password_reset_ends_the_live_connection() {
    let server = TestServer::start(1000).await;
    let (name, token) = server.bot().await;
    let mut client = server.join(&token).await;
    let email = bot_email(&server.state.pool, &name).await;

    let (status, body) = server
        .api(
            "/api/auth/password-reset",
            serde_json::json!({ "email": email }),
            None,
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    // Asking for a link signs nobody out.
    client.still_open().await;

    let url = body["dev_reset_url"].as_str().unwrap();
    let reset = url.split("reset=").nth(1).unwrap();
    let (status, body) = server
        .api(
            "/api/auth/password-reset/confirm",
            serde_json::json!({ "token": reset, "password": "a brand new password" }),
            None,
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    assert_eq!(
        client.kicked_with().await,
        (KickCode::SessionExpired, KICK_SESSION.to_owned())
    );
    // And the old token cannot come back in.
    let mut again = server.socket().await;
    again.hello(&token).await;
    assert_eq!(again.kicked_with().await.0, KickCode::SessionExpired);
}

#[tokio::test]
async fn signing_out_ends_only_the_connection_of_that_session() {
    // The default recheck (minutes): the kick below is the sign-out's own.
    let server = TestServer::start(1000).await;
    let (name, playing) = server.bot().await;
    // The same account signed in on another device.
    let other_device = server.bot_named(&name).await;
    let mut client = server.join(&playing).await;

    let (status, _) = server
        .api(
            "/api/auth/logout",
            serde_json::json!({}),
            Some(&other_device),
        )
        .await;
    assert_eq!(status, StatusCode::NO_CONTENT);
    client.still_open().await;

    let (status, _) = server
        .api("/api/auth/logout", serde_json::json!({}), Some(&playing))
        .await;
    assert_eq!(status, StatusCode::NO_CONTENT);
    assert_eq!(
        client.kicked_with().await,
        (KickCode::SessionExpired, KICK_SESSION.to_owned())
    );
}

#[tokio::test]
async fn expired_or_deleted_sessions_are_dropped_by_the_periodic_check() {
    let server =
        TestServer::start_with(1000, |c| c.session_recheck = Duration::from_millis(200)).await;

    // Expired while playing.
    let (_, token) = server.bot().await;
    let mut client = server.join(&token).await;
    // A live session survives several checks.
    tokio::time::sleep(Duration::from_millis(700)).await;
    client.still_open().await;
    sqlx::query(
        "UPDATE sessions SET expires_at = now() - interval '1 second' WHERE token_hash = $1",
    )
    .bind(&server::auth::token::hash(&token)[..])
    .execute(&server.state.pool)
    .await
    .unwrap();
    assert_eq!(
        client.kicked_with().await,
        (KickCode::SessionExpired, KICK_SESSION.to_owned())
    );

    // Deleted behind the server's back (no HTTP request to kick it).
    let (_, token) = server.bot().await;
    let mut client = server.join(&token).await;
    sqlx::query("DELETE FROM sessions WHERE token_hash = $1")
        .bind(&server::auth::token::hash(&token)[..])
        .execute(&server.state.pool)
        .await
        .unwrap();
    assert_eq!(client.kicked_with().await.0, KickCode::SessionExpired);
}

#[tokio::test]
async fn bad_handshakes_are_refused() {
    let server = TestServer::start(1000).await;

    let mut c = server.socket().await;
    c.hello("not-a-real-token").await;
    assert_eq!(
        c.kicked_with().await,
        (KickCode::SessionExpired, KICK_SESSION.to_owned())
    );

    let (_, token) = server.bot().await;
    let mut c = server.socket().await;
    c.send(&ClientMsg::Hello {
        protocol_version: PROTOCOL_VERSION + 1,
        token,
        character_id: None,
    })
    .await;
    assert_eq!(
        c.kicked_with().await,
        (KickCode::VersionMismatch, KICK_VERSION.to_owned())
    );

    let mut c = server.socket().await;
    c.send(&ClientMsg::Ping { client_time_ms: 1 }).await;
    assert_eq!(c.kicked_with().await.0, KickCode::ProtocolError);

    let mut c = server.socket().await;
    c.ws.send(Message::Binary(vec![0xff, 0xff, 0xff].into()))
        .await
        .unwrap();
    assert_eq!(c.kicked().await, KICK_PROTOCOL);
}

#[tokio::test]
async fn positions_are_saved_on_disconnect_and_shutdown() {
    let server = TestServer::start(1000).await;
    let pool = server.state.pool.clone();
    let (a_name, ta) = server.bot().await;
    let (b_name, tb) = server.bot().await;
    let mut a = server.join(&ta).await;
    let mut b = server.join(&tb).await;
    a.move_to(1, [100.0, 0.0, 100.6]).await;
    b.move_to(1, [100.0, 0.0, 159.4]).await;
    tokio::time::sleep(Duration::from_millis(150)).await;

    let saved = |name: String| {
        let pool = pool.clone();
        async move {
            sqlx::query_as::<_, (Option<f32>, Option<f32>, Option<f32>)>(
                "SELECT pos_x, pos_y, pos_z FROM characters WHERE name = $1",
            )
            .bind(name)
            .fetch_one(&pool)
            .await
            .unwrap()
        }
    };

    // A disconnects: its position is written shortly after.
    drop(a);
    let mut got = (None, None, None);
    for _ in 0..50 {
        got = saved(a_name.clone()).await;
        if got.0.is_some() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert_eq!(got, (Some(100.0), Some(0.0), Some(100.6)));

    // Shutdown kicks B and writes its position before returning.
    let rt = server.state.realtime.clone().unwrap();
    rt.shutdown().await;
    assert_eq!(b.kicked().await, KICK_RESTART);
    assert_eq!(saved(b_name).await, (Some(100.0), Some(0.0), Some(159.4)));

    // Reconnecting after shutdown is refused politely.
    let mut late = server.socket().await;
    assert_eq!(late.kicked().await, KICK_RESTART);

    // A comes back at its saved spot on a fresh server.
    let again = TestServer::start(1000).await;
    let token = again.bot_named(&a_name).await;
    let mut c = again.socket().await;
    c.hello(&token).await;
    let spawn = c
        .expect(|m| match m {
            ServerMsg::Welcome { spawn, .. } => Some(*spawn),
            _ => None,
        })
        .await;
    assert_eq!(spawn, QPos::from_m([100.0, 0.0, 100.6]));
}

async fn character_id(pool: &PgPool, name: &str) -> i64 {
    sqlx::query_scalar("SELECT id FROM characters WHERE name = $1")
        .bind(name)
        .fetch_one(pool)
        .await
        .unwrap()
}

/// Sets a character's house (`protocol::House` index) and resume point.
async fn set_character(pool: &PgPool, id: i64, house: i16, zone: &str, pos: Option<[f32; 3]>) {
    sqlx::query(
        "UPDATE characters SET house = $2, zone = $3, pos_x = $4, pos_y = $5, pos_z = $6
         WHERE id = $1",
    )
    .bind(id)
    .bind(house)
    .bind(zone)
    .bind(pos.map(|p| p[0]))
    .bind(pos.map(|p| p[1]))
    .bind(pos.map(|p| p[2]))
    .execute(pool)
    .await
    .unwrap();
}

/// An item instance in the character's inventory.
async fn give_item(pool: &PgPool, owner: i64, item_def: i32) -> i64 {
    sqlx::query_scalar(
        "INSERT INTO items (owner_id, item_def, dye_primary, dye_secondary, dye_trim)
         VALUES ($1, $2, 3, 4, 5) RETURNING id",
    )
    .bind(owner)
    .bind(item_def)
    .fetch_one(pool)
    .await
    .unwrap()
}

async fn befriend(pool: &PgPool, a: i64, b: i64) {
    sqlx::query("INSERT INTO friendships (character_id, friend_id) VALUES ($1, $2), ($2, $1)")
        .bind(a)
        .bind(b)
        .execute(pool)
        .await
        .unwrap();
}

/// The catalog id of the furniture whose asset contains `tag`.
fn furniture_def(tag: &str) -> i32 {
    let c = &server::catalog::shipped().catalog;
    i32::from(
        c.items
            .iter()
            .find(|i| i.kind == sim_core::ItemKind::Furniture && i.asset.contains(tag))
            .unwrap()
            .id,
    )
}

/// Polls until `f` holds (the saver and dorm jobs write asynchronously).
async fn eventually<F, Fut>(mut f: F)
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

async fn saved_zone(pool: &PgPool, id: i64) -> (String, Option<f32>, Option<f32>) {
    sqlx::query_as("SELECT zone, pos_x, pos_z FROM characters WHERE id = $1")
        .bind(id)
        .fetch_one(pool)
        .await
        .unwrap()
}

/// Through the common room's dorm door into your own room; furniture placed,
/// moved and removed is stored and survives a reconnect, which resumes in the
/// room; the room's way out leads back to the door.
#[tokio::test]
async fn a_character_from_before_wardrobes_is_dressed_on_hello() {
    let server = TestServer::start(1000).await;
    let pool = server.state.pool.clone();
    // Bot characters are made bare: no grant.
    let (name, token) = server.bot().await;
    let id = character_id(&pool, &name).await;
    let items = |pool: PgPool| async move {
        let n: i64 = sqlx::query_scalar("SELECT count(*) FROM items WHERE owner_id = $1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap();
        n
    };
    assert_eq!(items(pool.clone()).await, 0);

    // No HTTP request first: the WebSocket alone grants it.
    let mut c = server.socket().await;
    c.hello(&token).await;
    let (version, appearance) = c
        .expect(|m| match m {
            ServerMsg::Welcome {
                appearance_version,
                appearance,
                ..
            } => Some((*appearance_version, *appearance)),
            _ => None,
        })
        .await;
    assert_eq!(version, 1);
    assert!(appearance.outfit.body.is_some(), "{appearance:?}");
    let granted = items(pool.clone()).await;
    assert!(granted > 20, "{granted} starter items");
    drop(c);

    // Signing in again grants nothing more.
    let again = server.join(&token).await;
    drop(again);
    assert_eq!(items(pool).await, granted);
}

#[tokio::test]
async fn doors_rooms_and_furniture_persist() {
    let server = TestServer::start(1000).await;
    let pool = server.state.pool.clone();
    let (name, token) = server.bot().await;
    let cid = character_id(&pool, &name).await;
    // A Roarwing standing in the common room, by the dorm door.
    set_character(&pool, cid, 0, "castle", Some([214.0, 0.0, 100.0])).await;
    let desk = give_item(&pool, cid, furniture_def("desk")).await;
    let lantern = give_item(&pool, cid, furniture_def("lantern")).await;

    let mut a = server.join(&token).await;
    a.send(&ClientMsg::Interact {
        target: "dorm_door_r".into(),
    })
    .await;
    let zone = a
        .expect(|m| match m {
            ServerMsg::ZoneChanged { zone, .. } => Some(zone.clone()),
            _ => None,
        })
        .await;
    assert_eq!(
        zone,
        Zone::Dorm {
            owner_name: name.clone(),
            house: protocol::House::Roarwing,
            layout: "dorm_roarwing".into()
        }
    );
    let (open, items) = a
        .expect(|m| match m {
            ServerMsg::DormState {
                open_to_friends,
                items,
                ..
            } => Some((*open_to_friends, items.clone())),
            _ => None,
        })
        .await;
    assert!(!open);
    // The room starts with the character's Four-Poster Bed by its wall: the
    // starter grant (on `Hello`) placed it at the shell's starter spot.
    let spot = server::dorm::shells::shipped().starter_furniture(protocol::House::Roarwing)[0];
    let (bed_pos, bed_yaw) = (QPos::from_m(spot.pos), protocol::yaw_to_u8(spot.yaw));
    assert_eq!(
        items
            .iter()
            .map(|p| (p.item_def, p.pos, p.yaw))
            .collect::<Vec<_>>(),
        vec![(spot.item, bed_pos, bed_yaw)]
    );
    let bed = items[0].item_id;

    // Place the desk, then a lantern on its top.
    let origin = QPos::from_m([0.0, 0.0, 0.0]);
    a.send(&ClientMsg::DormPlace {
        item_id: desk,
        pos: origin,
        yaw: 0,
    })
    .await;
    match a.furniture().await {
        FurnitureEdit::Placed(p) => {
            assert_eq!((p.item_id, p.pos, p.dyes), (desk, origin, [3, 4, 5]));
        }
        other => panic!("{other:?}"),
    }
    let location = |id: i64| {
        let pool = pool.clone();
        async move {
            sqlx::query_scalar::<_, String>("SELECT location FROM items WHERE id = $1")
                .bind(id)
                .fetch_one(&pool)
                .await
                .unwrap()
        }
    };
    assert_eq!(location(desk).await, "dorm");
    let top = QPos::from_m([0.2, 0.76, 0.0]);
    a.send(&ClientMsg::DormPlace {
        item_id: lantern,
        pos: top,
        yaw: 0,
    })
    .await;
    assert!(matches!(a.furniture().await, FurnitureEdit::Placed(p) if p.item_id == lantern));
    // Refused: placed twice, someone else's item, outside the room.
    a.send(&ClientMsg::DormPlace {
        item_id: lantern,
        pos: top,
        yaw: 0,
    })
    .await;
    assert_eq!(a.dorm_error().await, DormErrorCode::ItemUnavailable);
    a.send(&ClientMsg::DormPlace {
        item_id: i64::MAX,
        pos: origin,
        yaw: 0,
    })
    .await;
    assert_eq!(a.dorm_error().await, DormErrorCode::ItemNotFound);
    a.send(&ClientMsg::DormMove {
        item_id: lantern,
        pos: QPos::from_m([40.0, 0.0, 0.0]),
        yaw: 0,
    })
    .await;
    assert_eq!(a.dorm_error().await, DormErrorCode::OutOfBounds);
    // The desk holds the lantern: it stays put until the lantern is off it.
    a.send(&ClientMsg::DormRemove { item_id: desk }).await;
    assert_eq!(a.dorm_error().await, DormErrorCode::NotEmpty);
    let floor = QPos::from_m([2.0, 0.0, 2.0]);
    a.send(&ClientMsg::DormMove {
        item_id: lantern,
        pos: floor,
        yaw: 0,
    })
    .await;
    assert!(
        matches!(a.furniture().await, FurnitureEdit::Moved { item_id, .. } if item_id == lantern)
    );

    // Move and turn the desk, then put it away.
    let to = QPos::from_m([1.0, 0.0, -1.0]);
    a.send(&ClientMsg::DormMove {
        item_id: desk,
        pos: to,
        yaw: 64,
    })
    .await;
    assert_eq!(
        a.furniture().await,
        FurnitureEdit::Moved {
            item_id: desk,
            pos: to,
            yaw: 64
        }
    );
    a.send(&ClientMsg::DormRemove { item_id: desk }).await;
    assert_eq!(
        a.furniture().await,
        FurnitureEdit::Removed { item_id: desk }
    );
    assert_eq!(location(desk).await, "inventory");

    // The bed is furniture like any other: moved, turned, put away and put
    // back by its wall.
    assert_eq!(location(bed).await, "dorm");
    let middle = QPos::from_m([0.0, 0.0, -2.0]);
    for (pos, yaw) in [(middle, 0), (middle, 128)] {
        a.send(&ClientMsg::DormMove {
            item_id: bed,
            pos,
            yaw,
        })
        .await;
        assert_eq!(
            a.furniture().await,
            FurnitureEdit::Moved {
                item_id: bed,
                pos,
                yaw
            }
        );
    }
    a.send(&ClientMsg::DormRemove { item_id: bed }).await;
    assert_eq!(a.furniture().await, FurnitureEdit::Removed { item_id: bed });
    assert_eq!(location(bed).await, "inventory");
    a.send(&ClientMsg::DormPlace {
        item_id: bed,
        pos: bed_pos,
        yaw: bed_yaw,
    })
    .await;
    assert!(matches!(a.furniture().await, FurnitureEdit::Placed(p) if p.item_id == bed));

    // Open to friends: told at once, stored.
    a.send(&ClientMsg::DormSetOpen { open: true }).await;
    a.expect(|m| (*m == ServerMsg::DormOpenChanged { open: true }).then_some(()))
        .await;
    eventually(|| {
        let pool = pool.clone();
        async move {
            sqlx::query_scalar::<_, bool>("SELECT open_to_friends FROM dorms WHERE owner_id = $1")
                .bind(cid)
                .fetch_optional(&pool)
                .await
                .unwrap()
                == Some(true)
        }
    })
    .await;

    // Reconnecting resumes in the room, which loads what was placed.
    drop(a);
    eventually(|| {
        let pool = pool.clone();
        async move { saved_zone(&pool, cid).await.0 == "dorm" }
    })
    .await;
    let mut a = server.socket().await;
    a.hello(&server.bot_named(&name).await).await;
    let zone = a
        .expect(|m| match m {
            ServerMsg::Welcome { zone, .. } => Some(zone.clone()),
            _ => None,
        })
        .await;
    assert!(matches!(zone, Zone::Dorm { .. }));
    let (open, items) = a
        .expect(|m| match m {
            ServerMsg::DormState {
                open_to_friends,
                items,
                ..
            } => Some((*open_to_friends, items.clone())),
            _ => None,
        })
        .await;
    assert!(open);
    assert_eq!(
        items.iter().map(|p| (p.item_id, p.pos)).collect::<Vec<_>>(),
        vec![(lantern, floor), (bed, bed_pos)]
    );

    // The way out leads to the dorm door, facing out of it.
    a.send(&ClientMsg::LeaveInstance).await;
    let (zone, spawn) = a
        .expect(|m| match m {
            ServerMsg::ZoneChanged { zone, spawn, .. } => Some((zone.clone(), *spawn)),
            _ => None,
        })
        .await;
    assert_eq!(zone, Zone::Castle);
    assert_eq!(spawn, QPos::from_m([215.0, 0.0, 100.0]));
    drop(a);
    eventually(|| {
        let pool = pool.clone();
        async move { saved_zone(&pool, cid).await == ("castle".into(), Some(215.0), Some(100.0)) }
    })
    .await;
}

/// Friends see each other come online; an invitation takes a friend of
/// another house straight into the room, where they see and hear each
/// other; when the owner leaves, the guest is sent back to where they were.
/// A friendship made over HTTP reaches the world through `friends_changed`.
#[tokio::test]
async fn friends_meet_in_a_dorm_by_invitation() {
    let server = TestServer::start(1000).await;
    let pool = server.state.pool.clone();
    let world = server.state.world().unwrap().clone();
    let (a_name, ta) = server.bot().await;
    let (b_name, tb) = server.bot().await;
    let (c_name, tc) = server.bot().await;
    let (a_cid, b_cid, c_cid) = (
        character_id(&pool, &a_name).await,
        character_id(&pool, &b_name).await,
        character_id(&pool, &c_name).await,
    );
    set_character(&pool, a_cid, 1, "dorm", None).await;
    set_character(&pool, b_cid, 3, "castle", Some([120.0, 0.0, 120.0])).await;
    befriend(&pool, a_cid, b_cid).await;

    let mut b = server.join(&tb).await;
    let mut a = server.join(&ta).await;
    // B hears A come online, in A's room.
    let ev = b
        .expect(|m| match m {
            ServerMsg::Friend { event } => Some(event.clone()),
            _ => None,
        })
        .await;
    assert_eq!(
        ev,
        FriendEvent::Online {
            character_id: a_cid,
            location: Location::Dorm {
                owner_name: a_name.clone()
            },
            room_open: false,
        }
    );
    let status = world.online_status(&[a_cid, b_cid, c_cid]).await;
    assert_eq!(status.len(), 2);
    assert_eq!(
        status[&b_cid],
        Location::Castle {
            place: "Hall".into()
        }
    );

    a.send(&ClientMsg::DormInvite {
        character_name: b_name.clone(),
    })
    .await;
    let invite_id = b
        .expect(|m| match m {
            ServerMsg::DormInviteReceived {
                invite_id,
                from_name,
                ..
            } if *from_name == a_name => Some(*invite_id),
            _ => None,
        })
        .await;
    b.send(&ClientMsg::DormAcceptInvite { invite_id }).await;
    b.expect(|m| match m {
        ServerMsg::ZoneChanged {
            zone: Zone::Dorm { owner_name, .. },
            ..
        } if *owner_name == a_name => Some(()),
        _ => None,
    })
    .await;
    b.expect(|m| matches!(m, ServerMsg::DormState { .. }).then_some(()))
        .await;
    let b_id = b.id;
    a.expect(|m| match m {
        ServerMsg::Snapshot { entered, .. } => entered.iter().any(|e| e.id() == b_id).then_some(()),
        _ => None,
    })
    .await;
    a.say(ChatChannel::Say, "welcome in").await;
    assert_eq!(b.chat().await.2, "welcome in");
    // B is saved at its return point, not in A's room.
    eventually(|| {
        let pool = pool.clone();
        async move { saved_zone(&pool, b_cid).await == ("castle".into(), Some(120.0), Some(120.0)) }
    })
    .await;

    // C becomes A's friend over HTTP: the world reloads and tells C.
    let mut c = server.join(&tc).await;
    befriend(&pool, a_cid, c_cid).await;
    world.friends_changed(a_cid);
    world.friends_changed(c_cid);
    let ev = c
        .expect(|m| match m {
            ServerMsg::Friend { event } => Some(event.clone()),
            _ => None,
        })
        .await;
    assert!(matches!(ev, FriendEvent::Online { character_id, .. } if character_id == a_cid));
    // Not a friend of B: B's room stays closed to C.
    c.send(&ClientMsg::DormVisit {
        owner_name: b_name.clone(),
    })
    .await;
    assert_eq!(c.dorm_error().await, DormErrorCode::NotFriend);

    // A goes offline: B is sent back to where it stood, and hears it.
    drop(a);
    let spawn = b
        .expect(|m| match m {
            ServerMsg::ZoneChanged {
                zone: Zone::Castle,
                spawn,
                ..
            } => Some(*spawn),
            _ => None,
        })
        .await;
    assert_eq!(spawn, QPos::from_m([120.0, 0.0, 120.0]));
    b.expect(|m| {
        matches!(m, ServerMsg::Friend { event: FriendEvent::Offline { character_id } } if *character_id == a_cid)
            .then_some(())
    })
    .await;
}

/// Emotes show in others' snapshots; a new look reaches the character and
/// everyone who sees it.
#[tokio::test]
async fn emotes_and_new_looks_reach_viewers() {
    let server = TestServer::start(1000).await;
    let pool = server.state.pool.clone();
    let (a_name, ta) = server.bot().await;
    let (_, tb) = server.bot().await;
    let a_cid = character_id(&pool, &a_name).await;
    set_character(&pool, a_cid, 2, "castle", Some([100.0, 0.0, 100.0])).await;
    let mut a = server.join(&ta).await;
    let mut b = server.join(&tb).await;
    let a_id = a.id;
    b.expect(|m| match m {
        ServerMsg::Snapshot { entered, .. } => entered.iter().any(|e| e.id() == a_id).then_some(()),
        _ => None,
    })
    .await;

    a.send(&ClientMsg::Emote { emote: Emote::Wave }).await;
    b.expect(|m| match m {
        ServerMsg::Snapshot { full, .. } => full
            .iter()
            .any(|s| s.id == a_id && s.anim == Anim::Wave)
            .then_some(()),
        _ => None,
    })
    .await;

    let look = protocol::Appearance {
        hair_style: 5,
        ..Default::default()
    };
    server
        .state
        .world()
        .unwrap()
        .appearance_changed(a_cid, look, 9);
    for c in [&mut a, &mut b] {
        let got = c
            .expect(|m| match m {
                ServerMsg::AppearanceChanged {
                    id,
                    appearance_version,
                    appearance,
                } => Some((*id, *appearance_version, appearance.hair_style)),
                _ => None,
            })
            .await;
        assert_eq!(got, (a_id, 9, 5));
    }
}

/// Protocol v10: a character's look target reaches the players who see it,
/// once per change; a target out of range counts as none.
#[tokio::test]
async fn looks_reach_viewers_and_far_targets_count_as_none() {
    let server = TestServer::start(1000).await;
    let pool = server.state.pool.clone();
    let mut joined = Vec::new();
    for pos in [
        [100.0, 0.0, 100.0],
        [102.0, 0.0, 100.0],
        [100.0, 0.0, 130.0],
    ] {
        let (name, token) = server.bot().await;
        let cid = character_id(&pool, &name).await;
        set_character(&pool, cid, 2, "castle", Some(pos)).await;
        joined.push(server.join(&token).await);
    }
    let Ok([mut a, mut b, c]) = <[Client; 3]>::try_from(joined) else {
        unreachable!()
    };
    let (a_id, b_id, c_id) = (a.id, b.id, c.id);
    // Wait until B sees both others.
    let mut seen = Vec::new();
    while !(seen.contains(&a_id) && seen.contains(&c_id)) {
        let ids = b
            .expect(|m| match m {
                ServerMsg::Snapshot { entered, .. } => {
                    Some(entered.iter().map(EntityEntry::id).collect::<Vec<_>>())
                }
                _ => None,
            })
            .await;
        seen.extend(ids);
    }
    let next_look = |m: &ServerMsg| match m {
        ServerMsg::Snapshot { looks, .. } => looks.iter().find(|l| l.id == a_id).copied(),
        _ => None,
    };

    a.send(&ClientMsg::LookAt { target: Some(b_id) }).await;
    let got = b.expect(next_look).await;
    assert_eq!(got.target, Some(b_id));

    // C stands 30 m away: beyond the look range, so A looks at nobody.
    a.send(&ClientMsg::LookAt { target: Some(c_id) }).await;
    let got = b.expect(next_look).await;
    assert_eq!(got.target, None);

    a.send(&ClientMsg::LookAt { target: Some(b_id) }).await;
    assert_eq!(b.expect(next_look).await.target, Some(b_id));
    a.send(&ClientMsg::LookAt { target: None }).await;
    assert_eq!(b.expect(next_look).await.target, None);
    drop(c);
}

/// Smoke test against a separately running `shs-server` (with `SHS_DEV=1` and
/// the real castle layout):
///
/// ```text
/// SHS_SMOKE_ADDR=127.0.0.1:8080 cargo test -p server --test realtime -- --ignored live_server_smoke
/// ```
#[tokio::test]
#[ignore]
async fn live_server_smoke() {
    let addr: SocketAddr = std::env::var("SHS_SMOKE_ADDR")
        .unwrap_or_else(|_| "127.0.0.1:8080".into())
        .parse()
        .unwrap();
    let suffix = rand::thread_rng().gen_range(0..100_000_000);
    let ta = http_bot_session(addr, &format!("smoke{suffix:08}a")).await;
    let tb = http_bot_session(addr, &format!("smoke{suffix:08}b")).await;
    let mut a = Client::connect(addr).await;
    let (a_id, a_spawn) = a.sign_in(&ta).await;
    let mut b = Client::connect(addr).await;
    b.sign_in(&tb).await;

    // Half a metre toward the marble staircase (north, -z) from the spawn.
    let [x, y, z] = a_spawn.to_m();
    a.move_to(1, [x, y, z - 0.5]).await;
    let want = QPos::from_m([x, y, z - 0.5]);
    b.expect(|m| match m {
        ServerMsg::Snapshot { full, .. } => full
            .iter()
            .any(|s| s.id == a_id && s.pos == want)
            .then_some(()),
        _ => None,
    })
    .await;

    a.say(ChatChannel::Say, "Good morning!").await;
    assert_eq!(
        b.chat().await,
        (a_id, ChatChannel::Say, "Good morning!".to_owned())
    );

    a.move_to(2, [x + 40.0, y, z]).await;
    let (seq, pos) = a
        .expect(|m| match m {
            ServerMsg::Correction { seq, pos } => Some((*seq, *pos)),
            _ => None,
        })
        .await;
    assert_eq!((seq, pos), (2, want));
}

/// `POST /api/dev/bot-session` over a plain HTTP/1.1 connection.
async fn http_bot_session(addr: SocketAddr, name: &str) -> String {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let body = serde_json::json!({ "name": name }).to_string();
    let mut s = TcpStream::connect(addr).await.expect("server is running");
    let req = format!(
        "POST /api/dev/bot-session HTTP/1.1\r\nHost: {addr}\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    s.write_all(req.as_bytes()).await.unwrap();
    let mut res = String::new();
    s.read_to_string(&mut res).await.unwrap();
    assert!(res.starts_with("HTTP/1.1 200"), "{res}");
    let json = &res[res.find("\r\n\r\n").unwrap() + 4..];
    let v: serde_json::Value = serde_json::from_str(json).unwrap();
    v["token"].as_str().unwrap().to_owned()
}

/// A second character for the bot account behind `token` (the session then
/// plays it); returns its (id, name).
async fn second_character(server: &TestServer, token: &str) -> (i64, String) {
    let name = format!(
        "Alt{}",
        (0..10)
            .map(|_| rand::thread_rng().gen_range(b'a'..=b'z') as char)
            .collect::<String>()
    );
    let (status, body) = server
        .api(
            "/api/characters",
            serde_json::json!({ "name": name, "house": "Skytalon" }),
            Some(token),
        )
        .await;
    assert_eq!(status, StatusCode::CREATED, "{body:?}");
    (body["id"].as_i64().unwrap(), name)
}

/// `DELETE /api/characters/{id} { confirm_name }`.
async fn delete_character(
    server: &TestServer,
    token: &str,
    id: i64,
    name: &str,
) -> (StatusCode, serde_json::Value) {
    let req = Request::delete(format!("/api/characters/{id}"))
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::AUTHORIZATION, format!("Bearer {token}"))
        .body(Body::from(
            serde_json::json!({ "confirm_name": name }).to_string(),
        ))
        .unwrap();
    let res = http::router(server.state.clone())
        .layer(MockConnectInfo(SocketAddr::from((
            [203, 0, 113, 8],
            50_000,
        ))))
        .oneshot(req)
        .await
        .unwrap();
    let status = res.status();
    let bytes = axum::body::to_bytes(res.into_body(), usize::MAX)
        .await
        .unwrap();
    (status, serde_json::from_slice(&bytes).unwrap_or_default())
}

async fn welcome_name(c: &mut Client, token: &str) -> String {
    c.hello(token).await;
    c.expect(|m| match m {
        ServerMsg::Welcome { character, .. } => Some(character.name.clone()),
        _ => None,
    })
    .await
}

#[tokio::test]
async fn the_gateway_plays_the_character_the_session_picked() {
    let server = TestServer::start(10).await;
    let (bot, token) = server.bot().await;
    let (alt_id, alt) = second_character(&server, &token).await;
    let bot_id = character_id(&server.state.pool, &bot).await;

    // Creating it picked the new character.
    let mut c = server.socket().await;
    assert_eq!(welcome_name(&mut c, &token).await, alt);

    // Picking the other one ends the connection playing this one...
    let (status, body) = server
        .api(
            &format!("/api/characters/{bot_id}/select"),
            serde_json::json!({}),
            Some(&token),
        )
        .await;
    assert_eq!(status, StatusCode::OK, "{body:?}");
    assert_eq!(body["name"], bot.as_str());
    assert_eq!(c.kicked_with().await.0, KickCode::Replaced);
    // ...and the next Hello plays it.
    let mut c = server.socket().await;
    assert_eq!(welcome_name(&mut c, &token).await, bot);

    // Picking the one already played changes nothing.
    let (status, _) = server
        .api(
            &format!("/api/characters/{bot_id}/select"),
            serde_json::json!({}),
            Some(&token),
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    c.still_open().await;

    // An online character cannot be deleted; an offline one can.
    let (status, body) = delete_character(&server, &token, bot_id, &bot).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["error"], "character_online");
    let (status, body) = delete_character(&server, &token, alt_id, &alt).await;
    assert_eq!(status, StatusCode::NO_CONTENT, "{body:?}");
    c.still_open().await;
}

#[tokio::test]
async fn a_session_that_picked_nothing_is_refused() {
    let server = TestServer::start(10).await;
    let (bot, _) = server.bot().await;
    let account: i64 = sqlx::query_scalar("SELECT account_id FROM characters WHERE name_key = $1")
        .bind(bot.to_lowercase())
        .fetch_one(&server.state.pool)
        .await
        .unwrap();
    let (token, _) =
        server::auth::create_session(&server.state.pool, account, Duration::from_secs(3600))
            .await
            .unwrap();
    let mut c = server.socket().await;
    c.hello(&token).await;
    assert_eq!(c.kicked_with().await.0, KickCode::NoCharacter);
}

/// Says Hello naming the character the client was built for.
async fn hello_as(c: &mut Client, token: &str, character_id: i64) {
    c.send(&ClientMsg::Hello {
        protocol_version: PROTOCOL_VERSION,
        token: token.into(),
        character_id: Some(character_id),
    })
    .await;
}

#[tokio::test]
async fn a_hello_for_another_character_than_the_sessions_is_refused() {
    let server = TestServer::start(10).await;
    let (bot, token) = server.bot().await;
    let bot_id = character_id(&server.state.pool, &bot).await;
    // Another window picks a second character on the same session.
    let (alt_id, alt) = second_character(&server, &token).await;

    // A client still built for the bot reconnects: refused for good.
    let mut c = server.socket().await;
    hello_as(&mut c, &token, bot_id).await;
    assert_eq!(c.kicked_with().await.0, KickCode::Replaced);
    // The client built for the picked one comes in.
    let mut c = server.socket().await;
    hello_as(&mut c, &token, alt_id).await;
    let name = c
        .expect(|m| match m {
            ServerMsg::Welcome { character, .. } => Some(character.name.clone()),
            _ => None,
        })
        .await;
    assert_eq!(name, alt);
}

#[tokio::test]
async fn an_older_clients_hello_gets_version_mismatch() {
    #[derive(serde::Serialize)]
    enum V12 {
        Hello {
            protocol_version: u16,
            token: String,
        },
    }
    let server = TestServer::start(10).await;
    let (_, token) = server.bot().await;
    let old = postcard::to_allocvec(&V12::Hello {
        protocol_version: 12,
        token,
    })
    .unwrap();
    let mut c = server.socket().await;
    c.ws.send(Message::Binary(old.into())).await.unwrap();
    assert_eq!(
        c.kicked_with().await,
        (KickCode::VersionMismatch, KICK_VERSION.to_owned())
    );
}

#[tokio::test]
async fn a_connection_whose_session_stops_playing_its_character_is_kicked() {
    let server =
        TestServer::start_with(1000, |c| c.session_recheck = Duration::from_millis(200)).await;
    let (_, token) = server.bot().await;
    let mut c = server.join(&token).await;
    c.still_open().await;
    // As the FK does when the character is deleted under it.
    let hash = server::auth::token::hash(&token);
    sqlx::query("UPDATE sessions SET character_id = NULL WHERE token_hash = $1")
        .bind(&hash[..])
        .execute(&server.state.pool)
        .await
        .unwrap();
    assert_eq!(c.kicked_with().await.0, KickCode::NoCharacter);
}

#[tokio::test]
async fn deleting_a_character_kicks_a_connection_still_on_its_way_in() {
    // One slot, taken: the second account's connection waits in the queue,
    // registered but not in the world yet.
    let server = TestServer::start(1).await;
    let (_, first) = server.bot().await;
    let _first = server.join(&first).await;
    let (bot, token) = server.bot().await;
    let (alt_id, alt) = second_character(&server, &token).await;
    let mut waiting = server.socket().await;
    hello_as(&mut waiting, &token, alt_id).await;
    assert_eq!(
        waiting.recv().await,
        Some(ServerMsg::Queued { position: 1 })
    );
    // Not in the world, so the delete goes ahead, and the waiting
    // connection playing it ends.
    let (status, body) = delete_character(&server, &token, alt_id, &alt).await;
    assert_eq!(status, StatusCode::NO_CONTENT, "{body:?}");
    assert_eq!(waiting.kicked_with().await.0, KickCode::NoCharacter);
    // The account's other character is untouched.
    assert!(
        server::characters::find(
            &server.state.pool,
            character_id(&server.state.pool, &bot).await
        )
        .await
        .unwrap()
        .is_some()
    );
}

#[tokio::test]
async fn deleting_another_accounts_online_character_is_not_found() {
    let server = TestServer::start(10).await;
    let (bot, token) = server.bot().await;
    let _playing = server.join(&token).await;
    let bot_id = character_id(&server.state.pool, &bot).await;
    let (_, stranger) = server.bot().await;
    let (status, body) = delete_character(&server, &stranger, bot_id, &bot).await;
    assert_eq!(status, StatusCode::NOT_FOUND, "{body:?}");
    assert_eq!(body["error"], "character_not_found");
    // Its owner is refused while it plays.
    let (status, body) = delete_character(&server, &token, bot_id, &bot).await;
    assert_eq!(status, StatusCode::CONFLICT, "{body:?}");
    assert_eq!(body["error"], "character_online");
}

/// Parties end to end: an invitation by name, accepted from a dorm room; the
/// party sees both members and where they are; party chat crosses zones and
/// reaches nobody outside the party; outside a party it is refused.
#[tokio::test]
async fn a_party_talks_across_zones() {
    let server = TestServer::start(1000).await;
    let pool = server.state.pool.clone();
    let (a_name, ta) = server.bot().await;
    let (b_name, tb) = server.bot().await;
    let (c_name, tc) = server.bot().await;
    // B, a Roarwing, starts by the dorm door and goes into the room; A and C
    // stand side by side in the hall.
    let b_id = character_id(&pool, &b_name).await;
    set_character(&pool, b_id, 0, "castle", Some([214.0, 0.0, 100.0])).await;
    for (name, x) in [(&a_name, 100.0), (&c_name, 101.0)] {
        let id = character_id(&pool, name).await;
        set_character(&pool, id, 2, "castle", Some([x, 0.0, 100.0])).await;
    }
    let mut a = server.join(&ta).await;
    let mut b = server.join(&tb).await;
    let mut c = server.join(&tc).await;
    b.send(&ClientMsg::Interact {
        target: "dorm_door_r".into(),
    })
    .await;
    b.expect(|m| {
        matches!(
            m,
            ServerMsg::ZoneChanged {
                zone: Zone::Dorm { .. },
                ..
            }
        )
        .then_some(())
    })
    .await;

    a.send(&ClientMsg::PartyInvite {
        name: b_name.to_uppercase(),
    })
    .await;
    let invite_id = b
        .expect(|m| match m {
            ServerMsg::PartyInvited {
                invite_id,
                from_name,
                ..
            } if *from_name == a_name => Some(*invite_id),
            _ => None,
        })
        .await;
    b.send(&ClientMsg::PartyRespond {
        invite_id,
        accept: true,
    })
    .await;
    for x in [&mut a, &mut b] {
        let party = x
            .expect(|m| match m {
                ServerMsg::PartyState { party: Some(p) } => Some(p.clone()),
                _ => None,
            })
            .await;
        let names: Vec<&str> = party.members.iter().map(|m| m.name.as_str()).collect();
        assert_eq!(names, vec![a_name.as_str(), b_name.as_str()]);
        assert!(matches!(
            party.members[1].online.as_ref().unwrap().place,
            protocol::PartyPlace::Dorm { .. }
        ));
    }

    // Party chat reaches B in the dorm; C, standing by A, hears only the say after it.
    a.say(ChatChannel::Party, "meet at the library").await;
    let line = (a.id, ChatChannel::Party, "meet at the library".to_owned());
    assert_eq!(b.chat().await, line);
    assert_eq!(a.chat().await, line);
    a.say(ChatChannel::Say, "hello").await;
    assert_eq!(c.chat().await, (a.id, ChatChannel::Say, "hello".to_owned()));

    // C is in no party: refused.
    c.say(ChatChannel::Party, "can I come?").await;
    let refused = c.next_chat;
    assert_eq!(
        c.chat_rejected().await,
        (refused, ChatRejectReason::NoChannel)
    );
}

/// The social hotbar (protocol v17) end to end: a character that never set
/// one gets its learned hotbar spells at join; `SetHotbar` is answered,
/// saved, and sent again after a reconnect.
#[tokio::test]
async fn hotbars_are_set_saved_and_sent_again_at_join() {
    let server = TestServer::start(1000).await;
    let pool = server.state.pool.clone();
    let (name, token) = server.bot().await;
    let id = character_id(&pool, &name).await;
    let spells = &server::catalog::shipped().spells;
    let key = |k: &str| spells.by_key(k).unwrap().id;
    let mut tx = pool.begin().await.unwrap();
    server::progression::learn_spells(
        &mut tx,
        id,
        &[key("rigora"), key("torpeo"), key("lucerna")],
        "admin",
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();

    let mut c = server.join(&token).await;
    let mut want = vec![None; protocol::HOTBAR_SLOTS];
    want[0] = Some(key("lucerna"));
    want[1] = Some(key("rigora"));
    assert_eq!(
        spellbook_hotbar(&mut c).await,
        want,
        "spellbook order, flourishes left out"
    );

    let mut set = vec![None; protocol::HOTBAR_SLOTS];
    set[9] = Some(key("lucerna"));
    c.send(&ClientMsg::SetHotbar { slots: set.clone() }).await;
    let answer = c
        .expect(|m| match m {
            ServerMsg::Hotbar { slots } => Some(slots.clone()),
            _ => None,
        })
        .await;
    assert_eq!(answer, set);
    let mut want_db = vec![None; protocol::HOTBAR_SLOTS];
    want_db[9] = Some(i32::from(key("lucerna")));
    eventually(|| {
        let pool = pool.clone();
        let want_db = want_db.clone();
        async move {
            let stored: Option<Vec<Option<i32>>> =
                sqlx::query_scalar("SELECT hotbar FROM characters WHERE id = $1")
                    .bind(id)
                    .fetch_one(&pool)
                    .await
                    .unwrap();
            stored == Some(want_db)
        }
    })
    .await;

    drop(c);
    tokio::time::sleep(Duration::from_millis(200)).await;
    let mut again = server.join(&token).await;
    assert_eq!(
        spellbook_hotbar(&mut again).await,
        set,
        "the saved layout, Rigora left off"
    );
}

async fn spellbook_hotbar(c: &mut Client) -> Vec<Option<u16>> {
    c.expect(|m| match m {
        ServerMsg::Spellbook { hotbar, .. } => Some(hotbar.clone()),
        _ => None,
    })
    .await
}
