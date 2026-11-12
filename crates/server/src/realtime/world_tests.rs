//! Tests of the world simulation (`world.rs`, `instances.rs`, `social.rs`),
//! driven directly without sockets or a database: database jobs are run
//! against [`FakeDb`], which follows the same rules as `dorm::store`.

use std::collections::HashMap;
use std::sync::atomic::{AtomicI64, Ordering};

use protocol::{
    DORM_ITEM_CAP, DormErrorCode, Emote, EntityEntry, FurnitureEdit, House, LookCache,
    PlacedFurniture, SnapshotReceiver, decode_server,
};

use super::*;
use crate::characters::{SavedPosition, SavedZone};
use crate::dorm::rules;
use crate::metrics::Registry;
use crate::realtime::entities::{ReturnPoint, Social};
use crate::realtime::instances::{DORM_GUESTS_MAX, GUEST_MEMO_TTL};
use crate::realtime::jobs::{Edit, Job};

/// A 200 m square hall (level 0) with a 50 m gallery 6 m above one corner
/// (level 1), and the Roarwing common room through a doorway east of the
/// hall (gated), with its dorm door.
pub(crate) const LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 },
        { "id": "gallery", "kind": "room", "name": "Gallery", "level": 1, "cell": "c",
          "min": [0, 0], "max": [50, 50], "y": 6, "ceiling": 4 },
        { "id": "common_r", "kind": "room", "name": "Red Common Room", "level": 0, "cell": "c",
          "min": [200, 90], "max": [220, 110], "y": 0, "ceiling": 6, "house": "roarwing" }
    ],
    "spawns": [{ "id": "s", "pos": [100, 0, 100], "yaw": 0 }],
    "interactables": [
        { "id": "dorm_door_r", "kind": "dorm_door", "house": "roarwing",
          "pos": [215, 0, 100], "yaw": 1.5707964, "radius": 1.5 },
        { "id": "owl_exchange", "kind": "owl_exchange", "pos": [110, 0, 100], "radius": 2.5 },
        { "id": "craft_charms_r", "kind": "craft_charms", "house": "roarwing",
          "pos": [210, 0, 95], "radius": 1.8 },
        { "id": "find_hall", "kind": "find_spot", "pos": [120, 0, 100], "radius": 1.2 },
        { "id": "class_door_t", "kind": "class_door", "pos": [130, 0, 100], "radius": 1.5 }
    ],
    "areas": [
        { "id": "club", "kind": "dueling_club", "name": "Club",
          "min": [140, -1, 90], "max": [160, 3, 110] }
    ]
}"#;

pub(crate) struct TestClient {
    pub id: EntityId,
    pub cid: i64,
    pub rx: mpsc::Receiver<Bytes>,
    pub control: Arc<ConnControl>,
    /// Applies snapshots as a client does.
    pub snaps: SnapshotReceiver,
    /// Resolves cached entries as a client does.
    pub looks: LookCache,
}

impl TestClient {
    /// Every message received so far. Snapshots come back with their deltas
    /// applied (`full` holds every updated state, absolute; `deltas` is
    /// empty) and every entry resolved to `EntityEntry::Full`.
    pub fn drain(&mut self) -> Vec<ServerMsg> {
        let mut v = Vec::new();
        while let Ok(b) = self.rx.try_recv() {
            let msg = decode_server(&b).unwrap();
            v.push(match msg {
                ServerMsg::Welcome { .. } => {
                    self.snaps.reset();
                    self.looks.clear();
                    msg
                }
                ServerMsg::ZoneChanged { .. } => {
                    self.snaps.reset();
                    msg
                }
                ServerMsg::Snapshot {
                    tick,
                    entered,
                    full,
                    deltas,
                    left,
                    looks,
                } => {
                    let mut infos = Vec::new();
                    self.looks
                        .resolve(&entered, &mut infos)
                        .expect("every cached entry was sent in full before");
                    let mut states = Vec::new();
                    self.snaps
                        .apply(
                            entered.iter().map(EntityEntry::id),
                            &full,
                            &deltas,
                            &left,
                            &mut states,
                        )
                        .expect("snapshots stay in sync");
                    ServerMsg::Snapshot {
                        tick,
                        entered: infos.into_iter().map(EntityEntry::Full).collect(),
                        full: states,
                        deltas: vec![],
                        left,
                        looks,
                    }
                }
                other => other,
            });
        }
        v
    }

    fn chats(&mut self) -> Vec<(EntityId, ChatChannel, String)> {
        self.drain()
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::Chat {
                    from,
                    channel,
                    text,
                    ..
                } => Some((from, channel, text)),
                _ => None,
            })
            .collect()
    }

    fn states_of(&mut self, id: EntityId) -> Vec<EntityState> {
        self.drain()
            .into_iter()
            .flat_map(|m| match m {
                ServerMsg::Snapshot { full, .. } => full,
                _ => vec![],
            })
            .filter(|s| s.id == id)
            .collect()
    }

    fn dorm_errors(&mut self) -> Vec<DormErrorCode> {
        self.drain()
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::DormError { code, .. } => Some(code),
                _ => None,
            })
            .collect()
    }
}

/// The info of an entry [`TestClient::drain`] resolved.
fn full_entry(e: EntityEntry) -> Option<protocol::EntityInfo> {
    match e {
        EntityEntry::Full(info) => Some(info),
        EntityEntry::Cached { .. } => None,
    }
}

pub(crate) fn sim() -> WorldSim {
    WorldSim::new(
        Arc::new(sim_core::World::from_json(LAYOUT).unwrap()),
        WordFilter::from_list("badword"),
        None,
        Arc::new(RealtimeMetrics::register(&Registry::default())),
        1,
    )
}

static NEXT_CHARACTER: AtomicI64 = AtomicI64::new(1);

pub(crate) fn character(name: &str, house: House, pos: Option<[f32; 3]>) -> Character {
    Character {
        id: NEXT_CHARACTER.fetch_add(1, Ordering::Relaxed),
        name: name.into(),
        house,
        zone: SavedZone::Castle,
        last_pos: pos,
        appearance_version: 0,
        appearance: Appearance::default(),
    }
}

/// A character whose resume point is its own dorm (a new character).
pub(crate) fn in_dorm(name: &str, house: House) -> Character {
    Character {
        zone: SavedZone::Dorm,
        ..character(name, house, None)
    }
}

pub(crate) fn join_with(sim: &mut WorldSim, joining: Joining, queue: usize) -> TestClient {
    let (tx, rx) = mpsc::channel(queue);
    let control = Arc::new(ConnControl::default());
    let cid = joining.character.id;
    let id = sim
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

pub(crate) fn join_as(sim: &mut WorldSim, ch: Character, queue: usize) -> TestClient {
    join_with(sim, Joining::alone(ch), queue)
}

pub(crate) fn join(sim: &mut WorldSim, name: &str, house: House, pos: [f32; 3]) -> TestClient {
    join_as(sim, character(name, house, Some(pos)), 256)
}

fn say(sim: &mut WorldSim, c: &TestClient, channel: ChatChannel, text: &str) {
    sim.input(
        c.id,
        Instant::now(),
        Input::Chat {
            channel,
            text: text.into(),
            client_seq: 0,
        },
    );
}

fn mv(sim: &mut WorldSim, c: &TestClient, at: Instant, seq: u32, pos: [f32; 3]) {
    sim.input(
        c.id,
        at,
        Input::Move {
            seq,
            pos,
            yaw: 0.0,
            anim: Anim::Walk,
        },
    );
}

pub(crate) fn ticks(sim: &mut WorldSim, n: u32) {
    for _ in 0..n {
        sim.tick(Instant::now());
    }
}

fn index(sim: &WorldSim, c: &TestClient) -> usize {
    sim.ents.index_of(c.id).unwrap()
}

fn zone_of(sim: &WorldSim, c: &TestClient) -> ZoneIdOf {
    let i = index(sim, c);
    match sim.instances.get(sim.ents.zone[i]) {
        Some(d) => ZoneIdOf::Dorm(d.owner),
        None => ZoneIdOf::Castle,
    }
}

/// Which zone a test character is in, by owner.
#[derive(Debug, PartialEq, Eq)]
enum ZoneIdOf {
    Castle,
    Dorm(i64),
}

/// Makes everyone in `group` friends with each other.
fn befriend(sim: &mut WorldSim, group: &[&TestClient]) {
    for a in group {
        let friends = group
            .iter()
            .filter(|b| b.id != a.id)
            .map(|b| b.cid)
            .collect();
        let i = index(sim, a);
        let mut s = sim.ents.social[i].clone();
        s.friends = friends;
        sim.apply_social(i, s, Instant::now());
    }
}

// ------------------------------------------------------------ fake database

/// What the database holds for the dorm and friends jobs.
#[derive(Default)]
struct FakeDb {
    /// item id -> (owner, item_def, location)
    items: HashMap<i64, (i64, u16, &'static str)>,
    placed: HashMap<i64, Vec<PlacedFurniture>>,
    open: HashMap<i64, bool>,
    social: HashMap<i64, Social>,
    next_item: i64,
}

impl FakeDb {
    fn give(&mut self, owner: i64, item_def: u16) -> i64 {
        self.next_item += 1;
        self.items
            .insert(self.next_item, (owner, item_def, "inventory"));
        self.next_item
    }

    /// Runs every queued job (and those its results queue) to completion.
    fn run(&mut self, sim: &mut WorldSim) {
        loop {
            let jobs = sim.take_jobs();
            if jobs.is_empty() {
                return;
            }
            for job in jobs {
                let cmd = self.execute(job);
                sim.handle(cmd);
            }
        }
    }

    fn execute(&mut self, job: Job) -> WorldCmd {
        match job {
            Job::LoadRoom { owner } => WorldCmd::RoomDone {
                owner,
                outcome: RoomOutcome::Loaded(Some(
                    self.placed.get(&owner).cloned().unwrap_or_default(),
                )),
            },
            Job::SetOpen { owner, open } => {
                self.open.insert(owner, open);
                WorldCmd::RoomDone {
                    owner,
                    outcome: RoomOutcome::OpenSaved,
                }
            }
            Job::LoadSocial { character_id, seq } => WorldCmd::SocialLoaded {
                character_id,
                seq,
                social: Some(self.social.get(&character_id).cloned().unwrap_or_default()),
            },
            Job::Edit { owner, edit } => WorldCmd::RoomDone {
                owner,
                outcome: RoomOutcome::Edited(self.edit(owner, edit)),
            },
        }
    }

    fn edit(&mut self, owner: i64, edit: Edit) -> Result<FurnitureEdit, (DormErrorCode, i64)> {
        let cat = &crate::catalog::shipped().catalog;
        match edit {
            Edit::Place {
                item_id,
                pos,
                yaw,
                areas,
                way,
            } => {
                let err = |c| (c, item_id);
                let &(o, def, loc) = self
                    .items
                    .get(&item_id)
                    .filter(|it| it.0 == owner)
                    .ok_or(err(DormErrorCode::ItemNotFound))?;
                rules::shape_of(cat, def).map_err(err)?;
                if loc != "inventory" {
                    return Err(err(DormErrorCode::ItemUnavailable));
                }
                rules::check(cat, def, &areas, pos, yaw).map_err(err)?;
                way.check(cat, def, item_id, pos, yaw).map_err(err)?;
                let room = self.placed.entry(o).or_default();
                if room.len() >= usize::from(DORM_ITEM_CAP) {
                    return Err(err(DormErrorCode::CapReached));
                }
                let p = PlacedFurniture {
                    item_id,
                    item_def: def,
                    dyes: [0, 0, 0],
                    pos,
                    yaw,
                };
                room.push(p);
                self.items.insert(item_id, (o, def, "dorm"));
                Ok(FurnitureEdit::Placed(p))
            }
            Edit::Move { item_id, pos, yaw } => {
                let room = self.placed.entry(owner).or_default();
                let it = room
                    .iter_mut()
                    .find(|p| p.item_id == item_id)
                    .ok_or((DormErrorCode::ItemNotFound, item_id))?;
                it.pos = pos;
                it.yaw = yaw;
                Ok(FurnitureEdit::Moved { item_id, pos, yaw })
            }
            Edit::Remove { item_id } => {
                let room = self.placed.entry(owner).or_default();
                let before = room.len();
                room.retain(|p| p.item_id != item_id);
                if room.len() == before {
                    return Err((DormErrorCode::ItemNotFound, item_id));
                }
                if let Some(it) = self.items.get_mut(&item_id) {
                    it.2 = "inventory";
                }
                Ok(FurnitureEdit::Removed { item_id })
            }
        }
    }
}

/// The furniture item whose asset contains `tag`.
fn furniture(tag: &str) -> u16 {
    crate::catalog::shipped()
        .catalog
        .items
        .iter()
        .find(|i| i.kind == sim_core::ItemKind::Furniture && i.asset.contains(tag))
        .unwrap()
        .id
}

// ------------------------------------------------------------ joining and moving

#[test]
fn welcome_comes_first_and_spawns_at_the_saved_position() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, [10.0, 0.2, 20.0]);
    match &a.drain()[..] {
        [
            ServerMsg::Welcome {
                your_id,
                spawn,
                zone,
                ..
            },
            // The HUD's wallet, mail owl and effect timers.
            ServerMsg::Wallet { crowns: 0 },
            ServerMsg::MailNotice { unread: 0 },
            ServerMsg::OwnEffects {
                effects,
                speed_pct: 100,
                broom_pct: 100,
            },
            // Level, XP and spells.
            ServerMsg::Progress {
                progress:
                    protocol::Progress {
                        level: 1, xp: 0, ..
                    },
                gained: None,
            },
            ServerMsg::Spellbook { slots: 3, .. },
        ] if effects.is_empty() => {
            assert_eq!(*your_id, a.id);
            assert_eq!(*zone, protocol::Zone::Castle);
            assert_eq!(
                *spawn,
                QPos::from_m([10.0, 0.0, 20.0]),
                "snapped to the floor"
            );
        }
        other => panic!("{other:?}"),
    }
    // On the gallery: level 1.
    let b = join(&mut s, "Bea", House::Skytalon, [10.0, 6.0, 10.0]);
    assert_eq!(s.ents.level[index(&s, &b)], 1);
    // Off the map, or never saved: the layout spawn.
    let c = join(&mut s, "Cy", House::Skytalon, [900.0, 0.0, 900.0]);
    assert_eq!(s.position(c.id), Some([100.0, 0.0, 100.0]));
    // The next player at the same spawn takes the next slot, beside it.
    let d = join_as(&mut s, character("Di", House::Skytalon, None), 8);
    let pd = s.position(d.id).unwrap();
    let off = ((pd[0] - 100.0).powi(2) + (pd[2] - 100.0).powi(2)).sqrt();
    assert!((off - SPAWN_SPACING).abs() < 1e-3, "{pd:?}");
    assert_eq!(pd[1], 0.0);
}

