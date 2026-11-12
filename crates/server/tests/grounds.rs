//! The hilly grounds through the server's world. Moves on the
//! heightfield grounds are checked by the same `sim_core` code the client
//! predicts with, so an honest client walking the grounds tour (out of the
//! Entrance Hall through the courtyard gate, down the hill to the Lake
//! Shore, along it to the Boathouse, up to the Groundskeeper's Cottage, the paddock gate,
//! the stone circle and the greenhouse gate, and back) is never corrected,
//! while a step into the lake is. Saved positions that no longer stand on
//! walkable ground (the old greybox grounds, the lake, a building's
//! footprint) resume at a spawn. No database: the world alone.

use std::sync::Arc;
use std::time::{Duration, Instant};

use bytes::Bytes;
use protocol::{Anim, House, ServerMsg, decode_server};
use server::characters::{Character, SavedZone};
use server::metrics::Registry;
use server::realtime::ConnControl;
use server::realtime::chat::WordFilter;
use server::realtime::metrics::RealtimeMetrics;
use server::realtime::world::{Input, Joining, TICK, WorldSim};
use sim_core::{MoveParams, World, move_and_slide};
use tokio::sync::mpsc;

const LAYOUT: &str = include_str!("../../../data/world/layout.json");

/// How often the client sends a move while walking (as the browser does).
const MOVE_EVERY: Duration = Duration::from_millis(100);

fn castle() -> Arc<World> {
    Arc::new(World::from_json(LAYOUT).expect("the shipped layout loads"))
}

