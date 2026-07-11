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
real pipeline produces at that scaling.
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
from utils.dpi import ensure_dpi_awareness  # noqa: E402
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


def _save_png_unicode_safe(path: Path, rgb: Any) -> None:
    """Write an RGB array to PNG via imencode+tofile (unicode-path safe)."""
    import cv2

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError(f"Failed to encode PNG for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))


def _render_and_capture(
    root: Any,
    frame: Any,
    photo_img: Any,
    fx: float,
    fy: float,
    bg_color: str,
    screen_capture: Any,
    settle: float,
) -> Any:
    """Draw the button on a borderless full-monitor window and capture it.

    Returns the captured image of the frame being rendered on.
    """
    import tkinter as tk
    from PIL import ImageTk

    win = tk.Toplevel(root)
    win.overrideredirect(True)
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
    root.update()  # process the destroy so the next window paints cleanly
    return captured.image


def _build_plan(
    types: list[ButtonType],
    frames: list[Any],
    positions: int,
    scales: int,
) -> list[dict[str, Any]]:
    """Cartesian sweep of (monitor frame, button_type, scale, position)."""
    pos = _POSITION_POOL[: max(1, positions)]
    scl = _SCALE_POOL[: max(1, scales)]
    plan: list[dict[str, Any]] = []
    for frame in frames:
        for bt in types:
            for scale in scl:
                for fx, fy in pos:
                    plan.append(
                        {
                            "frame": frame,
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


def _frame_meta(
    frame: Any, virtual_layout: list[list[int]], awareness: str
) -> dict[str, Any]:
    """Shared meta block describing the captured frame's geometry."""
    return {
        "resolution": f"{frame.width}x{frame.height}",
        "frame": {
            "left": frame.left,
            "top": frame.top,
            "width": frame.width,
            "height": frame.height,
        },
        "virtual_layout": virtual_layout,
        "dpi_awareness": awareness,
        "orientation": "portrait" if frame.height > frame.width else "landscape",
        "monitor_index": frame.index,
        "source": "scenario",
    }


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
    target_frames = capture.targets

    print(f"DPI awareness: {awareness.value}")
    print(f"Monitors ({len(capture.desktop.frames)}):")
    for frame in capture.desktop.frames:
        print(
            f"  [{frame.index}] origin=({frame.left},{frame.top}) "
            f"size={frame.width}x{frame.height}"
        )

    plan = _build_plan(types, target_frames, args.positions, args.scales)
    print(
        f"Planned {len(plan)} present scenarios + "
        f"{args.negatives * len(target_frames)} negatives. Starting...\n"
    )

    args.out.mkdir(parents=True, exist_ok=True)
    virtual_layout = [
        [f.left, f.top, f.width, f.height] for f in capture.desktop.frames
    ]

    detector = None
    if args.verify:
        from services.button_detector import ButtonDetector

        detector = ButtonDetector(args.assets)

    root = tk.Tk()
    root.withdraw()

    cases: list[dict[str, Any]] = []
    index = 0

    # --- present scenarios -------------------------------------------------
    for step in plan:
        frame = step["frame"]
        bt: ButtonType = step["button_type"]
        scale: float = step["scale"]
        fx, fy = step["fx"], step["fy"]

        pil = _load_pil(args.assets / _TEMPLATE_ASSET[bt], scale)
        bg = _BG_COLORS[index % len(_BG_COLORS)]
        img = _render_and_capture(root, frame, pil, fx, fy, bg, capture, args.settle)

        # Ground truth in frame-image px: the window covers the monitor, so a
        # button drawn at fraction (fx, fy) sits at that fraction of the frame.
        point = (int(round(fx * frame.width)), int(round(fy * frame.height)))

        # tolerance ~ half the button size + margin (Tk renders in physical
        # pixels under per-monitor awareness, so PIL size == captured size)
        pw, ph = pil.size
        tol = int(0.5 * max(pw, ph) + 25)

        filename = f"{args.prefix}_{index:03d}.png"
        _save_png_unicode_safe(args.out / filename, img)

        case = {
            "image": f"real/{filename}",
            "button_type": bt.value,
            "expect": "present",
            "point": [point[0], point[1]],
            "tolerance_px": tol,
            "meta": {
                **_frame_meta(frame, virtual_layout, awareness.value),
                "scale": scale,
                "position": [fx, fy],
            },
        }
        cases.append(case)

        note = ""
        if detector is not None:
            res = detector.detect(img, bt)
            if res is None:
                note = "  detect=MISS"
            else:
                err = ((res.x - point[0]) ** 2 + (res.y - point[1]) ** 2) ** 0.5
                hit = "ok" if err <= tol else "OFF"
                note = f"  detect={res.num_matches}m err={err:.0f}px({hit})"
        print(
            f"[{index:03d}] mon{frame.index} {bt.value:<10} "
            f"x{scale} @({fx},{fy}) -> gt={point} tol={tol}{note}"
        )
        index += 1

    # --- negative scenarios ------------------------------------------------
    neg_types = [t.value for t in types][:2] or [ButtonType.VORTEX.value]
    for frame in target_frames:
        for n in range(args.negatives):
            bg = _BG_COLORS[index % len(_BG_COLORS)]
            img = _render_and_capture(
                root, frame, None, 0.5, 0.5, bg, capture, args.settle
            )
            filename = f"{args.prefix}_{index:03d}.png"
            _save_png_unicode_safe(args.out / filename, img)
            absent_type = neg_types[n % len(neg_types)]
            cases.append(
                {
                    "image": f"real/{filename}",
                    "button_type": absent_type,
                    "expect": "absent",
                    "meta": _frame_meta(frame, virtual_layout, awareness.value),
                }
            )
            print(f"[{index:03d}] mon{frame.index} negative -> absent {absent_type}")
            index += 1

    root.destroy()
    _write_cases(cases, args)
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
