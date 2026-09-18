import io
import json
import os
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from agy_rate import (
    ADDRESS_VARIABLE,
    CLI_START_COOLDOWN,
    CLI_USAGE_TIMEOUT,
    parse_usage_command_payload,
    credential_attempts,
    find_agy_pids,
    find_all_agy_credentials,
    forget_credentials,
    recall_credentials,
    remember_credentials,
    CSRF_HEADER,
    TOKEN_VARIABLE,
    AgyCredentials,
    AgyQuotaSnapshot,
    AgyQuotaWindow,
    describe_error,
    find_agy_credentials,
    quota_headers,
    quota_ports,
    fetch_quota_snapshot,
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


def _bucket(bucket_id, name, remaining, reset):
    return {
        "id": bucket_id,
        "name": name,
        "remaining_fraction": remaining,
        "reset_time": reset,
    }


# The shape `agy -p /usage --output-format json` actually returns.
FULL_USAGE_PAYLOAD = {
    "status": "SUCCESS",
    "usage": {"total_tokens": 0},
    "command": {
        "name": "usage",
        "data": {
            "groups": [
                {
                    "name": "Gemini Models",
                    "buckets": [
                        _bucket("gemini-5h", "Five Hour Limit Remaining", 0.8954, "2026-09-18T11:00:36Z"),
                        _bucket("gemini-weekly", "Weekly Limit Remaining", 0.1829, "2026-09-23T03:54:44Z"),
                    ],
                },
                {
                    "name": "Claude and GPT models",
                    "buckets": [
                        _bucket("3p-5h", "Five Hour Limit Remaining", 0.6964, "2026-09-18T11:01:14Z"),
                        _bucket("3p-weekly", "Weekly Limit Remaining", 0.8988, "2026-09-25T06:01:14Z"),
                    ],
                },
            ]
        },
    },
}

USAGE_COMMAND_PAYLOAD = {
    "command": {
        "data": {
            "groups": [
                {
                    "name": "Gemini Models",
                    "buckets": [
                        _bucket("gemini-5h", "Five Hour Limit Remaining", 0.8954, "2026-09-18T11:00:36Z"),
                        _bucket("gemini-weekly", "Weekly Limit Remaining", 0.1829, "2026-09-23T03:54:44Z"),
                    ],
                }
            ]
        }
    }
}


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

    def test_asking_the_cli_stays_opt_in(self):
        runs = []
        with mock.patch.dict(os.environ, {"AGY_AUTO_START": ""}, clear=False):
            self.assertIsNone(
                fetch_quota_with_cli(runner=lambda *args: runs.append(args) or "{}")
            )
        self.assertEqual(runs, [])

    def test_the_usage_command_is_what_gets_run(self):
        runs = []

        def runner(agy_bin, timeout):
            runs.append((agy_bin, timeout))
            return json.dumps(USAGE_COMMAND_PAYLOAD)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AGY_AUTO_START": "1"}
        ), mock.patch("agy_rate.find_agy_cli", return_value="/usr/bin/agy"):
            snapshot = fetch_quota_with_cli(
                runner=runner, stamp_path=Path(tmp) / "stamp"
            )

        self.assertEqual(runs, [("/usr/bin/agy", CLI_USAGE_TIMEOUT)])
        self.assertEqual(
            [(w.group_id, w.cadence, w.used_percent) for w in snapshot.windows],
            [("gemini", "5h", 10), ("gemini", "7d", 82)],
        )

    def test_a_command_that_fails_keeps_the_cache_rather_than_raising(self):
        for failure in (
            OSError("no such binary"),
            RuntimeError("agy /usage exited 1"),
            ValueError("not json"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                    os.environ, {"AGY_AUTO_START": "1"}
                ), mock.patch("agy_rate.find_agy_cli", return_value="/usr/bin/agy"):
                    self.assertIsNone(
                        fetch_quota_with_cli(
                            runner=mock.Mock(side_effect=failure),
                            stamp_path=Path(tmp) / "stamp",
                        )
                    )

    def test_output_that_is_not_the_usage_screen_is_not_a_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AGY_AUTO_START": "1"}
        ), mock.patch("agy_rate.find_agy_cli", return_value="/usr/bin/agy"):
            self.assertIsNone(
                fetch_quota_with_cli(
                    runner=lambda *_args: json.dumps({"status": "SUCCESS"}),
                    stamp_path=Path(tmp) / "stamp",
                )
            )

    def test_asking_the_cli_waits_out_the_cooldown(self):
        # A CLI that cannot sign in must not earn a process on every poll.
        runs = []

        def runner(agy_bin, timeout):
            runs.append(agy_bin)
            raise RuntimeError("agy /usage exited 1")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"AGY_AUTO_START": "1"}
        ), mock.patch("agy_rate.find_agy_cli", return_value="/usr/bin/agy"):
            stamp = Path(tmp) / "stamp"
            fetch_quota_with_cli(runner=runner, stamp_path=stamp, now=1_000.0)
            self.assertEqual(len(runs), 1)
            fetch_quota_with_cli(runner=runner, stamp_path=stamp, now=1_060.0)
            self.assertEqual(len(runs), 1, "still inside the cooldown")
            fetch_quota_with_cli(
                runner=runner, stamp_path=stamp, now=1_000.0 + CLI_START_COOLDOWN + 1
            )
            self.assertEqual(len(runs), 2)

    def test_the_usage_payload_carries_both_groups(self):
        snapshot = parse_usage_command_payload(FULL_USAGE_PAYLOAD)
        self.assertEqual(
            [(w.group_id, w.cadence, w.used_percent) for w in snapshot.windows],
            [
                ("gemini", "5h", 10),
                ("gemini", "7d", 82),
                ("claude-gpt", "5h", 30),
                ("claude-gpt", "7d", 10),
            ],
        )

    def test_a_payload_without_groups_is_refused(self):
        with self.assertRaises(RuntimeError):
            parse_usage_command_payload({"status": "SUCCESS"})
        with self.assertRaises(RuntimeError):
            parse_usage_command_payload({"command": {"data": {}}})

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


