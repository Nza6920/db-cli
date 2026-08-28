from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from db_query.config import config_path, load_config


class ConfigPathTests(unittest.TestCase):
    def test_uses_db_cli_directory_under_xdg_config_home(self):
        with patch.dict(
            "os.environ",
            {"XDG_CONFIG_HOME": "/tmp/example-config"},
            clear=True,
        ):
            self.assertEqual(
                config_path(),
                Path("/tmp/example-config/db-cli/config.toml"),
            )

    def test_uses_appdata_on_windows(self):
        self.assertEqual(
            config_path(
                environ={"APPDATA": "C:/Users/example/AppData/Roaming"},
                platform="win32",
                home=Path("C:/Users/example"),
            ),
            Path("C:/Users/example/AppData/Roaming/db-cli/config.toml"),
        )

    def test_windows_falls_back_to_roaming_directory_under_home(self):
        self.assertEqual(
            config_path(
                environ={},
                platform="win32",
                home=Path("C:/Users/example"),
            ),
            Path("C:/Users/example/AppData/Roaming/db-cli/config.toml"),
        )

    def test_explicit_and_environment_paths_keep_their_precedence(self):
        environment = {
            "DB_QUERY_CONFIG": "/config/from-db-query.toml",
            "XDG_CONFIG_HOME": "/config/from-xdg",
            "APPDATA": "C:/Users/example/AppData/Roaming",
        }

        self.assertEqual(
            config_path("/config/explicit.toml", environ=environment, platform="win32"),
            Path("/config/explicit.toml"),
        )
        self.assertEqual(
            config_path(environ=environment, platform="win32"),
            Path("/config/from-db-query.toml"),
        )
        del environment["DB_QUERY_CONFIG"]
        self.assertEqual(
            config_path(environ=environment, platform="win32"),
            Path("/config/from-xdg/db-cli/config.toml"),
        )


class ConfigFileWarningTests(unittest.TestCase):
    def test_windows_does_not_run_posix_owner_or_mode_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[profiles.test]
url = "mysql://localhost/example"
username = "reader"
password_env = "DB_PASSWORD"
environment = "test"
""".strip(),
                encoding="utf-8",
            )

            with (
                patch("db_query.config.os.name", "nt"),
                patch(
                    "db_query.config.os.getuid",
                    side_effect=AssertionError("POSIX owner check called on Windows"),
                ),
            ):
                config = load_config(path)

        self.assertEqual(config.warnings, ())


if __name__ == "__main__":
    unittest.main()
