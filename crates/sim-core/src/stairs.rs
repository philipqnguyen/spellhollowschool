//! The Grand Staircase's moving flights (`data/world/stairs.json`).
//!
//! Each moving flight is two stair surfaces of the castle layout tagged
//! `moving` ([`crate::layout::Moving`]): position A (the layout's resting
//! place) and position B, which is A turned about a vertical axis through
//! the flight's `pivot` by `angle_deg`. Only one of them exists at a time.
//!
//! **The schedule is a pure function of the clock** (Unix ms, the server's;
//! clients correct theirs from `Welcome.unix_ms` and `Pong`), the same on
//! every end, so no message is ever sent about it. A flight switches position
//! every `period` at `epoch + (phase + k) * period`: after switch `k` it
//! rests in A for even `k` and B for odd `k`. During the last `swing` of each
//! interval it swings toward its next position, and during the `warning`
//! before that it shudders (the client's dust and the chat line). The
//! walkable position changes only at the switch, the end of the swing:
//! riders are carried there ([`Stairs::carry`]), on both ends, by the same
//! rotation. For [`GRACE`] either side of a switch a move is accepted against
//! the old or the new position ([`Stairs::states_near`]).
//!
//! A **staircase state** is a `u8` with bit `i` set when flight `i` (in the
//! layout's order, [`crate::Layout::flights`]) is at B. [`Worlds`] holds one
//! [`World`] per state (and per house in [`HouseWorlds`]), built once.
//!
//! **Landing gates** (art and data only, no collision: walls are the absence
//! of ground): each landing edge a flight meets has a gate that is open while
//! its flight rests at that position and swings shut as it leaves
//! ([`Stairs::gate_open`]).

use std::sync::Arc;

use protocol::House;
use serde::{Deserialize, Serialize};

use crate::layout::{EDGE_EPSILON, Layout, LayoutError, StairPosition, Surface};
use crate::movement::{MoveCheck, MoveParams, check_move};
use crate::world::World;

/// The schema version of `data/world/stairs.json`.
pub const STAIRS_VERSION: u32 = 1;

/// How far a B surface may lie from A turned about the pivot (metres).
const ROTATION_TOLERANCE: f32 = 0.01;

#[derive(Debug, thiserror::Error)]
pub enum StairsError {
    #[error("stairs JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("stairs are inconsistent: {0}")]
    Invalid(String),
    #[error(transparent)]
    Layout(#[from] LayoutError),
}

/// One landing gate (`stairs.json` `flights[].gates[]`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Gate {
    pub id: String,
    /// The flight position whose arrival opens it.
    pub position: StairPosition,
    /// The surface whose edge it stands on.
    pub landing: String,
    /// The two hinges [x, z]: one leaf from each, meeting in the middle.
    pub line: [[f32; 2]; 2],
    /// The landing's floor height.
    pub y: f32,
    /// The unit direction [dx, dz] the leaves open to (onto the landing).
    pub opens: [f32; 2],
}

/// One moving flight (`stairs.json` `flights[]`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FlightDef {
    pub id: String,
    /// Display name ("Flight 6-7").
    pub name: String,
    /// The surface ids of position A and position B.
    pub a: String,
    pub b: String,
    /// The vertical axis it turns about, [x, z].
    pub pivot: [f32; 2],
    /// The turn from A to B, degrees about +y (right-handed: see
    /// [`rotate`]); a multiple of 90 so both positions are rectangles.
    pub angle_deg: f32,
    /// Where in the period its switches fall (0..1): the stagger.
    pub phase: f64,
    #[serde(default)]
    pub gates: Vec<Gate>,
}

/// `data/world/stairs.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StairsFile {
    pub version: u32,
    /// Seconds between a flight's switches (about 3 minutes).
    pub period_s: u32,
    /// The development period ([`Stairs::with_period`]); never in production.
    pub dev_period_s: u32,
    pub warning_s: u32,
    pub swing_s: u32,
    /// How long a gate takes to swing shut or open.
    pub gate_s: f32,
    /// Moves within this of a switch are accepted against either position.
    pub grace_ms: u32,
    /// The schedule's origin (Unix ms).
    pub epoch_ms: i64,
    pub flights: Vec<FlightDef>,
}

/// What a flight is doing at an instant.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FlightPhase {
    /// Resting at `at`; `warning`: the swing is less than `warning_s` away.
    Rest { at: StairPosition, warning: bool },
    /// Swinging from `from` to `to`; `progress` 0..1 (0 = at `from`).
    Swing {
        from: StairPosition,
        to: StairPosition,
        progress: f32,
    },
}

/// A flight's state at an instant.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FlightState {
    /// The position its walkable surface is in (it changes at the switch,
    /// the end of a swing).
    pub walk: StairPosition,
    pub phase: FlightPhase,
    /// The last switch at or before the instant, and the next one (Unix ms).
    pub last_switch_ms: i64,
    pub next_switch_ms: i64,
}

/// The validated schedule and geometry of the moving flights.
#[derive(Debug, Clone)]
pub struct Stairs {
    pub file: StairsFile,
    period_ms: i64,
}

