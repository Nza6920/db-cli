from __future__ import annotations

from dataclasses import dataclass
import ssl
import time
from typing import Any

from db_query.config import Profile
from db_query.errors import RunnerError
from db_query.redaction import redact_connection_message
from db_query.result_encoding import normalize_rows


@dataclass(frozen=True)
class ConnectionResult:
    duration_ms: int
    tls_active: bool


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, object]]
    duration_ms: int


class _ReadOnlySessionError(RuntimeError):
    pass


def validate_connection(profile: Profile, password: str) -> ConnectionResult:
    import pymysql

    started = time.monotonic()
    try:
        with pymysql.connect(**_connection_options(profile, password)) as connection:
            with connection.cursor() as cursor:
                _configure_read_only(cursor)
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


def run_json_query(profile: Profile, password: str, sql: str) -> QueryResult:
    import pymysql

    started = time.monotonic()
    phase = "connection"
    try:
        with pymysql.connect(**_connection_options(profile, password)) as connection:
            with connection.cursor() as cursor:
                phase = "session"
                _configure_read_only(cursor)
                phase = "query"
                cursor.execute(sql)
                description = cursor.description or ()
                columns = [column[0] for column in description]
                raw_rows = cursor.fetchall()
    except pymysql.MySQLError as exc:
        if phase == "query":
            code = "QUERY_TIMEOUT" if _mysql_errno(exc) == 2013 else "QUERY_FAILED"
            raise RunnerError(
                code,
                "database query timed out" if code == "QUERY_TIMEOUT" else "database query failed",
                5,
                **_mysql_error_details(exc),
            ) from exc
        raise RunnerError(
            "CONNECTION_FAILED",
            "database connection or read-only session setup failed",
            4,
            **_mysql_error_details(exc),
        ) from exc
    except _ReadOnlySessionError as exc:
        raise RunnerError("CONNECTION_FAILED", str(exc), 4) from exc
    except RunnerError:
        raise
    except (OSError, ssl.SSLError) as exc:
        code = "QUERY_FAILED" if phase == "query" else "CONNECTION_FAILED"
        raise RunnerError(
            code,
            "database query failed"
            if code == "QUERY_FAILED"
            else "database connection or TLS setup failed",
            5 if code == "QUERY_FAILED" else 4,
        ) from exc
    except (UnicodeError, TypeError, ValueError) as exc:
        code = "RESULT_ENCODING_FAILED" if phase == "query" else "CONNECTION_FAILED"
        raise RunnerError(
            code,
            "database result could not be decoded safely"
            if code == "RESULT_ENCODING_FAILED"
            else "database connection configuration failed",
            5 if code == "RESULT_ENCODING_FAILED" else 4,
        ) from exc

    return QueryResult(
        columns=columns,
        rows=normalize_rows(columns, raw_rows),
        duration_ms=round((time.monotonic() - started) * 1000),
    )


def _connection_options(profile: Profile, password: str) -> dict[str, Any]:
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
            f"unsupported TLS mode for direct MySQL connection: {profile.tls}",
            4,
        )
    return options


def _configure_read_only(cursor: Any) -> None:
    cursor.execute("SET SESSION TRANSACTION READ ONLY")
    cursor.execute("SELECT @@SESSION.transaction_read_only")
    if cursor.fetchone() != (1,):
        raise _ReadOnlySessionError(
            "database session did not enter read-only transaction mode"
        )


def _mysql_errno(exc: BaseException) -> int | None:
    return exc.args[0] if exc.args and isinstance(exc.args[0], int) else None


def _mysql_error_details(exc: BaseException) -> dict[str, object]:
    details: dict[str, object] = {}
    if mysql_errno := _mysql_errno(exc):
        details["mysql_errno"] = mysql_errno
    if sqlstate := getattr(exc, "sqlstate", None):
        details["sqlstate"] = sqlstate
    return details
