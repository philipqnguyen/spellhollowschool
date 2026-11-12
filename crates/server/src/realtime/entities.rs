//! Dense, data-oriented storage for the characters in the world (the castle
//! and every dorm instance).
//!
//! Every per-character field lives in its own `Vec`, all indexed by the same
//! *dense index* `0..len`. Hot loops (the spatial grid rebuild, visibility,
//! snapshot encoding) walk the few arrays they need contiguously; rarely used
//! data (friends, return points) sits in its own cold arrays. Removing a
//! character swap-removes it from every array, so the arrays never have holes.
//!
//! Entity ids are what clients see. An id packs a slot number (low 16 bits)
//! and that slot's generation (high 16 bits), so looking an id up is one array
//! access plus a generation check, and a reused slot gets a new id: a client
//! never confuses a newcomer with the character who left. A stale id kept in
//! some list (a departed friend) simply stops resolving.
//!
//! Scripted NPCs (a class's professor) are entities too, so
//! visibility and snapshots treat them like anyone else. They are marked by
//! `Info.npc`, have character id 0 and a dead [`Client`] (its queue's
//! receiver is gone, so sends to it vanish); the world never builds
//! snapshots for them, never saves them and leaves them out of player
//! counts, chat routing and character lookups.

use std::collections::VecDeque;
use std::sync::Arc;
use std::time::Instant;

use bytes::Bytes;
use protocol::{
    Anim, Appearance, EntityId, EntityState, House, LookVersion, QPos, SPEED_PCT_NORMAL, SentLooks,
    VIEW_LIMIT_DEFAULT, VisibleEffect,
};
use tokio::sync::mpsc;

use super::ConnControl;
use super::ext::ActiveEffect;
use super::limits::{self, Bucket};

/// Slots available; ids keep the slot in 16 bits.
pub const MAX_ENTITIES: usize = 1 << 16;

/// Recent whisper partners kept per player as priority players.
pub const MAX_PRIORITY: usize = 8;

/// Which zone a character is in: [`CASTLE`] or a dorm instance's id.
pub type ZoneId = u32;
/// The seamless castle and grounds.
pub const CASTLE: ZoneId = 0;

#[derive(Debug, Clone, Copy)]
struct Slot {
    /// Bumped each time the slot is freed. Starts at 1, so id 0 is never issued
    /// (it is reserved for server notices, see [`protocol::SERVER_ENTITY_ID`]).
    generation: u16,
    /// Dense index while occupied, `FREE` otherwise.
    dense: u32,
}

const FREE: u32 = u32::MAX;

pub fn make_id(slot: u32, generation: u16) -> EntityId {
    (u32::from(generation) << 16) | slot
}

pub fn slot_of(id: EntityId) -> u32 {
    id & 0xFFFF
}

/// Display data, sent once in `EntityInfo` when a character enters a view.
#[derive(Debug, Clone)]
pub struct Info {
    pub character_id: i64,
    pub name: String,
    /// Case-folded name for whisper lookups.
    pub name_key: String,
    pub appearance_version: u32,
    pub appearance: Appearance,
    /// Bumped whenever `effects` changes (from 0 at join).
    pub effects_version: u32,
    /// The visible part of the active effects, in kind order, plus Lucerna.
    pub effects: Vec<VisibleEffect>,
    /// The player level (0 for NPCs); a change bumps
    /// `effects_version`.
    pub level: u8,
    /// `Some(data id)` for a scripted NPC. NPCs have no
    /// character id (0), no connection and no saved position.
    pub npc: Option<String>,
}

impl Info {
    /// The version pair that keys the look cache (`protocol::looks`).
    pub fn look_version(&self) -> LookVersion {
        LookVersion {
            appearance: self.appearance_version,
            effects: self.effects_version,
        }
    }
}

