//! Tests of the class handler on the world thread, without a database:
//! the admission and reward jobs are dropped and their results fed back by
//! hand (`ClassDone`), so every path (an admission failing, a student
//! barred, a reward granted) is driven deterministically. The world is the
//! realtime tests' hall with its class door renamed to the class's door
//! (Charms, or Potions for the pick beats ), on a clock pinned
//! to a session of that class.

use std::sync::Arc;
use std::time::{Duration, Instant};

use protocol::{
    ClassErrorCode, ClassRewards, Grade, House, LessonPhase, NpcEvent, ServerMsg, TracePoint,
    TraceReject, XpSource, Zone,
};

use super::*;
use crate::clock::ServerClock;
use crate::metrics::Registry;
use crate::progression::XpGrant;
use crate::realtime::chat::WordFilter;
use crate::realtime::metrics::RealtimeMetrics;
use crate::realtime::world::tests::{LAYOUT, TestClient, join};
use crate::realtime::world::{Input, WorldSim};

const DOOR: [f32; 3] = [130.0, 0.0, 100.0];

/// A world whose clock reads the start of a session of `class` at `t0`.
struct Lesson {
    s: WorldSim,
    t0: Instant,
    times: SessionTimes,
    class: &'static str,
    door: String,
}

impl Lesson {
    fn new() -> Self {
        Self::of("charms")
    }

    fn of(class: &'static str) -> Self {
        let door = shipped().schedule.class(class).unwrap().door.clone();
        let layout = LAYOUT.replace("class_door_t", &door);
        let mut s = WorldSim::new(
            Arc::new(sim_core::World::from_json(&layout).unwrap()),
            WordFilter::from_list("badword"),
            None,
            Arc::new(RealtimeMetrics::register(&Registry::default())),
            1,
        );
        let schedule = &shipped().schedule;
        let sched = schedule.class(class).unwrap();
        let times = schedule.next(sched, 1_800_000_000_000);
        let t0 = Instant::now();
        s.set_clock(Arc::new(ServerClock::starting_at(times.start_ms, t0)));
        Lesson {
            s,
            t0,
            times,
            class,
            door,
        }
    }

    /// The instant `ms` into the session.
    fn at(&self, ms: i64) -> Instant {
        self.t0 + Duration::from_millis(ms as u64)
    }

    fn student(&mut self, name: &str) -> TestClient {
        let mut c = join(&mut self.s, name, House::Skytalon, DOOR);
        c.drain();
        c
    }

    fn door(&mut self, c: &TestClient, ms: i64) {
        let at = self.at(ms);
        let door = self.door.clone();
        self.s.input(c.id, at, Input::Interact(door));
    }

    /// Feeds back the admission job of `c` (dropping the real one).
    fn admitted(&mut self, c: &TestClient, ms: i64, result: Option<Admission>) {
        let done = ClassDone::Admitted {
            class: self.class.into(),
            key: self.times.key,
            character_id: c.cid,
            who: c.id,
            result,
        };
        let at = self.at(ms);
        self.s.ext_done(ExtDone::Class(done), at);
    }

    /// Door, job, admitted: the student is in. Returns its section.
    fn enter(&mut self, c: &mut TestClient, ms: i64, attendance_id: i64) -> u8 {
        self.door(c, ms);
        assert_eq!(self.s.take_ext_jobs().len(), 1, "one admission job");
        self.admitted(c, ms, Some(Admission::Admitted { attendance_id }));
        let msgs = c.drain();
        let section = msgs.iter().find_map(|m| match m {
            ServerMsg::ZoneChanged {
                zone: Zone::Class { section, .. },
                ..
            } => Some(*section),
            _ => None,
        });
        assert!(
            msgs.iter()
                .any(|m| matches!(m, ServerMsg::ClassJoined { .. })),
            "{msgs:?}"
        );
        section.expect("into a section")
    }

    fn tick(&mut self, ms: i64) {
        let at = self.at(ms);
        self.s.tick(at);
    }

    /// Runs `f` with a world context and returns what it found.
    fn with_ctx<T: Send + 'static>(
        &mut self,
        f: impl FnOnce(&mut WorldCtx<'_>) -> T + Send + 'static,
    ) -> T {
        let out = Arc::new(std::sync::Mutex::new(None));
        let o = out.clone();
        let run = ExtDone::Run(Box::new(move |ctx| *o.lock().unwrap() = Some(f(ctx))));
        self.s.ext_done(run, Instant::now());
        out.lock().unwrap().take().expect("ran")
    }

    fn trace(
        &mut self,
        c: &TestClient,
        ms: i64,
        round: u8,
        beat: u8,
        offset: i16,
        pts: Vec<TracePoint>,
    ) {
        let at = self.at(ms);
        let input = Input::Class(ClassInput::Trace {
            round,
            beat,
            start_offset_ms: offset,
            points: pts,
        });
        self.s.input(c.id, at, input);
    }

