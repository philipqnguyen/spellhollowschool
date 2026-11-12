//! The game clock and the class schedule:
//! `data/classes/schedule.json` ([`Schedule`]).
//!
//! Game time is a pure function of the real time (UTC, Unix milliseconds),
//! so every server and every client agrees without talking: one game day
//! lasts [`ClockRules::day_real_secs`] real seconds (7,200: twelve game days
//! per real day) counted from [`ClockRules::epoch_unix_ms`]. The day has
//! three [`Period`]s, morning, afternoon and night, which set the lighting.
//!
//! Classes run at fixed game times of every game day
//! ([`ClassSchedule::sessions`]), so they come round several times per real
//! hour-or-two and no time zone is locked out. A session lasts
//! `duration_secs` real seconds; its door admits students during the first
//! `join_secs`. Sessions are named by a [`SessionKey`] (game day and slot),
//! whose [`SessionKey::id`] keys the database rows.
//!
//! All arithmetic is integer: a game time maps to the real millisecond it
//! starts at (floored), and the files only use whole game minutes, which map
//! to whole real milliseconds at any day length that divides evenly.
//!
//! **The timetable (v2).** The day has fixed `slots` (game times
//! when classes start); every session starts at one of them, and a slot
//! runs several classes at once in different rooms ([`Schedule::slot_of`],
//! [`Schedule::in_slot`]). No two classes share a door (the door names its
//! class). Two classes may share a room layout (History of Magic reuses
//! the Charms room), but never in the same slot (a slot's classes
//! are in different rooms). A `night_only` class (Astronomy) starts only in
//! the night period. The file also names the nine subjects for display
//! (`subjects`).

use std::collections::{BTreeMap, HashSet};
use std::fmt;

use serde::{Deserialize, Deserializer, Serialize, Serializer};

use crate::classes::Subject;

/// The schedule schema version this crate reads (2: slots, `night_only`
/// and subject names).
pub const SCHEDULE_VERSION: u32 = 2;

/// Game seconds in a game day.
pub const GAME_DAY_SECS: u32 = 86_400;

#[derive(Debug, thiserror::Error)]
pub enum ScheduleError {
    #[error("schedule JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("schedule is inconsistent: {0}")]
    Invalid(String),
}

/// A part of the game day.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Period {
    Morning,
    Afternoon,
    Night,
}

impl Period {
    pub const ALL: [Period; 3] = [Period::Morning, Period::Afternoon, Period::Night];

    pub fn id(self) -> &'static str {
        match self {
            Period::Morning => "morning",
            Period::Afternoon => "afternoon",
            Period::Night => "night",
        }
    }
}

/// A time of the game day, written `"HH:MM"` in the files (game seconds
/// since game midnight in code).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct TimeOfDay(pub u32);

impl TimeOfDay {
    pub fn from_hm(h: u32, m: u32) -> Option<TimeOfDay> {
        (h < 24 && m < 60).then_some(TimeOfDay(h * 3_600 + m * 60))
    }

    pub fn secs(self) -> u32 {
        self.0
    }
}

impl fmt::Display for TimeOfDay {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:02}:{:02}", self.0 / 3_600, self.0 / 60 % 60)
    }
}

impl Serialize for TimeOfDay {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.collect_str(self)
    }
}

impl<'de> Deserialize<'de> for TimeOfDay {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        let s = String::deserialize(d)?;
        let bad = || serde::de::Error::custom(format!("{s:?} is not a game time \"HH:MM\""));
        let (h, m) = s.split_once(':').ok_or_else(bad)?;
        if h.len() != 2 || m.len() != 2 {
            return Err(bad());
        }
        let (h, m) = (h.parse().map_err(|_| bad())?, m.parse().map_err(|_| bad())?);
        TimeOfDay::from_hm(h, m).ok_or_else(bad)
    }
}

/// When a period starts.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct PeriodStart {
    pub period: Period,
    pub starts: TimeOfDay,
}

/// The clock's numbers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClockRules {
    /// The real moment game day 0 began (Unix ms, UTC).
    pub epoch_unix_ms: i64,
    /// Real seconds per game day.
    pub day_real_secs: u32,
    /// Each period's start, in order through the day; the last one runs past
    /// midnight to the first.
    pub periods: Vec<PeriodStart>,
}

