//! Furniture placement in dorm rooms: the rule the server enforces for
//! `ClientMsg::DormPlace` / `DormMove`, and the client previews with.
//!
//! A dorm's instance layout lists [`PlaceArea`]s, axis-aligned regions of the
//! room shell where items may go. An item (its catalog [`FurnitureShape`]) may
//! be placed when it fits inside one area of a surface it can use:
//!
//! - **Floor** items stand on a floor area. Their origin is the bottom centre
//!   of the footprint; at yaw 0 the item faces +Z with `footprint[0]` along X
//!   and `footprint[1]` along Z. The rotated footprint's bounding box must lie
//!   inside the area, and the origin's height must be the area's.
//! - **Table** items stand on a table area (a fixed shelf or sill in the shell,
//!   or a flat top or shelf of a placed item, see
//!   `catalog::FurnitureDef::top_areas`) or on the floor, by the same rule.
//!   A placed item's tops turn with it: such an area carries a [`AreaFrame`]
//!   (the item's origin and yaw) and its box is in that frame, so the rule is
//!   checked in the item's own axes at any yaw. An area with a `clear`
//!   (a shelf under another) takes only items no taller than that.
//! - **Wall** items hang on a wall area, a vertical rectangle flat along X or
//!   Z. Their origin is the bottom centre of their back; they must face the
//!   way the wall does (`facing`), stand on its plane, and their width
//!   (`footprint[0]`) and `height` must fit inside it.
//!
//! Items may overlap each other. Floor pieces taller than a step are solid
//! (`catalog::FurnitureDef::blocker`, docs/architecture.md "Layouts"),
//! so a piece may also go only where [`keeps_way`] says: on
//! nobody, off the room's entry, with the way from the entry to the exit
//! and to everyone in the room still open. Snapping is a client convenience
//! ([`GRID_STEP_M`], [`FINE_STEP_M`], [`YAW_STEP`]); the server only checks the
//! rule above on the quantized wire values (centimetres, 1/256 turn).

use serde::{Deserialize, Serialize};

/// Default placement grid (metres).
pub const GRID_STEP_M: f32 = 0.25;
/// Grid in fine mode (metres).
pub const FINE_STEP_M: f32 = 0.05;
/// Rotation step in wire yaw units (1/256 turn): 16 = 22.5 degrees. Fine mode
/// rotates by single units.
pub const YAW_STEP: u8 = 16;
/// Slack for rounding to centimetres and float error (metres).
pub const PLACE_EPSILON: f32 = 0.01;
/// How far a wall item's yaw may differ from its wall's facing (radians):
/// half a wire yaw unit, plus float slack.
const FACING_TOLERANCE: f32 = std::f32::consts::TAU / 512.0 + 1e-4;

/// Where a furniture item can go.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PlaceSurface {
    Floor,
    Wall,
    Table,
}

/// A region of an instance layout where furniture may be placed.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PlaceArea {
    pub id: String,
    pub surface: PlaceSurface,
    /// The region as a box, `[x, y, z]` corners. Floor and table areas are
    /// horizontal (`min[1] == max[1]`, the surface height); wall areas are
    /// vertical and flat along exactly one of X and Z (the wall's plane).
    pub min: [f32; 3],
    pub max: [f32; 3],
    /// Wall areas only: the yaw the wall faces, into the room (0 = +Z,
    /// π/2 = +X, π = -Z, 3π/2 = -X). Perpendicular to the wall's plane.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub facing: Option<f32>,
    /// Floor and table areas: the tallest item that fits under whatever is
    /// above (a shelf's headroom, metres). None: no limit.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub clear: Option<f32>,
    /// Floor and table areas: `min` / `max` are in this frame (a placed
    /// item's tops turn with it). None: world axes.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub frame: Option<AreaFrame>,
}

/// The axes an area's box is given in: `world = origin + R(yaw) · local`,
/// `R(yaw)` turning local +Z to `(sin yaw, 0, cos yaw)` like an item's yaw.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct AreaFrame {
    pub origin: [f32; 3],
    pub yaw: f32,
}

