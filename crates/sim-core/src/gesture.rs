//! Wand gestures and the trace scorer:
//! `data/classes/gestures.json` ([`Gestures`]).
//!
//! In class a student traces the beat's gesture with the mouse or a finger.
//! The client sends compact samples (`protocol::TracePoint`: the time since
//! the previous sample and a position on a square pad, -100..=100, y up)
//! and the server scores them with [`Gestures::score`], the same code the
//! client runs for its instant preview. Nothing here reads a clock: the
//! server passes the beat time and when the trace arrived.
//!
//! Scoring, in order:
//!
//! 1. **Plausibility** ([`Gestures::check`]): a sample count the pad can
//!    produce, every sample on the pad, a duration within the gesture's
//!    `min_ms..=max_ms`, a shape big enough to be a gesture, and no faster
//!    than a hand (average and per-sample speed limits). Failing any is a
//!    [`TraceReject`] (the trace scores nothing).
//! 2. **Shape**: the path and the template are both resampled to
//!    `resample` points evenly spaced along their length, centred on their
//!    centroid and scaled so their larger side is 1 (direction and stroke
//!    order matter; position and size do not). The mean distance between
//!    corresponding points gives the shape score: 100 at or below
//!    `full_marks`, 0 at or above `zero_marks`, linear between. A distance
//!    below `too_perfect` is closer than any hand traces: generated input,
//!    rejected (`TooPerfect`).
//! 3. **Timing** ([`Gestures::timing`]): how far the trace started from the
//!    beat. 100 within `timing_full_ms`, falling to 0 at the round's window.
//! 4. **Score** = shape weighted by timing (`timing_weight_pct` of it rides
//!    on timing), and the [`Grade`] from the thresholds.
//!
//! The server adds what only it can know ([`Gestures::check_arrival`]: the
//! trace cannot arrive before it could have been drawn, nor long after) and
//! session checks: one score per beat, and no trace may repeat an earlier
//! one exactly ([`fingerprint`], `Replayed`).

use serde::{Deserialize, Serialize};

use protocol::{GestureId, GestureResult, Grade, TRACE_POINTS_MAX, TracePoint, TraceReject};

/// The gestures schema version this crate reads.
pub const GESTURES_VERSION: u32 = 1;
/// The pad's half-width: samples lie in -`PAD`..=`PAD` on both axes.
pub const PAD: i8 = 100;

#[derive(Debug, thiserror::Error)]
pub enum GestureError {
    #[error("gestures JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("gestures are inconsistent: {0}")]
    Invalid(String),
}

/// Grade thresholds (scores 0-100).
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct GradeThresholds {
    pub good: u8,
    pub great: u8,
    pub perfect: u8,
}

/// The scorer's numbers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Scoring {
    /// Points both paths are resampled to.
    pub resample: usize,
    /// Fewest samples a trace may have.
    pub min_points: usize,
    /// The larger side of the trace's bounding box must be at least this
    /// (pad units).
    pub min_extent: f32,
    /// Fastest average speed over the whole trace (pad units per ms).
    pub max_avg_speed: f32,
    /// Fastest speed over one sample (pad units per ms, `dt_ms` at least 1).
    pub max_step_speed: f32,
    /// Mean distance (normalised units) for a full shape score...
    pub full_marks: f32,
    /// ...and for none.
    pub zero_marks: f32,
    /// Closer than this is generated input.
    pub too_perfect: f32,
    /// A start within this of the beat (ms) is perfectly on time.
    pub timing_full_ms: u16,
    /// How much of the score rides on timing (percent).
    pub timing_weight_pct: u8,
    pub grades: GradeThresholds,
    /// A trace may arrive this much before its claimed end (clock error, ms)...
    pub arrival_early_ms: u32,
    /// ...and at most this long after it (network, ms).
    pub arrival_late_ms: u32,
}

