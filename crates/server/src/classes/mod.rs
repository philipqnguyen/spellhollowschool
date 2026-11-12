//! Scheduled classes.
//! Everything here is generic over the classes of
//! `data/classes/schedule.json`, so a new class is data only.
//!
//! **Flow** (docs/architecture.md, "Classes and progression"):
//!
//! 1. A student `Interact`s with the class's door (`class_door`, in reach;
//!    the world checks reach and calls [`world::Classes::on_door`]). During
//!    the session's join window (`Schedule::joinable`) they get a seat in a
//!    section with room (`instance_cap`), opening another section when all
//!    are full (up to `max_sections`; then `ClassError { Full }`); outside
//!    it `ClassError { NotOpen }`. [`admit`] (a database job) writes the
//!    section's `class_sessions` row and the student's `class_attendance`
//!    row (one per session and character, whichever section); then the
//!    student enters the section (`ZoneChanged`, `ClassJoined`). The first
//!    student of a section opens its instance and spawns the professor
//!    ([`crate::npc::spawn_professor`], a priority entity). A student who
//!    left may come back to their own section while the door is open;
//!    otherwise, and after being rewarded, it is `AlreadyAttended`.
//! 2. The lesson follows the script (`ClassDef::phase_at`, `LessonAction`):
//!    `NpcEvent`s for the professor's lines, emotes and demonstrations,
//!    `LessonPhaseChanged` on each phase, `LessonRound` with the beats'
//!    Unix ms as each round starts. All sections of a session share its
//!    timeline (it is reckoned from the session's start, not the section's).
//! 3. `TraceGesture` and `PickAnswer` ([`world::Classes::on_input`]): one
//!    answer per beat, of the beat's kind (`WrongBeatKind`), under a
//!    per-student rate limit shared by both. A trace is scored with the
//!    shared scorer (`Gestures::check` and `combine`, the code the client
//!    previews with), the arrival check (`Gestures::check_arrival` with the
//!    receipt time), and no trace repeated in the session
//!    (`gesture::fingerprint`, any student: copied input is generated
//!    input). A pick is judged by `sim_core::pick::judge`
//!    against the answer the server holds (never sent): the choice is one of
//!    the options, it arrived in time, right scores by its timing and wrong
//!    scores 0. A pick for a round of another session (a replay of an old
//!    message) finds no such round. `BeatScored` back. No database work per
//!    answer.
//! 4. Results: per student still in the room, [`reward`] in one
//!    transaction (character row first): the attendance row's score and
//!    `rewarded_at` (set once: a retry finds it set and grants nothing),
//!    `progression::grant_xp` for `ClassAttend` and `ClassBonus` (ref: the
//!    attendance id; both count against the daily `class` cap; a running
//!    Keen Mind fortune adds `keen_mind_pct` to the bonus), the subject's
//!    skill XP (plus the section bonus, if the class has one), house points
//!    (source class, ref the attendance id), the spells the class teaches
//!    at the level reached (`progression::teach`), and rewards v2:
//!    Crowns (`ledger::adjust_crowns`, reason `class`, ref the
//!    attendance id), items (`items::give`, the same reason and ref, merged
//!    into stacks), the recipes it teaches at the subject level reached
//!    (`known_recipes` rows), a lore entry and a hint ([`lore`]) and a
//!    fortune (`effects::store`). What was granted is recorded on the
//!    attendance row (the hint too; the spells taught
//!    and the cap flag), so a retry answers the same and the audit can match
//!    the ledgers. Fewer than `min_answers` accepted answers earns nothing
//!    (the score is still recorded). Then `ClassResult`, `Progress`
//!    (`LevelChanged` to viewers), `Spellbook`, `Wallet` and the fortune's
//!    effect. At the end students still in the room go back to the
//!    corridor.
//!
//! Routes ([`routes`]): `GET /api/classes/schedule` (each class's current
//! and next session, and the timetable's slots with when each next starts,
//! for tests and tools; the HUD computes the same from the data), `GET
//! /api/classes/attendance?limit=&after=` (the character's sessions, newest
//! first) and `GET /api/lore` ([`lore`]).

