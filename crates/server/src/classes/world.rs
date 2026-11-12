//! The classes' world-thread handler: class
//! doors, sections, the professor's script, beats and traces. The world owns
//! a [`Classes`] and calls it through `realtime::ext` (see the module docs
//! of [`crate::classes`] for the rules).
//!
//! State is per running session (`Session`, made when its door is first
//! used and dropped when it ends): its sections (instances, opened on
//! demand), every student's seat (scores per beat, accepted traces, the
//! attendance row) and the lesson's progress (steps run, the phase last
//! announced, the practice rounds started). All sections of a session
//! follow its one timeline, reckoned from the session's start on the
//! server clock (`WorldCtx::unix_ms`), so every tick only compares a few
//! numbers: an idle server pays nothing, a full class of 30 a handful of
//! comparisons per tick and one small message per beat window.

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use protocol::{
    BeatPrompt, ClassErrorCode, ClassInfo, ClassRewards, EntityId, GestureResult, LessonBeat,
    LessonPhase, NpcEvent, ServerMsg, TraceReject,
};
use sim_core::classes::{BeatKind, ClassDef, LessonAction, LessonStep};
use sim_core::clock::{ClassSchedule, SessionKey, SessionTimes};
use sim_core::gesture::{self, Gestures};
use sim_core::pick::{self, PickBeat};
use sim_core::ring;

use super::{Admission, AdmitRequest, RewardOutcome, RewardRequest};
use crate::catalog::shipped;
use crate::realtime::entities::ZoneId;
use crate::realtime::ext::{ClassInput, ExtDone, FeatureKind, WorldCtx};
use crate::realtime::limits::Bucket;

/// Answers (traces and picks share it) per student: a burst of 4, then one
/// a second (beats come every 2.5 s or more; one answer per beat counts).
pub const TRACE_BURST: f32 = 4.0;
pub const TRACE_PER_SECOND: f32 = 1.0;
/// Script lines, emotes and demonstrations more than this late (the dev
/// clock jumped, the world stalled) are skipped rather than said at once.
pub const STALE_MS: i64 = 5_000;
/// A reward job retries a failed transaction this many times (the grant is
/// idempotent: a retry after a lost commit grants nothing more).
const REWARD_ATTEMPTS: u32 = 3;

/// Every running class session (the handler's state).
#[derive(Debug, Default)]
pub struct Classes {
    sessions: Vec<Session>,
    /// Students inside a section now, by entity.
    students: HashMap<EntityId, Student>,
}

/// The result of a class database job (`ExtDone::Class`).
#[derive(Debug)]
pub enum ClassDone {
    /// [`super::admit`] finished (`None`: it failed, logged).
    Admitted {
        class: String,
        key: SessionKey,
        character_id: i64,
        who: EntityId,
        result: Option<Admission>,
    },
    /// [`super::reward`] finished (`None`: it failed, logged).
    Rewarded {
        class: String,
        session: i64,
        character_id: i64,
        score_pct: u8,
        result: Option<RewardOutcome>,
    },
}

/// Where a student inside a section sits.
#[derive(Debug, Clone, Copy)]
struct Student {
    class: &'static str,
    key: SessionKey,
    character_id: i64,
}

#[derive(Debug)]
struct Session {
    sched: &'static ClassSchedule,
    def: &'static ClassDef,
    times: SessionTimes,
    /// By section number.
    sections: Vec<Section>,
    /// Every student admitted (or being admitted), by character.
    seats: HashMap<i64, Seat>,
    /// Lesson steps run so far (`def.lesson[..next_step]`).
    next_step: usize,
    /// The phase last announced.
    phase: LessonPhase,
    /// Practice rounds started so far, in order.
    rounds: Vec<Round>,
    /// Fingerprints of every trace this session (any student).
    fingerprints: HashSet<u64>,
    /// The results step ran: no more traces.
    results_given: bool,
}

