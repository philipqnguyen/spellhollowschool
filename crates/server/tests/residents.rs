//! NPCs in the open castle (docs/architecture.md "NPCs"): the residents'
//! runtime and their daily gifts end to end.
//!
//! - the ghosts, staff, shopkeepers and students stand where their routines
//!   say at given clock times (staff at their points, the off-duty and the
//!   students in class or in bed absent), walkers walk, standers send
//!   nothing, and the students stay within their castle-wide cap;
//! - talking is refused afar, for a non-resident and when rate-limited;
//! - a gift comes once per character, per NPC, per UTC day, however the
//!   claims race, and a retried claim answers the same gift; a ghost gives
//!   only to its own house; house points stay under the NPC source's daily
//!   cap; an NPC's lore shows in the lore book with no subject; the ledgers
//!   (and the NPC gift audit) are clean;
//! - hints never name a secret or a searched find spot, and no hint line is
//!   ever picked for a secret the player found;
//! - NPCs are never the target of a spell, a duel, a trade or Mimic Draught.

#[path = "castle_harness.rs"]
mod castle;
mod common;

use std::sync::Arc;

use bytes::Bytes;
use castle::Castle;
use common::{TestApp, TestPlayer};
use protocol::{
    DuelErrorCode, EntityEntry, EntityId, House, NpcErrorCode, NpcGift, NpcReply, PotionErrorCode,
    ServerMsg, SpellErrorCode, SpellTarget, TradeErrorCode, decode_server,
};
use server::catalog::shipped;
use server::characters::{Character, SavedZone};
use server::realtime::ConnControl;
use server::realtime::ext::{DuelMsg, NpcInput, PotionInput, SpellInput, TradeInput};
use server::realtime::world::{Input, Joining};
use server::residents::{self, Claimed};
use sim_core::npcs::{Activity, LineContext, LineWhen, NpcKind};
use sim_core::spells::Social;
use tokio::sync::mpsc;

/// A character in the castle, joined directly.
struct Player {
    id: EntityId,

    rx: mpsc::Receiver<Bytes>,
}

impl Player {
    fn msgs(&mut self) -> Vec<ServerMsg> {
        let mut v = Vec::new();
        while let Ok(b) = self.rx.try_recv() {
            v.push(decode_server(&b).expect("decodes"));
        }
        v
    }

    fn replies(&mut self) -> Vec<(EntityId, NpcReply)> {
        self.msgs()
            .into_iter()
            .filter_map(|m| match m {
                ServerMsg::NpcReply { id, reply } => Some((id, reply)),
                _ => None,
            })
            .collect()
    }
}

fn house_of(name: &str) -> House {
    House::ALL
        .into_iter()
        .find(|h| h.id() == name.to_lowercase())
        .unwrap()
}

/// Joins character `cid` of `house` at `pos` (every spell learned).
fn join(w: &mut Castle, cid: i64, house: House, pos: [f32; 3]) -> Player {
    let character = Character {
        id: cid,
        name: format!("Tester{cid}"),
        house,
        zone: SavedZone::Castle,
        last_pos: Some(pos),
        appearance_version: 0,
        appearance: protocol::Appearance::default(),
    };
    let mut joining = Joining::alone(character);
    joining.school.learned = shipped().spells.spells.iter().map(|s| s.id).collect();
    joining.school.learned.sort_unstable();
    let (tx, rx) = mpsc::channel(1 << 14);
    let at = w.at(w.ms);
    let id =
        w.s.join(joining, tx, Arc::new(ConnControl::default()), at)
            .expect("joined");
    Player { id, rx }
}

/// The next real moment (after the castle's clock) the game clock reads
/// `hh:mm`.
fn next_game_time(w: &Castle, hh: u32, mm: u32) -> i64 {
    let clock = &shipped().schedule.clock;
    let day = clock.day_ms();
    let in_day = i64::from(hh * 3_600 + mm * 60) * day / 86_400;
    let start = clock.epoch_unix_ms + (w.ms - clock.epoch_unix_ms).div_euclid(day) * day;
    let mut t = start + in_day;
    while t <= w.ms + 200 {
        t += day;
    }
    t
}

/// Moves the castle's clock to game time `hh:mm` and ticks the residents.
fn go_to(w: &mut Castle, hh: u32, mm: u32) -> i64 {
    let t = next_game_time(w, hh, mm);
    w.ms = t - 100;
    w.wait(100);
    t
}