    fn pick(&mut self, c: &TestClient, ms: i64, round: u8, beat: u8, choice: u8, offset: i16) {
        let at = self.at(ms);
        let input = Input::Class(ClassInput::Pick {
            round,
            beat,
            choice,
            offset_ms: offset,
        });
        self.s.input(c.id, at, input);
    }

    /// When beat `beat` of round `round` starts (ms into the session).
    fn beat_ms(round: u8, beat: usize) -> (i64, u16) {
        Self::beat_ms_of("charms", round, beat)
    }

    fn beat_ms_of(class: &str, round: u8, beat: usize) -> (i64, u16) {
        let def = shipped().class(class).unwrap();
        let (step, _, window, beats) = def.rounds().find(|r| r.1 == round).unwrap();
        (
            i64::from(step.at_secs) * 1_000 + i64::from(beats[beat].at_ms),
            window,
        )
    }
}

fn errors(c: &mut TestClient) -> Vec<ClassErrorCode> {
    c.drain()
        .into_iter()
        .filter_map(|m| match m {
            ServerMsg::ClassError { code } => Some(code),
            _ => None,
        })
        .collect()
}

fn scored(msgs: &[ServerMsg]) -> Vec<GestureResult> {
    msgs.iter()
        .filter_map(|m| match m {
            ServerMsg::BeatScored { result, .. } => Some(*result),
            _ => None,
        })
        .collect()
}

/// A hand-like trace of gesture `key`: 40 samples over `ms` along the
/// template, wobbling by up to `wobble` pad units (`seed` varies it).
fn hand_trace(key: &str, ms: u32, wobble: f32, seed: f32) -> Vec<TracePoint> {
    let g = gestures().by_key(key).unwrap();
    let pts: Vec<[f32; 2]> = g
        .points
        .iter()
        .map(|p| [f32::from(p[0]), f32::from(p[1])])
        .collect();
    let seg: Vec<f32> = pts
        .windows(2)
        .map(|w| ((w[1][0] - w[0][0]).powi(2) + (w[1][1] - w[0][1]).powi(2)).sqrt())
        .collect();
    let total: f32 = seg.iter().sum();
    let n = 40;
    let dt = (ms / (n - 1)) as u8;
    (0..n)
        .map(|k| {
            // The point k/(n-1) of the way along the template.
            let mut d = total * k as f32 / (n - 1) as f32;
            let mut i = 0;
            while i + 1 < seg.len() && d > seg[i] {
                d -= seg[i];
                i += 1;
            }
            let t = (d / seg[i].max(1e-6)).min(1.0);
            let x = pts[i][0] + t * (pts[i + 1][0] - pts[i][0]);
            let y = pts[i][1] + t * (pts[i + 1][1] - pts[i][1]);
            let w = wobble * ((k as f32 * 1.7 + seed).sin() + (k as f32 * 0.6 + seed).cos()) / 2.0;
            TracePoint {
                dt_ms: if k == 0 { 0 } else { dt },
                x: (x + w).round().clamp(-100.0, 100.0) as i8,
                y: (y - w).round().clamp(-100.0, 100.0) as i8,
            }
        })
        .collect()
}

#[test]
fn the_door_admits_only_during_the_join_window() {
    let mut l = Lesson::new();
    let mut a = l.student("Ada");
    l.door(&a, -1_000);
    assert_eq!(errors(&mut a), vec![ClassErrorCode::NotOpen], "before");
    let join_ms = l.times.join_until_ms - l.times.start_ms;
    l.door(&a, join_ms);
    assert_eq!(errors(&mut a), vec![ClassErrorCode::NotOpen], "after");
    assert!(l.s.take_ext_jobs().is_empty());
    // At the last moment it opens; the job then admits.
    let section = l.enter(&mut a, join_ms - 1, 11);
    assert_eq!(section, 0);
}

