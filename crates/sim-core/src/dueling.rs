//! Dueling rules and venues: `data/dueling/rules.json`
//! ([`DuelRules`]) and `data/dueling/venues.json` ([`Venues`]). The duel
//! itself is [`crate::duel`]; spell numbers are in `data/spells.json`.
//!
//! Dueling is opt-in and happens only in dueling areas: a venue names an
//! area of the castle layout (`Layout.areas`, kind `dueling_club`) where
//! players may challenge each other and watch, and the arena layout duels
//! are fought in (an instance: the stage plus the spectators' edges).
//! Nothing is ever lost in a duel. Potions cannot be drunk in an arena, and
//! the Pick-Me-Up Tonic's speed and the shrinking or swelling scale are
//! suspended there (movement is validated at `move_speed_pct` instead).

use serde::{Deserialize, Serialize};

/// The dueling schema version this crate reads.
pub const DUELING_VERSION: u32 = 1;

#[derive(Debug, thiserror::Error)]
pub enum DuelingError {
    #[error("dueling JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("dueling data is inconsistent: {0}")]
    Invalid(String),
}

/// What duels give besides XP (`data/progression.json` `xp.duel_*`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DuelRewardRules {
    /// House points for a win...
    pub win_house_points: u16,
    /// ...for at most this many wins per character per UTC day.
    pub house_point_wins_daily: u16,
    /// A duel that fought for less than this (a quick forfeit) gives no XP
    /// and no points.
    pub min_fight_ms: u32,
    /// Rewarded duels between the same two characters per UTC day.
    pub pair_daily_max: u8,
}

/// `data/dueling/rules.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DuelRules {
    pub version: u32,
    /// Each duelist starts with this much Poise; the first at 0 loses.
    pub poise_max: u16,
    /// Fighting time; then the higher Poise wins (equal is a draw).
    pub time_limit_ms: u32,
    /// From entering the arena to the first cast.
    pub countdown_ms: u32,
    /// The duel steps at the world's tick (20 Hz = 50 ms).
    pub tick_ms: u32,
    /// A duelist's body for hit tests (metres).
    pub duelist_radius_m: f32,
    /// Projectiles vanish after flying this far (metres).
    pub max_range_m: f32,
    /// Duelists move at this percentage of normal speed in an arena.
    pub move_speed_pct: u16,
    /// After the result, everyone returns to the club after this long.
    pub return_delay_ms: u32,
    /// A challenge may be answered for this long (seconds).
    pub challenge_secs: u16,
    /// A duelist who disconnects has this long to come back (the duel goes
    /// on without them, standing still); then they lose by `Disconnect`.
    #[serde(default = "default_disconnect_grace_ms")]
    pub disconnect_grace_ms: u32,
    pub rewards: DuelRewardRules,
}

fn default_disconnect_grace_ms() -> u32 {
    10_000
}

impl DuelRules {
    pub fn from_json(json: &str) -> Result<DuelRules, DuelingError> {
        let r: DuelRules = serde_json::from_str(json)?;
        r.validate().map_err(DuelingError::Invalid)?;
        Ok(r)
    }

    fn validate(&self) -> Result<(), String> {
        if self.version != DUELING_VERSION {
            return Err(format!(
                "unsupported version {} (expected {DUELING_VERSION})",
                self.version
            ));
        }
        if self.poise_max == 0 || self.time_limit_ms < 10_000 || self.tick_ms == 0 {
            return Err("poise, a time limit of at least 10 s and a tick are needed".into());
        }
        if !self.time_limit_ms.is_multiple_of(self.tick_ms)
            || !self.countdown_ms.is_multiple_of(self.tick_ms)
        {
            return Err("the countdown and time limit are whole ticks".into());
        }
        let positive = |v: f32| v.is_finite() && v > 0.0;
        if !positive(self.duelist_radius_m) || !positive(self.max_range_m) {
            return Err("radius and range must be positive".into());
        }
        if !(20..=protocol::SPEED_PCT_NORMAL).contains(&self.move_speed_pct) {
            return Err("move_speed_pct must be 20..=100".into());
        }
        if self.challenge_secs == 0 {
            return Err("challenges need time to answer".into());
        }
        Ok(())
    }
}

/// A standing place: position and facing.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Spot {
    pub pos: [f32; 3],
    pub yaw: f32,
}

/// The rectangle (x, z) duelists stand in.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct Ring {
    pub min: [f32; 2],
    pub max: [f32; 2],
}

/// A dueling venue.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Venue {
    /// Stable id (`Zone::Arena.venue`, spell unlocks `at`).
    pub id: String,
    /// Display name ("Dueling Club").
    pub name: String,
    /// The castle layout area (kind `dueling_club`) players challenge and
    /// watch from.
    pub area: String,
    /// The arena instance layout (`data/world/instances/<layout>.json`).
    pub arena_layout: String,
    /// Where duelists A (the challenger) and B start in the arena.
    pub duelists: [Spot; 2],
    /// Where duelists may stand (the stage); for checks and the client.
    pub ring: Ring,
    /// Where spectators appear.
    pub spectators: Spot,
    pub max_spectators: u16,
    /// Most duels this venue runs at once.
    pub max_arenas: u16,
}

