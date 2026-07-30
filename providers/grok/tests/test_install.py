#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "ubuntu-indicator" / "install.sh"


class InstallScriptTests(unittest.TestCase):
    def test_timer_uses_on_active_sec(self):
        text = INSTALL.read_text(encoding="utf-8")
        self.assertIn("OnActiveSec=30s", text)
        self.assertIn("OnUnitActiveSec=1m", text)
        self.assertNotIn("OnBootSec=", text)
        self.assertIn("grok-rate-poll.timer", text)
        self.assertIn("grok-rate-indicator.service", text)
        self.assertIn("gir1.2-appindicator3-0.1", text)
        self.assertIn('mkdir -p "$APP_DIR/assets"', text)
        self.assertIn(
            'cp "$SCRIPT_DIR/assets/grok-logo.png" "$APP_DIR/assets/grok-logo.png"',
            text,
        )


if __name__ == "__main__":
    unittest.main()
