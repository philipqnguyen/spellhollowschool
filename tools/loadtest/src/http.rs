//! Minimal HTTP/1.1 client for the endpoints the load test calls:
//! `POST /api/dev/bot-session`, `GET /api/health`, `GET /metrics`, and the
//! wardrobe calls that dress each bot (`/api/characters/me`, `/api/inventory`,
//! `/api/equipment`, `/api/items/{id}/dye`, with a bearer token).
//!
//! One `Connection: close` request per TCP connection, which is all a bot
//! needs (it asks for a session once) and keeps this tiny: the response is
//! read to the end and its body taken by `Content-Length`, chunked encoding
//! or end of stream.

use std::time::Duration;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

/// A request that takes longer than this has failed.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);
/// Larger responses are refused (`/metrics` is a few tens of KB).
const MAX_RESPONSE: usize = 8 << 20;

/// The server under test: `http://host:port`.
#[derive(Debug, Clone)]
pub struct Target {
    /// `host:port`, used both to connect and as the `Host` header.
    pub authority: String,
}

/// Status code and body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Response {
    pub status: u16,
    pub body: Vec<u8>,
}

impl Response {
    pub fn is_success(&self) -> bool {
        (200..300).contains(&self.status)
    }
}

impl Target {
    /// Parses `http://host[:port]` (a trailing slash is fine; HTTPS is not supported).
    pub fn parse(url: &str) -> Result<Target, String> {
        let rest = url
            .strip_prefix("http://")
            .ok_or_else(|| format!("--url must start with http:// (got {url:?})"))?;
        let host = rest.trim_end_matches('/');
        if host.is_empty() || host.contains('/') {
            return Err(format!("--url must be http://host[:port] (got {url:?})"));
        }
        let authority = if host.contains(':') {
            host.to_owned()
        } else {
            format!("{host}:80")
        };
        Ok(Target { authority })
    }

    /// The WebSocket endpoint.
    pub fn ws_url(&self) -> String {
        format!("ws://{}/ws", self.authority)
    }

    pub async fn get(&self, path: &str) -> Result<Response, String> {
        self.request("GET", path, None, None).await
    }

    pub async fn post_json(
        &self,
        path: &str,
        body: &serde_json::Value,
    ) -> Result<Response, String> {
        self.request("POST", path, Some(body.to_string()), None)
            .await
    }

    /// `GET` as the player whose session `token` is.
    pub async fn get_as(&self, token: &str, path: &str) -> Result<Response, String> {
        self.request("GET", path, None, Some(token)).await
    }

    /// `POST` with a JSON body as the player whose session `token` is.
    pub async fn post_json_as(
        &self,
        token: &str,
        path: &str,
        body: &serde_json::Value,
    ) -> Result<Response, String> {
        self.request("POST", path, Some(body.to_string()), Some(token))
            .await
    }

    async fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<String>,
        token: Option<&str>,
    ) -> Result<Response, String> {
        let work = async {
            let mut stream = TcpStream::connect(&self.authority)
                .await
                .map_err(|e| format!("connect {}: {e}", self.authority))?;
            stream.set_nodelay(true).ok();
            let mut req = format!(
                "{method} {path} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n",
                self.authority
            );
            if let Some(t) = token {
                req += &format!("Authorization: Bearer {t}\r\n");
            }
            if let Some(b) = &body {
                req += &format!(
                    "Content-Type: application/json\r\nContent-Length: {}\r\n",
                    b.len()
                );
            }
            req += "\r\n";
            req += body.as_deref().unwrap_or_default();
            stream
                .write_all(req.as_bytes())
                .await
                .map_err(|e| format!("{path}: write: {e}"))?;
            let mut raw = Vec::new();
            let mut buf = [0u8; 16 * 1024];
            loop {
                let n = stream
                    .read(&mut buf)
                    .await
                    .map_err(|e| format!("{path}: read: {e}"))?;
                if n == 0 {
                    break;
                }
                raw.extend_from_slice(&buf[..n]);
                if raw.len() > MAX_RESPONSE {
                    return Err(format!("{path}: response too large"));
                }
            }
            parse_response(&raw).map_err(|e| format!("{path}: {e}"))
        };
        tokio::time::timeout(REQUEST_TIMEOUT, work)
            .await
            .map_err(|_| format!("{path}: timed out"))?
    }
}

