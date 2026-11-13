//! Searching find spots on the world thread (seam:
//! `realtime::ext`). The world calls `on_search` when a character uses a
//! `find_spot` interactable in reach (it checked the reach and the house
//! gate, and took a token of the action bucket), `on_done` with the
//! [`FindDone`] of the search's job, and `tick` once a second.
//!
//! One search per character at a time: another `Interact` with a spot while
//! one is in flight is dropped (the first one answers). The cooldown itself
//! is the database's (`super::grant`), so spamming a spot, from one
//! connection or two, pays out once. Fortune's Draught, if active when the
//! search starts, lends its luck.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use protocol::{EffectKind, EntityId, FindOutcome, ServerMsg};

use crate::catalog::shipped;
use crate::realtime::ext::{ExtDone, WorldCtx};

/// A job that never came back frees its character after this long.
const JOB_TIMEOUT: Duration = Duration::from_secs(60);

/// What a search job hands back.
#[derive(Debug)]
pub struct FindDone {
    pub character_id: i64,
    pub spot: String,
    pub result: Result<super::Searched, String>,
}

/// Searches in flight (one per character at a time), by character id.
#[derive(Debug, Default)]
pub struct Finds {
    in_flight: HashMap<i64, Instant>,
}

impl Finds {
    pub fn on_search(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, spot: &str) {
        let Some(cid) = ctx.character_id(who) else {
            return;
        };
        if shipped().finds.spot(spot).is_none() {
            // A find_spot of a layout without a finds.json entry (tests).
            let msg = ServerMsg::FindResult {
                spot: spot.to_owned(),
                outcome: FindOutcome::Failed,
            };
            return ctx.send(who, &msg);
        }
        if self.in_flight.contains_key(&cid) {
            return;
        }
        self.in_flight.insert(cid, ctx.now());
        let luck = ctx
            .has_effect(who, EffectKind::FortunesDraught)
            .then(super::fortunes_draught_luck);
        // ---- Fortune: Lucky Find
        // (`effects.fortune.luck_*`) when there is no Fortune's Draught.
        let luck = luck.or_else(|| {
            ctx.effects(who)
                .iter()
                .any(|e| e.fortune == Some(protocol::FortuneKind::Luck))
                .then(|| {
                    let f = &shipped().economy.effects.fortune;
                    sim_core::Luck {
                        extra_rolls: f.luck_extra_rolls,
                        rare_weight_pct: f.luck_rare_weight_pct,
                    }
                })
        });
        // ---- end fortune.
        let spot = spot.to_owned();
        ctx.spawn(Box::new(move |pool| {
            Box::pin(async move {
                let result = super::search(&pool, cid, &spot, luck).await.map_err(|e| {
                    crate::monitoring::capture_for_character(&e, cid);
                    e.to_string()
                });
                ExtDone::Find(FindDone {
                    character_id: cid,
                    spot,
                    result,
                })
            })
        }));
    }

    pub fn on_done(&mut self, ctx: &mut WorldCtx<'_>, done: FindDone) {
        self.in_flight.remove(&done.character_id);
        let Some(e) = ctx.entity_of(done.character_id) else {
            return;
        };
        let (outcome, crowns, xp) = match done.result {
            Ok(s) => (s.outcome, s.crowns, s.xp),
            Err(error) => {
                tracing::error!(character = done.character_id, %error, "a find search failed");
                (FindOutcome::Failed, None, None)
            }
        };
        ctx.send(
            e,
            &ServerMsg::FindResult {
                spot: done.spot,
                outcome,
            },
        );
        if let Some(crowns) = crowns {
            ctx.send(e, &ServerMsg::Wallet { crowns });
        }
        // The XP the find gave.
        if let Some(grant) = xp {
            ctx.progress_changed(e, grant.xp, Some(grant.gain()));
        }
    }

    pub fn tick(&mut self, ctx: &mut WorldCtx<'_>) {
        let now = ctx.now();
        self.in_flight
            .retain(|_, at| now.saturating_duration_since(*at) < JOB_TIMEOUT);
    }
}