/// One section: its instance while it is open (it reopens on demand while
/// the door admits students, e.g. after everyone in it dropped out).
#[derive(Debug, Default)]
struct Section {
    zone: Option<ZoneId>,
    professor: Option<EntityId>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SeatState {
    /// The admission job is running (the seat counts against the cap).
    Admitting,
    /// Inside the section.
    Present,
    /// Admitted, then left (or never arrived): may come back while the
    /// door is open.
    Away,
    /// Rewarded for this session before (a restart): `AlreadyAttended`.
    Barred,
}

#[derive(Debug)]
struct Seat {
    section: u8,
    state: SeatState,
    attendance: Option<i64>,
    /// Each beat's score, by its index across the rounds.
    scores: Vec<Option<u8>>,
    /// Answers (traces and picks) that passed every check.
    accepted: u8,
    /// The rate limit of both answer kinds.
    traces: Bucket,
}

impl Seat {
    /// The session score: the average over every beat (missed ones 0).
    fn score_pct(&self) -> u8 {
        let n = self.scores.len().max(1) as u32;
        let sum: u32 = self.scores.iter().map(|s| u32::from(s.unwrap_or(0))).sum();
        (sum / n).min(100) as u8
    }
}

/// A practice round that has started.
#[derive(Debug)]
struct Round {
    round: u8,
    window_ms: u16,
    /// Index of its first beat across the rounds.
    first: usize,
    /// Each beat: when it starts (Unix ms), what it asks (sent as is) and,
    /// for a pick, which option is right (never sent).
    beats: Vec<(i64, BeatPrompt, Option<PickBeat>)>,
}

impl Round {
    fn message(&self) -> ServerMsg {
        ServerMsg::LessonRound {
            round: self.round,
            window_ms: self.window_ms,
            beats: self
                .beats
                .iter()
                .enumerate()
                .map(|(k, (at, prompt, _))| LessonBeat {
                    beat: k as u8,
                    prompt: prompt.clone(),
                    at_unix_ms: unix(*at),
                })
                .collect(),
        }
    }
}

fn unix(ms: i64) -> u64 {
    u64::try_from(ms).unwrap_or(0)
}

fn class_error(ctx: &mut WorldCtx<'_>, who: EntityId, code: ClassErrorCode) {
    ctx.send(who, &ServerMsg::ClassError { code });
}

fn gestures() -> &'static Gestures {
    &shipped().gestures
}

impl Session {
    fn new(sched: &'static ClassSchedule, def: &'static ClassDef, times: SessionTimes) -> Self {
        Session {
            sched,
            def,
            times,
            sections: Vec::new(),
            seats: HashMap::new(),
            next_step: 0,
            phase: LessonPhase::Gathering,
            rounds: Vec::new(),
            fingerprints: HashSet::new(),
            results_given: false,
        }
    }

    fn duration_ms(&self) -> i64 {
        self.times.end_ms - self.times.start_ms
    }

    /// The phase at `now` and when it ends (Unix ms).
    fn phase_at(&self, now: i64) -> (LessonPhase, i64) {
        let clamp = |ms: i64| u32::try_from(ms.max(0)).unwrap_or(u32::MAX);
        let (phase, until) = self
            .def
            .phase_at(clamp(self.times.elapsed_ms(now)), clamp(self.duration_ms()));
        (phase, self.times.start_ms + i64::from(until))
    }

    /// Seats taken in section `n` (being admitted or inside).
    fn occupancy(&self, n: u8) -> usize {
        self.seats
            .values()
            .filter(|s| {
                s.section == n && matches!(s.state, SeatState::Admitting | SeatState::Present)
            })
            .count()
    }

    fn has_room(&self, n: u8) -> bool {
        self.occupancy(n) < usize::from(self.sched.instance_cap)
    }

    /// A section with room, opening a new one when every section is full.
    fn pick_section(&mut self) -> Option<u8> {
        let open = self.sections.len() as u8;
        if let Some(n) = (0..open).find(|&n| self.has_room(n)) {
            return Some(n);
        }
        if self.sections.len() < usize::from(self.sched.max_sections) {
            self.sections.push(Section::default());
            return Some(open);
        }
        None
    }

    /// The zones of the sections open now.
    fn zones(&self) -> Vec<ZoneId> {
        self.sections.iter().filter_map(|s| s.zone).collect()
    }

    fn professors(&self) -> Vec<EntityId> {
        self.sections
            .iter()
            .filter(|s| s.zone.is_some())
            .filter_map(|s| s.professor)
            .collect()
    }

    fn new_seat(&self, section: u8, now: std::time::Instant) -> Seat {
        Seat {
            section,
            state: SeatState::Admitting,
            attendance: None,
            scores: vec![None; self.def.beat_count()],
            accepted: 0,
            traces: Bucket::full(TRACE_BURST, now),
        }
    }
}

impl Classes {
    fn find(&self, class: &str, key: SessionKey) -> Option<usize> {
        self.sessions
            .iter()
            .position(|s| s.sched.id == class && s.times.key == key)
    }

    /// The session `times` of class `sched`, made on first use.
    fn session(
        &mut self,
        sched: &'static ClassSchedule,
        def: &'static ClassDef,
        times: SessionTimes,
    ) -> usize {
        match self.find(&sched.id, times.key) {
            Some(i) => i,
            None => {
                self.sessions.push(Session::new(sched, def, times));
                self.sessions.len() - 1
            }
        }
    }

