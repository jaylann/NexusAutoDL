"""Scan status model."""

from __future__ import annotations

from pydantic import BaseModel, Field

from models.scan_state import ScanState
from models.detection_result import DetectionResult


class ScanStatus(BaseModel):
    """Current status of scanning operation."""

    state: ScanState = ScanState.IDLE
    current_action: str = "Initializing..."
    detections: list[DetectionResult] = Field(default_factory=list)
    clicks_count: int = 0
    errors: list[str] = Field(default_factory=list)
    web_retry_count: int = 0

    class Config:
        use_enum_values = True
