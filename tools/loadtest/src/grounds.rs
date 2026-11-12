//! The `grounds` scenario's walkers. Some of the bots (`walkers`
//! in `scenarios.json`, 300 by default; the rest keep to the scenario's own
//! cells, the packed halls) walk out of the Entrance Hall through the
//! courtyard gate and tour the hilly grounds: down to the Lake Shore, along
//! it to the Boathouse, up to the Groundskeeper's Cottage, the class gates and the stone
//! circle, and back to the hall (`stops`, key-place targets as in
//! `data/world/key_places.json`). Each walker starts the tour at a random
//! stop, stops within `jitter` metres of each, and makes a share of its
//! trips (`stray`) to a random point of its cells instead, so the crowd
//! spreads over the grounds rather than marching in one file.
//!
//! Walkers route with the shared navigation (`sim_core::nav`: the grounds'
//! 8 m portals, the house's own world) through their own cells only
//! (`cells`: the hall, the courtyard, `grounds_*`), round the furniture and
//! the grounds' keep-outs (`data/world/keep_out.json`), and step with
//! `move_and_slide` over the hills, so the server never corrects them.
//!
//! The `stairs` scenario's walkers (`scenarios.json` `stairs`,
//! the same kind of tour) go up and down the Grand Staircase through its
//! moving flights instead; their navigation is per staircase state
//! ([`crate::stairs::CastleNavs`]).

use std::sync::Arc;

use rand::Rng;
use serde::{Deserialize, Serialize};
use sim_core::key_places::PlaceTarget;
use sim_core::{Layout, World};

use crate::nav::{CLEARANCE, Nav, Scenario};
use crate::stairs::CastleNavs;

/// Round a stop, directions a walker's footprint must find ground in.
const FOOTPRINT: [(f32, f32); 8] = [
    (-1.0, 0.0),
    (1.0, 0.0),
    (0.0, -1.0),
    (0.0, 1.0),
    (-0.7, -0.7),
    (0.7, -0.7),
    (-0.7, 0.7),
    (0.7, 0.7),
];

/// The role the grounds walkers report under.
pub const ROLE: &str = "grounds walker";

/// Tries at a jittered stop before standing on the stop itself.
const JITTER_DRAWS: usize = 8;
/// How far below or above the stop a jittered point's ground may be (one
/// storey on the grounds: the hill only).
const JITTER_RISE: f32 = 4.0;

/// The `grounds` entry of a scenario in `scenarios.json`.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct GroundsSpec {
    /// How many of the bots tour the grounds (all of them if fewer).
    pub walkers: usize,
    /// The cells they walk (and draw stray destinations from).
    pub cells: Vec<String>,
    /// The tour, in order (it loops).
    pub stops: Vec<PlaceTarget>,
    /// A stop is any point on the ground within this many metres of it.
    pub jitter: f32,
    /// Share of trips to a random point of `cells` instead of the next stop.
    pub stray: f64,
}

/// The grounds walkers of one run (or the stair walkers).
pub struct Grounds {
    pub spec: GroundsSpec,
    /// The role they report under ([`ROLE`], [`crate::stairs::ROLE`]).
    pub role: &'static str,
    /// Navigation per house (and staircase state) over the walkers' cells.
    navs: CastleNavs,
    /// The tour's stops, resolved in the layout.
    pub stops: Arc<[[f32; 3]]>,
    /// Which bots are grounds walkers, by index.
    members: Vec<bool>,
}

impl Grounds {
    /// The walkers of `scenario` in `world` (`layout`: the castle with both
    /// positions of each moving flight), chosen among the bots that only
    /// walk (`walking[i]`: no school role): `spec.walkers` of them, evenly
    /// spread over the ramp. The scenario's `grounds` walkers, or its
    /// `stairs` walkers.
    #[cfg(test)]
    pub fn new(
        world: &World,
        layout: &Layout,
        scenario: &Scenario,
        walking: &[bool],
    ) -> Result<Grounds, String> {
        let (spec, role) = match (&scenario.grounds, &scenario.stairs) {
            (Some(g), _) => (g.clone(), ROLE),
            (None, Some(s)) => (s.clone(), crate::stairs::ROLE),
            (None, None) => {
                return Err(format!(
                    "scenario {} has no grounds or stair walkers",
                    scenario.name
                ));
            }
        };
        Grounds::with_spec(world, layout, scenario, spec, role, walking)
    }

