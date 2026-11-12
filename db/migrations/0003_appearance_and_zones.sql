-- Milestone 1b: character appearance, where a character resumes, and the
-- starter grant.

ALTER TABLE characters
    -- protocol::Appearance as JSON (the same serde shape the HTTP API uses):
    -- body type, sliders, skin, hair and the worn outfit (item defs + dyes).
    -- NULL for characters made before 1b: the server treats it as the creator
    -- defaults and fills it in when it grants their starter wardrobe.
    ADD COLUMN appearance jsonb CHECK (appearance IS NULL OR jsonb_typeof(appearance) = 'object'),
    -- Which zone pos_x/y/z are in: 'castle', or 'dorm' (the character's OWN dorm
    -- room). This is the resume point: a guest in someone else's dorm is saved
    -- at their return point (where they were before entering), never inside
    -- another player's room.
    ADD COLUMN zone text NOT NULL DEFAULT 'castle' CHECK (zone IN ('castle', 'dorm')),
    -- When the starter wardrobe and furniture were granted; NULL = not yet
    -- (new characters are granted on creation, 1a characters on their next
    -- 1b sign-in).
    ADD COLUMN starter_granted_at timestamptz;

-- Existing (1a) characters keep resuming in the castle; new characters start
-- in their dorm room (pos NULL = the dorm layout's spawn).
ALTER TABLE characters ALTER COLUMN zone SET DEFAULT 'dorm';
