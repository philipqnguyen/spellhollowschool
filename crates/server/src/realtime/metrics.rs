//! Metrics for the gateway and the world, registered once in the shared
//! [`Registry`](crate::metrics::Registry) and updated lock-free from the tick
//! thread, the snapshot workers and the connection tasks.

use std::sync::Arc;

use sim_core::RejectReason;

use crate::metrics::{Counter, Gauge, Histogram, Registry};

/// Tick durations in seconds. Dense around the 15 ms p99 budget so
/// `histogram_quantile(0.5 | 0.99, ...)` interpolates within a millisecond.
pub const TICK_BUCKETS: &[f64] = &[
    0.0005, 0.001, 0.002, 0.003, 0.004, 0.005, 0.0075, 0.01, 0.0125, 0.015, 0.02, 0.03, 0.05, 0.1,
];

/// Encoded snapshot sizes in bytes (60 B is a lone state, 6 KB is a full crowd).
pub const SNAPSHOT_BYTES_BUCKETS: &[f64] = &[
    16.0, 64.0, 128.0, 256.0, 512.0, 768.0, 1024.0, 2048.0, 4096.0, 8192.0, 16384.0,
];

#[derive(Debug)]
pub struct RealtimeMetrics {
    pub tick_seconds: Arc<Histogram>,
    pub tick_overruns: Arc<Counter>,
    pub players_online: Arc<Gauge>,
    pub queue_length: Arc<Gauge>,
    pub connections: Arc<Gauge>,
    pub ws_bytes_sent: Arc<Counter>,
    pub ws_bytes_received: Arc<Counter>,
    pub ws_messages_sent: Arc<Counter>,
    pub ws_messages_received: Arc<Counter>,
    pub snapshot_bytes: Arc<Histogram>,
    /// Rejected moves by reason: too_fast, not_walkable, blocked_path,
    /// off_ground; flight moves: fly_too_fast, fly_out_of_airspace,
    /// fly_no_fly, fly_below_ground (`flying::flight_correction_index`).
    pub corrections: [Arc<Counter>; 8],
    /// Brooms mounted, and mount or landing requests refused.
    pub mounts: Arc<Counter>,
    pub mount_refusals: Arc<Counter>,
    pub kicks_slow: Arc<Counter>,
    pub inbound_dropped: Arc<Counter>,
    pub chat_messages: Arc<Counter>,
    pub chat_dropped: Arc<Counter>,
    /// Friend and invitation notices dropped because a client's backlog was full.
    pub notices_dropped: Arc<Counter>,
    pub chat_rejected: Arc<Counter>,
    /// Entity states sent in snapshots, absolute and as deltas.
    pub snapshot_full_states: Arc<Counter>,
    pub snapshot_deltas: Arc<Counter>,
    /// Protocol v10: look changes sent in snapshots, and `LookAt` messages
    /// taken (dropped ones: over the rate).
    pub snapshot_looks: Arc<Counter>,
    pub look_targets: Arc<Counter>,
    pub look_dropped: Arc<Counter>,
    pub emotes: Arc<Counter>,
    pub zone_changes: Arc<Counter>,
    pub dorm_instances: Arc<Gauge>,
    /// Live class sections and duel arenas.
    pub class_instances: Arc<Gauge>,
    pub arena_instances: Arc<Gauge>,
    pub dorm_edits: Arc<Counter>,
    pub dorm_errors: Arc<Counter>,
    pub positions_saved: Arc<Counter>,
    pub position_save_errors: Arc<Counter>,
    /// Moving staircase flights that switched position, riders
    /// they carried, moves accepted only through the grace window round a
    /// switch, and the world ticks within a second of a switch.
    pub stairs_switches: Arc<Counter>,
    pub stairs_riders_carried: Arc<Counter>,
    pub stairs_grace_accepts: Arc<Counter>,
    pub stairs_switch_tick_seconds: Arc<Histogram>,
    /// Grace accepts refused as a reverse ride (a step from fixed
    /// ground onto a flight's incoming position before its switch).
    pub stairs_reverse_refused: Arc<Counter>,
    /// Fix: steps onto a flight's incoming position held (not
    /// stored, not corrected) because they came within an honest clock error
    /// of its switch, and walking corrections within the grace of a switch.
    pub stairs_early_holds: Arc<Counter>,
    pub stairs_corrections: Arc<Counter>,
    /// Map reveals written, the saver's batch time (positions and
    /// reveals), secrets found, Wishing Room doors opened and
    /// entries, live rooms.
    pub reveals_saved: Arc<Counter>,
    pub save_seconds: Arc<Histogram>,
    pub discoveries: Arc<Counter>,
    pub wishing_room_doors: Arc<Counter>,
    pub wishing_room_entries: Arc<Counter>,
    pub wishing_room_instances: Arc<Gauge>,
    /// Walkers whose new castle place went to their friends.
    pub friend_place_notices: Arc<Counter>,
}

