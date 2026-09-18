import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from claude_oauth import (
    CLI_USAGE_COOLDOWN,
    fetch_usage_with_cli,
    parse_usage_text,
    BETA_HEADER,
    CLIENT_ID,
    TOKEN_ENDPOINT,
    ClaudeOAuthSnapshot,
    ClaudeOAuthUnavailable,
    ClaudeOAuthWindow,
    fetch_oauth_snapshot,
    read_cache,
    read_credentials,
    refresh_credentials,
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


def _refreshable_credential(refresh_token="rt-old", expires_at=0):
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "at-old",
                "refreshToken": refresh_token,
                "expiresAt": expires_at,
                "refreshTokenExpiresAt": 5_000_000_000_000,
                "scopes": ["user:profile"],
                "subscriptionType": "pro",
            }
        },
        indent=2,
    )


class ClaudeOAuthRefreshTests(unittest.TestCase):
    """The refresh has to survive a Claude Code running alongside it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.credentials = root / ".credentials.json"
        self.credentials.write_text(_refreshable_credential(), encoding="utf-8")
        self.stamp = root / "stamp"
        patch = mock.patch.dict(os.environ, {"CLAUDE_AUTO_REFRESH": "true"})
        patch.start()
        self.addCleanup(patch.stop)

    def _stored(self):
        return json.loads(self.credentials.read_text(encoding="utf-8"))["claudeAiOauth"]

    def test_a_refresh_rotates_both_tokens_and_keeps_the_other_fields(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return {
                "access_token": "at-new",
                "refresh_token": "rt-new",
                "expires_in": 28_800,
                "refresh_token_expires_in": 2_592_000,
            }

        credentials = refresh_credentials(
            self.credentials, opener=opener, stamp_path=self.stamp, now=1_000.0
        )

        self.assertEqual(captured["url"], TOKEN_ENDPOINT)
        self.assertEqual(captured["body"]["grant_type"], "refresh_token")
        self.assertEqual(captured["body"]["refresh_token"], "rt-old")
        self.assertEqual(captured["body"]["client_id"], CLIENT_ID)

        self.assertIsNotNone(credentials)
        self.assertEqual(credentials.access_token, "at-new")
        stored = self._stored()
        self.assertEqual(stored["accessToken"], "at-new")
        self.assertEqual(stored["refreshToken"], "rt-new")
        self.assertEqual(stored["expiresAt"], 1_000_000 + 28_800_000)
        # Fields the token response says nothing about must survive untouched.
        self.assertEqual(stored["subscriptionType"], "pro")
        self.assertEqual(stored["scopes"], ["user:profile"])
        self.assertEqual(self.credentials.stat().st_mode & 0o777, 0o600)
        # Trimming must not leave the staging file behind.
        self.assertEqual(
            sorted(p.name for p in self.credentials.parent.iterdir()),
            [".credentials.json", "stamp"],
        )

    def test_a_response_without_a_new_refresh_token_keeps_the_old_one(self):
        def opener(request, timeout):
            return {"access_token": "at-new", "expires_in": 28_800}

        refresh_credentials(
            self.credentials, opener=opener, stamp_path=self.stamp, now=1_000.0
        )

        self.assertEqual(self._stored()["refreshToken"], "rt-old")

    def test_a_refresh_that_lost_the_race_leaves_the_file_alone(self):
        # Claude Code refreshed while our request was in flight, so the token we
        # spent is already dead and its pair is the live one.
        def opener(request, timeout):
            self.credentials.write_text(
                _refreshable_credential(refresh_token="rt-theirs"),
                encoding="utf-8",
            )
            return {
                "access_token": "at-ours",
                "refresh_token": "rt-ours",
                "expires_in": 28_800,
            }

        credentials = refresh_credentials(
            self.credentials, opener=opener, stamp_path=self.stamp, now=1_000.0
        )

        self.assertIsNone(credentials)
        self.assertEqual(self._stored()["refreshToken"], "rt-theirs")
        self.assertEqual(self._stored()["accessToken"], "at-old")

    def test_a_failed_refresh_never_blanks_the_credential(self):
        # Claude Code answers invalid_grant by emptying both tokens on disk,
        # which costs a full re-login. We must not copy that.
        def opener(request, timeout):
            raise ClaudeOAuthUnavailable("Claude OAuth usage request failed")

        before = self.credentials.read_text(encoding="utf-8")
        credentials = refresh_credentials(
            self.credentials, opener=opener, stamp_path=self.stamp, now=1_000.0
        )

        self.assertIsNone(credentials)
        self.assertEqual(self.credentials.read_text(encoding="utf-8"), before)

    def test_an_explicit_flag_beats_the_environment(self):
        # The indicator never sees providers.env in its environment, so the
        # adapter reads the flag and passes it in; that must win either way.
        def opener(request, timeout):
            raise AssertionError("no request may be made while the flag is off")

        self.assertIsNone(
            refresh_credentials(
                self.credentials,
                opener=opener,
                stamp_path=self.stamp,
                enabled=False,
            )
        )

        with mock.patch.dict(os.environ, {"CLAUDE_AUTO_REFRESH": "false"}):
            credentials = refresh_credentials(
                self.credentials,
                opener=lambda request, timeout: {
                    "access_token": "at-new",
                    "expires_in": 28_800,
                },
                stamp_path=self.stamp,
                now=1_000.0,
                enabled=True,
            )
        self.assertIsNotNone(credentials)

    def test_the_flag_is_opt_in(self):
        def opener(request, timeout):
            raise AssertionError("no request may be made while the flag is off")

        with mock.patch.dict(os.environ, {"CLAUDE_AUTO_REFRESH": "false"}):
            self.assertIsNone(
                refresh_credentials(
                    self.credentials, opener=opener, stamp_path=self.stamp
                )
            )

    def test_a_recent_attempt_holds_the_next_one_off(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            return {"access_token": "at-new", "expires_in": 28_800}

        refresh_credentials(
            self.credentials, opener=opener, stamp_path=self.stamp, now=1_000.0
        )
        refresh_credentials(
            self.credentials, opener=opener, stamp_path=self.stamp, now=1_060.0
        )
        self.assertEqual(len(calls), 1)

        # Past the cooldown it may try again.
        refresh_credentials(
            self.credentials, opener=opener, stamp_path=self.stamp, now=1_400.0
        )
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()


USAGE_SCREEN = """You are currently using your subscription to power your Claude Code usage

