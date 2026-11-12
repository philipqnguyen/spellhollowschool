-- Phase 2b, the House Cup and the common rooms (docs/architecture.md
-- "Phase 2b"; data/house_cup.json): two more house-point sources, the weekly
-- cup's closed weeks, the reward mail's kind and the first-day tour's step.
--
-- Pre-launch: no backfill. Every change below only widens a CHECK or adds a
-- table, an index or a column with a default, so existing dev rows stay
-- valid as they are (old house points fall in past weeks, which nobody
-- closes: the close job only closes the week before the current one).

-- House points from exploration finds (a find that turned something up;
-- ref_id NULL, made in the find's transaction, so the find's ledger rows
-- share its `at`) and crafting (a craft of skill 2 or more; ref_id =
-- crafts.id, once per craft by house_points_once).
ALTER TABLE house_points DROP CONSTRAINT IF EXISTS house_points_source_check;
ALTER TABLE house_points ADD CONSTRAINT house_points_source_check
    CHECK (source IN ('class', 'duel', 'admin', 'find', 'craft'));

-- The week's standings sum house_points over [week_start, week_end) by `at`.
CREATE INDEX house_points_at_idx ON house_points (at);

-- One row per closed cup week, written once by the close job in the same
-- transaction as the reward mails (so a crash mid-close leaves no row and
-- the next run closes it again). `id` is what the reward's ledger rows and
-- mails name (ledger reason 'house_cup', mail kind 'house_cup', ref_id).
CREATE TABLE house_cups (
    week_start timestamptz PRIMARY KEY,
    id         bigint      GENERATED ALWAYS AS IDENTITY UNIQUE,
    week_end   timestamptz NOT NULL,
    -- protocol::House ids on the top score (ties shared); empty when no house
    -- scored that week.
    winners    text[]      NOT NULL
        CHECK (winners <@ ARRAY['roarwing', 'burrowsby', 'skytalon', 'serpentyne']::text[]),
    -- { "<house id>": points } for all four houses.
    totals     jsonb       NOT NULL CHECK (jsonb_typeof(totals) = 'object'),
    closed_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (week_end > week_start)
);

CREATE TRIGGER house_cups_append_only BEFORE UPDATE OR DELETE ON house_cups
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();

-- The reward arrives by owl as system mail of its own kind.
ALTER TABLE mail DROP CONSTRAINT IF EXISTS mail_kind_check;
ALTER TABLE mail ADD CONSTRAINT mail_kind_check CHECK (kind IN (
    'letter', 'shop', 'exchange_sold', 'exchange_bought', 'exchange_returned',
    'exchange_expired', 'system', 'house_cup'));

-- The first-day tour (plan decision 15): 0 = not yet toured (the client runs
-- the tour on the next login), 1 = toured or skipped (the one-time "class
-- starting" prompt is still due), 2 = done (the prompt was shown). Only
-- rises (`POST /api/characters/me/tour`).
ALTER TABLE characters
    ADD COLUMN tour_step smallint NOT NULL DEFAULT 0 CHECK (tour_step BETWEEN 0 AND 2);
