//! Who each client sees: the nearest N characters, ranked by a score that
//! prefers the viewer's own floor, always including priority players, with
//! hysteresis so the set does not flicker as people walk past each other.
//! Group members (the viewer's party, `party.rs`) rank ahead of every other
//! priority player, so a crowd of online friends never pushes a party
//! member out of the view limit.
//!
//! The score is the 3D distance in metres, plus [`LEVEL_PENALTY_M`] for
//! characters on another floor level: someone one floor up and 5 m away ranks
//! like someone on the viewer's floor 20 m away. Current members of the set
//! compete with a *sticky* score, [`sticky`], reduced by the larger of
//! [`STICKY_MARGIN_M`] and [`STICKY_MARGIN_FRAC`] of their score, so a
//! newcomer replaces a member only when it is clearly closer.
//!
//! In the castle, candidates come from the spatial grid, ring by ring outward
//! from the viewer's cell, stopping as soon as N candidates score below the
//! distance to the next ring: nothing further out could rank higher. In a
//! sparse castle that means a handful of rings; in a crowd, the viewer's own
//! cell. In a dorm instance (a dozen people at most) every occupant is a
//! candidate. Only characters in the viewer's zone are ever visible, priority
//! players included.
//!
//! The selection also splits the set into tiers for the snapshot rate
//! (docs/architecture.md, "Snapshots"): the [`NEAR_TIER`] best-ranked members
//! are *near* and get every change at 10 Hz; the rest may be updated at 5 Hz.

use std::cmp::Ordering;

use protocol::EntityId;

use super::entities::EntView;
use super::grid::Grid;

/// Members of a visible set ranked this high or better are in the near tier.
/// Ranking (not a distance) keeps the split meaningful in a crowd, where a
/// hundred people stand within a few metres: there the twelve nearest are
/// within about 2.5 m. Measured in the packed 1,000-bot hall, 12 instead of
/// 25 saves about 0.2 KB/s per client at either view limit (measure with
/// tools/loadtest).
pub const NEAR_TIER: usize = 12;

/// Extra metres added to characters on another floor level.
pub const LEVEL_PENALTY_M: f32 = 15.0;
/// A newcomer must be this much closer than a current member to replace it...
pub const STICKY_MARGIN_M: f32 = 2.0;
/// ...or this fraction of the member's score, whichever is larger.
pub const STICKY_MARGIN_FRAC: f32 = 0.15;
/// Priority players rank ahead of everyone...
const PRIORITY_SCORE: f32 = -1.0;
/// ...and group members ahead of them.
const GROUP_SCORE: f32 = -2.0;

#[derive(Debug, Clone, Copy)]
pub struct Candidate {
    pub score: f32,
    /// Dense index.
    pub index: u32,
}

/// Ranking score of a character at `pos` on `level` for a viewer.
#[inline]
pub fn score(viewer_pos: [f32; 3], viewer_level: i32, pos: [f32; 3], level: i32) -> f32 {
    let (dx, dy, dz) = (
        pos[0] - viewer_pos[0],
        pos[1] - viewer_pos[1],
        pos[2] - viewer_pos[2],
    );
    let d = (dx * dx + dy * dy + dz * dz).sqrt();
    if level == viewer_level {
        d
    } else {
        d + LEVEL_PENALTY_M
    }
}

/// The score a current member competes with.
#[inline]
pub fn sticky(score: f32) -> f32 {
    (score - STICKY_MARGIN_M.max(score * STICKY_MARGIN_FRAC)).max(0.0)
}

/// One viewer's query.
pub struct Query<'a> {
    /// Dense index of the viewer.
    pub viewer: usize,
    /// The viewer's current visible set, sorted by id.
    pub current: &'a [EntityId],
    /// Priority players (any order, no duplicates). Those in other zones are
    /// skipped.
    pub priority: &'a [EntityId],
    /// Group members (the viewer's party): priority players that rank ahead
    /// of the others. Those in other zones are skipped.
    pub group: &'a [EntityId],
    pub limit: usize,
}

/// Where the candidates come from.
#[derive(Clone, Copy)]
pub enum Source<'a> {
    /// The castle's spatial grid (which holds only castle characters).
    Grid(&'a Grid),
    /// Every occupant of an instance (ids; the viewer among them is skipped).
    Members(&'a [EntityId]),
}

/// One member of a selected visible set.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Member {
    pub id: EntityId,
    /// In the near tier.
    pub near: bool,
}

