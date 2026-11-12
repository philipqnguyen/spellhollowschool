//! NPCs in the open castle (docs/architecture.md, "NPCs"): who they are
//! (`data/npcs/roster.json`), what they look like (`looks.json`), where the
//! talking portraits hang (`portraits.json`), where the others walk on the
//! game clock (`routines.json`), what they say (`lines.json`) and what they
//! give once a day (`gifts.json`).
//!
//! Five kinds ([`NpcKind`]): talking **portraits** (client only: placed,
//! animated and voiced on each player's own screen), the four house
//! **ghosts** and three resident **staff** (server entities walking their
//! routines, talking, giving a daily gift), **shopkeepers** (server
//! entities standing at the station they keep) and background **students**
//! (server entities walking to their classes by the schedule, capped).
//!
//! Everything here is pure and deterministic, shared by the server (routine
//! poses, gift rolls, validation of the shipped files) and the browser
//! client through sim-wasm (`NpcBook`: line picks, portrait placements, the
//! rules):
//!
//! - [`RoutinePlan::pose`]: where a routine NPC is at a real moment: each
//!   [`Step`] sets off at its game time from the previous step's point to
//!   its own along the NPC nav graph ([`NpcNavs`]: no hidden passage, no
//!   moving staircase flight, the NPC's own house's gated rooms only) at the
//!   walking speed, then stands there (or sits, or is hidden) until the
//!   next step. The day repeats every game day.
//! - [`Students::steps`]: a student's steps generated from the class
//!   schedule (to the door before each session, hidden inside it, out again
//!   and back to an idle spot; to bed at night).
//! - [`LinesFile::pick`]: a seeded, weighted pick among the lines whose
//!   conditions (house, period, House Cup standing, what the player wears,
//!   an unfound secret, the gift) hold.
//! - [`GiftTable::roll`]: a seeded, weighted roll among the gifts that are
//!   available; [`gift_day`] and [`gift_seed`] make it once per character,
//!   per NPC, per UTC day, the same on a retry.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::Arc;

use protocol::{Appearance, House};
use serde::{Deserialize, Serialize};

use crate::catalog::{Catalog, Creator, ItemKind, Palette};
use crate::clock::{ClockRules, GAME_DAY_SECS, Period, Schedule, TimeOfDay, valid_id};
use crate::house_cup::DAY_MS;
use crate::layout::Layout;
use crate::nav::{KeepOut, Nav};
use crate::world::World;

/// The schema version of every `data/npcs/*.json` file.
pub const NPCS_VERSION: u32 = 1;

/// Talking portraits in the castle: 15 to 20.
pub const PORTRAITS_MIN: usize = 15;
pub const PORTRAITS_MAX: usize = 20;
/// Lines per portrait: 20 to 30 (checked by [`LinesFile::check_counts`]).
pub const PORTRAIT_LINES_MIN: usize = 20;
pub const PORTRAIT_LINES_MAX: usize = 30;
/// Painted backdrops the portrait frame offers (`backdrop` 0..).
pub const PORTRAIT_BACKDROPS: u8 = 4;
/// The longest line, and the longest pass-by line (characters).
pub const LINE_CHARS_MAX: usize = 160;
pub const PASS_LINE_CHARS_MAX: usize = 90;
/// Placeholders a line may use, filled by the client: the player's name and
/// house, and the gift's place, item, lore title and points.
pub const PLACEHOLDERS: [&str; 6] = ["name", "house", "place", "item", "lore", "points"];
/// The most of one item a gift gives.
pub const GIFT_QUANTITY_MAX: u16 = 5;
/// The point id prefix that names a class door (`door:<class id>`: the
/// class's door interactable, from the schedule).
pub const DOOR_POINT_PREFIX: &str = "door:";

#[derive(Debug, thiserror::Error)]
pub enum NpcsError {
    #[error("NPC JSON is invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("NPC data is inconsistent: {0}")]
    Invalid(String),
}

fn invalid(msg: impl Into<String>) -> NpcsError {
    NpcsError::Invalid(msg.into())
}

fn check_version(file: &str, version: u32) -> Result<(), NpcsError> {
    if version != NPCS_VERSION {
        return Err(invalid(format!(
            "{file}: unsupported version {version} (expected {NPCS_VERSION})"
        )));
    }
    Ok(())
}

// ------------------------------------------------------------------ roster

/// What an NPC is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NpcKind {
    /// A talking portrait (client only, never an entity).
    Portrait,
    /// A house ghost (an entity; drifts through its common room).
    Ghost,
    /// Resident staff (an entity; walks a routine on the clock).
    Staff,
    /// Keeps a station (an entity standing at it).
    Shopkeeper,
    /// A background student (an entity; walks to classes, capped).
    Student,
    /// Stands at its desk (an entity at one point, like a shopkeeper) and
    /// talks through its feature's own dialogue, not `lines.json`: the clubs
    /// secretary (`data/clubs.json`). No walk, no gift, no station.
    Clerk,
}

impl NpcKind {
    pub const ALL: [NpcKind; 6] = [
        NpcKind::Portrait,
        NpcKind::Ghost,
        NpcKind::Staff,
        NpcKind::Shopkeeper,
        NpcKind::Student,
        NpcKind::Clerk,
    ];

    pub fn id(self) -> &'static str {
        match self {
            NpcKind::Portrait => "portrait",
            NpcKind::Ghost => "ghost",
            NpcKind::Staff => "staff",
            NpcKind::Shopkeeper => "shopkeeper",
            NpcKind::Student => "student",
            NpcKind::Clerk => "clerk",
        }
    }

    /// Whether the server spawns it as a castle entity.
    pub fn is_entity(self) -> bool {
        self != NpcKind::Portrait
    }

    /// Whether it stands at one point all day (a routine of one step).
    pub fn stands(self) -> bool {
        matches!(self, NpcKind::Shopkeeper | NpcKind::Clerk)
    }

    /// Whether it walks a routine (`routines.json` or the student generator).
    pub fn walks(self) -> bool {
        matches!(self, NpcKind::Ghost | NpcKind::Staff | NpcKind::Student)
    }

    /// Whether it may give a daily gift (`gifts.json`).
    pub fn gives(self) -> bool {
        matches!(self, NpcKind::Ghost | NpcKind::Staff)
    }
}

/// The numbers every NPC rule uses (`roster.json` `rules`). The portrait
/// anti-annoyance rules are binding: portraits must never nag a player
/// (docs/architecture.md, "NPCs").
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NpcRules {
    /// The client offers "Talk" within this distance (metres, horizontal,
    /// the same floor).
    pub talk_radius_m: f32,
    /// The server accepts `NpcInteract` within this distance (more than
    /// `talk_radius_m`: the NPC may have walked on meanwhile).
    pub talk_reach_m: f32,
    /// A portrait notices a player passing within this distance.
    pub pass_radius_m: f32,
    /// Seconds before the same portrait notices the same player again.
    pub pass_cooldown_s: u32,
    /// Seconds between any two pass-by lines on one screen (walking past a
    /// row of portraits gives one line, not five).
    pub pass_gap_s: u32,
    /// Percent of noticed passes that get a short line (the rest a nod).
    pub pass_line_pct: u8,
    /// A portrait is alive (breathes, blinks, shifts) within this distance.
    pub alive_radius_m: f32,
    /// A portrait's head follows the player within this distance.
    pub look_radius_m: f32,
    /// How long a subtitle line stays up (ms).
    pub subtitle_ms: u32,
    /// Lines in one conversation (each interact shows the next).
    pub talk_lines_max: u8,
    /// Percent of portrait talks that start with a hint toward an unfound
    /// secret, when the portrait has one.
    pub hint_pct: u8,
    /// Background students spawned at once, castle-wide (the pool's size
    /// may not exceed it).
    pub students_max: u8,
    /// Students in one player's visible set at once (the server's cap).
    pub students_in_view_max: u8,
    /// Metres added to a student's visibility score (players rank first).
    pub student_view_penalty_m: f32,
    /// Walking speed of staff and students (m/s, real time).
    pub walk_speed_mps: f32,
    /// A ghost's drifting speed (m/s).
    pub ghost_speed_mps: f32,
    /// How high a ghost floats above the ground (m; the client draws it).
    pub ghost_float_m: f32,
}

impl NpcRules {
    fn validate(&self) -> Result<(), String> {
        let positive = [
            ("talk_radius_m", self.talk_radius_m),
            ("pass_radius_m", self.pass_radius_m),
            ("alive_radius_m", self.alive_radius_m),
            ("look_radius_m", self.look_radius_m),
            ("walk_speed_mps", self.walk_speed_mps),
            ("ghost_speed_mps", self.ghost_speed_mps),
        ];
        for (name, v) in positive {
            if !(v.is_finite() && v > 0.0) {
                return Err(format!("rules.{name} must be positive"));
            }
        }
        if !(self.talk_reach_m >= self.talk_radius_m && self.talk_reach_m <= 8.0) {
            return Err("rules.talk_reach_m: at least talk_radius_m, at most 8".into());
        }
        if self.pass_cooldown_s < 120 {
            return Err("rules.pass_cooldown_s: at least two minutes (a few minutes)".into());
        }
        if self.pass_line_pct > 100 || self.hint_pct > 100 {
            return Err("rules: percentages are 0-100".into());
        }
        if self.look_radius_m > self.alive_radius_m || self.pass_radius_m > self.alive_radius_m {
            return Err("rules: look and pass radii lie within the alive radius".into());
        }
        if self.talk_lines_max == 0 || self.subtitle_ms < 1_000 {
            return Err("rules: a conversation has lines, a subtitle stays a second".into());
        }
        if self.students_in_view_max > self.students_max {
            return Err("rules.students_in_view_max: at most students_max".into());
        }
        if self.walk_speed_mps > 2.0 || self.ghost_speed_mps > 2.0 {
            return Err("rules: NPCs walk at most 2 m/s".into());
        }
        if !(self.student_view_penalty_m.is_finite() && self.student_view_penalty_m >= 0.0) {
            return Err("rules.student_view_penalty_m: 0 or more".into());
        }
        if !(self.ghost_float_m.is_finite() && (0.0..=1.0).contains(&self.ghost_float_m)) {
            return Err("rules.ghost_float_m: 0-1".into());
        }
        Ok(())
    }
}

/// One NPC (`roster.json` `npcs[]`). Display text, every name included,
/// lives only in the data.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct NpcDef {
    /// Stable id (`EntityInfo.npc`, `npc_gifts.npc`): lower-case, digits, `_`.
    pub id: String,
    pub kind: NpcKind,
    /// Display name (nameplate, talk card): our own invention, not taken
    /// from any existing work.
    pub name: String,
    /// Shown under the name ("Caretaker", "Ghost of ..."), if any.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    /// A house id: required for ghosts and students (their house), optional
    /// otherwise (nameplate colours).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub house: Option<String>,
    /// One line for the writers: who they are and how they talk.
    pub personality: String,
    /// Shopkeepers: the station interactable they keep (the client's
    /// interact sends `Interact { target: station }`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub station: Option<String>,
    /// Portraits only: today's hint opens every conversation.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub daily_hint: bool,
    /// The `lines.json` key whose lines this NPC says (default its id;
    /// students share a pool).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lines: Option<String>,
}

impl NpcDef {
    /// Its house, if it has one.
    pub fn house(&self) -> Option<House> {
        self.house.as_deref().and_then(House::from_id)
    }

    /// Its `lines.json` key.
    pub fn lines_key(&self) -> &str {
        self.lines.as_deref().unwrap_or(&self.id)
    }
}

/// `data/npcs/roster.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Roster {
    pub version: u32,
    pub rules: NpcRules,
    pub npcs: Vec<NpcDef>,
}

impl Roster {
    pub fn from_json(json: &str) -> Result<Roster, NpcsError> {
        let r: Roster = serde_json::from_str(json)?;
        r.validate()?;
        Ok(r)
    }

