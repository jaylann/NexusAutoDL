"""ClickController tests against the deterministic win32 mocks."""

from __future__ import annotations

import pytest

from services.click_controller import ClickController
from services.geometry import VirtualDesktop
from tests.test_geometry import LAYOUTS
from utils.platform import IS_WINDOWS
from utils import mock_win32

pytestmark = pytest.mark.skipif(IS_WINDOWS, reason="asserts on the mock win32 backend")

LEFTDOWN = mock_win32.win32con.MOUSEEVENTF_LEFTDOWN
LEFTUP = mock_win32.win32con.MOUSEEVENTF_LEFTUP


@pytest.fixture(autouse=True)
def _reset_mock_state():
    mock_win32.reset()
    yield
    mock_win32.reset()


def _controller(layout: str, **kwargs) -> ClickController:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS[layout])
    return ClickController(desktop, **kwargs)


def _button_events() -> list[tuple]:
    return [e for e in mock_win32.state.events if e[0] == "mouse"]


def test_click_on_negative_origin_monitor() -> None:
    controller = _controller("left_of_primary")
    mock_win32.configure(cursor=(7, 9))

    assert controller.click(-1000, 500, delay=0)

    assert _button_events() == [
        ("mouse", LEFTDOWN, (-1000, 500)),
        ("mouse", LEFTUP, (-1000, 500)),
    ]
    # Cursor restored after the click.
    assert mock_win32.state.cursor == (7, 9)


def test_click_without_cursor_restore() -> None:
    controller = _controller("single", restore_cursor=False)
    assert controller.click(100, 200, delay=0)
    assert mock_win32.state.cursor == (100, 200)


def test_click_near_edge_is_clamped() -> None:
    controller = _controller("single", restore_cursor=False)
    assert controller.click(1925, 500, delay=0)
    assert _button_events() == [
        ("mouse", LEFTDOWN, (1917, 500)),
        ("mouse", LEFTUP, (1917, 500)),
    ]


def test_click_in_dead_zone_is_refused() -> None:
    controller = _controller("non_contiguous")
    assert not controller.click(2200, 500, delay=0)
    assert _button_events() == []
    assert all(e[0] != "move" for e in mock_win32.state.events)
