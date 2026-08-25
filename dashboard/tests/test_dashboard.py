import json
import tempfile
import unittest
from pathlib import Path

from usage_monitor import (
    DANGER_PERCENT,
    THEME_FIELDS,
    load_themes,
    WARNING_PERCENT,
    MonitorError,
    Settings,
    Snapshot,
    SnapshotClient,
    TerminalRenderer,
    absent_window,
    load_settings,
    ordered_windows,
    parse_snapshot,
    percent_label,
    snapshot_as_json,
    snapshot_time,
)
from usage_web import SnapshotCache, render_page, resolve_providers


def _payload(**overrides):
    providers = [
        {
            "provider": "codex",
            "label": "Codex",
            "updated_at": "2026-08-24T11:44:40Z",
            "windows": [
                {"id": "7d", "label": "7D", "used_percent": 20, "resets_at": 1788139042}
            ],
            "status": "fresh",
            "error": None,
            "extras": ["Reset credits: --"],
        },
        {
            "provider": "claude",
            "label": "Claude",
            "updated_at": "2026-08-24T11:45:36Z",
            "windows": [
                {"id": "5h", "label": "5H", "used_percent": 32, "resets_at": 1787584200},
                {"id": "7d", "label": "7D", "used_percent": 16, "resets_at": 1788051600},
            ],
            "status": "fresh",
            "error": None,
            "extras": [],
        },
        {
            "provider": "grok",
            "label": "Grok",
            "updated_at": "2026-08-24T11:44:41Z",
            # Grok calls its weekly window `weekly`, not `7d`.
            "windows": [
                {"id": "weekly", "label": "7D", "used_percent": 24, "resets_at": 1787901221}
            ],
            "status": "fresh",
            "error": None,
            "extras": [],
        },
        {
            "provider": "gemini",
            "label": "Gemini",
            "updated_at": "2026-08-24T11:44:42Z",
            "windows": [
                {"id": "7d", "label": "Gemini 7D", "used_percent": 0, "resets_at": 1788178072},
                {"id": "claude-gpt-7d", "label": "Claude/GPT 7D", "used_percent": 10},
            ],
            "status": "fresh",
            "error": None,
            "extras": [],
        },
    ]
    payload = {"providers": providers}
    payload.update(overrides)
    return payload


class ParseTests(unittest.TestCase):
    def test_every_provider_survives_the_round_trip(self):
        snapshot = parse_snapshot(_payload(), fetched_at=1_000.0, reachable=True)

        self.assertEqual(
            [provider.provider for provider in snapshot.providers],
            ["codex", "claude", "grok", "gemini"],
        )
        self.assertEqual(snapshot.provider("codex").extras, ("Reset credits: --",))
        self.assertTrue(snapshot.reachable)

    def test_a_payload_without_providers_is_refused(self):
        with self.assertRaises(MonitorError):
            parse_snapshot({}, fetched_at=1_000.0, reachable=True)

    def test_a_window_without_a_percentage_is_kept_as_unreported(self):
        snapshot = parse_snapshot(
            {
                "providers": [
                    {
                        "provider": "gemini",
                        "windows": [{"id": "5h", "label": "5H", "used_percent": None}],
                    }
                ]
            },
            fetched_at=1_000.0,
            reachable=True,
        )

        window = snapshot.provider("gemini").windows[0]
        self.assertIsNone(window.used_percent)
        self.assertEqual(percent_label(window), "--")

    def test_a_boolean_is_not_a_percentage(self):
        # json.loads happily produces True here, and `isinstance(True, int)` is
        # the classic way a bar ends up 1% full.
        snapshot = parse_snapshot(
            {"providers": [{"provider": "codex", "windows": [{"id": "7d", "used_percent": True}]}]},
            fetched_at=1_000.0,
            reachable=True,
        )

        self.assertIsNone(snapshot.provider("codex").windows[0].used_percent)

    def test_the_snapshot_time_is_the_newest_provider_update(self):
        snapshot = parse_snapshot(_payload(), fetched_at=1_000.0, reachable=True)

        # 11:45:36 is Claude's, the latest of the four.
        self.assertEqual(snapshot_time(snapshot), 1787571936.0)


