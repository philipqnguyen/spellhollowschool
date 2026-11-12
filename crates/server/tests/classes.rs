//! Classes over the database: seats (`classes::admit`), the
//! rewards (`classes::reward`: once per session even when raced, nothing
//! for too little taking part, spells at the level reached), the routes,
//! and a whole Charms lesson end to end over WebSockets on a pinned clock.

mod common;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::http::StatusCode;
use chrono::Utc;
use common::{TestApp, TestPlayer};
use futures_util::{SinkExt, StreamExt};
use protocol::{
    ClassErrorCode, ClientMsg, Grade, LessonPhase, PROTOCOL_VERSION, ServerMsg, TracePoint,
    TraceReject, XpSource, Zone, decode_server, encode_client,
};
use server::catalog::shipped;
use server::classes::{self, Admission, AdmitRequest, RewardOutcome, RewardRequest, class_xp};
use server::realtime::chat::WordFilter;
use server::{AppState, Config, db, email, http};
use sim_core::SessionTimes;
use sqlx::PgPool;
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

/// Charms session `k` of a fixed far-off day (seats are unique per session
/// and character, and every test makes fresh characters).
fn session(k: i64) -> SessionTimes {
    let schedule = &shipped().schedule;
    let charms = schedule.class("charms").unwrap();
    schedule.next(charms, 4_000_000_000_000 + k * 7_200_000)
}

async fn admit(pool: &PgPool, p: &TestPlayer, s: SessionTimes, section: u8) -> Admission {
    let req = AdmitRequest {
        class_id: "charms".into(),
        session: s,
        section,
        character_id: p.id,
    };
    classes::admit(pool, &req).await.unwrap()
}

fn attendance(a: Admission) -> i64 {
    match a {
        Admission::Admitted { attendance_id } => attendance_id,
        Admission::AlreadyAttended => panic!("barred"),
    }
}

fn request(p: &TestPlayer, attendance_id: i64, score_pct: u8, traces: u8) -> RewardRequest {
    RewardRequest {
        attendance_id,
        character_id: p.id,
        class_id: "charms".into(),
        score_pct,
        answers: traces,
        section_pct: 0,
    }
}

async fn ledger(pool: &PgPool, id: i64) -> Vec<(String, Option<i64>, i32)> {
    sqlx::query_as(
        "SELECT source, ref_id, amount FROM xp_ledger WHERE character_id = $1 ORDER BY id",
    )
    .bind(id)
    .fetch_all(pool)
    .await
    .unwrap()
}

async fn spells_of(pool: &PgPool, id: i64) -> Vec<i32> {
    sqlx::query_scalar(
        "SELECT spell_id FROM known_spells WHERE character_id = $1 ORDER BY spell_id",
    )
    .bind(id)
    .fetch_all(pool)
    .await
    .unwrap()
}

#[tokio::test]
async fn a_seat_is_one_per_session_whichever_section() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Skytalon").await;
    let s = session(1);
    let first = attendance(admit(pool, &p, s, 0).await);
    // Again (a retry), or into another section after a restart: the same row.
    assert_eq!(attendance(admit(pool, &p, s, 0).await), first);
    assert_eq!(attendance(admit(pool, &p, s, 3).await), first);
    let (section, rows): (i16, i64) = sqlx::query_as(
        "SELECT cs.section, (SELECT count(*) FROM class_attendance WHERE character_id = $2)
         FROM class_attendance ca JOIN class_sessions cs ON cs.id = ca.session_id WHERE ca.id = $1",
    )
    .bind(first)
    .bind(p.id)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!((section, rows), (3, 1));
    // Rewarded: barred from the session from then on; other sessions open.
    let out = classes::reward(pool, &request(&p, first, 80, 10), Utc::now())
        .await
        .unwrap();
    assert!(matches!(out, RewardOutcome::Granted(_)));
    assert_eq!(admit(pool, &p, s, 0).await, Admission::AlreadyAttended);
    let other = attendance(admit(pool, &p, session(2), 0).await);
    assert_ne!(other, first);
}

