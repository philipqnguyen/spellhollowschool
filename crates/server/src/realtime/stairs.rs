//! The Grand Staircase's moving flights in the world
//! (`sim_core::stairs`, `data/world/stairs.json`; docs/architecture.md
//! "Moving staircases").
//!
//! The schedule is a pure function of the server's clock, which the client
//! shares (`Welcome.unix_ms`, `Pong`), so nothing is ever sent about it:
//!
//! - **The state's world.** The castle's stored positions are in one
//!   staircase state, [`WorldSim::stair_state`] (bit `i`: flight `i` at B).
//!   Walking moves are checked against the house's world of that state
//!   ([`WorldSim::castle_world`]).
//! - **Switches.** Each tick [`WorldSim::step_stairs`] compares the state the
//!   clock gives with the stored one. For every flight that switched, each
//!   castle character standing on its old surface (the old state's world,
//!   `ground_at`) is carried to the same spot of its new position
//!   (`Stairs::carry`, the same function the client carries itself with):
//!   no `Correction`; viewers get the new position in their next snapshot
//!   (a full state when the jump does not fit a delta). The few flights
//!   are scanned over the castle's characters with a rectangle test first;
//!   it runs once per switch (a minute apart in production) and is safe at
//!   any moment, unlike the tick's grid between rebuilds.
//! - **The grace window.** For `grace_ms` either side of a switch a move is
//!   checked against both positions of the switching flight
//!   (`Stairs::states_near`, `check_move_near`), so a client whose clock is
//!   a little early or late is not corrected. A client that carried itself
//!   before the server did (or after) sends a move from the other position:
//!   the server's position is carried into the state the move is checked in
//!   first, and the accepted position is carried back into the stored state
//!   (the switch carries it again when it comes).
//! - **Who rides** is decided by one rule on every end,
//!   `Stairs::carry_rider` (the ground under the character is the flight's
//!   surface, within the edges' 1 mm tolerance), and a carry keeps a rider
//!   a hair past one end a hair past the other, so carried there and back
//!   is the same ride (a load test found the one correction this caused).
//! - **An early step** from fixed ground onto a flight's incoming position
//!   within `EARLY_STEP_MS` of its switch is held where it stood until the
//!   switch, uncorrected; earlier it is refused (the reverse ride).

use std::time::Instant;

use protocol::{House, QPos};
use sim_core::{MoveCheck, MoveParams, StairPosition, check_move, check_move_near};

use super::entities::CASTLE;
use super::world::WorldSim;

/// How long before a switch a step onto the flight's incoming position is
/// held rather than refused (ms): an honest client's clock is at most 300 ms
/// early (`crates/server/tests/stairs.rs`), and a margin. Within the grace.
const EARLY_STEP_MS: i64 = 400;

/// The distance between two points on the XZ plane (metres).
pub(super) fn horizontal(a: [f32; 3], b: [f32; 3]) -> f32 {
    ((b[0] - a[0]).powi(2) + (b[2] - a[2]).powi(2)).sqrt()
}

/// Flight `flight`'s position in staircase state `state`.
fn position(state: u8, flight: usize) -> StairPosition {
    if state >> flight & 1 == 1 {
        StairPosition::B
    } else {
        StairPosition::A
    }
}

impl WorldSim {
    /// `pos` in staircase state `from` as it stands in state `to`: carried
    /// to the same spot of a flight that differs when it stands on one in
    /// `house`'s world of `from` (`Stairs::carry_rider`, the rule every end
    /// carries with), else unchanged.
    pub(super) fn carry_between(&self, house: House, pos: [f32; 3], from: u8, to: u8) -> [f32; 3] {
        let Some(stairs) = self.stairs.as_deref() else {
            return pos;
        };
        if from == to {
            return pos;
        }
        let layout = self.castle_all.layout();
        let world = self.house_worlds.get(house, from);
        (0..stairs.flights().len())
            .filter(|&f| position(from, f) != position(to, f))
            .find_map(|f| {
                stairs.carry_rider(layout, world, pos, f, position(from, f), position(to, f))
            })
            .unwrap_or(pos)
    }

