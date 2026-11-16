from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = '''[profiles.local]
driver = "postgresql"
url = "postgresql://localhost/example"
username = "reader"
password_env = "PG_TEST_PASSWORD"
environment = "test"
tls = "required"
'''


def query_stub(sql: str, rows: str = "[(1, '50% text')]", columns: str = "['n', 'text']") -> str:
    return f"""
class Error(Exception): pass
class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return Cursor()
class Cursor:
    description = [(name,) for name in {columns}]
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql):
        self.sql = sql
        if not sql.startswith(('SET ', 'SHOW ')):
            assert sql == {sql!r}, repr(sql)
    def fetchone(self): return ('on',)
    def fetchall(self): return {rows}
def connect(**kwargs): return Connection()
"""


class PostgreSqlCliTests(unittest.TestCase):
    def run_cli(self, *args: str, config: str = CONFIG, stub: str | None = None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            config_path = path / "config.toml"
            config_path.write_text(config)
            config_path.chmod(0o600)
            if stub is not None:
                (path / "psycopg.py").write_text(stub)
            return subprocess.run(
                [sys.executable, "-m", "db_query", *args],
                env={**os.environ, "DB_QUERY_CONFIG": str(config_path),
                     "PG_TEST_PASSWORD": "secret-test-password",
                     "PYTHONPATH": os.pathsep.join((directory, str(ROOT / "src")))},
                text=True, capture_output=True,
            )

    def test_select_delivers_original_sql_and_shared_scalars(self):
        sql = "/* original */ SELECT 1 AS n, '50% text' AS text; -- tail"
        for fmt in ("json", "csv", "table"):
            result = self.run_cli("query", "--profile", "local", "--sql", sql,
                                  "--format", fmt, stub=query_stub(sql))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if fmt == "json":
                self.assertEqual(json.loads(result.stdout)["rows"], [{"n": 1, "text": "50% text"}])
            else:
                self.assertIn("50% text", result.stdout)

    def test_postgresql_policy_preserves_literals_identifiers_and_outer_limits(self):
        allowed = [
            "SELECT $$; DROP TABLE t; FOR UPDATE$$", "SELECT $tag$set_config('x')$tag$",
            r"SELECT E'escaped\\text; DELETE'", "SELECT U&'d\0061ta'",
            'SELECT "update", "for", "limit" FROM t LIMIT 3',
            "SELECT 1 /* outer /* DROP */ FOR UPDATE */; -- end",
            'SELECT COUNT(*) AS "total" FROM t', "SELECT SUM(n), MAX(n) total FROM t",
            "SELECT abs(1), current_setting('x')", "((SELECT * FROM t LIMIT 3))",
            "(SELECT 1 UNION SELECT 2) LIMIT 3", "SELECT * FROM (SELECT * FROM t) s LIMIT 3",
            "SELECT * FROM t LIMIT 3 OFFSET 9999", "SELECT 1 # 2",
        ]
        for sql in allowed:
            with self.subTest(sql=sql):
                result = self.run_cli("query", "--profile", "local", "--sql", sql,
                                      stub=query_stub(sql))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_postgresql_rejections_happen_before_driver_import(self):
        rejected = [
            "SELECT * FROM t", "SELECT * FROM t FETCH FIRST 1 ROW ONLY",
            "SELECT * FROM t LIMIT ALL", "SELECT * FROM t LIMIT NULL",
            "SELECT * FROM t LIMIT 1+1", "SELECT * FROM t LIMIT 1,2",
            "SELECT * FROM t LIMIT -1", "SELECT * FROM t LIMIT 1001",
            "SELECT * FROM t LIMIT " + "9" * 5000,
            "SELECT * FROM t LIMIT 1 LIMIT 2", "SELECT * FROM t LIMIT $1",
            "SELECT * FROM t LIMIT 1.0", "SELECT * FROM t LIMIT １",
            "SELECT 1 UNION ALL SELECT 2", "(SELECT * FROM t LIMIT 1) UNION (SELECT 1 LIMIT 1)",
            "SELECT COUNT(*) OVER () FROM t", "SELECT max FROM t", "SELECT SUM(n)+1 FROM t",
            "SELECT COUNT(*), n FROM t", "SELECT COUNT(*) FROM t GROUP BY n",
            "SELECT 1 INTO TEMP t", "SELECT * FROM t FOR UPDATE LIMIT 1",
            "SELECT * FROM t FOR NO KEY UPDATE LIMIT 1", "SELECT * FROM t FOR SHARE LIMIT 1",
            "SELECT * FROM t FOR KEY SHARE LIMIT 1", "SELECT \"set_config\"('x','y',false)",
            "SELECT pg_catalog.\"nextval\"('s')", 'SELECT "pg_try_advisory_lock"(1)',
            r"SELECT U&\"set_\0063onfig\"('x','y',false)",
            r"SELECT U&\"set_!0063onfig\" UESCAPE '!' ('x','y',false)",
            "SELECT pg_notify('x','y')", "SELECT setval('s',1)",
            "/* prefix */ \\dt", "SELECT 1; SELECT 2", "SELECT $$unterminated",
            "SELECT 1 /* outer /* inner */", "SELECT (1", "SELECT `x`",
            "WITH c AS (SELECT 1) SELECT * FROM c LIMIT 1", "SHOW timezone", "EXPLAIN SELECT 1",
        ]
        for sql in rejected:
            with self.subTest(sql=sql):
                result = self.run_cli("query", "--profile", "local", "--sql", sql,
                                      stub="raise AssertionError('must reject before import')")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)["error"]["code"].startswith("SQL_"))

    def test_query_timeout_and_failure_are_structured_and_redacted(self):
        sql = "SELECT pg_sleep(2)"
        for state, code in (("57014", "QUERY_TIMEOUT"), ("42P01", "QUERY_FAILED")):
            stub = query_stub(sql).replace("class Error(Exception): pass",
                    f"class Error(Exception): sqlstate = {state!r}")
            stub = stub.replace("assert sql ==", "raise Error('secret-test-password'); assert sql ==")
            result = self.run_cli("query", "--profile", "local", "--sql", sql, stub=stub)
            self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], code)
            self.assertNotIn("secret-test-password", result.stdout + result.stderr)

    def test_query_setup_failure_prevents_user_sql(self):
        sql = "SELECT 1"
        for setup in ("SET default_transaction_read_only", "SHOW transaction_read_only",
                      "SET standard_conforming_strings", "SHOW standard_conforming_strings",
                      "SET statement_timeout"):
            stub = query_stub(sql).replace("self.sql = sql",
                    f"self.sql = sql\n        if sql.startswith({setup!r}): raise Error('setup failed')")
            result = self.run_cli("query", "--profile", "local", "--sql", sql, stub=stub)
            self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "CONNECTION_FAILED")

    def test_query_shared_scalars_and_unsupported_types(self):
        sql = "SELECT 1"
        values = "[(None, __import__('decimal').Decimal('12.30'), __import__('datetime').datetime(2026,1,2,3,4,5), bytes([0,255]))]"
        result = self.run_cli("query", "--profile", "local", "--sql", sql,
                              stub=query_stub(sql, values, "['n','d','t','b']"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["rows"], [{"n": None, "d": "12.30",
                         "t": "2026-01-02T03:04:05", "b": "0x00ff"}])
        for values, columns in (("[(object(),)]", "['v']"),
                                ("[(1,2)]", "['v','v']")):
            result = self.run_cli("query", "--profile", "local", "--sql", sql,
                                  stub=query_stub(sql, values, columns))
            self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "RESULT_ENCODING_FAILED")

    def test_profiles_reports_explicit_postgresql_driver(self):
        result = self.run_cli("profiles")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile = json.loads(result.stdout)["profiles"][0]
        self.assertEqual((profile["driver"], profile["database"], profile["port"]),
                         ("postgresql", "example", 5432))

    def test_validate_requires_verified_read_only_session(self):
        stub = """
class Error(Exception): pass
class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return self
    def execute(self, sql): pass
    def fetchone(self): return ('off',)
def connect(**kwargs): return Connection()
"""
        result = self.run_cli("validate", "--profile", "local", "--connect", stub=stub)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "CONNECTION_FAILED")


    def test_configuration_rejects_driver_url_and_host_ambiguity(self):
        invalid = [
            CONFIG.replace('driver = "postgresql"', 'driver = "other"'),
            CONFIG.replace('driver = "postgresql"', 'driver = "mysql"'),
            CONFIG.replace('/example"', '/"'),
            CONFIG.replace('localhost/example', 'localhost'),
            CONFIG.replace('localhost/example', 'alice:secret@localhost/example'),
            CONFIG.replace('localhost/example', 'localhost,other/example'),
            CONFIG.replace('localhost/example', '%2Ftmp/example'),
            CONFIG.replace('localhost/example', 'localhost/example?host=other'),
            CONFIG.replace('localhost/example', 'localhost:bad/example'),
            CONFIG.replace('localhost/example', 'localhost/example#fragment'),
            CONFIG.replace('localhost/example', 'localhost/example/other'),
        ]
        for config in invalid:
            with self.subTest(config=config):
                result = self.run_cli("profiles", config=config,
                                      stub="raise AssertionError('must not connect')")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], "CONFIG_ERROR")
                self.assertNotIn("alice:secret", result.stdout)

    def test_mysql_default_retains_optional_database(self):
        config = CONFIG.replace('driver = "postgresql"\n', '').replace(
            'postgresql://localhost/example', 'mysql://localhost/')
        result = self.run_cli("profiles", config=config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile = json.loads(result.stdout)["profiles"][0]
        self.assertEqual((profile["driver"], profile["database"], profile["port"]),
                         ("mysql", None, 3306))

    def test_unsafe_query_is_rejected_before_driver_access(self):
        result = self.run_cli("query", "--profile", "local", "--sql", "SELECT 1; SELECT 2",
                              stub="raise AssertionError('must not import driver')")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"]["code"],
                         "SQL_MULTIPLE_STATEMENTS")

    def test_missing_optional_driver_explains_installation(self):
        result = self.run_cli("validate", "--profile", "local", "--connect",
                              stub="raise ImportError('driver absent')")
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        error = json.loads(result.stdout)["error"]
        self.assertEqual(error["code"], "DRIVER_NOT_INSTALLED")
        self.assertIn("db-query[postgresql]", error["message"])

    def test_production_requires_exact_confirmation_before_connection(self):
        config = CONFIG.replace('environment = "test"', 'environment = "production"')
        for confirmation in ((), ("--confirm-profile", "other")):
            result = self.run_cli("validate", "--profile", "local", "--connect",
                                  *confirmation, config=config,
                                  stub="raise AssertionError('must not import driver')")
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["error"]["code"],
                             "PRODUCTION_CONFIRMATION_REQUIRED")

    def test_driver_failure_is_structured_and_redacted(self):
        stub = """
class Error(Exception):
    sqlstate = '28P01'
def connect(**kwargs):
    raise Error('password secret-test-password was invalid')
"""
        result = self.run_cli("validate", "--profile", "local", "--connect", stub=stub)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        error = json.loads(result.stdout)["error"]
        self.assertEqual((error["code"], error["sqlstate"]), ("CONNECTION_FAILED", "28P01"))
        self.assertNotIn("secret-test-password", result.stdout + result.stderr)

    def test_verified_connection_reports_tls_and_closes_on_success_or_failure(self):
        stub = """
class Error(Exception): pass
class Connection:
    pgconn = type('Info', (), {'ssl_in_use': True})()
    def __enter__(self): return self
    def __exit__(self, *args): print('connection closed', file=__import__('sys').stderr)
    def cursor(self): return Cursor()
class Cursor:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql): pass
    def fetchone(self): return ('on',)
def connect(**kwargs):
    assert kwargs['sslmode'] == 'verify-full'
    assert kwargs['sslrootcert'] == '/example/ca.pem'
    assert kwargs['dbname'] == 'example'
    return Connection()
"""
        config = CONFIG + 'tls_ca_file = "/example/ca.pem"\n'
        result = self.run_cli("validate", "--profile", "local", "--connect", config=config, stub=stub)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["tls_active"])
        self.assertIn("connection closed", result.stderr)
        failed = self.run_cli("validate", "--profile", "local", "--connect", config=config,
                              stub=stub.replace("return ('on',)", "raise Error('setup failed')"))
        self.assertEqual(failed.returncode, 4, failed.stdout + failed.stderr)
        self.assertIn("connection closed", failed.stderr)



if __name__ == "__main__":
    unittest.main()
