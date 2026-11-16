"""Template metadata for button detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
from numpy import float32, uint8
from numpy.typing import NDArray


@dataclass(frozen=True)
class TemplateCandidate:
    """Precomputed matching data for a single template image."""

    kps: tuple[cv2.KeyPoint, ...]
    desc: Optional[NDArray[float32]]
    gray: NDArray[uint8]
    width: int
    height: int
    name: str = ""
