-- Accounts, email verification and login sessions.
--
-- Age: the date of birth is checked at registration and never stored; only the
-- time the player attested to being an adult is kept (adult_attested_at).
-- Tokens (verification and session) are random secrets; only their SHA-256 is stored.

CREATE TABLE accounts (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Normalized (trimmed, lowercased) by the server before insert.
    email             text        NOT NULL,
    -- Argon2id PHC string. Bot accounts hold an unusable marker instead.
    password_hash     text        NOT NULL,
    adult_attested_at timestamptz NOT NULL,
    email_verified_at timestamptz,
    -- Throwaway accounts made by the dev-only bot-session endpoint (load tests).
    is_bot            boolean     NOT NULL DEFAULT false,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT accounts_email_normalized CHECK (email = lower(btrim(email))),
    CONSTRAINT accounts_email_length CHECK (char_length(email) BETWEEN 3 AND 254)
);

-- Case-insensitive uniqueness (the server also stores emails lowercased).
CREATE UNIQUE INDEX accounts_email_key ON accounts (lower(email));

CREATE TABLE email_verifications (
    token_hash bytea       PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    account_id bigint      NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

CREATE INDEX email_verifications_account_idx ON email_verifications (account_id);
CREATE INDEX email_verifications_expires_idx ON email_verifications (expires_at);

CREATE TABLE sessions (
    token_hash bytea       PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    account_id bigint      NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

CREATE INDEX sessions_account_idx ON sessions (account_id);
CREATE INDEX sessions_expires_idx ON sessions (expires_at);
