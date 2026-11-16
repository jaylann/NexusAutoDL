"""
Window management for browser and Vortex positioning.
"""

from __future__ import annotations

import subprocess
import time
from typing import Optional

from models import BoundingBox, BrowserType, Monitor
from utils.platform import (
    IS_WINDOWS,
    ensure_dpi_awareness,
    user32,
    win32api,
    win32gui,
)
from utils.logger import get_logger

logger = get_logger(__name__)


class WindowManager:
    """Manages window positioning and browser launching."""

    def __init__(self, monitors: list[Monitor]) -> None:
        """
        Initialize window manager.

        Args:
            monitors: List of available monitors
        """
        if not IS_WINDOWS:
            raise RuntimeError("WindowManager is only available on Windows hosts")

        self.monitors: list[Monitor] = monitors
        logger.info("Window manager initialized")

    def launch_browser(self, browser: BrowserType) -> None:
        """
        Launch and position browser.

        Args:
            browser: Browser type to launch

        Raises:
            ValueError: If browser type not supported
        """
        commands: dict[BrowserType, str] = {
            BrowserType.CHROME: r"start chrome about:blank",
            BrowserType.FIREFOX: r"start firefox",
        }

        window_names: dict[BrowserType, str] = {
            BrowserType.CHROME: "about:blank - Google Chrome",
            BrowserType.FIREFOX: "Mozilla Firefox",
        }

        if browser not in commands:
            raise ValueError(f"Browser '{browser}' not supported")

        logger.info(f"Launching {browser.value}")
        subprocess.Popen(commands[browser], shell=True)
        time.sleep(0.4)

        window_name: str = window_names[browser]
        h_browser: int = user32.FindWindowW(None, window_name)

        if h_browser == 0:
            logger.warning(f"Could not find {browser.value} window")
            return

        if len(self.monitors) > 1:
            primary: Monitor = self.monitors[0]
            user32.ShowWindow(h_browser, 1)  # SW_SHOWNORMAL
            win32gui.SetWindowPos(
                h_browser,
                None,
                primary.x,
                primary.y,
                primary.width,
                primary.height,
                True,
            )
            user32.ShowWindow(h_browser, 3)  # SW_MAXIMIZE

        logger.info(f"{browser.value} positioned successfully")

    def position_vortex(self) -> None:
        """Position Vortex window on secondary monitor."""
        vortex_handle: int = user32.FindWindowW(None, "Vortex")

        if vortex_handle == 0:
            logger.warning("Vortex window not found")
            return

        logger.info("Found Vortex window")

        if len(self.monitors) > 1:
            secondary: Monitor = self.monitors[1]
            user32.ShowWindow(vortex_handle, 1)  # SW_SHOWNORMAL
            win32gui.SetWindowPos(
                vortex_handle,
                None,
                secondary.x,
                secondary.y,
                secondary.width,
                secondary.height,
                True,
            )
            user32.ShowWindow(vortex_handle, 3)  # SW_MAXIMIZE

        logger.info("Vortex positioned successfully")

    def position_window_by_title(self, title_substr: str) -> None:
        """
        Position window matching title substring.

        Args:
            title_substr: Substring to match in window title

        Raises:
            RuntimeError: If no matching window found
        """
        handles: list[int] = []

        def enum_callback(hwnd: int, _) -> bool:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and title_substr.lower() in title.lower():
                    handles.append(hwnd)
            return True

        win32gui.EnumWindows(enum_callback, None)

        if not handles:
            raise RuntimeError(f"No visible window contains title: {title_substr!r}")

        handle: int = handles[0]
        window_title: str = win32gui.GetWindowText(handle)
        logger.info(f"Found window '{window_title}' matching '{title_substr}'")

        if len(self.monitors) > 1:
            primary = self.monitors[0]
            user32.ShowWindow(handle, 1)
            win32gui.SetWindowPos(
                handle, None, primary.x, primary.y, primary.width, primary.height, True
            )
            user32.ShowWindow(handle, 3)

        logger.info(f"Window '{window_title}' positioned")

    def get_vortex_bbox(self) -> Optional[BoundingBox]:
        """
        Get Vortex window bounding box.

        Returns:
            BoundingBox if Vortex found, None otherwise
        """
        vortex_handle: int = user32.FindWindowW(None, "Vortex")

        if vortex_handle == 0:
            logger.warning("Vortex window not found")
            return None

        rect = win32gui.GetWindowRect(vortex_handle)
        bbox = BoundingBox(x1=rect[0], y1=rect[1], x2=rect[2], y2=rect[3])

        logger.debug(f"Vortex bbox: {bbox}")
        return bbox

    @staticmethod
    def get_all_monitors() -> list[Monitor]:
        """
        Get all available monitors in physical pixels.

        Makes the process per-monitor DPI aware first (``ensure_dpi_awareness``),
        so the bounds share one coordinate space with ``mss`` capture,
        ``GetWindowRect`` and ``SetCursorPos``. Without it, a scaled monitor
        reports logical bounds (1920x1080 at 150 % -> 1280x720).

        Returns:
            List of Monitor objects
        """
        ensure_dpi_awareness()
        raw_monitors = win32api.EnumDisplayMonitors(None, None)
        monitors: list[Monitor] = []

        for _, _, rect in raw_monitors:
            x, y, right, bottom = rect
            monitor = Monitor(x=x, y=y, width=right - x, height=bottom - y)
            monitors.append(monitor)

        logger.info(f"Found {len(monitors)} monitors: {monitors}")
        return monitors
