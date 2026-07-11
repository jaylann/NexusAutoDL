"""
Pure monitor-layout geometry: virtual-desktop coordinate mapping.

Everything here operates in physical virtual-desktop pixels (the space mss
captures and, once the process is DPI-aware, the space win32 reports). No
win32 imports -- fully unit-testable on any platform.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from models import BoundingBox, Monitor
from utils.logger import get_logger

logger = get_logger(__name__)

# Fractional inset applied to a window rect before searching it, trimming
# window chrome (title bar, borders) that could distract the detector.
VORTEX_BBOX_INSET = 0.04

# How far (px) outside every monitor a click target may lie and still be
# snapped onto the nearest monitor edge; farther targets are refused.
CLICK_SNAP_MAX_PX = 32
CLICK_SNAP_INSET_PX = 2


class MonitorFrame(BaseModel):
    """One physical monitor's region of the virtual desktop."""

    index: int
    left: int
    top: int
    width: int
    height: int

    class Config:
        frozen = True

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def is_primary(self) -> bool:
        """On Windows the primary monitor always spans from (0, 0)."""
        return self.left == 0 and self.top == 0

    def to_virtual(self, x: int, y: int) -> tuple[int, int]:
        """Frame-image coordinates -> virtual-desktop coordinates."""
        return x + self.left, y + self.top

    def from_virtual(self, x: int, y: int) -> tuple[int, int]:
        """Virtual-desktop coordinates -> frame-image coordinates."""
        return x - self.left, y - self.top

    def contains(self, x: int, y: int) -> bool:
        """True if the virtual-desktop point lies on this monitor."""
        return self.left <= x < self.right and self.top <= y < self.bottom

    def distance_to(self, x: int, y: int) -> int:
        """Chebyshev distance from a virtual-desktop point to this frame."""
        dx = max(self.left - x, 0, x - (self.right - 1))
        dy = max(self.top - y, 0, y - (self.bottom - 1))
        return max(dx, dy)


class VirtualDesktop(BaseModel):
    """All physical monitors composing the virtual desktop."""

    frames: list[MonitorFrame]

    class Config:
        frozen = True

    @classmethod
    def from_mss_monitors(cls, mss_monitors: list[dict[str, int]]) -> "VirtualDesktop":
        """Build from ``mss.monitors`` (index 0 = union, 1.. = physical)."""
        if not mss_monitors:
            raise ValueError("No monitors reported by mss")
        physical = mss_monitors[1:] if len(mss_monitors) > 1 else mss_monitors
        frames = [
            MonitorFrame(
                index=i,
                left=m["left"],
                top=m["top"],
                width=m["width"],
                height=m["height"],
            )
            for i, m in enumerate(physical)
        ]
        return cls(frames=frames)

    @property
    def primary(self) -> MonitorFrame:
        """The monitor at the virtual-desktop origin."""
        for frame in self.frames:
            if frame.is_primary:
                return frame
        logger.warning("No monitor spans (0, 0); using the first one as primary")
        return self.frames[0]

    @property
    def secondaries(self) -> list[MonitorFrame]:
        """Non-primary monitors, ordered left-to-right then top-to-bottom."""
        primary = self.primary
        return sorted(
            (f for f in self.frames if f != primary),
            key=lambda f: (f.left, f.top),
        )

    def frame_containing(self, x: int, y: int) -> Optional[MonitorFrame]:
        """The monitor containing a virtual-desktop point, if any."""
        for frame in self.frames:
            if frame.contains(x, y):
                return frame
        return None

    def clamp_to_nearest(
        self,
        x: int,
        y: int,
        inset: int = CLICK_SNAP_INSET_PX,
        max_snap: int = CLICK_SNAP_MAX_PX,
    ) -> Optional[tuple[int, int]]:
        """Snap a point onto the nearest monitor, or None if too far off.

        Points already on a monitor pass through unchanged; points within
        ``max_snap`` px of one are clamped just inside its edge (``inset``);
        anything farther (e.g. in a dead zone of a non-rectangular layout)
        is refused so we never click a wildly wrong location.
        """
        if self.frame_containing(x, y):
            return x, y

        nearest = min(self.frames, key=lambda f: f.distance_to(x, y))
        if nearest.distance_to(x, y) > max_snap:
            return None

        cx = min(max(x, nearest.left + inset), nearest.right - 1 - inset)
        cy = min(max(y, nearest.top + inset), nearest.bottom - 1 - inset)
        return cx, cy

    def matches_win32(self, monitors: list[Monitor], tolerance: int = 2) -> bool:
        """Check win32-reported monitor rects agree with the mss layout.

        A mismatch means the process is not truly DPI-aware (Windows is
        virtualizing win32 coordinates) and clicks would be scaled wrong;
        callers should log loudly.
        """
        if len(monitors) != len(self.frames):
            logger.error(
                f"win32 reports {len(monitors)} monitors but mss reports "
                f"{len(self.frames)}"
            )
            return False

        unmatched = list(self.frames)
        for monitor in monitors:
            match = next(
                (
                    f
                    for f in unmatched
                    if abs(f.left - monitor.x) <= tolerance
                    and abs(f.top - monitor.y) <= tolerance
                    and abs(f.width - monitor.width) <= tolerance
                    and abs(f.height - monitor.height) <= tolerance
                ),
                None,
            )
            if match is None:
                logger.error(
                    f"win32 monitor {monitor} has no matching mss monitor -- "
                    "DPI awareness likely failed; clicks may be misplaced"
                )
                return False
            unmatched.remove(match)
        return True


def compute_search_bbox(
    window_rect: BoundingBox,
    frame: MonitorFrame,
    inset: float = VORTEX_BBOX_INSET,
) -> Optional[BoundingBox]:
    """Convert a virtual-desktop window rect into a frame-image search bbox.

    Clips to the frame first (maximized windows report rects that overhang
    the monitor by their invisible resize borders), then applies a small
    fractional inset to trim window chrome. Returns None when the window
    does not meaningfully intersect this frame.
    """
    x1, y1 = frame.from_virtual(window_rect.x1, window_rect.y1)
    x2, y2 = frame.from_virtual(window_rect.x2, window_rect.y2)

    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(frame.width, x2), min(frame.height, y2)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None

    clipped = BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2)
    padded = clipped.pad(inset)
    return padded if padded.width > 0 and padded.height > 0 else clipped
