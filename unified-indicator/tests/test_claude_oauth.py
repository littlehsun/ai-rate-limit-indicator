import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from claude_oauth import (
    BETA_HEADER,
    ClaudeOAuthSnapshot,
    ClaudeOAuthUnavailable,
    ClaudeOAuthWindow,
    fetch_oauth_snapshot,
    read_cache,
    read_credentials,
    write_cache,
)


def _credential_json(access_token, expires_at=4_102_444_800_000, scopes=None):
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": access_token,
                "expiresAt": expires_at,
                "scopes": scopes or ["user:profile"],
            }
        }
    )


class ClaudeOAuthTests(unittest.TestCase):
    def test_reads_claude_code_credential_shape_without_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / ".credentials.json"
            credentials.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "test-oauth-token",
                            "refreshToken": "test-refresh-token",
                            "expiresAt": 2_000_000,
                            "scopes": ["user:profile", "user:inference"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            parsed = read_credentials(credentials, now_ms=1_000_000)

        self.assertEqual(parsed.access_token, "test-oauth-token")
        self.assertEqual(parsed.scopes, ("user:profile", "user:inference"))
        self.assertNotIn("test-oauth-token", repr(parsed))
        self.assertNotIn("refresh", repr(parsed))

    def test_rejects_expired_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / ".credentials.json"
            credentials.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "expired-token",
                            "expiresAt": 1_000_000,
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ClaudeOAuthUnavailable, "expired"):
                read_credentials(credentials, now_ms=1_000_000)

    def test_fetches_usage_with_codexbar_headers_and_maps_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / ".credentials.json"
            credentials.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "live-token",
                            "expiresAt": 4_102_444_800_000,
                            "scopes": ["user:profile"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            captured = {}

            def opener(request, timeout):
                captured["request"] = request
                captured["timeout"] = timeout
                return {
                    "five_hour": {
                        "utilization": 12.5,
                        "resets_at": "2026-07-30T11:00:00Z",
                    },
                    "seven_day": {
                        "utilization": 44.6,
                        "resets_at": "2026-08-05T00:00:00Z",
                    },
                }

            snapshot = fetch_oauth_snapshot(
                credentials,
                opener=opener,
                now=datetime(2026, 7, 30, 6, 0, tzinfo=timezone.utc),
            )

        request = captured["request"]
        self.assertEqual(request.full_url, "https://api.anthropic.com/api/oauth/usage")
        self.assertEqual(request.get_header("Authorization"), "Bearer live-token")
        self.assertEqual(request.get_header("Anthropic-beta"), BETA_HEADER)
        self.assertEqual(captured["timeout"], 30.0)
        self.assertEqual([window.id for window in snapshot.windows], ["5h", "7d"])
        self.assertEqual(
            [window.used_percent for window in snapshot.windows],
            [13, 45],
        )


class ClaudeOAuthKeychainTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(
            os.environ,
            {"CLAUDE_CONFIG_DIR": "", "CLAUDE_OAUTH_CREDENTIALS_FILE": ""},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_falls_back_to_keychain_when_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            parsed = read_credentials(
                now_ms=1_000_000,
                keychain_reader=lambda: _credential_json("keychain-token"),
            )

        self.assertEqual(parsed.access_token, "keychain-token")

    def test_prefers_the_credentials_file_over_the_keychain(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            Path(tmp, ".credentials.json").write_text(
                _credential_json("file-token"), encoding="utf-8"
            )
            parsed = read_credentials(
                now_ms=1_000_000,
                keychain_reader=self.fail,
            )

        self.assertEqual(parsed.access_token, "file-token")

    def test_falls_back_to_keychain_when_the_file_token_is_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            Path(tmp, ".credentials.json").write_text(
                _credential_json("stale-token", expires_at=1_000_000),
                encoding="utf-8",
            )
            parsed = read_credentials(
                now_ms=1_000_000,
                keychain_reader=lambda: _credential_json("keychain-token"),
            )

        self.assertEqual(parsed.access_token, "keychain-token")

    def test_reports_the_file_error_when_the_keychain_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            Path(tmp, ".credentials.json").write_text(
                _credential_json("stale-token", expires_at=1_000_000),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ClaudeOAuthUnavailable, "expired"):
                read_credentials(now_ms=1_000_000, keychain_reader=lambda: None)

    def test_file_override_does_not_consult_the_keychain(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_OAUTH_CREDENTIALS_FILE"] = str(
                Path(tmp, "absent.json")
            )
            with self.assertRaises(ClaudeOAuthUnavailable):
                read_credentials(now_ms=1_000_000, keychain_reader=self.fail)

    def test_explicit_path_does_not_consult_the_keychain(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ClaudeOAuthUnavailable):
                read_credentials(
                    Path(tmp, "absent.json"),
                    now_ms=1_000_000,
                    keychain_reader=self.fail,
                )

    def test_mcp_only_credentials_point_at_signing_in_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.json"
            path.write_text(
                json.dumps({"mcpOAuth": {"some-server": {"accessToken": "x"}}}),
                encoding="utf-8",
            )

            with self.assertRaises(ClaudeOAuthUnavailable) as caught:
                read_credentials(path, now_ms=1_000_000)

        self.assertIn("sign in again", str(caught.exception))

    def test_unrecognised_credentials_keep_the_generic_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "credentials.json"
            path.write_text(json.dumps({"somethingElse": {}}), encoding="utf-8")

            with self.assertRaises(ClaudeOAuthUnavailable) as caught:
                read_credentials(path, now_ms=1_000_000)

        self.assertIn("no claudeAiOauth entry", str(caught.exception))

    def test_cache_roundtrip_keeps_windows_and_owner_only_permissions(self):
        snapshot = ClaudeOAuthSnapshot(
            updated_at="2026-07-30T06:00:00+00:00",
            windows=(
                ClaudeOAuthWindow("5h", 17, "2026-07-30T11:00:00Z"),
                ClaudeOAuthWindow("7d", 39, None),
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "claude-oauth.json"
            write_cache(snapshot, path)
            loaded = read_cache(path)

            self.assertEqual(loaded, snapshot)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_reading_an_absent_or_broken_cache_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_cache(Path(tmp) / "absent.json"))

            broken = Path(tmp) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertIsNone(read_cache(broken))


if __name__ == "__main__":
    unittest.main()