#[test]
fn players_at_one_spawn_spread_out_on_reachable_ground() {
    // In the open: every slot is a character's width or more from the others.
    let mut s = sim();
    let ids: Vec<_> = (0..SPAWN_SLOTS)
        .map(|i| {
            join_as(
                &mut s,
                character(&format!("Open{i}"), House::Burrowsby, None),
                8,
            )
            .id
        })
        .collect();
    let pos: Vec<_> = ids.iter().map(|&id| s.position(id).unwrap()).collect();
    let r = s.params.radius;
    for (i, a) in pos.iter().enumerate() {
        assert!(
            dist2(*a, [100.0, 0.0, 100.0]).sqrt()
                < SPAWN_SPACING * (SPAWN_SLOTS as f32).sqrt() + 0.01
        );
        for b in &pos[i + 1..] {
            assert!(dist2(*a, *b).sqrt() > 2.0 * r, "{a:?} and {b:?} overlap");
        }
    }
    // The pattern repeats after a full spiral.
    let again = join_as(&mut s, character("Again", House::Burrowsby, None), 8);
    assert_eq!(s.position(again.id), Some([100.0, 0.0, 100.0]));

    // In a corner: walls pull slots in, but each still fits on the floor.
    let world = sim_core::World::from_json(
        r#"{ "version": 1,
             "surfaces": [{ "id": "box", "kind": "room", "name": "Box", "level": 0, "cell": "c",
                            "min": [0, 0], "max": [4, 4], "y": 0, "ceiling": 3 }],
             "spawns": [{ "id": "s", "pos": [1, 0, 1], "yaw": 0 }] }"#,
    )
    .unwrap();
    let params = MoveParams::default();
    for slot in 0..SPAWN_SLOTS {
        let p = spawn_slot(&world, &params, [1.0, 0.0, 1.0], slot);
        assert!(
            sim_core::footprint_ground(&world, &params, p[0], p[2], p[1]).is_some(),
            "slot {slot} at {p:?} doesn't fit"
        );
    }
}

#[test]
fn valid_moves_are_accepted_and_teleports_corrected() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, [100.0, 0.0, 100.0]);
    a.drain();
    let t0 = Instant::now();
    let ms = |n| t0 + Duration::from_millis(n);
    mv(&mut s, &a, ms(100), 1, [100.6, 0.0, 100.0]);
    assert_eq!(s.position(a.id), Some([100.6, 0.0, 100.0]));
    assert!(a.drain().is_empty());

    // 50 m in 100 ms.
    mv(&mut s, &a, ms(200), 2, [150.0, 0.0, 100.0]);
    assert_eq!(s.position(a.id), Some([100.6, 0.0, 100.0]));
    assert_eq!(
        a.drain(),
        vec![ServerMsg::Correction {
            seq: 2,
            pos: QPos::from_m([100.6, 0.0, 100.0])
        }]
    );
    // Moves already in flight are dropped quietly for a moment...
    mv(&mut s, &a, ms(250), 3, [151.0, 0.0, 100.0]);
    assert!(a.drain().is_empty());
    // ...and old or repeated sequence numbers are ignored.
    mv(&mut s, &a, ms(600), 3, [100.9, 0.0, 100.0]);
    assert_eq!(s.position(a.id), Some([100.6, 0.0, 100.0]));
    // Climbing to the gallery without stairs is blocked.
    let mut b = join(&mut s, "Bea", House::Skytalon, [10.0, 0.0, 10.0]);
    b.drain();
    mv(&mut s, &b, ms(300), 1, [10.2, 6.0, 10.0]);
    assert!(matches!(
        b.drain()[..],
        [ServerMsg::Correction { seq: 1, .. }]
    ));
    assert_eq!(
        s.metrics.corrections[correction_index(sim_core::RejectReason::TooFast)].get(),
        2
    );
}

#[test]
fn dev_teleports_land_on_ground_the_character_may_walk() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, [100.0, 0.0, 100.0]);
    a.drain();
    let t0 = Instant::now();
    mv(&mut s, &a, t0, 1, [100.5, 0.0, 100.0]);
    // Up on the gallery: the client follows the correction, and its next
    // moves carry on from there.
    assert!(s.dev_teleport(a.cid, [10.0, 6.0, 10.0], t0));
    assert_eq!(s.position(a.id), Some([10.0, 6.0, 10.0]));
    assert_eq!(
        a.drain(),
        vec![ServerMsg::Correction {
            seq: 1,
            pos: QPos::from_m([10.0, 6.0, 10.0])
        }]
    );
    mv(
        &mut s,
        &a,
        t0 + Duration::from_millis(100),
        2,
        [10.4, 6.0, 10.0],
    );
    assert_eq!(s.position(a.id), Some([10.4, 6.0, 10.0]));
    assert!(a.drain().is_empty());
    // Not into another house's common room, off the map or for nobody.
    assert!(!s.dev_teleport(a.cid, [210.0, 0.0, 100.0], t0));
    assert!(!s.dev_teleport(a.cid, [-50.0, 0.0, 10.0], t0));
    assert!(!s.dev_teleport(a.cid, [f32::NAN, 0.0, 10.0], t0));
    assert!(!s.dev_teleport(-1, [10.0, 0.0, 10.0], t0));
    assert_eq!(s.position(a.id), Some([10.4, 6.0, 10.0]));
}

/// Review fix: a move sent from the old spot before the client heard of a
/// dev teleport is dropped, not corrected (nor counted).
#[test]
fn a_move_in_flight_during_a_dev_teleport_is_not_a_correction() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, [100.0, 0.0, 100.0]);
    a.drain();
    let t0 = Instant::now();
    mv(&mut s, &a, t0, 1, [100.5, 0.0, 100.0]);
    assert!(s.dev_teleport(a.cid, [10.0, 6.0, 10.0], t0));
    a.drain();
    let count = |s: &WorldSim| -> u64 { s.metrics.corrections.iter().map(|c| c.get()).sum() };
    let before = count(&s);
    mv(
        &mut s,
        &a,
        t0 + Duration::from_millis(50),
        2,
        [101.0, 0.0, 100.0],
    );
    assert!(a.drain().is_empty());
    assert_eq!(s.position(a.id), Some([10.0, 6.0, 10.0]));
    assert_eq!(count(&s), before);
}

#[test]
fn bunched_moves_use_banked_credit_but_not_more() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, [100.0, 0.0, 100.0]);
    a.drain();
    let t0 = Instant::now() + Duration::from_secs(2); // Credit is full (1 s).
    // Two 0.65 m moves arrive together after a jitter spike: both fine.
    let mut x = 100.0;
    for seq in 1..=2 {
        x += 0.65;
        mv(&mut s, &a, t0, seq, [x, 0.0, 100.0]);
    }
    assert!(a.drain().is_empty());
    // A speed hack sending 0.65 m every 20 ms runs out of credit quickly.
    let mut corrected = false;
    for seq in 3..40u32 {
        x += 0.65;
        let at = t0 + Duration::from_millis(20 * u64::from(seq));
        mv(&mut s, &a, at, seq, [x, 0.0, 100.0]);
        corrected |= !a.drain().is_empty();
    }
    assert!(corrected);
    let travelled = s.position(a.id).unwrap()[0] - 100.0;
    // ~0.8 s at 8.1 m/s plus the banked second.
    assert!(travelled < 17.0, "travelled {travelled}");
}

/// Rival common rooms are walls: a Serpentyne walking into the Roarwing
/// common room is corrected, a Roarwing is not, and nobody is ever
/// spawned on another house's ground.
#[test]
fn house_gates_are_enforced_by_the_server() {
    let mut s = sim();
    let t0 = Instant::now();
    let walk_in = |s: &mut WorldSim, c: &mut TestClient| {
        c.drain();
        let mut x = 199.0;
        for seq in 1..=4 {
            x += 0.4;
            let at = t0 + Duration::from_millis(100 * u64::from(seq));
            mv(s, c, at, seq, [x, 0.0, 100.0]);
        }
        c.drain()
            .iter()
            .any(|m| matches!(m, ServerMsg::Correction { .. }))
    };
    let mut lion = join(&mut s, "Lion", House::Roarwing, [199.0, 0.0, 100.0]);
    assert!(!walk_in(&mut s, &mut lion));
    assert!(s.position(lion.id).unwrap()[0] > 200.0, "inside");
    let mut snake = join(&mut s, "Snake", House::Serpentyne, [199.0, 0.0, 100.0]);
    assert!(walk_in(&mut s, &mut snake));
    assert!(s.position(snake.id).unwrap()[0] <= 200.0, "kept out");
    assert!(
        s.metrics.corrections[correction_index(sim_core::RejectReason::NotWalkable)].get()
            + s.metrics.corrections[correction_index(sim_core::RejectReason::BlockedPath)].get()
            >= 1
    );
    // A saved position inside a rival common room resumes at a spawn; one
    // inside your own is kept.
    let rival = join(&mut s, "Viper", House::Serpentyne, [210.0, 0.0, 100.0]);
    assert!(s.position(rival.id).unwrap()[0] < 200.0);
    let home = join(&mut s, "Cub", House::Roarwing, [210.0, 0.0, 100.0]);
    assert_eq!(s.position(home.id), Some([210.0, 0.0, 100.0]));
}

// ------------------------------------------------------------ chat

#[test]
fn say_reaches_only_those_within_its_radius() {
    let r = chat::SAY_RADIUS_M;
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Roarwing, [100.0, 0.0, 100.0]);
    // 0.9 of the radius away, then a quarter beyond it.
    let mut near = join(
        &mut s,
        "Bea",
        House::Serpentyne,
        [100.0 + 0.75 * r, 0.0, 100.0 + 0.5 * r],
    );
    let mut far = join(
        &mut s,
        "Cy",
        House::Roarwing,
        [100.0 + 1.25 * r, 0.0, 100.0],
    );
    say(&mut s, &a, ChatChannel::Say, "  hello there ");
    s.tick(Instant::now());
    let heard = vec![(a.id, ChatChannel::Say, "hello there".to_owned())];
    assert_eq!(a.chats(), heard, "the speaker sees their own line");
    assert_eq!(near.chats(), heard);
    assert!(far.chats().is_empty());
}

