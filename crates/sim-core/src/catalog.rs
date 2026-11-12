//! The content catalogs shared by art, server and client:
//!
//! - `data/items/catalog.json` ([`Catalog`]): every item definition, by stable
//!   numeric id;
//! - `data/items/palette.json` ([`Palette`]): the dye colours and each house's
//!   colours;
//! - `data/characters/creator.json` ([`Creator`]): body types, sliders, skin
//!   tones, hair styles, hair colours and eye colours (version 2: no build
//!   sliders, height a uniform scale, hair styles per body, retired styles
//!   with a per-body `migrate_to`, hair and eye colours).
//!
//! These files are the contract between the art (asset keys), the server
//! (ids, starter grants, validation) and the client (labels, assets). Display
//! names live only here, never in code.
//!
//! **Ids are forever.** An item id (`u16`) or hair style id is never reused or
//! renumbered: item instances in the database and saved appearances refer to
//! them. An item that should no longer be granted is marked `"retired": true`
//! and stays in the file. Palette, skin tone, hair colour and body type
//! entries are referred to by index, so they are only ever appended.
//!
//! **Asset keys** are lower-case paths (`[a-z0-9_/]`, no IP names, as for every
//! asset file name) under `client/public/assets/`:
//!
//! - furniture: `<asset>.glb` (e.g. `furniture/bed_four_poster_a.glb`);
//! - clothing and hair are fitted per body type: `<asset>_<body type id>.glb`
//!   (e.g. `gear/body/robe_school_house_a_body_a.glb`), and hair also ships
//!   `<asset>_cap_<body type id>.glb`, worn under hats;
//! - bodies: `<asset>.glb` (e.g. `characters/body_a.glb`).
//!
//! Every item has the three dye channels (primary, secondary, trim) of the
//! base body spec's dye mask; `dyes` are their default palette indices.
//!
//! Catalog version 2 adds the economy's item kinds
//! (`material`, `dye`, `finish`, `potion`, `pattern`), stacks
//! (`stackable` + `max_stack`), `required_level`, `house_theme`, the Owl
//! Exchange class (`exchange: { category, subcategory }`, see
//! [`crate::exchange`]), the owl-order `price` of basic items, restyle
//! `family` for crafted clothing, and the kind-specific `dye`, `finish`,
//! `potion` and `pattern` fields; furniture may be a `station` (cauldrons).
//! The palette gains `families` (every colour in exactly one), which dyes
//! are made for.
//!
//! A garment may be restricted to one [`Wear`] class
//! (`"wear": "female"` / `"male"` on the item); every body type names its
//! class (`"wear"` in `creator.json`). [`can_wear`] is the one rule: the
//! server enforces it on every way a garment is put on, the client (through
//! sim-wasm's `canWear`) greys out and labels what a body cannot wear. Only
//! wearing is restricted: anyone may buy, sell, trade, mail and own any item.

use std::collections::{BTreeMap, HashSet};

use protocol::{Appearance, ChameleonPart, EffectKind, Finish, GearSlot, House, StationKind, Worn};
use serde::{Deserialize, Serialize};

use crate::exchange;
use crate::placement::{AreaFrame, FurnitureShape, PlaceArea, PlaceSurface};

/// Who may wear a restricted garment, and which class each
/// body type is. Serialised lower-case (`"female"`, `"male"`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Wear {
    Female,
    Male,
}

impl Wear {
    pub fn id(self) -> &'static str {
        match self {
            Wear::Female => "female",
            Wear::Male => "male",
        }
    }

    pub fn from_id(id: &str) -> Option<Wear> {
        match id {
            "female" => Some(Wear::Female),
            "male" => Some(Wear::Male),
            _ => None,
        }
    }

    /// The label shown wherever a restricted item shows ("Female only").
    pub fn label(self) -> &'static str {
        match self {
            Wear::Female => "Female only",
            Wear::Male => "Male only",
        }
    }
}

/// The wear rule: a body of class `body` may wear a garment
/// restricted to `item` (`None`: anyone). The one rule, shared by the server
/// and (through sim-wasm `canWear`) the client.
pub fn can_wear(body: Wear, item: Option<Wear>) -> bool {
    item.is_none_or(|w| w == body)
}

/// The catalog schema versions this crate reads.
pub const CATALOG_VERSION: u32 = 2;
pub const PALETTE_VERSION: u32 = 1;
pub const CREATOR_VERSION: u32 = 2;

/// Tag: granted to every new character (and to characters made before starter
/// wardrobes on their next login), subject to its `house:` tag.
pub const TAG_STARTER: &str = "starter";
/// Tag: preselected in the character creator for its slot.
pub const TAG_DEFAULT: &str = "default";
/// Tag prefix: `house:<house id>` limits a starter item to that house.
pub const TAG_HOUSE_PREFIX: &str = "house:";

/// Required levels run from 1 to this (player levels 1-20).
pub const LEVEL_MAX: u8 = 20;
/// The largest stack any item may allow.
pub const STACK_MAX: u32 = 9_999;
/// The highest owl-order price (Crowns).
pub const PRICE_MAX: u32 = 1_000_000;

#[derive(Debug, thiserror::Error)]
pub enum CatalogError {
    #[error("catalog JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("catalog is inconsistent: {0}")]
    Invalid(String),
}

fn invalid<T>(msg: String) -> Result<T, CatalogError> {
    Err(CatalogError::Invalid(msg))
}

fn check_asset_key(what: &str, key: &str) -> Result<(), CatalogError> {
    let ok = !key.is_empty()
        && !key.starts_with('/')
        && !key.ends_with('/')
        && !key.contains("//")
        && key
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'/');
    if ok {
        Ok(())
    } else {
        invalid(format!("{what}: asset key {key:?} must match [a-z0-9_/]"))
    }
}

fn parse_hex(hex: &str) -> Option<[u8; 3]> {
    let h = hex.strip_prefix('#')?;
    if h.len() != 6 || !h.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let byte = |i: usize| u8::from_str_radix(&h[i..i + 2], 16).ok();
    Some([byte(0)?, byte(2)?, byte(4)?])
}

// ------------------------------------------------------------------ items

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ItemKind {
    Clothing,
    Furniture,
    /// Crafting materials: fibres, feathers, fur, dye plants, potion
    /// ingredients, rare scraps (stackable).
    Material,
    /// Brewed dye for one palette family, consumed when applied (stackable).
    Dye,
    /// Brewed garment finish, consumed when applied (stackable).
    Finish,
    /// A drinkable potion (stackable).
    Potion,
    /// Teaches a recipe when learned, which uses it up (stackable).
    Pattern,
}

impl ItemKind {
    /// Kinds whose items stack (one row holds a quantity).
    pub fn stacks(self) -> bool {
        !matches!(self, ItemKind::Clothing | ItemKind::Furniture)
    }

    pub fn id(self) -> &'static str {
        match self {
            ItemKind::Clothing => "clothing",
            ItemKind::Furniture => "furniture",
            ItemKind::Material => "material",
            ItemKind::Dye => "dye",
            ItemKind::Finish => "finish",
            ItemKind::Potion => "potion",
            ItemKind::Pattern => "pattern",
        }
    }
}

/// Where an item is listed on the Owl Exchange ([`crate::exchange`]).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExchangeClass {
    pub category: String,
    pub subcategory: String,
}

/// Dye items: the palette family they colour with.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DyeDef {
    /// A `palette.json` `families[].id`.
    pub family: String,
}

/// Potion items: what drinking one does. Durations and numbers come from
/// `data/economy.json`; a brewed potion's primary dye sets its colour (Glow
/// aura colour, Chameleon palette family).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct PotionDef {
    pub effect: EffectKind,
    /// Chameleon Tonic only: which colour cycles.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub part: Option<ChameleonPart>,
}

/// Pattern items: the recipe (`data/crafting/recipes.json` id) learning one teaches.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct PatternDef {
    pub recipe: u16,
}

fn is_level_one(v: &u8) -> bool {
    *v == 1
}

fn level_one() -> u8 {
    1
}

/// Default palette indices of an item's three dye channels.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct DyeChannels {
    pub primary: u8,
    pub secondary: u8,
    pub trim: u8,
}

impl DyeChannels {
    pub fn to_array(self) -> [u8; 3] {
        [self.primary, self.secondary, self.trim]
    }
}

/// Which of a house's two colours a channel takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HouseColour {
    HousePrimary,
    HouseSecondary,
}

/// Channels that take the owner's house colours when the item is granted
/// (bed hangings, jumper trim); unset channels keep `dyes`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct HouseDyes {
    #[serde(default)]
    pub primary: Option<HouseColour>,
    #[serde(default)]
    pub secondary: Option<HouseColour>,
    #[serde(default)]
    pub trim: Option<HouseColour>,
}

/// Furniture-only fields.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FurnitureDef {
    /// Where it can go (see `crate::placement`).
    pub surface: PlaceSurface,
    /// Width along X and depth along Z at yaw 0 (metres).
    pub footprint: [f32; 2],
    pub height: f32,
    /// Height of a flat top that table items may stand on (desks, bedside
    /// tables, trunks, bookcases), above the item's origin.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub top: Option<f32>,
    /// The part of the top items may stand on, `[x0, z0, x1, z1]` in the
    /// item's own axes (yaw 0): inside the footprint, clear of a gallery or a
    /// round edge. Absent: the whole footprint. Only with `top`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub top_area: Option<[f32; 4]>,
    /// Lower flat surfaces table items may stand on (a bookcase's free
    /// shelf ends), each with its headroom.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub shelves: Vec<FurnitureShelf>,
    /// Where a character playing `sit` by the placed item is drawn (chairs,
    /// stools, armchairs, a bed's sides): the client's seats, as the
    /// castle's chairs (visual; the server checks the sit emote itself).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub seats: Vec<FurnitureSeat>,
    /// Placed in a dorm, the item is a crafting station its room's owner and
    /// guests use with `ClientMsg::UseFurniture` (only `cauldron` so far).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub station: Option<StationKind>,
}

