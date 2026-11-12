//! Tests of spells cast outside duels, on the world thread without a
//! database (dorm rooms load from a hand-made `RoomDone`). Spells to level
//! 20 (SOCIAL): Tinctura, Rigora, Tardara, Adveni, Dissolvo, Aperio
//! and Volucra further down.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use protocol::{
    EffectKind, EntityId, House, LookCache, PlacedFurniture, QPos, ServerMsg, SnapshotReceiver,
    SpellErrorCode, SpellTarget, VisibleEffect, Zone,
};
use tokio::sync::mpsc;

use crate::progression::SchoolState;
use crate::realtime::ConnControl;
use crate::realtime::entities::Social;
use crate::realtime::ext::{ActiveEffect, ExtDone, FeatureKind, SpellInput, TradeInput, WorldCtx};
use crate::realtime::jobs::{Job, RoomOutcome};
use crate::realtime::world::tests::{TestClient, character, join, sim};
use crate::realtime::world::{Input, Joining, WorldCmd, WorldSim};

const LUCERNA: u16 = 1;
const ELEVARA: u16 = 2;
const TORPEO: u16 = 11;

/// A character who knows `learned`, with `friends` (character ids).
fn join_knowing(
    s: &mut WorldSim,
    name: &str,
    house: House,
    pos: [f32; 3],
    learned: Vec<u16>,
    friends: Vec<i64>,
    cid: Option<i64>,
) -> TestClient {
    join_as(s, name, house, pos, learned, cid, |j| {
        j.social = Social {
            friends,
            ..Default::default()
        };
    })
}

/// A character who knows `learned`, as `setup` makes it.
fn join_as(
    s: &mut WorldSim,
    name: &str,
    house: House,
    pos: [f32; 3],
    learned: Vec<u16>,
    cid: Option<i64>,
    setup: impl FnOnce(&mut Joining),
) -> TestClient {
    let mut ch = character(name, house, Some(pos));
    if let Some(id) = cid {
        ch.id = id;
    }
    let mut joining = Joining::alone(ch);
    joining.school = SchoolState {
        xp: 0,
        learned,
        loadout: Vec::new(),
        ..Default::default()
    };
    joining.dorm_open = true;
    setup(&mut joining);
    let (tx, rx) = mpsc::channel(256);
    let control = Arc::new(ConnControl::default());
    let cid = joining.character.id;
    let id = s
        .join(joining, tx, control.clone(), Instant::now())
        .unwrap();
    let mut c = TestClient {
        id,
        cid,
        rx,
        control,
        snaps: SnapshotReceiver::new(),
        looks: LookCache::new(),
    };
    c.drain();
    c
}

fn cast(s: &mut WorldSim, c: &TestClient, at: Instant, spell: u16, target: SpellTarget) {
    s.input(c.id, at, Input::Spell(SpellInput::Cast { spell, target }));
}

fn spell_errors(c: &mut TestClient) -> Vec<SpellErrorCode> {
    c.drain()
        .into_iter()
        .filter_map(|m| match m {
            ServerMsg::SpellError { code, .. } => Some(code),
            _ => None,
        })
        .collect()
}

fn lucerna_seen(c: &mut TestClient, id: EntityId) -> Option<bool> {
    c.drain().into_iter().rev().find_map(|m| match m {
        ServerMsg::EffectsChanged {
            id: who, effects, ..
        } if who == id => Some(effects.contains(&VisibleEffect::Lucerna)),
        _ => None,
    })
}

#[test]
fn lucerna_lights_the_wand_for_viewers_until_ended() {
    let mut s = sim();
    let mut a = join_knowing(
        &mut s,
        "Ada",
        House::Roarwing,
        [214.0, 0.0, 100.0],
        vec![LUCERNA],
        vec![],
        None,
    );
    let mut b = join(&mut s, "Bea", House::Roarwing, [212.0, 0.0, 100.0]);
    let t0 = Instant::now();
    s.tick(t0);
    s.tick(t0);
    a.drain();
    b.drain();
    // Bea has not learned it.
    cast(&mut s, &b, t0, LUCERNA, SpellTarget::None);
    assert_eq!(spell_errors(&mut b), vec![SpellErrorCode::NotLearned]);
    cast(&mut s, &a, t0, LUCERNA, SpellTarget::None);
    let cast_msg = ServerMsg::SpellCast {
        caster: a.id,
        spell: LUCERNA,
        target: SpellTarget::None,
    };
    assert!(b.drain().contains(&cast_msg), "the flourish, nearby");
    s.tick(t0);
    let msgs = b.drain();
    assert!(msgs.iter().any(|m| matches!(
        m,
        ServerMsg::EffectsChanged { id, effects, .. }
            if *id == a.id && effects == &vec![VisibleEffect::Lucerna]
    )));
    let anims: Vec<_> = msgs
        .iter()
        .flat_map(|m| match m {
            ServerMsg::Snapshot { full, .. } => full.clone(),
            _ => vec![],
        })
        .filter(|st| st.id == a.id)
        .map(|st| st.anim)
        .collect();
    assert_eq!(anims, vec![protocol::Anim::Cast], "the cast, seen");
    // Too soon again.
    cast(
        &mut s,
        &a,
        t0 + Duration::from_millis(100),
        LUCERNA,
        SpellTarget::None,
    );
    assert_eq!(spell_errors(&mut a), vec![SpellErrorCode::Cooldown]);
    // Ended: out for everyone.
    s.input(a.id, t0, Input::Spell(SpellInput::End { spell: LUCERNA }));
    s.tick(t0);
    assert_eq!(lucerna_seen(&mut b, a.id), Some(false));
    // A zone change puts it out too.
    cast(
        &mut s,
        &a,
        t0 + Duration::from_secs(2),
        LUCERNA,
        SpellTarget::None,
    );
    s.tick(t0);
    assert_eq!(lucerna_seen(&mut b, a.id), Some(true));
    let id = a.id;
    let lit = move |s: &mut WorldSim| {
        let out = Arc::new(std::sync::Mutex::new(false));
        let o = out.clone();
        let run = ExtDone::Run(Box::new(move |ctx| *o.lock().unwrap() = ctx.lucerna(id)));
        s.ext_done(run, Instant::now());
        *out.lock().unwrap()
    };
    assert!(lit(&mut s));
    s.input(a.id, t0, Input::Interact("dorm_door_r".into()));
    assert!(
        a.drain()
            .iter()
            .any(|m| matches!(m, ServerMsg::ZoneChanged { .. }))
    );
    assert!(!lit(&mut s), "out on entering the room");
}

