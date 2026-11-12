//! Class definitions (v2 ): `data/classes/<id>.json`
//! ([`ClassDef`]).
//!
//! A class is taught in its own instance (`ClassSchedule::layout`) by a
//! professor: a server-side scripted NPC ([`NpcDef`]) drawn with the
//! ordinary character system, wearing existing catalog items. The lesson is
//! data: a list of [`LessonStep`]s at real seconds from the session's start.
//! Lines are spoken (`Say`), gestures or the coming task demonstrated
//! (`Demonstrate`), practice rounds of on-beat prompts run (`Practice`), and
//! results given (`Results`). The phases students see
//! (`protocol::LessonPhase`) follow from the steps ([`ClassDef::phase_at`]):
//! gathering until the first demonstration (which comes after the join
//! window closes, so everyone admitted sees it), then demonstrations and
//! practice rounds, then results until the session ends.
//!
//! **Beats (v2).** A beat is traced ([`BeatKind::Trace`]: a wand gesture,
//! scored by `crate::gesture`) or picked ([`BeatKind::Pick`]: a prompt and
//! 2-6 options from the class's vocabularies, [`PickSpec`] naming two sets
//! of `data/classes/picks.json`, scored by `crate::pick`). Which option is
//! right lives in the server-only answer key
//! (`data/classes/answers/<class>.json`, [`crate::pick::AnswerKey`]), never
//! in this file, which the client bundles.
//!
//! **Rewards (v2, [`ClassRewardRules`]).** Skill XP in the class's
//! [`Subject`], house points, and Crowns, items, recipes taught at skill
//! thresholds, a lore entry and hint, and a fortune, as the class's data
//! asks. Player XP comes from `data/progression.json` (daily `class` cap).
//!
//! Display text, including the professor's name, lives here (IP names in
//! data only, CONTRIBUTING.md).

use std::collections::HashSet;

use serde::{Deserialize, Serialize};

use protocol::{Appearance, FortuneKind, House, LESSON_BEATS_MAX, LessonPhase};

use crate::catalog::{Catalog, ItemKind};
use crate::clock::ClassSchedule;
use crate::crafting::{Discipline, Recipes};
use crate::gesture::Gestures;
use crate::pick::{AnswerKey, PickSets};
use crate::spells::Spells;

/// The class schema version this crate reads (2: the beats and
/// rewards).
pub const CLASS_VERSION: u32 = 2;
/// The most Crowns one class may pay (attending plus the whole bonus).
pub const CLASS_CROWNS_MAX: u32 = 100;
/// How long after a trace beat the client's pad keeps its gesture up, so a
/// stroke begun on the beat can be finished (ms; client `TRACE_HOLD_MS`).
pub const TRACE_HOLD_MS: u32 = 3_000;
/// How long before a beat the client's approach ring starts closing, the
/// next gesture coming up on the pad with it (ms; client `APPROACH_MS`).
pub const TRACE_APPROACH_MS: u32 = 1_600;
/// The least time between two trace beats: the first's hold, then the
/// second's approach (docs/architecture.md, "Classes and progression").
pub const TRACE_BEAT_GAP_MIN_MS: u32 = TRACE_HOLD_MS + TRACE_APPROACH_MS;

/// A school subject: each class teaches one and its skill XP goes to that
/// track (`skills.discipline` in the database, `GET /api/skills`). The
/// three crafting [`Discipline`]s keep their ids, so crafting is unchanged.
/// Ids are forever. Display names are data (`schedule.json` `subjects`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Subject {
    Charms,
    Transfiguration,
    Potions,
    /// Defensive Magic.
    Defence,
    /// History of Magic.
    History,
    Divination,
    Astronomy,
    Herbology,
    /// Creature Keeping.
    Creatures,
    /// Flying (Madam Swift's ring course); its skill level sets the
    /// broom's speed tier (`crate::flight`).
    Flying,
}

impl Subject {
    pub const ALL: [Subject; 10] = [
        Subject::Charms,
        Subject::Transfiguration,
        Subject::Potions,
        Subject::Defence,
        Subject::History,
        Subject::Divination,
        Subject::Astronomy,
        Subject::Herbology,
        Subject::Creatures,
        Subject::Flying,
    ];

    /// The id used in JSON and the database.
    pub fn id(self) -> &'static str {
        match self {
            Subject::Charms => "charms",
            Subject::Transfiguration => "transfiguration",
            Subject::Potions => "potions",
            Subject::Defence => "defence",
            Subject::History => "history",
            Subject::Divination => "divination",
            Subject::Astronomy => "astronomy",
            Subject::Herbology => "herbology",
            Subject::Creatures => "creatures",
            Subject::Flying => "flying",
        }
    }

    pub fn from_id(id: &str) -> Option<Subject> {
        Subject::ALL.into_iter().find(|s| s.id() == id)
    }

    /// The crafting discipline this subject's skill gates, if any (the
    /// recipes of `data/crafting/recipes.json` need its level).
    pub fn discipline(self) -> Option<Discipline> {
        match self {
            Subject::Charms => Some(Discipline::Charms),
            Subject::Transfiguration => Some(Discipline::Transfiguration),
            Subject::Potions => Some(Discipline::Potions),
            _ => None,
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ClassError {
    #[error("class JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("class is inconsistent: {0}")]
    Invalid(String),
}

/// A scripted NPC.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NpcDef {
    /// Stable data id (`EntityInfo.npc`).
    pub id: String,
    /// Display name (nameplate, chat).
    pub name: String,
    /// The nameplate's house colours (lower-case house id).
    pub house: String,
    /// What it wears: catalog clothing, palette dyes (`protocol::Appearance`
    /// in its JSON shape, checked against the creator and catalog).
    pub appearance: Appearance,
    /// Where it stands in the instance, facing `yaw`.
    pub pos: [f32; 3],
    pub yaw: f32,
}

impl NpcDef {
    pub fn house(&self) -> House {
        House::from_id(&self.house).expect("validated")
    }
}

/// An NPC's emote.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NpcEmote {
    Wave,
    Bow,
}

impl NpcEmote {
    pub fn emote(self) -> protocol::Emote {
        match self {
            NpcEmote::Wave => protocol::Emote::Wave,
            NpcEmote::Bow => protocol::Emote::Bow,
        }
    }
}

/// What a beat asks for. In the file a trace beat names a `gesture`, a
/// pick beat a `prompt` and its `options` (keys of the class's prompt and
/// option sets, [`PickSpec`]; options in the order shown).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum BeatKind {
    /// Trace the gesture (key).
    Trace { gesture: String },
    /// Pick the right one of `options` for `prompt`.
    Pick {
        prompt: String,
        options: Vec<String>,
    },
    /// Fly through ring `ring` of the class's instance layout
    /// (`Layout::rings`, by index) within the round's window of the beat
    /// (`crate::ring`).
    Ring { ring: u8 },
}

/// One prompt of a practice round.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BeatDef {
    /// Milliseconds after the round's step.
    pub at_ms: u32,
    #[serde(flatten)]
    pub kind: BeatKind,
}

impl BeatDef {
    /// The gesture key of a trace beat.
    pub fn gesture(&self) -> Option<&str> {
        match &self.kind {
            BeatKind::Trace { gesture } => Some(gesture),
            BeatKind::Pick { .. } | BeatKind::Ring { .. } => None,
        }
    }
}

/// The vocabularies a class with pick beats uses: two set ids of
/// `data/classes/picks.json` (they may be the same set, as in Potions,
/// where the prompt names the ingredient and the shelf shows them).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PickSpec {
    /// The set prompts come from (`BeatPrompt::Pick.prompt` indexes it).
    pub prompts: String,
    /// The set options come from (`BeatPrompt::Pick.options` index it).
    pub options: String,
}

