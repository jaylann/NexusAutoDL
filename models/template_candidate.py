"""Template metadata for button detection."""

from dataclasses import dataclass

from numpy import float32
from numpy.typing import NDArray


@dataclass(frozen=True)
class TemplateCandidate:
    """Descriptor metadata for drawing debug info."""

    desc: NDArray[float32]
    width: int
    height: int
