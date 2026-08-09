ALTER TABLE calendar_feeds ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE calendar_feeds ADD COLUMN last_error_type TEXT;
ALTER TABLE calendar_feeds ADD COLUMN last_error_message TEXT;
ALTER TABLE calendar_feeds ADD COLUMN last_error_at TEXT;
ALTER TABLE calendar_feeds ADD COLUMN disabled_at TEXT;
