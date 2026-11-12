//! Flying: a broom's moves, checked by the same code on both ends
//! as walking is (`crate::movement`). The client calls [`fly_and_slide`]
//! every frame while mounted; the server calls [`check_flight`] on each
//! `ClientMsg::Move` of a mounted character. A client that flies honestly is
//! never corrected.
//!
//! **Where a broom may fly.** The layout's [`AreaKind::Airspace`] boxes are
//! open sky (the grounds, the pitch and the lake up to the ceiling; a Flying
//! class's field); its [`AreaKind::NoFly`] boxes are closed to brooms (the
//! castle, the Owl Loft site, the buildings, the pitch's stands and towers). A
//! flight position is valid when it is inside an airspace box (its x, z, and
//! at most its top), outside every no-fly box, and at least
//! `ground_clearance` above the **floor**: the highest walkable ground under
//! it ([`World::ground_below`]) or, where there is none (the water), the
//! box's bottom. The shipped grounds' boxes bottom out at the wading limit,
//! so the lowest a broom flies over the lake is the water's surface.
//!
//! **The client** ([`fly_and_slide`]) keeps a margin (`margin`) inside the
//! airspace and off the no-fly boxes, slides along their faces like walking
//! slides along walls, never sinks below the floor (it rises over hills and
//! stops on the ground) and never climbs past a box's top. **The server**
//! ([`check_flight`]) tests the straight move: the speed (horizontally the
//! broom's, vertically its climb plus what the steepest ground can lift it),
//! every [`FLIGHT_SAMPLE_SPACING`] along the way inside the airspace and out
//! of the no-fly boxes (no margin: an honest client's margin covers a
//! corner cut between two reports), and the end above the floor. Positions
//! are not snapped: a flyer is where it says.
//!
//! **Mounting** ([`mount`]): standing on walkable ground in open sky, the
//! broom lifts the character to `ground_clearance` over the floor. **Landing**
//! ([`dismount`]): within `dismount_height` of walkable ground below, the
//! character stands on it. A character saved while flying (a logout in the
//! air) is saved on the ground below ([`landing_spot`]), or the server's
//! usual fallback where there is none (over the water).
//!
//! Numbers are data: `data/flying.json` ([`FlightRules`]).

use serde::{Deserialize, Serialize};

use protocol::{BROOM_PCT_MAX, BROOM_PCT_NORMAL, MountRefused};

use crate::layout::{AreaKind, MAX_GROUND_SLOPE};
use crate::world::World;

/// The flying schema version this crate reads.
pub const FLYING_VERSION: u32 = 1;

/// Spacing of the points [`check_flight`] tests along a move (metres): finer
/// than any no-fly box is thin.
pub const FLIGHT_SAMPLE_SPACING: f32 = 0.5;

/// Longest distance [`fly_and_slide`] moves in one collision step (metres).
pub const MAX_FLIGHT_SUBSTEP: f32 = 0.25;

/// Distance [`check_flight`] allows on top of the speed limits, for rounding
/// and a frame of timing slop (metres), as walking's `SPEED_EPSILON`.
pub const FLIGHT_EPSILON: f32 = 0.05;

/// The most the floor may step up between two points a hair apart (metres):
/// where the walkable shore meets the water (the shipped grounds' worst is
/// under 0.5 m). [`check_flight`] allows it on top of the vertical speed.
pub const FLOOR_STEP_MAX: f32 = 0.6;

/// How far above a flyer [`fly_and_slide`] and [`check_flight`] look for the
/// ground under it (metres): more than one substep can rise.
const FLOOR_REACH: f32 = 1.0;

/// Tolerance of the floor test (metres): the same `f32`s on both ends.
const FLOOR_EPSILON: f32 = 1e-3;

/// Bisection steps used to slide flush against a face: 0.25 m / 2^5 < 1 cm.
const SLIDE_BISECTIONS: u32 = 5;

#[derive(Debug, thiserror::Error)]
pub enum FlightError {
    #[error("flying JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("flying rules are inconsistent: {0}")]
    Invalid(String),
}

/// A broom speed tier: from Flying skill level `from_level`, top speed
/// `speed` (m/s, horizontal).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct SpeedTier {
    pub from_level: u8,
    pub speed: f32,
}

/// `data/flying.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FlightRules {
    pub version: u32,
    /// By rising skill level; the first from level 1. `ServerMsg::Mounted`
    /// names a tier by its number (1 = the first).
    pub tiers: Vec<SpeedTier>,
    /// How fast the client's broom gathers and sheds speed (m/s², the
    /// controller's feel; the server checks only the top speed).
    pub acceleration: f32,
    /// The fastest a broom climbs or dives (m/s).
    pub climb_speed: f32,
    /// Multiplier on the speeds the server tolerates for latency jitter.
    pub speed_tolerance: f32,
    /// The lowest a broom flies over the floor (metres).
    pub ground_clearance: f32,
    /// How far the client keeps off the airspace's edges and the no-fly
    /// boxes (metres), so the straight move between two reports never
    /// cuts a corner the server tests.
    pub margin: f32,
    /// A rider may land when walkable ground is at most this far below
    /// (metres).
    pub dismount_height: f32,
    /// The top of the grounds' and the lake's airspace (world y, metres):
    /// about 60 m over the lake. The layout's boxes carry it
    /// (`tools/blender/terrain_layout.py` `CEILING_Y`; a test keeps them
    /// equal).
    pub ceiling_y: f32,
}

