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

from models import ButtonType
from tools.ci.display_report import check_app_view
from tools.ci.topology import MonitorSpec, format_topology, match_rects, parse_topology
from tools.e2e_click_check import (
    IDLE_SCENES,
    VORTEX_TEMPLATES,
    WEB_TEMPLATES,
    TemplateInfo,
    build_steps,
    draw_scale,
    judge_click,
    logged_clicks,
    summary_markdown,
    unseen_clicks,
)
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


class TestClickJudgement:
    RECT: ClassVar[list[int]] = [100, 200, 370, 238]  # 270x38, centre (235, 219)

    def test_centre_click_is_ok(self) -> None:
        assert judge_click(self.RECT, [235, 219], 4) == (True, True, [0, 0])

    def test_inside_but_off_centre_is_not_ok(self) -> None:
        # Hits the wide button, but 100 px from where it should have clicked.
        inside, ok, offset = judge_click(self.RECT, [335, 219], 4)
        assert (inside, ok, offset) == (True, False, [100, 0])

    def test_tolerance_is_a_radius(self) -> None:
        assert judge_click(self.RECT, [238, 222], 4)[1] is False  # 4.24 px away
        assert judge_click(self.RECT, [235, 223], 4)[1] is True

    def test_outside_is_a_miss(self) -> None:
        inside, ok, _ = judge_click(self.RECT, [235, 190], 100)
        assert (inside, ok) == (False, False)


class TestDrawScale:
    AT_150: ClassVar[TemplateInfo] = VORTEX_TEMPLATES[0]
    AT_100: ClassVar[TemplateInfo] = WEB_TEMPLATES[1]

    def test_150_percent_template_on_a_100_percent_monitor(self) -> None:
        # Issue #13: the real button at 100 % is two thirds of the template.
        assert draw_scale(self.AT_150, 1.0, 1.0) == pytest.approx(2 / 3)

    def test_template_scale_is_native_on_its_capture_scale(self) -> None:
        assert draw_scale(self.AT_150, 1.5, 1.0) == pytest.approx(1.0)
        assert draw_scale(self.AT_100, 1.0, 1.0) == pytest.approx(1.0)

    def test_zoom_multiplies(self) -> None:
        assert draw_scale(self.AT_150, 1.25, 1.5) == pytest.approx(1.25)


class TestBuildSteps:
    def test_web_covers_every_monitor_each_round(self) -> None:
        steps = build_steps("web", monitor_count=2, primary=0, rounds=3)
        buttons = [s for s in steps if s.kind == "web"]
        assert [s.monitor for s in buttons] == [0, 1, 0, 1, 0, 1]
        # Every picture of the website button is shown, old design included.
        assert {s.template for s in buttons} == set(WEB_TEMPLATES)
        assert [s.index for s in steps] == list(range(len(steps)))

    def test_one_idle_scene_after_each_round(self) -> None:
        steps = build_steps("web", monitor_count=1, primary=0, rounds=3)
        assert [s.kind for s in steps] == ["web", "idle"] * 3
        assert [s.scene for s in steps if s.kind == "idle"] == list(IDLE_SCENES)

    def test_vortex_rounds_end_where_the_app_starts(self) -> None:
        # Vortex button, then the website button on the primary, then idle:
        # an idle scene between the two would strand the app mid-sequence.
        steps = build_steps("vortex", monitor_count=2, primary=1, rounds=2)
        assert [s.kind for s in steps] == ["vortex", "web", "idle"] * 2
        assert all(s.monitor == 1 for s in steps if s.kind == "web")

    def test_vortex_mode_shows_the_old_and_the_new_vortex_button(self) -> None:
        steps = build_steps("vortex", monitor_count=2, primary=0, rounds=3)
        shown = [s.template for s in steps if s.kind == "vortex"]
        assert set(shown) == set(VORTEX_TEMPLATES)
        assert {t.generation for t in shown} == {"new", "legacy"}
        assert all(t.button_type is ButtonType.VORTEX for t in shown)

    def test_zoom_changes_per_round(self) -> None:
        steps = build_steps("web", monitor_count=1, primary=0, rounds=3)
        assert [s.zoom for s in steps if s.kind == "web"] == [1.0, 1.5, 1.1]


class TestSummaryMarkdown:
    def _summary(self, **phase: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "mode": "web",
            "steps": 1,
            "ok": True,
            "skipped": "",
            "idle_scenes": ["blank", "lookalikes"],
            "unseen_app_clicks": [],
            "clicks": [
                {
                    "step": 0,
                    "button_type": "website",
                    "asset": "WebsiteDownloadButtonNew.png",
                    "generation": "new",
                    "monitor": 0,
                    "dpi_scale": 1.5,
                    "draw_scale": 1.0,
                    "offset": [0, -1],
                    "latency": 1.4,
                    "ok": True,
                    "note": "",
                    "click": [960, 540],
                }
            ],
        }
        base.update(phase)
        return {
            "monitors": [{"left": 0, "top": 0, "width": 1920, "height": 1080}],
            "scales": [1.5],
            "phases": [base],
        }

    def test_lists_buttons_and_idle_scenes(self) -> None:
        text = summary_markdown(self._summary())
        assert "1920x1080@0,0 150%" in text
        assert "PASS, 1/1 buttons clicked on centre, 0 stray" in text
        assert "idle scenes: blank, lookalikes" in text
        assert (
            "| 0 | WebsiteDownloadButtonNew.png | new | 0 (150%) | x1.0 | [0, -1] "
            "| 1.4 | ok |"
        ) in text

    def test_stray_click_is_listed(self) -> None:
        stray = {"step": None, "click": [10, 20], "note": "stray:idle:busy"}
        summary = self._summary(ok=False)
        summary["phases"][0]["clicks"].append(stray)
        text = summary_markdown(summary)
        assert "FAIL" in text and "1 stray" in text
        assert "STRAY at [10, 20] (stray:idle:busy)" in text

    def test_skipped_phase(self) -> None:
        text = summary_markdown(self._summary(skipped="vortex mode needs 2+ monitors"))
        assert "skipped (vortex mode needs 2+ monitors)" in text
