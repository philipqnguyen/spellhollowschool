//! Runtime view of a [`Layout`]: surfaces flattened into cache-friendly
//! records plus a uniform XZ grid, so "which surfaces contain this point?" costs
//! one cell lookup and a handful of rectangle tests no matter how large the
//! castle is.
//!
//! Surfaces overlap in XZ wherever floors stack (a balcony above a hall, a
//! stair above a corridor). Every query therefore takes a reference height
//! (`near_y`) and only considers surfaces whose ground is within a tolerance of
//! it.

use protocol::House;

use crate::layout::{
    AreaKind, Blocker, EDGE_EPSILON, Interactable, Layout, LayoutError, Plane, Spawn, Surface,
};
use crate::placement::PlaceArea;

/// Tolerance of [`World::ground_at`]: how far a position may sit above or
/// below the floor it belongs to. Well under the height of a storey, so stacked
/// floors never get confused.
pub const SNAP_TOLERANCE: f32 = 1.0;

/// Preferred grid cell edge (metres). Rooms are typically 5-20 m across, so a
/// cell overlaps only a few surfaces.
const CELL_SIZE: f32 = 4.0;

/// Cap on grid cells (4 MB of offsets); very large layouts get coarser cells.
const MAX_CELLS: usize = 1 << 20;

/// Index of a surface in [`World::surfaces`] (and in the layout's `surfaces`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct SurfaceId(pub u32);

/// The walkable ground found under a point.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Ground {
    /// Ground height (metres).
    pub y: f32,
    pub surface: SurfaceId,
    /// The surface's floor level (0 = ground floor).
    pub level: i32,
}

/// The hot part of a surface: its rectangle (already widened by
/// [`EDGE_EPSILON`]), its height plane (or heightfield) and its level.
#[derive(Debug, Clone)]
struct Patch {
    min_x: f32,
    min_z: f32,
    max_x: f32,
    max_z: f32,
    plane: Plane,
    level: i32,
}

impl Patch {
    #[inline]
    fn contains(&self, x: f32, z: f32) -> bool {
        x >= self.min_x && x <= self.max_x && z >= self.min_z && z <= self.max_z
    }
}

/// Uniform grid over the layout's XZ bounds. Cell `i` holds the patches whose
/// rectangles overlap it, stored compactly as `items[starts[i]..starts[i + 1]]`.
#[derive(Debug, Clone)]
struct Grid {
    origin_x: f32,
    origin_z: f32,
    inv_cell: f32,
    nx: usize,
    nz: usize,
    starts: Vec<u32>,
    items: Vec<u32>,
}

impl Grid {
    /// The grid over `rects` (`[min x, min z, max x, max z]` each); one empty
    /// cell when there are none.
    fn build(rects: &[[f32; 4]], cell_size: f32) -> Grid {
        if rects.is_empty() {
            return Grid {
                origin_x: 0.0,
                origin_z: 0.0,
                inv_cell: 1.0,
                nx: 1,
                nz: 1,
                starts: vec![0, 0],
                items: Vec::new(),
            };
        }
        let (mut min_x, mut min_z) = (f32::INFINITY, f32::INFINITY);
        let (mut max_x, mut max_z) = (f32::NEG_INFINITY, f32::NEG_INFINITY);
        for p in rects {
            min_x = min_x.min(p[0]);
            min_z = min_z.min(p[1]);
            max_x = max_x.max(p[2]);
            max_z = max_z.max(p[3]);
        }
        let (w, d) = (max_x - min_x, max_z - min_z);
        let mut cell = cell_size;
        let cells = |cell: f32| ((w / cell).ceil().max(1.0), (d / cell).ceil().max(1.0));
        let (cx, cz) = cells(cell);
        if (cx * cz) as usize > MAX_CELLS {
            cell *= (cx * cz / MAX_CELLS as f32).sqrt() * 1.01;
        }
        let (cx, cz) = cells(cell);
        let (nx, nz) = (cx as usize, cz as usize);
        let inv_cell = 1.0 / cell;

        let cell_range = |p: &[f32; 4]| {
            let clamp = |v: f32, n: usize| (v.floor().max(0.0) as usize).min(n - 1);
            (
                clamp((p[0] - min_x) * inv_cell, nx),
                clamp((p[2] - min_x) * inv_cell, nx),
                clamp((p[1] - min_z) * inv_cell, nz),
                clamp((p[3] - min_z) * inv_cell, nz),
            )
        };

        // Two passes (count, then fill) give a compact CSR layout.
        let mut counts = vec![0u32; nx * nz + 1];
        for p in rects {
            let (x0, x1, z0, z1) = cell_range(p);
            for z in z0..=z1 {
                for x in x0..=x1 {
                    counts[z * nx + x + 1] += 1;
                }
            }
        }
        for i in 1..counts.len() {
            counts[i] += counts[i - 1];
        }
        let starts = counts;
        let mut fill = starts.clone();
        let mut items = vec![0u32; *starts.last().unwrap() as usize];
        for (index, p) in rects.iter().enumerate() {
            let (x0, x1, z0, z1) = cell_range(p);
            for z in z0..=z1 {
                for x in x0..=x1 {
                    let slot = &mut fill[z * nx + x];
                    items[*slot as usize] = index as u32;
                    *slot += 1;
                }
            }
        }

        Grid {
            origin_x: min_x,
            origin_z: min_z,
            inv_cell,
            nx,
            nz,
            starts,
            items,
        }
    }

