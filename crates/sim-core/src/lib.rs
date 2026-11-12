//! Game rules shared by the server and the browser client (compiled to WebAssembly
//! through `sim-wasm`). No I/O, no clocks, no randomness: every function is a pure
//! function of its inputs so the client predicts exactly what the server checks.
//!
//! Coordinates: metres, right-handed, Y up (same as glTF). x = east, z = south.
//!
//! - [`layout`], [`world`], [`movement`]: the walkable world and how characters move.
//! - [`placement`]: where dorm furniture may stand.
//! - [`catalog`]: the item catalog, dye palette and character-creator data.
//! - [`economy`] (`data/economy.json`), [`exchange`] (the Owl
//!   Exchange's categories), [`crafting`] (`data/crafting/recipes.json`),
//!   [`finds`] (`data/world/finds.json`, `data/world/loot.json`).
//! - [`clock`] (game time and the class schedule,
//!   `data/classes/schedule.json`), [`classes`] (class definitions, the
//!   professor and the lesson script, `data/classes/<id>.json`), [`gesture`]
//!   (wand gestures and the trace scorer, `data/classes/gestures.json`),
//!   [`progression`] (levels and XP, `data/progression.json`), [`spells`]
//!   (`data/spells.json`), [`dueling`] (`data/dueling/*.json`) and [`duel`]
//!   (the duel step function).
//! - [`pick`] (pick beats: vocabularies `data/classes/picks.json`,
//!   the server-only answer keys `data/classes/answers/`, the scorer),
//!   class definitions v2 with [`classes::Subject`] and rewards v2, the
//!   timetable's slots and night-only classes ([`clock`]), the daily class
//!   XP cap ([`progression`]) and the wayfinding trail's destinations
//!   ([`key_places`], `data/world/key_places.json`).
//! - [`house_cup`] (the weekly House Cup: weeks, per-source daily
//!   caps, winners and the reward, `data/house_cup.json`) and the
//!   `notice_board` interactable kind.
//! - [`flight`] (brooms: airspace, `check_flight`, `fly_and_slide`,
//!   mounting and landing, `data/flying.json`), [`ring`] (the Flying class's
//!   ring beats) and `Subject::Flying`.
//! - [`stairs`] (the Grand Staircase's moving flights: the shared
//!   schedule, carrying riders, the grace window, the landing gates and one
//!   world per staircase state, `data/world/stairs.json`).
//! - [`secrets`] (the hidden passages, the first-step discovery,
//!   and the map's reveal rule, `data/world/secrets.json`)
//!   and [`wishing_room`] (the Wishing Room's pacing).
//! - Spells to level 20 (protocol v11): the new spells and their duel
//!   effects ([`spells`], [`duel`]: roots, slows, waves, Adveni's pull and
//!   stagger, Dissolvo's cleanse, the Guardian ward), slowing hindrances and
//!   Adveni's safe slide ([`movement::pull_destination`]) and Aperio's spots
//!   ([`reveal`]).

pub mod catalog;
pub mod classes;
pub mod clock;
pub mod crafting;
pub mod duel;
pub mod dueling;
pub mod economy;
pub mod exchange;
pub mod finds;
pub mod flight;
pub mod gesture;
pub mod house_cup;
pub mod key_places;
pub mod layout;
pub mod movement;
// `nav`: the walkable-surface graph and path search (ported from
// tools/loadtest/src/nav.rs).
pub mod nav;
pub mod npcs;
pub mod pick;
pub mod placement;
pub mod progression;
pub mod reveal;
pub mod ring;
pub mod secrets;
pub mod spells;
pub mod stairs;
pub mod wishing_room;
pub mod world;

#[cfg(test)]
mod testutil;

pub use catalog::{
    AppearanceError, Catalog, CatalogError, Creator, DyeError, ItemDef, ItemKind, Palette,
};
pub use classes::{ClassDef, ClassError, LessonAction, LessonStep, Lore, NpcDef, Subject};
pub use clock::{ClockRules, GameTime, Period, Schedule, ScheduleError, SessionKey, SessionTimes};
pub use crafting::{Discipline, Recipe, RecipeError, RecipeKind, Recipes};
pub use duel::{Duel, DuelInput, PullMotion, Ward, pull_target};
pub use dueling::{DuelRules, DuelingError, Venue, Venues};
pub use economy::{Economy, EconomyError};
pub use finds::{Drops, Finds, FindsError, LootTables, Luck};
pub use flight::{
    Airspace, FlightCheck, FlightReject, FlightRules, FlyParams, check_flight, fly_and_slide,
};
pub use gesture::{Gesture, GestureError, Gestures};
pub use house_cup::{CupError, CupRules, CupSource, CupWeek};
pub use key_places::{KeyPlace, KeyPlaces, PlaceGroup, PlaceTarget};
pub use layout::{
    Area, AreaKind, Blocker, Heights, Interactable, InteractableKind, Layout, LayoutError,
    MAX_GROUND_SLOPE, Moving, Ring, StairPosition, Surface, SurfaceKind,
};
pub use movement::{
    MoveCheck, MoveParams, PullBlocked, Reach, RejectReason, check_move, footprint_ground,
    move_and_slide, pull_destination, stand_near,
};
pub use npcs::{
    GiftsFile, LinesFile, Looks, NpcData, NpcKind, NpcNavs, NpcRules, NpcsError, PortraitsFile,
    Roster, RoutinePlan, RoutinesFile,
};
pub use pick::{AnswerKey, PickError, PickSet, PickSets};
pub use placement::{
    AreaFrame, FurnitureShape, PlaceArea, PlaceSurface, PlacementError, check_placement, keeps_way,
};
pub use progression::{Progression, ProgressionError};
pub use reveal::reveal_near;
pub use secrets::{Reward, Secret, Secrets, SecretsError, SecretsFile, reveal_at};
pub use spells::{SpellDef, SpellError, Spells, guardian_animal};
pub use stairs::{
    FlightPhase, FlightState, HouseWorlds, Stairs, StairsError, Worlds, check_move_near,
};
pub use wishing_room::{Pacing, RoomDef, WishingRoomRules};
pub use world::{Ground, SurfaceId, World};