    pub fn get(&self, id: &str) -> Option<&NpcDef> {
        self.npcs.iter().find(|n| n.id == id)
    }

    pub fn of_kind(&self, kind: NpcKind) -> impl Iterator<Item = &NpcDef> {
        self.npcs.iter().filter(move |n| n.kind == kind)
    }

    fn validate(&self) -> Result<(), NpcsError> {
        check_version("roster.json", self.version)?;
        self.rules.validate().map_err(invalid)?;
        let mut ids = BTreeSet::new();
        for n in &self.npcs {
            let id = &n.id;
            if !valid_id(id) || !ids.insert(id.as_str()) {
                return Err(invalid(format!("npc {id:?}: ids are unique [a-z0-9_]")));
            }
            if !(1..=40).contains(&n.name.chars().count())
                || n.title
                    .as_ref()
                    .is_some_and(|t| !(1..=40).contains(&t.chars().count()))
            {
                return Err(invalid(format!("npc {id}: a name and title of 1-40")));
            }
            if !(1..=240).contains(&n.personality.chars().count()) {
                return Err(invalid(format!("npc {id}: a personality line of 1-240")));
            }
            if n.house.is_some() && n.house().is_none() {
                return Err(invalid(format!("npc {id}: unknown house")));
            }
            let needs_house = matches!(n.kind, NpcKind::Ghost | NpcKind::Student);
            if needs_house && n.house.is_none() {
                return Err(invalid(format!(
                    "npc {id}: ghosts and students have a house"
                )));
            }
            if (n.kind == NpcKind::Shopkeeper) != n.station.is_some() {
                return Err(invalid(format!(
                    "npc {id}: exactly shopkeepers keep a station"
                )));
            }
            if n.daily_hint && n.kind != NpcKind::Portrait {
                return Err(invalid(format!(
                    "npc {id}: only portraits give a daily hint"
                )));
            }
            if n.lines.as_ref().is_some_and(|k| !valid_id(k)) {
                return Err(invalid(format!("npc {id}: a lines key is an id")));
            }
            if n.lines.is_some() && n.kind != NpcKind::Student {
                return Err(invalid(format!("npc {id}: only students share lines")));
            }
        }
        let count = |k: NpcKind| self.of_kind(k).count();
        if !(PORTRAITS_MIN..=PORTRAITS_MAX).contains(&count(NpcKind::Portrait)) {
            return Err(invalid(format!(
                "{PORTRAITS_MIN}-{PORTRAITS_MAX} talking portraits"
            )));
        }
        for house in House::ALL {
            let ghosts = self
                .of_kind(NpcKind::Ghost)
                .filter(|g| g.house() == Some(house))
                .count();
            if ghosts != 1 {
                return Err(invalid(format!("one ghost per house ({house:?})")));
            }
        }
        if count(NpcKind::Staff) != 3 {
            return Err(invalid("three resident staff"));
        }
        let hints = self.npcs.iter().filter(|n| n.daily_hint).count();
        if !(1..=2).contains(&hints) {
            return Err(invalid("one or two portraits give a daily hint"));
        }
        let students = count(NpcKind::Student);
        if students == 0 || students > usize::from(self.rules.students_max) {
            return Err(invalid("1..=rules.students_max students"));
        }
        let stations: BTreeSet<_> = self
            .npcs
            .iter()
            .filter_map(|n| n.station.as_deref())
            .collect();
        if stations.len() != count(NpcKind::Shopkeeper) {
            return Err(invalid("one shopkeeper per station"));
        }
        Ok(())
    }

    /// The cross-check with the castle: every shopkeeper's station is a
    /// station interactable (`InteractableKind::station`) of the layout.
    pub fn validate_with(&self, layout: &Layout) -> Result<(), NpcsError> {
        for n in self.of_kind(NpcKind::Shopkeeper) {
            let station = n.station.as_deref().unwrap_or_default();
            let ok = layout
                .interactable(station)
                .is_some_and(|i| i.kind.station().is_some());
            if !ok {
                return Err(invalid(format!(
                    "npc {}: station {station:?} is not a station interactable",
                    n.id
                )));
            }
        }
        Ok(())
    }
}

// ------------------------------------------------------------------- looks

/// `data/npcs/looks.json`: every NPC's appearance (portrait subjects
/// included), catalog clothing in palette dyes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Looks {
    pub version: u32,
    pub looks: BTreeMap<String, Appearance>,
}

impl Looks {
    pub fn from_json(json: &str) -> Result<Looks, NpcsError> {
        let l: Looks = serde_json::from_str(json)?;
        check_version("looks.json", l.version)?;
        Ok(l)
    }

    pub fn get(&self, npc: &str) -> Option<&Appearance> {
        self.looks.get(npc)
    }

    /// Exactly one look per roster NPC, each a valid character look
    /// (`Creator::check_appearance`: shipped hair of its body, wearable
    /// catalog clothing, palette dyes).
    pub fn validate_with(
        &self,
        roster: &Roster,
        creator: &Creator,
        catalog: &Catalog,
        palette: &Palette,
    ) -> Result<(), NpcsError> {
        for n in &roster.npcs {
            let look = self
                .looks
                .get(&n.id)
                .ok_or_else(|| invalid(format!("looks.json: no look for {}", n.id)))?;
            creator
                .check_appearance(look, catalog, palette)
                .map_err(|e| invalid(format!("looks.json {}: {e}", n.id)))?;
        }
        if let Some(extra) = self.looks.keys().find(|k| roster.get(k).is_none()) {
            return Err(invalid(format!("looks.json: {extra} is not in the roster")));
        }
        Ok(())
    }
}

// --------------------------------------------------------------- portraits

/// The wall a portrait hangs on: an edge of its surface's rectangle (the
/// wall's inner face). The portrait faces into the surface.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WallEdge {
    /// The wall at `min.x`, facing +x.
    XMin,
    /// The wall at `max.x`, facing -x.
    XMax,
    /// The wall at `min.z`, facing +z.
    ZMin,
    /// The wall at `max.z`, facing -z.
    ZMax,
}

impl WallEdge {
    /// The facing (yaw 0 faces +z, as everywhere).
    pub fn yaw(self) -> f32 {
        use std::f32::consts::{FRAC_PI_2, PI};
        match self {
            WallEdge::XMin => FRAC_PI_2,
            WallEdge::XMax => -FRAC_PI_2,
            WallEdge::ZMin => 0.0,
            WallEdge::ZMax => PI,
        }
    }
}

fn default_portrait_y() -> f32 {
    2.0
}

fn default_portrait_size() -> [f32; 2] {
    [1.0, 1.3]
}

/// Where one talking portrait hangs (`portraits.json` `portraits[]`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PortraitPlacement {
    /// Its roster id (kind `portrait`).
    pub npc: String,
    /// The surface whose wall it hangs on (not secret, not a moving flight).
    pub surface: String,
    pub edge: WallEdge,
    /// The canvas centre's distance along the edge from its lower-coordinate
    /// end (metres).
    pub t: f32,
    /// The canvas centre's height above the surface's floor (metres).
    #[serde(default = "default_portrait_y")]
    pub y: f32,
    /// How far the wall's face stands back from the surface edge (metres,
    /// positive: outward), tuned to the built wall.
    #[serde(default)]
    pub inset: f32,
    /// The canvas (inside the frame), width and height in metres.
    #[serde(default = "default_portrait_size")]
    pub size: [f32; 2],
    /// Which painted backdrop (0..`PORTRAIT_BACKDROPS`).
    #[serde(default)]
    pub backdrop: u8,
}

/// A portrait placed in the castle ([`PortraitsFile::resolve`]).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PlacedPortrait {
    pub npc: String,
    /// The canvas centre on the wall's face (metres, world).
    pub pos: [f32; 3],
    /// Facing into the room.
    pub yaw: f32,
    pub size: [f32; 2],
    pub backdrop: u8,
    /// The floor level of its surface (the client's floor-aware culling).
    pub level: i32,
}

/// `data/npcs/portraits.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PortraitsFile {
    pub version: u32,
    pub portraits: Vec<PortraitPlacement>,
}

impl PortraitsFile {
    pub fn from_json(json: &str) -> Result<PortraitsFile, NpcsError> {
        let p: PortraitsFile = serde_json::from_str(json)?;
        check_version("portraits.json", p.version)?;
        Ok(p)
    }

    /// Every placement on its wall, in file order (checked: one per roster
    /// portrait, each on a plain surface of the layout and within its edge,
    /// sizes and heights in range, no two closer than 3 m on one floor).
    pub fn resolve(
        &self,
        roster: &Roster,
        layout: &Layout,
    ) -> Result<Vec<PlacedPortrait>, NpcsError> {
        let mut out = Vec::with_capacity(self.portraits.len());
        for p in &self.portraits {
            let id = &p.npc;
            if !roster.get(id).is_some_and(|n| n.kind == NpcKind::Portrait) {
                return Err(invalid(format!(
                    "portraits.json: {id} is not a roster portrait"
                )));
            }
            if out.iter().any(|o: &PlacedPortrait| &o.npc == id) {
                return Err(invalid(format!("portraits.json: {id} twice")));
            }
            let s = layout
                .surfaces
                .iter()
                .find(|s| s.id == p.surface)
                .ok_or_else(|| invalid(format!("portraits.json {id}: no surface {}", p.surface)))?;
            if s.secret || s.moving.is_some() {
                return Err(invalid(format!("portraits.json {id}: a plain surface")));
            }
            let [w, h] = p.size;
            if !((0.6..=1.6).contains(&w) && (0.8..=2.0).contains(&h)) {
                return Err(invalid(format!(
                    "portraits.json {id}: size 0.6-1.6 x 0.8-2.0"
                )));
            }
            if !((1.2..=4.0).contains(&p.y) && (-0.5..=1.0).contains(&p.inset)) {
                return Err(invalid(format!(
                    "portraits.json {id}: y 1.2-4, inset -0.5-1"
                )));
            }
            if p.backdrop >= PORTRAIT_BACKDROPS {
                return Err(invalid(format!("portraits.json {id}: backdrop 0-3")));
            }
            let along_x = matches!(p.edge, WallEdge::ZMin | WallEdge::ZMax);
            let (lo, hi) = if along_x {
                (s.min[0], s.max[0])
            } else {
                (s.min[1], s.max[1])
            };
            let half = w / 2.0 + 0.2;
            if !(p.t.is_finite() && p.t >= half && p.t <= hi - lo - half) {
                return Err(invalid(format!(
                    "portraits.json {id}: t {} is off its {:.1} m wall",
                    p.t,
                    hi - lo
                )));
            }
            let a = lo + p.t;
            let (x, z) = match p.edge {
                WallEdge::XMin => (s.min[0] - p.inset, a),
                WallEdge::XMax => (s.max[0] + p.inset, a),
                WallEdge::ZMin => (a, s.min[1] - p.inset),
                WallEdge::ZMax => (a, s.max[1] + p.inset),
            };
            // The floor at the wall foot (just inside the surface's edge).
            let (fx, fz) = match p.edge {
                WallEdge::XMin => (s.min[0] + 0.05, a),
                WallEdge::XMax => (s.max[0] - 0.05, a),
                WallEdge::ZMin => (a, s.min[1] + 0.05),
                WallEdge::ZMax => (a, s.max[1] - 0.05),
            };
            let floor = s.height_at(fx, fz);
            let pos = [x, floor + p.y, z];
            for o in &out {
                let d = ((o.pos[0] - pos[0]).powi(2) + (o.pos[2] - pos[2]).powi(2)).sqrt();
                if o.level == s.level && d < 3.0 {
                    return Err(invalid(format!(
                        "portraits.json: {id} and {} are closer than 3 m",
                        o.npc
                    )));
                }
            }
            out.push(PlacedPortrait {
                npc: id.clone(),
                pos,
                yaw: p.edge.yaw(),
                size: p.size,
                backdrop: p.backdrop,
                level: s.level,
            });
        }
        if let Some(missing) = roster
            .of_kind(NpcKind::Portrait)
            .find(|n| !out.iter().any(|o| o.npc == n.id))
        {
            return Err(invalid(format!(
                "portraits.json: {} hangs nowhere",
                missing.id
            )));
        }
        Ok(out)
    }
}

