"""Unit tests for virtual-desktop geometry across odd monitor layouts."""

from __future__ import annotations

import pytest

from models import BoundingBox, Monitor
from services.geometry import MonitorFrame, VirtualDesktop, compute_search_bbox


def _mss(*rects: tuple[int, int, int, int]) -> list[dict[str, int]]:
    """Build an mss-style monitor list (index 0 = union of the rest)."""
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    union = {"left": left, "top": top, "width": right - left, "height": bottom - top}
    return [union] + [
        {"left": r[0], "top": r[1], "width": r[2], "height": r[3]} for r in rects
    ]


LAYOUTS: dict[str, list[dict[str, int]]] = {
    "single": _mss((0, 0, 1920, 1080)),
    "left_of_primary": _mss((0, 0, 1920, 1080), (-1920, 0, 1920, 1080)),
    "above_primary": _mss((0, 0, 2560, 1440), (0, -1440, 2560, 1440)),
    "portrait_right": _mss((0, 0, 1920, 1080), (1920, -400, 1080, 1920)),
    "non_contiguous": _mss((0, 0, 1920, 1080), (2500, 200, 1280, 720)),
    "mixed_triple": _mss(
        (0, 0, 3840, 2160), (-1920, 500, 1920, 1080), (3840, -300, 1080, 1920)
    ),
}


@pytest.mark.parametrize("name", LAYOUTS)
def test_round_trip_conversion(name: str) -> None:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS[name])
    for frame in desktop.frames:
        for fx, fy in ((0, 0), (frame.width - 1, frame.height - 1), (17, 43)):
            vx, vy = frame.to_virtual(fx, fy)
            assert frame.from_virtual(vx, vy) == (fx, fy)
            assert frame.contains(vx, vy)
            assert desktop.frame_containing(vx, vy) == frame


@pytest.mark.parametrize("name", LAYOUTS)
def test_primary_is_origin_monitor(name: str) -> None:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS[name])
    assert desktop.primary.left == 0
    assert desktop.primary.top == 0


def test_primary_detected_regardless_of_order() -> None:
    # Primary listed last: origin monitor must still win.
    monitors = _mss((-1920, 0, 1920, 1080), (0, 0, 1920, 1080))
    desktop = VirtualDesktop.from_mss_monitors(monitors)
    assert desktop.primary.left == 0
    assert [f.left for f in desktop.secondaries] == [-1920]


def test_single_monitor_list_without_union_entry() -> None:
    desktop = VirtualDesktop.from_mss_monitors(
        [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
    )
    assert len(desktop.frames) == 1
    assert desktop.primary.width == 1920


def test_clamp_inside_passes_through() -> None:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS["left_of_primary"])
    assert desktop.clamp_to_nearest(-1000, 500) == (-1000, 500)
    assert desktop.clamp_to_nearest(100, 100) == (100, 100)


def test_clamp_snaps_near_edges() -> None:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS["single"])
    assert desktop.clamp_to_nearest(-5, 500) == (2, 500)
    assert desktop.clamp_to_nearest(1925, 500) == (1917, 500)
    assert desktop.clamp_to_nearest(500, 1085) == (500, 1077)


def test_clamp_refuses_dead_zones() -> None:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS["non_contiguous"])
    # Gap between the two monitors, farther than max_snap from both.
    assert desktop.clamp_to_nearest(2200, 500) is None
    # Just outside the second monitor still snaps.
    assert desktop.clamp_to_nearest(2495, 500) == (2502, 500)


def test_clamp_negative_origin_monitor() -> None:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS["above_primary"])
    assert desktop.clamp_to_nearest(1280, -1445) == (1280, -1438)


def test_matches_win32_agreement() -> None:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS["left_of_primary"])
    monitors = [
        Monitor(x=0, y=0, width=1920, height=1080),
        Monitor(x=-1920, y=0, width=1920, height=1080),
    ]
    assert desktop.matches_win32(monitors)


def test_matches_win32_detects_dpi_virtualization() -> None:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS["single"])
    # win32 reporting logical (150%-scaled) coordinates: 1920/1.5 = 1280.
    scaled = [Monitor(x=0, y=0, width=1280, height=720)]
    assert not desktop.matches_win32(scaled)


def test_matches_win32_detects_count_mismatch() -> None:
    desktop = VirtualDesktop.from_mss_monitors(LAYOUTS["left_of_primary"])
    assert not desktop.matches_win32([Monitor(x=0, y=0, width=1920, height=1080)])


def test_search_bbox_clips_overhanging_window() -> None:
    frame = MonitorFrame(index=0, left=1920, top=0, width=1920, height=1080)
    # Maximized window overhangs the monitor by 8px invisible borders.
    rect = BoundingBox(x1=1912, y1=-8, x2=3848, y2=1088)
    bbox = compute_search_bbox(rect, frame)
    assert bbox is not None
    assert 0 <= bbox.x1 < bbox.x2 <= frame.width
    assert 0 <= bbox.y1 < bbox.y2 <= frame.height


def test_search_bbox_none_for_window_on_other_monitor() -> None:
    frame = MonitorFrame(index=0, left=0, top=0, width=1920, height=1080)
    rect = BoundingBox(x1=2000, y1=100, x2=3000, y2=900)
    assert compute_search_bbox(rect, frame) is None


def test_search_bbox_window_spanning_negative_origin() -> None:
    frame = MonitorFrame(index=1, left=-1920, top=0, width=1920, height=1080)
    rect = BoundingBox(x1=-1820, y1=100, x2=-100, y2=980)
    bbox = compute_search_bbox(rect, frame)
    assert bbox is not None
    assert bbox.x1 >= 100 - 100  # frame-image space, inset applied
    assert bbox.x2 <= 1820