/// The default grace either side of a switch (`stairs.json` `grace_ms`).
pub const GRACE: i64 = 1_000;

/// Turns `(dx, dz)` by `angle` radians about +y (right-handed, y up):
/// `(dx cos a + dz sin a, -dx sin a + dz cos a)`. A multiple of 90 degrees is
/// exact.
pub fn rotate(d: [f32; 2], angle_deg: f32) -> [f32; 2] {
    let quarter = angle_deg / 90.0;
    let (c, s) = if (quarter - quarter.round()).abs() < 1e-6 {
        match (quarter.round() as i32).rem_euclid(4) {
            0 => (1.0, 0.0),
            1 => (0.0, 1.0),
            2 => (-1.0, 0.0),
            _ => (0.0, -1.0),
        }
    } else {
        let a = angle_deg.to_radians();
        (a.cos(), a.sin())
    };
    [d[0] * c + d[1] * s, -d[0] * s + d[1] * c]
}

impl Stairs {
    /// Parses `stairs.json` and checks it against the castle layout: the
    /// flights are the layout's, in its order; each B surface is its A
    /// surface turned about the pivot (rectangle and heights, within 1 cm);
    /// every gate stands on an edge of its landing at its height; the swing
    /// and warning fit in the period (also the development one).
    pub fn from_json(json: &str, layout: &Layout) -> Result<Stairs, StairsError> {
        Stairs::with_layout(serde_json::from_str(json)?, layout)
    }

    /// [`Stairs::from_json`] for a parsed file.
    pub fn with_layout(file: StairsFile, layout: &Layout) -> Result<Stairs, StairsError> {
        let stairs = Stairs::new(file)?;
        stairs.check_layout(layout)?;
        Ok(stairs)
    }

    /// The schedule without the layout checks (the layout is checked by
    /// [`Stairs::from_json`]).
    pub fn new(file: StairsFile) -> Result<Stairs, StairsError> {
        let invalid = |m: String| Err(StairsError::Invalid(m));
        if file.version != STAIRS_VERSION {
            return invalid(format!("unsupported version {}", file.version));
        }
        let busy = u64::from(file.warning_s) + u64::from(file.swing_s);
        for p in [file.period_s, file.dev_period_s] {
            if u64::from(p) <= busy + 1 {
                return invalid(format!(
                    "a period of {p} s leaves no rest round a {busy} s warning and swing"
                ));
            }
        }
        if file.swing_s == 0 || !(file.gate_s > 0.0 && file.gate_s <= file.swing_s as f32) {
            return invalid("swing_s > 0 and 0 < gate_s <= swing_s".into());
        }
        if u64::from(file.grace_ms) * 2 >= u64::from(file.swing_s) * 1000 {
            return invalid("the grace must be shorter than half the swing".into());
        }
        if file.flights.is_empty() || file.flights.len() > 8 {
            return invalid("1 to 8 flights".into());
        }
        for (i, f) in file.flights.iter().enumerate() {
            if file.flights[..i].iter().any(|g| g.id == f.id) || f.id.is_empty() {
                return invalid(format!("flight {:?}: ids are unique", f.id));
            }
            if !(0.0..1.0).contains(&f.phase) {
                return invalid(format!("flight {}: phase in 0..1", f.id));
            }
            let q = f.angle_deg / 90.0;
            if (q - q.round()).abs() > 1e-6 || q.round() as i32 % 4 == 0 {
                return invalid(format!(
                    "flight {}: the angle turns a multiple of 90 degrees",
                    f.id
                ));
            }
        }
        let period_ms = i64::from(file.period_s) * 1000;
        Ok(Stairs { file, period_ms })
    }

    /// These stairs on a period of `period_s` seconds (the development
    /// override, `stairs.json` `dev_period_s`, or a test's): the phases
    /// stay fractions of it, so the stagger scales too.
    pub fn with_period(&self, period_s: u32) -> Result<Stairs, StairsError> {
        let mut file = self.file.clone();
        file.period_s = period_s;
        file.dev_period_s = file.dev_period_s.min(period_s);
        Stairs::new(file)
    }

    pub fn flights(&self) -> &[FlightDef] {
        &self.file.flights
    }

    /// The index of flight `id`.
    pub fn flight_index(&self, id: &str) -> Option<usize> {
        self.file.flights.iter().position(|f| f.id == id)
    }

    /// The number of staircase states (2 to the number of flights).
    pub fn states(&self) -> usize {
        1 << self.file.flights.len()
    }

    pub fn period_ms(&self) -> i64 {
        self.period_ms
    }

    pub fn grace_ms(&self) -> i64 {
        i64::from(self.file.grace_ms)
    }

    fn offset_ms(&self, flight: usize) -> i64 {
        self.file.epoch_ms
            + (self.file.flights[flight].phase * self.period_ms as f64).round() as i64
    }

