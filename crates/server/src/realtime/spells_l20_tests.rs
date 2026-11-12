//! Spells to level 20 (protocol v11): the realtime core's new primitives,
//! driven without sockets or a database: hindrance speed (no grace, every
//! zone), spell effects ending on their tick, forced moves (Adveni) and the
//! spell-target queries.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use protocol::{EffectKind, House, QPos, RevealKind, ServerMsg, VisibleEffect};

use super::*;
use crate::realtime::world::tests::{TestClient, join, sim};
use crate::realtime::world::{FORCED_TAIL, Input, WorldCmd, WorldSim};

fn run(s: &mut WorldSim, f: impl FnOnce(&mut WorldCtx<'_>) + Send + 'static) {
    s.handle(WorldCmd::ExtDone(ExtDone::Run(Box::new(f))));
}

fn mv(s: &mut WorldSim, c: &TestClient, at: Instant, seq: u32, pos: [f32; 3]) {
    let input = Input::Move {
        seq,
        pos,
        yaw: 0.0,
        anim: Anim::Walk,
    };
    s.input(c.id, at, input);
}

fn hindrance(kind: EffectKind, pct: u16, until: Instant) -> ActiveEffect {
    let visible = match kind {
        EffectKind::Petrified => VisibleEffect::Bound,
        _ => VisibleEffect::Slowed { pct: pct as u8 },
    };
    ActiveEffect {
        kind,
        item_def: 0,
        fortune: None,
        visible: Some(visible),
        speed_pct: pct,
        until,
    }
}

fn own_speed(msgs: &[ServerMsg]) -> Option<u16> {
    msgs.iter().rev().find_map(|m| match m {
        ServerMsg::OwnEffects { speed_pct, .. } => Some(*speed_pct),
        _ => None,
    })
}

fn corrections(msgs: &[ServerMsg]) -> usize {
    msgs.iter()
        .filter(|m| matches!(m, ServerMsg::Correction { .. }))
        .count()
}

#[test]
fn petrified_stops_moves_at_once_and_ends_on_its_tick() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
    let t0 = Instant::now();
    s.tick(t0);
    a.drain();
    let (id, i) = (a.id, s.ents.index_of(a.id).unwrap());
    let until = t0 + Duration::from_millis(1_200);
    run(&mut s, move |ctx| {
        ctx.apply_effect(id, hindrance(EffectKind::Petrified, 0, until));
    });
    assert_eq!(own_speed(&a.drain()), Some(0));
    // No grace: a step taken just now is refused and corrected.
    let at = t0 + Duration::from_millis(100);
    assert_eq!(s.move_speed_pct(i, at), 0);
    mv(
        &mut s,
        &a,
        at + Duration::from_secs(1),
        1,
        [101.0, 0.0, 100.0],
    );
    assert_eq!(s.ents.pos[i], [100.0, 0.0, 100.0]);
    assert_eq!(corrections(&a.drain()), 1);
    // It ends on the tick it expires (not at the next whole second).
    s.tick(until - Duration::from_millis(10));
    assert!(
        s.ents.effects[i]
            .active
            .iter()
            .any(|e| e.kind == EffectKind::Petrified)
    );
    s.tick(until + Duration::from_millis(1));
    assert!(s.ents.effects[i].active.is_empty());
    assert_eq!(own_speed(&a.drain()), Some(100));
    // Walking again (the credit came back with time).
    let later = until + Duration::from_secs(1);
    mv(&mut s, &a, later, 2, [101.0, 0.0, 100.0]);
    assert_eq!(s.ents.pos[i], [101.0, 0.0, 100.0]);
}

