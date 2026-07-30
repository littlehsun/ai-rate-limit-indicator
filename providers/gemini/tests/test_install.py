import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "ubuntu-indicator" / "install.sh"


class GeminiInstallTests(unittest.TestCase):
    def test_installer_copies_assets_and_uses_recurring_timer(self):
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('cp "$SCRIPT_DIR/assets/gemini-logo.svg"', source)
        self.assertIn("OnActiveSec=30s", source)
        self.assertIn("OnUnitActiveSec=1m", source)
        self.assertIn("gemini-rate-poll.timer", source)


if __name__ == "__main__":
    unittest.main()
