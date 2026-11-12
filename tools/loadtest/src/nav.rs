//! Bot navigation over the castle layout.
//!
//! The walkable-surface graph and its path search are shared with the
//! browser client's wayfinding trail: [`sim_core::nav`]. This
//! module adds what only the load test needs: the scenarios (which cells a
//! crowd uses, its keep-outs) and random destinations.
//!
//! The layout is a set of axis-aligned rectangles ([`sim_core::Surface`]).
//! Each rectangle is convex, so a straight line between two points inside one
//! surface stays on it. Navigation therefore only needs the *portals* where two
//! surfaces connect (a shared edge or an overlap at matching heights) and a
//! shortest path over them:
//!
//! - every portal has one point inside each surface, set back from the shared
//!   edge, so a bot crosses it squarely instead of grazing a door frame;
//! - every waypoint keeps [`CLEARANCE`] from the edges of the surface it lies
//!   in, so the character's whole footprint (collision radius 0.3 m) stays on
//!   that surface and `move_and_slide` never has to slide.
//!
//! Bots still steer with `sim_core::move_and_slide`, exactly like the browser
//! client, so the positions they send are ones the server must accept.
//!
//! Furniture the layout does not know about (the Great Hall's house tables
//! and benches, the staff table: castle D1, docs/art/castle-kit.md) is a
//! scenario's `keep_out` rectangles: no destination falls inside one, and a
//! straight leg that would cross one walks round its corners instead (a
//! shortest path over the corners inside the leg's surface). The server does
//! not know about them either; this only keeps the crowd looking sensible.
//!
//! The grounds are 64 heightfield surfaces. The shared graph puts
//! a portal every 8 m on a seam between two grounds tiles, and the bots'
//! `move_and_slide` follows the hill, so a bot walks them like any floor.
//! A scenario's `cells` may end in `*` (`"grounds_*"`: every grounds tile),
//! and `cell_weights` scales the area a cell's surfaces count for when
//! destinations are drawn (`spread` weights the grounds down, so they keep
//! the share the old greybox grounds had). The `grounds` scenario's walkers
//! tour named stops instead ([`crate::grounds`]).

use std::collections::BTreeMap;
use std::sync::Arc;

use rand::Rng;
use serde::{Deserialize, Serialize};
pub use sim_core::nav::{CLEARANCE, KeepOut, Portal};
#[cfg(test)]
use sim_core::nav::{KEEP_OUT_CLEARANCE, Rect, dist};
use sim_core::{MoveParams, SurfaceKind, World};

/// Destinations drawn again at most this often when they land in a keep-out.
const KEEP_OUT_DRAWS: usize = 32;

/// The scenarios, by name. Place names live in this data file, not in code
/// (CONTRIBUTING.md).
const SCENARIOS: &str = include_str!("../scenarios.json");

/// The furniture footprints walkers keep out of, shared with the client's
/// wayfinding trail (`data/world/keep_out.json` `keep_out`).
const KEEP_OUT: &str = include_str!("../../../data/world/keep_out.json");

#[derive(Deserialize)]
struct KeepOutFile {
    keep_out: Vec<KeepOut>,
}

/// Every furniture footprint of `data/world/keep_out.json`.
pub fn furniture_keep_out() -> Vec<KeepOut> {
    serde_json::from_str::<KeepOutFile>(KEEP_OUT)
        .expect("data/world/keep_out.json is valid")
        .keep_out
}

/// A load-test scenario: where bots choose their destinations.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct Scenario {
    #[serde(skip_deserializing)]
    pub name: String,
    pub description: String,
    /// Layout cells destinations are drawn from; `None` means anywhere reachable.
    pub cells: Option<Vec<String>>,
    /// The Owl Exchange court crowd (`exchange.rs`), for the `exchange` scenario.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exchange: Option<crate::exchange::ExchangeSpec>,
    /// Whether bots walk round the furniture of `data/world/keep_out.json`
    /// (those on the scenario's cells, all of them when it has none).
    #[serde(default)]
    pub furniture_keep_out: bool,
    /// The area a cell's surfaces count for when destinations are
    /// drawn, times this (cell names as in `cells`, `*` a prefix; 1 when
    /// none matches).
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub cell_weights: BTreeMap<String, f32>,
    /// The grounds walkers (`grounds` scenario, [`crate::grounds`]).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub grounds: Option<crate::grounds::GroundsSpec>,
    /// Flyers over the pitch and spectators in its stands (`pitch`
    /// scenario, [`crate::flyer`]).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pitch: Option<crate::flyer::PitchSpec>,
    /// Walkers touring the Grand Staircase through its moving
    /// flights (`stairs` scenario, [`crate::stairs`]), as the grounds
    /// walkers tour the grounds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stairs: Option<crate::grounds::GroundsSpec>,
    /// Walkers touring the hidden passages ([`crate::secrets`]).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub explore: Option<crate::secrets::ExploreSpec>,
    /// Bots pacing for the Wishing Room ([`crate::secrets`]).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ritual: Option<crate::secrets::RitualSpec>,
    /// Furniture footprints bots never stop in or walk through (castle D1),
    /// filled from `data/world/keep_out.json` when `furniture_keep_out`.
    #[serde(skip_deserializing, skip_serializing_if = "Vec::is_empty")]
    pub keep_out: Vec<KeepOut>,
}

impl Scenario {
    /// The scenario called `name` in `scenarios.json`.
    pub fn named(name: &str) -> Result<Scenario, String> {
        let mut all = Self::all();
        all.remove(name).ok_or_else(|| {
            let names: Vec<String> = Self::all().into_keys().collect();
            format!(
                "unknown scenario {name:?} (choose from {})",
                names.join(", ")
            )
        })
    }

