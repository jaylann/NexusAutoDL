# Real screenshots

Everything in `../synthetic/` is drawn from the app's own button pictures, so it
proves geometry and scaling but not that those pictures look like what Vortex or
a browser really puts on screen. The images here are the only cases that do.
They are listed in `../cases.json` with `meta.source == "real"`.

## What is here

| Image | From | Shows | Result |
|---|---|---|---|
| `vortex_2.10.1_dialog_100.png` | Vortex 2.10.1 on a CI runner | "Download mod" dialog at 100 % scaling | found, 0.5 px off centre |
| `vortex_2.10.1_dialog_125.png` | same | the dialog at 125 % | found, 1.5 px off centre |
| `vortex_2.10.1_dialog_150.png` | same | the dialog at 150 % | found, 0.7 px off centre |
| `issue23_old_vortex.png` | issue #23 | Vortex's older free-download dialog (orange "Download"), 1366x768 monitor at 100 % | found, 1 px off centre |
| `issue23_go_premium.png` | issue #23 | Vortex's own "Go Premium" advert from the same screenshot | nothing may fire |
| `issue13_new_vortex_dialog.png` | issue #13 | "Download mod" dialog at 100 %, debug box drawn through the button | known gap |
| `issue13_new_vortex_crop.png` | issue #13 | the same from a second user, as posted (JPEG) | known gap |

Each image is cropped to the Vortex dialog (or the advert): no desktop, no
account name. For every image the other button types must stay silent.

### The Vortex 2.10.1 captures

These are the independent ones: Vortex drew them, and the app's pictures of the
button come from somewhere else. A hosted Windows runner installs the Vortex
release, a small local extension (`tools/ci/real_vortex/` on the
`spike/real-vortex-ci` branch) puts one entry into the queue Vortex shows its
free-download dialog for, and the screen is captured through the app's own
capture pipeline. No Nexus account is involved, nothing is downloaded and
nothing is clicked, which is why the dialog says "Mod with id 1".

The runner's render matches a user's: the button cut from the 100 % capture is
found in the issue #13 screenshot at the right pixel, same size (271x39 px).

The 125 % capture is why `MIN_INLIER_RATIO` is 0.2. The dialog says "download"
five times, which splits the matches; the fit on the real button was thrown out
at the earlier floor of 0.5.

### The issue screenshots

- **The old dialog's case is not independent of its picture.**
  `assets/VortexDownloadButtonLegacy100.png` was cut from that same screenshot.
  The case shows the button is picked out of its real dialog, next to three
  other orange buttons, and that nothing else fires. It does not show that
  another user's Vortex draws the same pixels.
- `issue23_go_premium.png` is there because the detector matched it: an orange
  button with white text the size of "Understood!" and of the old "Download".
  The template search took it for both until it compared the label on its own
  (`LABEL_NCC_THRESHOLD` in `services/button_detector.py`). Old Vortex shows
  this button all the time, so a wrong match there is a wrong click on every
  scan.
- **The issue #13 images are debug frames**: the app's green box is drawn on
  top and its lower edge runs through the button. That line is what makes the
  detector miss it: drawn onto the clean 100 % capture it has the same effect.
  They are kept as known gaps (`known_gap` in the case) for the other
  direction: detecting the button anywhere else fails, and the dialog title
  "Download mod" used to match.

A known gap is reported as an expected failure and listed by
`detection_report.py` outside the score. It tolerates only "nothing detected".

## Not covered by a real capture

- Old Vortex (1.x) from a clean install: it closes itself on the runner before
  the dialog can be shown.
- Vortex scaled by its own zoom setting, other themes, other languages.
- Any website button in a real browser.

## Adding captures

- `tools/capture_fixtures.py` captures every monitor frame interactively.
- `tools/generate_scenarios.py` renders buttons at known positions and scales
  and labels them geometrically (those are synthetic, whatever the folder).

Both write one PNG per monitor frame with frame-relative ground-truth points
(`meta.frame` records the monitor's virtual-desktop geometry). Captures made
before the per-monitor capture pipeline (pre-2026-07) used whole-virtual-desktop
coordinates and must be regenerated.

Before committing a capture: crop it to the app window, check it for account
names and anything else personal, record where it came from in `meta.origin`
and the display it was taken on in `meta.display`. Take it without
`--debug-frame-dir` output on top.