// ---------------------------------------------------------------- routines

/// A named spot in the castle (`routines.json` `points`).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Point {
    /// On walkable ground (metres).
    pub pos: [f32; 3],
    /// Facing while standing there (default: the way it arrived).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub yaw: Option<f32>,
}

/// What an NPC does once it has arrived.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepAnim {
    #[default]
    Idle,
    /// The sit emote's pose (`Anim::Sit`), on a bench or the floor.
    Sit,
}

/// One step of a routine: at game time `at`, set off for point `to`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Step {
    pub at: TimeOfDay,
    /// A `points` id, or `door:<class id>` (that class's door).
    pub to: String,
    #[serde(default, skip_serializing_if = "is_idle")]
    pub anim: StepAnim,
    /// Gone (in class, in bed) from `at` until the next step: the server
    /// removes the entity; it reappears at `to` when the next step starts.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub hidden: bool,
}

fn is_idle(a: &StepAnim) -> bool {
    *a == StepAnim::Idle
}

/// A day of steps (`routines.json` `routines.<npc id>`), sorted by `at`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Routine {
    pub steps: Vec<Step>,
}

/// One student's week-day: the classes they go to, where they idle between
/// them and where they sleep (`routines.json` `students.spec.<npc id>`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StudentSpec {
    /// Class ids of the schedule (sessions in the night period are skipped).
    pub classes: Vec<String>,
    /// A point: where they spend free time (idle or sitting).
    pub idle: String,
    #[serde(default, skip_serializing_if = "is_idle")]
    pub idle_anim: StepAnim,
    /// A point: where they go to bed (hidden from `bed` to `rise`).
    pub night: String,
}

fn default_lead_mins() -> u32 {
    25
}

fn default_bed() -> TimeOfDay {
    TimeOfDay(22 * 3_600)
}

fn default_rise() -> TimeOfDay {
    TimeOfDay(6 * 3_600 + 30 * 60)
}

fn default_bed_walk_mins() -> u32 {
    45
}

/// The student generator's rules (`routines.json` `students`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Students {
    /// Game minutes before a session a student sets off for its door.
    #[serde(default = "default_lead_mins")]
    pub lead_mins: u32,
    #[serde(default = "default_bed")]
    pub bed: TimeOfDay,
    #[serde(default = "default_rise")]
    pub rise: TimeOfDay,
    /// Game minutes from `bed` until they are hidden (time to walk to the
    /// night point).
    #[serde(default = "default_bed_walk_mins")]
    pub bed_walk_mins: u32,
    pub spec: BTreeMap<String, StudentSpec>,
}

/// Game minutes in seconds.
const fn mins(m: u32) -> u32 {
    m * 60
}

/// A student step's point with its slot (`<point>@<k>`, k >= 1): students
/// who share a class door or a night point stand side by side there, not in
/// one spot (slot 0 is the point itself).
pub const SLOT_SEPARATOR: char = '@';

/// How far apart (m) students stand in neighbouring slots.
pub const SLOT_SPACING_M: f32 = 0.7;

fn slotted(point: &str, slot: u32) -> String {
    if slot == 0 {
        point.to_string()
    } else {
        format!("{point}{SLOT_SEPARATOR}{slot}")
    }
}

/// Where slot `k` of `p` stands on `nav`: beside it (across its facing, or
/// along x), alternating sides; the point itself when neither side is
/// walkable.
fn slot_point(p: Point, k: u32, nav: &Nav) -> Point {
    if k == 0 {
        return p;
    }
    let (sx, sz) = match p.yaw {
        Some(y) => (y.cos(), -y.sin()),
        None => (1.0, 0.0),
    };
    let reach = SLOT_SPACING_M * k.div_ceil(2) as f32;
    let side = if k % 2 == 1 { 1.0 } else { -1.0 };
    for d in [
        side * reach,
        -side * reach,
        side * reach * 0.5,
        -side * reach * 0.5,
    ] {
        let q = [p.pos[0] + sx * d, p.pos[1], p.pos[2] + sz * d];
        if nav.surface_at(q).is_some() {
            return Point { pos: q, yaw: p.yaw };
        }
    }
    p
}

impl Students {
    /// Student `id`'s steps, from the schedule: at `rise` they appear at
    /// their `night` point and walk to `idle`; for each session of their
    /// classes (in time order, skipping night sessions and any that would
    /// overlap the previous one) they set off `lead_mins` before it for the
    /// class door, are hidden through the session and come out at its end,
    /// then walk back to `idle`; at `bed` they walk to `night` and are
    /// hidden from `bed_walk_mins` later until `rise`.
    pub fn steps(
        &self,
        id: &str,
        schedule: &Schedule,
        clock: &ClockRules,
    ) -> Result<Vec<Step>, String> {
        let spec = self
            .spec
            .get(id)
            .ok_or_else(|| format!("students: no spec for {id}"))?;
        let game_per_real = f64::from(GAME_DAY_SECS) / f64::from(clock.day_real_secs);
        let mut sessions: Vec<(u32, u32, &str)> = Vec::new();
        for class in &spec.classes {
            let c = schedule
                .class(class)
                .ok_or_else(|| format!("students {id}: unknown class {class}"))?;
            let dur = (f64::from(c.duration_secs) * game_per_real).round() as u32;
            for s in &c.sessions {
                let start = s.secs();
                if clock.period_at_secs(start) == Period::Night {
                    continue;
                }
                sessions.push((start, start + dur, class.as_str()));
            }
        }
        sessions.sort();
        let (rise, bed, lead) = (self.rise.secs(), self.bed.secs(), mins(self.lead_mins));
        let at = |s: u32| TimeOfDay(s % GAME_DAY_SECS);
        let step = |s: u32, to: &str, anim: StepAnim, hidden: bool| Step {
            at: at(s),
            to: to.to_string(),
            anim,
            hidden,
        };
        // Students sharing a door or a night point: side by side there, and
        // setting off a game minute apart (they would walk one path as one).
        let rank = |shares: &dyn Fn(&StudentSpec) -> bool| -> u32 {
            self.spec
                .iter()
                .filter(|(_, s)| shares(s))
                .position(|(k, _)| k == id)
                .unwrap_or(0) as u32
        };
        let night_slot = rank(&|s| s.night == spec.night);
        let night = slotted(&spec.night, night_slot);
        let mut out = vec![
            step(rise, &night, StepAnim::Idle, false),
            step(
                rise + mins(5 + night_slot),
                &spec.idle,
                spec.idle_anim,
                false,
            ),
        ];
        let mut free_from = rise + mins(10 + night_slot);
        for (start, end, class) in sessions {
            if start < free_from + lead || end + mins(15) > bed {
                // Too soon after the last one, or too close to bedtime.
                continue;
            }
            let slot = rank(&|s| s.classes.iter().any(|c| c == class));
            let door = slotted(&format!("{DOOR_POINT_PREFIX}{class}"), slot);
            out.push(step(start - lead, &door, StepAnim::Idle, false));
            out.push(step(start, &door, StepAnim::Idle, true));
            out.push(step(end, &door, StepAnim::Idle, false));
            out.push(step(
                end + mins(3 + slot),
                &spec.idle,
                spec.idle_anim,
                false,
            ));
            free_from = end + mins(3 + slot);
        }
        out.push(step(bed + mins(night_slot), &night, StepAnim::Idle, false));
        out.push(step(
            bed + mins(self.bed_walk_mins),
            &night,
            StepAnim::Idle,
            true,
        ));
        out.sort_by_key(|s| s.at);
        Ok(out)
    }
}

/// `data/npcs/routines.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RoutinesFile {
    pub version: u32,
    pub points: BTreeMap<String, Point>,
    /// Ghosts, staff and shopkeepers (a shopkeeper's routine is one step:
    /// where it stands).
    pub routines: BTreeMap<String, Routine>,
    pub students: Students,
}

impl RoutinesFile {
    pub fn from_json(json: &str) -> Result<RoutinesFile, NpcsError> {
        let r: RoutinesFile = serde_json::from_str(json)?;
        check_version("routines.json", r.version)?;
        Ok(r)
    }

    /// Every entity NPC's steps: its routine, or for a student the
    /// generated ones.
    pub fn steps_of(
        &self,
        npc: &NpcDef,
        schedule: &Schedule,
        clock: &ClockRules,
    ) -> Result<Vec<Step>, String> {
        if npc.kind == NpcKind::Student {
            return self.students.steps(&npc.id, schedule, clock);
        }
        self.routines
            .get(&npc.id)
            .map(|r| r.steps.clone())
            .ok_or_else(|| format!("routines.json: no routine for {}", npc.id))
    }

    /// Plans every entity NPC of the roster against the castle (the
    /// server's startup; the shipped-file test): each routine's walks
    /// exist and fit between their steps. In roster order.
    pub fn plan_all(
        &self,
        roster: &Roster,
        navs: &NpcNavs,
        schedule: &Schedule,
    ) -> Result<Vec<(String, RoutinePlan)>, NpcsError> {
        let clock = &schedule.clock;
        let mut out = Vec::new();
        for n in roster.npcs.iter().filter(|n| n.kind.is_entity()) {
            let steps = self.steps_of(n, schedule, clock).map_err(invalid)?;
            if n.kind.stands() && (steps.len() != 1 || steps[0].hidden) {
                return Err(invalid(format!(
                    "routines.json {}: a {} stands at one point",
                    n.id,
                    n.kind.id()
                )));
            }
            let nav = navs.for_npc(n);
            let speed = if n.kind == NpcKind::Ghost {
                roster.rules.ghost_speed_mps
            } else {
                roster.rules.walk_speed_mps
            };
            // A student's `<point>@<k>`: slot k beside the point (`slot_point`).
            let resolve = |p: &str| match p.split_once(SLOT_SEPARATOR) {
                Some((base, k)) => Some(slot_point(
                    self.point(base, schedule, navs.layout())?,
                    k.parse().ok()?,
                    nav,
                )),
                None => self.point(p, schedule, navs.layout()),
            };
            let plan = RoutinePlan::new(&steps, resolve, nav, speed, clock)
                .map_err(|e| invalid(format!("routine {}: {e}", n.id)))?;
            out.push((n.id.clone(), plan));
        }
        let used: BTreeSet<_> = out.iter().map(|(id, _)| id.as_str()).collect();
        if let Some(extra) = self
            .routines
            .keys()
            .chain(self.students.spec.keys())
            .find(|k| !used.contains(k.as_str()))
        {
            return Err(invalid(format!(
                "routines.json: {extra} is not a walking roster NPC"
            )));
        }
        Ok(out)
    }

    /// A point id's place: a `points` entry, or `door:<class>` (the class
    /// door interactable's point, facing the door).
    pub fn point(&self, id: &str, schedule: &Schedule, layout: &Layout) -> Option<Point> {
        if let Some(class) = id.strip_prefix(DOOR_POINT_PREFIX) {
            let door = layout.interactable(&schedule.class(class)?.door)?;
            return Some(Point {
                pos: door.pos,
                yaw: Some(door.yaw + std::f32::consts::PI),
            });
        }
        self.points.get(id).copied()
    }
}

/// The NPC nav graphs: the castle without its moving staircase flights or
/// hidden passages, one for the public ground and one per house (its own
/// gated rooms added). Built once at startup (a few hundred milliseconds).
pub struct NpcNavs {
    layout: Layout,
    public: Nav,
    houses: Vec<(House, Nav)>,
}

