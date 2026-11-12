//! The built client (`SHS_STATIC_DIR`), served next to the API, and how long
//! browsers and caches may keep each file (docs/deploy.md, "Assets from a
//! bucket").
//!
//! - `/` and `/index.html`: the page, `no-cache`, read for every request.
//!   With `SHS_ASSETS_CDN_URL` it gains `<meta name="shs-assets-cdn">`, which
//!   tells the client to load the game assets from the bucket (client
//!   `src/assets.ts`).
//! - `/app/...`: Vite's scripts, styles and fonts, whose names carry their
//!   content hash: `public, max-age=31536000, immutable`.
//! - The game assets (`/assets/...`, `/audio/...`): the client asks for
//!   `<path>?v=<hash>`, the hash the build recorded in `asset-map.json`. When
//!   `v` matches the hash of the file this server has, it may be kept for a
//!   year (immutable); otherwise (an old tab during a deploy, no `v`) it is
//!   `no-cache`, so a stale version is never cached for long.
//! - Everything else: `no-cache` (browsers revalidate with `Last-Modified`).
//!
//! The client has no paths of its own (links carry query strings on `/`), so
//! nothing falls back to `index.html`; a missing file is the JSON 404. A
//! `.gz` sibling made at image build time goes to clients that accept gzip.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::body::Body;
use axum::extract::Request;
use axum::handler::HandlerWithoutStateExt;
use axum::http::{HeaderValue, Method, StatusCode, header};
use axum::response::{IntoResponse, Response};
use serde::Deserialize;
use tower::ServiceExt;
use tower_http::services::ServeDir;

/// The asset map's file name in the static folder (client/scripts/asset-map.mjs `MAP_FILE`).
pub const MAP_FILE: &str = "asset-map.json";
/// The meta tag the client reads (client/src/assets.ts `ASSET_CDN_META`).
pub const CDN_META: &str = "shs-assets-cdn";
/// Vite's output folder for its content-hashed files (client/vite.config.ts `build.assetsDir`).
const HASHED_DIR: &str = "/app/";

const IMMUTABLE: HeaderValue = HeaderValue::from_static("public, max-age=31536000, immutable");
const NO_CACHE: HeaderValue = HeaderValue::from_static("no-cache");

#[derive(Deserialize)]
struct MapFile {
    version: u32,
    files: HashMap<String, String>,
    #[serde(default, rename = "sameOrigin")]
    same_origin: Vec<String>,
}

/// The asset map: logical path (`assets/world/manifest.json`) to content hash.
#[derive(Debug, Default, Clone)]
pub struct AssetMap {
    files: HashMap<String, String>,
    same_origin: Vec<String>,
}

impl AssetMap {
    /// `<dir>/asset-map.json`; empty when it is missing (a client built
    /// without the map: nothing is cached for long) or unreadable (logged).
    pub fn load(dir: &Path) -> Self {
        let path = dir.join(MAP_FILE);
        let text = match std::fs::read_to_string(&path) {
            Ok(t) => t,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                tracing::info!(path = %path.display(), "no asset map: game assets are served no-cache");
                return Self::default();
            }
            Err(e) => {
                tracing::warn!(path = %path.display(), error = %e, "could not read the asset map");
                return Self::default();
            }
        };
        match Self::parse(&text) {
            Ok(map) => map,
            Err(e) => {
                tracing::warn!(path = %path.display(), error = %e, "ignoring a malformed asset map");
                Self::default()
            }
        }
    }

    pub fn parse(text: &str) -> Result<Self, String> {
        let file: MapFile = serde_json::from_str(text).map_err(|e| e.to_string())?;
        if file.version != 1 {
            return Err(format!("version {} (want 1)", file.version));
        }
        Ok(Self {
            files: file.files,
            same_origin: file.same_origin,
        })
    }

    pub fn len(&self) -> usize {
        self.files.len()
    }

    pub fn is_empty(&self) -> bool {
        self.files.is_empty()
    }

    /// The hash the build recorded for `path` (logical, no leading slash).
    pub fn hash(&self, path: &str) -> Option<&str> {
        self.files.get(path).map(String::as_str)
    }

    /// The files the client loads from a bucket when there is one (all but
    /// the same-origin ones) that `dir` does not have.
    fn missing_bucket_files(&self, dir: &Path) -> usize {
        self.files
            .keys()
            .filter(|p| !self.same_origin.iter().any(|s| p.starts_with(s.as_str())))
            .filter(|p| !dir.join(p).is_file())
            .count()
    }
}

/// The built client's files with their caching rules (see the module docs).
#[derive(Clone)]
pub struct StaticFiles {
    dir: PathBuf,
    map: Arc<AssetMap>,
    cdn: Option<Arc<str>>,
}

