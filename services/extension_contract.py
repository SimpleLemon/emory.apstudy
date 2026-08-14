"""Stable contracts shared by the Nest browser-extension API and later phases."""

import re
from urllib.parse import urlsplit


EXTENSION_CONTRACT_VERSION = 1
EXTENSION_CALENDAR_CAPABILITY = "calendar_integration"
EXTENSION_SOURCE_REF_PREFIX = "src1:"
EXTENSION_CALENDAR_ROLLOUT_ENV = "APSTUDY_EXTENSION_CALENDAR_ROLLOUT"
EXTENSION_CALENDAR_READ_ONLY_ROLLOUT = "readonly-v1"
# This is deliberately an exact-value mode, not a generic boolean parser.
# The operator must opt into the complete read-only cohort as one reviewed
# configuration value.  No environment value enables source mutation,
# mirroring, or writeback.
READ_ONLY_ROLLOUT_CAPABILITIES = frozenset({
    EXTENSION_CALENDAR_CAPABILITY,
    "calendar_read",
    "calendar_upload",
    "calendar_projection",
    "calendar_shares_ics",
})
DESTRUCTIVE_EXTENSION_CAPABILITIES = frozenset({
    "calendar_mirroring",
    "calendar_two_way_writeback",
    "calendar_source_mutation",
})
# These are intentionally fail-closed.  Tests and a future rollout may supply
# an app-local override, but production must not acquire calendar powers merely
# because a consent row exists.
EXTENSION_CALENDAR_INTEGRATION_ENABLED = False
EXTENSION_CAPABILITIES = {
    EXTENSION_CALENDAR_CAPABILITY: EXTENSION_CALENDAR_INTEGRATION_ENABLED,
    "calendar_read": False,
    "calendar_upload": False,
    "calendar_projection": False,
    "calendar_mirroring": False,
    "calendar_two_way_writeback": False,
    "calendar_source_mutation": False,
    "calendar_shares_ics": False,
}

CONSENT_SCOPES = (
    "full_history_upload",
    "ongoing_read",
    "two_way_writeback",
    "mirroring",
    "shares_ics_inclusion",
)
CONSENT_SCOPE_SET = frozenset(CONSENT_SCOPES)

CANVAS_LEGACY_SOURCE_KEY = "canvas"
CANVAS_SOURCE_KEY_PREFIX = "canvas:"
CANVAS_SOURCE_KEY_PATTERN = re.compile(r"^canvas:[0-9a-f]{64}$")
ACCOUNT_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CURRENT_CONSENT_SCOPES = frozenset({
    "full_history_upload",
    "ongoing_read",
    "shares_ics_inclusion",
})