pub mod lore;
pub mod world;

use std::time::{Duration, Instant};

use axum::extract::State;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use protocol::{ClassRewards, FortuneKind, RewardItem, SpellId, XpSource};
use serde::Serialize;
use sim_core::{ClassDef, Lore, SessionTimes};
use sqlx::types::Json as DbJson;
use sqlx::{PgConnection, PgPool};

use crate::AppState;
use crate::catalog::shipped;
use crate::characters::{Page, PageQuery, Player, house_from_db};
use crate::house_points::{self, PointsSource};
use crate::http::{ApiError, ApiQuery};
use crate::inventory::LOCATION_INVENTORY;
use crate::items::{self, NewItem};
use crate::ledger::{self, Reason};
use crate::progression::{self, XpGrant};

// ---------------------------------------------------------------- admission

/// A seat to record ([`admit`]): a student entering section `section` of
/// session `session` of class `class_id`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdmitRequest {
    pub class_id: String,
    pub session: SessionTimes,
    pub section: u8,
    pub character_id: i64,
}

/// What [`admit`] found.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Admission {
    /// The student's attendance row (new, or theirs from before a restart).
    Admitted { attendance_id: i64 },
    /// They were already rewarded for this session.
    AlreadyAttended,
}

fn timestamp(ms: i64) -> DateTime<Utc> {
    DateTime::from_timestamp_millis(ms).unwrap_or_default()
}

/// Records a student's seat: the section's `class_sessions` row (made by
/// its first student) and the student's `class_attendance` row, unique per
/// session and character. A row left from before a server restart is
/// reused (moved to the new section) unless it was already rewarded.
pub async fn admit(pool: &PgPool, req: &AdmitRequest) -> Result<Admission, sqlx::Error> {
    let mut tx = pool.begin().await?;
    let key = req.session.key.id();
    let session_id: i64 = sqlx::query_scalar(
        "INSERT INTO class_sessions (class_id, session_key, section, starts_at, ends_at)
         VALUES ($1, $2, $3, $4, $5)
         ON CONFLICT (class_id, session_key, section) DO UPDATE SET class_id = EXCLUDED.class_id
         RETURNING id",
    )
    .bind(&req.class_id)
    .bind(key)
    .bind(i16::from(req.section))
    .bind(timestamp(req.session.start_ms))
    .bind(timestamp(req.session.end_ms))
    .fetch_one(&mut *tx)
    .await?;
    let inserted: Option<i64> = sqlx::query_scalar(
        "INSERT INTO class_attendance (session_id, character_id, class_id, session_key)
         VALUES ($1, $2, $3, $4)
         ON CONFLICT (class_id, session_key, character_id) DO NOTHING
         RETURNING id",
    )
    .bind(session_id)
    .bind(req.character_id)
    .bind(&req.class_id)
    .bind(key)
    .fetch_optional(&mut *tx)
    .await?;
    let admission = match inserted {
        Some(attendance_id) => Admission::Admitted { attendance_id },
        None => {
            let (id, rewarded): (i64, Option<DateTime<Utc>>) = sqlx::query_as(
                "SELECT id, rewarded_at FROM class_attendance
                 WHERE class_id = $1 AND session_key = $2 AND character_id = $3 FOR UPDATE",
            )
            .bind(&req.class_id)
            .bind(key)
            .bind(req.character_id)
            .fetch_one(&mut *tx)
            .await?;
            if rewarded.is_some() {
                Admission::AlreadyAttended
            } else {
                sqlx::query("UPDATE class_attendance SET session_id = $2 WHERE id = $1")
                    .bind(id)
                    .bind(session_id)
                    .execute(&mut *tx)
                    .await?;
                Admission::Admitted { attendance_id: id }
            }
        }
    };
    tx.commit().await?;
    Ok(admission)
}

// ---------------------------------------------------------------- rewards

