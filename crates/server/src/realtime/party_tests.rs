//! Tests of parties (`party.rs`): the rules on their own ([`Parties`]) and
//! in the world (invitations, leaving, leadership, chat, visibility),
//! driven directly as in `world_tests.rs`.

use std::time::{Duration, Instant};

use protocol::{
    ChatChannel, ChatRejectReason, EntityEntry, House, PartyErrorCode, PartyNotice, PartyPlace,
    PartyView, ServerMsg,
};

use super::*;
use crate::realtime::entities::Social;
use crate::realtime::world::tests::{
    TestClient, character, in_dorm, join, join_as, join_with, sim, ticks,
};
use crate::realtime::world::{Input, Joining};

// ---------------------------------------------------------------- the rules

fn member(id: i64) -> Member {
    Member {
        character_id: id,
        name: format!("M{id}"),
        house: House::Skytalon,
        level: 1,
        offline_since: None,
    }
}

/// A party of `ids` led by the first, built by invitations.
fn party_of(p: &mut Parties, ids: &[i64], now: Instant) -> PartyId {
    let mut id = 0;
    for &c in &ids[1..] {
        let inv = p.invite(ids[0], c, now).unwrap();
        assert_eq!(p.take_invite(inv.id, c, now), Some(inv));
        id = p.join(member(ids[0]), member(c)).unwrap();
    }
    id
}

fn order(p: &Parties, id: PartyId) -> Vec<i64> {
    p.get(id)
        .unwrap()
        .ordered()
        .map(|m| m.character_id)
        .collect()
}

#[test]
fn the_list_is_leader_first_then_joining_order() {
    let now = Instant::now();
    let mut p = Parties::default();
    let id = party_of(&mut p, &[1, 2, 3, 4], now);
    assert_eq!(order(&p, id), vec![1, 2, 3, 4]);
    p.promote(1, 3, true).unwrap();
    assert_eq!(order(&p, id), vec![3, 1, 2, 4]);
    // The leader leaving passes it to the first member in joining order.
    p.leave(3).unwrap();
    assert_eq!(p.get(id).unwrap().leader, 1);
    assert_eq!(order(&p, id), vec![1, 2, 4]);
}

#[test]
fn six_at_most() {
    let now = Instant::now();
    let mut p = Parties::default();
    let id = party_of(&mut p, &[1, 2, 3, 4, 5], now);
    // Two invitations out while there is one place left.
    let a = p.invite(1, 6, now).unwrap();
    let b = p.invite(2, 7, now).unwrap();
    p.take_invite(a.id, 6, now).unwrap();
    p.join(member(1), member(6)).unwrap();
    assert_eq!(p.get(id).unwrap().members.len(), PARTY_MAX);
    p.take_invite(b.id, 7, now).unwrap();
    assert_eq!(p.join(member(2), member(7)), Err(PartyErrorCode::Full));
    assert_eq!(p.invite(1, 8, now), Err(PartyErrorCode::Full));
    assert!(p.of(7).is_none());
}

#[test]
fn invitations_lapse_are_capped_and_answer_only_their_target() {
    let now = Instant::now();
    let mut p = Parties::default();
    assert_eq!(p.invite(1, 1, now), Err(PartyErrorCode::SelfInvite));
    let a = p.invite(1, 2, now).unwrap();
    assert_eq!(p.take_invite(a.id, 3, now), None, "not theirs");
    // A newer invitation between the same two replaces the older.
    let b = p.invite(1, 2, now).unwrap();
    assert_eq!(p.invites().len(), 1);
    assert_eq!(p.take_invite(a.id, 2, now), None);
    assert_eq!(p.take_invite(b.id, 2, now + INVITE_TTL), None, "lapsed");
    for to in 10..30 {
        p.invite(1, to, now).unwrap();
    }
    assert_eq!(p.invites().len(), INVITES_PER_SENDER);
    for from in 40..60 {
        p.invite(from, 99, now).unwrap();
    }
    assert_eq!(
        p.invites().iter().filter(|i| i.to == 99).count(),
        INVITES_PER_TARGET
    );
    p.expire(now + INVITE_TTL);
    assert!(p.invites().is_empty());
}