    /// `Interact` with a class door `door` in reach (castle only).
    pub fn on_door(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, door: &str) {
        let Some(character_id) = ctx.character_id(who) else {
            return;
        };
        let content = shipped();
        let now = ctx.unix_ms();
        let Some(sched) = content.schedule.class_by_door(door) else {
            // A class door the timetable does not name.
            return class_error(ctx, who, ClassErrorCode::Failed);
        };
        let Some(def) = content.class(&sched.id) else {
            return class_error(ctx, who, ClassErrorCode::Failed);
        };
        let Some(times) = content.schedule.joinable(sched, now) else {
            return class_error(ctx, who, ClassErrorCode::NotOpen);
        };
        let idx = self.session(sched, def, times);
        let session = &mut self.sessions[idx];
        match session
            .seats
            .get(&character_id)
            .map(|s| (s.state, s.section))
        {
            // Already on the way in (a double click).
            Some((SeatState::Admitting, _)) => {}
            Some((SeatState::Barred, _)) => class_error(ctx, who, ClassErrorCode::AlreadyAttended),
            // Back to their own section (never another one) if it has room.
            Some((SeatState::Present | SeatState::Away, section)) => {
                if session.has_room(section) {
                    self.seat_student(ctx, idx, character_id, who);
                } else {
                    class_error(ctx, who, ClassErrorCode::Full);
                }
            }
            None => {
                let Some(section) = session.pick_section() else {
                    return class_error(ctx, who, ClassErrorCode::Full);
                };
                let seat = session.new_seat(section, ctx.now());
                session.seats.insert(character_id, seat);
                let req = AdmitRequest {
                    class_id: sched.id.clone(),
                    session: times,
                    section,
                    character_id,
                };
                ctx.spawn(Box::new(move |pool| {
                    Box::pin(async move {
                        let result = match super::admit(&pool, &req).await {
                            Ok(a) => Some(a),
                            Err(e) => {
                                tracing::warn!(error = %e, character_id, "class admission failed");
                                None
                            }
                        };
                        ExtDone::Class(ClassDone::Admitted {
                            class: req.class_id,
                            key: req.session.key,
                            character_id,
                            who,
                            result,
                        })
                    })
                }));
            }
        }
    }

    /// Takes an admitted student into their section (opening it, with its
    /// professor, if it is not open), then `ClassJoined`.
    fn seat_student(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        idx: usize,
        character_id: i64,
        who: EntityId,
    ) {
        let session = &mut self.sessions[idx];
        let Some(n) = session.seats.get(&character_id).map(|s| s.section) else {
            return;
        };
        let section = &mut session.sections[usize::from(n)];
        let (zone, opened) = match section.zone.filter(|&z| ctx.instance(z).is_some()) {
            Some(z) => (z, false),
            None => {
                let kind = FeatureKind::Class {
                    class: session.sched.id.clone(),
                    section: n,
                };
                let Some(z) = ctx.open_instance(kind, &session.sched.layout) else {
                    tracing::error!(layout = %session.sched.layout, "class layout missing");
                    return class_error(ctx, who, ClassErrorCode::Failed);
                };
                section.zone = Some(z);
                section.professor = crate::npc::spawn_professor(ctx, z, &session.sched.id);
                (z, true)
            }
        };
        // A Flying class lines its students up in rows by the
        // entry, inside the field's airspace (the spawns' spiral reaches
        // past its edge, where no broom lifts).
        let spot = if session.def.has_rings() {
            let k = session
                .seats
                .values()
                .filter(|s| s.section == n && s.state == SeatState::Present)
                .count();
            ctx.instance(zone)
                .and_then(|f| f.world.spawns().first().map(|s| s.pos))
                .map(|s| (line_up(s, k), std::f32::consts::PI))
        } else {
            None
        };
        if !ctx.enter_instance(who, zone, spot) {
            // Not in the castle any more: a room nobody entered closes.
            if opened {
                ctx.close_instance(zone);
            }
            return class_error(ctx, who, ClassErrorCode::Failed);
        }
        if let Some(seat) = session.seats.get_mut(&character_id) {
            seat.state = SeatState::Present;
        }
        // A Flying class puts every student on a broom.
        if session.def.has_rings() && !ctx.mount_broom(who) {
            tracing::warn!(class = %session.sched.id, "a student could not mount in class");
        }
        let sched: &'static ClassSchedule = session.sched;
        self.students.insert(
            who,
            Student {
                class: sched.id.as_str(),
                key: session.times.key,
                character_id,
            },
        );
        let now = ctx.unix_ms();
        let (phase, until) = session.phase_at(now);
        let info = ClassInfo {
            class: session.sched.id.clone(),
            session: session.times.key.id(),
            section: n,
            start_unix_ms: unix(session.times.start_ms),
            join_until_unix_ms: unix(session.times.join_until_ms),
            end_unix_ms: unix(session.times.end_ms),
            professor: section_professor(session, n),
            phase,
            phase_until_unix_ms: unix(until),
        };
        ctx.send(who, &ServerMsg::ClassJoined { info });
        // Arriving during a round (not in Charms, whose door closes first):
        // its beats too.
        if let LessonPhase::Practice { round } = phase
            && let Some(r) = session.rounds.iter().find(|r| r.round == round)
        {
            ctx.send(who, &r.message());
        }
    }

