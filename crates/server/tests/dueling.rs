//! Dueling over the database: the record and its rewards (XP for both by
//! the result, house points for a win, once per duel), the limits that keep
//! duels from being farmed (the fight's minimum length, rewarded duels per
//! opponent, the daily duel XP cap, house-point wins per day), the duel
//! history route, and a whole duel through the world (the Dueling Club's
//! lesson, the challenge, the fight, the record) with its jobs run against
//! Postgres; a duel still running when the server stops is
//! recorded once as no contest. Nothing a duel does may touch an item or a
//! Crown.

mod common;

use std::sync::Arc;
use std::time::Instant;

use axum::http::StatusCode;
use bytes::Bytes;
use chrono::{DateTime, TimeZone, Utc};
use common::{TestApp, TestPlayer};
use protocol::{
    DuelEndReason, DuelEvent, DuelRewards, DuelSide, House, ServerMsg, XpSource, decode_server,
};
use server::catalog::shipped;
use server::characters::{Character, SavedZone};
use server::dueling::{self, Finished};
use server::metrics::Registry;
use server::realtime::ConnControl;
use server::realtime::chat::WordFilter;
use server::realtime::ext::DuelMsg;
use server::realtime::metrics::RealtimeMetrics;
use server::realtime::world::{Input, Joining, TICK, WorldSim};
use sqlx::PgPool;
use tokio::sync::mpsc;

const TORPEO: u16 = 11;

fn day(d: u32) -> DateTime<Utc> {
    Utc.with_ymd_and_hms(2026, 9, d, 12, 0, 0).unwrap()
}

fn key() -> i64 {
    (rand::random::<i64>() & i64::MAX).max(1)
}

/// A duel between `a` and `b` that fought for `fought_ms`, ending on `at`.
fn finished(
    a: &TestPlayer,
    b: &TestPlayer,
    winner: Option<DuelSide>,
    fought_ms: u32,
    at: DateTime<Utc>,
) -> Finished {
    Finished {
        duel_key: key(),
        venue: "dueling_club".into(),
        characters: [a.id, b.id],
        winner,
        reason: DuelEndReason::Poise,
        poise: [40, 0],
        fought_ms,
        started_at: at - chrono::Duration::seconds(30),
        ended_at: at,
    }
}

fn rewards(xp: u32, capped: bool, house_points: u16) -> DuelRewards {
    DuelRewards {
        xp,
        capped,
        house_points,
    }
}

async fn xp_of(pool: &PgPool, id: i64) -> i32 {
    sqlx::query_scalar("SELECT xp FROM characters WHERE id = $1")
        .bind(id)
        .fetch_one(pool)
        .await
        .unwrap()
}

/// Everything about a character's goods: item rows, both ledgers, the purse.
async fn goods(pool: &PgPool, id: i64) -> (Vec<(i64, i32, i32, String)>, i64, i64, i64) {
    let items = sqlx::query_as(
        "SELECT id, item_def, quantity, location FROM items WHERE owner_id = $1 ORDER BY id",
    )
    .bind(id)
    .fetch_all(pool)
    .await
    .unwrap();
    let item_moves: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM item_ledger WHERE from_owner = $1 OR to_owner = $1",
    )
    .bind(id)
    .fetch_one(pool)
    .await
    .unwrap();
    let crown_moves: i64 =
        sqlx::query_scalar("SELECT count(*) FROM crown_ledger WHERE character_id = $1")
            .bind(id)
            .fetch_one(pool)
            .await
            .unwrap();
    let purse: i64 = sqlx::query_scalar("SELECT crowns FROM characters WHERE id = $1")
        .bind(id)
        .fetch_one(pool)
        .await
        .unwrap();
    (items, item_moves, crown_moves, purse)
}

