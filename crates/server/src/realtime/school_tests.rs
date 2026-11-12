//! Tests of the seams in the realtime core: feature instances
//! (class sections, arenas) with their NPCs, zone priority and zone rules,
//! levels and Lucerna in looks, the clock in `Welcome`, and the routing of
//! school and dueling input to their handlers.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use protocol::{
    ClassErrorCode, DuelErrorCode, EffectKind, EntityEntry, House, PotionErrorCode, ServerMsg,
    SpellErrorCode, SpellTarget, TraceReject, VisibleEffect, Zone,
};

use super::*;
use crate::clock::ServerClock;
use crate::realtime::world::tests::{TestClient, join, sim};
use crate::realtime::world::{Input, WorldCmd, WorldSim};

fn run(s: &mut WorldSim, f: impl FnOnce(&mut WorldCtx<'_>) + Send + 'static) {
    s.handle(WorldCmd::ExtDone(ExtDone::Run(Box::new(f))));
}

fn ticks(s: &mut WorldSim, at: Instant, n: u32) {
    for _ in 0..n {
        s.tick(at);
    }
}

fn professor() -> NpcSpawn {
    let def = &crate::catalog::shipped().class("charms").unwrap().professor;
    NpcSpawn {
        npc: def.id.clone(),
        name: def.name.clone(),
        house: def.house(),
        appearance: def.appearance,
        pos: def.pos,
        yaw: def.yaw,
    }
}

/// Opens a class section with its professor and takes `who` in; returns
/// (zone, professor).
fn open_class(s: &mut WorldSim, who: &[EntityId]) -> (ZoneId, EntityId) {
    let out = Arc::new(Mutex::new(None));
    let (o, who) = (out.clone(), who.to_vec());
    run(s, move |ctx| {
        let kind = FeatureKind::Class {
            class: "charms".into(),
            section: 0,
        };
        let zone = ctx
            .open_instance(kind, "class_charms")
            .expect("a known layout");
        let prof = ctx.spawn_npc(zone, professor()).expect("room for an NPC");
        ctx.set_instance_priority(zone, vec![prof]);
        for e in who {
            assert!(ctx.enter_instance(e, zone, None));
            assert_eq!(ctx.zone(e), Some(zone));
        }
        *o.lock().unwrap() = Some((zone, prof));
    });
    out.lock().unwrap().take().unwrap()
}

fn entries(c: &mut TestClient) -> Vec<protocol::EntityInfo> {
    c.drain()
        .into_iter()
        .flat_map(|m| match m {
            ServerMsg::Snapshot { entered, .. } => entered,
            _ => vec![],
        })
        .filter_map(|e| match e {
            EntityEntry::Full(info) => Some(info),
            EntityEntry::Cached { .. } => None,
        })
        .collect()
}

#[test]
fn class_sections_show_their_professor_and_close_when_empty() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, [100.0, 0.0, 100.0]);
    let mut b = join(&mut s, "Bea", House::Skytalon, [101.0, 0.0, 100.0]);
    let now = Instant::now();
    ticks(&mut s, now, 2);
    a.drain();
    b.drain();
    let (zone, prof) = open_class(&mut s, &[a.id, b.id]);
    assert!(a.drain().contains(&ServerMsg::ZoneChanged {
        zone: Zone::Class {
            class: "charms".into(),
            section: 0,
            layout: "class_charms".into(),
        },
        spawn: protocol::QPos::from_m(s.position(a.id).unwrap()),
        yaw: protocol::yaw_to_u8(std::f32::consts::PI),
    }));
    // With room for one, Ada still sees the professor (a priority entity),
    // drawn from its data, at level 0.
    s.input(a.id, now, Input::ViewLimit(1));
    ticks(&mut s, now, 2);
    let seen = entries(&mut a);
    assert_eq!(seen.len(), 1, "{seen:?}");
    assert_eq!(seen[0].id, prof);
    assert_eq!(seen[0].npc.as_deref(), Some("charms_professor"));
    assert_eq!(
        (seen[0].level, seen[0].character.house),
        (0, House::Skytalon)
    );
    assert_eq!(s.players(), 2, "NPCs are not players");
    // NPC events reach the room; NPCs get no snapshots and send nothing.
    run(&mut s, move |ctx| {
        ctx.npc_event(
            prof,
            protocol::NpcEvent::Say {
                text: "Hello!".into(),
            },
        );
        assert!(ctx.is_npc(prof));
        assert_eq!(ctx.character_id(prof), None);
    });
    assert!(b.drain().contains(&ServerMsg::NpcEvent {
        id: prof,
        event: protocol::NpcEvent::Say {
            text: "Hello!".into()
        },
    }));

    // Leaving goes back to where they stood; the last one out closes it.
    s.input(a.id, now, Input::LeaveInstance);
    assert!(a.drain().iter().any(|m| matches!(
        m,
        ServerMsg::ZoneChanged {
            zone: Zone::Castle,
            ..
        }
    )));
    assert_eq!(s.position(a.id), Some([100.0, 0.0, 100.0]));
    assert!(s.instances.feature(zone).is_some(), "Bea is still in class");
    s.input(b.id, now, Input::LeaveInstance);
    assert!(s.instances.feature(zone).is_none());
    assert_eq!(s.ents.index_of(prof), None, "the professor went with it");
    assert_eq!(s.players(), s.ents.len());
}

