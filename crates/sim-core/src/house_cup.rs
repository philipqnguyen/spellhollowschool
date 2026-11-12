//! The House Cup (`data/house_cup.json`, docs/architecture.md).
//! A week runs from `week_start` (a weekday and an hour, UTC:
//! no daylight saving, so every week is exactly [`WEEK_MS`] long) to the
//! same moment a week later. A house's score for the week is the raw sum of
//! its `house_points` rows in that window; the highest score wins and ties
//! are shared (every house on the top score wins). Points come from four
//! sources ([`CupSource`]), each with a per-character daily cap in points
//! per UTC day. The week's winners mail [`CupReward::item`] to each
//! character of a winning house with at least [`CupReward::min_points`]
//! that week, and wear the cup badge the following week.
//!
//! Everything here is a pure function of its inputs (`unix_ms` in, weeks
//! and points out), so the server, the client and the tests agree.

use protocol::House;
use serde::{Deserialize, Serialize};

use crate::catalog::{Catalog, ItemKind};
use crate::dueling::DuelRules;
use crate::placement::PlaceSurface;

pub const CUP_VERSION: u32 = 1;
/// One day and one week in milliseconds.
pub const DAY_MS: i64 = 86_400_000;
pub const WEEK_MS: i64 = 7 * DAY_MS;

#[derive(Debug, thiserror::Error)]
pub enum CupError {
    #[error("house cup JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("house cup data is inconsistent: {0}")]
    Invalid(String),
}

/// A day of the week (`week_start.weekday`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Weekday {
    Monday,
    Tuesday,
    Wednesday,
    Thursday,
    Friday,
    Saturday,
    Sunday,
}

impl Weekday {
    /// Days from a Thursday (1970-01-01, day 0 of Unix time, was one).
    fn days_after_thursday(self) -> i64 {
        match self {
            Weekday::Thursday => 0,
            Weekday::Friday => 1,
            Weekday::Saturday => 2,
            Weekday::Sunday => 3,
            Weekday::Monday => 4,
            Weekday::Tuesday => 5,
            Weekday::Wednesday => 6,
        }
    }
}

/// When a cup week starts: `weekday` at `hour_utc`:00 UTC.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct WeekStart {
    pub weekday: Weekday,
    pub hour_utc: u8,
}

impl WeekStart {
    /// The Unix ms of the first week start at or after the epoch.
    fn anchor_ms(self) -> i64 {
        (self.weekday.days_after_thursday() * 24 + i64::from(self.hour_utc)) * 3_600_000
    }
}

/// One cup week: `[start_ms, end_ms)` in Unix ms.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct CupWeek {
    pub start_ms: i64,
    pub end_ms: i64,
}

impl CupWeek {
    pub fn contains(&self, unix_ms: i64) -> bool {
        (self.start_ms..self.end_ms).contains(&unix_ms)
    }

    /// The week before this one.
    pub fn prev(&self) -> CupWeek {
        CupWeek {
            start_ms: self.start_ms - WEEK_MS,
            end_ms: self.start_ms,
        }
    }

    /// The week after this one.
    pub fn next(&self) -> CupWeek {
        CupWeek {
            start_ms: self.end_ms,
            end_ms: self.end_ms + WEEK_MS,
        }
    }

    /// Milliseconds from `unix_ms` to the week's end (the reset), 0 once past.
    pub fn ends_in_ms(&self, unix_ms: i64) -> i64 {
        (self.end_ms - unix_ms).max(0)
    }
}

/// Where house points come from (`house_points.source`, except `admin`,
/// which the cup counts but never caps).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CupSource {
    /// A class's 1 / 3 / 5 tiers (`ClassRewardRules::house_points`).
    Class,
    /// A duel win (`DuelRewardRules::win_house_points`).
    Duel,
    /// An exploration find that turned something up.
    Find,
    /// A completed craft of a recipe of skill `min_skill` or more.
    Craft,
    /// NPCs: a house ghost's daily gift (docs/architecture.md "NPCs").
    Npc,
}

impl CupSource {
    pub const ALL: [CupSource; 5] = [
        CupSource::Class,
        CupSource::Duel,
        CupSource::Find,
        CupSource::Craft,
        CupSource::Npc,
    ];

