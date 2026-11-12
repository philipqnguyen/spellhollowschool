//! Character movement rules. The client calls [`move_and_slide`] every frame to
//! move and collide; the server calls [`check_move`] on each `ClientMsg::Move`
//! it receives. Both run this same code (the client through WebAssembly), so a
//! client that moves honestly is never corrected.
//!
//! Characters never fall or jump: they walk from surface to surface, and a
//! move is possible only where the ground continues within
//! [`MoveParams::step_height`]. Walls are simply the absence of ground, so the
//! gap between two rooms blocks movement and a balcony's edge acts as a
//! railing.
//!
//! Spells to level 20: hindrances slow a character down to a stop
//! ([`MoveParams::with_speed_pct`] below 100, 0 for a Rigora bind), and
//! Adveni's slide goes only where [`pull_destination`] finds safe ground.

use crate::layout::SurfaceKind;
use crate::world::World;

/// Tunable movement constants, identical on client and server.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MoveParams {
    pub walk_speed: f32,
    pub run_speed: f32,
    /// Largest height change allowed between two connected surfaces.
    pub step_height: f32,
    /// Character collision radius used to keep players off wall edges.
    pub radius: f32,
    /// Multiplier on `run_speed` the server tolerates for latency jitter.
    pub speed_tolerance: f32,
}

impl Default for MoveParams {
    fn default() -> Self {
        Self {
            walk_speed: 3.0,
            run_speed: 6.5,
            step_height: 0.35,
            radius: 0.3,
            speed_tolerance: 1.25,
        }
    }
}

impl MoveParams {
    /// These parameters for a character whose movement speed is `speed_pct`
    /// percent of normal (Pick-Me-Up Tonic; spells to level 20:
    /// Tardara and Rigora slow down to 0), clamped to
    /// `protocol::SPEED_PCT_MIN..=SPEED_PCT_MAX`. Walk and run speeds scale
    /// (0 stops them); the step height, the collision radius and the
    /// tolerance do not (Shrinking and Swelling are drawn smaller or larger
    /// but collide as usual).
    ///
    /// The server validates each move with the multiplier it supplies
    /// (`OwnEffects.speed_pct`, which the client also predicts with), so a
    /// boosted honest client is never corrected, and nobody else can run
    /// faster. The same integer percent on both ends gives the same `f32`s.
    pub fn with_speed_pct(&self, speed_pct: u16) -> MoveParams {
        let pct = speed_pct.clamp(protocol::SPEED_PCT_MIN, protocol::SPEED_PCT_MAX);
        if pct == protocol::SPEED_PCT_NORMAL {
            return *self;
        }
        let k = f32::from(pct) / 100.0;
        MoveParams {
            walk_speed: self.walk_speed * k,
            run_speed: self.run_speed * k,
            ..*self
        }
    }

    /// The fastest a validated move may cover ground (metres per second of
    /// earned movement credit).
    pub fn max_speed(&self) -> f32 {
        self.run_speed * self.speed_tolerance
    }
}

/// Result of validating a client-reported move on the server.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum MoveCheck {
    /// Accept; the position is snapped to the ground height of its surface.
    Accept { pos: [f32; 3] },
    /// Reject; the client must be corrected back to the last accepted position.
    Reject(RejectReason),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RejectReason {
    NotWalkable,
    TooFast,
    BlockedPath,
}

/// Longest distance [`move_and_slide`] moves in one collision step (metres).
pub const MAX_SUBSTEP: f32 = 0.25;

/// Spacing of the points [`check_move`] tests along a move (metres). Any gap
/// between unconnected surfaces (a wall) wider than this is caught.
pub const PATH_SAMPLE_SPACING: f32 = 0.1;

/// Distance [`check_move`] allows on top of the speed limit, for rounding and
/// a frame of timing slop (metres).
pub const SPEED_EPSILON: f32 = 0.05;

/// Bisection steps used to slide flush against a wall: 0.25 m / 2^5 < 1 cm.
const SLIDE_BISECTIONS: u32 = 5;

/// Points on the unit circle where the character's footprint must have ground.
const FOOTPRINT: [[f32; 2]; 8] = {
    use std::f32::consts::FRAC_1_SQRT_2 as D;
    [
        [1.0, 0.0],
        [D, D],
        [0.0, 1.0],
        [-D, D],
        [-1.0, 0.0],
        [-D, -D],
        [0.0, -1.0],
        [D, -D],
    ]
};

/// Ground height for a character centred at `(x, z)` coming from height
/// `from_y`, if its whole footprint (centre plus [`FOOTPRINT`] points at
/// `params.radius`) stands on ground connected within a step.
pub fn footprint_ground(
    world: &World,
    params: &MoveParams,
    x: f32,
    z: f32,
    from_y: f32,
) -> Option<f32> {
    let y = world.step_ground(x, z, from_y, params.step_height)?.y;
    let r = params.radius;
    FOOTPRINT
        .iter()
        .all(|[ox, oz]| world.has_ground_within(x + ox * r, z + oz * r, y, params.step_height))
        .then_some(y)
}

/// Spacing of the rings [`stand_near`] tries round a point (metres).
const STAND_STEP: f32 = 0.1;

