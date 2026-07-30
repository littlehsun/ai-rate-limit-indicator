#!/usr/bin/env python3
from __future__ import annotations

import base64
import html
import os
import signal
import sys
import time
import traceback
from contextlib import suppress
from pathlib import Path
from typing import Optional, Sequence

import gi

gi.require_version("AppIndicator3", "0.1")
gi.require_version("Gtk", "3.0")
from gi.repository import AppIndicator3, GLib, Gtk  # noqa: E402

from codex_rate import (
    default_codex_home,
    find_latest_snapshot,
    format_indicator_label,
    format_indicator_parts,
    format_menu_line,
    format_updated_at,
    max_used_percent,
)
from wham import default_wham_cache_path, format_reset_credit_lines, read_wham_snapshot


POLL_INTERVAL_SECONDS = 60
ICON_NAMES = ["codex-rate-0", "codex-rate-1"]
FALLBACK_ICON_NAME = "view-refresh-symbolic"
NEUTRAL_TEXT_COLOR = "#FFFFFF"
COLORS = {
    "green": "#00AF50",
    "yellow": "#E6C800",
    "red": "#FF5555",
}
TextSegment = tuple[str, str]
IconText = str | Sequence[TextSegment]
CODEX_LOGO_PATH = Path(__file__).parent / "assets" / "codex-logo.png"