    /// Indices of the patches overlapping the cell that contains `(x, z)`;
    /// empty outside the grid (and for non-finite input).
    #[inline]
    fn candidates(&self, x: f32, z: f32) -> &[u32] {
        let fx = (x - self.origin_x) * self.inv_cell;
        let fz = (z - self.origin_z) * self.inv_cell;
        // Written so NaN fails the test too. Points on the far edge of the
        // layout belong to the last cell.
        if !(fx >= 0.0 && fz >= 0.0 && fx <= self.nx as f32 && fz <= self.nz as f32) {
            return &[];
        }
        let cx = (fx as usize).min(self.nx - 1);
        let cz = (fz as usize).min(self.nz - 1);
        let cell = cz * self.nx + cx;
        &self.items[self.starts[cell] as usize..self.starts[cell + 1] as usize]
    }
}

/// A validated layout with its spatial index. Immutable once built and cheap to
/// share (`Arc<World>`) between the server's world and instance tasks.
#[derive(Debug, Clone)]
pub struct World {
    layout: Layout,
    patches: Vec<Patch>,
    grid: Grid,
    /// Everything solid: the layout's [`AreaKind::Solid`] boxes, its
    /// [`Layout::solids`] and any added with [`World::with_solids`]. No ground
    /// inside them.
    blockers: Vec<Blocker>,
    /// The same, compact, for the ground queries.
    solids: Vec<Solid>,
    /// The solids by plan cell ([`SOLID_CELL`]).
    solid_grid: Grid,
}

/// Cell edge of the solids' grid (metres): furniture is a metre or two
/// across, so a cell holds a few pieces even in a library.
const SOLID_CELL: f32 = 2.0;

/// How far inside a blocker's outline its ground is taken away (metres):
/// the rounding of a stored position (1 cm) and of a slide's bisection
/// (under 1 cm) never leaves a flush character overlapping it.
const SOLID_EDGE: f32 = 0.02;

/// How far into a solid the straight line between two reported positions
/// may pass (metres): what a walker sliding round a corner or a post cuts
/// off between them ([`World::step_ground_grazing`]).
pub const SOLID_GRAZE: f32 = 0.25;
/// ... and at most this share of the solid's smaller half, so a thin piece
/// (a fence rail, a bench) is never crossed.
pub const SOLID_GRAZE_SHARE: f32 = 0.45;

/// A [`Blocker`] for the ground queries. A point is inside when strictly
/// within its outline (so ground on its faces stays walkable) and within its
/// y span.
#[derive(Debug, Clone, Copy)]
struct Solid {
    at: [f32; 2],
    /// Half extents; the squared inverses for round ones.
    half: [f32; 2],
    inv_sq: [f32; 2],
    /// sin and cos of the yaw (0 and 1 for axis-aligned ones).
    sin: f32,
    cos: f32,
    y: [f32; 2],
    round: bool,
}

