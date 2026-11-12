//! Ledger reconciliation: replays the append-only ledgers and checks that
//! they explain exactly what the characters hold now. A duplication or loss
//! bug (an item or Crowns appearing, vanishing or ending up in two places)
//! shows up here as a [`Problem`].
//!
//! The checks, each for the characters in scope:
//!
//! - **Crowns**: the balance is the sum of the character's
//!   `crown_ledger` deltas, and the newest row's `balance_after`.
//! - **Item rows**: replaying an item's ledger rows (create, grant and split
//!   add units, consume, destroy and merge take them, and the other side of
//!   a split or merge gives or takes them; the newest create, grant, move or
//!   split row says where it is) gives the row's quantity, owner, location
//!   and definition; an item the ledger says still holds units must exist.
//! - **Places**: an item in the mail is attached to exactly one uncollected
//!   mail of its owner; one on the Exchange is the item of exactly one active
//!   listing of its owner, with the listing's definition and quantity; worn
//!   items have an `equipment` row and placed ones a `dorm_placements` row.
//! - **Money in transit**: a letter's Crowns are what its sender's
//!   `mail_send` rows took; a sale's proceeds mail holds the price minus the
//!   tax; collected mail paid out exactly its Crowns, uncollected mail
//!   nothing; a sold listing took its price from the buyer and every listing
//!   its fee from the seller; each completed trade's Crown rows match its
//!   record and add up to zero.
//! - **Where Crowns come from** (hardening): every Crown row
//!   has a known reason with the right sign (payments take, collections,
//!   finds and the purse give; trades and admin either way), the starting
//!   purse comes once, and a payment for an order, a listing or a trade
//!   names one that is the payer's.
//! - **Orders and postage**: each owl order paid exactly its total, and the
//!   units created for it are exactly the units ordered; each letter still
//!   in a mailbox paid its postage once. (Old collected mail is cleaned up,
//!   so the mail checks cover the mail that still exists; the balances and
//!   item replays cover everything.)
//! - **XP, classes, duels and house points**: the XP ledger
//!   is an unbroken chain whose sum is each character's XP and whose level
//!   is its level; class attendance and duel records match the XP and house
//!   points granted for them; every class or duel house-point row names
//!   what earned it (see `school_problems`).
//! - **Class rewards v2**: every Crown and item row with
//!   reason `class` names a rewarded attendance of the character's, and
//!   each attendance's recorded Crowns and items are exactly what those
//!   rows gave; each lore unlock is its attendance's (see
//!   `class_v2_problems`).
//! - **The House Cup**: every `find` house-point row has a find
//!   (an item or Crown row with reason `find`) of the same character in
//!   the same transaction (the same `at`), every `craft` row names a craft
//!   of that character of a recipe of skill `sources.craft.min_skill` or
//!   more, and every reward mail and `house_cup` item row names a closed
//!   `house_cups` week that the recipient's house won, once per character
//!   and week, to a character with the reward's points that week (see
//!   `house_cup_problems`).
//! - **Discoveries**: every secret found records exactly the
//!   rewards it gave: one `explore` XP row naming the secret's `num` with the
//!   row's XP, the secret's recipe known; every `explore` XP row names a
//!   discovery of its character; a `find` house point may also come from a
//!   discovery in the same transaction (see `discovery_problems`).
//!
//! [`reconcile`] runs in one `REPEATABLE READ` transaction, so it sees one
//! consistent snapshot while the game keeps running. Tests call it at the
//! end with the characters they used (`tests/` helpers bypass the ledger for
//! characters of their own, so a whole-database check would flag those).

use sqlx::PgPool;

/// One thing the ledgers do not explain.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Problem {
    /// Which check failed (`"crowns"`, `"item"`, `"mail"`, ...).
    pub check: &'static str,
    /// What, in words a person reading a test failure or an alert can act on.
    pub detail: String,
}

impl std::fmt::Display for Problem {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.check, self.detail)
    }
}

/// The result of [`reconcile`].
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Report {
    pub problems: Vec<Problem>,
    /// Item rows (current or once held) replayed.
    pub items_checked: usize,
    /// XP ledger rows checked.
    pub xp_rows_checked: usize,
}

impl Report {
    pub fn is_clean(&self) -> bool {
        self.problems.is_empty()
    }

    /// Panics listing every problem (for tests).
    #[track_caller]
    pub fn assert_clean(&self) {
        if !self.is_clean() {
            let lines: Vec<String> = self.problems.iter().map(ToString::to_string).collect();
            panic!(
                "the ledgers do not reconcile ({} problems):\n{}",
                lines.len(),
                lines.join("\n")
            );
        }
    }
}