/// A shelf of a furniture item: a flat surface at `y` above the origin over
/// `area` (`[x0, z0, x1, z1]`, item axes) with `clear` metres of headroom.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct FurnitureShelf {
    pub y: f32,
    pub area: [f32; 4],
    pub clear: f32,
}

/// A seat of a furniture item: its top at `y` above the origin over `area`
/// (`[x0, z0, x1, z1]`, item axes), a sitter facing `yaw` relative to the
/// item (0: the item's front, +Z). Like the castle's seat rows
/// (docs/architecture.md "Seats fit the sitter's legs") it says how far the
/// seat reaches from the area's centre line along the facing: `front` ahead
/// to its front edge (the cushion or the frame under the top, whichever is
/// further), `back` behind to the backrest's face or a backless seat's back
/// edge, and `drop` the top above the floor the feet rest on (measured off
/// the models: tools/blender/tests/test_world.py `FurnitureTests`).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct FurnitureSeat {
    pub y: f32,
    pub area: [f32; 4],
    #[serde(default)]
    pub yaw: f32,
    pub front: f32,
    pub back: f32,
    pub drop: f32,
}

impl FurnitureDef {
    pub fn shape(&self) -> FurnitureShape {
        FurnitureShape {
            surface: self.surface,
            footprint: self.footprint,
            height: self.height,
        }
    }

    /// What a placed copy at `pos` turned by `yaw` blocks (docs/architecture.md
    /// "Layouts"): its footprint, turned with it, over the
    /// floor it stands on, for floor pieces taller than a step (a rug is
    /// walked over; wall and table pieces stand out of the way). The server
    /// adds these to a dorm's world for every member's moves and the client
    /// predicts with the same (`SimWorld.setFurniture`).
    pub fn blocker(&self, id: &str, pos: [f32; 3], yaw: f32) -> Option<crate::layout::Blocker> {
        let step = crate::movement::MoveParams::default().step_height;
        if self.surface != PlaceSurface::Floor || self.height.is_nan() || self.height <= step {
            return None;
        }
        let [w, d] = self.footprint;
        Some(crate::layout::Blocker {
            id: id.to_owned(),
            at: [pos[0], pos[2]],
            half: [w / 2.0, d / 2.0],
            y: [pos[1] - 0.3, pos[1] + self.height],
            yaw,
            round: false,
        })
    }

    /// The table areas a placed copy at `pos` turned by `yaw` offers: its top
    /// and its shelves, in its own frame (they turn with it at any yaw). Ids
    /// are `<id>` for the top and `<id>.shelf<n>` for the shelves.
    pub fn top_areas(&self, id: &str, pos: [f32; 3], yaw: f32) -> Vec<PlaceArea> {
        let [w, d] = self.footprint;
        let frame = Some(AreaFrame { origin: pos, yaw });
        let area = |id: String, y: f32, r: [f32; 4], clear: Option<f32>| PlaceArea {
            id,
            surface: PlaceSurface::Table,
            min: [r[0], y, r[1]],
            max: [r[2], y, r[3]],
            facing: None,
            clear,
            frame,
        };
        let mut out = Vec::new();
        if let Some(top) = self.top {
            let r = self
                .top_area
                .unwrap_or([-w / 2.0, -d / 2.0, w / 2.0, d / 2.0]);
            out.push(area(id.to_owned(), top, r, None));
        }
        for (n, s) in self.shelves.iter().enumerate() {
            out.push(area(format!("{id}.shelf{n}"), s.y, s.area, Some(s.clear)));
        }
        out
    }

    /// Checks the tops, shelves and seats against the footprint and height.
    fn check_surfaces(&self) -> Result<(), &'static str> {
        let [w, d] = self.footprint;
        let e = crate::placement::PLACE_EPSILON;
        let rect_ok = |r: &[f32; 4]| {
            r.iter().all(|v| v.is_finite())
                && r[0] < r[2]
                && r[1] < r[3]
                && r[0] >= -w / 2.0 - e
                && r[2] <= w / 2.0 + e
                && r[1] >= -d / 2.0 - e
                && r[3] <= d / 2.0 + e
        };
        let height_ok = |y: f32| y.is_finite() && y > 0.0 && y <= self.height + e;
        if self.top_area.is_some() && self.top.is_none() {
            return Err("top_area without a top");
        }
        if self.top_area.as_ref().is_some_and(|r| !rect_ok(r)) {
            return Err("top_area outside the footprint");
        }
        for s in &self.shelves {
            let under_top = self.top.is_none_or(|t| s.y + s.clear <= t + e);
            let clear_ok = s.clear.is_finite() && s.clear > 0.0;
            if !height_ok(s.y) || !rect_ok(&s.area) || !clear_ok || !under_top {
                return Err("bad shelf");
            }
        }
        for s in &self.seats {
            let fit_ok = s.front.is_finite()
                && s.front >= 0.0
                && s.back.is_finite()
                && s.back >= 0.0
                && s.drop.is_finite()
                && s.drop > 0.0
                && s.drop <= s.y + e;
            if !height_ok(s.y) || !rect_ok(&s.area) || !s.yaw.is_finite() || !fit_ok {
                return Err("bad seat");
            }
        }
        if self.surface == PlaceSurface::Wall
            && (self.top.is_some() || !self.shelves.is_empty() || !self.seats.is_empty())
        {
            return Err("wall items have no tops or seats");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ItemDef {
    /// Stable id, never reused.
    pub id: u16,
    pub kind: ItemKind,
    /// Clothing only: the gear slot it is worn in.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub slot: Option<GearSlot>,
    /// Display name.
    pub name: String,
    /// Asset key (see the module docs for the file names it maps to).
    pub asset: String,
    pub dyes: DyeChannels,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub house_dyes: Option<HouseDyes>,
    /// `starter`, `default`, `house:<id>`, plus free-form tags for the
    /// wardrobe's filters (`school`, `fashion`, ...).
    #[serde(default)]
    pub tags: Vec<String>,
    /// Furniture only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub furniture: Option<FurnitureDef>,
    /// No longer granted; existing instances still work.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub retired: bool,

    // ---- Economy and crafting.
    /// One row holds a quantity of up to `max_stack` (materials, dyes,
    /// finishes, potions, patterns); clothing and furniture are one row each.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub stackable: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_stack: Option<u32>,
    /// Player level needed to use it (levels arrive; 1 until then).
    #[serde(default = "level_one", skip_serializing_if = "is_level_one")]
    pub required_level: u8,
    /// A house theme (lower-case house id) for the Exchange's house filter.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub house_theme: Option<String>,
    /// Clothing only: only bodies of this class may wear it
    /// ([`can_wear`]); `None` (the default): anyone.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wear: Option<Wear>,
    /// Owl Exchange category and subcategory.
    pub exchange: ExchangeClass,
    /// Owl-order catalog price in Crowns: only basic, plain items are sold
    /// (never a crafted one, see `crafting::Recipes::validate`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub price: Option<u32>,
    /// Clothing only: the Transfiguration restyle family (members restyle
    /// into each other, keeping dyes, finish and maker's mark).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub family: Option<String>,
    /// Dye items only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dye: Option<DyeDef>,
    /// Finish items only: the finish they apply.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub finish: Option<Finish>,
    /// Potion items only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub potion: Option<PotionDef>,
    /// Pattern items only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pattern: Option<PatternDef>,
}

impl ItemDef {
    pub fn has_tag(&self, tag: &str) -> bool {
        self.tags.iter().any(|t| t == tag)
    }

    /// Units one row may hold: `max_stack` for stackable items, else 1.
    pub fn stack_limit(&self) -> u32 {
        if self.stackable {
            self.max_stack.unwrap_or(1)
        } else {
            1
        }
    }

    /// The house of its `house_theme`.
    pub fn theme_house(&self) -> Option<House> {
        self.house_theme.as_deref().and_then(House::from_id)
    }

    /// Whether the owl-order catalog sells it.
    pub fn in_shop(&self) -> bool {
        self.price.is_some() && !self.retired
    }

    /// Checks the economy fields: stacks only for stacking kinds, each kind's own
    /// field present exactly for that kind, the Exchange class matching the
    /// kind, slot or effect, and levels, prices and themes in range.
    fn validate_economy(&self, what: &str, palette: &Palette) -> Result<(), CatalogError> {
        if self.stackable != self.kind.stacks() {
            return invalid(format!(
                "{what}: {} items are {}stackable",
                self.kind.id(),
                if self.kind.stacks() { "" } else { "not " }
            ));
        }
        match (self.stackable, self.max_stack) {
            (true, Some(n)) if (2..=STACK_MAX).contains(&n) => {}
            (false, None) => {}
            _ => {
                return invalid(format!(
                    "{what}: max_stack (2 to {STACK_MAX}) goes with stackable"
                ));
            }
        }
        if !(1..=LEVEL_MAX).contains(&self.required_level) {
            return invalid(format!("{what}: required_level must be 1 to {LEVEL_MAX}"));
        }
        if let Some(h) = &self.house_theme
            && House::from_id(h).is_none()
        {
            return invalid(format!("{what}: unknown house_theme {h:?}"));
        }
        if self.price.is_some_and(|p| p == 0 || p > PRICE_MAX) {
            return invalid(format!("{what}: price must be 1 to {PRICE_MAX}"));
        }
        let only = |field: &str, present: bool, kind: ItemKind| {
            if present != (self.kind == kind) {
                invalid(format!(
                    "{what}: `{field}` is for {} items only, and required there",
                    kind.id()
                ))
            } else {
                Ok(())
            }
        };
        only("dye", self.dye.is_some(), ItemKind::Dye)?;
        only("finish", self.finish.is_some(), ItemKind::Finish)?;
        only("potion", self.potion.is_some(), ItemKind::Potion)?;
        only("pattern", self.pattern.is_some(), ItemKind::Pattern)?;
        if self.family.is_some() && self.kind != ItemKind::Clothing {
            return invalid(format!("{what}: only clothing has a restyle family"));
        }
        if self.wear.is_some() && self.kind != ItemKind::Clothing {
            return invalid(format!("{what}: only clothing has a wear restriction"));
        }
        if let Some(d) = &self.dye
            && palette.family_index(&d.family).is_none()
        {
            return invalid(format!("{what}: unknown palette family {:?}", d.family));
        }
        if self.finish == Some(Finish::None) {
            return invalid(format!("{what}: a finish item must apply a finish"));
        }
        if let Some(p) = self.potion
            && !p.effect.is_potion()
        {
            return invalid(format!("{what}: {:?} is not a potion effect", p.effect));
        }
        if let Some(p) = self.potion
            && p.part.is_some() != (p.effect == EffectKind::Chameleon)
        {
            return invalid(format!(
                "{what}: `part` is for Chameleon potions only, and required there"
            ));
        }
        if let Some(f) = &self.furniture
            && f.station.is_some_and(|s| s != StationKind::Cauldron)
        {
            return invalid(format!("{what}: furniture can only be a cauldron station"));
        }
        let expected = exchange::category_for(self);
        let class = &self.exchange;
        let sub_ok = exchange::subcategories(&class.category)
            .is_some_and(|subs| subs.contains(&class.subcategory.as_str()));
        if expected != Some(class.category.as_str()) || !sub_ok {
            return invalid(format!(
                "{what}: Exchange class {}/{} does not fit the item",
                class.category, class.subcategory
            ));
        }
        if let Some(p) = self.potion
            && class.subcategory != p.effect.id()
        {
            return invalid(format!("{what}: potions are listed under their effect"));
        }
        let pattern_sub = class.subcategory == exchange::SUB_PATTERNS;
        if pattern_sub != (self.kind == ItemKind::Pattern) {
            return invalid(format!(
                "{what}: patterns, and only patterns, list under patterns"
            ));
        }
        let finish_sub = class.subcategory == exchange::SUB_FINISHES;
        if class.category == exchange::CAT_DYES && finish_sub != (self.kind == ItemKind::Finish) {
            return invalid(format!(
                "{what}: finishes, and only finishes, list under finishes"
            ));
        }
        Ok(())
    }

    /// The house a `house:<id>` tag limits this item to.
    pub fn house(&self) -> Option<House> {
        self.tags
            .iter()
            .find_map(|t| t.strip_prefix(TAG_HOUSE_PREFIX))
            .and_then(House::from_id)
    }

    /// Whether new characters of `house` are granted it.
    pub fn is_starter_for(&self, house: House) -> bool {
        !self.retired && self.has_tag(TAG_STARTER) && self.house().is_none_or(|h| h == house)
    }

    /// The dyes a fresh instance granted to a character of `house` gets.
    pub fn granted_dyes(&self, house: House, palette: &Palette) -> [u8; 3] {
        let mut dyes = self.dyes.to_array();
        let Some(hd) = self.house_dyes else {
            return dyes;
        };
        let colours = palette.house(house);
        for (slot, choice) in dyes.iter_mut().zip([hd.primary, hd.secondary, hd.trim]) {
            match (choice, colours) {
                (Some(HouseColour::HousePrimary), Some(c)) => *slot = c.primary,
                (Some(HouseColour::HouseSecondary), Some(c)) => *slot = c.secondary,
                _ => {}
            }
        }
        dyes
    }
}

/// `data/items/catalog.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Catalog {
    pub version: u32,
    pub items: Vec<ItemDef>,
}

impl Catalog {
    /// Parses a catalog and checks it against the palette it is dyed from.
    pub fn from_json(json: &str, palette: &Palette) -> Result<Catalog, CatalogError> {
        let c: Catalog = serde_json::from_str(json)?;
        c.validate(palette)?;
        Ok(c)
    }

