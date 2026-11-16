"""Unit tests for ButtonDetector internals and detect() behaviour."""

from __future__ import annotations

import math

import numpy as np
import pytest

from models import BoundingBox, ButtonType
from services.button_detector import (
    MAX_ROTATION_DEG,
    SCALE_RANGE,
    ButtonDetector,
)
from tests._generate_synthetic import _background, _paste
from tests.harness import ASSETS_PATH, read_rgb


def _affine(scale: float, rotation_deg: float, tx: float = 0.0, ty: float = 0.0):
    theta = math.radians(rotation_deg)
    return np.array(
        [
            [scale * math.cos(theta), -scale * math.sin(theta), tx],
            [scale * math.sin(theta), scale * math.cos(theta), ty],
        ],
        dtype=np.float64,
    )


class TestValidateTransform:
    def test_identity_accepted(self, detector: ButtonDetector) -> None:
        scale, rot = detector._validate_transform(_affine(1.0, 0.0))
        assert scale == pytest.approx(1.0)
        assert rot == pytest.approx(0.0)

    @pytest.mark.parametrize("scale", [SCALE_RANGE[0], 1.0, SCALE_RANGE[1]])
    def test_scale_window_inclusive(self, detector: ButtonDetector, scale) -> None:
        assert detector._validate_transform(_affine(scale, 0.0)) is not None

    @pytest.mark.parametrize(
        "scale", [0.05, SCALE_RANGE[0] - 0.05, SCALE_RANGE[1] + 0.1, 10.0]
    )
    def test_implausible_scale_rejected(self, detector: ButtonDetector, scale) -> None:
        assert detector._validate_transform(_affine(scale, 0.0)) is None

    def test_small_rotation_accepted(self, detector: ButtonDetector) -> None:
        assert (
            detector._validate_transform(_affine(1.0, MAX_ROTATION_DEG - 1)) is not None
        )

    @pytest.mark.parametrize("deg", [MAX_ROTATION_DEG + 1, 45.0, -30.0, 180.0])
    def test_rotation_rejected(self, detector: ButtonDetector, deg) -> None:
        assert detector._validate_transform(_affine(1.0, deg)) is None


class TestProjectCenter:
    def test_translation_only(self, detector: ButtonDetector) -> None:
        px, py = detector._project_center(_affine(1.0, 0.0, tx=100, ty=50), 40, 20)
        assert (px, py) == (120.0, 60.0)

    def test_uniform_scale(self, detector: ButtonDetector) -> None:
        px, py = detector._project_center(_affine(2.0, 0.0, tx=10, ty=10), 40, 20)
        assert (px, py) == (50.0, 30.0)


