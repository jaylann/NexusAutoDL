"""Invariants of the synthetic fixture generator and its manifest."""

from __future__ import annotations

import numpy as np

from models import ButtonType
from tests import _generate_synthetic as gen
from tests.harness import case_image_path, load_cases, skip_reason


def test_case_records_are_deterministic() -> None:
    assert gen.generate() == gen.generate()


def test_images_are_byte_stable() -> None:
    first = {p.name: p.read_bytes() for p in gen.SYNTHETIC_DIR.glob("*.png")}
    gen.generate()
    second = {p.name: p.read_bytes() for p in gen.SYNTHETIC_DIR.glob("*.png")}
    assert first == second


def test_manifest_invariants() -> None:
    cases = [c for c in load_cases() if not skip_reason(c)]
    assert len(cases) >= 150

    seen_images = set()
    for case in cases:
        path = case_image_path(case)
        assert path.exists(), case["image"]
        seen_images.add(case["image"])

        if case["expect"] == "present":
            assert case["tolerance_px"] > 0
            for point in case.get("points") or [case["point"]]:
                res = case["meta"]["resolution"].split("x")
                assert 0 <= point[0] < int(res[0])
                assert 0 <= point[1] < int(res[1])
        else:
            assert "point" not in case

    # Every present button type appears in multiple families.
    variants_per_type: dict[str, set[str]] = {}
    for case in cases:
        if case["expect"] == "present":
            variants_per_type.setdefault(case["button_type"], set()).add(
                case["meta"]["variant"]
            )
    for bt, variants in variants_per_type.items():
        assert len(variants) >= 5, f"{bt} only covered by {variants}"


def test_no_cross_template_negative_pairs_equivalent_types() -> None:
    for case in load_cases():
        if case.get("meta", {}).get("variant") != "cross_template":
            continue
        # image is named after the type it CONTAINS: <type>_100.png
        contained = ButtonType(case["image"].split("/")[1].split("_")[0])
        asserted_absent = ButtonType(case["button_type"])
        assert not gen._equivalent(contained, asserted_absent), case


def test_background_styles_are_feature_rich_vs_flat() -> None:
    import cv2

    sift = cv2.SIFT_create()
    flat = cv2.cvtColor(gen._background(0), cv2.COLOR_RGB2GRAY)
    busy = cv2.cvtColor(gen._bg_ui_panel(3), cv2.COLOR_RGB2GRAY)
    noise = cv2.cvtColor(gen._bg_block_noise(7), cv2.COLOR_RGB2GRAY)

    kp_flat = len(sift.detect(flat, None))
    assert kp_flat == 0, "gradient background must stay keypoint-free"
    assert len(sift.detect(busy, None)) > 500
    assert len(sift.detect(noise, None)) > 500


def test_paste_at_fraction_keeps_button_inside_canvas() -> None:
    button = np.zeros((73, 466, 3), np.uint8)  # widest template
    for frac in gen._POSITION_POOL:
        for scale in gen._SCALES:
            canvas = gen._background(0)
            center = gen._paste_at_fraction(canvas, button, frac, scale)
            assert 0 <= center[0] < canvas.shape[1]
            assert 0 <= center[1] < canvas.shape[0]