#[tokio::test]
async fn a_duel_is_recorded_once_with_rewards_by_result() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let a = app.player("Roarwing").await;
    let b = app.player("Serpentyne").await;
    let xp = &shipped().progression.xp;
    let points = shipped().duel_rules.rewards.win_house_points;
    let before = (goods(pool, a.id).await, goods(pool, b.id).await);

    let f = finished(&a, &b, Some(DuelSide::A), 20_000, day(1));
    let r = dueling::record(pool, &f).await.unwrap();
    assert!(!r.duplicate);
    assert_eq!(
        r.rewards,
        [
            rewards(xp.duel_win, false, points),
            rewards(xp.duel_loss, false, 0)
        ]
    );
    let ga = r.grants[0].unwrap();
    assert_eq!((ga.source, ga.granted), (XpSource::DuelWin, xp.duel_win));
    assert_eq!(xp_of(pool, a.id).await, xp.duel_win as i32);
    assert_eq!(xp_of(pool, b.id).await, xp.duel_loss as i32);
    let row: (String, String, i32, i32, i32, i16, i16) = sqlx::query_as(
        "SELECT result, reason, a_xp, b_xp, house_points, a_poise, b_poise FROM duels WHERE id = $1",
    )
    .bind(r.id)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(
        row,
        (
            "a".into(),
            "poise".into(),
            xp.duel_win as i32,
            xp.duel_loss as i32,
            i32::from(points),
            40,
            0
        )
    );
    // The ledgers name the duel.
    let ledger: Vec<(i64, String, Option<i64>)> = sqlx::query_as(
        "SELECT character_id, source, ref_id FROM xp_ledger WHERE ref_id = $1 AND source LIKE 'duel%'
         AND character_id = ANY($2) ORDER BY character_id",
    )
    .bind(r.id)
    .bind(&[a.id, b.id][..])
    .fetch_all(pool)
    .await
    .unwrap();
    let mut want = vec![
        (a.id, "duel_win".to_owned(), Some(r.id)),
        (b.id, "duel_loss".to_owned(), Some(r.id)),
    ];
    want.sort();
    assert_eq!(ledger, want);
    let house: Vec<(String, i32, Option<i64>)> = sqlx::query_as(
        "SELECT house, points, character_id FROM house_points WHERE source = 'duel' AND ref_id = $1",
    )
    .bind(r.id)
    .fetch_all(pool)
    .await
    .unwrap();
    assert_eq!(
        house,
        vec![("roarwing".into(), i32::from(points), Some(a.id))]
    );

    // Recording it again (a retry) grants nothing and answers the first.
    let again = dueling::record(pool, &f).await.unwrap();
    assert!(again.duplicate);
    assert_eq!((again.id, again.rewards), (r.id, r.rewards));
    assert_eq!(again.grants, [None, None]);
    assert_eq!(xp_of(pool, a.id).await, xp.duel_win as i32);

    // A draw: the draw XP each, no house points.
    let d = dueling::record(pool, &finished(&a, &b, None, 90_000, day(1)))
        .await
        .unwrap();
    assert_eq!(
        d.rewards,
        [
            rewards(xp.duel_draw, false, 0),
            rewards(xp.duel_draw, false, 0)
        ]
    );
    // A quick forfeit gives nothing (but is still a duel on record).
    let mut quick = finished(&a, &b, Some(DuelSide::B), 4_000, day(1));
    quick.reason = DuelEndReason::Forfeit;
    let q = dueling::record(pool, &quick).await.unwrap();
    assert_eq!(q.rewards, [rewards(0, false, 0), rewards(0, false, 0)]);
    assert_eq!(q.grants, [None, None]);
    let reason: String = sqlx::query_scalar("SELECT reason FROM duels WHERE id = $1")
        .bind(q.id)
        .fetch_one(pool)
        .await
        .unwrap();
    assert_eq!(reason, "forfeit");

    // Nothing a duel does touches goods.
    assert_eq!(before, (goods(pool, a.id).await, goods(pool, b.id).await));
}

#[tokio::test]
async fn duels_against_one_opponent_stop_paying_for_the_day() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let a = app.player("Burrowsby").await;
    let b = app.player("Skytalon").await;
    let c = app.player("Skytalon").await;
    let max = shipped().duel_rules.rewards.pair_daily_max;
    for _ in 0..max {
        let r = dueling::record(pool, &finished(&a, &b, Some(DuelSide::A), 20_000, day(2)))
            .await
            .unwrap();
        assert!(r.rewards[0].xp > 0 && !r.rewards[0].capped);
    }
    // One more against the same opponent: nothing for either, "capped".
    let r = dueling::record(pool, &finished(&a, &b, Some(DuelSide::A), 20_000, day(2)))
        .await
        .unwrap();
    assert_eq!(r.rewards, [rewards(0, true, 0), rewards(0, true, 0)]);
    // Either way round: the pair is the pair.
    let r = dueling::record(pool, &finished(&b, &a, Some(DuelSide::A), 20_000, day(2)))
        .await
        .unwrap();
    assert_eq!(r.rewards, [rewards(0, true, 0), rewards(0, true, 0)]);
    // Another opponent still pays; so does the same one the next day.
    let r = dueling::record(pool, &finished(&a, &c, Some(DuelSide::A), 20_000, day(2)))
        .await
        .unwrap();
    assert!(r.rewards[0].xp > 0);
    let r = dueling::record(pool, &finished(&a, &b, Some(DuelSide::A), 20_000, day(3)))
        .await
        .unwrap();
    assert!(r.rewards[0].xp > 0 && r.rewards[1].xp > 0);
}

