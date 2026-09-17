"""Where the tray finds the floating desktop widget, and how it talks to it.

The widget lives with the dashboard rather than with the tray, because it
reads the dashboard's data layer, its palettes and its config. The tray only
needs to open it, close it, and know which of the two it is looking at, so
this is the whole of what crosses between them.

The two layouts to find it in are the repository, where `dashboard/` is a
sibling of this directory, and an install, where everything lands in one
directory. A machine with neither gets a tray without the menu entry rather
than a tray that fails to start.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Optional


MODULE_NAME = "usage_float"


def float_widget_dir() -> Optional[Path]:
    here = Path(__file__).resolve().parent
    for candidate in (here, here.parent / "dashboard"):
        if (candidate / f"{MODULE_NAME}.py").is_file():
            return candidate
    return None


def _module() -> Optional[Any]:
    directory = float_widget_dir()
    if directory is None:
        return None
    # Appended rather than inserted: this is somebody else's directory, and
    # the tray's own modules have to keep winning the names they share.
    if str(directory) not in sys.path:
        sys.path.append(str(directory))
    try:
        return importlib.import_module(MODULE_NAME)
    except ImportError:
        return None


def float_widget_available() -> bool:
    return _module() is not None


def float_is_running() -> bool:
    module = _module()
    return bool(module and module.float_is_running())


def start_float() -> bool:
    """Start the widget, or bring a running one to the front."""

    module = _module()
    if module is None:
        return False
    module.start_float()
    return True


def stop_float() -> bool:
    module = _module()
    return bool(module and module.stop_float())
