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
from tests._generate_synthetic import (
    _background,
    _paste,
    half_painted_window,
    label_prefix_scene,
)
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
        self, detector: ButtonDetector, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Template matching takes over when the feature path finds nothing.
        # Which scenes starve SIFT shifts with its tuning, so it is switched
        # off here rather than relied on to fail.
        monkeypatch.setattr(detector, "_match_sift", lambda *args, **kwargs: None)
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

    @pytest.mark.parametrize(
        ("button_type", "asset"),
        [
            (ButtonType.VORTEX, "VortexDownloadButton.png"),  # old Vortex
            (ButtonType.VORTEX, "VortexDownloadButtonNew.png"),
            (ButtonType.WEBSITE, "WebsiteDownloadButton.png"),  # old site
            (ButtonType.WEBSITE, "WebsiteDownloadButtonNew.png"),
            (ButtonType.WEBSITE, "WabbajackDownloadButton.png"),  # 100 % capture
        ],
    )
    def test_every_picture_of_a_button_is_found_without_a_mode_switch(
        self, detector: ButtonDetector, button_type: ButtonType, asset: str
    ) -> None:
        # One detector, no --legacy: the user cannot be expected to know which
        # Vortex or Nexus design they have, and may have one old and one new.
        canvas = _background(1)
        center = _paste(canvas, read_rgb(ASSETS_PATH / asset), (480, 300), 1.0)
        result = detector.detect(canvas, button_type)
        assert result is not None
        assert abs(result.x - center[0]) <= 3 and abs(result.y - center[1]) <= 3

    def test_featureless_image_yields_nothing(self, detector: ButtonDetector) -> None:
        img = _background(2)
        for bt in ButtonType:
            assert detector.detect(img, bt) is None


class TestRejectsNonButtons:
    """Scenes that showed up as stray clicks on real screens or on the rig."""

    @pytest.mark.parametrize("scale", [0.85, 1.0, 1.25])
    @pytest.mark.parametrize("fraction", [0.45, 0.55])
    def test_text_that_only_starts_like_the_label(
        self, detector: ButtonDetector, fraction: float, scale: float
    ) -> None:
        img = label_prefix_scene(fraction, scale)
        assert detector.detect(img, ButtonType.VORTEX) is None

    @pytest.mark.parametrize("level", [0, 30, 43, 128, 255])
    def test_uniform_frame(self, detector: ButtonDetector, level: int) -> None:
        # A blank or not-yet-painted screen: no texture at all.
        img = np.full((720, 1280, 3), level, dtype=np.uint8)
        for bt in ButtonType:
            assert detector.detect(img, bt) is None

    def test_half_painted_window(self, detector: ButtonDetector) -> None:
        img = half_painted_window()
        for bt in ButtonType:
            assert detector.detect(img, bt) is None


class TestButtonsCutByAnEdge:
    """A button partly beyond the frame or the search box is still a button.

    Frames are per monitor and the Vortex search box is an inset of its
    window, so a button near either edge is seen cut off.
    """

    @pytest.mark.parametrize("cut", [0.05, 0.10, 0.25])
    @pytest.mark.parametrize(
        "button_type",
        [ButtonType.VORTEX, ButtonType.WEBSITE, ButtonType.WABBAJACK, ButtonType.CLICK],
    )
    def test_button_running_past_the_right_frame_edge(
        self, detector: ButtonDetector, button_type: ButtonType, cut: float
    ) -> None:
        from tests._generate_synthetic import _TEMPLATE_ASSET

        button = read_rgb(ASSETS_PATH / _TEMPLATE_ASSET[button_type])
        canvas = _background(0)
        center = _paste(canvas, button, (480, 300), 1.0)
        # Crop the frame so `cut` of the button's width lies beyond its edge.
        right_edge = center[0] + round(button.shape[1] * (0.5 - cut))
        frame = np.ascontiguousarray(canvas[:, :right_edge])

        result = detector.detect(frame, button_type)

        assert result is not None
        assert abs(result.x - center[0]) <= 4 and abs(result.y - center[1]) <= 4

    def test_button_sliced_by_the_search_box(self, detector: ButtonDetector) -> None:
        img, center = TestDetect()._scene(ButtonType.VORTEX)
        button_width = 421
        left = center[0] - button_width // 2 + round(0.08 * button_width)
        bbox = BoundingBox(x1=left, y1=200, x2=1200, y2=420)

        result = detector.detect(img, ButtonType.VORTEX, bbox=bbox)

        assert result is not None
        assert abs(result.x - center[0]) <= 4 and abs(result.y - center[1]) <= 4

    def test_sliver_of_a_button_is_not_enough(self, detector: ButtonDetector) -> None:
        # Less than half of it visible: too little to be sure, and the click
        # point would lie outside the frame.
        img, center = TestDetect()._scene(ButtonType.VORTEX)
        frame = np.ascontiguousarray(img[:, : center[0] - 60])
        assert detector.detect(frame, ButtonType.VORTEX) is None


