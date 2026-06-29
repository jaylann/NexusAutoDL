"""Parametrized detection presence + click-accuracy assertions.

Each entry in ``fixtures/detection/cases.json`` becomes one test:

* ``expect == "present"`` -- ``detect`` must return a result whose centroid
  lands within ``tolerance_px`` of the ground-truth ``point``.
* ``expect == "absent"``  -- ``detect`` must return ``None`` (false-positive
  guard; this is the direct regression test for raising ``min_matches``).

Cases whose button type / template asset / image is unavailable on the current
branch are skipped, not failed, so the suite is portable across branches.
"""

from __future__ import annotations

from typing import Any

import pytest

from models import AppConfig, ButtonType
from services.button_detector import ButtonDetector
from tests.harness import (
    case_id,
    case_image_path,
    centroid_error,
    load_cases,
    read_rgb,
    skip_reason,
)

_CASES = load_cases()


def _params(case: dict[str, Any], defaults: AppConfig) -> dict[str, Any]:
    """Resolve per-case detection overrides, falling back to AppConfig."""
    return {
        "min_matches": int(case.get("min_matches", defaults.min_matches)),
        "ratio": float(case.get("ratio", defaults.ratio_threshold)),
    }


@pytest.mark.skipif(not _CASES, reason="no detection cases in cases.json")
@pytest.mark.parametrize("case", _CASES, ids=[case_id(c) for c in _CASES])
def test_detection_case(case: dict[str, Any], detector: ButtonDetector) -> None:
    reason = skip_reason(case)
    if reason:
        pytest.skip(reason)

    defaults = AppConfig()
    button_type = ButtonType(case["button_type"])
    img = read_rgb(case_image_path(case))
    result = detector.detect(img, button_type, **_params(case, defaults))

    if case["expect"] == "absent":
        assert result is None, (
            f"expected no detection but found {button_type.value} at "
            f"({result.x}, {result.y}) with {result.num_matches} matches"
        )
        return

    # expect == "present"
    assert result is not None, (
        f"expected {button_type.value} but detect() returned None"
    )

    point = case["point"]
    tolerance = float(case["tolerance_px"])
    error = centroid_error((result.x, result.y), point)
    assert error <= tolerance, (
        f"{button_type.value} centroid ({result.x}, {result.y}) is {error:.1f}px "
        f"from ground truth {tuple(point)} (tolerance {tolerance:.0f}px)"
    )
