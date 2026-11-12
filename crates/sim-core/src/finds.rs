//! Exploration finds: hidden spots in the castle that reward
//! getting a little lost.
//!
//! - **Spots** are layout interactables of kind `find_spot`
//!   (`data/world/layout.json`: behind tapestries, under staircases, in
//!   alcoves, atop towers, in the dungeons). `data/world/finds.json`
//!   ([`Finds`]) gives each one a loot table and sets the per-character
//!   cooldown (once per real day per spot).
//! - **Loot tables** are `data/world/loot.json` ([`LootTables`]): weighted
//!   entries of an item (with a quantity range), Crowns, or nothing.
//!   Fortune's Draught adds rolls and makes `rare` entries likelier
//!   (`EffectRules.fortunes_draught`).
//!
//! Rolling is pure: the caller passes the random numbers ([`LootTable::roll`]),
//! so tests are deterministic and the rule is the same everywhere.

use std::collections::{BTreeMap, HashSet};

use serde::{Deserialize, Serialize};

use crate::catalog::Catalog;
use crate::layout::{InteractableKind, Layout};

pub const FINDS_VERSION: u32 = 1;
pub const LOOT_VERSION: u32 = 1;

#[derive(Debug, thiserror::Error)]
pub enum FindsError {
    #[error("finds JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("finds are inconsistent: {0}")]
    Invalid(String),
}

fn fail<T>(m: String) -> Result<T, FindsError> {
    Err(FindsError::Invalid(m))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FindSpot {
    /// The `find_spot` interactable's id in the castle layout (or
    /// in an instance layout, the Lost Property Vault's daily find).
    pub id: String,
    /// A `loot.json` table id.
    pub loot: String,
}

/// `data/world/finds.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Finds {
    pub version: u32,
    /// Per character and spot: how long after a search the spot can be
    /// searched again (seconds).
    pub cooldown_secs: u32,
    pub spots: Vec<FindSpot>,
}

impl Finds {
    pub fn from_json(json: &str) -> Result<Finds, FindsError> {
        Ok(serde_json::from_str(json)?)
    }

    /// Every `find_spot` interactable of `layouts` (the castle's and the
    /// instance layouts that hold find spots) has exactly one spot
    /// here and every spot names one, with a loot table that exists.
    pub fn validate(&self, layouts: &[&Layout], loot: &LootTables) -> Result<(), FindsError> {
        if self.version != FINDS_VERSION || self.cooldown_secs == 0 {
            return fail("unsupported finds version or zero cooldown".into());
        }
        let in_layout: HashSet<&str> = layouts
            .iter()
            .flat_map(|l| l.interactables.iter())
            .filter(|i| i.kind == InteractableKind::FindSpot)
            .map(|i| i.id.as_str())
            .collect();
        let mut seen = HashSet::new();
        for s in &self.spots {
            if !seen.insert(s.id.as_str()) {
                return fail(format!("spot {:?} listed twice", s.id));
            }
            if !in_layout.contains(s.id.as_str()) {
                return fail(format!("spot {:?} is not a find_spot in the layout", s.id));
            }
            if loot.get(&s.loot).is_none() {
                return fail(format!("spot {:?}: unknown loot table {:?}", s.id, s.loot));
            }
        }
        if let Some(missing) = in_layout.iter().find(|id| !seen.contains(*id)) {
            return fail(format!("find_spot {missing:?} has no entry in finds.json"));
        }
        Ok(())
    }

    pub fn spot(&self, id: &str) -> Option<&FindSpot> {
        self.spots.iter().find(|s| s.id == id)
    }
}

/// One weighted outcome of a loot roll: an item, Crowns, or (neither) nothing.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LootEntry {
    pub weight: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub item: Option<u16>,
    /// Inclusive range of units (items only).
    #[serde(default = "one_one")]
    pub quantity: [u32; 2],
    /// Inclusive range of Crowns.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub crowns: Option<[u32; 2]>,
    /// Fortune's Draught multiplies this entry's weight.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub rare: bool,
}

