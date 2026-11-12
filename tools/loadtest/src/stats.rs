//! What the bots measure: a [`BotReport`] per bot (merged at the end) plus
//! [`Live`] counters every bot bumps as it goes, which the monitor samples into
//! a timeline.

use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};

use serde::Serialize;

use crate::walker::WalkStats;

/// Running totals shared by all bots, sampled by the monitor.
#[derive(Debug, Default)]
pub struct Live {
    /// Bots currently in the world (between `Welcome` and disconnecting).
    pub in_world: AtomicI64,
    pub welcomed: AtomicU64,
    /// WebSocket payload bytes received.
    pub rx_bytes: AtomicU64,
    /// Payload plus WebSocket frame headers.
    pub rx_wire_bytes: AtomicU64,
    pub rx_messages: AtomicU64,
    pub tx_bytes: AtomicU64,
    pub corrections: AtomicU64,
    pub disconnects: AtomicU64,
}

impl Live {
    pub fn add(counter: &AtomicU64, n: u64) {
        counter.fetch_add(n, Ordering::Relaxed);
    }

    pub fn get(counter: &AtomicU64) -> u64 {
        counter.load(Ordering::Relaxed)
    }
}

/// One bot's measurements over the whole run.
#[derive(Debug, Default)]
pub struct BotReport {
    /// Received `Welcome` at least once.
    pub welcomed: bool,
    /// From starting the bot (session request included) to its first `Welcome`.
    pub join_ms: Option<f32>,
    /// Ping round trips.
    pub rtt_ms: Vec<f32>,
    pub corrections: u64,
    /// Times this bot waited in the login queue.
    pub queued: u64,
    pub rx_bytes: u64,
    pub rx_messages: u64,
    pub snapshot_bytes: Vec<u32>,
    /// Sum of the visible-set size after each snapshot (for the mean).
    pub visible_sum: u64,
    /// Entity states received as full states and as deltas.
    pub states_full: u64,
    pub states_delta: u64,
    /// Entering characters sent whole (name and appearance) and as cached
    /// entries (`protocol::looks`).
    pub entries_full: u64,
    pub entries_cached: u64,
    /// Snapshots that did not apply (the bot then reconnects).
    pub desyncs: u64,
    /// Chat lines the server refused.
    pub chat_rejected: u64,
    pub emotes_sent: u64,
    /// Protocol v10: `LookAt` messages sent (a target set or cleared), and
    /// look changes received in snapshots.
    pub looks_sent: u64,
    pub look_changes: u64,
    /// NPCs (protocol v12): resident NPCs in the visible set, summed over
    /// snapshots (with `visible_sum`, their share of the view); `NpcInteract`
    /// messages sent (`--npc-talk-every-secs`) and the `NpcReply` answers by
    /// kind.
    pub npc_visible_sum: u64,
    pub npc_talks_sent: u64,
    pub npc_talk_replies: u64,
    pub npc_gifts: u64,
    pub npc_too_far: u64,
    pub npc_refused: u64,
    /// Bytes of the snapshot entries about resident NPCs (their `left` ids,
    /// entries, full states, deltas and look changes, each postcard-encoded
    /// on its own) plus the `NpcReply` messages: the NPCs' share of `rx_bytes`.
    pub npc_rx_bytes: u64,
    /// Dressed (or found dressed) through the wardrobe API.
    pub dressed: bool,
    pub dress_errors: Vec<String>,
    /// Given a potion to drink (`--effects-pct`), and the server's answers.
    pub potion: bool,
    pub drank: u64,
    pub potion_errors: u64,
    /// `EffectsChanged` messages received (others' effects starting or ending).
    pub effects_changed: u64,
    pub chats_received: u64,
    pub moves_sent: u64,
    pub chats_sent: u64,
    pub pings_sent: u64,
    /// Connections that ended before the run did, with the reason.
    pub disconnects: Vec<String>,
    /// Failures to get a session or to connect at all.
    pub connect_errors: Vec<String>,
    pub walk: WalkStats,
    /// The bot's school role, and what it did.
    pub role: Option<&'static str>,
    pub school: crate::school::SchoolStats,
    /// Dev teleports to the class door, the club or the Exchange court that failed.
    pub teleport_errors: u64,
    /// Castle kit M2: what the bot did in the Owl Exchange court (`exchange` scenario).
    pub exchange: crate::exchange::ExchangeStats,
    /// Payload plus frame headers received, and when (seconds into the run) the first `Welcome` came.
    pub rx_wire_bytes: u64,
    pub welcomed_at_secs: Option<f64>,
    /// The bot's `GET /api/house-cup` requests (`--cup-poll-secs`).
    pub cup: crate::cup::CupPolls,
    /// What a flyer or pitch spectator did (`crate::flyer`).
    pub fly: crate::flyer::FlyStats,
    /// What the bot did about the moving staircases.
    pub stairs: crate::stairs::StairStats,
    /// The map at sign-in, secrets found, the Wishing Room.
    pub secrets: crate::secrets::SecretStats,
    /// The players list and cards (`--players-pct`).
    pub players: crate::players::PlayerStats,
}

/// Summary of a set of samples.
#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize)]
pub struct Dist {
    pub count: usize,
    pub mean: f64,
    pub p50: f64,
    pub p90: f64,
    pub p99: f64,
    pub max: f64,
}

impl Dist {
    /// Nearest-rank percentiles; sorts `values` in place.
    pub fn of(values: &mut [f64]) -> Dist {
        if values.is_empty() {
            return Dist::default();
        }
        values.sort_by(f64::total_cmp);
        let n = values.len();
        let rank = |q: f64| values[((q * n as f64).ceil() as usize).clamp(1, n) - 1];
        Dist {
            count: n,
            mean: values.iter().sum::<f64>() / n as f64,
            p50: rank(0.50),
            p90: rank(0.90),
            p99: rank(0.99),
            max: values[n - 1],
        }
    }
}

/// Size of the WebSocket frame header for an unmasked server frame of `len` bytes.
pub fn ws_header_len(len: usize) -> usize {
    match len {
        0..=125 => 2,
        126..=65535 => 4,
        _ => 10,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentiles_use_nearest_rank() {
        let mut v: Vec<f64> = (1..=100).map(f64::from).collect();
        let d = Dist::of(&mut v);
        assert_eq!(
            (d.count, d.p50, d.p90, d.p99, d.max),
            (100, 50.0, 90.0, 99.0, 100.0)
        );
        assert_eq!(d.mean, 50.5);
        assert_eq!(Dist::of(&mut [7.0]).p99, 7.0);
        assert_eq!(Dist::of(&mut []), Dist::default());
    }

    #[test]
    fn frame_headers() {
        assert_eq!(ws_header_len(10), 2);
        assert_eq!(ws_header_len(126), 4);
        assert_eq!(ws_header_len(70_000), 10);
    }
}
