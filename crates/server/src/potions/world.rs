//! Drinking and Mimic Draught consent on the world thread
//! (seam: `realtime::ext`). The world owns a [`Potions`] and calls:
//!
//! - `on_input` for `ClientMsg::Drink`, `MimicDraughtAsk`, `MimicDraughtAnswer`
//!   (as `PotionInput`);
//! - `on_done` with the [`PotionDone`] of a job it spawned (a drink's
//!   transaction, or the check before a consent request);
//! - `on_left` / `on_zone_changed`: drop consent requests involving them;
//! - `tick` once a second: expire consent requests (`MIMIC_DRAUGHT_REQUEST_SECS`).
//!
//! Rules: one potion job per character at a time (`Busy`); every action
//! spends a token of the shared action bucket (`RateLimited`). `Drink` of a
//! Mimic Draught answers `PotionError { NeedsTarget }`; `MimicDraughtAsk` needs an
//! online friend (no block either way) in the same zone within
//! `EffectRules.mimic_draught.range_m`, and a Mimic Draught in the asker's
//! inventory (checked in a job before the friend is asked), then the friend
//! gets `MimicDraughtRequest` (`ctx.notice`); declining sends the asker
//! `MimicDraughtDeclined`; accepting re-checks friendship and range, then
//! drinks the potion with the friend's own current look (never a disguise
//! they wear) as the disguise. A drink that succeeds answers `Drank`, then
//! the world's `OwnEffects`. The disguise copies the look only: the
//! nameplate and chat keep the drinker's name.
//!
//! State is keyed by character id, so a reconnect (a new entity) finds its
//! job's result.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use protocol::{EntityId, MIMIC_DRAUGHT_REQUEST_SECS, PotionErrorCode, ServerMsg};

use super::Drunk;
use crate::catalog::shipped;
use crate::realtime::ext::{ExtDone, PotionInput, WorldCtx};

/// A job that never came back (a dropped connection to the database) frees
/// its character after this long.
const JOB_TIMEOUT: Duration = Duration::from_secs(60);
/// Consent requests waiting on one friend at a time, at most.
const REQUESTS_PER_TARGET_MAX: usize = 4;

/// What a potion job hands back to the world thread.
#[derive(Debug)]
pub struct PotionDone {
    pub character_id: i64,
    pub item_id: i64,
    pub result: JobResult,
}

#[derive(Debug)]
pub enum JobResult {
    /// The drink's transaction.
    Drank(Result<Drunk, PotionErrorCode>),
    /// The check before asking `target` (a character id) for consent.
    Checked {
        target: i64,
        result: Result<(), PotionErrorCode>,
    },
}

/// A Mimic Draught consent request waiting for the friend's answer.
#[derive(Debug, Clone, PartialEq)]
struct Request {
    id: u32,
    /// Character ids.
    asker: i64,
    target: i64,
    item_id: i64,
    at: Instant,
}

/// Potion jobs in flight and pending Mimic Draught consent requests.
#[derive(Debug, Default)]
pub struct Potions {
    /// Character id → when its job started.
    busy: HashMap<i64, Instant>,
    requests: Vec<Request>,
    next_id: u32,
}

fn error(ctx: &mut WorldCtx<'_>, e: EntityId, code: PotionErrorCode, item_id: Option<i64>) {
    ctx.send(e, &ServerMsg::PotionError { code, item_id });
}

impl Potions {
    pub fn on_input(&mut self, ctx: &mut WorldCtx<'_>, from: EntityId, input: PotionInput) {
        let item_id = match input {
            PotionInput::Drink { item_id } | PotionInput::MimicDraughtAsk { item_id, .. } => {
                Some(item_id)
            }
            PotionInput::MimicDraughtAnswer { .. } => None,
        };
        let Some(cid) = ctx.character_id(from) else {
            return;
        };
        if !ctx.take_action(from) {
            return error(ctx, from, PotionErrorCode::RateLimited, item_id);
        }
        match input {
            PotionInput::Drink { item_id } => {
                if self.busy.contains_key(&cid) {
                    return error(ctx, from, PotionErrorCode::Busy, Some(item_id));
                }
                self.spawn_drink(ctx, cid, item_id, None);
            }
            PotionInput::MimicDraughtAsk { item_id, target } => {
                self.ask(ctx, from, cid, item_id, target);
            }
            PotionInput::MimicDraughtAnswer { request_id, accept } => {
                self.answer(ctx, from, cid, request_id, accept);
            }
        }
    }

