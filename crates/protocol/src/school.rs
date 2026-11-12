//! School life in realtime (protocol v5): the game clock,
//! scheduled classes and their lessons, player levels and XP, spells cast
//! outside duels, and scripted NPCs (the professor). Dueling is in
//! [`crate::dueling`]. See docs/architecture.md, "Classes and progression".
//!
//! **Clock.** Game time is a pure function of UTC (`sim_core::clock`), so the
//! client works out the period, the lighting and the class schedule itself.
//! `ServerMsg::Welcome.unix_ms` (and `Pong.server_time_ms`) give it the
//! server's clock to correct its own by.
//!
//! **Classes.** `ClientMsg::Interact` with a class door (`class_door`
//! interactable) during a session's join window takes the student into a
//! section of the class: `ZoneChanged` (`Zone::Class`), then `ClassJoined`.
//! The section's members get `LessonPhase` on every phase change and
//! `LessonRound` (the beats of a practice round) as it starts. Each beat is
//! a [`BeatPrompt`]: a gesture to trace (`TraceGesture`, compact samples,
//! scored with the shared scorer `sim_core::gesture`) or, from protocol v7,
//! a prompt and 2-6 options to pick from (`PickAnswer`, scored by
//! `sim_core::pick`; the answer key stays on the server). The server answers
//! each with `BeatScored`. At the end, `ClassResult` names the rewards.
//!
//! **Levels.** `Progress` (after `Welcome` and after every XP grant) is the
//! character's own level and XP; viewers see the level in
//! `EntityInfo.level` and `LevelChanged`. Learned spells and the dueling
//! loadout come in `Spellbook`.
//!
//! **Spells outside duels.** `CastSpell` / `EndSpell`: Lucerna lights the
//! wand (`VisibleEffect::Lucerna`, seen by everyone who has the caster in
//! view), Elevara floats a furniture piece of the dorm room the
//! caster is in (`Levitating` to the room), other spells play a harmless
//! flourish (`SpellCast` to those nearby).
//!
//! **NPCs** are entities in snapshots like characters, with
//! `EntityInfo.npc` set; their scripted speech, wand demonstrations and
//! emotes come as `NpcEvent`.

use serde::{Deserialize, Serialize};

use crate::{Emote, EntityId, QPos};

/// A spell's stable id (`data/spells.json` `id`). Like item ids, never reused.
pub type SpellId = u16;
/// A wand gesture's stable id (`data/classes/gestures.json` `id`).
pub type GestureId = u8;

/// The highest player level.
pub const LEVEL_MAX: u8 = 20;
/// Most samples in one traced gesture (`TraceGesture.points`).
pub const TRACE_POINTS_MAX: usize = 128;
/// Most spells in a dueling loadout at any level (`Spellbook.slots` is the
/// character's own number, from `data/progression.json`).
pub const LOADOUT_MAX: usize = 8;
/// Most beats in one practice round (`LessonRound.beats`).
pub const LESSON_BEATS_MAX: usize = 32;
/// Most options one pick beat offers (`BeatPrompt::Pick.options`), and so
/// the highest `PickAnswer.choice` + 1 (protocol v7).
pub const PICK_OPTIONS_MAX: usize = 6;
/// Fewest options a pick beat offers.
pub const PICK_OPTIONS_MIN: usize = 2;

/// An index into a class's pick vocabulary (`data/classes/picks.json`, the
/// set the class names for its prompts or its options; protocol v7).
pub type PickIndex = u16;

/// Where XP came from (`xp_ledger.source`, `Progress.gained`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum XpSource {
    /// Attending a class to its end.
    ClassAttend,
    /// Doing well in a class (scaled by the score).
    ClassBonus,
    /// Crafting (a share of the recipe's skill XP; daily cap).
    Craft,
    /// An exploration find (daily cap).
    Find,
    /// Duels (daily cap shared by the three).
    DuelWin,
    DuelLoss,
    DuelDraw,
    /// `POST /api/dev/grant` and operators.
    Admin,
    /// Finding a secret (once per character and secret, no daily
    /// cap; the ledger's ref_id is the secret's `num`).
    Explore,
}

