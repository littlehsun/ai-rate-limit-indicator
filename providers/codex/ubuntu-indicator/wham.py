#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from codex_rate import FIVE_HOUR_MINUTES, WEEKLY_MINUTES, CodexRateSnapshot, RateWindow, format_indicator_label


USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
TOKEN_EXPIRY_LEEWAY_SECONDS = 60


def default_wham_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    cache_home = Path(base) if base else Path.home() / ".cache"
    return cache_home / "codex-rate-indicator" / "wham.json"


def default_codex_auth_path() -> Path:
    return Path(os.environ.get("CODEX_AUTH_FILE", Path.home() / ".codex" / "auth.json"))


def resolve_access_token(*, now: Optional[int] = None) -> Optional[str]:
    for candidate in (
        _optional_str(os.environ.get("CHATGPT_ACCESS_TOKEN")),
        _optional_str(os.environ.get("CHATGPT_BEARER_TOKEN")),
        read_codex_access_token(default_codex_auth_path()),
    ):
        # An expired token only earns a 401 on every poll. The codex CLI owns
        # refreshing it, so skip it and let the caller say so.
        if candidate is not None and not token_is_expired(candidate, now=now):
            return candidate
    return None


def read_codex_access_token(path: Path) -> Optional[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    return _optional_str(tokens.get("access_token"))


def token_is_expired(token: str, *, now: Optional[int] = None) -> bool:
    """Report expiry only when the token itself proves it.

    Codex issues a JWT whose `exp` claim carries the deadline, so no extra
    request is needed. Opaque tokens and JWTs without a readable `exp` stay
    usable: refusing to send a token we cannot inspect would break the
    environment overrides for no gain.
    """

    expires_at = _jwt_expiry(token)
    if expires_at is None:
        return False
    now = int(time.time()) if now is None else now
    return expires_at <= now + TOKEN_EXPIRY_LEEWAY_SECONDS


def describe_missing_token(path: Optional[Path] = None) -> str:
    """Explain why polling has no usable token, without echoing the token."""

    auth_path = path or default_codex_auth_path()
    token = read_codex_access_token(auth_path)
    if token is not None and token_is_expired(token):
        return (
            "Codex access token expired; sign in again with the codex CLI "
            f"({auth_path})"
        )
    return (
        "CHATGPT_ACCESS_TOKEN or "
        f"{auth_path} tokens.access_token is required for wham polling"
    )


def _jwt_expiry(token: str) -> Optional[int]:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, Mapping):
        return None
    expires_at = claims.get("exp")
    return expires_at if isinstance(expires_at, int) else None


def fetch_wham_snapshot(
    access_token: str,
    usage_url: str = USAGE_URL,
    reset_credits_url: str = RESET_CREDITS_URL,
    timeout: float = 10.0,
) -> CodexRateSnapshot:
    usage = _fetch_json(
        usage_url,
        access_token=access_token,
        timeout=timeout,
    )
    snapshot = parse_usage_response(usage)
    if snapshot is None:
        raise RuntimeError("wham usage response did not include usable rate limits")

    if snapshot.account_id:
        try:
            credits = _fetch_json(
                reset_credits_url,
                access_token=access_token,
                account_id=snapshot.account_id,
                timeout=timeout,
            )
            snapshot = merge_reset_credits(snapshot, credits)
        except (urllib.error.URLError, OSError, json.JSONDecodeError, RuntimeError) as exc:
            print(f"codex-rate-indicator: reset-credit polling failed: {exc}", file=sys.stderr)

    return snapshot


def parse_usage_response(payload: Mapping[str, Any], updated_at: Optional[str] = None) -> Optional[CodexRateSnapshot]:
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return None

    windows = (
        _parse_wham_window(rate_limit.get("primary_window")),
        _parse_wham_window(rate_limit.get("secondary_window")),
    )
    five_hour = next(
        (window for window in windows if window and window.window_minutes == FIVE_HOUR_MINUTES),
        None,
    )
    weekly = next(
        (window for window in windows if window and window.window_minutes == WEEKLY_MINUTES),
        None,
    )
    if five_hour is None and weekly is None:
        return None

    reset_credits = _parse_reset_credit_payload(rate_limit.get("rate_limit_reset_credits"))
    return CodexRateSnapshot(
        updated_at=updated_at or _utc_now(),
        five_hour=five_hour,
        weekly=weekly,
        plan_type=_optional_str(rate_limit.get("plan_type") or payload.get("plan_type")),
        source_kind="wham",
        account_id=_optional_str(payload.get("account_id") or rate_limit.get("account_id")),
        reset_credits_available=reset_credits[0],
        reset_credit_expirations=reset_credits[1],
    )


