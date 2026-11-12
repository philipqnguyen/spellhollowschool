-- Phase 2a, the school day (docs/architecture.md "Phase 2a"): nine subject
-- skill tracks, class rewards v2 (Crowns, items, recipes, lore, a fortune),
-- pick answers, the Divination fortune effect and lore unlocks.
--
-- Pre-launch: no backfill and no data kept for its own sake. Every change
-- below only widens a CHECK or adds columns with defaults, so existing dev
-- rows stay valid as they are.

-- Skill tracks: the nine school subjects (sim_core::classes::Subject). The
-- three crafting disciplines keep their ids.
ALTER TABLE skills DROP CONSTRAINT IF EXISTS skills_discipline_check;
ALTER TABLE skills ADD CONSTRAINT skills_discipline_check CHECK (discipline IN (
    'charms', 'transfiguration', 'potions', 'defence', 'history', 'divination',
    'astronomy', 'herbology', 'creatures'));

-- Class attendance: answers (traces and picks accepted) replace traces; what
-- the v2 rewards granted, so a retried reward reports them
-- (`RewardOutcome::AlreadyRewarded`) and the audit can match them to the
-- ledger rows with reason 'class' (ref: the attendance id).
ALTER TABLE class_attendance RENAME COLUMN traces TO answers;
ALTER TABLE class_attendance
    ADD COLUMN crowns integer NOT NULL DEFAULT 0 CHECK (crowns >= 0),
    -- [{ "item_def": u16, "quantity": u32 }], as `protocol::RewardItem`.
    ADD COLUMN items    jsonb   NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(items) = 'array'),
    -- data/crafting/recipes.json ids learned in this class.
    ADD COLUMN recipes  integer[] NOT NULL DEFAULT '{}',
    -- data/classes/lore.json id unlocked in this class.
    ADD COLUMN lore_id  integer CHECK (lore_id BETWEEN 0 AND 65535),
    -- protocol::FortuneKind id drawn in this class.
    ADD COLUMN fortune  text CHECK (fortune IN ('luck', 'quick_hands', 'keen_mind'));

-- The Divination fortune: an effect that is not a potion (item_def 0), with
-- its kind.
ALTER TABLE active_effects DROP CONSTRAINT IF EXISTS active_effects_kind_check;
ALTER TABLE active_effects ADD CONSTRAINT active_effects_kind_check CHECK (kind IN (
    'glow', 'chameleon', 'shrinking', 'swelling', 'babbling', 'mimic_draught', 'fortunes_draught',
    'pick_me_up_tonic', 'invisibility', 'fortune'));
ALTER TABLE active_effects
    ADD COLUMN fortune text CHECK (fortune IN ('luck', 'quick_hands', 'keen_mind')),
    ADD CONSTRAINT active_effects_fortune CHECK ((kind = 'fortune') = (fortune IS NOT NULL));

-- Lore entries a character has unlocked in class (`GET /api/lore`), each
-- once; `attendance_id` is the class that unlocked it.
CREATE TABLE lore_unlocks (
    character_id  bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    lore_id       integer     NOT NULL CHECK (lore_id BETWEEN 0 AND 65535),
    unlocked_at   timestamptz NOT NULL DEFAULT now(),
    attendance_id bigint      NOT NULL REFERENCES class_attendance (id) ON DELETE CASCADE,
    PRIMARY KEY (character_id, lore_id)
);

-- The lore page is newest first per character (keyset on unlocked_at, lore_id).
CREATE INDEX lore_unlocks_page_idx ON lore_unlocks (character_id, unlocked_at DESC, lore_id);
CREATE UNIQUE INDEX lore_unlocks_attendance_idx ON lore_unlocks (attendance_id);
