from __future__ import annotations

from urllib.parse import quote

from db_query.config import Profile


def redact_connection_message(message: str, profile: Profile, password: str) -> str:
    redacted = message
    if password:
        redacted = redacted.replace(password, "<REDACTED>").replace(
            quote(password, safe=""), "<REDACTED>"
        )
    return redacted.replace(profile.username, "<REDACTED>").replace(
        profile.url, "<REDACTED_DSN>"
    )
