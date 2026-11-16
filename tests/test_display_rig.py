"""Cross-platform tests for the display-rig helpers.

The rig itself only runs on Windows (``.github/workflows/windows-displays.yml``);
these cover its pure logic: topology parsing/matching, the label-integrity
coverage gate in ``tools/generate_scenarios.py``, the check that the app sees
the physical layout, and the reconciliation of logged with received clicks.
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pytest

from tools.ci.display_report import check_app_view
from tools.ci.topology import MonitorSpec, format_topology, match_rects, parse_topology
from tools.e2e_click_check import logged_clicks, unseen_clicks
from tools.generate_scenarios import _coverage, _frame_coverage, _hex_to_rgb


class TestParseTopology:
    def test_two_monitors_with_scales(self) -> None:
        monitors = parse_topology("1920x1080@0,0:100;1920x1080@1920,0:150")
        assert monitors == [
            MonitorSpec(1920, 1080, 0, 0, 100),
            MonitorSpec(1920, 1080, 1920, 0, 150),
        ]
        assert monitors[0].is_primary and not monitors[1].is_primary

    def test_negative_offset_and_optional_scale(self) -> None:
        monitors = parse_topology(" 1920x1080@0,0 ; 1280x720@-1280,100 ")
        assert monitors[1] == MonitorSpec(1280, 720, -1280, 100, None)

    def test_round_trip(self) -> None:
        spec = "2560x1440@0,0:125;1920x1080@2560,0:100"
        assert format_topology(parse_topology(spec)) == spec

    @pytest.mark.parametrize(
        "spec, message",
        [
            ("", "empty"),
            ("1920x1080", "Bad monitor spec"),
            ("1920x1080@0,0:110", "not one of"),
            ("1920x1080@10,0", "Exactly one monitor"),
            ("1920x1080@0,0;1920x1080@0,0", "Exactly one monitor"),
            ("1920x1080@0,0;1920x1080@1000,0", "overlap"),
            ("0x1080@0,0", "positive size"),
        ],
    )
    def test_rejects_invalid(self, spec: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            parse_topology(spec)


class TestMatchRects:
    def test_exact_match_in_any_order(self) -> None:
        expected = parse_topology("1920x1080@0,0;1920x1080@-1920,0")
        actual = [
            {"left": -1920, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
        assert match_rects(expected, actual) == []

    def test_reports_missing_and_unexpected(self) -> None:
        expected = parse_topology("1920x1080@0,0;1920x1080@1920,0")
        actual = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1280, "height": 720},
        ]
        problems = match_rects(expected, actual)
        assert problems == [
            "missing monitor 1920x1080@1920,0",
            "unexpected monitor 1280x720@1920,0",
        ]


class TestCoverageGate:
    """A window sized in the wrong DPI space must fail the label gate."""

    BG = "#1e1e2e"
    PHYS: ClassVar[dict[str, int]] = {"left": 0, "top": 0, "width": 300, "height": 200}

    def _screen(self) -> np.ndarray:
        return np.zeros((200, 300, 3), dtype=np.uint8)

    def test_hex_to_rgb(self) -> None:
        assert _hex_to_rgb(self.BG) == (0x1E, 0x1E, 0x2E)

    def test_full_cover_passes(self) -> None:
        img = self._screen()
        img[:, :] = _hex_to_rgb(self.BG)
        assert _coverage(img, self.PHYS, 0, 0, self.BG, None) == pytest.approx(1.0)

    def test_button_area_is_ignored(self) -> None:
        img = self._screen()
        img[:, :] = _hex_to_rgb(self.BG)
        img[90:110, 140:160] = (255, 255, 255)  # the drawn button
        assert _coverage(img, self.PHYS, 0, 0, self.BG, (140, 90, 160, 110)) == 1.0

    def test_window_two_thirds_size_fails(self) -> None:
        # 150 % monitor, window sized from logical bounds: covers 2/3 x 2/3.
        img = self._screen()
        img[:133, :200] = _hex_to_rgb(self.BG)
        assert _coverage(img, self.PHYS, 0, 0, self.BG, None) < 0.5

    def test_offset_capture_origin(self) -> None:
        # Secondary monitor left of primary: capture origin is negative.
        img = np.zeros((200, 600, 3), dtype=np.uint8)
        img[:, :300] = _hex_to_rgb(self.BG)  # the left monitor at x=-300
        phys = {"left": -300, "top": 0, "width": 300, "height": 200}
        assert _coverage(img, phys, -300, 0, self.BG, None) == pytest.approx(1.0)

    def test_per_monitor_frame(self) -> None:
        # The scenario generator checks one monitor's own frame image.
        frame = MonitorSpec(300, 200, -300, 0, None)
        img = self._screen()
        img[:, :] = _hex_to_rgb(self.BG)
        assert _frame_coverage(img, frame, self.BG, None) == pytest.approx(1.0)
        img[:, 200:] = 0
        assert _frame_coverage(img, frame, self.BG, None) == pytest.approx(2 / 3)


class TestAppView:
    """The app must see the physical layout, or every click is scaled wrong."""

    TOPOLOGY = "1920x1080@0,0:150;1366x768@1920,0:100"  # issue #23's setup

    def _view(self, **overrides: Any) -> dict[str, Any]:
        view: dict[str, Any] = {
            "dpi_awareness_before": 0,
            "dpi_awareness_after": 2,
            "app_monitors": [
                {"x": 0, "y": 0, "width": 1920, "height": 1080},
                {"x": 1920, "y": 0, "width": 1366, "height": 768},
            ],
            "app_frames": [
                {"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080},
                {"index": 1, "left": 1920, "top": 0, "width": 1366, "height": 768},
            ],
        }
        view.update(overrides)
        return view

    def test_physical_layout_passes(self) -> None:
        assert check_app_view(self._view(), parse_topology(self.TOPOLOGY)) == []

    def test_logical_monitor_bounds_fail(self) -> None:
        # What issue #23's log shows: the 150 % primary reported as 1280x720.
        view = self._view(
            app_monitors=[
                {"x": 0, "y": 0, "width": 1280, "height": 720},
                {"x": 1920, "y": 0, "width": 1366, "height": 768},
            ]
        )
        problems = check_app_view(view, parse_topology(self.TOPOLOGY))
        assert problems == [
            "app monitor list: missing monitor 1920x1080@0,0:150",
            "app monitor list: unexpected monitor 1280x720@0,0",
        ]

    def test_unaware_process_fails(self) -> None:
        problems = check_app_view(
            self._view(dpi_awareness_after=0), parse_topology(self.TOPOLOGY)
        )
        assert problems == ["app process DPI awareness is 0, wanted 2 (per-monitor)"]


class TestClickReconciliation:
    """Every click the app logs must have been received by a target window."""

    LOG = (
        "13:17:08 | INFO     | click:74 - Clicked at (3178, 800)\n"
        "13:17:08 | DEBUG    | capture_frames:88 - Captured 2 frame(s)\n"
        "13:17:14 | INFO     | click:74 - Clicked at (-959, 527)\n"
    )

    def test_parses_logged_clicks_in_order(self) -> None:
        assert logged_clicks(self.LOG) == [[3178, 800], [-959, 527]]

    def test_all_received(self) -> None:
        received = [[-959, 527], [3178, 801]]  # any order, rounding slack
        assert unseen_clicks(logged_clicks(self.LOG), received) == []

    def test_click_outside_every_window_is_unseen(self) -> None:
        assert unseen_clicks(logged_clicks(self.LOG), [[-959, 527]]) == [[3178, 800]]

    def test_one_received_click_matches_one_logged_click(self) -> None:
        # The app clicked the same point twice; the target got it once.
        logged = [[100, 100], [100, 100]]
        assert unseen_clicks(logged, [[100, 100]]) == [[100, 100]]