    /// The `house_points.source` id.
    pub fn id(self) -> &'static str {
        match self {
            CupSource::Class => "class",
            CupSource::Duel => "duel",
            CupSource::Find => "find",
            CupSource::Craft => "craft",
            CupSource::Npc => "npc",
        }
    }

    /// The `daily_caps.cap` group that counts this source's points per UTC
    /// day. `None` for duels: dueling keeps its own cap (`duel_points`, in
    /// wins, `house_point_wins_daily`), which [`CupRules::validate_with`]
    /// checks comes to the same number of points.
    pub fn cap_group(self) -> Option<&'static str> {
        match self {
            CupSource::Class => Some("hp_class"),
            CupSource::Duel => None,
            CupSource::Find => Some("hp_find"),
            CupSource::Craft => Some("hp_craft"),
            CupSource::Npc => Some("hp_npc"),
        }
    }
}

/// One source's rule.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceRule {
    /// Points per event: required for `find`, `craft` and `npc`; `class` and `duel`
    /// take theirs from the class and dueling data, so they have none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub points: Option<u16>,
    /// Points per character per UTC day.
    pub daily_cap: u16,
    /// `craft` only: the least recipe skill level that earns points.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub min_skill: Option<u8>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct CupSources {
    pub class: SourceRule,
    pub duel: SourceRule,
    pub find: SourceRule,
    pub craft: SourceRule,
    /// NPCs: a house ghost's daily gift (`points` per gift).
    pub npc: SourceRule,
}

/// How a tie on the top score is settled.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TieRule {
    /// Every house on the top score wins.
    Shared,
}

/// The reward mail's text; `{house}` becomes the winning house's name.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RewardMail {
    pub sender: String,
    pub subject: String,
    pub body: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CupReward {
    /// The catalog item mailed (the House Cup Pennant: wall furniture with
    /// `house_dyes`, never sold).
    pub item: u16,
    /// Points the character earned that week to receive it.
    pub min_points: u32,
    pub mail: RewardMail,
}

/// `data/house_cup.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CupRules {
    pub version: u32,
    pub week_start: WeekStart,
    pub sources: CupSources,
    pub ties: TieRule,
    pub reward: CupReward,
}

/// A house's display name (the mail's `{house}`).
pub fn house_name(house: House) -> &'static str {
    match house {
        House::Roarwing => "Roarwing",
        House::Burrowsby => "Burrowsby",
        House::Skytalon => "Skytalon",
        House::Serpentyne => "Serpentyne",
    }
}

impl CupRules {
    pub fn from_json(json: &str) -> Result<CupRules, CupError> {
        let r: CupRules = serde_json::from_str(json)?;
        r.validate().map_err(CupError::Invalid)?;
        Ok(r)
    }

    fn validate(&self) -> Result<(), String> {
        if self.version != CUP_VERSION {
            return Err(format!(
                "unsupported version {} (expected {CUP_VERSION})",
                self.version
            ));
        }
        if self.week_start.hour_utc > 23 {
            return Err("week_start.hour_utc is 0-23".into());
        }
        for source in CupSource::ALL {
            let r = self.rule(source);
            let per_event = matches!(source, CupSource::Find | CupSource::Craft | CupSource::Npc);
            if per_event != r.points.is_some() {
                return Err(format!(
                    "{}: points are {} here",
                    source.id(),
                    if per_event { "required" } else { "not set" }
                ));
            }
            if r.points == Some(0) || r.daily_cap == 0 {
                return Err(format!(
                    "{}: points and the daily cap are positive",
                    source.id()
                ));
            }
            if r.points.is_some_and(|p| p > r.daily_cap) {
                return Err(format!("{}: one event is over the daily cap", source.id()));
            }
            if (source == CupSource::Craft) != r.min_skill.is_some() {
                return Err(format!("{}: only craft has a min_skill", source.id()));
            }
        }
        if self.reward.min_points == 0 {
            return Err("reward.min_points is positive".into());
        }
        let m = &self.reward.mail;
        // The mail table's limits, with room for the longest house name.
        let sender_ok = (1..=40).contains(&m.sender.chars().count());
        let subject_ok = (1..=60).contains(&m.subject.chars().count());
        if !sender_ok || !subject_ok || m.body.chars().count() > 1900 {
            return Err("reward.mail: a sender of 1-40, a subject of 1-60 and a body of at most 1900 characters".into());
        }
        Ok(())
    }