impl Solid {
    fn new(b: &Blocker) -> Solid {
        let (sin, cos) = if b.yaw == 0.0 {
            (0.0, 1.0)
        } else {
            b.yaw.sin_cos()
        };
        // A position stored to the centimetre (`QPos`) flush against a piece
        // still fits: the outline is taken SOLID_EDGE in, as a surface's
        // edge is widened by EDGE_EPSILON.
        let [hx, hz] = b.extent();
        let half = [hx - SOLID_EDGE, hz - SOLID_EDGE];
        Solid {
            at: b.at,
            half,
            inv_sq: [1.0 / (half[0] * half[0]), 1.0 / (half[1] * half[1])],
            sin,
            cos,
            y: b.y,
            round: b.round,
        }
    }

    /// Whether the ground point `(x, y, z)` lies inside it deeper than its
    /// graze ([`World::step_ground_grazing`]).
    #[inline]
    fn deep(&self, x: f32, y: f32, z: f32) -> bool {
        if y < self.y[0] || y > self.y[1] {
            return false;
        }
        let (dx, dz) = (x - self.at[0], z - self.at[1]);
        let (lx, lz) = if self.sin == 0.0 {
            (dx, dz)
        } else {
            (dx * self.cos - dz * self.sin, dx * self.sin + dz * self.cos)
        };
        let small = self.half[0].min(self.half[1]);
        let graze = SOLID_GRAZE.min(SOLID_GRAZE_SHARE * small);
        if self.round {
            // Inside the ellipse shrunk by the graze (its smaller axis's share).
            let k = (1.0 - graze / small).max(0.0);
            lx * lx * self.inv_sq[0] + lz * lz * self.inv_sq[1] < k * k
        } else {
            lx.abs() < self.half[0] - graze && lz.abs() < self.half[1] - graze
        }
    }

    /// The same test as [`Blocker::blocks`].
    #[inline]
    fn blocks(&self, x: f32, y: f32, z: f32) -> bool {
        if y < self.y[0] || y > self.y[1] {
            return false;
        }
        let (dx, dz) = (x - self.at[0], z - self.at[1]);
        let (lx, lz) = if self.sin == 0.0 {
            (dx, dz)
        } else {
            (dx * self.cos - dz * self.sin, dx * self.sin + dz * self.cos)
        };
        if self.round {
            lx * lx * self.inv_sq[0] + lz * lz * self.inv_sq[1] < 1.0
        } else {
            lx.abs() < self.half[0] && lz.abs() < self.half[1]
        }
    }
}

impl World {
    /// Validates `layout` and builds the index.
    pub fn new(layout: Layout) -> Result<World, LayoutError> {
        layout.validate()?;
        Ok(World::index(layout, Vec::new()))
    }

    /// This world with `extra` solids added (a dorm's placed furniture:
    /// [`crate::catalog::FurnitureDef::blocker`]). The layout is not checked
    /// again; a blocker with a non-finite number or an empty outline is left
    /// out.
    pub fn with_solids(&self, extra: impl IntoIterator<Item = Blocker>) -> World {
        let extra: Vec<Blocker> = extra
            .into_iter()
            .filter(|b| {
                b.at.iter()
                    .chain(&b.half)
                    .chain(&b.y)
                    .all(|v| v.is_finite())
                    && b.yaw.is_finite()
                    && b.half[0] > 0.0
                    && b.half[1] > 0.0
            })
            .collect();
        World::index(self.layout.clone(), extra)
    }

