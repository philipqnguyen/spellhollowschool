//! Player levels and XP: `data/progression.json`
//! ([`Progression`]).
//!
//! A character's level (1 to [`protocol::LEVEL_MAX`]) follows from its total
//! XP through the curve `levels` (the total XP each level starts at). Beside
//! the level the HUD and nameplates show a school year: Year 1 at levels
//! 1-3, then one year per three levels, up to Year 7 at levels 19-20
//! (`years`, checked to cover every level). XP comes from classes, crafting,
//! finds and duels (`xp`); crafting, finds, duels and classes
//! count against daily caps (`xp.daily_caps`, per real UTC day) so nothing
//! can be farmed.
//!
//! Levels open options, not raw power: more spells to learn
//! (`data/spells.json` unlocks) and more dueling loadout slots
//! (`loadout_slots`). All four gear slots are open from level 1; an item's
//! `required_level` is checked when it is equipped.

use serde::{Deserialize, Serialize};

use protocol::{LEVEL_MAX, Progress, XpSource};

/// The progression schema version this crate reads.
pub const PROGRESSION_VERSION: u32 = 1;

#[derive(Debug, thiserror::Error)]
pub enum ProgressionError {
    #[error("progression JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("progression is inconsistent: {0}")]
    Invalid(String),
}

/// A school year and the first level it covers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct YearTitle {
    pub year: u8,
    /// Display text ("First Year").
    pub name: String,
    pub from_level: u8,
}

/// From `level` on, a loadout has `slots` slots.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct SlotStep {
    pub level: u8,
    pub slots: u8,
}

/// A daily cap on one group of XP sources (`XpSource::cap_group`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DailyCap {
    pub group: String,
    pub xp: u32,
}

/// Where XP comes from and how much.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct XpRules {
    /// Attending a class to its results.
    pub class_attend: u32,
    /// The most a class's score adds on top (scaled by the score).
    pub class_bonus_max: u32,
    /// A craft gives this percentage of its recipe's skill XP as player XP.
    pub craft_pct: u8,
    /// An exploration find that turned something up.
    pub find: u32,
    pub duel_win: u32,
    pub duel_loss: u32,
    pub duel_draw: u32,
    pub daily_caps: Vec<DailyCap>,
}

/// `data/progression.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Progression {
    pub version: u32,
    /// `levels[k]` is the total XP level `k + 1` starts at (`levels[0]` = 0).
    pub levels: Vec<u32>,
    pub years: Vec<YearTitle>,
    pub xp: XpRules,
    pub loadout_slots: Vec<SlotStep>,
}

impl Progression {
    /// Parses and validates the progression rules.
    pub fn from_json(json: &str) -> Result<Progression, ProgressionError> {
        let p: Progression = serde_json::from_str(json)?;
        p.validate().map_err(ProgressionError::Invalid)?;
        Ok(p)
    }

    /// The level total XP `xp` reaches.
    pub fn level_for(&self, xp: u32) -> u8 {
        self.levels.iter().take_while(|&&start| start <= xp).count() as u8
    }

    /// The total XP `level` starts at (clamped to 1..=cap).
    pub fn level_start(&self, level: u8) -> u32 {
        let k = usize::from(level.clamp(1, LEVEL_MAX)) - 1;
        self.levels[k]
    }

    /// The total XP the level after `level` needs; `None` at the cap.
    pub fn next_level_xp(&self, level: u8) -> Option<u32> {
        self.levels.get(usize::from(level.max(1))).copied()
    }

    /// The school year of `level`.
    pub fn year(&self, level: u8) -> &YearTitle {
        self.years
            .iter()
            .rev()
            .find(|y| y.from_level <= level)
            .unwrap_or(&self.years[0])
    }

    /// Loadout slots at `level`.
    pub fn loadout_slots(&self, level: u8) -> u8 {
        self.loadout_slots
            .iter()
            .rev()
            .find(|s| s.level <= level)
            .map_or(0, |s| s.slots)
    }

    /// The daily cap of a source's group, if it has one.
    pub fn daily_cap(&self, source: XpSource) -> Option<(&'static str, u32)> {
        let group = source.cap_group()?;
        let cap = self.xp.daily_caps.iter().find(|c| c.group == group)?;
        Some((group, cap.xp))
    }

    /// Player XP a craft of a recipe worth `recipe_xp` skill XP gives.
    pub fn craft_xp(&self, recipe_xp: u32) -> u32 {
        recipe_xp.saturating_mul(u32::from(self.xp.craft_pct)) / 100
    }

    /// A class's bonus XP for a score of `score_pct` (0-100).
    pub fn class_bonus(&self, score_pct: u8) -> u32 {
        self.xp
            .class_bonus_max
            .saturating_mul(u32::from(score_pct.min(100)))
            / 100
    }

    /// What the HUD shows for total XP `xp`.
    pub fn progress(&self, xp: u32) -> Progress {
        let level = self.level_for(xp);
        Progress {
            level,
            xp,
            level_xp: self.level_start(level),
            next_level_xp: self.next_level_xp(level),
            year: self.year(level).year,
        }
    }

