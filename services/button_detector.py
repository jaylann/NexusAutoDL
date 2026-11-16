"""
Button detection using SIFT feature matching.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from models import (
    BoundingBox,
    ButtonAssets,
    ButtonType,
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

# Share of the label's matched keypoints that must agree on one placement.
# Low on purpose: a word of the label that is on screen several times splits
# the matches between its instances. Vortex's real dialog says "download" five
# times around the "Download manually" button. At 125 % scaling the fit on the
# real button holds 0.35 of the matches and was rejected at the earlier floor
# of 0.5, although it sits on the button and passes every other check. No
# benchmark case needs this gate at all: with it off there is still no false
# positive, the span, appearance and label-end checks reject them first.
MIN_INLIER_RATIO = 0.2

# Share of the template's keypoint width the inliers must span. Text that only
# starts like the label fits perfectly and looks right where it overlaps: the
# dialog holding Vortex's "Download manually" button is titled "Download mod",
# and its "Download m" was accepted as the button. Such a fit covers under half
# of the label (0.46 and 0.24 on the real screenshots from issue #13), while
# true fits measure >= 0.67 across the benchmark.
MIN_INLIER_SPAN = 0.6

# Transform acceptance gates. Buttons only translate and uniformly scale (DPI),
# so anything rotated or outside the plausible scale window is a false match.
# The scale window extends slightly past the rendered-scale extremes (0.65-2.0
# in the benchmark) so a boundary fit is not rejected by rounding.
SCALE_RANGE = (0.4, 2.2)
MAX_ROTATION_DEG = 5.0

# Normalized cross-correlation fallback for templates too small/featureless for
# SIFT. SIFT only starves on DOWN-scales (fewer pixels -> fewer keypoints);
# upscaled buttons always carry enough features, so the ladder stops at 1.1 --
# a wider ladder lets busy UI scenes (generic dark rects with light text)
# impersonate small button templates at inflated scales.
TM_SCALES: tuple[float, ...] = tuple(
    round(0.60 * (1.10 / 0.60) ** (i / 7), 4) for i in range(8)
)

# The ladder's steps are 9 % apart, so a button can be up to 4.5 % off the
# size it was found at. The whole picture still correlates there, but its
# label is a pixel or two out of place at the ends, enough to fail the label
# check: the old Vortex button at 150 % scaling (real Vortex 1.13.7) scored
# 0.70 at the ladder's size and 0.95 at its own. The label is therefore
# judged at each of these sizes around the ladder's and the best one counts.
# The size that fits the label is not the one that fits the outline best: a
# button's padding does not scale exactly with its text.
TM_REFINE_FACTORS: tuple[float, ...] = (
    0.955,
    0.97,
    0.985,
    1.0,
    1.015,
    1.03,
    1.045,
)

# Layered acceptance: strong intensity peaks pass outright; band peaks must
# also match the template's gradient (edge) signature at the peak location.
# Measured on adversarial rect+text UI scenes: false peaks reach 0.824
# intensity but at most 0.71 gradient, while the weakest true fallback case
# (thin-text wabbajack at 0.85x) scores 0.876 intensity / 0.913 gradient.
TM_STRONG_THRESHOLD = 0.90
TM_BAND_THRESHOLD = 0.80
TM_GRAD_THRESHOLD = 0.80

# Appearance verification of accepted SIFT fits: NCC between the scene region
# the transform predicts and the template at the recovered scale. A coherent
# text-fragment match (one shared word inside a *different* button) scores
# ~0.5 here while true matches measure >=0.8; the small search pad absorbs
# sub-pixel misalignment of the projected region.
VERIFY_NCC_THRESHOLD = 0.60
VERIFY_SEARCH_PAD = 6
# Share of the button that must be on screen to verify it. Frames are per
# monitor and the Vortex search box is an inset of its window, so a button near
# an edge is seen cut off; below half there is too little to be sure of, and
# the click point (the button's centre) would lie off the frame.
VERIFY_MIN_VISIBLE = 0.5

# Both ends of the label must look right on their own. A neighbour that shares
# one word with the label ("Fast download" beside "Slow download") matches
# where the words overlap and scores well over the whole button; only the
# other end gives it away. Each end is this share of the label's width, with a
# few template pixels of margin so the first and last glyph are whole.
END_PATCH_FRACTION = 0.3
END_PATCH_MARGIN = 4
END_NCC_THRESHOLD = 0.5

# What is written on the button, compared without its outline. The template
# search matches shape first: another button of the same size and colour
# scores 0.9 as a whole. On a user's screenshot (issue #23) Vortex's own "Go
# Premium" button passed as "Understood!" and as the old "Download". The middle
# band of a picture holds the label and its fill but no border. There the
# wrong matches score 0.60 to 0.64 and every true template match in the
# benchmark 0.90 or more, real Vortex 1.13.7 at 125 and 150 % among them. The
# bar sits between.
LABEL_BAND_X = (0.15, 0.85)
LABEL_BAND_Y = (0.25, 0.75)
LABEL_SEARCH_PAD = 3
LABEL_NCC_THRESHOLD = 0.75

# Geometric fits to try per template. The best-supported one can be such a
# neighbour; its matches are set aside and the rest is fitted again.
MAX_FIT_ROUNDS = 3


class ButtonDetector:
    """Detects buttons in screenshots using SIFT."""

    def __init__(self, assets_path: Path = Path("assets")) -> None:
        """
        Initialize button detector.

        Args:
            assets_path: Path to button image assets
        """
        self.assets_path = assets_path
        self.sift = cv2.SIFT_create()
        self.matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)

        # Per-frame caches, see _scene_for.
        self._scene: tuple[Any, tuple[int, int, int, int], Any] | None = None
        self._ncc_peaks: dict[int, tuple[Any]] = {}

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
            "vortex_legacy_100": "VortexDownloadButtonLegacy100.png",
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

        assets = self.assets
        # Every picture we ship of a button, per type: each UI generation and
        # each size it was captured at. detect() tries them all and keeps the
        # best fit, so nobody has to tell the app which Vortex or Nexus design
        # is on screen (old Vortex with today's site is a real combination).
        pictures: dict[ButtonType, tuple[tuple[str, object], ...]] = {
            ButtonType.VORTEX: (
                ("new", assets.vortex_new_img),
                ("legacy", assets.vortex_img),
                # The old orange button as Vortex draws it at 100 % scaling,
                # cut from a user's screenshot (issue #23). The two pictures
                # above are 150 % captures; shrunk to two thirds they do not
                # look like text Windows rendered at 100 %.
                ("legacy-100", assets.vortex_legacy_100_img),
            ),
            ButtonType.WEBSITE: (
                ("new", assets.web_new_img),
                # The same "Slow download" button captured at 100 % scaling;
                # the "new" picture above is a 150 % capture.
                ("new-100", assets.wabbajack_img),
                ("legacy", assets.web_img),
            ),
            ButtonType.WABBAJACK: (("default", assets.wabbajack_img),),
            ButtonType.CLICK: (("default", assets.click_img),),
            ButtonType.UNDERSTOOD: (("default", assets.understood_img),),
            ButtonType.STAGING: (("default", assets.staging_img),),
        }
        templates: dict[ButtonType, list[TemplateCandidate]] = {}
        for button_type, named_images in pictures.items():
            candidates = [self._make_candidate(img, name) for name, img in named_images]
            templates[button_type] = [c for c in candidates if c is not None]
        logger.info("Computed template matching data for all assets")
        return templates

    def _make_candidate(
        self,
        img: npt.NDArray[np.uint8] | None,
        name: str,
    ) -> TemplateCandidate | None:
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
        """Log which pictures each button type is matched against."""
        for button_type, candidates in self._templates.items():
            names = ", ".join(c.name for c in candidates) or "none"
            logger.debug(f"{button_type.value} templates: {names}")
            if not candidates:
                logger.warning(f"No template available for {button_type.value}")

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
    ) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.uint8]] | None:
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
    ) -> tuple[float, float] | None:
        """Return (scale, rotation_deg) if the transform is plausible, else None."""
        scale = float(math.hypot(transform[0, 0], transform[1, 0]))
        rotation_deg = float(math.degrees(math.atan2(transform[1, 0], transform[0, 0])))
        if not (SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]):
            return None
        if abs(rotation_deg) > MAX_ROTATION_DEG:
            return None
        return scale, rotation_deg

    @staticmethod
    def _inlier_span(
        template: TemplateCandidate, inlier_pts: npt.NDArray[np.float32]
    ) -> float:
        """Horizontal share of the template's keypoints the inliers cover."""
        template_xs = [kp.pt[0] for kp in template.kps]
        extent = max(template_xs) - min(template_xs)
        if extent <= 0 or len(inlier_pts) == 0:
            return 1.0
        xs = inlier_pts[:, 0]
        return float(xs.max() - xs.min()) / extent

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
        """NCC between the predicted scene region and the scaled template.

        Only the part of the button that lies inside ``scene_gray`` is
        compared, so a button cut by the frame edge still verifies; -1.0 when
        less than ``VERIFY_MIN_VISIBLE`` of it is there.
        """
        sw = max(8, round(template.width * scale))
        sh = max(8, round(template.height * scale))
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(template.gray, (sw, sh), interpolation=interpolation)

        scene_h, scene_w = scene_gray.shape[:2]
        left, top = round(cx - sw / 2), round(cy - sh / 2)
        vis_x1, vis_y1 = max(0, left), max(0, top)
        vis_x2, vis_y2 = min(scene_w, left + sw), min(scene_h, top + sh)
        if vis_x2 <= vis_x1 or vis_y2 <= vis_y1:
            return -1.0
        visible = (vis_x2 - vis_x1) * (vis_y2 - vis_y1) / (sw * sh)
        if visible < VERIFY_MIN_VISIBLE:
            return -1.0

        part = resized[vis_y1 - top : vis_y2 - top, vis_x1 - left : vis_x2 - left]
        x1 = max(0, vis_x1 - VERIFY_SEARCH_PAD)
        y1 = max(0, vis_y1 - VERIFY_SEARCH_PAD)
        x2 = min(scene_w, vis_x2 + VERIFY_SEARCH_PAD)
        y2 = min(scene_h, vis_y2 + VERIFY_SEARCH_PAD)
        result = cv2.matchTemplate(scene_gray[y1:y2, x1:x2], part, cv2.TM_CCOEFF_NORMED)
        return float(np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0).max())

    def _candidate_matches(
        self, matches: list[list[cv2.DMatch]], ratio: float
    ) -> list[cv2.DMatch]:
        """Ratio-tested matches that tolerate the label being on screen twice.

        Lowe's test drops a keypoint whose two best matches are equally good,
        which is every keypoint of a word that appears twice: "download" in
        "Slow download" next to "Fast download", or one button in two windows.
        Such a keypoint keeps both matches when both clearly beat the third;
        the geometric fit then decides which instance each belongs to.
        """
        good: list[cv2.DMatch] = []
        for group in matches:
            if len(group) < 2:
                continue
            first, second = group[0], group[1]
            if first.distance < ratio * second.distance:
                good.append(first)
            elif len(group) > 2 and second.distance < ratio * group[2].distance:
                good.extend((first, second))
        return good

    def _label_ends_score(
        self,
        template: TemplateCandidate,
        scene_gray: npt.NDArray[np.uint8],
        left: float,
        top: float,
        scale: float,
    ) -> float:
        """Lower NCC of the label's two ends at a predicted button position.

        ``left``/``top`` is where the template's top-left corner lands in
        ``scene_gray``. An end that lies outside the image cannot be judged
        and is skipped; -1.0 when neither end can be.
        """
        xs = [kp.pt[0] for kp in template.kps]
        if not xs:
            return 1.0
        label_left, label_right = min(xs), max(xs)
        end_width = END_PATCH_FRACTION * (label_right - label_left)
        if end_width < 2:
            return 1.0

        sw = max(8, round(template.width * scale))
        sh = max(8, round(template.height * scale))
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(template.gray, (sw, sh), interpolation=interpolation)
        scene_h, scene_w = scene_gray.shape[:2]
        x0, y0 = round(left), round(top)

        scores: list[float] = []
        for start, stop in (
            (label_left - END_PATCH_MARGIN, label_left + end_width),
            (label_right - end_width, label_right + END_PATCH_MARGIN),
        ):
            px1 = max(0, round(start * scale))
            px2 = min(sw, round(stop * scale))
            if px2 - px1 < 4:
                continue
            # The patch must be fully on screen, pad included on no side.
            if x0 + px1 < 0 or x0 + px2 > scene_w or y0 < 0 or y0 + sh > scene_h:
                continue
            patch = resized[:, px1:px2]
            x1 = max(0, x0 + px1 - VERIFY_SEARCH_PAD)
            y1 = max(0, y0 - VERIFY_SEARCH_PAD)
            x2 = min(scene_w, x0 + px2 + VERIFY_SEARCH_PAD)
            y2 = min(scene_h, y0 + sh + VERIFY_SEARCH_PAD)
            result = cv2.matchTemplate(
                scene_gray[y1:y2, x1:x2], patch, cv2.TM_CCOEFF_NORMED
            )
            scores.append(
                float(np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0).max())
            )
        return min(scores) if scores else -1.0

    def _label_score(
        self,
        template: TemplateCandidate,
        scene_gray: npt.NDArray[np.uint8],
        left: int,
        top: int,
        scale: float,
    ) -> float | None:
        """NCC of the label band alone at a predicted button position.

        ``left``/``top`` is where the template's top-left corner lands in
        ``scene_gray``. None when the band cannot be judged: it has no text
        in it, or it does not lie fully inside the image.
        """
        sw = max(8, round(template.width * scale))
        sh = max(8, round(template.height * scale))
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(template.gray, (sw, sh), interpolation=interpolation)

        bx1, bx2 = round(LABEL_BAND_X[0] * sw), round(LABEL_BAND_X[1] * sw)
        by1, by2 = round(LABEL_BAND_Y[0] * sh), round(LABEL_BAND_Y[1] * sh)
        band = resized[by1:by2, bx1:bx2]
        if band.shape[0] < 4 or band.shape[1] < 4 or float(band.std()) < 1.0:
            return None

        scene_h, scene_w = scene_gray.shape[:2]
        if left + bx1 < 0 or top + by1 < 0:
            return None
        if left + bx2 > scene_w or top + by2 > scene_h:
            return None
        x1 = max(0, left + bx1 - LABEL_SEARCH_PAD)
        y1 = max(0, top + by1 - LABEL_SEARCH_PAD)
        x2 = min(scene_w, left + bx2 + LABEL_SEARCH_PAD)
        y2 = min(scene_h, top + by2 + LABEL_SEARCH_PAD)
        result = cv2.matchTemplate(scene_gray[y1:y2, x1:x2], band, cv2.TM_CCOEFF_NORMED)
        return float(np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0).max())

    def _best_label_score(
        self,
        template: TemplateCandidate,
        scene_gray: npt.NDArray[np.uint8],
        cx: float,
        cy: float,
        scale: float,
    ) -> float | None:
        """Best label score for a button centred on ``(cx, cy)``.

        Tried at each size in ``TM_REFINE_FACTORS`` around ``scale``; None
        when the label cannot be judged at any of them.
        """
        scores: list[float] = []
        for factor in TM_REFINE_FACTORS:
            refined = scale * factor
            width = max(8, round(template.width * refined))
            height = max(8, round(template.height * refined))
            score = self._label_score(
                template,
                scene_gray,
                round(cx - width / 2),
                round(cy - height / 2),
                refined,
            )
            if score is not None:
                scores.append(score)
        return max(scores) if scores else None

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
    ) -> DetectionResult | None:
        """Match one template via SIFT + RANSAC-verified similarity transform.

        ``scene_gray`` is the whole frame; ``kps``/``des`` come from the search
        box at ``(offset_x, offset_y)`` in it.
        """
        if template.desc is None or len(template.kps) == 0:
            return None

        neighbours = min(3, len(kps))
        matches: list[list[cv2.DMatch]] = self.matcher.knnMatch(
            template.desc, des, k=neighbours
        )
        remaining = self._candidate_matches(matches, ratio)
        min_candidates = max(4, min_matches // 2)
        min_inliers = max(4, math.ceil(0.75 * min_matches))

        if len(remaining) < min_candidates:
            logger.debug(
                f"{button_type}: only {len(remaining)} ratio-test matches "
                f"(need {min_candidates} to attempt a geometric fit)"
            )
            return None

        # The best-supported fit can be the wrong instance (a neighbour that
        # shares a word with the label). Set its matches aside and fit again.
        for _ in range(MAX_FIT_ROUNDS):
            if len(remaining) < min_candidates:
                break
            src_pts = np.float32([template.kps[m.queryIdx].pt for m in remaining])
            dst_pts = np.float32([kps[m.trainIdx].pt for m in remaining])

            fit = self._estimate_similarity(src_pts, dst_pts)
            if fit is None:
                logger.debug(f"{button_type}: similarity fit failed")
                break
            transform, inlier_mask = fit
            inlier_flags = inlier_mask.ravel() > 0
            inliers = int(inlier_flags.sum())
            if inliers < min_inliers:
                logger.debug(
                    f"{button_type}: rejected fit with {inliers} inliers "
                    f"(need {min_inliers})"
                )
                break

            result = self._accept_fit(
                template,
                transform,
                src_pts[inlier_flags],
                # A keypoint with two candidate matches counts once.
                inliers / len({m.queryIdx for m in remaining}),
                scene_gray,
                button_type,
                min_inliers,
                offset_x,
                offset_y,
            )
            if result is not None:
                return result
            remaining = [m for m, used in zip(remaining, inlier_flags) if not used]
        return None

    def _accept_fit(
        self,
        template: TemplateCandidate,
        transform: npt.NDArray[np.float64],
        inlier_pts: npt.NDArray[np.float32],
        inlier_ratio: float,
        scene_gray: npt.NDArray[np.uint8],
        button_type: ButtonType,
        min_inliers: int,
        offset_x: int,
        offset_y: int,
    ) -> DetectionResult | None:
        """Gate one geometric fit; the detection if it is the button."""
        inliers = len(inlier_pts)
        if inlier_ratio < MIN_INLIER_RATIO:
            logger.debug(
                f"{button_type}: rejected fit with {inliers} inliers at ratio "
                f"{inlier_ratio:.2f} (need {MIN_INLIER_RATIO})"
            )
            return None

        span = self._inlier_span(template, inlier_pts)
        if span < MIN_INLIER_SPAN:
            logger.debug(
                f"{button_type}: rejected fit covering {span:.2f} of the label "
                f"width (need {MIN_INLIER_SPAN})"
            )
            return None

        validated = self._validate_transform(transform)
        if validated is None:
            logger.debug(f"{button_type}: rejected implausible transform")
            return None
        scale, rotation_deg = validated

        cx, cy = self._project_center(transform, template.width, template.height)
        cx += offset_x
        cy += offset_y

        # Appearance check: a geometrically coherent fit can still be a shared
        # text fragment inside a different button (e.g. "download" glyphs).
        # ``scene_gray`` is the whole frame, not the search box: a button the
        # box slices through is compared in full.
        verify_score = self._verify_match(template, scene_gray, cx, cy, scale)
        if verify_score < VERIFY_NCC_THRESHOLD:
            logger.debug(
                f"{button_type}: rejected fit, appearance check scored "
                f"{verify_score:.2f} (need {VERIFY_NCC_THRESHOLD})"
            )
            return None

        ends_score = self._label_ends_score(
            template,
            scene_gray,
            cx - template.width * scale / 2,
            cy - template.height * scale / 2,
            scale,
        )
        if ends_score < END_NCC_THRESHOLD:
            logger.debug(
                f"{button_type}: rejected fit, one end of the label scored "
                f"{ends_score:.2f} (need {END_NCC_THRESHOLD})"
            )
            return None

        confidence = min(
            1.0, 0.6 * min(inliers / (2 * min_inliers), 1.0) + 0.4 * inlier_ratio
        )

        logger.info(
            f"Detected {button_type} at ({int(cx)}, {int(cy)}) with {inliers} inliers "
            f"(scale {scale:.2f}, rot {rotation_deg:.1f} deg, "
            f"confidence: {confidence:.2f}, ends {ends_score:.2f})"
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

    @staticmethod
    def _gradient_magnitude(gray: npt.NDArray[np.uint8]) -> npt.NDArray[np.float32]:
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
        # Softening the edge maps absorbs the sub-pixel edge misalignment that
        # resampled thin text produces (true wabbajack@0.65 scores 0.95 blurred
        # vs 0.80 raw) while structurally different regions stay low (~0.64).
        return cv2.GaussianBlur(cv2.magnitude(gx, gy), (3, 3), 0)

    def _gradient_score(
        self,
        template: TemplateCandidate,
        scene_gray: npt.NDArray[np.uint8],
        peak_xy: tuple[int, int],
        scale: float,
        sw: int,
        sh: int,
    ) -> float:
        """Gradient (edge) NCC around an intensity peak.

        Intensity NCC is contrast-normalized, so generic dark-rect-with-text UI
        regions can impersonate small button templates; their edge structure
        does not. A small search pad absorbs peak/rung misalignment.
        """
        pad = VERIFY_SEARCH_PAD
        x1, y1 = max(0, peak_xy[0] - pad), max(0, peak_xy[1] - pad)
        x2 = min(scene_gray.shape[1], peak_xy[0] + sw + pad)
        y2 = min(scene_gray.shape[0], peak_xy[1] + sh + pad)
        if x2 - x1 < sw or y2 - y1 < sh:
            return -1.0
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(template.gray, (sw, sh), interpolation=interpolation)
        result = cv2.matchTemplate(
            self._gradient_magnitude(scene_gray[y1:y2, x1:x2]),
            self._gradient_magnitude(resized),
            cv2.TM_CCOEFF_NORMED,
        )
        return float(result.max())

    def _best_ncc_peak(
        self, template: TemplateCandidate, scene_gray: npt.NDArray[np.uint8]
    ) -> tuple[float, tuple[int, int], float, int, int] | None:
        """Strongest correlation peak of a template over the scale ladder.

        The ladder is the expensive part of a miss (eight full-frame passes),
        and two button types can share a picture, so the result is kept for
        the frame being searched (see ``_scene_for``).
        """
        cached = self._ncc_peaks.get(id(template))
        if cached is not None:
            return cached[0]

        scene_h, scene_w = scene_gray.shape[:2]
        best: tuple[float, tuple[int, int], float, int, int] | None = None
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

        self._ncc_peaks[id(template)] = (best,)
        return best

    def _scene_for(
        self, img: npt.NDArray[np.uint8], box: tuple[int, int, int, int]
    ) -> tuple[npt.NDArray[np.uint8], npt.NDArray[np.uint8], Any, Any]:
        """``(full_gray, gray, keypoints, descriptors)`` for a search box.

        The scanner asks for several button types on one capture, and SIFT on
        a full frame is the bulk of each call, so the features of the last
        frame are kept. The frame is recognised by identity; the reference
        held here keeps that identity from being handed to another array.
        Callers must not modify a frame between calls.
        """
        cached = self._scene
        if cached is not None and cached[0] is img and cached[1] == box:
            return cached[2]

        x1, y1, x2, y2 = box
        full_gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        gray = full_gray[y1:y2, x1:x2]
        kps, des = self.sift.detectAndCompute(gray, mask=None)
        scene = (full_gray, gray, kps, des)
        self._scene = (img, box, scene)
        self._ncc_peaks = {}
        return scene

    def _match_ncc(
        self,
        template: TemplateCandidate,
        scene_gray: npt.NDArray[np.uint8],
        button_type: ButtonType,
        offset_x: int,
        offset_y: int,
    ) -> DetectionResult | None:
        """Multi-scale normalized cross-correlation fallback."""
        best = self._best_ncc_peak(template, scene_gray)

        if best is None or best[0] < TM_BAND_THRESHOLD:
            if best is not None:
                logger.debug(
                    f"{button_type}: NCC peak {best[0]:.2f} below {TM_BAND_THRESHOLD}"
                )
            return None

        peak, (px, py), scale, sw, sh = best

        if peak < TM_STRONG_THRESHOLD:
            grad = self._gradient_score(template, scene_gray, (px, py), scale, sw, sh)
            if grad < TM_GRAD_THRESHOLD:
                logger.debug(
                    f"{button_type}: NCC band peak {peak:.2f} rejected, "
                    f"gradient score {grad:.2f} (need {TM_GRAD_THRESHOLD})"
                )
                return None

        ends_score = self._label_ends_score(template, scene_gray, px, py, scale)
        if ends_score < END_NCC_THRESHOLD:
            logger.debug(
                f"{button_type}: NCC peak {peak:.2f} rejected, one end of the "
                f"label scored {ends_score:.2f} (need {END_NCC_THRESHOLD})"
            )
            return None
        label_score = self._best_label_score(
            template, scene_gray, px + sw / 2.0, py + sh / 2.0, scale
        )
        if label_score is not None and label_score < LABEL_NCC_THRESHOLD:
            logger.debug(
                f"{button_type}: NCC peak {peak:.2f} rejected, the label itself "
                f"scored {label_score:.2f} (need {LABEL_NCC_THRESHOLD})"
            )
            return None
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
        bbox: BoundingBox | None = None,
    ) -> DetectionResult | None:
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

        # Keypoints come from the search box only; the whole frame stays at
        # hand for the appearance check.
        full_gray, gray, kps, des = self._scene_for(
            img,
            (
                offset_x,
                offset_y,
                offset_x + img_to_search.shape[1],
                offset_y + img_to_search.shape[0],
            ),
        )

        best_result: DetectionResult | None = None
        if des is not None and len(kps) > 0:
            for template in template_candidates:
                result: DetectionResult | None = self._match_sift(
                    template,
                    kps,
                    des,
                    full_gray,
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