/// Only a new or stronger hindrance takes the speed grace away: a slowed
/// character losing a boost (or any other effect changing) keeps the
/// grace a speed drop always gets.
#[test]
fn only_a_new_or_stronger_hindrance_drops_the_speed_grace() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Roarwing, [100.0, 0.0, 100.0]);
    let t0 = Instant::now();
    s.tick(t0);
    a.drain();
    let (id, i) = (a.id, s.ents.index_of(a.id).unwrap());
    let long = t0 + Duration::from_secs(600);
    let pick_me_up_tonic = ActiveEffect {
        fortune: None,
        kind: EffectKind::PickMeUpTonic,
        item_def: 3309,
        visible: Some(VisibleEffect::Steam),
        speed_pct: 115,
        until: long,
    };
    run(&mut s, move |ctx| {
        ctx.apply_effect(id, pick_me_up_tonic);
        ctx.apply_effect(id, hindrance(EffectKind::Slowed, 40, long));
    });
    assert_eq!(own_speed(&a.drain()), Some(46));
    // The slow came on: no grace.
    let at = t0 + Duration::from_millis(50);
    assert_eq!(s.move_speed_pct(i, at), 46);
    // Slowed, the boost ends: 46 -> 40, and moves in flight at 46 pass.
    run(&mut s, move |ctx| {
        ctx.remove_effect(id, EffectKind::PickMeUpTonic)
    });
    assert_eq!(own_speed(&a.drain()), Some(40));
    assert_eq!(s.move_speed_pct(i, at), 46);
    // Something unrelated changes while the grace runs: it stays.
    run(&mut s, move |ctx| ctx.set_lucerna(id, true));
    assert_eq!(s.move_speed_pct(i, at), 46);
    // A weaker slow replacing it: faster, nothing to forgive.
    run(&mut s, move |ctx| {
        ctx.apply_effect(id, hindrance(EffectKind::Slowed, 60, long));
    });
    assert_eq!(own_speed(&a.drain()), Some(60));
    // A stronger hindrance on top: the grace goes at once.
    run(&mut s, move |ctx| {
        ctx.apply_effect(id, hindrance(EffectKind::Petrified, 0, long));
    });
    assert_eq!(own_speed(&a.drain()), Some(0));
    assert_eq!(s.move_speed_pct(i, at), 0);
}

#[test]
fn hindrances_multiply_boosts_and_the_arena_speed() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Serpentyne, [100.0, 0.0, 100.0]);
    let id = a.id;
    let until = Instant::now() + Duration::from_secs(600);
    run(&mut s, move |ctx| {
        ctx.apply_effect(
            id,
            ActiveEffect {
                fortune: None,
                kind: EffectKind::PickMeUpTonic,
                item_def: 3309,
                visible: Some(VisibleEffect::Steam),
                speed_pct: 115,
                until,
            },
        );
        ctx.apply_effect(id, hindrance(EffectKind::Slowed, 40, until));
    });
    // Outside: the boost times the slow (115 x 40 %).
    assert_eq!(own_speed(&a.drain()), Some(46));
    // In an arena: its walking speed times the slow.
    run(&mut s, move |ctx| {
        let kind = FeatureKind::Arena {
            duel_id: 1,
            venue: "dueling_club".into(),
        };
        let zone = ctx.open_instance(kind, "duel_arena").unwrap();
        assert!(ctx.enter_instance(id, zone, Some(([-5.5, 0.9, 0.0], 1.5707964))));
    });
    // (Entering took the old slow off: zone changes end hindrances.)
    run(&mut s, move |ctx| {
        assert!(!ctx.has_effect(id, EffectKind::Slowed));
        ctx.apply_effect(id, hindrance(EffectKind::Slowed, 50, until));
    });
    let rules = crate::catalog::shipped().duel_rules.move_speed_pct;
    assert_eq!(own_speed(&a.drain()), Some(rules * 50 / 100));
    // The slowest hindrance wins.
    run(&mut s, move |ctx| {
        ctx.apply_effect(id, hindrance(EffectKind::Petrified, 0, until));
    });
    assert_eq!(own_speed(&a.drain()), Some(0));
}

#[test]
fn a_zone_change_ends_hindrances_and_forced_moves_but_not_recolours() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Serpentyne, [100.0, 0.0, 100.0]);
    let id = a.id;
    let until = Instant::now() + Duration::from_secs(600);
    run(&mut s, move |ctx| {
        let kind = FeatureKind::Arena {
            duel_id: 1,
            venue: "dueling_club".into(),
        };
        let zone = ctx.open_instance(kind, "duel_arena").unwrap();
        assert!(ctx.enter_instance(id, zone, Some(([-5.5, 0.9, 0.0], 1.5707964))));
    });
    run(&mut s, move |ctx| {
        ctx.apply_effect(id, hindrance(EffectKind::Petrified, 0, until));
        ctx.apply_effect(id, hindrance(EffectKind::Slowed, 50, until));
        ctx.apply_effect(
            id,
            ActiveEffect {
                kind: EffectKind::Recoloured,
                item_def: 0,
                fortune: None,
                visible: Some(VisibleEffect::Recoloured { colour: 21 }),
                speed_pct: 100,
                until,
            },
        );
        assert!(ctx.force_move(id, [-4.0, 0.9, 0.0], 300, 500, id));
        assert!(ctx.being_moved(id));
    });
    a.drain();
    s.input(id, Instant::now(), Input::LeaveInstance);
    let i = s.ents.index_of(id).unwrap();
    let kinds: Vec<EffectKind> = s.ents.effects[i].active.iter().map(|e| e.kind).collect();
    assert_eq!(kinds, vec![EffectKind::Recoloured]);
    assert!(s.ents.mover[i].forced.is_none());
    assert_eq!(own_speed(&a.drain()), Some(100));
}