    pub fn all() -> BTreeMap<String, Scenario> {
        let mut all: BTreeMap<String, Scenario> =
            serde_json::from_str(SCENARIOS).expect("scenarios.json is valid");
        let furniture = furniture_keep_out();
        for (name, s) in &mut all {
            s.name = name.clone();
            s.fill_keep_out(&furniture);
        }
        all
    }

    /// A scenario of its own for bots that walk `cells` (the
    /// grounds walkers), round the furniture there as this one does.
    pub fn walking(&self, name: &str, cells: &[String]) -> Scenario {
        let mut s = Scenario {
            name: name.to_owned(),
            description: String::new(),
            cells: Some(cells.to_vec()),
            exchange: None,
            furniture_keep_out: self.furniture_keep_out,
            cell_weights: BTreeMap::new(),
            grounds: None,
            pitch: None,
            stairs: None,
            explore: None,
            ritual: None,
            keep_out: Vec::new(),
        };
        s.fill_keep_out(&furniture_keep_out());
        s
    }

    /// The furniture of `furniture` on this scenario's cells (all of it
    /// when it has none), if it walks round furniture.
    fn fill_keep_out(&mut self, furniture: &[KeepOut]) {
        if self.furniture_keep_out {
            self.keep_out = furniture
                .iter()
                .filter(|k| k.cell.as_deref().is_none_or(|c| self.allows(c)))
                .cloned()
                .collect();
        }
    }

    /// True if bots may choose destinations in layout cell `cell` (and walk
    /// through it: [`Nav`] routes a scenario's bots only through its cells).
    pub fn allows(&self, cell: &str) -> bool {
        self.cells
            .as_ref()
            .is_none_or(|cells| cells.iter().any(|c| cell_matches(c, cell)))
    }

    /// How much a square metre of `cell` counts for when destinations are
    /// drawn (`cell_weights`; 1 by default).
    pub fn weight(&self, cell: &str) -> f32 {
        self.cell_weights
            .iter()
            .find(|(c, _)| cell_matches(c, cell))
            .map_or(1.0, |(_, &w)| w)
    }
}

/// `pattern` names `cell`, or ends in `*` and starts it (`"grounds_*"`).
fn cell_matches(pattern: &str, cell: &str) -> bool {
    pattern
        .strip_suffix('*')
        .map_or(pattern == cell, |prefix| cell.starts_with(prefix))
}

/// Where bots start in the castle, in the Entrance Hall
/// (players start outside on the front steps, the layout's spawns). The
/// scenarios are drawn round the hall. The server makes the real run match: `POST /api/dev/bot-session` takes an optional `start` (one of these
/// points) as a new bot's position.
pub const BOT_STARTS: [[f32; 3]; 2] = [[14.0, 0.0, 32.0], [18.0, 0.0, 32.0]];

/// Where bots start in `world`: [`BOT_STARTS`] in the castle (both on the
/// Entrance Hall's floor), else the layout's spawns (instance rooms).
pub fn bot_starts(world: &World) -> Vec<([f32; 3], f32)> {
    let in_hall = |p: &[f32; 3]| {
        world
            .ground_at(p[0], p[2], p[1])
            .is_some_and(|g| world.surface(g.surface).cell == "entrance_hall")
    };
    if BOT_STARTS.iter().all(in_hall) {
        BOT_STARTS
            .iter()
            .map(|&p| (p, std::f32::consts::PI))
            .collect()
    } else {
        world.spawns().iter().map(|s| (s.pos, s.yaw)).collect()
    }
}

/// The walkable graph of a layout plus the destinations of one scenario.
pub struct Nav {
    graph: sim_core::nav::Nav,
    /// Surfaces reachable from where bots start ([`bot_starts`]).
    reachable: Vec<bool>,
    /// Destination surfaces and their cumulative area weights.
    targets: Vec<u32>,
    target_weights: Vec<f32>,
}

impl Nav {
    pub fn new(world: Arc<World>, scenario: &Scenario) -> Nav {
        // A scenario that keeps its bots in some cells routes them inside
        // those cells too (castle M5c: the Owl Gate made the courtyard a
        // shortcut from the hall's Owl Exchange court to its front doors).
        let graph = sim_core::nav::Nav::new(world.clone(), &scenario.keep_out, |s| {
            scenario.allows(&s.cell)
        });
        // Flood fill from the surfaces under the spawns.
        let reachable = graph.reachable_from(
            bot_starts(&world)
                .iter()
                .filter_map(|(p, _)| world.ground_at(p[0], p[2], p[1]))
                .map(|g| g.surface.0),
        );
        let mut targets = Vec::new();
        let mut target_weights = Vec::new();
        let mut total = 0.0;
        for (i, s) in world.surfaces().iter().enumerate() {
            let (w, d) = (s.max[0] - s.min[0], s.max[1] - s.min[1]);
            let roomy = w > 2.0 * CLEARANCE + 0.2 && d > 2.0 * CLEARANCE + 0.2;
            let weight = scenario.weight(&s.cell);
            if reachable[i]
                && roomy
                && weight > 0.0
                && scenario.allows(&s.cell)
                && s.kind != SurfaceKind::Door
            {
                total += w * d * weight;
                targets.push(i as u32);
                target_weights.push(total);
            }
        }
        assert!(
            !targets.is_empty(),
            "the layout has no reachable destinations for scenario {:?}",
            scenario.name
        );
        Nav {
            graph,
            reachable,
            targets,
            target_weights,
        }
    }

