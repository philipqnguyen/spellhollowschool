//! Bot roles: students who attend class and duelists and
//! spectators at the Dueling Club (`--class-pct`, `--duel-pct`), so a run
//! measures what class sections and arenas add to the world tick and to the
//! bandwidth, next to the bots walking the castle.
//!
//! - **Students** (spread across every class of one timetable
//!   slot, `--class-slot`, by default the slot with the most classes) are
//!   moved to their class's door with the dev teleport
//!   (`POST /api/dev/teleport`), `Interact` with it until the door admits
//!   them (the tool sets the dev clock so the slot's sessions open as the
//!   run starts, see [`School::session_start`]; always a slot start after
//!   the server's clock, so never a session the server already ran), and
//!   answer every beat of every practice round. A trace beat gets the
//!   gesture's template with a random human wobble, size and speed, started
//!   within the beat's window and sent when the stroke ends, as the browser
//!   does. A pick beat gets a click within the window: the right option
//!   most of the time (the tool reads the answer keys of `data/classes/
//!   answers/`, which the server never sends), another one otherwise. At
//!   the end of the class the server returns them to the corridor and they
//!   walk the castle again. In the room a student walks from the
//!   entry to a desk seat and sits there (`crate::desks`; a room with no
//!   seats, the Potions classroom, gets standing spots).
//!   The slot may be named `night` (the night slot with the most
//!   classes), and the report has a line per class with its kind (trace or
//!   pick) and its answers scored (right: above 0) and rejected.
//!   Every slot holds three classes, so the default slot and
//!   `night` are both 01:00 (Astronomy's traces, Herbology's and Creature
//!   Keeping's picks); students stay in the room to the results,
//!   and the report sums, per class, what the results carried (Crowns,
//!   items, recipes, lore, hints, fortunes, spells, the XP cap) and when
//!   they came (for the tick p99 of the results burst, `--long-window`).
//!   The Flying class's students fly its ring course instead of
//!   walking to a place (`crate::rings`): the class mounts them, they hover
//!   until a round's ring beats come and fly through each ring on its beat;
//!   the server scores the passes (the report's `rings` count). `--class-only
//!   flying` sends every student to the Flying class.
//! - **Duelists** come in pairs: both stand on the Dueling Club floor, the
//!   challenger sends `DuelChallengeName` once the club has taught them their
//!   spells, the other accepts, and in the arena each casts a random spell of
//!   its loadout every one to three seconds while side-stepping along the
//!   stage. After each duel they challenge again.
//! - **Spectators** stand in the club, ask for `DuelList` and watch the duel
//!   with the fewest spectators until it ends, then pick another.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;

use protocol::{
    Anim, BeatPrompt, ClientMsg, DuelRole, Emote, ServerMsg, TracePoint, Zone,
    school::{LessonBeat, TraceReject},
};
use rand::Rng;
use rand::rngs::StdRng;
use serde::Serialize;
use sim_core::classes::PickSpec;
use sim_core::{AnswerKey, ClassDef, Gestures, Period, PickSets, Schedule, Venues, World};
use tokio::time::Instant;

use crate::desks::{Desk, Room, Stroll};
use crate::rings::{Course, RingFlight};

/// How often a bot picks the right option of a pick beat.
const PICK_RIGHT: f64 = 0.85;

/// The class files the tool knows (`data/classes/<id>.json`) and the answer
/// keys of those with pick beats.
const CLASS_FILES: &[&str] = &[
    include_str!("../../../data/classes/charms.json"),
    include_str!("../../../data/classes/potions.json"),
    include_str!("../../../data/classes/transfiguration.json"),
    include_str!("../../../data/classes/defence.json"),
    include_str!("../../../data/classes/history.json"),
    include_str!("../../../data/classes/divination.json"),
    include_str!("../../../data/classes/astronomy.json"),
    include_str!("../../../data/classes/herbology.json"),
    include_str!("../../../data/classes/creatures.json"),
    include_str!("../../../data/classes/flying.json"),
];
const ANSWER_FILES: &[&str] = &[
    include_str!("../../../data/classes/answers/potions.json"),
    include_str!("../../../data/classes/answers/transfiguration.json"),
    include_str!("../../../data/classes/answers/history.json"),
    include_str!("../../../data/classes/answers/divination.json"),
    include_str!("../../../data/classes/answers/herbology.json"),
    include_str!("../../../data/classes/answers/creatures.json"),
];

/// What a bot does in the world.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Role {
    /// Walks the castle.
    Walker,
    /// Attends the class, then walks.
    Student,
    /// Duels at the club; the challenger names its partner.
    Duelist { challenge: Option<String> },
    /// Watches duels at the club.
    Spectator,
}

impl Role {
    pub fn name(&self) -> &'static str {
        match self {
            Role::Walker => "walker",
            Role::Student => "student",
            Role::Duelist { .. } => "duelist",
            Role::Spectator => "spectator",
        }
    }
}

/// Every bot's role: `class_pct` percent students, `duel_pct` percent at the
/// Dueling Club (one in five of those spectating, the rest in pairs). The
/// buckets are spread over the bot numbers so every role joins throughout
/// the ramp.
pub fn roles(bots: usize, prefix: &str, class_pct: f64, duel_pct: f64) -> Vec<Role> {
    let bucket = |i: usize| ((i * 37) % 100) as f64;
    let mut roles = vec![Role::Walker; bots];
    let mut club = Vec::new();
    for (i, role) in roles.iter_mut().enumerate() {
        let b = bucket(i);
        if b < class_pct {
            *role = Role::Student;
        } else if b < class_pct + duel_pct {
            club.push(i);
        }
    }
    let mut duelists = Vec::new();
    for (k, &i) in club.iter().enumerate() {
        if k % 5 == 4 {
            roles[i] = Role::Spectator;
        } else {
            duelists.push(i);
        }
    }
    for pair in duelists.chunks(2) {
        if let [a, b] = *pair {
            roles[a] = Role::Duelist {
                challenge: Some(format!("{prefix}{b:04}")),
            };
            roles[b] = Role::Duelist { challenge: None };
        }
    }
    roles
}

/// One class the students attend: its door and, for a class with pick
/// beats, its vocabularies and answer key.
pub struct ClassSpot {
    pub id: String,
    door: String,
    door_pos: [f32; 3],
    picks: Option<(PickSpec, AnswerKey)>,
    /// The class room and its students' places.
    pub room: Room,
    /// A Flying class's ring course (its students fly it).
    pub course: Option<Arc<Course>>,
}

impl ClassSpot {
    /// The position in `options` of the right answer to `prompt`, if the
    /// tool knows it.
    fn right_choice(&self, sets: &PickSets, prompt: u16, options: &[u16]) -> Option<u8> {
        let (spec, key) = self.picks.as_ref()?;
        let prompt = &sets.set(&spec.prompts)?.get(prompt)?.key;
        let answer = sets.set(&spec.options)?.index(key.answer(prompt)?)?;
        options
            .iter()
            .position(|&o| o == answer)
            .and_then(|k| u8::try_from(k).ok())
    }
}

