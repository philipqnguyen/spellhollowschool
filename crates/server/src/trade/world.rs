//! The trade window's state and rules on the world thread
//! (seam: `realtime::ext`). The world owns a [`Trades`] and calls it:
//!
//! - `on_input` for every `ClientMsg::Trade*` (as `TradeInput`);
//! - `on_done` with the [`TradeDone`] of a job it spawned (`ctx.spawn`:
//!   loading an offer's items and the balance, or the commit);
//! - `on_left` before a character leaves the world (disconnect, or a new
//!   connection replacing it): its window or request ends as `Disconnected`;
//! - `on_zone_changed` after a zone change: it ends as `WalkedAway`;
//! - `tick` once a second: requests expire after `TRADE_REQUEST_SECS`, and
//!   windows whose players drifted beyond `distance_m + leave_slack_m` end.
//!
//! The flow (docs/architecture.md, "Economy and crafting"):
//!
//! 1. `TradeRequest` → `TradeRequested` to the target (a notice: another
//!    player caused it) **and the same message back to the requester**
//!    (`from` = its own id), which tells it the `trade_id` it may withdraw
//!    with `TradeCancel`.
//! 2. `TradeRespond`: declined → `TradeResult { Declined }` to both;
//!    accepted → `TradeState` to both (revision 1, empty offers).
//! 3. `TradeSetOffer` (unlocked only) loads the offered items (owned, in the
//!    inventory, enough units) and the balance in a job; the newest load
//!    wins. While it runs the side is busy (`Lock` and `Confirm` answer
//!    `Busy`). `TradeLock` locks or unlocks the sender's own offer.
//! 4. Every change to an offer or a lock bumps `rev` and clears both
//!    confirmations. `TradeConfirm { rev }` needs both offers locked and the
//!    current revision; when both confirmed it, the window is committing:
//!    one job runs `trade::commit_with_balances` with the window's random
//!    63-bit key, and nothing can change, cancel or walk away from it any
//!    more. `TradeResult { Completed }` and `Wallet` to both, or `Failed`.
//! 5. `TradeCancel` (either side, also withdraws or refuses a request),
//!    walking apart, a zone change or leaving end it for both.
//!
//! Errors answer only the sender (`TradeError`) and leave the window as it
//! was; messages to the partner go through `ctx.notice`.

use std::collections::HashMap;
use std::collections::HashSet;
use std::time::{Duration, Instant};

use protocol::{
    EntityId, OfferItem, OfferedItem, ServerMsg, TRADE_ITEMS_MAX, TRADE_REQUEST_SECS,
    TradeErrorCode, TradeOffer, TradeOutcome, TradeView,
};
use sqlx::PgPool;

use super::{Committed, Side};
use crate::catalog::shipped;
use crate::inventory::LOCATION_INVENTORY;
use crate::realtime::ext::{ExtDone, TradeInput, WorldCtx};

/// What a trade job hands back to the world thread.
#[derive(Debug)]
pub enum TradeDone {
    /// A `TradeSetOffer`'s items and balance were checked: the offer as
    /// both sides will see it, or why it cannot be made.
    Offer {
        trade_id: u32,
        character_id: i64,
        seq: u32,
        result: Result<TradeOffer, TradeErrorCode>,
    },
    /// The commit finished.
    Commit {
        trade_id: u32,
        result: Result<Committed, TradeErrorCode>,
    },
}

#[derive(Debug)]
struct Request {
    from: EntityId,
    to: EntityId,
    expires: Instant,
}

/// One side of an open window.
#[derive(Debug)]
struct Party {
    entity: EntityId,
    character_id: i64,
    name: String,
    offer: TradeOffer,
    locked: bool,
    /// The revision this side confirmed, if it did.
    confirmed: Option<u32>,
    /// The `TradeSetOffer` being loaded (its sequence number).
    loading: Option<u32>,
}

#[derive(Debug)]
struct Window {
    key: i64,
    /// The requester, then the one who accepted.
    parties: [Party; 2],
    rev: u32,
    committing: bool,
}

impl Window {
    fn side_of(&self, e: EntityId) -> Option<usize> {
        self.parties.iter().position(|p| p.entity == e)
    }

    /// Any change to an offer or a lock: a new revision nobody confirmed.
    fn changed(&mut self) {
        self.rev = self.rev.wrapping_add(1);
        for p in &mut self.parties {
            p.confirmed = None;
        }
    }

    fn view(&self, trade_id: u32, side: usize) -> TradeView {
        let (me, them) = (&self.parties[side], &self.parties[1 - side]);
        TradeView {
            trade_id,
            partner: them.entity,
            partner_name: them.name.clone(),
            rev: self.rev,
            mine: me.offer.clone(),
            theirs: them.offer.clone(),
            mine_locked: me.locked,
            theirs_locked: them.locked,
            mine_confirmed: me.confirmed == Some(self.rev),
            theirs_confirmed: them.confirmed == Some(self.rev),
        }
    }
}

/// Every open trade window and pending request.
#[derive(Debug, Default)]
pub struct Trades {
    next_id: u32,
    next_seq: u32,
    requests: HashMap<u32, Request>,
    windows: HashMap<u32, Window>,
    /// Who is in which request or window (both sides).
    by_entity: HashMap<EntityId, u32>,
}

