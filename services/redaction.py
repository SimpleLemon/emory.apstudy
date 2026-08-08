"""Shared patterns for removing secrets from diagnostic text."""

import re


SECRET_TEXT_RE = re.compile(
    r"((?:[?&]|\b)(?:secret|key|token|password)=)[^&\s]+",
    re.IGNORECASE,
)
