//! The castle's secrets and the map's reveal rule:
//! `data/world/secrets.json` ([`SecretsFile`], validated against the castle
//! layout by [`Secrets::new`]).
//!
//! - **Hidden passages** ([`Secret`]): each groups layout surfaces with
//!   `secret: true` (every one of them belongs to exactly one secret) and
//!   names its two doors. The first time a character stands on any of its
//!   surfaces, it **discovers** the secret ([`Secrets::discovers`]): once per
//!   character, with its reward ([`Reward`]: XP, a House Cup point from the
//!   `find` source under its daily cap, a recipe for two of them).
//! - **The map's reveal rule** ([`reveal_at`]): a place is revealed whole when
//!   you stand on it: the surface under you (the same ground query moves
//!   use). Reveals are keyed by the surface's stable string id. A secret's
//!   surfaces reveal with it, so the map never draws an unfound passage.
//! - **The Wishing Room**: [`crate::wishing_room`].
//!
//! Secret in play, not in code: the client knows every surface (it predicts
//! moves on them), so `secret` means never on the Monstra Viam trail
//! ([`crate::nav::trail_allows`]), never on the map and never named until found.

use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};

use crate::layout::{Layout, Surface, SurfaceKind};
use crate::wishing_room::WishingRoomRules;
use crate::world::World;

/// The secrets schema version this crate reads.
pub const SECRETS_VERSION: u32 = 1;

/// The walk-through pieces a passage's public ends may have (the art's
/// `castle_kit/grid.py` `ENTRANCE_STYLES`).
pub const ENTRANCES: [&str; 5] = ["tapestry", "panel", "painting", "statue", "wall"];

/// The most XP one secret may give.
pub const REWARD_XP_MAX: u32 = 1_000;

#[derive(Debug, thiserror::Error)]
pub enum SecretsError {
    #[error("secrets JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("secrets are inconsistent: {0}")]
    Invalid(String),
}

fn fail<T>(m: String) -> Result<T, SecretsError> {
    Err(SecretsError::Invalid(m))
}

/// What finding a secret gives, once per character.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Reward {
    /// XP (source `explore`, no daily cap).
    pub xp: u32,
    /// A recipe learned (`data/crafting/recipes.json` id), if any.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recipe: Option<u16>,
}

/// One hidden passage.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Secret {
    /// Stable id (`discoveries.secret`, `GET /api/map`, `ServerMsg::Discovered`).
    pub id: String,
    /// Stable number from 1: the reward ledgers' `ref_id`.
    pub num: u16,
    /// Our own name, shown only once found.
    pub name: String,
    /// The walk-through piece on its public ends ([`ENTRANCES`]).
    pub entrance: String,
    /// Every layout surface of the passage (all `secret: true`).
    pub surfaces: Vec<String>,
    /// Its two door surfaces (in the walls of the rooms it joins).
    pub doors: Vec<String>,
    pub reward: Reward,
}

/// `data/world/secrets.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SecretsFile {
    pub version: u32,
    pub secrets: Vec<Secret>,
    pub wishing_room: WishingRoomRules,
}

/// The validated secrets with their lookups.
#[derive(Debug, Clone)]
pub struct Secrets {
    file: SecretsFile,
    /// Surface id -> index into `file.secrets`.
    by_surface: HashMap<String, usize>,
}

impl Secrets {
    /// Parses and validates `data/world/secrets.json` against the castle layout
    /// (the whole file, both positions of every moving flight).
    pub fn from_json(json: &str, layout: &Layout) -> Result<Secrets, SecretsError> {
        Secrets::new(serde_json::from_str(json)?, layout)
    }

    pub fn new(file: SecretsFile, layout: &Layout) -> Result<Secrets, SecretsError> {
        if file.version != SECRETS_VERSION {
            return fail(format!("unsupported version {}", file.version));
        }
        let surface = |id: &str| layout.surfaces.iter().find(|s| s.id == id);
        let mut ids = HashSet::new();
        let mut nums = HashSet::new();
        let mut by_surface = HashMap::new();
        for (k, s) in file.secrets.iter().enumerate() {
            let id = &s.id;
            if !crate::clock::valid_id(id) || !ids.insert(id.as_str()) {
                return fail(format!("secret {id:?}: ids are unique [a-z0-9_] ids"));
            }
            if s.num == 0 || !nums.insert(s.num) {
                return fail(format!("{id}: num is unique and at least 1"));
            }
            if s.name.trim().is_empty() || s.name.len() > 64 {
                return fail(format!("{id}: a name of 1-64 bytes"));
            }
            if !ENTRANCES.contains(&s.entrance.as_str()) {
                return fail(format!("{id}: entrance is one of {ENTRANCES:?}"));
            }
            if s.reward.xp > REWARD_XP_MAX {
                return fail(format!("{id}: at most {REWARD_XP_MAX} XP"));
            }
            if s.surfaces.is_empty() {
                return fail(format!("{id}: no surfaces"));
            }
            for sid in &s.surfaces {
                let Some(found) = surface(sid) else {
                    return fail(format!("{id}: unknown surface {sid:?}"));
                };
                if !found.secret {
                    return fail(format!("{id}: surface {sid:?} is not marked secret"));
                }
                if by_surface.insert(sid.clone(), k).is_some() {
                    return fail(format!("surface {sid:?} is in two secrets"));
                }
            }
            if s.doors.len() != 2
                || s.doors[0] == s.doors[1]
                || s.doors.iter().any(|d| {
                    !s.surfaces.contains(d)
                        || surface(d).is_none_or(|x| x.kind != SurfaceKind::Door)
                })
            {
                return fail(format!("{id}: two door surfaces of its own"));
            }
        }
        if let Some(stray) = layout
            .surfaces
            .iter()
            .find(|s| s.secret && !by_surface.contains_key(&s.id))
        {
            return fail(format!(
                "secret surface {:?} belongs to no secret",
                stray.id
            ));
        }
        file.wishing_room
            .validate(layout)
            .map_err(SecretsError::Invalid)?;
        Ok(Secrets { file, by_surface })
    }

