//! What a character looks like: the character-creator choices plus the four
//! worn gear slots. Sent once when an entity enters a client's visible set
//! (`EntityInfo`), again whenever it changes (`ServerMsg::AppearanceChanged`),
//! and stored as JSON in `characters.appearance` (the same serde shape).
//!
//! Every field is an index or an id into a data file, never a free value, so
//! the struct stays small (about 30-40 bytes in postcard) and the server can
//! validate it against the data:
//!
//! - `body_type`, the slider bytes, `skin_tone`, `hair_style`,
//!   `hair_colour` and `eye_colour` refer to `data/characters/creator.json`;
//! - [`Worn::item_def`] is a stable item id from `data/items/catalog.json`;
//! - [`Worn::dyes`] are indices into `data/items/palette.json`;
//! - [`Worn::finish`] is a brewed [`Finish`].

use serde::{Deserialize, Serialize};

/// Neutral value of a slider byte (the middle of its range).
pub const SLIDER_NEUTRAL: u8 = 128;

/// The eye colour of looks stored before eye colours existed (Brown,
/// `creator.json` `default_eye_colour`).
pub const EYE_COLOUR_DEFAULT: u8 = 1;

/// The hair style of [`Appearance::default`]: the default style of body
/// type 0 (`creator.json` `body_types[0].default_hair_style`).
pub const HAIR_STYLE_DEFAULT: u16 = 14;

fn eye_colour_default() -> u8 {
    EYE_COLOUR_DEFAULT
}

/// One of the four gear slots, in wire and database order.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GearSlot {
    Hat,
    Body,
    Legs,
    Shoes,
}

impl GearSlot {
    pub const ALL: [GearSlot; 4] = [
        GearSlot::Hat,
        GearSlot::Body,
        GearSlot::Legs,
        GearSlot::Shoes,
    ];

    /// The slot's id in data files, JSON and the database (`"hat"`, `"body"`, ...).
    pub fn id(self) -> &'static str {
        match self {
            GearSlot::Hat => "hat",
            GearSlot::Body => "body",
            GearSlot::Legs => "legs",
            GearSlot::Shoes => "shoes",
        }
    }

    pub fn from_id(id: &str) -> Option<GearSlot> {
        GearSlot::ALL.into_iter().find(|s| s.id() == id)
    }
}

/// A brewed finish applied to a garment: consumed when
/// applied, stored per item instance (`items.finish`) and shown to everyone.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Finish {
    #[default]
    None,
    /// A soft sheen over the dyed channels.
    Shimmer,
    /// The dyed channels drift through neighbouring hues.
    ColourShift,
}

impl Finish {
    pub const ALL: [Finish; 3] = [Finish::None, Finish::Shimmer, Finish::ColourShift];

    /// The id used in data files, JSON and the database (`items.finish`).
    pub fn id(self) -> &'static str {
        match self {
            Finish::None => "none",
            Finish::Shimmer => "shimmer",
            Finish::ColourShift => "colour_shift",
        }
    }

    pub fn from_id(id: &str) -> Option<Finish> {
        Finish::ALL.into_iter().find(|f| f.id() == id)
    }
}

/// An item as worn: which catalog item, in which colours, with which finish.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Worn {
    /// Stable item id (`data/items/catalog.json` `id`).
    pub item_def: u16,
    /// Palette indices for the primary, secondary and trim dye channels.
    pub dyes: [u8; 3],
    /// Absent in looks stored before finishes existed (`none`).
    #[serde(default)]
    pub finish: Finish,
}

impl Worn {
    /// An item without a finish.
    pub fn new(item_def: u16, dyes: [u8; 3]) -> Self {
        Self {
            item_def,
            dyes,
            finish: Finish::None,
        }
    }
}

/// The worn gear, one optional item per slot.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Outfit {
    pub hat: Option<Worn>,
    pub body: Option<Worn>,
    pub legs: Option<Worn>,
    pub shoes: Option<Worn>,
}

impl Outfit {
    pub fn get(&self, slot: GearSlot) -> Option<Worn> {
        match slot {
            GearSlot::Hat => self.hat,
            GearSlot::Body => self.body,
            GearSlot::Legs => self.legs,
            GearSlot::Shoes => self.shoes,
        }
    }

    pub fn set(&mut self, slot: GearSlot, worn: Option<Worn>) {
        match slot {
            GearSlot::Hat => self.hat = worn,
            GearSlot::Body => self.body = worn,
            GearSlot::Legs => self.legs = worn,
            GearSlot::Shoes => self.shoes = worn,
        }
    }
}

/// A character's look. Slider bytes map 0..=255 linearly onto the range the
/// creator file gives that slider; [`SLIDER_NEUTRAL`] is the body's default.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Appearance {
    /// Index into `creator.json` `body_types` (0 = `body_a`, Male; 1 =
    /// `body_b`, Female). Both share one skeleton.
    pub body_type: u8,
    /// Height slider (`creator.json` slider `height`): 0 / 128 / 255 = the
    /// body type's minimum / default / maximum height, a uniform scale of the
    /// whole character. There is no build any more (protocol 6): every body
    /// is the neutral build, and a stored look's old `build` field is ignored.
    pub height: u8,
    /// Face sliders in the order jaw, cheek, nose, brow, eye shape
    /// (`creator.json` sliders `face_jaw` ... `face_eyes`).
    pub face: [u8; 5],
    /// Index into `creator.json` `skin_tones`.
    pub skin_tone: u8,
    /// Stable hair style id (`creator.json` `hair_styles[].id`).
    pub hair_style: u16,
    /// Index into `creator.json` `hair_colours`.
    pub hair_colour: u8,
    /// Index into `creator.json` `eye_colours`. Absent in looks stored
    /// before it: [`EYE_COLOUR_DEFAULT`] (Brown).
    #[serde(default = "eye_colour_default")]
    pub eye_colour: u8,
    pub outfit: Outfit,
}

impl Default for Appearance {
    /// The neutral look: the first body, every slider at its default, the
    /// first skin tone and hair colour, the first body's default hair style,
    /// Brown eyes, and nothing worn. Used for
    /// characters made before appearances existed until the
    /// server gives them their starter outfit.
    fn default() -> Self {
        Self {
            body_type: 0,
            height: SLIDER_NEUTRAL,
            face: [SLIDER_NEUTRAL; 5],
            skin_tone: 0,
            hair_style: HAIR_STYLE_DEFAULT,
            hair_colour: 0,
            eye_colour: EYE_COLOUR_DEFAULT,
            outfit: Outfit::default(),
        }
    }
}
