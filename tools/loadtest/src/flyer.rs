//! brooms. **Flyers** (`--fly-pct`, and the `pitch` scenario's
//! `pitch.flyers`) get the Flying skill from the dev grant (a third at
//! each broom tier: 8, 9 and 10 m/s), are moved to a launch spot on the
//! grounds with the dev teleport, mount (`Mount`) and fly loops through
//! their region at a little under their broom's speed, each leg to a random
//! point 5-40 m over the floor (`heights`); at a share of the points
//! (`hover_share`) they hover a few seconds (a hovering broom sends a move a
//! second, as a standing walker does). Now and then (`churn_per_min`: 10 %
//! of the flyers a minute) a flyer comes down at a launch spot, lands
//! (`Mount { on: false }`), stands a few seconds and takes off again. Every
//! position comes from `sim_core::flight::fly_and_slide`, as the browser
//! predicts, so the server never has a reason to correct one.
//!
//! **Pitch spectators** (`pitch.spectators`) are teleported to a spot on the
//! stands' decks or the field's edge and stand there facing the field
//! (waving, bowing and sitting like any standing bot).
//!
//! The roles report their bandwidth on their own (`flyer`, `pitch
//! spectator`); the Flying class's students fly its ring course
//! (`crate::rings`).

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use protocol::{Anim, ClientMsg, ServerMsg};
use rand::Rng;
use serde::{Deserialize, Serialize};
use sim_core::World;
use sim_core::flight::{self, Airspace, FlightRules, FlyParams};
use tokio::time::Instant;

/// The roles' names in the report.
pub const FLYER: &str = "flyer";
pub const SPECTATOR: &str = "pitch spectator";

/// Flying skill XP that reaches each broom tier (levels 1, 4 and 7 of
/// `data/crafting/recipes.json` `skill_levels`).
pub const TIER_XP: [u32; 3] = [10, 450, 1_400];

/// The shipped flight rules.
pub fn rules() -> FlightRules {
    FlightRules::from_json(include_str!("../../../data/flying.json"))
        .expect("data/flying.json is valid")
}

/// Share of the broom's top speed the bots fly at.
const CRUISE: f32 = 0.92;
/// Share of the climb speed they climb and dive at.
const CLIMB: f32 = 0.85;
/// Close enough to a point (m).
const ARRIVE: f32 = 0.3;
/// Spacing of the launch spots' grid (m).
const LAUNCH_GRID: f32 = 3.0;

/// A rectangle in plan (`x`, `z`).
#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize)]
pub struct Rect {
    pub min: [f32; 2],
    pub max: [f32; 2],
}

impl Rect {
    fn random(&self, rng: &mut impl Rng) -> [f32; 2] {
        [
            rng.gen_range(self.min[0]..=self.max[0]),
            rng.gen_range(self.min[1]..=self.max[1]),
        ]
    }
}

/// Where and how flyers fly.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct FlySpec {
    /// The flyers' region in plan (they launch, fly and land inside it).
    pub region: Rect,
    /// Heights over the floor of their points (m).
    pub heights: [f32; 2],
    /// Share of the points a flyer hovers at, for 2-8 s.
    pub hover_share: f64,
    /// Share of the flyers that land and take off again each minute.
    pub churn_per_min: f64,
}

impl FlySpec {
    /// `--fly-pct`'s flyers: the whole grounds' airspace south of the
    /// castle's no-fly box, and the lake.
    pub fn grounds() -> FlySpec {
        FlySpec {
            region: Rect {
                min: [-100.0, 106.0],
                max: [112.0, 330.0],
            },
            heights: [5.0, 40.0],
            hover_share: 0.3,
            churn_per_min: 0.1,
        }
    }
}

/// The `pitch` entry of a scenario: flyers over the pitch, spectators in
/// the stands.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct PitchSpec {
    pub flyers: usize,
    pub spectators: usize,
    pub fly: FlySpec,
    /// The stands' decks (layout surface ids) and the field's edge: where
    /// spectators stand.
    pub decks: Vec<String>,
    pub edges: Vec<Rect>,
    /// The field's middle, which spectators face.
    pub centre: [f32; 2],
}

/// A bot's part in the flying.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Member {
    /// Flies (with this broom tier's skill, 1-3).
    Flyer {
        tier: u8,
    },
    Spectator,
}