    fn index(layout: Layout, extra: Vec<Blocker>) -> World {
        let patches: Vec<Patch> = layout
            .surfaces
            .iter()
            .map(|s| Patch {
                min_x: s.min[0] - EDGE_EPSILON,
                min_z: s.min[1] - EDGE_EPSILON,
                max_x: s.max[0] + EDGE_EPSILON,
                max_z: s.max[1] + EDGE_EPSILON,
                plane: s.plane(),
                level: s.level,
            })
            .collect();
        let rects: Vec<[f32; 4]> = patches
            .iter()
            .map(|p| [p.min_x, p.min_z, p.max_x, p.max_z])
            .collect();
        let grid = Grid::build(&rects, CELL_SIZE);
        let blockers: Vec<Blocker> = layout
            .areas
            .iter()
            .filter(|a| a.kind == AreaKind::Solid)
            .map(|a| Blocker::from_box(&a.id, a.min, a.max))
            .chain(layout.solids.iter().cloned())
            .chain(extra)
            .collect();
        let solids = blockers.iter().map(Solid::new).collect();
        let boxes: Vec<[f32; 4]> = blockers.iter().map(Blocker::aabb).collect();
        let solid_grid = Grid::build(&boxes, SOLID_CELL);
        World {
            layout,
            patches,
            grid,
            blockers,
            solids,
            solid_grid,
        }
    }

    /// Everything solid in this world (the layout's solid areas as boxes,
    /// its solid furniture, and any added with [`World::with_solids`]).
    pub fn solids(&self) -> &[Blocker] {
        &self.blockers
    }

    /// The solid holding the ground point `(x, y, z)`, if any.
    pub fn solid_at(&self, x: f32, y: f32, z: f32) -> Option<&Blocker> {
        self.solid_grid
            .candidates(x, z)
            .iter()
            .find(|&&i| self.solids[i as usize].blocks(x, y, z))
            .map(|&i| &self.blockers[i as usize])
    }

    /// Parses, validates and indexes a layout file.
    pub fn from_json(json: &str) -> Result<World, LayoutError> {
        World::new(Layout::from_json(json)?)
    }

    /// This world as characters of `house` walk it: surfaces gated to other
    /// houses are gone ([`Layout::for_house`]). The server validates each
    /// player's moves against their house's world and the client predicts with
    /// the same one, so a house gate is a wall for everyone else.
    pub fn for_house(&self, house: House) -> Result<World, LayoutError> {
        World::new(self.layout.for_house(house))
    }

    /// The interactable with this id, if the layout has one.
    pub fn interactable(&self, id: &str) -> Option<&Interactable> {
        self.layout.interactable(id)
    }

    pub fn interactables(&self) -> &[Interactable] {
        &self.layout.interactables
    }

    /// Where furniture may be placed (instance layouts).
    pub fn place_areas(&self) -> &[PlaceArea] {
        &self.layout.place_areas
    }

    /// Named areas with a purpose (dueling areas).
    pub fn areas(&self) -> &[crate::layout::Area] {
        &self.layout.areas
    }

    /// The first area of `kind` containing `pos`.
    pub fn area_at(
        &self,
        pos: [f32; 3],
        kind: crate::layout::AreaKind,
    ) -> Option<&crate::layout::Area> {
        self.layout
            .areas
            .iter()
            .find(|a| a.kind == kind && a.contains(pos))
    }

    pub fn layout(&self) -> &Layout {
        &self.layout
    }

    pub fn surfaces(&self) -> &[Surface] {
        &self.layout.surfaces
    }

    pub fn surface(&self, id: SurfaceId) -> &Surface {
        &self.layout.surfaces[id.0 as usize]
    }

    pub fn spawns(&self) -> &[Spawn] {
        &self.layout.spawns
    }

    /// Calls `f(id, ground_height)` for every surface containing `(x, z)`.
    #[inline]
    fn for_each_at(&self, x: f32, z: f32, mut f: impl FnMut(u32, &Patch, f32)) {
        for &i in self.grid.candidates(x, z) {
            let p = &self.patches[i as usize];
            if p.contains(x, z) {
                let h = p.plane.height_at(x, z);
                if !self.in_solid(x, h, z) {
                    f(i, p, h);
                }
            }
        }
    }

