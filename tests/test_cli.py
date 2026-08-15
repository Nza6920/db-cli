from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def fake_pymysql_module(
    *,
    assertions: str = "",
    read_only_value: int = 1,
    tls_cipher: str = "TLS_FAKE_CIPHER",
    error_args: tuple[object, ...] | None = None,
    sqlstate: str | None = None,
    module_setup: str = "",
) -> str:
    setup = textwrap.dedent(module_setup).strip()
    checks = textwrap.indent(textwrap.dedent(assertions).strip(), "    ")
    failure = ""
    if error_args is not None:
        failure = textwrap.indent(
            f"exc = MySQLError(*{error_args!r})\n"
            f"exc.sqlstate = {sqlstate!r}\n"
            "raise exc",
            "    ",
        )
    connect_body = "\n".join(part for part in (failure, checks, "    return Connection()") if part)
    return f"""
import ssl

class MySQLError(Exception):
    sqlstate = None

class Cursor:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        expected = [
            "SET SESSION TRANSACTION READ ONLY",
            "SELECT @@SESSION.transaction_read_only",
        ]
        if {read_only_value} == 1:
            expected.append("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        if self.statements != expected:
            raise AssertionError(self.statements)

    def execute(self, sql):
        self.statements.append(sql)

    def fetchone(self):
        if self.statements[-1] == "SELECT @@SESSION.transaction_read_only":
            return ({read_only_value},)
        return ("Ssl_cipher", {tls_cipher!r})

class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return Cursor()

{setup}

def connect(**kwargs):
{connect_body}
"""


def fake_pymysql_query_module(
    *,
    columns: tuple[str, ...] = ("id",),
    rows_expression: str = "[(1,)]",
    assertions: str = "",
    expected_sql: str | None = None,
    error_args: tuple[object, ...] | None = None,
    sqlstate: str | None = None,
) -> str:
    checks = textwrap.indent(textwrap.dedent(assertions).strip(), "    ")
    failure = ""
    if error_args is not None:
        failure = textwrap.indent(
            f"exc = MySQLError(*{error_args!r})\n"
            f"exc.sqlstate = {sqlstate!r}\n"
            "raise exc",
            "            ",
        )
    return f"""
import datetime
from decimal import Decimal

class MySQLError(Exception):
    sqlstate = None

class Cursor:
    def __init__(self):
        self.statements = []
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, sql):
        self.statements.append(sql)
        if sql not in (
            "SET SESSION TRANSACTION READ ONLY",
            "SELECT @@SESSION.transaction_read_only",
        ):
            if sql != {expected_sql!r} and {expected_sql is not None!r}:
                raise AssertionError(sql)
{failure or '            pass'}
            self.description = [(name, None, None, None, None, None, None) for name in {columns!r}]

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return {rows_expression}

class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return Cursor()

def connect(**kwargs):
{checks or '    pass'}
    return Connection()
"""


