//! Flying through the server's world (docs/architecture.md).
//! The world alone (no database): mounting needs the Flying
//! skill, open sky and not being busy; an honest rider predicting with
//! `sim_core::flight::fly_and_slide` is never corrected, even flown straight
//! at the castle or the ceiling; a move out of the airspace, into a no-fly
//! box, under the ground or too fast is corrected and the rider stays up;
//! doors refuse riders; a logout in the air saves the ground below (a spawn
//! over the water); Featherlight Elixir's broom speed counts; landing needs
//! the ground within reach. Then over the database and WebSockets: the
//! Flying class end to end (refused before it, mounted on joining, every
//! ring of the first round flown through and scored by the server from the
//! checked moves, the results feeding the Flying skill, landed when the
//! class ends, allowed to mount after it) and a retried reward.

mod common;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use chrono::Utc;
use common::TestApp;
use futures_util::{SinkExt, StreamExt};
use protocol::{
    Anim, BeatPrompt, ClientMsg, DormErrorCode, EffectKind, House, MountRefused, PROTOCOL_VERSION,
    ServerMsg, Zone, decode_server, encode_client,
};
use serde_json::json;
use server::catalog::shipped;
use server::characters::{Character, SavedPosition, SavedZone};
use server::classes::{self, RewardOutcome, RewardRequest};
use server::metrics::Registry;
use server::realtime::ConnControl;
use server::realtime::chat::WordFilter;
use server::realtime::metrics::RealtimeMetrics;
use server::realtime::persist::SaveBatch;
use server::realtime::world::{Input, Joining, TICK, WorldSim};
use server::{AppState, Config, db, email, http};
use sim_core::flight::{self, Airspace, FlyParams};
use sim_core::{LessonAction, Subject, World};
use tokio::net::TcpStream;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

const LAYOUT: &str = include_str!("../../../data/world/layout.json");
const CLASS_PITCH: &str = include_str!("../../../data/world/instances/class_pitch.json");

/// The client's move rate (as the browser).
const MOVE_EVERY: Duration = Duration::from_millis(100);
/// Featherlight Elixir (catalogue 3313).
const FEATHERLIGHT: u16 = 3313;

fn castle() -> Arc<World> {
    Arc::new(World::from_json(LAYOUT).expect("the shipped layout loads"))
}

fn sim(world: Arc<World>, saver: Option<mpsc::UnboundedSender<SaveBatch>>) -> WorldSim {
    WorldSim::new(
        world,
        WordFilter::from_list(""),
        saver,
        Arc::new(RealtimeMetrics::register(&Registry::default())),
        1,
    )
}

fn character(id: i64, house: House, last_pos: Option<[f32; 3]>) -> Character {
    Character {
        id,
        name: format!("Flyer{id}"),
        house,
        zone: SavedZone::Castle,
        last_pos,
        appearance_version: 0,
        appearance: protocol::Appearance::default(),
    }
}

fn gate(world: &World) -> [f32; 3] {
    world
        .interactable("pitch_gate")
        .expect("the pitch gate")
        .pos
}

fn dist(a: [f32; 3], b: [f32; 3]) -> f32 {
    (0..3).map(|k| (a[k] - b[k]).powi(2)).sum::<f32>().sqrt()
}

/// One rider (or walker) in a world of its own, with its own clock: the
/// world ticks every 50 ms, the client moves every 100 ms.
struct Rider {
    s: WorldSim,
    id: protocol::EntityId,
    rx: mpsc::Receiver<Bytes>,
    /// The house's castle, as the client predicts with it.
    world: Arc<World>,
    air: Airspace,
    pos: [f32; 3],
    at: Instant,
    seq: u32,
    tier: u8,
    broom_pct: u16,
}

impl Rider {
    fn join(
        s: WorldSim,
        id: i64,
        last_pos: Option<[f32; 3]>,
        flying_xp: u32,
        featherlight: bool,
    ) -> Rider {
        Self::join_in(s, castle(), id, last_pos, flying_xp, featherlight)
    }

