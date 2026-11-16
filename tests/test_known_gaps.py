"""A known gap is a miss the suite tolerates, never a wrong click.

``known_gap`` marks a real button the detector does not find yet. These tests
pin what the marker allows: nothing detected is an expected failure, a
detection anywhere else fails, and finding the button fails too until the
marker is removed, so a closed gap cannot stay labelled as open.
"""

from __future__ import annotations

from typing import Any

import pytest

import detection_report
from models import ButtonType, DetectionResult
from tests.harness import known_gap, load_cases
from tests.test_detection import test_detection_case as run_case

_GAP_CASE: dict[str, Any] = {
    "image": "real/issue13_new_vortex_dialog.png",
    "button_type": "vortex",
    "expect": "present",
    "point": [182, 379],
    "tolerance_px": 10,
    "known_gap": "no picture captured at 100 %",
    "meta": {"source": "real", "variant": "unit"},
}


def _result(x: int, y: int) -> DetectionResult:
    return DetectionResult(
        button_type=ButtonType.VORTEX,
        x=x,
        y=y,
        confidence=0.9,
        num_matches=12,
        template_width=421,
        template_height=73,
    )


class _Detector:
    """Stands in for ButtonDetector: returns a fixed answer."""

    def __init__(self, result: DetectionResult | None) -> None:
        self._result = result

    def detect(self, *_args: Any, **_kwargs: Any) -> DetectionResult | None:
        return self._result


def test_a_missed_known_gap_is_an_expected_failure() -> None:
    with pytest.raises(pytest.xfail.Exception, match="known gap"):
        run_case(_GAP_CASE, _Detector(None))


def test_a_known_gap_detected_elsewhere_fails() -> None:
    # The dialog title "Download mod" sits about 340 px above the button.
    with pytest.raises(AssertionError, match="wrong place"):
        run_case(_GAP_CASE, _Detector(_result(60, 35)))


def test_a_closed_known_gap_fails_until_the_marker_is_removed() -> None:
    with pytest.raises(pytest.fail.Exception, match="Remove 'known_gap'"):
        run_case(_GAP_CASE, _Detector(_result(183, 380)))


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        (None, "missed"),
        (_result(183, 380), "found now: remove known_gap from the case"),
        (_result(60, 35), "DETECTED IN THE WRONG PLACE"),
    ],
)
def test_report_names_the_outcome(result: DetectionResult | None, outcome: str) -> None:
    assert detection_report._gap_outcome(_GAP_CASE, result) == outcome


def test_only_present_cases_carry_the_marker() -> None:
    """On an absent-case the marker would silently do nothing."""
    marked = [case for case in load_cases() if known_gap(case)]
    assert marked, "the manifest lists the real buttons that are not found yet"
    assert all(case["expect"] == "present" for case in marked)
