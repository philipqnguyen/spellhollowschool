//! Parties (docs/architecture.md, "Parties"): up to `protocol::PARTY_MAX`
//! characters of any house, in the world's memory only (a restart disbands
//! them), keyed by character id, so a party follows a character whichever
//! account plays it and survives a relog.
//!
//! [`Parties`] holds the rules and the bookkeeping (no I/O, unit tested
//! below); the `WorldSim` methods at the end glue it to the world:
//!
//! - **Invitations**: any member (or someone in no party) may invite an
//!   online character in no party, by name, unless either blocked the other.
//!   An invitation lasts `PARTY_INVITE_TTL_SECS`; accepting joins the
//!   inviter's party, or starts one the inviter leads.
//! - **Leaving**: a member may leave; the leader may remove anyone and hand
//!   leadership to an online member. When the leader leaves (or goes
//!   offline while someone else is online) the next member in joining order
//!   leads, online members first. A party left with one member disbands.
//! - **Offline members** stay, shown offline, for [`OFFLINE_KEEP`], then
//!   leave.
//! - **What members get**: the whole party after every change
//!   (`ServerMsg::PartyState`, one frame for all of them, sent once per
//!   settle however many changes came), notices for the chat log
//!   (`PartyNotice`), party chat from anywhere (`World::route_chat`), and
//!   each other as *group* entities in their snapshots while in the same
//!   zone (`Client::party`): they rank ahead of every other priority player
//!   (visibility.rs), so a member is always drawn and on the map whatever
//!   the distance. Nobody outside the party gets any of it.
//!
//! Every message a party action sends to someone else goes through the
//! notice backlog (`WorldSim::notice`), so invitations cannot be used to
//! flood a player's queue.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use bytes::Bytes;
use protocol::{
    EntityId, House, PARTY_INVITE_TTL_SECS, PartyErrorCode, PartyMember, PartyNotice, PartyPlace,
    PartyPresence, PartyView, ServerMsg, encode_server,
};

use super::instances::FeatureKind;
use super::world::WorldSim;

/// The most members of one party.
pub const PARTY_MAX: usize = protocol::PARTY_MAX as usize;
/// How long an invitation may be accepted.
pub const INVITE_TTL: Duration = Duration::from_secs(PARTY_INVITE_TTL_SECS as u64);
/// Invitations one character may have out at once (a newer one drops the
/// oldest), and invitations waiting for one character at once (likewise).
pub const INVITES_PER_SENDER: usize = 8;
pub const INVITES_PER_TARGET: usize = 8;
/// An offline member stays in the party this long, then leaves it.
pub const OFFLINE_KEEP: Duration = Duration::from_secs(30 * 60);

pub type PartyId = u32;

/// One member as the party remembers it (also while offline).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Member {
    pub character_id: i64,
    pub name: String,
    pub house: House,
    /// The last level known.
    pub level: u8,
    /// `Some` while offline: since when.
    pub offline_since: Option<Instant>,
}

#[derive(Debug, Clone)]
pub struct Party {
    pub id: PartyId,
    pub leader: i64,
    /// In joining order (the leader anywhere among them).
    pub members: Vec<Member>,
}

impl Party {
    fn position(&self, character_id: i64) -> Option<usize> {
        self.members
            .iter()
            .position(|m| m.character_id == character_id)
    }

    pub fn member(&self, character_id: i64) -> Option<&Member> {
        self.members.iter().find(|m| m.character_id == character_id)
    }

    /// The members as the list shows them: the leader first, then the others
    /// in joining order.
    pub fn ordered(&self) -> impl Iterator<Item = &Member> {
        let leader = self.member(self.leader);
        leader.into_iter().chain(
            self.members
                .iter()
                .filter(move |m| m.character_id != self.leader),
        )
    }

