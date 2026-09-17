import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path

from usage_float import (
    DEFAULT_OPACITY,
    STATUS_BADGE,
    DEFAULT_WIDTH,
    MAX_OPACITY,
    MIN_OPACITY,
    MIN_WIDTH,
    FloatSettings,
    apply_state,
    autostart_entry,
    autostart_installed,
    build_css,
    build_parser,
    build_rows,
    build_window_row,
    clear_pid,
    float_is_running,
    header_text,
    install_autostart,
    is_current,
    load_float_settings,
    FloatingWidget,
    monitor_namespace,
    read_state,
    remove_autostart,
    resolve_float_settings,
    resolve_source,
    running_pid,
    stop_float,
    state_from,
    status_text,
    strings_for,
    tone_for,
    write_pid,
    write_state,
)
from usage_monitor import (
    DANGER_PERCENT,
    DEFAULT_ENDPOINT,
    STALE_AFTER_SECONDS,
    THEME_FIELDS,
    WARNING_PERCENT,
    ProviderSnapshot,
    Snapshot,
    UsageWindow,
    load_themes,
    resolve_settings as resolve_monitor_settings,
)


def window(**overrides):
    values = {"id": "7d", "label": "7D", "used_percent": 10, "resets_at": None}
    values.update(overrides)
    return UsageWindow(**values)


def provider(name="claude", **overrides):
    values = {
        "provider": name,
        "label": name.title(),
        "updated_at": None,
        "windows": (window(),),
        "status": "fresh",
        "error": None,
        "extras": (),
    }
    values.update(overrides)
    return ProviderSnapshot(**values)


def snapshot(*providers, fetched_at=1_000_000.0, reachable=True):
    return Snapshot(providers=providers, fetched_at=fetched_at, reachable=reachable)


class ToneTests(unittest.TestCase):
    def test_an_unreported_window_is_empty_rather_than_green(self):
        self.assertEqual(tone_for(None), "empty")

    def test_the_thresholds_are_the_monitor_s_own(self):
        self.assertEqual(tone_for(WARNING_PERCENT - 1), "good")
        self.assertEqual(tone_for(WARNING_PERCENT), "warning")
        self.assertEqual(tone_for(DANGER_PERCENT - 1), "warning")
        self.assertEqual(tone_for(DANGER_PERCENT), "danger")


