//! Spells: `data/spells.json` ([`Spells`]).
//!
//! Every spell has a stable numeric id (like item ids, never reused), a
//! wand gesture and a colour that telegraph it, how it is learned
//! ([`Unlock`]: at a level, in a place that teaches it: a class or a dueling
//! venue), what it does as a social action outside duels ([`Social`]) and,
//! for dueling spells, its duel numbers ([`DuelSpell`]). The numbers never
//! scale with the caster's level: levels open options (more spells, more
//! loadout slots), not power.
//!
//! - **Lucerna** lights the caster's wand (seen by everyone around).
//! - **Elevara** floats a furniture piece of the dorm room the
//!   caster is in for a few seconds (positions do not change).
//! - Dueling spells cast outside a duel are a harmless flourish of their
//!   gesture and colour, so spells double as social actions.
//!
//! Spells to level 20 (`SPELLS_VERSION` 2, protocol v11) add eight:
//! Tinctura (recolours a robe), Rigora (a bind), Volucra (a
//! flutter of birds), Adveni (a pull), Dissolvo (ends spells),
//! Aperio (shows secrets near the caster), Tardara (a slow) and
//! Custodio (a silver companion, a ward in duels). Their social actions are
//! [`Social`] variants; their duel effects ride on projectiles
//! ([`RootRider`], [`SlowRider`], [`PullRider`], [`WaveShape`]) or are
//! [`DuelEffect::Dispel`] and [`DuelEffect::Ward`]. See docs/architecture.md,
//! "Spells and dueling".
//!
//! A character's dueling **loadout** is a fixed number of slots
//! (`data/progression.json`, by level) filled from learned dueling spells
//! ([`Spells::check_loadout`], [`Spells::default_loadout`]).
//!
//! The social **hotbar** (protocol v17) is [`HOTBAR_SLOTS`] slots of learned
//! spells that do something outside a duel ([`SpellDef::on_hotbar`]: every
//! spell but the flourishes), each at most once ([`Spells::check_hotbar`]).
//! A character that never set one has its learned ones in spellbook order
//! ([`Spells::default_hotbar`]); a spell learned later goes into the first
//! empty slot ([`Spells::hotbar_add`]).

use serde::{Deserialize, Serialize};

use protocol::{GuardianAnimal, HOTBAR_SLOTS, SpellId};

use crate::gesture::Gestures;

/// The spells schema version this crate reads.
pub const SPELLS_VERSION: u32 = 2;

#[derive(Debug, thiserror::Error)]
pub enum SpellError {
    #[error("spells JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("spells are inconsistent: {0}")]
    Invalid(String),
}

/// Which subject teaches it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum School {
    Charms,
    /// Transfiguration (Volucra).
    Transfiguration,
    /// Defensive Magic (the Dueling Club).
    Defence,
}

/// What a spell is for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SpellKind {
    /// No duel use (Lucerna, Elevara).
    Utility,
    /// A projectile that costs the target Poise (blocked by a shield).
    Attack,
    /// A short shield window (Scutara).
    Shield,
    /// Breaks a shield and disarms (Dimitto).
    Disarm,
    /// A projectile that roots its target (Rigora).
    Bind,
    /// A projectile (or wave) that slows its target (Tardara).
    Slow,
    /// A projectile that pulls and staggers its target (Adveni).
    Pull,
    /// Clears the caster's root and slow (Dissolvo).
    Counter,
    /// Blocks the next spell that would land (Custodio).
    Ward,
}

/// How a spell is learned: at `level` or above, in place `at`: a class id
/// (`data/classes/schedule.json`, taught at the end of a session attended)
/// or a dueling venue id (`data/dueling/venues.json`, taught on entering
/// its dueling area).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Unlock {
    pub level: u8,
    pub at: String,
}

/// What casting it outside a duel does. Every cast also shows the caster's
/// `Anim::Cast` and the spell's gesture and colour (`SpellCast`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Social {
    /// A burst of colour, seen within `range_m`.
    Flourish { cooldown_ms: u32, range_m: f32 },
    /// The wand tip lights (`VisibleEffect::Lucerna`) until ended (`EndSpell`).
    Light {
        cooldown_ms: u32,
        /// The light's reach and strength, for the client's point light.
        radius_m: f32,
        intensity: f32,
    },
    /// A furniture piece of the dorm room within `range_m` floats `height_m`
    /// up and bobs `bob_m` at `bob_hz` for `duration_ms`.
    Levitate {
        cooldown_ms: u32,
        range_m: f32,
        duration_ms: u32,
        height_m: f32,
        bob_m: f32,
        bob_hz: f32,
    },
    /// Tinctura: an ally's (a friend, party or club member; or the
    /// caster's) body garment shows one of `colours` (palette indices) for
    /// `duration_ms`.
    Recolour {
        cooldown_ms: u32,
        range_m: f32,
        duration_ms: u32,
        colours: Vec<u8>,
    },
    /// Rigora outside duels: an ally stiffens (no walking) for
    /// `duration_ms`; they are immune to further hindrances for `immune_ms`.
    Bind {
        cooldown_ms: u32,
        range_m: f32,
        duration_ms: u32,
        immune_ms: u32,
    },
    /// Volucra: `count` birds from the wand tip, circling and flying off
    /// over `duration_ms`; seen within `range_m`.
    Birds {
        cooldown_ms: u32,
        range_m: f32,
        count: u8,
        duration_ms: u32,
    },
    /// Adveni outside duels: an ally slides up to `distance_m` toward the
    /// caster (at least `min_m`, never closer than `gap_m`) over `move_ms`,
    /// held still until `hold_ms`.
    Pull {
        cooldown_ms: u32,
        range_m: f32,
        distance_m: f32,
        min_m: f32,
        gap_m: f32,
        move_ms: u32,
        hold_ms: u32,
        immune_ms: u32,
    },
    /// Dissolvo outside duels: ends an ally's (or the caster's) Lucerna, bind,
    /// slow and recolour, or lands a floating piece.
    Dispel { cooldown_ms: u32, range_m: f32 },
    /// Aperio: the caster alone sees passage doors and find spots within
    /// `radius_m` (and `height_m` up or down) for `duration_ms`.
    Reveal {
        cooldown_ms: u32,
        radius_m: f32,
        height_m: f32,
        duration_ms: u32,
    },
    /// Tardara outside duels: an ally walks at `pct`% for `duration_ms`.
    Slow {
        cooldown_ms: u32,
        range_m: f32,
        duration_ms: u32,
        pct: u8,
        immune_ms: u32,
    },
    /// Custodio outside duels: the caster's silver animal follows
    /// them for `duration_ms` (seen within `range_m`).
    Guardian {
        cooldown_ms: u32,
        range_m: f32,
        duration_ms: u32,
    },
}

