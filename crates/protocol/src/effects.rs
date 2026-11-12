//! Potion effects: what a character drank, and what everyone
//! around them sees.
//!
//! Effects are short (5-20 minutes), mostly social or cosmetic, survive a
//! relog until they expire (`active_effects` rows) and are shown to other
//! players with the character's look:
//!
//! - [`VisibleEffect`]s travel in `EntityInfo.effects` (versioned by
//!   `EntityInfo.effects_version`, which keys the look cache together with
//!   `appearance_version`, see [`crate::looks`]) and in
//!   `ServerMsg::EffectsChanged` to everyone who has the character in view;
//! - the drinker also gets `ServerMsg::OwnEffects` (every active effect with
//!   its remaining time, and the movement speed the server validates with).
//!
//! Durations and numbers (scales, speed) are data: `data/economy.json`
//! (`sim_core::economy::EffectRules`). Protocol v8 adds Featherlight Elixir
//! ([`EffectKind::Featherlight`]): lighter on a broom (`OwnEffects.broom_pct`).
//!
//! Protocol v7 adds one effect that is not a potion: a Divination class's
//! fortune ([`EffectKind::Fortune`], which [`FortuneKind`]), seen only by
//! its holder (`OwnEffect.fortune`; no [`VisibleEffect`]).
//!
//! Protocol v11 adds four effects that spells cast ([`EffectKind::is_spell`]:
//! never drunk, never stored): Rigora ([`EffectKind::Petrified`]) and
//! Tardara ([`EffectKind::Slowed`]) are *hindrances* whose speed
//! multiplies the zone's ([`EffectKind::is_hindrance`]), Tinctura recolours
//! a robe ([`EffectKind::Recoloured`]) and Custodio brings out the
//! caster's silver animal ([`EffectKind::Guardian`], [`GuardianAnimal`]).

use serde::{Deserialize, Serialize};

use crate::Appearance;
use crate::school::FortuneKind;

/// Movement speed in percent when no effect changes it (`OwnEffects.speed_pct`).
pub const SPEED_PCT_NORMAL: u16 = 100;
/// The fastest an effect may make anyone (percent); the server clamps to it.
pub const SPEED_PCT_MAX: u16 = 200;
/// Protocol v11: the slowest (a Rigora bind stops walking altogether).
pub const SPEED_PCT_MIN: u16 = 0;
/// Scale in percent of a character no effect resizes.
pub const SCALE_PCT_NORMAL: u8 = 100;

/// What a potion does. One of each kind can be active at a time: drinking
/// the same kind again restarts its timer; `Shrinking` and `Swelling` replace
/// each other.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EffectKind {
    /// Glow Draught: a soft coloured aura.
    Glow,
    /// Chameleon Tonic: hair or robe colour cycles through a palette family.
    Chameleon,
    /// Diminishing Draught: half size.
    Shrinking,
    /// Enlarging Draught: larger.
    Swelling,
    /// Chatterbrew: the drinker's chat comes out scrambled.
    Babbling,
    /// Mimic Draught: the drinker wears a consenting friend's look.
    MimicDraught,
    /// Fortune's Draught: better exploration finds.
    FortunesDraught,
    /// Pick-Me-Up Tonic: steam from the ears and a small speed boost.
    PickMeUpTonic,
    /// Invisibility Draught: hides the nameplate (not the body).
    Invisibility,
    /// Protocol v7: a Divination fortune (not a potion; nobody else sees
    /// it). Which one is `OwnEffect.fortune`.
    Fortune,
    /// Protocol v8: Featherlight Elixir: a faster broom
    /// (`OwnEffects.broom_pct`). Nobody else sees it.
    Featherlight,
    /// Protocol v11: Rigora: stiff as a board, no walking
    /// (a hindrance: speed 0).
    Petrified,
    /// Protocol v11: Tardara: walking at a fraction of normal speed
    /// (a hindrance).
    Slowed,
    /// Protocol v11: Tinctura: the robe shows another colour.
    Recoloured,
    /// Protocol v11: Custodio: the caster's silver animal is out.
    Guardian,
}

