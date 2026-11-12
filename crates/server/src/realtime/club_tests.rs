//! Tests of clubs in the world (`club.rs`), without a database: the
//! database's answers ([`Outcome`]s) are handed in as a job would bring them
//! back, and the jobs actions start are counted.

use std::time::{Duration, Instant};

use protocol::{
    ChatChannel, ChatRejectReason, ClubErrorCode, ClubNotice, ClubRole, ClubView, House, ServerMsg,
};

use super::*;
use crate::characters::Character;
use crate::clubs::Member;
use crate::realtime::entities::Social;
use crate::realtime::world::tests::{TestClient, character, in_dorm, join, join_with, sim, ticks};
use crate::realtime::world::{Input, Joining, WorldCmd};

const HALL: [f32; 3] = [100.0, 0.0, 100.0];

fn club_of(id: i64, version: i64, members: &[(&Character, ClubRole)]) -> Club {
    Club {
        id,
        name: "The Night Owls".into(),
        tag: "OWL".into(),
        motd: String::new(),
        version,
        members: members
            .iter()
            .map(|(c, role)| Member {
                character_id: c.id,
                name: c.name.clone(),
                house: c.house,
                level: 3,
                role: *role,
            })
            .collect(),
    }
}

fn join_in(s: &mut WorldSim, ch: Character, club: Option<Club>) -> TestClient {
    join_with(
        s,
        Joining {
            club,
            ..Joining::alone(ch)
        },
        256,
    )
}

fn act(s: &mut WorldSim, c: &TestClient, input: ClubInput) {
    s.input(c.id, Instant::now(), Input::Club(input));
}

#[derive(Default)]
struct Got {
    states: Vec<Option<ClubView>>,
    presence: Vec<(i64, bool)>,
    notices: Vec<ClubNotice>,
    errors: Vec<ClubErrorCode>,
    invited: Vec<u32>,
    chats: Vec<(ChatChannel, String)>,
    rejected: Vec<ChatRejectReason>,
}

fn got(c: &mut TestClient) -> Got {
    let mut g = Got::default();
    for m in c.drain() {
        match m {
            ServerMsg::ClubState { club } => g.states.push(club),
            ServerMsg::ClubPresence {
                character_id,
                online,
                ..
            } => g.presence.push((character_id, online)),
            ServerMsg::ClubNotice { notice } => g.notices.push(notice),
            ServerMsg::ClubError { code } => g.errors.push(code),
            ServerMsg::ClubInvited { invite_id, .. } => g.invited.push(invite_id),
            ServerMsg::Chat { channel, text, .. } => g.chats.push((channel, text)),
            ServerMsg::ChatRejected { reason, .. } => g.rejected.push(reason),
            _ => {}
        }
    }
    g
}

/// Ada owns the Night Owls, Bea is an officer, Cy a member; Di is in no club.
struct Four {
    s: WorldSim,
    a: TestClient,
    b: TestClient,
    c: TestClient,
    d: TestClient,
    club: Club,
}

fn four() -> Four {
    let mut s = sim();
    let ada = character("Ada", House::Skytalon, Some(HALL));
    // Bea is in her dorm (another zone), Cy far across the hall.
    let bea = in_dorm("Bea", House::Roarwing);
    let cy = character("Cy", House::Burrowsby, Some([5.0, 0.0, 195.0]));
    let di = character("Di", House::Skytalon, Some([100.5, 0.0, 100.0]));
    let club = club_of(
        70,
        1,
        &[
            (&ada, ClubRole::Owner),
            (&bea, ClubRole::Officer),
            (&cy, ClubRole::Member),
        ],
    );
    let mut a = join_in(&mut s, ada, Some(club.clone()));
    let mut b = join_in(&mut s, bea, Some(club.clone()));
    let mut c = join_in(&mut s, cy, Some(club.clone()));
    let mut d = join_in(&mut s, di, None);
    for x in [&mut a, &mut b, &mut c, &mut d] {
        x.drain();
    }
    Four {
        s,
        a,
        b,
        c,
        d,
        club,
    }
}

