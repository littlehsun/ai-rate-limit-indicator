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

from adapters import (  # noqa: E402
    display_settings,
    dropdown_providers,
    load_snapshots,
    provider_display_order,
    read_manager_config,
    write_display_settings,
)
from models import (  # noqa: E402
    ProviderSnapshot,
    countdown,
    local_reset_time,
    parse_timestamp,
    write_snapshot_cache,
)


POLL_INTERVAL_SECONDS = 60
ICON_NAMES = ["rate-limit-unified-0", "rate-limit-unified-1"]
FALLBACK_ICON_NAME = "view-refresh-symbolic"
NEUTRAL_TEXT_COLOR = "#FFFFFF"
METRIC_SEPARATOR_COLOR = "#808080"
COLORS = {"green": "#00AF50", "yellow": "#E6C800", "red": "#FF5555"}
ASSET_TYPES = {
    "codex": ("codex-logo.png", "image/png"),
    "claude": ("claude-logo.svg", "image/svg+xml"),
    "grok": ("grok-logo.png", "image/png"),
    "gemini": ("gemini-logo.svg", "image/svg+xml"),
}
TextSegment = tuple[str, str]
IconText = str | Sequence[TextSegment]
IconEntry = tuple[str, IconText, str]
ASSET_DIR = Path(__file__).parent / "assets"


def _load_brand_data() -> dict[str, str]:
    result = {}
    for provider, (filename, mime_type) in ASSET_TYPES.items():
        try:
            encoded = base64.b64encode((ASSET_DIR / filename).read_bytes()).decode(
                "ascii"
            )
        except OSError:
            continue
        result[provider] = f"data:{mime_type};base64,{encoded}"
    return result


BRAND_DATA = _load_brand_data()


def _window_kind(window_id: str) -> str:
    normalized = window_id.lower()
    if normalized in {"7d", "weekly"} or normalized.endswith(("-7d", "-weekly")):
        return "7d"
    if normalized == "monthly" or normalized.endswith("-monthly"):
        return "monthly"
    return "5h"


def choose_snapshot(
    snapshots: tuple[ProviderSnapshot, ...],
    preferred: Optional[str] = None,
) -> Optional[ProviderSnapshot]:
    if not snapshots:
        return None
    by_id = {snapshot.provider: snapshot for snapshot in snapshots}
    if preferred and preferred != "highest" and preferred in by_id:
        return by_id[preferred]
    fresh = [
        snapshot
        for snapshot in snapshots
        if snapshot.status == "fresh" and snapshot.windows
    ]
    usable = [snapshot for snapshot in snapshots if snapshot.windows]
    return max(
        fresh or usable or list(snapshots), key=lambda item: item.max_used_percent
    )


def seven_day_percent(snapshot: ProviderSnapshot) -> Optional[int]:
    windows = [
        window.used_percent
        for window in snapshot.windows
        if _window_kind(window.id) == "7d"
    ]
    return max(windows) if windows else None


def split_reset_credit_extras(
    extras: tuple[str, ...],
) -> tuple[Optional[str], tuple[str, ...], tuple[str, ...]]:
    reset_label = next(
        (extra for extra in extras if extra.startswith("Reset credits:")),
        None,
    )
    if reset_label is None:
        return None, (), extras
    expirations = tuple(
        extra
        for extra in extras
        if extra.split(".", 1)[0].isdigit() and " expires " in extra
    )
    remaining = tuple(
        extra for extra in extras if extra != reset_label and extra not in expirations
    )
    return reset_label, expirations, remaining


def order_dropdown_snapshots(
    snapshots: tuple[ProviderSnapshot, ...],
    provider_order: tuple[str, ...],
    dropdown_order: tuple[str, ...],
) -> tuple[ProviderSnapshot, ...]:
    by_id = {snapshot.provider: snapshot for snapshot in snapshots}
    ordered_ids = []
    for provider in (*provider_order, *dropdown_order):
        if (
            provider in dropdown_order
            and provider in by_id
            and provider not in ordered_ids
        ):
            ordered_ids.append(provider)
    return tuple(by_id[provider] for provider in ordered_ids)