/// A character's active potion effects, sorted by kind, and
/// what else the world adds to its look.
#[derive(Debug, Clone, Default)]
pub struct EffectState {
    pub active: Vec<ActiveEffect>,
    /// The wand is lit (Lucerna).
    pub lucerna: bool,
    /// In a zone that suspends boosts (an arena): no Pick-Me-Up Tonic
    /// speed, no Diminishing Draught or Enlarging Draught scale.
    pub suspended: bool,
}

/// Movement validation and animation state.
#[derive(Debug, Clone)]
pub struct Mover {
    /// Highest `Move.seq` seen; older or repeated moves are ignored.
    pub last_seq: Option<u32>,
    /// Seconds of movement the player has earned but not used (see `world.rs`).
    pub credit: f32,
    pub credit_at: Instant,
    pub last_correction: Option<Instant>,
    /// When the character last changed zone: failing moves just after are
    /// dropped silently (they were sent in the old zone).
    pub zone_entered_at: Option<Instant>,
    /// Moved (or changed zone) since the position was last handed to the saver.
    pub unsaved: bool,
    /// A wave or bow ends at this time; a sit (`None` with an emote anim)
    /// lasts until the character moves.
    pub emote_until: Option<Instant>,
    pub emotes: Bucket,
    /// Movement speed (percent of normal) moves are validated with, from
    /// the active effects (Pick-Me-Up Tonic).
    pub speed_pct: u16,
    /// After the speed drops, the old speed still counts until then, for
    /// moves the client sent before it heard (`world::SPEED_DROP_GRACE`).
    pub speed_grace: Option<(u16, Instant)>,
    /// Spells L20: the bind/slow multiplier (percent) `speed_pct` was last
    /// computed with: a new or stronger hindrance gets no `speed_grace`.
    pub hinder_pct: u16,
    /// The broom, while mounted (`None` walking). The server's
    /// own state picks the move check, never the client's anim.
    pub mount: Option<Mount>,
    /// The character's Flying skill XP, cached at join and
    /// refreshed when a grant changes it (above 0: may mount).
    pub flying_xp: u32,
    /// The broom speed (percent of the tier's) flight moves are
    /// checked with (`OwnEffects.broom_pct`, Featherlight), and the old one
    /// for a moment after it drops (as `speed_grace`).
    pub broom_pct: u16,
    pub broom_grace: Option<(u16, Instant)>,
    /// When the mount state last changed: rejected moves just
    /// after are dropped silently (sent before the client heard).
    pub mount_changed_at: Option<Instant>,
    /// When the last move was accepted (a ring crossing is dated
    /// between two accepted moves).
    pub moved_at: Option<Instant>,
    /// `Mount` requests (a burst of 3, then one a second).
    pub mounts: Bucket,
    /// Protocol v10: `LookAt` messages (`limits::LOOK_BURST`).
    pub looks: Bucket,
    /// Spells L20 (protocol v11): the server is moving the character
    /// (Adveni): the client's moves are dropped until it ends.
    pub forced: Option<Forced>,
}

/// Spells L20: a forced move (`WorldCtx::force_move`, `ServerMsg::ForcedMove`):
/// a linear slide `from` -> `to` over `start..end`, then standing still
/// until `hold_until`; the client's moves are dropped (not corrected) until
/// `hold_until + world::FORCED_TAIL`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Forced {
    pub from: [f32; 3],
    pub to: [f32; 3],
    pub start: Instant,
    pub end: Instant,
    pub hold_until: Instant,
    /// Who cast it.
    pub by: EntityId,
}

/// A mounted character's broom.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Mount {
    /// The speed tier (1..; `data/flying.json` `tiers`).
    pub tier: u8,
}

/// The last state a client received for one member of its visible set.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Baseline {
    pub state: EntityState,
    /// Snapshots since the last full state (a resync is due at
    /// `RESYNC_SNAPSHOTS`).
    pub age: u8,
    /// A change was held back in the last snapshot (far tier), so the next
    /// one must carry it.
    pub skipped: bool,
}

