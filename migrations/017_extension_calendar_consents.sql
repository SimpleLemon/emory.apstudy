CREATE TABLE IF NOT EXISTS calendar_integration_consents (
    id TEXT PRIMARY KEY,
    nest_user_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    account_key TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version = 1),
    scopes_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'revoked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    granted_at TEXT,
    revoked_at TEXT,
    cancellation_state TEXT NOT NULL DEFAULT 'not_applicable',
    archive_state TEXT NOT NULL DEFAULT 'not_applicable',
    UNIQUE(nest_user_id, source_key, account_key)
);

CREATE INDEX IF NOT EXISTS idx_calendar_consents_user_source
    ON calendar_integration_consents(nest_user_id, source_key);

CREATE INDEX IF NOT EXISTS idx_calendar_consents_state
    ON calendar_integration_consents(state, updated_at);
