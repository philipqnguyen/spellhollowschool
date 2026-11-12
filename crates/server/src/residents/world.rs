//! The residents on the world thread (seam:
//! `realtime::ext`). The world calls `on_input` with `ClientMsg::NpcInteract`,
//! `on_left` before a character leaves, and `tick` at the snapshot rate
//! (10 Hz).
//!
//! - **First tick**: the plans for the castle (`super::plan::plans_for`,
//!   once per process; a layout that is not the shipped castle plans
//!   nobody).
//! - **Every tick**: each entity NPC's pose at the server clock
//!   (`RoutinePlan::pose`: a binary search and a lerp). An NPC whose step
//!   hides it (a student in class or in bed, staff off duty) has no entity
//!   (`remove_npc`); one that appears is spawned into `CASTLE`
//!   (`spawn_npc`); one that moves or changes its anim is placed
//!   (`move_npc`, `Anim::Walk / Idle / Sit`). A standing NPC is not touched,
//!   so it costs viewers nothing after it enters their view. At most
//!   `rules.students_max` students are spawned at once (roster order).
//! - **Talk** (`on_input`): an NPC of the roster (not a professor, not a
//!   player) in the talker's zone within `rules.talk_reach_m`, then the
//!   shared action bucket. A ghost or staff member that gives to the
//!   talker's house claims today's gift in a job (`super::claim`) and
//!   answers `Gift`, or `Talk { given_today: true }` once given; everyone
//!   else answers `Talk` at once. The answer goes to the talker alone and
//!   carries no line (lines are the client's).

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use protocol::{Anim, EntityId, NpcErrorCode, NpcReply, QPos, ServerMsg};
use sim_core::npcs::{Activity, NpcKind, gift_day};

use super::plan::{Plans, plans_for};
use super::{Claimed, claim, gives_to};
use crate::catalog::shipped;
use crate::realtime::entities::CASTLE;
use crate::realtime::ext::{ExtDone, NpcInput, NpcSpawn, WorldCtx};

/// One resident's entity and what its viewers were last shown.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Shown {
    entity: EntityId,
    pos: QPos,
    /// The yaw as snapshots carry it (`u8` turns).
    yaw: u8,
    anim: Anim,
}

/// Gifts claimed today, by (character, roster index): the UTC day. A talk
/// the same day answers `given_today` without the database. Shared with the
/// claim jobs.
type Given = Arc<Mutex<HashMap<(i64, u16), i64>>>;

/// The residents' state (Default: not planned yet).
#[derive(Debug, Default)]
pub struct Residents {
    plans: Option<Arc<Plans>>,
    /// Parallel to `plans.npcs`.
    shown: Vec<Option<Shown>>,
    by_entity: HashMap<EntityId, u16>,
    given: Given,
}

fn yaw_u8(yaw: f32) -> u8 {
    let turns = yaw.rem_euclid(std::f32::consts::TAU) / std::f32::consts::TAU;
    ((turns * 256.0).round() as u32 % 256) as u8
}

fn anim_of(a: Activity) -> Anim {
    match a {
        Activity::Walk => Anim::Walk,
        Activity::Sit => Anim::Sit,
        Activity::Idle | Activity::Hidden => Anim::Idle,
    }
}

impl Residents {
    /// The plans this world runs (`None` before the first tick).
    pub fn plans(&self) -> Option<&Arc<Plans>> {
        self.plans.as_ref()
    }

    /// Every resident with an entity now: (roster id, entity).
    pub fn spawned(&self) -> Vec<(&str, EntityId)> {
        let Some(plans) = &self.plans else {
            return Vec::new();
        };
        plans
            .npcs
            .iter()
            .zip(&self.shown)
            .filter_map(|(p, s)| s.map(|s| (p.id.as_str(), s.entity)))
            .collect()
    }

    /// The roster id of resident entity `e`.
    pub fn resident(&self, e: EntityId) -> Option<&str> {
        let k = *self.by_entity.get(&e)?;
        Some(self.plans.as_ref()?.npcs[usize::from(k)].id.as_str())
    }