/// Enters Roarwing owner `o`'s room and loads it with `items`; the
/// room's spawn.
fn into_room(
    s: &mut WorldSim,
    o: &mut TestClient,
    items: impl FnOnce([f32; 3]) -> Vec<PlacedFurniture>,
) {
    s.input(o.id, Instant::now(), Input::Interact("dorm_door_r".into()));
    let spawn = o
        .drain()
        .into_iter()
        .find_map(|m| match m {
            ServerMsg::ZoneChanged {
                zone: Zone::Dorm { .. },
                spawn,
                ..
            } => Some(spawn.to_m()),
            _ => None,
        })
        .expect("into the room");
    assert!(
        s.take_jobs()
            .iter()
            .any(|j| matches!(j, Job::LoadRoom { owner } if *owner == o.cid))
    );
    s.handle(WorldCmd::RoomDone {
        owner: o.cid,
        outcome: RoomOutcome::Loaded(Some(items(spawn))),
    });
    o.drain();
}

fn piece(item_id: i64, pos: [f32; 3]) -> PlacedFurniture {
    PlacedFurniture {
        item_id,
        item_def: 1001,
        dyes: [0, 0, 0],
        pos: QPos::from_m(pos),
        yaw: 0,
    }
}

#[test]
fn elevara_floats_furniture_in_the_room_for_owner_and_guests() {
    let mut s = sim();
    let door = [215.0, 0.0, 100.0];
    let (owner_id, guest_id) = (9_001_001, 9_001_002);
    let mut o = join_knowing(
        &mut s,
        "Ola",
        House::Roarwing,
        door,
        vec![ELEVARA],
        vec![guest_id],
        Some(owner_id),
    );
    let mut g = join_knowing(
        &mut s,
        "Gus",
        House::Skytalon,
        [150.0, 0.0, 100.0],
        vec![ELEVARA, TORPEO],
        vec![owner_id],
        Some(guest_id),
    );
    // Not here: the castle has no placed furniture.
    let t0 = Instant::now();
    cast(
        &mut s,
        &g,
        t0,
        ELEVARA,
        SpellTarget::Furniture { item_id: 1 },
    );
    assert_eq!(spell_errors(&mut g), vec![SpellErrorCode::NotHere]);

    // One piece by the entry, one across the room.
    into_room(&mut s, &mut o, |at| {
        vec![
            piece(501, [at[0] + 1.0, at[1], at[2]]),
            piece(502, [at[0] + 1.0, at[1], at[2] + 30.0]),
        ]
    });
    s.input(g.id, t0, Input::DormVisit("Ola".into()));
    assert!(g.drain().iter().any(|m| matches!(
        m,
        ServerMsg::ZoneChanged {
            zone: Zone::Dorm { .. },
            ..
        }
    )));

    cast(
        &mut s,
        &o,
        t0,
        ELEVARA,
        SpellTarget::Furniture { item_id: 501 },
    );
    let floating = ServerMsg::Levitating {
        item_id: 501,
        by: o.id,
        duration_ms: 5_000,
    };
    assert!(o.drain().contains(&floating));
    assert!(g.drain().contains(&floating), "the whole room sees it");
    // The guest may cast too, but not on a piece that is already up...
    cast(
        &mut s,
        &g,
        t0,
        ELEVARA,
        SpellTarget::Furniture { item_id: 501 },
    );
    assert_eq!(spell_errors(&mut g), vec![SpellErrorCode::Busy]);
    // ...nor out of range, nor on something that is not placed here, nor
    // on nothing; a flourish needs no furniture.
    cast(
        &mut s,
        &g,
        t0,
        ELEVARA,
        SpellTarget::Furniture { item_id: 502 },
    );
    cast(
        &mut s,
        &g,
        t0,
        ELEVARA,
        SpellTarget::Furniture { item_id: 999 },
    );
    cast(&mut s, &g, t0, ELEVARA, SpellTarget::None);
    cast(
        &mut s,
        &g,
        t0,
        TORPEO,
        SpellTarget::Furniture { item_id: 501 },
    );
    assert_eq!(
        spell_errors(&mut g),
        vec![
            SpellErrorCode::TooFar,
            SpellErrorCode::BadTarget,
            SpellErrorCode::BadTarget,
            SpellErrorCode::BadTarget,
        ]
    );
    // Once it lands, the guest lifts it.
    let later = t0 + Duration::from_secs(6);
    cast(
        &mut s,
        &g,
        later,
        ELEVARA,
        SpellTarget::Furniture { item_id: 501 },
    );
    assert!(o.drain().contains(&ServerMsg::Levitating {
        item_id: 501,
        by: g.id,
        duration_ms: 5_000,
    }));
    // A flourish aimed at someone in range.
    cast(&mut s, &g, later, TORPEO, SpellTarget::Entity { id: o.id });
    assert!(o.drain().contains(&ServerMsg::SpellCast {
        caster: g.id,
        spell: TORPEO,
        target: SpellTarget::Entity { id: o.id },
    }));
}

// ------------------------------------------------------- spells to level 20 (SOCIAL)

const TINCTURA: u16 = 3;
const ADVENI: u16 = 4;
const APERIO: u16 = 5;
const RIGORA: u16 = 15;
const DISSOLVO: u16 = 16;
const TARDARA: u16 = 17;
const VOLUCRA: u16 = 30;
const SOCIAL_L20: [u16; 7] = [TINCTURA, ADVENI, APERIO, RIGORA, DISSOLVO, TARDARA, VOLUCRA];
/// B's robe's primary dye (gold, one of Tinctura's colours).
const B_DYE: u8 = 21;