#[tokio::test]
async fn the_daily_duel_cap_and_house_point_wins_hold() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let w = app.player("Serpentyne").await;
    let rules = &shipped().duel_rules.rewards;
    let xp = &shipped().progression.xp;
    let (_, cap) = shipped().progression.daily_cap(XpSource::DuelWin).unwrap();
    let wins = (cap / xp.duel_win + 2) as usize;
    let mut total = 0;
    let mut points = 0;
    let mut capped = 0;
    let mut everyone = vec![w.id];
    for _ in 0..wins.max(usize::from(rules.house_point_wins_daily) + 1) {
        let other = app.player("Roarwing").await;
        let r = dueling::record(
            pool,
            &finished(&w, &other, Some(DuelSide::A), 20_000, day(4)),
        )
        .await
        .unwrap();
        everyone.push(other.id);
        total += r.rewards[0].xp;
        points += u32::from(r.rewards[0].house_points);
        capped += usize::from(r.rewards[0].capped);
        // The loser's own cap is untouched by the winner's.
        assert_eq!(r.rewards[1].xp, xp.duel_loss);
    }
    assert_eq!(total, cap, "XP stops at the daily cap");
    assert!(capped >= 2, "the capped wins say so");
    assert_eq!(
        points,
        u32::from(rules.house_point_wins_daily) * u32::from(rules.win_house_points),
        "house points for so many wins a day"
    );
    assert_eq!(xp_of(pool, w.id).await, cap as i32);
    // A new UTC day: both start again.
    let other = app.player("Roarwing").await;
    let r = dueling::record(
        pool,
        &finished(&w, &other, Some(DuelSide::A), 20_000, day(5)),
    )
    .await
    .unwrap();
    assert_eq!(
        r.rewards[0],
        rewards(xp.duel_win, false, rules.win_house_points)
    );
    // The audit reconciles every duel's XP and house points, capped or not.
    everyone.push(other.id);
    server::ledger::audit::reconcile(pool, &everyone)
        .await
        .unwrap()
        .assert_clean();
}

#[tokio::test]
async fn the_duel_history_shows_each_side_its_own_result() {
    let app = TestApp::new().await;
    let pool = &app.state.pool;
    let a = app.player("Roarwing").await;
    let b = app.player("Burrowsby").await;
    let won = dueling::record(pool, &finished(&a, &b, Some(DuelSide::A), 30_000, day(6)))
        .await
        .unwrap();
    let mut timeout = finished(&a, &b, None, 90_000, day(6));
    timeout.reason = DuelEndReason::Timeout;
    let drawn = dueling::record(pool, &timeout).await.unwrap();

    let res = app.get("/api/duels", a.token()).await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let items = res.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 2);
    assert_eq!(items[0]["id"], drawn.id);
    assert_eq!(items[0]["result"], "draw");
    assert_eq!(items[0]["reason"], "timeout");
    assert_eq!(items[1]["result"], "win");
    assert_eq!(items[1]["opponent"]["character_id"], b.id);
    assert_eq!(items[1]["opponent"]["name"], b.name.as_str());
    assert_eq!(items[1]["xp"], won.rewards[0].xp);
    assert_eq!(items[1]["house_points"], won.rewards[0].house_points);
    assert_eq!(items[1]["fought_ms"], 30_000);
    assert!(res.body["next"].is_null());

    // B sees the loss, a page at a time.
    let res = app.get("/api/duels?limit=1", b.token()).await;
    let items = res.body["items"].as_array().unwrap();
    assert_eq!(items.len(), 1);
    let next = res.body["next"].as_str().unwrap().to_owned();
    let res = app
        .get(&format!("/api/duels?limit=1&after={next}"), b.token())
        .await;
    let items = res.body["items"].as_array().unwrap();
    assert_eq!(items[0]["result"], "loss");
    assert_eq!(items[0]["house_points"], 0);
    assert_eq!(items[0]["opponent"]["name"], a.name.as_str());
    assert!(res.body["next"].is_null());

    assert_eq!(
        app.get("/api/duels?after=nonsense", a.token()).await.status,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(
        app.get("/api/duels", None).await.status,
        StatusCode::UNAUTHORIZED
    );
}

