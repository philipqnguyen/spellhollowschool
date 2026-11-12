//! The Grand Staircase's moving flights through the server's world
//! (docs/architecture.md). The schedule is computed from the
//! clock on both ends, so nothing is sent about it:
//!
//! - a character standing on a flight when it switches is carried to the
//!   same spot of its new position (no `Correction`), and walks on from
//!   there;
//! - an honest client whose clock is up to 300 ms early or late, carrying
//!   itself at its own switch and holding still while its flight swings,
//!   rides and walks the staircase across 10 switches without a single
//!   correction (the grace window either side of each switch);
//! - a walker heading for a landing whose flight has swung away stops at
//!   the edge, and a move past it is corrected.
//!
//! No database: the world alone, with a pinned clock.

use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use protocol::{Anim, House, ServerMsg, decode_server};
use server::characters::{Character, SavedZone};
use server::clock::ServerClock;
use server::metrics::Registry;
use server::realtime::ConnControl;
use server::realtime::chat::WordFilter;
use server::realtime::metrics::RealtimeMetrics;
use server::realtime::world::{Input, Joining, WorldSim};
use sim_core::stairs::flight_surface;
use sim_core::{FlightPhase, MoveParams, StairPosition, Stairs, World, move_and_slide};
use tokio::sync::mpsc;

const LAYOUT: &str = include_str!("../../../data/world/layout.json");

/// Flight 6-7 (bit 2): from the sixth floor's east gallery across the well
/// (A), or up south onto the seventh floor's balcony (B).
const F67: usize = 2;

struct World3c {
    s: WorldSim,
    metrics: Arc<RealtimeMetrics>,
    stairs: Arc<Stairs>,
    all: Arc<World>,
    /// The instant the pinned clock reads `t0` Unix ms.
    base: Instant,
    t0: i64,
}

impl World3c {
    /// The castle with its clock pinned at `t0` (Unix ms) now, on the
    /// shipped period or `period_s`.
    fn new(t0: i64, period_s: Option<u32>) -> World3c {
        let all = Arc::new(World::from_json(LAYOUT).expect("the shipped layout loads"));
        let metrics = Arc::new(RealtimeMetrics::register(&Registry::default()));
        let mut s = WorldSim::new(
            all.clone(),
            WordFilter::from_list(""),
            None,
            metrics.clone(),
            1,
        );
        if let Some(p) = period_s {
            s.set_stairs_period(p).unwrap();
        }
        let base = Instant::now();
        s.set_clock(Arc::new(ServerClock::starting_at(t0, base)));
        let stairs = s.stairs().expect("the castle has moving flights").clone();
        World3c {
            s,
            metrics,
            stairs,
            all,
            base,
            t0,
        }
    }

    /// The instant the server's clock reads `ms`.
    fn at(&self, ms: i64) -> Instant {
        self.base + Duration::from_millis((ms - self.t0) as u64)
    }

    fn join(&mut self, id: i64, house: House, last_pos: [f32; 3]) -> Seat {
        let character = Character {
            id,
            name: format!("Rider{id}"),
            house,
            zone: SavedZone::Castle,
            last_pos: Some(last_pos),
            appearance_version: 0,
            appearance: protocol::Appearance::default(),
        };
        let (tx, rx) = mpsc::channel(1 << 16);
        let id = self
            .s
            .join(
                Joining::alone(character),
                tx,
                Arc::new(ConnControl::default()),
                self.base,
            )
            .expect("joined");
        Seat { id, rx }
    }

    /// The first switch of `flight` to `to` after `after` (Unix ms).
    fn switch(&self, flight: usize, to: StairPosition, after: i64) -> i64 {
        self.stairs
            .switches_between(after, after + 4 * self.stairs.period_ms())
            .into_iter()
            .find(|&(f, _, p)| f == flight && p == to)
            .expect("a switch within two periods")
            .1
    }

    /// The middle of flight `flight`'s surface at `position`, on its ground.
    fn middle(&self, flight: usize, position: StairPosition) -> [f32; 3] {
        let s = flight_surface(self.all.layout(), &self.stairs.flights()[flight], position)
            .expect("the flight's surface");
        let (x, z) = ((s.min[0] + s.max[0]) / 2.0, (s.min[1] + s.max[1]) / 2.0);
        [x, s.height_at(x, z), z]
    }
}

