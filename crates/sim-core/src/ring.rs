//! Ring beats (the Flying class): a student passes a ring by
//! flying through its disc, and the beat scores by when.
//!
//! A ring ([`Ring`], the class instance layout's `rings`) is a disc of
//! `radius` round `pos`, facing along `yaw` (yaw 0 faces +z). A checked
//! flight move from `a` to `b` passes it when the segment crosses the disc's
//! plane inside the disc, in either direction ([`crossing`]). The server
//! tests each accepted move of a student against the ring of every open ring
//! beat and dates the crossing between the move's two report times
//! ([`crossed_at`]); the client cannot claim a pass.
//!
//! The score is a pick's timing on a right answer (`crate::pick`): 100 within
//! `timing_full_ms` of the beat, falling to `100 - timing_weight_pct` at the
//! round's window; outside the window (either side) the crossing does not
//! count ([`judge`]), and a ring never passed in its window is a miss.

use protocol::GestureResult;

use crate::gesture::Gestures;
use crate::layout::Ring;

/// Where along the move from `a` to `b` (0..=1) it crosses `ring`'s disc,
/// or `None` if it does not (it stays on one side of the plane, runs in the
/// plane, or crosses it outside the disc).
pub fn crossing(ring: &Ring, a: [f32; 3], b: [f32; 3]) -> Option<f32> {
    let n = [ring.yaw.sin(), 0.0, ring.yaw.cos()];
    let side = |p: [f32; 3]| (0..3).map(|k| (p[k] - ring.pos[k]) * n[k]).sum::<f32>();
    // Within a hundredth of a millimetre of the plane is on it (yaw's sine
    // and cosine are not exact).
    let on = |d: f32| if d.abs() < 1e-5 { 0.0 } else { d };
    let (da, db) = (on(side(a)), on(side(b)));
    let same_side = (da > 0.0 && db > 0.0) || (da < 0.0 && db < 0.0);
    if !(da.is_finite() && db.is_finite()) || same_side || (da == 0.0 && db == 0.0) {
        return None;
    }
    let t = da / (da - db);
    let p: [f32; 3] = std::array::from_fn(|k| a[k] + (b[k] - a[k]) * t);
    let r2 = (0..3).map(|k| (p[k] - ring.pos[k]).powi(2)).sum::<f32>();
    (r2 <= ring.radius * ring.radius).then_some(t)
}

/// When (Unix ms) a move reported at `a_ms` (from `a`) and `b_ms` (to `b`)
/// crosses `ring`, or `None`.
pub fn crossed_at(ring: &Ring, a: [f32; 3], b: [f32; 3], a_ms: i64, b_ms: i64) -> Option<i64> {
    let t = crossing(ring, a, b)?;
    Some(a_ms + ((b_ms - a_ms) as f64 * f64::from(t)).round() as i64)
}

/// The score of passing a ring `offset_ms` from its beat in a round whose
/// window is `window_ms` (inside the window).
pub fn score(gestures: &Gestures, window_ms: u16, offset_ms: i32) -> GestureResult {
    gestures.combine(100, gestures.timing(window_ms, offset_ms))
}

/// The server's verdict on a crossing at `crossed_ms` of the ring of a beat
/// at `beat_at_ms`: its score, or `None` outside the window (the beat stays
/// open for a crossing that counts).
pub fn judge(
    gestures: &Gestures,
    beat_at_ms: i64,
    window_ms: u16,
    crossed_ms: i64,
) -> Option<GestureResult> {
    let offset = crossed_ms - beat_at_ms;
    (offset.unsigned_abs() <= u64::from(window_ms))
        .then(|| score(gestures, window_ms, offset as i32))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gesture::tests::shipped as gestures;
    use protocol::Grade;

    fn ring() -> Ring {
        // Facing +x at (10, 5, 0), radius 2.
        Ring {
            id: "r".into(),
            pos: [10.0, 5.0, 0.0],
            yaw: std::f32::consts::FRAC_PI_2,
            radius: 2.0,
        }
    }

    #[test]
    fn a_move_through_the_disc_crosses_it_either_way() {
        let r = ring();
        let t = crossing(&r, [9.0, 5.0, 0.0], [11.0, 5.0, 0.0]).unwrap();
        assert!((t - 0.5).abs() < 1e-5);
        assert!(
            crossing(&r, [11.0, 6.0, 1.0], [9.5, 6.0, 1.0]).is_some(),
            "the other way"
        );
        // Through the plane but outside the disc, alongside it, or short of it.
        assert!(crossing(&r, [9.0, 8.0, 0.0], [11.0, 8.0, 0.0]).is_none());
        assert!(crossing(&r, [9.0, 5.0, 3.0], [11.0, 5.0, -3.0]).is_some());
        assert!(crossing(&r, [9.0, 5.0, 2.5], [11.0, 5.0, 2.5]).is_none());
        assert!(
            crossing(&r, [10.0, 4.0, -1.0], [10.0, 6.0, 1.0]).is_none(),
            "in its plane"
        );
        assert!(
            crossing(&r, [8.0, 5.0, 0.0], [9.9, 5.0, 0.0]).is_none(),
            "short of it"
        );
        // A move ending or starting on the disc crosses it (a beat scores once).
        assert!(crossing(&r, [9.0, 5.0, 0.0], [10.0, 5.0, 0.0]).is_some());
        assert!(crossing(&r, [10.0, 5.0, 0.0], [11.0, 5.0, 0.0]).is_some());
        assert!(crossing(&r, [f32::NAN, 5.0, 0.0], [11.0, 5.0, 0.0]).is_none());
        assert_eq!(
            crossed_at(&r, [9.0, 5.0, 0.0], [10.5, 5.0, 0.0], 1_000, 1_100),
            Some(1_067)
        );
    }

    #[test]
    fn rings_score_by_timing_like_right_picks() {
        let g = gestures();
        let full = i64::from(g.scoring.timing_full_ms);
        let at = 1_790_000_000_000;
        let on = judge(&g, at, 1_500, at + 10).unwrap();
        assert_eq!(
            (on.score, on.grade, on.rejected),
            (100, Grade::Perfect, None)
        );
        assert_eq!(judge(&g, at, 1_500, at - full).unwrap().score, 100);
        let edge = judge(&g, at, 1_500, at + 1_500).unwrap();
        assert_eq!(edge.score, 100 - g.scoring.timing_weight_pct);
        assert_eq!(edge, crate::pick::preview(&g, 1_500, 1_500));
        assert!(judge(&g, at, 1_500, at + 1_501).is_none());
        assert!(judge(&g, at, 1_500, at - 1_501).is_none());
    }
}