fn error(ctx: &mut WorldCtx<'_>, to: EntityId, trade_id: Option<u32>, code: TradeErrorCode) {
    ctx.send(to, &ServerMsg::TradeError { trade_id, code });
}

impl Trades {
    pub fn on_input(&mut self, ctx: &mut WorldCtx<'_>, from: EntityId, input: TradeInput) {
        match input {
            TradeInput::Request { target } => self.request(ctx, from, target),
            TradeInput::Respond { trade_id, accept } => self.respond(ctx, from, trade_id, accept),
            TradeInput::SetOffer {
                trade_id,
                items,
                crowns,
            } => self.set_offer(ctx, from, trade_id, items, crowns),
            TradeInput::Lock { trade_id, locked } => self.lock(ctx, from, trade_id, locked),
            TradeInput::Confirm { trade_id, rev } => self.confirm(ctx, from, trade_id, rev),
            TradeInput::Cancel { trade_id } => self.cancel(ctx, from, trade_id),
        }
    }

    pub fn on_done(&mut self, ctx: &mut WorldCtx<'_>, done: TradeDone) {
        match done {
            TradeDone::Offer {
                trade_id,
                character_id,
                seq,
                result,
            } => self.offer_loaded(ctx, trade_id, character_id, seq, result),
            TradeDone::Commit { trade_id, result } => self.committed(ctx, trade_id, result),
        }
    }

    pub fn on_left(&mut self, ctx: &mut WorldCtx<'_>, id: EntityId) {
        self.end_for(ctx, id, TradeOutcome::Disconnected);
    }

    pub fn on_zone_changed(&mut self, ctx: &mut WorldCtx<'_>, id: EntityId) {
        self.end_for(ctx, id, TradeOutcome::WalkedAway);
    }

    pub fn tick(&mut self, ctx: &mut WorldCtx<'_>) {
        let now = ctx.now();
        let expired: Vec<u32> = self
            .requests
            .iter()
            .filter(|(_, r)| r.expires <= now)
            .map(|(&id, _)| id)
            .collect();
        for id in expired {
            self.end(ctx, id, TradeOutcome::Expired, None);
        }
        let rules = &shipped().economy.trade;
        let leave = rules.distance_m + rules.leave_slack_m;
        let apart: Vec<u32> = self
            .windows
            .iter()
            .filter(|(_, w)| !w.committing)
            .filter(|(_, w)| {
                ctx.distance(w.parties[0].entity, w.parties[1].entity)
                    .is_none_or(|d| d > leave)
            })
            .map(|(&id, _)| id)
            .collect();
        for id in apart {
            self.end(ctx, id, TradeOutcome::WalkedAway, None);
        }
    }

    /// Whether `id` is in an open trade window (a rider
    /// may not mount then).
    pub fn is_trading(&self, id: EntityId) -> bool {
        self.by_entity
            .get(&id)
            .is_some_and(|t| self.windows.contains_key(t))
    }

    /// Open windows and pending requests (metrics, tests).
    pub fn open(&self) -> (usize, usize) {
        (self.windows.len(), self.requests.len())
    }

    // ---- input

    fn request(&mut self, ctx: &mut WorldCtx<'_>, from: EntityId, target: EntityId) {
        if !ctx.take_action(from) {
            return error(ctx, from, None, TradeErrorCode::RateLimited);
        }
        if target == from {
            return error(ctx, from, None, TradeErrorCode::SelfTrade);
        }
        // A block either way looks like an unknown player (blocks stay private).
        if ctx.character_id(target).is_none() || ctx.blocked_either(from, target) {
            return error(ctx, from, None, TradeErrorCode::UnknownTarget);
        }
        let reach = shipped().economy.trade.distance_m;
        if ctx.distance(from, target).is_none_or(|d| d > reach) {
            return error(ctx, from, None, TradeErrorCode::TooFar);
        }
        if self.by_entity.contains_key(&from) || self.by_entity.contains_key(&target) {
            return error(ctx, from, None, TradeErrorCode::Busy);
        }
        let trade_id = self.new_id();
        let expires = ctx.now() + Duration::from_secs(u64::from(TRADE_REQUEST_SECS));
        self.requests.insert(
            trade_id,
            Request {
                from,
                to: target,
                expires,
            },
        );
        self.by_entity.insert(from, trade_id);
        self.by_entity.insert(target, trade_id);
        let msg = ServerMsg::TradeRequested {
            trade_id,
            from,
            from_name: ctx.name(from).unwrap_or_default().to_owned(),
            expires_in_secs: TRADE_REQUEST_SECS,
        };
        ctx.notice(target, &msg);
        ctx.send(from, &msg);
    }

    fn respond(&mut self, ctx: &mut WorldCtx<'_>, from: EntityId, trade_id: u32, accept: bool) {
        let Some(req) = self.requests.get(&trade_id).filter(|r| r.to == from) else {
            return error(ctx, from, Some(trade_id), TradeErrorCode::UnknownTrade);
        };
        if !accept {
            return self.end(ctx, trade_id, TradeOutcome::Declined, Some(from));
        }
        let reach = shipped().economy.trade.distance_m;
        if ctx.distance(req.from, req.to).is_none_or(|d| d > reach) {
            return error(ctx, from, Some(trade_id), TradeErrorCode::TooFar);
        }
        let (a, b) = (req.from, req.to);
        let (Some(pa), Some(pb)) = (party(ctx, a), party(ctx, b)) else {
            return self.end(ctx, trade_id, TradeOutcome::Disconnected, None);
        };
        self.requests.remove(&trade_id);
        let window = Window {
            // Positive and non-zero, unique per window (`trades.trade_key`).
            key: (rand::random::<i64>() & i64::MAX).max(1),
            parties: [pa, pb],
            rev: 1,
            committing: false,
        };
        self.windows.insert(trade_id, window);
        self.broadcast(ctx, trade_id, Some(from));
    }