impl XpSource {
    pub const ALL: [XpSource; 9] = [
        XpSource::ClassAttend,
        XpSource::ClassBonus,
        XpSource::Craft,
        XpSource::Find,
        XpSource::DuelWin,
        XpSource::DuelLoss,
        XpSource::DuelDraw,
        XpSource::Admin,
        XpSource::Explore,
    ];

    /// The id used in the database and JSON.
    pub fn id(self) -> &'static str {
        match self {
            XpSource::ClassAttend => "class_attend",
            XpSource::ClassBonus => "class_bonus",
            XpSource::Craft => "craft",
            XpSource::Find => "find",
            XpSource::DuelWin => "duel_win",
            XpSource::DuelLoss => "duel_loss",
            XpSource::DuelDraw => "duel_draw",
            XpSource::Admin => "admin",
            XpSource::Explore => "explore",
        }
    }

    pub fn from_id(id: &str) -> Option<XpSource> {
        XpSource::ALL.into_iter().find(|s| s.id() == id)
    }

    /// The daily cap this source counts against (`data/progression.json`
    /// `xp.daily_caps`), if any. Classes share the `class` group
    /// (about six full classes a UTC day; skill XP stays uncapped).
    pub fn cap_group(self) -> Option<&'static str> {
        match self {
            XpSource::Craft => Some("craft"),
            XpSource::Find => Some("find"),
            XpSource::DuelWin | XpSource::DuelLoss | XpSource::DuelDraw => Some("duel"),
            XpSource::ClassAttend | XpSource::ClassBonus => Some("class"),
            XpSource::Admin | XpSource::Explore => None,
        }
    }
}

/// A character's level and XP as its own HUD shows them.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Progress {
    /// 1..=`LEVEL_MAX`.
    pub level: u8,
    /// Total XP earned.
    pub xp: u32,
    /// Total XP at which the current level began.
    pub level_xp: u32,
    /// Total XP the next level needs; `None` at the cap.
    pub next_level_xp: Option<u32>,
}

/// An XP grant, as `Progress.gained` reports it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct XpGain {
    pub source: XpSource,
    /// XP actually added (after any daily cap).
    pub amount: u32,
    /// The daily cap cut the grant short (or to nothing).
    pub capped: bool,
}

/// How well a beat went (`BeatScored`, `ClassResult`): a traced gesture's
/// match, or a pick's timing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Grade {
    Miss,
    Good,
    Great,
    Perfect,
}

impl Grade {
    pub fn id(self) -> &'static str {
        match self {
            Grade::Miss => "miss",
            Grade::Good => "good",
            Grade::Great => "great",
            Grade::Perfect => "perfect",
        }
    }
}

/// One sample of a traced gesture. Coordinates are on the client's trace
/// pad, normalised to -100..=100 on both axes (x right, y up; the pad is
/// square, so shapes keep their proportions). `dt_ms` is the time since the
/// previous sample (0 for the first).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct TracePoint {
    pub dt_ms: u8,
    pub x: i8,
    pub y: i8,
}

/// Why a beat's answer was not scored (`GestureResult.rejected`), a trace
/// or a pick. The first six come from the shared trace scorer (the
/// client's preview gives the same answer); the rest are the server's
/// session checks. A wrong pick is not a rejection: it scores 0, `Miss`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TraceReject {
    /// Fewer samples than a gesture needs, or more than `TRACE_POINTS_MAX`.
    BadSampleCount,
    /// Shorter or longer (in time) than the gesture allows.
    BadDuration,
    /// Moved faster than a hand can.
    TooFast,
    /// Closer to the template than any hand traces: generated input.
    TooPerfect,
    /// Too small to be a gesture (a click, a tiny wiggle).
    TooSmall,
    /// A sample outside the pad (-100..=100).
    OutOfRange,
    /// The same samples as an earlier trace this session.
    Replayed,
    /// Arrived before it could have been traced, or long after the beat.
    BadTiming,
    /// This beat was already scored, or no such beat is open.
    UnknownBeat,
    /// The sender is not a student of a class in practice.
    NotInPractice,
    /// Too many traces; slow down.
    RateLimited,
    /// Protocol v7: a trace for a pick beat or a pick for a trace beat.
    WrongBeatKind,
    /// Protocol v7: a pick's `choice` is not one of the beat's options.
    BadChoice,
}