#[test]
fn a_full_section_opens_another_and_the_professor_greets_each() {
    let mut l = Lesson::new();
    let cap = usize::from(shipped().schedule.class("charms").unwrap().instance_cap);
    let mut students: Vec<TestClient> = (0..=cap).map(|k| l.student(&format!("S{k}"))).collect();
    // Everyone clicks first (seats are reserved while their jobs run).
    for c in &students {
        l.door(c, 1_000);
    }
    assert_eq!(l.s.take_ext_jobs().len(), cap + 1);
    let mut sections = Vec::new();
    for (k, c) in students.iter_mut().enumerate() {
        l.admitted(
            c,
            1_100,
            Some(Admission::Admitted {
                attendance_id: k as i64,
            }),
        );
        let msgs = c.drain();
        let info = msgs
            .iter()
            .find_map(|m| match m {
                ServerMsg::ClassJoined { info } => Some(info.clone()),
                _ => None,
            })
            .expect("joined");
        assert_eq!(info.phase, LessonPhase::Gathering);
        assert_eq!(info.start_unix_ms, l.times.start_ms as u64);
        sections.push((info.section, info.professor));
    }
    assert!(sections[..cap].iter().all(|s| s.0 == 0));
    assert_eq!(sections[cap].0, 1, "the 31st opens section 1");
    assert_ne!(sections[0].1, sections[cap].1, "with its own professor");
    // The script reaches both sections: the wave at 2 s, the greeting at 3 s.
    l.tick(3_000);
    for c in [0, cap] {
        let msgs = students[c].drain();
        assert!(msgs.contains(&ServerMsg::NpcEvent {
            id: sections[c].1,
            event: NpcEvent::Emote {
                emote: protocol::Emote::Wave
            },
        }));
        assert!(msgs.iter().any(|m| matches!(
            m,
            ServerMsg::NpcEvent {
                event: NpcEvent::Say { .. },
                ..
            }
        )));
    }
}

#[test]
fn leaving_and_coming_back_keeps_the_seat_but_never_another_section() {
    let mut l = Lesson::new();
    let mut a = l.student("Ada");
    assert_eq!(l.enter(&mut a, 1_000, 7), 0);
    l.s.input(a.id, l.at(2_000), Input::LeaveInstance);
    assert!(a.drain().iter().any(|m| matches!(
        m,
        ServerMsg::ZoneChanged {
            zone: Zone::Castle,
            ..
        }
    )));
    // The room emptied and closed; coming back reopens it, with no new job.
    l.door(&a, 3_000);
    assert!(l.s.take_ext_jobs().is_empty(), "the attendance is known");
    let msgs = a.drain();
    assert!(msgs.iter().any(|m| matches!(
        m,
        ServerMsg::ZoneChanged {
            zone: Zone::Class { section: 0, .. },
            ..
        }
    )));
    // A student the database knows as rewarded is barred, now and later.
    let mut b = l.student("Bea");
    l.door(&b, 4_000);
    l.s.take_ext_jobs();
    l.admitted(&b, 4_000, Some(Admission::AlreadyAttended));
    assert_eq!(errors(&mut b), vec![ClassErrorCode::AlreadyAttended]);
    l.door(&b, 5_000);
    assert_eq!(errors(&mut b), vec![ClassErrorCode::AlreadyAttended]);
    assert!(l.s.take_ext_jobs().is_empty());
    // A failed admission frees the seat: the next click tries again.
    let mut c = l.student("Cy");
    l.door(&c, 6_000);
    l.s.take_ext_jobs();
    l.admitted(&c, 6_000, None);
    assert_eq!(errors(&mut c), vec![ClassErrorCode::Failed]);
    l.door(&c, 7_000);
    assert_eq!(l.s.take_ext_jobs().len(), 1);
}