/// What a lesson step does.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum LessonAction {
    /// The professor speaks.
    Say { line: String },
    /// The professor waves or bows.
    Emote { emote: NpcEmote },
    /// The professor demonstrates a gesture (with the spell's effect, if one
    /// is named) over `duration_ms`, perhaps saying `line`. Without a
    /// gesture (v2, pick classes) the professor shows the coming task: the
    /// line is said and the phase is `Demonstration` all the same.
    Demonstrate {
        #[serde(default)]
        gesture: Option<String>,
        #[serde(default)]
        spell: Option<String>,
        duration_ms: u16,
        #[serde(default)]
        line: Option<String>,
    },
    /// Practice round `round` (numbered from 1): students trace each beat
    /// starting, or pick for it, within `window_ms` of it.
    Practice {
        round: u8,
        window_ms: u16,
        beats: Vec<BeatDef>,
    },
    /// Scores and rewards, perhaps with a closing `line`.
    Results {
        #[serde(default)]
        line: Option<String>,
    },
}

/// A step of the lesson script.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LessonStep {
    /// Real seconds after the session starts.
    pub at_secs: u32,
    #[serde(flatten)]
    pub action: LessonAction,
}

/// House points for a score of at least `min_pct`.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct PointsTier {
    pub min_pct: u8,
    pub points: u16,
}

/// Crowns for a class (ledger reason `class`).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CrownReward {
    /// For taking part...
    pub attend: u32,
    /// ...plus up to this much, scaled by the score.
    pub bonus_max: u32,
}

/// An item a class gives (ledger reason `class`, merged into stacks): none
/// below `min_pct`, then from `quantity[0]` at `min_pct` rising evenly to
/// `quantity[1]` at 100 %, rounded to the nearest unit (halves up:
/// "1-2 units" gives 2 from halfway between `min_pct` and 100 %).
///
/// An item with `draw` is not given outright. The class's `draw`
/// items whose `min_pct` the score reaches form a pool, and one of them is
/// drawn with the server's random roll ([`ClassRewardRules::items`], like a
/// fortune), so a class can be a second source of several rare found-only
/// materials without giving every one of them to every good student.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ItemReward {
    /// Catalog id (a stackable material, dye, finish, potion or pattern:
    /// Astronomy draws its Star Charts).
    pub item: u16,
    pub quantity: [u32; 2],
    #[serde(default)]
    pub min_pct: u8,
    /// One of the reached `draw` items is given.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub draw: bool,
}

impl ItemReward {
    /// How many a score of `score_pct` earns (0 below `min_pct`).
    pub fn quantity(&self, score_pct: u8) -> u32 {
        let score = score_pct.min(100);
        if score < self.min_pct {
            return 0;
        }
        let [lo, hi] = self.quantity;
        let span = u32::from(100 - self.min_pct);
        if span == 0 {
            return hi;
        }
        let rise = hi.saturating_sub(lo) * u32::from(score - self.min_pct);
        lo + (2 * rise + span) / (2 * span)
    }
}

/// A recipe the class teaches (a `known_recipes` row) once the student's
/// skill in the class's subject reaches `skill` (checked at the class's
/// results, after its skill XP; in a class only).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct TeachRule {
    /// `data/crafting/recipes.json` id: a pattern-gated recipe of the
    /// subject's discipline.
    pub recipe: u16,
    /// Skill level needed (at least the recipe's own).
    pub skill: u8,
}

/// A fortune a score of at least `min_pct` may draw.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct FortuneRule {
    pub kind: FortuneKind,
    #[serde(default)]
    pub min_pct: u8,
}

/// What a class gives besides player XP (`data/progression.json`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClassRewardRules {
    /// Skill XP in the class's subject for attending...
    pub attend_skill_xp: u32,
    /// ...plus up to this much, scaled by the score.
    pub bonus_skill_xp_max: u32,
    /// Fewest accepted answers (traces and picks; a wrong pick counts, a
    /// rejected one does not) that count as taking part (no rewards below).
    pub min_answers: u8,
    /// House points by score, highest tier that applies (rising `min_pct`).
    pub house_points: Vec<PointsTier>,
    // ---- v2; each defaults to nothing.
    #[serde(default)]
    pub crowns: CrownReward,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub items: Vec<ItemReward>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub teaches: Vec<TeachRule>,
    /// Unlock the student's next lore entry (`data/classes/lore/lore.json`) and
    /// give a hint naming where a find spot they have not searched today is.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub lore: bool,
    /// One fortune drawn among those the score reaches (`fortune_for`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub fortune: Vec<FortuneRule>,
    /// Extra skill XP for everyone rewarded, scaled by the section's average
    /// score (Creature Keeping' group bonus; 0 = none).
    #[serde(default)]
    pub section_bonus_skill_xp_max: u32,
}

impl ClassRewardRules {
    /// House points for a score of `score_pct`.
    pub fn house_points(&self, score_pct: u8) -> u16 {
        self.house_points
            .iter()
            .rev()
            .find(|t| t.min_pct <= score_pct)
            .map_or(0, |t| t.points)
    }

    /// Skill XP for a score of `score_pct`.
    pub fn skill_xp(&self, score_pct: u8) -> u32 {
        self.attend_skill_xp + scaled(self.bonus_skill_xp_max, score_pct)
    }

    /// The group bonus's skill XP for a section averaging `section_pct`.
    pub fn section_bonus(&self, section_pct: u8) -> u32 {
        scaled(self.section_bonus_skill_xp_max, section_pct)
    }

    /// Crowns for a score of `score_pct`.
    pub fn crowns(&self, score_pct: u8) -> u32 {
        self.crowns.attend + scaled(self.crowns.bonus_max, score_pct)
    }

    /// The items a score of `score_pct` earns, (catalog id, quantity): every
    /// item without `draw` in file order, then one of the `draw` items the
    /// score reaches, chosen by the server's random `roll` (modulo how many
    /// are reached). None with a quantity of 0.
    pub fn items(&self, score_pct: u8, roll: u32) -> Vec<(u16, u32)> {
        let mut out: Vec<(u16, u32)> = self
            .items
            .iter()
            .filter(|r| !r.draw)
            .map(|r| (r.item, r.quantity(score_pct)))
            .filter(|&(_, q)| q > 0)
            .collect();
        let pool: Vec<(u16, u32)> = self
            .items
            .iter()
            .filter(|r| r.draw)
            .map(|r| (r.item, r.quantity(score_pct)))
            .filter(|&(_, q)| q > 0)
            .collect();
        if !pool.is_empty() {
            out.push(pool[roll as usize % pool.len()]);
        }
        out
    }

    /// The recipes taught at subject skill level `skill_level`.
    pub fn teaches_at(&self, skill_level: u8) -> impl Iterator<Item = u16> + '_ {
        self.teaches
            .iter()
            .filter(move |t| t.skill <= skill_level)
            .map(|t| t.recipe)
    }

    /// The fortune a score of `score_pct` draws with the server's random
    /// `roll`: one of those whose `min_pct` it reaches (`roll` modulo how
    /// many), or none.
    pub fn fortune_for(&self, score_pct: u8, roll: u32) -> Option<FortuneKind> {
        let reached: Vec<FortuneKind> = self
            .fortune
            .iter()
            .filter(|f| f.min_pct <= score_pct.min(100))
            .map(|f| f.kind)
            .collect();
        (!reached.is_empty()).then(|| reached[roll as usize % reached.len()])
    }
}

