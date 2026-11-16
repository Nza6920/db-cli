"""Select a backend without importing optional database dependencies eagerly."""
from __future__ import annotations

from db_query.config import Profile
from db_query.mysql_runner import ConnectionResult, QueryResult


def validate_connection(profile: Profile, password: str) -> ConnectionResult:
    if profile.driver == "postgresql":
        from db_query.postgresql_runner import validate_connection as validate
    else:
        from db_query.mysql_runner import validate_connection as validate
    return validate(profile, password)


def run_query(profile: Profile, password: str, sql: str) -> QueryResult:
    if profile.driver == "postgresql":
        from db_query.postgresql_runner import run_query as run
    else:
        from db_query.mysql_runner import run_query as run
    return run(profile, password, sql)