impl EffectKind {
    /// Every kind: the potions, then the fortune, then (protocol v8)
    /// Featherlight, then (protocol v11) the spell effects.
    pub const ALL: [EffectKind; 15] = [
        EffectKind::Glow,
        EffectKind::Chameleon,
        EffectKind::Shrinking,
        EffectKind::Swelling,
        EffectKind::Babbling,
        EffectKind::MimicDraught,
        EffectKind::FortunesDraught,
        EffectKind::PickMeUpTonic,
        EffectKind::Invisibility,
        EffectKind::Fortune,
        EffectKind::Featherlight,
        EffectKind::Petrified,
        EffectKind::Slowed,
        EffectKind::Recoloured,
        EffectKind::Guardian,
    ];

    /// The kinds a potion may have (the Exchange's potion subcategories).
    pub const POTIONS: [EffectKind; 10] = [
        EffectKind::Glow,
        EffectKind::Chameleon,
        EffectKind::Shrinking,
        EffectKind::Swelling,
        EffectKind::Babbling,
        EffectKind::MimicDraught,
        EffectKind::FortunesDraught,
        EffectKind::PickMeUpTonic,
        EffectKind::Invisibility,
        EffectKind::Featherlight,
    ];

    /// The id used in data files (`potion.effect`, Exchange potion
    /// subcategories), JSON and the database (`active_effects.kind`).
    pub fn id(self) -> &'static str {
        match self {
            EffectKind::Glow => "glow",
            EffectKind::Chameleon => "chameleon",
            EffectKind::Shrinking => "shrinking",
            EffectKind::Swelling => "swelling",
            EffectKind::Babbling => "babbling",
            EffectKind::MimicDraught => "mimic_draught",
            EffectKind::FortunesDraught => "fortunes_draught",
            EffectKind::PickMeUpTonic => "pick_me_up_tonic",
            EffectKind::Invisibility => "invisibility",
            EffectKind::Fortune => "fortune",
            EffectKind::Featherlight => "featherlight",
            EffectKind::Petrified => "petrified",
            EffectKind::Slowed => "slowed",
            EffectKind::Recoloured => "recoloured",
            EffectKind::Guardian => "guardian",
        }
    }

    pub fn from_id(id: &str) -> Option<EffectKind> {
        EffectKind::ALL.into_iter().find(|k| k.id() == id)
    }

    /// Whether a potion can have this effect (every kind but the fortune
    /// and the spell effects).
    pub fn is_potion(self) -> bool {
        self != EffectKind::Fortune && !self.is_spell()
    }

    /// Protocol v11: bind and slow. Their `speed_pct` multiplies the zone's
    /// speed (in every zone, arenas included); the slowest one wins.
    pub fn is_hindrance(self) -> bool {
        matches!(self, EffectKind::Petrified | EffectKind::Slowed)
    }

    /// Protocol v11: cast by a spell, never drunk or stored (no
    /// `active_effects` rows; they end with the session).
    pub fn is_spell(self) -> bool {
        matches!(
            self,
            EffectKind::Petrified
                | EffectKind::Slowed
                | EffectKind::Recoloured
                | EffectKind::Guardian
        )
    }

    /// Kinds that cannot be active together with this one (drinking one
    /// ends the other).
    pub fn excludes(self) -> Option<EffectKind> {
        match self {
            EffectKind::Shrinking => Some(EffectKind::Swelling),
            EffectKind::Swelling => Some(EffectKind::Shrinking),
            _ => None,
        }
    }
}

/// Which colour a Chameleon Tonic cycles.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChameleonPart {
    Hair,
    /// The primary dye of the garment in the body slot.
    Robe,
}

/// Protocol v11: the silver animal a character's guardian takes (stable per
/// character: `sim_core::spells::guardian_animal`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GuardianAnimal {
    Hare,
    Fox,
    Cat,
    Owl,
    Otter,
    Horse,
}