    /// Flight `flight`'s state at `unix_ms`.
    pub fn flight_state(&self, flight: usize, unix_ms: i64) -> FlightState {
        let p = self.period_ms;
        let since0 = unix_ms - self.offset_ms(flight);
        let k = since0.div_euclid(p);
        let since = since0.rem_euclid(p);
        let at = |k: i64| {
            if k.rem_euclid(2) == 0 {
                StairPosition::A
            } else {
                StairPosition::B
            }
        };
        let walk = at(k);
        let swing = i64::from(self.file.swing_s) * 1000;
        let warning = i64::from(self.file.warning_s) * 1000;
        let phase = if since >= p - swing {
            FlightPhase::Swing {
                from: walk,
                to: at(k + 1),
                progress: ((since - (p - swing)) as f32 / swing as f32).clamp(0.0, 1.0),
            }
        } else {
            FlightPhase::Rest {
                at: walk,
                warning: since >= p - swing - warning,
            }
        };
        let last = unix_ms - since;
        FlightState {
            walk,
            phase,
            last_switch_ms: last,
            next_switch_ms: last + p,
        }
    }

    /// The staircase state (bit `i`: flight `i` at B) walked at `unix_ms`.
    pub fn state(&self, unix_ms: i64) -> u8 {
        (0..self.file.flights.len()).fold(0u8, |bits, i| {
            if self.flight_state(i, unix_ms).walk == StairPosition::B {
                bits | 1 << i
            } else {
                bits
            }
        })
    }

    /// The states a move received at `unix_ms` is checked against: the
    /// state at `unix_ms` first, then, for each flight within the grace of
    /// a switch (either side), the state with that flight in its other
    /// position. Without a switch near, just the one state.
    pub fn states_near(&self, unix_ms: i64) -> Vec<u8> {
        let now = self.state(unix_ms);
        let mut out = vec![now];
        let g = self.grace_ms();
        for i in 0..self.file.flights.len() {
            let s = self.flight_state(i, unix_ms);
            if unix_ms - s.last_switch_ms <= g || s.next_switch_ms - unix_ms <= g {
                let other = now ^ (1 << i);
                if !out.contains(&other) {
                    out.push(other);
                }
            }
        }
        out
    }

    /// The flights that switch in `(from_ms, to_ms]`, with their switch
    /// time and new position, in time order: the server carries each one's
    /// riders then.
    pub fn switches_between(&self, from_ms: i64, to_ms: i64) -> Vec<(usize, i64, StairPosition)> {
        let mut out = Vec::new();
        if to_ms <= from_ms {
            return out;
        }
        for i in 0..self.file.flights.len() {
            let mut s = self.flight_state(i, from_ms);
            while s.next_switch_ms <= to_ms {
                let at = s.next_switch_ms;
                s = self.flight_state(i, at);
                out.push((i, at, s.walk));
            }
        }
        out.sort_by_key(|&(i, at, _)| (at, i));
        out
    }

    /// How far gate `gate` of flight `flight` is open at `unix_ms` (0 shut,
    /// 1 open): open while the flight rests at the gate's position, closing
    /// over `gate_s` from the start of a swing away, opening over `gate_s`
    /// after the switch that brings it there.
    pub fn gate_open(&self, flight: usize, position: StairPosition, unix_ms: i64) -> f32 {
        let gate_ms = (self.file.gate_s * 1000.0) as i64;
        let s = self.flight_state(flight, unix_ms);
        match s.phase {
            FlightPhase::Rest { at, .. } if at == position => {
                ((unix_ms - s.last_switch_ms) as f32 / gate_ms as f32).clamp(0.0, 1.0)
            }
            FlightPhase::Swing { from, progress, .. } if from == position => {
                let into = progress * self.file.swing_s as f32 * 1000.0;
                (1.0 - into / gate_ms as f32).clamp(0.0, 1.0)
            }
            _ => 0.0,
        }
    }

    /// The angle (degrees) flight `flight`'s A model is turned at `unix_ms`:
    /// 0 at rest in A, `angle_deg` at rest in B, in between while swinging
    /// (eased in and out).
    pub fn turn_deg(&self, flight: usize, unix_ms: i64) -> f32 {
        let f = &self.file.flights[flight];
        let s = self.flight_state(flight, unix_ms);
        let t = match s.phase {
            FlightPhase::Rest { at, .. } => match at {
                StairPosition::A => 0.0,
                StairPosition::B => 1.0,
            },
            FlightPhase::Swing { from, progress, .. } => {
                let e = progress * progress * (3.0 - 2.0 * progress); // smoothstep
                match from {
                    StairPosition::A => e,
                    StairPosition::B => 1.0 - e,
                }
            }
        };
        f.angle_deg * t
    }

