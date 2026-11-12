//! Query plans of the Owl Exchange on a seeded table: 100,000 active
//! listings of 200 sellers (plus 20,000 closed ones) spread over the whole
//! catalog, every browse shape built by the server's own
//! `exchange::search::build`, `EXPLAIN (ANALYZE, BUFFERS)`, inside a
//! transaction that is rolled back.
//!
//! Ignored by default (it takes a while and prints the plans):
//!
//! ```text
//! DATABASE_URL=postgres://localhost/shs_test \
//!   cargo test -p server --test exchange_plans -- --ignored --nocapture
//! ```
//!
//! It checks that every page is an index scan of `exchange_listings` (no
//! sequential scan) taking under 20 ms, apart from the one shape no index
//! can serve (filters that match almost nothing, where the planner rightly
//! scans), which must stay within 100 ms.

mod common;

use common::TestApp;
use server::catalog::shipped;
use server::exchange::search::{self, Cursor, Search, Sort};
use sqlx::PgConnection;

const ACTIVE: i32 = 100_000;
const CLOSED: i32 = 20_000;
/// The page budget.
const PAGE_MS: f64 = 20.0;

/// Characters selling on the seeded Exchange.
const SELLERS: i32 = 200;

/// Seeds the listings (sellers, items, active and closed listings) and
/// analyzes the table. Returns the sellers.
async fn seed(conn: &mut PgConnection, tag: &str) -> Vec<i64> {
    let sellers: Vec<i64> = sqlx::query_scalar(
        "WITH a AS (
             INSERT INTO accounts (email, password_hash, adult_attested_at, email_verified_at)
             SELECT 'seed-' || g || '-' || $1 || '@example.org', '!', now(), now()
             FROM generate_series(1, $2) g
             RETURNING id
         )
         INSERT INTO characters (account_id, name, name_key, house, starter_granted_at)
         SELECT id, n, lower(n), id % 4, now()
         FROM (SELECT id, 'Seller' || translate(id::text, '0123456789', 'abcdefghij') AS n FROM a) s
         RETURNING id",
    )
    .bind(tag)
    .bind(SELLERS)
    .fetch_all(&mut *conn)
    .await
    .unwrap();
    let items = &shipped().catalog.items;
    let defs: Vec<i32> = items.iter().map(|d| i32::from(d.id)).collect();
    let cats: Vec<&str> = items.iter().map(|d| d.exchange.category.as_str()).collect();
    let subs: Vec<&str> = items
        .iter()
        .map(|d| d.exchange.subcategory.as_str())
        .collect();
    let levels: Vec<i16> = items.iter().map(|d| i16::from(d.required_level)).collect();
    let houses: Vec<Option<&str>> = items.iter().map(|d| d.house_theme.as_deref()).collect();
    let n = i32::try_from(items.len()).unwrap();
    sqlx::query(
        "WITH defs AS (
             SELECT * FROM unnest($2::int[], $3::text[], $4::text[], $5::smallint[], $6::text[])
                 WITH ORDINALITY AS d(def, cat, sub, lvl, house, n)
         ), made AS (
             INSERT INTO items (owner_id, item_def, dye_primary, dye_secondary, dye_trim, location)
             SELECT ($1::bigint[])[1 + (g * 31) % cardinality($1::bigint[])], d.def, (g * 7) % 48, (g * 11) % 48,
                    (g * 13) % 48, 'exchange'
             FROM generate_series(1, $7) g JOIN defs d ON d.n = 1 + (g % $8)
             RETURNING id, owner_id, item_def, dye_primary, dye_secondary, dye_trim
         )
         INSERT INTO exchange_listings
             (seller_id, item_id, item_def, quantity, price, fee, category, subcategory,
              required_level, colour, dye_secondary, dye_trim, house_theme, created_at, expires_at)
         SELECT m.owner_id, m.id, m.item_def, 1, 1 + (m.id * 7919) % 5000, 3, d.cat, d.sub, d.lvl,
                m.dye_primary, m.dye_secondary, m.dye_trim, d.house,
                now() - (m.id % 400000) * interval '1 second',
                now() + interval '5 days' - (m.id % 400000) * interval '1 second'
         FROM made m JOIN defs d ON d.def = m.item_def",
    )
    .bind(&sellers)
    .bind(&defs)
    .bind(&cats)
    .bind(&subs)
    .bind(&levels)
    .bind(&houses)
    .bind(ACTIVE)
    .bind(n)
    .execute(&mut *conn)
    .await
    .unwrap();
    // History: sold listings no longer hold an item.
    sqlx::query(
        "INSERT INTO exchange_listings
             (seller_id, item_id, item_def, quantity, price, fee, category, subcategory,
              colour, state, created_at, expires_at, closed_at, tax)
         SELECT ($1::bigint[])[1 + g % cardinality($1::bigint[])], NULL, 3001, 1, 10, 3, 'materials', 'fibers', 0,
                'sold', now() - interval '9 days', now() - interval '4 days', now(), 0
         FROM generate_series(1, $2) g",
    )
    .bind(&sellers)
    .bind(CLOSED)
    .execute(&mut *conn)
    .await
    .unwrap();
    for table in ["exchange_listings", "characters"] {
        sqlx::query(&format!("ANALYZE {table}"))
            .execute(&mut *conn)
            .await
            .unwrap();
    }
    sellers
}