/// A moment in game time.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct GameTime {
    /// Game days since the epoch (negative before it).
    pub day: i64,
    /// Game seconds since game midnight (0..`GAME_DAY_SECS`).
    pub secs: u32,
}

impl ClockRules {
    /// Real milliseconds per game day.
    pub fn day_ms(&self) -> i64 {
        i64::from(self.day_real_secs) * 1_000
    }

    /// The game time at real time `unix_ms`.
    pub fn game_time(&self, unix_ms: i64) -> GameTime {
        let d = unix_ms.saturating_sub(self.epoch_unix_ms);
        let day_ms = self.day_ms();
        let in_day = d.rem_euclid(day_ms);
        let secs = i128::from(in_day) * i128::from(GAME_DAY_SECS) / i128::from(day_ms);
        GameTime {
            day: d.div_euclid(day_ms),
            secs: secs as u32,
        }
    }

    /// The first real millisecond of game time `secs` on game day `day`.
    pub fn unix_ms(&self, day: i64, secs: u32) -> i64 {
        let into = i128::from(secs) * i128::from(self.day_ms()) / i128::from(GAME_DAY_SECS);
        self.epoch_unix_ms
            .saturating_add(day.saturating_mul(self.day_ms()))
            .saturating_add(into as i64)
    }

    /// The period at game time `secs`.
    pub fn period_at_secs(&self, secs: u32) -> Period {
        // The last period that started at or before `secs`; before the
        // first start it is still the last one (it runs past midnight).
        self.periods
            .iter()
            .rev()
            .find(|p| p.starts.secs() <= secs)
            .or(self.periods.last())
            .map_or(Period::Morning, |p| p.period)
    }

    /// The period at real time `unix_ms`.
    pub fn period(&self, unix_ms: i64) -> Period {
        self.period_at_secs(self.game_time(unix_ms).secs)
    }

    /// The next period change after `unix_ms`: the period and when it starts.
    pub fn next_period(&self, unix_ms: i64) -> (Period, i64) {
        let now = self.game_time(unix_ms);
        for day in [now.day, now.day + 1] {
            for p in &self.periods {
                let at = self.unix_ms(day, p.starts.secs());
                if at > unix_ms {
                    return (p.period, at);
                }
            }
        }
        unreachable!("a validated clock has periods every day")
    }

    /// How much daylight there is at `unix_ms`, 0 (night) to 1 (midday),
    /// for the client's lighting: 0 through the night period, rising over
    /// the first game hour of the morning, 1 until the last game hour of the
    /// afternoon, falling to 0 as night starts.
    pub fn daylight(&self, unix_ms: i64) -> f32 {
        const RAMP: u32 = 3_600;
        let secs = self.game_time(unix_ms).secs;
        let start = |p: Period| {
            self.periods
                .iter()
                .find(|s| s.period == p)
                .map_or(0, |s| s.starts.secs())
        };
        let (dawn, dusk) = (start(Period::Morning), start(Period::Night));
        if self.period_at_secs(secs) == Period::Night {
            return 0.0;
        }
        let since_dawn = (secs + GAME_DAY_SECS - dawn) % GAME_DAY_SECS;
        let to_dusk = (dusk + GAME_DAY_SECS - secs) % GAME_DAY_SECS;
        let rise = (since_dawn as f32 / RAMP as f32).min(1.0);
        let fall = (to_dusk as f32 / RAMP as f32).min(1.0);
        rise.min(fall)
    }

    fn validate(&self) -> Result<(), String> {
        if self.day_real_secs < 600 || self.day_real_secs > 86_400 {
            return Err("day_real_secs must be 600..=86400".into());
        }
        if self.periods.len() != Period::ALL.len()
            || Period::ALL
                .iter()
                .any(|p| self.periods.iter().filter(|s| s.period == *p).count() != 1)
        {
            return Err("periods must name morning, afternoon and night once each".into());
        }
        if self.periods.windows(2).any(|w| w[0].starts >= w[1].starts) {
            return Err("periods must start in order through the day".into());
        }
        // Every whole game minute lands on a whole real millisecond.
        if (60 * self.day_ms()) % i64::from(GAME_DAY_SECS) != 0 {
            return Err("day_real_secs must map game minutes to whole milliseconds".into());
        }
        Ok(())
    }
}

