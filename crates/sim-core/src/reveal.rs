//! Aperio (spells to level 20, protocol v11): what the charm shows its
//! caster. A pure function of the world and the castle's secrets, so the
//! server answers a cast with `ServerMsg::Revealed` and tests need no clock.

use protocol::{REVEAL_SPOTS_MAX, RevealKind};

use crate::layout::{InteractableKind, Surface};
use crate::secrets::Secrets;
use crate::world::World;

/// Aperio: passage doors (`Secrets` door surfaces' centres at ground
/// height) and find spots (`InteractableKind::FindSpot` positions) within
/// `radius_m` horizontally and `height_m` vertically of `at`, nearest
/// first, at most [`REVEAL_SPOTS_MAX`]. `secrets` is `None` outside the
/// castle (dorms, classes, the Wishing Room show find spots only).
pub fn reveal_near(
    world: &World,
    secrets: Option<&Secrets>,
    at: [f32; 3],
    radius_m: f32,
    height_m: f32,
) -> Vec<(RevealKind, [f32; 3])> {
    if !at.iter().all(|v| v.is_finite()) {
        return Vec::new();
    }
    let near = |p: [f32; 3]| {
        let d = (p[0] - at[0]).hypot(p[2] - at[2]);
        (d <= radius_m && (p[1] - at[1]).abs() <= height_m).then_some(d)
    };
    let mut found: Vec<(f32, RevealKind, [f32; 3])> = Vec::new();
    if let Some(secrets) = secrets {
        for secret in secrets.secrets() {
            for door in &secret.doors {
                let Some(s) = world.surfaces().iter().find(|s| &s.id == door) else {
                    continue;
                };
                let p = door_centre(world, s);
                if let Some(d) = near(p) {
                    found.push((d, RevealKind::Passage, p));
                }
            }
        }
    }
    for i in world.interactables() {
        if i.kind == InteractableKind::FindSpot
            && let Some(d) = near(i.pos)
        {
            found.push((d, RevealKind::FindSpot, i.pos));
        }
    }
    found.sort_by(|a, b| a.0.total_cmp(&b.0));
    found.truncate(REVEAL_SPOTS_MAX);
    found.into_iter().map(|(_, k, p)| (k, p)).collect()
}

/// A door surface's centre, on its ground.
fn door_centre(world: &World, s: &Surface) -> [f32; 3] {
    let (x, z) = ((s.min[0] + s.max[0]) * 0.5, (s.min[1] + s.max[1]) * 0.5);
    let y = world.ground_at(x, z, s.y).map_or(s.y, |g| g.y);
    [x, y, z]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::{Interactable, Layout};

    const LAYOUT: &str = include_str!("../../../data/world/layout.json");
    const SECRETS: &str = include_str!("../../../data/world/secrets.json");

    fn castle() -> (World, Secrets) {
        let layout = Layout::from_json(LAYOUT).unwrap();
        let secrets = Secrets::from_json(SECRETS, &layout).unwrap();
        (World::new(layout).unwrap(), secrets)
    }

    #[test]
    fn aperio_shows_nearby_passages_and_find_spots_nearest_first() {
        let (world, secrets) = castle();
        // By the Weavers' Stair tapestry door (fifth floor).
        let at = [-76.0, 45.0, 2.0];
        let spots = reveal_near(&world, Some(&secrets), at, 20.0, 4.0);
        assert!(
            spots.iter().any(|(k, _)| *k == RevealKind::Passage),
            "{spots:?}"
        );
        let dist = |p: [f32; 3]| (p[0] - at[0]).hypot(p[2] - at[2]);
        for w in spots.windows(2) {
            assert!(dist(w[0].1) <= dist(w[1].1));
        }
        for (_, p) in &spots {
            assert!(dist(*p) <= 20.0 && (p[1] - at[1]).abs() <= 4.0, "{p:?}");
        }
        // Outside the castle's secrets: no passages.
        let none = reveal_near(&world, None, at, 20.0, 4.0);
        assert!(none.iter().all(|(k, _)| *k == RevealKind::FindSpot));
        // Nothing far from everything.
        assert!(reveal_near(&world, Some(&secrets), [900.0, 0.0, 900.0], 20.0, 4.0).is_empty());
        assert!(reveal_near(&world, Some(&secrets), [f32::NAN, 0.0, 0.0], 20.0, 4.0).is_empty());
    }

    #[test]
    fn aperio_stays_on_its_floor_and_is_capped() {
        let (world, secrets) = castle();
        // A find spot: seen from its own floor, not from a floor above.
        let spot = world
            .interactables()
            .iter()
            .find(|i| i.kind == InteractableKind::FindSpot)
            .unwrap()
            .pos;
        let here = reveal_near(&world, Some(&secrets), spot, 5.0, 4.0);
        assert!(
            here.iter()
                .any(|(k, p)| *k == RevealKind::FindSpot && *p == spot)
        );
        let above = [spot[0], spot[1] + 6.0, spot[2]];
        assert!(
            !reveal_near(&world, Some(&secrets), above, 5.0, 4.0)
                .iter()
                .any(|(_, p)| *p == spot)
        );
        // Many spots in reach: the nearest REVEAL_SPOTS_MAX.
        let mut layout = Layout::from_json(LAYOUT).unwrap();
        for k in 0..(REVEAL_SPOTS_MAX + 8) {
            layout.interactables.push(Interactable {
                id: format!("test_spot_{k}"),
                kind: InteractableKind::FindSpot,
                house: None,
                pos: [
                    spot[0] + (k % 8) as f32 * 0.04,
                    spot[1],
                    spot[2] + (k / 8) as f32 * 0.04,
                ],
                yaw: 0.0,
                radius: 1.0,
                name: None,
            });
        }
        let world = World::new(layout).unwrap();
        let many = reveal_near(&world, None, spot, 20.0, 4.0);
        assert_eq!(many.len(), REVEAL_SPOTS_MAX);
        assert_eq!(many[0].1, spot);
    }
}