class AutoDisplaySelector:
    def __init__(self) -> None:
        self.last_values: dict[str, int] = {}
        self.selected_provider: Optional[str] = None

    def choose(
        self,
        snapshots: tuple[ProviderSnapshot, ...],
    ) -> Optional[ProviderSnapshot]:
        eligible = []
        for snapshot in snapshots:
            used_percent = seven_day_percent(snapshot)
            if snapshot.status != "fresh" or used_percent is None:
                continue
            eligible.append(
                (
                    snapshot,
                    used_percent,
                    parse_timestamp(snapshot.updated_at) or 0,
                )
            )
        if not eligible:
            self.selected_provider = None
            fresh = [item for item in snapshots if item.status == "fresh"]
            return max(
                fresh or list(snapshots),
                key=lambda item: parse_timestamp(item.updated_at) or 0,
                default=None,
            )

        changed = []
        for snapshot, current_value, updated_at in eligible:
            previous_value = self.last_values.get(snapshot.provider)
            if previous_value is not None and previous_value != current_value:
                changed.append(
                    (
                        abs(current_value - previous_value),
                        updated_at,
                        snapshot,
                    )
                )

        eligible_by_provider = {item[0].provider: item[0] for item in eligible}
        if changed:
            selected = max(changed, key=lambda item: (item[0], item[1]))[2]
        elif self.selected_provider in eligible_by_provider:
            selected = eligible_by_provider[self.selected_provider]
        else:
            selected = max(eligible, key=lambda item: item[2])[0]

        self.last_values.update(
            {snapshot.provider: value for snapshot, value, _ in eligible}
        )
        self.selected_provider = selected.provider
        return selected


def make_icon_svg(provider: str, text: IconText, color: str) -> str:
    return make_multi_icon_svg(((provider, text, color),))


def make_multi_icon_svg(entries: Sequence[IconEntry]) -> str:
    if not entries:
        entries = (("codex", "--", "green"),)
    blocks = []
    cursor = 1
    for index, (provider, text, color) in enumerate(entries):
        if index:
            blocks.append(
                f'<text x="{cursor}" y="15" fill="{NEUTRAL_TEXT_COLOR}" '
                'font-family="monospace,DejaVu Sans Mono" font-size="11">│</text>'
            )
            cursor += 14
        plain = _plain_text(text)
        spans = _render_text_spans(text, color)
        fill = _resolve_color(color)
        brand = BRAND_DATA.get(provider)
        blocks.append(
            (
                f'<image x="{cursor}" y="1" width="20" height="20" href="{brand}"/>'
                if brand
                else f'<circle cx="{cursor + 8}" cy="11" r="6" fill="{fill}"/>'
            )
        )
        blocks.append(
            f'<text x="{cursor + 24}" y="15" font-family="monospace,DejaVu Sans Mono" '
            f'font-size="11" xml:space="preserve">{spans}</text>'
        )
        cursor += max(92, int(len(plain) * 7.5) + 32)
    width = max(98, cursor)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="22">'
        f"{''.join(blocks)}</svg>"
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


def _icon_dir() -> Optional[Path]:
    candidates = []
    if os.environ.get("XDG_RUNTIME_DIR"):
        candidates.append(Path(os.environ["XDG_RUNTIME_DIR"]) / "rate-limit-indicator")
    candidates.append(Path.home() / ".cache" / "rate-limit-indicator")
    for path in candidates:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    return None