fn point(id: &str) -> [f32; 3] {
    shipped().npcs.routines.points[id].pos
}

fn dist(a: [f32; 3], b: [f32; 3]) -> f32 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()
}

/// The resident entity of roster id `npc`, if spawned.
fn entity(w: &Castle, npc: &str) -> Option<EntityId> {
    w.s.residents()
        .spawned()
        .into_iter()
        .find(|(id, _)| *id == npc)
        .map(|(_, e)| e)
}

fn spawned_students(w: &Castle) -> usize {
    let roster = &shipped().npcs.roster;
    w.s.residents()
        .spawned()
        .into_iter()
        .filter(|(id, _)| roster.get(id).is_some_and(|n| n.kind == NpcKind::Student))
        .count()
}

// ------------------------------------------------------------ the runtime

#[test]
fn residents_stand_where_their_routines_say() {
    let mut w = Castle::new(None);
    w.wait(100);
    let plans = w.s.residents().plans().expect("planned").clone();
    let roster = &shipped().npcs.roster;
    let entities = roster.npcs.iter().filter(|n| n.kind.is_entity()).count();
    assert_eq!(plans.npcs.len(), entities, "every entity NPC planned");

    for (hh, mm) in [(3, 0), (9, 0), (12, 40), (15, 10), (19, 45), (23, 30)] {
        let t = go_to(&mut w, hh, mm);
        for p in &plans.npcs {
            let pose = p.plan.pose(t);
            let e = entity(&w, &p.id);
            if pose.activity == Activity::Hidden {
                assert!(e.is_none(), "{} is out of sight at {hh}:{mm:02}", p.id);
                continue;
            }
            let e = e.unwrap_or_else(|| panic!("{} is about at {hh}:{mm:02}", p.id));
            let at = w.s.position(e).unwrap();
            // A walker moved at most a tick's worth since the residents' tick.
            let slack = if pose.activity == Activity::Walk {
                0.2
            } else {
                0.01
            };
            assert!(
                dist(at, pose.pos) <= slack,
                "{} at {hh}:{mm:02} stands at {at:?}, its routine says {:?}",
                p.id,
                pose.pos
            );
        }
        assert!(spawned_students(&w) <= usize::from(roster.rules.students_max));
    }

    // The staff's own times (data/npcs/routines.json).
    go_to(&mut w, 9, 0);
    let librarian = entity(&w, "staff_librarian").expect("on duty");
    assert!(dist(w.s.position(librarian).unwrap(), point("library_desk")) < 0.01);
    go_to(&mut w, 23, 30);
    assert!(
        entity(&w, "staff_librarian").is_none(),
        "the library is shut"
    );
    assert!(
        entity(&w, "staff_groundskeeper").is_none(),
        "home for the night"
    );
    let caretaker = entity(&w, "staff_caretaker").expect("locking up");
    assert!(dist(w.s.position(caretaker).unwrap(), point("eh_stair_foot")) < 0.01);
    go_to(&mut w, 3, 0);
    assert_eq!(spawned_students(&w), 0, "students are in bed at 03:00");
    // Shopkeepers never move.
    for n in roster.of_kind(NpcKind::Shopkeeper) {
        let step = &shipped().npcs.routines.routines[&n.id].steps[0];
        let e = entity(&w, &n.id).unwrap();
        assert!(
            dist(w.s.position(e).unwrap(), point(&step.to)) < 0.01,
            "{}",
            n.id
        );
    }
}

