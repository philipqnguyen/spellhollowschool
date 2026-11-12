-- NPCs in the open castle (docs/architecture.md "NPCs"; data/npcs,
-- sim_core::npcs, crate::residents).
--
-- Pre-launch: no backfill. One new table, a widened CHECK and one relaxed
-- column; existing dev rows stay valid.

-- Daily NPC gifts: once per character, per NPC, per UTC day. The row is the
-- claim (the primary key makes it once) and is written in the same
-- transaction as what it gives: a lore unlock (`lore_unlocks` source 'npc'),
-- items (`item_ledger` reason 'npc_gift', ref_id = id), a house point
-- (`house_points` source 'npc', ref_id = id, under the House Cup's daily cap
-- `hp_npc`) or a hint (nothing else written). `gift` is what was given, in
-- protocol::NpcGift's serde JSON shape, so a retried claim answers the same.
CREATE TABLE npc_gifts (
    id           bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    -- The roster id (data/npcs/roster.json), e.g. 'ghost_house_c'.
    npc          text        NOT NULL CHECK (char_length(npc) BETWEEN 1 AND 64),
    -- The UTC day (sim_core::npcs::gift_day).
    day          date        NOT NULL,
    at           timestamptz NOT NULL DEFAULT now(),
    gift         jsonb       NOT NULL CHECK (jsonb_typeof(gift) = 'object'),
    PRIMARY KEY (character_id, npc, day)
);

-- A ghost's daily house point.
ALTER TABLE house_points DROP CONSTRAINT IF EXISTS house_points_source_check;
ALTER TABLE house_points ADD CONSTRAINT house_points_source_check
    CHECK (source IN ('class', 'duel', 'admin', 'find', 'craft', 'npc'));

-- Lore an NPC told: no class attendance. A class unlock still names its
-- attendance (unique); an NPC unlock names the NPC (its roster id) instead.
-- GET /api/lore lists both (a LEFT JOIN: `subject` is null for an NPC's).
ALTER TABLE lore_unlocks ALTER COLUMN attendance_id DROP NOT NULL;
ALTER TABLE lore_unlocks ADD COLUMN npc text CHECK (char_length(npc) BETWEEN 1 AND 64);
ALTER TABLE lore_unlocks ADD CONSTRAINT lore_unlocks_class_or_npc
    CHECK ((attendance_id IS NULL) <> (npc IS NULL));
