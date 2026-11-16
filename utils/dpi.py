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

Windows can refuse the request without an error worth acting on: awareness is
fixed once a manifest, an earlier call, or a compatibility override on
python.exe ("Override high DPI scaling behavior") has set it. The level that
actually applies is therefore read back from the OS, never inferred from the
setters' return values.
"""

from __future__ import annotations

import sys
from enum import Enum

from utils.logger import get_logger

logger = get_logger(__name__)

# user32.SetProcessDpiAwarenessContext argument (Windows 10 1703+).
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

# shcore.SetProcessDpiAwareness argument (Windows 8.1+).
_PROCESS_PER_MONITOR_DPI_AWARE = 2

# DPI_AWARENESS / PROCESS_DPI_AWARENESS values reported by the OS.
_OS_UNAWARE = 0
_OS_SYSTEM_AWARE = 1
_OS_PER_MONITOR_AWARE = 2


class CoordinatesScaledError(RuntimeError):
    """Windows reports monitor bounds scaled to this process.

    Raised at startup instead of running: with scaled coordinates every click
    would be off by the monitor's scale factor.
    """

    def __init__(self, monitors: list[str]) -> None:
        self.monitors = monitors
        details = "\n".join(f"  {monitor}" for monitor in monitors)
        super().__init__(
            "Windows is scaling this program's coordinates, so clicks would land "
            "in the wrong place:\n"
            f"{details}\n"
            "A high-DPI compatibility setting on python.exe does this. Remove it "
            "(python.exe > Properties > Compatibility > Change high DPI settings, "
            "untick the override) and start again."
        )


class DpiAwareness(str, Enum):
    """Achieved process DPI awareness level."""

    PER_MONITOR_V2 = "per_monitor_v2"
    PER_MONITOR = "per_monitor"
    SYSTEM = "system"
    UNAWARE = "unaware"
    NOT_WINDOWS = "not_windows"


_result: DpiAwareness | None = None


def ensure_dpi_awareness() -> DpiAwareness:
    """Declare per-monitor DPI awareness; idempotent, safe off-Windows.

    Requests, in order: per-monitor-v2 (Win10 1703+), per-monitor (Win8.1+),
    system-aware (Vista+). Returns the level Windows actually applies, so
    callers can tell when coordinates are still scaled.
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
            "Windows refused DPI awareness for this process; clicks will be "
            "misplaced on any display scaled != 100%"
        )
    elif _result is DpiAwareness.SYSTEM:
        logger.warning(
            "Process is only system DPI aware; clicks will be misplaced on "
            "monitors scaled differently from the primary"
        )
    else:
        logger.info(f"Process DPI awareness: {_result.value}")
    return _result


def _set_windows_awareness(ctypes) -> DpiAwareness:
    """Request per-monitor awareness, then report what Windows applied."""
    _request_per_monitor_awareness(ctypes)
    return _read_awareness(ctypes)


def _request_per_monitor_awareness(ctypes) -> None:
    windll = ctypes.windll
    try:
        set_context = windll.user32.SetProcessDpiAwarenessContext
        # DPI_AWARENESS_CONTEXT is a pointer-sized handle: a bare Python int is
        # passed as a 32-bit C int, which can fail on 64-bit Python.
        set_context.argtypes = [ctypes.c_void_p]
        if set_context(ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            return
    except (AttributeError, OSError):
        pass

    try:
        if windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return
    except (AttributeError, OSError):
        pass

    try:
        windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _read_awareness(ctypes) -> DpiAwareness:
    """The DPI awareness Windows applies to this thread right now."""
    user32 = ctypes.windll.user32
    try:
        get_context = user32.GetThreadDpiAwarenessContext
        get_context.restype = ctypes.c_void_p
        context = ctypes.c_void_p(get_context())

        contexts_equal = user32.AreDpiAwarenessContextsEqual
        contexts_equal.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if contexts_equal(
            context, ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        ):
            return DpiAwareness.PER_MONITOR_V2

        get_level = user32.GetAwarenessFromDpiAwarenessContext
        get_level.argtypes = [ctypes.c_void_p]
        level = get_level(context)
    except (AttributeError, OSError):
        # Before Windows 10 1607 only the process-wide query exists.
        level = _read_process_awareness(ctypes)

    if level == _OS_PER_MONITOR_AWARE:
        return DpiAwareness.PER_MONITOR
    if level == _OS_SYSTEM_AWARE:
        return DpiAwareness.SYSTEM
    return DpiAwareness.UNAWARE


def _read_process_awareness(ctypes) -> int:
    try:
        value = ctypes.c_int(_OS_UNAWARE)
        hresult = ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(value))
        if hresult == 0:
            return int(value.value)
    except (AttributeError, OSError):
        pass
    return _OS_UNAWARE
