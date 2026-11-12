//! The indoor classes over the database and end to end (docs/
//! architecture.md): each of Defensive Magic,
//! History of Magic, Divination and Astronomy (on the dev clock at night)
//! run once over WebSockets with answers scripted from the shipped data
//! (the gesture templates, traced by an unsteady hand, and the answer keys);
//! Defence teaching Titillara at level 3 and Repulsa at level 5; the
//! hint kept on the attendance so a retried reward answers it; the
//! Divination fortune running for an hour; and the lore routes serving the
//! text of unlocked entries only.

mod common;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::http::StatusCode;
use chrono::Utc;
use common::{TestApp, TestPlayer};
use futures_util::{SinkExt, StreamExt};
use protocol::{
    BeatPrompt, ClassRewards, ClientMsg, EffectKind, FortuneKind, PROTOCOL_VERSION, ServerMsg,
    TracePoint, Zone, decode_server, encode_client,
};
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use server::catalog::shipped;
use server::classes::{self, Admission, AdmitRequest, RewardOutcome, RewardRequest};
use server::realtime::chat::WordFilter;
use server::{AppState, Config, db, email, http};
use sim_core::Period;
use sim_core::{Gesture, LessonAction, SessionTimes, Subject};
use sqlx::PgPool;
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

const TITILLARA: u16 = 13;
const REPULSA: u16 = 14;

// ---------------------------------------------------------------- harness

/// A hall with the class's door at the spawn.
fn layout(door: &str) -> sim_core::World {
    let json = r#"{
        "version": 1,
        "surfaces": [
            { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
              "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 }
        ],
        "spawns": [{ "id": "door", "pos": [130, 0, 100], "yaw": 0 }],
        "interactables": [
            { "id": "DOOR", "kind": "class_door", "pos": [130, 0, 100], "radius": 1.5 }
        ]
    }"#;
    sim_core::World::from_json(&json.replace("DOOR", door)).unwrap()
}

const WAIT: Duration = Duration::from_secs(5);

struct Server {
    addr: SocketAddr,
    state: AppState,
}

impl Server {
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

    /// Sets the game clock to read `unix_ms` now (the dev skew).
    fn set_clock(&self, unix_ms: i64) {
        let clock = self.state.world().unwrap().clock();
        let real = clock.now_unix_ms() - clock.skew_ms();
        clock.set_skew_ms(unix_ms - real);
    }

