//! Per-client snapshots (protocol v2, with v3 entries): diff the new visible
//! set against the last one sent, then encode `ServerMsg::Snapshot` straight
//! from the world's arrays, with states relative to what this client last
//! received.
//!
//! For every member of a client's visible set the client keeps a
//! [`Baseline`]: the last state it was sent. Each snapshot carries, per member
//! (docs/architecture.md, "Snapshots"):
//!
//! - nothing, if its state equals the baseline;
//! - a full `EntityState` when it entered, when the change does not fit an
//!   `EntityDelta` (more than 1.27 m on an axis), and as a resync once the
//!   baseline is `RESYNC_SNAPSHOTS` snapshots old;
//! - otherwise a 6-byte `EntityDelta` against the baseline. Far-tier members
//!   (see `visibility::NEAR_TIER`) are updated on every other snapshot only:
//!   a change is held back once, unless holding it would let the next delta
//!   overflow, and is always sent the snapshot after.
//!
//! Protocol v10: each member also has the look target this client was last
//! told (`Client::looks_sent`); when what it should see now
//! ([`shown_look`]) differs, the snapshot carries a `LookChange`.
//!
//! An entering member is sent as `EntityEntry::Full` (name, house and
//! appearance) the first time this connection is sent that look, and as a
//! 5-byte `EntityEntry::Cached` after that (`protocol::looks`;
//! `Client::looks` is this connection's record).
//!
//! Snapshots are built for hundreds of clients per tick on the snapshot worker
//! threads, so everything here works in thread-local scratch buffers that keep
//! their capacity. The only allocation per snapshot is the outgoing frame.
//!
//! Encoding goes through [`SnapshotFrame`], a borrowed mirror of
//! `ServerMsg::Snapshot` whose `Serialize` impl writes exactly what postcard
//! writes for the owned message (a test checks the bytes decode to it), so no
//! `EntityInfo` strings or `Vec`s are built just to be serialized.

use std::cell::RefCell;
use std::time::Instant;

use bytes::Bytes;
use protocol::{
    Appearance, EntityDelta, EntityId, EntityState, House, LookChange, RESYNC_SNAPSHOTS,
    VisibleEffect,
};
use serde::ser::{Serialize, SerializeSeq, SerializeStruct, SerializeStructVariant, Serializer};

use super::entities::{Baseline, Client, EntView};
use super::visibility::{Member, Query, Source, VisScratch, select_visible};

/// `ServerMsg::Snapshot`'s position among `ServerMsg`'s variants (postcard
/// encodes variants by index).
const SNAPSHOT_VARIANT: u32 = 1;

/// A far-tier change may be held back for a snapshot only while its delta is
/// at most this many centimetres on every axis: moving on at the same speed,
/// the next delta (twice as large) still fits an `i8`.
const HOLD_MAX_CM: i8 = 63;

/// Reusable buffers for one worker thread.
#[derive(Default)]
pub struct Scratch {
    vis: VisScratch,
    priority: Vec<EntityId>,
    next: Vec<Member>,
    next_base: Vec<Baseline>,
    /// Entering members: dense index, and whether the client already has
    /// their look (sent as `EntityEntry::Cached`).
    entered: Vec<(u32, bool)>,
    full: Vec<EntityState>,
    deltas: Vec<EntityDelta>,
    left: Vec<EntityId>,
    /// Protocol v10: what each member of the new set is shown looking at
    /// (parallel to `next`), and the changes to send.
    next_looks: Vec<Option<EntityId>>,
    looks: Vec<LookChange>,
    buf: Vec<u8>,
}

thread_local! {
    static SCRATCH: RefCell<Scratch> = RefCell::new(Scratch::default());
}

/// Runs `f` with this thread's scratch buffers.
pub fn with_scratch<R>(f: impl FnOnce(&mut Scratch) -> R) -> R {
    SCRATCH.with(|s| f(&mut s.borrow_mut()))
}

/// One client's snapshot.
#[derive(Debug, Default)]
pub struct Built {
    /// The encoded frame, `None` when nothing changed for this client.
    pub frame: Option<Bytes>,
    /// Full states and deltas it carries (for the metrics).
    pub full: u32,
    pub deltas: u32,
    /// Look changes it carries (protocol v10).
    pub looks: u32,
}

