//! The wayfinding trail's destinations: `data/world/key_places.json`
//! ([`KeyPlaces`]).
//!
//! The client's "Find my way" list and the timetable's class entries lead a
//! glowing trail to one of these. Key places only: the dorms and common
//! rooms and the Owl Order Catalogue stands (each for its own house), the
//! Owl Exchange, the Great Hall, the
//! Entrance Hall, the Dueling Club, each class door and the
//! grounds' places. Nothing meant to be
//! discovered (find spots, secret passages) is ever a key place, and the
//! checks here refuse a find spot as a target. The path itself is the
//! client's (the walkable-surface graph in `crate::nav`, built for the
//! player's house with `Layout::for_house`), never the server's.
//!
//! Display names are data (IP names in data only, CONTRIBUTING.md).

use std::collections::HashSet;

use serde::{Deserialize, Serialize};

use protocol::House;

use crate::clock::Schedule;
use crate::layout::{InteractableKind, Layout};
use crate::world::World;

/// The key places schema version this crate reads.
pub const KEY_PLACES_VERSION: u32 = 1;

#[derive(Debug, thiserror::Error)]
pub enum KeyPlacesError {
    #[error("key places JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("key places are inconsistent: {0}")]
    Invalid(String),
}

/// What kind of place it is (the client groups and words the list by it:
/// "Your dorm", "Your common room").
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PlaceGroup {
    Dorm,
    CommonRoom,
    OwlExchange,
    /// The Owl Order Catalogue's stand in the house's own common room
    /// (one per house).
    OwlOrder,
    GreatHall,
    EntranceHall,
    DuelingClub,
    /// A class door (`class` names the class).
    Class,
    /// A place on the grounds (Groundskeeper's Cottage, the Lake Shore, the
    /// Boathouse; the Owl Loft).
    Grounds,
    /// A place around the castle (the Hospital Wing, the Clock
    /// Tower Courtyard).
    Castle,
}

/// Where the trail ends: a castle interactable (standing at it), a
/// landmark, or a point on walkable ground.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PlaceTarget {
    Interactable(String),
    Landmark(String),
    Pos([f32; 3]),
}

/// One destination.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KeyPlace {
    /// Stable id (the client may remember the last one).
    pub id: String,
    pub group: PlaceGroup,
    /// Display name ("Potions Classroom").
    pub name: String,
    /// Offered only to this house (lower-case id), whose gated rooms the
    /// trail may cross; `None`: everyone.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub house: Option<String>,
    /// The scheduled class whose door this is (`group` class).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub class: Option<String>,
    pub target: PlaceTarget,
}

impl KeyPlace {
    pub fn house(&self) -> Option<House> {
        self.house.as_deref().and_then(House::from_id)
    }

    /// Whether a character of `house` is offered it.
    pub fn offered_to(&self, house: House) -> bool {
        self.house().is_none_or(|h| h == house)
    }

    /// Where the trail ends in `layout` (the target's point), if it exists.
    pub fn resolve(&self, layout: &Layout) -> Option<[f32; 3]> {
        match &self.target {
            PlaceTarget::Interactable(id) => layout.interactable(id).map(|i| i.pos),
            PlaceTarget::Landmark(id) => {
                layout.landmarks.iter().find(|l| l.id == *id).map(|l| l.pos)
            }
            PlaceTarget::Pos(p) => Some(*p),
        }
    }
}

/// `data/world/key_places.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KeyPlaces {
    pub version: u32,
    pub places: Vec<KeyPlace>,
}

impl KeyPlaces {
    pub fn from_json(json: &str) -> Result<KeyPlaces, KeyPlacesError> {
        let k: KeyPlaces = serde_json::from_str(json)?;
        if k.version != KEY_PLACES_VERSION {
            return Err(KeyPlacesError::Invalid(format!(
                "unsupported version {}",
                k.version
            )));
        }
        Ok(k)
    }

    pub fn get(&self, id: &str) -> Option<&KeyPlace> {
        self.places.iter().find(|p| p.id == id)
    }

    /// The places a character of `house` is offered, in file order.
    pub fn offered_to(&self, house: House) -> impl Iterator<Item = &KeyPlace> {
        self.places.iter().filter(move |p| p.offered_to(house))
    }

    /// The class door place of `class` (the timetable's trail).
    pub fn class_door(&self, class: &str) -> Option<&KeyPlace> {
        self.places
            .iter()
            .find(|p| p.group == PlaceGroup::Class && p.class.as_deref() == Some(class))
    }

