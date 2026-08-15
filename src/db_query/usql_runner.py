from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote, urlencode

from db_query.config import Profile
from db_query.errors import RunnerError
from db_query.redaction import redact_connection_message


@dataclass(frozen=True)
class QueryResult:
    stdout: str
    duration_ms: int


def run_query(profile: Profile, password: str, sql: str, output_format: str) -> QueryResult:
    executable = shutil.which("usql")
    if not executable:
        raise RunnerError("USQL_NOT_FOUND", "usql is not installed or not on PATH", 127)

    with tempfile.TemporaryDirectory(prefix="db-query-") as temp_dir:
        directory = Path(temp_dir)
        config_file = directory / "usql-config.yaml"
        sql_file = directory / "query.sql"
        config_file.write_text(_usql_config(profile, password), encoding="utf-8")
        sql_file.write_text(_terminated_sql(sql), encoding="utf-8")
        config_file.chmod(0o600)
        sql_file.chmod(0o600)

        format_flags = {"json": ["--json"], "csv": ["--csv"], "table": []}[output_format]
        command = [
            executable,
            "--config",
            str(config_file),
            "--no-init",
            "--no-password",
            "--quiet",
            *format_flags,
            "--file",
            str(sql_file),
            "db_query",
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=profile.query_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RunnerError(
                "QUERY_TIMEOUT",
                f"query exceeded {profile.query_timeout_seconds} seconds",
                5,
            ) from exc
        duration_ms = round((time.monotonic() - started) * 1000)

    if completed.returncode != 0:
        message = redact_connection_message(
            completed.stderr.strip() or "usql failed", profile, password
        )
        connection_markers = (
            "access denied",
            "authentication",
            "certificate",
            "connection",
            "dial tcp",
            "tls",
            "timeout",
        )
        is_connection = any(marker in message.lower() for marker in connection_markers)
        raise RunnerError(
            "CONNECTION_FAILED" if is_connection else "QUERY_FAILED",
            message,
            4 if is_connection else 5,
        )
    return QueryResult(stdout=completed.stdout, duration_ms=duration_ms)


def parse_json_rows(output: str) -> tuple[list[str], list[dict[str, object]]]:
    try:
        rows = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RunnerError("USQL_OUTPUT_INVALID", "usql returned invalid JSON", 5) from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RunnerError("USQL_OUTPUT_INVALID", "usql returned an unexpected JSON shape", 5)
    columns = list(rows[0]) if rows else []
    return columns, rows


def _usql_config(profile: Profile, password: str) -> str:
    tls = {"required": "true", "preferred": "preferred", "disabled": "false"}[profile.tls]
    options = {
        "timeout": f"{profile.connect_timeout_seconds}s",
        "readTimeout": f"{profile.query_timeout_seconds}s",
        "writeTimeout": f"{profile.query_timeout_seconds}s",
        "tls": tls,
    }
    host = f"[{profile.host}]" if ":" in profile.host else profile.host
    userinfo = f"{quote(profile.username, safe='')}:{quote(password, safe='')}"
    database = quote(profile.database, safe="") if profile.database else ""
    query = urlencode(options, quote_via=quote)
    dsn = f"mysql://{userinfo}@{host}:{profile.port}/{database}?{query}"
    return f"connections:\n  db_query: {_yaml_string(dsn)}\n"


def _terminated_sql(sql: str) -> str:
    statement = sql.rstrip()
    if not statement.endswith(";"):
        statement += ";"
    return statement + "\n"


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
