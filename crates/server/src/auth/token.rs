//! Opaque bearer secrets: 32 random bytes, base64url without padding (43 chars).
//! Only the SHA-256 of the token string is ever stored.

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use rand::RngCore;
use rand::rngs::OsRng;
use sha2::{Digest, Sha256};

pub const TOKEN_BYTES: usize = 32;
/// Encoded length of a token; anything else is rejected before touching the database.
pub const TOKEN_LEN: usize = 43;

pub type TokenHash = [u8; 32];

/// A fresh token and its hash.
pub fn generate() -> (String, TokenHash) {
    let mut bytes = [0u8; TOKEN_BYTES];
    OsRng.fill_bytes(&mut bytes);
    let token = URL_SAFE_NO_PAD.encode(bytes);
    let hash = hash(&token);
    (token, hash)
}

pub fn hash(token: &str) -> TokenHash {
    Sha256::digest(token.as_bytes()).into()
}

/// Cheap shape check: the right length and alphabet.
pub fn looks_valid(token: &str) -> bool {
    token.len() == TOKEN_LEN
        && token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_are_unique_well_formed_and_hashed() {
        let (a, ha) = generate();
        let (b, hb) = generate();
        assert_ne!(a, b);
        assert!(looks_valid(&a) && looks_valid(&b));
        assert_eq!(hash(&a), ha);
        assert_ne!(ha, hb);
    }

    #[test]
    fn rejects_malformed() {
        assert!(!looks_valid(""));
        assert!(!looks_valid(&"a".repeat(42)));
        assert!(!looks_valid(&format!("{}+", "a".repeat(42))));
    }
}
