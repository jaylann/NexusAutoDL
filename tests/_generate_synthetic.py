"""Generate deterministic synthetic detection fixtures.

Composites each button template onto deterministic backgrounds at known
(x, y) positions and scales, then writes the images plus matching
``cases.json`` entries with exact ground-truth points. All randomness is
seeded, so output is byte-stable and CI reproducible.

The asset composited for VORTEX/WEBSITE is the *New* template -- the one the
detector actually matches against in default (non-legacy) mode -- so synthetic
matches reflect production behaviour rather than the legacy fallback.

Case families (``meta.variant``):

* clean:          every type x scales 0.65-2.0 on gradients, varied positions
* noise/jpeg/brightness/contrast/blur: degraded scale-1.0 composites
* occluded:       a cursor arrow drawn over the button center
* busy_ui/busy_noise: buttons on feature-rich backgrounds (synthetic UI
                  panels full of rect+text widgets; block noise)
* clutter:        target plus two non-target templates in one frame
* multi_instance: the same button twice (nearest ground-truth point wins)
* mega:           all templates in one large frame
* absent guards:  empty frames, busy frames with no buttons at all,
                  cross-template negatives, lookalike buttons whose TEXT
                  differs (same visual style -- must NOT match)

Run from the repo root:

    python -m tests._generate_synthetic
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

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


# Scale factors proxy DPI scaling in both directions, spanning the detector's
# supported window. Sub-1.0 scales starve SIFT on small templates (covered by
# the NCC fallback); 2.0 exercises the upper SIFT scale gate.
_SCALES = (0.65, 0.75, 0.85, 1.0, 1.25, 1.5, 1.75, 2.0)
_CANVAS = (1280, 720)
_PASTE_XY = (480, 300)
# Fractional positions of the free area (canvas minus button), cycled through
# the clean sweep so offset bugs anywhere on the frame get caught.
_POSITION_POOL: tuple[tuple[float, float], ...] = (
    (0.50, 0.50),
    (0.02, 0.03),
    (0.95, 0.05),
    (0.05, 0.92),
    (0.93, 0.90),
    (0.50, 0.05),
    (0.03, 0.50),
    (0.92, 0.50),
)
# Distractor positions for clutter scenes; chosen so even the widest template
# (466 px) fits the canvas and cannot overlap the target at _PASTE_XY.
_CLUTTER_XY = ((100, 100), (700, 550))

# Tolerances: the projected-center click point is sub-pixel accurate on clean
# composites; degradation, clutter and busy scenes get a little slack.
_TOL_CLEAN = 10
_TOL_DEGRADED = 15

_NOISE_SEED = 1234
_NOISE_SIGMA = 4.0
_JPEG_QUALITY = 70

# Text rendered on lookalike buttons: same visual style as the "Slow download"
# button but different words -- the detector must NOT match any template.
# (A "Slow download" lookalike in another font is intentionally excluded: that
# IS the target button and detecting it would be correct.)
_LOOKALIKE_TEXTS = ("Cancel download", "Fast download", "Slow upload", "Settings")


# --------------------------------------------------------------------------
# image primitives
# --------------------------------------------------------------------------


def _save_rgb(path: Path, rgb: np.ndarray) -> None:
    """Write an RGB array to PNG (cv2 expects BGR on disk)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def _background(variant: int, size: tuple[int, int] = _CANVAS) -> np.ndarray:
    """Smooth diagonal gradient background.

    Gradients compress to tiny PNGs (unlike per-pixel noise) and are corner-free,
    so SIFT finds no button-like keypoints on them -- present-cases match only the
    pasted button, and negative cases stay false-positive-free. ``variant`` picks
    a deterministic colour ramp so frames differ without randomness.
    """
    w, h = size
    xs = np.linspace(0, 1, w, dtype=np.float32)
    ys = np.linspace(0, 1, h, dtype=np.float32)
    ramp = (xs[None, :] + ys[:, None]) / 2.0  # 0..1 diagonal
    low, high = (40, 90) if variant % 2 == 0 else (70, 130)
    grey = (low + ramp * (high - low)).astype(np.uint8)
    return np.repeat(grey[:, :, None], 3, axis=2)