impl Social {
    pub fn cooldown_ms(&self) -> u32 {
        match *self {
            Social::Flourish { cooldown_ms, .. }
            | Social::Light { cooldown_ms, .. }
            | Social::Levitate { cooldown_ms, .. }
            | Social::Recolour { cooldown_ms, .. }
            | Social::Bind { cooldown_ms, .. }
            | Social::Birds { cooldown_ms, .. }
            | Social::Pull { cooldown_ms, .. }
            | Social::Dispel { cooldown_ms, .. }
            | Social::Reveal { cooldown_ms, .. }
            | Social::Slow { cooldown_ms, .. }
            | Social::Guardian { cooldown_ms, .. } => cooldown_ms,
        }
    }
}

/// Rigora: a hit roots the target (no moving) for `ms`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct RootRider {
    pub ms: u32,
}

/// Tardara: a hit slows the target to `pct`% speed for `ms`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct SlowRider {
    pub ms: u32,
    pub pct: u8,
}

/// Adveni: a hit interrupts any cast, pulls the target up to `distance_m`
/// toward the caster (never closer than `gap_m`) over `move_ms`, and
/// staggers it (no casting, no moving) for `stagger_ms`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct PullRider {
    pub distance_m: f32,
    pub gap_m: f32,
    pub move_ms: u32,
    pub stagger_ms: u32,
}

/// Tardara: the projectile is a wave `2 * half_width_m` wide that no
/// sidestep escapes.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct WaveShape {
    pub half_width_m: f32,
}

/// What a dueling spell does when it releases.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DuelEffect {
    /// A projectile flying straight at where the opponent stood at release.
    Projectile {
        speed_mps: f32,
        /// Added to the duelist's radius for the hit test.
        radius_m: f32,
        poise_damage: u16,
        /// Shatters a shield instead of being blocked (and still lands).
        #[serde(default)]
        breaks_shield: bool,
        /// The target cannot cast for this long after a hit.
        #[serde(default)]
        disarm_ms: u32,
        /// Spells to level 20: Rigora's root.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        root: Option<RootRider>,
        /// Tardara's slow.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        slow: Option<SlowRider>,
        /// Adveni's pull and stagger.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        pull: Option<PullRider>,
        /// Tardara: a wave instead of a bolt.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        wave: Option<WaveShape>,
    },
    /// A shield for `shield_ms`; projectiles landing in its first
    /// `perfect_ms` are reflected back at their caster.
    Shield { shield_ms: u32, perfect_ms: u32 },
    /// Dissolvo: at release, clears the caster's root and slow.
    Dispel,
    /// Custodio: until `ward_ms` after release, blocks the next
    /// spell that would land on the caster, from any direction.
    Ward { ward_ms: u32 },
}

/// The caster's guardian, stable per character: splitmix64 of the
/// character id, over [`GuardianAnimal::ALL`].
pub fn guardian_animal(character_id: i64) -> GuardianAnimal {
    let mut z = (character_id as u64).wrapping_add(0x9E37_79B9_7F4A_7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^= z >> 31;
    GuardianAnimal::ALL[(z % GuardianAnimal::ALL.len() as u64) as usize]
}

/// A dueling spell's numbers.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct DuelSpell {
    /// From the start of the cast (telegraphed by the gesture) to release.
    pub cast_ms: u32,
    /// From the start of the cast until it can be cast again.
    pub cooldown_ms: u32,
    /// A hit during the cast breaks it (the cooldown is still spent).
    #[serde(default)]
    pub interruptible: bool,
    pub effect: DuelEffect,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpellDef {
    pub id: SpellId,
    /// Stable data id ("elevara").
    pub key: String,
    /// Display name.
    pub name: String,
    /// What the caster says (chat bubble).
    pub incantation: String,
    pub school: School,
    pub kind: SpellKind,
    pub unlock: Unlock,
    /// The spell's colour ("#rrggbb"), for its light and projectile.
    pub colour: String,
    /// The wand gesture (`data/classes/gestures.json` key) that casts it.
    pub gesture: String,
    pub social: Social,
    /// Dueling spells only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duel: Option<DuelSpell>,
}

/// Why a loadout is not allowed.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum LoadoutError {
    #[error("more spells than the loadout has slots")]
    TooMany,
    #[error("spell {0} is listed twice")]
    Duplicate(SpellId),
    #[error("spell {0} is not a dueling spell")]
    NotDueling(SpellId),
    #[error("spell {0} is not learned")]
    NotLearned(SpellId),
}

