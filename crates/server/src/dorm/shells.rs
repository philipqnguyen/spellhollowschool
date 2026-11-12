//! The four dorm room shells (`data/world/instances/dorm_<house id>.json`),
//! loaded once at start as walkable worlds.

use std::path::Path;
use std::sync::Arc;

use protocol::House;

use super::layout_id;

/// The shells compiled into the server, used when the instances directory
/// (`SHS_INSTANCES`) does not have them (tests, a binary run outside the repo).
const EMBEDDED: [(House, &str); 4] = [
    (
        House::Roarwing,
        include_str!("../../../../data/world/instances/dorm_roarwing.json"),
    ),
    (
        House::Burrowsby,
        include_str!("../../../../data/world/instances/dorm_burrowsby.json"),
    ),
    (
        House::Skytalon,
        include_str!("../../../../data/world/instances/dorm_skytalon.json"),
    ),
    (
        House::Serpentyne,
        include_str!("../../../../data/world/instances/dorm_serpentyne.json"),
    ),
];

/// One validated room world per house. Dorms have no house gates (only
/// friends get in), so everyone walks the same world.
#[derive(Debug, Clone)]
pub struct DormShells {
    worlds: [Arc<sim_core::World>; 4],
}

fn slot(house: House) -> usize {
    House::ALL
        .iter()
        .position(|&h| h == house)
        .expect("every house is in House::ALL")
}

impl DormShells {
    /// The shells the server was built with.
    pub fn embedded() -> Self {
        Self::from_json(|house| {
            EMBEDDED
                .iter()
                .find(|(h, _)| *h == house)
                .map(|(_, json)| (*json).to_owned())
                .ok_or_else(|| format!("no embedded shell for {}", house.id()))
        })
        .expect("the embedded dorm shells are valid (checked by tests)")
    }

    /// Reads `<dir>/dorm_<house id>.json` for every house. A directory that
    /// does not exist falls back to the embedded shells (logged); a file that
    /// exists but does not load is an error.
    pub fn load(dir: &Path) -> Result<Self, String> {
        if !dir.is_dir() {
            tracing::warn!(dir = %dir.display(), "no instances directory; using the built-in dorm shells");
            return Ok(Self::embedded());
        }
        Self::from_json(|house| {
            let path = dir.join(format!("{}.json", layout_id(house)));
            std::fs::read_to_string(&path)
                .map_err(|e| format!("cannot read {}: {e}", path.display()))
        })
    }

    fn from_json(mut read: impl FnMut(House) -> Result<String, String>) -> Result<Self, String> {
        let mut worlds = Vec::with_capacity(4);
        for house in House::ALL {
            let json = read(house)?;
            let world = sim_core::World::from_json(&json)
                .map_err(|e| format!("dorm shell {}: {e}", layout_id(house)))?;
            if world.spawns().is_empty() {
                return Err(format!("dorm shell {} has no spawn", layout_id(house)));
            }
            if !world
                .interactables()
                .iter()
                .any(|i| i.kind == sim_core::InteractableKind::DormExit)
            {
                return Err(format!("dorm shell {} has no dorm_exit", layout_id(house)));
            }
            worlds.push(Arc::new(world));
        }
        Ok(Self {
            worlds: worlds.try_into().expect("four houses"),
        })
    }

    /// The room world of `house`'s dorms.
    pub fn world(&self, house: House) -> &Arc<sim_core::World> {
        &self.worlds[slot(house)]
    }

    /// The furniture a new character of `house` finds placed in their room
    /// (the shell's `starter_furniture`: the Four-Poster Bed by its wall).
    pub fn starter_furniture(&self, house: House) -> &[sim_core::StarterPiece] {
        &self.world(house).layout().starter_furniture
    }
}

/// The embedded shells, parsed once: what the starter grant places a new
/// character's furniture by (`inventory::starter::grant`).
pub fn shipped() -> &'static DormShells {
    static SHELLS: std::sync::OnceLock<DormShells> = std::sync::OnceLock::new();
    SHELLS.get_or_init(DormShells::embedded)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shells_load_from_the_repository_and_embedded() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/instances");
        let loaded = DormShells::load(&dir).unwrap();
        let embedded = DormShells::embedded();
        for house in House::ALL {
            assert_eq!(
                loaded.world(house).spawns()[0].pos,
                embedded.world(house).spawns()[0].pos
            );
            assert!(!loaded.world(house).place_areas().is_empty());
        }
        // A missing directory falls back.
        assert!(DormShells::load(Path::new("/nonexistent/instances")).is_ok());
    }

    /// Every house's room starts with the starter Four-Poster Bed (a
    /// `starter` catalog item every house is granted), at a spot the
    /// placement rule and the way rule both take in the empty room: the
    /// room's first edit is never refused because of where the grant put it.
    #[test]
    fn the_starter_bed_fits_every_room() {
        use protocol::{PlacedFurniture, QPos, yaw_to_u8};

        use crate::catalog::shipped as content;
        use crate::dorm::rules;

        let c = &content().catalog;
        for house in House::ALL {
            let pieces = shipped().starter_furniture(house);
            assert_eq!(pieces.len(), 1, "{house:?}");
            let p = pieces[0];
            let bed = c.get(p.item).expect("a catalog item");
            assert_eq!(bed.name, "Four-Poster Bed");
            assert!(bed.is_starter_for(house), "{house:?}");
            let shape = rules::shape_of(c, p.item).unwrap();
            let shell = shipped().world(house);
            // Lower than the room: the burrow's 2.9 m walls take the 2.5 m bed.
            let ceiling = shell
                .surfaces()
                .iter()
                .filter_map(|s| s.ceiling)
                .fold(f32::MAX, f32::min);
            assert!(
                shape.height + 0.2 <= ceiling,
                "{house:?}: {} under {ceiling}",
                shape.height
            );
            let (pos, yaw) = (QPos::from_m(p.pos), yaw_to_u8(p.yaw));
            assert_eq!(
                rules::check(c, p.item, shell.place_areas(), pos, yaw),
                Ok(()),
                "{house:?}"
            );
            let way = rules::Way {
                shell: shell.clone(),
                room: Vec::new(),
                people: vec![shell.spawns()[0].pos],
            };
            assert_eq!(way.check(c, p.item, 1, pos, yaw), Ok(()), "{house:?}");
            // Placed, it is solid furniture like any other piece, and the way
            // in and out stays open.
            let placed = PlacedFurniture {
                item_id: 1,
                item_def: p.item,
                dyes: [0; 3],
                pos,
                yaw,
            };
            assert_eq!(rules::blockers(c, &[placed], None).len(), 1);
        }
    }
}