/// Reusable buffers for [`select_visible`], one per worker thread.
#[derive(Debug, Default)]
pub struct VisScratch {
    cands: Vec<Candidate>,
    /// `marks[i] == stamp`: dense index `i` is already a candidate (a
    /// priority player or a current member), so the ring search skips it.
    /// Bumping `stamp` clears every mark at once.
    marks: Vec<u32>,
    stamp: u32,
}

impl VisScratch {
    /// Starts a query over `n` entities with no marks set.
    fn begin(&mut self, n: usize) {
        self.cands.clear();
        if self.marks.len() < n {
            self.marks.resize(n, 0);
        }
        self.stamp = self.stamp.wrapping_add(1);
        if self.stamp == 0 {
            self.marks.fill(0);
            self.stamp = 1;
        }
    }
}

/// Computes the viewer's new visible set into `out`, sorted by id, each
/// member marked near or far. `scratch` keeps its buffers between calls.
pub fn select_visible(
    view: EntView<'_>,
    source: Source<'_>,
    q: &Query<'_>,
    scratch: &mut VisScratch,
    out: &mut Vec<Member>,
) {
    out.clear();
    scratch.begin(view.len());
    if q.limit == 0 || view.len() < 2 {
        return;
    }
    let VisScratch {
        cands,
        marks,
        stamp,
    } = scratch;
    let stamp = *stamp;
    let vp = view.pos[q.viewer];
    let vl = view.level[q.viewer];
    let vz = view.zone[q.viewer];
    // NPCs: background students rank `student_view_penalty_m` behind (the
    // penalty only adds, so the ring bound below still holds).
    let student_penalty = crate::catalog::shipped()
        .npcs
        .roster
        .rules
        .student_view_penalty_m;
    let score_of = |i: usize| {
        let s = score(vp, vl, view.pos[i], view.level[i]);
        match view.info[i].npc.as_deref() {
            Some(npc) if crate::residents::is_student(npc) => s + student_penalty,
            _ => s,
        }
    };
    // A live entity other than the viewer, in the viewer's zone.
    let candidate = |id: EntityId| {
        view.index_of(id)
            .filter(|&i| i != q.viewer && view.zone[i] == vz)
    };

    for (list, score) in [(q.group, GROUP_SCORE), (q.priority, PRIORITY_SCORE)] {
        for &id in list {
            if let Some(i) = candidate(id)
                && marks[i] != stamp
            {
                marks[i] = stamp;
                cands.push(Candidate {
                    score,
                    index: i as u32,
                });
            }
        }
    }
    for &id in q.current {
        // Priority players are already in, with their better score. Members
        // who left the zone drop out.
        if let Some(i) = candidate(id)
            && marks[i] != stamp
        {
            marks[i] = stamp;
            cands.push(Candidate {
                score: sticky(score_of(i)),
                index: i as u32,
            });
        }
    }

    match source {
        Source::Grid(grid) => {
            let (cx, cz) = grid.cell_coords(vp[0], vp[2]);
            // Set when the search stopped because enough candidates lie within
            // this distance: nobody scoring above it can be among the nearest.
            let mut bound = None;
            for r in 0..=grid.max_ring() {
                let inside = grid.for_each_in_ring(cx, cz, r, |i| {
                    let i = i as usize;
                    if i == q.viewer || marks[i] == stamp {
                        return;
                    }
                    cands.push(Candidate {
                        score: score_of(i),
                        index: i as u32,
                    });
                });
                if !inside {
                    break;
                }
                // Everything outside the rings searched so far is at least this
                // far away horizontally, and the score is never less than the
                // horizontal distance.
                let clearance = grid.clearance(vp[0], vp[2], r);
                if cands.iter().filter(|c| c.score <= clearance).count() >= q.limit {
                    bound = Some(clearance);
                    break;
                }
            }
            // At least `limit` candidates score within the bound, so the ones
            // beyond it can never make the cut; dropping them first makes the
            // selection below much cheaper in a crowd.
            if let Some(b) = bound {
                cands.retain(|c| c.score <= b);
            }
        }
        Source::Members(members) => {
            for &id in members {
                if let Some(i) = candidate(id)
                    && marks[i] != stamp
                {
                    marks[i] = stamp;
                    cands.push(Candidate {
                        score: score_of(i),
                        index: i as u32,
                    });
                }
            }
        }
    }

    // Ties break by id, so equal scores never reorder between snapshots.
    let ids = view.ids;
    let order = |a: &Candidate, b: &Candidate| {
        a.score
            .partial_cmp(&b.score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| ids[a.index as usize].cmp(&ids[b.index as usize]))
    };
    cap_students(view, cands, order);
    if cands.len() > q.limit {
        cands.select_nth_unstable_by(q.limit - 1, order);
        cands.truncate(q.limit);
    }
    // The best NEAR_TIER of the selection go first.
    if cands.len() > NEAR_TIER {
        cands.select_nth_unstable_by(NEAR_TIER - 1, order);
    }
    out.extend(cands.iter().enumerate().map(|(rank, c)| Member {
        id: ids[c.index as usize],
        near: rank < NEAR_TIER,
    }));
    out.sort_unstable_by_key(|m| m.id);
}

