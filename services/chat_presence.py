import hashlib
import logging
import re

from appwrite.exception import AppwriteException
from appwrite.query import Query
from appwrite.services.users import Users

from appwrite_client import COLLECTIONS
from appwrite_client import client as appwrite_client
from appwrite_helpers import first_row, get_row_safe, list_rows_all
from services.row_utils import row_id as _row_id
from services.universities import normalize_school_key, school_payload


logger = logging.getLogger(__name__)

CHAT_UNIVERSITY_LABEL_PREFIX = "chatuni"
LEGACY_CHAT_UNIVERSITY_LABEL_PREFIX = "chat_uni_"
APPWRITE_LABEL_RE = re.compile(r"^[A-Za-z0-9]{1,36}$")


def university_presence_label(school_key):
    """Return the Appwrite label used to reveal same-university chat presence."""
    normalized = normalize_school_key(school_key)
    if not normalized:
        return None
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:24]
    return f"{CHAT_UNIVERSITY_LABEL_PREFIX}{digest}"


def _is_chat_presence_label(label):
    text = str(label or "")
    return text.startswith(CHAT_UNIVERSITY_LABEL_PREFIX) or text.startswith(LEGACY_CHAT_UNIVERSITY_LABEL_PREFIX)


def _is_valid_appwrite_label(label):
    return bool(APPWRITE_LABEL_RE.fullmatch(str(label or "")))

def _school_key_for_user_doc(user_doc):
    if not user_doc:
        return None
    return (
        normalize_school_key(user_doc.get("school_key"))
        or school_payload(user_doc.get("school")).get("school_key")
    )


def _school_has_approved_channel(school_key):
    if not school_key:
        return False
    try:
        channel = first_row(
            COLLECTIONS["chat_channels"],
            [
                Query.equal("kind", ["university"]),
                Query.equal("school_key", [school_key]),
                Query.equal("approved", [True]),
            ],
        )
    except AppwriteException:
        logger.exception("Failed to check university chat approval for %s", school_key)
        return False
    return bool(channel)


def _account_to_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json")
    return {}


def _current_appwrite_labels(user_id):
    try:
        account = Users(appwrite_client).get(str(user_id))
    except Exception:
        logger.exception("Failed to load Appwrite user labels for %s", user_id)
        return None
    return list(_account_to_dict(account).get("labels") or [])


def sync_chat_presence_labels_for_user(user_id, user_doc=None):
    """Sync only Nest chat presence labels, preserving unrelated Appwrite labels."""
    user_id = str(user_id or "").strip()
    if not user_id:
        return []
    if user_doc is None:
        try:
            user_doc = get_row_safe(COLLECTIONS["users"], user_id, allow_missing=True)
        except Exception:
            logger.exception("Failed to load user row for presence label sync")
            user_doc = None

    school_key = _school_key_for_user_doc(user_doc)
    desired = set()
    if _school_has_approved_channel(school_key):
        label = university_presence_label(school_key)
        if label:
            desired.add(label)

    current_labels = _current_appwrite_labels(user_id)
    if current_labels is None:
        return []

    preserved = [
        label
        for label in current_labels
        if not _is_chat_presence_label(label) and _is_valid_appwrite_label(label)
    ]
    next_labels = preserved + sorted(desired)
    if set(next_labels) == set(current_labels) and len(next_labels) == len(current_labels):
        return next_labels

    try:
        Users(appwrite_client).update_labels(user_id, next_labels)
    except Exception:
        logger.exception("Failed to update Appwrite user labels for %s", user_id)
        return current_labels
    return next_labels


def sync_chat_presence_labels_for_school(school_key):
    """Refresh university presence labels for everyone with a matching school key."""
    normalized = normalize_school_key(school_key)
    if not normalized:
        return 0
    try:
        users = list_rows_all(
            COLLECTIONS["users"],
            [Query.equal("school_key", [normalized])],
        )
    except AppwriteException:
        logger.exception("Failed to list users for university presence label sync")
        return 0

    synced = 0
    for user_doc in users:
        user_id = _row_id(user_doc)
        if not user_id:
            continue
        sync_chat_presence_labels_for_user(user_id, user_doc)
        synced += 1
    return synced
