//! Friends in the world (docs/architecture.md, "Network"):
//!
//! - online friends in the same zone are priority players (always visible,
//!   counted in the view limit), through each client's `online_friends`;
//! - friends hear `FriendEvent::Online` when a character joins, `Moved` when
//!   it changes zone and `Offline` when it leaves (not when a second login
//!   simply replaces the first);
//! - `WorldHandle::friends_changed` reloads a character's friends and blocks
//!   from the database ([`Job::LoadSocial`]); a new friend who is online gets
//!   both of them `Online`, and guests who are no longer welcome leave the room.
//!
//! Friendships are symmetric in the database (two rows), and the HTTP side
//! reports a change for both characters, so each side's reload updates its
//! own list and announces to itself; the priority lists are kept in step both
//! ways by whichever reload lands first.

use std::time::Instant;

use protocol::{EntityId, FriendEvent};

use super::entities::{CASTLE, Social};
use super::jobs::Job;
use super::world::{WorldSim, friend_frame};

impl WorldSim {
    /// Online friends of character `i` (dense indices).
    fn online_friend_indices(&self, i: usize) -> Vec<usize> {
        self.ents.social[i]
            .friends
            .iter()
            .filter_map(|&f| self.index_of_character(f))
            .filter(|&j| j != i)
            .collect()
    }

    /// A character just joined: fills its priority list with its online
    /// friends, adds it to theirs, and (`announce`) tells them it is online.
    pub(super) fn connect_friends(&mut self, i: usize, announce: bool) {
        let id = self.ents.ids[i];
        let cid = self.ents.info[i].character_id;
        let friends = self.online_friend_indices(i);
        let frame = announce.then(|| {
            friend_frame(FriendEvent::Online {
                character_id: cid,
                location: self.location_of(i),
                room_open: self.ents.presence[i].dorm_open,
            })
        });
        let mut online = Vec::with_capacity(friends.len());
        for j in friends {
            online.push(self.ents.ids[j]);
            push_unique(&mut self.ents.client[j].online_friends, id);
            if let Some(f) = &frame {
                self.notice(j, f.clone());
            }
        }
        self.ents.client[i].online_friends = online;
    }

    /// A character is leaving: takes it out of its friends' priority lists
    /// and (`announce`) tells them it went offline.
    pub(super) fn disconnect_friends(&mut self, i: usize, announce: bool) {
        let id = self.ents.ids[i];
        let cid = self.ents.info[i].character_id;
        let frame = announce.then(|| friend_frame(FriendEvent::Offline { character_id: cid }));
        for j in self.online_friend_indices(i) {
            self.ents.client[j].online_friends.retain(|&e| e != id);
            if let Some(f) = &frame {
                self.notice(j, f.clone());
            }
        }
    }

    /// A character opened or closed its room: online friends learn whether
    /// they may visit (the friend list's Visit).
    pub(super) fn notify_friends_room_open(&mut self, i: usize) {
        let frame = friend_frame(FriendEvent::RoomOpen {
            character_id: self.ents.info[i].character_id,
            open: self.ents.presence[i].dorm_open,
        });
        for j in self.online_friend_indices(i) {
            self.notice(j, frame.clone());
        }
    }

    /// A character changed zone: its online friends learn where it is now.
    pub(super) fn notify_friends_moved(&mut self, i: usize) {
        let friends = self.online_friend_indices(i);
        if friends.is_empty() {
            return;
        }
        let frame = friend_frame(FriendEvent::Moved {
            character_id: self.ents.info[i].character_id,
            location: self.location_of(i),
        });
        for j in friends {
            self.notice(j, frame.clone());
        }
    }

    /// `WorldCmd::FriendsChanged`: reload an online character's friends; an
    /// offline one's change is remembered briefly in case it is joining.
    pub(super) fn friends_changed(&mut self, character_id: i64, now: Instant) {
        match self.index_of_character(character_id) {
            Some(i) => self.request_social(i),
            None => {
                self.social_changed.insert(character_id, now);
            }
        }
    }

    /// Queues a reload of character `i`'s friends and blocks.
    pub(super) fn request_social(&mut self, i: usize) {
        let p = &mut self.ents.presence[i];
        p.friends_seq += 1;
        self.jobs.push(Job::LoadSocial {
            character_id: self.ents.info[i].character_id,
            seq: p.friends_seq,
        });
    }

    /// A reload finished; only the newest one requested applies.
    pub(super) fn social_loaded(&mut self, character_id: i64, seq: u64, social: Option<Social>) {
        let Some(i) = self.index_of_character(character_id) else {
            return;
        };
        if seq != self.ents.presence[i].friends_seq {
            return;
        }
        match social {
            Some(s) => self.apply_social(i, s, Instant::now()),
            None => tracing::warn!(
                character_id,
                "reloading friends failed; keeping the old list"
            ),
        }
    }

    /// Replaces character `i`'s friends and blocks and acts on the difference.
    pub(super) fn apply_social(&mut self, i: usize, new: Social, now: Instant) {
        let new = new.normalized();
        let old = std::mem::replace(&mut self.ents.social[i], new);
        let id = self.ents.ids[i];
        let cid = self.ents.info[i].character_id;
        let new = &self.ents.social[i];
        let added: Vec<i64> = new
            .friends
            .iter()
            .copied()
            .filter(|f| !old.is_friend(*f))
            .collect();
        let removed: Vec<i64> = old
            .friends
            .iter()
            .copied()
            .filter(|f| !new.is_friend(*f))
            .collect();
        for f in added {
            let Some(j) = self.index_of_character(f).filter(|&j| j != i) else {
                continue;
            };
            let jid = self.ents.ids[j];
            push_unique(&mut self.ents.client[i].online_friends, jid);
            push_unique(&mut self.ents.client[j].online_friends, id);
            let frame = friend_frame(FriendEvent::Online {
                character_id: f,
                location: self.location_of(j),
                room_open: self.ents.presence[j].dorm_open,
            });
            self.notice(i, frame);
        }
        for f in removed {
            let Some(j) = self.index_of_character(f) else {
                continue;
            };
            let jid = self.ents.ids[j];
            self.ents.client[i].online_friends.retain(|&e| e != jid);
            self.ents.client[j].online_friends.retain(|&e| e != id);
        }

        // Guests in this character's room (or Wishing Room)
        // who are no longer welcome leave...
        let rooms = [
            self.instances.of_owner(cid),
            self.instances.of_wishing_room(cid),
        ];
        for z in rooms.into_iter().flatten() {
            let unwelcome: Vec<EntityId> = self
                .instances
                .members(z)
                .iter()
                .copied()
                .filter(|&m| m != id)
                .filter(|&m| {
                    self.ents.index_of(m).is_some_and(|j| {
                        let g = self.ents.info[j].character_id;
                        let s = &self.ents.social[i];
                        !s.is_friend(g) || s.any_block(g)
                    })
                })
                .collect();
            for m in unwelcome {
                if let Some(j) = self.ents.index_of(m) {
                    self.leave_dorm(j, now);
                }
            }
        }
        // ...and so does this character from a room whose owner it no longer
        // counts as a friend.
        if self.ents.zone[i] != CASTLE
            && let Some(d) = self.instances.get(self.ents.zone[i])
            && d.owner != cid
        {
            let owner = d.owner;
            let s = &self.ents.social[i];
            if !s.is_friend(owner) || s.any_block(owner) {
                self.leave_dorm(i, now);
            }
        }
    }
}

fn push_unique(list: &mut Vec<EntityId>, id: EntityId) {
    if !list.contains(&id) {
        list.push(id);
    }
}
