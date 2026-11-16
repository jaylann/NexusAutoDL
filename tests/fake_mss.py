"""Fake mss implementation for cross-platform ScreenCapture tests."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


class FakeMSS:
    """Mimics ``mss.mss()``: a monitor list and a ``grab`` method.

    Each grab returns a BGRA image whose blue channel encodes the requested
    region's identity, so tests can tell which monitor a frame came from.
    """

    def __init__(self, monitors: list[dict[str, int]]) -> None:
        self.monitors = monitors
        self.grabs: list[dict[str, int]] = []

    def grab(self, region: dict[str, int]) -> npt.NDArray[np.uint8]:
        self.grabs.append(dict(region))
        img = np.zeros((region["height"], region["width"], 4), dtype=np.uint8)
        img[:, :, 0] = (region["left"] + region["top"]) % 251  # blue channel tag
        img[:, :, 3] = 255
        return img