/// The nearest place to `pos` (in plan, within `max_dist`) where a character
/// fits: its whole footprint on ground within a step of the floor at `pos`'s
/// height, outside every solid. `pos` itself when it fits. Used to push a
/// character out of furniture placed on it, to stand up beside a piece, to
/// put an arrival whose saved spot is now inside a table next to it, and to
/// end the Monstra Viam trail beside a place that stands in furniture. Tries
/// rings every [`STAND_STEP`], so the answer is the first ring with room
/// (the closest point on it).
pub fn stand_near(
    world: &World,
    params: &MoveParams,
    pos: [f32; 3],
    max_dist: f32,
) -> Option<[f32; 3]> {
    if !pos.iter().all(|v| v.is_finite()) {
        return None;
    }
    let fits = |x: f32, z: f32| {
        // The floor at the point's height (a step either way), the
        // footprint on it: the same test as walking.
        let g = world.ground_within(x, z, pos[1], params.step_height + 0.05)?;
        footprint_ground(world, params, x, z, g.y).map(|y| [x, y, z])
    };
    if let Some(p) = fits(pos[0], pos[2]) {
        return Some(p);
    }
    let rings = (max_dist / STAND_STEP).ceil() as u32;
    for k in 1..=rings {
        let r = k as f32 * STAND_STEP;
        // About one try per 5 cm of the ring's length.
        let n = ((std::f32::consts::TAU * r / 0.05).ceil() as u32).max(8);
        let mut best: Option<([f32; 3], f32)> = None;
        for i in 0..n {
            let a = i as f32 * std::f32::consts::TAU / n as f32;
            let (x, z) = (pos[0] + r * a.cos(), pos[2] + r * a.sin());
            if let Some(p) = fits(x, z) {
                let d = (p[1] - pos[1]).abs();
                if best.is_none_or(|(_, bd)| d < bd) {
                    best = Some((p, d));
                }
            }
        }
        if let Some((p, _)) = best {
            return Some(p);
        }
    }
    None
}

/// Where a character can walk from a start, on a grid: every grid point
/// whose footprint fits ([`footprint_ground`]) and joins a neighbour's by
/// ground (stacked floors apart). The dorm rule's "is the way out still
/// open" ([`crate::placement::keeps_way`]) and the solid furniture tests'
/// "is anything walled in" both flood with it.
#[derive(Debug, Clone)]
pub struct Reach {
    step: f32,
    origin: [f32; 2],
    /// (column, row, floor band) -> ground height of the reached points.
    cells: std::collections::HashMap<(i32, i32, i32), f32>,
}

/// Height of [`Reach`]'s floor bands (metres): stacked floors are further apart.
const REACH_BAND: f32 = 2.0;

impl Reach {
    /// Floods from `from` (moved onto the nearest point a character fits,
    /// within a metre) over a grid of `step` metres, stopping after `limit`
    /// points. Empty when nobody fits near `from`.
    pub fn flood(
        world: &World,
        params: &MoveParams,
        from: [f32; 3],
        step: f32,
        limit: usize,
    ) -> Reach {
        let mut reach = Reach {
            step,
            origin: [from[0], from[2]],
            cells: std::collections::HashMap::new(),
        };
        let Some(start) = stand_near(world, params, from, 1.0) else {
            return reach;
        };
        reach.origin = [start[0], start[2]];
        let band = |y: f32| (y / REACH_BAND).floor() as i32;
        let mut todo = vec![(0i32, 0i32, start[1])];
        reach.cells.insert((0, 0, band(start[1])), start[1]);
        while let Some((i, k, y)) = todo.pop() {
            if reach.cells.len() >= limit {
                break;
            }
            let (x, z) = reach.at(i, k);
            for (di, dk) in [(1, 0), (-1, 0), (0, 1), (0, -1)] {
                let (ni, nk) = (i + di, k + dk);
                let (nx, nz) = reach.at(ni, nk);
                // The way between them has ground too (no thin wall crossed).
                let Some(mid) =
                    world.step_ground((x + nx) * 0.5, (z + nz) * 0.5, y, params.step_height)
                else {
                    continue;
                };
                let Some(ny) = footprint_ground(world, params, nx, nz, mid.y) else {
                    continue;
                };
                if let std::collections::hash_map::Entry::Vacant(e) =
                    reach.cells.entry((ni, nk, band(ny)))
                {
                    e.insert(ny);
                    todo.push((ni, nk, ny));
                }
            }
        }
        reach
    }

    fn at(&self, i: i32, k: i32) -> (f32, f32) {
        (
            self.origin[0] + i as f32 * self.step,
            self.origin[1] + k as f32 * self.step,
        )
    }

    /// How many grid points were reached.
    pub fn len(&self) -> usize {
        self.cells.len()
    }

    pub fn is_empty(&self) -> bool {
        self.cells.is_empty()
    }

    /// Every reached point ([x, y, z]).
    pub fn points(&self) -> impl Iterator<Item = [f32; 3]> + '_ {
        self.cells.iter().map(|(&(i, k, _), &y)| {
            let (x, z) = self.at(i, k);
            [x, y, z]
        })
    }

    /// Whether a reached point lies within `within` of `pos` in plan and
    /// `dy` of its height.
    pub fn reaches(&self, pos: [f32; 3], within: f32, dy: f32) -> bool {
        let n = (within / self.step).ceil() as i32;
        let ci = ((pos[0] - self.origin[0]) / self.step).round() as i32;
        let ck = ((pos[2] - self.origin[1]) / self.step).round() as i32;
        let b0 = ((pos[1] - dy) / REACH_BAND).floor() as i32;
        let b1 = ((pos[1] + dy) / REACH_BAND).floor() as i32;
        for i in ci - n..=ci + n {
            for k in ck - n..=ck + n {
                let (x, z) = self.at(i, k);
                if (x - pos[0]).hypot(z - pos[2]) > within {
                    continue;
                }
                for b in b0..=b1 {
                    if self
                        .cells
                        .get(&(i, k, b))
                        .is_some_and(|&y| (y - pos[1]).abs() <= dy)
                    {
                        return true;
                    }
                }
            }
        }
        false
    }
}

