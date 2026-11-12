//! Dorm rooms: persistence (`dorms`, `dorm_placements`, and moving furniture
//! `items` between `inventory` and `dorm`) and placement validation with
//! `sim_core::placement`, for the edits that arrive over the WebSocket
//! (`ClientMsg::DormPlace`, `DormMove`, `DormRemove`, `DormSetOpen`).
//!
//! - [`shells`]: the four room shells (instance layouts), loaded at start.
//! - [`rules`]: the placement checks on wire values (pure).
//! - [`store`]: the database side, run off the world thread.
//!
//! Owned by the realtime side (docs/architecture.md), like
//! `crate::realtime`, which runs the dorm instances (`realtime::instances`).
//! Database work happens off the world thread; the world applies an edit (and
//! broadcasts `DormFurniture`) once it is stored.

pub mod rules;
pub mod shells;
pub mod store;

pub use shells::DormShells;

/// Instance layout id of a house's dorm room: `data/world/instances/<id>.json`.
pub fn layout_id(house: protocol::House) -> String {
    format!("dorm_{}", house.id())
}

#[cfg(test)]
mod tests {
    use protocol::House;

    #[test]
    fn every_house_has_a_dorm_layout() {
        let dir =
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/instances");
        for house in House::ALL {
            let path = dir.join(format!("{}.json", super::layout_id(house)));
            let json = std::fs::read_to_string(&path).expect("dorm layout exists");
            sim_core::World::from_json(&json).expect("dorm layout is valid");
        }
    }
}