impl AreaFrame {
    /// A world position and yaw in this frame.
    pub fn to_local(&self, pos: [f32; 3], yaw: f32) -> ([f32; 3], f32) {
        let (s, c) = self.yaw.sin_cos();
        let (dx, dz) = (pos[0] - self.origin[0], pos[2] - self.origin[2]);
        (
            [dx * c - dz * s, pos[1] - self.origin[1], dx * s + dz * c],
            yaw - self.yaw,
        )
    }

    /// A local position in world coordinates.
    pub fn to_world(&self, local: [f32; 3]) -> [f32; 3] {
        let (s, c) = self.yaw.sin_cos();
        [
            self.origin[0] + local[0] * c + local[2] * s,
            self.origin[1] + local[1],
            self.origin[2] - local[0] * s + local[2] * c,
        ]
    }
}

/// The placement-relevant part of a furniture catalog entry.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FurnitureShape {
    pub surface: PlaceSurface,
    /// Width along X and depth along Z at yaw 0 (metres).
    pub footprint: [f32; 2],
    pub height: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlacementError {
    /// A coordinate or the yaw is not finite.
    NotFinite,
    /// The item fits in no area it can use.
    OutOfBounds,
}

/// Normalizes an angle to [0, 2π).
fn norm(a: f32) -> f32 {
    a.rem_euclid(std::f32::consts::TAU)
}

/// Smallest absolute difference between two angles.
fn angle_diff(a: f32, b: f32) -> f32 {
    let d = norm(a - b);
    d.min(std::f32::consts::TAU - d)
}

/// Half extents (x, z) of a `footprint` rotated by `yaw`.
fn half_extents(footprint: [f32; 2], yaw: f32) -> (f32, f32) {
    let (s, c) = (yaw.sin().abs(), yaw.cos().abs());
    let (hw, hd) = (footprint[0] / 2.0, footprint[1] / 2.0);
    (c * hw + s * hd, s * hw + c * hd)
}

impl PlaceArea {
    /// True for a wall area whose plane is `x = const` (else `z = const`).
    fn wall_on_x_plane(&self) -> bool {
        (self.max[0] - self.min[0]).abs() <= PLACE_EPSILON
    }

    /// Checks the shape rules listed on the fields.
    pub fn validate(&self) -> Result<(), String> {
        let all = self.min.iter().chain(&self.max);
        let frame_ok = self
            .frame
            .is_none_or(|f| f.yaw.is_finite() && f.origin.iter().all(|v| v.is_finite()));
        if !all.clone().all(|v| v.is_finite())
            || self.facing.is_some_and(|f| !f.is_finite())
            || !frame_ok
        {
            return Err("non-finite coordinate".into());
        }
        if self.clear.is_some_and(|c| !(c.is_finite() && c > 0.0)) {
            return Err("clear must be above zero".into());
        }
        if (0..3).any(|i| self.min[i] > self.max[i]) {
            return Err("min must be <= max".into());
        }
        let flat = |i: usize| self.max[i] - self.min[i] <= PLACE_EPSILON;
        match self.surface {
            PlaceSurface::Floor | PlaceSurface::Table => {
                if !flat(1) || flat(0) || flat(2) {
                    return Err("floor and table areas are horizontal rectangles".into());
                }
                if self.facing.is_some() {
                    return Err("only wall areas have a facing".into());
                }
            }
            PlaceSurface::Wall if self.frame.is_some() || self.clear.is_some() => {
                return Err("wall areas have no frame or clear".into());
            }
            PlaceSurface::Wall => {
                if flat(0) == flat(2) || flat(1) {
                    return Err("wall areas are vertical and flat along exactly one of x, z".into());
                }
                let Some(facing) = self.facing else {
                    return Err("wall areas need a facing".into());
                };
                use std::f32::consts::{FRAC_PI_2, PI};
                let options = if self.wall_on_x_plane() {
                    [FRAC_PI_2, 3.0 * FRAC_PI_2]
                } else {
                    [0.0, PI]
                };
                if !options.iter().any(|&o| angle_diff(facing, o) <= 1e-3) {
                    return Err("facing must be perpendicular to the wall".into());
                }
            }
        }
        Ok(())
    }