    fn join_in(
        mut s: WorldSim,
        castle: Arc<World>,
        id: i64,
        last_pos: Option<[f32; 3]>,
        flying_xp: u32,
        featherlight: bool,
    ) -> Rider {
        let house = House::Skytalon;
        let now = Instant::now();
        let effects = if featherlight {
            vec![server::potions::effect_for(FEATHERLIGHT, [0, 0, 0], None, now).unwrap()]
        } else {
            Vec::new()
        };
        let (tx, rx) = mpsc::channel(1 << 16);
        let joining = Joining {
            flying_xp,
            effects,
            ..Joining::alone(character(id, house, last_pos))
        };
        let eid = s
            .join(joining, tx, Arc::new(ConnControl::default()), now)
            .expect("joined");
        let pos = s.position(eid).unwrap();
        let mut r = Rider {
            s,
            id: eid,
            rx,
            world: Arc::new(castle.for_house(house).unwrap()),
            air: Airspace::new(&castle),
            pos,
            at: now,
            seq: 0,
            tier: 0,
            broom_pct: 100,
        };
        for m in r.drain() {
            if let ServerMsg::OwnEffects { broom_pct, .. } = m {
                r.broom_pct = broom_pct;
            }
        }
        r
    }

    fn drain(&mut self) -> Vec<ServerMsg> {
        let mut v = Vec::new();
        while let Ok(b) = self.rx.try_recv() {
            if let Ok(m) = decode_server(&b) {
                v.push(m);
            }
        }
        v
    }

    fn params(&self) -> FlyParams {
        shipped().flight.params(self.tier, self.broom_pct)
    }

    /// Lets `d` pass on both clocks.
    fn wait(&mut self, d: Duration) {
        self.at += d;
        self.s.tick(self.at);
    }

    fn input(&mut self, input: Input) -> Vec<ServerMsg> {
        self.at += Duration::from_millis(10);
        self.s.input(self.id, self.at, input);
        self.s.tick(self.at);
        self.drain()
    }

    /// Asks to mount (`on`) or land: the `Mounted` answer. The client
    /// follows it as the browser does (lifted by `flight::mount`, stood by
    /// `flight::dismount`).
    fn mount(&mut self, on: bool) -> (bool, u8, Option<MountRefused>) {
        let msgs = self.input(Input::Mount(on));
        let answer = msgs
            .iter()
            .find_map(|m| match m {
                ServerMsg::Mounted { on, tier, reason } => Some((*on, *tier, *reason)),
                _ => None,
            })
            .unwrap_or_else(|| panic!("no Mounted in {msgs:?}"));
        if answer.2.is_none() {
            if answer.0 && self.tier == 0 {
                self.tier = answer.1;
                self.pos = flight::mount(&self.world, &self.air, &self.params(), self.pos).unwrap();
            } else if !answer.0 && self.tier != 0 {
                self.pos = flight::dismount(&self.world, &shipped().flight, self.pos).unwrap();
                self.tier = 0;
            }
            assert_server_at(&self.s, self.id, self.pos);
        }
        // Let the mount change's grace pass before testing corrections.
        self.wait(Duration::from_millis(600));
        answer
    }

    /// Sends one move to `pos` (anim `Fly`): the corrections it got.
    fn send(&mut self, pos: [f32; 3]) -> Vec<[f32; 3]> {
        self.at += MOVE_EVERY;
        self.seq += 1;
        self.s.tick(self.at - TICK);
        self.s.input(
            self.id,
            self.at,
            Input::Move {
                seq: self.seq,
                pos,
                yaw: 0.0,
                anim: Anim::Fly,
            },
        );
        self.s.tick(self.at);
        self.drain()
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::Correction { pos, .. } => Some(pos.to_m()),
                _ => None,
            })
            .collect()
    }

    /// Flies honestly for `secs` at `vel` (m/s): `fly_and_slide` every move,
    /// never corrected, the server agreeing where the broom is.
    fn fly(&mut self, vel: [f32; 3], secs: f32) {
        let steps = (secs / MOVE_EVERY.as_secs_f32()).round() as u32;
        let dt = MOVE_EVERY.as_secs_f32();
        for _ in 0..steps {
            let next = flight::fly_and_slide(
                &self.world,
                &self.air,
                &self.params(),
                self.pos,
                vel.map(|v| v * dt),
            );
            let corrected = self.send(next);
            assert!(
                corrected.is_empty(),
                "corrected from {:?} to {next:?}: {corrected:?}",
                self.pos
            );
            self.pos = next;
            assert_server_at(&self.s, self.id, next);
        }
    }
}

fn assert_server_at(s: &WorldSim, id: protocol::EntityId, at: [f32; 3]) {
    let server = s.position(id).expect("in the world");
    assert!(
        dist(server, at) < 1e-3,
        "the server has {server:?}, the client {at:?}"
    );
}

