#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


DEFAULT_ENDPOINT = "https://cloudcode-pa.googleapis.com"
API_VERSION = "v1internal"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
USER_AGENT = "gemini-rate-indicator/1.0"


@dataclass(frozen=True)
class QuotaWindow:
    tier: str
    label: str
    model_id: str
    used_percent: int
    remaining_fraction: float
    reset_at: Optional[str] = None
    remaining_amount: Optional[int] = None


@dataclass(frozen=True)
class GeminiQuotaSnapshot:
    updated_at: str
    windows: tuple[QuotaWindow, ...]

    @property
    def max_used_percent(self) -> int:
        return max((window.used_percent for window in self.windows), default=0)


def default_gemini_home() -> Path:
    return Path.home() / ".gemini"


def default_cache_path() -> Path:
    return Path.home() / ".cache" / "gemini-rate-indicator" / "quota.json"


def read_access_token(
    gemini_home: Optional[Path] = None,
    *,
    now_ms: Optional[int] = None,
) -> str:
    home = gemini_home or default_gemini_home()
    credentials_path = home / "oauth_creds.json"
    try:
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read Gemini CLI OAuth credentials from {credentials_path}") from exc

    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    access_token = credentials.get("access_token")
    expiry_date = _as_int(credentials.get("expiry_date"))
    if access_token and (expiry_date is None or expiry_date > now_ms + 60_000):
        return str(access_token)

    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Gemini CLI OAuth credentials are expired and have no refresh token")

    client_id = (
        os.environ.get("GEMINI_OAUTH_CLIENT_ID")
        or credentials.get("client_id")
        or _id_token_audience(credentials.get("id_token"))
    )
    client_secret = os.environ.get("GEMINI_OAUTH_CLIENT_SECRET") or credentials.get("client_secret")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Gemini OAuth refresh requires GEMINI_OAUTH_CLIENT_SECRET in "
            "~/.config/gemini-rate-indicator/oauth.env"
        )

    refreshed = _request_form(
        OAUTH_TOKEN_URL,
        {
            "client_id": str(client_id),
            "client_secret": str(client_secret),
            "refresh_token": str(refresh_token),
            "grant_type": "refresh_token",
        },
    )
    new_access_token = refreshed.get("access_token")
    if not new_access_token:
        raise RuntimeError("Gemini OAuth refresh response did not contain an access token")

    expires_in = _as_int(refreshed.get("expires_in")) or 3600
    credentials.update(refreshed)
    credentials["access_token"] = str(new_access_token)
    credentials["refresh_token"] = str(refresh_token)
    credentials["expiry_date"] = now_ms + expires_in * 1000
    _write_private_json(credentials_path, credentials)
    return str(new_access_token)