/// The server's verdict on one beat (traced or picked; `BeatScored`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct GestureResult {
    /// 0..=100: shape accuracy weighted by timing.
    pub score: u8,
    pub grade: Grade,
    /// `Some` when the trace was not accepted (`score` is 0, `grade` miss).
    pub rejected: Option<TraceReject>,
}

/// Where a lesson is (`LessonPhase`). Phases follow the class's lesson
/// script (`data/classes/<class>.json`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LessonPhase {
    /// The join window: students arrive, the professor greets and explains.
    Gathering,
    /// The professor demonstrates a gesture.
    Demonstration,
    /// A practice round (`LessonRound` carries its beats).
    Practice { round: u8 },
    /// Scores and rewards (`ClassResult` to each student).
    Results,
    /// The session is over; students are sent back to the corridor.
    Ended,
}

/// What a beat asks of the student (protocol v7).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum BeatPrompt {
    /// Trace `gesture` (`data/classes/gestures.json` id) on the pad;
    /// answered by `TraceGesture`.
    Trace { gesture: GestureId },
    /// Show `prompt` (an index into the class's prompt set) and offer
    /// `options` (indices into its option set, `PICK_OPTIONS_MIN..=
    /// PICK_OPTIONS_MAX` of them, in the order shown); answered by
    /// `PickAnswer { choice }`, the position in `options`. The sets are
    /// named by the class file (`picks.prompts`, `picks.options`) and read
    /// from `data/classes/picks.json`. Which option is right is never sent.
    Pick {
        prompt: PickIndex,
        options: Vec<PickIndex>,
    },
    /// Protocol v8 (the Flying class): fly through ring `ring` (an index
    /// into the class instance layout's `rings`, `sim_core::layout::Ring`)
    /// within the round's window of the beat. Nothing is sent: the server
    /// scores the crossing from the student's checked flight positions
    /// (`sim_core::ring`) and answers with `BeatScored`; a ring not passed
    /// in its window is a miss with no message.
    Ring { ring: u8 },
}

/// One on-beat prompt of a practice round.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LessonBeat {
    /// Index within the round (0..), named by `TraceGesture.beat` and
    /// `PickAnswer.beat`.
    pub beat: u8,
    pub prompt: BeatPrompt,
    /// When the trace should start or the pick land (server clock, Unix ms).
    pub at_unix_ms: u64,
}

/// A small, invisible fortune from Divination (protocol v7): one hour of
/// a little luck. Only its holder sees it (`OwnEffect.fortune`); numbers in
/// `data/economy.json` `effects.fortune`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FortuneKind {
    /// Lucky Find: a share of Fortune's Draught's find bonus.
    Luck,
    /// Quick Hands: crafts take less time.
    QuickHands,
    /// Keen Mind: more class bonus XP.
    KeenMind,
}

impl FortuneKind {
    pub const ALL: [FortuneKind; 3] = [
        FortuneKind::Luck,
        FortuneKind::QuickHands,
        FortuneKind::KeenMind,
    ];

    /// The id used in data files, JSON and the database
    /// (`active_effects.fortune`).
    pub fn id(self) -> &'static str {
        match self {
            FortuneKind::Luck => "luck",
            FortuneKind::QuickHands => "quick_hands",
            FortuneKind::KeenMind => "keen_mind",
        }
    }

    pub fn from_id(id: &str) -> Option<FortuneKind> {
        FortuneKind::ALL.into_iter().find(|k| k.id() == id)
    }
}

/// Items a class gave (`ClassRewards.items`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct RewardItem {
    /// Catalog id.
    pub item_def: u16,
    pub quantity: u32,
}

/// What a student earned in a class (`ClassResult`).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClassRewards {
    /// Player XP (attending plus the bonus, after the daily class cap).
    pub xp: u32,
    /// The daily class XP cap cut `xp` short (or to nothing).
    pub xp_capped: bool,
    /// XP in the class's subject skill (`sim_core::classes::Subject`).
    pub skill_xp: u32,
    /// For the student's house (`house_points` ledger).
    pub house_points: u16,
    /// Spells learned in this class (newly).
    pub spells: Vec<SpellId>,
    // ---- Protocol v7 (classes v2).
    /// Crowns paid (ledger reason `class`).
    pub crowns: u32,
    /// Items given (ledger reason `class`, merged into stacks).
    pub items: Vec<RewardItem>,
    /// Recipes learned (`data/crafting/recipes.json` ids, newly known).
    pub recipes: Vec<u16>,
    /// The lore entry unlocked (`data/classes/lore.json` id).
    pub lore: Option<u16>,
    /// A hint: the display name of where an unsearched find spot is (a
    /// castle surface's name), with the lore.
    pub hint: Option<String>,
    /// The fortune drawn (an `EffectKind::Fortune` effect now runs).
    pub fortune: Option<FortuneKind>,
}