/// What the flyers and spectators of one run share.
pub struct Flight {
    pub world: Arc<World>,
    pub air: Airspace,
    pub rules: FlightRules,
    /// Per bot (by index): its part, if any.
    members: Vec<Option<(Member, usize)>>,
    /// The pitch flyers' spec and the `--fly-pct` flyers' (by member set).
    specs: Vec<FlySpec>,
    /// Ground points where a broom may lift off, per spec.
    launch: Vec<Vec<[f32; 3]>>,
    /// Where spectators stand, and which way they face.
    spots: Vec<([f32; 3], f32)>,
}

impl Flight {
    /// The flying roles: the pitch's flyers and spectators (`pitch`), then
    /// `fly_share` of the remaining walking bots (`walking[i]`) as flyers
    /// over the whole grounds.
    pub fn new(
        world: Arc<World>,
        pitch: Option<&PitchSpec>,
        fly_share: f64,
        walking: &[bool],
    ) -> Result<Flight, String> {
        let rules = rules();
        let air = Airspace::new(&world);
        let mut members: Vec<Option<(Member, usize)>> = vec![None; walking.len()];
        let mut specs = Vec::new();
        let mut spots = Vec::new();
        let mut free: Vec<bool> = walking.to_vec();
        let tier = |i: usize| (i % 3) as u8 + 1;
        if let Some(p) = pitch {
            specs.push(p.fly.clone());
            for (i, m) in crate::grounds::members(&free, p.flyers)
                .into_iter()
                .enumerate()
            {
                if m {
                    members[i] = Some((Member::Flyer { tier: tier(i) }, 0));
                    free[i] = false;
                }
            }
            for (i, m) in crate::grounds::members(&free, p.spectators)
                .into_iter()
                .enumerate()
            {
                if m {
                    members[i] = Some((Member::Spectator, 0));
                    free[i] = false;
                }
            }
            spots = spectator_spots(&world, p)?;
        }
        if fly_share > 0.0 {
            let k = specs.len();
            specs.push(FlySpec::grounds());
            let n = (free.iter().filter(|&&f| f).count() as f64 * fly_share).round() as usize;
            for (i, m) in crate::grounds::members(&free, n).into_iter().enumerate() {
                if m {
                    members[i] = Some((Member::Flyer { tier: tier(i) }, k));
                }
            }
        }
        let params = rules.params(1, protocol::BROOM_PCT_NORMAL);
        let launch: Vec<Vec<[f32; 3]>> = specs
            .iter()
            .map(|s| launch_spots(&world, &air, &params, s.region))
            .collect();
        if let Some(k) = launch.iter().position(Vec::is_empty) {
            return Err(format!("flying: no launch spot in {:?}", specs[k].region));
        }
        Ok(Flight {
            world,
            air,
            rules,
            members,
            specs,
            launch,
            spots,
        })
    }

    /// Bot `index`'s part, if any.
    pub fn member(&self, index: usize) -> Option<Member> {
        self.members.get(index).copied().flatten().map(|(m, _)| m)
    }

    /// How many bots fly and spectate.
    pub fn counts(&self) -> (usize, usize) {
        let all = self.members.iter().flatten();
        let flyers = all
            .clone()
            .filter(|(m, _)| matches!(m, Member::Flyer { .. }))
            .count();
        (flyers, all.count() - flyers)
    }

    /// Launch spots and spectator spots (the start-up line).
    pub fn spot_counts(&self) -> (usize, usize) {
        (self.launch.iter().map(Vec::len).sum(), self.spots.len())
    }

    /// Bot `index`'s act on one connection.
    pub fn act(&self, index: usize, now: Instant) -> Option<FlyAct> {
        let (member, spec) = self.members.get(index).copied().flatten()?;
        Some(FlyAct {
            member,
            spec,
            state: State::Waiting,
            pos: [0.0; 3],
            yaw: 0.0,
            tier: 0,
            target: None,
            hover_until: None,
            next_land: now,
            until: now,
            index,
        })
    }

    fn params(&self, tier: u8) -> FlyParams {
        self.rules.params(tier, protocol::BROOM_PCT_NORMAL)
    }

    /// A random flight point of spec `k`: in its region, `heights` over the
    /// floor, in open sky.
    fn point(&self, k: usize, params: &FlyParams, rng: &mut impl Rng) -> Option<[f32; 3]> {
        let s = &self.specs[k];
        for _ in 0..16 {
            let [x, z] = s.region.random(rng);
            let Some(floor) = self.air.floor_at(&self.world, x, z, self.rules.ceiling_y) else {
                continue;
            };
            let y = (floor + rng.gen_range(s.heights[0]..=s.heights[1]))
                .min(self.rules.ceiling_y - 1.0);
            let p = [x, y, z];
            if self.air.check_point(&self.world, params, p).is_ok() {
                return Some(p);
            }
        }
        None
    }
}

