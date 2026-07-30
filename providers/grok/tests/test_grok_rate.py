#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from grok_rate import (
    GrokBillingSnapshot,
    PeriodUsage,
    fetch_billing_snapshot,
    format_indicator_label,
    format_menu_line,
    format_usd_cents,
    merge_snapshots,
    parse_credits_payload,
    parse_monthly_payload,
    poll_and_cache,
    read_access_token,
    read_cache,
    write_cache,
)


MONTHLY_PAYLOAD = {
    "config": {
        "monthlyLimit": {"val": 15000},
        "used": {"val": 178},
        "onDemandCap": {"val": 0},
        "billingPeriodStart": "2026-07-01T00:00:00+00:00",
        "billingPeriodEnd": "2026-08-01T00:00:00+00:00",
        "history": [],
    }
}

CREDITS_PAYLOAD = {
    "config": {
        "currentPeriod": {
            "type": "USAGE_PERIOD_TYPE_WEEKLY",
            "start": "2026-07-22T00:00:00+00:00",
            "end": "2026-07-29T00:00:00+00:00",
        },
        "creditUsagePercent": 4.0,
        "onDemandCap": {"val": 0},
        "onDemandUsed": {"val": 0},
        "productUsage": [
            {"product": "GrokBuild", "usagePercent": 4.0},
            {"product": "GrokChat"},
        ],
        "isUnifiedBillingUser": True,
        "prepaidBalance": {"val": 0},
        "billingPeriodStart": "2026-07-22T00:00:00+00:00",
        "billingPeriodEnd": "2026-07-29T00:00:00+00:00",
    }
}