// ---------------------------------------------------------------- the world

#[test]
fn mounting_needs_the_flying_skill_open_sky_and_a_free_hand() {
    let world = castle();
    let at_gate = Some(gate(&world));
    // No Flying class taken yet.
    let mut r = Rider::join(sim(world.clone(), None), 1, at_gate, 0, false);
    assert_eq!(r.mount(true), (false, 0, Some(MountRefused::NoSkill)));
    // Inside the castle: never.
    let mut r = Rider::join(sim(world.clone(), None), 2, None, 10, false);
    let (on, _, reason) = r.mount(true);
    assert!(!on);
    assert!(
        matches!(reason, Some(MountRefused::NoFly | MountRefused::NoAirspace)),
        "{reason:?}"
    );
    // Sitting on the grass.
    let mut r = Rider::join(sim(world.clone(), None), 3, at_gate, 10, false);
    r.input(Input::Emote(protocol::Emote::Sit));
    assert_eq!(r.mount(true), (false, 0, Some(MountRefused::Busy)));
    // Standing at the pitch gate after a class: up, at the first tier, the
    // clearance over the grass.
    let mut r = Rider::join(sim(world.clone(), None), 4, at_gate, 10, false);
    let ground = r.pos[1];
    assert_eq!(r.mount(true), (true, 1, None));
    let rules = &shipped().flight;
    assert!((r.pos[1] - ground - rules.ground_clearance).abs() < 1e-4);
    // Asking again answers the state; so does landing twice.
    assert_eq!(r.mount(true), (true, 1, None));
    assert_eq!(r.mount(false), (false, 0, None));
    assert_eq!(r.mount(false), (false, 0, None));
    assert!((r.pos[1] - ground).abs() < 1e-3);
    // A flood is refused: a burst of three, then one a second.
    let mut r = Rider::join(sim(world.clone(), None), 5, at_gate, 10, false);
    let answers: Vec<_> = (0..4)
        .map(|_| {
            r.input(Input::Mount(true))
                .into_iter()
                .find_map(|m| match m {
                    ServerMsg::Mounted { reason, .. } => Some(reason),
                    _ => None,
                })
                .unwrap()
        })
        .collect();
    assert_eq!(answers[3], Some(MountRefused::RateLimited), "{answers:?}");
    // A higher skill flies faster.
    let skilled = shipped().recipes.skill_levels[2];
    let mut r = Rider::join(sim(world, None), 6, at_gate, skilled, false);
    assert_eq!(r.mount(true), (true, 2, None));
}

#[test]
fn an_honest_flight_at_the_castle_and_the_ceiling_is_never_corrected() {
    let world = castle();
    let mut r = Rider::join(sim(world.clone(), None), 10, Some(gate(&world)), 10, false);
    assert!(r.mount(true).0);
    let rules = &shipped().flight;
    let speed = rules.tiers[0].speed;
    // Climb a little, then straight north at the castle until it stops us.
    r.fly([0.0, 3.0, -speed * 0.5], 2.0);
    r.fly([0.0, 0.0, -speed], 13.0);
    assert!(
        r.pos[2] >= 100.0 + rules.margin - 0.02 && r.pos[2] < 101.0,
        "stopped short of the castle's no-fly box: {:?}",
        r.pos
    );
    // Along its face, and up into the ceiling, never over the castle.
    r.fly([-speed * 0.7, rules.climb_speed, -speed * 0.7], 12.0);
    assert!(r.pos[2] >= 100.0, "{:?}", r.pos);
    assert!(
        r.pos[1] <= rules.ceiling_y + 1e-3 && r.pos[1] > rules.ceiling_y - 1.0,
        "{:?}",
        r.pos
    );
    // Dive at the ground: skims it at the clearance.
    r.fly([speed * 0.5, -rules.climb_speed, speed * 0.5], 15.0);
    let floor = r
        .air
        .floor_at(&r.world, r.pos[0], r.pos[2], r.pos[1])
        .unwrap();
    assert!(
        (r.pos[1] - floor - rules.ground_clearance).abs() < 0.05,
        "{:?}",
        r.pos
    );
    // Hovering is free and fine.
    r.fly([0.0, 0.0, 0.0], 3.0);
}

