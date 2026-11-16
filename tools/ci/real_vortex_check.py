"""Does the app find the download button in a real Vortex?

Windows-only (see ``.github/workflows/real-vortex.yml``). Vortex is already
running with the rig extension (``tools/ci/real_vortex``), which opens the
free-download dialog a few seconds after start and keeps writing where Vortex
drew its buttons to ``vortex-dialog.json``. This script:

1. presses "Ignore" on Vortex's administrator warning (a hosted runner has no
   other account) and maximises the window;
2. captures every monitor through the app's own capture pipeline at a fixed
   interval and saves the raw frames;
3. runs the app's detector on each frame and compares the point it would
   click with the centre of the button as Vortex reports it. The expected
   point never comes from the detector.

Only frames taken while the button stood still count (the dialog fades in).
On one of them every other button type is searched too and must stay silent.

It never clicks: the button would open a Nexus Mods page.

    python tools/ci/real_vortex_check.py --out artifacts/real-vortex

Exit codes: 0 found on every settled frame, 1 missed or off target or another
type fired, 2 the dialog could not be shown (nothing was tested).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_VORTEX_TITLE = "Vortex"
# Vortex warns when it runs as administrator. The warning sits on top of the
# window until "Ignore" is pressed.
_ADMIN_DIALOG_TITLE = "Admin rights detected"
# TDM_CLICK_BUTTON (WM_USER + 102): presses a task dialog's button by id.
_TDM_CLICK_BUTTON = 0x0400 + 102
REPORT_NAME = "vortex-dialog.json"
# The button the app has to find: its DOM id in Vortex 2.x, else its label.
_BUTTON_ID = "download-mod-button"
_BUTTON_LABELS = ("Download manually", "Download")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_SHOWN = 2


def target_button(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """The download button among the dialog's buttons, or None if not drawn."""
    if not report:
        return None
    buttons = [b for b in report.get("buttons", []) if _has_area(b["rect"])]
    for button in buttons:
        if button.get("id") == _BUTTON_ID:
            return button
    for label in _BUTTON_LABELS:
        for button in buttons:
            if button.get("text") == label:
                return button
    return None


def why_not_shown(report: dict[str, Any] | None) -> str | None:
    """What Vortex reports instead of the download dialog, if anything.

    Old Vortex stops at a start-up check when the .NET runtime is missing,
    for example; its dialog's buttons say so better than "not shown" does.
    """
    if not report:
        return "Vortex's extension wrote no report"
    if target_button(report) is not None:
        return None
    dispatched = str(report.get("dispatched", ""))
    if dispatched.startswith("failed"):
        return f"opening the dialog {dispatched}"
    labels = [b["text"] for b in report.get("buttons", []) if b.get("text")]
    if labels:
        return "Vortex shows another dialog, with the buttons: " + ", ".join(labels)
    return None


def _has_area(rect: dict[str, float]) -> bool:
    return rect["right"] > rect["left"] and rect["bottom"] > rect["top"]


def expected_centre(
    report: dict[str, Any] | None, client_origin: tuple[int, int]
) -> tuple[float, float] | None:
    """Screen position of the button's centre, in physical pixels.

    Vortex reports rectangles in CSS pixels relative to its window's client
    area; one CSS pixel is ``devicePixelRatio`` physical ones.
    """
    button = target_button(report)
    if button is None or report is None:
        return None
    ratio = float(report["devicePixelRatio"])
    rect = button["rect"]
    return (
        client_origin[0] + (rect["left"] + rect["right"]) / 2 * ratio,
        client_origin[1] + (rect["top"] + rect["bottom"]) / 2 * ratio,
    )