fn run(s: &mut WorldSim, f: impl FnOnce(&mut WorldCtx<'_>) + Send + 'static) {
    s.handle(WorldCmd::ExtDone(ExtDone::Run(Box::new(f))));
}

/// Asks the world something, on its thread.
fn ask<T: Send + 'static>(
    s: &mut WorldSim,
    f: impl FnOnce(&mut WorldCtx<'_>) -> T + Send + 'static,
) -> T {
    let out = Arc::new(Mutex::new(None));
    let o = out.clone();
    run(s, move |ctx| *o.lock().unwrap() = Some(f(ctx)));
    out.lock().unwrap().take().expect("answered")
}

fn kinds_of(s: &mut WorldSim, id: EntityId) -> Vec<EffectKind> {
    ask(s, move |ctx| {
        ctx.effects(id).iter().map(|e| e.kind).collect()
    })
}

fn recolour_of(s: &mut WorldSim, id: EntityId) -> Option<u8> {
    ask(s, move |ctx| {
        ctx.effects(id).iter().find_map(|e| match e.visible {
            Some(VisibleEffect::Recoloured { colour }) => Some(colour),
            _ => None,
        })
    })
}

fn position_of(s: &mut WorldSim, id: EntityId) -> [f32; 3] {
    ask(s, move |ctx| ctx.position(id).unwrap())
}

fn ent(id: EntityId) -> SpellTarget {
    SpellTarget::Entity { id }
}

fn spell_cast(caster: EntityId, spell: u16, target: SpellTarget) -> ServerMsg {
    ServerMsg::SpellCast {
        caster,
        spell,
        target,
    }
}

fn own_speed(msgs: &[ServerMsg]) -> Option<u16> {
    msgs.iter().rev().find_map(|m| match m {
        ServerMsg::OwnEffects { speed_pct, .. } => Some(*speed_pct),
        _ => None,
    })
}

/// The caster A, who knows every new social spell, and around it: B, a
/// friend 3 m east wearing a gold robe; F, a friend 15 m away; C, a
/// stranger; D, a friend A has blocked. B and F know the spells too.
struct Party {
    a: TestClient,
    b: TestClient,
    c: TestClient,
    d: TestClient,
    f: TestClient,
}

const A_ID: i64 = 9_002_001;
const B_ID: i64 = 9_002_002;
const C_ID: i64 = 9_002_003;
const D_ID: i64 = 9_002_004;
const F_ID: i64 = 9_002_005;

fn party_at(s: &mut WorldSim, a_pos: [f32; 3], b_pos: [f32; 3]) -> Party {
    let all = SOCIAL_L20.to_vec();
    let friends = |ids: Vec<i64>| Social {
        friends: ids,
        ..Default::default()
    };
    let a = join_as(
        s,
        "Ada",
        House::Roarwing,
        a_pos,
        all.clone(),
        Some(A_ID),
        |j| {
            j.social = Social {
                friends: vec![B_ID, D_ID, F_ID],
                blocked: vec![D_ID],
                blocked_by: vec![],
            };
        },
    );
    let b = join_as(
        s,
        "Bea",
        House::Burrowsby,
        b_pos,
        all.clone(),
        Some(B_ID),
        |j| {
            j.social = friends(vec![A_ID]);
            j.character.appearance.outfit.body = Some(protocol::Worn::new(1, [B_DYE, 0, 0]));
        },
    );
    let side = |p: [f32; 3], dx: f32, dz: f32| [p[0] + dx, p[1], p[2] + dz];
    let c = join_as(
        s,
        "Cal",
        House::Serpentyne,
        side(a_pos, -3.0, 0.0),
        vec![],
        Some(C_ID),
        |_| {},
    );
    let d = join_as(
        s,
        "Dot",
        House::Skytalon,
        side(a_pos, 0.0, -3.0),
        vec![],
        Some(D_ID),
        |j| {
            j.social = Social {
                friends: vec![A_ID],
                blocked: vec![],
                blocked_by: vec![A_ID],
            };
        },
    );
    let f = join_as(
        s,
        "Fin",
        House::Roarwing,
        side(a_pos, 0.0, 15.0),
        all,
        Some(F_ID),
        |j| {
            j.social = friends(vec![A_ID]);
        },
    );
    let mut p = Party { a, b, c, d, f };
    // Two ticks: everyone in view of everyone near.
    let now = Instant::now();
    s.tick(now);
    s.tick(now);
    p.drain();
    p
}

fn party(s: &mut WorldSim) -> Party {
    party_at(s, [100.0, 0.0, 60.0], [103.0, 0.0, 60.0])
}

impl Party {
    fn drain(&mut self) {
        for c in [
            &mut self.a,
            &mut self.b,
            &mut self.c,
            &mut self.d,
            &mut self.f,
        ] {
            c.drain();
        }
    }
}

#[test]
fn colour_picks_skip_the_robe_and_the_colour_shown() {
    let colours = [17, 19, 21];
    for roll in 0..50 {
        let c = super::pick_colour(&colours, &[Some(21), Some(17)], roll).unwrap();
        assert_eq!(c, 19);
        let c = super::pick_colour(&colours, &[Some(21), None], roll).unwrap();
        assert!(c == 17 || c == 19);
    }
    // Everything excluded: any of them; nothing to pick from: none.
    assert!(super::pick_colour(&[21], &[Some(21)], 3) == Some(21));
    assert_eq!(super::pick_colour(&[], &[], 3), None);
}

#[test]
fn tinctura_recolours_a_friend_or_yourself_for_its_time() {
    let mut s = sim();
    let mut p = party(&mut s);
    let t0 = Instant::now();
    s.tick(t0);
    p.drain();
    let (a, b) = (p.a.id, p.b.id);
    // A stranger, a blocked friend, a friend too far, a piece of furniture.
    for target in [ent(p.c.id), ent(p.d.id), ent(p.f.id)] {
        cast(&mut s, &p.a, t0, TINCTURA, target);
    }
    cast(
        &mut s,
        &p.a,
        t0,
        TINCTURA,
        SpellTarget::Furniture { item_id: 1 },
    );
    assert_eq!(
        spell_errors(&mut p.a),
        vec![
            SpellErrorCode::NotFriend,
            SpellErrorCode::NotFriend,
            SpellErrorCode::TooFar,
            SpellErrorCode::BadTarget,
        ]
    );
    assert_eq!(recolour_of(&mut s, p.c.id), None);
    // Refusals start no cooldown: B at once, seen by those near.
    let colours = match &crate::catalog::shipped()
        .spells
        .get(TINCTURA)
        .unwrap()
        .social
    {
        sim_core::spells::Social::Recolour { colours, .. } => colours.clone(),
        _ => unreachable!(),
    };
    let mut shown = None;
    let mut last = t0;
    for k in 0..16u64 {
        last = t0 + Duration::from_secs(6 * k);
        cast(&mut s, &p.a, last, TINCTURA, ent(b));
        assert_eq!(spell_errors(&mut p.a), vec![]);
        let c = recolour_of(&mut s, b).expect("recoloured");
        assert!(colours.contains(&c));
        assert_ne!(c, B_DYE, "never the robe's own colour");
        assert_ne!(Some(c), shown, "never the colour it shows");
        shown = Some(c);
    }
    assert!(p.b.drain().contains(&spell_cast(a, TINCTURA, ent(b))));
    assert!(p.c.drain().contains(&spell_cast(a, TINCTURA, ent(b))));
    // Too soon again.
    cast(
        &mut s,
        &p.a,
        last + Duration::from_secs(1),
        TINCTURA,
        ent(b),
    );
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::Cooldown]);
    // Viewers see it; it ends on its tick, 30 s on.
    s.tick(last + Duration::from_millis(100));
    assert!(p.c.drain().iter().any(|m| matches!(
        m,
        ServerMsg::EffectsChanged { id, effects, .. }
            if *id == b && effects == &vec![VisibleEffect::Recoloured { colour: shown.unwrap() }]
    )));
    s.tick(last + Duration::from_millis(29_990));
    assert_eq!(kinds_of(&mut s, b), vec![EffectKind::Recoloured]);
    s.tick(last + Duration::from_millis(30_001));
    assert_eq!(kinds_of(&mut s, b), vec![]);
    // Nothing selected (or yourself): your own robe.
    let later = last + Duration::from_secs(31);
    cast(&mut s, &p.a, later, TINCTURA, SpellTarget::None);
    assert!(recolour_of(&mut s, a).is_some());
    assert!(
        p.b.drain()
            .contains(&spell_cast(a, TINCTURA, SpellTarget::None))
    );
    cast(
        &mut s,
        &p.a,
        later + Duration::from_secs(6),
        TINCTURA,
        ent(a),
    );
    assert_eq!(spell_errors(&mut p.a), vec![]);
}