    /// Unique ids, clothing with (and only clothing with) a slot, furniture
    /// with (and only furniture with) furniture fields, valid asset keys,
    /// palette indices and house tags.
    pub fn validate(&self, palette: &Palette) -> Result<(), CatalogError> {
        if self.version != CATALOG_VERSION {
            return invalid(format!("unsupported catalog version {}", self.version));
        }
        let mut ids = HashSet::with_capacity(self.items.len());
        for item in &self.items {
            let what = format!("item {} ({:?})", item.id, item.name);
            if !ids.insert(item.id) {
                return invalid(format!("{what}: duplicate id"));
            }
            if item.name.trim().is_empty() {
                return invalid(format!("{what}: empty name"));
            }
            check_asset_key(&what, &item.asset)?;
            if item
                .dyes
                .to_array()
                .iter()
                .any(|&d| usize::from(d) >= palette.colours.len())
            {
                return invalid(format!("{what}: dye index outside the palette"));
            }
            match (item.kind, item.slot, &item.furniture) {
                (ItemKind::Clothing, Some(_), None) => {}
                (ItemKind::Clothing, _, _) => {
                    return invalid(format!("{what}: clothing needs a slot and no furniture"));
                }
                (ItemKind::Furniture, None, Some(f)) => {
                    let dims = [f.footprint[0], f.footprint[1], f.height];
                    let top_ok = f
                        .top
                        .is_none_or(|t| t.is_finite() && t > 0.0 && t <= f.height);
                    if !dims.iter().all(|v| v.is_finite() && *v > 0.0) || !top_ok {
                        return invalid(format!("{what}: bad furniture dimensions"));
                    }
                    if let Err(e) = f.check_surfaces() {
                        return invalid(format!("{what}: {e}"));
                    }
                }
                (ItemKind::Furniture, _, _) => {
                    return invalid(format!(
                        "{what}: furniture needs furniture fields and no slot"
                    ));
                }
                (_, None, None) => {}
                (_, _, _) => {
                    return invalid(format!(
                        "{what}: only clothing has a slot and only furniture furniture fields"
                    ));
                }
            }
            for tag in &item.tags {
                if let Some(h) = tag.strip_prefix(TAG_HOUSE_PREFIX)
                    && House::from_id(h).is_none()
                {
                    return invalid(format!("{what}: unknown house in tag {tag:?}"));
                }
            }
            item.validate_economy(&what, palette)?;
        }
        // Restyle families: two or more items of one slot each.
        let mut families: BTreeMap<&str, Vec<&ItemDef>> = BTreeMap::new();
        for item in &self.items {
            if let Some(f) = &item.family {
                families.entry(f.as_str()).or_default().push(item);
            }
        }
        for (family, members) in &families {
            if members.len() < 2 || members.iter().any(|m| m.slot != members[0].slot) {
                return invalid(format!(
                    "restyle family {family:?}: needs two or more items of one slot"
                ));
            }
        }
        Ok(())
    }

    /// The members of restyle family `family`, in catalog order.
    pub fn family(&self, family: &str) -> impl Iterator<Item = &ItemDef> {
        self.items
            .iter()
            .filter(move |i| i.family.as_deref() == Some(family))
    }

    /// What the owl-order catalog sells, in catalog order.
    pub fn shop_items(&self) -> impl Iterator<Item = &ItemDef> {
        self.items.iter().filter(|i| i.in_shop())
    }

    /// Items whose display name contains `query` (case-insensitive), for the
    /// Exchange's name search: listings are then filtered by item id.
    pub fn search_names(&self, query: &str) -> Vec<u16> {
        let q = query.trim().to_lowercase();
        if q.is_empty() {
            return Vec::new();
        }
        self.items
            .iter()
            .filter(|i| i.name.to_lowercase().contains(&q))
            .map(|i| i.id)
            .collect()
    }

    pub fn get(&self, id: u16) -> Option<&ItemDef> {
        self.items.iter().find(|i| i.id == id)
    }

    /// The clothing item `id` if it is worn in `slot`.
    pub fn clothing(&self, id: u16, slot: GearSlot) -> Option<&ItemDef> {
        self.get(id)
            .filter(|i| i.kind == ItemKind::Clothing && i.slot == Some(slot))
    }

    /// What a new character of `house` is granted, in catalog order.
    pub fn starter_items(&self, house: House) -> impl Iterator<Item = &ItemDef> {
        self.items.iter().filter(move |i| i.is_starter_for(house))
    }
}

// ------------------------------------------------------------------ palette

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaletteColour {
    /// Its position in `colours` (written out to make the file reviewable).
    pub index: u8,
    pub id: String,
    pub name: String,
    /// sRGB, `#rrggbb`.
    pub hex: String,
}

/// A house's two colours, as palette indices.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct HouseColours {
    pub primary: u8,
    pub secondary: u8,
}