#[test]
fn forced_moves_slide_hold_and_drop_the_clients_moves() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, [100.0, 0.0, 100.0]);
    let b = join(&mut s, "Bea", House::Skytalon, [97.0, 0.0, 100.0]);
    let t0 = Instant::now();
    s.tick(t0);
    a.drain();
    let (id, by, i) = (a.id, b.id, s.ents.index_of(a.id).unwrap());
    let out = Arc::new(Mutex::new(None));
    let o = out.clone();
    run(&mut s, move |ctx| {
        let to = ctx.pull_destination(id, by, 2.5, 1.5, 1.2);
        *o.lock().unwrap() = Some(to);
        let to = to.unwrap();
        assert!(ctx.force_move(id, to, 350, 450, by));
        // Not twice at once; not an unknown entity.
        assert!(!ctx.force_move(id, to, 350, 450, by));
        assert!(!ctx.spell_target_free(id), "being moved");
        assert!(ctx.spell_target_free(by));
    });
    let to = out.lock().unwrap().unwrap().unwrap();
    assert!((to[0] - 98.2).abs() < 1e-3 && to[2] == 100.0, "{to:?}");
    let msgs = a.drain();
    assert!(msgs.contains(&ServerMsg::ForcedMove {
        to: QPos::from_m(to),
        move_ms: 350,
        hold_ms: 450,
        by,
    }));
    let start = s.ents.mover[i].forced.unwrap().start;
    // Halfway through the slide: halfway there (snapshots carry it).
    s.tick(start + Duration::from_millis(175));
    let x = s.ents.pos[i][0];
    assert!((x - (100.0 + (to[0] - 100.0) * 0.5)).abs() < 0.01, "{x}");
    assert_eq!(s.ents.state[i].anim, Anim::Idle);
    // The client's moves are dropped meanwhile, never corrected.
    mv(
        &mut s,
        &a,
        start + Duration::from_millis(200),
        1,
        [103.0, 0.0, 100.0],
    );
    assert!((s.ents.pos[i][0] - x).abs() < 1e-4);
    s.tick(start + Duration::from_millis(400));
    assert_eq!(s.ents.pos[i], to);
    mv(
        &mut s,
        &a,
        start + Duration::from_millis(500),
        2,
        [103.0, 0.0, 100.0],
    );
    assert_eq!(s.ents.pos[i], to);
    assert_eq!(corrections(&a.drain()), 0);
    // Until the hold and its tail are over.
    s.tick(start + Duration::from_millis(450) + FORCED_TAIL - Duration::from_millis(1));
    assert!(s.ents.mover[i].forced.is_some());
    s.tick(start + Duration::from_millis(450) + FORCED_TAIL);
    assert!(s.ents.mover[i].forced.is_none());
    let walk = [to[0] + 0.5, 0.0, 100.0];
    mv(&mut s, &a, start + Duration::from_millis(800), 3, walk);
    assert_eq!(s.ents.pos[i], walk, "walking from where it was put");
}

#[test]
fn pulls_refuse_ledges_and_nobody_is_moved_off_the_ground() {
    let mut s = sim();
    // The test hall has a gallery 6 m up over its corner (0..50).
    let a = join(&mut s, "Ada", House::Skytalon, [49.0, 6.0, 20.0]);
    let b = join(&mut s, "Bea", House::Skytalon, [56.0, 0.0, 20.0]);
    let (id, by) = (a.id, b.id);
    run(&mut s, move |ctx| {
        assert_eq!(
            ctx.pull_destination(id, by, 2.5, 1.5, 1.2),
            Err(sim_core::PullBlocked::Ledge)
        );
        assert!(!ctx.force_move(by, [500.0, 0.0, 500.0], 300, 300, id));
        assert!(!ctx.being_moved(by));
        assert_eq!(
            ctx.pull_destination(id, 0xdead, 2.5, 1.5, 1.2),
            Err(sim_core::PullBlocked::Wall)
        );
    });
}