/// One class's place in the timetable.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClassSchedule {
    /// Class id: `data/classes/<id>.json` (`sim_core::classes::ClassDef`).
    pub id: String,
    /// Display name ("Charms").
    pub name: String,
    /// Game times its sessions start, every game day, in order.
    pub sessions: Vec<TimeOfDay>,
    /// Real seconds a session lasts.
    pub duration_secs: u32,
    /// Real seconds from the start during which the door admits students.
    pub join_secs: u32,
    /// Students per section; when every open section is full, another opens.
    pub instance_cap: u16,
    /// Most sections one session opens.
    pub max_sections: u8,
    /// The castle interactable (`class_door`) that admits students.
    pub door: String,
    /// The classroom's instance layout (`data/world/instances/<layout>.json`).
    pub layout: String,
    /// Where it is, for the HUD: a castle layout landmark id.
    pub landmark: String,
    /// Sessions start only in the night period (Astronomy).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub night_only: bool,
}

/// Names one session: game day and slot (index into `sessions`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct SessionKey {
    pub day: i64,
    pub slot: u8,
}

impl SessionKey {
    /// Slots a day may have (the id packs the slot in 6 bits).
    pub const SLOTS_MAX: usize = 64;

    /// The stable number of this session (`class_sessions.session_key`,
    /// `ClassInfo.session`).
    pub fn id(self) -> i64 {
        self.day * Self::SLOTS_MAX as i64 + i64::from(self.slot)
    }

    pub fn from_id(id: i64) -> SessionKey {
        SessionKey {
            day: id.div_euclid(Self::SLOTS_MAX as i64),
            slot: id.rem_euclid(Self::SLOTS_MAX as i64) as u8,
        }
    }
}

/// When one session starts, stops admitting and ends (real Unix ms).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SessionTimes {
    pub key: SessionKey,
    pub start_ms: i64,
    pub join_until_ms: i64,
    pub end_ms: i64,
}

impl SessionTimes {
    /// The door admits students at `now`.
    pub fn joinable(&self, now: i64) -> bool {
        (self.start_ms..self.join_until_ms).contains(&now)
    }

    /// The session is under way at `now`.
    pub fn running(&self, now: i64) -> bool {
        (self.start_ms..self.end_ms).contains(&now)
    }

    /// Real milliseconds since the session started (negative before).
    pub fn elapsed_ms(&self, now: i64) -> i64 {
        now - self.start_ms
    }
}

/// `data/classes/schedule.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Schedule {
    pub version: u32,
    pub clock: ClockRules,
    /// Display names of the nine subjects (skills, the results card).
    pub subjects: BTreeMap<Subject, String>,
    /// The game times classes start, in order through the day (v2).
    pub slots: Vec<TimeOfDay>,
    pub classes: Vec<ClassSchedule>,
}

impl Schedule {
    /// Parses and validates the schedule.
    pub fn from_json(json: &str) -> Result<Schedule, ScheduleError> {
        let s: Schedule = serde_json::from_str(json)?;
        s.validate().map_err(ScheduleError::Invalid)?;
        Ok(s)
    }

    pub fn class(&self, id: &str) -> Option<&ClassSchedule> {
        self.classes.iter().find(|c| c.id == id)
    }

    /// The class whose door is interactable `door`.
    pub fn class_by_door(&self, door: &str) -> Option<&ClassSchedule> {
        self.classes.iter().find(|c| c.door == door)
    }

    /// The slot (index into `slots`) a game time starts, if it is one.
    pub fn slot_of(&self, at: TimeOfDay) -> Option<u8> {
        self.slots
            .iter()
            .position(|&t| t == at)
            .and_then(|k| u8::try_from(k).ok())
    }

    /// The classes with a session in slot `slot`, in file order.
    pub fn in_slot(&self, slot: u8) -> impl Iterator<Item = &ClassSchedule> {
        let at = self.slots.get(usize::from(slot)).copied();
        self.classes
            .iter()
            .filter(move |c| at.is_some_and(|t| c.sessions.contains(&t)))
    }

    /// A subject's display name.
    pub fn subject_name(&self, subject: Subject) -> &str {
        self.subjects
            .get(&subject)
            .map_or(subject.id(), String::as_str)
    }

