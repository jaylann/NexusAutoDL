# Real screenshots

Everything in `../synthetic/` is drawn from the app's own button pictures, so it
proves geometry and scaling but not that those pictures look like what Vortex or
a browser really puts on screen. The images here are the only cases that do.
They are listed in `../cases.json` with `meta.source == "real"`.

## What is here

| Image | From | Shows | Result |
|---|---|---|---|
| `issue23_old_vortex.png` | issue #23 | Vortex's older free-download dialog (orange "Download"), 1366x768 monitor at 100 % | found, 1 px off centre |
| `issue13_new_vortex_dialog.png` | issue #13 | Vortex's "Download mod" dialog ("Download manually"), 2560x1440 at 100 % | known gap |
| `issue13_new_vortex_crop.png` | issue #13 | the same dialog from a second user, as posted (JPEG) | known gap |

Each image is cropped to the Vortex dialog: no desktop, no account name. For
every image the other five button types must stay silent.

Read the results with these limits in mind:

- **The old dialog's case is not independent of its picture.**
  `assets/VortexDownloadButtonLegacy100.png` was cut from that same screenshot.
  The case shows the button is picked out of its real dialog, next to three
  other orange buttons, and that nothing else fires. It does not show that
  another user's Vortex draws the same pixels.
- **All three are debug frames**: the app's green box and caption are drawn on
  top. In the issue #13 images the box's lower edge runs through the button.
- **Known gaps** are real buttons the detector does not find yet (`known_gap`
  in the case). Both are the new dialog at 100 % scaling: the app's pictures of
  "Download manually" are 150 % captures, and shrunk to two thirds they do not
  match text Windows rendered at 100 %. A picture cut from one of the two
  images finds the button in the other, so the fix is a clean capture at 100 %.
  Until then the cases still guard the other direction: detecting the button
  anywhere else (the dialog title "Download mod" used to match) fails.

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
