"""Scanner state-machine tests with a stubbed detector (mock win32 + FakeMSS)."""

from __future__ import annotations

import pytest

from models import AppConfig, ButtonType, DetectionResult, Monitor, ScanState
from services import scanner as scanner_module
from services import window_manager as wm_module
from services.screen_capture import ScreenCapture
from tests.fake_mss import FakeMSS
from tests.test_geometry import LAYOUTS
from utils import mock_win32
from utils.platform import IS_WINDOWS

pytestmark = pytest.mark.skipif(IS_WINDOWS, reason="asserts on the mock win32 backend")


class StubDetector:
    """Programmable detector double keyed by button type."""

    def __init__(self) -> None:
        self.responses: dict[ButtonType, DetectionResult | None] = {}
        self.calls: list[ButtonType] = []

    def detect(self, img, button_type, **kwargs):
        self.calls.append(button_type)
        return self.responses.get(button_type)


def _detection(bt: ButtonType) -> DetectionResult:
    return DetectionResult(button_type=bt, x=500, y=400, confidence=0.9, num_matches=10)


@pytest.fixture()
def make_scanner(monkeypatch: pytest.MonkeyPatch):
    """Factory building a Scanner on the single-monitor mock layout."""
    layout = LAYOUTS["single"]
    monitors = [
        Monitor(x=m["left"], y=m["top"], width=m["width"], height=m["height"])
        for m in layout[1:]
    ]
    mock_win32.configure(windows={"Vortex": (100, 100, 1820, 980)})
    monkeypatch.setattr(wm_module, "IS_WINDOWS", True)
    monkeypatch.setattr(
        scanner_module,
        "ScreenCapture",
        lambda mons, force_primary=False: ScreenCapture(
            mons, force_primary, mss_factory=lambda: FakeMSS(layout)
        ),
    )

    def build(**config_kwargs):
        config = AppConfig(click_delay=0.1, retry_delay=0.1, **config_kwargs)
        scanner = scanner_module.Scanner(config, monitors)
        stub = StubDetector()
        scanner.button_detector = stub
        return scanner, stub

    yield build
    mock_win32.reset()


def test_full_vortex_web_dialog_cycle(make_scanner) -> None:
    scanner, stub = make_scanner(vortex=True)
    stub.responses[ButtonType.VORTEX] = _detection(ButtonType.VORTEX)
    stub.responses[ButtonType.WEBSITE] = _detection(ButtonType.WEBSITE)

    # iter1: vortex clicked; iter2: website clicked; iter3: dialog skipped
    # (non-legacy) -> state machine resets for the next mod.
    scanner.scan_loop(max_iterations=3)

    assert scanner.status.clicks_count == 2
    assert [d.button_type for d in scanner.status.detections] == [
        ButtonType.VORTEX,
        ButtonType.WEBSITE,
    ]
    assert scanner.status.state == ScanState.WAITING_FOR_VORTEX


def test_web_retry_falls_back_to_vortex_search(make_scanner) -> None:
    scanner, stub = make_scanner(vortex=True)
    stub.responses[ButtonType.VORTEX] = _detection(ButtonType.VORTEX)
    stub.responses[ButtonType.WEBSITE] = None

    # iter1 clicks vortex; iters 2-4 exhaust VORTEX_WEB_RETRY_LIMIT=3 web
    # attempts; iter5 resets to vortex search.
    scanner.scan_loop(max_iterations=5)

    assert scanner.status.clicks_count == 1
    assert scanner.status.web_retry_count == 0
    assert scanner.status.state == ScanState.WAITING_FOR_VORTEX


def test_vortex_waits_when_window_missing(make_scanner) -> None:
    mock_win32.configure(windows={})  # no Vortex window at all
    scanner, stub = make_scanner(vortex=True)
    stub.responses[ButtonType.VORTEX] = _detection(ButtonType.VORTEX)

    scanner.scan_loop(max_iterations=1)

    assert scanner.status.clicks_count == 0
    assert stub.calls == []  # detector never consulted without a window bbox
    assert "Vortex window" in scanner.status.current_action


def test_legacy_popup_clicked_before_vortex(make_scanner) -> None:
    scanner, stub = make_scanner(vortex=True, legacy=True)
    stub.responses[ButtonType.UNDERSTOOD] = _detection(ButtonType.UNDERSTOOD)
    stub.responses[ButtonType.VORTEX] = _detection(ButtonType.VORTEX)

    scanner.scan_loop(max_iterations=2)

    # Popup is handled every iteration without ever claiming vortex_found:
    # the handler returns False so the vortex search restarts next sweep.
    assert scanner.status.clicks_count == 2
    assert all(
        d.button_type == ButtonType.UNDERSTOOD for d in scanner.status.detections
    )
    assert scanner.status.state == ScanState.HANDLING_POPUP


def test_wabbajack_mode_clicks_and_resets_each_iteration(make_scanner) -> None:
    scanner, stub = make_scanner(vortex=False)
    stub.responses[ButtonType.WABBAJACK] = _detection(ButtonType.WABBAJACK)

    scanner.scan_loop(max_iterations=2)

    assert scanner.status.clicks_count == 2
    assert all(d.button_type == ButtonType.WABBAJACK for d in scanner.status.detections)
    # WEBSITE is probed before WABBAJACK on every sweep.
    assert stub.calls[0] == ButtonType.WEBSITE


def test_website_checked_before_wabbajack(make_scanner) -> None:
    scanner, stub = make_scanner(vortex=False)
    stub.responses[ButtonType.WEBSITE] = _detection(ButtonType.WEBSITE)
    stub.responses[ButtonType.WABBAJACK] = _detection(ButtonType.WABBAJACK)

    scanner.scan_loop(max_iterations=1)

    assert scanner.status.clicks_count == 1
    assert scanner.status.detections[0].button_type == ButtonType.WEBSITE


def test_vortex_window_off_every_scanned_monitor_is_reported(make_scanner) -> None:
    # Minimised windows report a rect far outside the desktop.
    scanner, stub = make_scanner(vortex=True)
    mock_win32.configure(windows={"Vortex": (-32000, -32000, -31840, -31972)})
    stub.responses[ButtonType.VORTEX] = _detection(ButtonType.VORTEX)

    scanner.scan_loop(max_iterations=2)

    assert scanner.status.current_action == "Vortex window is not on a scanned monitor"
    assert stub.calls == []
    assert scanner.status.clicks_count == 0


def test_legacy_mode_does_not_wait_forever_for_the_click_here_link(
    make_scanner,
) -> None:
    # Today's Nexus site starts the download itself; the old "click here" link
    # never shows. With --legacy the app must give up on it and go on to the
    # next mod instead of stalling after the first one.
    scanner, stub = make_scanner(vortex=True, legacy=True)
    stub.responses[ButtonType.VORTEX] = _detection(ButtonType.VORTEX)
    stub.responses[ButtonType.WEBSITE] = _detection(ButtonType.WEBSITE)

    scanner.scan_loop(max_iterations=2 + scanner_module.CLICK_DIALOG_RETRY_LIMIT + 2)

    # Vortex, website, a bounded wait for the link, then Vortex again.
    assert stub.calls.count(ButtonType.CLICK) == scanner_module.CLICK_DIALOG_RETRY_LIMIT
    assert scanner.status.clicks_count >= 3
    assert [c for c in stub.calls if c is ButtonType.VORTEX][1:] != []