/// A group of related palette colours: one dye item is brewed
/// per family, and a dye recolours a channel to any colour of its family.
/// Families are referred to by position (`VisibleEffect::Chameleon.family`),
/// so they are only ever appended.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PaletteFamily {
    pub id: String,
    pub name: String,
    /// Palette indices, each in exactly one family.
    pub colours: Vec<u8>,
}

/// `data/items/palette.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Palette {
    pub version: u32,
    pub colours: Vec<PaletteColour>,
    /// House id → its colours.
    pub houses: BTreeMap<String, HouseColours>,
    /// Dye families; together they hold every colour once.
    #[serde(default)]
    pub families: Vec<PaletteFamily>,
    /// The basic palette: plain colours anyone may dye with
    /// for free. Every other colour, and every finish, uses up a brewed dye
    /// (the dye rule, [`Palette::dyes_needed`]). Each family keeps colours
    /// outside this list, so every dye item stays worth brewing.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub basic: Vec<u8>,
}

/// Why a recolouring was refused (`400 invalid_dye` / the dye rule).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DyeError {
    #[error("a dye is outside the palette")]
    OutOfPalette,
    #[error("colour {0} belongs to no dye family")]
    NoFamily(u8),
}

impl Palette {
    pub fn from_json(json: &str) -> Result<Palette, CatalogError> {
        let p: Palette = serde_json::from_str(json)?;
        p.validate()?;
        Ok(p)
    }

    /// Indices match positions, ids are unique, colours are `#rrggbb`, and
    /// every house has colours inside the palette.
    pub fn validate(&self) -> Result<(), CatalogError> {
        if self.version != PALETTE_VERSION {
            return invalid(format!("unsupported palette version {}", self.version));
        }
        if self.colours.is_empty() || self.colours.len() > 256 {
            return invalid("a palette has 1 to 256 colours".into());
        }
        let mut ids = HashSet::new();
        for (i, c) in self.colours.iter().enumerate() {
            if usize::from(c.index) != i {
                return invalid(format!(
                    "colour {:?} has index {} at position {i}",
                    c.id, c.index
                ));
            }
            if !ids.insert(c.id.as_str()) || parse_hex(&c.hex).is_none() {
                return invalid(format!("colour {:?}: duplicate id or bad hex", c.id));
            }
        }
        for h in House::ALL {
            let Some(c) = self.houses.get(h.id()) else {
                return invalid(format!("no colours for house {:?}", h.id()));
            };
            if [c.primary, c.secondary]
                .iter()
                .any(|&i| usize::from(i) >= self.colours.len())
            {
                return invalid(format!("house {:?}: index outside the palette", h.id()));
            }
        }
        if self.houses.len() != House::ALL.len() {
            return invalid("palette lists an unknown house".into());
        }
        if !self.families.is_empty() {
            let mut seen = vec![false; self.colours.len()];
            let mut ids = HashSet::new();
            for f in &self.families {
                if !ids.insert(f.id.as_str()) || f.colours.is_empty() {
                    return invalid(format!("family {:?}: duplicate id or no colours", f.id));
                }
                for &c in &f.colours {
                    match seen.get_mut(usize::from(c)) {
                        Some(s) if !*s => *s = true,
                        _ => {
                            return invalid(format!(
                                "family {:?}: colour {c} is outside the palette or in two families",
                                f.id
                            ));
                        }
                    }
                }
            }
            if let Some(c) = seen.iter().position(|s| !s) {
                return invalid(format!("colour {c} is in no family"));
            }
            if self.families.len() > 256 {
                return invalid("at most 256 families".into());
            }
        }
        let mut basic = HashSet::new();
        for &c in &self.basic {
            if usize::from(c) >= self.colours.len() || !basic.insert(c) {
                return invalid(format!(
                    "basic colour {c} is outside the palette or listed twice"
                ));
            }
        }
        // A family made only of basic colours would leave its dye useless.
        if let Some(f) = self
            .families
            .iter()
            .find(|f| f.colours.iter().all(|c| basic.contains(c)))
        {
            return invalid(format!("family {:?} has only basic colours", f.id));
        }
        Ok(())
    }

    /// Whether colour `index` is in the free basic palette.
    pub fn is_basic(&self, index: u8) -> bool {
        self.basic.contains(&index)
    }

    pub fn house(&self, house: House) -> Option<HouseColours> {
        self.houses.get(house.id()).copied()
    }

    /// Position of family `id` in `families`.
    pub fn family_index(&self, id: &str) -> Option<u8> {
        let i = self.families.iter().position(|f| f.id == id)?;
        u8::try_from(i).ok()
    }

    /// The family (position in `families`) that colour `index` belongs to.
    pub fn family_of(&self, index: u8) -> Option<u8> {
        let i = self
            .families
            .iter()
            .position(|f| f.colours.contains(&index))?;
        u8::try_from(i).ok()
    }

    /// The dye rule: which dyes recolouring an item from
    /// `current` to `wanted` uses up, as `(family index, count)` sorted by
    /// family. A channel costs one dye of the new colour's family when its
    /// colour changes to anything but the item's own default for that
    /// channel (`defaults`, e.g. `ItemDef::granted_dyes`) or a colour of the
    /// basic palette (`basic`); going back to the default, keeping a colour
    /// or picking a basic one is free. A new garment crafted with chosen
    /// dyes pays the same, with `current = defaults`.
    pub fn dyes_needed(
        &self,
        defaults: [u8; 3],
        current: [u8; 3],
        wanted: [u8; 3],
    ) -> Result<Vec<(u8, u32)>, DyeError> {
        let mut need: BTreeMap<u8, u32> = BTreeMap::new();
        for ((&d, &c), &w) in defaults.iter().zip(&current).zip(&wanted) {
            if usize::from(w) >= self.colours.len() {
                return Err(DyeError::OutOfPalette);
            }
            if w == c || w == d || self.is_basic(w) {
                continue;
            }
            let family = self.family_of(w).ok_or(DyeError::NoFamily(w))?;
            *need.entry(family).or_default() += 1;
        }
        Ok(need.into_iter().collect())
    }

    /// The colour of a palette index as sRGB bytes.
    pub fn rgb(&self, index: u8) -> Option<[u8; 3]> {
        self.colours
            .get(usize::from(index))
            .and_then(|c| parse_hex(&c.hex))
    }
}

// ------------------------------------------------------------------ creator

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BodyType {
    /// Its position in `body_types` (= `Appearance::body_type`).
    pub index: u8,
    /// `body_a` / `body_b`: the suffix of fitted asset file names.
    pub id: String,
    /// The creator's label ("Male" / "Female").
    pub name: String,
    pub asset: String,
    /// Height range (metres) the height slider spans, and its default. The
    /// height is a uniform scale of the whole character (version 2).
    pub height_m: [f32; 2],
    pub default_height_m: f32,
    /// The hair style a new character of this body starts with (version 2):
    /// a shipped style of this body.
    pub default_hair_style: u16,
    /// The wear class of this body (`body_a` male, `body_b`
    /// female), checked against a garment's `wear` by [`can_wear`].
    pub wear: Wear,
}

impl BodyType {
    /// Whether this body may wear `item` ([`can_wear`]).
    pub fn can_wear(&self, item: &ItemDef) -> bool {
        can_wear(self.wear, item.wear)
    }

    /// The height (metres) a slider byte means for this body: 0 / 128 / 255
    /// = minimum / default / maximum, piecewise linear.
    pub fn height_metres(&self, byte: u8) -> f32 {
        let [lo, hi] = self.height_m;
        let d = self.default_height_m;
        let n = f32::from(protocol::SLIDER_NEUTRAL);
        let b = f32::from(byte);
        if b <= n {
            lo + (d - lo) * b / n
        } else {
            d + (hi - d) * (b - n) / (255.0 - n)
        }
    }
}

/// Which `Appearance` byte a slider sets.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SliderField {
    Height,
    Face,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Slider {
    pub id: String,
    pub name: String,
    /// UI grouping ("body", "face").
    pub group: String,
    pub field: SliderField,
    /// Index into `face`; absent for `height`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub byte: Option<u8>,
    /// Face sliders: one key, which the byte drives from 0 (at 0) to 1 (at
    /// 255); or two keys, a two-sided slider neutral at `SLIDER_NEUTRAL`
    /// (128), driving the first key toward 0 and the second toward 255.
    /// `height` has none: 0 / 128 / 255 map to the body type's minimum /
    /// default / maximum height, a uniform scale ([`BodyType::height_metres`]).
    pub shape_keys: Vec<String>,
    pub default: u8,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Swatch {
    pub index: u8,
    pub name: String,
    pub hex: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HairStyle {
    /// Stable id, never reused (= `Appearance::hair_style`).
    pub id: u16,
    pub name: String,
    /// Shipped styles: the asset key (files `<asset>_<body>.glb` and
    /// `<asset>_cap_<body>.glb`, for its own body only).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub asset: Option<String>,
    /// Shipped styles: the body type id the style belongs to.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body: Option<String>,
    /// No longer offered and has no asset; looks wearing it are migrated.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub retired: bool,
    /// Retired styles: body type id -> the shipped style of that body that
    /// replaces it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub migrate_to: Option<BTreeMap<String, u16>>,
}

impl HairStyle {
    /// Offered in the creator and drawn (not retired).
    pub fn shipped(&self) -> bool {
        !self.retired && self.asset.is_some()
    }
}

/// `data/characters/creator.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Creator {
    pub version: u32,
    pub body_types: Vec<BodyType>,
    pub sliders: Vec<Slider>,
    pub skin_tones: Vec<Swatch>,
    pub hair_styles: Vec<HairStyle>,
    pub hair_colours: Vec<Swatch>,
    /// Version 2: the iris tint (body dye mask B channel).
    pub eye_colours: Vec<Swatch>,
    pub default_eye_colour: u8,
}

/// Why an appearance was refused (`400 invalid_appearance`).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum AppearanceError {
    #[error("unknown body type")]
    BodyType,
    #[error("unknown skin tone")]
    SkinTone,
    #[error("unknown hair style, a retired one, or one of the other body")]
    HairStyle,
    #[error("unknown hair colour")]
    HairColour,
    #[error("unknown eye colour")]
    EyeColour,
    #[error("the {0:?} slot holds an item that is not clothing for it")]
    Item(GearSlot),
    #[error("a dye is outside the palette")]
    Dye,
}

