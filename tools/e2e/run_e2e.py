"""Real end-to-end run against live Nexus Mods, Vortex and Wabbajack.

Manual, Windows-only, meant for the test VM described in ``tools/e2e/RUNBOOK.md``
(throwaway *free* Nexus account, latest apps via ``update_apps.ps1``). Never
run it from CI: it uses a real account and real downloads.

For each flow it:

1. snapshots the flow's download folder,
2. opens the target (Vortex collection, mod file page, or Wabbajack),
3. waits for the tester to finish the steps the tool does not automate
   (``--yes`` turns this into a timed wait),
4. starts NexusAutoDL (``main.py``) with the flow's flags and
   ``--debug-frame-dir``,
5. passes once the expected number of new, *complete* files has landed
   before ``--timeout``; otherwise saves a raw capture of the whole desktop,
6. writes ``result.json`` and cleans up (deletes the new files, runs the
   flow's optional ``reset`` command) so the run can be repeated.

While a flow runs it also saves a raw capture of every monitor every
``--raw-every`` seconds (``<flow>/raw/``). Those are unannotated frames from the
app's own capture pipeline: the source for regression fixtures and for button
pictures at the scale the VM runs at. ``--label`` names the run after the app
versions under test (see "Older versions" in the runbook).

    python tools/e2e/run_e2e.py --flow all
    python tools/e2e/run_e2e.py --flow website --yes --timeout 600
    python tools/e2e/run_e2e.py --flow vortex --label vortex-1.13.7

Flows and paths come from ``tools/e2e/targets.toml`` (copy
``targets.example.toml``; the real file is gitignored). Results go to
``e2e-results/<timestamp>/`` (gitignored). Exit code: 0 all flows passed,
1 a flow failed, 2 bad configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from utils.platform import IS_WINDOWS

FLOWS: tuple[str, ...] = ("vortex", "website", "wabbajack")

# Browser / downloader temp-file suffixes: such a file is still being written.
_INCOMPLETE_SUFFIXES = (
    ".crdownload",
    ".part",
    ".partial",
    ".tmp",
    ".download",
    ".opdownload",
)

# What the tester does before NexusAutoDL starts. A flow's ``prep`` in
# targets.toml replaces the text.
_DEFAULT_PREP: dict[str, str] = {
    "vortex": (
        "In Vortex, accept the collection install dialog(s) until Vortex starts "
        "asking for the first mod download."
    ),
    "website": "Wait until the mod page shows the slow-download button.",
    "wabbajack": (
        "In Wabbajack, open the modlist from targets.toml, set the install and "
        "download folders, and start the install. Continue once Wabbajack shows "
        "its Nexus download page."
    ),
}


def load_targets(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ImportError as exc:  # Python < 3.11
        raise SystemExit("run_e2e.py needs Python 3.11+ (tomllib)") from exc
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Copy tools/e2e/targets.example.toml and fill it in."
        )
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _is_complete(path: Path) -> bool:
    return path.is_file() and not path.name.lower().endswith(_INCOMPLETE_SUFFIXES)


def snapshot(folder: Path) -> set[str]:
    """Names of everything in ``folder`` right now (missing folder = empty)."""
    return {p.name for p in folder.iterdir()} if folder.exists() else set()


def new_complete_files(folder: Path, before: set[str]) -> dict[str, int]:
    """New complete files (name -> size) that were not in ``before``."""
    if not folder.exists():
        return {}
    return {
        p.name: p.stat().st_size
        for p in folder.iterdir()
        if p.name not in before and _is_complete(p) and p.stat().st_size > 0
    }


def wait_for_files(
    folder: Path,
    before: set[str],
    expected: int,
    timeout: float,
    app: subprocess.Popen,
) -> tuple[bool, dict[str, int], str]:
    """Poll until ``expected`` new files exist and their sizes stopped changing."""
    deadline = time.monotonic() + timeout
    previous: dict[str, int] = {}
    while time.monotonic() < deadline:
        current = new_complete_files(folder, before)
        if len(current) >= expected and current == previous:
            return True, current, "expected files arrived"
        if app.poll() is not None:
            return False, current, f"NexusAutoDL exited early ({app.returncode})"
        previous = current
        time.sleep(5)
    return False, new_complete_files(folder, before), f"timeout after {timeout:.0f}s"


def exe_version(path: str | None) -> str | None:
    """ProductVersion of a Windows executable (None if unknown)."""
    if not path or not Path(path).exists():
        return None
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Item -LiteralPath '{path}').VersionInfo.ProductVersion",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _git_revision() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _process_running(image_name: str) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return image_name.lower() in out.stdout.lower()


def open_target(flow: str, cfg: dict[str, Any], browser: dict[str, Any]) -> None:
    """Bring up whatever NexusAutoDL is supposed to click through."""
    if flow == "vortex":
        exe = Path(cfg["exe"])
        if not _process_running(exe.name):
            subprocess.Popen([str(exe)])
            time.sleep(15)  # Vortex takes a while to start
        os.startfile(cfg["collection_url"])  # type: ignore[attr-defined]  # Windows
    elif flow == "website":
        subprocess.Popen([browser["exe"], cfg["mod_file_url"]])
    elif flow == "wabbajack":
        subprocess.Popen([cfg["exe"]])


def app_command(flow: str, browser: dict[str, Any], frames: Path) -> list[str]:
    cmd = [sys.executable, str(ROOT / "main.py")]
    if flow == "vortex":
        cmd += ["--vortex", "--browser", browser.get("name", "chrome")]
    elif flow == "wabbajack":
        cmd += ["--window-title", "Wabbajack"]
    return cmd + ["--debug-frame-dir", str(frames), "--verbose"]


def save_final_capture(folder: Path) -> None:
    """Raw per-monitor captures of what NexusAutoDL saw at the end.

    Uses the app's own pipeline, so each ``final_capture_m<index>.png`` is a
    frame the detector would have searched and can become a fixture as is.
    """
    try:
        import cv2

        from services.screen_capture import ScreenCapture
        from services.window_manager import WindowManager

        capture = ScreenCapture(WindowManager.get_all_monitors())
        for captured in capture.capture_frames():
            path = folder / f"final_capture_m{captured.frame.index}.png"
            cv2.imwrite(str(path), cv2.cvtColor(captured.image, cv2.COLOR_RGB2BGR))
    # Diagnostics only: a failed capture must not mask the flow's own result.
    except Exception as exc:  # noqa: BLE001
        print(f"  could not save final capture: {exc}")


_CLICK_LOG = re.compile(r"Clicked at \((-?\d+), (-?\d+)\)")


class RawFrameSaver(threading.Thread):
    """Saves a raw capture of every monitor at a fixed interval.

    Runs beside the app, through the app's own capture pipeline, so each
    ``<seconds>_m<index>.png`` is a frame the detector could have been given.
    """

    def __init__(self, folder: Path, every: float) -> None:
        super().__init__(daemon=True)
        self.folder = folder
        self.every = every
        self._stop_requested = threading.Event()
        self.saved = 0

    def run(self) -> None:
        import cv2

        from services.screen_capture import ScreenCapture
        from services.window_manager import WindowManager

        # mss objects belong to the thread that created them.
        capture = ScreenCapture(WindowManager.get_all_monitors())
        self.folder.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        while not self._stop_requested.wait(self.every):
            seconds = round(time.monotonic() - started)
            for captured in capture.capture_frames():
                path = self.folder / f"{seconds:05d}_m{captured.frame.index}.png"
                cv2.imwrite(str(path), cv2.cvtColor(captured.image, cv2.COLOR_RGB2BGR))
                self.saved += 1

    def stop(self) -> None:
        self._stop_requested.set()
        self.join(timeout=10)


def run_flow(
    flow: str, targets: dict[str, Any], args: argparse.Namespace, out: Path
) -> dict[str, Any]:
    cfg = targets[flow]
    browser = targets.get("browser", {})
    folder = Path(cfg["downloads"] if "downloads" in cfg else browser["downloads"])
    expected = int(cfg.get("expected_files", 1))
    flow_dir = out / flow
    flow_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "flow": flow,
        "downloads": str(folder),
        "expected_files": expected,
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    print(f"\n== {flow}: watching {folder} for {expected} new file(s)")

    before = snapshot(folder)
    open_target(flow, cfg, browser)

    prep = cfg.get("prep", _DEFAULT_PREP[flow])
    if args.yes:
        wait = float(cfg.get("prep_seconds", 20))
        print(f"  prep (auto, {wait:.0f}s): {prep}")
        time.sleep(wait)
    else:
        input(f"  PREP: {prep}\n  Press Enter to start NexusAutoDL... ")

    cmd = app_command(flow, browser, flow_dir / "frames")
    print(f"  starting: {' '.join(cmd[1:])}")
    started = time.monotonic()
    ok, files, reason = False, {}, "interrupted"
    saver = RawFrameSaver(flow_dir / "raw", args.raw_every) if args.raw_every else None
    with open(flow_dir / "app.log", "w", encoding="utf-8") as log:
        app = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        if saver:
            saver.start()
        try:
            ok, files, reason = wait_for_files(
                folder, before, expected, args.timeout, app
            )
        finally:
            if saver:
                saver.stop()
            if not ok:
                save_final_capture(flow_dir)
            if app.poll() is None:
                app.terminate()
                try:
                    app.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    app.kill()

    # How often the app clicked for these files: far more clicks than files
    # means it kept hitting something that did nothing.
    log_text = (flow_dir / "app.log").read_text(encoding="utf-8", errors="replace")
    result.update(
        {
            "clicks": len(_CLICK_LOG.findall(log_text)),
            "raw_frames": saver.saved if saver else 0,
            "passed": ok,
            "reason": reason,
            "elapsed_s": round(time.monotonic() - started, 1),
            "files": files,
            "app_log": str(flow_dir / "app.log"),
        }
    )
    print(f"  {'PASS' if ok else 'FAIL'}: {reason} ({len(files)}/{expected} files)")

    if not args.keep_downloads:
        for name in files:
            try:
                (folder / name).unlink()
            except OSError as exc:
                print(f"  could not delete {name}: {exc}")
    if cfg.get("reset"):
        print(f"  reset: {cfg['reset']}")
        rc = subprocess.run(cfg["reset"], shell=True, cwd=ROOT, check=False).returncode
        result["reset_exit_code"] = rc

    (flow_dir / "result.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--flow", choices=(*FLOWS, "all"), default="all")
    parser.add_argument(
        "--targets", type=Path, default=ROOT / "tools" / "e2e" / "targets.toml"
    )
    parser.add_argument(
        "--timeout", type=float, default=900, help="seconds per flow for the downloads"
    )
    parser.add_argument(
        "--yes", action="store_true", help="no prompts: wait prep_seconds instead"
    )
    parser.add_argument(
        "--keep-downloads", action="store_true", help="don't delete what was downloaded"
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--label",
        default=None,
        help="name for this run, e.g. the app versions under test (vortex-1.13.7)",
    )
    parser.add_argument(
        "--raw-every",
        type=float,
        default=10.0,
        help="seconds between raw captures of every monitor (0 = off)",
    )
    args = parser.parse_args()

    if not IS_WINDOWS:
        print(
            "run_e2e.py only runs on Windows (the test VM from tools/e2e/RUNBOOK.md)."
        )
        return 1

    targets = load_targets(args.targets)
    flows = list(FLOWS) if args.flow == "all" else [args.flow]
    missing = [f for f in flows if f not in targets]
    if missing:
        print(f"targets.toml has no section for: {', '.join(missing)}")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder_name = f"{stamp}-{args.label}" if args.label else stamp
    out = args.out or ROOT / "e2e-results" / folder_name
    out.mkdir(parents=True, exist_ok=True)

    browser = targets.get("browser", {})
    versions = {
        "nexusautodl": _git_revision(),
        "vortex": exe_version(targets.get("vortex", {}).get("exe")),
        "browser": exe_version(browser.get("exe")),
        "wabbajack": exe_version(targets.get("wabbajack", {}).get("exe")),
    }
    print(f"Versions: {versions}")

    results = [run_flow(flow, targets, args, out) for flow in flows]
    summary = {"label": args.label, "versions": versions, "results": results}
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    print(f"\nResults in {out}")
    for r in results:
        print(
            f"  {r['flow']:<10} {'PASS' if r['passed'] else 'FAIL':<5} "
            f"{len(r['files'])}/{r['expected_files']} files, {r['clicks']} clicks, "
            f"{r['elapsed_s']}s  {r['reason']}"
        )
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
