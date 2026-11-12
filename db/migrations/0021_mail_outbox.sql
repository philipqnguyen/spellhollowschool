-- Owl mail: the sender's Outbox.
--
-- A player's letter is one row seen from both ends: the recipient's
-- mailbox (`recipient_id`) and the sender's Outbox (`sender_id`, kind
-- 'letter'). When the recipient throws a letter away, the row is only
-- hidden from them (`recipient_deleted_at`), so the sender's copy stays.
-- System mail (sender_id NULL) is still deleted outright. Either way the
-- row goes for good by the usual cleanup (`server::mail::cleanup`,
-- `mail.retention_days` after `collected_at`), so the Outbox keeps a
-- letter exactly as long as its recipient could have.
ALTER TABLE mail ADD COLUMN recipient_deleted_at timestamptz;

-- What went with a letter, as it was attached: [ItemView] at send time.
-- `mail_attachments` rows go when the recipient collects, so the Outbox
-- shows this instead. NULL for system mail.
ALTER TABLE mail ADD COLUMN sent_items jsonb
    CHECK (sent_items IS NULL OR jsonb_typeof(sent_items) = 'array');

-- Outbox pages, newest first: (sender_id, id DESC) keyset over letters.
CREATE INDEX mail_sender_idx ON mail (sender_id, id) WHERE kind = 'letter';

-- The new-mail indicator counts only mail still in the mailbox.
DROP INDEX mail_unread_idx;
CREATE INDEX mail_unread_idx ON mail (recipient_id)
    WHERE read_at IS NULL AND recipient_deleted_at IS NULL;
