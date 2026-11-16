"""Build one compatibility table from every layout's rig artifacts.

Each leg of ``.github/workflows/windows-displays.yml`` uploads an artifact
named ``displays-<layout>``. Download them all into one directory and run:

    python tools/ci/summarise_rig.py rig-artifacts --out compat-report.md

One row per layout: what the app saw, how the scenario sweep and the click
check went. Exits 1 when a layout failed or its results are missing, so the
report cannot read as green with a leg absent. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

_ARTIFACT_PREFIX = "displays-"
_PER_MONITOR_AWARE = 2


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _scenario_cell(verify: dict[str, Any] | None) -> tuple[str, bool]:
    """Text and verdict for one scenario sweep (``<prefix>.verify.json``)."""
    if verify is None:
        return "no result", False
    ok = (
        verify["present"] > 0
        and verify["hit"] == verify["present"]
        and verify["false_positives"] == 0
        and verify.get("coverage_failures", 0) == 0
    )
    checks = verify["negative_frames"] * verify.get("button_types", 1)
    text = (
        f"{verify['hit']}/{verify['present']} found within "
        f"{verify['tolerance_px']} px (worst {verify['max_error_px']}), "
        f"{verify['false_positives']} false positives in {checks} checks"
    )
    if verify.get("coverage_failures"):
        text += f", {verify['coverage_failures']} unusable captures"
    return text, ok


def _click_cell(phase: dict[str, Any] | None) -> tuple[str, bool]:
    """Text and verdict for one click-check phase."""
    if phase is None:
        return "no result", False
    if phase["skipped"]:
        return f"skipped ({phase['skipped']})", True
    buttons = [c for c in phase["clicks"] if c["step"] is not None]
    strays = len(phase["clicks"]) - len(buttons)
    offsets = [math.hypot(*c["offset"]) for c in buttons if c["offset"]]
    worst = f"{max(offsets):.1f}" if offsets else "-"
    text = (
        f"{sum(c['ok'] for c in buttons)}/{phase['steps']} on centre "
        f"(worst {worst} px), {strays} stray"
    )
    if phase["unseen_app_clicks"]:
        text += f", {len(phase['unseen_app_clicks'])} never received"
    if not phase["completed"]:
        text += ", incomplete"
    return text, bool(phase["ok"])


def leg_row(leg_dir: Path) -> dict[str, Any]:
    """Everything the table shows about one layout."""
    name = leg_dir.name[len(_ARTIFACT_PREFIX) :]
    artifacts = leg_dir / "artifacts"
    report = _load(artifacts / "report" / "report.json")
    results = _load(artifacts / "click-check" / "results.json")
    real = leg_dir / "tests" / "fixtures" / "detection" / "real"

    monitors = "no report"
    app_ok = False
    if report:
        monitors = ", ".join(
            f"{m['width']}x{m['height']}@{m['left']},{m['top']} {scale}%"
            for m, scale in zip(report["mss_monitors"], report["monitor_scales"])
        )
        view = report.get("app_view", {})
        app_ok = (
            not report.get("problems")
            and view.get("dpi_awareness_after") == _PER_MONITOR_AWARE
        )

    scenarios, scenarios_ok = _scenario_cell(_load(real / f"{name}.verify.json"))
    noisy = _load(artifacts / "noisy-scenarios" / f"{name}-noisy.verify.json")
    noisy_text = _scenario_cell(noisy)[0] if noisy else "not run"

    phases = {p["mode"]: p for p in results["phases"]} if results else {}
    web, web_ok = _click_cell(phases.get("web"))
    vortex, vortex_ok = _click_cell(phases.get("vortex"))

    return {
        "layout": name,
        "monitors": monitors,
        "app_view": "physical layout, per-monitor aware"
        if app_ok
        else "MISMATCH" + (f": {report['problems']}" if report else ""),
        "scenarios": scenarios,
        "noisy": noisy_text,
        "web": web,
        "vortex": vortex,
        "ok": app_ok and scenarios_ok and web_ok and vortex_ok,
    }


def render(rows: list[dict[str, Any]]) -> str:
    """The compatibility table as Markdown."""
    passed = sum(row["ok"] for row in rows)
    lines = [
        "## Display compatibility",
        "",
        (
            f"{passed} of {len(rows)} layouts pass. Each row is one virtual-monitor "
            "layout on a hosted Windows runner, driven by the real app."
        ),
        "",
        (
            "| layout | monitors | app sees | scenario sweep | web clicks "
            "| Vortex clicks | result |"
        ),
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['layout']} | {row['monitors']} | {row['app_view']} "
            f"| {row['scenarios']} | {row['web']} | {row['vortex']} "
            f"| {'PASS' if row['ok'] else 'FAIL'} |"
        )
    lines += [
        "",
        (
            "Scenario sweep with the runner's own desktop visible on the other "
            "monitors (report only):"
        ),
        "",
    ]
    lines += [f"- {row['layout']}: {row['noisy']}" for row in rows]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path, help="folder holding displays-*/")
    parser.add_argument("--out", type=Path, default=None, help="write the table here")
    args = parser.parse_args()

    legs = sorted(p for p in args.artifacts.glob(f"{_ARTIFACT_PREFIX}*") if p.is_dir())
    if not legs:
        print(f"No {_ARTIFACT_PREFIX}* artifacts under {args.artifacts}")
        return 1

    rows = [leg_row(leg) for leg in legs]
    text = render(rows)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    return 0 if all(row["ok"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
