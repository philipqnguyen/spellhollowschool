-- Milestone 1c: Crowns, stacks and maker's marks, the append-only item and
-- Crown ledgers, owl mail, the Owl Exchange and trade records.
--
-- Integrity rules (docs/architecture.md, "Economy integrity"):
-- * every trade, purchase, listing, mail collection and craft is one
--   transaction, locking the character row(s) first (FOR NO KEY UPDATE, in id
--   order), then item rows by ascending id;
-- * every item movement and every Crown movement appends a ledger row in
--   that same transaction (`server::ledger`), with a reason and a reference
--   id, so duplication can be detected and reversed. The ledgers are never
--   updated or deleted (a trigger refuses it).

-- ------------------------------------------------------------------ Crowns

ALTER TABLE characters
    -- One integer unit (Crowns). Never negative.
    ADD COLUMN crowns bigint NOT NULL DEFAULT 0
        CONSTRAINT characters_crowns_nonneg CHECK (crowns >= 0),
    -- When the starting purse was given (with the starter grant).
    ADD COLUMN purse_granted_at timestamptz;

-- ------------------------------------------------------------------ items

ALTER TABLE items
    -- Units in this row: 1 for clothing and furniture (one row per item);
    -- up to the catalog's max_stack for stackable items.
    ADD COLUMN quantity integer NOT NULL DEFAULT 1 CONSTRAINT items_quantity_positive CHECK (quantity >= 1),
    -- Maker's mark of crafted items: who made it (the name as it was then).
    ADD COLUMN maker_id bigint REFERENCES characters (id) ON DELETE SET NULL,
    ADD COLUMN maker_name text CHECK (maker_name IS NULL OR char_length(maker_name) BETWEEN 3 AND 24),
    -- protocol::Finish: 0 none, 1 shimmer, 2 colour_shift.
    ADD COLUMN finish smallint NOT NULL DEFAULT 0 CHECK (finish BETWEEN 0 AND 2);

-- New places an item can be: attached to owl mail (owned by the recipient,
-- see mail_attachments) or listed on the Owl Exchange (owned by the seller,
-- see exchange_listings).
ALTER TABLE items DROP CONSTRAINT items_location_check;
ALTER TABLE items ADD CONSTRAINT items_location_check
    CHECK (location IN ('inventory', 'equipped', 'dorm', 'mail', 'exchange'));

-- Stack merging and recipe inputs look items up by definition.
CREATE INDEX items_owner_def_idx ON items (owner_id, item_def, id);

-- ------------------------------------------------------------------ ledgers

CREATE FUNCTION ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END
$$;

-- One row per item movement. No foreign keys: the ledger outlives the items
-- and characters it mentions.
CREATE TABLE item_ledger (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at            timestamptz NOT NULL DEFAULT now(),
    item_id       bigint      NOT NULL,
    item_def      integer     NOT NULL,
    -- create: a new row from nothing the economy had (shop, find, craft output);
    -- grant: created by a grant (starter set, backfill);
    -- move: owner and/or location changed (trade, mail, Exchange, equip, dorm);
    -- split: a new row (item_id) took `quantity` units from related_item;
    -- merge: `quantity` units of item_id went into related_item (item_id is gone or smaller);
    -- consume: `quantity` units used up (craft inputs, dyes, potions, patterns learned);
    -- destroy: removed for another reason (admin);
    -- restyle: item_def changed in place (prev_item_def -> item_def).
    op            text        NOT NULL CHECK (op IN ('create', 'grant', 'move', 'split', 'merge',
                                                     'consume', 'destroy', 'restyle')),
    quantity      integer     NOT NULL CHECK (quantity >= 1),
    from_owner    bigint,
    to_owner      bigint,
    from_location text,
    to_location   text,
    related_item  bigint,
    prev_item_def integer,
    -- Why (server::ledger::Reason ids, e.g. 'trade', 'mail_collect') and the
    -- id of the row that explains it (trades.id, mail.id, exchange_listings.id,
    -- shop_orders.id, crafts.id, ...; NULL when there is none).
    reason        text        NOT NULL,
    ref_id        bigint
);

CREATE INDEX item_ledger_item_idx ON item_ledger (item_id, id);
CREATE INDEX item_ledger_from_idx ON item_ledger (from_owner, at) WHERE from_owner IS NOT NULL;
CREATE INDEX item_ledger_to_idx ON item_ledger (to_owner, at) WHERE to_owner IS NOT NULL;
CREATE INDEX item_ledger_at_idx ON item_ledger (at);
CREATE INDEX item_ledger_ref_idx ON item_ledger (reason, ref_id) WHERE ref_id IS NOT NULL;

CREATE TRIGGER item_ledger_append_only BEFORE UPDATE OR DELETE ON item_ledger
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();

