//! The shipped content catalogs, compiled into the server: the item catalog
//! (`data/items/catalog.json`), the dye palette (`data/items/palette.json`)
//! and the character-creator data (`data/characters/creator.json`). Their
//! schemas and checks are `sim_core::catalog`; ids and indices in the
//! database and on the wire refer to these files, so the server and its data
//! are always built together.
//!
//! [`load`] adds the server's own checks to sim-core's (every house has a
//! complete default starter outfit). [`routes`] parses and checks the
//! shipped files when the router is built, so a bad data file stops the
//! server at startup rather than on the first request that needs it.
//!
//! `GET /api/catalog` and `GET /api/creator` serve the files as shipped,
//! with a strong `ETag` and `Cache-Control: public, max-age=3600`. They are
//! the same for everyone and part of the open-source repository, so they need
//! no session.
//!
//! Owned by the data side (docs/architecture.md). The realtime
//! side reads [`shipped`] too (dorm furniture shapes).

use std::sync::LazyLock;

use axum::Router;
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use protocol::{GearSlot, House};
use sha2::{Digest, Sha256};
use sim_core::catalog::TAG_DEFAULT;
use sim_core::classes::ClassContext;
use sim_core::{
    AnswerKey, Catalog, CatalogError, ClassDef, Creator, CupRules, DuelRules, Economy, Finds,
    FlightRules, Gestures, ItemKind, LootTables, Lore, Palette, PickSets, Progression, Recipes,
    Schedule, Spells, Venues,
};

use crate::AppState;

