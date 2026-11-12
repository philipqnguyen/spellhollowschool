//! Uniform spatial grid over the XZ plane for neighbour queries (visibility,
//! say-radius chat, nearby counts).
//!
//! Rebuilt from scratch every tick with a counting sort into two flat arrays
//! (`starts`, `items`) that keep their capacity, so a rebuild is O(players +
//! cells) with no allocation. Positions outside the layout's bounds clamp to
//! the border cells; the grid stays correct, just less selective there.

/// Cell edge in metres. About a classroom: rings of cells grow quickly enough
/// that a nearest-50 query in a sparse castle needs few rings, while a crowd in
/// one hall spreads over several cells.
pub const CELL_SIZE: f32 = 8.0;

/// `cell_of` marker for an entity left out of the grid.
const EXCLUDED: u32 = u32::MAX;

#[derive(Debug, Clone)]
pub struct Grid {
    origin: [f32; 2],
    inv_cell: f32,
    cols: i32,
    rows: i32,
    /// `starts[c]..starts[c + 1]` indexes `items` for cell `c`.
    starts: Vec<u32>,
    /// Dense entity indices grouped by cell.
    items: Vec<u32>,
    /// Scratch: each entity's cell during a rebuild.
    cell_of: Vec<u32>,
}

impl Grid {
    /// A grid covering `min..max` (XZ, metres) plus one cell of margin.
    pub fn new(min: [f32; 2], max: [f32; 2]) -> Self {
        let origin = [min[0] - CELL_SIZE, min[1] - CELL_SIZE];
        let cols = (((max[0] - min[0]) / CELL_SIZE).ceil() as i32 + 2).max(1);
        let rows = (((max[1] - min[1]) / CELL_SIZE).ceil() as i32 + 2).max(1);
        Self {
            origin,
            inv_cell: 1.0 / CELL_SIZE,
            cols,
            rows,
            starts: vec![0; (cols * rows) as usize + 1],
            items: Vec::new(),
            cell_of: Vec::new(),
        }
    }

    /// Covers every surface of a layout.
    pub fn for_world(world: &sim_core::World) -> Self {
        let mut min = [f32::MAX; 2];
        let mut max = [f32::MIN; 2];
        for s in world.surfaces() {
            min = [min[0].min(s.min[0]), min[1].min(s.min[1])];
            max = [max[0].max(s.max[0]), max[1].max(s.max[1])];
        }
        if min[0] > max[0] {
            min = [0.0; 2];
            max = [0.0; 2];
        }
        Self::new(min, max)
    }

    /// Cell coordinates of a point, clamped into the grid.
    #[inline]
    pub fn cell_coords(&self, x: f32, z: f32) -> (i32, i32) {
        let cx = ((x - self.origin[0]) * self.inv_cell).floor() as i32;
        let cz = ((z - self.origin[1]) * self.inv_cell).floor() as i32;
        (cx.clamp(0, self.cols - 1), cz.clamp(0, self.rows - 1))
    }

    /// Re-buckets every entity (`positions` indexed by dense index).
    pub fn rebuild(&mut self, positions: &[[f32; 3]]) {
        self.rebuild_where(positions, |_| true);
    }

    /// Re-buckets the entities for which `include(dense index)` holds; the
    /// others are in no cell (the castle grid leaves out characters in dorm
    /// instances, whose room coordinates overlap the castle's).
    pub fn rebuild_where(&mut self, positions: &[[f32; 3]], include: impl Fn(usize) -> bool) {
        let cells = (self.cols * self.rows) as usize;
        self.starts.clear();
        self.starts.resize(cells + 1, 0);
        self.cell_of.clear();
        let mut count = 0;
        for (i, p) in positions.iter().enumerate() {
            if !include(i) {
                self.cell_of.push(EXCLUDED);
                continue;
            }
            let (cx, cz) = self.cell_coords(p[0], p[2]);
            let c = (cz * self.cols + cx) as u32;
            self.cell_of.push(c);
            self.starts[c as usize + 1] += 1;
            count += 1;
        }
        for c in 0..cells {
            self.starts[c + 1] += self.starts[c];
        }
        self.items.clear();
        self.items.resize(count, 0);
        // Fill using `starts[c]` as a cursor, then shift the cursors back.
        for (i, &c) in self.cell_of.iter().enumerate() {
            if c == EXCLUDED {
                continue;
            }
            let slot = &mut self.starts[c as usize];
            self.items[*slot as usize] = i as u32;
            *slot += 1;
        }
        for c in (1..=cells).rev() {
            self.starts[c] = self.starts[c - 1];
        }
        self.starts[0] = 0;
    }

