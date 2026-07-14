"""
Per-monitor screen capture.

Each monitor is grabbed as its own frame carrying its ``MonitorFrame``
geometry, so detections convert back to virtual-desktop coordinates with the
correct per-monitor offset -- exact even for negative origins, mixed-DPI and
non-contiguous layouts (where a single virtual-desktop grab would be a huge,
mostly-empty image with one global offset).
"""

from __future__ import annotations

from typing import Callable, NamedTuple

import cv2
import mss
import numpy as np
import numpy.typing as npt

from loguru import logger

from models import Monitor
from services.geometry import MonitorFrame, VirtualDesktop


class CapturedFrame(NamedTuple):
    """One monitor's screenshot plus its virtual-desktop geometry."""

    image: npt.NDArray[np.uint8]  # RGB
    frame: MonitorFrame


class ScreenCapture:
    """Captures each monitor of the virtual desktop as a separate frame."""

    def __init__(
        self,
        monitors: list[Monitor],
        force_primary: bool = False,
        mss_factory: Callable[[], "mss.base.MSSBase"] = mss.mss,
    ) -> None:
        """
        Initialize screen capture.

        Args:
            monitors: win32-reported monitors, used only to cross-check that
                the process sees the same physical layout mss captures
            force_primary: Only capture the primary monitor
            mss_factory: Injection point for a fake mss in tests
        """
        if not monitors:
            raise ValueError("No monitors provided to ScreenCapture")

        self.monitors: list[Monitor] = list(monitors)
        self.screen = mss_factory()
        self.desktop: VirtualDesktop = VirtualDesktop.from_mss_monitors(
            self.screen.monitors
        )
        self.force_primary = force_primary
        self.targets: list[MonitorFrame] = (
            [self.desktop.primary] if force_primary else list(self.desktop.frames)
        )

        # A disagreement means win32 coordinates are DPI-virtualized; capture
        # still works (mss geometry is authoritative) but window rects and
        # clicks based on win32 data would be scaled wrong.
        self.desktop.matches_win32(self.monitors)

        logger.info(
            f"Screen capture initialized: {len(self.desktop.frames)} monitor(s), "
            f"capturing {[f.index for f in self.targets]}"
        )

    def capture_frames(self) -> list[CapturedFrame]:
        """Capture every target monitor as an RGB frame."""
        frames: list[CapturedFrame] = []
        for frame in self.targets:
            region = {
                "left": frame.left,
                "top": frame.top,
                "width": frame.width,
                "height": frame.height,
            }
            raw: npt.NDArray[np.uint8] = np.array(self.screen.grab(region))
            code = cv2.COLOR_BGRA2RGB if raw.shape[2] == 4 else cv2.COLOR_BGR2RGB
            frames.append(CapturedFrame(cv2.cvtColor(raw, code), frame))
        logger.debug(f"Captured {len(frames)} frame(s)")
        return frames
