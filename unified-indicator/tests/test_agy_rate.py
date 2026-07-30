import tempfile
import unittest
from pathlib import Path

from agy_rate import parse_quota_payload, read_cache, write_cache


class AgyRateTests(unittest.TestCase):
    def test_parses_usage_groups_and_orders_five_hour_before_weekly(self):
        snapshot = parse_quota_payload(
            {
                "response": {
                    "groups": [
                        {
                            "displayName": "Gemini Models",
                            "buckets": [
                                {
                                    "bucketId": "gemini-weekly",
                                    "displayName": "Weekly Limit",
                                    "remainingFraction": 0.99841374,
                                    "resetTime": "2026-08-06T06:09:39Z",
                                },
                                {
                                    "bucketId": "gemini-5h",
                                    "displayName": "Five Hour Limit",
                                    "remainingFraction": 0.9904825,
                                    "resetTime": "2026-07-30T11:09:39Z",
                                },
                            ],
                        },
                        {
                            "displayName": "Claude and GPT models",
                            "buckets": [
                                {
                                    "bucketId": "3p-weekly",
                                    "displayName": "Weekly Limit",
                                    "remainingFraction": 0.98803973,
                                },
                                {
                                    "bucketId": "3p-5h",
                                    "displayName": "Five Hour Limit",
                                    "remainingFraction": 0.9641192,
                                },
                            ],
                        },
                    ]
                }
            },
            updated_at="2026-07-30T06:00:00+00:00",
        )

        self.assertEqual(
            [(window.group_id, window.cadence) for window in snapshot.windows],
            [
                ("gemini", "5h"),
                ("gemini", "7d"),
                ("claude-gpt", "5h"),
                ("claude-gpt", "7d"),
            ],
        )
        self.assertEqual(
            [window.used_percent for window in snapshot.windows],
            [1, 0, 4, 1],
        )

    def test_cache_roundtrip_uses_private_file(self):
        snapshot = parse_quota_payload(
            {
                "response": {
                    "groups": [
                        {
                            "displayName": "Gemini Models",
                            "buckets": [
                                {
                                    "bucketId": "gemini-5h",
                                    "remainingFraction": 0.95,
                                }
                            ],
                        }
                    ]
                }
            },
            updated_at="2026-07-30T06:00:00+00:00",
        )
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "agy.json"
            write_cache(snapshot, cache)
            restored = read_cache(cache)

            self.assertEqual(restored, snapshot)
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
