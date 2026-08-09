"""
Schema -> Appwrite collection mapping reference

Foreign keys are stored as string attributes containing the related row's
$id value.

Table: users
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- google_id VARCHAR(255) UNIQUE NOT NULL -> string(255) attribute: google_id (unique)
- email VARCHAR(255) UNIQUE NOT NULL -> email attribute: email (unique)
- name VARCHAR(255) -> string(255) attribute: name
- picture_url TEXT -> url attribute: picture_url
- banner_color VARCHAR(7) DEFAULT '#fecae1' -> string(7) attribute: banner_color
- avatar_file_id VARCHAR(64) -> string(64) attribute: avatar_file_id
- avatar_source VARCHAR(16) -> string(16) attribute: avatar_source
- avatar_file_size_bytes INTEGER NOT NULL DEFAULT 0 -> integer attribute: avatar_file_size_bytes
- tier VARCHAR(16) NOT NULL DEFAULT 'free' -> string(16) attribute: tier
- onboarding_complete BOOLEAN NOT NULL DEFAULT 0 -> boolean attribute: onboarding_complete
- onboarding_step INTEGER NOT NULL DEFAULT 1 -> integer attribute: onboarding_step
- education_level VARCHAR(32) -> string(32) attribute: education_level
- class_year VARCHAR(64) -> string(64) attribute: class_year
- emory_student BOOLEAN -> boolean attribute: emory_student
- emory_email VARCHAR(255) -> email attribute: emory_email
- discord_id VARCHAR(64) -> string(64) attribute: discord_id
- discord_username VARCHAR(255) -> string(255) attribute: discord_username
- discord_linked_at DATETIME -> datetime attribute: discord_linked_at
- created_at DATETIME NOT NULL -> datetime attribute: created_at
- last_login DATETIME -> datetime attribute: last_login

Table: user_settings
- user_id INTEGER PRIMARY KEY FK users.id -> string attribute: user_id (unique)
- canvas_ical_url TEXT -> url attribute: canvas_ical_url
- other_ical_urls_json TEXT -> string attribute: other_ical_urls_json
- ics_secret_token VARCHAR(255) UNIQUE -> string(255) attribute: ics_secret_token (unique)
- feed_refresh_minutes INTEGER NOT NULL DEFAULT 15 -> integer attribute: feed_refresh_minutes
- preferred_calendar_view VARCHAR(16) NOT NULL DEFAULT 'week' -> string(16) attribute: preferred_calendar_view
- interface_theme VARCHAR(32) DEFAULT 'system-match' -> string(32) attribute: interface_theme
- dashboard_layout_json TEXT -> text attribute: dashboard_layout_json
- dashboard_checklist_hidden_signature VARCHAR(64) -> string(64) attribute: dashboard_checklist_hidden_signature
- notes_page_setup_json TEXT -> longtext attribute: notes_page_setup_json
- created_at DATETIME NOT NULL -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at

Table: user_courses
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- term VARCHAR(64) NOT NULL -> string(64) attribute: term
- subject VARCHAR(64) NOT NULL -> string(64) attribute: subject
- catalog VARCHAR(64) NOT NULL -> string(64) attribute: catalog
- course_name VARCHAR(255) -> string(255) attribute: course_name
- section_number VARCHAR(64) -> string(64) attribute: section_number
- instructor_name VARCHAR(255) -> string(255) attribute: instructor_name
- source VARCHAR(32) NOT NULL DEFAULT 'settings' -> string(32) attribute: source
- crn VARCHAR(64) -> string(64) attribute: crn
- added_at DATETIME NOT NULL -> datetime attribute: added_at
- UNIQUE (user_id, term, subject, catalog, crn)

Table: course_seat_tracks
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- term VARCHAR(64) NOT NULL -> string(64) attribute: term
- subject VARCHAR(64) NOT NULL -> string(64) attribute: subject
- catalog VARCHAR(64) NOT NULL -> string(64) attribute: catalog
- crn VARCHAR(64) -> string(64) attribute: crn
- section_id VARCHAR(255) -> string(255) attribute: section_id
- course_code VARCHAR(64) -> string(64) attribute: course_code
- course_title VARCHAR(255) -> string(255) attribute: course_title
- last_status VARCHAR(64) -> string(64) attribute: last_status
- last_seats_available INTEGER -> integer attribute: last_seats_available
- interval_minutes INTEGER NOT NULL DEFAULT 30 -> integer attribute: interval_minutes
- next_check_at DATETIME -> datetime attribute: next_check_at
- last_waitlist_total INTEGER -> integer attribute: last_waitlist_total
- last_waitlist_capacity INTEGER -> integer attribute: last_waitlist_capacity
- cooldown_until_closed BOOLEAN NOT NULL DEFAULT 0 -> boolean attribute: cooldown_until_closed
- enabled BOOLEAN NOT NULL DEFAULT 1 -> boolean attribute: enabled
- last_checked_at DATETIME -> datetime attribute: last_checked_at
- last_notified_at DATETIME -> datetime attribute: last_notified_at
- created_at DATETIME NOT NULL -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at
- UNIQUE (user_id, term, subject, catalog, crn)

Table: calendar_cache
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- feed_url TEXT -> string(2048) attribute: feed_url
- feed_url_hash TEXT -> string(64) attribute: feed_url_hash
- event_uid VARCHAR(255) -> string(255) attribute: event_uid
- event_title TEXT -> string attribute: event_title
- event_start DATETIME -> datetime attribute: event_start
- event_end DATETIME -> datetime attribute: event_end
- is_all_day BOOLEAN NOT NULL DEFAULT 0 -> boolean attribute: is_all_day
- event_type VARCHAR(64) -> string(64) attribute: event_type
- course_name VARCHAR(255) -> string(255) attribute: course_name
- raw_description TEXT -> string attribute: raw_description
- fetched_at DATETIME -> datetime attribute: fetched_at
- UNIQUE (user_id, feed_url_hash, event_uid)

Table: calendar_feeds
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- feed_url TEXT -> string(2048) attribute: feed_url
- feed_url_hash TEXT -> string(64) attribute: feed_url_hash
- calendar_name VARCHAR(255) -> string(255) attribute: calendar_name
- etag_header TEXT -> string(1024) attribute: etag_header
- last_modified_header TEXT -> string(1024) attribute: last_modified_header
- last_fetch_http_code INTEGER -> integer attribute: last_fetch_http_code
- last_fetched DATETIME -> datetime attribute: last_fetched
- created_at DATETIME -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at

Table: user_calendar_preferences
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- calendar_name VARCHAR(255) -> string(255) attribute: calendar_name
- display_name VARCHAR(255) -> string(255) attribute: display_name
- color_hex VARCHAR(7) NOT NULL DEFAULT '#6366f1' -> string(7) attribute: color_hex
- visible BOOLEAN NOT NULL DEFAULT 1 -> boolean attribute: visible
- created_at DATETIME -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at
- UNIQUE (user_id, calendar_name)

Table: user_events
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- title VARCHAR(255) NOT NULL -> string(255) attribute: title
- description TEXT -> string attribute: description
- start DATETIME NOT NULL -> datetime attribute: start
- end DATETIME NOT NULL -> datetime attribute: end
- is_all_day BOOLEAN NOT NULL DEFAULT 0 -> boolean attribute: is_all_day
- color VARCHAR(7) -> string(7) attribute: color
- calendar_id VARCHAR(255) -> string(255) attribute: calendar_id
- created_at DATETIME NOT NULL -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at

Table: user_calendar_sources
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- source_id VARCHAR(255) -> string(255) attribute: source_id
- kind VARCHAR(32) -> string(32) attribute: kind
- default_name VARCHAR(255) -> string(255) attribute: default_name
- created_at DATETIME NOT NULL -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at
- UNIQUE (user_id, source_id)

Table: user_event_overrides
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- event_ref VARCHAR(255) -> string(255) attribute: event_ref
- hidden BOOLEAN NOT NULL DEFAULT 0 -> boolean attribute: hidden
- title VARCHAR(255) -> string(255) attribute: title
- description TEXT -> string attribute: description
- start DATETIME -> datetime attribute: start
- end DATETIME -> datetime attribute: end
- is_all_day BOOLEAN -> boolean attribute: is_all_day
- calendar_id VARCHAR(255) -> string(255) attribute: calendar_id
- color VARCHAR(7) -> string(7) attribute: color
- created_at DATETIME NOT NULL -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at
- UNIQUE (user_id, event_ref)

Table: calendar_shares
- id INTEGER PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- share_code VARCHAR(16) UNIQUE -> string(16) attribute: share_code (unique)
- is_active BOOLEAN NOT NULL DEFAULT 1 -> boolean attribute: is_active
- include_all_calendars BOOLEAN NOT NULL DEFAULT 1 -> boolean attribute: include_all_calendars
- calendar_ids_json TEXT -> string/text attribute: calendar_ids_json
- date_scope VARCHAR(16) NOT NULL DEFAULT 'all' -> string(16) attribute: date_scope
- fixed_start DATETIME -> datetime attribute: fixed_start
- fixed_end DATETIME -> datetime attribute: fixed_end
- rolling_days INTEGER -> integer attribute: rolling_days
- created_at DATETIME NOT NULL -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at

Table: shared_files
- id VARCHAR(36) PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- folder_id VARCHAR(64) -> string(64) attribute: folder_id
- original_filename VARCHAR(255) NOT NULL -> string(255) attribute: original_filename
- stored_path VARCHAR(512) NOT NULL -> string(512) attribute: stored_path
- storage_backend VARCHAR(32) -> string(32) attribute: storage_backend
- storage_bucket_id VARCHAR(64) -> string(64) attribute: storage_bucket_id
- storage_file_id VARCHAR(64) -> string(64) attribute: storage_file_id
- file_size_bytes INTEGER NOT NULL -> integer attribute: file_size_bytes
- mime_type VARCHAR(127) -> string(127) attribute: mime_type
- share_code VARCHAR(10) UNIQUE -> string(10) attribute: share_code (unique)
- is_public BOOLEAN NOT NULL DEFAULT 0 -> boolean attribute: is_public
- expires_at DATETIME NOT NULL -> datetime attribute: expires_at
- created_at DATETIME NOT NULL -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at
- downloaded_count INTEGER NOT NULL DEFAULT 0 -> integer attribute: downloaded_count

Table: file_folders
- id VARCHAR(36) PRIMARY KEY -> use Appwrite $id (string); no separate attribute
- user_id INTEGER FK users.id -> string attribute: user_id
- name VARCHAR(255) NOT NULL -> string(255) attribute: name
- parent_folder_id VARCHAR(64) -> string(64) attribute: parent_folder_id
- is_public BOOLEAN NOT NULL DEFAULT 0 -> boolean attribute: is_public
- share_code VARCHAR(10) UNIQUE -> string(10) attribute: share_code (unique)
- order INTEGER -> integer attribute: order
- created_at DATETIME NOT NULL -> datetime attribute: created_at
- updated_at DATETIME -> datetime attribute: updated_at
"""