/// A social hotbar: [`HOTBAR_SLOTS`] slots in key order (1-9, then 0).
pub type Hotbar = [Option<SpellId>; HOTBAR_SLOTS];

/// Why a hotbar is not allowed.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum HotbarError {
    #[error("more slots than the hotbar has")]
    TooMany,
    #[error("spell {0} is on the hotbar twice")]
    Duplicate(SpellId),
    #[error("spell {0} does nothing outside a duel")]
    NotSocial(SpellId),
    #[error("spell {0} is not learned")]
    NotLearned(SpellId),
}

/// The spellbook's order of schools (the client's `SCHOOLS`,
/// ui/spellbook-state.ts): the hotbar's default follows it.
const SCHOOL_ORDER: [School; 3] = [School::Charms, School::Transfiguration, School::Defence];

impl SpellDef {
    /// Whether it may go on the social hotbar: it does something outside a
    /// duel (every spell but the flourishes).
    pub fn on_hotbar(&self) -> bool {
        !matches!(self.social, Social::Flourish { .. })
    }
}

/// `data/spells.json`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Spells {
    pub version: u32,
    pub spells: Vec<SpellDef>,
}

impl Spells {
    /// Parses and validates the spells against the gestures.
    pub fn from_json(json: &str, gestures: &Gestures) -> Result<Spells, SpellError> {
        let s: Spells = serde_json::from_str(json)?;
        s.validate(gestures).map_err(SpellError::Invalid)?;
        Ok(s)
    }

    pub fn get(&self, id: SpellId) -> Option<&SpellDef> {
        self.spells.iter().find(|s| s.id == id)
    }

    pub fn by_key(&self, key: &str) -> Option<&SpellDef> {
        self.spells.iter().find(|s| s.key == key)
    }

    /// The duel numbers of a dueling spell.
    pub fn duel(&self, id: SpellId) -> Option<&DuelSpell> {
        self.get(id).and_then(|s| s.duel.as_ref())
    }

    /// Spells place `at` teaches a character of `level`.
    pub fn taught_at<'a>(&'a self, at: &'a str, level: u8) -> impl Iterator<Item = &'a SpellDef> {
        self.spells
            .iter()
            .filter(move |s| s.unlock.at == at && s.unlock.level <= level)
    }

    /// The loadout of someone who never chose one: their learned dueling
    /// spells in file order, as many as fit.
    pub fn default_loadout(&self, learned: &[SpellId], slots: u8) -> Vec<SpellId> {
        self.spells
            .iter()
            .filter(|s| s.duel.is_some() && learned.contains(&s.id))
            .map(|s| s.id)
            .take(usize::from(slots))
            .collect()
    }

    /// The spells the hotbar can hold, in spellbook order: by school
    /// (Charms, Transfiguration, Defence), in file order within each.
    pub fn hotbar_spells(&self) -> impl Iterator<Item = &SpellDef> {
        SCHOOL_ORDER.into_iter().flat_map(move |school| {
            self.spells
                .iter()
                .filter(move |s| s.school == school && s.on_hotbar())
        })
    }

    /// The hotbar of someone who never set one: their learned hotbar
    /// spells in spellbook order, as many as fit.
    pub fn default_hotbar(&self, learned: &[SpellId]) -> Hotbar {
        let mut bar: Hotbar = [None; HOTBAR_SLOTS];
        let ids = self
            .hotbar_spells()
            .filter(|s| learned.contains(&s.id))
            .map(|s| s.id);
        for (slot, id) in bar.iter_mut().zip(ids) {
            *slot = Some(id);
        }
        bar
    }

    /// `slots` as a hotbar if someone who learned `learned` may set it: at
    /// most [`HOTBAR_SLOTS`] (the rest empty), learned hotbar spells only,
    /// none twice.
    pub fn check_hotbar(
        &self,
        learned: &[SpellId],
        slots: &[Option<SpellId>],
    ) -> Result<Hotbar, HotbarError> {
        if slots.len() > HOTBAR_SLOTS {
            return Err(HotbarError::TooMany);
        }
        let mut bar: Hotbar = [None; HOTBAR_SLOTS];
        for (k, &slot) in slots.iter().enumerate() {
            let Some(id) = slot else { continue };
            if slots[..k].contains(&Some(id)) {
                return Err(HotbarError::Duplicate(id));
            }
            if !self.get(id).is_some_and(SpellDef::on_hotbar) {
                return Err(HotbarError::NotSocial(id));
            }
            if !learned.contains(&id) {
                return Err(HotbarError::NotLearned(id));
            }
            bar[k] = Some(id);
        }
        Ok(bar)
    }

    /// A stored hotbar with whatever is no longer allowed emptied (a spell
    /// gone from the data or not learned, a repeat); the rest keep their slots.
    pub fn trim_hotbar(&self, learned: &[SpellId], stored: &[Option<SpellId>]) -> Hotbar {
        let mut bar: Hotbar = [None; HOTBAR_SLOTS];
        for (k, &slot) in stored.iter().take(HOTBAR_SLOTS).enumerate() {
            if let Some(id) = slot
                && !bar.contains(&Some(id))
                && learned.contains(&id)
                && self.get(id).is_some_and(SpellDef::on_hotbar)
            {
                bar[k] = Some(id);
            }
        }
        bar
    }

    /// Spells just learned go into the first empty slots, in the order
    /// given (hotbar spells not already on it, while there is room).
    /// Whether the hotbar changed.
    pub fn hotbar_add(&self, bar: &mut Hotbar, new: &[SpellId]) -> bool {
        let mut changed = false;
        for &id in new {
            if bar.contains(&Some(id)) || !self.get(id).is_some_and(SpellDef::on_hotbar) {
                continue;
            }
            let Some(slot) = bar.iter_mut().find(|s| s.is_none()) else {
                break;
            };
            *slot = Some(id);
            changed = true;
        }
        changed
    }