    /// A job in flight, or a consent request still waiting (one ask at a time).
    fn is_busy(&self, cid: i64) -> bool {
        self.busy.contains_key(&cid) || self.requests.iter().any(|r| r.asker == cid)
    }

    fn spawn_drink(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        cid: i64,
        item_id: i64,
        disguise: Option<protocol::Appearance>,
    ) {
        self.busy.insert(cid, ctx.now());
        ctx.spawn(Box::new(move |pool| {
            Box::pin(async move {
                let result = super::drink(&pool, cid, item_id, disguise).await;
                ExtDone::Potion(PotionDone {
                    character_id: cid,
                    item_id,
                    result: JobResult::Drank(result),
                })
            })
        }));
    }

    /// Whether `a` may ask `b` (both entities): friends, same zone, in range.
    fn reachable(ctx: &WorldCtx<'_>, a: EntityId, b: EntityId) -> Result<(), PotionErrorCode> {
        if a == b || ctx.character_id(b).is_none() {
            return Err(if a == b {
                PotionErrorCode::NotFriend
            } else {
                PotionErrorCode::NotOnline
            });
        }
        if !ctx.friends(a, b) {
            return Err(PotionErrorCode::NotFriend);
        }
        let range = shipped().economy.effects.mimic_draught.range_m;
        match ctx.distance(a, b) {
            Some(d) if d <= range => Ok(()),
            _ => Err(PotionErrorCode::TooFar),
        }
    }

    fn ask(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        from: EntityId,
        cid: i64,
        item_id: i64,
        target: EntityId,
    ) {
        if self.is_busy(cid) {
            return error(ctx, from, PotionErrorCode::Busy, Some(item_id));
        }
        if let Err(code) = Self::reachable(ctx, from, target) {
            return error(ctx, from, code, Some(item_id));
        }
        let Some(target_cid) = ctx.character_id(target) else {
            return error(ctx, from, PotionErrorCode::NotOnline, Some(item_id));
        };
        if self
            .requests
            .iter()
            .filter(|r| r.target == target_cid)
            .count()
            >= REQUESTS_PER_TARGET_MAX
        {
            return error(ctx, from, PotionErrorCode::Busy, Some(item_id));
        }
        self.busy.insert(cid, ctx.now());
        ctx.spawn(Box::new(move |pool| {
            Box::pin(async move {
                let result = super::check(&pool, cid, item_id, true).await;
                ExtDone::Potion(PotionDone {
                    character_id: cid,
                    item_id,
                    result: JobResult::Checked {
                        target: target_cid,
                        result,
                    },
                })
            })
        }));
    }

    fn answer(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        from: EntityId,
        cid: i64,
        request_id: u32,
        accept: bool,
    ) {
        let Some(pos) = self
            .requests
            .iter()
            .position(|r| r.id == request_id && r.target == cid)
        else {
            return error(ctx, from, PotionErrorCode::Expired, None);
        };
        let req = self.requests.swap_remove(pos);
        let Some(asker) = ctx.entity_of(req.asker) else {
            return error(ctx, from, PotionErrorCode::Expired, None);
        };
        if !accept {
            let by_name = ctx.name(from).unwrap_or_default().to_owned();
            ctx.notice(asker, &ServerMsg::MimicDraughtDeclined { by_name });
            return;
        }
        if let Err(code) = Self::reachable(ctx, asker, from) {
            return error(ctx, asker, code, Some(req.item_id));
        }
        if self.busy.contains_key(&req.asker) {
            return error(ctx, asker, PotionErrorCode::Busy, Some(req.item_id));
        }
        // The friend's own look as they consented to it, never a disguise
        // they are wearing themselves.
        let Some(look) = ctx.appearance(from) else {
            return error(ctx, asker, PotionErrorCode::NotOnline, Some(req.item_id));
        };
        self.spawn_drink(ctx, req.asker, req.item_id, Some(look));
    }

