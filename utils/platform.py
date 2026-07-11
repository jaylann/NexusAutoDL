"""
Platform helpers for win32 APIs with graceful fallbacks on non-Windows hosts.
"""

from __future__ import annotations

import sys
from utils.mock_win32 import (
    get_mock_user32,
    send_input_mouse as mock_send_input_mouse,
    win32api as mock_win32api,
    win32con as mock_win32con,
    win32gui as mock_win32gui,
)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    import win32api as _win32api
    import win32con as _win32con
    import win32gui as _win32gui

    win32api = _win32api  # type: ignore[assignment]
    win32con = _win32con  # type: ignore[assignment]
    win32gui = _win32gui  # type: ignore[assignment]
    user32 = ctypes.windll.user32

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        )

    class _INPUT_UNION(ctypes.Union):
        _fields_ = (("mi", _MOUSEINPUT),)

    class _INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", _INPUT_UNION))

    _INPUT_MOUSE = 0

    def send_input_mouse(flags: int) -> None:
        """Send a mouse button event at the current cursor position.

        Raises OSError when the input was blocked (e.g. UIPI), letting the
        caller fall back to the legacy mouse_event API.
        """
        event = _INPUT(
            type=_INPUT_MOUSE,
            union=_INPUT_UNION(mi=_MOUSEINPUT(0, 0, 0, flags, 0, 0)),
        )
        sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT))
        if sent != 1:
            raise ctypes.WinError()

else:  # pragma: no cover - exercised implicitly on non-Windows hosts
    win32api = mock_win32api
    win32con = mock_win32con
    win32gui = mock_win32gui
    user32 = get_mock_user32().windll.user32
    send_input_mouse = mock_send_input_mouse

__all__ = [
    "IS_WINDOWS",
    "win32api",
    "win32con",
    "win32gui",
    "user32",
    "send_input_mouse",
]