impl StaticFiles {
    pub fn new(dir: PathBuf, cdn: Option<String>) -> Self {
        let map = AssetMap::load(&dir);
        if !map.is_empty() {
            let missing = map.missing_bucket_files(&dir);
            match (&cdn, missing) {
                (None, n) if n > 0 => tracing::warn!(
                    missing = n,
                    "{n} of the client's {} assets are not in SHS_STATIC_DIR and SHS_ASSETS_CDN_URL is not set: \
                     the client will draw stand-ins for them (an image built for an asset bucket needs SHS_ASSETS_CDN_URL)",
                    map.len()
                ),
                (Some(cdn), _) => {
                    tracing::info!(%cdn, assets = map.len(), "game assets load from the asset bucket")
                }
                _ => tracing::info!(assets = map.len(), "game assets load from this server"),
            }
        }
        Self {
            dir,
            map: Arc::new(map),
            cdn: cdn.map(Arc::from),
        }
    }

    /// Answers one request for a file (the router's fallback).
    pub async fn serve(self, req: Request) -> Response {
        let path = req.uri().path();
        let is_page = path == "/" || path == "/index.html";
        if is_page && (req.method() == Method::GET || req.method() == Method::HEAD) {
            return self.index(req.method() == Method::HEAD).await;
        }
        let cache = self.cache_for(path, req.uri().query());
        let serve = ServeDir::new(&self.dir)
            .precompressed_gzip()
            .not_found_service(crate::http::not_found.into_service());
        // ServeDir's error type is Infallible.
        let mut res = match serve.oneshot(req).await {
            Ok(res) => res.map(Body::new),
            Err(e) => match e {},
        };
        let ok = res.status().is_success() || res.status() == StatusCode::NOT_MODIFIED;
        res.headers_mut().insert(
            header::CACHE_CONTROL,
            if ok && cache { IMMUTABLE } else { NO_CACHE },
        );
        res
    }

    /// Whether `path` with `query` may be cached for a year.
    fn cache_for(&self, path: &str, query: Option<&str>) -> bool {
        if path.starts_with(HASHED_DIR) {
            return true;
        }
        let Some(want) = self.map.hash(path.trim_start_matches('/')) else {
            return false;
        };
        query
            .unwrap_or("")
            .split('&')
            .filter_map(|kv| kv.strip_prefix("v="))
            .any(|v| v == want)
    }

    async fn index(&self, head: bool) -> Response {
        let html = match tokio::fs::read_to_string(self.dir.join("index.html")).await {
            Ok(html) => html,
            Err(_) => return crate::http::not_found().await.into_response(),
        };
        let html = match &self.cdn {
            Some(cdn) => with_cdn_meta(&html, cdn),
            None => html,
        };
        let len = html.len();
        let body = if head {
            Body::empty()
        } else {
            Body::from(html)
        };
        let mut res = Response::new(body);
        let h = res.headers_mut();
        h.insert(
            header::CONTENT_TYPE,
            HeaderValue::from_static("text/html; charset=utf-8"),
        );
        h.insert(header::CACHE_CONTROL, NO_CACHE);
        h.insert(header::CONTENT_LENGTH, HeaderValue::from(len));
        res
    }
}

/// `html` with `<meta name="shs-assets-cdn" content="<cdn>">` at the end of its head.
pub fn with_cdn_meta(html: &str, cdn: &str) -> String {
    let escaped = cdn
        .replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('<', "&lt;")
        .replace('>', "&gt;");
    let tag = format!("<meta name=\"{CDN_META}\" content=\"{escaped}\" />\n");
    match html.to_ascii_lowercase().find("</head>") {
        Some(at) => format!("{}{tag}{}", &html[..at], &html[at..]),
        None => format!("{tag}{html}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn meta_goes_at_the_end_of_the_head_escaped() {
        let html = "<!doctype html><html><HEAD><title>x</title></HEAD><body></body></html>";
        let out = with_cdn_meta(html, "https://a.example.org/x?a=1&b=\"2\"");
        assert!(out.contains(
            "<meta name=\"shs-assets-cdn\" content=\"https://a.example.org/x?a=1&amp;b=&quot;2&quot;\" />\n</HEAD>"
        ));
        assert!(with_cdn_meta("<p>no head</p>", "https://a").starts_with("<meta"));
    }

    #[test]
    fn parses_the_map_and_rejects_other_versions() {
        let map = AssetMap::parse(
            r#"{"version":1,"hashLength":16,"sameOrigin":["assets/ktx2/"],"files":{"assets/a.glb":"0123456789abcdef"}}"#,
        )
        .unwrap();
        assert_eq!(map.hash("assets/a.glb"), Some("0123456789abcdef"));
        assert_eq!(map.hash("assets/b.glb"), None);
        assert!(AssetMap::parse(r#"{"version":2,"files":{}}"#).is_err());
        assert!(AssetMap::parse("not json").is_err());
    }
}