#[test]
fn a_disconnect_or_a_close_empties_the_instance() {
    let mut s = sim();
    let a = join(&mut s, "Ada", House::Burrowsby, [100.0, 0.0, 100.0]);
    let b = join(&mut s, "Bea", House::Burrowsby, [102.0, 0.0, 100.0]);
    let (zone, _) = open_class(&mut s, &[a.id]);
    s.remove_player(a.id);
    assert!(s.instances.feature(zone).is_none());
    // A handler closing an instance sends everyone back first.
    let (zone, prof) = open_class(&mut s, &[b.id]);
    run(&mut s, move |ctx| ctx.close_instance(zone));
    assert!(s.instances.feature(zone).is_none());
    assert_eq!(s.ents.index_of(prof), None);
    assert_eq!(
        s.ents.zone[s.ents.index_of(b.id).unwrap()],
        super::super::entities::CASTLE
    );
}

#[test]
fn arenas_suspend_boosts_and_refuse_potions_and_social_casts() {
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
        ctx.apply_effect(
            id,
            ActiveEffect {
                fortune: None,
                kind: EffectKind::Shrinking,
                item_def: 3304,
                visible: Some(VisibleEffect::Scale { pct: 50 }),
                speed_pct: 100,
                until,
            },
        );
    });
    a.drain();
    let out = Arc::new(Mutex::new(None));
    let o = out.clone();
    run(&mut s, move |ctx| {
        let kind = FeatureKind::Arena {
            duel_id: 1,
            venue: "dueling_club".into(),
        };
        let zone = ctx.open_instance(kind, "duel_arena").unwrap();
        assert!(ctx.enter_instance(id, zone, Some(([-5.5, 0.9, 0.0], 1.5707964))));
        *o.lock().unwrap() = Some(zone);
    });
    let zone = out.lock().unwrap().unwrap();
    let speed = |msgs: &[ServerMsg]| {
        msgs.iter().rev().find_map(|m| match m {
            ServerMsg::OwnEffects { speed_pct, .. } => Some(*speed_pct),
            _ => None,
        })
    };
    let msgs = a.drain();
    let rules = crate::catalog::shipped().duel_rules.move_speed_pct;
    assert_eq!(speed(&msgs), Some(rules), "the arena's walking speed");
    let i = s.ents.index_of(id).unwrap();
    assert_eq!(
        s.ents.info[i].effects,
        vec![VisibleEffect::Steam],
        "no scale"
    );
    assert_eq!(s.ents.pos[i], [-5.5, 0.9, 0.0]);
    let now = Instant::now();
    s.input(id, now, Input::Potion(PotionInput::Drink { item_id: 5 }));
    s.input(
        id,
        now,
        Input::Spell(SpellInput::Cast {
            spell: 1,
            target: SpellTarget::None,
        }),
    );
    let msgs = a.drain();
    assert!(msgs.contains(&ServerMsg::PotionError {
        code: PotionErrorCode::Disabled,
        item_id: Some(5),
    }));
    assert!(msgs.contains(&ServerMsg::SpellError {
        spell: 1,
        code: SpellErrorCode::NotHere,
    }));
    assert!(s.take_ext_jobs().is_empty(), "no drink job started");
    assert!(!s.instances.feature(zone).unwrap().rules.potions);
    // Back out: the boosts return.
    s.input(id, now, Input::LeaveInstance);
    assert_eq!(speed(&a.drain()), Some(115));
    let i = s.ents.index_of(id).unwrap();
    assert!(
        s.ents.info[i]
            .effects
            .contains(&VisibleEffect::Scale { pct: 50 })
    );
}

#[test]
fn levels_and_lucerna_reach_viewers() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Roarwing, [100.0, 0.0, 100.0]);
    let mut b = join(&mut s, "Bea", House::Roarwing, [101.0, 0.0, 100.0]);
    let now = Instant::now();
    ticks(&mut s, now, 2);
    a.drain();
    b.drain();
    let id = a.id;
    let level2 = crate::catalog::shipped().progression.levels[1];
    let gain = protocol::XpGain {
        source: protocol::XpSource::ClassAttend,
        amount: level2,
        capped: false,
    };
    run(&mut s, move |ctx| {
        ctx.progress_changed(id, level2, Some(gain));
        assert_eq!(ctx.level(id), Some(2));
    });
    assert!(a.drain().iter().any(|m| matches!(
        m,
        ServerMsg::Progress {
            progress: protocol::Progress { level: 2, .. },
            gained: Some(_),
        }
    )));
    ticks(&mut s, now, 1);
    assert!(b.drain().contains(&ServerMsg::LevelChanged {
        id,
        level: 2,
        effects_version: 1,
    }));
    run(&mut s, move |ctx| ctx.set_lucerna(id, true));
    ticks(&mut s, now, 1);
    assert!(b.drain().contains(&ServerMsg::EffectsChanged {
        id,
        effects_version: 2,
        effects: vec![VisibleEffect::Lucerna],
    }));
    // From the HTTP side too.
    s.handle(WorldCmd::ProgressChanged {
        character_id: a.cid,
        xp: level2 + 1,
        gained: None,
    });
    assert!(
        a.drain()
            .iter()
            .any(|m| matches!(m, ServerMsg::Progress { .. }))
    );
}