/// `max` scaled by a score (0-100, clamped), rounded down.
fn scaled(max: u32, score_pct: u8) -> u32 {
    max.saturating_mul(u32::from(score_pct.min(100))) / 100
}

/// One invented lore entry (`data/classes/lore/lore.json`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LoreEntry {
    /// Stable id (`lore_unlocks.lore_id`, `ClassRewards.lore`), never reused.
    pub id: u16,
    pub title: String,
    pub text: String,
}

/// `data/classes/lore/lore.json`: the entries a `lore` class unlocks, one per
/// class attended, in id order. Server only (a subdirectory, outside the
/// client's `data/classes/*.json` glob): `GET /api/lore` serves the text.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Lore {
    pub version: u32,
    pub entries: Vec<LoreEntry>,
}

/// The lore schema version this crate reads.
pub const LORE_VERSION: u32 = 1;

impl Lore {
    pub fn from_json(json: &str) -> Result<Lore, ClassError> {
        let l: Lore = serde_json::from_str(json)?;
        if l.version != LORE_VERSION {
            return Err(ClassError::Invalid(format!(
                "lore: unsupported version {}",
                l.version
            )));
        }
        if l.entries.windows(2).any(|w| w[0].id >= w[1].id) {
            return Err(ClassError::Invalid("lore ids must rise".into()));
        }
        if let Some(e) = l.entries.iter().find(|e| {
            e.title.trim().is_empty()
                || e.text.trim().is_empty()
                || e.title.len() > 80
                || e.text.len() > 2_000
        }) {
            return Err(ClassError::Invalid(format!(
                "lore {}: a title (at most 80 bytes) and a text (at most 2,000)",
                e.id
            )));
        }
        Ok(l)
    }

    pub fn get(&self, id: u16) -> Option<&LoreEntry> {
        self.entries.iter().find(|e| e.id == id)
    }

    /// The first entry not in `unlocked` (the next one a class unlocks).
    pub fn next(&self, unlocked: &HashSet<u16>) -> Option<&LoreEntry> {
        self.entries.iter().find(|e| !unlocked.contains(&e.id))
    }
}

/// The content a class is checked against ([`ClassDef::validate_with`]).
#[derive(Debug, Clone, Copy)]
pub struct ClassContext<'a> {
    pub schedule: &'a ClassSchedule,
    pub gestures: &'a Gestures,
    pub spells: &'a Spells,
    pub picks: &'a PickSets,
    /// The class's answer key (required when it has pick beats).
    pub answers: Option<&'a AnswerKey>,
    pub catalog: &'a Catalog,
    pub recipes: &'a Recipes,
    pub lore: &'a Lore,
}

/// `data/classes/<id>.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClassDef {
    pub version: u32,
    /// Matches its `data/classes/schedule.json` entry.
    pub id: String,
    /// The skill track its XP goes to.
    pub subject: Subject,
    pub professor: NpcDef,
    /// The vocabularies of its pick beats (required when it has any).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub picks: Option<PickSpec>,
    pub lesson: Vec<LessonStep>,
    pub rewards: ClassRewardRules,
}

impl ClassDef {
    /// Parses a class and checks it on its own (see also
    /// [`ClassDef::validate_with`], which the server runs with the rest of
    /// the content).
    pub fn from_json(json: &str) -> Result<ClassDef, ClassError> {
        let c: ClassDef = serde_json::from_str(json)?;
        c.validate().map_err(ClassError::Invalid)?;
        Ok(c)
    }

    /// The practice rounds, in order.
    pub fn rounds(&self) -> impl Iterator<Item = (&LessonStep, u8, u16, &[BeatDef])> {
        self.lesson.iter().filter_map(|s| match &s.action {
            LessonAction::Practice {
                round,
                window_ms,
                beats,
            } => Some((s, *round, *window_ms, beats.as_slice())),
            _ => None,
        })
    }

    /// Beats in all practice rounds (the denominator of the score).
    pub fn beat_count(&self) -> usize {
        self.rounds().map(|(_, _, _, b)| b.len()).sum()
    }

    /// Whether any beat is a pick.
    pub fn has_picks(&self) -> bool {
        self.rounds()
            .flat_map(|(_, _, _, b)| b)
            .any(|b| matches!(b.kind, BeatKind::Pick { .. }))
    }

    /// Whether any beat is a ring (the class flies: students are
    /// mounted in its instance).
    pub fn has_rings(&self) -> bool {
        self.rounds()
            .flat_map(|(_, _, _, b)| b)
            .any(|b| matches!(b.kind, BeatKind::Ring { .. }))
    }

    /// The checks against the class's instance layout: a class
    /// with ring beats is all rings, teaches Flying, and names only rings the
    /// layout has, in an airspace; a Flying class has rings.
    pub fn check_rings(&self, layout: &crate::layout::Layout) -> Result<(), String> {
        let id = &self.id;
        let beats: Vec<&BeatDef> = self.rounds().flat_map(|(_, _, _, b)| b).collect();
        let rings = beats
            .iter()
            .filter(|b| matches!(b.kind, BeatKind::Ring { .. }))
            .count();
        if (self.subject == Subject::Flying) != (rings > 0) {
            return Err(format!(
                "{id}: a Flying class flies rings, and only it does"
            ));
        }
        if rings > 0 && rings != beats.len() {
            return Err(format!("{id}: ring beats do not mix with others"));
        }
        for b in beats {
            if let BeatKind::Ring { ring } = b.kind
                && usize::from(ring) >= layout.rings.len()
            {
                return Err(format!(
                    "{id}: ring {ring} is not in the layout ({} rings)",
                    layout.rings.len()
                ));
            }
        }
        Ok(())
    }

    /// The lesson's phases as (start, phase), in real ms from the session
    /// start; the last runs until the session ends.
    pub fn phases(&self) -> Vec<(u32, LessonPhase)> {
        let mut out = vec![(0, LessonPhase::Gathering)];
        for s in &self.lesson {
            let phase = match &s.action {
                LessonAction::Demonstrate { .. } => LessonPhase::Demonstration,
                LessonAction::Practice { round, .. } => LessonPhase::Practice { round: *round },
                LessonAction::Results { .. } => LessonPhase::Results,
                LessonAction::Say { .. } | LessonAction::Emote { .. } => continue,
            };
            let at = s.at_secs * 1_000;
            if out.last().is_some_and(|&(_, p)| p == phase) {
                continue;
            }
            out.push((at, phase));
        }
        out
    }

    /// The phase at `elapsed_ms` into a session lasting `duration_ms`, and
    /// when it ends (ms from the start).
    pub fn phase_at(&self, elapsed_ms: u32, duration_ms: u32) -> (LessonPhase, u32) {
        if elapsed_ms >= duration_ms {
            return (LessonPhase::Ended, duration_ms);
        }
        let phases = self.phases();
        let k = phases
            .iter()
            .rposition(|&(at, _)| at <= elapsed_ms)
            .unwrap_or(0);
        let until = phases.get(k + 1).map_or(duration_ms, |&(at, _)| at);
        (phases[k].1, until)
    }

