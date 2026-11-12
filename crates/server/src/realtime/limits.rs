//! Token buckets for the per-player rate limits the world enforces (emotes,
//! door and dorm actions). Chat keeps its own constants in [`super::chat`].

use std::time::Instant;

/// A token bucket: up to `burst` actions at once, refilled at `per_second`.
#[derive(Debug, Clone, Copy)]
pub struct Bucket {
    tokens: f32,
    at: Instant,
}

impl Bucket {
    /// A full bucket.
    pub fn full(burst: f32, now: Instant) -> Self {
        Self {
            tokens: burst,
            at: now,
        }
    }

    /// Takes one token if there is one. Time never runs backwards: an `now`
    /// earlier than the last call (inputs stamped out of order) earns nothing.
    pub fn take(&mut self, now: Instant, burst: f32, per_second: f32) -> bool {
        let earned = now.saturating_duration_since(self.at).as_secs_f32();
        self.at = self.at.max(now);
        self.tokens = (self.tokens + earned * per_second).min(burst);
        if self.tokens >= 1.0 {
            self.tokens -= 1.0;
            true
        } else {
            false
        }
    }
}

/// Emotes: a burst of 3, then one a second.
pub const EMOTE_BURST: f32 = 3.0;
pub const EMOTE_PER_SECOND: f32 = 1.0;

/// Door, invitation and furniture actions: a burst of 10, then 4 a second
/// (dragging furniture around sends a move per drop, not per frame).
pub const ACTION_BURST: f32 = 10.0;
pub const ACTION_PER_SECOND: f32 = 4.0;

/// Protocol v10 `LookAt`: clients send at most `protocol::LOOK_SENDS_PER_SEC`
/// (4) a second; the bucket allows a burst of 8 for network jitter, then 4
/// a second. Extra messages are dropped.
pub const LOOK_BURST: f32 = 8.0;
pub const LOOK_PER_SECOND: f32 = protocol::LOOK_SENDS_PER_SEC as f32;

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    #[test]
    fn a_burst_then_the_rate() {
        let t0 = Instant::now();
        let mut b = Bucket::full(3.0, t0);
        assert!((0..3).all(|_| b.take(t0, 3.0, 1.0)));
        assert!(!b.take(t0, 3.0, 1.0));
        assert!(!b.take(t0 + Duration::from_millis(500), 3.0, 1.0));
        assert!(b.take(t0 + Duration::from_millis(1000), 3.0, 1.0));
        // Out-of-order timestamps earn nothing.
        assert!(!b.take(t0, 3.0, 1.0));
        // Refills only up to the burst.
        let later = t0 + Duration::from_secs(60);
        assert!((0..3).all(|_| b.take(later, 3.0, 1.0)));
        assert!(!b.take(later, 3.0, 1.0));
    }
}