/// NPCs (docs/architecture.md "NPCs"): at most
/// `rules.students_in_view_max` background students are in one view (the
/// best-ranked; their scores already carry `student_view_penalty_m`). Runs
/// over the candidates left after the ring search's bound.
fn cap_students(
    view: EntView<'_>,
    cands: &mut Vec<Candidate>,
    order: impl Fn(&Candidate, &Candidate) -> Ordering,
) {
    let mut students: Vec<Candidate> = cands
        .iter()
        .filter(|c| {
            view.info[c.index as usize]
                .npc
                .as_deref()
                .is_some_and(crate::residents::is_student)
        })
        .copied()
        .collect();
    let cap = usize::from(
        crate::catalog::shipped()
            .npcs
            .roster
            .rules
            .students_in_view_max,
    );
    if students.len() <= cap {
        return;
    }
    students.sort_unstable_by(&order);
    let dropped: Vec<u32> = students[cap..].iter().map(|c| c.index).collect();
    cands.retain(|c| !dropped.contains(&c.index));
}

#[cfg(test)]
mod tests {
    use std::time::Instant;

    use protocol::House;

    use super::*;
    use crate::realtime::entities::tests::test_entity;
    use crate::realtime::entities::{CASTLE, Entities};

    /// A world of entities at the given (x, z, level) spots; returns their ids.
    fn world(spots: &[(f32, f32, i32)]) -> (Entities, Grid, Vec<EntityId>) {
        let mut e = Entities::default();
        let now = Instant::now();
        let ids = spots
            .iter()
            .enumerate()
            .map(|(n, &(x, z, level))| {
                let y = level as f32 * 6.0;
                e.insert(
                    test_entity(&format!("p{n}"), [x, y, z], level, House::Skytalon),
                    now,
                )
                .unwrap()
            })
            .collect();
        let mut g = Grid::new([-200.0, -200.0], [200.0, 200.0]);
        g.rebuild(&e.pos);
        (e, g, ids)
    }

    fn select(
        e: &Entities,
        g: &Grid,
        viewer: EntityId,
        current: &[EntityId],
        limit: usize,
    ) -> Vec<EntityId> {
        select_with(e, g, viewer, current, &[], limit)
    }

    fn select_with(
        e: &Entities,
        g: &Grid,
        viewer: EntityId,
        current: &[EntityId],
        priority: &[EntityId],
        limit: usize,
    ) -> Vec<EntityId> {
        let mut cur = current.to_vec();
        cur.sort_unstable();
        let q = Query {
            viewer: e.index_of(viewer).unwrap(),
            current: &cur,
            priority,
            group: &[],
            limit,
        };
        let (mut s, mut out) = (VisScratch::default(), Vec::new());
        select_visible(e.view(), Source::Grid(g), &q, &mut s, &mut out);
        out.iter().map(|m| m.id).collect()
    }

