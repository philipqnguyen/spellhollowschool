//! The game clock and the timetable: game time from UTC at
//! period boundaries, across game midnight and before the epoch; the join
//! window's edges; sessions that run past midnight; and the server's clock
//! (monotonic, with the development skew the schedule route follows).

mod common;

use std::time::{Duration, Instant};

use axum::http::StatusCode;
use common::TestApp;
use serde_json::json;
use server::catalog::shipped;
use server::clock::ServerClock;
use sim_core::{Period, Schedule, SessionKey};

/// Real ms from the epoch to game time `h:m` of game day 0.
fn at(h: i64, m: i64) -> i64 {
    let s = &shipped().schedule.clock;
    s.epoch_unix_ms + (h * 3_600 + m * 60) * s.day_ms() / 86_400
}

#[test]
fn periods_change_exactly_at_their_starts() {
    let c = &shipped().schedule.clock;
    // Game midnight is night; morning starts at 06:00 to the millisecond.
    assert_eq!(c.period(at(0, 0)), Period::Night);
    assert_eq!(c.period(at(6, 0) - 1), Period::Night);
    assert_eq!(c.period(at(6, 0)), Period::Morning);
    assert_eq!(c.period(at(14, 0) - 1), Period::Morning);
    assert_eq!(c.period(at(14, 0)), Period::Afternoon);
    assert_eq!(c.period(at(22, 0) - 1), Period::Afternoon);
    assert_eq!(c.period(at(22, 0)), Period::Night);
    // Each period is a third of the day: 40 real minutes.
    assert_eq!(at(14, 0) - at(6, 0), 40 * 60_000);
    // The next change, including the one after midnight.
    assert_eq!(c.next_period(at(6, 0)), (Period::Afternoon, at(14, 0)));
    assert_eq!(
        c.next_period(at(23, 0)),
        (Period::Morning, at(6, 0) + c.day_ms())
    );
    // Lighting: dark at night, full by day, ramping in between.
    assert_eq!(c.daylight(at(3, 0)), 0.0);
    assert_eq!(c.daylight(at(12, 0)), 1.0);
    let dawn = c.daylight(at(6, 30));
    assert!(dawn > 0.0 && dawn < 1.0, "{dawn}");
}

#[test]
fn game_days_roll_over_at_game_midnight_and_before_the_epoch() {
    let c = &shipped().schedule.clock;
    let day = c.day_ms();
    assert_eq!(day, 7_200_000, "a game day is two real hours");
    let t = c.game_time(c.epoch_unix_ms + day - 1);
    assert_eq!((t.day, t.secs), (0, 86_399));
    let t = c.game_time(c.epoch_unix_ms + day);
    assert_eq!((t.day, t.secs), (1, 0));
    // Twelve game days per real day.
    assert_eq!(c.game_time(c.epoch_unix_ms + 86_400_000).day, 12);
    // Before the epoch: day -1, late at night.
    let t = c.game_time(c.epoch_unix_ms - 1);
    assert_eq!((t.day, t.secs), (-1, 86_399));
    assert_eq!(c.period(c.epoch_unix_ms - 1), Period::Night);
    // Round trips.
    for ms in [at(9, 0), at(17, 0) + 5 * day, at(23, 59) - 3 * day] {
        let t = c.game_time(ms);
        assert!(c.unix_ms(t.day, t.secs) <= ms);
        assert!(ms - c.unix_ms(t.day, t.secs) < day / 86_400 + 1);
    }
}