    /// A student's message (`TraceGesture`, `PickAnswer`): `BeatScored`.
    pub fn on_input(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, input: ClassInput) {
        let (round, beat, result) = match input {
            ClassInput::Trace {
                round,
                beat,
                start_offset_ms,
                points,
            } => (
                round,
                beat,
                self.score_trace(ctx, who, round, beat, start_offset_ms, &points),
            ),
            ClassInput::Pick {
                round,
                beat,
                choice,
                offset_ms,
            } => (
                round,
                beat,
                self.score_pick(ctx, who, round, beat, choice, offset_ms),
            ),
        };
        ctx.send(
            who,
            &ServerMsg::BeatScored {
                round,
                beat,
                result,
            },
        );
    }

    /// A checked flight move of `who` from `a` (received at
    /// `a_ms`) to `b` (at `b_ms`). In a Flying class, each open ring beat of
    /// a started round whose ring the move crosses inside the beat's window
    /// scores (`sim_core::ring`), once, and counts as an answer; the student
    /// hears `BeatScored`. A ring not passed in its window stays a miss (no
    /// message). The client claims nothing: the server dates the crossing
    /// between the two moves' arrival times.
    pub fn on_flight(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        who: EntityId,
        a: [f32; 3],
        b: [f32; 3],
        a_ms: i64,
        b_ms: i64,
    ) {
        let Some(st) = self.students.get(&who).copied() else {
            return;
        };
        let Some(idx) = self.find(st.class, st.key) else {
            return;
        };
        let s = &mut self.sessions[idx];
        if !s.def.has_rings() || s.results_given || s.rounds.is_empty() {
            return;
        }
        let Some(world) = ctx
            .zone(who)
            .and_then(|z| ctx.instance(z))
            .map(|f| f.world.clone())
        else {
            return;
        };
        let rings = &world.layout().rings;
        let Some(seat) = s
            .seats
            .get_mut(&st.character_id)
            .filter(|seat| seat.state == SeatState::Present)
        else {
            return;
        };
        let mut scored: Vec<(u8, u8, GestureResult)> = Vec::new();
        let mut passed: Vec<u8> = Vec::new();
        for r in &s.rounds {
            let window = i64::from(r.window_ms);
            for (k, (beat_at, prompt, _)) in r.beats.iter().enumerate() {
                let BeatPrompt::Ring { ring: n } = *prompt else {
                    continue;
                };
                if *beat_at + window < a_ms || *beat_at - window > b_ms || passed.contains(&n) {
                    continue;
                }
                let slot = r.first + k;
                if seat.scores.get(slot).is_none_or(Option::is_some) {
                    continue;
                }
                let Some(target) = rings.get(usize::from(n)) else {
                    continue;
                };
                let Some(crossed) = ring::crossed_at(target, a, b, a_ms, b_ms) else {
                    continue;
                };
                let Some(result) = ring::judge(gestures(), *beat_at, r.window_ms, crossed) else {
                    continue;
                };
                // One pass of a ring scores one beat.
                passed.push(n);
                seat.scores[slot] = Some(result.score);
                seat.accepted = seat.accepted.saturating_add(1);
                scored.push((r.round, k as u8, result));
            }
        }
        for (round, beat, result) in scored {
            ctx.send(
                who,
                &ServerMsg::BeatScored {
                    round,
                    beat,
                    result,
                },
            );
        }
    }

