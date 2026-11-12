-- Phase 2d (docs/architecture.md "Phase 2d"): the last two things a class
-- reward did not keep on its attendance row, so a retried reward
-- (`AlreadyRewarded`) reports them too: the spells the class taught
-- (`protocol::SpellId`s, newly learned) and whether the daily class XP cap
-- cut the XP (`ClassRewards.xp_capped`).
--
-- Pre-launch: plain columns, no backfill. Older rewarded rows keep the
-- defaults (no spells recorded, not capped).

ALTER TABLE class_attendance
    ADD COLUMN spells    integer[] NOT NULL DEFAULT '{}',
    ADD COLUMN xp_capped boolean   NOT NULL DEFAULT false;
