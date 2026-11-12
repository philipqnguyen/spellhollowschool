//! Input rules for registration: email normalization, password policy and the
//! adult attestation. Pure functions; the handlers turn their errors into responses.

pub const PASSWORD_MIN_CHARS: usize = 10;
pub const PASSWORD_MAX_CHARS: usize = 128;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuleError {
    InvalidEmail,
    AdultUnconfirmed,
    PasswordTooShort,
    PasswordTooLong,
    PasswordIsEmail,
}

/// Trims and lowercases an email address and checks its basic shape. This is
/// deliberately loose (the verification email is the real test): one `@`, a
/// non-empty local part, a dotted domain, no whitespace or control characters.
pub fn normalize_email(raw: &str) -> Result<String, RuleError> {
    let email = raw.trim().to_lowercase();
    let (local, domain) = email.rsplit_once('@').ok_or(RuleError::InvalidEmail)?;
    let ok = email.len() <= 254
        && !local.is_empty()
        && local.len() <= 64
        && !local.contains('@')
        && domain.contains('.')
        && !domain.starts_with('.')
        && !domain.ends_with('.')
        && !domain.contains("..")
        && !email.chars().any(|c| c.is_whitespace() || c.is_control());
    if ok {
        Ok(email)
    } else {
        Err(RuleError::InvalidEmail)
    }
}

/// Length limits count characters, not bytes, so non-ASCII passphrases are fine.
pub fn check_password(password: &str, normalized_email: &str) -> Result<(), RuleError> {
    let n = password.chars().count();
    if n < PASSWORD_MIN_CHARS {
        return Err(RuleError::PasswordTooShort);
    }
    if n > PASSWORD_MAX_CHARS {
        return Err(RuleError::PasswordTooLong);
    }
    let lowered = password.trim().to_lowercase();
    let local = normalized_email.split('@').next().unwrap_or_default();
    if lowered == normalized_email || lowered == local {
        return Err(RuleError::PasswordIsEmail);
    }
    Ok(())
}

/// The adult check: the player must tick "I am 18 or older". No date of birth
/// is asked for; the account records only when they attested.
pub fn check_adult(confirm_adult: bool) -> Result<(), RuleError> {
    if confirm_adult {
        Ok(())
    } else {
        Err(RuleError::AdultUnconfirmed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_email() {
        assert_eq!(
            normalize_email("  Ada@Example.ORG ").unwrap(),
            "ada@example.org"
        );
        assert_eq!(
            normalize_email("a.b+tag@mail.co.uk").unwrap(),
            "a.b+tag@mail.co.uk"
        );
    }

    #[test]
    fn rejects_bad_email() {
        for bad in [
            "",
            "ada",
            "@example.org",
            "ada@",
            "ada@localhost",
            "ada@.org",
            "ada@ex..org",
            "a da@example.org",
            "a@b@example.org",
            "ada@example.org.",
        ] {
            assert_eq!(
                normalize_email(bad),
                Err(RuleError::InvalidEmail),
                "{bad:?}"
            );
        }
        let long = format!("{}@example.org", "a".repeat(65));
        assert_eq!(normalize_email(&long), Err(RuleError::InvalidEmail));
    }

    #[test]
    fn password_rules() {
        let email = "ada@example.org";
        assert_eq!(
            check_password("short", email),
            Err(RuleError::PasswordTooShort)
        );
        assert_eq!(
            check_password(&"x".repeat(129), email),
            Err(RuleError::PasswordTooLong)
        );
        assert_eq!(
            check_password("ADA@example.org", email),
            Err(RuleError::PasswordIsEmail)
        );
        assert_eq!(
            check_password("adaaaaaaaa", "adaaaaaaaa@x.org"),
            Err(RuleError::PasswordIsEmail)
        );
        assert!(check_password("correct horse battery", email).is_ok());
        // Ten characters, more than ten bytes.
        assert!(check_password("ééééééééé1", email).is_ok());
    }

    #[test]
    fn adult_check() {
        assert_eq!(check_adult(true), Ok(()));
        assert_eq!(check_adult(false), Err(RuleError::AdultUnconfirmed));
    }
}
