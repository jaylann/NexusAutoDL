<div align="center">

# 🚀 NexusAutoDL

### Automated Download Assistant for Nexus Mods

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()

*Streamline your modding workflow by automating download button clicks on Nexus Mods*

[Features](#-features) • [Installation](#-installation) • [Usage](#-usage) • [Configuration](#-configuration) • [Troubleshooting](#-troubleshooting)

</div>

---

## ⚠️ Important Notice

> **Disclaimer**: Automating interactions with Nexus Mods is against their Terms of Service. This tool is provided for educational purposes only. Use at your own risk and responsibility.

---

## 📖 Overview

**NexusAutoDL** automates the "Download" / "Download with Vortex" flow on [Nexus Mods](https://www.nexusmods.com/). It watches one or more monitors, detects various download buttons (legacy and "New" layouts), and clicks through dialogs so you can walk away while your Vortex or browser download queue drains.

### 🎯 Features

- **🖥️ Multi-Monitor Support** - Screen capture across all monitors or constrain to primary display with `--force-primary`
- **🎮 Vortex Integration** - Automatic window positioning and popup handling for Vortex Mod Manager
- **🌐 Browser Support** - Works with Chrome and Firefox browsers
- **🔍 Smart Detection** - SIFT-based computer vision for detecting both legacy and modern Nexus Mods UI buttons
- **📦 Wabbajack Support** - Detects and handles Wabbajack download buttons
- **⏯️ Resumable Download Prompt** - Automatically dismisses Nexus Mods' beta "resumable downloads" modal (files >500MB) by choosing **Standard download** so the Wabbajack/browser flow keeps working (see [Resumable Downloads asset](#resumable-download-prompt-beta))
- **🐛 Debug Mode** - Save annotated screenshots with bounding boxes to diagnose detection issues
- **⚙️ Customizable Detection** - Fine-tune SIFT matching thresholds and timing parameters

---

## 💻 System Requirements

### For Full Automation (Production)
- **Python**: 3.9 or newer
- **Operating System**: Windows (requires `pywin32` for window management)
- **Display**: Visible Vortex and browser windows (not minimized)

---

## 🔧 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/jaylann/NexusAutoDL.git
cd NexusAutoDL
```

### 2. Create Virtual Environment (Recommended)

**Windows:**
```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Verify Installation

```bash
python validate.py
```

This validates that:
- All modules import correctly
- Required button template assets exist
- Pydantic models are properly configured

---

## 🚀 Usage

### Quick Start

#### Basic Usage (Windows Only)
Start monitoring for Website and Wabbajack download buttons only (no Vortex integration):

```bash
python main.py
```

**Note:** Without `--vortex`, the tool only detects Website and Wabbajack download buttons. It won't look for Vortex buttons or handle Vortex dialogs.

#### With Vortex Mod Manager
Enable Vortex integration with Chrome:

```bash
python main.py --vortex --browser chrome
```

#### Full Setup with Window Positioning
Automatically position windows and start scanning:

```bash
python main.py --vortex --browser chrome --window-title "Nexus Mods" --force-primary
```

### Common Usage Scenarios

<details>
<summary><b>Scenario 1: Basic Vortex + Browser (Primary Monitor Only)</b></summary>

```bash
python main.py --vortex --browser chrome --force-primary
```

**What it does:**
- Positions Vortex and Chrome windows
- Scans primary monitor only
- Handles modern green download buttons
- Clicks through Vortex dialogs automatically

**Best for:** Clean single-monitor setups

</details>

<details>
<summary><b>Scenario 2: Legacy Nexus Mods Interface</b></summary>

```bash
python main.py --vortex --browser firefox --legacy
```

**What it does:**
- Uses legacy button templates
- Handles "Staging" and "Understood" dialog buttons
- Works with older Nexus Mods layout
- Compatible with Firefox

**Best for:** Users on older Nexus Mods UI or with legacy template preferences

</details>

<details>
<summary><b>Scenario 3: Wabbajack Download Automation</b></summary>

```bash
python main.py --window-title "Wabbajack" --force-primary
```

**What it does:**
- Brings Wabbajack window to foreground
- Detects Wabbajack-specific download buttons
- Monitors primary display
- Handles Wabbajack download flow
- Dismisses the Nexus "resumable downloads" prompt by picking **Standard download** (see [below](#resumable-download-prompt-beta))

**Best for:** Wabbajack modlist installations

</details>

<details>
<summary><b>Scenario 4: Direct Browser Downloads (No Vortex)</b></summary>

```bash
python main.py --force-primary
```

**What it does:**
- Detects Website and Wabbajack download buttons only
- No Vortex integration (direct browser downloads)
- Monitors primary display
- Simpler workflow for non-Vortex users

**Best for:** Users downloading mods directly through browser without Vortex, or Wabbajack installations

</details>

<details>
<summary><b>Scenario 5: Debug Mode for Troubleshooting</b></summary>

```bash
python main.py --vortex --browser chrome --debug-frame-dir ./debug_frames --verbose
```

**What it does:**
- Saves every detection frame as PNG
- Draws bounding boxes around detected buttons
- Prints detailed state machine logs
- Helps diagnose false positives/negatives

**Best for:** Debugging detection issues or multi-monitor setups

</details>

<details>
<summary><b>Scenario 6: Fine-Tuned Detection Settings</b></summary>

```bash
python main.py --min-matches 12 --ratio 0.70 --click-delay 3.0
```

**What it does:**
- Requires 12 SIFT feature matches (stricter)
- Uses 0.70 Lowe ratio threshold
- Waits 3 seconds between scan iterations
- Reduces false positives

**Best for:** High-accuracy requirements or avoiding misclicks

</details>

<details>
<summary><b>Scenario 7: Multi-Monitor Full Desktop Scan</b></summary>

```bash
python main.py --vortex --browser chrome
```

**What it does:**
- Captures every monitor as its own frame (works with negative origins, portrait displays, mixed DPI scaling and non-contiguous layouts)
- Detects buttons anywhere on any screen
- Positions windows automatically

**Best for:** Multi-monitor setups where windows may be on any display

</details>

---

## ⚙️ Configuration

### Command-Line Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--browser` | choice | None | Browser to position: `chrome` or `firefox` (requires `--vortex`) |
| `--vortex` | flag | False | Enable Vortex Mod Manager integration (detects Vortex buttons, handles dialogs, manages windows). Without this, only Website and Wabbajack buttons are detected. |
| `--legacy` | flag | False | Use legacy button templates for old Nexus Mods UI |
| `--verbose` | flag | False | Enable detailed debug logging to console |
| `--force-primary` | flag | False | Scan primary monitor only (ignore secondary displays) |
| `--window-title` | text | None | Move window containing this text to foreground before scanning |
| `--min-matches` | int | 8 | Minimum SIFT feature matches required for button detection |
| `--ratio` | float | 0.75 | Lowe ratio test threshold for SIFT matching (0.0-1.0) |
| `--click-delay` | float | 2.0 | Seconds to wait between scan loop iterations |
| `--simulate` | flag | False | Run in simulation mode without actual clicking (safe mode) |
| `--debug-frame-dir` | path | None | Directory path to save annotated debug screenshots |

### Get Complete Help

```bash
python main.py --help
```

### Resumable Download Prompt (Beta)

Nexus Mods is rolling out a [resumable downloads](https://help.nexusmods.com/article/170-resumable-downloads) beta. For files over 500MB, clicking a manual download now opens a modal asking you to choose between **Standard download** and **Resumable download**. The resumable option streams the file through the browser's File System API (it pops a "save file" dialog), which Wabbajack and other browser-download watchers cannot intercept — so automation would stall on that modal.

In the non-Vortex (browser / Wabbajack) flow, NexusAutoDL watches for this modal and clicks **Standard download** to clear it, keeping the normal download flow intact. This runs ahead of the regular Website/Wabbajack button detection so the modal is dismissed before anything else.

Because the button varies per theme/scale, this is driven by an **optional** template asset. Detection stays dormant (a no-op) until you provide it:

- Add `assets/StandardDownloadButton.png` — a tight crop of the **Standard download** button from the modal.
- Capture it through the real pipeline with `python tools/capture_fixtures.py --out assets` (or crop a screenshot manually), matching the style of the other `assets/*Button.png` templates.

Once the asset is present, `python validate.py` lists it and the clicker begins handling the prompt automatically. No CLI flag is required.

## 🐛 Troubleshooting

### Common Issues & Solutions

<details>
<summary><b>Problem: No buttons detected</b></summary>

**Possible Causes:**
- Windows are minimized or occluded
- Wrong button templates for your Nexus Mods UI version
- Detection threshold too strict

**Solutions:**
1. Ensure browser and Vortex windows are **visible and not minimized**
2. Try lowering `--min-matches` threshold:
   ```bash
   python main.py --min-matches 5
   ```
3. Check if you need `--legacy` flag for old Nexus Mods UI
4. Enable debug mode to see what's being detected:
   ```bash
   python main.py --debug-frame-dir ./debug --verbose
   ```
5. Verify windows are on the monitor being scanned (try `--force-primary`)

</details>

<details>
<summary><b>Problem: False positives (clicking wrong things)</b></summary>

**Possible Causes:**
- Detection threshold too lenient
- Similar UI elements matching templates
- Multiple monitors with ambiguous content

**Solutions:**
1. Increase strictness with `--min-matches`:
   ```bash
   python main.py --min-matches 12
   ```
2. Lower the ratio threshold:
   ```bash
   python main.py --ratio 0.65
   ```
3. Use `--force-primary` to limit scan area
4. Review debug frames to identify false matches:
   ```bash
   python main.py --debug-frame-dir ./debug
   ```

</details>

<details>
<summary><b>Problem: "Platform not supported" or pywin32 errors</b></summary>

**Cause:** Full automation requires Windows with `pywin32`

**Solutions:**
- **On Windows:** Ensure `pywin32` is installed:
  ```bash
  pip install pywin32
  ```
- **On macOS/Linux:** Use simulation mode for testing:
  ```bash
  python main.py --simulate
  ```
- **Alternative:** Run in a Windows VM or container

</details>

<details>
<summary><b>Problem: Import errors or missing dependencies</b></summary>

**Solutions:**
1. Reinstall all dependencies:
   ```bash
   pip install -r requirements.txt --force-reinstall
   ```
2. Verify Python version (3.9+ required):
   ```bash
   python --version
   ```
3. Check for missing packages:
   ```bash
   python validate.py
   ```
4. Ensure virtual environment is activated

</details>

<details>
<summary><b>Problem: Window positioning not working</b></summary>

**Possible Causes:**
- Windows not running or title mismatch
- Fullscreen mode interfering
- Multi-monitor confusion

**Solutions:**
1. Manually position windows before starting NexusAutoDL
2. Use exact window title substring with `--window-title`:
   ```bash
   python main.py --window-title "Nexus Mods - Google Chrome"
   ```
3. Ensure windows are in **windowed mode** (not fullscreen)
4. On multi-monitor setups, manually move windows to primary display
5. Check window manager logs with `--verbose`

</details>

<details>
<summary><b>Problem: Buttons detected but clicks miss target</b></summary>

**Possible Causes:**
- Window moved between detection and click
- DPI awareness could not be set (very old Windows, or another component locked it first)

**Solutions:**
1. NexusAutoDL declares per-monitor DPI awareness itself at startup — no compatibility overrides needed. Run with `--verbose` and check the logged `Process DPI awareness` level; anything below `system` means clicks may be scaled wrong.
2. Increase `--click-delay` to ensure pages load:
   ```bash
   python main.py --click-delay 3.0
   ```
3. Keep windows stationary during operation
4. Report issue with debug frames

</details>

---

## 🙏 Credits

- **Original Inspiration:** [nexus-autodl](https://github.com/parsiad/nexus-autodl) by parsiad
- **Community:** Thanks to the modding community for testing, templates, and feedback
- **Contributors:** All who have contributed code, bug reports, and ideas

---

## 🎥 Demo

Watch NexusAutoDL in action (Old but same principle):

https://user-images.githubusercontent.com/61842101/202874471-d5700912-16fd-4b7e-ab3f-0b97d05f6d9e.mp4

---

<div align="center">

### ⭐ If you find this tool useful, please star the repo!

**Made with ❤️ by [Justin Lanfermann](https://lanfermann.dev)**

</div>
