//! The social hotbar on the world thread (protocol v16): sent in
//! `Spellbook` after `Welcome`, set with `SetHotbar` (checked, answered by
//! `Hotbar`, a change handed to the saver), and spells learned while online
//! added to the first empty slot.

use std::time::Instant;

use protocol::{HOTBAR_SLOTS, House, ServerMsg, SpellId};
use sim_core::spells::Hotbar;
use tokio::sync::mpsc;

use super::tests::{TestClient, character, join_with};
use super::{Input, Joining, WorldCmd, WorldSim};
use crate::catalog::shipped;
use crate::progression::SchoolState;
use crate::realtime::persist::SaveBatch;

fn id(key: &str) -> SpellId {
    shipped().spells.by_key(key).unwrap().id
}

/// A world with a saver, and Ada in it with `learned` and `hotbar`.
fn ada(
    learned: &[&str],
    hotbar: Hotbar,
) -> (WorldSim, TestClient, mpsc::UnboundedReceiver<SaveBatch>) {
    let mut s = super::tests::sim();
    let (tx, saves) = mpsc::unbounded_channel();
    s.saver = Some(tx);
    let joining = Joining {
        school: SchoolState {
            learned: learned.iter().map(|k| id(k)).collect(),
            hotbar,
            ..Default::default()
        },
        ..Joining::alone(character(
            "Ada",
            House::Burrowsby,
            Some([130.0, 0.0, 100.5]),
        ))
    };
    let c = join_with(&mut s, joining, 256);
    (s, c, saves)
}

fn wire(bar: &Hotbar) -> Vec<Option<SpellId>> {
    bar.to_vec()
}

fn hotbar_msgs(c: &mut TestClient) -> Vec<Vec<Option<SpellId>>> {
    c.drain()
        .into_iter()
        .filter_map(|m| match m {
            ServerMsg::Hotbar { slots } => Some(slots),
            _ => None,
        })
        .collect()
}

fn saved(saves: &mut mpsc::UnboundedReceiver<SaveBatch>) -> Vec<(i64, Hotbar)> {
    std::iter::from_fn(|| saves.try_recv().ok())
        .flat_map(|b| b.hotbars)
        .collect()
}

fn set(s: &mut WorldSim, c: &TestClient, slots: Vec<Option<SpellId>>) {
    s.input(c.id, Instant::now(), Input::SetHotbar(slots));
}

#[test]
fn the_spellbook_after_welcome_carries_the_hotbar() {
    let mut bar: Hotbar = [None; HOTBAR_SLOTS];
    bar[3] = Some(id("lucerna"));
    let (_s, mut c, _saves) = ada(&["lucerna", "torpeo"], bar);
    let hotbar = c.drain().into_iter().find_map(|m| match m {
        ServerMsg::Spellbook { hotbar, .. } => Some(hotbar),
        _ => None,
    });
    assert_eq!(hotbar, Some(wire(&bar)));
}

#[test]
fn set_hotbar_stores_learned_social_spells_and_saves_them() {
    let (mut s, mut c, mut saves) = ada(&["lucerna", "elevara", "torpeo"], [None; HOTBAR_SLOTS]);
    c.drain();
    // Two slots are enough: the rest are empty.
    set(
        &mut s,
        &c,
        vec![Some(id("elevara")), None, Some(id("lucerna"))],
    );
    let mut want: Hotbar = [None; HOTBAR_SLOTS];
    want[0] = Some(id("elevara"));
    want[2] = Some(id("lucerna"));
    assert_eq!(hotbar_msgs(&mut c), vec![wire(&want)]);
    assert_eq!(saved(&mut saves), vec![(c.cid, want)]);
    let i = s.ents.index_of(c.id).unwrap();
    assert_eq!(s.ents.school[i].hotbar, want);
    // The same layout again: answered, nothing to save.
    set(&mut s, &c, want.to_vec());
    assert_eq!(hotbar_msgs(&mut c), vec![wire(&want)]);
    assert!(saved(&mut saves).is_empty());
}

#[test]
fn set_hotbar_refuses_and_answers_with_the_kept_layout() {
    let mut bar: Hotbar = [None; HOTBAR_SLOTS];
    bar[0] = Some(id("lucerna"));
    let (mut s, mut c, mut saves) = ada(&["lucerna", "elevara", "torpeo"], bar);
    c.drain();
    for bad in [
        // A flourish (a dueling spell) does nothing outside duels.
        vec![Some(id("torpeo"))],
        // Not learned.
        vec![Some(id("custodio"))],
        // Twice.
        vec![Some(id("elevara")), Some(id("elevara"))],
        // Too many slots.
        vec![None; HOTBAR_SLOTS + 1],
        // An unknown spell.
        vec![Some(999)],
    ] {
        set(&mut s, &c, bad.clone());
        assert_eq!(hotbar_msgs(&mut c), vec![wire(&bar)], "{bad:?}");
    }
    assert!(saved(&mut saves).is_empty());
}

#[test]
fn spells_learned_online_fill_the_first_empty_slot_only_then() {
    let mut bar: Hotbar = [None; HOTBAR_SLOTS];
    bar[0] = Some(id("lucerna"));
    bar[1] = Some(id("elevara"));
    let (mut s, mut c, mut saves) = ada(&["lucerna", "elevara", "tinctura"], bar);
    c.drain();
    // The player takes Elevara off (Tinctura, learned, was never on).
    set(&mut s, &c, vec![Some(id("lucerna"))]);
    c.drain();
    saved(&mut saves);
    // Adveni and a flourish are learned: Adveni goes into slot 2, the
    // first empty one; Elevara stays off.
    let learned = ["lucerna", "elevara", "tinctura", "adveni", "repulsa"].map(id);
    s.handle(WorldCmd::SpellbookChanged {
        character_id: c.cid,
        learned: learned.to_vec(),
        loadout: Vec::new(),
        new: vec![id("repulsa"), id("adveni")],
    });
    let mut want: Hotbar = [None; HOTBAR_SLOTS];
    want[0] = Some(id("lucerna"));
    want[1] = Some(id("adveni"));
    let book = c.drain().into_iter().find_map(|m| match m {
        ServerMsg::Spellbook { hotbar, new, .. } => Some((hotbar, new)),
        _ => None,
    });
    assert_eq!(book, Some((wire(&want), vec![id("repulsa"), id("adveni")])));
    assert_eq!(saved(&mut saves), vec![(c.cid, want)]);
    // A spellbook change that teaches nothing new leaves the bar alone.
    s.handle(WorldCmd::SpellbookChanged {
        character_id: c.cid,
        learned: learned.to_vec(),
        loadout: Vec::new(),
        new: Vec::new(),
    });
    assert!(saved(&mut saves).is_empty());
    let i = s.ents.index_of(c.id).unwrap();
    assert_eq!(s.ents.school[i].hotbar, want);
}