#[test]
fn rigora_and_tardara_bind_and_slow_a_friend() {
    let mut s = sim();
    let mut p = party(&mut s);
    let t0 = Instant::now();
    s.tick(t0);
    p.drain();
    let (a, b) = (p.a.id, p.b.id);
    cast(&mut s, &p.a, t0, RIGORA, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![]);
    assert!(p.c.drain().contains(&spell_cast(a, RIGORA, ent(b))));
    assert_eq!(kinds_of(&mut s, b), vec![EffectKind::Petrified]);
    assert_eq!(own_speed(&p.b.drain()), Some(0), "stiff: no walking");
    s.tick(t0 + Duration::from_millis(100));
    assert!(p.c.drain().iter().any(|m| matches!(
        m,
        ServerMsg::EffectsChanged { id, effects, .. }
            if *id == b && effects == &vec![VisibleEffect::Bound]
    )));
    // 1.2 s, ended on its tick.
    s.tick(t0 + Duration::from_millis(1_201));
    assert_eq!(kinds_of(&mut s, b), vec![]);
    assert_eq!(own_speed(&p.b.drain()), Some(100));
    // Immune for 8 s from the bind: Tardara waits.
    cast(&mut s, &p.a, t0 + Duration::from_secs(3), TARDARA, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::Busy]);
    let t1 = t0 + Duration::from_millis(8_001);
    cast(&mut s, &p.a, t1, TARDARA, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![]);
    let bm = p.b.drain();
    assert!(bm.contains(&spell_cast(a, TARDARA, ent(b))));
    assert_eq!(own_speed(&bm), Some(40));
    s.tick(t1);
    assert!(p.c.drain().iter().any(|m| matches!(
        m,
        ServerMsg::EffectsChanged { id, effects, .. }
            if *id == b && effects == &vec![VisibleEffect::Slowed { pct: 40 }]
    )));
    s.tick(t1 + Duration::from_millis(4_001));
    assert_eq!(kinds_of(&mut s, b), vec![]);
}

#[test]
fn hindering_spells_refuse_strangers_far_friends_and_no_target() {
    for spell in [RIGORA, TARDARA, ADVENI] {
        let mut s = sim();
        let mut p = party(&mut s);
        let t0 = Instant::now();
        let a = p.a.id;
        let targets = [
            SpellTarget::None,
            ent(a),
            ent(p.c.id),
            ent(p.d.id),
            ent(p.f.id),
            ent(0xdead),
            SpellTarget::Furniture { item_id: 1 },
        ];
        for t in targets {
            cast(&mut s, &p.a, t0, spell, t);
        }
        assert_eq!(
            spell_errors(&mut p.a),
            vec![
                SpellErrorCode::NeedsTarget,
                SpellErrorCode::NeedsTarget,
                SpellErrorCode::NotFriend,
                SpellErrorCode::NotFriend,
                SpellErrorCode::TooFar,
                SpellErrorCode::BadTarget,
                SpellErrorCode::BadTarget,
            ],
            "spell {spell}"
        );
        for c in [p.c.id, p.d.id, p.f.id] {
            assert_eq!(kinds_of(&mut s, c), vec![], "spell {spell}");
        }
    }
}

