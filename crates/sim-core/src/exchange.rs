//! The Owl Exchange's category table: every catalog item
//! lists under one category and subcategory (`ItemDef.exchange`), which the
//! Exchange's browse filters use. The table is the design's:
//!
//! | Category | Subcategories |
//! | --- | --- |
//! | `hats` | `hats`, `hoods`, `hair_accessories` |
//! | `body` | `robes`, `jackets`, `sweaters`, `tops` |
//! | `legs` | `trousers`, `skirts` |
//! | `shoes` | `shoes`, `boots` |
//! | `furniture` | `beds`, `seating`, `tables`, `decor`, `lighting`, `plants` |
//! | `materials` | `fibers`, `feathers`, `fur`, `dye_plants`, `ingredients`, `patterns` |
//! | `dyes` | `dyes`, `finishes` |
//! | `potions` | one per effect (`glow`, `chameleon`, ..., `protocol::EffectKind::id`) |
//!
//! `materials/ingredients` (potion ingredients) is the one addition to the
//! design's list. Clothing lists under its slot's category, furniture under
//! `furniture`, materials and patterns under `materials` (patterns, and only
//! patterns, under `patterns`), dyes and finishes under `dyes`, potions under
//! `potions` by effect ([`category_for`]; checked by `Catalog::validate`).
//! Ids are stable: listings store them.

use protocol::GearSlot;

use crate::catalog::{ItemDef, ItemKind};

pub const CAT_HATS: &str = "hats";
pub const CAT_BODY: &str = "body";
pub const CAT_LEGS: &str = "legs";
pub const CAT_SHOES: &str = "shoes";
pub const CAT_FURNITURE: &str = "furniture";
pub const CAT_MATERIALS: &str = "materials";
pub const CAT_DYES: &str = "dyes";
pub const CAT_POTIONS: &str = "potions";

/// Patterns' subcategory under `materials`.
pub const SUB_PATTERNS: &str = "patterns";
/// Finishes' subcategory under `dyes`.
pub const SUB_FINISHES: &str = "finishes";

/// Every category with its subcategories, in browse order.
pub const CATEGORIES: &[(&str, &[&str])] = &[
    (CAT_HATS, &["hats", "hoods", "hair_accessories"]),
    (CAT_BODY, &["robes", "jackets", "sweaters", "tops"]),
    (CAT_LEGS, &["trousers", "skirts"]),
    (CAT_SHOES, &["shoes", "boots"]),
    (
        CAT_FURNITURE,
        &["beds", "seating", "tables", "decor", "lighting", "plants"],
    ),
    (
        CAT_MATERIALS,
        &[
            "fibers",
            "feathers",
            "fur",
            "dye_plants",
            "ingredients",
            SUB_PATTERNS,
        ],
    ),
    (CAT_DYES, &["dyes", SUB_FINISHES]),
    (
        CAT_POTIONS,
        &[
            "glow",
            "chameleon",
            "shrinking",
            "swelling",
            "babbling",
            "mimic_draught",
            "fortunes_draught",
            "pick_me_up_tonic",
            "invisibility",
            "featherlight",
        ],
    ),
];

/// The subcategories of `category`, or `None` for an unknown category.
pub fn subcategories(category: &str) -> Option<&'static [&'static str]> {
    CATEGORIES
        .iter()
        .find(|(c, _)| *c == category)
        .map(|(_, subs)| *subs)
}

/// Whether `(category, subcategory)` is in the table (`subcategory` may be
/// absent: the whole category).
pub fn is_valid(category: &str, subcategory: Option<&str>) -> bool {
    subcategories(category).is_some_and(|subs| subcategory.is_none_or(|s| subs.contains(&s)))
}

/// The category an item must list under, from its kind and slot.
pub fn category_for(item: &ItemDef) -> Option<&'static str> {
    Some(match item.kind {
        ItemKind::Clothing => match item.slot? {
            GearSlot::Hat => CAT_HATS,
            GearSlot::Body => CAT_BODY,
            GearSlot::Legs => CAT_LEGS,
            GearSlot::Shoes => CAT_SHOES,
        },
        ItemKind::Furniture => CAT_FURNITURE,
        ItemKind::Material | ItemKind::Pattern => CAT_MATERIALS,
        ItemKind::Dye | ItemKind::Finish => CAT_DYES,
        ItemKind::Potion => CAT_POTIONS,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use protocol::EffectKind;

    #[test]
    fn potions_list_under_every_effect() {
        let subs = subcategories(CAT_POTIONS).unwrap();
        assert_eq!(subs.len(), EffectKind::POTIONS.len());
        for k in EffectKind::POTIONS {
            assert!(subs.contains(&k.id()), "{k:?}");
        }
    }

    #[test]
    fn the_table_answers_lookups() {
        assert!(is_valid("hats", None));
        assert!(is_valid("hats", Some("hoods")));
        assert!(!is_valid("hats", Some("boots")));
        assert!(!is_valid("wands", None));
        assert_eq!(subcategories("dyes"), Some(&["dyes", "finishes"][..]));
    }
}
