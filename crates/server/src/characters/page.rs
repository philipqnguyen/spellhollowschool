//! Keyset pagination for every list endpoint (docs/architecture.md, "HTTP API"):
//! `?limit=25&after=<cursor>` → `{ items, next }`.
//!
//! A cursor is the sort key of the last item on the previous page, serialized
//! as JSON and base64url-encoded so clients treat it as an opaque string. Each
//! endpoint picks its key type (an item id, a `(name_key, id)` pair, ...) and
//! queries `WHERE key > / < cursor ORDER BY key LIMIT limit + 1`; the extra row
//! only tells [`Page::from_rows`] whether there is a next page.

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};

use crate::http::ApiError;

/// Page size when `limit` is absent.
pub const DEFAULT_LIMIT: u32 = 25;
/// Largest page; larger `limit`s are capped to it.
pub const MAX_LIMIT: u32 = 100;
/// Cursors are short (a key or two); anything longer is not one of ours.
const MAX_CURSOR_LEN: usize = 512;

/// The pagination half of a list endpoint's query string. Extract it with
/// `ApiQuery<PageQuery>` next to the endpoint's own filter struct (both read
/// the same query string; unknown keys are ignored).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct PageQuery {
    pub limit: Option<u32>,
    pub after: Option<String>,
}

impl PageQuery {
    /// The page size: [`DEFAULT_LIMIT`] when absent, at most [`MAX_LIMIT`];
    /// `limit=0` is refused (`400 invalid_query`).
    pub fn limit(&self) -> Result<u32, ApiError> {
        match self.limit {
            None => Ok(DEFAULT_LIMIT),
            Some(0) => Err(ApiError::bad_request(
                "invalid_query",
                "limit must be at least 1.",
            )),
            Some(n) => Ok(n.min(MAX_LIMIT)),
        }
    }

    /// `LIMIT` for the query: one more than the page, to learn whether a
    /// next page exists.
    pub fn fetch_limit(&self) -> Result<i64, ApiError> {
        Ok(i64::from(self.limit()?) + 1)
    }

    /// The decoded `after` cursor; `None` on the first page (absent or empty
    /// `after`). A cursor that is not one of this endpoint's is
    /// `400 invalid_cursor`.
    pub fn after<K: DeserializeOwned>(&self) -> Result<Option<K>, ApiError> {
        match self.after.as_deref() {
            None | Some("") => Ok(None),
            Some(s) => decode_cursor(s).map(Some).ok_or_else(|| {
                ApiError::bad_request(
                    "invalid_cursor",
                    "That page cursor is not valid. Start again from the first page.",
                )
            }),
        }
    }
}

/// One page of a list: `{ items, next }`, `next` being the cursor to pass as
/// `after` for the following page, or `null` on the last one.
#[derive(Debug, Clone, Serialize)]
pub struct Page<T> {
    pub items: Vec<T>,
    pub next: Option<String>,
}

impl<T> Page<T> {
    /// Builds a page from rows fetched with [`PageQuery::fetch_limit`]: if
    /// there are more than `limit`, the surplus row is dropped and `next` is
    /// the key of the last row kept.
    pub fn from_rows<K: Serialize>(mut rows: Vec<T>, limit: u32, key: impl Fn(&T) -> K) -> Self {
        let limit = limit as usize;
        let next = if rows.len() > limit {
            rows.truncate(limit);
            rows.last().map(|last| encode_cursor(&key(last)))
        } else {
            None
        };
        Page { items: rows, next }
    }

    /// The same page with each item converted (rows to their API views).
    pub fn map<U>(self, f: impl FnMut(T) -> U) -> Page<U> {
        Page {
            items: self.items.into_iter().map(f).collect(),
            next: self.next,
        }
    }
}

/// Encodes a sort key as an opaque cursor.
pub fn encode_cursor<K: Serialize>(key: &K) -> String {
    let json = serde_json::to_vec(key).expect("cursor keys serialize");
    URL_SAFE_NO_PAD.encode(json)
}

/// Decodes a cursor made by [`encode_cursor`] with the same key type.
pub fn decode_cursor<K: DeserializeOwned>(cursor: &str) -> Option<K> {
    if cursor.len() > MAX_CURSOR_LEN {
        return None;
    }
    let json = URL_SAFE_NO_PAD.decode(cursor).ok()?;
    serde_json::from_slice(&json).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn query(limit: Option<u32>, after: Option<&str>) -> PageQuery {
        PageQuery {
            limit,
            after: after.map(str::to_owned),
        }
    }

    #[test]
    fn limits_default_cap_and_refuse_zero() {
        assert_eq!(query(None, None).limit().unwrap(), DEFAULT_LIMIT);
        assert_eq!(query(Some(7), None).limit().unwrap(), 7);
        assert_eq!(query(Some(5000), None).limit().unwrap(), MAX_LIMIT);
        assert_eq!(query(Some(7), None).fetch_limit().unwrap(), 8);
        assert_eq!(
            query(Some(0), None).limit().unwrap_err().code,
            "invalid_query"
        );
    }

    #[test]
    fn cursors_round_trip() {
        let key = ("émile".to_owned(), 42_i64);
        let c = encode_cursor(&key);
        assert!(
            c.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        );
        assert_eq!(decode_cursor::<(String, i64)>(&c), Some(key));
        let q = query(None, Some(&c));
        assert_eq!(
            q.after::<(String, i64)>().unwrap(),
            Some(("émile".to_owned(), 42))
        );
    }

    #[test]
    fn empty_after_is_the_first_page() {
        assert_eq!(query(None, Some("")).after::<i64>().unwrap(), None);
        assert_eq!(query(None, None).after::<i64>().unwrap(), None);
    }

    #[test]
    fn bad_cursors_are_refused() {
        let id_cursor = encode_cursor(&5_i64);
        for bad in ["not base64!", "e30", &"A".repeat(600), &id_cursor] {
            let q = query(None, Some(bad));
            let err = q.after::<(String, i64)>().unwrap_err();
            assert_eq!(err.code, "invalid_cursor", "{bad:?}");
        }
    }

    #[test]
    fn pages_know_whether_more_follow() {
        let full = Page::from_rows(vec![5, 4, 3], 2, |&n| n);
        assert_eq!(full.items, vec![5, 4]);
        assert_eq!(decode_cursor::<i32>(full.next.as_deref().unwrap()), Some(4));
        let last = Page::from_rows(vec![2, 1], 2, |&n| n);
        assert_eq!(last.items, vec![2, 1]);
        assert_eq!(last.next, None);
        let empty = Page::from_rows(Vec::<i32>::new(), 2, |&n| n);
        assert!(empty.items.is_empty() && empty.next.is_none());
    }
}