class RowTests(unittest.TestCase):
    def setUp(self):
        self.text = strings_for("en")

    def test_an_unreported_window_gets_a_dash_and_an_empty_bar(self):
        row = build_window_row(
            window(used_percent=None), now=0.0, text=self.text
        )
        self.assertEqual(row.percent_text, "--")
        self.assertEqual(row.fraction, 0.0)
        self.assertEqual(row.tone, "empty")

    def test_a_percentage_over_a_hundred_does_not_overrun_the_bar(self):
        row = build_window_row(window(used_percent=140), now=0.0, text=self.text)
        self.assertEqual(row.fraction, 1.0)

    def test_a_reset_time_becomes_a_countdown(self):
        row = build_window_row(
            window(resets_at=3_600 + 100), now=100.0, text=self.text
        )
        self.assertIn("1 h", row.reset_text)

    def test_a_window_without_a_reset_time_says_so(self):
        row = build_window_row(window(resets_at=None), now=0.0, text=self.text)
        self.assertEqual(row.reset_text, self.text["no_reset"])

    def test_compact_mode_keeps_only_the_lead_window(self):
        claude = provider(
            windows=(
                window(id="5h", label="5H", used_percent=20),
                window(id="7d", label="7D", used_percent=40),
            )
        )
        rows = build_rows(
            snapshot(claude), ("claude",), now=0.0, text=self.text, compact=True
        )
        self.assertEqual(len(rows[0].windows), 1)
        # ordered_windows leads with the weekly window for Claude, and that is
        # the one bar that is comparable across all four providers.
        self.assertEqual(rows[0].windows[0].label, "7D")

    def test_full_mode_keeps_every_window_and_the_extras(self):
        codex = provider(
            "codex",
            windows=(window(id="5h", label="5H"), window(id="7d", label="7D")),
            extras=("credits expire in 3 days",),
        )
        rows = build_rows(
            snapshot(codex), ("codex",), now=0.0, text=self.text, compact=False
        )
        self.assertEqual(len(rows[0].windows), 2)
        self.assertEqual(rows[0].extras, ("credits expire in 3 days",))

    def test_compact_mode_drops_the_extras(self):
        codex = provider("codex", extras=("credits expire in 3 days",))
        rows = build_rows(
            snapshot(codex), ("codex",), now=0.0, text=self.text, compact=True
        )
        self.assertEqual(rows[0].extras, ())

    def test_a_provider_the_publisher_does_not_carry_says_so(self):
        rows = build_rows(
            snapshot(), ("grok",), now=0.0, text=self.text, compact=False
        )
        self.assertEqual(rows[0].windows, ())
        self.assertEqual(rows[0].extras, (self.text["missing"],))
        self.assertEqual(rows[0].badge, STATUS_BADGE)

    def test_a_stale_provider_is_marked_with_a_badge_not_a_word(self):
        rows = build_rows(
            snapshot(provider(status="stale")),
            ("claude",),
            now=0.0,
            text=self.text,
            compact=False,
        )
        self.assertFalse(rows[0].fresh)
        self.assertEqual(rows[0].badge, STATUS_BADGE)
        # The word is kept for the tooltip; it is the screen it stays off.
        self.assertEqual(rows[0].status, "stale")

    def test_a_fresh_provider_earns_no_mark_at_all(self):
        rows = build_rows(
            snapshot(provider(status="fresh")),
            ("claude",),
            now=0.0,
            text=self.text,
            compact=False,
        )
        self.assertEqual(rows[0].badge, "")


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.text = strings_for("en")

    def test_an_unreachable_publisher_is_reported_with_the_cache_age(self):
        message = status_text(
            snapshot(provider(), reachable=False),
            now=1_000_000.0 + 7_200,
            text=self.text,
        )
        self.assertIn("unreachable", message)
        self.assertIn("2 h", message)

    def test_an_old_snapshot_is_reported_separately_from_an_offline_one(self):
        taken = "2026-01-01T00:00:00Z"
        message = status_text(
            snapshot(provider(updated_at=taken)),
            now=1_767_225_600 + STALE_AFTER_SECONDS + 60,
            text=self.text,
        )
        self.assertIsNotNone(message)
        self.assertNotIn("unreachable", message)
        self.assertIn("old", message)

    def test_a_fresh_reachable_snapshot_warns_about_nothing(self):
        taken = "2026-01-01T00:00:00Z"
        self.assertIsNone(
            status_text(
                snapshot(provider(updated_at=taken)),
                now=1_767_225_600 + 60,
                text=self.text,
            )
        )

    def test_a_snapshot_nobody_has_stamped_still_gets_a_header(self):
        self.assertEqual(
            header_text(snapshot(provider()), text=self.text), self.text["never"]
        )


class CssTests(unittest.TestCase):
    def test_every_palette_renders_without_a_system_colour_leaking_in(self):
        for name, theme in load_themes().items():
            with self.subTest(theme=name):
                css = build_css(theme)
                for field in THEME_FIELDS:
                    colour = getattr(theme, field)
                    # background is the only one that appears as rgba, so it
                    # is matched on its channels rather than on its hex.
                    if field == "background":
                        red = int(colour.lstrip("#")[0:2], 16)
                        self.assertIn(f"rgba({red}, ", css)
                    else:
                        self.assertIn(colour, css, f"{name} drops {field}")

    def test_the_font_scale_reaches_the_stylesheet(self):
        theme = load_themes()["dracula"]
        self.assertIn("font-size: 12px", build_css(theme, scale=1.0))
        self.assertIn("font-size: 18px", build_css(theme, scale=1.5))


