//! A bot's legs: picks destinations, follows routes from [`Nav`] and moves with
//! `sim_core::move_and_slide` at walk or run speed, exactly as the browser
//! client moves its player. Pauses at each destination like a player looking
//! around. A walker with a [`Tour`] (the `grounds` scenario's
//! walkers) takes its destinations from the tour instead.

use std::collections::VecDeque;

use protocol::Anim;
use rand::Rng;
use sim_core::move_and_slide;

use crate::grounds::Tour;
use crate::nav::Nav;

/// Waypoints closer than this count as reached (metres).
const ARRIVE: f32 = 0.05;
/// Moving less than this fraction of the intended distance counts as blocked.
const BLOCKED_FRACTION: f32 = 0.5;
/// Blocked for this long (seconds), the walker gives up and picks a new destination.
const STUCK_AFTER: f32 = 1.0;
/// Pause at each destination (seconds).
const PAUSE: std::ops::Range<f32> = 1.0..8.0;
/// Share of trips made at run speed.
const RUN_CHANCE: f64 = 0.3;
/// Longest step simulated at once (seconds), so a stalled bot never sends one
/// giant move.
const MAX_DT: f32 = 0.25;

#[derive(Debug, Clone, Copy, PartialEq)]
enum Mode {
    Pausing { until: f32 },
    Walking { speed: f32 },
}

/// Counters a walker keeps about its own travels.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct WalkStats {
    pub arrivals: u64,
    /// Gave up on a destination after being blocked.
    pub stuck: u64,
    /// No route from where the walker stood.
    pub unroutable: u64,
    /// Steps that ended on a keep-out footprint (castle D1: the Great Hall's
    /// tables; zero unless a correction put the bot there).
    pub in_keep_out: u64,
}

impl WalkStats {
    pub fn plus(self, o: WalkStats) -> WalkStats {
        WalkStats {
            arrivals: self.arrivals + o.arrivals,
            stuck: self.stuck + o.stuck,
            unroutable: self.unroutable + o.unroutable,
            in_keep_out: self.in_keep_out + o.in_keep_out,
        }
    }
}

pub struct Walker {
    pub pos: [f32; 3],
    pub yaw: f32,
    pub anim: Anim,
    path: VecDeque<[f32; 2]>,
    mode: Mode,
    clock: f32,
    blocked_for: f32,
    pub stats: WalkStats,
    /// Where the walker goes next, if it tours the grounds.
    pub tour: Option<Tour>,
    /// Where the current trip ends (re-routed there when the
    /// staircase changes, [`Walker::replan`]).
    dest: Option<[f32; 3]>,
}

impl Walker {
    /// A walker standing at `pos`, about to choose its first destination.
    pub fn new(pos: [f32; 3], yaw: f32) -> Walker {
        Walker {
            pos,
            yaw,
            anim: Anim::Idle,
            path: VecDeque::new(),
            mode: Mode::Pausing { until: 0.0 },
            clock: 0.0,
            blocked_for: 0.0,
            stats: WalkStats::default(),
            tour: None,
            dest: None,
        }
    }

    /// The same walker on a tour of the grounds.
    pub fn with_tour(mut self, tour: Tour) -> Walker {
        self.tour = Some(tour);
        self
    }

    /// The server put us somewhere else: stand there and plan again.
    pub fn corrected(&mut self, pos: [f32; 3]) {
        self.pos = pos;
        self.path.clear();
        self.dest = None;
        self.mode = Mode::Pausing { until: self.clock };
    }