/// Updates `client`'s visible set and baselines and returns its snapshot.
/// `source` is where its zone's candidates come from; `zone_priority` who
/// everyone in its zone always sees (an instance's professor or duelists).
#[allow(clippy::too_many_arguments)]
pub fn build(
    view: EntView<'_>,
    source: Source<'_>,
    zone_priority: &[EntityId],
    viewer: usize,
    client: &mut Client,
    tick: u32,
    now: Instant,
    s: &mut Scratch,
) -> Built {
    client.priority.retain(|&(_, until)| until > now);
    s.priority.clear();
    s.priority.extend(client.priority.iter().map(|&(id, _)| id));
    s.priority.extend_from_slice(&client.online_friends);
    s.priority.extend_from_slice(zone_priority);
    s.priority.sort_unstable();
    s.priority.dedup();

    let q = Query {
        viewer,
        current: &client.visible,
        priority: &s.priority,
        group: &client.party,
        limit: usize::from(client.view_limit),
    };
    select_visible(view, source, &q, &mut s.vis, &mut s.next);

    // Merge-walk the old and new sets (both sorted by id). Position `b` in the
    // new set is the member's index in V once `left` and `entered` apply.
    s.entered.clear();
    s.full.clear();
    s.deltas.clear();
    s.left.clear();
    s.next_base.clear();
    s.next_looks.clear();
    s.looks.clear();
    let viewer_id = view.ids[viewer];
    let (old, new) = (&client.visible, &s.next);
    let (mut a, mut b) = (0, 0);
    while a < old.len() || b < new.len() {
        let o = old.get(a).copied().unwrap_or(EntityId::MAX);
        let n = new.get(b).map_or(EntityId::MAX, |m| m.id);
        if a < old.len() && (b == new.len() || o < n) {
            s.left.push(o);
            a += 1;
            continue;
        }
        // `select_visible` only returns live entities.
        let i = view.index_of(n).expect("visible entity is live");
        let state = view.state[i];
        if b < new.len() && (a == old.len() || n < o) {
            // `protocol::looks`: the whole entry the first time this
            // connection is sent this look, a cached one after that.
            let version = view.info[i].look_version();
            let cached = client.looks.has(n, version);
            if !cached {
                client.looks.record(n, version);
            }
            s.entered.push((i as u32, cached));
            s.full.push(state);
            s.next_base.push(Baseline {
                state,
                age: 0,
                skipped: false,
            });
            // An entering member looks at nobody until told otherwise.
            let look = shown_look(view, viewer_id, new, i);
            if look.is_some() {
                s.looks.push(LookChange {
                    id: n,
                    target: look,
                });
            }
            s.next_looks.push(look);
        } else {
            let base = client.base[a];
            let next = update(
                base,
                state,
                new[b].near,
                b as u8,
                &mut s.full,
                &mut s.deltas,
            );
            s.next_base.push(next);
            let look = shown_look(view, viewer_id, new, i);
            if client.looks_sent.get(a).copied().flatten() != look {
                s.looks.push(LookChange {
                    id: n,
                    target: look,
                });
            }
            s.next_looks.push(look);
            a += 1;
        }
        b += 1;
    }
    client.visible.clear();
    client.visible.extend(s.next.iter().map(|m| m.id));
    std::mem::swap(&mut client.base, &mut s.next_base);
    std::mem::swap(&mut client.looks_sent, &mut s.next_looks);

    let built = Built {
        frame: None,
        full: s.full.len() as u32,
        deltas: s.deltas.len() as u32,
        looks: s.looks.len() as u32,
    };
    if s.entered.is_empty()
        && s.full.is_empty()
        && s.deltas.is_empty()
        && s.left.is_empty()
        && s.looks.is_empty()
    {
        return built;
    }
    let frame = SnapshotFrame {
        tick,
        entered: Entered {
            entries: &s.entered,
            view,
        },
        full: &s.full,
        deltas: &s.deltas,
        left: &s.left,
        looks: &s.looks,
    };
    s.buf.clear();
    let buf = std::mem::take(&mut s.buf);
    let buf = postcard::to_extend(&frame, buf).expect("snapshot encodes");
    let bytes = Bytes::copy_from_slice(&buf);
    s.buf = buf;
    Built {
        frame: Some(bytes),
        ..built
    }
}