/// A student's result to reward ([`reward`]).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RewardRequest {
    pub attendance_id: i64,
    pub character_id: i64,
    pub class_id: String,
    /// The session's score, 0-100 (missed beats count 0).
    pub score_pct: u8,
    /// Answers accepted, traces and picks (taking part needs
    /// `ClassRewardRules::min_answers`).
    pub answers: u8,
    /// The average score of the student's section (the group bonus of a
    /// class with `section_bonus_skill_xp_max`).
    pub section_pct: u8,
}

/// What a reward granted.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rewarded {
    pub rewards: ClassRewards,
    /// The `ClassAttend` grant, then the `ClassBonus` one (if any).
    pub grants: Vec<XpGrant>,
    /// The spellbook after the class (learned, effective loadout), for
    /// `Spellbook` when spells were learned or the level changed.
    pub learned: Vec<SpellId>,
    pub loadout: Vec<SpellId>,
    /// The Crown balance after the class's Crowns (for `Wallet`), if it
    /// paid any.
    pub crowns_balance: Option<i64>,
    /// The student's skill XP in the class's subject after the
    /// class (a Flying class refreshes the world's cache: may they mount).
    pub skill_total: u32,
}

impl Rewarded {
    /// The character's total XP after the grants.
    pub fn xp(&self) -> Option<u32> {
        self.grants.last().map(|g| g.xp)
    }

    pub fn leveled_up(&self) -> bool {
        match (self.grants.first(), self.grants.last()) {
            (Some(a), Some(b)) => b.level > a.level_before,
            _ => false,
        }
    }
}

/// How [`reward`] ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RewardOutcome {
    /// Rewards granted now.
    Granted(Rewarded),
    /// Too few accepted traces: the score was recorded, nothing granted.
    NotTakingPart,
    /// Granted before (a retry): what was granted then; nothing changes.
    AlreadyRewarded(ClassRewards),
}

/// The player XP a score earns: attending plus the score's bonus.
pub fn class_xp(score_pct: u8) -> (u32, u32) {
    let p = &shipped().progression;
    (p.xp.class_attend, p.class_bonus(score_pct))
}

/// The bonus XP with a Keen Mind fortune running: `keen_mind_pct` more
/// (rounded down).
pub fn keen_mind_bonus(bonus: u32) -> u32 {
    let pct = u32::from(shipped().economy.effects.fortune.keen_mind_pct);
    bonus.saturating_mul(100 + pct) / 100
}

/// What the attendance row recorded of a reward (a retry answers it).
#[derive(sqlx::FromRow)]
struct Recorded {
    rewarded_at: Option<DateTime<Utc>>,
    xp: i32,
    skill_xp: i32,
    house_points: i32,
    crowns: i32,
    items: DbJson<Vec<RewardItem>>,
    recipes: Vec<i32>,
    lore_id: Option<i32>,
    fortune: Option<String>,
    /// The hint a lore class gave (`0016_class_hint.sql`).
    hint: Option<String>,
    /// The spells it taught and
    /// whether the daily class XP cap cut the XP.
    spells: Vec<i32>,
    xp_capped: bool,
}

impl Recorded {
    fn rewards(self) -> ClassRewards {
        ClassRewards {
            xp: u32::try_from(self.xp).unwrap_or(0),
            skill_xp: u32::try_from(self.skill_xp).unwrap_or(0),
            house_points: u16::try_from(self.house_points).unwrap_or(0),
            crowns: u32::try_from(self.crowns).unwrap_or(0),
            items: self.items.0,
            recipes: self
                .recipes
                .into_iter()
                .filter_map(|r| u16::try_from(r).ok())
                .collect(),
            lore: self.lore_id.and_then(|l| u16::try_from(l).ok()),
            fortune: self.fortune.as_deref().and_then(FortuneKind::from_id),
            hint: self.hint,
            spells: self
                .spells
                .into_iter()
                .filter_map(|s| SpellId::try_from(s).ok())
                .collect(),
            xp_capped: self.xp_capped,
        }
    }
}

fn ledger_db(e: ledger::LedgerError) -> sqlx::Error {
    match e {
        ledger::LedgerError::Db(db) => db,
        other => sqlx::Error::Protocol(other.to_string()),
    }
}

