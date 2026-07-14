"""
Process DPI awareness setup.

mss captures the screen in physical framebuffer pixels. A DPI-unaware process
gets *virtualized* (logical) coordinates from every win32 API instead --
GetWindowRect, EnumDisplayMonitors, SetCursorPos all disagree with the captured
pixels by the display scale factor, so clicks land in the wrong place on any
display scaled != 100%. Declaring per-monitor DPI awareness makes win32 report
and accept physical virtual-desktop pixels, the same space mss captures.

Awareness is process-global and can only be set once, before the first user32
call, so ``ensure_dpi_awareness`` must run at startup and is idempotent.
"""

from __future__ import annotations

import sys
from enum import Enum
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# shcore.SetProcessDpiAwareness returns this when awareness was already set
# (by an earlier call or an application manifest) -- that is success for us.
_E_ACCESSDENIED = -2147024891  # 0x80070005 as signed HRESULT

# user32.SetProcessDpiAwarenessContext argument (Windows 10 1703+).
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

# shcore.SetProcessDpiAwareness argument (Windows 8.1+).
_PROCESS_PER_MONITOR_DPI_AWARE = 2


class DpiAwareness(str, Enum):
    """Achieved process DPI awareness level."""

    PER_MONITOR_V2 = "per_monitor_v2"
    PER_MONITOR = "per_monitor"
    SYSTEM = "system"
    UNAWARE = "unaware"
    NOT_WINDOWS = "not_windows"


_result: Optional[DpiAwareness] = None


def ensure_dpi_awareness() -> DpiAwareness:
    """Declare per-monitor DPI awareness; idempotent, safe off-Windows.

    Tries, in order: per-monitor-v2 (Win10 1703+), per-monitor (Win8.1+),
    system-aware (Vista+). Returns the achieved level so callers can warn
    when coordinates may be scaled.
    """
    global _result
    if _result is not None:
        return _result

    if sys.platform != "win32":
        _result = DpiAwareness.NOT_WINDOWS
        return _result

    import ctypes

    _result = _set_windows_awareness(ctypes)
    if _result is DpiAwareness.UNAWARE:
        logger.error(
            "Could not set DPI awareness; clicks will be misplaced on any "
            "display scaled != 100%"
        )
    else:
        logger.info(f"Process DPI awareness: {_result.value}")
    return _result


def _set_windows_awareness(ctypes) -> DpiAwareness:
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
            _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return DpiAwareness.PER_MONITOR_V2
    except (AttributeError, OSError):
        pass

    try:
        hresult = ctypes.windll.shcore.SetProcessDpiAwareness(
            _PROCESS_PER_MONITOR_DPI_AWARE
        )
        if hresult == 0 or hresult == _E_ACCESSDENIED:
            return DpiAwareness.PER_MONITOR
    except (AttributeError, OSError):
        pass

    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return DpiAwareness.SYSTEM
    except (AttributeError, OSError):
        pass

    return DpiAwareness.UNAWARE
