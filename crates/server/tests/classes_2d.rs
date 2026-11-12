//! Over the database and end to end (docs/architecture.md):
//! Herbology and Creature Keeping run once each over
//! WebSockets with the answer keys' picks (Crowns, skill XP with the
//! Creatures group bonus, house points, the fixed and the drawn materials
//! arriving and stacking, a clean audit); the full timetable over a game
//! day on the dev clock (18 sessions, every door opening exactly at its
//! slot); a retried reward reporting the spells it taught and the cap flag
//! (`0017_class_reward_retry.sql`); night-only brews refused by day and
//! brewed at night at the dawn and dusk edges; the dev grant raising any of
//! the nine subjects.

mod common;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::http::StatusCode;
use chrono::Utc;
use common::TestApp;
use futures_util::{SinkExt, StreamExt};
use protocol::{
    BeatPrompt, ClassErrorCode, ClassRewards, ClientMsg, PROTOCOL_VERSION, RewardItem, ServerMsg,
    Zone, decode_server, encode_client,
};
use serde_json::json;
use server::catalog::shipped;
use server::classes::{self, Admission, AdmitRequest, RewardOutcome, RewardRequest};
use server::realtime::chat::WordFilter;
use server::{AppState, Config, db, email, http};
use sim_core::{LessonAction, Period, SessionKey, SessionTimes, Subject};
use sqlx::PgPool;
use tokio::net::TcpStream;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

const TITILLARA: u16 = 13;
/// Elderberries (Herbology's first reward) and Lynxkit Fur (Creatures').
const ELDERBERRIES: u16 = 3015;
const LYNXKIT_FUR: u16 = 3008;
/// Brew Moonlit Glow Draught (night only): Moonflower Petals x2 (3017) and
/// Moonstone Dust (3021) into Moonlit Glow Draught x2 (3311).
const MOONLIT_RECIPE: u16 = 331;
const MOONFLOWER: u16 = 3017;
const MOONSTONE: u16 = 3021;
const MOONLIT: u16 = 3311;
/// Knit a Cable Beanie: a day recipe everyone knows (Wool Skein x3).
const BEANIE_RECIPE: u16 = 6;
const WOOL: u16 = 3001;

// ---------------------------------------------------------------- harness

