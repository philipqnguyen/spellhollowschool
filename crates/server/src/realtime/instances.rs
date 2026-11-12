//! Zones: the castle plus one dorm instance per owner, all in the world
//! thread (docs/architecture.md, "Zones" and "Dorms and decorating").
//!
//! - **Instances** ([`Instances`]): a dorm room is created when someone first
//!   enters it (its furniture loads from the database, then `DormState` goes
//!   to everyone inside) and torn down when it empties. It lives only while
//!   its owner is online: an owner going offline sends every guest back.
//! - **Transitions** ([`WorldSim::move_to_zone`]): the client gets
//!   `ZoneChanged` (and `DormState` for a dorm); its visible set and
//!   baselines reset on both ends; the resume point is saved at once; online
//!   friends get `FriendEvent::Moved`.
//! - **Doors**: `Interact` with your house's `dorm_door` in reach takes you
//!   home; `dorm_exit` or `LeaveInstance` takes an owner back to that door and
//!   a guest back to where they were ([`ReturnPoint`]).
//! - **Guests** (friends only, no blocks either way, at most
//!   [`DORM_GUESTS_MAX`]): by invitation ([`Invites`], two minutes) or, while
//!   the room is open to friends, by `DormVisit`. A guest is saved at their
//!   return point, never inside someone else's room; one who drops out and
//!   reconnects within [`GUEST_MEMO_TTL`] is put back in.
//! - **Furniture**: each owner's room operations (load, edits, open flag) run
//!   one at a time through a [`RoomQueue`], so the database work for a room
//!   is serialized and a load sees every earlier edit. The world checks what
//!   it can before an edit's job starts (owner, cap, placement of a move); the
//!   job checks the rest in its transaction (`dorm::store`).
//! - **Feature instances** ([`FeatureInstance`]): class
//!   sections and dueling arenas, opened by their handler through
//!   `WorldCtx::open_instance` with a layout from `realtime::layouts`.
//!   Players enter from the castle (`enter_feature`) and return to where
//!   they stood (a `ReturnPoint::Castle`, also their saved resume point);
//!   NPCs live in them (`spawn_npc`) and go with them. An instance closes at
//!   the next settle after its last player leaves (`FeatureEvent::Closing`),
//!   and its handler hears `on_instance_closed`. Each has [`ZoneRules`]
//!   (arenas: no potions or social casts, boosts suspended, a walking
//!   speed) and a priority list every member always sees.
//! - **Wishing Rooms** ([`super::wishing_room`]): a
//!   [`DormInstance`] with `wishing_room` set, one per opener while occupied
//!   (`Instances::of_wishing_room`), with no furniture and an open flag of
//!   its own (not saved). The dorm code serves it: invitations, open to
//!   friends, visits, kicks, the friend list's location.

use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use std::time::{Duration, Instant};

use protocol::{
    DORM_INVITE_TTL_SECS, DORM_ITEM_CAP, DORM_KICK_BAR_SECS, DormErrorCode, EntityId,
    FurnitureEdit, House, Location, PlacedFurniture, QPos, ServerMsg, WishingRoomKind, Zone,
    yaw_to_u8,
};
use sim_core::InteractableKind;

use super::entities::{CASTLE, ReturnPoint, ZoneId};
use super::jobs::{Edit, Job, RoomOutcome};
use super::limits;
use super::persist::SaveBatch;
use super::stations::StationTarget;
use super::world::WorldSim;
use crate::catalog;
use crate::characters::{Character, SavedPosition, SavedZone, name_key};
use crate::dorm::{layout_id, rules};

/// Guests a room holds besides its owner (twelve people in all).
pub const DORM_GUESTS_MAX: usize = 11;
/// A guest who disconnects inside a friend's room is put back in if they
/// reconnect within this time while the owner is still online.
pub const GUEST_MEMO_TTL: Duration = Duration::from_secs(120);
/// Room operations waiting per owner; beyond this, edits are refused as
/// `RateLimited`.
pub const ROOM_QUEUE_MAX: usize = 16;

/// Where a joining character starts.
pub(super) struct Start {
    pub zone: ZoneId,
    pub pos: [f32; 3],
    pub level: i32,
    pub yaw: u8,
    pub return_to: Option<ReturnPoint>,
}

/// Why a character leaves the world.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum Leaving {
    /// The connection ended: friends see them go, their guests are sent back.
    Disconnect,
    /// The same character signed in again and takes over at once.
    Replaced,
}

/// A guest who dropped out of a friend's room.
#[derive(Debug, Clone, Copy)]
pub struct GuestMemo {
    pub owner: i64,
    pub return_to: ReturnPoint,
    pub until: Instant,
}

/// One live dorm room.
#[derive(Debug)]
pub struct DormInstance {
    pub owner: i64,
    pub owner_name: String,
    pub house: House,
    pub world: Arc<sim_core::World>,
    /// Everyone inside (the owner too, when home).
    pub members: Vec<EntityId>,
    /// The placed furniture; `None` until the room has loaded.
    pub items: Option<Vec<PlacedFurniture>>,
    /// A Wishing Room (which room), not a dorm.
    pub wishing_room: Option<WishingRoomKind>,
    /// A Wishing Room's layout id and whether its opener
    /// opened it to friends (dorms keep theirs in the owner's presence).
    pub layout: String,
    pub open: bool,
}

/// What a feature's own instance is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FeatureKind {
    /// A section of a class session (`classes::world`).
    Class { class: String, section: u8 },
    /// A dueling arena (`dueling::world`).
    Arena { duel_id: u32, venue: String },
}

impl FeatureKind {
    /// The rules of a zone of this kind.
    pub fn rules(&self) -> ZoneRules {
        match self {
            FeatureKind::Class { .. } => ZoneRules::default(),
            FeatureKind::Arena { .. } => ZoneRules {
                potions: false,
                boosts: false,
                social_spells: false,
                move_speed_pct: Some(catalog::shipped().duel_rules.move_speed_pct),
            },
        }
    }
}

/// What a zone allows. The castle, dorms and classes allow
/// everything; arenas forbid drinking potions and social casting, suspend
/// Pick-Me-Up Tonic's speed and the Diminishing or Enlarging Draught's
/// scale, and set the walking speed (`data/dueling/rules.json`
/// `move_speed_pct`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ZoneRules {
    pub potions: bool,
    pub boosts: bool,
    pub social_spells: bool,
    pub move_speed_pct: Option<u16>,
}

impl Default for ZoneRules {
    fn default() -> Self {
        Self {
            potions: true,
            boosts: true,
            social_spells: true,
            move_speed_pct: None,
        }
    }
}

/// One live instance of a feature's own (a class section, an
/// arena). Its handler opens it (`WorldCtx::open_instance`); it closes when
/// its last player leaves, taking its NPCs with it.
#[derive(Debug)]
pub struct FeatureInstance {
    pub kind: FeatureKind,
    /// Instance layout id (`Zone::Class.layout`, `Zone::Arena.layout`).
    pub layout: String,
    pub world: Arc<sim_core::World>,
    /// Players inside.
    pub members: Vec<EntityId>,
    /// Its NPCs.
    pub npcs: Vec<EntityId>,
    /// Members and NPCs: everyone a member may see.
    seen: Vec<EntityId>,
    /// Who every member always sees (`WorldCtx::set_instance_priority`).
    pub priority: Vec<EntityId>,
    pub rules: ZoneRules,
}

impl FeatureInstance {
    fn refresh_seen(&mut self) {
        self.seen.clear();
        self.seen.extend_from_slice(&self.members);
        self.seen.extend_from_slice(&self.npcs);
    }
}

/// Every live instance (dorm rooms by zone id and by owner;
/// feature instances by zone id). Zone ids are shared and never reused
/// while live.
#[derive(Debug, Default)]
pub struct Instances {
    by_id: HashMap<ZoneId, DormInstance>,
    by_owner: HashMap<i64, ZoneId>,
    /// Each opener's Wishing Room.
    by_wishing_room: HashMap<i64, ZoneId>,
    features: HashMap<ZoneId, FeatureInstance>,
    last_id: ZoneId,
}

