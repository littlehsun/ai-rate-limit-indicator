import tempfile
import unittest
from pathlib import Path

from models import ProviderSnapshot, UsageWindow, countdown, write_snapshot_cache


class ModelTests(unittest.TestCase):
    def test_countdown_formats_shared_reset_window(self):
        self.assertEqual(countdown(1_000 + 2 * 86400 + 3 * 3600, now=1_000), "2d3h")
        self.assertEqual(countdown(1_000 + 2 * 3600 + 5 * 60, now=1_000), "2h5m")

    def test_normalized_cache_contains_all_provider_fields(self):
        snapshot = ProviderSnapshot(
            provider="codex",
            label="Codex",
            updated_at="2026-07-30T00:00:00Z",
            windows=(UsageWindow("7d", "7D", 42, 2_000),),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshots.json"
            write_snapshot_cache((snapshot,), path)
            text = path.read_text(encoding="utf-8")
        self.assertIn('"provider": "codex"', text)
        self.assertIn('"used_percent": 42', text)


if __name__ == "__main__":
    unittest.main()
