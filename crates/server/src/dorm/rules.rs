//! Furniture placement checks for one room, on the wire's quantized values:
//! the room shell's placeable areas plus the table tops of what is already
//! placed (`sim_core::placement`). Pure, so the world thread, the store and
//! the tests share them.

use protocol::{DormErrorCode, PlacedFurniture, QPos, yaw_from_u8};
use std::sync::Arc;

use sim_core::{
    Blocker, Catalog, FurnitureShape, ItemKind, MoveParams, PlaceArea, PlaceSurface, World,
    check_placement, keeps_way,
};

/// The furniture shape of a catalog item, or `NotFurniture`.
pub fn shape_of(catalog: &Catalog, item_def: u16) -> Result<FurnitureShape, DormErrorCode> {
    catalog
        .get(item_def)
        .filter(|d| d.kind == ItemKind::Furniture)
        .and_then(|d| d.furniture.as_ref())
        .map(|f| f.shape())
        .ok_or(DormErrorCode::NotFurniture)
}

/// Where items may go in a room: the shell's areas plus the tops and shelves
/// of every placed item (turning with it; except `except`, the item being
/// moved, which cannot stand on itself).
pub fn room_areas(
    shell: &[PlaceArea],
    items: &[PlacedFurniture],
    catalog: &Catalog,
    except: Option<i64>,
) -> Vec<PlaceArea> {
    let mut areas = shell.to_vec();
    for it in items.iter().filter(|it| Some(it.item_id) != except) {
        areas.extend(item_tops(catalog, it));
    }
    areas
}

/// The tops and shelves of one placed item (none for most).
fn item_tops(catalog: &Catalog, it: &PlacedFurniture) -> Vec<PlaceArea> {
    catalog
        .get(it.item_def)
        .and_then(|d| d.furniture.as_ref())
        .map(|f| {
            f.top_areas(
                &format!("top_{}", it.item_id),
                it.pos.to_m(),
                yaw_from_u8(it.yaw),
            )
        })
        .unwrap_or_default()
}

/// Whether anything placed rests on `item_id`'s top or shelves: a table
/// item that fits one of them where it stands. Such a piece may not be moved
/// or put away (`DormErrorCode::NotEmpty`) until that is taken off, so
/// nothing is ever left floating. The client asks the same before lifting.
pub fn holds_items(catalog: &Catalog, items: &[PlacedFurniture], item_id: i64) -> bool {
    let Some(it) = items.iter().find(|it| it.item_id == item_id) else {
        return false;
    };
    let tops = item_tops(catalog, it);
    if tops.is_empty() {
        return false;
    }
    items.iter().filter(|o| o.item_id != item_id).any(|o| {
        let Ok(shape) = shape_of(catalog, o.item_def) else {
            return false;
        };
        shape.surface == PlaceSurface::Table
            && check_placement(&tops, &shape, o.pos.to_m(), yaw_from_u8(o.yaw)).is_ok()
    })
}

/// The solid furniture of a room's placed items (all but `except`, the
/// piece being moved): `FurnitureDef::blocker` of each (floor pieces taller
/// than a step), ids `furniture/<item id>`.
pub fn blockers(catalog: &Catalog, items: &[PlacedFurniture], except: Option<i64>) -> Vec<Blocker> {
    items
        .iter()
        .filter(|it| Some(it.item_id) != except)
        .filter_map(|it| blocker(catalog, it.item_def, it.item_id, it.pos, it.yaw))
        .collect()
}

fn blocker(catalog: &Catalog, item_def: u16, item_id: i64, pos: QPos, yaw: u8) -> Option<Blocker> {
    catalog
        .get(item_def)
        .and_then(|d| d.furniture.as_ref())
        .and_then(|f| {
            f.blocker(
                &format!("furniture/{item_id}"),
                pos.to_m(),
                yaw_from_u8(yaw),
            )
        })
}

