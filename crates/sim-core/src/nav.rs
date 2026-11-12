//! The walkable-surface graph and the path search over it (the
//! wayfinding trail, the Monstra Viam charm; ported from the load-test bots'
//! `tools/loadtest/src/nav.rs`, which now builds on this module).
//!
//! The layout is a set of axis-aligned rectangles ([`Surface`]). Each
//! rectangle is convex, so a straight line between two points inside one
//! surface stays on it. Navigation therefore only needs the *portals* where
//! two surfaces connect (a shared edge or an overlap at matching heights) and
//! a shortest path over them:
//!
//! - every portal has one point inside each surface, set back from the shared
//!   edge, so a walker crosses it squarely instead of grazing a door frame;
//! - every waypoint keeps [`CLEARANCE`] from the edges of the surface it lies
//!   in, so the character's whole footprint (collision radius 0.3 m) stays on
//!   that surface and `move_and_slide` never has to slide.
//!
//! Furniture the layout does not know about (the Great Hall's house tables
//! and benches, the staff table: castle D1, docs/art/castle-kit.md) is
//! given as [`KeepOut`] rectangles: a straight leg that would cross one walks
//! round its corners instead (a shortest path over the corners inside the
//! leg's surface). The layout's [`AreaKind::Solid`] boxes (the gatehouse
//! towers) are kept out of the same way on the surfaces at their height.
//!
//! Pure and deterministic: the browser client builds one graph for the
//! player's house ([`crate::World::for_house`]: other houses' gated rooms are
//! walls) and asks it for the trail to a key place ([`crate::key_places`]);
//! the server never uses it.
//!
//! The trail's graph leaves out the hidden passages
//! ([`trail_allows`]: a secret surface is never on it, in any staircase
//! state); the load-test bots' graph keeps them (they may walk them).

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};
use std::sync::Arc;

use serde::{Deserialize, Serialize};

use crate::layout::{Surface, SurfaceKind};
use crate::movement::MoveParams;
use crate::world::World;

/// Distance every waypoint keeps from the edges of its surface (metres):
/// the collision radius plus a margin.
pub const CLEARANCE: f32 = 0.45;

/// How far a portal's points sit back from the shared edge, at most (metres).
const PORTAL_SETBACK: f32 = 0.6;

/// Spacing of the height samples along a shared edge (metres).
const EDGE_SAMPLE: f32 = 0.1;

/// Shortest stretch of a shared edge, after the clearance at both ends, along
/// which the two surfaces must meet at the same height to count as connected.
/// Excludes the sides of a stair, which touch the floor only near its foot.
const MIN_PORTAL_RUN: f32 = 0.5;

/// Two surfaces are rectangles touching along an edge when their overlap is
/// thinner than this (metres).
const EDGE_THICKNESS: f32 = 0.01;

/// A walker's centre keeps this far from a keep-out rectangle (metres): the
/// same as from a surface's edge, so the whole body stays clear of it.
pub const KEEP_OUT_CLEARANCE: f32 = CLEARANCE;

/// Detour waypoints sit this much further out than the clearance, off each
/// corner (metres), so a leg along a rectangle's side never grazes it.
const KEEP_OUT_CORNER: f32 = 0.05;

/// Consecutive path points closer than this are one (metres).
const SAME_POINT: f32 = 0.01;

/// Two grounds surfaces meet along long edges (the grounds are
/// tiled in 40 m cells), so the edge between them carries a portal every
/// this many metres instead of one at its middle: a route across the grounds
/// strays at most half of it from the straight line at each seam.
pub const GROUNDS_PORTAL_SPACING: f32 = 8.0;

/// On a heightfield the trail's waypoints come at most this far
/// apart (metres), each on the ground, so a straight leg over a hill follows
/// it instead of cutting through it.
pub const HEIGHTFIELD_PATH_STEP: f32 = 2.0;

/// The surfaces the Monstra Viam trail may route over (every surface
/// but the hidden passages): `Nav::new(world, keep_out, trail_allows)`.
pub fn trail_allows(s: &Surface) -> bool {
    !s.secret
}

/// A plan rectangle of furniture the layout does not model: `min` and `max`
/// as [x, z] in metres, the footprint itself (the walkers' clearance is added
/// in code). Written in data as `{ id, min, max, cell? }`.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct KeepOut {
    /// What stands there (the castle kit placement's id), for reports and tests.
    pub id: String,
    pub min: [f32; 2],
    pub max: [f32; 2],
    /// Only on this layout cell's surfaces (castle M5a: the Undercroft's well
    /// stands under the first-floor corridor, whose floor stays open); on
    /// every surface in plan when absent.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cell: Option<String>,
}

impl KeepOut {
    /// Whether the footprint stands on the surfaces of layout cell `cell`.
    pub fn on_cell(&self, cell: &str) -> bool {
        self.cell.as_deref().is_none_or(|c| c == cell)
    }

    /// Whether `p` lies on the footprint (edges included).
    pub fn contains(&self, p: [f32; 2]) -> bool {
        p[0] >= self.min[0] && p[0] <= self.max[0] && p[1] >= self.min[1] && p[1] <= self.max[1]
    }

    /// The rectangle a walker's centre stays out of: the footprint grown by `by`.
    pub fn grown(&self, by: f32) -> Rect {
        Rect {
            min: [self.min[0] - by, self.min[1] - by],
            max: [self.max[0] + by, self.max[1] + by],
        }
    }
}

/// An axis-aligned plan rectangle (x, z).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Rect {
    pub min: [f32; 2],
    pub max: [f32; 2],
}

/// Slack for the open-interior tests below (metres): a point on a side, or a
/// leg along it, does not count as inside.
const RECT_EPS: f32 = 1e-3;

impl Rect {
    /// `p` strictly inside (not on a side).
    pub fn holds(&self, p: [f32; 2]) -> bool {
        p[0] > self.min[0] + RECT_EPS
            && p[0] < self.max[0] - RECT_EPS
            && p[1] > self.min[1] + RECT_EPS
            && p[1] < self.max[1] - RECT_EPS
    }

    /// Whether the segment `a`-`b` passes through the open interior
    /// (Liang-Barsky clipping against the rectangle shrunk by the slack).
    pub fn crossed_by(&self, a: [f32; 2], b: [f32; 2]) -> bool {
        let (mut t0, mut t1) = (0.0f32, 1.0f32);
        for axis in 0..2 {
            let d = b[axis] - a[axis];
            let (lo, hi) = (self.min[axis] + RECT_EPS, self.max[axis] - RECT_EPS);
            if d.abs() < 1e-9 {
                if a[axis] <= lo || a[axis] >= hi {
                    return false;
                }
                continue;
            }
            let (mut ta, mut tb) = ((lo - a[axis]) / d, (hi - a[axis]) / d);
            if ta > tb {
                std::mem::swap(&mut ta, &mut tb);
            }
            t0 = t0.max(ta);
            t1 = t1.min(tb);
            if t0 >= t1 {
                return false;
            }
        }
        true
    }

    /// The four detour waypoints just outside the corners.
    fn corners(&self, pad: f32) -> [[f32; 2]; 4] {
        let (x0, z0, x1, z1) = (
            self.min[0] - pad,
            self.min[1] - pad,
            self.max[0] + pad,
            self.max[1] + pad,
        );
        [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]
    }
}

/// A connection between surfaces `a` and `b`: `a_pt` lies inside `a`, `b_pt`
/// inside `b`, and the straight line between them crosses from one to the other.
#[derive(Debug, Clone, Copy)]
pub struct Portal {
    pub a: u32,
    pub b: u32,
    pub a_pt: [f32; 2],
    pub b_pt: [f32; 2],
}

/// One way through a portal: enter at `from_pt` on `from`, leave at `to_pt` on `to`.
#[derive(Debug, Clone, Copy)]
struct Crossing {
    from: u32,
    to: u32,
    from_pt: [f32; 2],
    to_pt: [f32; 2],
}

/// The walkable graph of a layout.
pub struct Nav {
    world: Arc<World>,
    params: MoveParams,
    portals: Vec<Portal>,
    crossings: Vec<Crossing>,
    /// Crossing indices leaving each surface.
    leaving: Vec<Vec<u32>>,
    /// The keep-out footprints as given.
    keep_out: Vec<KeepOut>,
    /// Every rectangle a walker's centre stays out of: the keep-outs grown by
    /// the clearance, then the solids grown the same.
    blocks: Vec<Rect>,
    /// The `blocks` that stand on each surface.
    surface_blocks: Vec<Vec<u32>>,
    /// Walking lengths from entering a surface by one crossing to leaving it by
    /// another, for the pairs a keep-out stands between (infinite: no way
    /// round inside that surface). Every other pair walks straight.
    detoured: HashMap<(u32, u32), f32>,
    /// Per surface with blocks: the detour waypoints round them and which see
    /// each other, found once (a library's shelves or a courtyard's piers are
    /// dozens of blocks; a detour then only joins its two ends to them).
    corners: Vec<Option<Corners>>,
}