    /// Where a rider at `pos` on flight `flight`'s `from` position stands on
    /// its `to` position: the same spot on the flight, turned about the pivot
    /// (A to B by `angle_deg`, B to A back), at the `to` surface's height.
    /// `None` when `pos` is not on the `from` surface of `layout` (within
    /// the edge tolerance and a step of its ground).
    pub fn carry(
        &self,
        layout: &Layout,
        pos: [f32; 3],
        flight: usize,
        from: StairPosition,
        to: StairPosition,
    ) -> Option<[f32; 3]> {
        let f = self.file.flights.get(flight)?;
        let src = flight_surface(layout, f, from)?;
        let dst = flight_surface(layout, f, to)?;
        if !src.contains(pos[0], pos[2])
            || (src.height_at(pos[0], pos[2]) - pos[1]).abs()
                > MoveParams::default().step_height + EDGE_EPSILON
        {
            return None;
        }
        if from == to {
            return Some(pos);
        }
        let angle = if from == StairPosition::A {
            f.angle_deg
        } else {
            -f.angle_deg
        };
        // Kept within the edge tolerance, not pulled onto the rectangle: a
        // rider a hair past one end lands a hair past the other, on the same
        // continued slope, so the way back is the same ride (pulled onto the
        // edge, it stood level with the landing there, which `ground_at`
        // may pick instead: carried one way and not back).
        let d = rotate([pos[0] - f.pivot[0], pos[2] - f.pivot[1]], angle);
        let (x, z) = (
            (f.pivot[0] + d[0]).clamp(dst.min[0] - EDGE_EPSILON, dst.max[0] + EDGE_EPSILON),
            (f.pivot[1] + d[1]).clamp(dst.min[1] - EDGE_EPSILON, dst.max[1] + EDGE_EPSILON),
        );
        Some([x, dst.height_at(x, z), z])
    }

    /// Who rides: [`Stairs::carry`] for a character at `pos` whose ground in
    /// `world` (the walked world of a state with flight `flight` at `from`)
    /// is that flight's surface, else `None`. Every end (the server's
    /// switch and grace, the browser, the bots) decides who a switch
    /// carries with this, so they agree to the millimetre: where a flight's
    /// end meets a landing, a point within [`EDGE_EPSILON`] of both is the
    /// ground [`World::ground_at`] picks, not "on the flight" by its
    /// rectangle alone (a load test's one correction: a rider 0.35 mm
    /// past a flight's end, carried by itself and not by the server).
    pub fn carry_rider(
        &self,
        layout: &Layout,
        world: &World,
        pos: [f32; 3],
        flight: usize,
        from: StairPosition,
        to: StairPosition,
    ) -> Option<[f32; 3]> {
        let src = flight_surface(layout, self.file.flights.get(flight)?, from)?;
        let g = world.ground_at(pos[0], pos[2], pos[1])?;
        if world.surface(g.surface).id != src.id {
            return None;
        }
        self.carry(layout, pos, flight, from, to)
    }

    fn check_layout(&self, layout: &Layout) -> Result<(), StairsError> {
        let invalid = |m: String| Err(StairsError::Invalid(m));
        let order = layout.flights();
        let ids: Vec<&str> = self.file.flights.iter().map(|f| f.id.as_str()).collect();
        if order != ids {
            return invalid(format!(
                "the flights {ids:?} are not the layout's {order:?} in its order"
            ));
        }
        for f in &self.file.flights {
            let (Some(a), Some(b)) = (
                flight_surface(layout, f, StairPosition::A),
                flight_surface(layout, f, StairPosition::B),
            ) else {
                return invalid(format!(
                    "flight {}: its surfaces {} and {} are not its A and B",
                    f.id, f.a, f.b
                ));
            };
            // B is A turned about the pivot: corners onto corners, heights kept.
            let corners = [
                [a.min[0], a.min[1]],
                [a.max[0], a.min[1]],
                [a.min[0], a.max[1]],
                [a.max[0], a.max[1]],
            ];
            let mut lo = [f32::INFINITY; 2];
            let mut hi = [f32::NEG_INFINITY; 2];
            for c in corners {
                let d = rotate([c[0] - f.pivot[0], c[1] - f.pivot[1]], f.angle_deg);
                let p = [f.pivot[0] + d[0], f.pivot[1] + d[1]];
                for k in 0..2 {
                    lo[k] = lo[k].min(p[k]);
                    hi[k] = hi[k].max(p[k]);
                }
                let (ha, hb) = (a.height_at(c[0], c[1]), b.height_at(p[0], p[1]));
                if (ha - hb).abs() > ROTATION_TOLERANCE {
                    return invalid(format!(
                        "flight {}: B's height {hb} at {p:?} is not A's {ha}",
                        f.id
                    ));
                }
            }
            let off = (0..2)
                .map(|k| (lo[k] - b.min[k]).abs().max((hi[k] - b.max[k]).abs()))
                .fold(0.0f32, f32::max);
            if off > ROTATION_TOLERANCE {
                return invalid(format!(
                    "flight {}: B is not A turned {} degrees about the pivot ({off} m off)",
                    f.id, f.angle_deg
                ));
            }
            for g in &f.gates {
                let Some(l) = layout.surfaces.iter().find(|s| s.id == g.landing) else {
                    return invalid(format!("gate {}: no landing {}", g.id, g.landing));
                };
                let [p, q] = g.line;
                let on_edge = |x: f32, z: f32| {
                    let near = |v: f32, w: f32| (v - w).abs() <= ROTATION_TOLERANCE;
                    l.contains(x, z)
                        && (near(x, l.min[0])
                            || near(x, l.max[0])
                            || near(z, l.min[1])
                            || near(z, l.max[1]))
                };
                let len = ((q[0] - p[0]).powi(2) + (q[1] - p[1]).powi(2)).sqrt();
                let unit = (g.opens[0].powi(2) + g.opens[1].powi(2) - 1.0).abs() < 1e-3;
                let mid = [
                    (p[0] + q[0]) / 2.0 + g.opens[0] * 0.5,
                    (p[1] + q[1]) / 2.0 + g.opens[1] * 0.5,
                ];
                if !on_edge(p[0], p[1])
                    || !on_edge(q[0], q[1])
                    || len < 1.0
                    || (l.height_at(p[0], p[1]) - g.y).abs() > ROTATION_TOLERANCE
                    || !unit
                    || !l.contains(mid[0], mid[1])
                    || l.moving.is_some()
                {
                    return invalid(format!(
                        "gate {}: not on an edge of {} opening onto it",
                        g.id, g.landing
                    ));
                }
            }
        }
        Ok(())
    }
}