    /// The scenario's keep-out footprints.
    pub fn keep_out(&self) -> &[KeepOut] {
        self.graph.keep_out()
    }

    /// The keep-out footprint a walker at `p` ([x, y, z]) stands on, if any
    /// (a cell's own keep-outs only on that cell's ground).
    pub fn keep_out_at(&self, p: [f32; 3]) -> Option<&KeepOut> {
        self.graph.keep_out_at(p)
    }

    pub fn world(&self) -> &World {
        self.graph.world()
    }

    pub fn params(&self) -> &MoveParams {
        self.graph.params()
    }

    pub fn portals(&self) -> &[Portal] {
        self.graph.portals()
    }

    /// Whether `p` (XZ) on `surface` lies in a keep-out grown by the
    /// clearance (no place to stop).
    pub fn blocked_at(&self, p: [f32; 2], surface: u32) -> bool {
        self.graph.blocked_at(p, surface)
    }

    pub fn is_reachable(&self, surface: u32) -> bool {
        self.reachable
            .get(surface as usize)
            .copied()
            .unwrap_or(false)
    }

    /// The destination surfaces of this scenario.
    pub fn target_surfaces(&self) -> &[u32] {
        &self.targets
    }

    /// A random destination: a surface chosen in proportion to its area, and a
    /// point on it at least [`CLEARANCE`] from its edges.
    ///
    /// Never inside a keep-out grown by the clearance (drawn again, at most
    /// [`KEEP_OUT_DRAWS`] times).
    pub fn random_target(&self, rng: &mut impl Rng) -> (u32, [f32; 2]) {
        let total = *self.target_weights.last().expect("targets exist");
        let mut draw = || {
            let pick = rng.gen_range(0.0..total);
            let k = self.target_weights.partition_point(|&w| w <= pick);
            let s = self.targets[k.min(self.targets.len() - 1)];
            let surf = &self.world().surfaces()[s as usize];
            let x = rng.gen_range(surf.min[0] + CLEARANCE..surf.max[0] - CLEARANCE);
            let z = rng.gen_range(surf.min[1] + CLEARANCE..surf.max[1] - CLEARANCE);
            (s, [x, z])
        };
        let mut t = draw();
        for _ in 1..KEEP_OUT_DRAWS {
            if !self.graph.blocked_at(t.1, t.0) {
                break;
            }
            t = draw();
        }
        t
    }

    /// The shortest route from `from` (a point on surface `from_surface`) to
    /// `to` (on `to_surface`), as XZ waypoints ending at `to`. `None` if the
    /// target cannot be reached ([`sim_core::nav::Nav::route`]).
    pub fn route(
        &self,
        from: [f32; 2],
        from_surface: u32,
        to: [f32; 2],
        to_surface: u32,
    ) -> Option<Vec<[f32; 2]>> {
        self.graph.route(from, from_surface, to, to_surface)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use rand::SeedableRng;
    use rand::rngs::StdRng;

    use super::*;

    pub const CASTLE: &str = include_str!("../../../data/world/layout.json");

    /// The castle with its moving staircase flights at rest in position A.
    pub fn castle() -> Arc<World> {
        let layout = sim_core::Layout::from_json(CASTLE).expect("castle layout loads");
        Arc::new(World::new(layout.for_stairs(0)).expect("castle layout loads"))
    }

    /// The castle without its solid furniture: whose floor a keep-out's
    /// footprint lies on (under the table it keeps the crowd away from).
    pub fn castle_bare() -> Arc<World> {
        let mut layout = sim_core::Layout::from_json(CASTLE).expect("castle layout loads");
        layout.solids.clear();
        Arc::new(World::new(layout.for_stairs(0)).expect("castle layout loads"))
    }

    /// Two rooms joined through a door, with a stair from room A up to a
    /// balcony that also overlooks room B.
    const ROOMS: &str = r#"{
        "version": 1,
        "surfaces": [
            { "id": "a", "kind": "room", "name": "A", "level": 0, "cell": "entrance_hall",
              "min": [0, 0], "max": [10, 10], "y": 0, "ceiling": 5 },
            { "id": "door", "kind": "door", "name": "Door", "level": 0, "cell": "x",
              "min": [10, 4], "max": [11, 6], "y": 0, "ceiling": 3 },
            { "id": "b", "kind": "room", "name": "B", "level": 0, "cell": "x",
              "min": [11, 0], "max": [21, 10], "y": 0, "ceiling": 5 },
            { "id": "stair", "kind": "stair", "name": "Stair", "level": 0, "cell": "entrance_hall",
              "min": [0, 10], "max": [4, 18], "y": 0, "y_top": 4, "rise": "+z", "ceiling": 5 },
            { "id": "balcony", "kind": "room", "name": "Balcony", "level": 1, "cell": "x",
              "min": [0, 18], "max": [21, 22], "y": 4, "ceiling": 4 }
        ],
        "spawns": [{ "id": "s", "pos": [5, 0, 5], "yaw": 0 }]
    }"#;

    fn scenario(name: &str) -> Scenario {
        Scenario::named(name).unwrap()
    }

    fn rooms(name: &str) -> Nav {
        Nav::new(Arc::new(World::from_json(ROOMS).unwrap()), &scenario(name))
    }

    fn assert_near(path: &[[f32; 2]], expected: &[[f32; 2]]) {
        assert_eq!(path.len(), expected.len(), "{path:?}");
        for (p, e) in path.iter().zip(expected) {
            assert!(dist(*p, *e) < 1e-4, "{path:?} != {expected:?}");
        }
    }