impl GuardianAnimal {
    pub const ALL: [GuardianAnimal; 6] = [
        GuardianAnimal::Hare,
        GuardianAnimal::Fox,
        GuardianAnimal::Cat,
        GuardianAnimal::Owl,
        GuardianAnimal::Otter,
        GuardianAnimal::Horse,
    ];

    /// The id in JSON and asset names (`guardian_<id>_<part>`).
    pub fn id(self) -> &'static str {
        match self {
            GuardianAnimal::Hare => "hare",
            GuardianAnimal::Fox => "fox",
            GuardianAnimal::Cat => "cat",
            GuardianAnimal::Owl => "owl",
            GuardianAnimal::Otter => "otter",
            GuardianAnimal::Horse => "horse",
        }
    }

    pub fn from_id(id: &str) -> Option<GuardianAnimal> {
        GuardianAnimal::ALL.into_iter().find(|a| a.id() == id)
    }
}

/// What viewers draw for an active effect. Compact: a few bytes each, an
/// empty list (the usual case) is one byte.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VisibleEffect {
    /// A soft aura in a palette colour (`data/items/palette.json` index).
    Glow { colour: u8 },
    /// `part` cycles through the colours of palette family `family`
    /// (index into `palette.json` `families`), one step every
    /// `EffectRules.chameleon.cycle_ms`.
    Chameleon { part: ChameleonPart, family: u8 },
    /// Drawn at `pct` percent of normal size (Shrinking 50, Swelling 130).
    /// Collision and reach are unchanged: the effect is cosmetic.
    Scale { pct: u8 },
    /// A speech-bubble flourish; the scrambling itself happens on the server.
    Babbling,
    /// Mimic Draught: draw this look (body, hair and outfit of the friend who
    /// consented) instead of the character's own. The nameplate keeps the
    /// drinker's name.
    Disguise { appearance: Appearance },
    /// Fortune's Draught: a faint golden glint.
    Lucky,
    /// Pick-Me-Up Tonic: steam from the ears.
    Steam,
    /// Invisibility Draught: no nameplate above the character.
    NameplateHidden,
    /// Lucerna, a lit wand tip (not a potion; the world adds it
    /// while the spell is on). Clients draw real lights for the nearest few
    /// lit wands and an emissive glow for the rest.
    Lucerna,
    /// Protocol v11: Rigora: stiff as a board (the pose freezes).
    Bound,
    /// Protocol v11: Tardara: moving in slow motion at `pct`%
    /// (animation rate).
    Slowed { pct: u8 },
    /// Protocol v11: Tinctura: the body garment's primary dye shows palette
    /// colour `colour` (overrides a Chameleon tint).
    Recoloured { colour: u8 },
    /// Protocol v11: Custodio: the holder's silver animal follows
    /// (`guarding`: it circles them, a duel ward).
    Guardian {
        animal: GuardianAnimal,
        guarding: bool,
    },
}

/// One of the client's own active effects (`ServerMsg::OwnEffects`), for the HUD.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct OwnEffect {
    pub kind: EffectKind,
    /// Whole seconds left (rounded up).
    pub remaining_secs: u32,
    /// Protocol v7: which fortune, for `EffectKind::Fortune` (else `None`).
    pub fortune: Option<FortuneKind>,
}

/// Why a potion action failed (`ServerMsg::PotionError`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum PotionErrorCode {
    /// No such item in the sender's inventory.
    ItemNotFound,
    /// The item is not a potion.
    NotAPotion,
    /// Mimic Draught needs a friend to copy (`MimicDraughtAsk`, not `Drink`).
    NeedsTarget,
    /// Mimic Draught copies friends only (no block either way).
    NotFriend,
    /// The friend is not in the same zone within the Mimic Draught range.
    TooFar,
    /// The friend is not online.
    NotOnline,
    /// The consent request expired or does not exist.
    Expired,
    /// Potions cannot be drunk here (dueling arenas).
    Disabled,
    /// The drinker or the friend already has a consent request pending.
    Busy,
    /// Too many potion actions; slow down.
    RateLimited,
    /// The server could not complete the action (e.g. a database error).
    Failed,
}