    /// Who leads after `except`: the first other member online, in joining
    /// order, else the first other member.
    fn next_leader(&self, except: i64) -> Option<i64> {
        let others = || self.members.iter().filter(|m| m.character_id != except);
        others()
            .find(|m| m.offline_since.is_none())
            .or_else(|| others().next())
            .map(|m| m.character_id)
    }
}

/// An invitation from `from` (a character) to `to`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Invite {
    pub id: u32,
    pub from: i64,
    pub to: i64,
    pub until: Instant,
}

/// Every party and invitation. Changes queue what members must be told:
/// [`Parties::take_dirty`] (parties to send again), [`Parties::take_gone`]
/// (characters now in no party) and [`Parties::take_notices`].
#[derive(Debug, Default)]
pub struct Parties {
    parties: HashMap<PartyId, Party>,
    of: HashMap<i64, PartyId>,
    invites: Vec<Invite>,
    next_party: PartyId,
    next_invite: u32,
    dirty: Vec<PartyId>,
    gone: Vec<i64>,
    notices: Vec<(i64, PartyNotice)>,
}

impl Parties {
    pub fn len(&self) -> usize {
        self.parties.len()
    }

    pub fn is_empty(&self) -> bool {
        self.parties.is_empty()
    }

    pub fn invites(&self) -> &[Invite] {
        &self.invites
    }

    pub fn get(&self, id: PartyId) -> Option<&Party> {
        self.parties.get(&id)
    }

    /// The party a character is in.
    pub fn of(&self, character_id: i64) -> Option<&Party> {
        self.parties.get(self.of.get(&character_id)?)
    }

