-- Character adoption (protocol 6, data/characters/creator.json version 2;
-- docs/art/character-adoption.md section 2):
--
-- - there are no build sliders any more: every look loses its `build`;
-- - eye colours are new: looks without one get Brown (index 1);
-- - the 14 old hair styles (ids 0-13) are retired; each maps to the nearest
--   of the six new ones of the look's own body (body_type 0 = Male, styles
--   14-16; body_type 1 = Female, styles 17-19), as creator.json's
--   `migrate_to` says;
-- - `appearance_version` goes up so clients refresh their cached looks.
--
-- The same mapping applies to Mimic Draught disguises still in effect
-- (`active_effects.visible = {"disguise": {"appearance": {...}}}`).
--
-- Idempotent: a look already migrated (no `build`, an `eye_colour`, a new
-- hair id) is left alone, and new ids map to themselves. The server also
-- migrates every look it reads (`server::catalog::migrate_look`), so a row
-- this misses still loads.

CREATE TEMPORARY TABLE adoption_hair_map (
    old_id    integer NOT NULL,
    body_type integer NOT NULL,
    new_id    integer NOT NULL,
    PRIMARY KEY (old_id, body_type)
) ON COMMIT DROP;

INSERT INTO adoption_hair_map (old_id, body_type, new_id) VALUES
    (0, 0, 14),  (0, 1, 19),   -- Short Crop
    (1, 0, 14),  (1, 1, 19),   -- Tousled
    (2, 0, 15),  (2, 1, 19),   -- Side Part
    (3, 0, 16),  (3, 1, 19),   -- Shoulder-Length Waves
    (4, 0, 16),  (4, 1, 17),   -- Long and Straight
    (5, 0, 16),  (5, 1, 18),   -- Single Plait
    (6, 0, 16),  (6, 1, 18),   -- High Ponytail
    (7, 0, 16),  (7, 1, 18),   -- Low Bun
    (8, 0, 14),  (8, 1, 19),   -- Curly Crop
    (9, 0, 16),  (9, 1, 17),   -- Box Braids
    (10, 0, 14), (10, 1, 19),  -- Afro
    (11, 0, 15), (11, 1, 19),  -- Pixie Cut
    (12, 0, 14), (12, 1, 19),  -- Close Crop
    (13, 0, 16), (13, 1, 17);  -- Long Curls

-- One look brought up to version 2.
CREATE FUNCTION pg_temp.adopt_look(look jsonb) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT (look - 'build') || jsonb_build_object(
        'eye_colour', COALESCE(look -> 'eye_colour', to_jsonb(1)),
        'hair_style', COALESCE(
            (SELECT to_jsonb(m.new_id)
             FROM adoption_hair_map m
             WHERE m.old_id = (look ->> 'hair_style')::integer
               AND m.body_type = COALESCE((look ->> 'body_type')::integer, 0)),
            -- An old id with an unknown body type: the first body's mapping.
            (SELECT to_jsonb(m.new_id)
             FROM adoption_hair_map m
             WHERE m.old_id = (look ->> 'hair_style')::integer AND m.body_type = 0),
            look -> 'hair_style',
            to_jsonb(14)))
$$;

-- Whether a look still needs it.
CREATE FUNCTION pg_temp.look_is_old(look jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT look ? 'build'
        OR NOT look ? 'eye_colour'
        OR COALESCE((look ->> 'hair_style')::integer < 14, true)
$$;

UPDATE characters
SET appearance = pg_temp.adopt_look(appearance),
    appearance_version = appearance_version + 1
WHERE appearance IS NOT NULL
  AND jsonb_typeof(appearance) = 'object'
  AND pg_temp.look_is_old(appearance);

UPDATE active_effects
SET visible = jsonb_set(visible, '{disguise,appearance}',
                        pg_temp.adopt_look(visible #> '{disguise,appearance}'))
WHERE kind = 'mimic_draught'
  AND jsonb_typeof(visible #> '{disguise,appearance}') = 'object'
  AND pg_temp.look_is_old(visible #> '{disguise,appearance}');

DROP FUNCTION pg_temp.look_is_old(jsonb);
DROP FUNCTION pg_temp.adopt_look(jsonb);