#[test]
fn party_and_club_members_are_allies_unless_blocked() {
    let mut s = sim();
    let at = [100.0, 0.0, 60.0];
    let near = |dx: f32| [at[0] + dx, at[1], at[2] + 1.0];
    let club = crate::clubs::Club {
        id: 77,
        name: "The Night Owls".into(),
        tag: "OWL".into(),
        motd: String::new(),
        version: 1,
        members: [(A_ID, "Ada"), (C_ID, "Cal"), (D_ID, "Dot")]
            .into_iter()
            .map(|(character_id, name)| crate::clubs::Member {
                character_id,
                name: name.into(),
                house: House::Roarwing,
                level: 3,
                role: protocol::ClubRole::Member,
            })
            .collect(),
    };
    let in_club = |j: &mut Joining| j.club = Some(club.clone());
    // Ada: no friends; Dot (a clubmate) blocked her.
    let mut a = join_as(
        &mut s,
        "Ada",
        House::Roarwing,
        at,
        SOCIAL_L20.to_vec(),
        Some(A_ID),
        |j| {
            in_club(j);
            j.social.blocked_by = vec![D_ID];
        },
    );
    let mut b = join_as(
        &mut s,
        "Bea",
        House::Burrowsby,
        near(2.0),
        vec![],
        Some(B_ID),
        |_| {},
    );
    let c = join_as(
        &mut s,
        "Cal",
        House::Serpentyne,
        near(-2.0),
        vec![],
        Some(C_ID),
        in_club,
    );
    let d = join_as(
        &mut s,
        "Dot",
        House::Skytalon,
        near(3.0),
        vec![],
        Some(D_ID),
        |j| {
            in_club(j);
            j.social.blocked = vec![A_ID];
        },
    );
    let f = join_as(
        &mut s,
        "Fen",
        House::Skytalon,
        near(-3.0),
        vec![],
        Some(F_ID),
        |_| {},
    );
    let t0 = Instant::now();
    s.tick(t0);
    // Bea joins Ada's party.
    s.input(a.id, t0, Input::PartyInvite("Bea".into()));
    s.tick(t0);
    let invite_id = b
        .drain()
        .into_iter()
        .find_map(|m| match m {
            ServerMsg::PartyInvited { invite_id, .. } => Some(invite_id),
            _ => None,
        })
        .expect("Bea is invited");
    s.input(
        b.id,
        t0,
        Input::PartyRespond {
            invite_id,
            accept: true,
        },
    );
    s.tick(t0);
    a.drain();
    // A stranger and a clubmate who blocked us: refused.
    cast(&mut s, &a, t0, TINCTURA, ent(f.id));
    cast(&mut s, &a, t0, TINCTURA, ent(d.id));
    assert_eq!(
        spell_errors(&mut a),
        vec![SpellErrorCode::NotFriend, SpellErrorCode::NotFriend]
    );
    // A party member and a club member: cast.
    cast(&mut s, &a, t0, TARDARA, ent(b.id));
    cast(&mut s, &a, t0, RIGORA, ent(c.id));
    assert_eq!(spell_errors(&mut a), vec![]);
    assert_eq!(kinds_of(&mut s, b.id), vec![EffectKind::Slowed]);
    assert_eq!(kinds_of(&mut s, c.id), vec![EffectKind::Petrified]);
    assert_eq!(kinds_of(&mut s, d.id), vec![]);
    assert_eq!(kinds_of(&mut s, f.id), vec![]);
}

/// What makes a friend unavailable to the hindering spells.
#[derive(Debug, Clone, Copy)]
enum Busy {
    Sitting,
    Mounted,
    Trading,
    Challenged,
    InClass,
}

#[test]
fn hindering_spells_wait_for_a_free_friend() {
    for spell in [RIGORA, TARDARA, ADVENI] {
        for busy in [
            Busy::Sitting,
            Busy::Mounted,
            Busy::Trading,
            Busy::Challenged,
            Busy::InClass,
        ] {
            // Brooms need open sky; the test hall has a roof.
            let mut s = match busy {
                Busy::Mounted => sim_of(OPEN_LAYOUT),
                Busy::Challenged => sim_of(CLUB_LAYOUT),
                _ => sim(),
            };
            // The club (dueling venue) for the challenge, open hall otherwise.
            let (a_pos, b_pos) = match busy {
                Busy::Challenged => ([150.0, 0.0, 98.0], [150.0, 0.0, 102.0]),
                _ => ([100.0, 0.0, 60.0], [104.0, 0.0, 60.0]),
            };
            let mut p = party_at(&mut s, a_pos, b_pos);
            let t0 = Instant::now();
            let (a, b) = (p.a.id, p.b.id);
            match busy {
                Busy::Sitting => s.input(b, t0, Input::Emote(protocol::Emote::Sit)),
                Busy::Mounted => run(&mut s, move |ctx| assert!(ctx.mount_broom(b))),
                Busy::Trading => {
                    s.input(b, t0, Input::Trade(TradeInput::Request { target: a }));
                    let trade_id =
                        p.a.drain()
                            .into_iter()
                            .find_map(|m| match m {
                                ServerMsg::TradeRequested { trade_id, .. } => Some(trade_id),
                                _ => None,
                            })
                            .expect("asked");
                    let respond = TradeInput::Respond {
                        trade_id,
                        accept: true,
                    };
                    s.input(a, t0, Input::Trade(respond));
                }
                Busy::Challenged => {
                    let set = vec![TORPEO];
                    run(&mut s, move |ctx| {
                        ctx.set_spellbook(b, set.clone(), set, Vec::new());
                    });
                    let msg = crate::realtime::ext::DuelMsg::Challenge { target: a };
                    s.input(b, t0, Input::Duel(msg));
                    assert!(
                        p.a.drain()
                            .iter()
                            .any(|m| matches!(m, ServerMsg::DuelChallenged { .. })),
                        "challenged"
                    );
                }
                Busy::InClass => run(&mut s, move |ctx| {
                    let kind = FeatureKind::Class {
                        class: "charms".into(),
                        section: 0,
                    };
                    let zone = ctx.open_instance(kind, "class_charms").unwrap();
                    assert!(ctx.enter_instance(a, zone, None));
                    assert!(ctx.enter_instance(b, zone, None));
                }),
            }
            let free = ask(&mut s, move |ctx| ctx.spell_target_free(b));
            assert!(!free, "{busy:?}");
            p.a.drain();
            let at = t0 + Duration::from_secs(1);
            cast(&mut s, &p.a, at, spell, ent(b));
            assert_eq!(
                spell_errors(&mut p.a),
                vec![SpellErrorCode::Unavailable],
                "spell {spell}, {busy:?}"
            );
            assert_eq!(kinds_of(&mut s, b), vec![], "{busy:?}");
        }
    }
}