    /// True if a solid holds the ground point `(x, y, z)`: one grid cell's
    /// few candidates, however many solids the world has.
    #[inline]
    fn in_solid(&self, x: f32, y: f32, z: f32) -> bool {
        self.solid_grid
            .candidates(x, z)
            .iter()
            .any(|&i| self.solids[i as usize].blocks(x, y, z))
    }

    /// The ground at `(x, z)` belonging to the floor a character at height
    /// `near_y` stands on: the containing surface whose height is closest to
    /// `near_y`, if that is within [`SNAP_TOLERANCE`]. Use it to place things
    /// (remote players, the camera, spawns) on the right storey.
    #[inline]
    pub fn ground_at(&self, x: f32, z: f32, near_y: f32) -> Option<Ground> {
        self.ground_within(x, z, near_y, SNAP_TOLERANCE)
    }

    /// Like [`World::ground_at`] with an explicit tolerance.
    pub fn ground_within(&self, x: f32, z: f32, near_y: f32, max_dy: f32) -> Option<Ground> {
        let mut best: Option<Ground> = None;
        let mut best_dy = max_dy;
        self.for_each_at(x, z, |i, p, h| {
            let dy = (h - near_y).abs();
            if dy <= best_dy {
                best_dy = dy;
                best = Some(Ground {
                    y: h,
                    surface: SurfaceId(i),
                    level: p.level,
                });
            }
        });
        best
    }

    /// The ground a character at height `from_y` reaches when it walks onto
    /// `(x, z)`: the *highest* containing surface within `step` of `from_y`.
    /// Preferring the highest means a character steps up onto a dais or the
    /// first tread of a stair that overlaps the floor it is on, instead of
    /// walking through it at floor level. Movement uses this rule.
    #[inline]
    pub fn step_ground(&self, x: f32, z: f32, from_y: f32, step: f32) -> Option<Ground> {
        let mut best: Option<Ground> = None;
        self.for_each_at(x, z, |i, p, h| {
            if (h - from_y).abs() <= step && best.is_none_or(|b| h > b.y) {
                best = Some(Ground {
                    y: h,
                    surface: SurfaceId(i),
                    level: p.level,
                });
            }
        });
        best
    }

    /// [`World::step_ground`] for a point on the way between two positions
    /// ([`crate::check_move`]'s path samples): a solid takes the ground away
    /// only deeper than its graze ([`SOLID_GRAZE`], at most
    /// [`SOLID_GRAZE_SHARE`] of its smaller half). A walker that slides
    /// round a lamp post or a table's corner between two reported
    /// positions cuts across the corner on the straight line the server
    /// checks; it never reaches a piece's middle, so nothing is crossed.
    pub fn step_ground_grazing(&self, x: f32, z: f32, from_y: f32, step: f32) -> Option<Ground> {
        let mut best: Option<Ground> = None;
        for &i in self.grid.candidates(x, z) {
            let p = &self.patches[i as usize];
            if !p.contains(x, z) {
                continue;
            }
            let h = p.plane.height_at(x, z);
            let deep = self
                .solid_grid
                .candidates(x, z)
                .iter()
                .any(|&k| self.solids[k as usize].deep(x, h, z));
            if !deep && (h - from_y).abs() <= step && best.is_none_or(|b| h > b.y) {
                best = Some(Ground {
                    y: h,
                    surface: SurfaceId(i),
                    level: p.level,
                });
            }
        }
        best
    }

    /// The highest ground at `(x, z)` that is not above `y` (any
    /// distance below), or `None` where no surface has ground there at or
    /// under `y` (the water, a building's hole, outside the layout). Flight
    /// uses it for the floor under a broom (`crate::flight`).
    pub fn ground_below(&self, x: f32, z: f32, y: f32) -> Option<f32> {
        let mut best: Option<f32> = None;
        self.for_each_at(x, z, |_, _, h| {
            if h <= y && best.is_none_or(|b| h > b) {
                best = Some(h);
            }
        });
        best
    }

    /// True if some surface contains `(x, z)` with ground within `max_dy` of `y`.
    #[inline]
    pub fn has_ground_within(&self, x: f32, z: f32, y: f32, max_dy: f32) -> bool {
        self.grid.candidates(x, z).iter().any(|&i| {
            let p = &self.patches[i as usize];
            p.contains(x, z) && {
                let h = p.plane.height_at(x, z);
                (h - y).abs() <= max_dy && !self.in_solid(x, h, z)
            }
        })
    }