fn check_swatches(what: &str, swatches: &[Swatch]) -> Result<(), CatalogError> {
    if swatches.is_empty() || swatches.len() > 256 {
        return invalid(format!("{what}: 1 to 256 entries"));
    }
    for (i, s) in swatches.iter().enumerate() {
        if usize::from(s.index) != i || parse_hex(&s.hex).is_none() {
            return invalid(format!("{what} {:?}: index or hex is wrong", s.name));
        }
    }
    Ok(())
}

impl Creator {
    pub fn from_json(json: &str) -> Result<Creator, CatalogError> {
        let c: Creator = serde_json::from_str(json)?;
        c.validate()?;
        Ok(c)
    }

    /// Indices match positions, ids are unique, the sliders cover `height`
    /// (no keys: a uniform scale) and face bytes 0-4 exactly once, every
    /// shipped hair style has an asset and one body, every retired one
    /// migrates to a shipped style of each body, and the defaults exist.
    pub fn validate(&self) -> Result<(), CatalogError> {
        if self.version != CREATOR_VERSION {
            return invalid(format!("unsupported creator version {}", self.version));
        }
        if self.body_types.is_empty() {
            return invalid("no body types".into());
        }
        for wear in [Wear::Female, Wear::Male] {
            if !self.body_types.iter().any(|b| b.wear == wear) {
                return invalid(format!("no body type wears {:?}", wear.id()));
            }
        }
        for (i, b) in self.body_types.iter().enumerate() {
            check_asset_key(&format!("body type {:?}", b.id), &b.asset)?;
            let [lo, hi] = b.height_m;
            if usize::from(b.index) != i || !(lo < b.default_height_m && b.default_height_m < hi) {
                return invalid(format!("body type {:?}: index or heights are wrong", b.id));
            }
        }
        let mut covered = HashSet::new();
        for s in &self.sliders {
            let ok = match (s.field, s.byte) {
                (SliderField::Height, None) => s.shape_keys.is_empty(),
                (SliderField::Face, Some(b)) => b < 5 && (1..=2).contains(&s.shape_keys.len()),
                _ => false,
            };
            if !ok || !covered.insert((s.field, s.byte)) {
                return invalid(format!("slider {:?}: bad byte, keys or a duplicate", s.id));
            }
        }
        if covered.len() != 1 + 5 {
            return invalid("sliders must cover height and face 0-4".into());
        }
        check_swatches("skin tone", &self.skin_tones)?;
        check_swatches("hair colour", &self.hair_colours)?;
        check_swatches("eye colour", &self.eye_colours)?;
        if usize::from(self.default_eye_colour) >= self.eye_colours.len() {
            return invalid("default_eye_colour is not an eye colour".into());
        }
        let mut ids = HashSet::new();
        for h in &self.hair_styles {
            let what = format!("hair style {}", h.id);
            if !ids.insert(h.id) {
                return invalid(format!("{what}: duplicate id"));
            }
            if h.retired {
                if h.asset.is_some() || h.body.is_some() {
                    return invalid(format!("{what}: a retired style has no asset or body"));
                }
                let Some(to) = &h.migrate_to else {
                    return invalid(format!("{what}: a retired style needs migrate_to"));
                };
                if to.len() != self.body_types.len() {
                    return invalid(format!("{what}: migrate_to names every body once"));
                }
                for b in &self.body_types {
                    let target = to.get(&b.id).and_then(|&id| self.hair_style(id));
                    if !target.is_some_and(|t| t.shipped() && t.body.as_deref() == Some(&b.id)) {
                        return invalid(format!(
                            "{what}: migrate_to {:?} is not a shipped style of that body",
                            b.id
                        ));
                    }
                }
            } else {
                let Some(asset) = &h.asset else {
                    return invalid(format!("{what}: a shipped style needs an asset"));
                };
                check_asset_key(&what, asset)?;
                if h.migrate_to.is_some() {
                    return invalid(format!("{what}: only retired styles migrate"));
                }
                if !h
                    .body
                    .as_deref()
                    .is_some_and(|id| self.body_types.iter().any(|b| b.id == id))
                {
                    return invalid(format!("{what}: body must name a body type"));
                }
            }
        }
        for b in &self.body_types {
            if !self
                .styles_of(b.index)
                .any(|h| h.id == b.default_hair_style)
            {
                return invalid(format!(
                    "body type {:?}: default_hair_style is not a shipped style of it",
                    b.id
                ));
            }
        }
        if self.body_types[0].default_hair_style != protocol::HAIR_STYLE_DEFAULT {
            return invalid("Appearance::default()'s hair style is not body 0's default".into());
        }
        Ok(())
    }

    pub fn hair_style(&self, id: u16) -> Option<&HairStyle> {
        self.hair_styles.iter().find(|h| h.id == id)
    }

    /// The shipped hair styles of a body type, in file order.
    pub fn styles_of(&self, body_type: u8) -> impl Iterator<Item = &HairStyle> {
        let body = self
            .body_types
            .get(usize::from(body_type))
            .map(|b| b.id.as_str());
        self.hair_styles
            .iter()
            .filter(move |h| h.shipped() && h.body.is_some() && h.body.as_deref() == body)
    }

    /// The hair style a look of `body_type` wearing `id` should wear: `id`
    /// itself when it is a shipped style of that body, a retired style's
    /// `migrate_to` for the body, otherwise (another body's style, an
    /// unknown id) the body's default. `None` for an unknown body type.
    pub fn migrate_hair(&self, id: u16, body_type: u8) -> Option<u16> {
        let body = self.body_types.get(usize::from(body_type))?;
        let Some(style) = self.hair_style(id) else {
            return Some(body.default_hair_style);
        };
        if style.shipped() {
            return Some(if style.body.as_deref() == Some(&body.id) {
                id
            } else {
                body.default_hair_style
            });
        }
        Some(
            style
                .migrate_to
                .as_ref()
                .and_then(|m| m.get(&body.id).copied())
                .unwrap_or(body.default_hair_style),
        )
    }

    /// Brings a stored look up to this file: the hair style through
    /// [`Creator::migrate_hair`], an unknown eye colour to the default.
    /// Returns whether it changed. Looks are migrated where they are read
    /// from the database, so a row the SQL migration missed still loads.
    pub fn migrate_appearance(&self, a: &mut Appearance) -> bool {
        let before = *a;
        if let Some(h) = self.migrate_hair(a.hair_style, a.body_type) {
            a.hair_style = h;
        }
        if usize::from(a.eye_colour) >= self.eye_colours.len() {
            a.eye_colour = self.default_eye_colour;
        }
        *a != before
    }

    /// The default appearance of a body type (`0` when unknown) with every
    /// slider at its creator default, the body's default hair style and the
    /// default eye colour.
    pub fn default_appearance(&self, body_type: u8) -> Appearance {
        let body_type = if usize::from(body_type) < self.body_types.len() {
            body_type
        } else {
            0
        };
        let mut a = Appearance {
            body_type,
            hair_style: self.body_types[usize::from(body_type)].default_hair_style,
            eye_colour: self.default_eye_colour,
            ..Appearance::default()
        };
        for s in &self.sliders {
            match (s.field, s.byte) {
                (SliderField::Height, _) => a.height = s.default,
                (SliderField::Face, Some(b)) => a.face[usize::from(b)] = s.default,
                _ => {}
            }
        }
        a
    }

    /// Whether a character of `body_type` may wear `item`
    /// (false for an unknown body type).
    pub fn can_wear(&self, body_type: u8, item: &ItemDef) -> bool {
        self.body_types
            .get(usize::from(body_type))
            .is_some_and(|b| b.can_wear(item))
    }