    /// The walkers of one tour of `scenario` (its grounds, stair
    /// or passage tour, `role` naming it), chosen among `walking`.
    pub fn with_spec(
        world: &World,
        layout: &Layout,
        scenario: &Scenario,
        spec: GroundsSpec,
        role: &'static str,
        walking: &[bool],
    ) -> Result<Grounds, String> {
        if spec.stops.is_empty() || !(0.0..=1.0).contains(&spec.stray) || spec.jitter < 0.0 {
            return Err(
                "grounds: needs stops, a stray share of 0-1 and a jitter of 0 or more".into(),
            );
        }
        let stops = resolve(world, &spec.stops)?;
        let theirs = scenario.walking(role, &spec.cells);
        let navs = CastleNavs::new(layout, &theirs)?;
        Ok(Grounds {
            members: members(walking, spec.walkers),
            spec,
            role,
            navs,
            stops: stops.into(),
        })
    }

    /// Whether bot `index` tours the grounds.
    pub fn is_member(&self, index: usize) -> bool {
        self.members.get(index).copied().unwrap_or(false)
    }

    /// How many bots tour the grounds.
    pub fn walkers(&self) -> usize {
        self.members.iter().filter(|&&m| m).count()
    }

    /// The walkers' navigation for `house` with every moving flight at A.
    pub fn nav(&self, house: protocol::House) -> &Arc<Nav> {
        self.navs.nav(house, 0)
    }

    /// The walkers' navigation for `house` in staircase state `state`.
    pub fn nav_in(&self, house: protocol::House, state: u8) -> &Arc<Nav> {
        self.navs.nav(house, state)
    }

    /// A walker's tour, starting at a random stop.
    pub fn tour(&self, rng: &mut impl Rng) -> Tour {
        Tour {
            stops: self.stops.clone(),
            next: rng.gen_range(0..self.stops.len()),
            jitter: self.spec.jitter,
            stray: self.spec.stray,
        }
    }
}

/// Where each stop is in `world`.
pub fn resolve(world: &World, stops: &[PlaceTarget]) -> Result<Vec<[f32; 3]>, String> {
    let layout = world.layout();
    stops
        .iter()
        .map(|t| {
            let p = match t {
                PlaceTarget::Interactable(id) => layout.interactable(id).map(|i| i.pos),
                PlaceTarget::Landmark(id) => {
                    layout.landmarks.iter().find(|l| l.id == *id).map(|l| l.pos)
                }
                PlaceTarget::Pos(p) => Some(*p),
            }
            .ok_or_else(|| format!("grounds stop {t:?} is not in the layout"))?;
            world
                .ground_at(p[0], p[2], p[1])
                .map(|g| [p[0], g.y, p[2]])
                .ok_or_else(|| format!("grounds stop {t:?} at {p:?} is not on the ground"))
        })
        .collect()
}

/// `want` of the bots marked in `walking`, evenly spread (all of them if
/// fewer).
pub fn members(walking: &[bool], want: usize) -> Vec<bool> {
    let candidates: Vec<usize> = (0..walking.len()).filter(|&i| walking[i]).collect();
    let n = want.min(candidates.len());
    let mut out = vec![false; walking.len()];
    for k in 0..n {
        out[candidates[k * candidates.len() / n]] = true;
    }
    out
}

/// One walker's way round the stops.
#[derive(Debug, Clone)]
pub struct Tour {
    stops: Arc<[[f32; 3]]>,
    next: usize,
    jitter: f32,
    stray: f64,
}

impl Tour {
    /// The next destination (a surface and a point on it): near the next
    /// stop, or now and then a random point of the walker's cells.
    pub fn next_target(&mut self, nav: &Nav, rng: &mut impl Rng) -> (u32, [f32; 2]) {
        if rng.gen_bool(self.stray) {
            return nav.random_target(rng);
        }
        let stop = self.stops[self.next];
        self.next = (self.next + 1) % self.stops.len();
        self.near(nav, stop, rng)
            .unwrap_or_else(|| nav.random_target(rng))
    }

