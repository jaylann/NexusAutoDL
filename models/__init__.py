"""
Pydantic models and enums for type-safe data structures.
"""

from models.app_config import AppConfig
from models.bounding_box import BoundingBox
from models.browser_type import BrowserType
from models.button_assets import ButtonAssets
from models.button_type import ButtonType
from models.detection_result import DetectionResult
from models.monitor import Monitor
from models.scan_state import ScanState
from models.scan_status import ScanStatus
from models.template_candidate import TemplateCandidate

__all__ = [
    "AppConfig",
    "BoundingBox",
    "BrowserType",
    "ButtonAssets",
    "ButtonType",
    "DetectionResult",
    "Monitor",
    "ScanState",
    "ScanStatus",
    "TemplateCandidate",
]