    /// Records an invitation from `from` to `to` (both online, no block:
    /// the world checked). A newer one between the same two replaces the
    /// older.
    pub fn invite(&mut self, from: i64, to: i64, now: Instant) -> Result<Invite, PartyErrorCode> {
        if from == to {
            return Err(PartyErrorCode::SelfInvite);
        }
        if self.of.contains_key(&to) {
            return Err(PartyErrorCode::InParty);
        }
        if self.of(from).is_some_and(|p| p.members.len() >= PARTY_MAX) {
            return Err(PartyErrorCode::Full);
        }
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
            from,
            to,
            until: now + INVITE_TTL,
        };
        self.invites.push(invite);
        Ok(invite)
    }

    /// Takes invitation `id` addressed to `to`, if it is still valid.
    pub fn take_invite(&mut self, id: u32, to: i64, now: Instant) -> Option<Invite> {
        let k = self.invites.iter().position(|i| i.id == id && i.to == to)?;
        let invite = self.invites.remove(k);
        (invite.until > now).then_some(invite)
    }

    /// `joiner` accepted an invitation from `inviter` (both online, no
    /// block): joins the inviter's party, or starts one the inviter leads.
    pub fn join(&mut self, inviter: Member, joiner: Member) -> Result<PartyId, PartyErrorCode> {
        if self.of.contains_key(&joiner.character_id) {
            return Err(PartyErrorCode::InParty);
        }
        let id = match self.of.get(&inviter.character_id) {
            Some(&id) => {
                let p = self.parties.get_mut(&id).expect("indexed party exists");
                if p.members.len() >= PARTY_MAX {
                    return Err(PartyErrorCode::Full);
                }
                p.members.push(joiner.clone());
                id
            }
            None => {
                self.next_party = self.next_party.wrapping_add(1).max(1);
                let id = self.next_party;
                self.of.insert(inviter.character_id, id);
                self.parties.insert(
                    id,
                    Party {
                        id,
                        leader: inviter.character_id,
                        members: vec![inviter, joiner.clone()],
                    },
                );
                id
            }
        };
        self.of.insert(joiner.character_id, id);
        self.tell_all(
            id,
            &PartyNotice::Joined {
                name: joiner.name.clone(),
            },
        );
        self.mark(id);
        Ok(id)
    }

    /// `character_id` leaves its party (no-op outside one).
    pub fn leave(&mut self, character_id: i64) -> Result<(), PartyErrorCode> {
        self.remove(character_id, |name| PartyNotice::Left { name })
    }

    /// The leader `by` removes `target`.
    pub fn kick(&mut self, by: i64, target: i64) -> Result<(), PartyErrorCode> {
        let p = self.of(by).ok_or(PartyErrorCode::NotInParty)?;
        if p.leader != by {
            return Err(PartyErrorCode::NotLeader);
        }
        if target == by || p.member(target).is_none() {
            return Err(PartyErrorCode::NotMember);
        }
        self.remove(target, |name| PartyNotice::Removed { name })
    }

    /// The leader `by` hands leadership to `target` (`online`: whether
    /// they are).
    pub fn promote(&mut self, by: i64, target: i64, online: bool) -> Result<(), PartyErrorCode> {
        let p = self.of(by).ok_or(PartyErrorCode::NotInParty)?;
        if p.leader != by {
            return Err(PartyErrorCode::NotLeader);
        }
        if p.member(target).is_none() {
            return Err(PartyErrorCode::NotMember);
        }
        if target == by {
            return Ok(());
        }
        if !online {
            return Err(PartyErrorCode::NotOnline);
        }
        let id = p.id;
        self.set_leader(id, target);
        Ok(())
    }

    /// A member came online (`level` now) or went offline. An offline
    /// leader hands over to the next member online, if any.
    pub fn set_online(&mut self, character_id: i64, online: bool, level: u8, now: Instant) {
        let Some(&id) = self.of.get(&character_id) else {
            return;
        };
        let p = self.parties.get_mut(&id).expect("indexed party exists");
        let k = p.position(character_id).expect("indexed member");
        let m = &mut p.members[k];
        m.level = level;
        m.offline_since = if online {
            None
        } else {
            m.offline_since.or(Some(now))
        };
        if !online
            && p.leader == character_id
            && let Some(next) = p.next_leader(character_id)
            && p.member(next).is_some_and(|m| m.offline_since.is_none())
        {
            self.set_leader(id, next);
        }
        self.mark(id);
    }

    /// A member's level changed.
    pub fn set_level(&mut self, character_id: i64, level: u8) {
        let Some(&id) = self.of.get(&character_id) else {
            return;
        };
        let p = self.parties.get_mut(&id).expect("indexed party exists");
        if let Some(k) = p.position(character_id)
            && p.members[k].level != level
        {
            p.members[k].level = level;
            self.mark(id);
        }
    }

    /// Something members see about `character_id` changed (where it is):
    /// its party is sent again.
    pub fn touch(&mut self, character_id: i64) {
        if let Some(&id) = self.of.get(&character_id) {
            self.mark(id);
        }
    }

    /// Once a second: drops lapsed invitations and members offline for
    /// longer than [`OFFLINE_KEEP`].
    pub fn expire(&mut self, now: Instant) {
        self.invites.retain(|i| i.until > now);
        let stale: Vec<i64> = self
            .parties
            .values()
            .flat_map(|p| &p.members)
            .filter(|m| {
                m.offline_since
                    .is_some_and(|t| now.saturating_duration_since(t) >= OFFLINE_KEEP)
            })
            .map(|m| m.character_id)
            .collect();
        for c in stale {
            let _ = self.leave(c);
        }
    }

    pub fn take_dirty(&mut self) -> Vec<PartyId> {
        let mut d = std::mem::take(&mut self.dirty);
        d.sort_unstable();
        d.dedup();
        d.retain(|id| self.parties.contains_key(id));
        d
    }

    pub fn take_gone(&mut self) -> Vec<i64> {
        std::mem::take(&mut self.gone)
    }

    pub fn take_notices(&mut self) -> Vec<(i64, PartyNotice)> {
        std::mem::take(&mut self.notices)
    }

    fn mark(&mut self, id: PartyId) {
        if !self.dirty.contains(&id) {
            self.dirty.push(id);
        }
    }

    fn tell_all(&mut self, id: PartyId, notice: &PartyNotice) {
        if let Some(p) = self.parties.get(&id) {
            for m in &p.members {
                self.notices.push((m.character_id, notice.clone()));
            }
        }
    }

    fn set_leader(&mut self, id: PartyId, leader: i64) {
        let Some(p) = self.parties.get_mut(&id) else {
            return;
        };
        let Some(name) = p.member(leader).map(|m| m.name.clone()) else {
            return;
        };
        p.leader = leader;
        self.tell_all(id, &PartyNotice::Leader { name });
        self.mark(id);
    }

    /// Takes `character_id` out of its party, telling everyone (it too) with
    /// `notice`; passes leadership on and disbands a party of one.
    fn remove(
        &mut self,
        character_id: i64,
        notice: impl FnOnce(String) -> PartyNotice,
    ) -> Result<(), PartyErrorCode> {
        let id = *self
            .of
            .get(&character_id)
            .ok_or(PartyErrorCode::NotInParty)?;
        let p = self.parties.get(&id).expect("indexed party exists");
        let name = p.member(character_id).expect("indexed member").name.clone();
        // Told while still listed, so the one leaving hears it too.
        self.tell_all(id, &notice(name));
        let p = self.parties.get_mut(&id).expect("indexed party exists");
        let was_leader = p.leader == character_id;
        let next = was_leader.then(|| p.next_leader(character_id)).flatten();
        p.members.retain(|m| m.character_id != character_id);
        self.of.remove(&character_id);
        self.gone.push(character_id);
        if p.members.len() <= 1 {
            let last: Vec<i64> = p.members.iter().map(|m| m.character_id).collect();
            self.parties.remove(&id);
            for c in last {
                self.of.remove(&c);
                self.gone.push(c);
                self.notices.push((c, PartyNotice::Disbanded));
            }
            return Ok(());
        }
        if let Some(next) = next {
            self.set_leader(id, next);
        }
        self.mark(id);
        Ok(())
    }
}