#[test]
fn a_bound_caster_may_cast_only_dissolvo() {
    let mut s = sim();
    let mut p = party(&mut s);
    let t0 = Instant::now();
    let (a, b) = (p.a.id, p.b.id);
    // B binds A.
    cast(&mut s, &p.b, t0, RIGORA, ent(a));
    assert_eq!(spell_errors(&mut p.b), vec![]);
    assert_eq!(kinds_of(&mut s, a), vec![EffectKind::Petrified]);
    for spell in [TINCTURA, RIGORA, VOLUCRA, APERIO] {
        let target = if spell == RIGORA {
            ent(b)
        } else {
            SpellTarget::None
        };
        cast(&mut s, &p.a, t0, spell, target);
    }
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::Busy; 4]);
    // Dissolvo on yourself frees you at once; then everything casts again.
    cast(&mut s, &p.a, t0, DISSOLVO, SpellTarget::None);
    let am = p.a.drain();
    assert!(!am.iter().any(|m| matches!(m, ServerMsg::SpellError { .. })));
    assert_eq!(own_speed(&am), Some(100));
    assert!(
        p.b.drain()
            .contains(&spell_cast(a, DISSOLVO, SpellTarget::None))
    );
    assert_eq!(kinds_of(&mut s, a), vec![]);
    cast(&mut s, &p.a, t0, VOLUCRA, SpellTarget::None);
    assert_eq!(spell_errors(&mut p.a), vec![]);
}

#[test]
fn adveni_slides_a_friend_toward_the_caster() {
    let mut s = sim();
    let mut p = party_at(&mut s, [100.0, 0.0, 60.0], [106.0, 0.0, 60.0]);
    let t0 = Instant::now();
    s.tick(t0);
    p.drain();
    let (a, b) = (p.a.id, p.b.id);
    let expected = ask(&mut s, move |ctx| {
        ctx.pull_destination(b, a, 2.5, 1.5, 1.2).unwrap()
    });
    assert!((expected[0] - 103.5).abs() < 1e-3, "{expected:?}");
    cast(&mut s, &p.a, t0, ADVENI, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![]);
    let msgs = p.b.drain();
    assert!(msgs.contains(&ServerMsg::ForcedMove {
        to: QPos::from_m(expected),
        move_ms: 350,
        hold_ms: 450,
        by: a,
    }));
    assert!(msgs.contains(&spell_cast(a, ADVENI, ent(b))));
    assert!(p.c.drain().contains(&spell_cast(a, ADVENI, ent(b))));
    // Being moved: not again (and immune after).
    cast(&mut s, &p.a, t0, RIGORA, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::Unavailable]);
    s.tick(t0 + Duration::from_millis(400));
    assert_eq!(position_of(&mut s, b), expected, "lands where it said");
    s.tick(t0 + Duration::from_millis(800));
    cast(&mut s, &p.a, t0 + Duration::from_secs(2), RIGORA, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::Busy]);
    // 3.5 m apart now: a shorter pull, to the gap (1.2 m from the caster)...
    let t1 = t0 + Duration::from_secs(20);
    cast(&mut s, &p.a, t1, ADVENI, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![]);
    s.tick(t1 + Duration::from_millis(400));
    let at = position_of(&mut s, b);
    assert!((at[0] - 101.2).abs() < 1e-3, "{at:?}");
    s.tick(t1 + Duration::from_secs(1));
    // ...and then too close for the shortest pull.
    cast(&mut s, &p.a, t1 + Duration::from_secs(20), ADVENI, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::NoPath]);
}

#[test]
fn adveni_finds_no_path_off_a_ledge() {
    let mut s = sim();
    // B up on the gallery (6 m), A below beside it.
    let mut p = party_at(&mut s, [56.0, 0.0, 20.0], [49.0, 6.0, 20.0]);
    let t0 = Instant::now();
    let b = p.b.id;
    cast(&mut s, &p.a, t0, ADVENI, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::NoPath]);
    assert_eq!(position_of(&mut s, b), [49.0, 6.0, 20.0]);
    // Refused: no cooldown, no immunity.
    cast(&mut s, &p.a, t0, RIGORA, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![]);
}

/// Two rooms joined by a doorway (a door surface 0.5 m deep at x = 100).
const DOOR_LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "west", "kind": "room", "name": "West", "level": 0, "cell": "c",
          "min": [0, 0], "max": [100, 100], "y": 0, "ceiling": 6 },
        { "id": "door", "kind": "door", "name": "Door", "level": 0, "cell": "c",
          "min": [100, 40], "max": [100.5, 42], "y": 0, "ceiling": 3 },
        { "id": "east", "kind": "room", "name": "East", "level": 0, "cell": "c",
          "min": [100.5, 0], "max": [200, 100], "y": 0, "ceiling": 6 }
    ],
    "spawns": [{ "id": "s", "pos": [50, 0, 50], "yaw": 0 }],
    "interactables": [],
    "areas": []
}"#;

/// Open grounds under the sky (brooms may mount there).
const OPEN_LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "lawn", "kind": "grounds", "name": "Lawn", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0 }
    ],
    "spawns": [{ "id": "s", "pos": [50, 0, 50], "yaw": 0 }],
    "interactables": [],
    "areas": [
        { "id": "sky", "kind": "airspace", "name": "Sky", "min": [0, 0, 0], "max": [200, 40, 200] }
    ]
}"#;