    /// The straightforward version of [`select_visible`] (as first written):
    /// binary searches instead of marks, the plain ring bound, and selection
    /// over every candidate found. The optimised one must agree exactly.
    fn reference(view: EntView<'_>, grid: &Grid, q: &Query<'_>) -> Vec<EntityId> {
        let mut cands = Vec::new();
        if q.limit == 0 || view.len() < 2 {
            return Vec::new();
        }
        let vp = view.pos[q.viewer];
        let vl = view.level[q.viewer];
        let score_of = |i: usize| score(vp, vl, view.pos[i], view.level[i]);
        for &id in q.group {
            if let Some(i) = view.index_of(id).filter(|&i| i != q.viewer) {
                cands.push(Candidate {
                    score: GROUP_SCORE,
                    index: i as u32,
                });
            }
        }
        for &id in q.priority {
            if q.group.contains(&id) {
                continue;
            }
            if let Some(i) = view.index_of(id).filter(|&i| i != q.viewer) {
                cands.push(Candidate {
                    score: PRIORITY_SCORE,
                    index: i as u32,
                });
            }
        }
        for &id in q.current {
            if q.priority.contains(&id) || q.group.contains(&id) {
                continue;
            }
            if let Some(i) = view.index_of(id).filter(|&i| i != q.viewer) {
                cands.push(Candidate {
                    score: sticky(score_of(i)),
                    index: i as u32,
                });
            }
        }
        let (cx, cz) = grid.cell_coords(vp[0], vp[2]);
        for r in 0..=grid.max_ring() {
            let inside = grid.for_each_in_ring(cx, cz, r, |i| {
                let i = i as usize;
                let id = view.ids[i];
                if i == q.viewer
                    || q.current.binary_search(&id).is_ok()
                    || q.priority.contains(&id)
                    || q.group.contains(&id)
                {
                    return;
                }
                cands.push(Candidate {
                    score: score_of(i),
                    index: i as u32,
                });
            });
            let clearance = Grid::ring_clearance(r);
            if !inside || cands.iter().filter(|c| c.score <= clearance).count() >= q.limit {
                break;
            }
        }
        let ids = view.ids;
        cands.sort_by(|a, b| {
            a.score
                .partial_cmp(&b.score)
                .unwrap()
                .then_with(|| ids[a.index as usize].cmp(&ids[b.index as usize]))
        });
        let mut out: Vec<EntityId> = cands
            .iter()
            .take(q.limit)
            .map(|c| ids[c.index as usize])
            .collect();
        out.sort_unstable();
        out
    }

    #[test]
    fn matches_the_reference_over_moving_crowds() {
        let mut seed = 0x9e37_79b9_u32;
        let mut next = move || {
            seed ^= seed << 13;
            seed ^= seed >> 17;
            seed ^= seed << 5;
            (seed % 100_000) as f32 / 100_000.0
        };
        // A dense hall, a sparse field, and a few strays outside the grid.
        let mut spots: Vec<(f32, f32, i32)> = Vec::new();
        for _ in 0..600 {
            spots.push((next() * 40.0, next() * 30.0, (next() * 1.3) as i32));
        }
        for _ in 0..300 {
            spots.push((
                next() * 380.0 - 190.0,
                next() * 380.0 - 190.0,
                (next() * 3.0) as i32,
            ));
        }
        spots.extend([(-400.0, 10.0, 0), (10.0, 900.0, 0), (-250.0, -250.0, 2)]);
        let (mut e, mut g, ids) = world(&spots);
        let viewers = [0usize, 7, 333, 599, 650, 899, 900, 902];
        let mut current: Vec<Vec<EntityId>> = vec![Vec::new(); viewers.len()];
        let mut scratch = VisScratch::default();
        let mut out = Vec::new();
        for frame in 0..30 {
            // Everyone shuffles a little, as between two snapshots.
            for p in e.pos.iter_mut() {
                p[0] += next() * 3.0 - 1.5;
                p[2] += next() * 3.0 - 1.5;
            }
            g.rebuild(&e.pos);
            for (k, &v) in viewers.iter().enumerate() {
                let priority: Vec<EntityId> = if k % 3 == 0 {
                    vec![ids[(v + 450) % ids.len()], ids[(v + 1) % ids.len()]]
                } else {
                    Vec::new()
                };
                // Some viewers have a party too (one of them also a priority player).
                let group: Vec<EntityId> = if k % 2 == 0 {
                    vec![ids[(v + 1) % ids.len()], ids[(v + 700) % ids.len()]]
                } else {
                    Vec::new()
                };
                let limit = [20usize, 50, 100, 1][(k + frame) % 4];
                let q = Query {
                    viewer: v,
                    current: &current[k],
                    priority: &priority,
                    group: &group,
                    limit,
                };
                let want = reference(e.view(), &g, &q);
                select_visible(e.view(), Source::Grid(&g), &q, &mut scratch, &mut out);
                let got: Vec<EntityId> = out.iter().map(|m| m.id).collect();
                assert_eq!(got, want, "frame {frame}, viewer {v}, limit {limit}");
                assert_eq!(
                    out.iter().filter(|m| m.near).count(),
                    got.len().min(NEAR_TIER)
                );
                current[k] = got;
            }
        }
    }