    /// Entities in one cell (coordinates must be inside the grid).
    #[inline]
    pub fn cell(&self, cx: i32, cz: i32) -> &[u32] {
        let c = (cz * self.cols + cx) as usize;
        &self.items[self.starts[c] as usize..self.starts[c + 1] as usize]
    }

    /// Visits every entity in the cells of Chebyshev ring `r` around `(cx, cz)`.
    /// Returns false once the ring lies entirely outside the grid (so every
    /// larger ring does too).
    pub fn for_each_in_ring(&self, cx: i32, cz: i32, r: i32, mut f: impl FnMut(u32)) -> bool {
        let (z0, z1) = (cz - r, cz + r);
        let (x0, x1) = (cx - r, cx + r);
        if x0 < 0 && z0 < 0 && x1 >= self.cols && z1 >= self.rows {
            return false;
        }
        for z in z0.max(0)..=z1.min(self.rows - 1) {
            if z == z0 || z == z1 || r == 0 {
                for x in x0.max(0)..=x1.min(self.cols - 1) {
                    self.cell(x, z).iter().for_each(|&i| f(i));
                }
            } else {
                if x0 >= 0 {
                    self.cell(x0, z).iter().for_each(|&i| f(i));
                }
                if x1 < self.cols {
                    self.cell(x1, z).iter().for_each(|&i| f(i));
                }
            }
        }
        true
    }

    /// Visits every entity whose cell overlaps the square of half-size `radius`
    /// around `(x, z)`. Callers filter by exact distance.
    pub fn for_each_near(&self, x: f32, z: f32, radius: f32, mut f: impl FnMut(u32)) {
        let (x0, z0) = self.cell_coords(x - radius, z - radius);
        let (x1, z1) = self.cell_coords(x + radius, z + radius);
        for cz in z0..=z1 {
            for cx in x0..=x1 {
                self.cell(cx, cz).iter().for_each(|&i| f(i));
            }
        }
    }

    /// The minimum horizontal distance from a point in cell `(cx, cz)` to any
    /// entity outside rings `0..=r`: everything not yet visited is at least
    /// this far away.
    pub fn ring_clearance(r: i32) -> f32 {
        r as f32 * CELL_SIZE
    }

    /// A tighter [`Grid::ring_clearance`] for a known point `(x, z)`: the
    /// distance from it to the nearest edge of the block of cells in rings
    /// `0..=r` around it. Edges on the grid's border don't count (nothing lies
    /// beyond them: outside positions clamp into the border cells, which the
    /// block then contains). A crowd query can often stop a ring earlier.
    pub fn clearance(&self, x: f32, z: f32, r: i32) -> f32 {
        let (cx, cz) = self.cell_coords(x, z);
        let mut d = f32::INFINITY;
        let edge = |origin: f32, cell: i32| origin + cell as f32 * CELL_SIZE;
        if cx - r > 0 {
            d = d.min(x - edge(self.origin[0], cx - r));
        }
        if cx + r < self.cols - 1 {
            d = d.min(edge(self.origin[0], cx + r + 1) - x);
        }
        if cz - r > 0 {
            d = d.min(z - edge(self.origin[1], cz - r));
        }
        if cz + r < self.rows - 1 {
            d = d.min(edge(self.origin[1], cz + r + 1) - z);
        }
        // A point clamped in from outside the grid can lie beyond its own
        // cell; the ring bound still holds for it.
        d.max(Self::ring_clearance(r))
    }