impl Instances {
    /// Live dorm rooms (Wishing Rooms not counted).
    pub fn len(&self) -> usize {
        self.by_id.len() - self.by_wishing_room.len()
    }

    /// Live Wishing Rooms.
    pub fn wishing_room_count(&self) -> usize {
        self.by_wishing_room.len()
    }

    /// The live Wishing Room of `owner` (its opener).
    pub fn of_wishing_room(&self, owner: i64) -> Option<ZoneId> {
        self.by_wishing_room.get(&owner).copied()
    }

    pub fn is_empty(&self) -> bool {
        self.by_id.is_empty()
    }

    pub fn get(&self, zone: ZoneId) -> Option<&DormInstance> {
        self.by_id.get(&zone)
    }

    pub fn get_mut(&mut self, zone: ZoneId) -> Option<&mut DormInstance> {
        self.by_id.get_mut(&zone)
    }

    /// The live room of `owner`.
    pub fn of_owner(&self, owner: i64) -> Option<ZoneId> {
        self.by_owner.get(&owner).copied()
    }

    /// The players in an instance zone, dorm or feature (empty for the
    /// castle or a closed instance).
    pub fn members(&self, zone: ZoneId) -> &[EntityId] {
        if let Some(d) = self.by_id.get(&zone) {
            return &d.members;
        }
        self.features.get(&zone).map_or(&[], |f| &f.members)
    }

    /// Everyone a member of `zone` may see: its players and NPCs.
    pub fn candidates(&self, zone: ZoneId) -> &[EntityId] {
        if let Some(d) = self.by_id.get(&zone) {
            return &d.members;
        }
        self.features.get(&zone).map_or(&[], |f| &f.seen)
    }

    /// Who every member of `zone` always sees (feature instances only).
    pub fn zone_priority(&self, zone: ZoneId) -> &[EntityId] {
        self.features.get(&zone).map_or(&[], |f| &f.priority)
    }

    pub fn feature(&self, zone: ZoneId) -> Option<&FeatureInstance> {
        self.features.get(&zone)
    }

    pub fn feature_mut(&mut self, zone: ZoneId) -> Option<&mut FeatureInstance> {
        self.features.get_mut(&zone)
    }

    /// Live feature instances.
    pub fn feature_count(&self) -> usize {
        self.features.len()
    }

    /// Live feature instances by kind: (class sections, arenas).
    pub fn feature_counts(&self) -> (usize, usize) {
        let classes = self
            .features
            .values()
            .filter(|f| matches!(f.kind, FeatureKind::Class { .. }))
            .count();
        (classes, self.features.len() - classes)
    }

    /// The walkable world of an instance zone (dorm or feature).
    pub fn world(&self, zone: ZoneId) -> Option<&Arc<sim_core::World>> {
        match self.by_id.get(&zone) {
            Some(d) => Some(&d.world),
            None => self.features.get(&zone).map(|f| &f.world),
        }
    }

    /// The rules of a zone (everything allowed outside feature instances).
    pub fn rules(&self, zone: ZoneId) -> ZoneRules {
        self.features
            .get(&zone)
            .map_or_else(ZoneRules::default, |f| f.rules)
    }

    fn next_id(&mut self) -> ZoneId {
        // Zone ids are never reused while an instance is live (0 is the castle).
        loop {
            self.last_id = self.last_id.wrapping_add(1);
            let id = self.last_id;
            if id != CASTLE && !self.by_id.contains_key(&id) && !self.features.contains_key(&id) {
                return id;
            }
        }
    }

    /// Opens a feature instance.
    pub fn create_feature(
        &mut self,
        kind: FeatureKind,
        layout: &str,
        world: Arc<sim_core::World>,
    ) -> ZoneId {
        let id = self.next_id();
        let rules = kind.rules();
        self.features.insert(
            id,
            FeatureInstance {
                kind,
                layout: layout.to_owned(),
                world,
                members: Vec::new(),
                npcs: Vec::new(),
                seen: Vec::new(),
                priority: Vec::new(),
                rules,
            },
        );
        id
    }

    pub fn add_npc(&mut self, zone: ZoneId, id: EntityId) {
        if let Some(f) = self.features.get_mut(&zone)
            && !f.npcs.contains(&id)
        {
            f.npcs.push(id);
            f.refresh_seen();
        }
    }

    pub fn remove_npc(&mut self, zone: ZoneId, id: EntityId) {
        if let Some(f) = self.features.get_mut(&zone) {
            f.npcs.retain(|&n| n != id);
            f.refresh_seen();
        }
    }

    /// Removes a feature instance nobody is in any more; it is handed back
    /// (its NPCs are the caller's to remove).
    pub fn take_feature_if_empty(&mut self, zone: ZoneId) -> Option<FeatureInstance> {
        if self
            .features
            .get(&zone)
            .is_some_and(|f| f.members.is_empty())
        {
            return self.features.remove(&zone);
        }
        None
    }

    fn create(
        &mut self,
        owner: i64,
        owner_name: &str,
        house: House,
        world: Arc<sim_core::World>,
    ) -> ZoneId {
        let id = self.next_id();
        self.by_id.insert(
            id,
            DormInstance {
                owner,
                owner_name: owner_name.to_owned(),
                house,
                world,
                members: Vec::new(),
                items: None,
                wishing_room: None,
                layout: layout_id(house),
                open: false,
            },
        );
        self.by_owner.insert(owner, id);
        id
    }

    /// Opens `owner`'s Wishing Room (closed to friends,
    /// no furniture).
    pub fn create_wishing_room(
        &mut self,
        owner: i64,
        owner_name: &str,
        house: House,
        room: WishingRoomKind,
        layout: &str,
        world: Arc<sim_core::World>,
    ) -> ZoneId {
        let id = self.next_id();
        self.by_id.insert(
            id,
            DormInstance {
                owner,
                owner_name: owner_name.to_owned(),
                house,
                world,
                members: Vec::new(),
                items: Some(Vec::new()),
                wishing_room: Some(room),
                layout: layout.to_owned(),
                open: false,
            },
        );
        self.by_wishing_room.insert(owner, id);
        id
    }

    pub fn add_member(&mut self, zone: ZoneId, id: EntityId) {
        if let Some(d) = self.by_id.get_mut(&zone)
            && !d.members.contains(&id)
        {
            d.members.push(id);
        }
        if let Some(f) = self.features.get_mut(&zone)
            && !f.members.contains(&id)
        {
            f.members.push(id);
            f.refresh_seen();
        }
    }

    pub fn remove_member(&mut self, zone: ZoneId, id: EntityId) {
        if let Some(d) = self.by_id.get_mut(&zone) {
            d.members.retain(|&m| m != id);
        }
        if let Some(f) = self.features.get_mut(&zone) {
            f.members.retain(|&m| m != id);
            f.refresh_seen();
        }
    }

    /// Closes a dorm zone nobody is in. True if it did.
    pub fn teardown_if_empty(&mut self, zone: ZoneId) -> bool {
        if self.by_id.get(&zone).is_some_and(|d| d.members.is_empty()) {
            let d = self.by_id.remove(&zone).expect("checked");
            if d.wishing_room.is_some() {
                self.by_wishing_room.remove(&d.owner);
            } else {
                self.by_owner.remove(&d.owner);
            }
            return true;
        }
        false
    }
}

/// Outstanding dorm invitations, at most one per (owner, guest).
#[derive(Debug, Default)]
pub struct Invites {
    by_id: HashMap<u32, Invite>,
    /// (owner, guest) → invitation id, so a new invitation replaces the old
    /// one without scanning everyone's.
    by_pair: HashMap<(i64, i64), u32>,
    last_id: u32,
}

#[derive(Debug, Clone, Copy)]
struct Invite {
    owner: i64,
    guest: i64,
    expires: Instant,
    /// Into the owner's Wishing Room, not their dorm.
    wishing_room: bool,
}

