"""
Mock win32 API for testing on non-Windows platforms.

All state lives in a single deterministic ``MockDisplayState`` so tests can
configure arbitrary monitor layouts / windows and assert on the exact cursor
positions and mouse events produced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

_DEFAULT_MONITORS: list[tuple[int, int, int, int]] = [
    (0, 0, 1920, 1080),  # primary
    (1920, 0, 3840, 1080),  # secondary
]
_DEFAULT_WINDOWS: dict[str, tuple[int, int, int, int]] = {
    "Vortex": (100, 100, 1820, 980),
    "Chrome": (50, 50, 1870, 1030),
}
_DEFAULT_CURSOR: tuple[int, int] = (200, 200)
_HANDLE_BASE = 1000


@dataclass
class MockDisplayState:
    """Mutable state backing every mock win32 call."""

    monitors: list[tuple[int, int, int, int]] = field(
        default_factory=lambda: list(_DEFAULT_MONITORS)
    )
    windows: dict[str, tuple[int, int, int, int]] = field(
        default_factory=lambda: dict(_DEFAULT_WINDOWS)
    )
    cursor: tuple[int, int] = _DEFAULT_CURSOR
    events: list[tuple] = field(default_factory=list)

    def title_for_handle(self, hwnd: int) -> Optional[str]:
        titles = list(self.windows)
        idx = hwnd - _HANDLE_BASE
        return titles[idx] if 0 <= idx < len(titles) else None

    def handle_for_title(self, title: str) -> int:
        for idx, key in enumerate(self.windows):
            if key == title:
                return _HANDLE_BASE + idx
        return 0


state = MockDisplayState()


def configure(
    monitors: Optional[list[tuple[int, int, int, int]]] = None,
    windows: Optional[dict[str, tuple[int, int, int, int]]] = None,
    cursor: tuple[int, int] = _DEFAULT_CURSOR,
) -> None:
    """Point the mocks at a specific display layout (for tests)."""
    state.monitors = list(monitors) if monitors is not None else list(_DEFAULT_MONITORS)
    state.windows = dict(windows) if windows is not None else dict(_DEFAULT_WINDOWS)
    state.cursor = cursor
    state.events = []


def reset() -> None:
    """Restore the default dual-1080p layout."""
    configure()


def send_input_mouse(flags: int) -> None:
    """Record a button event at the current cursor position."""
    state.events.append(("mouse", flags, state.cursor))
    logging.debug(f"[MOCK] send_input_mouse: flags={flags} at {state.cursor}")


class MockWin32API:
    """Mock win32api module."""

    @staticmethod
    def GetCursorPos() -> tuple[int, int]:
        return state.cursor

    @staticmethod
    def SetCursorPos(pos: tuple[int, int]) -> None:
        state.cursor = (int(pos[0]), int(pos[1]))
        state.events.append(("move", state.cursor))
        logging.debug(f"[MOCK] SetCursorPos: {pos}")

    @staticmethod
    def mouse_event(event: int, x: int, y: int, data: int, extra_info: int) -> None:
        state.events.append(("mouse_event", event, state.cursor))
        logging.debug(f"[MOCK] mouse_event: {event} at ({x}, {y})")

    @staticmethod
    def EnumDisplayMonitors(
        hdc, rect
    ) -> list[tuple[int, int, tuple[int, int, int, int]]]:
        return [(0, 0, monitor) for monitor in state.monitors]


class MockWin32GUI:
    """Mock win32gui module."""

    @staticmethod
    def GetWindowRect(hwnd: int) -> tuple[int, int, int, int]:
        title = state.title_for_handle(hwnd)
        if title is not None:
            return state.windows[title]
        return (100, 100, 1820, 980)

    @staticmethod
    def GetWindowText(hwnd: int) -> str:
        return state.title_for_handle(hwnd) or ""

    @staticmethod
    def IsWindowVisible(hwnd: int) -> bool:
        return True

    @staticmethod
    def EnumWindows(callback, data) -> None:
        for idx in range(len(state.windows)):
            callback(_HANDLE_BASE + idx, data)

    @staticmethod
    def SetWindowPos(
        hwnd: int, after, x: int, y: int, w: int, h: int, flags: bool
    ) -> None:
        title = state.title_for_handle(hwnd)
        if title is not None:
            state.windows[title] = (x, y, x + w, y + h)
        logging.debug(f"[MOCK] SetWindowPos: hwnd={hwnd} pos=({x},{y}) size=({w},{h})")


class MockWin32Con:
    """Mock win32con constants."""

    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004


class MockUserDLL:
    """Mock user32.dll."""

    @staticmethod
    def FindWindowW(class_name, window_name: Optional[str]) -> int:
        if window_name:
            handle = state.handle_for_title(window_name)
            logging.debug(f"[MOCK] FindWindowW: {window_name} -> {handle}")
            return handle
        return 0

    @staticmethod
    def ShowWindow(hwnd: int, cmd: int) -> None:
        logging.debug(f"[MOCK] ShowWindow: hwnd={hwnd} cmd={cmd}")


# Create singleton instances
win32api = MockWin32API()
win32gui = MockWin32GUI()
win32con = MockWin32Con()


def get_mock_user32():
    """Get mock user32 DLL."""
    return type(
        "MockCTypes", (), {"windll": type("windll", (), {"user32": MockUserDLL()})}
    )()


# Export for easy importing
__all__ = [
    "win32api",
    "win32gui",
    "win32con",
    "get_mock_user32",
    "state",
    "configure",
    "reset",
    "send_input_mouse",
]