class ExtensionContractError(ValueError):
    """A client-correctable contract validation error."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def extension_read_only_rollout_enabled(value):
    """Return true only for the reviewed, exact read-only rollout value."""
    return value == EXTENSION_CALENDAR_READ_ONLY_ROLLOUT


def extension_capabilities_for_rollout(value):
    """Build the production capability snapshot for an operator rollout.

    Unknown, empty, boolean-looking, and future values remain fail-closed.
    Destructive capabilities are never enabled by this rollout mode; their
    existing app-config injection seam is retained for isolated tests and
    separately authorized future work.
    """
    capabilities = dict(EXTENSION_CAPABILITIES)
    if extension_read_only_rollout_enabled(value):
        for capability in READ_ONLY_ROLLOUT_CAPABILITIES:
            capabilities[capability] = True
    for capability in DESTRUCTIVE_EXTENSION_CAPABILITIES:
        capabilities[capability] = False
    return capabilities


def validate_version(value, *, default=None):
    if value is None and default is not None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int) or value != EXTENSION_CONTRACT_VERSION:
        raise ExtensionContractError(
            "unsupported_version",
            f"Only consent contract version {EXTENSION_CONTRACT_VERSION} is supported.",
        )
    return value


def canonical_canvas_source_key(account_key):
    """Return the account-bound Canvas consent key used by contract v1."""
    account_key = validate_account_key(account_key)
    # The extension already sends the SHA-256 account binding. Hashing it
    # again would make the server reject the exact source_key it received.
    return f"{CANVAS_SOURCE_KEY_PREFIX}{account_key}"


def validate_source_key(value, *, account_key=None, allow_legacy=True):
    """Accept only the account-bound Canvas key or the one-release legacy key."""
    source_key = value if isinstance(value, str) else ""
    if source_key == CANVAS_LEGACY_SOURCE_KEY and allow_legacy:
        return source_key
    if not CANVAS_SOURCE_KEY_PATTERN.fullmatch(source_key):
        raise ExtensionContractError(
            "invalid_source_key",
            "source_key must be canvas or canvas:<64 lowercase hexadecimal characters>.",
        )
    if account_key is not None and source_key != canonical_canvas_source_key(account_key):
        raise ExtensionContractError(
            "source_key_account_mismatch",
            "source_key must be derived from account_key.",
        )
    return source_key


def validate_account_key(value):
    account_key = value.strip() if isinstance(value, str) else ""
    if not ACCOUNT_KEY_PATTERN.fullmatch(account_key):
        raise ExtensionContractError(
            "invalid_account_key",
            "account_key must be exactly 64 lowercase hexadecimal characters.",
        )
    return account_key


def validate_grant_scopes(value):
    scopes = validate_scopes(value)
    if set(scopes) != CURRENT_CONSENT_SCOPES:
        raise ExtensionContractError(
            "exact_scope_set_required",
            "A v1 grant must include exactly full_history_upload, ongoing_read, and shares_ics_inclusion.",
        )
    return scopes


def validate_scopes(value):
    if not isinstance(value, list) or not value:
        raise ExtensionContractError("invalid_scopes", "scopes must be a non-empty array.")
    normalized = []
    for raw_scope in value:
        if not isinstance(raw_scope, str) or raw_scope not in CONSENT_SCOPE_SET:
            raise ExtensionContractError("invalid_scope", f"Unsupported consent scope: {raw_scope!r}.")
        if raw_scope not in normalized:
            normalized.append(raw_scope)
    return tuple(scope for scope in CONSENT_SCOPES if scope in normalized)


_CAPABILITY_ALIASES = {
    "calendar_read": ("calendar_read", "read", "import_read"),
    "calendar_upload": ("calendar_upload", "upload", "import_upload"),
    "calendar_projection": ("calendar_projection", "projection", "calendar_projection_read"),
    "calendar_mirroring": ("calendar_mirroring", "mirroring"),
    "calendar_two_way_writeback": (
        "calendar_two_way_writeback",
        "two_way_writeback",
        "writeback",
    ),
    "calendar_source_mutation": ("calendar_source_mutation", "source_mutation"),
    "calendar_shares_ics": (
        "calendar_shares_ics",
        "shares_ics_inclusion",
        "calendar_share_projection",
    ),
}


def extension_capability_enabled(capability, *, app=None):
    """Read a fail-closed capability snapshot with a test/deployment seam."""
    configured = None
    try:
        from flask import current_app, has_app_context

        if app is not None:
            configured = app.config.get("EXTENSION_CAPABILITIES")
            integration_enabled = app.config.get(
                "EXTENSION_CALENDAR_INTEGRATION_ENABLED",
                EXTENSION_CALENDAR_INTEGRATION_ENABLED,
            )
        elif has_app_context():
            configured = current_app.config.get("EXTENSION_CAPABILITIES")
            integration_enabled = current_app.config.get(
                "EXTENSION_CALENDAR_INTEGRATION_ENABLED",
                EXTENSION_CALENDAR_INTEGRATION_ENABLED,
            )
        else:
            from config import load_environment_config

            environment_config = load_environment_config()
            configured = extension_capabilities_for_rollout(
                environment_config.extension_calendar_rollout_raw
            )
            integration_enabled = configured[EXTENSION_CALENDAR_CAPABILITY]
    except RuntimeError:
        integration_enabled = EXTENSION_CALENDAR_INTEGRATION_ENABLED

    capabilities = configured if isinstance(configured, dict) else EXTENSION_CAPABILITIES
    if not bool(integration_enabled) or not bool(
        capabilities.get(EXTENSION_CALENDAR_CAPABILITY, integration_enabled)
    ):
        return False
    return any(bool(capabilities.get(alias)) for alias in _CAPABILITY_ALIASES.get(capability, (capability,)))


def validate_https_avatar(value):
    """Return a safe HTTPS avatar URL, or None for absent/unsafe profile data."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return candidate