impl Invites {
    fn create(&mut self, owner: i64, guest: i64, now: Instant, wishing_room: bool) -> u32 {
        // A new invitation to the same guest replaces the old one.
        if let Some(old) = self.by_pair.remove(&(owner, guest)) {
            self.by_id.remove(&old);
        }
        loop {
            self.last_id = self.last_id.wrapping_add(1);
            if self.last_id != 0 && !self.by_id.contains_key(&self.last_id) {
                break;
            }
        }
        self.by_id.insert(
            self.last_id,
            Invite {
                owner,
                guest,
                expires: now + Duration::from_secs(u64::from(DORM_INVITE_TTL_SECS)),
                wishing_room,
            },
        );
        self.by_pair.insert((owner, guest), self.last_id);
        self.last_id
    }

    /// Uses up invitation `id` if it is for `guest`: the inviting owner, and
    /// whether it was still valid.
    fn take_any(&mut self, id: u32, guest: i64, now: Instant) -> Option<(i64, bool)> {
        let inv = *self.by_id.get(&id)?;
        if inv.guest != guest {
            return None;
        }
        self.by_id.remove(&id);
        self.by_pair.remove(&(inv.owner, inv.guest));
        Some((inv.owner, inv.expires > now))
    }

    /// Uses up invitation `id` if it is for `guest` and still valid; the
    /// inviting owner, and whether it is into their Wishing Room.
    fn take(&mut self, id: u32, guest: i64, now: Instant) -> Option<(i64, bool)> {
        let wishing_room = self.by_id.get(&id).is_some_and(|i| i.wishing_room);
        self.take_any(id, guest, now)
            .and_then(|(owner, valid)| valid.then_some((owner, wishing_room)))
    }

    pub fn expire(&mut self, now: Instant) {
        self.by_id.retain(|_, i| i.expires > now);
        let by_id = &self.by_id;
        self.by_pair.retain(|_, id| by_id.contains_key(id));
    }

    pub fn len(&self) -> usize {
        self.by_id.len()
    }

    pub fn is_empty(&self) -> bool {
        self.by_id.is_empty()
    }
}

/// One room operation waiting for (or in) its database job.
#[derive(Debug, Clone, PartialEq)]
pub enum RoomOp {
    Load,
    Place { item_id: i64, pos: QPos, yaw: u8 },
    Move { item_id: i64, pos: QPos, yaw: u8 },
    Remove { item_id: i64 },
    SetOpen(bool),
}

/// An owner's room operations, run one at a time.
#[derive(Debug, Default)]
pub struct RoomQueue {
    /// A job is in flight.
    pub busy: bool,
    /// The job in flight is a load.
    pub loading: bool,
    pub ops: VecDeque<RoomOp>,
}

impl WorldSim {
    // ------------------------------------------------------------ zones

    /// The protocol's name for a zone.
    pub(super) fn protocol_zone(&self, zone: ZoneId) -> Zone {
        if let Some(f) = self.instances.feature(zone) {
            return match &f.kind {
                FeatureKind::Class { class, section } => Zone::Class {
                    class: class.clone(),
                    section: *section,
                    layout: f.layout.clone(),
                },
                FeatureKind::Arena { duel_id, venue } => Zone::Arena {
                    duel_id: *duel_id,
                    venue: venue.clone(),
                    layout: f.layout.clone(),
                },
            };
        }
        match self.instances.get(zone) {
            Some(d) => match d.wishing_room {
                Some(room) => Zone::WishingRoom {
                    owner_name: d.owner_name.clone(),
                    room,
                    layout: d.layout.clone(),
                },
                None => Zone::Dorm {
                    owner_name: d.owner_name.clone(),
                    house: d.house,
                    layout: layout_id(d.house),
                },
            },
            None => Zone::Castle,
        }
    }

    /// Where a character is, for the friend list. A class or an arena is
    /// shown as a place in the castle ("Charms", "Dueling Club").
    pub(super) fn location_of(&self, i: usize) -> Location {
        if let Some(f) = self.instances.feature(self.ents.zone[i]) {
            let content = catalog::shipped();
            let place = match &f.kind {
                FeatureKind::Class { class, .. } => content
                    .schedule
                    .class(class)
                    .map_or_else(|| class.clone(), |c| c.name.clone()),
                FeatureKind::Arena { venue, .. } => content
                    .venues
                    .get(venue)
                    .map_or_else(|| venue.clone(), |v| v.name.clone()),
            };
            return Location::Castle { place };
        }
        match self.instances.get(self.ents.zone[i]) {
            Some(d) if d.wishing_room.is_some() => Location::WishingRoom {
                owner_name: d.owner_name.clone(),
            },
            Some(d) => Location::Dorm {
                owner_name: d.owner_name.clone(),
            },
            None => {
                // A rider is named by the ground below.
                let pos = self.ents.pos[i];
                let p = match self.ents.mover[i].mount {
                    Some(_) => sim_core::flight::landing_spot(&self.castle, pos).unwrap_or(pos),
                    None => pos,
                };
                // Both positions of each moving flight are named.
                let place = self
                    .castle_all
                    .ground_at(p[0], p[2], p[1])
                    .map(|g| self.castle_all.surface(g.surface).name.clone())
                    .unwrap_or_default();
                Location::Castle { place }
            }
        }
    }

    /// What to save for a character: where it stands, or a guest's return point.
    pub(super) fn resume_point(&self, i: usize) -> SavedPosition {
        let character_id = self.ents.info[i].character_id;
        let (zone, pos) = match self.ents.presence[i].return_to {
            Some(ReturnPoint::Castle { pos, .. }) => (SavedZone::Castle, pos),
            Some(ReturnPoint::OwnDorm { pos, .. }) => (SavedZone::Dorm, pos),
            // A rider is saved on the ground below.
            None if self.ents.zone[i] == CASTLE => (SavedZone::Castle, self.standing_pos(i)),
            None => (SavedZone::Dorm, self.ents.pos[i]),
        };
        SavedPosition {
            character_id,
            zone,
            pos,
        }
    }

    /// The live room of `owner`, created (and its furniture load queued) if
    /// needed.
    fn ensure_instance(&mut self, owner: i64, owner_name: &str, house: House) -> ZoneId {
        if let Some(z) = self.instances.of_owner(owner) {
            return z;
        }
        let world = self.shells.world(house).clone();
        let z = self.instances.create(owner, owner_name, house, world);
        self.enqueue_room(owner, RoomOp::Load);
        z
    }

    /// The room's entry spawn (the shells have exactly one).
    fn dorm_spawn(&self, zone: ZoneId) -> ([f32; 3], u8) {
        let d = self.instances.get(zone).expect("live dorm zone");
        let s = &d.world.spawns()[0];
        let y = d
            .world
            .ground_at(s.pos[0], s.pos[2], s.pos[1])
            .map_or(s.pos[1], |g| g.y);
        ([s.pos[0], y, s.pos[2]], yaw_to_u8(s.yaw))
    }

    /// Guests in a room (its members other than the owner).
    fn guest_count(&self, zone: ZoneId) -> usize {
        let Some(d) = self.instances.get(zone) else {
            return 0;
        };
        let owner = self.by_character.get(&d.owner).copied();
        d.members.iter().filter(|&&m| Some(m) != owner).count()
    }

