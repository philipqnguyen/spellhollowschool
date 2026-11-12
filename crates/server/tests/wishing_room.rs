//! The Wishing Room through the world (docs/architecture.md
//! "Secrets and the map"; `data/world/secrets.json` `wishing_room`):
//!
//! - pacing past the blank wall of the seventh-floor east corridor three
//!   times within a minute opens the door for the pacer alone, for 60 s;
//!   two passes, or a stroll off the corridor between, do not;
//! - the door takes you in only while it is open, within its reach: into
//!   the Lost Property Vault or the Gathering Room, one room per opener
//!   while it is occupied (entering again joins it), coming out in front
//!   of the door, the room closing once empty;
//! - friends come by invitation or, while the opener has it open, by a
//!   visit; the opener sends a guest out (who cannot walk straight back)
//!   and locks it; strangers never come in; at most 20 people; the opener
//!   going offline sends everyone out;
//! - the Lost Property Vault's heap gives one find a day per character.

#[path = "castle_harness.rs"]
mod castle;
mod common;

use castle::{Castle, DOOR, EAST, Seat, WEST};
use protocol::{DormErrorCode, FindOutcome, Location, ServerMsg, WishingRoomKind, Zone};
use server::realtime::world::Input;

fn door_opened(msgs: &[ServerMsg]) -> Option<u64> {
    msgs.iter().find_map(|m| match m {
        ServerMsg::WishingRoomDoor { open_until_ms } => Some(*open_until_ms),
        _ => None,
    })
}

fn dorm_errors(msgs: &[ServerMsg]) -> Vec<DormErrorCode> {
    msgs.iter()
        .filter_map(|m| match m {
            ServerMsg::DormError { code, .. } => Some(*code),
            _ => None,
        })
        .collect()
}

fn zone_changes(msgs: &[ServerMsg]) -> Vec<Zone> {
    msgs.iter()
        .filter_map(|m| match m {
            ServerMsg::ZoneChanged { zone, .. } => Some(zone.clone()),
            _ => None,
        })
        .collect()
}

/// Paces `seat` three times, walks to the door and enters `room`.
fn open_and_enter(w: &mut Castle, seat: &mut Seat, room: WishingRoomKind) -> Vec<ServerMsg> {
    w.walk(seat, WEST);
    w.pace(seat, 3);
    let opened = seat.msgs();
    assert!(door_opened(&opened).is_some_and(|t| t > 0), "{opened:?}");
    w.walk(seat, DOOR);
    w.input(seat, Input::EnterWishingRoom(room));
    seat.msgs()
}

#[test]
fn three_passes_within_a_minute_open_the_door_for_the_pacer() {
    let mut w = Castle::new(None);
    let mut ada = w.join(1, "Ada", WEST, vec![], Default::default());
    let mut bea = w.join(2, "Bea", DOOR, vec![], Default::default());
    ada.msgs();
    bea.msgs();
    // Two passes: nothing.
    w.pace(&mut ada, 2);
    assert_eq!(w.s.pacing_passes(1), Some(2));
    assert!(door_opened(&ada.msgs()).is_none());
    // The third: the door, for a minute, for Ada only.
    w.pace(&mut ada, 1);
    let until = door_opened(&ada.msgs()).expect("the door opens");
    // On reaching the east mark (the walk ends a step or two inside it).
    let until = until as i64;
    assert!((w.ms + 59_000..=w.ms + 60_000).contains(&until), "{until}");
    assert!(door_opened(&bea.msgs()).is_none(), "the door is Ada's only");
    assert_eq!(w.metrics.wishing_room_doors.get(), 1);
    // Bea cannot use Ada's door.
    w.input(&bea, Input::EnterWishingRoom(WishingRoomKind::Gathering));
    assert_eq!(dorm_errors(&bea.msgs()), vec![DormErrorCode::Closed]);
    // The counter started over: the next door takes three passes again.
    assert_eq!(w.s.pacing_passes(1), Some(0));
}

