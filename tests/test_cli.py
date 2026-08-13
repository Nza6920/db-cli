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


class DbQueryCliTests(unittest.TestCase):
    def run_cli(
        self,
        config: str,
        *args: str,
        extra_env: dict[str, str] | None = None,
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

    def test_query_runs_usql_without_putting_password_or_sql_in_argv(self):
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
            fake_usql="""
                #!/usr/bin/env python3
                import os
                from pathlib import Path
                import sys

                argv = "\\0".join(sys.argv)
                secret = os.environ["DB_QUERY_TEST_PASSWORD"]
                if secret in argv or "SELECT id" in argv:
                    print("secret or SQL leaked into argv", file=sys.stderr)
                    raise SystemExit(91)
                config_path = Path(sys.argv[sys.argv.index("--config") + 1])
                config = config_path.read_text()
                sql_path = Path(sys.argv[sys.argv.index("--file") + 1])
                expected = (
                    'connections:\\n'
                    '  db_query: "mysql://readonly:'
                    's3cret%3A%40%20value@db.example:3307/'
                    '?timeout=5s&readTimeout=30s&writeTimeout=30s&tls=true"\\n'
                )
                if (
                    config_path.stat().st_mode & 0o777 != 0o600
                    or config != expected
                    or "writetimeout" in config
                    or "readtimeout" in config
                    or sql_path.read_text() != "SELECT id, name FROM app.users LIMIT 2;\\n"
                ):
                    print("bad generated config", file=sys.stderr)
                    raise SystemExit(92)
                print('[{"id":1,"name":"alice"},{"id":2,"name":"bob"}]')
            """,
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
            fake_usql="""
                #!/usr/bin/env python3
                from pathlib import Path
                import sys

                config_path = Path(sys.argv[sys.argv.index("--config") + 1])
                config = config_path.read_text()
                expected = "?timeout=9s&readTimeout=12s&writeTimeout=12s&tls=false"
                if expected not in config:
                    print("JDBC options were not applied", file=sys.stderr)
                    raise SystemExit(93)
                print('[{"id":1}]')
            """,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_string_dsn_encodes_database_and_ipv6_authority(self):
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
            fake_usql="""
                #!/usr/bin/env python3
                from pathlib import Path
                import sys

                config_path = Path(sys.argv[sys.argv.index("--config") + 1])
                config = config_path.read_text()
                expected = "mysql://read%20only:p%40ss%2Fword@[2001:db8::1]:3307/report%20data?"
                if expected not in config or "report%2520data" in config:
                    raise SystemExit(94)
                print('[{"id":1}]')
            """,
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
            fake_usql="""
                #!/usr/bin/env python3
                from pathlib import Path
                import sys

                sql_path = Path(sys.argv[sys.argv.index("--file") + 1])
                if sql_path.read_text() != "SELECT 1 AS connection;\\n":
                    raise SystemExit(95)
                print('[{"connection":1}]')
            """,
        )

        self.assertEqual(unconfirmed.returncode, 3)
        self.assertEqual(
            json.loads(unconfirmed.stdout)["error"]["code"],
            "PRODUCTION_CONFIRMATION_REQUIRED",
        )
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertEqual(json.loads(confirmed.stdout)["connected"], True)

    def test_supported_read_only_statements_reach_usql(self):
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
        fake_usql = """
            #!/usr/bin/env python3
            print('[]')
        """

        for sql in statements:
            with self.subTest(sql=sql):
                result = self.run_cli(
                    config,
                    "query",
                    "--profile",
                    "test",
                    "--stdin",
                    extra_env={"DB_QUERY_TEST_PASSWORD": "secret"},
                    fake_usql=fake_usql,
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

    def test_usql_error_is_classified_and_credentials_are_redacted(self):
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
            "SELECT * FROM app.users LIMIT 1",
            extra_env={"DB_QUERY_TEST_PASSWORD": "secret-password"},
            fake_usql="""
                #!/usr/bin/env python3
                import sys
                print("Access denied for readonly-user using secret-password", file=sys.stderr)
                raise SystemExit(1)
            """,
        )

        self.assertEqual(result.returncode, 4)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"]["code"], "CONNECTION_FAILED")
        self.assertNotIn("readonly-user", result.stdout + result.stderr)
        self.assertNotIn("secret-password", result.stdout + result.stderr)

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
            fake_usql="""
                #!/usr/bin/env python3
                import sys
                print("Access denied", file=sys.stderr)
                raise SystemExit(1)
            """,
        )

        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "CONNECTION_FAILED")

    def test_missing_usql_has_stable_exit_code(self):
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
        )

        self.assertEqual(result.returncode, 127)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "USQL_NOT_FOUND")

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
            fake_usql="""
                #!/usr/bin/env python3
                import time
                time.sleep(2)
            """,
        )

        self.assertEqual(result.returncode, 5)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "QUERY_TIMEOUT")

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