    /// Where a character who is not online yet starts (`Welcome`).
    pub(super) fn start_point(
        &mut self,
        character: &Character,
        social: &super::entities::Social,
        now: Instant,
    ) -> Start {
        // A guest who dropped out of a friend's room a moment ago goes back in.
        if let Some(memo) = self.guest_memo.remove(&character.id)
            && memo.until > now
            && social.is_friend(memo.owner)
            && !social.any_block(memo.owner)
            && let Some(o) = self.index_of_character(memo.owner)
        {
            let (name, house) = (self.ents.info[o].name.clone(), self.ents.house[o]);
            let zone = self.ensure_instance(memo.owner, &name, house);
            if self.guest_count(zone) < DORM_GUESTS_MAX {
                let (pos, yaw) = self.dorm_spawn(zone);
                return Start {
                    zone,
                    pos,
                    level: 0,
                    yaw,
                    return_to: Some(memo.return_to),
                };
            }
            self.instances.teardown_if_empty(zone);
        }
        match character.zone {
            SavedZone::Dorm => {
                let zone = self.ensure_instance(character.id, &character.name, character.house);
                let world = self
                    .instances
                    .get(zone)
                    .expect("just ensured")
                    .world
                    .clone();
                let (pos, level, yaw) = match character.last_pos {
                    Some(_) => self.spawn_in(&world, character.last_pos),
                    None => {
                        let (pos, yaw) = self.dorm_spawn(zone);
                        (pos, 0, yaw)
                    }
                };
                Start {
                    zone,
                    pos,
                    level,
                    yaw,
                    return_to: None,
                }
            }
            SavedZone::Castle => {
                let world = self.castle_world(character.house).clone();
                // Saved on a flight that has swung since: on it still.
                let last = character.last_pos.map(|p| self.to_stair_state(p));
                let (pos, level, yaw) = self.spawn_in(&world, last);
                Start {
                    zone: CASTLE,
                    pos,
                    level,
                    yaw,
                    return_to: None,
                }
            }
        }
    }

    /// Removes a character from the world (see [`Leaving`]) and saves where
    /// it resumes. False for stale ids.
    pub(super) fn leave(&mut self, id: EntityId, how: Leaving, now: Instant) -> bool {
        if self.ents.index_of(id).is_none() {
            return false;
        }
        // Their trade window and pending requests end first,
        // while the other side can still be told who left.
        self.features_left(id, now);
        let Some(i) = self.ents.index_of(id) else {
            return false;
        };
        let cid = self.ents.info[i].character_id;
        let zone = self.ents.zone[i];
        if how == Leaving::Disconnect {
            // The owner going offline sends every guest back; the room closes
            // (their Wishing Room too).
            let rooms = [
                self.instances.of_owner(cid),
                self.instances.of_wishing_room(cid),
            ];
            for z in rooms.into_iter().flatten() {
                let guests: Vec<EntityId> = self
                    .instances
                    .members(z)
                    .iter()
                    .copied()
                    .filter(|&g| g != id)
                    .collect();
                for g in guests {
                    if let Some(j) = self.ents.index_of(g) {
                        self.leave_dorm(j, now);
                    }
                }
            }
            // A guest of a dorm is put back in on a quick reconnect (one of a
            // Wishing Room resumes at its door).
            if let (Some(rp), Some(d)) = (
                self.ents.presence[i].return_to,
                self.instances
                    .get(zone)
                    .filter(|d| d.wishing_room.is_none()),
            ) {
                self.guest_memo.insert(
                    cid,
                    GuestMemo {
                        owner: d.owner,
                        return_to: rp,
                        until: now + GUEST_MEMO_TTL,
                    },
                );
            }
        }
        // Leaving others' rooms above never removes anyone, so `i` holds.
        self.disconnect_friends(i, how == Leaving::Disconnect);
        // Its party shows it offline (a connection taking over keeps it on).
        if how == Leaving::Disconnect {
            self.party_offline(cid, self.ents.info[i].level, now);
        }
        // Its club too, once it is out of the world (below).
        let leaving_club = (how == Leaving::Disconnect).then_some(self.ents.info[i].level);
        let save = SaveBatch {
            positions: vec![self.resume_point(i)],
            // The map's reveals not saved yet.
            reveals: self.explore_left(cid, how == Leaving::Replaced),
        };
        let removed = self.ents.remove(id).expect("checked live");
        if self.by_character.get(&cid) == Some(&id) {
            self.by_character.remove(&cid);
        }
        if self.by_name.get(&removed.info.name_key) == Some(&id) {
            self.by_name.remove(&removed.info.name_key);
        }
        self.instances.remove_member(zone, id);
        if how == Leaving::Disconnect {
            self.instances.teardown_if_empty(zone);
        }
        // A class section or arena left empty closes (at the next settle).
        if self.instances.feature(zone).is_some() {
            self.feature_events
                .push_back(super::world::FeatureEvent::Closing(zone));
        }
        self.save(save);
        if let Some(level) = leaving_club {
            self.club_offline(cid, level);
        }
        // Dropping the client closes its outbound queue, which ends the
        // connection task if it has not ended already.
        drop(removed.client);
        self.metrics.players_online.set(self.players() as f64);
        true
    }

    /// Moves character `i` into `zone` at `pos` facing `yaw`.
    pub(super) fn move_to_zone(
        &mut self,
        i: usize,
        zone: ZoneId,
        pos: [f32; 3],
        yaw: u8,
        return_to: Option<ReturnPoint>,
        at: Instant,
    ) {
        let id = self.ents.ids[i];
        let old = self.ents.zone[i];
        // A rider lands first (`Mounted` before `ZoneChanged`); a
        // Flying class mounts its students again.
        self.unmount_for_zone_change(i, at);
        self.instances.remove_member(old, id);
        if old != zone {
            self.instances.teardown_if_empty(old);
        }
        self.ents.zone[i] = zone;
        // Protocol v10: nobody to look at in the new zone yet.
        self.ents.look[i] = None;
        self.ents.presence[i].return_to = return_to;
        let level = self
            .world_of(i)
            .level_at(pos[0], pos[2], pos[1])
            .unwrap_or(0);
        self.ents.pos[i] = pos;
        self.ents.level[i] = level;
        let state = &mut self.ents.state[i];
        state.pos = QPos::from_m(pos);
        state.yaw = yaw;
        state.anim = protocol::Anim::Idle;
        let m = &mut self.ents.mover[i];
        m.emote_until = None;
        m.zone_entered_at = Some(at);
        m.moved_at = None;
        m.unsaved = false;
        self.ents.client[i].reset_view();
        self.instances.add_member(zone, id);
        let msg = ServerMsg::ZoneChanged {
            zone: self.protocol_zone(zone),
            spawn: QPos::from_m(pos),
            yaw,
        };
        self.send_msg(i, &msg);
        self.send_dorm_state(i);
        self.save(SaveBatch::position(self.resume_point(i)));
        self.notify_friends_moved(i);
        self.party_moved(i);
        self.metrics.zone_changes.inc();
        // Arenas suspend boosts and set the walking speed.
        if self.instances.rules(old) != self.instances.rules(zone) {
            self.effects_updated(i, at);
        }
        // A trade window cannot follow anyone into another zone; classes and
        // duels hear of it too (at the next settle, never re-entrantly).
        self.feature_events
            .push_back(super::world::FeatureEvent::ZoneChanged { id, from: old });
        if old != zone && self.instances.feature(old).is_some() {
            self.feature_events
                .push_back(super::world::FeatureEvent::Closing(old));
        }
    }

    /// Sends `DormState` to character `i` if it is in a loaded room.
    pub(super) fn send_dorm_state(&mut self, i: usize) {
        let Some(d) = self.instances.get(self.ents.zone[i]) else {
            return;
        };
        let Some(items) = &d.items else {
            return;
        };
        // A Wishing Room has its own open flag, no items.
        let open = match d.wishing_room {
            Some(_) => d.open,
            None => self
                .index_of_character(d.owner)
                .is_some_and(|o| self.ents.presence[o].dorm_open),
        };
        let cap = if d.wishing_room.is_some() {
            0
        } else {
            DORM_ITEM_CAP
        };
        let msg = ServerMsg::DormState {
            open_to_friends: open,
            cap,
            items: items.clone(),
        };
        self.send_msg(i, &msg);
    }

    /// Takes character `i` into their own room, at its entry.
    fn enter_own_dorm(&mut self, i: usize, at: Instant) {
        let (cid, house) = (self.ents.info[i].character_id, self.ents.house[i]);
        let name = self.ents.info[i].name.clone();
        let zone = self.ensure_instance(cid, &name, house);
        let (pos, yaw) = self.dorm_spawn(zone);
        self.move_to_zone(i, zone, pos, yaw, None, at);
    }

