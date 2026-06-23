"""
Screen capture and coordinate conversion utilities.
"""

from __future__ import annotations

from loguru import logger

import cv2
import mss
import numpy as np
import numpy.typing as npt

from models import Monitor


class ScreenCapture:
    """Handles screen capture and coordinate conversions."""

    def __init__(self, monitors: list[Monitor], force_primary: bool = False) -> None:
        """
        Initialize screen capture.

        Args:
            monitors: List of available monitors
            force_primary: Only use primary monitor
        """
        if not monitors:
            raise ValueError("No monitors provided to ScreenCapture")

        # Preserve OS-reported ordering so index 0 remains the real primary display
        self.monitors: list[Monitor] = list(monitors)
        self.screen: mss.mss = mss.mss()
        self.screen_monitors: list[dict[str, int]] = self.screen.monitors
        self.force_primary = force_primary

        self.v_monitor: dict[str, int] = self._determine_capture_region()
        self.min_x: int = self.v_monitor["left"]
        self.min_y: int = self.v_monitor["top"]
        self.virtual_width: int = self.v_monitor["width"]
        self.virtual_height: int = self.v_monitor["height"]

        logger.info(
            f"Initialized screen capture with {len(self.monitors)} app monitors "
            f"and capturing region {self.v_monitor}"
        )

    def _determine_capture_region(self) -> dict[str, int]:
        """Select the monitor region to capture."""
        if self.force_primary or len(self.screen_monitors) <= 1:
            return self._get_primary_monitor_bounds()

        # screen.monitors[0] is already the full virtual desktop
        full_virtual: dict[str, int] = self.screen_monitors[0]
        return {
            "top": full_virtual["top"],
            "left": full_virtual["left"],
            "width": full_virtual["width"],
            "height": full_virtual["height"],
        }

    def _get_primary_monitor_bounds(self) -> dict[str, int]:
        """Resolve primary monitor bounds using MSS data for DPI-safe capture."""
        primary_monitor: Monitor = self.monitors[0]
        matched_monitor = self._match_monitor_to_mss(primary_monitor)

        if matched_monitor:
            return matched_monitor

        logger.warning(
            "Falling back to app monitor geometry for primary capture (MSS match failed)"
        )
        return {
            "top": primary_monitor.y,
            "left": primary_monitor.x,
            "width": primary_monitor.width,
            "height": primary_monitor.height,
        }

    def _match_monitor_to_mss(self, target: Monitor | None) -> dict[str, int] | None:
        """
        Match an app-level Monitor definition to the closest MSS monitor entry.

        This keeps capture dimensions aligned with what MSS expects even when DPI
        scaling causes win32-reported bounds to differ from raw framebuffer pixels.
        """
        if not self.screen_monitors:
            return None

        # MSS returns index 0 as the virtual desktop and remainder per monitor
        physical_monitors = (
            self.screen_monitors[1:]
            if len(self.screen_monitors) > 1
            else self.screen_monitors
        )

        if not physical_monitors:
            return None

        if target is None:
            match = physical_monitors[0]
        else:
            match = min(
                physical_monitors,
                key=lambda m: (
                    abs(m["left"] - target.x)
                    + abs(m["top"] - target.y)
                    + abs(m["width"] - target.width)
                    + abs(m["height"] - target.height)
                ),
            )

        return {
            "top": match["top"],
            "left": match["left"],
            "width": match["width"],
            "height": match["height"],
        }

    def capture(self) -> npt.NDArray[np.uint8]:
        """
        Capture current screen.

        Returns:
            RGB image array
        """
        img: npt.NDArray[np.uint8] = np.array(self.screen.grab(self.v_monitor))
        logger.debug("Screen captured")
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    def img_coords_to_monitor_coords(self, x: int, y: int) -> tuple[int, int]:
        """
        Convert image coordinates to monitor coordinates.

        Args:
            x: Image X coordinate
            y: Image Y coordinate

        Returns:
            Monitor X and Y coordinates
        """
        return x + self.min_x, y + self.min_y

    def monitor_coords_to_img_coords(self, x: int, y: int) -> tuple[int, int]:
        """
        Convert monitor coordinates to image coordinates.

        Args:
            x: Monitor X coordinate
            y: Monitor Y coordinate

        Returns:
            Image X and Y coordinates
        """
        return x - self.min_x, y - self.min_y

    @property
    def capture_width(self) -> int:
        """Get capture area width."""
        return self.v_monitor["width"]

    @property
    def capture_height(self) -> int:
        """Get capture area height."""
        return self.v_monitor["height"]