-- One row per change of a character's balance; the sum of a character's
-- deltas is their balance.
CREATE TABLE crown_ledger (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at            timestamptz NOT NULL DEFAULT now(),
    character_id  bigint      NOT NULL,
    delta         bigint      NOT NULL CHECK (delta <> 0),
    balance_after bigint      NOT NULL CHECK (balance_after >= 0),
    reason        text        NOT NULL,
    ref_id        bigint
);

CREATE INDEX crown_ledger_character_idx ON crown_ledger (character_id, id);
CREATE INDEX crown_ledger_at_idx ON crown_ledger (at);
CREATE INDEX crown_ledger_ref_idx ON crown_ledger (reason, ref_id) WHERE ref_id IS NOT NULL;

CREATE TRIGGER crown_ledger_append_only BEFORE UPDATE OR DELETE ON crown_ledger
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();

-- Backfill: every item that exists gets its 'grant' row, and every character
-- that already has its starter grant gets the starting purse
-- (data/economy.json starting_purse = 100 when this migration was written;
-- server::catalog's tests check the two agree).
INSERT INTO item_ledger (at, item_id, item_def, op, quantity, to_owner, to_location, reason)
SELECT created_at, id, item_def, 'grant', quantity, owner_id, location, 'backfill'
FROM items ORDER BY id;

UPDATE characters SET crowns = 100, purse_granted_at = now()
WHERE starter_granted_at IS NOT NULL;

INSERT INTO crown_ledger (character_id, delta, balance_after, reason)
SELECT id, 100, 100, 'starting_purse' FROM characters WHERE purse_granted_at IS NOT NULL ORDER BY id;

-- ------------------------------------------------------------------ owl mail

CREATE TABLE mail (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipient_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    -- NULL for system mail (shop deliveries, Exchange returns and proceeds).
    sender_id    bigint      REFERENCES characters (id) ON DELETE SET NULL,
    -- Shown as the sender: the character's name when sent, or the system
    -- sender's display name ("Owl Exchange").
    sender_name  text        NOT NULL CHECK (char_length(sender_name) BETWEEN 1 AND 40),
    kind         text        NOT NULL CHECK (kind IN ('letter', 'shop', 'exchange_sold',
                                                      'exchange_bought', 'exchange_returned',
                                                      'exchange_expired', 'system')),
    subject      text        NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 80),
    body         text        NOT NULL DEFAULT '' CHECK (char_length(body) <= 2000),
    -- Crowns enclosed (taken from the sender, or Exchange proceeds), paid
    -- out on collect.
    crowns       bigint      NOT NULL DEFAULT 0 CHECK (crowns >= 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    read_at      timestamptz,
    -- When the attachments and Crowns were collected (NULL while any wait).
    collected_at timestamptz,
    -- Optional: when an unread, uncollected letter may be cleaned up (not in 1c).
    expires_at   timestamptz,
    -- The row this mail is about (exchange_listings.id, shop_orders.id).
    ref_id       bigint,
    -- Letters only: the sender's request id, so a retried send is not sent twice.
    client_key   text CHECK (client_key IS NULL OR char_length(client_key) BETWEEN 1 AND 64),
    CONSTRAINT mail_client_key UNIQUE (sender_id, client_key)
);

-- Mailbox pages, newest first: (recipient_id, id DESC) keyset.
CREATE INDEX mail_recipient_idx ON mail (recipient_id, id);
-- The new-mail indicator.
CREATE INDEX mail_unread_idx ON mail (recipient_id) WHERE read_at IS NULL;

-- Items travelling with a mail: owned by the recipient, location 'mail',
-- until collected (then the row is deleted and the item is 'inventory').
CREATE TABLE mail_attachments (
    mail_id  bigint NOT NULL REFERENCES mail (id) ON DELETE CASCADE,
    item_id  bigint NOT NULL,
    owner_id bigint NOT NULL,
    PRIMARY KEY (mail_id, item_id),
    CONSTRAINT mail_attachments_item_key UNIQUE (item_id),
    CONSTRAINT mail_attachments_item_owner_fkey FOREIGN KEY (item_id, owner_id)
        REFERENCES items (id, owner_id) ON DELETE CASCADE
);

-- ------------------------------------------------------------------ owl-order shop

-- One row per catalog order (its items arrive by owl mail).
CREATE TABLE shop_orders (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    -- [{ item_def, quantity, unit_price }] as ordered.
    lines        jsonb       NOT NULL CHECK (jsonb_typeof(lines) = 'array'),
    total        bigint      NOT NULL CHECK (total >= 0),
    mail_id      bigint      REFERENCES mail (id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- The client's request id: a retried order is answered, not repeated.
    client_key   text        CHECK (client_key IS NULL OR char_length(client_key) BETWEEN 1 AND 64),
    CONSTRAINT shop_orders_client_key UNIQUE (character_id, client_key)
);

CREATE INDEX shop_orders_character_idx ON shop_orders (character_id, id);

-- ------------------------------------------------------------------ Owl Exchange

CREATE TABLE exchange_listings (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id      bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    -- The listed item (its own row: a stack is split for the listing),
    -- owned by the seller with location 'exchange' while active; NULL once
    -- the listing closed and the item moved on.
    item_id        bigint,
    item_def       integer     NOT NULL CHECK (item_def BETWEEN 0 AND 65535),
    quantity       integer     NOT NULL CHECK (quantity >= 1),
    -- Buyout price for the whole listing, and the listing fee paid.
    price          bigint      NOT NULL CHECK (price >= 1),
    fee            bigint      NOT NULL CHECK (fee >= 0),
    -- Copied from the catalog and the item when listed, for browsing.
    category       text        NOT NULL,
    subcategory    text        NOT NULL,
    required_level smallint    NOT NULL DEFAULT 1 CHECK (required_level BETWEEN 1 AND 20),
    -- The item's primary dye (palette index): the colour filter.
    colour         smallint    NOT NULL CHECK (colour BETWEEN 0 AND 255),
    -- The catalog's house_theme (lower-case house id) or NULL.
    house_theme    text,
    maker_name     text,
    state          text        NOT NULL DEFAULT 'active'
                   CHECK (state IN ('active', 'sold', 'cancelled', 'expired')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    closed_at      timestamptz,
    buyer_id       bigint      REFERENCES characters (id) ON DELETE SET NULL,
    -- Sales tax removed from the economy on a sale (price - tax went to the seller).
    tax            bigint      CHECK (tax IS NULL OR tax >= 0),
    CONSTRAINT exchange_listings_active_item CHECK ((state = 'active') = (item_id IS NOT NULL)),
    CONSTRAINT exchange_listings_item_owner_fkey FOREIGN KEY (item_id, seller_id)
        REFERENCES items (id, owner_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX exchange_listings_item_idx ON exchange_listings (item_id) WHERE item_id IS NOT NULL;
-- Browse paths, active listings only, each with keyset pagination on
-- (sort key, id): newest = id DESC; price = (price, id) ascending or descending.
CREATE INDEX exchange_active_newest_idx ON exchange_listings (id) WHERE state = 'active';
CREATE INDEX exchange_active_price_idx ON exchange_listings (price, id) WHERE state = 'active';
CREATE INDEX exchange_cat_newest_idx ON exchange_listings (category, id) WHERE state = 'active';
CREATE INDEX exchange_cat_price_idx ON exchange_listings (category, price, id) WHERE state = 'active';
CREATE INDEX exchange_sub_newest_idx ON exchange_listings (category, subcategory, id) WHERE state = 'active';
CREATE INDEX exchange_sub_price_idx ON exchange_listings (category, subcategory, price, id) WHERE state = 'active';
-- Name search: the query is resolved to catalog ids, then item_def = ANY(...).
CREATE INDEX exchange_def_newest_idx ON exchange_listings (item_def, id) WHERE state = 'active';
CREATE INDEX exchange_def_price_idx ON exchange_listings (item_def, price, id) WHERE state = 'active';
-- A seller's listings (GET /api/exchange/mine, the active-listing limit).
CREATE INDEX exchange_seller_idx ON exchange_listings (seller_id, id);
-- The expiry job.
CREATE INDEX exchange_expiry_idx ON exchange_listings (expires_at) WHERE state = 'active';

-- ------------------------------------------------------------------ trades

-- One row per completed trade window (its item and Crown movements are in
-- the ledgers with reason 'trade' and ref_id = trades.id).
CREATE TABLE trades (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Unique per trade window (the world's random 63-bit key), so the commit
    -- can never run twice.
    trade_key   bigint      NOT NULL UNIQUE,
    a_id        bigint      REFERENCES characters (id) ON DELETE SET NULL,
    b_id        bigint      REFERENCES characters (id) ON DELETE SET NULL,
    a_crowns    bigint      NOT NULL CHECK (a_crowns >= 0),
    b_crowns    bigint      NOT NULL CHECK (b_crowns >= 0),
    -- [{ item_id, item_def, quantity, new_item_id? }] each side gave.
    a_items     jsonb       NOT NULL CHECK (jsonb_typeof(a_items) = 'array'),
    b_items     jsonb       NOT NULL CHECK (jsonb_typeof(b_items) = 'array'),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX trades_a_idx ON trades (a_id, id);
CREATE INDEX trades_b_idx ON trades (b_id, id);