    /// Takes character `i` into the room of owner `o` (checks done by the caller).
    fn enter_as_guest(&mut self, i: usize, o: usize, at: Instant) -> Result<(), DormErrorCode> {
        let (owner, house) = (self.ents.info[o].character_id, self.ents.house[o]);
        let name = self.ents.info[o].name.clone();
        let zone = self.ensure_instance(owner, &name, house);
        if self.ents.zone[i] == zone {
            return Ok(());
        }
        if self.guest_count(zone) >= DORM_GUESTS_MAX {
            self.instances.teardown_if_empty(zone);
            return Err(DormErrorCode::Full);
        }
        let here = (self.ents.pos[i], self.ents.state[i].yaw);
        // A guest hopping between friends' rooms keeps their first return point.
        let return_to = self.ents.presence[i]
            .return_to
            .unwrap_or(match self.ents.zone[i] {
                CASTLE => ReturnPoint::Castle {
                    pos: here.0,
                    yaw: here.1,
                },
                _ => ReturnPoint::OwnDorm {
                    pos: here.0,
                    yaw: here.1,
                },
            });
        let (pos, yaw) = self.dorm_spawn(zone);
        self.move_to_zone(i, zone, pos, yaw, Some(return_to), at);
        Ok(())
    }

    /// Takes character `i` out of the dorm it is in: an owner to their house's
    /// dorm door in the castle, a guest back to their return point.
    pub(super) fn leave_dorm(&mut self, i: usize, at: Instant) {
        if self.ents.zone[i] == CASTLE {
            return;
        }
        let house = self.ents.house[i];
        let castle = self.castle_world(house).clone();
        match self.ents.presence[i].return_to {
            Some(ReturnPoint::Castle { pos, yaw }) => {
                let pos = self.to_stair_state(pos);
                let (p, yaw) = match castle.ground_at(pos[0], pos[2], pos[1]) {
                    Some(g) => ([pos[0], g.y, pos[2]], yaw),
                    None => {
                        let (p, _, yaw) = self.spawn_in(&castle, None);
                        (p, yaw)
                    }
                };
                self.move_to_zone(i, CASTLE, p, yaw, None, at);
            }
            Some(ReturnPoint::OwnDorm { pos, yaw }) => {
                let cid = self.ents.info[i].character_id;
                let name = self.ents.info[i].name.clone();
                let zone = self.ensure_instance(cid, &name, house);
                let world = self
                    .instances
                    .get(zone)
                    .expect("just ensured")
                    .world
                    .clone();
                let (p, yaw) = match world.ground_at(pos[0], pos[2], pos[1]) {
                    Some(g) => ([pos[0], g.y, pos[2]], yaw),
                    None => self.dorm_spawn(zone),
                };
                self.move_to_zone(i, zone, p, yaw, None, at);
            }
            None => {
                let (p, yaw) = match self.dorm_door(house) {
                    Some(door) => door,
                    // A castle without common rooms yet: the layout spawns.
                    None => {
                        let (p, _, yaw) = self.spawn_in(&castle, None);
                        (p, yaw)
                    }
                };
                self.move_to_zone(i, CASTLE, p, yaw, None, at);
            }
        }
    }

    /// Where an owner reappears when leaving their dorm: their house's
    /// `dorm_door` in the castle, on the ground, facing its `yaw`.
    fn dorm_door(&self, house: House) -> Option<([f32; 3], u8)> {
        let door = self.castle.interactables().iter().find(|it| {
            it.kind == InteractableKind::DormDoor && it.house.as_deref() == Some(house.id())
        })?;
        let world = self.castle_world(house);
        let g = world.ground_at(door.pos[0], door.pos[2], door.pos[1])?;
        Some(([door.pos[0], g.y, door.pos[2]], yaw_to_u8(door.yaw)))
    }

    pub(super) fn dorm_error(&mut self, i: usize, code: DormErrorCode, item_id: Option<i64>) {
        self.metrics.dorm_errors.inc();
        self.send_msg(i, &ServerMsg::DormError { code, item_id });
    }

    /// Takes an action token, or answers `RateLimited`.
    pub(super) fn take_action(&mut self, i: usize, at: Instant, item_id: Option<i64>) -> bool {
        let ok =
            self.ents.presence[i]
                .actions
                .take(at, limits::ACTION_BURST, limits::ACTION_PER_SECOND);
        if !ok {
            self.dorm_error(i, DormErrorCode::RateLimited, item_id);
        }
        ok
    }

    /// `ClientMsg::Interact`: the dorm door of a common room, or a room's exit.
    pub(super) fn on_interact(&mut self, i: usize, at: Instant, target: &str) {
        if !self.take_action(i, at, None) {
            return;
        }
        let pos = self.ents.pos[i];
        let zone = self.ents.zone[i];
        let found = match self.instances.world(zone) {
            Some(w) => w.interactable(target),
            None => self.castle.interactable(target),
        }
        .map(|it| (it.kind, it.house.clone(), it.in_reach(pos)));
        let Some((kind, house, reach)) = found else {
            return self.dorm_error(i, DormErrorCode::UnknownTarget, None);
        };
        // A notice board is read on the client (the House Cup
        // panel, the house's next classes); the server answers nothing and
        // never fails it, whoever uses it from wherever. The
        // Owl Loft's owl mail opens the mail panel the same way. So
        // does the Wishing Room's door (the client offers the room
        // choice; `EnterWishingRoom` is what the server checks).
        if matches!(
            kind,
            InteractableKind::NoticeBoard
                | InteractableKind::OwlMail
                | InteractableKind::WishingRoomDoor
        ) {
            return;
        }
        // Doors, stations and find spots refuse riders.
        if self.ents.mover[i].mount.is_some() {
            return self.dorm_error(i, DormErrorCode::Mounted, None);
        }
        // Gated interactables (dorm doors, common-room stations, common-room
        // find spots) are their house's only.
        if house
            .as_deref()
            .is_some_and(|h| h != self.ents.house[i].id())
        {
            return self.dorm_error(i, DormErrorCode::WrongHouse, None);
        }
        match (kind, zone == CASTLE) {
            (InteractableKind::DormDoor, true) => {
                if !reach {
                    self.dorm_error(i, DormErrorCode::TooFar, None);
                } else {
                    self.enter_own_dorm(i, at);
                }
            }
            (InteractableKind::DormExit | InteractableKind::InstanceExit, false) => {
                if reach {
                    self.leave_dorm(i, at);
                } else {
                    self.dorm_error(i, DormErrorCode::TooFar, None);
                }
            }
            // A classroom door (`crate::classes::world`).
            (InteractableKind::ClassDoor, true) => {
                if reach {
                    let id = self.ents.ids[i];
                    self.with_classes(at, |c, ctx| c.on_door(ctx, id, target));
                } else {
                    self.dorm_error(i, DormErrorCode::TooFar, None);
                }
            }
            // Exploration finds (`crate::finds::world`).
            (InteractableKind::FindSpot, _) => {
                if reach {
                    let id = self.ents.ids[i];
                    self.with_finds(at, |f, ctx| f.on_search(ctx, id, target));
                } else {
                    self.dorm_error(i, DormErrorCode::TooFar, None);
                }
            }
            // Stations and the Owl Exchange counter: a grant the HTTP side checks.
            (k, _) if k.station().is_some() => {
                let station = k.station().expect("matched");
                if reach {
                    let target = StationTarget::Interactable { id: target.into() };
                    self.open_station(i, station, target, at);
                } else {
                    self.dorm_error(i, DormErrorCode::TooFar, None);
                }
            }
            _ => self.dorm_error(i, DormErrorCode::UnknownTarget, None),
        }
    }

