//! Clubs in the world (docs/architecture.md, "Clubs"). The database is the
//! record (`crate::clubs::store`); the world keeps a copy of each club that
//! has a member online, the invitations, and what members are sent:
//!
//! - **Copies**: a character coming online loads its club in a job (unless
//!   the club is already here); every change comes back from its
//!   transaction as the whole club ([`store::Outcome`]) and replaces the
//!   copy unless the copy is newer (`clubs.version`). A club whose last
//!   member online leaves is dropped. A character deleted over HTTP
//!   reloads its club (`WorldHandle::club_changed`).
//! - **Actions** (`ClientMsg::Club*`): the shared action bucket, then the
//!   rules on the copy ([`Club`](crate::clubs::Club)), so most refusals are
//!   immediate; then a job runs the change in the database, which checks
//!   again under the club's lock and decides.
//! - **Invitations** (in memory, `CLUB_INVITE_TTL_SECS`): an officer or the
//!   owner invites an online character in no club, unless either blocked
//!   the other (`Blocked` to the blocker, `NotOnline` to the blocked).
//! - **What members get**: `ClubState` (the whole club) after a change of
//!   members, ranks, name, tag or message of the day, `ClubPresence` when a
//!   member comes online, goes offline or levels, `ClubNotice` for the chat
//!   log, and club chat from anywhere (`World::route_chat`). Messages caused
//!   by someone else go through the notice backlog.

use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::time::{Duration, Instant};

use bytes::Bytes;
use protocol::{
    CLUB_INVITE_TTL_SECS, ClubErrorCode, ClubMember, ClubNotice, ClubRole, ClubView, EntityId,
    ServerMsg, encode_server,
};
use sqlx::PgPool;

use super::ext::ExtDone;
use super::world::WorldSim;
use crate::clubs::store::{self, Outcome, StoreError};
use crate::clubs::{self, Club};

/// How long a club invitation may be accepted.
pub const INVITE_TTL: Duration = Duration::from_secs(CLUB_INVITE_TTL_SECS as u64);
/// Invitations one character may have out, and waiting for one character,
/// at once (a newer one drops the oldest).
pub const INVITES_PER_SENDER: usize = 8;
pub const INVITES_PER_TARGET: usize = 8;

/// An invitation from `from` (an officer or the owner) into `club` for `to`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Invite {
    pub id: u32,
    pub club: i64,
    pub from: i64,
    pub to: i64,
    pub until: Instant,
}

/// The copies of clubs with a member online, and the invitations.
#[derive(Debug, Default)]
pub struct Clubs {
    clubs: HashMap<i64, Club>,
    of: HashMap<i64, i64>,
    /// Clubs disbanded since the start: a late copy of one is never kept.
    gone: HashSet<i64>,
    invites: Vec<Invite>,
    next_invite: u32,
}

impl Clubs {
    pub fn len(&self) -> usize {
        self.clubs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.clubs.is_empty()
    }

    pub fn get(&self, id: i64) -> Option<&Club> {
        self.clubs.get(&id)
    }

    /// The club a character is in, if its copy is here.
    pub fn of(&self, character_id: i64) -> Option<&Club> {
        self.clubs.get(self.of.get(&character_id)?)
    }

    pub fn invites(&self) -> &[Invite] {
        &self.invites
    }

    /// Keeps `club` as club `id`'s copy (`None`: it is gone) unless the copy
    /// here is newer. Returns the members of the copy it replaced, or
    /// `None` when `club` was older and nothing changed.
    pub fn apply(&mut self, id: i64, club: Option<Club>) -> Option<Vec<i64>> {
        if self.gone.contains(&id) {
            return None;
        }
        if let (Some(old), Some(new)) = (self.clubs.get(&id), &club)
            && old.version > new.version
        {
            return None;
        }
        let before: Vec<i64> = self
            .clubs
            .remove(&id)
            .map(|c| c.members.iter().map(|m| m.character_id).collect())
            .unwrap_or_default();
        for c in &before {
            if self.of.get(c) == Some(&id) {
                self.of.remove(c);
            }
        }
        match club {
            Some(c) => {
                for m in &c.members {
                    self.of.insert(m.character_id, id);
                }
                self.clubs.insert(id, c);
            }
            None => {
                self.gone.insert(id);
                self.invites.retain(|i| i.club != id);
            }
        }
        Some(before)
    }

