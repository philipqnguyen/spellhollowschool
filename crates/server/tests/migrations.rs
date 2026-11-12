//! The migrations apply cleanly on top of an early
//! database with data in it (and, like every other test, on a fresh one):
//! the 1b schema is built from migrations 0001-0006 alone in a throwaway
//! database, filled with an early character, then upgraded with the full
//! set the server embeds.

use rand::Rng;
use server::db::MIGRATOR;
use sqlx::migrate::Migrator;
use sqlx::{Connection, Executor, PgConnection, PgPool};

/// The last 1b migration.
const LAST_1B: i64 = 6;

fn base_url() -> String {
    std::env::var("DATABASE_URL").unwrap_or_else(|_| "postgres://localhost/shs_test".into())
}

/// `postgres://host/db` with the database name replaced.
fn with_database(url: &str, db: &str) -> String {
    let cut = url.rfind('/').expect("a database url has a path");
    let (head, tail) = url.split_at(cut);
    let query = tail.find('?').map_or("", |q| &tail[q..]);
    format!("{head}/{db}{query}")
}

#[tokio::test]
async fn the_1c_migrations_upgrade_a_1b_database() {
    let name = format!(
        "shs_migtest_{}",
        rand::thread_rng().gen_range(100_000_000u64..999_999_999)
    );
    let mut admin = PgConnection::connect(&base_url())
        .await
        .expect("database reachable");
    admin
        .execute(format!("CREATE DATABASE {name}").as_str())
        .await
        .unwrap();
    let url = with_database(&base_url(), &name);
    let result = upgrade(&url).await;
    // Always clean up, then report.
    admin
        .execute(format!("DROP DATABASE {name} WITH (FORCE)").as_str())
        .await
        .unwrap();
    result.unwrap();
}