/// A hall with the Dueling Club's area (the shipped venue's id).
const CLUB_LAYOUT: &str = r#"{
    "version": 1,
    "surfaces": [
        { "id": "hall", "kind": "room", "name": "Hall", "level": 0, "cell": "c",
          "min": [0, 0], "max": [200, 200], "y": 0, "ceiling": 10 }
    ],
    "spawns": [{ "id": "s", "pos": [100, 0, 100], "yaw": 0 }],
    "areas": [
        { "id": "dueling_club", "kind": "dueling_club", "name": "Dueling Club",
          "min": [140, -1, 90], "max": [160, 3, 110] }
    ]
}"#;

/// A world of `layout` (no database).
fn sim_of(layout: &str) -> WorldSim {
    WorldSim::new(
        Arc::new(sim_core::World::from_json(layout).unwrap()),
        crate::realtime::chat::WordFilter::from_list("badword"),
        None,
        Arc::new(crate::realtime::metrics::RealtimeMetrics::register(
            &crate::metrics::Registry::default(),
        )),
        1,
    )
}

#[test]
fn adveni_never_pulls_through_a_doorway() {
    let mut s = sim_of(DOOR_LAYOUT);
    // B a step past the doorway: every pull from 2.5 m down to 1.5 m crosses it.
    let mut p = party_at(&mut s, [97.0, 0.0, 41.0], [101.5, 0.0, 41.0]);
    let t0 = Instant::now();
    let b = p.b.id;
    cast(&mut s, &p.a, t0, ADVENI, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::NoPath]);
    assert_eq!(position_of(&mut s, b), [101.5, 0.0, 41.0]);
    // Standing in the doorway itself.
    let mut s = sim_of(DOOR_LAYOUT);
    let mut q = party_at(&mut s, [96.0, 0.0, 41.0], [100.25, 0.0, 41.0]);
    cast(&mut s, &q.a, t0, ADVENI, ent(q.b.id));
    assert_eq!(spell_errors(&mut q.a), vec![SpellErrorCode::NoPath]);
    let _ = (&mut p, &mut q);
}

fn lit(s: &mut WorldSim, id: EntityId) -> bool {
    ask(s, move |ctx| ctx.lucerna(id))
}

#[test]
fn dissolvo_ends_spells_on_a_friend_or_yourself_but_not_potions_or_the_guardian() {
    let mut s = sim();
    let mut p = party(&mut s);
    let t0 = Instant::now();
    let (a, b) = (p.a.id, p.b.id);
    // Nothing to end: refused (and no cooldown).
    cast(&mut s, &p.a, t0, DISSOLVO, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::BadTarget]);
    // B: lit, slowed, recoloured, a potion and a guardian.
    let until = t0 + Duration::from_secs(60);
    run(&mut s, move |ctx| {
        ctx.set_lucerna(b, true);
        let effect = |kind, visible, speed_pct| ActiveEffect {
            kind,
            item_def: 0,
            fortune: None,
            visible: Some(visible),
            speed_pct,
            until,
        };
        ctx.apply_effect(
            b,
            effect(EffectKind::Slowed, VisibleEffect::Slowed { pct: 40 }, 40),
        );
        ctx.apply_effect(
            b,
            effect(
                EffectKind::Recoloured,
                VisibleEffect::Recoloured { colour: 17 },
                100,
            ),
        );
        let glow = ActiveEffect {
            item_def: 3301,
            ..effect(EffectKind::Glow, VisibleEffect::Glow { colour: 5 }, 100)
        };
        ctx.apply_effect(b, glow);
        let animal = protocol::GuardianAnimal::Fox;
        ctx.apply_effect(
            b,
            effect(
                EffectKind::Guardian,
                VisibleEffect::Guardian {
                    animal,
                    guarding: false,
                },
                100,
            ),
        );
    });
    p.drain();
    // Strangers and far friends: no.
    cast(&mut s, &p.a, t0, DISSOLVO, ent(p.c.id));
    cast(&mut s, &p.a, t0, DISSOLVO, ent(p.f.id));
    assert_eq!(
        spell_errors(&mut p.a),
        vec![SpellErrorCode::NotFriend, SpellErrorCode::TooFar]
    );
    cast(&mut s, &p.a, t0, DISSOLVO, ent(b));
    assert_eq!(spell_errors(&mut p.a), vec![]);
    assert!(p.c.drain().contains(&spell_cast(a, DISSOLVO, ent(b))));
    assert!(!lit(&mut s, b));
    let mut left = kinds_of(&mut s, b);
    left.sort_by_key(|k| format!("{k:?}"));
    assert_eq!(left, vec![EffectKind::Glow, EffectKind::Guardian]);
    assert_eq!(own_speed(&p.b.drain()), Some(100));
    // Cooldown 3 s; then on yourself (bound by B a moment before).
    let t1 = t0 + Duration::from_secs(4);
    cast(&mut s, &p.b, t1, RIGORA, ent(a));
    assert_eq!(spell_errors(&mut p.b), vec![]);
    cast(&mut s, &p.a, t1, DISSOLVO, ent(a));
    assert_eq!(spell_errors(&mut p.a), vec![]);
    assert_eq!(kinds_of(&mut s, a), vec![]);
}

#[test]
fn dissolvo_lands_a_floating_piece() {
    let mut s = sim();
    let door = [215.0, 0.0, 100.0];
    let mut o = join_knowing(
        &mut s,
        "Ola",
        House::Roarwing,
        door,
        vec![ELEVARA, DISSOLVO],
        vec![],
        Some(9_003_001),
    );
    let t0 = Instant::now();
    // Not here: nothing floats in the castle.
    cast(
        &mut s,
        &o,
        t0,
        DISSOLVO,
        SpellTarget::Furniture { item_id: 501 },
    );
    assert_eq!(spell_errors(&mut o), vec![SpellErrorCode::BadTarget]);
    into_room(&mut s, &mut o, |at| {
        vec![
            piece(501, [at[0] + 1.0, at[1], at[2]]),
            piece(502, [at[0] + 1.0, at[1], at[2] + 30.0]),
        ]
    });
    // On the floor: nothing to land.
    cast(
        &mut s,
        &o,
        t0,
        DISSOLVO,
        SpellTarget::Furniture { item_id: 501 },
    );
    assert_eq!(spell_errors(&mut o), vec![SpellErrorCode::BadTarget]);
    cast(
        &mut s,
        &o,
        t0,
        ELEVARA,
        SpellTarget::Furniture { item_id: 501 },
    );
    o.drain();
    cast(
        &mut s,
        &o,
        t0,
        DISSOLVO,
        SpellTarget::Furniture { item_id: 501 },
    );
    let msgs = o.drain();
    assert!(msgs.contains(&ServerMsg::Levitating {
        item_id: 501,
        by: o.id,
        duration_ms: 0,
    }));
    assert!(msgs.contains(&spell_cast(
        o.id,
        DISSOLVO,
        SpellTarget::Furniture { item_id: 501 }
    )));
    // Landed: Elevara lifts it again at once (after its cooldown).
    let later = t0 + Duration::from_secs(7);
    cast(
        &mut s,
        &o,
        later,
        ELEVARA,
        SpellTarget::Furniture { item_id: 501 },
    );
    assert_eq!(spell_errors(&mut o), vec![]);
}

