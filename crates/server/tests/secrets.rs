//! The secrets and the map end to end against the database
//! (docs/architecture.md "Secrets and the map"):
//!
//! - walking onto a hidden passage the first time discovers it: one
//!   transaction writes the discovery and its reward (100 XP `explore`, a
//!   House Cup point from the `find` source, the recipe), then `Discovered`
//!   and `Progress`; walking it again, rejoining or retrying the job gives
//!   nothing more, and the audit is clean;
//! - the map's reveals (the surfaces stood on) are saved with the position
//!   save and at logout, survive a retried save, come back at the next join
//!   (and are not written again), and `GET /api/map` lists them with the
//!   secrets found;
//! - the audit flags an `explore` XP grant without its discovery.

#[path = "castle_harness.rs"]
mod castle;
mod common;

use std::sync::Arc;

use axum::http::StatusCode;
use castle::Castle;
use common::TestApp;
use protocol::{ServerMsg, XpSource};
use server::catalog::shipped;
use server::map::{self, Reveal};
use server::metrics::Registry;
use server::realtime::metrics::RealtimeMetrics;
use tokio::sync::mpsc;

/// On the fifth-floor west corridor, two metres short of the tapestry.
const BY_TAPESTRY: [f32; 3] = [-70.0, 45.0, 5.0];
/// Through it: the tapestry door, then the passage's top landing.
const TAPESTRY_DOOR: [f32; 3] = [-70.0, 45.0, 8.0];
const PASSAGE_TOP: [f32; 3] = [-70.0, 45.0, 11.0];

fn weavers_stair() -> sim_core::Secret {
    shipped()
        .secrets
        .secrets
        .iter()
        .find(|s| s.id == "weavers_stair")
        .unwrap()
        .clone()
}

async fn count(pool: &sqlx::PgPool, sql: &str, id: i64) -> i64 {
    sqlx::query_scalar(sql)
        .bind(id)
        .fetch_one(pool)
        .await
        .unwrap()
}