class WindowOrderTests(unittest.TestCase):
    def test_claude_leads_with_the_weekly_window(self):
        snapshot = parse_snapshot(_payload(), fetched_at=1_000.0, reachable=True)

        # Every lead is a weekly window so four bars of the same width stay
        # comparable, which is why 7D comes before 5H here.
        self.assertEqual(
            [window.id for window in ordered_windows(snapshot.provider("claude"))],
            ["7d", "5h"],
        )

    def test_grok_falls_back_to_what_it_actually_reported(self):
        snapshot = parse_snapshot(_payload(), fetched_at=1_000.0, reachable=True)

        # `weekly` is not in the lead map, and inventing two absent slots for a
        # provider that reported a perfectly good window would blank the card.
        self.assertEqual(
            [window.id for window in ordered_windows(snapshot.provider("grok"))],
            ["weekly"],
        )

    def test_a_dropped_window_keeps_its_slot(self):
        snapshot = parse_snapshot(_payload(), fetched_at=1_000.0, reachable=True)

        windows = ordered_windows(snapshot.provider("gemini"))

        # Antigravity dropped the 5H bucket. Leaving it out would move every
        # row after it, so the slot stays and reports nothing.
        self.assertEqual([window.id for window in windows], ["7d", "5h", "claude-gpt-7d"])
        self.assertIsNone(windows[1].used_percent)
        self.assertEqual(percent_label(windows[1]), "--")

    def test_an_absent_window_is_labelled_by_its_cadence(self):
        self.assertEqual(absent_window("claude-gpt-5h").label, "5H")
        self.assertEqual(absent_window("7d").label, "7D")


class SettingsTests(unittest.TestCase):
    def test_provider_switches_are_read_in_display_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[providers]\ngemini = false\ncodex = true\nclaude = false\ngrok = true\n",
                encoding="utf-8",
            )

            settings = load_settings(path)

        # The file lists gemini first; the screen order is what comes back.
        self.assertEqual(settings.providers, ("codex", "grok"))

    def test_a_config_without_a_providers_section_shows_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text("[monitor]\ntheme = nord\n", encoding="utf-8")

            settings = load_settings(path)

        self.assertEqual(settings.providers, ("codex", "claude", "grok", "gemini"))
        self.assertEqual(settings.theme, "nord")

    def test_a_missing_config_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings(Path(tmp) / "absent.ini")

        self.assertEqual(settings, Settings())


