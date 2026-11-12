//! Daily caps (`daily_caps`): per character, per UTC day, per cap group, how
//! much was used. XP groups count XP (`XpSource::cap_group`); the dueling
//! code also counts duels (wins that gave house points, rewarded duels per
//! opponent) with groups of its own. Always inside a transaction that holds
//! the character's row lock, so takes for one character never interleave.

use chrono::NaiveDate;
use sqlx::PgConnection;

/// Takes up to `want` from the day's `cap` (of `limit`), returning what was
/// allowed (0 once the cap is used up).
pub async fn take(
    conn: &mut PgConnection,
    character_id: i64,
    day: NaiveDate,
    cap: &str,
    want: u32,
    limit: u32,
) -> Result<u32, sqlx::Error> {
    let used = used(&mut *conn, character_id, day, cap).await?;
    let allowed = want.min(limit.saturating_sub(used));
    if allowed > 0 {
        let total = i32::try_from(u64::from(used) + u64::from(allowed)).unwrap_or(i32::MAX);
        sqlx::query(
            "INSERT INTO daily_caps (character_id, day, cap, used) VALUES ($1, $2, $3, $4)
             ON CONFLICT (character_id, day, cap) DO UPDATE SET used = EXCLUDED.used",
        )
        .bind(character_id)
        .bind(day)
        .bind(cap)
        .bind(total)
        .execute(&mut *conn)
        .await?;
    }
    Ok(allowed)
}

/// How much of the day's `cap` is used.
pub async fn used(
    conn: &mut PgConnection,
    character_id: i64,
    day: NaiveDate,
    cap: &str,
) -> Result<u32, sqlx::Error> {
    let used: Option<i32> = sqlx::query_scalar(
        "SELECT used FROM daily_caps WHERE character_id = $1 AND day = $2 AND cap = $3",
    )
    .bind(character_id)
    .bind(day)
    .bind(cap)
    .fetch_optional(&mut *conn)
    .await?;
    Ok(used.and_then(|u| u32::try_from(u).ok()).unwrap_or(0))
}

/// The minute job: forgets days before `before` (yesterday's caps are kept
/// for a day so a duel ending just after midnight still finds its day).
pub async fn cleanup(pool: &sqlx::PgPool, before: NaiveDate) -> Result<u64, sqlx::Error> {
    let r = sqlx::query("DELETE FROM daily_caps WHERE day < $1")
        .bind(before)
        .execute(pool)
        .await?;
    Ok(r.rows_affected())
}
