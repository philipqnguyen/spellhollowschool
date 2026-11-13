//! SpellHollow School game server: one program per server (gateway, world,
//! instances, HTTP API). Configuration comes from the environment
//! (docs/configuration.md).
//!
//! `shs-server healthcheck` probes a running server's `/api/health` and exits
//! 0 or 1; the container image uses it because it ships without curl.
//!
//! `shs-server audit [NAME ...]` replays the item and Crown ledgers against
//! what the named characters (every character without names) hold now
//! (`ledger::audit::reconcile`, one read-only snapshot, safe while the game
//! runs), prints each problem and exits 1 if the ledgers do not reconcile.

use std::io::{Read, Write};
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, TcpStream};
use std::process::ExitCode;
use std::time::Duration;

use server::realtime::{self, chat::WordFilter};
use server::{AppState, Config, db, email, http, monitoring};

fn main() -> ExitCode {
    match std::env::args().nth(1).as_deref() {
        Some("healthcheck") => return healthcheck(),
        Some("audit") => return audit(std::env::args().skip(2).collect()),
        _ => {}
    }

    // Error tracking and performance (server::monitoring): off without
    // GOODSORCERY_API_KEY. Declared before the runtime, so it is dropped after
    // it: dropping the guard sends what is still queued.
    let _monitoring = goodsorcery::init(monitoring::config());
    monitoring::init_tracing();

    let config = match Config::from_env() {
        Ok(c) => c,
        Err(e) => {
            tracing::error!("{e}");
            goodsorcery::capture_error(&e);
            return ExitCode::FAILURE;
        }
    };

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("tokio runtime starts");
    match runtime.block_on(run(config)) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            tracing::error!("{e}");
            goodsorcery::capture_error(&*e);
            ExitCode::FAILURE
        }
    }
}

async fn run(config: Config) -> Result<(), Box<dyn std::error::Error>> {
    tracing::info!(
        bind = %config.bind,
        player_cap = config.player_cap,
        dev = config.dev,
        layout = %config.layout_path.display(),
        "starting shs-server"
    );

    let pool = connect_with_retry(&config.database_url).await?;
    db::spawn_janitor(pool.clone(), Duration::from_secs(3600));

    let world = realtime::load_world(&config.layout_path)?;
    tracing::info!(surfaces = world.surfaces().len(), "loaded layout");
    let filter = WordFilter::load_or_empty(&config.blocked_words_path);

    let mailer = email::from_config(&config)?;
    let bind = config.bind;
    let mut state = AppState::new(pool, config, mailer);
    state.start_realtime(world, filter);
    let realtime = state.realtime.clone().expect("realtime started");
    spawn_economy_jobs(state.pool.clone(), realtime.clone());
    let app = http::router(state);

    let listener = tokio::net::TcpListener::bind(bind).await?;
    tracing::info!("listening on http://{}", listener.local_addr()?);
    // On a signal, stop the world first: that disconnects every player (saving
    // their positions), which lets the WebSocket connections finish so the
    // graceful HTTP shutdown can complete.
    let stopping = realtime.clone();
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(async move {
        shutdown_signal().await;
        stopping.shutdown().await;
    })
    .await?;
    realtime.shutdown().await;
    tracing::info!("stopped");
    Ok(())
}

/// Background jobs, once a minute: Owl Exchange listings that
/// ran their five days go back to their sellers by owl, expired potion
/// effects and find cooldowns are deleted (the world expires effects in
/// memory on its own), and mail collected longer ago than
/// `mail.retention_days` is cleaned up (never uncollected items or Crowns).
/// Each run is one Good Sorcery job (`economy_jobs`): its errors carry that
/// name, and with performance monitoring its database time is recorded.
fn spawn_economy_jobs(pool: sqlx::PgPool, realtime: std::sync::Arc<realtime::Realtime>) {
    tokio::spawn(async move {
        let mut every = tokio::time::interval(Duration::from_secs(60));
        every.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            every.tick().await;
            let _ = goodsorcery::job("economy_jobs", None, economy_jobs(&pool, &realtime)).await;
        }
    });
}

