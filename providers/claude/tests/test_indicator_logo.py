import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDICATOR = ROOT / 'indicator.py'
INSTALLER = ROOT / 'install.sh'
LOGO = ROOT / 'assets' / 'claude-logo.svg'


class IndicatorLogoTests(unittest.TestCase):
    def test_logo_asset_and_python_source_are_valid(self):
        self.assertTrue(LOGO.is_file())
        logo = LOGO.read_text(encoding='utf-8')
        source = INDICATOR.read_text(encoding='utf-8')
        self.assertIn('<svg', logo)
        self.assertIn('<path fill="#D97757"', logo)
        self.assertIn('CLAUDE_LOGO_MARKUP', source)
        ast.parse(source)

    def test_installer_copies_logo_next_to_indicator(self):
        installer = INSTALLER.read_text(encoding='utf-8')
        self.assertIn('cp "$SCRIPT_DIR/assets/claude-logo.svg"', installer)
        self.assertIn('"$APP_DIR/assets/claude-logo.svg"', installer)
        self.assertIn('nohup "$BIN"', installer)
        self.assertNotIn('python3 "$BIN"', installer)
        self.assertIn('ExecStart=$BIN', installer)
        self.assertIn('systemctl --user enable claude-rate-indicator.service', installer)
        self.assertIn('systemctl --user restart claude-rate-indicator.service', installer)


if __name__ == '__main__':
    unittest.main()
