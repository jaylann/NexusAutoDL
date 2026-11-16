"""End-to-end click-landing check: does the real app click *on* the button?

Windows-only. Fixtures (``generate_scenarios.py``) test detection; this tests
the whole loop under the current monitor topology and DPI scaling: capture ->
detect -> image-to-screen coordinates -> ``SetCursorPos`` / ``mouse_event``.

Two processes, like production:

* **Target (this process)** -- per-monitor DPI aware *before* any window
  exists, so every coordinate it handles is a physical pixel. It plays the
  apps NexusAutoDL drives: a borderless "browser page" window per monitor,
  and in Vortex mode a normal window titled exactly ``Vortex``. It shows one
  button template at a time, scaled by that monitor's Windows scale (as a
  browser renders it), and records where each click actually lands.
* **App under test** -- the real CLI, ``python main.py`` (plus ``--vortex`` in
  Vortex mode), started as a subprocess with ``--debug-frame-dir``.

Because the target draws the button and receives the click in its own
(physical) coordinates, the verdict needs no cross-process coordinate
translation: a click is good iff it lands inside the drawn button.

Modes:

* ``web``    -- website / Wabbajack buttons on every monitor (non-Vortex mode).
* ``vortex`` -- needs 2+ monitors. The app finds the ``Vortex`` window,
  positions + maximises it on monitor 1 itself (``WindowManager.position_vortex``),
  searches inside its rect, then clicks the website button on the primary.
* ``both``   -- web, then vortex (skipped with a note on a single monitor).

    python tools/e2e_click_check.py --mode both --rounds 3 --out artifacts/click

Exit codes: 0 all clicks inside their buttons, 1 a click missed (or a stray
click), 2 incomplete (timeout or the app exited early).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import ButtonType  # noqa: E402
from utils.platform import IS_WINDOWS  # noqa: E402

# Templates the default (non-legacy) app mode matches, as in generate_scenarios.
_TEMPLATES: dict[ButtonType, str] = {
    ButtonType.VORTEX: "VortexDownloadButtonNew.png",
    ButtonType.WEBSITE: "WebsiteDownloadButtonNew.png",
    ButtonType.WABBAJACK: "WabbajackDownloadButton.png",
}
_WEB_POSITIONS: tuple[tuple[float, float], ...] = (
    (0.50, 0.50),
    (0.25, 0.30),
    (0.75, 0.70),
    (0.30, 0.75),
    (0.70, 0.25),
)
# Kept central: the app only searches the inner part of the Vortex window
# (``Scanner._handle_vortex_state`` pads its bbox by up to ~20 % per side).
_VORTEX_POSITIONS: tuple[tuple[float, float], ...] = (
    (0.50, 0.50),
    (0.42, 0.45),
    (0.58, 0.55),
    (0.45, 0.58),
)
_PAGE_BG = "#20232a"
_VORTEX_BG = "#2b2d42"
_ADVANCE_MS = 250  # pause between a click and showing the next button


@dataclass
class Step:
    """One button the app has to click."""

    index: int
    kind: str  # "web" or "vortex"
    button_type: ButtonType
    monitor: int  # physical monitor index it is drawn on; -1 = the Vortex window
    fx: float
    fy: float


@dataclass
class ClickRecord:
    """Where a click landed relative to the button that was showing."""

    step: Optional[int]
    kind: Optional[str]
    button_type: Optional[str]
    monitor: Optional[int]
    dpi_scale: Optional[float]
    button_rect: Optional[list[int]]  # x1, y1, x2, y2 (screen, physical px)
    click: list[int]  # x, y (screen, physical px)
    inside: bool
    offset: Optional[list[int]] = None  # click - button centre
    note: str = ""


@dataclass
class PhaseResult:
    mode: str
    steps: int
    clicks: list[ClickRecord] = field(default_factory=list)
    completed: bool = False
    app_exit_code: Optional[int] = None
    skipped: str = ""

    @property
    def ok(self) -> bool:
        if self.skipped:
            return True
        return self.completed and all(c.inside for c in self.clicks)


class Target:
    """Tk windows standing in for the browser page(s) and the Vortex window."""

    def __init__(
        self,
        monitors: list[dict[str, int]],
        scales: list[float],
        steps: list[Step],
        with_vortex: bool,
    ) -> None:
        import tkinter as tk

        self.tk = tk
        self.monitors = monitors
        self.scales = scales
        self.steps = steps
        self.current = 0
        self.clicks: list[ClickRecord] = []
        self._photo: Any = None  # keep a reference or Tk drops the image
        self._item: Optional[int] = None
        self._item_canvas: Any = None
        self._images: dict[tuple[ButtonType, float], Any] = {}

        self.root = tk.Tk()
        self.root.withdraw()

        # "Browser page" windows (monitor index -> canvas). In Vortex mode only the
        # primary gets one (the app puts the browser there); in web mode all do.
        self.pages: dict[int, Any] = {}
        page_monitors = [self._primary_index()] if with_vortex else range(len(monitors))
        for i in page_monitors:
            self.pages[i] = self._page_canvas(monitors[i])

        self.vortex_window: Any = None
        self.vortex_canvas: Any = None
        if with_vortex:
            self._make_vortex_window()

        self.root.update()

    # --- windows -----------------------------------------------------------

    def _primary_index(self) -> int:
        for i, mon in enumerate(self.monitors):
            if mon["left"] == 0 and mon["top"] == 0:
                return i
        return 0

    def _page_canvas(self, mon: dict[str, int]) -> Any:
        """Borderless window covering one monitor; returns its canvas."""
        win = self.tk.Toplevel(self.root)
        win.title("NexusAutoDL click check")
        win.overrideredirect(True)
        # Topmost so no stray runner/desktop window hides the target; a hidden
        # target would turn good clicks into misses.
        win.attributes("-topmost", True)
        win.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        canvas = self.tk.Canvas(win, highlightthickness=0, bg=_PAGE_BG)
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Button-1>", self._on_click)
        return canvas

    def _make_vortex_window(self) -> None:
        # Start it un-maximised on a non-primary monitor; the app is expected to
        # move + maximise it onto its monitors[1] itself.
        primary = self._primary_index()
        other = next(i for i in range(len(self.monitors)) if i != primary)
        mon = self.monitors[other]
        win = self.tk.Toplevel(self.root)
        win.title("Vortex")  # WindowManager looks it up by this exact title
        win.attributes("-topmost", True)
        width, height = min(1280, mon["width"] - 100), min(800, mon["height"] - 100)
        win.geometry(f"{width}x{height}+{mon['left'] + 50}+{mon['top'] + 50}")
        canvas = self.tk.Canvas(win, highlightthickness=0, bg=_VORTEX_BG)
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Button-1>", self._on_click)
        canvas.bind("<Configure>", lambda _e: self._place_current())
        self.vortex_window = win
        self.vortex_canvas = canvas

    # --- steps -------------------------------------------------------------

    def _image_for(self, button_type: ButtonType, scale: float) -> Any:
        key = (button_type, round(scale, 2))
        if key not in self._images:
            from PIL import Image

            img = Image.open(ROOT / "assets" / _TEMPLATES[button_type]).convert("RGB")
            if abs(scale - 1.0) > 0.01:
                img = img.resize(
                    (
                        max(1, round(img.width * scale)),
                        max(1, round(img.height * scale)),
                    ),
                    Image.LANCZOS,
                )
            self._images[key] = img
        return self._images[key]

    def _canvas_for(self, step: Step) -> Any:
        if step.kind == "vortex":
            return self.vortex_canvas
        return self.pages[step.monitor]

    def _scale_for(self, step: Step) -> float:
        if step.kind == "vortex":
            from tools.win_display import dpi_scale_at

            canvas = self.vortex_canvas
            return dpi_scale_at(
                canvas.winfo_rootx() + canvas.winfo_width() // 2,
                canvas.winfo_rooty() + canvas.winfo_height() // 2,
            )
        return self.scales[step.monitor]

    def show_current(self) -> None:
        """Draw the current step's button (no-op once all steps are done)."""
        self._clear()
        if self.current >= len(self.steps):
            return
        from PIL import ImageTk

        step = self.steps[self.current]
        image = self._image_for(step.button_type, self._scale_for(step))
        self._photo = ImageTk.PhotoImage(image)
        self._item_canvas = self._canvas_for(step)
        self._item = self._item_canvas.create_image(
            0, 0, image=self._photo, anchor="center"
        )
        self._place_current()

    def _place_current(self) -> None:
        if self._item is None or self.current >= len(self.steps):
            return
        step = self.steps[self.current]
        canvas = self._item_canvas
        canvas.coords(
            self._item,
            int(step.fx * canvas.winfo_width()),
            int(step.fy * canvas.winfo_height()),
        )

    def _clear(self) -> None:
        if self._item is not None and self._item_canvas is not None:
            self._item_canvas.delete(self._item)
        self._item = None
        self._item_canvas = None

    def _current_rect(self) -> Optional[list[int]]:
        if self._item is None:
            return None
        canvas = self._item_canvas
        x1, y1, x2, y2 = canvas.bbox(self._item)
        ox, oy = canvas.winfo_rootx(), canvas.winfo_rooty()
        return [ox + x1, oy + y1, ox + x2, oy + y2]

    def _on_click(self, event: Any) -> None:
        click = [int(event.x_root), int(event.y_root)]
        rect = self._current_rect()
        if rect is None or self.current >= len(self.steps):
            self.clicks.append(
                ClickRecord(
                    None, None, None, None, None, None, click, False, None, "stray"
                )
            )
            return

        step = self.steps[self.current]
        inside = rect[0] <= click[0] < rect[2] and rect[1] <= click[1] < rect[3]
        centre = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        self.clicks.append(
            ClickRecord(
                step=step.index,
                kind=step.kind,
                button_type=step.button_type.value,
                monitor=step.monitor,
                dpi_scale=round(self._scale_for(step), 2),
                button_rect=rect,
                click=click,
                inside=inside,
                offset=[click[0] - centre[0], click[1] - centre[1]],
            )
        )
        print(
            f"  step {step.index:02d} {step.kind:<6} {step.button_type.value:<9} "
            f"mon{step.monitor} rect={rect} click={click} "
            f"{'inside' if inside else 'MISSED'}"
        )
        self._clear()
        self.current += 1
        self.root.after(_ADVANCE_MS, self.show_current)

    @property
    def done(self) -> bool:
        return self.current >= len(self.steps)

    def close(self) -> None:
        self.root.destroy()