#[test]
fn offline_members_stay_a_while_and_an_offline_leader_hands_over() {
    let now = Instant::now();
    let mut p = Parties::default();
    let id = party_of(&mut p, &[1, 2, 3], now);
    p.take_notices();
    p.set_online(1, false, 1, now);
    assert_eq!(p.get(id).unwrap().leader, 2, "the next member online leads");
    p.set_online(2, false, 1, now);
    p.set_online(3, false, 1, now);
    // Nobody online to hand over to: 3 keeps leading.
    p.set_online(1, true, 5, now);
    assert_eq!(p.get(id).unwrap().member(1).unwrap().level, 5);
    p.expire(now + OFFLINE_KEEP - Duration::from_secs(1));
    assert_eq!(p.get(id).unwrap().members.len(), 3);
    p.expire(now + OFFLINE_KEEP);
    // 2 and 3 stayed away too long: 1 is alone, so the party is gone.
    assert!(p.of(1).is_none() && p.is_empty());
    assert!(p.take_notices().contains(&(1, PartyNotice::Disbanded)));
}

// ---------------------------------------------------------------- in the world

const HALL: [f32; 3] = [100.0, 0.0, 100.0];

fn act(s: &mut WorldSim, c: &TestClient, input: Input) {
    s.input(c.id, Instant::now(), input);
}

fn invite(s: &mut WorldSim, from: &TestClient, name: &str) {
    act(s, from, Input::PartyInvite(name.into()));
}

/// The invitation `c` received last.
fn invited(c: &mut TestClient) -> u32 {
    c.drain()
        .into_iter()
        .rev()
        .find_map(|m| match m {
            ServerMsg::PartyInvited { invite_id, .. } => Some(invite_id),
            _ => None,
        })
        .expect("an invitation arrived")
}

fn accept(s: &mut WorldSim, c: &mut TestClient) {
    let invite_id = invited(c);
    act(
        s,
        c,
        Input::PartyRespond {
            invite_id,
            accept: true,
        },
    );
}

struct Got {
    states: Vec<Option<PartyView>>,
    notices: Vec<PartyNotice>,
    errors: Vec<PartyErrorCode>,
    chats: Vec<(ChatChannel, String)>,
    rejected: Vec<ChatRejectReason>,
}

fn got(c: &mut TestClient) -> Got {
    let mut g = Got {
        states: vec![],
        notices: vec![],
        errors: vec![],
        chats: vec![],
        rejected: vec![],
    };
    for m in c.drain() {
        match m {
            ServerMsg::PartyState { party } => g.states.push(party),
            ServerMsg::PartyNotice { notice } => g.notices.push(notice),
            ServerMsg::PartyError { code } => g.errors.push(code),
            ServerMsg::Chat { channel, text, .. } => g.chats.push((channel, text)),
            ServerMsg::ChatRejected { reason, .. } => g.rejected.push(reason),
            _ => {}
        }
    }
    g
}

fn names(v: &PartyView) -> Vec<String> {
    v.members.iter().map(|m| m.name.clone()).collect()
}

/// `a` invites each of `others` and they accept; every queue is drained.
fn form(s: &mut WorldSim, a: &mut TestClient, others: &mut [(&mut TestClient, &str)]) {
    for (c, name) in others.iter_mut() {
        invite(s, a, name);
        accept(s, c);
    }
    a.drain();
    for (c, _) in others.iter_mut() {
        c.drain();
    }
}

