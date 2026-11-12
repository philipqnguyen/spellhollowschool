-- Milestone 1b: item instances, equipped gear and saved outfits.
--
-- Every owned item is one row (an *instance* of a catalog item definition in
-- data/items/catalog.json), so items can be traded one by one (milestone 1c).
-- What an item is (kind, slot, name, asset) comes from the catalog by
-- item_def; the database stores only what differs per instance.

CREATE TABLE items (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id    bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    -- Catalog id (u16). Never reused by the catalog, so this stays meaningful.
    item_def    integer     NOT NULL CHECK (item_def BETWEEN 0 AND 65535),
    -- Palette indices (data/items/palette.json) of the three dye channels.
    dye_primary   smallint  NOT NULL CHECK (dye_primary BETWEEN 0 AND 255),
    dye_secondary smallint  NOT NULL CHECK (dye_secondary BETWEEN 0 AND 255),
    dye_trim      smallint  NOT NULL CHECK (dye_trim BETWEEN 0 AND 255),
    -- Where the item is. 'inventory' and 'equipped' are changed by the HTTP
    -- API (inventory, equipment, outfits); 'dorm' only by the realtime dorm
    -- code, together with dorm_placements. Kept in step with `equipment` and
    -- `dorm_placements`, which the composite foreign keys below tie to the owner.
    location    text        NOT NULL DEFAULT 'inventory'
                CHECK (location IN ('inventory', 'equipped', 'dorm')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    -- Target of the (item, owner) foreign keys: an item can only be equipped
    -- or placed by its owner, and changing the owner (a trade) fails while it is.
    CONSTRAINT items_id_owner_key UNIQUE (id, owner_id)
);

-- Keyset pagination of one character's items, newest first (ORDER BY id DESC),
-- optionally filtered by location. Kind and slot filters become
-- `item_def = ANY($defs)` with the defs taken from the catalog.
CREATE INDEX items_owner_idx ON items (owner_id, id);
CREATE INDEX items_owner_location_idx ON items (owner_id, location, id);

-- What each character wears: at most one item per slot, each item in at most
-- one slot. Slots: 'hat', 'body', 'legs', 'shoes' (protocol::GearSlot ids).
CREATE TABLE equipment (
    character_id bigint NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    slot         text   NOT NULL CHECK (slot IN ('hat', 'body', 'legs', 'shoes')),
    item_id      bigint NOT NULL,
    PRIMARY KEY (character_id, slot),
    CONSTRAINT equipment_item_key UNIQUE (item_id),
    CONSTRAINT equipment_item_owner_fkey FOREIGN KEY (item_id, character_id)
        REFERENCES items (id, owner_id) ON DELETE CASCADE
);

-- Saved outfits: a name and an item per slot (NULL = leave the slot empty).
-- At most protocol::SAVED_OUTFITS_MAX (10) per character, enforced by the API
-- (it locks the character row while counting).
CREATE TABLE saved_outfits (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_id  bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    name          text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 32),
    -- Items may since have been traded away or deleted; wearing the outfit
    -- skips slots whose item the character no longer owns.
    hat_item_id   bigint REFERENCES items (id) ON DELETE SET NULL,
    body_item_id  bigint REFERENCES items (id) ON DELETE SET NULL,
    legs_item_id  bigint REFERENCES items (id) ON DELETE SET NULL,
    shoes_item_id bigint REFERENCES items (id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX saved_outfits_character_idx ON saved_outfits (character_id, id);
