"""
Main scanning orchestrator with state machine.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from models import (
    AppConfig,
    ButtonType,
    DetectionResult,
    Monitor,
    ScanState,
    ScanStatus,
)
from services.button_detector import ButtonDetector
from services.click_controller import ClickController
from services.debug_recorder import DebugRecorder
from services.geometry import MonitorFrame, compute_search_bbox
from services.screen_capture import CapturedFrame, ScreenCapture
from services.window_manager import WindowManager
from utils.logger import get_logger

logger = get_logger(__name__)

VORTEX_WEB_RETRY_LIMIT = 3  # Attempts before falling back to Vortex search


class Scanner:
    """Orchestrates the scanning and clicking process."""

    def __init__(
        self,
        config: AppConfig,
        monitors: list[Monitor],
        status_callback: Optional[Callable[[ScanStatus], None]] = None,
    ) -> None:
        """
        Initialize scanner.

        Args:
            config: Application configuration
            monitors: List of monitors
            status_callback: Optional callback for status updates
        """
        self.config = config
        self.monitors = monitors
        self.status_callback = status_callback

        # Initialize components
        self.screen_capture = ScreenCapture(monitors, config.force_primary)
        self.button_detector = ButtonDetector(use_legacy_buttons=self.config.legacy)
        self.window_manager = WindowManager(self.screen_capture.desktop)
        self.click_controller = ClickController(self.screen_capture.desktop)
        debug_path = (
            Path(self.config.debug_frame_dir) if self.config.debug_frame_dir else None
        )
        self.debug_recorder = DebugRecorder(debug_path)

        # Initialize status
        self.status = ScanStatus(current_action="Initialized")

        # Setup windows if needed
        self._setup_windows()

        logger.info("Scanner initialized")

    def _setup_windows(self) -> None:
        """Setup browser and Vortex windows."""
        try:
            if self.config.browser:
                self.window_manager.launch_browser(self.config.browser)
                self.status.current_action = f"Launched {self.config.browser.value}"
                self._update_status()

            if self.config.vortex:
                self.window_manager.position_vortex()
                self.status.current_action = "Positioned Vortex"
                self._update_status()

            if self.config.window_title:
                self.window_manager.position_window_by_title(self.config.window_title)
                self.status.current_action = (
                    f"Positioned window: {self.config.window_title}"
                )
                self._update_status()
        except Exception as e:
            error_msg = f"Window setup error: {e}"
            logger.error(error_msg)
            self.status.errors.append(error_msg)
            self._update_status()

    def _update_status(self) -> None:
        """Update status and trigger callback."""
        if self.status_callback:
            self.status_callback(self.status)

    def _click_detection(self, detection: DetectionResult, frame: MonitorFrame) -> bool:
        """
        Click on a detection result.

        Args:
            detection: Detection to click
            frame: Monitor frame the detection was found in

        Returns:
            True if the click was performed
        """
        virtual_x, virtual_y = frame.to_virtual(detection.x, detection.y)

        if not self.click_controller.click(virtual_x, virtual_y):
            return False

        self.status.clicks_count += 1
        self.status.detections.append(detection)
        self._update_status()
        return True

    def _handle_vortex_state(
        self,
        captured: CapturedFrame,
        iteration: int,
    ) -> bool:
        """
        Handle Vortex button detection state.

        Args:
            captured: Current frame of the monitor hosting Vortex

        Returns:
            True if Vortex button found and clicked
        """
        # Get Vortex window bbox (virtual-desktop coords)
        vortex_rect = self.window_manager.get_vortex_bbox()
        if not vortex_rect:
            self.status.current_action = "Waiting for Vortex window..."
            self._update_status()
            return False

        padded_bbox = compute_search_bbox(vortex_rect, captured.frame)
        if padded_bbox is None:
            # Vortex lives on another monitor; nothing to do in this frame.
            return False

        img = captured.image

        # Check for popup dialogs first (legacy workflow only)
        if self.config.legacy:
            popup_buttons: list[tuple[ButtonType, str]] = [
                (ButtonType.UNDERSTOOD, "Clicking 'Understood' button"),
                (ButtonType.STAGING, "Clicking 'Staging' button"),
            ]
            for button_type, action in popup_buttons:
                detection: Optional[DetectionResult] = self.button_detector.detect(
                    img,
                    button_type,
                    min_matches=6,
                    ratio=self.config.ratio_threshold,
                )
                if detection:
                    self.status.state = ScanState.HANDLING_POPUP
                    self.status.current_action = action
                    self._update_status()
                    self.debug_recorder.record(
                        img,
                        detection,
                        iteration,
                        f"m{captured.frame.index}_popup_{button_type.value}",
                    )
                    self._click_detection(detection, captured.frame)
                    time.sleep(self.config.retry_delay)
                    return False

        # Detect Vortex download button
        vortex_detection: Optional[DetectionResult] = self.button_detector.detect(
            img,
            ButtonType.VORTEX,
            min_matches=self.config.min_matches,
            ratio=self.config.ratio_threshold,
            bbox=padded_bbox,
        )

        if vortex_detection:
            self.status.current_action = "Clicking Vortex download button"
            self._update_status()
            self.debug_recorder.record(
                img,
                vortex_detection,
                iteration,
                f"m{captured.frame.index}_vortex_download",
            )
            return self._click_detection(vortex_detection, captured.frame)

        self.status.current_action = "Searching for Vortex button..."
        self._update_status()
        return False

    def _handle_web_state(
        self,
        captured: CapturedFrame,
        iteration: int,
    ) -> Optional[bool]:
        """
        Handle web download button detection on one frame.

        Args:
            captured: Current frame

        Returns:
            True if a web button was clicked, None if nothing was found
            in this frame (caller decides about retries across frames)
        """
        targets: list[tuple[ButtonType, str]] = [
            (ButtonType.WEBSITE, "website download button")
        ]

        if not self.config.vortex:
            targets.append((ButtonType.WABBAJACK, "Wabbajack download button"))

        for button_type, label in targets:
            detection: Optional[DetectionResult] = self.button_detector.detect(
                captured.image,
                button_type,
                min_matches=6,
                ratio=self.config.ratio_threshold,
            )

            if detection:
                self.status.current_action = f"Clicking {label}"
                self._update_status()
                self.debug_recorder.record(
                    captured.image,
                    detection,
                    iteration,
                    f"m{captured.frame.index}_web_{button_type.value}",
                )
                if self._click_detection(detection, captured.frame):
                    self.status.web_retry_count = 0
                    return True

        return None

    def _handle_web_retry(self) -> bool:
        """
        Track a failed web-button sweep across all frames.

        Returns:
            True if the state machine should reset to Vortex search
        """
        retry_limit = (
            VORTEX_WEB_RETRY_LIMIT
            if self.config.vortex
            else self.config.wabbajack_retry_limit
        )

        if self.status.web_retry_count >= retry_limit:
            if self.config.vortex:
                logger.info(
                    "Web button not found after %s attempts, returning to Vortex scan",
                    retry_limit,
                )
                self.status.current_action = "Rechecking Vortex (web button missing)"
                self.status.web_retry_count = 0
                self._update_status()
                return True

            logger.info("Web button not found, restarting...")
            self.status.current_action = "Restarting (button not found)"
            self.status.web_retry_count = 0
            self._update_status()
            return False

        self.status.web_retry_count += 1
        target_text = (
            "website download button"
            if self.config.vortex
            else "website or Wabbajack download button"
        )
        self.status.current_action = (
            f"Searching for {target_text}... "
            f"(attempt {self.status.web_retry_count}/{retry_limit})"
        )
        self._update_status()
        return False

    def _handle_click_dialog_state(
        self,
        captured: CapturedFrame,
        iteration: int,
    ) -> bool:
        """
        Handle click dialog detection state.

        Args:
            captured: Current frame

        Returns:
            True if dialog found and clicked
        """
        if not self.config.legacy:
            self.status.current_action = "Skipping legacy click dialog"
            self._update_status()
            return True

        click_detection: Optional[DetectionResult] = self.button_detector.detect(
            captured.image,
            ButtonType.CLICK,
            min_matches=6,
            ratio=self.config.ratio_threshold,
        )

        if click_detection:
            self.status.current_action = "Clicking dialog button"
            self._update_status()
            self.debug_recorder.record(
                captured.image,
                click_detection,
                iteration,
                f"m{captured.frame.index}_click_dialog",
            )
            self._click_detection(click_detection, captured.frame)
            time.sleep(3)  # Wait for dialog to process
            return True

        return False

    def scan_loop(self, max_iterations: Optional[int] = None) -> None:
        """
        Main scanning loop.

        Args:
            max_iterations: Optional maximum iterations (for testing)
        """
        self.status.state = (
            ScanState.WAITING_FOR_VORTEX
            if self.config.vortex
            else ScanState.WAITING_FOR_WEB
        )
        self._update_status()

        vortex_found: bool = False
        web_clicked: bool = False
        iteration: int = 0

        try:
            while True:
                if max_iterations and iteration >= max_iterations:
                    break
                iteration += 1

                # Capture every monitor; each handler checks the frames it
                # cares about, so windows may live on any monitor.
                frames = self.screen_capture.capture_frames()

                # State machine
                if not vortex_found and self.config.vortex:
                    self.status.state = ScanState.WAITING_FOR_VORTEX
                    for captured in frames:
                        if self._handle_vortex_state(captured, iteration):
                            vortex_found = True
                            self.status.state = ScanState.VORTEX_CLICKED
                            self._update_status()
                            break

                elif web_clicked and self.config.vortex:
                    self.status.state = ScanState.WEB_CLICKED
                    dialog_complete = False
                    for captured in frames:
                        if self._handle_click_dialog_state(captured, iteration):
                            dialog_complete = True
                            break
                    if not dialog_complete:
                        self.status.current_action = "Waiting for click dialog..."
                        self._update_status()
                    if dialog_complete:
                        # Reset for next mod
                        vortex_found = False
                        web_clicked = False
                        self.status.state = ScanState.WAITING_FOR_VORTEX
                        self._update_status()

                elif vortex_found or not self.config.vortex:
                    self.status.state = ScanState.WAITING_FOR_WEB
                    clicked = None
                    for captured in frames:
                        clicked = self._handle_web_state(captured, iteration)
                        if clicked:
                            break

                    if clicked:
                        web_clicked = True
                    else:
                        reset_to_vortex = self._handle_web_retry()
                        if reset_to_vortex and self.config.vortex:
                            vortex_found = False
                            web_clicked = False
                            self.status.state = ScanState.WAITING_FOR_VORTEX
                            self._update_status()

                    if web_clicked and not self.config.vortex:
                        # Non-Vortex mode: reset immediately
                        vortex_found = False
                        web_clicked = False

                # Wait before next iteration
                time.sleep(self.config.click_delay)

        except KeyboardInterrupt:
            self.status.current_action = "Stopped by user"
            self.status.state = ScanState.IDLE
            self._update_status()
            logger.info("Scan stopped by user")
        except Exception as e:
            error_msg = f"Scanner error: {e}"
            logger.error(error_msg, exc_info=True)
            self.status.errors.append(error_msg)
            self.status.state = ScanState.ERROR
            self._update_status()
            raise