/// Per-connection state: the outbound queue and what this client has been sent.
#[derive(Debug)]
pub struct Client {
    pub out: mpsc::Sender<Bytes>,
    pub control: Arc<ConnControl>,
    pub view_limit: u8,
    /// Entities in this client's visible set, sorted by id.
    pub visible: Vec<EntityId>,
    /// Their baselines, parallel to `visible`.
    pub base: Vec<Baseline>,
    /// Protocol v10: whom this client was last told each member looks at,
    /// parallel to `visible` (`Snapshot.looks`).
    pub looks_sent: Vec<Option<EntityId>>,
    /// Which looks this connection was sent in full (`protocol::looks`).
    /// Kept across zone changes; a new connection starts empty.
    pub looks: SentLooks,
    /// Characters (this one included) whose new look this client still has
    /// to be sent (`AppearanceChanged`), delivered as its queue has room.
    pub pending_looks: Vec<EntityId>,
    /// The same for new visible effects (`EffectsChanged`).
    pub pending_effects: Vec<EntityId>,
    /// The same for new levels (`LevelChanged`).
    pub pending_levels: Vec<EntityId>,
    /// Friend events and invitations for this client, oldest first, sent as
    /// its queue has room (`WorldSim::notice`).
    pub notices: VecDeque<Bytes>,
    /// Snapshot parity (0 or 1): half the clients snapshot on even ticks, half on
    /// odd, which spreads the snapshot work evenly over the 20 Hz ticks.
    pub snapshot_phase: u8,
    /// Tick offset (0..20) for this client's once-a-second roster.
    pub roster_phase: u8,
    /// Recent whisper partners and when they stop being priority players.
    pub priority: Vec<(EntityId, Instant)>,
    /// Online friends' entity ids (priority players while in the same zone).
    /// May hold ids of friends who have since left; those no longer resolve.
    pub online_friends: Vec<EntityId>,
    /// The other online members of this character's party (`party.rs`):
    /// group entities, ranked ahead of every priority player while in the
    /// same zone. Set whenever the party is sent again.
    pub party: Vec<EntityId>,
    pub chat_tokens: f32,
    pub chat_at: Instant,
    /// Set while building snapshots when the outbound queue was full.
    pub too_slow: bool,
}

impl Client {
    pub fn new(out: mpsc::Sender<Bytes>, control: Arc<ConnControl>, now: Instant) -> Self {
        let cap = usize::from(protocol::VIEW_LIMIT_MAX);
        Self {
            out,
            control,
            view_limit: VIEW_LIMIT_DEFAULT,
            visible: Vec::with_capacity(cap),
            base: Vec::with_capacity(cap),
            looks_sent: Vec::with_capacity(cap),
            looks: SentLooks::new(),
            pending_looks: Vec::new(),
            pending_effects: Vec::new(),
            pending_levels: Vec::new(),
            notices: VecDeque::new(),
            snapshot_phase: 0,
            roster_phase: 0,
            priority: Vec::with_capacity(MAX_PRIORITY),
            online_friends: Vec::new(),
            party: Vec::new(),
            chat_tokens: super::chat::CHAT_BURST,
            chat_at: now,
            too_slow: false,
        }
    }

    /// Forgets the visible set (`Welcome`, `ZoneChanged`): the next snapshot
    /// starts from an empty set, as the client does.
    pub fn reset_view(&mut self) {
        self.visible.clear();
        self.base.clear();
        self.looks_sent.clear();
    }
}

/// A character's friends and blocks (character ids, each list sorted), loaded
/// from the database at join and reloaded on `WorldHandle::friends_changed`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Social {
    pub friends: Vec<i64>,
    /// Characters this one blocked: their chat is not delivered to it.
    pub blocked: Vec<i64>,
    /// Characters that blocked this one.
    pub blocked_by: Vec<i64>,
}

impl Social {
    /// Sorts and dedups every list.
    pub fn normalized(mut self) -> Self {
        for v in [&mut self.friends, &mut self.blocked, &mut self.blocked_by] {
            v.sort_unstable();
            v.dedup();
        }
        self
    }