#[test]
fn slow_or_broken_pacing_opens_nothing() {
    let mut w = Castle::new(None);
    let mut ada = w.join(1, "Ada", WEST, vec![], Default::default());
    ada.msgs();
    // Three passes, but resting 25 s after each: over the minute.
    for _ in 0..3 {
        w.pace(&mut ada, 1);
        w.wait(25_000);
        let back = if ada.pos[0] > 0.0 { EAST } else { WEST };
        assert_eq!(ada.pos, back);
    }
    assert!(door_opened(&ada.msgs()).is_none());
    // Two passes, a step off the corridor (on the Grand Staircase's
    // seventh-floor landing), then one more: started over.
    let mut w = Castle::new(None);
    let mut bea = w.join(2, "Bea", WEST, vec![], Default::default());
    bea.msgs();
    w.pace(&mut bea, 2);
    assert_eq!(w.s.pacing_passes(2), Some(2));
    let gs = shipped_off_corridor(&w);
    w.teleport(&mut bea, gs);
    w.walk(&mut bea, [gs[0] + 0.3, gs[1], gs[2]]);
    assert_eq!(w.s.pacing_passes(2), None, "off the corridor: no counter");
    w.teleport(&mut bea, WEST);
    w.pace(&mut bea, 1);
    assert!(door_opened(&bea.msgs()).is_none());
}

/// A walkable castle point off the Wishing Room's corridor.
fn shipped_off_corridor(_w: &Castle) -> [f32; 3] {
    let world = castle::shipped_castle();
    let s = world
        .surfaces()
        .iter()
        .find(|s| s.id == "gs_landing_l7")
        .expect("the seventh-floor landing");
    let (x, z) = ((s.min[0] + s.max[0]) / 2.0, (s.min[1] + s.max[1]) / 2.0);
    [x, s.height_at(x, z), z]
}

#[test]
fn the_door_closes_after_a_minute_and_wants_you_near() {
    let mut w = Castle::new(None);
    let mut ada = w.join(1, "Ada", WEST, vec![], Default::default());
    w.pace(&mut ada, 3);
    assert!(door_opened(&ada.msgs()).is_some());
    // From the east mark: too far.
    w.input(&ada, Input::EnterWishingRoom(WishingRoomKind::LostProperty));
    assert_eq!(dorm_errors(&ada.msgs()), vec![DormErrorCode::TooFar]);
    w.walk(&mut ada, DOOR);
    // A minute after it opened: gone.
    w.wait(60_000);
    w.input(&ada, Input::EnterWishingRoom(WishingRoomKind::LostProperty));
    let msgs = ada.msgs();
    assert_eq!(dorm_errors(&msgs), vec![DormErrorCode::Closed]);
    assert!(zone_changes(&msgs).is_empty());
}

#[test]
fn each_room_opens_for_its_opener_and_closes_when_empty() {
    let mut w = Castle::new(None);
    let mut ada = w.join(1, "Ada", WEST, vec![], Default::default());
    let msgs = open_and_enter(&mut w, &mut ada, WishingRoomKind::LostProperty);
    assert_eq!(door_opened(&msgs), Some(0), "the door is used up");
    assert_eq!(
        zone_changes(&msgs),
        vec![Zone::WishingRoom {
            owner_name: "Ada".into(),
            room: WishingRoomKind::LostProperty,
            layout: "lost_property_vault".into(),
        }]
    );
    assert!(msgs.iter().any(|m| matches!(
        m,
        ServerMsg::DormState { open_to_friends: false, items, .. } if items.is_empty()
    )));
    w.wait(100);
    assert_eq!(w.metrics.wishing_room_instances.get(), 1.0);
    assert_eq!(w.metrics.wishing_room_entries.get(), 1);
    // In the room, the door is used up: entering again is refused.
    w.input(&ada, Input::EnterWishingRoom(WishingRoomKind::LostProperty));
    assert_eq!(dorm_errors(&ada.msgs()), vec![DormErrorCode::Closed]);
    // No furniture here.
    w.input(&ada, Input::DormRemove { item_id: 1 });
    assert_eq!(dorm_errors(&ada.msgs()), vec![DormErrorCode::NotInDorm]);
    // Out through the exit: in front of the door; the room closes.
    let exit =
        w.s.position(ada.id)
            .map(|_| server::realtime::layouts::InstanceLayouts::embedded())
            .unwrap()
            .get("lost_property_vault")
            .unwrap()
            .interactable("wishing_room_exit")
            .unwrap()
            .pos;
    assert!(w.teleport(&mut ada, exit));
    w.input(&ada, Input::Interact("wishing_room_exit".into()));
    let msgs = ada.msgs();
    assert_eq!(zone_changes(&msgs), vec![Zone::Castle]);
    let out = w.s.position(ada.id).unwrap();
    assert!(
        (out[0] - DOOR[0]).abs() < 1.0 && (out[1] - 63.0).abs() < 0.01,
        "{out:?}"
    );
    w.wait(100);
    assert_eq!(w.metrics.wishing_room_instances.get(), 0.0);
    // The Gathering Room the next time.
    ada.pos = out;
    let msgs = open_and_enter(&mut w, &mut ada, WishingRoomKind::Gathering);
    assert_eq!(
        zone_changes(&msgs),
        vec![Zone::WishingRoom {
            owner_name: "Ada".into(),
            room: WishingRoomKind::Gathering,
            layout: "wishing_room_gathering".into(),
        }]
    );
    // LeaveInstance works as well.
    w.input(&ada, Input::LeaveInstance);
    assert_eq!(zone_changes(&ada.msgs()), vec![Zone::Castle]);
}

