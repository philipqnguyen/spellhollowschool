//! Postgres pool, embedded migrations and periodic cleanup.

use std::time::Duration;

use sqlx::PgPool;
use sqlx::migrate::Migrator;
use sqlx::postgres::PgPoolOptions;

/// The migrations in `db/migrations`, compiled into the binary.
pub static MIGRATOR: Migrator = sqlx::migrate!("../../db/migrations");

/// Pool size: the HTTP API and the world's periodic saves share it. Postgres on
/// the same 2 vCPU box gains nothing from more concurrent connections.
const MAX_CONNECTIONS: u32 = 16;

/// Connects and applies any pending migrations. Concurrent callers (tests, a
/// restarting server) are safe: sqlx serializes migrations with an advisory lock.
pub async fn connect(database_url: &str) -> Result<PgPool, sqlx::Error> {
    connect_with(database_url, MAX_CONNECTIONS).await
}

pub async fn connect_with(database_url: &str, max_connections: u32) -> Result<PgPool, sqlx::Error> {
    let pool = PgPoolOptions::new()
        .max_connections(max_connections)
        .acquire_timeout(Duration::from_secs(5))
        .connect(database_url)
        .await?;
    MIGRATOR.run(&pool).await?;
    Ok(pool)
}

/// Deletes expired sessions, verification tokens and password reset tokens.
pub async fn delete_expired(pool: &PgPool) -> Result<u64, sqlx::Error> {
    let sessions = sqlx::query("DELETE FROM sessions WHERE expires_at <= now()")
        .execute(pool)
        .await?
        .rows_affected();
    let verifications = sqlx::query("DELETE FROM email_verifications WHERE expires_at <= now()")
        .execute(pool)
        .await?
        .rows_affected();
    let resets = sqlx::query("DELETE FROM password_resets WHERE expires_at <= now()")
        .execute(pool)
        .await?
        .rows_affected();
    Ok(sessions + verifications + resets)
}

/// Runs [`delete_expired`] every `every` until the process exits.
pub fn spawn_janitor(pool: PgPool, every: Duration) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(every);
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            interval.tick().await;
            match delete_expired(&pool).await {
                Ok(0) => {}
                Ok(n) => tracing::debug!(rows = n, "deleted expired tokens"),
                Err(e) => tracing::warn!(error = %e, "token cleanup failed"),
            }
        }
    })
}

/// True if `err` is a unique violation of the named constraint or index.
pub fn is_unique_violation(err: &sqlx::Error, constraint: &str) -> bool {
    match err {
        sqlx::Error::Database(db) => {
            db.is_unique_violation() && db.constraint() == Some(constraint)
        }
        _ => false,
    }
}