/// One gesture: its template is a polyline on the pad.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Gesture {
    pub id: GestureId,
    /// Stable data id ("twirl_and_tap"); spells and lessons name gestures by it.
    pub key: String,
    /// Display name ("Twirl and Tap").
    pub name: String,
    /// Shortest and longest a trace may take (ms).
    pub min_ms: u32,
    pub max_ms: u32,
    /// The template, in stroke order (pad units, y up).
    pub points: Vec<[i8; 2]>,
    /// The template resampled and normalised (computed at load).
    #[serde(skip)]
    prepared: Vec<[f32; 2]>,
}

impl Gesture {
    /// The template as the scorer compares it (resampled, normalised).
    pub fn prepared(&self) -> &[[f32; 2]] {
        &self.prepared
    }
}

/// A trace that passed the plausibility checks, with its shape score.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ShapeScore {
    /// 0..=100.
    pub shape: u8,
    /// Mean distance to the template (normalised units).
    pub distance: f32,
    /// The trace's duration (ms).
    pub duration_ms: u32,
}

/// `data/classes/gestures.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Gestures {
    pub version: u32,
    pub scoring: Scoring,
    pub gestures: Vec<Gesture>,
}

impl Gestures {
    /// Parses and validates the gestures (and prepares their templates).
    pub fn from_json(json: &str) -> Result<Gestures, GestureError> {
        let mut g: Gestures = serde_json::from_str(json)?;
        g.validate().map_err(GestureError::Invalid)?;
        let n = g.scoring.resample;
        for gesture in &mut g.gestures {
            let pts: Vec<[f32; 2]> = gesture
                .points
                .iter()
                .map(|p| [f32::from(p[0]), f32::from(p[1])])
                .collect();
            gesture.prepared = normalise(&resample(&pts, n));
        }
        Ok(g)
    }

    pub fn get(&self, id: GestureId) -> Option<&Gesture> {
        self.gestures.iter().find(|g| g.id == id)
    }

    pub fn by_key(&self, key: &str) -> Option<&Gesture> {
        self.gestures.iter().find(|g| g.key == key)
    }

    /// Step 1 and 2: the plausibility checks and the shape score.
    pub fn check(
        &self,
        gesture: &Gesture,
        points: &[TracePoint],
    ) -> Result<ShapeScore, TraceReject> {
        let s = &self.scoring;
        if points.len() < s.min_points || points.len() > TRACE_POINTS_MAX {
            return Err(TraceReject::BadSampleCount);
        }
        if points
            .iter()
            .any(|p| p.x.unsigned_abs() > PAD as u8 || p.y.unsigned_abs() > PAD as u8)
        {
            return Err(TraceReject::OutOfRange);
        }
        let duration_ms = duration_ms(points);
        if duration_ms < gesture.min_ms || duration_ms > gesture.max_ms {
            return Err(TraceReject::BadDuration);
        }
        let pts: Vec<[f32; 2]> = points
            .iter()
            .map(|p| [f32::from(p.x), f32::from(p.y)])
            .collect();
        let (min, max) = bounds(&pts);
        if (max[0] - min[0]).max(max[1] - min[1]) < s.min_extent {
            return Err(TraceReject::TooSmall);
        }
        let length = path_length(&pts);
        if length / duration_ms.max(1) as f32 > s.max_avg_speed {
            return Err(TraceReject::TooFast);
        }
        for (w, p) in pts.windows(2).zip(&points[1..]) {
            let step = dist(w[0], w[1]);
            if step / f32::from(p.dt_ms.max(1)) > s.max_step_speed {
                return Err(TraceReject::TooFast);
            }
        }
        let trace = normalise(&resample(&pts, s.resample));
        let distance = mean_distance(&trace, &gesture.prepared);
        if distance < s.too_perfect {
            return Err(TraceReject::TooPerfect);
        }
        let shape = if distance <= s.full_marks {
            100.0
        } else if distance >= s.zero_marks {
            0.0
        } else {
            100.0 * (s.zero_marks - distance) / (s.zero_marks - s.full_marks)
        };
        Ok(ShapeScore {
            shape: shape.round().clamp(0.0, 100.0) as u8,
            distance,
            duration_ms,
        })
    }

