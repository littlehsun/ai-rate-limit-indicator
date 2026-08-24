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
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from codex_rate import FIVE_HOUR_MINUTES, WEEKLY_MINUTES, CodexRateSnapshot, RateWindow, format_indicator_label


USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
TOKEN_ENDPOINT = "https://auth.openai.com/oauth/token"
# The codex CLI's own OAuth client. A refresh minted under a different client
# id is rejected, so this has to match the CLI that owns auth.json.
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
USER_AGENT = "codex-rate-indicator/1.0"
TOKEN_EXPIRY_LEEWAY_SECONDS = 60
REFRESH_LEAD_SECONDS = 300
REFRESH_COOLDOWN = 300.0
REFRESH_TIMEOUT = 30.0


def default_wham_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    cache_home = Path(base) if base else Path.home() / ".cache"
    return cache_home / "codex-rate-indicator" / "wham.json"


def default_codex_auth_path() -> Path:
    return Path(os.environ.get("CODEX_AUTH_FILE", Path.home() / ".codex" / "auth.json"))


def resolve_access_token(
    *,
    now: Optional[int] = None,
    allow_refresh: Optional[bool] = None,
) -> Optional[str]:
    for candidate in (
        _optional_str(os.environ.get("CHATGPT_ACCESS_TOKEN")),
        _optional_str(os.environ.get("CHATGPT_BEARER_TOKEN")),
    ):
        # An expired token only earns a 401 on every poll. These two are the
        # user's own overrides, and nothing here can renew them.
        if candidate is not None and not token_is_expired(candidate, now=now):
            return candidate

    stored = read_codex_access_token(default_codex_auth_path())
    if stored is not None and not token_is_expired(stored, now=now):
        if not token_expires_soon(stored, now=now):
            return stored
        # Still usable, but a token that lapses between this call and the
        # request costs a whole poll. Renewing early closes that window, and a
        # refresh that fails leaves the token we already hold in place.
        return refresh_access_token(enabled=allow_refresh, now=now) or stored

    # Nothing on hand is usable. The codex CLI is the usual thing that renews
    # auth.json, and on a machine where it never runs nothing does, so mint a
    # token ourselves when the user has opted in.
    return refresh_access_token(enabled=allow_refresh, now=now)


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


def token_expires_soon(token: str, *, now: Optional[int] = None) -> bool:
    """Report whether a still-usable token is close enough to expiry to renew.

    A token that outlives this call but not the request it authorises fails the
    poll anyway, so the lead time is the cheapest way to never see that. As with
    expiry, a token that cannot prove its own deadline is left alone.
    """

    expires_at = _jwt_expiry(token)
    if expires_at is None:
        return False
    now = int(time.time()) if now is None else now
    return expires_at <= now + REFRESH_LEAD_SECONDS