    /// Scores one pick: the trace's session checks (a student
    /// present, the shared rate limit, a round of this session under way, a
    /// beat of it not yet answered), a pick beat (`WrongBeatKind`, which
    /// does not use the beat up: a confused client may still trace it),
    /// then `sim_core::pick::judge` (the choice is one of the options, the
    /// arrival, right by its timing, wrong 0). Every judged pick uses its
    /// beat up, whatever it scored, so a second answer to the same beat (a
    /// replayed or repeated message) is `UnknownBeat`.
    fn score_pick(
        &mut self,
        ctx: &WorldCtx<'_>,
        who: EntityId,
        round: u8,
        beat: u8,
        choice: u8,
        offset_ms: i16,
    ) -> GestureResult {
        let reject = gesture::rejected;
        let Some(st) = self.students.get(&who).copied() else {
            return reject(TraceReject::NotInPractice);
        };
        let Some(idx) = self.find(st.class, st.key) else {
            return reject(TraceReject::NotInPractice);
        };
        let s = &mut self.sessions[idx];
        let Some(seat) = s
            .seats
            .get_mut(&st.character_id)
            .filter(|seat| seat.state == SeatState::Present)
        else {
            return reject(TraceReject::NotInPractice);
        };
        if !seat.traces.take(ctx.now(), TRACE_BURST, TRACE_PER_SECOND) {
            return reject(TraceReject::RateLimited);
        }
        if s.results_given {
            return reject(TraceReject::NotInPractice);
        }
        let Some(r) = s.rounds.iter().find(|r| r.round == round) else {
            return reject(TraceReject::NotInPractice);
        };
        let Some((beat_at, _, held)) = r.beats.get(usize::from(beat)) else {
            return reject(TraceReject::UnknownBeat);
        };
        let k = r.first + usize::from(beat);
        if seat.scores.get(k).is_none_or(Option::is_some) {
            return reject(TraceReject::UnknownBeat);
        }
        let Some(held) = held else {
            return reject(TraceReject::WrongBeatKind);
        };
        let result = pick::judge(
            gestures(),
            *held,
            *beat_at,
            r.window_ms,
            choice,
            offset_ms,
            ctx.unix_ms(),
        );
        seat.scores[k] = Some(result.score);
        if result.rejected.is_none() {
            seat.accepted = seat.accepted.saturating_add(1);
        }
        result
    }

    /// Scores one trace (see the module docs for the checks, in order).
    fn score_trace(
        &mut self,
        ctx: &WorldCtx<'_>,
        who: EntityId,
        round: u8,
        beat: u8,
        start_offset_ms: i16,
        points: &[protocol::TracePoint],
    ) -> GestureResult {
        let reject = gesture::rejected;
        let Some(st) = self.students.get(&who).copied() else {
            return reject(TraceReject::NotInPractice);
        };
        let Some(idx) = self.find(st.class, st.key) else {
            return reject(TraceReject::NotInPractice);
        };
        let s = &mut self.sessions[idx];
        let Some(seat) = s
            .seats
            .get_mut(&st.character_id)
            .filter(|seat| seat.state == SeatState::Present)
        else {
            return reject(TraceReject::NotInPractice);
        };
        if !seat.traces.take(ctx.now(), TRACE_BURST, TRACE_PER_SECOND) {
            return reject(TraceReject::RateLimited);
        }
        if s.results_given {
            return reject(TraceReject::NotInPractice);
        }
        let Some(r) = s.rounds.iter().find(|r| r.round == round) else {
            return reject(TraceReject::NotInPractice);
        };
        let Some((beat_at, prompt, _)) = r.beats.get(usize::from(beat)) else {
            return reject(TraceReject::UnknownBeat);
        };
        let beat_at = *beat_at;
        let k = r.first + usize::from(beat);
        if seat.scores.get(k).is_none_or(Option::is_some) {
            return reject(TraceReject::UnknownBeat);
        }
        let BeatPrompt::Trace {
            gesture: gesture_id,
        } = *prompt
        else {
            return reject(TraceReject::WrongBeatKind);
        };
        let g = gestures();
        let Some(template) = g.get(gesture_id) else {
            return reject(TraceReject::UnknownBeat);
        };
        // The shared scorer's plausibility and shape, then what only the
        // server knows: a repeat of any earlier trace, and the arrival time.
        let result = match g.check(template, points) {
            Err(reason) => reject(reason),
            Ok(_) if !s.fingerprints.insert(gesture::fingerprint(points)) => {
                reject(TraceReject::Replayed)
            }
            Ok(shape) => match g.check_arrival(
                beat_at,
                r.window_ms,
                start_offset_ms,
                shape.duration_ms,
                ctx.unix_ms(),
            ) {
                Err(reason) => reject(reason),
                Ok(()) => g.combine(
                    shape.shape,
                    g.timing(r.window_ms, i32::from(start_offset_ms)),
                ),
            },
        };
        // One score per beat, whatever it was.
        seat.scores[k] = Some(result.score);
        if result.rejected.is_none() {
            seat.accepted = seat.accepted.saturating_add(1);
        }
        result
    }

    /// A class database job finished.
    pub fn on_done(&mut self, ctx: &mut WorldCtx<'_>, done: ClassDone) {
        match done {
            ClassDone::Admitted {
                class,
                key,
                character_id,
                who,
                result,
            } => self.admitted(ctx, &class, key, character_id, who, result),
            ClassDone::Rewarded {
                class,
                session,
                character_id,
                score_pct,
                result,
            } => rewarded(ctx, class, session, character_id, score_pct, result),
        }
    }

