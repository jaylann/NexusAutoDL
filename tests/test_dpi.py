"""DPI awareness setup tests."""

from __future__ import annotations

import pytest

from utils.dpi import DpiAwareness, ensure_dpi_awareness
from utils.platform import IS_WINDOWS


def test_idempotent() -> None:
    assert ensure_dpi_awareness() == ensure_dpi_awareness()


@pytest.mark.skipif(IS_WINDOWS, reason="Windows-only assertion below")
def test_non_windows_short_circuits() -> None:
    assert ensure_dpi_awareness() is DpiAwareness.NOT_WINDOWS


@pytest.mark.skipif(not IS_WINDOWS, reason="requires real win32 DPI APIs")
def test_windows_achieves_awareness() -> None:
    # Any supported Windows (8.1+) must reach at least system awareness.
    assert ensure_dpi_awareness() in (
        DpiAwareness.PER_MONITOR_V2,
        DpiAwareness.PER_MONITOR,
        DpiAwareness.SYSTEM,
    )