#[test]
fn house_chat_reaches_the_house_anywhere() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Burrowsby, [10.0, 0.0, 10.0]);
    let mut mate = join(&mut s, "Bea", House::Burrowsby, [190.0, 0.0, 190.0]);
    let mut other = join(&mut s, "Cy", House::Skytalon, [10.0, 0.0, 10.0]);
    // In a dorm too.
    let mut home = join_as(&mut s, in_dorm("Dot", House::Burrowsby), 64);
    say(&mut s, &a, ChatChannel::House, "meet at the greenhouses");
    s.tick(Instant::now());
    assert_eq!(a.chats().len(), 1);
    assert_eq!(mate.chats()[0].1, ChatChannel::House);
    assert_eq!(home.chats().len(), 1);
    assert!(other.chats().is_empty());
}

fn rejected(c: &mut TestClient) -> Vec<(u32, ChatRejectReason)> {
    c.drain()
        .into_iter()
        .filter_map(|m| match m {
            ServerMsg::ChatRejected { client_seq, reason } => Some((client_seq, reason)),
            _ => None,
        })
        .collect()
}

#[test]
fn whispers_reach_one_player_and_make_them_priority() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Burrowsby, [10.0, 0.0, 10.0]);
    let mut b = join(&mut s, "Mary-Kate", House::Serpentyne, [190.0, 0.0, 190.0]);
    let mut c = join(&mut s, "Cy", House::Burrowsby, [10.5, 0.0, 10.0]);
    say(
        &mut s,
        &a,
        ChatChannel::Whisper {
            to: "mary-KATE".into(),
        },
        "psst",
    );
    s.tick(Instant::now());
    let want = vec![(
        a.id,
        ChatChannel::Whisper {
            to: "Mary-Kate".into(),
        },
        "psst".to_owned(),
    )];
    assert_eq!(b.chats(), want);
    assert_eq!(a.chats(), want, "the sender gets a copy");
    assert!(c.chats().is_empty());

    // With room for one, Ada now sees her far-away partner over Cy next to her.
    s.input(a.id, Instant::now(), Input::ViewLimit(1));
    ticks(&mut s, 2);
    let ai = index(&s, &a);
    assert_eq!(s.ents.client[ai].visible, vec![b.id]);

    // Whispering someone offline is refused, naming the line.
    a.drain();
    s.input(
        a.id,
        Instant::now(),
        Input::Chat {
            channel: ChatChannel::Whisper {
                to: "Nobody".into(),
            },
            text: "hello?".into(),
            client_seq: 41,
        },
    );
    s.tick(Instant::now());
    assert_eq!(
        rejected(&mut a),
        vec![(41, ChatRejectReason::UnknownTarget)]
    );
}

#[test]
fn refused_lines_are_answered_and_delivered_to_no_one() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Burrowsby, [10.0, 0.0, 10.0]);
    let mut b = join(&mut s, "Bea", House::Burrowsby, [11.0, 0.0, 10.0]);
    a.drain();
    let t0 = Instant::now();
    let line = |s: &mut WorldSim, seq: u32, text: String, at: Instant| {
        s.input(
            a.id,
            at,
            Input::Chat {
                channel: ChatChannel::Say,
                text,
                client_seq: seq,
            },
        )
    };
    // The filter refuses (protocol v2 never masks).
    line(&mut s, 1, "what a Badword".into(), t0);
    // Empty and over-long lines are invalid.
    line(&mut s, 2, "   ".into(), t0);
    line(&mut s, 3, "x".repeat(protocol::CHAT_MAX_LEN + 1), t0);
    // Burst of 5 (one already used by the filtered line), then rate limited.
    for n in 4..10 {
        line(&mut s, n, format!("hello {n}"), t0);
    }
    s.tick(t0);
    let got = rejected(&mut a);
    assert_eq!(
        got,
        vec![
            (1, ChatRejectReason::Filtered),
            (2, ChatRejectReason::Invalid),
            (3, ChatRejectReason::Invalid),
            (8, ChatRejectReason::RateLimited),
            (9, ChatRejectReason::RateLimited),
        ]
    );
    let heard: Vec<String> = b.chats().into_iter().map(|c| c.2).collect();
    assert_eq!(heard, vec!["hello 4", "hello 5", "hello 6", "hello 7"]);
    assert_eq!(s.metrics.chat_rejected.get(), 5);
}

#[test]
fn blocked_players_are_not_heard_and_blockers_look_offline() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Burrowsby, [10.0, 0.0, 10.0]);
    let mut b = join(&mut s, "Bea", House::Burrowsby, [11.0, 0.0, 10.0]);
    // Bea blocked Ada.
    let bi = index(&s, &b);
    s.apply_social(
        bi,
        Social {
            blocked: vec![a.cid],
            ..Social::default()
        },
        Instant::now(),
    );
    let ai = index(&s, &a);
    s.apply_social(
        ai,
        Social {
            blocked_by: vec![b.cid],
            ..Social::default()
        },
        Instant::now(),
    );
    a.drain();
    b.drain();
    say(&mut s, &a, ChatChannel::Say, "hello");
    say(&mut s, &a, ChatChannel::House, "hello house");
    s.tick(Instant::now());
    assert!(b.chats().is_empty());
    assert_eq!(a.chats().len(), 2, "the sender still sees their lines");
    // Whispering the blocker is refused as if they were offline.
    say(
        &mut s,
        &a,
        ChatChannel::Whisper { to: "Bea".into() },
        "psst",
    );
    s.tick(Instant::now());
    assert_eq!(rejected(&mut a), vec![(0, ChatRejectReason::UnknownTarget)]);
    assert!(b.chats().is_empty());
    // Bea can still speak to Ada.
    say(&mut s, &b, ChatChannel::Say, "go away");
    s.tick(Instant::now());
    assert_eq!(a.chats().len(), 1);
}

/// A crowd chatting at once must not disconnect its listeners: lines that
/// would leave no room in a listener's queue are dropped instead.
#[test]
fn a_burst_of_chat_drops_lines_rather_than_listeners() {
    let mut s = sim();
    let queue = super::super::gateway::OUTBOUND_QUEUE;
    let mut listener = join_as(
        &mut s,
        character("Ada", House::Burrowsby, Some([100.0, 0.0, 100.0])),
        queue,
    );
    let speakers: Vec<TestClient> = (0..14)
        .map(|n| {
            let x = 100.0 + (n % 7) as f32;
            let z = 101.0 + (n / 7) as f32;
            join_as(
                &mut s,
                character(&format!("Speaker{n}"), House::Burrowsby, Some([x, 0.0, z])),
                queue,
            )
        })
        .collect();
    listener.drain();
    // 14 × 5 = 70 lines in one tick, more than the 64-frame queue.
    let t0 = Instant::now();
    for sp in &speakers {
        for n in 0..chat::CHAT_BURST as u32 {
            s.input(
                sp.id,
                t0,
                Input::Chat {
                    channel: ChatChannel::Say,
                    text: format!("hello {n}"),
                    client_seq: n,
                },
            );
        }
    }
    s.tick(t0);
    s.tick(t0);
    assert_eq!(listener.control.reason(), None, "the listener stays");
    assert_eq!(s.len(), 15);
    let got = listener.drain();
    let chats = got
        .iter()
        .filter(|m| matches!(m, ServerMsg::Chat { .. }))
        .count();
    assert!((40..70).contains(&chats), "{chats} lines delivered");
    // The snapshot still got through.
    assert!(got.iter().any(|m| matches!(m, ServerMsg::Snapshot { .. })));
    assert!(s.metrics.chat_dropped.get() > 0);
}

// ------------------------------------------------------------ snapshots

#[test]
fn players_see_each_other_and_their_moves() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
    let mut b = join(&mut s, "Bea", House::Serpentyne, [102.0, 0.0, 100.0]);
    ticks(&mut s, 2);
    let entered: Vec<EntityId> = b
        .drain()
        .into_iter()
        .flat_map(|m| match m {
            ServerMsg::Snapshot { entered, .. } => entered.iter().map(EntityEntry::id).collect(),
            _ => vec![],
        })
        .collect();
    assert_eq!(entered, vec![a.id]);
    a.drain();

    mv(
        &mut s,
        &a,
        Instant::now() + Duration::from_millis(100),
        1,
        [100.5, 0.0, 100.0],
    );
    ticks(&mut s, 2);
    let states = b.states_of(a.id);
    assert_eq!(states.len(), 1);
    assert_eq!(states[0].pos, QPos::from_m([100.5, 0.0, 100.0]));
    assert_eq!(states[0].anim, Anim::Walk);
    // B didn't move, so A gets no state for B.
    assert!(a.states_of(b.id).is_empty());
    // That move went out as a delta.
    assert!(s.metrics.snapshot_deltas.get() >= 1);
}

/// Every viewer gets every state change, whichever half of the ticks it
/// snapshots on and whenever between ticks the move arrives.
#[test]
fn moves_reach_viewers_of_both_snapshot_phases() {
    // Arrive after an even tick, then after an odd one.
    for offset in 0..2 {
        let mut s = sim();
        let mover = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
        let mut even = join(&mut s, "Bea", House::Serpentyne, [102.0, 0.0, 100.0]);
        let mut odd = join(&mut s, "Cy", House::Serpentyne, [101.0, 0.0, 102.0]);
        let phase = |s: &WorldSim, id| s.ents.client[s.ents.index_of(id).unwrap()].snapshot_phase;
        assert_ne!(phase(&s, even.id), phase(&s, odd.id), "one viewer per half");
        ticks(&mut s, 4 + offset);
        even.drain();
        odd.drain();

        mv(
            &mut s,
            &mover,
            Instant::now() + Duration::from_millis(100),
            1,
            [100.5, 0.0, 100.0],
        );
        ticks(&mut s, TICKS_PER_SNAPSHOT);
        for viewer in [&mut even, &mut odd] {
            let got = viewer.states_of(mover.id);
            assert_eq!(got.len(), 1, "offset {offset}: {got:?}");
            assert_eq!(got[0].pos, QPos::from_m([100.5, 0.0, 100.0]));
        }
    }
}

#[test]
fn the_same_character_joining_again_replaces_the_old_connection() {
    let mut s = sim();
    let ch = character("Ada", House::Burrowsby, Some([30.0, 0.0, 30.0]));
    let old = join_as(&mut s, ch.clone(), 8);
    mv(
        &mut s,
        &old,
        Instant::now() + Duration::from_millis(100),
        1,
        [30.5, 0.0, 30.0],
    );
    // The new session's saved position is stale; it takes over the live one.
    let new = join_as(&mut s, ch, 8);
    assert_eq!(s.len(), 1);
    assert_eq!(s.position(new.id), Some([30.5, 0.0, 30.0]));
    assert_eq!(old.control.reason(), Some(KickCode::Replaced));
    assert!(new.control.reason().is_none());
    // The old connection's leave is stale and ignored.
    assert!(!s.remove_player(old.id));
    assert_eq!(s.len(), 1);
}

#[test]
fn a_client_that_cannot_keep_up_is_disconnected() {
    let mut s = sim();
    // Room for Welcome only.
    let slow = join_as(&mut s, character("Ada", House::Burrowsby, None), 1);
    let _other = join(&mut s, "Bea", House::Burrowsby, [100.0, 0.0, 101.0]);
    ticks(&mut s, 4);
    assert_eq!(s.len(), 1);
    assert_eq!(slow.control.reason(), Some(KickCode::TooSlow));
    assert_eq!(s.metrics.kicks_slow.get(), 1);
}

#[test]
fn a_burst_of_new_looks_never_costs_a_viewer_the_connection() {
    let mut s = sim();
    let queue = crate::realtime::gateway::OUTBOUND_QUEUE;
    let mut viewer = join_as(
        &mut s,
        character("Vee", House::Roarwing, Some([100.0, 0.0, 100.0])),
        queue,
    );
    let dressers: Vec<TestClient> = (0..4)
        .map(|n| {
            join(
                &mut s,
                &format!("Dresser{n}"),
                House::Skytalon,
                [101.0, 0.0, 100.0],
            )
        })
        .collect();
    ticks(&mut s, 2);
    viewer.drain();
    // Each tries on 30 looks at once (the wardrobe's burst), all within one
    // tick, while the viewer's connection has not drained anything yet.
    for d in &dressers {
        for v in 1..=30 {
            s.handle(WorldCmd::AppearanceChanged {
                character_id: d.cid,
                appearance: Appearance {
                    hair_style: v as u16,
                    ..Appearance::default()
                },
                version: v,
            });
        }
    }
    ticks(&mut s, 1);
    assert_eq!(viewer.control.reason(), None, "not disconnected");
    let looks: Vec<(EntityId, u32)> = viewer
        .drain()
        .into_iter()
        .filter_map(|m| match m {
            ServerMsg::AppearanceChanged {
                id,
                appearance_version,
                ..
            } => Some((id, appearance_version)),
            _ => None,
        })
        .collect();
    // One per character, the newest.
    let mut want: Vec<(EntityId, u32)> = dressers.iter().map(|d| (d.id, 30)).collect();
    want.sort_unstable();
    let mut got = looks.clone();
    got.sort_unstable();
    assert_eq!(got, want, "{looks:?}");
}

