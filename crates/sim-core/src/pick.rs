//! Pick beats: the vocabularies of `data/classes/picks.json`
//! ([`PickSets`]), the server-only answer keys of
//! `data/classes/answers/<class>.json` ([`AnswerKey`]) and the pure scorer.
//!
//! A pick beat shows a prompt (an item of the class's prompt set: an icon or
//! a short line) and 2-6 options (items of its option set). The student
//! picks one inside the round's window (`ClientMsg::PickAnswer`). The score
//! uses the trace scorer's numbers (`data/classes/gestures.json` `scoring`),
//! so a round of picks keeps the rhythm of a round of traces:
//!
//! - a wrong pick scores 0 (`Miss`, not a rejection: it was an answer);
//! - a right pick scores like a perfect shape traced at that timing
//!   ([`Gestures::combine`] of 100 and [`Gestures::timing`]): 100 within
//!   `timing_full_ms` of the beat, falling to `100 - timing_weight_pct` at
//!   the window's edge;
//! - the server checks the arrival like a trace's of no duration
//!   ([`Gestures::check_arrival`]), and that the choice is one of the
//!   beat's options (`BadChoice`).
//!
//! Which option is right is never sent: `protocol::BeatPrompt::Pick` carries
//! indices into the two sets, and the key ([`AnswerKey`]) is compiled into
//! the server only. The client previews the timing ([`preview`]) and learns
//! the verdict from `BeatScored`. The data is open source, so a script can
//! know every answer (accepted: plan risk 4); the server's timing, arrival,
//! one-answer-per-beat and daily caps bound what that gains.

use std::collections::{BTreeMap, HashSet};

use serde::{Deserialize, Serialize};

use protocol::{
    BeatPrompt, GestureResult, PICK_OPTIONS_MAX, PICK_OPTIONS_MIN, PickIndex, TraceReject,
};

use crate::catalog::Catalog;
use crate::classes::{BeatKind, PickSpec};
use crate::gesture::{Gestures, rejected};

/// The picks schema version this crate reads.
pub const PICKS_VERSION: u32 = 1;
/// The answer-key schema version this crate reads.
pub const ANSWERS_VERSION: u32 = 1;
/// Longest label (bytes): a word or a short line.
pub const PICK_LABEL_MAX: usize = 80;

#[derive(Debug, thiserror::Error)]
pub enum PickError {
    #[error("picks JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("picks are inconsistent: {0}")]
    Invalid(String),
}

/// One prompt or option.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PickItem {
    /// Stable within its set (class files and answer keys name it).
    pub key: String,
    /// Display text (the line a prompt shows, an option's caption).
    pub label: String,
    /// Icon key (`icons/<kind>/<name>`, as catalog assets): a catalog
    /// item's own icon when `item` is set, else `icons/class/<name>`.
    pub icon: String,
    /// The catalog item it stands for (Potions' ingredients), if any.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub item: Option<u16>,
}

/// A vocabulary: the prompts or options of a class.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PickSet {
    pub id: String,
    /// Indexed by `PickIndex` in `BeatPrompt::Pick` (order is the contract:
    /// append only).
    pub items: Vec<PickItem>,
}

impl PickSet {
    /// The index of `key` (what `BeatPrompt::Pick` carries).
    pub fn index(&self, key: &str) -> Option<PickIndex> {
        self.items
            .iter()
            .position(|i| i.key == key)
            .and_then(|k| PickIndex::try_from(k).ok())
    }

    pub fn get(&self, index: PickIndex) -> Option<&PickItem> {
        self.items.get(usize::from(index))
    }
}

/// `data/classes/picks.json`: every class's vocabularies.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PickSets {
    pub version: u32,
    pub sets: Vec<PickSet>,
}

impl PickSets {
    /// Parses and validates the sets on their own (see also
    /// [`PickSets::validate_with`]).
    pub fn from_json(json: &str) -> Result<PickSets, PickError> {
        let p: PickSets = serde_json::from_str(json)?;
        p.validate().map_err(PickError::Invalid)?;
        Ok(p)
    }

    pub fn set(&self, id: &str) -> Option<&PickSet> {
        self.sets.iter().find(|s| s.id == id)
    }

