#!/usr/bin/env python3
"""A floating desktop widget for the four providers, always in view.

It reads the same published snapshot the terminal monitor and the iOS widget
read, so running it spends no provider quota and needs no credential: the
desktop indicator already paid for these numbers and `publish.py` hands them
out. What this adds is a place to put them -- a small frameless window that
stays above the other windows, on every workspace, wherever it was dragged.

The window is deliberately not a panel icon. The tray has room for one
provider's two numbers; a desk has room for all four with their reset
countdowns attached, which is what you actually want on screen while a long
agent run is burning through a weekly quota.

Everything above the GTK layer is plain data, so the rows, the palette, the
countdowns and the stored geometry can be tested without a display.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from usage_monitor import (  # noqa: E402
    DANGER_PERCENT,
    LANGUAGES,
    STALE_AFTER_SECONDS,
    STRINGS,
    WARNING_PERCENT,
    MonitorError,
    ProviderSnapshot,
    Settings,
    Snapshot,
    SnapshotClient,
    Theme,
    UsageWindow,
    age_text,
    default_config_path,
    load_themes,
    ordered_windows,
    percent_label,
    relative_time,
    resolve_settings as resolve_monitor_settings,
    snapshot_time,
)


DEFAULT_OPACITY = 0.95
DEFAULT_SCALE = 1.0
MIN_OPACITY = 0.30
MAX_OPACITY = 1.0
MIN_SCALE = 0.7
MAX_SCALE = 2.0
DEFAULT_WIDTH = 320
MIN_WIDTH = 220
BASE_FONT_PX = 12

AUTOSTART_FILENAME = "rate-limit-float.desktop"

# How often the countdowns are redrawn between polls. A reset time that still
# says "3 hours from now" an hour later is wrong, and redrawing four rows is
# the cheapest possible way to keep it honest.
TICK_SECONDS = 60


FLOAT_STRINGS: Dict[str, Dict[str, str]] = {
    "zh-TW": {
        "title": "AI 用量",
        "loading": "讀取中…",
        "refresh": "立即重新整理",
        "compact": "精簡模式",
        "on_top": "永遠置頂",
        "opacity": "不透明度",
        "scale": "字級",
        "theme": "主題",
        "language": "語言",
        "autostart": "登入時自動啟動",
        "quit": "結束",
        "taken_at": "快照 {time}",
        "never": "尚無快照",
        "reset_prefix": "⟳ ",
        "no_reset": "⟳ —",
    },
    "en": {
        "title": "AI usage",
        "loading": "Loading…",
        "refresh": "Refresh now",
        "compact": "Compact mode",
        "on_top": "Always on top",
        "opacity": "Opacity",
        "scale": "Text size",
        "theme": "Theme",
        "language": "Language",
        "autostart": "Start at login",
        "quit": "Quit",
        "taken_at": "Snapshot {time}",
        "never": "No snapshot yet",
        "reset_prefix": "⟳ ",
        "no_reset": "⟳ —",
    },
}


def strings_for(language: str) -> Dict[str, str]:
    """The terminal monitor's words plus the ones only a window needs."""

    return {**STRINGS[language], **FLOAT_STRINGS[language]}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class FloatSettings:
    """What the window looks like, as opposed to where the numbers come from."""

    compact: bool = False
    opacity: float = DEFAULT_OPACITY
    scale: float = DEFAULT_SCALE
    on_top: bool = True
    width: int = DEFAULT_WIDTH
    x: Optional[int] = None
    y: Optional[int] = None


