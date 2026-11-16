"""Report (and optionally assert) the display state the app will run against.

Windows-only. Records, in order:

1. the app's own view, taken the way ``app.py`` takes it: DPI awareness on
   entry, the monitors ``WindowManager.get_all_monitors()`` returns, the frames
   ``ScreenCapture`` will grab, and the awareness afterwards;
2. the interactive-session check (capture and clicks need a real desktop);
3. win32 bounds, per-monitor scale, MSS monitors, GDI devices and DisplayConfig
   sources;
4. a paint test: a coloured borderless window per monitor, captured through MSS,
   proving windows render on every monitor and capture sees them.

Writes ``report.json`` + ``paint_test.png`` to ``--out``. With ``--expect`` it
exits 1 unless the desktop is interactive, the paint test passes, the MSS
monitors / scales match the topology (syntax: ``tools/ci/topology.py``), and the
app's monitors and frames are that same physical layout with the process
per-monitor DPI aware. The last part is what catches a DPI regression: an
unaware process reports a 1920x1080 monitor at 150 % as 1280x720.

    python tools/ci/display_report.py --out artifacts/report --expect "1920x1080@0,0:100"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.ci.topology import match_rects, parse_topology

# DPI_AWARENESS_PER_MONITOR_AWARE, as tools.win_display.process_dpi_awareness
# reports it.
_PER_MONITOR_AWARE = 2

_PAINT_COLORS: tuple[tuple[int, int, int], ...] = (
    (220, 40, 40),
    (40, 200, 60),
    (40, 80, 220),
    (230, 200, 30),
    (200, 40, 200),
    (30, 200, 200),
)


def _win32_monitors() -> list[list[int]]:
    import win32api

    return [list(rect) for _, _, rect in win32api.EnumDisplayMonitors(None, None)]


def _app_view() -> dict[str, Any]:
    """What app.py computes at startup. Must run before anything else here
    touches win32 or mss, so the process starts as unaware as the app does."""
    from tools.win_display import process_dpi_awareness

    awareness_before = process_dpi_awareness()

    from services.screen_capture import ScreenCapture
    from services.window_manager import WindowManager

    monitors = WindowManager.get_all_monitors()
    capture = ScreenCapture(monitors)
    return {
        "dpi_awareness_before": awareness_before,
        "app_monitors": [m.model_dump() for m in monitors],
        "app_frames": [f.model_dump() for f in capture.desktop.frames],
        "dpi_awareness_after": process_dpi_awareness(),
    }


def _paint_test(monitors: list[dict[str, int]], out_dir: Path) -> list[dict[str, Any]]:
    """Paint each monitor a solid colour and check MSS sees it at the centre."""
    import tkinter as tk

    import cv2
    import mss
    import numpy as np

    root = tk.Tk()
    root.withdraw()
    windows = []
    for i, mon in enumerate(monitors):
        r, g, b = _PAINT_COLORS[i % len(_PAINT_COLORS)]
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        win.configure(bg=f"#{r:02x}{g:02x}{b:02x}")
        windows.append(win)
    root.update()
    time.sleep(1.0)
    root.update()

    with mss.mss() as sct:
        full = sct.monitors[0]
        bgra = np.array(sct.grab(full))
    for win in windows:
        win.destroy()
    root.destroy()

    rgb = bgra[:, :, :3][:, :, ::-1]
    cv2.imwrite(str(out_dir / "paint_test.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))

    results = []
    for i, mon in enumerate(monitors):
        expected = _PAINT_COLORS[i % len(_PAINT_COLORS)]
        cx = mon["left"] + mon["width"] // 2 - full["left"]
        cy = mon["top"] + mon["height"] // 2 - full["top"]
        actual = [int(v) for v in rgb[cy, cx]]
        ok = all(abs(a - e) <= 8 for a, e in zip(actual, expected))
        results.append(
            {"monitor": i, "expected": list(expected), "actual": actual, "ok": ok}
        )
    return results


def build_report(out_dir: Path) -> dict[str, Any]:
    from tools.win_display import (
        display_devices,
        display_sources,
        dpi_scale_at,
        get_scale,
        physical_monitors,
        process_dpi_awareness,
        session_info,
    )

    report: dict[str, Any] = {"app_view": _app_view()}
    report["session"] = session_info()
    report["dpi_awareness"] = process_dpi_awareness()
    report["win32_monitors_aware"] = _win32_monitors()

    mss_monitors = physical_monitors()
    report["mss_monitors"] = mss_monitors
    report["monitor_scales"] = [
        round(
            dpi_scale_at(m["left"] + m["width"] // 2, m["top"] + m["height"] // 2) * 100
        )
        for m in mss_monitors
    ]
    report["devices"] = display_devices()
    sources = []
    for src in display_sources():
        try:
            scale: Any = get_scale(src)
        except OSError as exc:
            scale = str(exc)
        sources.append(
            {
                "gdi_name": src.gdi_name,
                "rect": [src.x, src.y, src.width, src.height],
                "scale": scale,
            }
        )
    report["sources"] = sources

    try:
        report["paint_test"] = _paint_test(mss_monitors, out_dir)
    # Recorded as a failed paint test: the report must still be written.
    except Exception as exc:  # noqa: BLE001
        report["paint_test"] = [{"ok": False, "error": repr(exc)}]
    return report


def check_app_view(app_view: dict[str, Any], expected: list[Any]) -> list[str]:
    """Problems with the app's own view of a known physical layout.

    Pure (no win32), so it is unit-tested on every platform.
    """
    problems: list[str] = []
    if app_view["dpi_awareness_after"] != _PER_MONITOR_AWARE:
        problems.append(
            f"app process DPI awareness is {app_view['dpi_awareness_after']}, "
            f"wanted {_PER_MONITOR_AWARE} (per-monitor)"
        )
    app_monitors = [
        {"left": m["x"], "top": m["y"], "width": m["width"], "height": m["height"]}
        for m in app_view["app_monitors"]
    ]
    problems.extend(
        f"app monitor list: {problem}"
        for problem in match_rects(expected, app_monitors)
    )
    problems.extend(
        f"app capture frames: {problem}"
        for problem in match_rects(expected, app_view["app_frames"])
    )
    return problems


def check(report: dict[str, Any], expect: str) -> list[str]:
    """Problems that make this display state unusable for the given topology."""
    problems: list[str] = []
    if report["session"].get("input_desktop") is None:
        problems.append(f"no interactive desktop (session {report['session']})")
    failed = [p for p in report["paint_test"] if not p.get("ok")]
    if failed:
        problems.append(f"paint test failed: {failed}")

    expected = parse_topology(expect)
    problems.extend(match_rects(expected, report["mss_monitors"]))
    problems.extend(check_app_view(report["app_view"], expected))
    for spec in expected:
        if spec.scale is None:
            continue
        for mon, scale in zip(report["mss_monitors"], report["monitor_scales"]):
            if (mon["left"], mon["top"]) == (spec.x, spec.y) and scale != spec.scale:
                problems.append(
                    f"monitor at {spec.x},{spec.y} is {scale}%, wanted {spec.scale}%"
                )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expect", help="topology the display state must match")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("display_report.py only runs on Windows.")
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    report = build_report(args.out)
    problems = check(report, args.expect) if args.expect else []
    report["expect"] = args.expect
    report["problems"] = problems

    text = json.dumps(report, indent=2)
    (args.out / "report.json").write_text(text + "\n", encoding="utf-8")
    print(text)
    if problems:
        print("\nDISPLAY CHECK FAILED:\n  " + "\n  ".join(problems))
        return 1
    print(
        "\nDisplay check OK"
        if args.expect
        else "\nReport written (no expectation given)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
