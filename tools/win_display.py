"""Windows display helpers shared by the display-rig tools (Windows-only).

Used by ``tools/ci/*``, ``tools/generate_scenarios.py`` and
``tools/e2e_click_check.py`` to inspect and change the monitor topology the app
runs against: DPI awareness, the interactive-session check, the GDI display list,
and per-monitor scaling.

Per-monitor scaling has no documented Win32 setter. Windows' Settings app uses
``DisplayConfigSetDeviceInfo`` with the private request types -3 (get) and -4
(set), which take a scale *relative to the monitor's recommended scale*, as an
index step into ``DPI_VALUES``. That is the same mechanism SetDPI
(github.com/imniko/SetDPI) and the Virtual Display Driver's ``scale-VDD.ps1``
(via the ``DisplayConfig`` PowerShell module) rely on. Doing it here lets the
rig match sources to GDI device names (``\\\\.\\DISPLAYn``) and positions, which
the PowerShell module does not expose.

Nothing in this module runs at import time on non-Windows hosts, so the module
stays importable everywhere (lint, type checks); calling a helper there raises.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from tools.ci.topology import SCALE_STEPS

# Scale steps Windows offers, in order. The relative index from the private
# DisplayConfig DPI requests walks this list.
DPI_VALUES: tuple[int, ...] = SCALE_STEPS

_PROCESS_PER_MONITOR_DPI_AWARE = 2
_MDT_EFFECTIVE_DPI = 0
_MONITOR_DEFAULTTONEAREST = 2
_DESKTOP_READOBJECTS = 0x0001
_UOI_NAME = 2
_QDC_ONLY_ACTIVE_PATHS = 0x00000002
_DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE = 1
_DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1
_DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALE = -3
_DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALE = -4
_SDC_APPLY = 0x00000080
_SDC_TOPOLOGY_EXTEND = 0x00000004
_DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
_DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("tools.win_display only works on Windows")


# --- ctypes structures (wingdi.h) ---------------------------------------------


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", ctypes.c_uint32), ("Denominator", ctypes.c_uint32)]


class _REGION(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_uint32), ("cy", ctypes.c_uint32)]


class _POINTL(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _RECTL(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", _LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx", ctypes.c_uint32),
        ("statusFlags", ctypes.c_uint32),
    ]


class _PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", _LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx", ctypes.c_uint32),
        ("outputTechnology", ctypes.c_uint32),
        ("rotation", ctypes.c_uint32),
        ("scaling", ctypes.c_uint32),
        ("refreshRate", _RATIONAL),
        ("scanLineOrdering", ctypes.c_uint32),
        ("targetAvailable", wintypes.BOOL),
        ("statusFlags", ctypes.c_uint32),
    ]


class _PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", _PATH_SOURCE_INFO),
        ("targetInfo", _PATH_TARGET_INFO),
        ("flags", ctypes.c_uint32),
    ]


class _VIDEO_SIGNAL_INFO(ctypes.Structure):
    _fields_ = [
        ("pixelRate", ctypes.c_uint64),
        ("hSyncFreq", _RATIONAL),
        ("vSyncFreq", _RATIONAL),
        ("activeSize", _REGION),
        ("totalSize", _REGION),
        ("videoStandard", ctypes.c_uint32),
        ("scanLineOrdering", ctypes.c_uint32),
    ]


class _SOURCE_MODE(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pixelFormat", ctypes.c_uint32),
        ("position", _POINTL),
    ]


class _DESKTOP_IMAGE_INFO(ctypes.Structure):
    _fields_ = [
        ("PathSourceSize", _POINTL),
        ("DesktopImageRegion", _RECTL),
        ("DesktopImageClip", _RECTL),
    ]


class _MODE_UNION(ctypes.Union):
    _fields_ = [
        ("targetMode", _VIDEO_SIGNAL_INFO),
        ("sourceMode", _SOURCE_MODE),
        ("desktopImageInfo", _DESKTOP_IMAGE_INFO),
    ]


class _MODE_INFO(ctypes.Structure):
    _fields_ = [
        ("infoType", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
        ("adapterId", _LUID),
        ("u", _MODE_UNION),
    ]


class _DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int32),
        ("size", ctypes.c_uint32),
        ("adapterId", _LUID),
        ("id", ctypes.c_uint32),
    ]


class _SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", _DEVICE_INFO_HEADER),
        ("viewGdiDeviceName", ctypes.c_wchar * 32),
    ]


class _SOURCE_DPI_SCALE_GET(ctypes.Structure):
    _fields_ = [
        ("header", _DEVICE_INFO_HEADER),
        ("minScaleRel", ctypes.c_int32),
        ("curScaleRel", ctypes.c_int32),
        ("maxScaleRel", ctypes.c_int32),
    ]


class _SOURCE_DPI_SCALE_SET(ctypes.Structure):
    _fields_ = [("header", _DEVICE_INFO_HEADER), ("scaleRel", ctypes.c_int32)]


# --- process / session ----------------------------------------------------------


def set_per_monitor_dpi_awareness() -> int:
    """Make this process per-monitor DPI aware (what ``mss`` does on init).

    Returns the HRESULT; ``E_ACCESSDENIED`` (0x80070005) means awareness was
    already fixed earlier (by a manifest or an earlier call) and is harmless.
    """
    _require_windows()
    return int(
        ctypes.windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE)
    )


def process_dpi_awareness() -> int:
    """0 = unaware, 1 = system aware, 2 = per-monitor aware (calling thread)."""
    _require_windows()
    user32 = ctypes.windll.user32
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    context = user32.GetThreadDpiAwarenessContext()
    return int(user32.GetAwarenessFromDpiAwarenessContext(context))


def dpi_scale_at(x: int, y: int) -> float:
    """Effective scale factor (1.0 = 100 %) of the monitor nearest ``(x, y)``.

    Coordinates are interpreted in this process's DPI awareness, so call
    ``set_per_monitor_dpi_awareness`` first to pass physical pixels.
    """
    _require_windows()
    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    hmon = user32.MonitorFromPoint(wintypes.POINT(x, y), _MONITOR_DEFAULTTONEAREST)
    dpi_x = ctypes.c_uint()
    dpi_y = ctypes.c_uint()
    hr = ctypes.windll.shcore.GetDpiForMonitor(
        ctypes.c_void_p(hmon),
        _MDT_EFFECTIVE_DPI,
        ctypes.byref(dpi_x),
        ctypes.byref(dpi_y),
    )
    if hr != 0:
        raise OSError(f"GetDpiForMonitor failed: 0x{hr & 0xFFFFFFFF:08x}")
    return dpi_x.value / 96.0


def session_info() -> dict[str, Any]:
    """Session id + input desktop name.

    ``input_desktop`` is ``"Default"`` for a normal interactive desktop and
    ``None`` when this process cannot reach one (session 0 / service context),
    in which case screen capture and synthetic clicks cannot work.
    """
    _require_windows()
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    session_id = wintypes.DWORD()
    kernel32.ProcessIdToSessionId(
        kernel32.GetCurrentProcessId(), ctypes.byref(session_id)
    )

    user32.OpenInputDesktop.restype = ctypes.c_void_p
    hdesk = user32.OpenInputDesktop(0, False, _DESKTOP_READOBJECTS)
    desktop: str | None = None
    if hdesk:
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if user32.GetUserObjectInformationW(
            ctypes.c_void_p(hdesk),
            _UOI_NAME,
            buf,
            ctypes.sizeof(buf),
            ctypes.byref(needed),
        ):
            desktop = buf.value
        user32.CloseDesktop(ctypes.c_void_p(hdesk))
    return {"session_id": int(session_id.value), "input_desktop": desktop}


# --- GDI display devices (pywin32) ---------------------------------------------------


def display_devices() -> list[dict[str, Any]]:
    """All GDI display devices with their adapter and first monitor strings."""
    _require_windows()
    import win32api

    devices: list[dict[str, Any]] = []
    index = 0
    while True:
        try:
            dev = win32api.EnumDisplayDevices(None, index, 0)
        except win32api.error:  # raised once the index runs past the end
            break
        index += 1
        monitor_string = monitor_id = ""
        # An adapter without a monitor attached raises; its strings stay empty.
        with contextlib.suppress(win32api.error):
            mon = win32api.EnumDisplayDevices(dev.DeviceName, 0, 0)
            monitor_string, monitor_id = mon.DeviceString, mon.DeviceID
        devices.append(
            {
                "name": dev.DeviceName,
                "adapter": dev.DeviceString,
                "adapter_id": dev.DeviceID,
                "attached": bool(dev.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP),
                "primary": bool(dev.StateFlags & _DISPLAY_DEVICE_PRIMARY_DEVICE),
                "monitor": monitor_string,
                "monitor_id": monitor_id,
            }
        )
    return devices


def extend_all_displays() -> int:
    """Switch to "Extend these displays" so every connected display is used.

    Returns the ``SetDisplayConfig`` result (0 = success).
    """
    _require_windows()
    return int(
        ctypes.windll.user32.SetDisplayConfig(
            0, None, 0, None, _SDC_APPLY | _SDC_TOPOLOGY_EXTEND
        )
    )


# --- DisplayConfig sources + per-monitor scale ---------------------------------------


@dataclass(frozen=True)
class DisplaySource:
    """One active DisplayConfig source (= one desktop monitor)."""

    gdi_name: str
    adapter_low: int
    adapter_high: int
    source_id: int
    x: int
    y: int
    width: int
    height: int

    def _header(self, request: int, size: int) -> _DEVICE_INFO_HEADER:
        header = _DEVICE_INFO_HEADER()
        header.type = request
        header.size = size
        header.adapterId.LowPart = self.adapter_low
        header.adapterId.HighPart = self.adapter_high
        header.id = self.source_id
        return header


def display_sources() -> list[DisplaySource]:
    """Active display sources with GDI name, desktop position and mode size."""
    _require_windows()
    user32 = ctypes.windll.user32
    num_paths = ctypes.c_uint32()
    num_modes = ctypes.c_uint32()
    rc = user32.GetDisplayConfigBufferSizes(
        _QDC_ONLY_ACTIVE_PATHS, ctypes.byref(num_paths), ctypes.byref(num_modes)
    )
    if rc != 0:
        raise OSError(f"GetDisplayConfigBufferSizes failed: {rc}")
    paths = (_PATH_INFO * num_paths.value)()
    modes = (_MODE_INFO * num_modes.value)()
    rc = user32.QueryDisplayConfig(
        _QDC_ONLY_ACTIVE_PATHS,
        ctypes.byref(num_paths),
        paths,
        ctypes.byref(num_modes),
        modes,
        None,
    )
    if rc != 0:
        raise OSError(f"QueryDisplayConfig failed: {rc}")

    sources: list[DisplaySource] = []
    seen: set[tuple[int, int, int]] = set()
    for path in paths[: num_paths.value]:
        src = path.sourceInfo
        key = (src.adapterId.LowPart, src.adapterId.HighPart, src.id)
        if key in seen:  # cloned paths share one source
            continue
        seen.add(key)

        name = _SOURCE_DEVICE_NAME()
        name.header.type = _DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME
        name.header.size = ctypes.sizeof(_SOURCE_DEVICE_NAME)
        name.header.adapterId = src.adapterId
        name.header.id = src.id
        if user32.DisplayConfigGetDeviceInfo(ctypes.byref(name)) != 0:
            continue

        x = y = width = height = 0
        if src.modeInfoIdx < num_modes.value:
            mode = modes[src.modeInfoIdx]
            if mode.infoType == _DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE:
                x = mode.u.sourceMode.position.x
                y = mode.u.sourceMode.position.y
                width = mode.u.sourceMode.width
                height = mode.u.sourceMode.height

        sources.append(
            DisplaySource(
                gdi_name=name.viewGdiDeviceName,
                adapter_low=src.adapterId.LowPart,
                adapter_high=src.adapterId.HighPart,
                source_id=src.id,
                x=x,
                y=y,
                width=width,
                height=height,
            )
        )
    return sources


def get_scale(source: DisplaySource) -> dict[str, int]:
    """Current / recommended / min / max scale (percent) of a source."""
    _require_windows()
    req = _SOURCE_DPI_SCALE_GET()
    req.header = source._header(
        _DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALE, ctypes.sizeof(_SOURCE_DPI_SCALE_GET)
    )
    rc = ctypes.windll.user32.DisplayConfigGetDeviceInfo(ctypes.byref(req))
    if rc != 0:
        raise OSError(f"DPI scale query failed for {source.gdi_name}: {rc}")

    recommended = -req.minScaleRel  # minScaleRel is <= 0: steps below recommended

    def pct(index: int) -> int:
        return DPI_VALUES[max(0, min(index, len(DPI_VALUES) - 1))]

    return {
        "current": pct(recommended + req.curScaleRel),
        "recommended": pct(recommended),
        "min": pct(0),
        "max": pct(recommended + req.maxScaleRel),
    }


def set_scale(source: DisplaySource, percent: int) -> None:
    """Set a source's scale (percent from ``DPI_VALUES``); applies live."""
    _require_windows()
    if percent not in DPI_VALUES:
        raise ValueError(f"{percent}% is not a Windows scale step {DPI_VALUES}")

    req = _SOURCE_DPI_SCALE_GET()
    req.header = source._header(
        _DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALE, ctypes.sizeof(_SOURCE_DPI_SCALE_GET)
    )
    user32 = ctypes.windll.user32
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(req)) != 0:
        raise OSError(f"DPI scale query failed for {source.gdi_name}")

    recommended = -req.minScaleRel
    rel = DPI_VALUES.index(percent) - recommended
    if not req.minScaleRel <= rel <= req.maxScaleRel:
        top = DPI_VALUES[min(recommended + req.maxScaleRel, len(DPI_VALUES) - 1)]
        raise ValueError(
            f"{percent}% is outside {source.gdi_name}'s range (max {top}%)"
        )

    setter = _SOURCE_DPI_SCALE_SET()
    setter.header = source._header(
        _DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALE, ctypes.sizeof(_SOURCE_DPI_SCALE_SET)
    )
    setter.scaleRel = rel
    rc = user32.DisplayConfigSetDeviceInfo(ctypes.byref(setter))
    if rc != 0:
        raise OSError(f"DPI scale set failed for {source.gdi_name}: {rc}")


def physical_monitors() -> list[dict[str, int]]:
    """MSS per-monitor rects (physical pixels), primary first as MSS orders them.

    Creating the ``mss`` instance makes this process per-monitor DPI aware.
    """
    import mss

    with mss.mss() as sct:
        return [dict(m) for m in sct.monitors[1:]]