/// Flight `f`'s surface at `position` in `layout` (checked against its
/// `moving` tag).
pub fn flight_surface<'a>(
    layout: &'a Layout,
    f: &FlightDef,
    position: StairPosition,
) -> Option<&'a Surface> {
    let id = match position {
        StairPosition::A => &f.a,
        StairPosition::B => &f.b,
    };
    layout.surfaces.iter().find(|s| {
        s.id == *id
            && s.moving
                .as_ref()
                .is_some_and(|m| m.flight == f.id && m.position == position)
    })
}

/// One [`World`] per staircase state of one layout (a house's, or the whole
/// castle's), built once and shared.
#[derive(Debug, Clone)]
pub struct Worlds {
    by_state: Vec<Arc<World>>,
}

impl Worlds {
    /// The worlds of `layout` in every staircase state ([`Layout::for_stairs`]).
    pub fn new(layout: &Layout) -> Result<Worlds, LayoutError> {
        let n = layout.flights().len();
        let by_state = (0..1u16 << n)
            .map(|s| World::new(layout.for_stairs(s as u8)).map(Arc::new))
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Worlds { by_state })
    }

    /// The world walked in staircase state `state` (bits beyond the layout's
    /// flights are ignored).
    pub fn get(&self, state: u8) -> &Arc<World> {
        &self.by_state[usize::from(state) % self.by_state.len()]
    }

    pub fn states(&self) -> usize {
        self.by_state.len()
    }

    pub fn iter(&self) -> impl Iterator<Item = &Arc<World>> {
        self.by_state.iter()
    }
}

/// The castle's [`Worlds`] for each house (`House::ALL` order): 4 x 8 worlds.
#[derive(Debug, Clone)]
pub struct HouseWorlds {
    houses: [Worlds; 4],
}

impl HouseWorlds {
    pub fn new(layout: &Layout) -> Result<HouseWorlds, LayoutError> {
        let mut out: Vec<Worlds> = Vec::with_capacity(4);
        for h in House::ALL {
            out.push(Worlds::new(&layout.for_house(h))?);
        }
        let houses: [Worlds; 4] = out.try_into().expect("four houses");
        Ok(HouseWorlds { houses })
    }

    /// House `house`'s worlds.
    pub fn house(&self, house: House) -> &Worlds {
        let i = House::ALL
            .iter()
            .position(|&h| h == house)
            .expect("in House::ALL");
        &self.houses[i]
    }

    /// House `house`'s world in staircase state `state`.
    pub fn get(&self, house: House, state: u8) -> &Arc<World> {
        self.house(house).get(state)
    }
}

