# Real end-to-end test VM

This runbook sets up a Windows VM that runs NexusAutoDL against the **live** Nexus Mods site and
the **latest** Vortex, Chrome, Firefox and Wabbajack. You start every run by hand. Nothing here
runs in CI.

The synthetic multi-monitor and DPI checks run in CI already
(`.github/workflows/windows-displays.yml`). This VM covers what CI can't: real pages, real apps
and real downloads.

> **Nexus Mods terms of service.** Automating downloads breaks them. Use a dedicated free
> throwaway account only, keep runs small and infrequent, and expect that account to be banned
> at some point. Never use your own account.

## 1. Create the VM (~30 min)

- Windows 11, any hypervisor. On `homebase`, use a Hyper-V Gen2 VM with 4 vCPU, 8 GB RAM and
  80 GB disk.
- On the host, allow 1080p on the Hyper-V display:
  `Set-VMVideo -VMName <vm> -HorizontalResolution 1920 -VerticalResolution 1080 -ResolutionType Single`
- **Use the VMConnect basic session.** In VMConnect, turn off *View → Enhanced session*. An
  enhanced session is RDP. RDP swaps in its own display, so the virtual monitors vanish from
  the session and screen captures come back black.

## 2. Keep the desktop alive (~10 min)

NexusAutoDL needs a logged-in, unlocked, awake desktop. In an elevated shell on the VM:

```powershell
winget install --id Microsoft.Sysinternals.Autologon --exact   # run it once, enter the test user
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\Personalization" /v NoLockScreen /t REG_DWORD /d 1 /f
reg add "HKCU\Control Panel\Desktop" /v ScreenSaveActive /t REG_SZ /d 0 /f
powercfg /change monitor-timeout-ac 0
powercfg /change standby-timeout-ac 0
```

If you ever RDP in, hand the session back to the console before you disconnect (in `cmd`):

```cmd
for /f "skip=1 tokens=3" %s in ('query user %USERNAME%') do tscon.exe %s /dest:console
```

## 3. Install the tools (~20 min)

```powershell
winget install --id Python.Python.3.12 --exact
winget install --id Git.Git --exact
git clone https://github.com/jaylann/NexusAutoDL.git C:\e2e\NexusAutoDL
cd C:\e2e\NexusAutoDL
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt
pwsh tools/e2e/update_apps.ps1     # installs Vortex, Chrome, Firefox, Wabbajack
```

## 4. Monitors (~10 min)

Add one virtual monitor next to the Hyper-V display. `--include-others` keeps the Hyper-V
display as the primary, so VMConnect still shows a desktop. The browser lands there and Vortex
goes to the virtual monitor (NexusAutoDL's `--vortex` layout).

```powershell
pwsh tools/ci/install_virtual_display_driver.ps1 -Count 1
python tools/ci/set_display_layout.py --include-others --layout "1920x1080@0,0:100;1920x1080@1920,0:100"
python tools/ci/display_report.py --out e2e-results/display --expect "1920x1080@0,0:100;1920x1080@1920,0:100"
```

`display_report.py` must end with `Display check OK`. To test mixed DPI, use `:150` on the
second monitor and run both commands again.

You can't see the virtual monitor in VMConnect. To watch it, run a VNC server on the VM (VNC
reads the whole desktop and, unlike RDP, doesn't replace the display driver).

The CI checks run here too, which helps when CI and the VM disagree:

```powershell
python tools/generate_scenarios.py --verify --out e2e-results/scenarios
python tools/e2e_click_check.py --mode both --out e2e-results/click-check
```

## 5. Accounts and apps (~20 min, by hand)

- Sign in to the throwaway **free** Nexus account in Chrome and Firefox and stay signed in.
  A premium account skips the buttons NexusAutoDL clicks.
- In Chrome, turn off *Ask where to save each file*.
- In Vortex, sign in through its Nexus login. Under *Settings → Downloads*, let Vortex handle
  `nxm://` links.

## 6. A game for Vortex and Wabbajack (spike)

Collections only download for a game Vortex manages, and Wabbajack modlists target a game too.

1. Try a stub first. Create a folder with the game's executable name (an empty file) and point
   Vortex at it (*Games → the game → Manually set location*). Keep the staging folder on the
   same drive.
2. If the game's extension rejects the folder, install a small game you own.

Write down what worked under [Findings](#findings).

## 7. Targets

Copy `tools/e2e/targets.example.toml` to `tools/e2e/targets.toml` (gitignored) and fill it in:

- one small collection (2–3 small mods),
- one small mod file page,
- one tiny Wabbajack modlist for the same game.

## 8. Run

```powershell
pwsh tools/e2e/update_apps.ps1          # newest Vortex / browsers / Wabbajack
python tools/e2e/run_e2e.py --flow all  # prompts for the manual prep step of each flow
```

For each flow, `run_e2e.py`:

1. opens the target,
2. waits for you to finish the prep it prints,
3. starts NexusAutoDL,
4. passes once the expected files have finished downloading.

Results land in `e2e-results/<timestamp>/`:

- `summary.json` with versions and pass/fail per flow,
- for each flow, `app.log`, debug `frames/`, and on failure `final_capture.png`.

The script deletes the downloads afterwards (unless `--keep-downloads`), so you can run it again
right away. Run it twice in a row once, to prove the cleanup works. Once a flow needs no hands,
use `--yes`.

## 9. Turn a failure into a regression test

A failure on a real page (for example #13's *Download manually* button) should become a
permanent test:

1. Leave the failing screen as it is. Then run
   `python tools/capture_fixtures.py --label --prefix <flow>-<date>` and click the button's
   centre in the preview.
2. Or take `final_capture.png` and add a `cases.json` entry by hand (`point` = the button
   centre in image pixels).
3. **Before committing, check the image for the account name and any other personal data.**
   Crop or blur it, or don't commit the image.
4. Commit the PNG and its `cases.json` entry under `tests/fixtures/detection/real/`.
   `pytest` and `detection_report.py` pick it up on every OS.

## Findings

Record answers here as the spikes settle them:

- [ ] Vortex accepts a stub game folder? Which game?
- [ ] Reliable way to reset a collection between runs (`reset` command in targets.toml)?
- [ ] Wabbajack needs the base game installed before it downloads?
- [ ] Virtual Display Driver works in this Hyper-V VM without GPU partitioning? (If the
      monitors don't appear, check `C:\VirtualDisplayDriver` and the driver's `<gpu>`
      setting.)