    /// Whether `shape` placed at `pos` with `yaw` fits this area (ignoring
    /// which surfaces the shape may use): also whether a table item placed
    /// there rests on it (`rules::holds_items` on the server).
    pub fn fits(&self, shape: &FurnitureShape, pos: [f32; 3], yaw: f32) -> bool {
        let e = PLACE_EPSILON;
        let within = |v: f32, lo: f32, hi: f32| v >= lo - e && v <= hi + e;
        match self.surface {
            PlaceSurface::Floor | PlaceSurface::Table => {
                let (pos, yaw) = match self.frame {
                    Some(f) => f.to_local(pos, yaw),
                    None => (pos, yaw),
                };
                let (hx, hz) = half_extents(shape.footprint, yaw);
                self.clear.is_none_or(|c| shape.height <= c + e)
                    && (pos[1] - self.min[1]).abs() <= e
                    && within(pos[0] - hx, self.min[0], self.max[0])
                    && within(pos[0] + hx, self.min[0], self.max[0])
                    && within(pos[2] - hz, self.min[2], self.max[2])
                    && within(pos[2] + hz, self.min[2], self.max[2])
            }
            PlaceSurface::Wall => {
                let Some(facing) = self.facing else {
                    return false;
                };
                // (plane axis, along-wall axis)
                let (plane, along) = if self.wall_on_x_plane() {
                    (0, 2)
                } else {
                    (2, 0)
                };
                let half = shape.footprint[0] / 2.0;
                angle_diff(yaw, facing) <= FACING_TOLERANCE
                    && (pos[plane] - self.min[plane]).abs() <= e
                    && within(pos[along] - half, self.min[along], self.max[along])
                    && within(pos[along] + half, self.min[along], self.max[along])
                    && within(pos[1], self.min[1], self.max[1])
                    && within(pos[1] + shape.height, self.min[1], self.max[1])
            }
        }
    }
}

/// Checks a placement against a room's areas (the layout's `place_areas`
/// plus the tops of placed items, `catalog::FurnitureDef::top_areas`). `yaw` is in radians
/// (`protocol::yaw_from_u8` of the wire value).
pub fn check_placement(
    areas: &[PlaceArea],
    shape: &FurnitureShape,
    pos: [f32; 3],
    yaw: f32,
) -> Result<(), PlacementError> {
    if !(pos.iter().all(|v| v.is_finite()) && yaw.is_finite()) {
        return Err(PlacementError::NotFinite);
    }
    let usable = |s: PlaceSurface| match shape.surface {
        PlaceSurface::Floor => s == PlaceSurface::Floor,
        PlaceSurface::Table => s == PlaceSurface::Table || s == PlaceSurface::Floor,
        PlaceSurface::Wall => s == PlaceSurface::Wall,
    };
    areas
        .iter()
        .any(|a| usable(a.surface) && a.fits(shape, pos, yaw))
        .then_some(())
        .ok_or(PlacementError::OutOfBounds)
}

/// Spacing of the grid [`keeps_way`] floods a room with (metres).
pub const WAY_STEP: f32 = 0.15;

