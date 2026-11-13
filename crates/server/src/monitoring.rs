//! Error tracking and performance monitoring with Good Sorcery
//! (docs/configuration.md, "Error tracking and performance"): the client's
//! settings, the filter its tracing layer gets, and the capture helpers the
//! game uses for failures that are ours, not the player's.
//!
//! `shs-server` starts the client in `main` from the `GOODSORCERY_*`
//! variables. Without `GOODSORCERY_API_KEY` it stays off: nothing is captured
//! and every call here returns at once, so development, the tests and CI send
//! nothing.

use goodsorcery::Options;
use goodsorcery::performance::Dialect;

/// Requests never timed: the proxy's health check (every two seconds) and
/// the metrics scrape.
pub const UNTIMED: [&str; 2] = ["GET /api/health", "GET /metrics"];

/// The crates whose stack frames are the game's own code (the binary is
/// `shs_server`, the library `server`).
pub const IN_APP: [&str; 4] = ["shs_server", "server", "sim_core", "protocol"];

/// What the Good Sorcery tracing layer sees, filtered on its own so the log
/// output (`RUST_LOG`) stays as it is: `INFO` and up become breadcrumbs,
/// sqlx's `DEBUG` event for each statement times the database inside a
/// request or job, and `goodsorcery=trace` keeps the span it opens per
/// request. Every other `DEBUG` and `TRACE` call site stays disabled.
pub const TRACING_FILTER: &str = "info,sqlx::query=debug,goodsorcery=trace";

/// Where the client looks for the release, first one set wins (the crate's
/// own list). Kamal sets `KAMAL_VERSION` (the deployed commit) in the
/// container.
const RELEASE_VARS: [&str; 8] = [
    "GOODSORCERY_RELEASE",
    "KAMAL_VERSION",
    "HEROKU_SLUG_COMMIT",
    "HEROKU_BUILD_COMMIT",
    "RENDER_GIT_COMMIT",
    "GIT_COMMIT",
    "GIT_SHA",
    "SOURCE_VERSION",
];

/// The client's configuration: the `GOODSORCERY_*` variables
/// (`GOODSORCERY_PERFORMANCE_MONITORING` turns timing on) plus the game's
/// fixed choices.
pub fn config() -> goodsorcery::Config {
    let config = goodsorcery::Config::from_env()
        .sql_dialect(Dialect::Postgres)
        .in_app_include(IN_APP)
        .performance_ignore_transactions(UNTIMED);
    let release_set = RELEASE_VARS
        .iter()
        .any(|name| std::env::var(name).is_ok_and(|v| !v.trim().is_empty()));
    if release_set {
        config
    } else {
        // Docker Compose and local runs: at least the server's version.
        config.release(concat!("shs-server@", env!("CARGO_PKG_VERSION")))
    }
}

/// Installs the tracing subscriber. The log output is `RUST_LOG` (default
/// `info`), in colour only on a terminal so container logs stay plain text.
/// When the client is reporting (call [`goodsorcery::init`] first), the Good
/// Sorcery layer also gets what [`TRACING_FILTER`] lets through. Each layer
/// has its own filter, so the log shows nothing more than `RUST_LOG` asks for.
pub fn init_tracing() {
    use std::io::IsTerminal;
    use tracing_subscriber::EnvFilter;
    use tracing_subscriber::prelude::*;

    let log = tracing_subscriber::fmt::layer()
        .with_ansi(std::io::stdout().is_terminal())
        .with_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")));
    let reports = goodsorcery::is_enabled()
        .then(|| goodsorcery::tracing::layer().with_filter(EnvFilter::new(TRACING_FILTER)));
    tracing_subscriber::registry()
        .with(log)
        .with(reports)
        .init();
}

/// Reports a failure of a character's game action (the world, its jobs,
/// the WebSocket gateway), tagged with the character. Only for failures
/// that are ours (the database, a broken invariant), never for a refusal
/// the player caused.
#[track_caller]
pub fn capture_for_character<E: std::error::Error + ?Sized>(error: &E, character_id: i64) {
    goodsorcery::capture_error_with(error, Options::new().tag("character_id", character_id));
}