class TestDetect:
    def _scene(self, bt: ButtonType, scale: float = 1.0):
        from tests._generate_synthetic import _TEMPLATE_ASSET

        button = read_rgb(ASSETS_PATH / _TEMPLATE_ASSET[bt])
        canvas = _background(0)
        center = _paste(canvas, button, (480, 300), scale)
        return canvas, center

    def test_bbox_offsets_are_added_back(self, detector: ButtonDetector) -> None:
        img, center = self._scene(ButtonType.VORTEX)
        full = detector.detect(img, ButtonType.VORTEX)
        bbox = BoundingBox(x1=400, y1=250, x2=1100, y2=500)
        cropped = detector.detect(img, ButtonType.VORTEX, bbox=bbox)
        assert full is not None and cropped is not None
        assert abs(full.x - cropped.x) <= 1 and abs(full.y - cropped.y) <= 1
        assert abs(cropped.x - center[0]) <= 3

    def test_bbox_away_from_button_finds_nothing(
        self, detector: ButtonDetector
    ) -> None:
        img, _ = self._scene(ButtonType.VORTEX)
        bbox = BoundingBox(x1=0, y1=0, x2=300, y2=200)
        assert detector.detect(img, ButtonType.VORTEX, bbox=bbox) is None

    def test_bbox_fully_outside_image_is_rejected(
        self, detector: ButtonDetector
    ) -> None:
        img, _ = self._scene(ButtonType.VORTEX)
        bbox = BoundingBox(x1=2000, y1=1000, x2=3000, y2=2000)
        assert detector.detect(img, ButtonType.VORTEX, bbox=bbox) is None

    def test_sift_path_reports_method_and_inliers(
        self, detector: ButtonDetector
    ) -> None:
        img, _ = self._scene(ButtonType.VORTEX)
        result = detector.detect(img, ButtonType.VORTEX)
        assert result is not None
        assert result.method == "sift"
        assert result.inliers is not None and result.inliers >= 6
        assert result.num_matches == result.inliers
        assert result.scale == pytest.approx(1.0, abs=0.05)

    def test_fallback_path_reports_template_method(
        self, detector: ButtonDetector
    ) -> None:
        # 0.65x starves SIFT on the thin-text wabbajack template.
        img, center = self._scene(ButtonType.WABBAJACK, scale=0.65)
        result = detector.detect(img, ButtonType.WABBAJACK)
        assert result is not None
        assert result.method == "template"
        assert result.num_matches == 0
        assert result.scale == pytest.approx(0.65, abs=0.05)
        assert abs(result.x - center[0]) <= 5 and abs(result.y - center[1]) <= 5

    def test_recovered_scale_tracks_rendered_scale(
        self, detector: ButtonDetector
    ) -> None:
        img, _ = self._scene(ButtonType.VORTEX, scale=1.5)
        result = detector.detect(img, ButtonType.VORTEX)
        assert result is not None
        assert result.scale == pytest.approx(1.5, abs=0.05)

    def test_detect_multiple(self, detector: ButtonDetector) -> None:
        img, _ = self._scene(ButtonType.CLICK)
        results = detector.detect_multiple(img, [ButtonType.CLICK, ButtonType.VORTEX])
        assert [r.button_type for r in results] == [ButtonType.CLICK]

    def test_legacy_mode_uses_legacy_template(self) -> None:
        legacy_detector = ButtonDetector(ASSETS_PATH, use_legacy_buttons=True)
        button = read_rgb(ASSETS_PATH / "VortexDownloadButton.png")
        canvas = _background(1)
        center = _paste(canvas, button, (480, 300), 1.0)
        result = legacy_detector.detect(canvas, ButtonType.VORTEX)
        assert result is not None
        assert abs(result.x - center[0]) <= 5

    def test_featureless_image_yields_nothing(self, detector: ButtonDetector) -> None:
        img = _background(2)
        for bt in ButtonType:
            assert detector.detect(img, bt) is None


class TestRejectsNonButtons:
    """Scenes that showed up as stray clicks on real screens or on the rig."""

    def _label_prefix_scene(self, fraction: float, scale: float) -> np.ndarray:
        """The Vortex label cut off after ``fraction`` of its width.

        Stands for other text that starts like the label: Vortex titles the
        dialog holding the "Download manually" button "Download mod".
        """
        import cv2

        button = read_rgb(ASSETS_PATH / "VortexDownloadButtonNew.png")
        text_left, text_right = 110, 312  # label extent inside the template
        cut = text_left + round(fraction * (text_right - text_left))
        prefix = button[14:59, text_left - 8 : cut]
        fill = button[36, 40].tolist()

        height, width = prefix.shape[:2]
        prefix = cv2.resize(
            prefix,
            (round(width * scale), round(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
        canvas = _background(0)
        canvas[100:260, 300:1000] = fill  # a plain title bar in the button's fill
        y, x = 150, 380
        canvas[y : y + prefix.shape[0], x : x + prefix.shape[1]] = prefix
        return canvas

    @pytest.mark.parametrize("scale", [0.85, 1.0, 1.25])
    @pytest.mark.parametrize("fraction", [0.45, 0.55])
    def test_text_that_only_starts_like_the_label(
        self, detector: ButtonDetector, fraction: float, scale: float
    ) -> None:
        img = self._label_prefix_scene(fraction, scale)
        assert detector.detect(img, ButtonType.VORTEX) is None

    @pytest.mark.parametrize("level", [0, 30, 43, 128, 255])
    def test_uniform_frame(self, detector: ButtonDetector, level: int) -> None:
        # A blank or not-yet-painted screen: no texture at all.
        img = np.full((720, 1280, 3), level, dtype=np.uint8)
        for bt in ButtonType:
            assert detector.detect(img, bt) is None

    def test_half_painted_window(self, detector: ButtonDetector) -> None:
        # What the app captured right after maximising the Vortex window on
        # the rig: one painted rectangle in an otherwise black client area.
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        img[23:823, 0:1280] = (43, 45, 66)
        img[0:23, :] = (240, 240, 240)  # title bar
        for bt in ButtonType:
            assert detector.detect(img, bt) is None
