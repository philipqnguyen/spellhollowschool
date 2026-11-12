//! Snapshot v2: per-viewer delta states and the far tier.
//!
//! `ServerMsg::Snapshot` updates one client's *visible set* `V`: the entities
//! it is shown, kept **sorted by id ascending** on both ends. Each snapshot is
//! applied in this order (see [`SnapshotReceiver::apply`]):
//!
//! 1. **`left`**: these ids leave `V` (and their baselines are forgotten).
//! 2. **`entered`**: these ids join `V`. `left` and `entered` never share an id.
//!    Each is an [`crate::EntityEntry`]: the whole `EntityInfo`, or `Cached`
//!    when this connection already has it (see [`crate::looks`]).
//! 3. **`full`**: absolute states ([`EntityState`]). Every entity in
//!    `entered` has one in the same snapshot. A full state becomes the
//!    entity's *baseline*: the last state this viewer received for it.
//! 4. **`deltas`**: [`EntityDelta`]s, each naming its entity by its position
//!    `index` in `V` *after* steps 1 and 2. The new state is the baseline plus
//!    `(dx, dy, dz)` in the delta's units (below), with `yaw` and `anim`
//!    replaced; it becomes the new baseline. Deltas are sorted by `index`,
//!    there is at most one per entity, and never one for an entity that also
//!    has a full state in the same snapshot.
//!
//! **Units (protocol v8).** A delta whose `anim` is flying (`Anim::Fly` or
//! `Anim::Hover`, [`crate::Anim::is_flying`]) counts in **2 cm** units
//! ([`FLYING_DELTA_CM`]), every other one in centimetres. So a broom at up to
//! 11.5 m/s still fits a 6-byte delta at the far tier's 5 Hz (2.3 m, under
//! 2.54 m). The flying delta rounds the move to an even number of
//! centimetres, so the receiver's state may differ from the server's by a
//! centimetre: the server keeps the *receiver's* state as its baseline
//! (`EntityDelta::apply` of the delta it sent, not the state it wanted to
//! send), so the next delta corrects it and the two ends never drift apart.
//!
//! The server sends a full state instead of a delta on enter, whenever a
//! component would not fit an `i8` (more than 1.27 m since the baseline, 2.54
//! m for a flying state), and
//! as a periodic resync, at least every [`RESYNC_SNAPSHOTS`] snapshots per
//! visible entity, so a bookkeeping bug can never drift for long. A client
//! whose deltas do not line up (an index outside `V`, a full state for an id
//! not in `V`) has lost sync: it must drop the connection and reconnect,
//! which starts again from an empty set.
//!
//! `V` (with every baseline) is reset to empty by `ServerMsg::Welcome` and
//! by `ServerMsg::ZoneChanged`: each zone has its own visible set.
//!
//! **Far tier.** Entities whose state did not change since the baseline get
//! no update. Near entities that changed get one in every snapshot (10 Hz);
//! the server may skip far ones on alternate snapshots (5 Hz). Whatever the
//! tier, a visible entity whose state changed is updated within
//! [`MAX_UPDATE_GAP_SNAPSHOTS`] snapshots. So a client must not read a
//! missing update as "stopped": an entity stops when a state with an idle
//! (or emote) `anim` arrives, and only an entity whose last state is moving
//! and that has had no update for longer than `MAX_UPDATE_GAP_SNAPSHOTS`
//! snapshot intervals (plus jitter) has really stood still. Interpolation
//! must handle the uneven 100 / 200 ms intervals between updates.
//!
//! **Looks (protocol v10).** `looks` lists the members of `V` (after steps 1
//! and 2) whose look target changed for this viewer, each with whom it now
//! looks at ([`LookChange`]). A member that enters looks at nobody until a
//! change says otherwise, so an entering member that looks at someone has a
//! change in the same snapshot. The server shows a look to a viewer only
//! while the target is in the viewer's `V` (or is the viewer), is not under
//! the Invisibility Draught and stands within [`LOOK_RANGE_M`] (plus a
//! little slack) of the looker; otherwise the viewer is sent `None`. A
//! change for an id not in `V` is ignored (it never happens; looks are
//! cosmetic, so it is no desync).

use serde::{Deserialize, Serialize};

use crate::{Anim, EntityId, EntityState, QPos};

/// A changed entity is updated at least once every this many snapshots
/// (200 ms at 10 Hz), whatever its tier.
pub const MAX_UPDATE_GAP_SNAPSHOTS: u32 = 2;

/// Every visible entity gets a full state at least this often (10 s at 10 Hz).
pub const RESYNC_SNAPSHOTS: u32 = 100;

