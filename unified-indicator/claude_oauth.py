from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional


USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
TOKEN_ENDPOINT = "https://api.anthropic.com/v1/oauth/token"
# Claude Code's own OAuth client. A refresh minted under a different client id
# is rejected, so this has to match the CLI that owns the credential.
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
BETA_HEADER = "oauth-2025-04-20"
USER_AGENT = "claude-code/2.1.0"
KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_TIMEOUT = 10.0
REFRESH_COOLDOWN = 300.0
REFRESH_TIMEOUT = 30.0


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


def default_cache_path() -> Path:
    return Path(
        os.environ.get(
            "CLAUDE_OAUTH_CACHE",
            Path.home() / ".cache" / "rate-limit-indicator" / "claude-oauth.json",
        )
    )


def write_cache(
    snapshot: ClaudeOAuthSnapshot,
    cache_path: Optional[Path] = None,
) -> Path:
    """Keep the last usage snapshot so an expired token degrades to stale."""

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


def read_cache(cache_path: Optional[Path] = None) -> Optional[ClaudeOAuthSnapshot]:
    path = cache_path or default_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        windows = tuple(
            ClaudeOAuthWindow(
                id=str(item["id"]),
                used_percent=int(item["used_percent"]),
                resets_at=str(item["resets_at"]) if item.get("resets_at") else None,
            )
            for item in payload.get("windows", [])
            if isinstance(item, Mapping)
        )
        return ClaudeOAuthSnapshot(
            updated_at=str(payload["updated_at"]),
            windows=windows,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


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


def read_keychain_credentials() -> Optional[str]:
    """Return the raw Claude Code credential JSON held in the login Keychain."""

    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ("security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"),
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def read_credentials(
    path: Optional[Path] = None,
    *,
    now_ms: Optional[int] = None,
    keychain_reader: Optional[Callable[[], Optional[str]]] = None,
) -> ClaudeOAuthCredentials:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    credentials_path = path or default_credentials_path()
    read_keychain = keychain_reader or read_keychain_credentials

    def sources() -> Iterator[str]:
        try:
            yield credentials_path.read_text(encoding="utf-8")
        except OSError:
            pass
        # Claude Code on macOS keeps credentials in the Keychain rather than on
        # disk. An explicit path or file override asks for that file only.
        if path is None and not os.environ.get("CLAUDE_OAUTH_CREDENTIALS_FILE"):
            raw = read_keychain()
            if raw is not None:
                yield raw

    first_error: Optional[ClaudeOAuthUnavailable] = None
    for raw in sources():
        try:
            return _parse_credentials(raw, now_ms)
        except ClaudeOAuthUnavailable as exc:
            first_error = first_error or exc
    if first_error is not None:
        raise first_error
    raise ClaudeOAuthUnavailable("Claude OAuth credentials are unavailable")


def _parse_credentials(raw: str, now_ms: int) -> ClaudeOAuthCredentials:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeOAuthUnavailable(
            "Claude OAuth credentials are unavailable"
        ) from exc

    oauth = payload.get("claudeAiOauth") if isinstance(payload, Mapping) else None
    if not isinstance(oauth, Mapping):
        # Claude Code 2.1.x can leave the credential holding MCP server OAuth
        # state and nothing else. No amount of retrying recovers that shape, so
        # say the one thing that does.
        if isinstance(payload, Mapping) and payload.get("mcpOAuth"):
            raise ClaudeOAuthUnavailable(
                "Claude stored only MCP server credentials; "
                "sign in again with `claude auth login`"
            )
        raise ClaudeOAuthUnavailable(
            "Claude OAuth credentials have no claudeAiOauth entry"
        )

    access_token = str(oauth.get("accessToken") or "").strip()
    expires_at_ms = _integer(oauth.get("expiresAt"))
    if not access_token or expires_at_ms is None:
        raise ClaudeOAuthUnavailable("Claude OAuth credentials are incomplete")

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


def default_refresh_stamp_path() -> Path:
    return Path(
        os.environ.get(
            "CLAUDE_REFRESH_STAMP",
            Path.home() / ".cache" / "rate-limit-indicator" / "claude-refresh-attempt",
        )
    )


def refresh_is_in_cooldown(
    stamp_path: Optional[Path] = None,
    *,
    now: Optional[float] = None,
) -> bool:
    """Report whether the last refresh is recent enough to skip this one."""

    path = stamp_path or default_refresh_stamp_path()
    now = time.time() if now is None else now
    try:
        last_attempt = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    # A clock that jumped backwards must not lock refreshing out until it
    # catches up, so a negative age counts as no cooldown at all.
    return 0.0 <= now - last_attempt < REFRESH_COOLDOWN


def record_refresh_attempt(
    stamp_path: Optional[Path] = None,
    *,
    now: Optional[float] = None,
) -> None:
    path = stamp_path or default_refresh_stamp_path()
    now = time.time() if now is None else now
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(f"{now}\n", encoding="utf-8")
    except OSError:
        # Losing the stamp costs us the cooldown, not the refresh.
        pass


def refresh_credentials(
    path: Optional[Path] = None,
    *,
    opener: Optional[
        Callable[[urllib.request.Request, float], Mapping[str, Any]]
    ] = None,
    stamp_path: Optional[Path] = None,
    now: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> Optional[ClaudeOAuthCredentials]:
    """Exchange the stored refresh token for a live access token.

    Claude Code only refreshes while it runs, so an idle machine wakes up with
    an expired token and nothing to fix it. The refresh itself is one POST, and
    Claude Code reads the credential back off disk before every refresh of its
    own, so writing the new pair back is enough to keep it working.

    Two rules make that safe to do underneath a running Claude Code:

    * The refresh token rotates. Whoever spends it first invalidates the other
      copy, so the write is a compare-and-swap -- re-read the file and only
      replace the entry that still holds the token we spent. Claude Code writes
      the same way.
    * A refresh that fails must change nothing. Claude Code answers a dead
      refresh token by blanking accessToken and refreshToken on disk, which
      costs the user a full `claude auth login`. We never do that: a failure
      here leaves the file exactly as it was and the caller reports an expired
      token, which is the state we were already in.
    """

    # The indicator is started from a desktop autostart entry, which never
    # sources providers.env, so the caller reads the flag from that file and
    # passes it in. The environment is the fallback for direct invocations.
    if enabled is None:
        enabled = _flag_enabled(os.environ.get("CLAUDE_AUTO_REFRESH"))
    if not enabled:
        return None
    # macOS keeps this credential in the Keychain, where none of the file
    # handling below applies.
    if sys.platform == "darwin" and path is None:
        return None

    credentials_path = path or default_credentials_path()
    try:
        payload = json.loads(credentials_path.read_text(encoding="utf-8"))
        oauth = payload["claudeAiOauth"]
        spent_token = str(oauth["refreshToken"] or "").strip()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not spent_token:
        return None

    now = time.time() if now is None else now
    stamp = stamp_path or default_refresh_stamp_path()
    # The poller fires every 60s. Without a cooldown, a refresh that keeps
    # failing earns a request every tick for as long as it stays broken.
    # Stamping before the request means a hang still counts as an attempt.
    if refresh_is_in_cooldown(stamp, now=now):
        return None
    record_refresh_attempt(stamp, now=now)

    request = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=json.dumps(
            {
                "grant_type": "refresh_token",
                "refresh_token": spent_token,
                "client_id": CLIENT_ID,
            }
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-beta": BETA_HEADER,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    request_json = opener or _open_json
    try:
        response = request_json(request, REFRESH_TIMEOUT)
    except ClaudeOAuthUnavailable:
        return None

    access_token = str(response.get("access_token") or "").strip()
    expires_in = _integer(response.get("expires_in"))
    if not access_token or expires_in is None:
        return None

    now_ms = int(now * 1000)
    updated = dict(oauth)
    updated["accessToken"] = access_token
    updated["expiresAt"] = now_ms + expires_in * 1000
    # A response without a new refresh token means the old one still stands.
    updated["refreshToken"] = str(response.get("refresh_token") or spent_token)
    rotated_in = _integer(response.get("refresh_token_expires_in"))
    if rotated_in is not None:
        updated["refreshTokenExpiresAt"] = now_ms + rotated_in * 1000

    if not _swap_credentials(credentials_path, spent_token, updated):
        return None

    raw_scopes = updated.get("scopes")
    return ClaudeOAuthCredentials(
        access_token=access_token,
        expires_at_ms=updated["expiresAt"],
        scopes=(
            tuple(str(scope) for scope in raw_scopes if isinstance(scope, str))
            if isinstance(raw_scopes, list)
            else ()
        ),
    )


def _swap_credentials(
    credentials_path: Path,
    spent_token: str,
    updated: Mapping[str, Any],
) -> bool:
    """Replace the credential only while it still holds the token we spent."""

    try:
        current = json.loads(credentials_path.read_text(encoding="utf-8"))
        if current["claudeAiOauth"]["refreshToken"] != spent_token:
            # Claude Code refreshed while our request was in flight. Its pair is
            # the live one; ours is already dead. Leave the file alone.
            return False
    except (OSError, ValueError, TypeError, KeyError):
        return False

    current["claudeAiOauth"] = dict(updated)
    temporary = credentials_path.with_name(f".{credentials_path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(current, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, credentials_path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False
    return True


def _flag_enabled(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def fetch_oauth_snapshot(
    credentials_path: Optional[Path] = None,
    *,
    endpoint: Optional[str] = None,
    opener: Optional[
        Callable[[urllib.request.Request, float], Mapping[str, Any]]
    ] = None,
    now: Optional[datetime] = None,
    allow_refresh: Optional[bool] = None,
) -> ClaudeOAuthSnapshot:
    try:
        credentials = read_credentials(credentials_path)
    except ClaudeOAuthUnavailable:
        # An idle machine reaches here every morning: the token expired
        # overnight and no Claude Code ran to renew it. Refreshing it ourselves
        # is opt-in, and refresh_credentials returns None whenever it declines,
        # so without CLAUDE_AUTO_REFRESH this raises exactly as it used to.
        credentials = refresh_credentials(
            credentials_path, opener=opener, enabled=allow_refresh
        )
        if credentials is None:
            raise

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