/// `data/dueling/venues.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Venues {
    pub version: u32,
    pub venues: Vec<Venue>,
}

impl Venues {
    pub fn from_json(json: &str) -> Result<Venues, DuelingError> {
        let v: Venues = serde_json::from_str(json)?;
        v.validate().map_err(DuelingError::Invalid)?;
        Ok(v)
    }

    pub fn get(&self, id: &str) -> Option<&Venue> {
        self.venues.iter().find(|v| v.id == id)
    }

    /// The venue whose dueling area is `area`.
    pub fn by_area(&self, area: &str) -> Option<&Venue> {
        self.venues.iter().find(|v| v.area == area)
    }

    fn validate(&self) -> Result<(), String> {
        if self.version != DUELING_VERSION {
            return Err(format!(
                "unsupported version {} (expected {DUELING_VERSION})",
                self.version
            ));
        }
        let mut ids = std::collections::HashSet::new();
        for v in &self.venues {
            let id = &v.id;
            if !crate::clock::valid_id(id) || !ids.insert(id.as_str()) {
                return Err(format!("venue {id:?}: ids must be unique [a-z0-9_]"));
            }
            if !crate::clock::valid_id(&v.area) || !crate::clock::valid_id(&v.arena_layout) {
                return Err(format!("{id}: area and arena_layout are ids"));
            }
            let r = &v.ring;
            if !(r.min[0] < r.max[0] && r.min[1] < r.max[1]) {
                return Err(format!("{id}: the ring needs min < max"));
            }
            for s in v.duelists {
                let inside = (r.min[0]..=r.max[0]).contains(&s.pos[0])
                    && (r.min[1]..=r.max[1]).contains(&s.pos[2]);
                if !inside {
                    return Err(format!("{id}: duelists start inside the ring"));
                }
            }
            if v.max_spectators == 0 || v.max_arenas == 0 {
                return Err(format!("{id}: room for spectators and arenas"));
            }
        }
        Ok(())
    }

    /// Checks each venue against its arena layout (spots on its ground) and
    /// the castle layout (the area exists and is a dueling area).
    pub fn validate_with(
        &self,
        castle: &crate::layout::Layout,
        arena: impl Fn(&str) -> Option<crate::world::World>,
    ) -> Result<(), String> {
        for v in &self.venues {
            let area = castle
                .areas
                .iter()
                .find(|a| a.id == v.area)
                .ok_or_else(|| format!("{}: no castle area {:?}", v.id, v.area))?;
            if area.kind != crate::layout::AreaKind::DuelingClub {
                return Err(format!("{}: area {:?} is not a dueling area", v.id, v.area));
            }
            let world = arena(&v.arena_layout)
                .ok_or_else(|| format!("{}: no arena layout {:?}", v.id, v.arena_layout))?;
            for s in v.duelists.iter().chain([&v.spectators]) {
                let [x, y, z] = s.pos;
                if world.ground_at(x, z, y).is_none() {
                    return Err(format!("{}: a spot off the arena's ground", v.id));
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub const RULES: &str = include_str!("../../../data/dueling/rules.json");
    pub const VENUES: &str = include_str!("../../../data/dueling/venues.json");

    pub fn rules() -> DuelRules {
        DuelRules::from_json(RULES).expect("the shipped duel rules are valid")
    }

    #[test]
    fn the_shipped_venue_fits_the_castle_and_its_arena() {
        let r = rules();
        assert_eq!(r.poise_max, 100);
        assert_eq!(r.time_limit_ms, 90_000);
        let v = Venues::from_json(VENUES).unwrap();
        let club = v.get("dueling_club").unwrap();
        assert_eq!(v.by_area(&club.area).unwrap().id, club.id);
        let castle =
            crate::layout::Layout::from_json(include_str!("../../../data/world/layout.json"))
                .unwrap();
        let arena = include_str!("../../../data/world/instances/duel_arena.json");
        v.validate_with(&castle, |id| {
            (id == "duel_arena").then(|| crate::world::World::from_json(arena).unwrap())
        })
        .unwrap();
    }

    #[test]
    fn bad_dueling_data_is_refused() {
        let mut v: serde_json::Value = serde_json::from_str(RULES).unwrap();
        v["move_speed_pct"] = 150.into();
        assert!(DuelRules::from_json(&v.to_string()).is_err());
        let mut v: serde_json::Value = serde_json::from_str(VENUES).unwrap();
        v["venues"][0]["duelists"][0]["pos"] = serde_json::json!([99.0, 0.9, 0.0]);
        assert!(
            Venues::from_json(&v.to_string())
                .unwrap_err()
                .to_string()
                .contains("ring")
        );
    }
}
