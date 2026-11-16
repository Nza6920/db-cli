"""PostgreSQL connections with a verified database-enforced read-only session."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import importlib
import time
from typing import Any

from db_query.config import Profile
from db_query.errors import RunnerError
from db_query.mysql_runner import ConnectionResult


def _driver() -> Any:
    try:
        return importlib.import_module("psycopg")
    except ImportError as exc:
        raise RunnerError(
            "DRIVER_NOT_INSTALLED",
            "PostgreSQL support requires: pip install 'db-query[postgresql]'",
            4,
        ) from exc


@contextmanager
def read_only_session(profile: Profile, password: str) -> Iterator[Any]:
    driver = _driver()
    options: dict[str, Any] = {
        "host": profile.host,
        "port": profile.port,
        "dbname": profile.database,
        "user": profile.username,
        "password": password,
        "connect_timeout": profile.connect_timeout_seconds,
        "sslmode": {"required": "verify-full", "preferred": "prefer", "disabled": "disable"}[profile.tls],
        "autocommit": True,
    }
    if profile.tls_ca_file:
        options["sslrootcert"] = profile.tls_ca_file
    elif profile.tls == "required":
        options["sslrootcert"] = "system"
    try:
        with driver.connect(**options) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET default_transaction_read_only = on")
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone() != ("on",):
                    raise RunnerError(
                        "CONNECTION_FAILED",
                        "database session did not enter read-only transaction mode",
                        4,
                    )
            yield connection
    except RunnerError:
        raise
    except (driver.Error, OSError, ValueError) as exc:
        details: dict[str, object] = {}
        if sqlstate := getattr(exc, "sqlstate", None):
            details["sqlstate"] = sqlstate
        # Driver messages can contain passwords and complete connection strings.
        raise RunnerError(
            "CONNECTION_FAILED",
            "PostgreSQL connection, authentication, TLS or read-only session setup failed",
            4,
            **details,
        ) from exc


def validate_connection(profile: Profile, password: str) -> ConnectionResult:
    started = time.monotonic()
    with read_only_session(profile, password) as connection:
        tls_active = bool(connection.pgconn.ssl_in_use)
    return ConnectionResult(round((time.monotonic() - started) * 1000), tls_active)
