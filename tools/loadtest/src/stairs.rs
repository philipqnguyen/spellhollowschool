//! The Grand Staircase's moving flights for the bots
//! (`data/world/stairs.json`, `sim_core::stairs`; docs/architecture.md).
//!
//! The schedule is a pure function of the server's clock, so a bot computes
//! it as the browser does, from `Welcome.unix_ms` refined by each `Pong`:
//!
//! - **State-aware navigation** ([`CastleNavs`]): one [`Nav`] per house and
//!   staircase state, built once, for scenarios whose cells hold a moving
//!   flight (others share one per house: their bots never set foot on one).
//! - **Each switch** ([`Rider::follow`]): when the bot's clock reaches a new
//!   state, a bot standing on a flight that switched carries itself to the
//!   same spot of its new position (`Stairs::carry`, as the server carries
//!   it), takes the new state's navigation and re-plans its route to the
//!   same destination.
//! - **While its flight swings** ([`Rider::held`]) a bot standing on it
//!   holds still, as the browser holds the player's input.
//! - The `stairs` scenario's walkers tour the Grand Staircase's landings,
//!   balconies and corbel, up through every moving flight and down again
//!   (`scenarios.json` `stairs`, toured like the grounds walkers:
//!   [`crate::grounds`]).
//! - `--stairs-period-s S` puts the bots on the development period, which
//!   the server must share (`SHS_STAIRS_PERIOD_S=S` with `SHS_DEV=1`).

use std::sync::Arc;

use protocol::House;
use serde::Serialize;
use sim_core::{FlightPhase, Layout, StairPosition, Stairs, World};
use tokio::time::Instant;

use crate::nav::{Nav, Scenario};
use crate::walker::Walker;

/// The role the `stairs` scenario's walkers report under.
pub const ROLE: &str = "stair walker";

/// The shipped schedule.
const STAIRS: &str = include_str!("../../../data/world/stairs.json");

/// The moving flights of the castle the bots walk: the schedule and the
/// layout with both positions of every flight.
pub struct Staircase {
    pub stairs: Stairs,
    pub layout: Layout,
}

impl Staircase {
    /// The shipped schedule over `layout` (both positions of each flight),
    /// on a period of `period_s` seconds if given; `None` when the layout
    /// has no moving flights.
    pub fn new(layout: &Layout, period_s: Option<u32>) -> Result<Option<Staircase>, String> {
        if layout.flights().is_empty() {
            return Ok(None);
        }
        let mut stairs = Stairs::from_json(STAIRS, layout)
            .map_err(|e| format!("data/world/stairs.json: {e}"))?;
        if let Some(p) = period_s {
            stairs = stairs
                .with_period(p)
                .map_err(|e| format!("--stairs-period-s {p}: {e}"))?;
        }
        Ok(Some(Staircase {
            stairs,
            layout: layout.clone(),
        }))
    }

    /// Flight `f`'s position in staircase state `state`.
    fn position(state: u8, f: usize) -> StairPosition {
        if state >> f & 1 == 1 {
            StairPosition::B
        } else {
            StairPosition::A
        }
    }

    /// The moving flight `pos` stands on in `world` (the house's world of
    /// some state), if any.
    pub fn flight_under(&self, world: &World, pos: [f32; 3]) -> Option<usize> {
        let g = world.ground_at(pos[0], pos[2], pos[1])?;
        let id = &world.surface(g.surface).id;
        self.stairs
            .flights()
            .iter()
            .position(|f| f.a == *id || f.b == *id)
    }

    /// `pos` on `world` (state `from`) as it stands in state `to`: carried
    /// to the same spot of a flight that switched, else unchanged (who
    /// rides: `Stairs::carry_rider`, as the server and the browser decide).
    pub fn carry(&self, world: &World, pos: [f32; 3], from: u8, to: u8) -> [f32; 3] {
        (0..self.stairs.flights().len())
            .filter(|&f| Self::position(from, f) != Self::position(to, f))
            .find_map(|f| {
                let (a, b) = (Self::position(from, f), Self::position(to, f));
                self.stairs.carry_rider(&self.layout, world, pos, f, a, b)
            })
            .unwrap_or(pos)
    }
}

/// Navigation per house and staircase state.
pub struct CastleNavs {
    /// `by_state[s][house]`; one entry when the scenario's bots never walk
    /// a moving flight (or the layout has none).
    by_state: Vec<[Arc<Nav>; 4]>,
}