/// One run of [`spawn_economy_jobs`]. A failed step is logged and reported
/// and the next one still runs; `Err` counts the failed steps (the run shows
/// as failed in performance monitoring).
async fn economy_jobs(pool: &sqlx::PgPool, realtime: &realtime::Realtime) -> Result<(), usize> {
    let mut failed = 0;
    let mut fail = |e: &dyn std::error::Error, what: &str| {
        tracing::error!(error = %e, "{what} failed");
        goodsorcery::capture_error(e);
        failed += 1;
    };
    match server::exchange::expire_listings(pool, Some(&realtime.world)).await {
        Ok(0) => {}
        Ok(n) => tracing::info!(listings = n, "expired Owl Exchange listings"),
        Err(e) => fail(&e, "expiring Exchange listings"),
    }
    if let Err(e) = server::effects::cleanup_expired(pool).await {
        fail(&e, "cleaning up expired effects");
    }
    let retention = server::catalog::shipped().economy.mail.retention();
    match server::mail::cleanup(pool, retention).await {
        Ok(c) => {
            if c.deleted > 0 {
                tracing::info!(mails = c.deleted, "cleaned up old mail");
            }
            for recipient in c.unread_changed {
                match server::mail::unread(pool, recipient).await {
                    Ok(n) => realtime.world.mail_changed(recipient, n),
                    Err(e) => {
                        tracing::warn!(error = %e, recipient, "counting unread mail failed")
                    }
                }
            }
        }
        Err(e) => fail(&e, "cleaning up old mail"),
    }
    // Daily caps of days before yesterday (UTC).
    let before = chrono::Utc::now().date_naive() - chrono::Days::new(1);
    if let Err(e) = server::progression::caps::cleanup(pool, before).await {
        fail(&e, "cleaning up daily caps");
    }
    // ---- The House Cup's weekly close.
    let now_ms = chrono::Utc::now().timestamp_millis();
    match server::house_cup::close_due(pool, now_ms).await {
        Ok(None) => {}
        Ok(Some(closed)) => {
            server::house_cup::forget_cached();
            tracing::info!(
                week_start_ms = closed.week.start_ms,
                winners = ?closed.winners,
                mailed = closed.mailed.len(),
                "closed the House Cup week"
            );
            for recipient in closed.mailed {
                match server::mail::unread(pool, recipient).await {
                    Ok(n) => realtime.world.mail_changed(recipient, n),
                    Err(e) => {
                        tracing::warn!(error = %e, recipient, "counting unread mail failed")
                    }
                }
            }
        }
        Err(e) => fail(&e, "closing the House Cup week"),
    }
    // ---- end of the weekly close.
    if failed == 0 { Ok(()) } else { Err(failed) }
}

/// The database may still be starting (compose, reboots): retry for about a minute.
async fn connect_with_retry(url: &str) -> Result<sqlx::PgPool, sqlx::Error> {
    let mut attempt = 1;
    loop {
        match db::connect(url).await {
            Ok(pool) => return Ok(pool),
            Err(e) if attempt < 30 && is_transient(&e) => {
                tracing::warn!(attempt, error = %e, "database not ready, retrying");
                tokio::time::sleep(Duration::from_secs(2)).await;
                attempt += 1;
            }
            Err(e) => return Err(e),
        }
    }
}

fn is_transient(e: &sqlx::Error) -> bool {
    matches!(e, sqlx::Error::Io(_) | sqlx::Error::PoolTimedOut)
        || matches!(e, sqlx::Error::Database(db) if db.code().as_deref() == Some("57P03"))
}

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut s) => {
                s.recv().await;
            }
            Err(_) => std::future::pending().await,
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! {
        () = ctrl_c => {},
        () = terminate => {},
    }
    tracing::info!("shutting down");
}

/// `shs-server audit [NAME ...]`: reconciles the ledgers (see the crate docs).
fn audit(names: Vec<String>) -> ExitCode {
    let config = match Config::from_env() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("audit: {e}");
            return ExitCode::FAILURE;
        }
    };
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio runtime starts");
    let run = async {
        let pool = db::connect(&config.database_url).await?;
        let ids: Vec<i64> = if names.is_empty() {
            sqlx::query_scalar("SELECT id FROM characters ORDER BY id")
                .fetch_all(&pool)
                .await?
        } else {
            let keys: Vec<String> = names
                .iter()
                .map(|n| server::characters::name_key(n))
                .collect();
            sqlx::query_scalar("SELECT id FROM characters WHERE name_key = ANY($1) ORDER BY id")
                .bind(&keys)
                .fetch_all(&pool)
                .await?
        };
        let report = server::ledger::audit::reconcile(&pool, &ids).await?;
        Ok::<_, sqlx::Error>((ids.len(), report))
    };
    match runtime.block_on(run) {
        Ok((characters, report)) => {
            for p in &report.problems {
                println!("{p}");
            }
            println!(
                "audit: {characters} characters, {} item rows replayed, {} XP rows checked, {} problems",
                report.items_checked,
                report.xp_rows_checked,
                report.problems.len()
            );
            if report.is_clean() {
                ExitCode::SUCCESS
            } else {
                ExitCode::FAILURE
            }
        }
        Err(e) => {
            eprintln!("audit: {e}");
            ExitCode::FAILURE
        }
    }
}

/// `GET /api/health` against the configured bind address over plain HTTP/1.0.
fn healthcheck() -> ExitCode {
    let Ok(config) = Config::from_env() else {
        return ExitCode::FAILURE;
    };
    let mut addr = config.bind;
    if addr.ip().is_unspecified() {
        addr.set_ip(match addr.ip() {
            IpAddr::V4(_) => IpAddr::V4(Ipv4Addr::LOCALHOST),
            IpAddr::V6(_) => IpAddr::V6(Ipv6Addr::LOCALHOST),
        });
    }
    let probe = || -> std::io::Result<bool> {
        let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(3))?;
        stream.set_read_timeout(Some(Duration::from_secs(3)))?;
        stream.write_all(b"GET /api/health HTTP/1.0\r\nHost: localhost\r\n\r\n")?;
        let mut head = [0u8; 12];
        stream.read_exact(&mut head)?;
        Ok(head.starts_with(b"HTTP/1.") && &head[8..12] == b" 200")
    };
    match probe() {
        Ok(true) => ExitCode::SUCCESS,
        _ => ExitCode::FAILURE,
    }
}
