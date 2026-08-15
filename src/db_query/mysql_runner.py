from __future__ import annotations

from dataclasses import dataclass
import ssl
import time
from typing import Any

from db_query.config import Profile
from db_query.errors import RunnerError
from db_query.redaction import redact_connection_message


@dataclass(frozen=True)
class ConnectionResult:
    duration_ms: int
    tls_active: bool


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
    started = time.monotonic()
    try:
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
        with pymysql.connect(**options) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("SELECT @@SESSION.transaction_read_only")
                if cursor.fetchone() != (1,):
                    raise _ReadOnlySessionError(
                        "database session did not enter read-only transaction mode"
                    )
                cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                tls_status = cursor.fetchone()
    except pymysql.MySQLError as exc:
        raise RunnerError(
            "CONNECTION_FAILED",
            redact_connection_message(str(exc), profile, password),
            4,
            **_mysql_error_details(exc),
        ) from exc
    except _ReadOnlySessionError as exc:
        raise RunnerError("CONNECTION_FAILED", str(exc), 4) from exc
    except RunnerError:
        raise
    except (OSError, ssl.SSLError) as exc:
        raise RunnerError(
            "CONNECTION_FAILED",
            redact_connection_message(
                str(exc) or "TLS initialization failed", profile, password
            ),
            4,
        ) from exc
    tls_active = bool(tls_status and len(tls_status) > 1 and tls_status[1])
    return ConnectionResult(
        duration_ms=round((time.monotonic() - started) * 1000),
        tls_active=tls_active,
    )


def _mysql_error_details(exc: BaseException) -> dict[str, object]:
    details: dict[str, object] = {}
    if exc.args and isinstance(exc.args[0], int):
        details["mysql_errno"] = exc.args[0]
    if sqlstate := getattr(exc, "sqlstate", None):
        details["sqlstate"] = sqlstate
    return details
