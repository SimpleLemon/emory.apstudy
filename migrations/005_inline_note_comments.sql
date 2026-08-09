ALTER TABLE note_comment_threads ADD COLUMN client_request_id TEXT;
ALTER TABLE note_comment_threads ADD COLUMN anchor_kind TEXT NOT NULL DEFAULT 'document';
ALTER TABLE note_comment_threads ADD COLUMN anchor_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE note_comment_threads ADD COLUMN relative_start TEXT;
ALTER TABLE note_comment_threads ADD COLUMN relative_end TEXT;
ALTER TABLE note_comment_threads ADD COLUMN start_block_id TEXT;
ALTER TABLE note_comment_threads ADD COLUMN start_offset INTEGER;
ALTER TABLE note_comment_threads ADD COLUMN end_block_id TEXT;
ALTER TABLE note_comment_threads ADD COLUMN end_offset INTEGER;
ALTER TABLE note_comment_threads ADD COLUMN context_before TEXT;
ALTER TABLE note_comment_threads ADD COLUMN context_after TEXT;
ALTER TABLE note_comment_threads ADD COLUMN anchor_state TEXT NOT NULL DEFAULT 'detached';
ALTER TABLE note_comment_threads ADD COLUMN edited_at TEXT;
ALTER TABLE note_comment_threads ADD COLUMN deleted_at TEXT;
ALTER TABLE note_comment_threads ADD COLUMN deleted_by_user_id TEXT;

UPDATE note_comment_threads
SET anchor_kind = CASE WHEN block_id IS NULL THEN 'document' ELSE 'legacy' END,
    start_block_id = block_id,
    end_block_id = block_id,
    anchor_state = 'detached';

CREATE UNIQUE INDEX IF NOT EXISTS idx_note_comment_request
    ON note_comment_threads(note_id, author_user_id, client_request_id)
    WHERE client_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_note_comment_anchor
    ON note_comment_threads(note_id, anchor_state, status, created_at);

ALTER TABLE note_comment_replies ADD COLUMN client_request_id TEXT;
ALTER TABLE note_comment_replies ADD COLUMN edited_at TEXT;
ALTER TABLE note_comment_replies ADD COLUMN deleted_at TEXT;
ALTER TABLE note_comment_replies ADD COLUMN deleted_by_user_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_note_comment_reply_request
    ON note_comment_replies(thread_id, author_user_id, client_request_id)
    WHERE client_request_id IS NOT NULL;

ALTER TABLE note_suggestions ADD COLUMN client_request_id TEXT;
ALTER TABLE note_suggestions ADD COLUMN target_kind TEXT NOT NULL DEFAULT 'body';
ALTER TABLE note_suggestions ADD COLUMN scope_json TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_note_suggestion_request
    ON note_suggestions(note_id, author_user_id, client_request_id)
    WHERE client_request_id IS NOT NULL;
