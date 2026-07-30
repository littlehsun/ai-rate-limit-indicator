import unittest
from unittest.mock import patch

import indicator


class GeminiIndicatorTests(unittest.TestCase):
    def test_svg_uses_logo_and_split_metric_colors(self):
        svg = indicator.make_icon_svg(
            [
                ("32%", "green"),
                ("|", indicator.NEUTRAL_TEXT_COLOR),
                ("88%", "yellow"),
                ("  ⟳4h0m", indicator.NEUTRAL_TEXT_COLOR),
            ],
            "yellow",
        )
        self.assertIn("data:image/svg+xml;base64,", svg)
        self.assertIn('<tspan fill="#00AF50">32%</tspan>', svg)
        self.assertIn('<tspan fill="#FFFFFF">|</tspan>', svg)
        self.assertIn('<tspan fill="#E6C800">88%</tspan>', svg)
        self.assertIn('<tspan fill="#FFFFFF">  ⟳4h0m</tspan>', svg)

    def test_svg_falls_back_when_logo_is_unavailable(self):
        with patch.object(indicator, "GEMINI_LOGO_DATA_URI", None):
            svg = indicator.make_icon_svg("Gemini --", "yellow")
        self.assertIn('<circle cx="8" cy="11" r="6" fill="#E6C800"/>', svg)


if __name__ == "__main__":
    unittest.main()
