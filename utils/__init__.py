"""Utilities for NexusAutoDL."""

from utils.platform import IS_WINDOWS, user32, win32api, win32con, win32gui
from utils.simulator import SimulatedScanner, get_simulated_monitors

__all__ = [
    "IS_WINDOWS",
    "SimulatedScanner",
    "get_simulated_monitors",
    "user32",
    "win32api",
    "win32con",
    "win32gui",
]
