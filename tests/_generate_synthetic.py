"""Generate deterministic synthetic detection fixtures.

Composites each button template onto gradient backgrounds at known (x, y)
positions and several scales (proxying DPI scaling), then writes the images
plus matching ``cases.json`` entries with exact ground-truth points. All
randomness is seeded, so output is byte-stable and CI reproducible.

The asset composited for VORTEX/WEBSITE is the *New* template -- the one the
detector actually matches against in default (non-legacy) mode -- so synthetic
matches reflect production behaviour rather than the legacy fallback.

Case families:

* clean:    each type at scales 0.75-1.5 (sub-1.0 exercises the NCC fallback)
* noise:    Gaussian noise (sigma=4) over the scale-1.0 composite
* jpeg:     JPEG quality-70 round-trip of the scale-1.0 composite
* clutter:  target plus two non-target templates in one frame
* absent:   button-free frames, cross-template negatives (a frame containing
            only OTHER button types must yield None) and clutter negatives

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

# Types whose templates depict the SAME visual button: WabbajackDownloadButton
# and WebsiteDownloadButtonNew are both the Nexus "Slow download" button at
# different sizes. Detecting one where the other sits is correct behaviour, so
# no cross-template negative may pair them.
_EQUIVALENT_TYPES: tuple[frozenset[ButtonType], ...] = (
    frozenset({ButtonType.WABBAJACK, ButtonType.WEBSITE}),
)


def _equivalent(a: ButtonType, b: ButtonType) -> bool:
    return a == b or any({a, b} <= group for group in _EQUIVALENT_TYPES)


# Scale factors proxy DPI scaling in both directions. Sub-1.0 scales starve
# SIFT on small templates; the detector's template-matching fallback covers
# them, and these cases keep it honest.
_SCALES = (0.75, 0.85, 1.0, 1.25, 1.5)
_CANVAS = (1280, 720)
_PASTE_XY = (480, 300)
# Distractor positions for clutter scenes; chosen so even the widest template
# (466 px) fits the canvas and cannot overlap the target at _PASTE_XY.
_CLUTTER_XY = ((100, 100), (700, 550))

# Tolerances: the projected-center click point is sub-pixel accurate on clean
# composites; degradation and clutter get a little slack.
_TOL_CLEAN = 10
_TOL_DEGRADED = 15

_NOISE_SEED = 1234
_NOISE_SIGMA = 4.0
_JPEG_QUALITY = 70


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


def _paste(
    canvas: np.ndarray, button: np.ndarray, xy: tuple[int, int], scale: float
) -> tuple[int, int]:
    """Paste ``button`` (scaled) onto ``canvas`` in place.

    Returns the button center point at this scale.
    """
    bh, bw = button.shape[:2]
    sw, sh = max(1, int(bw * scale)), max(1, int(bh * scale))
    resized = cv2.resize(button, (sw, sh), interpolation=cv2.INTER_AREA)
    x, y = xy
    canvas[y : y + sh, x : x + sw] = resized
    return (x + sw // 2, y + sh // 2)


def _add_noise(image: np.ndarray) -> np.ndarray:
    """Seeded Gaussian noise -- deterministic, so output stays byte-stable."""
    rng = np.random.default_rng(_NOISE_SEED)
    noise = rng.normal(0.0, _NOISE_SIGMA, image.shape)
    return np.clip(image.astype(np.float64) + noise, 0, 255).astype(np.uint8)


def _jpeg_roundtrip(image: np.ndarray) -> np.ndarray:
    """Encode/decode through JPEG to introduce realistic compression artifacts."""
    params = [int(cv2.IMWRITE_JPEG_QUALITY), _JPEG_QUALITY]
    ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), params)
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return cv2.cvtColor(cv2.imdecode(encoded, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def _present_case(
    image_rel: str,
    button_type: ButtonType,
    center: tuple[int, int],
    scale: float,
    tolerance: int,
    variant: str,
) -> dict[str, Any]:
    return {
        "image": image_rel,
        "button_type": button_type.value,
        "expect": "present",
        "point": [center[0], center[1]],
        "tolerance_px": tolerance,
        "meta": {
            "resolution": f"{_CANVAS[0]}x{_CANVAS[1]}",
            "scale": scale,
            "variant": variant,
            "source": "synthetic",
        },
    }


def _absent_case(
    image_rel: str, button_type: ButtonType, variant: str
) -> dict[str, Any]:
    return {
        "image": image_rel,
        "button_type": button_type.value,
        "expect": "absent",
        "meta": {
            "resolution": f"{_CANVAS[0]}x{_CANVAS[1]}",
            "variant": variant,
            "source": "synthetic",
        },
    }


def _write_case_image(name: str, image: np.ndarray) -> str:
    rel = f"synthetic/{name}.png"
    _save_rgb(SYNTHETIC_DIR / f"{name}.png", image)
    return rel


def generate() -> list[dict[str, Any]]:
    """Generate synthetic images + return their manifest cases."""
    cases: list[dict[str, Any]] = []
    variant = 0

    types: list[ButtonType] = [
        bt for bt, asset in _TEMPLATE_ASSET.items() if (ASSETS_PATH / asset).exists()
    ]
    buttons: dict[ButtonType, np.ndarray] = {
        bt: read_rgb(ASSETS_PATH / _TEMPLATE_ASSET[bt]) for bt in types
    }

    # Clean scale sweep + cross-template negatives off the scale-1.0 frames.
    for idx, button_type in enumerate(types):
        for scale in _SCALES:
            canvas = _background(variant)
            variant += 1
            center = _paste(canvas, buttons[button_type], _PASTE_XY, scale)

            tag = f"{int(scale * 100):03d}"
            rel = _write_case_image(f"{button_type.value}_{tag}", canvas)
            cases.append(
                _present_case(rel, button_type, center, scale, _TOL_CLEAN, "clean")
            )

            if scale == 1.0:
                # A frame containing ONLY this button must yield None for other
                # types -- the direct regression test for cross-template false
                # positives (e.g. a CLICK frame formerly produced 13 spurious
                # VORTEX matches at min_matches=8). Equivalent-looking types
                # are skipped: detecting one where the other sits is correct.
                others = [
                    types[(idx + offset) % len(types)]
                    for offset in range(1, len(types))
                    if not _equivalent(types[(idx + offset) % len(types)], button_type)
                ][:2]
                for other in others:
                    cases.append(_absent_case(rel, other, "cross_template"))

    # Degraded variants of the scale-1.0 composite.
    for button_type in types:
        for name, degrade in (("noise", _add_noise), ("jpeg", _jpeg_roundtrip)):
            canvas = _background(variant)
            variant += 1
            center = _paste(canvas, buttons[button_type], _PASTE_XY, 1.0)
            rel = _write_case_image(f"{button_type.value}_{name}", degrade(canvas))
            cases.append(
                _present_case(rel, button_type, center, 1.0, _TOL_DEGRADED, name)
            )

    # Clutter scenes: the target plus two non-target templates. Present-case for
    # the target, absent-case for a type not in the frame at all.
    for idx, button_type in enumerate(types):
        canvas = _background(variant)
        variant += 1
        center = _paste(canvas, buttons[button_type], _PASTE_XY, 1.0)
        distractors = [types[(idx + 1) % len(types)], types[(idx + 2) % len(types)]]
        for distractor, xy in zip(distractors, _CLUTTER_XY):
            _paste(canvas, buttons[distractor], xy, 1.0)

        rel = _write_case_image(f"{button_type.value}_clutter", canvas)
        cases.append(
            _present_case(rel, button_type, center, 1.0, _TOL_DEGRADED, "clutter")
        )
        present = (button_type, *distractors)
        missing = next(
            (bt for bt in types if not any(_equivalent(bt, p) for p in present)),
            None,
        )
        if missing is not None:
            cases.append(_absent_case(rel, missing, "clutter"))

    # Negative fixtures: button-free backgrounds -> basic false-positive guards
    # (a detector that hallucinates on featureless input fails these).
    for idx, button_type in enumerate((ButtonType.VORTEX, ButtonType.WEBSITE)):
        bg = _background(variant)
        variant += 1
        rel = _write_case_image(f"empty_{idx}", bg)
        cases.append(_absent_case(rel, button_type, "empty"))

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