def merge_reset_credits(snapshot: CodexRateSnapshot, payload: Mapping[str, Any]) -> CodexRateSnapshot:
    available, expirations = _parse_reset_credit_payload(payload)
    return CodexRateSnapshot(
        updated_at=snapshot.updated_at,
        five_hour=snapshot.five_hour,
        weekly=snapshot.weekly,
        plan_type=snapshot.plan_type,
        source_path=snapshot.source_path,
        source_kind=snapshot.source_kind,
        account_id=snapshot.account_id,
        reset_credits_available=available if available is not None else snapshot.reset_credits_available,
        reset_credit_expirations=expirations or snapshot.reset_credit_expirations,
    )


def preserve_cached_reset_credits(
    snapshot: CodexRateSnapshot,
    cached: Optional[CodexRateSnapshot],
) -> CodexRateSnapshot:
    if (
        snapshot.reset_credits_available is not None
        or cached is None
        or cached.reset_credits_available is None
        or snapshot.account_id != cached.account_id
    ):
        return snapshot
    return CodexRateSnapshot(
        updated_at=snapshot.updated_at,
        five_hour=snapshot.five_hour,
        weekly=snapshot.weekly,
        plan_type=snapshot.plan_type,
        source_path=snapshot.source_path,
        source_kind=snapshot.source_kind,
        account_id=snapshot.account_id,
        reset_credits_available=cached.reset_credits_available,
        reset_credit_expirations=cached.reset_credit_expirations,
    )


def read_wham_snapshot(path: Path = None) -> Optional[CodexRateSnapshot]:
    cache_path = default_wham_cache_path() if path is None else path
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return _snapshot_from_payload(payload)


def write_wham_snapshot(snapshot: CodexRateSnapshot, path: Path = None) -> None:
    cache_path = default_wham_cache_path() if path is None else path
    cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(f"{cache_path.suffix}.tmp")
    tmp_path.write_text(json.dumps(_snapshot_to_payload(snapshot), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, cache_path)


def format_reset_credit_lines(snapshot: CodexRateSnapshot) -> list[str]:
    if snapshot.reset_credits_available is None:
        return ["Reset credits: no data"]

    lines = [f"Reset credits: R{snapshot.reset_credits_available}"]
    for idx, expires_at in enumerate(snapshot.reset_credit_expirations, start=1):
        lines.append(f"{idx}. expires {format_wham_timestamp(expires_at)}")
    return lines


def format_wham_timestamp(value: str) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return value.replace("T", " ")[:16]
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll ChatGPT wham quota APIs into a local cache.")
    parser.add_argument("--once", action="store_true", help="Poll once and write the cache.")
    parser.add_argument("--cache", type=Path, default=Path(os.environ.get("CODEX_RATE_WHAM_CACHE", default_wham_cache_path())))
    parser.add_argument("--usage-url", default=os.environ.get("CHATGPT_WHAM_USAGE_URL", USAGE_URL))
    parser.add_argument("--reset-credits-url", default=os.environ.get("CHATGPT_WHAM_RESET_CREDITS_URL", RESET_CREDITS_URL))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("CHATGPT_WHAM_TIMEOUT", "10")))
    args = parser.parse_args()

    if not args.once:
        parser.error("--once is required")

    token = resolve_access_token()
    if not token:
        print(describe_missing_token(), file=sys.stderr)
        return 2

    try:
        cached = read_wham_snapshot(args.cache)
        snapshot = fetch_wham_snapshot(
            access_token=token,
            usage_url=args.usage_url,
            reset_credits_url=args.reset_credits_url,
            timeout=args.timeout,
        )
        snapshot = preserve_cached_reset_credits(snapshot, cached)
        write_wham_snapshot(snapshot, args.cache)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ChatGPT wham polling failed: {exc}", file=sys.stderr)
        return 1

    print(format_indicator_label(snapshot))
    return 0