#[test]
fn invite_and_accept_make_a_party_both_see() {
    let mut s = sim();
    let mut ada = join(&mut s, "Ada", House::Skytalon, HALL);
    let mut bea = join(&mut s, "Bea", House::Roarwing, [101.0, 0.0, 100.0]);
    ada.drain();
    bea.drain();

    invite(&mut s, &ada, "bea");
    let a = got(&mut ada);
    assert_eq!(
        a.notices,
        vec![PartyNotice::InviteSent { name: "Bea".into() }]
    );
    let b = bea.drain();
    assert!(b.iter().any(|m| matches!(
        m,
        ServerMsg::PartyInvited { from_name, from_house: House::Skytalon, expires_in_secs: 120, .. } if from_name == "Ada"
    )));
    let invite_id = b
        .iter()
        .find_map(|m| match m {
            ServerMsg::PartyInvited { invite_id, .. } => Some(*invite_id),
            _ => None,
        })
        .unwrap();
    act(
        &mut s,
        &bea,
        Input::PartyRespond {
            invite_id,
            accept: true,
        },
    );
    for c in [&mut ada, &mut bea] {
        let g = got(c);
        let view = g.states.last().unwrap().clone().expect("in a party");
        assert_eq!(names(&view), vec!["Ada", "Bea"], "leader first");
        assert_eq!(view.leader, view.members[0].character_id);
        let online = view.members[1].online.as_ref().expect("online");
        assert_eq!(online.zone, 0);
        assert_eq!(
            online.place,
            PartyPlace::Castle {
                place: "Hall".into()
            }
        );
        assert_eq!(g.notices, vec![PartyNotice::Joined { name: "Bea".into() }]);
    }
    // The invitation is used up.
    act(
        &mut s,
        &bea,
        Input::PartyRespond {
            invite_id,
            accept: true,
        },
    );
    assert_eq!(got(&mut bea).errors, vec![PartyErrorCode::InviteExpired]);
    // Nobody else hears of it.
    let mut cy = join(&mut s, "Cy", House::Skytalon, [102.0, 0.0, 100.0]);
    ticks(&mut s, 2);
    let c = got(&mut cy);
    assert!(c.states.is_empty() && c.notices.is_empty());
}

#[test]
fn declining_tells_the_inviter_and_invitations_lapse() {
    let mut s = sim();
    let mut ada = join(&mut s, "Ada", House::Skytalon, HALL);
    let mut bea = join(&mut s, "Bea", House::Burrowsby, HALL);
    invite(&mut s, &ada, "Bea");
    let invite_id = invited(&mut bea);
    ada.drain();
    act(
        &mut s,
        &bea,
        Input::PartyRespond {
            invite_id,
            accept: false,
        },
    );
    let a = got(&mut ada);
    assert_eq!(
        a.notices,
        vec![PartyNotice::Declined { name: "Bea".into() }]
    );
    assert!(a.states.is_empty());
    assert!(s.parties.is_empty());

    // Two minutes later an invitation can no longer be accepted.
    invite(&mut s, &ada, "Bea");
    let invite_id = invited(&mut bea);
    let late = Instant::now() + INVITE_TTL + Duration::from_secs(1);
    s.input(
        bea.id,
        late,
        Input::PartyRespond {
            invite_id,
            accept: true,
        },
    );
    assert_eq!(got(&mut bea).errors, vec![PartyErrorCode::InviteExpired]);
    assert!(s.parties.is_empty());
    // Unknown names, yourself, NPC-free: refused.
    invite(&mut s, &ada, "Nobody");
    invite(&mut s, &ada, "ada");
    assert_eq!(
        got(&mut ada).errors,
        vec![PartyErrorCode::NotOnline, PartyErrorCode::SelfInvite]
    );
}

#[test]
fn a_party_holds_six_at_most() {
    let mut s = sim();
    let mut lead = join(&mut s, "Lead", House::Skytalon, HALL);
    let mut rest: Vec<TestClient> = (1..=6)
        .map(|n| join(&mut s, &format!("P{n}"), House::ALL[n % 4], HALL))
        .collect();
    for (n, c) in rest.iter_mut().enumerate().take(5) {
        invite(&mut s, &lead, &format!("P{}", n + 1));
        accept(&mut s, c);
    }
    let view = got(&mut lead).states.last().unwrap().clone().unwrap();
    assert_eq!(view.members.len(), 6);
    invite(&mut s, &lead, "P6");
    assert_eq!(got(&mut lead).errors, vec![PartyErrorCode::Full]);
    // A member's invitation is refused the same way.
    invite(&mut s, &rest[0], "P6");
    assert_eq!(got(&mut rest[0]).errors, vec![PartyErrorCode::Full]);
    // Someone already in a party cannot be invited.
    let mut out = join(&mut s, "Out", House::Roarwing, HALL);
    invite(&mut s, &out, "P2");
    assert_eq!(got(&mut out).errors, vec![PartyErrorCode::InParty]);
}

