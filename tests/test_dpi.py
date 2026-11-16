"""DPI awareness setup tests.

Monitor bounds must be read in the same (physical) pixel space as capture.
python.exe starts DPI unaware, so win32 virtualizes geometry on scaled
monitors (1920x1080 at 150 % reports as 1280x720) until the process becomes
per-monitor aware.
"""

from __future__ import annotations

import ctypes
import types

import pytest

from services import window_manager
from services.window_manager import WindowManager
from utils import dpi
from utils.dpi import DpiAwareness, ensure_dpi_awareness
from utils.platform import IS_WINDOWS

_V2_HANDLE = ctypes.c_void_p(-4).value  # pointer-sized, as Windows compares it
_SYSTEM_HANDLE = ctypes.c_void_p(-2).value
_UNAWARE_HANDLE = ctypes.c_void_p(-1).value
_V1_HANDLE = ctypes.c_void_p(-3).value
_LEVEL_BY_HANDLE = {_UNAWARE_HANDLE: 0, _SYSTEM_HANDLE: 1, _V1_HANDLE: 2, _V2_HANDLE: 2}


class _FakeUser32:
    """user32 DPI entry points backed by one settable awareness handle.

    ``locked`` models awareness that Windows already fixed (manifest, earlier
    call, or a compatibility override on python.exe): setters then fail.
    """

    def __init__(self, handle: int, locked: bool = False) -> None:
        self.handle = handle
        self.locked = locked
        self.set_context_args: list[object] = []

        def set_context(context: object) -> int:
            self.set_context_args.append(context)
            if self.locked:
                return 0
            self.handle = context.value  # type: ignore[attr-defined]
            return 1

        def get_context() -> int:
            return self.handle

        def contexts_equal(a: object, b: object) -> int:
            return int(a.value == b.value)  # type: ignore[attr-defined]

        def get_level(context: object) -> int:
            return _LEVEL_BY_HANDLE[context.value]  # type: ignore[attr-defined]

        def set_system_aware() -> int:
            if self.locked:
                return 0
            self.handle = _SYSTEM_HANDLE
            return 1

        self.SetProcessDpiAwarenessContext = set_context
        self.GetThreadDpiAwarenessContext = get_context
        self.AreDpiAwarenessContextsEqual = contexts_equal
        self.GetAwarenessFromDpiAwarenessContext = get_level
        self.SetProcessDPIAware = set_system_aware


class _FakeShcore:
    def __init__(self, user32: _FakeUser32) -> None:
        self._user32 = user32

    def SetProcessDpiAwareness(self, value: int) -> int:
        if self._user32.locked:
            return -2147024891  # E_ACCESSDENIED
        self._user32.handle = _V1_HANDLE
        return 0

    def GetProcessDpiAwareness(self, _process: object, out: object) -> int:
        out._obj.value = _LEVEL_BY_HANDLE[self._user32.handle]  # type: ignore[attr-defined]
        return 0


def _fake_ctypes(user32: object, shcore: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        windll=types.SimpleNamespace(user32=user32, shcore=shcore),
        c_void_p=ctypes.c_void_p,
        c_int=ctypes.c_int,
        byref=ctypes.byref,
    )


def test_idempotent() -> None:
    assert ensure_dpi_awareness() == ensure_dpi_awareness()


@pytest.mark.skipif(IS_WINDOWS, reason="Windows-only assertion below")
def test_non_windows_short_circuits() -> None:
    assert ensure_dpi_awareness() is DpiAwareness.NOT_WINDOWS


def test_requests_per_monitor_v2_with_a_pointer_sized_handle() -> None:
    user32 = _FakeUser32(_UNAWARE_HANDLE)

    level = dpi._set_windows_awareness(_fake_ctypes(user32, _FakeShcore(user32)))

    assert level is DpiAwareness.PER_MONITOR_V2
    # A bare int would be marshalled as a 32-bit C int on 64-bit Python.
    (context,) = user32.set_context_args
    assert isinstance(context, ctypes.c_void_p)
    assert context.value == _V2_HANDLE


@pytest.mark.parametrize(
    ("forced_handle", "expected"),
    [
        (_UNAWARE_HANDLE, DpiAwareness.UNAWARE),
        (_SYSTEM_HANDLE, DpiAwareness.SYSTEM),
        (_V1_HANDLE, DpiAwareness.PER_MONITOR),
    ],
)
def test_reports_the_level_windows_applies_when_awareness_is_already_fixed(
    forced_handle: int, expected: DpiAwareness
) -> None:
    # "Access denied" from the setters only says awareness was fixed earlier,
    # e.g. by the python.exe high-DPI compatibility override. It must not be
    # read as success: the forced level decides.
    user32 = _FakeUser32(forced_handle, locked=True)

    level = dpi._set_windows_awareness(_fake_ctypes(user32, _FakeShcore(user32)))

    assert level is expected


def test_falls_back_to_process_wide_apis_on_older_windows() -> None:
    # Before Windows 10 1607 user32 has no awareness-context functions.
    modern = _FakeUser32(_UNAWARE_HANDLE)
    old_user32 = types.SimpleNamespace(SetProcessDPIAware=modern.SetProcessDPIAware)

    level = dpi._set_windows_awareness(_fake_ctypes(old_user32, _FakeShcore(modern)))

    assert level is DpiAwareness.PER_MONITOR


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


@pytest.mark.skipif(not IS_WINDOWS, reason="requires real win32 DPI APIs")
def test_windows_process_becomes_per_monitor_aware() -> None:
    level = ensure_dpi_awareness()

    # Ask the OS directly rather than trusting our own return value.
    user32 = ctypes.windll.user32
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    os_level = user32.GetAwarenessFromDpiAwarenessContext(
        user32.GetThreadDpiAwarenessContext()
    )

    assert os_level == 2  # DPI_AWARENESS_PER_MONITOR_AWARE
    assert level in (DpiAwareness.PER_MONITOR_V2, DpiAwareness.PER_MONITOR)
