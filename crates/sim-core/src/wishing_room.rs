//! The Wishing Room: pacing past the blank wall of the
//! seventh-floor east corridor opens a door for you alone
//! (`data/world/secrets.json` `wishing_room`, read by [`crate::secrets`]).
//!
//! Two [`crate::layout::AreaKind::PaceMark`] boxes lie across the corridor,
//! either side of the blank wall. A **pass** is walking from one mark to the
//! other; it starts when the character was last in the mark it left and ends
//! when it reaches the other. The door opens when `passes` passes (three) have
//! ended with the first of them started at most `window_s` (60 s) before the
//! last one ended ([`Pacing::step`]). Then the counter starts over. The
//! server runs the counter only while the character stands on the corridor
//! and starts it over when it steps off ([`Pacing::reset`]); the door stays
//! open for that character for `door_open_s` (60 s).
//!
//! Pure: the caller passes the mark the character stands in after each
//! accepted move and the server's clock.

use serde::{Deserialize, Serialize};

use protocol::WishingRoomKind;

use crate::layout::{AreaKind, InteractableKind, Layout};

/// One room the opener may choose.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RoomDef {
    pub room: WishingRoomKind,
    /// Display name ("Lost Property Vault").
    pub name: String,
    /// Instance layout id (`data/world/instances/<layout>.json`).
    pub layout: String,
}

/// `data/world/secrets.json` `wishing_room`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WishingRoomRules {
    /// The corridor surface the pacing happens on.
    pub corridor: String,
    /// The two pace mark areas (ids of `pace_mark` areas).
    pub marks: [String; 2],
    /// Passes between the marks that open the door.
    pub passes: u8,
    /// Within this many seconds (from the first pass's start to the last's end).
    pub window_s: u32,
    /// The `wishing_room_door` interactable.
    pub door: String,
    /// How long the door stays open for its opener (seconds).
    pub door_open_s: u32,
    /// Most people in one opener's room (protocol::WISHING_ROOM_MAX at most).
    pub capacity: u8,
    /// The rooms, in `WishingRoomKind::ALL` order.
    pub rooms: Vec<RoomDef>,
}

impl WishingRoomRules {
    pub fn window_ms(&self) -> u64 {
        u64::from(self.window_s) * 1000
    }

    pub fn door_open_ms(&self) -> u64 {
        u64::from(self.door_open_s) * 1000
    }

    /// The room definition of `room`.
    pub fn room(&self, room: WishingRoomKind) -> &RoomDef {
        self.rooms
            .iter()
            .find(|r| r.room == room)
            .expect("validated: every room is listed")
    }

    /// Which mark (0 or 1, in `marks` order) `pos` stands in, if any.
    pub fn mark_at(&self, layout: &Layout, pos: [f32; 3]) -> Option<u8> {
        self.marks
            .iter()
            .position(|id| {
                layout
                    .area(id)
                    .is_some_and(|a| a.kind == AreaKind::PaceMark && a.contains(pos))
            })
            .map(|i| i as u8)
    }