#[test]
fn leadership_is_handed_over_and_passes_on() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, HALL);
    let mut b = join(&mut s, "Bea", House::Skytalon, HALL);
    let mut c = join(&mut s, "Cy", House::Skytalon, HALL);
    form(&mut s, &mut a, &mut [(&mut b, "Bea"), (&mut c, "Cy")]);

    // Only the leader hands over or removes.
    act(&mut s, &b, Input::PartyPromote(c.cid));
    act(&mut s, &b, Input::PartyKick(c.cid));
    assert_eq!(
        got(&mut b).errors,
        vec![PartyErrorCode::NotLeader, PartyErrorCode::NotLeader]
    );
    act(&mut s, &a, Input::PartyPromote(c.cid));
    for x in [&mut a, &mut b, &mut c] {
        let g = got(x);
        assert_eq!(g.notices, vec![PartyNotice::Leader { name: "Cy".into() }]);
        assert_eq!(
            names(&g.states.last().unwrap().clone().unwrap()),
            vec!["Cy", "Ada", "Bea"]
        );
    }
    // The leader leaves: the next member in joining order leads.
    act(&mut s, &c, Input::PartyLeave);
    let gc = got(&mut c);
    assert_eq!(gc.states, vec![None]);
    assert_eq!(gc.notices, vec![PartyNotice::Left { name: "Cy".into() }]);
    let ga = got(&mut a);
    assert!(
        ga.notices
            .contains(&PartyNotice::Left { name: "Cy".into() })
    );
    assert!(
        ga.notices
            .contains(&PartyNotice::Leader { name: "Ada".into() })
    );
    assert_eq!(
        names(&ga.states.last().unwrap().clone().unwrap()),
        vec!["Ada", "Bea"]
    );

    // The leader going offline hands over to someone online.
    let mut d = join(&mut s, "Di", House::Roarwing, HALL);
    invite(&mut s, &a, "Di");
    accept(&mut s, &mut d);
    b.drain();
    s.remove_player(a.id);
    let gb = got(&mut b);
    assert!(
        gb.notices
            .contains(&PartyNotice::Leader { name: "Bea".into() })
    );
    let view = gb.states.last().unwrap().clone().unwrap();
    assert_eq!(names(&view), vec!["Bea", "Ada", "Di"]);
    assert!(view.members[1].online.is_none(), "Ada is shown offline");
}

#[test]
fn the_leader_removes_members_and_a_party_of_one_disbands() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, HALL);
    let mut b = join(&mut s, "Bea", House::Skytalon, HALL);
    let mut c = join(&mut s, "Cy", House::Skytalon, HALL);
    form(&mut s, &mut a, &mut [(&mut b, "Bea"), (&mut c, "Cy")]);

    act(&mut s, &a, Input::PartyKick(a.cid));
    assert_eq!(got(&mut a).errors, vec![PartyErrorCode::NotMember]);
    act(&mut s, &a, Input::PartyKick(c.cid));
    a.drain();
    let gc = got(&mut c);
    assert_eq!(gc.states, vec![None]);
    assert_eq!(gc.notices, vec![PartyNotice::Removed { name: "Cy".into() }]);
    let gb = got(&mut b);
    assert_eq!(gb.notices, vec![PartyNotice::Removed { name: "Cy".into() }]);
    assert_eq!(
        names(&gb.states.last().unwrap().clone().unwrap()),
        vec!["Ada", "Bea"]
    );

    // Bea leaves: Ada is alone, so the party is gone.
    act(&mut s, &b, Input::PartyLeave);
    let ga = got(&mut a);
    assert_eq!(ga.states, vec![None]);
    assert_eq!(
        ga.notices,
        vec![
            PartyNotice::Left { name: "Bea".into() },
            PartyNotice::Disbanded
        ]
    );
    assert!(s.parties.is_empty());
    act(&mut s, &a, Input::PartyLeave);
    assert_eq!(got(&mut a).errors, vec![PartyErrorCode::NotInParty]);
}