#[test]
fn traces_are_scored_once_per_beat_and_impossible_ones_rejected() {
    let mut l = Lesson::new();
    let mut a = l.student("Ada");
    let mut b = l.student("Bea");
    l.enter(&mut a, 1_000, 1);
    l.enter(&mut b, 1_000, 2);
    // Before any round: not in practice.
    let (beat0, window) = Lesson::beat_ms(1, 0);
    let good = hand_trace("twirl_and_tap", 900, 4.0, 0.0);
    l.trace(&a, 10_000, 1, 0, 0, good.clone());
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::NotInPractice)
    );
    // Round 1 starts: its beats go to everyone.
    l.tick(200_000);
    let round = a.drain().into_iter().find_map(|m| match m {
        ServerMsg::LessonRound {
            round,
            beats,
            window_ms,
        } => Some((round, beats, window_ms)),
        _ => None,
    });
    let (n, beats, w) = round.expect("LessonRound");
    assert_eq!((n, w), (1, window));
    assert_eq!(beats[0].at_unix_ms, (l.times.start_ms + beat0) as u64);
    assert!(b.drain().iter().any(|m| matches!(
        m,
        ServerMsg::LessonPhaseChanged {
            phase: LessonPhase::Practice { round: 1 },
            ..
        }
    )));

    // On the beat, drawn by hand: a good score.
    l.trace(&a, beat0 + 30 + 900 + 40, 1, 0, 30, good.clone());
    let r = scored(&a.drain())[0];
    assert_eq!(r.rejected, None, "{r:?}");
    assert!(matches!(r.grade, Grade::Great | Grade::Perfect), "{r:?}");
    // One score per beat.
    l.trace(
        &a,
        beat0 + 1_200,
        1,
        0,
        30,
        hand_trace("twirl_and_tap", 900, 5.0, 1.0),
    );
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::UnknownBeat)
    );
    // Bea copies Ada's trace: replayed (any student, this session).
    l.trace(&b, beat0 + 30 + 900 + 40, 1, 0, 30, good.clone());
    assert_eq!(scored(&b.drain())[0].rejected, Some(TraceReject::Replayed));
    // No such beat.
    l.trace(
        &b,
        beat0 + 1_000,
        1,
        40,
        0,
        hand_trace("twirl_and_tap", 900, 4.0, 2.0),
    );
    assert_eq!(
        scored(&b.drain())[0].rejected,
        Some(TraceReject::UnknownBeat)
    );

    let (beat1, _) = Lesson::beat_ms(1, 1);
    // Arriving before it could have been drawn.
    l.trace(
        &a,
        beat1 + 100,
        1,
        1,
        0,
        hand_trace("twirl_and_tap", 900, 4.0, 3.0),
    );
    assert_eq!(scored(&a.drain())[0].rejected, Some(TraceReject::BadTiming));
    // The scorer's own checks: too fast (Bea, beat 1).
    l.trace(
        &b,
        beat1 + 400,
        1,
        1,
        0,
        hand_trace("twirl_and_tap", 80, 4.0, 4.0),
    );
    let r = scored(&b.drain())[0].rejected;
    assert!(
        matches!(r, Some(TraceReject::TooFast | TraceReject::BadDuration)),
        "{r:?}"
    );
    // Too few samples.
    let (beat2, _) = Lesson::beat_ms(1, 2);
    let few = hand_trace("twirl_and_tap", 900, 4.0, 5.0)[..4].to_vec();
    l.trace(&a, beat2 + 1_000, 1, 2, 0, few);
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::BadSampleCount)
    );
    // A burst of traces runs into the rate limit.
    let (beat3, _) = Lesson::beat_ms(1, 3);
    for k in 0..6 {
        l.trace(
            &b,
            beat3 + 1_000,
            1,
            40 + k,
            0,
            hand_trace("circle", 900, 4.0, k as f32),
        );
    }
    let rejects: Vec<_> = scored(&b.drain()).iter().map(|r| r.rejected).collect();
    assert!(
        rejects.contains(&Some(TraceReject::RateLimited)),
        "{rejects:?}"
    );
    // Someone in the castle is not in practice.
    let mut c = l.student("Cy");
    l.trace(
        &c,
        beat3 + 1_000,
        1,
        3,
        0,
        hand_trace("twirl_and_tap", 900, 4.0, 9.0),
    );
    assert_eq!(
        scored(&c.drain())[0].rejected,
        Some(TraceReject::NotInPractice)
    );
}

fn grant(source: XpSource, granted: u32, xp: u32, before: u8, level: u8) -> XpGrant {
    XpGrant {
        source,
        requested: granted,
        granted,
        capped: false,
        duplicate: false,
        xp,
        level_before: before,
        level,
    }
}

