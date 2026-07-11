"""Scanner coordinate-path integration tests (mock win32 + FakeMSS)."""

from __future__ import annotations

import pytest

from models import AppConfig, ButtonType, DetectionResult, Monitor
from services import scanner as scanner_module
from services import window_manager as wm_module
from services.screen_capture import ScreenCapture
from tests.fake_mss import FakeMSS
from tests.test_geometry import LAYOUTS
from utils.platform import IS_WINDOWS
from utils import mock_win32

pytestmark = pytest.mark.skipif(IS_WINDOWS, reason="asserts on the mock win32 backend")


@pytest.fixture()
def scanner(monkeypatch: pytest.MonkeyPatch):
    """Scanner wired to the left-of-primary layout with Vortex on the left."""
    layout = LAYOUTS["left_of_primary"]
    monitors = [
        Monitor(x=m["left"], y=m["top"], width=m["width"], height=m["height"])
        for m in layout[1:]
    ]
    mock_win32.configure(
        monitors=[
            (m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"])
            for m in layout[1:]
        ],
        windows={"Vortex": (-1820, 100, -100, 980)},
    )

    monkeypatch.setattr(wm_module, "IS_WINDOWS", True)
    monkeypatch.setattr(
        scanner_module,
        "ScreenCapture",
        lambda mons, force_primary=False: ScreenCapture(
            mons, force_primary, mss_factory=lambda: FakeMSS(layout)
        ),
    )
    yield scanner_module.Scanner(AppConfig(vortex=True), monitors)
    mock_win32.reset()


def _detection() -> DetectionResult:
    return DetectionResult(
        button_type=ButtonType.VORTEX, x=500, y=400, confidence=0.9, num_matches=10
    )


def test_click_detection_converts_frame_to_virtual(scanner) -> None:
    left_frame = next(f for f in scanner.screen_capture.desktop.frames if f.left < 0)
    mock_win32.state.events = []

    assert scanner._click_detection(_detection(), left_frame)

    down = next(e for e in mock_win32.state.events if e[0] == "mouse")
    assert down[2] == (-1920 + 500, 0 + 400)
    assert scanner.status.clicks_count == 1


def test_scan_iteration_covers_all_monitors(scanner) -> None:
    fake = scanner.screen_capture.screen
    scanner.config = scanner.config.model_copy(update={"click_delay": 0.1})
    scanner.scan_loop(max_iterations=1)
    # Both monitors were captured in the sweep (blank frames -> no clicks).
    assert len(fake.grabs) == 2
    assert scanner.status.clicks_count == 0


def test_vortex_search_skips_frames_without_the_window(scanner) -> None:
    frames = scanner.screen_capture.capture_frames()
    primary = next(c for c in frames if c.frame.is_primary)
    # Vortex sits on the left monitor; the primary frame must be skipped
    # (returns False without ever consulting the detector).
    assert scanner._handle_vortex_state(primary, iteration=1) is False