/// Moves a character at `pos` by the horizontal `delta` (`[dx, dz]`, metres)
/// as far as the layout allows and returns its new position on the ground.
///
/// The move is split into steps of at most [`MAX_SUBSTEP`]. Each step tries
/// the full motion; if that leaves walkable ground the character slides: each
/// axis is tried on its own (larger component first) and moved as far as
/// possible, so walking diagonally into a wall glides along it and walking
/// straight at a wall ends flush with it (at `params.radius`).
///
/// A position that is not on ground at all (within
/// [`crate::world::SNAP_TOLERANCE`]) is returned unchanged; the server will
/// correct it. A position whose footprint does not fit (a correction that left
/// it hugging a wall) moves by its centre alone until it fits again, so a
/// character can never get stuck.
pub fn move_and_slide(
    world: &World,
    params: &MoveParams,
    pos: [f32; 3],
    delta: [f32; 2],
) -> [f32; 3] {
    let [mut x, _, mut z] = pos;
    let Some(start) = world.ground_at(x, z, pos[1]) else {
        return pos;
    };
    let mut y = start.y;
    let mut fits = footprint_ground(world, params, x, z, y).is_some();

    let len = (delta[0] * delta[0] + delta[1] * delta[1]).sqrt();
    // Also rejects NaN deltas.
    if !(len > 0.0 && len.is_finite()) {
        return [x, y, z];
    }
    let steps = (len / MAX_SUBSTEP).ceil() as u32;
    let (sx, sz) = (delta[0] / steps as f32, delta[1] / steps as f32);
    let ground = |fits: bool, x: f32, z: f32, y: f32| {
        if fits {
            footprint_ground(world, params, x, z, y)
        } else {
            world.step_ground(x, z, y, params.step_height).map(|g| g.y)
        }
    };

    for _ in 0..steps {
        if let Some(ny) = ground(fits, x + sx, z + sz, y) {
            (x, z, y) = (x + sx, z + sz, ny);
        } else {
            let axes = if sx.abs() >= sz.abs() {
                [(sx, 0.0), (0.0, sz)]
            } else {
                [(0.0, sz), (sx, 0.0)]
            };
            let mut moved = false;
            for (ax, az) in axes {
                if ax == 0.0 && az == 0.0 {
                    continue;
                }
                if let Some(ny) = ground(fits, x + ax, z + az, y) {
                    (x, z, y) = (x + ax, z + az, ny);
                    moved = true;
                    continue;
                }
                // Blocked on this axis: find how far it can go.
                let (mut lo, mut hi, mut best) = (0.0f32, 1.0f32, None);
                for _ in 0..SLIDE_BISECTIONS {
                    let mid = (lo + hi) * 0.5;
                    match ground(fits, x + ax * mid, z + az * mid, y) {
                        Some(ny) => (lo, best) = (mid, Some(ny)),
                        None => hi = mid,
                    }
                }
                if let Some(ny) = best {
                    (x, z, y) = (x + ax * lo, z + az * lo, ny);
                    moved = true;
                }
            }
            if !moved {
                break;
            }
        }
        if !fits {
            fits = footprint_ground(world, params, x, z, y).is_some();
        }
    }
    [x, y, z]
}

/// Why Adveni cannot pull someone ([`pull_destination`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PullBlocked {
    /// Already within the gap (or the shortest pull would end inside it).
    TooClose,
    /// The way drops or climbs more than a step (a balcony's edge, a lip).
    Ledge,
    /// A solid or no surface (a wall, furniture) in the way or at the end.
    Wall,
    /// The way crosses a door (no pulling anyone through doorways).
    Door,
    /// The way crosses one of the Grand Staircase's moving flights.
    MovingStair,
}

/// Spacing of [`pull_destination`]'s samples along the way (metres).
const PULL_SAMPLE: f32 = 0.1;
/// How much shorter each retry of [`pull_destination`] is (metres).
const PULL_RETRY: f32 = 0.25;