impl NpcNavs {
    /// From the whole castle layout (every flight position) and the
    /// furniture footprints to walk round (`data/world/keep_out.json`).
    pub fn new(layout: &Layout, keep_out: &[KeepOut]) -> Result<NpcNavs, NpcsError> {
        let mut fixed = layout.clone();
        fixed.surfaces.retain(|s| s.moving.is_none() && !s.secret);
        let world = World::new(fixed.clone()).map_err(|e| invalid(format!("npc nav: {e}")))?;
        let public = Nav::new(Arc::new(world.clone()), keep_out, |s| s.house.is_none());
        let mut houses = Vec::new();
        for house in House::ALL {
            let w = world
                .for_house(house)
                .map_err(|e| invalid(format!("npc nav {house:?}: {e}")))?;
            houses.push((house, Nav::new(Arc::new(w), keep_out, |_| true)));
        }
        Ok(NpcNavs {
            layout: fixed,
            public,
            houses,
        })
    }

    /// The castle as NPCs walk it (no moving flights, no passages).
    pub fn layout(&self) -> &Layout {
        &self.layout
    }

    /// The graph NPC `n` walks: its house's for ghosts and students (they
    /// may enter their own common room), the public one otherwise.
    pub fn for_npc(&self, n: &NpcDef) -> &Nav {
        match (n.kind, n.house()) {
            (NpcKind::Ghost | NpcKind::Student, Some(h)) => self.house(h),
            _ => &self.public,
        }
    }

    pub fn public(&self) -> &Nav {
        &self.public
    }

    pub fn house(&self, house: House) -> &Nav {
        &self
            .houses
            .iter()
            .find(|(h, _)| *h == house)
            .expect("every house")
            .1
    }
}

/// What an NPC is doing at a moment ([`RoutinePlan::pose`]).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Pose {
    pub pos: [f32; 3],
    pub yaw: f32,
    pub activity: Activity,
}

/// The pose's activity (the server maps it to `Anim`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Activity {
    Walk,
    Idle,
    Sit,
    /// Not in the castle: no entity.
    Hidden,
}

#[derive(Debug, Clone)]
struct Leg {
    /// Game seconds since midnight.
    at: u32,
    path: Vec<[f32; 3]>,
    /// Cumulative lengths along `path` (same length as `path`).
    cum: Vec<f32>,
    /// Facing once arrived.
    rest_yaw: f32,
    anim: StepAnim,
    hidden: bool,
}

/// A routine planned on the castle: each step's walk precomputed.
#[derive(Debug, Clone)]
pub struct RoutinePlan {
    legs: Vec<Leg>,
    /// Metres per game second (the walking speed at the clock's rate).
    speed_game: f64,
    clock_epoch: i64,
    day_ms: i64,
}

fn yaw_of(from: [f32; 3], to: [f32; 3]) -> Option<f32> {
    let (dx, dz) = (to[0] - from[0], to[2] - from[2]);
    (dx * dx + dz * dz > 1e-6).then(|| dx.atan2(dz))
}

impl RoutinePlan {
    /// Plans `steps` (sorted by `at`, at least one, distinct times) on
    /// `nav`: each step's walk from the previous step's point (the last
    /// step's, for the first) must exist and take no longer than the game
    /// time until the next step at `speed` m/s.
    pub fn new(
        steps: &[Step],
        resolve: impl Fn(&str) -> Option<Point>,
        nav: &Nav,
        speed: f32,
        clock: &ClockRules,
    ) -> Result<RoutinePlan, String> {
        if steps.is_empty() {
            return Err("no steps".into());
        }
        if steps.windows(2).any(|w| w[0].at >= w[1].at) {
            return Err("steps are in time order, at distinct times".into());
        }
        let game_per_real = f64::from(GAME_DAY_SECS) / f64::from(clock.day_real_secs);
        let speed_game = f64::from(speed) / game_per_real;
        let points: Vec<Point> = steps
            .iter()
            .map(|s| resolve(&s.to).ok_or_else(|| format!("unknown point {}", s.to)))
            .collect::<Result<_, _>>()?;
        for (s, p) in steps.iter().zip(&points) {
            if nav.surface_at(p.pos).is_none() {
                return Err(format!("point {} is off the NPC's ground", s.to));
            }
        }
        let n = steps.len();
        let mut legs = Vec::with_capacity(n);
        for k in 0..n {
            let step = &steps[k];
            let to = points[k];
            let from = points[(k + n - 1) % n];
            let path = if step.hidden || n == 1 {
                vec![to.pos]
            } else {
                nav.path(from.pos, to.pos).ok_or_else(|| {
                    format!(
                        "no way from {} to {} (step at {})",
                        steps[(k + n - 1) % n].to,
                        step.to,
                        step.at
                    )
                })?
            };
            let mut cum = Vec::with_capacity(path.len());
            let mut total = 0.0f32;
            for (i, p) in path.iter().enumerate() {
                if i > 0 {
                    let q = path[i - 1];
                    total +=
                        ((p[0] - q[0]).powi(2) + (p[1] - q[1]).powi(2) + (p[2] - q[2]).powi(2))
                            .sqrt();
                }
                cum.push(total);
            }
            let next_at = steps[(k + 1) % n].at.secs();
            let gap = (next_at + GAME_DAY_SECS - step.at.secs()) % GAME_DAY_SECS;
            let gap = if gap == 0 { GAME_DAY_SECS } else { gap };
            let walk = f64::from(total) / speed_game;
            if walk > f64::from(gap) {
                return Err(format!(
                    "the walk to {} at {} takes {:.0} game minutes, the step lasts {}",
                    step.to,
                    step.at,
                    walk / 60.0,
                    gap / 60
                ));
            }
            let arrived = path
                .windows(2)
                .rev()
                .find_map(|w| yaw_of(w[0], w[1]))
                .unwrap_or(0.0);
            legs.push(Leg {
                at: step.at.secs(),
                path,
                cum,
                rest_yaw: to.yaw.unwrap_or(arrived),
                anim: step.anim,
                hidden: step.hidden,
            });
        }
        Ok(RoutinePlan {
            legs,
            speed_game,
            clock_epoch: clock.epoch_unix_ms,
            day_ms: clock.day_ms(),
        })
    }

    /// The pose at real time `unix_ms` (the clock this plan was made with).
    pub fn pose(&self, unix_ms: i64) -> Pose {
        let in_day = (unix_ms - self.clock_epoch).rem_euclid(self.day_ms);
        let secs = in_day as f64 * f64::from(GAME_DAY_SECS) / self.day_ms as f64;
        self.pose_at_secs(secs)
    }

    /// The pose at `secs` game seconds after game midnight.
    pub fn pose_at_secs(&self, secs: f64) -> Pose {
        let secs = secs.rem_euclid(f64::from(GAME_DAY_SECS));
        let k = self
            .legs
            .iter()
            .rposition(|l| f64::from(l.at) <= secs)
            .unwrap_or(self.legs.len() - 1);
        let leg = &self.legs[k];
        let since = (secs - f64::from(leg.at)).rem_euclid(f64::from(GAME_DAY_SECS));
        let end = *leg.path.last().expect("a leg has a point");
        if leg.hidden {
            return Pose {
                pos: end,
                yaw: leg.rest_yaw,
                activity: Activity::Hidden,
            };
        }
        let d = (since * self.speed_game) as f32;
        let total = *leg.cum.last().expect("same length as path");
        if d >= total {
            return Pose {
                pos: end,
                yaw: leg.rest_yaw,
                activity: match leg.anim {
                    StepAnim::Idle => Activity::Idle,
                    StepAnim::Sit => Activity::Sit,
                },
            };
        }
        let i = leg.cum.partition_point(|&c| c <= d).max(1);
        let (a, b) = (leg.path[i - 1], leg.path[i]);
        let seg = leg.cum[i] - leg.cum[i - 1];
        let t = if seg > 0.0 {
            (d - leg.cum[i - 1]) / seg
        } else {
            1.0
        };
        Pose {
            pos: [
                a[0] + (b[0] - a[0]) * t,
                a[1] + (b[1] - a[1]) * t,
                a[2] + (b[2] - a[2]) * t,
            ],
            yaw: yaw_of(a, b).unwrap_or(leg.rest_yaw),
            activity: Activity::Walk,
        }
    }

    /// Every point this plan walks through (tests, debug drawing).
    pub fn waypoints(&self) -> impl Iterator<Item = [f32; 3]> + '_ {
        self.legs.iter().flat_map(|l| l.path.iter().copied())
    }
}

// ------------------------------------------------------------------- lines

/// When a line is said.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LineWhen {
    /// A portrait, as the player walks past (subtitle; short).
    Pass,
    /// On interact: a conversation's lines.
    Talk,
    /// A portrait's vague hint toward a secret the player has not found
    /// (`secret` required).
    Hint,
    /// With today's gift (`gift` may narrow it to a kind).
    Gift,
    /// When today's gift was already given (`NpcReply::Talk { given_today }`).
    Given,
    /// A shopkeeper's greeting as the shop opens.
    Shop,
}

impl LineWhen {
    pub fn id(self) -> &'static str {
        match self {
            LineWhen::Pass => "pass",
            LineWhen::Talk => "talk",
            LineWhen::Hint => "hint",
            LineWhen::Gift => "gift",
            LineWhen::Given => "given",
            LineWhen::Shop => "shop",
        }
    }

    pub fn from_id(id: &str) -> Option<LineWhen> {
        [
            LineWhen::Pass,
            LineWhen::Talk,
            LineWhen::Hint,
            LineWhen::Gift,
            LineWhen::Given,
            LineWhen::Shop,
        ]
        .into_iter()
        .find(|w| w.id() == id)
    }
}

/// A line's house condition: the player's house the same as the NPC's,
/// another, or one of a list.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum HouseCond {
    Rel(HouseRel),
    Houses(Vec<String>),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HouseRel {
    Same,
    Other,
}

/// The House Cup this week, as the player sees it (`GET /api/house-cup`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CupStanding {
    /// The player's house leads alone.
    YoursLeads,
    /// Another house leads alone.
    OtherLeads,
    /// Two or more houses share the top score (or nobody has points).
    Tied,
}

/// A gift's kind (`GiftDef`, `protocol::NpcGift`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GiftKind {
    Lore,
    Item,
    HousePoint,
    Hint,
}

fn one() -> u8 {
    1
}

/// One line (`lines.json` `npcs.<key>[]`). Every condition given must hold.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Line {
    pub text: String,
    pub when: LineWhen,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub house: Option<HouseCond>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub period: Option<Vec<Period>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cup: Option<CupStanding>,
    /// The player wears an item with this catalog tag (`knitwear`,
    /// `formal`, `school`, ...).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wears: Option<String>,
    /// Hint lines: the secret (`secrets.json` id) it hints at; said only
    /// while the player has not found it. Vague: never a precise place.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub secret: Option<String>,
    /// Gift lines: only with this kind of gift.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gift: Option<GiftKind>,
    /// Relative weight in a pick, 1-10.
    #[serde(default = "one")]
    pub weight: u8,
}

/// What a line's conditions are checked against.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LineContext {
    /// The player's house id.
    pub house: String,
    /// The NPC's house id, if it has one.
    #[serde(default)]
    pub npc_house: Option<String>,
    pub period: Option<Period>,
    #[serde(default)]
    pub cup: Option<CupStanding>,
    /// Catalog tags of everything the player wears.
    #[serde(default)]
    pub worn_tags: Vec<String>,
    /// Secret ids the player has found (`GET /api/map`).
    #[serde(default)]
    pub found_secrets: Vec<String>,
    /// The gift being given (gift lines).
    #[serde(default)]
    pub gift: Option<GiftKind>,
}

