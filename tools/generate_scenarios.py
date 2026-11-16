"""Auto-generate labeled detection scenarios on a real multi-monitor Windows box.

Unlike ``tools/capture_fixtures.py`` (interactive: you reproduce a scene and
click the button), this script *renders* each button template itself onto a
borderless full-screen window on a chosen monitor at known positions/scales,
then captures the frame through the SAME ``ScreenCapture`` pipeline ``app.py``
uses. Because the script controls where the button is drawn, ground-truth click
points are computed geometrically from the frame's own bounds -- exact, DPI-
and multi-monitor-correct, and independent of the detector.

That combination is the point: synthetic fixtures have exact labels but fake
geometry; hand-captured fixtures have real geometry but hand labels. This has
both -- real capture-pipeline geometry AND exact labels -- across many scenarios.

The process declares per-monitor DPI awareness first (exactly like ``main.py``),
so Tk window geometry, win32 monitor bounds and mss captures all agree in
physical pixels; ground truth stays exact at any Windows display scale.

Windows-only (needs the real capture pipeline). Produces PNGs + a cases stub the
cross-platform pytest harness / detection_report.py then consume on any OS.

Examples:
    # default sweep: all button types, 3 positions x 2 scales, all monitors
    python tools/generate_scenarios.py --out tests/fixtures/detection/real --verify

    # only vortex + website, more positions, append straight into cases.json
    python tools/generate_scenarios.py --types vortex website --positions 5 \\
        --append-to-cases --verify

Tip: to sweep DPI, change Windows Display > Scale between runs (e.g. 100% then
150%) and pass a different --prefix each time -- each run captures whatever the
real pipeline produces at that scaling. On a virtual-display rig,
``tools/ci/set_display_layout.py`` sets the topology instead (see
``.github/workflows/windows-displays.yml``).

Label integrity: every capture is checked to really show the window covering
its monitor (otherwise the computed label would be wrong); a capture that fails
the coverage check is never written as a case.

Determinism: by default every *other* monitor is covered with a plain window
too, so a case depends only on button, scale and DPI -- not on wallpaper,
taskbar or clock text. ``--noisy`` leaves those monitors showing the real
desktop, a harder report-only variant (CI writes it to artifacts, never to
cases.json).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# Repo root importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import ButtonType
from utils.dpi import ensure_dpi_awareness
from utils.platform import IS_WINDOWS

# Every picture the app ships of a button: (type, asset, UI generation). The
# detector tries all pictures of a type, so the sweep renders all of them.
_TEMPLATES: tuple[tuple[ButtonType, str, str], ...] = (
    (ButtonType.VORTEX, "VortexDownloadButtonNew.png", "new"),
    (ButtonType.VORTEX, "VortexDownloadButton.png", "legacy"),
    (ButtonType.VORTEX, "VortexDownloadButtonLegacy100.png", "legacy"),
    (ButtonType.WEBSITE, "WebsiteDownloadButtonNew.png", "new"),
    (ButtonType.WEBSITE, "WebsiteDownloadButton.png", "legacy"),
    (ButtonType.WABBAJACK, "WabbajackDownloadButton.png", "new"),
    (ButtonType.CLICK, "ClickHereButton.png", "legacy"),
    (ButtonType.UNDERSTOOD, "UnderstoodButton.png", "legacy"),
    (ButtonType.STAGING, "StagingButton.png", "legacy"),
)

# Background colours cycled across scenarios (Tk colour names). Solid is fine --
# the detector only needs the button's own features.
_BG_COLORS = ("#1e1e2e", "#3b3b3b", "#0a3d62", "#2d3436", "#4a235a")

# Fractional button-center positions within a monitor, ordered so the first N
# give a sensible spread (center first, then a ring of corners).
_POSITION_POOL: tuple[tuple[float, float], ...] = (
    (0.50, 0.50),
    (0.25, 0.25),
    (0.75, 0.25),
    (0.25, 0.75),
    (0.75, 0.75),
    (0.50, 0.20),
    (0.20, 0.50),
    (0.80, 0.50),
    (0.50, 0.80),
)

_SCALE_POOL: tuple[float, ...] = (1.0, 1.25, 1.5, 0.85)

# Allowed distance between the detected and the drawn button centre. The drawn
# centre is known exactly, so this only has to absorb Tk's integer placement.
_TOLERANCE_PX = 10

# The scanner asks for 6 matches on every button except Vortex, which uses the
# configurable default; cases record the same so the report mirrors production.
_SCANNER_MIN_MATCHES = 6

# Share of a monitor's non-button pixels that must show the window background
# for the label to be trusted. A visible taskbar costs ~5 %; a window sized in
# the wrong DPI space misses a third or more of the monitor.
_MIN_COVERAGE = 0.90

# Exit codes beyond argument errors.
_EXIT_COVERAGE = 3  # a window did not cover its monitor: labels would be wrong
_EXIT_STRICT = 4  # --strict and the detector missed / was off on some case


def _save_png_unicode_safe(path: Path, rgb: Any) -> None:
    """Write an RGB array to PNG via imencode+tofile (unicode-path safe)."""
    import cv2

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError(f"Failed to encode PNG for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))


def _min_matches_for(button_type: ButtonType) -> int | None:
    """``min_matches`` the scanner passes for this type (None = app default)."""
    return None if button_type is ButtonType.VORTEX else _SCANNER_MIN_MATCHES


def _render_and_capture(
    root: Any,
    frame: Any,
    photo_img: Any,
    fx: float,
    fy: float,
    bg_color: str,
    screen_capture: Any,
    settle: float,
    idle: list[Any] | None = None,
) -> Any:
    """Draw the button on a borderless full-monitor window and capture it.

    ``idle`` frames get a plain window in the same colour, so whatever the
    desktop shows there (wallpaper, taskbar, clock) can't reach the detector.
    Returns the captured image of the frame being rendered on.
    """
    import tkinter as tk

    from PIL import ImageTk

    blanks = []
    for other in idle or []:
        blank = tk.Toplevel(root)
        blank.overrideredirect(True)
        blank.attributes("-topmost", True)
        blank.geometry(f"{other.width}x{other.height}+{other.left}+{other.top}")
        blank.configure(bg=bg_color)
        blanks.append(blank)

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.geometry(f"{frame.width}x{frame.height}+{frame.left}+{frame.top}")

    canvas = tk.Canvas(
        win,
        width=frame.width,
        height=frame.height,
        highlightthickness=0,
        bg=bg_color,
    )
    canvas.pack()

    tk_photo = ImageTk.PhotoImage(photo_img) if photo_img is not None else None
    if tk_photo is not None:
        cx = int(fx * frame.width)
        cy = int(fy * frame.height)
        canvas.create_image(cx, cy, image=tk_photo, anchor="center")

    win.lift()
    win.update_idletasks()
    win.update()
    time.sleep(settle)  # let the compositor actually paint before grabbing

    captured = next(
        c for c in screen_capture.capture_frames() if c.frame.index == frame.index
    )
    win.destroy()
    for blank in blanks:
        blank.destroy()
    root.update()  # process the destroy so the next window paints cleanly
    return captured.image


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _coverage(
    img: Any,
    phys: dict[str, int],
    min_x: int,
    min_y: int,
    bg_color: str,
    button_box: tuple[int, int, int, int] | None,
) -> float:
    """Share of the monitor rect (minus the button) showing the window background.

    Proves the window really covered the monitor, which the ground truth point
    assumes. ``phys`` is the monitor rect and ``(min_x, min_y)`` the origin of
    ``img`` in the same coordinates; for a per-monitor frame that is the rect
    ``(0, 0, width, height)`` with origin ``(0, 0)``. ``button_box`` is
    ``(x1, y1, x2, y2)`` in image px.
    """
    import numpy as np

    x1, y1 = phys["left"] - min_x, phys["top"] - min_y
    x2, y2 = x1 + phys["width"], y1 + phys["height"]
    region = img[max(0, y1) : max(0, y2), max(0, x1) : max(0, x2)].astype(np.int16)
    if region.size == 0:
        return 0.0

    target = np.array(_hex_to_rgb(bg_color), dtype=np.int16)
    matches = np.all(np.abs(region - target) <= 6, axis=2)
    mask = np.ones(matches.shape, dtype=bool)
    if button_box is not None:
        bx1, by1, bx2, by2 = button_box
        mask[
            max(0, by1 - y1) : max(0, by2 - y1), max(0, bx1 - x1) : max(0, bx2 - x1)
        ] = False
    considered = int(mask.sum())
    return float((matches & mask).sum()) / considered if considered else 0.0


def _frame_coverage(
    img: Any, frame: Any, bg_color: str, button_box: tuple[int, int, int, int] | None
) -> float:
    """``_coverage`` for a per-monitor frame image."""
    rect = {"left": 0, "top": 0, "width": frame.width, "height": frame.height}
    return _coverage(img, rect, 0, 0, bg_color, button_box)


def _monitor_scale(frame: Any) -> float | None:
    """Effective Windows scale (1.0 = 100 %) of a monitor frame."""
    try:
        from tools.win_display import dpi_scale_at

        return dpi_scale_at(
            frame.left + frame.width // 2, frame.top + frame.height // 2
        )
    # Only decorates the case meta; never worth failing a capture run over.
    except Exception:  # noqa: BLE001
        return None


# Pictures the app only looks for at their own size or smaller: too small to
# be found enlarged (see ``tests/_generate_synthetic.py``).
_MAX_SCALE: dict[str, float] = {"VortexDownloadButtonLegacy100.png": 1.0}


def _build_plan(
    templates: list[tuple[ButtonType, str, str]],
    frames: list[Any],
    positions: int,
    scales: int,
) -> list[dict[str, Any]]:
    """Cartesian sweep of (monitor frame, template, scale, position)."""
    pos = _POSITION_POOL[: max(1, positions)]
    scl = _SCALE_POOL[: max(1, scales)]
    plan: list[dict[str, Any]] = []
    for frame in frames:
        for bt, asset, generation in templates:
            for scale in scl:
                if scale > _MAX_SCALE.get(asset, scale):
                    continue
                for fx, fy in pos:
                    plan.append(
                        {
                            "frame": frame,
                            "button_type": bt,
                            "asset": asset,
                            "generation": generation,
                            "scale": scale,
                            "fx": fx,
                            "fy": fy,
                        }
                    )
    return plan


def _load_pil(asset_path: Path, scale: float) -> Any:
    """Load + scale an asset as a PIL image for Tk display."""
    from PIL import Image

    img = Image.open(asset_path).convert("RGB")
    if scale != 1.0:
        w, h = img.size
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS
        )
    return img


def _frame_meta(
    frame: Any,
    virtual_layout: list[list[int]],
    awareness: str,
    noisy: bool,
    topology: str | None,
) -> dict[str, Any]:
    """Shared meta block describing the captured frame's geometry."""
    mon_scale = _monitor_scale(frame)
    meta: dict[str, Any] = {
        "resolution": f"{frame.width}x{frame.height}",
        "frame": {
            "left": frame.left,
            "top": frame.top,
            "width": frame.width,
            "height": frame.height,
        },
        "virtual_layout": virtual_layout,
        "dpi_awareness": awareness,
        "dpi_scale": round(mon_scale * 100) if mon_scale else None,
        "orientation": "portrait" if frame.height > frame.width else "landscape",
        "monitor_index": frame.index,
        "background": "noisy" if noisy else "blank",
        "source": "scenario",
    }
    if topology:
        meta["topology"] = topology
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out", type=Path, default=Path("tests/fixtures/detection/real")
    )
    parser.add_argument("--prefix", default="scn", help="captured-file name prefix")
    parser.add_argument(
        "--types",
        nargs="*",
        default=list(dict.fromkeys(bt.value for bt, _, _ in _TEMPLATES)),
        help="button types to render (default: all)",
    )
    parser.add_argument(
        "--positions", type=int, default=3, help="positions per type (1-9, default 3)"
    )
    parser.add_argument(
        "--scales", type=int, default=2, help="scales per type (1-4, default 2)"
    )
    parser.add_argument(
        "--negatives", type=int, default=2, help="button-free frames per monitor"
    )
    parser.add_argument(
        "--force-primary",
        action="store_true",
        help="capture only the primary monitor (matches app --force-primary)",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=0.35,
        help="seconds to wait after rendering before capture",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="run the detector on each capture and report match count + error",
    )
    parser.add_argument(
        "--append-to-cases",
        action="store_true",
        help="merge results into cases.json (replacing prior 'scenario' entries) "
        "instead of writing a standalone stub",
    )
    parser.add_argument("--assets", type=Path, default=Path("assets"))
    parser.add_argument(
        "--strict",
        action="store_true",
        help=f"with --verify: exit {_EXIT_STRICT} if any case is MISS/OFF (for CI)",
    )
    parser.add_argument(
        "--topology",
        default=None,
        help="topology label recorded in each case's meta (e.g. the CI matrix spec)",
    )
    parser.add_argument(
        "--noisy",
        action="store_true",
        help="leave other monitors showing the desktop instead of blanking them",
    )
    args = parser.parse_args()

    if not IS_WINDOWS:
        print(
            "generate_scenarios.py only runs on Windows (needs the real capture "
            "pipeline + win32 monitor geometry)."
        )
        return 1

    # Before Tk/win32/mss touch anything, exactly like main.py: with per-monitor
    # awareness, Tk geometry strings are physical pixels and ground truth from
    # frame fractions stays exact at any display scale.
    awareness = ensure_dpi_awareness()

    try:
        import tkinter as tk

        from PIL import Image, ImageTk  # noqa: F401
    except ImportError as exc:  # pragma: no cover - Windows-only path
        print(f"Missing GUI deps (tkinter / Pillow): {exc}")
        return 1

    from services.screen_capture import ScreenCapture
    from services.window_manager import WindowManager

    # Resolve requested button types up front.
    try:
        types = [ButtonType(t) for t in args.types]
    except ValueError as exc:
        print(f"Unknown button type: {exc}")
        return 2
    templates = [
        t for t in _TEMPLATES if t[0] in types and (args.assets / t[1]).exists()
    ]
    types = list(dict.fromkeys(t[0] for t in templates))
    if not types:
        print("No requested button types have template assets on this branch.")
        return 2

    monitors = WindowManager.get_all_monitors()
    capture = ScreenCapture(monitors, force_primary=args.force_primary)
    target_frames = capture.targets
    all_frames = capture.desktop.frames

    print(f"DPI awareness: {awareness.value}")
    print(f"Monitors ({len(all_frames)}):")
    for frame in all_frames:
        print(
            f"  [{frame.index}] origin=({frame.left},{frame.top}) "
            f"size={frame.width}x{frame.height}"
        )

    plan = _build_plan(templates, target_frames, args.positions, args.scales)
    print(
        f"Planned {len(plan)} present scenarios + "
        f"{args.negatives * len(target_frames)} negative frames. Starting...\n"
    )

    args.out.mkdir(parents=True, exist_ok=True)
    virtual_layout = [[f.left, f.top, f.width, f.height] for f in all_frames]

    detector = None
    if args.verify:
        from services.button_detector import ButtonDetector

        detector = ButtonDetector(args.assets)

    root = tk.Tk()
    root.withdraw()

    cases: list[dict[str, Any]] = []
    index = 0
    coverage_failures = 0
    detect_failures = 0
    # Machine-readable outcome of --verify, for the cross-layout report.
    tally: dict[str, Any] = {
        "present": 0,
        "hit": 0,
        "missed": 0,
        "off": 0,
        "negative_frames": 0,
        "false_positives": 0,
        "max_error_px": 0.0,
        "tolerance_px": _TOLERANCE_PX,
    }

    def idle_for(frame: Any) -> list[Any]:
        """Monitors to blank while ``frame`` shows the scenario."""
        return [] if args.noisy else [f for f in all_frames if f != frame]

    def meta_for(frame: Any) -> dict[str, Any]:
        return _frame_meta(
            frame, virtual_layout, awareness.value, args.noisy, args.topology
        )

    def detect(img: Any, button_type: ButtonType) -> Any:
        min_matches = _min_matches_for(button_type)
        if min_matches is None:
            return detector.detect(img, button_type)
        return detector.detect(img, button_type, min_matches=min_matches)

    # --- present scenarios -------------------------------------------------
    for step in plan:
        frame = step["frame"]
        bt: ButtonType = step["button_type"]
        scale: float = step["scale"]
        fx, fy = step["fx"], step["fy"]

        pil = _load_pil(args.assets / step["asset"], scale)
        bg = _BG_COLORS[index % len(_BG_COLORS)]
        img = _render_and_capture(
            root, frame, pil, fx, fy, bg, capture, args.settle, idle_for(frame)
        )

        # Ground truth in frame-image px: the window covers the monitor, so a
        # button drawn at fraction (fx, fy) sits at that fraction of the frame.
        # Tk renders in physical pixels under per-monitor awareness, so the
        # button is drawn at its PIL size.
        point = (round(fx * frame.width), round(fy * frame.height))
        pw, ph = pil.size
        button_box = (
            point[0] - pw // 2 - 2,
            point[1] - ph // 2 - 2,
            point[0] + pw // 2 + 2,
            point[1] + ph // 2 + 2,
        )
        cover = _frame_coverage(img, frame, bg, button_box)
        if cover < _MIN_COVERAGE:
            coverage_failures += 1
            print(
                f"[{index:03d}] mon{frame.index} COVERAGE {cover:.1%} < "
                f"{_MIN_COVERAGE:.0%}: window did not cover the monitor; case skipped"
            )
            _save_png_unicode_safe(
                args.out / f"{args.prefix}_{index:03d}_coverage_fail.png", img
            )
            index += 1
            continue

        filename = f"{args.prefix}_{index:03d}.png"
        _save_png_unicode_safe(args.out / filename, img)

        case: dict[str, Any] = {
            "image": f"real/{filename}",
            "button_type": bt.value,
            "expect": "present",
            "point": [point[0], point[1]],
            "tolerance_px": _TOLERANCE_PX,
            "meta": {
                **meta_for(frame),
                "scale": scale,
                "position": [fx, fy],
                "asset": step["asset"],
                "ui_generation": step["generation"],
            },
        }
        min_matches = _min_matches_for(bt)
        if min_matches is not None:
            case["min_matches"] = min_matches
        cases.append(case)
        tally["present"] += 1

        note = ""
        if detector is not None:
            res = detect(img, bt)
            if res is None:
                note = "  detect=MISS"
                detect_failures += 1
                tally["missed"] += 1
            else:
                err = ((res.x - point[0]) ** 2 + (res.y - point[1]) ** 2) ** 0.5
                hit = "ok" if err <= _TOLERANCE_PX else "OFF"
                if hit == "OFF":
                    detect_failures += 1
                    tally["off"] += 1
                else:
                    tally["hit"] += 1
                    tally["max_error_px"] = round(max(tally["max_error_px"], err), 1)
                note = (
                    f"  detect={res.method} {res.num_matches}m err={err:.1f}px({hit})"
                )
        print(
            f"[{index:03d}] mon{frame.index} {step['asset']:<30} "
            f"x{scale} @({fx},{fy}) -> gt={point}{note}"
        )
        index += 1

    # --- negative scenarios ------------------------------------------------
    # Every button type is checked on every button-free frame: a false positive
    # of any type is a stray click in production.
    for frame in target_frames:
        for _ in range(args.negatives):
            bg = _BG_COLORS[index % len(_BG_COLORS)]
            img = _render_and_capture(
                root, frame, None, 0.5, 0.5, bg, capture, args.settle, idle_for(frame)
            )
            cover = _frame_coverage(img, frame, bg, None)
            if cover < _MIN_COVERAGE:
                coverage_failures += 1
                print(
                    f"[{index:03d}] mon{frame.index} negative COVERAGE {cover:.1%}: "
                    "case skipped"
                )
                index += 1
                continue
            filename = f"{args.prefix}_{index:03d}.png"
            _save_png_unicode_safe(args.out / filename, img)

            false_positives: list[str] = []
            for absent_type in types:
                case = {
                    "image": f"real/{filename}",
                    "button_type": absent_type.value,
                    "expect": "absent",
                    "meta": meta_for(frame),
                }
                min_matches = _min_matches_for(absent_type)
                if min_matches is not None:
                    case["min_matches"] = min_matches
                cases.append(case)
                if detector is not None and detect(img, absent_type) is not None:
                    false_positives.append(absent_type.value)

            detect_failures += len(false_positives)
            tally["negative_frames"] += 1
            tally["false_positives"] += len(false_positives)
            note = (
                f"  detect=FALSE_POSITIVE({', '.join(false_positives)})"
                if false_positives
                else ""
            )
            print(f"[{index:03d}] mon{frame.index} negative{note}")
            index += 1

    root.destroy()
    _write_cases(cases, args)
    if detector is not None:
        tally["coverage_failures"] = coverage_failures
        tally["button_types"] = len(types)
        (args.out / f"{args.prefix}.verify.json").write_text(
            json.dumps(tally, indent=2) + "\n", encoding="utf-8"
        )

    if coverage_failures:
        print(
            f"\n{coverage_failures} capture(s) failed the coverage check: the "
            "window did not cover its monitor, so labels can't be trusted."
        )
        return _EXIT_COVERAGE
    if args.strict and detect_failures:
        print(f"\n--strict: {detect_failures} case(s) missed, off or false positive.")
        return _EXIT_STRICT
    return 0


def _write_cases(cases: list[dict[str, Any]], args: argparse.Namespace) -> None:
    """Persist generated cases, either standalone or merged into cases.json."""
    if args.append_to_cases:
        from tests.harness import CASES_PATH

        existing: list[dict[str, Any]] = []
        if CASES_PATH.exists():
            raw = json.loads(CASES_PATH.read_text(encoding="utf-8"))
            existing = raw.get("cases", []) if isinstance(raw, dict) else raw
        kept = [c for c in existing if c.get("meta", {}).get("source") != "scenario"]
        merged = kept + cases
        CASES_PATH.write_text(
            json.dumps({"cases": merged}, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"\nMerged {len(cases)} scenario cases into {CASES_PATH} "
            f"({len(merged)} total). Run: pytest -q"
        )
        return

    stub_path = args.out / "scenarios.cases.json"
    stub_path.write_text(
        json.dumps({"cases": cases}, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"\nWrote {len(cases)} cases to {stub_path}.\n"
        "Review, then merge its entries into tests/fixtures/detection/cases.json "
        "(or rerun with --append-to-cases). Then: pytest -q"
    )


if __name__ == "__main__":
    raise SystemExit(main())
