"""Monitor-topology specs for the display rig (pure Python, any OS).

A topology string lists monitors separated by ``;``. Each monitor is
``WIDTHxHEIGHT@X,Y`` with an optional ``:SCALE`` percent, e.g.::

    1920x1080@0,0:100;1920x1080@1920,0:150     two 1080p, right one at 150 %
    1920x1080@0,0;1920x1080@-1920,0            secondary left of primary

Positions are virtual-desktop coordinates in physical pixels, as Windows'
display settings store them. Exactly one monitor sits at ``0,0``; it becomes the
primary display.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Scale steps Windows offers (Settings > Display > Scale).
SCALE_STEPS: tuple[int, ...] = (
    100,
    125,
    150,
    175,
    200,
    225,
    250,
    300,
    350,
    400,
    450,
    500,
)

_MONITOR_RE = re.compile(r"^(\d+)x(\d+)@(-?\d+),(-?\d+)(?::(\d+))?$")


@dataclass(frozen=True)
class MonitorSpec:
    """One monitor of a topology."""

    width: int
    height: int
    x: int
    y: int
    scale: Optional[int] = None  # percent; None = leave as is

    @property
    def is_primary(self) -> bool:
        return self.x == 0 and self.y == 0

    def overlaps(self, other: "MonitorSpec") -> bool:
        return (
            self.x < other.x + other.width
            and other.x < self.x + self.width
            and self.y < other.y + other.height
            and other.y < self.y + self.height
        )

    def __str__(self) -> str:
        base = f"{self.width}x{self.height}@{self.x},{self.y}"
        return f"{base}:{self.scale}" if self.scale is not None else base


def parse_topology(spec: str) -> list[MonitorSpec]:
    """Parse and validate a topology string (see module docstring).

    Raises:
        ValueError: on syntax errors, unknown scales, overlapping monitors, or
            not exactly one monitor at ``0,0``.
    """
    monitors: list[MonitorSpec] = []
    for raw in spec.split(";"):
        part = raw.strip()
        if not part:
            continue
        match = _MONITOR_RE.match(part)
        if not match:
            raise ValueError(f"Bad monitor spec {part!r}; expected WxH@X,Y[:SCALE]")
        width, height, x, y = (int(g) for g in match.groups()[:4])
        scale = int(match.group(5)) if match.group(5) else None
        if width <= 0 or height <= 0:
            raise ValueError(f"Monitor {part!r} needs a positive size")
        if scale is not None and scale not in SCALE_STEPS:
            raise ValueError(f"Scale {scale}% in {part!r} is not one of {SCALE_STEPS}")
        monitors.append(MonitorSpec(width, height, x, y, scale))

    if not monitors:
        raise ValueError("Topology is empty")
    primaries = [m for m in monitors if m.is_primary]
    if len(primaries) != 1:
        raise ValueError("Exactly one monitor must sit at 0,0 (the primary)")
    for i, first in enumerate(monitors):
        for second in monitors[i + 1 :]:
            if first.overlaps(second):
                raise ValueError(f"Monitors {first} and {second} overlap")
    return monitors


def format_topology(monitors: list[MonitorSpec]) -> str:
    """Inverse of ``parse_topology``."""
    return ";".join(str(m) for m in monitors)


def match_rects(expected: list[MonitorSpec], actual: list[dict[str, int]]) -> list[str]:
    """Compare expected monitors with MSS-style rects; return mismatch messages.

    ``actual`` items carry ``left``, ``top``, ``width``, ``height`` (physical
    pixels, as ``mss.mss().monitors[1:]`` returns them). Order is ignored.
    """
    problems: list[str] = []
    remaining = list(actual)
    for spec in expected:
        hit = next(
            (
                r
                for r in remaining
                if (r["left"], r["top"], r["width"], r["height"])
                == (spec.x, spec.y, spec.width, spec.height)
            ),
            None,
        )
        if hit is None:
            problems.append(f"missing monitor {spec}")
        else:
            remaining.remove(hit)
    for extra in remaining:
        problems.append(
            "unexpected monitor "
            f"{extra['width']}x{extra['height']}@{extra['left']},{extra['top']}"
        )
    return problems
