import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agy_rate import (
    AgyQuotaSnapshot,
    AgyQuotaWindow,
    fetch_quota_with_cli,
    find_agy_cli,
    parse_quota_payload,
    read_cache,
    record_start_attempt,
    start_is_in_cooldown,
    write_cache,
)


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0


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

    def test_starting_agy_stays_opt_in(self):
        spawned = []
        with mock.patch.dict(os.environ, {"AGY_AUTO_START": ""}, clear=False):
            self.assertIsNone(fetch_quota_with_cli(spawner=spawned.append))

        self.assertEqual(spawned, [])

    def test_starting_agy_reads_quota_then_stops_the_process(self):
        snapshot = AgyQuotaSnapshot(
            updated_at="2026-07-30T06:00:00+00:00",
            windows=(AgyQuotaWindow("gemini", "Gemini", "5h", 3, 0.97, None),),
        )
        process = FakeProcess()
        # The server needs a moment to listen, so the read has to retry.
        attempts = [RuntimeError("AGY is not running"), snapshot]

        def fetch(**_kwargs):
            result = attempts.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AGY_AUTO_START": "1"}
        ), mock.patch("agy_rate.find_agy_cli", return_value="/usr/bin/agy"), mock.patch(
            "agy_rate.fetch_quota_snapshot", side_effect=fetch
        ):
            result = fetch_quota_with_cli(
                spawner=lambda _bin: process,
                stamp_path=Path(tmp) / "stamp",
                sleep=lambda _seconds: None,
            )

        self.assertEqual(result, snapshot)
        # Antigravity was only wanted for the read, so it does not linger.
        self.assertTrue(process.terminated)

    def test_starting_agy_gives_up_and_still_stops_the_process(self):
        process = FakeProcess()
        clock = iter([0.0, 0.0, 1_000.0])

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AGY_AUTO_START": "1"}
        ), mock.patch("agy_rate.find_agy_cli", return_value="/usr/bin/agy"), mock.patch(
            "agy_rate.fetch_quota_snapshot",
            side_effect=RuntimeError("AGY is not running"),
        ), mock.patch("agy_rate.time.monotonic", side_effect=lambda: next(clock)):
            result = fetch_quota_with_cli(
                spawner=lambda _bin: process,
                stamp_path=Path(tmp) / "stamp",
                sleep=lambda _seconds: None,
            )

        self.assertIsNone(result)
        self.assertTrue(process.terminated)

    def test_starting_agy_waits_out_the_cooldown(self):
        spawned = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AGY_AUTO_START": "1"}
        ), mock.patch("agy_rate.find_agy_cli", return_value="/usr/bin/agy"), mock.patch(
            "agy_rate.fetch_quota_snapshot",
            side_effect=RuntimeError("AGY is not running"),
        ), mock.patch("agy_rate.time.monotonic", side_effect=[0.0, 1_000.0] * 4):
            stamp = Path(tmp) / "stamp"

            def start(now):
                return fetch_quota_with_cli(
                    spawner=lambda _bin: spawned.append(_bin) or FakeProcess(),
                    stamp_path=stamp,
                    now=now,
                    sleep=lambda _seconds: None,
                )

            start(1_000.0)
            self.assertEqual(len(spawned), 1)

            start(1_060.0)
            self.assertEqual(len(spawned), 1)

            start(1_400.0)
            self.assertEqual(len(spawned), 2)

    def test_cooldown_ignores_a_stamp_from_the_future(self):
        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "stamp"
            record_start_attempt(stamp, now=9_000.0)

            self.assertFalse(start_is_in_cooldown(stamp, now=1_000.0))
            self.assertTrue(start_is_in_cooldown(stamp, now=9_100.0))
            self.assertFalse(start_is_in_cooldown(stamp, now=9_500.0))

    def test_a_non_executable_cli_override_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "agy"
            plain.write_text("", encoding="utf-8")
            plain.chmod(0o600)

            with mock.patch.dict(os.environ, {"AGY_CLI": str(plain)}):
                self.assertIsNone(find_agy_cli())


if __name__ == "__main__":
    unittest.main()