#[test]
fn walkers_walk_and_standers_send_nothing() {
    let mut w = Castle::new(None);
    w.wait(100);
    // 08:30 + a minute: the caretaker sets off from the undercroft walk to
    // the Trophy Room.
    go_to(&mut w, 8, 31);
    let caretaker = entity(&w, "staff_caretaker").unwrap();
    let clerk = entity(&w, "shop_owl_exchange").unwrap();
    let (a, c) = (
        w.s.position(caretaker).unwrap(),
        w.s.position(clerk).unwrap(),
    );
    w.wait(1_000);
    let (b, d) = (
        w.s.position(caretaker).unwrap(),
        w.s.position(clerk).unwrap(),
    );
    let moved = dist(a, b);
    let speed = shipped().npcs.roster.rules.walk_speed_mps;
    assert!(
        moved > 0.5 && moved < speed * 1.3,
        "walked {moved} m in a second"
    );
    assert_eq!(c, d, "the clerk stands");
    // The same entity throughout: walking is a move, not a respawn.
    assert_eq!(entity(&w, "staff_caretaker"), Some(caretaker));

    // A viewer by the clerk: the clerk enters its view once, then sends no
    // delta while it stands.
    let mut v = join(&mut w, 7_001, House::Skytalon, point("shop_exchange"));
    w.wait(300);
    let msgs = v.msgs();
    let entered = msgs.iter().any(|m| match m {
        ServerMsg::Snapshot { entered, .. } => entered.iter().any(|e| match e {
            EntityEntry::Full(info) => {
                info.id == clerk && info.npc.as_deref() == Some("shop_owl_exchange")
            }
            EntityEntry::Cached { id, .. } => *id == clerk,
        }),
        _ => false,
    });
    assert!(entered, "the clerk is in view, as an NPC");
    // The visible set as the client keeps it (deltas index it once `left`
    // and `entered` apply).
    fn apply(vis: &mut Vec<EntityId>, m: &ServerMsg) -> Vec<EntityId> {
        let ServerMsg::Snapshot {
            entered,
            left,
            deltas,
            ..
        } = m
        else {
            return Vec::new();
        };
        vis.retain(|id| !left.contains(id));
        vis.extend(entered.iter().map(EntityEntry::id));
        vis.sort_unstable();
        deltas.iter().map(|d| vis[usize::from(d.index)]).collect()
    }
    let mut vis: Vec<EntityId> = Vec::new();
    for m in &msgs {
        apply(&mut vis, m);
    }
    assert!(vis.contains(&clerk));
    w.wait(2_000);
    for m in v.msgs() {
        let moved = apply(&mut vis, &m);
        assert!(!moved.contains(&clerk), "no delta for a stander");
        if let ServerMsg::Snapshot { full, .. } = &m {
            assert!(full.iter().all(|s| s.id != clerk));
        }
    }
}

#[test]
fn students_stay_within_their_cap_all_day() {
    let mut w = Castle::new(None);
    w.wait(100);
    let max = usize::from(shipped().npcs.roster.rules.students_max);
    let mut seen_out = 0;
    for hh in 0..24 {
        go_to(&mut w, hh, 20);
        let n = spawned_students(&w);
        assert!(n <= max, "{n} students at {hh}:20");
        seen_out = seen_out.max(n);
    }
    assert!(seen_out > 0, "students are about during the day");
}

#[test]
fn talk_is_refused_afar_for_strangers_and_when_spammed() {
    let mut w = Castle::new(None);
    go_to(&mut w, 10, 0);
    let clerk = entity(&w, "shop_owl_exchange").unwrap();
    let at = w.s.position(clerk).unwrap();
    let mut near = join(&mut w, 7_101, House::Burrowsby, at);
    let mut far = join(&mut w, 7_102, House::Burrowsby, point("eh_mid"));
    w.wait(600);
    let reach = shipped().npcs.roster.rules.talk_reach_m;
    assert!(dist(w.s.position(near.id).unwrap(), at) <= reach);
    assert!(dist(w.s.position(far.id).unwrap(), at) > reach);

    let talk = |id| Input::Npc(NpcInput::Interact { id });
    send(&mut w, &near, talk(clerk));
    assert_eq!(
        near.replies(),
        vec![(clerk, NpcReply::Talk { given_today: false })],
        "a shopkeeper just talks (its shop opens with Interact)"
    );
    send(&mut w, &far, talk(clerk));
    assert_eq!(
        far.replies(),
        vec![(
            clerk,
            NpcReply::Refused {
                code: NpcErrorCode::TooFar
            }
        )]
    );
    // A player is no NPC; nor is an entity that is gone.
    send(&mut w, &near, talk(far.id));
    send(&mut w, &near, talk(0xdead_beef));
    let codes: Vec<NpcReply> = near.replies().into_iter().map(|(_, r)| r).collect();
    let unknown = NpcReply::Refused {
        code: NpcErrorCode::UnknownNpc,
    };
    assert_eq!(codes, vec![unknown.clone(), unknown]);
    // The shared action bucket.
    for _ in 0..40 {
        send(&mut w, &near, talk(clerk));
    }
    let replies = near.replies();
    assert_eq!(replies.len(), 40);
    assert!(replies.iter().any(|(_, r)| *r
        == NpcReply::Refused {
            code: NpcErrorCode::RateLimited
        }));
    // Nobody else hears a word.
    assert!(far.replies().is_empty());
}

