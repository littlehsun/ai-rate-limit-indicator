#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import patch

import indicator
from indicator import make_icon_svg
from grok_rate import GrokBillingSnapshot, PeriodUsage, format_indicator_parts


class IndicatorSvgTests(unittest.TestCase):
    def test_svg_split_colors_for_weekly_and_monthly(self):
        snap = GrokBillingSnapshot(
            updated_at="2026-07-24T00:00:00+00:00",
            weekly=PeriodUsage(95, period_end="2026-07-29T00:00:00+00:00"),
            monthly=PeriodUsage(20, used_cents=3000, limit_cents=15000),
        )
        weekly, monthly, reset = format_indicator_parts(snap, now=0)
        svg = make_icon_svg(
            [
                (weekly, "red"),
                ("|", "#FFFFFF"),
                (monthly, "green"),
                (f"  ⟳{reset}", "#FFFFFF"),
            ],
            "red",
        )
        self.assertIn('<image x="1" y="1" width="20" height="20"', svg)
        self.assertIn('href="data:image/png;base64,', svg)
        self.assertIn('fill="#FF5555"', svg)
        self.assertIn('fill="#00AF50"', svg)
        self.assertIn('fill="#FFFFFF"', svg)
        self.assertIn("95%", svg)
        self.assertIn("20%", svg)
        self.assertIn('xml:space="preserve"', svg)

    def test_svg_green_for_low_usage(self):
        svg = make_icon_svg("4%|1%  ⟳4d", "green")
        self.assertIn('<image x="1" y="1" width="20" height="20"', svg)
        self.assertIn('fill="#00AF50"', svg)
        self.assertIn("4%", svg)

    def test_svg_falls_back_to_status_circle_when_logo_is_unavailable(self):
        with patch.object(indicator, "GROK_LOGO_DATA_URI", None):
            svg = make_icon_svg("Grok --", "yellow")

        self.assertIn('<circle cx="8" cy="11" r="6" fill="#E6C800"/>', svg)
        self.assertNotIn("<image ", svg)


if __name__ == "__main__":
    unittest.main()