#[test]
fn results_reward_each_student_in_the_room_once_and_the_end_sends_them_back() {
    let mut l = Lesson::new();
    let mut a = l.student("Ada");
    let mut b = l.student("Bea");
    let mut c = l.student("Cy");
    l.enter(&mut a, 1_000, 31);
    l.enter(&mut b, 1_000, 32);
    l.enter(&mut c, 1_000, 33);
    let id = a.id;
    let zone = l.with_ctx(move |ctx| ctx.zone(id)).unwrap();
    // Ada traces three beats well; Cy leaves before the results.
    l.tick(200_000);
    for beat in 0..3 {
        let (at, _) = Lesson::beat_ms(1, beat);
        let t = hand_trace("twirl_and_tap", 900, 4.0, beat as f32);
        l.trace(&a, at + 1_000, 1, beat as u8, 50, t);
    }
    assert!(scored(&a.drain()).iter().all(|r| r.rejected.is_none()));
    l.s.input(c.id, l.at(300_000), Input::LeaveInstance);
    l.tick(409_000);
    assert!(l.s.take_ext_jobs().is_empty(), "nothing before the results");
    l.tick(410_000);
    assert_eq!(l.s.take_ext_jobs().len(), 2, "Ada and Bea, not Cy");
    l.tick(410_050);
    assert!(l.s.take_ext_jobs().is_empty(), "the results run once");
    assert!(a.drain().iter().any(|m| matches!(
        m,
        ServerMsg::LessonPhaseChanged {
            phase: LessonPhase::Results,
            ..
        }
    )));

    // Ada's reward: a level and Lucerna.
    let level2 = shipped().progression.levels[1];
    let lucerna = shipped().spells.by_key("lucerna").unwrap().id;
    let rewards = ClassRewards {
        xp: 110,
        skill_xp: 40,
        house_points: 1,
        spells: vec![lucerna],
        ..ClassRewards::default()
    };
    let done = ClassDone::Rewarded {
        class: "charms".into(),
        session: l.times.key.id(),
        character_id: a.cid,
        score_pct: 25,
        result: Some(RewardOutcome::Granted(super::super::Rewarded {
            rewards: rewards.clone(),
            grants: vec![
                grant(XpSource::ClassAttend, 80, level2 - 10, 1, 1),
                grant(XpSource::ClassBonus, 30, level2 + 20, 1, 2),
            ],
            learned: vec![lucerna],
            loadout: vec![],
            crowns_balance: Some(130),
            skill_total: 0,
        })),
    };
    l.s.ext_done(ExtDone::Class(done), l.at(410_100));
    let msgs = a.drain();
    assert!(msgs.contains(&ServerMsg::ClassResult {
        class: "charms".into(),
        session: l.times.key.id(),
        score_pct: 25,
        grade: gestures().grade(25),
        rewards,
    }));
    let progress: Vec<_> = msgs
        .iter()
        .filter_map(|m| match m {
            ServerMsg::Progress { progress, gained } => {
                Some((progress.level, gained.unwrap().source))
            }
            _ => None,
        })
        .collect();
    assert_eq!(
        progress,
        vec![(1, XpSource::ClassAttend), (2, XpSource::ClassBonus)]
    );
    assert!(msgs.iter().any(|m| matches!(
        m,
        ServerMsg::Spellbook { new, .. } if new == &vec![lucerna]
    )));
    assert!(msgs.contains(&ServerMsg::Wallet { crowns: 130 }));
    // Bea took too little part: a result with nothing in it.
    let done = ClassDone::Rewarded {
        class: "charms".into(),
        session: l.times.key.id(),
        character_id: b.cid,
        score_pct: 0,
        result: Some(RewardOutcome::NotTakingPart),
    };
    l.s.ext_done(ExtDone::Class(done), l.at(410_100));
    let msgs = b.drain();
    assert!(msgs.iter().any(|m| matches!(
        m,
        ServerMsg::ClassResult {
            rewards: ClassRewards { xp: 0, .. },
            ..
        }
    )));
    assert!(!msgs.iter().any(|m| matches!(m, ServerMsg::Progress { .. })));

    // Traces after the results do not count.
    let (at, _) = Lesson::beat_ms(4, 0);
    l.trace(&a, at + 1_000, 4, 0, 0, hand_trace("zigzag", 900, 4.0, 1.0));
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::NotInPractice)
    );

    // The end: back to the corridor, the room closes, the door is shut.
    let end = l.times.end_ms - l.times.start_ms;
    l.tick(end);
    for c in [&mut a, &mut b] {
        let msgs = c.drain();
        assert!(msgs.iter().any(|m| matches!(
            m,
            ServerMsg::LessonPhaseChanged {
                phase: LessonPhase::Ended,
                ..
            }
        )));
        assert!(msgs.iter().any(|m| matches!(
            m,
            ServerMsg::ZoneChanged {
                zone: Zone::Castle,
                ..
            }
        )));
    }
    assert!(l.with_ctx(move |ctx| ctx.instance(zone).is_none()));
    assert_eq!(l.s.position(a.id), Some(DOOR), "back where they stood");
    assert_eq!(l.s.players(), 3, "the professor went with the room");
    l.door(&a, end + 1_000);
    assert_eq!(errors(&mut a), vec![ClassErrorCode::NotOpen]);
}

#[test]
fn a_clock_jump_skips_stale_lines_but_still_runs_rounds() {
    let mut l = Lesson::new();
    let mut a = l.student("Ada");
    l.enter(&mut a, 1_000, 5);
    // Straight from the greeting into round 2: one phase message, round 1
    // and 2's beats, no old lines.
    l.tick(260_000);
    let msgs = a.drain();
    let says = msgs
        .iter()
        .filter(|m| matches!(m, ServerMsg::NpcEvent { .. }))
        .count();
    assert_eq!(says, 0, "{msgs:?}");
    let rounds: Vec<u8> = msgs
        .iter()
        .filter_map(|m| match m {
            ServerMsg::LessonRound { round, .. } => Some(*round),
            _ => None,
        })
        .collect();
    assert_eq!(rounds, vec![1, 2]);
    let phases: Vec<_> = msgs
        .iter()
        .filter_map(|m| match m {
            ServerMsg::LessonPhaseChanged { phase, .. } => Some(*phase),
            _ => None,
        })
        .collect();
    assert_eq!(phases, vec![LessonPhase::Practice { round: 2 }]);
}

// ---------------------------------------------------------------- Picks

/// The right option's position in beat `beat` of round `round` of `class`
/// (from the answer key the server holds; never sent).
fn answer(class: &str, round: u8, beat: usize) -> PickBeat {
    let content = shipped();
    let def = content.class(class).unwrap();
    let (_, _, _, beats) = def.rounds().find(|r| r.1 == round).unwrap();
    let (_, held) = content
        .picks
        .resolve(
            def.picks.as_ref().unwrap(),
            content.answers(class).unwrap(),
            &beats[beat].kind,
        )
        .unwrap();
    held
}