/// What the school roles share: the gestures and pick vocabularies, the
/// classes of the slot the students attend, where the club is.
pub struct School {
    gestures: Gestures,
    schedule: Schedule,
    picks: PickSets,
    /// The timetable slot (index into `schedule.slots`).
    slot: u8,
    pub classes: Vec<ClassSpot>,
    /// Floor spots in the Dueling Club area (not on the stage).
    club: Vec<[f32; 3]>,
}

impl School {
    #[cfg(test)]
    /// The shipped school; students attend the classes of slot `slot`
    /// (`"HH:MM"`; or `"night"`, the first night slot with the
    /// most classes), or of the first slot with the most classes.
    pub fn shipped(world: &World, slot: Option<&str>) -> Result<School, String> {
        Self::shipped_only(world, slot, &[])
    }

    /// The shipped school with the students in the classes `only` names
    /// (`--class-only`; all of the slot's when empty).
    pub fn shipped_only(
        world: &World,
        slot: Option<&str>,
        only: &[String],
    ) -> Result<School, String> {
        let gestures = Gestures::from_json(include_str!("../../../data/classes/gestures.json"))
            .map_err(|e| format!("gestures: {e}"))?;
        let schedule = Schedule::from_json(include_str!("../../../data/classes/schedule.json"))
            .map_err(|e| format!("schedule: {e}"))?;
        let venues = Venues::from_json(include_str!("../../../data/dueling/venues.json"))
            .map_err(|e| format!("venues: {e}"))?;
        let picks = PickSets::from_json(include_str!("../../../data/classes/picks.json"))
            .map_err(|e| format!("picks: {e}"))?;
        let defs = CLASS_FILES
            .iter()
            .map(|j| ClassDef::from_json(j).map_err(|e| format!("class: {e}")))
            .collect::<Result<Vec<_>, _>>()?;
        let keys = ANSWER_FILES
            .iter()
            .map(|j| AnswerKey::from_json(j).map_err(|e| format!("answers: {e}")))
            .collect::<Result<Vec<_>, _>>()?;
        let busiest = |night: bool| {
            let slots = (0..schedule.slots.len()).filter(|&k| {
                !night || schedule.clock.period_at_secs(schedule.slots[k].secs()) == Period::Night
            });
            let most = slots
                .clone()
                .map(|k| schedule.in_slot(k as u8).count())
                .max()
                .unwrap_or(0);
            slots
                .clone()
                .find(|&k| schedule.in_slot(k as u8).count() == most)
        };
        let slot = match slot {
            Some("night") => busiest(true).ok_or("the schedule has no night slot")?,
            Some(at) => schedule
                .slots
                .iter()
                .position(|t| t.to_string() == at)
                .ok_or_else(|| format!("no timetable slot {at}"))?,
            None => busiest(false).ok_or("the schedule has no slot")?,
        } as u8;
        let manifest = crate::desks::load_manifest();
        let mut classes = Vec::new();
        for c in schedule
            .in_slot(slot)
            .filter(|c| only.is_empty() || only.contains(&c.id))
        {
            let door = world
                .interactable(&c.door)
                .ok_or_else(|| format!("no interactable {}", c.door))?;
            let picks = defs
                .iter()
                .find(|d| d.id == c.id)
                .and_then(|d| d.picks.clone())
                .zip(keys.iter().find(|k| k.class == c.id).cloned());
            classes.push(ClassSpot {
                id: c.id.clone(),
                door: c.door.clone(),
                door_pos: door.pos,
                picks,
                room: Room::new(&c.layout, manifest.as_ref())?,
                course: Course::new(&c.layout)?.map(Arc::new),
            });
        }
        if classes.is_empty() {
            return Err(format!(
                "no class {}starts at {}",
                if only.is_empty() {
                    String::new()
                } else {
                    format!("of {} ", only.join(", "))
                },
                schedule.slots[usize::from(slot)]
            ));
        }
        let venue = venues.venues.first().ok_or("no dueling venue")?;
        let area = world
            .areas()
            .iter()
            .find(|a| a.id == venue.area)
            .ok_or_else(|| format!("no area {}", venue.area))?;
        let mut club = Vec::new();
        let mut x = area.min[0] + 0.75;
        while x < area.max[0] - 0.5 {
            let mut z = area.min[2] + 0.75;
            while z < area.max[2] - 0.5 {
                if let Some(g) = world.ground_at(x, z, area.min[1].max(0.0))
                    && g.y < area.min[1] + 0.6
                    && area.contains([x, g.y, z])
                {
                    club.push([x, g.y, z]);
                }
                z += 1.0;
            }
            x += 1.0;
        }
        if club.is_empty() {
            return Err("no floor in the Dueling Club area".into());
        }
        Ok(School {
            gestures,
            picks,
            slot,
            classes,
            club,
            schedule,
        })
    }

    /// The slot's game time, `"HH:MM"`.
    pub fn slot_name(&self) -> String {
        self.schedule.slots[usize::from(self.slot)].to_string()
    }

    /// When the slot next starts after `unix_ms`, strictly (the tool sets the
    /// server's dev clock to it, so the doors open as the bots arrive and
    /// every session is one the server has not run).
    pub fn session_start(&self, unix_ms: i64) -> i64 {
        let clock = &self.schedule.clock;
        let secs = self.schedule.slots[usize::from(self.slot)].secs();
        let day = clock.game_time(unix_ms).day;
        [day, day + 1]
            .into_iter()
            .map(|d| clock.unix_ms(d, secs))
            .find(|&t| t > unix_ms)
            .unwrap_or_else(|| clock.unix_ms(day + 2, secs))
    }

    /// The class student `index` attends.
    pub fn class_of(&self, index: usize) -> usize {
        index % self.classes.len()
    }
}

/// What the students of one class did.
#[derive(Debug, Default, Clone, Serialize)]
pub struct ClassStats {
    /// `"trace"` or `"pick"`, the kind of its answers (empty
    /// before any was scored).
    pub kind: &'static str,
    pub joined: u64,
    pub answers_scored: u64,
    /// Scored above 0 (a right pick; a trace that counted).
    pub answers_right: u64,
    pub answers_rejected: u64,
    pub results: u64,
    /// What the results carried, summed over the students: those
    /// the daily XP cap cut, Crowns, item units by catalog id, recipes
    /// learned, lore entries, hints, fortunes and spells learned.
    pub xp_capped: u64,
    pub crowns: u64,
    pub items: BTreeMap<u16, u64>,
    pub recipes: u64,
    pub lore: u64,
    pub hints: u64,
    pub fortunes: u64,
    pub spells: u64,
    /// When the first and the last result arrived (seconds since
    /// the run started).
    pub first_result_secs: Option<f64>,
    pub last_result_secs: Option<f64>,
}

