#!/usr/bin/env python3
from __future__ import annotations

import base64
import html
import os
import signal
import sys
import traceback
from contextlib import suppress
from pathlib import Path
from typing import Optional, Sequence

import gi

gi.require_version("AppIndicator3", "0.1")
gi.require_version("Gtk", "3.0")
from gi.repository import AppIndicator3, GLib, Gtk  # noqa: E402

from grok_rate import (
    default_cache_path,
    format_indicator_label,
    format_indicator_parts,
    format_menu_line,
    format_on_demand_line,
    format_updated_at,
    read_cache,
)

POLL_INTERVAL_SECONDS = 60
ICON_NAMES = ["grok-rate-0", "grok-rate-1"]
FALLBACK_ICON_NAME = "view-refresh-symbolic"
NEUTRAL_TEXT_COLOR = "#FFFFFF"
COLORS = {
    "green": "#00AF50",
    "yellow": "#E6C800",
    "red": "#FF5555",
}
TextSegment = tuple[str, str]
IconText = str | Sequence[TextSegment]
GROK_LOGO_PATH = Path(__file__).parent / "assets" / "grok-logo.png"


def _load_logo_data_uri() -> Optional[str]:
    try:
        encoded = base64.b64encode(GROK_LOGO_PATH.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/png;base64,{encoded}"


GROK_LOGO_DATA_URI = _load_logo_data_uri()


def make_icon_svg(text: IconText, color: str) -> str:
    fill = _resolve_color(color)
    plain = _plain_text(text)
    spans = _render_text_spans(text, color)
    width = max(98, int(len(plain) * 7.5) + 36)
    brand_mark = (
        f'<image x="1" y="1" width="20" height="20" '
        f'href="{GROK_LOGO_DATA_URI}"/>'
        if GROK_LOGO_DATA_URI
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
        candidates.append(Path(runtime_dir) / "grok-rate-indicator")
    candidates.append(Path.home() / ".cache" / "grok-rate-indicator")

    for path in candidates:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            return path
        except OSError as exc:
            print(f"grok-rate-indicator: cannot create icon dir {path}: {exc}", file=sys.stderr)
    return None


def _log_exception(context: str) -> None:
    print(f"grok-rate-indicator: {context}", file=sys.stderr)
    traceback.print_exc()


class GrokRateIndicator:
    def __init__(self) -> None:
        self.cache_path = Path(os.environ.get("GROK_RATE_CACHE", default_cache_path()))
        self._icon_idx = 0
        self.icon_dir = _create_icon_dir()
        initial_icon = self._write_icon(ICON_NAMES[0], "Grok --", "green") or FALLBACK_ICON_NAME

        if self.icon_dir is None:
            self.indicator = AppIndicator3.Indicator.new(
                "grok-rate-indicator",
                initial_icon,
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            )
        else:
            self.indicator = AppIndicator3.Indicator.new_with_path(
                "grok-rate-indicator",
                initial_icon,
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
                str(self.icon_dir),
            )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()

        self.item_weekly = Gtk.MenuItem(label="Weekly: --")
        self.item_weekly.set_sensitive(False)
        self.menu.append(self.item_weekly)

        self.item_monthly = Gtk.MenuItem(label="Monthly: --")
        self.item_monthly.set_sensitive(False)
        self.menu.append(self.item_monthly)

        self.item_on_demand = Gtk.MenuItem(label="On-demand: --")
        self.item_on_demand.set_sensitive(False)
        self.menu.append(self.item_on_demand)

        self.product_items: list[Gtk.MenuItem] = []
        for _ in range(4):
            item = Gtk.MenuItem(label="Product: --")
            item.set_sensitive(False)
            self.menu.append(item)
            self.product_items.append(item)

        self.item_updated = Gtk.MenuItem(label="Updated: --")
        self.item_updated.set_sensitive(False)
        self.menu.append(self.item_updated)

        self.menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect("activate", lambda _: Gtk.main_quit())
        self.menu.append(item_quit)

        self.menu.show_all()
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
        snapshot = read_cache(self.cache_path)
        if snapshot is None:
            self._set_icon("Grok --", "green")
            self.item_weekly.set_label("Weekly: no data")
            self.item_monthly.set_label("Monthly: no data")
            self.item_on_demand.hide()
            for item in self.product_items:
                item.hide()
            self.item_updated.set_label("Updated: --")
            return

        label = format_indicator_label(snapshot)
        color = self._color_for_pct(snapshot.max_used_percent)
        self._set_icon(self._indicator_text_segments(snapshot), color, description=label)

        self.item_weekly.set_label(format_menu_line(snapshot.weekly, "Weekly"))
        self.item_monthly.set_label(format_menu_line(snapshot.monthly, "Monthly"))

        on_demand = format_on_demand_line(snapshot)
        if on_demand is None:
            self.item_on_demand.hide()
        else:
            self.item_on_demand.set_label(on_demand)
            self.item_on_demand.show()

        for idx, item in enumerate(self.product_items):
            if idx < len(snapshot.product_usage):
                name, pct = snapshot.product_usage[idx]
                if pct is None:
                    item.set_label(f"{name}: --")
                else:
                    item.set_label(f"{name}: {pct}%")
                item.show()
            else:
                item.hide()

        self.item_updated.set_label(f"Updated: {format_updated_at(snapshot.updated_at)}")

    def _set_icon(self, text: IconText, color: str, description: Optional[str] = None) -> None:
        self._icon_idx ^= 1
        icon_name = self._write_icon(ICON_NAMES[self._icon_idx], text, color)
        if icon_name is None:
            icon_name = FALLBACK_ICON_NAME
        try:
            label = description or _plain_text(text)
            self.indicator.set_icon_full(icon_name, f"Grok usage: {label}")
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
            print(f"grok-rate-indicator: cannot write icon {icon_path}: {exc}", file=sys.stderr)
            return None

    def _indicator_text_segments(self, snapshot) -> list[TextSegment]:
        weekly, monthly, reset = format_indicator_parts(snapshot)
        weekly_color = self._color_for_pct(snapshot.weekly.used_percent if snapshot.weekly else 0)
        monthly_color = self._color_for_pct(snapshot.monthly.used_percent if snapshot.monthly else 0)
        return [
            (weekly, weekly_color),
            ("|", NEUTRAL_TEXT_COLOR),
            (monthly, monthly_color),
            (f"  ⟳{reset}", NEUTRAL_TEXT_COLOR),
        ]

    def _color_for_pct(self, value: int) -> str:
        if value >= 90:
            return "red"
        if value >= 70:
            return "yellow"
        return "green"


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    GrokRateIndicator()
    Gtk.main()


if __name__ == "__main__":
    main()
