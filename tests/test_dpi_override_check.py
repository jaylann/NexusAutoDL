"""Pure logic of the rig's forced-DPI-mode check."""

from __future__ import annotations

from typing import Any

import pytest

from tools.ci.dpi_override_check import expect_scaled, judge, summary_line
from tools.ci.topology import parse_topology

UNIFORM_100 = parse_topology("1920x1080@0,0:100;1920x1080@1920,0:100")
UNIFORM_150 = parse_topology("1920x1080@0,0:150")
ISSUE_23 = parse_topology("1920x1080@0,0:150;1366x768@1920,0:100")


class TestExpectScaled:
    def test_unaware_is_fine_at_100_percent_only(self) -> None:
        assert expect_scaled(0, UNIFORM_100) is False
        assert expect_scaled(0, UNIFORM_150) is True
        assert expect_scaled(0, ISSUE_23) is True

    def test_system_aware_is_fine_while_every_monitor_matches_the_primary(
        self,
    ) -> None:
        assert expect_scaled(1, UNIFORM_100) is False
        assert expect_scaled(1, UNIFORM_150) is False
        assert expect_scaled(1, ISSUE_23) is True

    def test_per_monitor_aware_is_never_scaled(self) -> None:
        assert expect_scaled(2, ISSUE_23) is False


def _probe(after: int, scaled: list[str]) -> dict[str, Any]:
    return {"before": after, "after": after, "scaled": scaled}


class TestJudge:
    SCALED = ("\\\\.\\DISPLAY1 is 1920x1080 but reported as 1280x720",)

    def test_refusal_on_a_scaled_layout_is_ok(self) -> None:
        result = judge("DPIUNAWARE", _probe(0, list(self.SCALED)), 2, True, ISSUE_23)
        assert result["status"] == "ok"

    def test_app_that_keeps_running_on_a_scaled_layout_fails(self) -> None:
        result = judge(
            "DPIUNAWARE", _probe(0, list(self.SCALED)), None, False, ISSUE_23
        )
        assert result["status"] == "fail"
        assert "should have refused" in result["reason"]

    def test_check_that_misses_the_scaling_fails(self) -> None:
        # The process is unaware on a 150 % monitor but the app's check saw
        # nothing: the comparison itself is broken.
        result = judge("DPIUNAWARE", _probe(0, []), None, False, UNIFORM_150)
        assert result["status"] == "fail"
        assert "scaled_monitors()" in result["reason"]

    def test_harmless_forced_mode_must_not_stop_the_app(self) -> None:
        # Unaware at 100 % everywhere: coordinates are physical anyway.
        assert (
            judge("DPIUNAWARE", _probe(0, []), None, False, UNIFORM_100)["status"]
            == "ok"
        )
        refused = judge("DPIUNAWARE", _probe(0, []), 2, True, UNIFORM_100)
        assert refused["status"] == "fail"

    def test_system_aware_on_a_uniform_layout_runs(self) -> None:
        result = judge("HIGHDPIAWARE", _probe(1, []), None, False, UNIFORM_150)
        assert result["status"] == "ok"

    def test_layer_that_did_not_take_effect_is_reported_not_failed(self) -> None:
        result = judge("DPIUNAWARE", _probe(2, []), None, False, ISSUE_23)
        assert result["status"] == "not applied"

    def test_missing_probe_fails(self) -> None:
        assert judge("DPIUNAWARE", None, None, False, ISSUE_23)["status"] == "fail"


def test_summary_line_names_every_layer() -> None:
    results = [
        {"layer": "DPIUNAWARE", "status": "ok", "reason": "app refused to start"},
        {"layer": "HIGHDPIAWARE", "status": "not applied", "reason": "x"},
    ]
    line = summary_line(results)
    assert line.startswith("Forced DPI mode: DPIUNAWARE: ok (app refused to start)")
    assert "HIGHDPIAWARE: not applied" in line


@pytest.mark.parametrize("layer", ["DPIUNAWARE", "HIGHDPIAWARE"])
def test_per_monitor_awareness_always_means_not_applied(layer: str) -> None:
    assert judge(layer, _probe(2, []), None, False, UNIFORM_100)["status"] == (
        "not applied"
    )