struct Seat {
    id: protocol::EntityId,
    rx: mpsc::Receiver<Bytes>,
}

impl Seat {
    /// The corrections received since the last call.
    fn corrections(&mut self) -> Vec<[f32; 3]> {
        let mut v = Vec::new();
        while let Ok(b) = self.rx.try_recv() {
            if let Ok(ServerMsg::Correction { pos, .. }) = decode_server(&b) {
                v.push(pos.to_m());
            }
        }
        v
    }
}

fn near(a: [f32; 3], b: [f32; 3], tol: f32) -> bool {
    (0..3).all(|k| (a[k] - b[k]).abs() <= tol)
}

/// A rider standing on flight 6-7 at A when it switches is carried to the
/// same spot of B, at B's height there, facing along the flight: no
/// correction, and its next move from there is accepted. Back at A after
/// the next switch.
#[test]
fn a_rider_is_carried() {
    let probe = World3c::new(180_000, None);
    let to_b = probe.switch(F67, StairPosition::B, 180_000);
    // Restart the clock 4 s before the switch (the flight swinging).
    let mut w = World3c::new(to_b - 4_000, None);
    assert_eq!(w.s.stair_state() >> F67 & 1, 0, "flight 6-7 at A");
    // (Pinning the clock was a jump of its own.)
    let switched = w.metrics.stairs_switches.get();
    let on_a = w.middle(F67, StairPosition::A);
    let mut seat = w.join(1, House::Roarwing, on_a);
    assert!(near(w.s.position(seat.id).unwrap(), on_a, 1e-3));
    let want = w
        .stairs
        .carry(
            w.all.layout(),
            on_a,
            F67,
            StairPosition::A,
            StairPosition::B,
        )
        .unwrap();
    // Ticks through the swing: still on A until the switch.
    let mut t = to_b - 4_000;
    while t < to_b - 50 {
        t += 50;
        w.s.tick(w.at(t));
    }
    assert!(near(w.s.position(seat.id).unwrap(), on_a, 1e-3));
    assert_eq!(w.metrics.stairs_riders_carried.get(), 0);
    w.s.tick(w.at(to_b));
    let at = w.s.position(seat.id).unwrap();
    assert!(near(at, want, 1e-3), "carried to {at:?}, want {want:?}");
    assert_eq!(w.s.stair_state() >> F67 & 1, 1);
    assert_eq!(w.metrics.stairs_riders_carried.get(), 1);
    assert_eq!(w.metrics.stairs_switches.get(), switched + 1);
    assert!(seat.corrections().is_empty(), "no correction for a ride");
    // B is on the south-running position: the rider's spot is on B's
    // surface, at its height.
    let b = flight_surface(w.all.layout(), &w.stairs.flights()[F67], StairPosition::B).unwrap();
    assert!(b.contains(at[0], at[2]));
    assert!((at[1] - b.height_at(at[0], at[2])).abs() < 1e-3);
    // The client carried itself with the same function and walks on, up B.
    let world = w.s_world(House::Roarwing, w.s.stair_state());
    let next = move_and_slide(&world, &MoveParams::default(), at, [0.0, 0.3]);
    assert!(next[2] > at[2] + 0.2, "walks up B");
    w.s.input(
        seat.id,
        w.at(to_b + 1_500),
        Input::Move {
            seq: 1,
            pos: next,
            yaw: 0.0,
            anim: Anim::Walk,
        },
    );
    assert!(seat.corrections().is_empty());
    assert!(near(w.s.position(seat.id).unwrap(), next, 1e-3));
    // The next switch takes it back to A, where it started from that spot.
    let to_a = w.switch(F67, StairPosition::A, to_b);
    let mut t = to_b + 1_500;
    while t < to_a {
        t = (t + 50).min(to_a);
        w.s.tick(w.at(t));
    }
    let back = w.s.position(seat.id).unwrap();
    let want = w
        .stairs
        .carry(
            w.all.layout(),
            next,
            F67,
            StairPosition::B,
            StairPosition::A,
        )
        .unwrap();
    assert!(near(back, want, 1e-3), "{back:?} != {want:?}");
    assert_eq!(w.metrics.stairs_riders_carried.get(), 2);
    assert!(seat.corrections().is_empty());
}