/// One input from `p` now.
fn send(w: &mut Castle, p: &Player, input: Input) {
    let at = w.at(w.ms);
    w.s.input(p.id, at, input);
}

#[test]
fn npcs_are_never_targets() {
    let mut w = Castle::new(None);
    go_to(&mut w, 10, 0);
    let clerk = entity(&w, "shop_owl_exchange").unwrap();
    let at = w.s.position(clerk).unwrap();
    let mut me = join(&mut w, 7_201, House::Roarwing, at);
    w.wait(600);
    let spell_of = |pick: fn(&Social) -> bool| {
        shipped()
            .spells
            .spells
            .iter()
            .find(|s| pick(&s.social))
            .unwrap()
            .id
    };
    let flourish = spell_of(|s| matches!(s, Social::Flourish { .. }));
    let bind = spell_of(|s| matches!(s, Social::Bind { .. }));
    let recolour = spell_of(|s| matches!(s, Social::Recolour { .. }));
    for spell in [flourish, bind, recolour] {
        send(
            &mut w,
            &me,
            Input::Spell(SpellInput::Cast {
                spell,
                target: SpellTarget::Entity { id: clerk },
            }),
        );
        w.wait(1_100);
    }
    send(
        &mut w,
        &me,
        Input::Duel(DuelMsg::Challenge { target: clerk }),
    );
    let clerk_name = shipped()
        .npcs
        .roster
        .get("shop_owl_exchange")
        .unwrap()
        .name
        .clone();
    send(
        &mut w,
        &me,
        Input::Duel(DuelMsg::ChallengeName { name: clerk_name }),
    );
    send(
        &mut w,
        &me,
        Input::Trade(TradeInput::Request { target: clerk }),
    );
    send(
        &mut w,
        &me,
        Input::Potion(PotionInput::MimicDraughtAsk {
            item_id: 1,
            target: clerk,
        }),
    );
    let msgs = me.msgs();
    let spells: Vec<SpellErrorCode> = msgs
        .iter()
        .filter_map(|m| match m {
            ServerMsg::SpellError { code, .. } => Some(*code),
            _ => None,
        })
        .collect();
    assert_eq!(spells, vec![SpellErrorCode::BadTarget; 3], "{msgs:?}");
    assert!(
        !msgs
            .iter()
            .any(|m| matches!(m, ServerMsg::SpellCast { .. })),
        "nothing was cast at the clerk"
    );
    let duels: Vec<DuelErrorCode> = msgs
        .iter()
        .filter_map(|m| match m {
            ServerMsg::DuelError { code, .. } => Some(*code),
            _ => None,
        })
        .collect();
    assert_eq!(duels, vec![DuelErrorCode::UnknownTarget; 2]);
    assert!(msgs.iter().any(|m| matches!(
        m,
        ServerMsg::TradeError {
            code: TradeErrorCode::UnknownTarget,
            ..
        }
    )));
    assert!(msgs.iter().any(|m| matches!(
        m,
        ServerMsg::PotionError {
            code: PotionErrorCode::NotOnline,
            ..
        }
    )));
}

// ------------------------------------------------------------ gifts

async fn gift_rows(pool: &sqlx::PgPool, cid: i64) -> i64 {
    sqlx::query_scalar("SELECT count(*) FROM npc_gifts WHERE character_id = $1")
        .bind(cid)
        .fetch_one(pool)
        .await
        .unwrap()
}

async fn audit_clean(pool: &sqlx::PgPool, ids: &[i64]) {
    server::ledger::audit::reconcile(pool, ids)
        .await
        .unwrap()
        .assert_clean();
    let npc = residents::audit::problems(pool, ids).await.unwrap();
    assert!(npc.is_empty(), "{npc:?}");
}

/// Noon UTC, `days` from a fixed day (well within the clock's range).
fn noon(days: i64) -> i64 {
    1_800_000_000_000 - 1_800_000_000_000 % 86_400_000 + days * 86_400_000 + 43_200_000
}

