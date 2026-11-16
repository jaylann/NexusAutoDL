# Real screenshots

Everything in `../synthetic/` is drawn from the app's own button pictures, so it
proves geometry and scaling but not that those pictures look like what Vortex or
a browser really puts on screen. The images here are the only cases that do.
They are listed in `../cases.json` with `meta.source == "real"`.

## What is here

| Image | From | Shows | Result |
|---|---|---|---|
| `vortex_2.10.1_dialog_100.png` | Vortex 2.10.1 on a CI runner | "Download mod" dialog at 100 % scaling | found |
| `vortex_2.10.1_dialog_125.png` | same | the dialog at 125 % | found |
| `vortex_2.10.1_dialog_150.png` | same | the dialog at 150 % | found |
| `vortex_1.13.7_dialog_100.png` | Vortex 1.13.7 on a CI runner | the older dialog (orange "Download") at 100 % | found |
| `vortex_1.13.7_dialog_125.png` | same | the older dialog at 125 % | found |
| `vortex_1.13.7_dialog_150.png` | same | the older dialog at 150 % | found |
| `vortex_1.13.7_frame_150.png` | same | the whole monitor at 150 %, with a dimmed "Go Premium" button in the corner | found |
| `issue23_old_vortex.png` | issue #23 | Vortex's older free-download dialog (orange "Download"), 1366x768 monitor at 100 % | found |
| `issue23_go_premium.png` | issue #23 | Vortex's own "Go Premium" advert from the same screenshot | nothing may fire |
| `issue13_new_vortex_dialog.png` | issue #13 | "Download mod" dialog at 100 %, debug box drawn through the button | known gap |
| `issue13_new_vortex_crop.png` | issue #13 | the same from a second user, as posted (JPEG) | known gap |

Every button marked "found" is found between 0.5 and 1.5 px from its centre.
Each image is cropped to the Vortex dialog (or the advert), except the one
whole frame, which shows a freshly installed Vortex on a CI runner: no
desktop of a user, no account name. For every image the other button types
must stay silent.

### The captures from a running Vortex

These are the independent ones: Vortex drew them, and the app's pictures of the
button come from somewhere else. Two versions: 2.10.1 with the "Download mod"
dialog, and 1.13.7 with the older dialog that has the orange "Download". A hosted Windows runner installs the Vortex
release, a small local extension puts one entry into the queue Vortex shows
its free-download dialog for, and the screen is captured through the app's own
capture pipeline. No Nexus account is involved, nothing is downloaded and
nothing is clicked, which is why the dialog says "Mod with id 1". The tooling
is the real-Vortex check that comes with the display rig
(`.github/workflows/real-vortex.yml`, `tools/ci/real_vortex/`); its artifacts
hold new captures on every run.

The runner's render matches a user's: the button cut from the 100 % capture is
found in the issue #13 screenshot at the right pixel, same size (271x39 px).

The 2.10.1 capture at 125 % is why `MIN_INLIER_RATIO` is 0.2. The dialog says
"download" five times, which splits the matches; the fit on the real button was
thrown out at the earlier floor of 0.5.

The 1.13.7 captures settle what the screenshot from issue #23 could not: the
picture cut from that screenshot finds the button a freshly installed Vortex
draws at 100 %. They also caught a regression on the day it was made. At 150 %
the template search found the button and the label check then rejected it by a
hair, because the search's size steps are 9 % apart and the label was compared
a little too small. It is now compared at several sizes around the match
(`TM_REFINE_FACTORS`). That miss only happens on the whole monitor frame, where
the keypoint path falls one match short, so the frame itself is a case; in the
dialog crop the button is found either way. For these cases the expected point
is the centre of the button as Vortex reports it, not a measurement in the
image.

### The issue screenshots

- **The old dialog's case from #23 is not independent of its picture.**
  `assets/VortexDownloadButtonLegacy100.png` was cut from that same screenshot.
  The case shows the button is picked out of a user's real dialog, next to
  three other orange buttons, and that nothing else fires. That another Vortex
  draws the same pixels is what the 1.13.7 capture at 100 % shows.
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

- Vortex versions other than 1.13.7 and 2.10.1, apart from the newest stable
  one the real-Vortex check installs on every run.
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
