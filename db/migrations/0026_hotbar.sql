-- The social hotbar (server::spells::hotbar, docs/architecture.md "Spells
-- and dueling"): protocol::HOTBAR_SLOTS (10) slots in key order, each a
-- spell id or NULL for an empty slot. NULL for the whole column: the
-- character never set one, and gets its learned hotbar spells in spellbook
-- order (sim_core::spells::Spells::default_hotbar).
ALTER TABLE characters
    ADD COLUMN hotbar integer[] CHECK (hotbar IS NULL OR cardinality(hotbar) = 10);