/// The solid furniture rule: may solid piece `piece` join a room whose world
/// without furniture is `shell` and whose other solid pieces are `room`
/// (the piece being moved left out), with characters standing at `people`?
/// Only if it stands on nobody (their footprint clear of it), the room's
/// entry (its first spawn) stays free, and from the entry a character still
/// reaches the room's exits (`dorm_exit` / `instance_exit` interactables,
/// within their reach) and everyone in it. The server refuses a placement
/// or a move that breaks it (`DormErrorCode::BlocksWay`); the client
/// previews it (`SimWorld.keepsWay`).
pub fn keeps_way(
    shell: &crate::World,
    params: &crate::MoveParams,
    room: &[crate::Blocker],
    piece: &crate::Blocker,
    people: &[[f32; 3]],
) -> bool {
    use crate::layout::InteractableKind;
    let on_someone = people.iter().any(|p| {
        p[1] >= piece.y[0] - params.step_height
            && p[1] <= piece.y[1]
            && piece.holds(p[0], p[2], params.radius)
    });
    if on_someone {
        return false;
    }
    let world = shell.with_solids(room.iter().cloned().chain(std::iter::once(piece.clone())));
    let Some(entry) = shell.spawns().first().map(|s| s.pos) else {
        return true;
    };
    let Some(g) = shell.ground_at(entry[0], entry[2], entry[1]) else {
        return true;
    };
    if crate::footprint_ground(&world, params, entry[0], entry[2], g.y).is_none() {
        return false;
    }
    let reach =
        crate::movement::Reach::flood(&world, params, [entry[0], g.y, entry[2]], WAY_STEP, 200_000);
    let exits_ok = shell
        .interactables()
        .iter()
        .filter(|i| {
            matches!(
                i.kind,
                InteractableKind::DormExit | InteractableKind::InstanceExit
            )
        })
        .all(|i| reach.reaches(i.pos, (i.radius - 0.1).max(WAY_STEP), 1.0));
    exits_ok
        && people
            .iter()
            .all(|p| reach.reaches(*p, params.radius + WAY_STEP, params.step_height + 0.1))
}

#[cfg(test)]
mod tests {
    use std::f32::consts::{FRAC_PI_2, PI};

    use super::*;

    fn floor() -> PlaceArea {
        PlaceArea {
            id: "floor".into(),
            surface: PlaceSurface::Floor,
            min: [-4.0, 0.0, -4.0],
            max: [4.0, 0.0, 4.0],
            facing: None,
            clear: None,
            frame: None,
        }
    }

    /// The north wall (z = -4.5), facing south into the room.
    fn north_wall() -> PlaceArea {
        PlaceArea {
            id: "wall_n".into(),
            surface: PlaceSurface::Wall,
            min: [-4.0, 0.5, -4.5],
            max: [4.0, 3.0, -4.5],
            facing: Some(0.0),
            clear: None,
            frame: None,
        }
    }

    fn shape(surface: PlaceSurface, w: f32, d: f32, h: f32) -> FurnitureShape {
        FurnitureShape {
            surface,
            footprint: [w, d],
            height: h,
        }
    }

    #[test]
    fn floor_items_fit_by_their_rotated_footprint() {
        let areas = [floor()];
        // A 2 x 1 m bed against the east edge: fits facing south, not turned
        // a quarter (its 2 m side would stick out).
        let bed = shape(PlaceSurface::Floor, 1.0, 2.0, 1.2);
        assert_eq!(check_placement(&areas, &bed, [3.5, 0.0, 0.0], 0.0), Ok(()));
        assert_eq!(
            check_placement(&areas, &bed, [3.5, 0.0, 0.0], FRAC_PI_2),
            Err(PlacementError::OutOfBounds)
        );
        // At the wrong height, or not finite.
        assert!(check_placement(&areas, &bed, [0.0, 0.2, 0.0], 0.0).is_err());
        assert_eq!(
            check_placement(&areas, &bed, [f32::NAN, 0.0, 0.0], 0.0),
            Err(PlacementError::NotFinite)
        );
        // Wall items never stand on the floor.
        let painting = shape(PlaceSurface::Wall, 1.0, 0.05, 0.8);
        assert!(check_placement(&areas, &painting, [0.0, 0.0, 0.0], 0.0).is_err());
    }