    /// The checks against the castle and the timetable: unique ids, every
    /// target exists and stands on walkable ground, nothing to discover
    /// (no find spot, no point within a find spot's reach), a place on a
    /// house's gated ground is that house's only, one dorm and one common
    /// room per house, the four public places, and a class door place for
    /// every scheduled class at its door.
    pub fn validate_with(&self, layout: &Layout, schedule: &Schedule) -> Result<(), String> {
        let world = World::new(layout.clone()).map_err(|e| e.to_string())?;
        let mut ids = HashSet::new();
        for p in &self.places {
            let id = &p.id;
            if !crate::clock::valid_id(id) || !ids.insert(id.as_str()) {
                return Err(format!("place {id:?}: ids are unique [a-z0-9_] ids"));
            }
            if p.name.trim().is_empty() || p.name.len() > 64 {
                return Err(format!("{id}: a name of 1-64 bytes"));
            }
            if p.house.is_some() && p.house().is_none() {
                return Err(format!("{id}: unknown house {:?}", p.house));
            }
            if let PlaceTarget::Interactable(t) = &p.target {
                let i = layout
                    .interactable(t)
                    .ok_or_else(|| format!("{id}: unknown interactable {t:?}"))?;
                if matches!(
                    i.kind,
                    InteractableKind::FindSpot | InteractableKind::WishingRoomDoor
                ) {
                    return Err(format!("{id}: {t} is meant to be discovered"));
                }
                if i.house.is_some() && i.house != p.house {
                    return Err(format!("{id}: {t} is {:?}'s only", i.house));
                }
            }
            let pos = p
                .resolve(layout)
                .ok_or_else(|| format!("{id}: unknown target {:?}", p.target))?;
            let ground = world
                .ground_at(pos[0], pos[2], pos[1])
                .ok_or_else(|| format!("{id}: {pos:?} is not on walkable ground"))?;
            let gate = &world.surface(ground.surface).house;
            if gate.is_some() && *gate != p.house {
                return Err(format!("{id}: stands on {gate:?}'s ground"));
            }
            // A moving flight is not there in every staircase state.
            if world.surface(ground.surface).moving.is_some() {
                return Err(format!("{id}: stands on a moving staircase"));
            }
            // The trail never leads to a secret.
            if world.surface(ground.surface).secret {
                return Err(format!("{id}: stands on a secret passage"));
            }
            if layout
                .interactables
                .iter()
                .any(|i| i.kind == InteractableKind::FindSpot && i.in_reach(pos))
            {
                return Err(format!("{id}: within reach of a find spot"));
            }
            match (p.group, &p.class) {
                (PlaceGroup::Class, Some(class)) => {
                    let c = schedule
                        .class(class)
                        .ok_or_else(|| format!("{id}: {class} is not scheduled"))?;
                    if p.target != PlaceTarget::Interactable(c.door.clone()) {
                        return Err(format!("{id}: the trail ends at {class}'s door"));
                    }
                }
                (PlaceGroup::Class, None) => return Err(format!("{id}: which class?")),
                (_, Some(_)) => return Err(format!("{id}: only class doors name a class")),
                (_, None) => {}
            }
        }
        for house in House::ALL {
            for group in [
                PlaceGroup::Dorm,
                PlaceGroup::CommonRoom,
                PlaceGroup::OwlOrder,
            ] {
                let n = self
                    .places
                    .iter()
                    .filter(|p| p.group == group && p.house() == Some(house))
                    .count();
                if n != 1 {
                    return Err(format!("{house:?}: {n} places of group {group:?}"));
                }
            }
        }
        for group in [
            PlaceGroup::OwlExchange,
            PlaceGroup::GreatHall,
            PlaceGroup::EntranceHall,
            PlaceGroup::DuelingClub,
        ] {
            if !self
                .places
                .iter()
                .any(|p| p.group == group && p.house.is_none())
            {
                return Err(format!("no public place of group {group:?}"));
            }
        }
        for c in &schedule.classes {
            if self.class_door(&c.id).is_none() {
                return Err(format!("class {}: no key place at its door", c.id));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHIPPED: &str = include_str!("../../../data/world/key_places.json");
    const LAYOUT: &str = include_str!("../../../data/world/layout.json");
    const SCHEDULE: &str = include_str!("../../../data/classes/schedule.json");

    fn shipped() -> (KeyPlaces, Layout, Schedule) {
        (
            KeyPlaces::from_json(SHIPPED).unwrap(),
            Layout::from_json(LAYOUT).unwrap(),
            Schedule::from_json(SCHEDULE).unwrap(),
        )
    }

    #[test]
    fn the_shipped_key_places_are_valid_and_only_key_places() {
        let (k, layout, schedule) = shipped();
        k.validate_with(&layout, &schedule).unwrap();
        // Each house is offered its own dorm and common room and no other.
        for house in House::ALL {
            let mine: Vec<&KeyPlace> = k.offered_to(house).collect();
            let dorms = mine.iter().filter(|p| p.group == PlaceGroup::Dorm);
            assert_eq!(dorms.count(), 1, "{house:?}");
            assert!(mine.iter().all(|p| p.offered_to(house)));
            // Every target stands on ground the house's own layout keeps.
            let theirs = World::new(layout.for_house(house)).unwrap();
            for p in &mine {
                let pos = p.resolve(&layout).unwrap();
                assert!(
                    theirs.ground_at(pos[0], pos[2], pos[1]).is_some(),
                    "{house:?}: {}",
                    p.id
                );
            }
        }
        assert_eq!(
            k.class_door("potions").map(|p| p.target.clone()),
            Some(PlaceTarget::Interactable("potions_classroom_door".into()))
        );
    }

    #[test]
    fn places_to_discover_are_refused() {
        let (k, layout, schedule) = shipped();
        let find = layout
            .interactables
            .iter()
            .find(|i| i.kind == InteractableKind::FindSpot && i.house.is_none())
            .unwrap();
        let mut spot = k.clone();
        spot.places[0].target = PlaceTarget::Interactable(find.id.clone());
        spot.places[0].house = None;
        assert!(spot.validate_with(&layout, &schedule).is_err());
        let mut near = k.clone();
        let g = near
            .places
            .iter_mut()
            .find(|p| p.group == PlaceGroup::GreatHall)
            .unwrap();
        g.target = PlaceTarget::Pos(find.pos);
        assert!(
            near.validate_with(&layout, &schedule)
                .unwrap_err()
                .contains("find spot")
        );
        let mut nowhere = k.clone();
        nowhere.places[0].target = PlaceTarget::Pos([0.0, 500.0, 0.0]);
        assert!(nowhere.validate_with(&layout, &schedule).is_err());
        let mut no_class = k;
        no_class
            .places
            .retain(|p| p.class.as_deref() != Some("charms"));
        assert!(
            no_class
                .validate_with(&layout, &schedule)
                .unwrap_err()
                .contains("charms")
        );
    }
}