impl CastleNavs {
    /// The navigation of `scenario` over `layout` (the castle with both
    /// positions of each moving flight; a layout with only one position
    /// walks that one).
    pub fn new(layout: &Layout, scenario: &Scenario) -> Result<CastleNavs, String> {
        let both = layout.surfaces.iter().any(|s| {
            s.moving
                .as_ref()
                .is_some_and(|m| m.position == StairPosition::B)
        });
        let walked = layout
            .surfaces
            .iter()
            .any(|s| s.moving.is_some() && scenario.allows(&s.cell));
        let states = if both && walked {
            1usize << layout.flights().len()
        } else {
            1
        };
        let mut by_state = Vec::with_capacity(states);
        for s in 0..states {
            let world = World::new(layout.for_stairs(s as u8)).map_err(|e| e.to_string())?;
            let mut navs = Vec::with_capacity(4);
            for house in House::ALL {
                let w = world.for_house(house).map_err(|e| e.to_string())?;
                navs.push(Arc::new(Nav::new(Arc::new(w), scenario)));
            }
            let Ok(navs) = navs.try_into() else {
                unreachable!("one per house");
            };
            by_state.push(navs);
        }
        Ok(CastleNavs { by_state })
    }

    /// `house`'s navigation in staircase state `state`.
    pub fn nav(&self, house: House, state: u8) -> &Arc<Nav> {
        let h = House::ALL.iter().position(|&x| x == house).unwrap_or(0);
        &self.by_state[usize::from(state) % self.by_state.len()][h]
    }

    /// Whether the navigation differs between staircase states.
    pub fn per_state(&self) -> bool {
        self.by_state.len() > 1
    }
}

/// What the bots did about the moving staircases.
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct StairStats {
    /// Staircase states the bots' clocks moved into (summed over bots).
    pub switches_seen: u64,
    /// Times a bot carried itself on a switching flight.
    pub rides: u64,
    /// Routes planned again after a switch.
    pub replans: u64,
    /// Frames a bot held still on a swinging flight.
    pub held_frames: u64,
}

impl StairStats {
    pub fn add(&mut self, o: &StairStats) {
        self.switches_seen += o.switches_seen;
        self.rides += o.rides;
        self.replans += o.replans;
        self.held_frames += o.held_frames;
    }
}

/// One bot's view of the staircases: its estimate of the server's clock and
/// the state it walks.
#[derive(Debug, Clone)]
pub struct Rider {
    /// The server's Unix ms minus this bot's monotonic ms.
    offset_ms: i64,
    origin: Instant,
    /// The staircase state the bot walks.
    pub state: u8,
}

impl Rider {
    /// The server's clock read `unix_ms` at `now` (`Welcome`).
    pub fn new(stairs: &Staircase, unix_ms: u64, now: Instant) -> Rider {
        let offset_ms = unix_ms as i64;
        Rider {
            offset_ms,
            origin: now,
            state: stairs.stairs.state(offset_ms),
        }
    }

    /// The server's Unix ms at `now`, as this bot reckons it.
    pub fn server_ms(&self, now: Instant) -> i64 {
        self.offset_ms + now.saturating_duration_since(self.origin).as_millis() as i64
    }

    /// A `Pong`: the server's clock read `server_ms` about half the round
    /// trip `rtt_ms` before `now`.
    pub fn pong(&mut self, server_ms: u64, rtt_ms: f32, now: Instant) {
        let since = now.saturating_duration_since(self.origin).as_millis() as i64;
        self.offset_ms = server_ms as i64 + (rtt_ms / 2.0).round() as i64 - since;
    }

    /// Follows the clock: on a new staircase state the walker (on `world`,
    /// its navigation's world of the old state) is carried if it stands on a
    /// flight that switched. Returns the new state, if it changed.
    pub fn follow(
        &mut self,
        stairs: &Staircase,
        world: &World,
        walker: &mut Walker,
        now: Instant,
        stats: &mut StairStats,
    ) -> Option<u8> {
        let next = stairs.stairs.state(self.server_ms(now));
        if next == self.state {
            return None;
        }
        stats.switches_seen += 1;
        let carried = stairs.carry(world, walker.pos, self.state, next);
        if carried != walker.pos {
            stats.rides += 1;
            walker.pos = carried;
        }
        self.state = next;
        Some(next)
    }