/// Adveni's destination: from `from` toward `toward` (horizontally), as far
/// as `distance_m` allows but never within `gap_m` of `toward`, trying
/// shorter pulls ([`PULL_RETRY`] steps) down to `min_m`. Every
/// [`PULL_SAMPLE`] along the way must be walkable ground reached by
/// [`World::step_ground`] within `params.step_height` of the previous one
/// (else [`PullBlocked::Ledge`]; a solid or no surface:
/// [`PullBlocked::Wall`]), on a surface that is not a door
/// ([`PullBlocked::Door`]) and not a moving flight
/// ([`PullBlocked::MovingStair`]); the end must be clear of solids at
/// `params.radius`. The start's own surface is checked the same way. `Err`
/// carries the full-length attempt's reason.
pub fn pull_destination(
    world: &World,
    params: &MoveParams,
    from: [f32; 3],
    toward: [f32; 3],
    distance_m: f32,
    min_m: f32,
    gap_m: f32,
) -> Result<[f32; 3], PullBlocked> {
    let (dx, dz) = (toward[0] - from[0], toward[2] - from[2]);
    let len = dx.hypot(dz);
    if !(len.is_finite() && from.iter().all(|v| v.is_finite())) {
        return Err(PullBlocked::Wall);
    }
    let longest = distance_m.min(len - gap_m);
    if !(longest >= min_m && min_m > 0.0) {
        return Err(PullBlocked::TooClose);
    }
    let dir = [dx / len, dz / len];
    let surface_ok = |g: crate::world::Ground| {
        let s = world.surface(g.surface);
        if s.kind == SurfaceKind::Door {
            Err(PullBlocked::Door)
        } else if s.moving.is_some() {
            Err(PullBlocked::MovingStair)
        } else {
            Ok(g.y)
        }
    };
    let Some(start) = world.ground_at(from[0], from[2], from[1]) else {
        return Err(PullBlocked::Wall);
    };
    let start_y = surface_ok(start)?;
    let attempt = |travel: f32| -> Result<[f32; 3], PullBlocked> {
        let samples = (travel / PULL_SAMPLE).ceil().max(1.0) as u32;
        let mut y = start_y;
        for i in 1..=samples {
            let t = (i as f32 * PULL_SAMPLE).min(travel);
            let (x, z) = (from[0] + dir[0] * t, from[2] + dir[1] * t);
            match world.step_ground(x, z, y, params.step_height) {
                Some(g) => y = surface_ok(g)?,
                None if world.solid_at(x, y, z).is_some() => return Err(PullBlocked::Wall),
                None if world.ground_at(x, z, y).is_some()
                    || world.ground_below(x, z, y).is_some() =>
                {
                    return Err(PullBlocked::Ledge);
                }
                None => return Err(PullBlocked::Wall),
            }
        }
        let (x, z) = (from[0] + dir[0] * travel, from[2] + dir[1] * travel);
        match footprint_ground(world, params, x, z, y) {
            Some(y) => Ok([x, y, z]),
            None => Err(PullBlocked::Wall),
        }
    };
    let first = attempt(longest);
    if first.is_ok() {
        return first;
    }
    let mut travel = longest - PULL_RETRY;
    while travel >= min_m - 1e-4 {
        if let Ok(p) = attempt(travel.max(min_m)) {
            return Ok(p);
        }
        travel -= PULL_RETRY;
    }
    first
}