    fn admitted(
        &mut self,
        ctx: &mut WorldCtx<'_>,
        class: &str,
        key: SessionKey,
        character_id: i64,
        who: EntityId,
        result: Option<Admission>,
    ) {
        let here = ctx.character_id(who) == Some(character_id);
        let Some(idx) = self.find(class, key) else {
            // The session ended meanwhile.
            if here {
                class_error(ctx, who, ClassErrorCode::NotOpen);
            }
            return;
        };
        let session = &mut self.sessions[idx];
        let Some(seat) = session
            .seats
            .get_mut(&character_id)
            .filter(|s| s.state == SeatState::Admitting)
        else {
            return;
        };
        match result {
            None => {
                session.seats.remove(&character_id);
                if here {
                    class_error(ctx, who, ClassErrorCode::Failed);
                }
            }
            Some(Admission::AlreadyAttended) => {
                seat.state = SeatState::Barred;
                if here {
                    class_error(ctx, who, ClassErrorCode::AlreadyAttended);
                }
            }
            Some(Admission::Admitted { attendance_id }) => {
                seat.attendance = Some(attendance_id);
                seat.state = SeatState::Away;
                // A student who signed in again meanwhile comes back
                // through the door.
                if here {
                    self.seat_student(ctx, idx, character_id, who);
                }
            }
        }
    }

    /// Marks `who` as out of their section (left, moved or disconnected).
    fn left_section(&mut self, who: EntityId) {
        let Some(st) = self.students.remove(&who) else {
            return;
        };
        if let Some(idx) = self.find(st.class, st.key)
            && let Some(seat) = self.sessions[idx].seats.get_mut(&st.character_id)
            && seat.state == SeatState::Present
        {
            seat.state = SeatState::Away;
        }
    }

    /// `who` is about to leave the world (or be replaced by a new login).
    pub fn on_left(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId) {
        let _ = ctx;
        self.left_section(who);
    }

    /// `who` moved out of zone `from` (into a section, or out of one).
    pub fn on_zone_changed(&mut self, ctx: &mut WorldCtx<'_>, who: EntityId, from: ZoneId) {
        let _ = from;
        let Some(st) = self.students.get(&who).copied() else {
            return;
        };
        let inside = self.find(st.class, st.key).is_some_and(|idx| {
            let s = &self.sessions[idx];
            let n = s.seats.get(&st.character_id).map(|seat| seat.section);
            let zone = n.and_then(|n| s.sections[usize::from(n)].zone);
            zone.is_some() && ctx.zone(who) == zone
        });
        if !inside {
            self.left_section(who);
        }
    }

    /// A class section closed (its last student left); its professor is gone.
    pub fn on_instance_closed(&mut self, ctx: &mut WorldCtx<'_>, zone: ZoneId, kind: FeatureKind) {
        let _ = ctx;
        let FeatureKind::Class { class, section } = kind else {
            return;
        };
        for s in self.sessions.iter_mut().filter(|s| s.sched.id == class) {
            if let Some(sec) = s.sections.get_mut(usize::from(section))
                && sec.zone == Some(zone)
            {
                *sec = Section::default();
            }
        }
    }

    /// Every world tick (20 Hz): the lesson scripts, phases and beats.
    pub fn tick(&mut self, ctx: &mut WorldCtx<'_>) {
        if self.sessions.is_empty() {
            return;
        }
        let now = ctx.unix_ms();
        for idx in 0..self.sessions.len() {
            self.run_session(ctx, idx, now);
        }
        // Ended sessions are gone (their rooms were closed); so are their
        // students' places.
        let before = self.sessions.len();
        self.sessions.retain(|s| now < s.times.end_ms);
        if self.sessions.len() != before {
            let live = &self.sessions;
            self.students.retain(|_, st| {
                live.iter()
                    .any(|s| s.sched.id == st.class && s.times.key == st.key)
            });
        }
    }

    /// Runs the steps of session `idx` that are due at `now`, announces a
    /// new phase, and closes its rooms at the end.
    fn run_session(&mut self, ctx: &mut WorldCtx<'_>, idx: usize, now: i64) {
        let s = &mut self.sessions[idx];
        let elapsed = s.times.elapsed_ms(now);
        if elapsed < 0 {
            // The development clock went back before the start.
            return;
        }
        let def = s.def;
        let mut ran = false;
        while let Some(step) = def.lesson.get(s.next_step) {
            let at = i64::from(step.at_secs) * 1_000;
            if at > elapsed {
                break;
            }
            s.next_step += 1;
            ran = true;
            run_step(ctx, s, step, elapsed - at > STALE_MS);
        }
        let ended = now >= s.times.end_ms;
        // Phases change only at steps and at the end.
        if ran || ended {
            let (phase, until) = s.phase_at(now);
            if phase != s.phase {
                s.phase = phase;
                let msg = ServerMsg::LessonPhaseChanged {
                    phase,
                    until_unix_ms: unix(until),
                };
                for z in s.zones() {
                    ctx.broadcast(z, &msg);
                }
            }
        }
        if ended {
            // Everyone still inside goes back to the corridor.
            for z in s.zones() {
                ctx.close_instance(z);
            }
        }
    }