/// [`check_move`] against each of `states` (from [`Stairs::states_near`]),
/// in order: the first acceptance wins, else the first state's rejection.
/// Within the grace of a switch an honest client a little early or late is
/// accepted against the position it predicted with.
pub fn check_move_near(
    worlds: &Worlds,
    states: &[u8],
    params: &MoveParams,
    prev: [f32; 3],
    next: [f32; 3],
    dt_seconds: f32,
) -> (MoveCheck, Option<u8>) {
    let mut first = None;
    for &s in states {
        match check_move(worlds.get(s), params, prev, next, dt_seconds) {
            MoveCheck::Accept { pos } => return (MoveCheck::Accept { pos }, Some(s)),
            r => {
                first.get_or_insert(r);
            }
        }
    }
    (
        first.unwrap_or(MoveCheck::Reject(
            crate::movement::RejectReason::NotWalkable,
        )),
        None,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::movement::move_and_slide;

    const STAIRS: &str = include_str!("../../../data/world/stairs.json");
    const LAYOUT: &str = include_str!("../../../data/world/layout.json");

    fn shipped() -> (Stairs, Layout) {
        let layout = Layout::from_json(LAYOUT).unwrap();
        (Stairs::from_json(STAIRS, &layout).unwrap(), layout)
    }

    #[test]
    fn the_shipped_stairs_fit_the_layout() {
        let (s, layout) = shipped();
        assert_eq!(s.flights().len(), 3);
        assert_eq!(s.states(), 8);
        assert_eq!(layout.flights(), ["flight_3_4", "flight_5_6", "flight_6_7"]);
        assert_eq!(
            (
                s.file.period_s,
                s.file.warning_s,
                s.file.swing_s,
                s.file.grace_ms
            ),
            (180, 5, 6, 1000)
        );
        assert_eq!(s.flights().iter().map(|f| f.gates.len()).sum::<usize>(), 12);
        for f in s.flights() {
            for pos in [StairPosition::A, StairPosition::B] {
                assert_eq!(
                    f.gates.iter().filter(|g| g.position == pos).count(),
                    2,
                    "{} {pos:?}",
                    f.id
                );
            }
        }
    }

    #[test]
    fn bad_files_are_refused() {
        let (s, layout) = shipped();
        let mut f = s.file.clone();
        f.flights[0].angle_deg = 45.0;
        assert!(Stairs::new(f).is_err());
        let mut f = s.file.clone();
        f.dev_period_s = 11;
        assert!(Stairs::new(f).is_err());
        let mut f = s.file.clone();
        f.flights[0].pivot[0] += 1.0;
        assert!(
            Stairs::new(f.clone())
                .unwrap()
                .check_layout(&layout)
                .is_err()
        );
        let mut f = s.file.clone();
        f.flights.swap(0, 1);
        assert!(Stairs::new(f).unwrap().check_layout(&layout).is_err());
        let mut f = s.file.clone();
        f.flights[2].gates[0].line[1][0] += 3.0; // off the gallery's edge
        assert!(Stairs::new(f).unwrap().check_layout(&layout).is_err());
        let mut f = s.file.clone();
        f.flights[2].gates[0].opens = [-1.0, 0.0];
        assert!(Stairs::new(f).unwrap().check_layout(&layout).is_err());
    }

    /// The schedule: every flight switches every period, alternating A and
    /// B, staggered by its phase; the swing fills the last 6 s before a
    /// switch and the warning the 5 s before that.
    #[test]
    fn the_schedule() {
        let (s, _) = shipped();
        let p = 180_000;
        assert_eq!(s.period_ms(), p);
        // Flight 0 (phase 0) switches at multiples of the period.
        let at = |t: i64| s.flight_state(0, t);
        assert_eq!(at(0).walk, StairPosition::A);
        assert_eq!(at(0).last_switch_ms, 0);
        assert_eq!(at(p - 1).walk, StairPosition::A);
        assert_eq!(at(p).walk, StairPosition::B);
        assert_eq!(at(2 * p).walk, StairPosition::A);
        assert_eq!(at(-1).walk, StairPosition::B, "before the epoch too");
        assert!(matches!(
            at(1000).phase,
            FlightPhase::Rest {
                at: StairPosition::A,
                warning: false
            }
        ));
        assert!(matches!(
            at(p - 11_001).phase,
            FlightPhase::Rest { warning: false, .. }
        ));
        assert!(matches!(
            at(p - 11_000).phase,
            FlightPhase::Rest { warning: true, .. }
        ));
        assert!(matches!(
            at(p - 6_001).phase,
            FlightPhase::Rest { warning: true, .. }
        ));
        match at(p - 3_000).phase {
            FlightPhase::Swing { from, to, progress } => {
                assert_eq!((from, to), (StairPosition::A, StairPosition::B));
                assert!((progress - 0.5).abs() < 1e-6);
            }
            other => panic!("{other:?}"),
        }
        assert!(matches!(
            at(p + 10).phase,
            FlightPhase::Rest {
                at: StairPosition::B,
                ..
            }
        ));
        // The stagger: a switch somewhere every minute.
        let mut switches: Vec<i64> = s
            .switches_between(0, 2 * p)
            .iter()
            .map(|&(_, t, _)| t)
            .collect();
        switches.dedup();
        assert_eq!(switches.len(), 6);
        for w in switches.windows(2) {
            assert!((w[1] - w[0] - 60_000).abs() <= 1, "{switches:?}");
        }
        // The state's bits follow the flights (at the epoch the staggered two are
        // still at B from their previous switch).
        assert_eq!(s.state(10), 0b110);
        assert_eq!(s.state(61_000), 0b100);
        assert_eq!(s.state(121_000), 0);
        assert_eq!(s.state(p + 10), 1);
        assert_eq!(s.state(p + 61_000), 0b011);
        assert_eq!(s.state(p + 121_000), 0b111);
        assert_eq!(s.state(2 * p + 10), 0b110);
        // The development period scales the stagger.
        let fast = s.with_period(s.file.dev_period_s).unwrap();
        assert_eq!(fast.period_ms(), 20_000);
        assert_eq!(fast.switches_between(0, 60_000).len(), 9);
        assert!(
            s.with_period(10).is_err(),
            "no room for the warning and the swing"
        );
    }

    /// The grace window: within 1 s either side of a switch both positions
    /// are accepted, the current one first; elsewhere only the current one.
    #[test]
    fn the_grace_window() {
        let (s, _) = shipped();
        let p = s.period_ms();
        assert_eq!(s.states_near(p / 2), vec![0b100]);
        assert_eq!(s.states_near(p - 1_000), vec![0, 1]);
        assert_eq!(s.states_near(p - 1_001), vec![0]);
        assert_eq!(s.states_near(p), vec![1, 0]);
        assert_eq!(s.states_near(p + 1_000), vec![1, 0]);
        assert_eq!(s.states_near(p + 1_001), vec![1]);
    }

    /// The gates: open at rest at their position, shutting during the
    /// swing's first second away, opening during the second after the
    /// arrival.
    #[test]
    fn gates_follow_the_flight() {
        let (s, _) = shipped();
        let p = s.period_ms();
        let (a, b) = (StairPosition::A, StairPosition::B);
        assert_eq!(s.gate_open(0, a, p / 2), 1.0);
        assert_eq!(s.gate_open(0, b, p / 2), 0.0);
        assert_eq!(s.gate_open(0, a, p - 6_000), 1.0, "the swing starts");
        assert!((s.gate_open(0, a, p - 5_500) - 0.5).abs() < 1e-3);
        assert_eq!(s.gate_open(0, a, p - 4_000), 0.0);
        assert_eq!(s.gate_open(0, b, p - 1), 0.0, "still swinging in");
        assert_eq!(s.gate_open(0, b, p), 0.0);
        assert!((s.gate_open(0, b, p + 500) - 0.5).abs() < 1e-3);
        assert_eq!(s.gate_open(0, b, p + 1_000), 1.0);
        assert_eq!(s.gate_open(0, a, p + 1_000), 0.0);
        // The model's turn: 0 at A, the angle at B, eased in between.
        assert_eq!(s.turn_deg(0, p / 2), 0.0);
        assert_eq!(s.turn_deg(0, p - 3_000), -45.0);
        assert_eq!(s.turn_deg(0, p + 10), -90.0);
        assert_eq!(s.turn_deg(0, 2 * p - 3_000), -45.0);
    }

    /// Carrying: a rider on A lands on the same spot of B (turned about the
    /// pivot, at B's height there) and back again; off the flight, nothing.
    #[test]
    fn riders_are_carried() {
        let (s, layout) = shipped();
        let (a, b) = (StairPosition::A, StairPosition::B);
        for (i, f) in s.flights().iter().enumerate() {
            let src = flight_surface(&layout, f, a).unwrap();
            let dst = flight_surface(&layout, f, b).unwrap();
            for (u, v) in [(0.1, 0.1), (0.5, 0.5), (0.9, 0.2), (0.3, 0.95)] {
                let x = src.min[0] + (src.max[0] - src.min[0]) * u;
                let z = src.min[1] + (src.max[1] - src.min[1]) * v;
                let p = [x, src.height_at(x, z), z];
                let q = s.carry(&layout, p, i, a, b).unwrap();
                assert!(dst.contains(q[0], q[2]), "{} {q:?}", f.id);
                assert!(
                    (q[1] - p[1]).abs() < 1e-3,
                    "{} keeps its height: {p:?} -> {q:?}",
                    f.id
                );
                assert!((q[1] - dst.height_at(q[0], q[2])).abs() < 1e-4);
                let back = s.carry(&layout, q, i, b, a).unwrap();
                for k in 0..3 {
                    assert!(
                        (back[k] - p[k]).abs() < 1e-3,
                        "{} round trip {p:?} -> {back:?}",
                        f.id
                    );
                }
            }
            // Off the flight (the pivot's far side, or a storey away): not a rider.
            let c = [src.min[0] - 3.0, src.y, src.min[1] - 3.0];
            assert!(s.carry(&layout, c, i, a, b).is_none());
            let mid = [
                (src.min[0] + src.max[0]) / 2.0,
                0.0,
                (src.min[1] + src.max[1]) / 2.0,
            ];
            assert!(s.carry(&layout, mid, i, a, b).is_none());
        }
    }

    /// Who rides, at the edges (a load test's one correction): a
    /// walker anywhere along a flight's edges, a hair inside, on, or a hair
    /// past them (within the 1 mm tolerance), whom a switch carries is
    /// carried back by the next one to where it stood, so a move carried
    /// into the other state and the switch's carry agree. A hair past an
    /// end the slope continues above the landing's floor there and the
    /// walker rides at both ends of the trip.
    #[test]
    fn riders_ride_back_from_the_edges() {
        let (s, layout) = shipped();
        let worlds = Worlds::new(&layout).unwrap();
        let params = MoveParams::default();
        let mut rode = 0;
        let mut past = 0;
        for (i, f) in s.flights().iter().enumerate() {
            for (from, to) in [
                (StairPosition::A, StairPosition::B),
                (StairPosition::B, StairPosition::A),
            ] {
                let state = |p: StairPosition| if p == StairPosition::B { 1u8 << i } else { 0 };
                let (wf, wt) = (worlds.get(state(from)), worlds.get(state(to)));
                let src = flight_surface(&layout, f, from).unwrap();
                for off in [-0.000_5f32, 0.0, 0.000_35, 0.000_7] {
                    for t in [0.0f32, 0.1, 0.5, 0.9, 1.0] {
                        let (x0, x1, z0, z1) = (src.min[0], src.max[0], src.min[1], src.max[1]);
                        let (lx, lz) = (x0 + (x1 - x0) * t, z0 + (z1 - z0) * t);
                        for (x, z) in [
                            (x0 - off, lz),
                            (x1 + off, lz),
                            (lx, z0 - off),
                            (lx, z1 + off),
                        ] {
                            let y0 = src.height_at(x.clamp(x0, x1), z.clamp(z0, z1));
                            // Where a walker stands there: the highest ground in reach.
                            let Some(g) = wf.step_ground(x, z, y0, params.step_height) else {
                                continue;
                            };
                            let p = [x, g.y, z];
                            let Some(q) = s.carry_rider(&layout, wf, p, i, from, to) else {
                                continue;
                            };
                            rode += 1;
                            if off > 0.0 && wf.surface(g.surface).id == src.id {
                                past += 1;
                            }
                            let back = s.carry_rider(&layout, wt, q, i, to, from);
                            let back = back.unwrap_or_else(|| {
                                panic!("{} {from:?} {p:?} -> {q:?} is not carried back", f.id)
                            });
                            for k in 0..3 {
                                assert!(
                                    (back[k] - p[k]).abs() < 1e-3,
                                    "{} {from:?} round trip {p:?} -> {q:?} -> {back:?}",
                                    f.id
                                );
                            }
                        }
                    }
                }
            }
        }
        assert!(rode > 100 && past > 0, "{rode} riders, {past} past an end");
    }

    /// A carried rider stands on walkable ground of the new state and can
    /// walk off the flight at its new landing.
    #[test]
    fn carried_riders_walk_on() {
        let (s, layout) = shipped();
        let worlds = Worlds::new(&layout).unwrap();
        let params = MoveParams::default();
        for (i, f) in s.flights().iter().enumerate() {
            let src = flight_surface(&layout, f, StairPosition::A).unwrap();
            let x = (src.min[0] + src.max[0]) / 2.0;
            let z = (src.min[1] + src.max[1]) / 2.0;
            let p = [x, src.height_at(x, z), z];
            let q = s
                .carry(&layout, p, i, StairPosition::A, StairPosition::B)
                .unwrap();
            let w = worlds.get(1 << i);
            let g = w.ground_at(q[0], q[2], q[1]).unwrap();
            assert_eq!(w.surface(g.surface).id, f.b);
            assert!(
                move_and_slide(w, &params, q, [0.3, 0.0]) != q
                    || move_and_slide(w, &params, q, [0.0, 0.3]) != q
            );
        }
    }

    /// Each state's world has exactly one position of each flight.
    #[test]
    fn worlds_per_state() {
        let (_, layout) = shipped();
        let worlds = Worlds::new(&layout).unwrap();
        assert_eq!(worlds.states(), 8);
        for state in 0..8u8 {
            let w = worlds.get(state);
            for (i, f) in ["flight_3_4", "flight_5_6", "flight_6_7"]
                .iter()
                .enumerate()
            {
                let at: Vec<StairPosition> = w
                    .surfaces()
                    .iter()
                    .filter_map(|s| {
                        s.moving
                            .as_ref()
                            .filter(|m| m.flight == *f)
                            .map(|m| m.position)
                    })
                    .collect();
                let want = if state >> i & 1 == 1 {
                    StairPosition::B
                } else {
                    StairPosition::A
                };
                assert_eq!(at, vec![want], "{f} in state {state}");
            }
        }
        let hw = HouseWorlds::new(&layout).unwrap();
        let mine = World::new(layout.for_house(House::Roarwing).for_stairs(5)).unwrap();
        assert_eq!(
            hw.get(House::Roarwing, 5).surfaces().len(),
            mine.surfaces().len()
        );
        assert!(mine.surfaces().len() < worlds.get(5).surfaces().len());
    }

    /// The grace window in a check: a move onto flight 6-7's B position,
    /// reported just before its switch by a client whose clock runs a little
    /// fast, is accepted against the next state; long before, it is not.
    #[test]
    fn grace_accepts_either_position() {
        let (s, layout) = shipped();
        let worlds = Worlds::new(&layout).unwrap();
        let params = MoveParams::default();
        let f = 2;
        // From the sixth-floor north gallery onto B's foot.
        let prev = [-39.75, 54.0, -11.8];
        let next = [-39.75, 54.0 + 9.0 * 0.3 / 16.0, -11.2];
        let switch = s
            .switches_between(0, 2 * s.period_ms())
            .into_iter()
            .find(|&(i, _, to)| i == f && to == StairPosition::B)
            .unwrap()
            .1;
        let early = switch - 500;
        let near = s.states_near(early);
        assert_eq!(near[0] >> f & 1, 0, "still at A");
        let (c, used) = check_move_near(&worlds, &near, &params, prev, next, 0.1);
        assert!(matches!(c, MoveCheck::Accept { .. }), "{c:?}");
        assert_eq!(used, Some(near[0] | 1 << f));
        let (c, _) = check_move_near(
            &worlds,
            &s.states_near(switch - 5_000),
            &params,
            prev,
            next,
            0.1,
        );
        assert!(matches!(c, MoveCheck::Reject(_)), "{c:?}");
    }
}