    /// Checks the rules against the castle layout.
    pub fn validate(&self, layout: &Layout) -> Result<(), String> {
        let corridor = layout
            .surfaces
            .iter()
            .find(|s| s.id == self.corridor)
            .ok_or_else(|| format!("wishing_room: no corridor surface {:?}", self.corridor))?;
        if corridor.secret || corridor.moving.is_some() || corridor.house.is_some() {
            return Err("wishing_room: the corridor is public, fixed ground".into());
        }
        if self.marks[0] == self.marks[1] {
            return Err("wishing_room: two different marks".into());
        }
        for id in &self.marks {
            let a = layout
                .area(id)
                .filter(|a| a.kind == AreaKind::PaceMark)
                .ok_or_else(|| format!("wishing_room: no pace_mark area {id:?}"))?;
            let overlaps = a.min[0] < corridor.max[0]
                && a.max[0] > corridor.min[0]
                && a.min[2] < corridor.max[1]
                && a.max[2] > corridor.min[1]
                && a.min[1] <= corridor.y
                && a.max[1] >= corridor.y + 1.5;
            if !overlaps {
                return Err(format!(
                    "wishing_room: mark {id:?} does not lie across the corridor"
                ));
            }
        }
        if layout
            .areas
            .iter()
            .any(|a| a.kind == AreaKind::PaceMark && !self.marks.contains(&a.id))
        {
            return Err("wishing_room: a pace_mark area it does not name".into());
        }
        if self.passes < 2 || self.window_s == 0 || self.door_open_s == 0 {
            return Err("wishing_room: at least 2 passes, a window and a door time".into());
        }
        let door = layout
            .interactable(&self.door)
            .filter(|i| i.kind == InteractableKind::WishingRoomDoor)
            .ok_or_else(|| format!("wishing_room: no wishing_room_door {:?}", self.door))?;
        if !corridor.contains(door.pos[0], door.pos[2]) || door.house.is_some() {
            return Err("wishing_room: the door stands on the corridor, for everyone".into());
        }
        if layout
            .interactables
            .iter()
            .filter(|i| i.kind == InteractableKind::WishingRoomDoor)
            .count()
            != 1
        {
            return Err("wishing_room: exactly one wishing_room_door".into());
        }
        if !(2..=protocol::WISHING_ROOM_MAX).contains(&self.capacity) {
            return Err("wishing_room: capacity 2..WISHING_ROOM_MAX".into());
        }
        let order: Vec<WishingRoomKind> = self.rooms.iter().map(|r| r.room).collect();
        if order != WishingRoomKind::ALL {
            return Err("wishing_room: rooms are WishingRoomKind::ALL, in order".into());
        }
        if self
            .rooms
            .iter()
            .any(|r| r.name.trim().is_empty() || r.name.len() > 64 || r.layout.is_empty())
        {
            return Err("wishing_room: every room has a name and a layout".into());
        }
        Ok(())
    }
}

/// One character's pacing (server side, one per character on the corridor).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Pacing {
    /// The mark last stood in, and the last time the character was in it.
    last: Option<(u8, u64)>,
    /// When each pass of the current run started (oldest first).
    starts: Vec<u64>,
}

impl Pacing {
    /// After an accepted move: `mark` is the mark the character now stands
    /// in ([`WishingRoomRules::mark_at`]), `now_ms` the server's clock.
    /// True when this move opens the door (the counter then starts over).
    pub fn step(&mut self, rules: &WishingRoomRules, mark: Option<u8>, now_ms: u64) -> bool {
        let Some(m) = mark else {
            return false;
        };
        match self.last {
            Some((l, since)) if l != m => {
                self.starts.push(since);
                let oldest = now_ms.saturating_sub(rules.window_ms());
                self.starts.retain(|&t| t >= oldest);
                self.last = Some((m, now_ms));
                if self.starts.len() >= usize::from(rules.passes) {
                    self.reset();
                    return true;
                }
                false
            }
            _ => {
                self.last = Some((m, now_ms));
                false
            }
        }
    }

    /// Starts over (stepping off the corridor, or the door opened).
    pub fn reset(&mut self) {
        *self = Pacing::default();
    }

