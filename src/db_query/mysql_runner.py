from __future__ import annotations

from dataclasses import dataclass
import ssl
import time
from typing import Any
from urllib.parse import quote

from db_query.config import Profile


@dataclass(frozen=True)
class ConnectionResult:
    duration_ms: int


class RunnerError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        exit_code: int,
        **details: object,
    ):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.details = details


class _ReadOnlySessionError(RuntimeError):
    pass


def validate_connection(profile: Profile, password: str) -> ConnectionResult:
    import pymysql

    options: dict[str, Any] = {
        "host": profile.host,
        "port": profile.port,
        "user": profile.username,
        "password": password,
        "database": profile.database,
        "charset": "utf8mb4",
        "autocommit": True,
        "local_infile": False,
        "connect_timeout": profile.connect_timeout_seconds,
        "read_timeout": profile.query_timeout_seconds,
        "write_timeout": profile.query_timeout_seconds,
    }
    if profile.tls == "required":
        options["ssl"] = ssl.create_default_context()
    elif profile.tls == "disabled":
        options["ssl_disabled"] = True
    elif profile.tls != "preferred":
        raise RunnerError(
            "CONNECTION_FAILED",
            f"unsupported TLS mode for direct MySQL validation: {profile.tls}",
            4,
        )

    started = time.monotonic()
    try:
        with pymysql.connect(**options) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("SELECT @@SESSION.transaction_read_only")
                if cursor.fetchone() != (1,):
                    raise _ReadOnlySessionError(
                        "database session did not enter read-only transaction mode"
                    )
    except pymysql.MySQLError as exc:
        raise RunnerError(
            "CONNECTION_FAILED",
            _redact(str(exc), profile, password),
            4,
            **_mysql_error_details(exc),
        ) from exc
    except _ReadOnlySessionError as exc:
        raise RunnerError("CONNECTION_FAILED", str(exc), 4) from exc
    return ConnectionResult(duration_ms=round((time.monotonic() - started) * 1000))


def _mysql_error_details(exc: BaseException) -> dict[str, object]:
    details: dict[str, object] = {}
    if exc.args and isinstance(exc.args[0], int):
        details["mysql_errno"] = exc.args[0]
    if sqlstate := getattr(exc, "sqlstate", None):
        details["sqlstate"] = sqlstate
    return details


def _redact(message: str, profile: Profile, password: str) -> str:
    redacted = message
    if password:
        redacted = redacted.replace(password, "<REDACTED>").replace(
            quote(password, safe=""), "<REDACTED>"
        )
    return redacted.replace(profile.username, "<REDACTED>").replace(
        profile.url, "<REDACTED_DSN>"
    )
