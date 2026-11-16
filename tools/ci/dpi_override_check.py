"""Does the app refuse to run when Windows forces a DPI mode on it?

Windows-only. A compatibility setting on python.exe ("Override high DPI
scaling behavior", which this project's README used to recommend) holds the
process in a DPI mode it cannot leave. Coordinates are then scaled on some or
all monitors, and every click would land in the wrong place. The app must
notice and exit with code 2 instead of clicking.

The setting is imitated per process with the ``__COMPAT_LAYER`` environment
variable, which Windows applies to a child the same way. For each layer:

1. a probe child reports the DPI awareness it ended up with and which monitors
   the app's own check (``WindowManager.scaled_monitors``) finds scaled;
2. whether coordinates *should* be scaled follows from that awareness and the
   layout alone: an unaware process is scaled on every monitor not at 100 %,
   a system-aware one on every monitor scaled differently from the primary;
3. the app is started the same way and must exit with code 2 naming the
   problem if they should be, and keep running if not.

If the layer did not take effect on this machine (the probe ends up
per-monitor aware anyway) the layer is reported as "not applied" and does not
fail the check: there is nothing to test then.

    python tools/ci/dpi_override_check.py --topology "1920x1080@0,0:150" --out artifacts/dpi-override

Exit codes: 0 every applied layer behaved as expected, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.ci.topology import MonitorSpec, parse_topology

# Compatibility layer -> the DPI awareness it is meant to force
# (0 unaware, 1 system aware), as tools.win_display.process_dpi_awareness
# reports it.
LAYERS: dict[str, int] = {"DPIUNAWARE": 0, "HIGHDPIAWARE": 1}
_PER_MONITOR_AWARE = 2
_EXIT_COORDINATES_SCALED = 2
_REFUSAL_TEXT = "Windows is scaling this program's coordinates"

_PROBE = """
import json, sys
sys.path.insert(0, {root!r})
from tools.win_display import process_dpi_awareness
before = process_dpi_awareness()
from services.window_manager import WindowManager
scaled = WindowManager.scaled_monitors()
print(json.dumps({{"before": before, "after": process_dpi_awareness(), "scaled": scaled}}))
"""


def expect_scaled(awareness: int, monitors: list[MonitorSpec]) -> bool:
    """Whether a process at ``awareness`` sees scaled coordinates on a layout.

    Unaware: Windows renders it at 100 % and scales, so any monitor above
    100 % is reported smaller than it is. System aware: it is laid out at the
    primary's scale, so only monitors at a different scale are affected.
    """
    scales = [m.scale or 100 for m in monitors]
    if awareness >= _PER_MONITOR_AWARE:
        return False
    if awareness == 0:
        return any(scale != 100 for scale in scales)
    primary = next(m.scale or 100 for m in monitors if m.is_primary)
    return any(scale != primary for scale in scales)


def judge(
    layer: str,
    probe: dict[str, Any] | None,
    app_exit: int | None,
    app_refused: bool,
    monitors: list[MonitorSpec],
) -> dict[str, Any]:
    """Verdict for one layer from the probe's and the app's behaviour.

    ``app_exit`` is None when the app was still running at the deadline.
    """
    result: dict[str, Any] = {"layer": layer, "probe": probe, "app_exit": app_exit}
    if probe is None:
        return {**result, "status": "fail", "reason": "probe produced no result"}
    if probe["after"] != LAYERS[layer]:
        return {
            **result,
            "status": "not applied",
            "reason": f"process ended at awareness {probe['after']}, "
            f"the layer should force {LAYERS[layer]}",
        }

    expected = expect_scaled(probe["after"], monitors)
    result["expect_scaled"] = expected
    if bool(probe["scaled"]) != expected:
        return {
            **result,
            "status": "fail",
            "reason": f"scaled_monitors() returned {probe['scaled']}, expected "
            f"{'some' if expected else 'none'}",
        }
    if expected:
        if app_exit == _EXIT_COORDINATES_SCALED and app_refused:
            return {**result, "status": "ok", "reason": "app refused to start"}
        return {
            **result,
            "status": "fail",
            "reason": f"app should have refused (exit {_EXIT_COORDINATES_SCALED}), "
            f"got exit {app_exit}, message shown: {app_refused}",
        }
    if app_exit is None:
        return {**result, "status": "ok", "reason": "app runs: coordinates are fine"}
    return {
        **result,
        "status": "fail",
        "reason": f"app exited with {app_exit} although coordinates are not scaled",
    }


def _run_probe(env: dict[str, str]) -> dict[str, Any] | None:
    out = subprocess.run(
        [sys.executable, "-c", _PROBE.format(root=str(ROOT))],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    for line in reversed(out.stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    print(f"  probe failed (exit {out.returncode}): {out.stderr.strip()[-400:]}")
    return None


def _run_app(
    env: dict[str, str], log_path: Path, wait: float
) -> tuple[int | None, bool]:
    """Start the app; ``(exit code or None if still running, refusal shown)``."""
    with open(log_path, "w", encoding="utf-8") as log:
        app = subprocess.Popen(
            [sys.executable, str(ROOT / "main.py"), "--verbose"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and app.poll() is None:
            time.sleep(0.2)
        exit_code = app.poll()
        if exit_code is None:
            app.terminate()
            try:
                app.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app.kill()
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return exit_code, _REFUSAL_TEXT in text


def summary_line(results: list[dict[str, Any]]) -> str:
    """One line for the job summary and the compatibility report."""
    parts = [f"{r['layer']}: {r['status']} ({r['reason']})" for r in results]
    return "Forced DPI mode: " + "; ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--topology", required=True, help="the layout in effect")
    parser.add_argument("--out", type=Path, default=Path("artifacts/dpi-override"))
    parser.add_argument(
        "--wait", type=float, default=15.0, help="seconds to watch the app for"
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print("dpi_override_check.py only runs on Windows.")
        return 1

    monitors = parse_topology(args.topology)
    args.out.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for layer in LAYERS:
        env = {**os.environ, "__COMPAT_LAYER": layer}
        print(f"\n== {layer}")
        probe = _run_probe(env)
        print(f"  probe: {probe}")
        app_exit, refused = _run_app(env, args.out / f"{layer}.log", args.wait)
        result = judge(layer, probe, app_exit, refused, monitors)
        print(f"  {result['status']}: {result['reason']}")
        results.append(result)

    (args.out / "results.json").write_text(
        json.dumps({"topology": args.topology, "layers": results}, indent=2) + "\n",
        encoding="utf-8",
    )
    line = summary_line(results)
    print(f"\n{line}")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return 1 if any(r["status"] == "fail" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