impl Line {
    /// Whether every condition holds in `cx`.
    pub fn holds(&self, cx: &LineContext) -> bool {
        let house_ok = match &self.house {
            None => true,
            Some(HouseCond::Rel(HouseRel::Same)) => {
                cx.npc_house.as_deref() == Some(cx.house.as_str())
            }
            Some(HouseCond::Rel(HouseRel::Other)) => {
                cx.npc_house.as_deref() != Some(cx.house.as_str())
            }
            Some(HouseCond::Houses(list)) => list.contains(&cx.house),
        };
        let period_ok = match (&self.period, cx.period) {
            (None, _) => true,
            (Some(list), Some(p)) => list.contains(&p),
            (Some(_), None) => false,
        };
        let cup_ok = self.cup.is_none() || self.cup == cx.cup;
        let wears_ok = self
            .wears
            .as_ref()
            .is_none_or(|t| cx.worn_tags.iter().any(|w| w == t));
        let secret_ok = self
            .secret
            .as_ref()
            .is_none_or(|s| !cx.found_secrets.iter().any(|f| f == s));
        let gift_ok = self.gift.is_none() || self.gift == cx.gift;
        house_ok && period_ok && cup_ok && wears_ok && secret_ok && gift_ok
    }
}

/// `data/npcs/lines.json`: lines by key (an NPC id, or a shared pool such as
/// the students').
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LinesFile {
    pub version: u32,
    pub npcs: BTreeMap<String, Vec<Line>>,
}

/// A 64-bit mix (splitmix64's finaliser): the one the client and server
/// both use for seeds.
pub fn mix64(mut z: u64) -> u64 {
    z = z.wrapping_add(0x9E37_79B9_7F4A_7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// FNV-1a of a string (seeds from ids).
pub fn str_seed(s: &str) -> u64 {
    s.bytes().fold(0xcbf2_9ce4_8422_2325, |h, b| {
        (h ^ u64::from(b)).wrapping_mul(0x0100_0000_01b3)
    })
}

/// A seeded weighted choice among `(index, weight)`; `None` when empty.
fn weighted<T: Copy>(items: &[(T, u32)], seed: u64) -> Option<T> {
    let total: u64 = items.iter().map(|&(_, w)| u64::from(w)).sum();
    if total == 0 {
        return None;
    }
    let mut r = mix64(seed) % total;
    for &(item, w) in items {
        let w = u64::from(w);
        if r < w {
            return Some(item);
        }
        r -= w;
    }
    None
}

impl LinesFile {
    pub fn from_json(json: &str) -> Result<LinesFile, NpcsError> {
        let l: LinesFile = serde_json::from_str(json)?;
        check_version("lines.json", l.version)?;
        Ok(l)
    }

    /// The lines under `key` (an NPC's `lines_key()`).
    pub fn lines(&self, key: &str) -> &[Line] {
        self.npcs.get(key).map_or(&[], Vec::as_slice)
    }

    /// A seeded, weighted pick among `key`'s `when` lines that hold in `cx`
    /// and are not in `exclude` (indices already said in this
    /// conversation): the line's index under `key`, or `None`.
    pub fn pick(
        &self,
        key: &str,
        when: LineWhen,
        cx: &LineContext,
        seed: u64,
        exclude: &[u16],
    ) -> Option<u16> {
        let items: Vec<(u16, u32)> = self
            .lines(key)
            .iter()
            .enumerate()
            .filter(|(i, l)| l.when == when && !exclude.contains(&(*i as u16)) && l.holds(cx))
            .map(|(i, l)| (i as u16, u32::from(l.weight)))
            .collect();
        weighted(&items, seed)
    }

    /// Today's hint of a daily-hint portrait: a hint line that holds,
    /// picked by the UTC day and the character, so it is the same all day
    /// for that player.
    pub fn daily_hint(
        &self,
        key: &str,
        cx: &LineContext,
        day: i64,
        character_id: i64,
    ) -> Option<u16> {
        let seed = mix64(str_seed(key) ^ mix64(day as u64) ^ (character_id as u64).rotate_left(17));
        self.pick(key, LineWhen::Hint, cx, seed, &[])
    }

    /// The text of line `index` under `key`.
    pub fn text(&self, key: &str, index: u16) -> Option<&str> {
        self.lines(key)
            .get(usize::from(index))
            .map(|l| l.text.as_str())
    }

    /// The schema checks against the roster, the secrets' ids and the
    /// catalog's tags: every key is an NPC id or a students' pool; text
    /// lengths and placeholders; hint lines name a secret, only portraits
    /// hint; gift and given lines only for NPCs that give; shop lines only
    /// for shopkeepers; pass lines only for portraits; conditions name real
    /// houses, tags and secrets.
    pub fn validate_with(
        &self,
        roster: &Roster,
        secrets: &[&str],
        tags: &BTreeSet<String>,
    ) -> Result<(), NpcsError> {
        check_version("lines.json", self.version)?;
        for (key, lines) in &self.npcs {
            let speakers: Vec<&NpcDef> = roster
                .npcs
                .iter()
                .filter(|n| n.lines_key() == key)
                .collect();
            if speakers.is_empty() {
                return Err(invalid(format!("lines.json: nobody says {key}'s lines")));
            }
            let kinds: BTreeSet<NpcKind> = speakers.iter().map(|n| n.kind).collect();
            let kind = *kinds.iter().next().expect("one at least");
            if kinds.len() != 1 {
                return Err(invalid(format!(
                    "lines.json {key}: one kind of NPC per key"
                )));
            }
            for (i, l) in lines.iter().enumerate() {
                let at = || format!("lines.json {key}[{i}]");
                let max = if l.when == LineWhen::Pass {
                    PASS_LINE_CHARS_MAX
                } else {
                    LINE_CHARS_MAX
                };
                if !(1..=max).contains(&l.text.chars().count()) {
                    return Err(invalid(format!("{}: 1-{max} characters", at())));
                }
                check_placeholders(&l.text).map_err(|e| invalid(format!("{}: {e}", at())))?;
                if !(1..=10).contains(&l.weight) {
                    return Err(invalid(format!("{}: weight 1-10", at())));
                }
                let allowed = match l.when {
                    LineWhen::Pass | LineWhen::Hint => kind == NpcKind::Portrait,
                    LineWhen::Gift | LineWhen::Given => kind.gives(),
                    LineWhen::Shop => kind == NpcKind::Shopkeeper,
                    LineWhen::Talk => true,
                };
                if !allowed {
                    return Err(invalid(format!(
                        "{}: a {kind:?} never says {} lines",
                        at(),
                        l.when.id()
                    )));
                }
                if (l.when == LineWhen::Hint) != l.secret.is_some() {
                    return Err(invalid(format!(
                        "{}: exactly hint lines name a secret",
                        at()
                    )));
                }
                if let Some(s) = &l.secret
                    && !secrets.contains(&s.as_str())
                {
                    return Err(invalid(format!("{}: unknown secret {s}", at())));
                }
                if l.gift.is_some() && l.when != LineWhen::Gift {
                    return Err(invalid(format!("{}: only gift lines name a gift", at())));
                }
                if let Some(t) = &l.wears
                    && !tags.contains(t)
                {
                    return Err(invalid(format!("{}: no catalog item is tagged {t}", at())));
                }
                if let Some(HouseCond::Houses(list)) = &l.house
                    && (list.is_empty() || list.iter().any(|h| House::from_id(h).is_none()))
                {
                    return Err(invalid(format!("{}: unknown house", at())));
                }
                if matches!(l.house, Some(HouseCond::Rel(_)))
                    && speakers.iter().any(|n| n.house.is_none())
                {
                    return Err(invalid(format!(
                        "{}: same/other needs an NPC with a house",
                        at()
                    )));
                }
                if l.period.as_ref().is_some_and(Vec::is_empty) {
                    return Err(invalid(format!("{}: an empty period list", at())));
                }
                let needs_gift_token = |tok: &str, kind: GiftKind| {
                    l.text.contains(&format!("{{{tok}}}"))
                        && !(l.when == LineWhen::Gift && l.gift == Some(kind))
                };
                if needs_gift_token("place", GiftKind::Hint)
                    || needs_gift_token("item", GiftKind::Item)
                    || needs_gift_token("lore", GiftKind::Lore)
                    || needs_gift_token("points", GiftKind::HousePoint)
                {
                    return Err(invalid(format!(
                        "{}: {{place}} {{item}} {{lore}} {{points}} only in gift lines of that kind",
                        at()
                    )));
                }
            }
        }
        Ok(())
    }

    /// The content gate: every portrait 20-30 lines with at least 4 pass, 8
    /// talk and 1 hint lines, and talk lines for each period and each house;
    /// every ghost and staff member 10+ talk, 2+ gift and 1+ given lines;
    /// every shopkeeper 2+ shop and 3+ talk lines; the students' pools 12+
    /// talk lines.
    pub fn check_counts(&self, roster: &Roster) -> Result<(), String> {
        let count = |key: &str, w: LineWhen| self.lines(key).iter().filter(|l| l.when == w).count();
        let mut problems = Vec::new();
        for n in &roster.npcs {
            let key = n.lines_key();
            let total = self.lines(key).len();
            match n.kind {
                NpcKind::Portrait => {
                    if !(PORTRAIT_LINES_MIN..=PORTRAIT_LINES_MAX).contains(&total) {
                        problems.push(format!("{key}: {total} lines (20-30)"));
                    }
                    if count(key, LineWhen::Pass) < 4
                        || count(key, LineWhen::Talk) < 8
                        || count(key, LineWhen::Hint) < 1
                    {
                        problems.push(format!("{key}: 4 pass, 8 talk and 1 hint lines at least"));
                    }
                    let talk = || self.lines(key).iter().filter(|l| l.when == LineWhen::Talk);
                    for p in Period::ALL {
                        if !talk().any(|l| l.period.as_ref().is_some_and(|ps| ps.contains(&p))) {
                            problems.push(format!("{key}: no talk line for the {}", p.id()));
                        }
                    }
                    for h in House::ALL {
                        let greets = talk().any(|l| matches!(&l.house, Some(HouseCond::Houses(list)) if list.iter().any(|x| x == h.id())));
                        if !greets {
                            problems.push(format!("{key}: no talk line for {}", h.id()));
                        }
                    }
                }
                NpcKind::Ghost | NpcKind::Staff => {
                    if count(key, LineWhen::Talk) < 10
                        || count(key, LineWhen::Gift) < 2
                        || count(key, LineWhen::Given) < 1
                    {
                        problems.push(format!("{key}: 10 talk, 2 gift and 1 given lines at least"));
                    }
                }
                NpcKind::Shopkeeper => {
                    if count(key, LineWhen::Shop) < 2 || count(key, LineWhen::Talk) < 3 {
                        problems.push(format!("{key}: 2 shop and 3 talk lines at least"));
                    }
                }
                NpcKind::Student => {
                    if count(key, LineWhen::Talk) < 12 {
                        problems.push(format!("{key}: 12 talk lines at least"));
                    }
                }
                // Its words are its feature's dialogue.
                NpcKind::Clerk => {}
            }
        }
        problems.sort();
        problems.dedup();
        if problems.is_empty() {
            Ok(())
        } else {
            Err(problems.join("; "))
        }
    }
}

fn check_placeholders(text: &str) -> Result<(), String> {
    let mut rest = text;
    while let Some(open) = rest.find('{') {
        let after = &rest[open + 1..];
        let close = after.find('}').ok_or("an unclosed {")?;
        let name = &after[..close];
        if !PLACEHOLDERS.contains(&name) {
            return Err(format!("unknown placeholder {{{name}}}"));
        }
        rest = &after[close + 1..];
    }
    if rest.contains('}') {
        return Err("a stray }".into());
    }
    Ok(())
}

// ------------------------------------------------------------------- gifts

/// One kind of gift (`gifts.json` entries, by `kind`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum GiftDef {
    /// The character's next locked lore entry (`Lore::next`); unavailable
    /// once every entry is unlocked.
    Lore,
    /// `quantity` [min, max] of catalog material `item`.
    Item { item: u16, quantity: [u16; 2] },
    /// `house_cup.json` `sources.npc.points` for the character's house;
    /// unavailable once the `npc` source's daily cap is used up.
    HousePoint,
    /// A place with a find spot the character has not searched today (the
    /// class hint's rule); unavailable when there is none.
    Hint,
}

impl GiftDef {
    pub fn kind(&self) -> GiftKind {
        match self {
            GiftDef::Lore => GiftKind::Lore,
            GiftDef::Item { .. } => GiftKind::Item,
            GiftDef::HousePoint => GiftKind::HousePoint,
            GiftDef::Hint => GiftKind::Hint,
        }
    }
}

/// A weighted entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GiftEntry {
    pub weight: u16,
    #[serde(flatten)]
    pub gift: GiftDef,
}