/// Checks the ledgers against the current holdings of `characters`.
pub async fn reconcile(pool: &PgPool, characters: &[i64]) -> Result<Report, sqlx::Error> {
    let mut tx = pool.begin().await?;
    sqlx::query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        .execute(&mut *tx)
        .await?;
    let mut report = Report::default();
    let mut add = |check: &'static str, detail: String| {
        report.problems.push(Problem { check, detail });
    };

    // ---- Crowns.
    let rows: Vec<(i64, i64, Option<i64>, Option<i64>)> = sqlx::query_as(
        "SELECT c.id, c.crowns,
                (SELECT sum(delta)::bigint FROM crown_ledger g WHERE g.character_id = c.id),
                (SELECT balance_after FROM crown_ledger g WHERE g.character_id = c.id
                 ORDER BY id DESC LIMIT 1)
         FROM characters c WHERE c.id = ANY($1) ORDER BY c.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for (id, crowns, sum, last) in rows {
        if sum.unwrap_or(0) != crowns || last.unwrap_or(0) != crowns {
            add(
                "crowns",
                format!(
                    "character {id} holds {crowns} but its ledger adds up to {} (last balance {})",
                    sum.unwrap_or(0),
                    last.map_or("none".into(), |b| b.to_string())
                ),
            );
        }
    }

    // ---- Item rows, replayed from the item ledger.
    let items: Vec<ItemReplay> = sqlx::query_as(
        "WITH scope AS (
             SELECT id FROM items WHERE owner_id = ANY($1)
             UNION
             SELECT item_id FROM item_ledger
             WHERE from_owner = ANY($1) OR to_owner = ANY($1)
             UNION
             SELECT related_item FROM item_ledger
             WHERE (from_owner = ANY($1) OR to_owner = ANY($1)) AND related_item IS NOT NULL
         ),
         units AS (
             SELECT l.item_id AS id,
                    CASE WHEN l.op IN ('create', 'grant', 'split') THEN l.quantity
                         WHEN l.op IN ('merge', 'consume', 'destroy') THEN -l.quantity
                         ELSE 0 END AS dq
             FROM item_ledger l JOIN scope s ON s.id = l.item_id
             UNION ALL
             SELECT l.related_item,
                    CASE l.op WHEN 'split' THEN -l.quantity ELSE l.quantity END
             FROM item_ledger l JOIN scope s ON s.id = l.related_item
             WHERE l.op IN ('split', 'merge')
         ),
         held AS (SELECT id, sum(dq)::bigint AS quantity FROM units GROUP BY id),
         place AS (
             SELECT DISTINCT ON (l.item_id) l.item_id AS id, l.to_owner, l.to_location
             FROM item_ledger l JOIN scope s ON s.id = l.item_id
             WHERE l.op IN ('create', 'grant', 'move', 'split')
             ORDER BY l.item_id, l.id DESC
         ),
         def AS (
             SELECT DISTINCT ON (l.item_id) l.item_id AS id, l.item_def
             FROM item_ledger l JOIN scope s ON s.id = l.item_id
             ORDER BY l.item_id, l.id DESC
         )
         SELECT s.id, coalesce(h.quantity, 0) AS ledger_quantity, p.to_owner AS ledger_owner,
                p.to_location AS ledger_location, d.item_def AS ledger_def,
                i.quantity::bigint AS quantity, i.owner_id, i.location, i.item_def
         FROM scope s
         LEFT JOIN held h ON h.id = s.id
         LEFT JOIN place p ON p.id = s.id
         LEFT JOIN def d ON d.id = s.id
         LEFT JOIN items i ON i.id = s.id
         ORDER BY s.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    let items_checked = items.len();
    for r in items {
        if let Some(problem) = r.problem() {
            add("item", problem);
        }
    }

    // ---- Places: mail, Exchange, worn, placed.
    let misplaced: Vec<(i64, String, String)> = sqlx::query_as(
        "SELECT i.id, i.location, CASE
             WHEN i.location = 'mail' THEN
                 (SELECT CASE WHEN count(*) = 1 AND bool_and(m.recipient_id = i.owner_id
                                                            AND m.collected_at IS NULL)
                              THEN NULL ELSE count(*)::text || ' mail attachment rows' END
                  FROM mail_attachments a JOIN mail m ON m.id = a.mail_id WHERE a.item_id = i.id)
             WHEN i.location = 'exchange' THEN
                 (SELECT CASE WHEN count(*) = 1 AND bool_and(x.seller_id = i.owner_id
                                                            AND x.item_def = i.item_def
                                                            AND x.quantity = i.quantity)
                              THEN NULL ELSE count(*)::text || ' matching active listings' END
                  FROM exchange_listings x WHERE x.item_id = i.id AND x.state = 'active')
             WHEN i.location = 'equipped' THEN
                 (SELECT CASE WHEN count(*) = 1 THEN NULL ELSE 'no equipment row' END
                  FROM equipment e WHERE e.item_id = i.id AND e.character_id = i.owner_id)
             WHEN i.location = 'dorm' THEN
                 (SELECT CASE WHEN count(*) = 1 THEN NULL ELSE 'no placement row' END
                  FROM dorm_placements d WHERE d.item_id = i.id AND d.owner_id = i.owner_id)
             ELSE
                 (SELECT CASE WHEN count(*) = 0 THEN NULL ELSE 'still attached to a mail' END
                  FROM mail_attachments a WHERE a.item_id = i.id)
         END AS issue
         FROM items i WHERE i.owner_id = ANY($1)
         ORDER BY i.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await
    .map(|rows: Vec<(i64, String, Option<String>)>| {
        rows.into_iter()
            .filter_map(|(id, loc, issue)| issue.map(|i| (id, loc, i)))
            .collect()
    })?;
    for (id, location, issue) in misplaced {
        add("place", format!("item {id} in '{location}': {issue}"));
    }

    // ---- Crowns travelling by owl.
    let mails: Vec<MailMoney> = sqlx::query_as(
        "SELECT m.id, m.kind, m.crowns, m.collected_at IS NOT NULL AS collected,
                (SELECT -sum(delta)::bigint FROM crown_ledger g
                 WHERE g.reason = 'mail_send' AND g.ref_id = m.id) AS sent,
                (SELECT sum(delta)::bigint FROM crown_ledger g
                 WHERE g.reason = 'mail_collect' AND g.ref_id = m.id) AS paid,
                CASE WHEN m.kind = 'exchange_sold' THEN
                    (SELECT x.price - coalesce(x.tax, 0) FROM exchange_listings x
                     WHERE x.id = m.ref_id AND x.state = 'sold')
                END AS proceeds
         FROM mail m
         WHERE m.recipient_id = ANY($1) OR m.sender_id = ANY($1)
         ORDER BY m.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for m in mails {
        let MailMoney {
            id,
            kind,
            crowns,
            collected,
            proceeds,
            ..
        } = m;
        let sent = m.sent.unwrap_or(0);
        let paid = m.paid.unwrap_or(0);
        if kind == crate::mail::KIND_LETTER && sent != crowns {
            add(
                "mail",
                format!("letter {id} holds {crowns} Crowns but its sender paid {sent}"),
            );
        }
        if kind == crate::mail::KIND_EXCHANGE_SOLD && proceeds != Some(crowns) {
            add(
                "mail",
                format!("sale mail {id} holds {crowns} Crowns, proceeds {proceeds:?}"),
            );
        }
        let expected = if collected { crowns } else { 0 };
        if paid != expected {
            add(
                "mail",
                format!(
                    "mail {id} (collected: {collected}) paid out {paid} of its {crowns} Crowns"
                ),
            );
        }
    }

    // ---- Listings: fees paid, sales paid for.
    let listings: Vec<ListingMoney> = sqlx::query_as(
        "SELECT x.id, x.state, x.price, x.fee,
                (SELECT -sum(delta)::bigint FROM crown_ledger g
                 WHERE g.reason = 'exchange_fee' AND g.ref_id = x.id) AS fee_paid,
                (SELECT -sum(delta)::bigint FROM crown_ledger g
                 WHERE g.reason = 'exchange_buy' AND g.ref_id = x.id) AS bought
         FROM exchange_listings x
         WHERE x.seller_id = ANY($1) OR x.buyer_id = ANY($1)
         ORDER BY x.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for ListingMoney {
        id,
        state,
        price,
        fee,
        fee_paid,
        bought,
    } in listings
    {
        if fee_paid.unwrap_or(0) != fee {
            add(
                "listing",
                format!("listing {id}: fee {fee}, paid {fee_paid:?}"),
            );
        }
        let expected = if state == crate::exchange::STATE_SOLD {
            price
        } else {
            0
        };
        if bought.unwrap_or(0) != expected {
            add(
                "listing",
                format!("listing {id} ({state}): buyers paid {bought:?}, expected {expected}"),
            );
        }
    }

    // ---- Trades: each side's Crowns, and nothing created.
    let trades: Vec<TradeMoney> = sqlx::query_as(
        "SELECT t.id, t.a_id, t.b_id, t.a_crowns, t.b_crowns,
                (SELECT sum(delta)::bigint FROM crown_ledger g
                 WHERE g.reason = 'trade' AND g.ref_id = t.id AND g.character_id = t.a_id) AS a_net,
                (SELECT sum(delta)::bigint FROM crown_ledger g
                 WHERE g.reason = 'trade' AND g.ref_id = t.id AND g.character_id = t.b_id) AS b_net,
                (SELECT sum(delta)::bigint FROM crown_ledger g
                 WHERE g.reason = 'trade' AND g.ref_id = t.id) AS total
         FROM trades t
         WHERE t.a_id = ANY($1) OR t.b_id = ANY($1)
         ORDER BY t.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for t in trades {
        let ok = t.total.unwrap_or(0) == 0
            && (t.a_id.is_none() || t.a_net.unwrap_or(0) == t.b_crowns - t.a_crowns)
            && (t.b_id.is_none() || t.b_net.unwrap_or(0) == t.a_crowns - t.b_crowns);
        if !ok {
            add(
                "trade",
                format!(
                    "trade {}: a gave {}, b gave {}, ledger a {:?} b {:?} total {:?}",
                    t.id, t.a_crowns, t.b_crowns, t.a_net, t.b_net, t.total
                ),
            );
        }
    }

    // ---- Every Crown row's reason, its sign and what it names.
    let odd: Vec<(i64, i64, String, i64)> = sqlx::query_as(
        "SELECT g.id, g.character_id, g.reason, g.delta FROM crown_ledger g
         WHERE g.character_id = ANY($1) AND NOT (
             (g.reason IN ('postage', 'mail_send', 'shop_order', 'exchange_fee', 'exchange_buy',
                           'character_deleted', 'club_found')
                  AND g.delta < 0)
             OR (g.reason IN ('starting_purse', 'mail_collect', 'find', 'class') AND g.delta > 0)
             OR g.reason IN ('trade', 'admin'))
         ORDER BY g.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for (id, character, reason, delta) in odd {
        add(
            "crowns",
            format!("ledger row {id} of character {character}: {delta} Crowns for '{reason}'"),
        );
    }
    let purses: Vec<(i64, i64)> = sqlx::query_as(
        "SELECT character_id, count(*) FROM crown_ledger
         WHERE character_id = ANY($1) AND reason = 'starting_purse'
         GROUP BY character_id HAVING count(*) > 1 ORDER BY character_id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for (character, n) in purses {
        add(
            "crowns",
            format!("character {character} got the starting purse {n} times"),
        );
    }
    // Payments naming a row that is not the payer's (an order, a listing or
    // a trade that does not exist, or someone else's).
    let orphans: Vec<(i64, String, Option<i64>)> = sqlx::query_as(
        "SELECT g.id, g.reason, g.ref_id FROM crown_ledger g
         WHERE g.character_id = ANY($1) AND CASE g.reason
             WHEN 'shop_order' THEN NOT EXISTS (SELECT 1 FROM shop_orders o
                                                WHERE o.id = g.ref_id AND o.character_id = g.character_id)
             WHEN 'exchange_fee' THEN NOT EXISTS (SELECT 1 FROM exchange_listings x
                                                  WHERE x.id = g.ref_id AND x.seller_id = g.character_id)
             WHEN 'exchange_buy' THEN NOT EXISTS (SELECT 1 FROM exchange_listings x
                                                  WHERE x.id = g.ref_id AND x.buyer_id = g.character_id)
             WHEN 'trade' THEN NOT EXISTS (SELECT 1 FROM trades t WHERE t.id = g.ref_id
                                           AND g.character_id IN (t.a_id, t.b_id))
             WHEN 'class' THEN NOT EXISTS (SELECT 1 FROM class_attendance a
                                           WHERE a.id = g.ref_id AND a.character_id = g.character_id
                                             AND a.rewarded_at IS NOT NULL)
             ELSE false END
         ORDER BY g.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for (id, reason, ref_id) in orphans {
        add(
            "crowns",
            format!("ledger row {id} pays for '{reason}' {ref_id:?}, which is not the payer's"),
        );
    }

    // ---- Owl-order catalogue: each order paid its total once, and the
    // owl brought exactly the units ordered.
    let orders: Vec<ShopOrderCheck> = sqlx::query_as(
        "SELECT o.id, o.total,
                (SELECT -sum(delta)::bigint FROM crown_ledger g
                 WHERE g.reason = 'shop_order' AND g.ref_id = o.id
                   AND g.character_id = o.character_id) AS paid,
                (SELECT coalesce(jsonb_object_agg(def, n), '{}'::jsonb) FROM (
                     SELECT (l->>'item_def') AS def, sum((l->>'quantity')::bigint) AS n
                     FROM jsonb_array_elements(o.lines) l GROUP BY 1) ordered) AS ordered,
                (SELECT coalesce(jsonb_object_agg(def, n), '{}'::jsonb) FROM (
                     SELECT item_def::text AS def, sum(quantity)::bigint AS n
                     FROM item_ledger i
                     WHERE i.reason = 'shop_order' AND i.ref_id = o.id AND i.op = 'create'
                     GROUP BY 1) made) AS made
         FROM shop_orders o
         WHERE o.character_id = ANY($1)
         ORDER BY o.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for o in orders {
        if o.paid != Some(o.total) {
            add(
                "order",
                format!("order {}: total {}, paid {:?}", o.id, o.total, o.paid),
            );
        }
        if o.ordered != o.made {
            add(
                "order",
                format!(
                    "order {}: ordered {} but the owl brought {}",
                    o.id, o.ordered.0, o.made.0
                ),
            );
        }
    }

    // ---- Letters still in a mailbox paid their postage exactly once.
    let unpaid: Vec<(i64, i64)> = sqlx::query_as(
        "SELECT m.id, (SELECT count(*) FROM crown_ledger g
                       WHERE g.reason = 'postage' AND g.ref_id = m.id
                         AND g.character_id = m.sender_id) AS paid
         FROM mail m
         WHERE m.kind = 'letter' AND m.sender_id IS NOT NULL
           AND (m.recipient_id = ANY($1) OR m.sender_id = ANY($1))
         ORDER BY m.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await
    .map(|rows: Vec<(i64, i64)>| {
        // Free postage (a data change) charges nothing and records nothing.
        let charged = crate::catalog::shipped().economy.mail.postage > 0;
        rows.into_iter()
            .filter(|&(_, paid)| paid > 1 || (paid == 0 && charged))
            .collect()
    })?;
    for (id, paid) in unpaid {
        add("mail", format!("letter {id} has {paid} postage payments"));
    }

    // ---- XP, class rewards, duel rewards and house points.
    let school = school_problems(&mut tx, characters).await?;
    report.xp_rows_checked = school.xp_rows;
    report.problems.extend(school.problems);
    // ---- Class Crowns, items and lore.
    report
        .problems
        .extend(class_v2_problems(&mut tx, characters).await?);
    // ---- Find and craft points, the House Cup reward.
    report
        .problems
        .extend(house_cup_problems(&mut tx, characters).await?);
    // ---- The secrets found and their rewards.
    report
        .problems
        .extend(discovery_problems(&mut tx, characters).await?);

    tx.commit().await?;
    report.items_checked = items_checked;
    // ---- NPCs: each daily gift row explains what it wrote (its own snapshot).
    report
        .problems
        .extend(crate::residents::audit::problems(pool, characters).await?);
    Ok(report)
}

/// What [`school_problems`] found.
struct SchoolAudit {
    problems: Vec<Problem>,
    xp_rows: usize,
}

/// The school checks of [`reconcile`]:
///
/// - **XP**: each ledger row starts where the one before it ended
///   (`xp_after - amount` is the previous `xp_after`, 0 for the first), the
///   character's `xp` is the sum of its amounts and the newest `xp_after`,
///   and its `level` is the level of that XP (`Progression::level_for`) and
///   the newest `level_after`.
/// - **Classes**: a rewarded attendance's `xp` is what its `class_attend`
///   and `class_bonus` rows granted (one `class_attend` row) and its
///   `house_points` the class house points awarded for it; an unrewarded
///   one has neither.
/// - **Duels**: each side's `a_xp` / `b_xp` is its `duel_*` row for the
///   duel, of the kind its result calls for; `house_points` went to the
///   winner (never for a draw).
/// - **House points**: a class or duel row names an attendance or a won
///   duel of the character it credits, in the character's house, with
///   positive points.
async fn school_problems(
    tx: &mut sqlx::PgConnection,
    characters: &[i64],
) -> Result<SchoolAudit, sqlx::Error> {
    let mut problems = Vec::new();
    let mut add = |check: &'static str, detail: String| problems.push(Problem { check, detail });
    let progression = &crate::catalog::shipped().progression;

    // Each row continues the one before it.
    let chain: Vec<XpLink> = sqlx::query_as(
        "SELECT id, character_id, amount, xp_after,
                lag(xp_after) OVER (PARTITION BY character_id ORDER BY id) AS before
         FROM xp_ledger WHERE character_id = ANY($1) ORDER BY id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    let xp_rows = chain.len();
    for l in chain {
        let before = l.before.unwrap_or(0);
        if i64::from(l.xp_after) - i64::from(l.amount) != i64::from(before) {
            add(
                "xp",
                format!(
                    "ledger row {} of character {} adds {} to reach {}, but the row before \
                     it ended at {before}",
                    l.id, l.character_id, l.amount, l.xp_after
                ),
            );
        }
    }
    let totals: Vec<XpTotal> = sqlx::query_as(
        "SELECT c.id, c.xp, c.level,
                (SELECT sum(amount)::bigint FROM xp_ledger x WHERE x.character_id = c.id) AS sum,
                (SELECT xp_after FROM xp_ledger x WHERE x.character_id = c.id
                 ORDER BY id DESC LIMIT 1) AS last_xp,
                (SELECT level_after FROM xp_ledger x WHERE x.character_id = c.id
                 ORDER BY id DESC LIMIT 1) AS last_level
         FROM characters c WHERE c.id = ANY($1) ORDER BY c.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for t in totals {
        let XpTotal {
            id,
            xp,
            level,
            sum,
            last_xp,
            last_level,
        } = t;
        let expected_level = progression.level_for(u32::try_from(xp).unwrap_or(0));
        if sum.unwrap_or(0) != i64::from(xp) || last_xp.unwrap_or(0) != xp {
            add(
                "xp",
                format!(
                    "character {id} has {xp} XP but its ledger adds up to {} (last total {last_xp:?})",
                    sum.unwrap_or(0)
                ),
            );
        }
        if i16::from(expected_level) != level || last_level.unwrap_or(1) != level {
            add(
                "xp",
                format!(
                    "character {id} is level {level}; {xp} XP is level {expected_level} \
                     (ledger says {last_level:?})"
                ),
            );
        }
    }

    // Class rewards against the XP and house-point rows that name them.
    let classes: Vec<ClassRewardCheck> = sqlx::query_as(
        "SELECT a.id, a.character_id, a.rewarded_at IS NOT NULL AS rewarded, a.xp,
                a.house_points AS points,
                (SELECT count(*) FROM xp_ledger x WHERE x.character_id = a.character_id
                   AND x.source = 'class_attend' AND x.ref_id = a.id) AS attend_rows,
                (SELECT coalesce(sum(amount), 0)::bigint FROM xp_ledger x
                 WHERE x.character_id = a.character_id
                   AND x.source IN ('class_attend', 'class_bonus') AND x.ref_id = a.id) AS granted,
                (SELECT coalesce(sum(points), 0)::bigint FROM house_points h
                 WHERE h.character_id = a.character_id AND h.source = 'class'
                   AND h.ref_id = a.id) AS awarded
         FROM class_attendance a WHERE a.character_id = ANY($1) ORDER BY a.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for c in classes {
        let ok = if c.rewarded {
            c.attend_rows == 1 && c.granted == i64::from(c.xp) && c.awarded == i64::from(c.points)
        } else {
            c.attend_rows == 0 && c.granted == 0 && c.awarded == 0
        };
        if !ok {
            add(
                "class",
                format!(
                    "attendance {} of character {} (rewarded: {}) records {} XP and {} points; \
                     the ledgers hold {} attendance grants, {} XP and {} points",
                    c.id,
                    c.character_id,
                    c.rewarded,
                    c.xp,
                    c.points,
                    c.attend_rows,
                    c.granted,
                    c.awarded
                ),
            );
        }
    }

    // Duel rewards.
    let duels: Vec<DuelRewards> = sqlx::query_as(
        "SELECT d.id, d.a_id, d.b_id, d.result, d.a_xp, d.b_xp, d.house_points,
                (SELECT string_agg(source || ':' || amount, ',') FROM xp_ledger x
                 WHERE x.character_id = d.a_id AND x.ref_id = d.id
                   AND x.source IN ('duel_win', 'duel_loss', 'duel_draw')) AS a_rows,
                (SELECT string_agg(source || ':' || amount, ',') FROM xp_ledger x
                 WHERE x.character_id = d.b_id AND x.ref_id = d.id
                   AND x.source IN ('duel_win', 'duel_loss', 'duel_draw')) AS b_rows,
                (SELECT coalesce(sum(points), 0)::bigint FROM house_points h
                 WHERE h.source = 'duel' AND h.ref_id = d.id) AS awarded,
                (SELECT count(*) FROM house_points h WHERE h.source = 'duel' AND h.ref_id = d.id
                   AND h.character_id IS DISTINCT FROM
                       CASE d.result WHEN 'a' THEN d.a_id WHEN 'b' THEN d.b_id END
                   -- A deleted winner's side is NULL: its points cannot be checked.
                   AND NOT (d.result = 'a' AND d.a_id IS NULL)
                   AND NOT (d.result = 'b' AND d.b_id IS NULL)) AS misawarded
         FROM duels d WHERE d.a_id = ANY($1) OR d.b_id = ANY($1) ORDER BY d.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for d in duels {
        if let Some(problem) = d.problem() {
            add("duel", problem);
        }
    }

    // Every house-point row of a class or duel names what earned it.
    let stray: Vec<(i64, String, Option<i64>)> = sqlx::query_as(
        "SELECT h.id, h.source, h.ref_id FROM house_points h
         JOIN characters c ON c.id = h.character_id
         WHERE h.character_id = ANY($1) AND (
             (h.source IN ('class', 'duel') AND h.points <= 0)
             -- characters.house is protocol::House's index.
             OR h.house <> (ARRAY['roarwing', 'burrowsby', 'skytalon', 'serpentyne'])[c.house + 1]
             OR (h.source = 'class' AND NOT EXISTS (
                 SELECT 1 FROM class_attendance a WHERE a.id = h.ref_id
                   AND a.character_id = h.character_id AND a.rewarded_at IS NOT NULL))
             OR (h.source = 'duel' AND NOT EXISTS (
                 SELECT 1 FROM duels d WHERE d.id = h.ref_id
                   AND h.character_id = CASE d.result WHEN 'a' THEN d.a_id WHEN 'b' THEN d.b_id END)))
         ORDER BY h.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for (id, source, ref_id) in stray {
        add(
            "house_points",
            format!("row {id} ({source}, ref {ref_id:?}) is not explained by what it names"),
        );
    }

    Ok(SchoolAudit { problems, xp_rows })
}

/// The House Cup checks of [`reconcile`]:
///
/// - **Find points**: a `find` row has positive points, no ref, and an item
///   row created for the character or a Crown row of theirs with reason
///   `find` at the same `at` (the find's own transaction).
/// - **Craft points**: a `craft` row has positive points and names a craft
///   of the character's whose recipe is of skill `sources.craft.min_skill`
///   or more.
/// - **Reward mails**: each `house_cup` mail names a `house_cups` row whose
///   winners include the recipient's house, at most one per character and
///   week, to a character with at least `reward.min_points` that week.
/// - **Reward items**: each `house_cup` item row creates the reward item
///   into the mail of a character whose house won the `house_cups` row it
///   names, at most once per character and week.
async fn house_cup_problems(
    tx: &mut sqlx::PgConnection,
    characters: &[i64],
) -> Result<Vec<Problem>, sqlx::Error> {
    let mut problems = Vec::new();
    let mut add = |check: &'static str, detail: String| problems.push(Problem { check, detail });
    let content = crate::catalog::shipped();
    let rules = &content.house_cup;
    let min_skill = rules.sources.craft.min_skill.unwrap_or(u8::MAX);
    let skilled: Vec<i32> = content
        .recipes
        .recipes
        .iter()
        .filter(|r| r.skill >= min_skill)
        .map(|r| i32::from(r.id))
        .collect();

    let points: Vec<(i64, String, Option<i64>)> = sqlx::query_as(
        "SELECT h.id, h.source, h.ref_id FROM house_points h
         WHERE h.character_id = ANY($1) AND h.source IN ('find', 'craft') AND (
             h.points <= 0
             OR (h.source = 'find' AND (h.ref_id IS NOT NULL OR NOT (
                 EXISTS (SELECT 1 FROM item_ledger l WHERE l.to_owner = h.character_id
                           AND l.at = h.at AND l.reason = 'find')
                 OR EXISTS (SELECT 1 FROM crown_ledger g WHERE g.character_id = h.character_id
                           AND g.at = h.at AND g.reason = 'find')
                 -- A secret found (its reward's point).
                 OR EXISTS (SELECT 1 FROM discoveries d WHERE d.character_id = h.character_id
                           AND d.at = h.at))))
             OR (h.source = 'craft' AND NOT EXISTS (
                 SELECT 1 FROM crafts c WHERE c.id = h.ref_id
                   AND c.character_id = h.character_id AND c.recipe_id = ANY($2))))
         ORDER BY h.id",
    )
    .bind(characters)
    .bind(&skilled)
    .fetch_all(&mut *tx)
    .await?;
    for (id, source, ref_id) in points {
        add(
            "house_points",
            format!(
                "row {id} ({source}, ref {ref_id:?}) is not explained by a {source} of its character"
            ),
        );
    }

    // characters.house is protocol::House's index; house_cups.winners hold ids.
    const HOUSE_ID: &str =
        "(ARRAY['roarwing', 'burrowsby', 'skytalon', 'serpentyne'])[c.house + 1]";
    let mails: Vec<(i64, i64, Option<i64>)> = sqlx::query_as(&format!(
        "SELECT m.id, m.recipient_id, m.ref_id FROM mail m JOIN characters c ON c.id = m.recipient_id
         WHERE m.recipient_id = ANY($1) AND m.kind = 'house_cup'
           AND (NOT EXISTS (SELECT 1 FROM house_cups k WHERE k.id = m.ref_id
                              AND {HOUSE_ID} = ANY(k.winners))
                OR EXISTS (SELECT 1 FROM mail o WHERE o.kind = 'house_cup' AND o.id <> m.id
                             AND o.recipient_id = m.recipient_id AND o.ref_id = m.ref_id)
                OR (SELECT coalesce(sum(p.points), 0) FROM house_points p
                      JOIN house_cups k ON k.id = m.ref_id
                    WHERE p.character_id = m.recipient_id
                      AND p.at >= k.week_start AND p.at < k.week_end) < $2)
         ORDER BY m.id"
    ))
    .bind(characters)
    .bind(i64::from(rules.reward.min_points))
    .fetch_all(&mut *tx)
    .await?;
    for (id, recipient, cup) in mails {
        add(
            "house_cup",
            format!(
                "reward mail {id} to character {recipient} names cup {cup:?}, which their house \
                 did not win, or is not their only reward mail for it, or they earned less \
                 than the reward's points that week"
            ),
        );
    }

    let items: Vec<(i64, i64, Option<i64>, Option<i64>)> = sqlx::query_as(&format!(
        "SELECT l.id, l.item_id, l.to_owner, l.ref_id FROM item_ledger l
         LEFT JOIN characters c ON c.id = l.to_owner
         WHERE l.to_owner = ANY($1) AND l.reason = 'house_cup'
           AND (l.op <> 'create' OR l.to_location IS DISTINCT FROM 'mail' OR l.item_def <> $2
                OR NOT EXISTS (SELECT 1 FROM house_cups k WHERE k.id = l.ref_id
                                 AND {HOUSE_ID} = ANY(k.winners))
                OR EXISTS (SELECT 1 FROM item_ledger o WHERE o.reason = 'house_cup'
                             AND o.id <> l.id AND o.to_owner = l.to_owner AND o.ref_id = l.ref_id))
         ORDER BY l.id"
    ))
    .bind(characters)
    .bind(i32::from(rules.reward.item))
    .fetch_all(&mut *tx)
    .await?;
    for (id, item, owner, cup) in items {
        add(
            "house_cup",
            format!(
                "item ledger row {id} (item {item}, to {owner:?}) names cup {cup:?}: not one \
                 reward created into the mail of a winner"
            ),
        );
    }
    Ok(problems)
}

/// One discovery with what the ledgers hold for it (`discovery_problems`).
#[derive(sqlx::FromRow)]
struct DiscoveryCheck {
    character: i64,
    secret: String,
    xp: i32,
    recipe: Option<i32>,
    /// Its `explore` XP rows, and what they added.
    grants: i64,
    granted: i64,
    /// Its recipe (if any) is known.
    knows: bool,
}

/// The discovery checks of [`reconcile`]:
///
/// - each `discoveries` row names a shipped secret, records its reward's
///   XP and recipe, has exactly one `explore` XP row of its character naming
///   the secret's `num` that added the row's XP, and its recipe (if any) is
///   known;
/// - each `explore` XP row names (by `num`) a secret its character found.
async fn discovery_problems(
    tx: &mut sqlx::PgConnection,
    characters: &[i64],
) -> Result<Vec<Problem>, sqlx::Error> {
    let mut problems = Vec::new();
    let mut add = |detail: String| {
        problems.push(Problem {
            check: "discovery",
            detail,
        })
    };
    let secrets = &crate::catalog::shipped().secrets.secrets;
    let ids: Vec<&str> = secrets.iter().map(|s| s.id.as_str()).collect();
    let nums: Vec<i64> = secrets.iter().map(|s| i64::from(s.num)).collect();
    let rows: Vec<DiscoveryCheck> = sqlx::query_as(
        "SELECT d.character_id AS character, d.secret, d.xp, d.recipe,
                (SELECT count(*) FROM xp_ledger x WHERE x.character_id = d.character_id
                   AND x.source = 'explore' AND x.ref_id = n.num) AS grants,
                (SELECT coalesce(sum(amount), 0)::bigint FROM xp_ledger x
                 WHERE x.character_id = d.character_id AND x.source = 'explore'
                   AND x.ref_id = n.num) AS granted,
                (d.recipe IS NULL OR EXISTS (SELECT 1 FROM known_recipes k
                   WHERE k.character_id = d.character_id AND k.recipe_id = d.recipe)) AS knows
         FROM discoveries d
         LEFT JOIN unnest($2::text[], $3::bigint[]) AS n(secret, num) ON n.secret = d.secret
         WHERE d.character_id = ANY($1) ORDER BY d.character_id, d.secret",
    )
    .bind(characters)
    .bind(&ids)
    .bind(&nums)
    .fetch_all(&mut *tx)
    .await?;
    for DiscoveryCheck {
        character,
        secret,
        xp,
        recipe,
        grants,
        granted,
        knows,
    } in rows
    {
        let Some(def) = secrets.iter().find(|s| s.id == secret) else {
            add(format!(
                "character {character} found {secret:?}, which is no secret"
            ));
            continue;
        };
        let reward_ok =
            i64::from(xp) == i64::from(def.reward.xp) && recipe == def.reward.recipe.map(i32::from);
        if !reward_ok || grants != 1 || granted != i64::from(xp) || !knows {
            add(format!(
                "character {character} found {secret} ({xp} XP, recipe {recipe:?}; the secret \
                 gives {} XP, recipe {:?}): {grants} explore XP rows adding {granted}, recipe \
                 known: {knows}",
                def.reward.xp, def.reward.recipe
            ));
        }
    }
    let stray: Vec<(i64, i64, Option<i64>)> = sqlx::query_as(
        "SELECT x.id, x.character_id, x.ref_id FROM xp_ledger x
         WHERE x.character_id = ANY($1) AND x.source = 'explore'
           AND NOT EXISTS (SELECT 1 FROM discoveries d
                 JOIN unnest($2::text[], $3::bigint[]) AS n(secret, num) ON n.secret = d.secret
               WHERE d.character_id = x.character_id AND n.num = x.ref_id)
         ORDER BY x.id",
    )
    .bind(characters)
    .bind(&ids)
    .bind(&nums)
    .fetch_all(&mut *tx)
    .await?;
    for (id, character, num) in stray {
        add(format!(
            "explore XP row {id} of character {character} names secret {num:?}, which they \
             did not find"
        ));
    }
    Ok(problems)
}

/// The class reward checks of [`reconcile`]:
///
/// - **Crowns**: a rewarded attendance's `crowns` is the sum of the
///   character's `class` Crown rows naming it; an unrewarded one has none.
///   (Rows naming no rewarded attendance of the payer are caught with the
///   other payments.)
/// - **Items**: every `class` item row is a `create` into a character's
///   inventory naming a rewarded attendance of that character, and for each
///   attendance the units created per item are its recorded `items`.
/// - **Lore**: each `lore_unlocks` row names a rewarded attendance of the
///   character whose `lore_id` it is, and an attendance with a `lore_id`
///   has that row.
async fn class_v2_problems(
    tx: &mut sqlx::PgConnection,
    characters: &[i64],
) -> Result<Vec<Problem>, sqlx::Error> {
    use std::collections::BTreeMap;

    use protocol::RewardItem;
    use sqlx::types::Json;

    let mut problems = Vec::new();
    let mut add = |detail: String| {
        problems.push(Problem {
            check: "class",
            detail,
        });
    };
    /// Attendance, character, rewarded, Crowns recorded, items recorded,
    /// Crowns the ledger paid for it.
    type Recorded = (i64, i64, bool, i32, Json<Vec<RewardItem>>, i64);
    /// Ref (attendance), owner, op, location, item, units.
    type Created = (Option<i64>, Option<i64>, String, Option<String>, i32, i64);
    /// Attendance, the unlock's attendance, the entry recorded, the entry unlocked.
    type Unlock = (Option<i64>, Option<i64>, Option<i32>, Option<i32>);
    let rows: Vec<Recorded> = sqlx::query_as(
        "SELECT a.id, a.character_id, a.rewarded_at IS NOT NULL, a.crowns, a.items,
                (SELECT coalesce(sum(delta), 0)::bigint FROM crown_ledger g
                 WHERE g.reason = 'class' AND g.ref_id = a.id
                   AND g.character_id = a.character_id) AS paid
         FROM class_attendance a WHERE a.character_id = ANY($1) ORDER BY a.id",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    // (attendance, character) -> the units recorded per item.
    let mut recorded: BTreeMap<(i64, i64), BTreeMap<i32, i64>> = BTreeMap::new();
    for (id, character, rewarded, crowns, items, paid) in rows {
        let expected = if rewarded { i64::from(crowns) } else { 0 };
        if paid != expected || (!rewarded && (crowns != 0 || !items.0.is_empty())) {
            add(format!(
                "attendance {id} of character {character} (rewarded: {rewarded}) records {crowns} Crowns; its class Crown rows add up to {paid}"
            ));
        }
        let mut per_item: BTreeMap<i32, i64> = BTreeMap::new();
        for it in &items.0 {
            *per_item.entry(i32::from(it.item_def)).or_default() += i64::from(it.quantity);
        }
        if rewarded {
            recorded.insert((id, character), per_item);
        }
    }
    let created: Vec<Created> = sqlx::query_as(
        "SELECT ref_id, to_owner, op, to_location, item_def, sum(quantity)::bigint
             FROM item_ledger WHERE reason = 'class' AND to_owner = ANY($1)
             GROUP BY ref_id, to_owner, op, to_location, item_def
             ORDER BY ref_id, item_def",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    let mut ledgered: BTreeMap<(i64, i64), BTreeMap<i32, i64>> = BTreeMap::new();
    for (ref_id, owner, op, location, item_def, units) in created {
        let (Some(att), Some(owner)) = (ref_id, owner) else {
            add(format!(
                "{units} units of item {item_def} were created for a class with no attendance or owner ({ref_id:?}, {owner:?})"
            ));
            continue;
        };
        if op != "create" || location.as_deref() != Some(crate::inventory::LOCATION_INVENTORY) {
            add(format!(
                "class item rows of attendance {att}: '{op}' into {location:?}, not a create into the inventory"
            ));
        }
        if !recorded.contains_key(&(att, owner)) {
            add(format!(
                "{units} units of item {item_def} name attendance {att}, which is not a rewarded class of character {owner}"
            ));
            continue;
        }
        *ledgered
            .entry((att, owner))
            .or_default()
            .entry(item_def)
            .or_default() += units;
    }
    for (key, items) in &recorded {
        let given = ledgered.get(key).cloned().unwrap_or_default();
        if &given != items {
            add(format!(
                "attendance {} of character {} records items {items:?}; the ledger created {given:?}",
                key.0, key.1
            ));
        }
    }
    let lore: Vec<Unlock> = sqlx::query_as(
        "SELECT a.id, l.attendance_id, a.lore_id, l.lore_id
         FROM (SELECT * FROM class_attendance WHERE character_id = ANY($1)
                 AND (lore_id IS NOT NULL OR rewarded_at IS NULL)) a
         FULL JOIN (SELECT * FROM lore_unlocks WHERE character_id = ANY($1)) l
           ON l.attendance_id = a.id AND l.character_id = a.character_id
         WHERE a.id IS NULL OR l.attendance_id IS NULL OR a.lore_id IS DISTINCT FROM l.lore_id
            OR a.rewarded_at IS NULL",
    )
    .bind(characters)
    .fetch_all(&mut *tx)
    .await?;
    for (att, unlock_att, recorded, unlocked) in lore {
        // An unrewarded attendance without an unlock is fine.
        if unlock_att.is_none() && recorded.is_none() {
            continue;
        }
        add(format!(
            "lore: attendance {att:?} records entry {recorded:?}; the unlock naming attendance {unlock_att:?} is entry {unlocked:?}"
        ));
    }
    Ok(problems)
}

/// One XP ledger row and where the character's row before it ended.
#[derive(Debug, sqlx::FromRow)]
struct XpLink {
    id: i64,
    character_id: i64,
    amount: i32,
    xp_after: i32,
    before: Option<i32>,
}

/// A character's XP and level against its XP ledger.
#[derive(Debug, sqlx::FromRow)]
struct XpTotal {
    id: i64,
    xp: i32,
    level: i16,
    sum: Option<i64>,
    last_xp: Option<i32>,
    last_level: Option<i16>,
}

/// A class attendance's recorded rewards and the ledger rows that name it.
#[derive(Debug, sqlx::FromRow)]
struct ClassRewardCheck {
    id: i64,
    character_id: i64,
    rewarded: bool,
    xp: i32,
    points: i32,
    attend_rows: i64,
    granted: i64,
    awarded: i64,
}

/// A duel's recorded rewards and the ledger rows that name it.
#[derive(Debug, sqlx::FromRow)]
struct DuelRewards {
    id: i64,
    /// `None` for a side whose character was deleted (its rows are not checked).
    a_id: Option<i64>,
    b_id: Option<i64>,
    result: String,
    a_xp: i32,
    b_xp: i32,
    house_points: i32,
    /// `source:amount` of each side's `duel_*` XP rows for the duel.
    a_rows: Option<String>,
    b_rows: Option<String>,
    /// House points awarded for the duel, and rows crediting anyone but the winner.
    awarded: i64,
    misawarded: i64,
}

impl DuelRewards {
    fn problem(&self) -> Option<String> {
        let id = self.id;
        let kind = |won: bool| match (self.result.as_str(), won) {
            ("draw", _) => "duel_draw",
            (_, true) => "duel_win",
            (_, false) => "duel_loss",
        };
        // A duel with no XP (too short, capped to nothing by the pair limit)
        // may have no rows or a row of 0; otherwise exactly one of the right kind.
        let side = |who: &str, xp: i32, rows: &Option<String>, won: bool| -> Option<String> {
            let expected = format!("{}:{xp}", kind(won));
            match rows.as_deref() {
                None if xp == 0 => None,
                Some(r) if r == expected => None,
                r => Some(format!(
                    "duel {id}: {who} recorded {xp} XP ({}), ledger rows {r:?}",
                    kind(won)
                )),
            }
        };
        let a = self
            .a_id
            .and_then(|_| side("a", self.a_xp, &self.a_rows, self.result == "a"));
        let b = || {
            self.b_id
                .and_then(|_| side("b", self.b_xp, &self.b_rows, self.result == "b"))
        };
        a.or_else(b).or_else(|| {
            (self.awarded != i64::from(self.house_points)
                || self.misawarded > 0
                || (self.result == "draw" && self.house_points != 0))
                .then(|| {
                    format!(
                        "duel {id} ({}): records {} house points, {} awarded, {} to someone \
                             other than the winner (a {}, b {})",
                        self.result,
                        self.house_points,
                        self.awarded,
                        self.misawarded,
                        self.a_id.map_or("deleted".into(), |id| id.to_string()),
                        self.b_id.map_or("deleted".into(), |id| id.to_string())
                    )
                })
        })
    }
}

/// An owl order's total and units against the ledgers.
#[derive(Debug, sqlx::FromRow)]
struct ShopOrderCheck {
    id: i64,
    total: i64,
    paid: Option<i64>,
    /// `{ "<item_def>": units }` of the order's lines and of the items created for it.
    ordered: sqlx::types::Json<serde_json::Value>,
    made: sqlx::types::Json<serde_json::Value>,
}

/// A mail's Crowns and what the ledger says about them.
#[derive(Debug, sqlx::FromRow)]
struct MailMoney {
    id: i64,
    kind: String,
    crowns: i64,
    collected: bool,
    /// What its sender's `mail_send` rows took.
    sent: Option<i64>,
    /// What `mail_collect` rows paid out.
    paid: Option<i64>,
    /// A sale mail: the listing's price minus its tax.
    proceeds: Option<i64>,
}

/// A listing's fee and price and what the ledger says was paid.
#[derive(Debug, sqlx::FromRow)]
struct ListingMoney {
    id: i64,
    state: String,
    price: i64,
    fee: i64,
    fee_paid: Option<i64>,
    bought: Option<i64>,
}

/// A trade's Crowns and its ledger rows (net per side, and in all).
#[derive(Debug, sqlx::FromRow)]
struct TradeMoney {
    id: i64,
    a_id: Option<i64>,
    b_id: Option<i64>,
    a_crowns: i64,
    b_crowns: i64,
    a_net: Option<i64>,
    b_net: Option<i64>,
    total: Option<i64>,
}

/// One item's replay ([`reconcile`]).
#[derive(Debug, sqlx::FromRow)]
struct ItemReplay {
    id: i64,
    ledger_quantity: i64,
    ledger_owner: Option<i64>,
    ledger_location: Option<String>,
    ledger_def: Option<i32>,
    quantity: Option<i64>,
    owner_id: Option<i64>,
    location: Option<String>,
    item_def: Option<i32>,
}

impl ItemReplay {
    fn problem(&self) -> Option<String> {
        let id = self.id;
        match self.quantity {
            None if self.ledger_quantity == 0 => None,
            None => Some(format!(
                "item {id} is gone but its ledger says it holds {} units",
                self.ledger_quantity
            )),
            Some(q) if q != self.ledger_quantity => Some(format!(
                "item {id} holds {q} units, its ledger {}",
                self.ledger_quantity
            )),
            Some(_) if self.ledger_owner != self.owner_id => Some(format!(
                "item {id} is owned by {:?}, its ledger says {:?}",
                self.owner_id, self.ledger_owner
            )),
            Some(_) if self.ledger_location != self.location => Some(format!(
                "item {id} is in {:?}, its ledger says {:?}",
                self.location, self.ledger_location
            )),
            Some(_) if self.ledger_def != self.item_def => Some(format!(
                "item {id} is definition {:?}, its ledger says {:?}",
                self.item_def, self.ledger_def
            )),
            Some(_) => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn replay() -> ItemReplay {
        ItemReplay {
            id: 7,
            ledger_quantity: 3,
            ledger_owner: Some(1),
            ledger_location: Some("inventory".into()),
            ledger_def: Some(3001),
            quantity: Some(3),
            owner_id: Some(1),
            location: Some("inventory".into()),
            item_def: Some(3001),
        }
    }

    fn duel() -> DuelRewards {
        DuelRewards {
            id: 9,
            a_id: Some(1),
            b_id: Some(2),
            result: "a".into(),
            a_xp: 40,
            b_xp: 15,
            house_points: 2,
            a_rows: Some("duel_win:40".into()),
            b_rows: Some("duel_loss:15".into()),
            awarded: 2,
            misawarded: 0,
        }
    }

    #[test]
    fn a_duel_record_must_match_its_grants() {
        assert_eq!(duel().problem(), None);
        // Too short to reward: no rows, nothing recorded.
        let short = DuelRewards {
            a_xp: 0,
            b_xp: 0,
            house_points: 0,
            a_rows: None,
            b_rows: None,
            awarded: 0,
            ..duel()
        };
        assert_eq!(short.problem(), None);
        // Capped to nothing still leaves a row of 0.
        let capped = DuelRewards {
            b_xp: 0,
            b_rows: Some("duel_loss:0".into()),
            ..duel()
        };
        assert_eq!(capped.problem(), None);
        let wrong_kind = DuelRewards {
            a_rows: Some("duel_loss:40".into()),
            ..duel()
        };
        assert!(wrong_kind.problem().unwrap().contains("duel_win"));
        let granted_twice = DuelRewards {
            b_rows: Some("duel_loss:15,duel_loss:15".into()),
            ..duel()
        };
        assert!(granted_twice.problem().is_some());
        let loser_points = DuelRewards {
            misawarded: 1,
            ..duel()
        };
        assert!(loser_points.problem().unwrap().contains("house points"));
        let draw_points = DuelRewards {
            result: "draw".into(),
            a_rows: Some("duel_draw:40".into()),
            b_rows: Some("duel_draw:15".into()),
            ..duel()
        };
        assert!(draw_points.problem().is_some());
        // The loser was deleted: its side has no rows left to check.
        let deleted_loser = DuelRewards {
            b_id: None,
            b_rows: None,
            ..duel()
        };
        assert_eq!(deleted_loser.problem(), None);
        let deleted_but_wrong = DuelRewards {
            b_id: None,
            b_rows: None,
            a_rows: Some("duel_loss:40".into()),
            ..duel()
        };
        assert!(deleted_but_wrong.problem().is_some());
    }

    #[test]
    fn an_item_row_must_match_its_replay() {
        assert_eq!(replay().problem(), None);
        let gone_ok = ItemReplay {
            ledger_quantity: 0,
            quantity: None,
            ..replay()
        };
        assert_eq!(gone_ok.problem(), None);
        let vanished = ItemReplay {
            quantity: None,
            ..replay()
        };
        assert!(vanished.problem().unwrap().contains("gone"));
        let duplicated = ItemReplay {
            quantity: Some(4),
            ..replay()
        };
        assert!(duplicated.problem().unwrap().contains("4 units"));
        let moved = ItemReplay {
            owner_id: Some(2),
            ..replay()
        };
        assert!(moved.problem().unwrap().contains("owned by"));
        let relocated = ItemReplay {
            location: Some("mail".into()),
            ..replay()
        };
        assert!(relocated.problem().unwrap().contains("is in"));
    }
}
