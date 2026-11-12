//! Seams for the features whose rules live outside `realtime/`
//! (docs/architecture.md): the trade window
//! (`crate::trade::world`), potions and Mimic Draught consent
//! (`crate::potions::world`) and find spots (`crate::finds::world`). The
//! world owns their state and calls them on the world
//! thread; they act on it only through a [`WorldCtx`]:
//!
//! - **Input**: the world turns `ClientMsg::Trade*` into [`TradeInput`] and
//!   `Drink` / `MimicDraught*` into [`PotionInput`] and calls
//!   `Trades::on_input` / `Potions::on_input`; `Interact` with a
//!   `find_spot` in reach calls `Finds::on_search`.
//! - **Lifecycle**: `on_left` when a character leaves the world,
//!   `on_zone_changed` after a zone change, `tick` once a second.
//! - **Database work**: [`WorldCtx::spawn`] runs an [`ExtJob`] on the tokio
//!   runtime (never on the world thread); its [`ExtDone`] comes back as a
//!   command and is handed to the feature's `on_done` (or run with a
//!   context, [`ExtDone::Run`]).
//! - **Effects** are core state: [`WorldCtx::apply_effect`] /
//!   [`WorldCtx::remove_effect`] update the character's visible effects
//!   (bumping `effects_version`, sending `EffectsChanged` to viewers once per
//!   tick and `OwnEffects` to the character) and its movement speed, which
//!   move validation uses at once (with a grace period when it drops).
//!   Effects expire on their own; the `active_effects` rows that make them
//!   survive a relog are the potions code's to write.
//!
//! Adds the class, spell and dueling handlers
//! (`crate::classes::world::Classes`, `crate::spells::world::Spells`,
//! `crate::dueling::world::Duels`) and,
//! for them, instances of their own (class sections, arenas: [`FeatureKind`]),
//! NPC entities (the professor), zone priority (who every member of an
//! instance always sees), the `Cast` animation, Lucerna, levels and spellbooks,
//! and the zone rules (no potions in arenas, Pick-Me-Up Tonic speed and Diminishing Draught or
//! Enlarging Draught scale suspended there). Lifecycle callbacks a handler causes
//! through the context (a zone change, an instance closing) are delivered
//! after that handler returns, never re-entrantly.
//!
//! Spells to level 20 (protocol v11) add the primitives the new spells
//! need: forced moves ([`WorldCtx::force_move`], [`WorldCtx::being_moved`],
//! [`WorldCtx::pull_destination`]), Aperio's spots
//! ([`WorldCtx::reveal_near`]) and what a hindering spell asks of its target
//! ([`WorldCtx::spell_target_free`] and the queries it is made of). Spell
//! effects go through [`WorldCtx::apply_effect`] like potions; hindrance
//! kinds (`EffectKind::is_hindrance`) multiply the speed.

use std::future::Future;
use std::pin::Pin;
use std::time::Instant;

use protocol::{
    Anim, Appearance, EffectKind, EntityId, House, NpcEvent, OfferItem, PlacedFurniture, ServerMsg,
    SpellId, SpellTarget, TracePoint, VisibleEffect, XpGain, encode_server,
};
use sqlx::PgPool;

use super::entities::ZoneId;
pub use super::instances::{FeatureInstance, FeatureKind, ZoneRules};
use super::world::WorldSim;

/// One active potion effect on a character in the world.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ActiveEffect {
    pub kind: EffectKind,
    /// The potion drunk (catalog id), for the HUD; 0 for a fortune.
    pub item_def: u16,
    /// Which fortune, for `EffectKind::Fortune` (else `None`).
    pub fortune: Option<protocol::FortuneKind>,
    /// What viewers see, if anything.
    pub visible: Option<VisibleEffect>,
    /// Movement speed in percent while active (`SPEED_PCT_NORMAL` = unchanged).
    pub speed_pct: u16,
    pub until: Instant,
}

/// The trade messages, as the world hands them to `trade::world::Trades`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TradeInput {
    Request {
        target: EntityId,
    },
    Respond {
        trade_id: u32,
        accept: bool,
    },
    SetOffer {
        trade_id: u32,
        items: Vec<OfferItem>,
        crowns: i64,
    },
    Lock {
        trade_id: u32,
        locked: bool,
    },
    Confirm {
        trade_id: u32,
        rev: u32,
    },
    Cancel {
        trade_id: u32,
    },
}

/// The potion messages, as the world hands them to `potions::world::Potions`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PotionInput {
    Drink { item_id: i64 },
    MimicDraughtAsk { item_id: i64, target: EntityId },
    MimicDraughtAnswer { request_id: u32, accept: bool },
}

