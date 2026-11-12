//! Saves character positions off the tick thread.
//!
//! The world sends batches of `SavedPosition`s (character, zone, position) (every 30 s for
//! everyone who moved, and one entry whenever a player leaves). This task
//! coalesces whatever has queued up, keeps only the latest position per
//! character, and writes it in one `UPDATE ... FROM unnest(...)` statement.
//!
//! The same batches carry the map's new reveals (`crate::map`),
//! written after the positions in one `INSERT ... ON CONFLICT DO NOTHING`
//! statement, so a retried batch changes nothing.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;

use sqlx::PgPool;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use super::metrics::RealtimeMetrics;
use crate::characters::{self, SavedPosition};
use crate::map::Reveal;

/// What the world hands the saver at once.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct SaveBatch {
    pub positions: Vec<SavedPosition>,
    /// Surfaces revealed since the last save.
    pub reveals: Vec<Reveal>,
}

impl SaveBatch {
    pub fn new() -> Self {
        Self::default()
    }

    /// One resume point (a player leaving or changing zone).
    pub fn position(p: SavedPosition) -> Self {
        Self {
            positions: vec![p],
            reveals: Vec::new(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.positions.is_empty() && self.reveals.is_empty()
    }
}

const ATTEMPTS: u32 = 3;

/// Starts the saver. It runs until every sender is dropped, then flushes and ends.
pub fn spawn(
    pool: PgPool,
    metrics: Arc<RealtimeMetrics>,
) -> (mpsc::UnboundedSender<SaveBatch>, JoinHandle<()>) {
    let (tx, rx) = mpsc::unbounded_channel();
    (tx, tokio::spawn(run(pool, rx, metrics)))
}

async fn run(
    pool: PgPool,
    mut rx: mpsc::UnboundedReceiver<SaveBatch>,
    metrics: Arc<RealtimeMetrics>,
) {
    let mut latest: HashMap<i64, SavedPosition> = HashMap::new();
    let mut revealed: HashSet<Reveal> = HashSet::new();
    let mut rows: Vec<SavedPosition> = Vec::new();
    let mut reveals: Vec<Reveal> = Vec::new();
    while let Some(batch) = rx.recv().await {
        latest.extend(batch.positions.into_iter().map(|p| (p.character_id, p)));
        revealed.extend(batch.reveals);
        while let Ok(more) = rx.try_recv() {
            latest.extend(more.positions.into_iter().map(|p| (p.character_id, p)));
            revealed.extend(more.reveals);
        }
        rows.clear();
        rows.extend(latest.drain().map(|(_, p)| p));
        reveals.clear();
        reveals.extend(revealed.drain());
        let started = std::time::Instant::now();
        for attempt in 1..=ATTEMPTS {
            let saved = async {
                characters::save_positions(&pool, &rows).await?;
                crate::map::save_reveals(&pool, &reveals).await
            };
            match saved.await {
                Ok(new) => {
                    metrics.positions_saved.add(rows.len() as u64);
                    metrics.reveals_saved.add(new);
                    metrics
                        .save_seconds
                        .observe(started.elapsed().as_secs_f64());
                    break;
                }
                Err(e) if attempt < ATTEMPTS => {
                    tracing::warn!(error = %e, attempt, rows = rows.len(), reveals = reveals.len(), "saving positions failed, retrying");
                    tokio::time::sleep(Duration::from_millis(500 * u64::from(attempt))).await;
                }
                Err(e) => {
                    metrics.position_save_errors.inc();
                    tracing::error!(error = %e, rows = rows.len(), reveals = reveals.len(), "saving positions failed, giving up on this batch");
                }
            }
        }
    }
}
