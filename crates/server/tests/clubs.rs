//! Clubs in the database (`server::clubs::store`) and over HTTP (the
//! register, the card, deleting a character): founding's level, fee and
//! ledger row, unique names and tags, joining and the cap, ranks, ownership
//! passing on when the owner leaves or is deleted, disbanding.

mod common;

use axum::http::StatusCode;
use common::{TestApp, TestPlayer};
use protocol::{ClubErrorCode as E, ClubNotice, ClubRole};
use rand::Rng;
use serde_json::json;
use server::clubs::store::{self, StoreError};
use server::clubs::{self, Club, ClubRules};
use server::ledger::audit::reconcile;

fn refused<T: std::fmt::Debug>(r: Result<T, StoreError>) -> E {
    match r {
        Err(StoreError::Refused(code)) => code,
        other => panic!("expected a refusal, got {other:?}"),
    }
}

/// A club name and tag nobody else's test uses.
fn unique() -> (String, String) {
    let mut rng = rand::thread_rng();
    let letters: String = (0..10)
        .map(|_| rng.gen_range(b'a'..=b'z') as char)
        .collect();
    let tag: String = (0..4).map(|_| rng.gen_range(b'A'..=b'Z') as char).collect();
    (format!("Club {letters}"), tag)
}

/// A player of level 4 with `crowns` Crowns more than the starting purse.
async fn eligible(app: &TestApp, crowns: i64) -> TestPlayer {
    let p = app.player("Skytalon").await;
    let res = app
        .post(
            "/api/dev/grant",
            json!({ "xp": 720, "crowns": crowns }),
            Some(&p.token),
        )
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    p
}

async fn found(app: &TestApp, p: &TestPlayer) -> Club {
    let (name, tag) = unique();
    let (outcome, _) = store::found(&app.state.pool, clubs::rules(), p.id, &name, &tag)
        .await
        .unwrap();
    outcome.club.unwrap()
}

async fn join(app: &TestApp, club: &Club, inviter: i64, p: &TestPlayer) {
    store::join(&app.state.pool, clubs::rules(), club.id, inviter, p.id)
        .await
        .unwrap();
}

fn roles(club: &Club) -> Vec<(i64, ClubRole)> {
    club.members
        .iter()
        .map(|m| (m.character_id, m.role))
        .collect()
}

async fn now(app: &TestApp, id: i64) -> Option<Club> {
    store::load(&app.state.pool, id).await.unwrap()
}

#[tokio::test]
async fn founding_needs_the_level_and_the_fee_and_ledgers_it() {
    let app = TestApp::dev().await;
    let pool = &app.state.pool;
    let rules = clubs::rules();
    let p = app.player("Roarwing").await;
    let (name, tag) = unique();
    // Level 1 with the starting purse (100): too low, and short.
    assert_eq!(
        refused(store::found(pool, rules, p.id, &name, &tag).await),
        E::LevelTooLow
    );
    // Level 4, still 100 Crowns.
    app.post("/api/dev/grant", json!({ "xp": 720 }), Some(&p.token))
        .await;
    assert_eq!(
        refused(store::found(pool, rules, p.id, &name, &tag).await),
        E::NotEnoughCrowns
    );
    // 160 Crowns: founded, the fee paid.
    app.post("/api/dev/grant", json!({ "crowns": 60 }), Some(&p.token))
        .await;
    let (outcome, balance) = store::found(pool, rules, p.id, &name, &tag).await.unwrap();
    assert_eq!(balance, 10);
    let club = outcome.club.unwrap();
    assert_eq!(
        (club.name.as_str(), club.tag.as_str()),
        (name.as_str(), tag.as_str())
    );
    assert_eq!(roles(&club), [(p.id, ClubRole::Owner)]);
    assert_eq!(
        outcome.notices,
        [ClubNotice::Founded { club: name.clone() }]
    );
    let fee: (i64, Option<i64>) = sqlx::query_as(
        "SELECT delta, ref_id FROM crown_ledger WHERE character_id = $1 AND reason = 'club_found'",
    )
    .bind(p.id)
    .fetch_one(pool)
    .await
    .unwrap();
    assert_eq!(fee, (-150, Some(club.id)));
    let history = app.get("/api/wallet/history", Some(&p.token)).await;
    assert_eq!(history.body["items"][0]["reason"], "club_found");
    // One club per character.
    app.post("/api/dev/grant", json!({ "crowns": 500 }), Some(&p.token))
        .await;
    let (other, other_tag) = unique();
    assert_eq!(
        refused(store::found(pool, rules, p.id, &other, &other_tag).await),
        E::AlreadyInClub
    );
    reconcile(pool, &[p.id]).await.unwrap().assert_clean();
}