class QuotaFetchErrorTests(unittest.TestCase):
    """What the tray says when AGY will not answer.

    The message ends up on a panel and in a floating widget, so it has to be
    the one that tells the reader what happened -- not whichever attempt
    happened to fail last.
    """

    def setUp(self):
        # A machine running AGY has a real token in a real process, and it
        # reorders the ports under these tests. What is being tested here is
        # the reporting, not the discovery.
        patcher = mock.patch("agy_rate.find_all_agy_credentials", return_value=())
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _http_error(code, body):
        return urllib.error.HTTPError(
            "https://127.0.0.1:1/x",
            code,
            "Unauthorized",
            {},
            io.BytesIO(json.dumps(body).encode("utf-8")),
        )

    def test_a_refusal_beats_the_wrong_scheme_on_another_port(self):
        # AGY listens on two ports and speaks TLS on only one of them, so
        # every run produces a "wrong version number" somewhere. It must not
        # be what the user reads.
        refusal = self._http_error(401, {"code": "unauthenticated", "message": "missing CSRF token"})
        wrong_scheme = urllib.error.URLError(
            ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number")
        )
        attempts = iter([wrong_scheme, refusal, wrong_scheme, wrong_scheme])

        def opener(*_args, **_kwargs):
            raise next(attempts)

        # With a token in hand a 401 is a real refusal rather than the
        # "no token yet" case, so the status survives into the message.
        with mock.patch("agy_rate.urllib.request.urlopen", side_effect=opener):
            with self.assertRaises(RuntimeError) as raised:
                fetch_quota_snapshot(
                    ports=(43105, 44587), credentials=AgyCredentials(token="held")
                )
        self.assertIn("401", str(raised.exception))
        self.assertIn("missing CSRF token", str(raised.exception))
        self.assertNotIn("WRONG_VERSION_NUMBER", str(raised.exception))

    def test_a_connection_refused_beats_the_wrong_scheme(self):
        refused = urllib.error.URLError(ConnectionRefusedError("refused"))
        wrong_scheme = urllib.error.URLError(
            ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number")
        )
        attempts = iter([wrong_scheme, refused])

        def opener(*_args, **_kwargs):
            raise next(attempts)

        with mock.patch("agy_rate.urllib.request.urlopen", side_effect=opener):
            with self.assertRaises(RuntimeError) as raised:
                fetch_quota_snapshot(ports=(44587,))
        self.assertIn("refused", str(raised.exception))

    def test_both_schemes_are_tried_on_every_port(self):
        calls = []

        def opener(request, *_args, **_kwargs):
            calls.append(request.full_url)
            raise urllib.error.URLError("nope")

        with mock.patch("agy_rate.urllib.request.urlopen", side_effect=opener):
            with self.assertRaises(RuntimeError):
                fetch_quota_snapshot(ports=(1234, 5678))
        self.assertEqual(
            [url.split("/exa")[0] for url in calls],
            [
                "https://127.0.0.1:1234",
                "http://127.0.0.1:1234",
                "https://127.0.0.1:5678",
                "http://127.0.0.1:5678",
            ],
        )

    def test_a_plaintext_port_still_answers(self):
        payload = {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "gemini-5h",
                                "displayName": "Five Hour Limit",
                                "remainingFraction": 0.5,
                                "resetTime": "2026-09-17T10:00:00Z",
                            }
                        ],
                    }
                ]
            }
        }

        class Response:
            def read(self):
                return json.dumps(payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def opener(request, *_args, **_kwargs):
            if request.full_url.startswith("https"):
                raise urllib.error.URLError(
                    ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number")
                )
            return Response()

        with mock.patch("agy_rate.urllib.request.urlopen", side_effect=opener):
            snapshot = fetch_quota_snapshot(ports=(44587,))
        self.assertTrue(snapshot.windows)

    def test_the_server_s_own_words_are_what_is_reported(self):
        error = self._http_error(401, {"code": "unauthenticated", "message": "missing CSRF token"})
        self.assertEqual(describe_error(error), "HTTP 401 missing CSRF token")

    def test_a_refusal_with_no_body_still_names_its_status(self):
        error = urllib.error.HTTPError("https://127.0.0.1:1/x", 503, "Nope", {}, io.BytesIO(b""))
        self.assertEqual(describe_error(error), "HTTP 503")


def fake_proc(root: Path, pid: int, variables: dict) -> Path:
    entry = root / str(pid)
    entry.mkdir()
    payload = b"\0".join(
        f"{name}={value}".encode("utf-8") for name, value in variables.items()
    )
    (entry / "environ").write_bytes(payload + b"\0")
    return entry


class CredentialTests(unittest.TestCase):
    """Borrowing the CSRF token from a process AGY started.

    AGY mints the token per run, never writes it down, and will not accept one
    from outside, so the environment of its own children is the only supply.
    """

    def setUp(self):
        for name in ("AGY_CSRF_TOKEN", "AGY_LS_ADDRESS"):
            self.addCleanup(os.environ.pop, name, None)
            os.environ.pop(name, None)

    def test_the_token_is_read_from_a_process_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_proc(root, 10, {"PATH": "/usr/bin"})
            fake_proc(
                root,
                20,
                {TOKEN_VARIABLE: "abc-123", ADDRESS_VARIABLE: "localhost:44563"},
            )
            credentials = find_agy_credentials(root)
        self.assertEqual(credentials.token, "abc-123")
        self.assertEqual(credentials.port, 44563)

    def test_no_such_process_is_no_credentials_rather_than_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_proc(root, 10, {"PATH": "/usr/bin"})
            self.assertIsNone(find_agy_credentials(root))

    def test_a_machine_without_proc_gets_no_credentials(self):
        # macOS reads the same adapter and has no /proc. It keeps its cache
        # rather than raising.
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(find_agy_credentials(Path(directory) / "absent"))

    def test_an_empty_token_is_not_a_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_proc(root, 10, {TOKEN_VARIABLE: "   "})
            self.assertIsNone(find_agy_credentials(root))

    def test_the_environment_can_supply_one_by_hand(self):
        os.environ["AGY_CSRF_TOKEN"] = "manual"
        os.environ["AGY_LS_ADDRESS"] = "localhost:1234"
        with tempfile.TemporaryDirectory() as directory:
            credentials = find_agy_credentials(Path(directory))
        self.assertEqual(credentials.token, "manual")
        self.assertEqual(credentials.port, 1234)

    def test_an_address_without_a_port_is_no_port(self):
        self.assertIsNone(AgyCredentials(token="x", address="localhost").port)
        self.assertIsNone(AgyCredentials(token="x").port)

    def test_the_token_port_is_tried_first(self):
        credentials = AgyCredentials(token="x", address="localhost:44563")
        self.assertEqual(
            quota_ports((33567, 44563), credentials), (44563, 33567)
        )

    def test_without_credentials_the_discovered_order_stands(self):
        self.assertEqual(quota_ports((33567, 44563), None), (33567, 44563))

    def test_the_headers_carry_the_token_and_the_origin_pair(self):
        headers = quota_headers(
            AgyCredentials(token="abc", address="localhost:1"), "http://127.0.0.1:1"
        )
        self.assertEqual(headers[CSRF_HEADER], "abc")
        self.assertEqual(headers["Origin"], "http://127.0.0.1:1")
        self.assertEqual(headers["Referer"], "http://127.0.0.1:1/")

    def test_no_credentials_sends_no_csrf_header(self):
        self.assertNotIn(CSRF_HEADER, quota_headers(None, "http://127.0.0.1:1"))

    def test_a_refusal_with_no_token_to_offer_says_what_is_missing(self):
        refusal = urllib.error.HTTPError(
            "http://127.0.0.1:1/x",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"code":"unauthenticated","message":"missing CSRF token"}'),
        )
        with mock.patch("agy_rate.urllib.request.urlopen", side_effect=refusal):
            with mock.patch("agy_rate.find_all_agy_credentials", return_value=()):
                with self.assertRaises(RuntimeError) as raised:
                    fetch_quota_snapshot(ports=(44563,))
        self.assertIn("CSRF token", str(raised.exception))
        self.assertIn("none is available", str(raised.exception))