    /// A saved or remembered castle position (from before the last
    /// switches) as it stands now: on a flight's other position, carried to
    /// its current one.
    pub(super) fn to_stair_state(&self, pos: [f32; 3]) -> [f32; 3] {
        let Some(stairs) = self.stairs.as_deref() else {
            return pos;
        };
        let layout = self.castle_all.layout();
        let now = self.stair_state;
        for (f, def) in stairs.flights().iter().enumerate() {
            let here = position(now, f);
            if let Some(q) = sim_core::stairs::flight_surface(layout, def, here.other())
                .filter(|s| s.contains(pos[0], pos[2]))
                .and_then(|_| stairs.carry(layout, pos, f, here.other(), here))
            {
                return q;
            }
        }
        pos
    }

    /// Checks a castle walking move of character `i` received at `at`:
    /// against the staircase state's world, and within the grace of a
    /// switch against the other position of the switching flight too.
    /// Returns the check (an accepted position in the stored state) and
    /// the distance walked (for the movement credit).
    pub(super) fn check_walk(
        &self,
        i: usize,
        at: Instant,
        params: &MoveParams,
        prev: [f32; 3],
        next: [f32; 3],
        credit: f32,
    ) -> (MoveCheck, f32) {
        let base = self.stair_state;
        let house = self.ents.house[i];
        let Some(stairs) = self.stairs.clone() else {
            let check = check_move(self.castle_world(house), params, prev, next, credit);
            return (check, horizontal(prev, next));
        };
        let mut near = stairs.states_near(self.clock.unix_ms_at(at));
        // The usual case: no switch near, the castle in the clock's state.
        if near.len() == 1 && near[0] == base {
            let check = check_move(self.castle_world(house), params, prev, next, credit);
            return (check, horizontal(prev, next));
        }
        // A switch is near (or the tick that carries its riders is still to
        // come): the stored state is one of the candidates too.
        if !near.contains(&base) {
            near.push(base);
        }
        let worlds = self.house_worlds.house(house).clone();
        let clock_state = near[0];
        // The state a flight is
        // about to switch into, within the grace before its switch.
        let ms = self.clock.unix_ms_at(at);
        let incoming = Some(stairs.state(ms + stairs.grace_ms())).filter(|&s| s != clock_state);
        // The server's position as it stands in each candidate state.
        let mut first = None;
        let mut hold = false;
        for &s in &near {
            let from = self.carry_between(house, prev, base, s);
            let (check, used) = check_move_near(&worlds, &[s], params, from, next, credit);
            match (check, used) {
                (MoveCheck::Accept { pos }, Some(s)) => {
                    let stored = self.carry_between(house, pos, s, base);
                    // A step from fixed ground (`from == prev`: nothing to
                    // carry) onto a flight's incoming position (`stored !=
                    // pos`: it stands on the flight that switches) before the
                    // switch would be stored on the flight's current position,
                    // across the well: never stored. Riders carried by the
                    // switch and moves on the flight where it is now are
                    // unaffected.
                    if Some(s) == incoming && s != base && from == prev && stored != pos {
                        // Within an honest clock error of the switch the
                        // client's clock has simply passed it first: held
                        // where it stood, uncorrected, until the switch (its
                        // next move is then a step from there onto the
                        // arrived flight). Earlier, refused.
                        if stairs.state(ms + EARLY_STEP_MS) == s {
                            hold = true;
                        } else {
                            self.metrics.stairs_reverse_refused.inc();
                            first.get_or_insert(MoveCheck::Reject(
                                sim_core::RejectReason::NotWalkable,
                            ));
                        }
                        continue;
                    }
                    if s != clock_state {
                        self.metrics.stairs_grace_accepts.inc();
                    }
                    return (MoveCheck::Accept { pos: stored }, horizontal(from, next));
                }
                (r, _) => {
                    first.get_or_insert(r);
                }
            }
        }
        if hold {
            self.metrics.stairs_early_holds.inc();
            return (MoveCheck::Accept { pos: prev }, 0.0);
        }
        let check = first.unwrap_or(MoveCheck::Reject(sim_core::RejectReason::NotWalkable));
        (check, horizontal(prev, next))
    }