    pub fn is_friend(&self, character_id: i64) -> bool {
        self.friends.binary_search(&character_id).is_ok()
    }

    /// This character blocked `character_id`.
    pub fn has_blocked(&self, character_id: i64) -> bool {
        self.blocked.binary_search(&character_id).is_ok()
    }

    /// A block either way.
    pub fn any_block(&self, character_id: i64) -> bool {
        self.has_blocked(character_id) || self.blocked_by.binary_search(&character_id).is_ok()
    }
}

/// Where a guest goes back to when they leave someone else's dorm: where they
/// stood before entering it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ReturnPoint {
    Castle {
        pos: [f32; 3],
        yaw: u8,
    },
    /// Their own dorm room.
    OwnDorm {
        pos: [f32; 3],
        yaw: u8,
    },
}

/// Zone and dorm bookkeeping per character.
#[derive(Debug, Clone)]
pub struct Presence {
    /// `Some` while a guest in someone else's dorm.
    pub return_to: Option<ReturnPoint>,
    /// This character's own room is open to friends (`dorms.open_to_friends`).
    pub dorm_open: bool,
    /// Bumped for every friends reload requested; only the newest reload's
    /// result is applied.
    pub friends_seq: u64,
    /// Doors, invitations and furniture edits.
    pub actions: Bucket,
}

/// Everything needed to add a character.
pub struct NewEntity {
    pub info: Info,
    pub house: House,
    pub zone: ZoneId,
    pub pos: [f32; 3],
    pub level: i32,
    pub yaw: u8,
    pub client: Client,
    pub social: Social,
    pub return_to: Option<ReturnPoint>,
    pub dorm_open: bool,
    /// Effects that survived a relog (or a replaced connection's).
    pub effects: Vec<ActiveEffect>,
    /// XP, learned spells and loadout.
    pub school: crate::progression::SchoolState,
}

#[derive(Debug, Default)]
pub struct Entities {
    slots: Vec<Slot>,
    free: Vec<u32>,
    // Dense arrays, all the same length.
    pub ids: Vec<EntityId>,
    pub pos: Vec<[f32; 3]>,
    pub level: Vec<i32>,
    /// The quantized state clients receive.
    pub state: Vec<EntityState>,
    pub zone: Vec<ZoneId>,
    pub house: Vec<House>,
    pub info: Vec<Info>,
    pub mover: Vec<Mover>,
    pub client: Vec<Client>,
    pub social: Vec<Social>,
    pub presence: Vec<Presence>,
    pub effects: Vec<EffectState>,
    /// XP, learned spells and loadout (cold).
    pub school: Vec<crate::progression::SchoolState>,
    /// Protocol v10: whom each character looks at (`ClientMsg::LookAt`,
    /// checked when set: in the same zone, within range). Viewers are shown
    /// it through `Snapshot.looks` (snapshot.rs `shown_look`).
    pub look: Vec<Option<EntityId>>,
}

/// Read-only view of the arrays that visibility and snapshots need, so they can
/// run in parallel while each worker mutates only its own [`Client`].
#[derive(Clone, Copy)]
pub struct EntView<'a> {
    slots: &'a [Slot],
    pub ids: &'a [EntityId],
    pub pos: &'a [[f32; 3]],
    pub level: &'a [i32],
    pub state: &'a [EntityState],
    pub zone: &'a [ZoneId],
    pub house: &'a [House],
    pub info: &'a [Info],
    pub look: &'a [Option<EntityId>],
}

impl EntView<'_> {
    /// Dense index of a live entity.
    #[inline]
    pub fn index_of(&self, id: EntityId) -> Option<usize> {
        lookup(self.slots, id)
    }

    pub fn len(&self) -> usize {
        self.ids.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ids.is_empty()
    }
}