    /// Students in sections now (metrics, tests).
    pub fn students(&self) -> usize {
        self.students.len()
    }
}

/// Where the `k`th student of a Flying class section stands on
/// arriving: rows of ten 1.5 m apart, the first a metre behind the first
/// spawn (`class_pitch`'s `class_entry_a`), three rows deep (a full section
/// of 30), all well inside the field's airspace.
fn line_up(spawn: [f32; 3], k: usize) -> [f32; 3] {
    let (col, row) = ((k % 10) as f32, ((k / 10) % 3) as f32);
    [
        spawn[0] + 1.0 + (col - 4.5) * 1.5,
        spawn[1],
        spawn[2] + 1.0 - row * 1.5,
    ]
}

/// The professor of section `n` (0 if it has none, which a spawn failing
/// would cause; the lesson then runs without one).
fn section_professor(s: &Session, n: u8) -> EntityId {
    s.sections
        .get(usize::from(n))
        .and_then(|sec| sec.professor)
        .unwrap_or_default()
}

/// One lesson step, in every open section of the session. `stale` steps
/// say nothing (a clock jump), but rounds and results still happen.
fn run_step(ctx: &mut WorldCtx<'_>, s: &mut Session, step: &'static LessonStep, stale: bool) {
    let content = shipped();
    let say = |ctx: &mut WorldCtx<'_>, s: &Session, line: &str| {
        for prof in s.professors() {
            let text = line.to_owned();
            ctx.npc_event(prof, NpcEvent::Say { text });
        }
    };
    match &step.action {
        LessonAction::Say { line } if !stale => say(ctx, s, line),
        LessonAction::Emote { emote } if !stale => {
            for prof in s.professors() {
                ctx.npc_event(
                    prof,
                    NpcEvent::Emote {
                        emote: emote.emote(),
                    },
                );
            }
        }
        LessonAction::Demonstrate {
            gesture,
            spell,
            duration_ms,
            line,
        } if !stale => {
            let spell = spell
                .as_deref()
                .and_then(|k| content.spells.by_key(k))
                .map(|sp| sp.id);
            // Without a gesture (a pick class) only the line is said.
            if let Some(g) = gesture.as_deref().and_then(|k| content.gestures.by_key(k)) {
                for prof in s.professors() {
                    let event = NpcEvent::Demonstrate {
                        gesture: g.id,
                        spell,
                        duration_ms: *duration_ms,
                    };
                    ctx.npc_event(prof, event);
                }
            }
            if let Some(line) = line {
                say(ctx, s, line);
            }
        }
        LessonAction::Say { .. }
        | LessonAction::Emote { .. }
        | LessonAction::Demonstrate { .. } => {}
        LessonAction::Practice {
            round,
            window_ms,
            beats,
        } => {
            let start = s.times.start_ms + i64::from(step.at_secs) * 1_000;
            let round = Round {
                round: *round,
                window_ms: *window_ms,
                first: s.rounds.iter().map(|r| r.beats.len()).sum(),
                beats: beats
                    .iter()
                    .filter_map(|b| {
                        let at = start + i64::from(b.at_ms);
                        match &b.kind {
                            BeatKind::Trace { gesture } => {
                                let g = content.gestures.by_key(gesture)?;
                                Some((at, BeatPrompt::Trace { gesture: g.id }, None))
                            }
                            // A ring is flown through; the Server
                            // builder scores it from the student's moves.
                            BeatKind::Ring { ring } => {
                                Some((at, BeatPrompt::Ring { ring: *ring }, None))
                            }
                            BeatKind::Pick { .. } => {
                                let (prompt, held) = content.picks.resolve(
                                    s.def.picks.as_ref()?,
                                    content.answers(&s.def.id)?,
                                    &b.kind,
                                )?;
                                Some((at, prompt, Some(held)))
                            }
                        }
                    })
                    .collect(),
            };
            let msg = round.message();
            s.rounds.push(round);
            for z in s.zones() {
                ctx.broadcast(z, &msg);
            }
        }
        LessonAction::Results { line } => {
            if let Some(line) = line.as_deref().filter(|_| !stale) {
                say(ctx, s, line);
            }
            give_results(ctx, s);
        }
    }
}

