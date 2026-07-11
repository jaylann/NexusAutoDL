"""
Mouse click control utilities.
"""

from __future__ import annotations

import time
from typing import Optional

from services.geometry import VirtualDesktop
from utils.platform import IS_WINDOWS, send_input_mouse, win32api, win32con
from utils.logger import get_logger

logger = get_logger(__name__)


class ClickController:
    """Performs validated mouse clicks in virtual-desktop coordinates."""

    def __init__(self, desktop: VirtualDesktop, restore_cursor: bool = True) -> None:
        """
        Initialize click controller.

        Args:
            desktop: Monitor layout used to validate click targets
            restore_cursor: Whether to restore cursor position after clicking
        """
        self.desktop = desktop
        self.restore_cursor: bool = restore_cursor
        if not IS_WINDOWS:
            logger.debug("ClickController running with mock win32 bindings")
        logger.info("Click controller initialized")

    def click(self, x: int, y: int, delay: float = 0.1) -> bool:
        """
        Perform mouse click at virtual-desktop coordinates.

        The target is clamped onto the nearest monitor (detections can land a
        few pixels past an edge); targets beyond any monitor are refused so a
        bad detection never clicks a wildly wrong location.

        Args:
            x: X coordinate
            y: Y coordinate
            delay: Delay between mouse down and up

        Returns:
            True if the click was performed
        """
        target = self.desktop.clamp_to_nearest(x, y)
        if target is None:
            logger.error(f"Refusing click at ({x}, {y}): outside every monitor")
            return False
        if target != (x, y):
            logger.debug(f"Clamped click ({x}, {y}) -> {target}")

        original_pos: Optional[tuple[int, int]] = (
            win32api.GetCursorPos() if self.restore_cursor else None
        )

        win32api.SetCursorPos(target)
        self._button_event(win32con.MOUSEEVENTF_LEFTDOWN, target)

        if delay > 0:
            time.sleep(delay)

        self._button_event(win32con.MOUSEEVENTF_LEFTUP, target)

        logger.info(f"Clicked at {target}")

        if self.restore_cursor and original_pos:
            win32api.SetCursorPos(original_pos)
        return True

    def _button_event(self, flags: int, target: tuple[int, int]) -> None:
        """Send a button event, falling back to legacy mouse_event."""
        try:
            send_input_mouse(flags)
        except OSError:
            logger.warning("SendInput blocked; falling back to mouse_event")
            win32api.mouse_event(flags, target[0], target[1], 0, 0)

    def double_click(self, x: int, y: int, delay: float = 0.1) -> bool:
        """
        Perform double click at coordinates.

        Args:
            x: X coordinate
            y: Y coordinate
            delay: Delay between clicks

        Returns:
            True if both clicks were performed
        """
        if not self.click(x, y, delay=delay):
            return False
        time.sleep(delay)
        clicked = self.click(x, y, delay=delay)
        if clicked:
            logger.info(f"Double-clicked at ({x}, {y})")
        return clicked

    def move_to(self, x: int, y: int) -> None:
        """
        Move cursor to coordinates without clicking.

        Args:
            x: X coordinate
            y: Y coordinate
        """
        win32api.SetCursorPos((x, y))
        logger.debug(f"Moved cursor to ({x}, {y})")