    fn validate(&self) -> Result<(), String> {
        if self.version != CLASS_VERSION {
            return Err(format!(
                "unsupported version {} (expected {CLASS_VERSION})",
                self.version
            ));
        }
        let id = &self.id;
        if !crate::clock::valid_id(id) || !crate::clock::valid_id(&self.professor.id) {
            return Err(format!("class {id:?}: ids must be [a-z0-9_]"));
        }
        let p = &self.professor;
        if p.name.is_empty() || House::from_id(&p.house).is_none() {
            return Err(format!("{id}: the professor needs a name and a house"));
        }
        if !(p.pos.iter().all(|v| v.is_finite()) && p.yaw.is_finite()) {
            return Err(format!("{id}: the professor's place must be finite"));
        }
        if self.lesson.windows(2).any(|w| w[0].at_secs > w[1].at_secs) {
            return Err(format!("{id}: lesson steps must be in time order"));
        }
        let mut next_round = 1;
        let mut results = false;
        for (k, s) in self.lesson.iter().enumerate() {
            if results
                && !matches!(
                    s.action,
                    LessonAction::Say { .. } | LessonAction::Emote { .. }
                )
            {
                return Err(format!("{id}: only speech and emotes follow the results"));
            }
            match &s.action {
                LessonAction::Say { line } if line.is_empty() || line.len() > 256 => {
                    return Err(format!("{id}: lines are 1-256 bytes"));
                }
                LessonAction::Practice {
                    round,
                    window_ms,
                    beats,
                } => {
                    if *round != next_round {
                        return Err(format!("{id}: practice rounds are numbered 1, 2, ..."));
                    }
                    next_round += 1;
                    if beats.is_empty() || beats.len() > LESSON_BEATS_MAX || *window_ms < 100 {
                        return Err(format!("{id}: round {round} needs 1-32 beats and a window"));
                    }
                    for b in beats {
                        if let BeatKind::Pick { options, .. } = &b.kind {
                            let distinct: HashSet<&String> = options.iter().collect();
                            if !(protocol::PICK_OPTIONS_MIN..=protocol::PICK_OPTIONS_MAX)
                                .contains(&options.len())
                                || distinct.len() != options.len()
                            {
                                return Err(format!(
                                    "{id}: round {round}: a pick offers 2-6 different options"
                                ));
                            }
                        }
                    }
                    if beats
                        .windows(2)
                        .any(|w| w[1].at_ms < w[0].at_ms + u32::from(*window_ms))
                    {
                        return Err(format!("{id}: round {round}'s beats overlap their windows"));
                    }
                    let traced = |b: &BeatDef| matches!(b.kind, BeatKind::Trace { .. });
                    if beats.windows(2).any(|w| {
                        traced(&w[0])
                            && traced(&w[1])
                            && w[1].at_ms < w[0].at_ms + TRACE_BEAT_GAP_MIN_MS
                    }) {
                        return Err(format!(
                            "{id}: round {round}'s traces need {TRACE_BEAT_GAP_MIN_MS} ms between beats"
                        ));
                    }
                    // The round's last beat (and its window, or a trace's
                    // hold on the pad) ends before the next phase step begins.
                    let last = beats.last().expect("not empty");
                    let tail = if traced(last) {
                        u32::from(*window_ms).max(TRACE_HOLD_MS)
                    } else {
                        u32::from(*window_ms)
                    };
                    let end = s.at_secs * 1_000 + last.at_ms + tail;
                    let next = self.lesson[k + 1..].iter().find(|n| {
                        !matches!(
                            n.action,
                            LessonAction::Say { .. } | LessonAction::Emote { .. }
                        )
                    });
                    if next.is_some_and(|n| n.at_secs * 1_000 < end) {
                        return Err(format!("{id}: round {round} runs into the next step"));
                    }
                }
                LessonAction::Results { .. } => results = true,
                _ => {}
            }
        }
        if next_round == 1 || !results {
            return Err(format!("{id}: a lesson needs practice and results"));
        }
        if self.has_picks() != self.picks.is_some() {
            return Err(format!(
                "{id}: `picks` names the vocabularies exactly when there are pick beats"
            ));
        }
        let r = &self.rewards;
        if r.min_answers == 0 || usize::from(r.min_answers) > self.beat_count() {
            return Err(format!("{id}: min_answers is 1 to the number of beats"));
        }
        let mut last = 0;
        for t in &r.house_points {
            if t.min_pct > 100 || t.min_pct < last || t.points == 0 {
                return Err(format!("{id}: house point tiers rise, up to 100 %"));
            }
            last = t.min_pct;
        }
        if r.crowns.attend.saturating_add(r.crowns.bonus_max) > CLASS_CROWNS_MAX {
            return Err(format!(
                "{id}: a class pays at most {CLASS_CROWNS_MAX} Crowns"
            ));
        }
        let mut items = HashSet::new();
        for i in &r.items {
            if !items.insert(i.item)
                || i.quantity[0] == 0
                || i.quantity[0] > i.quantity[1]
                || i.min_pct > 100
            {
                return Err(format!(
                    "{id}: item {}: listed once, 1 <= min <= max, min_pct <= 100",
                    i.item
                ));
            }
        }
        let mut taught = HashSet::new();
        if r.teaches.iter().any(|t| !taught.insert(t.recipe)) {
            return Err(format!("{id}: a recipe is taught once"));
        }
        let mut fortunes = HashSet::new();
        if r.fortune
            .iter()
            .any(|f| !fortunes.insert(f.kind) || f.min_pct > 100)
        {
            return Err(format!("{id}: each fortune once, min_pct <= 100"));
        }
        Ok(())
    }

    /// The checks that need the rest of the content: the class is scheduled
    /// and its script fits the session (the first demonstration after the
    /// join window, everything before the end), its gestures and spells
    /// exist, every practised gesture was demonstrated first, its pick
    /// beats use its vocabularies and its answer key gives each prompt an
    /// answer among the beat's options, and its rewards name real things
    /// (stackable items that fit a stack, pattern-gated recipes of its
    /// subject's discipline at reachable skill levels, lore to unlock).
    pub fn validate_with(&self, cx: &ClassContext<'_>) -> Result<(), String> {
        let (schedule, gestures, spells) = (cx.schedule, cx.gestures, cx.spells);
        let id = &self.id;
        if schedule.id != *id {
            return Err(format!("{id}: scheduled as {:?}", schedule.id));
        }
        if self
            .lesson
            .iter()
            .any(|s| s.at_secs >= schedule.duration_secs)
        {
            return Err(format!("{id}: a step comes after the session ends"));
        }
        // The vocabularies and the answer key.
        let sets = match &self.picks {
            None => None,
            Some(spec) => {
                let prompts = cx
                    .picks
                    .set(&spec.prompts)
                    .ok_or_else(|| format!("{id}: unknown pick set {:?}", spec.prompts))?;
                let options = cx
                    .picks
                    .set(&spec.options)
                    .ok_or_else(|| format!("{id}: unknown pick set {:?}", spec.options))?;
                let key = cx
                    .answers
                    .ok_or_else(|| format!("{id}: pick beats need an answer key"))?;
                if key.class != *id {
                    return Err(format!("{id}: the answer key is {:?}'s", key.class));
                }
                Some((prompts, options, key))
            }
        };
        let mut shown: Vec<&str> = Vec::new();
        let mut demonstrated = false;
        for s in &self.lesson {
            match &s.action {
                LessonAction::Demonstrate { gesture, spell, .. } => {
                    if s.at_secs < schedule.join_secs {
                        return Err(format!("{id}: a demonstration inside the join window"));
                    }
                    if let Some(gesture) = gesture {
                        if gestures.by_key(gesture).is_none() {
                            return Err(format!("{id}: unknown gesture {gesture:?}"));
                        }
                        shown.push(gesture);
                    }
                    if let Some(spell) = spell
                        && spells.by_key(spell).is_none()
                    {
                        return Err(format!("{id}: unknown spell {spell:?}"));
                    }
                    demonstrated = true;
                }
                LessonAction::Practice { beats, round, .. } => {
                    if !demonstrated {
                        return Err(format!("{id}: round {round} before any demonstration"));
                    }
                    for b in beats {
                        match &b.kind {
                            BeatKind::Trace { gesture } => {
                                if gestures.by_key(gesture).is_none() {
                                    return Err(format!("{id}: unknown gesture {gesture:?}"));
                                }
                                if !shown.contains(&gesture.as_str()) {
                                    return Err(format!(
                                        "{id}: round {round} practises {gesture:?} before it is shown"
                                    ));
                                }
                            }
                            BeatKind::Ring { .. } => {}
                            BeatKind::Pick { prompt, options } => {
                                let (prompts, set, key) = sets.expect("checked: picks named");
                                if prompts.index(prompt).is_none() {
                                    return Err(format!(
                                        "{id}: round {round}: unknown prompt {prompt:?}"
                                    ));
                                }
                                if let Some(o) = options.iter().find(|o| set.index(o).is_none()) {
                                    return Err(format!(
                                        "{id}: round {round}: unknown option {o:?}"
                                    ));
                                }
                                let Some(answer) = key.answer(prompt) else {
                                    return Err(format!(
                                        "{id}: the answer key has no answer for {prompt:?}"
                                    ));
                                };
                                if !options.iter().any(|o| o == answer) {
                                    return Err(format!(
                                        "{id}: round {round}: {prompt:?}'s answer {answer:?} is not offered"
                                    ));
                                }
                            }
                        }
                    }
                }
                _ => {}
            }
        }
        if let Some((prompts, set, key)) = sets {
            key.validate_with(prompts, set)?;
        }
        self.validate_rewards(cx)
    }