/// An honest client as the browser plays it: its clock `error_ms` off the
/// server's, it walks its own staircase state's world with
/// `move_and_slide`, carries itself when its clock reaches a switch of the
/// flight it stands on, and holds still while that flight swings.
struct Honest {
    house: House,
    error_ms: i64,
    pos: [f32; 3],
    state: u8,
    worlds: sim_core::Worlds,
    params: MoveParams,
    seq: u32,
    corrections: usize,
    /// Metres a second (walk speed by default).
    speed: f32,
}

impl Honest {
    fn new(w: &World3c, house: House, error_ms: i64, pos: [f32; 3], now_ms: i64) -> Honest {
        let layout = w.all.layout().for_house(house);
        Honest {
            house,
            error_ms,
            pos,
            state: w.stairs.state(now_ms + error_ms),
            worlds: sim_core::Worlds::new(&layout).unwrap(),
            params: MoveParams::default(),
            seq: 0,
            corrections: 0,
            speed: MoveParams::default().walk_speed,
        }
    }

    fn world(&self) -> &World {
        self.worlds.get(self.state)
    }

    /// The moving flight the client stands on, if any.
    fn on_flight(&self, w: &World3c) -> Option<usize> {
        let world = self.world();
        let g = world.ground_at(self.pos[0], self.pos[2], self.pos[1])?;
        let id = &world.surface(g.surface).id;
        w.stairs
            .flights()
            .iter()
            .position(|f| f.a == *id || f.b == *id)
    }

    /// The client's clock reads a new staircase state: carry itself if it
    /// stands on a flight that switched (the same function as the server).
    fn follow_clock(&mut self, w: &World3c, server_ms: i64) {
        let now = w.stairs.state(server_ms + self.error_ms);
        if now == self.state {
            return;
        }
        if let Some(f) = self.on_flight(w)
            && (now ^ self.state) >> f & 1 == 1
        {
            let (from, to) = if self.state >> f & 1 == 1 {
                (StairPosition::B, StairPosition::A)
            } else {
                (StairPosition::A, StairPosition::B)
            };
            self.pos = w
                .stairs
                .carry(w.all.layout(), self.pos, f, from, to)
                .expect("stands on the flight");
        }
        self.state = now;
    }

    /// Whether the flight under the client is swinging (input held).
    fn held(&self, w: &World3c, server_ms: i64) -> bool {
        self.on_flight(w).is_some_and(|f| {
            matches!(
                w.stairs.flight_state(f, server_ms + self.error_ms).phase,
                FlightPhase::Swing { .. }
            )
        })
    }

    /// One 100 ms frame at server time `ms`: follow the clock, step toward
    /// `goal` (unless held), send the move, tick the world twice.
    fn frame(&mut self, w: &mut World3c, seat: &mut Seat, ms: i64, goal: [f32; 3]) {
        self.follow_clock(w, ms);
        if !self.held(w, ms) {
            let (dx, dz) = (goal[0] - self.pos[0], goal[2] - self.pos[2]);
            let d = (dx * dx + dz * dz).sqrt();
            if d > 0.05 {
                let len = (self.speed * 0.1).min(d);
                self.pos = move_and_slide(
                    self.world(),
                    &self.params,
                    self.pos,
                    [dx / d * len, dz / d * len],
                );
            }
        }
        self.seq += 1;
        w.s.input(
            seat.id,
            w.at(ms),
            Input::Move {
                seq: self.seq,
                pos: self.pos,
                yaw: 0.0,
                anim: Anim::Run,
            },
        );
        w.s.tick(w.at(ms + 25));
        w.s.tick(w.at(ms + 75));
        let c = seat.corrections();
        if !c.is_empty() {
            eprintln!(
                "{:?} ({} ms off) corrected at {ms} (state {:03b}, server {:03b}): {:?} -> {c:?}",
                self.house,
                self.error_ms,
                self.state,
                w.s.stair_state(),
                self.pos
            );
            self.corrections += c.len();
            self.pos = c[c.len() - 1];
        }
    }
}

