"""
Button detection using SIFT feature matching.
"""

from __future__ import annotations

import math
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

# RANSAC similarity-fit parameters. SIFT localizes UI text/edges within ~1-2 px,
# so a 3 px reprojection threshold separates coherent matches from scatter.
RANSAC_REPROJ_PX = 3.0
RANSAC_MAX_ITERS = 2000
RANSAC_CONFIDENCE = 0.995

# Transform acceptance gates. Buttons only translate and uniformly scale (DPI),
# so anything rotated or outside the plausible scale window is a false match.
MIN_INLIER_RATIO = 0.5
SCALE_RANGE = (0.5, 2.0)
MAX_ROTATION_DEG = 5.0

# Normalized cross-correlation fallback for templates too small/featureless for
# SIFT. The geometric scale ladder steps ~9% per rung; NCC on these templates
# still correlates >0.9 within +-4.5% scale error. Downscaled thin-text
# templates bottom out around 0.78 (resampling blur) while cross-template false
# matches stay below 0.5, so 0.75 keeps a real margin on both sides.
TM_THRESHOLD = 0.75
TM_SCALES: tuple[float, ...] = tuple(
    round(0.60 * (2.00 / 0.60) ** (i / 14), 4) for i in range(15)
)

# Appearance verification of accepted SIFT fits: NCC between the scene region
# the transform predicts and the template at the recovered scale. A coherent
# text-fragment match (one shared word inside a *different* button) scores
# ~0.5 here while true matches measure >=0.8; the small search pad absorbs
# sub-pixel misalignment of the projected region.
VERIFY_NCC_THRESHOLD = 0.60
VERIFY_SEARCH_PAD = 6


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

    def _ratio_test(
        self, matches: list[list[cv2.DMatch]], ratio: float
    ) -> list[cv2.DMatch]:
        """Apply Lowe's ratio test to knn match pairs."""
        good: list[cv2.DMatch] = []
        for pair in matches:
            if len(pair) == 2:
                m, n = pair
                if m.distance < ratio * n.distance:
                    good.append(m)
        return good

    @staticmethod
    def _estimate_similarity(
        src_pts: npt.NDArray[np.float32],
        dst_pts: npt.NDArray[np.float32],
    ) -> Optional[tuple[npt.NDArray[np.float64], npt.NDArray[np.uint8]]]:
        """Fit a RANSAC similarity transform mapping template points to scene points."""
        transform, inlier_mask = cv2.estimateAffinePartial2D(
            src_pts,
            dst_pts,
            method=cv2.RANSAC,
            ransacReprojThreshold=RANSAC_REPROJ_PX,
            maxIters=RANSAC_MAX_ITERS,
            confidence=RANSAC_CONFIDENCE,
        )
        if transform is None or inlier_mask is None:
            return None
        return transform, inlier_mask

    @staticmethod
    def _validate_transform(
        transform: npt.NDArray[np.float64],
    ) -> Optional[tuple[float, float]]:
        """Return (scale, rotation_deg) if the transform is plausible, else None."""
        scale = float(math.hypot(transform[0, 0], transform[1, 0]))
        rotation_deg = float(math.degrees(math.atan2(transform[1, 0], transform[0, 0])))
        if not (SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]):
            return None
        if abs(rotation_deg) > MAX_ROTATION_DEG:
            return None
        return scale, rotation_deg

    @staticmethod
    def _project_center(
        transform: npt.NDArray[np.float64], width: int, height: int
    ) -> tuple[float, float]:
        """Project the template center through the fitted transform."""
        cx, cy = width / 2.0, height / 2.0
        px = transform[0, 0] * cx + transform[0, 1] * cy + transform[0, 2]
        py = transform[1, 0] * cx + transform[1, 1] * cy + transform[1, 2]
        return float(px), float(py)

    def _verify_match(
        self,
        template: TemplateCandidate,
        scene_gray: npt.NDArray[np.uint8],
        cx: float,
        cy: float,
        scale: float,
    ) -> float:
        """NCC between the predicted scene region and the scaled template."""
        sw = max(8, round(template.width * scale))
        sh = max(8, round(template.height * scale))
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(template.gray, (sw, sh), interpolation=interpolation)

        scene_h, scene_w = scene_gray.shape[:2]
        x1 = max(0, int(cx - sw / 2) - VERIFY_SEARCH_PAD)
        y1 = max(0, int(cy - sh / 2) - VERIFY_SEARCH_PAD)
        x2 = min(scene_w, int(cx + sw / 2) + VERIFY_SEARCH_PAD)
        y2 = min(scene_h, int(cy + sh / 2) + VERIFY_SEARCH_PAD)
        if x2 - x1 < sw or y2 - y1 < sh:
            return -1.0

        result = cv2.matchTemplate(
            scene_gray[y1:y2, x1:x2], resized, cv2.TM_CCOEFF_NORMED
        )
        return float(result.max())

    def _match_sift(
        self,
        template: TemplateCandidate,
        kps: list[cv2.KeyPoint],
        des: npt.NDArray[np.float32],
        scene_gray: npt.NDArray[np.uint8],
        button_type: ButtonType,
        min_matches: int,
        ratio: float,
        offset_x: int,
        offset_y: int,
    ) -> Optional[DetectionResult]:
        """Match one template via SIFT + RANSAC-verified similarity transform."""
        if template.desc is None or len(template.kps) == 0:
            return None

        matches: list[list[cv2.DMatch]] = self.matcher.knnMatch(template.desc, des, k=2)
        good_matches = self._ratio_test(matches, ratio)

        if len(good_matches) < max(4, min_matches // 2):
            logger.debug(
                f"{button_type}: only {len(good_matches)} ratio-test matches "
                f"(need {max(4, min_matches // 2)} to attempt a geometric fit)"
            )
            return None

        src_pts = np.float32([template.kps[m.queryIdx].pt for m in good_matches])
        dst_pts = np.float32([kps[m.trainIdx].pt for m in good_matches])

        fit = self._estimate_similarity(src_pts, dst_pts)
        if fit is None:
            logger.debug(f"{button_type}: similarity fit failed")
            return None
        transform, inlier_mask = fit

        inliers = int(inlier_mask.sum())
        min_inliers = max(4, math.ceil(0.75 * min_matches))
        inlier_ratio = inliers / len(good_matches)

        if inliers < min_inliers or inlier_ratio < MIN_INLIER_RATIO:
            logger.debug(
                f"{button_type}: rejected fit with {inliers} inliers "
                f"(need {min_inliers}) at ratio {inlier_ratio:.2f}"
            )
            return None

        validated = self._validate_transform(transform)
        if validated is None:
            logger.debug(f"{button_type}: rejected implausible transform")
            return None
        scale, rotation_deg = validated

        cx, cy = self._project_center(transform, template.width, template.height)

        # Appearance check: a geometrically coherent fit can still be a shared
        # text fragment inside a different button (e.g. "download" glyphs).
        verify_score = self._verify_match(template, scene_gray, cx, cy, scale)
        if verify_score < VERIFY_NCC_THRESHOLD:
            logger.debug(
                f"{button_type}: rejected fit, appearance check scored "
                f"{verify_score:.2f} (need {VERIFY_NCC_THRESHOLD})"
            )
            return None

        cx += offset_x
        cy += offset_y
        confidence = min(
            1.0, 0.6 * min(inliers / (2 * min_inliers), 1.0) + 0.4 * inlier_ratio
        )

        logger.info(
            f"Detected {button_type} at ({int(cx)}, {int(cy)}) with {inliers} inliers "
            f"(scale {scale:.2f}, rot {rotation_deg:.1f} deg, "
            f"confidence: {confidence:.2f})"
        )

        return DetectionResult(
            button_type=button_type,
            x=int(cx),
            y=int(cy),
            confidence=confidence,
            num_matches=inliers,
            template_width=template.width,
            template_height=template.height,
            scale=scale,
            inliers=inliers,
            method="sift",
        )

    def _match_ncc(
        self,
        template: TemplateCandidate,
        scene_gray: npt.NDArray[np.uint8],
        button_type: ButtonType,
        offset_x: int,
        offset_y: int,
    ) -> Optional[DetectionResult]:
        """Multi-scale normalized cross-correlation fallback."""
        scene_h, scene_w = scene_gray.shape[:2]
        best: Optional[tuple[float, tuple[int, int], float, int, int]] = None

        for scale in TM_SCALES:
            sw = max(1, round(template.width * scale))
            sh = max(1, round(template.height * scale))
            if sw > scene_w or sh > scene_h or sw < 8 or sh < 8:
                continue
            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            resized = cv2.resize(template.gray, (sw, sh), interpolation=interpolation)
            result = cv2.matchTemplate(scene_gray, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if best is None or max_val > best[0]:
                best = (float(max_val), max_loc, scale, sw, sh)

        if best is None or best[0] < TM_THRESHOLD:
            if best is not None:
                logger.debug(
                    f"{button_type}: NCC peak {best[0]:.2f} below {TM_THRESHOLD}"
                )
            return None

        peak, (px, py), scale, sw, sh = best
        cx = px + sw / 2.0 + offset_x
        cy = py + sh / 2.0 + offset_y

        logger.info(
            f"Detected {button_type} at ({int(cx)}, {int(cy)}) via template "
            f"matching (peak {peak:.2f}, scale {scale:.2f})"
        )

        return DetectionResult(
            button_type=button_type,
            x=int(cx),
            y=int(cy),
            confidence=min(peak, 1.0),
            num_matches=0,
            template_width=template.width,
            template_height=template.height,
            scale=scale,
            inliers=None,
            method="template",
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

        best_result: Optional[DetectionResult] = None
        if des is not None and len(kps) > 0:
            for template in template_candidates:
                result: Optional[DetectionResult] = self._match_sift(
                    template,
                    kps,
                    des,
                    gray,
                    button_type,
                    min_matches,
                    ratio,
                    offset_x,
                    offset_y,
                )
                if result and (
                    best_result is None
                    or (result.inliers or 0, result.confidence)
                    > (best_result.inliers or 0, best_result.confidence)
                ):
                    best_result = result
        else:
            logger.debug(f"No scene keypoints found for {button_type}")

        # Fall back to multi-scale template matching when the feature path
        # finds nothing (small/low-feature templates, sub-1.0 DPI scales).
        if best_result is None:
            for template in template_candidates:
                ncc_result = self._match_ncc(
                    template, gray, button_type, offset_x, offset_y
                )
                if ncc_result and (
                    best_result is None
                    or ncc_result.confidence > best_result.confidence
                ):
                    best_result = ncc_result

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
