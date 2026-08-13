from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tomllib
from urllib.parse import parse_qsl, unquote, urlsplit


ENVIRONMENTS = {"development", "test", "staging", "production"}
TLS_MODES = {"required", "preferred", "disabled"}
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    url: str
    username: str
    password_env: str
    environment: str
    tls: str
    connect_timeout_seconds: int
    query_timeout_seconds: int
    host: str
    port: int
    database: str | None
    jdbc_options: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Config:
    profiles: dict[str, Profile]
    warnings: tuple[str, ...]


def config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if env_path := os.environ.get("DB_QUERY_CONFIG"):
        return Path(env_path).expanduser()
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "db-cli" / "config.toml"


def load_config(path: Path) -> Config:
    warnings = _file_warnings(path)
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    raw_profiles = raw.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ConfigError("config must contain at least one [profiles.<name>] table")

    profiles: dict[str, Profile] = {}
    for name, values in raw_profiles.items():
        if not isinstance(values, dict):
            raise ConfigError(f"profile {name!r} must be a table")
        profiles[name] = _parse_profile(name, values)
    return Config(profiles=profiles, warnings=tuple(warnings))


def profile_warnings(profile: Profile) -> tuple[str, ...]:
    if profile.environment == "production" and profile.tls != "required":
        return (
            f"production profile {profile.name!r} does not require TLS; "
            "database traffic may be unencrypted",
        )
    return ()


def _parse_profile(name: str, values: dict[str, object]) -> Profile:
    forbidden = {"password", "pass"}.intersection(values)
    if forbidden:
        raise ConfigError(
            f"profile {name!r} contains forbidden plaintext field: {sorted(forbidden)[0]}"
        )
    allowed = {
        "url",
        "username",
        "password_env",
        "environment",
        "tls",
        "connect_timeout_seconds",
        "query_timeout_seconds",
    }
    unknown = set(values).difference(allowed)
    if unknown:
        raise ConfigError(f"profile {name!r} has unknown field: {sorted(unknown)[0]}")

    url = _required_string(name, values, "url")
    username = _required_string(name, values, "username")
    password_env = _required_string(name, values, "password_env")
    if not ENV_NAME.fullmatch(password_env):
        raise ConfigError(f"profile {name!r} has invalid password_env")
    environment = _required_string(name, values, "environment")
    if environment not in ENVIRONMENTS:
        raise ConfigError(f"profile {name!r} has invalid environment: {environment}")
    host, port, database, options = _parse_mysql_url(name, url)
    jdbc_options = dict(options)
    tls = values.get("tls")
    if tls is None:
        tls = _jdbc_tls(jdbc_options.get("useSSL"))
    if not isinstance(tls, str) or tls not in TLS_MODES:
        raise ConfigError(f"profile {name!r} has invalid tls mode: {tls}")
    connect_timeout = _timeout(
        name, values, "connect_timeout_seconds", 5, jdbc_options.get("connectTimeout")
    )
    query_timeout = _timeout(
        name, values, "query_timeout_seconds", 30, jdbc_options.get("socketTimeout")
    )
    return Profile(
        name=name,
        url=url,
        username=username,
        password_env=password_env,
        environment=environment,
        tls=tls,
        connect_timeout_seconds=connect_timeout,
        query_timeout_seconds=query_timeout,
        host=host,
        port=port,
        database=database,
        jdbc_options=options,
    )


def _required_string(name: str, values: dict[str, object], field: str) -> str:
    value = values.get(field)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"profile {name!r} requires non-empty {field}")
    return value


def _timeout(
    name: str,
    values: dict[str, object],
    field: str,
    default: int,
    jdbc_milliseconds: str | None,
) -> int:
    if field in values:
        value = values[field]
    elif jdbc_milliseconds is not None:
        value = max(1, (int(jdbc_milliseconds) + 999) // 1000)
    else:
        value = default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 120:
        raise ConfigError(f"profile {name!r} {field} must be between 1 and 120")
    return value


def _jdbc_tls(use_ssl: str | None) -> str:
    if use_ssl is None or use_ssl.lower() == "true":
        return "required"
    return "disabled"


def _parse_mysql_url(
    name: str, url: str
) -> tuple[str, int, str | None, tuple[tuple[str, str], ...]]:
    normalized = url[5:] if url.startswith("jdbc:") else url
    parsed = urlsplit(normalized)
    if parsed.scheme != "mysql":
        raise ConfigError(f"profile {name!r} must use jdbc:mysql:// or mysql://")
    if parsed.username or parsed.password:
        raise ConfigError(f"profile {name!r} URL must not contain credentials")
    if not parsed.hostname:
        raise ConfigError(f"profile {name!r} URL requires a hostname")
    try:
        port = parsed.port or 3306
    except ValueError as exc:
        raise ConfigError(f"profile {name!r} has invalid port") from exc
    database_path = parsed.path.lstrip("/")
    database = unquote(database_path) if database_path else None
    options = tuple(parse_qsl(parsed.query, keep_blank_values=True))
    _validate_jdbc_options(name, options)
    return parsed.hostname, port, database, options


def _validate_jdbc_options(name: str, options: tuple[tuple[str, str], ...]) -> None:
    allowed = {"connectTimeout", "socketTimeout", "useSSL"}
    seen: set[str] = set()
    for key, value in options:
        if key not in allowed:
            raise ConfigError(f"profile {name!r} has unsupported JDBC option: {key}")
        if key in seen:
            raise ConfigError(f"profile {name!r} repeats JDBC option: {key}")
        seen.add(key)
        if key in {"connectTimeout", "socketTimeout"} and (
            not value.isdigit() or int(value) <= 0
        ):
            raise ConfigError(f"profile {name!r} has invalid JDBC option: {key}")
        if key == "useSSL" and value.lower() not in {"true", "false"}:
            raise ConfigError(f"profile {name!r} has invalid JDBC option: useSSL")


def _file_warnings(path: Path) -> list[str]:
    warnings: list[str] = []
    try:
        if path.is_symlink():
            warnings.append(f"config file is a symbolic link: {path}")
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            warnings.append(f"config file permissions are broader than 0600: {path}")
        if path.stat().st_uid != os.getuid():
            warnings.append(f"config file is not owned by the current user: {path}")
    except FileNotFoundError:
        pass
    return warnings
