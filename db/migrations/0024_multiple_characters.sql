-- Up to five characters per account (server::characters::MAX_PER_ACCOUNT,
-- checked by the server when creating), played one at a time: the session
-- remembers which one its player picked.

ALTER TABLE characters DROP CONSTRAINT characters_account_id_key;
CREATE INDEX characters_account_idx ON characters (account_id, id);

-- The character this session plays: set by POST /api/characters/{id}/select
-- and by creating a character; NULL until the player picks one (the HTTP
-- endpoints answer 404 no_character and the gateway refuses the Hello).
-- Deleting the character unselects it.
ALTER TABLE sessions
    ADD COLUMN character_id bigint REFERENCES characters (id) ON DELETE SET NULL;
CREATE INDEX sessions_character_idx ON sessions (character_id) WHERE character_id IS NOT NULL;
-- Until now an account had at most one character: existing sessions play it.
UPDATE sessions s SET character_id = c.id FROM characters c WHERE c.account_id = s.account_id;

-- A player can delete a character (DELETE /api/characters/{id}). Its own
-- rows go with it (ON DELETE CASCADE everywhere else), but the records it
-- shares with other characters stay theirs: a duel stays in the opponent's
-- history and a sold listing stays the buyer's purchase, with the deleted
-- side NULL (as trades and mail senders already were). The server deletes
-- a character's active listings itself before the character.
ALTER TABLE duels
    ALTER COLUMN a_id DROP NOT NULL,
    ALTER COLUMN b_id DROP NOT NULL,
    DROP CONSTRAINT duels_a_id_fkey,
    DROP CONSTRAINT duels_b_id_fkey,
    ADD CONSTRAINT duels_a_id_fkey FOREIGN KEY (a_id) REFERENCES characters (id) ON DELETE SET NULL,
    ADD CONSTRAINT duels_b_id_fkey FOREIGN KEY (b_id) REFERENCES characters (id) ON DELETE SET NULL;

ALTER TABLE exchange_listings
    ALTER COLUMN seller_id DROP NOT NULL,
    DROP CONSTRAINT exchange_listings_seller_id_fkey,
    ADD CONSTRAINT exchange_listings_seller_id_fkey
        FOREIGN KEY (seller_id) REFERENCES characters (id) ON DELETE SET NULL,
    -- Only a closed listing may lose its seller.
    ADD CONSTRAINT exchange_listings_active_seller CHECK (state <> 'active' OR seller_id IS NOT NULL);
