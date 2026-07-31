import inspect
import unittest

import indicator
from models import ProviderSnapshot, UsageWindow


class IndicatorTests(unittest.TestCase):
    def test_initial_menu_is_built_before_it_is_registered(self):
        source = inspect.getsource(indicator.UnifiedRateIndicator.__init__)
        self.assertLess(
            source.index("self._build_loading_menu()"),
            source.index("self.indicator.set_menu(self.menu)"),
        )

    def test_auto_mode_is_configured_in_settings_not_provider_dropdown(self):
        dropdown_source = inspect.getsource(
            indicator.UnifiedRateIndicator._rebuild_menu
        )
        settings_source = inspect.getsource(
            indicator.UnifiedRateIndicator._open_settings
        )

        self.assertNotIn("Auto (recent 7D:", dropdown_source)
        self.assertIn("Auto: recent 7D change", settings_source)

    def test_unified_icon_uses_selected_brand_and_split_colors(self):
        svg = indicator.make_icon_svg(
            "claude",
            [
                ("30%", "green"),
                ("|", indicator.METRIC_SEPARATOR_COLOR),
                ("99%", "red"),
                ("  ⟳2h", indicator.NEUTRAL_TEXT_COLOR),
            ],
            "red",
        )
        self.assertIn("data:image/svg+xml;base64,", svg)
        self.assertIn('<tspan fill="#00AF50">30%</tspan>', svg)
        self.assertIn('<tspan fill="#FF5555">99%</tspan>', svg)
        self.assertIn('<tspan fill="#808080">|</tspan>', svg)
        self.assertIn('<tspan fill="#FFFFFF">  ⟳2h</tspan>', svg)

    def test_multi_icon_keeps_configured_provider_order(self):
        svg = indicator.make_multi_icon_svg(
            (
                ("grok", [("49%", "green")], "green"),
                ("codex", [("23%", "green")], "green"),
            )
        )

        self.assertLess(svg.index(">49%</tspan>"), svg.index(">23%</tspan>"))
        self.assertIn(">│</text>", svg)

    def test_highest_mode_ignores_stale_provider(self):
        stale = ProviderSnapshot(
            "claude",
            "Claude",
            "2026-04-01T00:00:00Z",
            (UsageWindow("7d", "7D", 99),),
            status="stale",
        )
        fresh = ProviderSnapshot(
            "grok",
            "Grok",
            "2026-07-30T00:00:00Z",
            (UsageWindow("weekly", "Weekly", 49),),
        )

        selected = indicator.choose_snapshot((stale, fresh), "highest")

        self.assertEqual(selected.provider, "grok")

    def test_auto_starts_with_most_recent_7d_provider(self):
        selector = indicator.AutoDisplaySelector()
        codex = ProviderSnapshot(
            "codex",
            "Codex",
            "2026-07-30T05:00:00Z",
            (UsageWindow("7d", "7D", 90),),
        )
        grok = ProviderSnapshot(
            "grok",
            "Grok",
            "2026-07-30T05:01:00Z",
            (UsageWindow("weekly", "Weekly", 20),),
        )

        selected = selector.choose((codex, grok))

        self.assertEqual(selected.provider, "grok")

    def test_auto_selects_largest_7d_change_then_stays_stable(self):
        selector = indicator.AutoDisplaySelector()
        selector.choose(
            (
                ProviderSnapshot(
                    "codex",
                    "Codex",
                    "2026-07-30T05:00:00Z",
                    (UsageWindow("7d", "7D", 20),),
                ),
                ProviderSnapshot(
                    "grok",
                    "Grok",
                    "2026-07-30T05:01:00Z",
                    (UsageWindow("weekly", "Weekly", 40),),
                ),
            )
        )
        changed = (
            ProviderSnapshot(
                "codex",
                "Codex",
                "2026-07-30T05:02:00Z",
                (UsageWindow("7d", "7D", 30),),
            ),
            ProviderSnapshot(
                "grok",
                "Grok",
                "2026-07-30T05:03:00Z",
                (UsageWindow("weekly", "Weekly", 41),),
            ),
        )

        selected = selector.choose(changed)
        unchanged = selector.choose(
            tuple(
                ProviderSnapshot(
                    item.provider,
                    item.label,
                    "2026-07-30T05:04:00Z",
                    item.windows,
                )
                for item in changed
            )
        )

        self.assertEqual(selected.provider, "codex")
        self.assertEqual(unchanged.provider, "codex")

    def test_auto_ignores_stale_and_non_7d_providers(self):
        selector = indicator.AutoDisplaySelector()
        stale = ProviderSnapshot(
            "claude",
            "Claude",
            "2026-07-30T05:03:00Z",
            (UsageWindow("7d", "7D", 99),),
            status="stale",
        )
        gemini = ProviderSnapshot(
            "gemini",
            "Gemini",
            "2026-07-30T05:04:00Z",
            (UsageWindow("pro", "Pro", 50),),
        )
        codex = ProviderSnapshot(
            "codex",
            "Codex",
            "2026-07-30T05:00:00Z",
            (UsageWindow("7d", "7D", 10),),
        )

        selected = selector.choose((stale, gemini, codex))

        self.assertEqual(selected.provider, "codex")

    def test_window_icons_follow_window_type_instead_of_row_position(self):
        self.assertEqual(indicator.UnifiedRateIndicator._window_icon("5h"), "⚡")
        self.assertEqual(indicator.UnifiedRateIndicator._window_icon("7d"), "📅")
        self.assertEqual(indicator.UnifiedRateIndicator._window_icon("weekly"), "📅")
        self.assertEqual(indicator.UnifiedRateIndicator._window_icon("monthly"), "📅")

    def test_grok_indicator_countdown_uses_weekly_reset(self):
        weekly = UsageWindow("weekly", "7D", 1, resets_at=100)
        monthly = UsageWindow("monthly", "Monthly", 31, resets_at=200)
        snapshot = ProviderSnapshot(
            "grok",
            "Grok",
            "2026-07-31T07:20:00Z",
            (weekly, monthly),
        )

        selected = indicator.indicator_reset_window(snapshot.windows)

        self.assertEqual(selected, weekly)

    def test_indicator_countdown_prefers_five_hour_over_weekly(self):
        five_hour = UsageWindow("5h", "5H", 1, resets_at=100)
        weekly = UsageWindow("7d", "7D", 31, resets_at=200)
        snapshot = ProviderSnapshot(
            "codex",
            "Codex",
            "2026-07-31T07:20:00Z",
            (five_hour, weekly),
        )

        selected = indicator.indicator_reset_window(snapshot.windows)

        self.assertEqual(selected, five_hour)

    def test_reset_credit_extras_become_expandable_group(self):
        label, expirations, remaining = indicator.split_reset_credit_extras(
            (
                "Reset credits: 2",
                "1. expires 2026-08-13 02:12",
                "2. expires 2026-08-20 02:12",
                "Other detail",
            )
        )

        self.assertEqual(label, "Reset credits: 2")
        self.assertEqual(len(expirations), 2)
        self.assertEqual(remaining, ("Other detail",))

    def test_dropdown_follows_indicator_order_then_appends_dropdown_only_items(self):
        snapshots = tuple(
            ProviderSnapshot(provider, provider.title(), None, ())
            for provider in ("codex", "claude", "grok", "gemini")
        )

        ordered = indicator.order_dropdown_snapshots(
            snapshots,
            ("grok", "claude", "codex", "gemini"),
            ("codex", "claude", "grok"),
        )

        self.assertEqual(
            tuple(snapshot.provider for snapshot in ordered),
            ("grok", "claude", "codex"),
        )


if __name__ == "__main__":
    unittest.main()