fn one_one() -> [u32; 2] {
    [1, 1]
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LootTable {
    pub id: String,
    /// Rolls per search (Fortune's Draught adds more).
    pub rolls: u8,
    pub entries: Vec<LootEntry>,
}

/// How lucky the searcher is (Fortune's Draught, `EffectRules.fortunes_draught`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Luck {
    pub extra_rolls: u8,
    /// Weight of `rare` entries in percent (100 = unchanged).
    pub rare_weight_pct: u16,
}

/// What a search found, merged by item (sorted by item id).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Drops {
    pub items: Vec<(u16, u32)>,
    pub crowns: u32,
}

impl Drops {
    pub fn is_empty(&self) -> bool {
        self.items.is_empty() && self.crowns == 0
    }
}

impl LootTable {
    fn weight(e: &LootEntry, luck: Option<Luck>) -> u64 {
        let w = u64::from(e.weight);
        match luck {
            Some(l) if e.rare => w * u64::from(l.rare_weight_pct) / 100,
            _ => w,
        }
    }

    /// Rolls the table: `rolls` times (plus `luck.extra_rolls`), each roll
    /// picking one entry by weight, then a quantity or Crown amount in its
    /// range. `rng` returns uniformly random `u32`s.
    pub fn roll(&self, luck: Option<Luck>, rng: &mut dyn FnMut() -> u32) -> Drops {
        let total: u64 = self.entries.iter().map(|e| Self::weight(e, luck)).sum();
        let rolls = u32::from(self.rolls) + u32::from(luck.map_or(0, |l| l.extra_rolls));
        let mut items: BTreeMap<u16, u32> = BTreeMap::new();
        let mut crowns = 0u32;
        let pick = |rng: &mut dyn FnMut() -> u32, [lo, hi]: [u32; 2]| {
            let span = u64::from(hi - lo) + 1;
            lo + (u64::from(rng()) % span) as u32
        };
        for _ in 0..rolls {
            if total == 0 {
                break;
            }
            let mut r = u64::from(rng()) % total;
            let Some(e) = self.entries.iter().find(|e| {
                let w = Self::weight(e, luck);
                if r < w {
                    true
                } else {
                    r -= w;
                    false
                }
            }) else {
                continue;
            };
            if let Some(item) = e.item {
                let n = pick(rng, e.quantity);
                let slot = items.entry(item).or_default();
                *slot = slot.saturating_add(n);
            }
            if let Some(range) = e.crowns {
                crowns = crowns.saturating_add(pick(rng, range));
            }
        }
        Drops {
            items: items.into_iter().collect(),
            crowns,
        }
    }
}

/// `data/world/loot.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LootTables {
    pub version: u32,
    pub tables: Vec<LootTable>,
}

impl LootTables {
    pub fn from_json(json: &str, catalog: &Catalog) -> Result<LootTables, FindsError> {
        let t: LootTables = serde_json::from_str(json)?;
        t.validate(catalog)?;
        Ok(t)
    }

    /// Unique ids; at least one roll; positive weights; known items whose
    /// stacks hold the largest quantity; ordered ranges.
    pub fn validate(&self, catalog: &Catalog) -> Result<(), FindsError> {
        if self.version != LOOT_VERSION {
            return fail(format!("unsupported loot version {}", self.version));
        }
        let mut ids = HashSet::new();
        for t in &self.tables {
            let what = format!("loot table {:?}", t.id);
            if !ids.insert(t.id.as_str()) || t.rolls == 0 || t.entries.is_empty() {
                return fail(format!("{what}: duplicate id, no rolls or no entries"));
            }
            for e in &t.entries {
                if e.weight == 0 || e.quantity[0] == 0 || e.quantity[0] > e.quantity[1] {
                    return fail(format!("{what}: zero weight or a bad quantity range"));
                }
                if let Some(item) = e.item {
                    let Some(def) = catalog.get(item) else {
                        return fail(format!("{what}: unknown item {item}"));
                    };
                    if e.quantity[1] > def.stack_limit() {
                        return fail(format!(
                            "{what}: item {item} does not stack to {}",
                            e.quantity[1]
                        ));
                    }
                }
                if let Some([lo, hi]) = e.crowns
                    && (lo == 0 || lo > hi)
                {
                    return fail(format!("{what}: bad Crown range"));
                }
            }
        }
        Ok(())
    }