    /// Drops club `id`'s copy (nobody in it is online).
    pub fn drop_copy(&mut self, id: i64) {
        if let Some(c) = self.clubs.remove(&id) {
            for m in &c.members {
                self.of.remove(&m.character_id);
            }
        }
    }

    /// A member's level changed: whether the copy changed.
    pub fn set_level(&mut self, character_id: i64, level: u8) -> bool {
        let Some(&id) = self.of.get(&character_id) else {
            return false;
        };
        let Some(m) = self.clubs.get_mut(&id).and_then(|c| {
            c.members
                .iter_mut()
                .find(|m| m.character_id == character_id)
        }) else {
            return false;
        };
        let changed = m.level != level;
        m.level = level;
        changed
    }

    /// Records an invitation (the world checked ranks, presence and
    /// blocks). A newer one between the same two replaces the older.
    pub fn invite(&mut self, club: i64, from: i64, to: i64, now: Instant) -> Invite {
        self.invites.retain(|i| !(i.from == from && i.to == to));
        for (by_sender, cap) in [(true, INVITES_PER_SENDER), (false, INVITES_PER_TARGET)] {
            let mine = |i: &Invite| {
                if by_sender {
                    i.from == from
                } else {
                    i.to == to
                }
            };
            while self.invites.iter().filter(|i| mine(i)).count() >= cap {
                let oldest = self.invites.iter().position(mine).expect("counted");
                self.invites.remove(oldest);
            }
        }
        self.next_invite = self.next_invite.wrapping_add(1).max(1);
        let invite = Invite {
            id: self.next_invite,
            club,
            from,
            to,
            until: now + INVITE_TTL,
        };
        self.invites.push(invite);
        invite
    }

    /// Takes invitation `id` addressed to `to`, if it is still valid.
    pub fn take_invite(&mut self, id: u32, to: i64, now: Instant) -> Option<Invite> {
        let k = self.invites.iter().position(|i| i.id == id && i.to == to)?;
        let invite = self.invites.remove(k);
        (invite.until > now).then_some(invite)
    }

    /// Once a second: drops lapsed invitations.
    pub fn expire(&mut self, now: Instant) {
        self.invites.retain(|i| i.until > now);
    }
}

fn frame(msg: &ServerMsg) -> Bytes {
    encode_server(msg).into()
}

/// The club messages, as the world hands them to `club.rs`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClubInput {
    Found {
        secretary: EntityId,
        name: String,
        tag: String,
    },
    Invite(String),
    Respond {
        invite_id: u32,
        accept: bool,
    },
    Leave,
    Kick(i64),
    SetRole {
        character_id: i64,
        role: ClubRole,
    },
    Transfer(i64),
    Rename {
        name: String,
        tag: String,
    },
    SetMotd(String),
    Disband,
}

/// A finished club job: the outcome, and the founder's new balance.
type Done = Result<(Outcome, Option<i64>), StoreError>;

impl WorldSim {
    fn club_error(&mut self, i: usize, code: ClubErrorCode) {
        self.send_msg(i, &ServerMsg::ClubError { code });
    }

    /// Takes a token from the action bucket, or refuses (`RateLimited`).
    fn club_action(&mut self, i: usize, at: Instant) -> bool {
        let ok = self.ents.presence[i].actions.take(
            at,
            super::limits::ACTION_BURST,
            super::limits::ACTION_PER_SECOND,
        );
        if !ok {
            self.club_error(i, ClubErrorCode::RateLimited);
        }
        ok
    }

    /// The copy of character `i`'s club (`NotInClub` to it otherwise).
    fn my_club(&mut self, i: usize) -> Option<Club> {
        let me = self.ents.info[i].character_id;
        let club = self.clubs.of(me).cloned();
        if club.is_none() {
            self.club_error(i, ClubErrorCode::NotInClub);
        }
        club
    }

    /// Runs a club change in the database; its outcome comes back to
    /// [`WorldSim::club_done`] for `actor` (who hears a refusal).
    fn club_job<F, Fut>(&mut self, actor: i64, job: F)
    where
        F: FnOnce(PgPool) -> Fut + Send + 'static,
        Fut: Future<Output = Done> + Send + 'static,
    {
        self.ext_jobs.push(Box::new(move |pool| {
            Box::pin(async move {
                let done = job(pool).await;
                ExtDone::Run(Box::new(move |ctx| {
                    let now = ctx.now();
                    ctx.sim.club_done(actor, done, now);
                }))
            })
        }));
    }

