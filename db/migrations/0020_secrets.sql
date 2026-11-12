-- Phase 3d, secrets and the map (docs/architecture.md "Phase 3d";
-- data/world/secrets.json, sim_core::secrets).
--
-- Pre-launch: no backfill. Two new tables and a widened CHECK; existing dev
-- rows stay valid (every character starts with an unexplored map).

-- The map's reveals: a place (a layout surface, by its stable string id, so a
-- later layout edit never scrambles a player's map) is revealed once the
-- character has stood on it. Written in batches with the 30 s position save
-- and at logout (ON CONFLICT DO NOTHING); `at` is the save that wrote it.
CREATE TABLE map_reveals (
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    surface      text        NOT NULL CHECK (char_length(surface) BETWEEN 1 AND 64),
    at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (character_id, surface)
);

-- Secrets found, once per character (the primary key makes the reward once):
-- the row is written in the same transaction as the reward it records (the
-- XP, `xp_ledger` source 'explore' with ref_id = the secret's `num`; the
-- House Cup point, source 'find' under its daily cap; the recipe learned,
-- `known_recipes`, NULL when the secret teaches none). The audit checks every
-- explore XP grant against its discovery.
CREATE TABLE discoveries (
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    secret       text        NOT NULL CHECK (char_length(secret) BETWEEN 1 AND 64),
    at           timestamptz NOT NULL DEFAULT now(),
    xp           integer     NOT NULL CHECK (xp >= 0),
    recipe       integer     CHECK (recipe BETWEEN 0 AND 65535),
    PRIMARY KEY (character_id, secret)
);

-- XP from finding a secret (protocol::XpSource::Explore; no daily cap).
ALTER TABLE xp_ledger DROP CONSTRAINT IF EXISTS xp_ledger_source_check;
ALTER TABLE xp_ledger ADD CONSTRAINT xp_ledger_source_check
    CHECK (source IN ('class_attend', 'class_bonus', 'craft', 'find', 'duel_win', 'duel_loss',
                      'duel_draw', 'admin', 'explore'));