    #[test]
    fn scenarios_come_from_the_data_file() {
        assert!(scenario("spread").cells.is_none());
        assert!(scenario("spread").allows("anything"));
        let packed = scenario("packed");
        assert_eq!(packed.name, "packed");
        assert!(packed.allows("entrance_hall") && !packed.allows("courtyard"));
        assert!(Scenario::named("nowhere").is_err());
    }

    #[test]
    fn finds_edge_portals_only_where_heights_meet() {
        let nav = rooms("spread");
        let pairs: Vec<(u32, u32)> = nav.portals().iter().map(|p| (p.a, p.b)).collect();
        // a-door, a-stair (foot of the stair), door-b, stair-balcony (top).
        assert_eq!(pairs, vec![(0, 1), (0, 3), (1, 2), (3, 4)]);
        // The door portal crosses the a/door edge at x = 10 in the door's middle,
        // set back 0.6 m into the room and half the door's depth into the door.
        let door = nav.portals()[0];
        assert_near(&[door.a_pt, door.b_pt], &[[9.4, 5.0], [10.5, 5.0]]);
    }

    #[test]
    fn routes_through_the_door() {
        let nav = rooms("spread");
        let path = nav.route([2.0, 2.0], 0, [18.0, 8.0], 2).unwrap();
        assert_near(
            &path,
            &[
                [9.4, 5.0],
                [10.5, 5.0],
                [10.5, 5.0],
                [11.6, 5.0],
                [18.0, 8.0],
            ],
        );
        // The balcony is reached up the stair, not over the room B wall.
        let up = nav.route([18.0, 8.0], 2, [18.0, 20.0], 4).unwrap();
        assert!(up.iter().any(|p| p[0] < 4.0 && p[1] > 10.0));
        assert_eq!(
            nav.route([2.0, 2.0], 0, [3.0, 3.0], 0).unwrap(),
            vec![[3.0, 3.0]]
        );
    }

    #[test]
    fn packed_targets_stay_in_their_cells() {
        let nav = rooms("packed");
        assert_eq!(nav.target_surfaces(), &[0, 3]);
        let packed = scenario("packed");
        let mut rng = StdRng::seed_from_u64(1);
        for _ in 0..1000 {
            let (s, [x, z]) = nav.random_target(&mut rng);
            let surf = &nav.world().surfaces()[s as usize];
            assert!(packed.allows(&surf.cell));
            assert!(x >= surf.min[0] + CLEARANCE && x <= surf.max[0] - CLEARANCE);
            assert!(z >= surf.min[1] + CLEARANCE && z <= surf.max[1] - CLEARANCE);
        }
    }

    #[test]
    fn a_leg_walks_round_a_keep_out() {
        let mut s = scenario("spread");
        s.keep_out = vec![KeepOut {
            id: "table".into(),
            min: [4.0, 1.0],
            max: [6.0, 9.0],
            cell: None,
        }];
        let nav = Nav::new(Arc::new(World::from_json(ROOMS).unwrap()), &s);
        // Straight across room A would cross the table: round one end, just
        // outside its clearance (z 1 - 0.45 - 0.05 = 0.5; both ends are as short).
        let path = nav.route([2.0, 5.0], 0, [8.0, 5.0], 0).unwrap();
        assert_near(&path, &[[3.5, 0.5], [6.5, 0.5], [8.0, 5.0]]);
        let grown = KeepOut {
            id: String::new(),
            min: [4.0, 1.0],
            max: [6.0, 9.0],
            cell: None,
        }
        .grown(KEEP_OUT_CLEARANCE);
        let mut at = [2.0, 5.0];
        for &p in &path {
            assert!(
                !grown.crossed_by(at, p),
                "{at:?} -> {p:?} crosses the table"
            );
            at = p;
        }
        // Through the door to room B: the first leg goes round too, then the portal.
        let through = nav.route([2.0, 5.0], 0, [18.0, 8.0], 2).unwrap();
        assert_near(
            &through[2..],
            &[
                [9.4, 5.0],
                [10.5, 5.0],
                [10.5, 5.0],
                [11.6, 5.0],
                [18.0, 8.0],
            ],
        );
        // No destination inside the table's clearance.
        let mut rng = StdRng::seed_from_u64(5);
        for _ in 0..2000 {
            let (_, p) = nav.random_target(&mut rng);
            assert!(!grown.holds(p), "{p:?}");
        }
        // A walker a correction left on the table walks straight off it.
        assert_eq!(
            nav.route([5.0, 5.0], 0, [8.0, 5.0], 0).unwrap(),
            vec![[8.0, 5.0]]
        );
    }

    #[test]
    fn a_keep_out_closing_a_surface_leaves_no_route() {
        let mut s = scenario("spread");
        s.keep_out = vec![KeepOut {
            id: "wall".into(),
            min: [4.0, -1.0],
            max: [6.0, 11.0],
            cell: None,
        }];
        let nav = Nav::new(Arc::new(World::from_json(ROOMS).unwrap()), &s);
        assert!(nav.route([2.0, 5.0], 0, [8.0, 5.0], 0).is_none());
    }

    #[test]
    fn rectangles_clip_segments_by_their_open_interior() {
        let r = Rect {
            min: [0.0, 0.0],
            max: [2.0, 2.0],
        };
        assert!(r.crossed_by([-1.0, 1.0], [3.0, 1.0]));
        assert!(r.crossed_by([1.0, 1.0], [1.0, 1.5]), "inside");
        assert!(!r.crossed_by([-1.0, 0.0], [3.0, 0.0]), "along a side");
        assert!(!r.crossed_by([-1.0, 3.0], [3.0, 2.5]), "outside");
        assert!(!r.crossed_by([-1.0, 1.0], [-0.1, 1.0]), "stops short");
        assert!(r.crossed_by([-1.0, -1.0], [3.0, 3.0]), "diagonal");
        assert!(
            !r.crossed_by([-1.0, 1.0], [1.0, 3.0]),
            "touches a corner only"
        );
        assert!(r.holds([1.0, 1.0]) && !r.holds([0.0, 1.0]));
    }