    pub fn file(&self) -> &SecretsFile {
        &self.file
    }

    pub fn secrets(&self) -> &[Secret] {
        &self.file.secrets
    }

    pub fn get(&self, id: &str) -> Option<&Secret> {
        self.file.secrets.iter().find(|s| s.id == id)
    }

    /// The secret a surface (by id) belongs to.
    pub fn secret_of(&self, surface: &str) -> Option<&Secret> {
        self.by_surface.get(surface).map(|&k| &self.file.secrets[k])
    }

    /// The first-step rule: standing on `surface`, a character that has not
    /// found its secret yet (`found` says which it has) discovers it now.
    pub fn discovers(&self, surface: &str, found: impl Fn(&str) -> bool) -> Option<&Secret> {
        self.secret_of(surface).filter(|s| !found(&s.id))
    }

    pub fn wishing_room(&self) -> &WishingRoomRules {
        &self.file.wishing_room
    }
}

/// The map's reveal rule: the surface a character at `pos` stands on (its
/// whole rectangle is revealed; the key is its id).
pub fn reveal_at(world: &World, pos: [f32; 3]) -> Option<&Surface> {
    world
        .ground_at(pos[0], pos[2], pos[1])
        .map(|g| world.surface(g.surface))
}

#[cfg(test)]
mod tests {
    use super::*;

    const LAYOUT: &str = include_str!("../../../data/world/layout.json");
    const SECRETS: &str = include_str!("../../../data/world/secrets.json");

    fn shipped() -> (Layout, Secrets) {
        let layout = Layout::from_json(LAYOUT).unwrap();
        let secrets = Secrets::from_json(SECRETS, &layout).unwrap();
        (layout, secrets)
    }

    #[test]
    fn the_shipped_secrets() {
        let (layout, secrets) = shipped();
        assert_eq!(secrets.secrets().len(), 5);
        let recipes = secrets
            .secrets()
            .iter()
            .filter(|s| s.reward.recipe.is_some())
            .count();
        assert_eq!(recipes, 2, "a recipe for two of the five");
        // Every secret surface is in one secret, and every passage is its own cell.
        let mut cells = HashSet::new();
        for s in secrets.secrets() {
            let own: HashSet<&str> = s
                .surfaces
                .iter()
                .map(|id| {
                    layout
                        .surfaces
                        .iter()
                        .find(|x| &x.id == id)
                        .unwrap()
                        .cell
                        .as_str()
                })
                .collect();
            assert_eq!(own.len(), 1, "{}", s.id);
            assert!(cells.insert(own.into_iter().next().unwrap()));
        }
    }

    #[test]
    fn the_first_step_discovers_once() {
        let (layout, secrets) = shipped();
        let world = World::new(layout.clone()).unwrap();
        // The tapestry door at L5: standing on it reveals it and discovers its secret.
        let pos = [-70.0, 45.0, 8.0];
        let here = reveal_at(&world, pos).unwrap();
        assert_eq!(here.id, "sp_tapestry_door_l5");
        let s = secrets.discovers(&here.id, |_| false).unwrap();
        assert_eq!(s.id, "weavers_stair");
        assert_eq!(s.reward.recipe, Some(20));
        assert!(
            secrets
                .discovers(&here.id, |id| id == "weavers_stair")
                .is_none()
        );
        // Public ground discovers nothing; it is revealed like any place.
        let corridor = reveal_at(&world, [-76.0, 45.0, 2.0]).unwrap();
        assert_eq!(corridor.id, "l5_corridor_west");
        assert!(secrets.discovers(&corridor.id, |_| false).is_none());
        assert!(reveal_at(&world, [500.0, 0.0, 500.0]).is_none());
    }

    #[test]
    fn rejects_bad_files() {
        let (layout, secrets) = shipped();
        let bad = |f: &dyn Fn(&mut SecretsFile)| {
            let mut file = secrets.file().clone();
            f(&mut file);
            Secrets::new(file, &layout).unwrap_err().to_string()
        };
        assert!(bad(&|f| f.version = 2).contains("version"));
        assert!(bad(&|f| f.secrets[1].id = f.secrets[0].id.clone()).contains("unique"));
        assert!(bad(&|f| f.secrets[1].num = 1).contains("num"));
        assert!(bad(&|f| f.secrets[0].entrance = "trapdoor".into()).contains("entrance"));
        assert!(
            bad(&|f| f.secrets[0].surfaces.push("l5_corridor_west".into()))
                .contains("not marked secret")
        );
        assert!(
            bad(&|f| {
                let s = f.secrets[0].surfaces.pop().unwrap();
                f.secrets[1].surfaces.push(s);
            })
            .contains("door")
        );
        assert!(
            bad(&|f| {
                f.secrets[0].surfaces.retain(|s| s != "sp_tapestry_stair");
            })
            .contains("belongs to no secret")
        );
        assert!(
            bad(&|f| {
                f.secrets[0].doors.pop();
            })
            .contains("two door")
        );
        assert!(bad(&|f| f.secrets[0].reward.xp = 5_000).contains("XP"));
        assert!(bad(&|f| f.wishing_room.passes = 0).contains("wishing_room"));
    }
}