const CATALOG_JSON: &str = include_str!("../../../data/items/catalog.json");
const PALETTE_JSON: &str = include_str!("../../../data/items/palette.json");
const CREATOR_JSON: &str = include_str!("../../../data/characters/creator.json");
const RECIPES_JSON: &str = include_str!("../../../data/crafting/recipes.json");
const ECONOMY_JSON: &str = include_str!("../../../data/economy.json");
const FINDS_JSON: &str = include_str!("../../../data/world/finds.json");
const LOOT_JSON: &str = include_str!("../../../data/world/loot.json");
const SCHEDULE_JSON: &str = include_str!("../../../data/classes/schedule.json");
const GESTURES_JSON: &str = include_str!("../../../data/classes/gestures.json");
const PROGRESSION_JSON: &str = include_str!("../../../data/progression.json");
const SPELLS_JSON: &str = include_str!("../../../data/spells.json");
const DUEL_RULES_JSON: &str = include_str!("../../../data/dueling/rules.json");
const VENUES_JSON: &str = include_str!("../../../data/dueling/venues.json");
/// Every class definition (`data/classes/<id>.json`), one per scheduled class.
const CLASS_JSON: &[&str] = &[
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
// The pick vocabularies, the answer keys of the classes with pick
// beats (server only: never served or bundled), the lore entries.
const PICKS_JSON: &str = include_str!("../../../data/classes/picks.json");
const ANSWERS_JSON: &[&str] = &[
    include_str!("../../../data/classes/answers/potions.json"),
    include_str!("../../../data/classes/answers/transfiguration.json"),
    include_str!("../../../data/classes/answers/history.json"),
    include_str!("../../../data/classes/answers/divination.json"),
    include_str!("../../../data/classes/answers/herbology.json"),
    include_str!("../../../data/classes/answers/creatures.json"),
];
// In its own directory, out of the client's `data/classes/*.json`
// glob, so locked lore never reaches the bundle (`GET /api/lore` serves the
// text of the entries a character has unlocked).
const LORE_JSON: &str = include_str!("../../../data/classes/lore/lore.json");
const HOUSE_CUP_JSON: &str = include_str!("../../../data/house_cup.json");
const FLYING_JSON: &str = include_str!("../../../data/flying.json");
const STAIRS_JSON: &str = include_str!("../../../data/world/stairs.json");
const SECRETS_JSON: &str = include_str!("../../../data/world/secrets.json");
// NPCs in the open castle (docs/architecture.md "NPCs"): every file of
// data/npcs (the server reads lines.json only to check it).
const NPC_ROSTER_JSON: &str = include_str!("../../../data/npcs/roster.json");
const NPC_LOOKS_JSON: &str = include_str!("../../../data/npcs/looks.json");
const NPC_PORTRAITS_JSON: &str = include_str!("../../../data/npcs/portraits.json");
const NPC_ROUTINES_JSON: &str = include_str!("../../../data/npcs/routines.json");
const NPC_LINES_JSON: &str = include_str!("../../../data/npcs/lines.json");
const NPC_GIFTS_JSON: &str = include_str!("../../../data/npcs/gifts.json");

/// The content files, parsed and validated: the item catalog, palette and
/// creator, and the recipes, economy numbers, find spots and loot
/// tables, and the clock and schedule, classes, gestures, progression,
/// spells and dueling rules and venues. The finds, class doors and
/// dueling areas are checked against the castle layout when the world loads
/// it (`realtime::load_world`), since the layout is read at run time.
#[derive(Debug)]
pub struct Content {
    pub catalog: Catalog,
    pub palette: Palette,
    pub creator: Creator,
    pub recipes: Recipes,
    pub economy: Economy,
    pub finds: Finds,
    pub loot: LootTables,
    pub schedule: Schedule,
    /// One per scheduled class, in schedule order.
    pub classes: Vec<ClassDef>,
    pub gestures: Gestures,
    pub progression: Progression,
    pub spells: Spells,
    pub duel_rules: DuelRules,
    pub venues: Venues,
    /// `data/classes/picks.json`.
    pub picks: PickSets,
    /// One per class with pick beats (`data/classes/answers/<class>.json`).
    pub answers: Vec<AnswerKey>,
    /// `data/classes/lore/lore.json`.
    pub lore: Lore,
    /// `data/house_cup.json`.
    pub house_cup: CupRules,
    /// `data/flying.json`: broom speeds by Flying skill tier, clearance,
    /// landing.
    pub flight: FlightRules,
    /// `data/world/stairs.json`: the moving staircases' schedule, pivots and
    /// gates (checked against the castle layout by `realtime::load_world`).
    pub stairs: sim_core::stairs::StairsFile,
    /// `data/world/secrets.json`: the hidden passages and their rewards and
    /// the Wishing Room (checked against the castle layout by
    /// `realtime::load_world`, which makes the `sim_core::Secrets`).
    pub secrets: sim_core::SecretsFile,
    // NPCs.
    /// `data/npcs/*.json`, checked against the catalog, creator, palette and
    /// secrets (`NpcData::from_json`); the castle checks (stations,
    /// portrait walls, routines) run when the world loads the layout
    /// (`realtime::load_world`).
    pub npcs: sim_core::NpcData,
}

static SHIPPED: LazyLock<Content> = LazyLock::new(|| {
    load(CATALOG_JSON, PALETTE_JSON, CREATOR_JSON)
        .expect("the shipped catalogs are valid (checked by tests)")
});

fn invalid(e: impl std::fmt::Display) -> CatalogError {
    CatalogError::Invalid(e.to_string())
}

/// Parses and validates a set of content files (the files as shipped).
pub fn load(catalog: &str, palette: &str, creator: &str) -> Result<Content, CatalogError> {
    let palette = Palette::from_json(palette)?;
    let catalog = Catalog::from_json(catalog, &palette)?;
    let creator = Creator::from_json(creator)?;
    let recipes = Recipes::from_json(RECIPES_JSON, &catalog).map_err(invalid)?;
    let economy = Economy::from_json(ECONOMY_JSON).map_err(invalid)?;
    let loot = LootTables::from_json(LOOT_JSON, &catalog).map_err(invalid)?;
    let finds = Finds::from_json(FINDS_JSON).map_err(invalid)?;
    let schedule = Schedule::from_json(SCHEDULE_JSON).map_err(invalid)?;
    let gestures = Gestures::from_json(GESTURES_JSON).map_err(invalid)?;
    let progression = Progression::from_json(PROGRESSION_JSON).map_err(invalid)?;
    let spells = Spells::from_json(SPELLS_JSON, &gestures).map_err(invalid)?;
    let duel_rules = DuelRules::from_json(DUEL_RULES_JSON).map_err(invalid)?;
    let venues = Venues::from_json(VENUES_JSON).map_err(invalid)?;
    let classes = CLASS_JSON
        .iter()
        .map(|json| ClassDef::from_json(json).map_err(invalid))
        .collect::<Result<Vec<_>, _>>()?;
    let picks = PickSets::from_json(PICKS_JSON).map_err(invalid)?;
    picks.validate_with(&catalog).map_err(invalid)?;
    let answers = ANSWERS_JSON
        .iter()
        .map(|json| AnswerKey::from_json(json).map_err(invalid))
        .collect::<Result<Vec<_>, _>>()?;
    let lore = Lore::from_json(LORE_JSON).map_err(invalid)?;
    let house_cup = CupRules::from_json(HOUSE_CUP_JSON).map_err(invalid)?;
    let flight = FlightRules::from_json(FLYING_JSON).map_err(invalid)?;
    let stairs: sim_core::stairs::StairsFile =
        serde_json::from_str(STAIRS_JSON).map_err(invalid)?;
    sim_core::Stairs::new(stairs.clone()).map_err(invalid)?;
    let secrets: sim_core::SecretsFile = serde_json::from_str(SECRETS_JSON).map_err(invalid)?;
    // Every reward recipe exists.
    for s in &secrets.secrets {
        if let Some(r) = s.reward.recipe
            && recipes.get(r).is_none()
        {
            return Err(invalid(format!("secret {}: unknown recipe {r}", s.id)));
        }
    }
    house_cup
        .validate_with(&catalog, &duel_rules)
        .map_err(invalid)?;
    let secret_ids: Vec<&str> = secrets.secrets.iter().map(|s| s.id.as_str()).collect();
    let npcs = sim_core::NpcData::from_json(
        NPC_ROSTER_JSON,
        NPC_LOOKS_JSON,
        NPC_PORTRAITS_JSON,
        NPC_ROUTINES_JSON,
        NPC_LINES_JSON,
        NPC_GIFTS_JSON,
        &sim_core::npcs::NpcChecks {
            creator: &creator,
            catalog: &catalog,
            palette: &palette,
            secrets: &secret_ids,
        },
    )
    .map_err(invalid)?;
    let content = Content {
        catalog,
        palette,
        creator,
        recipes,
        economy,
        finds,
        loot,
        schedule,
        classes,
        gestures,
        progression,
        spells,
        duel_rules,
        venues,
        picks,
        answers,
        lore,
        house_cup,
        flight,
        stairs,
        secrets,
        npcs,
    };
    content.check_starter_outfits()?;
    content.check_school()?;
    Ok(content)
}

impl Content {
    /// The class definition of a scheduled class.
    pub fn class(&self, id: &str) -> Option<&ClassDef> {
        self.classes.iter().find(|c| c.id == id)
    }

    /// The answer key of a class with pick beats (server only).
    pub fn answers(&self, class: &str) -> Option<&AnswerKey> {
        self.answers.iter().find(|k| k.class == class)
    }

    /// the cross-checks: every scheduled class has exactly one
    /// definition that fits its session, gestures and spells; professors
    /// wear valid catalog clothing; every spell is taught by a scheduled
    /// class or a venue; layouts the schedule and venues name are distinct
    /// from the dorm shells. Each class's picks, answer key and
    /// rewards v2 (`ClassDef::validate_with`), and every answer key belongs
    /// to a scheduled class with pick beats.
    fn check_school(&self) -> Result<(), CatalogError> {
        let fail = |msg: String| Err(CatalogError::Invalid(msg));
        if self.classes.len() != self.schedule.classes.len() {
            return fail("every scheduled class needs one definition".into());
        }
        for slot in &self.schedule.classes {
            let Some(class) = self.class(&slot.id) else {
                return fail(format!("class {}: no definition", slot.id));
            };
            let cx = ClassContext {
                schedule: slot,
                gestures: &self.gestures,
                spells: &self.spells,
                picks: &self.picks,
                answers: self.answers(&slot.id),
                catalog: &self.catalog,
                recipes: &self.recipes,
                lore: &self.lore,
            };
            class.validate_with(&cx).map_err(CatalogError::Invalid)?;
            let look = &class.professor.appearance;
            if let Err(e) = self
                .creator
                .check_appearance(look, &self.catalog, &self.palette)
            {
                return fail(format!("class {}: the professor's look: {e}", slot.id));
            }
        }
        for key in &self.answers {
            if !self.class(&key.class).is_some_and(ClassDef::has_picks) {
                return fail(format!(
                    "answers {}: no scheduled class with picks",
                    key.class
                ));
            }
        }
        for spell in &self.spells.spells {
            let at = &spell.unlock.at;
            if self.schedule.class(at).is_none() && self.venues.get(at).is_none() {
                return fail(format!("spell {}: nothing teaches it at {at:?}", spell.key));
            }
        }
        // Spells L20 (CORE): Tinctura's colours are palette dyes.
        self.spells
            .check_palette(self.palette.colours.len())
            .map_err(CatalogError::Invalid)?;
        for layout in self
            .schedule
            .classes
            .iter()
            .map(|c| c.layout.as_str())
            .chain(self.venues.venues.iter().map(|v| v.arena_layout.as_str()))
        {
            if layout.starts_with("dorm_") {
                return fail(format!("layout {layout}: dorm_ layouts are dorm shells"));
            }
        }
        Ok(())
    }

    /// What character creation relies on, per house: at most one `default`
    /// item per slot, each a starter clothing item of that house for the
    /// slot, covering body, legs and shoes; and the creator's default look
    /// wearing them passes `Creator::check_appearance`.
    fn check_starter_outfits(&self) -> Result<(), CatalogError> {
        let fail = |msg: String| Err(CatalogError::Invalid(msg));
        for item in self.catalog.items.iter().filter(|i| i.has_tag(TAG_DEFAULT)) {
            if item.kind != ItemKind::Clothing || !item.has_tag(sim_core::catalog::TAG_STARTER) {
                return fail(format!(
                    "item {}: default items are starter clothing",
                    item.id
                ));
            }
        }
        for house in House::ALL {
            let mut outfit = protocol::Outfit::default();
            for item in self.catalog.starter_items(house) {
                let Some(slot) = item.slot.filter(|_| item.has_tag(TAG_DEFAULT)) else {
                    continue;
                };
                if outfit.get(slot).is_some() {
                    return fail(format!("{house:?}: two default items for {slot:?}"));
                }
                outfit.set(
                    slot,
                    Some(protocol::Worn::new(
                        item.id,
                        item.granted_dyes(house, &self.palette),
                    )),
                );
            }
            for slot in [GearSlot::Body, GearSlot::Legs, GearSlot::Shoes] {
                if outfit.get(slot).is_none() {
                    return fail(format!("{house:?}: no default item for {slot:?}"));
                }
            }
            let look = protocol::Appearance {
                outfit,
                ..self.creator.default_appearance(0)
            };
            if let Err(e) = self
                .creator
                .check_appearance(&look, &self.catalog, &self.palette)
            {
                return fail(format!("{house:?}: the default look is invalid: {e}"));
            }
        }
        Ok(())
    }
}

/// The content this server was built with.
pub fn shipped() -> &'static Content {
    &SHIPPED
}

