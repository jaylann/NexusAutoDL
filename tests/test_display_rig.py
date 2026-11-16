"""Cross-platform tests for the display-rig helpers.

The rig itself only runs on Windows (``.github/workflows/windows-displays.yml``);
these cover its pure logic: topology parsing/matching and the label-integrity
coverage gate in ``tools/generate_scenarios.py``.
"""

from __future__ import annotations

import numpy as np
import pytest

from tools.ci.topology import MonitorSpec, format_topology, match_rects, parse_topology
from tools.generate_scenarios import _coverage, _hex_to_rgb


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
    PHYS = {"left": 0, "top": 0, "width": 300, "height": 200}

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