Current session: 25% used · resets Sep 18, 5pm (Asia/Taipei)
Current week (all models): 99% used · resets Sep 20, 8:59am (Asia/Taipei)

What's contributing to your limits usage?
Last 24h · 542 requests · 3 sessions
  90% of your usage was at >150k context
"""

NOW = datetime(2026, 9, 18, 8, 30, tzinfo=timezone.utc)


class UsageScreenTests(unittest.TestCase):
    """Reading the screen Claude Code prints for a person.

    It is a fallback for the morning after an idle night, when the token has
    expired and the API read cannot happen at all. Prose is the price, so the
    percentages are read independently of the dates: a window with no reset
    time still says something true, and a guessed one does not.
    """

    def test_both_windows_are_read(self):
        windows = parse_usage_text(USAGE_SCREEN, now=NOW)
        self.assertEqual([(w.id, w.used_percent) for w in windows], [("5h", 25), ("7d", 99)])

    def test_the_reset_times_land_in_the_stated_zone(self):
        windows = {w.id: w.resets_at for w in parse_usage_text(USAGE_SCREEN, now=NOW)}
        # 5pm in Taipei is 09:00 UTC.
        self.assertEqual(windows["5h"], "2026-09-18T09:00:00+00:00")
        self.assertEqual(windows["7d"], "2026-09-20T00:59:00+00:00")

    def test_a_percentage_without_a_readable_reset_still_counts(self):
        windows = parse_usage_text("Current session: 40% used · resets whenever", now=NOW)
        self.assertEqual(windows[0].used_percent, 40)
        self.assertIsNone(windows[0].resets_at)

    def test_an_unknown_timezone_falls_back_rather_than_failing(self):
        windows = parse_usage_text(
            "Current session: 40% used · resets Sep 18, 5pm (Mars/Olympus)", now=NOW
        )
        self.assertEqual(windows[0].used_percent, 40)

    def test_a_december_reset_read_in_december_rolls_into_next_year(self):
        december = datetime(2026, 12, 31, 20, 0, tzinfo=timezone.utc)
        windows = parse_usage_text(
            "Current week (all models): 10% used · resets Jan 2, 9am (UTC)", now=december
        )
        self.assertEqual(windows[0].resets_at, "2027-01-02T09:00:00+00:00")

    def test_the_opus_line_is_not_mistaken_for_the_all_models_one(self):
        text = (
            "Current week (all models): 99% used · resets Sep 20, 8:59am (Asia/Taipei)\n"
            "Current week (Opus): 12% used · resets Sep 20, 8:59am (Asia/Taipei)\n"
        )
        windows = parse_usage_text(text, now=NOW)
        self.assertEqual([(w.id, w.used_percent) for w in windows], [("7d", 99)])

    def test_a_screen_with_no_limit_lines_is_no_windows(self):
        self.assertEqual(parse_usage_text("Welcome to Claude Code", now=NOW), ())

    def test_an_absurd_percentage_is_clamped(self):
        windows = parse_usage_text("Current session: 250% used", now=NOW)
        self.assertEqual(windows[0].used_percent, 100)


class UsageCommandTests(unittest.TestCase):
    def _runner(self, result, calls=None):
        def run(claude_bin, timeout):
            if calls is not None:
                calls.append(claude_bin)
            return json.dumps({"result": result, "total_cost_usd": 0})

        return run

    def test_the_fallback_is_a_switch(self):
        calls = []
        with mock.patch.dict(os.environ, {"CLAUDE_USAGE_CLI": "false"}, clear=False):
            self.assertIsNone(
                fetch_usage_with_cli(runner=self._runner(USAGE_SCREEN, calls))
            )
        self.assertEqual(calls, [])

    def test_a_screen_becomes_a_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "claude_oauth.find_claude_cli", return_value="/usr/bin/claude"
        ):
            snapshot = fetch_usage_with_cli(
                enabled=True,
                runner=self._runner(USAGE_SCREEN),
                stamp_path=Path(tmp) / "stamp",
                now=NOW,
            )
        self.assertEqual([w.id for w in snapshot.windows], ["5h", "7d"])
        self.assertEqual(snapshot.updated_at, NOW.isoformat())

    def test_a_cli_that_fails_keeps_the_caller_s_cache(self):
        for failure in (OSError("no binary"), ValueError("not json")):
            with self.subTest(failure=type(failure).__name__):
                with tempfile.TemporaryDirectory() as tmp, mock.patch(
                    "claude_oauth.find_claude_cli", return_value="/usr/bin/claude"
                ):
                    self.assertIsNone(
                        fetch_usage_with_cli(
                            enabled=True,
                            runner=mock.Mock(side_effect=failure),
                            stamp_path=Path(tmp) / "stamp",
                        )
                    )

    def test_output_without_the_limit_lines_is_not_a_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "claude_oauth.find_claude_cli", return_value="/usr/bin/claude"
        ):
            self.assertIsNone(
                fetch_usage_with_cli(
                    enabled=True,
                    runner=self._runner("Welcome to Claude Code"),
                    stamp_path=Path(tmp) / "stamp",
                )
            )

    def test_the_cooldown_keeps_a_hanging_cli_to_one_attempt(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "claude_oauth.find_claude_cli", return_value="/usr/bin/claude"
        ):
            stamp = Path(tmp) / "stamp"
            runner = self._runner(USAGE_SCREEN, calls)
            fetch_usage_with_cli(enabled=True, runner=runner, stamp_path=stamp, now=NOW)
            fetch_usage_with_cli(
                enabled=True,
                runner=runner,
                stamp_path=stamp,
                now=NOW + timedelta(seconds=60),
            )
            self.assertEqual(len(calls), 1)
            fetch_usage_with_cli(
                enabled=True,
                runner=runner,
                stamp_path=stamp,
                now=NOW + timedelta(seconds=CLI_USAGE_COOLDOWN + 1),
            )
            self.assertEqual(len(calls), 2)

    def test_the_spawn_turns_the_auto_updater_off(self):
        # A probe on a timer that lets the CLI resume an unfinished update
        # pulls gigabytes; CodexBar measured 90 GiB in three days.
        import inspect
        from claude_oauth import _run_usage_command

        self.assertIn("DISABLE_AUTOUPDATER", inspect.getsource(_run_usage_command))