def _fetch_json(url: str, access_token: str, timeout: float, account_id: Optional[str] = None) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "OpenAI-Beta": "codex-1",
        "Referer": "https://chatgpt.com/",
        "originator": "Codex Desktop",
        "User-Agent": "codex-rate-indicator/1.0",
    }
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id

    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("wham response was not a JSON object")
    return payload


def _parse_wham_window(value: Any) -> Optional[RateWindow]:
    if not isinstance(value, dict):
        return None

    reset = _parse_timestamp(_first_non_none(value.get("reset_at"), value.get("resets_at")))
    if reset is None:
        return None

    used_percent = _parse_used_percent(value)
    if used_percent is None:
        return None

    window_minutes = _parse_int(value.get("window_minutes"))
    if window_minutes is None:
        window_seconds = _parse_int(
            _first_non_none(value.get("limit_window_seconds"), value.get("window_seconds"))
        )
        if window_seconds is not None and window_seconds % 60 == 0:
            window_minutes = window_seconds // 60
    if window_minutes is None:
        return None
    return RateWindow(used_percent=used_percent, window_minutes=window_minutes, resets_at=reset)


def _parse_used_percent(value: Mapping[str, Any]) -> Optional[int]:
    explicit = _parse_number(
        _first_non_none(
            value.get("used_percent"),
            value.get("usage_percent"),
            value.get("percent_used"),
        )
    )
    if explicit is not None:
        return _clamp_percent(round(explicit))

    limit = _parse_number(_first_non_none(value.get("limit"), value.get("total"), value.get("max_count")))
    used = _parse_number(_first_non_none(value.get("used"), value.get("used_count")))
    remaining = _parse_number(
        _first_non_none(
            value.get("remaining"),
            value.get("remaining_count"),
            value.get("available_count"),
        )
    )

    if limit and used is not None:
        return _clamp_percent(round((used / limit) * 100))
    if limit and remaining is not None:
        return _clamp_percent(round(((limit - remaining) / limit) * 100))
    return None


def _parse_reset_credit_payload(value: Any) -> tuple[Optional[int], tuple[str, ...]]:
    if not isinstance(value, dict):
        return None, ()

    available = _parse_int(value.get("available_count"))
    raw_credits = value.get("credits") or value.get("items") or value.get("data") or []
    expirations: list[str] = []
    if isinstance(raw_credits, list):
        for item in raw_credits:
            if not isinstance(item, dict):
                continue
            expires_at = _optional_str(item.get("expires_at") or item.get("expiration_time"))
            if expires_at:
                expirations.append(expires_at)
    return available, tuple(expirations)


def _first_non_none(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _snapshot_to_payload(snapshot: CodexRateSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    payload["source_path"] = str(snapshot.source_path) if snapshot.source_path else None
    return payload


def _snapshot_from_payload(payload: Mapping[str, Any]) -> Optional[CodexRateSnapshot]:
    updated_at = _optional_str(payload.get("updated_at"))
    if not updated_at:
        return None
    return CodexRateSnapshot(
        updated_at=updated_at,
        five_hour=_window_from_payload(payload.get("five_hour")),
        weekly=_window_from_payload(payload.get("weekly")),
        plan_type=_optional_str(payload.get("plan_type")),
        source_kind=_optional_str(payload.get("source_kind")) or "wham",
        account_id=_optional_str(payload.get("account_id")),
        reset_credits_available=_parse_int(payload.get("reset_credits_available")),
        reset_credit_expirations=tuple(str(item) for item in payload.get("reset_credit_expirations", []) if item),
    )


def _window_from_payload(value: Any) -> Optional[RateWindow]:
    if not isinstance(value, dict):
        return None
    try:
        return RateWindow(
            used_percent=int(value["used_percent"]),
            window_minutes=int(value["window_minutes"]),
            resets_at=int(value["resets_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        pass
    parsed = _parse_datetime(value)
    return int(parsed.timestamp()) if parsed else None


def _parse_datetime(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _clamp_percent(value: int) -> int:
    return max(0, min(100, int(value)))


if __name__ == "__main__":
    raise SystemExit(main())