class GrokRateTests(unittest.TestCase):
    def test_parse_monthly_payload_as_usd_cents(self):
        monthly = parse_monthly_payload(MONTHLY_PAYLOAD)
        self.assertIsNotNone(monthly)
        assert monthly is not None
        self.assertEqual(monthly.used_cents, 178)
        self.assertEqual(monthly.limit_cents, 15000)
        self.assertEqual(monthly.used_percent, 1)  # 178/15000 ≈ 1.19 → 1
        self.assertEqual(format_usd_cents(15000), "$150")
        self.assertEqual(format_usd_cents(178), "$1.78")

    def test_parse_credits_weekly_percent(self):
        weekly, meta = parse_credits_payload(CREDITS_PAYLOAD)
        self.assertIsNotNone(weekly)
        assert weekly is not None
        self.assertEqual(weekly.used_percent, 4)
        self.assertEqual(weekly.period_start, "2026-07-22T00:00:00+00:00")
        self.assertEqual(weekly.period_end, "2026-07-29T00:00:00+00:00")
        self.assertTrue(meta["is_unified"])
        self.assertEqual(meta["product_usage"][0], ("GrokBuild", 4))

    def test_zero_percent_preserved(self):
        payload = {
            "config": {
                "creditUsagePercent": 0,
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_WEEKLY",
                    "start": "2026-07-22T00:00:00+00:00",
                    "end": "2026-07-29T00:00:00+00:00",
                },
            }
        }
        weekly, _ = parse_credits_payload(payload)
        self.assertIsNotNone(weekly)
        assert weekly is not None
        self.assertEqual(weekly.used_percent, 0)

    def test_format_indicator_weekly_monthly(self):
        snap = merge_snapshots(
            weekly=PeriodUsage(4, "2026-07-22T00:00:00+00:00", "2026-07-25T12:00:00+00:00"),
            monthly=PeriodUsage(1, "2026-07-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00", 178, 15000),
            updated_at="2026-07-24T00:00:00+00:00",
        )
        assert snap is not None
        now = int(datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(format_indicator_label(snap, now=now), "4%|1%  ⟳1d")

    def test_format_menu_money_and_percent(self):
        monthly = PeriodUsage(
            used_percent=1,
            period_start="2026-07-01T00:00:00+00:00",
            period_end="2026-08-01T00:00:00+00:00",
            used_cents=178,
            limit_cents=15000,
        )
        line = format_menu_line(monthly, "Monthly", now=0)
        self.assertIn("$1.78 / $150", line)
        self.assertIn("(1%)", line)

        weekly = PeriodUsage(4, period_end="2026-07-29T00:00:00+00:00")
        wline = format_menu_line(weekly, "Weekly", now=0)
        self.assertIn("4%", wline)
        self.assertNotIn("$", wline)

    def test_read_access_token_prefers_unexpired(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = {
                "expired": {"key": "old-token", "expires_at": "2020-01-01T00:00:00+00:00"},
                "fresh": {"key": "new-token", "expires_at": "2099-01-01T00:00:00+00:00"},
            }
            (home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
            self.assertEqual(read_access_token(home), "new-token")

    def test_cache_roundtrip(self):
        snap = merge_snapshots(
            weekly=PeriodUsage(4, "2026-07-22T00:00:00+00:00", "2026-07-29T00:00:00+00:00"),
            monthly=PeriodUsage(1, "2026-07-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00", 178, 15000),
            meta={
                "product_usage": (("GrokBuild", 4), ("GrokChat", None)),
                "is_unified": True,
                "on_demand_cap_cents": 0,
            },
            updated_at="2026-07-24T00:00:00+00:00",
        )
        assert snap is not None
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "billing.json"
            write_cache(snap, path)
            loaded = read_cache(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.weekly.used_percent, 4)
            self.assertEqual(loaded.monthly.used_cents, 178)
            self.assertEqual(loaded.product_usage[0], ("GrokBuild", 4))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_legacy_cache_still_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "billing.json"
            path.write_text(
                json.dumps(
                    {
                        "updated_at": "2026-07-24T00:00:00+00:00",
                        "used": 50,
                        "monthly_limit": 15000,
                        "on_demand_cap": 0,
                        "period_start": "2026-07-01T00:00:00+00:00",
                        "period_end": "2026-08-01T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            loaded = read_cache(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertIsNone(loaded.weekly)
            self.assertEqual(loaded.monthly.used_cents, 50)

    def test_fetch_combines_both_endpoints(self):
        def fake_urlopen(req, timeout=15):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()

            class FakeResp:
                def __init__(self, body: bytes):
                    self._body = body

                def read(self):
                    return self._body

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            if "format=credits" in url:
                return FakeResp(json.dumps(CREDITS_PAYLOAD).encode())
            return FakeResp(json.dumps(MONTHLY_PAYLOAD).encode())

        with mock.patch("grok_rate.urllib.request.urlopen", side_effect=fake_urlopen):
            snap = fetch_billing_snapshot(token="tok")
        self.assertEqual(snap.weekly.used_percent, 4)
        self.assertEqual(snap.monthly.used_cents, 178)
        self.assertEqual(snap.monthly.used_percent, 1)
        self.assertTrue(snap.is_unified)

    def test_poll_and_cache_writes_file(self):
        def fake_urlopen(req, timeout=15):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()

            class FakeResp:
                def __init__(self, body: bytes):
                    self._body = body

                def read(self):
                    return self._body

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            if "format=credits" in url:
                return FakeResp(json.dumps(CREDITS_PAYLOAD).encode())
            return FakeResp(json.dumps(MONTHLY_PAYLOAD).encode())

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "grok"
            home.mkdir()
            (home / "auth.json").write_text(
                json.dumps({"https://auth.x.ai::x": {"key": "tok", "expires_at": "2099-01-01T00:00:00+00:00"}}),
                encoding="utf-8",
            )
            cache = Path(tmp) / "billing.json"
            with mock.patch("grok_rate.urllib.request.urlopen", side_effect=fake_urlopen):
                snap = poll_and_cache(grok_home=home, cache_path=cache)
            self.assertEqual(snap.weekly.used_percent, 4)
            loaded = read_cache(cache)
            self.assertEqual(loaded.weekly.used_percent, 4)
            self.assertEqual(loaded.monthly.limit_cents, 15000)


if __name__ == "__main__":
    unittest.main()