/// Riding: an honest client shuffles up and down the middle of flight 6-7
/// for 10 of its switches (the development period, 20 s), its clock 300 ms
/// early, then 300 ms late, then 150 ms early: never corrected, carried
/// (by itself and by the server) to the same spots, and the server agrees
/// where it stands at the end.
#[test]
fn an_honest_rider_crosses_ten_switches_uncorrected() {
    for (k, error_ms) in [300i64, -300, 150].into_iter().enumerate() {
        let t0 = 1_000_000;
        let mut w = World3c::new(t0, Some(20));
        let state = w.s.stair_state();
        let at = if state >> F67 & 1 == 1 {
            StairPosition::B
        } else {
            StairPosition::A
        };
        let start = w.middle(F67, at);
        let mut seat = w.join(10 + k as i64, House::Burrowsby, start);
        let mut c = Honest::new(&w, House::Burrowsby, error_ms, start, t0);
        let switches = |w: &World3c| w.metrics.stairs_switches.get();
        let mut ms = t0;
        let mut out = true;
        while w.metrics.stairs_riders_carried.get() < 10 {
            ms += 100;
            // Shuffle along the flight's long axis, 1.5 m either side of
            // its middle, in the client's own state.
            let pos = if c.state >> F67 & 1 == 1 {
                StairPosition::B
            } else {
                StairPosition::A
            };
            let mid = w.middle(F67, pos);
            let s = flight_surface(w.all.layout(), &w.stairs.flights()[F67], pos).unwrap();
            let along_x = s.max[0] - s.min[0] > s.max[1] - s.min[1];
            let off = if out { 1.5 } else { -1.5 };
            let goal = if along_x {
                [mid[0] + off, mid[1], mid[2]]
            } else {
                [mid[0], mid[1], mid[2] + off]
            };
            if (c.pos[0] - goal[0]).abs() + (c.pos[2] - goal[2]).abs() < 0.1 {
                out = !out;
            }
            c.frame(&mut w, &mut seat, ms, goal);
            assert!(ms < t0 + 400_000, "10 rides take about 200 s");
        }
        // Let the client's clock catch up with the last switch.
        for _ in 0..5 {
            ms += 100;
            let goal = c.pos;
            c.frame(&mut w, &mut seat, ms, goal);
        }
        assert_eq!(c.corrections, 0, "{error_ms} ms off: corrected");
        assert!(switches(&w) >= 10);
        let server = w.s.position(seat.id).unwrap();
        assert!(
            near(server, c.pos, 1e-3),
            "{error_ms} ms off: server {server:?}, client {:?}",
            c.pos
        );
        eprintln!(
            "rider {error_ms} ms off: {} switches, {} rides, {} grace accepts, 0 corrections",
            switches(&w),
            w.metrics.stairs_riders_carried.get(),
            w.metrics.stairs_grace_accepts.get()
        );
        if error_ms < 0 {
            assert!(
                w.metrics.stairs_grace_accepts.get() > 0,
                "a late client's moves from the old position are accepted by the grace"
            );
        }
    }
}

/// Walking: an honest client tours the Grand Staircase (up from the ground
/// floor through every moving flight's landings to the seventh floor and
/// down again) along the trail of its own staircase state, re-planned
/// whenever its state changes, across at least 10 switches, its clock up
/// to 300 ms off either way: never corrected.
#[test]
fn an_honest_walker_crosses_ten_switches_uncorrected() {
    for error_ms in [300i64, -300, 0] {
        honest_walker(error_ms);
    }
}

