//! Clubs in the database. Every change is one transaction that locks the
//! club's row (`FOR UPDATE`), loads its members, checks the change with the
//! [`Club`](super::Club) rules, writes it, bumps `clubs.version` and returns
//! the club as it is now in an [`Outcome`] (the world keeps the newest
//! version). Founding locks the founder's `characters` row first, like every
//! Crown change (docs/architecture.md, "Database": lock order).

use protocol::{ClubErrorCode, ClubNotice, ClubRole};
use serde::Serialize;
use sqlx::{PgConnection, PgPool};

use super::{Club, ClubRules, Member, name_key};
use crate::characters::house_from_db;
use crate::db::is_unique_violation;
use crate::ledger::{self, LedgerError, Reason};

/// Why a change did not happen.
#[derive(Debug)]
pub enum StoreError {
    /// The rules refused it (the player's error).
    Refused(ClubErrorCode),
    Db(sqlx::Error),
}

impl From<sqlx::Error> for StoreError {
    fn from(e: sqlx::Error) -> Self {
        StoreError::Db(e)
    }
}

impl From<ClubErrorCode> for StoreError {
    fn from(c: ClubErrorCode) -> Self {
        StoreError::Refused(c)
    }
}

impl StoreError {
    /// What the player hears (a database failure is `Failed`, logged by
    /// the caller).
    pub fn code(&self) -> ClubErrorCode {
        match self {
            StoreError::Refused(c) => *c,
            StoreError::Db(_) => ClubErrorCode::Failed,
        }
    }
}

/// A change made: the club now, who was in it before, what to tell them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Outcome {
    pub club_id: i64,
    /// `None`: the club is gone.
    pub club: Option<Club>,
    /// Members before the change (those no longer in it hear they are not).
    pub before: Vec<i64>,
    /// For every member before and after.
    pub notices: Vec<ClubNotice>,
}

#[derive(sqlx::FromRow)]
struct ClubRow {
    id: i64,
    name: String,
    tag: String,
    motd: String,
    version: i64,
}

#[derive(sqlx::FromRow)]
struct MemberRow {
    character_id: i64,
    name: String,
    house: i16,
    level: i16,
    role: String,
}

async fn members(conn: &mut PgConnection, club_id: i64) -> Result<Vec<Member>, sqlx::Error> {
    let rows: Vec<MemberRow> = sqlx::query_as(
        "SELECT m.character_id, c.name, c.house, c.level, m.role
         FROM club_members m JOIN characters c ON c.id = m.character_id
         WHERE m.club_id = $1 ORDER BY m.joined_at, m.character_id",
    )
    .bind(club_id)
    .fetch_all(&mut *conn)
    .await?;
    Ok(rows
        .into_iter()
        .map(|r| Member {
            character_id: r.character_id,
            name: r.name,
            house: house_from_db(r.house).unwrap_or(protocol::House::ALL[0]),
            level: u8::try_from(r.level).unwrap_or(1),
            role: ClubRole::from_id(&r.role).unwrap_or(ClubRole::Member),
        })
        .collect())
}

async fn club_row(
    conn: &mut PgConnection,
    id: i64,
    lock: bool,
) -> Result<Option<Club>, sqlx::Error> {
    let sql = if lock {
        "SELECT id, name, tag, motd, version FROM clubs WHERE id = $1 FOR UPDATE"
    } else {
        "SELECT id, name, tag, motd, version FROM clubs WHERE id = $1"
    };
    let Some(r): Option<ClubRow> = sqlx::query_as(sql)
        .bind(id)
        .fetch_optional(&mut *conn)
        .await?
    else {
        return Ok(None);
    };
    let members = members(conn, r.id).await?;
    Ok(Some(Club {
        id: r.id,
        name: r.name,
        tag: r.tag,
        motd: r.motd,
        version: r.version,
        members,
    }))
}

async fn club_id_of(
    conn: &mut PgConnection,
    character_id: i64,
) -> Result<Option<i64>, sqlx::Error> {
    sqlx::query_scalar("SELECT club_id FROM club_members WHERE character_id = $1")
        .bind(character_id)
        .fetch_optional(&mut *conn)
        .await
}

/// Club `id` as stored now.
pub async fn load(pool: &PgPool, id: i64) -> Result<Option<Club>, sqlx::Error> {
    let mut conn = pool.acquire().await?;
    club_row(&mut conn, id, false).await
}

/// The club `character_id` is in, if any.
pub async fn load_of(pool: &PgPool, character_id: i64) -> Result<Option<Club>, sqlx::Error> {
    let mut conn = pool.acquire().await?;
    match club_id_of(&mut conn, character_id).await? {
        Some(id) => club_row(&mut conn, id, false).await,
        None => Ok(None),
    }
}

