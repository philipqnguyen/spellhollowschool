//! Instance layouts of the features' own instances: the class
//! rooms (`data/classes/schedule.json` `layout`) and the dueling arenas
//! (`data/dueling/venues.json` `arena_layout`), loaded once at start like
//! the dorm shells (`dorm::DormShells`): from `SHS_INSTANCES` when the files
//! are there, else the copies compiled into the server.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

/// The layouts compiled into the server.
const EMBEDDED: &[(&str, &str)] = &[
    (
        "class_charms",
        include_str!("../../../../data/world/instances/class_charms.json"),
    ),
    (
        "duel_arena",
        include_str!("../../../../data/world/instances/duel_arena.json"),
    ),
    (
        "class_potions",
        include_str!("../../../../data/world/instances/class_potions.json"),
    ),
    (
        "class_classroom",
        include_str!("../../../../data/world/instances/class_classroom.json"),
    ),
    // The art tools generate these rooms. History of Magic reuses
    // `class_charms`.
    (
        "class_defence",
        include_str!("../../../../data/world/instances/class_defence.json"),
    ),
    (
        "class_divination",
        include_str!("../../../../data/world/instances/class_divination.json"),
    ),
    (
        "class_astronomy",
        include_str!("../../../../data/world/instances/class_astronomy.json"),
    ),
    (
        "class_greenhouse",
        include_str!("../../../../data/world/instances/class_greenhouse.json"),
    ),
    (
        "class_paddock",
        include_str!("../../../../data/world/instances/class_paddock.json"),
    ),
    (
        "class_pitch",
        include_str!("../../../../data/world/instances/class_pitch.json"),
    ),
    // The Wishing Room's two rooms (data/world/secrets.json
    // `wishing_room.rooms`; the art tools regenerate them from their rooms,
    // keeping the ids: the spawn, `wishing_room_exit` and, in the Lost
    // Property Vault, the find spot `find_lost_property_vault`).
    (
        "lost_property_vault",
        include_str!("../../../../data/world/instances/lost_property_vault.json"),
    ),
    (
        "wishing_room_gathering",
        include_str!("../../../../data/world/instances/wishing_room_gathering.json"),
    ),
];

/// The Wishing Room's room layouts (`data/world/secrets.json`
/// `wishing_room.rooms[].layout`), in `WishingRoomKind` order.
pub const WISHING_ROOM_LAYOUTS: [&str; 2] = ["lost_property_vault", "wishing_room_gathering"];

/// The instance layout whose find spot is the Lost Property Vault's
/// daily find (`data/world/finds.json` lists it beside the castle's spots).
pub const LOST_PROPERTY_LAYOUT: &str = "lost_property_vault";

/// Every layout id the shipped content names.
pub fn layout_ids() -> Vec<String> {
    let content = crate::catalog::shipped();
    let mut ids: Vec<String> = content
        .schedule
        .classes
        .iter()
        .map(|c| c.layout.clone())
        .chain(content.venues.venues.iter().map(|v| v.arena_layout.clone()))
        .chain(WISHING_ROOM_LAYOUTS.iter().map(|id| (*id).to_owned()))
        .collect();
    ids.sort();
    ids.dedup();
    ids
}

/// One validated world per layout id.
#[derive(Debug, Clone, Default)]
pub struct InstanceLayouts {
    worlds: HashMap<String, Arc<sim_core::World>>,
    /// Each layout's open sky (empty for the indoor rooms).
    airs: HashMap<String, Arc<sim_core::Airspace>>,
}

impl InstanceLayouts {
    /// The layouts the server was built with.
    pub fn embedded() -> Self {
        Self::from_json(|id| {
            EMBEDDED
                .iter()
                .find(|(i, _)| *i == id)
                .map(|(_, json)| (*json).to_owned())
                .ok_or_else(|| format!("no embedded layout {id}"))
        })
        .expect("the embedded instance layouts are valid (checked by tests)")
    }

    /// Reads `<dir>/<id>.json` for every layout the content names; a missing
    /// directory falls back to the embedded layouts, a broken file is an error.
    pub fn load(dir: &Path) -> Result<Self, String> {
        if !dir.is_dir() {
            return Ok(Self::embedded());
        }
        Self::from_json(|id| {
            let path = dir.join(format!("{id}.json"));
            std::fs::read_to_string(&path)
                .map_err(|e| format!("cannot read {}: {e}", path.display()))
        })
    }

    fn from_json(mut read: impl FnMut(&str) -> Result<String, String>) -> Result<Self, String> {
        let mut worlds = HashMap::new();
        for id in layout_ids() {
            let json = read(&id)?;
            let world = sim_core::World::from_json(&json)
                .map_err(|e| format!("instance layout {id}: {e}"))?;
            if world.spawns().is_empty() {
                return Err(format!("instance layout {id} has no spawn"));
            }
            if !world
                .interactables()
                .iter()
                .any(|i| i.kind == sim_core::InteractableKind::InstanceExit)
            {
                return Err(format!("instance layout {id} has no instance_exit"));
            }
            worlds.insert(id, Arc::new(world));
        }
        // Every class's ring beats name rings its layout has.
        let content = crate::catalog::shipped();
        for slot in &content.schedule.classes {
            let (Some(class), Some(world)) = (content.class(&slot.id), worlds.get(&slot.layout))
            else {
                continue;
            };
            class
                .check_rings(world.layout())
                .map_err(|e| format!("instance layout {}: {e}", slot.layout))?;
        }
        let airs = worlds
            .iter()
            .map(|(id, w)| (id.clone(), Arc::new(sim_core::Airspace::new(w))))
            .collect();
        Ok(Self { worlds, airs })
    }

    pub fn get(&self, id: &str) -> Option<&Arc<sim_core::World>> {
        self.worlds.get(id)
    }

    /// The airspace of layout `id` (where a Flying class flies).
    pub fn air(&self, id: &str) -> Option<&Arc<sim_core::Airspace>> {
        self.airs.get(id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layouts_load_from_the_repository_and_embedded() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/instances");
        let loaded = InstanceLayouts::load(&dir).unwrap();
        let embedded = InstanceLayouts::embedded();
        for id in layout_ids() {
            assert_eq!(
                loaded.get(&id).unwrap().spawns()[0].pos,
                embedded.get(&id).unwrap().spawns()[0].pos
            );
        }
        assert!(InstanceLayouts::load(Path::new("/nonexistent")).is_ok());
        // Every venue's spots stand on its arena's ground.
        let content = crate::catalog::shipped();
        let castle =
            sim_core::Layout::from_json(include_str!("../../../../data/world/layout.json"))
                .unwrap();
        content
            .venues
            .validate_with(&castle, |id| {
                embedded
                    .get(id)
                    .map(|w| sim_core::World::new(w.layout().clone()).unwrap())
            })
            .unwrap();
    }
}