    /// The cross-checks: the reward is wall furniture, house-dyed and not
    /// sold, and dueling's own cap comes to `sources.duel.daily_cap` points.
    pub fn validate_with(&self, catalog: &Catalog, duel: &DuelRules) -> Result<(), String> {
        let item = catalog
            .get(self.reward.item)
            .ok_or_else(|| format!("reward item {} is not in the catalog", self.reward.item))?;
        let wall = item
            .furniture
            .as_ref()
            .is_some_and(|f| f.surface == PlaceSurface::Wall);
        if item.kind != ItemKind::Furniture || !wall {
            return Err(format!("reward item {}: wall furniture", item.id));
        }
        if item.house_dyes.is_none() {
            return Err(format!(
                "reward item {}: in house colours (house_dyes)",
                item.id
            ));
        }
        if item.price.is_some() || item.retired {
            return Err(format!(
                "reward item {}: never sold, never retired",
                item.id
            ));
        }
        let duel_points = u32::from(duel.rewards.win_house_points)
            * u32::from(duel.rewards.house_point_wins_daily);
        if duel_points != u32::from(self.sources.duel.daily_cap) {
            return Err(format!(
                "sources.duel.daily_cap {} differs from dueling's {duel_points} (win_house_points x house_point_wins_daily)",
                self.sources.duel.daily_cap
            ));
        }
        Ok(())
    }

    pub fn rule(&self, source: CupSource) -> &SourceRule {
        match source {
            CupSource::Class => &self.sources.class,
            CupSource::Duel => &self.sources.duel,
            CupSource::Find => &self.sources.find,
            CupSource::Craft => &self.sources.craft,
            CupSource::Npc => &self.sources.npc,
        }
    }

    /// The cup week holding `unix_ms`.
    pub fn week_of(&self, unix_ms: i64) -> CupWeek {
        let anchor = self.week_start.anchor_ms();
        let start = anchor + (unix_ms - anchor).div_euclid(WEEK_MS) * WEEK_MS;
        CupWeek {
            start_ms: start,
            end_ms: start + WEEK_MS,
        }
    }

    /// Points a find earns before the cap: `find.points` if it turned
    /// something up, else none.
    pub fn find_points(&self, found_something: bool) -> u16 {
        if found_something {
            self.sources.find.points.unwrap_or(0)
        } else {
            0
        }
    }

    /// Points a completed craft of a recipe of skill level `recipe_skill`
    /// earns before the cap.
    pub fn craft_points(&self, recipe_skill: u8) -> u16 {
        let r = &self.sources.craft;
        if recipe_skill >= r.min_skill.unwrap_or(u8::MAX) {
            r.points.unwrap_or(0)
        } else {
            0
        }
    }

    /// How many of `want` points `source` may still award to a character
    /// who has `used_today` of its daily cap (`progression::caps::take` does
    /// this in the database; duels count wins there instead).
    pub fn allowed(&self, source: CupSource, want: u32, used_today: u32) -> u32 {
        want.min(u32::from(self.rule(source).daily_cap).saturating_sub(used_today))
    }

    /// Whether a character of a winning house with `points` that week gets
    /// the reward.
    pub fn earns_reward(&self, points: i64) -> bool {
        points >= i64::from(self.reward.min_points)
    }

    /// The reward mail for a winner: `(sender, subject, body)`.
    pub fn reward_mail(&self, house: House) -> (String, String, String) {
        let m = &self.reward.mail;
        let name = house_name(house);
        (
            m.sender.clone(),
            m.subject.replace("{house}", name),
            m.body.replace("{house}", name),
        )
    }
}