#[test]
fn leaving_the_airspace_is_corrected_and_the_rider_stays_up() {
    let world = castle();
    let mut r = Rider::join(sim(world.clone(), None), 20, Some(gate(&world)), 10, false);
    assert!(r.mount(true).0);
    let speed = shipped().flight.tiers[0].speed;
    r.fly([0.0, 3.0, -speed], 12.0);
    let edge = r.pos;
    // A metre into the castle's no-fly box.
    let into = [edge[0], edge[1], 99.0];
    assert_eq!(r.send(into), vec![edge_q(edge)]);
    assert_server_at(&r.s, r.id, edge);
    r.wait(Duration::from_millis(300));
    // Under the ground.
    let floor = r.air.floor_at(&r.world, edge[0], edge[2], edge[1]).unwrap();
    assert_eq!(r.send([edge[0], floor - 0.5, edge[2]]), vec![edge_q(edge)]);
    r.wait(Duration::from_millis(300));
    // Far too fast.
    assert_eq!(
        r.send([edge[0] + 40.0, edge[1], edge[2] + 40.0]),
        vec![edge_q(edge)]
    );
    r.wait(Duration::from_millis(300));
    // Still mounted: honest flight goes on, nothing landed it.
    r.fly([speed, 0.0, speed], 2.0);
    assert!(
        r.drain()
            .iter()
            .all(|m| !matches!(m, ServerMsg::Mounted { .. }))
    );
}

/// A correction's position as the client receives it (centimetres).
fn edge_q(p: [f32; 3]) -> [f32; 3] {
    protocol::QPos::from_m(p).to_m()
}

#[test]
fn doors_refuse_riders() {
    let world = castle();
    let mut r = Rider::join(sim(world.clone(), None), 30, Some(gate(&world)), 10, false);
    assert!(r.mount(true).0);
    let msgs = r.input(Input::Interact("pitch_gate".into()));
    assert!(
        msgs.iter().any(|m| matches!(
            m,
            ServerMsg::DormError {
                code: DormErrorCode::Mounted,
                ..
            }
        )),
        "{msgs:?}"
    );
    assert!(
        !msgs
            .iter()
            .any(|m| matches!(m, ServerMsg::ZoneChanged { .. }))
    );
}

#[test]
fn a_logout_in_the_air_saves_the_ground_below() {
    let world = castle();
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut r = Rider::join(
        sim(world.clone(), Some(tx)),
        40,
        Some(gate(&world)),
        10,
        false,
    );
    assert!(r.mount(true).0);
    let speed = shipped().flight.tiers[0].speed;
    // Up over the field.
    r.fly([speed * 0.6, 3.5, speed * 0.6], 5.0);
    let (x, y, z) = (r.pos[0], r.pos[1], r.pos[2]);
    let ground = world.ground_below(x, z, y).unwrap();
    assert!(y - ground > 10.0, "{:?}", r.pos);
    let saved = |rx: &mut mpsc::UnboundedReceiver<SaveBatch>, id: i64| -> SavedPosition {
        let mut last = None;
        while let Ok(batch) = rx.try_recv() {
            last = batch
                .positions
                .into_iter()
                .rev()
                .find(|p| p.character_id == id)
                .or(last);
        }
        last.expect("saved")
    };
    assert!(r.s.remove_player(r.id));
    let p = saved(&mut rx, 40);
    assert_eq!(p.zone, SavedZone::Castle);
    assert_eq!(p.pos, [x, ground, z], "on the ground below");

    // Over deep water: a spawn instead.
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut r = Rider::join(
        sim(world.clone(), Some(tx)),
        41,
        Some(gate(&world)),
        10,
        false,
    );
    assert!(r.mount(true).0);
    r.fly([0.0, 3.0, 0.0], 1.0);
    let mut flown = 0;
    while world.ground_below(r.pos[0], r.pos[2], r.pos[1]).is_some() {
        r.fly([0.0, 0.0, speed], 1.0);
        flown += 1;
        assert!(flown < 40, "no deep water south of the pitch: {:?}", r.pos);
    }
    assert!(r.s.remove_player(r.id));
    let p = saved(&mut rx, 41);
    assert_eq!(p.pos, world.spawns()[0].pos);
}