from appwrite.client import Client
from appwrite.services.tables_db import TablesDB
from flask import current_app, has_app_context

from config import ENVIRONMENT_CONFIG_EXTENSION_KEY, EnvironmentConfig, load_environment_config


def _load_environment_config() -> EnvironmentConfig:
	if has_app_context():
		configured = current_app.extensions.get(ENVIRONMENT_CONFIG_EXTENSION_KEY)
		if configured is not None:
			return configured
	return load_environment_config()


environment_config = _load_environment_config()


client = Client()
_endpoint = environment_config.appwrite_endpoint
_project = environment_config.appwrite_project_id
_api_key = environment_config.appwrite_api_key
_database_id = environment_config.appwrite_database_id_raw

if _endpoint:
	client.set_endpoint(_endpoint)
if _project:
	client.set_project(_project)
if _api_key:
	client.set_key(_api_key)

# tablesdb may be None if Appwrite env vars are not configured (e.g., during static imports)
tablesdb = TablesDB(client) if _endpoint and _project else None
DATABASE_ID = _database_id
PROJECT_ID = _project
ENDPOINT = _endpoint
PROFILE_AVATAR_BUCKET_ID = environment_config.appwrite_profile_avatar_bucket_id
FILE_SHARE_BUCKET_ID = environment_config.appwrite_file_share_bucket_id
NOTES_MEDIA_BUCKET_ID = environment_config.appwrite_notes_media_bucket_id
CHAT_ATTACHMENTS_BUCKET_ID = environment_config.appwrite_chat_attachments_bucket_id