    fn set_offer(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        from: EntityId,
        trade_id: u32,
        items: Vec<OfferItem>,
        crowns: i64,
    ) {
        let Some((w, side)) = self.window_of(from, trade_id) else {
            return error(ctx, from, Some(trade_id), TradeErrorCode::UnknownTrade);
        };
        if w.committing {
            return error(ctx, from, Some(trade_id), TradeErrorCode::Busy);
        }
        if w.parties[side].locked {
            return error(ctx, from, Some(trade_id), TradeErrorCode::Locked);
        }
        if !offer_ok(&items, crowns) {
            return error(ctx, from, Some(trade_id), TradeErrorCode::InvalidOffer);
        }
        if !ctx.take_action(from) {
            return error(ctx, from, Some(trade_id), TradeErrorCode::RateLimited);
        }
        self.next_seq = self.next_seq.wrapping_add(1);
        let seq = self.next_seq;
        let Some(w) = self.windows.get_mut(&trade_id) else {
            return;
        };
        let party = &mut w.parties[side];
        party.loading = Some(seq);
        let character_id = party.character_id;
        ctx.spawn(Box::new(move |pool: PgPool| {
            Box::pin(async move {
                let result = load_offer(&pool, character_id, &items, crowns).await;
                ExtDone::Trade(TradeDone::Offer {
                    trade_id,
                    character_id,
                    seq,
                    result,
                })
            })
        }));
    }

    fn lock(&mut self, ctx: &mut WorldCtx<'_>, from: EntityId, trade_id: u32, locked: bool) {
        let Some((w, side)) = self.window_of(from, trade_id) else {
            return error(ctx, from, Some(trade_id), TradeErrorCode::UnknownTrade);
        };
        if w.committing || w.parties[side].loading.is_some() {
            return error(ctx, from, Some(trade_id), TradeErrorCode::Busy);
        }
        if w.parties[side].locked == locked {
            let view = w.view(trade_id, side);
            return ctx.send(from, &ServerMsg::TradeState { view });
        }
        if !ctx.take_action(from) {
            return error(ctx, from, Some(trade_id), TradeErrorCode::RateLimited);
        }
        if let Some(w) = self.windows.get_mut(&trade_id) {
            w.parties[side].locked = locked;
            w.changed();
        }
        self.broadcast(ctx, trade_id, Some(from));
    }

    fn confirm(&mut self, ctx: &mut WorldCtx<'_>, from: EntityId, trade_id: u32, rev: u32) {
        let Some((w, side)) = self.window_of(from, trade_id) else {
            return error(ctx, from, Some(trade_id), TradeErrorCode::UnknownTrade);
        };
        if w.committing || w.parties.iter().any(|p| p.loading.is_some()) {
            return error(ctx, from, Some(trade_id), TradeErrorCode::Busy);
        }
        if !w.parties.iter().all(|p| p.locked) {
            return error(ctx, from, Some(trade_id), TradeErrorCode::NotLocked);
        }
        if rev != w.rev {
            return error(ctx, from, Some(trade_id), TradeErrorCode::StaleRevision);
        }
        if w.parties
            .iter()
            .all(|p| p.offer.items.is_empty() && p.offer.crowns == 0)
        {
            return error(ctx, from, Some(trade_id), TradeErrorCode::InvalidOffer);
        }
        let Some(w) = self.windows.get_mut(&trade_id) else {
            return;
        };
        w.parties[side].confirmed = Some(rev);
        if w.parties.iter().all(|p| p.confirmed == Some(rev)) {
            w.committing = true;
            let key = w.key;
            let [a, b] = [&w.parties[0], &w.parties[1]].map(|p| Side {
                character_id: p.character_id,
                items: p
                    .offer
                    .items
                    .iter()
                    .map(|i| (i.item_id, i.quantity))
                    .collect(),
                crowns: p.offer.crowns,
                reviewed: p.offer.items.clone(),
            });
            ctx.spawn(Box::new(move |pool: PgPool| {
                Box::pin(async move {
                    let result = super::commit_with_balances(&pool, key, &a, &b).await;
                    ExtDone::Trade(TradeDone::Commit { trade_id, result })
                })
            }));
        }
        self.broadcast(ctx, trade_id, Some(from));
    }

    fn cancel(&mut self, ctx: &mut WorldCtx<'_>, from: EntityId, trade_id: u32) {
        let in_request = self
            .requests
            .get(&trade_id)
            .is_some_and(|r| r.from == from || r.to == from);
        let in_window = self.window_of(from, trade_id).map(|(w, _)| w.committing);
        match (in_request, in_window) {
            (_, Some(true)) => error(ctx, from, Some(trade_id), TradeErrorCode::Busy),
            (true, _) | (_, Some(false)) => {
                self.end(ctx, trade_id, TradeOutcome::Cancelled, Some(from))
            }
            (false, None) => error(ctx, from, Some(trade_id), TradeErrorCode::UnknownTrade),
        }
    }