#[tokio::test]
async fn walking_into_a_passage_discovers_it_once() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let ada = app.player("Skytalon").await;
    let mut w = Castle::new(None);
    let mut a = w.join(
        ada.id,
        &ada.name,
        BY_TAPESTRY,
        vec![],
        map::load(&pool, ada.id).await.unwrap(),
    );
    a.msgs();
    w.walk(&mut a, TAPESTRY_DOOR);
    assert_eq!(w.run_jobs(&pool).await, 1, "one discovery job");
    let msgs = a.msgs();
    let found: Vec<_> = msgs
        .iter()
        .filter_map(|m| match m {
            ServerMsg::Discovered {
                secret,
                xp,
                house_point,
                recipe,
            } => Some((secret.clone(), *xp, *house_point, *recipe)),
            _ => None,
        })
        .collect();
    assert_eq!(
        found,
        vec![("weavers_stair".to_owned(), 100, true, Some(20))]
    );
    assert!(msgs.iter().any(|m| matches!(
        m,
        ServerMsg::Progress { gained: Some(g), .. }
            if g.source == XpSource::Explore && g.amount == 100
    )));
    // On through the passage and back: nothing more.
    w.walk(&mut a, PASSAGE_TOP);
    w.walk(&mut a, BY_TAPESTRY);
    w.walk(&mut a, TAPESTRY_DOOR);
    assert_eq!(w.run_jobs(&pool).await, 0, "found already");
    assert_eq!(w.metrics.discoveries.get(), 1);
    // The rows: the discovery, its XP, its point and its recipe.
    let one = |sql| count(&pool, sql, ada.id);
    assert_eq!(
        one("SELECT count(*) FROM discoveries WHERE character_id = $1 AND secret = 'weavers_stair' AND xp = 100 AND recipe = 20").await,
        1
    );
    assert_eq!(
        one("SELECT count(*) FROM xp_ledger WHERE character_id = $1 AND source = 'explore' AND ref_id = 1 AND amount = 100").await,
        1
    );
    assert_eq!(
        one("SELECT count(*) FROM house_points WHERE character_id = $1 AND source = 'find' AND points = 1").await,
        1
    );
    assert_eq!(
        one("SELECT count(*) FROM known_recipes WHERE character_id = $1 AND recipe_id = 20").await,
        1
    );
    // A retried job (or a second connection) finds the row and gives nothing.
    assert_eq!(
        map::discover(&pool, ada.id, &weavers_stair())
            .await
            .unwrap(),
        None
    );
    // Rejoining with what was saved: the passage is known, no job starts.
    assert!(w.s.remove_player(a.id));
    let mut a = w.join(
        ada.id,
        &ada.name,
        BY_TAPESTRY,
        vec![],
        map::load(&pool, ada.id).await.unwrap(),
    );
    w.walk(&mut a, TAPESTRY_DOOR);
    assert_eq!(w.run_jobs(&pool).await, 0);
    assert_eq!(
        one("SELECT count(*) FROM xp_ledger WHERE character_id = $1 AND source = 'explore'").await,
        1
    );
    server::ledger::audit::reconcile(&pool, &[ada.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Two discoveries of the same secret at once (two connections, a job run
/// twice): one rewards, the other finds the row.
#[tokio::test]
async fn racing_discoveries_reward_once() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let bea = app.player("Burrowsby").await;
    let secret = weavers_stair();
    let (x, y) = tokio::join!(
        map::discover(&pool, bea.id, &secret),
        map::discover(&pool, bea.id, &secret)
    );
    let rewarded = [x.unwrap(), y.unwrap()]
        .into_iter()
        .flatten()
        .collect::<Vec<_>>();
    assert_eq!(rewarded.len(), 1);
    assert_eq!(rewarded[0].xp.granted, 100);
    assert_eq!(rewarded[0].recipe, Some(20));
    // Every other secret once, the House Cup points under the find cap.
    for s in &shipped().secrets.secrets {
        if s.id != secret.id {
            assert!(map::discover(&pool, bea.id, s).await.unwrap().is_some());
        }
    }
    let xp: i64 = count(
        &pool,
        "SELECT coalesce(sum(amount), 0)::bigint FROM xp_ledger WHERE character_id = $1 AND source = 'explore'",
        bea.id,
    )
    .await;
    assert_eq!(xp, 500);
    server::ledger::audit::reconcile(&pool, &[bea.id])
        .await
        .unwrap()
        .assert_clean();
}

/// Reveals are written with the saves (the 30 s save and the logout),
/// a retried save writes nothing twice, the next join brings them back and
/// nothing is written again; `GET /api/map` lists the reveals and secrets.
#[tokio::test]
async fn reveals_persist_across_logout() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let cy = app.player("Roarwing").await;
    // The real saver.
    let metrics = Arc::new(RealtimeMetrics::register(&Registry::default()));
    let (tx, saver) = server::realtime::persist::spawn(pool.clone(), metrics.clone());
    let mut w = Castle::new(Some(tx));
    let mut c = w.join(
        cy.id,
        &cy.name,
        BY_TAPESTRY,
        vec![],
        map::load(&pool, cy.id).await.unwrap(),
    );
    w.walk(&mut c, [-75.0, 45.0, 5.0]);
    // The 30 s save carries the corridor.
    w.wait(31_000);
    w.walk(&mut c, BY_TAPESTRY);
    w.walk(&mut c, PASSAGE_TOP);
    w.run_jobs(&pool).await;
    assert!(w.s.remove_player(c.id), "logout: the passage's surfaces");
    w.s.shutdown();
    drop(w);
    saver.await.unwrap();
    let saved = map::load(&pool, cy.id).await.unwrap();
    for id in ["l5_corridor_west", "sp_tapestry_door_l5", "sp_tapestry_top"] {
        assert!(saved.reveals.iter().any(|r| r == id), "{id} in {saved:?}");
    }
    assert_eq!(saved.secrets, vec!["weavers_stair".to_owned()]);
    assert_eq!(metrics.reveals_saved.get(), saved.reveals.len() as u64);
    // A retried save writes nothing twice.
    let again: Vec<Reveal> = saved
        .reveals
        .iter()
        .map(|s| Reveal {
            character_id: cy.id,
            surface: s.as_str().into(),
        })
        .collect();
    assert_eq!(map::save_reveals(&pool, &again).await.unwrap(), 0);
    // Back in: known places are not dirty again; a new one is.
    let (tx, mut rx) = mpsc::unbounded_channel();
    let mut w = Castle::new(Some(tx));
    let mut c = w.join(cy.id, &cy.name, BY_TAPESTRY, vec![], saved.clone());
    let live = w.s.explored_of(cy.id).unwrap();
    assert_eq!(live.reveals, saved.reveals);
    w.walk(&mut c, PASSAGE_TOP);
    w.walk(&mut c, BY_TAPESTRY);
    assert!(w.s.remove_player(c.id));
    let reveals: Vec<Reveal> = std::iter::from_fn(|| rx.try_recv().ok())
        .flat_map(|b| b.reveals)
        .collect();
    assert!(reveals.is_empty(), "nothing new: {reveals:?}");
    assert_eq!(w.run_jobs(&pool).await, 0, "the secret is known");
    // GET /api/map.
    let res = app.get("/api/map", Some(&cy.token)).await;
    assert_eq!(res.status, StatusCode::OK);
    let surfaces: Vec<&str> = res.body["surfaces"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    assert_eq!(
        surfaces,
        saved.reveals.iter().map(String::as_str).collect::<Vec<_>>()
    );
    assert_eq!(res.body["secrets"], serde_json::json!(["weavers_stair"]));
    assert_eq!(
        app.get("/api/map", None).await.status,
        StatusCode::UNAUTHORIZED
    );
    server::ledger::audit::reconcile(&pool, &[cy.id])
        .await
        .unwrap()
        .assert_clean();
}

/// An online character's reveals not saved yet are in the world's answer
/// (`GET /api/map` adds them to the database's).
#[test]
fn the_world_knows_unsaved_reveals() {
    let mut w = Castle::new(None);
    let mut d = w.join(7_000_001, "Dee", BY_TAPESTRY, vec![], Default::default());
    assert!(w.s.explored_of(7_000_001).unwrap().reveals.is_empty());
    w.walk(&mut d, [-75.0, 45.0, 5.0]);
    let live = w.s.explored_of(7_000_001).unwrap();
    assert_eq!(live.reveals, vec!["l5_corridor_west".to_owned()]);
    assert!(live.secrets.is_empty(), "no job ran: nothing found yet");
    // Standing on a passage queues its discovery once.
    w.walk(&mut d, BY_TAPESTRY);
    w.walk(&mut d, TAPESTRY_DOOR);
    w.walk(&mut d, PASSAGE_TOP);
    assert_eq!(w.s.take_ext_jobs().len(), 1);
    assert!(w.s.remove_player(d.id));
    assert!(w.s.explored_of(7_000_001).is_none(), "offline");
}

/// Review fix: a friend hears the new place as one walks into it (not only
/// at a zone change), at most once every two seconds.
#[test]
fn friends_hear_a_new_place_as_one_walks_into_it() {
    use protocol::{FriendEvent, Location};
    let places = |msgs: Vec<ServerMsg>| -> Vec<String> {
        msgs.into_iter()
            .filter_map(|m| match m {
                ServerMsg::Friend {
                    event:
                        FriendEvent::Moved {
                            location: Location::Castle { place },
                            ..
                        },
                } => Some(place),
                _ => None,
            })
            .collect()
    };
    let mut w = Castle::new(None);
    let mut ada = w.join(
        7_000_011,
        "Ada",
        castle::WEST,
        vec![7_000_012],
        Default::default(),
    );
    let mut bea = w.join(
        7_000_012,
        "Bea",
        castle::EAST,
        vec![7_000_011],
        Default::default(),
    );
    bea.msgs();
    // Along the corridor: the same place, nothing.
    w.walk(&mut ada, [-20.0, 63.0, 7.0]);
    w.wait(200);
    assert!(places(bea.msgs()).is_empty());
    // Through its west arch (a new place), then straight on to the balcony
    // within two seconds: the arch now, the balcony held back.
    w.walk(&mut ada, [-29.0, 63.0, 7.0]);
    w.wait(200);
    assert_eq!(
        places(bea.msgs()),
        vec!["Grand Staircase Seventh-Floor East Arch".to_owned()]
    );
    w.walk(&mut ada, [-31.0, 63.0, 7.0]);
    w.wait(200);
    assert!(places(bea.msgs()).is_empty());
    // A later step on the balcony tells it.
    w.wait(2_000);
    w.walk(&mut ada, [-32.0, 63.0, 7.0]);
    w.wait(200);
    assert_eq!(
        places(bea.msgs()),
        vec!["Grand Staircase, Seventh-Floor East Balcony".to_owned()]
    );
    assert_eq!(w.metrics.friend_place_notices.get(), 2);
}

/// The audit flags an explore grant that no discovery explains.
#[tokio::test]
async fn the_audit_checks_explore_grants() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let eve = app.player("Serpentyne").await;
    let mut tx = pool.begin().await.unwrap();
    server::progression::grant_xp(
        &mut tx,
        eve.id,
        XpSource::Explore,
        Some(3),
        100,
        chrono::Utc::now(),
    )
    .await
    .unwrap();
    tx.commit().await.unwrap();
    let report = server::ledger::audit::reconcile(&pool, &[eve.id])
        .await
        .unwrap();
    assert!(
        report.problems.iter().any(|p| p.check == "discovery"),
        "{report:?}"
    );
}