    /// The keep-outs (castle D1) cover the Great Hall's house tables and their
    /// benches, the staff table, the owl lectern and the hourglass plinth as
    /// placed in data/world/castle_kit.json, lie on the hall's floor, and are
    /// the same for every scenario that sends bots there (spread adds the
    /// Undercroft's well, castle M5a).
    #[test]
    fn keep_outs_cover_the_great_hall_furniture() {
        let packed = scenario("packed");
        // (Castle M5c: both also keep out of the Owl Exchange court dressing,
        // scoped to the entrance hall; the Great Hall's furniture is scoped
        // to great_hall, off the rooms below it.)
        let halls = |k: &KeepOut| {
            k.cell
                .as_deref()
                .is_none_or(|c| c == "entrance_hall" || c == "great_hall")
        };
        let spread_hall: Vec<KeepOut> = scenario("spread")
            .keep_out
            .into_iter()
            .filter(halls)
            .collect();
        assert_eq!(packed.keep_out, spread_hall);
        assert!(scenario("exchange").keep_out.is_empty());
        let kit: serde_json::Value =
            serde_json::from_str(include_str!("../../../data/world/castle_kit.json")).unwrap();
        let mut placed = 0;
        let cells = kit["cells"].as_object().expect("castle_kit.json cells");
        for p in cells["great_hall"]["hero"]
            .as_array()
            .expect("great hall hero placements")
        {
            let piece = p["piece"].as_str().unwrap();
            let (len, depth) = if let Some(l) = piece
                .strip_prefix("kit_table_house_")
                .or(piece.strip_prefix("kit_table_staff_"))
            {
                (l.parse::<f32>().unwrap(), 1.0)
            } else if let Some(l) = piece.strip_prefix("kit_bench_") {
                (l.parse::<f32>().unwrap(), 0.4)
            } else {
                continue;
            };
            let pos = [
                p["pos"][0].as_f64().unwrap() as f32,
                p["pos"][2].as_f64().unwrap() as f32,
            ];
            let along_z =
                p["yaw"].as_f64().unwrap().abs() > 1.0 && p["yaw"].as_f64().unwrap().abs() < 2.0;
            let (hx, hz) = if along_z {
                (depth / 2.0, len / 2.0)
            } else {
                (len / 2.0, depth / 2.0)
            };
            let (lo, hi) = (
                [pos[0] - hx + 0.01, pos[1] - hz + 0.01],
                [pos[0] + hx - 0.01, pos[1] + hz - 0.01],
            );
            assert!(
                packed
                    .keep_out
                    .iter()
                    .any(|k| k.contains(lo) && k.contains(hi)),
                "{} {piece} at {pos:?} is not kept out",
                p["id"]
            );
            placed += 1;
        }
        assert_eq!(
            placed,
            4 + 8 + 1,
            "four house tables, eight benches, the staff table"
        );
        let world = castle_bare(); // keep-outs lie on the floor under the furniture
        for k in packed
            .keep_out
            .iter()
            .filter(|k| k.cell.as_deref() == Some("great_hall"))
        {
            for p in [k.min, k.max, [k.min[0], k.max[1]], [k.max[0], k.min[1]]] {
                let g = world
                    .ground_at(p[0].clamp(34.01, 99.99), p[1].clamp(9.01, 30.99), 1.0)
                    .expect("on the hall's floor");
                assert_eq!(
                    world.surfaces()[g.surface.0 as usize].cell,
                    "great_hall",
                    "{}",
                    k.id
                );
            }
        }
    }

    /// Castle M5a (docs/art/castle-kit.md): the spread crowd keeps out
    /// of the Undercroft Walk's dry well (grid.WELL_RADIUS round the hero
    /// `well` of data/world/castle_kit.json), and the first-floor corridor
    /// above it keeps its floor.
    #[test]
    fn the_spread_crowd_keeps_out_of_the_undercroft_well() {
        let spread = scenario("spread");
        let well: Vec<&KeepOut> = spread
            .keep_out
            .iter()
            .filter(|k| k.cell.as_deref() == Some("gf_undercroft"))
            .collect();
        assert_eq!(well.len(), 1);
        let well = well[0];
        assert_eq!(
            (well.id.as_str(), well.cell.as_deref()),
            ("well", Some("gf_undercroft"))
        );
        let kit: serde_json::Value =
            serde_json::from_str(include_str!("../../../data/world/castle_kit.json")).unwrap();
        let hero = kit["cells"]["gf_undercroft"]["hero"]
            .as_array()
            .unwrap()
            .iter()
            .find(|h| h["piece"] == "kit_well_dry")
            .expect("the well hero");
        let (x, z) = (
            hero["pos"][0].as_f64().unwrap() as f32,
            hero["pos"][2].as_f64().unwrap() as f32,
        );
        const WELL_RADIUS: f32 = 1.5; // castle_kit/grid.py WELL_RADIUS
        assert!(
            well.contains([x - WELL_RADIUS, z - WELL_RADIUS])
                && well.contains([x + WELL_RADIUS, z + WELL_RADIUS])
        );

        let world = castle_bare(); // keep-outs lie on the floor under the furniture
        let nav = Nav::new(world.clone(), &spread);
        let surface_of = |cell: &str, y: f32| {
            let g = world.ground_at(x, z, y).expect("ground at the well");
            assert_eq!(world.surfaces()[g.surface.0 as usize].cell, cell);
            g.surface.0
        };
        let (under, over) = (
            surface_of("gf_undercroft", 0.0),
            surface_of("ff_corridor_north", 9.0),
        );
        // Across the walk: round the well; across the corridor above: straight.
        let (a, b) = ([x - 4.0, z], [x + 4.0, z]);
        let walk = nav.route(a, under, b, under).unwrap();
        assert!(walk.len() > 1, "{walk:?}");
        assert_eq!(nav.route(a, over, b, over).unwrap(), vec![b]);
        assert!(nav.keep_out_at([x, 0.0, z]).is_some());
        assert!(nav.keep_out_at([x, 9.0, z]).is_none());
        let mut rng = StdRng::seed_from_u64(11);
        for _ in 0..4000 {
            let (s, p) = nav.random_target(&mut rng);
            if s == under {
                assert!(!well.contains(p), "{p:?}");
            }
        }
    }