#[test]
fn blocked_players_cannot_invite_each_other() {
    let mut s = sim();
    let ada = character("Ada", House::Skytalon, Some(HALL));
    let bea = character("Bea", House::Roarwing, Some(HALL));
    let (ada_id, bea_id) = (ada.id, bea.id);
    let mut a = join_with(
        &mut s,
        Joining {
            social: Social {
                blocked: vec![bea_id],
                ..Social::default()
            },
            ..Joining::alone(ada)
        },
        256,
    );
    let mut b = join_with(
        &mut s,
        Joining {
            social: Social {
                blocked_by: vec![ada_id],
                ..Social::default()
            },
            ..Joining::alone(bea)
        },
        256,
    );
    a.drain();
    b.drain();
    invite(&mut s, &a, "Bea");
    assert_eq!(got(&mut a).errors, vec![PartyErrorCode::Blocked]);
    // The blocked one sees the blocker as not online (blocks stay private).
    invite(&mut s, &b, "Ada");
    assert_eq!(got(&mut b).errors, vec![PartyErrorCode::NotOnline]);
    assert!(got(&mut a).notices.is_empty() && s.parties.invites().is_empty());
}

#[test]
fn party_chat_reaches_every_member_anywhere_and_nobody_else() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, HALL);
    // Bea is in her dorm (another zone), Cy far across the hall.
    let mut b = join_as(&mut s, in_dorm("Bea", House::Roarwing), 256);
    let mut c = join(&mut s, "Cy", House::Burrowsby, [5.0, 0.0, 195.0]);
    // Di stands right next to Ada but is not in the party; Eve shares Ada's house.
    let mut d = join(&mut s, "Di", House::Skytalon, [100.5, 0.0, 100.0]);
    let mut e = join(&mut s, "Eve", House::Skytalon, [150.0, 0.0, 100.0]);
    form(&mut s, &mut a, &mut [(&mut b, "Bea"), (&mut c, "Cy")]);
    d.drain();
    e.drain();

    let line = |s: &mut WorldSim, from: &TestClient, text: &str, seq| {
        s.input(
            from.id,
            Instant::now(),
            Input::Chat {
                channel: ChatChannel::Party,
                text: text.into(),
                client_seq: seq,
            },
        );
        ticks(s, 1);
    };
    line(&mut s, &a, "meet at the library", 1);
    for x in [&mut a, &mut b, &mut c] {
        assert_eq!(
            got(x).chats,
            vec![(ChatChannel::Party, "meet at the library".into())]
        );
    }
    for x in [&mut d, &mut e] {
        assert!(got(x).chats.is_empty());
    }
    // From the dorm too.
    line(&mut s, &b, "coming", 2);
    assert_eq!(
        got(&mut c).chats,
        vec![(ChatChannel::Party, "coming".into())]
    );
    // The speaker gets its own line back (the client skips it), as with say.
    assert_eq!(got(&mut b).chats.len(), 1);
    a.drain();
    assert!(got(&mut d).chats.is_empty());

    // The word filter and the rate limit apply as everywhere.
    line(&mut s, &a, "a badword", 3);
    assert_eq!(got(&mut a).rejected, vec![ChatRejectReason::Filtered]);
    assert!(got(&mut b).chats.is_empty());
    for n in 0..6 {
        line(&mut s, &c, "spam", 10 + n);
    }
    assert_eq!(got(&mut c).rejected, vec![ChatRejectReason::RateLimited]);
    assert_eq!(got(&mut a).chats.len(), 5, "the burst got through");
    b.drain();

    // A member who blocked the speaker does not hear them.
    let bi = s.ents.index_of(b.id).unwrap();
    s.ents.social[bi].blocked = vec![c.cid];
    let at = Instant::now() + Duration::from_secs(10);
    s.input(
        c.id,
        at,
        Input::Chat {
            channel: ChatChannel::Party,
            text: "anyone?".into(),
            client_seq: 30,
        },
    );
    ticks(&mut s, 1);
    assert_eq!(got(&mut a).chats.len(), 1);
    assert!(got(&mut b).chats.is_empty());

    // Outside a party there is nobody to talk to.
    line(&mut s, &d, "hello?", 40);
    assert_eq!(got(&mut d).rejected, vec![ChatRejectReason::NoChannel]);
    assert!(got(&mut a).chats.is_empty());
}