    pub fn on_done(&mut self, ctx: &mut WorldCtx<'_>, done: PotionDone) {
        self.busy.remove(&done.character_id);
        let me = ctx.entity_of(done.character_id);
        match done.result {
            JobResult::Drank(Ok(drunk)) => {
                // Offline now: the effect row is stored and applies at the
                // next sign-in.
                let Some(e) = me else { return };
                let kind = drunk.effect.kind;
                ctx.send(
                    e,
                    &ServerMsg::Drank {
                        item_id: done.item_id,
                        effect: kind,
                    },
                );
                for other in drunk.ended {
                    ctx.remove_effect(e, other);
                }
                ctx.apply_effect(e, drunk.effect);
            }
            JobResult::Drank(Err(code)) => {
                if let Some(e) = me {
                    error(ctx, e, code, Some(done.item_id));
                }
            }
            JobResult::Checked { target, result } => {
                let Some(e) = me else { return };
                if let Err(code) = result {
                    return error(ctx, e, code, Some(done.item_id));
                }
                let Some(t) = ctx.entity_of(target) else {
                    return error(ctx, e, PotionErrorCode::NotOnline, Some(done.item_id));
                };
                if let Err(code) = Self::reachable(ctx, e, t) {
                    return error(ctx, e, code, Some(done.item_id));
                }
                self.next_id = self.next_id.wrapping_add(1).max(1);
                let id = self.next_id;
                self.requests.push(Request {
                    id,
                    asker: done.character_id,
                    target,
                    item_id: done.item_id,
                    at: ctx.now(),
                });
                let from_name = ctx.name(e).unwrap_or_default().to_owned();
                ctx.notice(
                    t,
                    &ServerMsg::MimicDraughtRequest {
                        request_id: id,
                        from: e,
                        from_name,
                        expires_in_secs: MIMIC_DRAUGHT_REQUEST_SECS,
                    },
                );
            }
        }
    }

    /// Drops the requests involving entity `id`; an asker whose friend went
    /// away hears `code`.
    fn drop_involving(&mut self, ctx: &mut WorldCtx<'_>, id: EntityId, code: PotionErrorCode) {
        let Some(cid) = ctx.character_id(id) else {
            return;
        };
        let (gone, kept): (Vec<Request>, Vec<Request>) = std::mem::take(&mut self.requests)
            .into_iter()
            .partition(|r| r.asker == cid || r.target == cid);
        self.requests = kept;
        for r in gone.into_iter().filter(|r| r.target == cid) {
            if let Some(asker) = ctx.entity_of(r.asker) {
                error(ctx, asker, code, Some(r.item_id));
            }
        }
    }

    pub fn on_left(&mut self, ctx: &mut WorldCtx<'_>, id: EntityId) {
        self.drop_involving(ctx, id, PotionErrorCode::NotOnline);
    }

    pub fn on_zone_changed(&mut self, ctx: &mut WorldCtx<'_>, id: EntityId) {
        self.drop_involving(ctx, id, PotionErrorCode::TooFar);
    }

