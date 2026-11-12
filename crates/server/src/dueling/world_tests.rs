//! Tests of the dueling handler on the world thread, driven directly
//! without sockets or a database: the challenge rules (the club area,
//! blocks, busy players, two challenges at once), the arena and its duel
//! loop, forfeits, disconnects and their grace, spectators (who see
//! everything and change nothing), the Dueling Club's lessons, loadouts at
//! the start, and the cost of many duels at once. Database jobs are not
//! run here: their results are handed back as the jobs would (the record
//! itself is tested against Postgres in `tests/dueling.rs`).

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use protocol::{
    Anim, ChallengeEnd, DuelErrorCode, DuelEvent, DuelRewards, DuelRole, DuelSide, EffectKind,
    House, LookCache, PotionErrorCode, ServerMsg, SnapshotReceiver, SpellId, XpSource, Zone,
};
use tokio::sync::mpsc;

use super::*;
use crate::characters::Character;
use crate::metrics::Registry;
use crate::progression::{SchoolState, XpGrant};
use crate::realtime::ConnControl;
use crate::realtime::chat::WordFilter;
use crate::realtime::entities::Social;
use crate::realtime::ext::PotionInput;
use crate::realtime::metrics::RealtimeMetrics;
use crate::realtime::world::tests::{TestClient, character};
use crate::realtime::world::{Input, Joining, TICK, WorldCmd, WorldSim};

/// A 200 m hall with the Dueling Club's area (the shipped venue's area id)
/// in one part of it.
const LAYOUT: &str = r#"{
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

const SCUTARA: SpellId = 10;
const TORPEO: SpellId = 11;
const DIMITTO: SpellId = 12;
const TITILLARA: SpellId = 13;
const REPULSA: SpellId = 14;
/// What the club teaches at level 1.
const DUELING: &[SpellId] = &[SCUTARA, TORPEO, DIMITTO];
const CLUB: [f32; 3] = [150.0, 0.0, 100.0];
const OUTSIDE: [f32; 3] = [100.0, 0.0, 100.0];

fn sim() -> WorldSim {
    WorldSim::new(
        Arc::new(sim_core::World::from_json(LAYOUT).unwrap()),
        WordFilter::from_list(""),
        None,
        Arc::new(RealtimeMetrics::register(&Registry::default())),
        1,
    )
}

