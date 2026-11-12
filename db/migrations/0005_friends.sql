-- Milestone 1b: friends, friend requests and blocks.

-- One row per direction: a friendship between A and B is (A, B) and (B, A),
-- written and deleted together in one transaction. Listing a character's
-- friends is then a single index range, paginated by the friend's name_key.
-- At most protocol::FRIENDS_MAX (200) per character, enforced by the API.
CREATE TABLE friendships (
    character_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    friend_id    bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (character_id, friend_id),
    CONSTRAINT friendships_not_self CHECK (character_id <> friend_id)
);

-- Pending requests only: accepting or declining deletes the row. A request
-- while the other side already asked is accepted at once by the API.
CREATE TABLE friend_requests (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_id    bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    to_id      bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT friend_requests_pair_key UNIQUE (from_id, to_id),
    CONSTRAINT friend_requests_not_self CHECK (from_id <> to_id)
);

-- Incoming and outgoing lists, newest first by id.
CREATE INDEX friend_requests_to_idx ON friend_requests (to_id, id);
CREATE INDEX friend_requests_from_idx ON friend_requests (from_id, id);

-- blocker_id blocked blocked_id. Blocking removes any friendship and pending
-- requests between the two (in the same transaction). A blocked player cannot
-- send the blocker requests, whispers or dorm invitations, and their chat is
-- not delivered to the blocker; nothing tells them they are blocked.
CREATE TABLE blocks (
    blocker_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    blocked_id bigint      NOT NULL REFERENCES characters (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (blocker_id, blocked_id),
    CONSTRAINT blocks_not_self CHECK (blocker_id <> blocked_id)
);

-- "Who blocked me", for the realtime side's chat and invite checks.
CREATE INDEX blocks_blocked_idx ON blocks (blocked_id);