class ThemeLoadingTests(unittest.TestCase):
    """themes.ini is the only entry point, so everything comes through it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _theme(self, name, **overrides):
        values = {key: "#101010" for key in THEME_FIELDS}
        values.update(overrides)
        body = "[theme]\n" + "\n".join(f"{k} = {v}" for k, v in values.items())
        (self.root / f"{name}.ini").write_text(body + "\n", encoding="utf-8")

    def _index(self, *entries):
        path = self.root / "themes.ini"
        path.write_text(
            "[themes]\ninclude =\n" + "".join(f"    {e}\n" for e in entries),
            encoding="utf-8",
        )
        return path

    def test_the_shipped_index_loads_ten_themes(self):
        themes = load_themes()

        self.assertEqual(len(themes), 10)
        self.assertIn("dracula", themes)
        # Light palettes are in the set too; the web view takes its background
        # from the theme, so a light theme means a light page.
        self.assertIn("solarized-light", themes)
        for name, theme in themes.items():
            for field in THEME_FIELDS:
                self.assertRegex(getattr(theme, field), r"^#[0-9A-Fa-f]{6}$", f"{name}.{field}")

    def test_a_theme_is_named_by_its_file(self):
        self._theme("seafoam")

        self.assertEqual(list(load_themes(self._index("seafoam.ini"))), ["seafoam"])

    def test_a_later_entry_overrides_an_earlier_name(self):
        self._theme("base", good="#111111")
        (self.root / "mine.ini").write_text(
            "[theme]\nname = base\n"
            + "\n".join(f"{k} = #222222" for k in THEME_FIELDS)
            + "\n",
            encoding="utf-8",
        )

        themes = load_themes(self._index("base.ini", "mine.ini"))

        # Overriding a shipped theme must not require editing it.
        self.assertEqual(list(themes), ["base"])
        self.assertEqual(themes["base"].good, "#222222")

    def test_an_untouched_template_is_skipped_rather_than_refused(self):
        self._theme("real")
        blank = "[theme]\n" + "\n".join(f"{k} =" for k in THEME_FIELDS)
        (self.root / "custom.ini").write_text(blank + "\n", encoding="utf-8")

        # custom.ini ships listed and empty; that is a template, not an error.
        self.assertEqual(list(load_themes(self._index("real.ini", "custom.ini"))), ["real"])

    def test_a_half_filled_theme_names_what_is_missing(self):
        self._theme("partial")
        path = self.root / "partial.ini"
        path.write_text(
            path.read_text(encoding="utf-8")
            .replace("warning = #101010", "warning =")
            .replace("border = #101010", "border ="),
            encoding="utf-8",
        )

        with self.assertRaises(MonitorError) as caught:
            load_themes(self._index("partial.ini"))

        self.assertIn("warning", str(caught.exception))
        self.assertIn("border", str(caught.exception))

    def test_a_value_that_is_not_a_colour_is_refused(self):
        self._theme("wrong", good="green")

        with self.assertRaises(MonitorError) as caught:
            load_themes(self._index("wrong.ini"))

        self.assertIn("good=green", str(caught.exception))

    def test_a_missing_theme_file_is_reported_by_name(self):
        with self.assertRaises(MonitorError) as caught:
            load_themes(self._index("absent.ini"))

        self.assertIn("absent.ini", str(caught.exception))

    def test_an_index_with_nothing_usable_is_an_error(self):
        with self.assertRaises(MonitorError):
            load_themes(self._index())

    def test_an_unknown_theme_name_lists_what_is_available(self):
        self._theme("only")

        with self.assertRaises(ValueError) as caught:
            TerminalRenderer(
                color=False, clear=False, theme="nope",
                themes=load_themes(self._index("only.ini")),
            )

        self.assertIn("only", str(caught.exception))


class SnapshotClientTests(unittest.TestCase):
    """Losing the publisher must not blank the screen."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name) / "snapshot.json"

    def test_a_successful_fetch_is_cached(self):
        client = SnapshotClient(
            "http://example/usage.json",
            cache_path=self.cache,
            opener=lambda endpoint, timeout: _payload(),
        )

        snapshot = client.fetch(now=1_000.0)

        self.assertTrue(snapshot.reachable)
        cached = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(cached["fetched_at"], 1_000.0)
        self.assertEqual(len(cached["payload"]["providers"]), 4)

    def test_an_unreachable_publisher_falls_back_to_the_cache(self):
        self.cache.write_text(
            json.dumps({"fetched_at": 500.0, "payload": _payload()}), encoding="utf-8"
        )

        def refuse(endpoint, timeout):
            raise OSError("connection refused")

        client = SnapshotClient(
            "http://example/usage.json",
            cache_path=self.cache,
            opener=refuse,
            attempts=2,
            sleep=lambda seconds: None,
        )

        snapshot = client.fetch(now=1_000.0)

        # The numbers are still the best answer available; what changes is that
        # their age is now visible.
        self.assertFalse(snapshot.reachable)
        self.assertEqual(snapshot.fetched_at, 500.0)
        self.assertEqual(len(snapshot.providers), 4)

    def test_no_publisher_and_no_cache_is_an_error(self):
        client = SnapshotClient(
            "http://example/usage.json",
            cache_path=self.cache,
            opener=_refuse,
            attempts=1,
        )

        with self.assertRaises(MonitorError):
            client.fetch(now=1_000.0)

    def test_every_attempt_is_spent_before_giving_up(self):
        calls = []

        def count(endpoint, timeout):
            calls.append(endpoint)
            raise OSError("refused")

        client = SnapshotClient(
            "http://example/usage.json",
            cache_path=self.cache,
            opener=count,
            attempts=3,
            sleep=lambda seconds: None,
        )

        with self.assertRaises(MonitorError):
            client.fetch(now=1_000.0)
        self.assertEqual(len(calls), 3)

    def test_a_recovered_publisher_replaces_the_stale_cache(self):
        self.cache.write_text(
            json.dumps({"fetched_at": 500.0, "payload": {"providers": []}}), encoding="utf-8"
        )
        client = SnapshotClient(
            "http://example/usage.json",
            cache_path=self.cache,
            opener=lambda endpoint, timeout: _payload(),
        )

        snapshot = client.fetch(now=2_000.0)

        self.assertTrue(snapshot.reachable)
        self.assertEqual(len(snapshot.providers), 4)


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.renderer = TerminalRenderer(color=False, clear=False, language="en")
        self.snapshot = parse_snapshot(_payload(), fetched_at=1_000.0, reachable=True)

    def test_an_unreported_window_says_so_instead_of_showing_zero(self):
        rendered = self.renderer.render(
            self.snapshot, ("gemini",), now=1787571936.0
        )

        self.assertIn("not reported", rendered)
        # An empty track, not a zero-length fill in the usual green.
        self.assertNotIn("█", rendered.split("5H")[1].split("\n")[0])

    def test_an_offline_snapshot_is_called_out_with_its_age(self):
        offline = Snapshot(
            providers=self.snapshot.providers, fetched_at=1787571936.0, reachable=False
        )

        rendered = self.renderer.render(offline, ("codex",), now=1787571936.0 + 3600)

        self.assertIn("unreachable", rendered)

    def test_a_stale_snapshot_is_distinguished_from_an_offline_one(self):
        rendered = self.renderer.render(
            self.snapshot, ("codex",), now=1787571936.0 + 3600
        )

        # An old snapshot and an unreachable publisher are different problems:
        # this one reached the publisher and got numbers nobody had refreshed.
        self.assertIn("Snapshot is", rendered)
        self.assertIn("old", rendered)
        self.assertNotIn("unreachable", rendered)

    def test_a_provider_the_publisher_never_sent_is_named(self):
        rendered = self.renderer.render(self.snapshot, ("codex",), now=1787571936.0)
        self.assertNotIn("no data", rendered)

        empty = Snapshot(providers=(), fetched_at=1_000.0, reachable=True)
        rendered = self.renderer.render(empty, ("codex",), now=1_000.0)

        self.assertIn("carries no data", rendered)

    def test_no_enabled_provider_says_so_rather_than_rendering_nothing(self):
        rendered = self.renderer.render(self.snapshot, (), now=1787571936.0)

        self.assertIn("No provider is enabled", rendered)

    def test_the_colour_thresholds_match_the_widget(self):
        theme = load_themes()["dracula"]

        self.assertEqual(TerminalRenderer.theme_color(WARNING_PERCENT - 1, theme), theme.good)
        self.assertEqual(TerminalRenderer.theme_color(WARNING_PERCENT, theme), theme.warning)
        self.assertEqual(TerminalRenderer.theme_color(DANGER_PERCENT - 1, theme), theme.warning)
        self.assertEqual(TerminalRenderer.theme_color(DANGER_PERCENT, theme), theme.danger)

    def test_an_unknown_theme_or_language_is_refused(self):
        with self.assertRaises(ValueError):
            TerminalRenderer(color=False, clear=False, theme="nope")
        with self.assertRaises(ValueError):
            TerminalRenderer(color=False, clear=False, language="fr")