/// Protocol v10: whom the character at dense index `looker` is shown
/// looking at by viewer `viewer_id`, whose new visible set is `visible`
/// (sorted by id). Its target must still be live and either the viewer or
/// in the viewer's set (so a viewer never learns of a character it is not
/// shown), not under the Invisibility Draught (no nameplate: nobody gives
/// them away by looking), and within `LOOK_KEEP_M` of the looker.
#[inline]
pub fn shown_look(
    view: EntView<'_>,
    viewer_id: EntityId,
    visible: &[Member],
    looker: usize,
) -> Option<EntityId> {
    let t = view.look[looker]?;
    let j = view.index_of(t)?;
    if t != viewer_id && visible.binary_search_by_key(&t, |m| m.id).is_err() {
        return None;
    }
    if view.info[j]
        .effects
        .iter()
        .any(|e| matches!(e, VisibleEffect::NameplateHidden))
    {
        return None;
    }
    let (a, b) = (view.pos[looker], view.pos[j]);
    let d2 = (a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2);
    let keep = super::world::LOOK_KEEP_M;
    (d2 <= keep * keep).then_some(t)
}

/// What to send for a member that stays in the set (at `index`), and its new
/// baseline.
fn update(
    base: Baseline,
    state: EntityState,
    near: bool,
    index: u8,
    full: &mut Vec<EntityState>,
    deltas: &mut Vec<EntityDelta>,
) -> Baseline {
    let age = base.age.saturating_add(1);
    if u32::from(age) >= RESYNC_SNAPSHOTS {
        full.push(state);
        return Baseline {
            state,
            age: 0,
            skipped: false,
        };
    }
    if state == base.state {
        return Baseline {
            age,
            skipped: false,
            ..base
        };
    }
    let Some(delta) = EntityDelta::between(index, &base.state, &state) else {
        full.push(state);
        return Baseline {
            state,
            age: 0,
            skipped: false,
        };
    };
    // Protocol v8: a flying delta rounds to 2 cm units, so the receiver ends
    // at `sent`, not `state`; that is the baseline kept, and a change that
    // rounds away (a hovering flyer's centimetre) is no change.
    let sent = delta.apply(&base.state);
    if sent == base.state {
        return Baseline {
            age,
            skipped: false,
            ..base
        };
    }
    let unit = EntityDelta::unit_cm(delta.anim);
    let small = [delta.dx, delta.dy, delta.dz]
        .iter()
        .all(|d| i32::from(d.unsigned_abs()) * unit <= i32::from(HOLD_MAX_CM));
    if !near && !base.skipped && small {
        // Far tier: hold this change back one snapshot.
        return Baseline {
            age,
            skipped: true,
            ..base
        };
    }
    deltas.push(delta);
    Baseline {
        state: sent,
        age,
        skipped: false,
    }
}

/// Borrowed `ServerMsg::Snapshot`.
pub struct SnapshotFrame<'a> {
    pub tick: u32,
    pub entered: Entered<'a>,
    pub full: &'a [EntityState],
    pub deltas: &'a [EntityDelta],
    pub left: &'a [EntityId],
    pub looks: &'a [LookChange],
}

impl Serialize for SnapshotFrame<'_> {
    fn serialize<S: Serializer>(&self, ser: S) -> Result<S::Ok, S::Error> {
        let mut v = ser.serialize_struct_variant("ServerMsg", SNAPSHOT_VARIANT, "Snapshot", 6)?;
        v.serialize_field("tick", &self.tick)?;
        v.serialize_field("entered", &self.entered)?;
        v.serialize_field("full", self.full)?;
        v.serialize_field("deltas", self.deltas)?;
        v.serialize_field("left", self.left)?;
        v.serialize_field("looks", self.looks)?;
        v.end()
    }
}

/// The `entered` list as `EntityEntry`s read from the world's arrays: dense
/// indices, each with whether the client has the look already.
pub struct Entered<'a> {
    pub entries: &'a [(u32, bool)],
    pub view: EntView<'a>,
}

impl Serialize for Entered<'_> {
    fn serialize<S: Serializer>(&self, ser: S) -> Result<S::Ok, S::Error> {
        let mut seq = ser.serialize_seq(Some(self.entries.len()))?;
        for &(i, cached) in self.entries {
            let i = i as usize;
            let (id, info) = (self.view.ids[i], &self.view.info[i]);
            if cached {
                seq.serialize_element(&CachedRef {
                    id,
                    appearance_version: info.appearance_version,
                    effects_version: info.effects_version,
                })?;
            } else {
                seq.serialize_element(&FullRef(InfoRef {
                    id,
                    name: &info.name,
                    house: self.view.house[i],
                    appearance_version: info.appearance_version,
                    appearance: &info.appearance,
                    effects_version: info.effects_version,
                    effects: &info.effects,
                    level: info.level,
                    npc: info.npc.as_deref(),
                }))?;
            }
        }
        seq.end()
    }
}