/// Locks the club `character_id` is in (`NotInClub` outside one).
async fn lock_club_of(conn: &mut PgConnection, character_id: i64) -> Result<Club, StoreError> {
    let id = club_id_of(conn, character_id)
        .await?
        .ok_or(ClubErrorCode::NotInClub)?;
    let club = club_row(conn, id, true)
        .await?
        .ok_or(ClubErrorCode::NotInClub)?;
    // It may have left between the two reads.
    club.member(character_id).ok_or(ClubErrorCode::NotInClub)?;
    Ok(club)
}

async fn bump(conn: &mut PgConnection, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE clubs SET version = version + 1 WHERE id = $1")
        .bind(id)
        .execute(&mut *conn)
        .await?;
    Ok(())
}

async fn set_role_row(
    conn: &mut PgConnection,
    character_id: i64,
    role: ClubRole,
) -> Result<(), sqlx::Error> {
    sqlx::query("UPDATE club_members SET role = $2 WHERE character_id = $1")
        .bind(character_id)
        .bind(role.id())
        .execute(&mut *conn)
        .await?;
    Ok(())
}

/// Takes `character_id` out of `club` (locked), passing ownership on or
/// disbanding an emptied club. `notice` says why; returns every notice.
async fn remove_member(
    conn: &mut PgConnection,
    club: &Club,
    character_id: i64,
    notice: ClubNotice,
) -> Result<Vec<ClubNotice>, sqlx::Error> {
    let next = club.successor(character_id);
    sqlx::query("DELETE FROM club_members WHERE character_id = $1")
        .bind(character_id)
        .execute(&mut *conn)
        .await?;
    let mut notices = vec![notice];
    if club.members.len() <= 1 {
        sqlx::query("DELETE FROM clubs WHERE id = $1")
            .bind(club.id)
            .execute(&mut *conn)
            .await?;
        notices.push(ClubNotice::Disbanded {
            club: club.name.clone(),
        });
        return Ok(notices);
    }
    if let Some(next) = next {
        set_role_row(conn, next, ClubRole::Owner).await?;
        let name = club
            .member(next)
            .map(|m| m.name.clone())
            .unwrap_or_default();
        notices.push(ClubNotice::Owner { name });
    }
    bump(conn, club.id).await?;
    Ok(notices)
}

fn before(club: &Club) -> Vec<i64> {
    club.members.iter().map(|m| m.character_id).collect()
}

/// Reads the club back, commits and phrases the outcome.
async fn finish(
    mut tx: sqlx::Transaction<'_, sqlx::Postgres>,
    club_id: i64,
    before: Vec<i64>,
    notices: Vec<ClubNotice>,
) -> Result<Outcome, StoreError> {
    let club = club_row(&mut tx, club_id, false).await?;
    tx.commit().await?;
    Ok(Outcome {
        club_id,
        club,
        before,
        notices,
    })
}

/// Founds a club owned by `character_id` (name and tag already cleaned):
/// level, Crowns and no club checked under the character's lock, the fee
/// debited (`club_found`, ref the club). Returns the outcome and the new
/// balance.
pub async fn found(
    pool: &PgPool,
    rules: &ClubRules,
    character_id: i64,
    name: &str,
    tag: &str,
) -> Result<(Outcome, i64), StoreError> {
    let mut tx = pool.begin().await?;
    let row: Option<(i16, i64)> =
        sqlx::query_as("SELECT level, crowns FROM characters WHERE id = $1 FOR NO KEY UPDATE")
            .bind(character_id)
            .fetch_optional(&mut *tx)
            .await?;
    let (level, crowns) = row.ok_or(ClubErrorCode::Failed)?;
    if level < i16::from(rules.found_level) {
        return Err(ClubErrorCode::LevelTooLow.into());
    }
    if crowns < rules.found_crowns {
        return Err(ClubErrorCode::NotEnoughCrowns.into());
    }
    if club_id_of(&mut tx, character_id).await?.is_some() {
        return Err(ClubErrorCode::AlreadyInClub.into());
    }
    let inserted: Result<i64, sqlx::Error> = sqlx::query_scalar(
        "INSERT INTO clubs (name, name_key, tag) VALUES ($1, $2, $3) RETURNING id",
    )
    .bind(name)
    .bind(name_key(name))
    .bind(tag)
    .fetch_one(&mut *tx)
    .await;
    let id = match inserted {
        Ok(id) => id,
        Err(e) if is_unique_violation(&e, "clubs_name_key_key") => {
            return Err(ClubErrorCode::NameTaken.into());
        }
        Err(e) if is_unique_violation(&e, "clubs_tag_key") => {
            return Err(ClubErrorCode::TagTaken.into());
        }
        Err(e) => return Err(e.into()),
    };
    sqlx::query("INSERT INTO club_members (character_id, club_id, role) VALUES ($1, $2, 'owner')")
        .bind(character_id)
        .bind(id)
        .execute(&mut *tx)
        .await?;
    let balance = match ledger::adjust_crowns(
        &mut tx,
        character_id,
        -rules.found_crowns,
        Reason::ClubFound,
        Some(id),
    )
    .await
    {
        Ok(b) => b,
        Err(LedgerError::InsufficientCrowns) => return Err(ClubErrorCode::NotEnoughCrowns.into()),
        Err(LedgerError::Db(e)) => return Err(e.into()),
        Err(LedgerError::ZeroQuantity) => return Err(ClubErrorCode::Failed.into()),
    };
    let notices = vec![ClubNotice::Founded {
        club: name.to_owned(),
    }];
    let outcome = finish(tx, id, Vec::new(), notices).await?;
    Ok((outcome, balance))
}