impl FlightRules {
    pub fn from_json(json: &str) -> Result<FlightRules, FlightError> {
        let r: FlightRules = serde_json::from_str(json)?;
        r.validate().map_err(FlightError::Invalid)?;
        Ok(r)
    }

    fn validate(&self) -> Result<(), String> {
        if self.version != FLYING_VERSION {
            return Err(format!("unsupported version {}", self.version));
        }
        if self.tiers.first().is_none_or(|t| t.from_level != 1) {
            return Err("the first tier starts at level 1".into());
        }
        if self.tiers.len() > usize::from(u8::MAX) {
            return Err("too many tiers".into());
        }
        let rising = self
            .tiers
            .windows(2)
            .all(|w| w[1].from_level > w[0].from_level && w[1].speed > w[0].speed);
        if !rising
            || self
                .tiers
                .iter()
                .any(|t| !(t.speed.is_finite() && t.speed > 0.0))
        {
            return Err("tiers rise in level and speed, every speed positive".into());
        }
        let positive = |v: f32| v.is_finite() && v > 0.0;
        if !positive(self.acceleration) || !positive(self.climb_speed) {
            return Err("acceleration and climb speed are positive".into());
        }
        if !(self.speed_tolerance.is_finite() && self.speed_tolerance >= 1.0) {
            return Err("the speed tolerance is at least 1".into());
        }
        if !(self.margin.is_finite() && (0.0..=1.0).contains(&self.margin)) {
            return Err("the margin is 0-1 m".into());
        }
        if !(self.ground_clearance.is_finite()
            && self.ground_clearance >= 0.0
            && self.dismount_height.is_finite()
            && self.dismount_height > self.ground_clearance)
        {
            return Err("0 <= ground clearance < dismount height".into());
        }
        if !self.ceiling_y.is_finite() {
            return Err("the ceiling is a height".into());
        }
        Ok(())
    }

    /// The tier (1-based) of a character with Flying skill level `level`.
    pub fn tier_for_level(&self, level: u8) -> u8 {
        let reached = self.tiers.iter().filter(|t| t.from_level <= level).count();
        u8::try_from(reached.max(1)).unwrap_or(u8::MAX)
    }

    /// The flight parameters of tier `tier` (1-based, clamped to the tiers)
    /// at `broom_pct` percent of its speed (`OwnEffects.broom_pct`, clamped to
    /// `BROOM_PCT_NORMAL..=BROOM_PCT_MAX`; Featherlight Elixir is 115). The
    /// server validates with exactly what the client predicts with: the same
    /// integers give the same `f32`s.
    pub fn params(&self, tier: u8, broom_pct: u16) -> FlyParams {
        let k = usize::from(tier.max(1)).min(self.tiers.len()) - 1;
        let pct = broom_pct.clamp(BROOM_PCT_NORMAL, BROOM_PCT_MAX);
        let speed = if pct == BROOM_PCT_NORMAL {
            self.tiers[k].speed
        } else {
            self.tiers[k].speed * f32::from(pct) / 100.0
        };
        FlyParams {
            speed,
            climb_speed: self.climb_speed,
            tolerance: self.speed_tolerance,
            clearance: self.ground_clearance,
            margin: self.margin,
        }
    }
}

/// One tier's flight numbers ([`FlightRules::params`]).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FlyParams {
    /// Top horizontal speed (m/s).
    pub speed: f32,
    /// Top climbing and diving speed (m/s).
    pub climb_speed: f32,
    pub tolerance: f32,
    pub clearance: f32,
    pub margin: f32,
}

impl FlyParams {
    /// The fastest the server lets a flyer rise or fall (m/s): its climb,
    /// plus what the steepest walkable ground lifts a broom skimming it at
    /// top speed.
    pub fn max_vertical_speed(&self) -> f32 {
        self.climb_speed + MAX_GROUND_SLOPE * self.speed
    }
}

/// Result of validating a flight move on the server.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FlightCheck {
    /// Accept; the flyer is at `pos` (as reported).
    Accept { pos: [f32; 3] },
    /// Reject; correct the client back to the last accepted position.
    Reject(FlightReject),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FlightReject {
    /// Outside every airspace box (or above its top, or not a number).
    OutOfAirspace,
    /// Into a no-fly box.
    NoFly,
    /// Faster than the broom (horizontally or vertically).
    TooFast,
    /// Under the floor: into the ground or the water.
    BelowGround,
}

#[derive(Debug, Clone, Copy)]
struct Box3 {
    min: [f32; 3],
    max: [f32; 3],
}

/// A world's open sky: its airspace and no-fly boxes. Build it once per
/// world (`Airspace::new`) next to the world itself.
#[derive(Debug, Clone, Default)]
pub struct Airspace {
    boxes: Vec<Box3>,
    no_fly: Vec<Box3>,
}