/// The corner waypoints of one surface's blocks ([`Rect::corners`] inside
/// the surface's clearance and outside every other block) and, for each,
/// the others it sees in a straight line with the distance.
struct Corners {
    nodes: Vec<[f32; 2]>,
    sees: Vec<Vec<(u32, f32)>>,
}

impl Nav {
    /// The graph of `world`'s surfaces that `allows` accepts (a portal joins
    /// two accepted surfaces only), walking round `keep_out` and the
    /// layout's solids.
    pub fn new(world: Arc<World>, keep_out: &[KeepOut], allows: impl Fn(&Surface) -> bool) -> Nav {
        let params = MoveParams::default();
        let surfaces = world.surfaces();
        let mut blocks: Vec<Rect> = keep_out
            .iter()
            .map(|k| k.grown(KEEP_OUT_CLEARANCE))
            .collect();
        let mut surface_blocks: Vec<Vec<u32>> = surfaces
            .iter()
            .map(|s| {
                keep_out
                    .iter()
                    .enumerate()
                    .filter(|(_, k)| k.on_cell(&s.cell))
                    .map(|(i, _)| i as u32)
                    .collect()
            })
            .collect();
        // Every solid (the layout's solid areas and its furniture), by its
        // bounding box in plan: a round table or a turned bench is walked
        // round as the box round it.
        for solid in world.solids() {
            let [x0, z0, x1, z1] = solid.aabb();
            let i = blocks.len() as u32;
            blocks.push(Rect {
                min: [x0 - KEEP_OUT_CLEARANCE, z0 - KEEP_OUT_CLEARANCE],
                max: [x1 + KEEP_OUT_CLEARANCE, z1 + KEEP_OUT_CLEARANCE],
            });
            // A solid stands on a surface when the surface's ground
            // under the solid's footprint reaches into its height span (the
            // steps up to a pitch stand rise out of the stand's base).
            let (min, max) = ([x0, z0], [x1, z1]);
            for (s, list) in surfaces.iter().zip(&mut surface_blocks) {
                let overlaps = s.min[0] < max[0]
                    && min[0] < s.max[0]
                    && s.min[1] < max[1]
                    && min[1] < s.max[1];
                if overlaps
                    && s.height_range_in(min, max)
                        .is_some_and(|(lo, hi)| lo <= solid.y[1] && hi >= solid.y[0])
                {
                    list.push(i);
                }
            }
        }
        let accepted: Vec<bool> = surfaces.iter().map(&allows).collect();
        let mut portals = Vec::new();
        for a in 0..surfaces.len() {
            if !accepted[a] {
                continue;
            }
            for b in a + 1..surfaces.len() {
                if !accepted[b] {
                    continue;
                }
                let mut pair: Vec<Rect> = surface_blocks[a]
                    .iter()
                    .chain(&surface_blocks[b])
                    .map(|&i| blocks[i as usize])
                    .collect();
                pair.dedup();
                for (a_pt, b_pt) in portal_between(&surfaces[a], &surfaces[b], &params, &pair) {
                    portals.push(Portal {
                        a: a as u32,
                        b: b as u32,
                        a_pt,
                        b_pt,
                    });
                }
            }
        }
        let mut crossings = Vec::with_capacity(portals.len() * 2);
        let mut leaving = vec![Vec::new(); surfaces.len()];
        for p in &portals {
            for c in [
                Crossing {
                    from: p.a,
                    to: p.b,
                    from_pt: p.a_pt,
                    to_pt: p.b_pt,
                },
                Crossing {
                    from: p.b,
                    to: p.a,
                    from_pt: p.b_pt,
                    to_pt: p.a_pt,
                },
            ] {
                leaving[c.from as usize].push(crossings.len() as u32);
                crossings.push(c);
            }
        }
        let mut nav = Nav {
            world,
            params,
            portals,
            crossings,
            leaving,
            keep_out: keep_out.to_vec(),
            blocks,
            surface_blocks,
            detoured: HashMap::new(),
            corners: Vec::new(),
        };
        nav.corners = (0..nav.leaving.len() as u32)
            .map(|s| nav.corners_of(s))
            .collect();
        if !nav.blocks.is_empty() {
            let mut detoured = HashMap::new();
            for (i, inn) in nav.crossings.iter().enumerate() {
                for &out in &nav.leaving[inn.to as usize] {
                    let o = &nav.crossings[out as usize];
                    if nav.blocked(inn.to_pt, o.from_pt, inn.to) {
                        detoured.insert((i as u32, out), nav.hop_len(inn.to_pt, o.from_pt, inn.to));
                    }
                }
            }
            nav.detoured = detoured;
        }
        nav
    }

    pub fn world(&self) -> &World {
        &self.world
    }

    /// The world, shared.
    pub fn world_arc(&self) -> &Arc<World> {
        &self.world
    }

    pub fn params(&self) -> &MoveParams {
        &self.params
    }

    pub fn portals(&self) -> &[Portal] {
        &self.portals
    }

    /// The keep-out footprints as given.
    pub fn keep_out(&self) -> &[KeepOut] {
        &self.keep_out
    }

    /// The keep-out footprint a walker at `p` ([x, y, z]) stands on, if any
    /// (a cell's own keep-outs only on that cell's ground).
    pub fn keep_out_at(&self, p: [f32; 3]) -> Option<&KeepOut> {
        let cell = self
            .world
            .ground_at(p[0], p[2], p[1])
            .map(|g| self.world.surfaces()[g.surface.0 as usize].cell.as_str());
        self.keep_out.iter().find(|k| {
            k.contains([p[0], p[2]]) && (k.cell.is_none() || cell.is_some_and(|c| k.on_cell(c)))
        })
    }

    /// Whether `p` (on `surface`) stands where a walker's centre should not:
    /// inside a keep-out or solid grown by the clearance.
    pub fn blocked_at(&self, p: [f32; 2], surface: u32) -> bool {
        self.blocks_on(surface).any(|b| b.holds(p))
    }

    /// The surfaces reachable from `seeds` over the portals.
    pub fn reachable_from(&self, seeds: impl IntoIterator<Item = u32>) -> Vec<bool> {
        let mut reachable = vec![false; self.leaving.len()];
        let mut stack: Vec<u32> = seeds.into_iter().collect();
        while let Some(s) = stack.pop() {
            let Some(seen) = reachable.get_mut(s as usize) else {
                continue;
            };
            if std::mem::replace(seen, true) {
                continue;
            }
            stack.extend(
                self.leaving[s as usize]
                    .iter()
                    .map(|&c| self.crossings[c as usize].to)
                    .filter(|&t| !reachable[t as usize]),
            );
        }
        reachable
    }

    /// The surface a walker at `p` ([x, y, z]) stands on.
    pub fn surface_at(&self, p: [f32; 3]) -> Option<u32> {
        self.world.ground_at(p[0], p[2], p[1]).map(|g| g.surface.0)
    }

    /// The rectangles that stand on `surface`.
    fn blocks_on(&self, surface: u32) -> impl Iterator<Item = &Rect> {
        self.surface_blocks
            .get(surface as usize)
            .into_iter()
            .flatten()
            .map(|&i| &self.blocks[i as usize])
    }

    /// The shortest route from `from` (a point on surface `from_surface`) to
    /// `to` (on `to_surface`), as XZ waypoints ending at `to`. `None` if the
    /// target cannot be reached.
    pub fn route(
        &self,
        from: [f32; 2],
        from_surface: u32,
        to: [f32; 2],
        to_surface: u32,
    ) -> Option<Vec<[f32; 2]>> {
        self.route_on(from, from_surface, to, to_surface)
            .map(|path| path.into_iter().map(|(p, _)| p).collect())
    }

    /// The trail between two points of the world ([x, y, z], each on the
    /// ground of the storey nearest its height): the route's waypoints from
    /// `from` (first) to `to` (last), each with its ground height, no two
    /// the same. `None` when either point is off the ground or no way joins
    /// them. A leg across a heightfield gains a waypoint every
    /// [`HEIGHTFIELD_PATH_STEP`], on the ground.
    pub fn path(&self, from: [f32; 3], to: [f32; 3]) -> Option<Vec<[f32; 3]>> {
        let a = self.surface_at(from)?;
        let b = self.surface_at(to)?;
        let (fa, tb) = ([from[0], from[2]], [to[0], to[2]]);
        let route = self.route_on(fa, a, tb, b)?;
        let surfaces = self.world.surfaces();
        let mut out: Vec<[f32; 3]> = Vec::with_capacity(route.len() + 1);
        let height = |p: [f32; 2], s: u32| surfaces[s as usize].height_at(p[0], p[1]);
        out.push([fa[0], height(fa, a), fa[1]]);
        let mut on = a;
        for (p, s) in route {
            let q = [p[0], height(p, s), p[1]];
            let last = out[out.len() - 1];
            let d = dist([last[0], last[2]], p);
            if d < SAME_POINT && (last[1] - q[1]).abs() < 0.05 {
                continue;
            }
            // A leg inside one heightfield surface: follow its ground.
            if s == on && surfaces[s as usize].heights.is_some() && d > HEIGHTFIELD_PATH_STEP {
                let n = (d / HEIGHTFIELD_PATH_STEP).ceil() as usize;
                for k in 1..n {
                    let t = k as f32 / n as f32;
                    let m = [
                        last[0] + (p[0] - last[0]) * t,
                        last[2] + (p[1] - last[2]) * t,
                    ];
                    out.push([m[0], height(m, s), m[1]]);
                }
            }
            out.push(q);
            on = s;
        }
        Some(out)
    }

