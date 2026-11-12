//! Crafting recipes: `data/crafting/recipes.json` ([`Recipes`]).
//!
//! Gamp's Law: nothing is conjured from nothing. Every recipe turns inputs
//! (catalog items and quantities) into an output at a station:
//!
//! - **Charms** (`craft_charms`, the knitting and sewing corner of each
//!   common room): materials and a learned pattern make a garment; the
//!   crafter picks its dyes (`dyeable`), paying the dye rule
//!   (`Palette::dyes_needed`) in dye items.
//! - **Transfiguration** (`craft_transfiguration`, the table in each common
//!   room): a *restyle* changes a crafted garment into another member of its
//!   `family` (same slot), keeping its dyes, finish and maker's mark; the
//!   recipe's `inputs` are the extra materials it uses.
//! - **Potions** (`cauldron`: the Potions classroom in the dungeons, or a
//!   cauldron placed in a dorm): dyes, finishes and potions.
//!
//! Each recipe needs a skill level in its discipline (skill XP from crafting
//! now, classes) and gives XP. Recipes without a `pattern` are known
//! by everyone; the others are learned from a pattern item (found or
//! bought), which learning uses up. Crafted items carry the crafter's
//! maker's mark. `craft_secs` is the crafter's busy time: the server
//! refuses another craft until it has passed.

use std::collections::{BTreeMap, HashSet};

use protocol::StationKind;
use serde::{Deserialize, Serialize};

use crate::catalog::{Catalog, ItemKind};

/// The recipes schema version this crate reads.
pub const RECIPES_VERSION: u32 = 1;
/// Longest craft time (seconds): crafting is short.
pub const CRAFT_SECS_MAX: u16 = 60;

#[derive(Debug, thiserror::Error)]
pub enum RecipeError {
    #[error("recipes JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("recipes are inconsistent: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Discipline {
    Charms,
    Transfiguration,
    Potions,
}

impl Discipline {
    pub const ALL: [Discipline; 3] = [
        Discipline::Charms,
        Discipline::Transfiguration,
        Discipline::Potions,
    ];

