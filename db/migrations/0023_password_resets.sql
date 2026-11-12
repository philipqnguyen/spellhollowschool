-- Password reset links (`server::auth::reset`).
--
-- One live token per account: a new request replaces the old row, and a
-- successful reset deletes it, so every link is single-use. Only the
-- SHA-256 of the token is stored. Expired rows go with the janitor
-- (`db::delete_expired`).
CREATE TABLE password_resets (
    account_id bigint      PRIMARY KEY REFERENCES accounts (id) ON DELETE CASCADE,
    token_hash bytea       NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

CREATE INDEX password_resets_expires_idx ON password_resets (expires_at);