class TestCandidateMatches:
    """Lowe's ratio test, relaxed for a label that is on screen twice."""

    @staticmethod
    def _group(*distances: float) -> list:
        import cv2

        return [cv2.DMatch(0, i, d) for i, d in enumerate(distances)]

    def test_unique_match_is_kept(self, detector: ButtonDetector) -> None:
        kept = detector._candidate_matches([self._group(10, 40, 50)], 0.75)
        assert [m.trainIdx for m in kept] == [0]

    def test_two_equal_matches_are_both_kept_when_they_beat_the_third(
        self, detector: ButtonDetector
    ) -> None:
        # The same word twice: plain Lowe drops the keypoint (10 vs 11).
        kept = detector._candidate_matches([self._group(10, 11, 50)], 0.75)
        assert [m.trainIdx for m in kept] == [0, 1]

    def test_three_similar_matches_are_noise(self, detector: ButtonDetector) -> None:
        assert detector._candidate_matches([self._group(10, 11, 12)], 0.75) == []

    def test_ambiguous_pair_without_a_third_is_dropped(
        self, detector: ButtonDetector
    ) -> None:
        assert detector._candidate_matches([self._group(10, 11)], 0.75) == []


class TestPerFrameCaching:
    """Several button types on one capture share the expensive work."""

    class _CountingSift:
        def __init__(self, sift) -> None:
            self._sift = sift
            self.calls = 0

        def detectAndCompute(self, *args, **kwargs):
            self.calls += 1
            return self._sift.detectAndCompute(*args, **kwargs)

    @pytest.fixture()
    def counting(self, detector: ButtonDetector, monkeypatch: pytest.MonkeyPatch):
        counter = self._CountingSift(detector.sift)
        monkeypatch.setattr(detector, "sift", counter)
        monkeypatch.setattr(detector, "_scene", None)
        return counter

    def test_features_are_computed_once_per_frame(
        self, detector: ButtonDetector, counting
    ) -> None:
        img = _background(2)
        for bt in (ButtonType.WEBSITE, ButtonType.WABBAJACK, ButtonType.CLICK):
            detector.detect(img, bt)
        assert counting.calls == 1

    def test_a_new_frame_is_searched_afresh(
        self, detector: ButtonDetector, counting
    ) -> None:
        blank = _background(2)
        assert detector.detect(blank, ButtonType.VORTEX) is None
        img, center = TestDetect()._scene(ButtonType.VORTEX)
        result = detector.detect(img, ButtonType.VORTEX)
        assert counting.calls == 2
        assert result is not None and abs(result.x - center[0]) <= 3

    def test_a_different_search_box_is_searched_afresh(
        self, detector: ButtonDetector, counting
    ) -> None:
        img, _ = TestDetect()._scene(ButtonType.VORTEX)
        assert detector.detect(img, ButtonType.VORTEX) is not None
        away = BoundingBox(x1=0, y1=0, x2=300, y2=200)
        assert detector.detect(img, ButtonType.VORTEX, bbox=away) is None
        assert counting.calls == 2
