import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from gemini_rate import (
    fetch_quota_snapshot,
    format_indicator_label,
    format_menu_line,
    parse_quota_payload,
    read_access_token,
    read_cache,
    write_cache,
)


class GeminiRateTests(unittest.TestCase):
    def test_groups_models_by_tier_and_uses_most_constrained_bucket(self):
        snapshot = parse_quota_payload(
            {
                "buckets": [
                    {
                        "modelId": "gemini-3-pro-preview",
                        "remainingFraction": 0.68,
                        "resetTime": "2026-07-30T16:00:00Z",
                    },
                    {
                        "modelId": "gemini-2.5-pro",
                        "remainingFraction": 0.90,
                        "resetTime": "2026-07-31T00:00:00Z",
                    },
                    {
                        "modelId": "gemini-3-flash-preview",
                        "remainingFraction": 0.92,
                        "resetTime": "2026-07-30T18:00:00Z",
                    },
                ]
            },
            updated_at="2026-07-30T08:00:00Z",
        )
        self.assertEqual([window.label for window in snapshot.windows], ["Pro", "Flash"])
        self.assertEqual([window.used_percent for window in snapshot.windows], [32, 8])
        self.assertEqual(snapshot.windows[0].model_id, "gemini-3-pro-preview")

    def test_formats_indicator_and_claude_style_menu_with_countdown(self):
        snapshot = parse_quota_payload(
            {
                "buckets": [
                    {
                        "modelId": "gemini-3-pro-preview",
                        "remainingFraction": 0.68,
                        "resetTime": "2026-07-30T16:00:00Z",
                    },
                    {
                        "modelId": "gemini-3-flash-preview",
                        "remainingFraction": 0.92,
                        "resetTime": "2026-07-30T18:00:00Z",
                    },
                ]
            }
        )
        now = int(datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(format_indicator_label(snapshot, now=now), "32%|8%  ⟳4h0m")
        line = format_menu_line(snapshot.windows[0], now=now)
        self.assertTrue(line.startswith("✨ Pro: 32%  ⟳ "))
        self.assertIn("(4h0m)", line)

    def test_cache_roundtrip_contains_no_token(self):
        snapshot = parse_quota_payload(
            {"buckets": [{"modelId": "gemini-3-pro", "remainingFraction": 0.5}]},
            updated_at="2026-07-30T08:00:00Z",
        )
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "quota.json"
            write_cache(snapshot, cache)
            restored = read_cache(cache)
            text = cache.read_text(encoding="utf-8")
        self.assertEqual(restored, snapshot)
        self.assertNotIn("access_token", text)
        self.assertNotIn("refresh_token", text)

    def test_refreshes_expired_oauth_and_preserves_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            credentials_path = home / "oauth_creds.json"
            credentials_path.write_text(
                json.dumps(
                    {
                        "access_token": "expired",
                        "refresh_token": "refresh-value",
                        "expiry_date": 1000,
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "gemini_rate._request_form",
                return_value={"access_token": "fresh", "expires_in": 3600},
            ):
                with patch.dict(
                    "os.environ",
                    {
                        "GEMINI_OAUTH_CLIENT_ID": "test-client-id",
                        "GEMINI_OAUTH_CLIENT_SECRET": "test-client-secret",
                    },
                ):
                    token = read_access_token(home, now_ms=2_000)
            updated = json.loads(credentials_path.read_text(encoding="utf-8"))

        self.assertEqual(token, "fresh")
        self.assertEqual(updated["refresh_token"], "refresh-value")
        self.assertEqual(updated["expiry_date"], 3_602_000)

    def test_fetch_discovers_project_and_sends_supported_quota_payload(self):
        with patch(
            "gemini_rate._request_json",
            side_effect=[
                {"cloudaicompanionProject": "quota-project"},
                {
                    "buckets": [
                        {
                            "modelId": "gemini-3-pro",
                            "remainingFraction": 0.75,
                        }
                    ]
                },
            ],
        ) as request:
            snapshot = fetch_quota_snapshot("local-token")

        self.assertEqual(snapshot.windows[0].used_percent, 25)
        self.assertEqual(request.call_args_list[1].args[1], {"project": "quota-project"})
        self.assertNotIn("userAgent", request.call_args_list[1].args[1])


if __name__ == "__main__":
    unittest.main()
