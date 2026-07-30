from __future__ import annotations

import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


QUOTA_PATH = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"


@dataclass(frozen=True)
class AgyQuotaWindow:
    group_id: str
    group_label: str
    cadence: str
    used_percent: int
    remaining_fraction: float
    reset_at: Optional[str]


@dataclass(frozen=True)
class AgyQuotaSnapshot:
    updated_at: str
    windows: tuple[AgyQuotaWindow, ...]


def default_cache_path() -> Path:
    return Path(
        os.environ.get(
            "AGY_RATE_CACHE",
            Path.home() / ".cache" / "rate-limit-indicator" / "agy-quota.json",
        )
    )


def write_cache(
    snapshot: AgyQuotaSnapshot,
    cache_path: Optional[Path] = None,
) -> Path:
    path = cache_path or default_cache_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    payload = {
        "updated_at": snapshot.updated_at,
        "windows": [asdict(window) for window in snapshot.windows],
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def read_cache(cache_path: Optional[Path] = None) -> Optional[AgyQuotaSnapshot]:
    path = cache_path or default_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        windows = tuple(
            AgyQuotaWindow(
                group_id=str(item["group_id"]),
                group_label=str(item["group_label"]),
                cadence=str(item["cadence"]),
                used_percent=int(item["used_percent"]),
                remaining_fraction=float(item["remaining_fraction"]),
                reset_at=str(item["reset_at"]) if item.get("reset_at") else None,
            )
            for item in payload.get("windows", [])
            if isinstance(item, Mapping)
        )
        return AgyQuotaSnapshot(
            updated_at=str(payload["updated_at"]),
            windows=windows,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def find_agy_ports() -> tuple[int, ...]:
    try:
        processes = subprocess.run(
            ["pgrep", "-x", "agy"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot find the AGY process: {exc}") from exc

    pids = [
        line.strip() for line in processes.stdout.splitlines() if line.strip().isdigit()
    ]
    if not pids:
        raise RuntimeError("AGY is not running")

    ports: list[int] = []
    for pid in pids:
        try:
            result = subprocess.run(
                [
                    "lsof",
                    "-nP",
                    "-a",
                    "-p",
                    pid,
                    "-iTCP",
                    "-sTCP:LISTEN",
                    "-Fn",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"cannot inspect AGY listening ports: {exc}") from exc
        for line in result.stdout.splitlines():
            if not line.startswith("n"):
                continue
            address = line[1:]
            host, separator, port_text = address.rpartition(":")
            if (
                separator
                and host in {"127.0.0.1", "localhost"}
                and port_text.isdigit()
                and int(port_text) not in ports
            ):
                ports.append(int(port_text))
    if not ports:
        raise RuntimeError("AGY is running but has no localhost listening port")
    return tuple(ports)


def fetch_quota_snapshot(
    ports: Optional[tuple[int, ...]] = None,
    *,
    timeout: float = 3.0,
) -> AgyQuotaSnapshot:
    candidates = ports or find_agy_ports()
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    payload = json.dumps({"forceRefresh": True}).encode("utf-8")
    last_error: Optional[Exception] = None

    for port in candidates:
        request = urllib.request.Request(
            f"https://127.0.0.1:{port}{QUOTA_PATH}",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=context
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
            snapshot = parse_quota_payload(body)
            if snapshot.windows:
                return snapshot
        except (
            OSError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.URLError,
        ) as exc:
            last_error = exc

    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"AGY quota endpoint is unavailable{detail}")


def parse_quota_payload(
    payload: Mapping[str, Any],
    *,
    updated_at: Optional[str] = None,
) -> AgyQuotaSnapshot:
    response = payload.get("response")
    if not isinstance(response, Mapping):
        response = payload
    groups = response.get("groups")
    if not isinstance(groups, list):
        groups = []

    windows: list[tuple[int, int, AgyQuotaWindow]] = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        group_name = str(group.get("displayName") or "").strip()
        group_id, group_label, group_rank = _classify_group(group_name)
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, Mapping) or bucket.get("disabled") is True:
                continue
            remaining = _as_float(bucket.get("remainingFraction"))
            if remaining is None:
                continue
            cadence, cadence_rank = _classify_cadence(
                str(bucket.get("bucketId") or ""),
                str(bucket.get("displayName") or ""),
            )
            if cadence is None:
                continue
            remaining = min(1.0, max(0.0, remaining))
            windows.append(
                (
                    group_rank,
                    cadence_rank,
                    AgyQuotaWindow(
                        group_id=group_id,
                        group_label=group_label,
                        cadence=cadence,
                        used_percent=round((1.0 - remaining) * 100),
                        remaining_fraction=remaining,
                        reset_at=(
                            str(bucket["resetTime"])
                            if bucket.get("resetTime")
                            else None
                        ),
                    ),
                )
            )

    ordered = tuple(item[2] for item in sorted(windows, key=lambda item: item[:2]))
    return AgyQuotaSnapshot(
        updated_at=updated_at or datetime.now(timezone.utc).isoformat(),
        windows=ordered,
    )


def _classify_group(name: str) -> tuple[str, str, int]:
    lowered = name.lower()
    if "gemini" in lowered:
        return "gemini", "Gemini", 0
    if "claude" in lowered or "gpt" in lowered:
        return "claude-gpt", "Claude/GPT", 1
    return _slug(name) or "quota", name or "Quota", 2


def _classify_cadence(bucket_id: str, display_name: str) -> tuple[Optional[str], int]:
    value = f"{bucket_id} {display_name}".lower().replace("_", "-")
    if (
        "5h" in value
        or "five hour" in value
        or "five-hour" in value
        or "session" in value
    ):
        return "5h", 0
    if "weekly" in value or "7d" in value:
        return "7d", 1
    return None, 2


def _slug(value: str) -> str:
    return "-".join(value.lower().split())


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