/// Class messages, as the world hands them to
/// `classes::world::Classes` (the door itself arrives as `on_door`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClassInput {
    /// `ClientMsg::TraceGesture`.
    Trace {
        round: u8,
        beat: u8,
        start_offset_ms: i16,
        points: Vec<TracePoint>,
    },
    /// `ClientMsg::PickAnswer`.
    Pick {
        round: u8,
        beat: u8,
        choice: u8,
        offset_ms: i16,
    },
}

/// Spells cast outside duels, as the world hands them to
/// `spells::world::Spells`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SpellInput {
    Cast { spell: SpellId, target: SpellTarget },
    End { spell: SpellId },
}

/// Dueling messages, as the world hands them to
/// `dueling::world::Duels`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DuelMsg {
    Challenge { target: EntityId },
    ChallengeName { name: String },
    Respond { challenge_id: u32, accept: bool },
    Cancel { challenge_id: u32 },
    Cast { spell: SpellId },
    Forfeit,
    List,
    Spectate { duel_id: u32 },
}

/// NPCs in the open castle (protocol v12): the messages the world hands to
/// `crate::residents::world::Residents`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NpcInput {
    /// `ClientMsg::NpcInteract`.
    Interact { id: EntityId },
}

/// The result of an [`ExtJob`], run on the world thread. The payload types
/// belong to the feature modules (opaque to the world).
pub enum ExtDone {
    Trade(crate::trade::world::TradeDone),
    Potion(crate::potions::world::PotionDone),
    Find(crate::finds::world::FindDone),
    Class(crate::classes::world::ClassDone),
    Spell(crate::spells::world::SpellDone),
    Duel(crate::dueling::world::DuelDone),
    /// Anything else: run with a context.
    Run(Box<dyn FnOnce(&mut WorldCtx<'_>) + Send>),
}

impl std::fmt::Debug for ExtDone {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            ExtDone::Trade(_) => "ExtDone::Trade",
            ExtDone::Potion(_) => "ExtDone::Potion",
            ExtDone::Find(_) => "ExtDone::Find",
            ExtDone::Class(_) => "ExtDone::Class",
            ExtDone::Spell(_) => "ExtDone::Spell",
            ExtDone::Duel(_) => "ExtDone::Duel",
            ExtDone::Run(_) => "ExtDone::Run",
        })
    }
}

/// An NPC to add to an instance (`WorldCtx::spawn_npc`).
#[derive(Debug, Clone, PartialEq)]
pub struct NpcSpawn {
    /// Data id (`EntityInfo.npc`).
    pub npc: String,
    /// Nameplate.
    pub name: String,
    pub house: House,
    pub appearance: Appearance,
    pub pos: [f32; 3],
    pub yaw: f32,
}

/// Database work for a feature: given the pool, a future (run on the tokio
/// runtime) that resolves to what the world thread does next.
pub type ExtJob = Box<dyn FnOnce(PgPool) -> Pin<Box<dyn Future<Output = ExtDone> + Send>> + Send>;

/// What a feature handler may see and do in the world, by entity id. Every
/// lookup answers `None` / `false` for an entity that is no longer there.
pub struct WorldCtx<'a> {
    pub(super) sim: &'a mut WorldSim,
    pub(super) now: Instant,
}

