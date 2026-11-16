"""
Mock win32 API for testing on non-Windows platforms.

All state lives in a single deterministic ``MockDisplayState`` so tests can
configure arbitrary monitor layouts / windows and assert on the exact cursor
positions and mouse events produced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

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
    # Real display mode per monitor index, where it differs from the reported
    # rect: what a process sees when Windows scales its coordinates.
    display_modes: dict[int, tuple[int, int]] = field(default_factory=dict)

    def title_for_handle(self, hwnd: int) -> str | None:
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
    monitors: list[tuple[int, int, int, int]] | None = None,
    windows: dict[str, tuple[int, int, int, int]] | None = None,
    cursor: tuple[int, int] = _DEFAULT_CURSOR,
    display_modes: dict[int, tuple[int, int]] | None = None,
) -> None:
    """Point the mocks at a specific display layout (for tests)."""
    state.monitors = list(monitors) if monitors is not None else list(_DEFAULT_MONITORS)
    state.windows = dict(windows) if windows is not None else dict(_DEFAULT_WINDOWS)
    state.cursor = cursor
    state.events = []
    state.display_modes = dict(display_modes or {})


def reset() -> None:
    """Restore the default dual-1080p layout."""
    configure()


def send_input_mouse(flags: int) -> None:
    """Record a button event at the current cursor position."""
    state.events.append(("mouse", flags, state.cursor))
    logger.debug(f"[MOCK] send_input_mouse: flags={flags} at {state.cursor}")


@dataclass(frozen=True)
class _MockDevMode:
    """The two DEVMODE fields the app reads."""

    PelsWidth: int
    PelsHeight: int


class MockWin32API:
    """Mock win32api module."""

    @staticmethod
    def GetCursorPos() -> tuple[int, int]:
        return state.cursor

    @staticmethod
    def SetCursorPos(pos: tuple[int, int]) -> None:
        state.cursor = (int(pos[0]), int(pos[1]))
        state.events.append(("move", state.cursor))
        logger.debug(f"[MOCK] SetCursorPos: {pos}")

    @staticmethod
    def mouse_event(event: int, x: int, y: int, data: int, extra_info: int) -> None:
        state.events.append(("mouse_event", event, state.cursor))
        logger.debug(f"[MOCK] mouse_event: {event} at ({x}, {y})")

    @staticmethod
    def EnumDisplayMonitors(
        hdc, rect
    ) -> list[tuple[int, int, tuple[int, int, int, int]]]:
        # The first item is the monitor handle; here it is the monitor's index.
        return [(index, 0, monitor) for index, monitor in enumerate(state.monitors)]

    @staticmethod
    def GetMonitorInfo(handle: int) -> dict[str, object]:
        return {
            "Monitor": state.monitors[handle],
            "Device": f"\\\\.\\DISPLAY{handle + 1}",
        }

    @staticmethod
    def EnumDisplaySettings(device: str, mode: int) -> _MockDevMode:
        index = int(device.rsplit("DISPLAY", 1)[1]) - 1
        left, top, right, bottom = state.monitors[index]
        width, height = state.display_modes.get(index, (right - left, bottom - top))
        return _MockDevMode(PelsWidth=width, PelsHeight=height)


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
        hwnd: int, after, x: int, y: int, w: int, h: int, flags: int
    ) -> None:
        # Honours the flags like the real call: SWP_NOSIZE keeps the window's
        # size and SWP_NOMOVE its position. A mock that always applied both
        # hid a call that passed SWP_NOSIZE by accident.
        title = state.title_for_handle(hwnd)
        if title is not None:
            left, top, right, bottom = state.windows[title]
            if flags & MockWin32Con.SWP_NOSIZE:
                w, h = right - left, bottom - top
            if flags & MockWin32Con.SWP_NOMOVE:
                x, y = left, top
            state.windows[title] = (x, y, x + w, y + h)
        logger.debug(f"[MOCK] SetWindowPos: hwnd={hwnd} pos=({x},{y}) size=({w},{h})")


class MockWin32Con:
    """Mock win32con constants."""

    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    ENUM_CURRENT_SETTINGS = -1
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOZORDER = 0x0004


class MockUserDLL:
    """Mock user32.dll."""

    @staticmethod
    def FindWindowW(class_name, window_name: str | None) -> int:
        if window_name:
            handle = state.handle_for_title(window_name)
            logger.debug(f"[MOCK] FindWindowW: {window_name} -> {handle}")
            return handle
        return 0

    @staticmethod
    def ShowWindow(hwnd: int, cmd: int) -> None:
        logger.debug(f"[MOCK] ShowWindow: hwnd={hwnd} cmd={cmd}")


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
    "configure",
    "get_mock_user32",
    "reset",
    "send_input_mouse",
    "state",
    "win32api",
    "win32con",
    "win32gui",
]