#[test]
fn members_get_their_club_on_joining_and_see_each_other_come_and_go() {
    let mut s = sim();
    let ada = character("Ada", House::Skytalon, Some(HALL));
    let bea = character("Bea", House::Roarwing, Some(HALL));
    let club = club_of(71, 1, &[(&ada, ClubRole::Owner), (&bea, ClubRole::Member)]);
    let (ada_id, bea_id) = (ada.id, bea.id);
    let mut a = join_in(&mut s, ada, Some(club.clone()));
    let g = got(&mut a);
    let view = g.states[0].clone().expect("the club, after Welcome");
    assert_eq!(view.tag, "OWL");
    assert_eq!(
        view.members
            .iter()
            .map(|m| (m.name.as_str(), m.online))
            .collect::<Vec<_>>(),
        [("Ada", true), ("Bea", false)]
    );
    assert!(s.take_ext_jobs().is_empty(), "the gateway loaded it");
    // Bea comes online with an older copy: the one here stays.
    let mut b = join_in(
        &mut s,
        bea,
        Some(Club {
            version: 0,
            ..club.clone()
        }),
    );
    assert_eq!(got(&mut a).presence, [(bea_id, true)]);
    let gb = got(&mut b);
    assert!(
        gb.states[0]
            .as_ref()
            .unwrap()
            .members
            .iter()
            .all(|m| m.online)
    );
    // Bea leaves the world: Ada sees her go; Ada leaves too: the copy goes.
    s.handle(WorldCmd::Leave { id: b.id });
    assert_eq!(got(&mut a).presence, [(bea_id, false)]);
    assert!(s.clubs.of(ada_id).is_some());
    s.handle(WorldCmd::Leave { id: a.id });
    assert!(s.clubs.is_empty() && s.clubs.of(ada_id).is_none());
}

#[test]
fn club_chat_reaches_every_member_in_any_zone_and_nobody_else() {
    let Four {
        mut s,
        mut a,
        mut b,
        mut c,
        mut d,
        ..
    } = four();
    // Eve shares Ada's house, Di stands right beside her: neither hears it.
    let mut e = join(&mut s, "Eve", House::Skytalon, [150.0, 0.0, 100.0]);
    e.drain();
    let line = |s: &mut WorldSim, from: &TestClient, text: &str, seq| {
        s.input(
            from.id,
            Instant::now(),
            Input::Chat {
                channel: ChatChannel::Club,
                text: text.into(),
                client_seq: seq,
            },
        );
        ticks(s, 1);
    };
    line(&mut s, &a, "meeting at the lake", 1);
    for x in [&mut a, &mut b, &mut c] {
        assert_eq!(
            got(x).chats,
            [(ChatChannel::Club, "meeting at the lake".into())]
        );
    }
    for x in [&mut d, &mut e] {
        assert!(got(x).chats.is_empty());
    }
    // From the dorm too.
    line(&mut s, &b, "on my way", 2);
    assert_eq!(got(&mut c).chats, [(ChatChannel::Club, "on my way".into())]);
    a.drain();
    b.drain();
    // The word filter applies as everywhere.
    line(&mut s, &c, "a badword", 3);
    assert_eq!(got(&mut c).rejected, [ChatRejectReason::Filtered]);
    assert!(got(&mut a).chats.is_empty());
    // And the rate limit (shared with every channel): Bea spoke once
    // already, so four more of her burst of five get through.
    for n in 0..6 {
        line(&mut s, &b, "spam", 10 + n);
    }
    assert_eq!(got(&mut b).rejected, [ChatRejectReason::RateLimited; 2]);
    assert_eq!(got(&mut a).chats.len(), 4);
    c.drain();
    // Outside a club there is nobody to talk to.
    line(&mut s, &d, "hello?", 40);
    assert_eq!(got(&mut d).rejected, [ChatRejectReason::NoChannel]);
    assert!(got(&mut c).chats.is_empty());
}