def load_float_settings(path: Optional[Path] = None) -> FloatSettings:
    """Read the `[float]` section, falling back to the defaults for any of it."""

    config_path = path or default_config_path()
    if not config_path.is_file():
        return FloatSettings()
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        raise MonitorError(f"cannot read {config_path}: {exc}") from exc
    if not parser.has_section("float"):
        return FloatSettings()
    try:
        return FloatSettings(
            compact=parser.getboolean("float", "compact", fallback=False),
            opacity=clamp(
                parser.getfloat("float", "opacity", fallback=DEFAULT_OPACITY),
                MIN_OPACITY,
                MAX_OPACITY,
            ),
            scale=clamp(
                parser.getfloat("float", "scale", fallback=DEFAULT_SCALE),
                MIN_SCALE,
                MAX_SCALE,
            ),
            on_top=parser.getboolean("float", "on_top", fallback=True),
            width=max(MIN_WIDTH, parser.getint("float", "width", fallback=DEFAULT_WIDTH)),
        )
    except ValueError as exc:
        raise MonitorError(f"cannot read [float] in {config_path}: {exc}") from exc


def default_state_path() -> Path:
    """Where the dragged position lives.

    Geometry is state rather than configuration: it changes every time the
    window is moved, and writing that back into the file a user hand-edits
    would fight with them over their own comments.
    """

    override = os.environ.get("USAGE_FLOAT_STATE")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_STATE_HOME")
    state_home = Path(base) if base else Path.home() / ".local" / "state"
    return state_home / "rate-limit-indicator" / "float.json"


def read_state(path: Optional[Path] = None) -> Dict[str, Any]:
    state_path = path or default_state_path()
    try:
        stored = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def write_state(state: Mapping[str, Any], path: Optional[Path] = None) -> None:
    state_path = path or default_state_path()
    try:
        state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(dict(state), indent=2), encoding="utf-8")
        os.replace(temporary, state_path)
    except OSError:
        # A position we cannot save costs the next launch its placement, not
        # this session its window.
        pass


def apply_state(settings: FloatSettings, state: Mapping[str, Any]) -> FloatSettings:
    """Lay a saved session over the configured defaults.

    Every field is checked rather than trusted: this file is rewritten on every
    drag, so a truncated write must cost the window its position, not its
    launch.
    """

    updates: Dict[str, Any] = {}
    for flag in ("compact", "on_top"):
        if isinstance(state.get(flag), bool):
            updates[flag] = state[flag]
    if isinstance(state.get("opacity"), (int, float)):
        updates["opacity"] = clamp(float(state["opacity"]), MIN_OPACITY, MAX_OPACITY)
    if isinstance(state.get("scale"), (int, float)):
        updates["scale"] = clamp(float(state["scale"]), MIN_SCALE, MAX_SCALE)
    for axis in ("x", "y"):
        value = state.get(axis)
        if isinstance(value, int) and not isinstance(value, bool):
            updates[axis] = value
    return replace(settings, **updates)


def state_from(settings: FloatSettings) -> Dict[str, Any]:
    return {
        "compact": settings.compact,
        "on_top": settings.on_top,
        "opacity": round(settings.opacity, 3),
        "scale": round(settings.scale, 3),
        "x": settings.x,
        "y": settings.y,
    }


@dataclass(frozen=True)
class WindowRow:
    label: str
    percent_text: str
    fraction: float
    tone: str
    reset_text: str


@dataclass(frozen=True)
class ProviderRow:
    provider: str
    label: str
    status: str
    fresh: bool
    windows: Tuple[WindowRow, ...]
    extras: Tuple[str, ...]
    error: Optional[str]


def tone_for(used_percent: Optional[int]) -> str:
    """Which palette colour a percentage earns.

    None is not zero. A window the backend stopped reporting gets the empty
    tone, because painting it green would claim a quota nobody measured is
    untouched -- the rule every other surface here follows. The two thresholds
    are the monitor's, so one percentage cannot be amber in the terminal and
    green on the desktop.
    """

    if used_percent is None:
        return "empty"
    if used_percent >= DANGER_PERCENT:
        return "danger"
    if used_percent >= WARNING_PERCENT:
        return "warning"
    return "good"


def build_window_row(
    window: UsageWindow, *, now: float, text: Mapping[str, str]
) -> WindowRow:
    if window.resets_at is None:
        reset = text["no_reset"]
    else:
        reset = text["reset_prefix"] + relative_time(window.resets_at - now, text)
    fraction = (
        0.0
        if window.used_percent is None
        else clamp(window.used_percent / 100, 0.0, 1.0)
    )
    return WindowRow(
        label=window.label,
        percent_text=percent_label(window),
        fraction=fraction,
        tone=tone_for(window.used_percent),
        reset_text=reset,
    )


