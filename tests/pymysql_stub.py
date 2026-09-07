"""Driver fixture copied into CLI subprocesses; never connects to a database."""

import datetime
from decimal import Decimal
import os
import ssl
from typing import Any


scenario: dict[str, Any] = {}


def configure(settings: dict[str, Any]) -> None:
    scenario.update(settings)
    MySQLError.sqlstate = settings["sqlstate"]
    exec(settings["module_setup"], globals())


def record(event: str) -> None:
    if path := os.environ.get("DB_QUERY_DRIVER_EVENTS"):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(event + "\n")
    if expression := scenario["failures"].get(event):
        raise eval(expression, globals())


class MySQLError(Exception):
    sqlstate = None


class Cursor:
    def __init__(self) -> None:
        self.description: list[tuple[object, ...]] | None = None
        self.last_event = ""

    def __enter__(self):
        record("cursor.enter")
        return self

    def __exit__(self, *args):
        record("cursor.exit")

    def execute(self, sql: str) -> None:
        self.last_event = {
            "SET SESSION TRANSACTION READ ONLY": "set_read_only",
            "SELECT @@SESSION.transaction_read_only": "read_read_only",
            "SHOW SESSION STATUS LIKE 'Ssl_cipher'": "read_tls",
        }.get(sql, "execute")
        record(self.last_event)
        if self.last_event == "execute":
            expected = scenario["expected_sql"]
            if expected is not None and sql != expected:
                raise AssertionError(sql)
            self.description = [
                (name, None, None, None, None, None, None)
                for name in scenario["columns"]
            ]

    def fetchone(self):
        if self.last_event == "read_read_only":
            record("fetch_read_only")
            return (scenario["read_only_value"],)
        record("fetch_tls")
        return ("Ssl_cipher", scenario["tls_cipher"])

    def fetchall(self):
        record("fetch_all")
        return eval(scenario["rows_expression"], globals())


class Connection:
    def __enter__(self):
        record("connection.enter")
        return self

    def __exit__(self, *args):
        record("connection.exit")

    def cursor(self):
        record("cursor")
        return Cursor()


def connect(**kwargs):
    record("connect")
    assertion_scope = globals().copy()
    assertion_scope["kwargs"] = kwargs
    exec(scenario["assertions"], assertion_scope, assertion_scope)
    return Connection()
