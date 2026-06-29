"""Capture raw detection fixtures through the real Windows capture pipeline.

Issue #13 is about coordinate/DPI math, so a fixture must equal the array
``ButtonDetector.detect`` actually receives -- not a generic OS screenshot.
This helper builds the same ``WindowManager`` + ``ScreenCapture`` stack that
``app.py`` uses on Windows, grabs the capture region via ``ScreenCapture.capture``
(raw RGB, no annotation -- unlike ``DebugRecorder``, which only fires on a hit
and draws a box), and writes unicode-safe PNGs into the fixtures tree.

Windows-only (guarded by ``utils.platform.IS_WINDOWS``). The cross-platform
pytest harness consumes the PNGs afterward on any OS.

Usage (on the Windows machine):

    python tools/capture_fixtures.py --out tests/fixtures/detection/real
    # press <enter> to capture each frame; type 'q' then <enter> to quit.

    # optional: click the button in a preview window to record its point into
    # a cases.json stub
    python tools/capture_fixtures.py --out tests/fixtures/detection/real --label
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

# Ensure repo root is importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.platform import IS_WINDOWS  # noqa: E402


def _save_png_unicode_safe(path: Path, rgb: np.ndarray) -> None:
    """Write an RGB array to PNG via imencode+tofile (handles unicode paths)."""
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError(f"Failed to encode PNG for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))


def _label_point(rgb: np.ndarray) -> tuple[int, int] | None:
    """Open a preview; return the (x, y) the user clicks, or None if skipped."""
    clicked: list[tuple[int, int]] = []

    def on_mouse(event: int, x: int, y: int, flags: int, _param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append((x, y))

    window = "capture_fixtures: click button center, any key to confirm"
    preview = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)
    cv2.imshow(window, preview)
    cv2.waitKey(0)
    cv2.destroyWindow(window)
    return clicked[-1] if clicked else None


def _stub_case(
    image_rel: str, point: tuple[int, int] | None, geometry: dict[str, int]
) -> dict[str, object]:
    """Build a cases.json stub entry for a freshly captured frame."""
    case: dict[str, object] = {
        "image": image_rel,
        "button_type": "TODO",  # fill in: vortex/website/wabbajack/click/understood/staging
        "expect": "present",
        "tolerance_px": 40,
        "meta": {
            "resolution": f"{geometry['width']}x{geometry['height']}",
            "dpi": "TODO",
            "monitors": "TODO",
            "source": "capture_fixtures",
        },
    }
    if point is not None:
        case["point"] = [point[0], point[1]]
    return case


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("tests/fixtures/detection/real"),
        help="output directory for captured PNGs",
    )
    parser.add_argument(
        "--prefix", default="capture", help="filename prefix for captures"
    )
    parser.add_argument(
        "--label",
        action="store_true",
        help="after each capture, click the button in a preview to record its point",
    )
    parser.add_argument(
        "--force-primary",
        action="store_true",
        help="capture only the primary monitor (matches --force-primary in the app)",
    )
    args = parser.parse_args()

    if not IS_WINDOWS:
        print(
            "capture_fixtures.py only runs on Windows (needs the real capture pipeline)."
        )
        return 1

    # Imported lazily: WindowManager raises on non-Windows at import-time use.
    from services.screen_capture import ScreenCapture
    from services.window_manager import WindowManager

    monitors = WindowManager.get_all_monitors()
    capture = ScreenCapture(monitors, force_primary=args.force_primary)
    geometry = {
        "left": capture.min_x,
        "top": capture.min_y,
        "width": capture.virtual_width,
        "height": capture.virtual_height,
    }

    print(f"Monitors ({len(monitors)}):")
    for i, m in enumerate(monitors):
        print(f"  [{i}] x={m.x} y={m.y} {m.width}x{m.height}")
    print(f"Capture region: {geometry}")
    print(f"Output dir: {args.out.resolve()}")
    print(
        "Reproduce a scenario, then press <enter> to capture. Type 'q' + <enter> to quit.\n"
    )

    args.out.mkdir(parents=True, exist_ok=True)
    stub_path = args.out / "cases.stub.json"
    stubs: list[dict[str, object]] = []
    index = 0

    while True:
        cmd = input(f"[{index}] capture (enter) / quit (q): ").strip().lower()
        if cmd == "q":
            break

        rgb = capture.capture()
        filename = f"{args.prefix}_{index:03d}.png"
        path = args.out / filename
        _save_png_unicode_safe(path, rgb)
        print(f"  saved {path} ({rgb.shape[1]}x{rgb.shape[0]})")

        point = _label_point(rgb) if args.label else None
        if point is not None:
            print(f"  labeled point: {point}")
        stubs.append(_stub_case(f"real/{filename}", point, geometry))
        index += 1

    if stubs:
        stub_path.write_text(
            json.dumps({"cases": stubs}, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"\nWrote {len(stubs)} stub case(s) to {stub_path}.\n"
            "Fill in button_type / expect / meta, label any missing points, then "
            "merge the entries into tests/fixtures/detection/cases.json."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