def build_provider_row(
    name: str,
    provider: Optional[ProviderSnapshot],
    *,
    now: float,
    text: Mapping[str, str],
    compact: bool,
) -> ProviderRow:
    if provider is None:
        return ProviderRow(
            provider=name,
            label=name.title(),
            status="",
            fresh=False,
            windows=(),
            extras=(text["missing"],),
            error=None,
        )
    windows = ordered_windows(provider)
    # Compact mode keeps the lead window only. `ordered_windows` already puts
    # the weekly one first for every provider, so the one bar left standing is
    # the one that is comparable across all four.
    if compact:
        windows = windows[:1]
    return ProviderRow(
        provider=provider.provider,
        label=provider.label,
        status=provider.status,
        fresh=provider.status == "fresh",
        windows=tuple(
            build_window_row(window, now=now, text=text) for window in windows
        ),
        extras=() if compact else tuple(provider.extras),
        error=provider.error,
    )


def build_rows(
    snapshot: Snapshot,
    providers: Sequence[str],
    *,
    now: float,
    text: Mapping[str, str],
    compact: bool,
) -> Tuple[ProviderRow, ...]:
    return tuple(
        build_provider_row(
            name, snapshot.provider(name), now=now, text=text, compact=compact
        )
        for name in providers
    )


def header_text(snapshot: Snapshot, *, text: Mapping[str, str]) -> str:
    taken_at = snapshot_time(snapshot)
    if taken_at is None:
        return text["never"]
    return text["taken_at"].format(time=time.strftime("%m-%d %H:%M", time.localtime(taken_at)))


def status_text(
    snapshot: Snapshot, *, now: float, text: Mapping[str, str]
) -> Optional[str]:
    """The one warning line, or None when there is nothing to warn about.

    An unreachable publisher and an old snapshot are different problems -- a
    network that dropped versus a desktop that stopped refreshing -- so they
    keep the monitor's two separate wordings rather than collapsing into one.
    """

    if not snapshot.reachable:
        return text["offline"].format(age=age_text(now - snapshot.fetched_at, text))
    taken_at = snapshot_time(snapshot)
    if taken_at is not None and now - taken_at > STALE_AFTER_SECONDS:
        return text["stale"].format(age=age_text(now - taken_at, text))
    return None


def _rgba(hex_color: str, alpha: float) -> str:
    """A palette colour as CSS, since the root needs one it can be tinted at."""

    value = hex_color.lstrip("#")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha:.3f})"


def build_css(theme: Theme, *, scale: float = DEFAULT_SCALE) -> str:
    """The whole widget's look, from one palette and one font scale.

    Every colour here is the theme's. Nothing is left to fall through to the
    GTK theme: a dracula widget wearing the system's grey surface is the same
    bug the web view had, and it is the sort that looks like a choice.
    """

    font = round(BASE_FONT_PX * scale)
    return f"""
.float-root {{
    background-color: {_rgba(theme.background, 1.0)};
    border: 1px solid {theme.border};
    border-radius: 12px;
    padding: 10px 12px;
}}
.float-root, .float-root label {{
    color: {theme.text};
    font-size: {font}px;
}}
.float-title {{
    color: {theme.heading};
    font-weight: bold;
    font-size: {round(font * 1.1)}px;
}}
.float-muted {{
    color: {theme.muted};
    font-size: {round(font * 0.85)}px;
}}
.float-warning {{
    color: {theme.warning};
    font-size: {round(font * 0.85)}px;
}}
.float-danger {{
    color: {theme.danger};
    font-size: {round(font * 0.85)}px;
}}
.float-provider {{
    color: {theme.heading};
    font-weight: bold;
}}
.float-window {{
    color: {theme.accent};
}}
.float-percent {{
    color: {theme.text};
    font-family: monospace;
}}
.float-card {{
    background-color: {theme.surface};
    border-radius: 8px;
    padding: 6px 8px;
}}
progressbar.float-bar,
progressbar.float-bar trough,
progressbar.float-bar progress {{
    min-height: 8px;
    border: none;
    border-radius: 4px;
}}
progressbar.float-bar trough {{
    background-color: {theme.empty};
}}
progressbar.float-bar.tone-good progress {{ background-color: {theme.good}; }}
progressbar.float-bar.tone-warning progress {{ background-color: {theme.warning}; }}
progressbar.float-bar.tone-danger progress {{ background-color: {theme.danger}; }}
progressbar.float-bar.tone-empty progress {{ background-color: {theme.empty}; }}
""".strip()