def settled_ticks(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ticks whose button is where it was one tick before.

    The dialog fades and slides in; a frame taken meanwhile shows the button
    somewhere else than the report read beside it says.
    """
    settled = []
    for before, tick in zip(ticks, ticks[1:]):
        if tick["expected"] is not None and tick["expected"] == before["expected"]:
            settled.append(tick)
    return settled


def judge(
    ticks: list[dict[str, Any]],
    others_fired: list[str] | None,
    tolerance: float,
    min_frames: int,
) -> dict[str, Any]:
    """Verdict from the recorded ticks.

    ``others_fired`` lists other button types the detector reported on a
    settled frame; None when that search never ran.
    """
    settled = settled_ticks(ticks)
    if len(settled) < min_frames:
        return {
            "status": "not shown",
            "exit": EXIT_NOT_SHOWN,
            "frames": len(settled),
            "reason": f"the button stood still on {len(settled)} frames, "
            f"{min_frames} are needed",
        }

    missed = [t for t in settled if t["detected"] is None]
    errors = [
        math.hypot(
            t["detected"]["x"] - t["expected"][0],
            t["detected"]["y"] - t["expected"][1],
        )
        for t in settled
        if t["detected"] is not None
    ]
    worst = max(errors) if errors else None
    problems = []
    if missed:
        problems.append(f"not found on {len(missed)} of {len(settled)} frames")
    if worst is not None and worst > tolerance:
        problems.append(f"{worst:.1f} px from the button (limit {tolerance:g})")
    if others_fired is None:
        problems.append("the other button types were never searched")
    elif others_fired:
        problems.append("also fired: " + ", ".join(others_fired))

    result = {"frames": len(settled), "worst_px": worst}
    if problems:
        return {
            **result,
            "status": "fail",
            "exit": EXIT_FAILED,
            "reason": "; ".join(problems),
        }
    return {
        **result,
        "status": "ok",
        "exit": EXIT_OK,
        "reason": f"found on {len(settled)} of {len(settled)} frames, at most "
        f"{worst:.1f} px from the button's centre",
    }


def summary_line(label: str, verdict: dict[str, Any]) -> str:
    """One line for the job summary."""
    return f"Real Vortex ({label}): {verdict['status']} ({verdict['reason']})"


# --------------------------------------------------------------------------
# Windows side
# --------------------------------------------------------------------------


def _vortex_window() -> int:
    """Handle of Vortex's main window (the largest visible one), or 0."""
    import win32gui

    best: tuple[int, int] = (0, 0)

    def visit(handle: int, _extra: Any) -> bool:
        nonlocal best
        if (
            win32gui.IsWindowVisible(handle)
            and win32gui.GetWindowText(handle) == _VORTEX_TITLE
        ):
            left, top, right, bottom = win32gui.GetWindowRect(handle)
            area = (right - left) * (bottom - top)
            if area > best[1]:
                best = (handle, area)
        return True

    win32gui.EnumWindows(visit, None)
    return best[0]


def _dismiss_admin_dialog() -> str | None:
    """Press "Ignore" on Vortex's administrator warning; what was done, if any."""
    import win32con
    import win32gui

    handle = win32gui.FindWindow(None, _ADMIN_DIALOG_TITLE)
    if not handle:
        return None

    children: list[tuple[int, str, str]] = []

    def visit(child: int, _extra: Any) -> bool:
        children.append(
            (child, win32gui.GetClassName(child), win32gui.GetWindowText(child))
        )
        return True

    try:
        win32gui.EnumChildWindows(handle, visit, None)
    except Exception as error:  # noqa: BLE001 - raised when there are no children
        print(f"  admin dialog: could not list its controls: {error}")

    for child, class_name, text in children:
        if class_name == "Button" and "ignore" in text.replace("&", "").lower():
            win32gui.PostMessage(child, win32con.BM_CLICK, 0, 0)
            return f"pressed the {text!r} button"

    # The buttons are not always created yet: ask the task dialog itself, by
    # button id. Electron numbers a message box's buttons from 100; "Ignore"
    # is the second one.
    win32gui.PostMessage(handle, _TDM_CLICK_BUTTON, 101, 0)
    return "sent TDM_CLICK_BUTTON 101"


def _read_report(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/real-vortex"))
    parser.add_argument("--seconds", type=float, default=110.0)
    parser.add_argument("--every", type=float, default=5.0)
    parser.add_argument(
        "--tolerance",
        type=float,
        default=4.0,
        help="largest distance in px between the click point and the button's centre",
    )
    parser.add_argument(
        "--min-frames",
        type=int,
        default=5,
        help="settled frames needed before anything counts as tested",
    )
    parser.add_argument("--label", default="", help="shown in the summary")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("real_vortex_check.py only runs on Windows.")
        return EXIT_FAILED

    import cv2
    import win32con
    import win32gui

    from models import AppConfig, ButtonType
    from services.button_detector import ButtonDetector
    from services.screen_capture import ScreenCapture
    from services.window_manager import WindowManager
    from utils.dpi import ensure_dpi_awareness

    awareness = ensure_dpi_awareness()
    print(f"DPI awareness: {awareness.value}")

    args.out.mkdir(parents=True, exist_ok=True)
    frames_dir = args.out / "frames"
    frames_dir.mkdir(exist_ok=True)
    report_path = args.out / REPORT_NAME

    # Dismissed as soon as it appears, before the first capture.
    dismissals: list[str] = []
    waited = time.monotonic()
    while time.monotonic() - waited < 45:
        done = _dismiss_admin_dialog()
        if done:
            dismissals.append(done)
            print(f"admin dialog: {done}")
            time.sleep(2.0)
            if not win32gui.FindWindow(None, _ADMIN_DIALOG_TITLE):
                break
        time.sleep(0.5)

    capture = ScreenCapture(WindowManager.get_all_monitors())
    detector = ButtonDetector(ROOT / "assets")
    defaults = AppConfig()

    def detect(image: Any, button_type: ButtonType) -> Any:
        return detector.detect(
            image,
            button_type,
            min_matches=defaults.min_matches,
            ratio=defaults.ratio_threshold,
        )

    ticks: list[dict[str, Any]] = []
    others_fired: list[str] = []
    others_checked = False
    maximised = 0  # the window handle that was maximised
    started = time.monotonic()
    while time.monotonic() - started <= args.seconds:
        tick_started = time.monotonic()
        done = _dismiss_admin_dialog()
        if done:
            dismissals.append(done)
            time.sleep(1.0)

        # Vortex shows a splash window under the same title first, so the
        # handle changes once and can go stale between two calls.
        handle = _vortex_window()
        origin = None
        try:
            if handle and handle != maximised:
                win32gui.ShowWindow(handle, win32con.SW_MAXIMIZE)
                maximised = handle
                time.sleep(1.5)  # let the window repaint at its new size
            if handle:
                origin = win32gui.ClientToScreen(handle, (0, 0))
        except win32gui.error as error:
            print(f"  Vortex window went away mid-tick: {error}")
            handle = 0

        report = _read_report(report_path)
        expected = expected_centre(report, origin) if origin else None
        tick: dict[str, Any] = {
            "seconds": round(tick_started - started, 1),
            "window": bool(handle),
            "client_origin": list(origin) if origin else None,
            "expected": [round(v, 1) for v in expected] if expected else None,
            "detected": None,
            "frames": [],
        }

        target_image = None
        for captured in capture.capture_frames():
            frame = captured.frame
            name = f"{round(tick['seconds']):04d}_m{frame.index}.png"
            cv2.imwrite(
                str(frames_dir / name),
                cv2.cvtColor(captured.image, cv2.COLOR_RGB2BGR),
            )
            tick["frames"].append(name)
            result = detect(captured.image, ButtonType.VORTEX)
            if result is not None and tick["detected"] is None:
                tick["detected"] = {
                    "x": frame.left + result.x,
                    "y": frame.top + result.y,
                    "scale": result.scale,
                    "method": result.method,
                    "confidence": result.confidence,
                }
            if expected and frame.left <= expected[0] < frame.right:
                target_image = captured.image
        ticks.append(tick)

        # Once the button stands still: nothing else may fire on that frame.
        settled = settled_ticks(ticks)
        if (
            not others_checked
            and target_image is not None
            and settled
            and settled[-1] is tick
        ):
            others_checked = True
            for other in ButtonType:
                if other is not ButtonType.VORTEX and detect(target_image, other):
                    others_fired.append(other.value)

        detected = tick["detected"]
        print(
            f"t={tick['seconds']:6.1f}s  window: {'yes' if handle else 'no '}  "
            f"button at: {tick['expected']}  detector: "
            + (f"({detected['x']}, {detected['y']})" if detected else "nothing")
        )
        time.sleep(max(0.0, args.every - (time.monotonic() - tick_started)))

    verdict = judge(
        ticks,
        others_fired if others_checked else None,
        args.tolerance,
        args.min_frames,
    )
    last_report = _read_report(report_path)
    if verdict["exit"] == EXIT_NOT_SHOWN:
        why = why_not_shown(last_report)
        if why:
            verdict["reason"] += f"; {why}"
    summary = {
        "label": args.label,
        "awareness": awareness.value,
        "admin_dialog": dismissals,
        "vortex_report": last_report,
        "others_checked": others_checked,
        "others_fired": others_fired,
        "verdict": verdict,
        "ticks": ticks,
    }
    (args.out / "results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    line = summary_line(args.label, verdict)
    print(f"\n{line}")
    if os.environ.get("GITHUB_ACTIONS"):
        # An annotation: readable from the run page and the API without
        # downloading the frames.
        level = "notice" if verdict["exit"] == EXIT_OK else "error"
        print(f"::{level} title=Real Vortex::{line}")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle_out:
            handle_out.write(line + "\n")
    return int(verdict["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
