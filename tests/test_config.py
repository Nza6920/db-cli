from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from db_query.config import config_path


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


if __name__ == "__main__":
    unittest.main()