    /// `ClientMsg::UseFurniture`: station furniture (a cauldron) placed in
    /// the dorm the character is in, within reach of its origin.
    pub(super) fn on_use_furniture(&mut self, i: usize, at: Instant, item_id: i64) {
        if !self.take_action(i, at, Some(item_id)) {
            return;
        }
        if self.ents.mover[i].mount.is_some() {
            return self.dorm_error(i, DormErrorCode::Mounted, Some(item_id));
        }
        let pos = self.ents.pos[i];
        let found = self
            .instances
            .get(self.ents.zone[i])
            .and_then(|d| d.items.as_ref())
            .and_then(|items| items.iter().find(|p| p.item_id == item_id))
            .map(|p| (p.item_def, p.pos.to_m()));
        let Some((def, item_pos)) = found else {
            return self.dorm_error(i, DormErrorCode::ItemNotFound, Some(item_id));
        };
        let station = catalog::shipped()
            .catalog
            .get(def)
            .and_then(|d| d.furniture.as_ref())
            .and_then(|f| f.station);
        let Some(station) = station else {
            return self.dorm_error(i, DormErrorCode::UnknownTarget, Some(item_id));
        };
        if !super::world::within(pos, item_pos, super::stations::FURNITURE_REACH_M) {
            return self.dorm_error(i, DormErrorCode::TooFar, Some(item_id));
        }
        self.open_station(i, station, StationTarget::Furniture { item_id }, at);
    }

    // ------------------------------------------------------------ guests

    /// Friends without a block either way.
    fn welcome_between(&self, i: usize, j: usize) -> bool {
        let other = self.ents.info[j].character_id;
        let s = &self.ents.social[i];
        s.is_friend(other) && !s.any_block(other)
    }

    pub(super) fn on_dorm_invite(&mut self, i: usize, at: Instant, name: &str) {
        if !self.take_action(i, at, None) {
            return;
        }
        let Some(t) = self
            .by_name
            .get(&name_key(name.trim()))
            .and_then(|&id| self.ents.index_of(id))
        else {
            return self.dorm_error(i, DormErrorCode::NotOnline, None);
        };
        if t == i || !self.welcome_between(i, t) {
            return self.dorm_error(i, DormErrorCode::NotFriend, None);
        }
        let (owner, guest) = (
            self.ents.info[i].character_id,
            self.ents.info[t].character_id,
        );
        // Sent from inside one's own Wishing Room, the
        // invitation is into it.
        let wishing_room = self.own_wishing_room(i);
        let invite_id = self
            .invites
            .create(owner, guest, at, wishing_room.is_some());
        let msg = ServerMsg::DormInviteReceived {
            invite_id,
            from_name: self.ents.info[i].name.clone(),
            expires_in_secs: DORM_INVITE_TTL_SECS,
            wishing_room,
        };
        // Another player's doing: queued as a notice, so invitations from
        // many friends at once cannot fill the invitee's outbound queue.
        self.notice(t, protocol::encode_server(&msg).into());
    }

    pub(super) fn on_accept_invite(&mut self, i: usize, at: Instant, invite_id: u32) {
        if !self.take_action(i, at, None) {
            return;
        }
        let me = self.ents.info[i].character_id;
        let Some((owner, wishing_room)) = self.invites.take(invite_id, me, at) else {
            return self.dorm_error(i, DormErrorCode::InviteExpired, None);
        };
        let Some(o) = self.index_of_character(owner) else {
            return self.dorm_error(i, DormErrorCode::NotOnline, None);
        };
        if !self.welcome_between(i, o) {
            return self.dorm_error(i, DormErrorCode::NotFriend, None);
        }
        let entered = if wishing_room {
            self.enter_wishing_room_as_guest(i, owner, at)
        } else {
            self.enter_as_guest(i, o, at)
        };
        match entered {
            // The owner asked them in: a kick bar no longer applies.
            Ok(()) => {
                self.kick_bars.remove(&(owner, me));
            }
            Err(code) => self.dorm_error(i, code, None),
        }
    }

    /// `DormDeclineInvite`: uses the invitation up and tells the inviter.
    pub(super) fn on_decline_invite(&mut self, i: usize, at: Instant, invite_id: u32) {
        if !self.take_action(i, at, None) {
            return;
        }
        let me = self.ents.info[i].character_id;
        // An expired invitation is declined too: the inviter still learns
        // the answer. One that is not this player's is ignored silently.
        let Some((owner, _)) = self.invites.take_any(invite_id, me, at) else {
            return;
        };
        if let Some(o) = self.index_of_character(owner) {
            let msg = ServerMsg::DormInviteDeclined {
                by_name: self.ents.info[i].name.clone(),
            };
            self.notice(o, protocol::encode_server(&msg).into());
        }
    }

    pub(super) fn on_dorm_visit(&mut self, i: usize, at: Instant, owner_name: &str) {
        if !self.take_action(i, at, None) {
            return;
        }
        let Some(o) = self
            .by_name
            .get(&name_key(owner_name.trim()))
            .and_then(|&id| self.ents.index_of(id))
        else {
            return self.dorm_error(i, DormErrorCode::NotOnline, None);
        };
        if o == i {
            // Your own room is reached through its door.
            return self.dorm_error(i, DormErrorCode::UnknownTarget, None);
        }
        if !self.welcome_between(i, o) {
            return self.dorm_error(i, DormErrorCode::NotFriend, None);
        }
        let barred = self
            .kick_bars
            .get(&(
                self.ents.info[o].character_id,
                self.ents.info[i].character_id,
            ))
            .is_some_and(|&until| until > at);
        // The owner's Wishing Room while it is open to
        // friends (a guest the opener just sent out finds it closed).
        let owner = self.ents.info[o].character_id;
        if let Some(z) = self.instances.of_wishing_room(owner)
            && self.instances.get(z).is_some_and(|d| d.open)
        {
            if barred {
                return self.dorm_error(i, DormErrorCode::Closed, None);
            }
            if let Err(code) = self.enter_wishing_room_as_guest(i, owner, at) {
                self.dorm_error(i, code, None);
            }
            return;
        }
        // A guest the owner just sent out finds the room closed to them.
        if !self.ents.presence[o].dorm_open || barred {
            return self.dorm_error(i, DormErrorCode::Closed, None);
        }
        if let Err(code) = self.enter_as_guest(i, o, at) {
            self.dorm_error(i, code, None);
        }
    }

    pub(super) fn on_dorm_kick(&mut self, i: usize, at: Instant, entity: EntityId) {
        if !self.take_action(i, at, None) {
            return;
        }
        let owner = self.ents.info[i].character_id;
        // The opener's Wishing Room as well as the dorm.
        let mine = [
            self.instances.of_owner(owner),
            self.instances.of_wishing_room(owner),
        ];
        match self.ents.index_of(entity) {
            Some(t) if t != i && mine.contains(&Some(self.ents.zone[t])) => {
                let guest = self.ents.info[t].character_id;
                let until = at + Duration::from_secs(u64::from(DORM_KICK_BAR_SECS));
                self.kick_bars.insert((owner, guest), until);
                self.leave_dorm(t, at);
            }
            _ => self.dorm_error(i, DormErrorCode::UnknownTarget, None),
        }
    }

    pub(super) fn on_dorm_set_open(&mut self, i: usize, at: Instant, open: bool) {
        // Inside one's own Wishing Room, its flag.
        if self.own_wishing_room(i).is_some() {
            return self.set_wishing_room_open(i, at, open);
        }
        let owner = self.ents.info[i].character_id;
        if self.ents.presence[i].dorm_open == open {
            return;
        }
        // Each change is a database write and a message to the room and to
        // every online friend.
        if !self.take_action(i, at, None) {
            return;
        }
        self.ents.presence[i].dorm_open = open;
        self.enqueue_room(owner, RoomOp::SetOpen(open));
        let frame: bytes::Bytes =
            protocol::encode_server(&ServerMsg::DormOpenChanged { open }).into();
        let mut told_owner = false;
        if let Some(z) = self.instances.of_owner(owner) {
            for m in self.instances.members(z).to_vec() {
                if let Some(j) = self.ents.index_of(m) {
                    told_owner |= j == i;
                    self.send(j, frame.clone());
                }
            }
        }
        if !told_owner {
            self.send(i, frame);
        }
        self.notify_friends_room_open(i);
    }

    // ------------------------------------------------------------ furniture