    /// Passes counted in the current run.
    pub fn passes(&self) -> usize {
        self.starts.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LAYOUT: &str = include_str!("../../../data/world/layout.json");
    const SECRETS: &str = include_str!("../../../data/world/secrets.json");

    fn shipped() -> (Layout, WishingRoomRules) {
        let layout = Layout::from_json(LAYOUT).unwrap();
        let v: serde_json::Value = serde_json::from_str(SECRETS).unwrap();
        let rules: WishingRoomRules = serde_json::from_value(v["wishing_room"].clone()).unwrap();
        rules.validate(&layout).unwrap();
        (layout, rules)
    }

    #[test]
    fn the_shipped_ritual() {
        let (layout, rules) = shipped();
        assert_eq!(
            (rules.passes, rules.window_s, rules.door_open_s),
            (3, 60, 60)
        );
        assert_eq!(rules.capacity, protocol::WISHING_ROOM_MAX);
        assert_eq!(
            rules.room(WishingRoomKind::LostProperty).layout,
            "lost_property_vault"
        );
        assert_eq!(
            rules.room(WishingRoomKind::Gathering).layout,
            "wishing_room_gathering"
        );
        // The marks lie either side of the door, along the corridor.
        let door = layout.interactable(&rules.door).unwrap().pos;
        assert_eq!(rules.mark_at(&layout, [-11.0, 63.0, 3.0]), Some(0));
        assert_eq!(rules.mark_at(&layout, [5.0, 63.0, 8.0]), Some(1));
        assert_eq!(rules.mark_at(&layout, door), None);
        assert_eq!(
            rules.mark_at(&layout, [-11.0, 54.0, 3.0]),
            None,
            "the floor below"
        );
    }

    /// Walks between the marks: `legs` passes, each taking `leg_ms`, after
    /// waiting `dwell_ms` in each mark; returns when the door opened.
    fn pace(
        rules: &WishingRoomRules,
        p: &mut Pacing,
        t0: u64,
        legs: usize,
        leg_ms: u64,
        dwell_ms: u64,
    ) -> Option<u64> {
        let mut t = t0;
        let mut at = 0u8;
        if p.step(rules, Some(at), t) {
            return Some(t);
        }
        for _ in 0..legs {
            t += dwell_ms;
            assert!(!p.step(rules, Some(at), t), "standing in a mark");
            t += leg_ms / 2;
            assert!(!p.step(rules, None, t), "between the marks");
            t += leg_ms / 2;
            at ^= 1;
            if p.step(rules, Some(at), t) {
                return Some(t);
            }
        }
        None
    }

    #[test]
    fn three_passes_within_a_minute_open_the_door() {
        let (_, rules) = shipped();
        let mut p = Pacing::default();
        // At a walk (16 m, about 4 s a pass) the third pass opens it.
        let opened = pace(&rules, &mut p, 1_000, 3, 4_000, 500).expect("opens");
        assert_eq!(opened, 1_000 + 3 * 4_500);
        assert_eq!(p.passes(), 0, "starts over");
        // Two passes do not.
        let mut p = Pacing::default();
        assert_eq!(pace(&rules, &mut p, 0, 2, 4_000, 0), None);
        assert_eq!(p.passes(), 2);
        // Slowly (18 s a pass): three passes in 54 s still open it.
        let mut p = Pacing::default();
        assert_eq!(pace(&rules, &mut p, 0, 10, 18_000, 0), Some(3 * 18_000));
        // Too slow (21 s a pass): never three in a minute.
        let mut p = Pacing::default();
        assert_eq!(pace(&rules, &mut p, 0, 12, 21_000, 0), None);
        // A slow pass, then three quick ones: the slow one falls out.
        let mut p = Pacing::default();
        assert_eq!(pace(&rules, &mut p, 0, 1, 70_000, 0), None);
        let mut q = p.clone();
        assert!(!q.step(&rules, Some(0), 74_000));
        assert!(!q.step(&rules, Some(1), 78_000));
        assert!(q.step(&rules, Some(0), 82_000), "three within a minute");
        // Going back to the mark one came from is not a pass.
        let mut p = Pacing::default();
        for k in 0..20 {
            assert!(!p.step(&rules, Some(0), k * 1000));
            assert!(!p.step(&rules, None, k * 1000 + 500));
        }
        assert_eq!(p.passes(), 0);
        // Stepping off the corridor starts over.
        let mut p = Pacing::default();
        assert_eq!(pace(&rules, &mut p, 0, 2, 4_000, 0), None);
        p.reset();
        assert_eq!(pace(&rules, &mut p, 10_000, 2, 4_000, 0), None);
        assert_eq!(pace(&rules, &mut p, 20_000, 1, 4_000, 0), Some(24_000));
    }

    #[test]
    fn rejects_bad_rules() {
        let (layout, rules) = shipped();
        let bad = |f: &dyn Fn(&mut WishingRoomRules)| {
            let mut r = rules.clone();
            f(&mut r);
            r.validate(&layout).unwrap_err()
        };
        assert!(bad(&|r| r.corridor = "nowhere".into()).contains("corridor"));
        assert!(bad(&|r| r.marks[1] = r.marks[0].clone()).contains("different"));
        assert!(bad(&|r| r.marks[1] = "nowhere".into()).contains("pace_mark"));
        assert!(bad(&|r| r.passes = 1).contains("passes"));
        assert!(bad(&|r| r.door = "owl_mail".into()).contains("wishing_room_door"));
        assert!(bad(&|r| r.capacity = 21).contains("capacity"));
        assert!(bad(&|r| r.rooms.reverse()).contains("order"));
        assert!(bad(&|r| r.rooms[0].name = " ".into()).contains("name"));
    }
}
