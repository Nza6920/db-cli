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

    def test_query_is_unavailable_before_driver_access(self):
        result = self.run_cli("query", "--profile", "local", "--sql", "SELECT 1",
                              stub="raise AssertionError('must not import driver')")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"]["code"],
                         "POSTGRESQL_QUERY_UNAVAILABLE")

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
