#!/usr/bin/env python3
import gi
gi.require_version('AppIndicator3', '0.1')
gi.require_version('Gtk', '3.0')
from gi.repository import AppIndicator3, Gtk, GLib
import json
import time
import signal
import html
from pathlib import Path
from typing import Sequence

RATE_LIMITS_FILE = Path.home() / '.claude' / 'rate_limits_live.json'
ICON_TMPS = ['/tmp/claude-rate-0.svg', '/tmp/claude-rate-1.svg']
POLL_INTERVAL = 60  # seconds

NEUTRAL_TEXT_COLOR = '#FFFFFF'
COLORS = {
    'green':  '#00AF50',
    'yellow': '#E6C800',
    'red':    '#FF5555',
}
TextSegment = tuple[str, str]
IconText = str | Sequence[TextSegment]
CLAUDE_LOGO_PATH = Path(__file__).parent / 'assets' / 'claude-logo.svg'


def _load_logo_markup():
    try:
        lines = CLAUDE_LOGO_PATH.read_text(encoding='utf-8').splitlines()
    except OSError:
        return None
    return next(
        (line.strip() for line in lines if line.lstrip().startswith('<path ')),
        None,
    )


CLAUDE_LOGO_MARKUP = _load_logo_markup()


def make_icon_svg(text: IconText, color: str):
    """Generate an SVG icon with the Claude logo and a coloured text label."""
    fill = COLORS.get(color, COLORS['green'])
    plain = _plain_text(text)
    spans = _render_text_spans(text, color)
    # ~7.5px per char + logo(22) + padding(14)
    width = max(90, int(len(plain) * 7.5) + 36)
    brand_mark = (
        f'<g transform="translate(1 1) scale(0.833333)">'
        f'{CLAUDE_LOGO_MARKUP}</g>'
        if CLAUDE_LOGO_MARKUP
        else f'<circle cx="8" cy="11" r="6" fill="{fill}"/>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="22">'
        f'{brand_mark}'
        f'<text x="25" y="15" font-family="monospace,DejaVu Sans Mono"'
        f' font-size="11" xml:space="preserve">{spans}</text>'
        f'</svg>'
    )


def _plain_text(text: IconText) -> str:
    if isinstance(text, str):
        return text
    return ''.join(segment for segment, _ in text)


def _render_text_spans(text: IconText, default_color: str) -> str:
    segments = [(text, default_color)] if isinstance(text, str) else text
    return ''.join(
        f'<tspan fill="{COLORS.get(color, color)}">{html.escape(segment)}</tspan>'
        for segment, color in segments
    )


class ClaudeRateIndicator:
    def __init__(self):
        self._icon_idx = 0
        # Write initial icon so AppIndicator has a valid file path
        Path(ICON_TMPS[0]).write_text(make_icon_svg('--', 'green'))

        self.indicator = AppIndicator3.Indicator.new(
            'claude-rate-indicator',
            ICON_TMPS[0],
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()

        self.item_5h = Gtk.MenuItem(label='⚡ 5H: --')
        self.item_5h.set_sensitive(False)
        self.menu.append(self.item_5h)

        self.item_7d = Gtk.MenuItem(label='📅 7D: --')
        self.item_7d.set_sensitive(False)
        self.menu.append(self.item_7d)

        self.item_updated = Gtk.MenuItem(label='Updated: --')
        self.item_updated.set_sensitive(False)
        self.menu.append(self.item_updated)

        self.menu.append(Gtk.SeparatorMenuItem())

        item_refresh = Gtk.MenuItem(label='Refresh')
        item_refresh.connect('activate', lambda _: self.update())
        self.menu.append(item_refresh)

        item_quit = Gtk.MenuItem(label='Quit')
        item_quit.connect('activate', lambda _: Gtk.main_quit())
        self.menu.append(item_quit)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        GLib.timeout_add(500, self._initial_update)
        GLib.timeout_add_seconds(POLL_INTERVAL, self._poll)

    def _initial_update(self):
        self.update()
        return False  # run once

    def _poll(self):
        self.update()
        return True

    def _set_icon(self, text: IconText, color: str):
        # Alternate between two tmp paths to force GNOME Shell to reload the SVG
        self._icon_idx ^= 1
        path = ICON_TMPS[self._icon_idx]
        Path(path).write_text(make_icon_svg(text, color))
        self.indicator.set_icon_full(path, f'Claude rate limit: {_plain_text(text)}')

    def update(self):
        data = self._read_data()

        if data is None:
            self._set_icon('--', 'green')
            self.item_5h.set_label('⚡ 5H: no data')
            self.item_7d.set_label('📅 7D: no data')
            self.item_updated.set_label('Updated: --')
            return

        u5h = int(data.get('utilization_5h', 0))
        u7d = int(data.get('utilization_7d', 0))
        reset_5h = int(data.get('reset_5h', 0))
        reset_7d = int(data.get('reset_7d', 0))
        updated_at = int(data.get('updated_at', 0))

        now = int(time.time())
        cd5 = self._countdown(reset_5h, now)
        cd7 = self._countdown(reset_7d, now)

        max_pct = max(u5h, u7d)
        color = 'red' if max_pct >= 90 else 'yellow' if max_pct >= 70 else 'green'
        self._set_icon(
            [
                (f'{u5h}%', self._color_for_pct(u5h)),
                ('|', NEUTRAL_TEXT_COLOR),
                (f'{u7d}%', self._color_for_pct(u7d)),
                (f'  ⟳{cd5}', NEUTRAL_TEXT_COLOR),
            ],
            color,
        )

        t5 = self._fmt_time(reset_5h, '%H:%M')
        self.item_5h.set_label(f'⚡ 5H: {u5h}%  ⟳ {t5} ({cd5})')

        t7 = self._fmt_time(reset_7d, '%m/%d %H:%M')
        self.item_7d.set_label(f'📅 7D: {u7d}%  ⟳ {t7} ({cd7})')

        if updated_at:
            self.item_updated.set_label(f'Updated: {self._fmt_time(updated_at, "%H:%M:%S")}')

    def _read_data(self):
        try:
            return json.loads(RATE_LIMITS_FILE.read_text())
        except Exception:
            return None

    def _countdown(self, reset_ts, now):
        secs = reset_ts - now
        if secs <= 0:
            return 'soon'
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d > 0:
            return f'{d}d{h}h'
        if h > 0:
            return f'{h}h{m}m'
        return f'{m}m'

    def _fmt_time(self, ts, fmt):
        try:
            return time.strftime(fmt, time.localtime(ts))
        except Exception:
            return '--'

    def _color_for_pct(self, value):
        if value >= 90:
            return 'red'
        if value >= 70:
            return 'yellow'
        return 'green'


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    ClaudeRateIndicator()
    Gtk.main()


if __name__ == '__main__':
    main()
