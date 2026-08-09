#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl


DEFAULT_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing"
DEFAULT_USER_AGENT = "grok-rate-indicator/0.1"
CLI_REFRESH_TIMEOUT = 60.0


@dataclass(frozen=True)
class PeriodUsage:
    """One usage window (weekly pool or monthly credit budget)."""

    used_percent: int
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    # Absolute amounts are USD cents when present (CLI shows "used of $ limit").
    used_cents: Optional[int] = None
    limit_cents: Optional[int] = None

    @property
    def remaining_cents(self) -> Optional[int]:
        if self.used_cents is None or self.limit_cents is None:
            return None
        return max(self.limit_cents - self.used_cents, 0)


@dataclass(frozen=True)
class GrokBillingSnapshot:
    updated_at: str
    weekly: Optional[PeriodUsage]
    monthly: Optional[PeriodUsage]
    on_demand_cap_cents: Optional[int] = None
    on_demand_used_cents: Optional[int] = None
    prepaid_balance_cents: Optional[int] = None
    product_usage: tuple[tuple[str, Optional[int]], ...] = ()
    is_unified: bool = False
    subscription_tier: Optional[str] = None
    source_kind: str = "billing"
    source_url: Optional[str] = None

    @property
    def max_used_percent(self) -> int:
        values = []
        if self.weekly is not None:
            values.append(self.weekly.used_percent)
        if self.monthly is not None:
            values.append(self.monthly.used_percent)
        return max(values) if values else 0


def default_grok_home() -> Path:
    return Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))


def default_cache_path() -> Path:
    override = os.environ.get("GROK_RATE_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "grok-rate-indicator" / "billing.json"


def default_billing_url() -> str:
    return os.environ.get("GROK_RATE_BILLING_URL", DEFAULT_BILLING_URL)


def credits_billing_url(base_url: Optional[str] = None) -> str:
    """CLI /usage weekly view uses GET /billing?format=credits."""
    return _with_query(base_url or default_billing_url(), {"format": "credits"})


def _read_auth_candidates(
    grok_home: Optional[Path] = None,
) -> tuple[list[tuple[float, str]], int]:
    """Return live (score, token) pairs from auth.json and the expired count."""
    home = grok_home or default_grok_home()
    auth_path = home / "auth.json"
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], 0

    if not isinstance(raw, dict):
        return [], 0

    candidates: list[tuple[float, str]] = []
    expired = 0
    now = datetime.now(timezone.utc)
    for value in raw.values():
        if not isinstance(value, dict):
            continue
        token = value.get("key") or value.get("access_token")
        if not isinstance(token, str) or not token.strip():
            continue
        score = 1.0
        expires_at = value.get("expires_at")
        if isinstance(expires_at, str) and expires_at:
            try:
                exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
            else:
                # An expired token only earns a 401 on every poll. The Grok CLI
                # owns refreshing it, so skip it and let the caller say so.
                if exp <= now:
                    expired += 1
                    continue
                score = exp.timestamp()
        candidates.append((score, token.strip()))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates, expired


def read_access_token(grok_home: Optional[Path] = None) -> Optional[str]:
    """Return the best unexpired bearer token from ~/.grok/auth.json."""
    candidates, _ = _read_auth_candidates(grok_home)
    return candidates[0][1] if candidates else None


def refresh_token_with_cli(
    grok_home: Optional[Path] = None,
    *,
    runner: Optional[Any] = None,
) -> Optional[str]:
    """Ask the Grok CLI to refresh its own token, and return the fresh one.

    auth.json belongs to the CLI, which serialises refreshes behind a file lock
    and rotates the refresh token as it goes. Running one cheap authenticated
    command is the supported way to get a live token without reimplementing any
    of that. `grok models` is used because it authenticates, exits in about a
    second, and spends no model quota.
    """
    if not _flag_enabled(os.environ.get("GROK_AUTO_REFRESH")):
        return None
    grok_bin = find_grok_cli()
    if grok_bin is None:
        return None
    run = runner or _run_grok_models
    try:
        run(grok_bin)
    except Exception:
        # A refresh that did not happen is reported by the caller as an expired
        # token, which is already the state we were trying to leave.
        return None
    return read_access_token(grok_home)


