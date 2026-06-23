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
        self._compute_descriptors()
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

    def _compute_descriptors(self) -> None:
        """Compute SIFT descriptors for all assets."""

        def compute_desc(
            img: npt.NDArray[np.uint8],
        ) -> Optional[npt.NDArray[np.float32]]:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            _, desc = self.sift.detectAndCompute(gray, mask=None)
            return desc

        self.assets.vortex_desc = compute_desc(self.assets.vortex_img)
        if self.assets.vortex_new_img is not None:
            self.assets.vortex_new_desc = compute_desc(self.assets.vortex_new_img)

        self.assets.web_desc = compute_desc(self.assets.web_img)
        if self.assets.web_new_img is not None:
            self.assets.web_new_desc = compute_desc(self.assets.web_new_img)
        self.assets.wabbajack_desc = compute_desc(self.assets.wabbajack_img)
        self.assets.click_desc = compute_desc(self.assets.click_img)
        self.assets.understood_desc = compute_desc(self.assets.understood_img)
        self.assets.staging_desc = compute_desc(self.assets.staging_img)

        logger.info("Computed descriptors for all assets")

    def _log_asset_mode(self) -> None:
        """Log which button assets will be used for detection."""
        mode = "legacy" if self.use_legacy_buttons else "new"
        logger.info(f"Using {mode} button templates")

        required = [
            ("Vortex", self.assets.vortex_desc, self.assets.vortex_new_desc),
            ("Website", self.assets.web_desc, self.assets.web_new_desc),
        ]
        for label, legacy_desc, new_desc in required:
            target_desc = legacy_desc if self.use_legacy_buttons else new_desc
            if target_desc is None:
                logger.warning(
                    f"{label} {mode} template not found. "
                    f"{'Add the legacy asset or run without --legacy' if self.use_legacy_buttons else 'Provide the new asset or rerun with --legacy'}."
                )

    def _mode_specific_candidates(
        self,
        *,
        legacy_img: Optional[npt.NDArray[np.uint8]],
        legacy_desc: Optional[npt.NDArray[np.float32]],
        new_img: Optional[npt.NDArray[np.uint8]],
        new_desc: Optional[npt.NDArray[np.float32]],
    ) -> list[TemplateCandidate]:
        """Return template candidate for selected mode if available."""
        img: Optional[npt.NDArray[np.uint8]] = (
            legacy_img if self.use_legacy_buttons else new_img
        )
        desc: Optional[npt.NDArray[np.float32]] = (
            legacy_desc if self.use_legacy_buttons else new_desc
        )
        candidate = self._make_candidate(img, desc)
        return [candidate] if candidate else []

    def _single_candidate(
        self,
        img: Optional[npt.NDArray[np.uint8]],
        desc: Optional[npt.NDArray[np.float32]],
    ) -> list[TemplateCandidate]:
        """Return a single template candidate if descriptors exist."""
        candidate = self._make_candidate(img, desc)
        return [candidate] if candidate else []

    def _make_candidate(
        self,
        img: Optional[npt.NDArray[np.uint8]],
        desc: Optional[npt.NDArray[np.float32]],
    ) -> Optional[TemplateCandidate]:
        if img is None or desc is None:
            return None
        height, width = img.shape[:2]
        return TemplateCandidate(desc=desc, width=int(width), height=int(height))

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
        # Get descriptor for button type
        candidate_map = {
            ButtonType.VORTEX: self._mode_specific_candidates(
                legacy_img=self.assets.vortex_img,
                legacy_desc=self.assets.vortex_desc,
                new_img=self.assets.vortex_new_img,
                new_desc=self.assets.vortex_new_desc,
            ),
            ButtonType.WEBSITE: self._mode_specific_candidates(
                legacy_img=self.assets.web_img,
                legacy_desc=self.assets.web_desc,
                new_img=self.assets.web_new_img,
                new_desc=self.assets.web_new_desc,
            ),
            ButtonType.WABBAJACK: self._single_candidate(
                self.assets.wabbajack_img, self.assets.wabbajack_desc
            ),
            ButtonType.CLICK: self._single_candidate(
                self.assets.click_img, self.assets.click_desc
            ),
            ButtonType.UNDERSTOOD: self._single_candidate(
                self.assets.understood_img, self.assets.understood_desc
            ),
            ButtonType.STAGING: self._single_candidate(
                self.assets.staging_img, self.assets.staging_desc
            ),
        }

        template_candidates = candidate_map[button_type]
        if not template_candidates:
            logger.warning(f"No descriptors for {button_type}")
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
