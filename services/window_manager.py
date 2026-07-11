"""
Window management for browser and Vortex positioning.
"""

from __future__ import annotations

import subprocess
import time
from typing import Optional

from models import BoundingBox, BrowserType, Monitor
from services.geometry import MonitorFrame, VirtualDesktop
from utils.dpi import ensure_dpi_awareness
from utils.platform import IS_WINDOWS, user32, win32api, win32gui
from utils.logger import get_logger

logger = get_logger(__name__)


class WindowManager:
    """Manages window positioning and browser launching."""

    def __init__(self, desktop: VirtualDesktop) -> None:
        """
        Initialize window manager.

        Args:
            desktop: Monitor layout in physical virtual-desktop pixels
        """
        if not IS_WINDOWS:
            raise RuntimeError("WindowManager is only available on Windows hosts")

        self.desktop = desktop
        logger.info("Window manager initialized")

    def _place_window(self, handle: int, frame: MonitorFrame) -> None:
        """Move a window onto a monitor and maximize it there."""
        user32.ShowWindow(handle, 1)  # SW_SHOWNORMAL
        win32gui.SetWindowPos(
            handle,
            None,
            frame.left,
            frame.top,
            frame.width,
            frame.height,
            True,
        )
        user32.ShowWindow(handle, 3)  # SW_MAXIMIZE

    def launch_browser(self, browser: BrowserType) -> None:
        """
        Launch and position browser on the primary monitor.

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

        if len(self.desktop.frames) > 1:
            self._place_window(h_browser, self.desktop.primary)

        logger.info(f"{browser.value} positioned successfully")

    def position_vortex(self) -> None:
        """Position Vortex window on the first secondary monitor."""
        vortex_handle: int = user32.FindWindowW(None, "Vortex")

        if vortex_handle == 0:
            logger.warning("Vortex window not found")
            return

        logger.info("Found Vortex window")

        secondaries = self.desktop.secondaries
        if secondaries:
            self._place_window(vortex_handle, secondaries[0])

        logger.info("Vortex positioned successfully")

    def position_window_by_title(self, title_substr: str) -> None:
        """
        Position window matching title substring on the primary monitor.

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

        if len(self.desktop.frames) > 1:
            self._place_window(handle, self.desktop.primary)

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
        Get all available monitors (physical pixels; primary first).

        Returns:
            List of Monitor objects
        """
        # Must precede the EnumDisplayMonitors call: an unaware process gets
        # DPI-virtualized (logical) bounds that disagree with captured pixels.
        ensure_dpi_awareness()

        raw_monitors = win32api.EnumDisplayMonitors(None, None)
        monitors: list[Monitor] = []

        for _, _, rect in raw_monitors:
            x, y, right, bottom = rect
            monitor = Monitor(x=x, y=y, width=right - x, height=bottom - y)
            monitors.append(monitor)

        monitors.sort(key=lambda m: (m.x != 0 or m.y != 0, m.x, m.y))

        logger.info(f"Found {len(monitors)} monitors: {monitors}")
        return monitors