    /// The surfaces a trail from `from` to `to` crosses, in order
    /// (the start's first, the end's last, no two in a row the same), or
    /// `None` like [`Nav::path`]. The client re-plans the Monstra Viam trail when
    /// a moving flight among them starts to swing.
    pub fn path_surfaces(&self, from: [f32; 3], to: [f32; 3]) -> Option<Vec<u32>> {
        let a = self.surface_at(from)?;
        let b = self.surface_at(to)?;
        let route = self.route_on([from[0], from[2]], a, [to[0], to[2]], b)?;
        let mut out = vec![a];
        for (_, s) in route {
            if out.last() != Some(&s) {
                out.push(s);
            }
        }
        Some(out)
    }

    /// Length of a 3D polyline (metres).
    pub fn length(path: &[[f32; 3]]) -> f32 {
        path.windows(2)
            .map(|w| {
                let d = [w[1][0] - w[0][0], w[1][1] - w[0][1], w[1][2] - w[0][2]];
                (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt()
            })
            .sum()
    }

    /// [`Nav::route`] with the surface each waypoint lies on.
    ///
    /// Dijkstra over portal crossings: the state is "just crossed into a
    /// surface at this point", and the cost is the walking distance.
    fn route_on(
        &self,
        from: [f32; 2],
        from_surface: u32,
        to: [f32; 2],
        to_surface: u32,
    ) -> Option<Vec<([f32; 2], u32)>> {
        if from_surface as usize >= self.leaving.len() || to_surface as usize >= self.leaving.len()
        {
            return None;
        }
        if from_surface == to_surface {
            // Straight (or round the keep-outs) within the surface; when they
            // close the way, out and back in by other doors.
            let mut path = Vec::new();
            if self.leg(from, to, from_surface, &mut path) {
                return Some(path.into_iter().map(|p| (p, from_surface)).collect());
            }
        }
        let n = self.crossings.len();
        let mut best = vec![f32::INFINITY; n];
        let mut prev = vec![u32::MAX; n];
        let mut heap = BinaryHeap::new();
        for &c in &self.leaving[from_surface as usize] {
            let x = &self.crossings[c as usize];
            let cost = self.hop_len(from, x.from_pt, from_surface) + dist(x.from_pt, x.to_pt);
            if cost < best[c as usize] {
                best[c as usize] = cost;
                heap.push(Node { cost, crossing: c });
            }
        }
        let mut goal: Option<(f32, u32)> = None;
        while let Some(Node { cost, crossing }) = heap.pop() {
            if cost > best[crossing as usize] {
                continue;
            }
            if goal.is_some_and(|(g, _)| cost >= g) {
                break;
            }
            let here = self.crossings[crossing as usize];
            if here.to == to_surface {
                let total = cost + self.hop_len(here.to_pt, to, to_surface);
                if goal.is_none_or(|(g, _)| total < g) {
                    goal = Some((total, crossing));
                }
                continue;
            }
            for &next in &self.leaving[here.to as usize] {
                let x = &self.crossings[next as usize];
                let walk = match self.detoured.get(&(crossing, next)) {
                    Some(&d) => d,
                    None => dist(here.to_pt, x.from_pt),
                };
                let c = cost + walk + dist(x.from_pt, x.to_pt);
                if c < best[next as usize] {
                    best[next as usize] = c;
                    prev[next as usize] = crossing;
                    heap.push(Node {
                        cost: c,
                        crossing: next,
                    });
                }
            }
        }
        let (total, mut c) = goal?;
        if !total.is_finite() {
            return None;
        }
        let mut chain = vec![c];
        while prev[c as usize] != u32::MAX {
            c = prev[c as usize];
            chain.push(c);
        }
        chain.reverse();
        // Walk the chain: each leg inside a surface (round any keep-out), then the portal.
        let mut path: Vec<([f32; 2], u32)> = Vec::new();
        let mut leg = Vec::new();
        let (mut at, mut on) = (from, from_surface);
        for &c in &chain {
            let x = self.crossings[c as usize];
            leg.clear();
            if !self.leg(at, x.from_pt, on, &mut leg) {
                return None;
            }
            path.extend(leg.iter().map(|&p| (p, on)));
            path.push((x.to_pt, x.to));
            (at, on) = (x.to_pt, x.to);
        }
        leg.clear();
        if !self.leg(at, to, on, &mut leg) {
            return None;
        }
        path.extend(leg.iter().map(|&p| (p, on)));
        Some(path)
    }

    /// Appends the waypoints of a straight walk from `a` to `b` inside
    /// `surface` (ending with `b`), round the keep-outs; false when the
    /// keep-outs close the way inside that surface.
    ///
    /// A keep-out the walk starts or ends in is ignored (a walker put there
    /// by a correction walks out; nothing chooses such a point). Otherwise the
    /// detour is the shortest path over the grown rectangles' corners that
    /// lie inside the surface's clearance, each hop a straight line no
    /// rectangle's interior crosses.
    fn leg(&self, a: [f32; 2], b: [f32; 2], surface: u32, out: &mut Vec<[f32; 2]>) -> bool {
        if !self.blocked(a, b, surface) {
            out.push(b);
            return true;
        }
        let Some(hops) = self.detour(a, b, surface) else {
            return false;
        };
        out.extend(hops);
        true
    }

    /// Whether a keep-out on `surface` (not one `a` or `b` is in) stands across `a`-`b`.
    fn blocked(&self, a: [f32; 2], b: [f32; 2], surface: u32) -> bool {
        self.blocks_on(surface)
            .any(|r| !r.holds(a) && !r.holds(b) && r.crossed_by(a, b))
    }

    /// The walking length of [`Nav::leg`] (infinite when there is no way).
    fn hop_len(&self, a: [f32; 2], b: [f32; 2], surface: u32) -> f32 {
        if !self.blocked(a, b, surface) {
            return dist(a, b);
        }
        match self.detour(a, b, surface) {
            Some(hops) => {
                let mut at = a;
                let mut len = 0.0;
                for p in hops {
                    len += dist(at, p);
                    at = p;
                }
                len
            }
            None => f32::INFINITY,
        }
    }

    /// The corner waypoints of `surface`'s blocks and their sight lines
    /// (`None` without blocks).
    fn corners_of(&self, surface: u32) -> Option<Corners> {
        let active: Vec<Rect> = self.blocks_on(surface).copied().collect();
        if active.is_empty() {
            return None;
        }
        let s = &self.world.surfaces()[surface as usize];
        let (lo, hi) = (
            [s.min[0] + CLEARANCE, s.min[1] + CLEARANCE],
            [s.max[0] - CLEARANCE, s.max[1] - CLEARANCE],
        );
        let mut nodes = Vec::new();
        for r in &active {
            for p in r.corners(KEEP_OUT_CORNER) {
                let on_surface = p[0] >= lo[0] && p[0] <= hi[0] && p[1] >= lo[1] && p[1] <= hi[1];
                if on_surface && !active.iter().any(|o| o.holds(p)) {
                    nodes.push(p);
                }
            }
        }
        let mut sees = vec![Vec::new(); nodes.len()];
        for i in 0..nodes.len() {
            for j in i + 1..nodes.len() {
                if !active.iter().any(|r| r.crossed_by(nodes[i], nodes[j])) {
                    let d = dist(nodes[i], nodes[j]);
                    sees[i].push((j as u32, d));
                    sees[j].push((i as u32, d));
                }
            }
        }
        Some(Corners { nodes, sees })
    }

    /// The detour's waypoints from `a` to `b` (ending with `b`), if any: the
    /// shortest way over the surface's corner waypoints ([`Corners`]).
    fn detour(&self, a: [f32; 2], b: [f32; 2], surface: u32) -> Option<Vec<[f32; 2]>> {
        let blocks: Vec<&Rect> = self.blocks_on(surface).collect();
        if blocks.iter().any(|r| r.holds(a) || r.holds(b)) {
            // An end inside a block (a walker put there by a correction):
            // that block is ignored, so the cached sight lines do not hold.
            return self.detour_slow(a, b, surface);
        }
        let Some(c) = self.corners.get(surface as usize).and_then(Option::as_ref) else {
            return Some(vec![b]);
        };
        let clear = |p: [f32; 2], q: [f32; 2]| !blocks.iter().any(|r| r.crossed_by(p, q));
        if clear(a, b) {
            return Some(vec![b]);
        }
        let n = c.nodes.len();
        let to_b: Vec<Option<f32>> = c
            .nodes
            .iter()
            .map(|&p| clear(p, b).then(|| dist(p, b)))
            .collect();
        let mut best = vec![f32::INFINITY; n];
        let mut prev = vec![u32::MAX; n];
        let mut heap = BinaryHeap::new();
        for (i, &p) in c.nodes.iter().enumerate() {
            if clear(a, p) {
                best[i] = dist(a, p);
                heap.push(Node {
                    cost: best[i],
                    crossing: i as u32,
                });
            }
        }
        let mut goal: Option<(f32, u32)> = None;
        while let Some(Node { cost, crossing: u }) = heap.pop() {
            if cost > best[u as usize] {
                continue;
            }
            if goal.is_some_and(|(g, _)| cost >= g) {
                break;
            }
            if let Some(d) = to_b[u as usize]
                && goal.is_none_or(|(g, _)| cost + d < g)
            {
                goal = Some((cost + d, u));
            }
            for &(v, d) in &c.sees[u as usize] {
                let nc = cost + d;
                if nc < best[v as usize] {
                    best[v as usize] = nc;
                    prev[v as usize] = u;
                    heap.push(Node {
                        cost: nc,
                        crossing: v,
                    });
                }
            }
        }
        let (_, mut v) = goal?;
        let mut hops = vec![b];
        loop {
            hops.push(c.nodes[v as usize]);
            if prev[v as usize] == u32::MAX {
                break;
            }
            v = prev[v as usize];
        }
        hops.reverse();
        Some(hops)
    }

    /// [`Nav::detour`] without the cache: the blocks `a` or `b` stand in are left out.
    fn detour_slow(&self, a: [f32; 2], b: [f32; 2], surface: u32) -> Option<Vec<[f32; 2]>> {
        let active: Vec<Rect> = self
            .blocks_on(surface)
            .filter(|r| !r.holds(a) && !r.holds(b))
            .copied()
            .collect();
        let clear = |p: [f32; 2], q: [f32; 2]| !active.iter().any(|r| r.crossed_by(p, q));
        let s = &self.world.surfaces()[surface as usize];
        let (lo, hi) = (
            [s.min[0] + CLEARANCE, s.min[1] + CLEARANCE],
            [s.max[0] - CLEARANCE, s.max[1] - CLEARANCE],
        );
        let mut nodes = vec![a, b];
        for r in &active {
            for p in r.corners(KEEP_OUT_CORNER) {
                let on_surface = p[0] >= lo[0] && p[0] <= hi[0] && p[1] >= lo[1] && p[1] <= hi[1];
                if on_surface && !active.iter().any(|o| o.holds(p)) {
                    nodes.push(p);
                }
            }
        }
        // Dijkstra over the visibility graph (a few dozen nodes at most).
        let n = nodes.len();
        let mut best = vec![f32::INFINITY; n];
        let mut prev = vec![usize::MAX; n];
        let mut done = vec![false; n];
        best[0] = 0.0;
        loop {
            let u = (0..n)
                .filter(|&i| !done[i] && best[i].is_finite())
                .min_by(|&i, &j| best[i].total_cmp(&best[j]))?;
            if u == 1 {
                break;
            }
            done[u] = true;
            for v in 0..n {
                if done[v] || !clear(nodes[u], nodes[v]) {
                    continue;
                }
                let c = best[u] + dist(nodes[u], nodes[v]);
                if c < best[v] {
                    best[v] = c;
                    prev[v] = u;
                }
            }
        }
        let mut hops = Vec::new();
        let mut v = 1;
        while v != 0 {
            hops.push(nodes[v]);
            v = prev[v];
        }
        hops.reverse();
        Some(hops)
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
struct Node {
    cost: f32,
    crossing: u32,
}

impl Eq for Node {}

impl Ord for Node {
    /// Reversed so `BinaryHeap` pops the cheapest node first.
    fn cmp(&self, other: &Self) -> Ordering {
        other.cost.total_cmp(&self.cost)
    }
}

impl PartialOrd for Node {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Plan distance between two [x, z] points.
pub fn dist(a: [f32; 2], b: [f32; 2]) -> f32 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt()
}

/// Where `a` and `b` connect, as a point inside each; none if they don't.
///
/// Surfaces touching along an edge connect through the longest stretch of that
/// edge where their heights agree (within half a step), at its middle.
/// Keep-outs (`blocks`, grown by the clearance) split that stretch: one
/// portal in the middle of each part where neither point nor the crossing
/// touches one (the Great Hall's tables cross the joins of its floor
/// surfaces, and the owl lectern stands before the middle of the dais steps).
/// Overlapping surfaces connect at the matching point nearest the middle of the
/// overlap.
fn portal_between(
    a: &Surface,
    b: &Surface,
    params: &MoveParams,
    blocks: &[Rect],
) -> Vec<([f32; 2], [f32; 2])> {
    let grounds = a.kind == SurfaceKind::Grounds && b.kind == SurfaceKind::Grounds;
    let lo = [a.min[0].max(b.min[0]), a.min[1].max(b.min[1])];
    let hi = [a.max[0].min(b.max[0]), a.max[1].min(b.max[1])];
    let (w, d) = (hi[0] - lo[0], hi[1] - lo[1]);
    if w < -EDGE_THICKNESS || d < -EDGE_THICKNESS {
        return Vec::new();
    }
    let tolerance = params.step_height * 0.5;
    let matches = |x: f32, z: f32| (a.height_at(x, z) - b.height_at(x, z)).abs() <= tolerance;

    if w <= EDGE_THICKNESS || d <= EDGE_THICKNESS {
        // Shared edge. `n` is the axis across it, `t` the axis along it.
        let (n, t) = if w <= EDGE_THICKNESS { (0, 1) } else { (1, 0) };
        let edge = (lo[n] + hi[n]) * 0.5;
        let (start, end) = (lo[t] + CLEARANCE, hi[t] - CLEARANCE);
        if end - start < MIN_PORTAL_RUN {
            return Vec::new();
        }
        // Longest run of samples along the edge where the heights match.
        let samples = ((end - start) / EDGE_SAMPLE).ceil() as usize;
        let at = |i: usize| start + (end - start) * i as f32 / samples as f32;
        let point = |s: f32| if n == 0 { [edge, s] } else { [s, edge] };
        let (mut run, mut best) = (None::<usize>, None::<(usize, usize)>);
        for i in 0..=samples + 1 {
            let ok = i <= samples && {
                let [x, z] = point(at(i));
                matches(x, z)
            };
            match (ok, run) {
                (true, None) => run = Some(i),
                (false, Some(r)) => {
                    if best.is_none_or(|(b0, b1)| i - 1 - r > b1 - b0) {
                        best = Some((r, i - 1));
                    }
                    run = None;
                }
                _ => {}
            }
        }
        let Some((r0, r1)) = best else {
            return Vec::new();
        };
        if at(r1) - at(r0) < MIN_PORTAL_RUN {
            return Vec::new();
        }
        // Which side of the edge each surface lies on, and how deep it is.
        let a_low = (a.min[n] + a.max[n]) * 0.5 < edge;
        let setback = |s: &Surface| (PORTAL_SETBACK).min((s.max[n] - s.min[n]) * 0.5);
        let (sa, sb) = (setback(a), setback(b));
        let (a_n, b_n) = if a_low {
            (edge - sa, edge + sb)
        } else {
            (edge + sa, edge - sb)
        };
        let pt = |across: f32, along: f32| {
            if n == 0 {
                [across, along]
            } else {
                [along, across]
            }
        };
        let clear = |along: f32| {
            let (p, q) = (pt(a_n, along), pt(b_n, along));
            !blocks
                .iter()
                .any(|r| r.holds(p) || r.holds(q) || r.crossed_by(p, q))
        };
        // The run's clear parts (all of it without keep-outs: one portal at
        // its middle; between two grounds surfaces one every
        // GROUNDS_PORTAL_SPACING, each in the middle of its stretch).
        let mut out = Vec::new();
        let mut open: Option<f32> = None;
        for i in r0..=r1 + 1 {
            let ok = i <= r1 && clear(at(i));
            match (ok, open) {
                (true, None) => open = Some(at(i)),
                (false, Some(s0)) => {
                    let s1 = at(i - 1);
                    let pieces = if grounds {
                        ((s1 - s0) / GROUNDS_PORTAL_SPACING).ceil().max(1.0) as usize
                    } else {
                        1
                    };
                    for k in 0..pieces {
                        let m = s0 + (s1 - s0) * (k as f32 + 0.5) / pieces as f32;
                        out.push((pt(a_n, m), pt(b_n, m)));
                    }
                    open = None;
                }
                _ => {}
            }
        }
        return out;
    }

    // Overlap: the matching point nearest the middle, outside the blocks
    // (a pitch stand's steps rise out of the field beside its base).
    const GRID: usize = 16;
    let centre = [(lo[0] + hi[0]) * 0.5, (lo[1] + hi[1]) * 0.5];
    let mut best: Option<([f32; 2], f32)> = None;
    for i in 0..=GRID {
        for j in 0..=GRID {
            let x = lo[0] + w * i as f32 / GRID as f32;
            let z = lo[1] + d * j as f32 / GRID as f32;
            if matches(x, z) && !blocks.iter().any(|r| r.holds([x, z])) {
                let dd = dist([x, z], centre);
                if best.is_none_or(|(_, b)| dd < b) {
                    best = Some(([x, z], dd));
                }
            }
        }
    }
    best.map(|(p, _)| (p, p)).into_iter().collect()
}

#[cfg(test)]
mod tests {
    use protocol::House;

    use super::*;
    use crate::clock::Schedule;
    use crate::key_places::KeyPlaces;
    use crate::layout::{AreaKind, InteractableKind, Layout, SurfaceKind};

    const LAYOUT: &str = include_str!("../../../data/world/layout.json");
    const KEY_PLACES: &str = include_str!("../../../data/world/key_places.json");
    const SCHEDULE: &str = include_str!("../../../data/classes/schedule.json");
    /// The load test's scenarios: the client's trail walks round the `spread`
    /// crowd's keep-outs (every furniture footprint the layout leaves out).
    /// The furniture footprints the bots and the trail share.
    const KEEP_OUT: &str = include_str!("../../../data/world/keep_out.json");

    /// Two rooms joined through a door, with a stair from room A up to a
    /// balcony that also overlooks room B.
    const ROOMS: &str = r#"{
        "version": 1,
        "surfaces": [
            { "id": "a", "kind": "room", "name": "A", "level": 0, "cell": "entrance_hall",
              "min": [0, 0], "max": [10, 10], "y": 0, "ceiling": 5 },
            { "id": "door", "kind": "door", "name": "Door", "level": 0, "cell": "x",
              "min": [10, 4], "max": [11, 6], "y": 0, "ceiling": 3 },
            { "id": "b", "kind": "room", "name": "B", "level": 0, "cell": "x",
              "min": [11, 0], "max": [21, 10], "y": 0, "ceiling": 5 },
            { "id": "stair", "kind": "stair", "name": "Stair", "level": 0, "cell": "entrance_hall",
              "min": [0, 10], "max": [4, 18], "y": 0, "y_top": 4, "rise": "+z", "ceiling": 5 },
            { "id": "balcony", "kind": "room", "name": "Balcony", "level": 1, "cell": "x",
              "min": [0, 18], "max": [21, 22], "y": 4, "ceiling": 4 }
        ],
        "spawns": [{ "id": "s", "pos": [5, 0, 5], "yaw": 0 }]
    }"#;

    fn rooms(keep_out: &[KeepOut]) -> Nav {
        Nav::new(Arc::new(World::from_json(ROOMS).unwrap()), keep_out, |_| {
            true
        })
    }

    fn spread_keep_outs() -> Vec<KeepOut> {
        let v: serde_json::Value = serde_json::from_str(KEEP_OUT).unwrap();
        serde_json::from_value(v["keep_out"].clone()).unwrap()
    }

    fn assert_near(path: &[[f32; 2]], expected: &[[f32; 2]]) {
        assert_eq!(path.len(), expected.len(), "{path:?}");
        for (p, e) in path.iter().zip(expected) {
            assert!(dist(*p, *e) < 1e-4, "{path:?} != {expected:?}");
        }
    }

    #[test]
    fn portals_route_through_doors_and_up_stairs() {
        let nav = rooms(&[]);
        let pairs: Vec<(u32, u32)> = nav.portals().iter().map(|p| (p.a, p.b)).collect();
        assert_eq!(pairs, vec![(0, 1), (0, 3), (1, 2), (3, 4)]);
        let path = nav.route([2.0, 2.0], 0, [18.0, 8.0], 2).unwrap();
        assert_near(
            &path,
            &[
                [9.4, 5.0],
                [10.5, 5.0],
                [10.5, 5.0],
                [11.6, 5.0],
                [18.0, 8.0],
            ],
        );
        // In 3D: the doubled door point is one, heights follow the stair.
        let p = nav.path([2.0, 0.0, 2.0], [18.0, 0.0, 8.0]).unwrap();
        assert_eq!(p.len(), 5, "{p:?}");
        assert_eq!(p[0], [2.0, 0.0, 2.0]);
        assert_eq!(p[4], [18.0, 0.0, 8.0]);
        let up = nav.path([18.0, 0.0, 8.0], [18.0, 4.0, 20.0]).unwrap();
        assert!(up.iter().any(|q| q[0] < 4.0 && q[2] > 10.0 && q[1] > 0.0));
        assert!((up.last().unwrap()[1] - 4.0).abs() < 1e-4);
        assert!(Nav::length(&up) > dist([18.0, 8.0], [18.0, 20.0]));
        assert!(
            nav.path([2.0, 0.0, 2.0], [50.0, 0.0, 50.0]).is_none(),
            "off the ground"
        );
    }

    #[test]
    fn a_surface_left_out_is_not_walked() {
        // Without the door, room B is walled off.
        let nav = Nav::new(Arc::new(World::from_json(ROOMS).unwrap()), &[], |s| {
            s.id != "door"
        });
        // The balcony is 4 m above room B and never touches it.
        assert!(nav.route([2.0, 2.0], 0, [18.0, 8.0], 2).is_none());
        let reach = nav.reachable_from([0]);
        assert_eq!(reach, vec![true, false, false, true, true]);
    }

    #[test]
    fn a_leg_walks_round_a_keep_out() {
        let table = KeepOut {
            id: "table".into(),
            min: [4.0, 1.0],
            max: [6.0, 9.0],
            cell: None,
        };
        let nav = rooms(std::slice::from_ref(&table));
        let path = nav.route([2.0, 5.0], 0, [8.0, 5.0], 0).unwrap();
        assert_near(&path, &[[3.5, 0.5], [6.5, 0.5], [8.0, 5.0]]);
        let grown = table.grown(KEEP_OUT_CLEARANCE);
        let mut at = [2.0, 5.0];
        for &p in &path {
            assert!(
                !grown.crossed_by(at, p),
                "{at:?} -> {p:?} crosses the table"
            );
            at = p;
        }
        assert!(nav.blocked_at([5.0, 5.0], 0) && !nav.blocked_at([8.0, 5.0], 0));
        assert_eq!(
            nav.keep_out_at([5.0, 0.0, 5.0]).map(|k| k.id.as_str()),
            Some("table")
        );
        // A walker standing on the table walks straight off it.
        assert_eq!(
            nav.route([5.0, 5.0], 0, [8.0, 5.0], 0).unwrap(),
            vec![[8.0, 5.0]]
        );
        // A wall-to-wall keep-out closes the room.
        let wall = KeepOut {
            id: "wall".into(),
            min: [4.0, -1.0],
            max: [6.0, 11.0],
            cell: None,
        };
        assert!(rooms(&[wall]).route([2.0, 5.0], 0, [8.0, 5.0], 0).is_none());
    }

    #[test]
    fn solids_are_walked_round_on_their_storey_only() {
        let mut layout = Layout::from_json(ROOMS).unwrap();
        layout.areas.push(crate::layout::Area {
            id: "pillar".into(),
            kind: AreaKind::Solid,
            name: "Pillar".into(),
            min: [4.0, 0.0, 1.0],
            max: [6.0, 3.0, 9.0],
        });
        let nav = Nav::new(Arc::new(World::new(layout).unwrap()), &[], |_| true);
        assert!(nav.route([2.0, 5.0], 0, [8.0, 5.0], 0).unwrap().len() > 1);
        // The balcony at 4 m is above it.
        assert_eq!(
            nav.route([2.0, 20.0], 4, [8.0, 20.0], 4).unwrap(),
            vec![[8.0, 20.0]]
        );
    }

    fn sources(world: &World, house: House) -> Vec<(String, [f32; 3])> {
        let mut out: Vec<(String, [f32; 3])> = world
            .spawns()
            .iter()
            .map(|s| (format!("spawn {}", s.id), s.pos))
            .collect();
        let dorms: Vec<_> = world
            .interactables()
            .iter()
            .filter(|i| i.kind == InteractableKind::DormDoor)
            .collect();
        assert_eq!(dorms.len(), 1, "{house:?} sees its own dorm door only");
        assert_eq!(dorms[0].house.as_deref(), Some(house.id()));
        out.push((format!("dorm {}", dorms[0].id), dorms[0].pos));
        out
    }

    /// The wayfinding trail's promise: every key place offered to a house is
    /// reachable from both spawns and from that house's dorm door, on the
    /// house's own layout (other houses' gated rooms are walls), with and
    /// without the furniture keep-outs; every waypoint stays on that house's
    /// ground, and no trail ends on a find spot.
    #[test]
    fn every_key_place_is_reachable_from_the_spawns_and_every_dorm() {
        let layout = Layout::from_json(LAYOUT).unwrap();
        let places = KeyPlaces::from_json(KEY_PLACES).unwrap();
        places
            .validate_with(&layout, &Schedule::from_json(SCHEDULE).unwrap())
            .unwrap();
        let keep = spread_keep_outs();
        assert!(!keep.is_empty());
        // In every staircase state (with the keep-outs; state 0 also without).
        let states = 1u8 << layout.flights().len();
        assert_eq!(states, 8);
        for house in House::ALL {
            let runs = (0..states)
                .map(|st| (st, &keep[..]))
                .chain([(0u8, &[][..])]);
            for (state, keep_out) in runs {
                let world =
                    Arc::new(World::new(layout.for_house(house).for_stairs(state)).unwrap());
                // The trail's graph (no hidden passage).
                let nav = Nav::new(world.clone(), keep_out, trail_allows);
                let from = sources(&world, house);
                let offered: Vec<_> = places.offered_to(house).collect();
                assert_eq!(offered.len(), 33 - 3 * 3, "{house:?}");
                for p in offered {
                    let to = p
                        .resolve(world.layout())
                        .expect("the place is in the house's layout");
                    for (name, at) in &from {
                        let path = nav.path(*at, to).unwrap_or_else(|| {
                            panic!(
                                "{house:?}, state {state:03b}: no trail from {name} to {}",
                                p.id
                            )
                        });
                        assert!(!path.is_empty(), "{house:?} {name} -> {}", p.id);
                        let crossed = nav.path_surfaces(*at, to).unwrap();
                        assert!(
                            crossed
                                .iter()
                                .all(|&s| !world.surfaces()[s as usize].secret),
                            "{house:?}, state {state:03b}: the trail from {name} to {} crosses a secret",
                            p.id
                        );
                        let end = path[path.len() - 1];
                        assert!(
                            dist([end[0], end[2]], [to[0], to[2]]) < 1e-3,
                            "{house:?}: the trail to {} ends at it",
                            p.id
                        );
                        for w in path.windows(2) {
                            let mid = [
                                (w[0][0] + w[1][0]) * 0.5,
                                (w[0][1] + w[1][1]) * 0.5,
                                (w[0][2] + w[1][2]) * 0.5,
                            ];
                            for q in [w[0], mid, w[1]] {
                                assert!(
                                    world.ground_within(q[0], q[2], q[1], 0.6).is_some(),
                                    "{house:?} {name} -> {}: {q:?} is off the house's ground",
                                    p.id
                                );
                            }
                        }
                    }
                }
            }
        }
    }

    /// Every shipped keep-out names its layout cell: one without stands on
    /// every surface in plan, the storeys above and below included (the
    /// Great Hall's tables once fenced off part of the Burrowsby common
    /// room and the cellars under them).
    #[test]
    fn every_shipped_keep_out_names_its_cell() {
        let layout = Layout::from_json(LAYOUT).unwrap();
        let cells: std::collections::HashSet<&str> =
            layout.surfaces.iter().map(|s| s.cell.as_str()).collect();
        for k in spread_keep_outs() {
            let cell = k
                .cell
                .as_deref()
                .unwrap_or_else(|| panic!("{}: no cell", k.id));
            assert!(cells.contains(cell), "{}: unknown cell {cell}", k.id);
        }
    }

    /// Points a walker can stand on and use `it` from: on the ground within
    /// its reach ([`Interactable::in_reach`]), outside every keep-out and
    /// solid (grown by the walker's clearance). Its own position first, then
    /// rings out to its radius.
    fn standing_points(nav: &Nav, it: &crate::layout::Interactable) -> Vec<[f32; 3]> {
        let mut out = Vec::new();
        let rings = [0.0f32, 0.3, 0.55, 0.8, 0.95];
        for (k, f) in rings.iter().enumerate() {
            let spokes = if k == 0 { 1 } else { 16 };
            for n in 0..spokes {
                let a = n as f32 / spokes as f32 * std::f32::consts::TAU;
                let (x, z) = (
                    it.pos[0] + f * it.radius * a.cos(),
                    it.pos[2] + f * it.radius * a.sin(),
                );
                let Some(g) = nav.world().ground_within(
                    x,
                    z,
                    it.pos[1],
                    crate::layout::INTERACT_HEIGHT_TOLERANCE,
                ) else {
                    continue;
                };
                let p = [x, g.y, z];
                if it.in_reach(p) && !nav.blocked_at([x, z], g.surface.0) {
                    out.push(p);
                }
            }
        }
        out
    }

    /// The obtainability audit: every find spot and every craft station (the
    /// Potions cauldrons and each house's Knitting Corner and Transfiguration
    /// Table) can be walked to by every house that may use it, from both
    /// spawns and the house's dorm door, in every staircase state, on the
    /// trail's graph (no hidden passage) with the furniture keep-outs: some
    /// unblocked point within its reach is joined to them. The Lost
    /// Property Vault's heap is in its own instance layout, entered by the
    /// Wishing Room door, which is checked here too.
    #[test]
    fn every_find_spot_and_craft_station_is_reachable() {
        let layout = Layout::from_json(LAYOUT).unwrap();
        let keep = spread_keep_outs();
        let checked = |k: InteractableKind| {
            matches!(
                k,
                InteractableKind::FindSpot
                    | InteractableKind::CraftCharms
                    | InteractableKind::CraftTransfiguration
                    | InteractableKind::Cauldron
                    | InteractableKind::WishingRoomDoor
            )
        };
        let all: Vec<&str> = layout
            .interactables
            .iter()
            .filter(|i| checked(i.kind))
            .map(|i| i.id.as_str())
            .collect();
        assert_eq!(
            all.len(),
            42 + 12 + 1,
            "42 find spots, 12 stations, the Wishing Room door"
        );
        let mut reached: std::collections::HashSet<String> = Default::default();
        let mut unreachable: Vec<String> = Vec::new();
        for house in House::ALL {
            for state in 0..1u8 << layout.flights().len() {
                let world =
                    Arc::new(World::new(layout.for_house(house).for_stairs(state)).unwrap());
                let nav = Nav::new(world.clone(), &keep, trail_allows);
                let from = sources(&world, house);
                for it in world.interactables().iter().filter(|i| checked(i.kind)) {
                    let stand = standing_points(&nav, it);
                    let missed: Vec<&str> = from
                        .iter()
                        .filter(|(_, at)| !stand.iter().any(|&p| nav.path(*at, p).is_some()))
                        .map(|(name, _)| name.as_str())
                        .collect();
                    if missed.is_empty() {
                        reached.insert(it.id.clone());
                    } else {
                        unreachable.push(format!(
                            "{house:?}, state {state:03b}: {} ({} standing points) from {missed:?}",
                            it.id,
                            stand.len()
                        ));
                    }
                }
            }
        }
        assert!(unreachable.is_empty(), "unreachable: {unreachable:#?}");
        // Each was checked for some house (a house's own only for it).
        let never: Vec<&&str> = all.iter().filter(|id| !reached.contains(**id)).collect();
        assert!(never.is_empty(), "checked for no house: {never:?}");
        // Inside the Lost Property Vault: its heap from where one enters.
        let room = Arc::new(
            World::from_json(include_str!(
                "../../../data/world/instances/lost_property_vault.json"
            ))
            .unwrap(),
        );
        let nav = Nav::new(room.clone(), &[], trail_allows);
        let heap = room
            .interactables()
            .iter()
            .find(|i| i.id == "find_lost_property_vault")
            .unwrap();
        let stand = standing_points(&nav, heap);
        for s in room.spawns() {
            assert!(
                stand.iter().any(|&p| nav.path(s.pos, p).is_some()),
                "the heap from {}",
                s.id
            );
        }
    }

    /// Other houses' common rooms and dorm doors are never on a house's
    /// trail: their gated ground is not in its layout, so no trail ends there.
    #[test]
    fn house_gates_keep_trails_out_of_other_houses_rooms() {
        let layout = Layout::from_json(LAYOUT).unwrap();
        let places = KeyPlaces::from_json(KEY_PLACES).unwrap();
        for house in House::ALL {
            let world = Arc::new(World::new(layout.for_house(house)).unwrap());
            let nav = Nav::new(world.clone(), &[], |_| true);
            let spawn = world.spawns()[0].pos;
            for p in places
                .places
                .iter()
                .filter(|p| p.house().is_some_and(|h| h != house))
            {
                let to = p.resolve(&layout).unwrap();
                assert!(nav.path(spawn, to).is_none(), "{house:?} reaches {}", p.id);
            }
        }
    }

    /// The trail to every grounds key place, from both spawns and
    /// every dorm, never crosses water: every point along it (every 0.5 m)
    /// stands on the house's ground, at least the wading depth above the lake
    /// bed's walk limit; on the grounds it follows the ground (no waypoint
    /// further than `HEIGHTFIELD_PATH_STEP` from the next while on a hill,
    /// each at the ground's height), and it crosses the grounds' seams near
    /// the straight line (a portal every `GROUNDS_PORTAL_SPACING`).
    #[test]
    fn grounds_trails_stay_on_land_and_on_the_ground() {
        let grounds: serde_json::Value =
            serde_json::from_str(include_str!("../../../data/world/terrain/grounds.json")).unwrap();
        let walk_limit =
            grounds["water_y"].as_f64().unwrap() as f32 - grounds["wade"].as_f64().unwrap() as f32;
        let layout = Layout::from_json(LAYOUT).unwrap();
        let places = KeyPlaces::from_json(KEY_PLACES).unwrap();
        let ids: Vec<&str> = places
            .places
            .iter()
            .filter(|p| p.group == crate::key_places::PlaceGroup::Grounds)
            .map(|p| p.id.as_str())
            .collect();
        assert_eq!(
            ids,
            [
                "groundskeepers_cottage",
                "lake_shore",
                "boathouse",
                "flying_pitch",
                "owl_loft"
            ]
        );
        for house in House::ALL {
            let world = Arc::new(World::new(layout.for_house(house)).unwrap());
            let nav = Nav::new(world.clone(), &spread_keep_outs(), |_| true);
            for id in &ids {
                let to = places.get(id).unwrap().resolve(&layout).unwrap();
                for (name, at) in sources(&world, house) {
                    let path = nav
                        .path(at, to)
                        .unwrap_or_else(|| panic!("{house:?}: no trail from {name} to {id}"));
                    for w in path.windows(2) {
                        let (a, b) = (w[0], w[1]);
                        let len = dist([a[0], a[2]], [b[0], b[2]]);
                        let n = (len / 0.5).ceil().max(1.0) as usize;
                        for k in 0..=n {
                            let t = k as f32 / n as f32;
                            let q = [
                                a[0] + (b[0] - a[0]) * t,
                                a[1] + (b[1] - a[1]) * t,
                                a[2] + (b[2] - a[2]) * t,
                            ];
                            let g =
                                world
                                    .ground_within(q[0], q[2], q[1], 0.6)
                                    .unwrap_or_else(|| {
                                        panic!("{house:?} {name} -> {id}: {q:?} is off the ground")
                                    });
                            let s = world.surface(g.surface);
                            if s.kind == SurfaceKind::Grounds {
                                assert!(
                                    g.y >= walk_limit - 1e-3,
                                    "{name} -> {id}: {q:?} is in the lake"
                                );
                                // Linear between waypoints at most 2 m apart: within 10 cm of the hill.
                                assert!(
                                    (g.y - q[1]).abs() < 0.1,
                                    "{name} -> {id}: {q:?} leaves the ground ({})",
                                    g.y
                                );
                            }
                        }
                        if world
                            .ground_at(a[0], a[2], a[1])
                            .is_some_and(|g| world.surface(g.surface).heights.is_some())
                            && world
                                .ground_at(b[0], b[2], b[1])
                                .is_some_and(|g| world.surface(g.surface).heights.is_some())
                        {
                            assert!(
                                len <= HEIGHTFIELD_PATH_STEP + 1.3,
                                "{name} -> {id}: a {len} m leg on a hill"
                            );
                        }
                    }
                }
            }
        }
        // The seams between grounds tiles carry several portals.
        let world = Arc::new(World::new(layout.clone()).unwrap());
        let nav = Nav::new(world.clone(), &[], |_| true);
        let surfaces = world.surfaces();
        let id = |name: &str| surfaces.iter().position(|s| s.id == name).unwrap() as u32;
        let (a, b) = (id("grounds_r1_c2"), id("grounds_r1_c3"));
        let seam = nav
            .portals()
            .iter()
            .filter(|p| (p.a, p.b) == (a.min(b), a.max(b)))
            .count();
        assert!(seam >= 4, "{seam} portals on a 40 m seam");
        // From the gate to the hut the trail is less than 15 % longer than the crow flies.
        let gate = [16.0, 0.0, 95.0];
        let hut = places
            .get("groundskeepers_cottage")
            .unwrap()
            .resolve(&layout)
            .unwrap();
        let path = nav.path(gate, hut).unwrap();
        let crow = dist([gate[0], gate[2]], [hut[0], hut[2]]);
        let plan: f32 = path
            .windows(2)
            .map(|w| dist([w[0][0], w[0][2]], [w[1][0], w[1][2]]))
            .sum();
        assert!(
            plan < crow * 1.15,
            "{plan} m against {crow} m as the crow flies"
        );
    }

    /// Every surface of every staircase state is reachable from the spawns
    /// (nobody is ever stranded: a B landing always has a fixed way out).
    #[test]
    fn the_whole_castle_is_one_graph() {
        let layout = Layout::from_json(LAYOUT).unwrap();
        for state in 0..1u8 << layout.flights().len() {
            let world = Arc::new(World::new(layout.for_stairs(state)).unwrap());
            let nav = Nav::new(world.clone(), &spread_keep_outs(), |_| true);
            let seeds = world
                .spawns()
                .iter()
                .filter_map(|s| nav.surface_at(s.pos))
                .collect::<Vec<_>>();
            let reach = nav.reachable_from(seeds);
            let unreachable: Vec<&str> = world
                .surfaces()
                .iter()
                .zip(&reach)
                .filter(|(_, r)| !**r)
                .map(|(s, _)| s.id.as_str())
                .collect();
            assert!(
                unreachable.is_empty(),
                "state {state:03b}: unreachable: {unreachable:?}"
            );
        }
    }

    /// Travel distances for the layout rules, as `tools/blender/scale_pass/routes.mjs`
    /// measures them: the shortest horizontal walk
    /// over the walkable rectangles, through contact points sampled every 2 m
    /// (at most 17) wherever two surfaces meet within a step. A player runs
    /// these straight lines; the trail's one portal per edge is a little longer.
    struct Travel {
        surfaces: Vec<Surface>,
        nodes: Vec<([f32; 2], [usize; 2])>,
        by_surface: Vec<Vec<usize>>,
    }

    impl Travel {
        fn new(layout: &Layout) -> Travel {
            let surfaces = layout.surfaces.clone();
            let step = MoveParams::default().step_height;
            let mut nodes = Vec::new();
            let mut by_surface = vec![Vec::new(); surfaces.len()];
            for i in 0..surfaces.len() {
                for j in i + 1..surfaces.len() {
                    let (a, b) = (&surfaces[i], &surfaces[j]);
                    let (x0, x1) = (a.min[0].max(b.min[0]), a.max[0].min(b.max[0]));
                    let (z0, z1) = (a.min[1].max(b.min[1]), a.max[1].min(b.max[1]));
                    if x1 < x0 - 1e-3 || z1 < z0 - 1e-3 || (x1 - x0 < 1e-3 && z1 - z0 < 1e-3) {
                        continue;
                    }
                    let n = ((x1 - x0).max(z1 - z0) / 2.0).ceil().clamp(1.0, 16.0) as usize;
                    for k in 0..=n {
                        let f = k as f32 / n as f32;
                        let (x, z) = (x0 + (x1 - x0) * f, z0 + (z1 - z0) * f);
                        if (a.height_at(x, z) - b.height_at(x, z)).abs() > step + 1e-4 {
                            continue;
                        }
                        by_surface[i].push(nodes.len());
                        by_surface[j].push(nodes.len());
                        nodes.push(([x, z], [i, j]));
                    }
                }
            }
            Travel {
                surfaces,
                nodes,
                by_surface,
            }
        }

        fn surface_at(&self, p: [f32; 3]) -> Option<usize> {
            let mut best: Option<(usize, f32)> = None;
            for (i, s) in self.surfaces.iter().enumerate() {
                if !s.contains(p[0], p[2]) {
                    continue;
                }
                let d = (s.height_at(p[0], p[2]) - p[1]).abs();
                if d <= 1.5 && best.is_none_or(|(_, b)| d < b) {
                    best = Some((i, d));
                }
            }
            best.map(|(i, _)| i)
        }

        /// The walk from `from` to each of `targets` (metres), `None` where no way.
        fn lengths(&self, from: [f32; 3], targets: &[[f32; 3]]) -> Vec<Option<f32>> {
            let Some(sf) = self.surface_at(from) else {
                return vec![None; targets.len()];
            };
            let f2 = [from[0], from[2]];
            let mut best = vec![f32::INFINITY; self.nodes.len()];
            let mut heap = BinaryHeap::new();
            for &n in &self.by_surface[sf] {
                best[n] = dist(f2, self.nodes[n].0);
                heap.push(Node {
                    cost: best[n],
                    crossing: n as u32,
                });
            }
            while let Some(Node { cost, crossing }) = heap.pop() {
                let u = crossing as usize;
                if cost > best[u] {
                    continue;
                }
                for &s in &self.nodes[u].1 {
                    for &v in &self.by_surface[s] {
                        let c = cost + dist(self.nodes[u].0, self.nodes[v].0);
                        if c < best[v] {
                            best[v] = c;
                            heap.push(Node {
                                cost: c,
                                crossing: v as u32,
                            });
                        }
                    }
                }
            }
            targets
                .iter()
                .map(|&t| {
                    let st = self.surface_at(t)?;
                    let t2 = [t[0], t[2]];
                    if st == sf {
                        return Some(dist(f2, t2));
                    }
                    let m = self.by_surface[st]
                        .iter()
                        .map(|&n| best[n] + dist(self.nodes[n].0, t2))
                        .fold(f32::INFINITY, f32::min);
                    m.is_finite().then_some(m)
                })
                .collect()
        }
    }

    /// The layout without its hidden passages: the ways everyone knows.
    fn public(layout: &Layout) -> Layout {
        Layout {
            surfaces: layout
                .surfaces
                .iter()
                .filter(|s| !s.secret)
                .cloned()
                .collect(),
            ..layout.clone()
        }
    }

    /// In every staircase state and for every house, the trail's
    /// graph (no hidden passage) still reaches every public surface of the
    /// house from both spawns (a passage is never the only way anywhere), its
    /// portals never touch a secret, and the five passages are each a
    /// shortcut: with them the walk between their two ends is shorter than
    /// without (`Travel`, the same measure as the trip rules).
    #[test]
    fn the_trail_never_uses_a_secret_and_passages_are_shortcuts() {
        let layout = Layout::from_json(LAYOUT).unwrap();
        let secrets: Vec<&Surface> = layout.surfaces.iter().filter(|s| s.secret).collect();
        assert!(secrets.len() >= 5, "{} secret surfaces", secrets.len());
        for house in House::ALL {
            for state in 0..1u8 << layout.flights().len() {
                let world =
                    Arc::new(World::new(layout.for_house(house).for_stairs(state)).unwrap());
                let nav = Nav::new(world.clone(), &spread_keep_outs(), trail_allows);
                for p in nav.portals() {
                    for s in [p.a, p.b] {
                        assert!(!world.surfaces()[s as usize].secret);
                    }
                }
                let seeds = world
                    .spawns()
                    .iter()
                    .filter_map(|s| nav.surface_at(s.pos))
                    .collect::<Vec<_>>();
                let reach = nav.reachable_from(seeds);
                let cut_off: Vec<&str> = world
                    .surfaces()
                    .iter()
                    .zip(&reach)
                    .filter(|(s, r)| !s.secret && !**r)
                    .map(|(s, _)| s.id.as_str())
                    .collect();
                assert!(
                    cut_off.is_empty(),
                    "{house:?}, state {state:03b}: reachable only through a passage: {cut_off:?}"
                );
            }
        }
        // Each passage's two doors: the walk between the rooms they open
        // into, with and without the passages.
        let all = Travel::new(&layout.for_stairs(0));
        let known = Travel::new(&public(&layout.for_stairs(0)));
        let mut cells: Vec<&str> = secrets.iter().map(|s| s.cell.as_str()).collect();
        cells.dedup();
        assert_eq!(cells.len(), 5, "{cells:?}");
        for cell in cells {
            let doors: Vec<&&Surface> = secrets
                .iter()
                .filter(|s| s.cell == cell && s.kind == SurfaceKind::Door)
                .collect();
            assert_eq!(doors.len(), 2, "{cell}: two ends");
            // A point 1 m into the public room beyond each door.
            let beyond = |d: &Surface| -> [f32; 3] {
                let c = [(d.min[0] + d.max[0]) * 0.5, (d.min[1] + d.max[1]) * 0.5];
                let h = [
                    (d.max[0] - d.min[0]) * 0.5 + 1.0,
                    (d.max[1] - d.min[1]) * 0.5 + 1.0,
                ];
                [
                    [c[0] - h[0], c[1]],
                    [c[0] + h[0], c[1]],
                    [c[0], c[1] - h[1]],
                    [c[0], c[1] + h[1]],
                ]
                .into_iter()
                .find_map(|[x, z]| {
                    layout
                        .surfaces
                        .iter()
                        .find(|s| {
                            !s.secret && s.contains(x, z) && (s.height_at(x, z) - d.y).abs() < 0.2
                        })
                        .map(|s| [x, s.height_at(x, z), z])
                })
                .unwrap_or_else(|| panic!("{}: no public room beyond", d.id))
            };
            let (a, b) = (beyond(doors[0]), beyond(doors[1]));
            let with = all.lengths(a, &[b])[0].unwrap();
            let without = known.lengths(a, &[b])[0].unwrap();
            println!("{cell}: {with:.0} m through it, {without:.0} m without");
            assert!(with < without * 0.8, "{cell}: {with} m against {without} m");
        }
    }

    /// The moving staircases' layout rules, in every one
    /// of the 8 staircase states: no class door is more than 60 s away at a
    /// run from either spawn, and at least one common trip (between the
    /// spawns, the dorms, the common rooms, the halls and the class doors)
    /// is at least 30 % longer in some state than in its best one. From the
    /// dorms, common rooms and class doors no class door is more than 90 s
    /// away (the worst, Roarwing's dorm to the paddock, is 84 s).
    /// `cargo test -p sim-core staircase_trip_rules -- --nocapture` prints
    /// the table from the first spawn (docs/architecture.md).
    #[test]
    fn staircase_trip_rules() {
        use crate::key_places::PlaceGroup as G;
        let layout = Layout::from_json(LAYOUT).unwrap();
        let places = KeyPlaces::from_json(KEY_PLACES).unwrap();
        let run = MoveParams::default().run_speed;
        let states = 1u8 << layout.flights().len();
        assert_eq!(states, 8);
        let mut worst_growth = (0.0f32, String::new());
        let mut slowest_door = (0.0f32, String::new());
        let mut slowest_any = (0.0f32, String::new());
        for house in House::ALL {
            let graphs: Vec<Travel> = (0..states)
                .map(|st| Travel::new(&public(&layout.for_house(house).for_stairs(st))))
                .collect();
            let offered: Vec<_> = places.offered_to(house).collect();
            let targets: Vec<[f32; 3]> = offered
                .iter()
                .map(|p| p.resolve(&layout).unwrap())
                .collect();
            let common = |g: G| {
                matches!(
                    g,
                    G::Dorm | G::CommonRoom | G::Class | G::GreatHall | G::EntranceHall
                )
            };
            let mut sources: Vec<(String, [f32; 3], bool)> = layout
                .spawns
                .iter()
                .map(|s| (s.id.clone(), s.pos, true))
                .collect();
            sources.extend(
                offered
                    .iter()
                    .zip(&targets)
                    .filter(|(p, _)| common(p.group))
                    .map(|(p, t)| (p.id.clone(), *t, false)),
            );
            if house == House::Roarwing {
                println!(
                    "| From the first spawn to | {} |",
                    (0..states)
                        .map(|s| format!("{s:03b}"))
                        .collect::<Vec<_>>()
                        .join(" | ")
                );
            }
            for (name, from, spawn) in &sources {
                let by_state: Vec<Vec<Option<f32>>> =
                    graphs.iter().map(|g| g.lengths(*from, &targets)).collect();
                for (k, p) in offered.iter().enumerate() {
                    let lens: Vec<f32> = by_state
                        .iter()
                        .map(|l| {
                            l[k].unwrap_or_else(|| {
                                panic!("{house:?}: no way from {name} to {}", p.id)
                            })
                        })
                        .collect();
                    let (lo, hi) = lens
                        .iter()
                        .fold((f32::INFINITY, 0.0f32), |(a, b), &v| (a.min(v), b.max(v)));
                    if house == House::Roarwing && *name == layout.spawns[0].id {
                        println!(
                            "| {} | {} |",
                            p.name,
                            lens.iter()
                                .map(|m| format!("{m:.0} m, {:.1} s", m / run))
                                .collect::<Vec<_>>()
                                .join(" | ")
                        );
                    }
                    if common(p.group) && lo > 10.0 && hi / lo - 1.0 > worst_growth.0 {
                        worst_growth = (
                            hi / lo - 1.0,
                            format!("{house:?}: {name} to {} ({lo:.0} to {hi:.0} m)", p.id),
                        );
                    }
                    if p.group == G::Class && hi / run > slowest_any.0 {
                        slowest_any = (
                            hi / run,
                            format!("{house:?}: {name} to {} ({hi:.0} m)", p.id),
                        );
                    }
                    if *spawn && p.group == G::Class && hi / run > slowest_door.0 {
                        slowest_door = (
                            hi / run,
                            format!("{house:?}: {name} to {} ({hi:.0} m)", p.id),
                        );
                    }
                }
            }
        }
        println!(
            "most growth {worst_growth:?}; slowest class door {slowest_door:?}; from any common place {slowest_any:?}"
        );
        assert!(
            slowest_door.0 <= 60.0,
            "a class door is {slowest_door:?} s away at a run"
        );
        // From a dorm or common room (where students set out for class) the
        // far grounds classes were past 60 s before the staircases moved
        // (Roarwing's dorm to the paddock is 72 s in its best state), so
        // the bound there is half the 180 s a class door is open.
        assert!(
            slowest_any.0 <= 90.0,
            "a class door is {slowest_any:?} s away at a run from a common place"
        );
        assert!(
            worst_growth.0 >= 0.3,
            "no common trip grows by 30 %: {worst_growth:?}"
        );
    }
}
