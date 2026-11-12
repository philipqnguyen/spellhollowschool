-- Phase 2c (docs/architecture.md "Phase 2c"): the hint a lore class gave is
-- kept on the attendance row, like every other reward, so a retried reward
-- reports it too (2a left it unstored: a retry answered without it).
--
-- Pre-launch: no backfill. Older rewarded rows keep NULL (no hint recorded).

ALTER TABLE class_attendance ADD COLUMN hint text;