def describe_missing_token(path: Optional[Path] = None) -> str:
    """Explain why polling has no usable token, without echoing the token."""

    auth_path = path or default_codex_auth_path()
    token = read_codex_access_token(auth_path)
    if token is not None and token_is_expired(token):
        # The refresh token usually outlives the access token, so running the
        # CLI once is enough. Telling people to sign in again would send them
        # through a login they almost certainly do not need.
        return (
            "Codex access token expired; run the codex CLI once to refresh it, "
            f"or set CODEX_AUTO_REFRESH=true ({auth_path})"
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


def default_refresh_stamp_path() -> Path:
    return Path(
        os.environ.get(
            "CODEX_REFRESH_STAMP",
            Path.home() / ".cache" / "rate-limit-indicator" / "codex-refresh-attempt",
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


def refresh_access_token(
    path: Optional[Path] = None,
    *,
    opener: Optional[
        Callable[[urllib.request.Request, float], Mapping[str, Any]]
    ] = None,
    stamp_path: Optional[Path] = None,
    now: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> Optional[str]:
    """Exchange the stored refresh token for a live access token.

    The codex CLI renews auth.json only while it runs, so a machine that polls
    without ever opening the CLI wakes up with an expired token and nothing to
    fix it. The refresh is one POST, and the CLI re-reads the file before every
    refresh of its own, so writing the new tokens back is enough to keep it
    working.

    The same two rules as claude_oauth.refresh_credentials make that safe to do
    underneath a running codex CLI:

    * The refresh token may rotate. Whoever spends it first invalidates the
      other copy, so the write is a compare-and-swap -- re-read the file and
      only replace tokens that still hold the token we spent.
    * A refresh that fails must change nothing. Leaving the file exactly as it
      was costs the caller one expired token, which is the state it was already
      reporting; blanking it would cost the user a full `codex login`.
    """

    # The unified indicator starts from a desktop autostart entry, which never
    # sources providers.env, so that caller reads the flag from the config and
    # passes it in. The environment is what the wham poll unit supplies.
    if enabled is None:
        enabled = _flag_enabled(os.environ.get("CODEX_AUTO_REFRESH"))
    if not enabled:
        return None

    auth_path = path or default_codex_auth_path()
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
        tokens = payload["tokens"]
        spent_token = str(tokens["refresh_token"] or "").strip()
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
        data=urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": spent_token,
            }
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    request_json = opener or _post_json
    try:
        response = request_json(request, REFRESH_TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError):
        return None

    access_token = _optional_str(response.get("access_token"))
    if access_token is None:
        return None

    updated = dict(tokens)
    updated["access_token"] = access_token
    # A response without a new refresh token means the old one still stands,
    # and id_token only moves when the endpoint chooses to reissue it.
    for key in ("refresh_token", "id_token"):
        value = _optional_str(response.get(key))
        if value is not None:
            updated[key] = value

    if not _swap_auth(auth_path, spent_token, updated, now=now):
        return None
    return access_token


def _swap_auth(
    auth_path: Path,
    spent_token: str,
    tokens: Mapping[str, Any],
    *,
    now: Optional[float] = None,
) -> bool:
    """Replace the tokens only while auth.json still holds the one we spent."""

    try:
        current = json.loads(auth_path.read_text(encoding="utf-8"))
        if current["tokens"]["refresh_token"] != spent_token:
            # The codex CLI refreshed while our request was in flight. Its pair
            # is the live one; ours is already dead. Leave the file alone.
            return False
    except (OSError, ValueError, TypeError, KeyError):
        return False

    current["tokens"] = dict(tokens)
    # The codex CLI stamps this field itself, so keeping it current means a
    # refresh of ours is not mistaken for the file having gone stale.
    current["last_refresh"] = _utc_now()
    return _write_secret_json(auth_path, current)


def _write_secret_json(path: Path, payload: Mapping[str, Any]) -> bool:
    """Replace a credential file atomically, never widening its permissions.

    The temporary file is created 0600 by open() rather than chmod'ed after the
    fact, because writing the refresh token into a default-umask file first
    leaves it world-readable for as long as the write takes.
    """

    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass
        return False
    return True


def _flag_enabled(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _post_json(request: urllib.request.Request, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_wham_snapshot(
    access_token: str,
    usage_url: str = USAGE_URL,
    reset_credits_url: str = RESET_CREDITS_URL,
    timeout: float = 10.0,
    *,
    allow_refresh: Optional[bool] = None,
) -> CodexRateSnapshot:
    try:
        usage = _fetch_json(
            usage_url,
            access_token=access_token,
            timeout=timeout,
        )
    except urllib.error.HTTPError as exc:
        # The token's own `exp` said it was live, so it is not the whole story:
        # a revoked or already-rotated token is refused exactly like this. One
        # refresh is worth the request, and the cooldown keeps a token that
        # stays refused from earning one on every tick.
        refreshed = (
            refresh_access_token(enabled=allow_refresh)
            if exc.code in (401, 403)
            else None
        )
        if refreshed is None or refreshed == access_token:
            raise
        access_token = refreshed
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
        "User-Agent": USER_AGENT,
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