/// The week's winners from each house's total (any order, a house missing
/// counts 0): every house on the top score (ties shared), in `House::ALL`
/// order; none when no house has a positive score.
pub fn winners(totals: &[(House, i64)]) -> Vec<House> {
    let total = |h: House| {
        totals
            .iter()
            .filter(|(t, _)| *t == h)
            .map(|(_, p)| *p)
            .sum::<i64>()
    };
    let top = House::ALL.into_iter().map(total).max().unwrap_or(0);
    if top <= 0 {
        return Vec::new();
    }
    House::ALL
        .into_iter()
        .filter(|&h| total(h) == top)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHIPPED: &str = include_str!("../../../data/house_cup.json");

    fn rules() -> CupRules {
        CupRules::from_json(SHIPPED).expect("shipped house cup rules")
    }

    /// Unix ms of a UTC date and time (proleptic Gregorian; days from
    /// 1970-01-01 by the civil-from-days inverse).
    fn utc(y: i64, m: i64, d: i64, h: i64, min: i64) -> i64 {
        let y2 = if m <= 2 { y - 1 } else { y };
        let era = y2.div_euclid(400);
        let yoe = y2 - era * 400;
        let mp = (m + 9) % 12;
        let doy = (153 * mp + 2) / 5 + d - 1;
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        let days = era * 146_097 + doe - 719_468;
        (days * 24 * 60 + h * 60 + min) * 60_000
    }

    #[test]
    fn the_date_helper_matches_known_instants() {
        assert_eq!(utc(1970, 1, 1, 0, 0), 0);
        assert_eq!(utc(2026, 10, 26, 0, 0), 1_792_972_800_000);
        assert_eq!(utc(2000, 3, 1, 0, 0), 951_868_800_000);
    }

    #[test]
    fn shipped_rules_are_the_decided_numbers() {
        let r = rules();
        assert_eq!(
            r.week_start,
            WeekStart {
                weekday: Weekday::Monday,
                hour_utc: 0
            }
        );
        assert_eq!(r.sources.class.daily_cap, 25);
        assert_eq!(r.sources.duel.daily_cap, 10);
        assert_eq!(
            (r.sources.find.points, r.sources.find.daily_cap),
            (Some(1), 5)
        );
        assert_eq!(
            (
                r.sources.craft.points,
                r.sources.craft.daily_cap,
                r.sources.craft.min_skill
            ),
            (Some(1), 5, Some(2))
        );
        assert_eq!(r.reward.min_points, 10);
        assert_eq!(r.ties, TieRule::Shared);
    }

    #[test]
    fn weeks_start_on_monday_midnight_utc() {
        let r = rules();
        // 2026-10-26 is a Monday.
        let monday = utc(2026, 10, 26, 0, 0);
        let w = r.week_of(utc(2026, 10, 27, 13, 19));
        assert_eq!(w.start_ms, monday);
        assert_eq!(w.end_ms, utc(2026, 11, 2, 0, 0));
        // The boundary belongs to the week it starts.
        assert_eq!(r.week_of(monday), w);
        assert_eq!(r.week_of(monday - 1), w.prev());
        assert_eq!(r.week_of(w.end_ms - 1), w);
        assert_eq!(r.week_of(w.end_ms), w.next());
        assert!(w.contains(monday) && !w.contains(w.end_ms));
        assert_eq!(w.ends_in_ms(w.end_ms - 5_000), 5_000);
        assert_eq!(w.ends_in_ms(w.end_ms + 5_000), 0);
        // Sunday 23:59 is still the old week.
        assert_eq!(r.week_of(utc(2026, 11, 1, 23, 59)), w);
    }

    #[test]
    fn a_week_spans_the_year_end() {
        let r = rules();
        // 2026-12-28 and 2027-01-04 are Mondays.
        let w = r.week_of(utc(2027, 1, 1, 12, 0));
        assert_eq!(w.start_ms, utc(2026, 12, 28, 0, 0));
        assert_eq!(w.end_ms, utc(2027, 1, 4, 0, 0));
        assert_eq!(r.week_of(utc(2026, 12, 31, 23, 59)), w);
        // A leap year's end: 2028-12-25 (Monday) to 2029-01-01 (Monday).
        let w = r.week_of(utc(2028, 12, 31, 23, 0));
        assert_eq!(
            (w.start_ms, w.end_ms),
            (utc(2028, 12, 25, 0, 0), utc(2029, 1, 1, 0, 0))
        );
        // Every week is exactly seven days (no daylight saving in UTC).
        let mut w = r.week_of(utc(2026, 3, 1, 0, 0));
        for _ in 0..60 {
            assert_eq!(w.end_ms - w.start_ms, WEEK_MS);
            assert_eq!(r.week_of(w.start_ms + WEEK_MS / 2), w);
            w = w.next();
        }
    }

    #[test]
    fn the_anchor_follows_the_data() {
        let mut r = rules();
        r.week_start = WeekStart {
            weekday: Weekday::Sunday,
            hour_utc: 18,
        };
        let w = r.week_of(utc(2026, 10, 27, 0, 0));
        assert_eq!(w.start_ms, utc(2026, 10, 25, 18, 0));
        // Before the epoch too.
        r.week_start.weekday = Weekday::Thursday;
        r.week_start.hour_utc = 0;
        assert_eq!(r.week_of(-1).start_ms, -WEEK_MS);
        assert_eq!(r.week_of(0).start_ms, 0);
    }

    #[test]
    fn caps_and_per_event_points() {
        let r = rules();
        // Class: five 5-point classes fill the day; the sixth gets nothing.
        let mut used = 0;
        for _ in 0..5 {
            let got = r.allowed(CupSource::Class, 5, used);
            assert_eq!(got, 5);
            used += got;
        }
        assert_eq!(r.allowed(CupSource::Class, 5, used), 0);
        // A partial award at the edge.
        assert_eq!(r.allowed(CupSource::Class, 5, 23), 2);
        assert_eq!(r.allowed(CupSource::Class, 3, 40), 0);
        // Finds: 1 for something found, none for nothing; 5 a day.
        assert_eq!(r.find_points(true), 1);
        assert_eq!(r.find_points(false), 0);
        assert_eq!(r.allowed(CupSource::Find, 1, 4), 1);
        assert_eq!(r.allowed(CupSource::Find, 1, 5), 0);
        // Crafts: skill 2 or more.
        assert_eq!(r.craft_points(1), 0);
        assert_eq!(r.craft_points(2), 1);
        assert_eq!(r.craft_points(8), 1);
        assert_eq!(r.allowed(CupSource::Craft, 1, 5), 0);
        // Duels: 10 points a day.
        assert_eq!(r.allowed(CupSource::Duel, 2, 8), 2);
        assert_eq!(r.allowed(CupSource::Duel, 2, 10), 0);
        assert_eq!(CupSource::Duel.cap_group(), None);
        let groups: Vec<_> = CupSource::ALL
            .iter()
            .filter_map(|s| s.cap_group())
            .collect();
        assert_eq!(groups, ["hp_class", "hp_find", "hp_craft", "hp_npc"]);
        // NPCs: a point a gift, two a day.
        assert_eq!(r.allowed(CupSource::Npc, 1, 1), 1);
        assert_eq!(r.allowed(CupSource::Npc, 1, 2), 0);
    }

    #[test]
    fn ties_are_shared_and_nobody_wins_an_empty_week() {
        use House::*;
        assert_eq!(winners(&[]), Vec::<House>::new());
        assert_eq!(
            winners(&[(Roarwing, 0), (Serpentyne, 0)]),
            Vec::<House>::new()
        );
        assert_eq!(
            winners(&[(Roarwing, 10), (Burrowsby, 30), (Skytalon, 5)]),
            [Burrowsby]
        );
        assert_eq!(
            winners(&[(Serpentyne, 30), (Roarwing, 30), (Skytalon, 29)]),
            [Roarwing, Serpentyne]
        );
        assert_eq!(
            winners(&[
                (Roarwing, 7),
                (Burrowsby, 7),
                (Skytalon, 7),
                (Serpentyne, 7)
            ]),
            House::ALL.to_vec()
        );
        // Rows of one house add up (raw sums).
        assert_eq!(
            winners(&[(Skytalon, 4), (Roarwing, 6), (Skytalon, 4)]),
            [Skytalon]
        );
    }

    #[test]
    fn the_reward_threshold_and_mail() {
        let r = rules();
        assert!(!r.earns_reward(9));
        assert!(r.earns_reward(10));
        let (sender, subject, body) = r.reward_mail(House::Skytalon);
        assert!(!sender.is_empty());
        assert!(subject.contains("Skytalon") && !subject.contains('{'));
        assert!(body.contains("Skytalon") && !body.contains('{'));
        assert!(subject.chars().count() <= 80);
    }

    #[test]
    fn bad_data_is_rejected() {
        let mut v: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        let check = |v: &serde_json::Value| CupRules::from_json(&v.to_string());
        assert!(check(&v).is_ok());
        v["sources"]["class"]["points"] = 3.into();
        assert!(check(&v).is_err(), "class points come from the classes");
        v["sources"]["class"]
            .as_object_mut()
            .unwrap()
            .remove("points");
        v["sources"]["find"]["daily_cap"] = 0.into();
        assert!(check(&v).is_err());
        v["sources"]["find"]["daily_cap"] = 5.into();
        v["sources"]["find"]["min_skill"] = 2.into();
        assert!(check(&v).is_err(), "only craft has a min_skill");
        v["sources"]["find"]
            .as_object_mut()
            .unwrap()
            .remove("min_skill");
        v["week_start"]["hour_utc"] = 24.into();
        assert!(check(&v).is_err());
        v["week_start"]["hour_utc"] = 0.into();
        v["ties"] = "split".into();
        assert!(check(&v).is_err());
        v["ties"] = "shared".into();
        v["version"] = 2.into();
        assert!(check(&v).is_err());
    }

    #[test]
    fn the_shipped_reward_and_duel_cap_check_against_their_data() {
        let palette =
            crate::Palette::from_json(include_str!("../../../data/items/palette.json")).unwrap();
        let catalog =
            Catalog::from_json(include_str!("../../../data/items/catalog.json"), &palette).unwrap();
        let duel = DuelRules::from_json(include_str!("../../../data/dueling/rules.json")).unwrap();
        let r = rules();
        r.validate_with(&catalog, &duel).unwrap();
        let mut bad = r.clone();
        bad.sources.duel.daily_cap = 12;
        assert!(bad.validate_with(&catalog, &duel).is_err());
        bad = r.clone();
        bad.reward.item = 1001; // the bed: floor furniture, sold
        assert!(bad.validate_with(&catalog, &duel).is_err());
    }
}
