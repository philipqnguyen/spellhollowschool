//! Error tracking and performance (monitoring.rs) on the real router: the
//! Good Sorcery client, turned on with a dummy key, reports to a fake ingest
//! server on localhost. API requests are timed under their route pattern
//! (with the `/api` prefix); the health check and metrics are not; the
//! client's files, unknown paths and the API's JSON 404 never become
//! transactions; an internal error is reported once, with its cause.
//!
//! Its own test binary: the client is process-wide. The other tests never
//! start it (`shs-server` does, in `main`).

mod common;

use std::io::Read;
use std::sync::{Arc, Mutex};

use axum::Router;
use axum::body::Bytes;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode, Uri};
use common::TestApp;
use serde_json::Value;

/// What the fake ingest server received: (path, JSON body).
#[derive(Clone, Default)]
struct Received(Arc<Mutex<Vec<(String, Value)>>>);

async fn ingest(
    State(received): State<Received>,
    uri: Uri,
    headers: HeaderMap,
    body: Bytes,
) -> StatusCode {
    let gzipped = headers
        .get("content-encoding")
        .is_some_and(|v| v.as_bytes() == b"gzip");
    let raw = if gzipped {
        let mut out = Vec::new();
        flate2::read::GzDecoder::new(&body[..])
            .read_to_end(&mut out)
            .expect("gzip body");
        out
    } else {
        body.to_vec()
    };
    let json = serde_json::from_slice(&raw).unwrap_or(Value::Null);
    received
        .0
        .lock()
        .unwrap()
        .push((uri.path().to_string(), json));
    StatusCode::ACCEPTED
}

/// A throwaway client build for `SHS_STATIC_DIR`.
fn client_dir() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "shs-monitoring-{}-{}",
        std::process::id(),
        rand::random::<u32>()
    ));
    std::fs::create_dir_all(dir.join("assets")).unwrap();
    std::fs::write(dir.join("index.html"), "<!doctype html><title>game</title>").unwrap();
    std::fs::write(dir.join("assets/app-0123456789abcdef.js"), "console.log(1)").unwrap();
    dir
}

/// Every string under `key` anywhere in `value`.
fn strings_under(value: &Value, key: &str, out: &mut Vec<String>) {
    match value {
        Value::Object(map) => {
            for (k, v) in map {
                if k == key
                    && let Some(s) = v.as_str()
                {
                    out.push(s.to_string());
                }
                strings_under(v, key, out);
            }
        }
        Value::Array(items) => items.iter().for_each(|v| strings_under(v, key, out)),
        _ => {}
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn reports_api_routes_by_pattern_and_internal_errors_once() {
    let received = Received::default();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let fake = Router::new().fallback(ingest).with_state(received.clone());
    tokio::spawn(async move { axum::serve(listener, fake).await.unwrap() });

    let guard = goodsorcery::init(
        server::monitoring::config()
            .api_key("gs_test_monitoring")
            .endpoint(endpoint)
            .enabled(true)
            .performance_monitoring(true),
    );
    assert!(guard.is_enabled());
    // shs-server's subscriber: sqlx's statement events reach the client.
    server::monitoring::init_tracing();

    let dir = client_dir();
    let app = TestApp::with_config(|c| c.static_dir = Some(dir.clone())).await;
    let (_, token) = app.signed_in().await;

    assert_eq!(app.get("/api/health", None).await.status, StatusCode::OK);
    assert_eq!(app.get("/metrics", None).await.status, StatusCode::OK);
    // A route with a parameter, signed in (the account goes into the scope).
    let card = app
        .get("/api/players/Nobody%20Here/card", Some(&token))
        .await;
    assert!(card.status.is_client_error(), "{:?}", card.status);
    assert_eq!(
        app.get("/api/characters", None).await.status,
        StatusCode::UNAUTHORIZED
    );
    // The client's files and paths no route matches.
    let asset = app.get("/assets/app-0123456789abcdef.js", None).await;
    assert_eq!(asset.status, StatusCode::OK);
    app.get("/castle/somewhere-1234", None).await;
    let unknown = app.get("/api/no-such-endpoint/5521", None).await;
    assert_eq!(unknown.status, StatusCode::NOT_FOUND);

    // The database goes away: the session lookup fails with a sqlx error.
    app.state.pool.close().await;
    let broken = app.get("/api/characters", Some(&token)).await;
    assert_eq!(broken.status, StatusCode::INTERNAL_SERVER_ERROR);

    // Dropping the guard sends the queued events and the open minute's
    // metrics (the client's threads block, so off the runtime's workers).
    tokio::task::spawn_blocking(move || drop(guard))
        .await
        .unwrap();
    std::fs::remove_dir_all(&dir).ok();

    let received = received.0.lock().unwrap().clone();
    let bodies = |path: &str| -> Vec<Value> {
        received
            .iter()
            .filter(|(p, _)| p == path)
            .map(|(_, body)| body.clone())
            .collect()
    };

    // One event for the 500: the sqlx error (class and message), named after
    // the route; no second "HTTP 500" event for the same request.
    let events: Vec<Value> = bodies("/api/v1/events/batch")
        .iter()
        .chain(bodies("/api/v1/events").iter())
        .flat_map(|b| match b.get("events") {
            Some(Value::Array(events)) => events.clone(),
            _ => vec![b.clone()],
        })
        .collect();
    assert_eq!(events.len(), 1, "{events:#?}");
    let event = &events[0];
    let class = event["exception"]["class"].as_str().unwrap_or_default();
    assert!(class.contains("sqlx"), "{event:#?}");
    let mut transactions = Vec::new();
    strings_under(event, "transaction", &mut transactions);
    assert!(
        transactions.iter().any(|t| t == "GET /api/characters"),
        "{event:#?}"
    );

    // The minute's metrics: API routes by pattern, nothing else.
    let metrics = bodies("/api/v1/apm/metrics");
    assert!(!metrics.is_empty(), "no metrics: {received:#?}");
    let mut names = Vec::new();
    for body in &metrics {
        strings_under(body, "name", &mut names);
    }
    names.sort();
    names.dedup();
    assert!(
        names.contains(&"GET /api/players/{name}/card".to_string()),
        "{names:?}"
    );
    assert!(
        names.contains(&"GET /api/characters".to_string()),
        "{names:?}"
    );
    for name in &names {
        assert!(
            !name.contains("health")
                && !name.contains("metrics")
                && !name.contains("assets")
                && !name.contains("castle")
                && !name.contains("no-such-endpoint")
                && !name.contains("5521"),
            "{name} should not be a transaction: {names:?}"
        );
    }

    // The slowest request per route comes with a trace; sqlx's statement
    // events time the database in it.
    let mut sql = Vec::new();
    for body in bodies("/api/v1/apm/traces") {
        strings_under(&body, "sql", &mut sql);
    }
    assert!(
        sql.iter().any(|s| s.contains("FROM sessions")),
        "no timed session lookup: {sql:?}"
    );
}