#[test]
fn a_flood_of_friend_requests_is_delivered_in_order_without_a_kick() {
    let mut s = sim();
    let queue = crate::realtime::gateway::OUTBOUND_QUEUE;
    let mut target = join_as(
        &mut s,
        character("Vee", House::Roarwing, Some([100.0, 0.0, 100.0])),
        queue,
    );
    ticks(&mut s, 2);
    target.drain();
    // Five accounts, each spending its whole friend-request burst (20) on
    // the same player within one tick.
    let n = 5 * 20;
    for request_id in 0..n {
        s.handle(WorldCmd::Notify {
            character_id: target.cid,
            frame: friend_frame(FriendEvent::RequestClosed { request_id }),
        });
    }
    let mut got = Vec::new();
    for _ in 0..10 {
        ticks(&mut s, 1);
        assert_eq!(target.control.reason(), None, "not disconnected");
        got.extend(target.drain().into_iter().filter_map(|m| match m {
            ServerMsg::Friend {
                event: FriendEvent::RequestClosed { request_id },
            } => Some(request_id),
            _ => None,
        }));
    }
    assert_eq!(got, (0..n).collect::<Vec<_>>(), "all of them, in order");
    assert_eq!(s.metrics.notices_dropped.get(), 0);
}

#[test]
fn rosters_count_online_and_nearby_once_a_second() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
    let _b = join(&mut s, "Bea", House::Burrowsby, [110.0, 0.0, 100.0]);
    let _c = join(&mut s, "Cy", House::Burrowsby, [190.0, 0.0, 100.0]);
    ticks(&mut s, WORLD_TICK_HZ * 2);
    let rosters: Vec<_> = a
        .drain()
        .into_iter()
        .filter_map(|m| match m {
            ServerMsg::Roster { online, nearby } => Some((online, nearby)),
            _ => None,
        })
        .collect();
    assert_eq!(rosters, vec![(3, 1), (3, 1)]);
}

#[test]
fn positions_are_saved_on_leave_periodically_and_at_shutdown() {
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut s = sim();
    s.saver = Some(tx);
    let a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
    let b = join(&mut s, "Bea", House::Burrowsby, [50.0, 0.0, 50.0]);
    let castle = |cid, pos| SavedPosition {
        character_id: cid,
        zone: SavedZone::Castle,
        pos,
    };
    mv(
        &mut s,
        &a,
        Instant::now() + Duration::from_millis(100),
        1,
        [100.5, 0.0, 100.0],
    );
    for _ in 0..SAVE_INTERVAL_TICKS {
        s.tick(Instant::now());
    }
    assert_eq!(
        rx.try_recv().unwrap().positions,
        vec![castle(a.cid, [100.5, 0.0, 100.0])],
        "only movers"
    );
    for _ in 0..SAVE_INTERVAL_TICKS {
        s.tick(Instant::now());
    }
    assert!(rx.try_recv().is_err(), "nobody moved since");
    assert!(s.remove_player(b.id));
    assert_eq!(rx.try_recv().unwrap().positions.len(), 1);
    s.shutdown();
    assert_eq!(
        rx.try_recv().unwrap().positions,
        vec![castle(a.cid, [100.5, 0.0, 100.0])]
    );
    assert_eq!(a.control.reason(), Some(KickCode::ServerRestart));
    assert!(rx.try_recv().is_err(), "the saver channel is closed");
}

// ------------------------------------------------------------ appearance and emotes

/// The seams the HTTP side calls: appearance changes reach the character
/// and its viewers, notifications reach one character, and online status
/// names the room each character stands in.
#[test]
fn a_look_changed_just_before_joining_is_not_lost() {
    let mut s = sim();
    let ch = character("Ada", House::Burrowsby, Some([100.0, 0.0, 100.0]));
    let newer = Appearance {
        hair_style: 5,
        ..Appearance::default()
    };
    // The gateway read the character (version 0), then the wardrobe
    // committed version 1 and told the world before the join landed.
    s.handle(WorldCmd::AppearanceChanged {
        character_id: ch.id,
        appearance: newer,
        version: 1,
    });
    let mut a = join_as(&mut s, ch.clone(), 64);
    let welcome = a.drain().into_iter().next().unwrap();
    assert!(
        matches!(
            welcome,
            ServerMsg::Welcome { appearance_version: 1, appearance, .. } if appearance == newer
        ),
        "{welcome:?}"
    );
    assert_eq!(s.ents.info[index(&s, &a)].appearance_version, 1);

    // An older note never overrides what the join brings.
    assert!(s.remove_player(a.id));
    s.handle(WorldCmd::AppearanceChanged {
        character_id: ch.id,
        appearance: Appearance::default(),
        version: 1,
    });
    let current = Character {
        appearance: newer,
        appearance_version: 2,
        ..ch
    };
    let b = join_as(&mut s, current.clone(), 64);
    assert_eq!(s.ents.info[index(&s, &b)].appearance_version, 2);
    assert!(s.pending_looks.is_empty(), "used or dropped at join");

    // A second login read the character before a change the first one got:
    // the new connection keeps the newer look.
    s.handle(WorldCmd::AppearanceChanged {
        character_id: current.id,
        appearance: Appearance::default(),
        version: 3,
    });
    let c = join_as(&mut s, current, 64);
    assert_eq!(b.control.reason(), Some(KickCode::Replaced));
    assert_eq!(s.ents.info[index(&s, &c)].appearance_version, 3);
}

#[test]
fn http_seams_reach_the_right_players() {
    let mut s = sim();
    let a_char = character("Ada", House::Burrowsby, Some([100.0, 0.0, 100.0]));
    let a_cid = a_char.id;
    let mut a = join_as(&mut s, a_char, 64);
    let mut b = join(&mut s, "Bea", House::Serpentyne, [101.0, 0.0, 100.0]);
    // With room for one each, Bea sees Ada (1 m) and Cy sees Bea (2 m), not Ada.
    let mut far = join(&mut s, "Cy", House::Serpentyne, [103.0, 0.0, 100.0]);
    s.input(far.id, Instant::now(), Input::ViewLimit(1));
    s.input(b.id, Instant::now(), Input::ViewLimit(1));
    ticks(&mut s, 4);
    for c in [&mut a, &mut b, &mut far] {
        c.drain();
    }

    let look = Appearance {
        hair_style: 3,
        ..Appearance::default()
    };
    s.handle(WorldCmd::AppearanceChanged {
        character_id: a_cid,
        appearance: look,
        version: 1,
    });
    // Delivered with the next tick.
    ticks(&mut s, 1);
    let changed = |c: &mut TestClient| {
        c.drain()
            .into_iter()
            .filter(|m| matches!(m, ServerMsg::AppearanceChanged { .. }))
            .count()
    };
    assert_eq!(changed(&mut a), 1, "the character itself");
    assert_eq!(changed(&mut b), 1, "a viewer");
    assert_eq!(changed(&mut far), 0, "someone who does not see Ada");
    let ai = index(&s, &a);
    assert_eq!(s.ents.info[ai].appearance.hair_style, 3);
    // A stale version is ignored.
    s.handle(WorldCmd::AppearanceChanged {
        character_id: a_cid,
        appearance: Appearance::default(),
        version: 1,
    });
    assert_eq!(s.ents.info[ai].appearance.hair_style, 3);
    // A later viewer gets the new look when Ada enters its view.
    let mut late = join(&mut s, "Dee", House::Serpentyne, [100.0, 0.0, 101.0]);
    ticks(&mut s, 2);
    let seen: Vec<(u32, u16)> = late
        .drain()
        .into_iter()
        .flat_map(|m| match m {
            ServerMsg::Snapshot { entered, .. } => entered
                .into_iter()
                .filter_map(full_entry)
                .filter(|e| e.id == a.id)
                .map(|e| (e.appearance_version, e.appearance.hair_style))
                .collect(),
            _ => vec![],
        })
        .collect();
    assert_eq!(seen, vec![(1, 3)]);

    let frame = encode_server(&ServerMsg::Friend {
        event: FriendEvent::Offline { character_id: 9 },
    });
    a.drain();
    s.handle(WorldCmd::Notify {
        character_id: a_cid,
        frame: frame.into(),
    });
    assert!(matches!(a.drain()[..], [ServerMsg::Friend { .. }]));

    let status = s.online_status(&[a_cid, 123_456]);
    assert_eq!(
        status.get(&a_cid),
        Some(&Location::Castle {
            place: "Hall".into()
        })
    );
    assert_eq!(status.len(), 1, "offline characters are left out");
}

/// Every look change `c` was sent, oldest first.
fn look_changes(c: &mut TestClient) -> Vec<protocol::LookChange> {
    c.drain()
        .into_iter()
        .flat_map(|m| match m {
            ServerMsg::Snapshot { looks, .. } => looks,
            _ => vec![],
        })
        .collect()
}

/// Protocol v10: `LookAt` is kept only for a live character in the same
/// zone within range, reaches viewers once per change (the target itself
/// included), is cleared by a zone change and rate limited.
#[test]
fn look_targets_are_checked_and_reach_viewers() {
    use protocol::LookChange;
    let mut s = sim();
    let a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
    let mut b = join(&mut s, "Bea", House::Burrowsby, [102.0, 0.0, 100.0]);
    let mut c = join(&mut s, "Cy", House::Burrowsby, [100.0, 0.0, 103.0]);
    let far = join(&mut s, "Fay", House::Burrowsby, [100.0, 0.0, 140.0]);
    let mut home = join_as(&mut s, in_dorm("Dot", House::Burrowsby), 256);
    FakeDb::default().run(&mut s);
    ticks(&mut s, 2);
    for x in [&mut b, &mut c, &mut home] {
        x.drain();
    }
    let t0 = Instant::now();
    let look = |s: &mut WorldSim, target: Option<EntityId>, at: Instant| {
        s.input(a.id, at, Input::LookAt(target));
        s.tick(at);
        s.tick(at);
    };

    // Ada looks at Bea: Bea and Cy both see it, once.
    look(&mut s, Some(b.id), t0);
    let want = vec![LookChange {
        id: a.id,
        target: Some(b.id),
    }];
    assert_eq!(look_changes(&mut b), want);
    assert_eq!(look_changes(&mut c), want);
    ticks(&mut s, 4);
    assert!(look_changes(&mut b).is_empty(), "sent once per change");

    // Out of range (40 m), in another zone, unknown, herself: nobody.
    for (k, target) in [far.id, home.id, 0xDEAD_BEEF, a.id].into_iter().enumerate() {
        let at = t0 + Duration::from_secs(1 + k as u64);
        look(&mut s, Some(b.id), at);
        look(&mut s, Some(target), at);
        assert_eq!(s.ents.look[index(&s, &a)], None, "{target:#x}");
    }
    let seen = look_changes(&mut b);
    assert_eq!(seen.last().map(|l| l.target), Some(None));
    assert!(
        look_changes(&mut home).is_empty(),
        "other zones hear nothing"
    );

    // Cleared: viewers hear `None`.
    let t0 = t0 + Duration::from_secs(6);
    look(&mut s, Some(c.id), t0);
    assert_eq!(
        look_changes(&mut b).last(),
        Some(&LookChange {
            id: a.id,
            target: Some(c.id)
        })
    );
    look(&mut s, None, t0);
    assert_eq!(
        look_changes(&mut b),
        vec![LookChange {
            id: a.id,
            target: None
        }]
    );

    // Over the rate (a burst of 8, then 4 a second) the rest is dropped.
    let t1 = t0 + Duration::from_secs(10);
    for k in 0..20 {
        let target = if k % 2 == 0 { Some(b.id) } else { None };
        s.input(a.id, t1, Input::LookAt(target));
    }
    // The 8th message (a `None`) was the last one taken.
    assert_eq!(s.ents.look[index(&s, &a)], None);
    s.input(a.id, t1, Input::LookAt(Some(c.id)));
    assert_eq!(s.ents.look[index(&s, &a)], None, "dropped");
    s.input(
        a.id,
        t1 + Duration::from_millis(300),
        Input::LookAt(Some(c.id)),
    );
    assert_eq!(s.ents.look[index(&s, &a)], Some(c.id), "the rate refills");

    // A zone change forgets the target (Dot leaves her dorm).
    let h = index(&s, &home);
    s.ents.look[h] = Some(home.id);
    s.leave_dorm(h, t1);
    assert_eq!(zone_of(&s, &home), ZoneIdOf::Castle);
    assert_eq!(s.ents.look[index(&s, &home)], None);
}