async fn upgrade(url: &str) -> Result<(), String> {
    let pool = PgPool::connect(url).await.map_err(|e| e.to_string())?;
    // The 1b schema: the embedded migrations up to 0006, under the same
    // versions and checksums, so the full set later sees them as applied.
    let mut old: Migrator = Migrator::new(std::path::Path::new(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../db/migrations"
    )))
    .await
    .map_err(|e| e.to_string())?;
    old.migrations = MIGRATOR
        .migrations
        .iter()
        .filter(|m| m.version <= LAST_1B)
        .cloned()
        .collect::<Vec<_>>()
        .into();
    old.run(&pool).await.map_err(|e| e.to_string())?;

    // A 1b character with its starter grant, an equipped item and a placed one.
    let account: i64 = sqlx::query_scalar(
        "INSERT INTO accounts (email, password_hash, adult_attested_at, email_verified_at)
         VALUES ('old@example.org', '!', now(), now()) RETURNING id",
    )
    .fetch_one(&pool)
    .await
    .map_err(|e| e.to_string())?;
    let character: i64 = sqlx::query_scalar(
        "INSERT INTO characters (account_id, name, name_key, house, starter_granted_at)
         VALUES ($1, 'Oldtimer', 'oldtimer', 2, now()) RETURNING id",
    )
    .bind(account)
    .fetch_one(&pool)
    .await
    .map_err(|e| e.to_string())?;
    let robe: i64 = sqlx::query_scalar(
        "INSERT INTO items (owner_id, item_def, dye_primary, dye_secondary, dye_trim, location)
         VALUES ($1, 203, 0, 32, 38, 'equipped') RETURNING id",
    )
    .bind(character)
    .fetch_one(&pool)
    .await
    .map_err(|e| e.to_string())?;
    sqlx::query("INSERT INTO equipment (character_id, slot, item_id) VALUES ($1, 'body', $2)")
        .bind(character)
        .bind(robe)
        .execute(&pool)
        .await
        .map_err(|e| e.to_string())?;
    sqlx::query(
        "INSERT INTO items (owner_id, item_def, dye_primary, dye_secondary, dye_trim)
         VALUES ($1, 1005, 10, 10, 14)",
    )
    .bind(character)
    .execute(&pool)
    .await
    .map_err(|e| e.to_string())?;

    // A session of that account (signed in before several characters).
    sqlx::query(
        "INSERT INTO sessions (token_hash, account_id, expires_at)
         VALUES (decode(repeat('ab', 32), 'hex'), $1, now() + interval '1 day')",
    )
    .bind(account)
    .execute(&pool)
    .await
    .map_err(|e| e.to_string())?;

    // Upgrade with everything the server embeds.
    MIGRATOR.run(&pool).await.map_err(|e| e.to_string())?;

    let plays: Option<i64> =
        sqlx::query_scalar("SELECT character_id FROM sessions WHERE account_id = $1")
            .bind(account)
            .fetch_one(&pool)
            .await
            .map_err(|e| e.to_string())?;
    check(
        plays == Some(character),
        "an existing session plays the account's character",
    )?;

    let (crowns, purse_rows): (i64, i64) = sqlx::query_as(
        "SELECT crowns, (SELECT count(*) FROM crown_ledger WHERE character_id = $1
                           AND reason = 'starting_purse' AND delta = 100)
         FROM characters WHERE id = $1",
    )
    .bind(character)
    .fetch_one(&pool)
    .await
    .map_err(|e| e.to_string())?;
    check(
        crowns == 100 && purse_rows == 1,
        "the purse is backfilled with its ledger row",
    )?;
    let (backfilled, quantities): (i64, i64) = sqlx::query_as(
        "SELECT (SELECT count(*) FROM item_ledger WHERE to_owner = $1 AND reason = 'backfill'),
                (SELECT count(*) FROM items WHERE owner_id = $1 AND quantity = 1 AND finish = 0)",
    )
    .bind(character)
    .fetch_one(&pool)
    .await
    .map_err(|e| e.to_string())?;
    check(
        backfilled == 2 && quantities == 2,
        "every item has a ledger row and quantity 1",
    )?;
    // The new locations are allowed, others still refused.
    sqlx::query(
        "UPDATE items SET location = 'mail' WHERE owner_id = $1 AND location = 'inventory'",
    )
    .bind(character)
    .execute(&pool)
    .await
    .map_err(|e| e.to_string())?;
    let bad = sqlx::query("UPDATE items SET location = 'pocket' WHERE owner_id = $1")
        .bind(character)
        .execute(&pool)
        .await;
    check(bad.is_err(), "unknown locations are refused")?;
    // Every 1c table exists.
    for table in [
        "mail",
        "mail_attachments",
        "shop_orders",
        "exchange_listings",
        "trades",
        "skills",
        "known_recipes",
        "crafts",
        "find_cooldowns",
        "active_effects",
    ] {
        sqlx::query(&format!("SELECT count(*) FROM {table}"))
            .execute(&pool)
            .await
            .map_err(|e| format!("{table}: {e}"))?;
    }
    pool.close().await;
    Ok(())
}

fn check(ok: bool, what: &str) -> Result<(), String> {
    if ok { Ok(()) } else { Err(what.to_owned()) }
}

/// Migration 0013 (character adoption): looks lose `build`, gain Brown eyes
/// and map their retired hair style per body; a look already migrated is
/// left alone; Mimic Draught disguises in effect are migrated too.
#[tokio::test]
async fn the_adoption_migration_maps_old_looks() {
    let name = format!(
        "shs_migtest_{}",
        rand::thread_rng().gen_range(100_000_000u64..999_999_999)
    );
    let mut admin = PgConnection::connect(&base_url())
        .await
        .expect("database reachable");
    admin
        .execute(format!("CREATE DATABASE {name}").as_str())
        .await
        .unwrap();
    let url = with_database(&base_url(), &name);
    let result = adopt(&url).await;
    admin
        .execute(format!("DROP DATABASE {name} WITH (FORCE)").as_str())
        .await
        .unwrap();
    result.unwrap();
}

