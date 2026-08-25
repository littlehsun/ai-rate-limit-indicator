#!/usr/bin/env python3
"""Terminal monitor for all four providers, read the way the iOS widget reads.

The numbers come from the snapshot `publish.py` serves on the Tailscale
network, never from the provider APIs. That is the same contract the Scriptable
widget in `mobile/` works to, and it is what makes this safe to run anywhere: no
credential is needed, no provider quota is spent, and one desktop refresh feeds
every screen watching it.

Every state the widget had to survive applies here too. The publishing machine
sleeps, so a stale snapshot and an unreachable host are normal rather than
exceptional, and the last numbers stay on screen with their age attached.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

VERSION = "0.1.0"

DEFAULT_ENDPOINT = "http://127.0.0.1:8477/usage.json"

# A publisher on a laptop can be mid-wake when we ask, so one refused
# connection is not proof it is down. The widget uses the same three tries.
FETCH_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 10.0

# A snapshot older than this is called out even when the publisher claims
# fresh, because the publisher can only report what it knew before it slept.
STALE_AFTER_SECONDS = 15 * 60

# Where a usage percentage stops being green. Shared with the web view so both
# surfaces agree with the widget and the macOS menu bar about what counts as bad.
WARNING_PERCENT = 70
DANGER_PERCENT = 90


class MonitorError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageWindow:
    id: str
    label: str
    # None means the backend stopped reporting this window rather than
    # reporting zero. Antigravity drops a window once its quota is spent, and a
    # nought there would read as "all of it still available".
    used_percent: Optional[int]
    resets_at: Optional[int] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: str
    label: str
    updated_at: Optional[str]
    windows: Tuple[UsageWindow, ...]
    status: str
    error: Optional[str]
    extras: Tuple[str, ...]


@dataclass(frozen=True)
class Snapshot:
    providers: Tuple[ProviderSnapshot, ...]
    # When this process obtained the payload, which is not when the desktop
    # last refreshed it. Both are shown: one says how long we have been
    # offline, the other how old the numbers themselves are.
    fetched_at: float
    reachable: bool

    def provider(self, name: str) -> Optional[ProviderSnapshot]:
        for candidate in self.providers:
            if candidate.provider == name:
                return candidate
        return None


PROVIDER_ORDER = ("codex", "claude", "grok", "gemini")

# Which window leads for each provider, mirroring the widget. Every lead is a
# weekly window: Codex and Grok only report one, so leading with 7D everywhere
# is what makes four bars of the same width comparable -- a 5H bar beside a 7D
# bar measures different things at identical length. Session windows still
# matter, so they ride along as numbers rather than disappearing.
PROVIDER_WINDOWS: Dict[str, Dict[str, Sequence[str]]] = {
    "claude": {"bar": ("7d",), "also": ("5h",)},
    # Antigravity reports a Gemini group and a Claude/GPT one. Only Gemini
    # leads; the Claude/GPT windows are listed underneath with their names
    # attached, the way the large widget does it.
    "gemini": {"bar": ("7d",), "also": ("5h",)},
}


@dataclass(frozen=True)
class Theme:
    """One palette, shared by the ANSI terminal view and the web view.

    The first seven are what the terminal draws with; a terminal supplies its
    own background, so it ignores the last four. The web view has to paint its
    own surfaces, and taking them from anywhere else is what made a dracula
    page look like a slate one with dracula progress bars on it.
    """

    heading: str
    accent: str
    muted: str
    good: str
    warning: str
    danger: str
    empty: str
    background: str
    surface: str
    border: str
    text: str


THEME_FIELDS = (
    "heading",
    "accent",
    "muted",
    "good",
    "warning",
    "danger",
    "empty",
    "background",
    "surface",
    "border",
    "text",
)


def default_themes_path() -> Path:
    override = os.environ.get("USAGE_DASHBOARD_THEMES")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent / "themes" / "themes.ini"


def load_themes(path: Optional[Path] = None) -> Dict[str, Theme]:
    """Read every palette through the one entry point that lists them.

    themes.ini names the files rather than holding the colours, so a palette is
    added by dropping a file beside it and listing it -- no editing of a theme
    someone else wrote, and no merge conflict in a file everyone touches. A
    later entry redefining an earlier name wins, which is how a shipped theme
    gets overridden without being edited.
    """

    index = path or default_themes_path()
    parser = configparser.ConfigParser()
    try:
        if not parser.read(index, encoding="utf-8"):
            raise MonitorError(f"no theme index at {index}")
    except configparser.Error as exc:
        raise MonitorError(f"cannot read {index}: {exc}") from exc

    raw = parser.get("themes", "include", fallback="") if parser.has_section("themes") else ""
    names = [entry.strip() for line in raw.splitlines() for entry in line.split(",")]
    themes: Dict[str, Theme] = {}
    for entry in names:
        if not entry:
            continue
        theme_path = (index.parent / entry).resolve()
        loaded = _read_theme(theme_path)
        if loaded is not None:
            themes[loaded[0]] = loaded[1]
    if not themes:
        raise MonitorError(f"{index} lists no usable theme")
    return themes


def _read_theme(path: Path) -> Optional[tuple[str, Theme]]:
    parser = configparser.ConfigParser()
    try:
        if not parser.read(path, encoding="utf-8"):
            raise MonitorError(f"theme file not found: {path}")
    except configparser.Error as exc:
        raise MonitorError(f"cannot read {path}: {exc}") from exc
    if not parser.has_section("theme"):
        raise MonitorError(f"{path} has no [theme] section")

    values = {key: parser.get("theme", key, fallback="").strip() for key in THEME_FIELDS}
    missing = [key for key, value in values.items() if not value]
    if len(missing) == len(THEME_FIELDS):
        # custom.ini ships with every key present and empty, so an untouched
        # one is a template rather than a mistake. Skipping it quietly is what
        # lets it stay listed in themes.ini until someone fills it in.
        return None
    if missing:
        raise MonitorError(
            f"{path} is missing a colour for: {', '.join(sorted(missing))}"
        )
    bad = [f"{key}={value}" for key, value in values.items() if not _is_hex_colour(value)]
    if bad:
        raise MonitorError(f"{path} has values that are not #rrggbb: {', '.join(bad)}")

    name = parser.get("theme", "name", fallback="").strip() or path.stem
    return name, Theme(**values)


def _is_hex_colour(value: str) -> bool:
    if len(value) != 7 or not value.startswith("#"):
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value[1:])

LANGUAGES = ("zh-TW", "en")
STRINGS: Dict[str, Dict[str, str]] = {
    "zh-TW": {
        "title": "AI 用量總覽",
        "updated": "快照 {time} · 每 {interval} 秒讀取一次 · theme: {theme}",
        "usage": "已使用 {used:>3}%  ·  剩餘 {remaining:>3}%",
        "unreported": "後端未回報此視窗",
        "reset_time": "重設時間",
        "no_reset": "重設時間  —",
        "next_check": "下次檢查：約 {seconds} 秒後 · Ctrl+C 結束",
        "offline": "無法連線發布端，顯示 {age}前的快取",
        "no_data": "沒有可用的快取，且無法連線發布端",
        "stale": "快照已 {age}未更新",
        "disabled": "未啟用任何 provider",
        "missing": "發布端沒有這個 provider 的資料",
        "expired": "已到期",
        "day": "天",
        "hour": "小時",
        "minute": "分",
        "after": "後",
    },
    "en": {
        "title": "AI usage overview",
        "updated": "Snapshot {time} · refresh every {interval}s · theme: {theme}",
        "usage": "Used {used:>3}%  ·  Remaining {remaining:>3}%",
        "unreported": "This window was not reported",
        "reset_time": "Resets at ",
        "no_reset": "Resets at   —",
        "next_check": "Next check in about {seconds}s · Ctrl+C to quit",
        "offline": "Publisher unreachable; showing a cache from {age} ago",
        "no_data": "No cached snapshot and the publisher is unreachable",
        "stale": "Snapshot is {age} old",
        "disabled": "No provider is enabled",
        "missing": "The publisher carries no data for this provider",
        "expired": "Expired",
        "day": "d",
        "hour": "h",
        "minute": "m",
        "after": " from now",
    },
}


@dataclass(frozen=True)
class Settings:
    endpoint: str = DEFAULT_ENDPOINT
    providers: Tuple[str, ...] = PROVIDER_ORDER
    theme: str = "dracula"
    language: str = "zh-TW"
    interval: int = 60
    color: bool = True
    clear: bool = True
    timeout: float = 8.0
    cache_file: Optional[Path] = None
    themes_file: Optional[Path] = None


def default_config_path() -> Path:
    override = os.environ.get("USAGE_DASHBOARD_CONFIG")
    if override:
        return Path(override).expanduser()
    # Keeping config.ini beside the script makes deployment a directory copy
    # while still allowing --config or the environment override.
    return Path(__file__).resolve().with_name("config.ini")


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    cache_home = Path(base) if base else Path.home() / ".cache"
    return cache_home / "rate-limit-indicator" / "dashboard-snapshot.json"


def load_settings(path: Path) -> Settings:
    parser = configparser.ConfigParser()
    if not path.is_file():
        return Settings()
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        raise MonitorError(f"cannot read {path}: {exc}") from exc

    monitor = parser["monitor"] if parser.has_section("monitor") else {}
    cache = monitor.get("cache_file", "").strip() if monitor else ""
    themes = monitor.get("themes", "").strip() if monitor else ""
    return Settings(
        endpoint=monitor.get("endpoint", DEFAULT_ENDPOINT) if monitor else DEFAULT_ENDPOINT,
        providers=_enabled_providers(parser),
        theme=monitor.get("theme", "dracula") if monitor else "dracula",
        language=monitor.get("language", "zh-TW") if monitor else "zh-TW",
        interval=parser.getint("monitor", "interval", fallback=60),
        color=parser.getboolean("monitor", "color", fallback=True),
        clear=parser.getboolean("monitor", "clear", fallback=True),
        timeout=parser.getfloat("monitor", "timeout", fallback=8.0),
        cache_file=Path(cache).expanduser() if cache else None,
        themes_file=Path(themes).expanduser() if themes else None,
    )


def _enabled_providers(parser: configparser.ConfigParser) -> Tuple[str, ...]:
    """Read the per-provider switches, keeping the canonical display order."""

    if not parser.has_section("providers"):
        return PROVIDER_ORDER
    enabled = []
    for name in PROVIDER_ORDER:
        if parser.getboolean("providers", name, fallback=True):
            enabled.append(name)
    return tuple(enabled)


def parse_snapshot(
    payload: Mapping[str, Any],
    *,
    fetched_at: float,
    reachable: bool,
) -> Snapshot:
    rows = payload.get("providers")
    if not isinstance(rows, list):
        raise MonitorError("snapshot payload has no providers list")
    providers = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("provider")
        if not isinstance(name, str) or not name:
            continue
        providers.append(
            ProviderSnapshot(
                provider=name,
                label=str(row.get("label") or name.title()),
                updated_at=row.get("updated_at") if isinstance(row.get("updated_at"), str) else None,
                windows=tuple(
                    window
                    for window in (_parse_window(value) for value in row.get("windows") or [])
                    if window is not None
                ),
                status=str(row.get("status") or "unknown"),
                error=row.get("error") if isinstance(row.get("error"), str) else None,
                extras=tuple(str(extra) for extra in row.get("extras") or []),
            )
        )
    return Snapshot(
        providers=tuple(providers), fetched_at=fetched_at, reachable=reachable
    )


def _parse_window(value: Any) -> Optional[UsageWindow]:
    if not isinstance(value, dict):
        return None
    window_id = value.get("id")
    if not isinstance(window_id, str) or not window_id:
        return None
    used = value.get("used_percent")
    resets_at = value.get("resets_at")
    detail = value.get("detail")
    return UsageWindow(
        id=window_id,
        label=str(value.get("label") or window_id.upper()),
        used_percent=used if isinstance(used, int) and not isinstance(used, bool) else None,
        resets_at=resets_at if isinstance(resets_at, int) and not isinstance(resets_at, bool) else None,
        detail=detail if isinstance(detail, str) else None,
    )


def absent_window(window_id: str) -> UsageWindow:
    """Hold the slot for a window the backend stopped reporting.

    Leaving it out would shorten the block and move everything after it, so the
    slot stays and says it has nothing rather than reporting a figure nobody
    sent.
    """

    cadence = window_id.split("-")[-1].upper()
    return UsageWindow(id=window_id, label=cadence, used_percent=None)


def ordered_windows(provider: ProviderSnapshot) -> Tuple[UsageWindow, ...]:
    """Return the provider's windows with its lead first, slots held."""

    wanted = PROVIDER_WINDOWS.get(provider.provider)
    if wanted is None:
        return provider.windows
    by_id = {window.id: window for window in provider.windows}
    chosen: List[UsageWindow] = []
    for window_id in tuple(wanted["bar"]) + tuple(wanted["also"]):
        # Grok labels its weekly window `weekly` rather than `7d`, so a lead
        # that is not found falls back to whatever the provider reported first
        # -- the same fallback the widget relies on.
        chosen.append(by_id.pop(window_id, None) or absent_window(window_id))
    # Anything the provider reports beyond the lead pair keeps its own order.
    for window in provider.windows:
        if window.id in by_id:
            chosen.append(window)
    if all(window.used_percent is None for window in chosen) and provider.windows:
        return provider.windows
    return tuple(chosen)


