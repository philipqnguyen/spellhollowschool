//! A harness for the tests (included by `secrets.rs` and
//! `wishing_room.rs` as a module; on its own it has no tests): the world
//! simulation over the shipped castle (`data/world/layout.json`, its secrets
//! and the Wishing Room), driven directly with a pinned clock, no
//! sockets. Database work (discoveries, finds) runs only when the test hands
//! over a pool ([`Castle::run_jobs`]); characters for it come from
//! `TestApp::player`.

#![allow(dead_code)]

use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use protocol::{Anim, House, ServerMsg, decode_server};
use server::characters::{Character, SavedZone};
use server::clock::ServerClock;
use server::metrics::Registry;
use server::realtime::ConnControl;
use server::realtime::chat::WordFilter;
use server::realtime::entities::Social;
use server::realtime::metrics::RealtimeMetrics;
use server::realtime::persist::SaveBatch;
use server::realtime::world::{Input, Joining, WorldCmd, WorldSim};
use sqlx::PgPool;
use tokio::sync::{mpsc, oneshot};

/// The Wishing Room's corridor, by the west mark and the east mark.
pub const WEST: [f32; 3] = [-11.0, 63.0, 4.0];
pub const EAST: [f32; 3] = [5.0, 63.0, 4.0];
/// In front of the blank wall's door, in its reach.
pub const DOOR: [f32; 3] = [-3.0, 63.0, 7.5];

pub struct Castle {
    pub s: WorldSim,
    pub metrics: Arc<RealtimeMetrics>,
    base: Instant,
    t0: i64,
    /// The pinned clock's "now" (Unix ms) as the test has moved it.
    pub ms: i64,
}

pub struct Seat {
    pub id: protocol::EntityId,
    pub cid: i64,
    pub name: String,
    pub rx: mpsc::Receiver<Bytes>,
    seq: u32,
    pub pos: [f32; 3],
}

impl Seat {
    /// Everything received since the last call.
    pub fn msgs(&mut self) -> Vec<ServerMsg> {
        let mut v = Vec::new();
        while let Ok(b) = self.rx.try_recv() {
            v.push(decode_server(&b).expect("decodes"));
        }
        v
    }
}

/// The shipped castle as the server loads it.
pub fn shipped_castle() -> sim_core::World {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/layout.json");
    server::realtime::load_world(&path).expect("the shipped castle loads")
}

impl Castle {
    /// The castle with its clock pinned; `saver` takes the saves.
    pub fn new(saver: Option<mpsc::UnboundedSender<SaveBatch>>) -> Castle {
        let metrics = Arc::new(RealtimeMetrics::register(&Registry::default()));
        let mut s = WorldSim::new(
            Arc::new(shipped_castle()),
            WordFilter::from_list(""),
            saver,
            metrics.clone(),
            1,
        );
        let base = Instant::now();
        let t0 = 1_800_000_000_000;
        s.set_clock(Arc::new(ServerClock::starting_at(t0, base)));
        Castle {
            s,
            metrics,
            base,
            t0,
            ms: t0,
        }
    }

    /// The instant the clock reads `ms` (Unix ms).
    pub fn at(&self, ms: i64) -> Instant {
        self.base + Duration::from_millis((ms - self.t0) as u64)
    }

    /// Joins character `id` at `pos` in the castle with `friends` and what it
    /// has explored.
    pub fn join(
        &mut self,
        id: i64,
        name: &str,
        pos: [f32; 3],
        friends: Vec<i64>,
        explored: server::map::Explored,
    ) -> Seat {
        let character = Character {
            id,
            name: name.to_owned(),
            house: House::Skytalon,
            zone: SavedZone::Castle,
            last_pos: Some(pos),
            appearance_version: 0,
            appearance: protocol::Appearance::default(),
        };
        let joining = Joining {
            social: Social {
                friends,
                ..Social::default()
            }
            .normalized(),
            explored,
            ..Joining::alone(character)
        };
        let (tx, rx) = mpsc::channel(1 << 14);
        let at = self.at(self.ms);
        let eid = self
            .s
            .join(joining, tx, Arc::new(ConnControl::default()), at)
            .expect("joined");
        let pos = self.s.position(eid).unwrap();
        Seat {
            id: eid,
            cid: id,
            name: name.to_owned(),
            rx,
            seq: 0,
            pos,
        }
    }

    /// Lets `ms` milliseconds pass (ticking the world every 50 ms).
    pub fn wait(&mut self, ms: i64) {
        let end = self.ms + ms;
        while self.ms < end {
            self.ms += 50;
            let at = self.at(self.ms);
            self.s.tick(at);
        }
    }

    /// One input from `seat` now.
    pub fn input(&mut self, seat: &Seat, input: Input) {
        let at = self.at(self.ms);
        self.s.input(seat.id, at, input);
    }

    /// Walks `seat` in a straight line to `to` at 3.5 m/s (a move every
    /// 100 ms); the world agrees where it ends.
    pub fn walk(&mut self, seat: &mut Seat, to: [f32; 3]) {
        loop {
            let (dx, dz) = (to[0] - seat.pos[0], to[2] - seat.pos[2]);
            let d = (dx * dx + dz * dz).sqrt();
            if d < 0.01 {
                break;
            }
            let len = d.min(0.35);
            let next = [
                seat.pos[0] + dx / d * len,
                to[1],
                seat.pos[2] + dz / d * len,
            ];
            self.wait(100);
            seat.seq += 1;
            self.input(
                seat,
                Input::Move {
                    seq: seat.seq,
                    pos: next,
                    yaw: 0.0,
                    anim: Anim::Walk,
                },
            );
            let stored = self.s.position(seat.id).unwrap();
            assert!(
                (stored[0] - next[0]).abs() < 1e-3 && (stored[2] - next[2]).abs() < 1e-3,
                "{} walking to {to:?}: the server holds {stored:?}, not {next:?}",
                seat.name
            );
            seat.pos = stored;
        }
    }

    /// Paces `seat` from the corridor's west mark to the east one and back
    /// `passes` times (it starts in the west mark).
    pub fn pace(&mut self, seat: &mut Seat, passes: usize) {
        for k in 0..passes {
            let to = if k % 2 == 0 { EAST } else { WEST };
            self.walk(seat, to);
        }
    }

    /// The dev teleport (`POST /api/dev/teleport`): to `pos` in the zone
    /// the character is in.
    pub fn teleport(&mut self, seat: &mut Seat, pos: [f32; 3]) -> bool {
        let (reply, mut rx) = oneshot::channel();
        self.s.handle(WorldCmd::DevTeleport {
            character_id: seat.cid,
            pos,
            reply,
        });
        let ok = rx.try_recv().unwrap_or(false);
        if ok {
            seat.pos = self.s.position(seat.id).unwrap();
            // A move after a teleport comes after the zone-change grace.
            self.wait(600);
        }
        ok
    }

    /// Runs the feature jobs queued so far against `pool` and hands their
    /// results back to the world (as the executor does).
    pub async fn run_jobs(&mut self, pool: &PgPool) -> usize {
        let mut n = 0;
        loop {
            let jobs = self.s.take_ext_jobs();
            if jobs.is_empty() {
                return n;
            }
            for job in jobs {
                n += 1;
                let done = job(pool.clone()).await;
                self.s.handle(WorldCmd::ExtDone(done));
            }
        }
    }
}

/// The test database (`DATABASE_URL`, default `postgres://localhost/shs_test`).
pub fn database_url() -> String {
    std::env::var("DATABASE_URL").unwrap_or_else(|_| "postgres://localhost/shs_test".into())
}