impl ClassStats {
    fn add(&mut self, o: &ClassStats) {
        if self.kind.is_empty() {
            self.kind = o.kind;
        }
        self.joined += o.joined;
        self.answers_scored += o.answers_scored;
        self.answers_right += o.answers_right;
        self.answers_rejected += o.answers_rejected;
        self.results += o.results;
        self.xp_capped += o.xp_capped;
        self.crowns += o.crowns;
        for (item, n) in &o.items {
            *self.items.entry(*item).or_default() += n;
        }
        self.recipes += o.recipes;
        self.lore += o.lore;
        self.hints += o.hints;
        self.fortunes += o.fortunes;
        self.spells += o.spells;
        let pick = |a: Option<f64>, b: Option<f64>, f: fn(f64, f64) -> f64| match (a, b) {
            (Some(a), Some(b)) => Some(f(a, b)),
            (a, b) => a.or(b),
        };
        self.first_result_secs = pick(self.first_result_secs, o.first_result_secs, f64::min);
        self.last_result_secs = pick(self.last_result_secs, o.last_result_secs, f64::max);
    }

    /// Counts one `ClassResult` that arrived `at_secs` into the run.
    fn result(&mut self, rewards: &protocol::ClassRewards, at_secs: f64) {
        self.results += 1;
        self.xp_capped += u64::from(rewards.xp_capped);
        self.crowns += u64::from(rewards.crowns);
        for it in &rewards.items {
            *self.items.entry(it.item_def).or_default() += u64::from(it.quantity);
        }
        self.recipes += rewards.recipes.len() as u64;
        self.lore += u64::from(rewards.lore.is_some());
        self.hints += u64::from(rewards.hint.is_some());
        self.fortunes += u64::from(rewards.fortune.is_some());
        self.spells += rewards.spells.len() as u64;
        self.first_result_secs = Some(self.first_result_secs.map_or(at_secs, |t| t.min(at_secs)));
        self.last_result_secs = Some(self.last_result_secs.map_or(at_secs, |t| t.max(at_secs)));
    }

    /// The report's line for what class `id`'s results carried
    /// (`None` before any result).
    pub fn results_line(&self, id: &str) -> Option<String> {
        let (first, last) = (self.first_result_secs?, self.last_result_secs?);
        let units: u64 = self.items.values().sum();
        Some(format!(
            "{id} results: {} at {first:.0}-{last:.0} s, {} Crowns, {units} item units of {} kinds, {} recipes, {} lore, {} hints, {} fortunes, {} spells, {} XP capped",
            self.results,
            self.crowns,
            self.items.len(),
            self.recipes,
            self.lore,
            self.hints,
            self.fortunes,
            self.spells,
            self.xp_capped
        ))
    }

    /// The report's line for class `id`.
    pub fn line(&self, id: &str) -> String {
        let kind = if self.kind.is_empty() {
            String::new()
        } else {
            format!(" ({}s)", self.kind)
        };
        format!(
            "{id}{kind}: {} joined, answers {} scored ({} right) / {} rejected, {} results",
            self.joined,
            self.answers_scored,
            self.answers_right,
            self.answers_rejected,
            self.results
        )
    }
}

/// What the school roles did, per bot (merged into the summary).
#[derive(Debug, Default, Clone, Serialize)]
pub struct SchoolStats {
    pub classes_joined: u64,
    pub class_refusals: u64,
    pub traces_sent: u64,
    pub traces_scored: u64,
    pub traces_rejected: u64,
    /// Pick answers sent, scored (right or wrong), scored right
    /// (more than 0), and refused.
    pub picks_sent: u64,
    pub picks_scored: u64,
    pub picks_right: u64,
    pub picks_rejected: u64,
    /// Refusals by reason, traces and picks together.
    pub rejects: BTreeMap<String, u64>,
    /// By class id.
    pub by_class: BTreeMap<String, ClassStats>,
    pub class_results: u64,
    pub challenges_sent: u64,
    pub duels_started: u64,
    pub duel_results: u64,
    pub duel_casts: u64,
    pub duel_events: u64,
    pub spectated: u64,
    pub duel_errors: u64,
    /// Students who walked to their place, who sat there (rooms
    /// with seats), and who stopped short (they stand where they stopped).
    pub desk_walks: u64,
    pub seated: u64,
    pub desk_unroutable: u64,
    /// The Flying class: students the class mounted, ring beats
    /// flown at, and those the server scored.
    pub class_mounts: u64,
    pub rings_flown: u64,
    pub rings_scored: u64,
}

impl SchoolStats {
    pub fn add(&mut self, o: &SchoolStats) {
        self.classes_joined += o.classes_joined;
        self.class_refusals += o.class_refusals;
        self.traces_sent += o.traces_sent;
        self.traces_scored += o.traces_scored;
        self.traces_rejected += o.traces_rejected;
        self.picks_sent += o.picks_sent;
        self.picks_scored += o.picks_scored;
        self.picks_right += o.picks_right;
        self.picks_rejected += o.picks_rejected;
        for (k, v) in &o.rejects {
            *self.rejects.entry(k.clone()).or_default() += v;
        }
        for (k, v) in &o.by_class {
            self.by_class.entry(k.clone()).or_default().add(v);
        }
        self.class_results += o.class_results;
        self.challenges_sent += o.challenges_sent;
        self.duels_started += o.duels_started;
        self.duel_results += o.duel_results;
        self.duel_casts += o.duel_casts;
        self.duel_events += o.duel_events;
        self.spectated += o.spectated;
        self.duel_errors += o.duel_errors;
        self.desk_walks += o.desk_walks;
        self.seated += o.seated;
        self.desk_unroutable += o.desk_unroutable;
        self.class_mounts += o.class_mounts;
        self.rings_flown += o.rings_flown;
        self.rings_scored += o.rings_scored;
    }
}

/// How often a parked bot retries the door, a challenge or the duel list.
const RETRY: Duration = Duration::from_secs(5);
/// Side-step amplitude (m) and angular speed (rad/s) on the stage: about
/// 0.6 m/s, well inside the arena's walking speed.
const SIDESTEP_M: f32 = 0.7;
const SIDESTEP_RAD_S: f32 = 0.9;