    // ---- jobs

    fn offer_loaded(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        trade_id: u32,
        character_id: i64,
        seq: u32,
        result: Result<TradeOffer, TradeErrorCode>,
    ) {
        let Some(w) = self.windows.get_mut(&trade_id) else {
            return;
        };
        let Some(side) = w
            .parties
            .iter()
            .position(|p| p.character_id == character_id && p.loading == Some(seq))
        else {
            return; // A newer offer replaced this one, or the window moved on.
        };
        w.parties[side].loading = None;
        let entity = w.parties[side].entity;
        match result {
            Err(code) => error(ctx, entity, Some(trade_id), code),
            Ok(offer) => {
                w.parties[side].offer = offer;
                w.changed();
                self.broadcast(ctx, trade_id, Some(entity));
            }
        }
    }

    fn committed(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        trade_id: u32,
        result: Result<Committed, TradeErrorCode>,
    ) {
        let Some(w) = self.windows.remove(&trade_id) else {
            return;
        };
        for p in &w.parties {
            if self.by_entity.get(&p.entity) == Some(&trade_id) {
                self.by_entity.remove(&p.entity);
            }
        }
        let outcome = match result {
            Ok(_) => TradeOutcome::Completed,
            Err(code) => TradeOutcome::Failed(code),
        };
        for (i, p) in w.parties.iter().enumerate() {
            // Only to the same character, still online on that connection.
            if ctx.character_id(p.entity) != Some(p.character_id) {
                continue;
            }
            ctx.notice(p.entity, &ServerMsg::TradeResult { trade_id, outcome });
            if let Ok(c) = &result {
                let crowns = if i == 0 { c.balances.0 } else { c.balances.1 };
                ctx.notice(p.entity, &ServerMsg::Wallet { crowns });
            }
        }
    }

    // ---- helpers

    fn new_id(&mut self) -> u32 {
        loop {
            self.next_id = self.next_id.wrapping_add(1);
            let id = self.next_id;
            if id != 0 && !self.requests.contains_key(&id) && !self.windows.contains_key(&id) {
                return id;
            }
        }
    }

    /// `from`'s window `trade_id`, and which side it is on.
    fn window_of(&self, from: EntityId, trade_id: u32) -> Option<(&Window, usize)> {
        let w = self.windows.get(&trade_id)?;
        w.side_of(from).map(|s| (w, s))
    }

    /// `TradeState` to both sides: a reply to `actor`, a notice to the other.
    fn broadcast(&self, ctx: &mut WorldCtx<'_>, trade_id: u32, actor: Option<EntityId>) {
        let Some(w) = self.windows.get(&trade_id) else {
            return;
        };
        for side in 0..2 {
            let to = w.parties[side].entity;
            let msg = ServerMsg::TradeState {
                view: w.view(trade_id, side),
            };
            if Some(to) == actor {
                ctx.send(to, &msg);
            } else {
                ctx.notice(to, &msg);
            }
        }
    }

    /// Ends request or window `trade_id` (unless it is committing) with
    /// `outcome` for both sides still there.
    fn end(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        trade_id: u32,
        outcome: TradeOutcome,
        actor: Option<EntityId>,
    ) {
        let both = if let Some(r) = self.requests.remove(&trade_id) {
            [r.from, r.to]
        } else {
            match self.windows.get(&trade_id) {
                Some(w) if !w.committing => {
                    let both = [w.parties[0].entity, w.parties[1].entity];
                    self.windows.remove(&trade_id);
                    both
                }
                _ => return,
            }
        };
        for e in both {
            if self.by_entity.get(&e) == Some(&trade_id) {
                self.by_entity.remove(&e);
            }
            let msg = ServerMsg::TradeResult { trade_id, outcome };
            if Some(e) == actor {
                ctx.send(e, &msg);
            } else {
                ctx.notice(e, &msg);
            }
        }
    }

    /// `id` left or changed zone: end what it is in. A committing window
    /// finishes anyway (the transaction decides); `id` just stops being in it.
    fn end_for(&mut self, ctx: &mut WorldCtx<'_>, id: EntityId, outcome: TradeOutcome) {
        let Some(&trade_id) = self.by_entity.get(&id) else {
            return;
        };
        if self.windows.get(&trade_id).is_some_and(|w| w.committing) {
            self.by_entity.remove(&id);
            return;
        }
        self.end(ctx, trade_id, outcome, None);
    }
}

/// A new party for a window: the character as the world knows it now.
fn party(ctx: &WorldCtx<'_>, e: EntityId) -> Option<Party> {
    Some(Party {
        entity: e,
        character_id: ctx.character_id(e)?,
        name: ctx.name(e)?.to_owned(),
        offer: TradeOffer::default(),
        locked: false,
        confirmed: None,
        loading: None,
    })
}

/// The shape rules of an offer (the database checks the rest).
fn offer_ok(items: &[OfferItem], crowns: i64) -> bool {
    let max = i64::from(shipped().economy.trade.crowns_max);
    let mut seen = HashSet::new();
    items.len() <= TRADE_ITEMS_MAX
        && (0..=max).contains(&crowns)
        && items
            .iter()
            .all(|i| i.quantity > 0 && seen.insert(i.item_id))
}

