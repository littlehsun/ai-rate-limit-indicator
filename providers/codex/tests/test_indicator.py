import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

try:
    import indicator
    from codex_rate import CodexRateSnapshot, RateWindow
except (ImportError, ValueError) as exc:
    indicator = None
    CodexRateSnapshot = None
    RateWindow = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class FakeIndicator:
    def __init__(self, created_with):
        self.created_with = created_with
        self.icon_updates = []
        self.status = None
        self.menu = None

    def set_status(self, status):
        self.status = status

    def set_menu(self, menu):
        self.menu = menu

    def set_icon_full(self, icon_name, description):
        self.icon_updates.append((icon_name, description))


class FakeIndicatorFactory:
    created = []

    @classmethod
    def new(cls, app_id, icon_name, category):
        obj = FakeIndicator(("new", app_id, icon_name, category))
        cls.created.append(obj)
        return obj

    @classmethod
    def new_with_path(cls, app_id, icon_name, category, icon_theme_path):
        obj = FakeIndicator(("new_with_path", app_id, icon_name, category, icon_theme_path))
        cls.created.append(obj)
        return obj


class FakeAppIndicator3:
    Indicator = FakeIndicatorFactory
    IndicatorCategory = type("IndicatorCategory", (), {"APPLICATION_STATUS": "application-status"})
    IndicatorStatus = type("IndicatorStatus", (), {"ACTIVE": "active"})


class FakeMenu:
    def __init__(self):
        self.items = []

    def append(self, item):
        self.items.append(item)

    def show_all(self):
        pass


class FakeMenuItem:
    def __init__(self, label=None):
        self.label = label
        self.sensitive = True
        self.connections = []
        self.visible = True

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive

    def set_label(self, label):
        self.label = label

    def connect(self, signal_name, callback):
        self.connections.append((signal_name, callback))

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


class FakeGtk:
    Menu = FakeMenu
    MenuItem = FakeMenuItem
    SeparatorMenuItem = FakeMenuItem

    @staticmethod
    def main_quit():
        pass


class FakeGLib:
    callbacks = []

    @classmethod
    def timeout_add(cls, delay_ms, callback):
        cls.callbacks.append((delay_ms, callback))
        return 1

    @classmethod
    def timeout_add_seconds(cls, delay_seconds, callback):
        cls.callbacks.append((delay_seconds * 1000, callback))
        return 1