class UnifiedRateIndicator:
    def __init__(self) -> None:
        self._icon_idx = 0
        self.icon_dir = _icon_dir()
        self.snapshots: tuple[ProviderSnapshot, ...] = ()
        self.auto_selector = AutoDisplaySelector()
        self.settings_window = None
        initial = (
            self._write_icon(ICON_NAMES[0], (("codex", "--", "green"),))
            or FALLBACK_ICON_NAME
        )
        if self.icon_dir:
            self.indicator = AppIndicator3.Indicator.new_with_path(
                "rate-limit-indicator",
                initial,
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
                str(self.icon_dir),
            )
        else:
            self.indicator = AppIndicator3.Indicator.new(
                "rate-limit-indicator",
                initial,
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.menu = Gtk.Menu()
        self._build_loading_menu()
        self.indicator.set_menu(self.menu)
        GLib.timeout_add(300, self._initial_update)
        GLib.timeout_add_seconds(POLL_INTERVAL_SECONDS, self._poll)

    def _build_loading_menu(self) -> None:
        self._append_info("Loading usage…")
        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        self.menu.append(quit_item)
        self.menu.show_all()

    def _initial_update(self) -> bool:
        self._safe_update()
        return False

    def _poll(self) -> bool:
        self._safe_update()
        return True

    def _safe_update(self) -> None:
        try:
            self.update()
        except Exception:
            print("rate-limit-indicator: update failed", file=sys.stderr)
            traceback.print_exc()

    def update(self) -> None:
        self.snapshots = load_snapshots()
        write_snapshot_cache(self.snapshots)
        selected = self._select_snapshots()
        if not selected:
            self._set_icon(
                (("codex", "--", "green"),),
                "No providers selected",
            )
        else:
            entries = tuple(
                (
                    snapshot.provider,
                    self._segments(snapshot),
                    self._color(snapshot.max_used_percent),
                )
                for snapshot in selected
            )
            self._set_icon(
                entries,
                " | ".join(
                    f"{snapshot.label}: {_plain_text(self._segments(snapshot))}"
                    for snapshot in selected
                ),
            )
        self._rebuild_menu(selected)

    def _select_snapshots(self) -> tuple[ProviderSnapshot, ...]:
        config = read_manager_config()
        mode, providers = display_settings(config)
        if mode == "auto":
            selected = self.auto_selector.choose(self.snapshots)
            return (selected,) if selected else ()
        by_id = {snapshot.provider: snapshot for snapshot in self.snapshots}
        selected_ids = set(providers)
        return tuple(
            by_id[provider]
            for provider in provider_display_order(config)
            if provider in selected_ids and provider in by_id
        )

    def _segments(self, snapshot: ProviderSnapshot) -> list[TextSegment]:
        windows = snapshot.windows[:2]
        if not windows:
            return [("--", "green")]
        segments: list[TextSegment] = []
        for index, window in enumerate(windows):
            if index:
                segments.append(("|", METRIC_SEPARATOR_COLOR))
            segments.append(
                (f"{window.used_percent}%", self._color(window.used_percent))
            )
        constrained = max(windows, key=lambda item: item.used_percent)
        segments.append((f"  ⟳{countdown(constrained.resets_at)}", NEUTRAL_TEXT_COLOR))
        return segments

    def _rebuild_menu(self, selected: tuple[ProviderSnapshot, ...]) -> None:
        for item in list(self.menu.get_children()):
            self.menu.remove(item)
        config = read_manager_config()
        mode, _display_order = display_settings(config)
        provider_order = provider_display_order(config)
        dropdown_order = dropdown_providers(config)
        selected_ids = {snapshot.provider for snapshot in selected}
        auto_label = selected[0].label if mode == "auto" and selected else "--"
        auto_item = Gtk.CheckMenuItem(
            label=f"Auto (recent 7D: {auto_label})",
        )
        auto_item.set_active(mode == "auto")
        auto_item.connect("toggled", self._toggle_auto_mode)
        self.menu.append(auto_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        visible_snapshots = order_dropdown_snapshots(
            self.snapshots,
            provider_order,
            dropdown_order,
        )
        for index, snapshot in enumerate(visible_snapshots):
            state = (
                f" ({snapshot.status.replace('_', ' ')})"
                if snapshot.status in {"error", "no_data"}
                else ""
            )
            header = Gtk.CheckMenuItem(label=f"{snapshot.label}{state}")
            header.set_active(mode == "custom" and snapshot.provider in selected_ids)
            header.connect("toggled", self._toggle_provider, snapshot.provider)
            self.menu.append(header)
            if snapshot.error:
                self._append_info(f"  Error: {snapshot.error}")
            elif not snapshot.windows:
                self._append_info("  No data")
            for window in snapshot.windows:
                icon = self._window_icon(window.id)
                body = (
                    f"{window.detail} ({window.used_percent}%)"
                    if window.detail
                    else f"{window.used_percent}%"
                )
                reset = (
                    f"  ⟳ {local_reset_time(window.resets_at)} "
                    f"({countdown(window.resets_at)})"
                    if window.resets_at
                    else ""
                )
                self._append_info(f"  {icon} {window.label}: {body}{reset}")
            self._append_extras(snapshot.extras)
            if snapshot.updated_at:
                self._append_info(
                    f"  Updated: {snapshot.updated_at.replace('T', ' ')[:16]}"
                )
            if index < len(visible_snapshots) - 1:
                self.menu.append(Gtk.SeparatorMenuItem())
        self.menu.append(Gtk.SeparatorMenuItem())
        settings_item = Gtk.MenuItem(label="Display settings…")
        settings_item.connect("activate", self._open_settings)
        self.menu.append(settings_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        self.menu.append(quit_item)
        self.menu.show_all()

    def _append_info(self, label: str) -> None:
        item = Gtk.MenuItem(label=label)
        item.set_sensitive(False)
        self.menu.append(item)

    def _append_extras(self, extras: tuple[str, ...]) -> None:
        reset_label, expirations, remaining = split_reset_credit_extras(extras)
        if reset_label:
            reset_item = Gtk.MenuItem(label=f"  {reset_label}")
            if expirations:
                submenu = Gtk.Menu()
                for expiration in expirations:
                    detail = Gtk.MenuItem(label=expiration)
                    detail.set_sensitive(False)
                    submenu.append(detail)
                submenu.show_all()
                reset_item.set_submenu(submenu)
            else:
                reset_item.set_sensitive(False)
            self.menu.append(reset_item)
        for extra in remaining:
            self._append_info(f"  {extra}")

    @staticmethod
    def _window_icon(window_id: str) -> str:
        return "📅" if _window_kind(window_id) in {"7d", "monthly"} else "⚡"

    def _toggle_auto_mode(self, item) -> None:
        mode, providers = display_settings(read_manager_config())
        if item.get_active():
            write_display_settings("auto", providers)
        elif mode == "auto":
            fallback = (
                (self.auto_selector.selected_provider,)
                if self.auto_selector.selected_provider
                else providers[:1]
            )
            write_display_settings("custom", fallback)
        self.update()

    def _toggle_provider(self, item, provider: str) -> None:
        mode, configured = display_settings(read_manager_config())
        providers = [] if mode == "auto" and item.get_active() else list(configured)
        if item.get_active() and provider not in providers:
            providers.append(provider)
        elif not item.get_active() and provider in providers:
            providers.remove(provider)
        write_display_settings("custom", tuple(providers))
        self.update()

    def _open_settings(self, _item=None) -> None:
        if self.settings_window is not None:
            self.settings_window.present()
            return

        config = read_manager_config()
        mode, configured = display_settings(config)
        configured_dropdown = dropdown_providers(config)
        available = [snapshot.provider for snapshot in self.snapshots]
        order = [
            provider
            for provider in provider_display_order(config)
            if provider in available
        ]
        order.extend(provider for provider in available if provider not in order)
        selected = set(configured)
        dropdown_selected = set(configured_dropdown)
        labels = {snapshot.provider: snapshot.label for snapshot in self.snapshots}

        window = Gtk.Window(title="Rate Limit Indicator Settings")
        window.set_default_size(520, 360)
        window.set_border_width(16)
        window.connect("destroy", self._settings_closed)
        self.settings_window = window

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        window.add(root)
        intro = Gtk.Label(
            label=(
                "Choose where each provider appears. Order controls both the "
                "indicator and dropdown."
            ),
            xalign=0,
        )
        intro.set_line_wrap(True)
        root.pack_start(intro, False, False, 0)

        auto_radio = Gtk.RadioButton.new_with_label(None, "Auto: recent 7D change")
        custom_radio = Gtk.RadioButton.new_with_label_from_widget(
            auto_radio,
            "Custom provider list",
        )
        auto_radio.set_active(mode == "auto")
        custom_radio.set_active(mode == "custom")
        mode_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        mode_box.set_border_width(8)
        mode_box.pack_start(auto_radio, False, False, 0)
        mode_box.pack_start(custom_radio, False, False, 0)
        mode_frame = Gtk.Frame(label="Mode")
        mode_frame.add(mode_box)
        root.pack_start(mode_frame, False, False, 0)

        provider_grid = Gtk.Grid()
        provider_grid.set_border_width(8)
        provider_grid.set_row_spacing(6)
        provider_grid.set_column_spacing(18)
        provider_grid.set_column_homogeneous(False)
        provider_frame = Gtk.Frame(label="Providers")
        provider_frame.add(provider_grid)
        root.pack_start(provider_frame, True, True, 0)

        def rebuild_rows() -> None:
            for child in list(provider_grid.get_children()):
                provider_grid.remove(child)
            provider_grid.attach(Gtk.Label(label="Provider", xalign=0), 0, 0, 1, 1)
            provider_grid.attach(Gtk.Label(label="Indicator"), 1, 0, 1, 1)
            provider_grid.attach(Gtk.Label(label="Dropdown"), 2, 0, 1, 1)
            provider_grid.attach(Gtk.Label(label="Order"), 3, 0, 1, 1)
            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            provider_grid.attach(separator, 0, 1, 4, 1)
            for index, provider in enumerate(order):
                row_index = index + 2
                provider_grid.attach(
                    Gtk.Label(label=labels.get(provider, provider.title()), xalign=0),
                    0,
                    row_index,
                    1,
                    1,
                )
                panel_check = Gtk.CheckButton()
                panel_check.set_tooltip_text("Show in panel indicator")
                panel_check.set_halign(Gtk.Align.CENTER)
                panel_check.set_active(provider in selected)
                panel_check.connect(
                    "toggled",
                    lambda button, name=provider: (
                        selected.add(name)
                        if button.get_active()
                        else selected.discard(name)
                    ),
                )
                dropdown_check = Gtk.CheckButton()
                dropdown_check.set_tooltip_text("Show usage section in dropdown menu")
                dropdown_check.set_halign(Gtk.Align.CENTER)
                dropdown_check.set_active(provider in dropdown_selected)
                dropdown_check.connect(
                    "toggled",
                    lambda button, name=provider: (
                        dropdown_selected.add(name)
                        if button.get_active()
                        else dropdown_selected.discard(name)
                    ),
                )
                provider_grid.attach(panel_check, 1, row_index, 1, 1)
                provider_grid.attach(dropdown_check, 2, row_index, 1, 1)
                order_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                up = Gtk.Button.new_from_icon_name(
                    "go-up-symbolic", Gtk.IconSize.BUTTON
                )
                down = Gtk.Button.new_from_icon_name(
                    "go-down-symbolic", Gtk.IconSize.BUTTON
                )
                up.set_tooltip_text("Move up")
                down.set_tooltip_text("Move down")
                up.set_size_request(36, 32)
                down.set_size_request(36, 32)
                up.set_sensitive(index > 0)
                down.set_sensitive(index < len(order) - 1)
                up.connect("clicked", lambda _button, idx=index: move_row(idx, -1))
                down.connect("clicked", lambda _button, idx=index: move_row(idx, 1))
                order_box.pack_start(up, False, False, 0)
                order_box.pack_start(down, False, False, 0)
                provider_grid.attach(order_box, 3, row_index, 1, 1)
            provider_grid.show_all()

        def move_row(index: int, offset: int) -> None:
            target = index + offset
            if target < 0 or target >= len(order):
                return
            order[index], order[target] = order[target], order[index]
            rebuild_rows()

        def apply_settings(_button) -> None:
            ordered_selected = tuple(
                provider for provider in order if provider in selected
            )
            ordered_dropdown = tuple(
                provider for provider in order if provider in dropdown_selected
            )
            selected_mode = "custom" if custom_radio.get_active() else "auto"
            write_display_settings(
                selected_mode,
                ordered_selected,
                dropdown=ordered_dropdown,
                provider_order=tuple(order),
            )
            window.destroy()
            self.update()

        rebuild_rows()
        actions = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
        actions.set_layout(Gtk.ButtonBoxStyle.END)
        cancel = Gtk.Button(label="Cancel")
        apply_button = Gtk.Button(label="Apply")
        cancel.connect("clicked", lambda _button: window.destroy())
        apply_button.connect("clicked", apply_settings)
        actions.add(cancel)
        actions.add(apply_button)
        root.pack_end(actions, False, False, 0)
        window.show_all()

    def _settings_closed(self, _window) -> None:
        self.settings_window = None

    def _set_icon(
        self,
        entries: Sequence[IconEntry],
        description: str,
    ) -> None:
        self._icon_idx ^= 1
        icon_name = self._write_icon(ICON_NAMES[self._icon_idx], entries)
        try:
            self.indicator.set_icon_full(icon_name or FALLBACK_ICON_NAME, description)
        except Exception:
            traceback.print_exc()

    def _write_icon(
        self,
        name: str,
        entries: Sequence[IconEntry],
    ) -> Optional[str]:
        if self.icon_dir is None:
            return None
        path = self.icon_dir / f"{name}.svg"
        tmp = self.icon_dir / f".{name}.svg.tmp"
        try:
            tmp.write_text(make_multi_icon_svg(entries), encoding="utf-8")
            os.replace(tmp, path)
            return name
        except OSError:
            with suppress(OSError):
                tmp.unlink()
            return None

    @staticmethod
    def _color(value: int) -> str:
        if value >= 90:
            return "red"
        if value >= 70:
            return "yellow"
        return "green"


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    UnifiedRateIndicator()
    Gtk.main()


if __name__ == "__main__":
    main()