#[tokio::test]
async fn rewards_are_granted_once_even_when_raced() {
    let app = TestApp::with_pool(10, |_| {}).await;
    let pool = app.state.pool.clone();
    let p = app.player("Burrowsby").await;
    let id = attendance(admit(&pool, &p, session(3), 0).await);
    let score = 92;
    let req = Arc::new(request(&p, id, score, 20));
    let mut tasks = Vec::new();
    for _ in 0..8 {
        let (pool, req) = (pool.clone(), req.clone());
        tasks.push(tokio::spawn(async move {
            classes::reward(&pool, &req, Utc::now()).await.unwrap()
        }));
    }
    let mut granted = Vec::new();
    let mut repeats = Vec::new();
    for t in tasks {
        match t.await.unwrap() {
            RewardOutcome::Granted(r) => granted.push(r),
            RewardOutcome::AlreadyRewarded(r) => repeats.push(r),
            RewardOutcome::NotTakingPart => panic!("took part"),
        }
    }
    assert_eq!(granted.len(), 1, "exactly one grant");
    assert_eq!(repeats.len(), 7);
    let r = &granted[0];
    let (attend, bonus) = class_xp(score);
    let charms = shipped().class("charms").unwrap();
    assert_eq!(r.rewards.xp, attend + bonus);
    assert_eq!(r.rewards.skill_xp, charms.rewards.skill_xp(score));
    assert_eq!(r.rewards.house_points, charms.rewards.house_points(score));
    // Retries answer what the first granted (spells aside).
    for rep in &repeats {
        assert_eq!(
            (rep.xp, rep.skill_xp, rep.house_points),
            (r.rewards.xp, r.rewards.skill_xp, r.rewards.house_points)
        );
    }
    // The ledgers hold it once.
    assert_eq!(
        ledger(&pool, p.id).await,
        vec![
            ("class_attend".into(), Some(id), attend as i32),
            ("class_bonus".into(), Some(id), bonus as i32),
        ]
    );
    let points: Vec<(String, i32, String, Option<i64>)> = sqlx::query_as(
        "SELECT house, points, source, ref_id FROM house_points WHERE character_id = $1",
    )
    .bind(p.id)
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(
        points,
        vec![(
            "burrowsby".into(),
            i32::from(r.rewards.house_points),
            "class".into(),
            Some(id)
        )]
    );
    let skill: i32 = sqlx::query_scalar(
        "SELECT xp FROM skills WHERE character_id = $1 AND discipline = 'charms'",
    )
    .bind(p.id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(skill as u32, r.rewards.skill_xp);
    let (xp, level, rewarded): (i32, i16, bool) = sqlx::query_as(
        "SELECT c.xp, c.level, a.rewarded_at IS NOT NULL FROM characters c
         JOIN class_attendance a ON a.character_id = c.id WHERE a.id = $1",
    )
    .bind(id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!((xp as u32, level, rewarded), (attend + bonus, 1, true));
    // A first class at level 1 teaches Lucerna (Elevara needs level 2).
    let lucerna = shipped().spells.by_key("lucerna").unwrap().id;
    assert_eq!(r.rewards.spells, vec![lucerna]);
    assert_eq!(spells_of(&pool, p.id).await, vec![i32::from(lucerna)]);
    // The audit reconciles the XP, the attendance and the house points.
    server::ledger::audit::reconcile(&pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn too_little_taking_part_earns_nothing_and_a_level_up_teaches_more() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Roarwing").await;
    let min = shipped().class("charms").unwrap().rewards.min_answers;
    let id = attendance(admit(pool, &p, session(4), 0).await);
    let out = classes::reward(pool, &request(&p, id, 40, min - 1), Utc::now())
        .await
        .unwrap();
    assert_eq!(out, RewardOutcome::NotTakingPart);
    assert!(ledger(pool, p.id).await.is_empty());
    let (score, rewarded): (Option<i16>, bool) = sqlx::query_as(
        "SELECT score_pct, rewarded_at IS NOT NULL FROM class_attendance WHERE id = $1",
    )
    .bind(id)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!((score, rewarded), (Some(40), false), "the score is kept");

    // Just short of level 2: this class takes them over and teaches both.
    let level2 = shipped().progression.levels[1];
    sqlx::query("UPDATE characters SET xp = $2 WHERE id = $1")
        .bind(p.id)
        .bind(level2 as i32 - 10)
        .execute(pool)
        .await
        .unwrap();
    let id = attendance(admit(pool, &p, session(5), 0).await);
    let RewardOutcome::Granted(r) = classes::reward(pool, &request(&p, id, 0, min), Utc::now())
        .await
        .unwrap()
    else {
        panic!("granted");
    };
    assert!(r.leveled_up());
    assert_eq!(r.grants.len(), 1, "no bonus at 0 %");
    assert_eq!(r.grants[0].level, 2);
    assert_eq!(r.rewards.house_points, 0);
    assert_eq!(r.rewards.spells.len(), 2, "Lucerna and Elevara");
    assert_eq!(r.learned, r.rewards.spells);
    // Someone else's attendance is not theirs to claim.
    let q = app.player("Serpentyne").await;
    let err = classes::reward(pool, &request(&q, id, 100, min), Utc::now()).await;
    assert!(err.is_err());
}

#[tokio::test]
async fn the_routes_list_sessions_and_attendance() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Skytalon").await;
    let res = app.get("/api/classes/schedule", p.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let charms = &res.body["classes"][0];
    assert_eq!(charms["id"], "charms");
    // The timetable slots, each with the classes it starts.
    let slots = res.body["slots"].as_array().unwrap();
    assert_eq!(slots.len(), shipped().schedule.slots.len());
    assert!(slots.iter().any(|s| {
        s["classes"]
            .as_array()
            .unwrap()
            .iter()
            .any(|c| c == "potions")
    }));
    assert_eq!(charms["landmark"], "classroom_door_1");
    assert!(
        charms["next"]["start_ms"].as_i64().unwrap() > res.body["now_unix_ms"].as_i64().unwrap()
    );
    assert_eq!(
        app.get("/api/classes/schedule", None).await.status,
        StatusCode::UNAUTHORIZED
    );

    let mut ids = Vec::new();
    for k in 0..3 {
        ids.push(attendance(admit(pool, &p, session(10 + k), 0).await));
    }
    classes::reward(pool, &request(&p, ids[2], 75, 9), Utc::now())
        .await
        .unwrap();
    let page = app.get("/api/classes/attendance?limit=2", p.token()).await;
    assert_eq!(page.status, StatusCode::OK, "{:?}", page.body);
    let items = page.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 2);
    assert_eq!(items[0]["id"], ids[2], "newest first");
    assert_eq!(items[0]["score_pct"], 75);
    assert!(items[0]["rewarded_at"].is_string());
    assert!(items[1]["score_pct"].is_null());
    let next = page.body["next"].as_str().unwrap();
    let rest = app
        .get(&format!("/api/classes/attendance?after={next}"), p.token())
        .await;
    let items = rest.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0]["id"], ids[0]);
    assert!(rest.body["next"].is_null());
    let bad = app
        .get("/api/classes/attendance?after=zzz", p.token())
        .await;
    assert_eq!(bad.body["error"], "invalid_cursor");
}

