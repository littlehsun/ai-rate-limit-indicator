from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from models import ProviderSnapshot, UsageWindow, parse_timestamp


HERE = Path(__file__).resolve().parent
COLLECTORS = HERE / "collectors"
REPO_ROOT = HERE.parent

if COLLECTORS.is_dir():
    sys.path.append(str(COLLECTORS))
else:
    for path in (
        REPO_ROOT / "providers/codex/ubuntu-indicator",
        REPO_ROOT / "providers/grok/ubuntu-indicator",
        REPO_ROOT / "providers/gemini/ubuntu-indicator",
    ):
        sys.path.append(str(path))


PROVIDER_ORDER = ("codex", "claude", "grok", "gemini")
PROVIDER_LABELS = {
    "codex": "Codex",
    "claude": "Claude",
    "grok": "Grok",
    "gemini": "Gemini",
}


def default_manager_config() -> Path:
    return Path.home() / ".config" / "rate-limit-indicator" / "providers.env"


def _normalize_config_key(raw_key: str) -> str:
    key_parts = raw_key.strip().split(None, 1)
    if len(key_parts) == 2 and key_parts[0].lower() == "export":
        return key_parts[1].upper()
    return raw_key.strip().upper()


def read_manager_config(path: Optional[Path] = None) -> dict[str, str]:
    config_path = path or Path(
        os.environ.get("RATE_LIMIT_INDICATOR_CONFIG", default_manager_config())
    )
    values: dict[str, str] = {}
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.split("#", 1)[0].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        normalized = value.strip()
        if (
            len(normalized) >= 2
            and normalized[0] == normalized[-1]
            and normalized[0] in {"'", '"'}
        ):
            normalized = normalized[1:-1]
        values[_normalize_config_key(key)] = normalized
    return values


def display_settings(
    values: Optional[dict[str, str]] = None,
) -> tuple[str, tuple[str, ...]]:
    config = values if values is not None else read_manager_config()
    mode = config.get("DISPLAY_MODE", "").lower()
    providers = tuple(
        provider
        for provider in config.get("DISPLAY_PROVIDERS", "").lower().split(",")
        if provider in PROVIDER_ORDER
    )
    if mode not in {"auto", "custom"}:
        legacy = config.get("DISPLAY_PROVIDER", "auto").lower()
        if legacy in PROVIDER_ORDER:
            mode = "custom"
            providers = (legacy,)
        else:
            mode = "auto"
    if not providers:
        providers = tuple(
            provider
            for provider in PROVIDER_ORDER
            if config.get(provider.upper(), "false").lower()
            in {"1", "true", "yes", "on"}
        )
    return mode, providers


def dropdown_providers(
    values: Optional[dict[str, str]] = None,
) -> tuple[str, ...]:
    config = values if values is not None else read_manager_config()
    if "DROPDOWN_PROVIDERS" not in config:
        return tuple(
            provider
            for provider in PROVIDER_ORDER
            if config.get(provider.upper(), "false").lower()
            in {"1", "true", "yes", "on"}
        )
    return tuple(
        provider
        for provider in config["DROPDOWN_PROVIDERS"].lower().split(",")
        if provider in PROVIDER_ORDER
    )


def provider_display_order(
    values: Optional[dict[str, str]] = None,
) -> tuple[str, ...]:
    config = values if values is not None else read_manager_config()
    configured = [
        provider
        for provider in config.get("PROVIDER_ORDER", "").lower().split(",")
        if provider in PROVIDER_ORDER
    ]
    fallbacks = [
        *display_settings(config)[1],
        *dropdown_providers(config),
        *PROVIDER_ORDER,
    ]
    for provider in fallbacks:
        if provider not in configured:
            configured.append(provider)
    return tuple(configured)


def write_display_settings(
    mode: str,
    providers: tuple[str, ...],
    path: Optional[Path] = None,
    dropdown: Optional[tuple[str, ...]] = None,
    provider_order: Optional[tuple[str, ...]] = None,
) -> Path:
    config_path = path or Path(
        os.environ.get("RATE_LIMIT_INDICATOR_CONFIG", default_manager_config())
    )
    updates = {
        "DISPLAY_MODE": "auto" if mode == "auto" else "custom",
        "DISPLAY_PROVIDERS": ",".join(
            provider for provider in providers if provider in PROVIDER_ORDER
        ),
    }
    if dropdown is not None:
        updates["DROPDOWN_PROVIDERS"] = ",".join(
            provider for provider in dropdown if provider in PROVIDER_ORDER
        )
    if provider_order is not None:
        updates["PROVIDER_ORDER"] = ",".join(
            provider for provider in provider_order if provider in PROVIDER_ORDER
        )
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    written: set[str] = set()
    output = []
    for line in lines:
        stripped = line.split("#", 1)[0].strip()
        key = _normalize_config_key(stripped.split("=", 1)[0]) if "=" in stripped else ""
        if key not in updates:
            output.append(line)
            continue
        if key not in written:
            output.append(f"{key}={updates[key]}")
            written.add(key)
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}={value}")
    config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path.parent.chmod(0o700)
    tmp_path = config_path.with_name(f".{config_path.name}.tmp")
    try:
        tmp_path.write_text("\n".join(output) + "\n", encoding="utf-8")
        tmp_path.chmod(0o600)
        os.replace(tmp_path, config_path)
        config_path.chmod(0o600)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
    return config_path


