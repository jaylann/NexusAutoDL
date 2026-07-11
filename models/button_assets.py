"""Button assets model."""

from typing import Optional

from numpy import uint8
from numpy.typing import NDArray
from pydantic import BaseModel


class ButtonAssets(BaseModel):
    """Container for button image assets."""

    vortex_img: NDArray[uint8]
    vortex_new_img: Optional[NDArray[uint8]] = None
    web_img: NDArray[uint8]
    web_new_img: Optional[NDArray[uint8]] = None
    wabbajack_img: NDArray[uint8]
    click_img: NDArray[uint8]
    understood_img: NDArray[uint8]
    staging_img: NDArray[uint8]

    class Config:
        arbitrary_types_allowed = True