#[test]
fn officers_invite_and_answers_go_to_the_database() {
    let Four {
        mut s,
        mut a,
        mut b,
        mut c,
        mut d,
        club,
    } = four();
    // A member may not invite.
    act(&mut s, &c, ClubInput::Invite("Di".into()));
    assert_eq!(got(&mut c).errors, [ClubErrorCode::NotAllowed]);
    // Nor anyone outside a club.
    act(&mut s, &d, ClubInput::Invite("Ada".into()));
    assert_eq!(got(&mut d).errors, [ClubErrorCode::NotInClub]);
    // Inviting someone already in a club, yourself, or nobody.
    act(&mut s, &b, ClubInput::Invite("Cy".into()));
    act(&mut s, &b, ClubInput::Invite("Bea".into()));
    act(&mut s, &b, ClubInput::Invite("Nobody".into()));
    assert_eq!(
        got(&mut b).errors,
        [
            ClubErrorCode::InClub,
            ClubErrorCode::SelfInvite,
            ClubErrorCode::NotOnline
        ]
    );
    // The officer in her dorm invites Di (case does not matter).
    act(&mut s, &b, ClubInput::Invite("di".into()));
    assert_eq!(
        got(&mut b).notices,
        [ClubNotice::InviteSent { name: "Di".into() }]
    );
    let invite_id = got(&mut d).invited[0];
    // Declining tells the officer and costs nothing.
    act(
        &mut s,
        &d,
        ClubInput::Respond {
            invite_id,
            accept: false,
        },
    );
    assert_eq!(
        got(&mut b).notices,
        [ClubNotice::Declined { name: "Di".into() }]
    );
    act(
        &mut s,
        &d,
        ClubInput::Respond {
            invite_id,
            accept: true,
        },
    );
    assert_eq!(
        got(&mut d).errors,
        [ClubErrorCode::InviteExpired],
        "answered once"
    );
    // Accepting runs the join in the database.
    act(&mut s, &a, ClubInput::Invite("Di".into()));
    a.drain();
    let invite_id = got(&mut d).invited[0];
    act(
        &mut s,
        &d,
        ClubInput::Respond {
            invite_id,
            accept: true,
        },
    );
    assert_eq!(s.take_ext_jobs().len(), 1, "the join's job");
    // Its outcome: Di is a member; everyone online gets the club and hears.
    let di_id = d.cid;
    let mut joined = club.clone();
    joined.version = 2;
    joined.members.push(Member {
        character_id: di_id,
        name: "Di".into(),
        house: House::Skytalon,
        level: 1,
        role: ClubRole::Member,
    });
    s.club_done(
        di_id,
        Ok((
            Outcome {
                club_id: club.id,
                club: Some(joined),
                before: vec![a.cid, b.cid, c.cid],
                notices: vec![ClubNotice::Joined { name: "Di".into() }],
            },
            None,
        )),
        Instant::now(),
    );
    for x in [&mut a, &mut b, &mut c, &mut d] {
        let g = got(x);
        assert_eq!(g.states.len(), 1);
        assert_eq!(g.states[0].as_ref().unwrap().members.len(), 4);
        assert_eq!(g.notices, [ClubNotice::Joined { name: "Di".into() }]);
    }
    assert_eq!(s.clubs.of(di_id).map(|c| c.id), Some(club.id));
    // A refusal from the database reaches only the one who asked.
    s.club_done(
        di_id,
        Err(StoreError::Refused(ClubErrorCode::Full)),
        Instant::now(),
    );
    assert_eq!(got(&mut d).errors, [ClubErrorCode::Full]);
    assert!(got(&mut a).errors.is_empty());
}

#[test]
fn invitations_lapse_and_are_capped() {
    let Four {
        mut s,
        mut a,
        mut d,
        ..
    } = four();
    act(&mut s, &a, ClubInput::Invite("Di".into()));
    a.drain();
    let invite_id = got(&mut d).invited[0];
    let late = Instant::now() + INVITE_TTL + Duration::from_secs(1);
    s.input(
        d.id,
        late,
        Input::Club(ClubInput::Respond {
            invite_id,
            accept: true,
        }),
    );
    assert_eq!(got(&mut d).errors, [ClubErrorCode::InviteExpired]);
    assert!(s.take_ext_jobs().is_empty());
    let now = Instant::now();
    let mut clubs = Clubs::default();
    let first = clubs.invite(1, 10, 20, now);
    assert_eq!(clubs.take_invite(first.id, 21, now), None, "not theirs");
    clubs.invite(1, 10, 20, now);
    assert_eq!(clubs.invites().len(), 1, "a newer one replaces the older");
    for to in 30..50 {
        clubs.invite(1, 10, to, now);
    }
    assert_eq!(clubs.invites().len(), INVITES_PER_SENDER);
    clubs.expire(now + INVITE_TTL);
    assert!(clubs.invites().is_empty());
}