/// The frame of a party notice.
fn notice_frame(notice: PartyNotice) -> Bytes {
    encode_server(&ServerMsg::PartyNotice { notice }).into()
}

impl WorldSim {
    /// What the party remembers of online character `i`.
    fn party_member(&self, i: usize) -> Member {
        let info = &self.ents.info[i];
        Member {
            character_id: info.character_id,
            name: info.name.clone(),
            house: self.ents.house[i],
            level: info.level,
            offline_since: None,
        }
    }

    fn party_error(&mut self, i: usize, code: PartyErrorCode) {
        self.send_msg(i, &ServerMsg::PartyError { code });
    }

    /// Takes a token from the action bucket, or refuses (`RateLimited`).
    fn party_action(&mut self, i: usize, at: Instant) -> bool {
        let ok = self.ents.presence[i].actions.take(
            at,
            super::limits::ACTION_BURST,
            super::limits::ACTION_PER_SECOND,
        );
        if !ok {
            self.party_error(i, PartyErrorCode::RateLimited);
        }
        ok
    }

    /// `ClientMsg::PartyInvite`.
    pub(super) fn on_party_invite(&mut self, i: usize, at: Instant, name: &str) {
        if !self.party_action(i, at) {
            return;
        }
        let me = self.ents.info[i].character_id;
        let target = self
            .by_name
            .get(&crate::characters::name_key(name.trim()))
            .and_then(|&id| self.ents.index_of(id))
            .filter(|&t| self.ents.info[t].npc.is_none());
        let Some(t) = target else {
            return self.party_error(i, PartyErrorCode::NotOnline);
        };
        let them = self.ents.info[t].character_id;
        if t == i {
            return self.party_error(i, PartyErrorCode::SelfInvite);
        }
        // Someone who blocked the sender looks offline to them.
        if self.ents.social[t].has_blocked(me) {
            return self.party_error(i, PartyErrorCode::NotOnline);
        }
        if self.ents.social[i].has_blocked(them) {
            return self.party_error(i, PartyErrorCode::Blocked);
        }
        match self.parties.invite(me, them, at) {
            Ok(invite) => {
                let msg = ServerMsg::PartyInvited {
                    invite_id: invite.id,
                    from_name: self.ents.info[i].name.clone(),
                    from_house: self.ents.house[i],
                    from_level: self.ents.info[i].level,
                    expires_in_secs: PARTY_INVITE_TTL_SECS,
                };
                self.notice(t, encode_server(&msg).into());
                let sent = PartyNotice::InviteSent {
                    name: self.ents.info[t].name.clone(),
                };
                self.send(i, notice_frame(sent));
            }
            Err(code) => self.party_error(i, code),
        }
    }