def find_grok_cli() -> Optional[str]:
    """Locate the Grok CLI, tolerating a launcher with a bare PATH.

    Pollers run under launchd or systemd, whose PATH holds none of the
    directories the CLI installs itself into, so PATH alone finds nothing. The
    installer records GROK_CLI for that reason; the well-known locations are a
    fallback for a CLI installed after the fact.
    """
    override = (os.environ.get("GROK_CLI") or "").strip()
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("grok")
    if found:
        return found
    for candidate in (
        Path.home() / ".local" / "bin" / "grok",
        Path.home() / ".grok" / "bin" / "grok",
    ):
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _run_grok_models(grok_bin: str) -> None:
    subprocess.run(
        (grok_bin, "models"),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=CLI_REFRESH_TIMEOUT,
        check=False,
    )


def _flag_enabled(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def describe_missing_token(grok_home: Optional[Path] = None) -> str:
    """Explain why read_access_token found nothing usable."""
    auth_path = (grok_home or default_grok_home()) / "auth.json"
    _, expired = _read_auth_candidates(grok_home)
    if expired:
        return f"Grok access token expired; sign in again with the grok CLI ({auth_path})"
    return f"no access token found in {auth_path}"


def _money_val(value: Any) -> Optional[int]:
    """Extract integer amount from `{ "val": N }` or a bare number (USD cents)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, dict) and "val" in value:
        nested = value.get("val")
        if isinstance(nested, bool) or nested is None:
            return None
        if isinstance(nested, (int, float)):
            return int(round(nested))
        if isinstance(nested, str) and nested.strip():
            try:
                return int(round(float(nested)))
            except ValueError:
                return None
    if isinstance(value, str) and value.strip():
        try:
            return int(round(float(value)))
        except ValueError:
            return None
    return None


def _percent_val(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    if isinstance(value, str) and value.strip():
        try:
            return int(round(float(value)))
        except ValueError:
            return None
    return None


def format_usd_cents(cents: int) -> str:
    dollars = cents / 100.0
    if cents % 100 == 0:
        return f"${dollars:.0f}"
    return f"${dollars:.2f}"


def parse_monthly_payload(payload: dict[str, Any]) -> Optional[PeriodUsage]:
    config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    if not isinstance(config, dict):
        return None
    used = _money_val(config.get("used"))
    limit = _money_val(config.get("monthlyLimit"))
    if used is None or limit is None:
        return None
    percent = int(round(100.0 * used / limit)) if limit > 0 else 0
    start = config.get("billingPeriodStart")
    end = config.get("billingPeriodEnd")
    if not isinstance(start, str):
        start = None
    if not isinstance(end, str):
        end = None
    # Default monthly endpoint uses calendar-month windows; if the response
    # advertises a weekly currentPeriod, treat it as weekly absolute credits.
    current = config.get("currentPeriod")
    if isinstance(current, dict):
        ptype = str(current.get("type") or "")
        if "WEEKLY" in ptype.upper():
            return None
    return PeriodUsage(
        used_percent=percent,
        period_start=start,
        period_end=end,
        used_cents=used,
        limit_cents=limit,
    )


def parse_credits_payload(payload: dict[str, Any]) -> tuple[Optional[PeriodUsage], dict[str, Any]]:
    """Parse /billing?format=credits (weekly unified pool)."""
    config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    meta: dict[str, Any] = {}
    if not isinstance(config, dict):
        return None, meta

    percent = _percent_val(config.get("creditUsagePercent"))
    start = end = None
    period_type = ""
    current = config.get("currentPeriod")
    if isinstance(current, dict):
        period_type = str(current.get("type") or "")
        start = current.get("start") if isinstance(current.get("start"), str) else None
        end = current.get("end") if isinstance(current.get("end"), str) else None
        meta["period_type"] = period_type
    if start is None and isinstance(config.get("billingPeriodStart"), str):
        start = config.get("billingPeriodStart")
    if end is None and isinstance(config.get("billingPeriodEnd"), str):
        end = config.get("billingPeriodEnd")

    products: list[tuple[str, Optional[int]]] = []
    raw_products = config.get("productUsage")
    if isinstance(raw_products, list):
        for item in raw_products:
            if not isinstance(item, dict):
                continue
            name = item.get("product")
            if not isinstance(name, str) or not name:
                continue
            products.append((name, _percent_val(item.get("usagePercent"))))
    meta["product_usage"] = tuple(products)
    meta["is_unified"] = bool(config.get("isUnifiedBillingUser"))
    meta["on_demand_cap_cents"] = _money_val(config.get("onDemandCap"))
    meta["on_demand_used_cents"] = _money_val(config.get("onDemandUsed"))
    meta["prepaid_balance_cents"] = _money_val(config.get("prepaidBalance"))

    # Prefer product-specific GrokBuild % when overall is missing.
    if percent is None:
        for name, p in products:
            if name == "GrokBuild" and p is not None:
                percent = p
                break

    # The credits endpoint omits usage fields at the beginning of a new weekly
    # period. A declared weekly period still represents a valid zero-usage
    # window, not missing data.
    if (
        percent is None
        and "WEEKLY" in period_type.upper()
        and (start is not None or end is not None)
    ):
        percent = 0

    if percent is None:
        return None, meta

    return (
        PeriodUsage(used_percent=percent, period_start=start, period_end=end),
        meta,
    )


def merge_snapshots(
    *,
    weekly: Optional[PeriodUsage],
    monthly: Optional[PeriodUsage],
    meta: Optional[dict[str, Any]] = None,
    updated_at: Optional[str] = None,
    source_url: Optional[str] = None,
) -> Optional[GrokBillingSnapshot]:
    if weekly is None and monthly is None:
        return None
    meta = meta or {}
    products = meta.get("product_usage") or ()
    if not isinstance(products, tuple):
        products = tuple(products)
    return GrokBillingSnapshot(
        updated_at=updated_at or datetime.now(timezone.utc).isoformat(),
        weekly=weekly,
        monthly=monthly,
        on_demand_cap_cents=meta.get("on_demand_cap_cents"),
        on_demand_used_cents=meta.get("on_demand_used_cents"),
        prepaid_balance_cents=meta.get("prepaid_balance_cents"),
        product_usage=products,
        is_unified=bool(meta.get("is_unified")),
        subscription_tier=meta.get("subscription_tier"),
        source_kind="billing",
        source_url=source_url,
    )


def _http_get_json(url: str, token: str, timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read(300).decode("utf-8", errors="replace")
        raise RuntimeError(f"billing HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"billing network error: {exc.reason}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("billing response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("billing response is not a JSON object")
    return payload


def fetch_billing_snapshot(
    *,
    token: str,
    url: Optional[str] = None,
    timeout: float = 15.0,
) -> GrokBillingSnapshot:
    base = url or default_billing_url()
    credits_url = credits_billing_url(base)
    monthly_url = base

    weekly = None
    monthly = None
    meta: dict[str, Any] = {}
    errors: list[str] = []

    try:
        credits_payload = _http_get_json(credits_url, token, timeout=timeout)
        weekly, meta = parse_credits_payload(credits_payload)
    except RuntimeError as exc:
        errors.append(f"credits: {exc}")

    try:
        monthly_payload = _http_get_json(monthly_url, token, timeout=timeout)
        monthly = parse_monthly_payload(monthly_payload)
        # Fill on-demand from monthly response when credits omitted it.
        if isinstance(monthly_payload.get("config"), dict):
            cfg = monthly_payload["config"]
            if meta.get("on_demand_cap_cents") is None:
                meta["on_demand_cap_cents"] = _money_val(cfg.get("onDemandCap"))
    except RuntimeError as exc:
        errors.append(f"monthly: {exc}")

    snapshot = merge_snapshots(
        weekly=weekly,
        monthly=monthly,
        meta=meta,
        updated_at=datetime.now(timezone.utc).isoformat(),
        source_url=credits_url,
    )
    if snapshot is None:
        detail = "; ".join(errors) if errors else "no usable weekly/monthly fields"
        raise RuntimeError(f"billing response did not include usable usage fields ({detail})")
    return snapshot


def write_cache(snapshot: GrokBillingSnapshot, cache_path: Optional[Path] = None) -> Path:
    path = cache_path or default_cache_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "updated_at": snapshot.updated_at,
        "weekly": asdict(snapshot.weekly) if snapshot.weekly else None,
        "monthly": asdict(snapshot.monthly) if snapshot.monthly else None,
        "on_demand_cap_cents": snapshot.on_demand_cap_cents,
        "on_demand_used_cents": snapshot.on_demand_used_cents,
        "prepaid_balance_cents": snapshot.prepaid_balance_cents,
        "product_usage": [list(item) for item in snapshot.product_usage],
        "is_unified": snapshot.is_unified,
        "subscription_tier": snapshot.subscription_tier,
        "source_kind": snapshot.source_kind,
        "source_url": snapshot.source_url,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _period_from_dict(value: Any) -> Optional[PeriodUsage]:
    if not isinstance(value, dict):
        return None
    percent = _percent_val(value.get("used_percent"))
    if percent is None:
        return None
    start = value.get("period_start")
    end = value.get("period_end")
    if not isinstance(start, str):
        start = None
    if not isinstance(end, str):
        end = None
    return PeriodUsage(
        used_percent=percent,
        period_start=start,
        period_end=end,
        used_cents=_money_val(value.get("used_cents")),
        limit_cents=_money_val(value.get("limit_cents")),
    )


def read_cache(cache_path: Optional[Path] = None) -> Optional[GrokBillingSnapshot]:
    path = cache_path or default_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    # New cache format.
    if "weekly" in payload or "monthly" in payload:
        weekly = _period_from_dict(payload.get("weekly"))
        monthly = _period_from_dict(payload.get("monthly"))
        products_raw = payload.get("product_usage") or []
        products: list[tuple[str, Optional[int]]] = []
        if isinstance(products_raw, list):
            for item in products_raw:
                if isinstance(item, (list, tuple)) and item:
                    name = str(item[0])
                    pct = _percent_val(item[1]) if len(item) > 1 else None
                    products.append((name, pct))
        updated_at = payload.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            updated_at = datetime.now(timezone.utc).isoformat()
        return GrokBillingSnapshot(
            updated_at=updated_at,
            weekly=weekly,
            monthly=monthly,
            on_demand_cap_cents=_money_val(payload.get("on_demand_cap_cents")),
            on_demand_used_cents=_money_val(payload.get("on_demand_used_cents")),
            prepaid_balance_cents=_money_val(payload.get("prepaid_balance_cents")),
            product_usage=tuple(products),
            is_unified=bool(payload.get("is_unified")),
            subscription_tier=payload.get("subscription_tier")
            if isinstance(payload.get("subscription_tier"), str)
            else None,
            source_kind=str(payload.get("source_kind") or "billing"),
            source_url=payload.get("source_url") if isinstance(payload.get("source_url"), str) else None,
        )

    # Legacy v1 cache: flat used/monthly_limit only.
    if "used" in payload and "monthly_limit" in payload:
        used = _money_val(payload.get("used"))
        limit = _money_val(payload.get("monthly_limit"))
        if used is None or limit is None:
            return None
        percent = int(round(100.0 * used / limit)) if limit > 0 else 0
        start = payload.get("period_start") if isinstance(payload.get("period_start"), str) else None
        end = payload.get("period_end") if isinstance(payload.get("period_end"), str) else None
        updated_at = payload.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            updated_at = datetime.now(timezone.utc).isoformat()
        monthly = PeriodUsage(
            used_percent=percent,
            period_start=start,
            period_end=end,
            used_cents=used,
            limit_cents=limit,
        )
        return GrokBillingSnapshot(
            updated_at=updated_at,
            weekly=None,
            monthly=monthly,
            on_demand_cap_cents=_money_val(payload.get("on_demand_cap")),
            source_kind="billing",
            source_url=payload.get("source_url") if isinstance(payload.get("source_url"), str) else None,
        )

    # Raw API payload fallback.
    monthly = parse_monthly_payload(payload)
    weekly, meta = parse_credits_payload(payload)
    return merge_snapshots(weekly=weekly, monthly=monthly, meta=meta)


def poll_and_cache(
    *,
    grok_home: Optional[Path] = None,
    cache_path: Optional[Path] = None,
    url: Optional[str] = None,
) -> GrokBillingSnapshot:
    token = read_access_token(grok_home)
    if not token:
        token = refresh_token_with_cli(grok_home)
    if not token:
        raise RuntimeError(describe_missing_token(grok_home))
    snapshot = fetch_billing_snapshot(token=token, url=url)
    write_cache(snapshot, cache_path=cache_path)
    return snapshot


def format_indicator_label(snapshot: GrokBillingSnapshot, now: Optional[int] = None) -> str:
    weekly, monthly, reset = format_indicator_parts(snapshot, now=now)
    return f"{weekly}|{monthly}  ⟳{reset}"


def format_indicator_parts(snapshot: GrokBillingSnapshot, now: Optional[int] = None) -> tuple[str, str, str]:
    now = int(time.time()) if now is None else now
    weekly = f"{snapshot.weekly.used_percent}%" if snapshot.weekly else "--%"
    monthly = f"{snapshot.monthly.used_percent}%" if snapshot.monthly else "--%"
    # Countdown prefers the nearer weekly reset (what users hit first).
    end = None
    if snapshot.weekly and snapshot.weekly.period_end:
        end = snapshot.weekly.period_end
    elif snapshot.monthly and snapshot.monthly.period_end:
        end = snapshot.monthly.period_end
    reset = _countdown_to(end, now)
    return weekly, monthly, reset


def format_menu_line(period: Optional[PeriodUsage], label: str, now: Optional[int] = None) -> str:
    now = int(time.time()) if now is None else now
    if period is None:
        return f"{label}: no data"
    if period.used_cents is not None and period.limit_cents is not None:
        money = f"{format_usd_cents(period.used_cents)} / {format_usd_cents(period.limit_cents)}"
        body = f"{money} ({period.used_percent}%)"
    else:
        body = f"{period.used_percent}%"
    if period.period_end:
        reset_time = _format_local_minute(period.period_end)
        return f"{label}: {body}  ⟳ {reset_time} ({_countdown_to(period.period_end, now)})"
    return f"{label}: {body}"


def format_on_demand_line(snapshot: GrokBillingSnapshot) -> Optional[str]:
    if snapshot.on_demand_cap_cents is None:
        return None
    cap = format_usd_cents(snapshot.on_demand_cap_cents)
    if snapshot.on_demand_used_cents is not None:
        return f"On-demand: {format_usd_cents(snapshot.on_demand_used_cents)} / {cap}"
    return f"On-demand cap: {cap}"


def format_updated_at(updated_at: str) -> str:
    return _format_local_minute(updated_at)


def _format_local_minute(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value.replace("T", " ")[:16]
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M")


def _countdown_to(period_end: Optional[str], now: int) -> str:
    if not period_end:
        return "--"
    try:
        parsed = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
    except ValueError:
        return "--"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    seconds = int(parsed.timestamp()) - now
    if seconds <= 0:
        return "soon"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def _with_query(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch or display Grok Build billing usage.")
    parser.add_argument("--once", action="store_true", help="Fetch live billing and refresh the cache.")
    parser.add_argument("--cache-only", action="store_true", help="Read the local cache only (no network).")
    parser.add_argument("--grok-home", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--url", default=None, help="Override billing base URL.")
    parser.add_argument("--json", action="store_true", help="Print snapshot as JSON.")
    args = parser.parse_args(argv)

    cache_path = args.cache or default_cache_path()
    grok_home = args.grok_home or default_grok_home()

    snapshot: Optional[GrokBillingSnapshot] = None
    try:
        if args.once:
            snapshot = poll_and_cache(grok_home=grok_home, cache_path=cache_path, url=args.url)
        elif args.cache_only:
            snapshot = read_cache(cache_path)
        else:
            snapshot = read_cache(cache_path)
            if snapshot is None:
                snapshot = poll_and_cache(grok_home=grok_home, cache_path=cache_path, url=args.url)
    except RuntimeError as exc:
        print(f"Grok -- ({exc})", file=sys.stderr)
        return 1

    if snapshot is None:
        print("Grok --")
        return 1

    if args.json:
        print(json.dumps(asdict(snapshot), indent=2))
    else:
        print(f"Grok {format_indicator_label(snapshot)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