#[test]
fn blocks_refuse_invitations_privately() {
    let mut s = sim();
    let ada = character("Ada", House::Skytalon, Some(HALL));
    let bea = character("Bea", House::Roarwing, Some(HALL));
    let cy = character("Cy", House::Roarwing, Some(HALL));
    let club = club_of(72, 1, &[(&ada, ClubRole::Owner), (&cy, ClubRole::Officer)]);
    let (ada_id, bea_id, cy_id) = (ada.id, bea.id, cy.id);
    let mut a = join_with(
        &mut s,
        Joining {
            social: Social {
                blocked: vec![bea_id],
                ..Social::default()
            },
            club: Some(club.clone()),
            ..Joining::alone(ada)
        },
        256,
    );
    let _b = join_with(
        &mut s,
        Joining {
            social: Social {
                blocked_by: vec![ada_id],
                blocked: vec![cy_id],
                ..Social::default()
            },
            ..Joining::alone(bea)
        },
        256,
    );
    let mut c = join_in(&mut s, cy, Some(club));
    a.drain();
    c.drain();
    act(&mut s, &a, ClubInput::Invite("Bea".into()));
    assert_eq!(got(&mut a).errors, [ClubErrorCode::Blocked]);
    // Bea blocked Cy: to Cy she is simply not online.
    act(&mut s, &c, ClubInput::Invite("Bea".into()));
    assert_eq!(got(&mut c).errors, [ClubErrorCode::NotOnline]);
    assert!(s.clubs.invites().is_empty());
}

#[test]
fn ranks_are_checked_before_the_database_is_asked() {
    let Four {
        mut s,
        mut a,
        mut b,
        mut c,
        ..
    } = four();
    let (ada, bea, cy) = (a.cid, b.cid, c.cid);
    // An officer may not remove another officer or the owner, nor rename.
    act(&mut s, &b, ClubInput::Kick(ada));
    act(
        &mut s,
        &b,
        ClubInput::SetRole {
            character_id: cy,
            role: ClubRole::Officer,
        },
    );
    act(&mut s, &b, ClubInput::Transfer(cy));
    act(
        &mut s,
        &b,
        ClubInput::Rename {
            name: "Owls".into(),
            tag: "OW".into(),
        },
    );
    act(&mut s, &b, ClubInput::Disband);
    assert_eq!(got(&mut b).errors, [ClubErrorCode::NotAllowed; 5]);
    // A member may only leave (and chat).
    act(&mut s, &c, ClubInput::SetMotd("hi".into()));
    act(&mut s, &c, ClubInput::Kick(bea));
    assert_eq!(got(&mut c).errors, [ClubErrorCode::NotAllowed; 2]);
    assert!(s.take_ext_jobs().is_empty());
    // A bad name, tag or message is refused before any job.
    act(
        &mut s,
        &a,
        ClubInput::Rename {
            name: "No!".into(),
            tag: "OWL".into(),
        },
    );
    act(
        &mut s,
        &a,
        ClubInput::Rename {
            name: "Owls Club".into(),
            tag: "O".into(),
        },
    );
    act(
        &mut s,
        &a,
        ClubInput::Rename {
            name: "The Badword Club".into(),
            tag: "OWL".into(),
        },
    );
    act(&mut s, &b, ClubInput::SetMotd("a badword".into()));
    assert_eq!(
        got(&mut a).errors,
        [
            ClubErrorCode::NameInvalid,
            ClubErrorCode::TagInvalid,
            ClubErrorCode::Filtered
        ]
    );
    assert_eq!(got(&mut b).errors, [ClubErrorCode::Filtered]);
    assert!(s.take_ext_jobs().is_empty());
    // What each may do goes to the database.
    act(&mut s, &b, ClubInput::Kick(cy));
    act(&mut s, &b, ClubInput::SetMotd("Meet at the lake".into()));
    act(
        &mut s,
        &a,
        ClubInput::SetRole {
            character_id: cy,
            role: ClubRole::Officer,
        },
    );
    act(&mut s, &a, ClubInput::Transfer(bea));
    act(
        &mut s,
        &a,
        ClubInput::Rename {
            name: "Owls Club".into(),
            tag: "owls".into(),
        },
    );
    act(&mut s, &a, ClubInput::Disband);
    act(&mut s, &c, ClubInput::Leave);
    assert_eq!(s.take_ext_jobs().len(), 7);
    assert!(got(&mut a).errors.is_empty() && got(&mut b).errors.is_empty());
}