    fn validate_rewards(&self, cx: &ClassContext<'_>) -> Result<(), String> {
        let id = &self.id;
        let r = &self.rewards;
        for i in &r.items {
            let Some(def) = cx.catalog.get(i.item) else {
                return Err(format!("{id}: unknown reward item {}", i.item));
            };
            let stackable = matches!(
                def.kind,
                ItemKind::Material
                    | ItemKind::Dye
                    | ItemKind::Potion
                    | ItemKind::Finish
                    | ItemKind::Pattern
            );
            if !stackable || def.retired || i.quantity[1] > def.stack_limit() {
                return Err(format!(
                    "{id}: reward item {} must be a stackable material, dye, finish, potion or pattern within a stack",
                    i.item
                ));
            }
        }
        for t in &r.teaches {
            let Some(recipe) = cx.recipes.get(t.recipe) else {
                return Err(format!("{id}: teaches unknown recipe {}", t.recipe));
            };
            // A subject with no crafting track of its own
            // (Astronomy's night brews) may teach any discipline's recipes.
            if self
                .subject
                .discipline()
                .is_some_and(|d| d != recipe.discipline)
                || recipe.pattern.is_none()
                || t.skill < recipe.skill
                || t.skill > cx.recipes.max_level()
            {
                return Err(format!(
                    "{id}: recipe {}: a pattern-gated recipe of its subject, taught at its skill or above",
                    t.recipe
                ));
            }
        }
        if r.lore && cx.lore.entries.is_empty() {
            return Err(format!("{id}: unlocks lore, but lore.json has none"));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::Palette;
    use crate::clock::Schedule;

    const SHIPPED: &str = include_str!("../../../data/classes/charms.json");
    const POTIONS: &str = include_str!("../../../data/classes/potions.json");
    const TRANSFIGURATION: &str = include_str!("../../../data/classes/transfiguration.json");
    const POTIONS_KEY: &str = include_str!("../../../data/classes/answers/potions.json");
    const TRANSFIGURATION_KEY: &str =
        include_str!("../../../data/classes/answers/transfiguration.json");
    const DEFENCE: &str = include_str!("../../../data/classes/defence.json");
    const HISTORY: &str = include_str!("../../../data/classes/history.json");
    const DIVINATION: &str = include_str!("../../../data/classes/divination.json");
    const ASTRONOMY: &str = include_str!("../../../data/classes/astronomy.json");
    const HISTORY_KEY: &str = include_str!("../../../data/classes/answers/history.json");
    const DIVINATION_KEY: &str = include_str!("../../../data/classes/answers/divination.json");
    const HERBOLOGY: &str = include_str!("../../../data/classes/herbology.json");
    const CREATURES: &str = include_str!("../../../data/classes/creatures.json");
    const HERBOLOGY_KEY: &str = include_str!("../../../data/classes/answers/herbology.json");
    const CREATURES_KEY: &str = include_str!("../../../data/classes/answers/creatures.json");
    const FLYING: &str = include_str!("../../../data/classes/flying.json");
    const CLASS_PITCH: &str = include_str!("../../../data/world/instances/class_pitch.json");

    fn charms() -> ClassDef {
        ClassDef::from_json(SHIPPED).expect("the shipped Charms class is valid")
    }

    /// The content every class is checked against, as shipped.
    struct Shipped {
        schedule: Schedule,
        gestures: Gestures,
        spells: Spells,
        picks: PickSets,
        catalog: Catalog,
        recipes: Recipes,
        lore: Lore,
    }

    fn content() -> Shipped {
        let palette = Palette::from_json(include_str!("../../../data/items/palette.json")).unwrap();
        let catalog =
            Catalog::from_json(include_str!("../../../data/items/catalog.json"), &palette).unwrap();
        let recipes = Recipes::from_json(
            include_str!("../../../data/crafting/recipes.json"),
            &catalog,
        )
        .unwrap();
        let picks = crate::pick::tests::shipped();
        picks.validate_with(&catalog).unwrap();
        Shipped {
            schedule: Schedule::from_json(include_str!("../../../data/classes/schedule.json"))
                .unwrap(),
            gestures: crate::gesture::tests::shipped(),
            spells: crate::spells::tests::shipped(),
            picks,
            catalog,
            recipes,
            lore: Lore::from_json(include_str!("../../../data/classes/lore/lore.json")).unwrap(),
        }
    }

    fn check(c: &ClassDef, key: Option<&AnswerKey>, s: &Shipped) -> Result<(), String> {
        let cx = ClassContext {
            schedule: s.schedule.class(&c.id).expect("scheduled"),
            gestures: &s.gestures,
            spells: &s.spells,
            picks: &s.picks,
            answers: key,
            catalog: &s.catalog,
            recipes: &s.recipes,
            lore: &s.lore,
        };
        c.validate_with(&cx)
    }

    #[test]
    fn the_charms_lesson_fits_its_session() {
        let c = charms();
        check(&c, None, &content()).unwrap();
        assert_eq!(c.subject, Subject::Charms);
        assert!(c.beat_count() >= 20);
        assert!(!c.has_picks());
        assert_eq!(c.professor.house(), House::Skytalon);
    }

    #[test]
    fn every_shipped_class_validates() {
        let s = content();
        for (json, key) in [
            (SHIPPED, None),
            (POTIONS, Some(POTIONS_KEY)),
            (TRANSFIGURATION, Some(TRANSFIGURATION_KEY)),
            (DEFENCE, None),
            (HISTORY, Some(HISTORY_KEY)),
            (DIVINATION, Some(DIVINATION_KEY)),
            (ASTRONOMY, None),
            (HERBOLOGY, Some(HERBOLOGY_KEY)),
            (CREATURES, Some(CREATURES_KEY)),
            (FLYING, None),
        ] {
            let c = ClassDef::from_json(json).unwrap();
            let key = key.map(|k| AnswerKey::from_json(k).unwrap());
            check(&c, key.as_ref(), &s).unwrap_or_else(|e| panic!("{}: {e}", c.id));
            assert_eq!(c.has_picks(), key.is_some(), "{}", c.id);
            // 3-4 rounds, and every class pays the same Crowns.
            let rounds = c.rounds().count();
            assert!((3..=4).contains(&rounds), "{}: {rounds} rounds", c.id);
            assert_eq!(c.rewards.crowns(0), 10);
            assert_eq!(c.rewards.crowns(100), 20);
        }
    }

    #[test]
    fn potions_brews_and_teaches_the_four_pattern_brews() {
        let c = ClassDef::from_json(POTIONS).unwrap();
        assert_eq!(c.subject, Subject::Potions);
        assert_eq!(c.subject.discipline(), Some(Discipline::Potions));
        assert_eq!(Discipline::Potions.subject(), Subject::Potions);
        // Rounds are whole brews of 5-6 ingredients from a shelf of 5-6.
        for (_, round, _, beats) in c.rounds() {
            assert!((5..=6).contains(&beats.len()), "round {round}");
            for b in beats {
                let BeatKind::Pick { options, .. } = &b.kind else {
                    panic!("Potions picks");
                };
                assert!((5..=6).contains(&options.len()));
            }
        }
        let taught: Vec<u16> = c.rewards.teaches_at(20).collect();
        assert_eq!(taught.len(), 4);
        let s = content();
        for id in &taught {
            assert!(s.recipes.get(*id).unwrap().pattern.is_some());
        }
        assert_eq!(c.rewards.teaches_at(1).count(), 0);
        // 1-2 common ingredients, and a found-only one for a good score.
        assert_eq!(c.rewards.items(0, 0), vec![(3021, 1)]);
        assert_eq!(c.rewards.items(100, 7), vec![(3021, 2), (3020, 1)]);
    }

    /// What each indoor class is for.
    #[test]
    fn the_indoor_classes_teach_what_they_feed() {
        let s = content();
        let gestures_of = |c: &ClassDef| -> HashSet<String> {
            c.rounds()
                .flat_map(|(_, _, _, b)| b)
                .filter_map(|b| b.gesture().map(str::to_owned))
                .collect()
        };
        // Defence: traced drills of Scutara and the two spells it teaches.
        let defence = ClassDef::from_json(DEFENCE).unwrap();
        assert_eq!(defence.subject, Subject::Defence);
        assert!(!defence.has_picks());
        let drilled = gestures_of(&defence);
        for spell in ["scutara", "titillara", "repulsa"] {
            let gesture = &s.spells.by_key(spell).unwrap().gesture;
            assert!(drilled.contains(gesture), "{spell}");
        }
        // History: questions with long windows, and lore.
        let history = ClassDef::from_json(HISTORY).unwrap();
        assert_eq!(history.subject, Subject::History);
        assert!(history.rewards.lore && !s.lore.entries.is_empty());
        assert!(
            history
                .rounds()
                .all(|(_, _, w, _)| (6_000..=8_000).contains(&w))
        );
        // Grown from 36 so lore lasts (80-100 entries).
        assert!((80..=100).contains(&s.lore.entries.len()));
        // Divination: one of the three fortunes for taking part.
        let divination = ClassDef::from_json(DIVINATION).unwrap();
        assert_eq!(divination.subject, Subject::Divination);
        let kinds: HashSet<FortuneKind> = (0..3)
            .filter_map(|roll| divination.rewards.fortune_for(0, roll))
            .collect();
        assert_eq!(kinds.len(), 3);
        // Astronomy: night only, 4-6 invented constellations, and the night
        // brews it alone teaches.
        let astronomy = ClassDef::from_json(ASTRONOMY).unwrap();
        assert!(s.schedule.class("astronomy").unwrap().night_only);
        let charted = gestures_of(&astronomy);
        assert!((4..=6).contains(&charted.len()));
        assert!(charted.iter().all(|g| g.starts_with("const_")));
        let taught: Vec<u16> = astronomy.rewards.teaches_at(20).collect();
        assert!((2..=4).contains(&taught.len()));
        assert_eq!(astronomy.rewards.teaches_at(1).count(), 0);
        let potions = ClassDef::from_json(POTIONS).unwrap();
        for id in taught {
            let recipe = s.recipes.get(id).unwrap();
            assert_eq!(recipe.discipline, Discipline::Potions);
            assert!(recipe.pattern.is_some());
            assert!(potions.rewards.teaches_at(20).all(|r| r != id));
        }
    }

    /// The grounds classes. Herbology
    /// and Creature Keeping are pick classes on their props (the
    /// prompt key's prefix names the plant or creature the client animates);
    /// with the other classes they are a second source of every found-only
    /// material, the rare ones drawn one at a time.
    #[test]
    fn the_grounds_classes_feed_the_found_only_materials() {
        let s = content();
        let herbology = ClassDef::from_json(HERBOLOGY).unwrap();
        let creatures = ClassDef::from_json(CREATURES).unwrap();
        assert_eq!(herbology.subject, Subject::Herbology);
        assert_eq!(creatures.subject, Subject::Creatures);
        // Professor Tull stands within the male height range (at most 1.88 m: the
        // height slider's top), and both professors are adults in catalog
        // clothing (checked by the server's appearance validation).
        assert_eq!(creatures.professor.appearance.body_type, 0);
        for (c, props) in [
            (&herbology, &["vine", "root", "briar", "fern"][..]),
            (&creatures, &["puff", "lynxkit"][..]),
        ] {
            assert!(c.has_picks());
            let mut seen = HashSet::new();
            for b in c.rounds().flat_map(|(_, _, _, b)| b) {
                let BeatKind::Pick { prompt, options } = &b.kind else {
                    panic!("{}: picks only", c.id);
                };
                let prefix = prompt.split('_').next().unwrap();
                assert!(props.contains(&prefix), "{}: {prompt}", c.id);
                seen.insert(prefix);
                assert!((3..=4).contains(&options.len()));
            }
            assert_eq!(seen.len(), props.len(), "{}: every prop has beats", c.id);
            // 2-4 units for a good score, never more than five.
            let units = |score| -> u32 { c.rewards.items(score, 0).iter().map(|i| i.1).sum() };
            assert!(units(0) >= 1 && units(60) >= 2, "{}", c.id);
            assert!((3..=5).contains(&units(100)), "{}", c.id);
        }
        assert_eq!(creatures.rewards.section_bonus(100), 10);
        assert_eq!(herbology.rewards.section_bonus(100), 0);
        // One drawn rare per good score, each of the pool in turn.
        let drawn: HashSet<u16> = (0..5)
            .map(|roll| *creatures.rewards.items(100, roll).last().unwrap())
            .map(|(item, _)| item)
            .collect();
        assert_eq!(drawn.len(), 5);
        assert_eq!(creatures.rewards.items(84, 3).len(), 2);
        // Every found-only material (not sold in the shop) is some class's
        // reward (the shop keeps selling the other 18).
        let classes: Vec<ClassDef> = [
            SHIPPED,
            POTIONS,
            TRANSFIGURATION,
            DEFENCE,
            HISTORY,
            DIVINATION,
            ASTRONOMY,
            HERBOLOGY,
            CREATURES,
        ]
        .iter()
        .map(|j| ClassDef::from_json(j).unwrap())
        .collect();
        let rewarded: HashSet<u16> = classes
            .iter()
            .flat_map(|c| c.rewards.items.iter().map(|i| i.item))
            .collect();
        let found_only: Vec<u16> = s
            .catalog
            .items
            .iter()
            .filter(|i| i.kind == ItemKind::Material && !i.in_shop() && !i.retired)
            .map(|i| i.id)
            .collect();
        assert_eq!(found_only.len(), 14);
        for id in found_only {
            assert!(
                rewarded.contains(&id),
                "found-only material {id} is no class's reward"
            );
        }
    }

    /// The Flying class flies Madam Swift's ring course (decision
    /// 7a): 3-4 rounds of ring beats only, every ring of the course in each
    /// round and in order, beats a broom at the slowest tier can make, and
    /// rewards without items (skill XP, Crowns, house points).
    #[test]
    fn the_flying_class_flies_the_ring_course() {
        let c = ClassDef::from_json(FLYING).unwrap();
        let layout = crate::layout::Layout::from_json(CLASS_PITCH).unwrap();
        c.check_rings(&layout).unwrap();
        assert_eq!(c.subject, Subject::Flying);
        assert!(c.has_rings() && !c.has_picks());
        assert_eq!(c.professor.name, "Madam Petra Swift");
        assert!(c.rewards.items.is_empty() && c.rewards.teaches.is_empty());
        let rules =
            crate::flight::FlightRules::from_json(include_str!("../../../data/flying.json"))
                .unwrap();
        let slowest = rules.params(1, protocol::BROOM_PCT_NORMAL).speed;
        for (_, round, _, beats) in c.rounds() {
            let rings: Vec<u8> = beats
                .iter()
                .map(|b| match b.kind {
                    BeatKind::Ring { ring } => ring,
                    _ => panic!("round {round}: rings only"),
                })
                .collect();
            assert!(rings.len() >= layout.rings.len(), "round {round}");
            for (k, w) in rings.windows(2).enumerate() {
                assert_eq!(
                    usize::from(w[1]),
                    (usize::from(w[0]) + 1) % layout.rings.len()
                );
                let (a, b) = (
                    &layout.rings[usize::from(w[0])],
                    &layout.rings[usize::from(w[1])],
                );
                let gap = (0..3)
                    .map(|i| (a.pos[i] - b.pos[i]).powi(2))
                    .sum::<f32>()
                    .sqrt();
                let secs = (beats[k + 1].at_ms - beats[k].at_ms) as f32 / 1_000.0;
                assert!(
                    gap / secs < slowest * 0.75,
                    "round {round}: {gap} m in {secs} s"
                );
            }
        }
        // A ring the layout lacks, rings mixed with other beats, or rings in
        // a class that is not Flying are refused.
        let mut v: serde_json::Value = serde_json::from_str(FLYING).unwrap();
        let practice = v["lesson"]
            .as_array()
            .unwrap()
            .iter()
            .position(|s| s["kind"] == "practice")
            .unwrap();
        v["lesson"][practice]["beats"][0]["ring"] = 8.into();
        let far = ClassDef::from_json(&v.to_string()).unwrap();
        assert!(
            far.check_rings(&layout)
                .unwrap_err()
                .contains("not in the layout")
        );
        v["lesson"][practice]["beats"][0] =
            serde_json::json!({ "at_ms": 8000, "gesture": "twirl_and_tap" });
        let mixed = ClassDef::from_json(&v.to_string()).unwrap();
        assert!(mixed.check_rings(&layout).unwrap_err().contains("mix"));
        let charms = charms();
        charms.check_rings(&layout).unwrap();
        let mut not_flying = c.clone();
        not_flying.subject = Subject::Charms;
        assert!(not_flying.check_rings(&layout).is_err());
    }

    #[test]
    fn transfiguration_puzzles_chain_later() {
        let c = ClassDef::from_json(TRANSFIGURATION).unwrap();
        assert_eq!(c.subject, Subject::Transfiguration);
        let (_, _, _, first) = c.rounds().next().unwrap();
        for b in first {
            let BeatKind::Pick { options, .. } = &b.kind else {
                panic!("Transfiguration picks");
            };
            assert_eq!(options.len(), 3);
        }
        let chained = c
            .rounds()
            .flat_map(|(_, _, _, b)| b)
            .filter(|b| matches!(&b.kind, BeatKind::Pick { prompt, .. } if prompt.ends_with("_2")))
            .count();
        assert!(chained >= 3);
    }

    #[test]
    fn phases_follow_the_script() {
        let c = charms();
        let duration = 540_000;
        assert_eq!(c.phase_at(0, duration).0, LessonPhase::Gathering);
        let phases = c.phases();
        assert_eq!(phases[0], (0, LessonPhase::Gathering));
        assert_eq!(phases[1].1, LessonPhase::Demonstration);
        assert!(phases.contains(&(phases[2].0, LessonPhase::Practice { round: 1 })));
        let (results_at, last) = *phases.last().unwrap();
        assert_eq!(last, LessonPhase::Results);
        assert_eq!(
            c.phase_at(results_at, duration),
            (LessonPhase::Results, duration)
        );
        // Just before a phase starts it is still the one before.
        let (p, until) = c.phase_at(phases[1].0 - 1, duration);
        assert_eq!((p, until), (LessonPhase::Gathering, phases[1].0));
        assert_eq!(c.phase_at(duration, duration).0, LessonPhase::Ended);
        // A demonstration without a gesture is a demonstration too.
        let p = ClassDef::from_json(POTIONS).unwrap();
        assert_eq!(p.phases()[1].1, LessonPhase::Demonstration);
    }

    #[test]
    fn rewards_scale_with_the_score() {
        let r = charms().rewards;
        assert_eq!(r.house_points(0), 0);
        assert!(r.house_points(100) >= r.house_points(75));
        assert_eq!(r.skill_xp(0), r.attend_skill_xp);
        assert_eq!(r.skill_xp(100), r.attend_skill_xp + r.bonus_skill_xp_max);
        assert_eq!(r.section_bonus(100), 0);
        assert_eq!(r.fortune_for(100, 7), None);
        let item = ItemReward {
            item: 3007,
            quantity: [2, 4],
            min_pct: 50,
            draw: false,
        };
        assert_eq!(item.quantity(49), 0);
        assert_eq!(item.quantity(50), 2);
        assert_eq!(item.quantity(75), 3);
        assert_eq!(item.quantity(100), 4);
        assert_eq!(item.quantity(250), 4);
        let mut d = r.clone();
        d.fortune = vec![
            FortuneRule {
                kind: FortuneKind::Luck,
                min_pct: 0,
            },
            FortuneRule {
                kind: FortuneKind::KeenMind,
                min_pct: 80,
            },
        ];
        assert_eq!(d.fortune_for(50, 1), Some(FortuneKind::Luck));
        assert_eq!(d.fortune_for(90, 1), Some(FortuneKind::KeenMind));
        assert_eq!(d.fortune_for(90, 2), Some(FortuneKind::Luck));
        d.section_bonus_skill_xp_max = 20;
        assert_eq!(d.section_bonus(50), 10);
    }

    #[test]
    fn subjects_are_ten_with_stable_ids() {
        for s in Subject::ALL {
            assert_eq!(Subject::from_id(s.id()), Some(s));
            assert_eq!(serde_json::to_value(s).unwrap(), s.id());
        }
        for d in Discipline::ALL {
            assert_eq!(d.subject().id(), d.id());
            assert_eq!(d.subject().discipline(), Some(d));
        }
        assert_eq!(Subject::Astronomy.discipline(), None);
        assert_eq!(Subject::ALL.len(), 10);
        assert_eq!(Subject::from_id("flying"), Some(Subject::Flying));
        assert_eq!(Subject::Flying.discipline(), None);
    }

    #[test]
    fn bad_scripts_are_refused() {
        let v: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        let err =
            |v: &serde_json::Value| ClassDef::from_json(&v.to_string()).unwrap_err().to_string();
        let mut order = v.clone();
        order["lesson"][0]["at_secs"] = 9_999.into();
        assert!(err(&order).contains("time order"));
        let mut house = v.clone();
        house["professor"]["house"] = "no_such_house".into();
        assert!(err(&house).contains("house"));
        let mut no_results = v.clone();
        let steps = no_results["lesson"].as_array_mut().unwrap();
        steps.retain(|s| s["kind"] != "results");
        assert!(err(&no_results).contains("results"));
        let mut rich = v.clone();
        rich["rewards"]["crowns"] = serde_json::json!({ "attend": 90, "bonus_max": 90 });
        assert!(err(&rich).contains("Crowns"));
        let mut none = v.clone();
        none["rewards"]["min_answers"] = 0.into();
        assert!(err(&none).contains("min_answers"));
        // Traces need time to finish: a beat inside the one before's hold
        // and approach, or a round ending inside its last trace's hold.
        let practice = |v: &serde_json::Value| {
            v["lesson"]
                .as_array()
                .unwrap()
                .iter()
                .position(|s| s["kind"] == "practice")
                .unwrap()
        };
        let mut close = v.clone();
        let k = practice(&close);
        let first = close["lesson"][k]["beats"][0]["at_ms"].as_u64().unwrap();
        close["lesson"][k]["beats"][1]["at_ms"] =
            (first + u64::from(TRACE_BEAT_GAP_MIN_MS) - 1).into();
        assert!(err(&close).contains("ms between beats"));
        let mut short = v;
        let k = practice(&short);
        let round_at = short["lesson"][k]["at_secs"].as_u64().unwrap() * 1_000;
        let next = short["lesson"][k + 1]["at_secs"].as_u64().unwrap() * 1_000;
        let beats = short["lesson"][k]["beats"].as_array_mut().unwrap();
        let last = beats.last_mut().unwrap();
        // Room for the window, not for the hold.
        last["at_ms"] = (next - round_at - u64::from(TRACE_HOLD_MS) + 1).into();
        assert!(err(&short).contains("runs into the next step"));
    }

    #[test]
    fn tracing_rounds_leave_time_to_finish_each_trace() {
        for json in [SHIPPED, DEFENCE, ASTRONOMY] {
            let c = ClassDef::from_json(json).unwrap();
            for (_, round, window, beats) in c.rounds() {
                assert!(beats.len() >= 5, "{} round {round}: {}", c.id, beats.len());
                for w in beats.windows(2) {
                    let gap = w[1].at_ms - w[0].at_ms;
                    assert!(
                        (TRACE_BEAT_GAP_MIN_MS..=5_000).contains(&gap),
                        "{} round {round}: {gap} ms",
                        c.id
                    );
                }
                assert!(window < 1_000);
            }
        }
    }

    /// Review: the right option's place must not follow a pattern a
    /// student can press in rhythm without reading (1, 2, 3, 1, 2, 3 ...).
    #[test]
    fn right_answers_do_not_cycle() {
        for (json, key) in [
            (POTIONS, POTIONS_KEY),
            (TRANSFIGURATION, TRANSFIGURATION_KEY),
            (HISTORY, HISTORY_KEY),
            (DIVINATION, DIVINATION_KEY),
            (HERBOLOGY, HERBOLOGY_KEY),
            (CREATURES, CREATURES_KEY),
        ] {
            let c = ClassDef::from_json(json).unwrap();
            let key = AnswerKey::from_json(key).unwrap();
            let mut places = Vec::new();
            for step in &c.lesson {
                let LessonAction::Practice { beats, .. } = &step.action else {
                    continue;
                };
                for b in beats {
                    let BeatKind::Pick { prompt, options } = &b.kind else {
                        continue;
                    };
                    let answer = key.answer(prompt).unwrap();
                    let at = options.iter().position(|o| o == answer).unwrap();
                    places.push((at, options.len()));
                }
            }
            // Next place = this one + 1 (wrapping), or the same place, for
            // more than half of the class's beats in a row is a pattern.
            let steady = |next: fn(usize, usize) -> usize| {
                places
                    .windows(2)
                    .filter(|w| w[1].0 == next(w[0].0, w[1].1))
                    .count()
            };
            let half = places.len() / 2;
            assert!(steady(|a, n| (a + 1) % n) < half, "{}: places cycle", c.id);
            assert!(steady(|a, _| a) < half, "{}: places repeat", c.id);
            // And every place is used somewhere.
            let max = places.iter().map(|p| p.1).min().unwrap();
            for k in 0..max {
                assert!(
                    places.iter().any(|p| p.0 == k),
                    "{}: place {k} unused",
                    c.id
                );
            }
        }
    }

    #[test]
    fn bad_picks_are_refused() {
        let s = content();
        let v: serde_json::Value = serde_json::from_str(TRANSFIGURATION).unwrap();
        let key = AnswerKey::from_json(TRANSFIGURATION_KEY).unwrap();
        let practice = v["lesson"]
            .as_array()
            .unwrap()
            .iter()
            .position(|s| s["kind"] == "practice")
            .unwrap();
        let invalid = |v: &serde_json::Value| -> String {
            match ClassDef::from_json(&v.to_string()) {
                Err(e) => e.to_string(),
                Ok(c) => check(&c, Some(&key), &s).unwrap_err(),
            }
        };
        // The answer must be offered.
        let mut unanswerable = v.clone();
        let beat = &mut unanswerable["lesson"][practice]["beats"][0];
        let answer = key
            .answer(beat["prompt"].as_str().unwrap())
            .unwrap()
            .to_owned();
        let others: Vec<serde_json::Value> = s
            .picks
            .set("transfiguration_changes")
            .unwrap()
            .items
            .iter()
            .filter(|i| i.key != answer)
            .take(3)
            .map(|i| i.key.clone().into())
            .collect();
        beat["options"] = others.into();
        assert!(invalid(&unanswerable).contains("not offered"));
        // One option, or the same twice, is not a choice.
        let mut one = v.clone();
        one["lesson"][practice]["beats"][0]["options"] = serde_json::json!([answer]);
        assert!(invalid(&one).contains("2-6"));
        let mut twice = v.clone();
        twice["lesson"][practice]["beats"][0]["options"] = serde_json::json!([answer, answer]);
        assert!(invalid(&twice).contains("2-6"));
        // Unknown keys and a missing vocabulary.
        let mut unknown = v.clone();
        unknown["lesson"][practice]["beats"][0]["prompt"] = "a_pumpkin".into();
        assert!(invalid(&unknown).contains("prompt"));
        let mut no_spec = v.clone();
        no_spec.as_object_mut().unwrap().remove("picks");
        assert!(invalid(&no_spec).contains("vocabularies"));
        // Rewards that name nothing real.
        let mut item = v.clone();
        item["rewards"]["items"] = serde_json::json!([{ "item": 201, "quantity": [1, 1] }]);
        assert!(invalid(&item).contains("stackable"));
        let mut recipe = v.clone();
        recipe["rewards"]["teaches"] = serde_json::json!([{ "recipe": 330, "skill": 4 }]);
        assert!(invalid(&recipe).contains("pattern-gated recipe of its subject"));
        // A lore class needs entries to unlock (checked against an
        // empty file).
        let mut lore = v;
        lore["rewards"]["lore"] = true.into();
        let c = ClassDef::from_json(&lore.to_string()).unwrap();
        let empty = Shipped {
            lore: Lore::from_json(r#"{ "version": 1, "entries": [] }"#).unwrap(),
            ..content()
        };
        assert!(check(&c, Some(&key), &empty).unwrap_err().contains("lore"));
        check(&c, Some(&key), &s).unwrap();
        // Without its answer key a pick class does not load.
        let c = ClassDef::from_json(TRANSFIGURATION).unwrap();
        assert!(check(&c, None, &s).unwrap_err().contains("answer key"));
        let potions_key = AnswerKey::from_json(POTIONS_KEY).unwrap();
        assert!(
            check(&c, Some(&potions_key), &s)
                .unwrap_err()
                .contains("answer key")
        );
    }
}
