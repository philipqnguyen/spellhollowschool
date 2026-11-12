//! Database work the world thread hands off: it never blocks on Postgres.
//!
//! The world pushes [`Job`]s into an outbox while it handles commands; after
//! each command and tick the outbox goes to the [`Executor`], which runs every
//! job on a tokio task and reports the result back as a [`WorldCmd`]. Without
//! an executor (unit tests) jobs stay in the outbox for the test to complete.
//!
//! Each dorm room's operations (load, edits, the open flag) are serialized by
//! the world (`instances::RoomQueue`), so a room never has two in flight and a
//! load always sees every edit queued before it.

use protocol::{DormErrorCode, FurnitureEdit, PlacedFurniture, QPos};
use sim_core::PlaceArea;
use sqlx::PgPool;
use tokio::sync::mpsc;

use super::entities::Social;
use super::world::WorldCmd;
use crate::catalog;
use crate::dorm::rules;
use crate::dorm::store::{self, StoreError};

/// One piece of database work.
#[derive(Debug, Clone, PartialEq)]
pub enum Job {
    /// Every item placed in `owner`'s room.
    LoadRoom { owner: i64 },
    /// One furniture edit, already checked by the world where it can be.
    Edit { owner: i64, edit: Edit },
    /// Stores the room's open-to-friends flag.
    SetOpen { owner: i64, open: bool },
    /// Friends and blocks of a character (`seq` names the reload).
    LoadSocial { character_id: i64, seq: u64 },
}

#[derive(Debug, Clone, PartialEq)]
pub enum Edit {
    /// `areas`: the room's placeable areas when the edit started; `way`: what
    /// it must leave open (solid furniture, `rules::Way`).
    Place {
        item_id: i64,
        pos: QPos,
        yaw: u8,
        areas: Vec<PlaceArea>,
        way: rules::Way,
    },
    /// Already checked against the room.
    Move {
        item_id: i64,
        pos: QPos,
        yaw: u8,
    },
    Remove {
        item_id: i64,
    },
}

/// How a room operation ended (`WorldCmd::RoomDone`).
#[derive(Debug, Clone, PartialEq)]
pub enum RoomOutcome {
    /// `None` if the load failed (logged); the room then shows empty.
    Loaded(Option<Vec<PlacedFurniture>>),
    /// The stored edit, or why it was refused (and for which item).
    Edited(Result<FurnitureEdit, (DormErrorCode, i64)>),
    OpenSaved,
}

/// Runs jobs on the tokio runtime against Postgres.
#[derive(Clone)]
pub struct Executor {
    pool: PgPool,
    runtime: tokio::runtime::Handle,
    /// Weak, so the world's own executor never keeps its command channel open.
    back: mpsc::WeakSender<WorldCmd>,
}

impl Executor {
    pub fn new(
        pool: PgPool,
        runtime: tokio::runtime::Handle,
        back: mpsc::WeakSender<WorldCmd>,
    ) -> Self {
        Self {
            pool,
            runtime,
            back,
        }
    }

    pub fn run(&self, job: Job) {
        let pool = self.pool.clone();
        let back = self.back.clone();
        self.runtime.spawn(async move {
            let cmd = execute(&pool, job).await;
            if let Some(tx) = back.upgrade() {
                let _ = tx.send(cmd).await;
            }
        });
    }

    /// Runs a feature's job (`ext`) and hands its result back
    /// to the world.
    pub fn run_ext(&self, job: super::ext::ExtJob) {
        let pool = self.pool.clone();
        let back = self.back.clone();
        self.runtime.spawn(async move {
            let done = job(pool).await;
            if let Some(tx) = back.upgrade() {
                let _ = tx.send(WorldCmd::ExtDone(done)).await;
            }
        });
    }
}

/// Runs one job and phrases its result as the command the world expects.
async fn execute(pool: &PgPool, job: Job) -> WorldCmd {
    match job {
        Job::LoadRoom { owner } => {
            let items = match store::load_room(pool, owner).await {
                Ok(items) => Some(items),
                Err(e) => {
                    tracing::error!(owner, error = %e, "loading a dorm room failed");
                    None
                }
            };
            WorldCmd::RoomDone {
                owner,
                outcome: RoomOutcome::Loaded(items),
            }
        }
        Job::SetOpen { owner, open } => {
            if let Err(e) = store::set_open(pool, owner, open).await {
                tracing::error!(owner, error = %e, "saving a dorm's open flag failed");
            }
            WorldCmd::RoomDone {
                owner,
                outcome: RoomOutcome::OpenSaved,
            }
        }
        Job::Edit { owner, edit } => {
            let outcome = RoomOutcome::Edited(run_edit(pool, owner, edit).await);
            WorldCmd::RoomDone { owner, outcome }
        }
        Job::LoadSocial { character_id, seq } => WorldCmd::SocialLoaded {
            character_id,
            seq,
            social: load_social(pool, character_id).await.ok(),
        },
    }
}

async fn run_edit(
    pool: &PgPool,
    owner: i64,
    edit: Edit,
) -> Result<FurnitureEdit, (DormErrorCode, i64)> {
    let catalog = &catalog::shipped().catalog;
    let (item, res) = match edit {
        Edit::Place {
            item_id,
            pos,
            yaw,
            areas,
            way,
        } => (
            item_id,
            store::place(pool, catalog, owner, item_id, pos, yaw, &areas, &way)
                .await
                .map(FurnitureEdit::Placed),
        ),
        Edit::Move { item_id, pos, yaw } => (
            item_id,
            store::move_item(pool, owner, item_id, pos, yaw)
                .await
                .map(|()| FurnitureEdit::Moved { item_id, pos, yaw }),
        ),
        Edit::Remove { item_id } => (
            item_id,
            store::remove(pool, owner, item_id)
                .await
                .map(|()| FurnitureEdit::Removed { item_id }),
        ),
    };
    res.map_err(|e| {
        if let StoreError::Db(db) = &e {
            tracing::error!(owner, item, error = %db, "dorm edit failed");
        }
        (e.code(), item)
    })
}

/// A character's friends and blocks, as the world keeps them (sorted).
pub async fn load_social(pool: &PgPool, character_id: i64) -> Result<Social, sqlx::Error> {
    let friends = crate::friends::friend_ids(pool, character_id).await?;
    let blocks = crate::friends::block_lists(pool, character_id).await?;
    Ok(Social {
        friends,
        blocked: blocks.blocked,
        blocked_by: blocks.blocked_by,
    }
    .normalized())
}