#[inline]
fn lookup(slots: &[Slot], id: EntityId) -> Option<usize> {
    let slot = slots.get(slot_of(id) as usize)?;
    (slot.dense != FREE && make_id(slot_of(id), slot.generation) == id)
        .then_some(slot.dense as usize)
}

impl Entities {
    pub fn len(&self) -> usize {
        self.ids.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ids.is_empty()
    }

    #[inline]
    pub fn index_of(&self, id: EntityId) -> Option<usize> {
        lookup(&self.slots, id)
    }

    /// Adds a character; `None` when all slots are taken.
    pub fn insert(&mut self, e: NewEntity, now: Instant) -> Option<EntityId> {
        let slot = match self.free.pop() {
            Some(s) => s,
            None if self.slots.len() < MAX_ENTITIES => {
                self.slots.push(Slot {
                    generation: 1,
                    dense: FREE,
                });
                (self.slots.len() - 1) as u32
            }
            None => return None,
        };
        let dense = self.ids.len();
        let entry = &mut self.slots[slot as usize];
        entry.dense = dense as u32;
        let id = make_id(slot, entry.generation);

        let mut client = e.client;
        client.snapshot_phase =
            (slot % (protocol::WORLD_TICK_HZ / protocol::WORLD_SNAPSHOT_HZ)) as u8;
        client.roster_phase = (slot % protocol::WORLD_TICK_HZ) as u8;
        self.ids.push(id);
        self.pos.push(e.pos);
        self.level.push(e.level);
        self.state.push(EntityState {
            id,
            pos: QPos::from_m(e.pos),
            yaw: e.yaw,
            anim: Anim::Idle,
        });
        self.zone.push(e.zone);
        self.house.push(e.house);
        self.info.push(e.info);
        self.mover.push(Mover {
            last_seq: None,
            credit: super::world::JOIN_MOVE_CREDIT,
            credit_at: now,
            last_correction: None,
            zone_entered_at: None,
            unsaved: false,
            emote_until: None,
            emotes: Bucket::full(limits::EMOTE_BURST, now),
            speed_pct: SPEED_PCT_NORMAL,
            speed_grace: None,
            hinder_pct: SPEED_PCT_NORMAL,
            mount: None,
            flying_xp: 0,
            broom_pct: protocol::BROOM_PCT_NORMAL,
            broom_grace: None,
            mount_changed_at: None,
            moved_at: None,
            mounts: Bucket::full(limits::EMOTE_BURST, now),
            looks: Bucket::full(limits::LOOK_BURST, now),
            forced: None,
        });
        self.client.push(client);
        self.social.push(e.social);
        self.presence.push(Presence {
            return_to: e.return_to,
            dorm_open: e.dorm_open,
            friends_seq: 0,
            actions: Bucket::full(limits::ACTION_BURST, now),
        });
        self.effects.push(EffectState {
            active: e.effects,
            ..EffectState::default()
        });
        self.school.push(e.school);
        self.look.push(None);
        Some(id)
    }

    /// Removes a live entity, returning everything it held.
    pub fn remove(&mut self, id: EntityId) -> Option<Removed> {
        let dense = self.index_of(id)?;
        let slot = &mut self.slots[slot_of(id) as usize];
        slot.dense = FREE;
        // Generation 0 is skipped so id 0 stays reserved.
        slot.generation = match slot.generation.wrapping_add(1) {
            0 => 1,
            g => g,
        };
        self.free.push(slot_of(id));

        let last = self.ids.len() - 1;
        if dense != last {
            let moved = self.ids[last];
            self.slots[slot_of(moved) as usize].dense = dense as u32;
        }
        self.ids.swap_remove(dense);
        let pos = self.pos.swap_remove(dense);
        self.level.swap_remove(dense);
        let state = self.state.swap_remove(dense);
        let zone = self.zone.swap_remove(dense);
        self.house.swap_remove(dense);
        let info = self.info.swap_remove(dense);
        self.mover.swap_remove(dense);
        let client = self.client.swap_remove(dense);
        let social = self.social.swap_remove(dense);
        let presence = self.presence.swap_remove(dense);
        let effects = self.effects.swap_remove(dense);
        self.school.swap_remove(dense);
        self.look.swap_remove(dense);
        Some(Removed {
            info,
            pos,
            yaw: state.yaw,
            zone,
            client,
            social,
            presence,
            effects,
        })
    }