class SettingsTests(unittest.TestCase):
    def test_a_config_without_a_float_section_gets_the_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text("[monitor]\ntheme = nord\n", encoding="utf-8")
            self.assertEqual(load_float_settings(path), FloatSettings())

    def test_a_missing_config_gets_the_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                load_float_settings(Path(directory) / "absent.ini"), FloatSettings()
            )

    def test_the_float_section_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text(
                "[float]\ncompact = true\nopacity = 0.5\nscale = 1.25\n"
                "on_top = false\nwidth = 400\n",
                encoding="utf-8",
            )
            settings = load_float_settings(path)
            self.assertTrue(settings.compact)
            self.assertEqual(settings.opacity, 0.5)
            self.assertEqual(settings.scale, 1.25)
            self.assertFalse(settings.on_top)
            self.assertEqual(settings.width, 400)

    def test_an_absurd_opacity_is_clamped_rather_than_obeyed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text("[float]\nopacity = 0.0\nwidth = 10\n", encoding="utf-8")
            settings = load_float_settings(path)
            # An invisible widget cannot be right-clicked to be made visible
            # again, so the floor is not negotiable.
            self.assertEqual(settings.opacity, MIN_OPACITY)
            self.assertEqual(settings.width, MIN_WIDTH)

    def test_flags_win_over_the_config_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text("[float]\ncompact = false\nopacity = 0.9\n", encoding="utf-8")
            args = build_parser().parse_args(
                ["--config", str(path), "--compact", "--opacity", "0.5", "--no-on-top"]
            )
            settings = resolve_float_settings(args)
            self.assertTrue(settings.compact)
            self.assertEqual(settings.opacity, 0.5)
            self.assertFalse(settings.on_top)

    def test_the_saved_session_wins_over_the_config_but_not_the_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text("[float]\nopacity = 0.9\nscale = 1.0\n", encoding="utf-8")
            args = build_parser().parse_args(["--config", str(path), "--scale", "1.5"])
            settings = resolve_float_settings(
                args, state={"opacity": 0.5, "scale": 0.8, "x": 40, "y": 60}
            )
            self.assertEqual(settings.opacity, 0.5)
            self.assertEqual(settings.scale, 1.5)
            self.assertEqual((settings.x, settings.y), (40, 60))

    def test_the_monitor_owns_the_shared_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text(
                "[monitor]\ntheme = nord\nlanguage = en\ninterval = 30\n"
                "[providers]\ncodex = false\n",
                encoding="utf-8",
            )
            args = build_parser().parse_args(["--config", str(path)])
            settings = resolve_monitor_settings(monitor_namespace(args))
            self.assertEqual(settings.theme, "nord")
            self.assertEqual(settings.language, "en")
            self.assertEqual(settings.interval, 30)
            self.assertNotIn("codex", settings.providers)


class StateTests(unittest.TestCase):
    def test_a_saved_session_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.json"
            original = FloatSettings(compact=True, opacity=0.5, scale=1.25, x=10, y=20)
            write_state(state_from(original), path)
            self.assertEqual(apply_state(FloatSettings(), read_state(path)), original)

    def test_a_truncated_state_file_costs_the_position_not_the_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(read_state(path), {})
            self.assertEqual(apply_state(FloatSettings(), read_state(path)), FloatSettings())

    def test_a_state_file_holding_the_wrong_types_is_ignored_field_by_field(self):
        state = {"compact": "yes", "opacity": None, "x": "40", "y": 60}
        settings = apply_state(FloatSettings(), state)
        self.assertFalse(settings.compact)
        self.assertEqual(settings.opacity, DEFAULT_OPACITY)
        self.assertIsNone(settings.x)
        self.assertEqual(settings.y, 60)

    def test_a_state_file_holding_a_list_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.json"
            path.write_text(json.dumps([1, 2]), encoding="utf-8")
            self.assertEqual(read_state(path), {})

    def test_a_saved_opacity_is_clamped_on_the_way_back_in(self):
        self.assertEqual(
            apply_state(FloatSettings(), {"opacity": 12.0}).opacity, MAX_OPACITY
        )
        self.assertEqual(
            apply_state(FloatSettings(), {"opacity": -1.0}).opacity, MIN_OPACITY
        )

    def test_the_width_stays_configured_rather_than_saved(self):
        # The window is not resizable, so a width in the state file would be
        # a number nothing can change from the widget itself.
        self.assertNotIn("width", state_from(FloatSettings(width=DEFAULT_WIDTH)))


class AutostartTests(unittest.TestCase):
    def test_the_entry_launches_this_script(self):
        entry = autostart_entry("/usr/bin/python3 /opt/usage_float.py")
        self.assertIn("Exec=/usr/bin/python3 /opt/usage_float.py", entry)
        self.assertIn("Type=Application", entry)

    def test_installing_and_removing_are_reported_honestly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autostart" / "rate-limit-float.desktop"
            self.assertFalse(autostart_installed(path))
            install_autostart(path, "python3 float.py")
            self.assertTrue(autostart_installed(path))
            self.assertTrue(remove_autostart(path))
            self.assertFalse(remove_autostart(path))


