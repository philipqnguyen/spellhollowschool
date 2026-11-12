//! The `exchange` scenario (castle kit M2, docs/art/castle-kit.md): a
//! crowd in the Owl Exchange court of the Entrance Hall for the client's
//! frame-time test.
//!
//! - **Standers** (the first `standers` bots, then each `more` area's: castle
//!   M5c's Exchange Yard out through the Owl Gate) are moved by the dev teleport
//!   (`POST /api/dev/teleport`) to their own spot in the court, every spot
//!   outside the counter's reach ring and `spacing` apart, and stand there
//!   (waving, bowing and sitting like other standing bots). Now and then a
//!   share of them (`open_share`) walks into the ring, uses the counter
//!   (`Interact`, answered by `StationOpened`) and walks back to its spot.
//! - **Walkers** (the other bots) walk from where they joined (the hall's
//!   spawns) to the edge of the ring and back, over and over.
//!
//! With `--bots 50` and an observer in the browser that is the contract's
//! 51 characters. Where the court and the counter are lives in
//! `scenarios.json`, not in code (CONTRIBUTING.md).

use std::f32::consts::PI;
use std::time::Duration;

use protocol::{ClientMsg, ServerMsg, StationKind};
use rand::Rng;
use serde::{Deserialize, Serialize};
use sim_core::World;
use tokio::time::Instant;

use crate::nav::Nav;
use crate::walker::Walker;

/// Spots keep this much more than the reach radius from the counter's
/// interaction point (metres): a body (0.3 m) plus a margin, so a standing
/// bot is never in reach.
const RING_MARGIN: f32 = 0.5;
/// A stander using the counter stops this far inside the reach radius (metres).
const COUNTER_INSET: f32 = 1.2;
/// Walkers turn round this far outside the reach radius (metres).
const RING_EDGE: f32 = 0.6;
/// How long a bot stays at the counter, at the ring's edge or at home (seconds).
const LINGER: std::ops::Range<f64> = 2.0..6.0;

/// `scenarios.json` `exchange`: where the court and the counter are.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct ExchangeSpec {
    /// The counter's interactable (`layout.json` `interactables[].id`).
    pub interactable: String,
    /// The court in plan: `min` and `max` as [x, z] (metres).
    pub area: Area,
    /// Parts of the court nobody stands in: the counter and the perches (plan rectangles).
    #[serde(default)]
    pub keep_out: Vec<Area>,
    /// How many bots stand in the court (the rest walk to the ring and back).
    pub standers: usize,
    /// Least distance between two standing spots (metres).
    pub spacing: f32,
    /// Share of the standers that use the counter now and then.
    pub open_share: f64,
    /// Mean time between one such stander's visits (seconds).
    pub open_every_secs: f64,
    /// More places the crowd stands, after the court's `standers` (castle M5c,
    /// docs/art/castle-kit.md: the Exchange Yard in the courtyard, out
    /// through the Owl Gate). The same spacing and `keep_out` apply; a
    /// stander there that uses the counter walks in through the gate.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub more: Vec<StandArea>,
}

/// One more standing area of the crowd (`ExchangeSpec::more`).
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct StandArea {
    /// What the place is called, for the log.
    pub name: String,
    /// The area in plan: `min` and `max` as [x, z] (metres).
    pub area: Area,
    /// How many bots stand there.
    pub standers: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize)]
pub struct Area {
    pub min: [f32; 2],
    pub max: [f32; 2],
}

impl Area {
    pub fn contains(&self, p: [f32; 2]) -> bool {
        p[0] >= self.min[0] && p[0] <= self.max[0] && p[1] >= self.min[1] && p[1] <= self.max[1]
    }
}