    /// Advances `dt` seconds of real time.
    pub fn step(&mut self, nav: &Nav, rng: &mut impl Rng, dt: f32) {
        let dt = dt.clamp(0.0, MAX_DT);
        self.clock += dt;
        if let Mode::Pausing { until } = self.mode {
            if self.clock < until {
                self.anim = Anim::Idle;
                return;
            }
            if !self.plan(nav, rng) {
                self.stats.unroutable += 1;
                self.pause(rng);
                self.anim = Anim::Idle;
                return;
            }
        }
        let Mode::Walking { speed } = self.mode else {
            unreachable!("planned a trip");
        };
        self.anim = if speed > nav.params().walk_speed {
            Anim::Run
        } else {
            Anim::Walk
        };

        let mut budget = speed * dt;
        let mut blocked = false;
        while budget > 1e-4 {
            let Some(&[wx, wz]) = self.path.front() else {
                break;
            };
            let (dx, dz) = (wx - self.pos[0], wz - self.pos[2]);
            let d = (dx * dx + dz * dz).sqrt();
            if d <= ARRIVE {
                self.path.pop_front();
                continue;
            }
            let len = budget.min(d);
            let next = move_and_slide(
                nav.world(),
                nav.params(),
                self.pos,
                [dx / d * len, dz / d * len],
            );
            let moved = ((next[0] - self.pos[0]).powi(2) + (next[2] - self.pos[2]).powi(2)).sqrt();
            self.pos = next;
            // Yaw 0 faces +Z; forward is (sin yaw, cos yaw).
            self.yaw = dx.atan2(dz);
            budget -= len;
            if moved < len * BLOCKED_FRACTION {
                blocked = true;
                break;
            }
        }
        if nav.keep_out_at(self.pos).is_some() {
            self.stats.in_keep_out += 1;
        }

        if self.path.is_empty() {
            self.stats.arrivals += 1;
            self.pause(rng);
        } else if blocked {
            self.blocked_for += dt;
            if self.blocked_for >= STUCK_AFTER {
                self.stats.stuck += 1;
                self.path.clear();
                self.pause(rng);
            }
        } else {
            self.blocked_for = 0.0;
        }
    }

    /// Walks to `to` (at walk speed) and stops there: an errand someone
    /// else chose (the `exchange` scenario). After arriving the walker
    /// pauses as usual; call [`Walker::step`] only while [`Walker::on_errand`].
    /// False if there is no route from where it stands.
    pub fn errand(&mut self, nav: &Nav, to: [f32; 2]) -> bool {
        let Some(here) = nav.world().ground_at(self.pos[0], self.pos[2], self.pos[1]) else {
            return false;
        };
        let Some(there) = nav.world().ground_at(to[0], to[1], self.pos[1]) else {
            return false;
        };
        let Some(route) = nav.route(
            [self.pos[0], self.pos[2]],
            here.surface.0,
            to,
            there.surface.0,
        ) else {
            return false;
        };
        self.path = route.into();
        self.dest = Some([to[0], there.y, to[1]]);
        self.blocked_for = 0.0;
        self.mode = Mode::Walking {
            speed: nav.params().walk_speed,
        };
        true
    }

    /// The staircase changed (`nav` is the new state's): a walker
    /// on its way routes again from where it stands to the same
    /// destination, or, when no way leads there now, stops to choose
    /// another. False when it was not on its way.
    pub fn replan(&mut self, nav: &Nav) -> bool {
        let (Mode::Walking { .. }, Some(dest)) = (self.mode, self.dest) else {
            return false;
        };
        if self.path.is_empty() {
            return false;
        }
        let world = nav.world();
        let route = world
            .ground_at(self.pos[0], self.pos[2], self.pos[1])
            .zip(world.ground_at(dest[0], dest[2], dest[1]))
            .and_then(|(here, there)| {
                nav.route(
                    [self.pos[0], self.pos[2]],
                    here.surface.0,
                    [dest[0], dest[2]],
                    there.surface.0,
                )
            });
        match route {
            Some(route) => self.path = route.into(),
            None => {
                self.path.clear();
                self.mode = Mode::Pausing { until: self.clock };
            }
        }
        self.blocked_for = 0.0;
        true
    }

    /// Walking a route (an errand or a trip of its own).
    pub fn on_errand(&self) -> bool {
        matches!(self.mode, Mode::Walking { .. })
    }

    /// Stands still (the errand is over): idle until the next step.
    pub fn stand(&mut self) {
        self.anim = Anim::Idle;
    }

    fn pause(&mut self, rng: &mut impl Rng) {
        self.blocked_for = 0.0;
        self.mode = Mode::Pausing {
            until: self.clock + rng.gen_range(PAUSE),
        };
    }