def _load_logo_data_uri() -> Optional[str]:
    try:
        encoded = base64.b64encode(CODEX_LOGO_PATH.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/png;base64,{encoded}"


CODEX_LOGO_DATA_URI = _load_logo_data_uri()


def make_icon_svg(text: IconText, color: str) -> str:
    fill = _resolve_color(color)
    plain = _plain_text(text)
    spans = _render_text_spans(text, color)
    width = max(98, int(len(plain) * 7.5) + 36)
    brand_mark = (
        f'<image x="1" y="1" width="20" height="20" '
        f'href="{CODEX_LOGO_DATA_URI}"/>'
        if CODEX_LOGO_DATA_URI
        else f'<circle cx="8" cy="11" r="6" fill="{fill}"/>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="22">'
        f"{brand_mark}"
        f'<text x="25" y="15" font-family="monospace,DejaVu Sans Mono" '
        f'font-size="11" xml:space="preserve">{spans}</text>'
        f"</svg>"
    )


def _plain_text(text: IconText) -> str:
    if isinstance(text, str):
        return text
    return "".join(segment for segment, _ in text)


def _render_text_spans(text: IconText, default_color: str) -> str:
    segments = [(text, default_color)] if isinstance(text, str) else text
    return "".join(
        f'<tspan fill="{_resolve_color(color)}">{html.escape(segment)}</tspan>'
        for segment, color in segments
    )


def _resolve_color(color: str) -> str:
    return COLORS.get(color, color)


def _create_icon_dir() -> Optional[Path]:
    candidates: list[Path] = []
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        candidates.append(Path(runtime_dir) / "codex-rate-indicator")
    candidates.append(Path.home() / ".cache" / "codex-rate-indicator")

    for path in candidates:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            return path
        except OSError as exc:
            print(f"codex-rate-indicator: cannot create icon dir {path}: {exc}", file=sys.stderr)
    return None


def _log_exception(context: str) -> None:
    print(f"codex-rate-indicator: {context}", file=sys.stderr)
    traceback.print_exc()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class CodexRateIndicator:
    def __init__(self) -> None:
        self.codex_home = Path(os.environ.get("CODEX_HOME", default_codex_home()))
        self.source = os.environ.get("CODEX_RATE_SOURCE", "local").lower()
        self.show_five_hour = _env_flag("CODEX_RATE_SHOW_5H")
        self.wham_cache = Path(os.environ.get("CODEX_RATE_WHAM_CACHE", default_wham_cache_path()))
        self._icon_idx = 0
        self.icon_dir = _create_icon_dir()
        initial_icon = self._write_icon(ICON_NAMES[0], "Codex --", "green") or FALLBACK_ICON_NAME

        if self.icon_dir is None:
            self.indicator = AppIndicator3.Indicator.new(
                "codex-rate-indicator",
                initial_icon,
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            )
        else:
            self.indicator = AppIndicator3.Indicator.new_with_path(
                "codex-rate-indicator",
                initial_icon,
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
                str(self.icon_dir),
            )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()

        self.item_5h = Gtk.MenuItem(label="5h: --")
        self.item_5h.set_sensitive(False)
        self.menu.append(self.item_5h)

        self.item_weekly = Gtk.MenuItem(label="Weekly: --")
        self.item_weekly.set_sensitive(False)
        self.menu.append(self.item_weekly)

        self.item_updated = Gtk.MenuItem(label="Updated: --")
        self.item_updated.set_sensitive(False)
        self.menu.append(self.item_updated)

        self.item_reset_separator = Gtk.SeparatorMenuItem()
        self.menu.append(self.item_reset_separator)

        self.item_reset_credits = Gtk.MenuItem(label="Reset credits: --")
        self.item_reset_credits.set_sensitive(False)
        self.menu.append(self.item_reset_credits)

        self.credit_items = []
        for idx in range(1, 6):
            item = Gtk.MenuItem(label=f"{idx}. --")
            item.set_sensitive(False)
            self.menu.append(item)
            self.credit_items.append(item)

        self.menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect("activate", lambda _: Gtk.main_quit())
        self.menu.append(item_quit)

        self.menu.show_all()
        if not self.show_five_hour:
            self.item_5h.hide()
        self.indicator.set_menu(self.menu)

        GLib.timeout_add(500, self._initial_update)
        GLib.timeout_add_seconds(POLL_INTERVAL_SECONDS, self._poll)

    def _initial_update(self) -> bool:
        self._safe_update("initial update failed")
        return False

    def _poll(self) -> bool:
        self._safe_update("poll update failed")
        return True

    def _safe_update(self, context: str) -> None:
        try:
            self.update()
        except Exception:
            _log_exception(context)

    def update(self) -> None:
        snapshot = self._find_snapshot()
        if snapshot is None:
            self._set_icon("Codex --", "green")
            if self.show_five_hour:
                self.item_5h.show()
                self.item_5h.set_label("5h: no data")
            else:
                self.item_5h.hide()
            weekly_name = "Weekly" if self.show_five_hour else "7d"
            self.item_weekly.set_label(f"{weekly_name}: no data")
            self.item_updated.set_label("Updated: --")
            self._hide_optional_wham_items()
            return

        now = int(time.time())
        label = format_indicator_label(
            snapshot,
            now=now,
            show_five_hour=self.show_five_hour,
        )
        usage = (
            max_used_percent(snapshot)
            if self.show_five_hour
            else snapshot.weekly.used_percent if snapshot.weekly else 0
        )
        color = self._color_for_pct(usage)
        self._set_icon(self._indicator_text_segments(snapshot, now), color, description=label)

        if self.show_five_hour:
            self.item_5h.show()
            self.item_5h.set_label(format_menu_line(snapshot.five_hour, "5h", now=now))
        else:
            self.item_5h.hide()
        weekly_name = "Weekly" if self.show_five_hour else "7d"
        self.item_weekly.set_label(format_menu_line(snapshot.weekly, weekly_name, now=now))
        self.item_updated.set_label(f"Updated: {format_updated_at(snapshot.updated_at)}")
        self._update_optional_wham_items(snapshot)

    def _find_snapshot(self):
        if self.source == "wham":
            return read_wham_snapshot(self.wham_cache)
        if self.source == "auto":
            return read_wham_snapshot(self.wham_cache) or find_latest_snapshot(self.codex_home)
        return find_latest_snapshot(self.codex_home)

    def _set_icon(self, text: IconText, color: str, description: Optional[str] = None) -> None:
        self._icon_idx ^= 1
        icon_name = self._write_icon(ICON_NAMES[self._icon_idx], text, color)
        if icon_name is None:
            icon_name = FALLBACK_ICON_NAME
        try:
            label = description or _plain_text(text)
            self.indicator.set_icon_full(icon_name, f"Codex rate limit: {label}")
        except Exception:
            _log_exception("icon update failed")

    def _write_icon(self, name: str, text: IconText, color: str) -> Optional[str]:
        if self.icon_dir is None:
            return None

        icon_path = self.icon_dir / f"{name}.svg"
        tmp_path = self.icon_dir / f".{name}.svg.tmp"
        try:
            tmp_path.write_text(make_icon_svg(text, color), encoding="utf-8")
            os.replace(tmp_path, icon_path)
            return name
        except OSError as exc:
            with suppress(OSError):
                tmp_path.unlink()
            print(f"codex-rate-indicator: cannot write icon {icon_path}: {exc}", file=sys.stderr)
            return None

    def _indicator_text_segments(self, snapshot, now: int) -> list[TextSegment]:
        five_hour, weekly, reset = format_indicator_parts(
            snapshot,
            now=now,
            show_five_hour=self.show_five_hour,
        )
        five_hour_color = self._color_for_pct(snapshot.five_hour.used_percent if snapshot.five_hour else 0)
        weekly_color = self._color_for_pct(snapshot.weekly.used_percent if snapshot.weekly else 0)
        if self.show_five_hour:
            segments = [
                (five_hour, five_hour_color),
                ("|", NEUTRAL_TEXT_COLOR),
                (weekly, weekly_color),
            ]
        else:
            segments = [(weekly, weekly_color)]
        if snapshot.reset_credits_available is not None:
            segments.append((f" R{snapshot.reset_credits_available}", NEUTRAL_TEXT_COLOR))
        segments.append((f"  ⟳{reset}", NEUTRAL_TEXT_COLOR))
        return segments

    def _update_optional_wham_items(self, snapshot) -> None:
        if snapshot.source_kind != "wham":
            self._hide_optional_wham_items()
            return

        lines = format_reset_credit_lines(snapshot)
        self.item_reset_separator.show()
        self.item_reset_credits.set_label(lines[0])
        self.item_reset_credits.show()

        detail_lines = lines[1:]
        for idx, item in enumerate(self.credit_items):
            if idx < len(detail_lines):
                item.set_label(detail_lines[idx])
                item.show()
            else:
                item.hide()

    def _hide_optional_wham_items(self) -> None:
        self.item_reset_separator.hide()
        self.item_reset_credits.hide()
        for item in self.credit_items:
            item.hide()

    def _color_for_pct(self, value: int) -> str:
        if value >= 90:
            return "red"
        if value >= 70:
            return "yellow"
        return "green"


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    CodexRateIndicator()
    Gtk.main()


if __name__ == "__main__":
    main()