/// Protocol v8: centimetres per delta unit when the delta's `anim` is flying
/// (`Anim::Fly`, `Anim::Hover`); 1 otherwise.
pub const FLYING_DELTA_CM: i32 = 2;

/// Protocol v10: the farthest a character looks at another (m). Clients
/// choose look targets within it; the server keeps one only within it
/// (plus a little slack for latency).
pub const LOOK_RANGE_M: f32 = 20.0;

/// Protocol v10: clients send `ClientMsg::LookAt` at most this often (per
/// second), and only on a change.
pub const LOOK_SENDS_PER_SEC: u32 = 4;

/// Protocol v10: member `id` of the viewer's visible set now looks at
/// `target` (`None`: at nobody). See "Looks" above.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct LookChange {
    pub id: EntityId,
    pub target: Option<EntityId>,
}

/// A state relative to the viewer's baseline for one entity. Always 6 bytes
/// in postcard: `i8` and `u8` are single bytes and `anim` a one-byte variant.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct EntityDelta {
    /// Position of the entity in the viewer's visible set (sorted by id) after
    /// this snapshot's `left` and `entered` are applied. The set never holds
    /// more than `VIEW_LIMIT_MAX` (100) entities, so a byte is enough.
    pub index: u8,
    /// Movement since the baseline, in centimetres (in [`FLYING_DELTA_CM`]
    /// units when `anim` is flying).
    pub dx: i8,
    pub dy: i8,
    pub dz: i8,
    /// The new quantized yaw and animation (replacing the baseline's).
    pub yaw: u8,
    pub anim: Anim,
}

impl EntityDelta {
    /// Centimetres per unit of a delta carrying `anim` (protocol v8).
    pub fn unit_cm(anim: Anim) -> i32 {
        if anim.is_flying() { FLYING_DELTA_CM } else { 1 }
    }

    /// The delta from `base` toward `next`, or `None` if a component does not
    /// fit an `i8` (send a full state instead). A flying `next` is rounded to
    /// 2 cm units (half away from zero), so `apply` may land a centimetre off
    /// `next`: keep `delta.apply(base)` as the new baseline.
    pub fn between(index: u8, base: &EntityState, next: &EntityState) -> Option<EntityDelta> {
        let unit = i64::from(Self::unit_cm(next.anim));
        let d = |a: i32, b: i32| {
            let cm = i64::from(b) - i64::from(a);
            let units = if unit == 1 {
                cm
            } else {
                (cm + cm.signum() * (unit / 2)) / unit
            };
            i8::try_from(units).ok()
        };
        Some(EntityDelta {
            index,
            dx: d(base.pos.x, next.pos.x)?,
            dy: d(base.pos.y, next.pos.y)?,
            dz: d(base.pos.z, next.pos.z)?,
            yaw: next.yaw,
            anim: next.anim,
        })
    }

    /// `base` moved by this delta (in its units).
    pub fn apply(&self, base: &EntityState) -> EntityState {
        let unit = Self::unit_cm(self.anim);
        EntityState {
            id: base.id,
            pos: QPos {
                x: base.pos.x.wrapping_add(i32::from(self.dx) * unit),
                y: base.pos.y.wrapping_add(i32::from(self.dy) * unit),
                z: base.pos.z.wrapping_add(i32::from(self.dz) * unit),
            },
            yaw: self.yaw,
            anim: self.anim,
        }
    }
}

/// Why a snapshot could not be applied: the client is out of sync with the
/// server and must reconnect.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Desync {
    /// `left` named an id that is not in the visible set.
    UnknownLeft(EntityId),
    /// `entered` named an id that is already in the visible set.
    AlreadyVisible(EntityId),
    /// A full state for an id that is not in the visible set.
    UnknownFull(EntityId),
    /// A delta whose index is outside the visible set.
    BadIndex(u8),
    /// A delta for an entity that has no baseline yet.
    NoBaseline(EntityId),
    /// An entity entered without a full state.
    MissingFull(EntityId),
    /// A `Cached` entry for an entity this connection was never sent in full
    /// at that appearance version (see [`crate::looks`]).
    UnknownLook(EntityId),
}

impl std::fmt::Display for Desync {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Desync::UnknownLeft(id) => write!(f, "entity {id} left but was not visible"),
            Desync::AlreadyVisible(id) => write!(f, "entity {id} entered twice"),
            Desync::UnknownFull(id) => write!(f, "full state for invisible entity {id}"),
            Desync::BadIndex(i) => write!(f, "delta index {i} is outside the visible set"),
            Desync::NoBaseline(id) => write!(f, "delta for entity {id} without a baseline"),
            Desync::MissingFull(id) => write!(f, "entity {id} entered without a full state"),
            Desync::UnknownLook(id) => {
                write!(f, "entity {id} entered with an appearance never sent")
            }
        }
    }
}

