#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Optional


FIVE_HOUR_MINUTES = 300
WEEKLY_MINUTES = 10080


@dataclass(frozen=True)
class RateWindow:
    used_percent: int
    window_minutes: int
    resets_at: int


@dataclass(frozen=True)
class CodexRateSnapshot:
    updated_at: str
    five_hour: Optional[RateWindow]
    weekly: Optional[RateWindow]
    plan_type: Optional[str]
    source_path: Optional[Path] = None
    source_kind: str = "local"
    account_id: Optional[str] = None
    reset_credits_available: Optional[int] = None
    reset_credit_expirations: tuple[str, ...] = ()

    @classmethod
    def from_rate_limits(
        cls,
        updated_at: str,
        rate_limits: dict[str, Any],
        source_path: Optional[Path] = None,
    ) -> "CodexRateSnapshot":
        windows = [
            _parse_window(rate_limits.get("primary")),
            _parse_window(rate_limits.get("secondary")),
        ]
        five_hour = next((w for w in windows if w and w.window_minutes == FIVE_HOUR_MINUTES), None)
        weekly = next((w for w in windows if w and w.window_minutes == WEEKLY_MINUTES), None)

        return cls(
            updated_at=updated_at,
            five_hour=five_hour,
            weekly=weekly,
            plan_type=rate_limits.get("plan_type"),
            source_path=source_path,
        )


def find_latest_snapshot(codex_home: Path) -> Optional[CodexRateSnapshot]:
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.exists():
        return None

    latest: Optional[CodexRateSnapshot] = None
    for rollout in sessions_dir.glob("**/rollout-*.jsonl"):
        snapshot = _latest_snapshot_in_file(rollout)
        if snapshot is None:
            continue
        if latest is None or snapshot.updated_at > latest.updated_at:
            latest = snapshot
    return latest


def format_indicator_label(
    snapshot: CodexRateSnapshot,
    now: Optional[int] = None,
    show_five_hour: bool = True,
) -> str:
    five_hour, weekly, reset = format_indicator_parts(
        snapshot,
        now=now,
        show_five_hour=show_five_hour,
    )
    usage = f"{five_hour}|{weekly}" if show_five_hour else weekly
    if snapshot.reset_credits_available is not None:
        return f"{usage} R{snapshot.reset_credits_available}  ⟳{reset}"
    return f"{usage}  ⟳{reset}"


def format_indicator_parts(
    snapshot: CodexRateSnapshot,
    now: Optional[int] = None,
    show_five_hour: bool = True,
) -> tuple[str, str, str]:
    now = int(time.time()) if now is None else now
    five_hour = f"{_pct(snapshot.five_hour)}%"
    weekly = f"{_pct(snapshot.weekly)}%"
    reset_window = snapshot.five_hour if show_five_hour else snapshot.weekly
    reset = _countdown(reset_window.resets_at, now) if reset_window else "--"
    return five_hour, weekly, reset


def format_menu_line(window: Optional[RateWindow], label: str, now: Optional[int] = None) -> str:
    now = int(time.time()) if now is None else now
    if window is None:
        return f"{label}: no data"
    reset_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(window.resets_at))
    return f"{label}: {window.used_percent}%  reset {reset_time} ({_countdown(window.resets_at, now)})"


def format_updated_at(updated_at: str, tz: Optional[tzinfo] = None) -> str:
    try:
        parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return updated_at.replace("T", " ")[:16]

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(tz)
    return parsed.strftime("%Y-%m-%d %H:%M")


def max_used_percent(snapshot: CodexRateSnapshot) -> int:
    return max(_pct(snapshot.five_hour), _pct(snapshot.weekly))


def default_codex_home() -> Path:
    return Path.home() / ".codex"


def _latest_snapshot_in_file(path: Path) -> Optional[CodexRateSnapshot]:
    latest: Optional[CodexRateSnapshot] = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = _loads_json(line)
                if row is None:
                    continue
                payload = row.get("payload") if row.get("type") == "event_msg" else None
                if not isinstance(payload, dict):
                    continue
                rate_limits = payload.get("rate_limits")
                if not isinstance(rate_limits, dict):
                    continue
                timestamp = row.get("timestamp")
                if not isinstance(timestamp, str) or not timestamp:
                    continue
                snapshot = CodexRateSnapshot.from_rate_limits(timestamp, rate_limits, source_path=path)
                if snapshot.five_hour is None and snapshot.weekly is None:
                    continue
                if latest is None or snapshot.updated_at > latest.updated_at:
                    latest = snapshot
    except OSError:
        return None
    return latest


def _parse_window(value: Any) -> Optional[RateWindow]:
    if not isinstance(value, dict):
        return None
    try:
        return RateWindow(
            used_percent=int(round(float(value["used_percent"]))),
            window_minutes=int(value["window_minutes"]),
            resets_at=int(value["resets_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _loads_json(line: str) -> Optional[dict[str, Any]]:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def _pct(window: Optional[RateWindow]) -> int:
    return window.used_percent if window else 0


def _countdown(reset_ts: int, now: int) -> str:
    seconds = reset_ts - now
    if seconds <= 0:
        return "soon"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def main() -> int:
    parser = argparse.ArgumentParser(description="Print Codex rate limits from local rollout JSONL files.")
    parser.add_argument("--codex-home", type=Path, default=default_codex_home())
    args = parser.parse_args()

    snapshot = find_latest_snapshot(args.codex_home)
    if snapshot is None:
        print("Codex --")
        return 1
    print(f"Codex {format_indicator_label(snapshot)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