    /// Largest ring that can still contain cells.
    pub fn max_ring(&self) -> i32 {
        self.cols.max(self.rows)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn collect_ring(g: &Grid, x: f32, z: f32, r: i32) -> Vec<u32> {
        let (cx, cz) = g.cell_coords(x, z);
        let mut v = Vec::new();
        g.for_each_in_ring(cx, cz, r, |i| v.push(i));
        v.sort();
        v
    }

    #[test]
    fn rebuild_buckets_every_entity_once() {
        let mut g = Grid::new([0.0, 0.0], [100.0, 100.0]);
        let pos: Vec<[f32; 3]> = (0..500)
            .map(|i| [(i * 7 % 100) as f32, 0.0, (i * 13 % 100) as f32])
            .collect();
        g.rebuild(&pos);
        let mut seen = vec![0u32; pos.len()];
        for r in 0..=g.max_ring() {
            g.for_each_in_ring(0, 0, r, |i| seen[i as usize] += 1);
        }
        assert!(
            seen.iter().all(|&n| n == 1),
            "every entity visited exactly once"
        );

        // A smaller rebuild reuses the buffers and forgets the old entities.
        g.rebuild(&pos[..3]);
        let mut count = 0;
        g.for_each_near(50.0, 50.0, 200.0, |_| count += 1);
        assert_eq!(count, 3);

        // Left-out entities are in no cell; the others keep their indices.
        g.rebuild_where(&pos, |i| i % 2 == 1);
        let mut found = Vec::new();
        g.for_each_near(50.0, 50.0, 200.0, |i| found.push(i));
        found.sort();
        assert_eq!(found, (1..500).step_by(2).collect::<Vec<u32>>());
    }

    #[test]
    fn rings_grow_outward() {
        let mut g = Grid::new([0.0, 0.0], [80.0, 80.0]);
        // Entity 0 at the centre, 1 one cell east, 2 two cells north-west.
        g.rebuild(&[
            [40.0, 0.0, 40.0],
            [40.0 + CELL_SIZE, 0.0, 40.0],
            [40.0 - 2.0 * CELL_SIZE, 0.0, 40.0 - 2.0 * CELL_SIZE],
        ]);
        assert_eq!(collect_ring(&g, 40.0, 40.0, 0), vec![0]);
        assert_eq!(collect_ring(&g, 40.0, 40.0, 1), vec![1]);
        assert_eq!(collect_ring(&g, 40.0, 40.0, 2), vec![2]);
        let (cx, cz) = g.cell_coords(40.0, 40.0);
        assert!(!g.for_each_in_ring(cx, cz, g.max_ring() + 1, |_| {}));
    }

    #[test]
    fn clearance_is_a_true_lower_bound() {
        let mut g = Grid::new([0.0, 0.0], [80.0, 80.0]);
        // Entities everywhere, including far outside the grid.
        let mut pos = Vec::new();
        for i in 0..60 {
            for j in 0..60 {
                pos.push([i as f32 * 2.0 - 20.0, 0.0, j as f32 * 2.0 - 20.0]);
            }
        }
        pos.push([-300.0, 0.0, 40.0]);
        pos.push([40.0, 0.0, 500.0]);
        g.rebuild(&pos);
        for &(x, z) in &[
            (40.3, 41.7),
            (1.0, 79.0),
            (-30.0, 40.0),
            (12.0, 3.9),
            (95.0, 95.0),
        ] {
            let (cx, cz) = g.cell_coords(x, z);
            let mut inside = vec![false; pos.len()];
            for r in 0..6 {
                g.for_each_in_ring(cx, cz, r, |i| inside[i as usize] = true);
                let c = g.clearance(x, z, r);
                assert!(c >= Grid::ring_clearance(r));
                for (i, p) in pos.iter().enumerate().filter(|(i, _)| !inside[*i]) {
                    let d = ((p[0] - x).powi(2) + (p[2] - z).powi(2)).sqrt();
                    assert!(
                        d >= c - 1e-4,
                        "({x}, {z}) ring {r}: entity {i} at {d} < {c}"
                    );
                }
            }
        }
        // Mid-cell, the bound beats the plain ring bound by the offset.
        let (x, z) = (40.0 + CELL_SIZE * 0.5, 40.0 + CELL_SIZE * 0.5);
        assert!(g.clearance(x, z, 1) > Grid::ring_clearance(1) + CELL_SIZE * 0.25);
    }

    #[test]
    fn outside_points_clamp_to_the_border() {
        let mut g = Grid::new([0.0, 0.0], [10.0, 10.0]);
        g.rebuild(&[[-500.0, 0.0, 9000.0]]);
        let mut found = Vec::new();
        // Any query that also clamps to that corner cell finds it.
        g.for_each_near(-50.0, 300.0, 1.0, |i| found.push(i));
        assert_eq!(found, vec![0]);
        found.clear();
        g.for_each_near(1.0, 1.0, 1.0, |i| found.push(i));
        assert!(found.is_empty());
    }
}
