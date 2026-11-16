"""Shared fixtures for the detection test harness.

Fixtures are loaded exactly the way production does (``cv2.imread`` then
``BGR2RGB`` -- see ``tests/harness.read_rgb`` / ``services/button_detector``),
so the arrays fed to ``ButtonDetector.detect`` match the live capture pipeline.
"""

from __future__ import annotations

import pytest

from models import AppConfig
from services.button_detector import ButtonDetector
from tests.harness import ASSETS_PATH


def pytest_configure(config: pytest.Config) -> None:
    """Bootstrap synthetic fixtures so CI needn't commit PNGs.

    Real fixtures (``real/``) are committed by contributors; the deterministic
    synthetic set is (re)generated whenever its images are missing or were
    written by an older generator. Generation preserves any real entries
    already in ``cases.json``.
    """
    from tests._generate_synthetic import ensure_fixtures

    ensure_fixtures()


@pytest.fixture(scope="session")
def detector() -> ButtonDetector:
    """Build the SIFT detector once per session (descriptor build is expensive)."""
    return ButtonDetector(ASSETS_PATH)


@pytest.fixture(scope="session")
def app_defaults() -> AppConfig:
    """Default detection thresholds (``min_matches`` / ``ratio_threshold``)."""
    return AppConfig()
