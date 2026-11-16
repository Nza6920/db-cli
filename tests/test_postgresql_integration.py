"""CLI acceptance against disposable PostgreSQL 16/17/18 with a private CA."""
from __future__ import annotations

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
                     ca: str | None = "ca.pem", password: str = "integration-password"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            config = (
                '[profiles.local]\ndriver = "postgresql"\n'
                f'url = "postgresql://{host}:{self.port}/integration"\n'
                'username = "postgres"\npassword_env = "DB_QUERY_PG_TEST_PASSWORD"\n'
                f'environment = "test"\ntls = "{tls}"\n'
            )
            if ca is not None:
                config += f'tls_ca_file = "{Path(self.temporary.name) / ca}"\n'
            path.write_text(config)
            path.chmod(0o600)
            env = {**os.environ, "DB_QUERY_CONFIG": str(path),
                   "DB_QUERY_PG_TEST_PASSWORD": password, "PYTHONPATH": str(ROOT / "src")}
            return subprocess.run([sys.executable, "-m", "db_query", "validate", "--profile",
                                   "local", "--connect"], env=env, capture_output=True, text=True)

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