class SerialisationTests(unittest.TestCase):
    def test_only_the_requested_providers_are_serialised(self):
        snapshot = parse_snapshot(_payload(), fetched_at=1_000.0, reachable=True)

        rows = json.loads(snapshot_as_json(snapshot, ("codex", "gemini")))["providers"]

        self.assertEqual([row["provider"] for row in rows], ["codex", "gemini"])

    def test_the_serialised_windows_are_already_in_display_order(self):
        snapshot = parse_snapshot(_payload(), fetched_at=1_000.0, reachable=True)

        rows = json.loads(snapshot_as_json(snapshot, ("claude",)))["providers"]

        # The page renders what it is given, so the lead-first order has to
        # survive the wire rather than be recomputed in JavaScript.
        self.assertEqual([window["id"] for window in rows[0]["windows"]], ["7d", "5h"])


class WebTests(unittest.TestCase):
    def setUp(self):
        self.client = SnapshotClient(
            "http://example/usage.json",
            cache_path=Path(tempfile.mkdtemp()) / "snapshot.json",
            opener=lambda endpoint, timeout: _payload(),
        )

    def test_one_cached_snapshot_still_answers_different_provider_lists(self):
        cache = SnapshotCache(self.client, ttl=600.0)

        first = json.loads(cache.payload(("codex", "claude"), now=1_000.0))
        second = json.loads(cache.payload(("gemini",), now=1_001.0))

        # Caching the rendered JSON instead of the snapshot would hand the
        # first caller's provider list to every widget after it.
        self.assertEqual([row["provider"] for row in first["providers"]], ["codex", "claude"])
        self.assertEqual([row["provider"] for row in second["providers"]], ["gemini"])

    def test_the_publisher_is_asked_once_inside_the_ttl(self):
        calls = []

        def count(endpoint, timeout):
            calls.append(endpoint)
            return _payload()

        cache = SnapshotCache(
            SnapshotClient(
                "http://example/usage.json",
                cache_path=Path(tempfile.mkdtemp()) / "snapshot.json",
                opener=count,
            ),
            ttl=600.0,
        )

        cache.payload(("codex",), now=1_000.0)
        cache.payload(("claude",), now=1_100.0)

        self.assertEqual(len(calls), 1)

    def test_the_publisher_is_asked_again_once_the_ttl_lapses(self):
        calls = []

        def count(endpoint, timeout):
            calls.append(endpoint)
            return _payload()

        cache = SnapshotCache(
            SnapshotClient(
                "http://example/usage.json",
                cache_path=Path(tempfile.mkdtemp()) / "snapshot.json",
                opener=count,
            ),
            ttl=20.0,
        )

        cache.payload(("codex",), now=1_000.0)
        cache.payload(("codex",), now=1_050.0)

        self.assertEqual(len(calls), 2)

    def test_a_url_can_narrow_the_provider_list(self):
        self.assertEqual(
            resolve_providers("gemini,codex", ("codex", "claude", "grok", "gemini")),
            ("codex", "gemini"),
        )

    def test_an_empty_or_unknown_url_list_keeps_the_configured_one(self):
        configured = ("codex", "claude")

        self.assertEqual(resolve_providers(None, configured), configured)
        self.assertEqual(resolve_providers("", configured), configured)
        # A widget pointed at a provider this server does not serve should show
        # the configured set rather than an empty page.
        self.assertEqual(resolve_providers("nonesuch", configured), configured)

    def test_the_page_carries_its_configuration_and_no_external_request(self):
        page = render_page(
            providers=("codex", "grok"),
            language="en",
            theme="nord",
            compact=True,
            interval=45,
        )

        self.assertIn('"providers": ["codex", "grok"]', page)
        self.assertIn('"compact": true', page)
        self.assertIn(f'"warning": {WARNING_PERCENT}', page)
        # A widget frame is often offline-ish, and a CDN round trip is exactly
        # what leaves it blank.
        self.assertNotIn("https://", page.split("<style>")[1])

    def test_the_page_escapes_its_title(self):
        page = render_page(
            providers=(), language="en", theme="nord", compact=False, interval=60
        )

        self.assertNotIn("<script>alert", page)


def _refuse(endpoint, timeout):
    raise OSError("connection refused")


if __name__ == "__main__":
    unittest.main()
