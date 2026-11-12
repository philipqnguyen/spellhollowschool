//! The server's clock and the class schedule.
//!
//! Game time is a pure function of UTC (`sim_core::clock`). [`ServerClock`]
//! is the one source of "now" (Unix ms) for the world thread and the
//! gateway: `Welcome.unix_ms`, `Pong.server_time_ms`, class sessions and
//! lesson beats all read it. It follows the monotonic clock from a base
//! taken at start (a wall-clock jump never makes a beat run twice), plus a
//! development skew (`POST /api/dev/clock`) that lets playtests jump to the
//! next class without waiting.
//!
//! [`schedule`] gives the shipped schedule.

use std::sync::atomic::{AtomicI64, Ordering};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use sim_core::Schedule;

/// Real time for the game: Unix milliseconds (UTC), monotonic, with a
/// development skew. Shared (`Arc`) by the world and the gateway.
#[derive(Debug)]
pub struct ServerClock {
    base_instant: Instant,
    base_unix_ms: i64,
    skew_ms: AtomicI64,
}

impl Default for ServerClock {
    fn default() -> Self {
        Self::new()
    }
}

impl ServerClock {
    /// A clock reading the system time now.
    pub fn new() -> Self {
        let unix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_millis() as i64);
        Self::starting_at(unix, Instant::now())
    }

    /// A clock that reads `unix_ms` at `at` (tests).
    pub fn starting_at(unix_ms: i64, at: Instant) -> Self {
        Self {
            base_instant: at,
            base_unix_ms: unix_ms,
            skew_ms: AtomicI64::new(0),
        }
    }

    /// Unix ms at monotonic time `at`.
    pub fn unix_ms_at(&self, at: Instant) -> i64 {
        let since = if at >= self.base_instant {
            at.duration_since(self.base_instant).as_millis() as i64
        } else {
            -(self.base_instant.duration_since(at).as_millis() as i64)
        };
        self.base_unix_ms + since + self.skew_ms.load(Ordering::Relaxed)
    }

    /// Unix ms now.
    pub fn now_unix_ms(&self) -> i64 {
        self.unix_ms_at(Instant::now())
    }

    /// The development skew (ms added to the real time).
    pub fn skew_ms(&self) -> i64 {
        self.skew_ms.load(Ordering::Relaxed)
    }

    /// Development only (`POST /api/dev/clock`): shifts the game's clock.
    pub fn set_skew_ms(&self, ms: i64) {
        self.skew_ms.store(ms, Ordering::Relaxed);
    }
}

/// The shipped class schedule (`data/classes/schedule.json`).
pub fn schedule() -> &'static Schedule {
    &crate::catalog::shipped().schedule
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    #[test]
    fn the_clock_follows_instants_and_the_skew() {
        let t0 = Instant::now();
        let c = ServerClock::starting_at(1_000_000, t0);
        assert_eq!(c.unix_ms_at(t0), 1_000_000);
        assert_eq!(c.unix_ms_at(t0 + Duration::from_millis(250)), 1_000_250);
        c.set_skew_ms(60_000);
        assert_eq!(c.unix_ms_at(t0), 1_060_000);
        assert_eq!(c.skew_ms(), 60_000);
        assert!(ServerClock::new().now_unix_ms() > 1_700_000_000_000);
        assert!(schedule().class("charms").is_some());
    }
}
