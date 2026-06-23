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
pip install ruff mypy pytest   # dev tools
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

## Pull requests

- Open a PR against `main` (please don't send files/zips out of band — a PR keeps
  the change reviewable and credited to you).
- Fill in the PR template, especially the "Tested on" section.
- First-time contributors: a maintainer needs to approve the CI workflow run on
  your first PR (GitHub default for public repos) — this is normal.
- Keep PRs focused; one logical change per PR is easiest to review.
