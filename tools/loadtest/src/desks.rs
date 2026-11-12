//! Where students sit in a class room. A student walks from the
//! room's entry to a desk seat and sits (the `sit` emote), as a player
//! would, instead of standing by the door for the whole lesson.
//!
//! The seats are the class room's `seats` (`[{ pos, yaw }]`: the hips'
//! floor point and the facing, the rows `castle/seats.ts` seats players on)
//! from the generated instance manifest,
//! `client/public/assets/instances/manifest.json` (not in git: the asset
//! export writes it). A room the manifest gives no seats (the Potions
//! classroom stands at its cauldrons), or a run without the generated
//! assets, gets standing spots instead: a grid across the room between the
//! entry and the professor's desk.
//!
//! Standing spots keep out of what the layout does not know
//! stands in the room ([`keep_outs`]: the paddock's creature pen), and a student's straight walk to its spot never crosses it.
//!
//! A class room is one open floor (the Charms room's tiers are 0.3 m
//! steps), walked with `sim-core`'s `move_and_slide` over the room's own
//! layout, exactly as the server checks it ([`Stroll`]). Solid furniture
//! (docs/architecture.md "Layouts"): the room's desks,
//! benches and tables are solid, so a student walks a way round them found
//! on a grid from each entry ([`Ways`]: the castle's navigation graph does
//! not join surfaces a whole step apart), and a seat it sits at is reached
//! from the nearest spot beside it a character fits on, within the client's
//! sitting reach ([`SEAT_STAND`]). Only places such a walk reaches from
//! every entry are kept.

use std::collections::HashMap;

use sim_core::{MoveParams, World, footprint_ground, move_and_slide, stand_near};

/// A student stands at most this far from a seat's hips mark to sit on it
/// (metres): inside the client's sitting reach (`castle/seats.ts`
/// `SEAT_REACH`, 0.6 m to the seat's rectangle).
const SEAT_STAND: f32 = 0.55;
/// Spacing of the grid the ways round the furniture are found on (metres).
const WAY_STEP: f32 = 0.2;

/// The generated instance manifest (read at run time; optional).
const MANIFEST: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../client/public/assets/instances/manifest.json"
);

/// Grid step (m) and side margin of the standing spots of a room with no seats.
const GRID_STEP: f32 = 1.5;
const GRID_MARGIN: f32 = 1.5;
/// Standing spots keep this far from the entry and from the professor.
const GRID_KEEP_BACK: f32 = 2.5;
/// Close enough to the place (m).
const ARRIVE: f32 = 0.05;
/// A walk that gains less than this per step is stuck.
const STUCK_STEP: f32 = 1e-3;

/// Rectangles (`[x0, z0, x1, z1]`, with a margin) of a class
/// room's floor that students never stand in or walk through: the
/// paddock's creature pen (`tools/blender/world/school_rooms_paddock.py`
/// `PEN`, x -5..5, z -5..-1.2, its fence art only), 0.5 m round it.
pub fn keep_outs(layout: &str) -> &'static [[f32; 4]] {
    match layout {
        "class_paddock" => &[[-5.5, -5.5, 5.5, -0.7]],
        _ => &[],
    }
}

fn in_keep_out(layout: &str, x: f32, z: f32) -> bool {
    keep_outs(layout)
        .iter()
        .any(|r| x >= r[0] && x <= r[2] && z >= r[1] && z <= r[3])
}

/// A class room's layout, compiled in (the same files as the server's
/// `realtime/layouts.rs`).
pub fn layout_json(layout: &str) -> Option<&'static str> {
    Some(match layout {
        "class_charms" => include_str!("../../../data/world/instances/class_charms.json"),
        "class_potions" => include_str!("../../../data/world/instances/class_potions.json"),
        "class_classroom" => {
            include_str!("../../../data/world/instances/class_classroom.json")
        }
        "class_defence" => include_str!("../../../data/world/instances/class_defence.json"),
        "class_divination" => {
            include_str!("../../../data/world/instances/class_divination.json")
        }
        "class_astronomy" => {
            include_str!("../../../data/world/instances/class_astronomy.json")
        }
        "class_greenhouse" => {
            include_str!("../../../data/world/instances/class_greenhouse.json")
        }
        "class_paddock" => include_str!("../../../data/world/instances/class_paddock.json"),
        "class_pitch" => include_str!("../../../data/world/instances/class_pitch.json"),
        _ => return None,
    })
}