class StaleCredentialTests(unittest.TestCase):
    """A token outlives the AGY run that minted it.

    AGY hands the token to everything it spawns, and a shell or an ssh session
    started from AGY can sit there for days holding a token nothing will
    accept again. Reaching for the first one found means offering a restarted
    AGY a token from the run before it.
    """

    def setUp(self):
        for name in ("AGY_CSRF_TOKEN", "AGY_LS_ADDRESS"):
            self.addCleanup(os.environ.pop, name, None)
            os.environ.pop(name, None)

    def test_the_newest_process_is_offered_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_proc(root, 100, {TOKEN_VARIABLE: "yesterday", ADDRESS_VARIABLE: "localhost:44563"})
            fake_proc(root, 900, {TOKEN_VARIABLE: "today", ADDRESS_VARIABLE: "localhost:33567"})
            found = find_all_agy_credentials(root)
        self.assertEqual([c.token for c in found], ["today", "yesterday"])

    def test_one_run_s_many_processes_are_one_token(self):
        # AGY gives every process it starts the same token; trying it once per
        # process would be the same refused request several times over.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid in (100, 200, 300):
                fake_proc(root, pid, {TOKEN_VARIABLE: "same", ADDRESS_VARIABLE: "localhost:1"})
            self.assertEqual(len(find_all_agy_credentials(root)), 1)

    def test_a_token_whose_port_is_live_beats_a_newer_one_that_is_not(self):
        # The strongest evidence a token is current is that AGY is listening
        # on the port it names.
        newer = AgyCredentials(token="newer", address="localhost:99999")
        matching = AgyCredentials(token="matching", address="localhost:44563")
        attempts = credential_attempts((44563,), (newer, matching))
        self.assertEqual(attempts[0], (44563, matching))

    def test_every_token_is_still_tried_on_every_port(self):
        first = AgyCredentials(token="a", address="localhost:1")
        second = AgyCredentials(token="b")
        attempts = credential_attempts((7, 8), (first, second))
        self.assertEqual(
            set(attempts), {(7, first), (7, second), (8, first), (8, second)}
        )

    def test_no_tokens_still_asks_every_port(self):
        self.assertEqual(credential_attempts((7, 8), ()), ((7, None), (8, None)))

    def test_every_token_refused_says_they_are_old_rather_than_HTTP_401(self):
        refusal = urllib.error.HTTPError(
            "http://127.0.0.1:1/x", 401, "Unauthorized", {},
            io.BytesIO(b'{"code":"unauthenticated","message":"invalid CSRF token"}'),
        )
        held = (AgyCredentials(token="stale", address="localhost:44563"),)
        with mock.patch("agy_rate.urllib.request.urlopen", side_effect=refusal):
            with mock.patch("agy_rate.find_all_agy_credentials", return_value=held):
                with self.assertRaises(RuntimeError) as raised:
                    fetch_quota_snapshot(ports=(44563,))
        message = str(raised.exception)
        self.assertIn("earlier runs", message)
        self.assertNotIn("401", message)


