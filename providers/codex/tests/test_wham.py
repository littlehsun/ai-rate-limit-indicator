import base64
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from codex_rate import format_indicator_label
from wham import (
    CLIENT_ID,
    TOKEN_ENDPOINT,
    describe_exposed_auth_file,
    describe_missing_token,
    fetch_wham_snapshot,
    format_reset_credit_lines,
    merge_reset_credits,
    parse_usage_response,
    preserve_cached_reset_credits,
    read_codex_access_token,
    read_wham_snapshot,
    refresh_access_token,
    resolve_access_token,
    token_expires_soon,
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
            # Owner-only, so expiry is the only thing left to report.
            auth_path.chmod(0o600)

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


class WhamRefreshTests(unittest.TestCase):
    """The refresh has to survive a codex CLI running alongside it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.auth = root / "auth.json"
        self.auth.write_text(_auth_file(), encoding="utf-8")
        # The codex CLI writes this 0600 and the refresh declines anything
        # wider, so a fixture left at the test runner's umask would never
        # reach the code under test.
        self.auth.chmod(0o600)
        self.stamp = root / "stamp"
        env = patch.dict(
            os.environ,
            {"CODEX_AUTO_REFRESH": "true", "CODEX_AUTH_FILE": str(self.auth)},
            clear=True,
        )
        env.start()
        self.addCleanup(env.stop)

    def _stored(self):
        return json.loads(self.auth.read_text(encoding="utf-8"))

    def test_a_refresh_rotates_the_tokens_and_keeps_the_other_fields(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = dict(
                pair.split("=", 1)
                for pair in request.data.decode("utf-8").split("&")
            )
            return {
                "access_token": "at-new",
                "refresh_token": "rt-new",
                "id_token": "id-new",
            }

        token = refresh_access_token(
            self.auth, opener=opener, stamp_path=self.stamp, now=1_000.0
        )

        self.assertEqual(token, "at-new")
        self.assertEqual(captured["url"], TOKEN_ENDPOINT)
        self.assertEqual(captured["body"]["grant_type"], "refresh_token")
        self.assertEqual(captured["body"]["client_id"], CLIENT_ID)
        self.assertEqual(captured["body"]["refresh_token"], "rt-old")

        stored = self._stored()
        self.assertEqual(stored["tokens"]["access_token"], "at-new")
        self.assertEqual(stored["tokens"]["refresh_token"], "rt-new")
        self.assertEqual(stored["tokens"]["id_token"], "id-new")
        # account_id is what the reset-credit endpoint is addressed with, so
        # dropping it would cost the panel its expiry rows.
        self.assertEqual(stored["tokens"]["account_id"], "acct_123")
        self.assertEqual(stored["auth_mode"], "chatgpt")
        self.assertNotEqual(stored["last_refresh"], "2026-01-01T00:00:00Z")

    def test_a_response_without_a_new_refresh_token_keeps_the_old_one(self):
        token = refresh_access_token(
            self.auth,
            opener=lambda request, timeout: {"access_token": "at-new"},
            stamp_path=self.stamp,
            now=1_000.0,
        )

        self.assertEqual(token, "at-new")
        stored = self._stored()["tokens"]
        self.assertEqual(stored["refresh_token"], "rt-old")
        self.assertEqual(stored["id_token"], "id-old")

    def test_losing_the_race_leaves_the_cli_credential_alone(self):
        def opener(request, timeout):
            # The codex CLI refreshed while our request was in flight. Its pair
            # is the live one and ours is already dead.
            self.auth.write_text(
                _auth_file(refresh_token="rt-from-cli"), encoding="utf-8"
            )
            return {"access_token": "at-new", "refresh_token": "rt-new"}

        token = refresh_access_token(
            self.auth, opener=opener, stamp_path=self.stamp, now=1_000.0
        )

        self.assertIsNone(token)
        self.assertEqual(self._stored()["tokens"]["refresh_token"], "rt-from-cli")
        self.assertEqual(self._stored()["tokens"]["access_token"], "at-old")

    def test_a_failed_refresh_never_blanks_the_credential(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(TOKEN_ENDPOINT, 401, "no", {}, None)

        token = refresh_access_token(
            self.auth, opener=opener, stamp_path=self.stamp, now=1_000.0
        )

        self.assertIsNone(token)
        self.assertEqual(self._stored(), json.loads(_auth_file()))

    def test_the_credential_never_widens_past_owner_only(self):
        refresh_access_token(
            self.auth,
            opener=lambda request, timeout: {"access_token": "at-new"},
            stamp_path=self.stamp,
            now=1_000.0,
        )

        self.assertEqual(self.auth.stat().st_mode & 0o777, 0o600)
        siblings = [entry.name for entry in self.auth.parent.iterdir()]
        self.assertEqual([name for name in siblings if name.endswith(".tmp")], [])

    def test_the_flag_argument_beats_the_environment(self):
        with patch.dict(os.environ, {"CODEX_AUTO_REFRESH": "false"}):
            token = refresh_access_token(
                self.auth,
                opener=lambda request, timeout: {"access_token": "at-new"},
                stamp_path=self.stamp,
                now=1_000.0,
                enabled=True,
            )

        self.assertEqual(token, "at-new")

    def test_refreshing_is_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            token = refresh_access_token(
                self.auth,
                opener=_unreachable_opener,
                stamp_path=self.stamp,
                now=1_000.0,
            )

        self.assertIsNone(token)
        self.assertEqual(self._stored(), json.loads(_auth_file()))

    def test_a_recent_attempt_is_not_retried(self):
        self.stamp.write_text("900.0\n", encoding="utf-8")

        token = refresh_access_token(
            self.auth,
            opener=_unreachable_opener,
            stamp_path=self.stamp,
            now=1_000.0,
        )

        self.assertIsNone(token)

    def test_an_expired_token_is_replaced_through_resolve(self):
        self.auth.write_text(
            _auth_file(access_token=_jwt(exp=1_000)), encoding="utf-8"
        )

        with patch(
            "wham.refresh_access_token", return_value="at-new"
        ) as refresh:
            self.assertEqual(
                resolve_access_token(now=2_000, allow_refresh=True), "at-new"
            )

        self.assertEqual(refresh.call_args.kwargs["enabled"], True)

    def test_a_live_token_never_triggers_a_refresh(self):
        self.auth.write_text(
            _auth_file(access_token=_jwt(exp=9_000)), encoding="utf-8"
        )

        with patch("wham.refresh_access_token", side_effect=AssertionError) as refresh:
            self.assertIsNotNone(resolve_access_token(now=2_000, allow_refresh=True))

        refresh.assert_not_called()


class WhamAuthFilePermissionTests(unittest.TestCase):
    """A credential other accounts can read must not be handed a new token."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.auth = Path(self._tmp.name) / "auth.json"
        self.auth.write_text(_auth_file(), encoding="utf-8")
        self.auth.chmod(0o600)

    def test_an_owner_only_credential_is_not_flagged(self):
        self.assertIsNone(describe_exposed_auth_file(self.auth))

    def test_a_world_readable_credential_is_named_with_its_mode(self):
        self.auth.chmod(0o644)

        message = describe_exposed_auth_file(self.auth)

        self.assertIn("644", message)
        self.assertIn("chmod 600", message)
        # The message reaches a panel and a log, so it must carry no secret.
        self.assertNotIn("rt-old", message)

    def test_a_group_readable_credential_is_flagged_too(self):
        self.auth.chmod(0o640)

        self.assertIsNotNone(describe_exposed_auth_file(self.auth))

    def test_a_missing_credential_is_not_a_permission_problem(self):
        self.assertIsNone(
            describe_exposed_auth_file(Path(self._tmp.name) / "missing.json")
        )

    def test_an_exposed_credential_is_never_refreshed(self):
        self.auth.chmod(0o644)

        token = refresh_access_token(
            self.auth,
            opener=_unreachable_opener,
            stamp_path=Path(self._tmp.name) / "stamp",
            now=1_000.0,
            enabled=True,
        )

        self.assertIsNone(token)
        self.assertEqual(self._read(), json.loads(_auth_file()))

    def test_an_exposed_credential_explains_itself_before_expiry_does(self):
        self.auth.write_text(
            _auth_file(access_token=_jwt(exp=1_000)), encoding="utf-8"
        )
        self.auth.chmod(0o644)

        message = describe_missing_token(self.auth)

        self.assertIn("chmod 600", message)

    def test_reading_an_exposed_credential_still_works(self):
        # Blanking the panel over a mode this code never set would cost the
        # user their numbers for a problem they can still see reported.
        self.auth.chmod(0o644)

        self.assertEqual(read_codex_access_token(self.auth), "at-old")

    def _read(self):
        return json.loads(self.auth.read_text(encoding="utf-8"))


class WhamEarlyRenewalTests(unittest.TestCase):
    """A token that lapses mid-request costs a poll, so renew before it does."""

    def test_a_token_close_to_expiry_is_renewed_while_still_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            # Live for another 100s, which outlasts this call and may not
            # outlast the request it is about to authorise.
            auth_path.write_text(
                json.dumps({"tokens": {"access_token": _jwt(exp=2_100)}}),
                encoding="utf-8",
            )

            env = {"CODEX_AUTH_FILE": str(auth_path)}
            with patch.dict(os.environ, env, clear=True), patch(
                "wham.refresh_access_token", return_value="at-new"
            ):
                self.assertEqual(
                    resolve_access_token(now=2_000, allow_refresh=True), "at-new"
                )

    def test_a_failed_early_renewal_keeps_the_token_we_already_have(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            token = _jwt(exp=2_100)
            auth_path.write_text(
                json.dumps({"tokens": {"access_token": token}}), encoding="utf-8"
            )

            env = {"CODEX_AUTH_FILE": str(auth_path)}
            with patch.dict(os.environ, env, clear=True), patch(
                "wham.refresh_access_token", return_value=None
            ):
                # Downgrading a working token because the renewal failed would
                # turn a successful poll into a blank panel.
                self.assertEqual(
                    resolve_access_token(now=2_000, allow_refresh=True), token
                )

    def test_a_token_with_hours_left_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            token = _jwt(exp=9_000)
            auth_path.write_text(
                json.dumps({"tokens": {"access_token": token}}), encoding="utf-8"
            )

            env = {"CODEX_AUTH_FILE": str(auth_path)}
            with patch.dict(os.environ, env, clear=True), patch(
                "wham.refresh_access_token", side_effect=AssertionError
            ) as refresh:
                self.assertEqual(
                    resolve_access_token(now=2_000, allow_refresh=True), token
                )

            refresh.assert_not_called()

    def test_an_opaque_token_is_never_renewed_early(self):
        # Nothing proves its deadline, so renewing would be a guess.
        self.assertFalse(token_expires_soon("token-from-env", now=2_000))
        self.assertFalse(token_expires_soon(_jwt(), now=2_000))


class WhamUnauthorizedRetryTests(unittest.TestCase):
    """`exp` is not the whole story: a revoked token also answers 401."""

    def setUp(self):
        self.usage = {
            "account_id": "acct_123",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 10,
                    "limit_window_seconds": 604800,
                    "reset_at": "2026-07-01T08:00:00Z",
                }
            },
        }

    def test_a_rejected_token_is_refreshed_and_the_request_retried(self):
        calls = []

        def fetch(url, access_token, timeout, account_id=None):
            calls.append(access_token)
            if access_token == "at-old":
                raise urllib.error.HTTPError(url, 401, "no", {}, None)
            return self.usage

        with patch("wham._fetch_json", side_effect=fetch), patch(
            "wham.refresh_access_token", return_value="at-new"
        ):
            snapshot = fetch_wham_snapshot("at-old", allow_refresh=True)

        self.assertEqual(calls[:2], ["at-old", "at-new"])
        self.assertEqual(snapshot.weekly.used_percent, 10)

    def test_a_rejection_that_survives_the_refresh_is_reported(self):
        def fetch(url, access_token, timeout, account_id=None):
            raise urllib.error.HTTPError(url, 401, "no", {}, None)

        with patch("wham._fetch_json", side_effect=fetch), patch(
            "wham.refresh_access_token", return_value=None
        ):
            with self.assertRaises(urllib.error.HTTPError):
                fetch_wham_snapshot("at-old", allow_refresh=True)

    def test_the_same_token_back_from_a_refresh_is_not_retried(self):
        calls = []

        def fetch(url, access_token, timeout, account_id=None):
            calls.append(access_token)
            raise urllib.error.HTTPError(url, 403, "no", {}, None)

        with patch("wham._fetch_json", side_effect=fetch), patch(
            "wham.refresh_access_token", return_value="at-old"
        ):
            with self.assertRaises(urllib.error.HTTPError):
                fetch_wham_snapshot("at-old", allow_refresh=True)

        self.assertEqual(calls, ["at-old"])

    def test_a_failure_that_is_not_about_the_token_never_refreshes(self):
        def fetch(url, access_token, timeout, account_id=None):
            raise urllib.error.HTTPError(url, 500, "boom", {}, None)

        with patch("wham._fetch_json", side_effect=fetch), patch(
            "wham.refresh_access_token", side_effect=AssertionError
        ) as refresh:
            with self.assertRaises(urllib.error.HTTPError):
                fetch_wham_snapshot("at-old", allow_refresh=True)

        refresh.assert_not_called()


def _auth_file(
    refresh_token: str = "rt-old",
    access_token: str = "at-old",
) -> str:
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "id_token": "id-old",
                "account_id": "acct_123",
            },
            "last_refresh": "2026-01-01T00:00:00Z",
        },
        indent=2,
        sort_keys=True,
    )


def _unreachable_opener(request, timeout):
    raise AssertionError("the refresh endpoint must not be reached")


def _jwt(**claims: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


if __name__ == "__main__":
    unittest.main()
