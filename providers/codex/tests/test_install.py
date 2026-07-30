import unittest
from pathlib import Path


class InstallScriptTests(unittest.TestCase):
    def test_wham_timer_starts_after_user_manager_restart(self):
        install_script = (
            Path(__file__).resolve().parents[1] / "ubuntu-indicator" / "install.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("OnActiveSec=30s", install_script)
        self.assertIn("OnUnitActiveSec=1m", install_script)
        self.assertNotIn("OnUnitActiveSec=5m", install_script)
        self.assertNotIn("OnBootSec=30s", install_script)
        self.assertNotIn("OnStartupSec=30s", install_script)
        self.assertNotIn("Persistent=true", install_script)


if __name__ == "__main__":
    unittest.main()
