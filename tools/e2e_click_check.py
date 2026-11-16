"""End-to-end click-landing check: does the real app click *on* the button?

Windows-only. Fixtures (``generate_scenarios.py``) test detection; this tests
the whole loop under the current monitor topology and DPI scaling: capture ->
detect -> image-to-screen coordinates -> ``SetCursorPos`` / ``SendInput``.

Two processes, like production:

* **Target (this process)** -- per-monitor DPI aware *before* any window
  exists, so every coordinate it handles is a physical pixel. It plays the
  apps NexusAutoDL drives: a borderless "browser page" window on every monitor,
  and in Vortex mode a normal window titled exactly ``Vortex`` on top. It
  shows one button template at a time and records where each click lands.
* **App under test** -- the real CLI, ``python main.py`` (plus ``--vortex`` in
  Vortex mode), started as a subprocess with ``--debug-frame-dir``.

Because the target draws the button and receives the click in its own
(physical) coordinates, the verdict needs no cross-process coordinate
translation.

What a phase asserts:

* every button is clicked within ``--centre-tolerance`` px of its centre;
* nothing is clicked while no button is showing. Between rounds the target
  shows *idle scenes* for a few seconds each: a blank page, natively rendered
  lookalike buttons ("Download mod", "Fast download", ...), a busy text page
  and a half-painted window. Any click there is a stray click;
* every click the app logs (``Clicked at (x, y)``) was received by a target
  window. The pages cover every monitor, so a logged click nobody received
  means input never arrived.

Button size: the shipped templates are screenshots taken at a known Windows
scale (see ``TEMPLATES``). A button is drawn at ``monitor scale x zoom /
capture scale``, so a 100 % monitor shows it as small as a real 100 % user
sees it. ``zoom`` cycles per round to cover browser zoom.

Modes:

* ``web``    -- website / Wabbajack buttons on every monitor (non-Vortex mode).
* ``vortex`` -- needs 2+ monitors. The app finds the ``Vortex`` window,
  positions + maximises it on the first secondary monitor itself
  (``WindowManager.position_vortex``), searches inside its rect, then clicks the
  website button on the primary.
* ``both``   -- web, then vortex (skipped with a note on a single monitor).

    python tools/e2e_click_check.py --mode both --rounds 3 --out artifacts/click

Exit codes: 0 everything above holds, 1 a click missed, was off-centre, stray
or never received, 2 incomplete (timeout or the app exited early).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import ButtonType
from utils.platform import IS_WINDOWS


@dataclass(frozen=True)
class TemplateInfo:
    """A shipped picture of a button and the Windows scale it was captured at."""

    button_type: ButtonType
    asset: str
    capture_scale: float
    generation: str  # "new" or "legacy" UI design


# Capture scales are measured, not documented anywhere else: the real "Download
# manually" button in issue #13 (100 % scaling) is 0.67x the new Vortex picture,
# the old orange "Download" in issue #23 is 0.65x the legacy one, and the two
# "Slow download" pictures differ by the same factor. The 2022 website bar has
# no real capture to measure against and is taken as a 100 % capture.
VORTEX_TEMPLATES: tuple[TemplateInfo, ...] = (
    TemplateInfo(ButtonType.VORTEX, "VortexDownloadButtonNew.png", 1.5, "new"),
    TemplateInfo(ButtonType.VORTEX, "VortexDownloadButton.png", 1.5, "legacy"),
)
# Everything the app accepts as the website's download button.
WEB_TEMPLATES: tuple[TemplateInfo, ...] = (
    TemplateInfo(ButtonType.WEBSITE, "WebsiteDownloadButtonNew.png", 1.5, "new"),
    TemplateInfo(ButtonType.WABBAJACK, "WabbajackDownloadButton.png", 1.0, "new"),
    TemplateInfo(ButtonType.WEBSITE, "WebsiteDownloadButton.png", 1.0, "legacy"),
)

_WEB_POSITIONS: tuple[tuple[float, float], ...] = (
    (0.50, 0.50),
    (0.25, 0.30),
    (0.75, 0.70),
    (0.30, 0.75),
    (0.70, 0.25),
)
# Kept central: the app only searches the inner part of the Vortex window
# (``services.geometry.compute_search_bbox`` insets its rect).
_VORTEX_POSITIONS: tuple[tuple[float, float], ...] = (
    (0.50, 0.50),
    (0.42, 0.45),
    (0.58, 0.55),
    (0.45, 0.58),
)
# Browser zoom per round, on top of the monitor's own scale.
_ZOOMS: tuple[float, ...] = (1.0, 1.5, 1.1)
# Shown between rounds while the app must not click. ``blank`` is what the
# startup period shows anyway, so it is not repeated here.
IDLE_SCENES: tuple[str, ...] = ("lookalikes", "busy", "half-painted")

_PAGE_BG = "#20232a"
_VORTEX_BG = "#2b2d42"
_ADVANCE_MS = 250  # pause between a click and showing the next step

# The app's own record of a click (``ClickController.click``).
_CLICK_LOG = re.compile(r"Clicked at \((-?\d+), (-?\d+)\)")
# Tk reports the pointer position the click arrived at; allow rounding slack.
_CLICK_MATCH_PX = 2

# Lookalike widgets: (fill, outline, text colour, labels). Near misses of the
# real labels in the real button styles, drawn with Tk so the text is rendered
# natively instead of being template pixels.
_LOOKALIKES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "#29292e",
        "#5a5a60",
        "#d0d0d4",
        (
            "Download mod",
            "Download one by one",
            "Manual download",
            "Fast download",
            "Slow upload",
            "Cancel download",
            "Auto-download all",
        ),
    ),
    ("#d78f46", "#d78f46", "#ffffff", ("Go Premium", "Cancel", "Skip", "Upload")),
    ("#bbbbbb", "#bbbbbb", "#313131", ("FAST DOWNLOAD", "SLOW UPLOAD")),
)
_BUSY_LINES: tuple[str, ...] = (
    "Collections  >  Gate to Sovngarde  >  Revision 96",
    "Manual download for collections. Throttled download speeds (3 MB/s).",
    "Ads and delay for each download. Get all your mods fast with Premium.",
    "You need to download from the Nexus Mods website.",
    "The link below will open the mod page. Downloading 21.20 GB / 57.59 GB",
    "Don't touch anything during installation. Ignore all errors.",
    "Fewer clicks and higher download speeds when you go Premium",
    "Waiting to install   49%   Plugins   Save Games   Extensions   Settings",
)


@dataclass
class Step:
    """One thing the target shows: a button to click, or an idle scene."""

    index: int
    kind: str  # "web", "vortex" or "idle"
    template: TemplateInfo | None = None
    monitor: int = -1  # monitor the button is drawn on; -1 = the Vortex window
    fx: float = 0.5
    fy: float = 0.5
    zoom: float = 1.0
    scene: str = ""  # idle steps only


@dataclass
class ClickRecord:
    """Where a click landed relative to what was showing."""

    step: int | None
    kind: str | None
    button_type: str | None
    asset: str | None
    generation: str | None
    monitor: int | None
    dpi_scale: float | None
    draw_scale: float | None  # size relative to the template image
    button_rect: list[int] | None  # x1, y1, x2, y2 (screen, physical px)
    click: list[int]  # x, y (screen, physical px)
    inside: bool
    ok: bool
    offset: list[int] | None = None  # click - button centre
    note: str = ""
    at: float = 0.0  # seconds since the phase started
    latency: float | None = None  # seconds from button shown to click


@dataclass
class PhaseResult:
    mode: str
    steps: int  # buttons that had to be clicked
    idle_scenes: list[str] = field(default_factory=list)
    clicks: list[ClickRecord] = field(default_factory=list)
    completed: bool = False
    app_exit_code: int | None = None
    skipped: str = ""
    app_clicks: int = 0  # clicks the app logged
    # Logged clicks the target never received.
    unseen_app_clicks: list[list[int]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        if self.skipped:
            return True
        return (
            self.completed
            and all(c.ok for c in self.clicks)
            and not self.unseen_app_clicks
        )


def logged_clicks(log_text: str) -> list[list[int]]:
    """Screen points the app logged as clicked, in order."""
    return [[int(x), int(y)] for x, y in _CLICK_LOG.findall(log_text)]


def unseen_clicks(
    logged: list[list[int]], received: list[list[int]]
) -> list[list[int]]:
    """Logged clicks with no matching received click (each one matched once)."""
    remaining = list(received)
    unseen: list[list[int]] = []
    for click in logged:
        hit = next(
            (
                r
                for r in remaining
                if abs(r[0] - click[0]) <= _CLICK_MATCH_PX
                and abs(r[1] - click[1]) <= _CLICK_MATCH_PX
            ),
            None,
        )
        if hit is None:
            unseen.append(click)
        else:
            remaining.remove(hit)
    return unseen


def draw_scale(template: TemplateInfo, monitor_scale: float, zoom: float) -> float:
    """Size to draw a template at so it looks like the real button would.

    A template captured at 150 % shows a button 1.5x its 100 % size, so on a
    100 % monitor at 100 % zoom the real button is 1 / 1.5 of the template.
    """
    return monitor_scale * zoom / template.capture_scale


def judge_click(
    rect: list[int], click: list[int], tolerance: float
) -> tuple[bool, bool, list[int]]:
    """``(inside, ok, offset)`` of a click against the button it was meant for.

    ``ok`` needs the click inside the button and within ``tolerance`` px of
    its centre: "inside" alone would accept a click that only just hit a wide
    button, hiding a detection that drifts.
    """
    inside = rect[0] <= click[0] < rect[2] and rect[1] <= click[1] < rect[3]
    centre = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
    offset = [click[0] - centre[0], click[1] - centre[1]]
    return inside, inside and math.hypot(*offset) <= tolerance, offset


def build_steps(mode: str, monitor_count: int, primary: int, rounds: int) -> list[Step]:
    """Button steps for ``mode``, with one idle scene after each round.

    Templates cycle so every design the app knows gets clicked: with three
    rounds, all three website pictures and both Vortex designs.
    """
    steps: list[Step] = []

    def add(kind: str, **fields: Any) -> None:
        steps.append(Step(len(steps), kind, **fields))

    for r in range(rounds):
        zoom = _ZOOMS[r % len(_ZOOMS)]
        if mode == "web":
            for m in range(monitor_count):
                fx, fy = _WEB_POSITIONS[(r + m) % len(_WEB_POSITIONS)]
                add(
                    "web",
                    template=WEB_TEMPLATES[(r + m) % len(WEB_TEMPLATES)],
                    monitor=m,
                    fx=fx,
                    fy=fy,
                    zoom=zoom,
                )
        else:
            fx, fy = _VORTEX_POSITIONS[r % len(_VORTEX_POSITIONS)]
            add(
                "vortex",
                template=VORTEX_TEMPLATES[r % len(VORTEX_TEMPLATES)],
                fx=fx,
                fy=fy,
                zoom=zoom,
            )
            wx, wy = _WEB_POSITIONS[r % len(_WEB_POSITIONS)]
            add(
                "web",
                template=WEB_TEMPLATES[r % len(WEB_TEMPLATES)],
                monitor=primary,
                fx=wx,
                fy=wy,
                zoom=zoom,
            )
        # After a full round the app is back at its first state, so an idle
        # scene never strands it waiting for a button that is not coming.
        add("idle", scene=IDLE_SCENES[r % len(IDLE_SCENES)])
    return steps


class Target:
    """Tk windows standing in for the browser page(s) and the Vortex window."""

    def __init__(
        self,
        monitors: list[dict[str, int]],
        scales: list[float],
        steps: list[Step],
        with_vortex: bool,
        centre_tolerance: float,
        idle_seconds: float,
    ) -> None:
        import tkinter as tk

        self.tk = tk
        self.monitors = monitors
        self.scales = scales
        self.steps = steps
        self.centre_tolerance = centre_tolerance
        self.idle_seconds = idle_seconds
        self.current = 0
        self.clicks: list[ClickRecord] = []
        self.started_at = time.monotonic()
        self._started = False
        self._shown_at = 0.0
        self._photo: Any = None  # keep a reference or Tk drops the image
        self._item: int | None = None
        self._item_canvas: Any = None
        self._images: dict[tuple[str, float], Any] = {}

        self.root = tk.Tk()
        self.root.withdraw()

        # A "browser page" covers every monitor in every mode, so a click
        # anywhere on the desktop lands on a window that records it.
        self.pages: dict[int, Any] = {
            i: self._page_canvas(mon) for i, mon in enumerate(monitors)
        }

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
        # move + maximise it onto the first secondary monitor itself. Created
        # after the pages, so it sits above the page on its monitor.
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

    def _canvases(self) -> list[tuple[Any, float]]:
        """Every canvas with the Windows scale of the monitor it is on."""
        result = [(canvas, self.scales[i]) for i, canvas in self.pages.items()]
        if self.vortex_canvas is not None:
            result.append((self.vortex_canvas, self._vortex_scale()))
        return result

    def _vortex_scale(self) -> float:
        from tools.win_display import dpi_scale_at

        canvas = self.vortex_canvas
        return dpi_scale_at(
            canvas.winfo_rootx() + canvas.winfo_width() // 2,
            canvas.winfo_rooty() + canvas.winfo_height() // 2,
        )

    # --- buttons -----------------------------------------------------------

    def _image_for(self, asset: str, scale: float) -> Any:
        key = (asset, round(scale, 3))
        if key not in self._images:
            from PIL import Image

            img = Image.open(ROOT / "assets" / asset).convert("RGB")
            if abs(scale - 1.0) > 0.005:
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

    def _monitor_scale(self, step: Step) -> float:
        if step.kind == "vortex":
            return self._vortex_scale()
        return self.scales[step.monitor]

    def _draw_scale(self, step: Step) -> float:
        assert step.template is not None
        return draw_scale(step.template, self._monitor_scale(step), step.zoom)

    def start(self) -> None:
        """Show the first step; until then the pages are blank."""
        self._started = True
        self.show_current()

    def show_current(self) -> None:
        """Draw the current step (no-op once all steps are done)."""
        self._clear()
        if self.done:
            return
        step = self.steps[self.current]
        self._shown_at = time.monotonic()
        if step.kind == "idle":
            self._draw_scene(step.scene)
            print(
                f"  step {step.index:02d} idle   {step.scene} for {self.idle_seconds}s"
            )
            self.root.after(round(self.idle_seconds * 1000), self._end_idle, step.index)
            return

        from PIL import ImageTk

        assert step.template is not None
        image = self._image_for(step.template.asset, self._draw_scale(step))
        self._photo = ImageTk.PhotoImage(image)
        self._item_canvas = self._canvas_for(step)
        self._item = self._item_canvas.create_image(
            0, 0, image=self._photo, anchor="center"
        )
        self._place_current()

    def _end_idle(self, index: int) -> None:
        if self.current == index:
            self.current += 1
            self.show_current()

    def _place_current(self) -> None:
        if self._item is None or self.done:
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
        for canvas in list(self.pages.values()) + [self.vortex_canvas]:
            if canvas is not None:
                canvas.delete("scene")

    def _current_rect(self) -> list[int] | None:
        if self._item is None:
            return None
        canvas = self._item_canvas
        x1, y1, x2, y2 = canvas.bbox(self._item)
        ox, oy = canvas.winfo_rootx(), canvas.winfo_rooty()
        return [ox + x1, oy + y1, ox + x2, oy + y2]

    # --- idle scenes -------------------------------------------------------

    def _draw_scene(self, scene: str) -> None:
        for canvas, scale in self._canvases():
            width, height = canvas.winfo_width(), canvas.winfo_height()
            if scene == "lookalikes":
                self._draw_lookalikes(canvas, width, height, scale)
            elif scene == "busy":
                self._draw_busy(canvas, width, height, scale)
            elif scene == "half-painted":
                self._draw_half_painted(canvas, width, height)

    def _draw_lookalikes(
        self, canvas: Any, width: int, height: int, scale: float
    ) -> None:
        """Buttons in the real styles whose labels are near misses."""
        labels = [
            (fill, outline, text, label)
            for fill, outline, text, group in _LOOKALIKES
            for label in group
        ]
        columns = 3
        cell_w, cell_h = width // columns, height // (len(labels) // columns + 2)
        for i, (fill, outline, text_colour, label) in enumerate(labels):
            # Sizes cycle so the labels cover the sizes real buttons come in.
            size = scale * (1.0, 1.25, 0.85)[i % 3]
            box_w, box_h = round(270 * size), round(38 * size)
            cx = cell_w * (i % columns) + cell_w // 2
            cy = cell_h * (i // columns + 1)
            canvas.create_rectangle(
                cx - box_w // 2,
                cy - box_h // 2,
                cx + box_w // 2,
                cy + box_h // 2,
                fill=fill,
                outline=outline,
                tags="scene",
            )
            canvas.create_text(
                cx,
                cy,
                text=label,
                fill=text_colour,
                font=("Segoe UI", -max(8, round(15 * size))),
                tags="scene",
            )

    def _draw_busy(self, canvas: Any, width: int, height: int, scale: float) -> None:
        """A text-heavy page: sidebar, text lines, grid lines, colour blocks."""
        canvas.create_rectangle(
            0, 0, width // 6, height, fill="#17181c", outline="", tags="scene"
        )
        line_h = round(34 * scale)
        for i in range(height // line_h):
            y = line_h * (i + 1)
            canvas.create_text(
                width // 6 + round(24 * scale),
                y,
                anchor="w",
                text=_BUSY_LINES[i % len(_BUSY_LINES)],
                fill=("#d0d0d4", "#9aa0a6", "#d78f46")[i % 3],
                font=("Segoe UI", -max(8, round((13, 16, 20)[i % 3] * scale))),
                tags="scene",
            )
            canvas.create_line(
                width // 6,
                y + line_h // 2,
                width,
                y + line_h // 2,
                fill="#3a3d45",
                tags="scene",
            )
        for i, colour in enumerate(("#d78f46", "#5b2d8e", "#2d6cdf", "#3a3d45")):
            x = width - round(260 * scale)
            y = round((90 + 150 * i) * scale)
            canvas.create_rectangle(
                x,
                y,
                x + round(220 * scale),
                y + round(110 * scale),
                fill=colour,
                outline="",
                tags="scene",
            )

    def _draw_half_painted(self, canvas: Any, width: int, height: int) -> None:
        """A window caught mid-repaint: one painted area, the rest black."""
        canvas.create_rectangle(
            round(width * 0.62),
            0,
            width,
            height,
            fill="#000000",
            outline="",
            tags="scene",
        )
        canvas.create_rectangle(
            0,
            round(height * 0.72),
            width,
            height,
            fill="#000000",
            outline="",
            tags="scene",
        )

    # --- clicks ------------------------------------------------------------

    def _on_click(self, event: Any) -> None:
        click = [int(event.x_root), int(event.y_root)]
        at = round(time.monotonic() - self.started_at, 2)
        rect = self._current_rect()
        if rect is None or self.done:
            # No button is showing: at startup, during an idle scene, in the
            # pause between two steps, or after the last one.
            if not self._started:
                when = "startup"
            elif self.done:
                when = "after"
            elif self.steps[self.current].kind == "idle":
                when = f"idle:{self.steps[self.current].scene}"
            else:
                when = "gap"
            self.clicks.append(
                ClickRecord(
                    step=None,
                    kind=None,
                    button_type=None,
                    asset=None,
                    generation=None,
                    monitor=None,
                    dpi_scale=None,
                    draw_scale=None,
                    button_rect=None,
                    click=click,
                    inside=False,
                    ok=False,
                    note=f"stray:{when}",
                    at=at,
                )
            )
            print(f"  STRAY click at {click} ({when}, t={at}s)")
            return

        step = self.steps[self.current]
        assert step.template is not None
        template = step.template
        inside, ok, offset = judge_click(rect, click, self.centre_tolerance)
        note = "" if ok else ("off-centre" if inside else "miss")
        self.clicks.append(
            ClickRecord(
                step=step.index,
                kind=step.kind,
                button_type=template.button_type.value,
                asset=template.asset,
                generation=template.generation,
                monitor=step.monitor,
                dpi_scale=round(self._monitor_scale(step), 2),
                draw_scale=round(self._draw_scale(step), 3),
                button_rect=rect,
                click=click,
                inside=inside,
                ok=ok,
                offset=offset,
                note=note,
                at=at,
                latency=round(time.monotonic() - self._shown_at, 2),
            )
        )
        print(
            f"  step {step.index:02d} {step.kind:<6} {template.asset} "
            f"mon{step.monitor} x{self._draw_scale(step):.2f} rect={rect} "
            f"click={click} offset={offset} {'ok' if ok else note.upper()}"
        )
        self._clear()
        self.current += 1
        self.root.after(_ADVANCE_MS, self.show_current)

    @property
    def done(self) -> bool:
        return self.current >= len(self.steps)

    def close(self) -> None:
        self.root.destroy()


def run_phase(
    mode: str,
    monitors: list[dict[str, int]],
    scales: list[float],
    args: argparse.Namespace,
) -> PhaseResult:
    """Show the step sequence for one mode while the real app runs."""
    if mode == "vortex" and len(monitors) < 2:
        return PhaseResult(mode, 0, skipped="vortex mode needs 2+ monitors")

    primary = next(
        (i for i, m in enumerate(monitors) if (m["left"], m["top"]) == (0, 0)), 0
    )
    steps = build_steps(mode, len(monitors), primary, args.rounds)
    result = PhaseResult(
        mode,
        sum(s.kind != "idle" for s in steps),
        idle_scenes=["blank"] + [s.scene for s in steps if s.kind == "idle"],
    )
    target = Target(
        monitors,
        scales,
        steps,
        with_vortex=mode == "vortex",
        centre_tolerance=args.centre_tolerance,
        idle_seconds=args.idle_seconds,
    )

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
            # The startup period doubles as the blank idle scene: the app is
            # already scanning (and positioning the Vortex window).
            deadline = time.monotonic() + args.timeout
            start_at = time.monotonic() + args.startup
            started = False
            while time.monotonic() < deadline:
                target.root.update()
                if not started and time.monotonic() >= start_at:
                    target.start()
                    started = True
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

    # The app has exited, so its log is complete.
    logged = logged_clicks(
        (out / "app.log").read_text(encoding="utf-8", errors="replace")
    )
    result.app_clicks = len(logged)
    result.unseen_app_clicks = unseen_clicks(logged, [c.click for c in result.clicks])
    return result


def summary_markdown(summary: dict[str, Any]) -> str:
    """One table per phase for the job summary (pure: unit-tested everywhere)."""
    monitors = ", ".join(
        f"{m['width']}x{m['height']}@{m['left']},{m['top']} {round(s * 100)}%"
        for m, s in zip(summary["monitors"], summary["scales"])
    )
    lines = [f"**Click check** on {monitors}", ""]
    for phase in summary["phases"]:
        if phase["skipped"]:
            lines += [f"- `{phase['mode']}`: skipped ({phase['skipped']})", ""]
            continue
        buttons = [c for c in phase["clicks"] if c["step"] is not None]
        strays = [c for c in phase["clicks"] if c["step"] is None]
        lines += [
            (
                f"`{phase['mode']}`: {'PASS' if phase['ok'] else 'FAIL'}, "
                f"{sum(c['ok'] for c in buttons)}/{phase['steps']} buttons clicked on "
                f"centre, {len(strays)} stray, {len(phase['unseen_app_clicks'])} never "
                f"received; idle scenes: {', '.join(phase['idle_scenes'])}"
            ),
            "",
            (
                "| step | picture | design | monitor | drawn at | offset px "
                "| latency s | result |"
            ),
            "|---|---|---|---|---|---|---|---|",
        ]
        for c in phase["clicks"]:
            if c["step"] is None:
                lines.append(
                    f"| - | - | - | - | - | - | - | STRAY at {c['click']} "
                    f"({c['note']}) |"
                )
                continue
            lines.append(
                f"| {c['step']} | {c['asset']} | {c['generation']} | {c['monitor']} "
                f"({round(c['dpi_scale'] * 100)}%) | x{c['draw_scale']} | "
                f"{c['offset']} | {c['latency']} | {'ok' if c['ok'] else c['note']} |"
            )
        lines.append("")
    return "\n".join(lines)


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
        "--idle-seconds",
        type=float,
        default=6.0,
        help="how long each idle scene stays up (several app scans)",
    )
    parser.add_argument(
        "--centre-tolerance",
        type=float,
        default=4.0,
        help="max distance in px between a click and its button's centre",
    )
    parser.add_argument(
        "--timeout", type=float, default=240.0, help="seconds per phase"
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
        "centre_tolerance": args.centre_tolerance,
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
    markdown = summary_markdown(summary)
    (args.out / "summary.md").write_text(markdown + "\n", encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")

    exit_code = 0
    print("\nSummary:")
    for r in results:
        if r.skipped:
            print(f"  {r.mode:<6} skipped: {r.skipped}")
            continue
        buttons = [c for c in r.clicks if c.step is not None]
        bad = [c for c in r.clicks if not c.ok]
        state = "OK" if r.ok else ("INCOMPLETE" if not r.completed else "FAILED")
        print(
            f"  {r.mode:<6} {state}: {sum(c.ok for c in buttons)}/{r.steps} buttons "
            f"clicked on centre, {len(bad)} missed/off-centre/stray, "
            f"{r.app_clicks} clicks logged by the app, "
            f"{len(r.unseen_app_clicks)} of them never received"
        )
        for c in bad:
            print(
                f"     {c.note} t={c.at}s step={c.step} click={c.click} "
                f"rect={c.button_rect} offset={c.offset}"
            )
        for click in r.unseen_app_clicks:
            print(f"     unseen: the app clicked {click}, but no window received it")
        if bad or r.unseen_app_clicks:
            exit_code = max(exit_code, 1)
        if not r.completed:
            exit_code = max(exit_code, 2)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