/// Joins `ch` with `learned` spells (the default loadout) and `social`.
fn join_as(
    s: &mut WorldSim,
    ch: Character,
    social: Social,
    learned: &[SpellId],
    queue: usize,
) -> TestClient {
    let (tx, rx) = mpsc::channel(queue);
    let control = Arc::new(ConnControl::default());
    let cid = ch.id;
    let content = shipped();
    let loadout = content
        .spells
        .default_loadout(learned, content.progression.loadout_slots(1));
    let joining = Joining {
        social: social.normalized(),
        school: SchoolState {
            xp: 0,
            learned: learned.to_vec(),
            loadout,
            ..Default::default()
        },
        ..Joining::alone(ch)
    };
    let id = s
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

fn player(s: &mut WorldSim, name: &str, pos: [f32; 3], learned: &[SpellId]) -> TestClient {
    let ch = character(name, House::Roarwing, Some(pos));
    join_as(s, ch, Social::default(), learned, 1024)
}

fn run(s: &mut WorldSim, f: impl FnOnce(&mut WorldCtx<'_>) + Send + 'static) {
    s.handle(WorldCmd::ExtDone(ExtDone::Run(Box::new(f))));
}

/// Something read from the world through a context.
fn read<T: Send + 'static>(
    s: &mut WorldSim,
    f: impl FnOnce(&mut WorldCtx<'_>) -> T + Send + 'static,
) -> T {
    let out = Arc::new(Mutex::new(None));
    let o = out.clone();
    run(s, move |ctx| *o.lock().unwrap() = Some(f(ctx)));
    out.lock().unwrap().take().unwrap()
}

fn zone_of(s: &mut WorldSim, e: EntityId) -> Option<ZoneId> {
    read(s, move |ctx| ctx.zone(e))
}

fn send(s: &mut WorldSim, c: &TestClient, at: Instant, msg: DuelMsg) {
    s.input(c.id, at, Input::Duel(msg));
}

/// `n` world ticks, `at` advancing by one tick each.
fn ticks(s: &mut WorldSim, at: &mut Instant, n: u32) {
    for _ in 0..n {
        *at += TICK;
        s.tick(*at);
    }
}

fn errors(msgs: &[ServerMsg]) -> Vec<DuelErrorCode> {
    msgs.iter()
        .filter_map(|m| match m {
            ServerMsg::DuelError { code, .. } => Some(*code),
            _ => None,
        })
        .collect()
}

fn challenge_of(msgs: &[ServerMsg]) -> Option<(u32, EntityId, u8)> {
    msgs.iter().find_map(|m| match m {
        ServerMsg::DuelChallenged {
            challenge_id,
            from,
            from_level,
            ..
        } => Some((*challenge_id, *from, *from_level)),
        _ => None,
    })
}

fn endings(msgs: &[ServerMsg]) -> Vec<ChallengeEnd> {
    msgs.iter()
        .filter_map(|m| match m {
            ServerMsg::DuelChallengeEnded { reason, .. } => Some(*reason),
            _ => None,
        })
        .collect()
}

/// A `DuelStart` in short: `(duel_id, you, a's loadout, b's loadout, at_ms)`.
type Start = (u32, DuelRole, Vec<SpellId>, Vec<SpellId>, u32);

fn starts(msgs: &[ServerMsg]) -> Vec<Start> {
    msgs.iter()
        .filter_map(|m| match m {
            ServerMsg::DuelStart {
                duel_id,
                you,
                a,
                b,
                state,
                ..
            } => Some((
                *duel_id,
                *you,
                a.loadout.clone(),
                b.loadout.clone(),
                state.at_ms,
            )),
            _ => None,
        })
        .collect()
}

/// Every `DuelEvents` frame: `(at_ms, events)`.
fn frames(msgs: &[ServerMsg]) -> Vec<(u32, Vec<DuelEvent>)> {
    msgs.iter()
        .filter_map(|m| match m {
            ServerMsg::DuelEvents { at_ms, events, .. } => Some((*at_ms, events.clone())),
            _ => None,
        })
        .collect()
}

fn events(msgs: &[ServerMsg]) -> Vec<DuelEvent> {
    frames(msgs).into_iter().flat_map(|f| f.1).collect()
}

fn ended(events: &[DuelEvent]) -> Option<(Option<DuelSide>, DuelEndReason)> {
    events.iter().find_map(|e| match e {
        DuelEvent::Ended { winner, reason } => Some((*winner, *reason)),
        _ => None,
    })
}

fn in_castle(msgs: &[ServerMsg]) -> bool {
    msgs.iter().any(|m| {
        matches!(
            m,
            ServerMsg::ZoneChanged {
                zone: Zone::Castle,
                ..
            }
        )
    })
}

/// A challenges B and B accepts. Returns the duel id and the arena zone;
/// both clients are drained.
fn start(s: &mut WorldSim, a: &mut TestClient, b: &mut TestClient, at: Instant) -> (u32, ZoneId) {
    send(s, a, at, DuelMsg::Challenge { target: b.id });
    s.tick(at);
    let (challenge_id, _, _) = challenge_of(&b.drain()).expect("challenged");
    send(
        s,
        b,
        at,
        DuelMsg::Respond {
            challenge_id,
            accept: true,
        },
    );
    let got = starts(&a.drain());
    assert_eq!(got.len(), 1, "a DuelStart");
    b.drain();
    let zone = zone_of(s, a.id).unwrap();
    (got[0].0, zone)
}

/// Hands back the record job's result as `dueling::record` would answer.
fn recorded(
    s: &mut WorldSim,
    duel_id: u32,
    zone: ZoneId,
    clients: [&TestClient; 2],
    (winner, reason): (Option<DuelSide>, DuelEndReason),
    rewards: [DuelRewards; 2],
    at: Instant,
) {
    let grant = |side: DuelSide| {
        let r = rewards[side.index()];
        XpGrant {
            source: crate::dueling::xp_for(winner, side).0,
            requested: r.xp,
            granted: r.xp,
            capped: r.capped,
            duplicate: false,
            xp: r.xp,
            level_before: 1,
            level: 1,
        }
    };
    let finished = Finished {
        duel_key: 1,
        venue: "dueling_club".into(),
        characters: [clients[0].cid, clients[1].cid],
        winner,
        reason,
        poise: [100, 0],
        fought_ms: 20_000,
        started_at: Utc::now(),
        ended_at: Utc::now(),
    };
    let done = DuelDone::Recorded {
        duel_id,
        zone,
        finished,
        result: Ok(Recorded {
            id: 7,
            duplicate: false,
            rewards,
            grants: [Some(grant(DuelSide::A)), Some(grant(DuelSide::B))],
        }),
    };
    s.ext_done(ExtDone::Duel(done), at);
}

#[test]
fn challenges_need_two_free_players_in_the_club() {
    let mut s = sim();
    let mut a = player(&mut s, "Ada", CLUB, DUELING);
    let mut b = player(&mut s, "Bea", CLUB, DUELING);
    let mut c = player(&mut s, "Cy", OUTSIDE, DUELING);
    let mut d = player(&mut s, "Dot", CLUB, DUELING);
    let mut n = player(&mut s, "Nia", CLUB, &[]);
    let at = Instant::now();
    s.tick(at);
    for x in [&mut a, &mut b, &mut c, &mut d, &mut n] {
        x.drain();
    }

    // Outside the club; at someone outside; at yourself; at nobody; with
    // no dueling spells yet.
    send(&mut s, &c, at, DuelMsg::Challenge { target: a.id });
    assert_eq!(errors(&c.drain()), vec![DuelErrorCode::NotInArea]);
    for (target, code) in [
        (c.id, DuelErrorCode::TargetNotInArea),
        (a.id, DuelErrorCode::SelfDuel),
        (99_999, DuelErrorCode::UnknownTarget),
    ] {
        send(&mut s, &a, at, DuelMsg::Challenge { target });
        assert_eq!(errors(&a.drain()), vec![code]);
    }
    send(&mut s, &n, at, DuelMsg::Challenge { target: a.id });
    assert_eq!(errors(&n.drain()), vec![DuelErrorCode::NoSpells]);
    send(
        &mut s,
        &a,
        at,
        DuelMsg::ChallengeName {
            name: "Nobody".into(),
        },
    );
    assert_eq!(errors(&a.drain()), vec![DuelErrorCode::UnknownTarget]);

    // `/duel bea`: the target hears of it (a notice, with the next tick),
    // the challenger gets the id back.
    send(
        &mut s,
        &a,
        at,
        DuelMsg::ChallengeName { name: "bea".into() },
    );
    let echo = challenge_of(&a.drain()).expect("echoed");
    assert_eq!(echo.1, a.id, "the echo names the challenger");
    s.tick(at);
    let got = challenge_of(&b.drain()).expect("challenged");
    assert_eq!(got, (echo.0, a.id, 1));

    // One challenge at a time, on either side.
    send(&mut s, &d, at, DuelMsg::Challenge { target: b.id });
    send(&mut s, &d, at, DuelMsg::Challenge { target: a.id });
    assert_eq!(
        errors(&d.drain()),
        vec![DuelErrorCode::Busy, DuelErrorCode::Busy]
    );
    send(&mut s, &a, at, DuelMsg::Challenge { target: d.id });
    assert_eq!(errors(&a.drain()), vec![DuelErrorCode::Busy]);
    // Only the target answers, only the challenger cancels.
    send(
        &mut s,
        &d,
        at,
        DuelMsg::Respond {
            challenge_id: echo.0,
            accept: true,
        },
    );
    send(
        &mut s,
        &b,
        at,
        DuelMsg::Cancel {
            challenge_id: echo.0,
        },
    );
    assert_eq!(errors(&d.drain()), vec![DuelErrorCode::UnknownChallenge]);
    assert_eq!(errors(&b.drain()), vec![DuelErrorCode::UnknownChallenge]);
    // The challenged one has no spells: the challenge waits for them.
    send(&mut s, &d, at, DuelMsg::Challenge { target: n.id });
    s.tick(at);
    let (id, _, _) = challenge_of(&n.drain()).unwrap();
    send(
        &mut s,
        &n,
        at,
        DuelMsg::Respond {
            challenge_id: id,
            accept: true,
        },
    );
    assert_eq!(errors(&n.drain()), vec![DuelErrorCode::NoSpells]);
    assert_eq!(
        read(&mut s, |ctx| ctx.zone(ctx.entity_by_name("Dot").unwrap())),
        Some(CASTLE)
    );
}

#[test]
fn blocked_players_cannot_challenge_each_other() {
    let mut s = sim();
    let a_ch = character("Ada", House::Serpentyne, Some(CLUB));
    let b_ch = character("Bea", House::Serpentyne, Some(CLUB));
    let blocks = Social {
        blocked: vec![b_ch.id],
        ..Social::default()
    };
    let blocked_by = Social {
        blocked_by: vec![a_ch.id],
        ..Social::default()
    };
    let mut a = join_as(&mut s, a_ch, blocks, DUELING, 256);
    let mut b = join_as(&mut s, b_ch, blocked_by, DUELING, 256);
    let at = Instant::now();
    a.drain();
    b.drain();
    send(&mut s, &a, at, DuelMsg::Challenge { target: b.id });
    send(
        &mut s,
        &b,
        at,
        DuelMsg::ChallengeName { name: "Ada".into() },
    );
    s.tick(at);
    // Either way it looks like nobody is there (blocks stay private).
    assert_eq!(errors(&a.drain()), vec![DuelErrorCode::UnknownTarget]);
    assert_eq!(errors(&b.drain()), vec![DuelErrorCode::UnknownTarget]);
}

#[test]
fn challenges_end_by_decline_cancel_expiry_walking_away_or_leaving() {
    let mut s = sim();
    let mut a = player(&mut s, "Ada", CLUB, DUELING);
    let mut b = player(&mut s, "Bea", CLUB, DUELING);
    let mut at = Instant::now();
    s.tick(at);
    a.drain();
    b.drain();
    let challenge = |s: &mut WorldSim, a: &mut TestClient, b: &mut TestClient, at| {
        send(s, a, at, DuelMsg::Challenge { target: b.id });
        s.tick(at);
        b.drain();
        challenge_of(&a.drain()).unwrap().0
    };
    let both_ended = |s: &mut WorldSim, a: &mut TestClient, b: &mut TestClient, at| {
        s.tick(at);
        (endings(&a.drain()), endings(&b.drain()))
    };

    let id = challenge(&mut s, &mut a, &mut b, at);
    send(
        &mut s,
        &b,
        at,
        DuelMsg::Respond {
            challenge_id: id,
            accept: false,
        },
    );
    let declined = vec![ChallengeEnd::Declined];
    assert_eq!(
        both_ended(&mut s, &mut a, &mut b, at),
        (declined.clone(), declined)
    );

    let id = challenge(&mut s, &mut a, &mut b, at);
    send(&mut s, &a, at, DuelMsg::Cancel { challenge_id: id });
    let cancelled = vec![ChallengeEnd::Cancelled];
    assert_eq!(
        both_ended(&mut s, &mut a, &mut b, at),
        (cancelled.clone(), cancelled)
    );

    // Nobody answers: it expires (checked once a second); a late answer
    // finds nothing.
    let id = challenge(&mut s, &mut a, &mut b, at);
    let secs = shipped().duel_rules.challenge_secs;
    ticks(&mut s, &mut at, u32::from(secs) * 20 + 25);
    let expired = vec![ChallengeEnd::Expired];
    assert_eq!(endings(&a.drain()), expired);
    assert_eq!(endings(&b.drain()), expired);
    send(
        &mut s,
        &b,
        at,
        DuelMsg::Respond {
            challenge_id: id,
            accept: true,
        },
    );
    assert_eq!(errors(&b.drain()), vec![DuelErrorCode::UnknownChallenge]);

    // Walking out of the club ends it.
    let _ = challenge(&mut s, &mut a, &mut b, at);
    let (reply, _rx) = tokio::sync::oneshot::channel();
    s.handle(WorldCmd::DevTeleport {
        character_id: b.cid,
        pos: OUTSIDE,
        reply,
    });
    ticks(&mut s, &mut at, 25);
    let walked = vec![ChallengeEnd::WalkedAway];
    assert_eq!(endings(&a.drain()), walked);
    assert_eq!(endings(&b.drain()), walked);
    // Accepting after walking away is too late as well.
    let (reply, _rx) = tokio::sync::oneshot::channel();
    s.handle(WorldCmd::DevTeleport {
        character_id: b.cid,
        pos: CLUB,
        reply,
    });

    // Disconnecting ends it for the other.
    let _ = challenge(&mut s, &mut a, &mut b, at);
    s.remove_player(b.id);
    s.tick(at);
    assert_eq!(endings(&a.drain()), vec![ChallengeEnd::Disconnected]);
    // And A is free again.
    let mut c = player(&mut s, "Cy", CLUB, DUELING);
    send(&mut s, &a, at, DuelMsg::Challenge { target: c.id });
    s.tick(at);
    assert!(challenge_of(&c.drain()).is_some());
}

#[test]
fn two_challenges_at_once_start_one_duel() {
    let mut s = sim();
    let mut a = player(&mut s, "Ada", CLUB, DUELING);
    let mut b = player(&mut s, "Bea", CLUB, DUELING);
    let mut c = player(&mut s, "Cy", CLUB, DUELING);
    let at = Instant::now();
    s.tick(at);
    a.drain();
    b.drain();
    c.drain();
    // Both challenge in the same tick: the second answers the first.
    send(&mut s, &a, at, DuelMsg::Challenge { target: b.id });
    send(&mut s, &b, at, DuelMsg::Challenge { target: a.id });
    let (ma, mb) = (a.drain(), b.drain());
    assert!(errors(&ma).is_empty() && errors(&mb).is_empty());
    assert_eq!(endings(&ma), vec![ChallengeEnd::Started]);
    let (sa, sb) = (starts(&ma), starts(&mb));
    assert_eq!(sa.len(), 1);
    assert_eq!(sb.len(), 1);
    assert_eq!(sa[0].0, sb[0].0, "one duel");
    assert_eq!(
        sa[0].1,
        DuelRole::Duelist(DuelSide::A),
        "the first challenger is A"
    );
    assert_eq!(sb[0].1, DuelRole::Duelist(DuelSide::B));
    let za = zone_of(&mut s, a.id).unwrap();
    assert_ne!(za, CASTLE);
    assert_eq!(zone_of(&mut s, b.id), Some(za));
    // Nobody else can pull either of them into another duel.
    send(&mut s, &c, at, DuelMsg::Challenge { target: a.id });
    assert_eq!(errors(&c.drain()), vec![DuelErrorCode::TargetNotInArea]);
    let running = read(&mut s, |ctx| {
        ctx.instance(ctx.zone(ctx.entity_by_name("Ada").unwrap()).unwrap())
            .map(|f| f.members.len())
    });
    assert_eq!(running, Some(2));
}

#[test]
fn a_duel_is_fought_in_its_arena_and_everyone_returns() {
    let mut s = sim();
    let mut a = player(&mut s, "Ada", CLUB, DUELING);
    let mut b = player(&mut s, "Bea", [151.0, 0.0, 101.0], DUELING);
    let mut at = Instant::now();
    s.tick(at);
    a.drain();
    b.drain();

    // Accepting: the challenge ends, both move to the arena, then the
    // duel's opening state.
    send(&mut s, &a, at, DuelMsg::Challenge { target: b.id });
    s.tick(at);
    let (id, _, _) = challenge_of(&b.drain()).unwrap();
    a.drain();
    send(
        &mut s,
        &b,
        at,
        DuelMsg::Respond {
            challenge_id: id,
            accept: true,
        },
    );
    let msgs = a.drain();
    let pos = |pred: &dyn Fn(&ServerMsg) -> bool| msgs.iter().position(pred).unwrap();
    let ended_at = pos(&|m| matches!(m, ServerMsg::DuelChallengeEnded { .. }));
    let zoned = pos(&|m| {
        matches!(
            m,
            ServerMsg::ZoneChanged {
                zone: Zone::Arena { .. },
                ..
            }
        )
    });
    let started = pos(&|m| matches!(m, ServerMsg::DuelStart { .. }));
    assert!(ended_at < zoned && zoned < started, "{msgs:?}");
    let ServerMsg::ZoneChanged {
        zone: Zone::Arena {
            duel_id,
            venue,
            layout,
        },
        spawn,
        ..
    } = &msgs[zoned]
    else {
        unreachable!()
    };
    let club = shipped().venues.get("dueling_club").unwrap();
    assert_eq!(
        (venue.as_str(), layout.as_str()),
        ("dueling_club", "duel_arena")
    );
    assert_eq!(*spawn, protocol::QPos::from_m(club.duelists[0].pos));
    let ServerMsg::DuelStart {
        you,
        a: da,
        b: db,
        poise_max,
        state,
        ..
    } = &msgs[started]
    else {
        unreachable!()
    };
    assert_eq!(*you, DuelRole::Duelist(DuelSide::A));
    assert_eq!((da.entity, db.entity), (a.id, b.id));
    assert_eq!((da.name.as_str(), db.name.as_str()), ("Ada", "Bea"));
    assert_eq!(da.loadout, DUELING.to_vec());
    assert_eq!(*poise_max, shipped().duel_rules.poise_max);
    assert_eq!(state.at_ms, 0);
    assert_eq!(state.fight_at_ms, shipped().duel_rules.countdown_ms);
    // The arena's walking speed, and no potions there.
    let speed = msgs.iter().rev().find_map(|m| match m {
        ServerMsg::OwnEffects { speed_pct, .. } => Some(*speed_pct),
        _ => None,
    });
    assert_eq!(speed, Some(shipped().duel_rules.move_speed_pct));
    s.input(a.id, at, Input::Potion(PotionInput::Drink { item_id: 5 }));
    assert!(a.drain().contains(&ServerMsg::PotionError {
        code: PotionErrorCode::Disabled,
        item_id: Some(5),
    }));
    let duel_id = *duel_id;
    let zone = zone_of(&mut s, a.id).unwrap();
    b.drain();

    // Casting during the countdown is refused, to the caster only.
    send(&mut s, &a, at, DuelMsg::Cast { spell: TORPEO });
    ticks(&mut s, &mut at, 1);
    assert!(matches!(
        events(&a.drain())[..],
        [DuelEvent::Refused {
            side: DuelSide::A,
            spell: TORPEO,
            reason: protocol::CastRefusal::NotFighting,
        }]
    ));
    assert!(events(&b.drain()).is_empty());
    let countdown = shipped().duel_rules.countdown_ms / shipped().duel_rules.tick_ms;
    ticks(&mut s, &mut at, countdown);
    assert!(events(&a.drain()).contains(&DuelEvent::Fight));
    assert!(events(&b.drain()).contains(&DuelEvent::Fight));

    // B tries to step off the stage: the floor is 0.9 m down.
    at += Duration::from_secs(1);
    let off = [5.5, 0.9, 1.6];
    s.input(
        b.id,
        at,
        Input::Move {
            seq: 1,
            pos: off,
            yaw: 0.0,
            anim: Anim::Walk,
        },
    );
    assert!(
        b.drain()
            .iter()
            .any(|m| matches!(m, ServerMsg::Correction { .. }))
    );
    assert_eq!(s.position(b.id), Some(club.duelists[1].pos));

    // A casts Torpeo whenever it is ready; B stands still and loses.
    let mut seen_a = Vec::new();
    let mut seen_b = Vec::new();
    let mut cast_anim = false;
    for _ in 0..1_000 {
        send(&mut s, &a, at, DuelMsg::Cast { spell: TORPEO });
        ticks(&mut s, &mut at, 1);
        let mb = b.drain();
        cast_anim |= mb.iter().any(|m| match m {
            ServerMsg::Snapshot { full, .. } => {
                full.iter().any(|st| st.id == a.id && st.anim == Anim::Cast)
            }
            _ => false,
        });
        seen_a.extend(events(&a.drain()));
        seen_b.extend(events(&mb));
        if ended(&seen_a).is_some() {
            break;
        }
    }
    assert!(cast_anim, "B saw A's wand gesture");
    assert_eq!(
        ended(&seen_a),
        Some((Some(DuelSide::A), DuelEndReason::Poise))
    );
    // B sees everything A does, except A's own refusals.
    let shared: Vec<_> = seen_a
        .iter()
        .filter(|e| !matches!(e, DuelEvent::Refused { .. }))
        .copied()
        .collect();
    assert_eq!(shared, seen_b);
    assert!(
        seen_a
            .iter()
            .any(|e| matches!(e, DuelEvent::Refused { .. }))
    );
    let hits = seen_b
        .iter()
        .filter(|e| {
            matches!(
                e,
                DuelEvent::Hit {
                    target: DuelSide::B,
                    ..
                }
            )
        })
        .count();
    assert!(hits >= 5, "{hits} hits");
    // The record job: one, for this duel.
    let jobs = s.take_ext_jobs();
    assert_eq!(jobs.len(), 1);

    // Its result: DuelResult (with each side's rewards), then Progress.
    let win = DuelRewards {
        xp: 40,
        capped: false,
        house_points: 2,
    };
    let loss = DuelRewards {
        xp: 15,
        capped: false,
        house_points: 0,
    };
    recorded(
        &mut s,
        duel_id,
        zone,
        [&a, &b],
        (Some(DuelSide::A), DuelEndReason::Poise),
        [win, loss],
        at,
    );
    let ma = a.drain();
    let result = ma
        .iter()
        .position(|m| matches!(m, ServerMsg::DuelResult { .. }));
    let progress = ma
        .iter()
        .position(|m| matches!(m, ServerMsg::Progress { .. }));
    assert!(result.unwrap() < progress.unwrap());
    let ServerMsg::DuelResult {
        rewards,
        return_in_ms,
        winner,
        ..
    } = &ma[result.unwrap()]
    else {
        unreachable!()
    };
    assert_eq!((*rewards, *winner), (Some(win), Some(DuelSide::A)));
    assert!(*return_in_ms > 0 && *return_in_ms <= shipped().duel_rules.return_delay_ms);
    assert!(b.drain().iter().any(|m| matches!(
        m,
        ServerMsg::DuelResult {
            rewards: Some(r),
            ..
        } if *r == loss
    )));
    let progress = &ma[ma
        .iter()
        .position(|m| matches!(m, ServerMsg::Progress { .. }))
        .unwrap()];
    assert!(matches!(
        progress,
        ServerMsg::Progress {
            gained: Some(protocol::XpGain {
                source: XpSource::DuelWin,
                amount: 40,
                ..
            }),
            ..
        }
    ));

    // After the return delay, everyone is back where they stood.
    let delay = shipped().duel_rules.return_delay_ms / 50 + 2;
    ticks(&mut s, &mut at, delay);
    assert!(in_castle(&a.drain()));
    assert!(in_castle(&b.drain()));
    assert_eq!(s.position(a.id), Some(CLUB));
    assert_eq!(s.position(b.id), Some([151.0, 0.0, 101.0]));
    assert!(read(&mut s, move |ctx| ctx.instance(zone).is_none()));
    // Free to duel again.
    send(&mut s, &b, at, DuelMsg::Challenge { target: a.id });
    assert!(challenge_of(&b.drain()).is_some());
}

#[test]
fn forfeiting_or_walking_out_loses() {
    let mut s = sim();
    let mut a = player(&mut s, "Ada", CLUB, DUELING);
    let mut b = player(&mut s, "Bea", CLUB, DUELING);
    let mut at = Instant::now();
    s.tick(at);
    a.drain();
    b.drain();

    // A forfeit, even during the countdown.
    let (_, zone) = start(&mut s, &mut a, &mut b, at);
    send(&mut s, &b, at, DuelMsg::Forfeit);
    ticks(&mut s, &mut at, 1);
    let ea = events(&a.drain());
    assert_eq!(
        ended(&ea),
        Some((Some(DuelSide::A), DuelEndReason::Forfeit))
    );
    assert_eq!(ended(&events(&b.drain())), ended(&ea));
    assert_eq!(s.take_ext_jobs().len(), 1, "recorded");
    // Forfeiting again (or casting) changes nothing.
    send(&mut s, &b, at, DuelMsg::Forfeit);
    assert_eq!(errors(&b.drain()), vec![DuelErrorCode::UnknownDuel]);
    ticks(&mut s, &mut at, 120);
    assert!(in_castle(&a.drain()));
    b.drain();
    assert!(read(&mut s, move |ctx| ctx.instance(zone).is_none()));

    // Walking out of the arena (`LeaveInstance`) forfeits too; the other
    // duelist stays for the result.
    at += Duration::from_secs(1);
    let (_, zone) = start(&mut s, &mut a, &mut b, at);
    ticks(&mut s, &mut at, 70);
    a.drain();
    s.input(a.id, at, Input::LeaveInstance);
    assert!(in_castle(&a.drain()));
    ticks(&mut s, &mut at, 1);
    assert_eq!(
        ended(&events(&b.drain())),
        Some((Some(DuelSide::B), DuelEndReason::Forfeit))
    );
    assert!(
        events(&a.drain()).is_empty(),
        "outside, A hears nothing more"
    );
    assert_eq!(s.take_ext_jobs().len(), 1);
    assert!(read(&mut s, move |ctx| ctx.instance(zone).is_some()));
    // A is not busy any more; B is until it returns.
    send(&mut s, &b, at, DuelMsg::Challenge { target: a.id });
    assert_eq!(errors(&b.drain()), vec![DuelErrorCode::NotInArea]);

    // Both walking out: the empty arena closes, and the duel is still
    // recorded (the first to go lost).
    ticks(&mut s, &mut at, 120);
    b.drain();
    at += Duration::from_secs(1);
    let (_, zone) = start(&mut s, &mut a, &mut b, at);
    s.input(b.id, at, Input::LeaveInstance);
    s.input(a.id, at, Input::LeaveInstance);
    assert!(read(&mut s, move |ctx| ctx.instance(zone).is_none()));
    assert_eq!(s.take_ext_jobs().len(), 1);
}

#[test]
fn a_disconnected_duelist_may_come_back_then_loses() {
    let mut s = sim();
    let mut a = player(&mut s, "Ada", CLUB, DUELING);
    let b_ch = character("Bea", House::Skytalon, Some(CLUB));
    let mut b = join_as(&mut s, b_ch.clone(), Social::default(), DUELING, 1024);
    let mut at = Instant::now();
    s.tick(at);
    a.drain();
    b.drain();
    let (duel_id, zone) = start(&mut s, &mut a, &mut b, at);

    // B's connection drops: the duel goes on.
    s.remove_player(b.id);
    ticks(&mut s, &mut at, 3);
    assert!(ended(&events(&a.drain())).is_none());
    assert!(read(&mut s, move |ctx| ctx.instance(zone).is_some()));
    // B signs in again (in the castle): straight back to their mark, and
    // everyone starts from a fresh DuelStart naming B's new entity.
    let mut b2 = join_as(&mut s, b_ch.clone(), Social::default(), DUELING, 1024);
    assert_ne!(b2.id, b.id);
    ticks(&mut s, &mut at, 1);
    let mb = b2.drain();
    assert!(mb.iter().any(|m| matches!(
        m,
        ServerMsg::ZoneChanged {
            zone: Zone::Arena { duel_id: d, .. },
            ..
        } if *d == duel_id
    )));
    let back = starts(&mb);
    assert_eq!(back.len(), 1);
    assert_eq!(back[0].1, DuelRole::Duelist(DuelSide::B));
    assert!(back[0].4 > 0, "the duel clock ran on");
    let ma = a.drain();
    let again = ma.iter().find_map(|m| match m {
        ServerMsg::DuelStart { b, .. } => Some(b.entity),
        _ => None,
    });
    assert_eq!(again, Some(b2.id));
    assert_eq!(zone_of(&mut s, b2.id), Some(zone));

    // Gone again, for good: the duel goes on for a while, then B loses.
    ticks(&mut s, &mut at, 60);
    a.drain();
    s.remove_player(b2.id);
    ticks(&mut s, &mut at, 60);
    assert!(ended(&events(&a.drain())).is_none(), "still in the grace");
    // Away but still in play: deleting B's character is refused meanwhile.
    assert!(s.in_play(b_ch.id));
    send(&mut s, &a, at, DuelMsg::Cast { spell: TORPEO });
    ticks(&mut s, &mut at, 40);
    let ea = events(&a.drain());
    assert!(
        ea.iter().any(|e| matches!(
            e,
            DuelEvent::Hit {
                target: DuelSide::B,
                ..
            }
        )),
        "the absent duelist stands still: {ea:?}"
    );
    let grace = shipped().duel_rules.disconnect_grace_ms / 50;
    ticks(&mut s, &mut at, grace + 20);
    assert_eq!(
        ended(&events(&a.drain())),
        Some((Some(DuelSide::A), DuelEndReason::Disconnect))
    );
    assert!(!s.in_play(b_ch.id), "decided: B is let go");
    assert_eq!(s.take_ext_jobs().len(), 1);
    // A late return finds no duel to go back to.
    let mut b3 = join_as(&mut s, b_ch, Social::default(), DUELING, 1024);
    ticks(&mut s, &mut at, 2);
    assert!(starts(&b3.drain()).is_empty());
    assert_eq!(zone_of(&mut s, b3.id), Some(CASTLE));
}

/// Both duelists of two duels follow the same script; spectators of the
/// second one do whatever they can. Every duelist sees the same duel.
#[test]
fn spectators_see_everything_and_change_nothing() {
    let mut s = sim();
    let mut a1 = player(&mut s, "Ada", CLUB, DUELING);
    let mut b1 = player(&mut s, "Bea", CLUB, DUELING);
    let mut a2 = player(&mut s, "Cy", CLUB, DUELING);
    let mut b2 = player(&mut s, "Dot", CLUB, DUELING);
    let mut watchers: Vec<TestClient> = (0..3)
        .map(|n| player(&mut s, &format!("Fan{n}"), CLUB, DUELING))
        .collect();
    let mut at = Instant::now();
    s.tick(at);
    // Both duels start in the same tick, so their clocks agree.
    send(&mut s, &a1, at, DuelMsg::Challenge { target: b1.id });
    send(&mut s, &a2, at, DuelMsg::Challenge { target: b2.id });
    s.tick(at);
    for b in [&mut b1, &mut b2] {
        let (challenge_id, _, _) = challenge_of(&b.drain()).unwrap();
        let accept = DuelMsg::Respond {
            challenge_id,
            accept: true,
        };
        s.input(b.id, at, Input::Duel(accept));
    }
    let duel2 = starts(&a2.drain())[0].0;
    a1.drain();
    let (zone1, zone2) = (
        zone_of(&mut s, a1.id).unwrap(),
        zone_of(&mut s, a2.id).unwrap(),
    );
    assert_ne!(zone1, zone2);

    // The list from the club, then in.
    let w = &mut watchers[0];
    w.drain();
    s.input(w.id, at, Input::Duel(DuelMsg::List));
    let list = w.drain().into_iter().find_map(|m| match m {
        ServerMsg::DuelList { duels } => Some(duels),
        _ => None,
    });
    let list = list.expect("a list");
    assert_eq!(list.len(), 2);
    assert_eq!(list[1].duel_id, duel2);
    assert_eq!(
        (list[1].a_name.as_str(), list[1].b_name.as_str()),
        ("Cy", "Dot")
    );
    for w in &mut watchers {
        s.input(w.id, at, Input::Duel(DuelMsg::Spectate { duel_id: duel2 }));
        let st = starts(&w.drain());
        assert_eq!(st.len(), 1);
        assert_eq!(st[0].1, DuelRole::Spectator);
        assert_eq!(st[0].2, DUELING.to_vec(), "the duelists' loadouts");
    }
    assert_eq!(zone_of(&mut s, watchers[2].id), Some(zone2));
    // Spectators are not duelists, nor challengeable from inside.
    s.input(a1.id, at, Input::Duel(DuelMsg::Spectate { duel_id: duel2 }));
    assert_eq!(errors(&a1.drain()), vec![DuelErrorCode::NotInArea]);
    s.input(a2.id, at, Input::Duel(DuelMsg::List));
    assert_eq!(errors(&a2.drain()), vec![DuelErrorCode::NotInArea]);

    // The script: casts on fixed ticks; the spectators cast, forfeit,
    // answer and walk around (they cannot climb onto the stage).
    let script = |t: u32| -> (Option<SpellId>, Option<SpellId>) {
        let a = match t % 50 {
            0 => Some(TORPEO),
            25 if t % 150 == 25 => Some(DIMITTO),
            _ => None,
        };
        let b = match t % 50 {
            23 => Some(SCUTARA),
            30 => Some(TORPEO),
            _ => None,
        };
        (a, b)
    };
    let mut seen = [vec![], vec![], vec![], vec![]];
    let mut watched = vec![vec![]; watchers.len()];
    let mut seq = 0;
    for t in 0..2_400 {
        let (ca, cb) = script(t);
        for (x, c) in [(&a1, ca), (&a2, ca), (&b1, cb), (&b2, cb)] {
            if let Some(spell) = c {
                send(&mut s, x, at, DuelMsg::Cast { spell });
            }
        }
        if t % 10 == 0 {
            seq += 1;
            for (n, w) in watchers.iter().enumerate() {
                let msgs = [
                    DuelMsg::Cast { spell: TORPEO },
                    DuelMsg::Forfeit,
                    DuelMsg::Respond {
                        challenge_id: 1,
                        accept: true,
                    },
                ];
                send(&mut s, w, at, msgs[n % 3].clone());
                let p = s.position(w.id).unwrap();
                let toward = [p[0] + 0.3, 0.9, p[2] + if p[2] < 0.0 { 0.3 } else { -0.3 }];
                s.input(
                    w.id,
                    at,
                    Input::Move {
                        seq,
                        pos: toward,
                        yaw: 0.0,
                        anim: Anim::Walk,
                    },
                );
            }
        }
        ticks(&mut s, &mut at, 1);
        for (k, c) in [&mut a1, &mut b1, &mut a2, &mut b2].into_iter().enumerate() {
            seen[k].extend(frames(&c.drain()));
        }
        for (k, w) in watchers.iter_mut().enumerate() {
            watched[k].extend(frames(&w.drain()));
        }
        if seen
            .iter()
            .all(|f| ended(&f.iter().flat_map(|x| x.1.clone()).collect::<Vec<_>>()).is_some())
        {
            break;
        }
    }
    let all = |f: &[(u32, Vec<DuelEvent>)]| f.iter().flat_map(|x| x.1.clone()).collect::<Vec<_>>();
    assert!(ended(&all(&seen[0])).is_some(), "the duel ended");
    assert_eq!(seen[0], seen[2], "A sees the same duel");
    assert_eq!(seen[1], seen[3], "B sees the same duel");
    // Spectators see every shared event of the duel they watch.
    let shared: Vec<_> = seen[2]
        .iter()
        .map(|(at, ev)| {
            (
                *at,
                ev.iter()
                    .filter(|e| !matches!(e, DuelEvent::Refused { .. }))
                    .copied()
                    .collect::<Vec<_>>(),
            )
        })
        .filter(|f| !f.1.is_empty())
        .collect();
    for w in &watched {
        assert_eq!(*w, shared);
    }
    assert!(
        all(&seen[0])
            .iter()
            .any(|e| matches!(e, DuelEvent::Reflected { .. } | DuelEvent::Blocked { .. }))
    );
}

#[test]
fn spectating_has_room_for_so_many_and_ends_with_the_duel() {
    let mut s = sim();
    let mut a = player(&mut s, "Ada", CLUB, DUELING);
    let mut b = player(&mut s, "Bea", CLUB, DUELING);
    let max = usize::from(shipped().venues.get("dueling_club").unwrap().max_spectators);
    let mut fans: Vec<TestClient> = (0..=max)
        .map(|n| player(&mut s, &format!("Fan{n}"), CLUB, &[]))
        .collect();
    let mut at = Instant::now();
    s.tick(at);
    let (duel_id, zone) = start(&mut s, &mut a, &mut b, at);
    at += Duration::from_secs(10);
    for f in &mut fans {
        f.drain();
        s.input(f.id, at, Input::Duel(DuelMsg::Spectate { duel_id }));
    }
    let last = fans.last_mut().unwrap();
    assert_eq!(errors(&last.drain()), vec![DuelErrorCode::Full]);
    let inside = read(&mut s, move |ctx| ctx.instance(zone).unwrap().members.len());
    assert_eq!(inside, max + 2);
    // Spread out on the floor around the stage, never on it.
    let mut spots = std::collections::HashSet::new();
    for f in &fans[..max] {
        let p = s.position(f.id).unwrap();
        assert_eq!(p[1], 0.0, "{p:?}");
        spots.insert((p[0].to_bits(), p[2].to_bits()));
    }
    assert!(spots.len() > max / 2, "{} spots", spots.len());
    // One leaves: room for another; the list counts them.
    s.input(fans[0].id, at, Input::LeaveInstance);
    assert!(in_castle(&fans[0].drain()));
    let last = fans.last_mut().unwrap();
    s.input(last.id, at, Input::Duel(DuelMsg::Spectate { duel_id }));
    assert_eq!(starts(&last.drain()).len(), 1);
    s.input(fans[0].id, at, Input::Duel(DuelMsg::List));
    let n = fans[0].drain().into_iter().find_map(|m| match m {
        ServerMsg::DuelList { duels } => Some(duels[0].spectators),
        _ => None,
    });
    assert_eq!(n, Some(max as u16));
    // The duel ends; spectators get the result (no rewards) and go back too.
    send(&mut s, &a, at, DuelMsg::Forfeit);
    ticks(&mut s, &mut at, 1);
    recorded(
        &mut s,
        duel_id,
        zone,
        [&a, &b],
        (Some(DuelSide::B), DuelEndReason::Forfeit),
        [NO_REWARDS_TEST, NO_REWARDS_TEST],
        at,
    );
    let fan = &mut fans[3];
    assert!(fan.drain().iter().any(|m| matches!(
        m,
        ServerMsg::DuelResult {
            rewards: None,
            winner: Some(DuelSide::B),
            ..
        }
    )));
    ticks(&mut s, &mut at, 120);
    assert!(in_castle(&fans[3].drain()));
    assert!(read(&mut s, move |ctx| ctx.instance(zone).is_none()));
}

const NO_REWARDS_TEST: DuelRewards = DuelRewards {
    xp: 0,
    capped: false,
    house_points: 0,
};

#[test]
fn the_club_teaches_whoever_stands_in_it() {
    let mut s = sim();
    let mut n = player(&mut s, "Nia", OUTSIDE, &[]);
    let mut at = Instant::now();
    ticks(&mut s, &mut at, 40);
    assert!(s.take_ext_jobs().is_empty(), "not in the club");
    let (reply, _rx) = tokio::sync::oneshot::channel();
    s.handle(WorldCmd::DevTeleport {
        character_id: n.cid,
        pos: CLUB,
        reply,
    });
    ticks(&mut s, &mut at, 25);
    assert_eq!(s.take_ext_jobs().len(), 1, "one lesson");
    ticks(&mut s, &mut at, 60);
    assert!(s.take_ext_jobs().is_empty(), "one at a time");
    // A failed lesson is tried again later, not every second.
    let failed = DuelDone::Taught {
        character_id: n.cid,
        result: Err("database".into()),
    };
    s.ext_done(ExtDone::Duel(failed), at);
    ticks(&mut s, &mut at, 100);
    assert!(s.take_ext_jobs().is_empty());
    ticks(&mut s, &mut at, 600);
    assert_eq!(s.take_ext_jobs().len(), 1);
    n.drain();
    let taught = Taught {
        learned: DUELING.to_vec(),
        loadout: DUELING.to_vec(),
        new: DUELING.to_vec(),
    };
    let done = DuelDone::Taught {
        character_id: n.cid,
        result: Ok(taught),
    };
    s.ext_done(ExtDone::Duel(done), at);
    assert!(n.drain().contains(&ServerMsg::Spellbook {
        learned: DUELING.to_vec(),
        loadout: DUELING.to_vec(),
        slots: 3,
        new: DUELING.to_vec(),
        hotbar: vec![None; protocol::HOTBAR_SLOTS],
    }));
    ticks(&mut s, &mut at, 200);
    assert!(s.take_ext_jobs().is_empty(), "nothing more at level 1");
    // A level-up teaches nothing more here; the
    // Defence class teaches Titillara (level 3) and Repulsa (level 5).
    let id = n.id;
    let level5 = shipped().progression.level_start(5);
    run(&mut s, move |ctx| ctx.progress_changed(id, level5, None));
    ticks(&mut s, &mut at, 25);
    assert!(
        s.take_ext_jobs().is_empty(),
        "the club teaches level 1 only"
    );
    let _ = (TITILLARA, REPULSA);
}

#[test]
fn loadouts_are_checked_when_the_duel_starts() {
    let mut s = sim();
    let mut a = player(&mut s, "Ada", CLUB, DUELING);
    let mut b = player(&mut s, "Bea", CLUB, DUELING);
    let at = Instant::now();
    s.tick(at);
    // A chose an order; B's stored loadout names a spell it never learned
    // (and too many): B gets the default one.
    let (ia, ib) = (a.id, b.id);
    run(&mut s, move |ctx| {
        ctx.set_spellbook(ia, DUELING.to_vec(), vec![DIMITTO, SCUTARA], vec![]);
        ctx.set_spellbook(
            ib,
            DUELING.to_vec(),
            vec![REPULSA, TORPEO, SCUTARA, DIMITTO],
            vec![],
        );
    });
    a.drain();
    b.drain();
    send(&mut s, &a, at, DuelMsg::Challenge { target: b.id });
    s.tick(at);
    let (id, _, _) = challenge_of(&b.drain()).unwrap();
    send(
        &mut s,
        &b,
        at,
        DuelMsg::Respond {
            challenge_id: id,
            accept: true,
        },
    );
    let st = starts(&a.drain());
    assert_eq!(st[0].2, vec![DIMITTO, SCUTARA]);
    assert_eq!(st[0].3, DUELING.to_vec());
    // A spell outside the loadout is refused in the duel itself.
    let mut at = at;
    ticks(&mut s, &mut at, 61);
    a.drain();
    send(&mut s, &a, at, DuelMsg::Cast { spell: TORPEO });
    ticks(&mut s, &mut at, 1);
    assert!(events(&a.drain()).contains(&DuelEvent::Refused {
        side: DuelSide::A,
        spell: TORPEO,
        reason: protocol::CastRefusal::NotInLoadout,
    }));
}

/// 50 duels at once with two spectators each, every duelist casting and
/// sidestepping: the whole tick stays cheap, and so do the frames.
#[test]
fn many_duels_cost_little_per_tick() {
    const DUELS: usize = 50;
    let mut s = sim();
    let mut clients: Vec<TestClient> = (0..DUELS * 4)
        .map(|n| {
            let ch = character(&format!("P{n}"), House::ALL[n % 4], Some(CLUB));
            join_as(&mut s, ch, Social::default(), DUELING, 4096)
        })
        .collect();
    let mut at = Instant::now();
    s.tick(at);
    let mut duel_ids = Vec::new();
    for d in 0..DUELS {
        let (left, right) = clients.split_at_mut(4 * d + 1);
        let (id, _) = start(&mut s, &mut left[4 * d], &mut right[0], at);
        duel_ids.push(id);
    }
    for d in 0..DUELS {
        for k in 2..4 {
            let c = &clients[4 * d + k];
            s.input(
                c.id,
                at,
                Input::Duel(DuelMsg::Spectate {
                    duel_id: duel_ids[d],
                }),
            );
        }
    }
    for c in &mut clients {
        while c.rx.try_recv().is_ok() {}
    }
    let mut times = Vec::new();
    let mut bytes = vec![0usize; clients.len()];
    let (mut hits, mut corrections) = (0, 0);
    let mut seq = 0;
    let ticks_n = 30 * 20;
    for t in 0..ticks_n {
        at += TICK;
        let mut cmds = Vec::new();
        for d in 0..DUELS {
            for side in 0..2 {
                let c = &clients[4 * d + side];
                let phase = (t + (d as u32) * 7 + side as u32 * 25) % 50;
                let spell = match phase {
                    0 => Some(TORPEO),
                    20 => Some(SCUTARA),
                    35 if t % 200 < 50 => Some(DIMITTO),
                    _ => None,
                };
                if let Some(spell) = spell {
                    cmds.push(WorldCmd::Input {
                        id: c.id,
                        at,
                        input: Input::Duel(DuelMsg::Cast { spell }),
                    });
                }
                if t % 2 == 0 {
                    let p = s.position(c.id).unwrap();
                    let z = if (t / 40) % 2 == 0 { 0.08 } else { -0.08 };
                    cmds.push(WorldCmd::Input {
                        id: c.id,
                        at,
                        input: Input::Move {
                            seq: seq + 1,
                            // Sidesteps on the stage (0.9 m up), a stroll after.
                            pos: [
                                p[0],
                                p[1],
                                if p[1] > 0.5 {
                                    (p[2] + z).clamp(-0.6, 0.6)
                                } else {
                                    p[2] + z
                                },
                            ],
                            yaw: 0.0,
                            anim: Anim::Walk,
                        },
                    });
                }
            }
        }
        if t % 2 == 0 {
            seq += 1;
        }
        let begin = Instant::now();
        for cmd in cmds {
            s.handle(cmd);
        }
        s.tick(at);
        times.push(begin.elapsed());
        for (k, c) in clients.iter_mut().enumerate() {
            while let Ok(b) = c.rx.try_recv() {
                bytes[k] += b.len();
                // What the duelists heard (decoding is outside the timing).
                if k % 4 < 2 {
                    match protocol::decode_server(&b).unwrap() {
                        ServerMsg::DuelEvents { events, .. } => {
                            hits += events
                                .iter()
                                .filter(|e| matches!(e, DuelEvent::Hit { .. }))
                                .count();
                        }
                        ServerMsg::Correction { .. } => corrections += 1,
                        _ => {}
                    }
                }
            }
        }
    }
    assert!(hits > DUELS * 5, "the duels were fought: {hits} hits seen");
    assert_eq!(corrections, 0, "every sidestep was allowed");
    assert_eq!(s.len(), DUELS * 4, "nobody was dropped");
    times.sort();
    let p99 = times[times.len() * 99 / 100];
    let secs = ticks_n as f64 / 20.0;
    let per =
        |k: usize| bytes.iter().skip(k).step_by(4).sum::<usize>() as f64 / secs / DUELS as f64;
    println!(
        "{DUELS} duels, {} spectators: tick p50 {:?}, p99 {:?}, max {:?}; \
         {:.0} B/s per duelist, {:.0} B/s per spectator",
        DUELS * 2,
        times[times.len() / 2],
        p99,
        times.last().unwrap(),
        per(0),
        per(2),
    );
    // The production budget is 15 ms for 1,000 players; unoptimized test
    // builds get more room.
    let budget = if cfg!(debug_assertions) { 40 } else { 5 };
    assert!(p99 < Duration::from_millis(budget), "p99 {p99:?}");
    assert!(per(2) < 3_500.0, "spectators' bandwidth");
}

// ------------------------------------------------- spells to level 20

const ADVENI: SpellId = 4;
const RIGORA: SpellId = 15;
const DISSOLVO: SpellId = 16;
const TARDARA: SpellId = 17;
const GUARDIAN: SpellId = 18;
/// Every dueling spell at level 19 and up.
const ALL_DUELING: &[SpellId] = &[
    SCUTARA, TORPEO, DIMITTO, TITILLARA, REPULSA, RIGORA, ADVENI, DISSOLVO, TARDARA, GUARDIAN,
];

/// A level-20 duelist with every dueling spell and `loadout` (5 slots).
fn veteran(s: &mut WorldSim, name: &str, pos: [f32; 3], loadout: &[SpellId]) -> TestClient {
    veteran_as(s, character(name, House::Skytalon, Some(pos)), loadout)
}

/// [`veteran`] as character `ch` (to sign the same character in again).
fn veteran_as(s: &mut WorldSim, ch: Character, loadout: &[SpellId]) -> TestClient {
    let (tx, rx) = mpsc::channel(4096);
    let control = Arc::new(ConnControl::default());
    let cid = ch.id;
    let joining = Joining {
        school: SchoolState {
            xp: 10_640,
            learned: ALL_DUELING.to_vec(),
            loadout: loadout.to_vec(),
            ..Default::default()
        },
        ..Joining::alone(ch)
    };
    let id = s
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

fn effect_of(s: &mut WorldSim, e: EntityId, kind: EffectKind) -> Option<ActiveEffect> {
    read(s, move |ctx| {
        ctx.effects(e).iter().copied().find(|x| x.kind == kind)
    })
}

fn speed_of(s: &mut WorldSim, e: EntityId) -> u16 {
    read(s, move |ctx| ctx.speed_pct(e)).unwrap()
}

/// Ticks (A casting `spell` once first) until an event matching `pred`
/// reaches A; returns that tick's events.
fn cast_until(
    s: &mut WorldSim,
    a: &mut TestClient,
    b: &mut TestClient,
    at: &mut Instant,
    caster: DuelSide,
    spell: SpellId,
    pred: impl Fn(&DuelEvent) -> bool,
) -> Vec<DuelEvent> {
    let who = if caster == DuelSide::A { &*a } else { &*b };
    send(s, who, *at, DuelMsg::Cast { spell });
    for _ in 0..200 {
        ticks(s, at, 1);
        b.drain();
        let ev = events(&a.drain());
        if ev.iter().any(&pred) {
            return ev;
        }
    }
    panic!("no such event after casting {spell}");
}

/// Spells L20 (contract 4.2): each duel status becomes its world effect on
/// the duelist, and a duel's start and end clear them.
#[test]
fn duel_statuses_become_world_effects_and_are_cleared() {
    let mut s = sim();
    let mut a = veteran(
        &mut s,
        "Ada",
        CLUB,
        &[TORPEO, RIGORA, ADVENI, TARDARA, GUARDIAN],
    );
    let mut b = veteran(
        &mut s,
        "Bea",
        [151.0, 0.0, 101.0],
        &[SCUTARA, DISSOLVO, TORPEO, GUARDIAN, TARDARA],
    );
    let mut at = Instant::now();
    s.tick(at);
    // A social guardian does not come into the duel.
    let (ida, idb, cidb) = (a.id, b.id, b.cid);
    let long = at + Duration::from_secs(600);
    run(&mut s, move |ctx| {
        ctx.apply_effect(
            ida,
            ActiveEffect {
                kind: EffectKind::Guardian,
                item_def: 0,
                fortune: None,
                visible: Some(protocol::VisibleEffect::Guardian {
                    animal: protocol::GuardianAnimal::Hare,
                    guarding: false,
                }),
                speed_pct: 100,
                until: long,
            },
        );
    });
    a.drain();
    b.drain();
    let (duel_id, zone) = start(&mut s, &mut a, &mut b, at);
    assert!(effect_of(&mut s, ida, EffectKind::Guardian).is_none());
    let arena = shipped().duel_rules.move_speed_pct;
    let countdown = shipped().duel_rules.countdown_ms / shipped().duel_rules.tick_ms;
    ticks(&mut s, &mut at, countdown + 1);
    a.drain();
    b.drain();

    // Rooted: Petrified, speed 0 in the arena.
    let ev = cast_until(&mut s, &mut a, &mut b, &mut at, DuelSide::A, RIGORA, |e| {
        matches!(e, DuelEvent::Rooted { .. })
    });
    assert!(ev.iter().any(|e| matches!(
        e,
        DuelEvent::Rooted {
            side: DuelSide::B,
            ..
        }
    )));
    let fx = effect_of(&mut s, idb, EffectKind::Petrified).expect("petrified");
    assert_eq!(fx.visible, Some(protocol::VisibleEffect::Bound));
    assert_eq!(speed_of(&mut s, idb), 0);
    // Cleansed: both hindrances go.
    cast_until(
        &mut s,
        &mut a,
        &mut b,
        &mut at,
        DuelSide::B,
        DISSOLVO,
        |e| matches!(e, DuelEvent::Cleansed { .. }),
    );
    assert!(effect_of(&mut s, idb, EffectKind::Petrified).is_none());
    assert_eq!(speed_of(&mut s, idb), arena);
    // Slowed: the arena's speed times the slow.
    cast_until(&mut s, &mut a, &mut b, &mut at, DuelSide::A, TARDARA, |e| {
        matches!(e, DuelEvent::Slowed { .. })
    });
    let fx = effect_of(&mut s, idb, EffectKind::Slowed).expect("slowed");
    assert_eq!(
        fx.visible,
        Some(protocol::VisibleEffect::Slowed { pct: 50 })
    );
    assert_eq!(speed_of(&mut s, idb), arena * 50 / 100);
    // A ward: B's guarding guardian, its own animal; a block takes it away.
    cast_until(
        &mut s,
        &mut a,
        &mut b,
        &mut at,
        DuelSide::B,
        GUARDIAN,
        |e| matches!(e, DuelEvent::WardUp { .. }),
    );
    let fx = effect_of(&mut s, idb, EffectKind::Guardian).expect("ward");
    let animal = sim_core::guardian_animal(cidb);
    assert_eq!(
        fx.visible,
        Some(protocol::VisibleEffect::Guardian {
            animal,
            guarding: true
        })
    );
    cast_until(&mut s, &mut a, &mut b, &mut at, DuelSide::A, TORPEO, |e| {
        matches!(e, DuelEvent::Warded { .. })
    });
    assert!(effect_of(&mut s, idb, EffectKind::Guardian).is_none());
    // Pulled and staggered: a forced move to the pull's end, held for the
    // stagger.
    let ev = cast_until(&mut s, &mut a, &mut b, &mut at, DuelSide::A, ADVENI, |e| {
        matches!(e, DuelEvent::Staggered { .. })
    });
    let to = ev
        .iter()
        .find_map(|e| match *e {
            DuelEvent::Pulled {
                side: DuelSide::B,
                to,
                ..
            } => Some(to),
            _ => None,
        })
        .expect("pulled");
    assert!(read(&mut s, move |ctx| ctx.being_moved(idb)));
    ticks(&mut s, &mut at, 12);
    let p = s.position(idb).unwrap();
    assert!(
        ((p[0] * 100.0).round() as i16 - to[0]).abs() <= 1,
        "{p:?} {to:?}"
    );

    // The end (A forfeits): nobody keeps a status.
    cast_until(&mut s, &mut a, &mut b, &mut at, DuelSide::A, TARDARA, |e| {
        matches!(e, DuelEvent::Slowed { .. })
    });
    assert!(effect_of(&mut s, idb, EffectKind::Slowed).is_some());
    send(&mut s, &a, at, DuelMsg::Forfeit);
    ticks(&mut s, &mut at, 1);
    for e in [ida, idb] {
        for kind in [
            EffectKind::Petrified,
            EffectKind::Slowed,
            EffectKind::Guardian,
        ] {
            assert!(effect_of(&mut s, e, kind).is_none(), "{kind:?}");
        }
    }
    assert_eq!(speed_of(&mut s, idb), arena);
    let _ = (duel_id, zone);
}

/// Spells L20: a duelist who signs in again mid-duel (a new entity) gets
/// the duel's statuses back on it: a slow, a root and a guarding ward, each
/// for the time it has left.
#[test]
fn a_duelist_back_mid_duel_keeps_its_statuses() {
    let mut s = sim();
    let mut a = veteran(
        &mut s,
        "Ada",
        CLUB,
        &[TORPEO, RIGORA, ADVENI, TARDARA, GUARDIAN],
    );
    let b_ch = character("Bea", House::Skytalon, Some([151.0, 0.0, 101.0]));
    let loadout_b = [SCUTARA, DISSOLVO, TORPEO, GUARDIAN, TARDARA];
    let mut b = veteran_as(&mut s, b_ch.clone(), &loadout_b);
    let mut at = Instant::now();
    s.tick(at);
    a.drain();
    b.drain();
    let (_, zone) = start(&mut s, &mut a, &mut b, at);
    let arena = shipped().duel_rules.move_speed_pct;
    let countdown = shipped().duel_rules.countdown_ms / shipped().duel_rules.tick_ms;
    ticks(&mut s, &mut at, countdown + 1);
    a.drain();
    b.drain();

    // Signs B out and in again; the new client, back in the arena.
    let again = |s: &mut WorldSim, b: TestClient, at: &mut Instant| -> TestClient {
        s.remove_player(b.id);
        ticks(s, at, 1);
        let mut b2 = veteran_as(s, b_ch.clone(), &loadout_b);
        assert_ne!(b2.id, b.id);
        ticks(s, at, 1);
        assert_eq!(zone_of(s, b2.id), Some(zone));
        b2.drain();
        b2
    };

    // Slowed, back: still slowed, until the same moment.
    cast_until(&mut s, &mut a, &mut b, &mut at, DuelSide::A, TARDARA, |e| {
        matches!(e, DuelEvent::Slowed { .. })
    });
    let before = effect_of(&mut s, b.id, EffectKind::Slowed).expect("slowed");
    let mut b = again(&mut s, b, &mut at);
    let fx = effect_of(&mut s, b.id, EffectKind::Slowed).expect("slowed again");
    assert_eq!(fx.visible, before.visible);
    let drift = fx
        .until
        .max(before.until)
        .duration_since(fx.until.min(before.until));
    assert!(drift <= TICK * 2, "{drift:?}");
    assert_eq!(speed_of(&mut s, b.id), arena * 50 / 100);

    // Rooted, back: still petrified (speed 0).
    cast_until(&mut s, &mut a, &mut b, &mut at, DuelSide::A, RIGORA, |e| {
        matches!(e, DuelEvent::Rooted { .. })
    });
    let mut b = again(&mut s, b, &mut at);
    let fx = effect_of(&mut s, b.id, EffectKind::Petrified).expect("petrified again");
    assert_eq!(fx.visible, Some(protocol::VisibleEffect::Bound));
    assert_eq!(speed_of(&mut s, b.id), 0);

    // A ward, back: the guarding guardian again, and it still blocks.
    cast_until(
        &mut s,
        &mut a,
        &mut b,
        &mut at,
        DuelSide::B,
        GUARDIAN,
        |e| matches!(e, DuelEvent::WardUp { .. }),
    );
    let mut b = again(&mut s, b, &mut at);
    let fx = effect_of(&mut s, b.id, EffectKind::Guardian).expect("ward again");
    assert_eq!(
        fx.visible,
        Some(protocol::VisibleEffect::Guardian {
            animal: sim_core::guardian_animal(b.cid),
            guarding: true
        })
    );
    cast_until(&mut s, &mut a, &mut b, &mut at, DuelSide::A, TORPEO, |e| {
        matches!(e, DuelEvent::Warded { .. })
    });
    assert!(effect_of(&mut s, b.id, EffectKind::Guardian).is_none());
}