// ------------------------------------------------------------ through the world

struct Seat {
    id: protocol::EntityId,
    rx: mpsc::Receiver<Bytes>,
}

impl Seat {
    fn drain(&mut self) -> Vec<ServerMsg> {
        let mut v = Vec::new();
        while let Ok(b) = self.rx.try_recv() {
            v.push(decode_server(&b).unwrap());
        }
        v
    }
}

/// Joins a database character at `pos` in the castle with what the
/// gateway would load for it.
async fn seat(s: &mut WorldSim, pool: &PgPool, p: &TestPlayer, pos: [f32; 3]) -> Seat {
    let character = Character {
        id: p.id,
        name: p.name.clone(),
        house: House::Skytalon,
        zone: SavedZone::Castle,
        last_pos: Some(pos),
        appearance_version: 0,
        appearance: protocol::Appearance::default(),
    };
    let school = server::progression::load_school(pool, p.id).await.unwrap();
    let joining = Joining {
        school,
        ..Joining::alone(character)
    };
    let (tx, rx) = mpsc::channel(4096);
    let id = s
        .join(
            joining,
            tx,
            Arc::new(ConnControl::default()),
            Instant::now(),
        )
        .unwrap();
    Seat { id, rx }
}

/// Runs the feature jobs the world queued against the database and hands
/// their results back.
async fn run_jobs(s: &mut WorldSim, pool: &PgPool, at: Instant) -> usize {
    let jobs = s.take_ext_jobs();
    let n = jobs.len();
    for job in jobs {
        let done = job(pool.clone()).await;
        s.ext_done(done, at);
    }
    n
}

#[tokio::test]
async fn a_whole_duel_through_the_world() {
    let app = TestApp::new().await;
    let pool = app.state.pool.clone();
    let a = app.player("Skytalon").await;
    let b = app.player("Skytalon").await;
    let before = (goods(&pool, a.id).await, goods(&pool, b.id).await);
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../data/world/layout.json");
    let castle = sim_core::World::from_json(&std::fs::read_to_string(path).unwrap()).unwrap();
    // Both stand on the Great Hall's floor by the Dueling Club's stage, just
    // inside the club's area (read from the layout, so they follow the hall).
    let club = castle
        .areas()
        .iter()
        .find(|a| a.id == "dueling_club")
        .unwrap();
    let spot = |dx: f32| [club.min[0] + dx, 0.0, club.min[2] + 1.0];
    let (spot_a, spot_b) = (spot(6.0), spot(8.0));
    for p in [spot_a, spot_b] {
        assert!(castle.ground_at(p[0], p[2], p[1]).is_some(), "{p:?}");
    }
    let mut s = WorldSim::new(
        Arc::new(castle),
        WordFilter::from_list(""),
        None,
        Arc::new(RealtimeMetrics::register(&Registry::default())),
        1,
    );
    let mut sa = seat(&mut s, &pool, &a, spot_a).await;
    let mut sb = seat(&mut s, &pool, &b, spot_b).await;
    let mut at = Instant::now();

    // New to the club: it teaches them (a job each), then they may duel.
    s.input(sa.id, at, Input::Duel(DuelMsg::Challenge { target: sb.id }));
    assert!(sa.drain().contains(&ServerMsg::DuelError {
        challenge_id: None,
        code: protocol::DuelErrorCode::NoSpells,
    }));
    at += TICK;
    s.tick(at);
    assert_eq!(run_jobs(&mut s, &pool, at).await, 2, "a lesson each");
    let book = sa.drain().into_iter().find_map(|m| match m {
        ServerMsg::Spellbook { learned, new, .. } => Some((learned, new)),
        _ => None,
    });
    let level1: Vec<u16> = shipped()
        .spells
        .taught_at("dueling_club", 1)
        .map(|x| x.id)
        .collect();
    assert_eq!(book, Some((level1.clone(), level1.clone())));
    let known: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM known_spells WHERE character_id = $1 AND taught_at = 'dueling_club'",
    )
    .bind(a.id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(known, level1.len() as i64);

    // The challenge and the duel: A casts Torpeo whenever it can.
    s.input(sa.id, at, Input::Duel(DuelMsg::Challenge { target: sb.id }));
    at += TICK;
    s.tick(at);
    let challenge_id = sb
        .drain()
        .into_iter()
        .find_map(|m| match m {
            ServerMsg::DuelChallenged { challenge_id, .. } => Some(challenge_id),
            _ => None,
        })
        .expect("challenged");
    s.input(
        sb.id,
        at,
        Input::Duel(DuelMsg::Respond {
            challenge_id,
            accept: true,
        }),
    );
    let mut over = None;
    for _ in 0..2_000 {
        s.input(sa.id, at, Input::Duel(DuelMsg::Cast { spell: TORPEO }));
        at += TICK;
        s.tick(at);
        sb.drain();
        for m in sa.drain() {
            if let ServerMsg::DuelEvents { events, .. } = m {
                over = over.or(events.iter().find_map(|e| match e {
                    DuelEvent::Ended { winner, reason } => Some((*winner, *reason)),
                    _ => None,
                }));
            }
        }
        if over.is_some() {
            break;
        }
    }
    assert_eq!(over, Some((Some(DuelSide::A), DuelEndReason::Poise)));

    // The record job: the result, the rewards and the XP.
    assert_eq!(run_jobs(&mut s, &pool, at).await, 1);
    let xp = &shipped().progression.xp;
    let points = shipped().duel_rules.rewards.win_house_points;
    let result = |msgs: Vec<ServerMsg>| {
        msgs.into_iter().find_map(|m| match m {
            ServerMsg::DuelResult { rewards, .. } => rewards,
            _ => None,
        })
    };
    assert_eq!(
        result(sa.drain()),
        Some(rewards(xp.duel_win, false, points))
    );
    assert_eq!(result(sb.drain()), Some(rewards(xp.duel_loss, false, 0)));
    let res = app.get("/api/duels", a.token()).await;
    assert_eq!(res.body["items"][0]["result"], "win");
    assert_eq!(res.body["items"][0]["reason"], "poise");
    assert!(res.body["items"][0]["fought_ms"].as_i64().unwrap() >= 15_000);
    assert_eq!(xp_of(&pool, a.id).await, xp.duel_win as i32);

    // Everyone goes back to the club; no item or Crown moved.
    for _ in 0..(shipped().duel_rules.return_delay_ms / 50 + 2) {
        at += TICK;
        s.tick(at);
    }
    assert_eq!(s.position(sa.id), Some(spot_a));
    assert_eq!(before, (goods(&pool, a.id).await, goods(&pool, b.id).await));
}