/// Borrowed `EntityEntry::Full(EntityInfo)`.
struct FullRef<'a>(InfoRef<'a>);

impl Serialize for FullRef<'_> {
    fn serialize<S: Serializer>(&self, ser: S) -> Result<S::Ok, S::Error> {
        ser.serialize_newtype_variant("EntityEntry", 0, "Full", &self.0)
    }
}

/// `EntityEntry::Cached { id, appearance_version, effects_version }`.
struct CachedRef {
    id: EntityId,
    appearance_version: u32,
    effects_version: u32,
}

impl Serialize for CachedRef {
    fn serialize<S: Serializer>(&self, ser: S) -> Result<S::Ok, S::Error> {
        let mut v = ser.serialize_struct_variant("EntityEntry", 1, "Cached", 3)?;
        v.serialize_field("id", &self.id)?;
        v.serialize_field("appearance_version", &self.appearance_version)?;
        v.serialize_field("effects_version", &self.effects_version)?;
        v.end()
    }
}

/// Borrowed `EntityInfo { id, character: CharacterInfo { name, house },
/// appearance_version, appearance, effects_version, effects }`.
struct InfoRef<'a> {
    id: EntityId,
    name: &'a str,
    house: House,
    appearance_version: u32,
    appearance: &'a Appearance,
    effects_version: u32,
    effects: &'a [VisibleEffect],
    level: u8,
    npc: Option<&'a str>,
}

