-- Player characters: one per account.

CREATE TABLE characters (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id         bigint      NOT NULL UNIQUE REFERENCES accounts (id) ON DELETE CASCADE,
    -- Display name as the player typed it.
    name               text        NOT NULL CHECK (char_length(name) BETWEEN 3 AND 24),
    -- Case-folded name for uniqueness and lookups, computed by the server with
    -- Unicode lowercasing (Postgres lower() under the C collation folds ASCII only).
    name_key           text        NOT NULL,
    -- House index (0..=3). The display names live in data/localization, not the schema,
    -- so renaming a house never needs a migration. Order matches protocol::House.
    house              smallint    NOT NULL CHECK (house BETWEEN 0 AND 3),
    -- Last saved world position in metres (Y up). NULL until the character first
    -- leaves the world, meaning "spawn at the layout's default spawn".
    pos_x              real,
    pos_y              real,
    pos_z              real,
    -- Bumped whenever the outfit changes; clients cache appearance by (id, version).
    appearance_version integer     NOT NULL DEFAULT 0 CHECK (appearance_version >= 0),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT characters_name_key_folded CHECK (name_key = lower(name_key)),
    CONSTRAINT characters_pos_all_or_none CHECK (
        (pos_x IS NULL AND pos_y IS NULL AND pos_z IS NULL)
        OR (pos_x IS NOT NULL AND pos_y IS NOT NULL AND pos_z IS NOT NULL)
    )
);

-- Case-insensitive unique names: "Luna" and "luna" cannot both exist.
CREATE UNIQUE INDEX characters_name_key ON characters (name_key);