    /// Whether the walker at `pos` (on `world`, this state's) stands on a
    /// flight that is swinging: it holds still until the switch.
    pub fn held(&self, stairs: &Staircase, world: &World, pos: [f32; 3], now: Instant) -> bool {
        stairs.flight_under(world, pos).is_some_and(|f| {
            matches!(
                stairs.stairs.flight_state(f, self.server_ms(now)).phase,
                FlightPhase::Swing { .. }
            )
        })
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use rand::rngs::StdRng;
    use rand::{Rng, SeedableRng};
    use sim_core::{MoveCheck, Worlds, check_move_near};

    use super::*;
    use crate::nav::tests::CASTLE;

    /// The castle with both positions of every moving flight.
    pub fn castle_all() -> Layout {
        Layout::from_json(CASTLE).expect("castle layout loads")
    }

    #[test]
    fn the_stairs_scenario_walks_per_state_and_the_halls_do_not() {
        let layout = castle_all();
        let stairs = Scenario::named("stairs").unwrap();
        let spec = stairs.stairs.as_ref().expect("stair walkers");
        assert_eq!(spec.walkers, 300);
        assert_eq!(stairs.cells, Scenario::named("packed").unwrap().cells);
        let walkers = stairs.walking(ROLE, &spec.cells);
        assert!(CastleNavs::new(&layout, &walkers).unwrap().per_state());
        let packed = Scenario::named("packed").unwrap();
        assert!(!CastleNavs::new(&layout, &packed).unwrap().per_state());
        // A layout at rest (one position of each flight) walks as it is.
        let rest = layout.for_stairs(0);
        assert!(!CastleNavs::new(&rest, &walkers).unwrap().per_state());
        assert!(Staircase::new(&layout, Some(20)).unwrap().is_some());
        assert!(Staircase::new(&layout, Some(10)).is_err());
    }

    /// Stair walkers tour the Grand Staircase on the development period, as
    /// the bots do: their clock 0-300 ms off the server's either way, they
    /// carry themselves at each switch, hold still on a swinging flight and
    /// re-plan in the new state; every move is checked as the server checks
    /// it (the state's world, either side of a switch within the grace,
    /// with the server's own carry): none is refused, and the walkers get
    /// places through the moving flights.
    #[test]
    fn stair_walkers_cross_the_switches_legally() {
        let layout = castle_all();
        let stairs = Staircase::new(&layout, Some(20)).unwrap().unwrap();
        let g = crate::grounds::tests::stair_walkers(&layout, 1);
        let mut rng = StdRng::seed_from_u64(37);
        let mut total = crate::walker::WalkStats::default();
        let mut stats = StairStats::default();
        let t0 = Instant::now();
        let mut flights_used = [false; 3];
        for b in 0..8 {
            let house = House::ALL[b % 4];
            let server_worlds = Worlds::new(&layout.for_house(house)).unwrap();
            let unix0 = 5_000_000 + b as i64 * 3_100;
            let error = [-300i64, -120, 0, 90, 300][b % 5];
            let mut rider = Rider::new(&stairs, (unix0 + error) as u64, t0);
            // The server's own state and the walker's stored position there.
            let mut server_state = stairs.stairs.state(unix0);
            let nav = g.nav_in(house, rider.state);
            let spawn = crate::nav::bot_starts(nav.world())[b % 2].0;
            let mut w = Walker::new(spawn, 0.0).with_tour(g.tour(&mut rng));
            let mut server_pos = spawn;
            let mut t = 0.0f32;
            while t < 240.0 {
                let dt: f32 = rng.gen_range(0.08..0.13);
                t += dt;
                let now = t0 + std::time::Duration::from_secs_f32(t);
                let ms = unix0 + (t * 1000.0) as i64;
                // The server's switch: its riders carried.
                let s = stairs.stairs.state(ms);
                if s != server_state {
                    server_pos =
                        stairs.carry(server_worlds.get(server_state), server_pos, server_state, s);
                    server_state = s;
                }
                let old = g.nav_in(house, rider.state).clone();
                if rider
                    .follow(&stairs, old.world(), &mut w, now, &mut stats)
                    .is_some()
                {
                    let nav = g.nav_in(house, rider.state);
                    if w.replan(nav) {
                        stats.replans += 1;
                    }
                }
                let nav = g.nav_in(house, rider.state);
                if let Some(f) = stairs.flight_under(nav.world(), w.pos) {
                    flights_used[f] = true;
                }
                let before = w.pos;
                if rider.held(&stairs, nav.world(), w.pos, now) {
                    stats.held_frames += 1;
                    w.stand();
                } else {
                    w.step(nav, &mut rng, dt);
                }
                // The server's check: prev carried into each state near.
                let near = stairs.stairs.states_near(ms);
                let mut ok = None;
                for &st in &near {
                    let from = stairs.carry(
                        server_worlds.get(server_state),
                        server_pos,
                        server_state,
                        st,
                    );
                    if let (MoveCheck::Accept { pos }, Some(_)) =
                        check_move_near(&server_worlds, &[st], nav.params(), from, w.pos, dt)
                    {
                        ok = Some(stairs.carry(server_worlds.get(st), pos, st, server_state));
                        break;
                    }
                }
                server_pos = ok.unwrap_or_else(|| {
                    panic!(
                        "walker {b} ({error} ms off) refused at {ms}: {before:?} -> {:?} (bot state {:03b}, server {:03b})",
                        w.pos, rider.state, server_state
                    )
                });
            }
            total = total.plus(w.stats);
        }
        eprintln!("stair walkers: {total:?}, {stats:?}, flights used {flights_used:?}");
        assert!(total.arrivals >= 8 * 4, "{total:?}");
        assert_eq!(total.unroutable, 0, "{total:?}");
        assert!(stats.switches_seen >= 8 * 30, "{stats:?}");
        assert!(stats.replans > 0 && stats.rides > 0, "{stats:?}");
        assert!(
            flights_used.iter().all(|&u| u),
            "every moving flight walked: {flights_used:?}"
        );
    }
}