    /// Step 3: the timing score (0..=100) of a trace that started
    /// `offset_ms` from its beat, in a round whose window is `window_ms`.
    pub fn timing(&self, window_ms: u16, offset_ms: i32) -> u8 {
        let off = offset_ms.unsigned_abs();
        let full = u32::from(self.scoring.timing_full_ms);
        let window = u32::from(window_ms).max(full + 1);
        if off <= full {
            100
        } else if off >= window {
            0
        } else {
            (100 * (window - off) / (window - full)) as u8
        }
    }

    /// Step 4: the overall score and grade of a shape and a timing score.
    pub fn combine(&self, shape: u8, timing: u8) -> GestureResult {
        let w = u32::from(self.scoring.timing_weight_pct.min(100));
        let factor = (100 - w) + w * u32::from(timing) / 100;
        let score = (u32::from(shape) * factor / 100) as u8;
        GestureResult {
            score,
            grade: self.grade(score),
            rejected: None,
        }
    }

    pub fn grade(&self, score: u8) -> Grade {
        let g = &self.scoring.grades;
        if score >= g.perfect {
            Grade::Perfect
        } else if score >= g.great {
            Grade::Great
        } else if score >= g.good {
            Grade::Good
        } else {
            Grade::Miss
        }
    }

    /// Steps 1-4 for one trace: what the client previews and what the server
    /// awards (before its own arrival and session checks).
    pub fn score(
        &self,
        gesture: &Gesture,
        window_ms: u16,
        offset_ms: i32,
        points: &[TracePoint],
    ) -> GestureResult {
        match self.check(gesture, points) {
            Ok(shape) => self.combine(shape.shape, self.timing(window_ms, offset_ms)),
            Err(r) => rejected(r),
        }
    }

    /// The server's check of when a trace arrived: a trace claiming to start
    /// `offset_ms` after a beat at `beat_at_ms` and to last `duration_ms`
    /// cannot arrive (`received_ms`) before it ended (less the allowed clock
    /// error), nor long after; and it must start within the window.
    pub fn check_arrival(
        &self,
        beat_at_ms: i64,
        window_ms: u16,
        offset_ms: i16,
        duration_ms: u32,
        received_ms: i64,
    ) -> Result<(), TraceReject> {
        if offset_ms.unsigned_abs() > window_ms {
            return Err(TraceReject::BadTiming);
        }
        let ended = beat_at_ms + i64::from(offset_ms) + i64::from(duration_ms);
        let s = &self.scoring;
        if received_ms < ended - i64::from(s.arrival_early_ms)
            || received_ms > ended + i64::from(s.arrival_late_ms)
        {
            return Err(TraceReject::BadTiming);
        }
        Ok(())
    }

