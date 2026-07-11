"""
Button detection using SIFT feature matching.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import numpy.typing as npt

from models import (
    ButtonAssets,
    ButtonType,
    BoundingBox,
    DetectionResult,
    TemplateCandidate,
)
from utils.logger import get_logger

logger = get_logger(__name__)


class ButtonDetector:
    """Detects buttons in screenshots using SIFT."""

    def __init__(
        self,
        assets_path: Path = Path("assets"),
        use_legacy_buttons: bool = False,
    ) -> None:
        """
        Initialize button detector.

        Args:
            assets_path: Path to button image assets
        """
        self.assets_path = assets_path
        self.use_legacy_buttons = use_legacy_buttons
        self.sift = cv2.SIFT_create()
        self.matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)

        self.assets = self._load_assets()
        self._templates: dict[ButtonType, list[TemplateCandidate]] = (
            self._build_templates()
        )
        self._log_asset_mode()

        logger.info("Button detector initialized")

    def _load_assets(self) -> ButtonAssets:
        """
        Load button image assets.

        Returns:
            ButtonAssets with loaded images

        Raises:
            FileNotFoundError: If any asset file is missing
        """
        required_assets: dict[str, str] = {
            "vortex": "VortexDownloadButton.png",
            "web": "WebsiteDownloadButton.png",
            "wabbajack": "WabbajackDownloadButton.png",
            "click": "ClickHereButton.png",
            "understood": "UnderstoodButton.png",
            "staging": "StagingButton.png",
        }
        optional_assets: dict[str, str] = {
            "vortex_new": "VortexDownloadButtonNew.png",
            "web_new": "WebsiteDownloadButtonNew.png",
        }

        def read_rgb(path: Path) -> npt.NDArray[np.uint8]:
            img = cv2.imread(str(path))
            if img is None:
                raise ValueError(f"Failed to load image: {path}")
            return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        loaded_images: dict[str, npt.NDArray[np.uint8]] = {}
        for key, filename in required_assets.items():
            path: Path = self.assets_path / filename
            if not path.exists():
                raise FileNotFoundError(f"Asset not found: {path}")
            loaded_images[f"{key}_img"] = read_rgb(path)
            logger.debug(f"Loaded asset: {filename}")

        for key, filename in optional_assets.items():
            path = self.assets_path / filename
            if not path.exists():
                logger.debug(f"Optional asset missing: {filename}")
                continue
            loaded_images[f"{key}_img"] = read_rgb(path)
            logger.debug(f"Loaded optional asset: {filename}")

        return ButtonAssets(**loaded_images)

    def _build_templates(self) -> dict[ButtonType, list[TemplateCandidate]]:
        """Precompute matching data for every template once."""

        def mode_specific(
            legacy_img: Optional[npt.NDArray[np.uint8]],
            new_img: Optional[npt.NDArray[np.uint8]],
        ) -> list[TemplateCandidate]:
            img = legacy_img if self.use_legacy_buttons else new_img
            name = "legacy" if self.use_legacy_buttons else "new"
            candidate = self._make_candidate(img, name)
            return [candidate] if candidate else []

        def single(img: Optional[npt.NDArray[np.uint8]]) -> list[TemplateCandidate]:
            candidate = self._make_candidate(img, "default")
            return [candidate] if candidate else []

        templates = {
            ButtonType.VORTEX: mode_specific(
                self.assets.vortex_img, self.assets.vortex_new_img
            ),
            ButtonType.WEBSITE: mode_specific(
                self.assets.web_img, self.assets.web_new_img
            ),
            ButtonType.WABBAJACK: single(self.assets.wabbajack_img),
            ButtonType.CLICK: single(self.assets.click_img),
            ButtonType.UNDERSTOOD: single(self.assets.understood_img),
            ButtonType.STAGING: single(self.assets.staging_img),
        }
        logger.info("Computed template matching data for all assets")
        return templates

    def _make_candidate(
        self,
        img: Optional[npt.NDArray[np.uint8]],
        name: str,
    ) -> Optional[TemplateCandidate]:
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        kps, desc = self.sift.detectAndCompute(gray, mask=None)
        height, width = img.shape[:2]
        return TemplateCandidate(
            kps=tuple(kps),
            desc=desc,
            gray=gray,
            width=int(width),
            height=int(height),
            name=name,
        )

    def _log_asset_mode(self) -> None:
        """Log which button assets will be used for detection."""
        mode = "legacy" if self.use_legacy_buttons else "new"
        logger.info(f"Using {mode} button templates")

        for label, button_type in (
            ("Vortex", ButtonType.VORTEX),
            ("Website", ButtonType.WEBSITE),
        ):
            if not self._templates[button_type]:
                logger.warning(
                    f"{label} {mode} template not found. "
                    f"{'Add the legacy asset or run without --legacy' if self.use_legacy_buttons else 'Provide the new asset or rerun with --legacy'}."
                )

    def _match_template(
        self,
        template: TemplateCandidate,
        kps: list[cv2.KeyPoint],
        des: npt.NDArray[np.float32],
        button_type: ButtonType,
        min_matches: int,
        ratio: float,
        offset_x: int,
        offset_y: int,
    ) -> Optional[DetectionResult]:
        """Run descriptor matching for a single template."""
        if template.desc is None:
            return None

        matches: list[list[cv2.DMatch]] = self.matcher.knnMatch(template.desc, des, k=2)
        good_matches: list[cv2.DMatch] = []

        for pair in matches:
            if len(pair) == 2:
                m, n = pair
                if m.distance < ratio * n.distance:
                    good_matches.append(m)

        if len(good_matches) < min_matches:
            logger.debug(
                f"{button_type}: only {len(good_matches)} matches (need {min_matches})"
            )
            return None

        pts: npt.NDArray[np.float32] = np.float32(
            [kps[m.trainIdx].pt for m in good_matches]
        )
        cx, cy = np.mean(pts, axis=0)
        cx += offset_x
        cy += offset_y
        confidence = min(len(good_matches) / (min_matches * 2), 1.0)

        logger.info(
            f"Detected {button_type} at ({int(cx)}, {int(cy)}) "
            f"with {len(good_matches)} matches (confidence: {confidence:.2f})"
        )

        return DetectionResult(
            button_type=button_type,
            x=int(cx),
            y=int(cy),
            confidence=confidence,
            num_matches=len(good_matches),
            template_width=template.width,
            template_height=template.height,
        )

    def detect(
        self,
        img: npt.NDArray[np.uint8],
        button_type: ButtonType,
        min_matches: int = 8,
        ratio: float = 0.75,
        bbox: Optional[BoundingBox] = None,
    ) -> Optional[DetectionResult]:
        """
        Detect button in image.

        Args:
            img: Input image (RGB)
            button_type: Type of button to detect
            min_matches: Minimum number of good matches required
            ratio: Lowe's ratio test threshold
            bbox: Optional bounding box to search within

        Returns:
            DetectionResult if button found, None otherwise
        """
        template_candidates = self._templates[button_type]
        if not template_candidates:
            logger.warning(f"No templates for {button_type}")
            return None

        # Crop to bbox if provided
        img_to_search: npt.NDArray[np.uint8] = img
        offset_x: int = 0
        offset_y: int = 0

        if bbox:
            x1 = max(0, bbox.x1)
            y1 = max(0, bbox.y1)
            x2 = min(img.shape[1], bbox.x2)
            y2 = min(img.shape[0], bbox.y2)

            if x2 <= x1 or y2 <= y1:
                logger.debug(f"Invalid bbox for {button_type}: {bbox}")
                return None

            img_to_search = img[y1:y2, x1:x2]
            offset_x, offset_y = x1, y1

        # Convert to grayscale and compute keypoints
        gray = cv2.cvtColor(img_to_search, cv2.COLOR_RGB2GRAY)
        kps, des = self.sift.detectAndCompute(gray, mask=None)

        if des is None or len(kps) == 0:
            logger.debug(f"No keypoints found for {button_type}")
            return None

        best_result: Optional[DetectionResult] = None
        for template in template_candidates:
            result: Optional[DetectionResult] = self._match_template(
                template,
                kps,
                des,
                button_type,
                min_matches,
                ratio,
                offset_x,
                offset_y,
            )
            if result and (
                best_result is None or result.num_matches > best_result.num_matches
            ):
                best_result = result

        if best_result is None:
            logger.debug(
                f"{button_type}: no matches met the threshold across templates"
            )

        return best_result

    def detect_multiple(
        self,
        img: npt.NDArray[np.uint8],
        button_types: list[ButtonType],
        **kwargs,
    ) -> list[DetectionResult]:
        """
        Detect multiple button types in one image.

        Args:
            img: Input image
            button_types: List of button types to detect
            **kwargs: Additional arguments for detect()

        Returns:
            List of detection results
        """
        results = []
        for button_type in button_types:
            result = self.detect(img, button_type, **kwargs)
            if result:
                results.append(result)
        return results
