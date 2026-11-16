"""Auto-generate labeled detection scenarios on a real multi-monitor Windows box.

Unlike ``tools/capture_fixtures.py`` (interactive: you reproduce a scene and
click the button), this script *renders* each button template itself onto a
borderless full-screen window on a chosen monitor at known positions/scales,
then captures the frame through the SAME ``ScreenCapture`` pipeline ``app.py``
uses. Because the script controls where the button is drawn, ground-truth click
points are computed geometrically from the physical MSS monitor bounds -- exact,
DPI- and multi-monitor-correct, and independent of the detector.

That combination is the point: synthetic fixtures have exact labels but fake
geometry; hand-captured fixtures have real geometry but hand labels. This has
both -- real capture-pipeline geometry AND exact labels -- across many scenarios.

Windows-only (needs the real capture pipeline). Produces PNGs + a cases stub the
cross-platform pytest harness / detection_report.py then consume on any OS.

Examples:
    # default sweep: all button types, 3 positions x 2 scales, both monitors
    python tools/generate_scenarios.py --out tests/fixtures/detection/real --verify

    # only vortex + website, more positions, append straight into cases.json
    python tools/generate_scenarios.py --types vortex website --positions 5 \\
        --append-to-cases --verify

Tip: to sweep DPI, change Windows Display > Scale between runs (e.g. 100% then
150%) and pass a different --prefix each time -- each run captures whatever the
real pipeline produces at that scaling. On a virtual-display rig,
``tools/ci/set_display_layout.py`` sets the topology instead (see
``.github/workflows/windows-displays.yml``).

Label integrity: creating the ``ScreenCapture`` (mss) makes this process
per-monitor DPI aware, so Tk geometry is in physical pixels. Windows are
therefore sized from the matched *physical* MSS rect, and every capture is
checked to really show the window covering that rect (otherwise the computed
label would be wrong); a failed coverage check is never written as a case.

Determinism: by default every *other* monitor is covered with a plain window
too, so a case depends only on button, scale and DPI -- not on wallpaper,
taskbar or clock text, which otherwise attract stray keypoint matches.
``--noisy`` leaves those monitors showing the real desktop, a harder
report-only variant (CI writes it to artifacts, never to cases.json).
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

from models import ButtonType  # noqa: E402
from utils.platform import IS_WINDOWS  # noqa: E402

# Button type -> template asset rendered (the one matched in default/non-legacy
# mode, so captures reflect production behaviour).
_TEMPLATE_ASSET: dict[ButtonType, str] = {
    ButtonType.VORTEX: "VortexDownloadButtonNew.png",
    ButtonType.WEBSITE: "WebsiteDownloadButtonNew.png",
    ButtonType.WABBAJACK: "WabbajackDownloadButton.png",
    ButtonType.CLICK: "ClickHereButton.png",
    ButtonType.UNDERSTOOD: "UnderstoodButton.png",
    ButtonType.STAGING: "StagingButton.png",
}

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


def _match_physical(
    monitor: Any, screen_monitors: list[dict[str, int]]
) -> dict[str, int]:
    """Match a win32 Monitor to its physical MSS monitor entry.

    Same nearest-bounds heuristic ScreenCapture uses; reliable when monitors are
    well separated in coordinate space (always true for distinct displays), even
    when DPI scaling makes win32 logical bounds differ from physical framebuffer.
    """
    physical = screen_monitors[1:] if len(screen_monitors) > 1 else screen_monitors
    return min(
        physical,
        key=lambda m: (
            abs(m["left"] - monitor.x)
            + abs(m["top"] - monitor.y)
            + abs(m["width"] - monitor.width)
            + abs(m["height"] - monitor.height)
        ),
    )


def _ground_truth_point(
    phys: dict[str, int], fx: float, fy: float, min_x: int, min_y: int
) -> tuple[int, int]:
    """Button center in capture-image px.

    Works regardless of DPI: the window covers the monitor, so a button drawn at
    fraction (fx, fy) lands at that same fraction of the physical monitor. We then
    offset by the capture region origin to get image coordinates.
    """
    cx = phys["left"] + fx * phys["width"]
    cy = phys["top"] + fy * phys["height"]
    return int(round(cx - min_x)), int(round(cy - min_y))


def _render_and_capture(
    root: Any,
    phys: dict[str, int],
    photo_img: Any,
    fx: float,
    fy: float,
    bg_color: str,
    screen_capture: Any,
    settle: float,
    idle: list[dict[str, int]] | None = None,
) -> Any:
    """Draw the button on a borderless window covering the physical monitor rect.

    ``phys`` is the MSS rect (physical pixels). The process is per-monitor DPI
    aware once ``ScreenCapture`` exists, so Tk geometry is physical as well.
    ``idle`` monitors get a plain window in the same colour, so whatever the
    desktop shows there (wallpaper, taskbar, clock) can't reach the detector.
    """
    import tkinter as tk
    from PIL import ImageTk

    blanks = []
    for rect in idle or []:
        blank = tk.Toplevel(root)
        blank.overrideredirect(True)
        blank.attributes("-topmost", True)
        blank.geometry(f"{rect['width']}x{rect['height']}+{rect['left']}+{rect['top']}")
        blank.configure(bg=bg_color)
        blanks.append(blank)

    width, height = phys["width"], phys["height"]
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.geometry(f"{width}x{height}+{phys['left']}+{phys['top']}")

    canvas = tk.Canvas(
        win,
        width=width,
        height=height,
        highlightthickness=0,
        bg=bg_color,
    )
    canvas.pack()

    tk_photo = ImageTk.PhotoImage(photo_img) if photo_img is not None else None
    if tk_photo is not None:
        cx = int(fx * width)
        cy = int(fy * height)
        canvas.create_image(cx, cy, image=tk_photo, anchor="center")

    win.lift()
    win.update_idletasks()
    win.update()
    time.sleep(settle)  # let the compositor actually paint before grabbing

    img = screen_capture.capture()
    win.destroy()
    for blank in blanks:
        blank.destroy()
    root.update()  # process the destroy so the next window paints cleanly
    return img


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

    Proves the window really covered the physical monitor, which the ground
    truth point assumes. ``button_box`` is ``(x1, y1, x2, y2)`` in image px.
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


def _monitor_scale(phys: dict[str, int]) -> float | None:
    """Effective Windows scale (1.0 = 100 %) of a physical monitor rect."""
    try:
        from tools.win_display import dpi_scale_at

        return dpi_scale_at(
            phys["left"] + phys["width"] // 2, phys["top"] + phys["height"] // 2
        )
    except Exception:
        return None


def _build_plan(
    types: list[ButtonType],
    monitors: list[Any],
    positions: int,
    scales: int,
) -> list[dict[str, Any]]:
    """Cartesian sweep of (monitor, button_type, scale, position)."""
    pos = _POSITION_POOL[: max(1, positions)]
    scl = _SCALE_POOL[: max(1, scales)]
    plan: list[dict[str, Any]] = []
    for mon_idx, monitor in enumerate(monitors):
        for bt in types:
            for scale in scl:
                for fx, fy in pos:
                    plan.append(
                        {
                            "monitor_index": mon_idx,
                            "monitor": monitor,
                            "button_type": bt,
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
        default=[bt.value for bt in _TEMPLATE_ASSET],
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

    try:
        import tkinter as tk  # noqa: F401
        from PIL import Image, ImageTk  # noqa: F401
    except Exception as exc:  # pragma: no cover - Windows-only path
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
    types = [t for t in types if (args.assets / _TEMPLATE_ASSET[t]).exists()]
    if not types:
        print("No requested button types have template assets on this branch.")
        return 2

    monitors = WindowManager.get_all_monitors()
    capture = ScreenCapture(monitors, force_primary=args.force_primary)
    target_monitors = [monitors[0]] if args.force_primary else monitors

    print(f"Monitors ({len(monitors)}):")
    for i, m in enumerate(monitors):
        print(f"  [{i}] origin=({m.x},{m.y}) size={m.width}x{m.height}")
    print(
        f"Capture region: left={capture.min_x} top={capture.min_y} "
        f"{capture.virtual_width}x{capture.virtual_height}"
    )

    plan = _build_plan(types, target_monitors, args.positions, args.scales)
    print(
        f"Planned {len(plan)} present scenarios + "
        f"{args.negatives * len(target_monitors)} negatives. Starting...\n"
    )

    args.out.mkdir(parents=True, exist_ok=True)
    resolution = f"{capture.virtual_width}x{capture.virtual_height}"

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
    physical_list = (
        capture.screen_monitors[1:]
        if len(capture.screen_monitors) > 1
        else capture.screen_monitors
    )

    def idle_for(phys: dict[str, int]) -> list[dict[str, int]]:
        """Monitors to blank while ``phys`` shows the scenario."""
        return [] if args.noisy else [m for m in physical_list if m != phys]

    def base_meta(monitor_index: int, phys: dict[str, int]) -> dict[str, Any]:
        mon_scale = _monitor_scale(phys)
        meta: dict[str, Any] = {
            "resolution": resolution,
            "monitor_index": monitor_index,
            "monitors": len(monitors),
            "monitor_rect": [phys["left"], phys["top"], phys["width"], phys["height"]],
            "dpi_scale": round(mon_scale * 100) if mon_scale else None,
            "source": "scenario",
            "background": "noisy" if args.noisy else "blank",
        }
        if args.topology:
            meta["topology"] = args.topology
        return meta

    # --- present scenarios -------------------------------------------------
    for step in plan:
        monitor = step["monitor"]
        bt: ButtonType = step["button_type"]
        scale: float = step["scale"]
        fx, fy = step["fx"], step["fy"]

        phys = _match_physical(monitor, capture.screen_monitors)
        pil = _load_pil(args.assets / _TEMPLATE_ASSET[bt], scale)
        bg = _BG_COLORS[index % len(_BG_COLORS)]
        img = _render_and_capture(
            root, phys, pil, fx, fy, bg, capture, args.settle, idle_for(phys)
        )

        point = _ground_truth_point(phys, fx, fy, capture.min_x, capture.min_y)

        # The window is physical-sized, so the button is drawn at its PIL size.
        pw, ph = pil.size
        tol = int(0.5 * max(pw, ph) + 25)
        button_box = (
            point[0] - pw // 2 - 2,
            point[1] - ph // 2 - 2,
            point[0] + pw // 2 + 2,
            point[1] + ph // 2 + 2,
        )
        cover = _coverage(img, phys, capture.min_x, capture.min_y, bg, button_box)
        if cover < _MIN_COVERAGE:
            coverage_failures += 1
            print(
                f"[{index:03d}] mon{step['monitor_index']} COVERAGE {cover:.1%} < "
                f"{_MIN_COVERAGE:.0%}: window did not cover {phys}; case skipped"
            )
            _save_png_unicode_safe(
                args.out / f"{args.prefix}_{index:03d}_coverage_fail.png", img
            )
            index += 1
            continue

        filename = f"{args.prefix}_{index:03d}.png"
        _save_png_unicode_safe(args.out / filename, img)

        meta = base_meta(step["monitor_index"], phys)
        meta.update({"scale": scale, "position": [fx, fy]})
        case = {
            "image": f"real/{filename}",
            "button_type": bt.value,
            "expect": "present",
            "point": [point[0], point[1]],
            "tolerance_px": tol,
            "meta": meta,
        }
        cases.append(case)

        note = ""
        if detector is not None:
            res = detector.detect(img, bt)
            if res is None:
                note = "  detect=MISS"
                detect_failures += 1
            else:
                err = ((res.x - point[0]) ** 2 + (res.y - point[1]) ** 2) ** 0.5
                hit = "ok" if err <= tol else "OFF"
                if hit == "OFF":
                    detect_failures += 1
                note = f"  detect={res.num_matches}m err={err:.0f}px({hit})"
        print(
            f"[{index:03d}] mon{step['monitor_index']} {bt.value:<10} "
            f"x{scale} @({fx},{fy}) -> gt={point} tol={tol}{note}"
        )
        index += 1

    # --- negative scenarios ------------------------------------------------
    neg_types = [t.value for t in types][:2] or [ButtonType.VORTEX.value]
    for monitor_index, monitor in enumerate(target_monitors):
        phys = _match_physical(monitor, capture.screen_monitors)
        for n in range(args.negatives):
            bg = _BG_COLORS[index % len(_BG_COLORS)]
            img = _render_and_capture(
                root, phys, None, 0.5, 0.5, bg, capture, args.settle, idle_for(phys)
            )
            cover = _coverage(img, phys, capture.min_x, capture.min_y, bg, None)
            if cover < _MIN_COVERAGE:
                coverage_failures += 1
                print(
                    f"[{index:03d}] mon{monitor_index} negative COVERAGE {cover:.1%}: "
                    "case skipped"
                )
                index += 1
                continue
            filename = f"{args.prefix}_{index:03d}.png"
            _save_png_unicode_safe(args.out / filename, img)
            absent_type = neg_types[n % len(neg_types)]
            cases.append(
                {
                    "image": f"real/{filename}",
                    "button_type": absent_type,
                    "expect": "absent",
                    "meta": base_meta(monitor_index, phys),
                }
            )
            note = ""
            if detector is not None and detector.detect(img, ButtonType(absent_type)):
                note = "  detect=FALSE_POSITIVE"
                detect_failures += 1
            print(
                f"[{index:03d}] mon{monitor_index} negative -> absent {absent_type}{note}"
            )
            index += 1

    root.destroy()
    _write_cases(cases, args)

    if coverage_failures:
        print(
            f"\n{coverage_failures} capture(s) failed the coverage check: the "
            "window did not cover its physical monitor, so labels can't be trusted."
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
