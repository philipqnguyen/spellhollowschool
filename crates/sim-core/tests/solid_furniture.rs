//! Solid furniture and props (docs/architecture.md "Layouts"):
//! with every shipped layout's solids in place, nothing a character
//! could reach before is walled in, every spawn and arrival point is free,
//! every interactable can be used from a reachable spot, every landmark (the
//! Monstra Viam trail's and the tour's destinations, the NPCs' marks) has one
//! near it, and the Monstra Viam trail still finds every key place.
//!
//! The reach is a flood over a grid from the spawns with the same footprint
//! test as walking (`sim_core::Reach`), with and without the solids.

use std::collections::HashMap;

use sim_core::layout::InteractableKind;
use sim_core::{
    Layout, MoveParams, Reach, World, footprint_ground, layout::INTERACT_HEIGHT_TOLERANCE,
    stand_near,
};

const CASTLE: &str = include_str!("../../../data/world/layout.json");

/// Grid spacing of the floods (metres): an aisle a character fits through
/// (0.6 m) is found from 0.8 m wide.
const STEP: f32 = 0.2;

/// Walled-in pockets smaller than this (m²: a nook between a shelf and a
/// corner) are not ground anybody misses.
const POCKET_M2: f32 = 0.5;

/// Interactables are used from this far inside their reach (metres).
const REACH_MARGIN: f32 = 0.1;

/// A landmark's nearest reachable spot lies at most this far from it
/// (metres): the house tables' landmarks stand at a table's middle.
const LANDMARK_NEAR: f32 = 2.5;

/// How far from a landmark inside furniture its free spot may lie (metres:
/// the courtyard fountain's anchor is at its middle, 4 m from the rim).
const LANDMARK_FREE: f32 = 6.0;

/// Ground walled in on purpose: the paddock's creature pen (a closed fence
/// the class watches over, `tools/blender/world/school_rooms_paddock.py` PEN).
fn on_purpose(layout: &str, p: [f32; 3]) -> bool {
    layout == "class_paddock" && (-5.4..=5.4).contains(&p[0]) && (-5.4..=-0.8).contains(&p[2])
}

fn instances() -> Vec<(String, String)> {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/instances");
    let mut out: Vec<(String, String)> = std::fs::read_dir(&dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .map(|p| {
            let id = p.file_stem().unwrap().to_string_lossy().into_owned();
            (id, std::fs::read_to_string(&p).unwrap())
        })
        .collect();
    out.sort();
    out
}

/// Every layout to check: the castle (stair state 0, every house gate open)
/// and each instance room.
fn layouts() -> Vec<(String, Layout)> {
    let mut out = vec![(
        "castle".to_string(),
        Layout::from_json(CASTLE).unwrap().for_stairs(0),
    )];
    for (id, json) in instances() {
        out.push((id, Layout::from_json(&json).unwrap()));
    }
    out
}

/// The floods from a layout's spawns (one per spawn not reached yet: the
/// duel arena's duelists start on the stage, its spectators on the floor).
struct Floods(Vec<Reach>);

impl Floods {
    fn reaches(&self, pos: [f32; 3], within: f32, dy: f32) -> bool {
        self.0.iter().any(|r| r.reaches(pos, within, dy))
    }

    fn points(&self) -> impl Iterator<Item = [f32; 3]> + '_ {
        self.0.iter().flat_map(|r| r.points())
    }
}

fn flood(world: &World, params: &MoveParams) -> Floods {
    let mut out = Floods(Vec::new());
    for s in world.spawns() {
        if !out.reaches(s.pos, 0.5, 0.5) {
            out.0
                .push(Reach::flood(world, params, s.pos, STEP, 5_000_000));
        }
    }
    out
}

#[test]
fn every_layout_has_solids() {
    let layout = Layout::from_json(CASTLE).unwrap();
    assert!(
        layout.solids.len() > 300,
        "{} solids in the castle",
        layout.solids.len()
    );
    for (id, l) in layouts() {
        if id.starts_with("dorm_") {
            assert!(
                l.solids.is_empty(),
                "{id}: dorm shells keep their fixed pieces out of their floors"
            );
        } else {
            assert!(!l.solids.is_empty(), "{id} has no solid furniture");
        }
    }
}