    /// Tinctura's colours must be palette indices (below `palette_len`);
    /// the server's catalog calls this with `data/items/palette.json`.
    pub fn check_palette(&self, palette_len: usize) -> Result<(), String> {
        for s in &self.spells {
            if let Social::Recolour { colours, .. } = &s.social
                && let Some(c) = colours.iter().find(|&&c| usize::from(c) >= palette_len)
            {
                return Err(format!(
                    "{}: colour {c} is not in the palette ({palette_len} colours)",
                    s.key
                ));
            }
        }
        Ok(())
    }

    /// Whether `loadout` may be used by someone who learned `learned` and
    /// has `slots` slots: learned dueling spells, no repeats, not too many.
    pub fn check_loadout(
        &self,
        learned: &[SpellId],
        loadout: &[SpellId],
        slots: u8,
    ) -> Result<(), LoadoutError> {
        if loadout.len() > usize::from(slots) {
            return Err(LoadoutError::TooMany);
        }
        for (k, &id) in loadout.iter().enumerate() {
            if loadout[..k].contains(&id) {
                return Err(LoadoutError::Duplicate(id));
            }
            if self.duel(id).is_none() {
                return Err(LoadoutError::NotDueling(id));
            }
            if !learned.contains(&id) {
                return Err(LoadoutError::NotLearned(id));
            }
        }
        Ok(())
    }

    fn validate(&self, gestures: &Gestures) -> Result<(), String> {
        if self.version != SPELLS_VERSION {
            return Err(format!(
                "unsupported version {} (expected {SPELLS_VERSION})",
                self.version
            ));
        }
        let mut ids = std::collections::HashSet::new();
        let mut keys = std::collections::HashSet::new();
        for s in &self.spells {
            let k = &s.key;
            if !ids.insert(s.id) || !keys.insert(k.as_str()) || !crate::clock::valid_id(k) {
                return Err(format!("spell {k:?}: ids and keys must be unique ids"));
            }
            if s.name.is_empty() || s.incantation.is_empty() {
                return Err(format!("{k}: needs a name and an incantation"));
            }
            if !(1..=protocol::LEVEL_MAX).contains(&s.unlock.level)
                || !crate::clock::valid_id(&s.unlock.at)
            {
                return Err(format!("{k}: unlock level 1..=20 at a place id"));
            }
            if !is_hex_colour(&s.colour) {
                return Err(format!("{k}: colour must be \"#rrggbb\""));
            }
            if gestures.by_key(&s.gesture).is_none() {
                return Err(format!("{k}: unknown gesture {:?}", s.gesture));
            }
            let positive = |v: f32| v.is_finite() && v > 0.0;
            if !social_ok(&s.social) {
                return Err(format!("{k}: social numbers must be positive"));
            }
            match (s.kind, &s.duel) {
                (SpellKind::Utility, None) => {}
                (SpellKind::Utility, Some(_)) => {
                    return Err(format!("{k}: utility spells have no duel block"));
                }
                (_, None) => return Err(format!("{k}: dueling spells need a duel block")),
                (kind, Some(d)) => {
                    if d.cast_ms == 0 || d.cooldown_ms < d.cast_ms {
                        return Err(format!("{k}: 0 < cast_ms <= cooldown_ms"));
                    }
                    if !effect_suits(kind, &d.effect, positive) {
                        return Err(format!("{k}: duel effect does not suit its kind"));
                    }
                }
            }
        }
        for kind in [SpellKind::Attack, SpellKind::Shield, SpellKind::Disarm] {
            if !self.spells.iter().any(|s| s.kind == kind) {
                return Err(format!("no {kind:?} spell: dueling needs all three"));
            }
        }
        Ok(())
    }
}

/// Social numbers: positive ranges, durations that fit the wire where a
/// `u16` carries them, percentages and counts in range.
fn social_ok(social: &Social) -> bool {
    let positive = |v: f32| v.is_finite() && v > 0.0;
    let wire_ms = |ms: u32| ms > 0 && ms <= u32::from(u16::MAX);
    match *social {
        Social::Flourish { range_m, .. } => positive(range_m),
        Social::Light {
            radius_m,
            intensity,
            ..
        } => positive(radius_m) && positive(intensity),
        Social::Levitate {
            range_m,
            duration_ms,
            height_m,
            bob_m,
            bob_hz,
            ..
        } => {
            positive(range_m)
                && wire_ms(duration_ms)
                && positive(height_m)
                && bob_m >= 0.0
                && positive(bob_hz)
        }
        Social::Recolour {
            cooldown_ms,
            range_m,
            duration_ms,
            ref colours,
        } => cooldown_ms > 0 && positive(range_m) && duration_ms > 0 && !colours.is_empty(),
        Social::Bind {
            cooldown_ms,
            range_m,
            duration_ms,
            immune_ms,
        } => cooldown_ms > 0 && positive(range_m) && duration_ms > 0 && immune_ms > 0,
        Social::Birds {
            cooldown_ms,
            range_m,
            count,
            duration_ms,
        } => cooldown_ms > 0 && positive(range_m) && (1..=12).contains(&count) && duration_ms > 0,
        Social::Pull {
            cooldown_ms,
            range_m,
            distance_m,
            min_m,
            gap_m,
            move_ms,
            hold_ms,
            immune_ms,
        } => {
            cooldown_ms > 0
                && positive(range_m)
                && positive(min_m)
                && distance_m.is_finite()
                && distance_m >= min_m
                && positive(gap_m)
                && wire_ms(move_ms)
                && wire_ms(hold_ms)
                && immune_ms > 0
        }
        Social::Dispel {
            cooldown_ms,
            range_m,
        } => cooldown_ms > 0 && positive(range_m),
        Social::Reveal {
            cooldown_ms,
            radius_m,
            height_m,
            duration_ms,
        } => cooldown_ms > 0 && positive(radius_m) && positive(height_m) && wire_ms(duration_ms),
        Social::Slow {
            cooldown_ms,
            range_m,
            duration_ms,
            pct,
            immune_ms,
        } => {
            cooldown_ms > 0
                && positive(range_m)
                && duration_ms > 0
                && (10..=99).contains(&pct)
                && immune_ms > 0
        }
        Social::Guardian {
            cooldown_ms,
            range_m,
            duration_ms,
        } => cooldown_ms > 0 && positive(range_m) && duration_ms > 0,
    }
}