/// Turning round on a two-sided bench (client/src/game/bench-flip.ts) is a
/// `Move` that keeps the position and turns the yaw half round: the sit
/// stays, and others see the new yaw (they draw the bench's side nearest it).
#[test]
fn a_sitter_turns_round_with_a_move_in_place() {
    let mut s = sim();
    let a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
    let mut b = join(&mut s, "Bea", House::Burrowsby, [101.0, 0.0, 100.0]);
    ticks(&mut s, 2);
    b.drain();
    let t0 = Instant::now();
    s.input(a.id, t0, Input::Emote(Emote::Sit));
    s.tick(t0);
    s.tick(t0);
    let seen = b.states_of(a.id).last().copied().expect("Bea sees Ada");
    assert_eq!(seen.anim, Anim::Sit);
    assert_eq!(seen.yaw, protocol::yaw_to_u8(0.0));
    b.drain();
    // A tap behind her: the same position, the yaw half round.
    let t1 = t0 + Duration::from_millis(300);
    s.input(
        a.id,
        t1,
        Input::Move {
            seq: 1,
            pos: [100.0, 0.0, 100.0],
            yaw: std::f32::consts::PI,
            anim: Anim::Idle,
        },
    );
    s.tick(t1);
    s.tick(t1);
    let seen = b.states_of(a.id).last().copied().expect("the turn is sent");
    assert_eq!(seen.anim, Anim::Sit, "still sitting");
    assert_eq!(seen.yaw, protocol::yaw_to_u8(std::f32::consts::PI));
    assert_eq!(s.ents.state[index(&s, &a)].anim, Anim::Sit);
}

#[test]
fn emotes_show_to_others_and_end_on_time_or_movement() {
    let mut s = sim();
    let a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
    let mut b = join(&mut s, "Bea", House::Burrowsby, [101.0, 0.0, 100.0]);
    ticks(&mut s, 2);
    b.drain();
    let t0 = Instant::now();
    let anim_seen = |s: &mut WorldSim, b: &mut TestClient, now: Instant| {
        s.tick(now);
        s.tick(now);
        b.states_of(a.id).last().map(|st| st.anim)
    };

    s.input(a.id, t0, Input::Emote(Emote::Wave));
    assert_eq!(anim_seen(&mut s, &mut b, t0), Some(Anim::Wave));
    // A move that does not change the position keeps it...
    mv(
        &mut s,
        &a,
        t0 + Duration::from_millis(100),
        1,
        [100.0, 0.0, 100.0],
    );
    assert_eq!(s.ents.state[index(&s, &a)].anim, Anim::Wave);
    // ...and it ends by itself after 2.5 s.
    let later = t0 + Duration::from_millis(u64::from(protocol::EMOTE_WAVE_MS) + 10);
    assert_eq!(anim_seen(&mut s, &mut b, later), Some(Anim::Idle));

    // Sitting lasts until the character moves.
    s.input(a.id, later, Input::Emote(Emote::Sit));
    let much_later = later + Duration::from_secs(60);
    assert_eq!(anim_seen(&mut s, &mut b, much_later), Some(Anim::Sit));
    mv(&mut s, &a, much_later, 2, [100.4, 0.0, 100.0]);
    assert_eq!(anim_seen(&mut s, &mut b, much_later), Some(Anim::Walk));
    // Emotes while walking are ignored.
    s.input(a.id, much_later, Input::Emote(Emote::Bow));
    assert_eq!(s.ents.state[index(&s, &a)].anim, Anim::Walk);
    // A move claiming an emote anim counts as idle.
    s.input(
        a.id,
        much_later + Duration::from_millis(100),
        Input::Move {
            seq: 3,
            pos: [100.6, 0.0, 100.0],
            yaw: 0.0,
            anim: Anim::Bow,
        },
    );
    assert_eq!(s.ents.state[index(&s, &a)].anim, Anim::Idle);

    // Rate limited: a burst of three, then one a second.
    let t1 = much_later + Duration::from_secs(10);
    let before = s.metrics.emotes.get();
    for _ in 0..6 {
        s.input(a.id, t1, Input::Emote(Emote::Bow));
    }
    assert_eq!(s.metrics.emotes.get() - before, 3);
}

// ------------------------------------------------------------ zones and dorms

/// A new character starts in their own dorm, which loads and sends its
/// state; the door out leads to the castle (to the common room's dorm door
/// when the castle has one), and the door in takes them home again.
#[test]
fn first_login_is_in_your_dorm_and_doors_lead_in_and_out() {
    let mut s = sim();
    let mut db = FakeDb::default();
    let (tx, mut saves) = mpsc::unbounded_channel();
    s.saver = Some(tx);
    let mut a = join_as(&mut s, in_dorm("Ada", House::Roarwing), 64);
    let msgs = a.drain();
    let ServerMsg::Welcome { zone, spawn, .. } = &msgs[0] else {
        panic!("{msgs:?}")
    };
    assert_eq!(
        *zone,
        protocol::Zone::Dorm {
            owner_name: "Ada".into(),
            house: House::Roarwing,
            layout: "dorm_roarwing".into()
        }
    );
    let entry = s.shells.world(House::Roarwing).spawns()[0].pos;
    assert_eq!(*spawn, QPos::from_m(entry));
    // The room loads, then its state follows.
    db.run(&mut s);
    assert!(matches!(
        a.drain()[..],
        [ServerMsg::DormState {
            open_to_friends: false,
            cap: DORM_ITEM_CAP,
            ..
        }]
    ));
    assert_eq!(s.instances.len(), 1);
    // Its resume point is the dorm.
    assert_eq!(s.resume_point(index(&s, &a)).zone, SavedZone::Dorm);

    // The entry is next to the exit; from the far end of the room the exit
    // is out of reach.
    let t0 = Instant::now() + Duration::from_secs(1);
    let mut far_x = entry[0];
    let mut seq = 0;
    for k in 0..8 {
        far_x -= 0.4;
        seq += 1;
        mv(
            &mut s,
            &a,
            t0 + Duration::from_millis(100 * (k + 1)),
            seq,
            [far_x, 0.0, entry[2] - 0.4 * (k + 1) as f32],
        );
    }
    a.drain();
    s.input(
        a.id,
        t0 + Duration::from_secs(2),
        Input::Interact("dorm_exit".into()),
    );
    assert_eq!(a.dorm_errors(), vec![DormErrorCode::TooFar]);
    // An unknown target.
    s.input(
        a.id,
        t0 + Duration::from_secs(2),
        Input::Interact("nope".into()),
    );
    assert_eq!(a.dorm_errors(), vec![DormErrorCode::UnknownTarget]);

    // LeaveInstance from anywhere in the room: to the Roarwing dorm door.
    s.input(a.id, t0 + Duration::from_secs(3), Input::LeaveInstance);
    let msgs = a.drain();
    assert_eq!(
        msgs,
        vec![ServerMsg::ZoneChanged {
            zone: protocol::Zone::Castle,
            spawn: QPos::from_m([215.0, 0.0, 100.0]),
            yaw: yaw_to_u8(std::f32::consts::FRAC_PI_2),
        }]
    );
    assert_eq!(zone_of(&s, &a), ZoneIdOf::Castle);
    assert!(s.instances.is_empty(), "the empty room closed");
    let saved: Vec<SavedPosition> = std::iter::from_fn(|| saves.try_recv().ok())
        .flat_map(|b| b.positions)
        .collect();
    assert_eq!(
        saved.last().map(|p| (p.zone, p.pos)),
        Some((SavedZone::Castle, [215.0, 0.0, 100.0])),
        "saved at once"
    );

    // Moves still in flight from the room are dropped without a correction.
    seq += 1;
    mv(
        &mut s,
        &a,
        t0 + Duration::from_millis(3100),
        seq,
        [far_x, 0.0, 0.0],
    );
    assert!(a.drain().is_empty());

    // Back in through the door, which is in reach.
    s.input(
        a.id,
        t0 + Duration::from_secs(4),
        Input::Interact("dorm_door_r".into()),
    );
    db.run(&mut s);
    let msgs = a.drain();
    assert!(
        matches!(
            msgs[0],
            ServerMsg::ZoneChanged {
                zone: protocol::Zone::Dorm { .. },
                ..
            }
        ),
        "{msgs:?}"
    );
    assert!(matches!(msgs[1], ServerMsg::DormState { .. }));
}

#[test]
fn doors_check_house_and_reach() {
    let mut s = sim();
    let mut lion = join(&mut s, "Lion", House::Roarwing, [205.0, 0.0, 100.0]);
    let mut snake = join(&mut s, "Snake", House::Serpentyne, [199.0, 0.0, 100.0]);
    lion.drain();
    snake.drain();
    let t = Instant::now();
    s.input(snake.id, t, Input::Interact("dorm_door_r".into()));
    assert_eq!(snake.dorm_errors(), vec![DormErrorCode::WrongHouse]);
    s.input(lion.id, t, Input::Interact("dorm_door_r".into()));
    assert_eq!(lion.dorm_errors(), vec![DormErrorCode::TooFar]);
    s.input(lion.id, t, Input::Interact("dorm_exit".into()));
    assert_eq!(lion.dorm_errors(), vec![DormErrorCode::UnknownTarget]);
    // LeaveInstance in the castle is ignored.
    s.input(lion.id, t, Input::LeaveInstance);
    assert!(lion.drain().is_empty());
    // Rate limited after a burst.
    for _ in 0..20 {
        s.input(snake.id, t, Input::Interact("dorm_door_r".into()));
    }
    assert!(snake.dorm_errors().contains(&DormErrorCode::RateLimited));
}

/// Visibility, `Say` and rosters are per zone: someone in a dorm at the same
/// coordinates as a castle player neither sees nor hears them.
#[test]
fn zones_do_not_see_or_hear_each_other() {
    let mut s = sim();
    let mut db = FakeDb::default();
    // The dorm entry is at (0, 0, 3) in room coordinates; Bea stands half a
    // metre from that spot in the castle.
    let mut home = join_as(&mut s, in_dorm("Ada", House::Roarwing), 64);
    db.run(&mut s);
    let mut castle = join(&mut s, "Bea", House::Roarwing, [0.5, 0.0, 3.0]);
    ticks(&mut s, 4);
    let visible = |s: &WorldSim, c: &TestClient| s.ents.client[index(s, c)].visible.clone();
    assert!(visible(&s, &home).is_empty());
    assert!(visible(&s, &castle).is_empty());
    home.drain();
    castle.drain();
    say(&mut s, &home, ChatChannel::Say, "anyone?");
    s.tick(Instant::now());
    assert!(castle.chats().is_empty());
    assert_eq!(home.chats().len(), 1);
    ticks(&mut s, WORLD_TICK_HZ);
    let nearby: Vec<u32> = home
        .drain()
        .into_iter()
        .filter_map(|m| match m {
            ServerMsg::Roster { nearby, .. } => Some(nearby),
            _ => None,
        })
        .collect();
    assert_eq!(nearby, vec![0]);
}

// ------------------------------------------------------------ guests

/// Ada (Roarwing) owns the room; Bea and Cy are her friends, Dee is not.
struct Party {
    s: WorldSim,
    db: FakeDb,
    ada: TestClient,
    bea: TestClient,
    cy: TestClient,
    dee: TestClient,
}

fn party() -> Party {
    let mut s = sim();
    let mut db = FakeDb::default();
    let ada = join_as(&mut s, in_dorm("Ada", House::Roarwing), 256);
    let bea = join(&mut s, "Bea", House::Serpentyne, [50.0, 0.0, 60.0]);
    let cy = join(&mut s, "Cy", House::Skytalon, [150.0, 0.0, 150.0]);
    let dee = join(&mut s, "Dee", House::Burrowsby, [10.0, 0.0, 10.0]);
    befriend(&mut s, &[&ada, &bea, &cy]);
    db.run(&mut s);
    let mut p = Party {
        s,
        db,
        ada,
        bea,
        cy,
        dee,
    };
    for c in [&mut p.ada, &mut p.bea, &mut p.cy, &mut p.dee] {
        c.drain();
    }
    p
}

fn invite_id(c: &mut TestClient) -> u32 {
    c.drain()
        .into_iter()
        .find_map(|m| match m {
            ServerMsg::DormInviteReceived {
                invite_id,
                from_name,
                expires_in_secs,
                wishing_room: None,
            } => {
                assert_eq!(expires_in_secs, protocol::DORM_INVITE_TTL_SECS);
                assert_eq!(from_name, "Ada");
                Some(invite_id)
            }
            _ => None,
        })
        .expect("an invitation")
}

