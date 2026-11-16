"""Pure logic of the real-Vortex check: where the button is, and the verdict."""

from __future__ import annotations

from typing import Any

import pytest

from tools.ci.real_vortex_check import (
    EXIT_FAILED,
    EXIT_NOT_SHOWN,
    EXIT_OK,
    expected_centre,
    judge,
    settled_ticks,
    summary_line,
    target_button,
)


def _button(text: str, left: float, top: float, button_id: str = "") -> dict[str, Any]:
    return {
        "id": button_id,
        "text": text,
        "rect": {"left": left, "top": top, "right": left + 270, "bottom": top + 38},
    }


def _report(*buttons: dict[str, Any], ratio: float = 1.0) -> dict[str, Any]:
    return {"devicePixelRatio": ratio, "buttons": list(buttons)}


# Vortex 2.x: the dialog's buttons as the extension reports them.
NEW_DIALOG = _report(
    _button("Download manually", 660, 392, "download-mod-button"),
    _button("Auto-download all", 990, 392, "get-premium-button"),
    _button("Cancel install", 1100, 480, "cancel-button"),
    _button("Skip mod", 1210, 480, "skip-button"),
)


class TestTargetButton:
    def test_picked_by_its_id(self) -> None:
        assert target_button(NEW_DIALOG)["text"] == "Download manually"

    def test_old_dialog_is_picked_by_its_exact_label(self) -> None:
        old = _report(
            _button("Go Premium & Download Fast", 50, 300),
            _button("Download", 480, 300),
            _button("Cancel", 460, 380),
        )
        assert target_button(old)["rect"]["left"] == 480

    def test_no_report_or_no_dialog_means_no_button(self) -> None:
        assert target_button(None) is None
        assert target_button(_report()) is None

    def test_a_button_that_is_not_laid_out_yet_does_not_count(self) -> None:
        unpainted = _button("Download manually", 0, 0, "download-mod-button")
        unpainted["rect"] = {"left": 0, "top": 0, "right": 0, "bottom": 0}
        assert target_button(_report(unpainted)) is None


class TestExpectedCentre:
    def test_css_pixels_at_100_percent_are_screen_pixels(self) -> None:
        assert expected_centre(NEW_DIALOG, (0, 0)) == (795.0, 411.0)

    def test_scaled_by_the_device_pixel_ratio(self) -> None:
        at_150 = {**NEW_DIALOG, "devicePixelRatio": 1.5}
        assert expected_centre(at_150, (0, 0)) == (1192.5, 616.5)

    def test_offset_by_the_window_origin_in_physical_pixels(self) -> None:
        # A window on a monitor left of the primary: the origin is not scaled.
        at_125 = {**NEW_DIALOG, "devicePixelRatio": 1.25}
        assert expected_centre(at_125, (-1920, 30)) == (-926.25, 543.75)

    def test_none_while_the_dialog_is_not_there(self) -> None:
        assert expected_centre(_report(), (0, 0)) is None


def _tick(
    expected: list[float] | None, detected: tuple[int, int] | None
) -> dict[str, Any]:
    return {
        "expected": expected,
        "detected": None if detected is None else {"x": detected[0], "y": detected[1]},
    }


AT = [795.0, 411.0]


def _steady(count: int, detected: tuple[int, int] | None = (794, 412)) -> list[Any]:
    """The dialog appears, then ``count`` ticks with the button standing still."""
    return [_tick(None, None), _tick(AT, detected)] + [
        _tick(AT, detected) for _ in range(count)
    ]


class TestSettledTicks:
    def test_the_first_tick_with_a_button_is_not_settled(self) -> None:
        ticks = _steady(3)
        assert settled_ticks(ticks) == ticks[2:]

    def test_a_moving_button_is_not_settled(self) -> None:
        sliding = [_tick([795.0, 380.0 + step], (794, 400)) for step in range(4)]
        assert settled_ticks(sliding) == []


class TestJudge:
    def test_found_on_every_settled_frame_is_ok(self) -> None:
        verdict = judge(_steady(6), [], tolerance=4, min_frames=5)
        assert (verdict["status"], verdict["exit"]) == ("ok", EXIT_OK)
        assert verdict["frames"] == 6
        assert verdict["worst_px"] == pytest.approx(1.41, abs=0.01)

    def test_too_few_settled_frames_tests_nothing(self) -> None:
        verdict = judge(_steady(2), [], tolerance=4, min_frames=5)
        assert (verdict["status"], verdict["exit"]) == ("not shown", EXIT_NOT_SHOWN)

    def test_a_dialog_that_never_appears_tests_nothing(self) -> None:
        ticks = [_tick(None, None) for _ in range(20)]
        assert judge(ticks, [], 4, 5)["exit"] == EXIT_NOT_SHOWN

    def test_a_missed_button_fails(self) -> None:
        verdict = judge(_steady(6, detected=None), [], tolerance=4, min_frames=5)
        assert verdict["exit"] == EXIT_FAILED
        assert "not found on 6 of 6 frames" in verdict["reason"]

    def test_one_missed_frame_is_enough_to_fail(self) -> None:
        ticks = _steady(6)
        ticks[4] = _tick(AT, None)
        assert judge(ticks, [], 4, 5)["exit"] == EXIT_FAILED

    def test_a_click_point_beside_the_button_fails(self) -> None:
        # The dialog title sits well above the button.
        verdict = judge(_steady(6, detected=(700, 60)), [], tolerance=4, min_frames=5)
        assert verdict["exit"] == EXIT_FAILED
        assert "px from the button" in verdict["reason"]

    def test_another_button_type_firing_fails(self) -> None:
        verdict = judge(_steady(6), ["understood"], tolerance=4, min_frames=5)
        assert verdict["exit"] == EXIT_FAILED
        assert "also fired: understood" in verdict["reason"]

    def test_other_types_never_searched_fails(self) -> None:
        verdict = judge(_steady(6), None, tolerance=4, min_frames=5)
        assert verdict["exit"] == EXIT_FAILED
        assert "never searched" in verdict["reason"]


def test_summary_line_carries_the_label_and_the_reason() -> None:
    verdict = judge(_steady(6), [], tolerance=4, min_frames=5)
    line = summary_line("Vortex 2.10.1 at 125 %", verdict)
    assert line.startswith("Real Vortex (Vortex 2.10.1 at 125 %): ok (found on 6 of 6")