/// The last migration before the character adoption.
const LAST_BEFORE_ADOPTION: i64 = 12;

async fn adopt(url: &str) -> Result<(), String> {
    use serde_json::{Value, json};
    let e = |e: sqlx::Error| e.to_string();
    let pool = PgPool::connect(url).await.map_err(|e| e.to_string())?;
    let mut old: Migrator = Migrator::new(std::path::Path::new(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../db/migrations"
    )))
    .await
    .map_err(|e| e.to_string())?;
    old.migrations = MIGRATOR
        .migrations
        .iter()
        .filter(|m| m.version <= LAST_BEFORE_ADOPTION)
        .cloned()
        .collect::<Vec<_>>()
        .into();
    old.run(&pool).await.map_err(|e| e.to_string())?;

    let look = |body_type: u8, hair: u16| {
        json!({ "body_type": body_type, "height": 128, "build": [200, 0, 30],
                "face": [128, 128, 128, 128, 128], "skin_tone": 2, "hair_style": hair,
                "hair_colour": 3, "outfit": { "hat": null, "body": null, "legs": null, "shoes": null } })
    };
    let new_look = json!({ "body_type": 1, "height": 30, "face": [128, 128, 128, 128, 128],
        "skin_tone": 2, "hair_style": 18, "hair_colour": 15, "eye_colour": 9,
        "outfit": { "hat": null, "body": null, "legs": null, "shoes": null } });
    let mut ids = Vec::new();
    for (i, appearance) in [
        Some(look(0, 4)),
        Some(look(1, 4)),
        Some(look(1, 11)),
        Some(new_look.clone()),
        None,
    ]
    .into_iter()
    .enumerate()
    {
        let account: i64 = sqlx::query_scalar(
            "INSERT INTO accounts (email, password_hash, adult_attested_at, email_verified_at)
             VALUES ($1, '!', now(), now()) RETURNING id",
        )
        .bind(format!("adopt{i}@example.org"))
        .fetch_one(&pool)
        .await
        .map_err(e)?;
        let id: i64 = sqlx::query_scalar(
            "INSERT INTO characters (account_id, name, name_key, house, appearance, appearance_version)
             VALUES ($1, $2, $3, 1, $4, 5) RETURNING id",
        )
        .bind(account)
        .bind(format!("Adopt{i}"))
        .bind(format!("adopt{i}"))
        .bind(appearance)
        .fetch_one(&pool)
        .await
        .map_err(e)?;
        ids.push(id);
    }
    sqlx::query(
        "INSERT INTO active_effects (character_id, kind, visible, item_def, expires_at)
         VALUES ($1, 'mimic_draught', $2, 1, now() + interval '1 hour')",
    )
    .bind(ids[0])
    .bind(json!({ "disguise": { "appearance": look(1, 6) } }))
    .execute(&pool)
    .await
    .map_err(e)?;

    MIGRATOR.run(&pool).await.map_err(|e| e.to_string())?;

    let rows: Vec<(i64, Option<Value>, i32)> =
        sqlx::query_as("SELECT id, appearance, appearance_version FROM characters ORDER BY id")
            .fetch_all(&pool)
            .await
            .map_err(e)?;
    let got = |i: usize| rows.iter().find(|r| r.0 == ids[i]).unwrap().clone();
    for (i, hair) in [(0, 14 + 2), (1, 17), (2, 19)] {
        let (_, a, version) = got(i);
        let a = a.unwrap();
        check(a.get("build").is_none(), "build is dropped")?;
        check(a["eye_colour"] == 1, "old looks get Brown eyes")?;
        check(
            a["hair_style"] == hair,
            &format!("look {i}: hair {}", a["hair_style"]),
        )?;
        check(
            a["hair_colour"] == 3 && a["skin_tone"] == 2,
            "the rest is kept",
        )?;
        check(version == 6, "the appearance version goes up")?;
        // And it reads as a valid look of its body.
        let parsed: protocol::Appearance = serde_json::from_value(a).map_err(|e| e.to_string())?;
        let c = server::catalog::shipped();
        check(
            c.creator
                .check_appearance(&parsed, &c.catalog, &c.palette)
                .is_ok(),
            "the migrated look is valid",
        )?;
    }
    let (_, a, version) = got(3);
    check(
        a == Some(new_look) && version == 5,
        "an already migrated look is untouched",
    )?;
    let (_, a, version) = got(4);
    check(a.is_none() && version == 5, "no look stays no look")?;
    let disguise: Value =
        sqlx::query_scalar("SELECT visible FROM active_effects WHERE character_id = $1")
            .bind(ids[0])
            .fetch_one(&pool)
            .await
            .map_err(e)?;
    let d = &disguise["disguise"]["appearance"];
    check(
        d["hair_style"] == 18 && d["eye_colour"] == 1 && d.get("build").is_none(),
        "Mimic Draught disguises are migrated",
    )?;
    pool.close().await;
    Ok(())
}