/// A room's walkable world: its shell with its placed furniture solid. Every
/// member's moves are checked against it, and the client predicts with the
/// same (`SimWorld.setFurniture`).
pub fn furnished(shell: &World, catalog: &Catalog, items: &[PlacedFurniture]) -> World {
    shell.with_solids(blockers(catalog, items, None))
}

/// What an edit must leave open (`sim_core::keeps_way`), as the room stood
/// when it started: the shell, the other solid pieces and where its people
/// stand.
#[derive(Debug, Clone)]
pub struct Way {
    pub shell: Arc<World>,
    pub room: Vec<Blocker>,
    pub people: Vec<[f32; 3]>,
}

impl PartialEq for Way {
    fn eq(&self, other: &Way) -> bool {
        Arc::ptr_eq(&self.shell, &other.shell)
            && self.room == other.room
            && self.people == other.people
    }
}

impl Way {
    /// `BlocksWay` if `item_def` at `pos`, `yaw` would stand on someone or
    /// the entry, or cut the way out or to someone; a piece that is not
    /// solid always may.
    pub fn check(
        &self,
        catalog: &Catalog,
        item_def: u16,
        item_id: i64,
        pos: QPos,
        yaw: u8,
    ) -> Result<(), DormErrorCode> {
        let Some(piece) = blocker(catalog, item_def, item_id, pos, yaw) else {
            return Ok(());
        };
        if keeps_way(
            &self.shell,
            &MoveParams::default(),
            &self.room,
            &piece,
            &self.people,
        ) {
            Ok(())
        } else {
            Err(DormErrorCode::BlocksWay)
        }
    }
}

/// Checks that `item_def` may stand at `pos` facing `yaw` among `areas`.
pub fn check(
    catalog: &Catalog,
    item_def: u16,
    areas: &[PlaceArea],
    pos: QPos,
    yaw: u8,
) -> Result<(), DormErrorCode> {
    let shape = shape_of(catalog, item_def)?;
    check_placement(areas, &shape, pos.to_m(), yaw_from_u8(yaw))
        .map_err(|_| DormErrorCode::OutOfBounds)
}

#[cfg(test)]
mod tests {
    use protocol::House;

    use super::*;
    use crate::catalog::shipped;
    use crate::dorm::DormShells;

    fn def(tag: &str) -> u16 {
        // Furniture by asset name, so the test survives renumbering-free edits
        // to names.
        shipped()
            .catalog
            .items
            .iter()
            .find(|i| i.kind == ItemKind::Furniture && i.asset.contains(tag))
            .unwrap_or_else(|| panic!("a furniture item with asset {tag}"))
            .id
    }

    #[test]
    fn items_fit_the_shell_and_stand_on_tops() {
        let c = &shipped().catalog;
        let shells = DormShells::embedded();
        let shell = shells.world(House::Skytalon).place_areas();
        let desk = def("desk");
        let lantern = def("lantern");
        assert_eq!(
            check(c, desk, shell, QPos::from_m([0.0, 0.0, 0.0]), 0),
            Ok(())
        );
        assert_eq!(
            check(c, desk, shell, QPos::from_m([40.0, 0.0, 0.0]), 0),
            Err(DormErrorCode::OutOfBounds)
        );
        // Clothing is not furniture.
        let robe = c
            .items
            .iter()
            .find(|i| i.kind == ItemKind::Clothing)
            .unwrap()
            .id;
        assert_eq!(
            check(c, robe, shell, QPos::from_m([0.0, 0.0, 0.0]), 0),
            Err(DormErrorCode::NotFurniture)
        );

        // A lantern on the desk's top: only once the desk is placed, and not
        // on the desk when the desk itself is the item being moved.
        let top = c
            .get(desk)
            .unwrap()
            .furniture
            .as_ref()
            .unwrap()
            .top
            .unwrap();
        let on_desk = QPos::from_m([0.0, top, 0.0]);
        assert_eq!(
            check(c, lantern, shell, on_desk, 0),
            Err(DormErrorCode::OutOfBounds)
        );
        let placed = [placed(5, desk, [0.0, 0.0, 0.0], 0)];
        let areas = room_areas(shell, &placed, c, None);
        assert_eq!(check(c, lantern, &areas, on_desk, 0), Ok(()));
        let without = room_areas(shell, &placed, c, Some(5));
        assert_eq!(without.len(), shell.len());
    }