class SourceTests(unittest.TestCase):
    def test_the_local_snapshot_beats_asking_a_publisher_for_it(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_file = Path(directory) / "snapshots.json"
            snapshot_file.write_text("{}", encoding="utf-8")
            self.assertEqual(
                resolve_source(DEFAULT_ENDPOINT, snapshot_path=snapshot_file),
                snapshot_file.as_uri(),
            )

    def test_without_a_local_snapshot_the_publisher_is_still_the_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                resolve_source(
                    DEFAULT_ENDPOINT, snapshot_path=Path(directory) / "absent.json"
                ),
                DEFAULT_ENDPOINT,
            )

    def test_an_endpoint_on_the_command_line_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_file = Path(directory) / "snapshots.json"
            snapshot_file.write_text("{}", encoding="utf-8")
            self.assertEqual(
                resolve_source(
                    DEFAULT_ENDPOINT, explicit=True, snapshot_path=snapshot_file
                ),
                DEFAULT_ENDPOINT,
            )

    def test_a_configured_remote_publisher_is_not_second_guessed(self):
        # A widget on a second machine reads the first one's publisher, and a
        # stale snapshots.json of its own must not shadow that.
        with tempfile.TemporaryDirectory() as directory:
            snapshot_file = Path(directory) / "snapshots.json"
            snapshot_file.write_text("{}", encoding="utf-8")
            remote = "http://100.64.0.1:8477/usage.json"
            self.assertEqual(
                resolve_source(remote, snapshot_path=snapshot_file), remote
            )


class PidfileTests(unittest.TestCase):
    def test_our_own_pid_reads_back_as_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.pid"
            write_pid(path)
            self.assertEqual(running_pid(path), os.getpid())
            self.assertTrue(float_is_running(path))

    def test_no_pidfile_is_not_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.pid"
            self.assertIsNone(running_pid(path))
            self.assertFalse(stop_float(path))

    def test_a_pidfile_left_by_a_dead_process_is_not_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.pid"
            # A pid nothing can be running under: the kernel's own ceiling
            # plus one, so this cannot collide with a live process.
            path.write_text("4194305\n", encoding="utf-8")
            self.assertIsNone(running_pid(path))

    def test_a_pidfile_holding_nonsense_is_not_running(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.pid"
            for content in ("", "not a pid", "-1", "0"):
                path.write_text(content, encoding="utf-8")
                self.assertIsNone(running_pid(path), content)

    def test_quitting_removes_our_pidfile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.pid"
            write_pid(path)
            clear_pid(path)
            self.assertFalse(path.exists())

    def test_quitting_leaves_a_successor_s_pidfile_alone(self):
        # Two widgets can overlap for a moment while one is closing. The one
        # going away must not take the new one's claim with it.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "float.pid"
            write_pid(path, pid=os.getpid())
            clear_pid(path, pid=os.getpid() + 1)
            self.assertTrue(path.exists())


class ControlFlagTests(unittest.TestCase):
    def test_the_control_flags_parse(self):
        args = build_parser().parse_args(["--status"])
        self.assertTrue(args.status)
        self.assertTrue(build_parser().parse_args(["--stop"]).stop)
        self.assertTrue(build_parser().parse_args(["--toggle"]).toggle)

    def test_the_scale_menu_offers_the_larger_sizes(self):
        source = inspect.getsource(FloatingWidget._popup_menu)
        self.assertIn("(0.8, 1.0, 1.3, 1.5, 2.0)", source)

    def test_minimising_gives_up_the_utility_hint_and_the_taskbar_one(self):
        # Mutter offers no minimise action for a utility window, so a window
        # that stays one never goes down; and one that goes down while still
        # hidden from the taskbar cannot be clicked back up.
        source = inspect.getsource(FloatingWidget.minimize)
        self.assertLess(
            source.index("WindowTypeHint.NORMAL"), source.index("iconify()")
        )
        self.assertLess(
            source.index("set_skip_taskbar_hint(False)"), source.index("iconify()")
        )

    def test_restoring_takes_both_of_them_back(self):
        source = inspect.getsource(FloatingWidget.present)
        self.assertIn("set_skip_taskbar_hint(True)", source)
        self.assertIn("WindowTypeHint.UTILITY", source)


class MenuTests(unittest.TestCase):
    def test_the_option_in_force_is_matched_through_float_rounding(self):
        self.assertTrue(is_current(0.75, 0.7500000001))
        self.assertFalse(is_current(0.75, 0.9))
        self.assertTrue(is_current("nord", "nord"))


if __name__ == "__main__":
    unittest.main()
