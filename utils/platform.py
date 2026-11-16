"""
Platform helpers for win32 APIs with graceful fallbacks on non-Windows hosts.
"""

from __future__ import annotations

import sys

from utils.mock_win32 import (
    get_mock_user32,
)
from utils.mock_win32 import (
    win32api as mock_win32api,
)
from utils.mock_win32 import (
    win32con as mock_win32con,
)
from utils.mock_win32 import (
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

__all__ = ["IS_WINDOWS", "user32", "win32api", "win32con", "win32gui"]
