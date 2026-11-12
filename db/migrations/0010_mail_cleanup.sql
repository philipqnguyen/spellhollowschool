-- Hardening of milestone 1c: old mail is cleaned up.
--
-- A mail is kept `mail.retention_days` (data/economy.json) after nothing
-- waits in it any more (`collected_at`, which is the send time for mail
-- with nothing to collect); then `server::mail::cleanup` deletes it. Mail
-- whose items or Crowns were never collected (`collected_at IS NULL`) is
-- never deleted. The job walks this index oldest first.
CREATE INDEX mail_collected_idx ON mail (collected_at) WHERE collected_at IS NOT NULL;