def _bg_block_noise(seed: int) -> np.ndarray:
    """Feature-rich block-noise background (thousands of corner keypoints)."""
    w, h = _CANVAS
    rng = np.random.default_rng(seed)
    blocks = rng.integers(30, 120, (h // 16, w // 16), dtype=np.uint8)
    grey = cv2.resize(blocks, (w, h), interpolation=cv2.INTER_NEAREST)
    return np.repeat(grey[:, :, None], 3, axis=2)


def _bg_ui_panel(seed: int) -> np.ndarray:
    """Synthetic app UI: dark rect widgets with light text.

    Adversarial for small button templates -- intensity NCC sees every widget
    as 'dark rounded rect with text'. This is what the Vortex window actually
    looks like, so absent-cases on these frames guard the production FP mode.
    """
    w, h = _CANVAS
    img = np.full((h, w, 3), 35, np.uint8)
    rng = np.random.default_rng(seed)
    for _ in range(40):
        x, y = int(rng.integers(0, w - 200)), int(rng.integers(0, h - 60))
        ww, hh = int(rng.integers(60, 200)), int(rng.integers(20, 60))
        c = int(rng.integers(45, 90))
        cv2.rectangle(img, (x, y), (x + ww, y + hh), (c, c, c), -1)
        cv2.putText(
            img,
            "Item text",
            (x + 5, y + hh // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (160, 160, 160),
            1,
        )
    return img


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


def _paste_at_fraction(
    canvas: np.ndarray, button: np.ndarray, frac: tuple[float, float], scale: float
) -> tuple[int, int]:
    """Paste at a fraction of the free area so the button always fits."""
    bh, bw = button.shape[:2]
    sw, sh = max(1, int(bw * scale)), max(1, int(bh * scale))
    x = int(frac[0] * (canvas.shape[1] - sw))
    y = int(frac[1] * (canvas.shape[0] - sh))
    return _paste(canvas, button, (x, y), scale)


def _draw_cursor(canvas: np.ndarray, tip: tuple[int, int]) -> None:
    """Draw a mouse-cursor arrow with its tip at ``tip`` (realistic occlusion)."""
    x, y = tip
    pts = np.array(
        [
            [x, y],
            [x, y + 20],
            [x + 5, y + 16],
            [x + 9, y + 24],
            [x + 12, y + 22],
            [x + 8, y + 15],
            [x + 14, y + 14],
        ],
        np.int32,
    )
    cv2.fillPoly(canvas, [pts], (250, 250, 250))
    cv2.polylines(canvas, [pts], True, (20, 20, 20), 1)


def _draw_lookalike(canvas: np.ndarray, text: str, xy: tuple[int, int]) -> None:
    """Draw a 'Slow download'-styled button with different words."""
    x, y = xy
    cv2.rectangle(canvas, (x, y), (x + 420, y + 70), (45, 45, 48), -1)
    cv2.rectangle(canvas, (x, y), (x + 420, y + 70), (90, 90, 95), 2)
    cv2.putText(
        canvas,
        text,
        (x + 60, y + 45),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (220, 220, 220),
        2,
        cv2.LINE_AA,
    )


# --------------------------------------------------------------------------
# degradations
# --------------------------------------------------------------------------


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


def _brighten(image: np.ndarray) -> np.ndarray:
    """Global brightness shift (night-light / different monitor calibration)."""
    return np.clip(image.astype(np.int16) + 40, 0, 255).astype(np.uint8)


def _reduce_contrast(image: np.ndarray) -> np.ndarray:
    """Washed-out rendering (overlay dimming, cheap panels)."""
    return np.clip(image.astype(np.float64) * 0.7 + 38, 0, 255).astype(np.uint8)


def _blur(image: np.ndarray) -> np.ndarray:
    """Mild blur (fractional browser zoom, compositor scaling)."""
    return cv2.GaussianBlur(image, (3, 3), 0)


_DEGRADATIONS: tuple[tuple[str, Callable[[np.ndarray], np.ndarray]], ...] = (
    ("noise", _add_noise),
    ("jpeg", _jpeg_roundtrip),
    ("brightness", _brighten),
    ("contrast", _reduce_contrast),
    ("blur", _blur),
)


# --------------------------------------------------------------------------
# case records
# --------------------------------------------------------------------------


def _present_case(
    image_rel: str,
    button_type: ButtonType,
    center: tuple[int, int],
    scale: float,
    tolerance: int,
    variant: str,
    points: Optional[list[tuple[int, int]]] = None,
    resolution: str = f"{_CANVAS[0]}x{_CANVAS[1]}",
) -> dict[str, Any]:
    case: dict[str, Any] = {
        "image": image_rel,
        "button_type": button_type.value,
        "expect": "present",
        "point": [center[0], center[1]],
        "tolerance_px": tolerance,
        "meta": {
            "resolution": resolution,
            "scale": scale,
            "variant": variant,
            "source": "synthetic",
        },
    }
    if points is not None:
        case["points"] = [[p[0], p[1]] for p in points]
    return case


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


# --------------------------------------------------------------------------
# case families
# --------------------------------------------------------------------------


def _load_types() -> tuple[list[ButtonType], dict[ButtonType, np.ndarray]]:
    types = [
        bt for bt, asset in _TEMPLATE_ASSET.items() if (ASSETS_PATH / asset).exists()
    ]
    return types, {bt: read_rgb(ASSETS_PATH / _TEMPLATE_ASSET[bt]) for bt in types}


def _family_clean_sweep(
    types: list[ButtonType], buttons: dict[ButtonType, np.ndarray]
) -> list[dict[str, Any]]:
    """Scale sweep x cycled positions + cross-template negatives at 1.0."""
    cases: list[dict[str, Any]] = []
    variant = 0
    for idx, button_type in enumerate(types):
        for s_idx, scale in enumerate(_SCALES):
            canvas = _background(variant)
            variant += 1
            frac = _POSITION_POOL[(idx + s_idx) % len(_POSITION_POOL)]
            center = _paste_at_fraction(canvas, buttons[button_type], frac, scale)

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
    return cases


def _family_degraded(
    types: list[ButtonType], buttons: dict[ButtonType, np.ndarray]
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    variant = 100
    for button_type in types:
        for name, degrade in _DEGRADATIONS:
            canvas = _background(variant)
            variant += 1
            center = _paste(canvas, buttons[button_type], _PASTE_XY, 1.0)
            rel = _write_case_image(f"{button_type.value}_{name}", degrade(canvas))
            cases.append(
                _present_case(rel, button_type, center, 1.0, _TOL_DEGRADED, name)
            )
    return cases


def _family_occluded(
    types: list[ButtonType], buttons: dict[ButtonType, np.ndarray]
) -> list[dict[str, Any]]:
    """A cursor arrow over the button center -- users hover what we click."""
    cases: list[dict[str, Any]] = []
    variant = 200
    for button_type in types:
        canvas = _background(variant)
        variant += 1
        center = _paste(canvas, buttons[button_type], _PASTE_XY, 1.0)
        _draw_cursor(canvas, center)
        rel = _write_case_image(f"{button_type.value}_occluded", canvas)
        cases.append(
            _present_case(rel, button_type, center, 1.0, _TOL_DEGRADED, "occluded")
        )
    return cases


def _family_busy(
    types: list[ButtonType], buttons: dict[ButtonType, np.ndarray]
) -> list[dict[str, Any]]:
    """Buttons on feature-rich scenes + absent guards on the empty scenes."""
    cases: list[dict[str, Any]] = []

    # Present: on a UI panel at 1.0 and 0.75, on block noise at 1.0.
    for idx, button_type in enumerate(types):
        for label, bg, scale in (
            ("busy_ui", _bg_ui_panel(30 + idx), 1.0),
            ("busy_ui", _bg_ui_panel(60 + idx), 0.75),
            ("busy_noise", _bg_block_noise(90 + idx), 1.0),
        ):
            canvas = bg.copy()
            center = _paste(canvas, buttons[button_type], _PASTE_XY, scale)
            tag = f"{label}_{int(scale * 100):03d}"
            rel = _write_case_image(f"{button_type.value}_{tag}", canvas)
            cases.append(
                _present_case(rel, button_type, center, scale, _TOL_DEGRADED, label)
            )

    # Absent: button-free busy scenes; every type must return None. These are
    # the direct regression guard for the NCC fallback matching generic
    # rect+text widgets (found while expanding this suite).
    for seed in (3, 4, 5):
        rel = _write_case_image(f"busy_ui_empty_{seed}", _bg_ui_panel(seed))
        for button_type in types:
            cases.append(_absent_case(rel, button_type, "busy_ui_empty"))
    rel = _write_case_image("busy_noise_empty", _bg_block_noise(7))
    for button_type in types:
        cases.append(_absent_case(rel, button_type, "busy_noise_empty"))
    return cases


def _family_lookalike(types: list[ButtonType]) -> list[dict[str, Any]]:
    """Same button style, different words -- must never match any template."""
    cases: list[dict[str, Any]] = []
    for i, text in enumerate(_LOOKALIKE_TEXTS):
        canvas = _background(300 + i)
        _draw_lookalike(canvas, text, (480, 300))
        slug = text.lower().replace(" ", "_")
        rel = _write_case_image(f"lookalike_{slug}", canvas)
        for button_type in types:
            cases.append(_absent_case(rel, button_type, "lookalike"))
    return cases


def _family_clutter(
    types: list[ButtonType], buttons: dict[ButtonType, np.ndarray]
) -> list[dict[str, Any]]:
    """Target plus two non-target templates; absent-case for a missing type."""
    cases: list[dict[str, Any]] = []
    variant = 400
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
    return cases


def _family_multi_instance(
    types: list[ButtonType], buttons: dict[ButtonType, np.ndarray]
) -> list[dict[str, Any]]:
    """The same button twice; detection must land on ONE of the instances."""
    cases: list[dict[str, Any]] = []
    variant = 500
    for button_type in types:
        canvas = _background(variant)
        variant += 1
        c1 = _paste_at_fraction(canvas, buttons[button_type], (0.05, 0.10), 1.0)
        c2 = _paste_at_fraction(canvas, buttons[button_type], (0.90, 0.85), 1.0)
        rel = _write_case_image(f"{button_type.value}_multi", canvas)
        cases.append(
            _present_case(
                rel,
                button_type,
                c1,
                1.0,
                _TOL_DEGRADED,
                "multi_instance",
                points=[c1, c2],
            )
        )
    return cases


def _family_mega(
    types: list[ButtonType], buttons: dict[ButtonType, np.ndarray]
) -> list[dict[str, Any]]:
    """All templates in one large frame; each must be found among the others."""
    size = (1600, 900)
    canvas = _background(600, size)
    slots = (
        (0.05, 0.06),
        (0.90, 0.08),
        (0.06, 0.50),
        (0.92, 0.52),
        (0.10, 0.90),
        (0.88, 0.92),
    )
    centers: dict[ButtonType, tuple[int, int]] = {}
    for button_type, frac in zip(types, slots):
        centers[button_type] = _paste_at_fraction(
            canvas, buttons[button_type], frac, 1.0
        )
    rel = _write_case_image("mega_all_buttons", canvas)
    return [
        _present_case(
            rel,
            bt,
            centers[bt],
            1.0,
            _TOL_DEGRADED,
            "mega",
            resolution=f"{size[0]}x{size[1]}",
        )
        for bt in centers
    ]


def _family_empty(types: list[ButtonType]) -> list[dict[str, Any]]:
    """Featureless empty frames -- basic hallucination guard."""
    cases: list[dict[str, Any]] = []
    for idx, button_type in enumerate(types[:2]):
        rel = _write_case_image(f"empty_{idx}", _background(700 + idx))
        cases.append(_absent_case(rel, button_type, "empty"))
    return cases


def generate() -> list[dict[str, Any]]:
    """Generate synthetic images + return their manifest cases."""
    types, buttons = _load_types()
    cases: list[dict[str, Any]] = []
    cases += _family_clean_sweep(types, buttons)
    cases += _family_degraded(types, buttons)
    cases += _family_occluded(types, buttons)
    cases += _family_busy(types, buttons)
    cases += _family_lookalike(types)
    cases += _family_clutter(types, buttons)
    cases += _family_multi_instance(types, buttons)
    cases += _family_mega(types, buttons)
    cases += _family_empty(types)
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