def _build_steps(mode: str, monitors: list[dict[str, int]], rounds: int) -> list[Step]:
    steps: list[Step] = []
    if mode == "web":
        web_types = (ButtonType.WEBSITE, ButtonType.WABBAJACK)
        for r in range(rounds):
            for m in range(len(monitors)):
                fx, fy = _WEB_POSITIONS[(r + m) % len(_WEB_POSITIONS)]
                bt = web_types[(r + m) % len(web_types)]
                steps.append(Step(len(steps), "web", bt, m, fx, fy))
    else:
        primary = next(
            (i for i, m in enumerate(monitors) if (m["left"], m["top"]) == (0, 0)), 0
        )
        for r in range(rounds):
            fx, fy = _VORTEX_POSITIONS[r % len(_VORTEX_POSITIONS)]
            steps.append(Step(len(steps), "vortex", ButtonType.VORTEX, -1, fx, fy))
            wx, wy = _WEB_POSITIONS[r % len(_WEB_POSITIONS)]
            steps.append(Step(len(steps), "web", ButtonType.WEBSITE, primary, wx, wy))
    return steps


def run_phase(
    mode: str,
    monitors: list[dict[str, int]],
    scales: list[float],
    args: argparse.Namespace,
) -> PhaseResult:
    """Show the button sequence for one mode while the real app runs."""
    if mode == "vortex" and len(monitors) < 2:
        return PhaseResult(mode, 0, skipped="vortex mode needs 2+ monitors")

    steps = _build_steps(mode, monitors, args.rounds)
    result = PhaseResult(mode, len(steps))
    target = Target(monitors, scales, steps, with_vortex=mode == "vortex")

    out = args.out / mode
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(ROOT / "main.py"),
        "--click-delay",
        str(args.click_delay),
        "--debug-frame-dir",
        str(out / "frames"),
        "--verbose",
    ]
    if mode == "vortex":
        cmd.append("--vortex")

    print(f"\n== {mode}: {len(steps)} steps, app: {' '.join(cmd[1:])}")
    with open(out / "app.log", "w", encoding="utf-8") as log:
        # cwd=ROOT: ButtonDetector loads templates from the relative "assets" dir.
        app = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            # Give the app time to start (and to position the Vortex window).
            deadline = time.monotonic() + args.timeout
            start_at = time.monotonic() + args.startup
            shown = False
            while time.monotonic() < deadline:
                target.root.update()
                if not shown and time.monotonic() >= start_at:
                    target.show_current()
                    shown = True
                if target.done:
                    result.completed = True
                    break
                if app.poll() is not None:
                    print(f"  app exited early with code {app.returncode}")
                    break
                time.sleep(0.02)
            # Let in-flight clicks arrive so strays are recorded too.
            settle_until = time.monotonic() + max(1.0, args.click_delay * 2)
            while time.monotonic() < settle_until and app.poll() is None:
                target.root.update()
                time.sleep(0.02)
        finally:
            if app.poll() is None:
                app.terminate()
                try:
                    app.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    app.kill()
            result.app_exit_code = app.returncode
            result.clicks = target.clicks
            target.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode", choices=("web", "vortex", "both"), default="both")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--click-delay", type=float, default=0.5)
    parser.add_argument(
        "--startup", type=float, default=6.0, help="seconds before the first button"
    )
    parser.add_argument(
        "--timeout", type=float, default=180.0, help="seconds per phase"
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/click-check"))
    args = parser.parse_args()

    if not IS_WINDOWS:
        print(
            "e2e_click_check.py only runs on Windows (real clicks on a real desktop)."
        )
        return 1

    from tools.win_display import (
        dpi_scale_at,
        physical_monitors,
        set_per_monitor_dpi_awareness,
    )

    set_per_monitor_dpi_awareness()  # before any window: Tk works in physical px
    monitors = physical_monitors()
    scales = [
        dpi_scale_at(m["left"] + m["width"] // 2, m["top"] + m["height"] // 2)
        for m in monitors
    ]
    print("Monitors (physical):")
    for i, (m, s) in enumerate(zip(monitors, scales)):
        print(
            f"  [{i}] {m['width']}x{m['height']}@{m['left']},{m['top']} scale {s:.0%}"
        )

    args.out.mkdir(parents=True, exist_ok=True)
    modes = ["web", "vortex"] if args.mode == "both" else [args.mode]
    results = [run_phase(mode, monitors, scales, args) for mode in modes]

    summary = {
        "monitors": monitors,
        "scales": scales,
        "phases": [
            {
                **{k: v for k, v in asdict(r).items() if k != "clicks"},
                "ok": r.ok,
                "clicks": [asdict(c) for c in r.clicks],
            }
            for r in results
        ],
    }
    (args.out / "results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    exit_code = 0
    print("\nSummary:")
    for r in results:
        if r.skipped:
            print(f"  {r.mode:<6} skipped: {r.skipped}")
            continue
        good = sum(c.inside for c in r.clicks)
        misses = [c for c in r.clicks if not c.inside]
        state = "OK" if r.ok else ("INCOMPLETE" if not r.completed else "MISSED")
        print(
            f"  {r.mode:<6} {state}: {good}/{r.steps} on target, {len(misses)} missed/stray"
        )
        for c in misses:
            print(
                f"     {c.note or 'miss'} step={c.step} click={c.click} rect={c.button_rect} offset={c.offset}"
            )
        if misses:
            exit_code = max(exit_code, 1)
        if not r.completed:
            exit_code = max(exit_code, 2)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
