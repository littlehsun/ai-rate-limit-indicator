import json
import tempfile
import unittest
from datetime import timezone, timedelta
from pathlib import Path

from codex_rate import (
    CodexRateSnapshot,
    RateWindow,
    find_latest_snapshot,
    format_indicator_label,
    format_menu_line,
    format_updated_at,
)


class CodexRateTests(unittest.TestCase):
    def test_finds_latest_rate_limit_snapshot_across_rollouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_rollout = root / "sessions/2026/05/04/rollout-old.jsonl"
            new_rollout = root / "sessions/2026/05/05/rollout-new.jsonl"
            old_rollout.parent.mkdir(parents=True)
            new_rollout.parent.mkdir(parents=True)

            self._write_jsonl(
                old_rollout,
                [
                    self._token_count("2026-05-04T01:00:00Z", 40, 12),
                ],
            )
            self._write_jsonl(
                new_rollout,
                [
                    {"timestamp": "2026-05-05T00:00:00Z", "type": "event_msg", "payload": {"type": "agent_message"}},
                    self._token_count("2026-05-05T00:01:00Z", 2, 13),
                    self._token_count("2026-05-05T00:02:00Z", 3, 14),
                ],
            )

            snapshot = find_latest_snapshot(root)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.updated_at, "2026-05-05T00:02:00Z")
        self.assertEqual(snapshot.five_hour.used_percent, 3)
        self.assertEqual(snapshot.weekly.used_percent, 14)

    def test_returns_none_when_no_rate_limits_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "sessions/2026/05/05/rollout-empty.jsonl"
            rollout.parent.mkdir(parents=True)
            self._write_jsonl(
                rollout,
                [{"timestamp": "2026-05-05T00:00:00Z", "type": "event_msg", "payload": {"type": "agent_message"}}],
            )

            snapshot = find_latest_snapshot(root)

        self.assertIsNone(snapshot)

    def test_ignores_newer_rate_limits_with_no_valid_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "sessions/2026/05/05/rollout-partial.jsonl"
            rollout.parent.mkdir(parents=True)
            self._write_jsonl(
                rollout,
                [
                    self._token_count("2026-05-05T00:00:00Z", 44, 55),
                    {
                        "timestamp": "2026-05-05T00:01:00Z",
                        "type": "event_msg",
                        "payload": {"type": "token_count", "rate_limits": {}},
                    },
                ],
            )

            snapshot = find_latest_snapshot(root)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.updated_at, "2026-05-05T00:00:00Z")
        self.assertEqual(snapshot.five_hour.used_percent, 44)
        self.assertEqual(snapshot.weekly.used_percent, 55)

    def test_formats_compact_indicator_label(self):
        snapshot = CodexRateSnapshot.from_rate_limits(
            "2026-05-05T00:02:00Z",
            {
                "primary": {"used_percent": 3, "window_minutes": 300, "resets_at": 1777929435},
                "secondary": {"used_percent": 14, "window_minutes": 10080, "resets_at": 1778480096},
            },
        )

        label = format_indicator_label(snapshot, now=1777911600)

        self.assertEqual(label, "3%|14%  ⟳4h57m")

    def test_formats_weekly_only_indicator_label(self):
        snapshot = CodexRateSnapshot.from_rate_limits(
            "2026-05-05T00:02:00Z",
            {
                "primary": {"used_percent": 3, "window_minutes": 300, "resets_at": 1777915200},
                "secondary": {"used_percent": 14, "window_minutes": 10080, "resets_at": 1778516400},
            },
        )

        label = format_indicator_label(
            snapshot,
            now=1777911600,
            show_five_hour=False,
        )

        self.assertEqual(label, "14%  ⟳7d0h")

    def test_formats_updated_at_as_local_time_to_minute(self):
        local_tz = timezone(timedelta(hours=8))

        label = format_updated_at("2026-05-05T10:01:45.409Z", tz=local_tz)

        self.assertEqual(label, "2026-05-05 18:01")

    def test_formats_claude_style_menu_with_countdown_in_parentheses(self):
        line = format_menu_line(
            RateWindow(used_percent=42, window_minutes=300, resets_at=1777929435),
            "⚡ 5H",
            now=1777911600,
        )

        self.assertTrue(line.startswith("⚡ 5H: 42%  ⟳ "))
        self.assertTrue(line.endswith("(4h57m)"))

    def _token_count(self, timestamp, five_hour_pct, weekly_pct):
        return {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {
                        "used_percent": five_hour_pct,
                        "window_minutes": 300,
                        "resets_at": 1777929435,
                    },
                    "secondary": {
                        "used_percent": weekly_pct,
                        "window_minutes": 10080,
                        "resets_at": 1778480096,
                    },
                    "plan_type": "prolite",
                },
            },
        }

    def _write_jsonl(self, path, rows):
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