/// Parses a complete HTTP/1.1 response.
fn parse_response(raw: &[u8]) -> Result<Response, String> {
    let split = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("incomplete response head")?;
    let head = std::str::from_utf8(&raw[..split]).map_err(|_| "response head is not UTF-8")?;
    let rest = &raw[split + 4..];
    let mut lines = head.split("\r\n");
    let status_line = lines.next().unwrap_or_default();
    let status: u16 = status_line
        .split(' ')
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| format!("bad status line {status_line:?}"))?;
    let mut length = None;
    let mut chunked = false;
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        if name.eq_ignore_ascii_case("content-length") {
            length = Some(value.parse::<usize>().map_err(|_| "bad Content-Length")?);
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            chunked = value.to_ascii_lowercase().contains("chunked");
        }
    }
    let body = if chunked {
        dechunk(rest)?
    } else if let Some(n) = length {
        rest.get(..n)
            .ok_or("body shorter than Content-Length")?
            .to_vec()
    } else {
        rest.to_vec()
    };
    Ok(Response { status, body })
}

/// Decodes a chunked body (trailers are ignored).
fn dechunk(mut data: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let eol = data
            .windows(2)
            .position(|w| w == b"\r\n")
            .ok_or("truncated chunk size")?;
        let size_text = std::str::from_utf8(&data[..eol]).map_err(|_| "bad chunk size")?;
        let size_hex = size_text.split(';').next().unwrap_or_default().trim();
        let size = usize::from_str_radix(size_hex, 16).map_err(|_| "bad chunk size")?;
        data = &data[eol + 2..];
        if size == 0 {
            return Ok(out);
        }
        let chunk = data.get(..size).ok_or("truncated chunk")?;
        out.extend_from_slice(chunk);
        data = data.get(size + 2..).ok_or("truncated chunk")?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_urls() {
        let t = Target::parse("http://127.0.0.1:8080/").unwrap();
        assert_eq!(t.authority, "127.0.0.1:8080");
        assert_eq!(t.ws_url(), "ws://127.0.0.1:8080/ws");
        assert_eq!(Target::parse("http://game").unwrap().authority, "game:80");
        assert!(Target::parse("https://game").is_err());
        assert!(Target::parse("http://game/path").is_err());
    }

    #[test]
    fn parses_length_delimited_responses() {
        let r = parse_response(
            b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 13\r\n\r\n{\"token\":\"x\"}",
        )
        .unwrap();
        assert_eq!(r.status, 200);
        assert!(r.is_success());
        assert_eq!(r.body, b"{\"token\":\"x\"}");
        let r = parse_response(b"HTTP/1.1 404 Not Found\r\n\r\nnope").unwrap();
        assert_eq!((r.status, r.body.as_slice()), (404, &b"nope"[..]));
        assert!(!r.is_success());
    }

    #[test]
    fn parses_chunked_responses() {
        let r = parse_response(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n7;ext=1\r\n, world\r\n0\r\n\r\n",
        )
        .unwrap();
        assert_eq!(r.body, b"hello, world");
        assert!(
            parse_response(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhel")
                .is_err()
        );
        assert!(parse_response(b"HTTP/1.1 200 OK\r\n").is_err());
    }

    /// Against a real (tiny) server on a local port.
    #[tokio::test]
    async fn round_trips_over_tcp() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let (mut s, _) = listener.accept().await.unwrap();
            let mut buf = vec![0u8; 4096];
            let n = s.read(&mut buf).await.unwrap();
            let req = String::from_utf8_lossy(&buf[..n]).into_owned();
            assert!(req.starts_with("POST /api/dev/bot-session HTTP/1.1\r\n"));
            assert!(req.ends_with("{\"name\":\"lt0001\"}"));
            s.write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\n{}")
                .await
                .unwrap();
        });
        let t = Target::parse(&format!("http://{addr}")).unwrap();
        let r = t
            .post_json(
                "/api/dev/bot-session",
                &serde_json::json!({ "name": "lt0001" }),
            )
            .await
            .unwrap();
        assert_eq!((r.status, r.body.as_slice()), (200, &b"{}"[..]));
    }
}
