"""
Debug frame recording utilities.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import cv2
from numpy import uint8
from numpy.typing import NDArray

from models import DetectionResult
from utils.constants import DEFAULT_DEBUG_HEIGHT, DEFAULT_DEBUG_WIDTH
from utils.logger import get_logger

logger = get_logger(__name__)


class DebugRecorder:
    """Writes annotated detection frames for troubleshooting."""

    def __init__(self, output_dir: Path | None) -> None:
        self.output_dir: Path | None = output_dir
        if self.output_dir:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Debug frames will be saved to {self.output_dir}")

    def record(
        self,
        image: NDArray[uint8],
        detection: DetectionResult,
        iteration: int,
        label: str,
    ) -> None:
        """Save an annotated frame if debug directory configured."""
        if not self.output_dir:
            return

        annotated: NDArray[uint8] = image.copy()
        self._draw_detection_box(annotated, detection, label)
        filename: str = f"frame_{iteration:06d}_{label}.png"
        output_path: Path = self.output_dir / filename
        cv2.imwrite(
            str(output_path),
            cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR),
        )
        logger.debug(f"Wrote debug frame to {output_path}")

    def _draw_detection_box(
        self,
        image: NDArray[uint8],
        detection: DetectionResult,
        label: str,
    ) -> None:
        """Draw a bounding box and caption for a detection."""
        img_height, img_width = image.shape[:2]
        scale: float = detection.scale or 1.0
        box_width: int = int((detection.template_width or DEFAULT_DEBUG_WIDTH) * scale)
        box_height: int = int(
            (detection.template_height or DEFAULT_DEBUG_HEIGHT) * scale
        )
        half_w: int = box_width // 2
        half_h: int = box_height // 2

        x1: int = max(int(detection.x - half_w), 0)
        y1: int = max(int(detection.y - half_h), 0)
        x2: int = min(int(detection.x + half_w), img_width - 1)
        y2: int = min(int(detection.y + half_h), img_height - 1)

        color: Tuple[int, int, int] = (0, 255, 0)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        text: str = (
            f"{label} | {detection.button_type.value} | "
            f"conf={detection.confidence:.2f} | matches={detection.num_matches}"
        )
        text_pos: Tuple[int, int] = (x1, max(15, y1 - 10))
        cv2.putText(
            image,
            text,
            text_pos,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )
