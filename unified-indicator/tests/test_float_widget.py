import inspect
import sys
import unittest
import unittest.mock

import float_widget
import indicator


class FloatWidgetLocatorTests(unittest.TestCase):
    def test_the_widget_is_found_beside_the_dashboard_in_the_repository(self):
        directory = float_widget.float_widget_dir()
        self.assertIsNotNone(directory)
        self.assertTrue((directory / "usage_float.py").is_file())

    def test_the_installed_layout_is_looked_for_first(self):
        # install.sh copies the widget next to the tray, and that copy is the
        # one an installed tray must open -- not a checkout that happens to
        # still be on the same disk, two versions behind.
        source = inspect.getsource(float_widget.float_widget_dir)
        self.assertLess(source.index("here,"), source.index('"dashboard"'))

    def test_a_machine_without_the_widget_still_gets_a_tray(self):
        with unittest.mock.patch.object(
            float_widget, "float_widget_dir", return_value=None
        ):
            self.assertFalse(float_widget.float_widget_available())
            self.assertFalse(float_widget.float_is_running())
            self.assertFalse(float_widget.start_float())
            self.assertFalse(float_widget.stop_float())

    def test_the_widget_directory_does_not_shadow_the_tray_s_own_modules(self):
        # Both directories hold a models.py-shaped world. Inserting at the
        # front would hand the tray the dashboard's copy of a shared name.
        source = inspect.getsource(float_widget._module)
        self.assertIn("sys.path.append", source)
        self.assertNotIn("sys.path.insert", source)

    def test_the_tray_and_the_widget_agree_on_where_the_pidfile_is(self):
        directory = float_widget.float_widget_dir()
        sys.path.append(str(directory))
        import usage_float

        self.assertEqual(
            float_widget.float_is_running(), usage_float.float_is_running()
        )


class FloatWidgetMenuTests(unittest.TestCase):
    def test_the_menu_entry_reflects_what_is_actually_running(self):
        # Not what the tray last did: the widget is also started at login,
        # from the dock, and from a terminal, and it can be closed from its
        # own menu. The pidfile is the only thing that knows.
        source = inspect.getsource(indicator.UnifiedRateIndicator._rebuild_menu)
        self.assertIn("float_item.set_active(float_is_running())", source)

    def test_the_entry_is_hidden_when_the_widget_is_not_installed(self):
        source = inspect.getsource(indicator.UnifiedRateIndicator._rebuild_menu)
        self.assertIn("if float_widget_available():", source)

    def test_toggling_starts_and_stops_rather_than_supervising(self):
        source = inspect.getsource(indicator.UnifiedRateIndicator._toggle_float)
        self.assertIn("start_float()", source)
        self.assertIn("stop_float()", source)


if __name__ == "__main__":
    unittest.main()