COLLECTIONS = {
	"users": "users",
	"user_settings": "user_settings",
	"user_courses": "user_courses",
	"course_seat_tracks": "course_seat_tracks",
	"calendar_cache": "calendar_cache",
	"calendar_feeds": "calendar_feeds",
	"user_calendar_preferences": "user_calendar_preferences",
	"user_events": "user_events",
	"user_calendar_sources": "user_calendar_sources",
	"user_event_overrides": "user_event_overrides",
	"calendar_shares": "calendar_shares",
	"daily_quotes": "daily_quotes",
	"task_lists": "task_lists",
	"tasks": "tasks",
	"task_completions": "task_completions",
	"shared_files": "shared_files",
	"file_folders": "file_folders",
	"note_folders": "note_folders",
	"notes": "notes",
	"note_media": "note_media",
	"note_access_grants": "note_access_grants",
	"note_share_invitations": "note_share_invitations",
	"note_collaboration_documents": "note_collaboration_documents",
	"note_versions": "note_versions",
	"note_suggestions": "note_suggestions",
	"note_comment_threads": "note_comment_threads",
	"note_comment_replies": "note_comment_replies",
	"note_access_events": "note_access_events",
	"user_notifications": "user_notifications",
	"push_subscriptions": "push_subscriptions",
	"notification_preferences": "notification_preferences",
	"notification_deliveries": "notification_deliveries",
	"calendar_reminder_claims": "calendar_reminder_claims",
	"chat_channels": "chat_channels",
	"chat_messages": "chat_messages",
	"chat_attachments": "chat_attachments",
	"chat_dm_threads": "chat_dm_threads",
	"chat_blocks": "chat_blocks",
	"chat_presence": "chat_presence",
	"chat_read_states": "chat_read_states",
	"chat_events": "chat_events",
	"chat_link_previews": "chat_link_previews",
	"chat_bridge_config": "chat_bridge_config",
	"app_config": "chat_bridge_config",
	"admin_requests": "admin_requests",
	"dashboard_widgets": "dashboard_widgets",
	"focus_routines": "focus_routines",
	"focus_sessions": "focus_sessions",
	"focus_session_events": "focus_session_events",
	"user_invites": "user_invites",
	"user_invite_attributions": "user_invite_attributions",
	"user_invite_tier_events": "user_invite_tier_events",
}