impl WorldCtx<'_> {
    /// When the current command or tick happened.
    pub fn now(&self) -> Instant {
        self.now
    }

    fn index(&self, e: EntityId) -> Option<usize> {
        self.sim.ents.index_of(e)
    }

    /// The character behind a player entity (`None` for NPCs).
    pub fn character_id(&self, e: EntityId) -> Option<i64> {
        self.index(e)
            .filter(|&i| self.sim.ents.info[i].npc.is_none())
            .map(|i| self.sim.ents.info[i].character_id)
    }

    /// The entity of an online character.
    pub fn entity_of(&self, character_id: i64) -> Option<EntityId> {
        self.sim.by_character.get(&character_id).copied()
    }

    pub fn name(&self, e: EntityId) -> Option<&str> {
        self.index(e).map(|i| self.sim.ents.info[i].name.as_str())
    }

    pub fn house(&self, e: EntityId) -> Option<House> {
        self.index(e).map(|i| self.sim.ents.house[i])
    }

    pub fn zone(&self, e: EntityId) -> Option<ZoneId> {
        self.index(e).map(|i| self.sim.ents.zone[i])
    }

    pub fn position(&self, e: EntityId) -> Option<[f32; 3]> {
        self.index(e).map(|i| self.sim.ents.pos[i])
    }

    /// The character's own look (what it wears, without effects).
    pub fn appearance(&self, e: EntityId) -> Option<Appearance> {
        self.index(e).map(|i| self.sim.ents.info[i].appearance)
    }

    /// Distance between two characters in the same zone (metres, in 3D);
    /// `None` if either is gone or they are in different zones.
    pub fn distance(&self, a: EntityId, b: EntityId) -> Option<f32> {
        let (i, j) = (self.index(a)?, self.index(b)?);
        (self.sim.ents.zone[i] == self.sim.ents.zone[j])
            .then(|| super::world::dist2(self.sim.ents.pos[i], self.sim.ents.pos[j]).sqrt())
    }

    /// `a` and `b` are friends with no block either way.
    pub fn friends(&self, a: EntityId, b: EntityId) -> bool {
        let (Some(i), Some(other)) = (self.index(a), self.character_id(b)) else {
            return false;
        };
        let s = &self.sim.ents.social[i];
        s.is_friend(other) && !s.any_block(other)
    }

    /// A block between `a` and `b`, either way.
    pub fn blocked_either(&self, a: EntityId, b: EntityId) -> bool {
        match (self.index(a), self.character_id(b)) {
            (Some(i), Some(other)) => self.sim.ents.social[i].any_block(other),
            _ => false,
        }
    }

    /// Takes one token from the character's action bucket (doors, dorm,
    /// trade and potion actions share it); false when over the limit.
    pub fn take_action(&mut self, e: EntityId) -> bool {
        let now = self.now;
        let Some(i) = self.index(e) else {
            return false;
        };
        self.sim.ents.presence[i].actions.take(
            now,
            super::limits::ACTION_BURST,
            super::limits::ACTION_PER_SECOND,
        )
    }

    /// Sends a reply to the character (its own outbound queue; a full queue
    /// disconnects it as too slow, like every direct reply).
    pub fn send(&mut self, e: EntityId, msg: &ServerMsg) {
        if let Some(i) = self.index(e) {
            self.sim.send(i, encode_server(msg).into());
        }
    }

    /// Sends a message another player caused (a trade or Mimic Draught request):
    /// through the notice backlog, so a flood cannot disconnect the recipient.
    pub fn notice(&mut self, e: EntityId, msg: &ServerMsg) {
        if let Some(i) = self.index(e) {
            self.sim.notice(i, encode_server(msg).into());
        }
    }

    /// Runs database work off the world thread; its result comes back to
    /// the feature's `on_done` (see [`ExtDone`]). Without a database (unit
    /// tests) the job waits in `WorldSim::take_ext_jobs`.
    pub fn spawn(&mut self, job: ExtJob) {
        self.sim.ext_jobs.push(job);
    }

    /// The character's active effects.
    pub fn effects(&self, e: EntityId) -> &[ActiveEffect] {
        match self.index(e) {
            Some(i) => &self.sim.ents.effects[i].active,
            None => &[],
        }
    }

    pub fn has_effect(&self, e: EntityId, kind: EffectKind) -> bool {
        self.effects(e).iter().any(|x| x.kind == kind)
    }

    /// Starts an effect (replacing one of the same kind and the kind it
    /// excludes). Viewers see it from the next tick; the character gets
    /// `OwnEffects` at once.
    pub fn apply_effect(&mut self, e: EntityId, effect: ActiveEffect) {
        if let Some(i) = self.index(e) {
            self.sim.apply_effect(i, effect, self.now);
        }
    }

    /// Ends an effect early (no-op if it is not active).
    pub fn remove_effect(&mut self, e: EntityId, kind: EffectKind) {
        if let Some(i) = self.index(e) {
            self.sim.remove_effect(i, kind, self.now);
        }
    }

    // ---- Time, players and their school state.

    /// The server clock at this command or tick (Unix ms, UTC): what
    /// schedules and lesson beats are reckoned in (`crate::clock`).
    pub fn unix_ms(&self) -> i64 {
        self.sim.clock.unix_ms_at(self.now)
    }

    /// A scripted NPC (not a player): no character id, no trades or duels.
    pub fn is_npc(&self, e: EntityId) -> bool {
        self.index(e)
            .is_some_and(|i| self.sim.ents.info[i].npc.is_some())
    }

    /// The online player with this name (case-insensitive).
    pub fn entity_by_name(&self, name: &str) -> Option<EntityId> {
        let id = *self
            .sim
            .by_name
            .get(&crate::characters::name_key(name.trim()))?;
        self.index(id).map(|_| id)
    }

    /// A player's level (1-20; 0 for NPCs).
    pub fn level(&self, e: EntityId) -> Option<u8> {
        self.index(e).map(|i| self.sim.ents.info[i].level)
    }

    /// A player's total XP, learned spells and dueling loadout.
    pub fn school(&self, e: EntityId) -> Option<&crate::progression::SchoolState> {
        self.index(e).map(|i| &self.sim.ents.school[i])
    }

    /// After an XP grant committed: stores the total, sends `Progress` (with
    /// `gained`) and, if the level changed, `LevelChanged` to viewers.
    pub fn progress_changed(&mut self, e: EntityId, xp: u32, gained: Option<XpGain>) {
        if let Some(i) = self.index(e) {
            self.sim.set_progress(i, xp, gained);
        }
    }

    /// After spells were learned or the loadout changed: stores them and
    /// sends `Spellbook` (`new`: the spells just learned).
    pub fn set_spellbook(
        &mut self,
        e: EntityId,
        learned: Vec<SpellId>,
        loadout: Vec<SpellId>,
        new: Vec<SpellId>,
    ) {
        if let Some(i) = self.index(e) {
            self.sim.set_spellbook(i, learned, loadout, new);
        }
    }

    /// Mounts a player on a broom where it stands (a Flying
    /// class mounting its students; no skill needed), sending `Mounted`.
    /// False when there is no open sky at the spot.
    pub fn mount_broom(&mut self, e: EntityId) -> bool {
        match self.index(e) {
            Some(i) if self.sim.ents.info[i].npc.is_none() => self.sim.mount_for_class(i, self.now),
            _ => false,
        }
    }

    /// Whether a player is on a broom.
    pub fn mounted(&self, e: EntityId) -> bool {
        self.index(e)
            .is_some_and(|i| self.sim.ents.mover[i].mount.is_some())
    }

    /// A player's Flying skill XP changed (a Flying class's
    /// reward): may they mount, at which tier.
    pub fn set_flying_xp(&mut self, e: EntityId, xp: u32) {
        if let Some(i) = self.index(e) {
            let m = &mut self.sim.ents.mover[i];
            m.flying_xp = m.flying_xp.max(xp);
        }
    }

    /// Shows `Anim::Cast` on a character or NPC for `CAST_ANIM_MS` (it ends
    /// early if they move).
    pub fn play_cast(&mut self, e: EntityId) {
        if let Some(i) = self.index(e) {
            self.sim
                .play_anim(i, Anim::Cast, protocol::CAST_ANIM_MS, self.now);
        }
    }

    /// Lights or puts out a character's wand (`VisibleEffect::Lucerna`, seen
    /// by everyone who has them in view).
    pub fn set_lucerna(&mut self, e: EntityId, on: bool) {
        if let Some(i) = self.index(e) {
            self.sim.set_lucerna(i, on, self.now);
        }
    }

    pub fn lucerna(&self, e: EntityId) -> bool {
        self.index(e)
            .is_some_and(|i| self.sim.ents.effects[i].lucerna)
    }

    /// Sends `msg` to every player in `e`'s zone within `radius_m` of it
    /// (`e` included), e.g. a spell flourish.
    pub fn send_near(&mut self, e: EntityId, radius_m: f32, msg: &ServerMsg) {
        if let Some(i) = self.index(e) {
            self.sim.send_near(i, radius_m, msg);
        }
    }

    /// The castle area of `kind` that `e` stands in (its id), e.g. the
    /// Dueling Club. `None` outside, or outside the castle.
    pub fn area(&self, e: EntityId, kind: sim_core::AreaKind) -> Option<&str> {
        let i = self.index(e)?;
        if self.sim.ents.zone[i] != super::entities::CASTLE {
            return None;
        }
        self.sim
            .castle
            .area_at(self.sim.ents.pos[i], kind)
            .map(|a| a.id.as_str())
    }

    /// Every player standing in a castle area of `kind` (the Dueling Club
    /// teaches whoever walks in). A scan of the castle's players: call it
    /// once a second, not every tick.
    pub fn players_in_areas(&self, kind: sim_core::AreaKind) -> Vec<EntityId> {
        let e = &self.sim.ents;
        (0..e.len())
            .filter(|&i| e.zone[i] == super::entities::CASTLE && e.info[i].npc.is_none())
            .filter(|&i| self.sim.castle.area_at(e.pos[i], kind).is_some())
            .map(|i| e.ids[i])
            .collect()
    }

    /// A furniture item placed in dorm zone `zone`, and the room's owner.
    pub fn placed_item(&self, zone: ZoneId, item_id: i64) -> Option<(PlacedFurniture, i64)> {
        let d = self.sim.instances.get(zone)?;
        let item = d.items.as_ref()?.iter().find(|p| p.item_id == item_id)?;
        Some((*item, d.owner))
    }

    // ---- Instances of the features' own (class sections, arenas).

    /// Opens a new instance of `kind` with instance layout `layout` (one the
    /// server loaded, `realtime::layouts`). `None` if the layout is unknown.
    /// An instance closes by itself when its last player leaves (NPCs do not
    /// keep it open): the owning handler's `on_instance_closed` follows.
    pub fn open_instance(&mut self, kind: FeatureKind, layout: &str) -> Option<ZoneId> {
        self.sim.open_feature(kind, layout)
    }

    /// The instance in `zone` (its kind, layout, members, NPCs, rules).
    pub fn instance(&self, zone: ZoneId) -> Option<&FeatureInstance> {
        self.sim.instances.feature(zone)
    }

    /// Takes a player from the castle into instance `zone` at `spot`
    /// (position and yaw; the layout's spawns when `None`), remembering
    /// where they stood to return them there. False when they are not in the
    /// castle or the instance is gone. `ZoneChanged` goes to them;
    /// handlers hear `on_zone_changed`.
    pub fn enter_instance(
        &mut self,
        e: EntityId,
        zone: ZoneId,
        spot: Option<([f32; 3], f32)>,
    ) -> bool {
        match self.index(e) {
            Some(i) => self.sim.enter_feature(i, zone, spot, self.now),
            None => false,
        }
    }

    /// Sends a player in an instance back where they came from.
    pub fn leave_instance(&mut self, e: EntityId) {
        if let Some(i) = self.index(e)
            && self.sim.ents.zone[i] != super::entities::CASTLE
        {
            self.sim.leave_dorm(i, self.now);
        }
    }

    /// Sends everyone in instance `zone` back and closes it (its NPCs go).
    pub fn close_instance(&mut self, zone: ZoneId) {
        self.sim.close_feature(zone, self.now);
    }

    /// Players everyone in instance `zone` always sees, ahead of the view
    /// limit (the professor, the two duelists).
    pub fn set_instance_priority(&mut self, zone: ZoneId, ids: Vec<EntityId>) {
        if let Some(f) = self.sim.instances.feature_mut(zone) {
            f.priority = ids;
        }
    }

    /// Sends `msg` to every player in `zone` (an instance or a dorm room).
    pub fn broadcast(&mut self, zone: ZoneId, msg: &ServerMsg) {
        let frame: bytes::Bytes = encode_server(msg).into();
        for m in self.sim.instances.members(zone).to_vec() {
            if let Some(i) = self.index(m) {
                self.sim.send(i, frame.clone());
            }
        }
    }

    // ---- NPCs.

    /// Adds a scripted NPC to instance `zone` (removed with the instance).
    /// It is an entity in snapshots like a player, with `EntityInfo.npc`.
    pub fn spawn_npc(&mut self, zone: ZoneId, npc: NpcSpawn) -> Option<EntityId> {
        self.sim.spawn_npc(zone, npc, self.now)
    }

    /// Moves an NPC (no validation: scripts place it) and sets its anim.
    pub fn move_npc(&mut self, e: EntityId, pos: [f32; 3], yaw: f32, anim: Anim) {
        if let Some(i) = self.index(e)
            && self.sim.ents.info[i].npc.is_some()
        {
            self.sim.move_npc(i, pos, yaw, anim);
        }
    }

    /// An NPC's scripted action, to every player in its zone (`NpcEvent`).
    pub fn npc_event(&mut self, e: EntityId, event: NpcEvent) {
        let Some(i) = self.index(e) else {
            return;
        };
        if self.sim.ents.info[i].npc.is_none() {
            return;
        }
        if matches!(event, NpcEvent::Demonstrate { .. }) {
            self.sim
                .play_anim(i, Anim::Cast, protocol::CAST_ANIM_MS, self.now);
        }
        if let NpcEvent::Emote { emote } = event {
            let ms = emote.duration_ms().unwrap_or(protocol::EMOTE_WAVE_MS);
            self.sim.play_anim(i, emote.anim(), ms, self.now);
        }
        let zone = self.sim.ents.zone[i];
        self.broadcast(zone, &ServerMsg::NpcEvent { id: e, event });
    }

    pub fn remove_npc(&mut self, e: EntityId) {
        self.sim.remove_npc(e);
    }

    /// NPCs: the NPC data id of entity `e` (`EntityInfo.npc`), `None` for
    /// players and unknown ids.
    pub fn npc_id(&self, e: EntityId) -> Option<&str> {
        self.index(e)
            .and_then(|i| self.sim.ents.info[i].npc.as_deref())
    }

    /// NPCs: the whole castle layout (every moving flight position), for
    /// the residents' nav graphs (`sim_core::npcs::NpcNavs`).
    pub fn castle_layout(&self) -> &sim_core::Layout {
        self.sim.castle_all.layout()
    }

    // ---- Spells L20 (CORE): forced moves, Aperio, spell targets.

    /// Starts a forced move of player `e` to `to` (slide over `move_ms`,
    /// stand still until `hold_ms`, both from now) and sends it
    /// `ForcedMove`. False for NPCs, riders, someone already being moved, or
    /// `to` off the ground.
    pub fn force_move(
        &mut self,
        e: EntityId,
        to: [f32; 3],
        move_ms: u16,
        hold_ms: u16,
        by: EntityId,
    ) -> bool {
        match self.index(e) {
            Some(i) => self.sim.force_move(i, to, move_ms, hold_ms, by, self.now),
            None => false,
        }
    }

    /// The walking speed (percent of normal) `e`'s moves are checked with
    /// (`OwnEffects.speed_pct`: the zone's, boosts and hindrances).
    pub fn speed_pct(&self, e: EntityId) -> Option<u16> {
        self.index(e).map(|i| self.sim.ents.mover[i].speed_pct)
    }

    /// Whether the server is moving `e` (a forced move, its hold and tail).
    pub fn being_moved(&self, e: EntityId) -> bool {
        self.index(e)
            .is_some_and(|i| self.sim.ents.mover[i].forced.is_some())
    }

    /// `sim_core::movement::pull_destination` for `target` toward `toward`
    /// in the target's zone world (the castle at the current staircase
    /// state, the target house's gates). `Wall` when either is gone or they
    /// are in different zones.
    pub fn pull_destination(
        &self,
        target: EntityId,
        toward: EntityId,
        distance_m: f32,
        min_m: f32,
        gap_m: f32,
    ) -> Result<[f32; 3], sim_core::PullBlocked> {
        let (Some(i), Some(j)) = (self.index(target), self.index(toward)) else {
            return Err(sim_core::PullBlocked::Wall);
        };
        if self.sim.ents.zone[i] != self.sim.ents.zone[j] {
            return Err(sim_core::PullBlocked::Wall);
        }
        sim_core::pull_destination(
            self.sim.world_of(i),
            &self.sim.params,
            self.sim.ents.pos[i],
            self.sim.ents.pos[j],
            distance_m,
            min_m,
            gap_m,
        )
    }

    /// Aperio's spots around `e` in its zone (passages only in the castle),
    /// nearest first, at most `protocol::REVEAL_SPOTS_MAX`.
    pub fn reveal_near(
        &self,
        e: EntityId,
        radius_m: f32,
        height_m: f32,
    ) -> Vec<protocol::RevealedSpot> {
        let Some(i) = self.index(e) else {
            return Vec::new();
        };
        let secrets = (self.sim.ents.zone[i] == super::entities::CASTLE)
            .then(|| self.sim.explore_index.secrets())
            .flatten();
        sim_core::reveal_near(
            self.sim.world_of(i),
            secrets,
            self.sim.ents.pos[i],
            radius_m,
            height_m,
        )
        .into_iter()
        .map(|(kind, pos)| protocol::RevealedSpot {
            kind,
            pos: protocol::QPos::from_m(pos),
        })
        .collect()
    }

    /// The animation `e` shows now (seated: `Anim::Sit`).
    pub fn anim(&self, e: EntityId) -> Option<Anim> {
        self.index(e).map(|i| self.sim.ents.state[i].anim)
    }

    /// Whether `e` has a trade window open.
    pub fn trading(&self, e: EntityId) -> bool {
        self.sim.trades.is_trading(e)
    }

    /// Whether `e` is in an arena (dueling or watching) or has a challenge
    /// pending. Call it from spell handlers only (inside the dueling
    /// handler its own state is taken out and this answers from the zone
    /// alone).
    pub fn dueling(&self, e: EntityId) -> bool {
        self.sim.duels.is_dueling(e)
            || self.zone(e).is_some_and(|z| {
                self.instance(z)
                    .is_some_and(|f| matches!(f.kind, FeatureKind::Arena { .. }))
            })
    }

    /// Whether `e` is in a class section instance.
    pub fn in_class(&self, e: EntityId) -> bool {
        self.zone(e).is_some_and(|z| {
            self.instance(z)
                .is_some_and(|f| matches!(f.kind, FeatureKind::Class { .. }))
        })
    }

    /// What the hindering social spells need of a target: a player, not
    /// seated, not on a broom, not in class, not trading, not dueling, not
    /// being moved.
    pub fn spell_target_free(&self, e: EntityId) -> bool {
        self.character_id(e).is_some()
            && self.anim(e) != Some(Anim::Sit)
            && !self.mounted(e)
            && !self.in_class(e)
            && !self.trading(e)
            && !self.dueling(e)
            && !self.being_moved(e)
    }
}

