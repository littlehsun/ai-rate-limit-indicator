#!/usr/bin/env python3
"""Serve the cached usage snapshot to other devices on the Tailscale network.

The snapshot is whatever the menu bar or tray last wrote. Nothing here calls
the provider adapters: `load_snapshots()` fetches Claude live, and a phone
widget polling that would spend the usage endpoint's budget on top of what the
desktop UI already spends.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from models import default_snapshot_cache


DEFAULT_PORT = 8477
DEFAULT_ROUTE = "/usage.json"
TAILSCALE_TIMEOUT = 5.0
TAILSCALE_BINARIES = (
    "tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
)


def tailscale_address(runner: Optional[object] = None) -> Optional[str]:
    """Return this machine's Tailscale IPv4 address, or None when it has none.

    Binding to the Tailscale interface is what keeps the snapshot reachable
    from a phone without reaching anything else: the address does not exist
    until tailscaled is up, and nothing off the tailnet can route to it.
    """

    run = runner or _run_tailscale
    for binary in TAILSCALE_BINARIES:
        output = run(binary)
        if not output:
            continue
        for line in output.splitlines():
            address = line.strip()
            if address and ":" not in address:
                return address
    return None


def _run_tailscale(binary: str) -> Optional[str]:
    try:
        result = subprocess.run(
            (binary, "ip", "-4"),
            capture_output=True,
            text=True,
            timeout=TAILSCALE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


class SnapshotHandler(BaseHTTPRequestHandler):
    cache_path: Path = default_snapshot_cache()
    route: str = DEFAULT_ROUTE
    server_version = "RateLimitIndicator"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path.split("?", 1)[0] != self.route:
            self.send_error(404, "not found")
            return
        try:
            payload = type(self).cache_path.read_bytes()
        except OSError:
            # The cache only appears once the desktop UI has refreshed once,
            # so this is the normal state on a machine that just booted.
            self.send_error(503, "no usage snapshot yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        # A widget refreshing on its own schedule would otherwise write a line
        # into the launchd log forever, which is what we just finished capping.
        pass


def serve(
    address: str,
    port: int,
    cache_path: Optional[Path] = None,
    route: str = DEFAULT_ROUTE,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundSnapshotHandler",
        (SnapshotHandler,),
        {"cache_path": cache_path or default_snapshot_cache(), "route": route},
    )
    return ThreadingHTTPServer((address, port), handler)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Serve the cached usage snapshot on the Tailscale network"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MOBILE_PUBLISH_PORT", DEFAULT_PORT)),
    )
    parser.add_argument(
        "--bind",
        default=os.environ.get("MOBILE_PUBLISH_BIND", ""),
        help="Address to bind. Defaults to this machine's Tailscale IPv4.",
    )
    parser.add_argument("--cache", type=Path, default=None)
    args = parser.parse_args()

    address = args.bind.strip() or tailscale_address()
    if not address:
        # Falling back to a wildcard bind would put the snapshot on every
        # network this machine joins, which is not what opting in asked for.
        print(
            "no Tailscale address available; refusing to bind. "
            "Start Tailscale, or set MOBILE_PUBLISH_BIND deliberately.",
            file=sys.stderr,
        )
        return 2

    try:
        httpd = serve(address, args.port, cache_path=args.cache)
    except OSError as exc:
        print(f"cannot serve on {address}:{args.port}: {exc}", file=sys.stderr)
        return 1

    print(f"serving {DEFAULT_ROUTE} on http://{address}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