#[test]
fn featherlight_speeds_the_broom() {
    let world = castle();
    let over_the_pitch = [-20.0, -9.5, 200.0];
    // A held-back second, then one long dash east: within Featherlight's
    // 115 % of the first tier, beyond the plain broom.
    let dash = |featherlight: bool, id: i64| -> Vec<[f32; 3]> {
        let mut r = Rider::join(
            sim(world.clone(), None),
            id,
            Some(over_the_pitch),
            10,
            featherlight,
        );
        assert_eq!(r.broom_pct, if featherlight { 115 } else { 100 });
        assert!(r.mount(true).0);
        r.fly([0.0, 4.0, 0.0], 2.0);
        r.fly([0.0, 0.0, 0.0], 1.5);
        let p = r.pos;
        r.send([p[0] + 11.0, p[1], p[2]])
    };
    assert!(dash(true, 50).is_empty(), "Featherlight's 9.2 m/s");
    assert_eq!(dash(false, 51).len(), 1, "a plain broom's 8 m/s");
    // Drinking it in flight tells the client its new speed.
    let mut r = Rider::join(sim(world, None), 52, Some(over_the_pitch), 10, false);
    assert!(r.mount(true).0);
    let effect = server::potions::effect_for(FEATHERLIGHT, [0, 0, 0], None, r.at).unwrap();
    assert_eq!(effect.kind, EffectKind::Featherlight);
    assert!(effect.visible.is_none(), "nobody else sees it");
}

#[test]
fn landing_needs_the_ground_within_reach() {
    let world = castle();
    let mut r = Rider::join(sim(world.clone(), None), 60, Some(gate(&world)), 10, false);
    assert!(r.mount(true).0);
    r.fly([3.0, 3.0, 3.0], 2.0);
    assert_eq!(r.mount(false), (true, 1, Some(MountRefused::NotLanded)));
    r.fly([0.0, -3.0, 0.0], 3.0);
    assert_eq!(r.mount(false), (false, 0, None));
    let g = world.ground_at(r.pos[0], r.pos[2], r.pos[1]).unwrap();
    assert!((g.y - r.pos[1]).abs() < 1e-3);
    // Walking again: a walking move is checked as one.
    let next = sim_core::move_and_slide(
        &r.world,
        &sim_core::MoveParams::default(),
        r.pos,
        [0.4, 0.0],
    );
    r.at += MOVE_EVERY;
    r.seq += 1;
    r.s.input(
        r.id,
        r.at,
        Input::Move {
            seq: r.seq,
            pos: next,
            yaw: 0.0,
            anim: Anim::Walk,
        },
    );
    assert_server_at(&r.s, r.id, next);
}

// ---------------------------------------------------------------- end to end

const WAIT: Duration = Duration::from_secs(5);

struct Server {
    addr: SocketAddr,
    state: AppState,
}