    pub fn tick(&mut self, ctx: &mut WorldCtx<'_>) {
        let now = ctx.now();
        let ttl = Duration::from_secs(u64::from(MIMIC_DRAUGHT_REQUEST_SECS));
        let (expired, kept): (Vec<Request>, Vec<Request>) = std::mem::take(&mut self.requests)
            .into_iter()
            .partition(|r| now.saturating_duration_since(r.at) >= ttl);
        self.requests = kept;
        for r in expired {
            if let Some(asker) = ctx.entity_of(r.asker) {
                error(ctx, asker, PotionErrorCode::Expired, Some(r.item_id));
            }
        }
        self.busy
            .retain(|_, at| now.saturating_duration_since(*at) < JOB_TIMEOUT);
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    use protocol::{
        Appearance, EffectKind, House, LookCache, ServerMsg, SnapshotReceiver, VisibleEffect,
    };
    use tokio::sync::mpsc;

    use super::*;
    use crate::characters::Character;
    use crate::realtime::ConnControl;
    use crate::realtime::entities::Social;
    use crate::realtime::ext::{ExtDone, PotionInput};
    use crate::realtime::world::tests::{TestClient, character, sim};
    use crate::realtime::world::{Input, Joining, WorldSim};

    /// Joins `ch` with `friends` (character ids) as its friend list.
    fn join_with_friends(s: &mut WorldSim, ch: Character, friends: Vec<i64>) -> TestClient {
        let (tx, rx) = mpsc::channel(256);
        let control = Arc::new(ConnControl::default());
        let cid = ch.id;
        let joining = Joining {
            social: Social {
                friends,
                ..Social::default()
            },
            ..Joining::alone(ch)
        };
        let id = s
            .join(joining, tx, control.clone(), Instant::now())
            .unwrap();
        TestClient {
            id,
            cid,
            rx,
            control,
            snaps: SnapshotReceiver::new(),
            looks: LookCache::new(),
        }
    }

    /// Two friends standing `apart` metres from each other; Bea wears a
    /// look of her own.
    fn friends(s: &mut WorldSim, apart: f32) -> (TestClient, TestClient) {
        let ada = character("Ada", House::Skytalon, Some([100.0, 0.0, 100.0]));
        let mut bea = character("Bea", House::Serpentyne, Some([100.0 + apart, 0.0, 100.0]));
        bea.appearance.hair_colour = 7;
        bea.appearance.height = 200;
        let (a_id, b_id) = (ada.id, bea.id);
        let mut a = join_with_friends(s, ada, vec![b_id]);
        let mut b = join_with_friends(s, bea, vec![a_id]);
        // A few snapshots, so each has the other in view.
        for _ in 0..4 {
            s.tick(Instant::now());
        }
        a.drain();
        b.drain();
        (a, b)
    }

    fn potion(s: &mut WorldSim, c: &TestClient, input: PotionInput) {
        s.input(c.id, Instant::now(), Input::Potion(input));
    }

    fn errors(c: &mut TestClient) -> Vec<PotionErrorCode> {
        c.drain()
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::PotionError { code, .. } => Some(code),
                _ => None,
            })
            .collect()
    }

    fn done(s: &mut WorldSim, c: &TestClient, item_id: i64, result: JobResult) {
        let done = PotionDone {
            character_id: c.cid,
            item_id,
            result,
        };
        s.ext_done(ExtDone::Potion(done), Instant::now());
    }

    fn glow(colour: u8) -> Drunk {
        let def = crate::catalog::shipped()
            .catalog
            .items
            .iter()
            .find(|i| i.potion.is_some_and(|p| p.effect == EffectKind::Glow))
            .unwrap()
            .id;
        Drunk {
            effect: super::super::effect_for(def, [colour, 0, 0], None, Instant::now()).unwrap(),
            ended: vec![],
        }
    }

    #[test]
    fn one_drink_at_a_time_then_the_effect_applies() {
        let mut s = sim();
        let (mut a, mut b) = friends(&mut s, 1.0);
        potion(&mut s, &a, PotionInput::Drink { item_id: 40 });
        potion(&mut s, &a, PotionInput::Drink { item_id: 40 });
        assert_eq!(errors(&mut a), vec![PotionErrorCode::Busy]);
        assert_eq!(s.take_ext_jobs().len(), 1, "one drink job");

        done(&mut s, &a, 40, JobResult::Drank(Ok(glow(34))));
        let got = a.drain();
        let drank = got
            .iter()
            .position(|m| {
                matches!(
                    m,
                    ServerMsg::Drank {
                        item_id: 40,
                        effect: EffectKind::Glow
                    }
                )
            })
            .expect("Drank");
        let own = got
            .iter()
            .position(|m| matches!(m, ServerMsg::OwnEffects { .. }))
            .expect("OwnEffects");
        assert!(drank < own, "Drank first, then OwnEffects");
        // Viewers see it with the next tick.
        s.tick(Instant::now());
        let seen = b.drain().into_iter().any(|m| {
            matches!(m, ServerMsg::EffectsChanged { id, ref effects, .. }
                if id == a.id && effects == &vec![VisibleEffect::Glow { colour: 34 }])
        });
        assert!(seen);

        // Free again: a failed drink reports its code.
        potion(&mut s, &a, PotionInput::Drink { item_id: 41 });
        assert_eq!(s.take_ext_jobs().len(), 1);
        done(
            &mut s,
            &a,
            41,
            JobResult::Drank(Err(PotionErrorCode::ItemNotFound)),
        );
        assert_eq!(errors(&mut a), vec![PotionErrorCode::ItemNotFound]);
    }

    #[test]
    fn a_new_effect_ends_the_ones_it_conflicts_with() {
        let mut s = sim();
        let (mut a, _b) = friends(&mut s, 1.0);
        let id = a.id;
        done(&mut s, &a, 1, JobResult::Drank(Ok(glow(3))));
        let mut invisible = glow(3);
        invisible.effect.kind = EffectKind::Invisibility;
        invisible.effect.visible = Some(VisibleEffect::NameplateHidden);
        invisible.ended = vec![EffectKind::Glow];
        done(&mut s, &a, 2, JobResult::Drank(Ok(invisible)));
        a.drain();
        s.ext_done(
            ExtDone::Run(Box::new(move |ctx| {
                assert!(ctx.has_effect(id, EffectKind::Invisibility));
                assert!(!ctx.has_effect(id, EffectKind::Glow));
            })),
            Instant::now(),
        );
    }

    #[test]
    fn mimic_draught_asks_a_friend_in_range_who_may_decline() {
        let mut s = sim();
        let (mut a, mut b) = friends(&mut s, 3.0);
        let stranger = character("Cy", House::Burrowsby, Some([101.0, 0.0, 100.0]));
        let c = join_with_friends(&mut s, stranger, vec![]);
        potion(
            &mut s,
            &a,
            PotionInput::MimicDraughtAsk {
                item_id: 9,
                target: c.id,
            },
        );
        potion(
            &mut s,
            &a,
            PotionInput::MimicDraughtAsk {
                item_id: 9,
                target: a.id,
            },
        );
        potion(
            &mut s,
            &a,
            PotionInput::MimicDraughtAsk {
                item_id: 9,
                target: 999_999,
            },
        );
        assert_eq!(
            errors(&mut a),
            vec![
                PotionErrorCode::NotFriend,
                PotionErrorCode::NotFriend,
                PotionErrorCode::NotOnline
            ]
        );
        assert!(s.take_ext_jobs().is_empty());

        // In range: the item is checked first, then Bea is asked.
        potion(
            &mut s,
            &a,
            PotionInput::MimicDraughtAsk {
                item_id: 9,
                target: b.id,
            },
        );
        assert_eq!(s.take_ext_jobs().len(), 1, "the potion check");
        let target = b.cid;
        done(
            &mut s,
            &a,
            9,
            JobResult::Checked {
                target,
                result: Ok(()),
            },
        );
        let request = b
            .drain()
            .into_iter()
            .find_map(|m| match m {
                ServerMsg::MimicDraughtRequest {
                    request_id,
                    from,
                    from_name,
                    ..
                } => Some((request_id, from, from_name)),
                _ => None,
            })
            .expect("Bea is asked");
        assert_eq!((request.1, request.2.as_str()), (a.id, "Ada"));
        // A second ask while one waits is busy; Ada may still drink.
        potion(
            &mut s,
            &a,
            PotionInput::MimicDraughtAsk {
                item_id: 9,
                target: b.id,
            },
        );
        assert_eq!(errors(&mut a), vec![PotionErrorCode::Busy]);
        // Only Bea answers; she declines.
        potion(
            &mut s,
            &a,
            PotionInput::MimicDraughtAnswer {
                request_id: request.0,
                accept: true,
            },
        );
        assert_eq!(errors(&mut a), vec![PotionErrorCode::Expired]);
        potion(
            &mut s,
            &b,
            PotionInput::MimicDraughtAnswer {
                request_id: request.0,
                accept: false,
            },
        );
        let declined = a.drain().into_iter().any(
            |m| matches!(m, ServerMsg::MimicDraughtDeclined { ref by_name } if by_name == "Bea"),
        );
        assert!(declined);
        assert!(s.take_ext_jobs().is_empty(), "nothing drunk");
        // The request is gone.
        potion(
            &mut s,
            &b,
            PotionInput::MimicDraughtAnswer {
                request_id: request.0,
                accept: true,
            },
        );
        assert_eq!(errors(&mut b), vec![PotionErrorCode::Expired]);
    }

    #[test]
    fn accepting_drinks_and_requests_expire_or_end_with_their_people() {
        let mut s = sim();
        let (mut a, mut b) = friends(&mut s, 2.0);
        let ask = |s: &mut WorldSim, a: &TestClient, b: &mut TestClient| {
            potion(
                s,
                a,
                PotionInput::MimicDraughtAsk {
                    item_id: 9,
                    target: b.id,
                },
            );
            assert_eq!(s.take_ext_jobs().len(), 1);
            done(
                s,
                a,
                9,
                JobResult::Checked {
                    target: b.cid,
                    result: Ok(()),
                },
            );
            b.drain()
                .into_iter()
                .find_map(|m| match m {
                    ServerMsg::MimicDraughtRequest { request_id, .. } => Some(request_id),
                    _ => None,
                })
                .unwrap()
        };
        let id = ask(&mut s, &a, &mut b);
        potion(
            &mut s,
            &b,
            PotionInput::MimicDraughtAnswer {
                request_id: id,
                accept: true,
            },
        );
        assert_eq!(s.take_ext_jobs().len(), 1, "the drink with Bea's look");
        // The drink's result shows Bea's look on Ada, under Ada's name.
        let bea_look = Appearance {
            hair_colour: 7,
            height: 200,
            ..Appearance::default()
        };
        let def = crate::catalog::shipped()
            .catalog
            .items
            .iter()
            .find(|i| {
                i.potion
                    .is_some_and(|p| p.effect == EffectKind::MimicDraught)
            })
            .unwrap()
            .id;
        let effect = super::super::effect_for(def, [0; 3], Some(bea_look), Instant::now()).unwrap();
        done(
            &mut s,
            &a,
            9,
            JobResult::Drank(Ok(Drunk {
                effect,
                ended: vec![],
            })),
        );
        s.tick(Instant::now());
        let disguised = b.drain().into_iter().any(|m| {
            matches!(m, ServerMsg::EffectsChanged { id, ref effects, .. }
                if id == a.id && effects == &vec![VisibleEffect::Disguise { appearance: bea_look }])
        });
        assert!(disguised);
        let aid = a.id;
        s.ext_done(
            ExtDone::Run(Box::new(move |ctx| assert_eq!(ctx.name(aid), Some("Ada")))),
            Instant::now(),
        );
        a.drain();

        // Unanswered requests expire after MIMIC_DRAUGHT_REQUEST_SECS.
        let _ = ask(&mut s, &a, &mut b);
        let later = Instant::now() + Duration::from_secs(u64::from(MIMIC_DRAUGHT_REQUEST_SECS) + 1);
        for _ in 0..25 {
            s.tick(later);
        }
        assert_eq!(errors(&mut a), vec![PotionErrorCode::Expired]);

        // The friend leaving ends the request: the asker hears NotOnline.
        let _ = ask(&mut s, &a, &mut b);
        s.remove_player(b.id);
        assert_eq!(errors(&mut a), vec![PotionErrorCode::NotOnline]);
    }

    #[test]
    fn a_friend_out_of_range_is_too_far() {
        let mut s = sim();
        let (mut a, b) = friends(&mut s, 40.0);
        potion(
            &mut s,
            &a,
            PotionInput::MimicDraughtAsk {
                item_id: 9,
                target: b.id,
            },
        );
        assert_eq!(errors(&mut a), vec![PotionErrorCode::TooFar]);
    }
}
