"""Access the immutable environment snapshot for the current app."""

from flask import current_app, has_app_context

from config import (
    ENVIRONMENT_CONFIG_EXTENSION_KEY,
    EnvironmentConfig,
    load_environment_config,
)


def runtime_environment_config() -> EnvironmentConfig:
    """Use an app's snapshot, with a fresh fallback for standalone callers."""
    if has_app_context():
        configured = current_app.extensions.get(ENVIRONMENT_CONFIG_EXTENSION_KEY)
        if configured is not None:
            return configured
    return load_environment_config()