    /// Castle M5a (docs/art/castle-kit.md): every stop of the perf
    /// camera paths (`perf_paths.json`) stands on walkable ground of its
    /// path's cell, so a teleport there lands where the path means; the ids
    /// are unique and the M5a, M5b and M5c paths are there.
    #[test]
    fn perf_paths_stand_on_their_cells() {
        let paths: serde_json::Value =
            serde_json::from_str(include_str!("../perf_paths.json")).expect("perf_paths.json");
        let world = castle();
        let mut ids = std::collections::BTreeSet::new();
        for p in paths["paths"].as_array().expect("paths") {
            let id = p["id"].as_str().unwrap();
            assert!(ids.insert(id), "{id} twice");
            assert!(p["seconds"].as_f64().unwrap() > 0.0, "{id}");
            let cell = p["cell"].as_str().unwrap();
            let stops = p["stops"].as_array().unwrap();
            assert!(!stops.is_empty(), "{id}");
            for st in stops {
                let pos: Vec<f32> = st["pos"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_f64().unwrap() as f32)
                    .collect();
                let g = world
                    .ground_at(pos[0], pos[2], pos[1])
                    .unwrap_or_else(|| panic!("{id}: no ground at {pos:?}"));
                assert!(
                    (g.y - pos[1]).abs() < 0.01,
                    "{id}: ground at {} not {}",
                    g.y,
                    pos[1]
                );
                assert_eq!(
                    world.surfaces()[g.surface.0 as usize].cell,
                    cell,
                    "{id} at {pos:?}"
                );
                assert!(
                    st["orbit"].is_f64() != st["camera"].is_object(),
                    "{id}: an orbit or a held camera"
                );
            }
        }
        for id in [
            "eh",
            "gh",
            "gs_l0",
            "gs_l3",
            "gs_up",
            "sf",
            "l4",
            "tower_climb",
            "gallery_long",
            "undercroft_walk",
            "cr_a",
            "cr_b",
            "cr_c",
            "cr_d",
            "potions",
            "kitchens",
            "library",
            "trophy_room",
            "courtyard",
            "facade_front",
            "yard",
            "owl_gate",
        ] {
            assert!(ids.contains(id), "{id}");
        }
    }