class DbQueryCliTests(unittest.TestCase):
    def run_cli(
        self,
        config: str,
        *args: str,
        extra_env: dict[str, str] | None = None,
        fake_pymysql: str | None = None,
        fake_usql: str | None = None,
        stdin: str | None = None,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(textwrap.dedent(config), encoding="utf-8")
            config_path.chmod(0o600)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            env["DB_QUERY_CONFIG"] = str(config_path)
            if extra_env:
                env.update(extra_env)
            if fake_pymysql is not None:
                pymysql_dir = Path(temp_dir) / "pymysql"
                pymysql_dir.mkdir()
                (pymysql_dir / "__init__.py").write_text(
                    textwrap.dedent(fake_pymysql).lstrip(),
                    encoding="utf-8",
                )
                env["PYTHONPATH"] = f"{temp_dir}{os.pathsep}{env['PYTHONPATH']}"
            if fake_usql is not None:
                bin_dir = Path(temp_dir) / "bin"
                bin_dir.mkdir()
                usql_path = bin_dir / "usql"
                usql_path.write_text(textwrap.dedent(fake_usql).lstrip(), encoding="utf-8")
                usql_path.chmod(0o700)
                env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
            return subprocess.run(
                [sys.executable, "-m", "db_query", *args],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                input=stdin,
                capture_output=True,
                check=False,
            )

    def test_profiles_reports_safe_connection_metadata(self):
        result = self.run_cli(
            """
            [profiles.prod]
            url = "jdbc:mysql://jms.newchiwan.cn:33061/"
            username = "secret-user"
            password_env = "DB_QUERY_PROD_PASSWORD"
            environment = "production"
            tls = "required"
            """,
            "profiles",
            extra_env={"DB_QUERY_PROD_PASSWORD": "secret-password"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload,
            {
                "profiles": [
                    {
                        "name": "prod",
                        "environment": "production",
                        "host": "jms.newchiwan.cn",
                        "port": 33061,
                        "database": None,
                        "tls": "required",
                        "password_env": "DB_QUERY_PROD_PASSWORD",
                        "password_available": True,
                    }
                ]
            },
        )
        self.assertNotIn("secret-user", result.stdout + result.stderr)
        self.assertNotIn("secret-password", result.stdout + result.stderr)

    def test_validate_rejects_plaintext_password_without_echoing_it(self):
        result = self.run_cli(
            """
            [profiles.prod]
            url = "jdbc:mysql://db.example:3306/"
            username = "readonly"
            password = "must-not-leak"
            password_env = "DB_QUERY_PROD_PASSWORD"
            environment = "production"
            tls = "required"
            """,
            "validate",
        )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "CONFIG_ERROR")
        self.assertNotIn("must-not-leak", result.stdout + result.stderr)

    def test_validate_reports_missing_password_environment_as_exit_three(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://db.example/"
            username = "readonly"
            password_env = "DB_QUERY_DEFINITELY_MISSING_PASSWORD"
            environment = "test"
            """,
            "validate",
        )

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "PASSWORD_ENV_MISSING")
        self.assertEqual(payload["error"]["missing"], ["DB_QUERY_DEFINITELY_MISSING_PASSWORD"])

    def test_query_rejects_writes_multiple_statements_and_unbounded_detail_reads(self):
        config = """
            [profiles.test]
            url = "jdbc:mysql://db.example:3306/"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            tls = "required"
        """
        cases = {
            "UPDATE app.users SET enabled = 0": "SQL_NOT_READ_ONLY",
            "SELECT * FROM app.users LIMIT 1; SELECT 2": "SQL_MULTIPLE_STATEMENTS",
            "SELECT * FROM app.users": "SQL_LIMIT_REQUIRED",
            "SELECT * FROM app.users LIMIT 1001": "SQL_LIMIT_EXCEEDED",
            "SELECT * FROM app.users INTO OUTFILE '/tmp/users' LIMIT 1": "SQL_DANGEROUS_CLAUSE",
            "SELECT * FROM app.users LIMIT 1 FOR UPDATE": "SQL_DANGEROUS_CLAUSE",
            "SELECT * FROM app.users LIMIT 1 FOR SHARE": "SQL_DANGEROUS_CLAUSE",
            "SELECT * FROM app.users INTO @captured LIMIT 1": "SQL_DANGEROUS_CLAUSE",
            "SELECT * FROM app.users /*!50000 INTO OUTFILE '/tmp/users' */ LIMIT 1": "SQL_DANGEROUS_CLAUSE",
            "SELECT GET_LOCK('agent-lock', 10) LIMIT 1": "SQL_DANGEROUS_CLAUSE",
            "\\dt": "SQL_META_COMMAND",
        }

        for sql, expected_code in cases.items():
            with self.subTest(sql=sql):
                result = self.run_cli(
                    config,
                    "query",
                    "--profile",
                    "test",
                    "--sql",
                    sql,
                    extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], expected_code)

    def test_production_query_requires_exact_profile_confirmation(self):
        result = self.run_cli(
            """
            [profiles.prod]
            url = "jdbc:mysql://db.example:3306/"
            username = "readonly"
            password_env = "DB_QUERY_PROD_PASSWORD"
            environment = "production"
            tls = "disabled"
            """,
            "query",
            "--profile",
            "prod",
            "--sql",
            "SELECT * FROM app.users LIMIT 1",
            extra_env={"DB_QUERY_PROD_PASSWORD": "secret"},
        )

        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "PRODUCTION_CONFIRMATION_REQUIRED")

    def test_json_query_normalizes_representative_mysql_values(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "mysql://db.example/reporting"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            tls = "preferred"
            """,
            "query",
            "--profile",
            "test",
            "--sql",
            "SELECT 1 AS id",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
            fake_pymysql=fake_pymysql_query_module(
                columns=(
                    "integer_value",
                    "boolean_alias",
                    "decimal_value",
                    "null_value",
                    "empty_value",
                    "date_value",
                    "datetime_value",
                    "positive_time",
                    "negative_time",
                    "binary_value",
                ),
                rows_expression="""[(
                    7,
                    1,
                    Decimal("123456789012.123456789012345678"),
                    None,
                    "",
                    datetime.date(2026, 8, 15),
                    datetime.datetime(2026, 8, 15, 9, 10, 11, 123456),
                    datetime.timedelta(hours=12, minutes=34, seconds=56, microseconds=123456),
                    -datetime.timedelta(hours=1, minutes=2, seconds=3, microseconds=4),
                    b"\\x00\\xa5\\xff",
                )]""",
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["profile"], "test")
        self.assertEqual(payload["environment"], "test")
        self.assertEqual(payload["row_count"], 1)
        self.assertEqual(
            payload["rows"][0],
            {
                "integer_value": 7,
                "boolean_alias": 1,
                "decimal_value": "123456789012.123456789012345678",
                "null_value": None,
                "empty_value": "",
                "date_value": "2026-08-15",
                "datetime_value": "2026-08-15T09:10:11.123456",
                "positive_time": "12:34:56.123456",
                "negative_time": "-01:02:03.000004",
                "binary_value": "0x00a5ff",
            },
        )

    def test_json_query_rejects_duplicate_column_names(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "mysql://db.example/reporting"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            """,
            "query",
            "--profile",
            "test",
            "--sql",
            "SELECT 1 AS duplicated, 2 AS duplicated",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
            fake_pymysql=fake_pymysql_query_module(
                columns=("duplicated", "duplicated"),
                rows_expression="[(1, 2)]",
            ),
        )

        self.assertEqual(result.returncode, 5)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "RESULT_ENCODING_FAILED")
        self.assertIn("unique SQL aliases", payload["error"]["message"])

    def test_json_query_reports_execution_and_encoding_failures_without_sql(self):
        config = """
            [profiles.test]
            url = "mysql://db.example/reporting"
            username = "readonly-user"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
        """
        sql = "SELECT * FROM secrets WHERE token = 'original-sql-secret' LIMIT 1"
        query_failure = self.run_cli(
            config,
            "query",
            "--profile",
            "test",
            "--sql",
            sql,
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret-password"},
            fake_pymysql=fake_pymysql_query_module(
                error_args=(1064, f"syntax error near {sql} for secret-password"),
                sqlstate="42000",
            ),
        )
        encoding_failure = self.run_cli(
            config,
            "query",
            "--profile",
            "test",
            "--sql",
            sql,
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret-password"},
            fake_pymysql=fake_pymysql_query_module(rows_expression="[(object(),)]"),
        )

        self.assertEqual(query_failure.returncode, 5)
        query_error = json.loads(query_failure.stdout)["error"]
        self.assertEqual(query_error["code"], "QUERY_FAILED")
        self.assertEqual(query_error["mysql_errno"], 1064)
        self.assertEqual(query_error["sqlstate"], "42000")
        self.assertEqual(encoding_failure.returncode, 5)
        self.assertEqual(
            json.loads(encoding_failure.stdout)["error"]["code"],
            "RESULT_ENCODING_FAILED",
        )
        combined = (
            query_failure.stdout
            + query_failure.stderr
            + encoding_failure.stdout
            + encoding_failure.stderr
        )
        self.assertNotIn(sql, combined)
        self.assertNotIn("secret-password", combined)
        self.assertNotIn("readonly-user", combined)
        self.assertNotIn("mysql://db.example/reporting", combined)

    def test_json_query_uses_driver_without_leaking_password_or_sql(self):
        password = "s3cret:@ value"
        sql = "SELECT id, name FROM app.users LIMIT 2"
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://db.example:3307/?connectTimeout=9000&socketTimeout=9000&useSSL=true"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            tls = "required"
            connect_timeout_seconds = 5
            query_timeout_seconds = 30
            """,
            "query",
            "--profile",
            "test",
            "--stdin",
            extra_env={"DB_QUERY_TEST_PASSWORD": password},
            stdin=sql,
            fake_pymysql=fake_pymysql_query_module(
                columns=("id", "name"),
                rows_expression='[(1, "alice"), (2, "bob")]',
                expected_sql=sql,
                assertions="""
                    import os
                    expected = {
                        "host": "db.example",
                        "port": 3307,
                        "user": "readonly",
                        "password": os.environ["DB_QUERY_TEST_PASSWORD"],
                        "database": None,
                        "charset": "utf8mb4",
                        "autocommit": True,
                        "local_infile": False,
                        "connect_timeout": 5,
                        "read_timeout": 30,
                        "write_timeout": 30,
                    }
                    context = kwargs.pop("ssl")
                    if kwargs != expected:
                        raise AssertionError(kwargs)
                    if not context.check_hostname or context.verify_mode != 2:
                        raise AssertionError("TLS certificate verification is not required")
                """,
            ),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["row_count"], 2)
        self.assertEqual(payload["columns"], ["id", "name"])
        self.assertEqual(payload["rows"][1], {"id": 2, "name": "bob"})
        self.assertEqual(payload["profile"], "test")
        self.assertNotIn(password, result.stdout + result.stderr)
        self.assertNotIn(sql, result.stdout + result.stderr)

    def test_production_can_explicitly_disable_tls_with_a_warning(self):
        result = self.run_cli(
            """
            [profiles.prod]
            url = "jdbc:mysql://db.example:3306/"
            username = "readonly"
            password_env = "DB_QUERY_PROD_PASSWORD"
            environment = "production"
            tls = "disabled"
            """,
            "validate",
            extra_env={"DB_QUERY_PROD_PASSWORD": "secret"},
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(any("production" in warning and "TLS" in warning for warning in payload["warnings"]))

    def test_jdbc_timeouts_and_tls_apply_when_profile_fields_are_omitted(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://db.example:3306/?connectTimeout=9000&socketTimeout=12000&useSSL=false"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            """,
            "query",
            "--profile",
            "test",
            "--sql",
            "SELECT * FROM app.users LIMIT 1",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
            fake_pymysql=fake_pymysql_query_module(
                assertions="""
                    expected = {
                        "connect_timeout": 9,
                        "read_timeout": 12,
                        "write_timeout": 12,
                        "ssl_disabled": True,
                    }
                    actual = {key: kwargs[key] for key in expected}
                    if actual != expected or "ssl" in kwargs:
                        raise AssertionError(kwargs)
                """
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_string_dsn_maps_database_and_ipv6_authority_to_driver(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://[2001:db8::1]:3307/report%20data"
            username = "read only"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            """,
            "query",
            "--profile",
            "test",
            "--sql",
            "SELECT * FROM `report data`.users LIMIT 1",
            extra_env={"DB_QUERY_TEST_PASSWORD": "p@ss/word"},
            fake_pymysql=fake_pymysql_query_module(
                assertions="""
                    import os
                    expected = {
                        "host": "2001:db8::1",
                        "port": 3307,
                        "user": "read only",
                        "password": os.environ["DB_QUERY_TEST_PASSWORD"],
                        "database": "report data",
                    }
                    actual = {key: kwargs[key] for key in expected}
                    if actual != expected:
                        raise AssertionError(kwargs)
                """
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validate_connect_is_explicit_and_honors_production_confirmation(self):
        config = """
            [profiles.prod]
            url = "jdbc:mysql://db.example:3306/"
            username = "readonly"
            password_env = "DB_QUERY_PROD_PASSWORD"
            environment = "production"
            tls = "required"
        """
        unconfirmed = self.run_cli(
            config,
            "validate",
            "--profile",
            "prod",
            "--connect",
            extra_env={"DB_QUERY_PROD_PASSWORD": "secret"},
        )
        confirmed = self.run_cli(
            config,
            "validate",
            "--profile",
            "prod",
            "--connect",
            "--confirm-profile",
            "prod",
            extra_env={"DB_QUERY_PROD_PASSWORD": "secret"},
            fake_pymysql=fake_pymysql_module(
                assertions="""
                    import os
                    expected = {
                        "host": "db.example",
                        "port": 3306,
                        "user": "readonly",
                        "password": os.environ["DB_QUERY_PROD_PASSWORD"],
                        "database": None,
                        "charset": "utf8mb4",
                        "autocommit": True,
                        "local_infile": False,
                        "connect_timeout": 5,
                        "read_timeout": 30,
                        "write_timeout": 30,
                    }
                    context = kwargs.pop("ssl")
                    if kwargs != expected:
                        raise AssertionError(kwargs)
                    if not isinstance(context, ssl.SSLContext):
                        raise AssertionError(type(context))
                    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                        raise AssertionError("TLS verification is not required")
                """
            ),
        )

        self.assertEqual(unconfirmed.returncode, 3)
        self.assertEqual(
            json.loads(unconfirmed.stdout)["error"]["code"],
            "PRODUCTION_CONFIRMATION_REQUIRED",
        )
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertEqual(json.loads(confirmed.stdout)["connected"], True)
        self.assertEqual(json.loads(confirmed.stdout)["tls_active"], True)

    def test_validate_connect_uses_preferred_tls_and_jdbc_timeouts(self):
        result = self.run_cli(
            """
            [profiles.uat]
            url = "jdbc:mysql://db.example:3307/reporting?connectTimeout=9000&socketTimeout=12000"
            username = "readonly"
            password_env = "DB_QUERY_UAT_PASSWORD"
            environment = "staging"
            tls = "preferred"
            """,
            "validate",
            "--profile",
            "uat",
            "--connect",
            extra_env={"DB_QUERY_UAT_PASSWORD": "secret"},
            fake_pymysql=fake_pymysql_module(
                assertions="""
                    if "ssl" in kwargs or "ssl_disabled" in kwargs:
                        raise AssertionError(kwargs)
                    expected = {
                        "host": "db.example",
                        "port": 3307,
                        "database": "reporting",
                        "connect_timeout": 9,
                        "read_timeout": 12,
                        "write_timeout": 12,
                    }
                    actual = {key: kwargs[key] for key in expected}
                    if actual != expected:
                        raise AssertionError(actual)
                """
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["tls_active"], True)
        self.assertEqual(json.loads(result.stdout)["connected"], True)

    def test_validate_connect_reports_tls_initialization_failure_as_json(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "mysql://db.example/"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            tls = "required"
            """,
            "validate",
            "--profile",
            "test",
            "--connect",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret-password"},
            fake_pymysql=fake_pymysql_module(
                module_setup="""
                    def fail_default_context():
                        raise ssl.SSLError("certificate setup failed for secret-password")

                    ssl.create_default_context = fail_default_context
                """
            ),
        )

        self.assertEqual(result.returncode, 4)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "CONNECTION_FAILED")
        self.assertNotIn("secret-password", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_validate_connect_explicitly_disables_tls(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "mysql://db.example/"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            tls = "disabled"
            """,
            "validate",
            "--profile",
            "test",
            "--connect",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
            fake_pymysql=fake_pymysql_module(
                tls_cipher="",
                assertions="""
                    if kwargs.get("ssl_disabled") is not True or "ssl" in kwargs:
                        raise AssertionError(kwargs)
                """
            ),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["tls_active"], False)

    def test_validate_connect_reports_redacted_mysql_error_details(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "mysql://db.example/"
            username = "readonly-user"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            tls = "preferred"
            """,
            "validate",
            "--profile",
            "test",
            "--connect",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret-password"},
            fake_pymysql=fake_pymysql_module(
                error_args=(
                    1045,
                    "Access denied for readonly-user using secret-password at mysql://db.example/",
                ),
                sqlstate="28000",
            ),
        )

        self.assertEqual(result.returncode, 4)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "CONNECTION_FAILED")
        self.assertEqual(payload["error"]["mysql_errno"], 1045)
        self.assertEqual(payload["error"]["sqlstate"], "28000")
        self.assertNotIn("readonly-user", result.stdout + result.stderr)
        self.assertNotIn("secret-password", result.stdout + result.stderr)
        self.assertNotIn("mysql://db.example/", result.stdout + result.stderr)

    def test_validate_connect_fails_when_session_is_not_read_only(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "mysql://db.example/"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            tls = "preferred"
            """,
            "validate",
            "--profile",
            "test",
            "--connect",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
            fake_pymysql=fake_pymysql_module(read_only_value=0),
        )

        self.assertEqual(result.returncode, 4)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "CONNECTION_FAILED")
        self.assertIn("read-only", payload["error"]["message"])

    def test_supported_read_only_statements_reach_pymysql(self):
        config = """
            [profiles.test]
            url = "mysql://db.example/"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
        """
        statements = (
            "SELECT 1 AS connection",
            "SELECT * FROM app.users LIMIT 20 OFFSET 40",
            "SELECT * FROM app.users LIMIT 40, 20",
            "WITH recent AS (SELECT id FROM app.users LIMIT 5) SELECT * FROM recent LIMIT 5",
            "SELECT COUNT(*) AS total FROM app.users",
            "SHOW TABLES FROM app",
            "DESCRIBE app.users",
            "EXPLAIN SELECT * FROM app.users",
        )
        for sql in statements:
            with self.subTest(sql=sql):
                result = self.run_cli(
                    config,
                    "query",
                    "--profile",
                    "test",
                    "--stdin",
                    extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
                    fake_pymysql=fake_pymysql_query_module(expected_sql=sql),
                    stdin=sql,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_validate_scopes_profile_warnings_to_the_selected_profile(self):
        result = self.run_cli(
            """
            [profiles.prod]
            url = "jdbc:mysql://prod.example/"
            username = "readonly"
            password_env = "DB_QUERY_PROD_PASSWORD"
            environment = "production"
            tls = "disabled"

            [profiles.uat]
            url = "jdbc:mysql://uat.example/"
            username = "readonly"
            password_env = "DB_QUERY_UAT_PASSWORD"
            environment = "staging"
            tls = "required"
            """,
            "validate",
            "--profile",
            "uat",
            extra_env={"DB_QUERY_UAT_PASSWORD": "secret"},
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["warnings"], [])
        self.assertNotIn("production profile", result.stderr)

    def test_unknown_jdbc_option_is_rejected(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://db.example/?serverTimezone=UTC"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            """,
            "validate",
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "CONFIG_ERROR")
        self.assertIn("serverTimezone", result.stdout)

    def test_query_connection_error_is_classified_and_credentials_are_redacted(self):
        sql = "SELECT * FROM app.users WHERE token = 'original-sql-secret' LIMIT 1"
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://db.example/"
            username = "readonly-user"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            """,
            "query",
            "--profile",
            "test",
            "--sql",
            sql,
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret-password"},
            fake_pymysql=fake_pymysql_module(
                error_args=(
                    1045,
                    "Access denied for readonly-user using secret-password at jdbc:mysql://db.example/",
                ),
                sqlstate="28000",
            ),
        )

        self.assertEqual(result.returncode, 4)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "CONNECTION_FAILED")
        self.assertEqual(payload["error"]["mysql_errno"], 1045)
        self.assertEqual(payload["error"]["sqlstate"], "28000")
        self.assertNotIn("readonly-user", result.stdout + result.stderr)
        self.assertNotIn("secret-password", result.stdout + result.stderr)
        self.assertNotIn("jdbc:mysql://db.example/", result.stdout + result.stderr)
        self.assertNotIn(sql, result.stdout + result.stderr)

    def test_empty_password_does_not_corrupt_error_classification(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://db.example/"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            """,
            "query",
            "--profile",
            "test",
            "--sql",
            "SELECT * FROM app.users LIMIT 1",
            extra_env={"DB_QUERY_TEST_PASSWORD": ""},
            fake_pymysql=fake_pymysql_module(error_args=(1045, "Access denied")),
        )

        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "CONNECTION_FAILED")

    def test_json_query_does_not_require_usql_on_path(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://db.example/"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            """,
            "query",
            "--profile",
            "test",
            "--sql",
            "SELECT * FROM app.users LIMIT 1",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret", "PATH": "/path/that/does/not/exist"},
            fake_pymysql=fake_pymysql_query_module(),
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["rows"], [{"id": 1}])

    def test_query_timeout_has_stable_error(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://db.example/"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            query_timeout_seconds = 1
            """,
            "query",
            "--profile",
            "test",
            "--sql",
            "SELECT * FROM app.users LIMIT 1",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
            fake_pymysql=fake_pymysql_query_module(
                error_args=(2013, "Lost connection during query (timed out)"),
                sqlstate="HY000",
            ),
        )

        self.assertEqual(result.returncode, 5)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "QUERY_TIMEOUT")
        self.assertEqual(payload["error"]["mysql_errno"], 2013)
        self.assertEqual(payload["error"]["sqlstate"], "HY000")

    def test_csv_format_is_passed_through(self):
        result = self.run_cli(
            """
            [profiles.test]
            url = "jdbc:mysql://db.example/"
            username = "readonly"
            password_env = "DB_QUERY_TEST_PASSWORD"
            environment = "test"
            """,
            "query",
            "--profile",
            "test",
            "--format",
            "csv",
            "--sql",
            "SELECT * FROM app.users LIMIT 1",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
            fake_usql="""
                #!/usr/bin/env python3
                import sys
                if "--csv" not in sys.argv:
                    raise SystemExit(9)
                print("id,name")
                print("1,alice")
            """,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "id,name\n1,alice\n")


if __name__ == "__main__":
    unittest.main()