impl Serialize for InfoRef<'_> {
    fn serialize<S: Serializer>(&self, ser: S) -> Result<S::Ok, S::Error> {
        struct Character<'a>(&'a str, House);
        impl Serialize for Character<'_> {
            fn serialize<S: Serializer>(&self, ser: S) -> Result<S::Ok, S::Error> {
                let mut c = ser.serialize_struct("CharacterInfo", 2)?;
                c.serialize_field("name", self.0)?;
                c.serialize_field("house", &self.1)?;
                c.end()
            }
        }
        let mut e = ser.serialize_struct("EntityInfo", 8)?;
        e.serialize_field("id", &self.id)?;
        e.serialize_field("character", &Character(self.name, self.house))?;
        e.serialize_field("appearance_version", &self.appearance_version)?;
        e.serialize_field("appearance", self.appearance)?;
        e.serialize_field("effects_version", &self.effects_version)?;
        e.serialize_field("effects", self.effects)?;
        e.serialize_field("level", &self.level)?;
        e.serialize_field("npc", &self.npc)?;
        e.end()
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use protocol::{
        Anim, CharacterInfo, EntityEntry, EntityInfo, QPos, ServerMsg, SnapshotReceiver,
        decode_server,
    };
    use tokio::sync::mpsc;

    use super::*;
    use crate::realtime::ConnControl;
    use crate::realtime::entities::Entities;
    use crate::realtime::entities::tests::test_entity;
    use crate::realtime::grid::Grid;
    use crate::realtime::visibility::NEAR_TIER;

    fn client() -> Client {
        let (tx, _) = mpsc::channel(1);
        Client::new(tx, Arc::new(ConnControl::default()), Instant::now())
    }

    struct Decoded {
        tick: u32,
        entered: Vec<EntityEntry>,
        full: Vec<EntityState>,
        deltas: Vec<EntityDelta>,
        left: Vec<EntityId>,
        looks: Vec<LookChange>,
    }

    fn decode(b: &Bytes) -> Decoded {
        match decode_server(b).expect("decodes as a ServerMsg") {
            ServerMsg::Snapshot {
                tick,
                entered,
                full,
                deltas,
                left,
                looks,
            } => Decoded {
                tick,
                entered,
                full,
                deltas,
                left,
                looks,
            },
            other => panic!("not a snapshot: {other:?}"),
        }
    }

    fn snap(e: &Entities, g: &Grid, c: &mut Client, tick: u32) -> Option<Bytes> {
        with_scratch(|s| {
            build(
                e.view(),
                Source::Grid(g),
                &[],
                0,
                c,
                tick,
                Instant::now(),
                s,
            )
        })
        .frame
    }

    #[test]
    fn borrowed_encoding_matches_the_protocol() {
        let mut e = Entities::default();
        let now = Instant::now();
        let a = e
            .insert(test_entity("Viewer", [0.0; 3], 0, House::Burrowsby), now)
            .unwrap();
        let b = e
            .insert(
                test_entity("Émile Durand", [3.25, 0.0, -1.5], 0, House::Serpentyne),
                now,
            )
            .unwrap();
        let appearance = Appearance {
            body_type: 1,
            hair_style: 4,
            outfit: protocol::Outfit {
                hat: Some(protocol::Worn {
                    item_def: 101,
                    dyes: [0, 1, 0],
                    finish: protocol::Finish::Shimmer,
                }),
                ..Default::default()
            },
            ..Appearance::default()
        };
        e.info[1].appearance_version = 7;
        e.info[1].appearance = appearance;
        e.info[1].effects_version = 2;
        e.info[1].effects = vec![
            protocol::VisibleEffect::Glow { colour: 21 },
            protocol::VisibleEffect::Steam,
        ];
        // Fields: a level and an NPC tag.
        e.info[1].level = 4;
        e.info[1].npc = Some("charms_professor".into());
        let mut g = Grid::new([-10.0, -10.0], [10.0, 10.0]);
        g.rebuild(&e.pos);
        let mut c = client();
        let bytes = snap(&e, &g, &mut c, 2).unwrap();

        let want = ServerMsg::Snapshot {
            tick: 2,
            entered: vec![EntityEntry::Full(EntityInfo {
                id: b,
                character: CharacterInfo {
                    name: "Émile Durand".into(),
                    house: House::Serpentyne,
                },
                appearance_version: 7,
                appearance,
                effects_version: 2,
                effects: vec![
                    protocol::VisibleEffect::Glow { colour: 21 },
                    protocol::VisibleEffect::Steam,
                ],
                level: 4,
                npc: Some("charms_professor".into()),
            })],
            full: vec![EntityState {
                id: b,
                pos: QPos::from_m([3.25, 0.0, -1.5]),
                yaw: 0,
                anim: Anim::Idle,
            }],
            deltas: vec![],
            left: vec![],
            looks: vec![],
        };
        assert_eq!(decode_server(&bytes).unwrap(), want);
        assert_eq!(&bytes[..], &protocol::encode_server(&want)[..]);
        assert_eq!(c.visible, vec![b]);

        // Protocol v10: B looks at the viewer. The borrowed frame still
        // matches the owned message byte for byte.
        e.look[1] = Some(a);
        let bytes = snap(&e, &g, &mut c, 4).unwrap();
        let want = ServerMsg::Snapshot {
            tick: 4,
            entered: vec![],
            full: vec![],
            deltas: vec![],
            left: vec![],
            looks: vec![LookChange {
                id: b,
                target: Some(a),
            }],
        };
        assert_eq!(&bytes[..], &protocol::encode_server(&want)[..]);
    }

    /// Protocol v10: a look reaches a viewer once per change, on entering
    /// too, and only while the viewer may see it: the target in its set (or
    /// itself), not invisible, within range.
    #[test]
    fn looks_go_out_on_change_and_never_leak() {
        let mut e = Entities::default();
        let now = Instant::now();
        let viewer = e
            .insert(test_entity("V", [0.0; 3], 0, House::Roarwing), now)
            .unwrap();
        let b = e
            .insert(test_entity("B", [1.0, 0.0, 0.0], 0, House::Roarwing), now)
            .unwrap();
        let c_id = e
            .insert(test_entity("C", [2.0, 0.0, 0.0], 0, House::Roarwing), now)
            .unwrap();
        let far = e
            .insert(test_entity("F", [60.0, 0.0, 0.0], 0, House::Roarwing), now)
            .unwrap();
        let (bi, ci, fi) = (
            e.index_of(b).unwrap(),
            e.index_of(c_id).unwrap(),
            e.index_of(far).unwrap(),
        );
        // B already looks at C when the viewer first sees them: the
        // entering snapshot says so.
        e.look[bi] = Some(c_id);
        let mut g = Grid::new([-100.0, -100.0], [100.0, 100.0]);
        g.rebuild(&e.pos);
        let mut c = client();
        // The viewer sees two characters: B and C (F is farther).
        c.view_limit = 2;
        let d = decode(&snap(&e, &g, &mut c, 2).unwrap());
        assert_eq!(c.visible, vec![b, c_id]);
        assert_eq!(
            d.looks,
            vec![LookChange {
                id: b,
                target: Some(c_id)
            }]
        );
        // Unchanged: nothing more.
        assert!(snap(&e, &g, &mut c, 4).is_none());

        // B turns to the viewer: one change.
        e.look[bi] = Some(viewer);
        let d = decode(&snap(&e, &g, &mut c, 6).unwrap());
        assert_eq!(
            d.looks,
            vec![LookChange {
                id: b,
                target: Some(viewer)
            }]
        );
        assert!(d.full.is_empty() && d.deltas.is_empty());

        // C looks at F, whom the viewer is not shown: the viewer sees C look
        // at nobody (no change from what it had), so nothing is sent.
        e.look[ci] = Some(far);
        assert!(snap(&e, &g, &mut c, 8).is_none());

        // The viewer drinks nothing, but C drinks the Invisibility Draught:
        // B's look at C is hidden from everyone.
        e.look[bi] = Some(c_id);
        e.info[ci].effects = vec![VisibleEffect::NameplateHidden];
        let d = decode(&snap(&e, &g, &mut c, 10).unwrap());
        assert_eq!(
            d.looks,
            vec![LookChange {
                id: b,
                target: None
            }]
        );
        // It wears off: shown again.
        e.info[ci].effects.clear();
        let d = decode(&snap(&e, &g, &mut c, 12).unwrap());
        assert_eq!(
            d.looks,
            vec![LookChange {
                id: b,
                target: Some(c_id)
            }]
        );

        // C walks off beyond the keep distance (still in view): no look.
        e.pos[ci] = [1.0 + super::super::world::LOOK_KEEP_M + 1.0, 0.0, 0.0];
        let d = decode(&snap(&e, &g, &mut c, 14).unwrap());
        assert_eq!(
            d.looks,
            vec![LookChange {
                id: b,
                target: None
            }]
        );

        // A target that left the world no longer resolves.
        e.pos[ci] = [2.0, 0.0, 0.0];
        e.look[bi] = Some(c_id);
        let d = decode(&snap(&e, &g, &mut c, 16).unwrap());
        assert_eq!(d.looks.len(), 1);
        e.remove(far).unwrap();
        e.remove(c_id).unwrap();
        g.rebuild(&e.pos);
        let d = decode(&snap(&e, &g, &mut c, 18).unwrap());
        assert_eq!(d.left, vec![c_id]);
        assert_eq!(
            d.looks,
            vec![LookChange {
                id: b,
                target: None
            }]
        );
        assert_eq!(c.looks_sent, vec![None]);
        let _ = fi;
    }

    #[test]
    fn changes_go_out_as_deltas_and_only_once() {
        let mut e = Entities::default();
        let now = Instant::now();
        let _viewer = e
            .insert(test_entity("V", [0.0; 3], 0, House::Roarwing), now)
            .unwrap();
        let b = e
            .insert(test_entity("B", [1.0, 0.0, 0.0], 0, House::Roarwing), now)
            .unwrap();
        let c_id = e
            .insert(test_entity("C", [2.0, 0.0, 0.0], 0, House::Roarwing), now)
            .unwrap();
        let mut g = Grid::new([-10.0, -10.0], [10.0, 10.0]);
        g.rebuild(&e.pos);
        let mut c = client();

        let d = decode(&snap(&e, &g, &mut c, 2).unwrap());
        assert_eq!(d.entered.len(), 2);
        assert_eq!(
            d.full.len(),
            2,
            "newly entered entities always carry a state"
        );
        assert!(d.left.is_empty() && d.deltas.is_empty());

        // Nothing moved: no snapshot at all.
        assert!(snap(&e, &g, &mut c, 4).is_none());

        // B moves 50 cm: a delta at index 0 (B sorts before C).
        let bi = e.index_of(b).unwrap();
        e.state[bi].pos = QPos::from_m([1.5, 0.0, 0.0]);
        e.state[bi].anim = Anim::Walk;
        let d = decode(&snap(&e, &g, &mut c, 6).unwrap());
        assert_eq!(d.tick, 6);
        assert!(d.entered.is_empty() && d.full.is_empty());
        assert_eq!(
            d.deltas,
            vec![EntityDelta {
                index: 0,
                dx: 50,
                dy: 0,
                dz: 0,
                yaw: 0,
                anim: Anim::Walk
            }]
        );
        // Not again.
        assert!(snap(&e, &g, &mut c, 8).is_none());

        // A jump of 2 m does not fit a delta: a full state.
        e.state[bi].pos = QPos::from_m([3.5, 0.0, 0.0]);
        let d = decode(&snap(&e, &g, &mut c, 10).unwrap());
        assert_eq!(d.full.iter().map(|s| s.id).collect::<Vec<_>>(), vec![b]);
        assert!(d.deltas.is_empty());

        // C leaves.
        e.remove(c_id);
        g.rebuild(&e.pos);
        let d = decode(&snap(&e, &g, &mut c, 12).unwrap());
        assert!(d.full.is_empty() && d.deltas.is_empty());
        assert_eq!(d.left, vec![c_id]);
        assert_eq!(c.visible, vec![b]);
    }

    #[test]
    fn a_look_is_sent_once_per_connection_and_version() {
        let mut e = Entities::default();
        let now = Instant::now();
        e.insert(test_entity("V", [0.0; 3], 0, House::Roarwing), now);
        let b = e
            .insert(test_entity("B", [1.0, 0.0, 0.0], 0, House::Skytalon), now)
            .unwrap();
        let bi = e.index_of(b).unwrap();
        e.info[bi].appearance_version = 3;
        let mut g = Grid::new([-50.0, -50.0], [50.0, 50.0]);
        g.rebuild(&e.pos);
        let mut c = client();
        let mut looks = protocol::LookCache::new();
        let mut entries = |c: &mut Client, e: &Entities, g: &Grid, tick| {
            let d = decode(&snap(e, g, c, tick).unwrap());
            let mut infos = Vec::new();
            looks.resolve(&d.entered, &mut infos).expect("resolves");
            (d.entered, infos)
        };
        let (first, infos) = entries(&mut c, &e, &g, 2);
        assert!(matches!(&first[..], [EntityEntry::Full(i)] if i.id == b));
        assert_eq!(infos[0].character.name, "B");

        // B walks out of view and back: the entry is cached, and the client
        // still knows who it is.
        let far = [40.0, 0.0, 40.0];
        e.pos[bi] = far;
        e.state[bi].pos = QPos::from_m(far);
        c.view_limit = 0;
        g.rebuild(&e.pos);
        assert_eq!(decode(&snap(&e, &g, &mut c, 4).unwrap()).left, vec![b]);
        c.view_limit = 50;
        let (again, infos) = entries(&mut c, &e, &g, 6);
        assert_eq!(
            again,
            vec![EntityEntry::Cached {
                id: b,
                appearance_version: 3,
                effects_version: 0,
            }]
        );
        assert_eq!(infos[0].character.name, "B");

        // A zone change resets the visible set but not the record; a new
        // look is sent in full again.
        c.reset_view();
        e.info[bi].appearance_version = 4;
        let (changed, infos) = entries(&mut c, &e, &g, 8);
        assert!(matches!(&changed[..], [EntityEntry::Full(i)] if i.appearance_version == 4));
        assert_eq!(infos[0].appearance_version, 4);

        // New potion effects are a new look too.
        c.reset_view();
        e.info[bi].effects_version = 1;
        e.info[bi].effects = vec![protocol::VisibleEffect::NameplateHidden];
        let (changed, infos) = entries(&mut c, &e, &g, 10);
        assert!(matches!(&changed[..], [EntityEntry::Full(i)] if i.effects_version == 1));
        assert_eq!(
            infos[0].effects,
            vec![protocol::VisibleEffect::NameplateHidden]
        );
    }

    #[test]
    fn idle_members_are_resynced_every_hundred_snapshots() {
        let mut e = Entities::default();
        let now = Instant::now();
        e.insert(test_entity("V", [0.0; 3], 0, House::Roarwing), now);
        let b = e
            .insert(test_entity("B", [1.0, 0.0, 0.0], 0, House::Roarwing), now)
            .unwrap();
        let mut g = Grid::new([-10.0, -10.0], [10.0, 10.0]);
        g.rebuild(&e.pos);
        let mut c = client();
        snap(&e, &g, &mut c, 0).unwrap();
        let mut resyncs = Vec::new();
        for n in 1..=250u32 {
            if let Some(f) = snap(&e, &g, &mut c, n) {
                let d = decode(&f);
                assert_eq!(d.full.iter().map(|s| s.id).collect::<Vec<_>>(), vec![b]);
                resyncs.push(n);
            }
        }
        assert_eq!(resyncs, vec![RESYNC_SNAPSHOTS, 2 * RESYNC_SNAPSHOTS]);
    }

    /// A crowd walking for many snapshots: the client end
    /// (`SnapshotReceiver`) always agrees with the world, near members are
    /// updated every snapshot, far ones at least every other one.
    #[test]
    fn deltas_keep_the_receiver_exact_and_tiers_hold() {
        let mut e = Entities::default();
        let now = Instant::now();
        let n = 60;
        for k in 0..n {
            let p = [(k % 10) as f32 * 1.5, 0.0, (k / 10) as f32 * 1.5];
            e.insert(test_entity(&format!("p{k}"), p, 0, House::Skytalon), now);
        }
        let mut g = Grid::new([-50.0, -50.0], [50.0, 50.0]);
        let mut c = client();
        c.view_limit = 50;
        let mut rx = SnapshotReceiver::new();
        let mut seed = 0x2545_f491_u32;
        let mut rand = move || {
            seed ^= seed << 13;
            seed ^= seed >> 17;
            seed ^= seed << 5;
            seed
        };
        // Snapshots since each entity's state was last received.
        let mut lag: std::collections::HashMap<EntityId, u32> = Default::default();
        let (mut fulls, mut deltas) = (0, 0);
        for tick in 0..400u32 {
            for k in 1..n {
                // Everyone wanders; a few sprint (over 1.27 m per snapshot).
                // Every fifth one flies, up to 2.3 m a snapshot
                // and up and down, or hovers in place (2 cm delta units).
                let flies = k % 5 == 0;
                let step = match (flies, rand() % 40) {
                    (false, 0) => 1.4,
                    (false, 1..=20) => 0.4,
                    (true, 0..=9) => 2.3,
                    (true, 10..=25) => 0.73,
                    _ => 0.0,
                };
                let ang = (rand() % 628) as f32 / 100.0;
                e.pos[k][0] += step * ang.cos();
                e.pos[k][2] += step * ang.sin();
                if flies {
                    e.pos[k][1] += ((rand() % 61) as f32 - 30.0) / 100.0;
                }
                e.state[k].pos = QPos::from_m(e.pos[k]);
                e.state[k].yaw = (rand() % 256) as u8;
                e.state[k].anim = match (flies, step > 0.0) {
                    (true, true) => Anim::Fly,
                    (true, false) => Anim::Hover,
                    (false, true) => Anim::Walk,
                    (false, false) => Anim::Idle,
                };
            }
            g.rebuild(&e.pos);
            let built =
                with_scratch(|s| build(e.view(), Source::Grid(&g), &[], 0, &mut c, tick, now, s));
            fulls += built.full;
            deltas += built.deltas;
            if let Some(f) = built.frame {
                let d = decode(&f);
                let mut out = Vec::new();
                rx.apply(
                    d.entered.iter().map(EntityEntry::id),
                    &d.full,
                    &d.deltas,
                    &d.left,
                    &mut out,
                )
                .expect("in sync");
                for s in &out {
                    lag.insert(s.id, 0);
                }
            }
            assert_eq!(rx.visible(), &c.visible[..]);
            // Every member's received state is the baseline the server keeps,
            // and at most one snapshot behind the world (held back in the far
            // tier); near members are never held back. A flyer's is within a
            // centimetre of the world's (2 cm units).
            let mut held = 0;
            for (k, &id) in c.visible.iter().enumerate() {
                let i = e.index_of(id).unwrap();
                let got = rx.state(id).unwrap();
                assert_eq!(*got, c.base[k].state);
                let w = &e.state[i];
                let current = got.yaw == w.yaw
                    && got.anim == w.anim
                    && [
                        (got.pos.x, w.pos.x),
                        (got.pos.y, w.pos.y),
                        (got.pos.z, w.pos.z),
                    ]
                    .iter()
                    .all(|&(a, b)| (a - b).abs() <= i32::from(w.anim.is_flying()));
                if !current {
                    let l = lag.entry(id).or_insert(0);
                    *l += 1;
                    assert!(*l <= 1, "entity {id} held back twice");
                    assert!(c.base[k].skipped);
                    held += 1;
                } else {
                    lag.insert(id, 0);
                }
            }
            assert!(held <= c.visible.len().saturating_sub(NEAR_TIER));
        }
        assert!(deltas > 5 * fulls, "{deltas} deltas, {fulls} full states");
    }
}
