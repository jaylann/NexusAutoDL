Real captures land here (not committed by default).

- `tools/capture_fixtures.py` captures every monitor frame interactively.
- `tools/generate_scenarios.py` renders buttons at known positions/scales and labels them geometrically.

Both write one PNG per monitor frame with frame-relative ground-truth points
(`meta.frame` records the monitor's virtual-desktop geometry). Captures made
before the per-monitor capture pipeline (pre-2026-07) used whole-virtual-desktop
coordinates and must be regenerated.
