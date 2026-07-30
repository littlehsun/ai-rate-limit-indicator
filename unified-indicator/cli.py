#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from adapters import PROVIDER_ORDER, load_snapshots
from models import countdown, local_reset_time, write_snapshot_cache


def snapshot_payload(snapshots):
    return {
        "providers": [
            {
                **asdict(snapshot),
                "windows": [asdict(window) for window in snapshot.windows],
                "extras": list(snapshot.extras),
            }
            for snapshot in snapshots
        ]
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read normalized AI provider usage")
    parser.add_argument("--provider", choices=PROVIDER_ORDER)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    all_snapshots = load_snapshots()
    snapshots = tuple(
        snapshot
        for snapshot in all_snapshots
        if args.provider is None or snapshot.provider == args.provider
    )
    write_snapshot_cache(all_snapshots)
    if args.json:
        print(json.dumps(snapshot_payload(snapshots), ensure_ascii=False, indent=2))
        return 0

    for snapshot in snapshots:
        print(f"{snapshot.label} [{snapshot.status}]")
        if snapshot.error:
            print(f"  Error: {snapshot.error}")
        for window in snapshot.windows:
            detail = f"  {window.detail}" if window.detail else ""
            reset = (
                f"  ⟳ {local_reset_time(window.resets_at)} "
                f"({countdown(window.resets_at)})"
                if window.resets_at
                else ""
            )
            print(f"  {window.label}: {window.used_percent}%{detail}{reset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