    /// The times of session `key` of `class`.
    pub fn session(&self, class: &ClassSchedule, key: SessionKey) -> SessionTimes {
        let at = class.sessions[usize::from(key.slot) % class.sessions.len()];
        let start_ms = self.clock.unix_ms(key.day, at.secs());
        SessionTimes {
            key,
            start_ms,
            join_until_ms: start_ms + i64::from(class.join_secs) * 1_000,
            end_ms: start_ms + i64::from(class.duration_secs) * 1_000,
        }
    }

    /// The session of `class` under way at `now`, if any.
    pub fn current(&self, class: &ClassSchedule, now: i64) -> Option<SessionTimes> {
        let day = self.clock.game_time(now).day;
        // A session started late yesterday may still be running.
        [day - 1, day]
            .into_iter()
            .flat_map(|d| {
                (0..class.sessions.len()).map(move |slot| SessionKey {
                    day: d,
                    slot: slot as u8,
                })
            })
            .map(|k| self.session(class, k))
            .find(|s| s.running(now))
    }

    /// The session a student can join at `now`, if its door is open.
    pub fn joinable(&self, class: &ClassSchedule, now: i64) -> Option<SessionTimes> {
        self.current(class, now).filter(|s| s.joinable(now))
    }

    /// The next session to start after `now` (not the one under way).
    pub fn next(&self, class: &ClassSchedule, now: i64) -> SessionTimes {
        let day = self.clock.game_time(now).day;
        [day, day + 1]
            .into_iter()
            .flat_map(|d| {
                (0..class.sessions.len()).map(move |slot| SessionKey {
                    day: d,
                    slot: slot as u8,
                })
            })
            .map(|k| self.session(class, k))
            .find(|s| s.start_ms > now)
            .expect("a validated class has a session every game day")
    }

    fn validate(&self) -> Result<(), String> {
        if self.version != SCHEDULE_VERSION {
            return Err(format!(
                "unsupported version {} (expected {SCHEDULE_VERSION})",
                self.version
            ));
        }
        self.clock.validate()?;
        if Subject::ALL.iter().any(|s| {
            self.subjects
                .get(s)
                .is_none_or(|n| n.trim().is_empty() || n.len() > 64)
        }) {
            return Err("subjects must name all nine subjects".into());
        }
        if self.slots.is_empty()
            || self.slots.len() > SessionKey::SLOTS_MAX
            || self.slots.windows(2).any(|w| w[0] >= w[1])
            || self.slots.iter().any(|t| t.secs() % 60 != 0)
        {
            return Err("slots: 1..=64 whole game minutes, in order".into());
        }
        let day_secs = self.clock.day_real_secs;
        let mut ids = HashSet::new();
        let mut doors = HashSet::new();
        for c in &self.classes {
            let id = &c.id;
            if !valid_id(id) || !ids.insert(id.as_str()) {
                return Err(format!("class id {id:?} is not a unique [a-z0-9_] id"));
            }
            if c.sessions.is_empty() || c.sessions.len() > SessionKey::SLOTS_MAX {
                return Err(format!("{id}: 1..=64 sessions a day"));
            }
            if c.sessions.windows(2).any(|w| w[0] >= w[1]) {
                return Err(format!("{id}: sessions must be in order"));
            }
            if c.sessions.iter().any(|t| t.secs() % 60 != 0) {
                return Err(format!("{id}: sessions start on whole game minutes"));
            }
            if c.duration_secs < 60 || c.join_secs == 0 || c.join_secs > c.duration_secs {
                return Err(format!(
                    "{id}: duration at least a minute, join window within it"
                ));
            }
            // Sessions never overlap, also across midnight.
            let real = |game: u32| u64::from(game) * u64::from(day_secs) / 86_400;
            let mut starts: Vec<u64> = c.sessions.iter().map(|t| real(t.secs())).collect();
            starts.push(starts[0] + u64::from(day_secs));
            if starts
                .windows(2)
                .any(|w| w[1] - w[0] < u64::from(c.duration_secs))
            {
                return Err(format!("{id}: sessions overlap"));
            }
            if c.instance_cap == 0 || c.max_sections == 0 {
                return Err(format!(
                    "{id}: instance_cap and max_sections must be positive"
                ));
            }
            for (what, v) in [
                ("door", &c.door),
                ("layout", &c.layout),
                ("landmark", &c.landmark),
            ] {
                if !valid_id(v) {
                    return Err(format!("{id}: {what} {v:?} is not an id"));
                }
            }
            if let Some(t) = c.sessions.iter().find(|t| !self.slots.contains(t)) {
                return Err(format!("{id}: session {t} is not a slot"));
            }
            if !doors.insert(c.door.as_str()) {
                return Err(format!("{id}: door {:?} is another class's", c.door));
            }
            if c.night_only
                && let Some(t) = c
                    .sessions
                    .iter()
                    .find(|t| self.clock.period_at_secs(t.secs()) != Period::Night)
            {
                return Err(format!("{id}: night-only, but a session starts at {t}"));
            }
        }
        // The classes of a slot are taught in different rooms.
        for at in &self.slots {
            let mut rooms = HashSet::new();
            if let Some(c) = self
                .classes
                .iter()
                .filter(|c| c.sessions.contains(at))
                .find(|c| !rooms.insert(c.layout.as_str()))
            {
                return Err(format!(
                    "slot {at}: {} shares room {:?} with another class",
                    c.id, c.layout
                ));
            }
        }
        // A session of one slot ends before the next slot starts, so a
        // student can go from one class to the next (and slots are where
        // the timetable groups them).
        let real = |game: u32| u64::from(game) * u64::from(day_secs) / 86_400;
        let longest = self
            .classes
            .iter()
            .map(|c| u64::from(c.duration_secs))
            .max()
            .unwrap_or(0);
        let mut starts: Vec<u64> = self.slots.iter().map(|t| real(t.secs())).collect();
        starts.push(starts[0] + u64::from(day_secs));
        if starts.windows(2).any(|w| w[1] - w[0] < longest) {
            return Err("slots are closer together than a session lasts".into());
        }
        Ok(())
    }
}