// ---------------------------------------------------------------- end to end

/// The hall of the realtime tests with the Charms door at its spawn.
const LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 }
    ],
    "spawns": [{ "id": "door", "pos": [130, 0, 100], "yaw": 0 }],
    "interactables": [
        { "id": "charms_classroom_door", "kind": "class_door", "pos": [130, 0, 100], "radius": 1.5 }
    ]
}"#;

const WAIT: Duration = Duration::from_secs(5);

struct Server {
    addr: SocketAddr,
    state: AppState,
}

impl Server {
    async fn start() -> Self {
        Self::with_world(sim_core::World::from_json(LAYOUT).unwrap()).await
    }

    async fn with_world(world: sim_core::World) -> Self {
        let config = Config {
            database_url: std::env::var("DATABASE_URL")
                .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
            dev: true,
            ..Config::default()
        };
        let pool = db::connect_with(&config.database_url, 4).await.unwrap();
        let mut state = AppState::new(pool, config, Arc::new(email::DevMailer));
        state.start_realtime(world, WordFilter::from_list("badword"));
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
        Server { addr, state }
    }

    /// Sets the game clock to read `unix_ms` now.
    fn set_clock(&self, unix_ms: i64) {
        let clock = self.state.world().unwrap().clock();
        let real = clock.now_unix_ms() - clock.skew_ms();
        clock.set_skew_ms(unix_ms - real);
    }

    async fn player(&self) -> (i64, Client) {
        let app = TestApp::from_state(self.state.clone());
        let name = common::unique_name();
        let res = app
            .post(
                "/api/dev/bot-session",
                serde_json::json!({ "name": name }),
                None,
            )
            .await;
        let token = res.body["token"].as_str().unwrap().to_owned();
        let (ws, _) = tokio_tungstenite::connect_async(format!("ws://{}/ws", self.addr))
            .await
            .unwrap();
        let mut c = Client { ws };
        c.send(&ClientMsg::Hello {
            protocol_version: PROTOCOL_VERSION,
            token,
            character_id: None,
        })
        .await;
        c.expect(|m| matches!(m, ServerMsg::Welcome { .. }).then_some(()))
            .await;
        let me: i64 = sqlx::query_scalar("SELECT id FROM characters WHERE name = $1")
            .bind(&name)
            .fetch_one(&self.state.pool)
            .await
            .unwrap();
        (me, c)
    }
}