fn honest_walker(error_ms: i64) {
    let t0 = 2_000_000;
    let mut w = World3c::new(t0, Some(20));
    let lm = |id: &str| {
        w.all
            .layout()
            .landmarks
            .iter()
            .find(|l| l.id == id)
            .unwrap_or_else(|| panic!("landmark {id}"))
            .pos
    };
    let surface_mid = |id: &str| {
        let s = w.all.layout().surfaces.iter().find(|s| s.id == id).unwrap();
        let (x, z) = ((s.min[0] + s.max[0]) / 2.0, (s.min[1] + s.max[1]) / 2.0);
        [x, s.height_at(x, z), z]
    };
    let stops = [
        lm("grand_staircase_l3_balcony"),
        surface_mid("gs_pier_3_4"),
        surface_mid("gs_corbel_l6"),
        lm("grand_staircase_l7_balcony"),
        surface_mid("gs_landing_l7"),
        surface_mid("gs_l6_east"),
        surface_mid("gs_landing_3_4_a"),
        surface_mid("gs_floor_centre"),
    ];
    let start = surface_mid("gs_floor_centre");
    let mut seat = w.join(20, House::Skytalon, start);
    let mut c = Honest::new(&w, House::Skytalon, error_ms, start, t0);
    c.speed = c.params.run_speed;
    let mut navs: Vec<Option<sim_core::nav::Nav>> = (0..8).map(|_| None).collect();
    let mut ms = t0;
    let mut next = 0usize;
    let mut path: Vec<[f32; 3]> = Vec::new();
    let mut planned_in = u8::MAX;
    let mut reached = 0;
    let switched = w.metrics.stairs_switches.get();
    while w.metrics.stairs_switches.get() < switched + 10 || reached < stops.len() {
        ms += 100;
        c.follow_clock(&w, ms);
        if c.state != planned_in || path.is_empty() {
            let nav = navs[usize::from(c.state)].get_or_insert_with(|| {
                sim_core::nav::Nav::new(w.s_world(c.house, c.state), &[], |_| true)
            });
            if path.is_empty() && planned_in != u8::MAX {
                reached += 1;
                next = (next + 1) % stops.len();
            }
            path = nav
                .path(c.pos, stops[next])
                .unwrap_or_else(|| panic!("no trail to stop {next} in state {:03b}", c.state));
            path.remove(0);
            planned_in = c.state;
        }
        while path
            .first()
            .is_some_and(|p| (p[0] - c.pos[0]).abs() + (p[2] - c.pos[2]).abs() < 0.06)
        {
            path.remove(0);
        }
        let goal = path.first().copied().unwrap_or(c.pos);
        c.frame(&mut w, &mut seat, ms, goal);
        assert!(ms < t0 + 900_000, "the tour takes a few minutes");
    }
    eprintln!(
        "walker {error_ms} ms off: {} switches, {} stops reached, {} riders carried, {} grace accepts, {} corrections",
        w.metrics.stairs_switches.get() - switched,
        reached,
        w.metrics.stairs_riders_carried.get(),
        w.metrics.stairs_grace_accepts.get(),
        c.corrections
    );
    assert_eq!(c.corrections, 0);
}

/// Flight 6-7 has swung to B: a walker on the sixth floor's east gallery
/// heading west for its A foot stops at the gallery's edge (its own
/// `move_and_slide`, never corrected), and a move past the edge, over the
/// well, is corrected back.
#[test]
fn a_walker_stops_at_a_swung_away_landing() {
    let probe = World3c::new(1_000_000, None);
    let to_b = probe.switch(F67, StairPosition::B, 1_000_000);
    let t0 = to_b + 5_000;
    let mut w = World3c::new(t0, None);
    assert_eq!(w.s.stair_state() >> F67 & 1, 1, "flight 6-7 at B");
    // On the gallery, facing the A foot at x -37.75 (z -9.5).
    let start = [-34.0, 54.0, -9.5];
    let mut seat = w.join(30, House::Serpentyne, start);
    let mut c = Honest::new(&w, House::Serpentyne, 0, start, t0);
    let mut ms = t0;
    for _ in 0..40 {
        ms += 100;
        c.frame(&mut w, &mut seat, ms, [-45.0, 54.0, -9.5]);
    }
    assert_eq!(c.corrections, 0);
    let stood = w.s.position(seat.id).unwrap();
    assert!(
        stood[0] >= -37.75 - 0.01 && (stood[1] - 54.0).abs() < 1e-3,
        "stopped on the gallery: {stood:?}"
    );
    assert!(near(stood, c.pos, 1e-3));
    // A forged step over the edge onto where the flight was.
    w.s.input(
        seat.id,
        w.at(ms + 500),
        Input::Move {
            seq: 1_000,
            pos: [stood[0] - 0.4, 54.1, stood[2]],
            yaw: 0.0,
            anim: Anim::Walk,
        },
    );
    let c = seat.corrections();
    assert_eq!(c.len(), 1, "corrected");
    assert!(near(c[0], stood, 0.02), "back on the gallery: {c:?}");
    assert_eq!(w.s.position(seat.id).unwrap(), stood);
    // With the flight back at A the same step is the first tread.
    let to_a = w.switch(F67, StairPosition::A, ms);
    let mut t = ms + 500;
    while t < to_a + 2_000 {
        t += 50;
        w.s.tick(w.at(t));
    }
    assert_eq!(w.s.stair_state() >> F67 & 1, 0);
    let world = w.s_world(House::Serpentyne, w.s.stair_state());
    let step = move_and_slide(&world, &MoveParams::default(), stood, [-0.4, 0.0]);
    assert!(step[0] < stood[0] - 0.3, "onto the flight: {step:?}");
    w.s.input(
        seat.id,
        w.at(to_a + 2_100),
        Input::Move {
            seq: 1_001,
            pos: step,
            yaw: 0.0,
            anim: Anim::Walk,
        },
    );
    assert!(seat.corrections().is_empty());
    assert!(near(w.s.position(seat.id).unwrap(), step, 1e-3));
}