    pub fn get(&self, id: &str) -> Option<&LootTable> {
        self.tables.iter().find(|t| t.id == id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::{Catalog, Palette};

    const PALETTE: &str = include_str!("../../../data/items/palette.json");
    const CATALOG: &str = include_str!("../../../data/items/catalog.json");
    const LAYOUT: &str = include_str!("../../../data/world/layout.json");
    const FINDS: &str = include_str!("../../../data/world/finds.json");
    const LOOT: &str = include_str!("../../../data/world/loot.json");
    const LOST_PROPERTY: &str =
        include_str!("../../../data/world/instances/lost_property_vault.json");

    fn shipped() -> (Vec<Layout>, Finds, LootTables) {
        let palette = Palette::from_json(PALETTE).unwrap();
        let catalog = Catalog::from_json(CATALOG, &palette).unwrap();
        let loot = LootTables::from_json(LOOT, &catalog).unwrap();
        let layouts = vec![
            Layout::from_json(LAYOUT).unwrap(),
            Layout::from_json(LOST_PROPERTY).unwrap(),
        ];
        let finds = Finds::from_json(FINDS).unwrap();
        finds
            .validate(&layouts.iter().collect::<Vec<_>>(), &loot)
            .unwrap();
        // The Lost Property Vault's heap is a spot of its own.
        assert!(finds.spot("find_lost_property_vault").is_some());
        assert!(
            finds
                .validate(&[&layouts[0]], &loot)
                .unwrap_err()
                .to_string()
                .contains("find_lost_property_vault")
        );
        (layouts, finds, loot)
    }

    /// A small deterministic generator for the tests.
    fn rng(seed: u32) -> impl FnMut() -> u32 {
        let mut s = seed.max(1);
        move || {
            s ^= s << 13;
            s ^= s >> 17;
            s ^= s << 5;
            s
        }
    }

    #[test]
    fn the_shipped_finds_cover_the_castle() {
        let (mut layouts, finds, _) = shipped();
        let layout = layouts.swap_remove(0);
        assert!(finds.spots.len() >= 30, "{} spots", finds.spots.len());
        assert_eq!(finds.cooldown_secs, 24 * 3600);
        // Spread over many floors and places.
        let world = crate::World::new(layout).unwrap();
        let levels: HashSet<i32> = world
            .interactables()
            .iter()
            .filter(|i| i.kind == InteractableKind::FindSpot)
            .filter_map(|i| world.level_at(i.pos[0], i.pos[2], i.pos[1]))
            .collect();
        assert!(levels.len() >= 8, "levels {levels:?}");
    }

    /// Every class file (`data/classes/<id>.json`); the test below checks the
    /// list against the timetable, so a new class cannot be missed.
    const CLASSES: [&str; 10] = [
        include_str!("../../../data/classes/charms.json"),
        include_str!("../../../data/classes/potions.json"),
        include_str!("../../../data/classes/transfiguration.json"),
        include_str!("../../../data/classes/defence.json"),
        include_str!("../../../data/classes/history.json"),
        include_str!("../../../data/classes/divination.json"),
        include_str!("../../../data/classes/astronomy.json"),
        include_str!("../../../data/classes/herbology.json"),
        include_str!("../../../data/classes/creatures.json"),
        include_str!("../../../data/classes/flying.json"),
    ];

    /// Every catalog item can be had (crafting has no dead ends, no item is
    /// content nobody can reach). Walked to a fixed point from what creates
    /// items: the starter grant (any house), the owl-order shop, the loot
    /// tables of the find spots, class rewards and the House Cup reward;
    /// then crafting, which makes a recipe's output once the recipe can be
    /// learned (no pattern, an obtainable pattern, or a class or secret
    /// that teaches it) and every input is obtainable, a restyle also
    /// needing another member of its family. A pattern item itself must
    /// have a source of its own: a class teaching its recipe does not make
    /// the card obtainable, and the shop alone is not enough (every pattern
    /// drops from a find or is a class reward). Trades, mail and the
    /// Exchange only move items.
    #[test]
    fn every_catalog_item_is_obtainable() {
        let (_, finds, loot) = shipped();
        let palette = Palette::from_json(PALETTE).unwrap();
        let catalog = Catalog::from_json(CATALOG, &palette).unwrap();
        let recipes = crate::Recipes::from_json(
            include_str!("../../../data/crafting/recipes.json"),
            &catalog,
        )
        .unwrap();
        let schedule =
            crate::Schedule::from_json(include_str!("../../../data/classes/schedule.json"))
                .unwrap();
        let classes: Vec<crate::ClassDef> = CLASSES
            .iter()
            .map(|j| crate::ClassDef::from_json(j).unwrap())
            .collect();
        let read: HashSet<&str> = classes.iter().map(|c| c.id.as_str()).collect();
        let scheduled: HashSet<&str> = schedule.classes.iter().map(|c| c.id.as_str()).collect();
        assert_eq!(read, scheduled, "every scheduled class is read");
        let secrets: crate::SecretsFile =
            serde_json::from_str(include_str!("../../../data/world/secrets.json")).unwrap();
        let cup = crate::CupRules::from_json(include_str!("../../../data/house_cup.json")).unwrap();

        // Item -> how it is first had.
        let mut had: BTreeMap<u16, String> = BTreeMap::new();
        for item in catalog.items.iter().filter(|i| !i.retired) {
            if protocol::House::ALL.iter().any(|&h| item.is_starter_for(h)) {
                had.entry(item.id).or_insert_with(|| "starter".into());
            }
            if item.in_shop() {
                had.entry(item.id).or_insert_with(|| "shop".into());
            }
        }
        let searched: HashSet<&str> = finds.spots.iter().map(|s| s.loot.as_str()).collect();
        for t in loot
            .tables
            .iter()
            .filter(|t| searched.contains(t.id.as_str()))
        {
            for item in t.entries.iter().filter_map(|e| e.item) {
                had.entry(item).or_insert_with(|| format!("find:{}", t.id));
            }
        }
        for c in &classes {
            for r in &c.rewards.items {
                had.entry(r.item)
                    .or_insert_with(|| format!("class:{}", c.id));
            }
        }
        had.entry(cup.reward.item)
            .or_insert_with(|| "house_cup".into());
        // Every pattern can also be found or earned: one the owl-order shop
        // sells must still drop from a find spot (as the basic ones do in
        // the Lost Property Vault) or be a class reward.
        let found: HashSet<u16> = loot
            .tables
            .iter()
            .filter(|t| searched.contains(t.id.as_str()))
            .flat_map(|t| t.entries.iter().filter_map(|e| e.item))
            .chain(
                classes
                    .iter()
                    .flat_map(|c| c.rewards.items.iter().map(|r| r.item)),
            )
            .collect();
        let unfound: Vec<String> = catalog
            .items
            .iter()
            .filter(|i| !i.retired && i.kind == crate::ItemKind::Pattern && !found.contains(&i.id))
            .map(|i| format!("{} ({})", i.id, i.name))
            .collect();
        assert!(
            unfound.is_empty(),
            "patterns no find or class gives: {unfound:?}"
        );
        let taught: HashSet<u16> = classes
            .iter()
            .flat_map(|c| c.rewards.teaches.iter().map(|t| t.recipe))
            .chain(secrets.secrets.iter().filter_map(|s| s.reward.recipe))
            .collect();
        for &r in &taught {
            assert!(recipes.get(r).is_some(), "taught recipe {r} does not exist");
        }
        loop {
            let mut grew = false;
            for r in &recipes.recipes {
                let out = r.output.item;
                if had.contains_key(&out) {
                    continue;
                }
                let learnable =
                    r.pattern.is_none_or(|p| had.contains_key(&p)) || taught.contains(&r.id);
                let inputs = r.inputs.iter().all(|i| had.contains_key(&i.item));
                let garment = r.kind != crate::RecipeKind::Restyle
                    || r.family.as_deref().is_some_and(|f| {
                        catalog
                            .family(f)
                            .any(|m| m.id != out && had.contains_key(&m.id))
                    });
                if learnable && inputs && garment {
                    had.insert(out, format!("recipe:{}", r.id));
                    grew = true;
                }
            }
            if !grew {
                break;
            }
        }
        let missing: Vec<String> = catalog
            .items
            .iter()
            .filter(|i| !i.retired && !had.contains_key(&i.id))
            .map(|i| format!("{} ({:?}, {})", i.id, i.kind, i.name))
            .collect();
        assert!(missing.is_empty(), "never obtainable: {missing:?}");
        // Every kind the catalog has was walked.
        let kinds: HashSet<_> = catalog.items.iter().map(|i| i.kind).collect();
        assert!(kinds.len() >= 7, "kinds {kinds:?}");
    }

    #[test]
    fn rolls_follow_the_weights_and_luck_helps() {
        let table = LootTable {
            id: "t".into(),
            rolls: 1,
            entries: vec![
                LootEntry {
                    weight: 90,
                    item: None,
                    quantity: [1, 1],
                    crowns: None,
                    rare: false,
                },
                LootEntry {
                    weight: 10,
                    item: Some(7),
                    quantity: [1, 3],
                    crowns: None,
                    rare: true,
                },
            ],
        };
        let luck = Luck {
            extra_rolls: 1,
            rare_weight_pct: 400,
        };
        let (mut plain, mut lucky) = (0u32, 0u32);
        let mut r = rng(12345);
        for _ in 0..10_000 {
            let d = table.roll(None, &mut r);
            plain += d.items.first().map_or(0, |&(_, n)| u32::from(n > 0));
            let d = table.roll(Some(luck), &mut r);
            lucky += d.items.first().map_or(0, |&(_, n)| u32::from(n > 0));
            assert!(
                d.items
                    .iter()
                    .all(|&(id, n)| id == 7 && (1..=6).contains(&n))
            );
        }
        // About 10 % plainly; with four times the weight and two rolls, about 52 %.
        assert!((800..1200).contains(&plain), "{plain}");
        assert!(lucky > 4_000, "{lucky}");
    }

    #[test]
    fn rejects_broken_tables_and_spots() {
        let (layouts, finds, loot) = shipped();
        let layout: Vec<&Layout> = layouts.iter().collect();
        let palette = Palette::from_json(PALETTE).unwrap();
        let catalog = Catalog::from_json(CATALOG, &palette).unwrap();
        let mut t = loot.clone();
        t.tables[0].entries[0].weight = 0;
        assert!(t.validate(&catalog).is_err());
        let mut f = finds.clone();
        f.spots.pop();
        assert!(
            f.validate(&layout, &loot)
                .unwrap_err()
                .to_string()
                .contains("no entry")
        );
        let mut f = finds.clone();
        f.spots[0].loot = "nowhere".into();
        assert!(
            f.validate(&layout, &loot)
                .unwrap_err()
                .to_string()
                .contains("loot table")
        );
    }
}
