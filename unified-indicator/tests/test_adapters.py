import json
import tempfile
import unittest
from datetime import datetime, timezone
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
from claude_oauth import (
    ClaudeOAuthSnapshot,
    ClaudeOAuthUnavailable,
    ClaudeOAuthWindow,
)


class AdapterTests(unittest.TestCase):
    def test_manager_config_strips_matching_value_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "providers.env"
            config.write_text(
                'export CODEX=\'true\'\n'
                'export CODEX_RATE_SOURCE="auto"\n'
                "DISPLAY_MODE='custom'\n",
                encoding="utf-8",
            )

            values = read_manager_config(config)
            enabled = enabled_providers(config)

        self.assertEqual(values["CODEX_RATE_SOURCE"], "auto")
        self.assertEqual(values["DISPLAY_MODE"], "custom")
        self.assertEqual(enabled, ("codex",))

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

    def test_claude_adapter_returns_no_data_without_oauth(self):
        with patch(
            "claude_oauth.fetch_oauth_snapshot",
            side_effect=ClaudeOAuthUnavailable("no credentials"),
        ):
            snapshot = load_claude()

        self.assertEqual(snapshot.provider, "claude")
        self.assertEqual(snapshot.windows, ())
        self.assertEqual(snapshot.status, "no_data")

    def test_claude_adapter_prefers_oauth_usage(self):
        oauth_snapshot = ClaudeOAuthSnapshot(
            updated_at=datetime.now(timezone.utc).isoformat(),
            windows=(
                ClaudeOAuthWindow("5h", 17, "2026-07-30T11:00:00Z"),
                ClaudeOAuthWindow("7d", 39, "2026-08-05T00:00:00Z"),
            ),
        )
        with patch(
            "claude_oauth.fetch_oauth_snapshot",
            return_value=oauth_snapshot,
        ):
            snapshot = load_claude()

        self.assertEqual([window.id for window in snapshot.windows], ["5h", "7d"])
        self.assertEqual([window.used_percent for window in snapshot.windows], [17, 39])
        self.assertEqual(snapshot.status, "fresh")

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
            with patch.dict("os.environ", {"XDG_CACHE_HOME": tmp}), patch(
                "adapters.read_manager_config",
                return_value={"CODEX_RATE_SOURCE": "auto"},
            ):
                snapshot = load_codex()

        expected_expiration = datetime.fromisoformat(
            "2026-08-12T18:12:36.625939+00:00"
        ).astimezone().strftime("%Y-%m-%d %H:%M")
        self.assertEqual(snapshot.extras[0], "Reset credits: 1")
        self.assertEqual(snapshot.extras[1], f"1. expires {expected_expiration}")

    def test_codex_adapter_keeps_reset_credit_row_when_api_has_no_data(self):
        class Snapshot:
            updated_at = "2026-07-30T08:00:00Z"
            five_hour = None
            weekly = None
            reset_credits_available = None
            reset_credit_expirations = ()

        with patch(
            "adapters.read_manager_config",
            return_value={"CODEX_RATE_SOURCE": "auto"},
        ), patch("wham.read_wham_snapshot", return_value=Snapshot()):
            snapshot = load_codex()

        self.assertEqual(snapshot.extras, ("Reset credits: --",))

    def test_codex_local_source_never_reads_wham_cache(self):
        with patch(
            "adapters.read_manager_config",
            return_value={"CODEX_RATE_SOURCE": "local"},
        ), patch(
            "wham.read_wham_snapshot",
            side_effect=AssertionError("wham cache should not be read"),
        ), patch("codex_rate.find_latest_snapshot", return_value=None):
            snapshot = load_codex()

        self.assertEqual(snapshot.status, "no_data")

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