impl World3c {
    /// `house`'s castle in staircase state `state`, as the client builds it.
    fn s_world(&self, house: House, state: u8) -> Arc<World> {
        Arc::new(World::new(self.all.layout().for_house(house).for_stairs(state)).unwrap())
    }
}

/// A position saved on a flight's other position (the flight has swung
/// since) resumes on the same spot of where the flight is now.
#[test]
fn a_saved_rider_resumes_on_the_flight() {
    let probe = World3c::new(1_000_000, None);
    let to_b = probe.switch(F67, StairPosition::B, 1_000_000);
    let mut w = World3c::new(to_b - 60_000, None);
    assert_eq!(w.s.stair_state() >> F67 & 1, 0, "flight 6-7 at A");
    let on_b = w.middle(F67, StairPosition::B);
    let seat = w.join(40, House::Roarwing, on_b);
    let want = w
        .stairs
        .carry(
            w.all.layout(),
            on_b,
            F67,
            StairPosition::B,
            StairPosition::A,
        )
        .unwrap();
    assert!(near(w.s.position(seat.id).unwrap(), want, 1e-3));
}

/// The Owl Loft's owl mail opens the mail panel on the client: the server
/// answers nothing and never fails it, near or far.
#[test]
fn the_owl_mail_is_answered_like_a_notice_board() {
    let mut w = World3c::new(1_000_000, None);
    let post = w.all.interactable("owl_mail").expect("the owl mail").pos;
    assert_eq!(
        w.all.interactable("owl_mail").unwrap().kind,
        sim_core::InteractableKind::OwlMail
    );
    let mut near_seat = w.join(50, House::Burrowsby, [post[0] + 1.0, post[1], post[2]]);
    let far = w.all.spawns()[0].pos;
    let mut far_seat = w.join(51, House::Serpentyne, far);
    for seat in [&mut near_seat, &mut far_seat] {
        while seat.rx.try_recv().is_ok() {}
        w.s.input(seat.id, w.at(1_000_500), Input::Interact("owl_mail".into()));
        let mut answers = Vec::new();
        while let Ok(b) = seat.rx.try_recv() {
            answers.push(decode_server(&b).unwrap());
        }
        assert!(answers.is_empty(), "{answers:?}");
    }
}