/// Round `round`'s beats as the students received them.
fn round_beats(msgs: &[ServerMsg], round: u8) -> Vec<protocol::LessonBeat> {
    msgs.iter()
        .find_map(|m| match m {
            ServerMsg::LessonRound {
                round: r, beats, ..
            } if *r == round => Some(beats.clone()),
            _ => None,
        })
        .expect("LessonRound")
}

#[test]
fn pick_rounds_send_prompts_and_options_but_never_the_answer() {
    let mut l = Lesson::of("potions");
    let mut a = l.student("Ada");
    l.enter(&mut a, 1_000, 1);
    let (beat0, _) = Lesson::beat_ms_of("potions", 1, 0);
    l.tick(beat0 - 4_000);
    let beats = round_beats(&a.drain(), 1);
    let def = shipped().class("potions").unwrap();
    let (_, _, _, defs) = def.rounds().find(|r| r.1 == 1).unwrap();
    assert_eq!(beats.len(), defs.len());
    for (k, b) in beats.iter().enumerate() {
        let protocol::BeatPrompt::Pick { prompt, options } = &b.prompt else {
            panic!("a pick beat: {b:?}");
        };
        assert!((2..=protocol::PICK_OPTIONS_MAX).contains(&options.len()));
        // The indices name the beat's own prompt and options in the shared
        // vocabulary (the client reads the same file).
        let set = shipped().picks.set("potions_ingredients").unwrap();
        let sim_core::classes::BeatKind::Pick {
            prompt: key,
            options: keys,
        } = &defs[k].kind
        else {
            panic!("a pick beat");
        };
        assert_eq!(set.get(*prompt).unwrap().key, *key);
        let names: Vec<&str> = options
            .iter()
            .map(|&i| set.get(i).unwrap().key.as_str())
            .collect();
        assert_eq!(names, keys.iter().map(String::as_str).collect::<Vec<_>>());
        assert_eq!(
            b.at_unix_ms,
            (l.times.start_ms + beat0 + (k as i64) * 3_500) as u64
        );
    }
}

#[test]
fn picks_are_judged_once_per_beat_against_the_held_answer() {
    let mut l = Lesson::of("potions");
    let mut a = l.student("Ada");
    let mut b = l.student("Bea");
    l.enter(&mut a, 1_000, 1);
    l.enter(&mut b, 1_000, 2);
    // Before any round: not in practice.
    l.pick(&a, 10_000, 1, 0, 0, 0);
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::NotInPractice)
    );
    let (beat0, _) = Lesson::beat_ms_of("potions", 1, 0);
    l.tick(beat0 - 4_000);
    a.drain();
    b.drain();

    // Ada picks right on the beat: a full score.
    let right = answer("potions", 1, 0);
    l.pick(&a, beat0 + 40, 1, 0, right.answer, 30);
    let r = scored(&a.drain())[0];
    assert_eq!(r.rejected, None, "{r:?}");
    assert_eq!(r.score, 100, "{r:?}");
    assert_eq!(r.grade, Grade::Perfect);
    // One answer per beat: again (a repeat or a replayed message).
    l.pick(&a, beat0 + 200, 1, 0, right.answer, 30);
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::UnknownBeat)
    );
    // Bea picks wrong: 0, a miss, not a rejection; the beat is used up.
    let wrong = (right.answer + 1) % right.options;
    l.pick(&b, beat0 + 40, 1, 0, wrong, 30);
    let r = scored(&b.drain())[0];
    assert_eq!((r.score, r.rejected, r.grade), (0, None, Grade::Miss));
    l.pick(&b, beat0 + 300, 1, 0, right.answer, 30);
    assert_eq!(
        scored(&b.drain())[0].rejected,
        Some(TraceReject::UnknownBeat)
    );

    // Beat 1: a trace on a pick beat is the wrong kind (and does not use it
    // up); a choice past the options is refused.
    let (beat1, _) = Lesson::beat_ms_of("potions", 1, 1);
    let t = hand_trace("twirl_and_tap", 900, 4.0, 0.0);
    l.trace(&a, beat1 + 30 + 900 + 40, 1, 1, 30, t);
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::WrongBeatKind)
    );
    let right1 = answer("potions", 1, 1);
    l.pick(&a, beat1 + 600, 1, 1, right1.answer, 550);
    let late_ok = scored(&a.drain())[0];
    assert_eq!(late_ok.rejected, None, "{late_ok:?}");
    assert!(late_ok.score < 100 && late_ok.score > 0, "{late_ok:?}");
    l.pick(&b, beat1 + 40, 1, 1, right1.options, 30);
    assert_eq!(scored(&b.drain())[0].rejected, Some(TraceReject::BadChoice));

    // Beat 2: claims that do not fit the arrival are bad timing.
    let (beat2, window) = Lesson::beat_ms_of("potions", 1, 2);
    let right2 = answer("potions", 1, 2);
    // Received 100 ms after the beat, claiming it was made 900 ms after.
    l.pick(&a, beat2 + 100, 1, 2, right2.answer, 900);
    assert_eq!(scored(&a.drain())[0].rejected, Some(TraceReject::BadTiming));
    // Outside the window.
    let past = i16::try_from(window).unwrap() + 50;
    l.pick(&b, beat2 + i64::from(past) + 20, 1, 2, right2.answer, past);
    assert_eq!(scored(&b.drain())[0].rejected, Some(TraceReject::BadTiming));
    // A round that has not started, a beat that does not exist.
    l.pick(&a, beat2 + 1_000, 2, 0, 0, 0);
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::NotInPractice)
    );
    l.pick(&a, beat2 + 1_000, 1, 40, 0, 0);
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::UnknownBeat)
    );

    // A burst runs into the shared rate limit.
    let (beat3, _) = Lesson::beat_ms_of("potions", 1, 3);
    for k in 0..6 {
        l.pick(&b, beat3 + 40, 1, 3 + (k % 2), 0, 30);
    }
    let rejects: Vec<_> = scored(&b.drain()).iter().map(|r| r.rejected).collect();
    assert!(
        rejects.contains(&Some(TraceReject::RateLimited)),
        "{rejects:?}"
    );
    // Someone outside the class is not in practice.
    let mut c = l.student("Cy");
    l.pick(&c, beat3 + 40, 1, 3, 0, 30);
    assert_eq!(
        scored(&c.drain())[0].rejected,
        Some(TraceReject::NotInPractice)
    );
}