/// Whether a dueling spell's effect is what its kind promises.
fn effect_suits(kind: SpellKind, effect: &DuelEffect, positive: impl Fn(f32) -> bool) -> bool {
    match *effect {
        DuelEffect::Shield {
            shield_ms,
            perfect_ms,
        } => kind == SpellKind::Shield && perfect_ms > 0 && perfect_ms < shield_ms,
        DuelEffect::Dispel => kind == SpellKind::Counter,
        DuelEffect::Ward { ward_ms } => kind == SpellKind::Ward && ward_ms > 0,
        DuelEffect::Projectile {
            speed_mps,
            radius_m,
            poise_damage,
            breaks_shield,
            disarm_ms,
            root,
            slow,
            pull,
            wave,
        } => {
            let flies = positive(speed_mps)
                && speed_mps <= 100.0
                && radius_m.is_finite()
                && radius_m >= 0.0
                && poise_damage > 0;
            let plain = !breaks_shield && disarm_ms == 0;
            let riders = (root.is_some(), slow.is_some(), pull.is_some());
            flies
                && match kind {
                    SpellKind::Attack => plain && riders == (false, false, false) && wave.is_none(),
                    SpellKind::Disarm => {
                        breaks_shield
                            && disarm_ms > 0
                            && riders == (false, false, false)
                            && wave.is_none()
                    }
                    SpellKind::Bind => {
                        plain
                            && riders == (true, false, false)
                            && wave.is_none()
                            && root.is_some_and(|r| r.ms > 0)
                    }
                    SpellKind::Slow => {
                        plain
                            && riders == (false, true, false)
                            && slow.is_some_and(|r| r.ms > 0 && (10..100).contains(&r.pct))
                            && wave.is_none_or(|w| {
                                w.half_width_m.is_finite()
                                    && w.half_width_m > 0.0
                                    && w.half_width_m <= 4.0
                            })
                    }
                    SpellKind::Pull => {
                        plain
                            && riders == (false, false, true)
                            && wave.is_none()
                            && pull.is_some_and(|p| {
                                positive(p.distance_m)
                                    && positive(p.gap_m)
                                    && p.move_ms > 0
                                    && p.move_ms <= p.stagger_ms
                            })
                    }
                    _ => false,
                }
        }
    }
}