    fn validate(&self) -> Result<(), String> {
        if self.version != PICKS_VERSION {
            return Err(format!("unsupported picks version {}", self.version));
        }
        let mut ids = HashSet::new();
        for set in &self.sets {
            let id = &set.id;
            if !crate::clock::valid_id(id) || !ids.insert(id.as_str()) {
                return Err(format!("set {id:?}: ids are unique [a-z0-9_] ids"));
            }
            if set.items.len() < PICK_OPTIONS_MIN || set.items.len() > usize::from(u16::MAX) {
                return Err(format!("set {id}: at least {PICK_OPTIONS_MIN} items"));
            }
            let mut keys = HashSet::new();
            for item in &set.items {
                let k = &item.key;
                if !crate::clock::valid_id(k) || !keys.insert(k.as_str()) {
                    return Err(format!("set {id}: key {k:?} is not a unique id"));
                }
                if item.label.trim().is_empty() || item.label.len() > PICK_LABEL_MAX {
                    return Err(format!(
                        "set {id}: {k}: a label of 1-{PICK_LABEL_MAX} bytes"
                    ));
                }
                let icon_ok = item
                    .icon
                    .strip_prefix("icons/")
                    .and_then(|rest| rest.split_once('/'))
                    .is_some_and(|(kind, name)| {
                        crate::clock::valid_id(kind) && crate::clock::valid_id(name)
                    });
                if !icon_ok {
                    return Err(format!("set {id}: {k}: icon must be icons/<kind>/<name>"));
                }
            }
        }
        Ok(())
    }

    /// The checks against the catalog: an item a pick stands for exists and
    /// the pick shows its icon.
    pub fn validate_with(&self, catalog: &Catalog) -> Result<(), String> {
        for set in &self.sets {
            for item in &set.items {
                let Some(id) = item.item else { continue };
                let Some(def) = catalog.get(id) else {
                    return Err(format!("set {}: {}: unknown item {id}", set.id, item.key));
                };
                if def.asset != item.icon {
                    return Err(format!(
                        "set {}: {}: shows item {id}, so its icon is {:?}",
                        set.id, item.key, def.asset
                    ));
                }
            }
        }
        Ok(())
    }

    /// A pick beat of a class with vocabularies `spec`, resolved for the
    /// wire and the server: the `BeatPrompt` (indices) and the position of
    /// the right option in `options` (from `key`). `None` for a trace beat
    /// or anything that does not resolve (a validated class always does).
    pub fn resolve(
        &self,
        spec: &PickSpec,
        key: &AnswerKey,
        beat: &BeatKind,
    ) -> Option<(BeatPrompt, PickBeat)> {
        let BeatKind::Pick { prompt, options } = beat else {
            return None;
        };
        let prompts = self.set(&spec.prompts)?;
        let set = self.set(&spec.options)?;
        let answer = key.answer(prompt)?;
        let indices = options
            .iter()
            .map(|o| set.index(o))
            .collect::<Option<Vec<_>>>()?;
        let right = options.iter().position(|o| o == answer)?;
        Some((
            BeatPrompt::Pick {
                prompt: prompts.index(prompt)?,
                options: indices,
            },
            PickBeat {
                options: u8::try_from(options.len()).ok()?,
                answer: u8::try_from(right).ok()?,
            },
        ))
    }
}

/// `data/classes/answers/<class>.json`: for each prompt key of the class's
/// prompt set, the key of the right option. Server only (the client bundle
/// never includes it).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnswerKey {
    pub version: u32,
    /// The class it answers for.
    pub class: String,
    pub answers: BTreeMap<String, String>,
}

impl AnswerKey {
    pub fn from_json(json: &str) -> Result<AnswerKey, PickError> {
        let k: AnswerKey = serde_json::from_str(json)?;
        if k.version != ANSWERS_VERSION {
            return Err(PickError::Invalid(format!(
                "unsupported answers version {}",
                k.version
            )));
        }
        if !crate::clock::valid_id(&k.class) || k.answers.is_empty() {
            return Err(PickError::Invalid(format!(
                "answers {:?}: a class id and at least one answer",
                k.class
            )));
        }
        Ok(k)
    }

