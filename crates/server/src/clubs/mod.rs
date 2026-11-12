//! Clubs (docs/architecture.md, "Clubs"): lasting groups of characters with
//! an owner, officers and members, kept in the database (`clubs`,
//! `club_members`).
//!
//! - This module: the numbers (`data/clubs.json`, [`ClubRules`]), what a
//!   name, tag and message of the day may be, and who may do what to whom
//!   ([`Club`] and its checks, pure and unit tested). The database side and
//!   the world both use the same checks: the world to refuse at once, the
//!   database under the club's row lock, which decides.
//! - [`store`]: every change in one transaction that locks the club's row,
//!   checks again, writes, bumps `clubs.version` and returns the club as it
//!   is now ([`store::Outcome`]).
//! - [`routes`]: `GET /api/clubs` (the register the secretary reads from).
//! - The world (`realtime::club`) keeps the clubs of online characters,
//!   invitations, club chat and what members are sent.

pub mod routes;
pub mod store;

use std::collections::BTreeMap;
use std::sync::OnceLock;

use protocol::{ClubErrorCode, ClubRole, House};
use serde::Deserialize;

use crate::realtime::chat::{self, WordFilter};

const CLUBS_JSON: &str = include_str!("../../../../data/clubs.json");

/// `data/clubs.json`.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct ClubRules {
    pub version: u32,
    /// Founding needs this level...
    pub found_level: u8,
    /// ...and this many Crowns, the fee paid on founding.
    pub found_crowns: i64,
    /// Members of one club at most.
    pub members_max: u16,
    /// A name's length in characters, inclusive.
    pub name_chars: [u8; 2],
    /// A tag's length in characters, inclusive.
    pub tag_chars: [u8; 2],
    pub motd_chars_max: u16,
    /// The roster id of the clubs secretary (a clerk).
    pub secretary: String,
    /// What she says (the client's; checked here).
    pub secretary_lines: BTreeMap<String, String>,
}

impl ClubRules {
    pub fn from_json(json: &str) -> Result<ClubRules, String> {
        let r: ClubRules = serde_json::from_str(json).map_err(|e| e.to_string())?;
        if r.version != 1 {
            return Err(format!("clubs.json: version {} (expected 1)", r.version));
        }
        if !(1..=20).contains(&r.found_level) || r.found_crowns < 0 {
            return Err("clubs.json: found_level 1-20, found_crowns 0 or more".into());
        }
        if !(2..=1_000).contains(&r.members_max) {
            return Err("clubs.json: members_max 2-1000".into());
        }
        let ok = |[lo, hi]: [u8; 2]| lo >= 1 && lo <= hi;
        if !ok(r.name_chars) || !ok(r.tag_chars) || r.tag_chars[1] > 8 || r.name_chars[1] > 40 {
            return Err("clubs.json: name_chars and tag_chars are [min, max]".into());
        }
        if r.motd_chars_max == 0 || usize::from(r.motd_chars_max) > protocol::CHAT_MAX_LEN {
            return Err("clubs.json: motd_chars_max 1 to the chat limit".into());
        }
        for key in LINE_KEYS {
            let Some(line) = r.secretary_lines.get(*key) else {
                return Err(format!("clubs.json: secretary_lines.{key} is missing"));
            };
            if line.trim().is_empty() || line.chars().count() > 320 {
                return Err(format!(
                    "clubs.json: secretary_lines.{key}: 1-320 characters"
                ));
            }
            let mut rest = line.as_str();
            while let Some(open) = rest.find('{') {
                let Some(close) = rest[open..].find('}') else {
                    return Err(format!("clubs.json: secretary_lines.{key}: an open brace"));
                };
                let name = &rest[open + 1..open + close];
                if !PLACEHOLDERS.contains(&name) {
                    return Err(format!(
                        "clubs.json: secretary_lines.{key}: unknown placeholder {{{name}}}"
                    ));
                }
                rest = &rest[open + close + 1..];
            }
        }
        Ok(r)
    }

    /// Secretary line `key` with its placeholders filled (`vars`: name and
    /// value, besides `found_level` and `found_crowns`), as the client does.
    pub fn line(&self, key: &str, vars: &[(&str, String)]) -> Option<String> {
        let mut out = self.secretary_lines.get(key)?.clone();
        let own = [
            ("found_level", self.found_level.to_string()),
            ("found_crowns", self.found_crowns.to_string()),
        ];
        for (k, v) in own.iter().chain(vars) {
            out = out.replace(&format!("{{{k}}}"), v);
        }
        Some(out)
    }
}