/// `joiner` accepts an invitation from `inviter` into club `club_id`: the
/// inviter must still be one of its officers or its owner, the joiner in no
/// club, the club not full.
pub async fn join(
    pool: &PgPool,
    rules: &ClubRules,
    club_id: i64,
    inviter: i64,
    joiner: i64,
) -> Result<Outcome, StoreError> {
    let mut tx = pool.begin().await?;
    let club = club_row(&mut tx, club_id, true)
        .await?
        .ok_or(ClubErrorCode::InviteExpired)?;
    match club.check_invite(inviter, rules.members_max) {
        Ok(()) => {}
        Err(ClubErrorCode::Full) => return Err(ClubErrorCode::Full.into()),
        Err(_) => return Err(ClubErrorCode::InviteExpired.into()),
    }
    if club_id_of(&mut tx, joiner).await?.is_some() {
        return Err(ClubErrorCode::AlreadyInClub.into());
    }
    let inserted = sqlx::query(
        "INSERT INTO club_members (character_id, club_id, role) VALUES ($1, $2, 'member')",
    )
    .bind(joiner)
    .bind(club_id)
    .execute(&mut *tx)
    .await;
    match inserted {
        Ok(_) => {}
        Err(e) if is_unique_violation(&e, "club_members_pkey") => {
            return Err(ClubErrorCode::AlreadyInClub.into());
        }
        Err(e) => return Err(e.into()),
    }
    bump(&mut tx, club_id).await?;
    let name: String = sqlx::query_scalar("SELECT name FROM characters WHERE id = $1")
        .bind(joiner)
        .fetch_one(&mut *tx)
        .await?;
    finish(
        tx,
        club_id,
        before(&club),
        vec![ClubNotice::Joined { name }],
    )
    .await
}

/// `character_id` leaves its club.
pub async fn leave(pool: &PgPool, character_id: i64) -> Result<Outcome, StoreError> {
    let mut tx = pool.begin().await?;
    let club = lock_club_of(&mut tx, character_id).await?;
    let name = club
        .member(character_id)
        .map(|m| m.name.clone())
        .unwrap_or_default();
    let notices = remove_member(&mut tx, &club, character_id, ClubNotice::Left { name }).await?;
    finish(tx, club.id, before(&club), notices).await
}

/// `by` removes `target` from their club.
pub async fn kick(pool: &PgPool, by: i64, target: i64) -> Result<Outcome, StoreError> {
    let mut tx = pool.begin().await?;
    let club = lock_club_of(&mut tx, by).await?;
    club.check_kick(by, target)?;
    let name = club
        .member(target)
        .map(|m| m.name.clone())
        .unwrap_or_default();
    let notices = remove_member(&mut tx, &club, target, ClubNotice::Removed { name }).await?;
    finish(tx, club.id, before(&club), notices).await
}

/// The owner `by` makes `target` an officer or a member again.
pub async fn set_role(
    pool: &PgPool,
    by: i64,
    target: i64,
    role: ClubRole,
) -> Result<Outcome, StoreError> {
    let mut tx = pool.begin().await?;
    let club = lock_club_of(&mut tx, by).await?;
    club.check_set_role(by, target, role)?;
    let t = club.member(target).ok_or(ClubErrorCode::NotMember)?;
    let mut notices = Vec::new();
    if t.role != role {
        set_role_row(&mut tx, target, role).await?;
        bump(&mut tx, club.id).await?;
        let name = t.name.clone();
        notices.push(match role {
            ClubRole::Officer => ClubNotice::Promoted { name },
            _ => ClubNotice::Demoted { name },
        });
    }
    finish(tx, club.id, before(&club), notices).await
}