fn item_db(e: items::ItemError) -> sqlx::Error {
    match e {
        items::ItemError::Db(db) => db,
        items::ItemError::Ledger(l) => ledger_db(l),
        other => sqlx::Error::Protocol(other.to_string()),
    }
}

/// The recipes of `teaches` the character does not know yet, now known
/// (`known_recipes` rows), in the order given.
async fn teach_recipes(
    conn: &mut PgConnection,
    character_id: i64,
    teaches: impl Iterator<Item = u16>,
) -> Result<Vec<u16>, sqlx::Error> {
    let mut learned = Vec::new();
    for recipe in teaches {
        let new = sqlx::query(
            "INSERT INTO known_recipes (character_id, recipe_id) VALUES ($1, $2)
             ON CONFLICT DO NOTHING",
        )
        .bind(character_id)
        .bind(i32::from(recipe))
        .execute(&mut *conn)
        .await?
        .rows_affected();
        if new > 0 {
            learned.push(recipe);
        }
    }
    Ok(learned)
}

/// Grants a student's class rewards, once, in one transaction: the
/// character row is locked first (the established order), then the
/// attendance row, whose `rewarded_at` decides whether anything is left to
/// grant. Every grant names the attendance id, so even a grant outside
/// this function could not repeat it. The class is the shipped one of
/// `req.class_id` ([`reward_class`] takes any).
pub async fn reward(
    pool: &PgPool,
    req: &RewardRequest,
    now: DateTime<Utc>,
) -> Result<RewardOutcome, sqlx::Error> {
    let content = shipped();
    let class: &ClassDef = content
        .class(&req.class_id)
        .ok_or_else(|| sqlx::Error::Protocol(format!("unknown class {}", req.class_id)))?;
    reward_class(pool, class, &content.lore, req, now).await
}