/// One bot's school role on one connection.
pub struct Act {
    role: Role,
    /// Still to do: a student who has attended walks like everyone else.
    active: bool,
    /// The server clock at `Welcome` and when it arrived.
    clock: (i64, Instant),
    next_try: Instant,
    /// Answers to send (traces and picks): when, and the message.
    traces: Vec<(Instant, ClientMsg)>,
    /// Answers sent and not yet scored: whether each was a pick, by (round, beat).
    sent: HashMap<(u8, u8), bool>,
    /// A student's class (index into `School::classes`).
    class: usize,
    /// In an arena as a duelist: the loadout, the mark and when the fight starts.
    arena: Option<Arena>,
    spot: [f32; 3],
    /// Has been in a class section (a student is done once back in the castle).
    joined: bool,
    /// The student's place in its class room (its number among
    /// the class's students), and the walk there once in the room.
    seat: usize,
    desk: Option<DeskWalk>,
    /// When the run started (results are timed from it).
    epoch: Instant,
    /// Where the class put the student, its ring flight once the
    /// class mounted it, the ring beats it has to fly (local time, ring)
    /// and those flown, by (round, beat).
    class_spawn: [f32; 3],
    ring: Option<RingFlight>,
    ring_beats: Vec<(Instant, u8)>,
    ring_sent: HashSet<(u8, u8)>,
}

/// A student in the class room, walking to its desk and then sitting.
struct DeskWalk {
    walk: Stroll,
    desk: Desk,
    arrived: bool,
}

struct Arena {
    loadout: Vec<protocol::SpellId>,
    mark: [f32; 3],
    yaw: f32,
    since: Instant,
    next_cast: Instant,
}

impl Act {
    pub fn new(role: Role, school: &School, index: usize) -> Act {
        let spot = school.club[(index * 7) % school.club.len()];
        Act {
            active: role != Role::Walker,
            role,
            clock: (0, Instant::now()),
            next_try: Instant::now(),
            traces: Vec::new(),
            sent: HashMap::new(),
            class: school.class_of(index),
            arena: None,
            spot,
            joined: false,
            seat: index / school.classes.len().max(1),
            desk: None,
            epoch: Instant::now(),
            class_spawn: [0.0; 3],
            ring: None,
            ring_beats: Vec::new(),
            ring_sent: HashSet::new(),
        }
    }

    /// Times results from `epoch`, the run's start.
    pub fn with_epoch(mut self, epoch: Instant) -> Act {
        self.epoch = epoch;
        self
    }

    /// The bot stands still in the castle (waiting at the door or in the club).
    pub fn parked(&self) -> bool {
        self.active
    }

    /// Whether the bot stays in `zone` instead of leaving it at once.
    pub fn stays_in(&self, zone: &Zone) -> bool {
        match zone {
            Zone::Class { .. } => self.role == Role::Student,
            Zone::Arena { .. } => matches!(self.role, Role::Duelist { .. } | Role::Spectator),
            _ => false,
        }
    }