fn is_hex_colour(s: &str) -> bool {
    s.len() == 7 && s.starts_with('#') && s[1..].bytes().all(|b| b.is_ascii_hexdigit())
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub const SHIPPED: &str = include_str!("../../../data/spells.json");

    pub fn shipped() -> Spells {
        Spells::from_json(SHIPPED, &crate::gesture::tests::shipped())
            .expect("the shipped spells are valid")
    }

    #[test]
    fn the_shipped_spells_cover_the_design() {
        let s = shipped();
        for key in [
            "lucerna",
            "elevara",
            "scutara",
            "torpeo",
            "dimitto",
            "titillara",
            "repulsa",
        ] {
            assert!(s.by_key(key).is_some(), "{key}");
        }
        let lucerna = s.by_key("lucerna").unwrap();
        assert!(matches!(lucerna.social, Social::Light { .. }));
        assert!(matches!(
            s.by_key("elevara").unwrap().social,
            Social::Levitate { .. }
        ));
        // Charms teaches its spells (Adveni at 10), Transfiguration Volucra,
        // the club the first three dueling spells.
        let charms: Vec<_> = s.taught_at("charms", 20).map(|x| x.key.as_str()).collect();
        assert_eq!(
            charms,
            ["lucerna", "elevara", "tinctura", "adveni", "aperio"]
        );
        let transfiguration: Vec<_> = s
            .taught_at("transfiguration", 20)
            .map(|x| x.key.as_str())
            .collect();
        assert_eq!(transfiguration, ["volucra"]);
        assert!(s.taught_at("dueling_club", 20).all(|x| x.duel.is_some()));
        // Defence teaches Titillara at level 3
        // and Repulsa at level 5; the club keeps the level-1 spells.
        // Spells L20: then Rigora, Dissolvo, Tardara and the Guardian.
        let defence: Vec<_> = s.taught_at("defence", 20).map(|x| x.key.as_str()).collect();
        assert_eq!(
            defence,
            [
                "titillara",
                "repulsa",
                "rigora",
                "dissolvo",
                "tardara",
                "custodio"
            ]
        );
        assert_eq!(s.taught_at("defence", 2).count(), 0);
        assert_eq!(s.taught_at("defence", 4).count(), 1);
        assert_eq!(s.taught_at("dueling_club", 20).count(), 3);
        // Level 1 at the club gives a full first loadout of three.
        let first: Vec<SpellId> = s.taught_at("dueling_club", 1).map(|x| x.id).collect();
        assert_eq!(first.len(), 3);
        assert_eq!(s.default_loadout(&first, 3), first);
    }

    /// Spells L20: the level map (contract 1.1), exactly.
    #[test]
    fn the_spells_follow_the_level_map() {
        let s = shipped();
        let map: [(u8, &str, &str, School, SpellKind); 15] = [
            (1, "lucerna", "charms", School::Charms, SpellKind::Utility),
            (
                1,
                "scutara",
                "dueling_club",
                School::Defence,
                SpellKind::Shield,
            ),
            (
                1,
                "torpeo",
                "dueling_club",
                School::Defence,
                SpellKind::Attack,
            ),
            (
                1,
                "dimitto",
                "dueling_club",
                School::Defence,
                SpellKind::Disarm,
            ),
            (2, "elevara", "charms", School::Charms, SpellKind::Utility),
            (
                3,
                "titillara",
                "defence",
                School::Defence,
                SpellKind::Attack,
            ),
            (5, "repulsa", "defence", School::Defence, SpellKind::Attack),
            (6, "tinctura", "charms", School::Charms, SpellKind::Utility),
            (8, "rigora", "defence", School::Defence, SpellKind::Bind),
            (
                9,
                "volucra",
                "transfiguration",
                School::Transfiguration,
                SpellKind::Utility,
            ),
            (10, "adveni", "charms", School::Charms, SpellKind::Pull),
            (
                12,
                "dissolvo",
                "defence",
                School::Defence,
                SpellKind::Counter,
            ),
            (14, "aperio", "charms", School::Charms, SpellKind::Utility),
            (16, "tardara", "defence", School::Defence, SpellKind::Slow),
            (19, "custodio", "defence", School::Defence, SpellKind::Ward),
        ];
        assert_eq!(s.spells.len(), map.len());
        for (level, key, at, school, kind) in map {
            let d = s.by_key(key).unwrap_or_else(|| panic!("{key}"));
            assert_eq!(
                (d.unlock.level, d.unlock.at.as_str(), d.school, d.kind),
                (level, at, school, kind),
                "{key}"
            );
        }
        // Ids are stable (contract 1.2).
        for (key, id) in [
            ("tinctura", 3),
            ("adveni", 4),
            ("aperio", 5),
            ("rigora", 15),
            ("dissolvo", 16),
            ("tardara", 17),
            ("custodio", 18),
            ("volucra", 30),
        ] {
            assert_eq!(s.by_key(key).unwrap().id, id, "{key}");
        }
        let by = |level: u8| s.spells.iter().filter(move |x| x.unlock.level <= level);
        assert_eq!(by(19).count(), 15);
        assert_eq!(by(19).filter(|x| x.duel.is_some()).count(), 10);
        for level in [4, 7, 11, 13, 15, 17, 18, 20] {
            assert!(
                s.spells.iter().all(|x| x.unlock.level != level),
                "nothing new at {level}"
            );
        }
        // The default loadout at every level starts with the first five
        // dueling spells (file order), as many as are learned and fit.
        let old: Vec<SpellId> = ["scutara", "torpeo", "dimitto", "titillara", "repulsa"]
            .iter()
            .map(|k| s.by_key(k).unwrap().id)
            .collect();
        for level in 1..=20u8 {
            let slots = match level {
                1..=4 => 3,
                5..=9 => 4,
                _ => 5,
            };
            let learned: Vec<SpellId> = by(level).map(|x| x.id).collect();
            let expect: Vec<SpellId> = old
                .iter()
                .copied()
                .filter(|id| learned.contains(id))
                .take(slots)
                .collect();
            assert_eq!(s.default_loadout(&learned, slots as u8), expect, "{level}");
        }
        // Social shapes of the new spells.
        assert!(matches!(
            s.by_key("tinctura").unwrap().social,
            Social::Recolour { .. }
        ));
        assert!(matches!(
            s.by_key("custodio").unwrap().duel.unwrap().effect,
            DuelEffect::Ward { ward_ms: 10_000 }
        ));
        assert!(matches!(
            s.by_key("dissolvo").unwrap().duel.unwrap().effect,
            DuelEffect::Dispel
        ));
        let Some(DuelEffect::Projectile { wave, slow, .. }) =
            s.duel(s.by_key("tardara").unwrap().id).map(|d| d.effect)
        else {
            panic!("tardara");
        };
        assert_eq!(wave, Some(WaveShape { half_width_m: 1.5 }));
        assert_eq!(slow, Some(SlowRider { ms: 3_000, pct: 50 }));
    }

    #[test]
    fn tinctura_colours_must_be_in_the_palette() {
        let s = shipped();
        assert!(s.check_palette(48).is_ok());
        let err = s.check_palette(40).unwrap_err();
        assert!(err.contains("tinctura"), "{err}");
    }

    #[test]
    fn guardian_animals_are_stable_and_spread() {
        let mut counts = std::collections::HashMap::new();
        for id in 1..=600i64 {
            let a = guardian_animal(id);
            assert_eq!(a, guardian_animal(id));
            *counts.entry(a).or_insert(0) += 1;
        }
        assert_eq!(counts.len(), GuardianAnimal::ALL.len());
        assert!(counts.values().all(|&n| n >= 50), "{counts:?}");
    }

    /// Spells L20: each new kind's bad numbers are refused.
    #[test]
    fn bad_new_spells_are_refused() {
        let g = crate::gesture::tests::shipped();
        let v: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        let index = |key: &str| {
            v["spells"]
                .as_array()
                .unwrap()
                .iter()
                .position(|s| s["key"] == key)
                .unwrap()
        };
        let refused = |edit: &dyn Fn(&mut serde_json::Value), want: &str| {
            let mut e = v.clone();
            edit(&mut e);
            let err = Spells::from_json(&e.to_string(), &g)
                .unwrap_err()
                .to_string();
            assert!(err.contains(want), "{want}: {err}");
        };
        let (rigora, tardara, adveni, dissolvo, guardian, tinctura, aperio, volucra) = (
            index("rigora"),
            index("tardara"),
            index("adveni"),
            index("dissolvo"),
            index("custodio"),
            index("tinctura"),
            index("aperio"),
            index("volucra"),
        );
        let suit = "does not suit its kind";
        let social = "social numbers";
        // Kind / effect mismatches.
        refused(&|e| e["spells"][rigora]["kind"] = "attack".into(), suit);
        refused(
            &|e| e["spells"][rigora]["duel"]["effect"]["root"]["ms"] = 0.into(),
            suit,
        );
        refused(
            &|e| {
                e["spells"][rigora]["duel"]["effect"]["slow"] =
                    serde_json::json!({ "ms": 100, "pct": 50 })
            },
            suit,
        );
        refused(&|e| e["spells"][tardara]["kind"] = "bind".into(), suit);
        refused(
            &|e| e["spells"][tardara]["duel"]["effect"]["slow"]["pct"] = 100.into(),
            suit,
        );
        refused(
            &|e| e["spells"][tardara]["duel"]["effect"]["slow"]["pct"] = 5.into(),
            suit,
        );
        refused(
            &|e| e["spells"][tardara]["duel"]["effect"]["slow"]["ms"] = 0.into(),
            suit,
        );
        refused(
            &|e| e["spells"][tardara]["duel"]["effect"]["wave"]["half_width_m"] = 5.0.into(),
            suit,
        );
        refused(
            &|e| e["spells"][adveni]["duel"]["effect"]["pull"]["move_ms"] = 900.into(),
            suit,
        );
        refused(
            &|e| e["spells"][adveni]["duel"]["effect"]["pull"]["gap_m"] = 0.0.into(),
            suit,
        );
        refused(
            &|e| {
                e["spells"][adveni]["duel"]["effect"]["wave"] =
                    serde_json::json!({ "half_width_m": 1.0 })
            },
            suit,
        );
        refused(
            &|e| e["spells"][adveni]["duel"]["effect"]["breaks_shield"] = true.into(),
            suit,
        );
        refused(&|e| e["spells"][dissolvo]["kind"] = "ward".into(), suit);
        refused(
            &|e| e["spells"][guardian]["duel"]["effect"]["ward_ms"] = 0.into(),
            suit,
        );
        refused(
            &|e| e["spells"][guardian]["duel"]["effect"] = serde_json::json!({ "kind": "dispel" }),
            suit,
        );
        // A plain attack carries no rider.
        let torpeo = index("torpeo");
        refused(
            &|e| e["spells"][torpeo]["duel"]["effect"]["root"] = serde_json::json!({ "ms": 500 }),
            suit,
        );
        // Social numbers.
        refused(
            &|e| e["spells"][tinctura]["social"]["colours"] = serde_json::json!([]),
            social,
        );
        refused(
            &|e| e["spells"][tinctura]["social"]["duration_ms"] = 0.into(),
            social,
        );
        refused(
            &|e| e["spells"][rigora]["social"]["duration_ms"] = 0.into(),
            social,
        );
        refused(
            &|e| e["spells"][tardara]["social"]["pct"] = 100.into(),
            social,
        );
        refused(
            &|e| e["spells"][tardara]["social"]["pct"] = 9.into(),
            social,
        );
        refused(
            &|e| e["spells"][volucra]["social"]["count"] = 0.into(),
            social,
        );
        refused(
            &|e| e["spells"][volucra]["social"]["count"] = 13.into(),
            social,
        );
        refused(
            &|e| e["spells"][adveni]["social"]["min_m"] = 3.0.into(),
            social,
        );
        refused(
            &|e| e["spells"][adveni]["social"]["min_m"] = 0.0.into(),
            social,
        );
        refused(
            &|e| e["spells"][adveni]["social"]["move_ms"] = 70_000.into(),
            social,
        );
        refused(
            &|e| e["spells"][adveni]["social"]["hold_ms"] = 0.into(),
            social,
        );
        refused(
            &|e| e["spells"][aperio]["social"]["duration_ms"] = 70_000.into(),
            social,
        );
        refused(
            &|e| e["spells"][aperio]["social"]["height_m"] = 0.0.into(),
            social,
        );
        refused(
            &|e| e["spells"][guardian]["social"]["duration_ms"] = 0.into(),
            social,
        );
        // Utility spells still carry no duel block.
        refused(
            &|e| e["spells"][volucra]["duel"] = e["spells"][dissolvo]["duel"].clone(),
            "utility",
        );
    }

    #[test]
    fn loadouts_hold_learned_dueling_spells_only() {
        let s = shipped();
        let lucerna = s.by_key("lucerna").unwrap().id;
        let torpeo = s.by_key("torpeo").unwrap().id;
        let scutara = s.by_key("scutara").unwrap().id;
        let repulsa = s.by_key("repulsa").unwrap().id;
        let learned = [lucerna, torpeo, scutara];
        assert_eq!(s.check_loadout(&learned, &[torpeo, scutara], 3), Ok(()));
        assert_eq!(
            s.check_loadout(&learned, &[torpeo, torpeo], 3),
            Err(LoadoutError::Duplicate(torpeo))
        );
        assert_eq!(
            s.check_loadout(&learned, &[lucerna], 3),
            Err(LoadoutError::NotDueling(lucerna))
        );
        assert_eq!(
            s.check_loadout(&learned, &[repulsa], 3),
            Err(LoadoutError::NotLearned(repulsa))
        );
        assert_eq!(
            s.check_loadout(&learned, &[torpeo, scutara], 1),
            Err(LoadoutError::TooMany)
        );
        assert_eq!(s.default_loadout(&learned, 1).len(), 1);
    }

    #[test]
    fn the_hotbar_takes_every_spell_but_the_flourishes() {
        let s = shipped();
        let keys: Vec<&str> = s.hotbar_spells().map(|d| d.key.as_str()).collect();
        // Spellbook order: Charms, then Transfiguration, then Defence.
        assert_eq!(
            keys,
            [
                "lucerna", "elevara", "tinctura", "adveni", "aperio", "volucra", "rigora",
                "dissolvo", "tardara", "custodio"
            ]
        );
        for d in &s.spells {
            assert_eq!(d.on_hotbar(), !matches!(d.social, Social::Flourish { .. }));
        }
    }

    #[test]
    fn hotbars_hold_learned_social_spells_once() {
        let s = shipped();
        let id = |k: &str| s.by_key(k).unwrap().id;
        let (lucerna, elevara, torpeo, custodio) =
            (id("lucerna"), id("elevara"), id("torpeo"), id("custodio"));
        let learned = [lucerna, elevara, torpeo];
        let bar = s
            .check_hotbar(&learned, &[None, Some(elevara), None, Some(lucerna)])
            .unwrap();
        assert_eq!(bar[1], Some(elevara));
        assert_eq!(bar[3], Some(lucerna));
        assert_eq!(bar.iter().flatten().count(), 2);
        assert_eq!(
            s.check_hotbar(&learned, &[Some(lucerna), Some(lucerna)]),
            Err(HotbarError::Duplicate(lucerna))
        );
        assert_eq!(
            s.check_hotbar(&learned, &[Some(torpeo)]),
            Err(HotbarError::NotSocial(torpeo))
        );
        assert_eq!(
            s.check_hotbar(&learned, &[Some(custodio)]),
            Err(HotbarError::NotLearned(custodio))
        );
        assert_eq!(
            s.check_hotbar(&learned, &[Some(999)]),
            Err(HotbarError::NotSocial(999))
        );
        assert_eq!(
            s.check_hotbar(&learned, &[None; HOTBAR_SLOTS + 1]),
            Err(HotbarError::TooMany)
        );
        assert_eq!(s.check_hotbar(&learned, &[]), Ok([None; HOTBAR_SLOTS]));
    }

    #[test]
    fn default_and_trimmed_hotbars() {
        let s = shipped();
        let id = |k: &str| s.by_key(k).unwrap().id;
        // Learned out of order, with a dueling spell among them.
        let learned = [id("rigora"), id("torpeo"), id("lucerna"), id("volucra")];
        let bar = s.default_hotbar(&learned);
        assert_eq!(
            &bar[..4],
            &[
                Some(id("lucerna")),
                Some(id("volucra")),
                Some(id("rigora")),
                None
            ]
        );
        let every: Vec<SpellId> = s.spells.iter().map(|d| d.id).collect();
        assert!(s.default_hotbar(&every).iter().all(Option::is_some));
        // A stored bar keeps its slots; what is no longer allowed empties.
        let stored = [
            None,
            Some(id("volucra")),
            Some(id("torpeo")),
            Some(id("custodio")),
            Some(id("volucra")),
            Some(id("lucerna")),
        ];
        let trimmed = s.trim_hotbar(&learned, &stored);
        assert_eq!(
            &trimmed[..6],
            &[
                None,
                Some(id("volucra")),
                None,
                None,
                None,
                Some(id("lucerna"))
            ]
        );
    }

    #[test]
    fn learned_spells_fill_the_first_empty_slots() {
        let s = shipped();
        let id = |k: &str| s.by_key(k).unwrap().id;
        let mut bar: Hotbar = [None; HOTBAR_SLOTS];
        bar[0] = Some(id("lucerna"));
        bar[2] = Some(id("elevara"));
        assert!(s.hotbar_add(
            &mut bar,
            &[id("torpeo"), id("tinctura"), id("lucerna"), id("adveni")]
        ));
        assert_eq!(bar[1], Some(id("tinctura")));
        assert_eq!(bar[3], Some(id("adveni")));
        assert_eq!(bar.iter().flatten().count(), 4);
        // Nothing new: no change.
        assert!(!s.hotbar_add(&mut bar, &[id("lucerna"), id("torpeo")]));
        // A full bar takes nothing more.
        let mut full: Hotbar = [Some(id("lucerna")); HOTBAR_SLOTS];
        assert!(!s.hotbar_add(&mut full, &[id("custodio")]));
    }

    #[test]
    fn bad_spells_are_refused() {
        let g = crate::gesture::tests::shipped();
        let mut v: serde_json::Value = serde_json::from_str(SHIPPED).unwrap();
        let err = |v: &serde_json::Value| {
            Spells::from_json(&v.to_string(), &g)
                .unwrap_err()
                .to_string()
        };
        let mut gesture = v.clone();
        gesture["spells"][0]["gesture"] = "wiggle".into();
        assert!(err(&gesture).contains("unknown gesture"));
        let mut colour = v.clone();
        colour["spells"][0]["colour"] = "red".into();
        assert!(err(&colour).contains("colour"));
        let mut dup = v.clone();
        dup["spells"][1]["id"] = dup["spells"][0]["id"].clone();
        assert!(err(&dup).contains("unique"));
        v["spells"][0]["unlock"]["level"] = 30.into();
        assert!(err(&v).contains("unlock"));
    }
}
