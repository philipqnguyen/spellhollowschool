-- Milestone 1c: crafting skills and known recipes, crafts, exploration find
-- cooldowns and active potion effects.

-- Skill XP per character and discipline (sim_core::crafting::Discipline);
-- the level is computed from the XP (data/crafting/recipes.json skill_levels).
-- A missing row means 0 XP.
CREATE TABLE skills (
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    discipline   text        NOT NULL CHECK (discipline IN ('charms', 'transfiguration', 'potions')),
    xp           integer     NOT NULL DEFAULT 0 CHECK (xp >= 0),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (character_id, discipline)
);

-- Recipes learned from patterns (recipes without a pattern are known by
-- everyone and have no row).
CREATE TABLE known_recipes (
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    -- data/crafting/recipes.json id (u16).
    recipe_id    integer     NOT NULL CHECK (recipe_id BETWEEN 0 AND 65535),
    learned_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (character_id, recipe_id)
);

-- One row per craft (the ledger's ref_id for 'craft' movements).
CREATE TABLE crafts (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_id   bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    recipe_id      integer     NOT NULL CHECK (recipe_id BETWEEN 0 AND 65535),
    -- The item made (or restyled), and how many units.
    output_item_id bigint,
    quantity       integer     NOT NULL CHECK (quantity >= 1),
    xp             integer     NOT NULL CHECK (xp >= 0),
    created_at     timestamptz NOT NULL DEFAULT now(),
    -- The client's request id: a retried craft is answered, not repeated.
    client_key     text        CHECK (client_key IS NULL OR char_length(client_key) BETWEEN 1 AND 64),
    CONSTRAINT crafts_client_key UNIQUE (character_id, client_key)
);

CREATE INDEX crafts_character_idx ON crafts (character_id, id);

-- Busy until: a craft's craft_secs after it was made (another craft before
-- then answers 409 crafting_busy).
ALTER TABLE characters ADD COLUMN craft_ready_at timestamptz;

-- When each character may search each find spot again (layout interactable id).
CREATE TABLE find_cooldowns (
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    spot_id      text        NOT NULL CHECK (char_length(spot_id) BETWEEN 1 AND 64),
    ready_at     timestamptz NOT NULL,
    PRIMARY KEY (character_id, spot_id)
);

-- Old cooldowns are cleaned up by the effects/finds janitor.
CREATE INDEX find_cooldowns_ready_idx ON find_cooldowns (ready_at);

-- Potion effects that are active (they survive a relog until they expire).
-- One per kind per character; Shrinking and Swelling replace each other.
CREATE TABLE active_effects (
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    -- protocol::EffectKind id ('glow', 'pick_me_up_tonic', ...).
    kind         text        NOT NULL CHECK (kind IN ('glow', 'chameleon', 'shrinking', 'swelling',
                                                      'babbling', 'mimic_draught', 'fortunes_draught', 'pick_me_up_tonic',
                                                      'invisibility')),
    -- What viewers see (protocol::VisibleEffect as JSON), or NULL for none.
    visible      jsonb,
    -- Movement speed in percent while active (100 = unchanged).
    speed_pct    smallint    NOT NULL DEFAULT 100 CHECK (speed_pct BETWEEN 100 AND 200),
    -- The potion drunk (catalog id), for the HUD.
    item_def     integer     NOT NULL CHECK (item_def BETWEEN 0 AND 65535),
    started_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    PRIMARY KEY (character_id, kind),
    CONSTRAINT active_effects_window CHECK (expires_at > started_at)
);

CREATE INDEX active_effects_expiry_idx ON active_effects (expires_at);