#[test]
fn aperio_spots_and_target_queries() {
    let mut s = sim();
    let a = join(&mut s, "Ada", House::Roarwing, [118.0, 0.0, 100.0]);
    let id = a.id;
    run(&mut s, move |ctx| {
        // The test hall's find spot at (120, 0, 100); no passages here.
        let spots = ctx.reveal_near(id, 20.0, 4.0);
        assert_eq!(spots.len(), 1);
        assert_eq!(spots[0].kind, RevealKind::FindSpot);
        assert_eq!(spots[0].pos, QPos::from_m([120.0, 0.0, 100.0]));
        assert!(ctx.reveal_near(id, 1.0, 4.0).is_empty());
        assert_eq!(ctx.anim(id), Some(Anim::Idle));
        assert!(!ctx.trading(id) && !ctx.dueling(id) && !ctx.in_class(id));
        assert!(ctx.spell_target_free(id));
    });
}

fn mount_reply(msgs: &[ServerMsg]) -> Option<(bool, Option<protocol::MountRefused>)> {
    msgs.iter().rev().find_map(|m| match m {
        ServerMsg::Mounted { on, reason, .. } => Some((*on, *reason)),
        _ => None,
    })
}

#[test]
fn nobody_mounts_while_pulled_or_bound() {
    use protocol::MountRefused;
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, [100.0, 0.0, 100.0]);
    let b = join(&mut s, "Bea", House::Skytalon, [97.0, 0.0, 100.0]);
    let t0 = Instant::now();
    s.tick(t0);
    a.drain();
    let (id, by, i) = (a.id, b.id, s.ents.index_of(a.id).unwrap());
    s.ents.mover[i].flying_xp = 1_000;
    // Being pulled (the slide, the hold and its tail): busy.
    run(&mut s, move |ctx| {
        let to = ctx.pull_destination(id, by, 2.5, 1.5, 1.2).unwrap();
        assert!(ctx.force_move(id, to, 350, 450, by));
    });
    let start = s.ents.mover[i].forced.unwrap().start;
    for ms in [100, 600] {
        s.input(a.id, start + Duration::from_millis(ms), Input::Mount(true));
        assert_eq!(
            mount_reply(&a.drain()),
            Some((false, Some(MountRefused::Busy))),
            "{ms} ms into the pull"
        );
        assert!(s.ents.mover[i].mount.is_none());
    }
    let after = start + Duration::from_millis(450) + FORCED_TAIL;
    s.tick(after);
    assert!(s.ents.mover[i].forced.is_none());
    // Bound: busy too.
    let until = after + Duration::from_secs(2);
    run(&mut s, move |ctx| {
        ctx.apply_effect(id, hindrance(EffectKind::Petrified, 0, until));
    });
    a.drain();
    s.input(a.id, after + Duration::from_secs(1), Input::Mount(true));
    assert_eq!(
        mount_reply(&a.drain()),
        Some((false, Some(MountRefused::Busy)))
    );
    assert!(s.ents.mover[i].mount.is_none());
    // Once free, Busy is no longer the answer.
    s.tick(until + Duration::from_millis(1));
    a.drain();
    s.input(a.id, until + Duration::from_secs(2), Input::Mount(true));
    let reply = mount_reply(&a.drain());
    assert!(
        reply.is_some_and(|(_, r)| r != Some(MountRefused::Busy)),
        "{reply:?}"
    );
}

#[test]
fn a_forced_move_ends_once_its_target_rides() {
    let mut s = sim();
    let a = join(&mut s, "Ada", House::Skytalon, [100.0, 0.0, 100.0]);
    let b = join(&mut s, "Bea", House::Skytalon, [97.0, 0.0, 100.0]);
    let t0 = Instant::now();
    s.tick(t0);
    let (id, by, i) = (a.id, b.id, s.ents.index_of(a.id).unwrap());
    run(&mut s, move |ctx| {
        let to = ctx.pull_destination(id, by, 2.5, 1.5, 1.2).unwrap();
        assert!(ctx.force_move(id, to, 350, 450, by));
    });
    let start = s.ents.mover[i].forced.unwrap().start;
    // However it got on a broom, the slide no longer drives a rider.
    s.ents.mover[i].mount = Some(super::super::entities::Mount { tier: 1 });
    let lifted = [100.0, 3.0, 100.0];
    s.ents.pos[i] = lifted;
    s.tick(start + Duration::from_millis(175));
    assert!(s.ents.mover[i].forced.is_none());
    assert_eq!(s.ents.pos[i], lifted);
}
