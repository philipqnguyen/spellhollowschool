//! Turns what the bots and the monitor collected into the run summary: printed
//! as a table and written as JSON to `loadtest-results/`.
//!
//! Rates are measured over the *window*: from the end of the ramp plus the
//! settle time (when every bot is in and has walked off to its part of the
//! castle) to the end of the run. Counts of bad events (corrections,
//! disconnects) cover the whole run.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use serde::Serialize;

use crate::nav::Scenario;
use crate::scrape::{ContainerSample, HistogramDelta, Metrics};
use crate::stats::{BotReport, Dist};

/// The launch budgets.
pub const TICK_P99_BUDGET_MS: f64 = 15.0;
/// Data per client at view limit 100 (protocol v2)...
pub const BYTES_PER_CLIENT_BUDGET: f64 = 6.0 * 1024.0;
/// ...and at the default view limit of 50 or less.
pub const BYTES_PER_CLIENT_BUDGET_DEFAULT_VIEW: f64 = 3.5 * 1024.0;

/// The data budget for a run at `view_limit` (`None`: the server default, 50).
pub fn bytes_budget(view_limit: Option<u8>) -> f64 {
    match view_limit {
        Some(n) if n > protocol::VIEW_LIMIT_DEFAULT => BYTES_PER_CLIENT_BUDGET,
        _ => BYTES_PER_CLIENT_BUDGET_DEFAULT_VIEW,
    }
}
pub const SERVER_MEMORY_BUDGET_MIB: f64 = 1024.0;
/// 75% of the 2-CPU box, in `docker stats` percent of one CPU.
pub const CPU_BUDGET_PCT: f64 = 150.0;

#[derive(Debug, Clone, Serialize)]
pub struct RunConfig {
    pub url: String,
    pub bots: usize,
    pub ramp_secs: f64,
    pub duration_secs: f64,
    pub settle_secs: f64,
    pub chat_every_secs: f64,
    pub emote_every_secs: f64,
    /// Protocol v10: mean time between a bot's look-target changes (`--look-every-secs`).
    #[serde(default)]
    pub look_every_secs: f64,
    /// NPCs (protocol v12): mean time between a bot's `NpcInteract`
    /// (`--npc-talk-every-secs`; 0 = never).
    #[serde(default)]
    pub npc_talk_every_secs: f64,
    pub dress: bool,
    /// Percent of bots that drink a potion (`--effects-pct`).
    pub effects_pct: f64,
    /// Percent of bots that attend a class and that go to the Dueling Club.
    #[serde(default)]
    pub class_pct: f64,
    #[serde(default)]
    pub duel_pct: f64,
    pub ping_every_secs: f64,
    pub view_limit: Option<u8>,
    pub name_prefix: String,
    pub containers: Vec<String>,
    pub bot_threads: usize,
    /// Each bot's `GET /api/house-cup` interval (0: none), and
    /// whether one dev close runs mid-window.
    pub cup_poll_secs: f64,
    pub cup_close: bool,
    /// `--players-pct` and `--players-every-secs`.
    pub players_pct: f64,
    pub players_every_secs: f64,
    /// Percent of the walking bots flying over the grounds
    /// (`--fly-pct`), and the classes students were sent to (`--class-only`).
    #[serde(default)]
    pub fly_pct: f64,
    #[serde(default)]
    pub class_only: Vec<String>,
    /// The moving staircases' period the bots ran on (seconds;
    /// `--stairs-period-s` or the shipped one), `None` without them.
    #[serde(default)]
    pub stairs_period_secs: Option<u32>,
}

