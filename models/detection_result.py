"""Detection result model."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from models.button_type import ButtonType


class DetectionResult(BaseModel):
    """Result of button detection.

    ``num_matches`` reports RANSAC inliers on the SIFT path and 0 on the
    template-matching fallback path.
    """

    button_type: ButtonType
    x: int
    y: int
    confidence: float = Field(ge=0.0, le=1.0)
    num_matches: int = Field(ge=0)
    template_width: Optional[int] = Field(default=None, ge=1)
    template_height: Optional[int] = Field(default=None, ge=1)
    scale: Optional[float] = Field(default=None, gt=0)
    inliers: Optional[int] = Field(default=None, ge=0)
    method: Literal["sift", "template"] = "sift"

    class Config:
        frozen = True