    /// A change came back from the database.
    pub(super) fn club_done(&mut self, actor: i64, done: Done, _now: Instant) {
        match done {
            Ok((outcome, balance)) => {
                if let (Some(crowns), Some(i)) = (balance, self.index_of_character(actor)) {
                    self.send_msg(i, &ServerMsg::Wallet { crowns });
                }
                self.club_apply(outcome);
            }
            Err(e) => {
                if let StoreError::Db(db) = &e {
                    tracing::error!(character_id = actor, error = %db, "club change failed");
                }
                if let Some(i) = self.index_of_character(actor) {
                    self.club_error(i, e.code());
                }
            }
        }
    }

    /// Keeps the club as it is now and tells its online members (and those
    /// no longer in it): the club, then the notices.
    pub(super) fn club_apply(&mut self, outcome: Outcome) {
        let Outcome {
            club_id,
            club,
            mut before,
            notices,
        } = outcome;
        let now_in: Vec<i64> = club
            .as_ref()
            .map(|c| c.members.iter().map(|m| m.character_id).collect())
            .unwrap_or_default();
        if let Some(replaced) = self.clubs.apply(club_id, club) {
            before.extend(replaced);
            before.sort_unstable();
            before.dedup();
            // Those no longer in it are in no club now.
            let none = frame(&ServerMsg::ClubState { club: None });
            for c in before.iter().filter(|c| !now_in.contains(c)) {
                if self.clubs.of(*c).is_none()
                    && let Some(j) = self.index_of_character(*c)
                {
                    self.notice(j, none.clone());
                }
            }
            self.send_club(club_id);
        }
        let mut told: Vec<i64> = before;
        told.extend(&now_in);
        told.sort_unstable();
        told.dedup();
        for notice in notices {
            let f = frame(&ServerMsg::ClubNotice { notice });
            for &c in &told {
                if let Some(j) = self.index_of_character(c) {
                    self.notice(j, f.clone());
                }
            }
        }
        self.club_drop_if_idle(club_id);
    }

    /// Whether any member of club `id` is online.
    fn club_has_online(&self, id: i64) -> bool {
        self.clubs.get(id).is_some_and(|c| {
            c.members
                .iter()
                .any(|m| self.index_of_character(m.character_id).is_some())
        })
    }

    fn club_drop_if_idle(&mut self, id: i64) {
        if self.clubs.get(id).is_some() && !self.club_has_online(id) {
            self.clubs.drop_copy(id);
        }
    }

    /// The club as its members see it.
    fn club_view(&self, c: &Club) -> ClubView {
        ClubView {
            id: c.id,
            name: c.name.clone(),
            tag: c.tag.clone(),
            motd: c.motd.clone(),
            members: c
                .ordered()
                .map(|m| {
                    let online = self.index_of_character(m.character_id);
                    ClubMember {
                        character_id: m.character_id,
                        name: m.name.clone(),
                        house: m.house,
                        level: online.map_or(m.level, |i| self.ents.info[i].level),
                        role: m.role,
                        online: online.is_some(),
                    }
                })
                .collect(),
        }
    }