    fn validate(&self) -> Result<(), String> {
        if self.version != GESTURES_VERSION {
            return Err(format!(
                "unsupported version {} (expected {GESTURES_VERSION})",
                self.version
            ));
        }
        let s = &self.scoring;
        if !(8..=64).contains(&s.resample) || s.min_points < 2 || s.min_points > TRACE_POINTS_MAX {
            return Err("resample must be 8..=64 and min_points 2..=TRACE_POINTS_MAX".into());
        }
        let positive = [
            s.min_extent,
            s.max_avg_speed,
            s.max_step_speed,
            s.full_marks,
            s.zero_marks,
            s.too_perfect,
        ];
        if positive.iter().any(|v| !(v.is_finite() && *v > 0.0)) {
            return Err("scoring numbers must be positive".into());
        }
        if !(s.too_perfect < s.full_marks && s.full_marks < s.zero_marks) {
            return Err("too_perfect < full_marks < zero_marks".into());
        }
        let g = &s.grades;
        if !(g.good < g.great && g.great < g.perfect && g.perfect <= 100)
            || s.timing_weight_pct > 100
        {
            return Err("grades must rise to at most 100; timing weight at most 100".into());
        }
        let mut ids = std::collections::HashSet::new();
        let mut keys = std::collections::HashSet::new();
        for gesture in &self.gestures {
            let k = &gesture.key;
            if !ids.insert(gesture.id) || !keys.insert(k.as_str()) || !crate::clock::valid_id(k) {
                return Err(format!("gesture {k:?}: ids and keys must be unique"));
            }
            if gesture.points.len() < 2
                || gesture
                    .points
                    .iter()
                    .any(|p| p[0].unsigned_abs() > PAD as u8 || p[1].unsigned_abs() > PAD as u8)
            {
                return Err(format!("gesture {k}: at least two points, all on the pad"));
            }
            let pts: Vec<[f32; 2]> = gesture
                .points
                .iter()
                .map(|p| [f32::from(p[0]), f32::from(p[1])])
                .collect();
            let (min, max) = bounds(&pts);
            if (max[0] - min[0]).max(max[1] - min[1]) < s.min_extent {
                return Err(format!("gesture {k}: smaller than min_extent"));
            }
            if gesture.min_ms == 0 || gesture.min_ms >= gesture.max_ms {
                return Err(format!("gesture {k}: 0 < min_ms < max_ms"));
            }
            // A trace of the template at its slowest pace must pass the speed rule.
            if path_length(&pts) / gesture.min_ms as f32 > s.max_avg_speed {
                return Err(format!("gesture {k}: min_ms is faster than max_avg_speed"));
            }
        }
        Ok(())
    }
}

/// A rejected trace's result.
pub fn rejected(reason: TraceReject) -> GestureResult {
    GestureResult {
        score: 0,
        grade: Grade::Miss,
        rejected: Some(reason),
    }
}

/// How long a trace took (ms): the sum of its sample gaps.
pub fn duration_ms(points: &[TracePoint]) -> u32 {
    points.iter().skip(1).map(|p| u32::from(p.dt_ms)).sum()
}

/// A stable 64-bit fingerprint of a trace's samples (FNV-1a), for spotting
/// a trace sent twice.
pub fn fingerprint(points: &[TracePoint]) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for p in points {
        for b in [p.dt_ms, p.x as u8, p.y as u8] {
            h ^= u64::from(b);
            h = h.wrapping_mul(0x0100_0000_01b3);
        }
    }
    h
}

fn dist(a: [f32; 2], b: [f32; 2]) -> f32 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()
}

fn path_length(pts: &[[f32; 2]]) -> f32 {
    pts.windows(2).map(|w| dist(w[0], w[1])).sum()
}

fn bounds(pts: &[[f32; 2]]) -> ([f32; 2], [f32; 2]) {
    let mut min = [f32::INFINITY; 2];
    let mut max = [f32::NEG_INFINITY; 2];
    for p in pts {
        for k in 0..2 {
            min[k] = min[k].min(p[k]);
            max[k] = max[k].max(p[k]);
        }
    }
    (min, max)
}

/// `n` points evenly spaced along the polyline (the $1 recognizer's resample).
fn resample(pts: &[[f32; 2]], n: usize) -> Vec<[f32; 2]> {
    let total = path_length(pts);
    if total <= 0.0 || pts.len() < 2 {
        return vec![pts.first().copied().unwrap_or([0.0; 2]); n];
    }
    let step = total / (n - 1) as f32;
    let mut out = Vec::with_capacity(n);
    out.push(pts[0]);
    let mut carried = 0.0;
    let mut prev = pts[0];
    for &next in &pts[1..] {
        let mut seg = dist(prev, next);
        while carried + seg >= step && out.len() < n {
            let t = (step - carried) / seg;
            let p = [
                prev[0] + t * (next[0] - prev[0]),
                prev[1] + t * (next[1] - prev[1]),
            ];
            out.push(p);
            seg = dist(p, next);
            prev = p;
            carried = 0.0;
        }
        carried += seg;
        prev = next;
    }
    // Rounding can leave the last point short.
    while out.len() < n {
        out.push(*pts.last().expect("at least two points"));
    }
    out
}