/// The court as the bots use it, built once from the layout.
#[derive(Debug, Clone)]
pub struct Exchange {
    pub spec: ExchangeSpec,
    pub target: String,
    /// The counter's interaction point and reach.
    pub centre: [f32; 3],
    pub radius: f32,
    /// One spot per stander, all outside the reach ring.
    pub spots: Vec<[f32; 3]>,
    /// Solid furniture: where a stander may stop to use the counter, inside
    /// the reach ring and outside the counter itself (a character fits).
    pub counter_spots: Vec<[f32; 2]>,
}

impl Exchange {
    /// The court of `spec` in `world`: the interactable, and `spec.standers`
    /// spots on walkable ground in the area, `spacing` apart and out of reach,
    /// spread over the court.
    pub fn new(world: &World, spec: &ExchangeSpec) -> Result<Exchange, String> {
        let it = world
            .interactables()
            .iter()
            .find(|i| i.id == spec.interactable)
            .ok_or_else(|| {
                format!(
                    "exchange: no interactable {:?} in the layout",
                    spec.interactable
                )
            })?;
        let centre = it.pos;
        let radius = it.radius;
        let step = spec.spacing.max(0.6);
        let mut spots = Vec::new();
        let court = std::iter::once(("the court", spec.area, spec.standers));
        let more = spec
            .more
            .iter()
            .map(|m| (m.name.as_str(), m.area, m.standers));
        for (name, area, standers) in court.chain(more) {
            let mut candidates = Vec::new();
            let mut z = area.min[1];
            while z <= area.max[1] + 1e-4 {
                let mut x = area.min[0];
                while x <= area.max[0] + 1e-4 {
                    let out_of_reach =
                        flat_dist([x, z], [centre[0], centre[2]]) >= radius + RING_MARGIN;
                    let clear = !spec.keep_out.iter().any(|k| k.contains([x, z]));
                    if out_of_reach
                        && clear
                        && let Some(g) = world.ground_at(x, z, centre[1])
                    {
                        candidates.push([x, g.y, z]);
                    }
                    x += step;
                }
                z += step;
            }
            if candidates.len() < standers {
                return Err(format!(
                    "exchange: only {} spots in {name} for {standers} standers",
                    candidates.len(),
                ));
            }
            // Spread the standers over the area: every k-th spot in reading order.
            let n = candidates.len();
            spots.extend((0..standers).map(|i| candidates[i * n / standers.max(1)]));
        }
        Ok(Exchange {
            spec: spec.clone(),
            target: spec.interactable.clone(),
            centre,
            radius,
            spots,
            counter_spots: counter_spots(world, centre, radius - COUNTER_INSET),
        })
    }

    /// The `index`-th bot's part: a stander with its spot, or a walker.
    pub fn act(&self, index: usize) -> ExchangeAct {
        let kind = match self.spots.get(index) {
            Some(&spot) => {
                // Deterministic share of the standers that use the counter.
                let opens = (((index * 37) % 100) as f64) < self.spec.open_share * 100.0;
                Kind::Stand { spot, opens }
            }
            None => Kind::Walk { home: None },
        };
        ExchangeAct {
            kind,
            phase: Phase::Start,
            next: Instant::now(),
        }
    }

    /// Where a stander at `from` stops to use the counter: inside the reach
    /// ring on the line from the interaction point towards `from`.
    pub fn counter_point(&self, from: [f32; 3]) -> [f32; 2] {
        // The ring point towards `from`, or (solid furniture: the counter
        // fills the ring's back) the nearest one a character fits on.
        let want = self.towards(from, self.radius - COUNTER_INSET);
        self.counter_spots
            .iter()
            .copied()
            .min_by(|a, b| flat_dist(*a, want).total_cmp(&flat_dist(*b, want)))
            .unwrap_or(want)
    }

    /// Where a walker from `from` turns round: just outside the reach ring.
    pub fn ring_point(&self, from: [f32; 3], rng: &mut impl Rng) -> [f32; 2] {
        // Spread round the open side of the ring (the counter's back is the wall).
        let base = (from[2] - self.centre[2]).atan2(from[0] - self.centre[0]);
        let a = base + rng.gen_range(-0.5..0.5) * PI / 2.0;
        let r = self.radius + RING_EDGE;
        [self.centre[0] + a.cos() * r, self.centre[2] + a.sin() * r]
    }