#[cfg(test)]
mod tests {
    use std::time::{Duration, Instant};

    use protocol::{
        Anim, DormErrorCode, EntityEntry, FindOutcome, STATION_GRANT_SECS, StationKind,
        TradeErrorCode,
    };

    use super::*;
    use crate::realtime::world::tests::{TestClient, join, sim};
    use crate::realtime::world::{Input, WorldCmd};

    fn run(s: &mut WorldSim, f: impl FnOnce(&mut WorldCtx<'_>) + Send + 'static) {
        s.handle(WorldCmd::ExtDone(ExtDone::Run(Box::new(f))));
    }

    fn ticks(s: &mut WorldSim, at: Instant, n: u32) {
        for _ in 0..n {
            s.tick(at);
        }
    }

    fn mv(s: &mut WorldSim, c: &TestClient, at: Instant, seq: u32, pos: [f32; 3]) {
        let input = Input::Move {
            seq,
            pos,
            yaw: 0.0,
            anim: Anim::Run,
        };
        s.input(c.id, at, input);
    }

    fn pick_me_up_tonic(until: Instant) -> ActiveEffect {
        ActiveEffect {
            fortune: None,
            kind: EffectKind::PickMeUpTonic,
            item_def: 3309,
            visible: Some(VisibleEffect::Steam),
            speed_pct: 115,
            until,
        }
    }

    #[test]
    fn effects_show_to_viewers_speed_up_moves_and_expire() {
        let mut s = sim();
        let mut a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
        let mut b = join(&mut s, "Bea", House::Burrowsby, [101.0, 0.0, 100.0]);
        let t0 = Instant::now();
        ticks(&mut s, t0, 4);
        a.drain();
        b.drain();

        let id = a.id;
        let until = t0 + Duration::from_secs(5);
        run(&mut s, move |ctx| {
            ctx.apply_effect(id, pick_me_up_tonic(until))
        });
        let own: Vec<_> = a
            .drain()
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::OwnEffects {
                    effects, speed_pct, ..
                } => Some((effects, speed_pct)),
                _ => None,
            })
            .collect();
        assert_eq!(own.len(), 1);
        assert_eq!(own[0].1, 115);
        assert_eq!(own[0].0[0].kind, EffectKind::PickMeUpTonic);
        // Viewers (and the drinker) hear with the next tick, once.
        ticks(&mut s, t0, 1);
        let changed = |c: &mut TestClient| {
            c.drain()
                .into_iter()
                .filter_map(|m| match m {
                    ServerMsg::EffectsChanged {
                        id,
                        effects_version,
                        effects,
                    } => Some((id, effects_version, effects)),
                    _ => None,
                })
                .collect::<Vec<_>>()
        };
        assert_eq!(changed(&mut b), vec![(id, 1, vec![VisibleEffect::Steam])]);
        assert_eq!(changed(&mut a).len(), 1);

