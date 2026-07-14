"""ScreenCapture tests over odd monitor layouts (via FakeMSS)."""

from __future__ import annotations

from models import Monitor
from services.screen_capture import ScreenCapture
from tests.fake_mss import FakeMSS
from tests.test_geometry import LAYOUTS


def _capture(layout_name: str, **kwargs) -> tuple[ScreenCapture, FakeMSS]:
    layout = LAYOUTS[layout_name]
    fake = FakeMSS(layout)
    physical = layout[1:] if len(layout) > 1 else layout
    monitors = [
        Monitor(x=m["left"], y=m["top"], width=m["width"], height=m["height"])
        for m in physical
    ]
    capture = ScreenCapture(monitors, mss_factory=lambda: fake, **kwargs)
    return capture, fake


def test_captures_one_frame_per_monitor() -> None:
    capture, fake = _capture("mixed_triple")
    frames = capture.capture_frames()
    assert len(frames) == 3
    for captured in frames:
        assert captured.image.shape == (captured.frame.height, captured.frame.width, 3)
    # Each grab requested that monitor's own physical region.
    assert fake.grabs == [
        {"left": f.left, "top": f.top, "width": f.width, "height": f.height}
        for f in capture.desktop.frames
    ]


def test_negative_origin_frame_geometry() -> None:
    capture, _ = _capture("left_of_primary")
    frames = capture.capture_frames()
    secondary = next(c for c in frames if c.frame.left == -1920)
    assert secondary.frame.to_virtual(0, 0) == (-1920, 0)
    assert secondary.frame.from_virtual(-1, 0) == (1919, 0)


def test_force_primary_captures_only_origin_monitor() -> None:
    capture, fake = _capture("left_of_primary", force_primary=True)
    frames = capture.capture_frames()
    assert len(frames) == 1
    assert frames[0].frame.is_primary
    assert fake.grabs == [{"left": 0, "top": 0, "width": 1920, "height": 1080}]


def test_portrait_monitor_native_resolution() -> None:
    capture, _ = _capture("portrait_right")
    frames = capture.capture_frames()
    portrait = next(c for c in frames if c.frame.width == 1080)
    assert portrait.image.shape == (1920, 1080, 3)
    assert portrait.frame.top == -400


def test_frames_convert_bgra_to_rgb() -> None:
    capture, _ = _capture("non_contiguous")
    frames = capture.capture_frames()
    tagged = next(c for c in frames if c.frame.left == 2500)
    # FakeMSS writes its tag into the BGRA blue channel; after conversion to
    # RGB it must sit in channel 2, with red (channel 0) empty.
    assert int(tagged.image[0, 0, 2]) == (2500 + 200) % 251
    assert int(tagged.image[0, 0, 0]) == 0