    fn placed(item_id: i64, item_def: u16, pos: [f32; 3], yaw: u8) -> PlacedFurniture {
        PlacedFurniture {
            item_id,
            item_def,
            dyes: [0, 0, 0],
            pos: QPos::from_m(pos),
            yaw,
        }
    }

    /// Tops turn with their piece at any yaw (a desk turned one 22.5 degree
    /// step keeps its top), and a bookcase offers its top and two shelves.
    #[test]
    fn tops_at_any_yaw_and_on_shelves() {
        let c = &shipped().catalog;
        let shells = DormShells::embedded();
        let shell = shells.world(House::Burrowsby).place_areas();
        let (desk, lantern, cage) = (def("desk"), def("lantern"), def("cage_owl"));
        let top = c
            .get(desk)
            .unwrap()
            .furniture
            .as_ref()
            .unwrap()
            .top
            .unwrap();
        let room = [placed(5, desk, [0.0, 0.0, 0.0], 16)];
        let areas = room_areas(shell, &room, c, None);
        // 0.4 m along the desk's own X axis, turned 22.5 degrees.
        let yaw = std::f32::consts::PI / 8.0;
        let along = |d: f32| QPos::from_m([d * yaw.cos(), top, -d * yaw.sin()]);
        assert_eq!(check(c, lantern, &areas, along(0.4), 16), Ok(()));
        assert_eq!(check(c, lantern, &areas, along(0.4), 0), Ok(()));
        assert_eq!(
            check(c, lantern, &areas, along(0.7), 16),
            Err(DormErrorCode::OutOfBounds)
        );

        let bookcase = def("bookcase");
        let room = [placed(9, bookcase, [1.0, 0.0, 1.0], 0)];
        let areas = room_areas(shell, &room, c, None);
        let shelf = QPos::from_m([1.37, 0.525, 1.0]);
        assert_eq!(check(c, lantern, &areas, shelf, 0), Ok(()));
        // The owl cage is taller than the shelf's headroom, but fits the top.
        assert_eq!(
            check(c, cage, &areas, shelf, 0),
            Err(DormErrorCode::OutOfBounds)
        );
        assert_eq!(
            check(c, cage, &areas, QPos::from_m([1.0, 2.0, 1.0]), 0),
            Ok(())
        );
    }

    /// A piece with something on its top or shelves holds it: the room
    /// refuses to move it or put it away until that is taken off.
    #[test]
    fn a_piece_holding_items_is_held() {
        let c = &shipped().catalog;
        let (desk, lantern, chair, bookcase) = (
            def("desk"),
            def("lantern"),
            def("chair_wooden"),
            def("bookcase"),
        );
        let mut room = vec![
            placed(1, desk, [0.0, 0.0, 0.0], 64),
            placed(2, chair, [0.0, 0.0, 0.8], 0),
            placed(3, bookcase, [2.0, 0.0, 0.0], 0),
        ];
        assert!(!holds_items(c, &room, 1));
        room.push(placed(4, lantern, [0.0, 0.76, 0.3], 0));
        assert!(holds_items(c, &room, 1), "the lantern stands on the desk");
        assert!(!holds_items(c, &room, 2), "chairs have no top");
        assert!(!holds_items(c, &room, 4), "nothing on the lantern");
        assert!(!holds_items(c, &room, 99), "unknown item");
        // A lantern on the floor next to the bookcase, then on its shelf.
        room.push(placed(5, lantern, [2.0, 0.0, 0.6], 0));
        assert!(!holds_items(c, &room, 3));
        room.push(placed(6, lantern, [2.37, 0.525, 0.0], 0));
        assert!(holds_items(c, &room, 3));
    }
}