#[tokio::test]
async fn names_and_tags_are_unique_whatever_the_case() {
    let app = TestApp::dev().await;
    let pool = &app.state.pool;
    let rules = clubs::rules();
    let (a, b) = (eligible(&app, 100).await, eligible(&app, 100).await);
    let club = found(&app, &a).await;
    let (fresh, fresh_tag) = unique();
    let upper = club.name.to_uppercase();
    assert_eq!(
        refused(store::found(pool, rules, b.id, &upper, &fresh_tag).await),
        E::NameTaken
    );
    assert_eq!(
        refused(store::found(pool, rules, b.id, &fresh, &club.tag).await),
        E::TagTaken
    );
    // Nothing was paid for the refusals.
    let crowns: i64 = sqlx::query_scalar("SELECT crowns FROM characters WHERE id = $1")
        .bind(b.id)
        .fetch_one(pool)
        .await
        .unwrap();
    assert_eq!(crowns, 200);
    // Renaming to a taken name or tag is refused too; to a free one works.
    let theirs = found(&app, &b).await;
    let lower = theirs.name.to_lowercase();
    assert_eq!(
        refused(store::rename(pool, a.id, &lower, &club.tag).await),
        E::NameTaken
    );
    assert_eq!(
        refused(store::rename(pool, a.id, &club.name, &theirs.tag).await),
        E::TagTaken
    );
    let (name, tag) = unique();
    let renamed = store::rename(pool, a.id, &name, &tag).await.unwrap();
    let after = renamed.club.unwrap();
    assert_eq!((after.name, after.tag), (name.clone(), tag.clone()));
    assert!(after.version > club.version);
    assert_eq!(renamed.notices, [ClubNotice::Renamed { club: name, tag }]);
}

#[tokio::test]
async fn joining_needs_an_officer_a_free_place_and_no_other_club() {
    let app = TestApp::dev().await;
    let pool = &app.state.pool;
    let owner = eligible(&app, 50).await;
    let club = found(&app, &owner).await;
    let (b, c, d) = (
        app.player("Roarwing").await,
        app.player("Burrowsby").await,
        app.player("Serpentyne").await,
    );
    join(&app, &club, owner.id, &b).await;
    // A member's invitation no longer holds.
    assert_eq!(
        refused(store::join(pool, clubs::rules(), club.id, b.id, c.id).await),
        E::InviteExpired
    );
    // Already in it.
    assert_eq!(
        refused(store::join(pool, clubs::rules(), club.id, owner.id, b.id).await),
        E::AlreadyInClub
    );
    // A club of three at most (the shipped cap is a hundred).
    let small = ClubRules {
        members_max: 3,
        ..clubs::rules().clone()
    };
    store::join(pool, &small, club.id, owner.id, c.id)
        .await
        .unwrap();
    assert_eq!(
        refused(store::join(pool, &small, club.id, owner.id, d.id).await),
        E::Full
    );
    let club = now(&app, club.id).await.unwrap();
    assert_eq!(club.members.len(), 3);
    assert_eq!(clubs::rules().members_max, 100);
    // In another club already.
    let other_owner = eligible(&app, 50).await;
    let other = found(&app, &other_owner).await;
    assert_eq!(
        refused(store::join(pool, clubs::rules(), other.id, other_owner.id, b.id).await),
        E::AlreadyInClub
    );
    // A disbanded club's invitation is void.
    store::disband(pool, other_owner.id).await.unwrap();
    assert_eq!(
        refused(store::join(pool, clubs::rules(), other.id, other_owner.id, d.id).await),
        E::InviteExpired
    );
}