    #[test]
    fn wall_items_face_the_room_and_fit_the_wall() {
        let areas = [floor(), north_wall()];
        let painting = shape(PlaceSurface::Wall, 1.0, 0.05, 0.8);
        assert_eq!(
            check_placement(&areas, &painting, [0.0, 1.5, -4.5], 0.0),
            Ok(())
        );
        // Facing the wall, off its plane, too high, or past its end.
        assert!(check_placement(&areas, &painting, [0.0, 1.5, -4.5], PI).is_err());
        assert!(check_placement(&areas, &painting, [0.0, 1.5, -4.0], 0.0).is_err());
        assert!(check_placement(&areas, &painting, [0.0, 2.5, -4.5], 0.0).is_err());
        assert!(check_placement(&areas, &painting, [3.8, 1.5, -4.5], 0.0).is_err());
    }

    /// A desk's top (1.2 x 0.6 m at 0.75 m) as a framed area at `pos`, `yaw`.
    fn desk_top(pos: [f32; 3], yaw: f32) -> PlaceArea {
        PlaceArea {
            id: "desk".into(),
            surface: PlaceSurface::Table,
            min: [-0.6, 0.75, -0.3],
            max: [0.6, 0.75, 0.3],
            facing: None,
            clear: None,
            frame: Some(AreaFrame { origin: pos, yaw }),
        }
    }

    #[test]
    fn table_items_go_on_tops_or_the_floor() {
        let top = desk_top([0.0, 0.0, 0.0], FRAC_PI_2);
        let lamp = shape(PlaceSurface::Table, 0.2, 0.2, 0.4);
        let areas = [floor(), top];
        // Turned a quarter, the desk's long side runs along Z.
        assert_eq!(
            check_placement(&areas, &lamp, [0.0, 0.75, 0.4], 0.0),
            Ok(())
        );
        assert_eq!(check_placement(&areas, &lamp, [2.0, 0.0, 2.0], 0.0), Ok(()));
        assert!(check_placement(&areas, &lamp, [0.0, 0.75, 0.7], 0.0).is_err());
        assert!(check_placement(&areas, &lamp, [0.4, 0.75, 0.0], 0.0).is_err());
        // Floor-only items never stand on tables.
        let chair = shape(PlaceSurface::Floor, 0.2, 0.2, 0.9);
        assert!(check_placement(&areas[1..], &chair, [0.0, 0.75, 0.0], 0.0).is_err());
    }

    /// A top turns with its item at any yaw, not only by quarter turns.
    #[test]
    fn tops_turn_with_their_item() {
        let yaw = PI / 8.0; // 22.5 degrees, one rotation step
        let origin = [1.0, 0.0, -1.0];
        let frame = AreaFrame { origin, yaw };
        let areas = [desk_top(origin, yaw)];
        let lamp = shape(PlaceSurface::Table, 0.2, 0.2, 0.4);
        // 0.45 m along the desk's own X: on it, whatever the lamp's own yaw.
        let at = frame.to_world([0.45, 0.75, 0.0]);
        assert_eq!(check_placement(&areas, &lamp, at, 0.0), Ok(()));
        assert_eq!(check_placement(&areas, &lamp, at, yaw), Ok(()));
        // Turned 45 degrees against the top, its corners stick out at the end.
        let near_end = frame.to_world([0.5, 0.75, 0.0]);
        assert!(check_placement(&areas, &lamp, near_end, yaw + PI / 4.0).is_err());
        // Near the back edge in the desk's axes: on it; past it: off.
        assert_eq!(
            check_placement(&areas, &lamp, frame.to_world([0.0, 0.75, -0.19]), yaw),
            Ok(())
        );
        assert!(check_placement(&areas, &lamp, frame.to_world([0.0, 0.75, 0.28]), yaw).is_err());
        let (local, rel) = frame.to_local(at, yaw);
        assert!((local[0] - 0.45).abs() < 1e-5 && local[2].abs() < 1e-5 && rel.abs() < 1e-6);
    }

