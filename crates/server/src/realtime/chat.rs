//! Chat rules: text cleanup, the per-player rate limit and the word filter.
//! Routing (who receives a message) lives in the world, which knows where
//! everyone is; see `World::route_chat`.

use std::borrow::Cow;
use std::collections::HashSet;
use std::path::Path;
use std::time::{Duration, Instant};

use protocol::CHAT_MAX_LEN;

/// Say reaches everyone within this distance of the speaker (metres, 3D).
/// 30 m since the castle kit's scale pass (M1.0): the Great Hall is 66 m
/// long, so a say carries across about half of it.
pub const SAY_RADIUS_M: f32 = 30.0;
/// Messages a player may send in a burst...
pub const CHAT_BURST: f32 = 5.0;
/// ...and per second after that.
pub const CHAT_PER_SECOND: f32 = 1.0;
/// How long a whisper partner stays a priority player (always visible).
pub const WHISPER_PRIORITY: Duration = Duration::from_secs(10 * 60);

pub const NOTICE_TOO_FAST: &str = "You are sending messages too quickly. Wait a moment.";

/// Trims the text, turns control characters and line separators into spaces,
/// drops invisible formatting characters ([`is_hidden_format`]) and refuses
/// empty or over-long (> `CHAT_MAX_LEN` bytes) messages.
pub fn clean_text(raw: &str) -> Option<String> {
    if raw.len() > CHAT_MAX_LEN {
        return None;
    }
    let cleaned: String = raw
        .chars()
        .filter(|&c| !is_hidden_format(c))
        .map(|c| {
            if c.is_control() || matches!(c, '\u{2028}' | '\u{2029}') {
                ' '
            } else {
                c
            }
        })
        .collect();
    let trimmed = cleaned.trim();
    (!trimmed.is_empty()).then(|| trimmed.to_owned())
}

/// Invisible characters that only steer how text is displayed: bidirectional
/// overrides and isolates (which can make a line read backwards or run into
/// the sender's name), zero-width spaces and word joiners, the byte-order mark
/// and the soft hyphen. They have no place in a chat line and would let blocked
/// words slip past the filter ("bad\u{200B}word"). Zero-width (non-)joiners
/// stay: emoji sequences and some scripts need them.
pub fn is_hidden_format(c: char) -> bool {
    matches!(
        c,
        '\u{AD}'
            | '\u{61C}'
            | '\u{180E}'
            | '\u{200B}'
            | '\u{200E}'
            | '\u{200F}'
            | '\u{202A}'..='\u{202E}'
            | '\u{2060}'..='\u{2064}'
            | '\u{2066}'..='\u{2069}'
            | '\u{FEFF}'
    )
}

/// Token-bucket rate limit: takes one token if available.
pub fn take_token(tokens: &mut f32, at: &mut Instant, now: Instant) -> bool {
    let elapsed = now.saturating_duration_since(*at).as_secs_f32();
    *at = now.max(*at);
    *tokens = (*tokens + elapsed * CHAT_PER_SECOND).min(CHAT_BURST);
    if *tokens >= 1.0 {
        *tokens -= 1.0;
        true
    } else {
        false
    }
}

/// Masks blocked words with asterisks. The list is one word per line
/// (case-insensitive, whole words only); blank lines and `#` comments are
/// ignored. See `data/moderation/blocked_words.txt`.
#[derive(Debug, Default, Clone)]
pub struct WordFilter {
    words: HashSet<String>,
}

impl WordFilter {
    pub fn from_list(text: &str) -> Self {
        let words = text
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .map(str::to_lowercase)
            .collect();
        Self { words }
    }

    pub fn load(path: &Path) -> std::io::Result<Self> {
        Ok(Self::from_list(&std::fs::read_to_string(path)?))
    }

    /// Loads the list, or logs a warning and filters nothing if it is missing.
    pub fn load_or_empty(path: &Path) -> Self {
        match Self::load(path) {
            Ok(f) => {
                tracing::info!(words = f.len(), path = %path.display(), "loaded chat word filter");
                f
            }
            Err(e) => {
                tracing::warn!(path = %path.display(), error = %e, "no chat word filter loaded");
                Self::default()
            }
        }
    }

    pub fn len(&self) -> usize {
        self.words.len()
    }

    pub fn is_empty(&self) -> bool {
        self.words.is_empty()
    }