def percent_label(window: UsageWindow) -> str:
    return "--" if window.used_percent is None else f"{window.used_percent}%"


class SnapshotClient:
    """Fetch the published snapshot, falling back to the last one we saw.

    Losing the tailnet must not blank the screen: the previous numbers are
    still the best answer available, as long as their age is visible.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = 8.0,
        cache_path: Optional[Path] = None,
        attempts: int = FETCH_ATTEMPTS,
        opener: Optional[Any] = None,
        sleep: Any = time.sleep,
    ):
        self.endpoint = endpoint
        self.timeout = timeout
        self.cache_path = cache_path or default_cache_path()
        self.attempts = attempts
        self._opener = opener or self._urlopen_json
        self._sleep = sleep

    def fetch(self, *, now: Optional[float] = None) -> Snapshot:
        now = time.time() if now is None else now
        last_error: Optional[Exception] = None
        for attempt in range(1, self.attempts + 1):
            try:
                payload = self._opener(self.endpoint, self.timeout)
            except Exception as exc:  # noqa: BLE001 - every failure means "use the cache"
                last_error = exc
                if attempt < self.attempts:
                    self._sleep(RETRY_DELAY_SECONDS)
                continue
            snapshot = parse_snapshot(payload, fetched_at=now, reachable=True)
            self._write_cache(payload, now)
            return snapshot

        cached = self._read_cache()
        if cached is None:
            raise MonitorError(f"cannot reach {self.endpoint}: {last_error}")
        payload, fetched_at = cached
        return parse_snapshot(payload, fetched_at=fetched_at, reachable=False)

    @staticmethod
    def _urlopen_json(endpoint: str, timeout: float) -> Dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            headers={"Accept": "application/json", "User-Agent": f"usage-dashboard/{VERSION}"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise MonitorError("snapshot payload is not an object")
        return payload

    def _write_cache(self, payload: Mapping[str, Any], now: float) -> None:
        try:
            self.cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.cache_path.with_name(f".{self.cache_path.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps({"fetched_at": now, "payload": payload}), encoding="utf-8"
            )
            os.replace(temporary, self.cache_path)
        except OSError:
            # A cache we cannot write costs us the offline fallback, not this
            # reading. Failing the whole poll over it would be worse.
            pass

    def _read_cache(self) -> Optional[Tuple[Dict[str, Any], float]]:
        try:
            cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
            payload = cached["payload"]
            fetched_at = float(cached["fetched_at"])
        except (OSError, ValueError, TypeError, KeyError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload, fetched_at


def snapshot_time(snapshot: Snapshot) -> Optional[float]:
    """The moment the desktop last refreshed, not the moment we fetched it."""

    newest: Optional[float] = None
    for provider in snapshot.providers:
        parsed = _parse_iso8601(provider.updated_at)
        if parsed is not None and (newest is None or parsed > newest):
            newest = parsed
    return newest


def _parse_iso8601(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class TerminalRenderer:
    RESET = "\033[0m"

    def __init__(
        self,
        color: bool,
        clear: bool,
        theme: str = "dracula",
        language: str = "zh-TW",
        themes: Optional[Dict[str, Theme]] = None,
    ):
        # Palettes come from themes.ini rather than from this module, so the
        # caller can hand in an already-loaded set instead of re-reading the
        # files on every construction.
        available = themes if themes is not None else load_themes()
        if theme not in available:
            raise ValueError(
                f"unknown theme: {theme} (available: {', '.join(sorted(available))})"
            )
        if language not in LANGUAGES:
            raise ValueError(f"unknown language: {language}")
        self.color = color
        self.clear = clear
        self.theme_name = theme
        self.theme = available[theme]
        self.language = language
        self.text = STRINGS[language]

    @staticmethod
    def _ansi(hex_color: str) -> str:
        value = hex_color.lstrip("#")
        red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
        return f"\033[38;2;{red};{green};{blue}m"

    def _paint(self, text: str, color: str) -> str:
        if not self.color or not text:
            return text
        return f"{self._ansi(color)}{text}{self.RESET}"

    @staticmethod
    def _local_time(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    def _relative_time(self, seconds: float) -> str:
        if seconds <= 0:
            return self.text["expired"]
        days, rest = divmod(int(seconds), 86400)
        hours, rest = divmod(rest, 3600)
        minutes = rest // 60
        parts: List[str] = []
        if days:
            parts.append(f"{days} {self.text['day']}")
        if hours:
            parts.append(f"{hours} {self.text['hour']}")
        if minutes or not parts:
            parts.append(f"{minutes} {self.text['minute']}")
        return " ".join(parts[:2]) + self.text["after"]

    def _age(self, seconds: float) -> str:
        return self._relative_time(seconds).removesuffix(self.text["after"])

    def _color_for(self, used_percent: int) -> str:
        return self.theme_color(used_percent, self.theme)

    @staticmethod
    def theme_color(used_percent: int, theme: Theme) -> str:
        # 70 and 90 rather than the Codex monitor's 60 and 85, because these
        # numbers sit next to the iOS widget and the macOS menu bar on the same
        # desk and one percentage must not be amber in one and green in another.
        # UsageColor in the Swift app draws the same two lines.
        if used_percent >= DANGER_PERCENT:
            return theme.danger
        if used_percent >= WARNING_PERCENT:
            return theme.warning
        return theme.good

    def _progress(self, used_percent: Optional[int], width: int) -> str:
        # A window nobody reported gets an empty track. Drawing a zero-length
        # fill in the usual green would claim the quota is untouched.
        if used_percent is None:
            return f"[{self._paint('░' * width, self.theme.empty)}]"
        filled = round(width * min(used_percent, 100) / 100)
        used = "█" * filled
        free = "░" * (width - filled)
        return (
            f"[{self._paint(used, self._color_for(used_percent))}"
            f"{self._paint(free, self.theme.empty)}]"
        )

    def _window_lines(self, window: UsageWindow, now: float, bar_width: int) -> List[str]:
        name = self._paint(f"{window.label:<14}", self.theme.accent)
        if window.used_percent is None:
            return [
                f"    {name}{self._progress(None, bar_width)}  "
                + self._paint(self.text["unreported"], self.theme.muted)
            ]
        lines = [
            f"    {name}{self._progress(window.used_percent, bar_width)}  "
            + self.text["usage"].format(
                used=window.used_percent, remaining=100 - window.used_percent
            )
        ]
        if window.resets_at is None:
            lines.append(f"    {'':<14}{self._paint(self.text['no_reset'], self.theme.muted)}")
        else:
            lines.append(
                f"    {'':<14}"
                + self._paint(
                    f"{self.text['reset_time']}  {self._local_time(window.resets_at)}  "
                    f"({self._relative_time(window.resets_at - now)})",
                    self.theme.muted,
                )
            )
        return lines

    def _provider_lines(
        self, name: str, provider: Optional[ProviderSnapshot], now: float, bar_width: int
    ) -> List[str]:
        if provider is None:
            return [
                f"  {self._paint(name.title(), self.theme.heading)}",
                f"    {self._paint(self.text['missing'], self.theme.muted)}",
                "",
            ]
        status = provider.status
        status_color = self.theme.good if status == "fresh" else self.theme.warning
        lines = [
            f"  {self._paint(provider.label, self.theme.heading)}  "
            + self._paint(status, status_color)
        ]
        for window in ordered_windows(provider):
            lines.extend(self._window_lines(window, now, bar_width))
        for extra in provider.extras:
            lines.append(f"    {self._paint(extra, self.theme.muted)}")
        if provider.error:
            lines.append(f"    {self._paint(provider.error, self.theme.danger)}")
        lines.append("")
        return lines

    def render(
        self,
        snapshot: Snapshot,
        providers: Sequence[str],
        *,
        interval: int = 60,
        next_check: Optional[int] = None,
        now: Optional[float] = None,
    ) -> str:
        now = time.time() if now is None else now
        terminal_width = shutil.get_terminal_size((80, 24)).columns
        bar_width = max(12, min(36, terminal_width - 46))

        taken_at = snapshot_time(snapshot)
        header = self.text["updated"].format(
            time=self._local_time(taken_at) if taken_at else "—",
            interval=interval,
            theme=self.theme_name,
        )
        lines = [
            "",
            f"  {self._paint(self.text['title'], self.theme.heading)}",
            f"  {self._paint(header, self.theme.muted)}",
        ]

        if not snapshot.reachable:
            lines.append(
                f"  {self._paint(self.text['offline'].format(age=self._age(now - snapshot.fetched_at)), self.theme.warning)}"
            )
        elif taken_at is not None and now - taken_at > STALE_AFTER_SECONDS:
            lines.append(
                f"  {self._paint(self.text['stale'].format(age=self._age(now - taken_at)), self.theme.warning)}"
            )
        lines.append("")

        if not providers:
            lines.append(f"  {self._paint(self.text['disabled'], self.theme.warning)}")
            lines.append("")

        for name in providers:
            lines.extend(self._provider_lines(name, snapshot.provider(name), now, bar_width))

        if next_check is not None:
            lines.append(
                f"  {self._paint(self.text['next_check'].format(seconds=next_check), self.theme.muted)}"
            )
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch Codex, Claude, Grok and Gemini usage from one published snapshot."
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--endpoint", default=None, help="publish.py URL to read")
    parser.add_argument(
        "--providers",
        default=None,
        help="comma separated subset to show, e.g. codex,claude",
    )
    # No static choices: the palettes live in themes.ini and a user can add one
    # without this file knowing about it, so the name is checked after loading.
    parser.add_argument("--theme", default=None)
    parser.add_argument("--themes", type=Path, default=None, help="themes.ini to read")
    parser.add_argument(
        "--list-themes", action="store_true", help="print the available themes and exit"
    )
    parser.add_argument("--language", choices=LANGUAGES, default=None)
    parser.add_argument("--interval", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--once", action="store_true", help="render once and exit")
    parser.add_argument("--json", action="store_true", help="print the snapshot as JSON")
    parser.add_argument("--no-color", dest="color", action="store_false", default=None)
    parser.add_argument("--no-clear", dest="clear", action="store_false", default=None)
    return parser


def resolve_settings(args: argparse.Namespace) -> Settings:
    configured = load_settings(args.config or default_config_path())
    providers = configured.providers
    if args.providers is not None:
        wanted = {name.strip().lower() for name in args.providers.split(",") if name.strip()}
        unknown = wanted - set(PROVIDER_ORDER)
        if unknown:
            raise MonitorError(f"unknown provider(s): {', '.join(sorted(unknown))}")
        providers = tuple(name for name in PROVIDER_ORDER if name in wanted)
    return Settings(
        endpoint=args.endpoint or configured.endpoint,
        providers=providers,
        theme=args.theme or configured.theme,
        language=args.language or configured.language,
        interval=args.interval if args.interval is not None else configured.interval,
        color=configured.color if args.color is None else args.color,
        clear=configured.clear if args.clear is None else args.clear,
        timeout=args.timeout if args.timeout is not None else configured.timeout,
        cache_file=configured.cache_file,
        themes_file=args.themes or configured.themes_file,
    )


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = resolve_settings(args)
    except MonitorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if settings.interval < 1:
        print("error: --interval must be at least 1 second", file=sys.stderr)
        return 2

    try:
        themes = load_themes(settings.themes_file)
    except MonitorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list_themes:
        for name in sorted(themes):
            marker = "*" if name == settings.theme else " "
            print(f"{marker} {name}")
        return 0

    client = SnapshotClient(
        settings.endpoint, timeout=settings.timeout, cache_path=settings.cache_file
    )
    try:
        renderer = TerminalRenderer(
            color=settings.color,
            clear=settings.clear,
            theme=settings.theme,
            language=settings.language,
            themes=themes,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    while True:
        try:
            snapshot = client.fetch()
        except MonitorError as exc:
            print(f"{renderer.text['no_data']}: {exc}", file=sys.stderr)
            if args.once or args.json:
                return 1
            time.sleep(min(5, settings.interval))
            continue

        if args.json:
            print(snapshot_as_json(snapshot, settings.providers))
            return 0

        if settings.clear and not args.once:
            print("\033[H\033[J", end="")
        print(
            renderer.render(
                snapshot,
                settings.providers,
                interval=settings.interval,
                next_check=None if args.once else settings.interval,
            )
        )
        if args.once:
            return 0
        try:
            time.sleep(settings.interval)
        except KeyboardInterrupt:
            return 0


def snapshot_as_json(snapshot: Snapshot, providers: Sequence[str]) -> str:
    rows = []
    for name in providers:
        provider = snapshot.provider(name)
        if provider is None:
            continue
        rows.append(
            {
                "provider": provider.provider,
                "label": provider.label,
                "updated_at": provider.updated_at,
                "status": provider.status,
                "error": provider.error,
                "extras": list(provider.extras),
                "windows": [
                    {
                        "id": window.id,
                        "label": window.label,
                        "used_percent": window.used_percent,
                        "resets_at": window.resets_at,
                    }
                    for window in ordered_windows(provider)
                ],
            }
        )
    return json.dumps(
        {
            "providers": rows,
            "fetched_at": snapshot.fetched_at,
            "reachable": snapshot.reachable,
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
