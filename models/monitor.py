"""Monitor configuration model."""

from pydantic import BaseModel, Field


class Monitor(BaseModel):
    """Monitor configuration."""

    x: int = Field(description="X coordinate of monitor")
    y: int = Field(description="Y coordinate of monitor")
    width: int = Field(gt=0, description="Width in pixels")
    height: int = Field(gt=0, description="Height in pixels")

    @property
    def aspect_ratio(self) -> float:
        """Calculate aspect ratio."""
        return self.width / self.height

    class Config:
        frozen = True