fn sim(world: Arc<World>) -> WorldSim {
    WorldSim::new(
        world,
        WordFilter::from_list(""),
        None,
        Arc::new(RealtimeMetrics::register(&Registry::default())),
        1,
    )
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

fn join(s: &mut WorldSim, id: i64, house: House, last_pos: Option<[f32; 3]>) -> Seat {
    let character = Character {
        id,
        name: format!("Walker{id}"),
        house,
        zone: SavedZone::Castle,
        last_pos,
        appearance_version: 0,
        appearance: protocol::Appearance::default(),
    };
    let (tx, rx) = mpsc::channel(1 << 16);
    let id = s
        .join(
            Joining::alone(character),
            tx,
            Arc::new(ConnControl::default()),
            Instant::now(),
        )
        .expect("joined");
    Seat { id, rx }
}

fn landmark(world: &World, id: &str) -> [f32; 3] {
    world
        .layout()
        .landmarks
        .iter()
        .find(|l| l.id == id)
        .unwrap_or_else(|| panic!("landmark {id}"))
        .pos
}

fn interactable(world: &World, id: &str) -> [f32; 3] {
    world
        .interactable(id)
        .unwrap_or_else(|| panic!("interactable {id}"))
        .pos
}

/// The grounds tour, from the Entrance Hall's first spawn and back.
fn tour(world: &World) -> Vec<(&'static str, [f32; 3])> {
    vec![
        ("the Lake Shore", landmark(world, "lake_shore")),
        ("the Boathouse", landmark(world, "boathouse")),
        ("the Lake Shore again", landmark(world, "lake_shore")),
        (
            "Groundskeeper's Cottage",
            landmark(world, "groundskeepers_cottage"),
        ),
        ("the paddock gate", interactable(world, "paddock_gate")),
        ("the stone circle", landmark(world, "stone_circle")),
        (
            "the greenhouse gate",
            interactable(world, "greenhouse_gate"),
        ),
        ("the front steps", world.spawns()[0].pos),
    ]
}

/// An honest client: follows the trail's path (`sim_core::nav`, the
/// house's own world) at run speed with `move_and_slide`, sends a move
/// every 100 ms while the world ticks every 50 ms, and checks after each
/// move that the server agrees where it stands.
struct Client {
    s: WorldSim,
    seat: Seat,
    nav: sim_core::nav::Nav,
    params: MoveParams,
    pos: [f32; 3],
    at: Instant,
    seq: u32,
}

impl Client {
    fn new(house: House, character: i64) -> Client {
        let world = castle();
        let mut s = sim(world.clone());
        let seat = join(&mut s, character, house, None);
        let house_world = Arc::new(world.for_house(house).unwrap());
        let pos = s.position(seat.id).unwrap();
        Client {
            s,
            seat,
            nav: sim_core::nav::Nav::new(house_world, &[], |_| true),
            params: MoveParams::default(),
            pos,
            at: Instant::now(),
            seq: 0,
        }
    }

    /// Walks the trail to `to`; returns the metres walked.
    fn walk_to(&mut self, to: [f32; 3], name: &str) -> f32 {
        let path = self
            .nav
            .path(self.pos, to)
            .unwrap_or_else(|| panic!("no trail from {:?} to {name} {to:?}", self.pos));
        let mut walked = 0.0;
        let step = self.params.run_speed * MOVE_EVERY.as_secs_f32();
        for w in path.iter().skip(1) {
            let mut stalled = 0;
            loop {
                let pos = self.pos;
                let (dx, dz) = (w[0] - pos[0], w[2] - pos[2]);
                let d = (dx * dx + dz * dz).sqrt();
                if d < 0.05 {
                    break;
                }
                let len = step.min(d);
                let next = move_and_slide(
                    self.nav.world(),
                    &self.params,
                    pos,
                    [dx / d * len, dz / d * len],
                );
                let moved = ((next[0] - pos[0]).powi(2) + (next[2] - pos[2]).powi(2)).sqrt();
                walked += moved;
                stalled = if moved < len * 0.5 { stalled + 1 } else { 0 };
                assert!(stalled < 20, "stuck at {pos:?} on the way to {name}");
                self.pos = next;
                self.at += MOVE_EVERY;
                self.seq += 1;
                self.s.input(
                    self.seat.id,
                    self.at,
                    Input::Move {
                        seq: self.seq,
                        pos: next,
                        yaw: dx.atan2(dz),
                        anim: Anim::Run,
                    },
                );
                self.s.tick(self.at - TICK);
                self.s.tick(self.at);
                let corrected = self.seat.corrections();
                assert!(
                    corrected.is_empty(),
                    "corrected on the way to {name} at {next:?}: {corrected:?}"
                );
                let server = self.s.position(self.seat.id).expect("in the world");
                assert!(
                    (0..3).all(|k| (server[k] - next[k]).abs() < 1e-3),
                    "the server has {server:?}, the client {next:?} (to {name})"
                );
                // Feet on the ground: the client's height is the ground's.
                let g = self
                    .nav
                    .world()
                    .ground_at(next[0], next[2], next[1])
                    .expect("on the ground");
                assert!((g.y - next[1]).abs() < 1e-3, "{next:?} vs ground {}", g.y);
            }
        }
        walked
    }
}

/// The whole tour for one house, never corrected; returns the metres
/// walked and the lowest height stood on.
fn walk_the_tour(house: House, character: i64) -> (f32, f32) {
    let mut c = Client::new(house, character);
    let world = castle();
    let mut walked = 0.0;
    let mut lowest = f32::MAX;
    for (name, to) in tour(&world) {
        walked += c.walk_to(to, name);
        let pos = c.pos;
        lowest = lowest.min(pos[1]);
        let off = ((pos[0] - to[0]).powi(2) + (pos[2] - to[2]).powi(2)).sqrt();
        assert!(off < 0.1, "{name}: stopped {off} m short at {pos:?}");
    }
    (walked, lowest)
}

#[test]
fn an_honest_walk_round_the_grounds_is_never_corrected() {
    let (walked, lowest) = walk_the_tour(House::Roarwing, 1);
    // Out to the lake and the boathouse, along the shore, up the hill and
    // back (about 810 m), down to the shallows.
    assert!(walked > 700.0, "walked {walked} m");
    let water = -15.0;
    assert!(
        lowest < water + 0.5 && lowest >= water - 0.3 - 1e-3,
        "lowest {lowest}"
    );
}

#[test]
fn every_house_walks_the_grounds_tour_uncorrected() {
    for (i, house) in House::ALL.into_iter().enumerate().skip(1) {
        walk_the_tour(house, 10 + i as i64);
    }
}

/// A step from the shallows into deeper water is refused: the server puts
/// the character back on the shore.
#[test]
fn walking_into_the_lake_is_corrected() {
    let world = castle();
    let mut s = sim(world.clone());
    let shore = landmark(&world, "lake_shore");
    let mut seat = join(&mut s, 2, House::Burrowsby, Some(shore));
    let start = s.position(seat.id).unwrap();
    assert!((start[0] - shore[0]).abs() < 1e-3 && (start[2] - shore[2]).abs() < 1e-3);
    // Straight out into the lake until the ground ends.
    let mut wet = start;
    while world.ground_at(wet[0], wet[2], wet[1]).is_some() {
        wet[2] += 0.25;
        if let Some(g) = world.ground_at(wet[0], wet[2], wet[1]) {
            wet[1] = g.y;
        }
        assert!(wet[2] < start[2] + 40.0, "the shore never ends");
    }
    // Within a second's run of the shore, so only the water refuses it.
    assert!(wet[2] - start[2] < 6.0, "{wet:?}");
    let at = Instant::now() + Duration::from_secs(2);
    s.tick(at - TICK);
    s.input(
        seat.id,
        at,
        Input::Move {
            seq: 1,
            pos: wet,
            yaw: 0.0,
            anim: Anim::Walk,
        },
    );
    s.tick(at);
    let corrected = seat.corrections();
    assert_eq!(corrected.len(), 1, "{corrected:?}");
    assert_eq!(s.position(seat.id), Some(start));
}

/// Saved positions from before the grounds were re-laid: off the new ground
/// (the old greybox shore, now the pitch terrace's bank 3 m below; the lake;
/// the Groundskeeper's Cottage's footprint), they resume at a spawn in the Entrance Hall;
/// on it, where they were.
#[test]
fn saved_positions_off_the_new_ground_resume_at_a_spawn() {
    let world = castle();
    let mut s = sim(world.clone());
    let spawns: Vec<[f32; 3]> = world.spawns().iter().map(|p| p.pos).collect();
    for (i, old) in [
        // The 120 x 120 m greybox's lowest row, sloping to y -14 by z 212.
        [-30.0, -13.0, 210.0],
        // Out in the lake.
        [0.0, -15.0, 280.0],
        // Inside the Groundskeeper's Cottage.
        [99.0, -6.5, 177.0],
        // Inside the boathouse, over the water.
        [-100.0, -15.0, 203.0],
    ]
    .into_iter()
    .enumerate()
    {
        assert!(
            world.ground_at(old[0], old[2], old[1]).is_none(),
            "{old:?} is on the ground"
        );
        let seat = join(&mut s, 100 + i as i64, House::Skytalon, Some(old));
        let p = s.position(seat.id).unwrap();
        let g = world.ground_at(p[0], p[2], p[1]).expect("on the ground");
        // The spawns are on the courtyard paving outside the
        // front doors.
        assert_eq!(
            world.surface(g.surface).cell,
            "courtyard",
            "{old:?} -> {p:?}"
        );
        assert!(
            spawns
                .iter()
                .any(|sp| (sp[0] - p[0]).hypot(sp[2] - p[2]) < 8.0),
            "{old:?} -> {p:?}"
        );
    }
    // On the new ground: stays put (the hill's height there).
    let hut = landmark(&world, "groundskeepers_cottage");
    let seat = join(&mut s, 200, House::Serpentyne, Some(hut));
    let p = s.position(seat.id).unwrap();
    assert!((p[0] - hut[0]).abs() < 1e-3 && (p[2] - hut[2]).abs() < 1e-3);
    assert!((p[1] - hut[1]).abs() < 0.02, "{p:?} vs {hut:?}");
}