    /// A shelf takes only items no taller than its headroom.
    #[test]
    fn shelves_have_headroom() {
        let mut shelf = desk_top([0.0; 3], 0.0);
        shelf.clear = Some(0.39);
        let lantern = shape(PlaceSurface::Table, 0.18, 0.18, 0.35);
        let cage = shape(PlaceSurface::Table, 0.18, 0.18, 0.55);
        let areas = [shelf];
        assert_eq!(
            check_placement(&areas, &lantern, [0.0, 0.75, 0.0], 0.0),
            Ok(())
        );
        assert!(check_placement(&areas, &cage, [0.0, 0.75, 0.0], 0.0).is_err());
        assert!(areas[0].validate().is_ok());
        let mut bad = areas[0].clone();
        bad.clear = Some(0.0);
        assert!(bad.validate().is_err());
    }

    /// Every shipped dorm shell loads, has an entry spawn and a way out, and
    /// holds a bed on its floor and a banner on a wall.
    #[test]
    fn the_shipped_dorm_shells_are_usable() {
        use crate::layout::{InteractableKind, Layout};
        let shells = [
            include_str!("../../../data/world/instances/dorm_roarwing.json"),
            include_str!("../../../data/world/instances/dorm_burrowsby.json"),
            include_str!("../../../data/world/instances/dorm_skytalon.json"),
            include_str!("../../../data/world/instances/dorm_serpentyne.json"),
        ];
        let bed = shape(PlaceSurface::Floor, 1.6, 2.3, 2.5);
        let banner = shape(PlaceSurface::Wall, 0.9, 0.05, 1.6);
        for json in shells {
            let l = Layout::from_json(json).unwrap();
            assert_eq!(l.spawns.len(), 1);
            let exits = l
                .interactables
                .iter()
                .filter(|i| i.kind == InteractableKind::DormExit);
            assert_eq!(exits.count(), 1);
            assert!(l.surfaces.iter().all(|s| s.cell.starts_with("dorm_house_")));
            assert_eq!(
                check_placement(&l.place_areas, &bed, [0.0, 0.0, -2.0], 0.0),
                Ok(())
            );
            let north = l.place_areas.iter().find(|a| a.id == "wall_north").unwrap();
            let on_wall = [0.0, 1.0, north.min[2]];
            assert_eq!(
                check_placement(&l.place_areas, &banner, on_wall, 0.0),
                Ok(())
            );
            // The starter bed's spot: exact on the wire, and the rule takes it
            // (its wall's framed floor area).
            assert_eq!(l.starter_furniture.len(), 1);
            for p in &l.starter_furniture {
                let units = (p.yaw / std::f32::consts::TAU * 256.0).round();
                assert!((units * std::f32::consts::TAU / 256.0 - p.yaw).abs() < 1e-5);
                assert!(
                    p.pos
                        .iter()
                        .all(|v| (v * 100.0 - (v * 100.0).round()).abs() < 1e-3)
                );
                assert_eq!(check_placement(&l.place_areas, &bed, p.pos, p.yaw), Ok(()));
                let off_axis = p.yaw.rem_euclid(FRAC_PI_2);
                if off_axis.min(FRAC_PI_2 - off_axis) > 1e-4 {
                    // Against a diagonal wall only that wall's framed area holds it.
                    let axis: Vec<_> = l
                        .place_areas
                        .iter()
                        .filter(|a| a.frame.is_none())
                        .cloned()
                        .collect();
                    assert!(check_placement(&axis, &bed, p.pos, p.yaw).is_err());
                }
            }
        }
    }

    #[test]
    fn areas_are_validated() {
        assert!(floor().validate().is_ok());
        assert!(north_wall().validate().is_ok());
        let mut a = north_wall();
        a.facing = Some(FRAC_PI_2);
        assert!(a.validate().unwrap_err().contains("perpendicular"));
        a.facing = None;
        assert!(a.validate().unwrap_err().contains("need a facing"));
        let mut a = floor();
        a.max[1] = 1.0;
        assert!(a.validate().unwrap_err().contains("horizontal"));
        a.max[1] = 0.0;
        a.facing = Some(0.0);
        assert!(a.validate().is_err());
        let mut a = floor();
        a.min[0] = 5.0;
        assert!(a.validate().unwrap_err().contains("min must be"));
    }
}
