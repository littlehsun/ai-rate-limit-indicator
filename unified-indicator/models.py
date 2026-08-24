from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class UsageWindow:
    id: str
    label: str
    # None means the backend stopped reporting this window rather than reporting
    # zero. Antigravity drops a window once its quota is spent, and a nought
    # there would read as "all of it still available" -- the opposite of true.
    used_percent: Optional[int]
    resets_at: Optional[int] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: str
    label: str
    updated_at: Optional[str]
    windows: tuple[UsageWindow, ...]
    status: str = "fresh"
    error: Optional[str] = None
    extras: tuple[str, ...] = ()

    @property
    def max_used_percent(self) -> int:
        return max((window.used_percent for window in self.windows), default=0)


def countdown(reset_ts: Optional[int], now: Optional[int] = None) -> str:
    if reset_ts is None:
        return "--"
    now = int(time.time()) if now is None else now
    seconds = reset_ts - now
    if seconds <= 0:
        return "soon"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def local_reset_time(reset_ts: Optional[int]) -> str:
    if reset_ts is None:
        return "--"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(reset_ts))


def local_updated_time(value: str, tz: Optional[tzinfo] = None) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value.replace("T", " ")[:16]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(tz)
    return parsed.strftime("%Y-%m-%d %H:%M")


def parse_timestamp(value: object) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(
            datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        )
    except ValueError:
        return None


def default_snapshot_cache() -> Path:
    return Path.home() / ".cache" / "rate-limit-indicator" / "snapshots.json"


def write_snapshot_cache(
    snapshots: tuple[ProviderSnapshot, ...],
    path: Optional[Path] = None,
) -> Path:
    destination = path or default_snapshot_cache()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    tmp_path = destination.with_name(f".{destination.name}.tmp")
    payload = {
        "providers": [
            {
                **asdict(snapshot),
                "windows": [asdict(window) for window in snapshot.windows],
                "extras": list(snapshot.extras),
            }
            for snapshot in snapshots
        ]
    }
    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tmp_path.chmod(0o600)
        os.replace(tmp_path, destination)
        destination.chmod(0o600)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    return destination
