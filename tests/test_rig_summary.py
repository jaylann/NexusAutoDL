"""The cross-layout compatibility report built from rig artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.ci.summarise_rig import leg_row, render


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _click(step: int | None, ok: bool = True, offset: list[int] | None = None) -> dict:
    return {"step": step, "ok": ok, "offset": offset, "click": [5, 5], "note": ""}


def _phase(mode: str, **overrides: Any) -> dict[str, Any]:
    phase: dict[str, Any] = {
        "mode": mode,
        "steps": 2,
        "ok": True,
        "completed": True,
        "skipped": "",
        "unseen_app_clicks": [],
        "clicks": [_click(0, offset=[0, -2]), _click(1, offset=[1, 0])],
    }
    phase.update(overrides)
    return phase


def _leg(root: Path, name: str = "dual-1080p-100-150", **parts: Any) -> Path:
    """A downloaded ``displays-<name>`` artifact; ``parts`` override files."""
    leg = root / f"displays-{name}"
    report = {
        "mss_monitors": [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ],
        "monitor_scales": [100, 150],
        "app_view": {"dpi_awareness_after": 2},
        "problems": [],
    }
    verify = {
        "present": 72,
        "hit": 72,
        "missed": 0,
        "off": 0,
        "negative_frames": 4,
        "false_positives": 0,
        "max_error_px": 1.4,
        "tolerance_px": 10,
        "coverage_failures": 0,
        "button_types": 6,
    }
    results = {"phases": [_phase("web"), _phase("vortex")]}
    files = {"report": report, "verify": verify, "results": results, **parts}
    if files["report"] is not None:
        _write(leg / "artifacts" / "report" / "report.json", files["report"])
    if files["verify"] is not None:
        _write(
            leg / "tests" / "fixtures" / "detection" / "real" / f"{name}.verify.json",
            files["verify"],
        )
    if files["results"] is not None:
        _write(leg / "artifacts" / "click-check" / "results.json", files["results"])
    return leg


def test_passing_leg(tmp_path: Path) -> None:
    row = leg_row(_leg(tmp_path))
    assert row["ok"] is True
    assert row["layout"] == "dual-1080p-100-150"
    assert row["monitors"] == "1920x1080@0,0 100%, 1920x1080@1920,0 150%"
    assert row["scenarios"] == (
        "72/72 found within 10 px (worst 1.4), 0 false positives in 24 checks"
    )
    assert row["web"] == "2/2 on centre (worst 2.0 px), 0 stray"
    assert row["noisy"] == "not run"


def test_missing_results_fail_the_leg(tmp_path: Path) -> None:
    # A leg that died before the click check must not read as green.
    row = leg_row(_leg(tmp_path, results=None))
    assert row["ok"] is False
    assert row["web"] == "no result"


def test_stray_click_fails_the_leg(tmp_path: Path) -> None:
    phase = _phase("vortex", ok=False)
    phase["clicks"].append(_click(None, ok=False))
    row = leg_row(_leg(tmp_path, results={"phases": [_phase("web"), phase]}))
    assert row["ok"] is False
    assert row["vortex"] == "2/2 on centre (worst 2.0 px), 1 stray"


def test_false_positive_fails_the_leg(tmp_path: Path) -> None:
    verify = {
        "present": 36,
        "hit": 36,
        "negative_frames": 2,
        "false_positives": 1,
        "max_error_px": 0.0,
        "tolerance_px": 10,
        "button_types": 6,
    }
    row = leg_row(_leg(tmp_path, verify=verify))
    assert row["ok"] is False
    assert "1 false positives in 12 checks" in row["scenarios"]


def test_app_seeing_a_logical_layout_fails_the_leg(tmp_path: Path) -> None:
    report = {
        "mss_monitors": [{"left": 0, "top": 0, "width": 1920, "height": 1080}],
        "monitor_scales": [150],
        "app_view": {"dpi_awareness_after": 2},
        "problems": ["app monitor list: unexpected monitor 1280x720@0,0"],
    }
    row = leg_row(_leg(tmp_path, report=report))
    assert row["ok"] is False
    assert row["app_view"].startswith("MISMATCH")


def test_skipped_vortex_phase_on_one_monitor_still_passes(tmp_path: Path) -> None:
    skipped = _phase("vortex", skipped="vortex mode needs 2+ monitors", clicks=[])
    row = leg_row(_leg(tmp_path, results={"phases": [_phase("web"), skipped]}))
    assert row["ok"] is True
    assert row["vortex"] == "skipped (vortex mode needs 2+ monitors)"


def test_render_counts_passing_layouts(tmp_path: Path) -> None:
    rows = [
        leg_row(_leg(tmp_path, "single-1080p-100")),
        leg_row(_leg(tmp_path, "mixed", results=None)),
    ]
    text = render(rows)
    assert "1 of 2 layouts pass" in text
    assert "| single-1080p-100 |" in text and "| PASS |" in text
    assert "| mixed |" in text and "| FAIL |" in text
