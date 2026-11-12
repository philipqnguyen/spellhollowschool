-- Milestone 1d: player levels and XP, learned spells and loadouts, class
-- sessions and attendance, house points, duels and daily caps.
--
-- Existing characters start at level 1 with 0 XP (their 1c crafting skill
-- XP stays skill XP and is not converted).

-- The character's total XP and the level it gives (data/progression.json;
-- kept in the row so nameplates and equip checks need no recomputation).
ALTER TABLE characters
    ADD COLUMN xp    integer  NOT NULL DEFAULT 0 CHECK (xp >= 0),
    ADD COLUMN level smallint NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 20);

-- Every XP grant, append-only like the item and Crown ledgers (no
-- foreign key: the ledger outlives what it describes). `requested` is what
-- the source asked for, `amount` what was added after any daily cap (it may
-- be 0). A source that names a `ref_id` (a class session, a duel, a craft)
-- grants once per character: retries answer the first grant.
CREATE TABLE xp_ledger (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    character_id bigint      NOT NULL,
    source       text        NOT NULL CHECK (source IN ('class_attend', 'class_bonus', 'craft',
                                                       'find', 'duel_win', 'duel_loss',
                                                       'duel_draw', 'admin')),
    ref_id       bigint,
    requested    integer     NOT NULL CHECK (requested >= 0),
    amount       integer     NOT NULL CHECK (amount >= 0 AND amount <= requested),
    xp_after     integer     NOT NULL CHECK (xp_after >= 0),
    level_after  smallint    NOT NULL CHECK (level_after BETWEEN 1 AND 20)
);

CREATE UNIQUE INDEX xp_ledger_once ON xp_ledger (character_id, source, ref_id)
    WHERE ref_id IS NOT NULL;
CREATE INDEX xp_ledger_character_idx ON xp_ledger (character_id, id);

CREATE TRIGGER xp_ledger_append_only BEFORE UPDATE OR DELETE ON xp_ledger
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();

-- Per character, per UTC day, per capped group: how much was used. XP
-- groups ('duel', 'craft', 'find', `XpSource::cap_group`) count XP; the
-- dueling counters count duels ('duel_points' wins that gave house points,
-- 'pair:<other character id>' rewarded duels against one opponent).
CREATE TABLE daily_caps (
    character_id bigint  NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    day          date    NOT NULL,
    cap          text    NOT NULL CHECK (char_length(cap) BETWEEN 1 AND 40),
    used         integer NOT NULL DEFAULT 0 CHECK (used >= 0),
    PRIMARY KEY (character_id, day, cap)
);

-- Old days are cleaned up by the minute job.
CREATE INDEX daily_caps_day_idx ON daily_caps (day);

-- Spells a character has learned (data/spells.json ids).
CREATE TABLE known_spells (
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    spell_id     integer     NOT NULL CHECK (spell_id BETWEEN 0 AND 65535),
    -- Where it was learned: a class id or a dueling venue id.
    taught_at    text        NOT NULL,
    learned_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (character_id, spell_id)
);

-- The dueling loadout a character chose (no row: the default loadout,
-- `Spells::default_loadout`). At most LOADOUT_MAX spells, in slot order.
CREATE TABLE loadouts (
    character_id bigint      PRIMARY KEY REFERENCES characters (id) ON DELETE CASCADE,
    spells       integer[]   NOT NULL CHECK (cardinality(spells) <= 8),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- One row per class section that ran (opened when its first student entered).
CREATE TABLE class_sessions (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    class_id    text        NOT NULL CHECK (char_length(class_id) BETWEEN 1 AND 64),
    -- sim_core::clock::SessionKey::id.
    session_key bigint      NOT NULL,
    section     smallint    NOT NULL CHECK (section >= 0),
    starts_at   timestamptz NOT NULL,
    ends_at     timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT class_sessions_section UNIQUE (class_id, session_key, section)
);

-- Who attended which session (one row per session and character, whichever
-- section), with the score and the rewards granted. `rewarded_at` is set in
-- the transaction that grants the rewards, so they are granted once.
CREATE TABLE class_attendance (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id   bigint      NOT NULL REFERENCES class_sessions (id),
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    class_id     text        NOT NULL,
    session_key  bigint      NOT NULL,
    joined_at    timestamptz NOT NULL DEFAULT now(),
    score_pct    smallint    CHECK (score_pct BETWEEN 0 AND 100),
    traces       smallint    NOT NULL DEFAULT 0 CHECK (traces >= 0),
    xp           integer     NOT NULL DEFAULT 0 CHECK (xp >= 0),
    skill_xp     integer     NOT NULL DEFAULT 0 CHECK (skill_xp >= 0),
    house_points integer     NOT NULL DEFAULT 0 CHECK (house_points >= 0),
    rewarded_at  timestamptz,
    CONSTRAINT class_attendance_once UNIQUE (class_id, session_key, character_id)
);

CREATE INDEX class_attendance_character_idx ON class_attendance (character_id, id);

-- House points, append-only. `character_id` is who earned them (NULL for
-- staff awards later). A source that names a `ref_id` (a class session, a
-- duel) awards once per character.
CREATE TABLE house_points (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    house        text        NOT NULL CHECK (house IN ('roarwing', 'burrowsby', 'skytalon', 'serpentyne')),
    points       integer     NOT NULL CHECK (points <> 0),
    character_id bigint,
    source       text        NOT NULL CHECK (source IN ('class', 'duel', 'admin')),
    ref_id       bigint
);

CREATE UNIQUE INDEX house_points_once ON house_points (source, ref_id, character_id)
    WHERE ref_id IS NOT NULL;
CREATE INDEX house_points_house_idx ON house_points (house, at);
CREATE INDEX house_points_character_idx ON house_points (character_id, id);

CREATE TRIGGER house_points_append_only BEFORE UPDATE OR DELETE ON house_points
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();

-- One row per finished duel. `duel_key` is the arena's random 63-bit key,
-- unique, so recording a duel twice is impossible (a retry answers the first).
CREATE TABLE duels (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    duel_key     bigint      NOT NULL UNIQUE,
    venue        text        NOT NULL,
    a_id         bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    b_id         bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    result       text        NOT NULL CHECK (result IN ('a', 'b', 'draw')),
    reason       text        NOT NULL CHECK (reason IN ('poise', 'timeout', 'forfeit', 'disconnect')),
    a_poise      smallint    NOT NULL CHECK (a_poise >= 0),
    b_poise      smallint    NOT NULL CHECK (b_poise >= 0),
    fought_ms    integer     NOT NULL CHECK (fought_ms >= 0),
    a_xp         integer     NOT NULL DEFAULT 0 CHECK (a_xp >= 0),
    b_xp         integer     NOT NULL DEFAULT 0 CHECK (b_xp >= 0),
    house_points integer     NOT NULL DEFAULT 0 CHECK (house_points >= 0),
    started_at   timestamptz NOT NULL,
    ended_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (a_id <> b_id)
);

CREATE INDEX duels_a_idx ON duels (a_id, id);
CREATE INDEX duels_b_idx ON duels (b_id, id);