#[test]
fn spawns_and_arrival_points_are_free() {
    let params = MoveParams::default();
    for (id, layout) in layouts() {
        let world = World::new(layout).unwrap();
        for s in world.spawns() {
            let [x, y, z] = s.pos;
            assert!(
                world.solid_at(x, y, z).is_none(),
                "{id}: spawn {} inside a solid",
                s.id
            );
            let g = world
                .ground_at(x, z, y)
                .unwrap_or_else(|| panic!("{id}: spawn {} off the ground", s.id));
            assert!(
                footprint_ground(&world, &params, x, z, g.y).is_some(),
                "{id}: a character does not fit on spawn {}",
                s.id
            );
        }
        for i in world.interactables() {
            let arrival = matches!(
                i.kind,
                InteractableKind::DormDoor
                    | InteractableKind::DormExit
                    | InteractableKind::ClassDoor
                    | InteractableKind::InstanceExit
                    | InteractableKind::WishingRoomDoor
            );
            if !arrival {
                continue;
            }
            let [x, y, z] = i.pos;
            assert!(
                world.solid_at(x, y, z).is_none(),
                "{id}: characters arrive at {} inside {:?}",
                i.id,
                world.solid_at(x, y, z).map(|b| &b.id)
            );
            assert!(
                stand_near(&world, &params, i.pos, params.radius).is_some(),
                "{id}: no room to arrive at {}",
                i.id
            );
        }
    }
}

