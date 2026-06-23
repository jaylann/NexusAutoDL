"""
Mock win32 API for testing on non-Windows platforms.
Provides simulated window management and mouse control.
"""

from __future__ import annotations

import logging
import random
from typing import Optional


class MockWin32API:
    """Mock win32api module."""

    @staticmethod
    def GetCursorPos() -> tuple[int, int]:
        """Get fake cursor position."""
        return (random.randint(100, 1000), random.randint(100, 800))

    @staticmethod
    def SetCursorPos(pos: tuple[int, int]) -> None:
        """Set fake cursor position."""
        logging.debug(f"[MOCK] SetCursorPos: {pos}")

    @staticmethod
    def mouse_event(event: int, x: int, y: int, data: int, extra_info: int) -> None:
        """Simulate mouse event."""
        event_names = {
            0x0002: "MOUSEEVENTF_LEFTDOWN",
            0x0004: "MOUSEEVENTF_LEFTUP",
        }
        event_name = event_names.get(event, f"UNKNOWN({event})")
        logging.debug(f"[MOCK] mouse_event: {event_name} at ({x}, {y})")

    @staticmethod
    def EnumDisplayMonitors(
        hdc, rect
    ) -> list[tuple[int, int, tuple[int, int, int, int]]]:
        """Return fake monitor list."""
        # Simulate dual monitor setup
        return [
            (0, 0, (0, 0, 1920, 1080)),  # Primary monitor
            (0, 0, (1920, 0, 3840, 1080)),  # Secondary monitor
        ]


class MockWin32GUI:
    """Mock win32gui module."""

    _windows = {
        "Vortex": (100, 100, 1820, 980),
        "Chrome": (50, 50, 1870, 1030),
    }

    @staticmethod
    def GetWindowRect(hwnd: int) -> tuple[int, int, int, int]:
        """Get fake window rect."""
        # Return a fake rect for any handle
        return (100, 100, 1820, 980)

    @staticmethod
    def GetWindowText(hwnd: int) -> str:
        """Get fake window title."""
        titles = ["Vortex", "Chrome", "Firefox", "Wabbajack"]
        return random.choice(titles)

    @staticmethod
    def IsWindowVisible(hwnd: int) -> bool:
        """Check if window is visible."""
        return True

    @staticmethod
    def EnumWindows(callback, data) -> None:
        """Enumerate fake windows."""
        # Simulate a few windows
        fake_windows = [1001, 1002, 1003, 1004]
        for hwnd in fake_windows:
            callback(hwnd, data)

    @staticmethod
    def SetWindowPos(
        hwnd: int, after, x: int, y: int, w: int, h: int, flags: bool
    ) -> None:
        """Set fake window position."""
        logging.debug(f"[MOCK] SetWindowPos: hwnd={hwnd} pos=({x},{y}) size=({w},{h})")


class MockWin32Con:
    """Mock win32con constants."""

    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004


class MockUserDLL:
    """Mock user32.dll."""

    @staticmethod
    def FindWindowW(class_name, window_name: Optional[str]) -> int:
        """Find fake window by name."""
        if window_name:
            logging.debug(f"[MOCK] FindWindowW: {window_name}")
            # Return a fake handle
            return random.randint(1000, 9999)
        return 0

    @staticmethod
    def ShowWindow(hwnd: int, cmd: int) -> None:
        """Show fake window."""
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
__all__ = ["win32api", "win32gui", "win32con", "get_mock_user32"]