#[test]
fn invited_friends_go_straight_in_and_back_to_where_they_were() {
    let Party {
        mut s,
        mut db,
        mut ada,
        mut bea,
        mut dee,
        ..
    } = party();
    let t = Instant::now();
    // Only online friends can be invited.
    s.input(ada.id, t, Input::DormInvite("Dee".into()));
    assert_eq!(ada.dorm_errors(), vec![DormErrorCode::NotFriend]);
    s.input(ada.id, t, Input::DormInvite("Nobody".into()));
    assert_eq!(ada.dorm_errors(), vec![DormErrorCode::NotOnline]);
    assert!(dee.drain().is_empty());

    s.input(ada.id, t, Input::DormInvite("bea".into()));
    let invite = invite_id(&mut bea);
    // Someone else cannot use it.
    s.input(dee.id, t, Input::DormAcceptInvite(invite));
    assert_eq!(dee.dorm_errors(), vec![DormErrorCode::InviteExpired]);

    let bea_was = s.position(bea.id).unwrap();
    s.input(bea.id, t, Input::DormAcceptInvite(invite));
    db.run(&mut s);
    let msgs = bea.drain();
    assert!(
        matches!(&msgs[0], ServerMsg::ZoneChanged { zone: protocol::Zone::Dorm { owner_name, .. }, .. } if owner_name == "Ada"),
        "{msgs:?}"
    );
    assert!(matches!(msgs[1], ServerMsg::DormState { .. }));
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Dorm(ada.cid));
    // The invitation is used up.
    s.input(bea.id, t, Input::DormAcceptInvite(invite));
    assert_eq!(bea.dorm_errors(), vec![DormErrorCode::InviteExpired]);
    // The guest is saved at their return point, never inside the room.
    let bi = index(&s, &bea);
    assert_eq!(
        s.resume_point(bi),
        SavedPosition {
            character_id: bea.cid,
            zone: SavedZone::Castle,
            pos: bea_was
        }
    );
    // Both see each other in the room; friends hear where Bea went.
    ticks(&mut s, 2);
    assert_eq!(s.ents.client[bi].visible, vec![ada.id]);
    assert!(ada.drain().iter().any(|m| matches!(
        m,
        ServerMsg::Friend {
            event: FriendEvent::Moved {
                location: Location::Dorm { .. },
                ..
            }
        }
    )));
    // Online status says whose room they are in.
    assert_eq!(
        s.online_status(&[bea.cid])[&bea.cid],
        Location::Dorm {
            owner_name: "Ada".into()
        }
    );

    // Out again: back where Bea stood.
    bea.drain();
    s.input(bea.id, t + Duration::from_secs(1), Input::LeaveInstance);
    let msgs = bea.drain();
    assert_eq!(
        msgs,
        vec![ServerMsg::ZoneChanged {
            zone: protocol::Zone::Castle,
            spawn: QPos::from_m(bea_was),
            yaw: 0,
        }]
    );

    // An invitation older than two minutes has expired.
    s.input(
        ada.id,
        t + Duration::from_secs(2),
        Input::DormInvite("Bea".into()),
    );
    let invite = invite_id(&mut bea);
    let late = t + Duration::from_secs(2 + u64::from(protocol::DORM_INVITE_TTL_SECS) + 1);
    s.input(bea.id, late, Input::DormAcceptInvite(invite));
    assert_eq!(bea.dorm_errors(), vec![DormErrorCode::InviteExpired]);
}

#[test]
fn open_rooms_take_visiting_friends_and_owners_can_kick() {
    let Party {
        mut s,
        mut db,
        mut ada,
        mut bea,
        mut cy,
        mut dee,
    } = party();
    let t = Instant::now();
    s.input(bea.id, t, Input::DormVisit("Ada".into()));
    assert_eq!(bea.dorm_errors(), vec![DormErrorCode::Closed]);
    s.input(ada.id, t, Input::DormSetOpen(true));
    db.run(&mut s);
    assert_eq!(db.open.get(&ada.cid), Some(&true), "stored");
    assert!(
        ada.drain()
            .contains(&ServerMsg::DormOpenChanged { open: true })
    );
    // Not for strangers, and not for yourself.
    s.input(dee.id, t, Input::DormVisit("Ada".into()));
    assert_eq!(dee.dorm_errors(), vec![DormErrorCode::NotFriend]);
    s.input(ada.id, t, Input::DormVisit("Ada".into()));
    assert_eq!(ada.dorm_errors(), vec![DormErrorCode::UnknownTarget]);

    s.input(bea.id, t, Input::DormVisit("Ada".into()));
    s.input(cy.id, t, Input::DormVisit("ada".into()));
    db.run(&mut s);
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Dorm(ada.cid));
    assert_eq!(zone_of(&s, &cy), ZoneIdOf::Dorm(ada.cid));
    // Closing the room tells everyone inside; guests stay.
    for c in [&mut ada, &mut bea, &mut cy] {
        c.drain();
    }
    s.input(ada.id, t, Input::DormSetOpen(false));
    for c in [&mut ada, &mut bea, &mut cy] {
        assert!(
            c.drain()
                .contains(&ServerMsg::DormOpenChanged { open: false })
        );
    }
    // Only the owner kicks, and only guests of their room.
    s.input(bea.id, t, Input::DormKick(cy.id));
    assert_eq!(bea.dorm_errors(), vec![DormErrorCode::UnknownTarget]);
    s.input(ada.id, t, Input::DormKick(dee.id));
    assert_eq!(ada.dorm_errors(), vec![DormErrorCode::UnknownTarget]);
    s.input(ada.id, t, Input::DormKick(cy.id));
    assert_eq!(zone_of(&s, &cy), ZoneIdOf::Castle);
    assert_eq!(s.position(cy.id), Some([150.0, 0.0, 150.0]));

    // The owner going offline sends every guest back and closes the room.
    assert!(s.remove_player(ada.id));
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Castle);
    assert!(s.instances.is_empty());
    assert!(bea.drain().iter().any(|m| matches!(
        m,
        ServerMsg::Friend {
            event: FriendEvent::Offline { .. }
        }
    )));
}

#[test]
fn kicked_guests_cannot_walk_back_in_until_invited() {
    let Party {
        mut s,
        mut db,
        mut ada,
        mut bea,
        ..
    } = party();
    let t = Instant::now();
    s.input(ada.id, t, Input::DormSetOpen(true));
    s.input(bea.id, t, Input::DormVisit("Ada".into()));
    db.run(&mut s);
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Dorm(ada.cid));
    s.input(ada.id, t, Input::DormKick(bea.id));
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Castle);
    bea.drain();

    // The room is still open to friends, but not to the guest just sent out.
    let later = t + Duration::from_secs(5);
    s.input(bea.id, later, Input::DormVisit("Ada".into()));
    db.run(&mut s);
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Castle);
    assert_eq!(bea.dorm_errors(), vec![DormErrorCode::Closed]);

    // An invitation from the owner lifts the bar...
    ada.drain();
    s.input(ada.id, later, Input::DormInvite("Bea".into()));
    let invite = invite_id(&mut bea);
    s.input(bea.id, later, Input::DormAcceptInvite(invite));
    db.run(&mut s);
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Dorm(ada.cid));
    s.input(bea.id, later, Input::LeaveInstance);
    s.input(bea.id, later, Input::DormVisit("Ada".into()));
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Dorm(ada.cid));

    // ...and so does time.
    s.input(ada.id, later, Input::DormKick(bea.id));
    let much_later = later + Duration::from_secs(u64::from(protocol::DORM_KICK_BAR_SECS) + 1);
    s.tick(much_later);
    s.input(bea.id, much_later, Input::DormVisit("Ada".into()));
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Dorm(ada.cid));
}

#[test]
fn toggling_the_open_flag_is_rate_limited_and_coalesced() {
    let Party {
        mut s,
        mut ada,
        mut bea,
        ..
    } = party();
    let t = Instant::now();
    // A client flooding open / closed (30 frames a second get through the
    // gateway) must not queue a database write per frame, nor tell every
    // friend each time.
    for n in 0..300 {
        s.input(ada.id, t, Input::DormSetOpen(n % 2 == 0));
    }
    let queued = s.rooms.get(&ada.cid).map_or(0, |q| q.ops.len());
    assert!(queued <= 1, "{queued} open-flag writes queued");
    let jobs = s.take_jobs();
    assert!(jobs.len() <= 1, "{} jobs", jobs.len());
    let told = bea
        .drain()
        .into_iter()
        .filter(|m| {
            matches!(
                m,
                ServerMsg::Friend {
                    event: FriendEvent::RoomOpen { .. }
                }
            )
        })
        .count();
    assert!(
        told <= limits::ACTION_BURST as usize,
        "friends told {told} times"
    );
    assert!(ada.dorm_errors().contains(&DormErrorCode::RateLimited));
}

#[test]
fn friends_hear_when_a_room_opens_and_closes() {
    let Party {
        mut s,
        mut db,
        ada,
        mut bea,
        mut dee,
        ..
    } = party();
    let t = Instant::now();
    s.input(ada.id, t, Input::DormSetOpen(true));
    db.run(&mut s);
    let open = ServerMsg::Friend {
        event: FriendEvent::RoomOpen {
            character_id: ada.cid,
            open: true,
        },
    };
    assert!(bea.drain().contains(&open));
    assert!(dee.drain().is_empty(), "not a friend");
    assert!(s.presence_of(&[ada.cid])[&ada.cid].room_open);
    s.input(ada.id, t, Input::DormSetOpen(false));
    assert!(bea.drain().contains(&ServerMsg::Friend {
        event: FriendEvent::RoomOpen {
            character_id: ada.cid,
            open: false,
        },
    }));
}

#[test]
fn declined_invitations_tell_the_inviter() {
    let Party {
        mut s,
        mut ada,
        mut bea,
        mut cy,
        ..
    } = party();
    let t = Instant::now();
    s.input(ada.id, t, Input::DormInvite("Bea".into()));
    let invite = invite_id(&mut bea);
    // Only the invitee can decline it.
    s.input(cy.id, t, Input::DormDeclineInvite(invite));
    assert!(ada.drain().is_empty());
    assert!(cy.drain().is_empty());
    s.input(bea.id, t, Input::DormDeclineInvite(invite));
    assert_eq!(
        ada.drain(),
        vec![ServerMsg::DormInviteDeclined {
            by_name: "Bea".into()
        }]
    );
    // It is used up.
    s.input(bea.id, t, Input::DormAcceptInvite(invite));
    assert_eq!(bea.dorm_errors(), vec![DormErrorCode::InviteExpired]);
    assert!(s.invites.is_empty());
}

#[test]
fn a_new_invitation_replaces_the_last_to_the_same_friend() {
    let Party {
        mut s,
        ada,
        mut bea,
        mut cy,
        ..
    } = party();
    let t = Instant::now();
    s.input(ada.id, t, Input::DormInvite("Bea".into()));
    s.input(ada.id, t, Input::DormInvite("Cy".into()));
    let first = invite_id(&mut bea);
    invite_id(&mut cy);
    s.input(ada.id, t, Input::DormInvite("Bea".into()));
    let second = invite_id(&mut bea);
    assert_ne!(first, second);
    assert_eq!(s.invites.len(), 2);
    s.input(bea.id, t, Input::DormAcceptInvite(first));
    assert_eq!(bea.dorm_errors(), vec![DormErrorCode::InviteExpired]);
    s.invites
        .expire(t + Duration::from_secs(u64::from(protocol::DORM_INVITE_TTL_SECS) + 1));
    assert!(s.invites.is_empty());
}

#[test]
fn rooms_hold_eleven_guests() {
    let mut s = sim();
    let mut db = FakeDb::default();
    let mut ada = join_as(&mut s, in_dorm("Ada", House::Burrowsby), 256);
    let mut guests: Vec<TestClient> = (0..DORM_GUESTS_MAX + 1)
        .map(|n| {
            join(
                &mut s,
                &format!("Guest{n}"),
                House::Skytalon,
                [100.0, 0.0, 100.0],
            )
        })
        .collect();
    let mut all: Vec<&TestClient> = guests.iter().collect();
    all.push(&ada);
    befriend(&mut s, &all);
    s.input(ada.id, Instant::now(), Input::DormSetOpen(true));
    db.run(&mut s);
    ada.drain();
    let t = Instant::now();
    for g in &guests {
        s.input(g.id, t, Input::DormVisit("Ada".into()));
    }
    db.run(&mut s);
    let inside = guests
        .iter()
        .filter(|g| zone_of(&s, g) == ZoneIdOf::Dorm(ada.cid))
        .count();
    assert_eq!(inside, DORM_GUESTS_MAX);
    let last = guests.last_mut().unwrap();
    assert_eq!(last.dorm_errors(), vec![DormErrorCode::Full]);
}