    /// Splits into a shared view and the mutable clients.
    pub fn split(&mut self) -> (EntView<'_>, &mut [Client]) {
        (
            EntView {
                slots: &self.slots,
                ids: &self.ids,
                pos: &self.pos,
                level: &self.level,
                state: &self.state,
                zone: &self.zone,
                house: &self.house,
                info: &self.info,
                look: &self.look,
            },
            &mut self.client,
        )
    }

    pub fn view(&self) -> EntView<'_> {
        EntView {
            slots: &self.slots,
            ids: &self.ids,
            pos: &self.pos,
            level: &self.level,
            state: &self.state,
            zone: &self.zone,
            house: &self.house,
            info: &self.info,
            look: &self.look,
        }
    }
}

/// What [`Entities::remove`] hands back.
pub struct Removed {
    pub info: Info,
    pub pos: [f32; 3],
    pub yaw: u8,
    pub zone: ZoneId,
    pub client: Client,
    pub social: Social,
    pub presence: Presence,
    pub effects: EffectState,
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub fn test_entity(name: &str, pos: [f32; 3], level: i32, house: House) -> NewEntity {
        // Tests that need the frames build their own client; here the receiver is
        // dropped, so sends fail as if the connection had closed.
        let (out, _) = mpsc::channel(4);
        NewEntity {
            info: Info {
                character_id: 0,
                name: name.into(),
                name_key: name.to_lowercase(),
                appearance_version: 0,
                appearance: Appearance::default(),
                effects_version: 0,
                effects: Vec::new(),
                level: 1,
                npc: None,
            },
            house,
            zone: CASTLE,
            pos,
            level,
            yaw: 0,
            client: Client::new(out, Arc::new(ConnControl::default()), Instant::now()),
            social: Social::default(),
            return_to: None,
            dorm_open: false,
            effects: Vec::new(),
            school: Default::default(),
        }
    }

    #[test]
    fn ids_are_generation_checked_and_never_zero() {
        let mut e = Entities::default();
        let now = Instant::now();
        let a = e
            .insert(test_entity("a", [0.0; 3], 0, House::Roarwing), now)
            .unwrap();
        let b = e
            .insert(test_entity("b", [1.0; 3], 0, House::Roarwing), now)
            .unwrap();
        assert_ne!(a, 0);
        assert_eq!(e.index_of(a), Some(0));
        assert_eq!(e.index_of(b), Some(1));

        // Removing `a` moves `b` into dense slot 0 and retires `a`'s id.
        let removed = e.remove(a).unwrap();
        assert_eq!(removed.info.name, "a");
        assert_eq!(e.index_of(a), None);
        assert_eq!(e.index_of(b), Some(0));
        assert_eq!(e.pos[0], [1.0; 3]);

        // The slot is reused under a new id.
        let c = e
            .insert(test_entity("c", [2.0; 3], 0, House::Roarwing), now)
            .unwrap();
        assert_eq!(slot_of(c), slot_of(a));
        assert_ne!(c, a);
        assert_eq!(e.index_of(a), None);
        assert_eq!(e.index_of(c), Some(1));
        assert!(e.remove(a).is_none());
    }

    #[test]
    fn social_lists_answer_membership() {
        let s = Social {
            friends: vec![9, 3, 3],
            blocked: vec![7],
            blocked_by: vec![5],
        }
        .normalized();
        assert_eq!(s.friends, vec![3, 9]);
        assert!(s.is_friend(9) && !s.is_friend(7));
        assert!(s.has_blocked(7) && !s.has_blocked(5));
        assert!(s.any_block(5) && s.any_block(7) && !s.any_block(3));
    }
}