    /// A fresh bot character signed in over WebSockets: its id, its
    /// session token and the connection.
    async fn player(&self) -> (i64, String, Client) {
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
            token: token.clone(),
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
        (me, token, c)
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

// ---------------------------------------------------------------- scripted answers

/// `n` points evenly spaced along the polyline `pts`.
fn resample(pts: &[[f32; 2]], n: usize) -> Vec<[f32; 2]> {
    let seg = |a: [f32; 2], b: [f32; 2]| ((b[0] - a[0]).powi(2) + (b[1] - a[1]).powi(2)).sqrt();
    let total: f32 = pts.windows(2).map(|w| seg(w[0], w[1])).sum();
    let step = total / (n - 1) as f32;
    let mut out = vec![pts[0]];
    let mut want = step;
    let mut walked = 0.0;
    for w in pts.windows(2) {
        let len = seg(w[0], w[1]);
        while walked + len >= want && out.len() < n {
            let t = (want - walked) / len.max(1e-6);
            out.push([
                w[0][0] + (w[1][0] - w[0][0]) * t,
                w[0][1] + (w[1][1] - w[0][1]) * t,
            ]);
            want += step;
        }
        walked += len;
    }
    while out.len() < n {
        out.push(*pts.last().unwrap());
    }
    out
}

/// Gesture `g`'s template (from `data/classes/gestures.json`) traced by an
/// unsteady hand over about the middle of its allowed time, which the
/// shared scorer grades at least Good when it starts `offset_ms` into a
/// beat of `window_ms`. Every seed gives a different trace (no replay).
fn hand_trace(g: &Gesture, window_ms: u16, offset_ms: i16, seed: u64) -> Vec<TracePoint> {
    let gestures = &shipped().gestures;
    let template: Vec<[f32; 2]> = g
        .points
        .iter()
        .map(|p| [f32::from(p[0]), f32::from(p[1])])
        .collect();
    let ms = (g.min_ms + g.max_ms) / 2;
    let n = ((ms / 25) as usize).clamp(12, 60);
    let dt = (ms / (n as u32 - 1)).clamp(1, 255) as u8;
    let mut rng = StdRng::seed_from_u64(seed);
    for _ in 0..50 {
        let scale = rng.gen_range(0.85..1.0f32);
        let shift = [rng.gen_range(-5.0..5.0f32), rng.gen_range(-5.0..5.0f32)];
        let phase = rng.gen_range(0.0..std::f32::consts::TAU);
        let wobble = rng.gen_range(2.0..5.0f32);
        let points: Vec<TracePoint> = resample(&template, n)
            .iter()
            .enumerate()
            .map(|(k, p)| {
                let w = wobble * (k as f32 * 0.45 + phase).sin();
                let x = p[0] * scale + shift[0] + w + rng.gen_range(-1.5..1.5f32);
                let y = p[1] * scale + shift[1] - w * 0.6 + rng.gen_range(-1.5..1.5f32);
                TracePoint {
                    dt_ms: if k == 0 { 0 } else { dt },
                    x: x.round().clamp(-100.0, 100.0) as i8,
                    y: y.round().clamp(-100.0, 100.0) as i8,
                }
            })
            .collect();
        let r = gestures.score(g, window_ms, i32::from(offset_ms), &points);
        if r.rejected.is_none() && r.grade >= protocol::Grade::Good {
            return points;
        }
    }
    panic!("no good trace of {}", g.key);
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

/// What one class run gave.
struct Ran {
    me: i64,
    token: String,
    client: Client,
    server: Server,
    times: SessionTimes,
    score_pct: u8,
    rewards: ClassRewards,
    /// Answers sent, and how many the server scored above 0.
    answered: u32,
    right: u32,
}

/// Runs class `class_id` once in a hall with its door: a fresh student
/// joins in the join window (`prepare` runs on its character first),
/// answers every beat of rounds `rounds` (traces of the beat's gesture,
/// picks from the answer key, on the beat), and stays for the results.
async fn run_class(class_id: &str, rounds: &[u8], prepare: impl AsyncFnOnce(&PgPool, i64)) -> Ran {
    let schedule = &shipped().schedule;
    let sched = schedule.class(class_id).unwrap();
    let def = shipped().class(class_id).unwrap();
    let server = Server::with_world(layout(&sched.door)).await;
    let pool = server.state.pool.clone();
    let times = schedule.next(sched, Utc::now().timestamp_millis());
    let at = |ms: i64| times.start_ms + ms;
    let (me, token, mut a) = server.player().await;
    prepare(&pool, me).await;

    server.set_clock(at(10_000));
    a.send(&ClientMsg::Interact {
        target: sched.door.clone(),
    })
    .await;
    let zone = a
        .expect(|m| match m {
            ServerMsg::ZoneChanged { zone, .. } => Some(zone.clone()),
            _ => None,
        })
        .await;
    assert!(
        matches!(&zone, Zone::Class { class, section: 0, layout } if class == class_id && *layout == sched.layout),
        "{zone:?}"
    );
    a.expect(|m| matches!(m, ServerMsg::ClassJoined { .. }).then_some(()))
        .await;

    let gestures = &shipped().gestures;
    let (mut answered, mut right) = (0u32, 0u32);
    for &round in rounds {
        let (step, _, _, _) = def.rounds().find(|r| r.1 == round).unwrap();
        server.set_clock(at(i64::from(step.at_secs) * 1_000 + 500));
        let (window_ms, beats) = a
            .expect(|m| match m {
                ServerMsg::LessonRound {
                    round: r,
                    window_ms,
                    beats,
                } if *r == round => Some((*window_ms, beats.clone())),
                _ => None,
            })
            .await;
        for (k, beat) in beats.iter().enumerate() {
            // Real time between answers, as for a player (the per-student
            // rate limit is real time; the game clock jumps).
            tokio::time::sleep(Duration::from_millis(1_050)).await;
            let offset: i16 = 40;
            let msg = match &beat.prompt {
                BeatPrompt::Trace { gesture } => {
                    let g = gestures.get(*gesture).unwrap();
                    let seed = u64::from(round) * 1_000 + k as u64;
                    let points = hand_trace(g, window_ms, offset, seed);
                    let took: i64 = points.iter().map(|p| i64::from(p.dt_ms)).sum();
                    server.set_clock(beat.at_unix_ms as i64 + i64::from(offset) + took + 30);
                    ClientMsg::TraceGesture {
                        round,
                        beat: k as u8,
                        start_offset_ms: offset,
                        points,
                    }
                }
                BeatPrompt::Pick { .. } => {
                    server.set_clock(beat.at_unix_ms as i64 + i64::from(offset));
                    ClientMsg::PickAnswer {
                        round,
                        beat: k as u8,
                        choice: right_choice(class_id, round, k),
                        offset_ms: offset,
                    }
                }
                // These classes have no ring beats.
                BeatPrompt::Ring { .. } => unreachable!("{class_id}: a ring beat"),
            };
            a.send(&msg).await;
            answered += 1;
            let r = a
                .expect(|m| match m {
                    ServerMsg::BeatScored { result, .. } => Some(*result),
                    _ => None,
                })
                .await;
            assert_eq!(r.rejected, None, "{class_id} round {round} beat {k}: {r:?}");
            right += u32::from(r.score > 0);
        }
    }

    let results = def
        .lesson
        .iter()
        .find(|s| matches!(s.action, LessonAction::Results { .. }))
        .unwrap()
        .at_secs;
    server.set_clock(at(i64::from(results) * 1_000 + 500));
    let (score_pct, rewards) = a
        .expect(|m| match m {
            ServerMsg::ClassResult {
                class,
                score_pct,
                rewards,
                ..
            } if class == class_id => Some((*score_pct, rewards.clone())),
            _ => None,
        })
        .await;
    assert!(score_pct > 0, "{class_id}");
    Ran {
        me,
        token,
        client: a,
        server,
        times,
        score_pct,
        rewards,
        answered,
        right,
    }
}

async fn nothing(_: &PgPool, _: i64) {}

/// The student's attendance row of the session `ran` took part in.
async fn attendance_of(pool: &PgPool, ran: &Ran, class: &str) -> i64 {
    sqlx::query_scalar(
        "SELECT id FROM class_attendance
         WHERE character_id = $1 AND class_id = $2 AND session_key = $3",
    )
    .bind(ran.me)
    .bind(class)
    .bind(ran.times.key.id())
    .fetch_one(pool)
    .await
    .unwrap()
}

/// The common checks of a class run: Crowns, skill XP into the class's
/// subject, every answer counted, the audit clean.
async fn check_common(ran: &Ran, class: &str, subject: Subject) {
    let pool = &ran.server.state.pool;
    let def = shipped().class(class).unwrap();
    assert_eq!(def.subject, subject);
    let rules = &def.rewards;
    assert_eq!(ran.right, ran.answered, "{class}: every answer scored");
    assert_eq!(ran.rewards.crowns, rules.crowns(ran.score_pct));
    assert_eq!(ran.rewards.skill_xp, rules.skill_xp(ran.score_pct));
    assert_eq!(
        ran.rewards.house_points,
        rules.house_points(ran.score_pct),
        "{class}"
    );
    let (answers, skill): (i16, i32) = sqlx::query_as(
        "SELECT a.answers, s.xp FROM class_attendance a
         JOIN skills s ON s.character_id = a.character_id AND s.discipline = $3
         WHERE a.character_id = $1 AND a.session_key = $2 AND a.class_id = $4",
    )
    .bind(ran.me)
    .bind(ran.times.key.id())
    .bind(subject.id())
    .bind(class)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(answers as u32, ran.answered);
    assert!(skill as u32 >= ran.rewards.skill_xp);
    server::ledger::audit::reconcile(pool, &[ran.me])
        .await
        .unwrap()
        .assert_clean();
}

// ---------------------------------------------------------------- end to end

#[tokio::test]
async fn a_defence_lesson_end_to_end() {
    // Rounds 1 (shields) and 3 (shields, spirals and pushes).
    let ran = run_class("defence", &[1, 3], nothing).await;
    // Seven beats a round, 4.8 s apart (time to finish each trace).
    assert_eq!(ran.answered, 7 + 7);
    check_common(&ran, "defence", Subject::Defence).await;
    // At level 1 the class teaches no spell (Titillara needs level 3).
    assert!(ran.rewards.spells.is_empty(), "{:?}", ran.rewards.spells);
    assert!(ran.rewards.items.is_empty() && ran.rewards.recipes.is_empty());
    assert_eq!((ran.rewards.lore, ran.rewards.fortune), (None, None));
}

#[tokio::test]
async fn a_history_lesson_end_to_end_unlocks_lore_and_a_stored_hint() {
    let ran = run_class("history", &[1], nothing).await;
    assert_eq!(ran.answered, 5);
    check_common(&ran, "history", Subject::History).await;
    // The first lore entry, and a hint naming a place (a fresh character
    // has searched nothing).
    assert_eq!(ran.rewards.lore, Some(1));
    let hint = ran.rewards.hint.clone().expect("a hint");
    assert!(!hint.trim().is_empty());
    let pool = ran.server.state.pool.clone();
    let id = attendance_of(&pool, &ran, "history").await;
    let stored: Option<String> =
        sqlx::query_scalar("SELECT hint FROM class_attendance WHERE id = $1")
            .bind(id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(stored.as_deref(), Some(hint.as_str()));
    // A retried reward (a lost reply, a restart) answers the same, the
    // hint included, and grants nothing more.
    let req = RewardRequest {
        attendance_id: id,
        character_id: ran.me,
        class_id: "history".into(),
        score_pct: ran.score_pct,
        answers: ran.answered as u8,
        section_pct: ran.score_pct,
    };
    let RewardOutcome::AlreadyRewarded(again) =
        classes::reward(&pool, &req, Utc::now()).await.unwrap()
    else {
        panic!("a retry");
    };
    assert_eq!(again, ran.rewards);
    // The lore book: entry 1 with its shipped text, from the server.
    let app = TestApp::from_state(ran.server.state.clone());
    let book = app.get("/api/lore", Some(&ran.token)).await;
    assert_eq!(book.status, StatusCode::OK, "{:?}", book.body);
    let entry = shipped().lore.get(1).unwrap();
    assert_eq!(book.body["items"].as_array().unwrap().len(), 1);
    assert_eq!(book.body["items"][0]["id"], 1);
    assert_eq!(book.body["items"][0]["title"], entry.title.as_str());
    assert_eq!(book.body["items"][0]["text"], entry.text.as_str());
    assert_eq!(book.body["items"][0]["subject"], "history");
    let one = app.get("/api/lore/1", Some(&ran.token)).await;
    assert_eq!(one.status, StatusCode::OK, "{:?}", one.body);
    assert_eq!(one.body["text"], entry.text.as_str());
    assert_eq!(
        app.get("/api/lore/2", Some(&ran.token)).await.status,
        StatusCode::NOT_FOUND
    );
}

#[tokio::test]
async fn a_divination_lesson_end_to_end_runs_a_fortune_for_an_hour() {
    let mut ran = run_class("divination", &[1], nothing).await;
    check_common(&ran, "divination", Subject::Divination).await;
    let kind = ran.rewards.fortune.expect("a fortune");
    assert!(matches!(
        kind,
        FortuneKind::Luck | FortuneKind::QuickHands | FortuneKind::KeenMind
    ));
    // On the student's own HUD, with an hour to run (nobody else sees it).
    let own = ran
        .client
        .expect(|m| match m {
            ServerMsg::OwnEffects { effects, .. } => effects
                .iter()
                .find(|e| e.kind == EffectKind::Fortune)
                .cloned(),
            _ => None,
        })
        .await;
    assert_eq!(own.fortune, Some(kind));
    assert!(
        (3_590..=3_600).contains(&own.remaining_secs),
        "{}",
        own.remaining_secs
    );
    // Stored, so it survives a relog, and it ends an hour from now.
    let stored = server::effects::load_active(&ran.server.state.pool, ran.me)
        .await
        .unwrap();
    assert_eq!(stored.len(), 1);
    assert_eq!(stored[0].kind, EffectKind::Fortune);
    assert_eq!(stored[0].fortune, Some(kind));
    let left = stored[0].until.saturating_duration_since(Instant::now());
    assert!(
        (3_590..=3_600).contains(&left.as_secs()),
        "{}",
        left.as_secs()
    );
    let ends_at: chrono::DateTime<Utc> =
        sqlx::query_scalar("SELECT expires_at FROM active_effects WHERE character_id = $1")
            .bind(ran.me)
            .fetch_one(&ran.server.state.pool)
            .await
            .unwrap();
    let secs = (ends_at - Utc::now()).num_seconds();
    assert!((3_590..=3_600).contains(&secs), "{secs}");
}

#[tokio::test]
async fn an_astronomy_lesson_at_night_teaches_a_night_brew() {
    let schedule = &shipped().schedule;
    let astronomy = schedule.class("astronomy").unwrap();
    // Astronomy Skill 2 before the class (the first night brew's level).
    let skill2 = shipped().recipes.skill_levels[0];
    let ran = run_class("astronomy", &[1], async |pool: &PgPool, me: i64| {
        let mut tx = pool.begin().await.unwrap();
        server::skills::add_xp(&mut tx, me, Subject::Astronomy, skill2)
            .await
            .unwrap();
        tx.commit().await.unwrap();
    })
    .await;
    // Night only: the session the dev clock was moved to starts at night.
    let clock = &schedule.clock;
    assert!(astronomy.night_only);
    assert_eq!(
        clock.period_at_secs(clock.game_time(ran.times.start_ms).secs),
        Period::Night
    );
    let game_now = ran.server.state.world().unwrap().clock().now_unix_ms();
    assert_eq!(
        clock.period_at_secs(clock.game_time(game_now).secs),
        Period::Night
    );
    assert_eq!(ran.answered, 6);
    check_common(&ran, "astronomy", Subject::Astronomy).await;
    // Skill 2 teaches the first night brew (331) and no other.
    assert_eq!(ran.rewards.recipes, vec![331]);
    let known: Vec<i32> = sqlx::query_scalar(
        "SELECT recipe_id FROM known_recipes WHERE character_id = $1 AND recipe_id > 330",
    )
    .bind(ran.me)
    .fetch_all(&ran.server.state.pool)
    .await
    .unwrap();
    assert_eq!(known, vec![331]);
    // Pearl Dust only at 70 % or more.
    let pearl = ran.rewards.items.iter().any(|i| i.item_def == 3027);
    assert_eq!(pearl, ran.score_pct >= 70);
}

// ---------------------------------------------------------------- over the database

/// Session `k` of class `class` on a fixed far-off day.
fn session(class: &str, k: i64) -> SessionTimes {
    let schedule = &shipped().schedule;
    schedule.next(
        schedule.class(class).unwrap(),
        4_300_000_000_000 + k * 7_200_000,
    )
}

async fn admit(pool: &PgPool, p: &TestPlayer, class: &str, k: i64) -> i64 {
    let req = AdmitRequest {
        class_id: class.into(),
        session: session(class, k),
        section: 0,
        character_id: p.id,
    };
    match classes::admit(pool, &req).await.unwrap() {
        Admission::Admitted { attendance_id } => attendance_id,
        Admission::AlreadyAttended => panic!("barred"),
    }
}

fn request(p: &TestPlayer, class: &str, attendance_id: i64, score_pct: u8) -> RewardRequest {
    RewardRequest {
        attendance_id,
        character_id: p.id,
        class_id: class.into(),
        score_pct,
        answers: 10,
        section_pct: score_pct,
    }
}

async fn set_level(pool: &PgPool, p: &TestPlayer, level: u8) {
    let xp = shipped().progression.level_start(level);
    sqlx::query("UPDATE characters SET xp = $2, level = $3 WHERE id = $1")
        .bind(p.id)
        .bind(xp as i32)
        .bind(i16::from(level))
        .execute(pool)
        .await
        .unwrap();
}

#[tokio::test]
async fn defence_teaches_titillara_at_level_3_and_repulsa_at_level_5() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let spells = &shipped().spells;
    assert_eq!(spells.by_key("titillara").unwrap().id, TITILLARA);
    assert_eq!(spells.by_key("repulsa").unwrap().id, REPULSA);
    for (level, taught) in [
        (1, vec![]),
        (2, vec![]),
        (3, vec![TITILLARA]),
        (4, vec![TITILLARA]),
        (5, vec![TITILLARA, REPULSA]),
    ] {
        let p = app.player("Roarwing").await;
        set_level(pool, &p, level).await;
        let id = admit(pool, &p, "defence", i64::from(level)).await;
        let RewardOutcome::Granted(r) =
            classes::reward(pool, &request(&p, "defence", id, 50), Utc::now())
                .await
                .unwrap()
        else {
            panic!("granted");
        };
        assert_eq!(r.grants.last().unwrap().level, level, "stays at {level}");
        assert_eq!(r.rewards.spells, taught, "level {level}");
        let known: Vec<i32> = sqlx::query_scalar(
            "SELECT spell_id FROM known_spells WHERE character_id = $1 ORDER BY spell_id",
        )
        .bind(p.id)
        .fetch_all(pool)
        .await
        .unwrap();
        assert_eq!(
            known,
            taught.iter().map(|&s| i32::from(s)).collect::<Vec<_>>()
        );
        // The Dueling Club's level-1 spells are not Defence's to teach.
        assert!(r.learned.iter().all(|s| taught.contains(s)));
    }
    // Titillara known from an earlier class: at level 5 only Repulsa is new.
    let p = app.player("Serpentyne").await;
    set_level(pool, &p, 3).await;
    let id = admit(pool, &p, "defence", 10).await;
    classes::reward(pool, &request(&p, "defence", id, 50), Utc::now())
        .await
        .unwrap();
    set_level(pool, &p, 5).await;
    let id = admit(pool, &p, "defence", 11).await;
    let RewardOutcome::Granted(r) =
        classes::reward(pool, &request(&p, "defence", id, 50), Utc::now())
            .await
            .unwrap()
    else {
        panic!("granted");
    };
    assert_eq!(r.rewards.spells, vec![REPULSA]);
    assert_eq!(r.learned, vec![TITILLARA, REPULSA]);
    // (No audit here: the levels were set without the XP ledger.)
}

#[tokio::test]
async fn a_raced_history_reward_answers_the_same_hint_every_time() {
    let app = TestApp::with_pool(8, |_| {}).await;
    let pool = app.state.pool.clone();
    let p = app.player("Burrowsby").await;
    let id = admit(&pool, &p, "history", 1).await;
    let req = Arc::new(request(&p, "history", id, 80));
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
            RewardOutcome::Granted(r) => granted.push(r.rewards),
            RewardOutcome::AlreadyRewarded(r) => repeats.push(r),
            RewardOutcome::NotTakingPart => panic!("took part"),
        }
    }
    assert_eq!((granted.len(), repeats.len()), (1, 5));
    let first = &granted[0];
    assert_eq!(first.lore, Some(1));
    assert!(first.hint.is_some());
    for rep in &repeats {
        assert_eq!(rep.hint, first.hint);
        assert_eq!(rep.lore, first.lore);
        assert_eq!(rep, first);
    }
    let unlocked: i64 =
        sqlx::query_scalar("SELECT count(*) FROM lore_unlocks WHERE character_id = $1")
            .bind(p.id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(unlocked, 1);
    server::ledger::audit::reconcile(&pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn the_lore_routes_serve_only_unlocked_text() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Skytalon").await;
    let q = app.player("Skytalon").await;
    let lore = &shipped().lore;
    assert!(lore.entries.len() >= 30, "{}", lore.entries.len());
    // Nothing unlocked: an empty book, and no entry by id.
    let empty = app.get("/api/lore", p.token()).await;
    assert_eq!(empty.status, StatusCode::OK, "{:?}", empty.body);
    assert_eq!(empty.body["items"], serde_json::json!([]));
    assert!(empty.body["next"].is_null());
    for id in [0, 1, 2, 99_999] {
        let res = app.get(&format!("/api/lore/{id}"), p.token()).await;
        assert_eq!(res.status, StatusCode::NOT_FOUND, "{id}");
        assert_eq!(res.body["error"], "lore_locked");
    }
    assert_eq!(
        app.get("/api/lore/1", None).await.status,
        StatusCode::UNAUTHORIZED
    );
    // Two History classes: entries 1 and 2, nothing else.
    for k in 0..2 {
        let id = admit(pool, &p, "history", 20 + k).await;
        classes::reward(pool, &request(&p, "history", id, 60), Utc::now())
            .await
            .unwrap();
    }
    let book = app.get("/api/lore?limit=50", p.token()).await;
    let items = book.body["items"].as_array().unwrap();
    let ids: Vec<i64> = items.iter().map(|e| e["id"].as_i64().unwrap()).collect();
    assert_eq!(ids, vec![2, 1], "newest first");
    for e in items {
        let entry = lore.get(e["id"].as_u64().unwrap() as u16).unwrap();
        assert_eq!(e["title"], entry.title.as_str());
        assert_eq!(e["text"], entry.text.as_str());
        assert_eq!(e["subject"], "history");
        assert!(e["unlocked_at"].is_string());
    }
    // No locked entry's title or text appears in anything served.
    let two = app.get("/api/lore/2", p.token()).await;
    assert_eq!(two.status, StatusCode::OK);
    let served = format!("{} {}", book.body, two.body);
    for e in lore.entries.iter().filter(|e| e.id > 2) {
        assert!(!served.contains(&e.text), "entry {} leaked", e.id);
        assert!(!served.contains(&e.title), "entry {} leaked", e.id);
    }
    // Someone else's book stays empty, and their entries are not served.
    let other = app.get("/api/lore", q.token()).await;
    assert_eq!(other.body["items"], serde_json::json!([]));
    assert_eq!(
        app.get("/api/lore/1", q.token()).await.status,
        StatusCode::NOT_FOUND
    );
}
