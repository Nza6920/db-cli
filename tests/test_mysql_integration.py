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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.environ.get("DB_QUERY_RUN_MYSQL_INTEGRATION") == "1"


@unittest.skipUnless(RUN_INTEGRATION, "set DB_QUERY_RUN_MYSQL_INTEGRATION=1")
class DisposableMySqlCliTests(unittest.TestCase):
    container_names: list[str]
    tls_port: int
    plaintext_port: int

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("docker") is None:
            raise unittest.SkipTest("docker is not installed")
        cls.container_names = []
        try:
            cls.tls_port = cls._start_container("tls")
            cls.plaintext_port = cls._start_container("plaintext", "--skip-ssl")
        except BaseException:
            cls._remove_containers()
            raise

    @classmethod
    def _start_container(cls, label: str, *server_options: str) -> int:
        container_name = f"db-query-integration-{label}-{uuid.uuid4().hex[:8]}"
        cls.container_names.append(container_name)
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                container_name,
                "--env",
                "MYSQL_ROOT_PASSWORD=integration-password",
                "--env",
                "MYSQL_DATABASE=integration",
                "--publish",
                "127.0.0.1::3306",
                "mysql:8.0",
                "--default-authentication-plugin=mysql_native_password",
                *server_options,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        port_deadline = time.monotonic() + 10
        while time.monotonic() < port_deadline:
            port_output = subprocess.run(
                ["docker", "port", container_name, "3306/tcp"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if ":" in port_output:
                port = int(port_output.rsplit(":", 1)[1])
                break
            time.sleep(0.2)
        else:
            logs = subprocess.run(
                ["docker", "logs", container_name],
                check=False,
                capture_output=True,
                text=True,
            ).stderr.strip()
            raise RuntimeError(f"disposable MySQL {label} exposed no port: {logs}")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    container_name,
                    "mysqladmin",
                    "ping",
                    "--host=127.0.0.1",
                    "--password=integration-password",
                    "--silent",
                ],
                capture_output=True,
                text=True,
            )
            if ready.returncode == 0:
                return port
            time.sleep(1)
        raise RuntimeError(f"disposable MySQL {label} did not become ready within 60 seconds")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._remove_containers()

    @classmethod
    def _remove_containers(cls) -> None:
        for container_name in cls.container_names:
            subprocess.run(
                ["docker", "rm", "--force", container_name],
                check=False,
                capture_output=True,
                text=True,
            )

    def run_validate(self, tls: str, port: int) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            tls,
            port,
            "validate",
            "--profile",
            "local",
            "--connect",
        )

    def run_query(
        self, sql: str, *, query_timeout_seconds: int = 30
    ) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "preferred",
            self.tls_port,
            "query",
            "--profile",
            "local",
            "--sql",
            sql,
            query_timeout_seconds=query_timeout_seconds,
        )

    def run_cli(
        self,
        tls: str,
        port: int,
        *args: str,
        query_timeout_seconds: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[profiles.local]",
                        f'url = "mysql://127.0.0.1:{port}/integration"',
                        'username = "root"',
                        'password_env = "DB_QUERY_INTEGRATION_PASSWORD"',
                        'environment = "test"',
                        f'tls = "{tls}"',
                        f"query_timeout_seconds = {query_timeout_seconds}",
                    ]
                ),
                encoding="utf-8",
            )
            config_path.chmod(0o600)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            env["DB_QUERY_CONFIG"] = str(config_path)
            env["DB_QUERY_INTEGRATION_PASSWORD"] = "integration-password"
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "db_query",
                    *args,
                ],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_all_tls_modes_against_tls_and_plaintext_servers(self):
        preferred_tls = self.run_validate("preferred", self.tls_port)
        self.assertEqual(
            preferred_tls.returncode, 0, preferred_tls.stdout + preferred_tls.stderr
        )
        self.assertTrue(json.loads(preferred_tls.stdout)["tls_active"])

        preferred_plaintext = self.run_validate("preferred", self.plaintext_port)
        self.assertEqual(
            preferred_plaintext.returncode,
            0,
            preferred_plaintext.stdout + preferred_plaintext.stderr,
        )
        self.assertFalse(json.loads(preferred_plaintext.stdout)["tls_active"])

        disabled = self.run_validate("disabled", self.tls_port)
        self.assertEqual(disabled.returncode, 0, disabled.stdout + disabled.stderr)
        self.assertFalse(json.loads(disabled.stdout)["tls_active"])

        required = self.run_validate("required", self.tls_port)
        self.assertEqual(required.returncode, 4, required.stdout + required.stderr)
        self.assertEqual(json.loads(required.stdout)["error"]["code"], "CONNECTION_FAILED")
        self.assertNotIn("integration-password", required.stdout + required.stderr)

    def test_json_query_types_read_only_session_and_socket_timeout(self):
        sql = """
            SELECT
                7 AS integer_value,
                TRUE AS boolean_alias,
                CAST('123456789012.123456789012345678' AS DECIMAL(30,18)) AS decimal_value,
                NULL AS null_value,
                '' AS empty_value,
                CAST('2026-08-15' AS DATE) AS date_value,
                CAST('2026-08-15 09:10:11.123456' AS DATETIME(6)) AS datetime_value,
                CAST('-01:02:03.000004' AS TIME(6)) AS negative_time,
                X'00A5FF' AS binary_value,
                @@SESSION.transaction_read_only AS session_read_only
        """
        result = self.run_query(sql)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
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
                "negative_time": "-01:02:03.000004",
                "binary_value": "0x00a5ff",
                "session_read_only": 1,
            },
        )

        timeout = self.run_query("SELECT SLEEP(2) AS slept", query_timeout_seconds=1)
        self.assertEqual(timeout.returncode, 5, timeout.stdout + timeout.stderr)
        timeout_error = json.loads(timeout.stdout)["error"]
        self.assertEqual(timeout_error["code"], "QUERY_TIMEOUT")
        self.assertEqual(timeout_error["mysql_errno"], 2013)
