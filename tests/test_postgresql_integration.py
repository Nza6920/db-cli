"""CLI acceptance against disposable PostgreSQL 16/17/18 with a private CA."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.environ.get("DB_QUERY_RUN_POSTGRESQL_INTEGRATION") == "1"


class PostgreSqlIntegrationMixin:
    version: str
    temporary: tempfile.TemporaryDirectory[str]
    container: str
    port: int

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("docker") is None or shutil.which("openssl") is None:
            raise unittest.SkipTest("docker and openssl are required")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.container = f"db-query-pg-{cls.version}-{uuid.uuid4().hex[:8]}"
        cls.addClassCleanup(cls.temporary.cleanup)  # type: ignore[attr-defined]
        cls.addClassCleanup(cls.remove_container)  # type: ignore[attr-defined]
        directory = Path(cls.temporary.name)
        directory.chmod(0o755)
        def openssl(*args: str) -> None:
            subprocess.run(["openssl", *args], cwd=directory, check=True, capture_output=True)
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                "-subj", "/CN=db-query-test-ca", "-keyout", "ca.key", "-out", "ca.pem")
        openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
                "-keyout", "server.key", "-out", "server.csr")
        (directory / "extensions.cnf").write_text("subjectAltName=DNS:localhost\n")
        openssl("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
                "-CAcreateserial", "-days", "2", "-extfile", "extensions.cnf", "-out", "server.pem")
        # The container copies this disposable key and restricts its permissions before startup.
        (directory / "server.key").chmod(0o644)
        command = (
            "cp /tls/server.key /tmp/server.key && chown postgres:postgres /tmp/server.key "
            "&& chmod 600 /tmp/server.key && exec docker-entrypoint.sh postgres "
            "-c ssl=on -c ssl_cert_file=/tls/server.pem -c ssl_key_file=/tmp/server.key"
        )
        subprocess.run([
            "docker", "run", "--detach", "--rm", "--name", cls.container,
            "--env", "POSTGRES_PASSWORD=integration-password",
            "--env", "POSTGRES_DB=integration", "--publish", "127.0.0.1::5432",
            "--mount", f"type=bind,source={directory},target=/tls,readonly",
            f"postgres:{cls.version}", "bash", "-c", command,
        ], check=True, capture_output=True, text=True)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            ports = subprocess.run(["docker", "port", cls.container, "5432/tcp"],
                                   capture_output=True, text=True)
            ready = subprocess.run(["docker", "exec", cls.container, "pg_isready", "-h", "127.0.0.1"],
                                   capture_output=True, text=True)
            if ports.returncode == 0 and ports.stdout.strip() and ready.returncode == 0:
                cls.port = int(ports.stdout.strip().rsplit(":", 1)[1])
                return
            time.sleep(0.3)
        logs = subprocess.run(["docker", "logs", cls.container], capture_output=True, text=True)
        raise RuntimeError(f"PostgreSQL {cls.version} failed startup: {logs.stdout} {logs.stderr}")

    @classmethod
    def remove_container(cls) -> None:
        subprocess.run(["docker", "rm", "--force", cls.container], capture_output=True)

    def run_validate(self, *, tls: str = "required", host: str = "localhost",
                     ca: str | None = "ca.pem", password: str = "integration-password",
                     query: str | None = None, output: str = "json"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            config = (
                '[profiles.local]\ndriver = "postgresql"\n'
                f'url = "postgresql://{host}:{self.port}/integration"\n'
                'username = "postgres"\npassword_env = "DB_QUERY_PG_TEST_PASSWORD"\n'
                f'environment = "test"\ntls = "{tls}"\nquery_timeout_seconds = 1\n'
            )
            if ca is not None:
                config += f'tls_ca_file = "{Path(self.temporary.name) / ca}"\n'
            path.write_text(config)
            path.chmod(0o600)
            env = {**os.environ, "DB_QUERY_CONFIG": str(path),
                   "DB_QUERY_PG_TEST_PASSWORD": password, "PYTHONPATH": str(ROOT / "src")}
            arguments = ["validate", "--profile", "local", "--connect"] if query is None else [
                "query", "--profile", "local", "--sql", query, "--format", output]
            return subprocess.run([sys.executable, "-m", "db_query", *arguments],
                                  env=env, capture_output=True, text=True, timeout=20)

    def test_select_shared_types_and_output_formats(self):
        sql = """SELECT 42::integer AS n, 'text'::text AS t, 12.30::numeric AS d,
                 TIMESTAMP '2026-01-02 03:04:05' AS dt,
                 decode('00ff','hex') AS b, NULL::text AS empty"""
        for output in ("json", "csv", "table"):
            result = self.run_validate(query=sql, output=output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
            if output == "json":
                self.assertEqual(json.loads(result.stdout)["rows"], [{  # type: ignore[attr-defined]
                    "n": 42, "t": "text", "d": "12.30", "dt": "2026-01-02T03:04:05",
                    "b": "0x00ff", "empty": None}])
            else:
                self.assertIn("0x00ff", result.stdout)  # type: ignore[attr-defined]

    def test_ctes_show_and_nonexecuting_explain(self):
        queries = [
            ("WITH c AS (SELECT n FROM generate_series(1,3) AS n) SELECT * FROM c LIMIT 2", 2),
            ("WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c WHERE n<3) SELECT * FROM c LIMIT 3", 3),
            ("WITH c AS NOT MATERIALIZED (WITH d AS (SELECT 1 AS n) SELECT * FROM d) SELECT COUNT(*) AS total FROM c", 1),
            ("SHOW TIME ZONE", 1),
            ("SHOW transaction_read_only", 1),
            ("EXPLAIN (COSTS OFF, VERBOSE) WITH c AS (SELECT n FROM generate_series(1,3) AS n) SELECT * FROM c", None),
        ]
        for sql, count in queries:
            for output in ("json", "csv", "table"):
                with self.subTest(sql=sql, output=output):  # type: ignore[attr-defined]
                    result = self.run_validate(query=sql, output=output)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
                    if output == "json":
                        data = json.loads(result.stdout)
                        if count is not None:
                            self.assertEqual(data["row_count"], count)  # type: ignore[attr-defined]
                        if sql == "SHOW transaction_read_only":
                            self.assertEqual(data["rows"], [{"transaction_read_only": "on"}])  # type: ignore[attr-defined]
                        if sql.startswith("EXPLAIN"):
                            self.assertIn("Function Scan", str(data["rows"]))  # type: ignore[attr-defined]
                    else:
                        self.assertTrue(result.stdout.strip())  # type: ignore[attr-defined]

    def test_postgresql_lexical_forms_parse_on_server(self):
        sql = r"""SELECT $tag$; DROP TABLE x; FOR UPDATE$tag$ AS "update",
                  E'escaped\\text' AS escaped, U&'d\0061ta' AS unicode,
                  1 /* outer /* nested */ comment */ AS n"""
        result = self.run_validate(query=sql)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
        self.assertEqual(json.loads(result.stdout)["rows"][0]["unicode"], "data")  # type: ignore[attr-defined]

    def test_line_comment_newlines_match_server_and_cannot_hide_policy_tokens(self):
        for newline in ("\r", "\r\n", "\n"):
            # Both a leading comment and arithmetic continuation must reach PostgreSQL
            # unchanged: treating CR as comment text would return 1 instead of 3.
            sql = f"-- ignored DELETE;{newline}SELECT 1 -- ignored{newline}+ 2 AS n"
            with self.subTest(newline=newline):  # type: ignore[attr-defined]
                result = self.run_validate(query=sql)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
                self.assertEqual(json.loads(result.stdout)["rows"], [{"n": 3}])  # type: ignore[attr-defined]
            for suffix, code in (("; SELECT 2", "SQL_MULTIPLE_STATEMENTS"),
                                 ("FOR UPDATE", "SQL_NOT_READ_ONLY"),
                                 ("INTO TEMP hidden", "SQL_NOT_READ_ONLY"),
                                 ("FROM generate_series(1,3)", "SQL_LIMIT_REQUIRED")):
                with self.subTest(newline=newline, suffix=suffix):  # type: ignore[attr-defined]
                    result = self.run_validate(query=f"SELECT 1 -- ignored{newline}{suffix}")
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)  # type: ignore[attr-defined]
                    self.assertEqual(json.loads(result.stdout)["error"]["code"], code)  # type: ignore[attr-defined]

    def test_ordinary_string_functions_execute_without_rewriting(self):
        sql = ("SELECT substring('abcdef' FROM 1 FOR 2) AS part, "
               "overlay('Txxxxas' PLACING 'hom' FROM 2 FOR 4) AS overlaid, "
               "replace('abc','a','z') AS replaced")
        result = self.run_validate(query=sql)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
        self.assertEqual(json.loads(result.stdout)["rows"], [  # type: ignore[attr-defined]
            {"part": "ab", "overlaid": "Thomas", "replaced": "zbc"}])

    def test_server_statement_timeout_is_structured(self):
        result = self.run_validate(query="SELECT pg_sleep(3)")
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)  # type: ignore[attr-defined]
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "QUERY_TIMEOUT")  # type: ignore[attr-defined]

    def test_read_only_session_blocks_write_inside_ordinary_function(self):
        # Fixture setup is administrative; assertions remain at the CLI seam.
        fixture = """CREATE TABLE IF NOT EXISTS fixture_writes (n integer);
        CREATE OR REPLACE FUNCTION fixture_write() RETURNS integer LANGUAGE plpgsql AS $$
        BEGIN INSERT INTO fixture_writes VALUES (1); RETURN 1; END $$;"""
        subprocess.run(["docker", "exec", self.container, "psql", "-U", "postgres",
                        "-d", "integration", "-v", "ON_ERROR_STOP=1", "-c", fixture],
                       check=True, capture_output=True, text=True)
        result = self.run_validate(query="SELECT fixture_write()")
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)  # type: ignore[attr-defined]
        error = json.loads(result.stdout)["error"]
        self.assertEqual((error["code"], error["sqlstate"]),  # type: ignore[attr-defined]
                         ("QUERY_FAILED", "25006"))

    def test_real_table_limit_and_aggregate_exemption(self):
        for sql, count in (("SELECT n FROM generate_series(1,5) AS n LIMIT 2", 2),
                           ("SELECT COUNT(*) AS total FROM generate_series(1,5)", 1)):
            result = self.run_validate(query=sql)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
            self.assertEqual(json.loads(result.stdout)["row_count"], count)  # type: ignore[attr-defined]


    def test_native_postgresql_types_in_json_csv_and_table(self):
        sql = """SELECT true AS yes, false AS no,
            TIMESTAMPTZ '2026-01-02 03:04:05+00' AS dt,
            TIMETZ '03:04:05+05:30' AS t,
            '12345678-1234-5678-1234-567812345678'::uuid AS id,
            '{"nested":[true,null,{"text":"消息"}]}'::json AS obj,
            '{"items":[1,null,[false]]}'::jsonb AS doc,
            ARRAY[[1,NULL],[2,3]] AS matrix,
            ARRAY[12.30,NULL,0.10]::numeric[] AS numbers,
            ARRAY['12345678-1234-5678-1234-567812345678'::uuid,NULL] AS ids,
            ARRAY[TIMETZ '03:04:05+05:30',NULL] AS times,
            NULL::text AS sql_null, 'null'::json AS json_null,
            DATE '2026-01-02' AS day, TIME '03:04:05.123456' AS naive_time,
            12.30::numeric AS precise, decode('00ff','hex') AS binary"""
        expected = {
            'yes': True, 'no': False, 'dt': '2026-01-02T03:04:05+00:00',
            't': '03:04:05+05:30', 'id': '12345678-1234-5678-1234-567812345678',
            'obj': {'nested': [True, None, {'text': '消息'}]},
            'doc': {'items': [1, None, [False]]}, 'matrix': [[1, None], [2, 3]],
            'numbers': ['12.30', None, '0.10'],
            'ids': ['12345678-1234-5678-1234-567812345678', None],
            'times': ['03:04:05+05:30', None], 'sql_null': None, 'json_null': None,
            'day': '2026-01-02', 'naive_time': '03:04:05.123456',
            'precise': '12.30', 'binary': '0x00ff'}
        for output in ('json', 'csv', 'table'):
            with self.subTest(output=output):  # type: ignore[attr-defined]
                result = self.run_validate(query=sql, output=output)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
                if output == 'json':
                    row = json.loads(result.stdout)['rows'][0]
                    self.assertIs(row['yes'], True)  # type: ignore[attr-defined]
                    self.assertIs(row['no'], False)  # type: ignore[attr-defined]
                    self.assertIs(row['obj']['nested'][0], True)  # type: ignore[attr-defined]
                    self.assertIs(row['doc']['items'][2][0], False)  # type: ignore[attr-defined]
                    self.assertEqual(row, expected)  # type: ignore[attr-defined]
                elif output == 'csv':
                    row = next(csv.DictReader(io.StringIO(result.stdout)))
                    self.assertEqual(row['obj'], '{"nested":[true,null,{"text":"消息"}]}')  # type: ignore[attr-defined]
                    self.assertEqual(row['numbers'], '["12.30",null,"0.10"]')  # type: ignore[attr-defined]
                    self.assertEqual(row['json_null'], r'\N')  # type: ignore[attr-defined]
                    self.assertEqual(row['t'], '03:04:05+05:30')  # type: ignore[attr-defined]
                else:
                    self.assertIn('{"items":[1,null,[false]]}', result.stdout)  # type: ignore[attr-defined]
                    self.assertIn('[[1,null],[2,3]]', result.stdout)  # type: ignore[attr-defined]
                    self.assertIn('03:04:05+05:30', result.stdout)  # type: ignore[attr-defined]

    def test_explain_json_plan_remains_structured_or_compact_text(self):
        for output in ('json', 'csv', 'table'):
            result = self.run_validate(query='EXPLAIN (FORMAT JSON) SELECT 1', output=output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
            if output == 'json':
                plan = json.loads(result.stdout)['rows'][0]['QUERY PLAN']
                self.assertEqual(plan[0]['Plan']['Node Type'], 'Result')  # type: ignore[attr-defined]
            elif output == 'csv':
                row = next(csv.DictReader(io.StringIO(result.stdout)))
                self.assertIn('"Node Type":"Result"', row['QUERY PLAN'])  # type: ignore[attr-defined]
            else:
                self.assertIn('"Node Type":"Result"', result.stdout)  # type: ignore[attr-defined]

    def test_postgresql_empty_and_duplicate_type_results(self):
        for output in ('json', 'csv', 'table'):
            result = self.run_validate(query="SELECT true AS flag, '{}'::json AS obj WHERE false",
                                       output=output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
            if output == 'json':
                self.assertEqual(json.loads(result.stdout)['rows'], [])  # type: ignore[attr-defined]
            result = self.run_validate(query='SELECT true AS repeated, false AS repeated',
                                       output=output)
            self.assertEqual(result.returncode, 5, result.stdout + result.stderr)  # type: ignore[attr-defined]
            self.assertEqual(json.loads(result.stdout)['error']['code'], 'RESULT_ENCODING_FAILED')  # type: ignore[attr-defined]

    def test_trusted_ca_and_hostname_establish_verified_read_only_session(self):
        result = self.run_validate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
        self.assertTrue(json.loads(result.stdout)["tls_active"])  # type: ignore[attr-defined]

    def test_untrusted_ca_and_hostname_mismatch_fail(self):
        for options in ({"ca": "server.pem"}, {"host": "127.0.0.1"}, {"ca": "absent.pem"}):
            with self.subTest(options=options):  # type: ignore[attr-defined]
                result = self.run_validate(**options)
                self.assertEqual(result.returncode, 4, result.stdout + result.stderr)  # type: ignore[attr-defined]
                self.assertEqual(json.loads(result.stdout)["error"]["code"], "CONNECTION_FAILED")  # type: ignore[attr-defined]

    def test_authentication_failure_is_structured_and_redacted(self):
        result = self.run_validate(password="invalid-password-for-test")
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)  # type: ignore[attr-defined]
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "CONNECTION_FAILED")  # type: ignore[attr-defined]
        self.assertNotIn("invalid-password-for-test", result.stdout + result.stderr)  # type: ignore[attr-defined]

    def test_preferred_and_disabled_report_actual_tls(self):
        for tls, active in (("preferred", True), ("disabled", False)):
            result = self.run_validate(tls=tls, ca=None)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)  # type: ignore[attr-defined]
            self.assertEqual(json.loads(result.stdout)["tls_active"], active)  # type: ignore[attr-defined]


@unittest.skipUnless(RUN_INTEGRATION, "set DB_QUERY_RUN_POSTGRESQL_INTEGRATION=1")
class PostgreSql16CliTests(PostgreSqlIntegrationMixin, unittest.TestCase):
    version = "16"


@unittest.skipUnless(RUN_INTEGRATION, "set DB_QUERY_RUN_POSTGRESQL_INTEGRATION=1")
class PostgreSql17CliTests(PostgreSqlIntegrationMixin, unittest.TestCase):
    version = "17"


@unittest.skipUnless(RUN_INTEGRATION, "set DB_QUERY_RUN_POSTGRESQL_INTEGRATION=1")
class PostgreSql18CliTests(PostgreSqlIntegrationMixin, unittest.TestCase):
    version = "18"