/// Nothing reachable before is walled in, and every interactable, landmark
/// and spawn is reached.
#[test]
fn nothing_is_walled_in_and_everything_is_reached() {
    let params = MoveParams::default();
    let mut failures = Vec::new();
    for (id, layout) in layouts() {
        let solid = World::new(layout.clone()).unwrap();
        let bare = World::new(Layout {
            solids: Vec::new(),
            ..layout.clone()
        })
        .unwrap();
        let with = flood(&solid, &params);
        let before = flood(&bare, &params);
        // Points reached before where a character still fits, not reached
        // now, joined into pockets (grid neighbours).
        let key = |p: [f32; 3]| {
            (
                (p[0] / STEP).round() as i32,
                (p[2] / STEP).round() as i32,
                (p[1] / 2.0).floor() as i32,
            )
        };
        let mut lost: HashMap<(i32, i32, i32), [f32; 3]> = HashMap::new();
        for p in before.points() {
            let fits = footprint_ground(&solid, &params, p[0], p[2], p[1]).is_some();
            if fits && !with.reaches(p, STEP * 0.5, 0.3) && !on_purpose(&id, p) {
                lost.insert(key(p), p);
            }
        }
        let mut seen: HashMap<(i32, i32, i32), ()> = HashMap::new();
        let mut keys: Vec<_> = lost.keys().copied().collect();
        keys.sort();
        for k in keys {
            if seen.contains_key(&k) {
                continue;
            }
            let (mut todo, mut size) = (vec![k], 0usize);
            seen.insert(k, ());
            while let Some((i, j, b)) = todo.pop() {
                size += 1;
                for n in [(i + 1, j, b), (i - 1, j, b), (i, j + 1, b), (i, j - 1, b)] {
                    if lost.contains_key(&n) && seen.insert(n, ()).is_none() {
                        todo.push(n);
                    }
                }
            }
            let area = size as f32 * STEP * STEP;
            if area > 0.15 {
                let p = lost[&k];
                eprintln!(
                    "pocket {id}: {area:.2} m² at ({:.1}, {:.1}, {:.1})",
                    p[0], p[1], p[2]
                );
            }
            if area > POCKET_M2 {
                let p = lost[&k];
                failures.push(format!(
                    "{id}: {area:.1} m² walled in at ({:.1}, {:.1}, {:.1})",
                    p[0], p[1], p[2]
                ));
            }
        }
        for i in solid.interactables() {
            let within = (i.radius - REACH_MARGIN).max(STEP);
            if !with.reaches(i.pos, within, INTERACT_HEIGHT_TOLERANCE)
                && before.reaches(i.pos, within, INTERACT_HEIGHT_TOLERANCE)
            {
                failures.push(format!(
                    "{id}: interactable {} cannot be reached within {within} m",
                    i.id
                ));
            }
        }
        // A landmark (a trail's or the tour's destination, an NPC's mark, a
        // sound's anchor) in a piece of furniture: the trail ends at the
        // nearest free spot by it (`stand_near`), which must be reached.
        for l in &solid.layout().landmarks {
            if !before.reaches(l.pos, LANDMARK_NEAR, 1.5) || on_purpose(&id, l.pos) {
                continue;
            }
            // On the floor under it (the greenhouse's plants stand on the staging bench).
            let floor = bare
                .ground_below(l.pos[0], l.pos[2], l.pos[1] + 0.1)
                .unwrap_or(l.pos[1]);
            let spot = stand_near(&solid, &params, [l.pos[0], floor, l.pos[2]], LANDMARK_FREE);
            if !spot.is_some_and(|s| with.reaches(s, STEP, 0.5)) {
                failures.push(format!(
                    "{id}: landmark {} has no reachable spot by it ({spot:?})",
                    l.id
                ));
            }
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// The Monstra Viam trail (walking round every solid's box) still reaches every
/// key place from the front steps, for every house.
#[test]
fn the_trail_reaches_every_key_place_round_the_furniture() {
    use protocol::House;
    use sim_core::nav::{Nav, trail_allows};
    let key_places =
        sim_core::KeyPlaces::from_json(include_str!("../../../data/world/key_places.json"))
            .unwrap();
    let params = MoveParams::default();
    for house in House::ALL {
        let layout = Layout::from_json_for_house(CASTLE, house)
            .unwrap()
            .for_stairs(0);
        let world = std::sync::Arc::new(World::new(layout).unwrap());
        let nav = Nav::new(world.clone(), &[], trail_allows);
        let from = world.spawns()[0].pos;
        for place in key_places.offered_to(house) {
            let Some(target) = place.resolve(world.layout()) else {
                continue;
            };
            let to = stand_near(&world, &params, target, LANDMARK_NEAR)
                .unwrap_or_else(|| panic!("{house:?}: no free spot by {}", place.id));
            let path = nav.path(from, to);
            assert!(path.is_some(), "{house:?}: no trail to {}", place.id);
        }
    }
}

/// Solid furniture's cost on the movement queries (docs/architecture.md
/// "Layouts", the numbers there):
/// `cargo test -p sim-core --release --test solid_furniture -- --ignored --nocapture bench`.
/// The shipped castle without and with its solids, and with 5,000 more
/// spread over the castle's floors.
#[test]
#[ignore]
fn bench_movement_with_solids() {
    use sim_core::{Blocker, MoveCheck, check_move, move_and_slide};
    let params = MoveParams::default();
    let shipped = Layout::from_json(CASTLE).unwrap().for_stairs(0);
    let bare = Layout {
        solids: Vec::new(),
        ..shipped.clone()
    };
    let mut rng = 0x2545_f491_u32;
    let mut next = move || {
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        rng as f32 / u32::MAX as f32
    };
    let floors: Vec<_> = shipped
        .surfaces
        .iter()
        .filter(|s| s.heights.is_none() && s.y_top.is_none())
        .cloned()
        .collect();
    let mut crowded = shipped.clone();
    for k in 0..5000 {
        let s = &floors[k % floors.len()];
        let x = s.min[0] + next() * (s.max[0] - s.min[0]);
        let z = s.min[1] + next() * (s.max[1] - s.min[1]);
        crowded.solids.push(Blocker {
            id: format!("stress/{k}"),
            at: [x, z],
            half: [0.2 + next() * 0.4, 0.2 + next() * 0.4],
            y: [s.y - 0.3, s.y + 1.0],
            yaw: next() * 3.0,
            round: k % 3 == 0,
        });
    }
    // Moves from points on the floors, the same for every world.
    let world0 = World::new(bare.clone()).unwrap();
    let moves: Vec<([f32; 3], [f32; 3])> = (0..4096)
        .filter_map(|_| {
            let s = &floors[(next() * floors.len() as f32) as usize % floors.len()];
            let x = s.min[0] + next() * (s.max[0] - s.min[0]);
            let z = s.min[1] + next() * (s.max[1] - s.min[1]);
            let (a, d) = (next() * std::f32::consts::TAU, next() * 0.8);
            world0.ground_at(x, z, s.y)?;
            Some(([x, s.y, z], [x + a.cos() * d, s.y, z + a.sin() * d]))
        })
        .collect();
    for (name, layout) in [
        ("no furniture", bare),
        ("shipped", shipped),
        ("shipped + 5,000", crowded),
    ] {
        let n = layout.solids.len();
        let t = std::time::Instant::now();
        let world = World::new(layout).unwrap();
        let build = t.elapsed();
        let calls = 200_000;
        let t = std::time::Instant::now();
        let mut ok = 0;
        for i in 0..calls {
            let (a, b) = moves[i % moves.len()];
            let c = check_move(std::hint::black_box(&world), &params, a, b, 0.2);
            ok += matches!(c, MoveCheck::Accept { .. }) as u32;
        }
        let check = t.elapsed().as_nanos() as f64 / calls as f64;
        let t = std::time::Instant::now();
        let mut sum = 0.0;
        for i in 0..calls {
            let (a, b) = moves[i % moves.len()];
            let d = [(b[0] - a[0]) / 4.0, (b[2] - a[2]) / 4.0];
            sum += move_and_slide(std::hint::black_box(&world), &params, a, d)[0];
        }
        let slide = t.elapsed().as_nanos() as f64 / calls as f64;
        let t = std::time::Instant::now();
        let mut found = 0;
        for i in 0..calls {
            let (a, _) = moves[i % moves.len()];
            found += world.ground_at(a[0], a[2], a[1]).is_some() as u32;
        }
        let ground = t.elapsed().as_nanos() as f64 / calls as f64;
        println!(
            "{name:>16}: {n:5} solids, built in {build:.2?}; check_move {check:.0} ns, \
             move_and_slide (one frame) {slide:.0} ns, ground_at {ground:.0} ns \
             ({ok} accepted, {found} on ground, {sum:.0})"
        );
    }
}

/// Walkers that bump into furniture (the server benchmark's: a long
/// `move_and_slide` per report, turning when stuck) are never corrected.
#[test]
fn walkers_bumping_into_furniture_are_never_corrected() {
    use sim_core::{MoveCheck, check_move, move_and_slide};
    let params = MoveParams::default();
    let world = World::new(Layout::from_json(CASTLE).unwrap().for_stairs(0)).unwrap();
    let mut rng = 0x9e37_79b9u32;
    let mut next = move || {
        rng ^= rng << 13;
        rng ^= rng >> 17;
        rng ^= rng << 5;
        rng as f32 / u32::MAX as f32
    };
    // Start by furniture: beside every other solid.
    let starts: Vec<[f32; 3]> = world
        .solids()
        .iter()
        .step_by(2)
        .filter_map(|b| {
            let p = [b.at[0] + b.half[0] + 0.5, b.y[0] + 0.3, b.at[1]];
            stand_near(&world, &params, p, 1.0)
        })
        .collect();
    let mut bad = Vec::new();
    for (k, &s) in starts.iter().enumerate() {
        let mut pos = s;
        let mut heading = next() * std::f32::consts::TAU;
        for _ in 0..800 {
            let step = if k % 3 == 0 {
                params.run_speed
            } else {
                params.walk_speed
            } * 0.1;
            let to = move_and_slide(
                &world,
                &params,
                pos,
                [heading.sin() * step, heading.cos() * step],
            );
            if (to[0] - pos[0]).abs() + (to[2] - pos[2]).abs() < 0.05 || next() < 0.05 {
                heading = next() * std::f32::consts::TAU;
            }
            match check_move(&world, &params, pos, to, 0.1) {
                MoveCheck::Accept { pos: p } => pos = p,
                MoveCheck::Reject(r) => {
                    bad.push(format!("{r:?} {pos:?} -> {to:?}"));
                    pos = to;
                }
            }
        }
    }
    assert!(
        bad.is_empty(),
        "{} corrections:\n{}",
        bad.len(),
        bad[..bad.len().min(10)].join("\n")
    );
}