/// Why entering or acting in a class failed (`ServerMsg::ClassError`).
/// Reach and unknown doors answer `DormError` (`TooFar`, `UnknownTarget`),
/// as for every interactable.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClassErrorCode {
    /// No session is in its join window.
    NotOpen,
    /// Every section of this session is full.
    Full,
    /// This character already attended this session (and left).
    AlreadyAttended,
    /// Too many actions; slow down.
    RateLimited,
    /// The server could not complete it (e.g. a database error).
    Failed,
}

/// Who a spell cast outside a duel aims at.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SpellTarget {
    /// Nothing (Lucerna, a flourish).
    None,
    /// A furniture item placed in the dorm room the caster is in
    /// (Elevara), by `items.id`.
    Furniture { item_id: i64 },
    /// A visible character (flourishes aimed at someone).
    Entity { id: EntityId },
}

/// Why a spell cast outside a duel was refused (`ServerMsg::SpellError`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SpellErrorCode {
    /// No such spell, or the caster has not learned it.
    NotLearned,
    /// Not usable here (Elevara outside a dorm room, social casts in an
    /// arena, a spell with no social action).
    NotHere,
    /// Still cooling down.
    Cooldown,
    /// The target is gone, not furniture, or not in this room.
    BadTarget,
    /// The target is out of range.
    TooFar,
    /// That piece is already floating. Protocol v11: also that target was
    /// hindered a moment ago (immune for a while), or the caster is bound.
    Busy,
    /// Too many casts; slow down.
    RateLimited,
    /// Protocol v11: the target must be a friend, party member or club
    /// member (no block either way).
    NotFriend,
    /// Protocol v11: the spell needs a target (a selected ally) and got
    /// none, or got the caster.
    NeedsTarget,
    /// Protocol v11: the target is sitting, in class, trading, dueling, on
    /// a broom or already being moved.
    Unavailable,
    /// Protocol v11: Adveni: no safe way (a ledge, a door, the moving
    /// stairs, a wall) or too close already.
    NoPath,
}

/// Protocol v11: the most spots one Aperio shows (`ServerMsg::Revealed`).
pub const REVEAL_SPOTS_MAX: usize = 32;

/// Protocol v11: what an Aperio spot is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RevealKind {
    /// A secret passage's door (`data/world/secrets.json`), on the floor
    /// at its centre.
    Passage,
    /// A find spot (exploration).
    FindSpot,
}

/// Protocol v11: one spot Aperio shows its caster.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct RevealedSpot {
    pub kind: RevealKind,
    pub pos: QPos,
}

/// A scripted NPC's action (`ServerMsg::NpcEvent`). Movement and facing
/// come in snapshots, like any entity's.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum NpcEvent {
    /// A spoken line (from the lesson script in data): a speech bubble and
    /// a line in the chat log.
    Say { text: String },
    /// A wand demonstration: play `gesture` (and the spell's colour and
    /// effect, if any) over `duration_ms`.
    Demonstrate {
        gesture: GestureId,
        spell: Option<SpellId>,
        duration_ms: u16,
    },
    /// A wave or a bow.
    Emote { emote: Emote },
}

/// The class session a student just entered (`ServerMsg::ClassJoined`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClassInfo {
    /// `data/classes/schedule.json` class id ("charms").
    pub class: String,
    /// `sim_core::clock::SessionKey::id` of the session.
    pub session: i64,
    /// Section number (0..): overflow opens further sections.
    pub section: u8,
    pub start_unix_ms: u64,
    pub join_until_unix_ms: u64,
    pub end_unix_ms: u64,
    /// The professor's entity in this section.
    pub professor: EntityId,
    pub phase: LessonPhase,
    /// When the current phase ends (Unix ms).
    pub phase_until_unix_ms: u64,
}