/// Entity ids that entered `c`'s view since the last drain.
fn entered(c: &mut TestClient) -> Vec<u32> {
    c.drain()
        .into_iter()
        .flat_map(|m| match m {
            ServerMsg::Snapshot { entered, .. } => entered,
            _ => vec![],
        })
        .map(|e| match e {
            EntityEntry::Full(info) => info.id,
            EntityEntry::Cached { id, .. } => id,
        })
        .collect()
}

#[test]
fn members_see_each_other_whatever_the_distance_and_others_do_not() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, [10.0, 0.0, 10.0]);
    // A crowd around Ada fills her view limit.
    let _crowd: Vec<TestClient> = (0..6)
        .map(|n| {
            join(
                &mut s,
                &format!("C{n}"),
                House::Burrowsby,
                [11.0 + n as f32 * 0.5, 0.0, 10.0],
            )
        })
        .collect();
    let mut b = join(&mut s, "Bea", House::Roarwing, [190.0, 0.0, 190.0]);
    let mut stranger = join(&mut s, "Sid", House::Roarwing, [189.0, 0.0, 190.0]);
    for c in [&a, &b, &stranger] {
        s.input(c.id, Instant::now(), Input::ViewLimit(3));
    }
    ticks(&mut s, 4);
    assert!(!entered(&mut a).contains(&b.id), "too far without a party");
    form(&mut s, &mut a, &mut [(&mut b, "Bea")]);
    ticks(&mut s, 4);
    assert!(
        entered(&mut a).contains(&b.id),
        "the party member is in view"
    );
    assert!(entered(&mut b).contains(&a.id));
    // Bea's neighbour, not in the party, still does not see Ada.
    assert!(!entered(&mut stranger).contains(&a.id));
    // Leaving the party ends it.
    act(&mut s, &b, Input::PartyLeave);
    ticks(&mut s, 4);
    let i = s.ents.index_of(a.id).unwrap();
    assert!(!s.ents.client[i].visible.contains(&b.id));
}

#[test]
fn members_elsewhere_are_named_by_their_place_and_followed_across_a_relog() {
    let mut s = sim();
    let mut a = join(&mut s, "Ada", House::Skytalon, HALL);
    let bea = in_dorm("Bea", House::Roarwing);
    let bea_char = bea.clone();
    let mut b = join_as(&mut s, bea, 256);
    form(&mut s, &mut a, &mut [(&mut b, "Bea")]);
    ticks(&mut s, 1);
    let view = got(&mut a);
    assert!(view.states.is_empty(), "nothing changed");
    let i = s.ents.index_of(b.id).unwrap();
    let p = s.parties.of(a.cid).unwrap();
    let v = s.party_view(p);
    let bp = v.members[1].online.clone().unwrap();
    assert_eq!(
        bp.place,
        PartyPlace::Dorm {
            owner_name: "Bea".into(),
            house: House::Roarwing
        }
    );
    assert_eq!(bp.zone, s.ents.zone[i]);
    assert_ne!(bp.zone, 0);

    // Bea signs out: shown offline, still a member; back in, online again.
    s.remove_player(b.id);
    let v = got(&mut a).states.last().unwrap().clone().unwrap();
    assert!(v.members[1].online.is_none());
    let mut b = join_as(&mut s, bea_char, 256);
    s.settle(Instant::now());
    let gb = got(&mut b);
    let v = gb
        .states
        .last()
        .unwrap()
        .clone()
        .expect("still in the party");
    assert_eq!(names(&v), vec!["Ada", "Bea"]);
    assert_eq!(v.members[1].online.as_ref().unwrap().entity, b.id);
    assert!(
        got(&mut a).states.last().unwrap().clone().unwrap().members[1]
            .online
            .is_some()
    );
    let ia = s.ents.index_of(a.id).unwrap();
    assert_eq!(s.ents.client[ia].party, vec![b.id]);
}
