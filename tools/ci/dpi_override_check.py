"""Does the app refuse to run when Windows forces a DPI mode on it?

Windows-only. A compatibility setting on python.exe ("Override high DPI
scaling behavior", which this project's README used to recommend) holds the
process in a DPI mode it cannot leave. Coordinates are then scaled on some or
all monitors, and every click would land in the wrong place. The app must
notice and exit with code 2 instead of clicking.

The setting is imitated per process with the ``__COMPAT_LAYER`` environment
variable, which Windows applies to a child the same way. The dialog's choices
map to layers like this:

* "System" -> ``DPIUNAWARE``: Windows scales the program. Harmful.
* "System (Enhanced)" -> ``GDIDPISCALING DPIUNAWARE``: the same for
  coordinates, with sharper GDI text. Harmful.
* "Application" -> ``HIGHDPIAWARE``: the program is made per-monitor aware
  before it runs, so nothing is scaled. Harmless, and the app must not refuse.

A probe child runs first without any layer (the baseline), then for each
layer:

1. a probe child reports the DPI awareness it started with, the one it ended
   up with after asking for per-monitor awareness like the app does, and
   which monitors the app's own check (``WindowManager.scaled_monitors``)
   finds scaled;
2. whether coordinates *should* be scaled follows from that awareness and the
   layout alone: an unaware process is scaled on every monitor not at 100 %,
   a system-aware one on every monitor scaled differently from the primary;
3. the app is started the same way and must exit with code 2 naming the
   problem if they should be, and keep running if not.

A layer counts as applied when the probe starts at the awareness the layer
forces and behaves differently from the baseline. Otherwise it is reported as
"not applied" and does not fail the check: there is nothing to test then.

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

# Compatibility layer -> the DPI awareness it forces (0 unaware, 1 system
# aware, 2 per-monitor aware), as tools.win_display.process_dpi_awareness
# reports it. HIGHDPIAWARE was first assumed to force 1; on windows-2022 and
# windows-2025 runners a process under it starts at 2.
LAYERS: dict[str, int] = {
    "DPIUNAWARE": 0,
    "GDIDPISCALING DPIUNAWARE": 0,
    "HIGHDPIAWARE": 2,
}
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


def layer_applied(
    layer: str, probe: dict[str, Any], baseline: dict[str, Any] | None
) -> bool:
    """Whether the probe shows the layer at work.

    The process has to start at the awareness the layer forces. That alone
    proves nothing when python.exe starts there anyway, so the probe must
    also differ from the baseline: DPIUNAWARE starts where a plain python.exe
    does but cannot leave, HIGHDPIAWARE starts somewhere else. Without a
    baseline, a process that ends at the forced awareness counts.
    """
    forced = LAYERS[layer]
    if probe["before"] != forced:
        return False
    if baseline is None:
        return bool(probe["after"] == forced)
    return (probe["before"], probe["after"]) != (
        baseline["before"],
        baseline["after"],
    )


def judge(
    layer: str,
    probe: dict[str, Any] | None,
    app_exit: int | None,
    app_refused: bool,
    monitors: list[MonitorSpec],
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verdict for one layer from the probe's and the app's behaviour.

    ``app_exit`` is None when the app was still running at the deadline.
    ``baseline`` is the probe's result without any layer.
    """
    result: dict[str, Any] = {"layer": layer, "probe": probe, "app_exit": app_exit}
    if probe is None:
        return {**result, "status": "fail", "reason": "probe produced no result"}
    if not layer_applied(layer, probe, baseline):
        return {
            **result,
            "status": "not applied",
            "reason": f"process went from awareness {probe['before']} to "
            f"{probe['after']}, the layer should hold it at {LAYERS[layer]}"
            + ("" if baseline is None else ", and so does a plain python.exe"),
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

    plain_env = {k: v for k, v in os.environ.items() if k != "__COMPAT_LAYER"}
    baseline = _run_probe(plain_env)
    print(f"baseline (no layer): {baseline}")

    results: list[dict[str, Any]] = []
    for layer in LAYERS:
        env = {**plain_env, "__COMPAT_LAYER": layer}
        print(f"\n== {layer}")
        probe = _run_probe(env)
        print(f"  probe: {probe}")
        log_name = layer.replace(" ", "+") + ".log"
        app_exit, refused = _run_app(env, args.out / log_name, args.wait)
        result = judge(layer, probe, app_exit, refused, monitors, baseline)
        print(f"  {result['status']}: {result['reason']}")
        results.append(result)

    report = {"topology": args.topology, "baseline": baseline, "layers": results}
    (args.out / "results.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
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