/// The last migration before the school-day migrations.
const LAST_BEFORE_SCHOOL_DAY: i64 = 13;

/// the `0014_school_day` upgrades a 1d database with a class
/// attended and a potion running (dev rows stay valid as they are), and its
/// new rules hold: the nine subjects, a fortune effect names its fortune,
/// one lore unlock per attendance and per entry.
#[tokio::test]
async fn the_school_day_migration_upgrades_a_1d_database() {
    let name = format!(
        "shs_migtest_{}",
        rand::thread_rng().gen_range(100_000_000u64..999_999_999)
    );
    let mut admin = PgConnection::connect(&base_url())
        .await
        .expect("database reachable");
    admin
        .execute(format!("CREATE DATABASE {name}").as_str())
        .await
        .unwrap();
    let url = with_database(&base_url(), &name);
    let result = school_day(&url).await;
    admin
        .execute(format!("DROP DATABASE {name} WITH (FORCE)").as_str())
        .await
        .unwrap();
    result.unwrap();
}

async fn school_day(url: &str) -> Result<(), String> {
    let e = |e: sqlx::Error| e.to_string();
    let pool = PgPool::connect(url).await.map_err(|e| e.to_string())?;
    let mut old: Migrator = Migrator::new(std::path::Path::new(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../db/migrations"
    )))
    .await
    .map_err(|e| e.to_string())?;
    old.migrations = MIGRATOR
        .migrations
        .iter()
        .filter(|m| m.version <= LAST_BEFORE_SCHOOL_DAY)
        .cloned()
        .collect::<Vec<_>>()
        .into();
    old.run(&pool).await.map_err(|e| e.to_string())?;

    let account: i64 = sqlx::query_scalar(
        "INSERT INTO accounts (email, password_hash, adult_attested_at, email_verified_at)
         VALUES ('school@example.org', '!', now(), now()) RETURNING id",
    )
    .fetch_one(&pool)
    .await
    .map_err(e)?;
    let character: i64 = sqlx::query_scalar(
        "INSERT INTO characters (account_id, name, name_key, house)
         VALUES ($1, 'Scholar', 'scholar', 2) RETURNING id",
    )
    .bind(account)
    .fetch_one(&pool)
    .await
    .map_err(e)?;
    let session: i64 = sqlx::query_scalar(
        "INSERT INTO class_sessions (class_id, session_key, section, starts_at, ends_at)
         VALUES ('charms', 1000, 0, now(), now() + interval '9 minutes') RETURNING id",
    )
    .fetch_one(&pool)
    .await
    .map_err(e)?;
    let attendance: i64 = sqlx::query_scalar(
        "INSERT INTO class_attendance (session_id, character_id, class_id, session_key,
                                       score_pct, traces, xp, rewarded_at)
         VALUES ($1, $2, 'charms', 1000, 80, 7, 128, now()) RETURNING id",
    )
    .bind(session)
    .bind(character)
    .fetch_one(&pool)
    .await
    .map_err(e)?;
    sqlx::query(
        "INSERT INTO active_effects (character_id, kind, speed_pct, item_def, started_at, expires_at)
         VALUES ($1, 'glow', 100, 3301, now(), now() + interval '1 hour')",
    )
    .bind(character)
    .execute(&pool)
    .await
    .map_err(e)?;

    MIGRATOR.run(&pool).await.map_err(|e| e.to_string())?;

    // The attendance keeps its numbers: traces are now answers, nothing v2.
    let row: (
        i16,
        i32,
        i32,
        serde_json::Value,
        Vec<i32>,
        Option<i32>,
        Option<String>,
    ) = sqlx::query_as(
        "SELECT answers, xp, crowns, items, recipes, lore_id, fortune
             FROM class_attendance WHERE id = $1",
    )
    .bind(attendance)
    .fetch_one(&pool)
    .await
    .map_err(e)?;
    if row != (7, 128, 0, serde_json::json!([]), vec![], None, None) {
        return Err(format!("attendance after 0014: {row:?}"));
    }
    let glow: (String, Option<String>) =
        sqlx::query_as("SELECT kind, fortune FROM active_effects WHERE character_id = $1")
            .bind(character)
            .fetch_one(&pool)
            .await
            .map_err(e)?;
    if glow != ("glow".into(), None) {
        return Err(format!("the potion after 0014: {glow:?}"));
    }

    // Every subject is a skill track; anything else is not.
    for subject in sim_core::Subject::ALL {
        sqlx::query("INSERT INTO skills (character_id, discipline, xp) VALUES ($1, $2, 1)")
            .bind(character)
            .bind(subject.id())
            .execute(&pool)
            .await
            .map_err(e)?;
    }
    let refused = |r: Result<sqlx::postgres::PgQueryResult, sqlx::Error>, what: &str| match r {
        Err(_) => Ok(()),
        Ok(_) => Err(format!("{what} was accepted")),
    };
    refused(
        sqlx::query("INSERT INTO skills (character_id, discipline, xp) VALUES ($1, 'flying', 1)")
            .bind(character)
            .execute(&pool)
            .await,
        "a flying skill",
    )?;
    // A fortune names its kind; a potion never has one.
    refused(
        sqlx::query(
            "INSERT INTO active_effects (character_id, kind, speed_pct, item_def, started_at, expires_at)
             VALUES ($1, 'fortune', 100, 0, now(), now() + interval '1 hour')",
        )
        .bind(character)
        .execute(&pool)
        .await,
        "a fortune without its kind",
    )?;
    refused(
        sqlx::query("UPDATE active_effects SET fortune = 'luck' WHERE character_id = $1")
            .bind(character)
            .execute(&pool)
            .await,
        "a potion with a fortune",
    )?;
    sqlx::query(
        "INSERT INTO active_effects (character_id, kind, speed_pct, item_def, fortune, started_at, expires_at)
         VALUES ($1, 'fortune', 100, 0, 'keen_mind', now(), now() + interval '1 hour')",
    )
    .bind(character)
    .execute(&pool)
    .await
    .map_err(e)?;
    // One unlock per attendance, and each entry once per character.
    sqlx::query(
        "INSERT INTO lore_unlocks (character_id, lore_id, attendance_id) VALUES ($1, 1, $2)",
    )
    .bind(character)
    .bind(attendance)
    .execute(&pool)
    .await
    .map_err(e)?;
    refused(
        sqlx::query(
            "INSERT INTO lore_unlocks (character_id, lore_id, attendance_id) VALUES ($1, 2, $2)",
        )
        .bind(character)
        .bind(attendance)
        .execute(&pool)
        .await,
        "a second unlock for one attendance",
    )?;
    refused(
        sqlx::query("UPDATE class_attendance SET fortune = 'flying' WHERE id = $1")
            .bind(attendance)
            .execute(&pool)
            .await,
        "an unknown fortune",
    )?;
    pool.close().await;
    Ok(())
}