#[test]
fn a_trace_class_refuses_picks_as_the_wrong_kind() {
    let mut l = Lesson::new();
    let mut a = l.student("Ada");
    l.enter(&mut a, 1_000, 1);
    let (beat0, _) = Lesson::beat_ms(1, 0);
    l.tick(200_000);
    a.drain();
    l.pick(&a, beat0 + 40, 1, 0, 0, 30);
    assert_eq!(
        scored(&a.drain())[0].rejected,
        Some(TraceReject::WrongBeatKind)
    );
    // The beat is still open for its trace.
    let good = hand_trace("twirl_and_tap", 900, 4.0, 0.0);
    l.trace(&a, beat0 + 30 + 900 + 40, 1, 0, 30, good);
    assert_eq!(scored(&a.drain())[0].rejected, None);
}

#[test]
fn a_student_who_picks_every_beat_right_scores_full_marks() {
    let mut l = Lesson::of("potions");
    let mut a = l.student("Ada");
    let mut b = l.student("Bea");
    l.enter(&mut a, 1_000, 41);
    l.enter(&mut b, 1_000, 42);
    let def = shipped().class("potions").unwrap();
    // Ada answers every beat of every round right on the beat; Bea none.
    let rounds: Vec<(u8, usize)> = def.rounds().map(|r| (r.1, r.3.len())).collect();
    for (round, n) in rounds {
        let (first, _) = Lesson::beat_ms_of("potions", round, 0);
        l.tick(first - 3_000);
        for beat in 0..n {
            let (at, _) = Lesson::beat_ms_of("potions", round, beat);
            let right = answer("potions", round, beat);
            l.pick(&a, at + 40, round, beat as u8, right.answer, 30);
        }
        let results = scored(&a.drain());
        assert_eq!(results.len(), n);
        assert!(results.iter().all(|r| r.score == 100), "{results:?}");
    }
    let results_ms = def
        .lesson
        .iter()
        .find(|s| matches!(s.action, LessonAction::Results { .. }))
        .map(|s| i64::from(s.at_secs) * 1_000)
        .unwrap();
    l.tick(results_ms);
    assert_eq!(l.s.take_ext_jobs().len(), 2, "Ada and Bea");
}