/// The results: a reward job for every student in the room.
fn give_results(ctx: &mut WorldCtx<'_>, s: &mut Session) {
    if s.results_given {
        return;
    }
    s.results_given = true;
    let session = s.times.key.id();
    for req in reward_requests(s) {
        ctx.spawn(Box::new(move |pool| {
            Box::pin(async move {
                let result = reward_with_retries(&pool, &req).await;
                ExtDone::Class(ClassDone::Rewarded {
                    class: req.class_id,
                    session,
                    character_id: req.character_id,
                    score_pct: req.score_pct,
                    result,
                })
            })
        }));
    }
}

/// What to reward: every student in the room with an attendance row, with
/// their score, accepted answers and their section's average score (over
/// the students in its room).
fn reward_requests(s: &Session) -> Vec<RewardRequest> {
    let mut sums: HashMap<u8, (u32, u32)> = HashMap::new();
    for seat in s
        .seats
        .values()
        .filter(|seat| seat.state == SeatState::Present)
    {
        let e = sums.entry(seat.section).or_default();
        e.0 += u32::from(seat.score_pct());
        e.1 += 1;
    }
    let section_pct = |n: u8| {
        sums.get(&n)
            .map_or(0, |&(sum, k)| (sum / k.max(1)).min(100) as u8)
    };
    let mut out: Vec<RewardRequest> = s
        .seats
        .iter()
        .filter_map(|(&character_id, seat)| {
            let attendance_id = seat
                .attendance
                .filter(|_| seat.state == SeatState::Present)?;
            Some(RewardRequest {
                attendance_id,
                character_id,
                class_id: s.sched.id.clone(),
                score_pct: seat.score_pct(),
                answers: seat.accepted,
                section_pct: section_pct(seat.section),
            })
        })
        .collect();
    out.sort_by_key(|r| r.character_id);
    out
}

/// [`super::reward`], retried a few times on a database error (it is
/// idempotent); `None` if it kept failing.
async fn reward_with_retries(pool: &sqlx::PgPool, req: &RewardRequest) -> Option<RewardOutcome> {
    for attempt in 1..=REWARD_ATTEMPTS {
        match super::reward(pool, req, chrono::Utc::now()).await {
            Ok(r) => return Some(r),
            Err(e) => {
                tracing::warn!(error = %e, attempt, attendance = req.attendance_id, "class reward failed");
                tokio::time::sleep(Duration::from_millis(200 * u64::from(attempt))).await;
            }
        }
    }
    None
}

/// A reward job came back: `ClassResult`, then `Progress` for each grant,
/// `Spellbook` if spells were learned or the level changed, `Wallet` if it
/// paid Crowns, and the fortune's effect if it drew one. The student may
/// have left the room (or signed in again) since.
fn rewarded(
    ctx: &mut WorldCtx<'_>,
    class: String,
    session: i64,
    character_id: i64,
    score_pct: u8,
    result: Option<RewardOutcome>,
) {
    let Some(e) = ctx.entity_of(character_id) else {
        return;
    };
    let Some(outcome) = result else {
        return class_error(ctx, e, ClassErrorCode::Failed);
    };
    let rewards = match &outcome {
        RewardOutcome::Granted(r) => r.rewards.clone(),
        RewardOutcome::AlreadyRewarded(r) => r.clone(),
        RewardOutcome::NotTakingPart => ClassRewards::default(),
    };
    let class_id = class.clone();
    let msg = ServerMsg::ClassResult {
        class,
        session,
        score_pct,
        grade: gestures().grade(score_pct),
        rewards,
    };
    ctx.send(e, &msg);
    let flying = shipped()
        .class(&class_id)
        .is_some_and(|c| c.subject == sim_core::Subject::Flying);
    if let RewardOutcome::Granted(r) = outcome {
        // A Flying class's skill lets the student mount.
        if flying {
            ctx.set_flying_xp(e, r.skill_total);
        }
        for g in r.grants.iter().filter(|g| !g.duplicate) {
            ctx.progress_changed(e, g.xp, Some(g.gain()));
        }
        if let Some(crowns) = r.crowns_balance {
            ctx.send(e, &ServerMsg::Wallet { crowns });
        }
        if let Some(kind) = r.rewards.fortune {
            let until = ctx.now() + crate::effects::fortune_duration();
            ctx.apply_effect(e, crate::effects::fortune_effect(kind, until));
        }
        if !r.rewards.spells.is_empty() || r.leveled_up() {
            ctx.set_spellbook(e, r.learned, r.loadout, r.rewards.spells);
        }
    }
}

#[cfg(test)]
#[path = "world_tests.rs"]
mod tests;