impl Airspace {
    pub fn new(world: &World) -> Airspace {
        let of = |kind| {
            world
                .areas()
                .iter()
                .filter(|a| a.kind == kind)
                .map(|a| Box3 {
                    min: a.min,
                    max: a.max,
                })
                .collect()
        };
        Airspace {
            boxes: of(AreaKind::Airspace),
            no_fly: of(AreaKind::NoFly),
        }
    }

    /// No airspace at all (the castle's indoor instances, the dorms): no
    /// broom here.
    pub fn is_empty(&self) -> bool {
        self.boxes.is_empty()
    }

    /// The floor in box `b` under `(x, z)` for a flyer at height `y`.
    fn floor_in(world: &World, b: &Box3, x: f32, z: f32, y: f32) -> f32 {
        world
            .ground_below(x, z, y + FLOOR_REACH)
            .map_or(b.min[1], |g| g.max(b.min[1]))
    }

    /// The floor under `(x, z)` for a flyer at height `y` (the lowest of the
    /// boxes holding the point in plan), or `None` outside the airspace.
    pub fn floor_at(&self, world: &World, x: f32, z: f32, y: f32) -> Option<f32> {
        self.boxes
            .iter()
            .filter(|b| x >= b.min[0] && x <= b.max[0] && z >= b.min[2] && z <= b.max[2])
            .map(|b| Self::floor_in(world, b, x, z, y))
            .reduce(f32::min)
    }

    /// Whether `p` is a valid flight position, keeping `margin` inside the
    /// boxes (in plan) and off the no-fly boxes (in 3D). With `floor`, also at
    /// least `clearance` over the floor.
    fn classify(
        &self,
        world: &World,
        p: [f32; 3],
        clearance: f32,
        margin: f32,
        floor: bool,
    ) -> Result<(), FlightReject> {
        if !p.iter().all(|v| v.is_finite()) {
            return Err(FlightReject::OutOfAirspace);
        }
        let [x, y, z] = p;
        let m = margin;
        if self
            .no_fly
            .iter()
            .any(|b| (0..3).all(|k| p[k] >= b.min[k] - m && p[k] <= b.max[k] + m))
        {
            return Err(FlightReject::NoFly);
        }
        let mut inside = false;
        for b in &self.boxes {
            let in_plan =
                x >= b.min[0] + m && x <= b.max[0] - m && z >= b.min[2] + m && z <= b.max[2] - m;
            if !in_plan || y > b.max[1] + FLOOR_EPSILON {
                continue;
            }
            inside = true;
            if !floor || y >= Self::floor_in(world, b, x, z, y) + clearance - FLOOR_EPSILON {
                return Ok(());
            }
        }
        Err(if inside {
            FlightReject::BelowGround
        } else {
            FlightReject::OutOfAirspace
        })
    }

    /// Whether `p` is a valid flight position (no margin): what the server
    /// accepts a move to.
    pub fn check_point(
        &self,
        world: &World,
        params: &FlyParams,
        p: [f32; 3],
    ) -> Result<(), FlightReject> {
        self.classify(world, p, params.clearance, 0.0, true)
    }
}

/// Moves a flyer at `pos` by `delta` (`[dx, dy, dz]`, metres) as far as the
/// airspace allows and returns its new position. Call it every frame with
/// the broom's velocity times the frame time (horizontal speed at most
/// `params.speed`, vertical at most `params.climb_speed`).
///
/// The move is split into steps of at most [`MAX_FLIGHT_SUBSTEP`]. Each step
/// rises to `clearance` over the floor where the ground comes up under it
/// (a broom skims up a hill rather than stopping) and tries the full motion,
/// keeping `margin` inside the airspace and off the no-fly boxes; a step that
/// leaves them slides: each axis on its own (larger first), as far as
/// possible, so a broom flown into the castle's no-fly box glides along it
/// and one flown at it ends flush with the margin. A position already inside
/// the margin (a correction, a mount by an edge) moves without it until it
/// is clear; one not in open sky at all is returned unchanged (the server
/// will correct it).
pub fn fly_and_slide(
    world: &World,
    air: &Airspace,
    params: &FlyParams,
    pos: [f32; 3],
    delta: [f32; 3],
) -> [f32; 3] {
    let push = |mut q: [f32; 3]| {
        if let Some(f) = air.floor_at(world, q[0], q[2], q[1]) {
            q[1] = q[1].max(f + params.clearance);
        }
        q
    };
    let fits = |q: [f32; 3], strict: bool| {
        let m = if strict { params.margin } else { 0.0 };
        air.classify(world, q, params.clearance, m, true).is_ok()
    };
    let mut cur = push(pos);
    let mut strict = fits(cur, true);
    if !strict && !fits(cur, false) {
        return pos;
    }
    let len = delta.iter().map(|d| d * d).sum::<f32>().sqrt();
    // Also rejects NaN deltas.
    if !(len > 0.0 && len.is_finite()) {
        return cur;
    }
    let steps = (len / MAX_FLIGHT_SUBSTEP).ceil() as u32;
    let step = delta.map(|d| d / steps as f32);
    for _ in 0..steps {
        let full = push([cur[0] + step[0], cur[1] + step[1], cur[2] + step[2]]);
        if fits(full, strict) {
            cur = full;
        } else {
            let mut axes = [0usize, 1, 2];
            axes.sort_by(|&a, &b| step[b].abs().total_cmp(&step[a].abs()));
            let mut moved = false;
            for k in axes {
                if step[k] == 0.0 {
                    continue;
                }
                let along = |t: f32| {
                    let mut q = cur;
                    q[k] += step[k] * t;
                    push(q)
                };
                let q = along(1.0);
                if fits(q, strict) {
                    cur = q;
                    moved = true;
                    continue;
                }
                // Blocked on this axis: find how far it can go.
                let (mut lo, mut hi, mut best) = (0.0f32, 1.0f32, None);
                for _ in 0..SLIDE_BISECTIONS {
                    let mid = (lo + hi) * 0.5;
                    let q = along(mid);
                    if fits(q, strict) {
                        (lo, best) = (mid, Some(q));
                    } else {
                        hi = mid;
                    }
                }
                if let Some(q) = best {
                    cur = q;
                    moved = true;
                }
            }
            if !moved {
                break;
            }
        }
        if !strict {
            strict = fits(cur, true);
        }
    }
    cur
}