/// The `(price, id)` of the active listing half-way through `order`.
async fn middle(conn: &mut PgConnection, filter: &str, order: &str) -> (i64, i64) {
    sqlx::query_as(&format!(
        "SELECT price, id FROM exchange_listings x WHERE x.state = 'active' {filter}
         ORDER BY {order} OFFSET (SELECT count(*) / 2 FROM exchange_listings x
                                  WHERE x.state = 'active' {filter}) LIMIT 1"
    ))
    .fetch_one(conn)
    .await
    .unwrap()
}

/// Runs `EXPLAIN (ANALYZE, BUFFERS)` of `sql` / `search` and returns the
/// plan text and its execution time (ms).
async fn explain_search(conn: &mut PgConnection, s: &Search) -> (String, f64) {
    let rows: Vec<(String,)> = search::build(s, "EXPLAIN (ANALYZE, BUFFERS) ")
        .build_query_as()
        .fetch_all(conn)
        .await
        .unwrap();
    plan(rows)
}

async fn explain_sql(conn: &mut PgConnection, sql: &str, seller: i64) -> (String, f64) {
    let rows: Vec<(String,)> = sqlx::query_as(&format!("EXPLAIN (ANALYZE, BUFFERS) {sql}"))
        .bind(seller)
        .fetch_all(conn)
        .await
        .unwrap();
    plan(rows)
}

fn plan(rows: Vec<(String,)>) -> (String, f64) {
    let text: Vec<String> = rows.into_iter().map(|r| r.0).collect();
    let ms = text
        .iter()
        .find_map(|l| l.trim().strip_prefix("Execution Time: "))
        .and_then(|t| t.trim_end_matches(" ms").parse().ok())
        .expect("EXPLAIN ANALYZE reports an execution time");
    (text.join("\n"), ms)
}

fn base(sort: Sort) -> Search {
    Search {
        sort,
        fetch: 26,
        ..Search::default()
    }
}

fn names(q: &str) -> Option<Vec<i32>> {
    Some(
        shipped()
            .catalog
            .search_names(q)
            .into_iter()
            .map(i32::from)
            .collect(),
    )
}