    /// `ClientMsg::NpcInteract`.
    pub fn on_input(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, input: NpcInput) {
        let NpcInput::Interact { id } = input;
        let reach = shipped().npcs.roster.rules.talk_reach_m;
        let refuse = |code| NpcReply::Refused { code };
        let Some(k) = self.by_entity.get(&id).copied() else {
            let reply = refuse(NpcErrorCode::UnknownNpc);
            return ctx.send(who, &ServerMsg::NpcReply { id, reply });
        };
        let Some(cid) = ctx.character_id(who) else {
            return;
        };
        if ctx.zone(id) != ctx.zone(who) || ctx.distance(who, id).is_none_or(|d| d > reach) {
            let reply = refuse(NpcErrorCode::TooFar);
            return ctx.send(who, &ServerMsg::NpcReply { id, reply });
        }
        if !ctx.take_action(who) {
            let reply = refuse(NpcErrorCode::RateLimited);
            return ctx.send(who, &ServerMsg::NpcReply { id, reply });
        }
        let Some(plans) = &self.plans else {
            return;
        };
        let npc = plans.npcs[usize::from(k)].id.clone();
        let house = ctx.house(who).unwrap_or(protocol::House::ALL[0]);
        if !gives_to(&npc, house) {
            let reply = NpcReply::Talk { given_today: false };
            return ctx.send(who, &ServerMsg::NpcReply { id, reply });
        }
        let unix_ms = ctx.unix_ms();
        let day = gift_day(unix_ms);
        let given = self.given.clone();
        let known = given
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&(cid, k))
            .is_some_and(|&d| d == day);
        if known {
            let reply = NpcReply::Talk { given_today: true };
            return ctx.send(who, &ServerMsg::NpcReply { id, reply });
        }
        ctx.spawn(Box::new(move |pool| {
            Box::pin(async move {
                let claimed = claim(&pool, cid, house, &npc, unix_ms).await;
                ExtDone::Run(Box::new(move |ctx: &mut WorldCtx<'_>| {
                    let reply = match claimed {
                        Ok(Claimed::Gift(gift)) => {
                            remember(&given, cid, k, day);
                            NpcReply::Gift { gift }
                        }
                        Ok(Claimed::AlreadyToday) => {
                            remember(&given, cid, k, day);
                            NpcReply::Talk { given_today: true }
                        }
                        Ok(Claimed::Nothing) => NpcReply::Talk { given_today: false },
                        Err(e) => {
                            tracing::warn!(npc, cid, "npc gift claim failed: {e}");
                            NpcReply::Refused {
                                code: NpcErrorCode::Failed,
                            }
                        }
                    };
                    // The talker may have left meanwhile: `send` drops it.
                    if ctx.entity_of(cid) == Some(who) {
                        ctx.send(who, &ServerMsg::NpcReply { id, reply });
                    }
                }))
            })
        }));
    }

    /// Character `who` is leaving the world: forget its gifts of the day
    /// (a later talk asks the database again).
    pub fn on_left(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId) {
        if let Some(cid) = ctx.character_id(who) {
            self.given
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .retain(|&(c, _), _| c != cid);
        }
    }

    /// At the snapshot rate: every resident where its plan says.
    pub fn tick(&mut self, ctx: &mut WorldCtx<'_>) {
        let plans = match &self.plans {
            Some(p) => p.clone(),
            None => {
                let p = plans_for(ctx.castle_layout());
                self.shown = vec![None; p.npcs.len()];
                self.plans = Some(p.clone());
                p
            }
        };
        if plans.npcs.is_empty() {
            return;
        }
        let unix_ms = ctx.unix_ms();
        let students_max = usize::from(shipped().npcs.roster.rules.students_max);
        let mut students = 0usize;
        for (k, p) in plans.npcs.iter().enumerate() {
            let pose = p.plan.pose(unix_ms);
            let mut out = pose.activity == Activity::Hidden;
            if !out && p.kind == NpcKind::Student {
                students += 1;
                out = students > students_max;
            }
            let mut shown = self.shown[k];
            // An entity something else removed (a world shutdown) is gone.
            if let Some(s) = shown
                && ctx.position(s.entity).is_none()
            {
                self.by_entity.remove(&s.entity);
                shown = None;
            }
            if out {
                if let Some(s) = shown {
                    ctx.remove_npc(s.entity);
                    self.by_entity.remove(&s.entity);
                }
                self.shown[k] = None;
                continue;
            }
            let (pos, yaw, anim) = (
                QPos::from_m(pose.pos),
                yaw_u8(pose.yaw),
                anim_of(pose.activity),
            );
            match shown {
                Some(s) if s.pos == pos && s.yaw == yaw && s.anim == anim => {}
                Some(s) => {
                    ctx.move_npc(s.entity, pose.pos, pose.yaw, anim);
                    self.shown[k] = Some(Shown {
                        pos,
                        yaw,
                        anim,
                        ..s
                    });
                }
                None => {
                    let spawn = NpcSpawn {
                        npc: p.id.clone(),
                        name: p.name.clone(),
                        house: p.house,
                        appearance: p.appearance,
                        pos: pose.pos,
                        yaw: pose.yaw,
                    };
                    let Some(entity) = ctx.spawn_npc(CASTLE, spawn) else {
                        self.shown[k] = None;
                        continue;
                    };
                    ctx.move_npc(entity, pose.pos, pose.yaw, anim);
                    self.by_entity.insert(entity, k as u16);
                    self.shown[k] = Some(Shown {
                        entity,
                        pos,
                        yaw,
                        anim,
                    });
                }
            }
        }
    }
}

fn remember(given: &Given, cid: i64, k: u16, day: i64) {
    let mut g = given.lock().unwrap_or_else(|e| e.into_inner());
    // Yesterday's entries are of no use.
    g.retain(|_, d| *d >= day);
    g.insert((cid, k), day);
}