impl std::error::Error for Desync {}

/// The client end of the snapshot rules above: the visible set and each
/// member's baseline. Used by `sim-wasm`'s decoder (so the browser receives
/// absolute positions), the load-test bots and the server's own tests.
#[derive(Debug, Default, Clone)]
pub struct SnapshotReceiver {
    /// The visible set, sorted by id.
    ids: Vec<EntityId>,
    /// Baselines, parallel to `ids`; `None` between `entered` and its full state.
    base: Vec<Option<EntityState>>,
}

impl SnapshotReceiver {
    pub fn new() -> Self {
        Self::default()
    }

    /// Empties the visible set (on `Welcome` and `ZoneChanged`).
    pub fn reset(&mut self) {
        self.ids.clear();
        self.base.clear();
    }

    /// The visible set, sorted by id.
    pub fn visible(&self) -> &[EntityId] {
        &self.ids
    }

    /// The last state received for `id`, if it is visible.
    pub fn state(&self, id: EntityId) -> Option<&EntityState> {
        let i = self.ids.binary_search(&id).ok()?;
        self.base[i].as_ref()
    }

    /// Applies one snapshot and appends the new absolute state of every
    /// updated entity (full states first, then deltas) to `out`. On error the
    /// receiver is left part-way through the snapshot; reset it (reconnect).
    pub fn apply(
        &mut self,
        entered: impl IntoIterator<Item = EntityId>,
        full: &[EntityState],
        deltas: &[EntityDelta],
        left: &[EntityId],
        out: &mut Vec<EntityState>,
    ) -> Result<(), Desync> {
        for &id in left {
            let i = self
                .ids
                .binary_search(&id)
                .map_err(|_| Desync::UnknownLeft(id))?;
            self.ids.remove(i);
            self.base.remove(i);
        }
        for id in entered {
            match self.ids.binary_search(&id) {
                Ok(_) => return Err(Desync::AlreadyVisible(id)),
                Err(i) => {
                    self.ids.insert(i, id);
                    self.base.insert(i, None);
                }
            }
        }
        for s in full {
            let i = self
                .ids
                .binary_search(&s.id)
                .map_err(|_| Desync::UnknownFull(s.id))?;
            self.base[i] = Some(*s);
            out.push(*s);
        }
        for d in deltas {
            let i = usize::from(d.index);
            let slot = self.base.get_mut(i).ok_or(Desync::BadIndex(d.index))?;
            let base = slot.as_ref().ok_or(Desync::NoBaseline(self.ids[i]))?;
            let next = d.apply(base);
            *slot = Some(next);
            out.push(next);
        }
        if let Some(i) = self.base.iter().position(Option::is_none) {
            return Err(Desync::MissingFull(self.ids[i]));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state(id: EntityId, cm: [i32; 3], anim: Anim) -> EntityState {
        EntityState {
            id,
            pos: QPos {
                x: cm[0],
                y: cm[1],
                z: cm[2],
            },
            yaw: 7,
            anim,
        }
    }

    #[test]
    fn deltas_are_six_bytes_and_round_trip() {
        let base = state(70_001, [25_000, 1_250, -31_000], Anim::Walk);
        let next = state(70_001, [25_040, 1_250, -31_027], Anim::Run);
        let d = EntityDelta::between(3, &base, &next).unwrap();
        assert_eq!(d.apply(&base), next);
        assert_eq!(postcard::to_allocvec(&d).unwrap().len(), 6);
        // Over 1.27 m on one axis: a full state is needed.
        let far = state(70_001, [25_128, 1_250, -31_000], Anim::Run);
        assert!(EntityDelta::between(3, &base, &far).is_none());
        let back = state(70_001, [24_873, 1_250, -31_000], Anim::Run);
        assert_eq!(EntityDelta::between(3, &base, &back).unwrap().dx, -127);
    }

    /// Protocol v8: a flying state's delta counts 2 cm units, so a broom
    /// at the far tier's 5 Hz (up to 2.54 m an axis) still fits 6 bytes; the
    /// rounding lands within a centimetre, and a sender that keeps the
    /// applied state as its baseline never drifts from the receiver.
    #[test]
    fn flying_deltas_count_two_centimetres() {
        let base = state(70_001, [25_000, 1_250, -31_000], Anim::Fly);
        let far = state(70_001, [25_230, 1_251, -31_253], Anim::Fly);
        let d = EntityDelta::between(3, &base, &far).unwrap();
        assert_eq!((d.dx, d.dy, d.dz), (115, 1, -127));
        assert_eq!(postcard::to_allocvec(&d).unwrap().len(), 6);
        let got = d.apply(&base);
        assert_eq!(
            got.pos,
            QPos {
                x: 25_230,
                y: 1_252,
                z: -31_254
            }
        );
        // Walking, the same move is too far for a delta.
        let walk = state(70_001, [25_230, 1_251, -31_253], Anim::Run);
        assert!(EntityDelta::between(3, &base, &walk).is_none());
        // Beyond 2.54 m a flyer needs a full state too.
        let beyond = state(70_001, [25_256, 1_250, -31_000], Anim::Fly);
        assert!(EntityDelta::between(3, &base, &beyond).is_none());
        // Landing (a walking anim) counts centimetres again.
        let landed = state(70_001, [25_001, 1_250, -31_000], Anim::Idle);
        assert_eq!(EntityDelta::between(3, &base, &landed).unwrap().dx, 1);

        // A long flight, the sender keeping the applied state: the receiver
        // ends exactly at the sender's baseline and never more than a
        // centimetre an axis from the true position.
        let mut rx = SnapshotReceiver::new();
        let mut out = Vec::new();
        let mut truth = base;
        let mut baseline = base;
        rx.apply([truth.id], &[truth], &[], &[], &mut out).unwrap();
        let mut seed = 0x1234_5678_u32;
        for step in 0..2_000 {
            seed ^= seed << 13;
            seed ^= seed >> 17;
            seed ^= seed << 5;
            let r = |k: u32| ((seed >> k) % 461) as i32 - 230;
            truth.pos.x += r(0);
            truth.pos.y += r(9) / 4;
            truth.pos.z += r(18);
            truth.anim = if step % 50 == 49 {
                Anim::Hover
            } else {
                Anim::Fly
            };
            let d = EntityDelta::between(0, &baseline, &truth).expect("fits");
            baseline = d.apply(&baseline);
            out.clear();
            rx.apply([], &[], &[d], &[], &mut out).unwrap();
            assert_eq!(*rx.state(truth.id).unwrap(), baseline);
            for (a, b) in [
                (baseline.pos.x, truth.pos.x),
                (baseline.pos.y, truth.pos.y),
                (baseline.pos.z, truth.pos.z),
            ] {
                assert!((a - b).abs() <= 1, "{baseline:?} vs {truth:?}");
            }
        }
    }

    #[test]
    fn receiver_follows_enter_delta_and_leave() {
        let mut r = SnapshotReceiver::new();
        let mut out = Vec::new();
        let a = state(10, [0, 0, 0], Anim::Idle);
        let b = state(5, [100, 0, 100], Anim::Walk);
        r.apply([10, 5], &[a, b], &[], &[], &mut out).unwrap();
        assert_eq!(r.visible(), &[5, 10]);
        assert_eq!(out, vec![a, b]);

        // Index 1 is id 10 (sorted order).
        out.clear();
        let d = EntityDelta {
            index: 1,
            dx: 40,
            dy: 0,
            dz: -3,
            yaw: 9,
            anim: Anim::Walk,
        };
        r.apply([], &[], &[d], &[], &mut out).unwrap();
        assert_eq!(out[0].pos, QPos { x: 40, y: 0, z: -3 });
        assert_eq!(r.state(10).unwrap().yaw, 9);

        // 5 leaves and 20 enters: id 10 is now index 0.
        out.clear();
        let c = state(20, [1, 2, 3], Anim::Idle);
        let d = EntityDelta { index: 0, ..d };
        r.apply([20], &[c], &[d], &[5], &mut out).unwrap();
        assert_eq!(r.visible(), &[10, 20]);
        assert_eq!(out[1].pos, QPos { x: 80, y: 0, z: -6 });
    }

    #[test]
    fn receiver_detects_desync() {
        let mut r = SnapshotReceiver::new();
        let mut out = Vec::new();
        assert_eq!(
            r.apply([], &[], &[], &[3], &mut out),
            Err(Desync::UnknownLeft(3))
        );
        assert_eq!(
            r.apply([3], &[], &[], &[], &mut out),
            Err(Desync::MissingFull(3))
        );
        r.reset();
        let d = EntityDelta {
            index: 0,
            dx: 0,
            dy: 0,
            dz: 0,
            yaw: 0,
            anim: Anim::Idle,
        };
        assert_eq!(
            r.apply([], &[], &[d], &[], &mut out),
            Err(Desync::BadIndex(0))
        );
        let s = state(4, [0; 3], Anim::Idle);
        assert_eq!(
            r.apply([], &[s], &[], &[], &mut out),
            Err(Desync::UnknownFull(4))
        );
    }
}