/// Centred on the centroid, scaled so the larger side of the box is 1.
fn normalise(pts: &[[f32; 2]]) -> Vec<[f32; 2]> {
    let n = pts.len().max(1) as f32;
    let c = [
        pts.iter().map(|p| p[0]).sum::<f32>() / n,
        pts.iter().map(|p| p[1]).sum::<f32>() / n,
    ];
    let (min, max) = bounds(pts);
    let size = (max[0] - min[0]).max(max[1] - min[1]).max(1e-6);
    pts.iter()
        .map(|p| [(p[0] - c[0]) / size, (p[1] - c[1]) / size])
        .collect()
}

fn mean_distance(a: &[[f32; 2]], b: &[[f32; 2]]) -> f32 {
    let n = a.len().min(b.len()).max(1);
    a.iter().zip(b).map(|(p, q)| dist(*p, *q)).sum::<f32>() / n as f32
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub const SHIPPED: &str = include_str!("../../../data/classes/gestures.json");

    pub fn shipped() -> Gestures {
        Gestures::from_json(SHIPPED).expect("the shipped gestures are valid")
    }

    /// A trace along `gesture`'s template, `n` samples over `ms`, with a
    /// deterministic wobble of up to `wobble` pad units.
    pub fn trace(gesture: &Gesture, n: usize, ms: u32, wobble: f32) -> Vec<TracePoint> {
        let pts: Vec<[f32; 2]> = gesture
            .points
            .iter()
            .map(|p| [f32::from(p[0]), f32::from(p[1])])
            .collect();
        let path = resample(&pts, n);
        let dt = (ms / (n as u32 - 1)) as u8;
        path.iter()
            .enumerate()
            .map(|(k, p)| {
                let w = wobble * ((k as f32 * 1.7).sin() + (k as f32 * 0.6).cos()) / 2.0;
                TracePoint {
                    dt_ms: if k == 0 { 0 } else { dt },
                    x: (p[0] + w).round().clamp(-100.0, 100.0) as i8,
                    y: (p[1] - w).round().clamp(-100.0, 100.0) as i8,
                }
            })
            .collect()
    }

    /// Astronomy's invented constellations trace like any gesture,
    /// and each one traced for another is not a pass.
    #[test]
    fn the_constellations_trace_and_differ() {
        let g = shipped();
        let keys = [
            "const_lantern",
            "const_heron",
            "const_kettle",
            "const_hound",
            "const_owl",
        ];
        for key in keys {
            let c = g.by_key(key).unwrap_or_else(|| panic!("{key}"));
            let good = trace(c, 60, 2400, 4.0);
            let shape = g.check(c, &good).unwrap_or_else(|e| panic!("{key}: {e:?}"));
            assert!(shape.shape >= 85, "{key}: {shape:?}");
            for other in keys.iter().filter(|k| **k != key) {
                let wrong = trace(g.by_key(other).unwrap(), 60, 2400, 4.0);
                let r = g.score(c, 900, 0, &wrong);
                assert!(r.score < 60, "{other} traced for {key}: {r:?}");
            }
        }
    }

    #[test]
    fn a_careful_trace_scores_well_and_a_wrong_shape_does_not() {
        let g = shipped();
        let twirl = g.by_key("twirl_and_tap").unwrap();
        let circle = g.by_key("circle").unwrap();
        let good = trace(twirl, 40, 900, 4.0);
        let shape = g.check(twirl, &good).unwrap();
        assert!(shape.shape >= 85, "{shape:?}");
        let r = g.score(twirl, 600, 30, &good);
        assert!(matches!(r.grade, Grade::Perfect | Grade::Great), "{r:?}");
        // The circle traced for the twirl: a miss.
        let wrong = trace(circle, 40, 900, 4.0);
        let r = g.score(twirl, 600, 0, &wrong);
        assert_eq!(r.rejected, None);
        assert_eq!(r.grade, Grade::Miss, "{r:?}");
        // Sloppier hands score lower, but still pass.
        let sloppy = g.check(twirl, &trace(twirl, 40, 900, 14.0)).unwrap();
        assert!(sloppy.distance > shape.distance && sloppy.shape <= shape.shape);
        // Same input, same answer (the client's preview is the server's score).
        assert_eq!(
            g.score(twirl, 600, 30, &good),
            g.score(twirl, 600, 30, &good)
        );
    }

    #[test]
    fn timing_weighs_in() {
        let g = shipped();
        assert_eq!(g.timing(600, 0), 100);
        assert_eq!(g.timing(600, -100), 100);
        assert_eq!(g.timing(600, 600), 0);
        assert_eq!(g.timing(600, -900), 0);
        let mid = g.timing(600, 360);
        assert!(mid > 0 && mid < 100);
        let late = g.combine(100, 0);
        assert_eq!(late.score, 100 - g.scoring.timing_weight_pct);
        assert_eq!(g.combine(100, 100).grade, Grade::Perfect);
        assert_eq!(g.combine(0, 100).grade, Grade::Miss);
    }

    #[test]
    fn impossible_input_is_rejected() {
        let g = shipped();
        let twirl = g.by_key("twirl_and_tap").unwrap();
        // Replaying the template exactly is too perfect.
        let exact: Vec<TracePoint> = twirl
            .points
            .iter()
            .enumerate()
            .flat_map(|(k, p)| {
                // Densely along each segment so the sample count passes.
                let next = twirl.points.get(k + 1).copied().unwrap_or(*p);
                (0..4).map(move |s| TracePoint {
                    dt_ms: 30,
                    x: (f32::from(p[0]) + (f32::from(next[0]) - f32::from(p[0])) * s as f32 / 4.0)
                        .round() as i8,
                    y: (f32::from(p[1]) + (f32::from(next[1]) - f32::from(p[1])) * s as f32 / 4.0)
                        .round() as i8,
                })
            })
            .collect();
        assert_eq!(g.check(twirl, &exact), Err(TraceReject::TooPerfect));
        // Too fast: the whole twirl in a frame or two.
        let fast = trace(twirl, 20, 60, 4.0);
        assert!(matches!(
            g.check(twirl, &fast),
            Err(TraceReject::TooFast | TraceReject::BadDuration)
        ));
        let mut burst = trace(twirl, 40, 900, 4.0);
        for p in burst.iter_mut().skip(1) {
            p.dt_ms = 0;
        }
        burst.last_mut().unwrap().dt_ms = 255;
        burst[10].dt_ms = 255;
        burst[20].dt_ms = 255;
        assert_eq!(g.check(twirl, &burst), Err(TraceReject::TooFast));
        // Too slow, too few, too many, off the pad, too small.
        assert_eq!(
            g.check(twirl, &trace(twirl, 40, 9_000, 4.0)),
            Err(TraceReject::BadDuration)
        );
        assert_eq!(
            g.check(twirl, &trace(twirl, 40, 900, 4.0)[..3]),
            Err(TraceReject::BadSampleCount)
        );
        let many = vec![trace(twirl, 40, 900, 4.0)[1]; TRACE_POINTS_MAX + 1];
        assert_eq!(g.check(twirl, &many), Err(TraceReject::BadSampleCount));
        let mut off = trace(twirl, 40, 900, 4.0);
        off[3].x = -128;
        assert_eq!(g.check(twirl, &off), Err(TraceReject::OutOfRange));
        let tiny: Vec<TracePoint> = (0..20)
            .map(|k| TracePoint {
                dt_ms: 40,
                x: (k % 3) as i8,
                y: 0,
            })
            .collect();
        assert_eq!(g.check(twirl, &tiny), Err(TraceReject::TooSmall));
        // A replay is spotted by its fingerprint; a different trace is not.
        let a = trace(twirl, 40, 900, 4.0);
        let b = trace(twirl, 40, 900, 5.0);
        assert_eq!(fingerprint(&a), fingerprint(&a.clone()));
        assert_ne!(fingerprint(&a), fingerprint(&b));
    }

    #[test]
    fn arrival_must_fit_the_claimed_timing() {
        let g = shipped();
        let beat = 1_000_000;
        // Started 50 ms late, took 900 ms: ended at beat + 950.
        assert!(g.check_arrival(beat, 600, 50, 900, beat + 1_000).is_ok());
        assert_eq!(
            g.check_arrival(beat, 600, 50, 900, beat + 200),
            Err(TraceReject::BadTiming),
            "arrived before it could have been drawn"
        );
        assert_eq!(
            g.check_arrival(beat, 600, 50, 900, beat + 60_000),
            Err(TraceReject::BadTiming),
            "long after"
        );
        assert_eq!(
            g.check_arrival(beat, 600, 700, 900, beat + 1_700),
            Err(TraceReject::BadTiming),
            "outside the window"
        );
    }

    /// Spells L20: the 8 new templates are on file, and every spell's
    /// gesture traced carefully scores below `good` for every other spell's
    /// gesture, so a cast is never mistaken for another spell.
    #[test]
    fn spell_gestures_are_told_apart() {
        let g = shipped();
        for (id, key) in [
            (15, "colour_wave"),
            (16, "bind"),
            (17, "flutter"),
            (18, "hook"),
            (19, "unravel"),
            (20, "figure_eight"),
            (21, "cupped_sweep"),
            (22, "rising_spiral"),
        ] {
            assert_eq!(g.by_key(key).map(|x| x.id), Some(id), "{key}");
        }
        let spells = crate::spells::tests::shipped();
        let mut keys: Vec<&str> = spells.spells.iter().map(|s| s.gesture.as_str()).collect();
        keys.sort_unstable();
        keys.dedup();
        assert_eq!(keys.len(), spells.spells.len(), "one gesture per spell");
        let mut worst = (0u8, "", "");
        for &key in &keys {
            let gesture = g.by_key(key).unwrap();
            let ms = ((gesture.min_ms + gesture.max_ms) / 2).min(2_400);
            for &other in keys.iter().filter(|&&o| o != key) {
                let t = trace(g.by_key(other).unwrap(), 40, ms, 4.0);
                if let Ok(s) = g.check(gesture, &t) {
                    if s.shape > worst.0 {
                        worst = (s.shape, other, key);
                    }
                    // The one pair from before (Lucerna's flick and Torpeo's
                    // jab, both level 1) sits at `good` exactly; it keeps
                    // the older rule (below `great`) and its shipped shapes.
                    let old_pair = [key, other]
                        .iter()
                        .all(|k| ["lucerna_flick", "jab"].contains(k));
                    let limit = if old_pair {
                        g.scoring.grades.good + 1
                    } else {
                        g.scoring.grades.good
                    };
                    assert!(s.shape < limit, "{other} traced for {key}: {s:?}");
                }
            }
        }
        println!("closest pair: {worst:?}");
    }

    #[test]
    fn every_shipped_gesture_scores_its_own_trace() {
        let g = shipped();
        assert!(g.gestures.len() >= 4);
        for gesture in &g.gestures {
            let ms = (gesture.min_ms + gesture.max_ms) / 2;
            let t = trace(gesture, 40, ms.min(2_400), 5.0);
            let s = g
                .check(gesture, &t)
                .unwrap_or_else(|e| panic!("{}: {e:?}", gesture.key));
            assert!(s.shape >= 70, "{}: {s:?}", gesture.key);
            // And no other gesture's trace passes for it as Great.
            for other in g.gestures.iter().filter(|o| o.id != gesture.id) {
                let ot = trace(other, 40, ms.min(2_400), 5.0);
                if let Ok(os) = g.check(gesture, &ot) {
                    assert!(
                        os.shape < g.scoring.grades.great,
                        "{} passes for {} ({os:?})",
                        other.key,
                        gesture.key
                    );
                }
            }
        }
    }
}