    /// Castle M5b (docs/art/castle-kit.md): the spread crowd keeps out
    /// of the room furniture that stands in the way: every long table (with
    /// its benches) of the Kitchens, every free-standing bookcase and reading
    /// table of the library and every trophy case, as placed in
    /// data/world/castle_kit.json, lies inside a keep-out scoped to its cell;
    /// each keep-out lies on its cell's floor, no destination falls in one,
    /// and the rooms' doors stay clear of them.
    #[test]
    fn the_spread_crowd_keeps_out_of_the_m5b_room_furniture() {
        let spread = scenario("spread");
        let kit: serde_json::Value =
            serde_json::from_str(include_str!("../../../data/world/castle_kit.json")).unwrap();
        // (piece prefix, half depth across its facing, whether its origin is its back)
        let mut covered = std::collections::BTreeMap::new();
        for cell in ["kitchens", "sf_library", "trophy_room"] {
            for h in kit["cells"][cell]["hero"].as_array().unwrap() {
                let piece = h["piece"].as_str().unwrap();
                let id = h["id"].as_str().unwrap();
                let (len, depth, from_back) = if let Some(l) = piece
                    .strip_prefix("kit_table_house_")
                    .or(piece.strip_prefix("kit_bench_"))
                {
                    (
                        l.parse::<f32>().unwrap(),
                        if piece.contains("bench") { 0.4 } else { 1.0 },
                        false,
                    )
                } else if piece == "kit_trophy_case" {
                    (h["args"]["width"].as_f64().unwrap() as f32, 0.6, true)
                } else if piece == "kit_table_study" {
                    (h["args"]["length"].as_f64().unwrap() as f32, 0.9, false)
                } else if piece.starts_with("kit_bookcase_") && id.starts_with("stack_") {
                    let w = piece["kit_bookcase_".len()..].split('x').next().unwrap();
                    (w.parse::<f32>().unwrap(), 0.5, true)
                } else {
                    continue;
                };
                let (x, z) = (
                    h["pos"][0].as_f64().unwrap() as f32,
                    h["pos"][2].as_f64().unwrap() as f32,
                );
                let yaw = h["yaw"].as_f64().unwrap_or(0.0) as f32;
                // Facing (sin yaw, cos yaw); the piece's width runs across it.
                let (fx, fz) = (yaw.sin().round(), yaw.cos().round());
                let (near, far) = if from_back {
                    (0.0, depth)
                } else {
                    (-depth / 2.0, depth / 2.0)
                };
                let (a, b) = ([x + fx * near, z + fz * near], [x + fx * far, z + fz * far]);
                let (wx, wz) = (fz.abs() * len / 2.0, fx.abs() * len / 2.0);
                let lo = [a[0].min(b[0]) - wx + 0.01, a[1].min(b[1]) - wz + 0.01];
                let hi = [a[0].max(b[0]) + wx - 0.01, a[1].max(b[1]) + wz - 0.01];
                let k = spread
                    .keep_out
                    .iter()
                    .find(|k| k.cell.as_deref() == Some(cell) && k.contains(lo) && k.contains(hi));
                assert!(
                    k.is_some(),
                    "{cell} {id} {piece} {lo:?}..{hi:?} is not kept out"
                );
                *covered.entry(cell).or_insert(0) += 1;
            }
        }
        // Four tables and eight benches; four double stacks and two reading tables; 12 cases
        // (the Trophy Cabinet Stair's panel took one).
        assert_eq!(covered["kitchens"], 12);
        assert_eq!(covered["sf_library"], 10);
        assert_eq!(covered["trophy_room"], 12);

        let world = castle_bare(); // keep-outs lie on the floor under the furniture
        let nav = Nav::new(world.clone(), &spread);
        let m5b: Vec<&KeepOut> = spread
            .keep_out
            .iter()
            .filter(|k| {
                matches!(
                    k.cell.as_deref(),
                    Some("kitchens" | "sf_library" | "trophy_room")
                )
            })
            .collect();
        assert_eq!(m5b.len(), 17);
        for k in &m5b {
            let cell = k.cell.as_deref().unwrap();
            let y = world.surfaces().iter().find(|s| s.id == cell).unwrap().y;
            for p in [
                [k.min[0] + 0.01, k.min[1] + 0.01],
                [k.max[0] - 0.01, k.max[1] - 0.01],
            ] {
                let g = world.ground_at(p[0], p[1], y).expect("on the floor");
                assert_eq!(
                    world.surfaces()[g.surface.0 as usize].cell,
                    cell,
                    "{}",
                    k.id
                );
                assert!(
                    nav.keep_out_at([(k.min[0] + k.max[0]) / 2.0, y, (k.min[1] + k.max[1]) / 2.0])
                        .is_some(),
                    "{}",
                    k.id
                );
            }
        }
        let grown: Vec<Rect> = m5b.iter().map(|k| k.grown(KEEP_OUT_CLEARANCE)).collect();
        for p in nav.portals() {
            for r in &grown {
                assert!(
                    !r.holds(p.a_pt) && !r.holds(p.b_pt) && !r.crossed_by(p.a_pt, p.b_pt),
                    "{p:?}"
                );
            }
        }
        let mut rng = StdRng::seed_from_u64(26);
        for _ in 0..4000 {
            let (s, p) = nav.random_target(&mut rng);
            let surf = &world.surfaces()[s as usize];
            for k in &m5b {
                assert!(!(k.on_cell(&surf.cell) && k.contains(p)), "{} {p:?}", k.id);
            }
        }
    }

