import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_rate import format_indicator_label
from wham import (
    describe_missing_token,
    format_reset_credit_lines,
    merge_reset_credits,
    parse_usage_response,
    preserve_cached_reset_credits,
    read_codex_access_token,
    read_wham_snapshot,
    resolve_access_token,
    token_is_expired,
    write_wham_snapshot,
)


class WhamTests(unittest.TestCase):
    def test_classifies_windows_by_duration_not_primary_secondary_order(self):
        snapshot = parse_usage_response(
            {
                "account_id": "acct_123",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 25,
                        "limit_window_seconds": 604800,
                        "reset_at": "2026-07-01T08:00:00Z",
                    },
                    "secondary_window": {
                        "limit": 100,
                        "remaining": 10,
                        "limit_window_seconds": 18000,
                        "reset_at": "2026-07-07T08:00:00Z",
                    },
                    "rate_limit_reset_credits": {"available_count": 2},
                },
            },
            updated_at="2026-07-01T00:00:00Z",
        )

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.source_kind, "wham")
        self.assertEqual(snapshot.account_id, "acct_123")
        self.assertEqual(snapshot.five_hour.used_percent, 90)
        self.assertEqual(snapshot.weekly.used_percent, 25)
        self.assertEqual(snapshot.reset_credits_available, 2)
        self.assertEqual(format_indicator_label(snapshot, now=1782889200), "90%|25% R2  ⟳6d1h")

    def test_missing_five_hour_window_displays_zero_before_weekly(self):
        snapshot = parse_usage_response(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 7,
                        "limit_window_seconds": 604800,
                        "reset_at": 1784526033,
                    },
                    "secondary_window": None,
                },
            },
            updated_at="2026-07-13T08:00:00Z",
        )

        self.assertIsNone(snapshot.five_hour)
        self.assertEqual(snapshot.weekly.used_percent, 7)
        self.assertEqual(format_indicator_label(snapshot, now=1783910400), "0%|7%  ⟳--")

    def test_zero_used_percent_is_valid_after_weekly_reset(self):
        snapshot = parse_usage_response(
            {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 0,
                        "limit_window_seconds": 604800,
                        "reset_at": 1784679913,
                    },
                    "secondary_window": None,
                },
            },
            updated_at="2026-07-15T03:16:00Z",
        )

        self.assertIsNone(snapshot.five_hour)
        self.assertEqual(snapshot.weekly.used_percent, 0)
        self.assertEqual(format_indicator_label(snapshot, now=1784085360), "0%|0%  ⟳--")

    def test_merge_reset_credits_prefers_detailed_endpoint(self):
        snapshot = parse_usage_response(
            {
                "account_id": "acct_123",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 1,
                        "limit_window_seconds": 18000,
                        "reset_at": 1782892800,
                    },
                    "secondary_window": {
                        "used_percent": 2,
                        "limit_window_seconds": 604800,
                        "reset_at": 1783411200,
                    },
                    "rate_limit_reset_credits": {"available_count": 1},
                },
            },
            updated_at="2026-07-01T00:00:00Z",
        )

        merged = merge_reset_credits(
            snapshot,
            {
                "available_count": 3,
                "credits": [
                    {"expires_at": "2026-07-02T00:00:00Z"},
                    {"expires_at": "2026-07-03T00:00:00Z"},
                    {"expires_at": "2026-07-04T00:00:00Z"},
                ],
            },
        )

        self.assertEqual(merged.reset_credits_available, 3)
        self.assertEqual(
            format_reset_credit_lines(merged),
            [
                "Reset credits: R3",
                "1. expires 2026-07-02 08:00",
                "2. expires 2026-07-03 08:00",
                "3. expires 2026-07-04 08:00",
            ],
        )

    def test_preserves_cached_reset_credits_when_endpoint_is_unavailable(self):
        cached = parse_usage_response(
            {
                "account_id": "acct_123",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 1,
                        "limit_window_seconds": 604800,
                        "reset_at": 1783411200,
                    },
                    "rate_limit_reset_credits": {"available_count": 2},
                },
            },
            updated_at="2026-07-01T00:00:00Z",
        )
        snapshot = parse_usage_response(
            {
                "account_id": "acct_123",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 2,
                        "limit_window_seconds": 604800,
                        "reset_at": 1783411200,
                    },
                },
            },
            updated_at="2026-07-01T00:01:00Z",
        )

        preserved = preserve_cached_reset_credits(snapshot, cached)

        self.assertEqual(preserved.reset_credits_available, 2)
        self.assertEqual(preserved.updated_at, "2026-07-01T00:01:00Z")

    def test_writes_and_reads_wham_cache(self):
        snapshot = parse_usage_response(
            {
                "account_id": "acct_123",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 11,
                        "limit_window_seconds": 18000,
                        "reset_at": 1782892800,
                    },
                    "secondary_window": {
                        "used_percent": 22,
                        "limit_window_seconds": 604800,
                        "reset_at": 1783411200,
                    },
                    "rate_limit_reset_credits": {"available_count": 1},
                },
            },
            updated_at="2026-07-01T00:00:00Z",
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wham.json"
            write_wham_snapshot(snapshot, path)
            restored = read_wham_snapshot(path)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.account_id, "acct_123")
        self.assertEqual(restored.five_hour.used_percent, 11)
        self.assertEqual(restored.weekly.used_percent, 22)
        self.assertEqual(restored.reset_credits_available, 1)

    def test_reads_codex_auth_access_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text(json.dumps({"tokens": {"access_token": "token-from-codex"}}), encoding="utf-8")

            self.assertEqual(read_codex_access_token(path), "token-from-codex")

    def test_resolves_env_token_before_codex_auth_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(json.dumps({"tokens": {"access_token": "token-from-codex"}}), encoding="utf-8")

            env = {
                "CHATGPT_ACCESS_TOKEN": "token-from-env",
                "CODEX_AUTH_FILE": str(auth_path),
            }
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(resolve_access_token(), "token-from-env")

    def test_resolves_codex_auth_token_when_env_token_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(json.dumps({"tokens": {"access_token": "token-from-codex"}}), encoding="utf-8")

            with patch.dict(os.environ, {"CODEX_AUTH_FILE": str(auth_path)}, clear=True):
                self.assertEqual(resolve_access_token(), "token-from-codex")

    def test_missing_codex_auth_token_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CODEX_AUTH_FILE": str(Path(tmp) / "missing.json")}, clear=True):
                self.assertIsNone(resolve_access_token())

    def test_never_resolves_an_expired_jwt(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(
                json.dumps({"tokens": {"access_token": _jwt(exp=1_000)}}),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"CODEX_AUTH_FILE": str(auth_path)}, clear=True):
                self.assertIsNone(resolve_access_token(now=2_000))
                self.assertIn("expired", describe_missing_token(auth_path))

    def test_resolves_a_live_jwt(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            token = _jwt(exp=9_000)
            auth_path.write_text(
                json.dumps({"tokens": {"access_token": token}}), encoding="utf-8"
            )

            with patch.dict(os.environ, {"CODEX_AUTH_FILE": str(auth_path)}, clear=True):
                self.assertEqual(resolve_access_token(now=2_000), token)

    def test_an_expired_env_token_is_skipped_for_the_auth_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            live = _jwt(exp=9_000)
            auth_path.write_text(
                json.dumps({"tokens": {"access_token": live}}), encoding="utf-8"
            )

            env = {
                "CHATGPT_ACCESS_TOKEN": _jwt(exp=1_000),
                "CODEX_AUTH_FILE": str(auth_path),
            }
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(resolve_access_token(now=2_000), live)

    def test_opaque_tokens_stay_usable(self):
        # Only a token that proves its own expiry is refused; refusing every
        # token we cannot parse would break the environment overrides.
        self.assertFalse(token_is_expired("token-from-env", now=2_000))
        self.assertFalse(token_is_expired(_jwt(), now=2_000))

    def test_missing_token_message_falls_back_without_an_auth_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            message = describe_missing_token(Path(tmp) / "missing.json")

        self.assertIn("CHATGPT_ACCESS_TOKEN", message)
        self.assertNotIn("expired", message)


def _jwt(**claims: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


if __name__ == "__main__":
    unittest.main()
