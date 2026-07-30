import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adapters import (
    display_settings,
    dropdown_providers,
    enabled_providers,
    load_claude,
    load_codex,
    load_gemini,
    load_grok,
    provider_display_order,
    read_manager_config,
    write_display_settings,
)
from agy_rate import AgyQuotaSnapshot, AgyQuotaWindow


class AdapterTests(unittest.TestCase):
    def test_config_selects_enabled_providers_in_stable_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "providers.env"
            config.write_text(
                "CODEX=true\nCLAUDE=false\nGROK=yes\nGEMINI=1\n",
                encoding="utf-8",
            )
            self.assertEqual(
                enabled_providers(config),
                ("codex", "grok", "gemini"),
            )

    def test_claude_adapter_normalizes_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "claude.json"
            source.write_text(
                json.dumps(
                    {
                        "utilization_5h": 30,
                        "reset_5h": 2_000,
                        "utilization_7d": 99,
                        "reset_7d": 3_000,
                        "updated_at": 1_500,
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"CLAUDE_RATE_LIMITS_FILE": str(source)}):
                snapshot = load_claude()
        self.assertEqual(snapshot.provider, "claude")
        self.assertEqual([window.id for window in snapshot.windows], ["5h", "7d"])
        self.assertEqual([window.used_percent for window in snapshot.windows], [30, 99])
        self.assertEqual(snapshot.status, "stale")

    def test_display_settings_roundtrip_preserves_provider_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "providers.env"
            config.write_text(
                "CODEX=true\nCLAUDE=true\nGROK=true\nGEMINI=true\n"
                "DISPLAY_MODE=auto\nDISPLAY_PROVIDERS=codex,grok\n"
                "DROPDOWN_PROVIDERS=codex,claude,grok\n"
                "PROVIDER_ORDER=codex,grok,claude,gemini\n",
                encoding="utf-8",
            )

            write_display_settings(
                "custom",
                ("grok", "codex"),
                config,
                dropdown=("grok", "gemini"),
                provider_order=("grok", "claude", "codex", "gemini"),
            )
            mode, providers = display_settings(read_manager_config(config))

            self.assertEqual(mode, "custom")
            self.assertEqual(providers, ("grok", "codex"))
            self.assertEqual(
                dropdown_providers(read_manager_config(config)),
                ("grok", "gemini"),
            )
            self.assertEqual(
                provider_display_order(read_manager_config(config)),
                ("grok", "claude", "codex", "gemini"),
            )
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)

    def test_codex_adapter_keeps_reset_credit_expiration(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "codex-rate-indicator" / "wham.json"
            cache.parent.mkdir()
            cache.write_text(
                json.dumps(
                    {
                        "updated_at": "2026-07-30T05:50:00Z",
                        "five_hour": None,
                        "weekly": {
                            "used_percent": 25,
                            "window_minutes": 10080,
                            "resets_at": 1_785_902_956,
                        },
                        "source_kind": "wham",
                        "reset_credits_available": 1,
                        "reset_credit_expirations": [
                            "2026-08-12T18:12:36.625939Z",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"XDG_CACHE_HOME": tmp}):
                snapshot = load_codex()

        self.assertEqual(snapshot.extras[0], "Reset credits: R1")
        self.assertIn("1. expires 2026-08-13", snapshot.extras[1])

    def test_grok_adapter_labels_weekly_window_as_7d(self):
        class Window:
            used_percent = 23
            period_end = "2026-08-01T00:00:00Z"

        class Snapshot:
            weekly = Window()
            monthly = None
            updated_at = "2026-07-30T06:00:00Z"
            product_usage = ()

        with patch("grok_rate.read_cache", return_value=Snapshot()):
            snapshot = load_grok()

        self.assertEqual(snapshot.windows[0].label, "7D")

    def test_gemini_adapter_prefers_live_agy_quota(self):
        agy_snapshot = AgyQuotaSnapshot(
            updated_at="2026-07-30T06:00:00+00:00",
            windows=(
                AgyQuotaWindow(
                    "gemini",
                    "Gemini",
                    "5h",
                    1,
                    0.99,
                    "2026-07-30T11:00:00Z",
                ),
                AgyQuotaWindow(
                    "gemini",
                    "Gemini",
                    "7d",
                    0,
                    0.998,
                    "2026-08-06T06:00:00Z",
                ),
                AgyQuotaWindow(
                    "claude-gpt",
                    "Claude/GPT",
                    "5h",
                    4,
                    0.96,
                    None,
                ),
            ),
        )
        with (
            patch("agy_rate.fetch_quota_snapshot", return_value=agy_snapshot),
            patch("agy_rate.write_cache") as write_cache,
        ):
            snapshot = load_gemini()

        write_cache.assert_called_once_with(agy_snapshot)
        self.assertEqual(
            [window.id for window in snapshot.windows], ["5h", "7d", "claude-gpt-5h"]
        )
        self.assertEqual(
            [window.label for window in snapshot.windows],
            ["Gemini 5H", "Gemini 7D", "Claude/GPT 5H"],
        )
        self.assertEqual(snapshot.extras, ())

    def test_gemini_adapter_keeps_last_agy_snapshot_when_process_stops(self):
        cached = AgyQuotaSnapshot(
            updated_at="2026-07-30T06:00:00+00:00",
            windows=(
                AgyQuotaWindow(
                    "gemini",
                    "Gemini",
                    "5h",
                    2,
                    0.98,
                    None,
                ),
            ),
        )
        with (
            patch(
                "agy_rate.fetch_quota_snapshot",
                side_effect=RuntimeError("AGY is not running"),
            ),
            patch("agy_rate.read_cache", return_value=cached),
        ):
            snapshot = load_gemini()

        self.assertEqual(snapshot.windows[0].label, "Gemini 5H")
        self.assertEqual(snapshot.windows[0].used_percent, 2)
        self.assertEqual(snapshot.status, "stale")


if __name__ == "__main__":
    unittest.main()