def enabled_providers(path: Optional[Path] = None) -> tuple[str, ...]:
    values = read_manager_config(path)
    enabled = []
    for provider in PROVIDER_ORDER:
        value = values.get(provider.upper(), "false").lower()
        if value in {"1", "true", "yes", "on"}:
            enabled.append(provider)
    return tuple(enabled)


def load_snapshots(
    providers: Optional[tuple[str, ...]] = None,
) -> tuple[ProviderSnapshot, ...]:
    selected = providers if providers is not None else enabled_providers()
    loaders: dict[str, Callable[[], ProviderSnapshot]] = {
        "codex": load_codex,
        "claude": load_claude,
        "grok": load_grok,
        "gemini": load_gemini,
    }
    snapshots = []
    for provider in PROVIDER_ORDER:
        if provider not in selected:
            continue
        try:
            snapshots.append(loaders[provider]())
        except Exception as exc:
            snapshots.append(
                ProviderSnapshot(
                    provider=provider,
                    label=PROVIDER_LABELS[provider],
                    updated_at=None,
                    windows=(),
                    status="error",
                    error=str(exc),
                )
            )
    return tuple(snapshots)


def load_codex() -> ProviderSnapshot:
    from codex_rate import default_codex_home, find_latest_snapshot
    from wham import (
        default_wham_cache_path,
        describe_missing_token,
        format_wham_timestamp,
        read_wham_snapshot,
        resolve_access_token,
    )

    source = read_manager_config().get("CODEX_RATE_SOURCE", "local").lower()
    uses_wham = source in {"auto", "wham"}
    snapshot = None
    if uses_wham:
        snapshot = read_wham_snapshot(Path(default_wham_cache_path()))
    if snapshot is None and source != "wham":
        snapshot = find_latest_snapshot(
            Path(os.environ.get("CODEX_HOME", default_codex_home()))
        )

    # The wham cache only moves while the codex CLI keeps a live token, so an
    # expired one explains a blank or frozen panel better than silence does.
    # Local rollout data needs no token, so only ask once wham has let us down.
    error = None
    if uses_wham and (snapshot is None or _freshness(snapshot.updated_at) != "fresh"):
        if resolve_access_token() is None:
            error = describe_missing_token()

    if snapshot is None:
        return _no_data("codex", error)

    windows = []
    if snapshot.five_hour:
        windows.append(
            UsageWindow(
                id="5h",
                label="5H",
                used_percent=snapshot.five_hour.used_percent,
                resets_at=snapshot.five_hour.resets_at,
            )
        )
    if snapshot.weekly:
        windows.append(
            UsageWindow(
                id="7d",
                label="7D",
                used_percent=snapshot.weekly.used_percent,
                resets_at=snapshot.weekly.resets_at,
            )
        )
    extras = []
    if snapshot.reset_credits_available is not None:
        extras.append(f"Reset credits: {snapshot.reset_credits_available}")
    else:
        extras.append("Reset credits: --")
    for index, expires_at in enumerate(snapshot.reset_credit_expirations, start=1):
        extras.append(f"{index}. expires {format_wham_timestamp(expires_at)}")
    return ProviderSnapshot(
        provider="codex",
        label="Codex",
        updated_at=snapshot.updated_at,
        windows=tuple(windows),
        status=_freshness(snapshot.updated_at),
        error=error,
        extras=tuple(extras),
    )


def load_claude() -> ProviderSnapshot:
    from claude_oauth import (
        ClaudeOAuthUnavailable,
        fetch_oauth_snapshot,
        read_cache,
        write_cache,
    )

    # Claude Code owns refreshing this token, and it only does so while it runs.
    # Keeping the last snapshot means an idle machine shows stale numbers with a
    # reason instead of an empty panel.
    error = None
    try:
        oauth_snapshot = fetch_oauth_snapshot()
        write_cache(oauth_snapshot)
    except ClaudeOAuthUnavailable as exc:
        error = str(exc)
        oauth_snapshot = read_cache()

    if oauth_snapshot is None:
        return _no_data("claude", error)

    windows = tuple(
        UsageWindow(
            id=window.id,
            label=window.id.upper(),
            used_percent=window.used_percent,
            resets_at=parse_timestamp(window.resets_at),
        )
        for window in oauth_snapshot.windows
    )
    return ProviderSnapshot(
        "claude",
        "Claude",
        oauth_snapshot.updated_at,
        windows,
        status=_freshness(oauth_snapshot.updated_at),
        error=error,
    )


