CREATE TABLE IF NOT EXISTS user_invites (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    label TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deactivated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_invites_code
    ON user_invites(code);

CREATE INDEX IF NOT EXISTS idx_user_invites_owner
    ON user_invites(owner_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS user_invite_attributions (
    id TEXT PRIMARY KEY,
    invite_id TEXT NOT NULL,
    inviter_user_id TEXT NOT NULL,
    invited_user_id TEXT,
    status TEXT NOT NULL DEFAULT 'invited'
        CHECK(status IN ('invited', 'joined')),
    signed_up_at TEXT NOT NULL,
    activation_signal TEXT,
    activation_at TEXT,
    joined_at TEXT,
    initial_tier TEXT NOT NULL,
    current_tier TEXT NOT NULL,
    is_anonymized INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_user_invite_attributions_invite
    ON user_invite_attributions(invite_id);

CREATE INDEX IF NOT EXISTS idx_user_invite_attributions_inviter
    ON user_invite_attributions(inviter_user_id, signed_up_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_invite_attributions_invited
    ON user_invite_attributions(invited_user_id)
    WHERE invited_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS user_invite_tier_events (
    id TEXT PRIMARY KEY,
    attribution_id TEXT NOT NULL,
    invited_user_id TEXT NOT NULL,
    from_tier TEXT NOT NULL,
    to_tier TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_invite_tier_events_attribution
    ON user_invite_tier_events(attribution_id, changed_at DESC);