impl Server {
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
            World::from_json(LAYOUT).unwrap(),
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
        Server { addr, state }
    }

    fn set_clock(&self, unix_ms: i64) {
        let clock = self.state.world().unwrap().clock();
        let real = clock.now_unix_ms() - clock.skew_ms();
        clock.set_skew_ms(unix_ms - real);
    }

    async fn player(&self) -> (i64, Ws) {
        let app = TestApp::from_state(self.state.clone());
        let name = common::unique_name();
        let res = app
            .post("/api/dev/bot-session", json!({ "name": name }), None)
            .await;
        let token = res.body["token"].as_str().unwrap().to_owned();
        let (ws, _) = tokio_tungstenite::connect_async(format!("ws://{}/ws", self.addr))
            .await
            .unwrap();
        let mut c = Ws { ws, seq: 0 };
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

struct Ws {
    ws: WebSocketStream<MaybeTlsStream<TcpStream>>,
    seq: u32,
}

impl Ws {
    async fn send(&mut self, msg: &ClientMsg) {
        self.ws
            .send(Message::Binary(encode_client(msg).into()))
            .await
            .unwrap();
    }

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

    async fn mounted(&mut self) -> (bool, u8, Option<MountRefused>) {
        self.expect(|m| match m {
            ServerMsg::Mounted { on, tier, reason } => Some((*on, *tier, *reason)),
            _ => None,
        })
        .await
    }

    async fn move_to(&mut self, pos: [f32; 3]) {
        self.seq += 1;
        self.send(&ClientMsg::Move {
            seq: self.seq,
            pos,
            yaw: 0.0,
            anim: Anim::Fly,
        })
        .await;
    }
}

/// A student on the practice field, flying honestly in real time.
struct Student {
    ws: Ws,
    world: World,
    air: Airspace,
    params: FlyParams,
    pos: [f32; 3],
}

impl Student {
    /// Flies to `to` at a little under the first tier's speed (and climb),
    /// a move every 100 ms; returns when there.
    async fn fly_to(&mut self, to: [f32; 3]) {
        let dt = MOVE_EVERY.as_secs_f32();
        let (h, v) = (
            self.params.speed * 0.9 * dt,
            self.params.climb_speed * 0.9 * dt,
        );
        for _ in 0..400 {
            let d = [
                to[0] - self.pos[0],
                to[1] - self.pos[1],
                to[2] - self.pos[2],
            ];
            let flat = d[0].hypot(d[2]);
            if flat < 0.02 && d[1].abs() < 0.02 {
                return;
            }
            let k = if flat > h { h / flat } else { 1.0 };
            let step = [d[0] * k, d[1].clamp(-v, v), d[2] * k];
            self.pos = flight::fly_and_slide(&self.world, &self.air, &self.params, self.pos, step);
            self.ws.move_to(self.pos).await;
            tokio::time::sleep(MOVE_EVERY).await;
        }
        panic!("never reached {to:?} from {:?}", self.pos);
    }
}

/// The Flying class over WebSockets: refused a broom before it, mounted on
/// joining, the first round's eight rings flown through in time and scored
/// from the checked moves, the results feeding the Flying skill, landed
/// when the lesson ends, then mounting at the pitch gate; a retried reward
/// grants nothing more.
#[tokio::test]
async fn a_flying_lesson_end_to_end_then_a_broom_of_your_own() {
    let content = shipped();
    let schedule = &content.schedule;
    let sched = schedule.class("flying").unwrap();
    assert_eq!(sched.door, "pitch_gate");
    let def = content.class("flying").unwrap();
    assert_eq!(def.subject, Subject::Flying);
    let server = Server::start().await;
    let castle = World::from_json(LAYOUT).unwrap();
    let gate_pos = gate(&castle);
    let (me, mut ws) = server.player().await;
    let world = server.state.world().unwrap().clone();
    assert!(world.dev_teleport(me, gate_pos).await, "to the pitch gate");

    // No Flying class yet.
    ws.send(&ClientMsg::Mount { on: true }).await;
    assert_eq!(ws.mounted().await, (false, 0, Some(MountRefused::NoSkill)));

    // Into the class: on a broom at once.
    let times = schedule.next(sched, Utc::now().timestamp_millis());
    let at = |ms: i64| times.start_ms + ms;
    server.set_clock(at(10_000));
    ws.send(&ClientMsg::Interact {
        target: "pitch_gate".into(),
    })
    .await;
    let (zone, spawn) = ws
        .expect(|m| match m {
            ServerMsg::ZoneChanged { zone, spawn, .. } => Some((zone.clone(), spawn.to_m())),
            ServerMsg::ClassError { .. } | ServerMsg::DormError { .. } => panic!("{m:?}"),
            _ => None,
        })
        .await;
    assert!(
        matches!(&zone, Zone::Class { class, layout, .. } if class == "flying" && layout == "class_pitch"),
        "{zone:?}"
    );
    let (on, tier, reason) = ws.mounted().await;
    assert_eq!((on, tier, reason), (true, 1, None));
    ws.expect(|m| matches!(m, ServerMsg::ClassJoined { .. }).then_some(()))
        .await;
    let field = World::from_json(CLASS_PITCH).unwrap();
    let air = Airspace::new(&field);
    let params = content.flight.params(tier, 100);
    let lifted = flight::mount(&field, &air, &params, spawn).unwrap();
    let mut student = Student {
        ws,
        world: field,
        air,
        params,
        pos: lifted,
    };

    // Round 1: through every ring on its beat.
    let (step, round, _, _) = def.rounds().next().unwrap();
    server.set_clock(at(i64::from(step.at_secs) * 1_000 + 300));
    let (window, beats) = student
        .ws
        .expect(|m| match m {
            ServerMsg::LessonRound {
                round: r,
                window_ms,
                beats,
            } if *r == round => Some((*window_ms, beats.clone())),
            _ => None,
        })
        .await;
    assert_eq!(beats.len(), 8);
    let rings = student.world.layout().rings.clone();
    for (k, beat) in beats.iter().enumerate() {
        let BeatPrompt::Ring { ring } = beat.prompt else {
            panic!("a ring beat: {beat:?}");
        };
        let r = &rings[usize::from(ring)];
        let n = [r.yaw.sin(), 0.0, r.yaw.cos()];
        let before = [r.pos[0] - n[0] * 2.0, r.pos[1], r.pos[2] - n[2] * 2.0];
        let after = [r.pos[0] + n[0] * 2.0, r.pos[1], r.pos[2] + n[2] * 2.0];
        student.fly_to(before).await;
        // Just before the beat, through the ring.
        server.set_clock(beat.at_unix_ms as i64 - 250);
        student.fly_to(after).await;
        let (b, result) = student
            .ws
            .expect(|m| match m {
                ServerMsg::BeatScored {
                    round: r,
                    beat,
                    result,
                } if *r == round => Some((*beat, *result)),
                ServerMsg::Correction { .. } => panic!("corrected: {m:?}"),
                _ => None,
            })
            .await;
        assert_eq!(usize::from(b), k);
        assert_eq!(result.rejected, None);
        assert!(
            result.score >= 100 - shipped().gestures.scoring.timing_weight_pct,
            "{result:?}"
        );
        let _ = window;
    }

    // The results (rounds 2 and 3 missed): Flying skill XP.
    let results = def
        .lesson
        .iter()
        .find(|s| matches!(s.action, LessonAction::Results { .. }))
        .unwrap()
        .at_secs;
    server.set_clock(at(i64::from(results) * 1_000 + 300));
    let (score_pct, rewards) = student
        .ws
        .expect(|m| match m {
            ServerMsg::ClassResult {
                class,
                score_pct,
                rewards,
                ..
            } if class == "flying" => Some((*score_pct, rewards.clone())),
            _ => None,
        })
        .await;
    let total_beats = def.beat_count() as u32;
    assert!(u32::from(score_pct) * total_beats >= 8 * 80, "{score_pct}");
    assert!(rewards.skill_xp >= def.rewards.skill_xp(score_pct));
    assert!(rewards.items.is_empty(), "no items");
    let pool = &server.state.pool;
    let flying_xp: i32 = sqlx::query_scalar(
        "SELECT xp FROM skills WHERE character_id = $1 AND discipline = 'flying'",
    )
    .bind(me)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(flying_xp as u32, rewards.skill_xp);
    let (attendance_id, answers): (i64, i16) = sqlx::query_as(
        "SELECT id, answers FROM class_attendance
         WHERE character_id = $1 AND class_id = 'flying' AND session_key = $2",
    )
    .bind(me)
    .bind(times.key.id())
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(answers, 8);

    // The lesson ends: landed, then back at the pitch gate.
    server.set_clock(times.end_ms + 300);
    let mut ws = student.ws;
    assert_eq!(ws.mounted().await, (false, 0, None));
    let (zone, back) = ws
        .expect(|m| match m {
            ServerMsg::ZoneChanged { zone, spawn, .. } => Some((zone.clone(), spawn.to_m())),
            _ => None,
        })
        .await;
    assert_eq!(zone, Zone::Castle);
    assert!(dist(back, gate_pos) < 0.1, "{back:?}");

    // A broom of your own now.
    tokio::time::sleep(Duration::from_millis(1_100)).await;
    ws.send(&ClientMsg::Mount { on: true }).await;
    assert_eq!(ws.mounted().await, (true, 1, None));
    // The gate refuses a rider.
    ws.send(&ClientMsg::Interact {
        target: "pitch_gate".into(),
    })
    .await;
    let code = ws
        .expect(|m| match m {
            ServerMsg::DormError { code, .. } => Some(*code),
            ServerMsg::ZoneChanged { .. } => panic!("let a rider in"),
            _ => None,
        })
        .await;
    assert_eq!(code, DormErrorCode::Mounted);

    // A retried reward grants nothing more and reports the same.
    let req = RewardRequest {
        attendance_id,
        character_id: me,
        class_id: "flying".into(),
        score_pct,
        answers: 8,
        section_pct: score_pct,
    };
    match classes::reward(pool, &req, Utc::now()).await.unwrap() {
        RewardOutcome::AlreadyRewarded(r) => assert_eq!(r, rewards),
        other => panic!("{other:?}"),
    }
    let again: i32 = sqlx::query_scalar(
        "SELECT xp FROM skills WHERE character_id = $1 AND discipline = 'flying'",
    )
    .bind(me)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(again, flying_xp);
}
