from __future__ import annotations

import argparse
import json
import os
import sys

from db_query.config import Config, ConfigError, config_path, load_config, profile_warnings
from db_query.sql_safety import SqlSafetyError, validate_read_only
from db_query.usql_runner import RunnerError, parse_json_rows, run_query


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="db-query")
    parser.add_argument("--config", help="path to config.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("profiles", help="list configured profiles safely")
    validate = subparsers.add_parser("validate", help="validate configuration")
    validate.add_argument("--profile", help="validate only one profile")
    validate.add_argument("--connect", action="store_true", help="also test connectivity")
    validate.add_argument("--confirm-profile", help="confirm a production profile")
    query = subparsers.add_parser("query", help="run one read-only query")
    query.add_argument("--profile", required=True, help="profile name")
    sql_input = query.add_mutually_exclusive_group(required=True)
    sql_input.add_argument("--sql", help="SQL text")
    sql_input.add_argument("--stdin", action="store_true", help="read SQL from stdin")
    query.add_argument("--confirm-profile", help="confirm a production profile")
    query.add_argument("--format", choices=("json", "table", "csv"), default="json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(config_path(args.config))
    except ConfigError as exc:
        return _error("CONFIG_ERROR", str(exc), 2)

    if args.command == "profiles":
        _emit_warnings(_warnings_for(config, sorted(config.profiles)))
        profiles = []
        for profile in sorted(config.profiles.values(), key=lambda item: item.name):
            profiles.append(
                {
                    "name": profile.name,
                    "environment": profile.environment,
                    "host": profile.host,
                    "port": profile.port,
                    "database": profile.database,
                    "tls": profile.tls,
                    "password_env": profile.password_env,
                    "password_available": profile.password_env in os.environ,
                }
            )
        print(json.dumps({"profiles": profiles}, ensure_ascii=False))
        return 0
    if args.command == "validate":
        if args.connect:
            if not args.profile:
                return _error("PROFILE_REQUIRED", "--connect requires --profile", 2)
            if args.profile not in config.profiles:
                return _error("PROFILE_NOT_FOUND", f"unknown profile: {args.profile}", 2)
            profile = config.profiles[args.profile]
            _emit_warnings(_warnings_for(config, [profile.name]))
            if profile.environment == "production" and args.confirm_profile != profile.name:
                return _error(
                    "PRODUCTION_CONFIRMATION_REQUIRED",
                    f"production connection test requires --confirm-profile {profile.name}",
                    3,
                )
            password = os.environ.get(profile.password_env)
            if password is None:
                return _error(
                    "PASSWORD_ENV_MISSING",
                    f"required environment variable is not set: {profile.password_env}",
                    3,
                )
            try:
                result = run_query(profile, password, "SELECT 1 AS connection", "json")
                parse_json_rows(result.stdout)
            except RunnerError as exc:
                return _error(exc.code, str(exc), exc.exit_code)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "profile": profile.name,
                        "connected": True,
                        "duration_ms": result.duration_ms,
                    }
                )
            )
            return 0
        if args.profile and args.profile not in config.profiles:
            return _error("PROFILE_NOT_FOUND", f"unknown profile: {args.profile}", 2)
        checked = [args.profile] if args.profile else sorted(config.profiles)
        warnings = _warnings_for(config, checked)
        _emit_warnings(warnings)
        missing_env = [
            config.profiles[name].password_env
            for name in checked
            if config.profiles[name].password_env not in os.environ
        ]
        if missing_env:
            return _error(
                "PASSWORD_ENV_MISSING",
                "one or more password environment variables are not set",
                3,
                missing=missing_env,
            )
        print(
            json.dumps(
                {
                    "ok": True,
                    "profiles": checked,
                    "missing_password_envs": missing_env,
                    "warnings": warnings,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "query":
        if args.profile not in config.profiles:
            return _error("PROFILE_NOT_FOUND", f"unknown profile: {args.profile}", 2)
        profile = config.profiles[args.profile]
        _emit_warnings(_warnings_for(config, [profile.name]))
        sql = sys.stdin.read() if args.stdin else args.sql
        try:
            validate_read_only(sql)
        except SqlSafetyError as exc:
            return _error(exc.code, str(exc), 2)
        if profile.environment == "production" and args.confirm_profile != profile.name:
            return _error(
                "PRODUCTION_CONFIRMATION_REQUIRED",
                f"production query requires --confirm-profile {profile.name}",
                3,
            )
        password = os.environ.get(profile.password_env)
        if password is None:
            return _error(
                "PASSWORD_ENV_MISSING",
                f"required environment variable is not set: {profile.password_env}",
                3,
            )
        try:
            result = run_query(profile, password, sql, args.format)
            if args.format != "json":
                print(result.stdout, end="")
                return 0
            columns, rows = parse_json_rows(result.stdout)
        except RunnerError as exc:
            return _error(exc.code, str(exc), exc.exit_code)
        print(
            json.dumps(
                {
                    "ok": True,
                    "profile": profile.name,
                    "environment": profile.environment,
                    "duration_ms": result.duration_ms,
                    "row_count": len(rows),
                    "columns": columns,
                    "rows": rows,
                },
                ensure_ascii=False,
            )
        )
        return 0
    return _error("INTERNAL_ERROR", "unhandled command", 2)


def _error(code: str, message: str, exit_code: int, **details: object) -> int:
    error = {"code": code, "message": message, **details}
    print(json.dumps({"ok": False, "error": error}))
    return exit_code


def _warnings_for(config: Config, profile_names: list[str]) -> list[str]:
    warnings = list(config.warnings)
    for name in profile_names:
        warnings.extend(profile_warnings(config.profiles[name]))
    return warnings


def _emit_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