/// The owner `by` hands the club to `target` and becomes an officer.
pub async fn transfer(pool: &PgPool, by: i64, target: i64) -> Result<Outcome, StoreError> {
    let mut tx = pool.begin().await?;
    let club = lock_club_of(&mut tx, by).await?;
    club.check_transfer(by, target)?;
    // The old owner steps down first (one owner per club, an index).
    set_role_row(&mut tx, by, ClubRole::Officer).await?;
    set_role_row(&mut tx, target, ClubRole::Owner).await?;
    bump(&mut tx, club.id).await?;
    let name = club
        .member(target)
        .map(|m| m.name.clone())
        .unwrap_or_default();
    finish(tx, club.id, before(&club), vec![ClubNotice::Owner { name }]).await
}

/// The owner `by` renames the club (name and tag already cleaned).
pub async fn rename(pool: &PgPool, by: i64, name: &str, tag: &str) -> Result<Outcome, StoreError> {
    let mut tx = pool.begin().await?;
    let club = lock_club_of(&mut tx, by).await?;
    club.check_owner(by)?;
    let updated = sqlx::query(
        "UPDATE clubs SET name = $2, name_key = $3, tag = $4, version = version + 1 WHERE id = $1",
    )
    .bind(club.id)
    .bind(name)
    .bind(name_key(name))
    .bind(tag)
    .execute(&mut *tx)
    .await;
    match updated {
        Ok(_) => {}
        Err(e) if is_unique_violation(&e, "clubs_name_key_key") => {
            return Err(ClubErrorCode::NameTaken.into());
        }
        Err(e) if is_unique_violation(&e, "clubs_tag_key") => {
            return Err(ClubErrorCode::TagTaken.into());
        }
        Err(e) => return Err(e.into()),
    }
    let notice = ClubNotice::Renamed {
        club: name.to_owned(),
        tag: tag.to_owned(),
    };
    finish(tx, club.id, before(&club), vec![notice]).await
}

/// An officer or the owner `by` sets the message of the day (cleaned).
pub async fn set_motd(pool: &PgPool, by: i64, motd: &str) -> Result<Outcome, StoreError> {
    let mut tx = pool.begin().await?;
    let club = lock_club_of(&mut tx, by).await?;
    club.check_motd(by)?;
    sqlx::query("UPDATE clubs SET motd = $2, version = version + 1 WHERE id = $1")
        .bind(club.id)
        .bind(motd)
        .execute(&mut *tx)
        .await?;
    let name = club.member(by).map(|m| m.name.clone()).unwrap_or_default();
    finish(tx, club.id, before(&club), vec![ClubNotice::Motd { name }]).await
}

/// The owner `by` disbands the club.
pub async fn disband(pool: &PgPool, by: i64) -> Result<Outcome, StoreError> {
    let mut tx = pool.begin().await?;
    let club = lock_club_of(&mut tx, by).await?;
    club.check_owner(by)?;
    sqlx::query("DELETE FROM clubs WHERE id = $1")
        .bind(club.id)
        .execute(&mut *tx)
        .await?;
    let notice = ClubNotice::Disbanded {
        club: club.name.clone(),
    };
    finish(tx, club.id, before(&club), vec![notice]).await
}

/// Inside the character deletion's transaction (its `characters` row
/// already locked): takes the character out of its club as if it left
/// (ownership passed on, an emptied club disbanded). Returns the club and
/// the notices for its members, for the world.
pub async fn remove_deleted(
    conn: &mut PgConnection,
    character_id: i64,
) -> Result<Option<(i64, Vec<ClubNotice>)>, sqlx::Error> {
    let club = match lock_club_of(conn, character_id).await {
        Ok(c) => c,
        Err(StoreError::Refused(_)) => return Ok(None),
        Err(StoreError::Db(e)) => return Err(e),
    };
    let name = club
        .member(character_id)
        .map(|m| m.name.clone())
        .unwrap_or_default();
    let notices = remove_member(conn, &club, character_id, ClubNotice::Left { name }).await?;
    Ok(Some((club.id, notices)))
}

/// One club on the register (`GET /api/clubs`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, sqlx::FromRow)]
pub struct Listing {
    pub name: String,
    pub tag: String,
    pub members: i64,
}

/// The register: the largest clubs first, then by name; at most `limit`.
pub async fn list(pool: &PgPool, limit: i64) -> Result<Vec<Listing>, sqlx::Error> {
    sqlx::query_as(
        "SELECT c.name, c.tag, count(m.character_id) AS members
         FROM clubs c LEFT JOIN club_members m ON m.club_id = c.id
         GROUP BY c.id ORDER BY members DESC, c.name_key LIMIT $1",
    )
    .bind(limit)
    .fetch_all(pool)
    .await
}
