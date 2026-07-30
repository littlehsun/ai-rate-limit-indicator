from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"
USER_AGENT = "claude-code/2.1.0"


class ClaudeOAuthUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaudeOAuthCredentials:
    access_token: str = field(repr=False)
    expires_at_ms: int
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class ClaudeOAuthWindow:
    id: str
    used_percent: int
    resets_at: Optional[str]


@dataclass(frozen=True)
class ClaudeOAuthSnapshot:
    updated_at: str
    windows: tuple[ClaudeOAuthWindow, ...]


def default_credentials_path() -> Path:
    override = os.environ.get("CLAUDE_OAUTH_CREDENTIALS_FILE")
    if override:
        return Path(override).expanduser()

    config_roots = os.environ.get("CLAUDE_CONFIG_DIR", "")
    first_root = next(
        (value.strip() for value in config_roots.split(",") if value.strip()),
        "",
    )
    root = Path(first_root).expanduser() if first_root else Path.home() / ".claude"
    return root / ".credentials.json"


def read_credentials(
    path: Optional[Path] = None,
    *,
    now_ms: Optional[int] = None,
) -> ClaudeOAuthCredentials:
    credentials_path = path or default_credentials_path()
    try:
        payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaudeOAuthUnavailable(
            "Claude OAuth credentials are unavailable"
        ) from exc

    oauth = payload.get("claudeAiOauth") if isinstance(payload, Mapping) else None
    if not isinstance(oauth, Mapping):
        raise ClaudeOAuthUnavailable(
            "Claude OAuth credentials have no claudeAiOauth entry"
        )

    access_token = str(oauth.get("accessToken") or "").strip()
    expires_at_ms = _integer(oauth.get("expiresAt"))
    if not access_token or expires_at_ms is None:
        raise ClaudeOAuthUnavailable("Claude OAuth credentials are incomplete")

    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if expires_at_ms <= now_ms + 60_000:
        raise ClaudeOAuthUnavailable("Claude OAuth access token is expired")

    raw_scopes = oauth.get("scopes")
    scopes = (
        tuple(str(scope) for scope in raw_scopes if isinstance(scope, str))
        if isinstance(raw_scopes, list)
        else ()
    )
    return ClaudeOAuthCredentials(
        access_token=access_token,
        expires_at_ms=expires_at_ms,
        scopes=scopes,
    )


def fetch_oauth_snapshot(
    credentials_path: Optional[Path] = None,
    *,
    endpoint: Optional[str] = None,
    opener: Optional[
        Callable[[urllib.request.Request, float], Mapping[str, Any]]
    ] = None,
    now: Optional[datetime] = None,
) -> ClaudeOAuthSnapshot:
    credentials = read_credentials(credentials_path)
    request = urllib.request.Request(
        endpoint or USAGE_ENDPOINT,
        headers={
            "Authorization": f"Bearer {credentials.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": BETA_HEADER,
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    request_json = opener or _open_json
    payload = request_json(request, 30.0)
    windows = []
    for window_id, source_key in (("5h", "five_hour"), ("7d", "seven_day")):
        value = payload.get(source_key)
        if not isinstance(value, Mapping):
            continue
        utilization = _number(value.get("utilization"))
        if utilization is None:
            continue
        windows.append(
            ClaudeOAuthWindow(
                id=window_id,
                used_percent=_percent(utilization),
                resets_at=str(value["resets_at"]) if value.get("resets_at") else None,
            )
        )
    if not windows:
        raise ClaudeOAuthUnavailable("Claude OAuth usage response has no quota windows")

    timestamp = now or datetime.now(timezone.utc)
    return ClaudeOAuthSnapshot(
        updated_at=timestamp.astimezone(timezone.utc).isoformat(),
        windows=tuple(windows),
    )


def _open_json(
    request: urllib.request.Request,
    timeout: float,
) -> Mapping[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            message = "Claude OAuth request is unauthorized"
        elif exc.code == 403:
            message = "Claude OAuth token does not have the required usage scope"
        elif exc.code == 429:
            message = "Claude OAuth usage endpoint is rate limited"
        else:
            message = f"Claude OAuth usage API returned HTTP {exc.code}"
        raise ClaudeOAuthUnavailable(message) from exc
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ClaudeOAuthUnavailable("Claude OAuth usage request failed") from exc
    if not isinstance(payload, Mapping):
        raise ClaudeOAuthUnavailable("Claude OAuth usage response is invalid")
    return payload


def _percent(value: float) -> int:
    return max(0, min(100, int(value + 0.5)))


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