/// A look read from the database, brought up to the shipped creator file
/// (`Creator::migrate_appearance`): a retired hair style maps to its
/// replacement for the look's body and an unknown eye colour to Brown, so a
/// row that migration 0013 missed still loads instead of locking the player
/// out. Free when nothing changes.
pub fn migrate_look(mut look: protocol::Appearance) -> protocol::Appearance {
    if shipped().creator.migrate_appearance(&mut look) {
        tracing::debug!(hair_style = look.hair_style, "migrated a stored look");
    }
    look
}

/// A static JSON body with its strong validator.
struct Cached {
    body: String,
    etag: HeaderValue,
}

impl Cached {
    fn new(body: String) -> Self {
        let hash = Sha256::digest(body.as_bytes());
        let hex: String = hash[..12].iter().map(|b| format!("{b:02x}")).collect();
        let etag = HeaderValue::from_str(&format!("\"{hex}\"")).expect("hex is a valid header");
        Cached { body, etag }
    }

    /// `304` when the client's `If-None-Match` already has this version.
    fn respond(&self, headers: &HeaderMap) -> Response {
        let cache = [
            (
                header::CACHE_CONTROL,
                HeaderValue::from_static("public, max-age=3600"),
            ),
            (header::ETAG, self.etag.clone()),
        ];
        let fresh = headers
            .get_all(header::IF_NONE_MATCH)
            .iter()
            .filter_map(|v| v.to_str().ok())
            .flat_map(|v| v.split(','))
            .any(|tag| tag.trim() == self.etag || tag.trim() == "*");
        if fresh {
            return (StatusCode::NOT_MODIFIED, cache).into_response();
        }
        let json = (
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/json"),
        );
        (cache, [json], self.body.clone()).into_response()
    }
}