/// A duelist who drops out mid-duel stays "in play" for its disconnect
/// grace, so deleting the character is refused until the duel is decided.
#[tokio::test]
async fn a_duelist_away_within_the_grace_is_still_in_play() {
    let app = TestApp::new().await;
    let pool = app.state.pool.clone();
    let a = app.player("Skytalon").await;
    let b = app.player("Skytalon").await;
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../data/world/layout.json");
    let castle = sim_core::World::from_json(&std::fs::read_to_string(path).unwrap()).unwrap();
    let club = castle
        .areas()
        .iter()
        .find(|a| a.id == "dueling_club")
        .unwrap();
    let spot = |dx: f32| [club.min[0] + dx, 0.0, club.min[2] + 1.0];
    let (spot_a, spot_b) = (spot(6.0), spot(8.0));
    let mut s = WorldSim::new(
        Arc::new(castle),
        WordFilter::from_list(""),
        None,
        Arc::new(RealtimeMetrics::register(&Registry::default())),
        1,
    );
    let mut sa = seat(&mut s, &pool, &a, spot_a).await;
    let mut sb = seat(&mut s, &pool, &b, spot_b).await;
    let mut at = Instant::now();
    // The club's first lesson, then the challenge, accepted.
    s.input(sa.id, at, Input::Duel(DuelMsg::Challenge { target: sb.id }));
    at += TICK;
    s.tick(at);
    run_jobs(&mut s, &pool, at).await;
    s.input(sa.id, at, Input::Duel(DuelMsg::Challenge { target: sb.id }));
    at += TICK;
    s.tick(at);
    let challenge_id = sb
        .drain()
        .into_iter()
        .find_map(|m| match m {
            ServerMsg::DuelChallenged { challenge_id, .. } => Some(challenge_id),
            _ => None,
        })
        .expect("challenged");
    s.input(
        sb.id,
        at,
        Input::Duel(DuelMsg::Respond {
            challenge_id,
            accept: true,
        }),
    );
    at += TICK;
    s.tick(at);
    sa.drain();

    assert!(s.in_play(b.id));
    assert!(s.remove_player(sb.id), "B's connection drops mid-duel");
    assert!(s.in_play(b.id), "away within the grace");
    assert!(s.in_play(a.id));
    // Once the grace runs out the duel is decided and B is let go.
    let grace = shipped().duel_rules.disconnect_grace_ms;
    for _ in 0..(grace / 50 + 100) {
        at += TICK;
        s.tick(at);
    }
    assert!(!s.in_play(b.id));
}

