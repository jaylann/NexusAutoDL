"""Reusable detection-harness helpers shared by the pytest suite and the
standalone ``detection_report.py`` scorer.

Keeping the manifest parsing, asset gating and RGB loading here (instead of in
``conftest.py``) lets the report script reuse the exact same logic without
depending on pytest.
"""

from __future__ import annotations

import json
from math import hypot
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
import numpy.typing as npt

from models import ButtonType

ROOT = Path(__file__).resolve().parent.parent
ASSETS_PATH = ROOT / "assets"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "detection"
CASES_PATH = FIXTURES_DIR / "cases.json"

# Default detection thresholds, mirroring ``models.AppConfig``.
DEFAULT_MIN_MATCHES = 8
DEFAULT_RATIO = 0.75

# Template asset(s) a branch must ship for a button type to be testable. A case
# is skipped when none exist, so the same suite runs on ``main`` and on PRs that
# add/rename templates.
_REQUIRED_ASSETS: dict[ButtonType, tuple[str, ...]] = {
    ButtonType.VORTEX: ("VortexDownloadButton.png", "VortexDownloadButtonNew.png"),
    ButtonType.WEBSITE: ("WebsiteDownloadButton.png", "WebsiteDownloadButtonNew.png"),
    ButtonType.WABBAJACK: ("WabbajackDownloadButton.png",),
    ButtonType.CLICK: ("ClickHereButton.png",),
    ButtonType.UNDERSTOOD: ("UnderstoodButton.png",),
    ButtonType.STAGING: ("StagingButton.png",),
    ButtonType.STANDARD_DOWNLOAD: ("StandardDownloadButton.png",),
}


def read_rgb(path: Path) -> npt.NDArray[np.uint8]:
    """Load an image as RGB, mirroring ``button_detector.read_rgb`` exactly."""
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"Failed to load image: {path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def parse_button_type(value: str) -> Optional[ButtonType]:
    """Resolve a manifest ``button_type`` string to a ``ButtonType`` or None."""
    try:
        return ButtonType(value)
    except ValueError:
        return None


def asset_available(button_type: ButtonType, assets_path: Path = ASSETS_PATH) -> bool:
    """True if at least one template asset for ``button_type`` exists on disk."""
    names = _REQUIRED_ASSETS.get(button_type, ())
    return any((assets_path / name).exists() for name in names)


def load_cases(cases_path: Path = CASES_PATH) -> list[dict[str, Any]]:
    """Parse the manifest into a list of case dicts ([] if missing/empty)."""
    if not cases_path.exists():
        return []
    raw = json.loads(cases_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return list(raw.get("cases", []))
    return list(raw)


def case_image_path(case: dict[str, Any], fixtures_dir: Path = FIXTURES_DIR) -> Path:
    """Resolve a case's ``image`` (manifest-relative) to an absolute path."""
    return fixtures_dir / case["image"]


def skip_reason(
    case: dict[str, Any],
    fixtures_dir: Path = FIXTURES_DIR,
    assets_path: Path = ASSETS_PATH,
) -> Optional[str]:
    """Return why a case can't run on this branch, or None if it can.

    Cases are skipped (not failed) when the button type is unknown here, its
    template asset is absent, or the fixture image is missing -- this keeps the
    suite green across branches with differing assets.
    """
    bt = parse_button_type(case["button_type"])
    if bt is None:
        return f"button_type {case['button_type']!r} not in this branch's ButtonType"
    if not asset_available(bt, assets_path):
        return f"no template asset for {bt.value} on this branch"
    if not case_image_path(case, fixtures_dir).exists():
        return f"fixture image missing: {case['image']}"
    return None


def case_id(case: dict[str, Any]) -> str:
    """Readable id: ``<source>-<variant>-<scale>-<button_type>-<expect>``."""
    meta = case.get("meta", {})
    source = str(meta.get("source", "?")).replace(" ", "")
    variant = str(meta.get("variant", meta.get("resolution", "?")))
    parts = [source, variant]
    if "scale" in meta:
        parts.append(f"x{meta['scale']}")
    parts += [case["button_type"], case["expect"]]
    return "-".join(parts)


def centroid_error(result_xy: tuple[int, int], point: list[int]) -> float:
    """Euclidean distance (px) between a detection centroid and ground truth."""
    return hypot(result_xy[0] - point[0], result_xy[1] - point[1])


def ground_truth_error(result_xy: tuple[int, int], case: dict[str, Any]) -> float:
    """Distance (px) to the nearest ground-truth point of a present-case.

    Cases carry a single ``point``; multi-instance cases additionally list all
    valid instance centers under ``points`` -- hitting any instance counts.
    """
    points = case.get("points") or [case["point"]]
    return min(centroid_error(result_xy, point) for point in points)