#[tokio::test]
async fn a_ghost_gives_once_a_day_and_only_to_its_house() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let ada = app.player("Skytalon").await;
    let house = house_of(&ada.house);
    let ghost = "ghost_house_c";
    let first = residents::claim(&pool, ada.id, house, ghost, noon(0))
        .await
        .unwrap();
    let Claimed::Gift(gift) = first else {
        panic!("{first:?}")
    };
    // Again today: nothing more, however asked.
    for t in [noon(0) + 1, noon(0) + 11 * 3_600_000] {
        assert_eq!(
            residents::claim(&pool, ada.id, house, ghost, t)
                .await
                .unwrap(),
            Claimed::AlreadyToday
        );
    }
    // A retried claim (its first attempt's commit landed) answers the same.
    assert_eq!(
        residents::given_today(&pool, ada.id, ghost, noon(0))
            .await
            .unwrap(),
        Some(gift.clone())
    );
    assert_eq!(gift_rows(&pool, ada.id).await, 1);
    // Another house's ghost gives nothing (and writes nothing).
    for other in ["ghost_house_a", "ghost_house_b", "ghost_house_d"] {
        assert_eq!(
            residents::claim(&pool, ada.id, house, other, noon(0))
                .await
                .unwrap(),
            Claimed::Nothing
        );
    }
    // Students and shopkeepers never give.
    for n in ["student_c1", "shop_owl_exchange"] {
        assert_eq!(
            residents::claim(&pool, ada.id, house, n, noon(0))
                .await
                .unwrap(),
            Claimed::Nothing
        );
    }
    assert_eq!(gift_rows(&pool, ada.id).await, 1);
    // Tomorrow, again.
    assert!(matches!(
        residents::claim(&pool, ada.id, house, ghost, noon(1))
            .await
            .unwrap(),
        Claimed::Gift(_)
    ));
    assert_eq!(gift_rows(&pool, ada.id).await, 2);
    audit_clean(&pool, &[ada.id]).await;
}

#[tokio::test]
async fn racing_claims_give_once() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let bea = app.player("Burrowsby").await;
    let house = house_of(&bea.house);
    for (npc, day) in [
        ("staff_groundskeeper", 0),
        ("staff_librarian", 0),
        ("staff_caretaker", 0),
        ("ghost_house_b", 0),
        ("staff_groundskeeper", 1),
    ] {
        let claims: Vec<_> = (0..8)
            .map(|_| {
                let pool = pool.clone();
                let id = bea.id;
                tokio::spawn(
                    async move { residents::claim(&pool, id, house, npc, noon(day)).await },
                )
            })
            .collect();
        let mut got = Vec::new();
        for c in claims {
            got.push(c.await.unwrap());
        }
        let gifts: Vec<&NpcGift> = got
            .iter()
            .filter_map(|c| match c.as_ref().unwrap() {
                Claimed::Gift(g) => Some(g),
                _ => None,
            })
            .collect();
        assert_eq!(gifts.len(), 1, "{npc} day {day}: {got:?}");
        assert_eq!(
            got.iter()
                .filter(|c| matches!(c.as_ref().unwrap(), Claimed::AlreadyToday))
                .count(),
            7
        );
        if let NpcGift::Item { item_def, quantity } = *gifts[0] {
            let units: i64 = sqlx::query_scalar(
                "SELECT COALESCE(SUM(l.quantity), 0)::bigint FROM item_ledger l
                 JOIN npc_gifts g ON g.id = l.ref_id
                 WHERE l.reason = 'npc_gift' AND g.character_id = $1 AND g.npc = $2
                   AND g.day = $3 AND l.item_def = $4",
            )
            .bind(bea.id)
            .bind(npc)
            .bind(residents::day_date(noon(day)))
            .bind(i32::from(item_def))
            .fetch_one(&pool)
            .await
            .unwrap();
            assert_eq!(units, i64::from(quantity));
        }
    }
    assert_eq!(gift_rows(&pool, bea.id).await, 5);
    audit_clean(&pool, &[bea.id]).await;
}