/// A data id: lower-case letters, digits and underscores.
pub(crate) fn valid_id(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHIPPED: &str = include_str!("../../../data/classes/schedule.json");

    fn shipped() -> Schedule {
        Schedule::from_json(SHIPPED).expect("the shipped schedule is valid")
    }

    #[test]
    fn game_time_runs_twelve_days_a_real_day() {
        let s = shipped();
        let c = &s.clock;
        assert_eq!(c.day_real_secs, 7_200);
        let epoch = c.epoch_unix_ms;
        assert_eq!(c.game_time(epoch), GameTime { day: 0, secs: 0 });
        // One real hour is half a game day.
        assert_eq!(
            c.game_time(epoch + 3_600_000),
            GameTime {
                day: 0,
                secs: 43_200
            }
        );
        assert_eq!(c.game_time(epoch + 86_400_000).day, 12);
        // Before the epoch counts backwards.
        assert_eq!(
            c.game_time(epoch - 1),
            GameTime {
                day: -1,
                secs: 86_399
            }
        );
        // Game minutes map to whole real milliseconds and back.
        for secs in (0..GAME_DAY_SECS).step_by(60) {
            let at = c.unix_ms(5, secs);
            assert_eq!(c.game_time(at), GameTime { day: 5, secs });
            assert!(c.game_time(at - 1) < GameTime { day: 5, secs });
        }
    }

    #[test]
    fn periods_and_daylight_follow_the_day() {
        let s = shipped();
        let c = &s.clock;
        let at = |h, m| c.unix_ms(3, TimeOfDay::from_hm(h, m).unwrap().secs());
        assert_eq!(c.period(at(2, 0)), Period::Night);
        assert_eq!(c.period(at(7, 0)), Period::Morning);
        assert_eq!(c.period(at(15, 0)), Period::Afternoon);
        assert_eq!(c.period(at(23, 0)), Period::Night);
        assert_eq!(c.daylight(at(1, 0)), 0.0);
        assert_eq!(c.daylight(at(12, 0)), 1.0);
        let dawn = c.daylight(at(6, 30));
        assert!(dawn > 0.0 && dawn < 1.0, "{dawn}");
        let (next, when) = c.next_period(at(12, 0));
        assert_eq!(next, Period::Afternoon);
        assert!(when > at(12, 0));
        let (next, _) = c.next_period(at(23, 0));
        assert_eq!(next, Period::Morning);
    }

    #[test]
    fn charms_comes_round_and_opens_its_door_for_a_while() {
        let s = shipped();
        let charms = s.class("charms").expect("charms is scheduled");
        assert_eq!(s.class_by_door(&charms.door).unwrap().id, "charms");
        let epoch = s.clock.epoch_unix_ms;
        // Over one real day: sessions are at most an hour and a half apart.
        let mut now = epoch + 1;
        let mut gaps = Vec::new();
        let mut last = s.next(charms, now);
        for _ in 0..30 {
            now = last.start_ms;
            let next = s.next(charms, now);
            gaps.push(next.start_ms - last.start_ms);
            last = next;
        }
        assert!(gaps.iter().all(|&g| g <= 90 * 60_000), "{gaps:?}");
        let avg = gaps.iter().sum::<i64>() / gaps.len() as i64;
        assert!((40 * 60_000..=60 * 60_000).contains(&avg), "{avg}");

        // Joinable for the join window, running to its end.
        let session = s.next(charms, epoch);
        let t = session.start_ms;
        assert!(s.joinable(charms, t - 1).is_none());
        assert_eq!(s.joinable(charms, t), Some(session));
        assert_eq!(s.joinable(charms, session.join_until_ms - 1), Some(session));
        assert!(s.joinable(charms, session.join_until_ms).is_none());
        assert_eq!(s.current(charms, session.end_ms - 1), Some(session));
        assert!(s.current(charms, session.end_ms).is_none());
        // Keys round-trip through their id.
        assert_eq!(SessionKey::from_id(session.key.id()), session.key);
        let neg = SessionKey { day: -3, slot: 1 };
        assert_eq!(SessionKey::from_id(neg.id()), neg);
    }

    #[test]
    fn the_timetable_runs_classes_side_by_side_in_slots() {
        let s = shipped();
        assert_eq!(s.slots.len(), 6);
        // Charms, Potions and Transfiguration, each twice a game day.
        for id in ["charms", "potions", "transfiguration"] {
            let c = s.class(id).unwrap_or_else(|| panic!("{id} is scheduled"));
            assert_eq!(c.sessions.len(), 2, "{id}");
            assert!(!c.night_only);
        }
        let at = |h, m| TimeOfDay::from_hm(h, m).unwrap();
        let first = s.slot_of(at(8, 0)).unwrap();
        let ids: Vec<&str> = s.in_slot(first).map(|c| c.id.as_str()).collect();
        assert_eq!(ids, ["charms", "potions", "herbology"]);
        assert_eq!(s.slot_of(at(9, 0)), None);
        // Defence, History, Divination and Astronomy, twice a game day;
        // Astronomy only at night, three classes at 22:30.
        for id in ["defence", "history", "divination", "astronomy"] {
            let c = s.class(id).unwrap_or_else(|| panic!("{id} is scheduled"));
            assert_eq!(c.sessions.len(), 2, "{id}");
            assert_eq!(c.night_only, id == "astronomy", "{id}");
        }
        let late = s.slot_of(at(22, 30)).unwrap();
        let ids: Vec<&str> = s.in_slot(late).map(|c| c.id.as_str()).collect();
        assert_eq!(ids, ["defence", "divination", "astronomy"]);
        // Different doors in every slot (and everywhere).
        for k in 0..s.slots.len() as u8 {
            let doors: HashSet<&str> = s.in_slot(k).map(|c| c.door.as_str()).collect();
            assert_eq!(doors.len(), s.in_slot(k).count());
        }
        // Herbology and Creature Keeping through their gates.
        for id in ["herbology", "creatures"] {
            let c = s.class(id).unwrap_or_else(|| panic!("{id} is scheduled"));
            assert_eq!(c.sessions.len(), 2, "{id}");
            assert!(!c.night_only);
        }
        assert_eq!(s.subject_name(Subject::Creatures), "Creature Keeping");
        // A night-only class may start at the night slots.
        let mut v: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        v["classes"][0]["night_only"] = true.into();
        v["classes"][0]["sessions"] = serde_json::json!(["01:00", "22:30"]);
        Schedule::from_json(&v.to_string()).unwrap();
    }

    /// The full timetable. Six slots of three classes,
    /// every class twice a game day; Flying is a fourth class
    /// at 10:30 and 14:30, so a game day runs 20 sessions; no two classes of
    /// a slot share a room or a door, and over a whole game day every door
    /// opens exactly at its slots.
    #[test]
    fn the_full_timetable_never_double_books_a_room() {
        let s = shipped();
        assert_eq!(s.classes.len(), 10);
        assert!(
            Subject::ALL
                .iter()
                .all(|sub| s.classes.iter().any(|c| c.id == sub.id()))
        );
        let mut sessions = 0;
        for k in 0..s.slots.len() as u8 {
            let classes: Vec<&ClassSchedule> = s.in_slot(k).collect();
            let at = s.slots[usize::from(k)].to_string();
            let flying = classes.iter().any(|c| c.id == "flying");
            assert_eq!(flying, at == "10:30" || at == "14:30", "slot {at}");
            assert_eq!(classes.len(), 3 + usize::from(flying), "slot {at}");
            let rooms: HashSet<&str> = classes.iter().map(|c| c.layout.as_str()).collect();
            assert_eq!(
                rooms.len(),
                classes.len(),
                "slot {at}: a room is double-booked"
            );
            sessions += classes.len();
        }
        assert_eq!(sessions, 20);
        assert!(s.classes.iter().all(|c| c.sessions.len() == 2));
        // A game day from just after midnight: each class's next session
        // starts at its slots, 18 in all, and its door opens then.
        let day = 40;
        let mut opened = Vec::new();
        for c in &s.classes {
            let mut now = s.clock.unix_ms(day, 0);
            loop {
                let next = s.next(c, now);
                if next.start_ms >= s.clock.unix_ms(day + 1, 0) {
                    break;
                }
                let at = s.clock.game_time(next.start_ms);
                assert_eq!(at.day, day);
                assert!(s.slots.iter().any(|t| t.secs() == at.secs), "{}", c.id);
                assert!(
                    s.joinable(c, next.start_ms - 1)
                        .is_none_or(|j| j.start_ms != next.start_ms)
                );
                assert_eq!(s.joinable(c, next.start_ms), Some(next));
                opened.push((next.start_ms, c.id.clone()));
                now = next.start_ms;
            }
        }
        assert_eq!(opened.len(), 20);
        // Sharing a room in a slot is refused.
        let mut v: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        let history = v["classes"]
            .as_array()
            .unwrap()
            .iter()
            .position(|c| c["id"] == "history")
            .unwrap();
        v["classes"][history]["sessions"] = serde_json::json!(["08:00", "14:30"]);
        let err = Schedule::from_json(&v.to_string()).unwrap_err().to_string();
        assert!(err.contains("shares room"), "{err}");
    }

    #[test]
    fn bad_schedules_are_refused() {
        let mut v: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        let err =
            |v: &serde_json::Value| Schedule::from_json(&v.to_string()).unwrap_err().to_string();
        let mut overlap = v.clone();
        overlap["classes"][0]["sessions"] = serde_json::json!(["09:00", "09:30"]);
        assert!(err(&overlap).contains("overlap"));
        let mut bad_time = v.clone();
        bad_time["classes"][0]["sessions"] = serde_json::json!(["25:00"]);
        assert!(err(&bad_time).contains("game time"));
        let mut join = v.clone();
        join["classes"][0]["join_secs"] = serde_json::json!(100_000);
        assert!(err(&join).contains("join window"));
        let mut off_slot = v.clone();
        off_slot["classes"][0]["sessions"] = serde_json::json!(["09:15"]);
        assert!(err(&off_slot).contains("not a slot"));
        let mut door = v.clone();
        door["classes"][1]["door"] = door["classes"][0]["door"].clone();
        assert!(err(&door).contains("another class"));
        let mut night = v.clone();
        night["classes"][0]["night_only"] = true.into();
        assert!(err(&night).contains("night-only"));
        let mut subjects = v.clone();
        subjects["subjects"]
            .as_object_mut()
            .unwrap()
            .remove("herbology");
        assert!(err(&subjects).contains("subjects"));
        let mut slots = v.clone();
        slots["slots"] = serde_json::json!(["08:00", "08:10"]);
        assert!(err(&slots).contains("slot"));
        v["clock"]["periods"][2]["period"] = serde_json::json!("morning");
        assert!(err(&v).contains("periods"));
    }
}
