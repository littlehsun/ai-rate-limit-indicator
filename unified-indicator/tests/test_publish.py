import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from publish import DEFAULT_ROUTE, serve, tailscale_address


class TailscaleAddressTests(unittest.TestCase):
    def test_reads_the_first_ipv4_address(self):
        address = tailscale_address(runner=lambda _binary: "100.64.0.1\nfd7a::1\n")

        self.assertEqual(address, "100.64.0.1")

    def test_skips_ipv6_only_output(self):
        self.assertIsNone(tailscale_address(runner=lambda _binary: "fd7a::1\n"))

    def test_falls_through_to_the_next_binary(self):
        seen = []

        def runner(binary):
            seen.append(binary)
            return "100.64.0.9\n" if binary.startswith("/") else None

        self.assertEqual(tailscale_address(runner=runner), "100.64.0.9")
        self.assertGreater(len(seen), 1)

    def test_returns_none_when_tailscale_is_not_running(self):
        self.assertIsNone(tailscale_address(runner=lambda _binary: None))


class ServeTests(unittest.TestCase):
    def _serve(self, cache_path):
        # Port 0 lets the OS pick, so the suite never collides with a real one.
        httpd = serve("127.0.0.1", 0, cache_path=cache_path)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        host, port = httpd.server_address[:2]
        return f"http://{host}:{port}"

    def test_serves_the_snapshot_and_reflects_a_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "snapshots.json"
            cache.write_text(json.dumps({"providers": [{"provider": "grok"}]}))
            base = self._serve(cache)

            with urllib.request.urlopen(f"{base}{DEFAULT_ROUTE}", timeout=5) as first:
                self.assertEqual(first.headers["Content-Type"], "application/json; charset=utf-8")
                self.assertEqual(first.headers["Cache-Control"], "no-store")
                served = json.loads(first.read())
            self.assertEqual(served["providers"][0]["provider"], "grok")

            # The desktop UI rewrites this file in place on every refresh, so a
            # snapshot cached at startup would go permanently stale.
            cache.write_text(json.dumps({"providers": [{"provider": "claude"}]}))
            with urllib.request.urlopen(f"{base}{DEFAULT_ROUTE}", timeout=5) as second:
                self.assertEqual(json.loads(second.read())["providers"][0]["provider"], "claude")

    def test_only_the_snapshot_route_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "snapshots.json"
            cache.write_text("{}")
            base = self._serve(cache)

            for route in ("/", "/../snapshots.json", "/etc/passwd", "/usage.json.bak"):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(f"{base}{route}", timeout=5)
                self.assertEqual(caught.exception.code, 404, route)

    def test_a_query_string_still_reaches_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "snapshots.json"
            cache.write_text('{"providers": []}')
            base = self._serve(cache)

            with urllib.request.urlopen(f"{base}{DEFAULT_ROUTE}?t=1", timeout=5) as response:
                self.assertEqual(response.status, 200)

    def test_a_missing_cache_reports_unavailable_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._serve(Path(tmp) / "absent.json")

            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}{DEFAULT_ROUTE}", timeout=5)

        self.assertEqual(caught.exception.code, 503)

    def test_writes_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "snapshots.json"
            cache.write_text("{}")
            base = self._serve(cache)

            request = urllib.request.Request(
                f"{base}{DEFAULT_ROUTE}", data=b"{}", method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=5)

        self.assertEqual(caught.exception.code, 501)


class BindRefusalTests(unittest.TestCase):
    def test_main_refuses_to_bind_without_a_tailscale_address(self):
        import publish

        with mock.patch.object(publish, "tailscale_address", return_value=None), mock.patch(
            "sys.argv", ["publish.py"]
        ), mock.patch.dict("os.environ", {"MOBILE_PUBLISH_BIND": ""}), mock.patch(
            "publish.serve", side_effect=AssertionError("must not bind")
        ):
            self.assertEqual(publish.main(), 2)


if __name__ == "__main__":
    unittest.main()