/// One NPC's gifts (`gifts.json` `npcs.<npc id>`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GiftTable {
    /// Gives only to characters of its own house (ghosts: required).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub own_house_only: bool,
    pub entries: Vec<GiftEntry>,
}

impl GiftTable {
    /// Whether it gives to a character of `house` at all.
    pub fn gives_to(&self, npc_house: Option<House>, house: House) -> bool {
        !self.own_house_only || npc_house == Some(house)
    }

    /// A seeded, weighted roll among the entries whose kind is `available`
    /// (`None` when none is). The same seed and availability give the same
    /// gift (a retried claim).
    pub fn roll(&self, seed: u64, available: impl Fn(GiftKind) -> bool) -> Option<&GiftDef> {
        let items: Vec<(usize, u32)> = self
            .entries
            .iter()
            .enumerate()
            .filter(|(_, e)| available(e.gift.kind()))
            .map(|(i, e)| (i, u32::from(e.weight)))
            .collect();
        weighted(&items, seed).map(|i| &self.entries[i].gift)
    }
}

/// An item gift's quantity for `seed`, within `[min, max]`.
pub fn roll_quantity(range: [u16; 2], seed: u64) -> u16 {
    let span = u64::from(range[1].saturating_sub(range[0])) + 1;
    range[0] + (mix64(seed.rotate_left(29)) % span) as u16
}

/// `data/npcs/gifts.json`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GiftsFile {
    pub version: u32,
    pub npcs: BTreeMap<String, GiftTable>,
}

impl GiftsFile {
    pub fn from_json(json: &str) -> Result<GiftsFile, NpcsError> {
        let g: GiftsFile = serde_json::from_str(json)?;
        check_version("gifts.json", g.version)?;
        Ok(g)
    }

    pub fn table(&self, npc: &str) -> Option<&GiftTable> {
        self.npcs.get(npc)
    }

    /// Exactly the ghosts and staff have a table; ghosts give to their own
    /// house only; weights positive; item gifts are stackable materials, at
    /// most `GIFT_QUANTITY_MAX`.
    pub fn validate_with(&self, roster: &Roster, catalog: &Catalog) -> Result<(), NpcsError> {
        for n in &roster.npcs {
            if n.kind.gives() != self.npcs.contains_key(&n.id) {
                return Err(invalid(format!(
                    "gifts.json: exactly ghosts and staff give ({})",
                    n.id
                )));
            }
        }
        for (id, t) in &self.npcs {
            let n = roster
                .get(id)
                .ok_or_else(|| invalid(format!("gifts.json: {id} is not in the roster")))?;
            if (n.kind == NpcKind::Ghost) != t.own_house_only {
                return Err(invalid(format!(
                    "gifts.json {id}: exactly ghosts give to their own house only"
                )));
            }
            if t.entries.is_empty() || t.entries.iter().any(|e| e.weight == 0) {
                return Err(invalid(format!(
                    "gifts.json {id}: entries with positive weights"
                )));
            }
            for e in &t.entries {
                if let GiftDef::Item { item, quantity } = &e.gift {
                    let def = catalog
                        .get(*item)
                        .ok_or_else(|| invalid(format!("gifts.json {id}: no item {item}")))?;
                    if def.kind != ItemKind::Material || def.retired {
                        return Err(invalid(format!(
                            "gifts.json {id}: item {item} is not a material"
                        )));
                    }
                    if quantity[0] == 0
                        || quantity[0] > quantity[1]
                        || quantity[1] > GIFT_QUANTITY_MAX
                    {
                        return Err(invalid(format!(
                            "gifts.json {id}: quantity 1..={GIFT_QUANTITY_MAX}, min <= max"
                        )));
                    }
                }
            }
        }
        Ok(())
    }
}

/// The UTC day of `unix_ms` (days since 1970-01-01): the gift's day
/// (`npc_gifts.day`).
pub fn gift_day(unix_ms: i64) -> i64 {
    unix_ms.div_euclid(DAY_MS)
}

/// Seconds from `unix_ms` to the next UTC midnight (when gifts are ready
/// again).
pub fn gift_ready_in_secs(unix_ms: i64) -> u32 {
    let next = (gift_day(unix_ms) + 1) * DAY_MS;
    ((next - unix_ms + 999) / 1_000) as u32
}

/// The roll's seed for a character, an NPC and a day.
pub fn gift_seed(character_id: i64, npc: &str, day: i64) -> u64 {
    mix64(mix64(character_id as u64) ^ str_seed(npc) ^ (day as u64).rotate_left(32))
}

// ------------------------------------------------------------- everything

/// Every `data/npcs` file, parsed and cross-checked (the server's startup,
/// the shipped-file tests).
#[derive(Debug, Clone)]
pub struct NpcData {
    pub roster: Roster,
    pub looks: Looks,
    pub portraits: PortraitsFile,
    pub routines: RoutinesFile,
    pub lines: LinesFile,
    pub gifts: GiftsFile,
}

/// The content each check needs.
pub struct NpcChecks<'a> {
    pub creator: &'a Creator,
    pub catalog: &'a Catalog,
    pub palette: &'a Palette,
    /// `secrets.json` ids.
    pub secrets: &'a [&'a str],
}

impl NpcData {
    /// Parses the six files and checks everything that needs no layout.
    pub fn from_json(
        roster: &str,
        looks: &str,
        portraits: &str,
        routines: &str,
        lines: &str,
        gifts: &str,
        checks: &NpcChecks<'_>,
    ) -> Result<NpcData, NpcsError> {
        let d = NpcData {
            roster: Roster::from_json(roster)?,
            looks: Looks::from_json(looks)?,
            portraits: PortraitsFile::from_json(portraits)?,
            routines: RoutinesFile::from_json(routines)?,
            lines: LinesFile::from_json(lines)?,
            gifts: GiftsFile::from_json(gifts)?,
        };
        d.looks
            .validate_with(&d.roster, checks.creator, checks.catalog, checks.palette)?;
        let tags: BTreeSet<String> = checks
            .catalog
            .items
            .iter()
            .flat_map(|i| i.tags.iter().cloned())
            .collect();
        d.lines.validate_with(&d.roster, checks.secrets, &tags)?;
        d.gifts.validate_with(&d.roster, checks.catalog)?;
        Ok(d)
    }