/// The saver's batch times in seconds.
pub const SAVE_BUCKETS: &[f64] = &[
    0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5,
];

/// Index into [`RealtimeMetrics::corrections`] for a player standing off the ground.
pub const OFF_GROUND: usize = 3;

pub fn correction_index(reason: RejectReason) -> usize {
    match reason {
        RejectReason::TooFast => 0,
        RejectReason::NotWalkable => 1,
        RejectReason::BlockedPath => 2,
    }
}

impl RealtimeMetrics {
    pub fn register(reg: &Registry) -> Self {
        let corrections = [
            "too_fast",
            "not_walkable",
            "blocked_path",
            "off_ground",
            "fly_too_fast",
            "fly_out_of_airspace",
            "fly_no_fly",
            "fly_below_ground",
        ]
        .map(|r| {
            reg.counter_with(
                "shs_world_corrections_total",
                "Moves rejected and corrected, by reason.",
                &[("reason", r)],
            )
        });
        Self {
            tick_seconds: reg.histogram(
                "shs_world_tick_seconds",
                "Castle world tick duration (commands, simulation, snapshots).",
                TICK_BUCKETS,
            ),
            tick_overruns: reg.counter(
                "shs_world_tick_overruns_total",
                "Ticks that ran past their 50 ms slot.",
            ),
            players_online: reg.gauge(
                "shs_world_players",
                "Characters in the world (castle and dorms).",
            ),
            queue_length: reg.gauge(
                "shs_login_queue_length",
                "Players waiting in the login queue.",
            ),
            connections: reg.gauge("shs_ws_connections", "Open WebSocket connections."),
            ws_bytes_sent: reg.counter("shs_ws_sent_bytes_total", "WebSocket payload bytes sent."),
            ws_bytes_received: reg.counter(
                "shs_ws_received_bytes_total",
                "WebSocket payload bytes received.",
            ),
            ws_messages_sent: reg.counter("shs_ws_sent_messages_total", "WebSocket messages sent."),
            ws_messages_received: reg.counter(
                "shs_ws_received_messages_total",
                "WebSocket messages received.",
            ),
            snapshot_bytes: reg.histogram(
                "shs_world_snapshot_bytes",
                "Encoded size of each snapshot sent to a client.",
                SNAPSHOT_BYTES_BUCKETS,
            ),
            corrections,
            kicks_slow: reg.counter(
                "shs_ws_kicks_slow_total",
                "Clients disconnected because their outbound queue filled up.",
            ),
            inbound_dropped: reg.counter(
                "shs_world_inbound_dropped_total",
                "Client inputs dropped because the world's command queue was full.",
            ),
            chat_messages: reg.counter("shs_chat_messages_total", "Chat messages delivered."),
            chat_dropped: reg.counter(
                "shs_chat_dropped_total",
                "Chat lines not delivered because the recipient's outbound queue was nearly full.",
            ),
            notices_dropped: reg.counter(
                "shs_notices_dropped_total",
                "Friend and invitation notices dropped: the recipient had a full backlog of them.",
            ),
            chat_rejected: reg.counter(
                "shs_chat_rejected_total",
                "Chat lines refused (rate limit, filter, unknown whisper target, invalid).",
            ),
            snapshot_full_states: reg.counter(
                "shs_world_snapshot_full_states_total",
                "Absolute entity states sent in snapshots (enters, resyncs, long moves).",
            ),
            snapshot_deltas: reg.counter(
                "shs_world_snapshot_deltas_total",
                "Entity deltas sent in snapshots.",
            ),
            snapshot_looks: reg.counter(
                "shs_world_snapshot_looks_total",
                "Look changes sent in snapshots (protocol v10).",
            ),
            look_targets: reg.counter(
                "shs_world_look_targets_total",
                "LookAt messages taken (a target set or cleared).",
            ),
            look_dropped: reg.counter(
                "shs_world_look_dropped_total",
                "LookAt messages dropped over the rate limit.",
            ),
            emotes: reg.counter("shs_world_emotes_total", "Emotes started."),
            mounts: reg.counter("shs_world_mounts_total", "Brooms mounted."),
            mount_refusals: reg.counter(
                "shs_world_mount_refusals_total",
                "Mount and landing requests refused.",
            ),
            zone_changes: reg.counter(
                "shs_world_zone_changes_total",
                "Characters moved between the castle and dorm instances.",
            ),
            dorm_instances: reg.gauge("shs_world_dorm_instances", "Live dorm room instances."),
            class_instances: reg
                .gauge("shs_world_class_instances", "Live class section instances."),
            arena_instances: reg.gauge("shs_world_arena_instances", "Live duel arena instances."),
            dorm_edits: reg.counter(
                "shs_dorm_edits_total",
                "Furniture edits stored and broadcast.",
            ),
            dorm_errors: reg.counter(
                "shs_dorm_errors_total",
                "Door, invitation and furniture actions refused with DormError.",
            ),
            positions_saved: reg.counter(
                "shs_world_positions_saved_total",
                "Character positions written to the database.",
            ),
            position_save_errors: reg.counter(
                "shs_world_position_save_errors_total",
                "Failed position save batches.",
            ),
            stairs_switches: reg.counter(
                "shs_stairs_switches_total",
                "Moving staircase flights that switched position.",
            ),
            stairs_riders_carried: reg.counter(
                "shs_stairs_riders_carried_total",
                "Characters a switching staircase flight carried to its new position.",
            ),
            stairs_grace_accepts: reg.counter(
                "shs_stairs_grace_accepts_total",
                "Moves accepted only through the grace window round a staircase switch.",
            ),
            stairs_switch_tick_seconds: reg.histogram(
                "shs_stairs_switch_tick_seconds",
                "World tick duration within a second of a staircase switch.",
                TICK_BUCKETS,
            ),
            stairs_reverse_refused: reg.counter(
                "shs_stairs_reverse_refused_total",
                "Grace accepts refused: a step from fixed ground onto a flight's incoming position before its switch.",
            ),
            stairs_early_holds: reg.counter(
                "shs_stairs_early_holds_total",
                "Steps onto a flight's incoming position just before its switch, held until it comes (an early clock).",
            ),
            stairs_corrections: reg.counter(
                "shs_stairs_corrections_total",
                "Walking corrections within the grace window round a staircase switch.",
            ),
            reveals_saved: reg.counter(
                "shs_map_reveals_saved_total",
                "Map reveals (a character and a surface) written to the database.",
            ),
            save_seconds: reg.histogram(
                "shs_world_save_seconds",
                "Time to write one saver batch (positions and map reveals).",
                SAVE_BUCKETS,
            ),
            discoveries: reg.counter(
                "shs_secrets_discovered_total",
                "Secrets found for the first time by a character (rewards granted).",
            ),
            friend_place_notices: reg.counter(
                "shs_friend_place_notices_total",
                "Castle place changes sent to the walker's online friends.",
            ),
            wishing_room_doors: reg.counter(
                "shs_wishing_room_doors_total",
                "Wishing Room doors opened by pacing.",
            ),
            wishing_room_entries: reg.counter(
                "shs_wishing_room_entries_total",
                "Characters entering a Wishing Room (door, invitation or visit).",
            ),
            wishing_room_instances: reg.gauge(
                "shs_world_wishing_room_instances",
                "Live Wishing Room instances.",
            ),
        }
    }
}
