#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
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
    parse_auto_topup_payload,
    parse_monthly_payload,
    poll_and_cache,
    describe_missing_token,
    find_grok_cli,
    read_access_token,
    read_user_id_for_token,
    record_refresh_attempt,
    refresh_is_in_cooldown,
    refresh_token_with_cli,
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

    def test_parse_auto_topup_rule(self):
        meta = parse_auto_topup_payload(
            {
                "rule": {
                    "enabled": True,
                    "topupAmount": {"val": -1000},
                    "maxAmountPerMonth": {"val": -5000},
                }
            }
        )

        self.assertTrue(meta["auto_topup_enabled"])
        self.assertEqual(meta["auto_topup_amount_cents"], -1000)
        self.assertEqual(meta["auto_topup_monthly_cap_cents"], -5000)

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

    def test_missing_percent_at_start_of_week_is_zero(self):
        payload = {
            "config": {
                "currentPeriod": {
                    "type": "USAGE_PERIOD_TYPE_WEEKLY",
                    "start": "2026-07-31T07:13:41.975176+00:00",
                    "end": "2026-08-07T07:13:41.975176+00:00",
                },
                "billingPeriodStart": "2026-07-31T07:13:41.975176+00:00",
                "billingPeriodEnd": "2026-08-07T07:13:41.975176+00:00",
            }
        }

        weekly, _ = parse_credits_payload(payload)

        self.assertIsNotNone(weekly)
        assert weekly is not None
        self.assertEqual(weekly.used_percent, 0)
        self.assertEqual(weekly.period_end, "2026-08-07T07:13:41.975176+00:00")

    def test_missing_percent_without_weekly_period_remains_no_data(self):
        weekly, _ = parse_credits_payload(
            {
                "config": {
                    "billingPeriodStart": "2026-07-01T00:00:00+00:00",
                    "billingPeriodEnd": "2026-08-01T00:00:00+00:00",
                }
            }
        )

        self.assertIsNone(weekly)

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
        now = int(datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc).timestamp())
        line = format_menu_line(monthly, "Monthly", now=now)
        self.assertIn("$1.78 / $150", line)
        self.assertIn("(1%)", line)
        self.assertIn("  ⟳ ", line)
        self.assertTrue(line.endswith("(1d12h)"))

        weekly = PeriodUsage(4, period_end="2026-07-29T00:00:00+00:00")
        wline = format_menu_line(weekly, "Weekly", now=now)
        self.assertIn("4%", wline)
        self.assertNotIn("$", wline)
        self.assertIn("  ⟳ ", wline)

    def test_read_access_token_prefers_unexpired(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = {
                "expired": {"key": "old-token", "expires_at": "2020-01-01T00:00:00+00:00"},
                "fresh": {"key": "new-token", "expires_at": "2099-01-01T00:00:00+00:00"},
            }
            (home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
            self.assertEqual(read_access_token(home), "new-token")

    def test_reads_user_id_paired_with_selected_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "auth.json").write_text(
                json.dumps(
                    {
                        "fresh": {
                            "key": "new-token",
                            "user_id": "user-123",
                            "expires_at": "2099-01-01T00:00:00+00:00",
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                read_user_id_for_token("new-token", home), "user-123"
            )

    def test_read_access_token_never_returns_an_expired_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = {
                "expired": {
                    "key": "old-token",
                    "expires_at": "2020-01-01T00:00:00+00:00",
                }
            }
            (home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")

            # Sending it would only earn a 401 on every poll.
            self.assertIsNone(read_access_token(home))
            self.assertIn("expired", describe_missing_token(home))

    def test_missing_token_is_reported_apart_from_an_expired_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "auth.json").write_text("{}", encoding="utf-8")

            self.assertIsNone(read_access_token(home))
            message = describe_missing_token(home)
            self.assertIn("no access token found", message)
            self.assertNotIn("expired", message)

    def test_cli_refresh_is_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "auth.json").write_text(
                json.dumps(
                    {"e": {"key": "old", "expires_at": "2020-01-01T00:00:00+00:00"}}
                ),
                encoding="utf-8",
            )
            calls = []
            with mock.patch.dict(os.environ, {"GROK_AUTO_REFRESH": ""}):
                self.assertIsNone(
                    refresh_token_with_cli(home, runner=calls.append)
                )

        self.assertEqual(calls, [])

    def test_cli_refresh_rereads_the_token_the_cli_wrote(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = home / "auth.json"
            auth.write_text(
                json.dumps(
                    {"e": {"key": "old", "expires_at": "2020-01-01T00:00:00+00:00"}}
                ),
                encoding="utf-8",
            )

            def fake_cli(_grok_bin):
                auth.write_text(
                    json.dumps(
                        {
                            "e": {
                                "key": "refreshed",
                                "expires_at": "2099-01-01T00:00:00+00:00",
                            }
                        }
                    ),
                    encoding="utf-8",
                )

            with mock.patch.dict(os.environ, {"GROK_AUTO_REFRESH": "1"}), mock.patch(
                "grok_rate.shutil.which", return_value="/usr/bin/grok"
            ):
                self.assertEqual(
                    refresh_token_with_cli(
                        home, runner=fake_cli, stamp_path=home / "stamp"
                    ),
                    "refreshed",
                )

    def test_cli_refresh_survives_a_failing_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "auth.json").write_text(
                json.dumps(
                    {"e": {"key": "old", "expires_at": "2020-01-01T00:00:00+00:00"}}
                ),
                encoding="utf-8",
            )

            def boom(_grok_bin):
                raise subprocess.TimeoutExpired("grok", 60)

            with mock.patch.dict(os.environ, {"GROK_AUTO_REFRESH": "1"}), mock.patch(
                "grok_rate.shutil.which", return_value="/usr/bin/grok"
            ):
                self.assertIsNone(
                    refresh_token_with_cli(
                        home, runner=boom, stamp_path=home / "stamp"
                    )
                )

    def test_cli_refresh_waits_out_the_cooldown_after_a_failure(self):
        # The poller fires every 60s; a CLI that cannot refresh must not earn a
        # subprocess on every tick.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            stamp = home / "stamp"
            (home / "auth.json").write_text(
                json.dumps(
                    {"e": {"key": "old", "expires_at": "2020-01-01T00:00:00+00:00"}}
                ),
                encoding="utf-8",
            )
            calls = []

            with mock.patch.dict(os.environ, {"GROK_AUTO_REFRESH": "1"}), mock.patch(
                "grok_rate.shutil.which", return_value="/usr/bin/grok"
            ):
                refresh_token_with_cli(
                    home, runner=calls.append, stamp_path=stamp, now=1_000.0
                )
                self.assertEqual(len(calls), 1)

                # One minute later: still inside the cooldown.
                refresh_token_with_cli(
                    home, runner=calls.append, stamp_path=stamp, now=1_060.0
                )
                self.assertEqual(len(calls), 1)

                # Past the cooldown, the nudge is allowed through again.
                refresh_token_with_cli(
                    home, runner=calls.append, stamp_path=stamp, now=1_400.0
                )
                self.assertEqual(len(calls), 2)

    def test_cooldown_ignores_a_stamp_from_the_future(self):
        with tempfile.TemporaryDirectory() as tmp:
            stamp = Path(tmp) / "stamp"
            record_refresh_attempt(stamp, now=9_000.0)

            # A clock that jumped backwards must not lock the nudge out until
            # it catches up.
            self.assertFalse(refresh_is_in_cooldown(stamp, now=1_000.0))
            self.assertTrue(refresh_is_in_cooldown(stamp, now=9_100.0))
            self.assertFalse(refresh_is_in_cooldown(stamp, now=9_500.0))

    def test_cooldown_treats_an_unreadable_stamp_as_no_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(refresh_is_in_cooldown(Path(tmp) / "absent"))

            broken = Path(tmp) / "broken"
            broken.write_text("not a timestamp", encoding="utf-8")
            self.assertFalse(refresh_is_in_cooldown(broken))

    def test_cli_is_found_through_the_installer_override(self):
        # A launchd/systemd poller gets a bare PATH, so `which` finds nothing
        # and only the recorded absolute path works.
        with tempfile.TemporaryDirectory() as tmp:
            grok_bin = Path(tmp) / "grok"
            grok_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            grok_bin.chmod(0o700)
            with mock.patch.dict(os.environ, {"GROK_CLI": str(grok_bin)}), mock.patch(
                "grok_rate.shutil.which", return_value=None
            ):
                self.assertEqual(find_grok_cli(), str(grok_bin))

    def test_a_non_executable_override_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent"
            with mock.patch.dict(os.environ, {"GROK_CLI": str(missing)}), mock.patch(
                "grok_rate.shutil.which", return_value=None
            ):
                self.assertIsNone(find_grok_cli())

    def test_well_known_location_is_used_when_path_misses(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            grok_bin = home / ".local" / "bin" / "grok"
            grok_bin.parent.mkdir(parents=True)
            grok_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            grok_bin.chmod(0o700)
            with mock.patch.dict(os.environ, {"GROK_CLI": ""}), mock.patch(
                "grok_rate.shutil.which", return_value=None
            ), mock.patch("grok_rate.Path.home", return_value=home):
                self.assertEqual(find_grok_cli(), str(grok_bin))

    def test_cli_refresh_does_nothing_when_no_cli_can_be_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "auth.json").write_text("{}", encoding="utf-8")
            calls = []
            # home is patched too, so the well-known locations stay empty and
            # the developer's own install cannot satisfy the lookup.
            with mock.patch.dict(os.environ, {
                "GROK_AUTO_REFRESH": "1",
                "GROK_CLI": "",
            }), mock.patch(
                "grok_rate.shutil.which", return_value=None
            ), mock.patch("grok_rate.Path.home", return_value=home):
                self.assertIsNone(
                    refresh_token_with_cli(home, runner=calls.append)
                )

        self.assertEqual(calls, [])

    def test_token_without_an_expiry_is_still_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = {"legacy": {"key": "no-expiry-token"}}
            (home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
            self.assertEqual(read_access_token(home), "no-expiry-token")

    def test_unparsable_expiry_does_not_discard_the_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            auth = {"odd": {"key": "kept-token", "expires_at": "not-a-date"}}
            (home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
            self.assertEqual(read_access_token(home), "kept-token")

    def test_cache_roundtrip(self):
        snap = merge_snapshots(
            weekly=PeriodUsage(4, "2026-07-22T00:00:00+00:00", "2026-07-29T00:00:00+00:00"),
            monthly=PeriodUsage(1, "2026-07-01T00:00:00+00:00", "2026-08-01T00:00:00+00:00", 178, 15000),
            meta={
                "product_usage": (("GrokBuild", 4), ("GrokChat", None)),
                "is_unified": True,
                "on_demand_cap_cents": 0,
                "prepaid_balance_cents": -1250,
                "auto_topup_enabled": True,
                "auto_topup_amount_cents": -1000,
                "auto_topup_monthly_cap_cents": -5000,
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
            self.assertEqual(loaded.prepaid_balance_cents, -1250)
            self.assertTrue(loaded.auto_topup_enabled)
            self.assertEqual(loaded.auto_topup_amount_cents, -1000)
            self.assertEqual(loaded.auto_topup_monthly_cap_cents, -5000)
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
