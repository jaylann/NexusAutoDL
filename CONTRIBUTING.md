# Contributing to NexusAutoDL

Thanks for helping out! This guide covers local setup and the PR workflow.

## Development setup

```bash
git clone https://github.com/jaylann/NexusAutoDL.git
cd NexusAutoDL
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest + the pinned ruff
pip install mypy                      # optional type check
```

`pywin32` only installs on Windows. On macOS/Linux the app runs in a limited
mode (mocked Win32 + simulated scanner) — enough for development and for the
platform-agnostic tests, but not for real clicking.

## Before you open a PR

```bash
ruff check .          # lint
ruff format .         # auto-format
python validate.py    # imports / models / assets sanity check
pytest                # tests (once the tests/ suite exists)
```

CI runs the same checks on Linux + Windows. PRs must be green to merge.

## Simulate vs. real

- **Real mode** (`python main.py ...`) requires Windows + a visible screen and
  actually moves the mouse / clicks. Use it for true end-to-end checks.
- **Simulate mode** (`python main.py --simulate`) fabricates detections and does
  not touch the screen. Good for exercising the control flow, but note it does
  **not** test the real button-detection pipeline.

## Working with detection

Button detection uses SIFT template matching against the images in `assets/`.
The thing most likely to break is a Nexus Mods UI change that stops a template
from matching. If you hit a detection problem:

- A **screenshot of the failing screen** is the most useful thing you can attach
  — it can become a regression fixture.
- Note your **resolution, DPI scaling %, and monitor count**; these drive most
  detection/click-location issues.

## Testing monitor setups you don't own

Most click-location bugs depend on monitor count, position and DPI scaling.
Three rigs cover setups you don't have on your desk:

- **CI, virtual monitors** (`.github/workflows/windows-displays.yml`). It runs on
  PRs that touch the app or `tools/`, and nightly. A GitHub Windows runner gets
  virtual monitors (Virtual Display Driver) in several topologies, for example
  two 1080p screens at 100 % and 150 %, or a second screen left of the primary.
  On each topology it then:
  - captures labelled scenarios through the real pipeline
    (`tools/generate_scenarios.py`),
  - scores them (`detection_report.py --source scenario`),
  - runs the real app against stand-in windows and checks every click lands on
    its button (`tools/e2e_click_check.py`).

  Artifacts hold the captures, debug frames and per-click offsets. The scripts
  in `tools/ci/` also work on any Windows machine or VM.
- **CI, real Vortex** (`.github/workflows/real-vortex.yml`). It runs on PRs
  that touch the app or its button pictures, and nightly. A GitHub Windows
  runner installs a Vortex release (2.10.1 and 1.13.7, which has the older
  dialog with the orange "Download", both pinned, and the newest stable one),
  and a small local extension (`tools/ci/real_vortex/`) makes Vortex show its
  free-download dialog. `tools/ci/real_vortex_check.py` then compares the
  point the app would click with where Vortex itself says it drew the button,
  at 100, 125 and 150 % scaling. No Nexus account is involved, nothing is
  downloaded and nothing is clicked. The virtual-monitor rig draws the app's
  own pictures of the buttons; this is the check that those pictures still
  match what Vortex renders. Its artifacts hold clean captures of the dialog,
  which is where new fixtures under `tests/fixtures/detection/real/` and new
  button pictures come from.
- **Real end-to-end VM** (`tools/e2e/RUNBOOK.md`). Started by hand, never in
  CI. It drives the live Nexus site and the latest Vortex, browsers and
  Wabbajack. When it fails, turn the failing screen into a fixture under
  `tests/fixtures/detection/real/`, after removing any account details from
  the image.

## Pull requests

- Open a PR against `main` (please don't send files/zips out of band — a PR keeps
  the change reviewable and credited to you).
- Fill in the PR template, especially the "Tested on" section.
- First-time contributors: a maintainer needs to approve the CI workflow run on
  your first PR (GitHub default for public repos) — this is normal.
- Keep PRs focused; one logical change per PR is easiest to review.
