-- Phase 3a (docs/architecture.md "Phase 3a"): a duel still being fought when
-- the server shuts down is recorded as no contest: result 'draw', reason
-- 'shutdown', no house points and no XP (the Server builder writes the rows
-- on the way down; nothing is sent to the duelists, who are being
-- disconnected). Not a protocol reason: `protocol::DuelEndReason` is
-- unchanged (protocol v7).
--
-- Pre-launch: no backfill. Duels cut short by earlier shutdowns were never
-- recorded and stay unrecorded.

ALTER TABLE duels DROP CONSTRAINT duels_reason_check;
ALTER TABLE duels ADD CONSTRAINT duels_reason_check
    CHECK (reason IN ('poise', 'timeout', 'forfeit', 'disconnect', 'shutdown'));
