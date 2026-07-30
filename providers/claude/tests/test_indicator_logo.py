import ast
import importlib.util
import sys
import types
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

    def test_svg_colors_each_metric_and_keeps_neutral_text_white(self):
        fake_gi = types.ModuleType('gi')
        fake_gi.require_version = lambda *_: None
        fake_repository = types.ModuleType('gi.repository')
        fake_repository.AppIndicator3 = object()
        fake_repository.Gtk = object()
        fake_repository.GLib = object()
        old_modules = {
            name: sys.modules.get(name)
            for name in ('gi', 'gi.repository')
        }
        sys.modules['gi'] = fake_gi
        sys.modules['gi.repository'] = fake_repository
        try:
            spec = importlib.util.spec_from_file_location('claude_indicator_test', INDICATOR)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
        finally:
            for name, old_module in old_modules.items():
                if old_module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old_module

        svg = module.make_icon_svg(
            [
                ('18%', 'green'),
                ('|', module.NEUTRAL_TEXT_COLOR),
                ('89%', 'yellow'),
                ('  ⟳2h8m', module.NEUTRAL_TEXT_COLOR),
            ],
            'yellow',
        )
        self.assertIn('<tspan fill="#00AF50">18%</tspan>', svg)
        self.assertIn('<tspan fill="#FFFFFF">|</tspan>', svg)
        self.assertIn('<tspan fill="#E6C800">89%</tspan>', svg)
        self.assertIn('<tspan fill="#FFFFFF">  ⟳2h8m</tspan>', svg)
        self.assertIn('xml:space="preserve"', svg)


if __name__ == '__main__':
    unittest.main()