    /// The id used in JSON and the database (`skills.discipline`).
    pub fn id(self) -> &'static str {
        match self {
            Discipline::Charms => "charms",
            Discipline::Transfiguration => "transfiguration",
            Discipline::Potions => "potions",
        }
    }

    pub fn from_id(id: &str) -> Option<Discipline> {
        Discipline::ALL.into_iter().find(|d| d.id() == id)
    }

    /// The school subject whose skill track this is (classes
    /// feed the same track; the ids are the same).
    pub fn subject(self) -> crate::classes::Subject {
        use crate::classes::Subject;
        match self {
            Discipline::Charms => Subject::Charms,
            Discipline::Transfiguration => Subject::Transfiguration,
            Discipline::Potions => Subject::Potions,
        }
    }

    /// Where this discipline's recipes are made.
    pub fn station(self) -> StationKind {
        match self {
            Discipline::Charms => StationKind::CraftCharms,
            Discipline::Transfiguration => StationKind::CraftTransfiguration,
            Discipline::Potions => StationKind::Cauldron,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RecipeKind {
    /// Inputs become a new output item.
    Craft,
    /// A garment of `family` becomes `output.item` (Transfiguration).
    Restyle,
}

/// An item and how many of it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Ingredient {
    pub item: u16,
    pub quantity: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Recipe {
    /// Stable id, never reused (known recipes and patterns refer to it).
    pub id: u16,
    /// Display name.
    pub name: String,
    pub discipline: Discipline,
    pub station: StationKind,
    pub kind: RecipeKind,
    /// Used up by the craft (for a restyle: besides the garment itself).
    pub inputs: Vec<Ingredient>,
    pub output: Ingredient,
    /// Restyle only: the family the input garment must belong to (any
    /// member but the output).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub family: Option<String>,
    /// The crafter picks the output's three dyes (garments, furniture),
    /// paying the dye rule; otherwise the output gets its catalog dyes.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub dyeable: bool,
    /// Potions whose primary dye matters (Glow colour, Chameleon family):
    /// the crafter picks it, paying one dye of that colour's family.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub tint: bool,
    /// Skill level needed in `discipline` (1 = anyone).
    pub skill: u8,
    /// Skill XP the craft gives.
    pub xp: u32,
    /// Busy time (seconds).
    pub craft_secs: u16,
    /// The pattern item that teaches it; `None` = known by everyone.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pattern: Option<u16>,
}

/// `data/crafting/recipes.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Recipes {
    pub version: u32,
    /// XP needed for each skill level from 2 up: `skill_levels[0]` is the XP
    /// for level 2, and so on (increasing). Level 1 needs none.
    pub skill_levels: Vec<u32>,
    pub recipes: Vec<Recipe>,
}

impl Recipes {
    /// Parses recipes and checks them against the catalog they use.
    pub fn from_json(json: &str, catalog: &Catalog) -> Result<Recipes, RecipeError> {
        let r: Recipes = serde_json::from_str(json)?;
        r.validate(catalog)?;
        Ok(r)
    }

    /// Unique ids; each station its discipline's; inputs and outputs that
    /// exist, in quantities their stacks allow; restyles within a family of
    /// clothing; craft outputs that are never sold in the owl-order shop;
    /// patterns and recipes that name each other both ways; skill levels in
    /// range and increasing.
    pub fn validate(&self, catalog: &Catalog) -> Result<(), RecipeError> {
        let fail = |m: String| Err(RecipeError::Invalid(m));
        if self.version != RECIPES_VERSION {
            return fail(format!("unsupported recipes version {}", self.version));
        }
        if self.skill_levels.is_empty() || !self.skill_levels.windows(2).all(|w| w[0] < w[1]) {
            return fail("skill_levels must be increasing".into());
        }
        let max_level = self.max_level();
        let mut ids = HashSet::new();
        for r in &self.recipes {
            let what = format!("recipe {} ({:?})", r.id, r.name);
            if !ids.insert(r.id) || r.name.trim().is_empty() {
                return fail(format!("{what}: duplicate id or empty name"));
            }
            if r.station != r.discipline.station() {
                return fail(format!(
                    "{what}: {:?} is made at {:?}",
                    r.discipline,
                    r.discipline.station()
                ));
            }
            if !(1..=max_level).contains(&r.skill) || r.xp == 0 {
                return fail(format!(
                    "{what}: skill must be 1 to {max_level} and xp positive"
                ));
            }
            if !(1..=CRAFT_SECS_MAX).contains(&r.craft_secs) {
                return fail(format!("{what}: craft_secs must be 1 to {CRAFT_SECS_MAX}"));
            }
            let mut seen = HashSet::new();
            for i in r.inputs.iter().chain(std::iter::once(&r.output)) {
                let Some(def) = catalog.get(i.item) else {
                    return fail(format!("{what}: unknown item {}", i.item));
                };
                if i.quantity == 0 || i.quantity > def.stack_limit() {
                    return fail(format!(
                        "{what}: {} x{} does not fit a stack",
                        i.item, i.quantity
                    ));
                }
                if !seen.insert(i.item) {
                    return fail(format!("{what}: item {} listed twice", i.item));
                }
            }
            let out = catalog.get(r.output.item).expect("checked above");
            // The shop must not undercut crafted fashion: crafted garments
            // and furniture are never sold there (basic dyes may be both).
            let fashion = matches!(out.kind, ItemKind::Clothing | ItemKind::Furniture);
            if fashion && out.price.is_some() {
                return fail(format!("{what}: crafted fashion is never sold in the shop"));
            }
            if out.retired {
                return fail(format!("{what}: makes a retired item"));
            }
            match r.kind {
                RecipeKind::Craft if r.family.is_none() => {}
                RecipeKind::Restyle => {
                    let ok = r.discipline == Discipline::Transfiguration
                        && out.kind == ItemKind::Clothing
                        && r.family.is_some()
                        && out.family == r.family;
                    if !ok {
                        return fail(format!(
                            "{what}: a restyle makes a member of its clothing family"
                        ));
                    }
                }
                RecipeKind::Craft => return fail(format!("{what}: only restyles have a family")),
            }
            if r.dyeable && out.kind != ItemKind::Clothing && out.kind != ItemKind::Furniture {
                return fail(format!("{what}: only garments and furniture are dyeable"));
            }
            if r.tint && out.kind != ItemKind::Potion {
                return fail(format!("{what}: only potions are tinted"));
            }
            if let Some(p) = r.pattern {
                let teaches = catalog
                    .get(p)
                    .and_then(|i| i.pattern)
                    .is_some_and(|pd| pd.recipe == r.id);
                if !teaches {
                    return fail(format!("{what}: pattern {p} does not teach it"));
                }
            }
        }
        // Every pattern teaches a recipe that names it.
        for item in catalog.items.iter().filter(|i| i.kind == ItemKind::Pattern) {
            let recipe = item.pattern.map(|p| p.recipe);
            let ok = recipe
                .and_then(|id| self.get(id))
                .is_some_and(|r| r.pattern == Some(item.id));
            if !ok {
                return fail(format!(
                    "pattern {} teaches no recipe that names it",
                    item.id
                ));
            }
        }
        // Crafted clothing can be restyled: every family has a restyle into
        // each member.
        let mut restyles: BTreeMap<&str, HashSet<u16>> = BTreeMap::new();
        for r in self
            .recipes
            .iter()
            .filter(|r| r.kind == RecipeKind::Restyle)
        {
            if let Some(f) = &r.family {
                restyles
                    .entry(f.as_str())
                    .or_default()
                    .insert(r.output.item);
            }
        }
        let families: HashSet<&str> = catalog
            .items
            .iter()
            .filter_map(|i| i.family.as_deref())
            .collect();
        for family in families {
            for member in catalog.family(family) {
                if !restyles.get(family).is_some_and(|s| s.contains(&member.id)) {
                    return fail(format!("family {family:?}: no restyle into {}", member.id));
                }
            }
        }
        Ok(())
    }

    pub fn get(&self, id: u16) -> Option<&Recipe> {
        self.recipes.iter().find(|r| r.id == id)
    }

    /// The highest skill level.
    pub fn max_level(&self) -> u8 {
        u8::try_from(self.skill_levels.len() + 1).unwrap_or(u8::MAX)
    }

    /// The skill level `xp` reaches (1 with no XP).
    pub fn skill_level(&self, xp: u32) -> u8 {
        let reached = self
            .skill_levels
            .iter()
            .take_while(|&&need| xp >= need)
            .count();
        u8::try_from(reached + 1).unwrap_or(u8::MAX)
    }

    /// Recipes everyone knows without a pattern.
    pub fn known_by_default(&self) -> impl Iterator<Item = &Recipe> {
        self.recipes.iter().filter(|r| r.pattern.is_none())
    }

    /// The restyle turning a member of `family` into `target`.
    pub fn restyle_into(&self, family: &str, target: u16) -> Option<&Recipe> {
        self.recipes.iter().find(|r| {
            r.kind == RecipeKind::Restyle
                && r.family.as_deref() == Some(family)
                && r.output.item == target
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::{Catalog, Palette};

    const PALETTE: &str = include_str!("../../../data/items/palette.json");
    const CATALOG: &str = include_str!("../../../data/items/catalog.json");
    const RECIPES: &str = include_str!("../../../data/crafting/recipes.json");

    fn shipped() -> (Catalog, Recipes) {
        let palette = Palette::from_json(PALETTE).unwrap();
        let catalog = Catalog::from_json(CATALOG, &palette).unwrap();
        let recipes = Recipes::from_json(RECIPES, &catalog).unwrap();
        (catalog, recipes)
    }

    #[test]
    fn the_shipped_recipes_are_valid_and_cover_each_discipline() {
        let (catalog, recipes) = shipped();
        for d in Discipline::ALL {
            assert!(
                recipes.recipes.iter().filter(|r| r.discipline == d).count() >= 3,
                "{d:?}"
            );
            assert!(
                recipes.known_by_default().any(|r| r.discipline == d),
                "{d:?} starter recipe"
            );
        }
        // Every potion and dye in the catalog can be brewed.
        for item in catalog
            .items
            .iter()
            .filter(|i| matches!(i.kind, ItemKind::Potion | ItemKind::Dye | ItemKind::Finish))
        {
            assert!(
                recipes.recipes.iter().any(|r| r.output.item == item.id),
                "item {} is not brewed",
                item.id
            );
        }
    }

    #[test]
    fn levels_follow_xp() {
        let (_, recipes) = shipped();
        assert_eq!(recipes.skill_level(0), 1);
        let l2 = recipes.skill_levels[0];
        assert_eq!(recipes.skill_level(l2 - 1), 1);
        assert_eq!(recipes.skill_level(l2), 2);
        assert_eq!(recipes.skill_level(u32::MAX), recipes.max_level());
    }

    #[test]
    fn rejects_broken_recipes() {
        let (catalog, recipes) = shipped();
        let check = |f: &dyn Fn(&mut Recipes)| {
            let mut r = recipes.clone();
            f(&mut r);
            r.validate(&catalog).unwrap_err().to_string()
        };
        assert!(check(&|r| r.recipes[1].id = r.recipes[0].id).contains("duplicate"));
        assert!(check(&|r| r.recipes[0].station = StationKind::OwlExchange).contains("made at"));
        assert!(check(&|r| r.recipes[0].output.item = 60_000).contains("unknown item"));
        assert!(check(&|r| r.recipes[0].craft_secs = 0).contains("craft_secs"));
        assert!(check(&|r| r.skill_levels = vec![10, 5]).contains("increasing"));
        // A garment sold in the shop can never be a craft's output.
        let shop = catalog
            .shop_items()
            .find(|i| i.kind == ItemKind::Clothing)
            .unwrap()
            .id;
        assert!(
            check(&|r| r.recipes[0].output = Ingredient {
                item: shop,
                quantity: 1
            })
            .contains("never sold")
        );
    }
}
