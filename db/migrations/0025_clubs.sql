-- Clubs (server::clubs, docs/architecture.md "Clubs"): lasting groups of
-- characters with an owner, officers and members. Founded at the clubs
-- secretary's desk for a fee (crown_ledger reason 'club_found', ref_id the
-- club). Invitations live in the world's memory only.

CREATE TABLE clubs (
    id          bigserial   PRIMARY KEY,
    name        text        NOT NULL,
    -- server::clubs::name_key(name) and the upper-case tag: unique, so no
    -- two clubs share a name or a tag whatever the case.
    name_key    text        NOT NULL UNIQUE,
    tag         text        NOT NULL UNIQUE,
    -- The pinned message of the day ('' for none).
    motd        text        NOT NULL DEFAULT '',
    -- Bumped by every change, so the world keeps the newest copy.
    version     bigint      NOT NULL DEFAULT 1,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- One club per character (the primary key). A deleted character's row goes
-- with it, after the server has handed the club on (server::clubs::store).
CREATE TABLE club_members (
    character_id bigint      PRIMARY KEY REFERENCES characters (id) ON DELETE CASCADE,
    club_id      bigint      NOT NULL REFERENCES clubs (id) ON DELETE CASCADE,
    role         text        NOT NULL CHECK (role IN ('owner', 'officer', 'member')),
    joined_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX club_members_club_idx ON club_members (club_id, joined_at);
-- At most one owner per club.
CREATE UNIQUE INDEX club_members_owner_idx ON club_members (club_id) WHERE role = 'owner';