/// [`reward`] for `class` with the lore entries `lore` (tests reward
/// classes and lore that are not shipped, such as a fortune class).
pub async fn reward_class(
    pool: &PgPool,
    class: &ClassDef,
    lore: &Lore,
    req: &RewardRequest,
    now: DateTime<Utc>,
) -> Result<RewardOutcome, sqlx::Error> {
    let mut tx = pool.begin().await?;
    let house: i16 =
        sqlx::query_scalar("SELECT house FROM characters WHERE id = $1 FOR NO KEY UPDATE")
            .bind(req.character_id)
            .fetch_one(&mut *tx)
            .await?;
    let house = house_from_db(house)
        .ok_or_else(|| sqlx::Error::Protocol("character row has an unknown house".into()))?;
    let row: Option<Recorded> = sqlx::query_as(
        "SELECT rewarded_at, xp, skill_xp, house_points, crowns, items, recipes, lore_id,
                fortune, hint, spells, xp_capped
         FROM class_attendance
         WHERE id = $1 AND character_id = $2 AND class_id = $3 FOR UPDATE",
    )
    .bind(req.attendance_id)
    .bind(req.character_id)
    .bind(&req.class_id)
    .fetch_optional(&mut *tx)
    .await?;
    let Some(recorded) = row else {
        return Err(sqlx::Error::RowNotFound);
    };
    if recorded.rewarded_at.is_some() {
        tx.rollback().await?;
        return Ok(RewardOutcome::AlreadyRewarded(recorded.rewards()));
    }
    let score_pct = req.score_pct.min(100);
    let record_score = sqlx::query(
        "UPDATE class_attendance SET score_pct = $2, answers = $3 WHERE id = $1 AND rewarded_at IS NULL",
    )
    .bind(req.attendance_id)
    .bind(i16::from(score_pct))
    .bind(i16::from(req.answers));
    if req.answers < class.rewards.min_answers {
        record_score.execute(&mut *tx).await?;
        tx.commit().await?;
        return Ok(RewardOutcome::NotTakingPart);
    }
    record_score.execute(&mut *tx).await?;

    let rules = &class.rewards;
    let id = req.character_id;
    let r = Some(req.attendance_id);
    let (attend, mut bonus) = class_xp(score_pct);
    if crate::effects::active_fortune(&mut tx, id).await? == Some(FortuneKind::KeenMind) {
        bonus = keen_mind_bonus(bonus);
    }
    let mut grants =
        vec![progression::grant_xp(&mut tx, id, XpSource::ClassAttend, r, attend, now).await?];
    if bonus > 0 {
        let g = progression::grant_xp(&mut tx, id, XpSource::ClassBonus, r, bonus, now).await?;
        grants.push(g);
    }
    let xp: u32 = grants
        .iter()
        .try_fold(0u32, |sum, g| sum.checked_add(g.granted))
        .ok_or_else(|| sqlx::Error::Protocol("class XP overflows".into()))?;
    let xp_capped = grants.iter().any(|g| g.capped);
    let skill_xp = rules
        .skill_xp(score_pct)
        .saturating_add(rules.section_bonus(req.section_pct));
    let skill_total = crate::skills::add_xp(&mut tx, id, class.subject, skill_xp).await?;
    let skill_level = crate::skills::level_of(skill_total);
    // Under the House Cup's daily class cap (`hp_class`); the
    // attendance records what was awarded, so it matches the ledger.
    let earned = rules.house_points(score_pct);
    let awarded = house_points::award_capped(
        &mut tx,
        house,
        u32::from(earned),
        id,
        PointsSource::Class,
        r,
        now,
    )
    .await?;
    let points = u16::try_from(awarded).unwrap_or(earned);
    let level = grants.last().map_or(1, |g| g.level);
    let spells = progression::teach(&mut tx, id, &class.id, level).await?;
    let (learned, loadout) = progression::spellbook(&mut tx, id, level).await?;

    // ---- Rewards v2.
    let crowns = rules.crowns(score_pct);
    let crowns_balance = if crowns > 0 {
        Some(
            ledger::adjust_crowns(&mut tx, id, i64::from(crowns), Reason::Class, r)
                .await
                .map_err(ledger_db)?,
        )
    } else {
        None
    };
    let mut items_given = Vec::new();
    // Plus one of the class's `draw` items the score reaches.
    for (item_def, quantity) in rules.items(score_pct, rand::random::<u32>()) {
        let item = NewItem::plain(id, item_def, quantity, LOCATION_INVENTORY);
        items::give(&mut tx, &item, Reason::Class, r)
            .await
            .map_err(item_db)?;
        items_given.push(RewardItem { item_def, quantity });
    }
    let recipes = teach_recipes(&mut tx, id, rules.teaches_at(skill_level)).await?;
    let (lore_id, hint) = if rules.lore {
        (
            lore::unlock(&mut tx, lore, id, req.attendance_id).await?,
            lore::hint(&mut tx, id, house, req.attendance_id).await?,
        )
    } else {
        (None, None)
    };
    let fortune = rules.fortune_for(score_pct, rand::random::<u32>());
    if let Some(kind) = fortune {
        let duration: Duration = crate::effects::fortune_duration();
        let effect = crate::effects::fortune_effect(kind, Instant::now() + duration);
        crate::effects::store(&mut tx, id, &effect, duration).await?;
    }

    sqlx::query(
        "UPDATE class_attendance
         SET xp = $2, skill_xp = $3, house_points = $4, rewarded_at = $5, crowns = $6,
             items = $7, recipes = $8, lore_id = $9, fortune = $10, hint = $11,
             spells = $12, xp_capped = $13
         WHERE id = $1",
    )
    .bind(req.attendance_id)
    .bind(i32::try_from(xp).unwrap_or(i32::MAX))
    .bind(i32::try_from(skill_xp).unwrap_or(i32::MAX))
    .bind(i32::from(points))
    .bind(now)
    .bind(i32::try_from(crowns).unwrap_or(i32::MAX))
    .bind(DbJson(&items_given))
    .bind(recipes.iter().map(|&r| i32::from(r)).collect::<Vec<i32>>())
    .bind(lore_id.map(i32::from))
    .bind(fortune.map(FortuneKind::id))
    .bind(hint.as_deref())
    .bind(spells.iter().map(|&s| i32::from(s)).collect::<Vec<i32>>())
    .bind(xp_capped)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(RewardOutcome::Granted(Rewarded {
        rewards: ClassRewards {
            xp,
            xp_capped,
            skill_xp,
            house_points: points,
            spells,
            crowns,
            items: items_given,
            recipes,
            lore: lore_id,
            hint,
            fortune,
        },
        grants,
        learned,
        loadout,
        crowns_balance,
        skill_total,
    }))
}