    /// `ClientMsg::PartyRespond`.
    pub(super) fn on_party_respond(&mut self, i: usize, at: Instant, invite_id: u32, accept: bool) {
        if !self.party_action(i, at) {
            return;
        }
        let me = self.ents.info[i].character_id;
        let Some(invite) = self.parties.take_invite(invite_id, me, at) else {
            return self.party_error(i, PartyErrorCode::InviteExpired);
        };
        let inviter = self.index_of_character(invite.from);
        if !accept {
            if let Some(j) = inviter {
                let name = self.ents.info[i].name.clone();
                self.notice(j, notice_frame(PartyNotice::Declined { name }));
            }
            return;
        }
        // The inviter must still be online, and nobody blocked anyone since.
        let Some(j) = inviter.filter(|_| !self.ents.social[i].any_block(invite.from)) else {
            return self.party_error(i, PartyErrorCode::NotOnline);
        };
        let (host, guest) = (self.party_member(j), self.party_member(i));
        if let Err(code) = self.parties.join(host, guest) {
            self.party_error(i, code);
        }
    }

    /// `ClientMsg::PartyLeave`.
    pub(super) fn on_party_leave(&mut self, i: usize, at: Instant) {
        if !self.party_action(i, at) {
            return;
        }
        if let Err(code) = self.parties.leave(self.ents.info[i].character_id) {
            self.party_error(i, code);
        }
    }

    /// `ClientMsg::PartyKick`.
    pub(super) fn on_party_kick(&mut self, i: usize, at: Instant, target: i64) {
        if !self.party_action(i, at) {
            return;
        }
        if let Err(code) = self.parties.kick(self.ents.info[i].character_id, target) {
            self.party_error(i, code);
        }
    }

    /// `ClientMsg::PartyPromote`.
    pub(super) fn on_party_promote(&mut self, i: usize, at: Instant, target: i64) {
        if !self.party_action(i, at) {
            return;
        }
        let online = self.index_of_character(target).is_some();
        if let Err(code) = self
            .parties
            .promote(self.ents.info[i].character_id, target, online)
        {
            self.party_error(i, code);
        }
    }

    /// Character `i` joined the world (a relog, or a new connection taking
    /// over): its party learns it is online, and it gets the party.
    pub(super) fn party_online(&mut self, i: usize, now: Instant) {
        let info = &self.ents.info[i];
        self.parties
            .set_online(info.character_id, true, info.level, now);
    }

    /// A character left the world (not a connection taking over).
    pub(super) fn party_offline(&mut self, character_id: i64, level: u8, now: Instant) {
        self.parties.set_online(character_id, false, level, now);
    }

    /// Character `i` changed zone or place: its party sees where.
    pub(super) fn party_moved(&mut self, i: usize) {
        self.parties.touch(self.ents.info[i].character_id);
    }

    /// Character `i`'s level changed.
    pub(super) fn party_level(&mut self, i: usize) {
        let info = &self.ents.info[i];
        self.parties.set_level(info.character_id, info.level);
    }

