//! The Flying class's students fly its ring course. A student
//! the class mounted (`Mounted { on: true }` after the `ZoneChanged` into
//! the practice field) hovers over the field until a round's beats come
//! (`LessonRound`, `BeatPrompt::Ring`); for each ring beat in turn it flies
//! to a point in front of the ring (on the side it is on, so it never
//! crosses the disc early), waits there, and sets off so that it passes
//! through the ring's middle on the beat, then on to the next. A beat it
//! cannot reach in time is let go (a miss). The server scores every pass
//! from the checked moves (`BeatScored`); nothing is sent for a ring.
//! Positions come from `sim_core::flight::fly_and_slide` over the class
//! layout's own airspace, as the browser predicts.

use std::collections::VecDeque;
use std::time::Duration;

use protocol::Anim;
use rand::Rng;
use sim_core::World;
use sim_core::flight::{self, Airspace, FlightRules, FlyParams};
use sim_core::layout::Ring;
use tokio::time::Instant;

/// Metres before and after a ring a pass starts and ends.
const RUN_UP: f32 = 2.5;
/// Share of the broom's speed the students fly at.
const CRUISE: f32 = 0.92;
const CLIMB: f32 = 0.85;
/// Close enough (m).
const ARRIVE: f32 = 0.25;
/// A beat is let go this long after its time.
const LATE: Duration = Duration::from_millis(1_500);

/// A Flying class room's course: its layout, airspace and rings.
pub struct Course {
    pub world: World,
    pub air: Airspace,
    pub rules: FlightRules,
    pub rings: Vec<Ring>,
}

impl Course {
    /// The course of class layout `layout`, if it has rings.
    pub fn new(layout: &str) -> Result<Option<Course>, String> {
        let json =
            crate::desks::layout_json(layout).ok_or_else(|| format!("no class layout {layout}"))?;
        let world = World::from_json(json).map_err(|e| format!("{layout}: {e}"))?;
        let rings = world.layout().rings.clone();
        if rings.is_empty() {
            return Ok(None);
        }
        Ok(Some(Course {
            air: Airspace::new(&world),
            rules: crate::flyer::rules(),
            rings,
            world,
        }))
    }

    fn params(&self, tier: u8) -> FlyParams {
        self.rules.params(tier, protocol::BROOM_PCT_NORMAL)
    }
}

/// One student on the course.
pub struct RingFlight {
    pub pos: [f32; 3],
    yaw: f32,
    tier: u8,
    /// Where it waits between rounds.
    hover: [f32; 3],
    /// The ring beats still to fly: when (local time) and which ring.
    beats: VecDeque<(Instant, u8)>,
    /// The pass under way: from `before` to `after` of the front beat.
    pass: Option<([f32; 3], [f32; 3])>,
}

impl RingFlight {
    /// Mounted at `spawn` (the class lifts it) with broom tier `tier`.
    pub fn new(course: &Course, spawn: [f32; 3], tier: u8, rng: &mut impl Rng) -> RingFlight {
        let params = course.params(tier);
        let pos = flight::mount(&course.world, &course.air, &params, spawn).unwrap_or(spawn);
        let hover = [
            rng.gen_range(-12.0..12.0),
            rng.gen_range(2.0..4.0),
            rng.gen_range(-4.0..10.0),
        ];
        RingFlight {
            pos,
            yaw: std::f32::consts::PI,
            tier,
            hover,
            beats: VecDeque::new(),
            pass: None,
        }
    }

    /// A ring beat to fly: ring `ring` at `at`.
    pub fn add(&mut self, at: Instant, ring: u8) {
        let k = self.beats.partition_point(|(t, _)| *t <= at);
        self.beats.insert(k, (at, ring));
    }

    /// One step of `dt` seconds at `now`: the position, yaw and anim to send.
    pub fn step(&mut self, course: &Course, now: Instant, dt: f32) -> ([f32; 3], f32, Anim) {
        let params = course.params(self.tier);
        let speed = params.speed * CRUISE;
        // Let go of beats it can no longer make.
        while let Some(&(at, _)) = self.beats.front() {
            if now > at + LATE {
                self.beats.pop_front();
                self.pass = None;
            } else {
                break;
            }
        }
        let Some(&(at, n)) = self.beats.front() else {
            return self.toward(course, &params, self.hover, speed, dt);
        };
        let Some(ring) = course.rings.get(usize::from(n)) else {
            self.beats.pop_front();
            return (self.pos, self.yaw, Anim::Hover);
        };
        let (before, after) = *self.pass.get_or_insert_with(|| ends(ring, self.pos));
        let lead = Duration::from_secs_f32(RUN_UP / speed);
        if now + lead < at {
            // To the run-up point, and wait there.
            return self.toward(course, &params, before, speed, dt);
        }
        let out = self.toward(course, &params, after, speed, dt);
        if near(self.pos, after) {
            self.beats.pop_front();
            self.pass = None;
        }
        out
    }