#[test]
fn aperio_shows_the_caster_alone_and_volucra_is_only_its_cast() {
    let mut s = sim();
    // The test hall's find spot is at (120, 0, 100).
    let mut p = party_at(&mut s, [114.0, 0.0, 100.0], [116.0, 0.0, 100.0]);
    let t0 = Instant::now();
    let a = p.a.id;
    cast(&mut s, &p.a, t0, APERIO, SpellTarget::None);
    let msgs = p.a.drain();
    let revealed: Vec<_> = msgs
        .iter()
        .filter_map(|m| match m {
            ServerMsg::Revealed {
                spell,
                duration_ms,
                spots,
            } => Some((*spell, *duration_ms, spots.clone())),
            _ => None,
        })
        .collect();
    assert_eq!(revealed.len(), 1);
    let (spell, duration_ms, spots) = &revealed[0];
    assert_eq!((*spell, *duration_ms), (APERIO, 10_000));
    assert_eq!(spots.len(), 1);
    assert_eq!(spots[0].kind, protocol::RevealKind::FindSpot);
    assert_eq!(spots[0].pos, QPos::from_m([120.0, 0.0, 100.0]));
    // Others near see the cast, never the spots.
    let bm = p.b.drain();
    assert!(bm.contains(&spell_cast(a, APERIO, SpellTarget::None)));
    assert!(!bm.iter().any(|m| matches!(m, ServerMsg::Revealed { .. })));
    // 30 s before the next.
    cast(
        &mut s,
        &p.a,
        t0 + Duration::from_secs(10),
        APERIO,
        SpellTarget::None,
    );
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::Cooldown]);
    // Volucra: the cast and nothing else, for everyone near.
    cast(&mut s, &p.a, t0, VOLUCRA, ent(p.b.id));
    assert_eq!(spell_errors(&mut p.a), vec![SpellErrorCode::BadTarget]);
    cast(&mut s, &p.a, t0, VOLUCRA, SpellTarget::None);
    let am = p.a.drain();
    assert!(am.contains(&spell_cast(a, VOLUCRA, SpellTarget::None)));
    assert!(
        p.b.drain()
            .contains(&spell_cast(a, VOLUCRA, SpellTarget::None))
    );
    assert_eq!(kinds_of(&mut s, a), vec![]);
    // Nothing near: an empty list is still a cast.
    let mut s = sim();
    let mut q = party_at(&mut s, [60.0, 0.0, 160.0], [62.0, 0.0, 160.0]);
    cast(&mut s, &q.a, t0, APERIO, SpellTarget::None);
    assert!(q.a.drain().contains(&ServerMsg::Revealed {
        spell: APERIO,
        duration_ms: 10_000,
        spots: vec![],
    }));
}

/// A handler's step, run on the world thread.
type Step = Box<dyn FnOnce(&mut super::Spells, &mut WorldCtx<'_>) + Send>;

#[test]
fn social_spell_memory_stays_bounded() {
    let mut s = sim();
    let p = party(&mut s);
    let t0 = Instant::now();
    let (a, b) = (p.a.id, p.b.id);
    // A handler of our own, driven on the world thread at chosen times.
    let spells = Arc::new(Mutex::new(super::Spells::default()));
    let on = |s: &mut WorldSim, at: Instant, f: Step| {
        let sp = spells.clone();
        s.ext_done(
            ExtDone::Run(Box::new(move |ctx| f(&mut sp.lock().unwrap(), ctx))),
            at,
        );
    };
    let cast_in = |spell: u16, target: SpellTarget| -> Step {
        Box::new(move |sp, ctx| sp.on_input(ctx, a, SpellInput::Cast { spell, target }))
    };
    on(&mut s, t0, cast_in(RIGORA, ent(b)));
    on(&mut s, t0, cast_in(VOLUCRA, SpellTarget::None));
    // Refused casts leave nothing behind.
    on(&mut s, t0, cast_in(TARDARA, SpellTarget::None));
    // Two cooldowns and B's immunity.
    assert_eq!(spells.lock().unwrap().remembered(), 3);
    // B leaving forgets B's immunity; A leaving, A's cooldowns.
    on(&mut s, t0, Box::new(move |sp, ctx| sp.on_left(ctx, b)));
    assert_eq!(spells.lock().unwrap().remembered(), 2);
    on(&mut s, t0, Box::new(move |sp, ctx| sp.on_left(ctx, a)));
    assert_eq!(spells.lock().unwrap().remembered(), 0);
    // Time forgets the rest.
    on(&mut s, t0, cast_in(RIGORA, ent(b)));
    assert_eq!(spells.lock().unwrap().remembered(), 2);
    on(
        &mut s,
        t0 + Duration::from_secs(7),
        Box::new(|sp, ctx| sp.tick(ctx)),
    );
    assert_eq!(
        spells.lock().unwrap().remembered(),
        1,
        "the cooldown passed"
    );
    on(
        &mut s,
        t0 + Duration::from_secs(9),
        Box::new(|sp, ctx| sp.tick(ctx)),
    );
    assert_eq!(spells.lock().unwrap().remembered(), 0, "the immunity too");
}