#[test]
fn founding_needs_the_secretary_close_by() {
    let Four {
        mut s,
        mut a,
        mut d,
        ..
    } = four();
    // Nobody is the secretary in this test hall: always too far.
    act(
        &mut s,
        &d,
        ClubInput::Found {
            secretary: a.id,
            name: "Chess Club".into(),
            tag: "CHS".into(),
        },
    );
    assert_eq!(got(&mut d).errors, [ClubErrorCode::TooFar]);
    assert!(s.take_ext_jobs().is_empty());
    a.drain();
}

#[test]
fn leaving_kicking_and_disbanding_reach_everyone_once() {
    let Four {
        mut s,
        mut a,
        mut b,
        mut c,
        mut d,
        club,
    } = four();
    // The owner leaves: Bea (the longest-serving officer) owns it now.
    let mut after = club.clone();
    after.version = 2;
    after.members.retain(|m| m.character_id != a.cid);
    after.members[0].role = ClubRole::Owner;
    let outcome = Outcome {
        club_id: club.id,
        club: Some(after.clone()),
        before: vec![a.cid, b.cid, c.cid],
        notices: vec![
            ClubNotice::Left { name: "Ada".into() },
            ClubNotice::Owner { name: "Bea".into() },
        ],
    };
    s.club_apply(outcome.clone());
    let ga = got(&mut a);
    assert_eq!(ga.states, [None], "Ada is in no club now");
    assert_eq!(ga.notices.len(), 2, "and hears why");
    for x in [&mut b, &mut c] {
        let g = got(x);
        let view = g.states[0].clone().unwrap();
        assert_eq!(view.members[0].name, "Bea");
        assert_eq!(view.members[0].role, ClubRole::Owner);
        assert_eq!(g.notices.len(), 2);
    }
    assert!(got(&mut d).notices.is_empty());
    // The same outcome again (an older copy) changes nothing but is told.
    s.club_apply(Outcome {
        club: Some(club.clone()),
        notices: vec![],
        ..outcome
    });
    assert!(got(&mut b).states.is_empty());
    assert_eq!(s.clubs.get(club.id).unwrap().version, 2);
    // Chat in the club is now Bea's and Cy's only.
    s.input(
        a.id,
        Instant::now(),
        Input::Chat {
            channel: ChatChannel::Club,
            text: "hello?".into(),
            client_seq: 1,
        },
    );
    ticks(&mut s, 1);
    assert_eq!(got(&mut a).rejected, [ChatRejectReason::NoChannel]);
    // Disbanded: everyone hears, everyone is in no club, the copy is gone
    // for good.
    s.club_apply(Outcome {
        club_id: club.id,
        club: None,
        before: vec![b.cid, c.cid],
        notices: vec![ClubNotice::Disbanded {
            club: club.name.clone(),
        }],
    });
    for x in [&mut b, &mut c] {
        let g = got(x);
        assert_eq!(g.states, [None]);
        assert_eq!(
            g.notices,
            [ClubNotice::Disbanded {
                club: club.name.clone()
            }]
        );
    }
    assert!(s.clubs.is_empty());
    s.club_apply(Outcome {
        club_id: club.id,
        club: Some(after),
        before: vec![],
        notices: vec![],
    });
    assert!(
        s.clubs.is_empty(),
        "a late copy of a disbanded club is never kept"
    );
}

#[test]
fn a_deleted_member_reloads_the_club() {
    let Four { mut s, .. } = four();
    s.handle(WorldCmd::ClubChanged {
        club_id: 70,
        notices: vec![ClubNotice::Left {
            name: "Gone".into(),
        }],
    });
    assert_eq!(s.take_ext_jobs().len(), 1, "a reload of the club");
    // A club with nobody online is not reloaded (the next member loads it).
    s.handle(WorldCmd::ClubChanged {
        club_id: 999,
        notices: vec![],
    });
    assert!(s.take_ext_jobs().is_empty());
}

#[test]
fn a_member_levelling_tells_the_others() {
    let Four {
        mut s,
        mut a,
        mut b,
        ..
    } = four();
    let i = s.ents.index_of(b.id).unwrap();
    s.ents.info[i].level = 9;
    s.club_level(i);
    assert_eq!(got(&mut a).presence, [(b.cid, true)]);
    assert!(got(&mut b).presence.is_empty());
    s.club_level(i);
    assert!(got(&mut a).presence.is_empty(), "only a change is told");
}