    /// The checks against the castle: stations, portrait walls, and every
    /// routine planned (the plans, by NPC id, for the server to keep).
    pub fn validate_castle(
        &self,
        layout: &Layout,
        navs: &NpcNavs,
        schedule: &Schedule,
    ) -> Result<HashMap<String, RoutinePlan>, NpcsError> {
        self.roster.validate_with(layout)?;
        self.portraits.resolve(&self.roster, layout)?;
        Ok(self
            .routines
            .plan_all(&self.roster, navs, schedule)?
            .into_iter()
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::{flat, layout};

    const ROSTER: &str = include_str!("../../../data/npcs/roster.json");
    const LOOKS: &str = include_str!("../../../data/npcs/looks.json");
    const PORTRAITS: &str = include_str!("../../../data/npcs/portraits.json");
    const ROUTINES: &str = include_str!("../../../data/npcs/routines.json");
    const LINES: &str = include_str!("../../../data/npcs/lines.json");
    const GIFTS: &str = include_str!("../../../data/npcs/gifts.json");
    const LAYOUT: &str = include_str!("../../../data/world/layout.json");
    const KEEP_OUT: &str = include_str!("../../../data/world/keep_out.json");
    const SCHEDULE: &str = include_str!("../../../data/classes/schedule.json");
    const SECRETS: &str = include_str!("../../../data/world/secrets.json");
    const CATALOG: &str = include_str!("../../../data/items/catalog.json");
    const PALETTE: &str = include_str!("../../../data/items/palette.json");
    const CREATOR: &str = include_str!("../../../data/characters/creator.json");

    fn shipped() -> NpcData {
        let palette = Palette::from_json(PALETTE).unwrap();
        let catalog = Catalog::from_json(CATALOG, &palette).unwrap();
        let creator = Creator::from_json(CREATOR).unwrap();
        let secrets: crate::SecretsFile = serde_json::from_str(SECRETS).unwrap();
        let ids: Vec<&str> = secrets.secrets.iter().map(|s| s.id.as_str()).collect();
        let checks = NpcChecks {
            creator: &creator,
            catalog: &catalog,
            palette: &palette,
            secrets: &ids,
        };
        NpcData::from_json(ROSTER, LOOKS, PORTRAITS, ROUTINES, LINES, GIFTS, &checks)
            .unwrap_or_else(|e| panic!("{e}"))
    }

    fn castle() -> (Layout, NpcNavs, Schedule) {
        let layout = Layout::from_json(LAYOUT).unwrap();
        let keep: Vec<KeepOut> = serde_json::from_value(
            serde_json::from_str::<serde_json::Value>(KEEP_OUT).unwrap()["keep_out"].clone(),
        )
        .unwrap();
        let navs = NpcNavs::new(&layout, &keep).unwrap();
        (layout, navs, Schedule::from_json(SCHEDULE).unwrap())
    }

    #[test]
    fn the_shipped_npcs_are_valid_and_every_routine_walks() {
        let d = shipped();
        let (layout, navs, schedule) = castle();
        let plans = d
            .validate_castle(&layout, &navs, &schedule)
            .unwrap_or_else(|e| panic!("{e}"));
        let entities = d.roster.npcs.iter().filter(|n| n.kind.is_entity()).count();
        assert_eq!(plans.len(), entities);
        // Every pose all day long stands on the NPC's ground (sampled every
        // game minute), and walks never jump.
        for n in d.roster.npcs.iter().filter(|n| n.kind.is_entity()) {
            let plan = &plans[&n.id];
            let nav = navs.for_npc(n);
            let mut last: Option<Pose> = None;
            for minute in 0..(24 * 60) {
                let p = plan.pose_at_secs(f64::from(minute * 60));
                if p.activity != Activity::Hidden {
                    assert!(
                        nav.surface_at(p.pos).is_some(),
                        "{} off the ground at {minute}: {:?}",
                        n.id,
                        p.pos
                    );
                }
                if let Some(q) = last
                    && p.activity == Activity::Walk
                    && q.activity == Activity::Walk
                {
                    let d = ((p.pos[0] - q.pos[0]).powi(2) + (p.pos[2] - q.pos[2]).powi(2)).sqrt();
                    // One game minute is 5 real seconds at most 2 m/s.
                    assert!(d <= 10.5, "{} jumps {d} m at {minute}", n.id);
                }
                last = Some(p);
            }
        }
        // No two visible residents keep to one spot (students who share a
        // door or a night point take slots and set off apart): sampled every
        // 10 game seconds, no pair is within 0.25 m for three samples running
        // (a walker passing through someone standing is a moment).
        let walkers: Vec<_> = d
            .roster
            .npcs
            .iter()
            .filter(|n| n.kind.is_entity() && !n.kind.stands())
            .collect();
        let mut together: HashMap<(usize, usize), u32> = HashMap::new();
        for tick in 0..(24 * 360) {
            let at = f64::from(tick * 10);
            let poses: Vec<_> = walkers
                .iter()
                .map(|n| plans[&n.id].pose_at_secs(at))
                .collect();
            for i in 0..poses.len() {
                for j in i + 1..poses.len() {
                    let (p, q) = (&poses[i], &poses[j]);
                    let d = ((p.pos[0] - q.pos[0]).powi(2)
                        + (p.pos[1] - q.pos[1]).powi(2)
                        + (p.pos[2] - q.pos[2]).powi(2))
                    .sqrt();
                    let close = p.activity != Activity::Hidden
                        && q.activity != Activity::Hidden
                        && d <= 0.25;
                    let run = together.entry((i, j)).or_insert(0);
                    *run = if close { *run + 1 } else { 0 };
                    assert!(
                        *run < 3,
                        "{} and {} keep within {d} m of each other at game second {at}",
                        walkers[i].id,
                        walkers[j].id
                    );
                }
            }
        }
        // Students: at least one is out and about in each daytime hour.
        for hour in 8..21 {
            let out = d
                .roster
                .of_kind(NpcKind::Student)
                .filter(|n| {
                    plans[&n.id].pose_at_secs(f64::from(hour * 3_600)).activity != Activity::Hidden
                })
                .count();
            assert!(out >= 1, "nobody about at {hour}:00");
        }
    }

    #[test]
    fn the_shipped_roster_has_every_kind() {
        let d = shipped();
        let r = &d.roster;
        assert!((PORTRAITS_MIN..=PORTRAITS_MAX).contains(&r.of_kind(NpcKind::Portrait).count()));
        assert_eq!(r.of_kind(NpcKind::Ghost).count(), 4);
        assert_eq!(r.of_kind(NpcKind::Staff).count(), 3);
        assert!(r.of_kind(NpcKind::Shopkeeper).count() >= 1);
        assert!(r.of_kind(NpcKind::Student).count() <= usize::from(r.rules.students_max));
        // The binding anti-annoyance numbers.
        assert!(
            r.rules.pass_cooldown_s >= 180,
            "a few minutes per portrait per player"
        );
        assert!(r.rules.pass_gap_s >= 10);
        let (layout, _, _) = castle();
        let placed = d.portraits.resolve(r, &layout).unwrap();
        assert_eq!(placed.len(), r.of_kind(NpcKind::Portrait).count());
        for p in &placed {
            assert!(p.pos.iter().all(|v| v.is_finite()));
        }
    }

    #[test]
    fn a_routine_walks_waits_sits_and_hides_on_the_clock() {
        let l = layout(vec![flat("hall", [0.0, 0.0], [40.0, 10.0], 0.0)]);
        let world = Arc::new(World::new(l).unwrap());
        let nav = Nav::new(world, &[], |_| true);
        let clock = ClockRules {
            epoch_unix_ms: 0,
            day_real_secs: 7_200,
            periods: Schedule::from_json(SCHEDULE).unwrap().clock.periods,
        };
        let points: HashMap<&str, Point> = [
            (
                "a",
                Point {
                    pos: [5.0, 0.0, 5.0],
                    yaw: Some(1.0),
                },
            ),
            (
                "b",
                Point {
                    pos: [35.0, 0.0, 5.0],
                    yaw: None,
                },
            ),
        ]
        .into_iter()
        .collect();
        let step = |h: u32, to: &str, anim: StepAnim, hidden: bool| Step {
            at: TimeOfDay(h * 3_600),
            to: to.into(),
            anim,
            hidden,
        };
        let steps = vec![
            step(6, "a", StepAnim::Idle, false),
            step(12, "b", StepAnim::Sit, false),
            step(20, "b", StepAnim::Idle, true),
        ];
        let plan = RoutinePlan::new(&steps, |p| points.get(p).copied(), &nav, 1.0, &clock).unwrap();
        // 30 m at 1 m/s is 30 real seconds: 360 game seconds.
        let at = |h: f64| plan.pose_at_secs(h * 3_600.0);
        let walking = at(12.0 + 180.0 / 3_600.0);
        assert_eq!(walking.activity, Activity::Walk);
        assert!((walking.pos[0] - 20.0).abs() < 0.2, "{:?}", walking.pos);
        assert!((walking.yaw - std::f32::consts::FRAC_PI_2).abs() < 1e-3);
        let sat = at(13.0);
        assert_eq!(sat.activity, Activity::Sit);
        assert_eq!(sat.pos, [35.0, 0.0, 5.0]);
        assert_eq!(at(21.0).activity, Activity::Hidden);
        // Before 06:00 the day's last step (hidden) still holds.
        assert_eq!(at(3.0).activity, Activity::Hidden);
        // 06:00: out of hiding, walking from b back to a, then facing yaw 1.
        assert_eq!(at(6.01).activity, Activity::Walk);
        let rest = at(7.0);
        assert_eq!(
            (rest.activity, rest.pos, rest.yaw),
            (Activity::Idle, [5.0, 0.0, 5.0], 1.0)
        );
        // The real clock: game day 0 at the epoch, 7,200 s a day.
        assert_eq!(plan.pose(7 * 300 * 1_000).pos, rest.pos);
        assert_eq!(plan.pose(7_200_000 + 7 * 300 * 1_000).pos, rest.pos);
        // A walk longer than its step is refused; so is an unknown point.
        let slow = RoutinePlan::new(&steps, |p| points.get(p).copied(), &nav, 0.001, &clock);
        assert!(slow.unwrap_err().contains("game minutes"));
        let lost = vec![step(6, "nowhere", StepAnim::Idle, false)];
        assert!(RoutinePlan::new(&lost, |p| points.get(p).copied(), &nav, 1.0, &clock).is_err());
        let unsorted = vec![steps[1].clone(), steps[0].clone()];
        assert!(
            RoutinePlan::new(&unsorted, |p| points.get(p).copied(), &nav, 1.0, &clock).is_err()
        );
    }

    #[test]
    fn students_follow_the_schedule() {
        let schedule = Schedule::from_json(SCHEDULE).unwrap();
        let students = Students {
            lead_mins: 25,
            bed: default_bed(),
            rise: default_rise(),
            bed_walk_mins: 20,
            spec: [(
                "s".to_string(),
                StudentSpec {
                    classes: vec!["charms".into(), "history".into(), "astronomy".into()],
                    idle: "hall".into(),
                    idle_anim: StepAnim::Sit,
                    night: "bed".into(),
                },
            )]
            .into_iter()
            .collect(),
        };
        let steps = students.steps("s", &schedule, &schedule.clock).unwrap();
        assert!(steps.windows(2).all(|w| w[0].at < w[1].at));
        let door = |c: &str, hidden: bool| {
            steps
                .iter()
                .filter(|s| s.to == format!("door:{c}") && s.hidden == hidden)
                .map(|s| s.at.to_string())
                .collect::<Vec<_>>()
        };
        // Charms 08:00 (and 17:00), History 10:30 and 14:30; 108 game minutes each.
        assert_eq!(door("charms", true), ["08:00", "17:00"]);
        assert_eq!(door("history", true), ["10:30", "14:30"]);
        assert!(door("charms", false).contains(&"07:35".to_string()));
        assert!(door("charms", false).contains(&"09:48".to_string()));
        // Astronomy meets at night only: skipped.
        assert!(door("astronomy", true).is_empty());
        let hidden_at_night = steps
            .iter()
            .any(|s| s.hidden && s.to == "bed" && s.at.to_string() == "22:20");
        assert!(hidden_at_night);
        assert_eq!(steps[0].at.to_string(), "06:30");
        assert!(
            students
                .steps("nobody", &schedule, &schedule.clock)
                .is_err()
        );
    }

    fn cx(house: &str) -> LineContext {
        LineContext {
            house: house.into(),
            npc_house: Some("skytalon".into()),
            period: Some(Period::Morning),
            cup: Some(CupStanding::OtherLeads),
            worn_tags: vec!["knitwear".into()],
            found_secrets: vec!["weavers_stair".into()],
            gift: None,
        }
    }

    #[test]
    fn lines_are_picked_by_their_conditions() {
        let line = |text: &str, when: LineWhen| Line {
            text: text.into(),
            when,
            house: None,
            period: None,
            cup: None,
            wears: None,
            secret: None,
            gift: None,
            weight: 1,
        };
        let lines = LinesFile {
            version: NPCS_VERSION,
            npcs: [(
                "p".to_string(),
                vec![
                    line("plain", LineWhen::Talk),
                    Line {
                        house: Some(HouseCond::Rel(HouseRel::Same)),
                        ..line("same", LineWhen::Talk)
                    },
                    Line {
                        period: Some(vec![Period::Night]),
                        ..line("night", LineWhen::Talk)
                    },
                    Line {
                        wears: Some("knitwear".into()),
                        ..line("wool", LineWhen::Talk)
                    },
                    Line {
                        cup: Some(CupStanding::YoursLeads),
                        ..line("cup", LineWhen::Talk)
                    },
                    Line {
                        secret: Some("weavers_stair".into()),
                        ..line("found", LineWhen::Hint)
                    },
                    Line {
                        secret: Some("readers_shortcut".into()),
                        ..line("unfound", LineWhen::Hint)
                    },
                    Line {
                        house: Some(HouseCond::Houses(vec!["burrowsby".into()])),
                        ..line("badger", LineWhen::Talk)
                    },
                ],
            )]
            .into_iter()
            .collect(),
        };
        let all = |cx: &LineContext, when| {
            let mut seen = BTreeSet::new();
            for seed in 0..400 {
                if let Some(i) = lines.pick("p", when, cx, seed, &[]) {
                    seen.insert(lines.text("p", i).unwrap().to_string());
                }
            }
            seen.into_iter().collect::<Vec<_>>()
        };
        assert_eq!(all(&cx("roarwing"), LineWhen::Talk), ["plain", "wool"]);
        assert_eq!(
            all(&cx("skytalon"), LineWhen::Talk),
            ["plain", "same", "wool"]
        );
        assert_eq!(
            all(&cx("burrowsby"), LineWhen::Talk),
            ["badger", "plain", "wool"]
        );
        assert_eq!(all(&cx("roarwing"), LineWhen::Hint), ["unfound"]);
        assert!(all(&cx("roarwing"), LineWhen::Pass).is_empty());
        // Excluding what was said; the same seed picks the same line.
        let first = lines
            .pick("p", LineWhen::Talk, &cx("roarwing"), 7, &[])
            .unwrap();
        assert_eq!(
            lines.pick("p", LineWhen::Talk, &cx("roarwing"), 7, &[]),
            Some(first)
        );
        let other = lines
            .pick("p", LineWhen::Talk, &cx("roarwing"), 7, &[first])
            .unwrap();
        assert_ne!(other, first);
        assert_eq!(
            lines.pick("p", LineWhen::Talk, &cx("roarwing"), 7, &[0, 3]),
            None
        );
        // Today's hint is the same all day for one player.
        let day = gift_day(1_800_000_000_000);
        assert_eq!(
            lines.daily_hint("p", &cx("roarwing"), day, 42),
            lines.daily_hint("p", &cx("roarwing"), day, 42)
        );
        let mut found = cx("roarwing");
        found.found_secrets.push("readers_shortcut".into());
        assert_eq!(lines.daily_hint("p", &found, day, 42), None);
    }

    #[test]
    fn line_files_are_checked() {
        let d = shipped();
        let tags: BTreeSet<String> = ["knitwear".to_string()].into_iter().collect();
        let secrets = ["weavers_stair"];
        let portrait = d
            .roster
            .of_kind(NpcKind::Portrait)
            .next()
            .unwrap()
            .id
            .clone();
        let staff = d.roster.of_kind(NpcKind::Staff).next().unwrap().id.clone();
        let check = |key: &str, line: serde_json::Value| {
            let file = serde_json::json!({ "version": 1, "npcs": { key: [line] } });
            let l: LinesFile = serde_json::from_value(file).unwrap();
            l.validate_with(&d.roster, &secrets, &tags)
        };
        let ok = |key: &str, v: serde_json::Value| assert!(check(key, v.clone()).is_ok(), "{v}");
        let bad = |key: &str, v: serde_json::Value| assert!(check(key, v.clone()).is_err(), "{v}");
        ok(
            &portrait,
            serde_json::json!({ "text": "Good morning, {name}.", "when": "pass" }),
        );
        ok(
            &portrait,
            serde_json::json!({ "text": "Threads...", "when": "hint", "secret": "weavers_stair" }),
        );
        ok(
            &staff,
            serde_json::json!({ "text": "Take this: {item}.", "when": "gift", "gift": "item" }),
        );
        bad(
            &portrait,
            serde_json::json!({ "text": "Threads...", "when": "hint" }),
        );
        bad(
            &portrait,
            serde_json::json!({ "text": "x", "when": "hint", "secret": "nowhere" }),
        );
        bad(
            &portrait,
            serde_json::json!({ "text": "Hello {nobody}", "when": "talk" }),
        );
        bad(
            &portrait,
            serde_json::json!({ "text": "x", "when": "gift" }),
        );
        bad(&staff, serde_json::json!({ "text": "x", "when": "pass" }));
        bad(
            &staff,
            serde_json::json!({ "text": "Here: {item}", "when": "gift", "gift": "lore" }),
        );
        bad(
            &portrait,
            serde_json::json!({ "text": "x", "when": "talk", "wears": "sequins" }),
        );
        bad(
            &portrait,
            serde_json::json!({ "text": "x".repeat(91), "when": "pass" }),
        );
        bad(
            "nobody_at_all",
            serde_json::json!({ "text": "x", "when": "talk" }),
        );
        bad(
            &portrait,
            serde_json::json!({ "text": "x", "when": "talk", "house": "same" }),
        );
    }

    #[test]
    fn gifts_roll_once_per_day_and_respect_availability() {
        let table = GiftTable {
            own_house_only: true,
            entries: vec![
                GiftEntry {
                    weight: 3,
                    gift: GiftDef::Lore,
                },
                GiftEntry {
                    weight: 1,
                    gift: GiftDef::HousePoint,
                },
                GiftEntry {
                    weight: 2,
                    gift: GiftDef::Item {
                        item: 3025,
                        quantity: [1, 3],
                    },
                },
            ],
        };
        assert!(table.gives_to(Some(House::Skytalon), House::Skytalon));
        assert!(!table.gives_to(Some(House::Skytalon), House::Serpentyne));
        let day = gift_day(1_800_000_000_000);
        let seed = gift_seed(42, "ghost_house_c", day);
        assert_eq!(seed, gift_seed(42, "ghost_house_c", day));
        assert_ne!(seed, gift_seed(42, "ghost_house_c", day + 1));
        assert_ne!(seed, gift_seed(43, "ghost_house_c", day));
        let all = |avail: &dyn Fn(GiftKind) -> bool| {
            (0..300u64)
                .filter_map(|s| table.roll(s, avail).map(GiftDef::kind))
                .collect::<BTreeSet<_>>()
        };
        assert_eq!(all(&|_| true).len(), 3);
        assert_eq!(
            all(&|k| k != GiftKind::Lore),
            [GiftKind::Item, GiftKind::HousePoint].into_iter().collect()
        );
        assert!(all(&|_| false).is_empty());
        for s in 0..200 {
            assert!((1..=3).contains(&roll_quantity([1, 3], s)));
        }
        assert_eq!(roll_quantity([2, 2], 9), 2);
        // Days are UTC days; gifts are ready again at midnight.
        assert_eq!(gift_day(DAY_MS - 1), 0);
        assert_eq!(gift_day(DAY_MS), 1);
        assert_eq!(gift_ready_in_secs(DAY_MS - 1_000), 1);
        assert_eq!(gift_ready_in_secs(0), 86_400);
    }

    #[test]
    fn rosters_are_checked() {
        let base: serde_json::Value = serde_json::from_str(ROSTER).unwrap();
        let fails = |f: &dyn Fn(&mut serde_json::Value)| {
            let mut v = base.clone();
            f(&mut v);
            Roster::from_json(&v.to_string()).is_err()
        };
        assert!(!fails(&|_| {}));
        assert!(fails(&|v| v["version"] = 2.into()));
        assert!(fails(&|v| v["rules"]["pass_cooldown_s"] = 30.into()));
        assert!(fails(&|v| v["rules"]["students_in_view_max"] = 99.into()));
        // A second ghost for one house, a nameless NPC, a duplicate id.
        assert!(fails(&|v| {
            let ghost = v["npcs"]
                .as_array()
                .unwrap()
                .iter()
                .find(|n| n["kind"] == "ghost")
                .unwrap()
                .clone();
            let mut twin = ghost;
            twin["id"] = "ghost_twin".into();
            v["npcs"].as_array_mut().unwrap().push(twin);
        }));
        assert!(fails(&|v| v["npcs"][0]["name"] = "".into()));
        assert!(fails(&|v| {
            let first = v["npcs"][0].clone();
            v["npcs"].as_array_mut().unwrap().push(first);
        }));
        // A shopkeeper without a station.
        assert!(fails(&|v| {
            for n in v["npcs"].as_array_mut().unwrap() {
                if n["kind"] == "shopkeeper" {
                    n.as_object_mut().unwrap().remove("station");
                }
            }
        }));
    }

    #[test]
    fn placeholders_are_checked() {
        assert!(check_placeholders("Hello, {name} of {house}.").is_ok());
        assert!(check_placeholders("no braces").is_ok());
        assert!(check_placeholders("{nope}").is_err());
        assert!(check_placeholders("{name").is_err());
        assert!(check_placeholders("name}").is_err());
    }

    /// The content gate: every NPC has its full set of lines
    /// (`LinesFile::check_counts`).
    #[test]
    fn content_counts() {
        let d = shipped();
        d.lines
            .check_counts(&d.roster)
            .unwrap_or_else(|e| panic!("{e}"));
    }

    #[test]
    fn the_content_gate_counts() {
        let d = shipped();
        // No lines at all: short of the gate, and it says so per NPC.
        let empty = LinesFile {
            version: NPCS_VERSION,
            npcs: BTreeMap::new(),
        };
        let err = empty.check_counts(&d.roster).unwrap_err();
        let portrait = &d.roster.of_kind(NpcKind::Portrait).next().unwrap().id;
        assert!(
            err.contains(&format!("{portrait}: 0 lines (20-30)")),
            "{err}"
        );
        assert!(err.contains("students: 12 talk lines at least"), "{err}");
    }

    /// The Clubs and Societies desk in the Entrance Hall (the castle kit's
    /// hero ids `clubs_*`) stands clear of every NPC route through the hall.
    /// Planned over the castle without the desk, no leg of any roster NPC's
    /// routine comes within a walker's radius and a margin of its blockers
    /// (so the desk never stands in a walk), and planned with it every leg
    /// stays as clear (so no walk bends round it). The secretary's point is
    /// on her ground, and a player can stand on the hall side of the desk
    /// within talk_radius_m of her.
    #[test]
    fn the_clubs_desk_keeps_clear_of_every_npc_route() {
        const PREFIX: &str = "entrance_hall/clubs_";
        const SECRETARY: &str = "clerk_clubs";
        // A walker's radius (MoveParams::radius) and half a metre to spare.
        const CLEAR_M: f32 = 0.3 + 0.5;
        let d = shipped();
        let (layout, navs, schedule) = castle();
        let desk: Vec<crate::layout::Blocker> = layout
            .solids
            .iter()
            .filter(|b| b.id.starts_with(PREFIX))
            .cloned()
            .collect();
        assert!(
            desk.len() >= 2,
            "the desk and its chair are solid: {desk:?}"
        );
        // Distance in plan from `p` to blocker `b` (its box, in its own axes).
        let dist = |b: &crate::layout::Blocker, p: [f32; 2]| {
            let (s, c) = b.yaw.sin_cos();
            let (dx, dz) = (p[0] - b.at[0], p[1] - b.at[1]);
            let (lx, lz) = (dx * c - dz * s, dx * s + dz * c);
            let ox = (lx.abs() - b.half[0]).max(0.0);
            let oz = (lz.abs() - b.half[1]).max(0.0);
            ox.hypot(oz)
        };
        let check = |plans: &HashMap<String, RoutinePlan>, when: &str| {
            for (id, plan) in plans {
                if id == SECRETARY {
                    continue;
                }
                let w: Vec<[f32; 3]> = plan.waypoints().collect();
                for leg in w.windows(2) {
                    let (a, b) = (leg[0], leg[1]);
                    // The hall's floor only (y 0): flights above and the
                    // kitchen corridor below share its plan.
                    if a[1].abs() > 1.0 || b[1].abs() > 1.0 {
                        continue;
                    }
                    let len = (b[0] - a[0]).hypot(b[2] - a[2]);
                    let n = (len / 0.05).ceil().max(1.0) as u32;
                    for k in 0..=n {
                        let t = k as f32 / n as f32;
                        let p = [a[0] + (b[0] - a[0]) * t, a[2] + (b[2] - a[2]) * t];
                        for blocker in &desk {
                            let m = dist(blocker, p);
                            assert!(
                                m >= CLEAR_M,
                                "{when}: {id} walks {a:?} -> {b:?} within {m:.2} m of {}",
                                blocker.id
                            );
                        }
                    }
                }
            }
        };
        // Without the desk: where the routes run anyway.
        let mut bare = layout.clone();
        bare.solids.retain(|b| !b.id.starts_with(PREFIX));
        let keep: Vec<KeepOut> = serde_json::from_value::<Vec<KeepOut>>(
            serde_json::from_str::<serde_json::Value>(KEEP_OUT).unwrap()["keep_out"].clone(),
        )
        .unwrap()
        .into_iter()
        .filter(|k| !k.id.starts_with("clubs_"))
        .collect();
        let bare_navs = NpcNavs::new(&bare, &keep).unwrap();
        let without = d
            .validate_castle(&bare, &bare_navs, &schedule)
            .unwrap_or_else(|e| panic!("{e}"));
        check(&without, "without the desk");
        let with = d
            .validate_castle(&layout, &navs, &schedule)
            .unwrap_or_else(|e| panic!("{e}"));
        check(&with, "with the desk");
        // Her point: on her ground, behind the desk (east of it).
        let me = d.roster.get(SECRETARY).expect("the clubs secretary");
        assert_eq!(me.kind, NpcKind::Clerk);
        let at = d
            .routines
            .point("eh_clubs_desk", &schedule, &layout)
            .expect("her point");
        assert!(navs.for_npc(me).surface_at(at.pos).is_some(), "{at:?}");
        let front = desk
            .iter()
            .map(|b| b.at[0] - b.half[0].max(b.half[1]))
            .fold(f32::INFINITY, f32::min);
        assert!(at.pos[0] > front, "she stands behind the desk");
        assert!(
            desk.iter().all(|b| dist(b, [at.pos[0], at.pos[2]]) >= 0.3),
            "her spot is clear of the desk and the chair"
        );
        // A player stands on the hall side within talk reach.
        let world = World::new(layout.clone()).unwrap();
        let talk = d.roster.rules.talk_radius_m;
        let stands = |x: f32, z: f32| {
            (0..8).all(|k| {
                let a = k as f32 * std::f32::consts::FRAC_PI_4;
                let (px, pz) = (x + 0.3 * a.cos(), z + 0.3 * a.sin());
                world.ground_at(px, pz, 0.0).is_some()
            }) && world.ground_at(x, z, 0.0).is_some()
        };
        let mut spot = None;
        for i in 0..40 {
            for j in -20..=20 {
                let (x, z) = (front - 0.05 * i as f32, at.pos[2] + 0.1 * j as f32);
                let d = (x - at.pos[0]).hypot(z - at.pos[2]);
                if d <= talk - 0.25 && stands(x, z) {
                    spot = Some((x, z, d));
                }
            }
        }
        assert!(spot.is_some(), "nowhere in the hall to talk to her from");
    }
}