    /// The class a student attends.
    fn spot<'s>(&self, school: &'s School) -> &'s ClassSpot {
        &school.classes[self.class % school.classes.len()]
    }

    /// After `Welcome` (server clock `unix_ms`): where to teleport, if anywhere.
    pub fn on_welcome(&mut self, unix_ms: u64, school: &School) -> Option<[f32; 3]> {
        self.clock = (unix_ms as i64, Instant::now());
        self.next_try = Instant::now() + Duration::from_secs(2);
        if !self.active {
            return None;
        }
        Some(match self.role {
            Role::Student => self.spot(school).door_pos,
            _ => self.spot,
        })
    }

    fn local(&self, unix_ms: u64) -> Instant {
        let ahead = unix_ms as i64 - self.clock.0;
        if ahead >= 0 {
            self.clock.1 + Duration::from_millis(ahead as u64)
        } else {
            self.clock
                .1
                .checked_sub(Duration::from_millis((-ahead) as u64))
                .unwrap_or(self.clock.1)
        }
    }

    /// Messages due at `now` (`zone` is where the bot is).
    pub fn tick(
        &mut self,
        now: Instant,
        zone: &Zone,
        school: &School,
        rng: &mut StdRng,
        stats: &mut SchoolStats,
    ) -> Vec<ClientMsg> {
        let mut out = Vec::new();
        // Traces whose stroke has ended, picks whose click is due.
        let mut k = 0;
        while k < self.traces.len() {
            if self.traces[k].0 <= now {
                let msg = self.traces.swap_remove(k).1;
                match &msg {
                    ClientMsg::PickAnswer { round, beat, .. } => {
                        stats.picks_sent += 1;
                        self.sent.insert((*round, *beat), true);
                    }
                    ClientMsg::TraceGesture { round, beat, .. } => {
                        stats.traces_sent += 1;
                        self.sent.insert((*round, *beat), false);
                    }
                    _ => {}
                }
                out.push(msg);
            } else {
                k += 1;
            }
        }
        if !self.active {
            return out;
        }
        match (zone, &self.role) {
            (Zone::Castle, Role::Student) if now >= self.next_try => {
                self.next_try = now + RETRY;
                out.push(ClientMsg::Interact {
                    target: self.spot(school).door.clone(),
                });
            }
            (
                Zone::Castle,
                Role::Duelist {
                    challenge: Some(name),
                },
            ) if now >= self.next_try => {
                self.next_try = now + RETRY;
                stats.challenges_sent += 1;
                out.push(ClientMsg::DuelChallengeName { name: name.clone() });
            }
            (Zone::Castle, Role::Spectator) if now >= self.next_try => {
                self.next_try = now + RETRY;
                out.push(ClientMsg::DuelListRequest);
            }
            (Zone::Arena { .. }, Role::Duelist { .. }) => {
                if let Some(a) = &mut self.arena
                    && now >= a.next_cast
                    && !a.loadout.is_empty()
                {
                    a.next_cast = now + Duration::from_millis(rng.gen_range(1000..3000));
                    let spell = a.loadout[rng.gen_range(0..a.loadout.len())];
                    stats.duel_casts += 1;
                    out.push(ClientMsg::DuelCast { spell });
                }
            }
            _ => {}
        }
        out
    }

    /// A duelist's side-step on the stage: the position, yaw and anim to send.
    pub fn arena_move(&self, now: Instant) -> Option<([f32; 3], f32, Anim)> {
        let a = self.arena.as_ref()?;
        let t = now.duration_since(a.since).as_secs_f32();
        let pos = [
            a.mark[0],
            a.mark[1],
            a.mark[2] + SIDESTEP_M * (t * SIDESTEP_RAD_S).sin(),
        ];
        Some((pos, a.yaw, Anim::Walk))
    }

    /// A student's walk to its desk in the class room: the
    /// position, yaw and anim to send, and the `sit` emote to send after
    /// the move that stops there (once, in a room with seats).
    pub fn class_move(
        &mut self,
        school: &School,
        dt: f32,
        stats: &mut SchoolStats,
    ) -> Option<([f32; 3], f32, Anim, Option<ClientMsg>)> {
        let spot = &school.classes[self.class % school.classes.len()];
        // The ring course.
        if let (Some(course), Some(f)) = (spot.course.as_deref(), self.ring.as_mut()) {
            let (pos, yaw, anim) = f.step(course, Instant::now(), dt);
            return Some((pos, yaw, anim, None));
        }
        let room = &spot.room;
        let d = self.desk.as_mut()?;
        if d.walk.walking() {
            d.walk.step(room, dt);
            if d.walk.walking() {
                return Some((d.walk.pos, d.walk.yaw, Anim::Walk, None));
            }
        }
        let mut sit = None;
        if !d.arrived {
            d.arrived = true;
            if !d.walk.arrived() {
                // Stopped short (someone's correction, say): stand there.
                stats.desk_unroutable += 1;
                return Some((d.walk.pos, d.walk.yaw, Anim::Idle, None));
            }
            stats.desk_walks += 1;
            if room.seated {
                stats.seated += 1;
                sit = Some(ClientMsg::Emote { emote: Emote::Sit });
            }
        }
        let yaw = if d.walk.arrived() {
            d.desk.yaw
        } else {
            d.walk.yaw
        };
        Some((d.walk.pos, yaw, Anim::Idle, sit))
    }

    /// Reacts to a server message.
    pub fn on_msg(
        &mut self,
        msg: &ServerMsg,
        now: Instant,
        school: &School,
        rng: &mut StdRng,
        stats: &mut SchoolStats,
    ) -> Vec<ClientMsg> {
        let mut out = Vec::new();
        match msg {
            ServerMsg::ZoneChanged { zone, spawn, yaw } => match zone {
                Zone::Arena { .. } => {
                    if matches!(self.role, Role::Duelist { .. }) {
                        self.arena = Some(Arena {
                            loadout: Vec::new(),
                            mark: spawn.to_m(),
                            yaw: protocol::yaw_from_u8(*yaw),
                            since: now,
                            next_cast: now + Duration::from_secs(4),
                        });
                    }
                }
                Zone::Class { class, .. } => {
                    stats.classes_joined += 1;
                    stats.by_class.entry(class.clone()).or_default().joined += 1;
                    self.joined = true;
                    self.class_spawn = spawn.to_m();
                    // From the entry to the desk (a
                    // Flying class mounts its students instead).
                    if self.role == Role::Student && self.spot(school).course.is_none() {
                        let desk = self.spot(school).room.desk(self.seat);
                        self.desk = Some(DeskWalk {
                            walk: self
                                .spot(school)
                                .room
                                .stroll(spawn.to_m(), [desk.pos[0], desk.pos[2]]),
                            desk,
                            arrived: false,
                        });
                    }
                }
                Zone::Castle => {
                    self.arena = None;
                    self.desk = None;
                    self.ring = None;
                    self.ring_beats.clear();
                    self.ring_sent.clear();
                    self.traces.clear();
                    self.sent.clear();
                    // Back from a class: done; back from a duel: again soon.
                    if self.role == Role::Student && self.joined {
                        self.active = false;
                    }
                    self.next_try = now + Duration::from_secs(3);
                }
                _ => {}
            },
            ServerMsg::ClassError { .. } => stats.class_refusals += 1,
            // The Flying class mounted us: onto the course, with
            // any ring beats already announced.
            ServerMsg::Mounted {
                on: true,
                tier,
                reason: None,
            } if self.joined && self.role == Role::Student => {
                if let Some(course) = self.spot(school).course.clone()
                    && self.ring.is_none()
                {
                    stats.class_mounts += 1;
                    let mut f = RingFlight::new(&course, self.class_spawn, *tier, rng);
                    for &(at, ring) in &self.ring_beats {
                        f.add(at, ring);
                    }
                    self.ring = Some(f);
                }
            }
            ServerMsg::Correction { pos, .. } => {
                if let Some(f) = &mut self.ring {
                    f.pos = pos.to_m();
                }
            }
            ServerMsg::LessonRound {
                round,
                window_ms,
                beats,
            } => {
                for beat in beats {
                    let answer = match beat.prompt {
                        BeatPrompt::Trace { .. } => {
                            self.trace(school, *round, *window_ms, beat, rng)
                        }
                        BeatPrompt::Pick { .. } => self.pick(school, *round, *window_ms, beat, rng),
                        // Ring beats are flown, not answered.
                        BeatPrompt::Ring { ring } => {
                            let at = self.at_offset(beat.at_unix_ms, 0);
                            self.ring_sent.insert((*round, beat.beat));
                            self.ring_beats.push((at, ring));
                            stats.rings_flown += 1;
                            if let Some(f) = &mut self.ring {
                                f.add(at, ring);
                            }
                            None
                        }
                    };
                    if let Some(m) = answer {
                        self.traces.push(m);
                    }
                }
            }
            ServerMsg::BeatScored {
                round,
                beat,
                result,
            } => {
                let ring = self.ring_sent.remove(&(*round, *beat));
                let pick = self.sent.remove(&(*round, *beat)).unwrap_or(false);
                if ring && result.rejected.is_none() {
                    stats.rings_scored += 1;
                }
                let class = stats
                    .by_class
                    .entry(self.spot(school).id.clone())
                    .or_default();
                class.kind = if ring {
                    "ring"
                } else if pick {
                    "pick"
                } else {
                    "trace"
                };
                match result.rejected {
                    Some(r) => {
                        class.answers_rejected += 1;
                        if pick {
                            stats.picks_rejected += 1;
                        } else if !ring {
                            stats.traces_rejected += 1;
                        }
                        *stats.rejects.entry(format!("{r:?}")).or_default() += 1;
                    }
                    None => {
                        class.answers_scored += 1;
                        class.answers_right += u64::from(result.score > 0);
                        if pick {
                            stats.picks_scored += 1;
                            stats.picks_right += u64::from(result.score > 0);
                        } else if !ring {
                            stats.traces_scored += 1;
                        }
                    }
                }
            }
            ServerMsg::ClassResult { class, rewards, .. } => {
                stats.class_results += 1;
                let at = now.saturating_duration_since(self.epoch).as_secs_f64();
                stats
                    .by_class
                    .entry(class.clone())
                    .or_default()
                    .result(rewards, at);
            }
            ServerMsg::DuelChallenged {
                challenge_id,
                from_name,
                ..
            } => {
                // Accept a partner's challenge (the echo of our own names us
                // as the challenger and needs nothing).
                if matches!(self.role, Role::Duelist { challenge: None }) && !from_name.is_empty() {
                    out.push(ClientMsg::DuelRespond {
                        challenge_id: *challenge_id,
                        accept: true,
                    });
                }
            }
            ServerMsg::DuelError { .. } => stats.duel_errors += 1,
            ServerMsg::DuelStart { you, a, b, .. } => match you {
                DuelRole::Duelist(side) => {
                    stats.duels_started += 1;
                    if let Some(arena) = &mut self.arena {
                        arena.loadout = match side {
                            protocol::DuelSide::A => a.loadout.clone(),
                            protocol::DuelSide::B => b.loadout.clone(),
                        };
                    }
                }
                DuelRole::Spectator => stats.spectated += 1,
            },
            ServerMsg::DuelEvents { .. } => stats.duel_events += 1,
            ServerMsg::DuelResult { .. } => stats.duel_results += 1,
            ServerMsg::DuelList { duels } => {
                if self.role == Role::Spectator
                    && let Some(d) = duels.iter().min_by_key(|d| d.spectators)
                {
                    out.push(ClientMsg::DuelSpectate { duel_id: d.duel_id });
                }
            }
            _ => {}
        }
        out
    }

    /// The Instant `offset_ms` from the beat starting at server time `at_unix_ms`.
    fn at_offset(&self, at_unix_ms: u64, offset_ms: i16) -> Instant {
        let start = self.local(at_unix_ms);
        if offset_ms >= 0 {
            start + Duration::from_millis(offset_ms as u64)
        } else {
            start
                .checked_sub(Duration::from_millis(offset_ms.unsigned_abs() as u64))
                .unwrap_or(start)
        }
    }

    /// A player's click on pick `beat`: within the window, usually right.
    fn pick(
        &self,
        school: &School,
        round: u8,
        window_ms: u16,
        beat: &LessonBeat,
        rng: &mut StdRng,
    ) -> Option<(Instant, ClientMsg)> {
        let BeatPrompt::Pick { prompt, options } = &beat.prompt else {
            return None;
        };
        let n = u8::try_from(options.len()).ok().filter(|&n| n > 0)?;
        let right = self
            .spot(school)
            .right_choice(&school.picks, *prompt, options);
        let choice = match right {
            Some(r) if rng.gen_bool(PICK_RIGHT) || n == 1 => r,
            Some(r) => (r + rng.gen_range(1..n)) % n,
            None => rng.gen_range(0..n),
        };
        let offset = rng.gen_range(-60..i32::from(window_ms / 3).max(1)) as i16;
        Some((
            self.at_offset(beat.at_unix_ms, offset),
            ClientMsg::PickAnswer {
                round,
                beat: beat.beat,
                choice,
                offset_ms: offset,
            },
        ))
    }

    /// A human-like trace of `beat`: when to send it, and the message.
    fn trace(
        &self,
        school: &School,
        round: u8,
        window_ms: u16,
        beat: &LessonBeat,
        rng: &mut StdRng,
    ) -> Option<(Instant, ClientMsg)> {
        let BeatPrompt::Trace { gesture } = beat.prompt else {
            return None;
        };
        let g = school.gestures.get(gesture)?;
        let offset = rng.gen_range(-60..i32::from(window_ms / 3).max(1)) as i16;
        let span = g.max_ms.saturating_sub(g.min_ms);
        // Done within the pad's hold, as a player must (the client cuts a
        // stroke off `TRACE_HOLD_MS` after the beat).
        let hold = sim_core::classes::TRACE_HOLD_MS.saturating_sub(offset.max(0) as u32);
        let ms = (g.min_ms + span * rng.gen_range(35..75) / 100).min(hold.max(g.min_ms));
        let points = human_trace(&school.gestures, g, ms, rng)?;
        let at = self.at_offset(beat.at_unix_ms, offset) + Duration::from_millis(u64::from(ms));
        Some((
            at,
            ClientMsg::TraceGesture {
                round,
                beat: beat.beat,
                start_offset_ms: offset,
                points,
            },
        ))
    }
}