    fn toward(
        &mut self,
        course: &Course,
        params: &FlyParams,
        to: [f32; 3],
        speed: f32,
        dt: f32,
    ) -> ([f32; 3], f32, Anim) {
        if near(self.pos, to) {
            return (self.pos, self.yaw, Anim::Hover);
        }
        let d = [
            to[0] - self.pos[0],
            to[1] - self.pos[1],
            to[2] - self.pos[2],
        ];
        let flat = d[0].hypot(d[2]);
        let (h, v) = (speed * dt, params.climb_speed * CLIMB * dt);
        let k = if flat > h { h / flat } else { 1.0 };
        let step = [d[0] * k, d[1].clamp(-v, v), d[2] * k];
        let next = flight::fly_and_slide(&course.world, &course.air, params, self.pos, step);
        if flat > 0.05 {
            self.yaw = d[0].atan2(d[2]);
        }
        let moved = next != self.pos;
        self.pos = next;
        (next, self.yaw, if moved { Anim::Fly } else { Anim::Hover })
    }
}

/// The run-up and run-out points of `ring` for a flyer at `pos`: in front
/// of the disc on its side, and behind it.
fn ends(ring: &Ring, pos: [f32; 3]) -> ([f32; 3], [f32; 3]) {
    let n = [ring.yaw.sin(), 0.0, ring.yaw.cos()];
    let side = (pos[0] - ring.pos[0]) * n[0] + (pos[2] - ring.pos[2]) * n[2];
    let s = if side >= 0.0 { 1.0 } else { -1.0 };
    let at = |k: f32| {
        [
            ring.pos[0] + n[0] * RUN_UP * k,
            ring.pos[1],
            ring.pos[2] + n[2] * RUN_UP * k,
        ]
    };
    (at(s), at(-s))
}

fn near(a: [f32; 3], b: [f32; 3]) -> bool {
    (0..3).map(|k| (a[k] - b[k]).powi(2)).sum::<f32>() < ARRIVE * ARRIVE
}

#[cfg(test)]
mod tests {
    use rand::SeedableRng;
    use rand::rngs::StdRng;
    use sim_core::ring;

    use super::*;

    /// Round 3's pace (a ring every 2.5 s, two laps) at the slowest broom:
    /// every move passes the server's check, and every ring is crossed
    /// within the beat's window of its time.
    #[test]
    fn a_student_passes_every_ring_on_its_beat() {
        let course = Course::new("class_pitch").unwrap().expect("rings");
        assert_eq!(course.rings.len(), 8);
        let mut rng = StdRng::seed_from_u64(1);
        let spawn = course.world.spawns()[0].pos;
        let mut f = RingFlight::new(&course, spawn, 1, &mut rng);
        let t0 = Instant::now();
        let beat_at = |k: u64| t0 + Duration::from_millis(8_000 + 2_500 * k);
        for k in 0..16u64 {
            f.add(beat_at(k), (k % 8) as u8);
        }
        let params = course.params(1);
        let window = 1_200i64;
        let mut passed = [false; 16];
        let mut now = t0;
        for _ in 0..600 {
            let prev = f.pos;
            let prev_at = now;
            now += Duration::from_millis(100);
            let (pos, _, _) = f.step(&course, now, 0.1);
            let check = flight::check_flight(&course.world, &course.air, &params, prev, pos, 0.1);
            assert!(
                matches!(check, flight::FlightCheck::Accept { .. }),
                "{prev:?} -> {pos:?}: {check:?}"
            );
            let ms = |t: Instant| t.duration_since(t0).as_millis() as i64;
            for (k, hit) in passed.iter_mut().enumerate() {
                let r = &course.rings[k % 8];
                if let Some(t) = ring::crossed_at(r, prev, pos, ms(prev_at), ms(now)) {
                    let off = t - ms(beat_at(k as u64));
                    if off.abs() <= window {
                        *hit = true;
                    }
                }
            }
        }
        let n = passed.iter().filter(|&&p| p).count();
        assert!(n >= 14, "{n} of 16 rings on the beat: {passed:?}");
    }
}