    /// The key of the right option for prompt `prompt`.
    pub fn answer(&self, prompt: &str) -> Option<&str> {
        self.answers.get(prompt).map(String::as_str)
    }

    /// Every prompt is in the prompt set and every answer in the option set.
    pub fn validate_with(&self, prompts: &PickSet, options: &PickSet) -> Result<(), String> {
        for (p, a) in &self.answers {
            if prompts.index(p).is_none() || options.index(a).is_none() {
                return Err(format!(
                    "answers {}: {p:?} -> {a:?} is not in sets {} and {}",
                    self.class, prompts.id, options.id
                ));
            }
        }
        Ok(())
    }
}

/// A pick beat as the server holds it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PickBeat {
    /// How many options were shown (2..=`PICK_OPTIONS_MAX`).
    pub options: u8,
    /// The position of the right one.
    pub answer: u8,
}

/// The score of a pick: a wrong one 0 (`Miss`), a right one by its timing
/// (`offset_ms` from the beat, in a round whose window is `window_ms`).
pub fn score(gestures: &Gestures, correct: bool, window_ms: u16, offset_ms: i32) -> GestureResult {
    if correct {
        gestures.combine(100, gestures.timing(window_ms, offset_ms))
    } else {
        GestureResult {
            score: 0,
            grade: gestures.grade(0),
            rejected: None,
        }
    }
}

/// The client's preview: what a pick at `offset_ms` scores if it is right.
pub fn preview(gestures: &Gestures, window_ms: u16, offset_ms: i32) -> GestureResult {
    score(gestures, true, window_ms, offset_ms)
}

/// The server's arrival check for a pick claiming `offset_ms` from a beat
/// at `beat_at_ms`, received at `received_ms`: inside the window, and not
/// before it was made (less the clock error) nor long after.
pub fn check_arrival(
    gestures: &Gestures,
    beat_at_ms: i64,
    window_ms: u16,
    offset_ms: i16,
    received_ms: i64,
) -> Result<(), TraceReject> {
    gestures.check_arrival(beat_at_ms, window_ms, offset_ms, 0, received_ms)
}