struct Client {
    ws: WebSocketStream<MaybeTlsStream<TcpStream>>,
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
            let next = tokio::time::timeout(WAIT, self.ws.next())
                .await
                .expect("a message in time")
                .expect("still open")
                .unwrap();
            if let Message::Binary(b) = next
                && let Some(t) = f(&decode_server(&b).unwrap())
            {
                return t;
            }
        }
    }
}

/// A careful hand-drawn twirl and tap over `ms` (wobble varies by `seed`).
fn twirl(ms: u32, seed: f32) -> Vec<TracePoint> {
    let g = shipped().gestures.by_key("twirl_and_tap").unwrap();
    let n = g.points.len();
    let per = 3;
    let total = (n - 1) * per + 1;
    let dt = (ms / (total as u32 - 1)) as u8;
    (0..total)
        .map(|k| {
            let (i, s) = ((k / per).min(n - 2), (k % per) as f32 / per as f32);
            let s = if k == total - 1 { 1.0 } else { s };
            let (a, b) = (g.points[i], g.points[i + 1]);
            let w = 4.0 * ((k as f32 * 1.7 + seed).sin() + (k as f32 * 0.6).cos()) / 2.0;
            TracePoint {
                dt_ms: if k == 0 { 0 } else { dt },
                x: (f32::from(a[0]) + s * f32::from(b[0] - a[0]) + w).round() as i8,
                y: (f32::from(a[1]) + s * f32::from(b[1] - a[1]) - w).round() as i8,
            }
        })
        .collect()
}

#[tokio::test]
async fn a_whole_lesson_end_to_end() {
    let server = Server::start().await;
    let pool = server.state.pool.clone();
    let schedule = &shipped().schedule;
    let charms = schedule.class("charms").unwrap();
    let times = schedule.next(charms, Utc::now().timestamp_millis());
    let at = |ms: i64| times.start_ms + ms;

    let (me, mut a) = server.player().await;
    // Before the door opens.
    server.set_clock(at(-60_000));
    a.send(&ClientMsg::Interact {
        target: "charms_classroom_door".into(),
    })
    .await;
    let code = a
        .expect(|m| match m {
            ServerMsg::ClassError { code } => Some(*code),
            _ => None,
        })
        .await;
    assert_eq!(code, ClassErrorCode::NotOpen);

    // In the join window: into section 0, the professor waiting.
    server.set_clock(at(10_000));
    a.send(&ClientMsg::Interact {
        target: "charms_classroom_door".into(),
    })
    .await;
    let zone = a
        .expect(|m| match m {
            ServerMsg::ZoneChanged { zone, .. } => Some(zone.clone()),
            _ => None,
        })
        .await;
    assert!(matches!(zone, Zone::Class { section: 0, .. }), "{zone:?}");
    let info = a
        .expect(|m| match m {
            ServerMsg::ClassJoined { info } => Some(info.clone()),
            _ => None,
        })
        .await;
    assert_eq!(info.session, times.key.id());
    assert_ne!(info.professor, 0);

    // Round 1: three beats traced on time.
    server.set_clock(at(200_000));
    let beats = a
        .expect(|m| match m {
            ServerMsg::LessonRound {
                round: 1, beats, ..
            } => Some(beats.clone()),
            _ => None,
        })
        .await;
    for (k, beat) in beats.iter().take(3).enumerate() {
        // The trace "started" 40 ms after the beat and took 900 ms.
        server.set_clock(beat.at_unix_ms as i64 + 40 + 900 + 30);
        a.send(&ClientMsg::TraceGesture {
            round: 1,
            beat: k as u8,
            start_offset_ms: 40,
            points: twirl(900, k as f32),
        })
        .await;
        let r = a
            .expect(|m| match m {
                ServerMsg::BeatScored { result, .. } => Some(*result),
                _ => None,
            })
            .await;
        assert_eq!(r.rejected, None, "{r:?}");
        assert!(r.grade >= Grade::Good, "{r:?}");
    }
    // The same samples again: a replay.
    server.set_clock(beats[3].at_unix_ms as i64 + 1_000);
    a.send(&ClientMsg::TraceGesture {
        round: 1,
        beat: 3,
        start_offset_ms: 40,
        points: twirl(900, 0.0),
    })
    .await;
    let r = a
        .expect(|m| match m {
            ServerMsg::BeatScored { result, .. } => Some(*result),
            _ => None,
        })
        .await;
    assert_eq!(r.rejected, Some(TraceReject::Replayed));

    // The results: rewards, the new total, Lucerna.
    server.set_clock(at(411_000));
    let (score_pct, rewards) = a
        .expect(|m| match m {
            ServerMsg::ClassResult {
                score_pct, rewards, ..
            } => Some((*score_pct, rewards.clone())),
            _ => None,
        })
        .await;
    let (attend, bonus) = class_xp(score_pct);
    assert_eq!(rewards.xp, attend + bonus);
    let gained = a
        .expect(|m| match m {
            ServerMsg::Progress {
                gained: Some(g), ..
            } => Some(*g),
            _ => None,
        })
        .await;
    assert_eq!(gained.source, XpSource::ClassAttend);
    let new = a
        .expect(|m| match m {
            ServerMsg::Spellbook { new, .. } => Some(new.clone()),
            _ => None,
        })
        .await;
    assert_eq!(new, vec![shipped().spells.by_key("lucerna").unwrap().id]);
    let (xp, rewarded): (i32, bool) = sqlx::query_as(
        "SELECT c.xp, a.rewarded_at IS NOT NULL FROM characters c
         JOIN class_attendance a ON a.character_id = c.id
         WHERE c.id = $1 AND a.session_key = $2",
    )
    .bind(me)
    .bind(times.key.id())
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!((xp as u32, rewarded), (attend + bonus, true));

    // The end: back to the corridor.
    server.set_clock(at(541_000));
    a.expect(|m| match m {
        ServerMsg::LessonPhaseChanged {
            phase: LessonPhase::Ended,
            ..
        } => Some(()),
        _ => None,
    })
    .await;
    a.expect(|m| match m {
        ServerMsg::ZoneChanged {
            zone: Zone::Castle, ..
        } => Some(()),
        _ => None,
    })
    .await;
}