#[tokio::test]
async fn house_points_stay_under_the_npc_cap() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let cy = app.player("Serpentyne").await;
    let house = house_of(&cy.house);
    let rule = shipped().house_cup.rule(sim_core::CupSource::Npc);
    let cap = i64::from(rule.daily_cap);
    // Day 0: the cap is already used up (other NPC points today): the ghost
    // gives something else.
    sqlx::query(
        "INSERT INTO daily_caps (character_id, day, cap, used) VALUES ($1, $2, 'hp_npc', $3)",
    )
    .bind(cy.id)
    .bind(residents::day_date(noon(0)))
    .bind(cap as i32)
    .execute(&pool)
    .await
    .unwrap();
    let c = residents::claim(&pool, cy.id, house, "ghost_house_d", noon(0))
        .await
        .unwrap();
    assert!(
        !matches!(c, Claimed::Gift(NpcGift::HousePoint { .. })),
        "{c:?}"
    );
    // A month of days: every house point is the source's points, at most
    // the cap a day, all to Serpentyne.
    let mut points_days = 0;
    for day in 1..=30 {
        let c = residents::claim(&pool, cy.id, house, "ghost_house_d", noon(day))
            .await
            .unwrap();
        if let Claimed::Gift(NpcGift::HousePoint { points }) = c {
            assert_eq!(Some(u16::from(points)), rule.points);
            points_days += 1;
        }
    }
    assert!(points_days > 0, "a ghost gives a house point now and then");
    let over: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM (SELECT g.day, sum(h.points) s FROM house_points h
         JOIN npc_gifts g ON g.id = h.ref_id
         WHERE h.character_id = $1 AND h.source = 'npc' GROUP BY 1) x WHERE x.s > $2",
    )
    .bind(cy.id)
    .bind(cap)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(over, 0);
    let wrong_house: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM house_points WHERE character_id = $1 AND house <> 'serpentyne'",
    )
    .bind(cy.id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(wrong_house, 0);
    audit_clean(&pool, &[cy.id]).await;
}

#[tokio::test]
async fn an_npcs_lore_is_in_the_lore_book() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let dee = app.player("Roarwing").await;
    let house = house_of(&dee.house);
    // The ghost's table gives lore or a house point: days until lore.
    let mut told = None;
    for day in 0..40 {
        if let Claimed::Gift(NpcGift::Lore { lore_id, title }) =
            residents::claim(&pool, dee.id, house, "ghost_house_a", noon(day))
                .await
                .unwrap()
        {
            told = Some((lore_id, title));
            break;
        }
    }
    let (lore_id, title) = told.expect("a ghost tells lore");
    assert_eq!(title, shipped().lore.get(lore_id).unwrap().title);
    let row: (Option<i64>, Option<String>) = sqlx::query_as(
        "SELECT attendance_id, npc FROM lore_unlocks WHERE character_id = $1 AND lore_id = $2",
    )
    .bind(dee.id)
    .bind(i32::from(lore_id))
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(row, (None, Some("ghost_house_a".to_owned())));
    let book = app.get("/api/lore", dee.token()).await;
    let items = book.body["items"].as_array().unwrap();
    let entry = items
        .iter()
        .find(|i| i["id"].as_u64() == Some(u64::from(lore_id)))
        .unwrap_or_else(|| panic!("{:?}", book.body));
    assert!(entry["subject"].is_null());
    assert_eq!(entry["npc"], "ghost_house_a");
    let one = app.get(&format!("/api/lore/{lore_id}"), dee.token()).await;
    assert_eq!(one.body["npc"], "ghost_house_a");
    audit_clean(&pool, &[dee.id]).await;
}

// ------------------------------------------------------------ talk in the world

/// Talks to `npc` (spawned now) from right beside it; runs the jobs.
async fn talk(w: &mut Castle, pool: &sqlx::PgPool, who: &mut Player, npc: &str) -> NpcReply {
    let e = entity(w, npc).unwrap_or_else(|| panic!("{npc} is about"));
    send(w, who, Input::Npc(NpcInput::Interact { id: e }));
    w.run_jobs(pool).await;
    let r = who.replies();
    assert_eq!(r.len(), 1, "{r:?}");
    assert_eq!(r[0].0, e);
    r[0].1.clone()
}

#[tokio::test]
async fn talking_to_a_ghost_gives_todays_gift_then_talk() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let eve: TestPlayer = app.player("Skytalon").await;
    let mut w = Castle::new(None);
    // The ghost floats at its first point at 01:00 (its 00:00 step).
    go_to(&mut w, 1, 0);
    let ghost = entity(&w, "ghost_house_c").expect("about");
    let at = w.s.position(ghost).unwrap();
    let mut me = join(&mut w, eve.id, House::Skytalon, at);
    w.wait(600);
    let first = talk(&mut w, &pool, &mut me, "ghost_house_c").await;
    assert!(matches!(first, NpcReply::Gift { .. }), "{first:?}");
    let again = talk(&mut w, &pool, &mut me, "ghost_house_c").await;
    assert_eq!(again, NpcReply::Talk { given_today: true });
    assert_eq!(w.run_jobs(&pool).await, 0, "known: no database work");
    assert_eq!(gift_rows(&pool, eve.id).await, 1);
    // On its public round (06:00 to the sixth-floor corridor), another
    // house's player gets a word and nothing more.
    let fay = app.player("Burrowsby").await;
    go_to(&mut w, 7, 30);
    let ghost = entity(&w, "ghost_house_c").expect("about");
    let at = w.s.position(ghost).unwrap();
    let mut her = join(&mut w, fay.id, House::Burrowsby, at);
    w.wait(600);
    let r = talk(&mut w, &pool, &mut her, "ghost_house_c").await;
    assert_eq!(r, NpcReply::Talk { given_today: false });
    assert_eq!(gift_rows(&pool, fay.id).await, 0);
    audit_clean(&pool, &[eve.id, fay.id]).await;
}