#[tokio::test]
async fn ranks_decide_who_does_what() {
    let app = TestApp::dev().await;
    let pool = &app.state.pool;
    let owner = eligible(&app, 50).await;
    let club = found(&app, &owner).await;
    let (b, c, d) = (
        app.player("Roarwing").await,
        app.player("Burrowsby").await,
        app.player("Serpentyne").await,
    );
    for p in [&b, &c, &d] {
        join(&app, &club, owner.id, p).await;
    }
    // The owner appoints officers; nobody else does.
    assert_eq!(
        refused(store::set_role(pool, b.id, c.id, ClubRole::Officer).await),
        E::NotAllowed
    );
    let out = store::set_role(pool, owner.id, b.id, ClubRole::Officer)
        .await
        .unwrap();
    assert_eq!(
        out.notices,
        [ClubNotice::Promoted {
            name: b.name.clone()
        }]
    );
    store::set_role(pool, owner.id, c.id, ClubRole::Officer)
        .await
        .unwrap();
    // An officer invites (join succeeds), removes members, not officers or the owner.
    assert_eq!(refused(store::kick(pool, b.id, c.id).await), E::NotAllowed);
    assert_eq!(
        refused(store::kick(pool, b.id, owner.id).await),
        E::NotAllowed
    );
    let out = store::kick(pool, b.id, d.id).await.unwrap();
    assert_eq!(
        out.notices,
        [ClubNotice::Removed {
            name: d.name.clone()
        }]
    );
    assert!(out.before.contains(&d.id) && out.club.as_ref().unwrap().member(d.id).is_none());
    join(&app, &club, b.id, &d).await;
    // A member only leaves.
    assert_eq!(
        refused(store::set_motd(pool, d.id, "hi").await),
        E::NotAllowed
    );
    assert_eq!(refused(store::disband(pool, d.id).await), E::NotAllowed);
    // Officers set the message of the day.
    let out = store::set_motd(pool, c.id, "Meet at the lake")
        .await
        .unwrap();
    assert_eq!(out.club.unwrap().motd, "Meet at the lake");
    // The owner dismisses an officer, then hands the club on and becomes one.
    let out = store::set_role(pool, owner.id, c.id, ClubRole::Member)
        .await
        .unwrap();
    assert_eq!(
        out.notices,
        [ClubNotice::Demoted {
            name: c.name.clone()
        }]
    );
    assert_eq!(
        refused(store::transfer(pool, b.id, c.id).await),
        E::NotAllowed
    );
    let out = store::transfer(pool, owner.id, d.id).await.unwrap();
    let after = out.club.unwrap();
    assert_eq!(after.owner().unwrap().character_id, d.id);
    assert_eq!(after.member(owner.id).unwrap().role, ClubRole::Officer);
    assert_eq!(
        out.notices,
        [ClubNotice::Owner {
            name: d.name.clone()
        }]
    );
    // Outside the club nothing works.
    let stranger = app.player("Skytalon").await;
    assert_eq!(refused(store::leave(pool, stranger.id).await), E::NotInClub);
    assert_eq!(
        refused(store::kick(pool, owner.id, stranger.id).await),
        E::NotMember
    );
    // The owner disbands it: gone, its members in no club.
    let out = store::disband(pool, d.id).await.unwrap();
    assert!(out.club.is_none());
    assert_eq!(out.before.len(), 4);
    assert!(now(&app, club.id).await.is_none());
    assert!(store::load_of(pool, b.id).await.unwrap().is_none());
}