/// The walkable ground points of `region`, on a grid, where a broom lifts
/// off.
fn launch_spots(world: &World, air: &Airspace, params: &FlyParams, region: Rect) -> Vec<[f32; 3]> {
    let mut out = Vec::new();
    let mut x = region.min[0];
    while x <= region.max[0] {
        let mut z = region.min[1];
        while z <= region.max[1] {
            if let Some(g) = world.ground_below(x, z, 200.0)
                && world.ground_at(x, z, g).is_some()
                && flight::mount(world, air, params, [x, g, z]).is_ok()
            {
                out.push([x, g, z]);
            }
            z += LAUNCH_GRID;
        }
        x += LAUNCH_GRID;
    }
    out
}

/// Where spectators stand: a grid on the decks and the field's edges,
/// facing the field's middle.
fn spectator_spots(world: &World, p: &PitchSpec) -> Result<Vec<([f32; 3], f32)>, String> {
    let mut rects = Vec::new();
    for id in &p.decks {
        let s = world
            .surfaces()
            .iter()
            .find(|s| s.id == *id)
            .ok_or_else(|| format!("pitch: no surface {id}"))?;
        rects.push(Rect {
            min: [s.min[0] + 0.6, s.min[1] + 0.6],
            max: [s.max[0] - 0.6, s.max[1] - 0.6],
        });
    }
    rects.extend(p.edges.iter().copied());
    let mut out = Vec::new();
    for r in rects {
        let mut x = r.min[0];
        while x <= r.max[0] {
            let mut z = r.min[1];
            while z <= r.max[1] {
                if let Some(g) = world.ground_below(x, z, 50.0)
                    && world.ground_at(x, z, g).is_some()
                {
                    let yaw = (p.centre[0] - x).atan2(p.centre[1] - z);
                    out.push(([x, g, z], yaw));
                }
                z += 1.5;
            }
            x += 1.5;
        }
    }
    if out.is_empty() {
        return Err("pitch: nowhere for spectators to stand".into());
    }
    Ok(out)
}

/// What the flyers and spectators did, per bot (summed in the report).
#[derive(Debug, Default, Clone, Serialize)]
pub struct FlyStats {
    pub flyers: u64,
    pub spectators: u64,
    pub skill_grants: u64,
    pub grant_errors: u64,
    pub teleport_errors: u64,
    /// Accepted `Mounted { on: true }` (take-offs, the first included) and
    /// landings (`Mounted { on: false }`).
    pub mounts: u64,
    pub landings: u64,
    /// Refused mount or landing requests, by reason.
    pub refusals: BTreeMap<String, u64>,
    /// Legs flown, hovers, metres flown.
    pub legs: u64,
    pub hovers: u64,
    pub flown_m: f64,
}

impl FlyStats {
    pub fn add(&mut self, o: &FlyStats) {
        self.flyers += o.flyers;
        self.spectators += o.spectators;
        self.skill_grants += o.skill_grants;
        self.grant_errors += o.grant_errors;
        self.teleport_errors += o.teleport_errors;
        self.mounts += o.mounts;
        self.landings += o.landings;
        for (k, v) in &o.refusals {
            *self.refusals.entry(k.clone()).or_default() += v;
        }
        self.legs += o.legs;
        self.hovers += o.hovers;
        self.flown_m += o.flown_m;
    }