    /// True if any surface, on any floor, contains `(x, z)` (outside the solids).
    #[inline]
    pub fn is_walkable(&self, x: f32, z: f32) -> bool {
        self.grid.candidates(x, z).iter().any(|&i| {
            let p = &self.patches[i as usize];
            p.contains(x, z) && !self.in_solid(x, p.plane.height_at(x, z), z)
        })
    }

    /// Floor level at `(x, z)` for a character at height `near_y`.
    pub fn level_at(&self, x: f32, z: f32, near_y: f32) -> Option<i32> {
        self.ground_at(x, z, near_y).map(|g| g.level)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{flat, hall_and_balcony, world};

    #[test]
    fn flat_ground() {
        let w = world(vec![flat("room", [0.0, 0.0], [10.0, 10.0], 1.5)]);
        let g = w.ground_at(5.0, 5.0, 1.5).unwrap();
        assert_eq!((g.y, g.surface, g.level), (1.5, SurfaceId(0), 0));
        assert!(w.ground_at(5.0, 5.0, 5.0).is_none(), "too far above");
        assert!(w.ground_at(11.0, 5.0, 1.5).is_none(), "outside");
        assert!(
            w.ground_at(10.0, 10.0, 1.5).is_some(),
            "edges are inclusive"
        );
        assert!(w.ground_at(f32::NAN, 5.0, 1.5).is_none());
    }

    #[test]
    fn stacked_floors_pick_nearest() {
        let w = hall_and_balcony();
        assert_eq!(w.ground_at(2.0, 2.0, 0.2).unwrap().y, 0.0);
        assert_eq!(w.ground_at(2.0, 2.0, 3.6).unwrap().y, 4.0);
        assert_eq!(w.level_at(2.0, 2.0, 0.0), Some(0));
        assert_eq!(w.level_at(2.0, 2.0, 4.0), Some(1));
        assert_eq!(w.level_at(2.0, 2.0, 2.0), None, "between floors");
    }

    #[test]
    fn stairs_interpolate() {
        let w = hall_and_balcony();
        let g = w.ground_at(9.0, 8.0, 2.0).unwrap();
        assert!((g.y - 2.0).abs() < 1e-5);
        assert_eq!(w.surface(g.surface).id, "stair");
    }

    #[test]
    fn step_ground_prefers_the_step_up() {
        let w = hall_and_balcony();
        // Near the stair foot both the hall (0.0) and the stair (0.05) qualify.
        let g = w.step_ground(9.0, 11.9, 0.0, 0.35).unwrap();
        assert_eq!(w.surface(g.surface).id, "stair");
        let g = w.ground_within(9.0, 11.9, 0.0, 0.35).unwrap();
        assert_eq!(w.surface(g.surface).id, "hall");
    }

    #[test]
    fn grid_handles_many_and_large_surfaces() {
        // One huge grounds surface plus a row of small rooms; also exercises
        // the cell-count cap.
        let mut surfaces = vec![flat("grounds", [-3000.0, -3000.0], [3000.0, 3000.0], 0.0)];
        for i in 0..200 {
            let x = i as f32 * 6.0;
            surfaces.push(flat(&format!("r{i}"), [x, 100.0], [x + 5.0, 105.0], 8.0));
        }
        let w = world(surfaces);
        assert!(w.grid.nx * w.grid.nz <= MAX_CELLS);
        assert_eq!(w.ground_at(-2999.0, 2999.0, 0.0).unwrap().y, 0.0);
        assert_eq!(w.ground_at(3000.0, 3000.0, 0.0).unwrap().y, 0.0);
        let g = w.ground_at(6.0 * 150.0 + 2.5, 102.0, 8.0).unwrap();
        assert_eq!(w.surface(g.surface).id, "r150");
        assert!(w.ground_at(6.0 * 150.0 + 5.5, 102.0, 8.0).is_none());
    }

    /// A house gate is walkable for its house and a wall for everyone else,
    /// both for the server's check and the client's prediction.
    #[test]
    fn house_worlds_turn_gates_into_walls() {
        use crate::layout::SurfaceKind;
        use crate::movement::{MoveCheck, MoveParams, check_move, move_and_slide};
        let gate = |s: Surface| Surface {
            house: Some("burrowsby".into()),
            ..s
        };
        let base = world(vec![
            flat("corridor", [0.0, 0.0], [10.0, 10.0], 0.0),
            gate(Surface {
                kind: SurfaceKind::Door,
                ..flat("barrels", [10.0, 4.0], [10.5, 6.0], 0.0)
            }),
            gate(flat("common", [10.5, 0.0], [20.5, 10.0], 0.0)),
        ]);
        let params = MoveParams::default();
        let (from, into) = ([9.5, 0.0, 5.0], [11.0, 0.0, 5.0]);
        let member = base.for_house(House::Burrowsby).unwrap();
        let other = base.for_house(House::Roarwing).unwrap();
        assert!(matches!(
            check_move(&member, &params, from, into, 1.0),
            MoveCheck::Accept { .. }
        ));
        assert!(matches!(
            check_move(&other, &params, from, into, 1.0),
            MoveCheck::Reject(_)
        ));
        // Walking at the doorway stops at the wall for the other house.
        let stopped = move_and_slide(&other, &params, from, [1.5, 0.0]);
        assert!(stopped[0] <= 10.0 - params.radius + 1e-3, "{stopped:?}");
        let through = move_and_slide(&member, &params, from, [1.5, 0.0]);
        assert!(through[0] > 10.5, "{through:?}");
    }

    /// A solid box (the courtyard gatehouse's towers) is a wall on the floor
    /// it stands on, for the server's check and the client's prediction; a
    /// floor above its top keeps its ground, and so do its faces.
    #[test]
    fn solids_are_walls_on_the_floor_they_stand_on() {
        use crate::layout::{Area, AreaKind};
        use crate::movement::{MoveCheck, MoveParams, check_move, move_and_slide};
        use crate::testutil::layout;
        let mut l = layout(vec![
            flat("court", [0.0, 0.0], [20.0, 10.0], 0.0),
            flat("gallery", [0.0, 0.0], [20.0, 10.0], 12.0),
        ]);
        l.areas.push(Area {
            id: "tower".into(),
            kind: AreaKind::Solid,
            name: "Tower".into(),
            min: [8.0, 0.0, 6.0],
            max: [12.0, 10.0, 10.0],
        });
        let w = World::new(l).unwrap();
        assert!(w.ground_at(10.0, 8.0, 0.0).is_none(), "inside the tower");
        assert!(!w.has_ground_within(10.0, 8.0, 0.0, 0.35));
        assert!(
            w.ground_at(10.0, 8.0, 12.0).is_some(),
            "the floor over its top"
        );
        assert!(w.is_walkable(10.0, 8.0), "the floor over its top");
        assert!(w.ground_at(8.0, 8.0, 0.0).is_some(), "its face");
        assert!(w.ground_at(10.0, 5.0, 0.0).is_some(), "in front of it");
        let params = MoveParams::default();
        let from = [10.0, 0.0, 4.0];
        assert!(matches!(
            check_move(&w, &params, from, [10.0, 0.0, 7.0], 1.0),
            MoveCheck::Reject(_)
        ));
        let stopped = move_and_slide(&w, &params, from, [0.0, 3.0]);
        // A body's radius off the face, less the edge's 2 cm of slack.
        assert!(
            (stopped[2] - (6.0 - params.radius + SOLID_EDGE)).abs() < 1e-2,
            "{stopped:?}"
        );
        // Beside it the court stays open to its back wall.
        let beside = move_and_slide(&w, &params, [6.0, 0.0, 4.0], [0.0, 7.0]);
        assert!(
            (beside[2] - (10.0 - params.radius)).abs() < 1e-2,
            "{beside:?}"
        );
    }
}