        // A newcomer sees the effect in Ada's entry.
        let mut c = join(&mut s, "Cy", House::Burrowsby, [100.0, 0.0, 102.0]);
        ticks(&mut s, t0, 2);
        let entry = c
            .drain()
            .into_iter()
            .flat_map(|m| match m {
                ServerMsg::Snapshot { entered, .. } => entered,
                _ => vec![],
            })
            .find_map(|e| match e {
                EntityEntry::Full(info) if info.id == id => Some(info),
                _ => None,
            })
            .expect("Ada entered Cy's view");
        assert_eq!(entry.effects_version, 1);
        assert_eq!(entry.effects, vec![VisibleEffect::Steam]);

        // Boosted: a run a normal character could not make in its credit.
        let i = s.ents.index_of(id).unwrap();
        let start = s.ents.pos[i];
        let at = t0 + Duration::from_secs(2); // credit is full (1 s)
        let far = sim_core::MoveParams::default().max_speed() * 1.1;
        mv(&mut s, &a, at, 1, [start[0] + far, 0.0, start[2]]);
        assert_eq!(s.ents.pos[i][0], start[0] + far, "accepted at 115 %");

        // Expiry (checked once a second) brings the speed back, with a grace.
        let later = until + Duration::from_millis(10);
        ticks(&mut s, later, 20);
        assert!(s.ents.effects[i].active.is_empty());
        assert_eq!(s.ents.info[i].effects_version, 2);
        assert_eq!(
            s.move_speed_pct(i, later),
            115,
            "moves in flight still pass"
        );
        assert_eq!(s.move_speed_pct(i, later + Duration::from_secs(2)), 100);
    }

    #[test]
    fn shrinking_and_swelling_replace_each_other() {
        let mut s = sim();
        let a = join(&mut s, "Ada", House::Skytalon, [100.0, 0.0, 100.0]);
        let id = a.id;
        let until = Instant::now() + Duration::from_secs(60);
        let scale = move |kind, pct| ActiveEffect {
            fortune: None,
            kind,
            item_def: 3304,
            visible: Some(VisibleEffect::Scale { pct }),
            speed_pct: 100,
            until,
        };
        run(&mut s, move |ctx| {
            ctx.apply_effect(id, scale(EffectKind::Shrinking, 50));
            ctx.apply_effect(id, scale(EffectKind::Swelling, 130));
            assert!(ctx.has_effect(id, EffectKind::Swelling));
            assert!(!ctx.has_effect(id, EffectKind::Shrinking));
            ctx.remove_effect(id, EffectKind::Swelling);
            assert!(ctx.effects(id).is_empty());
        });
    }

    #[test]
    fn stations_open_with_interact_and_close_when_you_walk_away() {
        let mut s = sim();
        let mut a = join(&mut s, "Ada", House::Burrowsby, [110.0, 0.0, 101.0]);
        a.drain();
        let now = Instant::now();
        s.input(a.id, now, Input::Interact("owl_exchange".into()));
        assert_eq!(
            a.drain(),
            vec![ServerMsg::StationOpened {
                kind: StationKind::OwlExchange,
                target: "owl_exchange".into(),
                expires_in_secs: STATION_GRANT_SECS,
            }]
        );
        let access = s
            .station_access(a.cid, StationKind::OwlExchange, now)
            .unwrap();
        assert_eq!(access.target, "owl_exchange");
        assert!(
            s.station_access(a.cid, StationKind::Cauldron, now)
                .is_none()
        );
        // Drifting a little is fine; walking off closes it; so does time.
        let i = s.ents.index_of(a.id).unwrap();
        s.ents.pos[i] = [113.0, 0.0, 100.0];
        assert!(
            s.station_access(a.cid, StationKind::OwlExchange, now)
                .is_some()
        );
        s.ents.pos[i] = [120.0, 0.0, 100.0];
        assert!(
            s.station_access(a.cid, StationKind::OwlExchange, now)
                .is_none()
        );
        s.ents.pos[i] = [110.0, 0.0, 100.0];
        let late = now + Duration::from_secs(u64::from(STATION_GRANT_SECS) + 1);
        assert!(
            s.station_access(a.cid, StationKind::OwlExchange, late)
                .is_none()
        );

        // Out of reach, or another house's station.
        s.ents.pos[i] = [150.0, 0.0, 100.0];
        s.input(a.id, now, Input::Interact("owl_exchange".into()));
        s.input(a.id, now, Input::Interact("craft_charms_r".into()));
        let codes: Vec<_> = a
            .drain()
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::DormError { code, .. } => Some(code),
                _ => None,
            })
            .collect();
        assert_eq!(
            codes,
            vec![DormErrorCode::TooFar, DormErrorCode::WrongHouse]
        );
        // Leaving the world drops the grants.
        s.ents.pos[i] = [110.0, 0.0, 100.0];
        s.input(a.id, now, Input::Interact("owl_exchange".into()));
        assert!(
            s.station_access(a.cid, StationKind::OwlExchange, now)
                .is_some()
        );
        s.remove_player(a.id);
        assert!(s.station_grants.is_empty());
    }

    /// The world hands class, spell and duel input to the feature handlers.
    #[test]
    fn feature_input_reaches_its_handlers() {
        let mut s = sim();
        let mut a = join(&mut s, "Ada", House::Burrowsby, [120.0, 0.0, 100.5]);
        a.drain();
        let now = Instant::now();
        s.input(a.id, now, Input::Interact("find_hall".into()));
        s.input(a.id, now, Input::Trade(TradeInput::Request { target: 7 }));
        s.input(a.id, now, Input::Potion(PotionInput::Drink { item_id: 5 }));
        s.input(a.id, now, Input::UseFurniture(5));
        let got = a.drain();
        assert!(got.contains(&ServerMsg::FindResult {
            spot: "find_hall".into(),
            outcome: FindOutcome::Failed,
        }));
        // Nobody is entity 7: the trade handler (`trade::world`) refuses.
        assert!(got.contains(&ServerMsg::TradeError {
            trade_id: None,
            code: TradeErrorCode::UnknownTarget,
        }));
        // A drink is a database job (`potions::world`), answered when it ends.
        assert!(
            !got.iter()
                .any(|m| matches!(m, ServerMsg::PotionError { .. }))
        );
        assert_eq!(s.take_ext_jobs().len(), 1, "the drink's job");
        assert!(got.contains(&ServerMsg::DormError {
            code: DormErrorCode::ItemNotFound,
            item_id: Some(5),
        }));
        // Jobs wait for the executor; without one, tests take them.
        let id = a.id;
        run(&mut s, move |ctx| {
            ctx.spawn(Box::new(|_pool| {
                Box::pin(async { ExtDone::Run(Box::new(|_| {})) })
            }));
            assert_eq!(ctx.name(id), Some("Ada"));
        });
        assert_eq!(s.take_ext_jobs().len(), 1);
    }
}

#[cfg(test)]
#[path = "school_tests.rs"]
mod school_tests;

#[cfg(test)]
#[path = "spells_l20_tests.rs"]
mod spells_l20_tests;