/// The server's verdict on one pick after its session checks (in a
/// section, rate limit, an open pick beat): the choice is one of the
/// options (`BadChoice`), it arrived in time (`BadTiming`), then
/// [`score`].
pub fn judge(
    gestures: &Gestures,
    beat: PickBeat,
    beat_at_ms: i64,
    window_ms: u16,
    choice: u8,
    offset_ms: i16,
    received_ms: i64,
) -> GestureResult {
    if choice >= beat.options || usize::from(choice) >= PICK_OPTIONS_MAX {
        return rejected(TraceReject::BadChoice);
    }
    if let Err(r) = check_arrival(gestures, beat_at_ms, window_ms, offset_ms, received_ms) {
        return rejected(r);
    }
    score(
        gestures,
        choice == beat.answer,
        window_ms,
        i32::from(offset_ms),
    )
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::gesture::tests::shipped as gestures;
    use protocol::Grade;

    pub const SHIPPED: &str = include_str!("../../../data/classes/picks.json");

    pub fn shipped() -> PickSets {
        PickSets::from_json(SHIPPED).expect("the shipped picks are valid")
    }

    #[test]
    fn right_picks_score_by_timing_and_wrong_ones_miss() {
        let g = gestures();
        let full = i32::from(g.scoring.timing_full_ms);
        let on_beat = score(&g, true, 1_200, 0);
        assert_eq!((on_beat.score, on_beat.grade), (100, Grade::Perfect));
        assert_eq!(score(&g, true, 1_200, -full), on_beat);
        // At the window's edge a right pick keeps the share that does not
        // ride on timing.
        let edge = score(&g, true, 1_200, 1_200);
        assert_eq!(edge.score, 100 - g.scoring.timing_weight_pct);
        let mid = score(&g, true, 1_200, 600);
        assert!(edge.score < mid.score && mid.score < 100, "{mid:?}");
        let wrong = score(&g, false, 1_200, 0);
        assert_eq!(
            (wrong.score, wrong.grade, wrong.rejected),
            (0, Grade::Miss, None)
        );
        assert_eq!(preview(&g, 1_200, 600), mid);
    }

    #[test]
    fn the_server_checks_the_choice_and_the_arrival() {
        let g = gestures();
        let beat = PickBeat {
            options: 4,
            answer: 2,
        };
        let at = 1_790_000_000_000;
        let judge =
            |choice, offset: i16, received| judge(&g, beat, at, 1_200, choice, offset, received);
        // Right, on the beat, arriving a little later: full marks.
        assert_eq!(judge(2, 0, at + 80).score, 100);
        // Wrong: an answer, scored 0.
        let wrong = judge(1, 0, at + 80);
        assert_eq!((wrong.score, wrong.rejected), (0, None));
        // Not an option.
        assert_eq!(judge(4, 0, at + 80).rejected, Some(TraceReject::BadChoice));
        assert_eq!(judge(9, 0, at + 80).rejected, Some(TraceReject::BadChoice));
        // Outside the window, before it was made, or far too late.
        assert_eq!(
            judge(2, 1_500, at + 1_600).rejected,
            Some(TraceReject::BadTiming)
        );
        let s = &g.scoring;
        let early = at + 300 - i64::from(s.arrival_early_ms) - 1;
        assert_eq!(judge(2, 300, early).rejected, Some(TraceReject::BadTiming));
        let late = at + 300 + i64::from(s.arrival_late_ms) + 1;
        assert_eq!(judge(2, 300, late).rejected, Some(TraceReject::BadTiming));
        // An early pick inside the window counts.
        assert_eq!(judge(2, -200, at - 150).rejected, None);
    }

    #[test]
    fn beats_resolve_to_indices_without_the_answer() {
        let sets = shipped();
        let spec = PickSpec {
            prompts: "transfiguration_puzzles".into(),
            options: "transfiguration_changes".into(),
        };
        let key = AnswerKey::from_json(include_str!(
            "../../../data/classes/answers/transfiguration.json"
        ))
        .unwrap();
        let (prompt_key, answer) = key.answers.iter().next().unwrap();
        let options_set = sets.set(&spec.options).unwrap();
        let wrong = options_set
            .items
            .iter()
            .find(|i| i.key != *answer)
            .unwrap()
            .key
            .clone();
        let beat = BeatKind::Pick {
            prompt: prompt_key.clone(),
            options: vec![wrong.clone(), answer.clone()],
        };
        let (prompt, held) = sets.resolve(&spec, &key, &beat).unwrap();
        assert_eq!(
            held,
            PickBeat {
                options: 2,
                answer: 1
            }
        );
        let BeatPrompt::Pick { prompt, options } = prompt else {
            panic!("a pick");
        };
        let prompts = sets.set(&spec.prompts).unwrap();
        assert_eq!(prompts.get(prompt).unwrap().key, *prompt_key);
        assert_eq!(options_set.get(options[1]).unwrap().key, *answer);
        // A trace beat or an answer that is not offered resolves to nothing.
        let trace = BeatKind::Trace {
            gesture: "circle".into(),
        };
        assert!(sets.resolve(&spec, &key, &trace).is_none());
        let missing = BeatKind::Pick {
            prompt: prompt_key.clone(),
            options: vec![wrong.clone(), wrong],
        };
        assert!(sets.resolve(&spec, &key, &missing).is_none());
    }

    #[test]
    fn bad_vocabularies_are_refused() {
        let v: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        let err =
            |v: &serde_json::Value| PickSets::from_json(&v.to_string()).unwrap_err().to_string();
        let mut dup = v.clone();
        let first = dup["sets"][0]["items"][0].clone();
        dup["sets"][0]["items"].as_array_mut().unwrap().push(first);
        assert!(err(&dup).contains("unique"));
        let mut icon = v.clone();
        icon["sets"][0]["items"][0]["icon"] = "potion.png".into();
        assert!(err(&icon).contains("icon"));
        let mut label = v;
        label["sets"][0]["items"][0]["label"] = "".into();
        assert!(err(&label).contains("label"));
    }
}