/// The generated manifest, if the assets were exported.
pub fn load_manifest() -> Option<serde_json::Value> {
    let text = std::fs::read_to_string(MANIFEST).ok()?;
    serde_json::from_str(&text).ok()
}

/// One place a student goes: where, which way it faces.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Desk {
    pub pos: [f32; 3],
    pub yaw: f32,
}

/// A class room: its layout and its students' places.
pub struct Room {
    pub layout: String,
    pub world: World,
    pub params: MoveParams,
    pub desks: Vec<Desk>,
    /// Desk seats (sit there) or standing spots (stand there).
    pub seated: bool,
    /// The ways round the furniture from each entry (the room's spawns, in order).
    ways: Vec<Ways>,
}

impl Room {
    /// The room of layout `layout`, its seats from `manifest` if it has any.
    pub fn new(layout: &str, manifest: Option<&serde_json::Value>) -> Result<Room, String> {
        let json = layout_json(layout).ok_or_else(|| format!("no class layout {layout}"))?;
        let world = World::from_json(json).map_err(|e| format!("{layout}: {e}"))?;
        let params = MoveParams::default();
        let seats: Vec<Desk> = manifest
            .and_then(|m| m["instances"][layout]["seats"].as_array())
            .map(|rows| {
                rows.iter()
                    .filter_map(|s| {
                        let p = s["pos"].as_array()?;
                        let f = |i: usize| p.get(i)?.as_f64().map(|v| v as f32);
                        Some(Desk {
                            pos: [f(0)?, f(1)?, f(2)?],
                            yaw: s["yaw"].as_f64().unwrap_or(0.0) as f32,
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        let ways = world
            .spawns()
            .iter()
            .map(|s| Ways::from(&world, &params, s.pos))
            .collect();
        let mut room = Room {
            layout: layout.to_owned(),
            world,
            params,
            desks: Vec::new(),
            seated: true,
            ways,
        };
        // Solid furniture: a seat on a bench or chair is sat on from beside it.
        let seats: Vec<Desk> = seats
            .into_iter()
            .filter_map(|d| {
                let at = stand_near(&room.world, &room.params, d.pos, SEAT_STAND)?;
                Some(Desk {
                    pos: at,
                    yaw: d.yaw,
                })
            })
            .collect();
        room.desks = seats.into_iter().filter(|d| room.reachable(d)).collect();
        if room.desks.is_empty() {
            room.seated = false;
            room.desks = room
                .standing_spots()
                .into_iter()
                .filter(|d| room.reachable(d))
                .collect();
        }
        if room.desks.is_empty() {
            return Err(format!("{layout}: no reachable place for students"));
        }
        Ok(room)
    }

    /// The place of the `k`th student of a section.
    pub fn desk(&self, k: usize) -> Desk {
        self.desks[k % self.desks.len()]
    }

    /// A student's walk from `from` (an entry) to `to`: round the furniture
    /// by the way found from the entry nearest `from`, straight when there
    /// is none.
    pub fn stroll(&self, from: [f32; 3], to: [f32; 2]) -> Stroll {
        let ways = self
            .world
            .spawns()
            .iter()
            .zip(&self.ways)
            .min_by(|(a, _), (b, _)| {
                let d = |p: [f32; 3]| (p[0] - from[0]).hypot(p[2] - from[2]);
                d(a.pos).total_cmp(&d(b.pos))
            })
            .map(|(_, w)| w);
        let path = ways
            .and_then(|w| w.path(&self.world, &self.params, to))
            .unwrap_or_else(|| vec![to]);
        Stroll::along(from, path)
    }

    /// Whether a walk from every entry reaches `d` (and never crosses a
    /// keep-out on the way).
    fn reachable(&self, d: &Desk) -> bool {
        self.world.spawns().iter().all(|s| {
            let mut walk = self.stroll(s.pos, [d.pos[0], d.pos[2]]);
            let mut prev = walk.pos;
            for _ in 0..1200 {
                walk.step(self, 0.05);
                if in_keep_out(&self.layout, walk.pos[0], walk.pos[2])
                    || in_keep_out(
                        &self.layout,
                        (prev[0] + walk.pos[0]) / 2.0,
                        (prev[2] + walk.pos[2]) / 2.0,
                    )
                {
                    return false;
                }
                prev = walk.pos;
                if !walk.walking() {
                    break;
                }
            }
            walk.arrived()
        })
    }

    /// A grid of standing spots across the room, between the entry (the
    /// spawns) and the professor's desk, facing the professor.
    fn standing_spots(&self) -> Vec<Desk> {
        let world = &self.world;
        let entry_z = world
            .spawns()
            .iter()
            .map(|s| s.pos[2])
            .fold(f32::NEG_INFINITY, f32::max);
        let Some(front) = world
            .layout()
            .landmarks
            .iter()
            .find(|l| l.id == "class_professor")
            .map(|l| l.pos)
        else {
            return Vec::new();
        };
        let (mut x0, mut x1) = (f32::INFINITY, f32::NEG_INFINITY);
        for s in world.surfaces() {
            x0 = x0.min(s.min[0]);
            x1 = x1.max(s.max[0]);
        }
        let mut out = Vec::new();
        let mut z = front[2] + GRID_KEEP_BACK;
        while z <= entry_z - GRID_KEEP_BACK {
            let mut x = x0 + GRID_MARGIN;
            while x <= x1 - GRID_MARGIN {
                // Solid furniture: beside a piece that stands on the grid point.
                if let Some(p) = stand_near(world, &self.params, [x, front[1], z], 0.6) {
                    // Yaw 0 faces +Z; the professor is towards -Z.
                    let yaw = (front[0] - p[0]).atan2(front[2] - p[2]);
                    out.push(Desk { pos: p, yaw });
                }
                x += GRID_STEP;
            }
            z += GRID_STEP;
        }
        out
    }
}

/// A walk in a straight line to a place in the room, at walking speed,
/// stepped with `move_and_slide` (so every position is one the server
/// accepts). It ends on arrival or when it stops gaining ground.
#[derive(Debug, Clone)]
pub struct Stroll {
    pub pos: [f32; 3],
    pub yaw: f32,
    to: [f32; 2],
    /// The waypoints after `to`, in order (a way round the furniture).
    then: Vec<[f32; 2]>,
    state: StrollState,
}

/// The ways a character walks from one entry of a room to any place in it:
/// a flood over a [`WAY_STEP`] grid with the same footprint test as walking
/// (so round the solid furniture), each point remembering the one it was
/// reached from.
#[derive(Debug, Clone)]
struct Ways {
    origin: [f32; 2],
    /// (column, row) -> (the point it was reached from, its ground height).
    from: HashMap<(i32, i32), ((i32, i32), f32)>,
}

impl Ways {
    fn from(world: &World, params: &MoveParams, entry: [f32; 3]) -> Ways {
        let mut ways = Ways {
            origin: [entry[0], entry[2]],
            from: HashMap::new(),
        };
        let Some(start) = stand_near(world, params, entry, 1.0) else {
            return ways;
        };
        ways.origin = [start[0], start[2]];
        ways.from.insert((0, 0), ((0, 0), start[1]));
        let mut todo = std::collections::VecDeque::from([(0, 0)]);
        while let Some(c) = todo.pop_front() {
            let y = ways.from[&c].1;
            let [x, z] = ways.at(c);
            for (di, dk) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
                let n = (c.0 + di, c.1 + dk);
                if ways.from.contains_key(&n) {
                    continue;
                }
                let [nx, nz] = ways.at(n);
                let Some(mid) =
                    world.step_ground((x + nx) / 2.0, (z + nz) / 2.0, y, params.step_height)
                else {
                    continue;
                };
                if let Some(ny) = footprint_ground(world, params, nx, nz, mid.y) {
                    ways.from.insert(n, (c, ny));
                    todo.push_back(n);
                }
            }
        }
        ways
    }

    fn at(&self, c: (i32, i32)) -> [f32; 2] {
        [
            self.origin[0] + c.0 as f32 * WAY_STEP,
            self.origin[1] + c.1 as f32 * WAY_STEP,
        ]
    }

    /// Waypoints from the entry to `to` (ending with it), cut short where a
    /// straight walk does: `None` when no reached point lies within a step.
    fn path(&self, world: &World, params: &MoveParams, to: [f32; 2]) -> Option<Vec<[f32; 2]>> {
        let ci = ((to[0] - self.origin[0]) / WAY_STEP).round() as i32;
        let ck = ((to[1] - self.origin[1]) / WAY_STEP).round() as i32;
        let end = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
            .into_iter()
            .map(|(a, b)| (ci + a, ck + b))
            .filter(|c| self.from.contains_key(c))
            .min_by(|a, b| {
                let d = |c: &(i32, i32)| {
                    let p = self.at(*c);
                    (p[0] - to[0]).hypot(p[1] - to[1])
                };
                d(a).total_cmp(&d(b))
            })?;
        let mut cells = vec![end];
        let mut c = end;
        while c != (0, 0) {
            c = self.from[&c].0;
            cells.push(c);
        }
        cells.reverse();
        let points: Vec<([f32; 2], f32)> = cells
            .iter()
            .map(|c| (self.at(*c), self.from[c].1))
            .collect();
        // Keep a waypoint only where the straight walk from the last one kept
        // would leave the ground a character fits on.
        let clear = |a: ([f32; 2], f32), b: [f32; 2]| {
            let n = ((b[0] - a.0[0]).hypot(b[1] - a.0[1]) / 0.1).ceil().max(1.0) as usize;
            let mut y = a.1;
            (1..=n).all(|k| {
                let t = k as f32 / n as f32;
                let (x, z) = (a.0[0] + (b[0] - a.0[0]) * t, a.0[1] + (b[1] - a.0[1]) * t);
                match footprint_ground(world, params, x, z, y) {
                    Some(ny) => {
                        y = ny;
                        true
                    }
                    None => false,
                }
            })
        };
        let mut out = Vec::new();
        let mut at = points[0];
        let mut i = 0;
        while i + 1 < points.len() {
            let mut j = i + 1;
            while j + 1 < points.len() && clear(at, points[j + 1].0) {
                j += 1;
            }
            out.push(points[j].0);
            at = points[j];
            i = j;
        }
        out.push(to);
        Some(out)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StrollState {
    Walking,
    Arrived,
    Stuck,
}

impl Stroll {
    /// A walk through `path`'s waypoints in turn (the last is the place).
    pub fn along(from: [f32; 3], mut path: Vec<[f32; 2]>) -> Stroll {
        path.reverse();
        let to = path.pop().unwrap_or([from[0], from[2]]);
        Stroll {
            pos: from,
            yaw: 0.0,
            to,
            then: path,
            state: StrollState::Walking,
        }
    }

    pub fn walking(&self) -> bool {
        self.state == StrollState::Walking
    }

    pub fn arrived(&self) -> bool {
        self.state == StrollState::Arrived
    }

    /// Advances `dt` seconds.
    pub fn step(&mut self, room: &Room, dt: f32) {
        if !self.walking() {
            return;
        }
        let (dx, dz) = (self.to[0] - self.pos[0], self.to[1] - self.pos[2]);
        let d = (dx * dx + dz * dz).sqrt();
        if d <= ARRIVE {
            match self.then.pop() {
                Some(next) => self.to = next,
                None => self.state = StrollState::Arrived,
            }
            return;
        }
        let len = (room.params.walk_speed * dt.clamp(0.0, 0.25)).min(d);
        let next = move_and_slide(
            &room.world,
            &room.params,
            self.pos,
            [dx / d * len, dz / d * len],
        );
        let gained = d - ((self.to[0] - next[0]).powi(2) + (self.to[1] - next[2]).powi(2)).sqrt();
        self.pos = next;
        // Yaw 0 faces +Z; forward is (sin yaw, cos yaw).
        self.yaw = dx.atan2(dz);
        if gained < STUCK_STEP && len > STUCK_STEP {
            self.state = StrollState::Stuck;
        } else if d - gained <= ARRIVE {
            match self.then.pop() {
                Some(next) => self.to = next,
                None => self.state = StrollState::Arrived,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LAYOUTS: [&str; 9] = [
        "class_charms",
        "class_potions",
        "class_classroom",
        "class_defence",
        "class_divination",
        "class_astronomy",
        "class_greenhouse",
        "class_paddock",
        "class_pitch",
    ];

    #[test]
    fn every_class_room_has_a_place_for_a_section() {
        let manifest = load_manifest();
        for layout in LAYOUTS {
            for m in [manifest.as_ref(), None] {
                let room = Room::new(layout, m).unwrap();
                assert!(
                    room.desks.len() >= 20,
                    "{layout}: {} places (manifest: {})",
                    room.desks.len(),
                    m.is_some()
                );
            }
        }
    }

    #[test]
    fn students_walk_from_either_entry_to_their_place_on_legal_ground() {
        let manifest = load_manifest();
        for layout in LAYOUTS {
            let room = Room::new(layout, manifest.as_ref()).unwrap();
            for spawn in room.world.spawns() {
                for d in &room.desks {
                    let mut walk = room.stroll(spawn.pos, [d.pos[0], d.pos[2]]);
                    let mut prev = walk.pos;
                    while walk.walking() {
                        walk.step(&room, 1.0 / 15.0);
                        // Every step is one the server's own check accepts.
                        let check = sim_core::check_move(
                            &room.world,
                            &room.params,
                            prev,
                            walk.pos,
                            1.0 / 15.0,
                        );
                        assert!(
                            matches!(check, sim_core::MoveCheck::Accept { .. }),
                            "{layout}: {prev:?} -> {:?}: {check:?}",
                            walk.pos
                        );
                        prev = walk.pos;
                    }
                    assert!(walk.arrived(), "{layout}: {:?} to {:?}", spawn.pos, d.pos);
                    assert!(
                        (walk.pos[1] - d.pos[1]).abs() < 0.05,
                        "{layout}: {walk:?} {d:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn without_seats_a_room_gets_standing_spots_facing_the_professor() {
        let room = Room::new("class_potions", None).unwrap();
        assert!(!room.seated);
        for d in &room.desks {
            // Facing roughly -Z (towards the professor's desk).
            assert!(d.yaw.cos() < 0.0, "{d:?}");
            assert!(d.pos[2] < 5.5 - 2.0 && d.pos[2] > -6.0 + 2.0, "{d:?}");
        }
    }

    /// The paddock's students stand outside the
    /// creatures' pen and never walk through it.
    #[test]
    fn paddock_students_stand_outside_the_creature_pen() {
        let manifest = load_manifest();
        for m in [manifest.as_ref(), None] {
            let room = Room::new("class_paddock", m).unwrap();
            assert!(room.desks.len() >= 20);
            for d in &room.desks {
                assert!(!in_keep_out("class_paddock", d.pos[0], d.pos[2]), "{d:?}");
                // Not inside the pen itself, with room to spare.
                let pen = (-5.0..=5.0).contains(&d.pos[0]) && (-5.0..=-1.2).contains(&d.pos[2]);
                assert!(!pen, "{d:?}");
            }
        }
    }

    #[test]
    fn the_charms_seats_come_from_the_manifest() {
        let manifest = serde_json::json!({ "instances": { "class_charms": { "seats": [
            { "pos": [-1.95, 0.3, 1.52], "yaw": 3.0 },
            { "pos": [6.9, 0.9, 3.82], "yaw": 3.0 },
            { "pos": [40.0, 0.0, 0.0], "yaw": 0.0 }
        ] } } });
        let room = Room::new("class_charms", Some(&manifest)).unwrap();
        assert!(room.seated);
        assert_eq!(room.desks.len(), 2, "the one off the floor is dropped");
        assert_eq!(room.desk(3), room.desks[1]);
    }
}