/// Every line the secretary has (`secretary_lines`).
pub const LINE_KEYS: &[&str] = &[
    "greeting",
    "option_found",
    "option_list",
    "option_goodbye",
    "not_level",
    "not_crowns",
    "not_either",
    "in_club",
    "eligible",
    "sealed",
];

/// The placeholders her lines may use.
pub const PLACEHOLDERS: &[&str] = &[
    "found_level",
    "found_crowns",
    "level",
    "crowns",
    "short",
    "club",
];

/// The shipped `data/clubs.json` (checked once; a broken file stops the
/// server at start, `crate::catalog` style).
pub fn rules() -> &'static ClubRules {
    static RULES: OnceLock<ClubRules> = OnceLock::new();
    RULES.get_or_init(|| ClubRules::from_json(CLUBS_JSON).expect("data/clubs.json is valid"))
}

// ---------------------------------------------------------------- names

/// The case-folded key club names are unique by.
pub fn name_key(name: &str) -> String {
    name.to_lowercase()
}

fn is_letter(c: char) -> bool {
    c.is_ascii_alphabetic()
        || (('\u{C0}'..='\u{17F}').contains(&c) && c != '\u{D7}' && c != '\u{F7}')
}

/// A club name as stored: spaces collapsed; `name_chars` long; letters (as
/// character names), digits, single spaces, apostrophes, hyphens and
/// ampersands, starting and ending with a letter or digit; nothing the word
/// filter refuses.
pub fn clean_name(
    raw: &str,
    rules: &ClubRules,
    filter: &WordFilter,
) -> Result<String, ClubErrorCode> {
    let name = raw.split_whitespace().collect::<Vec<_>>().join(" ");
    let n = name.chars().count();
    let [lo, hi] = rules.name_chars;
    if !(usize::from(lo)..=usize::from(hi)).contains(&n) {
        return Err(ClubErrorCode::NameInvalid);
    }
    let word = |c: char| is_letter(c) || c.is_ascii_digit();
    let ok_chars = name
        .chars()
        .all(|c| word(c) || matches!(c, ' ' | '\'' | '-' | '&'));
    let ends = name.chars().next().is_some_and(word) && name.chars().last().is_some_and(word);
    let joiners = |c: char| matches!(c, '\'' | '-');
    let chars: Vec<char> = name.chars().collect();
    let no_double = chars.windows(2).all(|w| !(joiners(w[0]) && joiners(w[1])));
    if !(ok_chars && ends && no_double) {
        return Err(ClubErrorCode::NameInvalid);
    }
    if filtered(filter, &name) {
        return Err(ClubErrorCode::Filtered);
    }
    Ok(name)
}

/// A tag as stored: `tag_chars` ASCII letters or digits, upper case, and
/// nothing the word filter refuses.
pub fn clean_tag(
    raw: &str,
    rules: &ClubRules,
    filter: &WordFilter,
) -> Result<String, ClubErrorCode> {
    let tag = raw.trim();
    let [lo, hi] = rules.tag_chars;
    if !(usize::from(lo)..=usize::from(hi)).contains(&tag.chars().count())
        || !tag.chars().all(|c| c.is_ascii_alphanumeric())
    {
        return Err(ClubErrorCode::TagInvalid);
    }
    let tag = tag.to_ascii_uppercase();
    if filtered(filter, &tag) {
        return Err(ClubErrorCode::Filtered);
    }
    Ok(tag)
}

/// A message of the day as stored: cleaned like a chat line, at most
/// `motd_chars_max` characters, nothing the filter refuses; empty clears it.
pub fn clean_motd(
    raw: &str,
    rules: &ClubRules,
    filter: &WordFilter,
) -> Result<String, ClubErrorCode> {
    if raw.trim().is_empty() {
        return Ok(String::new());
    }
    let motd = chat::clean_text(raw).ok_or(ClubErrorCode::MotdInvalid)?;
    if motd.chars().count() > usize::from(rules.motd_chars_max) {
        return Err(ClubErrorCode::MotdInvalid);
    }
    if filtered(filter, &motd) {
        return Err(ClubErrorCode::Filtered);
    }
    Ok(motd)
}

fn filtered(filter: &WordFilter, text: &str) -> bool {
    matches!(filter.apply(text), std::borrow::Cow::Owned(_))
}

// ---------------------------------------------------------------- the club

/// One member as the club keeps it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Member {
    pub character_id: i64,
    pub name: String,
    pub house: House,
    /// As stored (the world shows an online member's own).
    pub level: u8,
    pub role: ClubRole,
}