    fn validate(&self) -> Result<(), String> {
        if self.version != PROGRESSION_VERSION {
            return Err(format!(
                "unsupported version {} (expected {PROGRESSION_VERSION})",
                self.version
            ));
        }
        if self.levels.len() != usize::from(LEVEL_MAX) || self.levels[0] != 0 {
            return Err(format!("levels: {LEVEL_MAX} entries starting at 0"));
        }
        if self.levels.windows(2).any(|w| w[0] >= w[1]) {
            return Err("levels must rise".into());
        }
        // Years: 1..=7 in order, from level 1, every level covered.
        if self.years.is_empty() || self.years[0].from_level != 1 {
            return Err("years must start at level 1".into());
        }
        for (k, y) in self.years.iter().enumerate() {
            if usize::from(y.year) != k + 1 || y.name.is_empty() || y.from_level > LEVEL_MAX {
                return Err(format!(
                    "year {}: numbered in order, named, within the cap",
                    y.year
                ));
            }
        }
        if self
            .years
            .windows(2)
            .any(|w| w[0].from_level >= w[1].from_level)
        {
            return Err("years must start at rising levels".into());
        }
        if self
            .loadout_slots
            .first()
            .is_none_or(|s| s.level != 1 || s.slots == 0)
        {
            return Err("loadout slots must start at level 1 with at least one".into());
        }
        if self
            .loadout_slots
            .windows(2)
            .any(|w| w[0].level >= w[1].level || w[0].slots >= w[1].slots)
        {
            return Err("loadout slots must grow with level".into());
        }
        if self
            .loadout_slots
            .iter()
            .any(|s| usize::from(s.slots) > protocol::LOADOUT_MAX || s.level > LEVEL_MAX)
        {
            return Err("at most LOADOUT_MAX slots, within the level cap".into());
        }
        if self.xp.craft_pct > 100 {
            return Err("craft_pct is a percentage".into());
        }
        for source in XpSource::ALL {
            if let Some(group) = source.cap_group()
                && !self
                    .xp
                    .daily_caps
                    .iter()
                    .any(|c| c.group == group && c.xp > 0)
            {
                return Err(format!("no daily cap for {group}"));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHIPPED: &str = include_str!("../../../data/progression.json");

    fn shipped() -> Progression {
        Progression::from_json(SHIPPED).expect("the shipped progression is valid")
    }

    #[test]
    fn levels_follow_the_curve_to_the_cap() {
        let p = shipped();
        assert_eq!(p.level_for(0), 1);
        let l2 = p.levels[1];
        assert_eq!(p.level_for(l2 - 1), 1);
        assert_eq!(p.level_for(l2), 2);
        assert_eq!(p.level_for(u32::MAX), LEVEL_MAX);
        assert_eq!(p.next_level_xp(1), Some(l2));
        assert_eq!(p.next_level_xp(LEVEL_MAX), None);
        let top = p.progress(p.levels[19] + 5);
        assert_eq!((top.level, top.next_level_xp, top.year), (20, None, 7));
        let mid = p.progress(l2 + 1);
        assert_eq!((mid.level, mid.level_xp), (2, l2));
    }

    #[test]
    fn school_years_are_three_levels_each() {
        let p = shipped();
        let years: Vec<u8> = (1..=LEVEL_MAX).map(|l| p.year(l).year).collect();
        assert_eq!(
            years,
            [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 5, 6, 6, 6, 7, 7]
        );
    }

    #[test]
    fn loadouts_grow_with_level_and_caps_exist() {
        let p = shipped();
        assert_eq!(p.loadout_slots(1), 3);
        assert_eq!(p.loadout_slots(4), 3);
        assert_eq!(p.loadout_slots(5), 4);
        assert_eq!(p.loadout_slots(10), 5);
        assert_eq!(p.loadout_slots(20), 5);
        assert!(p.daily_cap(XpSource::DuelWin).is_some());
        assert_eq!(
            p.daily_cap(XpSource::DuelWin).map(|c| c.0),
            p.daily_cap(XpSource::DuelLoss).map(|c| c.0)
        );
        // About six full classes a day, attendance
        // and bonus together; skill XP is not capped.
        assert_eq!(p.daily_cap(XpSource::ClassAttend), Some(("class", 840)));
        assert_eq!(
            p.daily_cap(XpSource::ClassBonus),
            p.daily_cap(XpSource::ClassAttend)
        );
        assert_eq!(
            6 * (p.xp.class_attend + p.xp.class_bonus_max),
            p.daily_cap(XpSource::ClassAttend).unwrap().1
        );
        assert_eq!(p.class_bonus(100), p.xp.class_bonus_max);
        assert_eq!(p.class_bonus(0), 0);
        assert!(p.craft_xp(40) <= 40);
    }

    #[test]
    fn bad_curves_are_refused() {
        let v: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        let err = |v: &serde_json::Value| {
            Progression::from_json(&v.to_string())
                .unwrap_err()
                .to_string()
        };
        let mut flat = v.clone();
        flat["levels"][5] = flat["levels"][4].clone();
        assert!(err(&flat).contains("rise"));
        let mut short = v.clone();
        short["levels"].as_array_mut().unwrap().pop();
        assert!(err(&short).contains("entries"));
        let mut caps = v.clone();
        caps["xp"]["daily_caps"] = serde_json::json!([]);
        assert!(err(&caps).contains("daily cap"));
        let mut slots = v;
        slots["loadout_slots"][1]["slots"] = 2.into();
        assert!(err(&slots).contains("grow"));
    }
}