    /// A point on reachable ground within `jitter` of `stop`, out of the
    /// keep-outs and [`CLEARANCE`] from any edge of the ground (the water,
    /// a building), so the walker's footprint fits there; the stop itself if
    /// no draw lands well.
    fn near(&self, nav: &Nav, stop: [f32; 3], rng: &mut impl Rng) -> Option<(u32, [f32; 2])> {
        let world = nav.world();
        let step = nav.params().step_height;
        let usable = |p: [f32; 2], y: f32, rise: f32| {
            let g = world.ground_within(p[0], p[1], y, rise)?;
            let s = g.surface.0;
            let fits = FOOTPRINT.iter().all(|(dx, dz)| {
                let (x, z) = (p[0] + dx * CLEARANCE, p[1] + dz * CLEARANCE);
                world.step_ground(x, z, g.y, step).is_some()
            });
            (fits && nav.is_reachable(s) && !nav.blocked_at(p, s)).then_some((s, p))
        };
        if self.jitter > 0.0 {
            for _ in 0..JITTER_DRAWS {
                let r = self.jitter * rng.gen_range(0.0f32..1.0).sqrt();
                let a = rng.gen_range(0.0..std::f32::consts::TAU);
                let p = [stop[0] + r * a.cos(), stop[2] + r * a.sin()];
                if let Some(t) = usable(p, stop[1], JITTER_RISE) {
                    return Some(t);
                }
            }
        }
        usable([stop[0], stop[2]], stop[1], sim_core::world::SNAP_TOLERANCE)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use rand::SeedableRng;
    use rand::rngs::StdRng;
    use sim_core::nav::dist;

    use super::*;
    use crate::nav::tests::castle;

    pub fn scenario() -> Scenario {
        Scenario::named("grounds").unwrap()
    }

    pub fn grounds(walking: usize) -> Grounds {
        let world = castle();
        Grounds::new(&world, world.layout(), &scenario(), &vec![true; walking]).unwrap()
    }

    /// The `stairs` scenario's walkers over `layout` (the castle
    /// with both positions of each moving flight).
    pub fn stair_walkers(layout: &Layout, walking: usize) -> Grounds {
        let world = World::new(layout.for_stairs(0)).unwrap();
        let s = Scenario::named("stairs").unwrap();
        let g = Grounds::new(&world, layout, &s, &vec![true; walking]).unwrap();
        assert_eq!(g.role, crate::stairs::ROLE);
        g
    }

    #[test]
    fn the_grounds_scenario_tours_the_named_stops() {
        let s = scenario();
        let spec = s.grounds.as_ref().expect("grounds walkers");
        assert_eq!(spec.walkers, 300);
        // The rest stay packed in the halls, as `packed`.
        assert_eq!(s.cells, Scenario::named("packed").unwrap().cells);
        let world = castle();
        let stops = resolve(&world, &spec.stops).unwrap();
        // Out to the lake, the boathouse, the Groundskeeper's Cottage, both class gates,
        // the stone circle, and back to the hall.
        let lm = |id: &str| PlaceTarget::Landmark(id.into());
        let gate = |id: &str| PlaceTarget::Interactable(id.into());
        for t in [
            lm("lake_shore"),
            lm("boathouse"),
            lm("groundskeepers_cottage"),
            lm("stone_circle"),
            gate("paddock_gate"),
            gate("greenhouse_gate"),
        ] {
            assert!(spec.stops.contains(&t), "{t:?}");
        }
        let cell = |p: [f32; 3]| {
            let g = world.ground_at(p[0], p[2], p[1]).unwrap();
            world.surface(g.surface).cell.clone()
        };
        assert_eq!(cell(*stops.last().unwrap()), "entrance_hall");
        assert!(stops.iter().any(|&p| p[1] < -14.0), "down at the shore");
        // Each walker's cells: the hall, the courtyard and every grounds tile.
        let ours = s.walking(ROLE, &spec.cells);
        for c in [
            "entrance_hall",
            "courtyard",
            "grounds_r0_c3",
            "grounds_r3_c0",
        ] {
            assert!(ours.allows(c), "{c}");
        }
        assert!(!ours.allows("great_hall"));
    }

    #[test]
    fn walkers_are_spread_over_the_walking_bots() {
        let walking: Vec<bool> = (0..1000).map(|i| i % 10 != 3).collect();
        let m = members(&walking, 300);
        assert_eq!(m.iter().filter(|&&x| x).count(), 300);
        assert!(m.iter().zip(&walking).all(|(&m, &w)| !m || w));
        // Evenly: some in every tenth of the ramp.
        for chunk in m.chunks(100) {
            let n = chunk.iter().filter(|&&x| x).count();
            assert!((25..=35).contains(&n), "{n}");
        }
        assert_eq!(members(&[true, false, true], 300), vec![true, false, true]);
        assert_eq!(members(&[true; 4], 0), vec![false; 4]);
        assert_eq!(grounds(10).walkers(), 10);
    }

    /// Every leg of the tour has a route for every house, from the hall
    /// out through the courtyard gate and back, and no route crosses water:
    /// every half metre of it stands on the house's walkable ground.
    #[test]
    fn every_leg_of_the_tour_routes_on_dry_ground_for_every_house() {
        let g = grounds(1);
        let mut through_gate = 0;
        for house in protocol::House::ALL {
            let nav = g.nav(house);
            let world = nav.world();
            let spawn = world.spawns()[0].pos;
            let mut from = [
                spawn[0],
                world.ground_at(spawn[0], spawn[2], spawn[1]).unwrap().y,
                spawn[2],
            ];
            for &to in g.stops.iter() {
                let a = world
                    .ground_at(from[0], from[2], from[1])
                    .unwrap()
                    .surface
                    .0;
                let b = world.ground_at(to[0], to[2], to[1]).unwrap().surface.0;
                let route = nav
                    .route([from[0], from[2]], a, [to[0], to[2]], b)
                    .unwrap_or_else(|| panic!("{house:?}: no route {from:?} -> {to:?}"));
                let mut at = [from[0], from[2]];
                for p in route {
                    let n = (dist(at, p) / 0.5).ceil().max(1.0) as usize;
                    for k in 0..=n {
                        let t = k as f32 / n as f32;
                        let q = [at[0] + (p[0] - at[0]) * t, at[1] + (p[1] - at[1]) * t];
                        assert!(
                            world.is_walkable(q[0], q[1]),
                            "{house:?}: {q:?} is off the ground"
                        );
                        // Out through the gate's passage.
                        if (90.0..92.0).contains(&q[1]) {
                            assert!((12.0..20.0).contains(&q[0]), "{q:?}");
                            through_gate += 1;
                        }
                    }
                    at = p;
                }
                from = to;
            }
        }
        assert!(through_gate > 0);
    }

    /// Stops are drawn round each stop in turn, on reachable ground out of
    /// the keep-outs, and strays go anywhere in the walkers' cells.
    #[test]
    fn a_tour_visits_its_stops_in_turn() {
        let g = grounds(1);
        let nav = g.nav(protocol::House::Skytalon);
        let mut rng = StdRng::seed_from_u64(9);
        let mut tour = g.tour(&mut rng);
        tour.stray = 0.0;
        let first = tour.next;
        for k in 0..3 * g.stops.len() {
            let stop = g.stops[(first + k) % g.stops.len()];
            let (s, p) = tour.next_target(nav, &mut rng);
            assert!(
                dist(p, [stop[0], stop[2]]) <= g.spec.jitter + 1e-3,
                "{p:?} {stop:?}"
            );
            assert!(nav.is_reachable(s) && !nav.blocked_at(p, s));
            let surf = &nav.world().surfaces()[s as usize];
            assert!(p[0] >= surf.min[0] && p[0] <= surf.max[0]);
            assert!(p[1] >= surf.min[1] && p[1] <= surf.max[1]);
        }
        tour.stray = 1.0;
        let on_grounds = (0..400)
            .filter(|_| {
                let (s, _) = tour.next_target(nav, &mut rng);
                nav.world().surfaces()[s as usize]
                    .cell
                    .starts_with("grounds_")
            })
            .count();
        assert!(on_grounds > 300, "{on_grounds}");
    }

    /// `spread` weights the grounds down: about the share of its
    /// destinations the old greybox grounds had (120 x 120 m against the
    /// castle's floors), not the 60 % their new area alone would draw.
    #[test]
    fn spread_keeps_most_of_its_crowd_in_the_castle() {
        let world = castle();
        let nav = Nav::new(world.clone(), &Scenario::named("spread").unwrap());
        let mut rng = StdRng::seed_from_u64(4);
        let n = 20_000;
        let outside = (0..n)
            .filter(|_| {
                let (s, _) = nav.random_target(&mut rng);
                world.surfaces()[s as usize].cell.starts_with("grounds_")
            })
            .count();
        let share = outside as f64 / n as f64;
        assert!((0.3..0.5).contains(&share), "{share}");
    }
}