// ---------------------------------------------------------------- routes

/// One session in `GET /api/classes/schedule`.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SessionView {
    /// `SessionKey::id`.
    pub session: i64,
    pub start_ms: i64,
    pub join_until_ms: i64,
    pub end_ms: i64,
    /// The door admits students now.
    pub joinable: bool,
}

impl SessionView {
    pub fn new(s: &SessionTimes, now: i64) -> Self {
        SessionView {
            session: s.key.id(),
            start_ms: s.start_ms,
            join_until_ms: s.join_until_ms,
            end_ms: s.end_ms,
            joinable: s.joinable(now),
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct ClassTimesView {
    pub id: String,
    pub name: String,
    /// The castle landmark at the door (the HUD's "where").
    pub landmark: String,
    /// The session under way, if any.
    pub current: Option<SessionView>,
    /// The next one to start.
    pub next: SessionView,
}

/// One slot of the timetable in `GET /api/classes/schedule`.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SlotView {
    /// Index into `schedule.json` `slots`.
    pub slot: u8,
    /// The game time it starts, `"HH:MM"`.
    pub at: String,
    /// `morning`, `afternoon` or `night`.
    pub period: &'static str,
    /// The classes starting then, in file order.
    pub classes: Vec<String>,
    /// When it next starts (Unix ms, after `now`).
    pub next_start_ms: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScheduleView {
    pub now_unix_ms: i64,
    pub classes: Vec<ClassTimesView>,
    /// Every slot of the day in order.
    pub slots: Vec<SlotView>,
}

/// Every class's current and next session at `now` (Unix ms).
pub fn schedule_at(now: i64) -> ScheduleView {
    let schedule = &shipped().schedule;
    let classes = schedule
        .classes
        .iter()
        .map(|c| ClassTimesView {
            id: c.id.clone(),
            name: c.name.clone(),
            landmark: c.landmark.clone(),
            current: schedule.current(c, now).map(|s| SessionView::new(&s, now)),
            next: SessionView::new(&schedule.next(c, now), now),
        })
        .collect();
    let clock = &schedule.clock;
    let today = clock.game_time(now).day;
    let slots = schedule
        .slots
        .iter()
        .enumerate()
        .map(|(k, at)| {
            let start = clock.unix_ms(today, at.secs());
            SlotView {
                slot: k as u8,
                at: at.to_string(),
                period: clock.period_at_secs(at.secs()).id(),
                classes: schedule.in_slot(k as u8).map(|c| c.id.clone()).collect(),
                next_start_ms: if start > now {
                    start
                } else {
                    clock.unix_ms(today + 1, at.secs())
                },
            }
        })
        .collect();
    ScheduleView {
        now_unix_ms: now,
        classes,
        slots,
    }
}

/// `GET /api/classes/schedule`, on the game's clock (the world's, with any
/// development skew, when it runs).
pub async fn get_schedule(State(state): State<AppState>, _player: Player) -> Json<ScheduleView> {
    let now = match state.world() {
        Some(w) => w.clock().now_unix_ms(),
        None => crate::clock::ServerClock::new().now_unix_ms(),
    };
    Json(schedule_at(now))
}

/// One row of `GET /api/classes/attendance`.
#[derive(Debug, Clone, Serialize, sqlx::FromRow)]
pub struct AttendanceView {
    pub id: i64,
    pub class_id: String,
    pub session: i64,
    pub joined_at: DateTime<Utc>,
    pub score_pct: Option<i16>,
    pub xp: i32,
    pub skill_xp: i32,
    pub house_points: i32,
    /// The Crowns it paid.
    pub crowns: i32,
    pub rewarded_at: Option<DateTime<Utc>>,
}

/// `GET /api/classes/attendance?limit=&after=`: newest first, keyset on the
/// id (`class_attendance (character_id, id)`).
pub async fn get_attendance(
    State(state): State<AppState>,
    player: Player,
    ApiQuery(page): ApiQuery<PageQuery>,
) -> Result<Json<Page<AttendanceView>>, ApiError> {
    let limit = page.limit()?;
    let after: Option<i64> = page.after()?;
    let rows: Vec<AttendanceView> = sqlx::query_as(
        "SELECT id, class_id, session_key AS session, joined_at, score_pct, xp, skill_xp,
                house_points, crowns, rewarded_at
         FROM class_attendance
         WHERE character_id = $1 AND ($2::bigint IS NULL OR id < $2)
         ORDER BY id DESC LIMIT $3",
    )
    .bind(player.character.id)
    .bind(after)
    .bind(page.fetch_limit()?)
    .fetch_all(&state.pool)
    .await?;
    Ok(Json(Page::from_rows(rows, limit, |r| r.id)))
}

/// This module's routes.
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    Router::new()
        .route("/classes/schedule", get(get_schedule))
        .route("/classes/attendance", get(get_attendance))
        .merge(lore::routes())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_schedule_view_names_the_current_and_next_sessions() {
        let schedule = &shipped().schedule;
        let charms = schedule.class("charms").unwrap();
        let first = schedule.next(charms, schedule.clock.epoch_unix_ms);
        let view = schedule_at(first.start_ms + 1_000);
        let c = &view.classes[0];
        assert_eq!(c.id, "charms");
        assert_eq!(c.landmark, charms.landmark);
        let current = c.current.as_ref().expect("under way");
        assert_eq!(current.session, first.key.id());
        assert!(current.joinable);
        assert!(c.next.start_ms > current.start_ms && !c.next.joinable);
        // After the join window the session still runs but admits no one.
        let late = schedule_at(first.join_until_ms);
        assert!(!late.classes[0].current.as_ref().unwrap().joinable);
        assert!(schedule_at(first.end_ms).classes[0].current.is_none());
    }

    #[test]
    fn the_schedule_view_lists_every_slot_with_its_next_start() {
        let schedule = &shipped().schedule;
        let charms = schedule.class("charms").unwrap();
        let first = schedule.next(charms, schedule.clock.epoch_unix_ms);
        let now = first.start_ms + 1_000;
        let view = schedule_at(now);
        assert_eq!(view.slots.len(), schedule.slots.len());
        for (k, s) in view.slots.iter().enumerate() {
            assert_eq!(usize::from(s.slot), k);
            assert!(s.next_start_ms > now);
            assert!(s.next_start_ms - now <= schedule.clock.day_ms());
        }
        // The slot under way starts again tomorrow.
        let at = schedule.clock.game_time(first.start_ms).secs;
        let slot = view
            .slots
            .iter()
            .find(|s| {
                s.classes.contains(&"charms".to_owned())
                    && s.next_start_ms - first.start_ms == schedule.clock.day_ms()
            })
            .expect("the slot under way");
        assert_eq!(slot.at, sim_core::clock::TimeOfDay(at).to_string());
        let json = serde_json::to_value(&view).unwrap();
        assert!(json["slots"][0]["period"].is_string());
    }

    #[test]
    fn keen_mind_adds_its_share_of_the_bonus() {
        let pct = shipped().economy.effects.fortune.keen_mind_pct;
        assert_eq!(keen_mind_bonus(0), 0);
        assert_eq!(keen_mind_bonus(100), 100 + u32::from(pct));
    }

    #[test]
    fn class_xp_scales_the_bonus() {
        let p = &shipped().progression;
        assert_eq!(class_xp(0), (p.xp.class_attend, 0));
        assert_eq!(class_xp(100), (p.xp.class_attend, p.xp.class_bonus_max));
        assert_eq!(class_xp(250), class_xp(100));
    }
}