// ------------------------------------------------------------ over sockets

/// A hall whose spawn stands in the Dueling Club's area (the shipped
/// venue's area id), so every bot starts in the club.
const CLUB_LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 }
    ],
    "spawns": [{ "id": "club", "pos": [100, 0, 100], "yaw": 0 }],
    "areas": [
        { "id": "dueling_club", "kind": "dueling_club", "name": "Dueling Club",
          "min": [80, -1, 80], "max": [120, 3, 120] }
    ]
}"#;

const WAIT: std::time::Duration = std::time::Duration::from_secs(10);

struct Socket {
    ws: tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
    id: protocol::EntityId,
}

impl Socket {
    async fn open(addr: std::net::SocketAddr, token: &str) -> Socket {
        use futures_util::SinkExt;
        let (ws, _) = tokio_tungstenite::connect_async(format!("ws://{addr}/ws"))
            .await
            .unwrap();
        let mut s = Socket { ws, id: 0 };
        s.ws.send(tokio_tungstenite::tungstenite::Message::Binary(
            protocol::encode_client(&protocol::ClientMsg::Hello {
                protocol_version: protocol::PROTOCOL_VERSION,
                token: token.into(),
                character_id: None,
            })
            .into(),
        ))
        .await
        .unwrap();
        s.id = s
            .expect(|m| match m {
                ServerMsg::Welcome { your_id, .. } => Some(*your_id),
                _ => None,
            })
            .await;
        s
    }

    async fn send(&mut self, msg: protocol::ClientMsg) {
        use futures_util::SinkExt;
        self.ws
            .send(tokio_tungstenite::tungstenite::Message::Binary(
                protocol::encode_client(&msg).into(),
            ))
            .await
            .unwrap();
    }

    /// The first message `f` picks, skipping the rest.
    async fn expect<T>(&mut self, mut f: impl FnMut(&ServerMsg) -> Option<T>) -> T {
        use futures_util::StreamExt;
        loop {
            let frame = tokio::time::timeout(WAIT, self.ws.next())
                .await
                .expect("a message in time")
                .expect("socket open")
                .unwrap();
            if let tokio_tungstenite::tungstenite::Message::Binary(b) = frame
                && let Some(t) = f(&decode_server(&b).unwrap())
            {
                return t;
            }
        }
    }
}