#[test]
fn the_join_window_and_the_next_session() {
    let schedule = &shipped().schedule;
    let charms = schedule.class("charms").unwrap();
    let start = at(8, 0);
    let s = schedule.joinable(charms, start).expect("open at the start");
    assert_eq!(s.key, SessionKey { day: 0, slot: 0 });
    assert_eq!(
        s.join_until_ms - s.start_ms,
        i64::from(charms.join_secs) * 1_000
    );
    assert!(schedule.joinable(charms, start - 1).is_none(), "not before");
    assert!(schedule.joinable(charms, s.join_until_ms - 1).is_some());
    assert!(
        schedule.joinable(charms, s.join_until_ms).is_none(),
        "closed"
    );
    // Running, but no longer admitting, until the end.
    assert_eq!(schedule.current(charms, s.join_until_ms), Some(s));
    assert_eq!(schedule.current(charms, s.end_ms), None);
    // After the day's last session comes tomorrow's first.
    let last = schedule.next(charms, start);
    assert_eq!(last.key, SessionKey { day: 0, slot: 1 });
    assert_eq!(last.start_ms, at(17, 0));
    let tomorrow = schedule.next(charms, last.start_ms);
    assert_eq!(tomorrow.key, SessionKey { day: 1, slot: 0 });
    assert_eq!(tomorrow.start_ms, start + schedule.clock.day_ms());
    // Sessions come every 45 then 75 real minutes: none is locked out.
    assert_eq!(last.start_ms - start, 45 * 60_000);
    assert_eq!(tomorrow.start_ms - last.start_ms, 75 * 60_000);
    assert_eq!(SessionKey::from_id(tomorrow.key.id()), tomorrow.key);
}

#[test]
fn a_session_past_game_midnight_is_still_current_the_next_day() {
    let mut v: serde_json::Value =
        serde_json::from_str(include_str!("../../../data/classes/schedule.json")).unwrap();
    v["classes"][0]["sessions"] = json!(["23:55"]);
    v["classes"] = json!([v["classes"][0].clone()]);
    v["slots"] = json!(["08:00", "10:30", "14:30", "17:00", "23:55"]);
    let schedule = Schedule::from_json(&v.to_string()).unwrap();
    let class = schedule.class("charms").unwrap();
    let start = at(23, 55);
    // Nine real minutes are 108 game minutes: it ends on day 1.
    let after_midnight = at(1, 0) + schedule.clock.day_ms();
    assert!(after_midnight < start + i64::from(class.duration_secs) * 1_000);
    let s = schedule
        .current(class, after_midnight)
        .expect("still running");
    assert_eq!(s.key, SessionKey { day: 0, slot: 0 });
    assert!(!s.joinable(after_midnight));
    assert_eq!(
        schedule.joinable(class, start + 1).map(|s| s.start_ms),
        Some(start)
    );
}

#[test]
fn the_server_clock_is_monotonic_with_a_skew() {
    let t0 = Instant::now();
    let c = ServerClock::starting_at(1_900_000_000_000, t0);
    assert_eq!(c.unix_ms_at(t0 + Duration::from_secs(2)), 1_900_000_002_000);
    assert_eq!(c.unix_ms_at(t0 - Duration::from_secs(1)), 1_899_999_999_000);
    c.set_skew_ms(-3_600_000);
    assert_eq!(c.unix_ms_at(t0), 1_899_996_400_000);
}

#[tokio::test]
async fn the_schedule_route_follows_the_dev_clock() {
    let app = TestApp::dev().await;
    let mut state = app.state.clone();
    state.start_realtime(
        sim_core::World::from_json(
            r#"{ "version": 1, "surfaces": [{ "id": "hall", "kind": "room", "name": "Hall",
                 "level": 0, "cell": "c", "min": [0, 0], "max": [50, 50], "y": 0, "ceiling": 9 }],
                 "spawns": [{ "id": "s", "pos": [10, 0, 10], "yaw": 0 }] }"#,
        )
        .unwrap(),
        server::realtime::chat::WordFilter::from_list("badword"),
    );
    let app = TestApp::from_state(state);
    let p = app.player("Skytalon").await;
    let target = at(8, 1) + 30 * shipped().schedule.clock.day_ms();
    let res = app
        .post("/api/dev/clock", json!({ "at_unix_ms": target }), p.token())
        .await;
    assert_eq!(res.status, StatusCode::OK, "{:?}", res.body);
    let res = app.get("/api/classes/schedule", p.token()).await;
    let now = res.body["now_unix_ms"].as_i64().unwrap();
    assert!((target..target + 5_000).contains(&now), "{now} vs {target}");
    let current = &res.body["classes"][0]["current"];
    assert_eq!(current["joinable"], true, "{current:?}");
    assert_eq!(
        current["start_ms"],
        at(8, 0) + 30 * shipped().schedule.clock.day_ms()
    );
}