    /// A castle walking move of character `i` received at `at` was
    /// corrected: within the grace of a staircase switch it is counted
    /// (`shs_stairs_corrections_total`), and every one is logged at debug
    /// (`server::realtime::stairs`) with the positions and staircase states,
    /// so one can be traced (`RUST_LOG=warn,server::realtime::stairs=debug`).
    pub(super) fn stair_correction(
        &self,
        i: usize,
        at: Instant,
        prev: [f32; 3],
        next: [f32; 3],
        reason: sim_core::RejectReason,
    ) {
        let Some(stairs) = self.stairs.as_deref() else {
            return;
        };
        let ms = self.clock.unix_ms_at(at);
        let near = stairs.states_near(ms);
        if near.len() > 1 {
            self.metrics.stairs_corrections.inc();
        }
        if !tracing::enabled!(tracing::Level::DEBUG) {
            return;
        }
        // Each flight's last and next switch, relative to the move (ms).
        let switches: Vec<(i64, i64)> = (0..stairs.flights().len())
            .map(|f| {
                let s = stairs.flight_state(f, ms);
                (s.last_switch_ms - ms, s.next_switch_ms - ms)
            })
            .collect();
        tracing::debug!(
            character = self.ents.info[i].character_id,
            ?reason,
            ?prev,
            ?next,
            stored_state = self.stair_state,
            clock_state = near[0],
            states_near = ?near,
            ?switches,
            "walking move corrected"
        );
    }

    /// Carries the riders of every flight that switched since the last call
    /// (the clock's staircase state at `at` against the stored one).
    pub(super) fn step_stairs(&mut self, at: Instant) {
        let Some(stairs) = self.stairs.clone() else {
            return;
        };
        let next = stairs.state(self.clock.unix_ms_at(at));
        let prev = self.stair_state;
        if next == prev {
            return;
        }
        let switched = prev ^ next;
        self.metrics
            .stairs_switches
            .add(u64::from(switched.count_ones()));
        // The switched flights' old surfaces: who might stand on them (with
        // the surfaces' own edge tolerance, as `Stairs::carry_rider` and the
        // clients have it: a load test's one correction was a rider
        // 0.35 mm past a flight's end that an exact rectangle left behind).
        let all = self.castle_all.clone();
        let layout = all.layout();
        let olds: Vec<&sim_core::layout::Surface> = stairs
            .flights()
            .iter()
            .enumerate()
            .filter(|&(f, _)| switched >> f & 1 == 1)
            .filter_map(|(f, def)| sim_core::stairs::flight_surface(layout, def, position(prev, f)))
            .collect();
        let mut carried = 0u64;
        for i in 0..self.ents.len() {
            let pos = self.ents.pos[i];
            if self.ents.zone[i] != CASTLE
                || self.ents.mover[i].mount.is_some()
                || !olds.iter().any(|s| s.contains(pos[0], pos[2]))
            {
                continue;
            }
            let q = self.carry_between(self.ents.house[i], pos, prev, next);
            if q == pos {
                continue;
            }
            // Facing the same way along the flight: turned with it.
            let turn: f32 = stairs
                .flights()
                .iter()
                .enumerate()
                .filter(|&(f, _)| switched >> f & 1 == 1)
                .filter(|&(f, def)| {
                    sim_core::stairs::flight_surface(layout, def, position(prev, f))
                        .is_some_and(|s| s.contains(pos[0], pos[2]))
                })
                .map(|(f, def)| match position(prev, f) {
                    StairPosition::A => def.angle_deg,
                    StairPosition::B => -def.angle_deg,
                })
                .next()
                .unwrap_or(0.0);
            self.ents.pos[i] = q;
            let level = self
                .house_worlds
                .get(self.ents.house[i], next)
                .level_at(q[0], q[2], q[1]);
            if let Some(level) = level {
                self.ents.level[i] = level;
            }
            let st = &mut self.ents.state[i];
            st.pos = QPos::from_m(q);
            st.yaw = st
                .yaw
                .wrapping_add((turn / 360.0 * 256.0).round() as i32 as u8);
            self.ents.mover[i].unsaved = true;
            carried += 1;
        }
        self.stair_state = next;
        self.metrics.stairs_riders_carried.add(carried);
    }

    /// Whether `at` is within a second of a staircase switch (the load
    /// test's tick round the switches).
    pub fn near_stair_switch(&self, at: Instant) -> bool {
        let Some(stairs) = self.stairs.as_deref() else {
            return false;
        };
        let ms = self.clock.unix_ms_at(at);
        (0..stairs.flights().len()).any(|f| {
            let s = stairs.flight_state(f, ms);
            ms - s.last_switch_ms <= 1_000 || s.next_switch_ms - ms <= 1_000
        })
    }
}
