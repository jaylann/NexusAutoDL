"""
Mouse click control utilities.
"""

from __future__ import annotations

import time

from utils.logger import get_logger
from utils.platform import IS_WINDOWS, win32api, win32con

logger = get_logger(__name__)


class ClickController:
    """Handles mouse clicking operations."""

    def __init__(self, restore_cursor: bool = True) -> None:
        """
        Initialize click controller.

        Args:
            restore_cursor: Whether to restore cursor position after clicking
        """
        self.restore_cursor: bool = restore_cursor
        if not IS_WINDOWS:
            logger.debug("ClickController running with mock win32 bindings")
        logger.info("Click controller initialized")

    def click(self, x: int, y: int, delay: float = 0.1) -> None:
        """
        Perform mouse click at coordinates.

        Args:
            x: X coordinate
            y: Y coordinate
            delay: Delay between mouse down and up
        """
        original_pos: tuple[int, int] | None = (
            win32api.GetCursorPos() if self.restore_cursor else None
        )

        win32api.SetCursorPos((x, y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)

        if delay > 0:
            time.sleep(delay)

        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)

        logger.info(f"Clicked at ({x}, {y})")

        if self.restore_cursor and original_pos:
            win32api.SetCursorPos(original_pos)

    def double_click(self, x: int, y: int, delay: float = 0.1) -> None:
        """
        Perform double click at coordinates.

        Args:
            x: X coordinate
            y: Y coordinate
            delay: Delay between clicks
        """
        self.click(x, y, delay=delay)
        time.sleep(delay)
        self.click(x, y, delay=delay)

        logger.info(f"Double-clicked at ({x}, {y})")

    def move_to(self, x: int, y: int) -> None:
        """
        Move cursor to coordinates without clicking.

        Args:
            x: X coordinate
            y: Y coordinate
        """
        win32api.SetCursorPos((x, y))
        logger.debug(f"Moved cursor to ({x}, {y})")