    /// The online members of club `id` (dense indices).
    fn club_online_indices(&self, id: i64) -> Vec<usize> {
        self.clubs
            .get(id)
            .map(|c| {
                c.members
                    .iter()
                    .filter_map(|m| self.index_of_character(m.character_id))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Sends club `id` to every online member.
    fn send_club(&mut self, id: i64) {
        let Some(c) = self.clubs.get(id) else {
            return;
        };
        let f = frame(&ServerMsg::ClubState {
            club: Some(self.club_view(c)),
        });
        for j in self.club_online_indices(id) {
            self.notice(j, f.clone());
        }
    }

    /// Tells the other online members of `character_id`'s club where it
    /// stands now.
    fn club_presence(&mut self, character_id: i64, online: bool, level: u8) {
        let Some(id) = self.clubs.of(character_id).map(|c| c.id) else {
            return;
        };
        let f = frame(&ServerMsg::ClubPresence {
            character_id,
            online,
            level,
        });
        for j in self.club_online_indices(id) {
            if self.ents.info[j].character_id != character_id {
                self.notice(j, f.clone());
            }
        }
    }

    /// Character `i` joined the world with its club as the gateway read it
    /// (`Joining::club`): the copy here is kept unless older, it gets its
    /// club and the others see it online.
    pub(super) fn club_online(&mut self, i: usize, loaded: Option<Club>) {
        let info = &self.ents.info[i];
        let (cid, level) = (info.character_id, info.level);
        if let Some(club) = loaded {
            self.clubs.apply(club.id, Some(club));
        }
        let Some(c) = self.clubs.of(cid) else {
            return;
        };
        let view = self.club_view(c);
        self.send_msg(i, &ServerMsg::ClubState { club: Some(view) });
        self.club_presence(cid, true, level);
    }

    /// A character left the world (not a connection taking over): the
    /// others see it offline, and a club with nobody online is dropped.
    pub(super) fn club_offline(&mut self, character_id: i64, level: u8) {
        let Some(id) = self.clubs.of(character_id).map(|c| c.id) else {
            return;
        };
        self.clubs.set_level(character_id, level);
        self.club_presence(character_id, false, level);
        self.club_drop_if_idle(id);
    }

    /// Character `i`'s level changed.
    pub(super) fn club_level(&mut self, i: usize) {
        let info = &self.ents.info[i];
        let (cid, level) = (info.character_id, info.level);
        if self.clubs.set_level(cid, level) {
            self.club_presence(cid, true, level);
        }
    }

    /// The HTTP side changed club `id` (a member's character was deleted):
    /// reload it and tell its members `notices`.
    pub(super) fn club_changed(&mut self, id: i64, notices: Vec<ClubNotice>) {
        if self.clubs.get(id).is_none() {
            // Nobody in it is online: the next one to come online loads it.
            return;
        }
        let before: Vec<i64> = self
            .clubs
            .get(id)
            .map(|c| c.members.iter().map(|m| m.character_id).collect())
            .unwrap_or_default();
        self.ext_jobs.push(Box::new(move |pool| {
            Box::pin(async move {
                let loaded = store::load(&pool, id).await;
                ExtDone::Run(Box::new(move |ctx| match loaded {
                    Ok(club) => ctx.sim.club_apply(Outcome {
                        club_id: id,
                        club,
                        before,
                        notices,
                    }),
                    Err(e) => tracing::error!(club = id, error = %e, "reloading a club failed"),
                }))
            })
        }));
    }

    /// The online members of character `i`'s club (dense indices, `i`
    /// included), for club chat; `None` outside a club.
    pub(super) fn club_chat_recipients(&self, i: usize) -> Option<Vec<usize>> {
        let c = self.clubs.of(self.ents.info[i].character_id)?;
        Some(self.club_online_indices(c.id))
    }

    // ---------------------------------------------------------------- actions

    /// Any `ClientMsg::Club*`.
    pub(super) fn on_club_input(&mut self, i: usize, at: Instant, input: ClubInput) {
        match input {
            ClubInput::Found {
                secretary,
                name,
                tag,
            } => self.on_club_found(i, at, secretary, &name, &tag),
            ClubInput::Invite(name) => self.on_club_invite(i, at, &name),
            ClubInput::Respond { invite_id, accept } => {
                self.on_club_respond(i, at, invite_id, accept)
            }
            ClubInput::Leave => self.on_club_leave(i, at),
            ClubInput::Kick(target) => self.on_club_kick(i, at, target),
            ClubInput::SetRole { character_id, role } => {
                self.on_club_set_role(i, at, character_id, role)
            }
            ClubInput::Transfer(target) => self.on_club_transfer(i, at, target),
            ClubInput::Rename { name, tag } => self.on_club_rename(i, at, &name, &tag),
            ClubInput::SetMotd(motd) => self.on_club_set_motd(i, at, &motd),
            ClubInput::Disband => self.on_club_disband(i, at),
        }
    }

    /// `ClientMsg::ClubFound`: at the secretary's desk, levelled enough, in
    /// no club, a good name and tag; the Crowns are the database's to check.
    pub(super) fn on_club_found(
        &mut self,
        i: usize,
        at: Instant,
        secretary: EntityId,
        name: &str,
        tag: &str,
    ) {
        if !self.club_action(i, at) {
            return;
        }
        let rules = clubs::rules();
        let me = self.ents.info[i].character_id;
        let reach = crate::catalog::shipped().npcs.roster.rules.talk_reach_m;
        let at_desk = self.residents.resident(secretary) == Some(rules.secretary.as_str())
            && self.ents.index_of(secretary).is_some_and(|s| {
                self.ents.zone[s] == self.ents.zone[i]
                    && super::world::dist2(self.ents.pos[s], self.ents.pos[i]) <= reach * reach
            });
        if !at_desk {
            return self.club_error(i, ClubErrorCode::TooFar);
        }
        if self.clubs.of(me).is_some() {
            return self.club_error(i, ClubErrorCode::AlreadyInClub);
        }
        if self.ents.info[i].level < rules.found_level {
            return self.club_error(i, ClubErrorCode::LevelTooLow);
        }
        let cleaned = clubs::clean_name(name, rules, &self.filter)
            .and_then(|n| Ok((n, clubs::clean_tag(tag, rules, &self.filter)?)));
        let (name, tag) = match cleaned {
            Ok(v) => v,
            Err(code) => return self.club_error(i, code),
        };
        self.club_job(me, move |pool| async move {
            let (outcome, balance) = store::found(&pool, clubs::rules(), me, &name, &tag).await?;
            Ok((outcome, Some(balance)))
        });
    }

    /// `ClientMsg::ClubInvite`.
    pub(super) fn on_club_invite(&mut self, i: usize, at: Instant, name: &str) {
        if !self.club_action(i, at) {
            return;
        }
        let Some(club) = self.my_club(i) else {
            return;
        };
        let me = self.ents.info[i].character_id;
        if let Err(code) = club.check_invite(me, clubs::rules().members_max) {
            return self.club_error(i, code);
        }
        let target = self
            .by_name
            .get(&crate::characters::name_key(name.trim()))
            .and_then(|&id| self.ents.index_of(id))
            .filter(|&t| self.ents.info[t].npc.is_none());
        let Some(t) = target else {
            return self.club_error(i, ClubErrorCode::NotOnline);
        };
        if t == i {
            return self.club_error(i, ClubErrorCode::SelfInvite);
        }
        let them = self.ents.info[t].character_id;
        // Someone who blocked the sender looks offline to them.
        if self.ents.social[t].has_blocked(me) {
            return self.club_error(i, ClubErrorCode::NotOnline);
        }
        if self.ents.social[i].has_blocked(them) {
            return self.club_error(i, ClubErrorCode::Blocked);
        }
        if self.clubs.of(them).is_some() {
            return self.club_error(i, ClubErrorCode::InClub);
        }
        let invite = self.clubs.invite(club.id, me, them, at);
        let msg = ServerMsg::ClubInvited {
            invite_id: invite.id,
            club_name: club.name.clone(),
            club_tag: club.tag.clone(),
            from_name: self.ents.info[i].name.clone(),
            expires_in_secs: CLUB_INVITE_TTL_SECS,
        };
        self.notice(t, frame(&msg));
        let sent = ClubNotice::InviteSent {
            name: self.ents.info[t].name.clone(),
        };
        self.send_msg(i, &ServerMsg::ClubNotice { notice: sent });
    }

    /// `ClientMsg::ClubRespond`.
    pub(super) fn on_club_respond(&mut self, i: usize, at: Instant, invite_id: u32, accept: bool) {
        if !self.club_action(i, at) {
            return;
        }
        let me = self.ents.info[i].character_id;
        let Some(invite) = self.clubs.take_invite(invite_id, me, at) else {
            return self.club_error(i, ClubErrorCode::InviteExpired);
        };
        if !accept {
            if let Some(j) = self.index_of_character(invite.from) {
                let notice = ClubNotice::Declined {
                    name: self.ents.info[i].name.clone(),
                };
                self.notice(j, frame(&ServerMsg::ClubNotice { notice }));
            }
            return;
        }
        // Nobody blocked anyone since.
        if self.ents.social[i].any_block(invite.from) {
            return self.club_error(i, ClubErrorCode::InviteExpired);
        }
        if self.clubs.of(me).is_some() {
            return self.club_error(i, ClubErrorCode::AlreadyInClub);
        }
        let (club, from) = (invite.club, invite.from);
        self.club_job(me, move |pool| async move {
            Ok((
                store::join(&pool, clubs::rules(), club, from, me).await?,
                None,
            ))
        });
    }

    /// `ClientMsg::ClubLeave`.
    pub(super) fn on_club_leave(&mut self, i: usize, at: Instant) {
        if !self.club_action(i, at) || self.my_club(i).is_none() {
            return;
        }
        let me = self.ents.info[i].character_id;
        self.club_job(me, move |pool| async move {
            Ok((store::leave(&pool, me).await?, None))
        });
    }

    /// `ClientMsg::ClubKick`.
    pub(super) fn on_club_kick(&mut self, i: usize, at: Instant, target: i64) {
        if !self.club_action(i, at) {
            return;
        }
        let Some(club) = self.my_club(i) else {
            return;
        };
        let me = self.ents.info[i].character_id;
        if let Err(code) = club.check_kick(me, target) {
            return self.club_error(i, code);
        }
        self.club_job(me, move |pool| async move {
            Ok((store::kick(&pool, me, target).await?, None))
        });
    }

    /// `ClientMsg::ClubSetRole`.
    pub(super) fn on_club_set_role(&mut self, i: usize, at: Instant, target: i64, role: ClubRole) {
        if !self.club_action(i, at) {
            return;
        }
        let Some(club) = self.my_club(i) else {
            return;
        };
        let me = self.ents.info[i].character_id;
        if let Err(code) = club.check_set_role(me, target, role) {
            return self.club_error(i, code);
        }
        self.club_job(me, move |pool| async move {
            Ok((store::set_role(&pool, me, target, role).await?, None))
        });
    }

    /// `ClientMsg::ClubTransfer`.
    pub(super) fn on_club_transfer(&mut self, i: usize, at: Instant, target: i64) {
        if !self.club_action(i, at) {
            return;
        }
        let Some(club) = self.my_club(i) else {
            return;
        };
        let me = self.ents.info[i].character_id;
        if let Err(code) = club.check_transfer(me, target) {
            return self.club_error(i, code);
        }
        self.club_job(me, move |pool| async move {
            Ok((store::transfer(&pool, me, target).await?, None))
        });
    }

    /// `ClientMsg::ClubRename`.
    pub(super) fn on_club_rename(&mut self, i: usize, at: Instant, name: &str, tag: &str) {
        if !self.club_action(i, at) {
            return;
        }
        let Some(club) = self.my_club(i) else {
            return;
        };
        let me = self.ents.info[i].character_id;
        if let Err(code) = club.check_owner(me) {
            return self.club_error(i, code);
        }
        let rules = clubs::rules();
        let cleaned = clubs::clean_name(name, rules, &self.filter)
            .and_then(|n| Ok((n, clubs::clean_tag(tag, rules, &self.filter)?)));
        let (name, tag) = match cleaned {
            Ok(v) => v,
            Err(code) => return self.club_error(i, code),
        };
        self.club_job(me, move |pool| async move {
            Ok((store::rename(&pool, me, &name, &tag).await?, None))
        });
    }

    /// `ClientMsg::ClubSetMotd`.
    pub(super) fn on_club_set_motd(&mut self, i: usize, at: Instant, motd: &str) {
        if !self.club_action(i, at) {
            return;
        }
        let Some(club) = self.my_club(i) else {
            return;
        };
        let me = self.ents.info[i].character_id;
        if let Err(code) = club.check_motd(me) {
            return self.club_error(i, code);
        }
        let motd = match clubs::clean_motd(motd, clubs::rules(), &self.filter) {
            Ok(m) => m,
            Err(code) => return self.club_error(i, code),
        };
        self.club_job(me, move |pool| async move {
            Ok((store::set_motd(&pool, me, &motd).await?, None))
        });
    }

    /// `ClientMsg::ClubDisband`.
    pub(super) fn on_club_disband(&mut self, i: usize, at: Instant) {
        if !self.club_action(i, at) {
            return;
        }
        let Some(club) = self.my_club(i) else {
            return;
        };
        let me = self.ents.info[i].character_id;
        if let Err(code) = club.check_owner(me) {
            return self.club_error(i, code);
        }
        self.club_job(me, move |pool| async move {
            Ok((store::disband(&pool, me).await?, None))
        });
    }
}

#[cfg(test)]
#[path = "club_tests.rs"]
mod club_tests;
