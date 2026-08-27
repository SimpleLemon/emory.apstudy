-- Additive state for owner-managed single-calendar ICS subscriptions.
-- Cross-column invariants are enforced by the calendar share service.
ALTER TABLE calendar_shares ADD COLUMN ics_token TEXT;
ALTER TABLE calendar_shares ADD COLUMN ics_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE calendar_shares ADD COLUMN ics_issued_at TEXT;
ALTER TABLE calendar_shares ADD COLUMN ics_rotated_at TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_shares_ics_token
    ON calendar_shares(ics_token)
    WHERE ics_token IS NOT NULL;
