//! Custodio outside duels (GUARDIAN): the cast, its refresh, the
//! dismissal (`EndSpell`, a class, an arena), what viewers see, and the
//! refusals every social spell shares. On the world thread, no database.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use protocol::{
    EffectKind, EntityId, GuardianAnimal, House, LookCache, ServerMsg, SnapshotReceiver,
    SpellErrorCode, SpellTarget, VisibleEffect, Zone,
};
use tokio::sync::mpsc;

use crate::progression::SchoolState;
use crate::realtime::ConnControl;
use crate::realtime::ext::{ActiveEffect, ExtDone, FeatureKind, SpellInput, WorldCtx};
use crate::realtime::world::tests::{TestClient, character, join, sim};
use crate::realtime::world::{Input, Joining, WorldCmd, WorldSim};

const GUARDIAN: u16 = 18;
const DURATION: Duration = Duration::from_millis(60_000);
const COOLDOWN: Duration = Duration::from_millis(60_000);

/// A character in the castle who knows `learned`.
fn join_knowing(
    s: &mut WorldSim,
    name: &str,
    house: House,
    pos: [f32; 3],
    learned: Vec<u16>,
) -> TestClient {
    let mut joining = Joining::alone(character(name, house, Some(pos)));
    joining.dorm_open = true;
    joining.school = SchoolState {
        xp: 0,
        learned,
        loadout: Vec::new(),
        ..Default::default()
    };
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

fn run(s: &mut WorldSim, f: impl FnOnce(&mut WorldCtx<'_>) + Send + 'static) {
    s.handle(WorldCmd::ExtDone(ExtDone::Run(Box::new(f))));
}

fn cast(s: &mut WorldSim, c: &TestClient, at: Instant) {
    let input = SpellInput::Cast {
        spell: GUARDIAN,
        target: SpellTarget::None,
    };
    s.input(c.id, at, Input::Spell(input));
}

fn end(s: &mut WorldSim, c: &TestClient, at: Instant) {
    s.input(c.id, at, Input::Spell(SpellInput::End { spell: GUARDIAN }));
}

/// `id`'s Guardian effects (at most one is ever on).
fn guardians(s: &mut WorldSim, id: EntityId) -> Vec<ActiveEffect> {
    let out = Arc::new(Mutex::new(Vec::new()));
    let o = out.clone();
    run(s, move |ctx| {
        *o.lock().unwrap() = ctx
            .effects(id)
            .iter()
            .filter(|e| e.kind == EffectKind::Guardian)
            .copied()
            .collect();
    });
    out.lock().unwrap().clone()
}

/// `id`'s Guardian effect, if any.
fn guardian(s: &mut WorldSim, id: EntityId) -> Option<ActiveEffect> {
    guardians(s, id).into_iter().next()
}

fn spell_errors(msgs: &[ServerMsg]) -> Vec<SpellErrorCode> {
    msgs.iter()
        .filter_map(|m| match m {
            ServerMsg::SpellError { code, .. } => Some(*code),
            _ => None,
        })
        .collect()
}

fn casts(msgs: &[ServerMsg]) -> usize {
    msgs.iter()
        .filter(|m| {
            matches!(
                m,
                ServerMsg::SpellCast {
                    spell: GUARDIAN,
                    target: SpellTarget::None,
                    ..
                }
            )
        })
        .count()
}

/// The visible effects viewers were last told `id` shows.
fn seen(msgs: &[ServerMsg], id: EntityId) -> Option<Vec<VisibleEffect>> {
    msgs.iter().rev().find_map(|m| match m {
        ServerMsg::EffectsChanged {
            id: who, effects, ..
        } if *who == id => Some(effects.clone()),
        _ => None,
    })
}

fn open_and_enter(s: &mut WorldSim, id: EntityId, kind: FeatureKind, layout: &'static str) {
    run(s, move |ctx| {
        let zone = ctx.open_instance(kind, layout).expect("a known layout");
        assert!(ctx.enter_instance(id, zone, None));
    });
}

#[test]
fn a_cast_summons_the_casters_own_animal_and_viewers_see_it() {
    let mut s = sim();
    let mut a = join_knowing(
        &mut s,
        "Ada",
        House::Skytalon,
        [100.0, 0.0, 100.0],
        vec![GUARDIAN],
    );
    let mut b = join(&mut s, "Bea", House::Roarwing, [102.0, 0.0, 100.0]);
    s.tick(Instant::now());
    b.drain();
    let t0 = Instant::now();
    cast(&mut s, &a, t0);
    let fx = guardian(&mut s, a.id).expect("summoned");
    let animal = sim_core::guardian_animal(a.cid);
    assert_eq!(
        fx.visible,
        Some(VisibleEffect::Guardian {
            animal,
            guarding: false
        })
    );
    assert_eq!(fx.speed_pct, protocol::SPEED_PCT_NORMAL);
    assert_eq!(fx.until, t0 + DURATION);
    let mine = a.drain();
    assert!(spell_errors(&mine).is_empty(), "{mine:?}");
    assert_eq!(casts(&mine), 1);
    // A nearby player hears the cast and, from the next tick, sees the animal.
    s.tick(t0);
    let theirs = b.drain();
    assert_eq!(casts(&theirs), 1);
    assert_eq!(
        seen(&theirs, a.id),
        Some(vec![VisibleEffect::Guardian {
            animal,
            guarding: false
        }])
    );
    // Always the same kind for the same character.
    assert_eq!(animal, sim_core::guardian_animal(a.cid));
    assert!(GuardianAnimal::ALL.contains(&animal));
}

#[test]
fn it_expires_on_its_tick_and_viewers_see_it_go() {
    let mut s = sim();
    let a = join_knowing(
        &mut s,
        "Ada",
        House::Skytalon,
        [100.0, 0.0, 100.0],
        vec![GUARDIAN],
    );
    let mut b = join(&mut s, "Bea", House::Roarwing, [102.0, 0.0, 100.0]);
    let t0 = Instant::now();
    cast(&mut s, &a, t0);
    s.tick(t0);
    b.drain();
    s.tick(t0 + DURATION - Duration::from_millis(50));
    assert!(guardian(&mut s, a.id).is_some());
    s.tick(t0 + DURATION);
    assert!(guardian(&mut s, a.id).is_none());
    assert_eq!(seen(&b.drain(), a.id), Some(vec![]));
}

#[test]
fn a_recast_after_the_cooldown_refreshes_it() {
    let mut s = sim();
    let mut a = join_knowing(
        &mut s,
        "Ada",
        House::Skytalon,
        [100.0, 0.0, 100.0],
        vec![GUARDIAN],
    );
    let t0 = Instant::now();
    cast(&mut s, &a, t0);
    // Still cooling down.
    let t1 = t0 + Duration::from_secs(5);
    cast(&mut s, &a, t1);
    assert_eq!(spell_errors(&a.drain()), vec![SpellErrorCode::Cooldown]);
    assert_eq!(guardian(&mut s, a.id).unwrap().until, t0 + DURATION);
    // As it fades (no tick in between: still out), a recast refreshes it.
    let t2 = t0 + COOLDOWN;
    cast(&mut s, &a, t2);
    assert!(spell_errors(&a.drain()).is_empty());
    assert_eq!(guardian(&mut s, a.id).unwrap().until, t2 + DURATION);
    assert_eq!(guardians(&mut s, a.id).len(), 1);
}

#[test]
fn end_spell_dismisses_it_but_not_a_duel_ward() {
    let mut s = sim();
    let mut a = join_knowing(
        &mut s,
        "Ada",
        House::Skytalon,
        [100.0, 0.0, 100.0],
        vec![GUARDIAN],
    );
    let mut b = join(&mut s, "Bea", House::Roarwing, [102.0, 0.0, 100.0]);
    let t0 = Instant::now();
    cast(&mut s, &a, t0);
    s.tick(t0);
    b.drain();
    end(&mut s, &a, t0 + Duration::from_millis(500));
    assert!(guardian(&mut s, a.id).is_none());
    s.tick(t0 + Duration::from_millis(500));
    assert_eq!(seen(&b.drain(), a.id), Some(vec![]));
    // Ending one that is not out does nothing (and says nothing).
    end(&mut s, &a, t0 + Duration::from_millis(600));
    assert!(spell_errors(&a.drain()).is_empty());
    // A ward is the duel's: EndSpell leaves it.
    let id = a.id;
    let until = t0 + Duration::from_secs(10);
    run(&mut s, move |ctx| {
        ctx.apply_effect(
            id,
            ActiveEffect {
                kind: EffectKind::Guardian,
                item_def: 0,
                fortune: None,
                visible: Some(VisibleEffect::Guardian {
                    animal: GuardianAnimal::Owl,
                    guarding: true,
                }),
                speed_pct: protocol::SPEED_PCT_NORMAL,
                until,
            },
        );
    });
    end(&mut s, &a, t0 + Duration::from_millis(700));
    assert!(guardian(&mut s, a.id).is_some());
}

#[test]
fn entering_a_class_or_an_arena_dismisses_it() {
    let mut s = sim();
    let mut a = join_knowing(
        &mut s,
        "Ada",
        House::Skytalon,
        [100.0, 0.0, 100.0],
        vec![GUARDIAN],
    );
    let t0 = Instant::now();
    cast(&mut s, &a, t0);
    assert!(guardian(&mut s, a.id).is_some());
    // Into a class: gone; casting there is refused.
    let class = FeatureKind::Class {
        class: "charms".into(),
        section: 0,
    };
    open_and_enter(&mut s, a.id, class, "class_charms");
    assert!(guardian(&mut s, a.id).is_none());
    a.drain();
    cast(&mut s, &a, t0 + COOLDOWN);
    assert_eq!(spell_errors(&a.drain()), vec![SpellErrorCode::NotHere]);
    assert!(guardian(&mut s, a.id).is_none());
    // Back out in the castle it may be cast again (the refusal started no
    // cooldown), and walking into an arena dismisses it.
    s.input(a.id, t0 + COOLDOWN, Input::LeaveInstance);
    cast(&mut s, &a, t0 + COOLDOWN);
    assert!(spell_errors(&a.drain()).is_empty());
    assert!(guardian(&mut s, a.id).is_some());
    let arena = FeatureKind::Arena {
        duel_id: 1,
        venue: "dueling_club".into(),
    };
    open_and_enter(&mut s, a.id, arena, "duel_arena");
    assert!(guardian(&mut s, a.id).is_none());
    // Arenas refuse social casts outright.
    cast(&mut s, &a, t0 + COOLDOWN * 2);
    assert_eq!(spell_errors(&a.drain()), vec![SpellErrorCode::NotHere]);
}

#[test]
fn it_follows_its_caster_into_a_dorm_room() {
    let mut s = sim();
    let mut a = join_knowing(
        &mut s,
        "Ada",
        House::Roarwing,
        [214.0, 0.0, 100.0],
        vec![GUARDIAN],
    );
    let t0 = Instant::now();
    cast(&mut s, &a, t0);
    a.drain();
    s.input(a.id, t0, Input::Interact("dorm_door_r".into()));
    assert!(a.drain().iter().any(|m| matches!(
        m,
        ServerMsg::ZoneChanged {
            zone: Zone::Dorm { .. },
            ..
        }
    )));
    assert!(guardian(&mut s, a.id).is_some());
}

#[test]
fn refusals_match_the_other_spells() {
    let mut s = sim();
    // Not learned.
    let mut n = join_knowing(&mut s, "Nev", House::Skytalon, [100.0, 0.0, 100.0], vec![]);
    cast(&mut s, &n, Instant::now());
    assert_eq!(spell_errors(&n.drain()), vec![SpellErrorCode::NotLearned]);
    assert!(guardian(&mut s, n.id).is_none());
    // A refused cast starts no cooldown; a burst of casts is rate limited.
    let mut a = join_knowing(
        &mut s,
        "Ada",
        House::Skytalon,
        [104.0, 0.0, 100.0],
        vec![GUARDIAN],
    );
    let t0 = Instant::now();
    for _ in 0..40 {
        cast(&mut s, &a, t0);
    }
    let errs = spell_errors(&a.drain());
    assert!(errs.contains(&SpellErrorCode::Cooldown), "{errs:?}");
    assert!(errs.contains(&SpellErrorCode::RateLimited), "{errs:?}");
    assert!(guardian(&mut s, a.id).is_some());
}