    fn sorted(mut v: Vec<EntityId>) -> Vec<EntityId> {
        v.sort_unstable();
        v
    }

    #[test]
    fn picks_the_nearest_n_and_never_the_viewer() {
        let (e, g, ids) = world(&[
            (0.0, 0.0, 0),
            (1.0, 0.0, 0),
            (50.0, 0.0, 0),
            (3.0, 0.0, 0),
            (-120.0, 90.0, 0),
            (0.0, 2.0, 0),
        ]);
        let got = select(&e, &g, ids[0], &[], 3);
        assert_eq!(got, sorted(vec![ids[1], ids[3], ids[5]]));
        // With room for everyone, even the far corner is included.
        assert_eq!(select(&e, &g, ids[0], &[], 50).len(), 5);
        assert!(select(&e, &g, ids[0], &[], 0).is_empty());
    }

    #[test]
    fn prefers_the_viewers_floor_at_similar_distances() {
        // Directly overhead on level 1 (6 m up) vs 12 m away on the same floor.
        let (e, g, ids) = world(&[(0.0, 0.0, 0), (0.5, 0.0, 1), (12.0, 0.0, 0)]);
        assert_eq!(select(&e, &g, ids[0], &[], 1), vec![ids[2]]);
        // But someone on another floor still beats a same-floor character far away.
        let (e, g, ids) = world(&[(0.0, 0.0, 0), (0.5, 0.0, 1), (40.0, 0.0, 0)]);
        assert_eq!(select(&e, &g, ids[0], &[], 1), vec![ids[1]]);
    }

    #[test]
    fn hysteresis_keeps_members_unless_clearly_beaten() {
        // Member at 10 m. Margin is max(2 m, 15% of 10 m) = 2 m.
        let (e, g, ids) = world(&[(0.0, 0.0, 0), (10.0, 0.0, 0), (0.0, 8.5, 0)]);
        assert_eq!(
            select(&e, &g, ids[0], &[ids[1]], 1),
            vec![ids[1]],
            "8.5 m is not 2 m closer"
        );
        // Without the hysteresis the closer one wins.
        assert_eq!(select(&e, &g, ids[0], &[], 1), vec![ids[2]]);

        let (e, g, ids) = world(&[(0.0, 0.0, 0), (10.0, 0.0, 0), (0.0, 7.9, 0)]);
        assert_eq!(
            select(&e, &g, ids[0], &[ids[1]], 1),
            vec![ids[2]],
            "7.9 m is"
        );

        // Far away the 15% margin dominates: a member at 100 m holds against 86 m.
        let (e, g, ids) = world(&[(0.0, 0.0, 0), (100.0, 0.0, 0), (0.0, 86.0, 0)]);
        assert_eq!(select(&e, &g, ids[0], &[ids[1]], 1), vec![ids[1]]);
        let (e, g, ids) = world(&[(0.0, 0.0, 0), (100.0, 0.0, 0), (0.0, 84.0, 0)]);
        assert_eq!(select(&e, &g, ids[0], &[ids[1]], 1), vec![ids[2]]);
    }

    #[test]
    fn walking_past_each_other_does_not_flicker() {
        // Two characters at nearly the same distance swap order every frame;
        // the visible set must not change at all.
        let mut current = Vec::new();
        let mut changes = 0;
        for frame in 0..40 {
            let wobble = if frame % 2 == 0 { 0.3 } else { -0.3 };
            let (e, g, ids) = world(&[
                (0.0, 0.0, 0),
                (20.0 + wobble, 0.0, 0),
                (0.0, 20.0 - wobble, 0),
            ]);
            // Ids are stable across rebuilt worlds because insertion order is.
            let next = select(&e, &g, ids[0], &current, 1);
            if frame > 0 && next != current {
                changes += 1;
            }
            current = next;
        }
        assert_eq!(changes, 0);
    }

    #[test]
    fn departed_members_drop_out() {
        let (mut e, g, ids) = world(&[(0.0, 0.0, 0), (1.0, 0.0, 0), (2.0, 0.0, 0)]);
        e.remove(ids[1]);
        let mut g2 = g.clone();
        g2.rebuild(&e.pos);
        assert_eq!(select(&e, &g2, ids[0], &[ids[1], ids[2]], 5), vec![ids[2]]);
    }