    /// Checks that every index and id in `a` exists: body type, skin tone,
    /// a shipped hair style of the look's own body, hair and eye colour in
    /// this file, each worn item as clothing for its slot in `catalog`, and
    /// each dye in `palette`. Slider bytes are always valid. Ownership of
    /// the worn items is the server's business.
    pub fn check_appearance(
        &self,
        a: &Appearance,
        catalog: &Catalog,
        palette: &Palette,
    ) -> Result<(), AppearanceError> {
        if usize::from(a.body_type) >= self.body_types.len() {
            return Err(AppearanceError::BodyType);
        }
        if usize::from(a.skin_tone) >= self.skin_tones.len() {
            return Err(AppearanceError::SkinTone);
        }
        if !self.styles_of(a.body_type).any(|h| h.id == a.hair_style) {
            return Err(AppearanceError::HairStyle);
        }
        if usize::from(a.hair_colour) >= self.hair_colours.len() {
            return Err(AppearanceError::HairColour);
        }
        if usize::from(a.eye_colour) >= self.eye_colours.len() {
            return Err(AppearanceError::EyeColour);
        }
        for slot in GearSlot::ALL {
            let Some(Worn { item_def, dyes, .. }) = a.outfit.get(slot) else {
                continue;
            };
            if catalog.clothing(item_def, slot).is_none() {
                return Err(AppearanceError::Item(slot));
            }
            if dyes
                .iter()
                .any(|&d| usize::from(d) >= palette.colours.len())
            {
                return Err(AppearanceError::Dye);
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PALETTE: &str = include_str!("../../../data/items/palette.json");
    const CATALOG: &str = include_str!("../../../data/items/catalog.json");
    const CREATOR: &str = include_str!("../../../data/characters/creator.json");

    fn shipped() -> (Palette, Catalog, Creator) {
        let palette = Palette::from_json(PALETTE).unwrap();
        let catalog = Catalog::from_json(CATALOG, &palette).unwrap();
        let creator = Creator::from_json(CREATOR).unwrap();
        (palette, catalog, creator)
    }

    /// The eight restricted garments, the body classes and
    /// the one rule.
    #[test]
    fn the_wear_rule() {
        let (_, catalog, creator) = shipped();
        assert_eq!(creator.body_types[0].wear, Wear::Male);
        assert_eq!(creator.body_types[1].wear, Wear::Female);
        let restricted: Vec<(u16, Wear)> = catalog
            .items
            .iter()
            .filter_map(|i| i.wear.map(|w| (i.id, w)))
            .collect();
        let female = [111, 112, 218, 302, 304, 306, 407];
        assert_eq!(restricted.len(), 8);
        for (id, w) in &restricted {
            let want = if female.contains(id) {
                Wear::Female
            } else {
                Wear::Male
            };
            assert_eq!(*w, want, "item {id}");
        }
        assert!(restricted.contains(&(305, Wear::Male)), "the kilt");
        // The rule: unrestricted for anyone, restricted for its class only.
        assert!(can_wear(Wear::Male, None) && can_wear(Wear::Female, None));
        assert!(can_wear(Wear::Female, Some(Wear::Female)));
        assert!(!can_wear(Wear::Male, Some(Wear::Female)));
        assert!(!can_wear(Wear::Female, Some(Wear::Male)));
        let skirt = catalog.get(302).unwrap();
        let kilt = catalog.get(305).unwrap();
        let trousers = catalog.get(301).unwrap();
        assert!(creator.can_wear(1, skirt) && !creator.can_wear(0, skirt));
        assert!(creator.can_wear(0, kilt) && !creator.can_wear(1, kilt));
        assert!(creator.can_wear(0, trousers) && creator.can_wear(1, trousers));
        assert!(!creator.can_wear(9, trousers), "unknown body type");
        assert_eq!(Wear::Female.label(), "Female only");
        assert_eq!(Wear::from_id("male"), Some(Wear::Male));
        // Only clothing may be restricted; every class has a body.
        let mut bad = catalog.clone();
        bad.items
            .iter_mut()
            .find(|i| i.kind != ItemKind::Clothing)
            .unwrap()
            .wear = Some(Wear::Male);
        let palette = Palette::from_json(PALETTE).unwrap();
        assert!(
            bad.validate(&palette)
                .unwrap_err()
                .to_string()
                .contains("wear")
        );
        let mut one = creator.clone();
        one.body_types[1].wear = Wear::Male;
        assert!(one.validate().unwrap_err().to_string().contains("female"));
    }

    /// Decorating: every flat-topped piece offers a top, every seat-like one
    /// a seat, and the tops turn with their piece.
    #[test]
    fn furniture_tops_and_seats() {
        let (palette, catalog, _) = shipped();
        let furniture = |tag: &str| -> Vec<(&str, &FurnitureDef)> {
            catalog
                .items
                .iter()
                .filter(|i| i.tags.iter().any(|t| t == tag))
                .map(|i| (i.name.as_str(), i.furniture.as_ref().unwrap()))
                .collect()
        };
        for (name, f) in furniture("table").into_iter().chain(furniture("storage")) {
            assert!(f.top.is_some(), "{name} has a flat top");
        }
        for (name, f) in furniture("seating").into_iter().chain(furniture("bed")) {
            assert!(!f.seats.is_empty(), "{name} is a seat");
        }
        // The bookcase: its top and the free ends of two shelves, with headroom.
        let bookcase = catalog
            .items
            .iter()
            .find(|i| i.asset.ends_with("bookcase_oak_a"))
            .unwrap();
        let areas = bookcase
            .furniture
            .as_ref()
            .unwrap()
            .top_areas("top_7", [1.0, 0.0, 2.0], 0.0);
        assert_eq!(areas.len(), 3);
        assert_eq!(areas[1].id, "top_7.shelf0");
        assert!(areas.iter().all(|a| a.validate().is_ok()));
        assert_eq!(areas[1].clear, Some(0.39));
        let lantern = catalog
            .items
            .iter()
            .find(|i| i.asset.ends_with("lantern_candle_a"))
            .unwrap();
        let cage = catalog
            .items
            .iter()
            .find(|i| i.asset.ends_with("cage_owl_a"))
            .unwrap();
        let shape = |i: &ItemDef| i.furniture.as_ref().unwrap().shape();
        let on_shelf = [1.0 + 0.372, 0.525, 2.0];
        assert!(crate::check_placement(&areas, &shape(lantern), on_shelf, 0.0).is_ok());
        assert!(
            crate::check_placement(&areas, &shape(cage), on_shelf, 0.0).is_err(),
            "too tall"
        );
        assert!(
            crate::check_placement(&areas, &shape(cage), [1.0, 2.0, 2.0], 0.0).is_ok(),
            "on top"
        );
        // Rules: a top area needs a top and stays on the footprint; shelves
        // sit under the top; wall items have neither.
        let mut bad = catalog.clone();
        let f = bad
            .items
            .iter_mut()
            .find(|i| i.id == bookcase.id)
            .unwrap()
            .furniture
            .as_mut()
            .unwrap();
        f.top_area = Some([-0.9, -0.2, 0.5, 0.2]);
        assert!(
            bad.validate(&palette)
                .unwrap_err()
                .to_string()
                .contains("top_area")
        );
        let mut bad = catalog.clone();
        let f = bad
            .items
            .iter_mut()
            .find(|i| i.id == bookcase.id)
            .unwrap()
            .furniture
            .as_mut()
            .unwrap();
        f.shelves[0].clear = 1.6;
        assert!(
            bad.validate(&palette)
                .unwrap_err()
                .to_string()
                .contains("shelf")
        );
        // Seats say how far they reach (the sitter's legs are fitted to
        // them), the top no further above the floor than the seat's own.
        let chair = catalog
            .items
            .iter()
            .find(|i| i.asset.ends_with("chair_wooden_a"))
            .unwrap();
        let seat = chair.furniture.as_ref().unwrap().seats[0];
        assert!(seat.front > 0.0 && seat.back > 0.0 && seat.drop == seat.y);
        for broken in [
            FurnitureSeat {
                front: -0.1,
                ..seat
            },
            FurnitureSeat {
                drop: seat.y + 0.2,
                ..seat
            },
            FurnitureSeat {
                back: f32::NAN,
                ..seat
            },
        ] {
            let mut bad = catalog.clone();
            bad.items
                .iter_mut()
                .find(|i| i.id == chair.id)
                .unwrap()
                .furniture
                .as_mut()
                .unwrap()
                .seats[0] = broken;
            assert!(
                bad.validate(&palette)
                    .unwrap_err()
                    .to_string()
                    .contains("seat")
            );
        }
    }

    #[test]
    fn the_shipped_files_are_valid() {
        let (palette, catalog, creator) = shipped();
        assert!((40..=64).contains(&palette.colours.len()));
        assert_eq!(creator.body_types.len(), 2);
        assert!(catalog.items.iter().all(|i| !i.name.is_empty()));
    }

    #[test]
    fn every_house_gets_a_full_starter_set() {
        let (palette, catalog, creator) = shipped();
        for house in House::ALL {
            let starter: Vec<_> = catalog.starter_items(house).collect();
            let clothing = starter
                .iter()
                .filter(|i| i.kind == ItemKind::Clothing)
                .count();
            let furniture = starter
                .iter()
                .filter(|i| i.kind == ItemKind::Furniture)
                .count();
            assert!(
                (12..=16).contains(&clothing),
                "{house:?}: {clothing} clothing items"
            );
            assert!(
                (10..=14).contains(&furniture),
                "{house:?}: {furniture} furniture items"
            );
            // The creator preselects one starter item in each of body, legs and shoes.
            for slot in [GearSlot::Body, GearSlot::Legs, GearSlot::Shoes] {
                let defaults = starter
                    .iter()
                    .filter(|i| i.slot == Some(slot) && i.has_tag(TAG_DEFAULT))
                    .count();
                assert_eq!(defaults, 1, "{house:?} {slot:?}");
            }
            // A house's own items carry its colours.
            let colours = palette.house(house).unwrap();
            let dyed = starter.iter().find(|i| i.house_dyes.is_some()).unwrap();
            let dyes = dyed.granted_dyes(house, &palette);
            assert!(dyes.contains(&colours.primary) || dyes.contains(&colours.secondary));
            // And the default look made of them is valid.
            let mut a = creator.default_appearance(house as u8 % 2);
            for item in starter.iter().filter(|i| i.has_tag(TAG_DEFAULT)) {
                let worn = Worn::new(item.id, item.granted_dyes(house, &palette));
                a.outfit.set(item.slot.unwrap(), Some(worn));
            }
            assert_eq!(creator.check_appearance(&a, &catalog, &palette), Ok(()));
        }
    }

    #[test]
    fn appearances_are_checked_against_the_data() {
        let (palette, catalog, creator) = shipped();
        let ok = creator.default_appearance(0);
        assert_eq!(creator.check_appearance(&ok, &catalog, &palette), Ok(()));
        let female = creator.default_appearance(1);
        assert_eq!(female.hair_style, 17);
        assert_eq!(female.eye_colour, 1);
        assert_eq!(
            creator.check_appearance(&female, &catalog, &palette),
            Ok(())
        );
        let check = |f: &dyn Fn(&mut Appearance)| {
            let mut a = ok;
            f(&mut a);
            creator.check_appearance(&a, &catalog, &palette)
        };
        assert_eq!(check(&|a| a.body_type = 9), Err(AppearanceError::BodyType));
        assert_eq!(
            check(&|a| a.skin_tone = 200),
            Err(AppearanceError::SkinTone)
        );
        // Mocha (9) is the darkest tone; Espresso and Ebony (10, 11) are gone.
        assert_eq!(check(&|a| a.skin_tone = 9), Ok(()));
        assert_eq!(check(&|a| a.skin_tone = 10), Err(AppearanceError::SkinTone));
        assert_eq!(
            check(&|a| a.hair_style = 60_000),
            Err(AppearanceError::HairStyle)
        );
        assert_eq!(
            check(&|a| a.hair_colour = 200),
            Err(AppearanceError::HairColour)
        );
        // Every colour is an ordinary index (one list each).
        assert_eq!(check(&|a| a.hair_colour = 19), Ok(()));
        assert_eq!(check(&|a| a.eye_colour = 12), Ok(()));
        assert_eq!(
            check(&|a| a.eye_colour = 13),
            Err(AppearanceError::EyeColour)
        );
        // A retired style, or a style of the other body, is refused.
        assert_eq!(
            check(&|a| a.hair_style = 4),
            Err(AppearanceError::HairStyle)
        );
        assert_eq!(
            check(&|a| a.hair_style = 17),
            Err(AppearanceError::HairStyle)
        );
        assert_eq!(check(&|a| a.hair_style = 16), Ok(()));
        let hat = catalog
            .items
            .iter()
            .find(|i| i.slot == Some(GearSlot::Hat))
            .unwrap()
            .id;
        let worn = |item_def, dye| Some(Worn::new(item_def, [dye, 0, 0]));
        assert_eq!(
            check(&|a| a.outfit.shoes = worn(hat, 0)),
            Err(AppearanceError::Item(GearSlot::Shoes))
        );
        assert_eq!(
            check(&|a| a.outfit.hat = worn(hat, 255)),
            Err(AppearanceError::Dye)
        );
        assert_eq!(check(&|a| a.outfit.hat = worn(hat, 3)), Ok(()));
    }

    #[test]
    fn creator_v2_bodies_hair_and_eyes() {
        let (_, _, c) = shipped();
        assert_eq!(c.version, 2);
        let names: Vec<_> = c.body_types.iter().map(|b| b.name.as_str()).collect();
        assert_eq!(names, ["Male", "Female"]);
        // Height: a uniform scale, 0 / 128 / 255 = min / default / max.
        let male = &c.body_types[0];
        let female = &c.body_types[1];
        let close = |a: f32, b: f32| (a - b).abs() < 1e-4;
        assert!(close(male.height_metres(0), 1.60));
        assert!(close(male.height_metres(128), 1.78));
        assert!(close(male.height_metres(255), 1.88));
        assert!(close(female.height_metres(0), 1.52));
        assert!(close(female.height_metres(128), 1.68));
        assert!(close(female.height_metres(255), 1.80));
        let height = c
            .sliders
            .iter()
            .find(|s| s.field == SliderField::Height)
            .unwrap();
        assert!(height.shape_keys.is_empty());
        assert!(c.sliders.iter().all(|s| !s.id.starts_with("build")));
        // The shipped styles per body (the second set of each appended; id
        // 25 was removed on 2026-11-07 and is never reused), every old id
        // retired.
        let of = |b| c.styles_of(b).map(|h| h.id).collect::<Vec<_>>();
        assert_eq!(of(0), [14, 15, 16, 20, 21, 22]);
        assert_eq!(of(1), [17, 18, 19, 23, 24]);
        assert!(c.hair_style(25).is_none());
        assert!((0..14).all(|id| c.hair_style(id).is_some_and(|h| h.retired)));
        // Ten skin tones, Porcelain to Mocha (Espresso and Ebony were removed:
        // their faces went near black at night).
        let skins: Vec<_> = c.skin_tones.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(skins.len(), 10);
        assert_eq!((skins[0], skins[9]), ("Porcelain", "Mocha"));
        // Colours (one list each, no natural/fantasy split):
        // 20 hair shades, 13 eye colours, indices unchanged.
        assert_eq!(c.hair_colours.len(), 20);
        assert_eq!(c.eye_colours.len(), 13);
        assert_eq!(
            c.eye_colours[usize::from(c.default_eye_colour)].name,
            "Brown"
        );
        assert_eq!(c.default_eye_colour, protocol::EYE_COLOUR_DEFAULT);
    }

    #[test]
    fn old_hair_styles_migrate_per_body() {
        let (palette, catalog, c) = shipped();
        // The contract's table (docs/art/character-adoption.md section 1).
        let table: [(u16, u16, u16); 14] = [
            (0, 14, 19),
            (1, 14, 19),
            (2, 15, 19),
            (3, 16, 19),
            (4, 16, 17),
            (5, 16, 18),
            (6, 16, 18),
            (7, 16, 18),
            (8, 14, 19),
            (9, 16, 17),
            (10, 14, 19),
            (11, 15, 19),
            (12, 14, 19),
            (13, 16, 17),
        ];
        for (old, male, female) in table {
            assert_eq!(c.migrate_hair(old, 0), Some(male), "old {old} male");
            assert_eq!(c.migrate_hair(old, 1), Some(female), "old {old} female");
        }
        // New ids map to themselves on their body, to the default elsewhere.
        for id in [14, 15, 16, 20, 21, 22] {
            assert_eq!(c.migrate_hair(id, 0), Some(id));
            assert_eq!(c.migrate_hair(id, 1), Some(17));
        }
        // A removed id (25) is unknown: the body's default.
        assert_eq!(c.migrate_hair(25, 1), Some(17));
        assert_eq!(c.migrate_hair(25, 0), Some(14));
        for id in [17, 18, 19, 23, 24] {
            assert_eq!(c.migrate_hair(id, 1), Some(id));
            assert_eq!(c.migrate_hair(id, 0), Some(14));
        }
        assert_eq!(c.migrate_hair(9_999, 1), Some(17));
        assert_eq!(c.migrate_hair(3, 7), None);
        // A whole stored look: hair mapped, eyes kept or defaulted, and the
        // result passes the check; migrating again changes nothing.
        let mut a = Appearance {
            body_type: 1,
            hair_style: 6,
            eye_colour: 200,
            ..c.default_appearance(1)
        };
        assert!(c.migrate_appearance(&mut a));
        assert_eq!((a.hair_style, a.eye_colour), (18, 1));
        assert_eq!(c.check_appearance(&a, &catalog, &palette), Ok(()));
        assert!(!c.migrate_appearance(&mut a));
    }

    #[test]
    fn rejects_broken_creator_files() {
        let base: serde_json::Value = serde_json::from_str(CREATOR).unwrap();
        let err = |f: &dyn Fn(&mut serde_json::Value)| {
            let mut v = base.clone();
            f(&mut v);
            match Creator::from_json(&v.to_string()) {
                Ok(_) => String::new(),
                Err(e) => e.to_string(),
            }
        };
        assert_eq!(err(&|_| {}), "");
        assert!(err(&|v| v["version"] = 1.into()).contains("version"));
        // A build slider (version 1) does not parse.
        assert!(
            !err(&|v| {
                v["sliders"].as_array_mut().unwrap().push(serde_json::json!({
            "id": "build_slim", "name": "Slim", "group": "build", "field": "build", "byte": 0,
            "shape_keys": ["build_slim"], "default": 0
        }))
            })
            .is_empty()
        );
        // Height takes no shape keys.
        assert!(
            err(&|v| v["sliders"][0]["shape_keys"] = serde_json::json!(["height_tall"]))
                .contains("slider")
        );
        // A retired style must migrate to a shipped style of each body (the
        // first retired entry follows the twelve shipped ones).
        assert!(
            err(&|v| v["hair_styles"][12]["migrate_to"]["body_a"] = 17.into())
                .contains("migrate_to")
        );
        assert!(
            err(&|v| {
                v["hair_styles"][12]["migrate_to"]
                    .as_object_mut()
                    .unwrap()
                    .remove("body_b");
            })
            .contains("migrate_to")
        );
        assert!(err(&|v| v["hair_styles"][12]["asset"] = "hair/x".into()).contains("retired"));
        // A shipped style needs an asset and a body.
        assert!(err(&|v| v["hair_styles"][0]["body"] = "body_z".into()).contains("body"));
        assert!(
            err(&|v| {
                v["hair_styles"][0].as_object_mut().unwrap().remove("asset");
            })
            .contains("asset")
        );
        // Defaults must exist.
        assert!(
            err(&|v| v["body_types"][1]["default_hair_style"] = 14.into())
                .contains("default_hair_style")
        );
        assert!(err(&|v| v["default_eye_colour"] = 13.into()).contains("eye"));
        assert!(err(&|v| v["eye_colours"][2]["hex"] = "#12345".into()).contains("eye colour"));
    }

    #[test]
    fn rejects_broken_catalogs() {
        let palette = Palette::from_json(PALETTE).unwrap();
        let item = r#"{ "id": 1, "kind": "clothing", "slot": "hat", "name": "Hat",
                        "asset": "gear/hat/a", "dyes": { "primary": 0, "secondary": 0, "trim": 0 },
                        "exchange": { "category": "hats", "subcategory": "hats" } }"#;
        let catalog = |items: &str| format!(r#"{{ "version": 2, "items": [{items}] }}"#);
        assert!(Catalog::from_json(&catalog(item), &palette).is_ok());
        let err = |json: String| Catalog::from_json(&json, &palette).unwrap_err().to_string();
        assert!(err(catalog(&format!("{item}, {item}"))).contains("duplicate id"));
        assert!(err(catalog(&item.replace("gear/hat/a", "Gear/Hat"))).contains("asset key"));
        assert!(err(catalog(&item.replace(r#""slot": "hat", "#, ""))).contains("needs a slot"));
        assert!(err(catalog(&item.replace(r#""trim": 0"#, r#""trim": 250"#))).contains("palette"));
        let tagged = item.replace(r#""name""#, r#""tags": ["house:no_such_house"], "name""#);
        assert!(err(catalog(&tagged)).contains("unknown house"));
        let chair = r#"{ "id": 2, "kind": "furniture", "name": "Chair", "asset": "furniture/chair",
                         "dyes": { "primary": 0, "secondary": 0, "trim": 0 },
                         "exchange": { "category": "furniture", "subcategory": "seating" },
                         "furniture": { "surface": "floor", "footprint": [0.5, 0.5], "height": 0 } }"#;
        assert!(err(catalog(chair)).contains("dimensions"));
    }

    #[test]
    fn rejects_broken_economy_fields() {
        let palette = Palette::from_json(PALETTE).unwrap();
        let wool = r#"{ "id": 3001, "kind": "material", "name": "Wool", "asset": "icons/material/wool",
                        "dyes": { "primary": 9, "secondary": 9, "trim": 9 },
                        "stackable": true, "max_stack": 99,
                        "exchange": { "category": "materials", "subcategory": "fibers" } }"#;
        let catalog = |items: &str| format!(r#"{{ "version": 2, "items": [{items}] }}"#);
        let ok = Catalog::from_json(&catalog(wool), &palette).unwrap();
        assert_eq!(ok.items[0].stack_limit(), 99);
        assert_eq!(ok.items[0].required_level, 1);
        let err = |json: String| Catalog::from_json(&json, &palette).unwrap_err().to_string();
        assert!(err(catalog(&wool.replace(r#""stackable": true, "#, ""))).contains("stackable"));
        assert!(
            err(catalog(
                &wool.replace(r#""max_stack": 99"#, r#""max_stack": 1"#)
            ))
            .contains("max_stack")
        );
        assert!(err(catalog(&wool.replace("\"fibers\"", "\"patterns\""))).contains("patterns"));
        assert!(
            err(catalog(&wool.replace("\"materials\"", "\"dyes\""))).contains("Exchange class")
        );
        assert!(err(catalog(&wool.replace("\"fibers\"", "\"robes\""))).contains("Exchange class"));
        let priced = wool.replace(r#""stackable""#, r#""price": 0, "stackable""#);
        assert!(err(catalog(&priced)).contains("price"));
        let themed = wool.replace(
            r#""stackable""#,
            r#""house_theme": "no_such_house", "stackable""#,
        );
        assert!(err(catalog(&themed)).contains("house_theme"));
        let levelled = wool.replace(r#""stackable""#, r#""required_level": 21, "stackable""#);
        assert!(err(catalog(&levelled)).contains("required_level"));
        let dyed = wool.replace(
            r#""stackable""#,
            r#""dye": { "family": "reds" }, "stackable""#,
        );
        assert!(err(catalog(&dyed)).contains("`dye`"));

        let dye = r#"{ "id": 3101, "kind": "dye", "name": "Red Dye", "asset": "icons/dye/red",
                       "dyes": { "primary": 16, "secondary": 16, "trim": 16 },
                       "stackable": true, "max_stack": 20, "dye": { "family": "reds" },
                       "exchange": { "category": "dyes", "subcategory": "dyes" } }"#;
        assert!(Catalog::from_json(&catalog(dye), &palette).is_ok());
        assert!(err(catalog(&dye.replace("\"reds\"", "\"plaids\""))).contains("family"));
        assert!(
            err(catalog(&dye.replace(
                r#""subcategory": "dyes""#,
                r#""subcategory": "finishes""#
            )))
            .contains("finishes")
        );

        let potion = r#"{ "id": 3301, "kind": "potion", "name": "Glow", "asset": "icons/potion/glow",
                          "dyes": { "primary": 21, "secondary": 21, "trim": 21 },
                          "stackable": true, "max_stack": 10, "potion": { "effect": "glow" },
                          "exchange": { "category": "potions", "subcategory": "glow" } }"#;
        assert!(Catalog::from_json(&catalog(potion), &palette).is_ok());
        assert!(
            err(catalog(&potion.replace(
                r#""subcategory": "glow""#,
                r#""subcategory": "fortunes_draught""#
            )))
            .contains("effect")
        );
        let chameleon = potion
            .replace("\"effect\": \"glow\"", "\"effect\": \"chameleon\"")
            .replace(r#""subcategory": "glow""#, r#""subcategory": "chameleon""#);
        assert!(err(catalog(&chameleon)).contains("part"));

        // A family of one, and a family across slots, are refused.
        let hat = |id: u16, slot: &str, cat: &str, sub: &str| {
            format!(
                r#"{{ "id": {id}, "kind": "clothing", "slot": "{slot}", "name": "Item {id}",
                   "asset": "gear/{slot}/item_{id}", "dyes": {{ "primary": 0, "secondary": 0, "trim": 0 }},
                   "family": "felt", "exchange": {{ "category": "{cat}", "subcategory": "{sub}" }} }}"#
            )
        };
        let one = hat(101, "hat", "hats", "hats");
        assert!(err(catalog(&one)).contains("restyle family"));
        let two = format!("{one}, {}", hat(102, "hat", "hats", "hats"));
        let family = Catalog::from_json(&catalog(&two), &palette).unwrap();
        assert_eq!(family.family("felt").count(), 2);
        let mixed = format!("{one}, {}", hat(201, "body", "body", "robes"));
        assert!(err(catalog(&mixed)).contains("restyle family"));
    }

    #[test]
    fn palette_families_cover_every_colour_once() {
        let palette = Palette::from_json(PALETTE).unwrap();
        assert!(palette.families.len() >= 8);
        for c in 0..palette.colours.len() as u8 {
            assert!(palette.family_of(c).is_some(), "colour {c}");
        }
        let mut broken = palette.clone();
        let taken = broken.families[1].colours[0];
        broken.families[0].colours.push(taken);
        assert!(
            broken
                .validate()
                .unwrap_err()
                .to_string()
                .contains("two families")
        );
        let mut broken = palette.clone();
        broken.families[0].colours.pop();
        assert!(
            broken
                .validate()
                .unwrap_err()
                .to_string()
                .contains("in no family")
        );
    }

    #[test]
    fn the_dye_rule_charges_one_dye_per_changed_channel() {
        let palette = Palette::from_json(PALETTE).unwrap();
        let fam = |c: u8| palette.family_of(c).unwrap();
        let defaults = [0, 16, 21];
        // Keeping colours or going back to the defaults is free.
        assert_eq!(
            palette.dyes_needed(defaults, defaults, defaults),
            Ok(vec![])
        );
        assert_eq!(
            palette.dyes_needed(defaults, [33, 34, 21], defaults),
            Ok(vec![])
        );
        // Two channels to blues cost two blue dyes; one to gold one gold dye.
        let need = palette
            .dyes_needed(defaults, defaults, [33, 34, 22])
            .unwrap();
        let mut expected = vec![(fam(33), 2), (fam(22), 1)];
        expected.sort();
        assert_eq!(need, expected);
        assert_eq!(
            palette.dyes_needed(defaults, defaults, [200, 0, 0]),
            Err(DyeError::OutOfPalette)
        );
    }

    #[test]
    fn basic_colours_are_free_and_every_family_keeps_paid_ones() {
        let palette = Palette::from_json(PALETTE).unwrap();
        assert!(palette.basic.len() >= 8, "a usable free palette");
        let basic: Vec<u8> = palette.basic.iter().copied().take(3).collect();
        let wanted = [basic[0], basic[1], basic[2]];
        let defaults = [5, 5, 5];
        assert_eq!(palette.dyes_needed(defaults, defaults, wanted), Ok(vec![]));
        // Every family has a colour that costs its dye.
        for f in &palette.families {
            assert!(f.colours.iter().any(|&c| !palette.is_basic(c)), "{}", f.id);
        }
        let mut broken = palette.clone();
        broken.basic.push(broken.basic[0]);
        assert!(broken.validate().unwrap_err().to_string().contains("twice"));
        let mut broken = palette.clone();
        broken.basic.extend(broken.families[0].colours.clone());
        broken.basic.sort_unstable();
        broken.basic.dedup();
        assert!(
            broken
                .validate()
                .unwrap_err()
                .to_string()
                .contains("only basic")
        );
    }
}