/// Checks an offer against the database: every item the character's, in
/// the inventory, with enough units; the balance covers the Crowns.
/// Returns the offer as both sides see it, in the order offered.
async fn load_offer(
    pool: &PgPool,
    character_id: i64,
    items: &[OfferItem],
    crowns: i64,
) -> Result<TradeOffer, TradeErrorCode> {
    let failed = |e: sqlx::Error| {
        tracing::error!(error = %e, character_id, "loading a trade offer failed");
        crate::monitoring::capture_for_character(&e, character_id);
        TradeErrorCode::Failed
    };
    let ids: Vec<i64> = items.iter().map(|i| i.item_id).collect();
    type Row = (i64, i32, i16, i16, i16, i16, i32, String, Option<String>);
    let rows: Vec<Row> = sqlx::query_as(
        "SELECT id, item_def, dye_primary, dye_secondary, dye_trim, finish, quantity, location,
                maker_name
         FROM items WHERE owner_id = $1 AND id = ANY($2)",
    )
    .bind(character_id)
    .bind(&ids)
    .fetch_all(pool)
    .await
    .map_err(failed)?;
    let mut offered = Vec::with_capacity(items.len());
    for want in items {
        let Some(r) = rows.iter().find(|r| r.0 == want.item_id) else {
            return Err(TradeErrorCode::ItemUnavailable);
        };
        let held = u32::try_from(r.6).unwrap_or(0);
        if r.7 != LOCATION_INVENTORY || want.quantity > held {
            return Err(TradeErrorCode::ItemUnavailable);
        }
        let byte = |v: i16| u8::try_from(v).unwrap_or(0);
        offered.push(OfferedItem {
            item_id: r.0,
            item_def: u16::try_from(r.1).unwrap_or(0),
            dyes: [byte(r.2), byte(r.3), byte(r.4)],
            finish: crate::items::finish_from_db(r.5),
            quantity: want.quantity,
            maker: r.8.clone(),
        });
    }
    if crowns > 0 {
        let balance = crate::ledger::balance(pool, character_id)
            .await
            .map_err(failed)?;
        if balance < crowns {
            return Err(TradeErrorCode::InsufficientCrowns);
        }
    }
    Ok(TradeOffer {
        items: offered,
        crowns,
    })
}

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use protocol::{Anim, Finish, House};

    use super::*;
    use crate::realtime::world::tests::{TestClient, join, sim};
    use crate::realtime::world::{Input, WorldCmd, WorldSim};

    /// A [`Trades`] driven through the world's `ExtDone::Run` (which hands
    /// out a `WorldCtx`), so the tests control every call.
    #[derive(Clone, Default)]
    struct Harness(Arc<Mutex<Trades>>);

    impl Harness {
        fn with(
            &self,
            s: &mut WorldSim,
            f: impl FnOnce(&mut Trades, &mut WorldCtx<'_>) + Send + 'static,
        ) {
            let t = self.0.clone();
            s.handle(WorldCmd::ExtDone(ExtDone::Run(Box::new(move |ctx| {
                f(&mut t.lock().unwrap(), ctx)
            }))));
        }

        fn input(&self, s: &mut WorldSim, from: EntityId, input: TradeInput) {
            self.with(s, move |t, ctx| t.on_input(ctx, from, input));
        }

        fn done(&self, s: &mut WorldSim, done: TradeDone) {
            self.with(s, move |t, ctx| t.on_done(ctx, done));
        }

        fn tick(&self, s: &mut WorldSim) {
            self.with(s, |t, ctx| t.tick(ctx));
        }

        fn open(&self) -> (usize, usize) {
            self.0.lock().unwrap().open()
        }
    }

    fn trade_msgs(c: &mut TestClient) -> Vec<ServerMsg> {
        c.drain()
            .into_iter()
            .filter(|m| {
                matches!(
                    m,
                    ServerMsg::TradeRequested { .. }
                        | ServerMsg::TradeState { .. }
                        | ServerMsg::TradeResult { .. }
                        | ServerMsg::TradeError { .. }
                        | ServerMsg::Wallet { .. }
                )
            })
            .collect()
    }

    fn last_view(c: &mut TestClient) -> TradeView {
        trade_msgs(c)
            .into_iter()
            .rev()
            .find_map(|m| match m {
                ServerMsg::TradeState { view } => Some(view),
                _ => None,
            })
            .expect("a TradeState")
    }

    fn errors(c: &mut TestClient) -> Vec<TradeErrorCode> {
        trade_msgs(c)
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::TradeError { code, .. } => Some(code),
                _ => None,
            })
            .collect()
    }

    fn results(c: &mut TestClient) -> Vec<TradeOutcome> {
        trade_msgs(c)
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::TradeResult { outcome, .. } => Some(outcome),
                _ => None,
            })
            .collect()
    }

    fn scarf(item_id: i64) -> OfferedItem {
        OfferedItem {
            item_id,
            item_def: 103,
            dyes: [1, 2, 3],
            finish: Finish::None,
            quantity: 1,
            maker: Some("Ada".into()),
        }
    }

    /// Two players 2 m apart with an open window; returns its id.
    fn open_window(h: &Harness, s: &mut WorldSim, a: &mut TestClient, b: &mut TestClient) -> u32 {
        h.input(s, a.id, TradeInput::Request { target: b.id });
        let trade_id = trade_msgs(b)
            .into_iter()
            .find_map(|m| match m {
                ServerMsg::TradeRequested { trade_id, from, .. } if from == a.id => Some(trade_id),
                _ => None,
            })
            .expect("b was asked");
        // The requester hears its own request back (to withdraw it).
        assert!(trade_msgs(a).contains(&ServerMsg::TradeRequested {
            trade_id,
            from: a.id,
            from_name: "Ada".into(),
            expires_in_secs: TRADE_REQUEST_SECS,
        }));
        h.input(
            s,
            b.id,
            TradeInput::Respond {
                trade_id,
                accept: true,
            },
        );
        let view = last_view(a);
        assert_eq!((view.partner, view.rev), (b.id, 1));
        assert_eq!(last_view(b).partner_name, "Ada");
        trade_id
    }

    /// Runs `a`'s offer load as the database would have answered it.
    fn loaded(h: &Harness, s: &mut WorldSim, trade_id: u32, who: &TestClient, offer: TradeOffer) {
        let jobs = s.take_ext_jobs();
        assert_eq!(jobs.len(), 1, "one load per offer");
        let seq = h.0.lock().unwrap().next_seq;
        h.done(
            s,
            TradeDone::Offer {
                trade_id,
                character_id: who.cid,
                seq,
                result: Ok(offer),
            },
        );
    }

    fn setup() -> (Harness, WorldSim, TestClient, TestClient) {
        let mut s = sim();
        let mut a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
        let mut b = join(&mut s, "Bea", House::Skytalon, [102.0, 0.0, 100.0]);
        a.drain();
        b.drain();
        (Harness::default(), s, a, b)
    }

    #[test]
    fn both_lock_both_confirm_and_any_change_resets_confirmations() {
        let (h, mut s, mut a, mut b) = setup();
        let id = open_window(&h, &mut s, &mut a, &mut b);

        // Ada offers a scarf and 5 Crowns; the job loads them.
        let items = vec![OfferItem {
            item_id: 11,
            quantity: 1,
        }];
        h.input(
            &mut s,
            a.id,
            TradeInput::SetOffer {
                trade_id: id,
                items,
                crowns: 5,
            },
        );
        // Busy while it loads.
        h.input(
            &mut s,
            a.id,
            TradeInput::Lock {
                trade_id: id,
                locked: true,
            },
        );
        assert_eq!(errors(&mut a), vec![TradeErrorCode::Busy]);
        let offer = TradeOffer {
            items: vec![scarf(11)],
            crowns: 5,
        };
        loaded(&h, &mut s, id, &a, offer.clone());
        let view = last_view(&mut b);
        assert_eq!((view.rev, view.theirs.clone()), (2, offer.clone()));

        // Confirming needs both locks.
        h.input(
            &mut s,
            a.id,
            TradeInput::Confirm {
                trade_id: id,
                rev: 2,
            },
        );
        assert_eq!(errors(&mut a), vec![TradeErrorCode::NotLocked]);
        h.input(
            &mut s,
            a.id,
            TradeInput::Lock {
                trade_id: id,
                locked: true,
            },
        );
        h.input(
            &mut s,
            b.id,
            TradeInput::Lock {
                trade_id: id,
                locked: true,
            },
        );
        let rev = last_view(&mut a).rev;
        assert_eq!(rev, 4);
        b.drain();
        // A locked offer cannot change.
        h.input(
            &mut s,
            a.id,
            TradeInput::SetOffer {
                trade_id: id,
                items: vec![],
                crowns: 1,
            },
        );
        assert_eq!(errors(&mut a), vec![TradeErrorCode::Locked]);
        // An old revision is refused; the current one counts.
        h.input(
            &mut s,
            a.id,
            TradeInput::Confirm {
                trade_id: id,
                rev: 2,
            },
        );
        assert_eq!(errors(&mut a), vec![TradeErrorCode::StaleRevision]);
        h.input(&mut s, a.id, TradeInput::Confirm { trade_id: id, rev });
        let seen = last_view(&mut b);
        assert!(seen.theirs_confirmed && !seen.mine_confirmed);

        // Bea unlocks: a new revision, Ada's confirmation is gone.
        h.input(
            &mut s,
            b.id,
            TradeInput::Lock {
                trade_id: id,
                locked: false,
            },
        );
        let after = last_view(&mut a);
        assert_eq!(after.rev, rev + 1);
        assert!(!after.mine_confirmed && !after.theirs_locked);

        // Both lock and confirm the same revision: the commit job starts.
        h.input(
            &mut s,
            b.id,
            TradeInput::Lock {
                trade_id: id,
                locked: true,
            },
        );
        let rev = last_view(&mut a).rev;
        h.input(&mut s, a.id, TradeInput::Confirm { trade_id: id, rev });
        h.input(&mut s, b.id, TradeInput::Confirm { trade_id: id, rev });
        assert_eq!(s.take_ext_jobs().len(), 1, "one commit");
        assert!(last_view(&mut a).theirs_confirmed);
        // Nothing changes, cancels or walks away while it commits.
        h.input(&mut s, b.id, TradeInput::Cancel { trade_id: id });
        assert_eq!(errors(&mut b), vec![TradeErrorCode::Busy]);
        s.remove_player(b.id);
        h.with(&mut s, {
            let bid = b.id;
            move |t, ctx| t.on_left(ctx, bid)
        });
        h.tick(&mut s);
        assert_eq!(h.open(), (1, 0));

        h.done(
            &mut s,
            TradeDone::Commit {
                trade_id: id,
                result: Ok(Committed {
                    trade_id: 77,
                    balances: (95, 205),
                }),
            },
        );
        let got = trade_msgs(&mut a);
        assert!(got.contains(&ServerMsg::TradeResult {
            trade_id: id,
            outcome: TradeOutcome::Completed
        }));
        assert!(got.contains(&ServerMsg::Wallet { crowns: 95 }));
        assert_eq!(h.open(), (0, 0));
    }

    #[test]
    fn requests_are_checked_and_expire() {
        let (h, mut s, mut a, mut b) = setup();
        let far = join(&mut s, "Cy", House::Roarwing, [150.0, 0.0, 100.0]);
        h.input(&mut s, a.id, TradeInput::Request { target: a.id });
        h.input(&mut s, a.id, TradeInput::Request { target: 99_999 });
        h.input(&mut s, a.id, TradeInput::Request { target: far.id });
        assert_eq!(
            errors(&mut a),
            vec![
                TradeErrorCode::SelfTrade,
                TradeErrorCode::UnknownTarget,
                TradeErrorCode::TooFar
            ]
        );
        h.input(&mut s, a.id, TradeInput::Request { target: b.id });
        let id = h.0.lock().unwrap().next_id;
        // Busy: one request or window at a time, on both sides.
        h.input(&mut s, b.id, TradeInput::Request { target: a.id });
        assert_eq!(errors(&mut b), vec![TradeErrorCode::Busy]);
        // Only the target answers.
        h.input(
            &mut s,
            a.id,
            TradeInput::Respond {
                trade_id: id,
                accept: true,
            },
        );
        assert_eq!(errors(&mut a), vec![TradeErrorCode::UnknownTrade]);
        // Thirty seconds later it is gone for both.
        let later = Instant::now() + Duration::from_secs(u64::from(TRADE_REQUEST_SECS) + 1);
        let t = h.0.clone();
        s.ext_done(
            ExtDone::Run(Box::new(move |ctx| t.lock().unwrap().tick(ctx))),
            later,
        );
        assert_eq!(results(&mut a), vec![TradeOutcome::Expired]);
        assert_eq!(results(&mut b), vec![TradeOutcome::Expired]);
        assert_eq!(h.open(), (0, 0));

        // Declining and withdrawing end it too.
        h.input(&mut s, a.id, TradeInput::Request { target: b.id });
        let id = h.0.lock().unwrap().next_id;
        h.input(
            &mut s,
            b.id,
            TradeInput::Respond {
                trade_id: id,
                accept: false,
            },
        );
        assert_eq!(results(&mut a), vec![TradeOutcome::Declined]);
        b.drain();
        h.input(&mut s, a.id, TradeInput::Request { target: b.id });
        let id = h.0.lock().unwrap().next_id;
        h.input(&mut s, a.id, TradeInput::Cancel { trade_id: id });
        assert_eq!(results(&mut b), vec![TradeOutcome::Cancelled]);
        assert_eq!(h.open(), (0, 0));
    }

    #[test]
    fn stale_loads_lose_and_failed_loads_explain() {
        let (h, mut s, mut a, mut b) = setup();
        let id = open_window(&h, &mut s, &mut a, &mut b);
        let one = |item_id| {
            vec![OfferItem {
                item_id,
                quantity: 1,
            }]
        };
        h.input(
            &mut s,
            a.id,
            TradeInput::SetOffer {
                trade_id: id,
                items: one(1),
                crowns: 0,
            },
        );
        let first = h.0.lock().unwrap().next_seq;
        h.input(
            &mut s,
            a.id,
            TradeInput::SetOffer {
                trade_id: id,
                items: one(2),
                crowns: 0,
            },
        );
        assert_eq!(s.take_ext_jobs().len(), 2);
        let second = h.0.lock().unwrap().next_seq;
        // The newer load answers first; the older one is ignored after it.
        let offer = |item_id| TradeOffer {
            items: vec![scarf(item_id)],
            crowns: 0,
        };
        for seq in [second, first] {
            h.done(
                &mut s,
                TradeDone::Offer {
                    trade_id: id,
                    character_id: a.cid,
                    seq,
                    result: Ok(offer(if seq == first { 1 } else { 2 })),
                },
            );
        }
        assert_eq!(last_view(&mut b).theirs, offer(2));
        // A refused load leaves the offer as it was and tells the sender.
        h.input(
            &mut s,
            a.id,
            TradeInput::SetOffer {
                trade_id: id,
                items: one(3),
                crowns: 0,
            },
        );
        s.take_ext_jobs();
        let seq = h.0.lock().unwrap().next_seq;
        h.done(
            &mut s,
            TradeDone::Offer {
                trade_id: id,
                character_id: a.cid,
                seq,
                result: Err(TradeErrorCode::ItemUnavailable),
            },
        );
        assert_eq!(errors(&mut a), vec![TradeErrorCode::ItemUnavailable]);
        assert!(trade_msgs(&mut b).is_empty());
        // Malformed offers never reach the database.
        let dup = vec![
            OfferItem {
                item_id: 4,
                quantity: 1,
            };
            2
        ];
        h.input(
            &mut s,
            a.id,
            TradeInput::SetOffer {
                trade_id: id,
                items: dup,
                crowns: 0,
            },
        );
        h.input(
            &mut s,
            a.id,
            TradeInput::SetOffer {
                trade_id: id,
                items: vec![],
                crowns: -3,
            },
        );
        assert_eq!(
            errors(&mut a),
            vec![TradeErrorCode::InvalidOffer, TradeErrorCode::InvalidOffer]
        );
        assert!(s.take_ext_jobs().is_empty());
    }

    #[test]
    fn walking_away_leaving_and_failed_commits_end_the_window() {
        let (h, mut s, mut a, mut b) = setup();
        let id = open_window(&h, &mut s, &mut a, &mut b);
        // Bea walks east 2 m a second (well within the move speed).
        let start = Instant::now();
        let mut seq = 0;
        let bid = b.id;
        let mut walk_to = |s: &mut WorldSim, x: f32| {
            seq += 1;
            let input = Input::Move {
                seq,
                pos: [x, 0.0, 100.0],
                yaw: 0.0,
                anim: Anim::Run,
            };
            s.input(bid, start + Duration::from_secs(u64::from(seq)), input);
        };
        // A few steps apart is fine; beyond distance + slack ends it.
        walk_to(&mut s, 104.0);
        walk_to(&mut s, 106.0);
        h.tick(&mut s);
        assert_eq!(h.open(), (1, 0));
        walk_to(&mut s, 108.0);
        h.tick(&mut s);
        assert_eq!(results(&mut a), vec![TradeOutcome::WalkedAway]);
        assert_eq!(results(&mut b), vec![TradeOutcome::WalkedAway]);

        // Back again; then disconnecting.
        walk_to(&mut s, 106.0);
        walk_to(&mut s, 104.0);
        walk_to(&mut s, 102.0);
        let id2 = open_window(&h, &mut s, &mut a, &mut b);
        assert_ne!(id, id2);
        let bid = b.id;
        h.with(&mut s, move |t, ctx| t.on_left(ctx, bid));
        assert_eq!(results(&mut a), vec![TradeOutcome::Disconnected]);

        // A zone change.
        let id3 = open_window(&h, &mut s, &mut a, &mut b);
        let aid = a.id;
        h.with(&mut s, move |t, ctx| t.on_zone_changed(ctx, aid));
        assert_eq!(results(&mut b), vec![TradeOutcome::WalkedAway]);

        // A commit the database refused: both hear why; nothing else changes.
        let id4 = open_window(&h, &mut s, &mut a, &mut b);
        h.input(
            &mut s,
            a.id,
            TradeInput::SetOffer {
                trade_id: id4,
                items: vec![],
                crowns: 7,
            },
        );
        loaded(
            &h,
            &mut s,
            id4,
            &a,
            TradeOffer {
                items: vec![],
                crowns: 7,
            },
        );
        for c in [&a, &b] {
            h.input(
                &mut s,
                c.id,
                TradeInput::Lock {
                    trade_id: id4,
                    locked: true,
                },
            );
        }
        let rev = last_view(&mut a).rev;
        b.drain();
        for c in [&a, &b] {
            h.input(&mut s, c.id, TradeInput::Confirm { trade_id: id4, rev });
        }
        s.take_ext_jobs();
        h.done(
            &mut s,
            TradeDone::Commit {
                trade_id: id4,
                result: Err(TradeErrorCode::InsufficientCrowns),
            },
        );
        let failed = TradeOutcome::Failed(TradeErrorCode::InsufficientCrowns);
        assert_eq!(results(&mut a), vec![failed]);
        assert_eq!(results(&mut b), vec![failed]);
        assert!(
            !trade_msgs(&mut a)
                .iter()
                .any(|m| matches!(m, ServerMsg::Wallet { .. }))
        );
        assert_eq!(h.open(), (0, 0));
        let _ = id3;
    }

    /// An open window (not a pending request) counts as
    /// trading, so its two players may not mount a broom.
    #[test]
    fn an_open_window_counts_as_trading() {
        let (h, mut s, mut a, mut b) = setup();
        h.input(&mut s, a.id, TradeInput::Request { target: b.id });
        assert!(!h.0.lock().unwrap().is_trading(a.id));
        trade_msgs(&mut a);
        trade_msgs(&mut b);
        let (h, mut s, mut a, mut b) = setup();
        let id = open_window(&h, &mut s, &mut a, &mut b);
        assert!(h.0.lock().unwrap().is_trading(a.id));
        assert!(h.0.lock().unwrap().is_trading(b.id));
        h.input(&mut s, a.id, TradeInput::Cancel { trade_id: id });
        assert!(!h.0.lock().unwrap().is_trading(a.id));
        assert!(!h.0.lock().unwrap().is_trading(b.id));
    }

    #[test]
    fn an_empty_trade_cannot_be_confirmed() {
        let (h, mut s, mut a, mut b) = setup();
        let id = open_window(&h, &mut s, &mut a, &mut b);
        for c in [&a, &b] {
            h.input(
                &mut s,
                c.id,
                TradeInput::Lock {
                    trade_id: id,
                    locked: true,
                },
            );
        }
        let rev = last_view(&mut a).rev;
        h.input(&mut s, a.id, TradeInput::Confirm { trade_id: id, rev });
        assert_eq!(errors(&mut a), vec![TradeErrorCode::InvalidOffer]);
    }
}