#[tokio::test]
async fn an_owner_leaving_hands_the_club_to_the_longest_serving_officer_else_member() {
    let app = TestApp::dev().await;
    let pool = &app.state.pool;
    let owner = eligible(&app, 50).await;
    let club = found(&app, &owner).await;
    let (b, c, d) = (
        app.player("Roarwing").await,
        app.player("Burrowsby").await,
        app.player("Serpentyne").await,
    );
    for p in [&b, &c, &d] {
        join(&app, &club, owner.id, p).await;
    }
    // c is the only officer, though b has been a member longer.
    store::set_role(pool, owner.id, c.id, ClubRole::Officer)
        .await
        .unwrap();
    let out = store::leave(pool, owner.id).await.unwrap();
    assert_eq!(
        out.notices,
        [
            ClubNotice::Left {
                name: owner.name.clone()
            },
            ClubNotice::Owner {
                name: c.name.clone()
            }
        ]
    );
    let after = out.club.unwrap();
    assert_eq!(
        roles(&after),
        [
            (b.id, ClubRole::Member),
            (c.id, ClubRole::Owner),
            (d.id, ClubRole::Member)
        ]
    );
    // No officers: the longest-serving member.
    store::leave(pool, c.id).await.unwrap();
    let after = now(&app, club.id).await.unwrap();
    assert_eq!(after.owner().unwrap().character_id, b.id);
    // The last one out disbands it.
    store::leave(pool, b.id).await.unwrap();
    let out = store::leave(pool, d.id).await.unwrap();
    assert!(out.club.is_none());
    assert_eq!(
        out.notices.last(),
        Some(&ClubNotice::Disbanded {
            club: club.name.clone()
        })
    );
    assert!(now(&app, club.id).await.is_none());
}

#[tokio::test]
async fn deleting_a_character_leaves_its_club_and_the_register_and_card_show_clubs() {
    let app = TestApp::dev().await;
    let pool = &app.state.pool;
    let owner = eligible(&app, 50).await;
    let club = found(&app, &owner).await;
    let (b, c) = (app.player("Roarwing").await, app.player("Burrowsby").await);
    join(&app, &club, owner.id, &b).await;
    join(&app, &club, owner.id, &c).await;
    store::set_role(pool, owner.id, c.id, ClubRole::Officer)
        .await
        .unwrap();

    // The card shows the club; the register lists it with its members.
    let card = app
        .get(&format!("/api/players/{}/card", b.name), Some(&c.token))
        .await;
    assert_eq!(card.status, StatusCode::OK, "{:?}", card.body);
    assert_eq!(
        card.body["club"],
        json!({ "name": club.name, "tag": club.tag })
    );
    let loner = app.player("Skytalon").await;
    let card = app
        .get(&format!("/api/players/{}/card", loner.name), Some(&c.token))
        .await;
    assert!(card.body["club"].is_null());
    let register = app.get("/api/clubs", Some(&loner.token)).await;
    assert_eq!(register.status, StatusCode::OK, "{:?}", register.body);
    let listed = register.body["clubs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|l| l["tag"] == club.tag.as_str())
        .cloned();
    // The register holds the largest hundred; a fresh club of three may
    // not be among them on a busy test database.
    if let Some(l) = listed {
        assert_eq!(
            l,
            json!({ "name": club.name, "tag": club.tag, "members": 3 })
        );
    }
    assert_eq!(
        app.get("/api/clubs", None).await.status,
        StatusCode::UNAUTHORIZED
    );

    // The owner's character is deleted: the officer owns the club now.
    let res = app
        .delete_character(&owner.token, owner.id, &owner.name)
        .await;
    assert_eq!(res.status, StatusCode::NO_CONTENT, "{:?}", res.body);
    let after = now(&app, club.id).await.unwrap();
    assert_eq!(
        roles(&after),
        [(b.id, ClubRole::Member), (c.id, ClubRole::Owner)]
    );
    // Then the others: the club is gone with its last member.
    app.delete_character(&c.token, c.id, &c.name).await;
    let res = app.delete_character(&b.token, b.id, &b.name).await;
    assert_eq!(res.status, StatusCode::NO_CONTENT, "{:?}", res.body);
    assert!(now(&app, club.id).await.is_none());
    reconcile(pool, &[owner.id, b.id, c.id])
        .await
        .unwrap()
        .assert_clean();
}
