"""Monitor bounds must be read in the same (physical) pixel space as capture.

python.exe starts DPI unaware, so win32 virtualizes geometry on scaled
monitors (1920x1080 at 150 % reports as 1280x720) until something -- in the
past only ``mss`` -- switches the process to per-monitor awareness. Found by the
virtual-monitor rig (.github/workflows/windows-displays.yml).
"""

from __future__ import annotations

import sys

import pytest

import services.window_manager as window_manager
from services.window_manager import WindowManager
from utils import platform


def test_get_all_monitors_sets_awareness_before_enumerating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        window_manager, "ensure_dpi_awareness", lambda: calls.append("aware")
    )

    def fake_enum(*_args: object) -> list[tuple[int, int, tuple[int, int, int, int]]]:
        calls.append("enumerate")
        return [(0, 0, (0, 0, 1920, 1080)), (0, 0, (1920, 0, 3840, 1080))]

    monkeypatch.setattr(window_manager.win32api, "EnumDisplayMonitors", fake_enum)

    monitors = WindowManager.get_all_monitors()

    assert calls == ["aware", "enumerate"]
    assert [(m.x, m.y, m.width, m.height) for m in monitors] == [
        (0, 0, 1920, 1080),
        (1920, 0, 1920, 1080),
    ]


def test_ensure_dpi_awareness_is_safe_to_repeat() -> None:
    platform.ensure_dpi_awareness()
    platform.ensure_dpi_awareness()


@pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows DPI APIs")
def test_process_becomes_per_monitor_aware() -> None:
    import ctypes

    platform.ensure_dpi_awareness()

    user32 = ctypes.windll.user32
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    awareness = user32.GetAwarenessFromDpiAwarenessContext(
        user32.GetThreadDpiAwarenessContext()
    )
    assert awareness == 2  # DPI_AWARENESS_PER_MONITOR_AWARE