    fn towards(&self, from: [f32; 3], r: f32) -> [f32; 2] {
        let (dx, dz) = (from[0] - self.centre[0], from[2] - self.centre[2]);
        let d = (dx * dx + dz * dz).sqrt().max(1e-3);
        [self.centre[0] + dx / d * r, self.centre[2] + dz / d * r]
    }

    pub fn in_reach(&self, pos: [f32; 3]) -> bool {
        flat_dist([pos[0], pos[2]], [self.centre[0], self.centre[2]]) <= self.radius
    }

    fn mean_visit(&self) -> Duration {
        Duration::from_secs_f64(self.spec.open_every_secs.max(1.0))
    }
}

/// What a bot did in the court.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct ExchangeStats {
    /// Standers moved to their spot, and how many of them use the counter.
    pub standers: u64,
    pub openers: u64,
    /// `Interact` with the counter sent, and `StationOpened` for it received.
    pub interacts: u64,
    pub opened: u64,
    /// Visits that ended out of reach (blocked or corrected on the way).
    pub missed: u64,
    /// Walkers' round trips to the ring.
    pub ring_trips: u64,
}

impl ExchangeStats {
    pub fn add(&mut self, o: &ExchangeStats) {
        self.standers += o.standers;
        self.openers += o.openers;
        self.interacts += o.interacts;
        self.opened += o.opened;
        self.missed += o.missed;
        self.ring_trips += o.ring_trips;
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Kind {
    Stand {
        spot: [f32; 3],
        opens: bool,
    },
    /// `home`: where the walker joined (the hall's spawn), once known.
    Walk {
        home: Option<[f32; 3]>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Phase {
    /// Not in the court yet (a stander waits for its teleport).
    Start,
    Standing,
    ToCounter,
    AtCounter,
    Back,
    ToRing,
    AtRing,
    Home,
    AtHome,
}

/// One bot's part in the scenario.
#[derive(Debug, Clone)]
pub struct ExchangeAct {
    kind: Kind,
    phase: Phase,
    /// When the current wait ends.
    next: Instant,
}

impl ExchangeAct {
    /// After `Welcome`: where to teleport (standers), if anywhere.
    pub fn on_welcome(&mut self, spawn: [f32; 3], stats: &mut ExchangeStats) -> Option<[f32; 3]> {
        match &mut self.kind {
            Kind::Stand { spot, opens } => {
                stats.standers += 1;
                stats.openers += u64::from(*opens);
                Some(*spot)
            }
            Kind::Walk { home } => {
                home.get_or_insert(spawn);
                self.phase = Phase::AtHome;
                self.next = Instant::now();
                None
            }
        }
    }

    /// The teleport (or a correction) put the bot at `pos`: stand there and carry on.
    pub fn moved(&mut self, now: Instant, ex: &Exchange, rng: &mut impl Rng) {
        match self.kind {
            Kind::Stand { .. } => {
                if self.phase == Phase::Start || self.phase == Phase::Standing {
                    self.phase = Phase::Standing;
                    self.next = now + poisson(ex.mean_visit(), rng);
                }
            }
            Kind::Walk { .. } => {}
        }
    }

    /// Advances the bot at `now`; returns a message to send, if any.
    pub fn tick(
        &mut self,
        now: Instant,
        ex: &Exchange,
        walker: &mut Walker,
        nav: &Nav,
        rng: &mut impl Rng,
        stats: &mut ExchangeStats,
    ) -> Option<ClientMsg> {
        if walker.on_errand() {
            return None;
        }
        match (self.kind, self.phase) {
            (Kind::Stand { opens, .. }, Phase::Standing) => {
                if opens && now >= self.next {
                    let to = ex.counter_point(walker.pos);
                    if walker.errand(nav, to) {
                        self.phase = Phase::ToCounter;
                    } else {
                        self.next = now + poisson(ex.mean_visit(), rng);
                    }
                }
                None
            }
            (Kind::Stand { .. }, Phase::ToCounter) => {
                self.phase = Phase::AtCounter;
                self.next = now + linger(rng);
                if ex.in_reach(walker.pos) {
                    stats.interacts += 1;
                    Some(ClientMsg::Interact {
                        target: ex.target.clone(),
                    })
                } else {
                    stats.missed += 1;
                    None
                }
            }
            (Kind::Stand { spot, .. }, Phase::AtCounter) if now >= self.next => {
                if walker.errand(nav, [spot[0], spot[2]]) {
                    self.phase = Phase::Back;
                }
                None
            }
            (Kind::Stand { .. }, Phase::Back) => {
                self.phase = Phase::Standing;
                self.next = now + poisson(ex.mean_visit(), rng);
                None
            }
            (Kind::Walk { .. }, Phase::AtHome) if now >= self.next => {
                let to = ex.ring_point(walker.pos, rng);
                if walker.errand(nav, to) {
                    self.phase = Phase::ToRing;
                } else {
                    self.next = now + linger(rng);
                }
                None
            }
            (Kind::Walk { .. }, Phase::ToRing) => {
                self.phase = Phase::AtRing;
                self.next = now + linger(rng);
                None
            }
            (Kind::Walk { home: Some(home) }, Phase::AtRing) if now >= self.next => {
                if walker.errand(nav, [home[0], home[2]]) {
                    self.phase = Phase::Home;
                }
                None
            }
            (Kind::Walk { .. }, Phase::Home) => {
                stats.ring_trips += 1;
                self.phase = Phase::AtHome;
                self.next = now + linger(rng);
                None
            }
            _ => None,
        }
    }

    /// A message from the server.
    pub fn on_msg(&mut self, msg: &ServerMsg, ex: &Exchange, stats: &mut ExchangeStats) {
        if let ServerMsg::StationOpened {
            kind: StationKind::OwlExchange,
            target,
            ..
        } = msg
            && *target == ex.target
        {
            stats.opened += 1;
        }
    }

    /// The role's name in the summary.
    pub fn role_name(&self) -> &'static str {
        match self.kind {
            Kind::Stand { .. } => "court stander",
            Kind::Walk { .. } => "court walker",
        }
    }
}

/// Points every 5 degrees on the ring of radius `r` round `centre` where a
/// character fits (footprint on ground, out of every solid: the counter).
fn counter_spots(world: &World, centre: [f32; 3], r: f32) -> Vec<[f32; 2]> {
    let params = sim_core::MoveParams::default();
    (0..72)
        .map(|k| (k as f32 * 5.0).to_radians())
        .map(|a| [centre[0] + a.cos() * r, centre[2] + a.sin() * r])
        .filter(|p| {
            world.ground_at(p[0], p[1], centre[1]).is_some_and(|g| {
                sim_core::footprint_ground(world, &params, p[0], p[1], g.y).is_some()
            })
        })
        .collect()
}

fn linger(rng: &mut impl Rng) -> Duration {
    Duration::from_secs_f64(rng.gen_range(LINGER))
}

fn flat_dist(a: [f32; 2], b: [f32; 2]) -> f32 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()
}

/// An exponential wait with mean `every` (visits arrive as a Poisson process).
fn poisson(every: Duration, rng: &mut impl Rng) -> Duration {
    every.mul_f64(-(1.0 - rng.gen_range(0.0..1.0f64)).ln())
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use rand::SeedableRng;
    use rand::rngs::StdRng;
    use sim_core::{MoveCheck, check_move};

    use super::*;
    use crate::nav::Scenario;
    use crate::nav::tests::castle;

    fn court() -> (Arc<World>, Exchange, Nav) {
        let world = castle();
        let scenario = Scenario::named("exchange").unwrap();
        let spec = scenario
            .exchange
            .clone()
            .expect("the exchange scenario has a court");
        let ex = Exchange::new(&world, &spec).unwrap();
        let nav = Nav::new(world.clone(), &scenario);
        (world, ex, nav)
    }

    /// Castle M5c (docs/art/castle-kit.md): half the crowd stands in the
    /// Owl Exchange court, half in the Exchange Yard out through the Owl Gate.
    #[test]
    fn forty_spots_in_the_court_and_the_yard_out_of_reach_and_apart() {
        let (world, ex, _) = court();
        assert_eq!(ex.spec.standers, 20);
        assert_eq!(ex.spec.more.len(), 1);
        assert_eq!(ex.spec.more[0].standers, 20);
        assert_eq!(ex.spots.len(), 40);
        assert_eq!(ex.centre, [3.5, 0.0, 30.0]);
        assert!((ex.radius - 3.5).abs() < 1e-6);
        for (i, s) in ex.spots.iter().enumerate() {
            let (a, cell) = if i < 20 {
                (ex.spec.area, "entrance_hall")
            } else {
                (ex.spec.more[0].area, "courtyard")
            };
            assert!(
                s[0] >= a.min[0] && s[0] <= a.max[0] && s[2] >= a.min[1] && s[2] <= a.max[1],
                "{s:?}"
            );
            assert!(!ex.in_reach(*s), "spot {s:?} is in reach");
            assert!(
                ex.spec.keep_out.iter().all(|k| !k.contains([s[0], s[2]])),
                "spot {s:?} in a keep-out"
            );
            assert!(
                flat_dist([s[0], s[2]], [3.5, 30.0]) >= 3.5 + 0.3,
                "{s:?} inside the ring with its body"
            );
            let g = world.ground_at(s[0], s[2], 0.0).expect("on the floor");
            assert_eq!(world.surfaces()[g.surface.0 as usize].cell, cell);
            for t in &ex.spots[i + 1..] {
                assert!(flat_dist([s[0], s[2]], [t[0], t[2]]) >= ex.spec.spacing - 1e-4);
            }
        }
        // Spread over the court and over the yard, not bunched at one end.
        let span = |r: std::ops::Range<usize>, k: usize| {
            let v: Vec<f32> = ex.spots[r].iter().map(|s| s[k]).collect();
            (
                v.iter().cloned().fold(f32::MAX, f32::min),
                v.iter().cloned().fold(f32::MIN, f32::max),
            )
        };
        let (z0, z1) = span(0..20, 2);
        assert!(z0 < 25.0 && z1 > 35.0, "{z0} {z1}");
        let (x0, x1) = span(20..40, 0);
        let (z0, z1) = span(20..40, 2);
        assert!(x1 - x0 > 12.0 && z1 - z0 > 8.0, "{x0}..{x1} {z0}..{z1}");
    }

    #[test]
    fn counter_and_ring_points() {
        let (_, ex, _) = court();
        let mut rng = StdRng::seed_from_u64(3);
        for s in &ex.spots {
            let c = ex.counter_point(*s);
            assert!(ex.in_reach([c[0], 0.0, c[1]]));
            let r = ex.ring_point(*s, &mut rng);
            assert!(!ex.in_reach([r[0], 0.0, r[1]]));
        }
        assert!(matches!(ex.act(45).kind, Kind::Walk { .. }));
        let openers = (0..40)
            .filter(|&i| matches!(ex.act(i).kind, Kind::Stand { opens: true, .. }))
            .count();
        assert!(openers > 0 && openers < 40, "{openers} openers");
    }

    /// Runs one bot for `secs` of simulated time without a server: every
    /// move it would send must be legal (the server would accept it).
    fn simulate(index: usize, secs: f32) -> (ExchangeStats, Vec<[f32; 3]>) {
        let (world, ex, nav) = court();
        let mut rng = StdRng::seed_from_u64(index as u64);
        let mut stats = ExchangeStats::default();
        let spawn = [14.0, 0.0, 32.0];
        let mut act = ex.act(index);
        let mut walker = Walker::new(spawn, 0.0);
        if let Some(pos) = act.on_welcome(spawn, &mut stats) {
            walker.corrected(pos);
        }
        let start = Instant::now();
        act.moved(start, &ex, &mut rng);
        let dt = 0.05;
        let mut trace = Vec::new();
        let mut t = 0.0;
        while t < secs {
            let now = start + Duration::from_secs_f32(t);
            if act
                .tick(now, &ex, &mut walker, &nav, &mut rng, &mut stats)
                .is_some()
            {
                // The server's reach test.
                assert!(ex.in_reach(walker.pos));
            }
            if walker.on_errand() {
                let before = walker.pos;
                walker.step(&nav, &mut rng, dt);
                if let MoveCheck::Reject(r) =
                    check_move(&world, nav.params(), before, walker.pos, dt)
                {
                    panic!(
                        "bot {index} rejected ({r:?}) {before:?} -> {:?}",
                        walker.pos
                    );
                }
            }
            trace.push(walker.pos);
            t += dt;
        }
        (stats, trace)
    }

    #[test]
    fn an_opener_uses_the_counter_and_goes_back() {
        let (_, ex, _) = court();
        let i = (0..20)
            .find(|&i| matches!(ex.act(i).kind, Kind::Stand { opens: true, .. }))
            .unwrap();
        let (stats, trace) = simulate(i, 600.0);
        assert!(stats.interacts >= 2, "{stats:?}");
        assert_eq!(stats.missed, 0);
        // Back at its spot at the end of every visit, out of reach while standing.
        let spot = ex.spots[i];
        assert!(trace.iter().any(|p| ex.in_reach(*p)));
        let home = trace
            .iter()
            .filter(|p| flat_dist([p[0], p[2]], [spot[0], spot[2]]) < 0.1)
            .count();
        assert!(home > trace.len() / 2);
    }

    /// Castle M5c: an opener standing in the Exchange Yard walks in through
    /// the Owl Gate to the counter and back out to its spot, every move legal.
    #[test]
    fn an_opener_in_the_yard_comes_in_through_the_owl_gate() {
        let (_, ex, _) = court();
        let i = (20..40)
            .find(|&i| matches!(ex.act(i).kind, Kind::Stand { opens: true, .. }))
            .unwrap();
        let (stats, trace) = simulate(i, 600.0);
        assert!(stats.interacts >= 2, "{stats:?}");
        assert_eq!(stats.missed, 0);
        assert!(trace.iter().any(|p| ex.in_reach(*p)));
        // Through the gate (x 1..4, z 40..42), never through the front doors (x 12..20).
        assert!(
            trace
                .iter()
                .any(|p| p[0] > 1.0 && p[0] < 4.0 && p[2] > 40.0 && p[2] < 42.0)
        );
        assert!(
            !trace
                .iter()
                .any(|p| p[0] > 11.0 && p[2] > 39.0 && p[2] < 43.0)
        );
    }

    #[test]
    fn a_stander_that_does_not_open_never_moves() {
        let (_, ex, _) = court();
        let i = (0..40)
            .find(|&i| matches!(ex.act(i).kind, Kind::Stand { opens: false, .. }))
            .unwrap();
        let (stats, trace) = simulate(i, 120.0);
        assert_eq!(stats.interacts, 0);
        assert!(trace.iter().all(|p| *p == ex.spots[i]));
    }

    #[test]
    fn walkers_go_to_the_ring_and_back() {
        let (_, ex, _) = court();
        let (stats, trace) = simulate(42, 300.0);
        assert!(stats.ring_trips >= 3, "{stats:?}");
        assert!(
            trace.iter().all(|p| !ex.in_reach(*p)),
            "a walker stepped into the reach ring"
        );
        assert!(
            trace
                .iter()
                .any(|p| flat_dist([p[0], p[2]], [3.5, 30.0]) < ex.radius + 1.0)
        );
    }
}