/// The shipped castle: the third-floor Charms door, in reach of its own
/// position, takes a student into the shipped classroom with its professor.
#[tokio::test]
async fn the_castle_charms_door_admits_students() {
    let path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/layout.json");
    let world = server::realtime::load_world(&path).expect("the shipped castle loads");
    let charms = shipped().schedule.class("charms").unwrap();
    let door = world.interactable(&charms.door).unwrap().pos;
    let server = Server::with_world(world).await;
    let (me, mut a) = server.player().await;
    let times = shipped()
        .schedule
        .next(charms, Utc::now().timestamp_millis());
    server.set_clock(times.start_ms + 5_000);
    assert!(
        server.state.world().unwrap().dev_teleport(me, door).await,
        "the door stands on walkable ground"
    );
    a.send(&ClientMsg::Interact {
        target: charms.door.clone(),
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
        Zone::Class {
            class: "charms".into(),
            section: 0,
            layout: charms.layout.clone(),
        }
    );
    let info = a
        .expect(|m| match m {
            ServerMsg::ClassJoined { info } => Some(info.clone()),
            _ => None,
        })
        .await;
    // The professor stands in the room, drawn from the data.
    let npc = a
        .expect(|m| match m {
            ServerMsg::Snapshot { entered, .. } => entered.iter().find_map(|e| match e {
                protocol::EntityEntry::Full(i) if i.id == info.professor => i.npc.clone(),
                _ => None,
            }),
            _ => None,
        })
        .await;
    assert_eq!(npc, shipped().class("charms").unwrap().professor.id);
}

/// Potions session `k` of a fixed far-off day.
fn potions_session(k: i64) -> SessionTimes {
    let schedule = &shipped().schedule;
    let potions = schedule.class("potions").unwrap();
    schedule.next(potions, 4_100_000_000_000 + k * 7_200_000)
}

async fn admit_to(pool: &PgPool, p: &TestPlayer, class: &str, s: SessionTimes) -> i64 {
    let req = AdmitRequest {
        class_id: class.into(),
        session: s,
        section: 0,
        character_id: p.id,
    };
    attendance(classes::admit(pool, &req).await.unwrap())
}

fn potions_request(p: &TestPlayer, attendance_id: i64, score_pct: u8) -> RewardRequest {
    RewardRequest {
        attendance_id,
        character_id: p.id,
        class_id: "potions".into(),
        score_pct,
        answers: 20,
        section_pct: score_pct,
    }
}

async fn add_skill(pool: &PgPool, p: &TestPlayer, subject: sim_core::Subject, xp: u32) {
    let mut tx = pool.begin().await.unwrap();
    server::skills::add_xp(&mut tx, p.id, subject, xp)
        .await
        .unwrap();
    tx.commit().await.unwrap();
}

async fn known_recipes(pool: &PgPool, id: i64) -> Vec<i32> {
    sqlx::query_scalar(
        "SELECT recipe_id FROM known_recipes WHERE character_id = $1 ORDER BY recipe_id",
    )
    .bind(id)
    .fetch_all(pool)
    .await
    .unwrap()
}

#[tokio::test]
async fn potions_rewards_v2_are_granted_once_ledgered_and_audited() {
    let app = TestApp::with_pool(10, |_| {}).await;
    let pool = app.state.pool.clone();
    let p = app.player("Serpentyne").await;
    let potions = shipped().class("potions").unwrap();
    let rules = &potions.rewards;
    // Just short of Potions 5: this class takes them over, so it teaches the
    // level-4 and level-5 brews.
    let levels = &shipped().recipes.skill_levels;
    let score = 90;
    let skill_gain = rules.skill_xp(score) + rules.section_bonus(score);
    add_skill(
        &pool,
        &p,
        sim_core::Subject::Potions,
        levels[3] - skill_gain + 1,
    )
    .await;
    let purse: i64 = sqlx::query_scalar("SELECT crowns FROM characters WHERE id = $1")
        .bind(p.id)
        .fetch_one(&pool)
        .await
        .unwrap();
    let id = admit_to(&pool, &p, "potions", potions_session(1)).await;
    let req = Arc::new(potions_request(&p, id, score));
    let mut tasks = Vec::new();
    for _ in 0..6 {
        let (pool, req) = (pool.clone(), req.clone());
        tasks.push(tokio::spawn(async move {
            classes::reward(&pool, &req, Utc::now()).await.unwrap()
        }));
    }
    let mut granted = Vec::new();
    let mut repeats = Vec::new();
    for t in tasks {
        match t.await.unwrap() {
            RewardOutcome::Granted(r) => granted.push(r),
            RewardOutcome::AlreadyRewarded(r) => repeats.push(r),
            RewardOutcome::NotTakingPart => panic!("took part"),
        }
    }
    assert_eq!((granted.len(), repeats.len()), (1, 5));
    let r = &granted[0];
    let crowns = rules.crowns(score);
    assert_eq!(crowns, 10 + 9);
    assert_eq!(r.rewards.crowns, crowns);
    assert_eq!(r.crowns_balance, Some(purse + i64::from(crowns)));
    let items: Vec<protocol::RewardItem> = rules
        .items(score, 0)
        .into_iter()
        .map(|(item_def, quantity)| protocol::RewardItem { item_def, quantity })
        .collect();
    assert_eq!(
        items.len(),
        2,
        "Moonstone Dust, and Moonknot at 80 % or more"
    );
    assert_eq!(r.rewards.items, items);
    assert_eq!(r.rewards.recipes, vec![330, 312]);
    assert_eq!(r.rewards.skill_xp, skill_gain);
    assert_eq!(r.rewards.lore, None);
    assert_eq!(r.rewards.fortune, None);
    // Retries answer what was granted (the spells and the cap
    // flag too).
    for rep in &repeats {
        assert_eq!(rep, &r.rewards);
    }
    // The ledgers hold it once, naming the attendance.
    let paid: Vec<(i64, String, Option<i64>)> = sqlx::query_as(
        "SELECT delta, reason, ref_id FROM crown_ledger
         WHERE character_id = $1 AND reason = 'class'",
    )
    .bind(p.id)
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(paid, vec![(i64::from(crowns), "class".into(), Some(id))]);
    for it in &items {
        let units: i64 = sqlx::query_scalar(
            "SELECT coalesce(sum(quantity), 0)::bigint FROM items
             WHERE owner_id = $1 AND item_def = $2 AND location = 'inventory'",
        )
        .bind(p.id)
        .bind(i32::from(it.item_def))
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(units, i64::from(it.quantity));
    }
    assert_eq!(known_recipes(&pool, p.id).await, vec![312, 330]);
    let skill: i32 = sqlx::query_scalar(
        "SELECT xp FROM skills WHERE character_id = $1 AND discipline = 'potions'",
    )
    .bind(p.id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(skill as u32, levels[3] + 1);

    // Another class: items stack, known recipes are not taught again.
    let id2 = admit_to(&pool, &p, "potions", potions_session(2)).await;
    let RewardOutcome::Granted(r2) =
        classes::reward(&pool, &potions_request(&p, id2, 100), Utc::now())
            .await
            .unwrap()
    else {
        panic!("granted");
    };
    assert!(r2.rewards.recipes.is_empty(), "{:?}", r2.rewards.recipes);
    assert_eq!(r2.rewards.crowns, 20);
    let stacks: i64 =
        sqlx::query_scalar("SELECT count(*) FROM items WHERE owner_id = $1 AND item_def = $2")
            .bind(p.id)
            .bind(i32::from(items[0].item_def))
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(stacks, 1, "merged into the stack");
    // The attendance page shows the Crowns.
    let page = app.get("/api/classes/attendance", p.token()).await;
    assert_eq!(page.body["items"][0]["crowns"], 20);
    // The skills route lists all ten subjects (Flying last).
    let skills = app.get("/api/skills", p.token()).await;
    let list = skills.body["skills"].as_array().unwrap();
    assert_eq!(list.len(), 10);
    assert_eq!(list[2]["discipline"], "potions");
    assert_eq!(list[8]["discipline"], "creatures");
    assert_eq!(list[9]["discipline"], "flying");
    server::ledger::audit::reconcile(&pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn the_daily_class_cap_limits_class_xp_but_not_the_rest() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Burrowsby").await;
    let now = Utc::now();
    let cap = shipped()
        .progression
        .xp
        .daily_caps
        .iter()
        .find(|c| c.group == "class")
        .unwrap()
        .xp;
    sqlx::query(
        "INSERT INTO daily_caps (character_id, day, cap, used) VALUES ($1, $2, 'class', $3)",
    )
    .bind(p.id)
    .bind(now.date_naive())
    .bind(cap as i32 - 40)
    .execute(pool)
    .await
    .unwrap();
    let id = admit_to(pool, &p, "potions", potions_session(3)).await;
    let RewardOutcome::Granted(r) = classes::reward(pool, &potions_request(&p, id, 100), now)
        .await
        .unwrap()
    else {
        panic!("granted");
    };
    assert_eq!(r.rewards.xp, 40, "only what was left of the cap");
    assert!(r.rewards.xp_capped);
    assert_eq!(r.rewards.crowns, 20, "Crowns are not capped");
    let rules = &shipped().class("potions").unwrap().rewards;
    assert_eq!(
        r.rewards.skill_xp,
        rules.skill_xp(100) + rules.section_bonus(100)
    );
    // The next class that day gives no XP at all, but still the rest.
    let id = admit_to(pool, &p, "potions", potions_session(4)).await;
    let RewardOutcome::Granted(r) = classes::reward(pool, &potions_request(&p, id, 100), now)
        .await
        .unwrap()
    else {
        panic!("granted");
    };
    assert_eq!((r.rewards.xp, r.rewards.xp_capped), (0, true));
    assert!(r.rewards.house_points > 0);
    server::ledger::audit::reconcile(pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

/// The castle hall with the Potions door at its spawn.
fn potions_layout() -> sim_core::World {
    sim_core::World::from_json(&LAYOUT.replace("charms_classroom_door", "potions_classroom_door"))
        .unwrap()
}

/// The right option of beat `beat` of `round` (the answer key the server
/// holds; the client is never sent it).
fn right_choice(class: &str, round: u8, beat: usize) -> u8 {
    let content = shipped();
    let def = content.class(class).unwrap();
    let (_, _, _, beats) = def.rounds().find(|r| r.1 == round).unwrap();
    let (_, held) = content
        .picks
        .resolve(
            def.picks.as_ref().unwrap(),
            content.answers(class).unwrap(),
            &beats[beat].kind,
        )
        .unwrap();
    held.answer
}

#[tokio::test]
async fn a_whole_potions_lesson_end_to_end() {
    let server = Server::with_world(potions_layout()).await;
    let pool = server.state.pool.clone();
    let schedule = &shipped().schedule;
    let potions = schedule.class("potions").unwrap();
    let def = shipped().class("potions").unwrap();
    let times = schedule.next(potions, Utc::now().timestamp_millis());
    let at = |ms: i64| times.start_ms + ms;

    let (me, mut a) = server.player().await;
    server.set_clock(at(10_000));
    a.send(&ClientMsg::Interact {
        target: "potions_classroom_door".into(),
    })
    .await;
    let zone = a
        .expect(|m| match m {
            ServerMsg::ZoneChanged { zone, .. } => Some(zone.clone()),
            _ => None,
        })
        .await;
    assert!(
        matches!(&zone, Zone::Class { class, section: 0, .. } if class == "potions"),
        "{zone:?}"
    );
    a.expect(|m| matches!(m, ServerMsg::ClassJoined { .. }).then_some(()))
        .await;

    // Rounds 1 and 4 (the first and the fastest): every beat picked on the
    // beat, right but for one. Real time passes between picks, as for a
    // player (the per-student rate limit is real time; the game clock jumps).
    let rounds: Vec<(u8, u32)> = def
        .rounds()
        .filter(|r| r.1 == 1 || r.1 == 4)
        .map(|r| (r.1, r.0.at_secs))
        .collect();
    let mut wrong_once = false;
    let mut right = 0u32;
    let mut answered = 0u32;
    for (round, at_secs) in rounds {
        server.set_clock(at(i64::from(at_secs) * 1_000 + 500));
        let beats = a
            .expect(|m| match m {
                ServerMsg::LessonRound {
                    round: r, beats, ..
                } if *r == round => Some(beats.clone()),
                _ => None,
            })
            .await;
        for (k, beat) in beats.iter().enumerate() {
            assert!(matches!(beat.prompt, protocol::BeatPrompt::Pick { .. }));
            server.set_clock(beat.at_unix_ms as i64 + 40);
            let mut choice = right_choice("potions", round, k);
            // One wrong pick (round 4's last beat): a miss, not a refusal.
            let wrong = round == 4 && k + 1 == beats.len();
            if wrong {
                choice = (choice + 1) % 5;
                wrong_once = true;
            }
            tokio::time::sleep(Duration::from_millis(1_050)).await;
            a.send(&ClientMsg::PickAnswer {
                round,
                beat: k as u8,
                choice,
                offset_ms: 30,
            })
            .await;
            answered += 1;
            right += u32::from(!wrong);
            let r = a
                .expect(|m| match m {
                    ServerMsg::BeatScored { result, .. } => Some(*result),
                    _ => None,
                })
                .await;
            assert_eq!(r.rejected, None, "{r:?}");
            assert_eq!(r.score, if wrong { 0 } else { 100 }, "{r:?}");
        }
        // A replay of the last answer: the beat is used up.
        tokio::time::sleep(Duration::from_millis(1_050)).await;
        let last = beats.len() as u8 - 1;
        a.send(&ClientMsg::PickAnswer {
            round,
            beat: last,
            choice: 0,
            offset_ms: 30,
        })
        .await;
        let r = a
            .expect(|m| match m {
                ServerMsg::BeatScored { result, .. } => Some(*result),
                _ => None,
            })
            .await;
        assert_eq!(r.rejected, Some(TraceReject::UnknownBeat));
    }
    assert!(wrong_once);

    // The results: everything a Potions class gives, and the new balance.
    let results = def
        .lesson
        .iter()
        .find(|s| matches!(s.action, sim_core::LessonAction::Results { .. }))
        .unwrap()
        .at_secs;
    server.set_clock(at(i64::from(results) * 1_000 + 500));
    let (score_pct, rewards) = a
        .expect(|m| match m {
            ServerMsg::ClassResult {
                score_pct, rewards, ..
            } => Some((*score_pct, rewards.clone())),
            _ => None,
        })
        .await;
    let n = def.beat_count() as u32;
    assert_eq!(u32::from(score_pct), right * 100 / n);
    let rules = &def.rewards;
    assert_eq!(rewards.crowns, rules.crowns(score_pct));
    assert_eq!(rewards.items.len(), rules.items(score_pct, 0).len());
    assert!(rewards.items.iter().all(|i| i.quantity > 0));
    let balance = a
        .expect(|m| match m {
            ServerMsg::Wallet { crowns } => Some(*crowns),
            _ => None,
        })
        .await;
    let held: i64 = sqlx::query_scalar("SELECT crowns FROM characters WHERE id = $1")
        .bind(me)
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(balance, held);
    let (answers, crowns): (i16, i32) = sqlx::query_as(
        "SELECT answers, crowns FROM class_attendance
         WHERE character_id = $1 AND class_id = 'potions' AND session_key = $2",
    )
    .bind(me)
    .bind(times.key.id())
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        answers as u32, answered,
        "a wrong pick still counts as an answer"
    );
    assert_eq!(crowns as u32, rewards.crowns);
    server::ledger::audit::reconcile(&pool, &[me])
        .await
        .unwrap()
        .assert_clean();
}