def autostart_path() -> Path:
    override = os.environ.get("USAGE_FLOAT_AUTOSTART")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(base) if base else Path.home() / ".config"
    return config_home / "autostart" / AUTOSTART_FILENAME


def autostart_command() -> str:
    return f"{sys.executable} {Path(__file__).resolve()}"


def autostart_entry(command: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Rate Limit Float\n"
        "Comment=Floating AI usage widget\n"
        f"Exec={command}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def install_autostart(
    path: Optional[Path] = None, command: Optional[str] = None
) -> Path:
    target = path or autostart_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(autostart_entry(command or autostart_command()), encoding="utf-8")
    return target


def remove_autostart(path: Optional[Path] = None) -> bool:
    try:
        (path or autostart_path()).unlink()
    except FileNotFoundError:
        return False
    return True


def autostart_installed(path: Optional[Path] = None) -> bool:
    return (path or autostart_path()).is_file()


def is_current(value: Any, current: Any) -> bool:
    """Whether a menu option is the one in force, floats included."""

    if isinstance(value, float) and isinstance(current, (int, float)):
        return abs(value - current) < 1e-6
    return value == current


class FloatingWidget:
    """The GTK window.

    It composes a `Gtk.Window` rather than subclassing one so that importing
    this module on a machine without GTK still yields every function above --
    which is what lets the tests run on a box with no display.
    """

    def __init__(
        self,
        settings: Settings,
        float_settings: FloatSettings,
        themes: Mapping[str, Theme],
        *,
        state_path: Optional[Path] = None,
    ):
        import gi

        # Gdk is asked for by name as well: requiring only Gtk leaves the
        # Gdk version to whatever the typelib search settles on, and on a
        # machine carrying GTK 4 that is Gdk 4 against a Gtk 3 window.
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk, Pango

        self._gdk = Gdk
        self._glib = GLib
        self._gtk = Gtk
        self._pango = Pango

        self.settings = settings
        self.float = float_settings
        self.themes = dict(themes)
        self.text = strings_for(settings.language)
        self.state_path = state_path
        self.snapshot: Optional[Snapshot] = None
        self.message: Optional[str] = None

        self._client = SnapshotClient(
            settings.endpoint, timeout=settings.timeout, cache_path=settings.cache_file
        )
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._save_pending = 0
        self._pending_place: Optional[Tuple[int, int]] = None

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Rate Limit Float")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        # UTILITY keeps it out of the alt-tab list. A desk widget that steals a
        # slot in the window switcher stops being furniture.
        self.window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.window.set_default_size(self.float.width, -1)
        self.window.set_app_paintable(True)
        self.window.stick()
        self.window.set_keep_above(self.float.on_top)
        self._gtk.Widget.set_opacity(self.window, self.float.opacity)

        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            # Without an RGBA visual the rounded corners come out as black
            # squares, which reads as a rendering fault rather than a widget.
            self.window.set_visual(visual)

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.root.get_style_context().add_class("float-root")
        self.root.set_size_request(self.float.width, -1)
        self.window.add(self.root)

        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            screen, self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._apply_css()

        self.window.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.SCROLL_MASK
        )
        self.window.connect("button-press-event", self._on_button_press)
        self.window.connect("scroll-event", self._on_scroll)
        self.window.connect("configure-event", self._on_configure)
        self.window.connect("destroy", self._on_destroy)

    # -- appearance ----------------------------------------------------

    @property
    def theme(self) -> Theme:
        return self.themes[self.settings.theme]

    def _apply_css(self) -> None:
        self._css.load_from_data(
            build_css(self.theme, scale=self.float.scale).encode("utf-8")
        )

    def _label(self, content: str, *classes: str, align: float = 0.0):
        label = self._gtk.Label(label=content)
        label.set_xalign(align)
        # A provider error can be a paragraph. Ellipsizing keeps one bad
        # message from stretching the widget across the desktop.
        label.set_ellipsize(self._pango.EllipsizeMode.END)
        for name in classes:
            label.get_style_context().add_class(name)
        return label

    # -- rendering -----------------------------------------------------

    def render(self) -> None:
        Gtk = self._gtk
        for child in self.root.get_children():
            self.root.remove(child)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = self._label(self.text["title"], "float-title")
        title.set_hexpand(True)
        header.pack_start(title, True, True, 0)
        if self.snapshot is not None:
            header.pack_end(
                self._label(
                    header_text(self.snapshot, text=self.text),
                    "float-muted",
                    align=1.0,
                ),
                False,
                False,
                0,
            )
        self.root.pack_start(header, False, False, 0)

        if self.snapshot is None:
            self.root.pack_start(
                self._label(self.message or self.text["loading"], "float-muted"),
                False,
                False,
                0,
            )
            self.root.show_all()
            return

        now = time.time()
        warning = status_text(self.snapshot, now=now, text=self.text)
        if warning:
            self.root.pack_start(self._label(warning, "float-warning"), False, False, 0)
        if self.message:
            self.root.pack_start(
                self._label(self.message, "float-danger"), False, False, 0
            )
        if not self.settings.providers:
            self.root.pack_start(
                self._label(self.text["disabled"], "float-warning"), False, False, 0
            )

        rows = build_rows(
            self.snapshot,
            self.settings.providers,
            now=now,
            text=self.text,
            compact=self.float.compact,
        )
        for row in rows:
            self.root.pack_start(self._provider_box(row), False, False, 0)

        self.root.show_all()

    def _provider_box(self, row: ProviderRow):
        Gtk = self._gtk
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        # Each provider is its own card rather than a stripe between rules:
        # four unrelated quotas read as four things, and the palette already
        # carries a surface colour for exactly this.
        box.get_style_context().add_class("float-card")

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = self._label(row.label, "float-provider")
        name.set_hexpand(True)
        heading.pack_start(name, True, True, 0)
        if row.status:
            heading.pack_end(
                self._label(
                    row.status,
                    "float-muted" if row.fresh else "float-warning",
                    align=1.0,
                ),
                False,
                False,
                0,
            )
        box.pack_start(heading, False, False, 0)

        # Antigravity names a window "Claude/GPT 7D" and Codex names one "7D".
        # Without a size group the bars in one card would each start at a
        # different x, and four bars that start in four places cannot be
        # compared at a glance, which is the only reason they are bars.
        labels = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        for window in row.windows:
            box.pack_start(self._window_box(window, labels), False, False, 0)
        for extra in row.extras:
            box.pack_start(self._label(extra, "float-muted"), False, False, 0)
        if row.error:
            box.pack_start(self._label(row.error, "float-danger"), False, False, 0)
        return box

    def _window_box(self, window: WindowRow, labels=None):
        Gtk = self._gtk
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        label = self._label(window.label, "float-window")
        label.set_size_request(round(34 * self.float.scale), -1)
        if labels is not None:
            labels.add_widget(label)
        line.pack_start(label, False, False, 0)

        bar = Gtk.ProgressBar()
        bar.set_fraction(window.fraction)
        bar.set_valign(Gtk.Align.CENTER)
        bar.set_hexpand(True)
        context = bar.get_style_context()
        context.add_class("float-bar")
        context.add_class(f"tone-{window.tone}")
        line.pack_start(bar, True, True, 0)

        if not self.float.compact:
            line.pack_end(
                self._label(window.reset_text, "float-muted", align=1.0),
                False,
                False,
                0,
            )
        percent = self._label(window.percent_text, "float-percent", align=1.0)
        percent.set_size_request(round(38 * self.float.scale), -1)
        line.pack_end(percent, False, False, 0)
        return line

    # -- interaction ---------------------------------------------------

    def _on_button_press(self, _widget, event) -> bool:
        if event.button == 1:
            # begin_move_drag hands the move to the window manager, which is
            # what keeps the drag smooth and snapping to screen edges instead
            # of us chasing the pointer a frame behind it.
            self.window.begin_move_drag(
                event.button, int(event.x_root), int(event.y_root), event.time
            )
            return True
        if event.button == 3:
            self._popup_menu(event)
            return True
        return False

    def _on_scroll(self, _widget, event) -> bool:
        step = 0.0
        if event.direction == self._gdk.ScrollDirection.UP:
            step = 0.05
        elif event.direction == self._gdk.ScrollDirection.DOWN:
            step = -0.05
        elif event.direction == self._gdk.ScrollDirection.SMOOTH:
            step = -0.05 if event.delta_y > 0 else 0.05
        if not step:
            return False
        self._set_opacity(self.float.opacity + step)
        return True

    def _on_configure(self, _widget, _event) -> bool:
        x, y = self.window.get_position()
        if self._pending_place is not None:
            # The window manager places the window as it maps it, and that
            # placement arrives here -- after every move we could make before
            # mapping, and after any idle callback. So the move that sticks is
            # the one made in answer to it. One attempt only: past this the
            # position is the user's to set by dragging.
            target, self._pending_place = self._pending_place, None
            if (x, y) != target:
                self.window.move(*target)
                return False
        if (x, y) != (self.float.x, self.float.y):
            self.float = replace(self.float, x=x, y=y)
            self._schedule_save()
        return False

    def _schedule_save(self) -> None:
        # A drag fires configure-event per frame. Saving each one would write
        # the state file a hundred times to record one move.
        if self._save_pending:
            self._glib.source_remove(self._save_pending)
        self._save_pending = self._glib.timeout_add_seconds(1, self._save_state)

    def _save_state(self) -> bool:
        self._save_pending = 0
        write_state(state_from(self.float), self.state_path)
        return False

    def _set_opacity(self, value: float) -> None:
        self.float = replace(self.float, opacity=clamp(value, MIN_OPACITY, MAX_OPACITY))
        self._gtk.Widget.set_opacity(self.window, self.float.opacity)
        self._schedule_save()

    def _set_scale(self, value: float) -> None:
        self.float = replace(self.float, scale=clamp(value, MIN_SCALE, MAX_SCALE))
        self._apply_css()
        self.render()
        self._shrink_to_fit()
        self._schedule_save()

    def _set_theme(self, name: str) -> None:
        self.settings = replace(self.settings, theme=name)
        self._apply_css()

    def _set_language(self, language: str) -> None:
        self.settings = replace(self.settings, language=language)
        self.text = strings_for(language)
        self.render()

    def _toggle_compact(self) -> None:
        self.float = replace(self.float, compact=not self.float.compact)
        self.render()
        # Leaving compact mode is the only case where the window has to grow
        # again; entering it leaves the old height behind unless we ask.
        self._shrink_to_fit()
        self._schedule_save()

    def _shrink_to_fit(self) -> None:
        self.window.resize(self.float.width, 1)

    def _toggle_on_top(self) -> None:
        self.float = replace(self.float, on_top=not self.float.on_top)
        self.window.set_keep_above(self.float.on_top)
        self._schedule_save()

    def _popup_menu(self, event) -> None:
        Gtk = self._gtk
        menu = Gtk.Menu()

        refresh = Gtk.MenuItem(label=self.text["refresh"])
        refresh.connect("activate", lambda _item: self.refresh_now())
        menu.append(refresh)

        compact = Gtk.CheckMenuItem(label=self.text["compact"])
        compact.set_active(self.float.compact)
        compact.connect("toggled", lambda _item: self._toggle_compact())
        menu.append(compact)

        on_top = Gtk.CheckMenuItem(label=self.text["on_top"])
        on_top.set_active(self.float.on_top)
        on_top.connect("toggled", lambda _item: self._toggle_on_top())
        menu.append(on_top)

        menu.append(
            self._submenu(
                self.text["opacity"],
                [(f"{round(value * 100)}%", value) for value in (0.4, 0.6, 0.75, 0.9, 1.0)],
                self.float.opacity,
                self._set_opacity,
            )
        )
        menu.append(
            self._submenu(
                self.text["scale"],
                [(f"{round(value * 100)}%", value) for value in (0.8, 1.0, 1.25, 1.5)],
                self.float.scale,
                self._set_scale,
            )
        )
        menu.append(
            self._submenu(
                self.text["theme"],
                [(name, name) for name in sorted(self.themes)],
                self.settings.theme,
                self._set_theme,
            )
        )
        menu.append(
            self._submenu(
                self.text["language"],
                [(name, name) for name in LANGUAGES],
                self.settings.language,
                self._set_language,
            )
        )

        autostart = Gtk.CheckMenuItem(label=self.text["autostart"])
        autostart.set_active(autostart_installed())
        autostart.connect("toggled", self._on_autostart_toggled)
        menu.append(autostart)

        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label=self.text["quit"])
        quit_item.connect("activate", lambda _item: self.window.destroy())
        menu.append(quit_item)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _submenu(self, title: str, options, current, apply):
        Gtk = self._gtk
        item = Gtk.MenuItem(label=title)
        submenu = Gtk.Menu()
        group = []
        for label, value in options:
            entry = Gtk.RadioMenuItem(label=label)
            if group:
                entry.join_group(group[0])
            group.append(entry)
            entry.set_active(is_current(value, current))
            # Radio items fire twice per change, once for the one going off.
            # Acting on both would apply the option that was just left.
            entry.connect(
                "toggled",
                lambda widget, chosen=value: widget.get_active() and apply(chosen),
            )
            submenu.append(entry)
        item.set_submenu(submenu)
        return item

    def _on_autostart_toggled(self, item) -> None:
        if item.get_active():
            install_autostart()
        else:
            remove_autostart()

    # -- polling -------------------------------------------------------

    def refresh_now(self) -> None:
        self._wake.set()

    def _poll_loop(self) -> None:
        # The fetch retries for up to half a minute before giving up on an
        # asleep publisher, so it cannot run on the GTK thread: a frozen
        # widget looks broken in a way an old number does not.
        while not self._stop.is_set():
            try:
                snapshot = self._client.fetch()
            except MonitorError as exc:
                self._glib.idle_add(self._on_error, str(exc))
            else:
                self._glib.idle_add(self._on_snapshot, snapshot)
            # Waiting on an event rather than sleeping is what makes "refresh
            # now" immediate instead of "immediate, in up to a minute".
            self._wake.wait(self.settings.interval)
            self._wake.clear()

    def _on_snapshot(self, snapshot: Snapshot) -> bool:
        self.snapshot = snapshot
        self.message = None
        self.render()
        self._shrink_to_fit()
        return False

    def _on_error(self, message: str) -> bool:
        self.message = f"{self.text['no_data']}: {message}"
        self.render()
        return False

    def _on_destroy(self, _widget) -> None:
        self._stop.set()
        self._wake.set()
        if self._save_pending:
            self._glib.source_remove(self._save_pending)
        write_state(state_from(self.float), self.state_path)
        self._gtk.main_quit()

    # -- lifecycle -----------------------------------------------------

    def run(self) -> int:
        self.render()
        # Read the target before mapping the window. Showing it makes the
        # window manager place it and fire configure-event, which would
        # otherwise record that placement as the current position and drop the
        # saved one on the floor.
        target = (self.float.x, self.float.y)
        if None not in target:
            self._pending_place = target
            self.window.move(*target)
        self.window.show_all()
        self._glib.timeout_add_seconds(TICK_SECONDS, self._tick)
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self._gtk.main()
        return 0

    def _tick(self) -> bool:
        if self.snapshot is not None:
            self.render()
        return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A floating desktop widget for Codex, Claude, Grok and Gemini usage."
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--endpoint", default=None, help="publish.py URL to read")
    parser.add_argument(
        "--providers", default=None, help="comma separated subset to show"
    )
    parser.add_argument("--theme", default=None)
    parser.add_argument("--themes", type=Path, default=None, help="themes.ini to read")
    parser.add_argument("--list-themes", action="store_true")
    parser.add_argument("--language", choices=LANGUAGES, default=None)
    parser.add_argument("--interval", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--compact", action="store_true", default=None)
    parser.add_argument("--opacity", type=float, default=None)
    parser.add_argument("--scale", type=float, default=None)
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--no-on-top", dest="on_top", action="store_false", default=None)
    parser.add_argument("--x", type=int, default=None, help="starting x position")
    parser.add_argument("--y", type=int, default=None, help="starting y position")
    parser.add_argument(
        "--reset-position",
        action="store_true",
        help="forget the saved geometry and start from the defaults",
    )
    parser.add_argument(
        "--autostart",
        choices=("install", "remove", "status"),
        help="manage the login autostart entry, then exit",
    )
    return parser


def resolve_float_settings(
    args: argparse.Namespace, *, state: Optional[Mapping[str, Any]] = None
) -> FloatSettings:
    """Config file first, then the saved session, then the flags."""

    settings = load_float_settings(args.config or default_config_path())
    if state:
        settings = apply_state(settings, state)
    updates: Dict[str, Any] = {}
    if args.compact is not None:
        updates["compact"] = args.compact
    if args.on_top is not None:
        updates["on_top"] = args.on_top
    if args.opacity is not None:
        updates["opacity"] = clamp(args.opacity, MIN_OPACITY, MAX_OPACITY)
    if args.scale is not None:
        updates["scale"] = clamp(args.scale, MIN_SCALE, MAX_SCALE)
    if args.width is not None:
        updates["width"] = max(MIN_WIDTH, args.width)
    if args.x is not None:
        updates["x"] = args.x
    if args.y is not None:
        updates["y"] = args.y
    return replace(settings, **updates)


def monitor_namespace(args: argparse.Namespace) -> argparse.Namespace:
    """The arguments the monitor's own resolver expects.

    Endpoint, providers, theme, language, interval and timeout are read
    through `usage_monitor` rather than here. Reading the same config twice,
    in two ways, is how a widget and a terminal end up disagreeing about which
    providers are enabled.
    """

    return argparse.Namespace(
        config=args.config,
        endpoint=args.endpoint,
        providers=args.providers,
        theme=args.theme,
        themes=args.themes,
        language=args.language,
        interval=args.interval,
        timeout=args.timeout,
        color=None,
        clear=None,
    )


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.autostart:
        if args.autostart == "install":
            print(f"autostart written to {install_autostart()}")
        elif args.autostart == "remove":
            print("autostart removed" if remove_autostart() else "no autostart entry")
        else:
            print("installed" if autostart_installed() else "not installed")
        return 0

    try:
        settings = resolve_monitor_settings(monitor_namespace(args))
        themes = load_themes(settings.themes_file)
    except MonitorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list_themes:
        for name in sorted(themes):
            print(f"{'*' if name == settings.theme else ' '} {name}")
        return 0

    if settings.theme not in themes:
        print(
            f"error: unknown theme: {settings.theme} "
            f"(available: {', '.join(sorted(themes))})",
            file=sys.stderr,
        )
        return 2
    if settings.interval < 1:
        print("error: --interval must be at least 1 second", file=sys.stderr)
        return 2

    if args.reset_position:
        write_state({})
        state: Mapping[str, Any] = {}
    else:
        state = read_state()

    try:
        float_settings = resolve_float_settings(args, state=state)
    except MonitorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        widget = FloatingWidget(settings, float_settings, themes)
    except (ImportError, ValueError) as exc:
        print(
            "error: the floating widget needs GTK 3 and its Python bindings "
            f"(python3-gi, gir1.2-gtk-3.0): {exc}",
            file=sys.stderr,
        )
        return 3
    return widget.run()


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
