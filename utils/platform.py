"""
Platform helpers for win32 APIs with graceful fallbacks on non-Windows hosts.
"""

from __future__ import annotations

import sys
from utils.mock_win32 import (
    get_mock_user32,
    win32api as mock_win32api,
    win32con as mock_win32con,
    win32gui as mock_win32gui,
)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    import win32api as _win32api
    import win32con as _win32con
    import win32gui as _win32gui

    win32api = _win32api  # type: ignore[assignment]
    win32con = _win32con  # type: ignore[assignment]
    win32gui = _win32gui  # type: ignore[assignment]
    user32 = ctypes.windll.user32
else:  # pragma: no cover - exercised implicitly on non-Windows hosts
    win32api = mock_win32api
    win32con = mock_win32con
    win32gui = mock_win32gui
    user32 = get_mock_user32().windll.user32

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Windows 10 1703+).
_PER_MONITOR_AWARE_V2 = -4
_PROCESS_PER_MONITOR_DPI_AWARE = 2
_dpi_awareness_requested = False


def ensure_dpi_awareness() -> None:
    """Make this process per-monitor DPI aware, once, before any geometry is read.

    Every coordinate the app handles must be in physical pixels: ``mss``
    captures physical pixels (and switches the process to per-monitor awareness
    itself when ``ScreenCapture`` is created), and ``SetCursorPos`` /
    ``SetWindowPos`` then expect physical pixels too. ``python.exe`` starts
    DPI *unaware*, so anything read before that switch is virtualized: a
    1920x1080 monitor at 150 % reports as 1280x720. Calling this first keeps
    monitor bounds, window rects, capture and clicks in one coordinate space.

    Idempotent and a no-op off Windows. Once awareness is fixed (by a manifest
    or an earlier call) Windows rejects further changes; that is harmless.
    """
    global _dpi_awareness_requested
    if not IS_WINDOWS or _dpi_awareness_requested:
        return
    _dpi_awareness_requested = True

    windll = ctypes.windll
    try:
        set_context = windll.user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        if set_context(ctypes.c_void_p(_PER_MONITOR_AWARE_V2)):
            return
    except AttributeError:  # older than Windows 10 1703
        pass
    try:
        # S_OK, or E_ACCESSDENIED when awareness is already fixed.
        windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE)
    except (AttributeError, OSError):  # older than Windows 8.1
        windll.user32.SetProcessDPIAware()


__all__ = [
    "IS_WINDOWS",
    "ensure_dpi_awareness",
    "win32api",
    "win32con",
    "win32gui",
    "user32",
]
