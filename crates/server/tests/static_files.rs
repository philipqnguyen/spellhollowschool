//! `SHS_STATIC_DIR`: the server hands out the built client next to the API,
//! with immutable caching only for content-hashed files (static_files.rs).

mod common;

use axum::body::Body;
use axum::http::{Method, Request, StatusCode, header};
use common::TestApp;

const IMMUTABLE: &str = "public, max-age=31536000, immutable";
const HASH: &str = "0123456789abcdef";

/// A throwaway client build: `index.html`, a Vite chunk, two game assets
/// (one with a gzip sibling) and the asset map naming them.
fn client_dir() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "shs-static-{}-{}",
        std::process::id(),
        rand::random::<u32>()
    ));
    std::fs::create_dir_all(dir.join("assets/world")).unwrap();
    std::fs::create_dir_all(dir.join("app")).unwrap();
    std::fs::write(
        dir.join("index.html"),
        "<!doctype html><html><head><title>game</title></head><body></body></html>",
    )
    .unwrap();
    std::fs::write(dir.join("app/index-Bx1.js"), "console.log(1)").unwrap();
    std::fs::write(dir.join("assets/app.js"), "console.log(2)").unwrap();
    std::fs::write(dir.join("assets/app.js.gz"), b"\x1f\x8b fake gzip").unwrap();
    std::fs::write(dir.join("assets/world/cell.glb"), "glTF").unwrap();
    std::fs::write(dir.join("assets/world/cell.glb.gz"), b"\x1f\x8b fake gzip").unwrap();
    std::fs::write(
        dir.join("asset-map.json"),
        format!(
            r#"{{"version":1,"hashLength":16,"sameOrigin":["assets/ktx2/"],"files":{{"assets/world/cell.glb":"{HASH}"}}}}"#
        ),
    )
    .unwrap();
    dir
}

fn cache(res: &common::Response) -> &str {
    res.headers[header::CACHE_CONTROL].to_str().unwrap()
}

#[tokio::test]
async fn serves_the_client_and_keeps_api_404s_json() {
    let dir = client_dir();
    let app = TestApp::with_config(|c| c.static_dir = Some(dir.clone())).await;

    let index = app.get("/", None).await;
    assert_eq!(index.status, StatusCode::OK);
    let html = index.body.as_str().unwrap();
    assert!(html.contains("<title>game</title>"));
    // No bucket: the page names none, and the client loads assets from here.
    assert!(!html.contains("shs-assets-cdn"));
    assert_eq!(cache(&index), "no-cache");
    assert!(
        index.headers[header::CONTENT_TYPE]
            .to_str()
            .unwrap()
            .starts_with("text/html")
    );
    assert_eq!(app.get("/index.html", None).await.body, index.body);

    let js = app.get("/assets/app.js", None).await;
    assert_eq!(js.status, StatusCode::OK);
    assert_eq!(js.body, "console.log(2)");

    let gz = app
        .send(
            Request::get("/assets/app.js")
                .header(header::ACCEPT_ENCODING, "gzip")
                .body(Body::empty())
                .unwrap(),
        )
        .await;
    assert_eq!(gz.headers[header::CONTENT_ENCODING], "gzip");

    // A missing file is a 404, not index.html.
    let missing = app.get("/assets/nope.glb", None).await;
    assert_eq!(missing.status, StatusCode::NOT_FOUND);
    assert_eq!(missing.body["error"], "not_found");
    assert_eq!(cache(&missing), "no-cache");

    // The API keeps its own routes and JSON 404s.
    assert_eq!(app.get("/api/health", None).await.status, StatusCode::OK);
    let api_missing = app.get("/api/no-such-endpoint", None).await;
    assert_eq!(api_missing.status, StatusCode::NOT_FOUND);
    assert_eq!(api_missing.body["error"], "not_found");

    std::fs::remove_dir_all(dir).ok();
}