    #[test]
    fn priority_players_are_always_included() {
        let (e, g, ids) = world(&[
            (0.0, 0.0, 0),
            (1.0, 0.0, 0),
            (2.0, 0.0, 0),
            (150.0, 150.0, 3),
        ]);
        let got = select_with(&e, &g, ids[0], &[], &[ids[3]], 2);
        assert_eq!(got, sorted(vec![ids[1], ids[3]]));
        // Also when they were already members, and never twice.
        let got = select_with(&e, &g, ids[0], &[ids[3], ids[1]], &[ids[3]], 2);
        assert_eq!(got, sorted(vec![ids[1], ids[3]]));
        // The viewer listing itself as a partner is ignored.
        let got = select_with(&e, &g, ids[0], &[], &[ids[0]], 1);
        assert_eq!(got, vec![ids[1]]);
    }

    #[test]
    fn only_the_viewers_zone_is_visible() {
        let (mut e, _, ids) = world(&[(0.0, 0.0, 0), (1.0, 0.0, 0), (2.0, 0.0, 0), (3.0, 0.0, 0)]);
        // 1 and 2 are in a dorm instance (zone 7); the grid holds the castle only.
        for &k in &[1, 2] {
            let i = e.index_of(ids[k]).unwrap();
            e.zone[i] = 7;
        }
        let mut g = Grid::new([-200.0, -200.0], [200.0, 200.0]);
        let zone = e.zone.clone();
        g.rebuild_where(&e.pos, |i| zone[i] == CASTLE);
        // A castle viewer whose old set and priority list name dorm occupants
        // sees only castle characters.
        let got = select_with(&e, &g, ids[0], &[ids[1]], &[ids[2]], 10);
        assert_eq!(got, vec![ids[3]]);

        // In the dorm: every other occupant, from the member list.
        let q = Query {
            viewer: e.index_of(ids[1]).unwrap(),
            current: &[],
            priority: &[ids[3]],
            group: &[],
            limit: 10,
        };
        let (mut s, mut out) = (VisScratch::default(), Vec::new());
        let members = [ids[1], ids[2]];
        select_visible(e.view(), Source::Members(&members), &q, &mut s, &mut out);
        assert_eq!(
            out,
            vec![Member {
                id: ids[2],
                near: true
            }]
        );
    }

    /// Party members rank ahead of every other priority player: with more
    /// priority players than the view limit, the party is never left out.
    #[test]
    fn group_members_outrank_priority_players() {
        let spots: Vec<(f32, f32, i32)> = (0..=40).map(|n| (n as f32 * 3.0, 0.0, 0)).collect();
        let (e, g, ids) = world(&spots);
        // Ten friends near by, the party far away at the end of the line.
        let priority: Vec<EntityId> = ids[1..=10].to_vec();
        let group = [ids[39], ids[40]];
        let q = Query {
            viewer: 0,
            current: &[],
            priority: &priority,
            group: &group,
            limit: 4,
        };
        let (mut s, mut out) = (VisScratch::default(), Vec::new());
        select_visible(e.view(), Source::Grid(&g), &q, &mut s, &mut out);
        let got: Vec<EntityId> = out.iter().map(|m| m.id).collect();
        assert_eq!(got.len(), 4);
        assert!(got.contains(&ids[39]) && got.contains(&ids[40]), "{got:?}");
        // A group member in another zone is not seen.
        let mut e = e;
        let i = e.index_of(ids[40]).unwrap();
        e.zone[i] = 7;
        let mut g = Grid::new([-200.0, -200.0], [200.0, 200.0]);
        let zone = e.zone.clone();
        g.rebuild_where(&e.pos, |i| zone[i] == CASTLE);
        select_visible(e.view(), Source::Grid(&g), &q, &mut s, &mut out);
        assert!(!out.iter().any(|m| m.id == ids[40]));
    }

    #[test]
    fn the_best_ranked_are_near() {
        // A line of characters 1 m apart: the NEAR_TIER closest are near.
        let spots: Vec<(f32, f32, i32)> = (0..=60).map(|n| (n as f32, 0.0, 0)).collect();
        let (e, g, ids) = world(&spots);
        let q = Query {
            viewer: 0,
            current: &[],
            priority: &[ids[60]],
            group: &[],
            limit: 50,
        };
        let (mut s, mut out) = (VisScratch::default(), Vec::new());
        select_visible(e.view(), Source::Grid(&g), &q, &mut s, &mut out);
        assert_eq!(out.len(), 50);
        let near: Vec<EntityId> = out.iter().filter(|m| m.near).map(|m| m.id).collect();
        // The priority player ranks first, then the 24 closest.
        let mut want: Vec<EntityId> = ids[1..NEAR_TIER].to_vec();
        want.push(ids[60]);
        want.sort_unstable();
        assert_eq!(near, want);
    }