    /// The owner of the room character `i` may edit: their own, which they
    /// must be in. Answers the error otherwise.
    fn editor(&mut self, i: usize, at: Instant, item_id: i64) -> Option<i64> {
        if !self.take_action(i, at, Some(item_id)) {
            return None;
        }
        let me = self.ents.info[i].character_id;
        let code = match self.instances.get(self.ents.zone[i]) {
            // A Wishing Room has no furniture.
            None => DormErrorCode::NotInDorm,
            Some(d) if d.wishing_room.is_some() => DormErrorCode::NotInDorm,
            Some(d) if d.owner != me => DormErrorCode::NotOwner,
            Some(_) => {
                let waiting = self.rooms.get(&me).map_or(0, |q| q.ops.len());
                if waiting < ROOM_QUEUE_MAX {
                    return Some(me);
                }
                DormErrorCode::RateLimited
            }
        };
        self.dorm_error(i, code, Some(item_id));
        None
    }

    pub(super) fn on_dorm_place(
        &mut self,
        i: usize,
        at: Instant,
        item_id: i64,
        pos: QPos,
        yaw: u8,
    ) {
        if let Some(owner) = self.editor(i, at, item_id) {
            self.enqueue_room(owner, RoomOp::Place { item_id, pos, yaw });
        }
    }

    pub(super) fn on_dorm_move(&mut self, i: usize, at: Instant, item_id: i64, pos: QPos, yaw: u8) {
        if let Some(owner) = self.editor(i, at, item_id) {
            self.enqueue_room(owner, RoomOp::Move { item_id, pos, yaw });
        }
    }

    pub(super) fn on_dorm_remove(&mut self, i: usize, at: Instant, item_id: i64) {
        if let Some(owner) = self.editor(i, at, item_id) {
            self.enqueue_room(owner, RoomOp::Remove { item_id });
        }
    }

    /// Queues a room operation. Loads and open flags are coalesced, so a
    /// player opening and closing their room (or walking in and out of it)
    /// as fast as the action limit allows queues at most one of each.
    pub(super) fn enqueue_room(&mut self, owner: i64, op: RoomOp) {
        let q = self.rooms.entry(owner).or_default();
        match op {
            // A load queued or running already sees every edit before it
            // (edits wait for a loaded room), so a second one adds nothing.
            RoomOp::Load if q.loading || q.ops.contains(&RoomOp::Load) => {}
            // Only the newest open flag matters.
            RoomOp::SetOpen(open) => {
                match q.ops.iter_mut().find(|o| matches!(o, RoomOp::SetOpen(_))) {
                    Some(queued) => *queued = RoomOp::SetOpen(open),
                    None => q.ops.push_back(op),
                }
            }
            op => q.ops.push_back(op),
        }
        self.start_room(owner);
    }

    /// Starts the owner's next room operation unless one is in flight.
    fn start_room(&mut self, owner: i64) {
        loop {
            let Some(q) = self.rooms.get_mut(&owner) else {
                return;
            };
            if q.busy {
                return;
            }
            let Some(op) = q.ops.pop_front() else {
                self.rooms.remove(&owner);
                return;
            };
            let loading = op == RoomOp::Load;
            if let Some(job) = self.begin_room_op(owner, op) {
                let q = self.rooms.get_mut(&owner).expect("still queued");
                q.busy = true;
                q.loading = loading;
                self.jobs.push(job);
                return;
            }
        }
    }

    /// The job for one room operation, or `None` if it was refused (the
    /// owner is told) or no longer applies (the room closed).
    fn begin_room_op(&mut self, owner: i64, op: RoomOp) -> Option<Job> {
        let edit = match op {
            RoomOp::Load => return Some(Job::LoadRoom { owner }),
            RoomOp::SetOpen(open) => return Some(Job::SetOpen { owner, open }),
            RoomOp::Place { item_id, pos, yaw } => (item_id, EditOp::Place { pos, yaw }),
            RoomOp::Move { item_id, pos, yaw } => (item_id, EditOp::Move { pos, yaw }),
            RoomOp::Remove { item_id } => (item_id, EditOp::Remove),
        };
        let (item_id, op) = edit;
        // Edits apply to the live, loaded room only (the owner left otherwise).
        let d = self.instances.get(self.instances.of_owner(owner)?)?;
        let items = d.items.as_ref()?;
        let shell = d.world.place_areas();
        let cat = &catalog::shipped().catalog;
        let placed = items.iter().find(|it| it.item_id == item_id).copied();
        // Solid furniture: what the edit must leave open (`rules::Way`).
        let way = |except: Option<i64>| rules::Way {
            shell: self.shells.world(d.house).clone(),
            room: rules::blockers(cat, items, except),
            people: d
                .members
                .iter()
                .filter_map(|&m| self.ents.index_of(m))
                .map(|j| self.ents.pos[j])
                .collect(),
        };
        let res = match op {
            EditOp::Place { pos, yaw } => {
                if placed.is_some() {
                    Err(DormErrorCode::ItemUnavailable)
                } else if items.len() >= usize::from(DORM_ITEM_CAP) {
                    Err(DormErrorCode::CapReached)
                } else {
                    Ok(Edit::Place {
                        item_id,
                        pos,
                        yaw,
                        areas: rules::room_areas(shell, items, cat, None),
                        way: way(None),
                    })
                }
            }
            EditOp::Move { pos, yaw } => match placed {
                None => Err(DormErrorCode::ItemNotFound),
                // Nothing may be left floating: take things off it first.
                Some(_) if rules::holds_items(cat, items, item_id) => Err(DormErrorCode::NotEmpty),
                Some(it) => {
                    let areas = rules::room_areas(shell, items, cat, Some(item_id));
                    rules::check(cat, it.item_def, &areas, pos, yaw)
                        .and_then(|()| {
                            way(Some(item_id)).check(cat, it.item_def, item_id, pos, yaw)
                        })
                        .map(|()| Edit::Move { item_id, pos, yaw })
                }
            },
            EditOp::Remove => match placed {
                None => Err(DormErrorCode::ItemNotFound),
                Some(_) if rules::holds_items(cat, items, item_id) => Err(DormErrorCode::NotEmpty),
                Some(_) => Ok(Edit::Remove { item_id }),
            },
        };
        match res {
            Ok(edit) => Some(Job::Edit { owner, edit }),
            Err(code) => {
                if let Some(o) = self.index_of_character(owner) {
                    self.dorm_error(o, code, Some(item_id));
                }
                None
            }
        }
    }

    /// A room operation's job finished: apply it, tell the room, start the next.
    pub(super) fn room_done(&mut self, owner: i64, outcome: RoomOutcome) {
        match outcome {
            RoomOutcome::Loaded(items) => {
                if let Some(z) = self.instances.of_owner(owner) {
                    let d = self.instances.get_mut(z).expect("live");
                    if d.items.is_none() {
                        d.items = Some(items.unwrap_or_default());
                        for m in d.members.clone() {
                            if let Some(j) = self.ents.index_of(m) {
                                self.send_dorm_state(j);
                            }
                        }
                        self.furnish(z);
                    }
                }
            }
            RoomOutcome::Edited(Ok(edit)) => {
                // Not loaded yet means the load runs after this edit and sees it.
                if let Some(z) = self.instances.of_owner(owner)
                    && let Some(d) = self.instances.get_mut(z)
                    && let Some(items) = &mut d.items
                {
                    apply_edit(items, edit);
                    let frame: bytes::Bytes =
                        protocol::encode_server(&ServerMsg::DormFurniture { edit }).into();
                    for m in d.members.clone() {
                        if let Some(j) = self.ents.index_of(m) {
                            self.send(j, frame.clone());
                        }
                    }
                    self.metrics.dorm_edits.inc();
                    self.furnish(z);
                }
            }
            RoomOutcome::Edited(Err((code, item_id))) => {
                if let Some(o) = self.index_of_character(owner) {
                    self.dorm_error(o, code, Some(item_id));
                }
            }
            RoomOutcome::OpenSaved => {}
        }
        if let Some(q) = self.rooms.get_mut(&owner) {
            q.busy = false;
            q.loading = false;
        }
        self.start_room(owner);
    }