/// One point of the timeline, every sample interval.
#[derive(Debug, Clone, Serialize)]
pub struct Sample {
    /// Seconds since the run started.
    pub t: f64,
    pub bots_in_world: i64,
    pub bot_rx_bytes: u64,
    pub bot_rx_wire_bytes: u64,
    pub bot_rx_messages: u64,
    pub bot_tx_bytes: u64,
    pub bot_corrections: u64,
    pub bot_disconnects: u64,
    /// From `/metrics`, if the scrape worked.
    pub players: Option<f64>,
    pub tick_p50_ms: Option<f64>,
    pub tick_p99_ms: Option<f64>,
    pub server_sent_bytes: Option<f64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct DockerSample {
    pub t: f64,
    pub containers: Vec<ContainerSample>,
}

/// What the monitor and the bots hand over at the end.
pub struct Collected {
    pub scenario: Scenario,
    pub config: RunConfig,
    pub started_unix: u64,
    pub window: (f64, f64),
    pub timeline: Vec<Sample>,
    pub scrapes: Vec<(f64, Metrics)>,
    pub docker: Vec<DockerSample>,
    pub docker_errors: Vec<String>,
    pub reports: Vec<BotReport>,
    /// The dev close, if one ran.
    pub cup_close: Option<crate::cup::CupClose>,
}

#[derive(Debug, Serialize)]
pub struct Summary {
    pub scenario: Scenario,
    pub config: RunConfig,
    pub started_unix: u64,
    pub window: Window,
    pub bots: BotsSummary,
    pub server: Option<ServerSummary>,
    /// The House Cup route and the dev close.
    pub cup: Option<CupSummary>,
    /// Each class's results burst (`--long-window` puts it in the
    /// window).
    pub results: Vec<ResultsSummary>,
    pub docker: BTreeMap<String, ContainerSummary>,
    pub budgets: Vec<Budget>,
    pub timeline: Vec<Sample>,
    pub docker_samples: Vec<DockerSample>,
    pub docker_errors: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct Window {
    pub start_secs: f64,
    pub end_secs: f64,
    pub secs: f64,
}

#[derive(Debug, Serialize)]
pub struct BotsSummary {
    pub requested: usize,
    pub welcomed: usize,
    pub mean_in_world: f64,
    pub join_ms: Dist,
    pub rtt_ms: Dist,
    pub corrections: u64,
    pub disconnects: usize,
    pub disconnect_reasons: BTreeMap<String, usize>,
    pub connect_errors: usize,
    pub connect_error_reasons: BTreeMap<String, usize>,
    pub queued: u64,
    /// WebSocket payload received per bot per second (window).
    pub rx_payload_bytes_per_sec: f64,
    /// Payload plus WebSocket frame headers.
    pub rx_wire_bytes_per_sec: f64,
    pub rx_messages_per_sec: f64,
    pub tx_bytes_per_sec: f64,
    pub snapshot_bytes: Dist,
    /// Mean size of the visible set the server streams to a bot.
    pub visible_mean: f64,
    /// Entity states received over the whole run, absolute and as deltas.
    pub states_full: u64,
    pub states_delta: u64,
    /// Entering characters sent whole and as cached entries, over the run.
    pub entries_full: u64,
    pub entries_cached: u64,
    pub desyncs: u64,
    pub chat_rejected: u64,
    pub emotes_sent: u64,
    /// Protocol v10: `LookAt` messages the bots sent, and look changes they
    /// received in snapshots.
    #[serde(default)]
    pub looks_sent: u64,
    #[serde(default)]
    pub look_changes: u64,
    /// NPCs (protocol v12): the mean number of resident NPCs in a bot's
    /// view, the `NpcInteract` messages sent and their answers.
    #[serde(default)]
    pub npc_visible_mean: f64,
    #[serde(default)]
    pub npc_talks_sent: u64,
    #[serde(default)]
    pub npc_talk_replies: u64,
    #[serde(default)]
    pub npc_gifts: u64,
    #[serde(default)]
    pub npc_too_far: u64,
    #[serde(default)]
    pub npc_refused: u64,
    /// The NPCs' share of the payload per bot, from each bot's first
    /// `Welcome` to the end of the window (as the role rates).
    #[serde(default)]
    pub npc_rx_bytes_per_sec: f64,
    pub dressed: usize,
    pub dress_errors: usize,
    pub dress_error_reasons: BTreeMap<String, usize>,
    /// Bots given a potion, those whose drink the server confirmed (`Drank`),
    /// refused drinks, and `EffectsChanged` messages received by all bots.
    pub potion_bots: usize,
    pub drank: u64,
    pub potion_errors: u64,
    pub effects_changed: u64,
    pub moves_sent: u64,
    pub chats_sent: u64,
    pub chats_received: u64,
    pub pings_sent: u64,
    pub arrivals: u64,
    pub stuck: u64,
    pub unroutable: u64,
    /// Castle D1: walking steps that ended on a keep-out footprint (`scenarios.json` `keep_out`).
    pub in_keep_out: u64,
    /// What the students, duelists and spectators did, and
    /// each role's bots with what they received per second from their
    /// first `Welcome` to the end of the window (ramp included).
    pub school: crate::school::SchoolStats,
    pub roles: BTreeMap<String, RoleSummary>,
    pub teleport_errors: u64,
    /// Castle kit M2: the Owl Exchange court (`exchange` scenario).
    pub exchange: crate::exchange::ExchangeStats,
    /// The flyers and pitch spectators (`crate::flyer`).
    pub fly: crate::flyer::FlyStats,
    /// What the bots did about the moving staircases.
    pub stairs: crate::stairs::StairStats,
    /// The map at sign-in (and its latency), secrets found, the
    /// Wishing Room's rituals.
    pub secrets: crate::secrets::SecretStats,
    pub map_ms: crate::stats::Dist,
    /// The players list pages and cards the bots asked for,
    /// and their latency (ms).
    pub players: crate::players::PlayerStats,
    pub players_list_ms: crate::stats::Dist,
    pub players_card_ms: crate::stats::Dist,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct RoleSummary {
    pub bots: usize,
    pub rx_wire_bytes_per_sec: f64,
    /// Corrections the role's bots received (whole run).
    pub corrections: u64,
}

#[derive(Debug, Serialize)]
pub struct ServerSummary {
    pub tick_p50_ms: f64,
    pub tick_p90_ms: f64,
    pub tick_p99_ms: f64,
    pub tick_mean_ms: f64,
    /// Upper bound of the histogram bucket holding the slowest tick; `None`
    /// when it was beyond the largest finite bucket (100 ms).
    pub tick_max_bucket_ms: Option<f64>,
    /// The worst p99 of any single sample interval in the window.
    pub tick_p99_worst_interval_ms: f64,
    pub ticks: f64,
    /// Ticks slower than the 15 ms budget, and than 50 ms (a missed tick slot).
    pub ticks_over_15ms: f64,
    pub ticks_over_50ms: f64,
    pub tick_overruns: f64,
    pub players_min: f64,
    pub players_max: f64,
    pub players_mean: f64,
    pub sent_bytes_per_sec_per_player: f64,
    pub sent_messages_per_sec_per_player: f64,
    pub received_bytes_per_sec_per_player: f64,
    pub snapshot_bytes_p50: f64,
    pub snapshot_bytes_p99: f64,
    pub snapshot_bytes_mean: f64,
    pub snapshots_per_sec_per_player: f64,
    /// Entity states per player per second, absolute and as deltas.
    pub full_states_per_sec_per_player: f64,
    pub deltas_per_sec_per_player: f64,
    pub chat_deliveries_per_sec: f64,
    /// Whole run, by reason.
    pub corrections: BTreeMap<String, f64>,
    pub corrections_total: f64,
    pub kicks_slow: f64,
    pub inbound_dropped: f64,
    pub position_save_errors: f64,
    /// Most class sections and duel arenas open at once in the window.
    pub class_instances_max: f64,
    pub arena_instances_max: f64,
    /// In the window: moving staircase switches, riders the
    /// server carried, moves accepted only through the grace window, and
    /// the world tick within a second of a switch (p99, and how many such
    /// ticks; `None` without any).
    pub stairs_switches: f64,
    pub stairs_riders_carried: f64,
    pub stairs_grace_accepts: f64,
    pub stairs_switch_ticks: f64,
    pub stairs_switch_tick_p99_ms: Option<f64>,
    /// In the window: reverse rides refused round a switch, map
    /// reveals written and the saver's batch time (positions and reveals:
    /// p50 / p99 / max bucket, `None` without a save), secrets found, Wishing
    /// Room doors opened and entries, most rooms open at once.
    pub stairs_reverse_refused: f64,
    /// Fix, in the window: steps onto an arriving flight held
    /// until its switch (an early clock), and walking corrections within
    /// the grace of a switch.
    pub stairs_early_holds: f64,
    pub stairs_corrections: f64,
    pub reveals_saved: f64,
    pub saves: f64,
    pub save_p50_ms: Option<f64>,
    pub save_p99_ms: Option<f64>,
    pub save_max_bucket_ms: Option<f64>,
    pub discoveries: f64,
    pub wishing_room_doors: f64,
    pub wishing_room_entries: f64,
    pub wishing_room_instances_max: f64,
}

/// `GET /api/house-cup` in the window, and the dev close.
#[derive(Debug, Serialize)]
pub struct CupSummary {
    pub poll_every_secs: f64,
    /// Successful requests sent in the window, and per second.
    pub polls: usize,
    pub polls_per_sec: f64,
    /// Latency of those requests (and its 95th percentile).
    pub ms: Dist,
    pub p95_ms: f64,
    /// Over the whole run.
    pub sent: u64,
    pub errors: u64,
    pub error_reasons: BTreeMap<String, u64>,
    pub close: Option<crate::cup::CupClose>,
    /// The world tick p99 of the sample interval the close fell in, next to
    /// the window's (`server.tick_p99_ms`).
    pub tick_p99_close_interval_ms: Option<f64>,
}

/// One class's results burst: when its `ClassResult`s came,
/// whether they fell inside the measured window, and the world tick over the
/// sample intervals they span.
#[derive(Debug, Serialize)]
pub struct ResultsSummary {
    pub class: String,
    pub results: u64,
    pub first_secs: f64,
    pub last_secs: f64,
    pub in_window: bool,
    /// The scrapes the tick is measured between (the last at or before the
    /// first result, the first at or after the last one).
    pub tick_from_secs: f64,
    pub tick_to_secs: f64,
    pub tick_p99_ms: Option<f64>,
    pub ticks_over_15ms: f64,
}

#[derive(Debug, Serialize)]
pub struct ContainerSummary {
    pub samples: usize,
    pub cpu_pct_mean: f64,
    pub cpu_pct_max: f64,
    pub mem_mib_mean: f64,
    pub mem_mib_max: f64,
    pub mem_limit_mib: f64,
}

#[derive(Debug, Serialize)]
pub struct Budget {
    pub name: &'static str,
    pub limit: String,
    pub measured: String,
    /// `None` when it could not be measured (e.g. no containers given).
    pub pass: Option<bool>,
}

impl Summary {
    pub fn build(c: Collected) -> Summary {
        let (w0, w1) = c.window;
        let window = Window {
            start_secs: w0,
            end_secs: w1,
            secs: (w1 - w0).max(0.0),
        };
        let bots = bots_summary(&c, w0, w1);
        let server = server_summary(&c.scrapes, w0, w1);
        let cup = cup_summary(&c, w0, w1);
        let results = results_summary(&bots.school, &c.scrapes, w0, w1);
        let docker = docker_summary(&c.docker, w0, w1);
        let budgets = budgets(&bots, server.as_ref(), &docker, c.config.view_limit);
        Summary {
            scenario: c.scenario,
            config: c.config,
            started_unix: c.started_unix,
            window,
            bots,
            server,
            cup,
            results,
            docker,
            budgets,
            timeline: c.timeline,
            docker_samples: c.docker,
            docker_errors: c.docker_errors,
        }
    }