def fetch_quota_snapshot(
    token: str,
    *,
    endpoint: Optional[str] = None,
    project: Optional[str] = None,
) -> GeminiQuotaSnapshot:
    base = (endpoint or os.environ.get("GEMINI_CODE_ASSIST_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    metadata = {
        "ideType": "GEMINI_CLI",
        "platform": "LINUX_AMD64",
        "pluginType": "GEMINI",
    }
    project = project or os.environ.get("GEMINI_CLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")

    load_payload: dict[str, Any] = {
        "metadata": metadata,
        "mode": "HEALTH_CHECK",
    }
    if project:
        load_payload["cloudaicompanionProject"] = project

    eligibility = _request_json(f"{base}/{API_VERSION}:loadCodeAssist", load_payload, headers)
    project = project or _project_id(eligibility.get("cloudaicompanionProject"))
    if not project:
        raise RuntimeError("Gemini Code Assist did not return a quota project")

    payload = _request_json(
        f"{base}/{API_VERSION}:retrieveUserQuota",
        {"project": project},
        headers,
    )
    return parse_quota_payload(payload)


def parse_quota_payload(
    payload: Mapping[str, Any],
    *,
    updated_at: Optional[str] = None,
) -> GeminiQuotaSnapshot:
    grouped: dict[str, QuotaWindow] = {}
    buckets = payload.get("buckets")
    if not isinstance(buckets, list):
        buckets = []

    for bucket in buckets:
        if not isinstance(bucket, Mapping):
            continue
        model_id = str(bucket.get("modelId") or "").strip()
        remaining = _as_float(bucket.get("remainingFraction"))
        if not model_id or remaining is None:
            continue
        remaining = min(1.0, max(0.0, remaining))
        tier, label = _tier_for_model(model_id)
        window = QuotaWindow(
            tier=tier,
            label=label,
            model_id=model_id,
            used_percent=round((1.0 - remaining) * 100),
            remaining_fraction=remaining,
            reset_at=str(bucket["resetTime"]) if bucket.get("resetTime") else None,
            remaining_amount=_as_int(bucket.get("remainingAmount")),
        )
        current = grouped.get(tier)
        if current is None or window.remaining_fraction < current.remaining_fraction:
            grouped[tier] = window

    order = {"pro": 0, "flash": 1, "flash-lite": 2}
    windows = tuple(sorted(grouped.values(), key=lambda item: (order.get(item.tier, 99), item.label)))
    timestamp = updated_at or datetime.now(timezone.utc).isoformat()
    return GeminiQuotaSnapshot(updated_at=timestamp, windows=windows)


def display_windows(snapshot: GeminiQuotaSnapshot) -> tuple[QuotaWindow, ...]:
    preferred = [window for window in snapshot.windows if window.tier in {"pro", "flash"}]
    return tuple((preferred or list(snapshot.windows))[:2])


def format_indicator_label(snapshot: GeminiQuotaSnapshot, now: Optional[int] = None) -> str:
    windows = display_windows(snapshot)
    usage = "|".join(f"{window.used_percent}%" for window in windows) or "--"
    constrained = max(windows, key=lambda item: item.used_percent, default=None)
    reset = _countdown(constrained.reset_at if constrained else None, now)
    return f"{usage}  ⟳{reset}"


def format_menu_line(window: QuotaWindow, now: Optional[int] = None) -> str:
    icon = "✨" if window.tier == "pro" else "⚡"
    body = f"{icon} {window.label}: {window.used_percent}%"
    if not window.reset_at:
        return body
    return f"{body}  ⟳ {_format_local_minute(window.reset_at)} ({_countdown(window.reset_at, now)})"


def format_updated_at(updated_at: str) -> str:
    return _format_local_minute(updated_at)


def write_cache(snapshot: GeminiQuotaSnapshot, cache_path: Optional[Path] = None) -> Path:
    path = cache_path or default_cache_path()
    payload = {
        "updated_at": snapshot.updated_at,
        "windows": [asdict(window) for window in snapshot.windows],
    }
    _write_private_json(path, payload)
    return path


def read_cache(cache_path: Optional[Path] = None) -> Optional[GeminiQuotaSnapshot]:
    path = cache_path or default_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        windows = tuple(
            QuotaWindow(
                tier=str(item["tier"]),
                label=str(item["label"]),
                model_id=str(item["model_id"]),
                used_percent=int(item["used_percent"]),
                remaining_fraction=float(item["remaining_fraction"]),
                reset_at=str(item["reset_at"]) if item.get("reset_at") else None,
                remaining_amount=_as_int(item.get("remaining_amount")),
            )
            for item in payload.get("windows", [])
            if isinstance(item, Mapping)
        )
        return GeminiQuotaSnapshot(updated_at=str(payload["updated_at"]), windows=windows)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def poll_and_cache(
    *,
    gemini_home: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    endpoint: Optional[str] = None,
    project: Optional[str] = None,
) -> GeminiQuotaSnapshot:
    token = read_access_token(gemini_home)
    snapshot = fetch_quota_snapshot(token, endpoint=endpoint, project=project)
    write_cache(snapshot, cache_path)
    return snapshot


def _request_json(url: str, payload: Mapping[str, Any], headers: Mapping[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    return _open_json(request)


def _request_form(url: str, payload: Mapping[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
        method="POST",
    )
    return _open_json(request)


def _open_json(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Gemini quota API returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Gemini quota API request failed: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Gemini quota API returned a non-object response")
    return value


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp_path = path.with_name(f".{path.name}.tmp")
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.chmod(0o600)
        os.replace(tmp_path, path)
        path.chmod(0o600)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _tier_for_model(model_id: str) -> tuple[str, str]:
    lowered = model_id.lower()
    if "flash-lite" in lowered or "flash_lite" in lowered:
        return "flash-lite", "Flash Lite"
    if "flash" in lowered:
        return "flash", "Flash"
    if "pro" in lowered:
        return "pro", "Pro"
    return model_id, model_id


def _project_id(value: Any) -> Optional[str]:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        for key in ("id", "name", "projectId"):
            candidate = value.get(key)
            if candidate:
                return str(candidate)
    return None


def _id_token_audience(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    try:
        encoded = value.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded))
        audience = payload.get("aud")
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return str(audience) if audience else None


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _countdown(reset_at: Optional[str], now: Optional[int] = None) -> str:
    timestamp = _parse_timestamp(reset_at)
    if timestamp is None:
        return "--"
    now = int(time.time()) if now is None else now
    seconds = timestamp - now
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


def _format_local_minute(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value.replace("T", " ")[:16]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M")


def _snapshot_json(snapshot: GeminiQuotaSnapshot) -> str:
    return json.dumps(
        {
            "updated_at": snapshot.updated_at,
            "windows": [asdict(window) for window in snapshot.windows],
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll and display Gemini CLI quota")
    parser.add_argument("--once", action="store_true", help="poll the API once and update the cache")
    parser.add_argument("--cache-only", action="store_true", help="read the existing cache without polling")
    parser.add_argument("--json", action="store_true", help="print JSON")
    parser.add_argument("--cache", type=Path, default=None)
    args = parser.parse_args()

    try:
        snapshot = read_cache(args.cache) if args.cache_only else poll_and_cache(cache_path=args.cache)
        if snapshot is None:
            raise RuntimeError("no Gemini quota cache is available")
    except RuntimeError as exc:
        print(f"gemini-rate-indicator: {exc}", file=sys.stderr)
        return 1

    print(_snapshot_json(snapshot) if args.json else format_indicator_label(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