    /// The text with every blocked word replaced by one `*` per character.
    pub fn apply<'a>(&self, text: &'a str) -> Cow<'a, str> {
        if self.words.is_empty() {
            return Cow::Borrowed(text);
        }
        let mut out = String::new();
        // Bytes of `text` already copied into `out`.
        let mut copied = 0;
        let mut changed = false;
        let mut word = String::new();
        // Words are maximal runs of letters and digits.
        let mut chars = text.char_indices().peekable();
        while let Some((start, c)) = chars.next() {
            if !c.is_alphanumeric() {
                continue;
            }
            word.clear();
            word.extend(c.to_lowercase());
            let mut end = start + c.len_utf8();
            while let Some(&(j, d)) = chars.peek().filter(|(_, d)| d.is_alphanumeric()) {
                word.extend(d.to_lowercase());
                end = j + d.len_utf8();
                chars.next();
            }
            if self.words.contains(&word) {
                out.push_str(&text[copied..start]);
                out.extend(std::iter::repeat_n('*', text[start..end].chars().count()));
                copied = end;
                changed = true;
            }
        }
        if !changed {
            return Cow::Borrowed(text);
        }
        out.push_str(&text[copied..]);
        Cow::Owned(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cleans_and_bounds_text() {
        assert_eq!(clean_text("  hi there \n").as_deref(), Some("hi there"));
        assert_eq!(clean_text("a\u{7}b").as_deref(), Some("a b"));
        assert_eq!(clean_text("   "), None);
        assert_eq!(clean_text(""), None);
        assert!(clean_text(&"x".repeat(CHAT_MAX_LEN)).is_some());
        assert_eq!(clean_text(&"x".repeat(CHAT_MAX_LEN + 1)), None);
    }

    #[test]
    fn drops_invisible_formatting() {
        // A right-to-left override would show "hello" as "olleh".
        assert_eq!(clean_text("\u{202E}hello").as_deref(), Some("hello"));
        assert_eq!(
            clean_text("bad\u{200B}word \u{2066}x\u{2069}\u{FEFF}").as_deref(),
            Some("badword x")
        );
        assert_eq!(clean_text("a\u{2028}b").as_deref(), Some("a b"));
        assert_eq!(clean_text("\u{200B}\u{202E}"), None);
        // Emoji joiners are kept.
        assert_eq!(
            clean_text("\u{1F469}\u{200D}\u{1F52C}").as_deref(),
            Some("\u{1F469}\u{200D}\u{1F52C}")
        );
        // So the filter sees the word.
        let f = WordFilter::from_list("badword");
        assert_eq!(
            f.apply(&clean_text("a bad\u{200B}word").unwrap()),
            "a *******"
        );
    }

    #[test]
    fn rate_limit_allows_a_burst_then_one_per_second() {
        let t0 = Instant::now();
        let (mut tokens, mut at) = (CHAT_BURST, t0);
        for _ in 0..5 {
            assert!(take_token(&mut tokens, &mut at, t0));
        }
        assert!(!take_token(&mut tokens, &mut at, t0));
        assert!(!take_token(
            &mut tokens,
            &mut at,
            t0 + Duration::from_millis(500)
        ));
        assert!(take_token(
            &mut tokens,
            &mut at,
            t0 + Duration::from_millis(1000)
        ));
        assert!(!take_token(
            &mut tokens,
            &mut at,
            t0 + Duration::from_millis(1100)
        ));
        // Long silences refill only up to the burst.
        let later = t0 + Duration::from_secs(60);
        for _ in 0..5 {
            assert!(take_token(&mut tokens, &mut at, later));
        }
        assert!(!take_token(&mut tokens, &mut at, later));
    }

    #[test]
    fn filter_masks_whole_words_case_insensitively() {
        let f = WordFilter::from_list("# comment\n\nBadword\n  zonk \n");
        assert_eq!(f.len(), 2);
        assert_eq!(f.apply("hello there"), "hello there");
        assert!(matches!(f.apply("hello there"), Cow::Borrowed(_)));
        assert_eq!(f.apply("what a BADWORD!"), "what a *******!");
        assert_eq!(
            f.apply("zonk, Zonk and badwords"),
            "****, **** and badwords"
        );
        assert_eq!(f.apply("zonk"), "****");
        assert_eq!(f.apply("café zonk"), "café ****");
        assert_eq!(WordFilter::default().apply("zonk"), "zonk");
    }

    #[test]
    fn the_shipped_list_loads() {
        let path =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/moderation/blocked_words.txt");
        let f = WordFilter::load(&path).expect("data/moderation/blocked_words.txt exists");
        assert!(!f.is_empty());
    }
}