#[test]
fn school_and_dueling_input_reach_their_handlers() {
    let mut s = sim();
    let t0 = Instant::now();
    s.set_clock(Arc::new(ServerClock::starting_at(1_800_000_000_000, t0)));
    let mut a = join(&mut s, "Ada", House::Burrowsby, [130.0, 0.0, 100.5]);
    let welcome = a.drain().into_iter().find_map(|m| match m {
        ServerMsg::Welcome { unix_ms, .. } => Some(unix_ms),
        _ => None,
    });
    let unix = welcome.expect("welcome first");
    assert!(
        (1_800_000_000_000..1_800_000_060_000).contains(&unix),
        "{unix}"
    );

    let now = Instant::now();
    s.input(a.id, now, Input::Interact("class_door_t".into()));
    s.input(
        a.id,
        now,
        Input::Class(ClassInput::Trace {
            round: 1,
            beat: 0,
            start_offset_ms: 0,
            points: vec![],
        }),
    );
    s.input(
        a.id,
        now,
        Input::Spell(SpellInput::Cast {
            spell: 1,
            target: SpellTarget::None,
        }),
    );
    s.input(a.id, now, Input::Duel(DuelMsg::List));
    let got = a.drain();
    // In this bare world the handlers answer with errors.
    assert!(got.contains(&ServerMsg::ClassError {
        code: ClassErrorCode::Failed
    }));
    assert!(got.iter().any(|m| matches!(
        m,
        ServerMsg::BeatScored {
            result: protocol::GestureResult {
                rejected: Some(TraceReject::NotInPractice),
                ..
            },
            ..
        }
    )));
    assert!(got.contains(&ServerMsg::SpellError {
        spell: 1,
        code: SpellErrorCode::NotLearned,
    }));
    // The duel handler: the list is for players in a dueling area.
    assert!(got.contains(&ServerMsg::DuelError {
        challenge_id: None,
        code: DuelErrorCode::NotInArea,
    }));
    // Areas and clocks for the handlers.
    let id = a.id;
    let i = s.ents.index_of(id).unwrap();
    s.ents.pos[i] = [150.0, 0.0, 100.0];
    run(&mut s, move |ctx| {
        assert_eq!(ctx.area(id, sim_core::AreaKind::DuelingClub), Some("club"));
        assert!(ctx.unix_ms() >= 1_800_000_000_000);
        assert_eq!(ctx.entity_by_name("ada"), Some(id));
        ctx.play_cast(id);
    });
    let i = s.ents.index_of(id).unwrap();
    assert_eq!(s.ents.state[i].anim, protocol::Anim::Cast);
}

#[test]
fn a_level_that_opens_a_loadout_slot_resends_the_spellbook() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Burrowsby, [130.0, 0.0, 100.5]);
    let i = s.ents.index_of(a.id).unwrap();
    let spells = &crate::catalog::shipped().spells;
    let learned = vec![10, 11, 12, 13, 14];
    s.ents.school[i].learned = learned.clone();
    s.ents.school[i].loadout = spells.default_loadout(&learned, 3);
    a.drain();
    let progression = &crate::catalog::shipped().progression;
    let level = |l: u8| progression.level_start(l);
    // Level 4 opens nothing: no spellbook.
    s.handle(WorldCmd::ProgressChanged {
        character_id: a.cid,
        xp: level(4),
        gained: None,
    });
    assert!(
        !a.drain()
            .iter()
            .any(|m| matches!(m, ServerMsg::Spellbook { .. }))
    );
    // Level 5 opens the fourth slot (a craft or a find would grant it just
    // the same): the default loadout grows into it.
    s.handle(WorldCmd::ProgressChanged {
        character_id: a.cid,
        xp: level(5),
        gained: None,
    });
    let book = a.drain().into_iter().find_map(|m| match m {
        ServerMsg::Spellbook { loadout, slots, .. } => Some((loadout, slots)),
        _ => None,
    });
    assert_eq!(book, Some((spells.default_loadout(&learned, 4), 4)));
    // A chosen loadout stays as it was when the fifth slot opens.
    s.ents.school[i].loadout = vec![12, 10];
    s.handle(WorldCmd::ProgressChanged {
        character_id: a.cid,
        xp: level(10),
        gained: None,
    });
    let book = a.drain().into_iter().find_map(|m| match m {
        ServerMsg::Spellbook { loadout, slots, .. } => Some((loadout, slots)),
        _ => None,
    });
    assert_eq!(book, Some((vec![12, 10], 5)));
}