    /// Castle M5c (docs/art/castle-kit.md): the spread crowd keeps out
    /// of the courtyard pieces that stand in the way (the fountain, the
    /// stalls, the benches, the planters, the lamp posts: one keep-out per
    /// hero of data/world/castle_kit.json under its id, holding its origin;
    /// the gatehouse's two towers either side of the gate's open passage; the
    /// free-standing arcade piers) and of the Owl Exchange court dressing;
    /// each lies on its cell's floor, no destination falls in one and the
    /// portals stay clear of them.
    #[test]
    fn the_spread_crowd_keeps_out_of_the_m5c_courtyard() {
        let spread = scenario("spread");
        let kit: serde_json::Value =
            serde_json::from_str(include_str!("../../../data/world/castle_kit.json")).unwrap();
        let find = |id: &str| spread.keep_out.iter().find(|k| k.id == id);
        let mut heroes = 0;
        for (cell, prefixes) in [
            (
                "courtyard",
                &[
                    "kit_fountain_",
                    "kit_market_stall",
                    "kit_planter_",
                    "kit_lamp_post",
                    "kit_bench_",
                ][..],
            ),
            (
                "entrance_hall",
                &["kit_notice_wall", "kit_perch_tree", "kit_crate_stack"][..],
            ),
        ] {
            for h in kit["cells"][cell]["hero"].as_array().unwrap() {
                let piece = h["piece"].as_str().unwrap();
                if !prefixes.iter().any(|p| piece.starts_with(p)) {
                    continue;
                }
                let id = h["id"].as_str().unwrap();
                let k = if id.starts_with("court_crates") {
                    find("court_crates")
                } else {
                    find(id)
                }
                .unwrap_or_else(|| panic!("{cell} {id} has no keep-out"));
                assert_eq!(k.cell.as_deref(), Some(cell), "{id}");
                let (x, z) = (
                    h["pos"][0].as_f64().unwrap() as f32,
                    h["pos"][2].as_f64().unwrap() as f32,
                );
                assert!(k.contains([x, z]), "{id} at ({x}, {z}) outside {k:?}");
                heroes += 1;
            }
        }
        // 1 fountain, 5 stalls, 7 benches, 6 planters, 6 lamp posts; the notice wall, the perch tree, 2 crate stacks.
        assert_eq!(heroes, 25 + 4);
        let piers: Vec<&KeepOut> = spread
            .keep_out
            .iter()
            .filter(|k| k.id.starts_with("arcade_pier_"))
            .collect();
        assert_eq!(piers.len(), 27); // 21, and the south arcade's 6 (castle M5c review)
        let (tw, te) = (
            find("gatehouse_tower_w").unwrap(),
            find("gatehouse_tower_e").unwrap(),
        );
        assert!(
            tw.max[0] <= 12.0 && te.min[0] >= 20.0,
            "the gate's passage x 12..20 stays open"
        );

        let world = castle_bare(); // keep-outs lie on the floor under the furniture
        let nav = Nav::new(world.clone(), &spread);
        let m5c: Vec<&KeepOut> = spread
            .keep_out
            .iter()
            .filter(|k| k.cell.as_deref() == Some("courtyard") || k.id.starts_with("court_"))
            .collect();
        assert_eq!(m5c.len(), 25 + 27 + 2 + 3);
        for k in &m5c {
            let cell = k.cell.as_deref().unwrap();
            if k.id.starts_with("gatehouse_tower_") {
                // The towers are layout solids (castle M5c review): no floor
                // inside, the keep-out over the solid (bots plan round it), the
                // courtyard's floor in front of it.
                let solid = world
                    .layout()
                    .areas
                    .iter()
                    .find(|a| a.id == k.id && a.kind == sim_core::AreaKind::Solid)
                    .expect("a layout solid");
                assert!(
                    (solid.min[0] - k.min[0]).abs() < 1e-3
                        && (solid.max[0] - k.max[0]).abs() < 1e-3
                        && (solid.min[2] - k.min[1]).abs() < 1e-3
                        && (solid.max[2] - k.max[1]).abs() < 1e-3,
                    "{}",
                    k.id
                );
                let mid = (k.min[0] + k.max[0]) / 2.0;
                assert!(
                    world
                        .ground_at(mid, (k.min[1] + k.max[1]) / 2.0, 0.0)
                        .is_none()
                );
                let g = world.ground_at(mid, k.min[1] - 0.5, 0.0).expect("in front");
                assert_eq!(world.surfaces()[g.surface.0 as usize].cell, cell);
                continue;
            }
            for p in [
                [k.min[0] + 0.01, k.min[1] + 0.01],
                [k.max[0] - 0.01, k.max[1] - 0.01],
            ] {
                let g = world.ground_at(p[0], p[1], 0.0).expect("on the floor");
                assert_eq!(
                    world.surfaces()[g.surface.0 as usize].cell,
                    cell,
                    "{}",
                    k.id
                );
            }
            assert!(
                nav.keep_out_at([
                    (k.min[0] + k.max[0]) / 2.0,
                    0.0,
                    (k.min[1] + k.max[1]) / 2.0
                ])
                .is_some(),
                "{}",
                k.id
            );
        }
        let grown: Vec<Rect> = m5c.iter().map(|k| k.grown(KEEP_OUT_CLEARANCE)).collect();
        for p in nav.portals() {
            for r in &grown {
                assert!(
                    !r.holds(p.a_pt) && !r.holds(p.b_pt) && !r.crossed_by(p.a_pt, p.b_pt),
                    "{p:?}"
                );
            }
        }
        let mut rng = StdRng::seed_from_u64(30);
        for _ in 0..4000 {
            let (s, p) = nav.random_target(&mut rng);
            let surf = &world.surfaces()[s as usize];
            for k in &m5c {
                assert!(!(k.on_cell(&surf.cell) && k.contains(p)), "{} {p:?}", k.id);
            }
        }
    }

    #[test]
    fn castle_portals_stay_clear_of_the_keep_outs() {
        let packed = scenario("packed");
        let nav = Nav::new(castle(), &packed);
        let grown: Vec<Rect> = packed
            .keep_out
            .iter()
            .map(|k| k.grown(KEEP_OUT_CLEARANCE))
            .collect();
        for p in nav.portals() {
            for r in &grown {
                assert!(
                    !r.holds(p.a_pt) && !r.holds(p.b_pt) && !r.crossed_by(p.a_pt, p.b_pt),
                    "{p:?}"
                );
            }
        }
    }

    #[test]
    fn whole_castle_is_reachable_and_routable() {
        let world = castle();
        let nav = Nav::new(world.clone(), &scenario("spread"));
        let unreachable: Vec<&str> = world
            .surfaces()
            .iter()
            .enumerate()
            .filter(|(i, _)| !nav.is_reachable(*i as u32))
            .map(|(_, s)| s.id.as_str())
            .collect();
        assert!(unreachable.is_empty(), "unreachable: {unreachable:?}");
        let mut rng = StdRng::seed_from_u64(7);
        for _ in 0..2000 {
            let (a, pa) = nav.random_target(&mut rng);
            let (b, pb) = nav.random_target(&mut rng);
            assert!(
                nav.route(pa, a, pb, b).is_some(),
                "no route {a} {pa:?} {} -> {b} {pb:?} {}",
                world.surfaces()[a as usize].id,
                world.surfaces()[b as usize].id
            );
        }
    }

    #[test]
    fn packed_castle_targets_cover_every_listed_cell() {
        let world = castle();
        let packed = scenario("packed");
        let nav = Nav::new(world.clone(), &packed);
        let cells: std::collections::BTreeSet<&str> = nav
            .target_surfaces()
            .iter()
            .map(|&s| world.surfaces()[s as usize].cell.as_str())
            .collect();
        let listed: std::collections::BTreeSet<&str> =
            packed.cells.iter().flatten().map(String::as_str).collect();
        assert_eq!(cells, listed);
    }
}