/// A club as stored, its members in joining order (longest-serving first).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Club {
    pub id: i64,
    pub name: String,
    pub tag: String,
    pub motd: String,
    /// `clubs.version`: every change bumps it.
    pub version: i64,
    pub members: Vec<Member>,
}

impl Club {
    pub fn member(&self, character_id: i64) -> Option<&Member> {
        self.members.iter().find(|m| m.character_id == character_id)
    }

    pub fn owner(&self) -> Option<&Member> {
        self.members.iter().find(|m| m.role == ClubRole::Owner)
    }

    /// `by`'s rank, or `NotInClub`.
    fn rank(&self, by: i64) -> Result<ClubRole, ClubErrorCode> {
        self.member(by)
            .map(|m| m.role)
            .ok_or(ClubErrorCode::NotInClub)
    }

    /// `target` (not `by`), or `NotMember`.
    fn other(&self, by: i64, target: i64) -> Result<&Member, ClubErrorCode> {
        self.member(target)
            .filter(|m| m.character_id != by)
            .ok_or(ClubErrorCode::NotMember)
    }

    /// Whether `by` may invite (an officer or the owner, the club not full).
    pub fn check_invite(&self, by: i64, members_max: u16) -> Result<(), ClubErrorCode> {
        if self.rank(by)? < ClubRole::Officer {
            return Err(ClubErrorCode::NotAllowed);
        }
        if self.members.len() >= usize::from(members_max) {
            return Err(ClubErrorCode::Full);
        }
        Ok(())
    }

    /// Whether `by` may remove `target`: officers remove members, the
    /// owner anyone else.
    pub fn check_kick(&self, by: i64, target: i64) -> Result<(), ClubErrorCode> {
        let rank = self.rank(by)?;
        let t = self.other(by, target)?;
        let ok = match rank {
            ClubRole::Owner => true,
            ClubRole::Officer => t.role == ClubRole::Member,
            ClubRole::Member => false,
        };
        if ok {
            Ok(())
        } else {
            Err(ClubErrorCode::NotAllowed)
        }
    }

    /// Whether `by` may make `target` an officer (`Officer`) or a member
    /// again (`Member`): the owner only, never on themselves.
    pub fn check_set_role(
        &self,
        by: i64,
        target: i64,
        role: ClubRole,
    ) -> Result<(), ClubErrorCode> {
        if self.rank(by)? != ClubRole::Owner || role == ClubRole::Owner {
            return Err(ClubErrorCode::NotAllowed);
        }
        self.other(by, target)?;
        Ok(())
    }

    /// Whether `by` may hand the club to `target`: the owner, to anyone else.
    pub fn check_transfer(&self, by: i64, target: i64) -> Result<(), ClubErrorCode> {
        if self.rank(by)? != ClubRole::Owner {
            return Err(ClubErrorCode::NotAllowed);
        }
        self.other(by, target)?;
        Ok(())
    }

    /// Renaming and disbanding: the owner only.
    pub fn check_owner(&self, by: i64) -> Result<(), ClubErrorCode> {
        if self.rank(by)? != ClubRole::Owner {
            return Err(ClubErrorCode::NotAllowed);
        }
        Ok(())
    }

    /// The message of the day: officers and the owner.
    pub fn check_motd(&self, by: i64) -> Result<(), ClubErrorCode> {
        if self.rank(by)? < ClubRole::Officer {
            return Err(ClubErrorCode::NotAllowed);
        }
        Ok(())
    }

    /// Who owns the club once `leaving` (its owner) is gone: the
    /// longest-serving officer, else the longest-serving member; `None`
    /// when nobody is left (or `leaving` is not the owner).
    pub fn successor(&self, leaving: i64) -> Option<i64> {
        if self.member(leaving)?.role != ClubRole::Owner {
            return None;
        }
        let others = || self.members.iter().filter(|m| m.character_id != leaving);
        others()
            .find(|m| m.role == ClubRole::Officer)
            .or_else(|| others().next())
            .map(|m| m.character_id)
    }

    /// The members as the roster lists them: the owner, the officers, then
    /// the members, each rank in joining order.
    pub fn ordered(&self) -> impl Iterator<Item = &Member> {
        [ClubRole::Owner, ClubRole::Officer, ClubRole::Member]
            .into_iter()
            .flat_map(move |r| self.members.iter().filter(move |m| m.role == r))
    }
}

#[cfg(test)]
mod tests;