#[tokio::test]
#[ignore = "seeds 120,000 listings and prints their plans; run with --ignored --nocapture"]
async fn every_browse_page_is_an_index_scan_on_100k_listings() {
    let app = TestApp::new().await;
    let seller = app.player("Roarwing").await;
    let mut tx = app.state.pool.begin().await.unwrap();
    let started = std::time::Instant::now();
    let sellers = seed(&mut tx, &seller.name.to_lowercase()).await;
    println!(
        "seeded {ACTIVE} active + {CLOSED} closed listings of {SELLERS} sellers in {:?}",
        started.elapsed()
    );

    let cursor = |sort, (price, id)| Some(Cursor { sort, price, id });
    let mid_price = middle(&mut tx, "", "price, id").await;
    let mid_new = middle(&mut tx, "", "id DESC").await;
    let mid_body = middle(&mut tx, "AND x.category = 'body'", "price, id").await;
    let mid_fibers = middle(
        &mut tx,
        "AND x.category = 'materials' AND x.subcategory = 'fibers'",
        "id DESC",
    )
    .await;
    let shapes: Vec<(&str, Search, bool)> = vec![
        ("newest, everything", base(Sort::Newest), true),
        (
            "newest, a middle page",
            Search {
                after: cursor(Sort::Newest, mid_new),
                ..base(Sort::Newest)
            },
            true,
        ),
        ("cheapest, everything", base(Sort::Price), true),
        (
            "cheapest, a middle page",
            Search {
                after: cursor(Sort::Price, mid_price),
                ..base(Sort::Price)
            },
            true,
        ),
        (
            "dearest, a middle page",
            Search {
                after: cursor(Sort::PriceDesc, mid_price),
                ..base(Sort::PriceDesc)
            },
            true,
        ),
        (
            "body, newest",
            Search {
                category: Some("body".into()),
                ..base(Sort::Newest)
            },
            true,
        ),
        (
            "body, cheapest, a middle page",
            Search {
                category: Some("body".into()),
                after: cursor(Sort::Price, mid_body),
                ..base(Sort::Price)
            },
            true,
        ),
        (
            "materials/fibers, cheapest",
            Search {
                category: Some("materials".into()),
                subcategory: Some("fibers".into()),
                ..base(Sort::Price)
            },
            true,
        ),
        (
            "materials/fibers, newest, a middle page",
            Search {
                category: Some("materials".into()),
                subcategory: Some("fibers".into()),
                after: cursor(Sort::Newest, mid_fibers),
                ..base(Sort::Newest)
            },
            true,
        ),
        (
            "name 'wool', cheapest",
            Search {
                item_defs: names("wool"),
                ..base(Sort::Price)
            },
            true,
        ),
        (
            "name 'hat' (many items), newest",
            Search {
                item_defs: names("hat"),
                ..base(Sort::Newest)
            },
            true,
        ),
        (
            "hats + colour + level 2-5, cheapest",
            Search {
                category: Some("hats".into()),
                colour: Some(5),
                min_level: Some(2),
                max_level: Some(5),
                ..base(Sort::Price)
            },
            true,
        ),
        (
            "house roarwing, newest",
            Search {
                house: Some("roarwing".into()),
                ..base(Sort::Newest)
            },
            true,
        ),
        (
            "colour 30, dearest",
            Search {
                colour: Some(30),
                ..base(Sort::PriceDesc)
            },
            true,
        ),
        (
            "worst case: filters matching nothing (walks the whole index)",
            Search {
                house: Some("serpentyne".into()),
                min_level: Some(20),
                ..base(Sort::Newest)
            },
            false,
        ),
    ];
    let mut report = Vec::new();
    for (name, s, indexed) in &shapes {
        let (text, ms) = explain_search(&mut tx, s).await;
        println!("\n=== {name}: {ms:.3} ms\n{text}");
        if *indexed {
            assert!(
                !text.contains("Seq Scan on exchange_listings"),
                "{name} scans the table:\n{text}"
            );
            assert!(text.contains("Index"), "{name}:\n{text}");
            assert!(ms < PAGE_MS, "{name} took {ms} ms:\n{text}");
        } else {
            // No index serves it: the planner may scan (in parallel), which
            // is the cheapest way to find nothing. It must stay bounded.
            assert!(ms < 5.0 * PAGE_MS, "{name} took {ms} ms:\n{text}");
        }
        report.push((name.to_string(), ms));
    }
    // The other Exchange queries: the expiry job, a seller's own list and
    // the listing limit's count.
    for (name, sql) in [
        (
            "expiry job batch",
            "SELECT id, seller_id FROM exchange_listings
             WHERE state = 'active' AND expires_at <= now() + interval '5 days' - interval '3 days'
               AND $1::bigint IS NOT NULL
             ORDER BY expires_at LIMIT 200",
        ),
        (
            "mine, newest (a seller with 600 listings)",
            "SELECT x.id FROM exchange_listings x WHERE x.seller_id = $1
             ORDER BY x.id DESC LIMIT 26",
        ),
        (
            "listing limit count",
            "SELECT count(*) FROM exchange_listings WHERE seller_id = $1 AND state = 'active'",
        ),
    ] {
        let (text, ms) = explain_sql(&mut tx, sql, sellers[0]).await;
        println!("\n=== {name}: {ms:.3} ms\n{text}");
        assert!(
            !text.contains("Seq Scan on exchange_listings"),
            "{name}:\n{text}"
        );
        report.push((name.to_string(), ms));
    }
    println!("\nsummary (execution time per page):");
    for (name, ms) in &report {
        println!("  {ms:>8.3} ms  {name}");
    }
    tx.rollback().await.unwrap();
}