#[test]
fn a_guest_who_drops_out_reconnects_into_the_room() {
    let Party {
        mut s,
        mut db,
        ada,
        mut bea,
        ..
    } = party();
    let t = Instant::now();
    s.input(ada.id, t, Input::DormSetOpen(true));
    s.input(bea.id, t, Input::DormVisit("Ada".into()));
    db.run(&mut s);
    let bea_char = Character {
        id: bea.cid,
        ..character("Bea", House::Serpentyne, Some([50.0, 0.0, 60.0]))
    };
    assert!(s.remove_player(bea.id));
    bea.drain();
    // Back within two minutes, still friends: into the room, same return point.
    let joining = Joining {
        character: bea_char.clone(),
        social: Social {
            friends: vec![ada.cid],
            ..Social::default()
        },
        dorm_open: false,
        ..Joining::alone(bea_char.clone())
    };
    let back = join_with(&mut s, joining.clone(), 64);
    assert_eq!(zone_of(&s, &back), ZoneIdOf::Dorm(ada.cid));
    assert_eq!(
        s.ents.presence[index(&s, &back)].return_to,
        Some(ReturnPoint::Castle {
            pos: [50.0, 0.0, 60.0],
            yaw: 0
        })
    );
    // Once the owner is gone, the saved return point applies.
    assert!(s.remove_player(back.id));
    assert!(s.remove_player(ada.id));
    let again = join_with(&mut s, joining, 64);
    assert_eq!(zone_of(&s, &again), ZoneIdOf::Castle);
    assert_eq!(s.position(again.id), Some([50.0, 0.0, 60.0]));
    assert!(GUEST_MEMO_TTL > Duration::from_secs(10));
}

// ------------------------------------------------------------ furniture

#[test]
fn owners_decorate_and_everyone_in_the_room_sees_it() {
    let Party {
        mut s,
        mut db,
        mut ada,
        mut bea,
        mut dee,
        ..
    } = party();
    let t = Instant::now();
    let desk = db.give(ada.cid, furniture("desk"));
    let lantern = db.give(ada.cid, furniture("lantern"));
    let foreign = db.give(dee.cid, furniture("rug"));
    s.input(ada.id, t, Input::DormSetOpen(true));
    s.input(bea.id, t, Input::DormVisit("Ada".into()));
    db.run(&mut s);
    ada.drain();
    bea.drain();
    let edits = |c: &mut TestClient| -> Vec<FurnitureEdit> {
        c.drain()
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::DormFurniture { edit } => Some(edit),
                _ => None,
            })
            .collect()
    };

    let at = QPos::from_m([0.0, 0.0, 0.0]);
    s.input(
        ada.id,
        t,
        Input::DormPlace {
            item_id: desk,
            pos: at,
            yaw: 0,
        },
    );
    db.run(&mut s);
    let placed = edits(&mut ada);
    assert!(matches!(placed[..], [FurnitureEdit::Placed(p)] if p.item_id == desk && p.pos == at));
    assert_eq!(edits(&mut bea), placed, "the guest sees it too");
    assert_eq!(db.items[&desk].2, "dorm");

    // A lantern on the desk's top.
    let top = crate::catalog::shipped()
        .catalog
        .get(furniture("desk"))
        .unwrap()
        .furniture
        .as_ref()
        .unwrap()
        .top
        .unwrap();
    s.input(
        ada.id,
        t,
        Input::DormPlace {
            item_id: lantern,
            pos: QPos::from_m([0.2, top, 0.0]),
            yaw: 0,
        },
    );
    db.run(&mut s);
    assert_eq!(edits(&mut bea).len(), 1);

    // Errors: outside the room, someone else's item, already placed, a guest
    // editing, someone outside any room.
    let errs = |s: &mut WorldSim, db: &mut FakeDb, c: &mut TestClient, input| {
        s.input(c.id, t, input);
        db.run(s);
        c.dorm_errors()
    };
    let far = QPos::from_m([30.0, 0.0, 0.0]);
    assert_eq!(
        errs(
            &mut s,
            &mut db,
            &mut ada,
            Input::DormMove {
                item_id: lantern,
                pos: far,
                yaw: 0
            }
        ),
        vec![DormErrorCode::OutOfBounds]
    );
    // The desk holds the lantern: it cannot be moved or put away until the
    // lantern is taken off, so nothing is left floating.
    for input in [
        Input::DormMove {
            item_id: desk,
            pos: QPos::from_m([1.0, 0.0, 0.0]),
            yaw: 0,
        },
        Input::DormRemove { item_id: desk },
    ] {
        assert_eq!(
            errs(&mut s, &mut db, &mut ada, input),
            vec![DormErrorCode::NotEmpty]
        );
    }
    assert_eq!(
        errs(
            &mut s,
            &mut db,
            &mut ada,
            Input::DormPlace {
                item_id: foreign,
                pos: at,
                yaw: 0
            }
        ),
        vec![DormErrorCode::ItemNotFound]
    );
    assert_eq!(
        errs(
            &mut s,
            &mut db,
            &mut ada,
            Input::DormPlace {
                item_id: desk,
                pos: at,
                yaw: 0
            }
        ),
        vec![DormErrorCode::ItemUnavailable]
    );
    assert_eq!(
        errs(
            &mut s,
            &mut db,
            &mut bea,
            Input::DormRemove { item_id: desk }
        ),
        vec![DormErrorCode::NotOwner]
    );
    assert_eq!(
        errs(
            &mut s,
            &mut db,
            &mut dee,
            Input::DormRemove { item_id: desk }
        ),
        vec![DormErrorCode::NotInDorm]
    );
    assert_eq!(
        errs(
            &mut s,
            &mut db,
            &mut ada,
            Input::DormRemove { item_id: 999_999 }
        ),
        vec![DormErrorCode::ItemNotFound]
    );

    // The lantern down on the floor, the desk is free again.
    s.input(
        ada.id,
        t + Duration::from_secs(30),
        Input::DormMove {
            item_id: lantern,
            pos: QPos::from_m([2.0, 0.0, 2.0]),
            yaw: 0,
        },
    );
    db.run(&mut s);
    assert_eq!(edits(&mut bea).len(), 1);
    ada.drain();

    // Moving and turning by a quarter, then taking it back.
    let to = QPos::from_m([1.0, 0.0, -1.0]);
    s.input(
        ada.id,
        t + Duration::from_secs(31),
        Input::DormMove {
            item_id: desk,
            pos: to,
            yaw: 64,
        },
    );
    db.run(&mut s);
    assert_eq!(
        edits(&mut bea),
        vec![FurnitureEdit::Moved {
            item_id: desk,
            pos: to,
            yaw: 64
        }]
    );
    ada.drain();
    s.input(
        ada.id,
        t + Duration::from_secs(32),
        Input::DormRemove { item_id: desk },
    );
    db.run(&mut s);
    assert_eq!(
        edits(&mut ada),
        vec![FurnitureEdit::Removed { item_id: desk }]
    );
    assert_eq!(db.items[&desk].2, "inventory");

    // The room remembers: leaving and coming back loads the lantern.
    s.input(bea.id, t, Input::LeaveInstance);
    s.input(ada.id, t, Input::LeaveInstance);
    assert!(s.instances.is_empty());
    s.input(
        ada.id,
        t + Duration::from_secs(5),
        Input::Interact("dorm_door_r".into()),
    );
    // (Ada is at the door, so in reach.)
    db.run(&mut s);
    let state = ada.drain().into_iter().find_map(|m| match m {
        ServerMsg::DormState { items, .. } => Some(items),
        _ => None,
    });
    assert_eq!(
        state.unwrap().iter().map(|p| p.item_id).collect::<Vec<_>>(),
        vec![lantern]
    );
}

/// Solid furniture (docs/architecture.md "Layouts"): a
/// placed desk is solid for everyone in the room (a walk through it is
/// corrected), never goes on anyone, the entry or across the way out
/// (`BlocksWay`), and taking it away opens the way again.
#[test]
fn placed_furniture_is_solid_and_never_traps_anyone() {
    let Party {
        mut s,
        mut db,
        mut ada,
        mut bea,
        ..
    } = party();
    let t = Instant::now();
    let desk = db.give(ada.cid, furniture("desk"));
    s.input(ada.id, t, Input::DormSetOpen(true));
    s.input(bea.id, t, Input::DormVisit("Ada".into()));
    db.run(&mut s);
    ada.drain();
    bea.drain();
    let place = |s: &mut WorldSim, db: &mut FakeDb, c: &mut TestClient, x: f32, z: f32| {
        s.input(
            c.id,
            t,
            Input::DormPlace {
                item_id: desk,
                pos: QPos::from_m([x, 0.0, z]),
                yaw: 0,
            },
        );
        db.run(s);
        c.dorm_errors()
    };
    // Where Bea stands (in the middle of the room): refused.
    let bi = index(&s, &bea);
    s.ents.pos[bi] = [0.3, 0.0, -1.5];
    assert_eq!(
        place(&mut s, &mut db, &mut ada, 0.0, -1.5),
        vec![DormErrorCode::BlocksWay]
    );
    s.ents.pos[bi] = [-2.5, 0.0, 0.5];
    let ai = index(&s, &ada);
    s.ents.pos[ai] = [2.5, 0.0, 0.5];
    // Across the room's entry (its spawn, z 3.4: the desk reaches z 3.2): refused.
    assert_eq!(
        place(&mut s, &mut db, &mut ada, 0.0, 2.9),
        vec![DormErrorCode::BlocksWay]
    );
    // In the middle of the room: placed, and solid for moves into it.
    assert!(place(&mut s, &mut db, &mut ada, 0.0, -1.5).is_empty());
    let zone = s.ents.zone[index(&s, &ada)];
    let world = s.instances.get(zone).unwrap().world.clone();
    assert!(
        world.solid_at(0.0, 0.0, -1.5).is_some(),
        "the desk is solid in the room's world"
    );
    assert!(world.ground_at(0.0, -1.5, 0.0).is_none());
    // Taken away: walkable again.
    s.input(ada.id, t, Input::DormRemove { item_id: desk });
    db.run(&mut s);
    let world = s.instances.get(zone).unwrap().world.clone();
    assert!(world.solid_at(0.0, 0.0, -1.5).is_none());
}

/// Sitting on placed furniture: a chair the owner placed has a seat (the
/// catalog's `seats`, which clients draw the sitter on, as the castle's
/// chairs), and the sit itself is the server's `Sit` emote, checked as
/// everywhere (standing still, not riding, the emote limit): the owner and
/// a guest both sit at it and everyone in the room sees them seated.
#[test]
fn owners_and_guests_sit_on_placed_chairs() {
    let Party {
        mut s,
        mut db,
        ada,
        mut bea,
        ..
    } = party();
    let t = Instant::now();
    let chair = db.give(ada.cid, furniture("chair_wooden"));
    s.input(ada.id, t, Input::DormSetOpen(true));
    s.input(bea.id, t, Input::DormVisit("Ada".into()));
    db.run(&mut s);
    // The chair goes in the room, and Ada steps up to it.
    let spot = [0.0, 0.0, 2.5];
    let at = QPos::from_m(spot);
    s.input(
        ada.id,
        t,
        Input::DormPlace {
            item_id: chair,
            pos: at,
            yaw: 0,
        },
    );
    db.run(&mut s);
    assert!(
        bea.drain()
            .iter()
            .any(|m| matches!(m, ServerMsg::DormFurniture { edit: FurnitureEdit::Placed(p) } if p.item_id == chair)),
        "the guest sees the chair"
    );
    let seat = crate::catalog::shipped()
        .catalog
        .get(furniture("chair_wooden"))
        .unwrap()
        .furniture
        .as_ref()
        .unwrap()
        .seats[0];
    let [x0, z0, x1, z1] = seat.area;
    assert!(
        x0 < 0.0 && x1 > 0.0 && z0 < 0.0 && z1 > 0.0,
        "the seat is over the chair's origin"
    );
    assert!((0.4..0.5).contains(&seat.y), "a chair's seat height");

    // Solid furniture: the chair is solid, so she stands in front of it
    // (inside the client's sitting reach) and sits from there.
    assert!(!s.dev_teleport(ada.cid, spot, t), "not inside the chair");
    let front = [0.0, 0.0, 2.5 + 0.5];
    assert!(s.dev_teleport(ada.cid, front, t));
    s.input(ada.id, t, Input::Emote(Emote::Sit));
    ticks(&mut s, 2);
    assert_eq!(s.ents.state[index(&s, &ada)].anim, Anim::Sit);
    assert_eq!(
        bea.states_of(ada.id).last().map(|st| st.anim),
        Some(Anim::Sit)
    );
    // The guest sits too (beside it, the client finds the nearest seat).
    s.input(bea.id, t, Input::Emote(Emote::Sit));
    ticks(&mut s, 2);
    assert_eq!(s.ents.state[index(&s, &bea)].anim, Anim::Sit);
}

