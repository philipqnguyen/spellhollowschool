-- Phase 3b, flying and the Flying Pitch (docs/architecture.md "Phase 3b"):
-- the Flying skill (the tenth school subject, sim_core::classes::Subject::Flying,
-- fed by the Flying class) and Featherlight Elixir's effect
-- (protocol::EffectKind::Featherlight: a faster broom for 10 minutes).
--
-- Pre-launch: no backfill. Both changes only widen a CHECK, so existing dev
-- rows stay valid. Mount state is never stored: a character who logs out in
-- the air is saved standing on the ground below (or at a spawn).

ALTER TABLE skills DROP CONSTRAINT IF EXISTS skills_discipline_check;
ALTER TABLE skills ADD CONSTRAINT skills_discipline_check CHECK (discipline IN (
    'charms', 'transfiguration', 'potions', 'defence', 'history', 'divination',
    'astronomy', 'herbology', 'creatures', 'flying'));

ALTER TABLE active_effects DROP CONSTRAINT IF EXISTS active_effects_kind_check;
ALTER TABLE active_effects ADD CONSTRAINT active_effects_kind_check CHECK (kind IN (
    'glow', 'chameleon', 'shrinking', 'swelling', 'babbling', 'mimic_draught', 'fortunes_draught',
    'pick_me_up_tonic', 'invisibility', 'fortune', 'featherlight'));