    /// The online members of a party (dense indices), in its list order.
    fn party_online_indices(&self, p: &Party) -> Vec<usize> {
        p.ordered()
            .filter_map(|m| self.index_of_character(m.character_id))
            .collect()
    }

    /// Where online character `i` is, for its party.
    fn party_presence(&self, i: usize) -> PartyPresence {
        let zone = self.ents.zone[i];
        let place = if let Some(f) = self.instances.feature(zone) {
            match &f.kind {
                FeatureKind::Class { class, .. } => PartyPlace::Class {
                    class: class.clone(),
                },
                FeatureKind::Arena { venue, .. } => PartyPlace::Arena {
                    venue: venue.clone(),
                },
            }
        } else if let Some(d) = self.instances.get(zone) {
            match d.wishing_room {
                Some(_) => PartyPlace::WishingRoom {
                    owner_name: d.owner_name.clone(),
                },
                None => PartyPlace::Dorm {
                    owner_name: d.owner_name.clone(),
                    house: d.house,
                },
            }
        } else {
            match self.location_of(i) {
                protocol::Location::Castle { place } => PartyPlace::Castle { place },
                // Unreachable for the castle zone; named as the castle anyway.
                _ => PartyPlace::Castle {
                    place: String::new(),
                },
            }
        };
        PartyPresence {
            entity: self.ents.ids[i],
            zone,
            place,
        }
    }

    /// The party as its members see it.
    fn party_view(&self, p: &Party) -> PartyView {
        PartyView {
            leader: p.leader,
            members: p
                .ordered()
                .map(|m| {
                    let online = self.index_of_character(m.character_id);
                    PartyMember {
                        character_id: m.character_id,
                        name: m.name.clone(),
                        house: m.house,
                        level: online.map_or(m.level, |i| self.ents.info[i].level),
                        online: online.map(|i| self.party_presence(i)),
                    }
                })
                .collect(),
        }
    }

    /// Sends what the party changes since the last settle mean: `PartyState
    /// { None }` to whoever is in no party any more, the new party to every
    /// online member of a changed one (and their group entities), then the
    /// notices.
    pub(super) fn flush_parties(&mut self) {
        let gone = self.parties.take_gone();
        let dirty = self.parties.take_dirty();
        let notices = self.parties.take_notices();
        if gone.is_empty() && dirty.is_empty() && notices.is_empty() {
            return;
        }
        let none: Bytes = encode_server(&ServerMsg::PartyState { party: None }).into();
        for cid in gone {
            if self.parties.of(cid).is_some() {
                continue;
            }
            if let Some(i) = self.index_of_character(cid) {
                self.ents.client[i].party.clear();
                self.notice(i, none.clone());
            }
        }
        for id in dirty {
            let Some(p) = self.parties.get(id) else {
                continue;
            };
            let frame: Bytes = encode_server(&ServerMsg::PartyState {
                party: Some(self.party_view(p)),
            })
            .into();
            let online = self.party_online_indices(p);
            let ids: Vec<EntityId> = online.iter().map(|&j| self.ents.ids[j]).collect();
            for &j in &online {
                let me = self.ents.ids[j];
                let c = &mut self.ents.client[j];
                c.party.clear();
                c.party.extend(ids.iter().copied().filter(|&e| e != me));
                self.notice(j, frame.clone());
            }
        }
        for (cid, notice) in notices {
            if let Some(i) = self.index_of_character(cid) {
                self.notice(i, notice_frame(notice));
            }
        }
    }

    /// The online members of character `i`'s party (dense indices, `i`
    /// included), for party chat; `None` outside a party.
    pub(super) fn party_chat_recipients(&self, i: usize) -> Option<Vec<usize>> {
        let p = self.parties.of(self.ents.info[i].character_id)?;
        Some(self.party_online_indices(p))
    }
}

#[cfg(test)]
#[path = "party_tests.rs"]
mod party_tests;