@unittest.skipIf(indicator is None, f"indicator import unavailable: {IMPORT_ERROR}")
class IndicatorTests(unittest.TestCase):
    def setUp(self):
        FakeIndicatorFactory.created = []
        FakeGLib.callbacks = []
        self.patches = [
            patch.object(indicator, "AppIndicator3", FakeAppIndicator3),
            patch.object(indicator, "Gtk", FakeGtk),
            patch.object(indicator, "GLib", FakeGLib),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_uses_user_scoped_icon_names_not_tmp_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
                app = indicator.CodexRateIndicator()

                icon_dir = Path(tmp) / "codex-rate-indicator"
                created = FakeIndicatorFactory.created[0]
                self.assertEqual(created.created_with[0], "new_with_path")
                self.assertEqual(created.created_with[2], "codex-rate-0")
                self.assertEqual(created.created_with[4], str(icon_dir))
                self.assertTrue((icon_dir / "codex-rate-0.svg").exists())

                app._set_icon("Codex 1", "green")

                self.assertEqual(created.icon_updates[-1][0], "codex-rate-1")
                self.assertFalse(created.icon_updates[-1][0].startswith("/tmp/"))

    def test_svg_text_colors_metrics_independently_and_keeps_separator_white(self):
        svg = indicator.make_icon_svg(
            [
                ("18%", "green"),
                ("|", indicator.NEUTRAL_TEXT_COLOR),
                ("89%", "yellow"),
                ("  ⟳2h8m", indicator.NEUTRAL_TEXT_COLOR),
            ],
            "yellow",
        )

        self.assertIn('<image x="1" y="1" width="20" height="20"', svg)
        self.assertIn('href="data:image/png;base64,', svg)
        self.assertIn('<tspan fill="#00AF50">18%</tspan>', svg)
        self.assertIn('<tspan fill="#FFFFFF">|</tspan>', svg)
        self.assertIn('<tspan fill="#E6C800">89%</tspan>', svg)
        self.assertIn('xml:space="preserve"', svg)
        self.assertIn('<tspan fill="#FFFFFF">  ⟳2h8m</tspan>', svg)

    def test_update_writes_split_metric_colors_from_each_window(self):
        snapshot = CodexRateSnapshot.from_rate_limits(
            "2026-05-05T00:02:00Z",
            {
                "primary": {"used_percent": 18, "window_minutes": 300, "resets_at": 1777929435},
                "secondary": {"used_percent": 89, "window_minutes": 10080, "resets_at": 1778480096},
            },
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {"XDG_RUNTIME_DIR": tmp, "CODEX_RATE_SHOW_5H": "true"},
            ):
                app = indicator.CodexRateIndicator()
                with patch.object(indicator, "find_latest_snapshot", return_value=snapshot):
                    with patch.object(indicator.time, "time", return_value=1777911600):
                        app.update()

                svg = (Path(tmp) / "codex-rate-indicator" / "codex-rate-1.svg").read_text(encoding="utf-8")

        self.assertIn('<image x="1" y="1" width="20" height="20"', svg)
        self.assertIn('href="data:image/png;base64,', svg)
        self.assertIn('<tspan fill="#00AF50">18%</tspan>', svg)
        self.assertIn('<tspan fill="#FFFFFF">|</tspan>', svg)
        self.assertIn('<tspan fill="#E6C800">89%</tspan>', svg)
        self.assertIn('xml:space="preserve"', svg)
        self.assertIn('<tspan fill="#FFFFFF">  ⟳4h57m</tspan>', svg)
        self.assertFalse(app.item_reset_separator.visible)

    def test_update_can_show_only_weekly_window(self):
        snapshot = CodexRateSnapshot.from_rate_limits(
            "2026-05-05T00:02:00Z",
            {
                "primary": {"used_percent": 18, "window_minutes": 300, "resets_at": 1777915200},
                "secondary": {"used_percent": 67, "window_minutes": 10080, "resets_at": 1778516400},
            },
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {"XDG_RUNTIME_DIR": tmp, "CODEX_RATE_SHOW_5H": "false"},
            ):
                app = indicator.CodexRateIndicator()
                with patch.object(indicator, "find_latest_snapshot", return_value=snapshot):
                    with patch.object(indicator.time, "time", return_value=1777911600):
                        app.update()

                svg = (Path(tmp) / "codex-rate-indicator" / "codex-rate-1.svg").read_text(
                    encoding="utf-8"
                )

        self.assertIn('<tspan fill="#00AF50">67%</tspan>', svg)
        self.assertNotIn(">18%</tspan>", svg)
        self.assertNotIn(">|</tspan>", svg)
        self.assertIn('<tspan fill="#FFFFFF">  ⟳7d0h</tspan>', svg)
        self.assertFalse(app.item_5h.visible)
        self.assertTrue(app.item_weekly.label.startswith("📅 7D:"))

    def test_wham_source_shows_reset_credits_without_source_or_account_details(self):
        snapshot = CodexRateSnapshot(
            updated_at="2026-07-01T00:00:00Z",
            five_hour=RateWindow(23, 300, 1782892800),
            weekly=RateWindow(90, 10080, 1783411200),
            plan_type=None,
            source_kind="wham",
            account_id="acct_123",
            reset_credits_available=3,
            reset_credit_expirations=(
                "2026-07-02T00:00:00Z",
                "2026-07-03T00:00:00Z",
                "2026-07-04T00:00:00Z",
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp, "CODEX_RATE_SOURCE": "wham"}):
                app = indicator.CodexRateIndicator()
                with patch.object(indicator, "read_wham_snapshot", return_value=snapshot):
                    with patch.object(indicator.time, "time", return_value=1782889200):
                        app.update()

                svg = (Path(tmp) / "codex-rate-indicator" / "codex-rate-1.svg").read_text(encoding="utf-8")

        self.assertIn('<tspan fill="#FFFFFF"> R3</tspan>', svg)
        self.assertTrue(app.item_reset_separator.visible)
        self.assertEqual(app.item_reset_credits.label, "Reset credits: R3")
        self.assertEqual(app.credit_items[0].label, "1. expires 2026-07-02 08:00")
        labels = [item.label for item in app.menu.items if item.label]
        self.assertNotIn("Source", labels)
        self.assertFalse(any(label.startswith("Source:") for label in labels))
        self.assertFalse(any(label.startswith("Account:") for label in labels))

    def test_menu_omits_manual_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
                app = indicator.CodexRateIndicator()

                labels = [item.label for item in app.menu.items if item.label]

        self.assertNotIn("Refresh", labels)
        self.assertIn("Quit", labels)

    def test_icon_write_failure_uses_fallback_icon_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
                with patch.object(indicator.Path, "write_text", side_effect=OSError("no space")):
                    with redirect_stderr(io.StringIO()):
                        app = indicator.CodexRateIndicator()
                        created = FakeIndicatorFactory.created[0]
                        self.assertEqual(created.created_with[2], indicator.FALLBACK_ICON_NAME)

                        app._set_icon("Codex 1", "green")

                self.assertEqual(created.icon_updates[-1][0], indicator.FALLBACK_ICON_NAME)

    def test_poll_keeps_running_when_update_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
                app = indicator.CodexRateIndicator()

                with patch.object(indicator, "find_latest_snapshot", side_effect=RuntimeError("bad data")):
                    with redirect_stderr(io.StringIO()):
                        keep_polling = app._poll()

        self.assertTrue(keep_polling)


if __name__ == "__main__":
    unittest.main()
