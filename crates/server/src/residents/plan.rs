//! The residents' routines planned against the castle: the NPC nav graphs
//! (`sim_core::npcs::NpcNavs`, the castle without its moving flights or
//! hidden passages, round the furniture of `data/world/keep_out.json`) and
//! every entity NPC's `RoutinePlan` (`NpcData::validate_castle`).
//!
//! Planning takes a few hundred milliseconds, so it happens once per
//! process and castle layout (the world's first tick; every test world over
//! the shipped castle shares it). A layout that is not the shipped castle
//! (the unit tests' small worlds) plans nobody: no station or routine point
//! of `data/npcs` is in it.

use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::sync::{Arc, LazyLock, Mutex};

use protocol::{Appearance, House};
use sim_core::Layout;
use sim_core::nav::KeepOut;
use sim_core::npcs::{NpcKind, NpcNavs, RoutinePlan};

use crate::catalog::shipped;

const KEEP_OUT_JSON: &str = include_str!("../../../../data/world/keep_out.json");

/// One entity NPC, planned.
#[derive(Debug)]
pub struct Planned {
    /// The roster id (`EntityInfo.npc`).
    pub id: String,
    pub kind: NpcKind,
    /// The nameplate (the client shows its own, by kind; none for students).
    pub name: String,
    /// The roster house; staff and shopkeepers have none and are sent with
    /// the first house (the client draws NPCs by their look and kind, not
    /// the house).
    pub house: House,
    pub appearance: Appearance,
    pub plan: RoutinePlan,
}

/// Every entity NPC of the roster, planned, in roster order.
#[derive(Debug, Default)]
pub struct Plans {
    pub npcs: Vec<Planned>,
}

impl Plans {
    pub fn get(&self, id: &str) -> Option<&Planned> {
        self.npcs.iter().find(|p| p.id == id)
    }
}

static CACHE: LazyLock<Mutex<HashMap<u64, Arc<Plans>>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

/// The furniture footprints NPCs walk round.
fn keep_out() -> Vec<KeepOut> {
    serde_json::from_str::<serde_json::Value>(KEEP_OUT_JSON)
        .ok()
        .and_then(|v| serde_json::from_value(v["keep_out"].clone()).ok())
        .unwrap_or_default()
}

/// A cheap fingerprint of a layout (its surfaces and interactables).
fn fingerprint(layout: &Layout) -> u64 {
    let mut h = std::collections::hash_map::DefaultHasher::new();
    layout.surfaces.len().hash(&mut h);
    for s in &layout.surfaces {
        s.id.hash(&mut h);
        for v in [s.min[0], s.min[1], s.max[0], s.max[1], s.y] {
            v.to_bits().hash(&mut h);
        }
    }
    layout.interactables.len().hash(&mut h);
    for i in &layout.interactables {
        i.id.hash(&mut h);
        for v in i.pos {
            v.to_bits().hash(&mut h);
        }
    }
    h.finish()
}

/// Whether `layout` could be the shipped castle: it has every shopkeeper's
/// station (a quick test before the expensive planning).
fn looks_like_the_castle(layout: &Layout) -> bool {
    let roster = &shipped().npcs.roster;
    roster
        .of_kind(NpcKind::Shopkeeper)
        .filter_map(|n| n.station.as_deref())
        .all(|st| layout.interactable(st).is_some())
        && roster.of_kind(NpcKind::Shopkeeper).next().is_some()
}

/// The plans for castle `layout` (planned on first use, then shared): empty
/// for a layout that is not the shipped castle.
pub fn plans_for(layout: &Layout) -> Arc<Plans> {
    let key = fingerprint(layout);
    let mut cache = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(p) = cache.get(&key) {
        return p.clone();
    }
    let plans = Arc::new(plan(layout));
    cache.insert(key, plans.clone());
    plans
}

fn plan(layout: &Layout) -> Plans {
    if !looks_like_the_castle(layout) {
        return Plans::default();
    }
    let content = shipped();
    let data = &content.npcs;
    let started = std::time::Instant::now();
    let navs = match NpcNavs::new(layout, &keep_out()) {
        Ok(n) => n,
        Err(e) => {
            tracing::warn!("residents: no NPC nav graph ({e}); nobody spawns");
            return Plans::default();
        }
    };
    let mut plans = match data.validate_castle(layout, &navs, &content.schedule) {
        Ok(p) => p,
        Err(e) => {
            tracing::warn!("residents: the routines do not fit this castle ({e}); nobody spawns");
            return Plans::default();
        }
    };
    let mut out = Vec::new();
    for n in data.roster.npcs.iter().filter(|n| n.kind.is_entity()) {
        let (Some(plan), Some(look)) = (plans.remove(&n.id), data.looks.get(&n.id)) else {
            continue;
        };
        out.push(Planned {
            id: n.id.clone(),
            kind: n.kind,
            name: n.name.clone(),
            house: n.house().unwrap_or(House::ALL[0]),
            appearance: *look,
            plan,
        });
    }
    tracing::info!(
        npcs = out.len(),
        ms = started.elapsed().as_millis() as u64,
        "residents planned"
    );
    Plans { npcs: out }
}

/// The plans for the shipped castle as the server loads it (tests, tools).
pub fn shipped_plans() -> Arc<Plans> {
    static SHIPPED: LazyLock<Option<Layout>> = LazyLock::new(|| {
        let path =
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/world/layout.json");
        crate::realtime::load_world(&path)
            .ok()
            .map(|w| w.layout().clone())
    });
    match SHIPPED.as_ref() {
        Some(l) => plans_for(l),
        None => Arc::new(Plans::default()),
    }
}