/// Validates a flight move from the last accepted position `prev` to `next`,
/// `dt_seconds` after it (see the module doc). Rejects, in order:
/// [`FlightReject::TooFast`] (horizontally over `speed * tolerance * dt`,
/// vertically over `max_vertical_speed * tolerance * dt`, each plus
/// [`FLIGHT_EPSILON`]; a rise may take [`FLOOR_STEP_MAX`] more only when it
/// ends that low over the floor, as a broom skimming up a step does); then, every
/// [`FLIGHT_SAMPLE_SPACING`] along the straight move and at `next`,
/// [`FlightReject::NoFly`] or [`FlightReject::OutOfAirspace`]; then
/// [`FlightReject::BelowGround`] at `next`.
pub fn check_flight(
    world: &World,
    air: &Airspace,
    params: &FlyParams,
    prev: [f32; 3],
    next: [f32; 3],
    dt_seconds: f32,
) -> FlightCheck {
    use FlightReject::*;
    if !next.iter().all(|v| v.is_finite()) {
        return FlightCheck::Reject(OutOfAirspace);
    }
    let dt = if dt_seconds.is_finite() {
        dt_seconds.max(0.0)
    } else {
        0.0
    };
    let (dx, dy, dz) = (next[0] - prev[0], next[1] - prev[1], next[2] - prev[2]);
    let flat = (dx * dx + dz * dz).sqrt();
    let h_limit = params.speed * params.tolerance * dt + FLIGHT_EPSILON;
    let v_limit = params.max_vertical_speed() * params.tolerance * dt + FLIGHT_EPSILON;
    // A NaN `prev` makes these NaN; reject that too.
    if flat.is_nan() || dy.is_nan() || flat > h_limit || dy < -v_limit {
        return FlightCheck::Reject(TooFast);
    }
    // Rising more than the climb allows is only the floor stepping up under
    // a broom skimming it (the shore out of the water, a jetty): the move
    // must end low over the floor. Otherwise every report could climb
    // another `FLOOR_STEP_MAX`, however many were sent a second.
    if dy > v_limit
        && (dy > v_limit + FLOOR_STEP_MAX
            || air
                .floor_at(world, next[0], next[2], next[1])
                .is_none_or(|f| next[1] > f + params.clearance + FLOOR_STEP_MAX + v_limit))
    {
        return FlightCheck::Reject(TooFast);
    }
    let len = (flat * flat + dy * dy).sqrt();
    let samples = (len / FLIGHT_SAMPLE_SPACING).ceil().max(1.0) as u32;
    for i in 1..samples {
        let t = i as f32 / samples as f32;
        let q = [prev[0] + dx * t, prev[1] + dy * t, prev[2] + dz * t];
        if let Err(r) = air.classify(world, q, params.clearance, 0.0, false) {
            return FlightCheck::Reject(r);
        }
    }
    match air.check_point(world, params, next) {
        Ok(()) => FlightCheck::Accept { pos: next },
        Err(r) => FlightCheck::Reject(r),
    }
}

/// Mounting where a character stands at `pos` (on walkable ground): the
/// flight position the broom lifts it to (`ground_clearance` over the
/// floor), or why not here. The Flying skill and being busy are the
/// server's own checks.
pub fn mount(
    world: &World,
    air: &Airspace,
    params: &FlyParams,
    pos: [f32; 3],
) -> Result<[f32; 3], MountRefused> {
    if air.is_empty() {
        return Err(MountRefused::NoAirspace);
    }
    let Some(ground) = world.ground_at(pos[0], pos[2], pos[1]) else {
        return Err(MountRefused::NoAirspace);
    };
    let floor = air
        .floor_at(world, pos[0], pos[2], ground.y)
        .map_or(ground.y, |f| f.max(ground.y));
    let lifted = [pos[0], floor + params.clearance, pos[2]];
    match air.check_point(world, params, lifted) {
        Ok(()) => Ok(lifted),
        Err(FlightReject::NoFly) => Err(MountRefused::NoFly),
        Err(_) => Err(MountRefused::NoAirspace),
    }
}