#[test]
fn rooms_stop_at_the_item_cap() {
    let mut s = sim();
    let mut db = FakeDb::default();
    let mut ada = join_as(&mut s, in_dorm("Ada", House::Skytalon), 1024);
    let rug = furniture("rug");
    for n in 0..DORM_ITEM_CAP {
        let id = db.give(ada.cid, rug);
        db.placed.entry(ada.cid).or_default().push(PlacedFurniture {
            item_id: id,
            item_def: rug,
            dyes: [0; 3],
            pos: QPos::from_m([0.0, 0.0, 0.0]),
            yaw: (n % 256) as u8,
        });
    }
    db.run(&mut s);
    ada.drain();
    let one_more = db.give(ada.cid, rug);
    s.input(
        ada.id,
        Instant::now(),
        Input::DormPlace {
            item_id: one_more,
            pos: QPos::from_m([0.0, 0.0, 0.0]),
            yaw: 0,
        },
    );
    db.run(&mut s);
    assert_eq!(ada.dorm_errors(), vec![DormErrorCode::CapReached]);
}

// ------------------------------------------------------------ friends

#[test]
fn friends_are_priority_players_and_hear_online_and_offline() {
    let mut s = sim();
    let mut db = FakeDb::default();
    let a = join(&mut s, "Ada", House::Burrowsby, [10.0, 0.0, 10.0]);
    let mut b = join(&mut s, "Bea", House::Serpentyne, [190.0, 0.0, 190.0]);
    let _c = join(&mut s, "Cy", House::Burrowsby, [10.5, 0.0, 10.0]);
    // The database now says Ada and Bea are friends; the HTTP side reports it.
    db.social.insert(
        a.cid,
        Social {
            friends: vec![b.cid],
            ..Social::default()
        },
    );
    db.social.insert(
        b.cid,
        Social {
            friends: vec![a.cid],
            ..Social::default()
        },
    );
    b.drain();
    s.handle(WorldCmd::FriendsChanged {
        character_id: a.cid,
    });
    s.handle(WorldCmd::FriendsChanged {
        character_id: b.cid,
    });
    db.run(&mut s);
    // Each hears the other is online, once.
    let online: Vec<i64> = b
        .drain()
        .into_iter()
        .filter_map(|m| match m {
            ServerMsg::Friend {
                event: FriendEvent::Online { character_id, .. },
            } => Some(character_id),
            _ => None,
        })
        .collect();
    assert_eq!(online, vec![a.cid]);
    // With room for one, Ada sees her far-away friend over Cy next to her.
    s.input(a.id, Instant::now(), Input::ViewLimit(1));
    ticks(&mut s, 2);
    assert_eq!(s.ents.client[index(&s, &a)].visible, vec![b.id]);

    // A stale reload (an older sequence number) is ignored.
    s.handle(WorldCmd::SocialLoaded {
        character_id: a.cid,
        seq: 0,
        social: Some(Social::default()),
    });
    assert!(s.ents.social[index(&s, &a)].is_friend(b.cid));

    // Ada leaving: Bea hears it.
    b.drain();
    assert!(s.remove_player(a.id));
    assert!(b.drain().contains(&ServerMsg::Friend {
        event: FriendEvent::Offline {
            character_id: a.cid
        }
    }));
    // Coming back: Bea hears that too, with the place.
    let back = join_with(
        &mut s,
        Joining {
            character: Character {
                id: a.cid,
                ..character("Ada", House::Burrowsby, Some([10.0, 0.0, 10.0]))
            },
            social: Social {
                friends: vec![b.cid],
                ..Social::default()
            },
            dorm_open: true,
            ..Joining::alone(character("Ada", House::Burrowsby, None))
        },
        64,
    );
    assert!(b.drain().contains(&ServerMsg::Friend {
        event: FriendEvent::Online {
            character_id: a.cid,
            location: Location::Castle {
                place: "Hall".into()
            },
            room_open: true,
        }
    }));
    assert_eq!(s.ents.client[index(&s, &b)].online_friends, vec![back.id]);

    // Unfriended: no longer a priority player.
    db.social.insert(a.cid, Social::default());
    s.handle(WorldCmd::FriendsChanged {
        character_id: a.cid,
    });
    db.run(&mut s);
    assert!(s.ents.client[index(&s, &b)].online_friends.is_empty());
    // A change for someone offline is remembered for their join.
    s.handle(WorldCmd::FriendsChanged {
        character_id: 777_777,
    });
    assert!(s.social_changed.contains_key(&777_777));
}

#[test]
fn unfriended_guests_leave_the_room() {
    let Party {
        mut s,
        mut db,
        ada,
        bea,
        ..
    } = party();
    let t = Instant::now();
    s.input(ada.id, t, Input::DormSetOpen(true));
    s.input(bea.id, t, Input::DormVisit("Ada".into()));
    db.run(&mut s);
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Dorm(ada.cid));
    let ai = index(&s, &ada);
    let mut social = s.ents.social[ai].clone();
    social.friends.retain(|&f| f != bea.cid);
    s.apply_social(ai, social, t);
    assert_eq!(zone_of(&s, &bea), ZoneIdOf::Castle);
}

// ------------------------------------------------------------ benchmark

/// `cargo test -p server --release --lib -- --ignored --nocapture world_tick_bench`
///
/// N synthetic players wander the real castle layout (starting crowded in
/// the Entrance Hall), each sending a move every 100 ms, chatting and
/// emoting now and then, all dressed. Reports per-tick wall time (commands +
/// simulation + snapshots) and the payload each client receives. Protocol
/// v10: the `looks` runs also change their look target about every 5 s (a
/// random player, or nobody), for the cost of `LookAt` and `Snapshot.looks`.
#[test]
#[ignore]
fn world_tick_bench() {
    for (players, threads, view, looks) in [
        (200, 2, 50, false),
        (1000, 2, 50, false),
        (1000, 2, 50, true),
        (1000, 2, 100, false),
        (1000, 2, 100, true),
    ] {
        bench(players, threads, view, looks);
    }
}

fn dressed(n: usize) -> Appearance {
    let worn = |item_def: u16, k: usize| {
        Some(protocol::Worn {
            item_def,
            dyes: [(k % 48) as u8, ((k * 7) % 48) as u8, ((k * 13) % 48) as u8],
            finish: protocol::Finish::None,
        })
    };
    Appearance {
        body_type: (n % 2) as u8,
        height: (n * 37 % 256) as u8,
        face: [128, (n % 256) as u8, 90, 160, 200],
        skin_tone: (n % 10) as u8,
        // A shipped style of the look's own body (Male 14-16, Female 17-19).
        hair_style: 14 + 3 * (n % 2) as u16 + (n % 3) as u16,
        hair_colour: (n % 20) as u8,
        eye_colour: (n % 13) as u8,
        outfit: protocol::Outfit {
            hat: worn(101, n),
            body: worn(201 + (n % 4) as u16, n + 1),
            legs: worn(301, n + 2),
            shoes: worn(401, n + 3),
        },
    }
}

fn bench(players: usize, threads: usize, view: u8, looks: bool) {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../data/world/layout.json");
    let world = Arc::new(crate::realtime::load_world(std::path::Path::new(path)).unwrap());
    let metrics = Arc::new(RealtimeMetrics::register(&Registry::default()));
    let mut s = WorldSim::new(world.clone(), WordFilter::default(), None, metrics, threads);
    let params = MoveParams::default();
    let houses = House::ALL;
    let mut clients: Vec<TestClient> = (0..players)
        .map(|n| {
            let mut ch = character(&format!("Bot{n}"), houses[n % 4], None);
            ch.appearance = dressed(n);
            ch.appearance_version = 1;
            join_as(&mut s, ch, 4096)
        })
        .collect();
    for c in &clients {
        s.input(c.id, Instant::now(), Input::ViewLimit(view));
    }
    let mut rng = 0x9e37_79b9u32;
    let mut rand = move || {
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        rng as f32 / u32::MAX as f32
    };
    let mut heading: Vec<f32> = (0..players)
        .map(|_| rand() * std::f32::consts::TAU)
        .collect();
    // Like the bots: walk (or run) for a while, then pause.
    let mut pause = vec![0u32; players];
    let mut seq = vec![0u32; players];
    let mut times = Vec::new();
    let mut bytes = 0usize;
    let t0 = Instant::now();
    let ticks = 60 * WORLD_TICK_HZ; // One simulated minute.
    for tick in 0..ticks {
        let now = t0 + TICK * tick;
        // Inputs, prepared outside the timed section like the gateway does.
        let mut cmds = Vec::with_capacity(players);
        for (n, c) in clients.iter().enumerate() {
            if !(n as u32 + tick).is_multiple_of(2) {
                continue;
            }
            seq[n] += 1;
            let pos = s.position(c.id).unwrap();
            if pause[n] > 0 {
                pause[n] -= 1;
                if rand() < 0.01 {
                    let e = [Emote::Wave, Emote::Bow, Emote::Sit][(rand() * 2.99) as usize];
                    cmds.push(WorldCmd::Input {
                        id: c.id,
                        at: now,
                        input: Input::Emote(e),
                    });
                }
                continue;
            }
            if rand() < 0.02 {
                pause[n] = 10 + (rand() * 60.0) as u32;
            }
            if rand() < 0.05 {
                heading[n] = rand() * std::f32::consts::TAU;
            }
            let run = n % 10 < 3;
            let speed = if run {
                params.run_speed
            } else {
                params.walk_speed
            };
            let step = speed * 0.1;
            // Each house walks its own view of the castle (rival common rooms
            // are walls), as the client predicts.
            let walk = s.castle_world(houses[n % 4]);
            let next = sim_core::move_and_slide(
                walk,
                &params,
                pos,
                [heading[n].sin() * step, heading[n].cos() * step],
            );
            if (next[0] - pos[0]).abs() + (next[2] - pos[2]).abs() < 0.05 {
                heading[n] = rand() * std::f32::consts::TAU;
            }
            cmds.push(WorldCmd::Input {
                id: c.id,
                at: now,
                input: Input::Move {
                    seq: seq[n],
                    pos: next,
                    yaw: heading[n],
                    anim: if run { Anim::Run } else { Anim::Walk },
                },
            });
            if looks && rand() < 0.04 {
                // About every 5 s (two chances a second): someone, or nobody.
                let target = (rand() < 0.5)
                    .then(|| clients[(rand() * players as f32) as usize % players].id);
                cmds.push(WorldCmd::Input {
                    id: c.id,
                    at: now,
                    input: Input::LookAt(target),
                });
            }
            if rand() < 0.002 {
                cmds.push(WorldCmd::Input {
                    id: c.id,
                    at: now,
                    input: Input::Chat {
                        channel: ChatChannel::Say,
                        text: "Has anyone seen the Owl Exchange?".into(),
                        client_seq: 0,
                    },
                });
            }
        }
        let start = Instant::now();
        for cmd in cmds {
            s.handle(cmd);
        }
        s.tick(now);
        times.push(start.elapsed());
        for c in &mut clients {
            while let Ok(b) = c.rx.try_recv() {
                bytes += b.len();
            }
        }
    }
    assert_eq!(s.len(), players, "nobody was corrected off or disconnected");
    let corrections: u64 = s.metrics.corrections.iter().map(|c| c.get()).sum();
    times.sort();
    let pct = |p: f64| times[((times.len() as f64 * p) as usize).min(times.len() - 1)];
    let secs = f64::from(ticks) / f64::from(WORLD_TICK_HZ);
    let mean = times.iter().sum::<Duration>() / times.len() as u32;
    println!(
        "{players} players, view limit {view}, {threads} snapshot threads, looks {looks}: tick p50 {:?}, \
         p99 {:?}, max {:?}, mean {mean:?}; {:.0} B/s per client (payload); {} full states / {} deltas; \
         {} LookAt taken, {} look changes sent; {corrections} corrections",
        pct(0.5),
        pct(0.99),
        times.last().unwrap(),
        bytes as f64 / secs / players as f64,
        s.metrics.snapshot_full_states.get(),
        s.metrics.snapshot_deltas.get(),
        s.metrics.look_targets.get(),
        s.metrics.snapshot_looks.get(),
    );
}