/// Validates a client-reported move from the last accepted position `prev` to
/// `next`, `dt_seconds` after it.
///
/// Rejects, in order of cost:
/// - [`RejectReason::TooFast`]: the horizontal distance exceeds
///   `run_speed * speed_tolerance * dt + SPEED_EPSILON`;
/// - [`RejectReason::NotWalkable`]: no surface on any floor contains `next`
///   (or a coordinate is not finite, or `prev` is off the ground);
/// - [`RejectReason::BlockedPath`]: walking the straight segment in
///   [`PATH_SAMPLE_SPACING`] steps leaves connected ground (the move crosses a
///   wall or a drop), or `next` claims a different floor than the one the path
///   arrives on.
///
/// Only the character's centre is checked, not its footprint, so a client that
/// rounds a corner within one move (cutting it slightly) is not rejected.
/// Accepted positions are snapped to the ground height the path arrives at.
pub fn check_move(
    world: &World,
    params: &MoveParams,
    prev: [f32; 3],
    next: [f32; 3],
    dt_seconds: f32,
) -> MoveCheck {
    use RejectReason::*;
    if !next.iter().all(|v| v.is_finite()) {
        return MoveCheck::Reject(NotWalkable);
    }
    let dt = if dt_seconds.is_finite() {
        dt_seconds.max(0.0)
    } else {
        0.0
    };
    let (dx, dz) = (next[0] - prev[0], next[2] - prev[2]);
    let dist_sq = dx * dx + dz * dz;
    let limit = params.run_speed * params.speed_tolerance * dt + SPEED_EPSILON;
    // A NaN `prev` makes `dist_sq` NaN; reject that too.
    if dist_sq.is_nan() || dist_sq > limit * limit {
        return MoveCheck::Reject(TooFast);
    }
    if !world.is_walkable(next[0], next[2]) {
        return MoveCheck::Reject(NotWalkable);
    }
    let Some(start) = world.ground_at(prev[0], prev[2], prev[1]) else {
        return MoveCheck::Reject(NotWalkable);
    };

    let samples = (dist_sq.sqrt() / PATH_SAMPLE_SPACING).ceil().max(1.0) as u32;
    let mut y = start.y;
    for i in 1..=samples {
        let (x, z) = if i == samples {
            (next[0], next[2])
        } else {
            let t = i as f32 / samples as f32;
            (prev[0] + dx * t, prev[2] + dz * t)
        };
        // On the way, a solid's outer graze may be cut (a slide round a
        // post between two reports); the end stands clear of every solid.
        let ground = if i == samples {
            world.step_ground(x, z, y, params.step_height)
        } else {
            world.step_ground_grazing(x, z, y, params.step_height)
        };
        match ground {
            Some(g) => y = g.y,
            None => return MoveCheck::Reject(BlockedPath),
        }
    }
    if (next[1] - y).abs() > params.step_height {
        return MoveCheck::Reject(BlockedPath);
    }
    MoveCheck::Accept {
        pos: [next[0], y, next[2]],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{
        Rng, flat, hall_and_balcony, room_door_corridor, synthetic_castle, world,
    };
    use RejectReason::*;

    const P: MoveParams = MoveParams {
        walk_speed: 3.0,
        run_speed: 6.5,
        step_height: 0.35,
        radius: 0.3,
        speed_tolerance: 1.25,
    };

    #[test]
    fn default_params_match_the_test_constants() {
        assert_eq!(MoveParams::default(), P);
    }

    fn close(a: [f32; 3], b: [f32; 3], tol: f32) -> bool {
        a.iter().zip(b).all(|(p, q)| (p - q).abs() <= tol)
    }

    /// Walks from `pos` in `frames` frames of `delta` each, like the client does.
    fn walk(w: &World, mut pos: [f32; 3], delta: [f32; 2], frames: usize) -> [f32; 3] {
        for _ in 0..frames {
            pos = move_and_slide(w, &P, pos, delta);
        }
        pos
    }

    fn accept(c: MoveCheck) -> [f32; 3] {
        match c {
            MoveCheck::Accept { pos } => pos,
            other => panic!("expected Accept, got {other:?}"),
        }
    }

    #[test]
    fn moves_freely_in_a_flat_room() {
        let w = world(vec![flat("room", [0.0, 0.0], [10.0, 10.0], 0.0)]);
        let end = move_and_slide(&w, &P, [2.0, 0.0, 2.0], [3.0, 4.0]);
        assert!(close(end, [5.0, 0.0, 6.0], 1e-4), "{end:?}");
        // Positions slightly off the floor snap to it.
        let end = move_and_slide(&w, &P, [2.0, 0.2, 2.0], [0.1, 0.0]);
        assert!(close(end, [2.1, 0.0, 2.0], 1e-4), "{end:?}");
    }

    #[test]
    fn walls_stop_at_the_collision_radius() {
        let w = world(vec![flat("room", [0.0, 0.0], [10.0, 10.0], 0.0)]);
        let end = walk(&w, [5.0, 0.0, 5.0], [0.1, 0.0], 100);
        assert!(close(end, [10.0 - P.radius, 0.0, 5.0], 0.01), "{end:?}");
        // One long move is clamped the same way as many short ones.
        let end = move_and_slide(&w, &P, [5.0, 0.0, 5.0], [0.0, -20.0]);
        assert!(close(end, [5.0, 0.0, P.radius], 0.01), "{end:?}");
    }

    #[test]
    fn slides_along_walls() {
        let w = world(vec![flat("room", [0.0, 0.0], [10.0, 10.0], 0.0)]);
        let end = move_and_slide(&w, &P, [5.0, 0.0, 9.5], [0.5, 0.5]);
        assert!(close(end, [5.5, 0.0, 10.0 - P.radius], 0.01), "{end:?}");
        // Pressing into the wall at a shallow angle keeps gliding along it.
        let end = walk(&w, [2.0, 0.0, 9.6], [0.05, 0.1], 40);
        assert!(close(end, [4.0, 0.0, 10.0 - P.radius], 0.01), "{end:?}");
        // Into a corner: stuck there, not through it.
        let end = walk(&w, [8.0, 0.0, 8.0], [0.1, 0.1], 100);
        assert!(close(end, [9.7, 0.0, 9.7], 0.01), "{end:?}");
    }

    #[test]
    fn doors_connect_rooms_and_walls_block_elsewhere() {
        let w = room_door_corridor();
        // Through the door, into the corridor.
        let end = walk(&w, [5.0, 0.0, 5.0], [0.1, 0.0], 150);
        assert!(close(end, [20.0, 0.0, 5.0], 1e-3), "{end:?}");
        // Beside the door the wall stops us, although the corridor is behind it.
        let end = walk(&w, [5.0, 0.0, 6.5], [0.1, 0.0], 150);
        assert!(close(end, [10.0 - P.radius, 0.0, 6.5], 0.01), "{end:?}");
        // Walking up the wall slides into the doorway and through.
        let end = walk(&w, [9.0, 0.0, 9.0], [0.1, -0.1], 60);
        assert!(end[0] > 10.5 && w.is_walkable(end[0], end[2]), "{end:?}");
    }

    #[test]
    fn stairs_climb_to_an_overlapping_upper_floor() {
        let w = hall_and_balcony();
        let end = walk(&w, [9.0, 0.0, 15.0], [0.0, -0.1], 200);
        assert!(close(end, [9.0, 4.0, P.radius], 0.01), "{end:?}");
        assert_eq!(w.level_at(end[0], end[2], end[1]), Some(1));
        // Halfway up, the height follows the stair.
        let mid = walk(&w, [9.0, 0.0, 15.0], [0.0, -0.1], 70);
        assert!(close(mid, [9.0, 2.0, 8.0], 1e-3), "{mid:?}");
        // And back down to the hall floor.
        let down = walk(&w, end, [0.0, 0.1], 200);
        assert!(close(down, [9.0, 0.0, 20.0 - P.radius], 0.01), "{down:?}");
    }

    #[test]
    fn walking_under_a_balcony_stays_downstairs() {
        let w = hall_and_balcony();
        let end = walk(&w, [2.0, 0.0, 10.0], [0.0, -0.1], 120);
        assert!(close(end, [2.0, 0.0, P.radius], 0.01), "{end:?}");
        assert_eq!(w.level_at(end[0], end[2], end[1]), Some(0));
    }

    #[test]
    fn balcony_edges_and_stair_sides_act_as_railings() {
        let w = hall_and_balcony();
        // On the balcony, walking south past its edge: stop, don't drop.
        let end = walk(&w, [2.0, 4.0, 2.0], [0.0, 0.1], 50);
        assert!(close(end, [2.0, 4.0, 4.0 - P.radius], 0.01), "{end:?}");
        // Halfway up the stair, walking sideways off it.
        let end = walk(&w, [9.0, 2.0, 8.0], [0.1, 0.0], 50);
        assert!(close(end, [10.0 - P.radius, 2.0, 8.0], 0.01), "{end:?}");
    }

    #[test]
    fn a_wall_hugging_position_can_still_move() {
        let w = world(vec![flat("room", [0.0, 0.0], [10.0, 10.0], 0.0)]);
        let end = move_and_slide(&w, &P, [9.95, 0.0, 5.0], [-1.0, 0.0]);
        assert!(close(end, [8.95, 0.0, 5.0], 1e-3), "{end:?}");
        // Off the ground entirely: unchanged.
        assert_eq!(
            move_and_slide(&w, &P, [20.0, 0.0, 5.0], [1.0, 0.0]),
            [20.0, 0.0, 5.0]
        );
        assert_eq!(
            move_and_slide(&w, &P, [5.0, 0.0, 5.0], [f32::NAN, 0.0]),
            [5.0, 0.0, 5.0]
        );
    }

    #[test]
    fn check_accepts_honest_moves_and_snaps_height() {
        let w = room_door_corridor();
        let pos = accept(check_move(&w, &P, [5.0, 0.0, 5.0], [5.5, 0.1, 5.2], 0.1));
        assert_eq!(pos, [5.5, 0.0, 5.2]);
        // Through the door in one move.
        accept(check_move(&w, &P, [9.8, 0.0, 5.0], [10.6, 0.0, 5.0], 0.1));
        // Standing still is always fine.
        accept(check_move(&w, &P, [5.0, 0.0, 5.0], [5.0, 0.0, 5.0], 0.0));
    }

    #[test]
    fn check_rejects_speeding() {
        let w = room_door_corridor();
        let max = P.run_speed * P.speed_tolerance * 0.1;
        accept(check_move(
            &w,
            &P,
            [1.0, 0.0, 1.0],
            [1.0 + max, 0.0, 1.0],
            0.1,
        ));
        let c = check_move(&w, &P, [1.0, 0.0, 1.0], [1.0 + max + 0.06, 0.0, 1.0], 0.1);
        assert_eq!(c, MoveCheck::Reject(TooFast));
        let c = check_move(&w, &P, [1.0, 0.0, 1.0], [5.0, 0.0, 1.0], 0.1);
        assert_eq!(c, MoveCheck::Reject(TooFast));
        let c = check_move(&w, &P, [1.0, 0.0, 1.0], [1.5, 0.0, 1.0], f32::NAN);
        assert_eq!(c, MoveCheck::Reject(TooFast));
    }

    #[test]
    fn check_rejects_teleporting_through_walls() {
        let w = room_door_corridor();
        // The corridor is right behind the wall but not reachable here.
        let c = check_move(&w, &P, [9.6, 0.0, 6.5], [10.9, 0.0, 6.5], 0.2);
        assert_eq!(c, MoveCheck::Reject(BlockedPath));
        // Into the wall itself.
        let c = check_move(&w, &P, [9.8, 0.0, 8.0], [10.2, 0.0, 8.0], 0.1);
        assert_eq!(c, MoveCheck::Reject(NotWalkable));
        // Off the map, or not even a number.
        let c = check_move(&w, &P, [0.2, 0.0, 5.0], [-0.3, 0.0, 5.0], 0.1);
        assert_eq!(c, MoveCheck::Reject(NotWalkable));
        let c = check_move(&w, &P, [5.0, 0.0, 5.0], [f32::NAN, 0.0, 5.0], 0.1);
        assert_eq!(c, MoveCheck::Reject(NotWalkable));
    }

    #[test]
    fn check_rejects_floor_teleporting_and_follows_stairs() {
        let w = hall_and_balcony();
        // Straight up from the hall to the balcony above it.
        let c = check_move(&w, &P, [2.0, 0.0, 2.0], [2.0, 4.0, 2.0], 0.1);
        assert_eq!(c, MoveCheck::Reject(BlockedPath));
        let c = check_move(&w, &P, [2.0, 0.0, 2.0], [2.3, 4.0, 2.0], 0.1);
        assert_eq!(c, MoveCheck::Reject(BlockedPath));
        // Climbing the stair onto the balcony is fine.
        let pos = accept(check_move(&w, &P, [9.0, 3.75, 4.5], [9.0, 4.0, 3.8], 0.1));
        assert_eq!(pos, [9.0, 4.0, 3.8]);
        let pos = accept(check_move(&w, &P, [9.0, 2.0, 8.0], [9.0, 2.3, 7.4], 0.1));
        assert!((pos[1] - 2.3).abs() < 1e-5, "{pos:?}");
    }

    /// The client and server must agree: every position an honest client
    /// reaches with `move_and_slide` at 60 fps, reported at 10 Hz, is accepted
    /// by `check_move` at the height the client computed.
    #[test]
    fn honest_clients_are_never_corrected() {
        let starts = [
            (room_door_corridor(), [5.0, 0.0, 5.0]),
            (hall_and_balcony(), [9.0, 0.0, 15.0]),
            (synthetic_castle(60), [6.0, 0.0, 6.0]),
        ];
        let mut rng = Rng(0x9e37_79b9);
        for (w, start) in &starts {
            let (mut pos, mut accepted) = (*start, *start);
            let mut heading = 0.0f32;
            for frame in 1..=60_000 {
                // Wander: mostly straight, sometimes turning, often hugging walls.
                if rng.next_u32().is_multiple_of(40) {
                    heading = rng.range(0.0, std::f32::consts::TAU);
                }
                let step = P.run_speed / 60.0;
                pos = move_and_slide(w, &P, pos, [heading.cos() * step, heading.sin() * step]);
                if frame % 6 == 0 {
                    let got = accept(check_move(w, &P, accepted, pos, 0.1));
                    assert!((got[1] - pos[1]).abs() < 1e-3, "{got:?} vs {pos:?}");
                    accepted = got;
                }
            }
        }
    }

    /// The same promise over hills. A synthetic grounds of four
    /// heightfield tiles (rolling at up to about 25 degrees, seams shared) and
    /// the shipped grounds from the courtyard gate: every position an honest
    /// client reaches is accepted at the height it computed, and its feet
    /// stay on the field's ground.
    #[test]
    fn honest_clients_are_never_corrected_on_hills() {
        use crate::layout::tests::hill;
        // Rolling ground to the centimetre, as the shipped files are.
        let roll = |x: f32, z: f32| {
            let h = (x * 0.21).sin() * 0.6 + (z * 0.17).cos() * 1.8 * (x * 0.05).cos();
            (h * 100.0).round() / 100.0
        };
        let tiles = vec![
            hill("t00", [0.0, 0.0], [40.0, 40.0], roll),
            hill("t10", [40.0, 0.0], [80.0, 40.0], roll),
            hill("t01", [0.0, 40.0], [40.0, 80.0], roll),
            hill("t11", [40.0, 40.0], [80.0, 80.0], roll),
        ];
        let max_slope = tiles
            .iter()
            .map(|t| t.heights.as_ref().unwrap().max_slope())
            .fold(0.0f32, f32::max);
        assert!(
            max_slope > 0.3 && max_slope <= crate::MAX_GROUND_SLOPE,
            "{max_slope}"
        );
        let synthetic = world(tiles);
        let shipped = World::from_json(include_str!("../../../data/world/layout.json")).unwrap();
        let starts = [
            (&synthetic, [20.0, roll(20.0, 20.0), 20.0]),
            (&shipped, [16.0, 0.0, 100.0]),
            (&shipped, [-40.0, -1.48, 116.0]),
        ];
        let mut rng = Rng(0x3a3a_1234);
        for (w, start) in starts {
            let start = [
                start[0],
                w.ground_at(start[0], start[2], start[1]).unwrap().y,
                start[2],
            ];
            let (mut pos, mut accepted) = (start, start);
            let mut heading = 0.0f32;
            let mut climbed = 0.0f32;
            for frame in 1..=60_000 {
                if rng.next_u32().is_multiple_of(40) {
                    heading = rng.range(0.0, std::f32::consts::TAU);
                }
                let step = P.run_speed / 60.0;
                let before = pos[1];
                pos = move_and_slide(w, &P, pos, [heading.cos() * step, heading.sin() * step]);
                climbed += (pos[1] - before).abs();
                let g = w.ground_at(pos[0], pos[2], pos[1]).expect("on the ground");
                assert!((g.y - pos[1]).abs() < 1e-4, "feet on the ground at {pos:?}");
                if frame % 6 == 0 {
                    let got = accept(check_move(w, &P, accepted, pos, 0.1));
                    assert!((got[1] - pos[1]).abs() < 1e-3, "{got:?} vs {pos:?}");
                    accepted = got;
                }
            }
            assert!(
                climbed > 50.0,
                "the walk went over hills ({climbed} m up and down)"
            );
        }
    }

    #[test]
    fn speed_modifiers_scale_the_limit_exactly() {
        let w = room_door_corridor();
        let fast = P.with_speed_pct(115);
        assert!((fast.run_speed - 6.5 * 1.15).abs() < 1e-5);
        assert_eq!(fast.radius, P.radius);
        assert_eq!(fast.step_height, P.step_height);
        // Normal is unchanged, absurd fast values clamp; spells to level
        // 20: slower than normal scales down exactly, 0 stops.
        assert_eq!(P.with_speed_pct(100), P);
        let slow = P.with_speed_pct(40);
        assert_eq!(slow.walk_speed, P.walk_speed * 0.4);
        assert_eq!(slow.run_speed, P.run_speed * 0.4);
        assert_eq!((slow.radius, slow.step_height), (P.radius, P.step_height));
        let stopped = P.with_speed_pct(0);
        assert_eq!((stopped.walk_speed, stopped.run_speed), (0.0, 0.0));
        assert_eq!(stopped.max_speed(), 0.0);
        // Bound: no step beyond the epsilon, however long it took.
        assert_eq!(
            check_move(&w, &stopped, [1.0, 0.0, 1.0], [1.1, 0.0, 1.0], 5.0),
            MoveCheck::Reject(TooFast)
        );
        accept(check_move(
            &w,
            &stopped,
            [1.0, 0.0, 1.0],
            [1.04, 0.0, 1.0],
            5.0,
        ));
        assert_eq!(
            P.with_speed_pct(u16::MAX),
            P.with_speed_pct(protocol::SPEED_PCT_MAX)
        );
        // A boosted move: rejected at normal speed, accepted at 115 %.
        let normal_max = P.max_speed() * 0.1;
        let boosted = [1.0 + normal_max * 1.1, 0.0, 1.0];
        assert_eq!(
            check_move(&w, &P, [1.0, 0.0, 1.0], boosted, 0.1),
            MoveCheck::Reject(TooFast)
        );
        accept(check_move(&w, &fast, [1.0, 0.0, 1.0], boosted, 0.1));
        // But not beyond the boosted limit.
        let too_far = [1.0 + fast.max_speed() * 0.1 + 0.06, 0.0, 1.0];
        assert_eq!(
            check_move(&w, &fast, [1.0, 0.0, 1.0], too_far, 0.1),
            MoveCheck::Reject(TooFast)
        );
    }

    /// Spells L20: Adveni's slide stops at the gap, shortens before a wall,
    /// and refuses ledges, doors, moving flights and starts on any of them.
    #[test]
    fn pull_destination_finds_safe_ground_only() {
        use crate::layout::{Moving, Rise, StairPosition, Surface};
        use crate::testutil::stair;
        let pull = |w: &World, from: [f32; 3], toward: [f32; 3]| {
            pull_destination(w, &P, from, toward, 2.5, 1.5, 1.2)
        };
        let room = room_door_corridor();
        // Open floor: the full 2.5 m toward the caster, on the ground.
        let to = pull(&room, [8.0, 0.0, 5.0], [2.0, 0.0, 5.0]).unwrap();
        assert!(close(to, [5.5, 0.0, 5.0], 1e-4), "{to:?}");
        // Never within the gap: 3 m apart gives 1.8 m.
        let to = pull(&room, [5.0, 0.0, 5.0], [2.0, 0.0, 5.0]).unwrap();
        assert!(close(to, [3.2, 0.0, 5.0], 1e-4), "{to:?}");
        // Too close for the shortest pull.
        assert_eq!(
            pull(&room, [4.0, 0.0, 5.0], [2.0, 0.0, 5.0]),
            Err(PullBlocked::TooClose)
        );
        // A wall ahead: shortened until the footprint fits (x >= radius).
        let to = pull(&room, [2.0, 0.0, 2.0], [-6.0, 0.0, 2.0]).unwrap();
        assert!(to[0] >= P.radius - 1e-4 && to[0] <= 0.55, "{to:?}");
        // Across the wall beside the door: no ground between.
        assert_eq!(
            pull(&room, [11.0, 0.0, 3.2], [7.0, 0.0, 3.2]),
            Err(PullBlocked::Wall)
        );
        // Through the doorway, and starting in it.
        assert_eq!(
            pull(&room, [12.0, 0.0, 5.0], [7.0, 0.0, 5.0]),
            Err(PullBlocked::Door)
        );
        assert_eq!(
            pull(&room, [10.25, 0.0, 5.0], [6.0, 0.0, 5.0]),
            Err(PullBlocked::Door)
        );
        // Off a balcony's edge: a ledge (a drop over the step height).
        let hall = hall_and_balcony();
        assert_eq!(
            pull(&hall, [3.0, 4.0, 3.0], [3.0, 0.0, 9.0]),
            Err(PullBlocked::Ledge)
        );
        // Further back on the balcony, a shorter pull stops before the edge.
        let to = pull(&hall, [3.0, 4.0, 2.0], [3.0, 0.0, 9.0]).unwrap();
        assert!(to[1] == 4.0 && to[2] + P.radius <= 4.0 + 1e-3, "{to:?}");
        // A moving flight on the way, or under the target.
        let flight = |moving: bool| Surface {
            moving: moving.then(|| Moving {
                flight: "f1".into(),
                position: StairPosition::A,
            }),
            ..stair("flight", [6.0, 0.0], [8.0, 4.0], 0.0, 0.3, Rise::PosX)
        };
        let stairs = |moving: bool| {
            world(vec![
                flat("a", [0.0, 0.0], [6.0, 4.0], 0.0),
                flight(moving),
                flat("b", [8.0, 0.0], [14.0, 4.0], 0.3),
            ])
        };
        assert_eq!(
            pull(&stairs(true), [9.0, 0.3, 2.0], [4.0, 0.0, 2.0]),
            Err(PullBlocked::MovingStair)
        );
        assert_eq!(
            pull(&stairs(true), [7.0, 0.15, 2.0], [1.0, 0.0, 2.0]),
            Err(PullBlocked::MovingStair)
        );
        // The same flight standing still is fine.
        let to = pull(&stairs(false), [9.0, 0.3, 2.0], [4.0, 0.0, 2.0]).unwrap();
        assert!(close(to, [6.5, 0.075, 2.0], 1e-3), "{to:?}");
    }

    /// An honest client running under the Pick-Me-Up Tonic (predicting with the same
    /// boosted parameters) is never corrected either.
    #[test]
    fn honest_boosted_clients_are_never_corrected() {
        let w = synthetic_castle(60);
        let fast = P.with_speed_pct(115);
        let (mut pos, mut accepted) = ([6.0, 0.0, 6.0], [6.0, 0.0, 6.0]);
        let mut rng = Rng(0x51ed_270b);
        let mut heading = 0.0f32;
        for frame in 1..=20_000 {
            if rng.next_u32().is_multiple_of(40) {
                heading = rng.range(0.0, std::f32::consts::TAU);
            }
            let step = fast.run_speed / 60.0;
            pos = move_and_slide(&w, &fast, pos, [heading.cos() * step, heading.sin() * step]);
            if frame % 6 == 0 {
                accepted = accept(check_move(&w, &fast, accepted, pos, 0.1));
            }
        }
    }

    /// `cargo test -p sim-core --release -- --ignored --nocapture bench`
    #[test]
    #[ignore]
    fn bench_check_move() {
        let w = synthetic_castle(500);
        assert_eq!(w.surfaces().len(), 500);
        let mut rng = Rng(12345);
        let moves: Vec<([f32; 3], [f32; 3])> = (0..4096)
            .map(|_| {
                let room = &w.surfaces()[(rng.next_u32() % 196) as usize];
                let x = rng.range(room.min[0], room.max[0]);
                let z = rng.range(room.min[1], room.max[1]);
                let a = rng.range(0.0, std::f32::consts::TAU);
                let d = rng.range(0.0, 0.8);
                ([x, 0.0, z], [x + a.cos() * d, 0.0, z + a.sin() * d])
            })
            .collect();
        let calls = 100_000;
        let t = std::time::Instant::now();
        let mut accepted = 0;
        for i in 0..calls {
            let (prev, next) = moves[i % moves.len()];
            let c = check_move(std::hint::black_box(&w), &P, prev, next, 0.1);
            accepted += matches!(c, MoveCheck::Accept { .. }) as u32;
        }
        let ns = t.elapsed().as_nanos() as f64 / calls as f64;
        println!("check_move: {ns:.0} ns/call over {calls} calls ({accepted} accepted)");
    }
}
