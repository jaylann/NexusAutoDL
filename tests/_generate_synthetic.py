"""Generate deterministic synthetic detection fixtures.

Composites each button template onto gradient backgrounds at known (x, y)
positions and a few scales (proxying DPI scaling), then writes the images plus
matching ``cases.json`` entries with exact ground-truth points. Backgrounds are
deterministic gradients (no RNG), so output is byte-stable and CI reproducible.

The asset composited for VORTEX/WEBSITE is the *New* template -- the one the
detector actually matches against in default (non-legacy) mode -- so synthetic
matches reflect production behaviour rather than the legacy fallback.

Run from the repo root:

    python -m tests._generate_synthetic
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from models import ButtonType
from tests.harness import ASSETS_PATH, CASES_PATH, FIXTURES_DIR, read_rgb

SYNTHETIC_DIR = FIXTURES_DIR / "synthetic"

# Button type -> template asset to composite (the one matched in default mode).
_TEMPLATE_ASSET: dict[ButtonType, str] = {
    ButtonType.VORTEX: "VortexDownloadButtonNew.png",
    ButtonType.WEBSITE: "WebsiteDownloadButtonNew.png",
    ButtonType.WABBAJACK: "WabbajackDownloadButton.png",
    ButtonType.CLICK: "ClickHereButton.png",
    ButtonType.UNDERSTOOD: "UnderstoodButton.png",
    ButtonType.STAGING: "StagingButton.png",
}

# Scale factors proxy DPI scaling. We stay >= 1.0 because downscaling small
# templates (e.g. UnderstoodButton) below their native size drops SIFT features
# under min_matches -- high-DPI rendering enlarges buttons anyway, so upscaling
# is the realistic direction to exercise here.
_SCALES = (1.0, 1.25, 1.5)
_CANVAS = (1280, 720)
_PASTE_XY = (480, 300)


def _save_rgb(path: Path, rgb: np.ndarray) -> None:
    """Write an RGB array to PNG (cv2 expects BGR on disk)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def _background(variant: int) -> np.ndarray:
    """Smooth diagonal gradient background.

    Gradients compress to tiny PNGs (unlike per-pixel noise) and are corner-free,
    so SIFT finds no button-like keypoints on them -- present-cases match only the
    pasted button, and negative cases stay false-positive-free. ``variant`` picks
    a deterministic colour ramp so frames differ without randomness.
    """
    w, h = _CANVAS
    xs = np.linspace(0, 1, w, dtype=np.float32)
    ys = np.linspace(0, 1, h, dtype=np.float32)
    ramp = (xs[None, :] + ys[:, None]) / 2.0  # 0..1 diagonal
    low, high = (40, 90) if variant % 2 == 0 else (70, 130)
    grey = (low + ramp * (high - low)).astype(np.uint8)
    return np.repeat(grey[:, :, None], 3, axis=2)


def _composite(
    background: np.ndarray, button: np.ndarray, scale: float
) -> tuple[np.ndarray, tuple[int, int], tuple[int, int]]:
    """Paste ``button`` (scaled) onto a copy of ``background``.

    Returns the image, the button center point, and its (w, h) at this scale.
    """
    bh, bw = button.shape[:2]
    sw, sh = max(1, int(bw * scale)), max(1, int(bh * scale))
    resized = cv2.resize(button, (sw, sh), interpolation=cv2.INTER_AREA)

    canvas = background.copy()
    x, y = _PASTE_XY
    canvas[y : y + sh, x : x + sw] = resized
    center = (x + sw // 2, y + sh // 2)
    return canvas, center, (sw, sh)


def _present_case(
    image_rel: str,
    button_type: ButtonType,
    center: tuple[int, int],
    size: tuple[int, int],
    scale: float,
) -> dict[str, Any]:
    sw, sh = size
    # Centroid of matched keypoints clusters inside the button; allow ~half the
    # larger dimension plus a small margin.
    tolerance = int(0.5 * max(sw, sh) + 15)
    return {
        "image": image_rel,
        "button_type": button_type.value,
        "expect": "present",
        "point": [center[0], center[1]],
        "tolerance_px": tolerance,
        "meta": {
            "resolution": f"{_CANVAS[0]}x{_CANVAS[1]}",
            "scale": scale,
            "source": "synthetic",
        },
    }


def _absent_case(image_rel: str, button_type: ButtonType) -> dict[str, Any]:
    return {
        "image": image_rel,
        "button_type": button_type.value,
        "expect": "absent",
        "meta": {
            "resolution": f"{_CANVAS[0]}x{_CANVAS[1]}",
            "source": "synthetic",
        },
    }


def generate() -> list[dict[str, Any]]:
    """Generate synthetic images + return their manifest cases."""
    cases: list[dict[str, Any]] = []
    variant = 0

    for button_type, asset_name in _TEMPLATE_ASSET.items():
        asset_path = ASSETS_PATH / asset_name
        if not asset_path.exists():
            continue
        button = read_rgb(asset_path)

        for scale in _SCALES:
            bg = _background(variant)
            variant += 1
            image, center, size = _composite(bg, button, scale)

            tag = f"{int(scale * 100):03d}"
            rel = f"synthetic/{button_type.value}_{tag}.png"
            _save_rgb(SYNTHETIC_DIR / f"{button_type.value}_{tag}.png", image)
            cases.append(_present_case(rel, button_type, center, size, scale))

    # Negative fixtures: button-free backgrounds -> basic false-positive guards
    # (a detector that hallucinates on featureless input fails these). Stronger
    # negatives with near-button distractors come from real screenshots captured
    # via tools/capture_fixtures.py; synthetic cross-template negatives are
    # intentionally omitted because main already false-positives on them (e.g. a
    # CLICK button yields 13 spurious VORTEX matches at min_matches=8).
    for idx, button_type in enumerate((ButtonType.VORTEX, ButtonType.WEBSITE)):
        bg = _background(variant)
        variant += 1
        rel = f"synthetic/empty_{idx}.png"
        _save_rgb(SYNTHETIC_DIR / f"empty_{idx}.png", bg)
        cases.append(_absent_case(rel, button_type))

    return cases


def merge_into_manifest(synthetic_cases: list[dict[str, Any]]) -> None:
    """Replace synthetic entries in cases.json, preserving real ones."""
    existing: list[dict[str, Any]] = []
    if CASES_PATH.exists():
        raw = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        existing = raw.get("cases", []) if isinstance(raw, dict) else raw

    non_synthetic = [
        c for c in existing if not str(c.get("image", "")).startswith("synthetic/")
    ]
    merged = non_synthetic + synthetic_cases
    CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CASES_PATH.write_text(
        json.dumps({"cases": merged}, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    cases = generate()
    merge_into_manifest(cases)
    print(f"Generated {len(cases)} synthetic cases -> {CASES_PATH}")


if __name__ == "__main__":
    main()