    /// The report's line (`None` with no flying roles).
    pub fn line(&self) -> Option<String> {
        if self.flyers + self.spectators == 0 {
            return None;
        }
        let refusals: Vec<String> = self
            .refusals
            .iter()
            .map(|(r, n)| format!("{r} {n}"))
            .collect();
        Some(format!(
            "flying: {} flyers ({} skill grants, {} failed), {} pitch spectators; {} take-offs, {} landings, refusals: {}; {} legs, {} hovers, {:.1} km flown; {} teleport errors",
            self.flyers,
            self.skill_grants,
            self.grant_errors,
            self.spectators,
            self.mounts,
            self.landings,
            if refusals.is_empty() {
                "none".to_owned()
            } else {
                refusals.join(", ")
            },
            self.legs,
            self.hovers,
            self.flown_m / 1000.0,
            self.teleport_errors
        ))
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum State {
    /// Before the teleport (or standing where it failed).
    Waiting,
    /// On the ground; `Mount` goes at `until`.
    Grounded,
    /// `Mount { on: true }` sent.
    Mounting,
    Flying,
    /// Coming down to land at `target`.
    Landing,
    /// `Mount { on: false }` sent.
    Dismounting,
    /// A spectator at its spot.
    Watching,
}

/// One flyer's or spectator's state on one connection.
pub struct FlyAct {
    pub member: Member,
    spec: usize,
    state: State,
    pos: [f32; 3],
    yaw: f32,
    tier: u8,
    target: Option<[f32; 3]>,
    hover_until: Option<Instant>,
    next_land: Instant,
    /// When a grounded flyer takes off (again).
    until: Instant,
    index: usize,
}

impl FlyAct {
    /// After `Welcome`: where to teleport.
    pub fn on_welcome(&mut self, fl: &Flight, rng: &mut impl Rng) -> ([f32; 3], f32) {
        match self.member {
            Member::Spectator => fl.spots[(self.index * 7919) % fl.spots.len()],
            Member::Flyer { .. } => {
                let spots = &fl.launch[self.spec];
                (spots[rng.gen_range(0..spots.len())], 0.0)
            }
        }
    }

    /// The teleport to `spot` succeeded (`None`: it failed; stand where we are).
    pub fn teleported(
        &mut self,
        spot: Option<([f32; 3], f32)>,
        spawn: [f32; 3],
        now: Instant,
        rng: &mut impl Rng,
    ) {
        match spot {
            None => {
                self.pos = spawn;
                self.state = State::Waiting;
            }
            Some((p, yaw)) => {
                self.pos = p;
                self.yaw = yaw;
                self.state = match self.member {
                    Member::Spectator => State::Watching,
                    Member::Flyer { .. } => {
                        // Take off within a few seconds of arriving.
                        self.until = now + Duration::from_millis(rng.gen_range(500..4_000));
                        State::Grounded
                    }
                };
            }
        }
    }

    /// One 100 ms step: a message to send first (a mount or a landing),
    /// then the move (position, yaw, anim).
    pub fn tick(
        &mut self,
        now: Instant,
        dt: f32,
        fl: &Flight,
        rng: &mut impl Rng,
        stats: &mut FlyStats,
    ) -> (Option<ClientMsg>, [f32; 3], f32, Anim) {
        let dt = dt.clamp(0.0, 0.5);
        match self.state {
            State::Waiting | State::Watching => (None, self.pos, self.yaw, Anim::Idle),
            State::Grounded if now >= self.until => {
                self.state = State::Mounting;
                self.until = now + Duration::from_secs(5);
                (
                    Some(ClientMsg::Mount { on: true }),
                    self.pos,
                    self.yaw,
                    Anim::Idle,
                )
            }
            State::Grounded => (None, self.pos, self.yaw, Anim::Idle),
            State::Mounting | State::Dismounting => {
                // No answer in time (a reconnect, a lost reply): ask again.
                let flying = self.state == State::Dismounting;
                if now >= self.until {
                    self.until = now + Duration::from_secs(5);
                    let msg = ClientMsg::Mount { on: !flying };
                    return (Some(msg), self.pos, self.yaw, still(flying));
                }
                (None, self.pos, self.yaw, still(flying))
            }
            State::Flying | State::Landing => self.fly(now, dt, fl, rng, stats),
        }
    }

    fn fly(
        &mut self,
        now: Instant,
        dt: f32,
        fl: &Flight,
        rng: &mut impl Rng,
        stats: &mut FlyStats,
    ) -> (Option<ClientMsg>, [f32; 3], f32, Anim) {
        let params = fl.params(self.tier);
        if let Some(until) = self.hover_until {
            if now < until {
                return (None, self.pos, self.yaw, Anim::Hover);
            }
            self.hover_until = None;
        }
        let spec = &fl.specs[self.spec];
        if self.state == State::Flying && now >= self.next_land {
            // Down to a launch spot, then land there.
            let spots = &fl.launch[self.spec];
            let g = spots[rng.gen_range(0..spots.len())];
            self.target = Some([g[0], g[1] + params.clearance, g[2]]);
            self.state = State::Landing;
        }
        let target = match self.target {
            Some(t) => t,
            None => match fl.point(self.spec, &params, rng) {
                Some(t) => {
                    stats.legs += 1;
                    self.target = Some(t);
                    t
                }
                None => return (None, self.pos, self.yaw, Anim::Hover),
            },
        };
        let d = [
            target[0] - self.pos[0],
            target[1] - self.pos[1],
            target[2] - self.pos[2],
        ];
        let flat = d[0].hypot(d[2]);
        if flat < ARRIVE && d[1].abs() < ARRIVE {
            self.target = None;
            if self.state == State::Landing {
                if fl
                    .world
                    .ground_below(self.pos[0], self.pos[2], self.pos[1] + 0.01)
                    .is_some_and(|g| self.pos[1] - g <= fl.rules.dismount_height * 0.8)
                {
                    self.state = State::Dismounting;
                    self.until = now + Duration::from_secs(5);
                    return (
                        Some(ClientMsg::Mount { on: false }),
                        self.pos,
                        self.yaw,
                        Anim::Hover,
                    );
                }
                // Not down yet (a hill rose under the spot): try another.
                self.state = State::Flying;
                self.next_land = now;
            } else if rng.gen_bool(spec.hover_share) {
                stats.hovers += 1;
                self.hover_until = Some(now + Duration::from_millis(rng.gen_range(2_000..8_000)));
            }
            return (None, self.pos, self.yaw, Anim::Hover);
        }
        let h = params.speed * CRUISE * dt;
        let v = params.climb_speed * CLIMB * dt;
        let k = if flat > h { h / flat } else { 1.0 };
        let step = [d[0] * k, d[1].clamp(-v, v), d[2] * k];
        let next = flight::fly_and_slide(&fl.world, &fl.air, &params, self.pos, step);
        let moved = (0..3)
            .map(|i| (next[i] - self.pos[i]).powi(2))
            .sum::<f32>()
            .sqrt();
        if moved < 1e-3 {
            // Blocked (a no-fly box in the way): somewhere else.
            self.target = None;
            if self.state == State::Landing {
                self.state = State::Flying;
                self.next_land = now;
            }
            return (None, self.pos, self.yaw, Anim::Hover);
        }
        stats.flown_m += f64::from(moved);
        if flat > 0.05 {
            self.yaw = d[0].atan2(d[2]);
        }
        self.pos = next;
        (None, next, self.yaw, Anim::Fly)
    }

    /// Reacts to the server: mount answers, corrections.
    pub fn on_msg(
        &mut self,
        msg: &ServerMsg,
        now: Instant,
        fl: &Flight,
        rng: &mut impl Rng,
        stats: &mut FlyStats,
    ) {
        match msg {
            ServerMsg::Mounted { on, tier, reason } => {
                if let Some(r) = reason {
                    *stats.refusals.entry(r.id().to_owned()).or_default() += 1;
                }
                match (self.state, *on, reason) {
                    (State::Mounting, true, _) | (State::Grounded, true, _) => {
                        self.tier = *tier;
                        let params = fl.params(*tier);
                        self.pos = flight::mount(&fl.world, &fl.air, &params, self.pos)
                            .unwrap_or(self.pos);
                        self.state = State::Flying;
                        self.target = None;
                        self.hover_until = None;
                        let per_min = fl.specs[self.spec].churn_per_min.max(1e-6);
                        let mean = 60.0 / per_min;
                        let wait = -(1.0 - rng.gen_range(0.0..1.0f64)).ln() * mean;
                        self.next_land = now + Duration::from_secs_f64(wait.min(3_600.0));
                        stats.mounts += 1;
                    }
                    (State::Mounting, false, Some(_)) => {
                        // Refused: stand and try again shortly.
                        self.state = State::Grounded;
                        self.until = now + Duration::from_secs(3);
                    }
                    (State::Dismounting | State::Flying | State::Landing, false, None) => {
                        self.pos =
                            flight::dismount(&fl.world, &fl.rules, self.pos).unwrap_or(self.pos);
                        self.state = State::Grounded;
                        self.until = now + Duration::from_millis(rng.gen_range(3_000..8_000));
                        stats.landings += 1;
                    }
                    (State::Dismounting, true, Some(_)) => {
                        // Not landed: come down a little more elsewhere.
                        self.state = State::Flying;
                        self.next_land = now;
                    }
                    _ => {}
                }
            }
            ServerMsg::Correction { pos, .. } => {
                self.pos = pos.to_m();
                self.target = None;
            }
            _ => {}
        }
    }
}

/// The anim of a bot waiting on a mount answer.
fn still(flying: bool) -> Anim {
    if flying { Anim::Hover } else { Anim::Idle }
}

#[cfg(test)]
mod tests {
    use rand::SeedableRng;
    use rand::rngs::StdRng;

    use super::*;
    use crate::nav::Scenario;
    use crate::nav::tests::castle;

    fn pitch() -> PitchSpec {
        Scenario::named("pitch")
            .unwrap()
            .pitch
            .expect("pitch roles")
    }

    #[test]
    fn the_pitch_scenario_has_flyers_spectators_and_room_for_them() {
        let p = pitch();
        assert_eq!((p.flyers, p.spectators), (300, 100));
        let world = castle();
        let walking = vec![true; 1000];
        let fl = Flight::new(world.clone(), Some(&p), 0.0, &walking).unwrap();
        assert_eq!(fl.counts(), (300, 100));
        let (launch, spots) = fl.spot_counts();
        assert!(launch > 200 && spots >= 100, "{launch} {spots}");
        // Spectators stand on the decks or the field's edge, facing in.
        for (s, _) in &fl.spots {
            assert!(world.ground_at(s[0], s[2], s[1]).is_some(), "{s:?}");
        }
        // Tiers spread over the flyers.
        let tiers: Vec<u8> = (0..1000)
            .filter_map(|i| match fl.member(i) {
                Some(Member::Flyer { tier }) => Some(tier),
                _ => None,
            })
            .collect();
        for t in 1..=3 {
            assert!(tiers.iter().filter(|&&x| x == t).count() > 80, "tier {t}");
        }
        // --fly-pct adds flyers over the whole grounds among the rest.
        let fl = Flight::new(world, Some(&p), 0.1, &walking).unwrap();
        assert_eq!(fl.counts(), (300 + 60, 100));
    }

    /// A flyer's whole life against the server's own check: take off, legs,
    /// hovers, landing and take-off again, every reported position accepted
    /// by `check_flight` from the last one at 10 Hz.
    #[test]
    fn a_flyer_flies_honestly_and_lands() {
        let world = castle();
        let fl = Flight::new(world.clone(), Some(&pitch()), 0.0, &[true; 10]).unwrap();
        let mut rng = StdRng::seed_from_u64(3);
        let mut stats = FlyStats::default();
        let i = (0..10)
            .find(|&i| matches!(fl.member(i), Some(Member::Flyer { .. })))
            .unwrap();
        let mut now = Instant::now();
        let mut act = fl.act(i, now).unwrap();
        let spot = act.on_welcome(&fl, &mut rng);
        act.teleported(Some(spot), [0.0; 3], now, &mut rng);
        let rules = &fl.rules;
        let mut landings = 0;
        let mut last = act.pos;
        let mut tier = 1;
        for _ in 0..12_000 {
            now += Duration::from_millis(100);
            let (msg, pos, _, anim) = act.tick(now, 0.1, &fl, &mut rng, &mut stats);
            let flying = matches!(
                act.state,
                State::Flying | State::Landing | State::Dismounting
            );
            if flying && pos != last && last[1] > -100.0 {
                let params = fl.params(tier);
                let check = flight::check_flight(&world, &fl.air, &params, last, pos, 0.1);
                assert!(
                    matches!(check, flight::FlightCheck::Accept { .. }),
                    "{last:?} -> {pos:?}: {check:?}"
                );
                assert!(matches!(anim, Anim::Fly | Anim::Hover));
            }
            last = pos;
            // The server's answers, as it would give them.
            if let Some(ClientMsg::Mount { on }) = msg {
                let reply = if on {
                    tier = match act.member {
                        Member::Flyer { tier } => tier,
                        Member::Spectator => 1,
                    };
                    let ok = flight::mount(&world, &fl.air, &fl.params(tier), act.pos).is_ok();
                    ServerMsg::Mounted {
                        on: ok,
                        tier: if ok { tier } else { 0 },
                        reason: (!ok).then_some(protocol::MountRefused::NoFly),
                    }
                } else {
                    let ok = flight::dismount(&world, rules, act.pos).is_ok();
                    landings += u32::from(ok);
                    ServerMsg::Mounted {
                        on: !ok,
                        tier: if ok { 0 } else { tier },
                        reason: (!ok).then_some(protocol::MountRefused::NotLanded),
                    }
                };
                act.on_msg(&reply, now, &fl, &mut rng, &mut stats);
                last = act.pos;
            }
        }
        assert!(stats.legs > 20 && stats.flown_m > 3_000.0, "{stats:?}");
        assert!(landings > 0 && stats.mounts > 1, "{stats:?}");
        assert!(stats.refusals.is_empty(), "{stats:?}");
    }
}
