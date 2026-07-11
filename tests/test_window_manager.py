"""WindowManager tests against the deterministic win32 mocks."""

from __future__ import annotations

import pytest

from models import Monitor
from services import window_manager as wm_module
from services.geometry import VirtualDesktop
from services.window_manager import WindowManager
from tests.test_geometry import LAYOUTS
from utils.platform import IS_WINDOWS
from utils import mock_win32

pytestmark = pytest.mark.skipif(IS_WINDOWS, reason="asserts on the mock win32 backend")


@pytest.fixture(autouse=True)
def _windows_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wm_module, "IS_WINDOWS", True)
    mock_win32.reset()
    yield
    mock_win32.reset()


def _manager(layout: str) -> WindowManager:
    return WindowManager(VirtualDesktop.from_mss_monitors(LAYOUTS[layout]))


def test_position_vortex_moves_to_first_secondary() -> None:
    manager = _manager("left_of_primary")
    manager.position_vortex()
    # Secondary is the monitor left of primary; window covers it fully.
    assert mock_win32.state.windows["Vortex"] == (-1920, 0, 0, 1080)


def test_position_vortex_single_monitor_leaves_window_alone() -> None:
    before = dict(mock_win32.state.windows)
    manager = _manager("single")
    manager.position_vortex()
    assert mock_win32.state.windows == before


def test_position_vortex_missing_window_is_noop() -> None:
    mock_win32.configure(windows={})
    manager = _manager("left_of_primary")
    manager.position_vortex()  # must not raise
    assert mock_win32.state.windows == {}


def test_position_window_by_title_moves_to_primary() -> None:
    manager = _manager("left_of_primary")
    manager.position_window_by_title("chro")  # case-insensitive substring
    assert mock_win32.state.windows["Chrome"] == (0, 0, 1920, 1080)


def test_position_window_by_title_raises_without_match() -> None:
    manager = _manager("left_of_primary")
    with pytest.raises(RuntimeError, match="No visible window"):
        manager.position_window_by_title("does-not-exist")


def test_get_vortex_bbox_reflects_mock_state() -> None:
    mock_win32.configure(windows={"Vortex": (-1800, 50, -200, 900)})
    manager = _manager("left_of_primary")
    bbox = manager.get_vortex_bbox()
    assert bbox is not None
    assert (bbox.x1, bbox.y1, bbox.x2, bbox.y2) == (-1800, 50, -200, 900)


def test_get_all_monitors_sorts_primary_first() -> None:
    # win32 enumerates the secondary FIRST; primary must still lead.
    mock_win32.configure(monitors=[(-1920, 0, 0, 1080), (0, 0, 1920, 1080)])
    monitors = WindowManager.get_all_monitors()
    assert monitors[0] == Monitor(x=0, y=0, width=1920, height=1080)
    assert monitors[1] == Monitor(x=-1920, y=0, width=1920, height=1080)


def test_raises_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wm_module, "IS_WINDOWS", False)
    with pytest.raises(RuntimeError, match="only available on Windows"):
        WindowManager(VirtualDesktop.from_mss_monitors(LAYOUTS["single"]))