#[tokio::test]
async fn a_matching_version_is_cached_for_a_year_and_anything_else_revalidates() {
    let dir = client_dir();
    let app = TestApp::with_config(|c| c.static_dir = Some(dir.clone())).await;

    let right = app
        .get(&format!("/assets/world/cell.glb?v={HASH}"), None)
        .await;
    assert_eq!(right.status, StatusCode::OK);
    assert_eq!(right.body, "glTF");
    assert_eq!(cache(&right), IMMUTABLE);

    // An old tab asking for the version before a deploy gets today's file,
    // which must not be cached under the old version's URL for a year.
    let wrong = app
        .get("/assets/world/cell.glb?v=ffffffffffffffff", None)
        .await;
    assert_eq!(wrong.status, StatusCode::OK);
    assert_eq!(cache(&wrong), "no-cache");
    let bare = app.get("/assets/world/cell.glb", None).await;
    assert_eq!(cache(&bare), "no-cache");
    // A file the map does not list is never immutable, whatever it asks for.
    let unlisted = app.get(&format!("/assets/app.js?v={HASH}"), None).await;
    assert_eq!(cache(&unlisted), "no-cache");
    // A 404 with a version is not cached either.
    let gone = app
        .get(&format!("/assets/world/gone.glb?v={HASH}"), None)
        .await;
    assert_eq!(gone.status, StatusCode::NOT_FOUND);
    assert_eq!(cache(&gone), "no-cache");

    // The gzip sibling keeps the caching of the file it stands for.
    let gz = app
        .send(
            Request::get(format!("/assets/world/cell.glb?v={HASH}"))
                .header(header::ACCEPT_ENCODING, "gzip")
                .body(Body::empty())
                .unwrap(),
        )
        .await;
    assert_eq!(gz.headers[header::CONTENT_ENCODING], "gzip");
    assert_eq!(cache(&gz), IMMUTABLE);

    // Vite's content-hashed output is immutable; the page never is.
    let chunk = app.get("/app/index-Bx1.js", None).await;
    assert_eq!(chunk.status, StatusCode::OK);
    assert_eq!(cache(&chunk), IMMUTABLE);
    assert_eq!(
        cache(&app.get(&format!("/?v={HASH}"), None).await),
        "no-cache"
    );

    std::fs::remove_dir_all(dir).ok();
}

#[tokio::test]
async fn with_a_bucket_the_page_names_it() {
    let dir = client_dir();
    let app = TestApp::with_config(|c| {
        c.static_dir = Some(dir.clone());
        c.assets_cdn_url = Some("https://assets.example.org".into());
    })
    .await;

    let index = app.get("/", None).await;
    assert_eq!(index.status, StatusCode::OK);
    let html = index.body.as_str().unwrap();
    assert!(
        html.contains(
            "<meta name=\"shs-assets-cdn\" content=\"https://assets.example.org\" />\n</head>"
        ),
        "{html}"
    );
    assert_eq!(cache(&index), "no-cache");
    assert_eq!(
        index.headers[header::CONTENT_LENGTH].to_str().unwrap(),
        html.len().to_string()
    );

    let head = app
        .send(
            Request::builder()
                .method(Method::HEAD)
                .uri("/")
                .body(Body::empty())
                .unwrap(),
        )
        .await;
    assert_eq!(head.status, StatusCode::OK);
    assert_eq!(
        head.headers[header::CONTENT_LENGTH].to_str().unwrap(),
        html.len().to_string()
    );

    // The server still answers for its own copies (same-origin files, old tabs).
    let cell = app
        .get(&format!("/assets/world/cell.glb?v={HASH}"), None)
        .await;
    assert_eq!(cache(&cell), IMMUTABLE);

    std::fs::remove_dir_all(dir).ok();
}

#[tokio::test]
async fn without_a_static_dir_nothing_is_served() {
    let app = TestApp::new().await;
    let index = app.get("/", None).await;
    assert_eq!(index.status, StatusCode::NOT_FOUND);
    assert_eq!(index.body["error"], "not_found");
}