    pub fn passed(&self) -> bool {
        self.budgets.iter().all(|b| b.pass != Some(false))
    }

    /// Human-readable summary for the terminal.
    pub fn render(&self) -> String {
        let mut s = String::new();
        let b = &self.bots;
        let _ = writeln!(
            s,
            "\n== {} with {} bots: window {:.0}-{:.0} s ({:.0} s) ==",
            self.scenario.name,
            self.config.bots,
            self.window.start_secs,
            self.window.end_secs,
            self.window.secs
        );
        let _ = writeln!(
            s,
            "bots      welcomed {}/{}  mean in world {:.0}  queued {}  join p50 {:.0} ms p99 {:.0} ms",
            b.welcomed, b.requested, b.mean_in_world, b.queued, b.join_ms.p50, b.join_ms.p99
        );
        let _ = writeln!(
            s,
            "          rtt p50 {:.1} ms p99 {:.1} ms max {:.1} ms ({} pings)",
            b.rtt_ms.p50, b.rtt_ms.p99, b.rtt_ms.max, b.rtt_ms.count
        );
        let _ = writeln!(
            s,
            "          rx {:.0} B/s payload, {:.0} B/s with frame headers, {:.1} msg/s; tx {:.0} B/s",
            b.rx_payload_bytes_per_sec,
            b.rx_wire_bytes_per_sec,
            b.rx_messages_per_sec,
            b.tx_bytes_per_sec
        );
        let _ = writeln!(
            s,
            "          snapshot p50 {:.0} B p99 {:.0} B max {:.0} B, visible set mean {:.1}",
            b.snapshot_bytes.p50, b.snapshot_bytes.p99, b.snapshot_bytes.max, b.visible_mean
        );
        let _ = writeln!(
            s,
            "          states {} full / {} deltas, desyncs {}; chat refused {}; emotes {}; dressed {} ({} errors)",
            b.states_full,
            b.states_delta,
            b.desyncs,
            b.chat_rejected,
            b.emotes_sent,
            b.dressed,
            b.dress_errors
        );
        let _ = writeln!(
            s,
            "          looks {} sent, {} changes received",
            b.looks_sent, b.look_changes
        );
        let _ = writeln!(
            s,
            "          npcs {:.1} in view, {:.1} B/s per bot; talk {} sent: {} talk, {} gifts, {} too far, {} refused",
            b.npc_visible_mean,
            b.npc_rx_bytes_per_sec,
            b.npc_talks_sent,
            b.npc_talk_replies,
            b.npc_gifts,
            b.npc_too_far,
            b.npc_refused
        );
        let _ = writeln!(
            s,
            "          entering characters {} sent whole / {} cached",
            b.entries_full, b.entries_cached
        );
        if b.potion_bots > 0 {
            let _ = writeln!(
                s,
                "          potions: {} bots given one, {} drank, {} refused; {} EffectsChanged received",
                b.potion_bots, b.drank, b.potion_errors, b.effects_changed
            );
        }
        // The grounds walkers' bandwidth on its own, even when
        // every bot tours the grounds.
        if b.roles.len() > 1
            || b.roles.contains_key(crate::grounds::ROLE)
            || b.roles.contains_key(crate::flyer::FLYER)
            || b.roles.contains_key(crate::stairs::ROLE)
        {
            let roles: Vec<String> = b
                .roles
                .iter()
                .map(|(r, e)| {
                    let fixed = if e.corrections > 0 {
                        format!(", {} corrected", e.corrections)
                    } else {
                        String::new()
                    };
                    format!("{r} {} ({:.0} B/s{fixed})", e.bots, e.rx_wire_bytes_per_sec)
                })
                .collect();
            let _ = writeln!(
                s,
                "          roles: {} (rx since welcome); teleport errors {}",
                roles.join(", "),
                b.teleport_errors
            );
        }
        let school_roles = b
            .roles
            .keys()
            .any(|r| matches!(r.as_str(), "student" | "duelist" | "spectator"));
        if school_roles {
            let sc = &b.school;
            let _ = writeln!(
                s,
                "          classes: {} joined, {} door refusals, traces {} sent / {} scored / {} rejected, {} results",
                sc.classes_joined,
                sc.class_refusals,
                sc.traces_sent,
                sc.traces_scored,
                sc.traces_rejected,
                sc.class_results
            );
            // From the entry to the desks.
            let _ = writeln!(
                s,
                "          desks: {} walked to their place, {} sat at a desk seat, {} stopped short",
                sc.desk_walks, sc.seated, sc.desk_unroutable
            );
            // The Flying class's ring course.
            if sc.rings_flown > 0 || sc.class_mounts > 0 {
                let _ = writeln!(
                    s,
                    "          rings: {} students mounted by the class, {} ring beats flown at, {} passes scored",
                    sc.class_mounts, sc.rings_flown, sc.rings_scored
                );
            }
            // Picks, refusals by reason, and each class.
            if sc.picks_sent > 0 || !sc.by_class.is_empty() {
                let rejects: Vec<String> =
                    sc.rejects.iter().map(|(r, n)| format!("{r} {n}")).collect();
                let _ = writeln!(
                    s,
                    "          picks {} sent / {} scored ({} right) / {} rejected; refusals: {}",
                    sc.picks_sent,
                    sc.picks_scored,
                    sc.picks_right,
                    sc.picks_rejected,
                    if rejects.is_empty() {
                        "none".to_owned()
                    } else {
                        rejects.join(", ")
                    }
                );
                // A line per class, with its kind and right answers.
                for (class, c) in &sc.by_class {
                    let _ = writeln!(s, "          {}", c.line(class));
                    // What its results carried, and the tick then.
                    if let Some(line) = c.results_line(class) {
                        let _ = writeln!(s, "            {line}");
                    }
                    if let Some(r) = self.results.iter().find(|r| &r.class == class) {
                        let _ = writeln!(
                            s,
                            "            results burst {} the window: tick p99 {} over {:.0}-{:.0} s, {} ticks > 15 ms",
                            if r.in_window { "inside" } else { "outside" },
                            r.tick_p99_ms
                                .map_or("n/a".to_owned(), |v| format!("{v:.2} ms")),
                            r.tick_from_secs,
                            r.tick_to_secs,
                            r.ticks_over_15ms
                        );
                    }
                }
            }
            let _ = writeln!(
                s,
                "          duels: {} challenges, {} started, {} results, {} casts, {} event frames, {} watched, {} errors",
                sc.challenges_sent,
                sc.duels_started,
                sc.duel_results,
                sc.duel_casts,
                sc.duel_events,
                sc.spectated,
                sc.duel_errors
            );
        }
        // The flyers and the spectators.
        if let Some(line) = b.fly.line() {
            let _ = writeln!(s, "          {line}");
        }
        if b.exchange.standers > 0 || b.exchange.ring_trips > 0 {
            let e = &b.exchange;
            let _ = writeln!(
                s,
                "          Owl Exchange court: {} standing ({} use the counter), {} Interact / {} StationOpened, {} visits out of reach, {} walks to the ring",
                e.standers, e.openers, e.interacts, e.opened, e.missed, e.ring_trips
            );
        }
        for (reason, n) in &b.dress_error_reasons {
            let _ = writeln!(s, "            {n} x {reason}");
        }
        let _ = writeln!(
            s,
            "          corrections {}  disconnects {}  connect errors {}  arrivals {}  stuck {}  keep-out steps {}",
            b.corrections, b.disconnects, b.connect_errors, b.arrivals, b.stuck, b.in_keep_out
        );
        for (reason, n) in b.disconnect_reasons.iter().chain(&b.connect_error_reasons) {
            let _ = writeln!(s, "            {n} x {reason}");
        }
        if let Some(cup) = &self.cup {
            let _ = writeln!(
                s,
                "cup       GET /api/house-cup every {:.0} s: {} in the window ({:.1}/s), p50 {:.1} ms p95 {:.1} ms p99 {:.1} ms max {:.1} ms; {} sent, {} errors",
                cup.poll_every_secs,
                cup.polls,
                cup.polls_per_sec,
                cup.ms.p50,
                cup.p95_ms,
                cup.ms.p99,
                cup.ms.max,
                cup.sent,
                cup.errors
            );
            for (reason, n) in &cup.error_reasons {
                let _ = writeln!(s, "            {n} x {reason}");
            }
            if let Some(close) = &cup.close {
                let _ = writeln!(
                    s,
                    "          dev close at {:.0} s: status {}, closed {:?}, winners {:?}, mailed {:?}, {:.0} ms{}; tick p99 of that interval {}",
                    close.at_secs,
                    close.status,
                    close.closed,
                    close.winners,
                    close.mailed,
                    close.ms,
                    close
                        .error
                        .as_deref()
                        .map(|e| format!(", error {e}"))
                        .unwrap_or_default(),
                    cup.tick_p99_close_interval_ms
                        .map_or("n/a".to_owned(), |v| format!("{v:.2} ms"))
                );
            }
        }
        if let Some(sv) = &self.server {
            let _ = writeln!(
                s,
                "server    tick p50 {:.2} ms p99 {:.2} ms (worst interval {:.2} ms) mean {:.2} ms, slowest {}, {} ticks > 15 ms, {} > 50 ms, overruns {}",
                sv.tick_p50_ms,
                sv.tick_p99_ms,
                sv.tick_p99_worst_interval_ms,
                sv.tick_mean_ms,
                match sv.tick_max_bucket_ms {
                    Some(ms) => format!("<= {ms:.1} ms"),
                    None => "beyond the largest bucket".to_owned(),
                },
                sv.ticks_over_15ms,
                sv.ticks_over_50ms,
                sv.tick_overruns
            );
            let _ = writeln!(
                s,
                "          players {:.0}-{:.0}  sent {:.0} B/s/player ({:.1} msg/s)  received {:.0} B/s/player",
                sv.players_min,
                sv.players_max,
                sv.sent_bytes_per_sec_per_player,
                sv.sent_messages_per_sec_per_player,
                sv.received_bytes_per_sec_per_player
            );
            let _ = writeln!(
                s,
                "          snapshots p50 {:.0} B p99 {:.0} B, {:.1}/s/player ({:.1} full states + {:.1} deltas /s/player); chat deliveries {:.0}/s; corrections {:?}; slow kicks {}; dropped inputs {}",
                sv.snapshot_bytes_p50,
                sv.snapshot_bytes_p99,
                sv.snapshots_per_sec_per_player,
                sv.full_states_per_sec_per_player,
                sv.deltas_per_sec_per_player,
                sv.chat_deliveries_per_sec,
                sv.corrections,
                sv.kicks_slow,
                sv.inbound_dropped
            );
            // The moving staircases.
            if sv.stairs_switches > 0.0 || b.stairs.switches_seen > 0 {
                let st = &b.stairs;
                let _ = writeln!(
                    s,
                    "stairs    {:.0} switches ({} s period): server carried {:.0} riders, {:.0} grace accepts, {:.0} early steps held, {:.0} corrections near a switch; tick within 1 s of a switch p99 {} over {:.0} ticks; bots saw {} switches, rode {}, re-planned {}, held {} frames",
                    sv.stairs_switches,
                    self.config
                        .stairs_period_secs
                        .map_or("n/a".to_owned(), |p| p.to_string()),
                    sv.stairs_riders_carried,
                    sv.stairs_grace_accepts,
                    sv.stairs_early_holds,
                    sv.stairs_corrections,
                    sv.stairs_switch_tick_p99_ms
                        .map_or("n/a".to_owned(), |v| format!("{v:.2} ms")),
                    sv.stairs_switch_ticks,
                    st.switches_seen,
                    st.rides,
                    st.replans,
                    st.held_frames
                );
            }
            // The map, the secrets and the Wishing Room.
            let se = &b.secrets;
            if se.map_fetches + se.map_errors > 0 || sv.saves > 0.0 {
                let ms = |v: Option<f64>| v.map_or("n/a".to_owned(), |v| format!("{v:.1} ms"));
                let _ = writeln!(
                    s,
                    "secrets   map at sign-in: {} fetched ({} errors {:?}), p50 {:.1} ms p99 {:.1} ms, {:.1} surfaces each; saves {:.0} in the window: p50 {} p99 {} (max bucket {}), {:.0} reveals written; {:.0} secrets found ({} seen by bots); reverse rides refused {:.0}",
                    se.map_fetches,
                    se.map_errors,
                    se.map_error_reasons,
                    b.map_ms.p50,
                    b.map_ms.p99,
                    se.map_surfaces as f64 / se.map_fetches.max(1) as f64,
                    sv.saves,
                    ms(sv.save_p50_ms),
                    ms(sv.save_p99_ms),
                    ms(sv.save_max_bucket_ms),
                    sv.reveals_saved,
                    sv.discoveries,
                    se.discoveries,
                    sv.stairs_reverse_refused
                );
            }
            // The players list and cards.
            let pl = &b.players;
            if pl.browses > 0 {
                let _ = writeln!(
                    s,
                    "players   {} looks: {} list pages (p50 {:.1} ms p99 {:.1} ms, {:.1} listed each), {} cards (p50 {:.1} ms p99 {:.1} ms), {} errors {:?}",
                    pl.browses,
                    pl.pages,
                    b.players_list_ms.p50,
                    b.players_list_ms.p99,
                    pl.listed as f64 / pl.pages.max(1) as f64,
                    pl.cards,
                    b.players_card_ms.p50,
                    b.players_card_ms.p99,
                    pl.errors,
                    pl.error_reasons
                );
            }
            if sv.wishing_room_doors > 0.0 || se.passes > 0 {
                let _ = writeln!(
                    s,
                    "          Wishing Room: {:.0} doors opened, {:.0} entries (bots: {} passes, {} doors, {} Lost Property Vault, {} Gathering, {} refusals, {} rituals that failed), at most {:.0} rooms open",
                    sv.wishing_room_doors,
                    sv.wishing_room_entries,
                    se.passes,
                    se.doors,
                    se.entries[0],
                    se.entries[1],
                    se.refusals,
                    se.failed,
                    sv.wishing_room_instances_max
                );
            }
            if sv.class_instances_max > 0.0 || sv.arena_instances_max > 0.0 {
                let _ = writeln!(
                    s,
                    "          instances open at most: {:.0} class sections, {:.0} duel arenas",
                    sv.class_instances_max, sv.arena_instances_max
                );
            }
        }
        for (name, d) in &self.docker {
            let _ = writeln!(
                s,
                "docker    {name}: cpu mean {:.0}% max {:.0}%, mem mean {:.0} MiB max {:.0} MiB (limit {:.0} MiB)",
                d.cpu_pct_mean, d.cpu_pct_max, d.mem_mib_mean, d.mem_mib_max, d.mem_limit_mib
            );
        }
        let _ = writeln!(s, "budgets");
        for bd in &self.budgets {
            let verdict = match bd.pass {
                Some(true) => "PASS",
                Some(false) => "FAIL",
                None => "n/a ",
            };
            let _ = writeln!(
                s,
                "  {verdict}  {:<34} {:>16}  (limit {})",
                bd.name, bd.measured, bd.limit
            );
        }
        s
    }
}

/// The value at the sample nearest `t` (for cumulative counters).
fn at<T: Copy>(timeline: &[Sample], t: f64, f: impl Fn(&Sample) -> T) -> Option<T> {
    timeline
        .iter()
        .min_by(|a, b| (a.t - t).abs().total_cmp(&(b.t - t).abs()))
        .map(f)
}

fn bots_summary(c: &Collected, w0: f64, w1: f64) -> BotsSummary {
    let mut join: Vec<f64> = Vec::new();
    let mut rtt: Vec<f64> = Vec::new();
    let mut snaps: Vec<f64> = Vec::new();
    let mut visible_sum = 0u64;
    let mut npc_visible_sum = 0u64;
    let mut disconnect_reasons = BTreeMap::new();
    let mut connect_error_reasons = BTreeMap::new();
    let sum = |f: fn(&BotReport) -> u64| c.reports.iter().map(f).sum::<u64>();
    let (corrections, queued, moves, chats, chats_rx, pings) = (
        sum(|r| r.corrections),
        sum(|r| r.queued),
        sum(|r| r.moves_sent),
        sum(|r| r.chats_sent),
        sum(|r| r.chats_received),
        sum(|r| r.pings_sent),
    );
    let (entries_full, entries_cached) = (sum(|r| r.entries_full), sum(|r| r.entries_cached));
    let (states_full, states_delta, desyncs, chat_rejected, emotes_sent) = (
        sum(|r| r.states_full),
        sum(|r| r.states_delta),
        sum(|r| r.desyncs),
        sum(|r| r.chat_rejected),
        sum(|r| r.emotes_sent),
    );
    let (looks_sent, look_changes) = (sum(|r| r.looks_sent), sum(|r| r.look_changes));
    let (npc_talks_sent, npc_talk_replies, npc_gifts, npc_too_far, npc_refused) = (
        sum(|r| r.npc_talks_sent),
        sum(|r| r.npc_talk_replies),
        sum(|r| r.npc_gifts),
        sum(|r| r.npc_too_far),
        sum(|r| r.npc_refused),
    );
    let mut dress_error_reasons = BTreeMap::new();
    for r in &c.reports {
        for e in &r.dress_errors {
            *dress_error_reasons.entry(reason_key(e)).or_insert(0) += 1;
        }
    }
    let mut school = crate::school::SchoolStats::default();
    let mut roles: BTreeMap<String, RoleSummary> = BTreeMap::new();
    let mut role_secs: BTreeMap<String, f64> = BTreeMap::new();
    let (mut npc_rx_bytes, mut npc_secs) = (0u64, 0.0f64);
    for r in &c.reports {
        if let Some(t) = r.welcomed_at_secs {
            npc_rx_bytes += r.npc_rx_bytes;
            npc_secs += (w1 - t).max(0.0);
        }
        school.add(&r.school);
        let role = r.role.unwrap_or("walker").to_owned();
        let e = roles.entry(role.clone()).or_default();
        e.bots += 1;
        e.corrections += r.corrections;
        if let Some(t) = r.welcomed_at_secs {
            e.rx_wire_bytes_per_sec += r.rx_wire_bytes as f64;
            *role_secs.entry(role).or_default() += (w1 - t).max(0.0);
        }
    }
    for (role, e) in &mut roles {
        let secs = role_secs.get(role).copied().unwrap_or(0.0);
        e.rx_wire_bytes_per_sec = if secs > 0.0 {
            e.rx_wire_bytes_per_sec / secs
        } else {
            0.0
        };
    }
    let (arrivals, stuck, unroutable, in_keep_out) = (
        sum(|r| r.walk.arrivals),
        sum(|r| r.walk.stuck),
        sum(|r| r.walk.unroutable),
        sum(|r| r.walk.in_keep_out),
    );
    for r in &c.reports {
        join.extend(r.join_ms.map(f64::from));
        rtt.extend(r.rtt_ms.iter().map(|&v| f64::from(v)));
        snaps.extend(r.snapshot_bytes.iter().map(|&v| f64::from(v)));
        visible_sum += r.visible_sum;
        npc_visible_sum += r.npc_visible_sum;
        for d in &r.disconnects {
            *disconnect_reasons.entry(reason_key(d)).or_insert(0) += 1;
        }
        for d in &r.connect_errors {
            *connect_error_reasons.entry(reason_key(d)).or_insert(0) += 1;
        }
    }
    let in_window: Vec<&Sample> = c
        .timeline
        .iter()
        .filter(|s| s.t >= w0 && s.t <= w1)
        .collect();
    let mean_in_world = if in_window.is_empty() {
        0.0
    } else {
        in_window
            .iter()
            .map(|s| s.bots_in_world as f64)
            .sum::<f64>()
            / in_window.len() as f64
    };
    let rate = |f: fn(&Sample) -> u64| -> f64 {
        let (Some(a), Some(b)) = (at(&c.timeline, w0, f), at(&c.timeline, w1, f)) else {
            return 0.0;
        };
        let secs = w1 - w0;
        if secs <= 0.0 || mean_in_world <= 0.0 {
            0.0
        } else {
            (b.saturating_sub(a)) as f64 / secs / mean_in_world
        }
    };
    let snapshot_bytes = Dist::of(&mut snaps);
    let mut secrets = crate::secrets::SecretStats::default();
    let mut players = crate::players::PlayerStats::default();
    for r in &c.reports {
        secrets.add(&r.secrets);
        players.add(&r.players);
    }
    BotsSummary {
        requested: c.config.bots,
        welcomed: c.reports.iter().filter(|r| r.welcomed).count(),
        mean_in_world,
        join_ms: Dist::of(&mut join),
        rtt_ms: Dist::of(&mut rtt),
        corrections,
        disconnects: disconnect_reasons.values().sum(),
        disconnect_reasons,
        connect_errors: connect_error_reasons.values().sum(),
        connect_error_reasons,
        queued,
        rx_payload_bytes_per_sec: rate(|s| s.bot_rx_bytes),
        rx_wire_bytes_per_sec: rate(|s| s.bot_rx_wire_bytes),
        rx_messages_per_sec: rate(|s| s.bot_rx_messages),
        tx_bytes_per_sec: rate(|s| s.bot_tx_bytes),
        visible_mean: if snapshot_bytes.count > 0 {
            visible_sum as f64 / snapshot_bytes.count as f64
        } else {
            0.0
        },
        snapshot_bytes,
        states_full,
        states_delta,
        entries_full,
        entries_cached,
        desyncs,
        chat_rejected,
        emotes_sent,
        looks_sent,
        look_changes,
        npc_visible_mean: if snapshot_bytes.count == 0 {
            0.0
        } else {
            npc_visible_sum as f64 / snapshot_bytes.count as f64
        },
        npc_talks_sent,
        npc_talk_replies,
        npc_gifts,
        npc_too_far,
        npc_refused,
        npc_rx_bytes_per_sec: if npc_secs > 0.0 {
            npc_rx_bytes as f64 / npc_secs
        } else {
            0.0
        },
        dressed: c.reports.iter().filter(|r| r.dressed).count(),
        dress_errors: dress_error_reasons.values().sum(),
        dress_error_reasons,
        potion_bots: c.reports.iter().filter(|r| r.potion).count(),
        drank: sum(|r| r.drank),
        potion_errors: sum(|r| r.potion_errors),
        effects_changed: sum(|r| r.effects_changed),
        moves_sent: moves,
        chats_sent: chats,
        chats_received: chats_rx,
        pings_sent: pings,
        arrivals,
        stuck,
        unroutable,
        in_keep_out,
        school,
        roles,
        teleport_errors: sum(|r| r.teleport_errors),
        exchange: {
            let mut e = crate::exchange::ExchangeStats::default();
            for r in &c.reports {
                e.add(&r.exchange);
            }
            e
        },
        fly: {
            let mut f = crate::flyer::FlyStats::default();
            for r in &c.reports {
                f.add(&r.fly);
            }
            f
        },
        stairs: {
            let mut st = crate::stairs::StairStats::default();
            for r in &c.reports {
                st.add(&r.stairs);
            }
            st
        },
        map_ms: crate::stats::Dist::of(&mut secrets.map_ms.clone()),
        secrets,
        players_list_ms: crate::stats::Dist::of(&mut players.list_ms.clone()),
        players_card_ms: crate::stats::Dist::of(&mut players.card_ms.clone()),
        players,
    }
}

/// The largest value of gauge `name` among the scrapes from `t0` to `t1`.
fn gauge_max(scrapes: &[(f64, Metrics)], t0: f64, t1: f64, name: &str) -> f64 {
    scrapes
        .iter()
        .filter(|(t, _)| *t >= t0 && *t <= t1)
        .map(|(_, m)| m.get(name))
        .fold(0.0, f64::max)
}

/// Groups error messages that differ only in details (numbers, addresses).
fn reason_key(reason: &str) -> String {
    let key: String = reason
        .chars()
        .map(|c| if c.is_ascii_digit() { '#' } else { c })
        .collect();
    key.chars().take(120).collect()
}

/// The scrape nearest `t`.
fn scrape_at(scrapes: &[(f64, Metrics)], t: f64) -> Option<&(f64, Metrics)> {
    scrapes
        .iter()
        .min_by(|a, b| (a.0 - t).abs().total_cmp(&(b.0 - t).abs()))
}

fn server_summary(scrapes: &[(f64, Metrics)], w0: f64, w1: f64) -> Option<ServerSummary> {
    let (t0, start) = scrape_at(scrapes, w0)?;
    let (t1, end) = scrape_at(scrapes, w1)?;
    let secs = t1 - t0;
    if secs <= 0.0 {
        return None;
    }
    let tick = HistogramDelta::between(start, end, "shs_world_tick_seconds");
    let snap = HistogramDelta::between(start, end, "shs_world_snapshot_bytes");
    let switch_tick = HistogramDelta::between(start, end, "shs_stairs_switch_tick_seconds");
    let save = HistogramDelta::between(start, end, "shs_world_save_seconds");
    let delta = |name: &str| end.get(name) - start.get(name);
    let players: Vec<f64> = scrapes
        .iter()
        .filter(|(t, _)| *t >= *t0 && *t <= *t1)
        .map(|(_, m)| m.get("shs_world_players"))
        .collect();
    let players_mean = players.iter().sum::<f64>() / players.len().max(1) as f64;
    let per_player = |name: &str| {
        let d = end.get(name) - start.get(name);
        if players_mean > 0.0 {
            d / secs / players_mean
        } else {
            0.0
        }
    };
    let worst = scrapes
        .windows(2)
        .filter(|w| w[0].0 >= *t0 && w[1].0 <= *t1)
        .map(|w| HistogramDelta::between(&w[0].1, &w[1].1, "shs_world_tick_seconds").quantile(0.99))
        .fold(0.0, f64::max);
    let last = &scrapes.last()?.1;
    // Counted over the run only: the server may have served others before.
    let first = &scrapes.first()?.1;
    let corrections: BTreeMap<String, f64> = last
        .by_label("shs_world_corrections_total", "reason")
        .into_iter()
        .map(|(reason, n)| {
            let before = first
                .by_label("shs_world_corrections_total", "reason")
                .get(&reason)
                .copied()
                .unwrap_or(0.0);
            (reason, n - before)
        })
        .collect();
    Some(ServerSummary {
        tick_p50_ms: tick.quantile(0.5) * 1e3,
        tick_p90_ms: tick.quantile(0.9) * 1e3,
        tick_p99_ms: tick.quantile(0.99) * 1e3,
        tick_mean_ms: tick.mean() * 1e3,
        tick_max_bucket_ms: Some(tick.max_bound() * 1e3).filter(|v| v.is_finite()),
        tick_p99_worst_interval_ms: worst * 1e3,
        ticks: tick.count,
        ticks_over_15ms: tick.count_above(0.015),
        ticks_over_50ms: tick.count_above(0.05),
        tick_overruns: end.get("shs_world_tick_overruns_total")
            - start.get("shs_world_tick_overruns_total"),
        players_min: players.iter().copied().fold(f64::INFINITY, f64::min),
        players_max: players.iter().copied().fold(0.0, f64::max),
        players_mean,
        sent_bytes_per_sec_per_player: per_player("shs_ws_sent_bytes_total"),
        sent_messages_per_sec_per_player: per_player("shs_ws_sent_messages_total"),
        received_bytes_per_sec_per_player: per_player("shs_ws_received_bytes_total"),
        snapshot_bytes_p50: snap.quantile(0.5),
        snapshot_bytes_p99: snap.quantile(0.99),
        snapshot_bytes_mean: snap.mean(),
        snapshots_per_sec_per_player: if players_mean > 0.0 {
            snap.count / secs / players_mean
        } else {
            0.0
        },
        full_states_per_sec_per_player: per_player("shs_world_snapshot_full_states_total"),
        deltas_per_sec_per_player: per_player("shs_world_snapshot_deltas_total"),
        chat_deliveries_per_sec: (end.get("shs_chat_messages_total")
            - start.get("shs_chat_messages_total"))
            / secs,
        corrections_total: last.sum("shs_world_corrections_total")
            - first.sum("shs_world_corrections_total"),
        corrections,
        kicks_slow: last.get("shs_ws_kicks_slow_total") - first.get("shs_ws_kicks_slow_total"),
        inbound_dropped: last.get("shs_world_inbound_dropped_total")
            - first.get("shs_world_inbound_dropped_total"),
        position_save_errors: last.get("shs_world_position_save_errors_total"),
        class_instances_max: gauge_max(scrapes, *t0, *t1, "shs_world_class_instances"),
        arena_instances_max: gauge_max(scrapes, *t0, *t1, "shs_world_arena_instances"),
        stairs_switches: end.get("shs_stairs_switches_total")
            - start.get("shs_stairs_switches_total"),
        stairs_riders_carried: end.get("shs_stairs_riders_carried_total")
            - start.get("shs_stairs_riders_carried_total"),
        stairs_grace_accepts: end.get("shs_stairs_grace_accepts_total")
            - start.get("shs_stairs_grace_accepts_total"),
        stairs_switch_ticks: switch_tick.count,
        stairs_switch_tick_p99_ms: (switch_tick.count > 0.0)
            .then(|| switch_tick.quantile(0.99) * 1e3),
        stairs_reverse_refused: delta("shs_stairs_reverse_refused_total"),
        stairs_early_holds: delta("shs_stairs_early_holds_total"),
        stairs_corrections: delta("shs_stairs_corrections_total"),
        reveals_saved: delta("shs_map_reveals_saved_total"),
        saves: save.count,
        save_p50_ms: (save.count > 0.0).then(|| save.quantile(0.5) * 1e3),
        save_p99_ms: (save.count > 0.0).then(|| save.quantile(0.99) * 1e3),
        save_max_bucket_ms: (save.count > 0.0)
            .then(|| save.max_bound() * 1e3)
            .filter(|v| v.is_finite()),
        discoveries: delta("shs_secrets_discovered_total"),
        wishing_room_doors: delta("shs_wishing_room_doors_total"),
        wishing_room_entries: delta("shs_wishing_room_entries_total"),
        wishing_room_instances_max: gauge_max(
            scrapes,
            *t0,
            *t1,
            "shs_world_wishing_room_instances",
        ),
    })
}

fn cup_summary(c: &Collected, w0: f64, w1: f64) -> Option<CupSummary> {
    if c.config.cup_poll_secs <= 0.0 && c.cup_close.is_none() {
        return None;
    }
    let mut all = crate::cup::CupPolls::default();
    for r in &c.reports {
        all.add(&r.cup);
    }
    let mut ms: Vec<f64> = all
        .samples
        .iter()
        .filter(|(t, _)| *t >= w0 && *t <= w1)
        .map(|s| s.1)
        .collect();
    let secs = w1 - w0;
    let polls = ms.len();
    let tick_p99_close_interval_ms = c.cup_close.as_ref().and_then(|close| {
        let w = c
            .scrapes
            .windows(2)
            .find(|w| w[0].0 <= close.at_secs && close.at_secs <= w[1].0)?;
        let tick = HistogramDelta::between(&w[0].1, &w[1].1, "shs_world_tick_seconds");
        Some(tick.quantile(0.99) * 1e3)
    });
    let dist = Dist::of(&mut ms);
    let p95_ms = if polls == 0 {
        0.0
    } else {
        ms[((0.95 * polls as f64).ceil() as usize).clamp(1, polls) - 1]
    };
    Some(CupSummary {
        poll_every_secs: c.config.cup_poll_secs,
        polls,
        polls_per_sec: if secs > 0.0 { polls as f64 / secs } else { 0.0 },
        ms: dist,
        p95_ms,
        sent: all.sent,
        errors: all.errors,
        error_reasons: all.error_reasons,
        close: c.cup_close.clone(),
        tick_p99_close_interval_ms,
    })
}

/// Each class's results burst (see [`ResultsSummary`]).
fn results_summary(
    school: &crate::school::SchoolStats,
    scrapes: &[(f64, Metrics)],
    w0: f64,
    w1: f64,
) -> Vec<ResultsSummary> {
    school
        .by_class
        .iter()
        .filter_map(|(class, c)| {
            let (first, last) = (c.first_result_secs?, c.last_result_secs?);
            let from = scrapes
                .iter()
                .rev()
                .find(|(t, _)| *t <= first)
                .or(scrapes.first());
            let to = scrapes.iter().find(|(t, _)| *t >= last).or(scrapes.last());
            let tick = match (from, to) {
                (Some(a), Some(b)) if b.0 > a.0 => Some((
                    a.0,
                    b.0,
                    HistogramDelta::between(&a.1, &b.1, "shs_world_tick_seconds"),
                )),
                _ => None,
            };
            Some(ResultsSummary {
                class: class.clone(),
                results: c.results,
                first_secs: first,
                last_secs: last,
                in_window: first >= w0 && last <= w1,
                tick_from_secs: tick.as_ref().map_or(first, |t| t.0),
                tick_to_secs: tick.as_ref().map_or(last, |t| t.1),
                tick_p99_ms: tick
                    .as_ref()
                    .filter(|t| t.2.count > 0.0)
                    .map(|t| t.2.quantile(0.99) * 1e3),
                ticks_over_15ms: tick.as_ref().map_or(0.0, |t| t.2.count_above(0.015)),
            })
        })
        .collect()
}

fn docker_summary(
    samples: &[DockerSample],
    w0: f64,
    w1: f64,
) -> BTreeMap<String, ContainerSummary> {
    let mut by_name: BTreeMap<String, Vec<&ContainerSample>> = BTreeMap::new();
    for s in samples.iter().filter(|s| s.t >= w0 && s.t <= w1) {
        for c in &s.containers {
            by_name.entry(c.name.clone()).or_default().push(c);
        }
    }
    by_name
        .into_iter()
        .map(|(name, v)| {
            let n = v.len() as f64;
            let summary = ContainerSummary {
                samples: v.len(),
                cpu_pct_mean: v.iter().map(|c| c.cpu_pct).sum::<f64>() / n,
                cpu_pct_max: v.iter().map(|c| c.cpu_pct).fold(0.0, f64::max),
                mem_mib_mean: v.iter().map(|c| c.mem_mib).sum::<f64>() / n,
                mem_mib_max: v.iter().map(|c| c.mem_mib).fold(0.0, f64::max),
                mem_limit_mib: v.iter().map(|c| c.mem_limit_mib).fold(0.0, f64::max),
            };
            (name, summary)
        })
        .collect()
}

fn budgets(
    bots: &BotsSummary,
    server: Option<&ServerSummary>,
    docker: &BTreeMap<String, ContainerSummary>,
    view_limit: Option<u8>,
) -> Vec<Budget> {
    let mut out = Vec::new();
    let tick = server.map(|s| s.tick_p99_ms);
    out.push(Budget {
        name: "world tick p99",
        limit: format!("<= {TICK_P99_BUDGET_MS} ms"),
        measured: tick.map_or("no metrics".into(), |v| format!("{v:.2} ms")),
        pass: tick.map(|v| v <= TICK_P99_BUDGET_MS),
    });
    // Judged on what the bots received including frame headers, which is
    // slightly more than the server's payload counter.
    let per_client = bots
        .rx_wire_bytes_per_sec
        .max(server.map_or(0.0, |s| s.sent_bytes_per_sec_per_player));
    let budget = bytes_budget(view_limit);
    out.push(Budget {
        name: "data sent per client",
        limit: format!(
            "<= {:.1} KB/s at view limit {}",
            budget / 1024.0,
            view_limit.unwrap_or(protocol::VIEW_LIMIT_DEFAULT)
        ),
        measured: format!("{:.2} KB/s", per_client / 1024.0),
        pass: Some(per_client <= budget),
    });
    let server_mem = docker
        .iter()
        .filter(|(n, _)| n.contains("server"))
        .map(|(_, c)| c.mem_mib_max)
        .reduce(f64::max);
    out.push(Budget {
        name: "server memory (max)",
        limit: format!("<= {SERVER_MEMORY_BUDGET_MIB:.0} MiB"),
        measured: server_mem.map_or("not sampled".into(), |m| format!("{m:.0} MiB")),
        pass: server_mem.map(|m| m <= SERVER_MEMORY_BUDGET_MIB),
    });
    let cpu = (!docker.is_empty()).then(|| docker.values().map(|c| c.cpu_pct_mean).sum::<f64>());
    out.push(Budget {
        name: "CPU, server + db (mean)",
        limit: format!("<= {CPU_BUDGET_PCT:.0}% (75% of 2 CPUs)"),
        measured: cpu.map_or("not sampled".into(), |c| format!("{c:.0}%")),
        pass: cpu.map(|c| c <= CPU_BUDGET_PCT),
    });
    out.push(Budget {
        name: "all bots joined",
        limit: format!("{}", bots.requested),
        measured: format!("{}", bots.welcomed),
        pass: Some(bots.welcomed == bots.requested),
    });
    out.push(Budget {
        name: "unexpected disconnects",
        limit: "0".into(),
        measured: format!("{}", bots.disconnects),
        pass: Some(bots.disconnects == 0),
    });
    out.push(Budget {
        name: "snapshot desyncs",
        limit: "0".into(),
        measured: format!("{}", bots.desyncs),
        pass: Some(bots.desyncs == 0),
    });
    let server_corrections = server.map_or(0.0, |s| s.corrections_total);
    out.push(Budget {
        name: "corrections to legal walkers",
        limit: "0".into(),
        measured: format!("{} (server {server_corrections})", bots.corrections),
        pass: Some(bots.corrections == 0 && server_corrections == 0.0),
    });
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(t: f64, bots: i64, rx: u64) -> Sample {
        Sample {
            t,
            bots_in_world: bots,
            bot_rx_bytes: rx,
            bot_rx_wire_bytes: rx + rx / 50,
            bot_rx_messages: rx / 100,
            bot_tx_bytes: rx / 10,
            bot_corrections: 0,
            bot_disconnects: 0,
            players: None,
            tick_p50_ms: None,
            tick_p99_ms: None,
            server_sent_bytes: None,
        }
    }

    #[test]
    fn rates_are_per_bot_over_the_window() {
        let c = Collected {
            scenario: Scenario::named("spread").unwrap(),
            config: RunConfig {
                url: "http://x:1".into(),
                bots: 2,
                ramp_secs: 0.0,
                duration_secs: 10.0,
                settle_secs: 0.0,
                chat_every_secs: 0.0,
                emote_every_secs: 0.0,
                look_every_secs: 0.0,
                npc_talk_every_secs: 0.0,
                dress: false,
                effects_pct: 0.0,
                class_pct: 0.0,
                duel_pct: 0.0,
                ping_every_secs: 5.0,
                view_limit: None,
                name_prefix: "lt".into(),
                containers: vec![],
                bot_threads: 1,
                cup_poll_secs: 0.0,
                cup_close: false,
                players_pct: 0.0,
                players_every_secs: 0.0,
                fly_pct: 0.0,
                stairs_period_secs: None,
                class_only: Vec::new(),
            },
            started_unix: 0,
            window: (10.0, 20.0),
            // 2 bots, 40 KB over 10 s in the window: 2 KB/s each.
            timeline: vec![
                sample(0.0, 0, 0),
                sample(10.0, 2, 1000),
                sample(20.0, 2, 41_000),
            ],
            scrapes: vec![],
            docker: vec![],
            docker_errors: vec![],
            reports: vec![
                BotReport {
                    welcomed: true,
                    ..BotReport::default()
                },
                BotReport {
                    welcomed: true,
                    disconnects: vec!["kick: slow 1".into(), "kick: slow 2".into()],
                    ..BotReport::default()
                },
            ],
            cup_close: None,
        };
        let s = Summary::build(c);
        assert_eq!(s.bots.rx_payload_bytes_per_sec, 2000.0);
        assert_eq!(s.bots.disconnect_reasons["kick: slow #"], 2);
        assert!(s.server.is_none());
        let failed: Vec<&str> = s
            .budgets
            .iter()
            .filter(|b| b.pass == Some(false))
            .map(|b| b.name)
            .collect();
        assert_eq!(failed, vec!["unexpected disconnects"]);
        assert!(!s.passed());
        assert!(s.render().contains("FAIL  unexpected disconnects"));
        assert!(s.cup.is_none(), "no cup polling asked for");
    }

    #[test]
    fn the_results_burst_is_timed_against_the_window() {
        let mut school = crate::school::SchoolStats::default();
        let mut c = crate::school::ClassStats {
            results: 30,
            first_result_secs: Some(406.0),
            last_result_secs: Some(408.0),
            ..Default::default()
        };
        school.by_class.insert("herbology".into(), c.clone());
        c.first_result_secs = Some(800.0);
        c.last_result_secs = Some(801.0);
        school.by_class.insert("late".into(), c);
        school.by_class.insert("none".into(), Default::default());
        let scrape = |t: f64, count: u64| {
            let text = format!(
                "shs_world_tick_seconds_bucket{{le=\"0.005\"}} {count}\n\
                 shs_world_tick_seconds_bucket{{le=\"+Inf\"}} {count}\n\
                 shs_world_tick_seconds_count {count}\n\
                 shs_world_tick_seconds_sum 0\n"
            );
            (t, Metrics::parse(&text))
        };
        let scrapes = vec![scrape(400.0, 100), scrape(405.0, 200), scrape(410.0, 300)];
        let r = results_summary(&school, &scrapes, 90.0, 750.0);
        assert_eq!(r.len(), 2, "classes with results only");
        let h = r.iter().find(|r| r.class == "herbology").unwrap();
        assert!(h.in_window);
        assert_eq!((h.tick_from_secs, h.tick_to_secs), (405.0, 410.0));
        let p99 = h.tick_p99_ms.unwrap();
        assert!(p99 > 0.0 && p99 <= 5.0, "{p99}");
        let late = r.iter().find(|r| r.class == "late").unwrap();
        assert!(!late.in_window);
    }

    #[test]
    fn the_cup_summary_counts_the_window_and_shows_the_close() {
        let polls = |samples: Vec<(f64, f64)>, errors: u64| crate::cup::CupPolls {
            sent: samples.len() as u64 + errors,
            errors,
            error_reasons: if errors > 0 {
                BTreeMap::from([("status 500".to_owned(), errors)])
            } else {
                BTreeMap::new()
            },
            samples,
        };
        let c = Collected {
            scenario: Scenario::named("spread").unwrap(),
            config: RunConfig {
                url: "http://x:1".into(),
                bots: 2,
                ramp_secs: 0.0,
                duration_secs: 10.0,
                settle_secs: 0.0,
                chat_every_secs: 0.0,
                emote_every_secs: 0.0,
                look_every_secs: 0.0,
                npc_talk_every_secs: 0.0,
                dress: false,
                effects_pct: 0.0,
                class_pct: 0.0,
                duel_pct: 0.0,
                ping_every_secs: 5.0,
                view_limit: None,
                name_prefix: "lt".into(),
                containers: vec![],
                bot_threads: 1,
                cup_poll_secs: 60.0,
                cup_close: true,
                players_pct: 0.0,
                players_every_secs: 0.0,
                fly_pct: 0.0,
                stairs_period_secs: None,
                class_only: Vec::new(),
            },
            started_unix: 0,
            window: (10.0, 20.0),
            timeline: vec![sample(0.0, 0, 0), sample(20.0, 2, 0)],
            scrapes: vec![],
            docker: vec![],
            docker_errors: vec![],
            reports: vec![
                BotReport {
                    // One before the window: not counted.
                    cup: polls(vec![(5.0, 900.0), (11.0, 2.0), (12.0, 4.0)], 0),
                    ..BotReport::default()
                },
                BotReport {
                    cup: polls((0..20).map(|k| (13.0 + k as f64 * 0.1, 3.0)).collect(), 1),
                    ..BotReport::default()
                },
            ],
            cup_close: Some(crate::cup::CupClose {
                at_secs: 15.0,
                ms: 40.0,
                status: 200,
                closed: Some(true),
                winners: vec!["Skytalon".into()],
                mailed: Some(3),
                error: None,
            }),
        };
        let s = Summary::build(c);
        let cup = s.cup.as_ref().unwrap();
        assert_eq!(cup.polls, 22);
        assert_eq!(cup.polls_per_sec, 2.2);
        assert_eq!(cup.ms.max, 4.0);
        assert_eq!(cup.p95_ms, 3.0);
        assert_eq!((cup.sent, cup.errors), (24, 1));
        assert_eq!(cup.tick_p99_close_interval_ms, None, "no scrapes");
        let text = s.render();
        assert!(
            text.contains("GET /api/house-cup every 60 s: 22 in the window"),
            "{text}"
        );
        assert!(text.contains("dev close at 15 s: status 200"), "{text}");
    }
}
