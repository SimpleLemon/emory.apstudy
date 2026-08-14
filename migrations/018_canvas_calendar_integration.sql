-- Phase 2A Canvas integration storage.
--
-- The migration runner records this file in schema_migrations and will skip it
-- on subsequent init_db() calls.  The ALTER TABLE statements intentionally
-- only add nullable columns (with a zero-compatible default for the tombstone
-- flag); the legacy feed UNIQUE(user_id, feed_url_hash, event_uid) remains
-- untouched.

ALTER TABLE calendar_cache ADD COLUMN canvas_source_id TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_account_key TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_source_item_key TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_event_ref TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_context_id TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_calendar_id TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_item_id TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_occurrence_id TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_item_type TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_source_revision TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_source_hash TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_completion_status TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_completion_source TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_completion_route TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_soft_deleted INTEGER DEFAULT 0;
ALTER TABLE calendar_cache ADD COLUMN canvas_deleted_at TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_last_seen_at TEXT;
ALTER TABLE calendar_cache ADD COLUMN canvas_last_seen_generation INTEGER;
ALTER TABLE calendar_cache ADD COLUMN canvas_last_seen_scope_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_calendar_cache_canvas_source
    ON calendar_cache(user_id, canvas_source_id, canvas_account_key,
                      canvas_last_seen_scope_hash, canvas_last_seen_generation);

CREATE INDEX IF NOT EXISTS idx_calendar_cache_canvas_event_ref
    ON calendar_cache(user_id, canvas_source_id, canvas_event_ref);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_cache_canvas_identity
    ON calendar_cache(
        user_id,
        canvas_source_id,
        canvas_account_key,
        canvas_context_id,
        canvas_calendar_id,
        canvas_item_type,
        canvas_item_id,
        IFNULL(canvas_occurrence_id, '')
    )
    WHERE canvas_source_id IS NOT NULL AND canvas_item_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS calendar_import_sources (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    nest_user_id TEXT,
    provider TEXT NOT NULL CHECK (provider = 'canvas'),
    origin TEXT NOT NULL,
    provider_user_id TEXT NOT NULL,
    account_key TEXT NOT NULL,
    source_id TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'paused', 'archived')),
    default_mirror_calendar TEXT,
    sync_state TEXT NOT NULL DEFAULT 'idle',
    last_sync_started_at TEXT,
    last_sync_completed_at TEXT,
    last_seen_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE(user_id, provider, account_key),
    UNIQUE(user_id, provider, source_id)
);

CREATE INDEX IF NOT EXISTS idx_calendar_import_sources_user
    ON calendar_import_sources(user_id, provider, status);

CREATE INDEX IF NOT EXISTS idx_calendar_import_sources_account
    ON calendar_import_sources(user_id, provider, account_key);

CREATE TABLE IF NOT EXISTS calendar_sync_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    lease_token TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    lease_renewed_at TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    scope_hash TEXT NOT NULL,
    consent_version INTEGER NOT NULL,
    checkpoint_json TEXT,
    cursor TEXT,
    counters_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active', 'complete', 'partial', 'expired', 'error',
                         'cancelled', 'superseded')),
    error_code TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    cancelled_at TEXT,
    idempotency_key TEXT,
    UNIQUE(user_id, source_id, run_id),
    UNIQUE(user_id, source_id, generation)
);

CREATE INDEX IF NOT EXISTS idx_calendar_sync_runs_source_generation
    ON calendar_sync_runs(user_id, source_id, generation DESC);

CREATE INDEX IF NOT EXISTS idx_calendar_sync_runs_lease
    ON calendar_sync_runs(user_id, source_id, state, lease_expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_sync_runs_idempotency
    ON calendar_sync_runs(user_id, source_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Batch receipts make retries safe without putting raw batches in the run row.
CREATE TABLE IF NOT EXISTS calendar_sync_batches (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    checkpoint_json TEXT,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, source_id, idempotency_key),
    UNIQUE(user_id, source_id, run_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_calendar_sync_batches_run
    ON calendar_sync_batches(user_id, source_id, run_id, generation);

CREATE TABLE IF NOT EXISTS calendar_import_routing (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('incomplete', 'completed')),
    destination_calendar_id TEXT,
    fallback_calendar_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, source_id, state)
);

CREATE INDEX IF NOT EXISTS idx_calendar_import_routing_source
    ON calendar_import_routing(user_id, source_id, state);

CREATE TABLE IF NOT EXISTS calendar_event_links (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    account_key TEXT NOT NULL,
    event_kind TEXT NOT NULL DEFAULT 'projection'
        CHECK (event_kind IN ('native', 'projection', 'feed')),
    nest_event_id TEXT,
    projection_event_id TEXT,
    event_ref TEXT,
    canvas_context_id TEXT,
    canvas_calendar_id TEXT,
    canvas_item_id TEXT,
    canvas_occurrence_id TEXT,
    canvas_item_type TEXT,
    source_revision TEXT,
    source_hash TEXT,
    mirror_state TEXT NOT NULL DEFAULT 'not_requested'
        CHECK (mirror_state IN ('not_requested', 'waiting_for_canvas_session',
                                'queued', 'applied', 'unsupported', 'forbidden',
                                'conflict', 'retryable_failed', 'cancelled')),
    mirror_error_code TEXT,
    mirror_error_message TEXT,
    mirrored_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_calendar_event_links_user_source
    ON calendar_event_links(user_id, source_id, archived_at);

CREATE INDEX IF NOT EXISTS idx_calendar_event_links_event_ref
    ON calendar_event_links(user_id, source_id, event_ref);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_event_links_canvas_identity
    ON calendar_event_links(
        user_id,
        source_id,
        account_key,
        canvas_context_id,
        canvas_calendar_id,
        canvas_item_type,
        canvas_item_id,
        IFNULL(canvas_occurrence_id, '')
    )
    WHERE canvas_item_id IS NOT NULL AND archived_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_event_links_active_ref
    ON calendar_event_links(user_id, source_id, event_ref)
    WHERE event_ref IS NOT NULL AND archived_at IS NULL;

CREATE TABLE IF NOT EXISTS calendar_writebacks (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    account_key TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('create', 'update', 'delete')),
    event_ref TEXT,
    expected_revision TEXT,
    payload_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    target_account TEXT NOT NULL,
    target_calendar TEXT,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'waiting_for_canvas_session'
        CHECK (state IN ('waiting_for_canvas_session', 'queued', 'applied',
                         'unsupported', 'forbidden', 'conflict',
                         'retryable_failed', 'cancelled')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_retry_at TEXT,
    result_revision TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    applied_at TEXT,
    cancelled_at TEXT,
    UNIQUE(user_id, source_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_calendar_writebacks_pending
    ON calendar_writebacks(user_id, source_id, state, updated_at);

CREATE INDEX IF NOT EXISTS idx_calendar_writebacks_event
    ON calendar_writebacks(user_id, source_id, event_ref);