#[test]
fn reward_requests_carry_scores_answers_and_the_section_average() {
    let content = shipped();
    let sched = content.schedule.class("potions").unwrap();
    let def = content.class("potions").unwrap();
    let times = content.schedule.next(sched, 1_800_000_000_000);
    let mut s = Session::new(sched, def, times);
    let now = Instant::now();
    let n = def.beat_count();
    let mut seat =
        |cid: i64, section: u8, every: Option<u8>, state: SeatState, att: Option<i64>| {
            let mut x = s.new_seat(section, now);
            x.scores = vec![every; n];
            x.accepted = if every.is_some() { n as u8 } else { 0 };
            x.state = state;
            x.attendance = att;
            s.seats.insert(cid, x);
        };
    // Section 0: 100 and 50 (average 75); section 1: 20 alone; one who left
    // (scored 100) and one still being admitted count for nothing.
    seat(1, 0, Some(100), SeatState::Present, Some(11));
    seat(2, 0, Some(50), SeatState::Present, Some(12));
    seat(3, 1, Some(20), SeatState::Present, Some(13));
    seat(4, 0, Some(100), SeatState::Away, Some(14));
    seat(5, 0, None, SeatState::Admitting, None);
    let reqs = reward_requests(&s);
    let got: Vec<(i64, i64, u8, u8, u8)> = reqs
        .iter()
        .map(|r| {
            (
                r.character_id,
                r.attendance_id,
                r.score_pct,
                r.answers,
                r.section_pct,
            )
        })
        .collect();
    let n = n as u8;
    assert_eq!(
        got,
        vec![(1, 11, 100, n, 75), (2, 12, 50, n, 75), (3, 13, 20, n, 20)]
    );
    assert!(reqs.iter().all(|r| r.class_id == "potions"));
}

#[test]
fn a_fortune_reward_runs_its_effect_and_crowns_update_the_wallet() {
    let mut l = Lesson::of("potions");
    let mut a = l.student("Ada");
    l.enter(&mut a, 1_000, 51);
    a.drain();
    let rewards = ClassRewards {
        xp: 80,
        crowns: 15,
        fortune: Some(protocol::FortuneKind::KeenMind),
        ..ClassRewards::default()
    };
    let done = ClassDone::Rewarded {
        class: "potions".into(),
        session: l.times.key.id(),
        character_id: a.cid,
        score_pct: 50,
        result: Some(RewardOutcome::Granted(super::super::Rewarded {
            rewards: rewards.clone(),
            grants: vec![grant(XpSource::ClassAttend, 80, 80, 1, 1)],
            learned: vec![],
            loadout: vec![],
            crowns_balance: Some(115),
            skill_total: 0,
        })),
    };
    l.s.ext_done(ExtDone::Class(done), l.at(410_100));
    let msgs = a.drain();
    assert!(msgs.iter().any(|m| matches!(
        m,
        ServerMsg::ClassResult { rewards: r, .. } if r == &rewards
    )));
    assert!(msgs.contains(&ServerMsg::Wallet { crowns: 115 }));
    let own = msgs
        .iter()
        .find_map(|m| match m {
            ServerMsg::OwnEffects { effects, .. } => Some(effects.clone()),
            _ => None,
        })
        .expect("OwnEffects");
    assert_eq!(own.len(), 1);
    assert_eq!(own[0].kind, protocol::EffectKind::Fortune);
    assert_eq!(own[0].fortune, Some(protocol::FortuneKind::KeenMind));
    let secs = shipped()
        .economy
        .effects
        .duration_secs(protocol::EffectKind::Fortune);
    assert!(own[0].remaining_secs <= secs && own[0].remaining_secs + 2 >= secs);
    // Nobody else sees a fortune.
    let id = a.id;
    let visible = l.with_ctx(move |ctx| {
        ctx.effects(id)
            .iter()
            .filter(|e| e.visible.is_some())
            .count()
    });
    assert_eq!(visible, 0);
    // A retried reward (already granted) changes nothing in the world.
    let done = ClassDone::Rewarded {
        class: "potions".into(),
        session: l.times.key.id(),
        character_id: a.cid,
        score_pct: 50,
        result: Some(RewardOutcome::AlreadyRewarded(rewards.clone())),
    };
    l.s.ext_done(ExtDone::Class(done), l.at(410_200));
    let msgs = a.drain();
    assert!(
        !msgs
            .iter()
            .any(|m| matches!(m, ServerMsg::Wallet { .. } | ServerMsg::OwnEffects { .. }))
    );
}

/// A full Flying class section (30) lines up inside the practice
/// field's airspace, a broom's width apart, so every student mounts.
#[test]
fn a_full_flying_section_lines_up_where_brooms_lift() {
    let layouts = crate::realtime::layouts::InstanceLayouts::embedded();
    let world = layouts.get("class_pitch").unwrap();
    let air = layouts.air("class_pitch").unwrap();
    let params = shipped().flight.params(1, 100);
    let spawn = world.spawns()[0].pos;
    let spots: Vec<[f32; 3]> = (0..30).map(|k| super::line_up(spawn, k)).collect();
    for (k, p) in spots.iter().enumerate() {
        assert!(world.ground_at(p[0], p[2], p[1]).is_some(), "{k}: {p:?}");
        assert!(
            sim_core::flight::mount(world, air, &params, *p).is_ok(),
            "{k}: {p:?}"
        );
        for q in &spots[..k] {
            assert!((p[0] - q[0]).hypot(p[2] - q[2]) >= 1.4, "{p:?} {q:?}");
        }
    }
}
