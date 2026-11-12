-- Milestone 1c (economy): what the Owl Exchange needs beyond 0007.
--
-- * The listed item's look, copied when listed like `colour` (its primary
--   dye), so a listing shows the item's secondary and trim dyes and finish
--   after it closed and the item moved on (`item_id` is NULL then).
-- * The seller's request id (`POST /api/exchange/listings { request_id }`),
--   so a retried listing is answered instead of listing a stack twice.
-- * The active-listing count per seller (the listing limit) without walking
--   a seller's whole history.

ALTER TABLE exchange_listings
    ADD COLUMN dye_secondary smallint NOT NULL DEFAULT 0 CHECK (dye_secondary BETWEEN 0 AND 255),
    ADD COLUMN dye_trim      smallint NOT NULL DEFAULT 0 CHECK (dye_trim BETWEEN 0 AND 255),
    -- protocol::Finish, as items.finish: 0 none, 1 shimmer, 2 colour_shift.
    ADD COLUMN finish        smallint NOT NULL DEFAULT 0 CHECK (finish BETWEEN 0 AND 2),
    ADD COLUMN client_key    text CHECK (client_key IS NULL OR char_length(client_key) BETWEEN 1 AND 64),
    ADD CONSTRAINT exchange_listings_client_key UNIQUE (seller_id, client_key);

CREATE INDEX exchange_seller_active_idx ON exchange_listings (seller_id) WHERE state = 'active';