/// The gesture's template traced over `ms` by an unsteady hand: resampled
/// by arc length, scaled and shifted a little, with a smooth wobble and a
/// jitter, until the shared scorer accepts it as human (not too perfect,
/// not too fast).
pub fn human_trace(
    gestures: &Gestures,
    g: &sim_core::Gesture,
    ms: u32,
    rng: &mut StdRng,
) -> Option<Vec<TracePoint>> {
    let template: Vec<[f32; 2]> = g
        .points
        .iter()
        .map(|p| [f32::from(p[0]), f32::from(p[1])])
        .collect();
    let n = ((ms / 25) as usize).clamp(12, 60);
    let dt = (ms / (n as u32 - 1)).clamp(1, 255) as u8;
    for _ in 0..8 {
        let scale = rng.gen_range(0.8..1.05f32);
        let shift = [rng.gen_range(-8.0..8.0f32), rng.gen_range(-8.0..8.0f32)];
        let phase = rng.gen_range(0.0..std::f32::consts::TAU);
        let wobble = rng.gen_range(3.0..7.0f32);
        let points: Vec<TracePoint> = resample(&template, n)
            .iter()
            .enumerate()
            .map(|(k, p)| {
                let w = wobble * (k as f32 * 0.45 + phase).sin();
                let x = p[0] * scale + shift[0] + w + rng.gen_range(-1.5..1.5f32);
                let y = p[1] * scale + shift[1] - w * 0.6 + rng.gen_range(-1.5..1.5f32);
                TracePoint {
                    dt_ms: if k == 0 { 0 } else { dt },
                    x: x.round().clamp(-100.0, 100.0) as i8,
                    y: y.round().clamp(-100.0, 100.0) as i8,
                }
            })
            .collect();
        match gestures.check(g, &points) {
            Ok(_) => return Some(points),
            Err(TraceReject::TooPerfect) => continue,
            Err(_) => continue,
        }
    }
    None
}