    /// Chooses a destination and a route to it from where the walker stands.
    fn plan(&mut self, nav: &Nav, rng: &mut impl Rng) -> bool {
        let Some(here) = nav.world().ground_at(self.pos[0], self.pos[2], self.pos[1]) else {
            return false;
        };
        for _ in 0..4 {
            let (surface, to) = match self.tour.as_mut() {
                Some(t) => t.next_target(nav, rng),
                None => nav.random_target(rng),
            };
            if let Some(route) = nav.route([self.pos[0], self.pos[2]], here.surface.0, to, surface)
            {
                self.path = route.into();
                let y = nav.world().surfaces()[surface as usize].height_at(to[0], to[1]);
                self.dest = Some([to[0], y, to[1]]);
                let p = nav.params();
                let speed = if rng.gen_bool(RUN_CHANCE) {
                    p.run_speed
                } else {
                    p.walk_speed
                };
                self.mode = Mode::Walking { speed };
                return true;
            }
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use rand::SeedableRng;
    use rand::rngs::StdRng;
    use sim_core::{MoveCheck, check_move};

    use super::*;
    use crate::nav::Scenario;
    use crate::nav::tests::castle;

    /// Walks bots around the real castle at 10 Hz (with timing jitter) and
    /// checks every move the way the server does: none may be rejected, and
    /// the bots must actually get places.
    fn walk_and_validate(scenario: &str, bots: usize, seconds: f32) -> WalkStats {
        walk_scenario(Scenario::named(scenario).unwrap(), &[], bots, seconds).0
    }

    /// As [`walk_and_validate`] for any scenario; also counts the steps that
    /// end on one of `footprints` (whatever the scenario's own keep-outs).
    fn walk_scenario(
        scenario: Scenario,
        footprints: &[crate::nav::KeepOut],
        bots: usize,
        seconds: f32,
    ) -> (WalkStats, u64) {
        let world = castle();
        let nav = Nav::new(world.clone(), &scenario);
        let mut on_footprints = 0;
        let mut rng = StdRng::seed_from_u64(42);
        let mut total = WalkStats::default();
        for b in 0..bots {
            let starts = crate::nav::bot_starts(&world);
            let (spawn, yaw) = starts[b % starts.len()];
            let g = world.ground_at(spawn[0], spawn[2], spawn[1]).unwrap();
            let mut w = Walker::new([spawn[0], g.y, spawn[2]], yaw);
            let mut t = 0.0;
            while t < seconds {
                let dt = rng.gen_range(0.08..0.13);
                t += dt;
                let prev = w.pos;
                w.step(&nav, &mut rng, dt);
                match check_move(&world, nav.params(), prev, w.pos, dt) {
                    MoveCheck::Accept { pos } => {
                        assert!(
                            (pos[1] - w.pos[1]).abs() < 1e-3,
                            "height disagrees at {pos:?}"
                        )
                    }
                    MoveCheck::Reject(r) => {
                        panic!("bot {b} rejected ({r:?}) {prev:?} -> {:?}", w.pos)
                    }
                }
                if footprints.iter().any(|k| k.contains([w.pos[0], w.pos[2]])) {
                    on_footprints += 1;
                }
                if scenario.cells.is_some() && w.stats.arrivals > 0 {
                    let s = world.ground_at(w.pos[0], w.pos[2], w.pos[1]).unwrap();
                    let cell = &world.surfaces()[s.surface.0 as usize].cell;
                    assert!(scenario.allows(cell), "packed bot wandered into {cell}");
                }
            }
            total.arrivals += w.stats.arrivals;
            total.stuck += w.stats.stuck;
            total.unroutable += w.stats.unroutable;
            total.in_keep_out += w.stats.in_keep_out;
        }
        eprintln!(
            "{} bots, {seconds} s each: {total:?}, {on_footprints} steps on the footprints",
            scenario.name
        );
        (total, on_footprints)
    }

    #[test]
    fn spread_bots_walk_legally() {
        let s = walk_and_validate("spread", 40, 300.0);
        // Four arrivals a bot in 300 s (70% of trips at the 3.0 m/s walk).
        assert!(s.arrivals >= 40 * 4, "{s:?}");
        assert_eq!(s.unroutable, 0, "{s:?}");
        assert!(s.stuck * 50 <= s.arrivals, "too many stuck walkers: {s:?}");
    }

    #[test]
    fn packed_bots_walk_legally_and_stay_inside() {
        let s = walk_and_validate("packed", 40, 300.0);
        assert!(s.arrivals >= 40 * 10, "{s:?}");
        assert_eq!(s.unroutable, 0, "{s:?}");
        assert!(s.stuck * 50 <= s.arrivals, "too many stuck walkers: {s:?}");
    }

    /// Castle D1 (docs/art/castle-kit.md): the packed crowd walks round
    /// the Great Hall's tables and benches; without the keep-outs the same
    /// bots still never step on them (solid furniture: the layout's solids
    /// hold every move off them, and the graph walks round them).
    #[test]
    fn packed_bots_walk_round_the_great_hall_tables() {
        let packed = Scenario::named("packed").unwrap();
        let footprints = packed.keep_out.clone();
        assert!(footprints.iter().any(|k| k.id == "table_1"));
        let (s, on) = walk_scenario(packed.clone(), &footprints, 40, 300.0);
        assert_eq!(on, 0, "steps on the furniture: {s:?}");
        assert_eq!(s.in_keep_out, 0, "{s:?}");
        assert!(s.arrivals >= 40 * 10, "{s:?}");
        assert_eq!(s.unroutable, 0, "{s:?}");
        assert!(s.stuck * 50 <= s.arrivals, "too many stuck walkers: {s:?}");
        let before = Scenario {
            keep_out: Vec::new(),
            ..packed
        };
        let (s, crossed) = walk_scenario(before, &footprints, 40, 300.0);
        assert_eq!(crossed, 0, "the tables are solid: no step on them ({s:?})");
        assert_eq!(s.unroutable, 0, "{s:?}");
        assert!(s.stuck * 50 <= s.arrivals, "too many stuck walkers: {s:?}");
    }

    /// Grounds walkers of every house tour the hilly grounds from
    /// the hall and back, every move checked the way the server checks it
    /// (none refused, feet on the hill), round the grounds' keep-outs, and
    /// between them reach every stop, down to the shallows.
    #[test]
    fn grounds_walkers_tour_the_hills_legally() {
        let g = crate::grounds::tests::grounds(1);
        let mut rng = StdRng::seed_from_u64(33);
        let mut total = WalkStats::default();
        let mut nearest = vec![f32::MAX; g.stops.len()];
        let mut lowest = f32::MAX;
        for b in 0..24 {
            let house = protocol::House::ALL[b % 4];
            let nav = g.nav(house);
            let world = nav.world();
            let starts = crate::nav::bot_starts(world);
            let (spawn, yaw) = starts[b % starts.len()];
            let y = world.ground_at(spawn[0], spawn[2], spawn[1]).unwrap().y;
            let mut w = Walker::new([spawn[0], y, spawn[2]], yaw).with_tour(g.tour(&mut rng));
            let mut t = 0.0;
            while t < 600.0 {
                let dt = rng.gen_range(0.08..0.13);
                t += dt;
                let prev = w.pos;
                w.step(nav, &mut rng, dt);
                match check_move(world, nav.params(), prev, w.pos, dt) {
                    MoveCheck::Accept { pos } => {
                        assert!(
                            (pos[1] - w.pos[1]).abs() < 1e-3,
                            "height disagrees at {pos:?}"
                        )
                    }
                    MoveCheck::Reject(r) => {
                        panic!("walker {b} rejected ({r:?}) {prev:?} -> {:?}", w.pos)
                    }
                }
                let cell = &world.surfaces()[world
                    .ground_at(w.pos[0], w.pos[2], w.pos[1])
                    .unwrap()
                    .surface
                    .0 as usize]
                    .cell;
                assert!(
                    // The pitch's stands stand on the grounds.
                    cell == "entrance_hall"
                        || cell == "courtyard"
                        || cell == "pitch"
                        || cell.starts_with("grounds_"),
                    "walker {b} wandered into {cell}"
                );
                lowest = lowest.min(w.pos[1]);
                for (k, stop) in g.stops.iter().enumerate() {
                    let d = (w.pos[0] - stop[0]).hypot(w.pos[2] - stop[2]);
                    nearest[k] = nearest[k].min(d);
                }
            }
            total = total.plus(w.stats);
        }
        eprintln!("grounds walkers: {total:?}, nearest {nearest:?}, lowest {lowest}");
        assert!(total.arrivals >= 24 * 8, "{total:?}");
        assert_eq!(total.unroutable, 0, "{total:?}");
        assert_eq!(total.in_keep_out, 0, "{total:?}");
        assert!(
            total.stuck * 50 <= total.arrivals,
            "too many stuck walkers: {total:?}"
        );
        for (k, d) in nearest.iter().enumerate() {
            assert!(
                *d <= g.spec.jitter + 0.5,
                "stop {k} {:?} never reached ({d} m)",
                g.stops[k]
            );
        }
        assert!(lowest < -14.0, "never down at the shore: {lowest}");
    }

    #[test]
    fn a_correction_resets_the_route() {
        let world = castle();
        let nav = Nav::new(world.clone(), &Scenario::named("packed").unwrap());
        let mut rng = StdRng::seed_from_u64(3);
        let spawn = crate::nav::bot_starts(&world)[0].0;
        let mut w = Walker::new(spawn, 0.0);
        for _ in 0..20 {
            w.step(&nav, &mut rng, 0.1);
        }
        w.corrected(spawn);
        assert_eq!(w.pos, spawn);
        assert!(w.path.is_empty());
        w.step(&nav, &mut rng, 0.1);
        assert_ne!(w.anim, Anim::Idle, "replans at once after a correction");
    }
}