/// A dev bot with the club's level-1 spells, and its character id.
async fn duelist(app: &TestApp, name: &str) -> (String, i64) {
    let res = app
        .post(
            "/api/dev/bot-session",
            serde_json::json!({ "name": name }),
            None,
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let token = res.body["token"].as_str().unwrap().to_owned();
    let res = app
        .post(
            "/api/dev/grant",
            serde_json::json!({ "spells": [10, 11, 12] }),
            Some(&token),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let id = app.get("/api/characters/me", Some(&token)).await.body["id"]
        .as_i64()
        .unwrap();
    (token, id)
}

#[tokio::test]
async fn a_duel_over_websockets_from_challenge_to_the_club() {
    let config = server::Config {
        database_url: std::env::var("DATABASE_URL")
            .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
        dev: true,
        ..server::Config::default()
    };
    let pool = server::db::connect_with(&config.database_url, 4)
        .await
        .unwrap();
    let mut state = server::AppState::new(pool, config, Arc::new(server::email::DevMailer));
    state.start_realtime(
        sim_core::World::from_json(CLUB_LAYOUT).unwrap(),
        WordFilter::from_list(""),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let router = server::http::router(state.clone());
    tokio::spawn(async move {
        axum::serve(
            listener,
            router.into_make_service_with_connect_info::<std::net::SocketAddr>(),
        )
        .await
        .unwrap();
    });
    let app = TestApp::from_state(state.clone());
    let n = rand::random::<u32>() % 100_000_000;
    let (ta, ca) = duelist(&app, &format!("ada{n:08}")).await;
    let (tb, cb) = duelist(&app, &format!("bea{n:08}")).await;
    let before = (goods(&state.pool, ca).await, goods(&state.pool, cb).await);
    let mut a = Socket::open(addr, &ta).await;
    let mut b = Socket::open(addr, &tb).await;
    use protocol::ClientMsg;

    a.send(ClientMsg::DuelChallenge { target: b.id }).await;
    let challenge_id = b
        .expect(|m| match m {
            ServerMsg::DuelChallenged {
                challenge_id, from, ..
            } => Some((*challenge_id, *from)),
            _ => None,
        })
        .await;
    assert_eq!(challenge_id.1, a.id);
    b.send(ClientMsg::DuelRespond {
        challenge_id: challenge_id.0,
        accept: true,
    })
    .await;
    let start = |m: &ServerMsg| match m {
        ServerMsg::DuelStart { duel_id, you, .. } => Some((*duel_id, *you)),
        _ => None,
    };
    let (duel_id, you) = a.expect(start).await;
    assert_eq!(you, protocol::DuelRole::Duelist(DuelSide::A));
    assert_eq!(b.expect(start).await.0, duel_id);

    // B gives up at once: A wins, nobody earns anything for so short a
    // fight, and both go back to the club after the delay.
    b.send(ClientMsg::DuelForfeit).await;
    let result = |m: &ServerMsg| match m {
        ServerMsg::DuelResult {
            winner,
            reason,
            rewards,
            ..
        } => Some((*winner, *reason, *rewards)),
        _ => None,
    };
    let none = Some(rewards(0, false, 0));
    assert_eq!(
        a.expect(result).await,
        (Some(DuelSide::A), DuelEndReason::Forfeit, none)
    );
    assert_eq!(
        b.expect(result).await,
        (Some(DuelSide::A), DuelEndReason::Forfeit, none)
    );
    let castle = |m: &ServerMsg| match m {
        ServerMsg::ZoneChanged {
            zone: protocol::Zone::Castle,
            ..
        } => Some(()),
        _ => None,
    };
    a.expect(castle).await;
    b.expect(castle).await;
    let res = app.get("/api/duels", Some(&tb)).await;
    assert_eq!(res.body["items"][0]["result"], "loss");
    assert_eq!(res.body["items"][0]["reason"], "forfeit");
    assert_eq!(res.body["items"][0]["opponent"]["character_id"], ca);
    assert_eq!(
        before,
        (goods(&state.pool, ca).await, goods(&state.pool, cb).await)
    );
}

/// `duel_key, result, reason, a_poise, b_poise, fought_ms, a_xp, b_xp,
/// house_points` of a `duels` row.
type DuelRow = (i64, String, String, i16, i16, i32, i32, i32, i32);

/// Every message up to the `Kick` that ends the connection, which it returns.
async fn until_kicked(s: &mut Socket) -> (Vec<ServerMsg>, protocol::KickCode) {
    use futures_util::StreamExt;
    let mut seen = Vec::new();
    loop {
        let frame = tokio::time::timeout(WAIT, s.ws.next())
            .await
            .expect("a message in time")
            .expect("socket open")
            .unwrap();
        if let tokio_tungstenite::tungstenite::Message::Binary(b) = frame {
            match decode_server(&b).unwrap() {
                ServerMsg::Kick { code, .. } => return (seen, code),
                m => seen.push(m),
            }
        }
    }
}

/// A duel still being fought when the server stops is recorded
/// once as no contest (a draw for the reason `shutdown`, the poise as it
/// stood, no XP, no house points), nobody is sent a result, and recording
/// it again (a second shutdown, a retry) writes nothing more.
#[tokio::test]
async fn a_duel_running_at_shutdown_is_recorded_once_as_no_contest() {
    let config = server::Config {
        database_url: std::env::var("DATABASE_URL")
            .unwrap_or_else(|_| "postgres://localhost/shs_test".into()),
        dev: true,
        ..server::Config::default()
    };
    let pool = server::db::connect_with(&config.database_url, 4)
        .await
        .unwrap();
    let mut state = server::AppState::new(pool, config, Arc::new(server::email::DevMailer));
    state.start_realtime(
        sim_core::World::from_json(CLUB_LAYOUT).unwrap(),
        WordFilter::from_list(""),
    );
    let realtime = state.realtime.clone().expect("realtime started");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let router = server::http::router(state.clone());
    tokio::spawn(async move {
        axum::serve(
            listener,
            router.into_make_service_with_connect_info::<std::net::SocketAddr>(),
        )
        .await
        .unwrap();
    });
    let app = TestApp::from_state(state.clone());
    let n = rand::random::<u32>() % 100_000_000;
    let (ta, ca) = duelist(&app, &format!("sda{n:08}")).await;
    let (tb, cb) = duelist(&app, &format!("sdb{n:08}")).await;
    let before = (goods(&state.pool, ca).await, goods(&state.pool, cb).await);
    let xp_before = (xp_of(&state.pool, ca).await, xp_of(&state.pool, cb).await);
    let mut a = Socket::open(addr, &ta).await;
    let mut b = Socket::open(addr, &tb).await;
    use protocol::ClientMsg;

    a.send(ClientMsg::DuelChallenge { target: b.id }).await;
    let challenge_id = b
        .expect(|m| match m {
            ServerMsg::DuelChallenged { challenge_id, .. } => Some(*challenge_id),
            _ => None,
        })
        .await;
    b.send(ClientMsg::DuelRespond {
        challenge_id,
        accept: true,
    })
    .await;
    let start = |m: &ServerMsg| match m {
        ServerMsg::DuelStart { duel_id, .. } => Some(*duel_id),
        _ => None,
    };
    a.expect(start).await;
    b.expect(start).await;

    // The server stops mid-duel: both are kicked, neither hears a result.
    realtime.shutdown().await;
    for s in [&mut a, &mut b] {
        let (seen, code) = until_kicked(s).await;
        assert_eq!(code, protocol::KickCode::ServerRestart);
        assert!(
            !seen
                .iter()
                .any(|m| matches!(m, ServerMsg::DuelResult { .. })),
            "{seen:?}"
        );
    }
    // A second stop does nothing more.
    realtime.shutdown().await;

    let rows: Vec<DuelRow> = sqlx::query_as(
        "SELECT duel_key, result, reason, a_poise, b_poise, fought_ms, a_xp, b_xp, house_points
         FROM duels WHERE a_id = $1 AND b_id = $2",
    )
    .bind(ca)
    .bind(cb)
    .fetch_all(&state.pool)
    .await
    .unwrap();
    assert_eq!(rows.len(), 1, "{rows:?}");
    let (key, result, reason, a_poise, b_poise, fought_ms, a_xp, b_xp, points) = rows[0].clone();
    assert_eq!(
        (result.as_str(), reason.as_str()),
        ("draw", dueling::REASON_SHUTDOWN)
    );
    assert!(a_poise > 0 && a_poise == b_poise, "nobody cast: {rows:?}");
    assert!(
        (0..i32::try_from(shipped().duel_rules.time_limit_ms).unwrap()).contains(&fought_ms),
        "{rows:?}"
    );
    assert_eq!((a_xp, b_xp, points), (0, 0, 0));
    assert_eq!(
        (xp_of(&state.pool, ca).await, xp_of(&state.pool, cb).await),
        xp_before
    );
    let points_rows: i64 =
        sqlx::query_scalar("SELECT count(*) FROM house_points WHERE character_id = ANY($1)")
            .bind(&[ca, cb][..])
            .fetch_one(&state.pool)
            .await
            .unwrap();
    assert_eq!(points_rows, 0);
    assert_eq!(
        before,
        (goods(&state.pool, ca).await, goods(&state.pool, cb).await)
    );

    // The history shows it as a draw for the reason `shutdown`.
    let res = app.get("/api/duels", Some(&ta)).await;
    assert_eq!(res.body["items"][0]["result"], "draw");
    assert_eq!(res.body["items"][0]["reason"], "shutdown");
    assert_eq!(res.body["items"][0]["xp"], 0);

    // Recording it again (a retry) writes nothing.
    let again = dueling::NoContest {
        duel_key: key,
        venue: "dueling_club".into(),
        characters: [ca, cb],
        poise: [1, 1],
        fought_ms: 0,
        started_at: Utc::now(),
        ended_at: Utc::now(),
    };
    assert!(
        !dueling::record_no_contest(&state.pool, &again)
            .await
            .unwrap()
    );
    assert_eq!(dueling::record_no_contests(&state.pool, &[again]).await, 0);
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM duels WHERE duel_key = $1")
        .bind(key)
        .fetch_one(&state.pool)
        .await
        .unwrap();
    assert_eq!(count, 1);
}