class RememberedTokenTests(unittest.TestCase):
    """Holding a token for as long as the AGY run that minted it lasts.

    The token lives in the environment of the processes AGY handed it to, and
    those exit. Without this, Gemini goes dark the moment the last one does,
    with Antigravity still open in front of the user.
    """

    def setUp(self):
        forget_credentials()
        self.addCleanup(forget_credentials)

    def test_nothing_is_remembered_to_begin_with(self):
        self.assertIsNone(recall_credentials((123,)))

    def test_a_token_survives_the_process_that_carried_it(self):
        credentials = AgyCredentials(token="live", address="localhost:44563")
        remember_credentials(credentials, (4242,))
        # The ssh session that held it is gone; AGY itself is not.
        self.assertEqual(recall_credentials((4242,)), credentials)

    def test_a_restarted_agy_does_not_get_the_old_token(self):
        remember_credentials(AgyCredentials(token="old"), (4242,))
        self.assertIsNone(recall_credentials((5555,)))

    def test_agy_closing_takes_the_token_with_it(self):
        remember_credentials(AgyCredentials(token="old"), (4242,))
        self.assertIsNone(recall_credentials(()))

    def test_a_working_token_is_remembered(self):
        payload = {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini Models",
                        "buckets": [
                            {
                                "bucketId": "gemini-5h",
                                "displayName": "Five Hour Limit",
                                "remainingFraction": 0.5,
                            }
                        ],
                    }
                ]
            }
        }

        class Response:
            def read(self):
                return json.dumps(payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        held = (AgyCredentials(token="live", address="localhost:44563"),)
        with mock.patch("agy_rate.find_agy_pids", return_value=(4242,)):
            with mock.patch("agy_rate.find_all_agy_credentials", return_value=held):
                with mock.patch("agy_rate.urllib.request.urlopen", return_value=Response()):
                    fetch_quota_snapshot(ports=(44563,))
        self.assertEqual(recall_credentials((4242,)).token, "live")

    def test_a_remembered_token_is_offered_when_no_process_has_one(self):
        remember_credentials(AgyCredentials(token="live", address="localhost:44563"), (4242,))
        sent = []

        def opener(request, *_args, **_kwargs):
            sent.append(request.headers)
            raise urllib.error.URLError("nope")

        with mock.patch("agy_rate.find_agy_pids", return_value=(4242,)):
            with mock.patch("agy_rate.find_all_agy_credentials", return_value=()):
                with mock.patch("agy_rate.urllib.request.urlopen", side_effect=opener):
                    with self.assertRaises(RuntimeError):
                        fetch_quota_snapshot(ports=(44563,))
        # urllib stores header names capitalised, so compare in one case.
        tokens_sent = [
            value
            for headers in sent
            for name, value in headers.items()
            if name.lower() == CSRF_HEADER
        ]
        self.assertEqual(set(tokens_sent), {"live"})

    def test_a_refused_token_is_not_offered_again(self):
        remember_credentials(AgyCredentials(token="stale", address="localhost:44563"), (4242,))
        refusal = urllib.error.HTTPError(
            "http://127.0.0.1:1/x", 401, "Unauthorized", {},
            io.BytesIO(b'{"code":"unauthenticated","message":"invalid CSRF token"}'),
        )
        with mock.patch("agy_rate.find_agy_pids", return_value=(4242,)):
            with mock.patch("agy_rate.find_all_agy_credentials", return_value=()):
                with mock.patch("agy_rate.urllib.request.urlopen", side_effect=refusal):
                    with self.assertRaises(RuntimeError):
                        fetch_quota_snapshot(ports=(44563,))
        self.assertIsNone(recall_credentials((4242,)))

    def test_a_live_process_token_is_not_displaced_by_the_remembered_one(self):
        remembered = AgyCredentials(token="remembered", address="localhost:44563")
        remember_credentials(remembered, (4242,))
        live = AgyCredentials(token="live", address="localhost:44563")
        attempts = credential_attempts((44563,), (live, remembered))
        self.assertEqual(attempts[0][1], live)
