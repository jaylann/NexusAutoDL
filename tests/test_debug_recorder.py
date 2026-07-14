"""DebugRecorder annotation tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from models import ButtonType, DetectionResult
from services.debug_recorder import DebugRecorder


def _detection(**overrides) -> DetectionResult:
    values = dict(
        button_type=ButtonType.VORTEX,
        x=200,
        y=150,
        confidence=0.9,
        num_matches=12,
        template_width=100,
        template_height=40,
    )
    values.update(overrides)
    return DetectionResult(**values)


def test_disabled_recorder_writes_nothing(tmp_path: Path) -> None:
    recorder = DebugRecorder(None)
    recorder.record(np.zeros((300, 400, 3), np.uint8), _detection(), 1, "x")
    assert list(tmp_path.iterdir()) == []


def test_record_writes_annotated_frame(tmp_path: Path) -> None:
    recorder = DebugRecorder(tmp_path)
    img = np.zeros((300, 400, 3), np.uint8)
    recorder.record(img, _detection(), 3, "vortex_download")
    out = tmp_path / "frame_000003_vortex_download.png"
    assert out.exists()

    import cv2

    written = cv2.imread(str(out))
    assert written.any(), "annotation must draw visible pixels"


def test_scaled_detection_near_edge_does_not_crash(tmp_path: Path) -> None:
    recorder = DebugRecorder(tmp_path)
    img = np.zeros((300, 400, 3), np.uint8)
    recorder.record(img, _detection(x=395, y=5, scale=2.0), 1, "edge")
    assert (tmp_path / "frame_000001_edge.png").exists()