/// `n` points evenly spaced along the polyline `pts`.
fn resample(pts: &[[f32; 2]], n: usize) -> Vec<[f32; 2]> {
    let seg = |a: [f32; 2], b: [f32; 2]| ((b[0] - a[0]).powi(2) + (b[1] - a[1]).powi(2)).sqrt();
    let total: f32 = pts.windows(2).map(|w| seg(w[0], w[1])).sum();
    if pts.len() < 2 || total <= 0.0 || n < 2 {
        return pts.to_vec();
    }
    let step = total / (n - 1) as f32;
    let mut out = vec![pts[0]];
    let mut want = step;
    let mut walked = 0.0;
    for w in pts.windows(2) {
        let len = seg(w[0], w[1]);
        while walked + len >= want && out.len() < n {
            let t = (want - walked) / len.max(1e-6);
            out.push([
                w[0][0] + (w[1][0] - w[0][0]) * t,
                w[0][1] + (w[1][1] - w[0][1]) * t,
            ]);
            want += step;
        }
        walked += len;
    }
    while out.len() < n {
        out.push(*pts.last().expect("two points"));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::SeedableRng;

    fn world() -> World {
        let layout =
            sim_core::Layout::from_json(include_str!("../../../data/world/layout.json")).unwrap();
        World::new(layout.for_stairs(0)).unwrap()
    }

    #[test]
    fn roles_pair_duelists_and_spread_students() {
        let roles = roles(1000, "lt", 10.0, 10.0);
        let students = roles.iter().filter(|r| **r == Role::Student).count();
        let spectators = roles.iter().filter(|r| **r == Role::Spectator).count();
        let challengers: Vec<&String> = roles
            .iter()
            .filter_map(|r| match r {
                Role::Duelist { challenge: Some(n) } => Some(n),
                _ => None,
            })
            .collect();
        let acceptors = roles
            .iter()
            .filter(|r| matches!(r, Role::Duelist { challenge: None }))
            .count();
        assert_eq!(students, 100);
        assert_eq!(spectators, 20);
        assert_eq!(challengers.len(), acceptors);
        assert_eq!(challengers.len() * 2 + spectators, 100);
        // Each challenger names a bot that accepts.
        for name in challengers {
            let i: usize = name[2..].parse().unwrap();
            assert_eq!(roles[i], Role::Duelist { challenge: None });
        }
        // Spread over the ramp: some of each in the first tenth.
        assert!(roles[..100].contains(&Role::Student));
        assert!(
            roles[..100]
                .iter()
                .any(|r| matches!(r, Role::Duelist { .. }))
        );
    }

    #[test]
    fn school_spots_are_on_the_club_floor() {
        let w = world();
        let s = School::shipped(&w, None).unwrap();
        assert!(s.club.len() > 20, "{}", s.club.len());
        assert!(s.club.iter().all(|p| p[1] < 0.5));
    }

    #[test]
    fn bot_traces_pass_the_shared_scorer_and_differ() {
        let w = world();
        let s = School::shipped(&w, None).unwrap();
        let mut rng = StdRng::seed_from_u64(7);
        let mut seen = std::collections::HashSet::new();
        for g in &s.gestures.gestures {
            for _ in 0..5 {
                let ms = (g.min_ms + g.max_ms) / 2;
                let t = human_trace(&s.gestures, g, ms, &mut rng).expect("a trace");
                let r = s.gestures.score(g, 600, 30, &t);
                assert_eq!(r.rejected, None, "{} {r:?}", g.key);
                assert!(r.score >= 30, "{} {r:?}", g.key);
                assert!(seen.insert(sim_core::gesture::fingerprint(&t)));
            }
        }
    }

    #[test]
    fn students_spread_across_the_classes_of_the_busiest_slot() {
        let w = world();
        let s = School::shipped(&w, None).unwrap();
        let most = (0..s.schedule.slots.len())
            .map(|k| s.schedule.in_slot(k as u8).count())
            .max()
            .unwrap();
        assert_eq!(s.classes.len(), most);
        assert!(s.classes.len() >= 2, "a trace class and a pick class");
        let n = 10 * s.classes.len();
        let picked: Vec<usize> = (0..n).map(|i| s.class_of(i)).collect();
        for k in 0..s.classes.len() {
            assert_eq!(picked.iter().filter(|&&c| c == k).count(), 10);
        }
        // Every door is in the castle; a named slot is that slot's classes.
        for c in &s.classes {
            assert!(w.interactable(&c.door).is_some(), "{}", c.id);
        }
        let ten = School::shipped(&w, Some("10:30")).unwrap();
        assert_eq!(
            ten.classes
                .iter()
                .map(|c| c.id.as_str())
                .collect::<Vec<_>>(),
            // Flying is a fourth class at 10:30 and 14:30.
            vec!["transfiguration", "history", "divination", "flying"]
        );
        assert!(School::shipped(&w, Some("09:00")).is_err());
    }

    #[test]
    fn the_night_slot_is_the_busiest_night_slot_with_astronomy() {
        let w = world();
        let s = School::shipped(&w, Some("night")).unwrap();
        let clock = &s.schedule.clock;
        let at = s.schedule.slots[usize::from(s.slot)];
        assert_eq!(clock.period_at_secs(at.secs()), Period::Night);
        // Both night slots hold three classes; the first is 01:00.
        assert_eq!(s.slot_name(), "01:00");
        let ids: Vec<&str> = s.classes.iter().map(|c| c.id.as_str()).collect();
        assert_eq!(ids, vec!["astronomy", "herbology", "creatures"]);
        // Its session start is a night one, for the dev clock.
        let start = s.session_start(clock.unix_ms(50, 12 * 3600));
        assert_eq!(
            clock.period_at_secs(clock.game_time(start).secs),
            Period::Night
        );
    }

    #[test]
    fn every_class_is_in_a_slot_the_students_can_attend() {
        let w = world();
        let s = School::shipped(&w, None).unwrap();
        let mut seen = std::collections::BTreeSet::new();
        for at in &s.schedule.slots {
            let slot = School::shipped(&w, Some(&at.to_string())).unwrap();
            for c in &slot.classes {
                // Each class's room is known; pick classes have their key.
                assert!(!c.room.desks.is_empty(), "{}", c.id);
                let def = CLASS_FILES
                    .iter()
                    .map(|j| ClassDef::from_json(j).unwrap())
                    .find(|d| d.id == c.id)
                    .expect("a class file");
                assert_eq!(c.picks.is_some(), def.picks.is_some(), "{}", c.id);
                seen.insert(c.id.clone());
            }
        }
        let all: std::collections::BTreeSet<String> =
            s.schedule.classes.iter().map(|c| c.id.clone()).collect();
        assert_eq!(seen, all);
        for id in ["defence", "history", "divination", "astronomy"] {
            assert!(seen.contains(id), "{id}");
        }
        assert_eq!(CLASS_FILES.len(), all.len(), "a class file per class");
    }

    #[test]
    fn scored_answers_count_per_class_with_their_kind() {
        let w = world();
        let s = School::shipped(&w, Some("22:30")).unwrap();
        let mut rng = StdRng::seed_from_u64(5);
        let mut stats = SchoolStats::default();
        let result = |score: u8, rejected: Option<TraceReject>| protocol::GestureResult {
            score,
            grade: protocol::Grade::Good,
            rejected,
        };
        let scored = |round: u8, beat: u8, r| ServerMsg::BeatScored {
            round,
            beat,
            result: r,
        };
        // Divination (picks): one right, one wrong, one refused.
        let div = s.classes.iter().position(|c| c.id == "divination").unwrap();
        let mut act = Act::new(Role::Student, &s, div);
        for (beat, r) in [
            (0, result(90, None)),
            (1, result(0, None)),
            (2, result(0, Some(TraceReject::BadTiming))),
        ] {
            act.sent.insert((1, beat), true);
            act.on_msg(
                &scored(1, beat, r),
                Instant::now(),
                &s,
                &mut rng,
                &mut stats,
            );
        }
        // Astronomy (traces): two that counted.
        let ast = s.classes.iter().position(|c| c.id == "astronomy").unwrap();
        let mut act = Act::new(Role::Student, &s, ast);
        for beat in 0..2 {
            act.sent.insert((1, beat), false);
            act.on_msg(
                &scored(1, beat, result(70, None)),
                Instant::now(),
                &s,
                &mut rng,
                &mut stats,
            );
        }
        let d = &stats.by_class["divination"];
        assert_eq!(
            (
                d.kind,
                d.answers_scored,
                d.answers_right,
                d.answers_rejected
            ),
            ("pick", 2, 1, 1)
        );
        assert_eq!(
            (stats.picks_scored, stats.picks_right, stats.picks_rejected),
            (2, 1, 1)
        );
        let a = &stats.by_class["astronomy"];
        assert_eq!((a.kind, a.answers_scored, a.answers_right), ("trace", 2, 2));
        assert_eq!(stats.traces_scored, 2);
        // Merged across bots, the kind stays.
        let mut total = SchoolStats::default();
        total.add(&stats);
        total.add(&stats);
        assert_eq!(
            total.by_class["divination"].line("divination"),
            "divination (picks): 0 joined, answers 4 scored (2 right) / 2 rejected, 0 results"
        );
        assert_eq!(
            ClassStats::default().line("history"),
            "history: 0 joined, answers 0 scored (0 right) / 0 rejected, 0 results"
        );
    }

    #[test]
    fn results_count_what_they_carried_and_when() {
        // The grounds classes at 14:30 and 01:00, results timed
        // from the run's start.
        let w = world();
        let s = School::shipped(&w, Some("14:30")).unwrap();
        let ids: Vec<&str> = s.classes.iter().map(|c| c.id.as_str()).collect();
        assert_eq!(ids, vec!["defence", "history", "creatures", "flying"]);
        // The busiest slot (the default) is 10:30, with Flying.
        let busiest = School::shipped(&w, None).unwrap();
        assert_eq!(busiest.slot_name(), "10:30");
        let creatures = s.classes.iter().position(|c| c.id == "creatures").unwrap();
        let mut rng = StdRng::seed_from_u64(9);
        let epoch = Instant::now();
        let result = |rewards: protocol::ClassRewards| ServerMsg::ClassResult {
            class: "creatures".into(),
            session: 1,
            score_pct: 90,
            grade: protocol::Grade::Great,
            rewards,
        };
        let item = |item_def, quantity| protocol::RewardItem { item_def, quantity };
        let mut stats = SchoolStats::default();
        let mut act = Act::new(Role::Student, &s, creatures).with_epoch(epoch);
        act.on_msg(
            &result(protocol::ClassRewards {
                crowns: 19,
                items: vec![item(3008, 2), item(3006, 1), item(3004, 1)],
                ..Default::default()
            }),
            epoch + Duration::from_secs(405),
            &s,
            &mut rng,
            &mut stats,
        );
        let mut other = SchoolStats::default();
        let mut act = Act::new(Role::Student, &s, creatures).with_epoch(epoch);
        act.on_msg(
            &result(protocol::ClassRewards {
                crowns: 12,
                xp_capped: true,
                items: vec![item(3008, 1)],
                recipes: vec![331],
                lore: Some(3),
                hint: Some("The Kitchens".into()),
                fortune: Some(protocol::FortuneKind::Luck),
                spells: vec![13],
                ..Default::default()
            }),
            epoch + Duration::from_millis(407_500),
            &s,
            &mut rng,
            &mut other,
        );
        let mut total = SchoolStats::default();
        total.add(&stats);
        total.add(&other);
        let c = &total.by_class["creatures"];
        assert_eq!(total.class_results, 2);
        assert_eq!((c.results, c.crowns, c.xp_capped), (2, 31, 1));
        assert_eq!(c.items.get(&3008), Some(&3));
        assert_eq!(
            (c.recipes, c.lore, c.hints, c.fortunes, c.spells),
            (1, 1, 1, 1, 1)
        );
        assert_eq!(
            c.results_line("creatures").unwrap(),
            "creatures results: 2 at 405-408 s, 31 Crowns, 5 item units of 3 kinds, 1 recipes, 1 lore, 1 hints, 1 fortunes, 1 spells, 1 XP capped"
        );
        assert_eq!(ClassStats::default().results_line("history"), None);
    }

    #[test]
    fn the_session_start_is_always_a_fresh_slot_start() {
        let w = world();
        let s = School::shipped(&w, Some("08:00")).unwrap();
        let clock = &s.schedule.clock;
        let eight = s.schedule.slots[usize::from(s.slot)].secs();
        let now = clock.unix_ms(100, eight);
        // At the very start, and during it: the next day's.
        assert_eq!(s.session_start(now), clock.unix_ms(101, eight));
        assert_eq!(s.session_start(now + 60_000), clock.unix_ms(101, eight));
        // Before it: today's.
        assert_eq!(s.session_start(now - 1), now);
        let t = s.session_start(now + 5);
        assert!(t > now + 5 && clock.game_time(t).secs == eight);
    }

    #[test]
    fn bots_pick_the_right_option_by_the_answer_key() {
        let w = world();
        let mut rng = StdRng::seed_from_u64(3);
        let mut right = 0;
        let mut total = 0;
        for (slot, file, answers) in [
            ("08:00", 1, 0),
            ("10:30", 2, 1),
            ("10:30", 4, 2),
            ("10:30", 5, 3),
            ("08:00", 7, 4),
            ("14:30", 8, 5),
        ] {
            let s = School::shipped(&w, Some(slot)).unwrap();
            let def = ClassDef::from_json(CLASS_FILES[file]).unwrap();
            let class = s
                .classes
                .iter()
                .position(|c| c.id == def.id)
                .expect("the class in its slot");
            let key = AnswerKey::from_json(ANSWER_FILES[answers]).unwrap();
            assert_eq!(key.class, def.id);
            let spec = def.picks.clone().unwrap();
            for (_, _, window, beats) in def.rounds() {
                for (k, b) in beats.iter().enumerate() {
                    let (prompt, held) = s.picks.resolve(&spec, &key, &b.kind).unwrap();
                    let BeatPrompt::Pick { prompt: p, options } = &prompt else {
                        panic!("a pick beat");
                    };
                    assert_eq!(
                        s.classes[class].right_choice(&s.picks, *p, options),
                        Some(held.answer)
                    );
                    let beat = LessonBeat {
                        beat: k as u8,
                        prompt: prompt.clone(),
                        at_unix_ms: 1_000_000,
                    };
                    let mut act = Act::new(Role::Student, &s, class);
                    act.clock = (0, Instant::now());
                    let Some((
                        _,
                        ClientMsg::PickAnswer {
                            choice, offset_ms, ..
                        },
                    )) = act.pick(&s, 1, window, &beat, &mut rng)
                    else {
                        panic!("a pick");
                    };
                    assert!(offset_ms.unsigned_abs() <= window);
                    assert!(usize::from(choice) < options.len());
                    right += u32::from(choice == held.answer);
                    total += 1;
                }
            }
        }
        assert!(
            right * 100 >= total * 70 && right < total,
            "{right} of {total}"
        );
    }
}