/// Within the grace before flight
/// 6-7 swings back to A, a forged step from the sixth-floor gallery onto
/// its incoming A position is refused (corrected back onto the gallery),
/// where before it was stored on the flight's current B position, across
/// the well. The same step once the switch has come is the first tread.
/// The honest clients above (300 ms early or late) are never corrected.
#[test]
fn a_reverse_ride_before_a_switch_is_refused() {
    let probe = World3c::new(1_000_000, None);
    let to_b = probe.switch(F67, StairPosition::B, 1_000_000);
    let t0 = to_b + 5_000;
    let mut w = World3c::new(t0, None);
    assert_eq!(w.s.stair_state() >> F67 & 1, 1, "flight 6-7 at B");
    let to_a = w.switch(F67, StairPosition::A, t0);
    // On the gallery by its west edge, where the A foot arrives (just off
    // the edge: a point on it would be carried onto the flight at sign-in).
    let edge = [-37.6, 54.0, -9.5];
    let mut seat = w.join(60, House::Serpentyne, edge);
    let mut t = t0;
    while t < to_a - 600 {
        t += 50;
        w.s.tick(w.at(t));
    }
    let _ = seat.corrections();
    assert_eq!(w.s.stair_state() >> F67 & 1, 1, "still at B");
    let stood = w.s.position(seat.id).unwrap();
    let a_world = w.s_world(House::Serpentyne, w.s.stair_state() & !(1 << F67));
    let step = move_and_slide(&a_world, &MoveParams::default(), stood, [-0.4, 0.0]);
    assert!(step[0] < -37.75 - 0.2, "onto the A foot: {step:?}");
    let refused = w.metrics.stairs_reverse_refused.get();
    w.s.input(
        seat.id,
        w.at(to_a - 500),
        Input::Move {
            seq: 1,
            pos: step,
            yaw: 0.0,
            anim: Anim::Walk,
        },
    );
    let c = seat.corrections();
    assert_eq!(c.len(), 1, "refused");
    assert!(near(c[0], stood, 1e-3), "back on the gallery: {c:?}");
    assert_eq!(w.s.position(seat.id).unwrap(), stood);
    assert_eq!(w.metrics.stairs_reverse_refused.get(), refused + 1);
    // After the switch the same step is walking onto the flight.
    let mut t = to_a - 500;
    while t < to_a + 300 {
        t += 50;
        w.s.tick(w.at(t));
    }
    assert_eq!(w.s.stair_state() >> F67 & 1, 0, "flight 6-7 at A");
    w.s.input(
        seat.id,
        w.at(to_a + 400),
        Input::Move {
            seq: 2,
            pos: step,
            yaw: 0.0,
            anim: Anim::Walk,
        },
    );
    assert!(seat.corrections().is_empty());
    assert!(near(w.s.position(seat.id).unwrap(), step, 1e-3));
}

/// Frames of an honest client from `from_ms` to `to_ms` (server time),
/// heading for `goal`.
fn frames(
    w: &mut World3c,
    seat: &mut Seat,
    c: &mut Honest,
    from_ms: i64,
    to_ms: i64,
    goal: [f32; 3],
) {
    let mut ms = from_ms;
    while ms < to_ms {
        ms += 100;
        c.frame(w, seat, ms, goal);
    }
}

/// Fix (a correction a load test found): a rider standing a
/// hair past a flight's end (within the 1 mm edge tolerance, where the
/// flight's slope continues just above the landing's floor, so `ground_at`
/// puts it on the flight) rides at the switch on every end: the client
/// carries itself, and so does the server. Before, the server's switch
/// skipped riders outside the flight's exact rectangle and the client's
/// next move, 14 m away, was `too_fast` in every candidate state. And the
/// carry keeps such a rider a hair past the other end, on the same slope,
/// so a late client's move from the old position is carried back by the
/// grace (before, it was pulled onto the edge, level with the landing
/// there, and not carried back). Clocks 300 ms early to 300 ms late.
#[test]
fn a_rider_a_hair_past_a_flight_end_rides_on_every_end() {
    const F56: usize = 1;
    let cases: [(usize, StairPosition, [f32; 3], [f32; 3]); 2] = [
        // Flight 5-6 at B, 0.35 mm past its corbel end; off north onto the
        // sixth floor's landing once it is back at A.
        (
            F56,
            StairPosition::B,
            [-56.000_35, 54.0, 2.0],
            [-46.0, 54.0, -9.75],
        ),
        // Flight 6-7 at A, 0.7 mm past its top onto the seventh floor's
        // landing; up onto the balcony once it is at B.
        (
            F67,
            StairPosition::A,
            [-53.750_713, 63.0, -9.511_744],
            [-39.75, 63.0, 6.0],
        ),
    ];
    let mut carried = 0;
    for (k, (flight, at, p, goal)) in cases.into_iter().enumerate() {
        for (j, error_ms) in [-300i64, -76, 0, 150, 300].into_iter().enumerate() {
            let probe = World3c::new(1_000_000, Some(20));
            let arrive = probe.switch(flight, at, 1_000_000);
            let mut w = World3c::new(arrive + 5_000, Some(20));
            let leave = w.switch(flight, at.other(), arrive + 5_000);
            let house = House::ALL[j % 4];
            let world = w.s_world(house, w.s.stair_state());
            // Walked there (`move_and_slide` steps onto the highest ground).
            let params = MoveParams::default();
            let y = world
                .step_ground(p[0], p[2], p[1], params.step_height)
                .expect("ground")
                .y;
            let g = world.ground_at(p[0], p[2], y).expect("ground");
            let surface = &world.surface(g.surface).id;
            let def = &w.stairs.flights()[flight];
            let want = if at == StairPosition::A {
                &def.a
            } else {
                &def.b
            };
            assert_eq!(surface, want, "a hair past the end is on the flight");
            assert!(g.y > p[1], "the slope continues above the landing");
            let start = [p[0], g.y, p[2]];
            let mut seat = w.join(70 + (k * 10 + j) as i64, house, start);
            assert_eq!(w.s.position(seat.id).unwrap(), start);
            let t0 = leave - 1_500;
            let mut c = Honest::new(&w, house, error_ms, start, t0);
            c.speed = c.params.run_speed;
            frames(&mut w, &mut seat, &mut c, t0, leave + 2_500, goal);
            assert_eq!(
                c.corrections, 0,
                "flight {flight} {error_ms} ms off: corrected"
            );
            // The server's switch carried it, or (the client's clock at or
            // past the switch) the grace took its move from the new spot.
            carried += w.metrics.stairs_riders_carried.get();
            let server = w.s.position(seat.id).unwrap();
            assert!(
                near(server, c.pos, 1e-3),
                "flight {flight} {error_ms} ms off: server {server:?}, client {:?}",
                c.pos
            );
            assert!(
                (c.pos[0] - start[0]).abs() + (c.pos[2] - start[2]).abs() > 3.0,
                "walked on from where it was carried: {:?}",
                c.pos
            );
        }
    }
    assert!(carried >= 4, "the server's switch carried the late ones");
}