// ------------------------------------------------------------ hints

#[tokio::test]
async fn hints_never_name_a_secret_or_a_searched_spot() {
    let app = TestApp::dev().await;
    let pool = app.state.pool.clone();
    let gus = app.player("Skytalon").await;
    let house = House::Skytalon;
    // Every spot searched today but one.
    let spots = &shipped().finds.spots;
    let keep = spots
        .iter()
        .find(|s| server::classes::lore::spot_place(&s.id).is_some_and(|(_, g)| g.is_none()))
        .unwrap();
    for s in spots.iter().filter(|s| s.id != keep.id) {
        sqlx::query(
            "INSERT INTO find_cooldowns (character_id, spot_id, ready_at)
             VALUES ($1, $2, now() + interval '1 day')",
        )
        .bind(gus.id)
        .bind(&s.id)
        .execute(&pool)
        .await
        .unwrap();
    }
    let mut conn = pool.acquire().await.unwrap();
    let places = server::classes::lore::hint_places(&mut conn, gus.id, house)
        .await
        .unwrap();
    let (keep_place, _) = server::classes::lore::spot_place(&keep.id).unwrap();
    assert_eq!(places, vec![keep_place]);
    for seed in 0..50 {
        let h = server::classes::lore::hint_seeded(&mut conn, gus.id, house, seed)
            .await
            .unwrap();
        assert_eq!(h.as_deref(), Some(keep_place));
    }
    drop(conn);
    // Staff hint gifts over many days name that place only.
    for day in 0..20 {
        for npc in ["staff_caretaker", "staff_librarian", "staff_groundskeeper"] {
            if let Claimed::Gift(NpcGift::Hint { place }) =
                residents::claim(&pool, gus.id, house, npc, noon(day))
                    .await
                    .unwrap()
            {
                assert_eq!(place, keep_place);
                assert!(!server::classes::lore::secret_place(&place));
            }
        }
    }
    audit_clean(&pool, &[gus.id]).await;
}

#[test]
fn no_hint_line_points_at_a_found_secret() {
    // Lines are the client's (`NpcBook.pick`, the same sim-core code): a hint
    // line naming a secret is never picked once that secret is found.
    let data = &shipped().npcs;
    let secrets: Vec<String> = shipped()
        .secrets
        .secrets
        .iter()
        .map(|s| s.id.clone())
        .collect();
    let mut checked = 0;
    for n in data.roster.of_kind(NpcKind::Portrait) {
        let lines = data.lines.lines(n.lines_key());
        for found in &secrets {
            let cx = LineContext {
                house: "skytalon".into(),
                found_secrets: vec![found.clone()],
                ..LineContext::default()
            };
            for seed in 0..64 {
                if let Some(i) = data
                    .lines
                    .pick(n.lines_key(), LineWhen::Hint, &cx, seed, &[])
                {
                    checked += 1;
                    assert_ne!(
                        lines[usize::from(i)].secret.as_ref(),
                        Some(found),
                        "{} hints at {found}, found already",
                        n.id
                    );
                }
            }
            if let Some(i) = data.lines.daily_hint(n.lines_key(), &cx, 20_000, 42) {
                assert_ne!(lines[usize::from(i)].secret.as_ref(), Some(found));
            }
        }
        // Everything found: no hint at all (every hint line names a secret).
        let all = LineContext {
            house: "skytalon".into(),
            found_secrets: secrets.clone(),
            ..LineContext::default()
        };
        assert!(
            data.lines
                .pick(n.lines_key(), LineWhen::Hint, &all, 1, &[])
                .is_none(),
            "{}",
            n.id
        );
    }
    assert!(checked > 0);
}