/// Landing a flyer at `pos`: where it stands (the walkable ground at most
/// `dismount_height` below), or [`MountRefused::NotLanded`].
pub fn dismount(
    world: &World,
    rules: &FlightRules,
    pos: [f32; 3],
) -> Result<[f32; 3], MountRefused> {
    let [x, y, z] = pos;
    match world.ground_below(x, z, y + FLOOR_EPSILON) {
        Some(g) if y - g <= rules.dismount_height + FLOOR_EPSILON => Ok([x, g, z]),
        _ => Err(MountRefused::NotLanded),
    }
}

/// Where a flyer at `pos` is saved when it leaves the world (a logout in
/// the air): standing on the highest walkable ground under it, or `None`
/// over the water (the server's spawn fallback).
pub fn landing_spot(world: &World, pos: [f32; 3]) -> Option<[f32; 3]> {
    let [x, y, z] = pos;
    if !pos.iter().all(|v| v.is_finite()) {
        return None;
    }
    world
        .ground_below(x, z, y + FLOOR_EPSILON)
        .map(|g| [x, g, z])
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::Layout;
    use crate::testutil::Rng;

    const RULES: &str = include_str!("../../../data/flying.json");
    const LAYOUT: &str = include_str!("../../../data/world/layout.json");
    const CLASS_PITCH: &str = include_str!("../../../data/world/instances/class_pitch.json");

    fn rules() -> FlightRules {
        FlightRules::from_json(RULES).expect("the shipped flying rules are valid")
    }

    fn grounds() -> (World, Airspace) {
        let w = World::from_json(LAYOUT).unwrap();
        let a = Airspace::new(&w);
        (w, a)
    }

    fn accept(c: FlightCheck) -> [f32; 3] {
        match c {
            FlightCheck::Accept { pos } => pos,
            other => panic!("expected Accept, got {other:?}"),
        }
    }

    /// On the pitch's field, 0.3 m up (the floor there is -9.5).
    const FIELD: [f32; 3] = [10.0, -9.2, 200.0];

    #[test]
    fn the_shipped_rules_are_the_users_speeds() {
        let r = rules();
        let speeds: Vec<f32> = r.tiers.iter().map(|t| t.speed).collect();
        assert_eq!(speeds, [8.0, 9.0, 10.0]);
        assert_eq!(r.tier_for_level(1), 1);
        assert_eq!(r.tier_for_level(0), 1);
        assert_eq!(r.tier_for_level(20), 3);
        assert_eq!(r.params(1, BROOM_PCT_NORMAL).speed, 8.0);
        // Featherlight (+15 %) takes the top tier to 11.5 m/s.
        assert!((r.params(3, 115).speed - 11.5).abs() < 1e-5);
        assert_eq!(r.params(99, 9_999), r.params(3, BROOM_PCT_MAX));
        assert_eq!(r.params(3, 50), r.params(3, BROOM_PCT_NORMAL));
        assert!(r.dismount_height > r.ground_clearance);
    }

    #[test]
    fn bad_rules_are_refused() {
        let v: serde_json::Value = serde_json::from_str(RULES).unwrap();
        let err = |v: &serde_json::Value| FlightRules::from_json(&v.to_string()).is_err();
        let mut tiers = v.clone();
        tiers["tiers"][1]["speed"] = 7.0.into();
        assert!(err(&tiers));
        let mut first = v.clone();
        first["tiers"][0]["from_level"] = 2.into();
        assert!(err(&first));
        let mut clear = v.clone();
        clear["ground_clearance"] = 0.9.into();
        assert!(err(&clear));
        let mut tol = v;
        tol["speed_tolerance"] = 0.5.into();
        assert!(err(&tol));
    }

    /// The layout's airspace tops are the rules' ceiling (terrain_layout.py
    /// writes them from its own constant).
    #[test]
    fn the_airspace_reaches_the_ceiling() {
        let r = rules();
        let layout = Layout::from_json(LAYOUT).unwrap();
        let tops: Vec<f32> = layout
            .areas
            .iter()
            .filter(|a| a.kind == AreaKind::Airspace)
            .map(|a| a.max[1])
            .collect();
        assert!(tops.len() >= 2);
        assert!(tops.iter().all(|&t| t == r.ceiling_y), "{tops:?}");
        // About 60 m over the lake.
        assert!((55.0..=65.0).contains(&(r.ceiling_y + 15.0)));
    }

    #[test]
    fn moves_inside_the_airspace_are_accepted_and_others_refused() {
        let (w, a) = grounds();
        let p = rules().params(1, BROOM_PCT_NORMAL);
        use FlightReject::*;
        // Over the field, up and along.
        accept(check_flight(&w, &a, &p, FIELD, [10.5, -8.9, 200.5], 0.1));
        // Too fast, sideways or up.
        let far = [10.0 + p.speed * p.tolerance * 0.1 + 0.1, -9.2, 200.0];
        assert_eq!(
            check_flight(&w, &a, &p, FIELD, far, 0.1),
            FlightCheck::Reject(TooFast)
        );
        let up = [10.0, -9.2 + 3.0, 200.0];
        assert_eq!(
            check_flight(&w, &a, &p, FIELD, up, 0.1),
            FlightCheck::Reject(TooFast)
        );
        // Into the ground.
        let under = [10.2, -9.5, 200.0];
        assert_eq!(
            check_flight(&w, &a, &p, FIELD, under, 0.1),
            FlightCheck::Reject(BelowGround)
        );
        // Into a pitch tower, and into the castle's no-fly box from the grounds.
        let tower = [-41.5, 0.0, 189.0];
        assert_eq!(a.check_point(&w, &p, tower), Err(NoFly));
        let at_castle = [16.0, 10.0, 100.4];
        accept(check_flight(
            &w,
            &a,
            &p,
            [16.0, 10.0, 100.9],
            at_castle,
            0.1,
        ));
        let in_castle = [16.0, 10.0, 99.9];
        assert_eq!(
            check_flight(&w, &a, &p, at_castle, in_castle, 0.1),
            FlightCheck::Reject(NoFly)
        );
        // Above the ceiling, beyond the forest's edge, and NaN.
        let r = rules();
        assert_eq!(
            a.check_point(&w, &p, [10.0, r.ceiling_y + 0.5, 200.0]),
            Err(OutOfAirspace)
        );
        assert_eq!(
            a.check_point(&w, &p, [117.0, 10.0, 200.0]),
            Err(OutOfAirspace)
        );
        assert_eq!(
            check_flight(&w, &a, &p, FIELD, [f32::NAN, 0.0, 0.0], 0.1),
            FlightCheck::Reject(OutOfAirspace)
        );
        // Over the lake the floor is the water: the wading limit plus the clearance.
        let lake = [0.0, -15.0, 330.0];
        assert_eq!(a.floor_at(&w, 0.0, 330.0, -15.0), Some(-15.3));
        a.check_point(&w, &p, lake).unwrap();
        assert_eq!(a.check_point(&w, &p, [0.0, -15.2, 330.0]), Err(BelowGround));
        // Through a pitch stand's no-fly box (its west steps' end) from open sky beside it.
        a.check_point(&w, &p, [-31.0, -4.0, 179.0]).unwrap();
        assert_eq!(
            check_flight(&w, &a, &p, [-31.0, -4.0, 179.0], [-29.0, -4.0, 179.0], 1.0),
            FlightCheck::Reject(NoFly)
        );
    }

    /// Review fix: the floor-step allowance is per report, so it holds only
    /// for a broom ending low over the floor; a flood of reports high up
    /// cannot climb with it.
    #[test]
    fn a_flood_of_reports_cannot_climb_faster_than_the_broom() {
        let (w, a) = grounds();
        let p = rules().params(1, BROOM_PCT_NORMAL);
        let high = [10.0, 5.0, 200.0];
        assert_eq!(
            check_flight(&w, &a, &p, high, [10.0, 5.6, 200.0], 0.0),
            FlightCheck::Reject(FlightReject::TooFast)
        );
        // Skimming the field, a step's rise is still allowed.
        accept(check_flight(&w, &a, &p, FIELD, [10.0, -8.7, 200.0], 0.0));
        // Diving gets no step allowance either.
        assert_eq!(
            check_flight(&w, &a, &p, high, [10.0, 4.4, 200.0], 0.0),
            FlightCheck::Reject(FlightReject::TooFast)
        );
    }

    #[test]
    fn mounting_and_landing_follow_the_rules() {
        let (w, a) = grounds();
        let r = rules();
        let p = r.params(1, BROOM_PCT_NORMAL);
        // On the field: lifted to the clearance.
        let up = mount(&w, &a, &p, [10.0, -9.5, 200.0]).unwrap();
        assert!((up[1] - (-9.5 + r.ground_clearance)).abs() < 1e-4, "{up:?}");
        // In the castle (the Entrance Hall's spawn), at the courtyard gate
        // (no-fly) and on a pitch stand (no-fly).
        let spawn = w.spawns()[0].pos;
        assert_eq!(mount(&w, &a, &p, spawn), Err(MountRefused::NoFly));
        assert_eq!(
            mount(&w, &a, &p, [16.0, 0.0, 95.0]),
            Err(MountRefused::NoFly)
        );
        assert_eq!(
            mount(&w, &a, &p, [8.0, -7.1, 178.55]),
            Err(MountRefused::NoFly)
        );
        // An instance without airspace (no areas at all).
        let dorm = World::from_json(include_str!(
            "../../../data/world/instances/dorm_roarwing.json"
        ))
        .unwrap();
        let none = Airspace::new(&dorm);
        assert!(none.is_empty());
        assert_eq!(
            mount(&dorm, &none, &p, dorm.spawns()[0].pos),
            Err(MountRefused::NoAirspace)
        );
        // Landing: within the dismount height of the field, not higher, never over the water.
        assert_eq!(dismount(&w, &r, up), Ok([10.0, -9.5, 200.0]));
        assert_eq!(
            dismount(&w, &r, [10.0, -9.0, 200.0]),
            Ok([10.0, -9.5, 200.0])
        );
        assert_eq!(
            dismount(&w, &r, [10.0, -8.9, 200.0]),
            Err(MountRefused::NotLanded)
        );
        assert_eq!(
            dismount(&w, &r, [0.0, -15.0, 330.0]),
            Err(MountRefused::NotLanded)
        );
        // A logout in the air lands on the ground below, or nowhere over the water.
        assert_eq!(
            landing_spot(&w, [10.0, 30.0, 200.0]),
            Some([10.0, -9.5, 200.0])
        );
        assert_eq!(landing_spot(&w, [0.0, 10.0, 330.0]), None);
    }

    #[test]
    fn fly_and_slide_stops_at_the_castle_and_rides_over_hills() {
        let (w, a) = grounds();
        let p = rules().params(3, BROOM_PCT_NORMAL);
        // North at the castle at 10 m: flush with the margin off its box (z 100).
        let mut pos = [16.0, 10.0, 130.0];
        for _ in 0..400 {
            pos = fly_and_slide(&w, &a, &p, pos, [0.0, 0.0, -0.15]);
        }
        assert!((pos[2] - (100.0 + p.margin)).abs() < 0.02, "{pos:?}");
        // Diving into the ground stops at the clearance; skimming up the hill
        // from the pitch to the castle keeps it.
        let mut pos = [10.0, 0.0, 170.0];
        for _ in 0..400 {
            pos = fly_and_slide(&w, &a, &p, pos, [0.0, -0.1, 0.0]);
        }
        let g = w.ground_below(pos[0], pos[2], pos[1]).unwrap();
        assert!((pos[1] - (g + p.clearance)).abs() < 1e-4, "{pos:?}");
        for _ in 0..300 {
            pos = fly_and_slide(&w, &a, &p, pos, [0.0, 0.0, -0.15]);
            let g = w.ground_below(pos[0], pos[2], pos[1] + 0.5).unwrap();
            assert!(pos[1] >= g + p.clearance - 1e-3, "{pos:?}");
        }
        assert!(pos[2] < 140.0, "{pos:?}");
        // Up to the ceiling and no further.
        let r = rules();
        for _ in 0..2_000 {
            pos = fly_and_slide(&w, &a, &p, pos, [0.0, 0.2, 0.0]);
        }
        assert!(
            pos[1] <= r.ceiling_y + 1e-3 && pos[1] > r.ceiling_y - 0.05,
            "{pos:?}"
        );
    }

    /// The promise: every position an honest client reaches with
    /// `fly_and_slide` at 60 fps, reported at 10 Hz, is accepted by
    /// `check_flight`, whatever it flies into (the castle, the towers and
    /// stands, the ground, the water, the airspace's edges, the ceiling), at
    /// every tier and under Featherlight, on the shipped grounds and in the
    /// Flying class's field.
    #[test]
    fn honest_flyers_are_never_corrected() {
        let (w, a) = grounds();
        let class = World::from_json(CLASS_PITCH).unwrap();
        let class_air = Airspace::new(&class);
        let r = rules();
        let starts: [(&World, &Airspace, [f32; 3]); 6] = [
            (&w, &a, FIELD),
            (&w, &a, [16.0, 2.0, 104.0]),
            (&w, &a, [-84.0, 12.0, 145.0]),
            (&w, &a, [0.0, -15.0, 300.0]),
            (&w, &a, [-120.0, 5.0, 260.0]),
            (&class, &class_air, [0.0, 0.3, 16.0]),
        ];
        let mut rng = Rng(0x3b3b_f1e5);
        let mut flights = 0;
        for (world, air, start) in starts {
            for (tier, pct) in [(1, BROOM_PCT_NORMAL), (3, BROOM_PCT_NORMAL), (3, 115)] {
                let p = r.params(tier, pct);
                let mut pos = fly_and_slide(world, air, &p, start, [0.0, 0.0, 0.0]);
                let mut accepted = pos;
                let (mut heading, mut climb, mut speed) = (0.0f32, 0.0f32, p.speed);
                for frame in 1..=12_000 {
                    if rng.next_u32().is_multiple_of(50) {
                        heading = rng.range(0.0, std::f32::consts::TAU);
                        // Climb, dive, level or hover; often flat out.
                        climb = [-1.0, -0.4, 0.0, 0.0, 0.5, 1.0][(rng.next_u32() % 6) as usize];
                        speed = [0.0, 0.5, 1.0, 1.0, 1.0][(rng.next_u32() % 5) as usize] * p.speed;
                    }
                    let dt = 1.0 / 60.0;
                    let d = [
                        heading.sin() * speed * dt,
                        climb * p.climb_speed * dt,
                        heading.cos() * speed * dt,
                    ];
                    pos = fly_and_slide(world, air, &p, pos, d);
                    if frame % 6 == 0 {
                        accepted = accept(check_flight(world, air, &p, accepted, pos, 0.1));
                        flights += 1;
                    }
                }
            }
        }
        assert_eq!(flights, 6 * 3 * 2_000);
    }

    /// The shore steps where the walkable grounds meet the water (the floor
    /// jumps from the airspace's bottom to the bank): the highest of them on
    /// the shipped grounds, ranked by the step's height.
    fn shore_steps(w: &World, a: &Airspace, count: usize) -> Vec<([f32; 2], [f32; 2], f32)> {
        let mut steps: Vec<([f32; 2], [f32; 2], f32)> = Vec::new();
        for s in w
            .surfaces()
            .iter()
            .filter(|s| s.kind == crate::layout::SurfaceKind::Grounds)
        {
            let sides: [([f32; 2], [f32; 2], [f32; 2]); 4] = [
                ([s.min[0], s.min[1]], [s.max[0], s.min[1]], [0.0, -1.0]),
                ([s.min[0], s.max[1]], [s.max[0], s.max[1]], [0.0, 1.0]),
                ([s.min[0], s.min[1]], [s.min[0], s.max[1]], [-1.0, 0.0]),
                ([s.max[0], s.min[1]], [s.max[0], s.max[1]], [1.0, 0.0]),
            ];
            for (p0, p1, n) in sides {
                let len = ((p1[0] - p0[0]).powi(2) + (p1[1] - p0[1]).powi(2)).sqrt();
                let k = (len / 0.5).ceil() as usize;
                for i in 1..k {
                    let t = i as f32 / k as f32;
                    let p = [p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t];
                    let q = [p[0] + n[0] * 0.2, p[1] + n[1] * 0.2];
                    if w.ground_below(q[0], q[1], 100.0).is_some() {
                        continue; // more ground beyond: not the shore
                    }
                    let Some(water) = a.floor_at(w, q[0], q[1], -14.0) else {
                        continue; // outside the airspace
                    };
                    if a.classify(w, [q[0], water + 0.3, q[1]], 0.3, 0.0, false)
                        .is_err()
                    {
                        continue; // a no-fly box (the boathouse's hole)
                    }
                    let bank = s.height_at(p[0], p[1]);
                    // The lake's banks (not the buildings' holes in the grounds).
                    let step = bank - water;
                    if bank < -13.0
                        && step > 0.05
                        && steps.iter().all(|(o, _, _)| dist2(*o, p) > 25.0)
                    {
                        steps.push((p, n, step));
                    }
                }
            }
        }
        steps.sort_by(|x, y| y.2.total_cmp(&x.2));
        steps.truncate(count);
        steps
    }

    fn dist2(a: [f32; 2], b: [f32; 2]) -> f32 {
        (a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)
    }

    /// The floor-step rule. A broom
    /// skimming the water at its clearance onto the shore's highest steps
    /// rises by the step within one report; at 10 Hz and in a flood of
    /// reports every frame, from every tier and under Featherlight, every
    /// report is accepted, and the rule is exercised (rises beyond the climb
    /// limit do happen).
    #[test]
    fn honest_skimmers_climb_the_shore_steps() {
        let (w, a) = grounds();
        let r = rules();
        let steps = shore_steps(&w, &a, 6);
        assert!(steps.len() >= 3, "{steps:?}");
        assert!(
            steps[0].2 > 0.2 && steps[0].2 <= FLOOR_STEP_MAX,
            "{steps:?}"
        );
        let mut over_climb = 0;
        let mut reports = 0;
        for &(p, n, _) in &steps {
            for (tier, pct) in [(1, BROOM_PCT_NORMAL), (3, BROOM_PCT_NORMAL), (3, 115)] {
                let fp = r.params(tier, pct);
                for every in [6usize, 1] {
                    let dt = 1.0 / 60.0;
                    let report_dt = every as f32 * dt;
                    let v_limit =
                        fp.max_vertical_speed() * fp.tolerance * report_dt + FLIGHT_EPSILON;
                    let start = [p[0] + n[0] * 4.0, -15.3 + fp.clearance, p[1] + n[1] * 4.0];
                    let mut pos = fly_and_slide(&w, &a, &fp, start, [0.0, 0.0, 0.0]);
                    let mut accepted = pos;
                    for frame in 1..=180 {
                        let d = [-n[0] * fp.speed * dt, 0.0, -n[1] * fp.speed * dt];
                        pos = fly_and_slide(&w, &a, &fp, pos, d);
                        if frame % every == 0 {
                            let dy = pos[1] - accepted[1];
                            over_climb += (dy > v_limit) as usize;
                            accepted = accept(check_flight(&w, &a, &fp, accepted, pos, report_dt));
                            reports += 1;
                        }
                    }
                }
            }
        }
        assert_eq!(reports, steps.len() * 3 * (30 + 180), "{reports}");
        assert!(over_climb > 0, "the floor-step rule was never needed");
    }

    /// `cargo test -p sim-core --release -- --ignored --nocapture bench_check_flight`
    #[test]
    #[ignore]
    fn bench_check_flight() {
        let (w, a) = grounds();
        let p = rules().params(3, 115);
        let mut rng = Rng(77);
        let moves: Vec<([f32; 3], [f32; 3])> = (0..4096)
            .map(|_| {
                let s = [
                    rng.range(-90.0, 100.0),
                    rng.range(0.0, 40.0),
                    rng.range(110.0, 280.0),
                ];
                let h = rng.range(0.0, std::f32::consts::TAU);
                (s, [s[0] + h.sin(), s[1] + 0.2, s[2] + h.cos()])
            })
            .collect();
        let calls = 100_000;
        let t = std::time::Instant::now();
        let mut accepted = 0;
        for i in 0..calls {
            let (prev, next) = moves[i % moves.len()];
            let c = check_flight(&w, &a, &p, prev, next, 0.1);
            accepted += matches!(c, FlightCheck::Accept { .. }) as u32;
        }
        let ns = t.elapsed().as_nanos() as f64 / calls as f64;
        println!("check_flight: {ns:.0} ns/call over {calls} calls ({accepted} accepted)");
    }
}
