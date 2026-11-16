"""Arrange the monitors into a topology (Windows-only, run as the desktop user).

Applies resolution + position with ``ChangeDisplaySettingsEx`` (one batched
registry update, then a single apply), detaches displays the layout does not
use, then sets each monitor's scale via ``tools.win_display.set_scale``.
Finally it reads everything back and exits non-zero if the result differs.

    python tools/ci/set_display_layout.py --layout "1920x1080@0,0:100;1920x1080@1920,0:150"

Which display gets which layout entry (in GDI order, ``\\\\.\\DISPLAYn``):

* default (CI runner): only the Virtual Display Driver monitors; every other
  display (the runner's Hyper-V adapter) is detached.
* ``--include-others`` (test VM): the already-attached real displays come
  first, then the virtual ones. On a Hyper-V VM this keeps the Hyper-V display
  as the primary, so VMConnect still shows a desktop.
* ``--keep-others``: never detach a real display, even if no entry uses it.

Topology syntax: ``tools/ci/topology.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.ci.topology import MonitorSpec, match_rects, parse_topology

# Adapter / monitor strings the Virtual Display Driver reports (adapter "Virtual
# Display Driver", monitor "VDD by MTT", hardware id ...MttVDD / MTT1337).
DEFAULT_VIRTUAL_MATCH = r"(?i)virtual display|mttvdd|vdd by mtt|mtt1337"

_DM_POSITION = 0x00000020
_DM_BITSPERPEL = 0x00040000
_DM_PELSWIDTH = 0x00080000
_DM_PELSHEIGHT = 0x00100000
_DM_DISPLAYFREQUENCY = 0x00400000
_CDS_UPDATEREGISTRY = 0x00000001
_CDS_SET_PRIMARY = 0x00000010
_CDS_NORESET = 0x10000000
_DISP_CHANGE_SUCCESSFUL = 0


def _gdi_index(name: str) -> int:
    digits = re.findall(r"\d+", name)
    return int(digits[-1]) if digits else 0


def _queue_mode(name: str, width: int, height: int, x: int, y: int, flags: int) -> int:
    """Queue a mode change for one display in the registry (no apply yet).

    A 0x0 size detaches the display. Attaching passes a full mode (32 bpp,
    60 Hz -- the refresh rate the VDD settings offer), since some drivers reject
    a size-only mode for a display that was not attached before.
    """
    import pywintypes
    import win32api

    devmode = pywintypes.DEVMODEType()
    devmode.PelsWidth = width
    devmode.PelsHeight = height
    devmode.Position_x = x
    devmode.Position_y = y
    devmode.Fields = _DM_PELSWIDTH | _DM_PELSHEIGHT | _DM_POSITION
    if width and height:
        devmode.BitsPerPel = 32
        devmode.DisplayFrequency = 60
        devmode.Fields |= _DM_BITSPERPEL | _DM_DISPLAYFREQUENCY
    return int(
        win32api.ChangeDisplaySettingsEx(
            name, devmode, _CDS_UPDATEREGISTRY | _CDS_NORESET | flags
        )
    )


def _snapshot() -> dict[str, Any]:
    from tools.win_display import (
        display_devices,
        display_sources,
        get_scale,
        physical_monitors,
    )

    sources = []
    for src in display_sources():
        try:
            scale: Any = get_scale(src)
        except OSError as exc:
            scale = str(exc)
        sources.append(
            {
                "gdi_name": src.gdi_name,
                "rect": [src.x, src.y, src.width, src.height],
                "scale": scale,
            }
        )
    return {
        "devices": display_devices(),
        "sources": sources,
        "mss_monitors": physical_monitors(),
    }


def apply_layout(
    layout: list[MonitorSpec],
    virtual_match: str,
    include_others: bool,
    keep_others: bool,
    settle: float,
) -> list[str]:
    """Apply ``layout``; return a list of problems (empty = success)."""
    import win32api

    from tools.win_display import (
        display_devices,
        display_sources,
        extend_all_displays,
        get_scale,
        set_per_monitor_dpi_awareness,
        set_scale,
    )

    set_per_monitor_dpi_awareness()  # positions/sizes below are physical pixels
    print(f"SetDisplayConfig(EXTEND) -> {extend_all_displays()}")
    time.sleep(settle)

    pattern = re.compile(virtual_match)
    devices = display_devices()
    virtual = sorted(
        (
            d
            for d in devices
            if pattern.search(
                " ".join([d["adapter"], d["adapter_id"], d["monitor"], d["monitor_id"]])
            )
        ),
        key=lambda d: _gdi_index(d["name"]),
    )
    others = sorted(
        (d for d in devices if d not in virtual and d["attached"]),
        key=lambda d: _gdi_index(d["name"]),
    )
    print(f"Virtual displays: {[d['name'] for d in virtual]}")
    print(f"Other attached displays: {[d['name'] for d in others]}")

    pool = (others if include_others else []) + virtual
    if len(pool) < len(layout):
        return [
            f"layout needs {len(layout)} displays, found {len(pool)} usable: "
            + json.dumps(devices, indent=2)
        ]
    targets = pool[: len(layout)]

    problems: list[str] = []
    for device, spec in zip(targets, layout):
        flags = _CDS_SET_PRIMARY if spec.is_primary else 0
        rc = _queue_mode(device["name"], spec.width, spec.height, spec.x, spec.y, flags)
        print(f"queue {device['name']} -> {spec}: {rc}")
        if rc != _DISP_CHANGE_SUCCESSFUL:
            problems.append(f"ChangeDisplaySettingsEx({device['name']}) returned {rc}")

    detach = [
        d
        for d in virtual + others
        if d not in targets and (d in virtual or not keep_others)
    ]
    for device in detach:
        rc = _queue_mode(device["name"], 0, 0, 0, 0, 0)
        print(f"queue detach {device['name']}: {rc}")
        if rc != _DISP_CHANGE_SUCCESSFUL:
            problems.append(f"detach {device['name']} returned {rc}")

    rc = int(win32api.ChangeDisplaySettingsEx(None, None, 0))
    print(f"apply -> {rc}")
    if rc != _DISP_CHANGE_SUCCESSFUL:
        problems.append(f"applying display changes returned {rc}")
    time.sleep(settle)

    # Scales, matched to the layout through each device's GDI name.
    by_name = {s.gdi_name.lower(): s for s in display_sources()}
    for device, spec in zip(targets, layout):
        if spec.scale is None:
            continue
        source = by_name.get(device["name"].lower())
        if source is None:
            problems.append(f"{device['name']} is not an active display source")
            continue
        try:
            set_scale(source, spec.scale)
            print(f"scale {device['name']} -> {spec.scale}%")
        except (OSError, ValueError) as exc:
            problems.append(str(exc))
    time.sleep(settle)

    # Read back: geometry from DisplayConfig, scale from the DPI query.
    sources = display_sources()
    target_names = {d["name"].lower() for d in targets}
    rects = [
        {"left": s.x, "top": s.y, "width": s.width, "height": s.height}
        for s in sources
        if s.gdi_name.lower() in target_names
    ]
    problems.extend(match_rects(layout, rects))
    if not keep_others:
        for s in sources:
            if s.gdi_name.lower() not in target_names:
                problems.append(f"{s.gdi_name} is still active but not in the layout")
    by_name = {s.gdi_name.lower(): s for s in sources}
    for device, spec in zip(targets, layout):
        source = by_name.get(device["name"].lower())
        if spec.scale is None or source is None:
            continue
        current = get_scale(source)["current"]
        if current != spec.scale:
            problems.append(
                f"{device['name']} scale is {current}%, wanted {spec.scale}%"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--layout", required=True, help="topology, e.g. 1920x1080@0,0:100"
    )
    parser.add_argument("--virtual-match", default=DEFAULT_VIRTUAL_MATCH)
    parser.add_argument(
        "--include-others",
        action="store_true",
        help="map layout entries to attached real displays first, then virtual ones",
    )
    parser.add_argument(
        "--keep-others",
        action="store_true",
        help="never detach real displays the layout does not use",
    )
    parser.add_argument(
        "--settle", type=float, default=3.0, help="seconds after changes"
    )
    parser.add_argument("--json", type=Path, help="write the final snapshot here")
    args = parser.parse_args()

    if sys.platform != "win32":
        print("set_display_layout.py only runs on Windows.")
        return 1

    layout = parse_topology(args.layout)
    problems = apply_layout(
        layout, args.virtual_match, args.include_others, args.keep_others, args.settle
    )

    snapshot = _snapshot()
    snapshot["requested"] = args.layout
    snapshot["problems"] = problems
    print(json.dumps(snapshot, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    if problems:
        print("\nLAYOUT FAILED:\n  " + "\n  ".join(problems))
        return 1
    print(f"\nLayout applied: {args.layout}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