#[test]
fn friends_come_by_invitation_or_visit_and_the_opener_kicks_and_locks() {
    let mut w = Castle::new(None);
    let mut ada = w.join(1, "Ada", WEST, vec![2, 3, 4], Default::default());
    let mut bea = w.join(2, "Bea", EAST, vec![1], Default::default());
    let mut cy = w.join(3, "Cy", EAST, vec![1], Default::default());
    let mut eve = w.join(5, "Eve", EAST, vec![], Default::default());
    open_and_enter(&mut w, &mut ada, WishingRoomKind::LostProperty);
    for s in [&mut bea, &mut cy, &mut eve] {
        s.msgs();
    }
    // An invitation from inside is into the room.
    w.input(&ada, Input::DormInvite("Bea".into()));
    let invite = bea
        .msgs()
        .iter()
        .find_map(|m| match m {
            ServerMsg::DormInviteReceived {
                invite_id,
                from_name,
                wishing_room,
                ..
            } => {
                assert_eq!(from_name, "Ada");
                assert_eq!(*wishing_room, Some(WishingRoomKind::LostProperty));
                Some(*invite_id)
            }
            _ => None,
        })
        .expect("invited");
    w.input(&bea, Input::DormAcceptInvite(invite));
    assert_eq!(
        zone_changes(&bea.msgs()),
        vec![Zone::WishingRoom {
            owner_name: "Ada".into(),
            room: WishingRoomKind::LostProperty,
            layout: "lost_property_vault".into(),
        }]
    );
    assert_eq!(
        w.s.presence_of(&[2])[&2].location,
        Location::WishingRoom {
            owner_name: "Ada".into()
        }
    );
    // A stranger is never invited; a visit while it is closed is refused.
    w.input(&ada, Input::DormInvite("Eve".into()));
    assert_eq!(dorm_errors(&ada.msgs()), vec![DormErrorCode::NotFriend]);
    w.input(&cy, Input::DormVisit("Ada".into()));
    assert_eq!(dorm_errors(&cy.msgs()), vec![DormErrorCode::Closed]);
    // Open to friends: Cy walks in; everyone inside hears it.
    w.input(&ada, Input::DormSetOpen(true));
    assert!(
        ada.msgs()
            .contains(&ServerMsg::DormOpenChanged { open: true })
    );
    assert!(
        bea.msgs()
            .contains(&ServerMsg::DormOpenChanged { open: true })
    );
    w.input(&cy, Input::DormVisit("Ada".into()));
    assert_eq!(zone_changes(&cy.msgs()).len(), 1);
    w.input(&eve, Input::DormVisit("Ada".into()));
    assert_eq!(dorm_errors(&eve.msgs()), vec![DormErrorCode::NotFriend]);
    // A guest cannot kick or lock.
    w.input(&bea, Input::DormKick(cy.id));
    assert_eq!(dorm_errors(&bea.msgs()), vec![DormErrorCode::UnknownTarget]);
    // The opener sends Bea out: in front of the door, and barred a while.
    w.input(&ada, Input::DormKick(bea.id));
    assert_eq!(zone_changes(&bea.msgs()), vec![Zone::Castle]);
    let out = w.s.position(bea.id).unwrap();
    assert!(
        (out[0] - DOOR[0]).abs() < 1.0 && (out[1] - 63.0).abs() < 0.01,
        "{out:?}"
    );
    w.input(&bea, Input::DormVisit("Ada".into()));
    assert_eq!(dorm_errors(&bea.msgs()), vec![DormErrorCode::Closed]);
    // Locked again: no more visits (to the room; Ada's dorm is closed too).
    w.input(&ada, Input::DormSetOpen(false));
    let mut dan = w.join(4, "Dan", EAST, vec![1], Default::default());
    w.input(&dan, Input::DormVisit("Ada".into()));
    assert_eq!(dorm_errors(&dan.msgs()), vec![DormErrorCode::Closed]);
    // The opener going offline sends everyone out; the room closes.
    cy.msgs();
    assert!(w.s.remove_player(ada.id));
    assert_eq!(zone_changes(&cy.msgs()), vec![Zone::Castle]);
    w.wait(100);
    assert_eq!(w.metrics.wishing_room_instances.get(), 0.0);
}