    #[test]
    fn grid_search_matches_brute_force() {
        // Deterministic pseudo-random crowd over several floors.
        let mut seed = 0x2545_f491_u32;
        let mut next = || {
            seed ^= seed << 13;
            seed ^= seed >> 17;
            seed ^= seed << 5;
            (seed % 10_000) as f32 / 10_000.0
        };
        let spots: Vec<(f32, f32, i32)> = (0..400)
            .map(|_| {
                (
                    next() * 300.0 - 150.0,
                    next() * 300.0 - 150.0,
                    (next() * 3.0) as i32,
                )
            })
            .collect();
        let (e, g, ids) = world(&spots);
        for (v, limit) in [(0usize, 20usize), (17, 50), (123, 100), (399, 1)] {
            let got = select(&e, &g, ids[v], &[], limit);
            let vi = e.index_of(ids[v]).unwrap();
            let mut all: Vec<(f32, EntityId)> = (0..e.len())
                .filter(|&i| i != vi)
                .map(|i| {
                    (
                        score(e.pos[vi], e.level[vi], e.pos[i], e.level[i]),
                        e.ids[i],
                    )
                })
                .collect();
            all.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap().then(a.1.cmp(&b.1)));
            let want = sorted(all.iter().take(limit).map(|&(_, id)| id).collect());
            assert_eq!(got, want, "viewer {v}, limit {limit}");
        }
    }

    #[test]
    fn students_rank_behind_players_and_are_capped_per_view() {
        // NPCs: eight background students close by, players farther off.
        let rules = &crate::catalog::shipped().npcs.roster.rules;
        let cap = usize::from(rules.students_in_view_max);
        let student_ids: Vec<String> = crate::catalog::shipped()
            .npcs
            .roster
            .of_kind(sim_core::npcs::NpcKind::Student)
            .map(|n| n.id.clone())
            .collect();
        assert!(student_ids.len() > cap);
        let mut e = Entities::default();
        let now = Instant::now();
        let viewer = e
            .insert(test_entity("me", [0.0, 0.0, 0.0], 0, House::Skytalon), now)
            .unwrap();
        let mut students = Vec::new();
        for (k, id) in student_ids.iter().enumerate().take(8) {
            let mut s = test_entity(id, [1.0 + k as f32, 0.0, 0.0], 0, House::Skytalon);
            s.info.npc = Some(id.clone());
            students.push(e.insert(s, now).unwrap());
        }
        // A ghost close by is no student: never capped.
        let mut ghost = test_entity("ghost", [0.0, 0.0, 1.5], 0, House::Skytalon);
        ghost.info.npc = Some("ghost_house_c".into());
        let ghost = e.insert(ghost, now).unwrap();
        let players: Vec<EntityId> = (0..30)
            .map(|k| {
                let p = test_entity(
                    &format!("p{k}"),
                    [0.0, 0.0, 10.0 + k as f32 * 0.5],
                    0,
                    House::Skytalon,
                );
                e.insert(p, now).unwrap()
            })
            .collect();
        let mut g = Grid::new([-200.0, -200.0], [200.0, 200.0]);
        g.rebuild(&e.pos);

        // Plenty of room: every player, the ghost, and only the nearest
        // `cap` students.
        let got = select(&e, &g, viewer, &[], 50);
        let shown: Vec<EntityId> = students
            .iter()
            .copied()
            .filter(|s| got.contains(s))
            .collect();
        assert_eq!(shown, students[..cap].to_vec());
        assert!(got.contains(&ghost));
        assert!(players.iter().all(|p| got.contains(p)));
        // A tight view: a player 10 m away beats a student 1 m away.
        let got = select(&e, &g, viewer, &[], 2);
        assert!(got.contains(&ghost) && got.contains(&players[0]), "{got:?}");
        // Members keep the cap too.
        let got = select(&e, &g, viewer, &students, 50);
        assert_eq!(students.iter().filter(|s| got.contains(s)).count(), cap);
    }
}