static CATALOG_BODY: LazyLock<Cached> = LazyLock::new(|| Cached::new(CATALOG_JSON.to_owned()));

static CREATOR_BODY: LazyLock<Cached> = LazyLock::new(|| {
    // Both files are valid JSON (parsed by `shipped`), so splicing them is too.
    Cached::new(format!(
        "{{\"creator\":{},\"palette\":{}}}",
        CREATOR_JSON.trim(),
        PALETTE_JSON.trim()
    ))
});

/// `GET /api/catalog`: `catalog.json` as shipped.
pub async fn catalog(headers: HeaderMap) -> Response {
    CATALOG_BODY.respond(&headers)
}

/// `GET /api/creator`: `{ creator, palette }` as shipped.
pub async fn creator(headers: HeaderMap) -> Response {
    CREATOR_BODY.respond(&headers)
}

/// This module's routes, merged under `/api` by `http::router`. Building them
/// loads and checks the shipped content (panicking on bad data at startup).
pub fn routes() -> Router<AppState> {
    use axum::routing::get;
    let content = shipped();
    tracing::debug!(
        items = content.catalog.items.len(),
        colours = content.palette.colours.len(),
        "content loaded"
    );
    Router::new()
        .route("/catalog", get(catalog))
        .route("/creator", get(creator))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_shipped_content_loads() {
        let c = super::shipped();
        assert!(!c.catalog.items.is_empty());
        assert_eq!(c.creator.body_types.len(), 2);
    }

    #[test]
    fn the_economy_data_loads_and_agrees_with_the_migration() {
        let c = super::shipped();
        assert!(!c.recipes.recipes.is_empty());
        assert!(c.finds.spots.len() >= 30);
        // db/migrations/0007_economy.sql backfills existing characters with
        // the starting purse of its day; a change here needs a migration too.
        assert_eq!(c.economy.starting_purse, 100);
        let migration = include_str!("../../../db/migrations/0007_economy.sql");
        assert!(migration.contains("SET crowns = 100"));
        // The finds match the shipped castle layout and the Lost
        // Property Vault.
        let layout =
            sim_core::Layout::from_json(include_str!("../../../data/world/layout.json")).unwrap();
        let hidden = sim_core::Layout::from_json(include_str!(
            "../../../data/world/instances/lost_property_vault.json"
        ))
        .unwrap();
        c.finds.validate(&[&layout, &hidden], &c.loot).unwrap();
    }

    /// Spells L20 (CORE): every spell is taught by a scheduled class or a
    /// venue at its level, and Tinctura's colours are palette dyes.
    #[test]
    fn every_spell_is_taught_and_tinctura_uses_the_palette() {
        let c = super::shipped();
        for spell in &c.spells.spells {
            let at = spell.unlock.at.as_str();
            assert!(
                c.schedule.class(at).is_some() || c.venues.get(at).is_some(),
                "{}",
                spell.key
            );
            assert!(
                c.spells
                    .taught_at(at, spell.unlock.level)
                    .any(|s| s.id == spell.id),
                "{}",
                spell.key
            );
        }
        assert_eq!(c.spells.taught_at("transfiguration", 20).count(), 1);
        assert!(c.spells.check_palette(c.palette.colours.len()).is_ok());
        assert!(c.spells.check_palette(20).is_err());
    }

    #[test]
    fn the_creator_body_is_valid_json() {
        let v: serde_json::Value = serde_json::from_str(&CREATOR_BODY.body).unwrap();
        assert_eq!(v["creator"]["version"], 2);
        assert_eq!(v["creator"]["eye_colours"].as_array().unwrap().len(), 13);
        assert!(v["palette"]["colours"].as_array().unwrap().len() >= 40);
    }

    /// db/migrations/0013_character_adoption.sql maps the retired hair
    /// styles exactly as creator.json's `migrate_to` does.
    #[test]
    fn the_adoption_migration_agrees_with_the_creator() {
        let sql = include_str!("../../../db/migrations/0013_character_adoption.sql");
        let c = &super::shipped().creator;
        let values = sql.split("new_id) VALUES").nth(1).unwrap();
        let values = values.split(';').next().unwrap();
        let mut pairs = 0;
        for line in values.lines().filter(|l| l.trim_start().starts_with('(')) {
            let line = line.split("--").next().unwrap();
            for tuple in line.split('(').skip(1) {
                let nums: Vec<u16> = tuple
                    .split(')')
                    .next()
                    .unwrap()
                    .split(',')
                    .map(|n| n.trim().parse().unwrap())
                    .collect();
                let [old, body, new] = nums[..] else {
                    panic!("{tuple:?}")
                };
                assert_eq!(c.migrate_hair(old, body as u8), Some(new), "{old} {body}");
                pairs += 1;
            }
        }
        let retired = c.hair_styles.iter().filter(|h| h.retired).count();
        assert_eq!(pairs, retired * c.body_types.len());
    }

    /// The shipped catalog with one edit applied to its text.
    fn edited(from: &str, to: &str) -> Result<Content, CatalogError> {
        assert!(CATALOG_JSON.contains(from), "{from:?}");
        load(
            &CATALOG_JSON.replacen(from, to, 1),
            PALETTE_JSON,
            CREATOR_JSON,
        )
    }

    #[test]
    fn every_house_needs_a_default_outfit() {
        // Roarwing's robe loses its default tag: no default body item.
        let err = edited(
            r#""tags": ["starter", "default", "school", "house:roarwing"]"#,
            r#""tags": ["starter", "school", "house:roarwing"]"#,
        )
        .unwrap_err();
        assert!(err.to_string().contains("no default item"), "{err}");
    }

    #[test]
    fn defaults_must_be_unique_starter_clothing() {
        // The pointed hat becomes a default, but is not starter: refused.
        let err = edited(
            r#""tags": ["starter", "school", "basic"],"#,
            r#""tags": ["default", "school", "basic"],"#,
        )
        .unwrap_err();
        assert!(err.to_string().contains("starter clothing"), "{err}");
        // A second default body item for everyone (the linen shirt).
        let err = edited(
            r#""tags": ["starter", "casual", "basic"],"#,
            r#""tags": ["starter", "default", "casual", "basic"],"#,
        )
        .unwrap_err();
        assert!(err.to_string().contains("two default items"), "{err}");
    }
}