/// Fix: a client whose clock runs early
/// steps from the sixth floor's north gallery onto flight 6-7's arriving
/// B foot as soon as its own clock says the flight has arrived, at run
/// speed: up to 400 ms before the server's switch. That step is held where
/// it stood (neither stored across the well nor corrected) until the
/// switch, and its next move, from the gallery onto the arrived flight, is
/// accepted. Never corrected, whatever the frames' phase against the
/// switch; the server agrees where it stands. (A step earlier than that
/// is still refused: `a_reverse_ride_before_a_switch_is_refused`.)
#[test]
fn an_early_client_steps_onto_an_arriving_flight_uncorrected() {
    let mut holds = 0;
    for (k, error_ms) in [300i64, 150, 0, -300].into_iter().enumerate() {
        for (j, phase) in [0i64, 25, 50, 75].into_iter().enumerate() {
            let probe = World3c::new(1_000_000, Some(20));
            let to_a = probe.switch(F67, StairPosition::A, 1_000_000);
            let mut w = World3c::new(to_a + 5_000, Some(20));
            let to_b = w.switch(F67, StairPosition::B, to_a + 5_000);
            assert_eq!(w.s.stair_state() >> F67 & 1, 0, "flight 6-7 at A");
            // On the gallery by B's foot (z -11.5), heading up onto it.
            let start = [-39.75, 54.0, -12.0];
            let goal = [-39.75, 54.0, -9.0];
            let house = House::ALL[k % 4];
            let mut seat = w.join(90 + (k * 4 + j) as i64, house, start);
            let t0 = to_b - 2_000 + phase;
            let mut c = Honest::new(&w, house, error_ms, start, t0);
            c.speed = c.params.run_speed;
            frames(&mut w, &mut seat, &mut c, t0, to_b + 2_000, goal);
            assert_eq!(
                c.corrections, 0,
                "{error_ms} ms off, phase {phase}: corrected"
            );
            assert_eq!(w.metrics.stairs_reverse_refused.get(), 0);
            let server = w.s.position(seat.id).unwrap();
            assert!(
                near(server, c.pos, 1e-3),
                "{error_ms} ms off, phase {phase}: server {server:?}, client {:?}",
                c.pos
            );
            assert!(
                c.pos[2] > -10.0 && c.pos[1] > 54.5,
                "up the flight: {:?}",
                c.pos
            );
            holds += w.metrics.stairs_early_holds.get();
        }
    }
    assert!(holds > 0, "an early client's step was held at least once");
}
