-- Milestone 1b: dorm rooms and their furniture.
--
-- Every character owns one dorm room. The row is created on first use
-- (INSERT ... ON CONFLICT DO NOTHING); the room's style and layout follow the
-- owner's house (data/world/instances/dorm_<house id>.json).

CREATE TABLE dorms (
    owner_id        bigint      PRIMARY KEY REFERENCES characters (id) ON DELETE CASCADE,
    -- Friends may walk in (ClientMsg::DormVisit) while the owner is online.
    open_to_friends boolean     NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- One row per placed furniture item; the item's location is 'dorm' while it
-- has a row here. At most protocol::DORM_ITEM_CAP (150) per room, enforced by
-- the realtime dorm code.
CREATE TABLE dorm_placements (
    item_id   bigint      PRIMARY KEY,
    owner_id  bigint      NOT NULL REFERENCES dorms (owner_id) ON DELETE CASCADE,
    -- The item's origin in room coordinates, in centimetres (protocol::QPos),
    -- and its yaw in 1/256 turns, exactly as sent on the wire.
    pos_x     integer     NOT NULL,
    pos_y     integer     NOT NULL,
    pos_z     integer     NOT NULL,
    yaw       smallint    NOT NULL CHECK (yaw BETWEEN 0 AND 255),
    placed_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    -- Only the owner's own items can be placed in their room.
    CONSTRAINT dorm_placements_item_owner_fkey FOREIGN KEY (item_id, owner_id)
        REFERENCES items (id, owner_id) ON DELETE CASCADE
);

CREATE INDEX dorm_placements_owner_idx ON dorm_placements (owner_id);