/// A hall with every door `doors` at the spawn, in reach of everyone
/// spawned round it.
fn layout(doors: &[&str]) -> sim_core::World {
    let interactables: Vec<serde_json::Value> = doors
        .iter()
        .map(|d| json!({ "id": d, "kind": "class_door", "pos": [130, 0, 100], "radius": 8.0 }))
        .collect();
    let world = json!({
        "version": 1,
        "surfaces": [
            { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
              "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 }
        ],
        "spawns": [{ "id": "door", "pos": [130, 0, 100], "yaw": 0 }],
        "interactables": interactables,
    });
    sim_core::World::from_json(&world.to_string()).unwrap()
}

const WAIT: Duration = Duration::from_secs(5);

struct Server {
    addr: SocketAddr,
    state: AppState,
}

impl Server {
    async fn with_world(world: sim_core::World, tweak: impl FnOnce(&mut Config)) -> Self {
        let mut config = Config {
            database_url: std::env::var("DATABASE_URL")
                .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
            dev: true,
            ..Config::default()
        };
        tweak(&mut config);
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

    /// A fresh bot character signed in over WebSockets: its id and the
    /// connection.
    async fn player(&self) -> (i64, Client) {
        let app = TestApp::from_state(self.state.clone());
        let name = common::unique_name();
        let res = app
            .post("/api/dev/bot-session", json!({ "name": name }), None)
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

    /// The first message `f` picks, within 5 s of the last one and 30 s in
    /// all (snapshots keep coming while it waits).
    async fn expect<T>(&mut self, mut f: impl FnMut(&ServerMsg) -> Option<T>) -> T {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(30);
        loop {
            assert!(
                tokio::time::Instant::now() < deadline,
                "no expected message in 30 s"
            );
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

    /// Interacts with `door` and returns the zone it moved to, or the class
    /// error it got (any other refusal fails the test).
    async fn knock(&mut self, door: &str) -> Result<Zone, ClassErrorCode> {
        self.send(&ClientMsg::Interact {
            target: door.to_owned(),
        })
        .await;
        self.expect(|m| match m {
            ServerMsg::ZoneChanged { zone, .. } => Some(Ok(zone.clone())),
            ServerMsg::ClassError { code } => Some(Err(*code)),
            ServerMsg::DormError { .. } => panic!("{door}: {m:?}"),
            _ => None,
        })
        .await
    }
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
    server: Server,
    times: SessionTimes,
    score_pct: u8,
    rewards: ClassRewards,
    answered: u32,
    right: u32,
}

/// Runs pick class `class_id` once in a hall with its door: a fresh student
/// (given `before` first: materials it already holds) joins in the join
/// window, picks the answer key's option on every beat of every round, and
/// stays for the results.
async fn run_class(class_id: &str, before: &[(u16, u32)]) -> Ran {
    let schedule = &shipped().schedule;
    let sched = schedule.class(class_id).unwrap();
    let def = shipped().class(class_id).unwrap();
    let server = Server::with_world(layout(&[&sched.door]), |_| {}).await;
    let times = schedule.next(sched, Utc::now().timestamp_millis());
    let at = |ms: i64| times.start_ms + ms;
    let (me, mut a) = server.player().await;
    for &(item_def, quantity) in before {
        let mut tx = server.state.pool.begin().await.unwrap();
        let item = server::items::NewItem::plain(me, item_def, quantity, "inventory");
        server::items::give(&mut tx, &item, server::ledger::Reason::Admin, None)
            .await
            .unwrap();
        tx.commit().await.unwrap();
    }

    server.set_clock(at(10_000));
    let zone = a.knock(&sched.door).await.expect("admitted");
    assert!(
        matches!(&zone, Zone::Class { class, section: 0, layout } if class == class_id && *layout == sched.layout),
        "{zone:?}"
    );
    a.expect(|m| matches!(m, ServerMsg::ClassJoined { .. }).then_some(()))
        .await;

    let (mut answered, mut right) = (0u32, 0u32);
    let rounds: Vec<u8> = def.rounds().map(|r| r.1).collect();
    for round in rounds {
        let (step, _, _, _) = def.rounds().find(|r| r.1 == round).unwrap();
        server.set_clock(at(i64::from(step.at_secs) * 1_000 + 500));
        let beats = a
            .expect(|m| match m {
                ServerMsg::LessonRound {
                    round: r, beats, ..
                } if *r == round => Some(beats.clone()),
                _ => None,
            })
            .await;
        for (k, beat) in beats.iter().enumerate() {
            // Real time between answers, as for a player (the per-student
            // rate limit is real time; the game clock jumps).
            tokio::time::sleep(Duration::from_millis(1_050)).await;
            let offset: i16 = 40;
            assert!(matches!(beat.prompt, BeatPrompt::Pick { .. }), "a pick");
            server.set_clock(beat.at_unix_ms as i64 + i64::from(offset));
            a.send(&ClientMsg::PickAnswer {
                round,
                beat: k as u8,
                choice: right_choice(class_id, round, k),
                offset_ms: offset,
            })
            .await;
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
    Ran {
        me,
        server,
        times,
        score_pct,
        rewards,
        answered,
        right,
    }
}

/// Units and rows of `item_def` the character holds.
async fn holding(pool: &PgPool, owner: i64, item_def: u16) -> (i64, i64) {
    sqlx::query_as(
        "SELECT count(*), COALESCE(SUM(quantity), 0)::bigint FROM items
         WHERE owner_id = $1 AND item_def = $2",
    )
    .bind(owner)
    .bind(i32::from(item_def))
    .fetch_one(pool)
    .await
    .unwrap()
}

/// The checks both grounds classes share: every pick right, Crowns, skill
/// XP into the subject (plus the section's group bonus), house points, the
/// fixed materials then one drawn from the pool the score reached, each
/// landing in the student's inventory and the first stacking onto what it
/// held (`held` units of `stacked` before), the attendance row recording
/// it, and a clean audit.
async fn check_grounds(ran: &Ran, class: &str, subject: Subject, stacked: u16, held: u32) {
    let pool = &ran.server.state.pool;
    let def = shipped().class(class).unwrap();
    assert_eq!(def.subject, subject);
    let rules = &def.rewards;
    let score = ran.score_pct;
    assert_eq!(ran.right, ran.answered, "{class}: every pick right");
    assert!(score >= 90, "{class}: {score}");
    assert_eq!(ran.rewards.crowns, rules.crowns(score));
    assert_eq!(
        ran.rewards.skill_xp,
        rules.skill_xp(score) + rules.section_bonus(score),
        "{class}: alone in the section, its average is the student's"
    );
    assert_eq!(ran.rewards.house_points, rules.house_points(score));
    assert_eq!(ran.rewards.house_points, 5);

    // The fixed materials in file order, then one of the drawn pool.
    let fixed: Vec<RewardItem> = rules
        .items
        .iter()
        .filter(|r| !r.draw)
        .map(|r| RewardItem {
            item_def: r.item,
            quantity: r.quantity(score),
        })
        .filter(|r| r.quantity > 0)
        .collect();
    let pool_ids: Vec<u16> = rules
        .items
        .iter()
        .filter(|r| r.draw && r.min_pct <= score)
        .map(|r| r.item)
        .collect();
    assert!(!pool_ids.is_empty(), "{class}: the score reaches the draw");
    let got = &ran.rewards.items;
    assert_eq!(got.len(), fixed.len() + 1, "{got:?}");
    assert_eq!(&got[..fixed.len()], &fixed[..]);
    let drawn = got.last().unwrap();
    assert!(pool_ids.contains(&drawn.item_def), "{drawn:?}");
    assert_eq!(drawn.quantity, 1);

    // In the inventory: the first material stacked onto what was held.
    for it in got {
        let before = if it.item_def == stacked { held } else { 0 };
        let (rows, units) = holding(pool, ran.me, it.item_def).await;
        assert_eq!(rows, 1, "{class}: item {} in one stack", it.item_def);
        assert_eq!(units, i64::from(before + it.quantity), "{}", it.item_def);
    }
    assert_eq!(got[0].item_def, stacked);

    // Recorded on the attendance, so a retry answers the same.
    let (answers, items): (i16, sqlx::types::Json<Vec<RewardItem>>) = sqlx::query_as(
        "SELECT answers, items FROM class_attendance
         WHERE character_id = $1 AND class_id = $2 AND session_key = $3",
    )
    .bind(ran.me)
    .bind(class)
    .bind(ran.times.key.id())
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(answers as u32, ran.answered);
    assert_eq!(&items.0, got);
    server::ledger::audit::reconcile(pool, &[ran.me])
        .await
        .unwrap()
        .assert_clean();
}

// ---------------------------------------------------------------- end to end

#[tokio::test]
async fn a_herbology_lesson_end_to_end_gives_materials_that_stack() {
    let ran = run_class("herbology", &[(ELDERBERRIES, 3)]).await;
    assert_eq!(ran.answered, 6 + 6 + 6 + 8);
    check_grounds(&ran, "herbology", Subject::Herbology, ELDERBERRIES, 3).await;
    let rules = &shipped().class("herbology").unwrap().rewards;
    assert_eq!(rules.section_bonus(100), 0, "no group bonus");
    // Elderberries, Rose Hips, and one of Glowing Mushroom, Lacewing Flies
    // or Moonknot.
    let ids: Vec<u16> = ran.rewards.items.iter().map(|i| i.item_def).collect();
    assert_eq!(&ids[..2], &[ELDERBERRIES, 3019]);
    assert!([3026, 3022, 3020].contains(&ids[2]), "{ids:?}");
    assert!(ran.rewards.spells.is_empty() && ran.rewards.recipes.is_empty());
    assert_eq!((ran.rewards.lore, ran.rewards.fortune), (None, None));
}

#[tokio::test]
async fn a_creatures_lesson_end_to_end_with_the_group_bonus() {
    let ran = run_class("creatures", &[(LYNXKIT_FUR, 1)]).await;
    assert_eq!(ran.answered, 6 + 6 + 6 + 8);
    check_grounds(&ran, "creatures", Subject::Creatures, LYNXKIT_FUR, 1).await;
    let rules = &shipped().class("creatures").unwrap().rewards;
    assert!(rules.section_bonus(ran.score_pct) > 0, "the group bonus");
    let skill: i32 = sqlx::query_scalar(
        "SELECT xp FROM skills WHERE character_id = $1 AND discipline = 'creatures'",
    )
    .bind(ran.me)
    .fetch_one(&ran.server.state.pool)
    .await
    .unwrap();
    assert_eq!(skill as u32, ran.rewards.skill_xp);
    // Lynxkit Fur, Hippogriff Feather, and one of the five rares.
    let ids: Vec<u16> = ran.rewards.items.iter().map(|i| i.item_def).collect();
    assert_eq!(&ids[..2], &[LYNXKIT_FUR, 3006]);
    assert!([3004, 3028, 3030, 3031, 3010].contains(&ids[2]), "{ids:?}");
}

// ---------------------------------------------------------------- the timetable

#[tokio::test]
async fn the_timetable_runs_a_game_day_with_every_door_opening_on_time() {
    let schedule = &shipped().schedule;
    let clock = &schedule.clock;
    let doors: Vec<&str> = schedule.classes.iter().map(|c| c.door.as_str()).collect();
    let server = Server::with_world(layout(&doors), |_| {}).await;
    // Tomorrow (game time), from midnight to midnight.
    let day = clock.game_time(Utc::now().timestamp_millis()).day + 1;
    let (dawn, dusk) = (clock.unix_ms(day, 0), clock.unix_ms(day + 1, 0));

    // Every session that starts in the day, in order, from the schedule.
    let mut sessions: Vec<(i64, &str)> = Vec::new();
    for c in &schedule.classes {
        let mut t = dawn - 1;
        loop {
            let s = schedule.next(c, t);
            if s.start_ms >= dusk {
                break;
            }
            sessions.push((s.start_ms, c.id.as_str()));
            t = s.start_ms;
        }
    }
    sessions.sort();
    assert_eq!(sessions.len(), 20, "ten classes, twice a day");
    // Three classes in each of the six slots (Flying a fourth at
    // 10:30 and 14:30), never two in one room.
    for (k, at) in schedule.slots.iter().enumerate() {
        let start = clock.unix_ms(day, at.secs());
        let here: Vec<&str> = sessions
            .iter()
            .filter(|s| s.0 == start)
            .map(|s| s.1)
            .collect();
        let busy = if here.contains(&"flying") { 4 } else { 3 };
        assert_eq!(here.len(), busy, "slot {at}: {here:?}");
        let mut rooms: Vec<&str> = schedule
            .in_slot(k as u8)
            .map(|c| c.layout.as_str())
            .collect();
        rooms.sort();
        rooms.dedup();
        assert_eq!(rooms.len(), busy, "slot {at}");
    }

    // Each door: shut a second before its slot, open as it starts.
    let mut opened = 0;
    for (start, id) in &sessions {
        let class = schedule.class(id).unwrap();
        let slot = SessionKey {
            day,
            slot: class
                .sessions
                .iter()
                .position(|t| clock.unix_ms(day, t.secs()) == *start)
                .expect("one of its slots") as u8,
        };
        assert_eq!(schedule.session(class, slot).start_ms, *start);
        let (_, mut c) = server.player().await;
        server.set_clock(start - 1_000);
        assert_eq!(
            c.knock(&class.door).await,
            Err(ClassErrorCode::NotOpen),
            "{id} before {start}"
        );
        // Admission takes real time between knocks (the rate limit).
        tokio::time::sleep(Duration::from_millis(1_100)).await;
        server.set_clock(start + 500);
        let zone = c.knock(&class.door).await;
        assert!(
            matches!(&zone, Ok(Zone::Class { class: k, layout, .. }) if k == id && *layout == class.layout),
            "{id} at {start}: {zone:?}"
        );
        opened += 1;
    }
    assert_eq!(opened, 20);
    // The schedule route agrees: each slot's three or four classes, in file order.
    let view = classes::schedule_at(dawn);
    assert_eq!(view.slots.len(), 6);
    assert!(
        view.slots
            .iter()
            .all(|s| (3..=4).contains(&s.classes.len()))
    );
}

// ---------------------------------------------------------------- the retry

#[tokio::test]
async fn a_retried_reward_reports_the_spells_taught_and_the_cap_flag() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let p = app.player("Roarwing").await;
    // Level 3 (Defence teaches Titillara), with only 10 XP of the day's
    // class cap left.
    let level = 3u8;
    let xp = shipped().progression.level_start(level);
    sqlx::query("UPDATE characters SET xp = $2, level = $3 WHERE id = $1")
        .bind(p.id)
        .bind(xp as i32)
        .bind(i16::from(level))
        .execute(pool)
        .await
        .unwrap();
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
    .bind(cap as i32 - 10)
    .execute(pool)
    .await
    .unwrap();
    let schedule = &shipped().schedule;
    let session = schedule.next(schedule.class("defence").unwrap(), 4_400_000_000_000);
    let req = AdmitRequest {
        class_id: "defence".into(),
        session,
        section: 0,
        character_id: p.id,
    };
    let Admission::Admitted { attendance_id } = classes::admit(pool, &req).await.unwrap() else {
        panic!("admitted");
    };
    let reward = RewardRequest {
        attendance_id,
        character_id: p.id,
        class_id: "defence".into(),
        score_pct: 80,
        answers: 10,
        section_pct: 80,
    };
    let RewardOutcome::Granted(first) = classes::reward(pool, &reward, now).await.unwrap() else {
        panic!("granted");
    };
    assert_eq!(first.rewards.spells, vec![TITILLARA]);
    assert!(first.rewards.xp_capped);
    assert_eq!(first.rewards.xp, 10, "what was left of the cap");
    // Kept on the attendance row...
    let (spells, capped): (Vec<i32>, bool) =
        sqlx::query_as("SELECT spells, xp_capped FROM class_attendance WHERE id = $1")
            .bind(attendance_id)
            .fetch_one(pool)
            .await
            .unwrap();
    assert_eq!((spells, capped), (vec![i32::from(TITILLARA)], true));
    // ...so a retry (a lost reply, a restart) reports them, granting nothing.
    for _ in 0..2 {
        let RewardOutcome::AlreadyRewarded(again) =
            classes::reward(pool, &reward, Utc::now()).await.unwrap()
        else {
            panic!("a retry");
        };
        assert_eq!(again, first.rewards);
    }
    let known: i64 =
        sqlx::query_scalar("SELECT count(*) FROM known_spells WHERE character_id = $1")
            .bind(p.id)
            .fetch_one(pool)
            .await
            .unwrap();
    assert_eq!(known, 1);
    // An uncapped class with no spell records both as such.
    let q = app.player("Skytalon").await;
    let session = schedule.next(schedule.class("history").unwrap(), 4_400_000_000_000);
    let req = AdmitRequest {
        class_id: "history".into(),
        session,
        section: 0,
        character_id: q.id,
    };
    let Admission::Admitted { attendance_id } = classes::admit(pool, &req).await.unwrap() else {
        panic!("admitted");
    };
    let reward = RewardRequest {
        attendance_id,
        character_id: q.id,
        class_id: "history".into(),
        ..reward
    };
    let RewardOutcome::Granted(first) = classes::reward(pool, &reward, now).await.unwrap() else {
        panic!("granted");
    };
    assert!(first.rewards.spells.is_empty() && !first.rewards.xp_capped);
    let RewardOutcome::AlreadyRewarded(again) = classes::reward(pool, &reward, now).await.unwrap()
    else {
        panic!("a retry");
    };
    assert_eq!(again, first.rewards);
    server::ledger::audit::reconcile(pool, &[q.id])
        .await
        .unwrap()
        .assert_clean();
}

// ---------------------------------------------------------------- night brews

/// The real moment the clock's `period` next starts after game day `day`'s
/// midnight.
fn period_start(day: i64, period: Period) -> i64 {
    let clock = &shipped().schedule.clock;
    let p = clock.periods.iter().find(|p| p.period == period).unwrap();
    clock.unix_ms(day, p.starts.secs())
}

#[test]
fn night_brews_are_judged_at_the_dawn_and_dusk_edges() {
    let catalog = &shipped().catalog;
    let clock = &shipped().schedule.clock;
    let moonlit = catalog.get(MOONLIT).unwrap();
    let beanie = catalog.get(106).unwrap();
    // The three night recipes' outputs carry the tag.
    for recipe in 331..=333 {
        let r = shipped().recipes.get(recipe).unwrap();
        assert!(
            catalog.get(r.output.item).unwrap().has_tag("night"),
            "{recipe}"
        );
    }
    let day = 400;
    let dusk = period_start(day, Period::Night);
    let dawn = period_start(day + 1, Period::Morning);
    assert_eq!(clock.period(dusk), Period::Night);
    assert_ne!(clock.period(dusk - 1), Period::Night);
    let refused = server::crafting::night_only(moonlit, dusk - 1).unwrap_err();
    assert_eq!(refused.status, StatusCode::FORBIDDEN);
    assert_eq!(refused.code, "night_only");
    assert!(server::crafting::night_only(moonlit, dusk).is_ok());
    assert!(server::crafting::night_only(moonlit, dawn - 1).is_ok());
    assert!(server::crafting::night_only(moonlit, dawn).is_err());
    // Midnight is night; midday is not.
    assert!(server::crafting::night_only(moonlit, clock.unix_ms(day + 1, 0)).is_ok());
    assert!(server::crafting::night_only(moonlit, clock.unix_ms(day, 12 * 3_600)).is_err());
    // Anything else crafts at any hour.
    assert!(server::crafting::night_only(beanie, dusk - 1).is_ok());
    assert!(server::crafting::night_only(beanie, dusk).is_ok());
}

#[tokio::test]
async fn night_brews_wait_for_night_on_the_game_clock() {
    let server = Server::with_world(layout(&[]), |c| c.stations_anywhere = true).await;
    let app = TestApp::from_state(server.state.clone());
    let pool = &server.state.pool;
    let p = app.player("Skytalon").await;
    // Potions Skill 2 and the goods, through the dev grant; the recipe known.
    let skill2 = shipped().recipes.skill_levels[0];
    let res = app
        .post(
            "/api/dev/grant",
            json!({
                "skills": { "potions": skill2 },
                "items": [
                    { "item_def": MOONFLOWER, "quantity": 6 },
                    { "item_def": MOONSTONE, "quantity": 3 },
                    { "item_def": WOOL, "quantity": 3 }
                ]
            }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    sqlx::query("INSERT INTO known_recipes (character_id, recipe_id) VALUES ($1, $2)")
        .bind(p.id)
        .bind(i32::from(MOONLIT_RECIPE))
        .execute(pool)
        .await
        .unwrap();
    let rested = || async {
        sqlx::query("UPDATE characters SET craft_ready_at = NULL WHERE id = $1")
            .bind(p.id)
            .execute(pool)
            .await
            .unwrap();
    };
    let brew = || {
        app.post(
            "/api/craft",
            json!({ "recipe_id": MOONLIT_RECIPE }),
            p.token(),
        )
    };
    let day = shipped()
        .schedule
        .clock
        .game_time(Utc::now().timestamp_millis())
        .day
        + 1;
    let dusk = period_start(day, Period::Night);
    let dawn = period_start(day + 1, Period::Morning);

    // A second before dusk: refused, and nothing is used up.
    server.set_clock(dusk - 1_000);
    let res = brew().await;
    assert_eq!(res.status, StatusCode::FORBIDDEN, "{:?}", res.body);
    assert_eq!(res.body["error"], "night_only");
    assert_eq!(res.body["message"], "That brew can only be made at night.");
    assert_eq!(holding(pool, p.id, MOONFLOWER).await.1, 6);
    assert_eq!(holding(pool, p.id, MOONSTONE).await.1, 3);
    assert_eq!(holding(pool, p.id, MOONLIT).await.1, 0);
    // A day recipe still crafts then.
    let res = app
        .post(
            "/api/craft",
            json!({ "recipe_id": BEANIE_RECIPE }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    rested().await;
    // At dusk it brews.
    server.set_clock(dusk + 500);
    let res = brew().await;
    assert_eq!(res.status, StatusCode::CREATED, "{:?}", res.body);
    assert_eq!(holding(pool, p.id, MOONLIT).await.1, 2);
    rested().await;
    // Just before dawn, still night.
    server.set_clock(dawn - 1_000);
    assert_eq!(brew().await.status, StatusCode::CREATED);
    rested().await;
    // At dawn: refused again.
    server.set_clock(dawn + 500);
    let res = brew().await;
    assert_eq!(res.status, StatusCode::FORBIDDEN);
    assert_eq!(res.body["error"], "night_only");
    assert_eq!(holding(pool, p.id, MOONLIT).await, (1, 4));
    assert_eq!(holding(pool, p.id, MOONFLOWER).await.1, 2);
    server::ledger::audit::reconcile(pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}

// ---------------------------------------------------------------- the dev grant

#[tokio::test]
async fn the_dev_grant_raises_any_of_the_nine_subjects() {
    let app = TestApp::dev().await;
    let p = app.player("Burrowsby").await;
    let res = app
        .post(
            "/api/dev/grant",
            json!({ "skills": { "astronomy": 300, "herbology": 120, "charms": 50 } }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let skills = app.get("/api/skills", p.token()).await;
    let xp_of = |id: &str| {
        skills.body["skills"]
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["discipline"] == id)
            .map(|s| s["xp"].as_u64().unwrap())
            .unwrap()
    };
    assert_eq!(xp_of("astronomy"), 300);
    assert_eq!(xp_of("herbology"), 120);
    assert_eq!(xp_of("charms"), 50);
    // Every subject id is accepted; anything else is refused whole.
    for s in Subject::ALL {
        let res = app
            .post(
                "/api/dev/grant",
                json!({ "skills": { s.id(): 1 } }),
                p.token(),
            )
            .await;
        assert_eq!(res.status, StatusCode::OK, "{}: {:?}", s.id(), res.body);
    }
    let res = app
        .post(
            "/api/dev/grant",
            json!({ "skills": { "astronomy": 5, "flying_pitch": 5 } }),
            p.token(),
        )
        .await;
    assert_eq!(res.status, StatusCode::BAD_REQUEST);
    assert_eq!(res.body["error"], "invalid_grant");
    assert_eq!(res.body["message"], "Unknown subject.");
    assert_eq!(
        app.get("/api/skills", p.token()).await.body["skills"]
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["discipline"] == "astronomy")
            .unwrap()["xp"],
        301
    );
}
