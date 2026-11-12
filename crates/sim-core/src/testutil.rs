//! Layout builders and a tiny deterministic RNG shared by the unit tests.

use crate::layout::{Layout, Rise, Spawn, Surface, SurfaceKind};
use crate::world::World;

pub fn flat(id: &str, min: [f32; 2], max: [f32; 2], y: f32) -> Surface {
    Surface {
        id: id.to_string(),
        kind: SurfaceKind::Room,
        name: id.to_string(),
        level: 0,
        cell: "test".to_string(),
        min,
        max,
        y,
        y_top: None,
        rise: None,
        ceiling: Some(4.0),
        house: None,
        heights: None,
        moving: None,
        secret: false,
    }
}

pub fn stair(id: &str, min: [f32; 2], max: [f32; 2], y: f32, y_top: f32, rise: Rise) -> Surface {
    Surface {
        kind: SurfaceKind::Stair,
        y_top: Some(y_top),
        rise: Some(rise),
        ..flat(id, min, max, y)
    }
}

pub fn on_level(surface: Surface, level: i32) -> Surface {
    Surface { level, ..surface }
}

pub fn layout(surfaces: Vec<Surface>) -> Layout {
    Layout {
        version: crate::layout::LAYOUT_VERSION,
        surfaces,
        spawns: Vec::new(),
        landmarks: Vec::new(),
        interactables: Vec::new(),
        place_areas: Vec::new(),
        areas: Vec::new(),
        solids: Vec::new(),
        rings: Vec::new(),
    }
}

pub fn layout_json(surfaces: &[Surface], spawns: &[(&str, [f32; 3])]) -> String {
    let mut l = layout(surfaces.to_vec());
    l.spawns = spawns
        .iter()
        .map(|&(id, pos)| Spawn {
            id: id.to_string(),
            pos,
            yaw: 0.0,
        })
        .collect();
    serde_json::to_string(&l).unwrap()
}

pub fn world(surfaces: Vec<Surface>) -> World {
    World::new(layout(surfaces)).unwrap()
}

/// A 10 x 10 m room whose east wall (the 0.5 m gap at x = 10..10.5) has a 2 m
/// door at z = 4..6 into a corridor running east.
pub fn room_door_corridor() -> World {
    world(vec![
        flat("room", [0.0, 0.0], [10.0, 10.0], 0.0),
        Surface {
            kind: SurfaceKind::Door,
            ..flat("door", [10.0, 4.0], [10.5, 6.0], 0.0)
        },
        Surface {
            kind: SurfaceKind::Corridor,
            ..flat("corridor", [10.5, 3.0], [30.5, 7.0], 0.0)
        },
    ])
}

/// A 20 x 20 m hall with a level-1 balcony 4 m up over its north strip
/// (z = 0..4) and a 2 m wide stair inside the hall rising north onto it.
pub fn hall_and_balcony() -> World {
    world(vec![
        flat("hall", [0.0, 0.0], [20.0, 20.0], 0.0),
        on_level(flat("balcony", [0.0, 0.0], [20.0, 4.0], 4.0), 1),
        stair("stair", [8.0, 4.0], [10.0, 12.0], 0.0, 4.0, Rise::NegZ),
    ])
}

/// A grid of 12 x 12 m rooms separated by 0.5 m walls, joined by 2 m doors,
/// with exactly `surfaces` surfaces in total (rooms first, then doors).
pub fn synthetic_castle(surfaces: usize) -> World {
    const ROOM: f32 = 12.0;
    const PITCH: f32 = 12.5;
    // Rooms on an n x n grid have 2n(n-1) possible doors.
    let n = (1..)
        .find(|&n| n * n + 2 * n * (n - 1) >= surfaces)
        .unwrap();
    let mut out = Vec::with_capacity(surfaces);
    for i in 0..n * n {
        let (x, z) = ((i % n) as f32 * PITCH, (i / n) as f32 * PITCH);
        out.push(flat(&format!("room{i}"), [x, z], [x + ROOM, z + ROOM], 0.0));
    }
    'doors: for i in 0..n * n {
        let (x, z) = ((i % n) as f32 * PITCH, (i / n) as f32 * PITCH);
        let mid = ROOM / 2.0;
        let mut door = |id: String, min, max| {
            if out.len() < surfaces {
                out.push(Surface {
                    kind: SurfaceKind::Door,
                    ..flat(&id, min, max, 0.0)
                });
            }
            out.len() < surfaces
        };
        if i % n + 1 < n
            && !door(
                format!("door{i}e"),
                [x + ROOM, z + mid - 1.0],
                [x + PITCH, z + mid + 1.0],
            )
        {
            break 'doors;
        }
        if i / n + 1 < n
            && !door(
                format!("door{i}s"),
                [x + mid - 1.0, z + ROOM],
                [x + mid + 1.0, z + PITCH],
            )
        {
            break 'doors;
        }
    }
    out.truncate(surfaces);
    world(out)
}

/// xorshift32: deterministic and dependency-free.
pub struct Rng(pub u32);

impl Rng {
    pub fn next_u32(&mut self) -> u32 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        self.0 = x;
        x
    }

    /// Uniform in `[lo, hi)`.
    pub fn range(&mut self, lo: f32, hi: f32) -> f32 {
        lo + (self.next_u32() >> 8) as f32 / (1u32 << 24) as f32 * (hi - lo)
    }
}