    /// Solid furniture: a dorm's world becomes its shell with its placed
    /// pieces solid (`rules::furnished`), and anyone standing where a piece
    /// now is goes to the nearest spot they fit (the rule refuses pieces put
    /// on people, so only a piece loaded from before, or one that raced a
    /// step, does this).
    fn furnish(&mut self, zone: ZoneId) {
        let Some(d) = self.instances.get_mut(zone) else {
            return;
        };
        let Some(items) = d.items.as_ref().filter(|_| d.wishing_room.is_none()) else {
            return;
        };
        let shell = self.shells.world(d.house).clone();
        let world = Arc::new(rules::furnished(&shell, &catalog::shipped().catalog, items));
        d.world = world.clone();
        let members = d.members.clone();
        let at = Instant::now();
        for m in members {
            let Some(j) = self.ents.index_of(m) else {
                continue;
            };
            let p = self.ents.pos[j];
            let fits = |w: &sim_core::World| {
                w.ground_at(p[0], p[2], p[1]).is_some_and(|g| {
                    sim_core::footprint_ground(w, &self.params, p[0], p[2], g.y).is_some()
                })
            };
            // Out only of the furniture: someone hugging a wall stays put.
            if fits(&world) || !fits(&shell) {
                continue;
            }
            let to = sim_core::stand_near(&world, &self.params, p, 3.0)
                .or_else(|| world.spawns().first().map(|s| s.pos));
            if let Some(to) = to {
                let level = world
                    .level_at(to[0], to[2], to[1])
                    .unwrap_or(self.ents.level[j]);
                self.put_at(j, to, level, at);
            }
        }
    }
}

/// The edit part of a [`RoomOp`].
enum EditOp {
    Place { pos: QPos, yaw: u8 },
    Move { pos: QPos, yaw: u8 },
    Remove,
}

/// Applies a stored edit to a room's item list (idempotent).
pub(super) fn apply_edit(items: &mut Vec<PlacedFurniture>, edit: FurnitureEdit) {
    match edit {
        FurnitureEdit::Placed(p) => match items.iter_mut().find(|it| it.item_id == p.item_id) {
            Some(it) => *it = p,
            None => items.push(p),
        },
        FurnitureEdit::Moved { item_id, pos, yaw } => {
            if let Some(it) = items.iter_mut().find(|it| it.item_id == item_id) {
                it.pos = pos;
                it.yaw = yaw;
            }
        }
        FurnitureEdit::Removed { item_id } => items.retain(|it| it.item_id != item_id),
    }
}

impl WorldSim {
    /// Opens a feature instance with layout `layout` (`WorldCtx::open_instance`).
    pub(super) fn open_feature(&mut self, kind: FeatureKind, layout: &str) -> Option<ZoneId> {
        let world = self.instance_layouts.get(layout)?.clone();
        Some(self.instances.create_feature(kind, layout, world))
    }

    /// Takes character `i` from the castle into feature zone `zone`
    /// (`WorldCtx::enter_instance`); it returns to where it stood.
    pub(super) fn enter_feature(
        &mut self,
        i: usize,
        zone: ZoneId,
        spot: Option<([f32; 3], f32)>,
        at: Instant,
    ) -> bool {
        if self.ents.zone[i] != CASTLE || self.ents.info[i].npc.is_some() {
            return false;
        }
        let Some(world) = self.instances.feature(zone).map(|f| f.world.clone()) else {
            return false;
        };
        let return_to = ReturnPoint::Castle {
            pos: self.ents.pos[i],
            yaw: self.ents.state[i].yaw,
        };
        let (pos, yaw) = match spot {
            Some((p, yaw)) => match world.ground_at(p[0], p[2], p[1]) {
                Some(g) => ([p[0], g.y, p[2]], yaw_to_u8(yaw)),
                None => {
                    let (p, _, yaw) = self.spawn_in(&world, None);
                    (p, yaw)
                }
            },
            None => {
                let (p, _, yaw) = self.spawn_in(&world, None);
                (p, yaw)
            }
        };
        self.move_to_zone(i, zone, pos, yaw, Some(return_to), at);
        true
    }

    /// Sends everyone in feature zone `zone` back; it closes once empty.
    pub(super) fn close_feature(&mut self, zone: ZoneId, at: Instant) {
        let Some(f) = self.instances.feature(zone) else {
            return;
        };
        for m in f.members.clone() {
            if let Some(j) = self.ents.index_of(m) {
                self.leave_dorm(j, at);
            }
        }
        self.feature_events
            .push_back(super::world::FeatureEvent::Closing(zone));
    }

    /// Closes feature zone `zone` if no player is left in it: its NPCs are
    /// removed and its kind is handed back (for the owner's callback).
    pub(super) fn close_feature_if_empty(&mut self, zone: ZoneId) -> Option<FeatureKind> {
        let f = self.instances.take_feature_if_empty(zone)?;
        for npc in f.npcs {
            if self.ents.remove(npc).is_some() {
                self.npc_count = self.npc_count.saturating_sub(1);
            }
        }
        Some(f.kind)
    }

    /// Adds an NPC to feature zone `zone`, or (NPCs in the open castle) to
    /// the castle (`WorldCtx::spawn_npc`).
    pub(super) fn spawn_npc(
        &mut self,
        zone: ZoneId,
        npc: super::ext::NpcSpawn,
        now: Instant,
    ) -> Option<EntityId> {
        let world = if zone == CASTLE {
            self.castle_all.clone()
        } else {
            self.instances.feature(zone)?.world.clone()
        };
        let p = npc.pos;
        let level = world.level_at(p[0], p[2], p[1]).unwrap_or_default();
        // A connection nobody reads: sends to an NPC vanish.
        let (out, _) = tokio::sync::mpsc::channel(1);
        let id = self.ents.insert(
            super::entities::NewEntity {
                info: super::entities::Info {
                    character_id: 0,
                    name: npc.name,
                    name_key: String::new(),
                    appearance_version: 1,
                    appearance: npc.appearance,
                    effects_version: 0,
                    effects: Vec::new(),
                    level: 0,
                    npc: Some(npc.npc),
                },
                house: npc.house,
                zone,
                pos: p,
                level,
                yaw: yaw_to_u8(npc.yaw),
                client: super::entities::Client::new(
                    out,
                    Arc::new(super::ConnControl::default()),
                    now,
                ),
                social: Default::default(),
                return_to: None,
                dorm_open: false,
                effects: Vec::new(),
                school: Default::default(),
            },
            now,
        )?;
        self.npc_count += 1;
        self.instances.add_npc(zone, id);
        Some(id)
    }

    /// Places NPC `i` (no validation) with `anim`.
    pub(super) fn move_npc(&mut self, i: usize, pos: [f32; 3], yaw: f32, anim: protocol::Anim) {
        if !pos.iter().all(|v| v.is_finite()) {
            return;
        }
        // NPCs in the open castle walk between floors: the floor level ranks
        // them in views (`visibility::score`).
        if (pos[1] - self.ents.pos[i][1]).abs() > 0.05 {
            let world = if self.ents.zone[i] == CASTLE {
                &self.castle_all
            } else {
                self.world_of(i)
            };
            if let Some(level) = world.level_at(pos[0], pos[2], pos[1]) {
                self.ents.level[i] = level;
            }
        }
        self.ents.pos[i] = pos;
        let s = &mut self.ents.state[i];
        s.pos = QPos::from_m(pos);
        if yaw.is_finite() {
            s.yaw = yaw_to_u8(yaw);
        }
        s.anim = anim;
        self.ents.mover[i].emote_until = None;
    }

    /// Removes an NPC entity.
    pub(super) fn remove_npc(&mut self, e: EntityId) {
        let Some(i) = self.ents.index_of(e) else {
            return;
        };
        if self.ents.info[i].npc.is_none() {
            return;
        }
        let zone = self.ents.zone[i];
        self.instances.remove_npc(zone, e);
        self.ents.remove(e);
        self.npc_count = self.npc_count.saturating_sub(1);
    }
}