def load_grok() -> ProviderSnapshot:
    from grok_rate import (
        default_cache_path,
        format_usd_cents,
        read_access_token,
        read_cache,
    )

    snapshot = read_cache(Path(os.environ.get("GROK_RATE_CACHE", default_cache_path())))
    # The cache only refreshes while the CLI keeps a live token, so an expired
    # one is why the panel is stuck rather than an unrelated poll failure.
    error = None if read_access_token() else "Grok sign-in expired; run the grok CLI"
    if snapshot is None:
        return _no_data("grok", error)
    windows = []
    if snapshot.weekly:
        windows.append(
            UsageWindow(
                id="weekly",
                label="7D",
                used_percent=snapshot.weekly.used_percent,
                resets_at=parse_timestamp(snapshot.weekly.period_end),
            )
        )
    # monthlyLimit/used belong to Grok's deprecated billing model.  Unified
    # accounts commonly report a non-zero used amount with a zero limit, so
    # exposing that as a quota window produces a misleading "$x / $0 (0%)".
    # Keep parsing it in the collector for old-cache compatibility, but only
    # expose the current weekly quota from the normalized adapter.
    extras = [
        f"{name}: {'--' if pct is None else f'{pct}%'}"
        for name, pct in snapshot.product_usage
    ]
    prepaid_balance = getattr(snapshot, "prepaid_balance_cents", None)
    if prepaid_balance:
        extras.append(f"Credits: {format_usd_cents(abs(prepaid_balance))}")
        if getattr(snapshot, "auto_topup_enabled", False):
            auto_topup_amount = getattr(snapshot, "auto_topup_amount_cents", None)
            if auto_topup_amount:
                extras.append(
                    f"Auto topup: {format_usd_cents(abs(auto_topup_amount))}"
                )
            monthly_topup_cap = getattr(
                snapshot, "auto_topup_monthly_cap_cents", None
            )
            if monthly_topup_cap:
                extras.append(
                    "Max monthly topup: "
                    f"{format_usd_cents(abs(monthly_topup_cap))}"
                )
    on_demand_cap = getattr(snapshot, "on_demand_cap_cents", None)
    if on_demand_cap and on_demand_cap > 0:
        on_demand_used = getattr(snapshot, "on_demand_used_cents", None)
        if on_demand_used is None:
            extras.append(f"Pay-as-you-go cap: {format_usd_cents(on_demand_cap)}")
        else:
            extras.append(
                "Pay-as-you-go: "
                f"{format_usd_cents(abs(on_demand_used))} / "
                f"{format_usd_cents(on_demand_cap)}"
            )
    return ProviderSnapshot(
        "grok",
        "Grok",
        snapshot.updated_at,
        tuple(windows),
        status=_freshness(snapshot.updated_at),
        error=error,
        extras=tuple(extras),
    )


def load_gemini() -> ProviderSnapshot:
    from agy_rate import (
        fetch_quota_snapshot,
        fetch_quota_with_cli,
        read_cache,
        write_cache,
    )

    # Antigravity owns the quota endpoint. When it is not listening the cached
    # numbers simply stop moving, and the reason is the useful part to show.
    error = None
    try:
        agy_snapshot = fetch_quota_snapshot()
        write_cache(agy_snapshot)
    except RuntimeError as exc:
        error = str(exc)
        agy_snapshot = read_cache()
        # Antigravity only listens while it runs, so an idle machine has no
        # endpoint at all. Starting it briefly is the same nudge we use for the
        # Grok CLI, and it stays opt-in for the same reason. Starting it to
        # refill a cache that is still fresh would burn a process for numbers
        # we already have, so the stale cache is what earns the spawn.
        if agy_snapshot is None or _freshness(agy_snapshot.updated_at) != "fresh":
            auto_start = read_manager_config().get("AGY_AUTO_START", "false").lower()
            started = fetch_quota_with_cli(
                enabled=auto_start in {"1", "true", "yes", "on"}
            )
            if started is not None:
                write_cache(started)
                agy_snapshot = started
                error = None

    if agy_snapshot is None:
        return _no_data("gemini", error)

    # Antigravity not listening is only worth saying once the cached numbers
    # have gone stale. Auto-start stops it on purpose after each read, so a
    # fresh cache alongside "not running" is our own doing, not a fault.
    if _freshness(agy_snapshot.updated_at) == "fresh":
        error = None

    windows = tuple(
        UsageWindow(
            id=(
                window.cadence
                if window.group_id == "gemini"
                else f"{window.group_id}-{window.cadence}"
            ),
            label=f"{window.group_label} {window.cadence.upper()}",
            used_percent=window.used_percent,
            resets_at=parse_timestamp(window.reset_at),
        )
        for window in agy_snapshot.windows
    )
    return ProviderSnapshot(
        "gemini",
        "Gemini",
        agy_snapshot.updated_at,
        windows,
        status=_freshness(agy_snapshot.updated_at),
        error=error,
    )


def _no_data(provider: str, reason: Optional[str] = None) -> ProviderSnapshot:
    return ProviderSnapshot(
        provider=provider,
        label=PROVIDER_LABELS[provider],
        updated_at=None,
        windows=(),
        status="no_data",
        error=reason,
    )


def _freshness(updated_at: Optional[str], max_age_seconds: int = 600) -> str:
    updated_ts = parse_timestamp(updated_at)
    if updated_ts is None:
        return "stale"
    return "stale" if int(time.time()) - updated_ts > max_age_seconds else "fresh"