#[test]
fn entering_again_while_occupied_joins_the_room_and_it_holds_twenty() {
    let mut w = Castle::new(None);
    let friends: Vec<i64> = (2..=22).collect();
    let mut ada = w.join(1, "Ada", WEST, friends.clone(), Default::default());
    open_and_enter(&mut w, &mut ada, WishingRoomKind::Gathering);
    w.input(&ada, Input::DormSetOpen(true));
    let mut guests: Vec<Seat> = friends
        .iter()
        .map(|&id| w.join(id, &format!("Guest{id}"), EAST, vec![1], Default::default()))
        .collect();
    // Nineteen guests fill it to twenty; the twentieth guest finds it full.
    for (k, g) in guests.iter_mut().enumerate() {
        g.msgs();
        w.input(g, Input::DormVisit("Ada".into()));
        let msgs = g.msgs();
        if k < 19 {
            assert_eq!(zone_changes(&msgs).len(), 1, "guest {k}: {msgs:?}");
        } else {
            assert!(
                dorm_errors(&msgs).contains(&DormErrorCode::Full),
                "{msgs:?}"
            );
        }
        // The action bucket refills.
        w.wait(1_000);
    }
    // Ada steps out; the room stays while guests are in. She paces again
    // and asks for the other room: she joins hers.
    w.input(&ada, Input::LeaveInstance);
    assert_eq!(zone_changes(&ada.msgs()), vec![Zone::Castle]);
    ada.pos = w.s.position(ada.id).unwrap();
    // One guest leaves first, so there is room for her.
    w.input(&guests[0], Input::LeaveInstance);
    let msgs = open_and_enter(&mut w, &mut ada, WishingRoomKind::LostProperty);
    assert_eq!(
        zone_changes(&msgs),
        vec![Zone::WishingRoom {
            owner_name: "Ada".into(),
            room: WishingRoomKind::Gathering,
            layout: "wishing_room_gathering".into(),
        }]
    );
}

/// The Lost Property Vault's heap: one find a day per character.
#[tokio::test]
async fn the_heap_of_lost_things_gives_one_find_a_day() {
    let app = common::TestApp::dev().await;
    let pool = app.state.pool.clone();
    let p = app.player("Skytalon").await;
    let mut w = Castle::new(None);
    let mut ada = w.join(p.id, &p.name, WEST, vec![], Default::default());
    open_and_enter(&mut w, &mut ada, WishingRoomKind::LostProperty);
    let heap = server::realtime::layouts::InstanceLayouts::embedded()
        .get("lost_property_vault")
        .unwrap()
        .interactable("find_lost_property_vault")
        .unwrap()
        .pos;
    assert!(w.teleport(&mut ada, heap), "at the heap");
    ada.msgs();
    w.input(&ada, Input::Interact("find_lost_property_vault".into()));
    assert_eq!(w.run_jobs(&pool).await, 1);
    let result = |msgs: Vec<ServerMsg>| {
        msgs.into_iter().find_map(|m| match m {
            ServerMsg::FindResult { spot, outcome } => {
                assert_eq!(spot, "find_lost_property_vault");
                Some(outcome)
            }
            _ => None,
        })
    };
    let first = result(ada.msgs()).expect("a result");
    assert!(
        matches!(first, FindOutcome::Found { .. }),
        "the heap always gives something: {first:?}"
    );
    w.wait(1_000);
    w.input(&ada, Input::Interact("find_lost_property_vault".into()));
    w.run_jobs(&pool).await;
    let second = result(ada.msgs()).expect("a result");
    assert!(
        matches!(second, FindOutcome::Cooldown { ready_in_secs } if ready_in_secs > 80_000),
        "{second:?}"
    );
    server::ledger::audit::reconcile(&pool, &[p.id])
        .await
        .unwrap()
        .assert_clean();
}
