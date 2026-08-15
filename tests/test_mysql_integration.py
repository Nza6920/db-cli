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
    container_name: str
    port: int

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("docker") is None:
            raise unittest.SkipTest("docker is not installed")
        cls.container_name = f"db-query-integration-{uuid.uuid4().hex[:12]}"
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                cls.container_name,
                "--env",
                "MYSQL_ROOT_PASSWORD=integration-password",
                "--env",
                "MYSQL_DATABASE=integration",
                "--publish",
                "127.0.0.1::3306",
                "mysql:8.0",
                "--default-authentication-plugin=mysql_native_password",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            port_output = subprocess.run(
                ["docker", "port", cls.container_name, "3306/tcp"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            cls.port = int(port_output.rsplit(":", 1)[1])
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                ready = subprocess.run(
                    [
                        "docker",
                        "exec",
                        cls.container_name,
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
                    return
                time.sleep(1)
            raise RuntimeError("disposable MySQL did not become ready within 60 seconds")
        except BaseException:
            cls._remove_container()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._remove_container()

    @classmethod
    def _remove_container(cls) -> None:
        subprocess.run(
            ["docker", "rm", "--force", cls.container_name],
            check=False,
            capture_output=True,
            text=True,
        )

    def run_validate(self, tls: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[profiles.local]",
                        f'url = "mysql://127.0.0.1:{self.port}/integration"',
                        'username = "root"',
                        'password_env = "DB_QUERY_INTEGRATION_PASSWORD"',
                        'environment = "test"',
                        f'tls = "{tls}"',
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
                    "validate",
                    "--profile",
                    "local",
                    "--connect",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_preferred_tls_connects_and_required_rejects_untrusted_certificate(self):
        preferred = self.run_validate("preferred")
        self.assertEqual(preferred.returncode, 0, preferred.stdout + preferred.stderr)
        self.assertTrue(json.loads(preferred.stdout)["connected"])

        required = self.run_validate("required")
        self.assertEqual(required.returncode, 4, required.stdout + required.stderr)
        self.assertEqual(json.loads(required.stdout)["error"]["code"], "CONNECTION_FAILED")
        self.assertNotIn("integration-password", required.stdout + required.stderr)
